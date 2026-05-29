"""
tests/test_phase11_verification_integrity.py — Phase 11: Verification Integrity Constraint

验证 _check_verification_integrity 的行为:
    1. Agent 做了调查（read_section/search_literature）后标 verified → 正常 resolve
    2. Agent 未做调查直接标 verified → 温和提醒，不自动 resolve
    3. 无匹配假说时 → 放行（无提醒）
    4. 模糊匹配路径 → 同样受完整性约束
    5. 假说已 resolved → 放行
    6. nudge 文本不含绕过提示

设计哲学:
    - 直接测试 Harness 方法，避免 full loop 的复杂性
    - 手动构造 state.tool_call_history 来模拟不同场景
"""

import unittest

from core.harness import Harness

# 防止 dotenv 环境污染
import core.checker as _checker_mod
_checker_mod.CHECKER_ENABLED = False


class TestVerificationIntegrityBasic(unittest.TestCase):
    """基础场景: 精确匹配 (_hdwm_hyp_id) 路径"""

    def _make_harness(self) -> Harness:
        harness = Harness(enable_hdwm=True, max_loop_turns=20)
        return harness

    def test_verified_without_investigation_triggers_hint(self):
        """Agent 未做调查直接标 verified → 返回完整性提示"""
        harness = self._make_harness()

        # Step 1: 模拟 update_findings(status=needs_verification)
        result1 = harness.execute_tool(
            "update_findings",
            {
                "finding": "The baseline comparison is unfair due to different hyperparameter tuning",
                "status": "needs_verification",
                "priority": "high",
                "section": "experiments",
            },
        )
        self.assertIn("[HD-WM]", result1)
        self.assertIn("H001", result1)

        # Step 2: Agent 直接标 verified（无 read_section / search_literature 调用）
        result2 = harness.execute_tool(
            "update_findings",
            {
                "finding": "The baseline comparison is unfair due to different hyperparameter tuning",
                "status": "verified",
                "priority": "high",
                "section": "experiments",
                "_hdwm_hyp_id": "H001",
            },
        )

        # 应该收到完整性提示
        self.assertIn("完整性提示", result2)
        self.assertIn("read_section", result2)
        self.assertIn("search_literature", result2)

        # 假说应该仍然是活跃的（未被 resolve）
        hyp = harness.hypothesis_module.get_hypothesis("H001")
        self.assertFalse(hyp.is_resolved)

    def test_verified_with_read_section_allows_resolve(self):
        """Agent 做了 read_section 后标 verified → 正常 resolve"""
        harness = self._make_harness()

        # Step 1: 创建 needs_verification finding
        harness.execute_tool(
            "update_findings",
            {
                "finding": "The baseline comparison is unfair due to different hyperparameter tuning",
                "status": "needs_verification",
                "priority": "high",
                "section": "experiments",
            },
        )

        # Step 2: Agent 做了 read_section 调查
        harness.execute_tool(
            "read_section",
            {"section_name": "experiments", "offset": 0},
        )

        # Step 3: 现在标 verified — 应该正常 resolve
        result = harness.execute_tool(
            "update_findings",
            {
                "finding": "The baseline comparison is unfair due to different hyperparameter tuning",
                "status": "verified",
                "priority": "high",
                "section": "experiments",
                "_hdwm_hyp_id": "H001",
            },
        )

        # 不应该有完整性提示
        self.assertNotIn("完整性提示", result)
        # 应该正常 resolve
        self.assertIn("验证完成", result)
        self.assertIn("supported", result)

        # 假说应该已 resolved
        hyp = harness.hypothesis_module.get_hypothesis("H001")
        self.assertTrue(hyp.is_resolved)

    def test_verified_with_search_literature_allows_resolve(self):
        """Agent 做了 search_literature 后标 verified → 正常 resolve"""
        harness = self._make_harness()

        # Step 1: 创建 needs_verification finding
        harness.execute_tool(
            "update_findings",
            {
                "finding": "The method lacks comparison with recent transformer-based approaches",
                "status": "needs_verification",
                "priority": "high",
                "section": "related_work",
            },
        )

        # Step 2: Agent 做了 search_literature 调查
        harness.execute_tool(
            "search_literature",
            {"query": "transformer comparison methods"},
        )

        # Step 3: 标 verified
        result = harness.execute_tool(
            "update_findings",
            {
                "finding": "The method lacks comparison with recent transformer-based approaches",
                "status": "verified",
                "priority": "high",
                "section": "related_work",
                "_hdwm_hyp_id": "H001",
            },
        )

        self.assertNotIn("完整性提示", result)
        self.assertIn("验证完成", result)

        hyp = harness.hypothesis_module.get_hypothesis("H001")
        self.assertTrue(hyp.is_resolved)


class TestVerificationIntegrityEdgeCases(unittest.TestCase):
    """边界场景"""

    def _make_harness(self) -> Harness:
        return Harness(enable_hdwm=True, max_loop_turns=20)

    def test_no_matching_hypothesis_passes_through(self):
        """没有匹配的活跃假说时，直接放行"""
        harness = self._make_harness()

        # 直接提交 verified finding（无对应的 needs_verification 前置）
        result = harness.execute_tool(
            "update_findings",
            {
                "finding": "A completely new observation with no prior hypothesis",
                "status": "verified",
                "priority": "medium",
                "section": "abstract",
            },
        )

        # 应该正常记录，无完整性提示
        self.assertNotIn("完整性提示", result)
        self.assertIn("已记录发现", result)

    def test_hypothesis_already_resolved_passes_through(self):
        """假说已 resolved 时，不再做完整性检查"""
        harness = self._make_harness()

        # Step 1: 创建 needs_verification
        harness.execute_tool(
            "update_findings",
            {
                "finding": "Some hypothesis that gets resolved early",
                "status": "needs_verification",
                "priority": "high",
                "section": "methods",
            },
        )

        # Step 2: 手动 resolve 假说（模拟 Agent 通过高级工具路径）
        harness.hypothesis_module.resolve(
            hyp_id="H001",
            status="supported",
            reason="Manually resolved",
            turn=harness.state.loop_turns,
        )

        # Step 3: 标 verified（假说已 resolved，应该放行）
        result = harness.execute_tool(
            "update_findings",
            {
                "finding": "Some hypothesis that gets resolved early",
                "status": "verified",
                "priority": "high",
                "section": "methods",
                "_hdwm_hyp_id": "H001",
            },
        )

        # 不应有完整性提示（假说已 resolved）
        self.assertNotIn("完整性提示", result)

    def test_hdwm_disabled_no_integrity_check(self):
        """HD-WM 关闭时，不做完整性检查"""
        harness = Harness(enable_hdwm=False)

        result = harness.execute_tool(
            "update_findings",
            {
                "finding": "Some verified finding",
                "status": "verified",
                "priority": "high",
                "section": "experiments",
            },
        )

        self.assertNotIn("完整性提示", result)
        self.assertIn("已记录发现", result)

    def test_investigation_before_hypothesis_does_not_count(self):
        """假说创建之前的 read_section 不算作调查"""
        harness = self._make_harness()

        # Step 1: 先做 read_section（在 hypothesis 创建之前）
        harness.execute_tool(
            "read_section",
            {"section_name": "abstract", "offset": 0},
        )

        # Step 2: 创建 needs_verification finding
        harness.execute_tool(
            "update_findings",
            {
                "finding": "The abstract overpromises results not supported by experiments",
                "status": "needs_verification",
                "priority": "high",
                "section": "abstract",
            },
        )

        # Step 3: 直接标 verified（之前的 read_section 不应该算）
        result = harness.execute_tool(
            "update_findings",
            {
                "finding": "The abstract overpromises results not supported by experiments",
                "status": "verified",
                "priority": "high",
                "section": "abstract",
                "_hdwm_hyp_id": "H001",
            },
        )

        # 应该触发完整性提示（前一个 read_section 在假说创建之前）
        self.assertIn("完整性提示", result)

        # 假说未 resolved
        hyp = harness.hypothesis_module.get_hypothesis("H001")
        self.assertFalse(hyp.is_resolved)


class TestVerificationIntegrityFuzzyMatch(unittest.TestCase):
    """模糊匹配路径下的完整性约束"""

    def _make_harness(self) -> Harness:
        return Harness(enable_hdwm=True, max_loop_turns=20)

    def test_fuzzy_match_without_investigation_triggers_hint(self):
        """模糊匹配路径: Agent 没做调查 → 触发完整性提示"""
        harness = self._make_harness()

        # Step 1: 创建 needs_verification (生成假说)
        harness.execute_tool(
            "update_findings",
            {
                "finding": "Gradient clipping threshold selection lacks theoretical justification",
                "status": "needs_verification",
                "priority": "high",
                "section": "methods",
            },
        )

        # Step 2: 用略微不同的措辞标 verified（无 _hdwm_hyp_id，走模糊匹配）
        result = harness.execute_tool(
            "update_findings",
            {
                "finding": "The gradient clipping threshold was selected without theoretical justification or ablation",
                "status": "verified",
                "priority": "high",
                "section": "methods",
            },
        )

        # 应该触发完整性提示（模糊匹配成功 + 无调查）
        self.assertIn("完整性提示", result)

    def test_fuzzy_match_with_investigation_allows_resolve(self):
        """模糊匹配路径: Agent 做了调查 → 正常 resolve"""
        harness = self._make_harness()

        # Step 1: 创建 needs_verification
        harness.execute_tool(
            "update_findings",
            {
                "finding": "Gradient clipping threshold selection lacks theoretical justification",
                "status": "needs_verification",
                "priority": "high",
                "section": "methods",
            },
        )

        # Step 2: Agent 做调查
        harness.execute_tool(
            "read_section",
            {"section_name": "methods", "offset": 0},
        )

        # Step 3: 用略微不同措辞标 verified
        result = harness.execute_tool(
            "update_findings",
            {
                "finding": "The gradient clipping threshold was selected without theoretical justification or ablation",
                "status": "verified",
                "priority": "high",
                "section": "methods",
            },
        )

        # 不应有完整性提示
        self.assertNotIn("完整性提示", result)
        # 应该正常 resolve
        self.assertIn("验证完成", result)


class TestNudgeTextNoBypassHint(unittest.TestCase):
    """验证 nudge 文本不再含有绕过提示"""

    def _make_harness(self) -> Harness:
        return Harness(enable_hdwm=True, max_loop_turns=20)

    def test_unverified_nudge_no_bypass(self):
        """未验证高优发现的 nudge 不应提示直接标 verified 绕过"""
        harness = self._make_harness()

        # 创建一个 high + needs_verification 的 finding
        harness.execute_tool(
            "update_findings",
            {
                "finding": "Critical flaw in statistical methodology",
                "status": "needs_verification",
                "priority": "high",
                "section": "methods",
            },
        )

        # 尝试 mark_complete → 应触发 nudge
        result = harness.execute_tool(
            "mark_complete",
            {"summary": "Done reviewing"},
        )

        # nudge 应引导做调查
        self.assertIn("read_section", result)
        self.assertIn("search_literature", result)
        # 不应含有绕过提示
        self.assertNotIn("降级", result)
        self.assertNotIn("标记为 verified", result)

    def test_hdwm_active_nudge_no_bypass(self):
        """HD-WM 活跃假说的 nudge 不应提示绕过"""
        harness = self._make_harness()

        # 生成假说
        harness.hypothesis_module.generate(
            statement="Test hypothesis for nudge",
            source="methods",
            turn=0,
        )

        # mark_complete → nudge
        # 先确保没有 high+needs_verification 的 findings（避免触发第一个 nudge）
        result = harness.execute_tool(
            "mark_complete",
            {"summary": "Done"},
        )

        # 应该触发 HD-WM nudge（因为有活跃假说）
        self.assertIn("待验证判断", result)
        self.assertIn("read_section", result)
        self.assertIn("search_literature", result)
        # 不应含有绕过提示
        self.assertNotIn("降级", result)
        self.assertNotIn("或降级", result)


class TestVerificationIntegrityFindingStillRecorded(unittest.TestCase):
    """确认完整性约束不阻止 finding 记录"""

    def _make_harness(self) -> Harness:
        return Harness(enable_hdwm=True, max_loop_turns=20)

    def test_finding_recorded_even_when_integrity_fails(self):
        """即使完整性检查失败，finding 仍应该被记录到 state"""
        harness = self._make_harness()

        # Step 1: 创建 needs_verification
        harness.execute_tool(
            "update_findings",
            {
                "finding": "Experimental setup description is incomplete",
                "status": "needs_verification",
                "priority": "high",
                "section": "experiments",
            },
        )
        self.assertEqual(len(harness.state.findings), 1)

        # Step 2: 直接标 verified（无调查）
        result = harness.execute_tool(
            "update_findings",
            {
                "finding": "Experimental setup description is incomplete",
                "status": "verified",
                "priority": "high",
                "section": "experiments",
                "_hdwm_hyp_id": "H001",
            },
        )

        # 完整性提示应该出现
        self.assertIn("完整性提示", result)

        # Phase P1: 原地更新设计 — 完整性检查失败时不更新 status，也不追加新记录
        self.assertEqual(len(harness.state.findings), 1)
        # 状态保持 needs_verification（完整性检查阻止了升级）
        self.assertEqual(harness.state.findings[0]["status"], "needs_verification")


if __name__ == "__main__":
    unittest.main()
