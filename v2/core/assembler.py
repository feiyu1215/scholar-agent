"""
core/v2/assembler.py — ContextAssembler: 上下文编排器

从 harness.py 的 format_context (~105 行) 提取而来。

设计依据:
    - Claude Code: 静态/动态分离 + Section 注册 + 惰性缓存
    - TencentDB: 分层压缩，不同信息有不同的"注意力层级"
    - Anthropic: "模型对世界的理解可能压在 1-2 万 token 里"

核心变化:
    - format_context 中的 9 个硬编码信息块 → 9 个独立注册的 Section
    - 每个 Section 有优先级 + 缓存策略 + 条件函数
    - token 预算不够时，低优先级 section 被自动丢弃
    - 不同阶段可以注入不同的 sections（通过 condition_fn）

与 harness.py 的关系:
    - Harness 创建 ContextAssembler 并在每轮调用 assemble()
    - 原 format_context 将被替换为 assembler.assemble(state, ...)
    - 输出兼容: 默认全预算下输出与原 format_context 等价
"""

from __future__ import annotations

import re
import logging
from typing import Any, TYPE_CHECKING

from core.sections import SectionRegistry, CachePolicy
from core.identity_static import STATIC_IDENTITY
from core.habits import HabitSelector, COGNITIVE_HABITS
from core.hypothesis import HypothesisModule
from core.paper_index import PaperStructureIndex
from core.paper_type_hints import CognitiveHints

if TYPE_CHECKING:
    from core.state import WorkspaceState
    from core.memory import MemoryStore
    from core.metacognition import CognitiveState
    from core.offload import OffloadStore
    from core.evolution import EvolutionEngine
    from core.token_budget import TokenBudgetManager, ZoneBAllocation
    from core.skill_registry import SkillRegistry

logger = logging.getLogger(__name__)


# ============================================================
# Section Compute Functions
# ============================================================
# 每个函数对应原 format_context 中的一个信息块。
# 接受一个 context dict（包含 state + 外部服务引用），返回 section 文本。

def _compute_paper_overview(ctx: dict) -> str:
    """论文概况 + section 列表 + 已读/未读。对应原 format_context 的第一块。"""
    state: WorkspaceState = ctx["state"]
    s = state
    parts = []

    if not s.paper_sections:
        return ""

    section_names = [k for k in s.paper_sections if k != "full"]
    total_chars = sum(len(v) for k, v in s.paper_sections.items() if k != "full")
    parts.append(f"论文已加载 | {len(section_names)} 个 sections | 总计 ~{total_chars} 字符")

    # 平铺展示所有 section + 字符数
    section_list = []
    for name in section_names:
        char_count = len(s.paper_sections[name])
        if char_count < 50:
            section_list.append(f"{name} (空)")
        else:
            section_list.append(f"{name} ({char_count}字)")
    parts.append(f"  Sections: {', '.join(section_list)}")
    parts.append("  用 read_section('<name>') 按需读取，长 section 支持 offset 续读")

    # 已读/未读
    if s.sections_read:
        parts.append(f"  ✅ 你已读过 ({len(s.sections_read)}): {', '.join(s.sections_read)}")
        unread = [n for n in section_names if n not in s.sections_read]
        if unread:
            parts.append(f"  📖 尚未读取: {', '.join(unread)}")

    return "\n".join(parts)


def _compute_section_digests(ctx: dict) -> str:
    """Section 摘要缓存。对应原 format_context 中 section_digests 块。"""
    state: WorkspaceState = ctx["state"]
    if not state.section_digests:
        return ""

    parts = [f"\n  📝 Section 摘要缓存 ({len(state.section_digests)} 条，无需重读即可回溯):"]
    for sec_name, digest in list(state.section_digests.items())[:10]:
        parts.append(f"    • {sec_name}: {digest}")
    return "\n".join(parts)


def _compute_findings(ctx: dict) -> str:
    """已有发现。对应原 format_context 中 findings 块。"""
    state: WorkspaceState = ctx["state"]
    s = state
    if not s.findings:
        return ""

    parts = [f"\n你已有的发现 ({len(s.findings)} 条):"]
    for i, f in enumerate(s.findings, 1):
        icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(f["priority"], "⚪")
        status = {"verified": "✓", "needs_verification": "?", "suggestion": "→"}.get(f["status"], "")
        evidence_hint = ""
        if f.get("evidence"):
            evidence_hint = f" [证据: \"{f['evidence'][:60]}...\"]"
        elif f.get("section"):
            evidence_hint = f" [来自: {f['section']}, 无证据]"
        parts.append(f"  {icon}[{status}] {f['finding'][:120]}{evidence_hint}")

    # 统计提示
    no_evidence = sum(1 for f in s.findings if not f.get("evidence"))
    if no_evidence > 0:
        parts.append(f"  ⚠️ {no_evidence} 条发现缺少原文证据，建议用 review_findings 复核")

    return "\n".join(parts)


def _compute_references(ctx: dict) -> str:
    """参考文献工作区（用户提供 + Agent 获取）。对应原 format_context 中参考文献块。"""
    state: WorkspaceState = ctx["state"]
    s = state
    if not s.reference_papers:
        return ""

    parts = []

    # 分类展示
    user_refs = {k: v for k, v in s.reference_papers.items() if v.get("source") == "user_provided"}
    agent_refs = {k: v for k, v in s.reference_papers.items() if v.get("source") != "user_provided"}

    if user_refs:
        parts.append(f"\n📎 用户提供的参考文献 ({len(user_refs)} 篇，可用 read_reference 深入阅读):")
        for ref_id, info in user_refs.items():
            char_info = f", {info.get('total_chars', '?')}字" if info.get("total_chars") else ""
            sec_info = f", {info.get('section_count', '?')} sections" if info.get("section_count") else ""
            abstract_hint = f" — {info['abstract'][:80]}..." if info.get("abstract") else ""
            parts.append(f"  • [{ref_id}] {info.get('title', '?')}{abstract_hint} ({sec_info}{char_info})")

    if agent_refs:
        parts.append(f"\n📚 Agent 获取的外部论文 ({len(agent_refs)} 篇):")
        for pid, info in list(agent_refs.items())[:5]:
            authors_short = ", ".join(info.get("authors", [])[:2])
            if len(info.get("authors", [])) > 2:
                authors_short += " et al."
            tldr = info.get("tldr", "")
            tldr_hint = f" — {tldr[:80]}..." if tldr else ""
            parts.append(f"  • {info.get('title', '?')} ({info.get('year', '?')}, {info.get('venue', '?')}){tldr_hint}")
        if len(agent_refs) > 5:
            parts.append(f"  ... 还有 {len(agent_refs) - 5} 篇")

    return "\n".join(parts)


def _compute_edits(ctx: dict) -> str:
    """已做的修改。对应原 format_context 中 edits 块。"""
    state: WorkspaceState = ctx["state"]
    if not state.edits:
        return ""
    return f"\n你已做的修改 ({len(state.edits)} 处): {', '.join(e['section'] for e in state.edits)}"


def _compute_memory(ctx: dict) -> str:
    """跨会话记忆注入。对应原 format_context 中 memory 块。"""
    memory: MemoryStore = ctx["memory"]
    paper_id: str | None = ctx.get("paper_id")
    memory_context = memory.format_memory_context(paper_id=paper_id)
    if not memory_context:
        return ""
    return f"\n{memory_context}"


def _compute_metacognition(ctx: dict) -> str:
    """元认知状态注入。对应原 format_context 中 cognitive 块。"""
    cognitive_state: CognitiveState = ctx["cognitive_state"]
    cognitive_context = cognitive_state.format_for_context()
    if not cognitive_context:
        return ""
    return f"\n{cognitive_context}"


def _compute_offload_refs(ctx: dict) -> str:
    """可恢复上下文引用列表。对应原 format_context 中 offload refs 块。"""
    offload_store: OffloadStore = ctx["offload_store"]
    refs_summary = offload_store.format_refs_summary()
    if not refs_summary:
        return ""
    return f"\n{refs_summary}"


def _compute_resource_status(ctx: dict) -> str:
    """资源状态。对应原 format_context 最后一行。"""
    state: WorkspaceState = ctx["state"]
    s = state
    return f"\n轮次: {s.loop_turns}/{s.max_loop_turns} | 对话轮: {s.conversation_turns} | Tokens: ~{s.total_tokens}"


def _compute_static_identity(ctx: dict) -> str:
    """静态身份区（~500字核心身份）。SESSION 缓存——整个会话不变。"""
    # 静态身份不依赖 state，但需要 workspace_state 占位符
    # 这里只返回身份模板（不含 workspace_state，那个由 resource_status 提供）
    # 去掉 {workspace_state} 和 "## 当前状态" 尾部——那些由其他 sections 负责
    identity = STATIC_IDENTITY
    # 截断到 "## 当前状态" 之前
    marker = "## 当前状态"
    if marker in identity:
        identity = identity[:identity.index(marker)].rstrip()
    return identity


def _compute_cognitive_habits(ctx: dict) -> str:
    """认知习惯（按阶段动态选取）。

    当 GODEL_HABIT_PROGRESSIVE_ENABLED 开启时：
    - 使用 select_and_format_progressive()（前 N 轮精简、后续完整）
    - 缓存策略应设为 NEVER（因为每轮 turn 不同导致输出可能不同）

    未开启时：
    - 保持原有 PHASE 缓存行为
    """
    from core.godel_config import GODEL_HABIT_PROGRESSIVE_ENABLED

    phase: str = ctx.get("current_phase", "")
    turn: int = ctx.get("current_turn", 0)
    selector: HabitSelector = ctx["habit_selector"]
    paper_type: str | None = _infer_paper_type(ctx)

    if GODEL_HABIT_PROGRESSIVE_ENABLED:
        return selector.select_and_format_progressive(
            phase=phase, turn=turn, paper_type=paper_type
        )
    return selector.select_and_format(phase=phase, turn=turn, paper_type=paper_type)


# ============================================================
# Condition Functions
# ============================================================

def _has_paper(ctx: dict) -> bool:
    """只在论文已加载时注入。"""
    return bool(ctx["state"].paper_sections)


def _has_digests(ctx: dict) -> bool:
    """只在有 digest 缓存时注入。"""
    return bool(ctx["state"].section_digests)


def _has_findings(ctx: dict) -> bool:
    """只在有 findings 时注入。"""
    return bool(ctx["state"].findings)


def _has_references(ctx: dict) -> bool:
    """只在有参考文献时注入。"""
    return bool(ctx["state"].reference_papers)


def _has_edits(ctx: dict) -> bool:
    """只在有编辑时注入。"""
    return bool(ctx["state"].edits)


def _has_hypotheses(ctx: dict) -> bool:
    """HD-WM 激活时始终注入（无论是否已有假说，提供引导提示）。"""
    module: HypothesisModule | None = ctx.get("hypothesis_module")
    return module is not None


def _has_paper_structure(ctx: dict) -> bool:
    """论文预索引已构建且非空时注入。"""
    state = ctx["state"]
    idx = getattr(state, "paper_structure_index", None)
    return idx is not None and not idx.is_empty()


def _has_cognitive_hints(ctx: dict) -> bool:
    """Agent 已生成认知提示时注入。"""
    state = ctx["state"]
    hints = getattr(state, "cognitive_hints", None)
    return hints is not None and not hints.is_empty()


def _has_pcg(ctx: dict) -> bool:
    """PCG 已构建且非空时注入（V3 Phase 0.5）。"""
    from core.godel_config import GODEL_PCG_ENABLED
    if not GODEL_PCG_ENABLED:
        return False
    state = ctx["state"]
    pcg = getattr(state, "paper_cognition_graph", None)
    return pcg is not None and not pcg.is_empty()


def _compute_pcg_navigation(ctx: dict) -> str:
    """
    PCG 认知图导航摘要（V3 Phase 0.5）。Priority 89 — 独立于 paper_structure(88)。

    提供图结构视角的论文导航：read_depth 标记、coverage_gaps、高权重边。
    与 paper_structure(88) 并存：paper_structure 提供原始结构，
    pcg_navigation 提供认知状态叠加层（已读深度、待读缺口、逻辑依赖）。
    """
    state = ctx["state"]
    pcg = getattr(state, "paper_cognition_graph", None)
    if pcg is None or pcg.is_empty():
        return ""
    return pcg.format_for_zone_a()


def _compute_paper_structure(ctx: dict) -> str:
    """
    论文结构预索引。Phase B1: Paper Mental Model.

    V3: 如 PCG 可用，优先使用 PCG.format_for_zone_a()（更紧凑、含认知状态）。
    INITIAL_SCAN 阶段注入完整索引，DEEP_REVIEW 阶段只注入当前 section 相关子集。
    """
    from core.godel_config import GODEL_PCG_ENABLED

    state = ctx["state"]
    idx: PaperStructureIndex = state.paper_structure_index
    current_phase: str = ctx.get("current_phase", "")

    # V3: PCG 优先 — format_for_zone_a 包含 read_depth + coverage_gaps
    if GODEL_PCG_ENABLED:
        pcg = getattr(state, "paper_cognition_graph", None)
        if pcg is not None and not pcg.is_empty():
            if current_phase.lower() in ("deep_review", "deepreview"):
                # DEEP_REVIEW: PCG context_for_task 提供任务相关上下文
                if state.sections_read:
                    task_ctx = pcg.context_for_task(state.sections_read[-1])
                    if task_ctx:
                        return task_ctx
            # 其他阶段或 context_for_task 为空: 使用 PCG 导航摘要
            return pcg.format_for_zone_a()

    # Fallback: V2 PaperStructureIndex 行为
    if current_phase.lower() in ("deep_review", "deepreview"):
        if state.sections_read:
            latest_section = state.sections_read[-1]
            subset = idx.format_subset_for_section(latest_section)
            if subset:
                return subset
        return ""

    return idx.format_for_context()


def _compute_cognitive_hints(ctx: dict) -> str:
    """
    Agent 自主生成的审稿认知提示。Phase S1: Paper-Type 自适应认知策略。

    仅在 INITIAL_SCAN / DEEP_REVIEW 阶段注入。
    SYNTHESIS / DRAFTING 后期不再注入（Agent 已内化策略）。
    """
    state = ctx["state"]
    hints: CognitiveHints | None = getattr(state, "cognitive_hints", None)
    if hints is None or hints.is_empty():
        return ""

    # 仅在早期阶段注入
    current_phase = ctx.get("current_phase", "").lower()
    if current_phase not in ("initial_scan", "initialscan", "deep_review", "deepreview", ""):
        return ""

    return hints.format_for_context()


def _compute_hypothesis_status(ctx: dict) -> str:
    """
    假说工作记忆状态。Phase 5 + H1 升级。

    H1 核心: 让 Agent "看到"假说但不被"命令追查"。
    措辞为认知辅助模式（COGNITIVE_ANCHOR §4.3）——
    信息呈现 + 自主权声明，而非指令。
    """
    module: HypothesisModule | None = ctx.get("hypothesis_module")
    if module is None:
        return ""
    if len(module.hypotheses) == 0:
        return (
            "[审稿假说模式已启用]\n"
            "当你对论文产生怀疑时，可用 generate_hypothesis 将其形式化。\n"
            "当前: 尚无假说。读到令你怀疑的地方时，随时提出。"
        )
    # H1: 认知辅助框架 + format_status 结构化数据
    status = module.format_status()
    return (
        "[当前审稿假说 — 你的待验证猜想]\n"
        f"{status}\n"
        "[以上假说由你的过往观察自动生成。"
        "你可以追查、修正或忽略它们。]"
    )


# ============================================================
# V4: Domain Skills Section (Knowledge Skills injection)
# ============================================================

def _has_domain_skills(ctx: dict) -> bool:
    """条件函数：轻量守卫，只检查 Kill Switch + registry 可用。

    不执行完整 query（避免与 _compute_domain_skills 重复计算）。
    实际匹配逻辑在 compute_fn 中完成——无结果时返回空字符串，不会被输出。
    """
    from core.godel_config import GODEL_SKILL_LOADING_ENABLED
    if not GODEL_SKILL_LOADING_ENABLED:
        return False
    skill_registry = ctx.get("skill_registry")
    if skill_registry is None:
        return False
    # 只需确认 registry 中有注册的 skills（O(1) 检查）
    return len(skill_registry.all_skills) > 0


def _kw_in_text(kw: str, text: str) -> bool:
    """检查关键词是否出现在文本中（短英文词使用 ASCII 字母边界防止子串误匹配）。

    对于短（≤4字符）纯 ASCII 字母关键词（如 "did", "rdd", "nlp"），要求
    匹配位置的前后字符不能是 ASCII 字母，以避免 "candidate" 中误匹配 "did"，
    同时允许中文上下文中的 "使用did的" 正确匹配。
    """
    if len(kw) <= 4 and kw.isascii() and kw.isalpha():
        # 使用 ASCII 字母边界而非 \b（因为 Python3 \w 包含中文字符）
        return bool(re.search(
            r'(?<![a-zA-Z])' + re.escape(kw) + r'(?![a-zA-Z])', text
        ))
    return kw in text


def _infer_paper_type(ctx: dict) -> str | None:
    """从 state 中推断当前论文类型（供 skill query 使用）。

    优先级:
    1. CognitiveHints.paper_type_description（Agent 自主判断）
    2. PaperStructureIndex.paper_type（结构推断）
    3. None（不过滤）
    """
    state = ctx.get("state")
    if state is None:
        return None

    # 从 CognitiveHints 推断
    hints = getattr(state, "cognitive_hints", None)
    if hints and hints.paper_type_description:
        desc = hints.paper_type_description.lower()
        # 关键词映射（匹配顺序有优先级含义）
        # 注意: ml_nlp 需排在 theoretical 前，因为 ML 论文常提 "model"
        if any(_kw_in_text(kw, desc) for kw in (
            "nlp", "deep learning", "机器学习", "神经网络",
            "transformer", "language model", "neural network",
        )):
            return "ml_nlp"
        # structural_econ 需在 empirical 之前匹配（结构模型常含 "calibrat" 和 "model"）
        if any(_kw_in_text(kw, desc) for kw in (
            "structural", "calibrat", "general equilibrium",
            "结构模型", "校准", "反事实", "counterfactual",
            "welfare", "optimal policy", "optimal tariff",
            "trade model", "贸易模型", "定量模型", "quantitative model",
            "tariff", "关税", "cge", "dsge", "computable",
        )):
            return "structural_econ"
        if any(_kw_in_text(kw, desc) for kw in (
            "empirical", "实证", "difference-in-differences", "did",
            "rdd", "instrumental variable", "causal",
        )):
            return "empirical_econ"
        if any(_kw_in_text(kw, desc) for kw in ("review", "survey", "综述", "梳理")):
            return "review"
        if any(_kw_in_text(kw, desc) for kw in (
            "theoretical", "理论", "proof", "博弈",
            "mechanism design", "equilibrium",
        )):
            # Guard: if PaperStructureIndex detected structural_econ, respect it
            # (Agent may describe a structural paper as "理论" if it has theory sections)
            idx = getattr(state, "paper_structure_index", None)
            if idx is not None and getattr(idx, "paper_type", "") == "structural_econ":
                return "structural_econ"
            return "theoretical"

    # 从 PaperStructureIndex 推断
    idx = getattr(state, "paper_structure_index", None)
    if idx is not None and hasattr(idx, "paper_type") and idx.paper_type:
        return idx.paper_type

    return None


def _compute_domain_skills(ctx: dict) -> str:
    """计算 Domain Skills 注入内容。

    V4 C2 增强: 优先加载模板推荐的 skills，剩余 budget 再用通用 query 补充。

    流程:
    1. 从 state.recommended_skills 获取模板推荐列表
    2. 按 ID 优先加载推荐 skills（验证 phase 适用性 + budget 约束）
    3. 剩余 budget 用 query() 补充（去重已加载的 ID）
    4. 合并结果 → C14 认知辅助框架包裹

    Priority=73，位于 metacognition(70) 和 section_digests(75) 之间。
    CachePolicy=PHASE: 同一阶段内 skill 组合不变。
    """
    from core.godel_config import GODEL_SKILL_LOADING_ENABLED, SKILL_ZONE_BUDGET
    if not GODEL_SKILL_LOADING_ENABLED:
        return ""

    skill_registry = ctx.get("skill_registry")
    if skill_registry is None:
        return ""

    paper_type = _infer_paper_type(ctx)
    phase = ctx.get("current_phase", "")
    phase_upper = phase.upper() if phase else None

    # --- C2: 优先加载模板推荐的 skills ---
    state = ctx.get("state")
    recommended_ids: list[str] = []
    if state is not None:
        _raw = getattr(state, "recommended_skills", None)
        if isinstance(_raw, list):
            recommended_ids = _raw

    priority_skills: list = []  # SkillMeta
    loaded_ids: set[str] = set()
    remaining_budget = SKILL_ZONE_BUDGET

    for skill_id in recommended_ids:
        meta = skill_registry.get(skill_id)
        if meta is None:
            logger.warning(
                "[DomainSkills/C2] Recommended skill '%s' not found in registry — skipped.",
                skill_id,
            )
            continue

        # 验证 phase 适用性（推荐 skill 不在当前 phase 列表中则跳过）
        if phase_upper and meta.applicable_phases:
            if phase_upper not in meta.applicable_phases:
                logger.debug(
                    "[DomainSkills/C2] Recommended skill '%s' not applicable to phase '%s' — skipped.",
                    skill_id,
                    phase_upper,
                )
                continue

        # Budget 约束
        if meta.token_estimate > remaining_budget:
            logger.debug(
                "[DomainSkills/C2] Recommended skill '%s' (%d tokens) exceeds remaining budget (%d) — skipped.",
                skill_id,
                meta.token_estimate,
                remaining_budget,
            )
            continue

        priority_skills.append(meta)
        loaded_ids.add(skill_id)
        remaining_budget -= meta.token_estimate

    # --- 通用 query 补充（去重已加载的推荐 skills）---
    if remaining_budget > 0:
        supplemental = skill_registry.query(
            paper_type=paper_type,
            phase=phase_upper,
            budget_tokens=remaining_budget,
        )
        for skill_meta in supplemental:
            if skill_meta.id not in loaded_ids:
                priority_skills.append(skill_meta)
                loaded_ids.add(skill_meta.id)

    if not priority_skills:
        return ""

    # 加载内容并组装
    parts: list[str] = []
    loaded_count = 0
    for skill_meta in priority_skills:
        content = skill_registry.load_content(skill_meta.id)
        if content:
            parts.append(content)
            loaded_count += 1

    if not parts:
        return ""

    # C14 认知辅助框架包装
    combined = "\n\n---\n\n".join(parts)
    return (
        "[领域审稿参考 — 按需加载，非指令]\n"
        f"以下 {loaded_count} 份参考知识基于论文类型和当前阶段自动匹配。"
        "你可以选择性参考，不必逐条遵循。\n\n"
        f"{combined}\n\n"
        "[以上为参考知识]"
    )


# ============================================================
# V3 Phase A1: Zone B Paper Content Section
# ============================================================

def _has_zone_b_content(ctx: dict) -> bool:
    """条件函数：Zone B 分配结果存在且有 full/digest 内容。"""
    from core.godel_config import GODEL_BUDGET_MANAGER_ENABLED
    if not GODEL_BUDGET_MANAGER_ENABLED:
        return False
    allocation = ctx.get("zone_b_allocation")
    if allocation is None:
        return False
    return bool(allocation.full_load or allocation.digest_load)


def _compute_zone_b_paper_content(ctx: dict) -> str:
    """计算 Zone B 动态加载内容。

    根据 TokenBudgetManager 的分配结果：
    - full_load sections → 注入完整原文
    - digest_load sections → 注入摘要（优先 state.section_digests，备选 PCG node.digest）
    - name_only → 不注入（已由 pcg_navigation / paper_overview 覆盖）

    优先级 77：高于 section_digests(75) 但低于 references(80)。
    """
    allocation = ctx.get("zone_b_allocation")
    if allocation is None:
        return ""

    state = ctx["state"]
    parts: list[str] = []

    # Full load: 注入完整 section 原文
    if allocation.full_load:
        for section_name in allocation.full_load:
            content = state.paper_sections.get(section_name, "")
            if content and len(content) > 50:
                # 截断保护：单个 section 不超过 ~30K chars（约 8K-10K tokens）
                truncated = content[:30000]
                if len(content) > 30000:
                    truncated += f"\n[...truncated, total {len(content)} chars]"
                parts.append(f"\n📄 [Zone B Full] {section_name}:\n{truncated}")

    # Digest load: 注入摘要
    if allocation.digest_load:
        digest_parts: list[str] = []
        for section_name in allocation.digest_load:
            # 优先使用已有的 section_digests
            digest = state.section_digests.get(section_name, "")
            if not digest:
                # 备选：PCG node.digest
                pcg = getattr(state, "paper_cognition_graph", None)
                if pcg and section_name in pcg.nodes:
                    digest = pcg.nodes[section_name].digest or ""
            if digest:
                digest_parts.append(f"    • {section_name}: {digest}")
            else:
                digest_parts.append(f"    • {section_name}: (未读取，无摘要)")
        if digest_parts:
            parts.append(f"\n📋 [Zone B Digest] 逻辑相关 sections ({len(digest_parts)} 条):")
            parts.extend(digest_parts)

    return "\n".join(parts) if parts else ""


# ============================================================
# P2: Evolution Context Section
# ============================================================

def _has_evolution_context(ctx: dict) -> bool:
    """条件函数：是否有进化引擎且有学习内容。"""
    engine = ctx.get("evolution_engine")
    if engine is None:
        return False
    return bool(engine.learned_habits)


def _compute_evolution_context(ctx: dict) -> str:
    """计算进化状态注入文本。"""
    engine = ctx.get("evolution_engine")
    if engine is None:
        return ""
    summary = engine.get_evolution_summary()
    return summary or ""


# ============================================================
# ContextAssembler
# ============================================================

class ContextAssembler:
    """
    上下文编排器——将状态变换为 LLM 可消费的 context 字符串。

    用法:
        assembler = ContextAssembler(memory=..., cognitive_state=..., offload_store=...)
        context_str = assembler.assemble(state, paper_id=..., current_turn=0)

    输出与原 format_context 等价（全预算下），但支持:
    - 按优先级裁剪（token 预算不够时丢弃低优先级 section）
    - 按阶段/条件动态注入
    - Section 级缓存（SESSION / PHASE / NEVER）
    """

    # Token 预算（蓝图 §3.2 定义）
    DYNAMIC_ZONE_BUDGET = 8000     # 动态 sections 总预算
    TOTAL_BUDGET = 15000           # system prompt 总上限（含静态区）

    def __init__(
        self,
        memory: Any,            # MemoryStore
        cognitive_state: Any,   # CognitiveState
        offload_store: Any,     # OffloadStore
        habit_selector: HabitSelector | None = None,
        hypothesis_module: HypothesisModule | None = None,
        evolution_engine: Any = None,  # P2: EvolutionEngine
        token_budget_manager: Any = None,  # V3: TokenBudgetManager (optional)
        skill_registry: "SkillRegistry | None" = None,  # V4: SkillRegistry (optional)
    ) -> None:
        self.memory = memory
        self.cognitive_state = cognitive_state
        self.offload_store = offload_store
        self.habit_selector = habit_selector or HabitSelector()
        self.hypothesis_module = hypothesis_module  # Phase 5: HD-WM（可为 None）
        self.evolution_engine = evolution_engine    # P2: 跨任务进化引擎
        self.token_budget_manager = token_budget_manager  # V3: Zone B 动态加载
        self.skill_registry = skill_registry        # V4: 领域知识 Skill 注册表
        self.registry = SectionRegistry()
        self._register_default_sections()

    def _register_default_sections(self) -> None:
        """注册所有默认 section（含身份+习惯+原 format_context 的 9 个块）。"""

        # Priority 设计说明:
        # 100: 静态身份 — 最高优先，SESSION 缓存
        # 95: 认知习惯 — 阶段相关，PHASE 缓存
        # 90: 论文概况 — 几乎总是需要的（Agent 的"视野"）
        # 85: Findings — Agent 的核心产出，不能丢
        # 80: 参考文献 — 深审阶段重要
        # 75: Section digests — 有用但可牺牲
        # 70: 元认知 — 行为引导
        # 65: 跨会话记忆 — 有用但通常不大
        # 60: Offload refs — 辅助信息
        # 55: Edits — 修改阶段重要
        # 50: 资源状态 — 总是很短，几乎不占预算

        # --- 身份层（静态区）---
        self.registry.register(
            name="static_identity",
            priority=100,
            cache_policy=CachePolicy.SESSION,  # 整个会话不变
            compute_fn=_compute_static_identity,
        )

        from core.godel_config import GODEL_HABIT_PROGRESSIVE_ENABLED
        # Progressive loading 需要 NEVER 缓存（每轮 turn 不同→输出可能变化）
        _habit_cache = CachePolicy.NEVER if GODEL_HABIT_PROGRESSIVE_ENABLED else CachePolicy.PHASE
        self.registry.register(
            name="cognitive_habits",
            priority=95,
            cache_policy=_habit_cache,
            compute_fn=_compute_cognitive_habits,
        )

        # --- 动态区（原 format_context 9 个块）---
        self.registry.register(
            name="paper_overview",
            priority=90,
            cache_policy=CachePolicy.NEVER,  # 已读/未读每轮变化
            compute_fn=_compute_paper_overview,
            condition_fn=_has_paper,
        )

        # V3 Phase 0.5: PCG 认知图导航（独立于 paper_structure，提供认知状态叠加层）
        self.registry.register(
            name="pcg_navigation",
            priority=89,
            cache_policy=CachePolicy.NEVER,  # read_depth/coverage_gaps 每轮变化
            compute_fn=_compute_pcg_navigation,
            condition_fn=_has_pcg,
        )

        # Phase B1: 论文结构预索引（INITIAL_SCAN 完整，DEEP_REVIEW 子集）
        self.registry.register(
            name="paper_structure",
            priority=88,
            cache_policy=CachePolicy.NEVER,  # 依阶段和最近读的 section 动态变化
            compute_fn=_compute_paper_structure,
            condition_fn=_has_paper_structure,
        )

        # Phase S1: Agent 自主生成的认知提示（INITIAL_SCAN + DEEP_REVIEW）
        self.registry.register(
            name="cognitive_hints",
            priority=86,  # 紧跟 paper_structure(88)，高于 findings(85)
            cache_policy=CachePolicy.NEVER,  # Agent 可随时修正
            compute_fn=_compute_cognitive_hints,
            condition_fn=_has_cognitive_hints,
        )

        self.registry.register(
            name="section_digests",
            priority=75,
            cache_policy=CachePolicy.NEVER,  # digest 随读取增长
            compute_fn=_compute_section_digests,
            condition_fn=_has_digests,
        )

        self.registry.register(
            name="findings",
            priority=85,
            cache_policy=CachePolicy.NEVER,
            compute_fn=_compute_findings,
            condition_fn=_has_findings,
        )

        # Phase 5: HD-WM 假说状态（仅当激活且有假说时注入）
        self.registry.register(
            name="hypothesis_status",
            priority=82,
            cache_policy=CachePolicy.NEVER,  # 每轮假说状态都可能变
            compute_fn=_compute_hypothesis_status,
            condition_fn=_has_hypotheses,
        )

        self.registry.register(
            name="references",
            priority=80,
            cache_policy=CachePolicy.NEVER,
            compute_fn=_compute_references,
            condition_fn=_has_references,
        )

        self.registry.register(
            name="edits",
            priority=55,
            cache_policy=CachePolicy.NEVER,
            compute_fn=_compute_edits,
            condition_fn=_has_edits,
        )

        self.registry.register(
            name="memory",
            priority=65,
            cache_policy=CachePolicy.SESSION,  # 跨会话记忆在会话内不变
            compute_fn=_compute_memory,
        )

        self.registry.register(
            name="metacognition",
            priority=70,
            cache_policy=CachePolicy.NEVER,  # 元认知状态每轮变
            compute_fn=_compute_metacognition,
        )

        self.registry.register(
            name="offload_refs",
            priority=60,
            cache_policy=CachePolicy.NEVER,
            compute_fn=_compute_offload_refs,
        )

        self.registry.register(
            name="resource_status",
            priority=50,
            cache_policy=CachePolicy.NEVER,
            compute_fn=_compute_resource_status,
        )

        # P2: 认知进化状态（中优先级——学习到的习惯需要可见才能生效）
        # 原 priority=52 过低，在 budget 压力下第一个被裁掉，导致学到的东西无法注入
        # 注意: memory=65 优先级更高（跨会话记忆对当前论文直接相关），evolution 降至 63
        self.registry.register(
            name="evolution_context",
            priority=63,
            cache_policy=CachePolicy.SESSION,  # 进化状态在会话内不变
            compute_fn=_compute_evolution_context,
            condition_fn=_has_evolution_context,
        )

        # V4: 领域知识 Skills 动态注入（知识型 Skill 内容）
        self.registry.register(
            name="domain_skills",
            priority=73,  # 高于 metacognition(70)，低于 section_digests(75)
            cache_policy=CachePolicy.PHASE,  # 同一阶段内 skill 组合不变
            compute_fn=_compute_domain_skills,
            condition_fn=_has_domain_skills,
        )

        # V3 Phase A1: Zone B 动态加载内容（PCG 驱动的 section 按需加载）
        self.registry.register(
            name="zone_b_paper_content",
            priority=77,  # 高于 section_digests(75)，低于 references(80)
            cache_policy=CachePolicy.NEVER,  # 每轮根据当前 task section 重算
            compute_fn=_compute_zone_b_paper_content,
            condition_fn=_has_zone_b_content,
        )

    def assemble(
        self,
        state: Any,
        paper_id: str | None = None,
        current_turn: int = 0,
        current_phase: str = "",
        budget: int | None = None,
    ) -> str:
        """
        组装完整的 context 字符串。

        Args:
            state: WorkspaceState 实例
            paper_id: 论文 ID（用于记忆检索）
            current_turn: 当前轮次
            current_phase: 当前阶段名
            budget: 自定义 token 预算（默认使用 DYNAMIC_ZONE_BUDGET）

        Returns:
            组装好的 context 字符串（等价于原 format_context 输出）
        """
        effective_budget = budget if budget is not None else self.DYNAMIC_ZONE_BUDGET

        # V3: 计算 Zone B 分配（如 TokenBudgetManager 可用）
        zone_b_allocation = None
        if self.token_budget_manager is not None:
            from core.godel_config import GODEL_BUDGET_MANAGER_ENABLED
            if GODEL_BUDGET_MANAGER_ENABLED:
                pcg = getattr(state, "paper_cognition_graph", None)
                current_task_section = self._determine_current_task_section(state, pcg)
                zone_b_allocation = self.token_budget_manager.compute_zone_b_allocation(
                    pcg=pcg,
                    current_task_section=current_task_section,
                )

        # 构建 context dict（所有 compute 函数的输入）
        ctx = {
            "state": state,
            "memory": self.memory,
            "cognitive_state": self.cognitive_state,
            "offload_store": self.offload_store,
            "paper_id": paper_id,
            "habit_selector": self.habit_selector,
            "current_phase": current_phase,
            "current_turn": current_turn,
            "hypothesis_module": self.hypothesis_module,  # Phase 5: HD-WM
            "evolution_engine": self.evolution_engine,     # P2: 进化引擎
            "zone_b_allocation": zone_b_allocation,       # V3: Zone B 分配结果
            "skill_registry": self.skill_registry,        # V4: Skill 注册表
        }

        # 获取按优先级排序、预算裁剪后的 sections
        active_sections = self.registry.get_active_sections(
            state=ctx,
            budget=effective_budget,
            current_turn=current_turn,
            current_phase=current_phase,
        )

        if not active_sections:
            return "（刚开始，还没有任何状态）"

        # 组装最终输出
        content = "\n".join(content for _, content in active_sections)
        return content

    def _determine_current_task_section(self, state: Any, pcg: Any) -> str:
        """确定当前任务 section（Zone B full_load 的目标）。

        优先级:
        1. state.sections_read[-1] — 最近读取的 section
        2. PCG coverage_gaps()["unread"][0] — 下一个应读的 section
        3. "" — 空字符串（ZoneBAllocation 返回空分配，不影响现有行为）
        """
        # 1. 最近读取的 section
        if state.sections_read:
            return state.sections_read[-1]

        # 2. PCG 推荐的下一个 unread section
        if pcg is not None and not pcg.is_empty():
            gaps = pcg.coverage_gaps()
            unread = gaps.get("unread", [])
            if unread:
                return unread[0]

        return ""

    def invalidate_phase_cache(self) -> None:
        """阶段切换时调用，清除 PHASE 策略的缓存。"""
        self.registry.invalidate_phase_cache()

    def invalidate_all_cache(self) -> None:
        """强制清除所有缓存。"""
        self.registry.invalidate_cache()
