"""
core/harness.py — Harness: Agent 的状态守护层

设计原则 (来自 COGNITIVE_ANCHOR §5.2, §4.3):
    - Harness 不控制 Agent 做什么，只守护边界
    - Harness 替 LLM 记住一切（LLM 是无状态 CPU）
    - Harness 在每轮提供 context，执行 tool call，检查约束

职责:
    1. 状态持久化 — 论文内容、发现、修改历史、对话记忆
    2. 边界守护 — doom loop guard、completion quality gate、token budget
    3. 工具执行 — 接收 tool call，执行并返回结果
    4. Context 组装 — 每轮为 LLM 组装当前状态摘要

不做:
    - 不决定 Agent 下一步做什么
    - 不路由到不同 pipeline
    - 不维护 tool registry（Agent 的 tools 在 identity 里定义）
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any

from core.state import WorkspaceState
from core.tools import ToolRegistry
from core.phases import Phase, PhaseFSM
from core.memory import (
    MemoryStore,
    SessionRecord,
    build_session_record,
    extract_domain_patterns,
    extract_procedural_patterns,
)
from core.cognition_graph import (
    build_cognition_graph,
    persist_cognitive_hints_as_experience,
)
from core.gate_config import (
    CompletionGateConfig,
    compute_gate_config,
    record_review_stats,
    compute_idle_rounds_before_exit,
)
from core.post_edit_verify import (
    verify_edit,
    format_verification_feedback,
    extract_voice,
    VoiceFingerprint,
)
from core.claim_signal import detect_verifiable_claims
from core.metacognition import CognitiveState
from core.offload import OffloadStore
from core.checker import CognitiveChecker
from core.compaction import CompactionEngine
from core.assembler import ContextAssembler
from core.hypothesis import HypothesisModule
from core.session_memory import SessionMemoryManager
from core.paper_index import PaperIndexBuilder
from core.finding_quality import FindingQualityGate
from core.boundary_guard import (
    check_doom_loop as _bg_check_doom_loop,
    check_soft_turn_limit as _bg_check_soft_turn_limit,
    check_cognitive_output as _bg_check_cognitive_output,
    track_cognitive_output as _bg_track_cognitive_output,
    increment_read_turn as _bg_increment_read_turn,
    check_reflection_needed as _bg_check_reflection_needed,
    check_token_budget as _bg_check_token_budget,
    check_completion_gate as _bg_check_completion_gate,
)
from core.paper_loader import (
    load_paper as _pl_load_paper,
    load_user_references as _pl_load_user_references,
)
from core.session_finalizer import end_session as _sf_end_session
from core.tool_reflect import (
    reflect_and_plan as _tr_reflect_and_plan,
    check_stagnation as _tr_check_stagnation,
)


# ============================================================
# Harness — 守护层
# ============================================================

class Harness:
    """
    Agent 的 Harness。职责：状态管理 + 边界守护 + 工具执行。

    使用方式:
        harness = Harness(paper_path="paper.md")
        # 每轮循环中:
        context = harness.format_context()  # 给 LLM 看的状态摘要
        result = harness.execute_tool(name, args)  # 执行 tool call
        verdict = harness.check_completion(findings)  # 检查是否允许完成
    """

    def __init__(self, paper_path: str | None = None, max_loop_turns: int = 50, token_budget: int = 200_000, context_window: int = 128_000, memory_dir: str | Path | None = None, persona: str = "scholar", reference_paths: list[str] | None = None, enable_hdwm: bool = False):
        self.state = WorkspaceState(
            paper_path=paper_path,
            max_loop_turns=max_loop_turns,
            token_budget=token_budget,
            context_window=context_window,
        )
        self._paper_loaded = False
        self._persona = persona  # Phase 55: 当前 persona 标识
        self.enable_hdwm = enable_hdwm  # Phase 5: HD-WM 可插拔开关
        if paper_path:
            _pl_load_paper(self.state, paper_path)
            self._paper_loaded = True

        # Phase 58: 加载用户提供的参考文献
        if reference_paths:
            _pl_load_user_references(self.state, reference_paths)

        # Phase 15: 跨会话记忆
        # memory_dir 默认为 paper_path 同级的 .memory/ 目录
        if memory_dir:
            self._memory_dir = Path(memory_dir)
        elif paper_path:
            p = Path(paper_path)
            self._memory_dir = (p.parent if p.is_file() else p) / ".memory"
        else:
            self._memory_dir = Path(".memory")
        self.memory = MemoryStore(self._memory_dir)
        self.memory.load()  # 渐进退化: 文件不存在时使用空状态
        self._paper_id: str | None = None
        if self._paper_loaded and self.state.paper_sections:
            self._paper_id = MemoryStore.compute_paper_id(self.state.paper_sections)

        # Phase 32: 元认知自我模型
        self.cognitive_state = CognitiveState()

        # Phase 54: 策略切换追踪（用于程序性记忆提取）
        self._strategy_transitions: list[tuple[str, str]] = []
        self._last_strategy: str = "undecided"

        # Phase 50/55: 认知校验层（小模型快速校验，支持 persona 适配）
        self.checker = CognitiveChecker(persona=persona)

        # Phase 32: 可恢复的上下文卸载
        workspace_root = Path(paper_path).parent if paper_path and Path(paper_path).is_file() else Path(paper_path or ".")
        refs_dir = workspace_root / ".workspace" / "refs"
        self.offload_store = OffloadStore(refs_dir=refs_dir)

        # v2 Phase 4: Phase FSM — 阶段管理
        self.phase_fsm = PhaseFSM()

        # v2 Phase 5: HD-WM — 可插拔假说驱动工作记忆（在 tool registry 之前初始化）
        self.hypothesis_module: HypothesisModule | None = None
        if self.enable_hdwm:
            self.hypothesis_module = HypothesisModule()

        # v2: ToolRegistry — 替代 execute_tool 中的 if-elif 分发
        self._init_tool_registry()

        # v2: Smart Compaction Engine — 压缩时注入工作台恢复信息
        self.compaction_engine = CompactionEngine()

        # v2 Phase 13: Session Memory — 审稿认知笔记（跨压缩保持 Agent 判断积累）
        self.session_memory = SessionMemoryManager()

        # Q1: Finding Quality Gate — mark_complete 时的规则质量自检
        self.finding_quality_gate = FindingQualityGate()

        # B4: Completion Gate 动态配置（初始为默认值，Agent 生成 cognitive_hints 后更新）
        self.gate_config = CompletionGateConfig()

        # v2 Phase 3: Context Assembler — 替代 format_context 中的硬编码逻辑
        self.assembler = ContextAssembler(
            memory=self.memory,
            cognitive_state=self.cognitive_state,
            offload_store=self.offload_store,
            hypothesis_module=self.hypothesis_module,  # Phase 5: HD-WM（None if disabled）
        )

    # ----------------------------------------------------------
    # 论文加载 — 委托 paper_loader 模块
    # ----------------------------------------------------------

    def load_paper(self, path: str | None = None):
        """公开接口: 加载论文。如已加载则跳过。"""
        if self._paper_loaded:
            return
        target = path or self.state.paper_path
        if target:
            _pl_load_paper(self.state, target)
            self._paper_loaded = True

    def load_references(self, paths: list[str]):
        """公开接口: 运行时追加参考文献。"""
        _pl_load_user_references(self.state, paths)

    # ----------------------------------------------------------
    # Context 组装 — 给 LLM 看的状态摘要
    # ----------------------------------------------------------

    def format_context(self, include_identity: bool = False) -> str:
        """格式化当前状态，注入到 system prompt 的 {workspace_state} 占位符。

        Phase 3 v2 重构：委托给 ContextAssembler。
        原逻辑保留在 _format_context_legacy() 供回退和对比测试。

        ContextAssembler 的优势:
        - Section 注册制：每个信息块独立、可缓存、有优先级
        - Token 预算裁剪：不够时低优先级 section 自动丢弃
        - 阶段感知：不同 Phase 可注入不同 sections

        Args:
            include_identity: 如果 True，assembler 输出包含静态身份+习惯
                              （用于 v2 模式，直接作为完整 system prompt 内容）
                              如果 False（默认），与旧 build_system_prompt 兼容
        """
        # v2 Phase 4: 从 FSM 获取当前阶段
        current_phase = self.phase_fsm.phase_name

        return self.assembler.assemble(
            state=self.state,
            paper_id=self._paper_id,
            current_turn=self.state.loop_turns,
            current_phase=current_phase,
        )

    def _format_context_legacy(self) -> str:
        """原 format_context 实现（Phase 18）。保留供回退和对比验证。
        
        Phase 18：只提供客观事实（section 名 + 字符数），不做优先级分类。
        Agent 自己决定阅读策略——它的 identity prompt 已经包含了"战略性阅读"的能力定义。
        """
        parts = []
        s = self.state

        # 论文概况 — 纯事实，不分类
        if s.paper_sections:
            section_names = [k for k in s.paper_sections if k != "full"]
            total_chars = sum(len(v) for k, v in s.paper_sections.items() if k != "full")
            parts.append(f"论文已加载 | {len(section_names)} 个 sections | 总计 ~{total_chars} 字符")

            # Phase 18: 平铺展示所有 section + 字符数，不强加优先级判断
            section_list = []
            for name in section_names:
                char_count = len(s.paper_sections[name])
                if char_count < 50:
                    section_list.append(f"{name} (空)")
                else:
                    section_list.append(f"{name} ({char_count}字)")
            parts.append(f"  Sections: {', '.join(section_list)}")
            parts.append("  用 read_section('<name>') 按需读取，长 section 支持 offset 续读")

            # Phase 14: 显示已读 sections — 减少 Agent 重复读取的概率
            if s.sections_read:
                parts.append(f"  ✅ 你已读过 ({len(s.sections_read)}): {', '.join(s.sections_read)}")
                unread = [n for n in section_names if n not in s.sections_read]
                if unread:
                    parts.append(f"  📖 尚未读取: {', '.join(unread)}")

            # Phase 16: 展示 section digests — 压缩后仍可回溯的结构化摘要
            if s.section_digests:
                parts.append(f"\n  📝 Section 摘要缓存 ({len(s.section_digests)} 条，无需重读即可回溯):")
                for sec_name, digest in list(s.section_digests.items())[:10]:
                    parts.append(f"    • {sec_name}: {digest}")

        # 已有发现（带 evidence 摘要，方便 Agent 快速回忆）
        if s.findings:
            parts.append(f"\n你已有的发现 ({len(s.findings)} 条):")
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

        # Phase 57+58: 参考文献工作区 — 统一展示所有外部文献
        if s.reference_papers:
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

        # 已做的修改
        if s.edits:
            parts.append(f"\n你已做的修改 ({len(s.edits)} 处): {', '.join(e['section'] for e in s.edits)}")

        # Phase 15: 跨会话记忆注入
        memory_context = self.memory.format_memory_context(paper_id=self._paper_id)
        if memory_context:
            parts.append(f"\n{memory_context}")

        # Phase 32: 元认知状态注入
        cognitive_context = self.cognitive_state.format_for_context()
        if cognitive_context:
            parts.append(f"\n{cognitive_context}")

        # Phase 32: 可恢复上下文引用列表
        refs_summary = self.offload_store.format_refs_summary()
        if refs_summary:
            parts.append(f"\n{refs_summary}")

        # 资源状态
        parts.append(f"\n轮次: {s.loop_turns}/{s.max_loop_turns} | 对话轮: {s.conversation_turns} | Tokens: ~{s.total_tokens}")

        return "\n".join(parts) if parts else "（刚开始，还没有任何状态）"

    # ----------------------------------------------------------
    # 工具注册
    # ----------------------------------------------------------

    def _init_tool_registry(self) -> None:
        """注册所有工具到 ToolRegistry。

        Phase 4: 每个工具标注可用阶段（phases）。
        phases=None 表示所有阶段可用（通用工具）。
        """
        self.tool_registry = ToolRegistry()

        # --- 通用工具（所有阶段可用）---
        self.tool_registry.register("update_findings", self._tool_update_findings, phases=None)
        self.tool_registry.register("review_findings", self._tool_review_findings, phases=None)
        self.tool_registry.register("talk_to_user", self._tool_talk_to_user, phases=None)
        self.tool_registry.register("reflect_and_plan", self._tool_reflect_and_plan, phases=None)
        self.tool_registry.register("recall_context", self._tool_recall_context, phases=None)
        self.tool_registry.register("done", self._tool_done, phases=None)
        self.tool_registry.register("mark_complete", self._tool_done, phases=None)
        self.tool_registry.register("request_phase_transition", self._tool_request_phase_transition, phases=None)

        # --- S1: 认知提示生成工具（initial_scan + deep_review）---
        self.tool_registry.register(
            "generate_cognitive_hints", self._tool_generate_cognitive_hints,
            phases={"initial_scan", "deep_review"},
        )

        # --- 阅读工具（initial_scan + deep_review + editing）---
        _reading_phases = {"initial_scan", "deep_review", "editing"}
        self.tool_registry.register("read_section", self._tool_read_section, phases=_reading_phases)

        # --- 搜索/分析工具（deep_review + synthesis）---
        _analysis_phases = {"initial_scan", "deep_review", "synthesis"}
        self.tool_registry.register("search_literature", self._tool_search_literature, phases=_analysis_phases)
        self.tool_registry.register("fetch_paper_detail", self._tool_fetch_paper_detail, phases={"deep_review", "synthesis"})
        self.tool_registry.register("read_reference", self._tool_read_reference, phases={"deep_review", "editing"})
        self.tool_registry.register("detect_ai_signals", self._tool_detect_ai_signals, phases={"deep_review", "editing"})
        self.tool_registry.register("verify_citations", self._tool_verify_citations, phases={"deep_review", "editing"})

        # --- 编辑工具（editing 阶段）---
        self.tool_registry.register("edit_section", self._tool_edit_section, phases={"editing"})

        # --- 视角分裂（deep_review + synthesis）---
        self.tool_registry.register("spawn_perspective", self._tool_spawn_perspective, phases={"deep_review", "synthesis"})

        # --- Phase 5→10: HD-WM 假说工具（可选高级工具）---
        # Phase 10 设计变更: HD-WM 的主要数据来源现在是 update_findings 的自动增强层
        # （_hdwm_auto_enhance），不再依赖 Agent 主动调用这三个工具。
        # 保留注册是为了：
        # 1. 向后兼容（已有测试依赖这些工具存在）
        # 2. 深度追查场景下 Agent 仍可显式管理假说（如添加反面证据）
        # 3. 可见性收窄到 deep_review 阶段——减少 initial_scan/synthesis 的工具噪声
        if self.enable_hdwm and self.hypothesis_module is not None:
            _hdwm_optional_phases = {"deep_review"}
            self.tool_registry.register(
                "generate_hypothesis", self._tool_generate_hypothesis,
                phases=_hdwm_optional_phases,
            )
            self.tool_registry.register(
                "add_evidence", self._tool_add_evidence,
                phases=_hdwm_optional_phases,
            )
            self.tool_registry.register(
                "resolve_hypothesis", self._tool_resolve_hypothesis,
                phases=_hdwm_optional_phases,
            )

    # ----------------------------------------------------------
    # 工具执行
    # ----------------------------------------------------------

    def execute_tool(self, name: str, args: dict) -> str:
        """执行 tool call，返回结果字符串。纯逻辑，不含 LLM 调用。"""

        # Phase 31: 统计工具使用频次
        self.state.tool_call_counts[name] = self.state.tool_call_counts.get(name, 0) + 1

        # Phase 34: 逐条记录
        self.state.tool_call_history.append({"name": name, "input": args})

        # 容错: 参数解析失败
        if "__parse_error__" in args:
            raw = args.get("__raw__", "")[:300]
            return (
                f"[工具调用失败] 你的参数格式无法解析。"
                f"错误: {args['__parse_error__']}。"
                f"原始内容: {raw}。"
                f"请重新调用 {name}，确保参数是合法的 JSON。"
            )

        # v2: ToolRegistry 分发（替代原来的 14 个 if-elif）
        result = self.tool_registry.execute(name, args)

        # Phase 55: 停滞检测
        stagnation_signal = self._check_stagnation(name)
        if stagnation_signal:
            result += stagnation_signal

        return result

    def _tool_read_section(self, args: dict) -> str:
        section = args.get("section", "").lower().strip()
        offset = args.get("offset", 0) or 0  # Phase 18: 续读支持
        sections = self.state.paper_sections

        # Phase 14: 追踪已读 sections，用于 format_context 提示防止重复读取
        # Phase 16: 同时生成 section digest 用于压缩后回溯
        # Phase 20: 同时累积 voice profile（零成本，为后续修改验证准备）
        # Phase 32: 同时 offload 到外部文件（折叠≠丢弃）
        def _record_read(resolved_name: str, content: str):
            if resolved_name not in self.state.sections_read:
                self.state.sections_read.append(resolved_name)
                # Phase 32: 首次读取时 offload 完整内容到外部文件
                if self.offload_store.should_offload(content, "read_section"):
                    digest = self.state.section_digests.get(resolved_name, content[:80])
                    self.offload_store.offload(
                        tool_name="read_section",
                        key=resolved_name,
                        content=content,
                        summary=digest,
                        loop_turn=self.state.loop_turns,
                    )
            # Phase 16: 生成 2 句话 digest（纯启发式，不调 LLM）
            if resolved_name not in self.state.section_digests:
                self.state.section_digests[resolved_name] = _generate_section_digest(resolved_name, content)
            # Phase 20: 提取并累积作者写作风格指纹
            if len(content) >= 200:  # 只对有实质内容的 section 提取
                section_fp = extract_voice(content)
                if section_fp.total_words_analyzed > 0:
                    if self.state.voice_profile is None:
                        self.state.voice_profile = section_fp
                    else:
                        # 加权合并（基于已分析的 word 数量）
                        existing = self.state.voice_profile
                        total = existing.total_words_analyzed + section_fp.total_words_analyzed
                        w1 = existing.total_words_analyzed / total
                        w2 = section_fp.total_words_analyzed / total
                        self.state.voice_profile = VoiceFingerprint(
                            avg_sentence_length=round(existing.avg_sentence_length * w1 + section_fp.avg_sentence_length * w2, 1),
                            sentence_length_std=round(existing.sentence_length_std * w1 + section_fp.sentence_length_std * w2, 1),
                            passive_ratio=round(existing.passive_ratio * w1 + section_fp.passive_ratio * w2, 2),
                            hedge_frequency=round(existing.hedge_frequency * w1 + section_fp.hedge_frequency * w2, 2),
                            total_words_analyzed=total,
                        )

        # Phase 18: 统一的截断+续读返回逻辑
        # 设计原则 (§4.3 约束-而非-控制):
        #   - 单次窗口 6000 字符是 token 预算约束（合理）
        #   - 但 Agent 有权决定是否继续读取（恢复自主权）
        #   - 截断时明确告知剩余量和续读方式
        WINDOW = 6000

        def _windowed_return(resolved_name: str, content: str) -> str:
            """返回 content[offset:offset+WINDOW]，必要时附带续读提示。
            
            Phase 31: 当内容含 verifiable claims 时，在末尾附加 claim signal。
            设计原则 (§4.3 约束-而非-控制):
            - 这是环境给 Agent 的信号，不是指令
            - Agent 可以忽略它（它只是一个 [信号]，不是 "你必须搜索"）
            - 类比：人类审稿人读到 "no prior work" 时，大脑会自动标记"这需要验证"
            - 我们只是在模拟这个"标记"过程，Agent 决定是否行动
            """
            total = len(content)
            if offset >= total:
                return (
                    f"[已到达 section '{resolved_name}' 末尾] "
                    f"全文 {total} 字符，offset={offset} 已超出范围。无需续读。"
                )
            chunk = content[offset:offset + WINDOW]
            end_pos = offset + len(chunk)
            remaining = total - end_pos

            # Phase 31: 检测 chunk 中的 verifiable claims
            claim_signal = detect_verifiable_claims(chunk)

            if remaining <= 0:
                # 本次返回已包含所有（从 offset 到末尾），无需续读提示
                if offset > 0:
                    result = f"[续读 {resolved_name}, 字符 {offset}-{end_pos}/{total}]\n\n{chunk}"
                else:
                    result = chunk
                return result + claim_signal if claim_signal else result
            else:
                # 还有剩余内容——告知 Agent 如何续读
                hint = (
                    f"\n\n[... 已显示字符 {offset}-{end_pos}，"
                    f"剩余 {remaining} 字符 (共 {total})。"
                    f"如需继续阅读，调用 read_section(section=\"{resolved_name}\", offset={end_pos}) ...]"
                )
                if offset > 0:
                    result = f"[续读 {resolved_name}, 字符 {offset}-{end_pos}/{total}]\n\n{chunk}{hint}"
                else:
                    result = chunk + hint
                return result + claim_signal if claim_signal else result

        if section == "list":
            names = [k for k in sections if k != "full"]
            lines = [f"可用 sections ({len(names)}):"]
            for name in names:
                char_count = len(sections[name])
                lines.append(f"  - {name} ({char_count} 字符)")
            return "\n".join(lines)
        elif section == "full":
            full = sections.get("full", "")
            if not full:
                return "没有全文。请用 read_section('list') 查看可用 sections，逐段读取。"
            # 短论文直接返回全文；长论文提示分段读取
            if len(full) > 12000:
                names = [k for k in sections if k != "full"]
                return (
                    full[:3000]
                    + f"\n\n[... 论文共 {len(full)} 字符，已截断。"
                    f"可用 sections: {', '.join(names[:10])}。"
                    f"请用 read_section 按需读取具体 section，避免全文注入浪费 token。 ...]"
                )
            return full
        else:
            # 1. 精确匹配
            if section in sections:
                content = sections[section]
                if len(content) < 50:
                    return (
                        f"[注意] Section '{section}' 内容极少（仅 {len(content)} 字符: \"{content.strip()}\"）。"
                        f"这可能是一个空壳子标题，实际内容在其子 section 中。"
                        f"建议读取其他相关 section。"
                    )
                _record_read(section, content)
                return _windowed_return(section, content)

            # 2. 模糊匹配：选择最佳匹配（最长 key 优先，避免短 key 意外匹配）
            candidates = []
            for key in sections:
                if key == "full":
                    continue
                if section in key.lower() or key.lower() in section:
                    candidates.append(key)

            if candidates:
                best = max(candidates, key=len)
                content = sections[best]
                if len(content) < 50:
                    return (
                        f"[注意] Section '{best}' 内容极少（仅 {len(content)} 字符: \"{content.strip()}\"）。"
                        f"这可能是一个空壳子标题，实际内容在其子 section 中。"
                        f"建议读取其他相关 section。"
                    )
                _record_read(best, content)
                return _windowed_return(best, content)

            # 3. 尝试数字匹配（如用户输入 "3" 匹配 "3. methodology"）
            for key in sections:
                if key.startswith(section + ".") or key.startswith(section + " "):
                    content = sections[key]
                    _record_read(key, content)
                    return _windowed_return(key, content)
            available = ", ".join(k for k in sections.keys() if k != "full")
            return f"未找到 section '{section}'。可用: {available}"

    def _tool_search_literature(self, args: dict) -> str:
        query = args.get("query", "")
        reason = args.get("reason", "")
        # Phase 39: 记录搜索行为用于认知观察
        if not hasattr(self, '_search_log'):
            self._search_log = []
        try:
            from core.web_search import intelligent_search
            response = intelligent_search(query, limit=5)
            self._search_log.append({
                "query": query,
                "reason": reason,
                "results_count": len(response.results),
                "source": response.source,
                "loop_turn": self.state.loop_turns,
            })
            if not response.results:
                return f"搜索 '{query}' 无结果。{response.error or ''}\n原因: {reason}"
            lines = [f"搜索 '{query}' 的结果 (来源: {response.source}, 共 {response.total_found} 条):"]
            for i, r in enumerate(response.results, 1):
                authors = ", ".join(r.authors[:3])
                if len(r.authors) > 3:
                    authors += " et al."
                lines.append(f"  [{i}] {r.title} ({r.year or '?'})")
                lines.append(f"      作者: {authors} | 发表于: {r.venue or '?'} | 引用: {r.citation_count or 'N/A'}")
                if r.abstract:
                    lines.append(f"      摘要: {r.abstract[:150]}...")
            result = "\n".join(lines)

            # Phase 32: offload 搜索结果
            if self.offload_store.should_offload(result, "search_literature"):
                summary = f"搜索'{query}'得到{len(response.results)}条结果"
                self.offload_store.offload(
                    tool_name="search_literature",
                    key=query,
                    content=result,
                    summary=summary,
                    loop_turn=self.state.loop_turns,
                )

            return result
        except Exception as e:
            self._search_log.append({
                "query": query,
                "reason": reason,
                "results_count": 0,
                "source": "error",
                "loop_turn": self.state.loop_turns,
                "error": str(e),
            })
            return f"搜索失败 ({type(e).__name__}: {e})。你可以基于已有知识继续判断，或标记为 'needs_verification'。"

    def _tool_fetch_paper_detail(self, args: dict) -> str:
        """Phase 57: 获取外部论文的详细信息，存入参考文献工作区。
        
        让 Agent 能"翻开"搜索结果中的论文，看到完整摘要、TLDR、
        关键引用关系——就像审稿人从书架上拿下一篇论文来对比方法论。
        """
        paper_id = args.get("paper_id")
        doi = args.get("doi")
        title = args.get("title")
        reason = args.get("reason", "")

        if not any([paper_id, doi, title]):
            return "必须提供 paper_id、doi 或 title 中的至少一个。"

        try:
            from core.web_search import fetch_paper_detail
            detail = fetch_paper_detail(paper_id=paper_id, doi=doi, title=title)

            if detail.error:
                return f"获取论文详情失败: {detail.error}\n原因: {reason}"

            # 存入参考文献工作区
            store_key = detail.paper_id or title or doi or "unknown"
            self.state.reference_papers[store_key] = {
                "title": detail.title,
                "authors": detail.authors,
                "year": detail.year,
                "venue": detail.venue,
                "abstract": detail.abstract,
                "tldr": detail.tldr,
                "citation_count": detail.citation_count,
                "reference_count": detail.reference_count,
                "influential_citation_count": detail.influential_citation_count,
                "fields_of_study": detail.fields_of_study,
                "key_references": detail.key_references,
                "key_citations": detail.key_citations,
                "fetched_at_turn": self.state.loop_turns,
                "fetch_reason": reason,
            }

            # 格式化返回给 Agent 的详细信息
            lines = [f"📄 论文详情: {detail.title}"]
            lines.append(f"   作者: {', '.join(detail.authors[:5])}")
            lines.append(f"   年份: {detail.year or '?'} | 发表于: {detail.venue or '?'}")
            lines.append(f"   引用: {detail.citation_count or 'N/A'} (其中 influential: {detail.influential_citation_count or 'N/A'})")
            lines.append(f"   参考文献数: {detail.reference_count or 'N/A'}")
            if detail.fields_of_study:
                lines.append(f"   领域: {', '.join(detail.fields_of_study)}")

            if detail.tldr:
                lines.append(f"\n   TLDR: {detail.tldr}")

            if detail.abstract:
                lines.append(f"\n   完整摘要: {detail.abstract}")

            if detail.key_references:
                lines.append(f"\n   关键参考文献 (该论文引用的高影响力论文, top {len(detail.key_references)}):")
                for i, ref in enumerate(detail.key_references[:7], 1):
                    lines.append(f"     [{i}] {ref['title']} ({ref['year']}, {ref['venue']})")

            if detail.key_citations:
                lines.append(f"\n   关键后续引用 (引用该论文的高影响力论文, top {len(detail.key_citations)}):")
                for i, cit in enumerate(detail.key_citations[:7], 1):
                    lines.append(f"     [{i}] {cit['title']} ({cit['year']}, {cit['venue']})")

            lines.append(f"\n   [已存入参考文献工作区，共 {len(self.state.reference_papers)} 篇]")

            result = "\n".join(lines)

            # Offload if too long
            if self.offload_store.should_offload(result, "fetch_paper_detail"):
                summary = f"获取了'{detail.title}'的详情 (TLDR: {(detail.tldr or '')[:60]})"
                self.offload_store.offload(
                    tool_name="fetch_paper_detail",
                    key=detail.title or store_key,
                    content=result,
                    summary=summary,
                    loop_turn=self.state.loop_turns,
                )

            return result

        except Exception as e:
            return f"获取论文详情时出错 ({type(e).__name__}: {e})。你可以基于搜索结果中的摘要继续判断。"

    def _tool_read_reference(self, args: dict) -> str:
        """Phase 58: 读取用户提供的参考文献内容。

        Agent 可以按 ref_id 读取特定 section，或列出可用 sections。
        支持 offset 续读长文档，与 read_section 的交互模式一致。
        """
        ref_id = args.get("ref_id", "")
        section = args.get("section", "")
        offset = args.get("offset", 0)
        max_chars = args.get("max_chars", 3000)

        # 如果没有用户参考文献
        if not self.state.user_reference_docs:
            return "当前没有用户提供的参考文献。参考文献工作区中的论文来自 Agent 搜索，请用 fetch_paper_detail 获取详情。"

        # 如果没指定 ref_id，列出所有可用的参考文献
        if not ref_id:
            lines = ["可用的参考文献:"]
            for rid, doc in self.state.user_reference_docs.items():
                sections_str = ", ".join(doc["section_names"][:10])
                lines.append(f"  • {rid}: {doc['title']} (sections: {sections_str})")
            lines.append("\n用 read_reference(ref_id='ref_1', section='abstract') 读取具体内容。")
            return "\n".join(lines)

        # 查找指定的参考文献
        if ref_id not in self.state.user_reference_docs:
            available = ", ".join(self.state.user_reference_docs.keys())
            return f"未找到参考文献 '{ref_id}'。可用的 ref_id: {available}"

        doc = self.state.user_reference_docs[ref_id]

        # 如果没指定 section，列出该文献的所有 sections
        if not section:
            lines = [f"📎 {doc['title']} 的可用 sections:"]
            for sec_name in doc["section_names"]:
                char_count = len(doc["sections"].get(sec_name, ""))
                lines.append(f"  • {sec_name} ({char_count}字)")
            lines.append(f"\n用 read_reference(ref_id='{ref_id}', section='<name>') 读取具体 section。")
            return "\n".join(lines)

        # 模糊匹配 section 名
        matched_section = None
        section_lower = section.lower().strip()
        for sec_name in doc["section_names"]:
            if sec_name.lower() == section_lower:
                matched_section = sec_name
                break
        if not matched_section:
            # 尝试部分匹配
            for sec_name in doc["section_names"]:
                if section_lower in sec_name.lower() or sec_name.lower() in section_lower:
                    matched_section = sec_name
                    break
        if not matched_section:
            available = ", ".join(doc["section_names"])
            return f"在 '{ref_id}' 中未找到 section '{section}'。可用: {available}"

        # 读取内容
        content = doc["sections"].get(matched_section, "")
        total_chars = len(content)

        if offset >= total_chars:
            return f"offset {offset} 超出 section '{matched_section}' 的总长度 ({total_chars}字)。"

        chunk = content[offset:offset + max_chars]
        remaining = total_chars - offset - len(chunk)

        header = f"📎 [{ref_id}] {doc['title']} → section: {matched_section}\n"
        header += f"   ({total_chars}字, 当前 offset={offset}, 返回 {len(chunk)}字"
        if remaining > 0:
            header += f", 剩余 {remaining}字 — 用 offset={offset + len(chunk)} 续读"
        header += ")\n\n"

        return header + chunk

    def _tool_update_findings(self, args: dict) -> str:
        finding = {
            "finding": args["finding"],
            "priority": args.get("priority", "medium"),
            "status": args.get("status", "suggestion"),
            "evidence": args.get("evidence", ""),  # 原文证据
            "section": args.get("section", ""),    # 出处章节
            "recorded_at_turn": self.state.loop_turns,  # Phase 52: 记录产出时的轮次
        }
        
        # Phase 47: 前置去重检查 — 如果新 finding 与已有 finding 高度重叠，提醒而非追加
        if self.state.findings:
            overlap_warning = self._check_finding_overlap(finding)
            if overlap_warning:
                return overlap_warning
        
        self.state.findings.append(finding)
        evidence_note = f" (含原文证据, 来自 '{finding['section']}')" if finding['evidence'] else ""
        base_msg = f"已记录发现{evidence_note} (当前共 {len(self.state.findings)} 条)"

        # === Phase 10: HD-WM 自动增强层 ===
        # 设计哲学：HD-WM 不再要求 Agent 走独立的三步工具路径，
        # 而是在 Agent 已有的 update_findings 行为上自动产生假说记录。
        # 这解决了 LLM 行为经济学问题——Agent 无需改变行为即可获得 HD-WM 的结构化跟踪。
        hdwm_note = self._hdwm_auto_enhance(finding)
        if hdwm_note:
            base_msg += f"\n{hdwm_note}"

        return base_msg

    def _hdwm_auto_enhance(self, finding: dict) -> str:
        """
        Phase 10: HD-WM 自动增强——在 update_findings 路径上自动维护假说生命周期。

        规则:
        1. status=needs_verification → 自动 generate_hypothesis
        2. status=verified/suggestion + 与已有假说匹配 → 自动 add_evidence + resolve
        3. HD-WM 未启用时静默返回空字符串（零副作用）

        设计依据:
        - 解决 G7 Layer 3: LLM 偏好短路径，不会主动调用 3 步 HD-WM 工具
        - 符合 C5 (约束-而非-控制): 不强制 Agent 改变行为，在已有路径上自动受益
        - 假说解决率 (review_readiness) 现在有了实际数据来源
        """
        if not self.enable_hdwm or self.hypothesis_module is None:
            return ""

        status = finding.get("status", "suggestion")
        statement = finding.get("finding", "")
        source = finding.get("section", "unknown") or "unknown"

        # --- 规则 1: needs_verification → 自动生成假说 ---
        if status == "needs_verification":
            hyp = self.hypothesis_module.generate(
                statement=statement,
                source=source,
                turn=self.state.loop_turns,
            )
            # 记录 finding→hypothesis 的映射关系（用于后续自动 resolve）
            finding["_hdwm_hyp_id"] = hyp.id
            return (
                f"[HD-WM] 自动跟踪待验证判断 → 假说 [{hyp.id}] "
                f"(活跃假说: {len(self.hypothesis_module.active_hypotheses)})"
            )

        # --- 规则 2: verified → 尝试匹配并解决之前的假说 ---
        if status == "verified":
            # Phase 11: Verification Integrity Constraint
            # 在 resolve 之前，检查 Agent 是否真的做了调查性行为
            integrity_issue = self._check_verification_integrity(finding)
            if integrity_issue:
                return integrity_issue

            matched_hyp = self._hdwm_match_and_resolve(finding)
            if matched_hyp:
                return (
                    f"[HD-WM] 验证完成 → 假说 [{matched_hyp.id}] 已确认 (supported) "
                    f"| 审稿完成度: {self.hypothesis_module.review_readiness:.0%}"
                )

        # --- 规则 2b: suggestion + 高优先级 + 有证据 → 也尝试匹配解决 ---
        if status == "suggestion" and finding.get("priority") == "high" and finding.get("evidence"):
            matched_hyp = self._hdwm_match_and_resolve(finding)
            if matched_hyp:
                return (
                    f"[HD-WM] 高优发现有充分证据 → 假说 [{matched_hyp.id}] 已确认 "
                    f"| 审稿完成度: {self.hypothesis_module.review_readiness:.0%}"
                )

        return ""

    def _check_verification_integrity(self, finding: dict) -> str:
        """
        Phase 11: Verification Integrity Constraint

        当 Agent 提交 update_findings(status=verified) 且该 finding 匹配一个之前
        的 needs_verification 假说时，检查 Agent 在假说创建之后是否实际执行了
        调查性行为（read_section / search_literature）。

        如果未执行，返回温和提醒字符串（不阻止 finding 记录，但不自动 resolve 假说）。
        如果已执行或无匹配假说，返回空字符串（放行）。

        设计哲学:
        - 约束-而非-控制: 不阻止 Agent 的任何行为，finding 仍正常记录
        - 只影响 HD-WM 的自动 resolve 路径——如果 Agent 没真正调查就标 verified，
          假说不会被自动 resolve，gate checker 在退出时仍会提醒
        - 温和信号: 提示 Agent 去做实际调查，而非惩罚
        """
        if self.hypothesis_module is None:
            return ""

        # 找到对应的假说（精确匹配优先）
        hyp_id = finding.get("_hdwm_hyp_id", "")
        target_hyp = None

        if hyp_id:
            target_hyp = self.hypothesis_module.get_hypothesis(hyp_id)
        else:
            # 尝试模糊匹配，但不 resolve，只是为了找到对应假说的 created_at_turn
            import re as _re
            statement = finding.get("finding", "")
            active_hyps = self.hypothesis_module.active_hypotheses
            if not active_hyps:
                return ""

            def _extract_terms(text: str) -> set:
                en_words = set(_re.findall(r'[a-zA-Z]{4,}', text.lower()))
                stopwords = {'this', 'that', 'with', 'from', 'have', 'been', 'which', 'their',
                             'more', 'than', 'also', 'some', 'other', 'about', 'would', 'could',
                             'should', 'these', 'those', 'into', 'only', 'very', 'such', 'each',
                             'finding', 'section', 'paper', 'author', 'however', 'therefore'}
                return {w for w in en_words if w not in stopwords}

            finding_terms = _extract_terms(statement)
            if len(finding_terms) >= 3:
                best_match = None
                best_overlap = 0.0
                for hyp in active_hyps:
                    hyp_terms = _extract_terms(hyp.statement)
                    if len(hyp_terms) < 3:
                        continue
                    intersection = finding_terms & hyp_terms
                    overlap = len(intersection) / min(len(finding_terms), len(hyp_terms))
                    if overlap > best_overlap:
                        best_overlap = overlap
                        best_match = hyp
                if best_match and best_overlap >= 0.60:
                    target_hyp = best_match

        if target_hyp is None or target_hyp.is_resolved:
            return ""  # 没有匹配的活跃假说，放行

        # 检查 tool_call_history 中，在假说创建之后是否有调查性行为
        investigative_tools = {"read_section", "search_literature"}

        # tool_call_history 没有 turn 信息，但我们可以通过索引估算:
        # 每轮可能有多个 tool call。我们需要找到假说创建之后的 tool calls。
        # 方案: 遍历 history，找到 hypothesis 创建对应的 update_findings 调用位置，
        # 然后检查其后是否有 investigative tool calls。
        #
        # 更精确的方案: 直接检查假说创建以来（按 history 索引）的所有 tool calls
        history = self.state.tool_call_history

        # 找到假说创建点: 那个产生此假说的 update_findings 调用
        # 它的特征: name=update_findings, status=needs_verification, finding text 匹配
        hyp_creation_idx = -1
        for i, call in enumerate(history):
            if call.get("name") == "update_findings":
                call_input = call.get("input", {})
                if (call_input.get("status") == "needs_verification" and
                    call_input.get("finding", "")[:50] == target_hyp.statement[:50]):
                    hyp_creation_idx = i
                    break  # 取第一个匹配的（假说只生成一次）

        if hyp_creation_idx < 0:
            # 无法定位假说创建点（可能是手动生成的假说），放行
            return ""

        # 检查创建点之后是否有 investigative tool calls
        subsequent_calls = history[hyp_creation_idx + 1:]
        has_investigation = any(
            call.get("name") in investigative_tools
            for call in subsequent_calls
        )

        if has_investigation:
            return ""  # Agent 做了调查，放行

        # Agent 没有做任何调查就直接标 verified → 温和提醒
        return (
            f"[HD-WM 完整性提示] 你将「{target_hyp.statement[:60]}」标记为 verified，"
            f"但自假说创建以来尚未观察到 read_section 或 search_literature 调用。"
            f"建议先追查原文证据再确认验证状态。"
            f"（finding 已正常记录，但假说暂不自动 resolve）"
        )

    def _hdwm_match_and_resolve(self, finding: dict) -> "Hypothesis | None":
        """
        尝试将一条 finding 与已有的活跃假说匹配。

        匹配策略:
        1. 精确匹配: finding 内部有 _hdwm_hyp_id 标记（同一条 finding 从 needs_verification → verified）
        2. 模糊匹配: 基于关键词重叠度（与 _check_finding_overlap 类似的思路）

        匹配成功后自动:
        - add_evidence（将 finding 的 evidence 作为证据）
        - resolve（标记为 supported）
        """
        import re as _re

        if self.hypothesis_module is None:
            return None

        active_hyps = self.hypothesis_module.active_hypotheses
        if not active_hyps:
            return None

        statement = finding.get("finding", "")
        evidence_text = finding.get("evidence", "")

        # --- 策略 1: 精确匹配（通过 _hdwm_hyp_id） ---
        hyp_id = finding.get("_hdwm_hyp_id", "")
        if hyp_id:
            hyp = self.hypothesis_module.get_hypothesis(hyp_id)
            if hyp and not hyp.is_resolved:
                # 添加证据并解决
                if evidence_text:
                    self.hypothesis_module.add_evidence(
                        hyp_id=hyp.id,
                        content=evidence_text[:200],
                        direction="for",
                        strength=0.8,
                        source=finding.get("section", ""),
                        turn=self.state.loop_turns,
                    )
                self.hypothesis_module.resolve(
                    hyp_id=hyp.id,
                    status="supported",
                    reason=f"Finding verified: {statement[:80]}",
                    turn=self.state.loop_turns,
                )
                return hyp

        # --- 策略 2: 模糊匹配（关键词重叠） ---
        def _extract_terms(text: str) -> set[str]:
            en_words = set(_re.findall(r'[a-zA-Z]{4,}', text.lower()))
            stopwords = {'this', 'that', 'with', 'from', 'have', 'been', 'which', 'their',
                         'more', 'than', 'also', 'some', 'other', 'about', 'would', 'could',
                         'should', 'these', 'those', 'into', 'only', 'very', 'such', 'each',
                         'finding', 'section', 'paper', 'author', 'however', 'therefore'}
            return {w for w in en_words if w not in stopwords}

        finding_terms = _extract_terms(statement)
        if len(finding_terms) < 3:
            return None

        best_match = None
        best_overlap = 0.0

        for hyp in active_hyps:
            hyp_terms = _extract_terms(hyp.statement)
            if len(hyp_terms) < 3:
                continue
            intersection = finding_terms & hyp_terms
            overlap = len(intersection) / min(len(finding_terms), len(hyp_terms))
            if overlap > best_overlap:
                best_overlap = overlap
                best_match = hyp

        # 阈值: 60% 重叠即认为是同一个认知对象
        if best_match and best_overlap >= 0.60:
            if evidence_text:
                self.hypothesis_module.add_evidence(
                    hyp_id=best_match.id,
                    content=evidence_text[:200],
                    direction="for",
                    strength=0.7,
                    source=finding.get("section", ""),
                    turn=self.state.loop_turns,
                )
            self.hypothesis_module.resolve(
                hyp_id=best_match.id,
                status="supported",
                reason=f"Fuzzy-matched finding verified (overlap={best_overlap:.0%}): {statement[:80]}",
                turn=self.state.loop_turns,
            )
            return best_match

        return None
    
    def _check_finding_overlap(self, new_finding: dict) -> str | None:
        """
        Phase 47: 检查新 finding 是否与已有 findings 高度重叠。
        
        如果重叠度 >= 70%，返回提醒字符串（不追加）。
        如果不重叠，返回 None（允许追加）。
        
        设计原则：
        - 不阻止 Agent 更新已有发现的状态（如 needs_verification → verified）
        - 只阻止"同一个观察记录两次"的情况
        - 如果新 finding 的 status 与已有不同（如升级为 verified），允许追加但提醒合并
        """
        import re as _re
        
        def _extract_terms(text: str) -> set[str]:
            en_words = set(_re.findall(r'[a-zA-Z]{4,}', text.lower()))
            stopwords = {'this', 'that', 'with', 'from', 'have', 'been', 'which', 'their',
                         'more', 'than', 'also', 'some', 'other', 'about', 'would', 'could',
                         'should', 'these', 'those', 'into', 'only', 'very', 'such', 'each',
                         'finding', 'section', 'paper', 'author', 'however', 'therefore',
                         'does', 'will', 'what', 'when', 'where', 'there', 'over', 'under',
                         'between', 'through', 'during', 'before', 'after', 'above', 'below'}
            return {w for w in en_words if w not in stopwords}
        
        new_terms = _extract_terms(new_finding["finding"])
        if len(new_terms) < 3:
            return None  # 太短，无法判断
        
        for i, existing in enumerate(self.state.findings):
            existing_terms = _extract_terms(existing.get("finding", ""))
            if len(existing_terms) < 3:
                continue
            
            intersection = new_terms & existing_terms
            overlap_coeff = len(intersection) / min(len(new_terms), len(existing_terms))
            
            if overlap_coeff >= 0.70:
                # 高度重叠 — 检查是否是状态更新
                new_status = new_finding.get("status", "suggestion")
                old_status = existing.get("status", "suggestion")
                
                if new_status != old_status:
                    # 状态变化：允许追加但建议合并
                    # Phase 10: 继承旧 finding 的 _hdwm_hyp_id（用于自动 resolve）
                    if "_hdwm_hyp_id" in existing and "_hdwm_hyp_id" not in new_finding:
                        new_finding["_hdwm_hyp_id"] = existing["_hdwm_hyp_id"]
                    self.state.findings.append(new_finding)
                    # Phase 10: 重叠允许追加时也触发 HD-WM 自动增强
                    hdwm_note = self._hdwm_auto_enhance(new_finding)
                    # Phase 12: 同步更新原 finding 的状态，防止 gate checker 重复触发
                    # 仅当假说成功 resolve（通过完整性检查）或无假说关联时才同步
                    # 如果完整性检查阻止了 resolve，原 finding 保留 needs_verification
                    # 以确保后续状态更新仍能走"状态变化"路径
                    if new_status == "verified" and old_status == "needs_verification":
                        hyp_id = new_finding.get("_hdwm_hyp_id")
                        if hyp_id and self.hypothesis_module:
                            hyp = self.hypothesis_module.get_hypothesis(hyp_id)
                            if hyp and hyp.is_resolved:
                                existing["status"] = "verified"
                        else:
                            # 无假说关联，直接同步状态
                            existing["status"] = "verified"
                    overlap_msg = (
                        f"已记录，但注意：这条发现与已有发现 #{i+1} 高度重叠 "
                        f"(术语重合 {overlap_coeff:.0%})。"
                        f"已有状态: {old_status} → 新状态: {new_status}。"
                        f"建议：如果是同一个问题的状态更新，考虑直接说明'之前的怀疑已验证/排除'即可。"
                        f" (当前共 {len(self.state.findings)} 条)"
                    )
                    if hdwm_note:
                        overlap_msg += f"\n{hdwm_note}"
                    return overlap_msg
                else:
                    # 同状态重复：不追加，直接提醒
                    return (
                        f"⚠️ 未记录：这条发现与已有发现 #{i+1} 高度重叠 "
                        f"(术语重合 {overlap_coeff:.0%})。"
                        f"重复的发现不增加审稿价值。"
                        f"如果你想更新已有发现的状态或补充证据，请明确说明。"
                        f" (当前仍为 {len(self.state.findings)} 条)"
                    )
        
        return None  # 无重叠，允许追加

    def _tool_review_findings(self, args: dict) -> str:
        """回顾已有发现，支持按过滤器查看。Agent 可用此工具自审、复核。"""
        filter_type = args.get("filter", "all")
        findings = self.state.findings

        if not findings:
            return "当前没有任何发现记录。"

        # 过滤
        if filter_type == "high":
            filtered = [f for f in findings if f.get("priority") == "high"]
        elif filter_type == "needs_verification":
            filtered = [f for f in findings if f.get("status") == "needs_verification"]
        elif filter_type == "verified":
            filtered = [f for f in findings if f.get("status") == "verified"]
        else:
            filtered = findings

        if not filtered:
            return f"按 filter='{filter_type}' 筛选后无匹配项。全部 {len(findings)} 条发现中: " + \
                   f"high={sum(1 for f in findings if f.get('priority')=='high')}, " + \
                   f"needs_verification={sum(1 for f in findings if f.get('status')=='needs_verification')}。"

        lines = [f"发现回顾 (filter='{filter_type}', 共 {len(filtered)}/{len(findings)} 条):"]
        lines.append("="*60)
        for i, f in enumerate(filtered, 1):
            icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(f["priority"], "⚪")
            status_label = {"verified": "✓已验证", "needs_verification": "?待验证", "suggestion": "→建议"}.get(f["status"], f["status"])
            lines.append(f"\n[{i}] {icon} [{status_label}] {f['finding']}")
            if f.get("section"):
                lines.append(f"    📍 出处: {f['section']}")
            if f.get("evidence"):
                # 展示证据，限制长度避免 token 浪费
                ev = f['evidence']
                if len(ev) > 300:
                    ev = ev[:300] + "..."
                lines.append(f"    📄 原文证据: \"{ev}\"")
            else:
                lines.append(f"    ⚠️ 无原文证据 — 建议重新查阅 section 补充")
        lines.append("\n" + "="*60)
        lines.append(f"提示: 对 '待验证' 的发现，可 read_section 重读原文核实，再 update_findings 更新状态。")
        return "\n".join(lines)

    def _tool_edit_section(self, args: dict) -> str:
        section = args.get("section", "")
        new_content = args.get("new_content", "")
        reason = args.get("reason", "")

        self.state.edits.append({
            "section": section,
            "reason": reason,
            "content_preview": new_content[:200] + "..." if len(new_content) > 200 else new_content,
        })

        for key in list(self.state.paper_sections.keys()):
            if section.lower() in key.lower() or key.lower() in section.lower():
                old_content = self.state.paper_sections[key]
                self.state.paper_sections[key] = new_content

                # Phase 20: 零成本三层验证，结果作为反馈返回给 Agent
                all_text = "\n\n".join(self.state.paper_sections.values())
                verification = verify_edit(
                    section_name=key,
                    old_text=old_content,
                    new_text=new_content,
                    all_sections_text=all_text,
                    voice_profile=self.state.voice_profile,
                )
                feedback = format_verification_feedback(verification, key)
                result = f"已修改 section '{key}'（原因: {reason}）\n\n{feedback}"

                # Phase 50: 小模型快速校验（认知分层 — System 1 辅助 System 2）
                checker_warning = self.checker.check_edit(new_content, reason)
                if checker_warning:
                    result += checker_warning

                return result
        return f"未找到 section '{section}'，修改已记录但未应用"

    def _tool_talk_to_user(self, args: dict) -> str:
        message = args.get("message", "")
        expects_reply = args.get("expects_reply", False)
        # 在交互模式下，这里会真正暂停等用户回复
        # 具体行为由 loop 层控制（loop 决定是否 yield 给用户）
        return f"__TALK__|{json.dumps({'message': message, 'expects_reply': expects_reply}, ensure_ascii=False)}"

    def _tool_spawn_perspective(self, args: dict) -> str:
        """发起子视角审视。返回 __SPAWN__ 信号给 loop 层驱动子循环。"""
        lens = args.get("lens", "")
        focus = args.get("focus", "")
        question = args.get("question", "")

        if not lens or not question:
            return "spawn_perspective 需要 lens 和 question 参数。"

        # 打包信息给 loop 层
        spawn_payload = json.dumps({
            "lens": lens,
            "focus": focus,
            "question": question,
        }, ensure_ascii=False)
        return f"__SPAWN__|{spawn_payload}"

    def ingest_perspective_findings(self, findings: list[dict], lens: str, summary: str) -> str:
        """
        将子视角的发现注入主 Agent 的 state。由 loop 层在子循环完成后调用。

        Args:
            findings: 子视角产出的 findings 列表
            lens: 子视角的身份标签
            summary: 子视角的总结

        Returns:
            给主 Agent 的结果摘要
        """
        injected_count = 0
        for f in findings:
            # 标记来源视角，避免混淆
            f["perspective"] = lens
            self.state.findings.append(f)
            injected_count += 1

        # 构建给主 Agent 的结果摘要
        lines = [f"独立视角 [{lens}] 审视完成。"]
        if injected_count > 0:
            lines.append(f"发现 {injected_count} 条问题（已加入你的工作记忆，标记为来自此视角）:")
            for i, f in enumerate(findings, 1):
                icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(f.get("priority", ""), "⚪")
                lines.append(f"  {icon} {f.get('finding', '')[:150]}")
        else:
            lines.append("未发现显著问题。")
        if summary:
            lines.append(f"视角总结: {summary}")
        return "\n".join(lines)

    def create_sub_harness(self, focus_sections: list[str]) -> "Harness":
        """
        创建一个轻量的子 Harness，只包含指定 sections 的内容。
        用于子视角的独立循环。

        Args:
            focus_sections: 需要提供给子视角的 section 名称列表

        Returns:
            一个独立的 Harness 实例（共享论文内容但有独立的 findings/state）
        """
        sub = Harness(max_loop_turns=8, token_budget=30000)
        sub._paper_loaded = True

        # 复制相关 sections 到子 harness
        for key, content in self.state.paper_sections.items():
            if key == "full":
                continue
            # 模糊匹配：focus 里的任何一个能匹配上就给
            for focus in focus_sections:
                if focus.lower() in key.lower() or key.lower() in focus.lower():
                    sub.state.paper_sections[key] = content
                    break

        # 如果没匹配到任何东西，给全部（退化为完整论文）
        if not sub.state.paper_sections:
            sub.state.paper_sections = dict(self.state.paper_sections)

        return sub

    # ----------------------------------------------------------
    # Phase 5: HD-WM 假说工具实现
    # ----------------------------------------------------------

    def _tool_generate_hypothesis(self, args: dict) -> str:
        """产生一个可验证的学术假说。"""
        if self.hypothesis_module is None:
            return "[HD-WM 未激活] 当前未启用假说驱动工作记忆。"

        statement = args.get("statement", "").strip()
        source = args.get("source", "").strip()
        if not statement:
            return "generate_hypothesis 需要 statement 参数（假说陈述）。"
        if not source:
            source = "unknown"

        hyp = self.hypothesis_module.generate(
            statement=statement,
            source=source,
            turn=self.state.loop_turns,
        )
        return (
            f"假说已生成: [{hyp.id}] {hyp.statement}\n"
            f"来源 section: {hyp.source} | 状态: {hyp.status.value}\n"
            f"当前活跃假说数: {len(self.hypothesis_module.active_hypotheses)}"
        )

    def _tool_add_evidence(self, args: dict) -> str:
        """为某个假说添加支持或反对的证据。"""
        if self.hypothesis_module is None:
            return "[HD-WM 未激活] 当前未启用假说驱动工作记忆。"

        hyp_id = args.get("hyp_id", "").strip()
        content = args.get("content", "").strip()
        direction = args.get("direction", "").strip()
        strength = args.get("strength", 0.5)

        if not hyp_id:
            return "add_evidence 需要 hyp_id 参数。"
        if not content:
            return "add_evidence 需要 content 参数（证据内容）。"
        if direction not in ("for", "against"):
            return "add_evidence 的 direction 必须是 'for' 或 'against'。"

        # 确保 strength 是 float
        try:
            strength = float(strength)
        except (TypeError, ValueError):
            strength = 0.5

        evidence = self.hypothesis_module.add_evidence(
            hyp_id=hyp_id,
            content=content,
            direction=direction,
            strength=strength,
            source=args.get("source", ""),
            evidence_type=args.get("type", "direct"),
            turn=self.state.loop_turns,
        )
        if evidence is None:
            return f"添加证据失败: 假说 {hyp_id} 不存在或已解决。"

        hyp = self.hypothesis_module.get_hypothesis(hyp_id)
        balance_desc = ""
        if hyp:
            b = hyp.evidence_balance
            balance_desc = f" | 证据平衡: {b:+.2f}"

        return (
            f"证据已添加到 [{hyp_id}]: {direction} (强度 {strength:.1f})\n"
            f"证据内容: {content[:100]}\n"
            f"当前证据: +{len(hyp.evidence_for)}/-{len(hyp.evidence_against)}{balance_desc}"
        )

    def _tool_resolve_hypothesis(self, args: dict) -> str:
        """解决一个假说——标记为 supported/refuted/suspended。"""
        if self.hypothesis_module is None:
            return "[HD-WM 未激活] 当前未启用假说驱动工作记忆。"

        hyp_id = args.get("hyp_id", "").strip()
        status = args.get("status", "").strip()
        reason = args.get("reason", "").strip()

        if not hyp_id:
            return "resolve_hypothesis 需要 hyp_id 参数。"
        if status not in ("supported", "refuted", "suspended"):
            return "resolve_hypothesis 的 status 必须是 'supported'、'refuted' 或 'suspended'。"

        success = self.hypothesis_module.resolve(
            hyp_id=hyp_id,
            status=status,
            reason=reason,
            turn=self.state.loop_turns,
        )
        if not success:
            return f"解决假说失败: {hyp_id} 不存在或已解决。"

        readiness = self.hypothesis_module.review_readiness
        return (
            f"假说 [{hyp_id}] 已解决 → {status}\n"
            f"理由: {reason}\n"
            f"审稿完成度: {readiness:.0%} | "
            f"解决率: {self.hypothesis_module.resolution_rate:.0%}"
        )

    def _tool_generate_cognitive_hints(self, args: dict) -> str:
        """
        S1: Agent 自主生成审稿认知提示。

        Agent 在初步理解论文后调用，基于自己的判断生成针对性的审稿关注点。
        结果存入 state.cognitive_hints，后续由 assembler 作为参考信息注入 context。
        """
        from core.paper_type_hints import handle_generate_cognitive_hints
        response, hints = handle_generate_cognitive_hints(args)
        if not hints.is_empty():
            self.state.cognitive_hints = hints
            # B4: Agent 生成 hints 后，更新 Completion Gate 配置
            self.gate_config = compute_gate_config(
                cognitive_hints=hints,
                memory_store=self.memory,
                paper_type=hints.paper_type_description,
            )
        return response

    def _tool_reflect_and_plan(self, args: dict) -> str:
        """元认知工具：Agent 主动触发反思。委托 tool_reflect 模块。"""
        result, new_strategy = _tr_reflect_and_plan(
            state=self.state,
            cognitive_state=self.cognitive_state,
            strategy_transitions=self._strategy_transitions,
            last_strategy=self._last_strategy,
            search_log=getattr(self, '_search_log', []),
            gate_config=self.gate_config,
            args=args,
        )
        self._last_strategy = new_strategy

        # 记录反思事件
        if not hasattr(self, '_reflection_log'):
            self._reflection_log = []
        self._reflection_log.append({
            "turn": self.state.loop_turns,
            "trigger": args.get("trigger", "自主反思"),
            "findings_count": len(self.state.findings),
            "current_thinking": args.get("current_thinking", "")[:100],
            "cognitive_strategy": self.cognitive_state.current_strategy,
        })

        return result

    def _check_stagnation(self, current_tool: str) -> str | None:
        """Phase 55: 停滞检测。委托 tool_reflect 模块。"""
        signal, new_turn = _tr_check_stagnation(
            state=self.state,
            gate_config=self.gate_config,
            last_stagnation_signal_turn=getattr(self, '_last_stagnation_signal_turn', 0),
            current_tool=current_tool,
        )
        if signal:
            self._last_stagnation_signal_turn = new_turn
        return signal

    def _tool_detect_ai_signals(self, args: dict) -> str:
        """Phase 22: 调用程序化 AI 信号检测器。"""
        text = args.get("text", "")
        if not text:
            return "错误: 'text' 参数为空。请传入要检测的文本内容。"
        
        from core.deai_detector import detect_ai_signals
        result = detect_ai_signals(text)
        return result.summary()

    def _tool_verify_citations(self, args: dict) -> str:
        """Phase 22: 验证参考文献完整性和引用一致性。"""
        bib_content = args.get("bib_content", "")
        tex_content = args.get("tex_content", "")
        project_dir = args.get("project_dir", "")
        check_orphaned = args.get("check_orphaned", True)

        if not bib_content and not project_dir:
            return "错误: 请传入 bib_content（.bib 文件内容）或 project_dir（项目目录路径）。"

        from core.bib_verify import verify_citations
        result = verify_citations(
            bib_content=bib_content or None,
            tex_content=tex_content or None,
            project_dir=project_dir or None,
            check_orphaned=check_orphaned,
        )
        return result.summary()

    def _tool_recall_context(self, args: dict) -> str:
        """Phase 32: 从 offload store 回查之前卸载的完整内容。"""
        ref_id = args.get("ref_id", "")
        key = args.get("key", "")

        if ref_id:
            content = self.offload_store.recall(ref_id)
            if content:
                return f"[回查 {ref_id}] 完整内容 ({len(content)} chars):\n\n{content}"
            return f"[回查失败] 找不到 ref_id='{ref_id}'。请检查可用的 ref_id 列表。"
        elif key:
            content = self.offload_store.recall_by_key(key)
            if content:
                return f"[回查 '{key}'] 完整内容 ({len(content)} chars):\n\n{content}"
            return f"[回查失败] 找不到 key='{key}' 的卸载内容。"
        else:
            return "错误: 请传入 ref_id (如 'ref_003') 或 key (如 section 名)。"

    def _tool_request_phase_transition(self, args: dict) -> str:
        """Agent 请求阶段转换。

        参数:
            target_phase: 目标阶段名 (initial_scan / deep_review / editing / synthesis)
            reason: Agent 为什么要转换（用于日志，不影响逻辑）
        """
        target_name = args.get("target_phase", "").strip().lower()
        reason = args.get("reason", "")

        # 解析目标阶段
        try:
            target = Phase(target_name)
        except ValueError:
            valid = [p.value for p in Phase]
            return (
                f"无效的目标阶段: '{target_name}'。"
                f"有效选项: {', '.join(valid)}"
            )

        # 收集前置条件检查所需的数据
        sections_read = len(self.state.sections_read)
        verified_findings = sum(
            1 for f in self.state.findings
            if f.get("confidence") == "verified"
        )

        # 请求转换
        result = self.phase_fsm.request_transition(
            target=target,
            sections_read=sections_read,
            verified_findings=verified_findings,
        )

        if result.allowed:
            # 阶段转换成功 → invalidate PHASE 缓存
            self.assembler.registry.invalidate_phase_cache()
            return (
                f"阶段转换成功: {result.from_phase.value} → {result.to_phase.value}。"
                f" {result.reason}"
            )
        else:
            return (
                f"阶段转换被拒绝: 无法从 {result.from_phase.value} "
                f"转到 {result.to_phase.value}。原因: {result.reason}"
            )

    def _tool_done(self, args: dict) -> str:
        summary = args.get("summary", "")

        # Phase 50: Pre-Completion Check（小模型快速扫描遗漏）
        # 在 quality gate 之前执行——如果 Checker 发现明显盲区，作为 nudge 返回
        abstract = self.state.paper_sections.get("abstract", "")
        if not abstract:
            # 尝试模糊匹配 abstract
            for key in self.state.paper_sections:
                if "abstract" in key.lower():
                    abstract = self.state.paper_sections[key]
                    break
        checker_nudge = self.checker.check_pre_completion(
            abstract=abstract,
            findings=self.state.findings,
        )
        if checker_nudge:
            return f"__NUDGE__|[Checker 校验] {checker_nudge}"

        # 在返回前检查 completion quality gate
        gate_result = self._check_completion_gate()
        if gate_result:
            return f"__NUDGE__|{gate_result}"

        # K1: 构建审稿认知图谱（零 LLM 调用）
        self.state.cognition_graph = build_cognition_graph(
            state=self.state,
            hypothesis_module=self.hypothesis_module,
            cognitive_hints=self.state.cognitive_hints,
        )

        return f"__DONE__|{summary}"

    # ----------------------------------------------------------
    # 边界守护 (委托给 boundary_guard.py)
    # ----------------------------------------------------------

    def check_doom_loop(self) -> str | None:
        """边界守护：硬截断检查。"""
        return _bg_check_doom_loop(self.state)

    def check_soft_turn_limit(self) -> str | None:
        """认知自评提问（Phase 28）。"""
        return _bg_check_soft_turn_limit(
            self.state,
            self.gate_config,
            self.state.tool_call_history,
            getattr(self, '_search_log', []),
        )

    def check_cognitive_output(self) -> str | None:
        """Phase 17: 认知产出催促器。"""
        return _bg_check_cognitive_output(self.state)

    def track_cognitive_output(self, tool_name: str):
        """Phase 17: 追踪工具使用类型。"""
        _bg_track_cognitive_output(self.state, tool_name)

    def increment_read_turn(self):
        """Phase 17: 由 loop 在无产出轮次结束时调用。"""
        _bg_increment_read_turn(self.state)

    def check_reflection_needed(self) -> str | None:
        """Phase 37+40+41: 反思催促器。"""
        return _bg_check_reflection_needed(
            self.state,
            getattr(self, '_reflection_log', []),
            getattr(self, '_search_log', []),
        )

    def check_token_budget(self) -> str | None:
        """Phase 16/45: Token 预算检查。"""
        result, updated = _bg_check_token_budget(
            self.state, getattr(self, '_cost_warned', False)
        )
        if updated:
            self._cost_warned = True
        return result

    def _check_completion_gate(self) -> str | None:
        """Completion Quality Gate（内部方法，由 _tool_done 调用）。"""
        if not hasattr(self, '_completion_nudges_fired'):
            self._completion_nudges_fired: set[str] = set()
        result, self._completion_nudges_fired = _bg_check_completion_gate(
            self.state,
            self.gate_config,
            self.hypothesis_module,
            self.finding_quality_gate,
            self._completion_nudges_fired,
        )
        return result

    def increment_turn(self, usage: dict | None = None):
        """每轮 loop 结束时调用，更新统计。"""
        self.state.loop_turns += 1
        if usage:
            self.state.total_tokens += usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)

        # Phase 13: Session Memory — 在认知断点时更新审稿笔记
        if self.session_memory.should_update(self.state):
            # 构建 recent_activity 摘要
            recent_activity = ""
            if self.state.tool_call_history:
                recent_tools = [
                    entry.get("tool", "?")
                    for entry in self.state.tool_call_history[-3:]
                ]
                recent_activity = " → ".join(recent_tools)

            # 收集新增 findings（自上次更新以来）
            new_findings = self.state.findings[self.session_memory._last_findings_count:]

            self.session_memory.update_sync(
                self.state,
                recent_activity=recent_activity,
                new_findings=new_findings,
            )

    def new_conversation_turn(self):
        """用户发了新消息，开始新的对话轮次。重置单轮 loop 计数。"""
        self.state.conversation_turns += 1
        self.state.loop_turns = 0  # 每轮对话重置 loop 计数器（防止累计触发 doom loop）

    # ----------------------------------------------------------
    # Phase 15: 会话结束时的记忆沉淀 — 委托 session_finalizer 模块
    # ----------------------------------------------------------

    def end_session(self, paper_title: str = "", user_messages: list[str] | None = None):
        """会话结束时调用: 将当前会话的认知产出沉淀到跨会话记忆。"""
        _sf_end_session(
            state=self.state,
            memory=self.memory,
            paper_id=self._paper_id,
            strategy_transitions=self._strategy_transitions if self._strategy_transitions else None,
            paper_title=paper_title,
            user_messages=user_messages,
        )

    # ----------------------------------------------------------
    # Context Window 管理 (委托给 message_compressor.py)
    # ----------------------------------------------------------

    def compress_messages(self, messages: list[dict], keep_recent: int = 6) -> list[dict]:
        """压缩 messages 列表以控制 context window 膨胀。"""
        from core.message_compressor import compress_messages as _mc_compress
        return _mc_compress(
            messages,
            self.state,
            self.compaction_engine,
            self.session_memory,
            self.hypothesis_module,
            keep_recent=keep_recent,
        )


# ============================================================
# Section 分类 — 为 format_context 提供优先级信号
# ============================================================

# 核心审阅 section：通常包含论文的核心贡献和可审查的实质内容
_CORE_PATTERNS = re.compile(
    r"(abstract|introduction|method|result|experiment|finding|"
    r"empirical|analysis|conclusion|discussion|"
    r"main result|baseline|treatment effect|robustness)",
    re.IGNORECASE,
)

# 可跳过 section：对审稿几乎无价值
_SKIP_PATTERNS = re.compile(
    r"(reference|bibliography|acknowledge|appendix|"
    r"author|affiliation|supplementar|table of content)",
    re.IGNORECASE,
)


def _classify_section(name: str) -> str:
    """
    将 section 名称分类为 core/support/skip。
    
    - core: 审稿核心内容（方法、结果、结论）
    - skip: 几乎无需阅读（参考文献、致谢）
    - support: 其他（数据描述、相关工作、背景等）
    """
    if _SKIP_PATTERNS.search(name):
        return "skip"
    if _CORE_PATTERNS.search(name):
        return "core"
    return "support"


# ============================================================
# Phase 16: Section Digest Generator — 纯启发式，不调 LLM
# ============================================================

def _generate_section_digest(section_name: str, content: str) -> str:
    """
    为已读 section 生成一个 1-2 句话的结构化摘要。
    
    设计原则:
    - 纯启发式（不调 LLM），零额外 API 成本
    - 目标：让 Agent 在 section 原文被压缩出 messages 后，
      仍能回溯"这个 section 讲了什么"而不需要重新 read_section
    - 不是完美的摘要——是"够用的记忆锚点"
    
    策略:
    1. 提取第一句有意义的文本（通常是 section 的核心主张）
    2. 统计关键数字（表格行数、公式、引用数）
    3. 拼接为 ≤150 字符的摘要
    """
    if not content or len(content) < 50:
        return f"({len(content)} chars, 内容极少)"
    
    # 去掉 markdown 标题行
    lines = [l.strip() for l in content.split("\n") if l.strip() and not l.strip().startswith("#")]
    
    # 提取第一句有实质内容的文本（跳过空行和表格标记）
    first_sentence = ""
    for line in lines:
        # 跳过表格分隔符、图片引用、空白占位
        if line.startswith("|") or line.startswith("![") or line.startswith("---"):
            continue
        # 取第一句（句号/问号/感叹号截断）
        for end_char in ["。", ".", "?", "？", "!", "！"]:
            idx = line.find(end_char)
            if idx > 10:  # 至少有一些内容
                first_sentence = line[:idx + 1]
                break
        if first_sentence:
            break
        # 如果一整行没有句号，取前 100 字符
        if len(line) > 20:
            first_sentence = line[:100]
            break
    
    if not first_sentence:
        first_sentence = lines[0][:80] if lines else "无内容"
    
    # 统计特征
    features = []
    table_rows = sum(1 for l in content.split("\n") if l.strip().startswith("|"))
    if table_rows > 2:
        features.append(f"含{table_rows}行表格")
    
    num_count = len(re.findall(r'\d+\.\d+', content))
    if num_count > 5:
        features.append(f"~{num_count}个数值")
    
    # 组装 digest (≤150 chars)
    digest = first_sentence[:120]
    if features:
        digest += f" [{', '.join(features)}]"
    
    return digest[:150]
