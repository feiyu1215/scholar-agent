"""
tests/test_v2_hypothesis.py — HD-WM (Hypothesis-Driven Working Memory) 测试

Phase 5: 验证假说生命周期管理、工具集成、assembler section、退化行为。

测试分组:
    - TestHypothesisModule: hypothesis.py 数据结构 + 生命周期
    - TestHypothesisTools: harness.py 中 3 个假说工具的集成
    - TestHypothesisSection: assembler.py 中 hypothesis_status section 的条件注入
    - TestHDWMDegradation: HD-WM 关闭时的干净退化
"""

import unittest

from core.hypothesis import (
    HypothesisModule,
    HypothesisStatus,
    EvidenceType,
    Evidence,
    Hypothesis,
)
from core.harness import Harness


class TestHypothesisModule(unittest.TestCase):
    """hypothesis.py 核心逻辑测试。"""

    def setUp(self):
        self.module = HypothesisModule()

    # --- Generate ---

    def test_generate_creates_hypothesis(self):
        hyp = self.module.generate("baseline 不公平", "experiments", turn=1)
        self.assertEqual(hyp.id, "H001")
        self.assertEqual(hyp.statement, "baseline 不公平")
        self.assertEqual(hyp.source, "experiments")
        self.assertEqual(hyp.status, HypothesisStatus.ACTIVE)
        self.assertEqual(hyp.created_at_turn, 1)
        self.assertIsNone(hyp.resolved_at_turn)

    def test_generate_sequential_ids(self):
        h1 = self.module.generate("假说1", "sec1")
        h2 = self.module.generate("假说2", "sec2")
        h3 = self.module.generate("假说3", "sec3")
        self.assertEqual(h1.id, "H001")
        self.assertEqual(h2.id, "H002")
        self.assertEqual(h3.id, "H003")

    def test_generate_resets_saturation_counter(self):
        self.module.tick(1)
        self.module.tick(2)
        self.module.tick(3)
        self.assertEqual(self.module._turns_since_last_hypothesis, 3)
        self.module.generate("test", "sec1", turn=4)
        self.assertEqual(self.module._turns_since_last_hypothesis, 0)

    # --- Add Evidence ---

    def test_add_evidence_for(self):
        hyp = self.module.generate("test", "sec1")
        ev = self.module.add_evidence(hyp.id, "evidence content", "for", 0.8)
        self.assertIsNotNone(ev)
        self.assertEqual(len(hyp.evidence_for), 1)
        self.assertEqual(len(hyp.evidence_against), 0)
        self.assertEqual(ev.direction, "for")
        self.assertEqual(ev.strength, 0.8)

    def test_add_evidence_against(self):
        hyp = self.module.generate("test", "sec1")
        self.module.add_evidence(hyp.id, "contra evidence", "against", 0.6)
        self.assertEqual(len(hyp.evidence_against), 1)
        self.assertAlmostEqual(hyp.evidence_balance, -1.0)

    def test_add_evidence_to_nonexistent_hypothesis(self):
        result = self.module.add_evidence("H999", "content", "for", 0.5)
        self.assertIsNone(result)

    def test_add_evidence_to_resolved_hypothesis(self):
        hyp = self.module.generate("test", "sec1")
        self.module.resolve(hyp.id, "supported")
        result = self.module.add_evidence(hyp.id, "late evidence", "for", 0.9)
        self.assertIsNone(result)

    def test_add_evidence_invalid_direction(self):
        hyp = self.module.generate("test", "sec1")
        result = self.module.add_evidence(hyp.id, "content", "maybe", 0.5)
        self.assertIsNone(result)

    def test_add_evidence_clamps_strength(self):
        hyp = self.module.generate("test", "sec1")
        ev1 = self.module.add_evidence(hyp.id, "strong", "for", 1.5)
        self.assertEqual(ev1.strength, 1.0)
        ev2 = self.module.add_evidence(hyp.id, "weak", "against", -0.3)
        self.assertEqual(ev2.strength, 0.0)

    # --- Resolve ---

    def test_resolve_supported(self):
        hyp = self.module.generate("test", "sec1")
        success = self.module.resolve(hyp.id, "supported", "证据充分", turn=5)
        self.assertTrue(success)
        self.assertEqual(hyp.status, HypothesisStatus.SUPPORTED)
        self.assertEqual(hyp.resolved_at_turn, 5)
        self.assertEqual(hyp.resolution_reason, "证据充分")

    def test_resolve_refuted(self):
        hyp = self.module.generate("test", "sec1")
        success = self.module.resolve(hyp.id, "refuted", "作者论证有效")
        self.assertTrue(success)
        self.assertEqual(hyp.status, HypothesisStatus.REFUTED)

    def test_resolve_suspended(self):
        hyp = self.module.generate("test", "sec1")
        success = self.module.resolve(hyp.id, "suspended", "证据不足")
        self.assertTrue(success)
        self.assertEqual(hyp.status, HypothesisStatus.SUSPENDED)

    def test_resolve_invalid_status(self):
        hyp = self.module.generate("test", "sec1")
        success = self.module.resolve(hyp.id, "invalid_status")
        self.assertFalse(success)
        self.assertEqual(hyp.status, HypothesisStatus.ACTIVE)

    def test_resolve_nonexistent(self):
        success = self.module.resolve("H999", "supported")
        self.assertFalse(success)

    def test_resolve_already_resolved(self):
        hyp = self.module.generate("test", "sec1")
        self.module.resolve(hyp.id, "supported")
        success = self.module.resolve(hyp.id, "refuted")
        self.assertFalse(success)
        self.assertEqual(hyp.status, HypothesisStatus.SUPPORTED)

    # --- Evidence Balance ---

    def test_evidence_balance_empty(self):
        hyp = self.module.generate("test", "sec1")
        self.assertEqual(hyp.evidence_balance, 0.0)

    def test_evidence_balance_all_for(self):
        hyp = self.module.generate("test", "sec1")
        self.module.add_evidence(hyp.id, "e1", "for", 0.8)
        self.module.add_evidence(hyp.id, "e2", "for", 0.6)
        self.assertAlmostEqual(hyp.evidence_balance, 1.0)

    def test_evidence_balance_mixed(self):
        hyp = self.module.generate("test", "sec1")
        self.module.add_evidence(hyp.id, "e1", "for", 0.8)
        self.module.add_evidence(hyp.id, "e2", "against", 0.4)
        # (0.8 - 0.4) / (0.8 + 0.4) = 0.4/1.2 ≈ 0.333
        expected = (0.8 - 0.4) / (0.8 + 0.4)
        self.assertAlmostEqual(hyp.evidence_balance, expected, places=3)

    # --- Review Readiness ---

    def test_review_readiness_empty(self):
        self.assertEqual(self.module.review_readiness, 0.0)

    def test_review_readiness_one_resolved(self):
        h1 = self.module.generate("h1", "sec1")
        self.module.resolve(h1.id, "supported")
        # 1 total, 1 resolved: resolution_rate=1.0, coverage=min(1/3,1)=0.333
        # readiness = 1.0*0.7 + 0.333*0.3 = 0.7 + 0.1 = 0.8
        self.assertAlmostEqual(self.module.review_readiness, 0.8, places=2)

    def test_review_readiness_three_all_resolved(self):
        for i in range(3):
            h = self.module.generate(f"h{i}", f"sec{i}")
            self.module.resolve(h.id, "supported")
        # resolution_rate=1.0, coverage=min(3/3,1)=1.0
        # readiness = 1.0*0.7 + 1.0*0.3 = 1.0
        self.assertAlmostEqual(self.module.review_readiness, 1.0)

    def test_review_readiness_partial(self):
        h1 = self.module.generate("h1", "sec1")
        h2 = self.module.generate("h2", "sec2")
        h3 = self.module.generate("h3", "sec3")
        self.module.resolve(h1.id, "supported")
        # 3 total, 1 resolved: rate=1/3, coverage=min(3/3,1)=1.0
        # readiness = 0.333*0.7 + 1.0*0.3 = 0.233 + 0.3 = 0.533
        expected = (1/3)*0.7 + 1.0*0.3
        self.assertAlmostEqual(self.module.review_readiness, expected, places=2)

    def test_is_ready_threshold(self):
        # Need readiness >= 0.8
        self.assertFalse(self.module.is_ready)
        # 3 hypotheses all resolved => readiness=1.0 (well above threshold)
        for i in range(3):
            h = self.module.generate(f"h{i}", f"sec{i}")
            self.module.resolve(h.id, "supported")
        self.assertTrue(self.module.is_ready)

    # --- Saturation ---

    def test_saturation_not_triggered_initially(self):
        self.assertFalse(self.module.is_saturated)

    def test_saturation_after_window(self):
        self.module.generate("h1", "sec1", turn=0)
        # 3 ticks without generating
        self.module.tick(1)
        self.module.tick(2)
        self.assertFalse(self.module.is_saturated)
        self.module.tick(3)
        self.assertTrue(self.module.is_saturated)

    def test_saturation_reset_on_generate(self):
        self.module.tick(1)
        self.module.tick(2)
        self.module.tick(3)
        self.assertTrue(self.module.is_saturated)
        self.module.generate("new hypothesis", "sec2", turn=4)
        self.assertFalse(self.module.is_saturated)

    # --- Serialization ---

    def test_to_dict(self):
        hyp = self.module.generate("test statement", "sec1", turn=2)
        self.module.add_evidence(hyp.id, "e1", "for", 0.7)
        d = hyp.to_dict()
        self.assertEqual(d["id"], "H001")
        self.assertEqual(d["statement"], "test statement")
        self.assertEqual(d["status"], "active")
        self.assertEqual(d["evidence_count"], 1)
        self.assertEqual(d["evidence_for_count"], 1)
        self.assertEqual(d["evidence_against_count"], 0)

    # --- Format Status ---

    def test_format_status_empty(self):
        self.assertEqual(self.module.format_status(), "")

    def test_format_status_with_hypotheses(self):
        self.module.generate("baseline 不公平", "experiments")
        status = self.module.format_status()
        self.assertIn("假说工作记忆", status)
        self.assertIn("活跃 1", status)
        self.assertIn("baseline 不公平", status)

    # --- Reset ---

    def test_reset_clears_all(self):
        self.module.generate("h1", "sec1")
        self.module.tick(1)
        self.module.reset()
        self.assertEqual(len(self.module.hypotheses), 0)
        self.assertEqual(self.module._turns_since_last_hypothesis, 0)
        self.assertEqual(self.module.review_readiness, 0.0)


class TestHypothesisTools(unittest.TestCase):
    """harness.py 中 3 个假说工具的集成测试。"""

    def setUp(self):
        self.harness = Harness(enable_hdwm=True)

    def test_generate_hypothesis_basic(self):
        result = self.harness.execute_tool("generate_hypothesis", {
            "statement": "方法描述不够清晰",
            "source": "methodology",
        })
        self.assertIn("H001", result)
        self.assertIn("方法描述不够清晰", result)
        self.assertIn("active", result)

    def test_generate_hypothesis_missing_statement(self):
        result = self.harness.execute_tool("generate_hypothesis", {
            "source": "experiments",
        })
        self.assertIn("需要 statement 参数", result)

    def test_add_evidence_basic(self):
        self.harness.execute_tool("generate_hypothesis", {
            "statement": "test", "source": "sec1",
        })
        result = self.harness.execute_tool("add_evidence", {
            "hyp_id": "H001",
            "content": "表1只有2个baseline",
            "direction": "for",
            "strength": 0.8,
        })
        self.assertIn("证据已添加", result)
        self.assertIn("+1/-0", result)

    def test_add_evidence_missing_params(self):
        result = self.harness.execute_tool("add_evidence", {
            "content": "some evidence",
            "direction": "for",
            "strength": 0.5,
        })
        self.assertIn("需要 hyp_id 参数", result)

    def test_add_evidence_invalid_direction(self):
        self.harness.execute_tool("generate_hypothesis", {
            "statement": "test", "source": "sec1",
        })
        result = self.harness.execute_tool("add_evidence", {
            "hyp_id": "H001",
            "content": "evidence",
            "direction": "maybe",
            "strength": 0.5,
        })
        self.assertIn("direction 必须是", result)

    def test_resolve_hypothesis_basic(self):
        self.harness.execute_tool("generate_hypothesis", {
            "statement": "test", "source": "sec1",
        })
        result = self.harness.execute_tool("resolve_hypothesis", {
            "hyp_id": "H001",
            "status": "supported",
            "reason": "证据充分",
        })
        self.assertIn("已解决", result)
        self.assertIn("supported", result)
        self.assertIn("完成度", result)

    def test_resolve_hypothesis_invalid_status(self):
        self.harness.execute_tool("generate_hypothesis", {
            "statement": "test", "source": "sec1",
        })
        result = self.harness.execute_tool("resolve_hypothesis", {
            "hyp_id": "H001",
            "status": "maybe",
            "reason": "test",
        })
        self.assertIn("status 必须是", result)

    def test_full_lifecycle(self):
        """完整生命周期: generate → add_evidence x2 → resolve"""
        self.harness.execute_tool("generate_hypothesis", {
            "statement": "实验数据可能有泄露",
            "source": "experiments",
        })
        self.harness.execute_tool("add_evidence", {
            "hyp_id": "H001",
            "content": "训练集和测试集有重叠",
            "direction": "for",
            "strength": 0.9,
        })
        self.harness.execute_tool("add_evidence", {
            "hyp_id": "H001",
            "content": "作者声称已做了去重",
            "direction": "against",
            "strength": 0.4,
        })
        result = self.harness.execute_tool("resolve_hypothesis", {
            "hyp_id": "H001",
            "status": "supported",
            "reason": "去重声称缺乏细节，泄露风险仍高",
        })
        self.assertIn("supported", result)
        # Check module state
        module = self.harness.hypothesis_module
        self.assertEqual(module.resolution_rate, 1.0)
        # 1 hypothesis resolved: readiness ~ 0.8 (floating point may be slightly under)
        self.assertGreaterEqual(module.review_readiness, 0.79)

    def test_tools_registered_in_correct_phases(self):
        """验证假说工具在正确的阶段可见（Phase 10: 收窄为 deep_review only）。"""
        registry = self.harness.tool_registry
        # Phase 10: 所有 HD-WM 工具收窄为 deep_review only（可选高级工具）
        # 主要数据来源已改为 update_findings 的 _hdwm_auto_enhance 内部增强层
        gen_tool = registry._tools["generate_hypothesis"]
        self.assertEqual(gen_tool.phases, {"deep_review"})
        add_tool = registry._tools["add_evidence"]
        self.assertEqual(add_tool.phases, {"deep_review"})
        res_tool = registry._tools["resolve_hypothesis"]
        self.assertEqual(res_tool.phases, {"deep_review"})


class TestHypothesisSection(unittest.TestCase):
    """assembler.py 中 hypothesis_status section 的条件注入测试。"""

    def setUp(self):
        self.harness = Harness(enable_hdwm=True)

    def test_section_not_injected_when_no_hypotheses(self):
        """无假说时不注入 section。"""
        context = self.harness.assembler.assemble(
            state=self.harness.state,
            current_turn=0,
        )
        self.assertNotIn("假说工作记忆", context)

    def test_section_injected_when_hypotheses_exist(self):
        """有假说时注入 section。"""
        self.harness.hypothesis_module.generate("test hypothesis", "abstract", turn=1)
        context = self.harness.assembler.assemble(
            state=self.harness.state,
            current_turn=1,
        )
        self.assertIn("假说工作记忆", context)
        self.assertIn("test hypothesis", context)

    def test_section_shows_resolution_state(self):
        """Section 正确显示已解决假说。"""
        module = self.harness.hypothesis_module
        h = module.generate("实验设置有问题", "methodology", turn=1)
        module.add_evidence(h.id, "样本量太小", "for", 0.7)
        module.resolve(h.id, "supported", "确认有问题", turn=3)
        context = self.harness.assembler.assemble(
            state=self.harness.state,
            current_turn=3,
        )
        self.assertIn("解决率", context)
        self.assertIn("100%", context)


class TestHDWMDegradation(unittest.TestCase):
    """HD-WM 关闭时的干净退化。"""

    def test_no_hypothesis_module_when_disabled(self):
        h = Harness(enable_hdwm=False)
        self.assertIsNone(h.hypothesis_module)

    def test_standard_tool_count_when_disabled(self):
        h = Harness(enable_hdwm=False)
        tool_names = list(h.tool_registry._tools.keys())
        self.assertNotIn("generate_hypothesis", tool_names)
        self.assertNotIn("add_evidence", tool_names)
        self.assertNotIn("resolve_hypothesis", tool_names)
        # 25 base + 1 apply_skill (SkillX) = 26 when GODEL_SKILLX_ENABLED
        self.assertGreaterEqual(len(tool_names), 25)

    def test_extra_tools_when_enabled(self):
        h = Harness(enable_hdwm=True)
        tool_names = list(h.tool_registry._tools.keys())
        self.assertIn("generate_hypothesis", tool_names)
        self.assertIn("add_evidence", tool_names)
        self.assertIn("resolve_hypothesis", tool_names)
        # 28 base + 1 apply_skill (SkillX) = 29 when GODEL_SKILLX_ENABLED
        self.assertGreaterEqual(len(tool_names), 28)

    def test_no_hypothesis_section_when_disabled(self):
        """HD-WM 关闭时 assembler 不注入 hypothesis section。"""
        h = Harness(enable_hdwm=False)
        context = h.assembler.assemble(state=h.state, current_turn=0)
        self.assertNotIn("假说工作记忆", context)

    def test_assembler_works_normally_when_disabled(self):
        """HD-WM 关闭时 assembler 的其他功能不受影响。"""
        h = Harness(enable_hdwm=False)
        context = h.assembler.assemble(state=h.state, current_turn=0)
        # 应该至少有静态身份
        self.assertTrue(len(context) > 0)

    def test_default_is_disabled(self):
        """默认构造的 Harness 不激活 HD-WM。"""
        h = Harness()
        self.assertFalse(h.enable_hdwm)
        self.assertIsNone(h.hypothesis_module)


if __name__ == "__main__":
    unittest.main()
