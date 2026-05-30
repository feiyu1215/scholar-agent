"""
core/v2/state.py - WorkspaceState: Agent 的外部状态

从 harness.py 提取，作为未来 StateManager 的基础。

设计原则:
    - LLM 是无状态 CPU，所有状态由外部维护
    - 状态变更只通过工具执行的副作用发生
    - 这个类只是数据容器，不包含业务逻辑
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from typing import TYPE_CHECKING

from core.post_edit_verify import VoiceFingerprint
from core.paper_index import PaperStructureIndex
from core.paper_type_hints import CognitiveHints
from core.cognition_graph import ReviewCognitionGraph
from core.review_checklist import ReviewChecklist

if TYPE_CHECKING:
    from core.paper_cognition_graph import PaperCognitionGraph


# ==============================================================
# EDIT-1: 修改计划数据结构
# ==============================================================

@dataclass
class EditStep:
    """一步修改操作。"""

    target_section: str                 # 目标 section 名称
    action: str                         # "reword" | "restructure" | "add_content" | "remove" | "verify_data"
    description: str                    # 具体做什么（人类可读）
    requires: list[str] = field(default_factory=list)   # 前置资源需求（如"需先读 Section X"）
    priority: str = "should"            # "must" | "should" | "could"
    status: str = "pending"             # "pending" | "in_progress" | "done" | "skipped"
    finding_ids: list[int] = field(default_factory=list)  # 对应的 finding 索引


@dataclass
class EditPlan:
    """结构化修改计划。Agent 根据 findings 产出，后续 EDIT-3/5 工具消费。"""

    steps: list[EditStep] = field(default_factory=list)
    source_finding_ids: list[int] = field(default_factory=list)  # 驱动此计划的 finding 索引
    estimated_scope: str = "局部措辞"     # "局部措辞" | "段落重组" | "章节重写"
    rationale: str = ""                  # Agent 对整体修改策略的解释


@dataclass
class WorkspaceState:
    """Agent 的完整工作状态。Harness 拥有并维护它，LLM 永远不直接访问它。"""

    # 论文内容
    paper_sections: dict[str, str] = field(default_factory=dict)
    paper_path: str | None = None

    # Phase 13/B1: 论文结构预索引
    paper_structure_index: PaperStructureIndex | None = None

    # Phase S1: Agent 自主生成的认知提示
    cognitive_hints: CognitiveHints | None = None

    # Agent 的工作记忆
    findings: list[dict] = field(default_factory=list)
    edits: list[dict] = field(default_factory=list)
    sections_read: list[str] = field(default_factory=list)
    section_digests: dict[str, str] = field(default_factory=dict)

    # EDIT-1: 结构化修改计划（generate_edit_plan 产出）
    edit_plan: EditPlan | None = None

    # EDIT-5: 编辑迭代修正追踪（section_key → 连续 FAIL 次数）
    edit_retry_counts: dict[str, int] = field(default_factory=dict)

    # DEAI-1: 去 AI 味迭代追踪
    deai_check_count: int = 0                    # 已执行的 de-AI 检查轮次
    deai_last_result: dict | None = None         # 上次检查的概要（verdict, signal_count, sections_checked）

    # Phase 57+58: 参考文献工作区
    reference_papers: dict[str, dict] = field(default_factory=dict)

    # Phase 58: 用户提供的参考文献原文
    user_reference_docs: dict[str, dict] = field(default_factory=dict)

    # 对话历史
    conversation_turns: int = 0

    # 写作风格指纹 (Phase 20)
    voice_profile: VoiceFingerprint | None = None

    # 认知行为追踪 (Phase 17)
    consecutive_read_turns: int = 0
    last_findings_count: int = 0

    # 资源追踪
    loop_turns: int = 0
    total_tokens: int = 0
    last_prompt_tokens: int = 0
    tool_call_counts: dict[str, int] = field(default_factory=dict)
    tool_call_history: list[dict] = field(default_factory=list)

    # K1: 审稿认知图谱（mark_complete 成功后构建）
    cognition_graph: ReviewCognitionGraph | None = None

    # V3 Phase 0.5: Paper Cognition Graph（session-scoped，论文加载后构建）
    paper_cognition_graph: "PaperCognitionGraph | None" = None

    # S3: 审稿维度覆盖追踪（从 PCG DomainTemplate 初始化）
    review_checklist: ReviewChecklist = field(default_factory=ReviewChecklist)

    # V3 Phase 0.5: EvidenceChain 追踪数据（finding_id → chain steps）
    evidence_chains: dict[str, list[dict]] = field(default_factory=dict)

    # V4 C2: 模板推荐加载的 Skill ID 列表（来自 TemplateRegistry 匹配）
    recommended_skills: list[str] = field(default_factory=list)

    # V3 Phase 1: IntraSession contrast plan (set by EvolutionEngine.initialize)
    contrast_plan: dict | None = None

    # V3 Phase 1: Section-level metrics collected during session
    # Each entry: {"section_name": str, "turns_spent": int, "findings_produced": int, ...}
    section_metrics: list[dict] = field(default_factory=list)

    # W1: 人格切换追踪
    current_persona: str = "scholar"
    persona_switch_count: int = 0

    # P2-fix: 用户纠正信号（跨会话学习的负反馈来源）
    # 每条: {"message": str, "turn": int, "related_finding_idx": int | None}
    user_corrections: list[dict] = field(default_factory=list)

    # Deep Verify hints: heuristic 规则引擎的检测结果，待 consolidation LLM 审核
    # 不直接加入 findings，遵循"提醒 + LLM 决策"模式
    deep_verify_hints: list[dict] = field(default_factory=list)

    # Auto-Spawn 调度状态（boundary_guard 使用）
    _role_spawn_nudge_fired: bool = False
    _verify_spawn_nudge_fired: bool = False
    _fallback_spawn_nudge_fired: bool = False

    # 模型切换优化/方案四: 认知催促已触发次数（防止无限催促）
    cognitive_nudge_count: int = 0

    # 配置
    max_loop_turns: int = 50
    token_budget: int = 200_000
    context_window: int = 128_000
