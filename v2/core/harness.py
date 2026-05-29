"""
core/harness.py -- Harness: Agent 的状态守护层

设计原则 (来自 COGNITIVE_ANCHOR S5.2, S4.3):
    - Harness 不控制 Agent 做什么，只守护边界
    - Harness 替 LLM 记住一切（LLM 是无状态 CPU）
    - Harness 在每轮提供 context，执行 tool call，检查约束

职责:
    1. 状态持久化 -- 论文内容、发现、修改历史、对话记忆
    2. 边界守护 -- doom loop guard、completion quality gate、token budget
    3. 工具执行 -- 接收 tool call，执行并返回结果
    4. Context 组装 -- 每轮为 LLM 组装当前状态摘要

不做:
    - 不决定 Agent 下一步做什么
    - 不路由到不同 pipeline
    - 不维护 tool registry（Agent 的 tools 在 identity 里定义）
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Callable

from core.state import WorkspaceState, EditPlan, EditStep
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
from core.edit_plan_validator import (
    validate_edit_plan,
    format_validation_nudge,
)
from core.claim_signal import detect_verifiable_claims
from core.metacognition import CognitiveState
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from core.meta_cognition_layer import MetaCognitionLayer
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
    check_auto_spawn_needed as _bg_check_auto_spawn_needed,
)
from core.paper_loader import (
    load_paper as _pl_load_paper,
    load_user_references as _pl_load_user_references,
)
from core.session_finalizer import end_session as _sf_end_session
from core.session_finalizer import end_session_with_reflection as _sf_end_session_async
from core.tool_reflect import (
    reflect_and_plan as _tr_reflect_and_plan,
    check_stagnation as _tr_check_stagnation,
)

# --- tool_handlers imports ---
from core.tool_handlers.reading import (
    tool_read_section as _th_read_section,
    tool_search_literature as _th_search_literature,
    tool_fetch_paper_detail as _th_fetch_paper_detail,
    tool_read_reference as _th_read_reference,
    _generate_section_digest,
)
from core.tool_handlers.findings import (
    tool_update_findings as _th_update_findings,
    tool_review_findings as _th_review_findings,
)
from core.tool_handlers.editing import (
    tool_generate_edit_plan as _th_generate_edit_plan,
    tool_edit_paragraph as _th_edit_paragraph,
    tool_reword_sentence as _th_reword_sentence,
    tool_insert_content as _th_insert_content,
    tool_edit_section as _th_edit_section,
    resolve_section_key as _th_resolve_section_key,
)
from core.tool_handlers.hypothesis import (
    tool_generate_hypothesis as _th_generate_hypothesis,
    tool_add_evidence as _th_add_evidence,
    tool_resolve_hypothesis as _th_resolve_hypothesis,
)
from core.tool_handlers.metacognition import (
    tool_generate_cognitive_hints as _th_generate_cognitive_hints,
    tool_reflect_and_plan as _th_reflect_and_plan,
    check_stagnation as _th_check_stagnation,
)
from core.tool_handlers.misc import (
    tool_talk_to_user as _th_talk_to_user,
    tool_spawn_perspective as _th_spawn_perspective,
    tool_spawn_parallel_readers as _th_spawn_parallel_readers,
    tool_detect_ai_signals as _th_detect_ai_signals,
    tool_verify_citations as _th_verify_citations,
    tool_recall_context as _th_recall_context,
    tool_request_phase_transition as _th_request_phase_transition,
    tool_done as _th_done,
    tool_switch_persona as _th_switch_persona,
    tool_switch_model as _th_switch_model,
)
from core.mcp_bridge import register_mcp_tools as _register_mcp_tools
from core.mcp_loader import MCPServiceLoader
from core.plugin_installer import tool_manage_plugins as _th_manage_plugins
from core.evolution import EvolutionEngine, AblationConfig
from core.adaptive_config import AdaptiveConfig
from core.budget_policy import BudgetPolicy

logger = logging.getLogger(__name__)


# ============================================================
# Harness -- 守护层
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

    def __init__(self, paper_path: str | None = None, max_loop_turns: int = 50, token_budget: int = 200_000, context_window: int = 128_000, memory_dir: str | Path | None = None, persona: str = "scholar", reference_paths: list[str] | None = None, enable_hdwm: bool = False, budget_policy: BudgetPolicy | None = None, session_model_mgr=None):
        self.state = WorkspaceState(
            paper_path=paper_path,
            max_loop_turns=max_loop_turns,
            token_budget=token_budget,
            context_window=context_window,
        )
        # Token Budget Policy: 向后兼容——不传时从 token_budget 构建
        if budget_policy is not None:
            self.budget_policy = budget_policy
        else:
            self.budget_policy = BudgetPolicy(token_limit=token_budget)
        self._paper_loaded = False
        self._persona = persona
        self.enable_hdwm = enable_hdwm
        # Multi-model: 可选的 SessionModelManager，用于 budget 委托
        self._session_model_mgr = session_model_mgr
        if paper_path:
            _pl_load_paper(self.state, paper_path)
            self._paper_loaded = True

        # Phase 58: 加载用户提供的参考文献
        if reference_paths:
            _pl_load_user_references(self.state, reference_paths)

        # Phase 15: 跨会话记忆
        if memory_dir:
            self._memory_dir = Path(memory_dir)
        elif paper_path:
            p = Path(paper_path)
            self._memory_dir = (p.parent if p.is_file() else p) / ".memory"
        else:
            self._memory_dir = Path(".memory")
        self.memory = MemoryStore(self._memory_dir)
        self.memory.load()
        self._paper_id: str | None = None
        if self._paper_loaded and self.state.paper_sections:
            self._paper_id = MemoryStore.compute_paper_id(self.state.paper_sections)

        # Phase 32: 元认知自我模型
        self.cognitive_state = CognitiveState()

        # Phase 54: 策略切换追踪
        self._strategy_transitions: list[tuple[str, str]] = []
        self._last_strategy: str = "undecided"

        # Phase 50/55: 认知校验层
        self.checker = CognitiveChecker(persona=persona, session_model_mgr=session_model_mgr)

        # Phase 32: 可恢复的上下文卸载
        workspace_root = Path(paper_path).parent if paper_path and Path(paper_path).is_file() else Path(paper_path or ".")
        refs_dir = workspace_root / ".workspace" / "refs"
        self.offload_store = OffloadStore(refs_dir=refs_dir)

        # v2 Phase 4: Phase FSM
        # Writer persona 天然需要编辑工具，直接从 EDITING 阶段开始
        # （与 switch_persona → writer 的 force_transition(EDITING) 逻辑统一）
        if persona == "writer":
            self.phase_fsm = PhaseFSM(initial_phase=Phase.EDITING)
        else:
            self.phase_fsm = PhaseFSM()

        # v2 Phase 5: HD-WM
        self.hypothesis_module: HypothesisModule | None = None
        if self.enable_hdwm:
            self.hypothesis_module = HypothesisModule()

        # === V3 Phase 3: Link hypothesis module to cognitive state (SoT unification) ===
        if self.hypothesis_module is not None:
            self.cognitive_state.set_hypothesis_module(self.hypothesis_module)

        # v2: ToolRegistry
        self._init_tool_registry()

        # v2: Smart Compaction Engine
        self.compaction_engine = CompactionEngine()

        # v2 Phase 13: Session Memory
        self.session_memory = SessionMemoryManager()

        # Q1: Finding Quality Gate
        self.finding_quality_gate = FindingQualityGate()

        # B4: Completion Gate 动态配置
        self.gate_config = CompletionGateConfig()

        # B3: AdaptiveConfig — runtime 参数自适应
        self.adaptive_config = AdaptiveConfig()

        # P2: Evolution Engine（跨任务自我进化）
        self.evolution_engine = EvolutionEngine(self.memory)
        from core.habits import COGNITIVE_HABITS, HabitSelector
        existing_habit_ids = {h.id for h in COGNITIVE_HABITS}
        self.evolution_engine.initialize(
            existing_habit_ids,
            paper_sections=self.state.paper_sections or None,
        )

        # V3 Phase 1: Store contrast plan in state for session_finalizer access
        self.state.contrast_plan = self.evolution_engine.get_contrast_plan()

        # P2: 将学习习惯注入 HabitSelector
        habit_selector = HabitSelector()
        learned_as_habits = self.evolution_engine.get_habits_for_selector()
        if learned_as_habits:
            habit_selector.extend_with_learned(learned_as_habits)

        # V3: Token Budget Manager（需在 Assembler 之前初始化，供 Zone B 动态加载）
        self.token_budget_manager = None
        from core.godel_config import GODEL_BUDGET_MANAGER_ENABLED
        if GODEL_BUDGET_MANAGER_ENABLED:
            from core.token_budget import TokenBudgetManager
            self.token_budget_manager = TokenBudgetManager(
                total_budget=context_window,
            )

        # V4: SkillRegistry + TemplateRegistry（需在 Assembler 之前初始化）
        self.skill_registry = None
        self.template_registry = None
        self._skill_handler_loader = None
        self._action_tool_schemas: list[dict] = []
        from core.godel_config import GODEL_SKILL_LOADING_ENABLED
        if GODEL_SKILL_LOADING_ENABLED:
            from core.skill_registry import SkillRegistry, TemplateRegistry
            skills_dir = Path(__file__).parent.parent / "skills"
            if skills_dir.exists():
                self.skill_registry = SkillRegistry(skills_dir)
                templates_dir = skills_dir / "templates"
                if templates_dir.exists():
                    self.template_registry = TemplateRegistry(templates_dir)
                # V4 D1: 初始化 Handler 加载器
                from core.skill_handler_loader import SkillHandlerLoader
                self._skill_handler_loader = SkillHandlerLoader(skills_dir)

        # V4 D1: 动态注册操作型 Skill 的 tools（必须在 skill_registry 初始化后执行）
        self._register_action_skill_tools()

        # V5: SkillX 三层技能体系 (Phase 3 集成)
        self.skillx: "SkillXIntegration | None" = None
        from core.godel_config import GODEL_SKILLX_ENABLED
        if GODEL_SKILLX_ENABLED:
            try:
                from core.skillx_integration import SkillXIntegration
                self.skillx = SkillXIntegration(
                    legacy_registry=self.skill_registry,
                    handler_loader=self._skill_handler_loader,
                    event_bus=getattr(self, "_event_bus", None),
                )
                # 注册 apply_skill tool (所有 Phase 可用)
                self.tool_registry.register(
                    "apply_skill", self._tool_apply_skill, phases=None
                )
            except Exception as exc:
                logger.warning(
                    "[Harness] SkillX initialization failed (graceful degradation): %s",
                    exc,
                    exc_info=True,
                )
                self.skillx = None

        # v2 Phase 3: Context Assembler
        self.assembler = ContextAssembler(
            memory=self.memory,
            cognitive_state=self.cognitive_state,
            offload_store=self.offload_store,
            habit_selector=habit_selector,
            hypothesis_module=self.hypothesis_module,
            evolution_engine=self.evolution_engine,
            token_budget_manager=self.token_budget_manager,
            skill_registry=self.skill_registry,
        )

        # MCL: MetaCognitionLayer（延迟绑定 LLM client，由 Agent 在创建后注入）
        self.mcl: MetaCognitionLayer | None = None

        # V3 Phase 0.5: Paper Cognition Graph
        self._init_pcg()

        # V3 Phase 0.5: Signal Dispatcher
        from core.godel_config import GODEL_SIGNAL_DISPATCHER_ENABLED
        if GODEL_SIGNAL_DISPATCHER_ENABLED:
            from core.signal_dispatcher import SignalDispatcher
            self.signal_dispatcher = SignalDispatcher()

        # V3 Phase 0.5: EvidenceChain Tracker
        from core.godel_config import GODEL_EVIDENCE_CHAIN_ENABLED
        if GODEL_EVIDENCE_CHAIN_ENABLED:
            from core.evidence_chain import EvidenceChainTracker
            self.evidence_tracker = EvidenceChainTracker()

        # V3 Phase 0.5: Godel Config 状态日志
        from core.godel_config import log_config_status
        log_config_status()

    # ----------------------------------------------------------
    # V3 Phase 0.5: PCG 初始化
    # ----------------------------------------------------------

    def _init_pcg(self) -> None:
        """构建 Paper Cognition Graph（如 PaperStructureIndex 已存在）。

        降级策略: GODEL_PCG_ENABLED=0 或构建失败 → state.paper_cognition_graph 保持 None。
        构建成功后同步更新 TokenBudgetManager.pcg（如已初始化）。
        """
        from core.godel_config import GODEL_PCG_ENABLED
        if not GODEL_PCG_ENABLED:
            return
        if self.state.paper_structure_index is None:
            return
        from core.paper_cognition_graph import PaperCognitionGraph, get_template_for_paper_type
        from core.review_checklist import ReviewChecklist
        pcg = PaperCognitionGraph.from_structure_index(self.state.paper_structure_index)
        if not pcg.is_empty():
            self.state.paper_cognition_graph = pcg
            # S3: 从 DomainTemplate 初始化 ReviewChecklist
            template = get_template_for_paper_type(pcg.paper_type)
            if template and template.methodology_checklist:
                self.state.review_checklist = ReviewChecklist.from_template(
                    pcg.paper_type, template.methodology_checklist
                )
                # S1-auto: 自动从 DomainTemplate 预生成 CognitiveHints（如 Agent 尚未生成）
                # 设计原则: 让 Agent 进入 loop 时就已有结构化审稿策略，
                # 而非依赖 Agent 自主调用 generate_cognitive_hints（它可能跳过）。
                # Agent 仍可通过 generate_cognitive_hints 覆盖/细化这些预生成的 hints。
                if self.state.cognitive_hints is None or self.state.cognitive_hints.is_empty():
                    from core.paper_type_hints import CognitiveHints
                    self.state.cognitive_hints = CognitiveHints(
                        paper_type_description=f"{pcg.paper_type} 类型论文",
                        focus_dimensions=list(template.focus_hints) if template.focus_hints else [],
                        typical_weaknesses=list(template.methodology_checklist[:4]),
                        verification_strategies=[
                            f"针对「{item.split('：')[0] if '：' in item else item[:20]}」逐 section 验证"
                            for item in template.methodology_checklist[:3]
                        ],
                    )
                    logger.info(
                        "S1-auto: Pre-generated CognitiveHints from DomainTemplate "
                        "(paper_type=%s, %d focus_dims, %d weaknesses)",
                        pcg.paper_type,
                        len(self.state.cognitive_hints.focus_dimensions),
                        len(self.state.cognitive_hints.typical_weaknesses),
                    )
            # 根据 paper_type 更新 gate_config 的 min_findings（如果尚未被 hints 覆盖）
            if self.gate_config.min_findings_for_exit == 0 and pcg.paper_type:
                updated_config = compute_gate_config(
                    cognitive_hints=None,
                    memory_store=self.memory,
                    paper_type=pcg.paper_type,
                )
                if updated_config.min_findings_for_exit > 0:
                    self.gate_config.min_findings_for_exit = updated_config.min_findings_for_exit

    # ----------------------------------------------------------
    # 论文加载 -- 委托 paper_loader 模块
    # ----------------------------------------------------------

    def load_paper(self, path: str | None = None):
        """公开接口: 加载论文。如已加载则跳过。"""
        if self._paper_loaded:
            return
        target = path or self.state.paper_path
        if target:
            _pl_load_paper(self.state, target)
            self._paper_loaded = True
            # V3: 论文加载后构建 PCG
            self._init_pcg()

    # ----------------------------------------------------------
    # S1-LLM: 前置 LLM 调用生成高质量 CognitiveHints
    # ----------------------------------------------------------

    async def pre_generate_cognitive_hints(
        self,
        llm_call_fn: Callable[[str, str, int], Any],
    ) -> bool:
        """在 cognitive_loop 启动前，用一次 LLM 调用深度加工审稿策略。

        设计原则 (Depth of Processing Effect):
            methodology_checklist 已通过 PCG 存在于 Agent context 中，但以"地图节点列表"
            形式被淹没。让 LLM 做一次显式加工——基于论文摘要+方法论checklist 写一段
            针对性审稿策略——能显著提升后续 deep review 的系统性。

        前置条件:
            - paper 已加载 (state.paper_sections 非空)
            - review_checklist 已初始化 (由 _init_pcg S3 完成)
            - state.cognitive_hints 为 seed 状态（由 _init_pcg S1-auto 填充）或为空

        Args:
            llm_call_fn: async 函数 (system, user, max_tokens) -> str
                         与 Agent/Loop 层共享同一 LLM client

        Returns:
            True 如果成功通过 LLM 生成了 CognitiveHints，False 则保留 seed/fallback
        """
        # 前置条件检查
        if not self.state.paper_sections:
            return False
        if self.state.review_checklist is None:
            return False

        # 收集 LLM 输入素材
        # 1. Paper abstract
        abstract = self.state.paper_sections.get("abstract", "")
        if not abstract:
            for key in self.state.paper_sections:
                if "abstract" in key.lower():
                    abstract = self.state.paper_sections[key]
                    break
        abstract = abstract[:3000] if abstract else ""

        # 2. Section structure
        section_names = [k for k in self.state.paper_sections if k != "full"]

        # 3. Methodology checklist from DomainTemplate
        checklist_items: list[str] = []
        if self.state.review_checklist and hasattr(self.state.review_checklist, 'items'):
            checklist_items = [item.description for item in self.state.review_checklist.items[:8]
                              if hasattr(item, 'description')]

        # 4. Paper type from PCG
        paper_type = ""
        if self.state.paper_cognition_graph:
            paper_type = self.state.paper_cognition_graph.paper_type or ""

        # 5. Existing seed hints (from programmatic S1-auto)
        seed_info = ""
        if self.state.cognitive_hints and not self.state.cognitive_hints.is_empty():
            seed_info = self.state.cognitive_hints.format_for_context()

        # 构造 prompt
        system_prompt = (
            "你是一位资深学术审稿人。根据提供的论文摘要、结构、领域方法论审查要点，"
            "生成一份针对性的审稿策略。输出必须严格遵循 JSON 格式。"
        )

        user_prompt = f"""请基于以下论文信息，生成针对性的审稿认知策略。

## 论文类型
{paper_type or '待判断'}

## 摘要
{abstract or '（摘要未提取）'}

## 论文结构
{', '.join(section_names[:20])}

## 领域方法论审查要点
{chr(10).join(f'- {item}' for item in checklist_items) if checklist_items else '（无模板匹配）'}

{f'## 初步策略（seed，待你深化）{chr(10)}{seed_info}' if seed_info else ''}

---

请输出 JSON（不要 markdown 代码块），包含以下字段：
- "paper_type_description": 对论文类型/方法论特征的精确描述（1-2句话）
- "focus_dimensions": 3-5个关键审查维度（基于这篇论文的具体方法，不要泛泛而谈）
- "typical_weaknesses": 2-4个这类论文的典型弱点（结合摘要中的具体方法）
- "verification_strategies": 2-3个你计划的验证策略（可操作的，如"检查Table X的..."）

要求：
1. focus_dimensions 和 typical_weaknesses 必须针对这篇论文的具体方法论，不是通用建议
2. verification_strategies 应该具体到可以在论文中执行的动作
3. 基于审查要点深化，不要简单复述"""

        try:
            response = await llm_call_fn(system_prompt, user_prompt, 1500)
            if not response or not response.strip():
                logger.warning("S1-LLM: LLM returned empty response, keeping seed hints.")
                return False

            # 解析 JSON 响应
            import json as _json
            # 容错：去除可能的 markdown 代码块标记
            cleaned = response.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()
            if cleaned.startswith("json"):
                cleaned = cleaned[4:].strip()

            parsed = _json.loads(cleaned)

            from core.paper_type_hints import CognitiveHints
            hints = CognitiveHints(
                paper_type_description=parsed.get("paper_type_description", paper_type),
                focus_dimensions=parsed.get("focus_dimensions", []),
                typical_weaknesses=parsed.get("typical_weaknesses", []),
                verification_strategies=parsed.get("verification_strategies", []),
            )

            if hints.is_empty():
                logger.warning("S1-LLM: Parsed hints are empty, keeping seed hints.")
                return False

            # 成功：用 LLM 生成的高质量 hints 覆盖 seed
            self.state.cognitive_hints = hints

            # 同步更新 gate_config
            new_config = compute_gate_config(
                cognitive_hints=hints,
                memory_store=self.memory,
                paper_type=hints.paper_type_description,
            )
            self.gate_config = new_config

            logger.info(
                "S1-LLM: Generated CognitiveHints via LLM "
                "(paper_type=%s, %d focus_dims, %d weaknesses, %d strategies)",
                hints.paper_type_description[:50],
                len(hints.focus_dimensions),
                len(hints.typical_weaknesses),
                len(hints.verification_strategies),
            )
            return True

        except (_json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.warning(
                "S1-LLM: Failed to parse LLM response as CognitiveHints: %s. Keeping seed hints.",
                exc,
            )
            return False
        except Exception as exc:
            logger.warning(
                "S1-LLM: LLM call failed: %s. Keeping seed hints (programmatic fallback).",
                exc,
            )
            return False

    def load_references(self, paths: list[str]):
        """公开接口: 运行时追加参考文献。"""
        _pl_load_user_references(self.state, paths)

    # ----------------------------------------------------------
    # Context 组装 -- 给 LLM 看的状态摘要
    # ----------------------------------------------------------

    def format_context(self, include_identity: bool = False) -> str:
        """格式化当前状态，注入到 system prompt 的 {workspace_state} 占位符。"""
        current_phase = self.phase_fsm.phase_name
        context = self.assembler.assemble(
            state=self.state,
            paper_id=self._paper_id,
            current_turn=self.state.loop_turns,
            current_phase=current_phase,
        )

        # V5: 追加 SkillX 可用能力提示
        if self.skillx is not None:
            try:
                skill_hints = self.skillx.get_skill_hints(token_budget=1200)
                if skill_hints:
                    context += f"\n\n{skill_hints}"
            except Exception:
                pass  # SkillX hint 失败不影响主流程

        # Phase 2: 追加多模型信息（让 Agent 知道可用模型和切换方式）
        if self._session_model_mgr is not None:
            try:
                from core.identity import format_model_info_for_prompt
                model_info = format_model_info_for_prompt(
                    models_formatted=self._session_model_mgr.list_models_formatted(),
                    current_model=self._session_model_mgr.current_model_id,
                )
                context += f"\n\n{model_info}"
            except Exception:
                pass  # 多模型信息注入失败不影响主流程

        return context

    def _format_context_legacy(self) -> str:
        """原 format_context 实现。保留供回退和对比验证。"""
        parts = []
        s = self.state

        if s.paper_sections:
            section_names = [k for k in s.paper_sections if k != "full"]
            total_chars = sum(len(v) for k, v in s.paper_sections.items() if k != "full")
            parts.append(f"论文已加载 | {len(section_names)} 个 sections | 总计 ~{total_chars} 字符")
            section_list = []
            for name in section_names:
                char_count = len(s.paper_sections[name])
                if char_count < 50:
                    section_list.append(f"{name} (空)")
                else:
                    section_list.append(f"{name} ({char_count}字)")
            parts.append(f"  Sections: {', '.join(section_list)}")
            parts.append("  用 read_section('<name>') 按需读取，长 section 支持 offset 续读")
            if s.sections_read:
                parts.append(f"  已读过 ({len(s.sections_read)}): {', '.join(s.sections_read)}")
                unread = [n for n in section_names if n not in s.sections_read]
                if unread:
                    parts.append(f"  尚未读取: {', '.join(unread)}")
            if s.section_digests:
                parts.append(f"\n  Section 摘要缓存 ({len(s.section_digests)} 条):")
                for sec_name, digest in list(s.section_digests.items())[:10]:
                    parts.append(f"    {sec_name}: {digest}")

        if s.findings:
            parts.append(f"\n你已有的发现 ({len(s.findings)} 条):")
            for i, f in enumerate(s.findings, 1):
                icon = {"high": "R", "medium": "Y", "low": "G"}.get(f["priority"], "?")
                status = {"verified": "v", "needs_verification": "?", "suggestion": ">"}.get(f["status"], "")
                evidence_hint = ""
                if f.get("evidence"):
                    evidence_hint = f" [证据: \"{f['evidence'][:60]}...\"]"
                elif f.get("section"):
                    evidence_hint = f" [来自: {f['section']}, 无证据]"
                parts.append(f"  [{icon}{status}] {f['finding'][:120]}{evidence_hint}")
            no_evidence = sum(1 for f in s.findings if not f.get("evidence"))
            if no_evidence > 0:
                parts.append(f"  {no_evidence} 条发现缺少原文证据")

        if s.reference_papers:
            parts.append(f"\n参考文献工作区 ({len(s.reference_papers)} 篇)")

        if s.edits:
            parts.append(f"\n你已做的修改 ({len(s.edits)} 处): {', '.join(e['section'] for e in s.edits)}")

        memory_context = self.memory.format_memory_context(paper_id=self._paper_id)
        if memory_context:
            parts.append(f"\n{memory_context}")

        cognitive_context = self.cognitive_state.format_for_context()
        if cognitive_context:
            parts.append(f"\n{cognitive_context}")

        refs_summary = self.offload_store.format_refs_summary()
        if refs_summary:
            parts.append(f"\n{refs_summary}")

        parts.append(f"\n轮次: {s.loop_turns}/{s.max_loop_turns} | 对话轮: {s.conversation_turns} | Tokens: ~{s.total_tokens}")

        return "\n".join(parts) if parts else "(刚开始，还没有任何状态)"

    # ----------------------------------------------------------
    # 工具注册
    # ----------------------------------------------------------

    def _init_tool_registry(self) -> None:
        """注册所有工具到 ToolRegistry。"""
        self.tool_registry = ToolRegistry()

        # --- 通用工具 ---
        self.tool_registry.register("update_findings", self._tool_update_findings, phases=None)
        self.tool_registry.register("review_findings", self._tool_review_findings, phases=None)
        self.tool_registry.register("talk_to_user", self._tool_talk_to_user, phases=None)
        self.tool_registry.register("reflect_and_plan", self._tool_reflect_and_plan, phases=None)
        self.tool_registry.register("recall_context", self._tool_recall_context, phases=None)
        self.tool_registry.register("done", self._tool_done, phases=None)
        self.tool_registry.register("mark_complete", self._tool_done, phases=None)
        self.tool_registry.register("request_phase_transition", self._tool_request_phase_transition, phases=None)
        self.tool_registry.register("switch_persona", self._tool_switch_persona, phases=None)
        self.tool_registry.register("switch_model", self._tool_switch_model, phases=None)

        # --- S1: 认知提示 ---
        self.tool_registry.register(
            "generate_cognitive_hints", self._tool_generate_cognitive_hints,
            phases={"initial_scan", "deep_review"},
        )

        # --- 阅读工具 ---
        _reading_phases = {"initial_scan", "deep_review", "editing"}
        self.tool_registry.register("read_section", self._tool_read_section, phases=_reading_phases)

        # --- 搜索/分析工具 ---
        _analysis_phases = {"initial_scan", "deep_review", "synthesis"}
        self.tool_registry.register("search_literature", self._tool_search_literature, phases=_analysis_phases)
        self.tool_registry.register("fetch_paper_detail", self._tool_fetch_paper_detail, phases={"deep_review", "synthesis"})
        self.tool_registry.register("read_reference", self._tool_read_reference, phases={"deep_review", "editing"})
        self.tool_registry.register("detect_ai_signals", self._tool_detect_ai_signals, phases={"deep_review", "editing"})
        self.tool_registry.register("verify_citations", self._tool_verify_citations, phases={"deep_review", "editing"})

        # --- 编辑工具 ---
        self.tool_registry.register("generate_edit_plan", self._tool_generate_edit_plan, phases={"deep_review", "editing"})
        self.tool_registry.register("edit_section", self._tool_edit_section, phases={"editing"})
        self.tool_registry.register("edit_paragraph", self._tool_edit_paragraph, phases={"editing"})
        self.tool_registry.register("reword_sentence", self._tool_reword_sentence, phases={"editing"})
        self.tool_registry.register("insert_content", self._tool_insert_content, phases={"editing"})

        # --- 视角分裂 ---
        self.tool_registry.register("spawn_perspective", self._tool_spawn_perspective, phases={"deep_review", "synthesis"})
        self.tool_registry.register("spawn_parallel_readers", self._tool_spawn_parallel_readers, phases={"deep_review"})

        # --- HD-WM 假说工具 ---
        if self.enable_hdwm and self.hypothesis_module is not None:
            _hdwm_optional_phases = {"deep_review"}
            self.tool_registry.register("generate_hypothesis", self._tool_generate_hypothesis, phases=_hdwm_optional_phases)
            self.tool_registry.register("add_evidence", self._tool_add_evidence, phases=_hdwm_optional_phases)
            self.tool_registry.register("resolve_hypothesis", self._tool_resolve_hypothesis, phases=_hdwm_optional_phases)

        # --- MCP Bridge 工具 (EDIT-4): 硬编码的 Stata 桥接 ---
        _register_mcp_tools(self.tool_registry)

        # --- 通用 MCP 服务加载 (Phase 3) ---
        self._mcp_loader = MCPServiceLoader()
        self._mcp_loader.load_and_register(self.tool_registry)

        # --- 插件管理工具 ---
        _all_phases = {"initial_scan", "deep_review", "editing", "synthesis"}
        self.tool_registry.register("manage_plugins", _th_manage_plugins, phases=_all_phases)

    # ----------------------------------------------------------
    # V4 D1: 操作型 Skill 动态 Tool 注册
    # ----------------------------------------------------------

    def _register_action_skill_tools(self) -> None:
        """从 SkillRegistry 获取 action skills 并将其 tools 注册到 ToolRegistry。

        对每个 action skill 的每个 ToolDef:
            1. 通过 SkillHandlerLoader 加载 handler 函数 (args, state) -> str
            2. 创建闭包包装为 ToolRegistry 期望的 (args) -> str 签名
            3. 注册到 ToolRegistry (phases=None → 所有阶段可用)
            4. 收集 API schema 到 self._action_tool_schemas 供 Agent 侧注入

        降级策略: Loader/Handler 不存在 → 跳过该 tool，不中断启动。
        """
        if not self.skill_registry or not self._skill_handler_loader:
            return

        action_skills = self.skill_registry.get_action_skills()
        if not action_skills:
            return

        registered_count = 0
        for skill in action_skills:
            for tool_def in skill.tools:
                # 名称冲突检查：防止覆盖内置工具
                if self.tool_registry.has_tool(tool_def.name):
                    logger.warning(
                        "[Harness D1] Tool name '%s' (skill='%s') conflicts with existing tool — skipped.",
                        tool_def.name,
                        skill.id,
                    )
                    continue

                # 加载 handler
                handler_fn = self._skill_handler_loader.load(tool_def.handler)
                if handler_fn is None:
                    logger.warning(
                        "[Harness D1] Failed to load handler for tool '%s' (skill='%s', handler='%s') — skipped.",
                        tool_def.name,
                        skill.id,
                        tool_def.handler,
                    )
                    continue

                # 创建闭包：将 (args, state) -> str 包装为 (args) -> str
                # 使用默认参数捕获当前 handler_fn，避免 late-binding 问题
                def _make_wrapper(fn: Any) -> Callable[[dict], str]:
                    def wrapper(args: dict) -> str:
                        try:
                            return fn(args, self.state)
                        except Exception as exc:
                            logger.error(
                                "[Harness] Skill handler '%s' raised: %s",
                                fn.__name__ if hasattr(fn, "__name__") else str(fn),
                                exc,
                                exc_info=True,
                            )
                            return f"[Error] Skill handler failed: {exc}"
                    return wrapper

                wrapped = _make_wrapper(handler_fn)

                # 注册到 ToolRegistry
                # 如果 skill 声明了 applicable_phases，则限定工具在这些阶段可用
                # 注意: Phase enum 的 .value 为小写 (e.g. "synthesis")，
                # 而 registry.json 中可能使用大写 (e.g. "SYNTHESIS")，需归一化
                tool_phases: set[str] | None = None
                if skill.applicable_phases:
                    tool_phases = {p.lower() for p in skill.applicable_phases}

                self.tool_registry.register(
                    name=tool_def.name,
                    handler=wrapped,
                    description=tool_def.description,
                    phases=tool_phases,
                )

                # 收集 API schema
                self._action_tool_schemas.append(tool_def.to_api_schema())
                registered_count += 1

        if registered_count > 0:
            logger.info(
                "[Harness D1] Registered %d action skill tool(s) from %d skill(s).",
                registered_count,
                len(action_skills),
            )

    def get_action_tool_schemas(self) -> list[dict]:
        """返回所有已注册的 action skill tool 的 API schema。

        Agent 侧使用: self.tools = list(base_tools) + harness.get_action_tool_schemas()

        Returns:
            List of dicts, 每个 dict 包含 name, description, input_schema。
        """
        return list(self._action_tool_schemas)

    def get_mcp_tool_schemas(self) -> list[dict]:
        """返回所有已加载 MCP 服务暴露的工具 schema。

        Agent 侧使用: self.tools += harness.get_mcp_tool_schemas()

        Returns:
            List of dicts, 每个 dict 包含 name, description, input_schema。
        """
        if self._mcp_loader is None:
            return []
        return self._mcp_loader.get_tool_schemas()

    # ----------------------------------------------------------
    # 工具执行
    # ----------------------------------------------------------

    def execute_tool(self, name: str, args: dict) -> str:
        """执行 tool call，返回结果字符串。"""
        self.state.tool_call_counts[name] = self.state.tool_call_counts.get(name, 0) + 1
        self.state.tool_call_history.append({"name": name, "input": args})

        if "__parse_error__" in args:
            raw = args.get("__raw__", "")[:300]
            return (
                f"[工具调用失败] 你的参数格式无法解析。"
                f"错误: {args['__parse_error__']}。"
                f"原始内容: {raw}。"
                f"请重新调用 {name}，确保参数是合法的 JSON。"
            )

        result = self.tool_registry.execute(name, args)

        # V3 Phase 0.5: EvidenceChain tracking hook
        self._track_evidence_step(name, args, result)

        # Phase 55: 停滞检测
        stagnation_signal = self._check_stagnation(name)
        if stagnation_signal:
            result += stagnation_signal

        return result

    def _track_evidence_step(self, tool_name: str, args: dict, result: str) -> None:
        """V3 Phase 0.5: EvidenceChain step tracking。

        记录关键工具调用作为 evidence chain 步骤。
        优先使用 EvidenceChainTracker（G3 fix），同时保留 state.evidence_chains dict 备份。
        当 GODEL_EVIDENCE_CHAIN_ENABLED=0 时静默跳过。
        """
        from core.godel_config import GODEL_EVIDENCE_CHAIN_ENABLED
        if not GODEL_EVIDENCE_CHAIN_ENABLED:
            return

        # 只追踪认知相关工具（读、搜索、发现、假说）
        TRACKED_TOOLS = {
            "read_section", "search_literature", "fetch_paper_detail",
            "update_findings", "generate_hypothesis", "add_evidence",
            "resolve_hypothesis", "read_reference",
        }
        if tool_name not in TRACKED_TOOLS:
            return

        target = args.get("section_name", args.get("section", args.get("query", "")))
        observation = str(result)[:120] if result else ""

        # G3 fix: 使用 EvidenceChainTracker 接口（如已初始化）
        if hasattr(self, 'evidence_tracker'):
            # 若当前轮次尚无 active chain，自动创建一条
            chain_id = f"turn_{self.state.loop_turns}"
            if self.evidence_tracker.get_chain(chain_id) is None:
                self.evidence_tracker.start_chain(
                    chain_id,
                    finding_text=f"Turn {self.state.loop_turns} evidence",
                    priority="medium",
                )
            self.evidence_tracker.add_step(
                finding_id=chain_id,
                action=tool_name,
                target=target,
                observation=observation,
                turn=self.state.loop_turns,
            )

        # 同时保留 state.evidence_chains dict（供 compaction 序列化）
        step = {
            "tool": tool_name,
            "turn": self.state.loop_turns,
            "section": target,
        }
        active_chain_id = f"turn_{self.state.loop_turns}"
        if active_chain_id not in self.state.evidence_chains:
            self.state.evidence_chains[active_chain_id] = []
        self.state.evidence_chains[active_chain_id].append(step)

    # ----------------------------------------------------------
    # 工具 thin wrappers -- 委托 tool_handlers
    # ----------------------------------------------------------

    def _tool_read_section(self, args: dict) -> str:
        return _th_read_section(args, self.state, self.offload_store)

    def _tool_search_literature(self, args: dict) -> str:
        if not hasattr(self, '_search_log'):
            self._search_log: list[dict] = []
        return _th_search_literature(args, self.state, self.offload_store, self._search_log)

    def _tool_fetch_paper_detail(self, args: dict) -> str:
        return _th_fetch_paper_detail(args, self.state, self.offload_store)

    def _tool_read_reference(self, args: dict) -> str:
        return _th_read_reference(args, self.state)

    def _tool_update_findings(self, args: dict) -> str:
        return _th_update_findings(args, self.state, self.enable_hdwm, self.hypothesis_module)

    def _tool_review_findings(self, args: dict) -> str:
        return _th_review_findings(args, self.state)

    def _tool_generate_edit_plan(self, args: dict) -> str:
        return _th_generate_edit_plan(args, self.state)

    def _tool_edit_paragraph(self, args: dict) -> str:
        return _th_edit_paragraph(args, self.state, self.checker)

    def _tool_reword_sentence(self, args: dict) -> str:
        return _th_reword_sentence(args, self.state, self.checker)

    def _tool_insert_content(self, args: dict) -> str:
        return _th_insert_content(args, self.state, self.checker)

    def _tool_edit_section(self, args: dict) -> str:
        return _th_edit_section(args, self.state, self.checker)

    def _tool_talk_to_user(self, args: dict) -> str:
        return _th_talk_to_user(args)

    def _tool_spawn_perspective(self, args: dict) -> str:
        return _th_spawn_perspective(args)

    def _tool_spawn_parallel_readers(self, args: dict) -> str:
        return _th_spawn_parallel_readers(args)

    def _tool_generate_hypothesis(self, args: dict) -> str:
        return _th_generate_hypothesis(args, self.state, self.hypothesis_module)

    def _tool_add_evidence(self, args: dict) -> str:
        return _th_add_evidence(args, self.state, self.hypothesis_module)

    def _tool_resolve_hypothesis(self, args: dict) -> str:
        return _th_resolve_hypothesis(args, self.state, self.hypothesis_module)

    def _tool_generate_cognitive_hints(self, args: dict) -> str:
        gate_config_holder = [self.gate_config]
        result = _th_generate_cognitive_hints(
            args, self.state, self.memory, gate_config_holder,
            template_registry=self.template_registry,
        )
        self.gate_config = gate_config_holder[0]
        return result

    def _tool_reflect_and_plan(self, args: dict) -> str:
        if not hasattr(self, '_reflection_log'):
            self._reflection_log: list[dict] = []
        result, new_strategy = _th_reflect_and_plan(
            args, self.state, self.cognitive_state,
            self._strategy_transitions, self._last_strategy,
            getattr(self, '_search_log', []), self.gate_config,
            self._reflection_log,
        )
        self._last_strategy = new_strategy
        return result

    def _check_stagnation(self, current_tool: str) -> str | None:
        signal, new_turn = _th_check_stagnation(
            self.state, self.gate_config,
            getattr(self, '_last_stagnation_signal_turn', 0),
            current_tool,
        )
        if signal:
            self._last_stagnation_signal_turn = new_turn
        return signal

    def _tool_detect_ai_signals(self, args: dict) -> str:
        return _th_detect_ai_signals(args, self.state)

    def _tool_verify_citations(self, args: dict) -> str:
        return _th_verify_citations(args)

    def _tool_recall_context(self, args: dict) -> str:
        return _th_recall_context(args, self.offload_store)

    def _tool_request_phase_transition(self, args: dict) -> str:
        result = _th_request_phase_transition(args, self.state, self.phase_fsm, self.assembler)
        # V5: Phase 转换成功后通知 SkillX 切换 ToolGroup
        if self.skillx is not None and "转换成功" in result:
            try:
                new_phase = self.phase_fsm.phase_name
                self.skillx.on_phase_transition(new_phase)
            except Exception as exc:
                logger.warning("[Harness] SkillX phase transition hook failed: %s", exc)
        return result

    def _tool_apply_skill(self, args: dict) -> str:
        """Agent 调用 SkillX Skill 执行分析。"""
        if self.skillx is None:
            return "[SkillX] 未启用。请检查 SCHOLAR_GODEL_SKILLX 环境变量。"
        from core.skillx_integration import tool_apply_skill
        return tool_apply_skill(args, self.skillx, self.state)

    def _tool_done(self, args: dict) -> str:
        return _th_done(args, self.state, self.checker, self.hypothesis_module, self._check_completion_gate)

    def _tool_switch_persona(self, args: dict) -> str:
        return _th_switch_persona(args, self.state)

    def _tool_switch_model(self, args: dict) -> str:
        return _th_switch_model(args, self.state)

    # ----------------------------------------------------------
    # 子视角管理（与状态紧密关联，保留在 Harness）
    # ----------------------------------------------------------

    def ingest_perspective_findings(self, findings: list[dict], lens: str, summary: str) -> str:
        """将子视角的发现注入主 Agent 的 state。"""
        injected_count = 0
        for f in findings:
            f["perspective"] = lens
            self.state.findings.append(f)
            injected_count += 1

        lines = [f"独立视角 [{lens}] 审视完成。"]
        if injected_count > 0:
            lines.append(f"发现 {injected_count} 条问题（已加入你的工作记忆，标记为来自此视角）:")
            for i, f in enumerate(findings, 1):
                icon = {"high": "R", "medium": "Y", "low": "G"}.get(f.get("priority", ""), "?")
                lines.append(f"  {icon} {f.get('finding', '')[:150]}")
        else:
            lines.append("未发现显著问题。")
        if summary:
            lines.append(f"视角总结: {summary}")
        return "\n".join(lines)

    def create_sub_harness(self, focus_sections: list[str]) -> "Harness":
        """创建轻量子 Harness，用于子视角独立循环。

        子 harness 继承父 harness 的当前认知阶段，确保子 agent 能看到
        与父 agent 相同阶段的工具（如 deep_review 阶段的搜索/验证工具）。

        注意：子 agent 不设独立的 token budget 限制。子视角的终止由
        max_loop_turns 硬约束保证（12 轮足够覆盖复杂的深读+验证流程）。
        子消耗事后回流父级，用于父级后续的预算决策。
        """
        sub = Harness(max_loop_turns=12)
        sub._paper_loaded = True

        # 继承父 harness 的认知阶段，避免子 agent 被困在 INITIAL_SCAN
        # （子 agent 通常在 deep_review 阶段被 spawn，需要访问该阶段的工具）
        parent_phase = self.phase_fsm.current_phase
        if parent_phase != sub.phase_fsm.current_phase:
            sub.phase_fsm._state.current = parent_phase

        for key, content in self.state.paper_sections.items():
            if key == "full":
                continue
            for focus in focus_sections:
                if focus.lower() in key.lower() or key.lower() in focus.lower():
                    sub.state.paper_sections[key] = content
                    break

        if not sub.state.paper_sections:
            sub.state.paper_sections = dict(self.state.paper_sections)

        return sub

    # ----------------------------------------------------------
    # 边界守护 (委托给 boundary_guard.py)
    # ----------------------------------------------------------

    def check_doom_loop(self) -> str | None:
        return _bg_check_doom_loop(self.state)

    def check_soft_turn_limit(self) -> str | None:
        return _bg_check_soft_turn_limit(
            self.state, self.gate_config,
            self.state.tool_call_history,
            getattr(self, '_search_log', []),
        )

    def check_cognitive_output(self) -> str | None:
        return _bg_check_cognitive_output(self.state)

    def track_cognitive_output(self, tool_name: str):
        _bg_track_cognitive_output(self.state, tool_name)

    def increment_read_turn(self):
        _bg_increment_read_turn(self.state)

    def check_reflection_needed(self) -> str | None:
        return _bg_check_reflection_needed(
            self.state,
            getattr(self, '_reflection_log', []),
            getattr(self, '_search_log', []),
        )

    def check_auto_spawn_needed(self) -> str | None:
        return _bg_check_auto_spawn_needed(
            self.state,
            self.phase_fsm.phase_name,
            self.state.tool_call_history,
        )

    def is_budget_exceeded(self) -> bool:
        """检查 token budget 是否已耗尽（硬截断判定）。

        当 SessionModelManager 可用时，优先使用其多模型维度的 budget 追踪；
        否则 fallback 到原有的单一 budget_policy 逻辑。
        """
        if self._session_model_mgr is not None:
            return self._session_model_mgr.is_budget_exceeded()
        return self.budget_policy.is_exceeded(self.state.total_tokens)

    def check_token_budget(self) -> str | None:
        """检查 context window 占用（仅保留 context_ratio 提醒，budget 硬截断由 loop 负责）。"""
        result, updated = _bg_check_token_budget(
            self.state, getattr(self, '_cost_warned', False)
        )
        if updated:
            self._cost_warned = True
        return result

    def _check_completion_gate(self) -> str | None:
        if not hasattr(self, '_completion_nudges_fired'):
            self._completion_nudges_fired: set[str] = set()

        # MCL 降级: 当 MCL 活跃时，spawn_gate 由 MCL 在 loop 层面处理，
        # 这里预先标记 spawn_gate 为已触发，避免旧逻辑重复拦截。
        if self.mcl is not None and "spawn_gate" not in self._completion_nudges_fired:
            self._completion_nudges_fired.add("spawn_gate")

        result, self._completion_nudges_fired = _bg_check_completion_gate(
            self.state, self.gate_config,
            self.hypothesis_module, self.finding_quality_gate,
            self._completion_nudges_fired,
        )
        return result

    # ----------------------------------------------------------
    # 生命周期
    # ----------------------------------------------------------

    def increment_turn(self, usage: dict | None = None):
        """每轮 loop 结束时调用。"""
        self.state.loop_turns += 1
        if usage:
            self.state.total_tokens += usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)

        if self.session_memory.should_update(self.state):
            recent_activity = ""
            if self.state.tool_call_history:
                recent_tools = [
                    entry.get("name", "?")
                    for entry in self.state.tool_call_history[-3:]
                ]
                recent_activity = " -> ".join(recent_tools)
            new_findings = self.state.findings[self.session_memory._last_findings_count:]
            self.session_memory.update_sync(
                self.state,
                recent_activity=recent_activity,
                new_findings=new_findings,
            )

    def new_conversation_turn(self):
        """用户发了新消息。重置单轮 loop 计数。"""
        self.state.conversation_turns += 1
        self.state.loop_turns = 0

    # ----------------------------------------------------------
    # 会话结束 -- 委托 session_finalizer
    # ----------------------------------------------------------

    def shutdown(self) -> None:
        """释放所有外部资源（MCP 子进程等）。

        可在 Agent.run() 结束后调用，也会被 end_session 自动调用。
        多次调用是安全的（幂等）。
        """
        if hasattr(self, '_mcp_loader') and self._mcp_loader is not None:
            self._mcp_loader.shutdown_all()
            self._mcp_loader = None

    def end_session(self, paper_title: str = "", user_messages: list[str] | None = None):
        """会话结束时调用: 将认知产出沉淀到跨会话记忆（同步版本，无反思）。"""
        self.shutdown()  # 先释放外部资源
        _sf_end_session(
            state=self.state,
            memory=self.memory,
            paper_id=self._paper_id,
            strategy_transitions=self._strategy_transitions if self._strategy_transitions else None,
            paper_title=paper_title,
            user_messages=user_messages,
        )

    async def end_session_with_reflection(
        self,
        llm_call_fn=None,
        paper_title: str = "",
        user_messages: list[str] | None = None,
    ) -> dict:
        """
        带 Agent 自省的 session 结束（async 版本）。

        Agent 会做一次 LLM reflection，自己决定本次学到了什么。
        经验存为 ProceduralPattern（evidence=1），后续累积验证后升级为习惯。

        Args:
            llm_call_fn: async (system, user, max_tokens) -> str
            paper_title: 论文标题
            user_messages: 用户消息列表

        Returns:
            反思统计 dict
        """
        # 先释放外部资源（MCP 子进程等）
        self.shutdown()

        # P2-fix12: Record evolution stats before session finalization
        try:
            evo_stats = self.evolution_engine.record_session_stats()
            evo_stats["paper_id"] = self._paper_id or ""
            self.memory.state.evolution_stats.append(evo_stats)
            # Sliding window: keep last 50 entries
            if len(self.memory.state.evolution_stats) > 50:
                self.memory.state.evolution_stats = self.memory.state.evolution_stats[-50:]
        except Exception:
            pass  # Non-fatal: don't block session finalization

        return await _sf_end_session_async(
            state=self.state,
            memory=self.memory,
            paper_id=self._paper_id,
            strategy_transitions=self._strategy_transitions if self._strategy_transitions else None,
            llm_call_fn=llm_call_fn,
            paper_title=paper_title,
            user_messages=user_messages,
        )

    # ----------------------------------------------------------
    # Context Window 管理
    # ----------------------------------------------------------

    def compress_messages(self, messages: list[dict], keep_recent: int = 6) -> list[dict]:
        """压缩 messages 列表以控制 context window 膨胀。"""
        from core.message_compressor import compress_messages as _mc_compress
        return _mc_compress(
            messages, self.state, self.compaction_engine,
            self.session_memory, self.hypothesis_module,
            keep_recent=keep_recent,
        )


# ============================================================
# Section 分类 -- 为 format_context 提供优先级信号
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
    """
    if _SKIP_PATTERNS.search(name):
        return "skip"
    if _CORE_PATTERNS.search(name):
        return "core"
    return "support"
