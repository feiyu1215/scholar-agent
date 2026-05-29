"""
tests/test_reflection_engine.py — Phase 6 三层反思引擎单元测试
"""

import unittest
import asyncio

from core.reflection_engine import (
    ReflectionEngine,
    MicroReflector,
    PhaseReflector,
    GlobalReflector,
    MicroReflection,
    PhaseReflection,
    GlobalReflection,
    MicroVerdict,
    ReflectionLevel,
)


class TestMicroReflector(unittest.TestCase):
    """Micro 级反射: 每次 tool_call 后的规则判断"""

    def setUp(self):
        self.reflector = MicroReflector()

    def test_normal_call_passes(self):
        """正常 tool call → PASS"""
        result = self.reflector.reflect(
            tool_name="extract_text",
            tool_params={"section": "introduction"},
            tool_result="Some extracted text content here that is non-empty",
            success=True,
            turn=1,
        )
        self.assertEqual(result.verdict, MicroVerdict.PASS)

    def test_failure_detected(self):
        """工具失败 → FAILURE"""
        result = self.reflector.reflect(
            tool_name="read_section",
            tool_params={"section": "nonexistent"},
            tool_result=None,
            success=False,
            turn=2,
        )
        self.assertEqual(result.verdict, MicroVerdict.FAILURE)
        self.assertIn("失败", result.observation)

    def test_empty_result_unexpected(self):
        """空结果 → UNEXPECTED"""
        result = self.reflector.reflect(
            tool_name="search_literature",
            tool_params={"query": "DID"},
            tool_result="",
            success=True,
            turn=3,
        )
        self.assertEqual(result.verdict, MicroVerdict.UNEXPECTED)
        self.assertIn("空", result.observation)

    def test_none_result_unexpected(self):
        """None 结果 → UNEXPECTED"""
        result = self.reflector.reflect(
            tool_name="analyze_table",
            tool_params={},
            tool_result=None,
            success=True,
            turn=4,
        )
        self.assertEqual(result.verdict, MicroVerdict.UNEXPECTED)

    def test_consecutive_anomalies_escalate(self):
        """连续异常积累后，下一次非空成功调用应触发 ANOMALY"""
        # 积累足够的异常（空结果），使 _anomaly_count >= _max_anomalies_before_alert
        for i in range(self.reflector._max_anomalies_before_alert):
            self.reflector.reflect(
                tool_name="search_literature",
                tool_params={"query": "test"},
                tool_result="",
                success=True,
                turn=i,
            )
        # 下一次传入非空成功结果，应检测到连续异常并返回 ANOMALY
        result = self.reflector.reflect(
            tool_name="extract_text",
            tool_params={"section": "intro"},
            tool_result="some non-empty result",
            success=True,
            turn=10,
        )
        self.assertEqual(result.verdict, MicroVerdict.ANOMALY)

    def test_success_resets_anomaly_count(self):
        """成功调用应重置异常计数"""
        # 触发 2 次失败
        self.reflector.reflect("x", {}, None, success=False, turn=1)
        self.reflector.reflect("x", {}, None, success=False, turn=2)
        # 一次成功
        self.reflector.reflect("x", {}, "good result", success=True, turn=3)
        # 然后 1 次失败不应触发 ANOMALY（计数已重置）
        result = self.reflector.reflect("x", {}, None, success=False, turn=4)
        self.assertEqual(result.verdict, MicroVerdict.FAILURE)  # not ANOMALY

    def test_suggestion_for_known_tools(self):
        """已知工具应有特定建议"""
        result = self.reflector.reflect(
            tool_name="read_section",
            tool_params={"section": "test"},
            tool_result=None,
            success=False,
            turn=1,
        )
        self.assertIn("section", result.suggestion.lower())

    def test_passed_convenience(self):
        """MicroReflection.passed() 便捷方法"""
        passed = MicroReflection.passed()
        self.assertEqual(passed.verdict, MicroVerdict.PASS)


class TestPhaseReflector(unittest.TestCase):
    """Phase 级反思: Phase 结束时的结构化评估"""

    def setUp(self):
        self.reflector = PhaseReflector(llm=None)

    def test_good_coverage_adequate_score(self):
        """覆盖充分时应有较高分数"""
        result = asyncio.run(self.reflector.reflect(
            phase_name="deep_analysis",
            findings_in_phase=[
                {"category": "methodology", "finding": "f1", "status": "verified"},
                {"category": "statistics", "finding": "f2", "status": "verified"},
                {"category": "clarity", "finding": "f3", "status": "verified"},
            ],
            sections_read_in_phase=["intro", "method", "results", "discussion", "conclusion"],
            tool_calls_in_phase=[{"tool": "extract_text"}, {"tool": "search_citations"}],
            total_turns_in_phase=8,
        ))
        self.assertIsInstance(result, PhaseReflection)
        self.assertEqual(result.phase_name, "deep_analysis")
        self.assertGreater(result.overall_score, 0.5)

    def test_insufficient_findings_flagged(self):
        """findings 不足应产生 gaps"""
        result = asyncio.run(self.reflector.reflect(
            phase_name="deep_analysis",
            findings_in_phase=[],
            sections_read_in_phase=["intro"],
            tool_calls_in_phase=[],
            total_turns_in_phase=2,
        ))
        self.assertTrue(result.gaps_identified)
        self.assertLess(result.depth_score, 0.5)

    def test_should_revisit_when_very_poor(self):
        """覆盖率和深度均极低时建议回退"""
        result = asyncio.run(self.reflector.reflect(
            phase_name="methodology_analysis",
            findings_in_phase=[],
            sections_read_in_phase=[],
            tool_calls_in_phase=[],
            total_turns_in_phase=1,
        ))
        self.assertTrue(result.should_revisit)
        self.assertTrue(result.revisit_reason)

    def test_efficiency_gap_detected(self):
        """高轮数低产出应标记效率问题"""
        result = asyncio.run(self.reflector.reflect(
            phase_name="overall_assessment",
            findings_in_phase=[{"category": "x", "finding": "y", "status": "verified"}],
            sections_read_in_phase=["s1", "s2", "s3"],
            tool_calls_in_phase=[{"tool": "t"}] * 10,
            total_turns_in_phase=15,
        ))
        # 15 轮只有 1 个 finding → 效率低
        efficiency_gap = any("效率" in g for g in result.gaps_identified)
        self.assertTrue(efficiency_gap)

    def test_unknown_phase_uses_defaults(self):
        """未知 phase 使用默认期望"""
        result = asyncio.run(self.reflector.reflect(
            phase_name="unknown_custom_phase",
            findings_in_phase=[{"category": "x", "finding": "y", "status": "verified"}],
            sections_read_in_phase=["s1", "s2"],
            tool_calls_in_phase=[],
            total_turns_in_phase=3,
        ))
        self.assertIsInstance(result, PhaseReflection)


class TestGlobalReflector(unittest.TestCase):
    """Global 级反思: Session 结束时的整体评估"""

    def setUp(self):
        self.reflector = GlobalReflector(llm=None)

    def test_good_session_high_score(self):
        """优质 session 应有较高自评分"""
        result = asyncio.run(self.reflector.reflect(
            findings=[
                {"category": "methodology", "finding": f"f{i}", "status": "verified", "priority": "high"}
                for i in range(8)
            ],
            edits=[{"type": "suggestion"}] * 3,
            sections_read=["intro", "method", "results", "discussion", "conclusion",
                          "abstract", "references", "appendix", "data"],
            tool_call_history=[{"tool": "t", "success": True}] * 20,
            loop_turns=20,
            total_tokens=5000,
        ))
        self.assertIsInstance(result, GlobalReflection)
        self.assertGreater(result.self_score, 5.0)
        self.assertTrue(result.strengths)

    def test_poor_session_low_score(self):
        """差 session 应有较低分数和弱点标记"""
        result = asyncio.run(self.reflector.reflect(
            findings=[
                {"category": "x", "finding": "y", "status": "tentative"}
            ],
            edits=[],
            sections_read=["intro"],
            tool_call_history=[
                {"tool": "t", "success": False},
                {"tool": "t", "success": False},
                {"tool": "t", "success": True},
            ],
            loop_turns=10,
            total_tokens=3000,
        ))
        self.assertLess(result.self_score, 6.0)
        self.assertTrue(result.weaknesses)

    def test_no_findings_extreme_weakness(self):
        """零 findings 应标记弱点"""
        result = asyncio.run(self.reflector.reflect(
            findings=[],
            edits=[],
            sections_read=["intro"],
            tool_call_history=[],
            loop_turns=5,
            total_tokens=1000,
        ))
        self.assertTrue(any("少" in w or "偏少" in w for w in result.weaknesses))

    def test_unverified_findings_weakness(self):
        """大量未验证 findings 应标记"""
        result = asyncio.run(self.reflector.reflect(
            findings=[
                {"category": "x", "finding": f"f{i}", "status": "tentative"}
                for i in range(10)
            ],
            edits=[],
            sections_read=["s1", "s2"],
            tool_call_history=[{"tool": "t", "success": True}] * 10,
            loop_turns=10,
            total_tokens=2000,
        ))
        self.assertTrue(any("验证" in w for w in result.weaknesses))


class TestReflectionEngine(unittest.TestCase):
    """ReflectionEngine 统一入口测试"""

    def setUp(self):
        self.engine = ReflectionEngine(llm=None)

    def test_micro_reflect(self):
        """micro_reflect 应正确分发"""
        result = self.engine.micro_reflect(
            tool_name="extract_text",
            tool_params={"section": "intro"},
            tool_result=None,
            success=False,
            turn=1,
        )
        self.assertIsInstance(result, MicroReflection)
        self.assertEqual(result.verdict, MicroVerdict.FAILURE)

    def test_phase_reflect(self):
        """phase_reflect 应正确分发"""
        result = asyncio.run(self.engine.phase_reflect(
            phase_name="deep_analysis",
            findings_in_phase=[{"category": "x", "finding": "y", "status": "verified"}] * 3,
            sections_read_in_phase=["s1", "s2", "s3", "s4", "s5"],
            tool_calls_in_phase=[],
            total_turns_in_phase=6,
            turn=10,
        ))
        self.assertIsInstance(result, PhaseReflection)

    def test_global_reflect(self):
        """global_reflect 应正确分发"""
        result = asyncio.run(self.engine.global_reflect(
            findings=[{"category": "x", "finding": "y", "status": "verified"}],
            edits=[],
            sections_read=["intro"],
            tool_call_history=[{"tool": "t", "success": True}],
            loop_turns=5,
            total_tokens=1000,
        ))
        self.assertIsInstance(result, GlobalReflection)

    def test_reflection_history_accumulated(self):
        """反思结果应被记录到历史"""
        self.engine.micro_reflect("x", {}, None, success=False, turn=1)
        self.engine.micro_reflect("y", {}, None, success=False, turn=2)
        # 只有非 PASS 的会记录
        self.assertGreaterEqual(len(self.engine.reflection_history), 2)

    def test_pass_not_recorded(self):
        """PASS 的 micro reflection 不应记录到历史"""
        self.engine.micro_reflect(
            "extract_text", {}, "good result", success=True, turn=1
        )
        micros = [r for r in self.engine.reflection_history if r["level"] == "micro"]
        self.assertEqual(len(micros), 0)

    def test_micro_anomaly_rate(self):
        """get_micro_anomaly_rate 统计"""
        # 制造一些失败
        for i in range(5):
            self.engine.micro_reflect("x", {}, None, success=False, turn=i)
        rate = self.engine.get_micro_anomaly_rate(last_n=10)
        self.assertGreater(rate, 0)


if __name__ == "__main__":
    unittest.main()
