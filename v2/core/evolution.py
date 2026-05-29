"""
core/evolution.py — 跨任务自我进化引擎 (P2: 认知能力进化)

设计原则 (来自 NEXT_STEPS.md P2 规划):
    - Agent 审了 100 篇后应比审第 1 篇更好
    - 进化是"持续存在的认知实体"和"被反复调用的函数"的根本区别
    - 进化知识注入遵循 §4.3（信息呈现，不是指令）：Agent 自主决定是否采纳
    - 零外部依赖：基于现有 MemoryStore 的三层记忆架构

三个子系统:
    C1 — HabitLearner: 从 ProceduralPatterns 中自动学习新的认知习惯
    R1 — EditExperienceInjector: 在 writer 视角时注入编辑经验
    C2 — AblationConfig: 约束模块的可选关闭框架

与现有系统的关系:
    - 读取: memory.py (ProceduralPattern, DomainPattern, SessionRecord)
    - 输出到: habits.py (生成 CognitiveHabit 实例)
    - 注入via: assembler.py (作为新 section 注入 system prompt)
    - 约束via: harness.py (AblationConfig 控制哪些模块启用)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.memory import MemoryStore, ProceduralPattern, DomainPattern
    from core.habits import CognitiveHabit

logger = logging.getLogger(__name__)


# ============================================================
# V3 Phase 3: Relative effectiveness scoring
# ============================================================


def compute_relative_effectiveness(
    findings_count: int,
    tokens_consumed: int,
    sections_covered: int,
    paper_type: str,
    historical_baseline: dict[str, float],
) -> float:
    """
    Compute relative effectiveness vs historical baseline for a paper type.

    Instead of absolute scoring, compares a pattern's performance against
    the historical average for that paper type.

    Args:
        findings_count: number of findings from this pattern
        tokens_consumed: tokens used when pattern was active
        sections_covered: how many sections were covered
        paper_type: paper type identifier (e.g., "DID", "RCT")
        historical_baseline: dict mapping paper_type -> avg findings_per_1k_tokens

    Returns:
        Relative effectiveness in [0.0, 1.0] range.
        0.5 = matches baseline exactly; >0.5 = above baseline; <0.5 = below.
    """
    # Current performance: findings per 1k tokens
    if tokens_consumed <= 0:
        return 0.5  # No data -> neutral
    current_rate = findings_count / (tokens_consumed / 1000.0)

    # Baseline for this paper type (fallback to global average)
    baseline_rate = historical_baseline.get(paper_type, 0.0)
    if baseline_rate <= 0:
        # No baseline data yet — use all-types average
        if historical_baseline:
            baseline_rate = sum(historical_baseline.values()) / len(historical_baseline)
        else:
            return 0.5  # No historical data at all

    # Compute relative ratio (capped at 2x for outliers)
    ratio = min(current_rate / max(baseline_rate, 0.001), 2.0)

    # Map [0, 2] -> [0.0, 1.0] via linear transform
    return min(1.0, max(0.0, ratio / 2.0))


# ============================================================
# C1: HabitLearner — 从经验中学习新的认知习惯
# ============================================================

@dataclass
class LearnedHabit:
    """
    从经验中学习到的认知习惯。

    与硬编码的 CognitiveHabit 区别:
    - source: 标注来源（哪些 ProceduralPatterns 产生了这个习惯）
    - confidence: 基于 evidence_count 和 effectiveness_score 的综合信心
    - generation: 第几代进化产生的（用于追踪进化轨迹）
    """
    id: str
    name: str
    phases: list[str]
    priority: int  # 基于 confidence 动态计算，范围 40-85（不超过硬编码习惯）
    content: str
    source_patterns: list[str]  # 来源 pattern_ids
    confidence: float  # 0.0~1.0
    generation: int = 1  # 第几代进化


class HabitLearner:
    """
    从 ProceduralPatterns 中自动学习新的认知习惯。

    学习逻辑:
        1. 筛选"成熟"的 ProceduralPatterns（evidence_count >= 3, effectiveness >= 0.6）
        2. 将策略性模式转化为习惯卡片（不是所有模式都适合成为习惯）
        3. 去重：与现有硬编码习惯对比，避免重复
        4. 优先级设定：学习到的习惯 priority 上限 85（低于大部分硬编码习惯 90+）

    设计约束:
        - 最多生成 10 条学习习惯（避免 prompt 膨胀）
        - 每条习惯 < 150 字符（简洁）
        - 纯规则提取，不调用 LLM（零运行时成本）
    """

    # 可学习的 ProceduralPattern 类别及其对应的 habit phases
    _LEARNABLE_CATEGORIES: dict[str, list[str]] = {
        "strategy_effectiveness": ["DEEP_REVIEW", "SYNTHESIS"],
        "review_focus": ["ORIENTATION", "DEEP_REVIEW"],
        "verification_strategy": ["DEEP_REVIEW"],
        "tool_sequence": ["DEEP_REVIEW", "EDITING"],
        "review_stats": ["ORIENTATION"],  # 审稿节奏相关
        "edit_strategy": ["EDITING"],     # P2/R1: 编辑策略
        "anti_pattern": ["DEEP_REVIEW", "EDITING"],  # P2-fix5: 负面经验也能晋升为警戒习惯
    }

    # 成熟度阈值
    MIN_EVIDENCE_COUNT = 3
    MIN_EFFECTIVENESS = 0.6
    MAX_LEARNED_HABITS = 10

    def __init__(self, memory: MemoryStore, existing_habit_ids: set[str] | None = None):
        """
        Args:
            memory: 跨会话记忆存储
            existing_habit_ids: 现有硬编码习惯的 ID 集合（用于去重）
        """
        self._memory = memory
        self._existing_ids = existing_habit_ids or set()

    def learn(self) -> list[LearnedHabit]:
        """
        从 ProceduralPatterns 中学习新习惯。

        Returns:
            新学习到的习惯列表（已去重、已排序）
        """
        # 1. 筛选成熟的可学习模式
        mature_patterns = self._select_mature_patterns()
        if not mature_patterns:
            return []

        # 2. 转化为习惯
        candidates: list[LearnedHabit] = []
        for pattern in mature_patterns:
            habit = self._pattern_to_habit(pattern)
            if habit and not self._is_duplicate(habit, candidates):
                candidates.append(habit)

        # 3. 按 confidence 排序，截断
        candidates.sort(key=lambda h: h.confidence, reverse=True)
        learned = candidates[:self.MAX_LEARNED_HABITS]

        if learned:
            logger.info(
                "HabitLearner: 从 %d 个成熟模式中学习了 %d 条新习惯",
                len(mature_patterns), len(learned)
            )

        return learned

    def _select_mature_patterns(self) -> list:
        """筛选满足成熟度阈值的 ProceduralPatterns。

        V3 enhanced: cross-reference with contrast results to boost patterns
        whose associated habits have positive contrast delta.
        """
        from core.memory import ProceduralPattern

        patterns = []
        for proc in self._memory.state.procedures:
            if proc.category not in self._LEARNABLE_CATEGORIES:
                continue
            if proc.evidence_count < self.MIN_EVIDENCE_COUNT:
                continue
            if proc.effectiveness_score < self.MIN_EFFECTIVENESS:
                continue
            patterns.append(proc)

        # === V3 NEW: Cross-reference with contrast results ===
        from core.godel_config import GODEL_INTRA_CONTRAST_ENABLED
        if GODEL_INTRA_CONTRAST_ENABLED:
            contrast_results = self._memory.state.contrast_results
            if contrast_results:
                for pattern in patterns:
                    related_contrast = [
                        r for r in contrast_results
                        if r.get("target_habit_id", "") == f"learned_{pattern.pattern_id}"
                    ]
                    if related_contrast:
                        avg_delta = sum(r.get("delta", 0) for r in related_contrast) / len(related_contrast)
                        # Inject contrast signal (temporary attribute for sorting)
                        pattern._contrast_boost = avg_delta
                    else:
                        pattern._contrast_boost = 0.0

        # 按综合分排序 (effectiveness * evidence + contrast boost)
        patterns.sort(
            key=lambda p: (
                p.effectiveness_score * p.evidence_count
                + getattr(p, "_contrast_boost", 0.0) * 10
            ),
            reverse=True,
        )
        return patterns

    def _pattern_to_habit(self, pattern) -> LearnedHabit | None:
        """
        将一个 ProceduralPattern 转化为 LearnedHabit。

        转化规则:
            - description → content（截断至 150 字符，前加 **标题** 格式）
            - trigger_context → 用于推断 phases
            - V3: 使用 compute_relative_effectiveness 进行相对评分
            - V3: 初始 confidence 被钳制到 [0.3, 0.7]（新习惯起始不确定）
        """
        from core.memory import ProceduralPattern

        # 确定习惯所属阶段
        phases = self._LEARNABLE_CATEGORIES.get(pattern.category, ["DEEP_REVIEW"])

        # === V3: Initial confidence from relative effectiveness ===
        baseline = self._memory.get_historical_baseline()
        if baseline:
            # Use relative scoring against historical baseline
            initial_confidence = compute_relative_effectiveness(
                findings_count=getattr(pattern, "_avg_findings", 5),
                tokens_consumed=getattr(pattern, "_avg_tokens", 50000),
                sections_covered=getattr(pattern, "_avg_sections", 10),
                paper_type=getattr(pattern, "paper_type", "unknown"),
                historical_baseline=baseline,
            )
        else:
            # Fallback to V2 formula when no baseline data exists
            import math
            initial_confidence = pattern.effectiveness_score * min(
                math.log2(pattern.evidence_count + 1) / 4.0, 1.0
            )

        # V3: Clamp initial confidence to [0.3, 0.7] (new habits start uncertain)
        confidence = max(0.3, min(0.7, initial_confidence))

        # 计算 priority (40-85 范围)
        priority = int(40 + confidence * 45)

        # 生成习惯内容
        # 从 description 和 trigger_context 组合成自然语言习惯
        # P2-fix5: anti_pattern 类型生成警戒性内容（"避免..." 而非 "应当..."）
        name = self._extract_short_name(pattern.description)
        if pattern.category == "anti_pattern":
            content = self._format_anti_pattern_content(pattern)
        else:
            content = self._format_habit_content(pattern)

        # 生成唯一 ID
        habit_id = f"learned_{pattern.pattern_id}"

        return LearnedHabit(
            id=habit_id,
            name=name,
            phases=phases,
            priority=priority,
            content=content,
            source_patterns=[pattern.pattern_id],
            confidence=confidence,
        )

    def _extract_short_name(self, description: str) -> str:
        """从描述中提取 2-4 字短名。"""
        # 取前 10 个字符作为短名（截断到合理位置）
        if len(description) <= 10:
            return description
        # 尝试在标点处断开
        for i in range(min(10, len(description)), 3, -1):
            if description[i] in "，。、；:：":
                return description[:i]
        return description[:8] + "…"

    def _format_anti_pattern_content(self, pattern) -> str:
        """P2-fix5: 将 anti_pattern 格式化为警戒性习惯文本。

        格式: **[警戒] 短名**：避免...。触发场景。
        """
        name = self._extract_short_name(pattern.description)
        desc = pattern.description[:120]
        trigger = pattern.trigger_context[:80] if pattern.trigger_context else ""

        content = f"**[警戒] {name}**：避免此行为——{desc}"
        if trigger:
            content += f"。识别信号：{trigger}"
        return content[:150]

    def _format_habit_content(self, pattern) -> str:
        """
        将 ProceduralPattern 格式化为习惯文本。

        格式: **短名**：描述。触发场景。
        """
        name = self._extract_short_name(pattern.description)
        desc = pattern.description[:120]
        trigger = pattern.trigger_context[:80] if pattern.trigger_context else ""

        content = f"**{name}**：{desc}"
        if trigger:
            content += f"（触发：{trigger}）"

        # 确保总长 < 200 字符
        if len(content) > 200:
            content = content[:197] + "..."

        return content

    def _is_duplicate(self, new_habit: LearnedHabit, existing: list[LearnedHabit]) -> bool:
        """检查新习惯是否与已有习惯重复。"""
        # 与硬编码习惯的 ID 去重
        if new_habit.id in self._existing_ids:
            return True

        # 与已生成的学习习惯内容去重（简单词重叠）
        new_words = set(new_habit.content.lower().split())
        for h in existing:
            h_words = set(h.content.lower().split())
            if not new_words or not h_words:
                continue
            overlap = len(new_words & h_words) / min(len(new_words), len(h_words))
            if overlap > 0.6:
                return True

        return False


# ============================================================
# R1: EditExperienceInjector — 编辑经验回注
# ============================================================

@dataclass
class EditExperience:
    """一条编辑经验。"""
    section_type: str  # e.g. "introduction", "methods", "abstract"
    lesson: str  # 经验教训
    effectiveness: float  # 历史有效性
    evidence_count: int


class EditExperienceInjector:
    """
    在 Writer persona 激活时注入相关编辑经验。

    回注逻辑:
        1. 从 ProceduralPatterns 中筛选 category="edit_strategy" 或 tool_sequence 中含编辑工具的模式
        2. 从 DomainPatterns 中筛选 category="writing" 的模式
        3. 根据当前论文的 sections 匹配相关经验
        4. 格式化为 ≤400 tokens 的注入文本

    注入时机:
        - __SWITCH__ 切换到 writer 时（loop.py 中触发）
        - 或 assembler 的 section 中作为 "edit_experience" section

    设计约束:
        - 最多注入 5 条经验（避免 prompt 膨胀）
        - 纯信息呈现，不是指令（"你过去的经验表明…" 而非 "你必须…"）
        - 零 LLM 成本：纯规则匹配
    """

    # 编辑相关的 ProceduralPattern 类别
    _EDIT_CATEGORIES: set[str] = {
        "edit_strategy",
        "tool_sequence",
        "verification_strategy",
    }

    # 编辑相关的 DomainPattern 类别
    _WRITING_CATEGORIES: set[str] = {
        "writing",
        "ai_signals",
        "typical_weakness",
    }

    MAX_EXPERIENCES = 5

    def __init__(self, memory: MemoryStore):
        self._memory = memory

    def get_edit_experiences(
        self,
        target_sections: list[str] | None = None,
    ) -> list[EditExperience]:
        """
        获取与当前编辑任务相关的经验。

        Args:
            target_sections: 当前要编辑的 sections（如 ["introduction", "abstract"]）。
                            None 时返回通用编辑经验。

        Returns:
            相关的编辑经验列表（按 effectiveness 排序）
        """
        experiences: list[EditExperience] = []

        # 1. 从 ProceduralPatterns 中提取编辑相关经验
        for proc in self._memory.state.procedures:
            if proc.category not in self._EDIT_CATEGORIES:
                continue
            if proc.effectiveness_score < 0.5:
                continue

            # 如果有 target_sections，检查 trigger_context 是否包含相关 section
            if target_sections:
                relevant = any(
                    sec.lower() in proc.trigger_context.lower() or
                    sec.lower() in proc.description.lower()
                    for sec in target_sections
                )
                if not relevant and proc.category != "verification_strategy":
                    continue

            experiences.append(EditExperience(
                section_type=self._infer_section_type(proc.trigger_context, proc.description),
                lesson=proc.description,
                effectiveness=proc.effectiveness_score,
                evidence_count=proc.evidence_count,
            ))

        # 2. 从 DomainPatterns 中提取写作相关经验
        for pattern in self._memory.state.patterns:
            if pattern.category not in self._WRITING_CATEGORIES:
                continue
            if pattern.evidence_count < 2:
                continue

            experiences.append(EditExperience(
                section_type=self._infer_section_type("", pattern.description),
                lesson=pattern.description,
                effectiveness=0.7,  # DomainPattern 没有 effectiveness，给默认值
                evidence_count=pattern.evidence_count,
            ))

        # 3. 排序并截断
        experiences.sort(
            key=lambda e: e.effectiveness * e.evidence_count,
            reverse=True,
        )
        return experiences[:self.MAX_EXPERIENCES]

    def format_for_injection(
        self,
        target_sections: list[str] | None = None,
    ) -> str | None:
        """
        格式化编辑经验为可注入 system prompt 的文本。

        Returns:
            格式化文本（≤400 tokens），或 None（无相关经验）
        """
        experiences = self.get_edit_experiences(target_sections)
        if not experiences:
            return None

        lines = ["## 你的编辑经验（来自历史审稿）", ""]
        for exp in experiences:
            score_pct = int(exp.effectiveness * 100)
            section_tag = f"[{exp.section_type}] " if exp.section_type != "general" else ""
            lines.append(
                f"- {section_tag}{exp.lesson} "
                f"(有效性 {score_pct}%, 验证 {exp.evidence_count} 次)"
            )
            lines.append("")

        result = "\n".join(lines).rstrip()

        # 确保 ≤ 1200 字符（约 400 tokens）
        if len(result) > 1200:
            result = result[:1197] + "..."

        return result

    def _infer_section_type(self, trigger: str, description: str) -> str:
        """从 trigger/description 推断关联的 section 类型。"""
        combined = (trigger + " " + description).lower()

        section_keywords = {
            "introduction": ["introduction", "intro", "开头", "引言"],
            "abstract": ["abstract", "摘要"],
            "methods": ["method", "methodology", "方法", "实验设计"],
            "results": ["result", "findings", "结果", "表格"],
            "discussion": ["discussion", "讨论", "limitation"],
            "conclusion": ["conclusion", "结论"],
        }

        for section, keywords in section_keywords.items():
            if any(kw in combined for kw in keywords):
                return section

        return "general"


# ============================================================
# C2: AblationConfig — 认知约束模块的可选关闭
# ============================================================

@dataclass
class AblationConfig:
    """
    约束模块的 Ablation 配置。

    用于实验性地关闭各个认知约束模块，量化每个模块的边际贡献。
    生产环境应全部启用（默认值）；实验/评估时按需关闭。

    设计原则:
        - 每个 flag 控制一个独立约束模块
        - 关闭 = 模块不产生 nudge/信号，但不影响其他模块
        - 支持组合关闭（2^N 实验空间）
        - 不影响安全兜底（doom_loop_guard 和 token_budget 不可关闭）
    """

    # 可关闭的约束模块
    boundary_guard: bool = True         # 边界守护（doom loop 检测、质量门）
    phase_fsm_nudges: bool = True       # Phase FSM 的 nudge 信号
    habit_injection: bool = True        # 认知习惯注入
    finding_quality_gate: bool = True   # finding 质量阈值
    hypothesis_module: bool = True      # 假说驱动工作记忆
    session_memory: bool = True         # 会话中认知笔记更新
    memory_injection: bool = True       # 跨会话记忆注入
    evolution_engine: bool = True       # P2 进化引擎（习惯学习 + 编辑经验）
    metacognition: bool = True          # 元认知状态

    # 不可关闭的安全兜底（仅用于文档说明）
    # doom_loop_guard: always True
    # token_budget: always True

    def describe(self) -> str:
        """生成当前配置的人类可读描述。"""
        disabled = []
        for field_name in [
            "boundary_guard", "phase_fsm_nudges", "habit_injection",
            "finding_quality_gate", "hypothesis_module", "session_memory",
            "memory_injection", "evolution_engine", "metacognition",
        ]:
            if not getattr(self, field_name):
                disabled.append(field_name)

        if not disabled:
            return "All cognitive constraints enabled (production mode)"
        return f"Ablation mode: disabled [{', '.join(disabled)}]"

    @classmethod
    def all_enabled(cls) -> "AblationConfig":
        """生产环境默认配置（全部启用）。"""
        return cls()

    @classmethod
    def single_ablation(cls, module_name: str) -> "AblationConfig":
        """关闭单个模块的 ablation 配置。"""
        config = cls()
        if hasattr(config, module_name):
            setattr(config, module_name, False)
        else:
            raise ValueError(f"Unknown module: {module_name}")
        return config

    @classmethod
    def from_dict(cls, d: dict) -> "AblationConfig":
        """从字典创建配置。"""
        config = cls()
        for key, value in d.items():
            if hasattr(config, key) and isinstance(value, bool):
                setattr(config, key, value)
        return config


# ============================================================
# EvolutionEngine — 统一入口
# ============================================================

class EvolutionEngine:
    """
    P2 进化引擎的统一入口。

    职责:
        1. 协调 HabitLearner 和 EditExperienceInjector
        2. 管理学习到的习惯的生命周期（生成、注入、衰减）
        3. 提供给 Assembler 的 section 内容
        4. 记录进化统计（用于 C2 ablation 实验）

    生命周期:
        - 会话开始时: learn_habits() → 生成/更新学习习惯
        - 每轮 system prompt 刷新时: get_evolution_context() → 注入相关进化知识
        - Writer 激活时: get_edit_experience() → 注入编辑经验
        - 会话结束时: record_evolution_stats() → 记录进化效果

    与 HabitSelector 的协作:
        - EvolutionEngine 生成 LearnedHabits
        - HabitSelector 扩展为支持 learned habits（作为低优先级候选）
        - 总注入量仍然 ≤5 条/轮（不改变 max_per_turn）
    """

    def __init__(self, memory: MemoryStore, ablation: AblationConfig | None = None):
        self._memory = memory
        self._ablation = ablation or AblationConfig.all_enabled()
        self._learned_habits: list[LearnedHabit] = []
        self._edit_injector = EditExperienceInjector(memory)

        # V3: IntraSession Contrast
        self._intra_contrast_manager = IntraSessionContrastManager()
        self._current_contrast_plan: dict | None = None

        # 统计
        self._habits_generated: int = 0
        self._habits_injected: int = 0
        self._edit_experiences_injected: int = 0

    @property
    def learned_habits(self) -> list[LearnedHabit]:
        """当前学习到的习惯。"""
        return self._learned_habits

    def initialize(
        self,
        existing_habit_ids: set[str] | None = None,
        paper_sections: dict[str, str] | None = None,
    ) -> None:
        """
        会话开始时调用：学习新习惯 + V3 IntraSession contrast planning。

        Args:
            existing_habit_ids: 硬编码习惯的 ID 集合
            paper_sections: 当前论文的 section dict (key=section_name)。
                如果提供，用当前论文的 sections 做 contrast planning；
                否则回退到从历史 section_experiences 推断（可能不准确）。
        """
        if not self._ablation.evolution_engine:
            logger.debug("EvolutionEngine disabled by ablation config")
            return

        learner = HabitLearner(self._memory, existing_habit_ids)
        self._learned_habits = learner.learn()
        self._habits_generated = len(self._learned_habits)

        if self._learned_habits:
            logger.info(
                "EvolutionEngine: 初始化完成, 学习了 %d 条新习惯 (confidence range: %.2f~%.2f)",
                len(self._learned_habits),
                self._learned_habits[-1].confidence,
                self._learned_habits[0].confidence,
            )

        # === V3 NEW: Plan IntraSession contrast ===
        from core.godel_config import GODEL_INTRA_CONTRAST_ENABLED
        if GODEL_INTRA_CONTRAST_ENABLED and self._learned_habits:
            sections = self._get_paper_sections(paper_sections)
            if sections:
                self._current_contrast_plan = (
                    self._intra_contrast_manager.plan_contrast(
                        sections=sections, habits=self._learned_habits
                    )
                )
                if self._current_contrast_plan:
                    logger.info(
                        "IntraSession contrast planned: target=%s, A=%d sections, B=%d sections",
                        self._current_contrast_plan["target_habit_id"],
                        len(self._current_contrast_plan["phase_a_sections"]),
                        len(self._current_contrast_plan["phase_b_sections"]),
                    )

    def get_contrast_plan(self) -> dict | None:
        """Return current session's contrast plan for Assembler/Finalizer."""
        return self._current_contrast_plan

    def _get_paper_sections(self, paper_sections: dict[str, str] | None = None) -> list[str]:
        """
        Retrieve paper section names for contrast planning.

        Priority:
        1. Current paper's sections (from paper_sections dict, excludes "full")
        2. Historical section_experiences from last session (fallback)
        3. Empty list (no contrast possible)
        """
        # Priority 1: Current paper sections (best source)
        if paper_sections:
            sections = [k for k in paper_sections if k != "full"]
            if sections:
                return sections

        # Priority 2: Historical section experiences (fallback, may be stale)
        recent_exps = self._memory.state.section_experiences
        if recent_exps:
            # Collect unique section names from the last session
            last_session_id = recent_exps[-1].get("session_id", "")
            sections = []
            seen: set[str] = set()
            for exp in recent_exps:
                if exp.get("session_id") == last_session_id:
                    name = exp.get("section_name", "")
                    if name and name not in seen:
                        sections.append(name)
                        seen.add(name)
            if sections:
                return sections
        return []

    # V3 Phase 3: Abandonment cooldown (sessions since abandonment)
    ABANDONMENT_CONFIDENCE_THRESHOLD = 0.3
    ABANDONMENT_COOLDOWN_SESSIONS = 12

    def get_habits_for_selector(self) -> list:
        """
        将学习习惯转化为 CognitiveHabit 格式，供 HabitSelector 使用。

        V3: Filters out abandoned habits (confidence < 0.3) and
        habits in cooldown period (abandoned within last 12 sessions).

        Returns:
            CognitiveHabit 列表（learned habits 转换后）
        """
        if not self._ablation.evolution_engine:
            return []

        from core.habits import CognitiveHabit

        # V3: Get abandoned habit IDs in cooldown
        abandoned_ids = self._get_abandoned_habit_ids_in_cooldown()

        result = []
        for lh in self._learned_habits:
            # V3 Phase 3: Skip abandoned habits (confidence < threshold)
            if lh.confidence < self.ABANDONMENT_CONFIDENCE_THRESHOLD:
                continue
            # V3 Phase 3: Skip habits in cooldown
            if lh.id in abandoned_ids:
                continue
            result.append(CognitiveHabit(
                id=lh.id,
                name=lh.name,
                phases=lh.phases,
                priority=lh.priority,
                content=lh.content,
                triggers=[],  # learned habits 没有预设 triggers
            ))
        return result

    def _get_abandoned_habit_ids_in_cooldown(self) -> set[str]:
        """
        V3 Phase 3: Get habit IDs that were abandoned recently (within cooldown).

        Checks evolution_records for "retire" decisions within the last
        ABANDONMENT_COOLDOWN_SESSIONS sessions.

        DeepReflector persists records with structure:
            {
                "trigger_type": "deep",
                "session_count": N,
                "habit_decisions": [{"habit_id": "...", "action": "retire", ...}],
                ...
            }
        """
        cooldown_ids: set[str] = set()
        total_sessions = len(self._memory.state.session_experiences_v3)
        for record in self._memory.state.evolution_records:
            session_at = record.get("session_count", 0)
            if total_sessions - session_at >= self.ABANDONMENT_COOLDOWN_SESSIONS:
                continue  # Outside cooldown window, skip
            # Scan nested habit_decisions for "retire" actions
            for decision in record.get("habit_decisions", []):
                if decision.get("action") == "retire":
                    habit_id = decision.get("habit_id", "")
                    if habit_id:
                        cooldown_ids.add(habit_id)
        return cooldown_ids

    def get_edit_experience_context(
        self,
        target_sections: list[str] | None = None,
    ) -> str | None:
        """
        获取编辑经验注入文本（Writer 激活时调用）。

        Args:
            target_sections: 当前要编辑的 sections

        Returns:
            格式化的编辑经验文本，或 None
        """
        if not self._ablation.evolution_engine:
            return None

        result = self._edit_injector.format_for_injection(target_sections)
        if result:
            self._edit_experiences_injected += 1
        return result

    def get_evolution_summary(self) -> str | None:
        """
        获取进化状态摘要（可作为 assembler section 注入）。

        只在有学习习惯时返回内容。
        """
        if not self._ablation.evolution_engine:
            return None

        if not self._learned_habits:
            return None

        lines = [
            f"## 认知进化状态",
            f"",
            f"你已从 {self._memory.state.sessions.__len__()} 次审稿经验中学习了 "
            f"{len(self._learned_habits)} 条新习惯。",
            f"这些习惯会自动融入你的认知过程——它们来自你过去的成功模式。",
        ]

        # 列出 top-3 学习习惯
        top_habits = self._learned_habits[:3]
        if top_habits:
            lines.append("")
            lines.append("最近学到的认知习惯:")
            for h in top_habits:
                lines.append(f"  • {h.name} (置信度 {h.confidence:.0%})")

        return "\n".join(lines)

    def record_session_stats(self) -> dict:
        """
        会话结束时记录进化统计。

        Returns:
            统计字典（可持久化到 MemoryStore）
        """
        return {
            "habits_generated": self._habits_generated,
            "habits_injected": self._habits_injected,
            "edit_experiences_injected": self._edit_experiences_injected,
            "total_learned_habits": len(self._learned_habits),
        }


# ============================================================
# V3: IntraSession Contrast Manager
# ============================================================

class IntraSessionContrastManager:
    """
    V3 Intra-session cognitive contrast manager.

    Design rationale (from GODEL_AGENT_PLAN_V3 §4.3):
        - 50-70 page papers have 15-25 sections
        - Split sections into Phase A and Phase B
        - Phase A: inject full habit set
        - Phase B: inject habit set minus 1 target habit under validation
        - Same paper -> controls paper quality confound
        - Same session -> controls Agent state confound
        - N section observations > 1 session observation

    Constraints:
        - Does not affect review quality (Phase B only removes 1 habit, not all)
        - Does not affect findings completeness (contrast is post-hoc analysis)
        - Only habits with confidence in [0.4, 0.7] are selected as targets
        - Requires minimum INTRA_CONTRAST_MIN_SECTIONS sections
    """

    PHASE_SPLIT_RATIO = 0.5
    TARGET_CONFIDENCE_RANGE = (0.4, 0.7)

    def plan_contrast(
        self, sections: list[str], habits: list[LearnedHabit]
    ) -> dict | None:
        """
        Plan contrast at session start.

        Args:
            sections: Paper section names (from paper_structure_index or PCG)
            habits: Current learned habits

        Returns:
            Contrast plan dict or None if no suitable habit to validate.
            {
                "target_habit_id": "learned_003",
                "phase_a_sections": [...],
                "phase_b_sections": [...],
                "phase_a_habits": [...],  # all habit IDs
                "phase_b_habits": [...],  # all minus target
            }
        """
        from core.godel_config import INTRA_CONTRAST_MIN_SECTIONS

        if len(sections) < INTRA_CONTRAST_MIN_SECTIONS:
            return None

        target = self._select_target_habit(habits)
        if target is None:
            return None

        split_point = int(len(sections) * self.PHASE_SPLIT_RATIO)
        phase_a = sections[:split_point]
        phase_b = sections[split_point:]

        all_habit_ids = [h.id for h in habits]
        phase_b_habits = [h.id for h in habits if h.id != target.id]

        return {
            "target_habit_id": target.id,
            "phase_a_sections": phase_a,
            "phase_b_sections": phase_b,
            "phase_a_habits": all_habit_ids,
            "phase_b_habits": phase_b_habits,
        }

    def analyze_contrast(
        self, section_experiences: list[dict], plan: dict
    ) -> dict:
        """
        Post-session contrast analysis.

        Compares findings density between Phase A (all habits) and Phase B
        (target habit removed). Positive delta means the target habit helps.

        Args:
            section_experiences: L0 experiences from this session
            plan: The contrast plan from plan_contrast()

        Returns:
            {
                "target_habit_id": str,
                "phase_a_findings_density": float,
                "phase_b_findings_density": float,
                "delta": float,  # A - B, positive = habit effective
                "statistical_note": str,
                "recommendation": "reinforce" | "doubt" | "insufficient_data"
            }
        """
        phase_a_sections = set(plan["phase_a_sections"])
        phase_b_sections = set(plan["phase_b_sections"])

        a_exps = [
            e for e in section_experiences
            if e.get("section_name") in phase_a_sections
        ]
        b_exps = [
            e for e in section_experiences
            if e.get("section_name") in phase_b_sections
        ]

        if len(a_exps) < 3 or len(b_exps) < 3:
            return {
                "target_habit_id": plan["target_habit_id"],
                "recommendation": "insufficient_data",
            }

        a_density = sum(e.get("findings_produced", 0) for e in a_exps) / len(a_exps)
        b_density = sum(e.get("findings_produced", 0) for e in b_exps) / len(b_exps)
        delta = a_density - b_density

        if delta > 0.15:
            recommendation = "reinforce"
        elif delta < -0.15:
            recommendation = "doubt"
        else:
            recommendation = "insufficient_data"

        return {
            "target_habit_id": plan["target_habit_id"],
            "phase_a_findings_density": round(a_density, 4),
            "phase_b_findings_density": round(b_density, 4),
            "delta": round(delta, 4),
            "statistical_note": f"N_a={len(a_exps)}, N_b={len(b_exps)}",
            "recommendation": recommendation,
        }

    def _select_target_habit(self, habits: list[LearnedHabit]) -> LearnedHabit | None:
        """
        Select most suitable habit for validation.

        Selection strategy:
            - Confidence must be in TARGET_CONFIDENCE_RANGE [0.4, 0.7]
            - Pick the one closest to 0.55 (maximum uncertainty → maximum information gain)
        """
        lo, hi = self.TARGET_CONFIDENCE_RANGE
        candidates = [h for h in habits if lo <= h.confidence <= hi]
        if not candidates:
            return None
        return min(candidates, key=lambda h: abs(h.confidence - 0.55))
