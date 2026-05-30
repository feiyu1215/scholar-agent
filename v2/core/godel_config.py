"""
core/godel_config.py — Gödel Agent V3 Kill Switch 配置

设计原则:
    - 所有 V3 新功能通过环境变量控制，默认 "1"（开启）
    - 设为 "0" 时对应功能静默降级为 V2/Phase 0 行为
    - 在调用点用 `if FLAG:` 守卫，无 import-time 副作用
    - 统一在此文件管理，避免散落各处

使用方式:
    from core.godel_config import GODEL_PCG_ENABLED, GODEL_BUDGET_MANAGER_ENABLED
    if GODEL_PCG_ENABLED:
        pcg = PaperCognitionGraph.from_structure_index(index)

环境变量命名规则:
    SCHOLAR_GODEL_<MODULE> = "1" | "0"

宪法层约束（Layer 0，不可被自修改）:
    - MAX_META_DEPTH = 2
    - SIGNAL_DISPATCHER_MAX_PER_TURN = 2
    - INTRA_CONTRAST_MIN_SECTIONS = 15
    - EVIDENCE_CHAIN_MIN_FOR_MODIFY = 3
    - ZONE_A_MIN_TOKENS = 6000
"""

from __future__ import annotations

import os
import logging

logger = logging.getLogger(__name__)


# ==============================================================
# Helper
# ==============================================================

def _env_flag(name: str, default: str = "1") -> bool:
    """读取环境变量作为 bool flag。'1'/'true'/'yes' → True，其他 → False。"""
    val = os.environ.get(name, default).strip().lower()
    return val in ("1", "true", "yes")


# ==============================================================
# Phase 0.5: Paper Cognition Infrastructure
# ==============================================================

GODEL_PCG_ENABLED: bool = _env_flag("SCHOLAR_GODEL_PCG")
"""PCG 构建 + Zone A 导航。OFF 时回退到 PaperStructureIndex.format_for_context()。"""

GODEL_BUDGET_MANAGER_ENABLED: bool = _env_flag("SCHOLAR_GODEL_BUDGET")
"""三区 Token Budget Manager。OFF 时使用 V2 被动压缩策略。"""

GODEL_SIGNAL_DISPATCHER_ENABLED: bool = _env_flag("SCHOLAR_GODEL_DISPATCHER")
"""统一信号调度器。OFF 时保留 loop.py stacked checks 行为。"""

GODEL_EVIDENCE_CHAIN_ENABLED: bool = _env_flag("SCHOLAR_GODEL_EVIDENCE_CHAIN")
"""EvidenceChain 全链路追踪。OFF 时不记录推理链。"""


# ==============================================================
# Phase 1: Hierarchical Experience + IntraSession Contrast
# ==============================================================

GODEL_SECTION_EXPERIENCE_ENABLED: bool = _env_flag("SCHOLAR_GODEL_SECTION_EXP")
"""Section 级经验记录（L0 层）。OFF 时不记录 section-level 经验。"""

GODEL_INTRA_CONTRAST_ENABLED: bool = _env_flag("SCHOLAR_GODEL_INTRA_CONTRAST")
"""IntraSession Contrast A/B 对比。OFF 时不做 session 内对比验证。"""


# ==============================================================
# Phase 2: Tri-frequency MetaReflector
# ==============================================================

GODEL_FAST_REFLECT_ENABLED: bool = _env_flag("SCHOLAR_GODEL_FAST_REFLECT")
"""Fast Reflector（每 3 sessions, zero LLM）。OFF 时跳过快速趋势检测。"""

GODEL_DEEP_REFLECT_ENABLED: bool = _env_flag("SCHOLAR_GODEL_DEEP_REFLECT")
"""Deep Reflector（每 10 sessions, full LLM）。OFF 时跳过深度决策。"""

GODEL_EMERGENCY_REFLECT_ENABLED: bool = _env_flag("SCHOLAR_GODEL_EMERGENCY")
"""Emergency Reflector（realtime, zero LLM）。OFF 时跳过紧急降级。"""


# ==============================================================
# V4: Skill Loading
# ==============================================================

GODEL_SKILL_LOADING_ENABLED: bool = _env_flag("SCHOLAR_GODEL_SKILL_LOADING")
"""Skill 动态加载（知识型 + 操作型）。OFF 时不注入任何 v2/skills/ 内容，不注册动态 tools。"""


# ==============================================================
# V5: SkillX 三层技能体系 (Phase 3)
# ==============================================================

GODEL_SKILLX_ENABLED: bool = _env_flag("SCHOLAR_GODEL_SKILLX")
"""SkillX 三层技能体系（Planning/Functional/Atomic）。OFF 时退化为旧 SkillRegistry 行为。"""

GODEL_DEEP_VERIFY_ENABLED: bool = _env_flag("SCHOLAR_GODEL_DEEP_VERIFY")
"""Layer 2-5 深度验证自动触发。OFF 时不自动执行 math_audit/table_consistency Skills。
解决 G005 类问题：LLM 不会主动调用 apply_skill，需要系统在 loop 结束后自动触发。"""


# ==============================================================
# Phase 1 Complete: Loop Guard (循环模式检测 + 恢复)
# ==============================================================

GODEL_LOOP_GUARD_ENABLED: bool = _env_flag("SCHOLAR_GODEL_LOOP_GUARD")
"""循环模式检测 + phase-aware 恢复干预。默认 ON。
OFF 时 detect() 始终返回 None（不检测），但 record_call 仍正常记录调用历史。
注：loop_guard.py 自身也直接读取 SCHOLAR_GODEL_LOOP_GUARD 环境变量，此处仅为统一注册。"""


# ==============================================================
# Phase 6 Complete: Reflection Complete Layer
# ==============================================================

GODEL_REFLECTION_ADAPTIVE_DEPTH_ENABLED: bool = _env_flag("SCHOLAR_GODEL_REFLECTION_ADAPTIVE_DEPTH")
"""反思深度自适应。OFF 时始终使用 STANDARD 深度。"""

GODEL_REFLECTION_COMPARATIVE_ENABLED: bool = _env_flag("SCHOLAR_GODEL_REFLECTION_COMPARATIVE")
"""对比反思（与历史最佳审稿对比）。OFF 时不做对比分析。"""

GODEL_REFLECTION_QUALITY_VERIFY_ENABLED: bool = _env_flag("SCHOLAR_GODEL_REFLECTION_QUALITY_VERIFY")
"""反思质量验证（防止虚假反思）。OFF 时跳过验证，信任所有反思结论。"""

GODEL_REFLECTION_SKILL_SYNTHESIS_ENABLED: bool = _env_flag("SCHOLAR_GODEL_REFLECTION_SKILL_SYNTHESIS")
"""反思触发 Skill 合成。OFF 时不检测反复差距模式，不产出合成信号。"""


# ==============================================================
# V6: Meta-Harness 自动评估 (Phase 5)
# ==============================================================

GODEL_META_HARNESS_ENABLED: bool = _env_flag("SCHOLAR_GODEL_META_HARNESS")
"""Meta-Harness 评估框架（过程指标+瓶颈分析）。OFF 时退化为基础 P/R/F1 评估。"""


# ==============================================================
# Backward Compatibility
# ==============================================================

GODEL_V2_CONTRAST_ENABLED: bool = _env_flag("SCHOLAR_GODEL_V2_CONTRAST", default="0")
"""V2 12% 随机对比（默认 OFF）。仅在需要 V2 行为时手动开启。"""


# ==============================================================
# 宪法层常量（Layer 0 — 绝对不可被自修改）
# ==============================================================

MAX_META_DEPTH: int = 2
"""递归自改进最大深度。Level 0=执行, Level 1=反思, Level 2=评估反思。禁止 Level 3。"""

SIGNAL_DISPATCHER_MAX_PER_TURN: int = 2
"""SignalDispatcher 每轮最多注入的 system message 数量。doom_loop_guard 独立于此限制。"""

INTRA_CONTRAST_MIN_SECTIONS: int = 15
"""IntraSession Contrast 要求的最低 section 数量。短论文不做对比。"""

EVIDENCE_CHAIN_MIN_FOR_MODIFY: int = 3
"""修改 Layer 1 配置所需的最低 evidence 累积数量。"""

ZONE_A_MIN_TOKENS: int = 6000
"""Zone A（常驻区）的最低 token 预算。不可被动态调整压缩到此线以下。"""

# 方案三: 子视角收尾窗口
SUB_PERSPECTIVE_DEADLINE_WINDOW: int = 2
"""子视角收尾窗口：最后 N 轮进入收尾模式。
联动：deadline_turn = max_loop_turns - SUB_PERSPECTIVE_DEADLINE_WINDOW
当 max_loop_turns=12(GPT) → deadline=10; =20(Claude) → deadline=18"""

# 方案四: 子视角最大轮次安全上限（用户可调）
SUB_PERSPECTIVE_MAX_TURNS_CAP: int = 40
"""子视角单次循环的绝对上限（安全阀）。
profile 中的 sub_perspective_max_turns 不得超过此值。
推荐范围 30-50，视论文复杂度和 token 预算权衡。
- 短论文/低预算: 建议 profile 设 12-20，cap 保持 40 即可
- 长论文/高预算: 可将此值调至 50，配合 profile 设 25-40
用户可直接修改此值调整全局天花板。如需更细粒度控制，
编辑 config/model_profiles.json 中各模型的 sub_perspective_max_turns。"""

ZONE_A_DEFAULT_TOKENS: int = 8000
"""Zone A 默认 token 预算。"""

ZONE_B_MAX_TOKENS: int = 40000
"""Zone B（动态加载区）的最大 token 预算。"""

PCG_FORMAT_MAX_TOKENS: int = 1500
"""PCG format_for_zone_a() 的输出 token 上限。"""

CONSECUTIVE_DECLINE_ROLLBACK: int = 2
"""连续 N 次 quality_score 下降触发回滚。"""

COLD_START_SESSION_THRESHOLD: int = 10
"""冷启动期 session 数。Phase 2-3 逻辑在此之前全部 no-op。"""

SECTION_EXPERIENCE_WINDOW: int = 500
"""L0 section_experiences FIFO 窗口大小。超限时淘汰最早的记录。"""

SIGNAL_DEDUP_WINDOW: int = 3
"""SignalDispatcher 同源去重窗口（轮数）。"""

TOTAL_CONTEXT_WINDOW: int = 128_000
"""模型 context window 总大小（tokens）。TokenBudgetManager 和 CompactionEngine 共享此值。

当切换 LLM（如 32K / 200K）时只需修改此处。
"""

SKILL_ZONE_BUDGET: int = 4500
"""动态加载 skills 的 token 预算上限。从 Zone A 内部分配，不侵占 Zone B/C。

设为 4500 以覆盖核心 methodology_checklist(~4400) 的独立加载，
或多个中小 skill 的组合（如 review_criteria + econ_writing + chinese_academic_standards = 1950）。
超大 skill 如 deai_rules(~8700) 仍需专门场景/手动加载。
"""


# ==============================================================
# Phase 4: Skill Synthesis (SkillTTA)
# ==============================================================

GODEL_SKILL_SYNTHESIS_ENABLED: bool = _env_flag("SCHOLAR_GODEL_SKILL_SYNTHESIS")
"""Phase 4 运行时 Skill 合成。OFF 时:
- SkillSynthesisOrchestrator.receive_synthesis_signal() 返回 False
- on_skill_failed() 仍记录失败到 FailureStore，但不触发合成
- SynthesizedSkill.can_apply() 返回 0.0
- SynthesizedSkill.execute() 返回空结果

包含：失败分类、根因分析、模板驱动合成、沙箱验证、生命周期管理。"""


# ==============================================================
# Phase 9A: Table Processing & Numerical Validation
# ==============================================================

GODEL_TABLE_PROCESSING_ENABLED: bool = _env_flag("SCHOLAR_GODEL_TABLE_PROCESSING")
"""Phase 9A 表格处理与数值验证。OFF 时 TableExtractionSkill/TableConsistencySkill 的
can_apply() 返回 0，execute() 立即返回空结果。

包含：文本表格提取、PDF 表格提取、经济学语义解析、8 规则一致性验证、文本交叉验证。"""


# ==============================================================
# Phase 9B: Figure Semantic Understanding & Cross-Modal Validation
# ==============================================================

GODEL_FIGURE_SEMANTIC_ENABLED: bool = _env_flag("SCHOLAR_GODEL_FIGURE_SEMANTIC")
"""Phase 9B 图表语义理解与跨模态验证。OFF 时 FigureSemanticSkill/FigureConsistencySkill 的
can_apply() 返回 0，execute() 立即返回空结果。

包含：图表提取与分类（14种经济学图表类型）、经济学特化分析（event study/DID/RD/coefficient plot）、
图文交叉验证（量级/显著性/趋势一致性）、覆盖度分析（孤立图表/幽灵引用检测）。"""


# ==============================================================
# Phase 8: Dual-Loop Architecture (Hermes)
# ==============================================================

GODEL_DUAL_LOOP_ENABLED: bool = _env_flag("SCHOLAR_GODEL_DUAL_LOOP")
"""Phase 8 双环架构编排器。OFF 时 DualLoopOrchestrator 所有方法变为 no-op:
- plan_review() 返回空 ReviewPlan
- tick() 返回空 advisory 列表
- on_phase_change()/on_finding() 无操作
- conclude() 不记录学习数据

包含：OuterLoop 观察/建议层、多维 ResourceBudget、ReviewPlan + 5 种策略模板、
PaperProfile 论文特征分析、DualLoopSignal 双环信号系统、PlanAdapter 动态重规划、
StrategyLearner 策略学习（Complete 层）。"""


# ==============================================================
# Phase 7: Adversarial Self-Training
# ==============================================================

GODEL_ADVERSARIAL_TRAINING_ENABLED: bool = _env_flag("SCHOLAR_GODEL_ADVERSARIAL_TRAINING")
"""Phase 7 对抗自训练框架总开关。OFF 时:
- RedTeam.generate_challenge() 返回空挑战（无对抗攻击）
- BlueTeam.respond() 直接通过（不做防御训练）
- ArenaOrchestrator.run_match() 返回 None（不执行对抗赛）
- TrainingLoop 中对抗 session 被跳过
- 已有 ELO 数据保留不变，可以安全重新开启

包含：ELO 动态评分、Red/Blue 6+6 策略库、Season 赛季管理、
平衡控制器、对抗挑战库、弱点追踪与课程设计。"""

GODEL_ADVERSARIAL_RED_TEAM_ENABLED: bool = _env_flag("SCHOLAR_GODEL_ADVERSARIAL_RED")
"""Red Team 独立开关。OFF 时 RedTeam 不产出攻击挑战，
但 Blue Team 仍可对外部输入进行防御训练。
可用于调试场景：仅观察 Blue Team 对固定 benchmark 的表现。"""

GODEL_ADVERSARIAL_BLUE_TEAM_ENABLED: bool = _env_flag("SCHOLAR_GODEL_ADVERSARIAL_BLUE")
"""Blue Team 独立开关。OFF 时 BlueTeam 不做主动防御响应，
但 Red Team 仍可生成挑战用于分析审稿弱点。
可用于场景：仅用 Red Team 做弱点发现而不触发自动修复。"""

GODEL_ADVERSARIAL_ELO_ENABLED: bool = _env_flag("SCHOLAR_GODEL_ADVERSARIAL_ELO")
"""ELO 评分系统开关。OFF 时不更新任何 ELO 评分，
对抗赛仍可运行但不记录胜负到 rating 系统。
用于热身赛或 dry-run 场景。"""

GODEL_ADVERSARIAL_SEASON_ENABLED: bool = _env_flag("SCHOLAR_GODEL_ADVERSARIAL_SEASON")
"""Season 赛季管理开关。OFF 时不进行赛季轮转，
所有对抗赛在单一无限赛季内运行。
适合初始阶段数据积累不足时使用。"""


# ==============================================================
# V5: Streaming Output (方案 B — 回调注入)
# ==============================================================

GODEL_STREAMING_ENABLED: bool = _env_flag("SCHOLAR_GODEL_STREAMING", default="0")
"""流式输出开关。默认 OFF (方案 B 条件做)。

开启后，当 cognitive_loop 收到 on_stream 回调时，LLM 调用切换为
chat_with_tools_stream，逐 chunk 推送 StreamEvent。

关闭时即使传入 on_stream 也不会走流式路径，保持完全兼容。
"""


# ==============================================================
# Sub-Reader 智能路由 (MCL Difficulty Assessment)
# ==============================================================

GODEL_SUB_READER_ROUTING_ENABLED: bool = _env_flag("SCHOLAR_GODEL_SUB_READER_ROUTING")
"""子视角模型智能路由（MCL difficulty assessment）。
ON 时：spawn 前 MCL 评估每个子视角的任务难度，选择对应模型层级。
OFF 时：所有子视角继续使用与主 Agent 相同的模型。"""


# ==============================================================
# 认知习惯渐进加载
# ==============================================================

GODEL_HABIT_PROGRESSIVE_ENABLED: bool = _env_flag("SCHOLAR_GODEL_HABIT_PROGRESSIVE")
"""认知习惯渐进加载（完整→摘要→名称）。
ON 时：阶段内习惯注入随轮次递减（前2轮完整，3-5轮摘要，6+轮仅名称）。
OFF 时：保持当前的 PHASE 缓存全量注入行为。"""


# ==============================================================
# 共享 Utility: Capacity 计算
# ==============================================================

def compute_capacity_pct(current_context_tokens: int, total: int = 0) -> float:
    """计算 context window 已用百分比的单一数据源 (Single Source of Truth)。

    TokenBudgetManager.compute_used_pct() 和 CompactionEngine.get_capacity_pct()
    都应委托给此函数，确保一致性。

    Args:
        current_context_tokens: 当前已使用的 context token 数
        total: context window 总大小。
              - > 0: 使用传入值
              - <= 0: 安全返回 0.0（表示"未配置"，避免误触发压缩）

    Returns:
        已用百分比，clamp 在 0.0~1.0 范围内
    """
    if total <= 0:
        return 0.0
    pct = current_context_tokens / total
    return max(0.0, min(1.0, pct))


# ==============================================================
# 启动时日志
# ==============================================================

def log_config_status() -> None:
    """在 Harness 初始化时调用，输出当前 Kill Switch 状态。"""
    flags = {
        "PCG": GODEL_PCG_ENABLED,
        "BudgetManager": GODEL_BUDGET_MANAGER_ENABLED,
        "SignalDispatcher": GODEL_SIGNAL_DISPATCHER_ENABLED,
        "EvidenceChain": GODEL_EVIDENCE_CHAIN_ENABLED,
        "SectionExperience": GODEL_SECTION_EXPERIENCE_ENABLED,
        "IntraContrast": GODEL_INTRA_CONTRAST_ENABLED,
        "FastReflect": GODEL_FAST_REFLECT_ENABLED,
        "DeepReflect": GODEL_DEEP_REFLECT_ENABLED,
        "Emergency": GODEL_EMERGENCY_REFLECT_ENABLED,
        "SkillLoading": GODEL_SKILL_LOADING_ENABLED,
        "SkillX": GODEL_SKILLX_ENABLED,
        "DeepVerify": GODEL_DEEP_VERIFY_ENABLED,
        "LoopGuard": GODEL_LOOP_GUARD_ENABLED,
        "ReflectionAdaptiveDepth": GODEL_REFLECTION_ADAPTIVE_DEPTH_ENABLED,
        "ReflectionComparative": GODEL_REFLECTION_COMPARATIVE_ENABLED,
        "ReflectionQualityVerify": GODEL_REFLECTION_QUALITY_VERIFY_ENABLED,
        "ReflectionSkillSynthesis": GODEL_REFLECTION_SKILL_SYNTHESIS_ENABLED,
        "MetaHarness": GODEL_META_HARNESS_ENABLED,
        "SkillSynthesis": GODEL_SKILL_SYNTHESIS_ENABLED,
        "TableProcessing": GODEL_TABLE_PROCESSING_ENABLED,
        "FigureSemantic": GODEL_FIGURE_SEMANTIC_ENABLED,
        "DualLoop": GODEL_DUAL_LOOP_ENABLED,
        "AdversarialTraining": GODEL_ADVERSARIAL_TRAINING_ENABLED,
        "AdversarialRedTeam": GODEL_ADVERSARIAL_RED_TEAM_ENABLED,
        "AdversarialBlueTeam": GODEL_ADVERSARIAL_BLUE_TEAM_ENABLED,
        "AdversarialELO": GODEL_ADVERSARIAL_ELO_ENABLED,
        "AdversarialSeason": GODEL_ADVERSARIAL_SEASON_ENABLED,
        "V2Contrast": GODEL_V2_CONTRAST_ENABLED,
        "Streaming": GODEL_STREAMING_ENABLED,
        "SubReaderRouting": GODEL_SUB_READER_ROUTING_ENABLED,
        "HabitProgressive": GODEL_HABIT_PROGRESSIVE_ENABLED,
    }
    enabled = [k for k, v in flags.items() if v]
    disabled = [k for k, v in flags.items() if not v]

    logger.info(
        "[GodelConfig] Enabled: %s | Disabled: %s",
        ", ".join(enabled) if enabled else "none",
        ", ".join(disabled) if disabled else "none",
    )
