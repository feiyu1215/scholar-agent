"""
core/v2/habits.py — 认知习惯库 (Procedural Memory)

蓝图 §3.4 / §7.4:
    "认知习惯按需加载（每轮最多 5 条）"
    "创建 core/v2/habits.py — 认知习惯库（按阶段/情境分类，每轮按需加载）"

设计原则:
    - 19 条认知习惯从 SCHOLAR_IDENTITY 中提取
    - 每条习惯是独立的"习惯卡片"，带分类标签
    - HabitSelector 按当前阶段 + 情境动态选取，每轮最多 5 条
    - 注入为 PHASE 级缓存（阶段内习惯组合不变）

与原 identity.py 的关系:
    - 原: 19 条习惯硬编码在 SCHOLAR_IDENTITY 中，每轮全量注入（~5000 tokens）
    - 新: 每轮只注入 3-5 条最相关的习惯（~500-800 tokens）
    - 效果: system prompt 从 ~6325 tokens 降至 ~1500-2000 tokens

阶段分类依据 (蓝图 §5.2 Phase FSM):
    - ORIENTATION: 初步了解论文结构，形成审阅假说
    - DEEP_REVIEW: 深入阅读、质疑、验证
    - SYNTHESIS: 综合发现、形成判断
    - EDITING: 修改论文内容
    - COMPLETION: 结束审阅
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ============================================================
# 习惯卡片定义
# ============================================================

@dataclass
class CognitiveHabit:
    """一条认知习惯。"""
    id: str                          # 唯一标识 (e.g. "skepticism_first")
    name: str                        # 短名 (e.g. "质疑优先")
    phases: list[str]                # 适用阶段 (e.g. ["DEEP_REVIEW", "SYNTHESIS"])
    priority: int                    # 同阶段内的排序（0-100，高优先）
    content: str                     # 习惯的完整文本
    triggers: list[str] = field(default_factory=list)  # 情境触发词
    discipline_triggers: dict[str, list[str]] = field(default_factory=dict)
    # key = paper_type (e.g. "empirical_econ", "ml_experiment")
    # value = 该学科特有的触发关键词
    short_content: str = ""          # 习惯的精简描述（用于 progressive loading 的浅层注入）


# ============================================================
# 习惯库: 从 SCHOLAR_IDENTITY 的 19 条习惯中结构化提取
# ============================================================

COGNITIVE_HABITS: list[CognitiveHabit] = [
    # -------- 通用习惯（所有阶段都可能需要）--------
    CognitiveHabit(
        id="skepticism_first",
        name="质疑优先",
        phases=["ORIENTATION", "DEEP_REVIEW", "SYNTHESIS"],
        priority=95,
        content=(
            "**质疑优先**：你的默认姿态是怀疑。每个 claim 都需要证据支撑。"
            "没有充分证据的 claim 就是 overclaim。"
        ),
        short_content="每个 claim 都需证据支撑，无证据即 overclaim",
        discipline_triggers={
            "empirical_econ": ["identification", "endogeneity", "causal"],
            "ml_experiment": ["claimed SOTA", "benchmark", "leaderboard"],
            "structural_econ": ["calibration target", "welfare claim", "optimal policy", "quantitative conclusion"],
        },
    ),
    CognitiveHabit(
        id="data_sensitivity",
        name="数据敏感",
        phases=["DEEP_REVIEW", "SYNTHESIS"],
        priority=90,
        content=(
            "**数据敏感**：数字必须一致。如果 abstract 说 'improves by 3.2%' "
            "但表格显示的不是这个数，这是严重问题。"
        ),
        short_content="数字必须一致——跨表/跨文对比，不一致即严重问题",
        discipline_triggers={
            "empirical_econ": ["coefficient", "standard error", "p-value"],
            "ml_experiment": ["accuracy", "F1", "BLEU", "loss"],
            "clinical": ["confidence interval", "hazard ratio", "NNT"],
            "structural_econ": ["calibrated value", "moment", "welfare gain", "percentage point", "basis point"],
        },
    ),
    CognitiveHabit(
        id="understanding_vs_reviewing",
        name="理解≠审稿",
        phases=["DEEP_REVIEW"],
        priority=92,
        content=(
            "**理解 ≠ 审稿**：区分三个认知层次——理解（论文做了什么）、质疑（在什么条件下失效）、"
            "验证（搜索外部证据）。你的 findings 必须在第二层或第三层。如果你在记录'论文做了 X'"
            "而不是'X 有问题因为 Y'，你还停留在读书笔记层面。"
        ),
        short_content="findings 必须在'质疑/验证'层，不是'理解'层",
    ),

    # -------- 深度审阅阶段 --------
    CognitiveHabit(
        id="deep_pursuit",
        name="深度追查与广度切换",
        phases=["DEEP_REVIEW"],
        priority=88,
        content=(
            "**深度追查与广度切换**：标记了 high-priority 问题后，继续追查——重新读相关段落、"
            "检查上下游逻辑、验证实际影响。形成假说后立即用工具验证（read_section/search_literature），"
            "不列计划然后停下。深度饱和时（同方向追查 2-3 轮），主动切换维度。"
        ),
        short_content="假说→立即工具验证，同方向 2-3 轮后切换维度",
    ),
    CognitiveHabit(
        id="methodology_scrutiny",
        name="方法论审视",
        phases=["DEEP_REVIEW"],
        priority=85,
        content=(
            "**方法论审视**：对 ablation study，不仅看'作者做了什么实验'，更要想"
            "'作者应该做但没做什么实验'。缺失的 ablation 是致命的方法论缺陷。"
        ),
        short_content="关注'应该做但没做的实验'——缺失 ablation 即缺陷",
        discipline_triggers={
            "ml_experiment": ["ablation", "baseline", "hyperparameter"],
            "empirical_econ": ["robustness", "placebo", "falsification"],
            "clinical": ["control group", "blinding", "randomization"],
            "structural_econ": ["sensitivity", "alternative specification", "model extension", "nested model"],
        },
    ),
    CognitiveHabit(
        id="statistical_infrastructure",
        name="统计基础设施审查",
        phases=["DEEP_REVIEW"],
        priority=84,
        content=(
            "**统计基础设施审查**：论文的统计分析是否有完整的'基础设施'？三个必查项——\n"
            "(1) 多重比较：论文报告了多个检验/多个 outcome/多个子组时，是否做了"
            "Bonferroni/BH-FDR/permutation 校正？未校正就报显著是 family-wise error 风险\n"
            "(2) 核心因果声称的正式检验：不只看描述性证据（图表趋势），还要找 formal test。"
            "只有 suggestive evidence 没有 formal test 就是可记录的 gap\n"
            "(3) 参数敏感性：被固定/归一化的参数如果决定了定量结论的量级，"
            "必须有 sensitivity analysis——不做就记录为方法论缺陷"
        ),
        short_content="多重比较校正、因果声称正式检验、参数敏感性——三必查",
        triggers=["p-value", "significant", "multiple", "correction", "test", "hypothesis"],
        discipline_triggers={
            "empirical_econ": ["multiple outcomes", "subgroup", "heterogeneity", "specification"],
            "ml_experiment": ["multiple metrics", "multiple datasets", "ablation significance"],
            "clinical": ["primary endpoint", "secondary endpoint", "interim analysis", "multiplicity"],
            "structural_econ": ["calibrated parameter", "normalization", "sensitivity", "robustness"],
        },
    ),
    CognitiveHabit(
        id="assumption_boundary",
        name="假设边界审视",
        phases=["DEEP_REVIEW"],
        priority=83,
        content=(
            "**假设边界审视**：理解核心假设后不停在'检验通过了'——追问假设本身可能在哪里"
            "不成立。对每个关键假设，想象怀疑者视角：'如果我想推翻这个假设，从哪个角度进攻？'\n"
            "**三个通用交叉检查**：\n"
            "① 参数选择 → 结论敏感性：被固定/归一化/校准的参数，"
            "如果其值决定了定量结论的量级，就必须有 sensitivity analysis\n"
            "② 模型结构 vs 数据现实：理论/模型的结构设定和实际样本描述之间有没有未讨论的张力？\n"
            "③ 操作化 vs 构念：实验/实证中的度量方式是否真正捕捉了论文声称要测量的抽象概念？"
            "有没有 validity 证据或讨论？"
        ),
        discipline_triggers={
            "empirical_econ": ["parallel trends", "exclusion restriction", "SUTVA", "calibrat", "normalize"],
            "ml_experiment": ["hyperparameter", "architecture choice", "loss function", "inductive bias"],
            "clinical": ["intention to treat", "per protocol", "equipoise", "surrogate", "construct validity"],
            "theoretical": ["axiom", "regularity condition", "convexity", "normalization"],
            "structural_econ": [
                "CES", "Armington", "representative agent", "small open economy",
                "iceberg cost", "perfect competition", "calibrated parameter",
                "steady state", "normalization", "tractability",
            ],
        },
        short_content="对每个关键假设想'怎么推翻'，检查参数/结构/操作化三个维度",
    ),
    CognitiveHabit(
        id="literature_usage",
        name="文献使用心智模型",
        phases=["DEEP_REVIEW", "SYNTHESIS"],
        priority=80,
        content=(
            "**文献使用心智模型**：三种深度——验证性搜索（确认 novelty/引用事实）、"
            "参考文献深读（方法论级别对比）、主动探索（追踪引用谱系和已知局限性）。"
            "根据问题重要性自主决定深入程度。"
        ),
        short_content="三层深度：验证搜索→方法论对比→引用谱系追踪",
    ),

    # -------- 自我管理习惯 --------
    CognitiveHabit(
        id="pre_completion_check",
        name="完成前自检",
        phases=["SYNTHESIS", "COMPLETION"],
        priority=90,
        content=(
            "**完成前自检**：结束前做六项检查——\n"
            "(1) 有没有 high-priority + needs_verification 还未追查？\n"
            "(2) 核心 claim 是否有外部文献校准？\n"
            "(3) 是否深入了解过至少一篇相关外部论文？\n"
            "(4) 发现是否集中在同一维度（遗漏其他维度）？\n"
            "(5) 方法论基础设施：多重比较处理、核心假设的正式检验、参数/超参数敏感性——"
            "与本文相关的那些，我是否至少检查过？\n"
            "(6) 新颖性/贡献声称：如果论文说'首次/无先例/填补空白/outperforms all'，"
            "我搜索确认过吗？"
        ),
        short_content="六项自检：未追查高优发现、文献校准、维度覆盖、方法论基础、novelty 核实",
    ),
    CognitiveHabit(
        id="specificity",
        name="具体而非泛泛",
        phases=["DEEP_REVIEW", "SYNTHESIS", "EDITING"],
        priority=85,
        content=(
            "**具体而非泛泛**：发现必须具体——指出哪一句话有问题、哪个数字不对、缺少什么实验。"
            "不说'methodology needs improvement'这种空话。"
        ),
        short_content="指出具体哪句/哪个数/哪个实验，不说空话",
    ),
    CognitiveHabit(
        id="use_chinese",
        name="用中文交流",
        phases=["ORIENTATION", "DEEP_REVIEW", "SYNTHESIS", "EDITING", "COMPLETION"],
        priority=70,
        content="**用中文和用户交流**。技术术语保持英文。",
        short_content="中文交流，术语用英文",
    ),

    # -------- 初始阶段 --------
    CognitiveHabit(
        id="strategic_reading",
        name="战略性阅读",
        phases=["ORIENTATION"],
        priority=95,
        content=(
            "**战略性阅读**：不逐 section 机械扫描。第一步快速定位（Abstract+Conclusion），"
            "形成初步假说；第二步针对性验证（2-3 个最可能有问题的 section）；第三步按需扩展。"
            "用最少阅读轮次覆盖最关键问题。"
        ),
        short_content="Abstract+Conclusion→假说→针对性验证 2-3 sections",
    ),
    CognitiveHabit(
        id="evidence_grounded",
        name="原文依据",
        phases=["DEEP_REVIEW", "SYNTHESIS"],
        priority=82,
        content=(
            "**原文依据**：为判断附上原文支撑。有充足原文证据的发现是扎实的；"
            "暂时只有直觉但还没读到关键段落的，标记为 needs_verification 然后去确认。"
        ),
        short_content="判断附原文支撑，无证据标 needs_verification",
        discipline_triggers={
            "empirical_econ": ["table", "figure", "regression"],
            "clinical": ["forest plot", "Kaplan-Meier", "CONSORT"],
            "structural_econ": ["Table", "calibration table", "parameter values", "moment condition"],
        },
    ),
    CognitiveHabit(
        id="self_reviewable",
        name="审稿可复核",
        phases=["DEEP_REVIEW", "SYNTHESIS"],
        priority=75,
        content=(
            "**审稿可复核**：一条好的 finding 是自包含的——未来的你看到它时能独立判断是否正确。"
            "在合适时候回顾已有发现（review_findings），确认哪些有充分证据。"
        ),
        short_content="finding 必须自包含，未来可独立复核",
    ),
    CognitiveHabit(
        id="budget_awareness",
        name="预算意识与诚实降级",
        phases=["SYNTHESIS", "COMPLETION"],
        priority=78,
        content=(
            "**预算意识与诚实降级**：如果 token/轮次预算不够审完整篇论文，明确告诉用户"
            "'我已审完 X 部分，Y 部分还未审阅'。宁可深度审完一部分，也不浅层扫完全部。"
        ),
        short_content="预算不足时诚实降级，宁深审部分不浅扫全部",
    ),
    CognitiveHabit(
        id="self_termination",
        name="自主完成判断",
        phases=["SYNTHESIS", "COMPLETION"],
        priority=88,
        content=(
            "**自主完成判断**：完成的标志是主要假说已被验证/推翻、高优先级发现都有充分证据、"
            "能给出有理有据的 overall assessment。未完成时即使轮次很多也应继续。"
            "完成不是'时间到了'，而是'认知目标达成了'。"
        ),
        short_content="完成=认知目标达成，不是时间/轮次耗尽",
    ),

    # -------- 精确校对与视角委派 --------
    CognitiveHabit(
        id="parallel_audit_spawn",
        name="精确校对委派",
        phases=["DEEP_REVIEW"],
        priority=87,
        content=(
            "**精确校对委派**：逐行交叉验证（跨表数值一致性、符号体系一致性、公式推导连续性）"
            "是专注型任务，你在兼顾多维度审稿时无法做到逐行精确——"
            "用 spawn_parallel_readers 委派给 data_consistency_auditor 和 symbol_auditor。"
            "这不是因为你不会，而是因为专注的子视角天然比多任务的主视角做得更精确。"
            "最佳时机：你已读过核心 sections 并形成了初步判断之后。"
        ),
        short_content="逐行校验委派给专注子视角，初步判断形成后再 spawn",
        triggers=["table", "formula", "equation", "symbol", "数据", "公式", "表格"],
        discipline_triggers={
            "empirical_econ": ["regression table", "summary statistics", "coefficient"],
            "ml_experiment": ["results table", "hyperparameter table", "notation"],
            "structural_econ": ["calibration table", "parameter values", "model equation"],
            "theoretical": ["theorem", "proof", "lemma", "notation"],
        },
    ),

    # -------- 跨学科/协作习惯 --------
    CognitiveHabit(
        id="perspective_split",
        name="跨学科视角分裂",
        phases=["DEEP_REVIEW"],
        priority=79,
        content=(
            "**跨学科视角分裂**：面对跨学科论文，你对某些学科的判断置信度天然低于核心专长。"
            "当你对某个方法论 claim 只能做表面判断（能看出写了什么，但不确定该领域的实际约束力）时，"
            "用 spawn_perspective 请该领域的独立专家审视。"
            "判断标准：如果你的质疑停留在'理解'层而无法到达'质疑'层（回忆三层区分），就该 spawn。"
        ),
        short_content="对非专长领域无法达到'质疑层'时，spawn 该领域专家",
    ),
    CognitiveHabit(
        id="proactive_reflection",
        name="主动反思",
        phases=["DEEP_REVIEW", "SYNTHESIS"],
        priority=72,
        content=(
            "**主动反思**：连续读了 2-3 个 section 后，用 reflect_and_plan 抬头看全局——"
            "'我到目前为止发现了什么？方向对吗？接下来该看哪里？'"
            "节奏是：行动-行动-反思-行动-行动-反思。"
        ),
        short_content="每 2-3 section 后反思全局，行动-行动-反思节奏",
    ),
    CognitiveHabit(
        id="reviewer_report",
        name="结构化呈现",
        phases=["SYNTHESIS", "COMPLETION"],
        priority=85,
        content=(
            "**结构化呈现**：向用户呈现最终结论时用审稿报告格式——Overall Assessment + "
            "Major Issues + Minor Issues + Strengths + Questions for Authors。"
            "中间讨论仍是自由的，只在最终结论时用此格式。"
        ),
        short_content="最终输出用审稿报告格式：Overall+Major+Minor+Strengths",
    ),
    CognitiveHabit(
        id="action_over_suggestion",
        name="行动优于建议",
        phases=["EDITING"],
        priority=95,
        content=(
            "**行动优于建议**：当用户说'帮我改一下'时，默认反应是用 edit_section 动手改，"
            "而不是写'建议你可以这样改...'。用文字描述'怎么改'是助手行为；"
            "直接改好并解释'为什么这样改'是专家行为。你是后者。"
        ),
        short_content="直接动手改+解释为什么，不写'建议你可以...'",
    ),
    CognitiveHabit(
        id="re_audit_independence",
        name="复审独立性",
        phases=["EDITING"],
        priority=80,
        content=(
            "**复审独立性**：修改内容后，你知道自己有'编辑者偏见'。对 major 修改，"
            "有意识地从 fresh reader 角度重新审视，或用 spawn_perspective 请独立视角来审。"
        ),
        short_content="修改后以 fresh reader 视角复审，克服编辑者偏见",
    ),
]


# ============================================================
# HabitSelector: 按阶段/情境选取习惯
# ============================================================

class HabitSelector:
    """
    认知习惯选择器。

    根据当前阶段、轮次、情境，从习惯库中选出最相关的 N 条。

    用法:
        selector = HabitSelector(habits=COGNITIVE_HABITS, max_per_turn=5)
        text = selector.select_and_format(phase="DEEP_REVIEW", turn=3)
    """

    DEFAULT_MAX_PER_TURN = 5

    def __init__(
        self,
        habits: list[CognitiveHabit] | None = None,
        max_per_turn: int = DEFAULT_MAX_PER_TURN,
    ) -> None:
        self.habits = habits if habits is not None else COGNITIVE_HABITS
        self._learned_habits: list[CognitiveHabit] = []  # P2: 学习到的习惯
        self.max_per_turn = max_per_turn

    def extend_with_learned(self, learned: list[CognitiveHabit]) -> None:
        """
        P2: 扩展选择器的候选习惯池（学习到的习惯）。

        学习习惯作为低优先级候选，与硬编码习惯竞争同一个 max_per_turn 配额。
        这保证了总注入量不变（≤5 条/轮），但候选池更大、更相关。
        """
        self._learned_habits = learned

    def select(
        self,
        phase: str = "",
        turn: int = 0,
        triggers: list[str] | None = None,
        paper_type: str | None = None,
    ) -> list[CognitiveHabit]:
        """
        选取当前轮次应注入的习惯。

        选取逻辑:
        1. 筛选: 只保留适用于当前阶段的习惯
        2. 排序: 按 priority 降序
        3. 触发加权: 通用触发词匹配 +20；学科特异触发词匹配 +25
        4. 截断: 最多 max_per_turn 条

        Args:
            phase: 当前阶段名 (e.g. "DEEP_REVIEW")
            turn: 当前轮次
            triggers: 当前情境的触发词列表
            paper_type: 论文类型 (e.g. "empirical_econ", "ml_experiment")

        Returns:
            选中的习惯列表（已按优先级排序）
        """
        if not phase:
            # 无阶段信息时，选全局优先级最高的
            candidates = sorted(self.habits, key=lambda h: h.priority, reverse=True)
            return candidates[:self.max_per_turn]

        # 1. 筛选适用于当前阶段的习惯（合并硬编码 + 学习习惯）
        phase_upper = phase.upper()
        all_habits = list(self.habits) + self._learned_habits
        candidates = [h for h in all_habits if phase_upper in h.phases]

        if not candidates:
            # fallback: 用全量库（含学习习惯）
            candidates = list(all_habits)

        # 2. 触发加权
        if triggers or paper_type:
            trigger_set = set(t.lower() for t in (triggers or []))
            scored = []
            for h in candidates:
                bonus = 0
                # 通用触发词匹配 → +20
                if trigger_set and any(t.lower() in trigger_set for t in h.triggers):
                    bonus = 20
                # 学科特异触发词匹配 → +25（覆盖通用 bonus，取较大值）
                if paper_type and paper_type in h.discipline_triggers:
                    disc_triggers = h.discipline_triggers[paper_type]
                    if trigger_set and any(t.lower() in trigger_set for t in disc_triggers):
                        bonus = max(bonus, 25)
                scored.append((h.priority + bonus, h))
            scored.sort(key=lambda x: x[0], reverse=True)
            candidates = [h for _, h in scored]
        else:
            candidates.sort(key=lambda h: h.priority, reverse=True)

        # 3. 截断
        return candidates[:self.max_per_turn]

    def select_and_format(
        self,
        phase: str = "",
        turn: int = 0,
        triggers: list[str] | None = None,
        paper_type: str | None = None,
    ) -> str:
        """
        选取并格式化为可注入 system prompt 的文本。

        Returns:
            格式化的习惯文本（Markdown 格式），或空字符串（无习惯可选时）
        """
        selected = self.select(phase=phase, turn=turn, triggers=triggers, paper_type=paper_type)
        if not selected:
            return ""

        lines = ["## 当前阶段的认知习惯", ""]
        for h in selected:
            lines.append(f"- {h.content}")
            lines.append("")

        return "\n".join(lines).rstrip()

    def select_and_format_progressive(
        self,
        phase: str = "",
        turn: int = 0,
        triggers: list[str] | None = None,
        paper_type: str | None = None,
        full_threshold: int = 2,
        name_only_threshold: int = 5,
    ) -> str:
        """
        渐进式习惯注入：前 N 轮完整学习，中间轮次摘要复习，后期仅名称激活。

        Progressive Loading 设计目标（递减策略）：
        - 阶段早期（turn < full_threshold）：注入完整 content（~80-150 tokens/条）— Agent 学习
        - 中间轮次（full_threshold <= turn < name_only_threshold）：注入 short_content（~15-20 tokens/条）— 复习
        - 后期轮次（turn >= name_only_threshold）：仅注入 name（~5 tokens/条）— 激活效应
        - 总效果：15 轮 DEEP_REVIEW 从全量 ~11250 tokens 降至 ~3750 tokens，节省 ~67%

        Args:
            phase: 当前阶段名
            turn: 当前轮次（0-indexed，通常是阶段内轮次）
            triggers: 情境触发词
            paper_type: 论文类型
            full_threshold: 前几轮注入完整版（默认 2）
            name_only_threshold: 从第几轮起只注入名称（默认 5）

        Returns:
            格式化的习惯文本
        """
        selected = self.select(phase=phase, turn=turn, triggers=triggers, paper_type=paper_type)
        if not selected:
            return ""

        if turn < full_threshold:
            # 早期：完整版（Agent 学习阶段认知习惯的具体含义）
            lines = ["## 当前阶段的认知习惯", ""]
            for h in selected:
                lines.append(f"- {h.content}")
                lines.append("")
        elif turn < name_only_threshold:
            # 中间：摘要版（Agent 已学习，精简提示足以激活）
            lines = ["## 认知习惯（摘要）", ""]
            for h in selected:
                short = h.short_content or h.name
                lines.append(f"- **{h.name}**：{short}")
                lines.append("")
        else:
            # 后期：仅名称（激活效应，极低 token 开销）
            lines = ["## 认知习惯（激活）", ""]
            for h in selected:
                lines.append(f"- [{h.name}]")
                lines.append("")

        return "\n".join(lines).rstrip()

    def get_habit_by_id(self, habit_id: str) -> CognitiveHabit | None:
        """按 ID 查找习惯。"""
        for h in self.habits:
            if h.id == habit_id:
                return h
        return None

    # ===== V3 Phase 3: Combination effectiveness tracking =====

    def record_combination_effectiveness(
        self, active_habit_ids: list[str], section_findings_density: float
    ) -> None:
        """Track combination effectiveness for future analysis.

        Called per-section by session_finalizer.
        P2-fix11: Data stored in _combination_log (session buffer) and flushed
        to MemoryState.combination_log at session end via flush_combination_log().
        """
        if not hasattr(self, "_combination_log"):
            self._combination_log: list[dict] = []
        self._combination_log.append({
            "combination": sorted(active_habit_ids),
            "density": section_findings_density,
        })

    def flush_combination_log(self, memory_store) -> int:
        """P2-fix11: Persist session's combination log to MemoryState.

        Called at session end. Appends buffered entries to memory_store and
        trims to a sliding window of 200 entries (most recent kept).

        Returns:
            Number of entries flushed.
        """
        entries = getattr(self, "_combination_log", [])
        if not entries:
            return 0
        memory_store.state.combination_log.extend(entries)
        # Sliding window: keep last 200 entries
        if len(memory_store.state.combination_log) > 200:
            memory_store.state.combination_log = memory_store.state.combination_log[-200:]
        flushed = len(entries)
        self._combination_log = []
        return flushed

    def get_combination_insights(self, memory_store=None) -> list[dict]:
        """Analyze: do certain combinations outperform individual habits?

        Returns insights only for combinations observed >= 3 times.
        P2-fix11: Reads from both in-memory buffer and persisted MemoryState.
        """
        from collections import defaultdict
        combo_stats: dict[tuple, list[float]] = defaultdict(list)
        # Include persisted cross-session data
        if memory_store is not None:
            for entry in memory_store.state.combination_log:
                key = tuple(entry["combination"])
                combo_stats[key].append(entry["density"])
        # Include current session buffer
        for entry in getattr(self, "_combination_log", []):
            key = tuple(entry["combination"])
            combo_stats[key].append(entry["density"])
        return [
            {"combination": list(k), "avg_density": sum(v) / len(v), "n": len(v)}
            for k, v in combo_stats.items()
            if len(v) >= 3
        ]
