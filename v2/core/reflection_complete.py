"""
core/reflection_complete.py — Phase 6 Complete Layer: 反思系统完善版

四大功能模块:
    1. AdaptiveReflectionDepth — 反思深度自适应
       简单论文只做轻量反思，复杂/有争议的论文触发深度反思。
       复杂度评估基于: 方法论难度、跨领域性、数据规模、争议信号。

    2. ComparativeReflector — 对比反思
       将当前审稿与历史最佳审稿对比，发现差距。
       对比维度: 覆盖率、深度、证据质量、发现多样性。

    3. ReflectionQualityVerifier — 反思质量验证
       避免"虚假反思"（LLM 说"我做得很好"但实际不好）。
       通过具体 evidence 验证反思结论，对抗自我感觉良好偏差。

    4. ReflectionSkillSynthesisTrigger — 反思触发的 Skill 合成
       当反思发现某类问题反复出现时，产出合成信号。
       为 Phase 4 (SkillTTA) 提供输入——目前通过 Protocol 定义接口。

统一编排:
    ReflectionCompleteOrchestrator 提供:
        - on_phase_end(): Phase 结束时协调自适应深度 + 质量验证
        - on_session_end(): Session 结束时协调对比反思 + 合成触发
        - get_reflection_report(): 导出完整反思报告

设计原则:
    - Kill Switch: 所有功能通过环境变量控制 (默认 ON)
    - 与 reflection_engine.py 松耦合: 增强而非替代
    - 渐进退化: 每个功能独立可关闭
    - 零外部依赖: 纯 Python 标准库 + typing
    - 序列化/反序列化: 所有状态可持久化
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ================================================================
# Kill Switches (默认 ON)
# ================================================================

def _env_enabled(key: str, default: bool = True) -> bool:
    """读取环境变量控制开关。'0'/'false'/'off' 为关闭。"""
    val = os.environ.get(key, "")
    if not val:
        return default
    return val.lower() not in ("0", "false", "off", "no", "disabled")


ADAPTIVE_DEPTH_ENABLED = _env_enabled("SCHOLAR_GODEL_REFLECTION_ADAPTIVE_DEPTH", True)
COMPARATIVE_REFLECTION_ENABLED = _env_enabled("SCHOLAR_GODEL_REFLECTION_COMPARATIVE", True)
REFLECTION_QUALITY_VERIFY_ENABLED = _env_enabled("SCHOLAR_GODEL_REFLECTION_QUALITY_VERIFY", True)
REFLECTION_SKILL_SYNTHESIS_ENABLED = _env_enabled("SCHOLAR_GODEL_REFLECTION_SKILL_SYNTHESIS", True)


# ================================================================
# Module 1: AdaptiveReflectionDepth — 反思深度自适应
# ================================================================

class ReflectionDepthLevel(Enum):
    """反思深度等级"""
    MINIMAL = "minimal"       # 极简: 仅统计指标检查，无 LLM 调用
    STANDARD = "standard"     # 标准: 规则评估 + 短 LLM 反思 (≤500 tokens)
    DEEP = "deep"             # 深度: 全面 LLM 反思 (≤1500 tokens) + evidence 交叉验证
    INTENSIVE = "intensive"   # 密集: 多轮 LLM 反思 + 外部信号 + 对比验证


@dataclass
class ComplexitySignals:
    """论文复杂度信号集合"""
    methodology_complexity: float = 0.0    # 方法论复杂度 (0-1)
    cross_disciplinary: float = 0.0        # 跨领域程度 (0-1)
    data_scale_complexity: float = 0.0     # 数据规模复杂度 (0-1)
    controversy_signals: float = 0.0       # 争议信号强度 (0-1)
    novelty_score: float = 0.0             # 新颖度 (0-1)
    length_factor: float = 0.0            # 论文长度因子 (sections 数量归一化)
    prior_failure_rate: float = 0.0        # 历史同类论文的审稿失败率

    @property
    def overall_complexity(self) -> float:
        """加权综合复杂度分数"""
        weights = {
            "methodology_complexity": 0.30,
            "cross_disciplinary": 0.15,
            "data_scale_complexity": 0.15,
            "controversy_signals": 0.20,
            "novelty_score": 0.10,
            "length_factor": 0.05,
            "prior_failure_rate": 0.05,
        }
        total = sum(
            getattr(self, attr) * w for attr, w in weights.items()
        )
        return min(1.0, max(0.0, total))


class ComplexityAssessor:
    """论文复杂度评估器。

    基于论文的结构特征、内容信号和历史数据评估审稿复杂度。
    不调用 LLM——纯规则推断。
    """

    # 方法论复杂度信号
    COMPLEX_METHODOLOGIES = {
        "did", "difference-in-differences", "rdd", "regression discontinuity",
        "iv", "instrumental variable", "bunching", "synthetic control",
        "bartik", "shift-share", "gmm", "structural estimation",
        "bayesian", "machine learning", "deep learning", "neural network",
        "meta-analysis", "spatial econometrics",
    }

    SIMPLE_METHODOLOGIES = {
        "ols", "descriptive", "correlation", "survey", "case study",
        "literature review", "qualitative",
    }

    def assess(
        self,
        sections_count: int,
        findings_so_far: list[dict],
        tool_call_history: list[dict],
        paper_metadata: dict | None = None,
    ) -> ComplexitySignals:
        """评估论文复杂度。

        Args:
            sections_count: 论文 section 数量
            findings_so_far: 目前为止产出的 findings
            tool_call_history: 工具调用历史
            paper_metadata: 论文元数据 (title, abstract, keywords, fields 等)

        Returns:
            ComplexitySignals 各维度评分
        """
        meta = paper_metadata or {}

        # 1. 方法论复杂度
        methodology_complexity = self._assess_methodology(meta, findings_so_far)

        # 2. 跨领域程度
        cross_disciplinary = self._assess_cross_disciplinary(meta)

        # 3. 数据规模复杂度
        data_scale = self._assess_data_scale(findings_so_far, meta)

        # 4. 争议信号
        controversy = self._assess_controversy(findings_so_far, tool_call_history)

        # 5. 新颖度
        novelty = self._assess_novelty(meta)

        # 6. 长度因子
        length_factor = min(1.0, sections_count / 15.0)

        # 7. 历史失败率（如果有）
        prior_failure = meta.get("prior_failure_rate", 0.0)

        return ComplexitySignals(
            methodology_complexity=methodology_complexity,
            cross_disciplinary=cross_disciplinary,
            data_scale_complexity=data_scale,
            controversy_signals=controversy,
            novelty_score=novelty,
            length_factor=length_factor,
            prior_failure_rate=prior_failure,
        )

    def _assess_methodology(self, meta: dict, findings: list[dict]) -> float:
        """评估方法论复杂度"""
        # 从 metadata 的 abstract/keywords 中检测
        text = (
            meta.get("abstract", "").lower() + " "
            + " ".join(meta.get("keywords", [])).lower() + " "
            + meta.get("methodology", "").lower()
        )

        complex_hits = sum(1 for m in self.COMPLEX_METHODOLOGIES if m in text)
        simple_hits = sum(1 for m in self.SIMPLE_METHODOLOGIES if m in text)

        # 多方法组合更复杂
        if complex_hits >= 2:
            return min(1.0, 0.6 + complex_hits * 0.15)
        elif complex_hits == 1:
            return 0.5
        elif simple_hits > 0:
            return 0.2
        else:
            # 从 findings 中推断
            method_findings = [
                f for f in findings
                if f.get("category", "") in ("methodology", "statistics", "identification")
            ]
            return min(0.7, len(method_findings) * 0.15)

    def _assess_cross_disciplinary(self, meta: dict) -> float:
        """评估跨领域程度"""
        fields = meta.get("fields", [])
        if not fields:
            # 从 keywords 推断
            keywords = meta.get("keywords", [])
            # 简单启发式：关键词分布跨越多个领域
            if len(keywords) > 8:
                return 0.5
            return 0.2
        return min(1.0, (len(fields) - 1) * 0.3)

    def _assess_data_scale(self, findings: list[dict], meta: dict) -> float:
        """评估数据规模复杂度"""
        # 从 findings 中检测多数据源、面板数据等信号
        data_signals = sum(
            1 for f in findings
            if any(kw in f.get("finding", "").lower()
                   for kw in ("panel", "longitudinal", "multi-source", "big data",
                              "administrative", "satellite", "web scraping"))
        )
        return min(1.0, data_signals * 0.25 + meta.get("data_complexity", 0.0))

    def _assess_controversy(self, findings: list[dict], tool_calls: list[dict]) -> float:
        """评估争议信号"""
        # 争议信号: findings 中有高优先级的方法论质疑
        high_priority_method_issues = sum(
            1 for f in findings
            if f.get("priority") == "high"
            and f.get("category") in ("methodology", "identification", "robustness")
        )
        # 工具调用中反复搜索文献（可能在验证争议性发现）
        search_calls = sum(
            1 for t in tool_calls if "search" in t.get("name", "").lower()
        )
        controversy = min(1.0, high_priority_method_issues * 0.3 + search_calls * 0.02)
        return controversy

    def _assess_novelty(self, meta: dict) -> float:
        """评估新颖度"""
        # 从 metadata 中获取
        novelty = meta.get("novelty_score", 0.0)
        if not novelty:
            # 简单启发式: 如果标题/摘要中有 "new", "novel", "first" 等
            text = (meta.get("title", "") + " " + meta.get("abstract", "")).lower()
            novel_terms = ("novel", "new approach", "first", "innovative", "pioneering")
            hits = sum(1 for t in novel_terms if t in text)
            novelty = min(0.8, hits * 0.2)
        return novelty


class AdaptiveReflectionDepth:
    """反思深度自适应控制器。

    根据论文复杂度和审稿进展动态决定反思深度。

    决策规则:
        complexity < 0.25 → MINIMAL (无 LLM 调用，纯统计)
        0.25 ≤ complexity < 0.50 → STANDARD (轻量 LLM)
        0.50 ≤ complexity < 0.75 → DEEP (完整 LLM + evidence)
        complexity ≥ 0.75 → INTENSIVE (多轮 + 对比)

    附加规则:
        - 如果当前 phase 的 micro_anomaly_rate > 0.5 → 升一级
        - 如果已经多次 phase_reflect 建议回退 → 升至 INTENSIVE
        - 如果 token budget 紧张 (> 80% capacity) → 降一级

    设计: 自身不做反思，只做"应该用什么深度"的决策。
    """

    # 复杂度阈值
    THRESHOLDS = {
        ReflectionDepthLevel.MINIMAL: (0.0, 0.25),
        ReflectionDepthLevel.STANDARD: (0.25, 0.50),
        ReflectionDepthLevel.DEEP: (0.50, 0.75),
        ReflectionDepthLevel.INTENSIVE: (0.75, 1.01),
    }

    def __init__(self, assessor: ComplexityAssessor | None = None):
        self._assessor = assessor or ComplexityAssessor()
        self._override_level: ReflectionDepthLevel | None = None
        self._decision_history: list[dict] = []

    def decide_depth(
        self,
        sections_count: int,
        findings: list[dict],
        tool_call_history: list[dict],
        paper_metadata: dict | None = None,
        micro_anomaly_rate: float = 0.0,
        revisit_count: int = 0,
        capacity_pct: float = 0.0,
    ) -> ReflectionDepthLevel:
        """决定本次反思应使用的深度等级。

        Args:
            sections_count: 论文 section 数量
            findings: 当前积累的 findings
            tool_call_history: 工具调用历史
            paper_metadata: 论文元数据
            micro_anomaly_rate: 最近 micro reflection 的异常率
            revisit_count: 本次 session 中建议回退的次数
            capacity_pct: context window 已用百分比

        Returns:
            建议的反思深度等级
        """
        if not ADAPTIVE_DEPTH_ENABLED:
            return ReflectionDepthLevel.STANDARD

        # 如果有手动覆盖
        if self._override_level is not None:
            return self._override_level

        # 评估复杂度
        signals = self._assessor.assess(
            sections_count, findings, tool_call_history, paper_metadata
        )
        complexity = signals.overall_complexity

        # 基础决策
        base_level = self._complexity_to_level(complexity)

        # 附加调整
        adjusted = self._apply_adjustments(
            base_level, micro_anomaly_rate, revisit_count, capacity_pct
        )

        # 记录决策
        self._decision_history.append({
            "complexity": complexity,
            "base_level": base_level.value,
            "adjusted_level": adjusted.value,
            "signals": {
                "methodology": signals.methodology_complexity,
                "controversy": signals.controversy_signals,
                "cross_disciplinary": signals.cross_disciplinary,
            },
            "adjustments": {
                "micro_anomaly_rate": micro_anomaly_rate,
                "revisit_count": revisit_count,
                "capacity_pct": capacity_pct,
            },
            "timestamp": time.time(),
        })

        return adjusted

    def override_depth(self, level: ReflectionDepthLevel | None) -> None:
        """手动覆盖反思深度（用于测试或特殊场景）。"""
        self._override_level = level

    def get_decision_history(self) -> list[dict]:
        """获取决策历史（深拷贝，防止外部修改内部状态）。"""
        return copy.deepcopy(self._decision_history)

    def get_token_budget_for_depth(self, level: ReflectionDepthLevel) -> int:
        """返回不同深度等级对应的 LLM token budget。"""
        budgets = {
            ReflectionDepthLevel.MINIMAL: 0,
            ReflectionDepthLevel.STANDARD: 500,
            ReflectionDepthLevel.DEEP: 1500,
            ReflectionDepthLevel.INTENSIVE: 3000,
        }
        return budgets.get(level, 500)

    def _complexity_to_level(self, complexity: float) -> ReflectionDepthLevel:
        """复杂度分数映射到深度等级。"""
        for level, (low, high) in self.THRESHOLDS.items():
            if low <= complexity < high:
                return level
        return ReflectionDepthLevel.DEEP  # fallback

    def _apply_adjustments(
        self,
        base: ReflectionDepthLevel,
        anomaly_rate: float,
        revisit_count: int,
        capacity_pct: float,
    ) -> ReflectionDepthLevel:
        """应用附加调整规则。"""
        levels = list(ReflectionDepthLevel)
        idx = levels.index(base)

        # 升级条件
        if anomaly_rate > 0.5 and idx < len(levels) - 1:
            idx += 1
            logger.debug("Reflection depth upgraded due to high anomaly rate (%.2f)", anomaly_rate)

        if revisit_count >= 2 and idx < len(levels) - 1:
            idx = len(levels) - 1  # 直接升至 INTENSIVE
            logger.debug("Reflection depth set to INTENSIVE due to %d revisits", revisit_count)

        # 降级条件
        if capacity_pct > 0.80 and idx > 0:
            idx -= 1
            logger.debug("Reflection depth downgraded due to token pressure (%.1f%%)", capacity_pct * 100)

        return levels[idx]

    def serialize(self) -> dict:
        """序列化状态。"""
        return {
            "decision_history": self._decision_history[-50:],  # 保留最近 50 条
            "override_level": self._override_level.value if self._override_level else None,
        }

    @classmethod
    def deserialize(cls, data: dict) -> "AdaptiveReflectionDepth":
        """反序列化。"""
        instance = cls()
        instance._decision_history = data.get("decision_history", [])
        override = data.get("override_level")
        if override:
            instance._override_level = ReflectionDepthLevel(override)
        return instance


# ================================================================
# Module 2: ComparativeReflector — 对比反思
# ================================================================

@dataclass
class ReviewSnapshot:
    """一次审稿的快照（用于历史对比）。"""
    session_id: str
    paper_type: str = ""
    paper_methodology: str = ""
    total_findings: int = 0
    high_priority_findings: int = 0
    findings_categories: dict[str, int] = field(default_factory=dict)
    sections_read: int = 0
    coverage_score: float = 0.0
    depth_score: float = 0.0
    evidence_quality: float = 0.0
    efficiency: float = 0.0        # findings / turns
    loop_turns: int = 0
    total_tokens: int = 0
    timestamp: float = 0.0
    quality_label: str = ""        # "good" | "excellent" | "poor" | ""
    verified_ratio: float = 0.0

    @property
    def composite_score(self) -> float:
        """综合质量分数 (0-10)。"""
        return (
            self.coverage_score * 2.5
            + self.depth_score * 3.0
            + self.evidence_quality * 2.5
            + min(1.0, self.efficiency * 5) * 2.0
        )


@dataclass
class ComparisonGap:
    """对比发现的差距。"""
    dimension: str          # 对比维度
    current_value: float    # 当前值
    reference_value: float  # 参考值
    gap_severity: float     # 差距严重程度 (0-1)
    suggestion: str         # 改进建议

    @property
    def gap_ratio(self) -> float:
        """差距比例 (负值表示当前优于参考)。"""
        if self.reference_value == 0:
            return 0.0
        return (self.reference_value - self.current_value) / self.reference_value


@dataclass
class ComparisonResult:
    """对比反思的结果。"""
    reference_snapshot: ReviewSnapshot | None
    gaps: list[ComparisonGap] = field(default_factory=list)
    strengths_vs_reference: list[str] = field(default_factory=list)
    overall_gap_score: float = 0.0    # 综合差距分数 (0 = 与参考一致, 1 = 差距极大)
    actionable_improvements: list[str] = field(default_factory=list)

    @property
    def has_significant_gaps(self) -> bool:
        """是否存在显著差距。"""
        return self.overall_gap_score > 0.3


class ComparativeReflector:
    """对比反思器: 将当前审稿与历史最佳审稿对比。

    维护一个"参考审稿库"(reference reviews)，存储历史最佳表现。
    在审稿结束时对比当前表现与最佳表现，发现差距并产出改进建议。

    对比维度:
        - 覆盖率: sections_read 比例
        - 深度: findings 数量和质量
        - 证据质量: verified_ratio
        - 发现多样性: categories 分布
        - 效率: findings/turns
    """

    MAX_REFERENCES = 50  # 最多存储的参考快照数

    def __init__(self):
        self._reference_snapshots: list[ReviewSnapshot] = []

    def add_reference(self, snapshot: ReviewSnapshot) -> None:
        """添加一个参考审稿快照。

        自动淘汰最差的快照以保持容量。
        """
        self._reference_snapshots.append(snapshot)
        # 容量管理: 按 composite_score 排序，保留最好的
        if len(self._reference_snapshots) > self.MAX_REFERENCES:
            self._reference_snapshots.sort(key=lambda s: s.composite_score, reverse=True)
            self._reference_snapshots = self._reference_snapshots[:self.MAX_REFERENCES]

    def compare(
        self,
        current_findings: list[dict],
        current_sections_read: list[str],
        current_tool_calls: list[dict],
        current_loop_turns: int,
        current_total_tokens: int,
        paper_type: str = "",
        paper_methodology: str = "",
    ) -> ComparisonResult:
        """将当前审稿与最佳参考对比。

        选择参考策略:
            1. 首选同类型 + 同方法论的最佳审稿
            2. 次选同类型的最佳审稿
            3. 最后选全局最佳审稿
        """
        if not COMPARATIVE_REFLECTION_ENABLED:
            return ComparisonResult(reference_snapshot=None)

        # 选择最佳参考
        reference = self._select_best_reference(paper_type, paper_methodology)
        if reference is None:
            return ComparisonResult(reference_snapshot=None)

        # 计算当前快照
        current = self._build_current_snapshot(
            current_findings, current_sections_read,
            current_tool_calls, current_loop_turns, current_total_tokens
        )

        # 对比各维度
        gaps = self._compare_dimensions(current, reference)

        # 识别优势
        strengths = self._identify_strengths(current, reference)

        # 计算综合差距
        overall_gap = self._compute_overall_gap(gaps)

        # 生成改进建议
        improvements = self._generate_improvements(gaps)

        return ComparisonResult(
            reference_snapshot=reference,
            gaps=gaps,
            strengths_vs_reference=strengths,
            overall_gap_score=overall_gap,
            actionable_improvements=improvements,
        )

    def get_reference_count(self) -> int:
        """获取参考库大小。"""
        return len(self._reference_snapshots)

    def get_best_reference(self, paper_type: str = "") -> ReviewSnapshot | None:
        """获取最佳参考快照。"""
        return self._select_best_reference(paper_type, "")

    def _select_best_reference(
        self, paper_type: str, methodology: str
    ) -> ReviewSnapshot | None:
        """选择最匹配的参考审稿。"""
        if not self._reference_snapshots:
            return None

        # 优先: 同类型 + 同方法论
        if paper_type and methodology:
            same_type_method = [
                s for s in self._reference_snapshots
                if s.paper_type == paper_type and s.paper_methodology == methodology
            ]
            if same_type_method:
                return max(same_type_method, key=lambda s: s.composite_score)

        # 次选: 同类型
        if paper_type:
            same_type = [
                s for s in self._reference_snapshots
                if s.paper_type == paper_type
            ]
            if same_type:
                return max(same_type, key=lambda s: s.composite_score)

        # 最后: 全局最佳
        return max(self._reference_snapshots, key=lambda s: s.composite_score)

    def _build_current_snapshot(
        self,
        findings: list[dict],
        sections_read: list[str],
        tool_calls: list[dict],
        loop_turns: int,
        total_tokens: int,
    ) -> ReviewSnapshot:
        """从当前审稿构建快照。"""
        # findings 分类统计
        categories: dict[str, int] = {}
        high_priority = 0
        verified = 0
        for f in findings:
            cat = f.get("category", "other")
            categories[cat] = categories.get(cat, 0) + 1
            if f.get("priority") == "high":
                high_priority += 1
            if f.get("status") == "verified":
                verified += 1

        verified_ratio = verified / max(1, len(findings))
        efficiency = len(findings) / max(1, loop_turns)

        # 分数估算 (与 PhaseReflector 一致的逻辑)
        coverage_score = min(1.0, len(sections_read) / 10.0)
        depth_score = min(1.0, len(findings) / 5.0)
        evidence_quality = verified_ratio

        return ReviewSnapshot(
            session_id=f"current_{int(time.time())}",
            total_findings=len(findings),
            high_priority_findings=high_priority,
            findings_categories=categories,
            sections_read=len(sections_read),
            coverage_score=coverage_score,
            depth_score=depth_score,
            evidence_quality=evidence_quality,
            efficiency=efficiency,
            loop_turns=loop_turns,
            total_tokens=total_tokens,
            verified_ratio=verified_ratio,
            timestamp=time.time(),
        )

    def _compare_dimensions(
        self, current: ReviewSnapshot, reference: ReviewSnapshot
    ) -> list[ComparisonGap]:
        """对比各维度。"""
        gaps = []

        dimensions = [
            ("coverage", current.coverage_score, reference.coverage_score,
             "扩大阅读范围，确保覆盖论文的所有关键 sections"),
            ("depth", current.depth_score, reference.depth_score,
             "深入分析，产出更多有实质价值的 findings"),
            ("evidence_quality", current.evidence_quality, reference.evidence_quality,
             "加强证据验证，提高 findings 的 verified 比例"),
            ("efficiency", current.efficiency, reference.efficiency,
             "优化审稿策略，减少无效操作轮次"),
            ("findings_diversity",
             len(current.findings_categories) / max(1, current.total_findings),
             len(reference.findings_categories) / max(1, reference.total_findings),
             "拓宽分析维度，覆盖更多 findings 类别"),
        ]

        for name, curr_val, ref_val, suggestion in dimensions:
            if ref_val > 0 and curr_val < ref_val:
                gap_severity = (ref_val - curr_val) / ref_val
                if gap_severity > 0.1:  # 只报告超过 10% 的差距
                    gaps.append(ComparisonGap(
                        dimension=name,
                        current_value=curr_val,
                        reference_value=ref_val,
                        gap_severity=min(1.0, gap_severity),
                        suggestion=suggestion,
                    ))

        return gaps

    def _identify_strengths(
        self, current: ReviewSnapshot, reference: ReviewSnapshot
    ) -> list[str]:
        """识别当前审稿相对参考的优势。"""
        strengths = []
        if current.coverage_score > reference.coverage_score * 1.1:
            strengths.append("覆盖率超越历史最佳")
        if current.evidence_quality > reference.evidence_quality * 1.1:
            strengths.append("证据质量超越历史最佳")
        if current.efficiency > reference.efficiency * 1.2:
            strengths.append("审稿效率显著优于历史最佳")
        if current.high_priority_findings > reference.high_priority_findings:
            strengths.append("发现了更多高优先级问题")
        return strengths

    def _compute_overall_gap(self, gaps: list[ComparisonGap]) -> float:
        """计算综合差距分数。"""
        if not gaps:
            return 0.0
        return sum(g.gap_severity for g in gaps) / len(gaps)

    def _generate_improvements(self, gaps: list[ComparisonGap]) -> list[str]:
        """基于差距生成可操作的改进建议。"""
        # 按严重程度排序，取 top 3
        sorted_gaps = sorted(gaps, key=lambda g: g.gap_severity, reverse=True)
        return [g.suggestion for g in sorted_gaps[:3]]

    def serialize(self) -> dict:
        """序列化。"""
        return {
            "reference_snapshots": [
                {
                    "session_id": s.session_id,
                    "paper_type": s.paper_type,
                    "paper_methodology": s.paper_methodology,
                    "total_findings": s.total_findings,
                    "high_priority_findings": s.high_priority_findings,
                    "findings_categories": s.findings_categories,
                    "sections_read": s.sections_read,
                    "coverage_score": s.coverage_score,
                    "depth_score": s.depth_score,
                    "evidence_quality": s.evidence_quality,
                    "efficiency": s.efficiency,
                    "loop_turns": s.loop_turns,
                    "total_tokens": s.total_tokens,
                    "timestamp": s.timestamp,
                    "quality_label": s.quality_label,
                    "verified_ratio": s.verified_ratio,
                }
                for s in self._reference_snapshots
            ]
        }

    @classmethod
    def deserialize(cls, data: dict) -> "ComparativeReflector":
        """反序列化。"""
        instance = cls()
        for snap_data in data.get("reference_snapshots", []):
            instance._reference_snapshots.append(ReviewSnapshot(
                session_id=snap_data.get("session_id", ""),
                paper_type=snap_data.get("paper_type", ""),
                paper_methodology=snap_data.get("paper_methodology", ""),
                total_findings=snap_data.get("total_findings", 0),
                high_priority_findings=snap_data.get("high_priority_findings", 0),
                findings_categories=snap_data.get("findings_categories", {}),
                sections_read=snap_data.get("sections_read", 0),
                coverage_score=snap_data.get("coverage_score", 0.0),
                depth_score=snap_data.get("depth_score", 0.0),
                evidence_quality=snap_data.get("evidence_quality", 0.0),
                efficiency=snap_data.get("efficiency", 0.0),
                loop_turns=snap_data.get("loop_turns", 0),
                total_tokens=snap_data.get("total_tokens", 0),
                timestamp=snap_data.get("timestamp", 0.0),
                quality_label=snap_data.get("quality_label", ""),
                verified_ratio=snap_data.get("verified_ratio", 0.0),
            ))
        return instance


# ================================================================
# Module 3: ReflectionQualityVerifier — 反思质量验证
# ================================================================

@dataclass
class QualityCheckResult:
    """单条反思结论的质量检查结果。"""
    claim: str                 # 反思声称
    verified: bool             # 是否通过验证
    evidence_for: list[str] = field(default_factory=list)    # 支持证据
    evidence_against: list[str] = field(default_factory=list) # 反对证据
    confidence: float = 0.0    # 验证信心 (0-1)
    correction: str = ""       # 如果不通过，正确结论是什么


@dataclass
class ReflectionVerificationReport:
    """反思质量验证报告。"""
    total_claims: int = 0
    verified_claims: int = 0
    refuted_claims: int = 0
    uncertain_claims: int = 0
    checks: list[QualityCheckResult] = field(default_factory=list)
    overall_reliability: float = 0.0   # 反思结论的整体可靠性 (0-1)
    optimism_bias: float = 0.0         # 乐观偏差检测 (>0 表示过度乐观)

    @property
    def accuracy(self) -> float:
        """验证通过率。"""
        if self.total_claims == 0:
            return 1.0
        return self.verified_claims / self.total_claims


class ReflectionQualityVerifier:
    """反思质量验证器: 验证反思结论是否有事实依据。

    问题: LLM 反思时倾向于:
        1. 自我感觉良好偏差 ("我做得很全面") — 但实际覆盖率很低
        2. 虚假深度声称 ("我的分析很深入") — 但 findings 多为表面性
        3. 遗漏盲点 ("没有什么遗漏的") — 但有整个 section 没读

    解决: 用硬数据验证软结论。

    验证规则:
        - "覆盖率高" → 检查 sections_read / total_sections
        - "分析深入" → 检查 verified_ratio + priority 分布
        - "没有遗漏" → 检查是否有未读的核心 section
        - "效率良好" → 检查 findings/turns 是否达到基线
    """

    # 质量基线（基于历史数据动态调整）
    DEFAULT_BASELINES = {
        "coverage_threshold": 0.6,          # 至少读 60% sections 才算"覆盖全面"
        "depth_min_findings": 3,            # 至少 3 条 findings 才算"有深度"
        "verified_ratio_threshold": 0.4,    # 至少 40% verified 才算"证据充分"
        "efficiency_baseline": 0.3,         # findings/turns ≥ 0.3 才算"效率良好"
        "diversity_min_categories": 3,      # 至少 3 个类别才算"全面"
    }

    # 核心 sections（如果未读则标记为可能遗漏）
    CORE_SECTIONS = {
        "abstract", "introduction", "methodology", "method", "methods",
        "results", "conclusion", "discussion", "data",
    }

    def __init__(self, baselines: dict[str, float] | None = None):
        self._baselines = baselines or dict(self.DEFAULT_BASELINES)
        self._verification_history: list[ReflectionVerificationReport] = []

    def verify_phase_reflection(
        self,
        reflection_claims: dict[str, Any],
        actual_findings: list[dict],
        actual_sections_read: list[str],
        total_sections: int,
        actual_tool_calls: list[dict],
        actual_loop_turns: int,
    ) -> ReflectionVerificationReport:
        """验证 phase reflection 的结论是否有事实依据。

        Args:
            reflection_claims: 反思产出的声称 (coverage_score, depth_score 等)
            actual_findings: 实际产出的 findings
            actual_sections_read: 实际读过的 sections
            total_sections: 论文总 section 数
            actual_tool_calls: 实际工具调用历史
            actual_loop_turns: 实际轮次

        Returns:
            验证报告
        """
        if not REFLECTION_QUALITY_VERIFY_ENABLED:
            return ReflectionVerificationReport(overall_reliability=1.0)

        checks: list[QualityCheckResult] = []

        # 检查覆盖率声称
        claimed_coverage = reflection_claims.get("coverage_score", 0.0)
        checks.append(self._verify_coverage(
            claimed_coverage, actual_sections_read, total_sections
        ))

        # 检查深度声称
        claimed_depth = reflection_claims.get("depth_score", 0.0)
        checks.append(self._verify_depth(claimed_depth, actual_findings))

        # 检查证据质量声称
        claimed_evidence = reflection_claims.get("evidence_quality", 0.0)
        checks.append(self._verify_evidence(claimed_evidence, actual_findings))

        # 检查效率声称 (如果有)
        if "efficiency" in reflection_claims:
            checks.append(self._verify_efficiency(
                reflection_claims["efficiency"], actual_findings, actual_loop_turns
            ))

        # 检查遗漏声称 (如果反思说"没有遗漏")
        if reflection_claims.get("no_gaps", False):
            checks.append(self._verify_no_gaps(actual_sections_read, total_sections))

        # 汇总
        verified = sum(1 for c in checks if c.verified)
        refuted = sum(1 for c in checks if not c.verified and c.confidence > 0.7)
        uncertain = len(checks) - verified - refuted

        # 计算乐观偏差
        optimism_bias = self._compute_optimism_bias(reflection_claims, checks)

        report = ReflectionVerificationReport(
            total_claims=len(checks),
            verified_claims=verified,
            refuted_claims=refuted,
            uncertain_claims=uncertain,
            checks=checks,
            overall_reliability=verified / max(1, len(checks)),
            optimism_bias=optimism_bias,
        )

        self._verification_history.append(report)
        return report

    def verify_global_reflection(
        self,
        global_self_score: float,
        claimed_strengths: list[str],
        claimed_weaknesses: list[str],
        actual_findings: list[dict],
        actual_sections_read: list[str],
        total_sections: int,
        actual_loop_turns: int,
    ) -> ReflectionVerificationReport:
        """验证 global reflection 的自评是否准确。"""
        if not REFLECTION_QUALITY_VERIFY_ENABLED:
            return ReflectionVerificationReport(overall_reliability=1.0)

        checks: list[QualityCheckResult] = []

        # 验证自评分数的合理性
        checks.append(self._verify_self_score(
            global_self_score, actual_findings, actual_sections_read, total_sections
        ))

        # 验证声称的强项
        for strength in claimed_strengths[:3]:
            check = self._verify_strength_claim(
                strength, actual_findings, actual_sections_read, total_sections
            )
            checks.append(check)

        # 验证弱点是否被充分识别
        checks.append(self._verify_weakness_awareness(
            claimed_weaknesses, actual_findings, actual_sections_read, total_sections
        ))

        verified = sum(1 for c in checks if c.verified)
        refuted = sum(1 for c in checks if not c.verified and c.confidence > 0.7)
        uncertain = len(checks) - verified - refuted

        optimism_bias = 0.0
        if global_self_score > 7.0:
            # 高分自评需要更严格的验证
            actual_quality = self._estimate_actual_quality(
                actual_findings, actual_sections_read, total_sections
            )
            optimism_bias = max(0.0, (global_self_score / 10.0) - actual_quality)

        report = ReflectionVerificationReport(
            total_claims=len(checks),
            verified_claims=verified,
            refuted_claims=refuted,
            uncertain_claims=uncertain,
            checks=checks,
            overall_reliability=verified / max(1, len(checks)),
            optimism_bias=optimism_bias,
        )

        self._verification_history.append(report)
        return report

    def get_historical_reliability(self, last_n: int = 10) -> float:
        """获取最近 N 次验证的平均可靠性。"""
        recent = self._verification_history[-last_n:]
        if not recent:
            return 1.0
        return sum(r.overall_reliability for r in recent) / len(recent)

    def get_optimism_trend(self, last_n: int = 10) -> float:
        """获取最近 N 次的乐观偏差趋势。"""
        recent = self._verification_history[-last_n:]
        if not recent:
            return 0.0
        return sum(r.optimism_bias for r in recent) / len(recent)

    # ---- 内部验证方法 ----

    def _verify_coverage(
        self, claimed: float, sections_read: list[str], total_sections: int
    ) -> QualityCheckResult:
        """验证覆盖率声称。"""
        actual = len(sections_read) / max(1, total_sections)
        discrepancy = abs(claimed - actual)

        evidence_for = []
        evidence_against = []

        if discrepancy <= 0.15:
            evidence_for.append(
                f"实际覆盖率 {actual:.2f} 与声称 {claimed:.2f} 偏差在合理范围内"
            )
            verified = True
        else:
            evidence_against.append(
                f"实际覆盖率 {actual:.2f} 与声称 {claimed:.2f} 偏差过大 ({discrepancy:.2f})"
            )
            verified = False

        return QualityCheckResult(
            claim=f"覆盖率为 {claimed:.2f}",
            verified=verified,
            evidence_for=evidence_for,
            evidence_against=evidence_against,
            confidence=min(1.0, 0.5 + discrepancy),
            correction=f"实际覆盖率为 {actual:.2f}" if not verified else "",
        )

    def _verify_depth(self, claimed: float, findings: list[dict]) -> QualityCheckResult:
        """验证深度声称。"""
        # 深度由 findings 数量 + 优先级分布 + verified 占比综合判断
        high_prio = sum(1 for f in findings if f.get("priority") == "high")
        med_prio = sum(1 for f in findings if f.get("priority") == "medium")
        verified = sum(1 for f in findings if f.get("status") == "verified")

        # 实际深度估算
        actual_depth = min(1.0, (
            len(findings) * 0.1
            + high_prio * 0.2
            + med_prio * 0.1
            + (verified / max(1, len(findings))) * 0.3
        ))

        discrepancy = abs(claimed - actual_depth)
        evidence_for = []
        evidence_against = []

        if discrepancy <= 0.2:
            evidence_for.append(
                f"实际深度估算 {actual_depth:.2f} 与声称 {claimed:.2f} 基本一致"
            )
            verified_check = True
        else:
            evidence_against.append(
                f"实际深度估算 {actual_depth:.2f} 与声称 {claimed:.2f} 存在差距"
            )
            if claimed > actual_depth:
                evidence_against.append("可能存在自我评价过高倾向")
            verified_check = False

        return QualityCheckResult(
            claim=f"分析深度为 {claimed:.2f}",
            verified=verified_check,
            evidence_for=evidence_for,
            evidence_against=evidence_against,
            confidence=min(1.0, 0.4 + discrepancy * 1.5),
            correction=f"实际深度约为 {actual_depth:.2f}" if not verified_check else "",
        )

    def _verify_evidence(self, claimed: float, findings: list[dict]) -> QualityCheckResult:
        """验证证据质量声称。"""
        verified_count = sum(1 for f in findings if f.get("status") == "verified")
        actual = verified_count / max(1, len(findings))
        discrepancy = abs(claimed - actual)

        if discrepancy <= 0.15:
            return QualityCheckResult(
                claim=f"证据质量为 {claimed:.2f}",
                verified=True,
                evidence_for=[f"实际 verified 占比 {actual:.2f} 与声称一致"],
                confidence=0.9,
            )
        else:
            return QualityCheckResult(
                claim=f"证据质量为 {claimed:.2f}",
                verified=False,
                evidence_against=[f"实际 verified 占比 {actual:.2f} 与声称 {claimed:.2f} 不符"],
                confidence=min(1.0, 0.5 + discrepancy),
                correction=f"实际证据质量为 {actual:.2f}",
            )

    def _verify_efficiency(
        self, claimed: float, findings: list[dict], loop_turns: int
    ) -> QualityCheckResult:
        """验证效率声称。"""
        actual = len(findings) / max(1, loop_turns)
        discrepancy = abs(claimed - actual)
        verified = discrepancy <= 0.15

        return QualityCheckResult(
            claim=f"效率为 {claimed:.2f}",
            verified=verified,
            evidence_for=[f"实际效率 {actual:.2f}"] if verified else [],
            evidence_against=[f"实际效率 {actual:.2f} 与声称 {claimed:.2f} 不符"] if not verified else [],
            confidence=0.95,  # 效率是硬数据，高信心
            correction=f"实际效率为 {actual:.2f}" if not verified else "",
        )

    def _verify_no_gaps(
        self, sections_read: list[str], total_sections: int
    ) -> QualityCheckResult:
        """验证"没有遗漏"的声称。"""
        read_lower = {s.lower() for s in sections_read}
        missed_core = self.CORE_SECTIONS - read_lower

        if not missed_core:
            return QualityCheckResult(
                claim="没有明显遗漏",
                verified=True,
                evidence_for=["所有核心 sections 均已读取"],
                confidence=0.8,
            )
        else:
            return QualityCheckResult(
                claim="没有明显遗漏",
                verified=False,
                evidence_against=[f"核心 sections 未读: {', '.join(sorted(missed_core))}"],
                confidence=0.9,
                correction=f"遗漏了 {len(missed_core)} 个核心 sections",
            )

    def _verify_self_score(
        self, score: float, findings: list[dict],
        sections_read: list[str], total_sections: int
    ) -> QualityCheckResult:
        """验证全局自评分数。"""
        estimated = self._estimate_actual_quality(findings, sections_read, total_sections) * 10
        discrepancy = abs(score - estimated)

        if discrepancy <= 1.5:
            return QualityCheckResult(
                claim=f"自评分 {score:.1f}/10",
                verified=True,
                evidence_for=[f"估算实际质量约 {estimated:.1f}/10，偏差在合理范围"],
                confidence=0.7,
            )
        else:
            direction = "过高" if score > estimated else "过低"
            return QualityCheckResult(
                claim=f"自评分 {score:.1f}/10",
                verified=False,
                evidence_against=[f"估算实际质量约 {estimated:.1f}/10，自评{direction}"],
                confidence=min(1.0, 0.4 + discrepancy * 0.15),
                correction=f"建议自评约 {estimated:.1f}/10",
            )

    def _verify_strength_claim(
        self, strength: str, findings: list[dict],
        sections_read: list[str], total_sections: int
    ) -> QualityCheckResult:
        """验证声称的强项。"""
        lower = strength.lower()

        # 根据声称内容匹配验证逻辑
        if "覆盖" in lower or "全面" in lower or "coverage" in lower:
            actual_coverage = len(sections_read) / max(1, total_sections)
            verified = actual_coverage >= self._baselines["coverage_threshold"]
            return QualityCheckResult(
                claim=strength,
                verified=verified,
                evidence_for=[f"覆盖率 {actual_coverage:.2f}"] if verified else [],
                evidence_against=[f"覆盖率仅 {actual_coverage:.2f}"] if not verified else [],
                confidence=0.8,
            )
        elif "证据" in lower or "验证" in lower or "evidence" in lower:
            verified_ratio = sum(1 for f in findings if f.get("status") == "verified") / max(1, len(findings))
            verified = verified_ratio >= self._baselines["verified_ratio_threshold"]
            return QualityCheckResult(
                claim=strength,
                verified=verified,
                evidence_for=[f"verified 占比 {verified_ratio:.2f}"] if verified else [],
                evidence_against=[f"verified 占比仅 {verified_ratio:.2f}"] if not verified else [],
                confidence=0.85,
            )
        else:
            # 无法具体验证的声称，给予中等信心
            return QualityCheckResult(
                claim=strength,
                verified=True,
                evidence_for=["无法用硬指标验证，暂时信任"],
                confidence=0.5,
            )

    def _verify_weakness_awareness(
        self, weaknesses: list[str], findings: list[dict],
        sections_read: list[str], total_sections: int
    ) -> QualityCheckResult:
        """验证弱点是否被充分识别（是否有明显弱点未被反思到）。"""
        actual_problems = []

        # 检查覆盖率
        coverage = len(sections_read) / max(1, total_sections)
        if coverage < self._baselines["coverage_threshold"]:
            actual_problems.append("覆盖率不足")

        # 检查 findings 数量
        if len(findings) < self._baselines["depth_min_findings"]:
            actual_problems.append("findings 数量不足")

        # 检查 verified 比例
        verified_ratio = sum(1 for f in findings if f.get("status") == "verified") / max(1, len(findings))
        if verified_ratio < self._baselines["verified_ratio_threshold"]:
            actual_problems.append("证据验证不足")

        # 对比: 实际问题是否都被反思提及
        weak_lower = " ".join(weaknesses).lower()
        missed_problems = [
            p for p in actual_problems
            if not any(keyword in weak_lower for keyword in p.replace("不足", "").split())
        ]

        if not missed_problems:
            return QualityCheckResult(
                claim="弱点识别充分",
                verified=True,
                evidence_for=["所有可检测的弱点均已被反思提及"],
                confidence=0.75,
            )
        else:
            return QualityCheckResult(
                claim="弱点识别充分",
                verified=False,
                evidence_against=[f"未识别的弱点: {', '.join(missed_problems)}"],
                confidence=0.8,
                correction=f"遗漏了 {len(missed_problems)} 个实际弱点",
            )

    def _estimate_actual_quality(
        self, findings: list[dict], sections_read: list[str], total_sections: int
    ) -> float:
        """估算实际审稿质量 (0-1)。"""
        coverage = len(sections_read) / max(1, total_sections)
        depth = min(1.0, len(findings) / 5.0)
        verified = sum(1 for f in findings if f.get("status") == "verified") / max(1, len(findings))
        high_prio = sum(1 for f in findings if f.get("priority") == "high") / max(1, len(findings))

        return (coverage * 0.25 + depth * 0.30 + verified * 0.25 + high_prio * 0.20)

    def _compute_optimism_bias(
        self, claims: dict, checks: list[QualityCheckResult]
    ) -> float:
        """计算乐观偏差: 声称值与实际值的系统性偏离。"""
        overestimates = sum(
            1 for c in checks
            if not c.verified and c.confidence > 0.6
        )
        if not checks:
            return 0.0
        return overestimates / len(checks)

    def serialize(self) -> dict:
        """序列化（保留验证历史的摘要以恢复统计计算能力）。"""
        # 保存每次验证的核心统计，而非完整 checks 对象
        history_summary = [
            {
                "total_claims": r.total_claims,
                "verified_claims": r.verified_claims,
                "refuted_claims": r.refuted_claims,
                "uncertain_claims": r.uncertain_claims,
                "overall_reliability": r.overall_reliability,
                "optimism_bias": r.optimism_bias,
            }
            for r in self._verification_history
        ]
        return {
            "baselines": self._baselines,
            "verification_history": history_summary,
            "verification_history_count": len(self._verification_history),
            "recent_reliability": self.get_historical_reliability(),
            "optimism_trend": self.get_optimism_trend(),
        }

    @classmethod
    def deserialize(cls, data: dict) -> "ReflectionQualityVerifier":
        """反序列化（恢复验证历史统计）。"""
        baselines = data.get("baselines", None)
        instance = cls(baselines=baselines)
        # 恢复验证历史摘要为 ReflectionVerificationReport 对象
        for h in data.get("verification_history", []):
            report = ReflectionVerificationReport(
                total_claims=h.get("total_claims", 0),
                verified_claims=h.get("verified_claims", 0),
                refuted_claims=h.get("refuted_claims", 0),
                uncertain_claims=h.get("uncertain_claims", 0),
                overall_reliability=h.get("overall_reliability", 0.0),
                optimism_bias=h.get("optimism_bias", 0.0),
            )
            instance._verification_history.append(report)
        return instance


# ================================================================
# Module 4: ReflectionSkillSynthesisTrigger — 反思触发 Skill 合成
# ================================================================

@dataclass
class RecurringGapPattern:
    """反复出现的反思差距模式。"""
    gap_type: str                # 差距类型 (coverage/depth/evidence/efficiency/methodology/...)
    description: str             # 描述
    occurrence_count: int = 0    # 出现次数
    first_seen: float = 0.0     # 首次出现时间
    last_seen: float = 0.0      # 最近出现时间
    sessions_involved: list[str] = field(default_factory=list)  # 涉及的 session ids
    severity_trend: list[float] = field(default_factory=list)   # 严重程度趋势

    @property
    def is_persistent(self) -> bool:
        """是否为持续性模式（出现 3 次以上）。"""
        return self.occurrence_count >= 3

    @property
    def average_severity(self) -> float:
        """平均严重程度。"""
        if not self.severity_trend:
            return 0.0
        return sum(self.severity_trend) / len(self.severity_trend)

    @property
    def is_worsening(self) -> bool:
        """是否在恶化（最近 3 次的趋势上升）。"""
        if len(self.severity_trend) < 3:
            return False
        recent = self.severity_trend[-3:]
        return recent[-1] > recent[0] * 1.1


@dataclass
class SynthesisSignal:
    """Skill 合成信号——传递给 Phase 4 (SkillTTA) 的输入。"""
    trigger_reason: str           # 触发原因
    gap_pattern: RecurringGapPattern  # 关联的差距模式
    suggested_skill_type: str     # 建议合成的 Skill 类型
    priority: float = 0.0         # 优先级 (0-1)
    context: dict = field(default_factory=dict)  # 附加上下文

    @property
    def signal_id(self) -> str:
        """信号唯一标识。"""
        content = f"{self.gap_pattern.gap_type}:{self.trigger_reason}"
        return hashlib.md5(content.encode()).hexdigest()[:12]


@runtime_checkable
class SkillSynthesisReceiver(Protocol):
    """Skill 合成信号接收者协议 (Phase 4 实现此接口)。"""

    def receive_synthesis_signal(self, signal: SynthesisSignal) -> bool:
        """接收一个合成信号。返回是否接受处理。"""
        ...


class ReflectionSkillSynthesisTrigger:
    """反思触发的 Skill 合成: 检测反复出现的差距并产出合成信号。

    工作流程:
        1. 每次反思结束后，记录发现的 gaps
        2. 检测同类 gap 是否反复出现 (≥3 次)
        3. 如果持续出现且恶化，产出 SynthesisSignal
        4. 信号推送给 Phase 4 的 SkillTTA (通过 Protocol)

    触发条件 (同时满足):
        - 同类 gap 出现 ≥ RECURRENCE_THRESHOLD 次
        - 平均严重程度 ≥ SEVERITY_THRESHOLD
        - 非恶化趋势时 cooldown 内不重复触发

    Gap 分类策略:
        - 按 dimension 归类 (coverage/depth/evidence/efficiency)
        - 按具体 methodology 细化 (DID/IV/RDD 等)
    """

    RECURRENCE_THRESHOLD = 3
    SEVERITY_THRESHOLD = 0.3
    COOLDOWN_SECONDS = 86400  # 同类信号最小间隔 24h

    # Gap 类型到建议 Skill 类型的映射
    GAP_TO_SKILL_TYPE = {
        "coverage": "systematic_scan_skill",
        "depth": "deep_analysis_skill",
        "evidence": "evidence_verification_skill",
        "efficiency": "strategy_optimization_skill",
        "methodology_did": "did_methodology_checker",
        "methodology_iv": "iv_methodology_checker",
        "methodology_rdd": "rdd_methodology_checker",
        "methodology_general": "general_methodology_skill",
    }

    def __init__(self, receiver: SkillSynthesisReceiver | None = None):
        self._receiver = receiver
        self._gap_patterns: dict[str, RecurringGapPattern] = {}
        self._signal_history: list[SynthesisSignal] = []
        self._last_signal_time: dict[str, float] = {}  # gap_type → last signal timestamp

    def record_gap(
        self,
        gap_type: str,
        description: str,
        severity: float,
        session_id: str = "",
    ) -> SynthesisSignal | None:
        """记录一个反思发现的差距。

        如果满足触发条件，返回 SynthesisSignal (并推送给 receiver)。
        """
        if not REFLECTION_SKILL_SYNTHESIS_ENABLED:
            return None

        now = time.time()

        # 更新或创建模式
        if gap_type not in self._gap_patterns:
            self._gap_patterns[gap_type] = RecurringGapPattern(
                gap_type=gap_type,
                description=description,
                occurrence_count=1,
                first_seen=now,
                last_seen=now,
                sessions_involved=[session_id] if session_id else [],
                severity_trend=[severity],
            )
        else:
            pattern = self._gap_patterns[gap_type]
            pattern.occurrence_count += 1
            pattern.last_seen = now
            pattern.severity_trend.append(severity)
            if session_id and session_id not in pattern.sessions_involved:
                pattern.sessions_involved.append(session_id)
            # 保留最近 20 次 severity
            if len(pattern.severity_trend) > 20:
                pattern.severity_trend = pattern.severity_trend[-20:]

        # 检查是否应该触发
        pattern = self._gap_patterns[gap_type]
        signal = self._check_trigger(pattern)

        if signal:
            self._signal_history.append(signal)
            self._last_signal_time[gap_type] = now
            # 推送给 receiver
            if self._receiver:
                try:
                    self._receiver.receive_synthesis_signal(signal)
                except Exception as e:
                    logger.warning("Failed to push synthesis signal: %s", e)

        return signal

    def get_persistent_patterns(self) -> list[RecurringGapPattern]:
        """获取所有持续性差距模式。"""
        return [p for p in self._gap_patterns.values() if p.is_persistent]

    def get_signal_history(self) -> list[SynthesisSignal]:
        """获取信号历史。"""
        return list(self._signal_history)

    def get_worsening_patterns(self) -> list[RecurringGapPattern]:
        """获取正在恶化的模式。"""
        return [p for p in self._gap_patterns.values() if p.is_worsening]

    def set_receiver(self, receiver: SkillSynthesisReceiver) -> None:
        """设置信号接收者。"""
        self._receiver = receiver

    def _check_trigger(self, pattern: RecurringGapPattern) -> SynthesisSignal | None:
        """检查是否应该触发合成信号。"""
        # 条件 1: 出现次数足够
        if pattern.occurrence_count < self.RECURRENCE_THRESHOLD:
            return None

        # 条件 2: 严重程度足够
        if pattern.average_severity < self.SEVERITY_THRESHOLD:
            return None

        # 条件 3: cooldown 检查
        now = time.time()
        last_signal = self._last_signal_time.get(pattern.gap_type, 0)
        if not pattern.is_worsening and (now - last_signal) < self.COOLDOWN_SECONDS:
            return None

        # 确定建议的 Skill 类型
        skill_type = self.GAP_TO_SKILL_TYPE.get(
            pattern.gap_type,
            self.GAP_TO_SKILL_TYPE.get("methodology_general", "general_improvement_skill")
        )

        # 计算优先级
        priority = min(1.0, (
            pattern.average_severity * 0.4
            + (pattern.occurrence_count / 10.0) * 0.3
            + (1.0 if pattern.is_worsening else 0.0) * 0.3
        ))

        return SynthesisSignal(
            trigger_reason=(
                f"反思差距 '{pattern.gap_type}' 已出现 {pattern.occurrence_count} 次，"
                f"平均严重程度 {pattern.average_severity:.2f}"
                + ("，且呈恶化趋势" if pattern.is_worsening else "")
            ),
            gap_pattern=pattern,
            suggested_skill_type=skill_type,
            priority=priority,
            context={
                "sessions_involved": pattern.sessions_involved[-5:],
                "severity_trend": pattern.severity_trend[-5:],
            },
        )

    def serialize(self) -> dict:
        """序列化。"""
        return {
            "gap_patterns": {
                k: {
                    "gap_type": v.gap_type,
                    "description": v.description,
                    "occurrence_count": v.occurrence_count,
                    "first_seen": v.first_seen,
                    "last_seen": v.last_seen,
                    "sessions_involved": v.sessions_involved[-10:],
                    "severity_trend": v.severity_trend[-20:],
                }
                for k, v in self._gap_patterns.items()
            },
            "signal_history_count": len(self._signal_history),
            "last_signal_times": self._last_signal_time,
        }

    @classmethod
    def deserialize(cls, data: dict, receiver: SkillSynthesisReceiver | None = None) -> "ReflectionSkillSynthesisTrigger":
        """反序列化。"""
        instance = cls(receiver=receiver)
        for k, v in data.get("gap_patterns", {}).items():
            instance._gap_patterns[k] = RecurringGapPattern(
                gap_type=v.get("gap_type", k),
                description=v.get("description", ""),
                occurrence_count=v.get("occurrence_count", 0),
                first_seen=v.get("first_seen", 0.0),
                last_seen=v.get("last_seen", 0.0),
                sessions_involved=v.get("sessions_involved", []),
                severity_trend=v.get("severity_trend", []),
            )
        instance._last_signal_time = data.get("last_signal_times", {})
        return instance


# ================================================================
# Orchestrator: ReflectionCompleteOrchestrator
# ================================================================

@dataclass
class ReflectionCompleteReport:
    """Complete 层反思的完整报告。"""
    depth_level: ReflectionDepthLevel = ReflectionDepthLevel.STANDARD
    comparison: ComparisonResult | None = None
    quality_verification: ReflectionVerificationReport | None = None
    synthesis_signals: list[SynthesisSignal] = field(default_factory=list)
    timestamp: float = 0.0

    @property
    def summary(self) -> dict:
        """简洁摘要。"""
        return {
            "depth": self.depth_level.value,
            "has_comparison": self.comparison is not None and self.comparison.reference_snapshot is not None,
            "comparison_gap": self.comparison.overall_gap_score if self.comparison else 0.0,
            "quality_reliability": self.quality_verification.overall_reliability if self.quality_verification else 1.0,
            "synthesis_signals_count": len(self.synthesis_signals),
        }


class ReflectionCompleteOrchestrator:
    """Phase 6 Complete 层统一编排器。

    协调四个模块在正确的时机工作:
        - on_phase_end(): 自适应深度决策 + 质量验证
        - on_session_end(): 对比反思 + 合成触发 + 快照存储
        - get_reflection_report(): 导出完整报告

    与 ReflectionEngine 的关系:
        - ReflectionEngine 做"反思动作"（micro/phase/global）
        - Orchestrator 做"反思质控"（验证 + 对比 + 自适应 + 触发）
        - 两者协作: Engine 产出结论 → Orchestrator 验证并增强
    """

    def __init__(
        self,
        depth_controller: AdaptiveReflectionDepth | None = None,
        comparator: ComparativeReflector | None = None,
        verifier: ReflectionQualityVerifier | None = None,
        synthesis_trigger: ReflectionSkillSynthesisTrigger | None = None,
    ):
        self.depth_controller = depth_controller or AdaptiveReflectionDepth()
        self.comparator = comparator or ComparativeReflector()
        self.verifier = verifier or ReflectionQualityVerifier()
        self.synthesis_trigger = synthesis_trigger or ReflectionSkillSynthesisTrigger()
        self._reports: list[ReflectionCompleteReport] = []

    def decide_reflection_depth(
        self,
        sections_count: int,
        findings: list[dict],
        tool_call_history: list[dict],
        paper_metadata: dict | None = None,
        micro_anomaly_rate: float = 0.0,
        revisit_count: int = 0,
        capacity_pct: float = 0.0,
    ) -> ReflectionDepthLevel:
        """在反思执行前决定应使用的深度。

        调用时机: ReflectionEngine.phase_reflect() 之前。
        返回值用于配置 PhaseReflector 的行为（是否调用 LLM、token budget 等）。
        """
        return self.depth_controller.decide_depth(
            sections_count=sections_count,
            findings=findings,
            tool_call_history=tool_call_history,
            paper_metadata=paper_metadata,
            micro_anomaly_rate=micro_anomaly_rate,
            revisit_count=revisit_count,
            capacity_pct=capacity_pct,
        )

    def on_phase_end(
        self,
        phase_reflection_claims: dict[str, Any],
        actual_findings: list[dict],
        actual_sections_read: list[str],
        total_sections: int,
        actual_tool_calls: list[dict],
        actual_loop_turns: int,
    ) -> ReflectionVerificationReport:
        """Phase 结束时: 验证反思结论的质量。

        调用时机: ReflectionEngine.phase_reflect() 之后。
        """
        return self.verifier.verify_phase_reflection(
            reflection_claims=phase_reflection_claims,
            actual_findings=actual_findings,
            actual_sections_read=actual_sections_read,
            total_sections=total_sections,
            actual_tool_calls=actual_tool_calls,
            actual_loop_turns=actual_loop_turns,
        )

    def on_session_end(
        self,
        findings: list[dict],
        sections_read: list[str],
        tool_calls: list[dict],
        loop_turns: int,
        total_tokens: int,
        total_sections: int,
        paper_type: str = "",
        paper_methodology: str = "",
        session_id: str = "",
        global_reflection_claims: dict[str, Any] | None = None,
    ) -> ReflectionCompleteReport:
        """Session 结束时: 执行完整 Complete 层反思流程。

        流程:
            1. 对比反思 (与历史最佳比)
            2. 质量验证 (全局反思的可靠性)
            3. 差距模式记录 + 合成信号触发
            4. 存储当前审稿快照 (如果质量好)
        """
        report = ReflectionCompleteReport(timestamp=time.time())

        # 设置深度决策（来自最近一次 decide_reflection_depth 调用）
        if self.depth_controller._decision_history:
            last_decision = self.depth_controller._decision_history[-1]
            report.depth_level = ReflectionDepthLevel(last_decision["adjusted_level"])

        # 1. 对比反思
        comparison = self.comparator.compare(
            current_findings=findings,
            current_sections_read=sections_read,
            current_tool_calls=tool_calls,
            current_loop_turns=loop_turns,
            current_total_tokens=total_tokens,
            paper_type=paper_type,
            paper_methodology=paper_methodology,
        )
        report.comparison = comparison

        # 2. 质量验证 (如果有全局反思声称)
        if global_reflection_claims:
            verification = self.verifier.verify_global_reflection(
                global_self_score=global_reflection_claims.get("self_score", 5.0),
                claimed_strengths=global_reflection_claims.get("strengths", []),
                claimed_weaknesses=global_reflection_claims.get("weaknesses", []),
                actual_findings=findings,
                actual_sections_read=sections_read,
                total_sections=total_sections,
                actual_loop_turns=loop_turns,
            )
            report.quality_verification = verification

        # 3. 差距模式记录 + 合成信号
        signals = []
        if comparison.has_significant_gaps:
            for gap in comparison.gaps:
                signal = self.synthesis_trigger.record_gap(
                    gap_type=gap.dimension,
                    description=gap.suggestion,
                    severity=gap.gap_severity,
                    session_id=session_id,
                )
                if signal:
                    signals.append(signal)
        report.synthesis_signals = signals

        # 4. 自动存储优质审稿快照
        self._maybe_store_snapshot(
            findings, sections_read, tool_calls, loop_turns,
            total_tokens, paper_type, paper_methodology, session_id,
        )

        self._reports.append(report)
        return report

    def get_reflection_report(self) -> dict:
        """导出完整反思报告（用于 evolution 系统）。"""
        return {
            "total_sessions_reflected": len(self._reports),
            "depth_decisions": self.depth_controller.get_decision_history()[-10:],
            "reference_count": self.comparator.get_reference_count(),
            "quality_reliability": self.verifier.get_historical_reliability(),
            "optimism_trend": self.verifier.get_optimism_trend(),
            "persistent_gaps": [
                {
                    "type": p.gap_type,
                    "count": p.occurrence_count,
                    "avg_severity": p.average_severity,
                    "worsening": p.is_worsening,
                }
                for p in self.synthesis_trigger.get_persistent_patterns()
            ],
            "synthesis_signals_total": len(self.synthesis_trigger.get_signal_history()),
        }

    def _maybe_store_snapshot(
        self,
        findings: list[dict],
        sections_read: list[str],
        tool_calls: list[dict],
        loop_turns: int,
        total_tokens: int,
        paper_type: str,
        paper_methodology: str,
        session_id: str,
    ) -> None:
        """如果当前审稿质量达标，存储为参考快照。"""
        verified_count = sum(1 for f in findings if f.get("status") == "verified")
        verified_ratio = verified_count / max(1, len(findings))

        # 质量门槛: 至少 3 个 findings 且 verified > 40%
        if len(findings) >= 3 and verified_ratio >= 0.4:
            categories: dict[str, int] = {}
            high_prio = 0
            for f in findings:
                cat = f.get("category", "other")
                categories[cat] = categories.get(cat, 0) + 1
                if f.get("priority") == "high":
                    high_prio += 1

            snapshot = ReviewSnapshot(
                session_id=session_id or f"session_{int(time.time())}",
                paper_type=paper_type,
                paper_methodology=paper_methodology,
                total_findings=len(findings),
                high_priority_findings=high_prio,
                findings_categories=categories,
                sections_read=len(sections_read),
                coverage_score=min(1.0, len(sections_read) / 10.0),
                depth_score=min(1.0, len(findings) / 5.0),
                evidence_quality=verified_ratio,
                efficiency=len(findings) / max(1, loop_turns),
                loop_turns=loop_turns,
                total_tokens=total_tokens,
                timestamp=time.time(),
                quality_label="good" if verified_ratio >= 0.6 else "",
                verified_ratio=verified_ratio,
            )
            self.comparator.add_reference(snapshot)

    def serialize(self) -> dict:
        """序列化所有模块状态。"""
        return {
            "depth_controller": self.depth_controller.serialize(),
            "comparator": self.comparator.serialize(),
            "verifier": self.verifier.serialize(),
            "synthesis_trigger": self.synthesis_trigger.serialize(),
            "reports_count": len(self._reports),
        }

    @classmethod
    def deserialize(
        cls,
        data: dict,
        synthesis_receiver: SkillSynthesisReceiver | None = None,
    ) -> "ReflectionCompleteOrchestrator":
        """反序列化。"""
        depth = AdaptiveReflectionDepth.deserialize(data.get("depth_controller", {}))
        comparator = ComparativeReflector.deserialize(data.get("comparator", {}))
        verifier = ReflectionQualityVerifier.deserialize(data.get("verifier", {}))
        trigger = ReflectionSkillSynthesisTrigger.deserialize(
            data.get("synthesis_trigger", {}), receiver=synthesis_receiver
        )
        return cls(
            depth_controller=depth,
            comparator=comparator,
            verifier=verifier,
            synthesis_trigger=trigger,
        )
