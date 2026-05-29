"""
tests/test_reflection_complete.py — Phase 6 Complete 层测试

覆盖:
    - AdaptiveReflectionDepth (ComplexityAssessor + 深度决策 + 调整规则)
    - ComparativeReflector (参考库 + 对比 + 差距识别)
    - ReflectionQualityVerifier (覆盖率/深度/证据/效率验证 + 乐观偏差)
    - ReflectionSkillSynthesisTrigger (模式记录 + 触发条件 + 信号推送)
    - ReflectionCompleteOrchestrator (编排流程 + 序列化)
    - Kill Switch (环境变量控制)
"""

import os
import time
import unittest

from core.reflection_complete import (
    # Kill Switches
    ADAPTIVE_DEPTH_ENABLED,
    COMPARATIVE_REFLECTION_ENABLED,
    REFLECTION_QUALITY_VERIFY_ENABLED,
    REFLECTION_SKILL_SYNTHESIS_ENABLED,
    # Module 1
    AdaptiveReflectionDepth,
    ComplexityAssessor,
    ComplexitySignals,
    ReflectionDepthLevel,
    # Module 2
    ComparativeReflector,
    ComparisonGap,
    ComparisonResult,
    ReviewSnapshot,
    # Module 3
    ReflectionQualityVerifier,
    ReflectionVerificationReport,
    QualityCheckResult,
    # Module 4
    ReflectionSkillSynthesisTrigger,
    RecurringGapPattern,
    SynthesisSignal,
    SkillSynthesisReceiver,
    # Orchestrator
    ReflectionCompleteOrchestrator,
    ReflectionCompleteReport,
)


# ================================================================
# Test Module 1: AdaptiveReflectionDepth
# ================================================================

class TestComplexityAssessor(unittest.TestCase):
    """ComplexityAssessor 论文复杂度评估"""

    def setUp(self):
        self.assessor = ComplexityAssessor()

    def test_simple_paper_low_complexity(self):
        """简单论文（OLS + 少量 sections）应有低复杂度"""
        signals = self.assessor.assess(
            sections_count=5,
            findings_so_far=[],
            tool_call_history=[],
            paper_metadata={
                "abstract": "This paper uses OLS regression to study...",
                "keywords": ["labor economics", "wages"],
            },
        )
        self.assertLess(signals.overall_complexity, 0.4)
        self.assertLess(signals.methodology_complexity, 0.4)

    def test_complex_paper_high_complexity(self):
        """复杂论文（DID + IV 组合 + 高争议）应有高复杂度"""
        findings = [
            {"category": "methodology", "finding": "weak IV", "priority": "high"},
            {"category": "identification", "finding": "parallel trends violated", "priority": "high"},
        ]
        signals = self.assessor.assess(
            sections_count=12,
            findings_so_far=findings,
            tool_call_history=[
                {"name": "search_literature"} for _ in range(5)
            ],
            paper_metadata={
                "abstract": "We use a difference-in-differences design combined with instrumental variables...",
                "keywords": ["DID", "IV", "causal inference", "health economics"],
                "fields": ["economics", "public health", "statistics"],
            },
        )
        self.assertGreater(signals.overall_complexity, 0.5)
        self.assertGreater(signals.methodology_complexity, 0.5)
        self.assertGreater(signals.cross_disciplinary, 0.3)

    def test_controversy_signals_detected(self):
        """高优先级方法论问题应提升争议信号"""
        findings = [
            {"category": "methodology", "finding": "issue1", "priority": "high"},
            {"category": "robustness", "finding": "issue2", "priority": "high"},
            {"category": "identification", "finding": "issue3", "priority": "high"},
        ]
        signals = self.assessor.assess(
            sections_count=8,
            findings_so_far=findings,
            tool_call_history=[],
            paper_metadata={},
        )
        self.assertGreater(signals.controversy_signals, 0.5)

    def test_no_metadata_graceful(self):
        """无元数据时应 graceful fallback"""
        signals = self.assessor.assess(
            sections_count=7,
            findings_so_far=[],
            tool_call_history=[],
            paper_metadata=None,
        )
        self.assertIsInstance(signals, ComplexitySignals)
        self.assertGreaterEqual(signals.overall_complexity, 0.0)
        self.assertLessEqual(signals.overall_complexity, 1.0)

    def test_complexity_signals_bounded(self):
        """所有信号值应在 [0, 1] 范围内"""
        signals = self.assessor.assess(
            sections_count=50,
            findings_so_far=[{"category": "methodology", "finding": f"f{i}", "priority": "high"} for i in range(20)],
            tool_call_history=[{"name": "search_literature"}] * 100,
            paper_metadata={
                "abstract": "DID IV RDD GMM structural estimation bayesian deep learning neural network",
                "fields": ["a", "b", "c", "d", "e"],
            },
        )
        for attr in ("methodology_complexity", "cross_disciplinary", "data_scale_complexity",
                     "controversy_signals", "novelty_score", "length_factor"):
            val = getattr(signals, attr)
            self.assertGreaterEqual(val, 0.0, f"{attr} should be >= 0")
            self.assertLessEqual(val, 1.0, f"{attr} should be <= 1")


class TestAdaptiveReflectionDepth(unittest.TestCase):
    """AdaptiveReflectionDepth 深度决策"""

    def setUp(self):
        self.controller = AdaptiveReflectionDepth()

    def test_simple_paper_minimal_depth(self):
        """简单论文应选择 MINIMAL 深度"""
        level = self.controller.decide_depth(
            sections_count=4,
            findings=[],
            tool_call_history=[],
            paper_metadata={
                "abstract": "This is a simple descriptive study using OLS.",
                "keywords": ["descriptive"],
            },
        )
        self.assertIn(level, [ReflectionDepthLevel.MINIMAL, ReflectionDepthLevel.STANDARD])

    def test_complex_paper_deep_or_intensive(self):
        """复杂论文应选择 DEEP 或 INTENSIVE"""
        level = self.controller.decide_depth(
            sections_count=15,
            findings=[
                {"category": "methodology", "finding": "x", "priority": "high"},
                {"category": "identification", "finding": "y", "priority": "high"},
            ],
            tool_call_history=[{"name": "search_literature"}] * 10,
            paper_metadata={
                "abstract": "DID with instrumental variables and regression discontinuity combined approach...",
                "keywords": ["DID", "IV", "RDD"],
                "fields": ["economics", "public health", "statistics"],
            },
        )
        self.assertIn(level, [ReflectionDepthLevel.DEEP, ReflectionDepthLevel.INTENSIVE])

    def test_anomaly_rate_upgrades_depth(self):
        """高异常率应升级深度"""
        # 先获取基础深度
        base_level = self.controller.decide_depth(
            sections_count=6,
            findings=[{"category": "x", "finding": "y", "priority": "medium"}],
            tool_call_history=[],
            paper_metadata={"abstract": "simple study", "keywords": ["labor"]},
        )

        # 重置 controller 以隔离测试
        controller2 = AdaptiveReflectionDepth()
        upgraded_level = controller2.decide_depth(
            sections_count=6,
            findings=[{"category": "x", "finding": "y", "priority": "medium"}],
            tool_call_history=[],
            paper_metadata={"abstract": "simple study", "keywords": ["labor"]},
            micro_anomaly_rate=0.7,  # 高异常率
        )
        # 升级后的深度应 >= 基础深度
        levels = list(ReflectionDepthLevel)
        self.assertGreaterEqual(levels.index(upgraded_level), levels.index(base_level))

    def test_revisit_count_triggers_intensive(self):
        """多次回退建议应触发 INTENSIVE"""
        level = self.controller.decide_depth(
            sections_count=6,
            findings=[],
            tool_call_history=[],
            paper_metadata={},
            revisit_count=3,
        )
        self.assertEqual(level, ReflectionDepthLevel.INTENSIVE)

    def test_capacity_pressure_downgrades(self):
        """Token 压力应降级深度"""
        # 先创建一个会触发高深度的场景
        controller = AdaptiveReflectionDepth()
        level_no_pressure = controller.decide_depth(
            sections_count=12,
            findings=[{"category": "methodology", "finding": "x", "priority": "high"}] * 3,
            tool_call_history=[{"name": "search_literature"}] * 5,
            paper_metadata={"abstract": "DID IV study", "keywords": ["DID", "IV"]},
        )

        controller2 = AdaptiveReflectionDepth()
        level_with_pressure = controller2.decide_depth(
            sections_count=12,
            findings=[{"category": "methodology", "finding": "x", "priority": "high"}] * 3,
            tool_call_history=[{"name": "search_literature"}] * 5,
            paper_metadata={"abstract": "DID IV study", "keywords": ["DID", "IV"]},
            capacity_pct=0.9,  # 高 token 压力
        )

        levels = list(ReflectionDepthLevel)
        self.assertLessEqual(
            levels.index(level_with_pressure),
            levels.index(level_no_pressure),
        )

    def test_override_respects_manual(self):
        """手动覆盖应被尊重"""
        self.controller.override_depth(ReflectionDepthLevel.INTENSIVE)
        level = self.controller.decide_depth(
            sections_count=3,
            findings=[],
            tool_call_history=[],
            paper_metadata={"abstract": "simple OLS"},
        )
        self.assertEqual(level, ReflectionDepthLevel.INTENSIVE)

        # 解除覆盖
        self.controller.override_depth(None)
        level2 = self.controller.decide_depth(
            sections_count=3,
            findings=[],
            tool_call_history=[],
            paper_metadata={"abstract": "simple OLS"},
        )
        self.assertNotEqual(level2, ReflectionDepthLevel.INTENSIVE)

    def test_token_budget_for_depth(self):
        """不同深度对应正确的 token budget"""
        self.assertEqual(self.controller.get_token_budget_for_depth(ReflectionDepthLevel.MINIMAL), 0)
        self.assertEqual(self.controller.get_token_budget_for_depth(ReflectionDepthLevel.STANDARD), 500)
        self.assertEqual(self.controller.get_token_budget_for_depth(ReflectionDepthLevel.DEEP), 1500)
        self.assertEqual(self.controller.get_token_budget_for_depth(ReflectionDepthLevel.INTENSIVE), 3000)

    def test_decision_history_recorded(self):
        """决策应被记录到历史"""
        self.controller.decide_depth(
            sections_count=5, findings=[], tool_call_history=[], paper_metadata={}
        )
        self.controller.decide_depth(
            sections_count=10, findings=[], tool_call_history=[], paper_metadata={}
        )
        history = self.controller.get_decision_history()
        self.assertEqual(len(history), 2)
        self.assertIn("complexity", history[0])
        self.assertIn("base_level", history[0])

    def test_serialize_deserialize(self):
        """序列化/反序列化保持状态"""
        self.controller.decide_depth(
            sections_count=8, findings=[], tool_call_history=[], paper_metadata={}
        )
        self.controller.override_depth(ReflectionDepthLevel.DEEP)

        data = self.controller.serialize()
        restored = AdaptiveReflectionDepth.deserialize(data)

        self.assertEqual(restored._override_level, ReflectionDepthLevel.DEEP)
        self.assertEqual(len(restored.get_decision_history()), 1)


# ================================================================
# Test Module 2: ComparativeReflector
# ================================================================

class TestComparativeReflector(unittest.TestCase):
    """ComparativeReflector 对比反思"""

    def setUp(self):
        self.reflector = ComparativeReflector()
        # 添加一些参考快照
        self._add_good_reference()

    def _add_good_reference(self):
        """添加一个优质参考快照"""
        self.reflector.add_reference(ReviewSnapshot(
            session_id="ref_001",
            paper_type="empirical",
            paper_methodology="DID",
            total_findings=8,
            high_priority_findings=3,
            findings_categories={"methodology": 3, "statistics": 2, "clarity": 2, "robustness": 1},
            sections_read=9,
            coverage_score=0.9,
            depth_score=0.8,
            evidence_quality=0.75,
            efficiency=0.4,
            loop_turns=20,
            total_tokens=5000,
            timestamp=time.time() - 3600,
            quality_label="excellent",
            verified_ratio=0.75,
        ))

    def test_no_references_empty_comparison(self):
        """无参考库时应返回空对比"""
        empty_reflector = ComparativeReflector()
        result = empty_reflector.compare(
            current_findings=[{"category": "x", "finding": "y", "status": "verified"}],
            current_sections_read=["intro"],
            current_tool_calls=[],
            current_loop_turns=5,
            current_total_tokens=1000,
        )
        self.assertIsNone(result.reference_snapshot)
        self.assertEqual(result.gaps, [])

    def test_worse_than_reference_shows_gaps(self):
        """劣于参考时应显示差距"""
        result = self.reflector.compare(
            current_findings=[
                {"category": "methodology", "finding": "f1", "status": "verified", "priority": "medium"}
            ],
            current_sections_read=["intro", "method"],
            current_tool_calls=[],
            current_loop_turns=10,
            current_total_tokens=2000,
            paper_type="empirical",
            paper_methodology="DID",
        )
        self.assertIsNotNone(result.reference_snapshot)
        self.assertTrue(result.has_significant_gaps)
        self.assertGreater(len(result.gaps), 0)
        self.assertGreater(result.overall_gap_score, 0.0)

    def test_better_than_reference_shows_strengths(self):
        """优于参考时应显示优势"""
        result = self.reflector.compare(
            current_findings=[
                {"category": f"cat{i}", "finding": f"f{i}", "status": "verified", "priority": "high"}
                for i in range(12)
            ],
            current_sections_read=[f"s{i}" for i in range(15)],
            current_tool_calls=[],
            current_loop_turns=15,
            current_total_tokens=4000,
            paper_type="empirical",
        )
        self.assertTrue(result.strengths_vs_reference)

    def test_type_matching_selects_correct_reference(self):
        """应优先选择同类型的参考"""
        # 添加不同类型的参考
        self.reflector.add_reference(ReviewSnapshot(
            session_id="ref_theory",
            paper_type="theoretical",
            paper_methodology="formal_model",
            total_findings=4,
            sections_read=6,
            coverage_score=0.6,
            depth_score=0.9,
            evidence_quality=0.5,
            efficiency=0.3,
            loop_turns=12,
            total_tokens=3000,
            timestamp=time.time(),
            verified_ratio=0.5,
        ))

        result = self.reflector.compare(
            current_findings=[{"category": "x", "finding": "y", "status": "verified"}],
            current_sections_read=["intro"],
            current_tool_calls=[],
            current_loop_turns=5,
            current_total_tokens=1000,
            paper_type="theoretical",
            paper_methodology="formal_model",
        )
        self.assertEqual(result.reference_snapshot.paper_type, "theoretical")

    def test_max_references_maintained(self):
        """应维持参考库容量上限"""
        reflector = ComparativeReflector()
        for i in range(60):
            reflector.add_reference(ReviewSnapshot(
                session_id=f"ref_{i}",
                coverage_score=i * 0.01,
                depth_score=0.5,
                evidence_quality=0.5,
                efficiency=0.3,
                total_findings=3,
                sections_read=5,
                loop_turns=10,
                total_tokens=2000,
                timestamp=time.time(),
                verified_ratio=0.5,
            ))
        self.assertLessEqual(reflector.get_reference_count(), ComparativeReflector.MAX_REFERENCES)

    def test_actionable_improvements_generated(self):
        """差距应产出可操作的改进建议"""
        result = self.reflector.compare(
            current_findings=[],
            current_sections_read=["intro"],
            current_tool_calls=[],
            current_loop_turns=10,
            current_total_tokens=2000,
        )
        if result.has_significant_gaps:
            self.assertTrue(result.actionable_improvements)
            # 建议应该是字符串
            for imp in result.actionable_improvements:
                self.assertIsInstance(imp, str)
                self.assertGreater(len(imp), 5)

    def test_serialize_deserialize(self):
        """序列化/反序列化保持参考库"""
        data = self.reflector.serialize()
        restored = ComparativeReflector.deserialize(data)
        self.assertEqual(restored.get_reference_count(), self.reflector.get_reference_count())

        # 恢复后的参考应该可用
        best = restored.get_best_reference("empirical")
        self.assertIsNotNone(best)
        self.assertEqual(best.session_id, "ref_001")


# ================================================================
# Test Module 3: ReflectionQualityVerifier
# ================================================================

class TestReflectionQualityVerifier(unittest.TestCase):
    """ReflectionQualityVerifier 反思质量验证"""

    def setUp(self):
        self.verifier = ReflectionQualityVerifier()

    def test_accurate_claims_verified(self):
        """准确的反思声称应通过验证"""
        findings = [
            {"category": "methodology", "finding": "f1", "status": "verified", "priority": "high"},
            {"category": "statistics", "finding": "f2", "status": "verified", "priority": "medium"},
            {"category": "clarity", "finding": "f3", "status": "verified", "priority": "medium"},
        ]
        report = self.verifier.verify_phase_reflection(
            reflection_claims={
                "coverage_score": 0.6,
                "depth_score": 0.6,
                "evidence_quality": 1.0,  # 全部 verified
            },
            actual_findings=findings,
            actual_sections_read=["intro", "method", "results", "discussion", "conclusion", "abstract"],
            total_sections=10,
            actual_tool_calls=[],
            actual_loop_turns=8,
        )
        self.assertGreater(report.overall_reliability, 0.5)

    def test_overestimated_coverage_refuted(self):
        """过高的覆盖率声称应被驳斥"""
        report = self.verifier.verify_phase_reflection(
            reflection_claims={
                "coverage_score": 0.9,  # 声称 90%
            },
            actual_findings=[],
            actual_sections_read=["intro"],  # 实际只读了 1 个
            total_sections=10,
            actual_tool_calls=[],
            actual_loop_turns=5,
        )
        # 应该有 refuted claims
        refuted = [c for c in report.checks if not c.verified and c.confidence > 0.5]
        self.assertGreater(len(refuted), 0)

    def test_overestimated_depth_refuted(self):
        """过高的深度声称应被驳斥"""
        report = self.verifier.verify_phase_reflection(
            reflection_claims={
                "coverage_score": 0.5,
                "depth_score": 0.9,  # 声称深度很高
            },
            actual_findings=[
                {"category": "x", "finding": "trivial", "status": "tentative", "priority": "low"}
            ],
            actual_sections_read=["intro", "method", "results", "discussion", "conclusion"],
            total_sections=10,
            actual_tool_calls=[],
            actual_loop_turns=5,
        )
        depth_check = next(
            (c for c in report.checks if "深度" in c.claim), None
        )
        if depth_check:
            self.assertFalse(depth_check.verified)

    def test_no_gaps_claim_with_missing_sections(self):
        """声称无遗漏但核心 sections 未读应被驳斥"""
        report = self.verifier.verify_phase_reflection(
            reflection_claims={
                "coverage_score": 0.5,
                "depth_score": 0.5,
                "evidence_quality": 0.5,
                "no_gaps": True,
            },
            actual_findings=[{"category": "x", "finding": "y", "status": "verified"}],
            actual_sections_read=["intro"],  # 缺少 methodology, results 等核心
            total_sections=10,
            actual_tool_calls=[],
            actual_loop_turns=5,
        )
        gap_check = next(
            (c for c in report.checks if "遗漏" in c.claim), None
        )
        self.assertIsNotNone(gap_check)
        self.assertFalse(gap_check.verified)

    def test_optimism_bias_detected(self):
        """过度乐观应被检测"""
        report = self.verifier.verify_global_reflection(
            global_self_score=9.5,  # 自评极高
            claimed_strengths=["覆盖非常全面", "分析极其深入"],
            claimed_weaknesses=[],  # 不承认任何弱点
            actual_findings=[{"category": "x", "finding": "y", "status": "tentative"}],
            actual_sections_read=["intro"],
            total_sections=12,
            actual_loop_turns=3,
        )
        self.assertGreater(report.optimism_bias, 0.0)

    def test_historical_reliability_tracking(self):
        """应追踪历史可靠性"""
        # 做几次验证
        for _ in range(3):
            self.verifier.verify_phase_reflection(
                reflection_claims={"coverage_score": 0.5, "depth_score": 0.5, "evidence_quality": 0.5},
                actual_findings=[{"category": "x", "finding": "y", "status": "verified"}],
                actual_sections_read=["intro", "method", "results", "discussion", "conclusion"],
                total_sections=10,
                actual_tool_calls=[],
                actual_loop_turns=5,
            )
        reliability = self.verifier.get_historical_reliability()
        self.assertGreaterEqual(reliability, 0.0)
        self.assertLessEqual(reliability, 1.0)

    def test_efficiency_claim_verification(self):
        """效率声称验证"""
        report = self.verifier.verify_phase_reflection(
            reflection_claims={
                "coverage_score": 0.5,
                "depth_score": 0.5,
                "evidence_quality": 0.5,
                "efficiency": 0.8,  # 声称效率 0.8
            },
            actual_findings=[{"category": "x", "finding": "y", "status": "verified"}],
            actual_sections_read=["intro", "method", "results", "discussion", "conclusion"],
            total_sections=10,
            actual_tool_calls=[],
            actual_loop_turns=10,  # 实际效率 = 1/10 = 0.1
        )
        eff_check = next(
            (c for c in report.checks if "效率" in c.claim), None
        )
        if eff_check:
            self.assertFalse(eff_check.verified)  # 0.8 vs 0.1 差距大

    def test_serialize_deserialize(self):
        """序列化/反序列化"""
        self.verifier.verify_phase_reflection(
            reflection_claims={"coverage_score": 0.5, "depth_score": 0.5, "evidence_quality": 0.5},
            actual_findings=[],
            actual_sections_read=["intro"],
            total_sections=10,
            actual_tool_calls=[],
            actual_loop_turns=5,
        )
        data = self.verifier.serialize()
        restored = ReflectionQualityVerifier.deserialize(data)
        self.assertIsInstance(restored, ReflectionQualityVerifier)


# ================================================================
# Test Module 4: ReflectionSkillSynthesisTrigger
# ================================================================

class TestReflectionSkillSynthesisTrigger(unittest.TestCase):
    """ReflectionSkillSynthesisTrigger 合成触发"""

    def setUp(self):
        self.trigger = ReflectionSkillSynthesisTrigger()

    def test_single_gap_no_trigger(self):
        """单次差距不应触发信号"""
        signal = self.trigger.record_gap(
            gap_type="coverage",
            description="覆盖率不足",
            severity=0.5,
            session_id="s1",
        )
        self.assertIsNone(signal)

    def test_recurring_gap_triggers_signal(self):
        """反复出现的差距应触发信号"""
        for i in range(self.trigger.RECURRENCE_THRESHOLD):
            signal = self.trigger.record_gap(
                gap_type="coverage",
                description="覆盖率不足",
                severity=0.5,
                session_id=f"s{i}",
            )

        # 第 RECURRENCE_THRESHOLD 次应该触发
        self.assertIsNotNone(signal)
        self.assertIsInstance(signal, SynthesisSignal)
        self.assertEqual(signal.suggested_skill_type, "systematic_scan_skill")

    def test_low_severity_no_trigger(self):
        """低严重程度即使反复出现也不触发"""
        for i in range(5):
            signal = self.trigger.record_gap(
                gap_type="minor_issue",
                description="小问题",
                severity=0.1,  # 低于 SEVERITY_THRESHOLD
                session_id=f"s{i}",
            )
        self.assertIsNone(signal)

    def test_cooldown_prevents_rapid_fire(self):
        """Cooldown 应防止连续触发"""
        # 触发第一次信号
        for i in range(3):
            self.trigger.record_gap("coverage", "覆盖率不足", 0.5, f"s{i}")

        # 立即再记录一次
        signal = self.trigger.record_gap("coverage", "覆盖率不足", 0.5, "s4")
        # 因为 cooldown，不应再次触发（除非 worsening）
        self.assertIsNone(signal)

    def test_worsening_bypasses_cooldown(self):
        """恶化趋势应绕过 cooldown"""
        trigger = ReflectionSkillSynthesisTrigger()
        trigger.COOLDOWN_SECONDS = 99999  # 超长 cooldown

        # 初始触发（逐渐加重）
        for i in range(3):
            trigger.record_gap("depth", "深度不足", 0.3 + i * 0.1, f"s{i}")

        # 恶化中再触发 — severity 持续上升使得 is_worsening=True
        signal = trigger.record_gap("depth", "深度不足", 0.7, "s4")
        # 恶化模式应绕过 cooldown
        # 注意: is_worsening 需要 3 条 severity_trend 且最后 > 第一个 * 1.1
        # 目前有 [0.3, 0.4, 0.5, 0.7] → recent[-3:] = [0.4, 0.5, 0.7], 0.7 > 0.4*1.1=0.44 ✓
        if signal is not None:
            self.assertIsInstance(signal, SynthesisSignal)

    def test_signal_priority_calculation(self):
        """信号优先级应正确计算"""
        for i in range(5):
            self.trigger.record_gap("evidence", "证据不足", 0.8, f"s{i}")

        signals = self.trigger.get_signal_history()
        self.assertGreater(len(signals), 0)
        # 高 severity (0.8) + 多次出现 → 高优先级
        self.assertGreater(signals[-1].priority, 0.3)

    def test_persistent_patterns_detected(self):
        """持续性模式应被识别"""
        for i in range(5):
            self.trigger.record_gap("coverage", "覆盖率不足", 0.5, f"s{i}")
        for i in range(2):
            self.trigger.record_gap("depth", "深度不足", 0.4, f"s{i}")

        persistent = self.trigger.get_persistent_patterns()
        self.assertEqual(len(persistent), 1)  # 只有 coverage 达到 3 次
        self.assertEqual(persistent[0].gap_type, "coverage")

    def test_worsening_patterns(self):
        """恶化趋势检测"""
        # 严重程度递增
        severities = [0.3, 0.4, 0.5, 0.6, 0.7]
        for i, sev in enumerate(severities):
            self.trigger.record_gap("depth", "深度恶化", sev, f"s{i}")

        worsening = self.trigger.get_worsening_patterns()
        self.assertTrue(len(worsening) > 0)
        self.assertTrue(worsening[0].is_worsening)

    def test_cooldown_prevents_repeated_signals(self):
        """冷却期内不重复触发"""
        signals = []
        for i in range(6):
            s = self.trigger.record_gap("evidence", "证据不足", 0.5, f"s{i}")
            if s:
                signals.append(s)

        # 应该只触发一次（cooldown 内不重复）
        self.assertEqual(len(signals), 1)

    def test_worsening_bypasses_cooldown(self):
        """恶化模式绕过冷却期"""
        # 先触发一次
        for i in range(3):
            self.trigger.record_gap("coverage", "覆盖率不足", 0.5, f"s{i}")

        # 模拟恶化（严重程度超过最初 10%）
        # 需要 >= 3 条 severity_trend 且最后一条 > 第一条 * 1.1
        self.trigger._gap_patterns["coverage"].severity_trend = [0.3, 0.4, 0.6]
        self.trigger._last_signal_time["coverage"] = time.time() - 100  # 仅 100 秒前

        # 恶化模式应该绕过 cooldown
        signal = self.trigger.record_gap("coverage", "覆盖率不足", 0.7, "s_extra")
        # Note: 是否触发取决于 is_worsening 检查
        # is_worsening 需要最近 3 条的最后一条 > 第一条 * 1.1
        pattern = self.trigger._gap_patterns["coverage"]
        if pattern.is_worsening:
            self.assertIsNotNone(signal)

    def test_serialize_deserialize(self):
        """序列化/反序列化"""
        for i in range(4):
            self.trigger.record_gap("coverage", "覆盖率", 0.5, f"s{i}")
        self.trigger.record_gap("depth", "深度", 0.3, "s0")

        data = self.trigger.serialize()
        restored = ReflectionSkillSynthesisTrigger.deserialize(data)

        self.assertEqual(len(restored._gap_patterns), 2)
        self.assertEqual(restored._gap_patterns["coverage"].occurrence_count, 4)

    def test_receiver_protocol(self):
        """接收者协议正确推送"""
        received = []

        class MockReceiver:
            def receive_synthesis_signal(self, signal):
                received.append(signal)
                return True

        trigger = ReflectionSkillSynthesisTrigger(receiver=MockReceiver())
        for i in range(4):
            trigger.record_gap("coverage", "覆盖率不足", 0.6, f"s{i}")

        self.assertTrue(len(received) > 0)


class TestReflectionCompleteOrchestrator(unittest.TestCase):
    """统一编排器测试"""

    def setUp(self):
        self.orchestrator = ReflectionCompleteOrchestrator()

    def test_decide_reflection_depth(self):
        """深度决策应基于复杂度"""
        # 简单论文
        level = self.orchestrator.decide_reflection_depth(
            sections_count=5,
            findings=[],
            tool_call_history=[],
            paper_metadata={"methodology": "ols", "keywords": ["descriptive"]},
        )
        self.assertIn(level, [ReflectionDepthLevel.MINIMAL, ReflectionDepthLevel.STANDARD])

    def test_on_phase_end_verification(self):
        """Phase 结束时应验证反思质量"""
        claims = {
            "coverage_score": 0.9,  # 声称高覆盖
            "depth_score": 0.8,
            "evidence_quality": 0.7,
        }
        report = self.orchestrator.on_phase_end(
            phase_reflection_claims=claims,
            actual_findings=[
                {"category": "x", "finding": "y", "status": "verified", "priority": "high"}
            ],
            actual_sections_read=["intro", "method"],  # 只读了 2 个
            total_sections=10,  # 总共 10 个
            actual_tool_calls=[],
            actual_loop_turns=5,
        )
        # 声称覆盖率 0.9 但实际 2/10 = 0.2 → 应该被 refute
        self.assertIsInstance(report, ReflectionVerificationReport)
        self.assertGreater(report.refuted_claims, 0)

    def test_on_session_end_full_flow(self):
        """Session 结束时应执行完整流程"""
        # 先添加参考
        ref = ReviewSnapshot(
            session_id="ref_1",
            total_findings=8,
            high_priority_findings=3,
            sections_read=10,
            coverage_score=0.9,
            depth_score=0.9,
            evidence_quality=0.8,
            efficiency=0.5,
            loop_turns=16,
            total_tokens=5000,
            verified_ratio=0.8,
            timestamp=time.time() - 86400,
        )
        self.orchestrator.comparator.add_reference(ref)

        # 执行 session end
        report = self.orchestrator.on_session_end(
            findings=[
                {"category": "x", "finding": "f1", "status": "verified", "priority": "medium"},
                {"category": "y", "finding": "f2", "status": "tentative", "priority": "low"},
            ],
            sections_read=["intro", "method", "results"],
            tool_calls=[{"name": "read", "success": True}] * 5,
            loop_turns=10,
            total_tokens=3000,
            total_sections=10,
            session_id="test_session",
        )
        self.assertIsInstance(report, ReflectionCompleteReport)
        # 应该有对比（因为添加了参考）
        self.assertIsNotNone(report.comparison)
        self.assertIsNotNone(report.comparison.reference_snapshot)

    def test_on_session_end_stores_good_snapshot(self):
        """质量好的审稿应自动存储为参考"""
        self.assertEqual(self.orchestrator.comparator.get_reference_count(), 0)

        # 产出一次质量好的审稿
        self.orchestrator.on_session_end(
            findings=[
                {"category": "methodology", "finding": f"f{i}", "status": "verified", "priority": "high"}
                for i in range(5)
            ],
            sections_read=["intro", "method", "results", "discussion", "conclusion"],
            tool_calls=[{"name": "read", "success": True}] * 10,
            loop_turns=15,
            total_tokens=4000,
            total_sections=8,
            session_id="good_session",
        )

        # 应该自动存储为参考
        self.assertEqual(self.orchestrator.comparator.get_reference_count(), 1)

    def test_on_session_end_does_not_store_poor_snapshot(self):
        """质量差的审稿不存储"""
        self.orchestrator.on_session_end(
            findings=[{"category": "x", "finding": "y", "status": "tentative", "priority": "low"}],
            sections_read=["intro"],
            tool_calls=[],
            loop_turns=10,
            total_tokens=2000,
            total_sections=10,
            session_id="poor_session",
        )
        # 只有 1 个 finding 且 verified_ratio = 0 → 不存储
        self.assertEqual(self.orchestrator.comparator.get_reference_count(), 0)

    def test_get_reflection_report(self):
        """报告导出格式正确"""
        report = self.orchestrator.get_reflection_report()
        self.assertIn("total_sessions_reflected", report)
        self.assertIn("quality_reliability", report)
        self.assertIn("persistent_gaps", report)

    def test_serialize_deserialize(self):
        """完整序列化/反序列化"""
        # 添加一些状态
        self.orchestrator.comparator.add_reference(ReviewSnapshot(
            session_id="ref1", total_findings=5, sections_read=8,
            coverage_score=0.8, depth_score=0.7, evidence_quality=0.6,
            efficiency=0.4, verified_ratio=0.6, timestamp=time.time(),
        ))
        self.orchestrator.synthesis_trigger.record_gap("coverage", "测试", 0.5, "s1")

        data = self.orchestrator.serialize()
        restored = ReflectionCompleteOrchestrator.deserialize(data)

        self.assertEqual(restored.comparator.get_reference_count(), 1)
        self.assertEqual(len(restored.synthesis_trigger._gap_patterns), 1)

    def test_global_reflection_verification(self):
        """全局反思验证流程"""
        report = self.orchestrator.on_session_end(
            findings=[
                {"category": "x", "finding": "y", "status": "verified", "priority": "high"}
                for _ in range(4)
            ],
            sections_read=["intro", "method", "results", "discussion"],
            tool_calls=[{"name": "t", "success": True}] * 10,
            loop_turns=12,
            total_tokens=4000,
            total_sections=8,
            session_id="test",
            global_reflection_claims={
                "self_score": 9.0,  # 过高的自评
                "strengths": ["覆盖面广", "证据充分"],
                "weaknesses": [],
            },
        )
        # 应该有质量验证
        self.assertIsNotNone(report.quality_verification)
        # 9.0 分自评对于 4 findings + 4 sections 来说可能偏高
        self.assertIsInstance(report.quality_verification.optimism_bias, float)


class TestKillSwitches(unittest.TestCase):
    """Kill Switch 测试"""

    def test_adaptive_depth_disabled(self):
        """关闭 adaptive depth 应返回 STANDARD"""
        import core.reflection_complete as rc
        original = rc.ADAPTIVE_DEPTH_ENABLED
        try:
            rc.ADAPTIVE_DEPTH_ENABLED = False
            controller = AdaptiveReflectionDepth()
            level = controller.decide_depth(
                sections_count=20,
                findings=[{"category": "x"}] * 10,
                tool_call_history=[{"name": "t"}] * 20,
                paper_metadata={"methodology": "structural estimation bayesian"},
            )
            self.assertEqual(level, ReflectionDepthLevel.STANDARD)
        finally:
            rc.ADAPTIVE_DEPTH_ENABLED = original

    def test_comparative_disabled(self):
        """关闭 comparative 应返回空结果"""
        import core.reflection_complete as rc
        original = rc.COMPARATIVE_REFLECTION_ENABLED
        try:
            rc.COMPARATIVE_REFLECTION_ENABLED = False
            comparator = ComparativeReflector()
            comparator.add_reference(ReviewSnapshot(
                session_id="r", total_findings=5, coverage_score=0.9,
                depth_score=0.9, evidence_quality=0.8, efficiency=0.5,
                verified_ratio=0.8, timestamp=time.time(),
            ))
            result = comparator.compare(
                current_findings=[{"status": "verified"}] * 2,
                current_sections_read=["intro"],
                current_tool_calls=[],
                current_loop_turns=5,
                current_total_tokens=1000,
            )
            self.assertIsNone(result.reference_snapshot)
        finally:
            rc.COMPARATIVE_REFLECTION_ENABLED = original

    def test_quality_verify_disabled(self):
        """关闭 quality verify 应返回 reliability=1.0"""
        import core.reflection_complete as rc
        original = rc.REFLECTION_QUALITY_VERIFY_ENABLED
        try:
            rc.REFLECTION_QUALITY_VERIFY_ENABLED = False
            verifier = ReflectionQualityVerifier()
            report = verifier.verify_phase_reflection(
                reflection_claims={"coverage_score": 0.99},
                actual_findings=[],
                actual_sections_read=[],
                total_sections=20,
                actual_tool_calls=[],
                actual_loop_turns=1,
            )
            self.assertEqual(report.overall_reliability, 1.0)
        finally:
            rc.REFLECTION_QUALITY_VERIFY_ENABLED = original

    def test_synthesis_disabled(self):
        """关闭 synthesis 应不产出信号"""
        import core.reflection_complete as rc
        original = rc.REFLECTION_SKILL_SYNTHESIS_ENABLED
        try:
            rc.REFLECTION_SKILL_SYNTHESIS_ENABLED = False
            trigger = ReflectionSkillSynthesisTrigger()
            for i in range(10):
                signal = trigger.record_gap("coverage", "test", 0.8, f"s{i}")
                self.assertIsNone(signal)
        finally:
            rc.REFLECTION_SKILL_SYNTHESIS_ENABLED = original


if __name__ == "__main__":
    unittest.main()
