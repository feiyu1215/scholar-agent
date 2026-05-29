"""
tests/test_phase12_hdwm_integrity_loop.py — Phase 12: HD-WM Integrity E2E Loop Validation

验证 Phase 10+11 联合效果在 cognitive_loop 中的实际行为:

    1. 正确路径: Agent read → needs_verification → read_section → verified → resolve → exit
    2. 绕过路径被阻断: Agent read → needs_verification → 直接 verified (无调查) → 假说未 resolve → gate 拦截
    3. 被引导后的正确修复: nudge 后 Agent 去做调查 → 再标 verified → resolve → exit
    4. 完整审稿流程: 多条 findings 混合 (suggestion + needs_verification + verified) 的正确处理

设计哲学:
    使用 cognitive_loop + MockLLMClient 模拟完整的 Agent 行为序列。
    验证的是系统级行为（loop + harness + hdwm + gate 协同），不是单个方法。

运行: pytest tests/test_phase12_hdwm_integrity_loop.py -v
"""

import asyncio
import unittest

from core.loop import cognitive_loop, LoopDone, LoopDoomStop
from core.harness import Harness

# 防止 dotenv 环境污染
import core.checker as _checker_mod
_checker_mod.CHECKER_ENABLED = False


def _run(coro):
    """运行异步协程。"""
    return asyncio.run(coro)


class MockLLMClient:
    """可编程的 LLM Client mock。"""

    def __init__(self, responses: list[dict]):
        self._responses = list(responses)
        self._call_count = 0
        self.call_history: list[list[dict]] = []

    async def chat_with_tools(self, **kwargs) -> dict:
        self.call_history.append(list(kwargs.get("messages", [])))
        if self._call_count >= len(self._responses):
            return {"content": "", "tool_calls": [], "usage": {}}
        resp = self._responses[self._call_count]
        self._call_count += 1
        return resp


# ============================================================
# Test: 正确路径 — 调查后标 verified → 假说 resolve → clean exit
# ============================================================

class TestCorrectPathResolves(unittest.TestCase):
    """Agent 做了调查后标 verified，假说自动 resolve，gate 放行退出。"""

    def test_investigate_then_verify_resolves_and_exits(self):
        """
        完整正确路径:
        T1: update_findings(needs_verification) → 假说 H001 自动生成
        T2: read_section(methods) → 实质调查
        T3: update_findings(verified, _hdwm_hyp_id=H001) → 通过完整性检查 → resolve
        T4: mark_complete → gate 放行（无活跃假说）→ LoopDone
        """
        harness = Harness(enable_hdwm=True, max_loop_turns=10)

        responses = [
            # T1: 记录待验证发现
            {
                "content": "发现方法论问题",
                "tool_calls": [{
                    "id": "tc1",
                    "name": "update_findings",
                    "arguments": {
                        "finding": "The baseline comparison should use same hyperparameter tuning budgets",
                        "status": "needs_verification",
                        "priority": "high",
                        "section": "experiments",
                        "evidence": "Table 2 shows baseline uses grid search while proposed method uses Bayesian optimization with 3x budget.",
                    },
                }],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            },
            # T2: 调查原文
            {
                "content": "追查实验部分",
                "tool_calls": [{
                    "id": "tc2",
                    "name": "read_section",
                    "arguments": {"section_name": "experiments", "offset": 0},
                }],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            },
            # T3: 确认验证
            {
                "content": "已确认问题存在",
                "tool_calls": [{
                    "id": "tc3",
                    "name": "update_findings",
                    "arguments": {
                        "finding": "The baseline comparison should use same hyperparameter tuning budgets",
                        "status": "verified",
                        "priority": "high",
                        "section": "experiments",
                        "evidence": "Table 2 confirms baseline uses grid search while proposed uses Bayesian opt with 3x budget; Section 4.1 para 2 acknowledges this difference.",
                        "_hdwm_hyp_id": "H001",
                    },
                }],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            },
            # T4: 退出
            {
                "content": "",
                "tool_calls": [{
                    "id": "tc4",
                    "name": "mark_complete",
                    "arguments": {"summary": "审阅完成，已验证关键方法论问题"},
                }],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            },
        ]
        client = MockLLMClient(responses)
        messages = [
            {"role": "system", "content": "test system"},
            {"role": "user", "content": "审稿"},
        ]

        result = _run(cognitive_loop(
            messages=messages, harness=harness, tools=[], client=client, verbose=False,
        ))

        # 验证: 正常退出
        self.assertIsInstance(result, LoopDone)
        self.assertEqual(harness.state.loop_turns, 4)

        # 验证: 假说已 resolve
        hyp = harness.hypothesis_module.get_hypothesis("H001")
        self.assertTrue(hyp.is_resolved)
        self.assertEqual(hyp.status.value, "supported")

        # 验证: findings 正常记录（Phase P1: 状态升级原地更新，不追加新记录）
        self.assertEqual(len(harness.state.findings), 1)  # 原记录被原地升级为 verified
        self.assertEqual(harness.state.findings[0]["status"], "verified")


# ============================================================
# Test: 绕过路径被阻断 — 无调查直接 verified → 假说未 resolve → gate 拦截
# ============================================================

class TestBypassPathBlocked(unittest.TestCase):
    """Agent 试图绕过（无调查直接标 verified）时，系统正确阻断。"""

    def test_verify_without_investigation_keeps_hypothesis_active(self):
        """
        绕过路径:
        T1: update_findings(needs_verification) → H001 生成
        T2: update_findings(verified) 直接绕过（无 read_section）→ 完整性提示
        T3: mark_complete → gate 拦截（活跃假说未 resolve）
        T4: Agent 去做 read_section（被引导后）
        T5: update_findings(verified) → 这次通过完整性检查 → resolve
        T6: mark_complete → 通过
        """
        harness = Harness(enable_hdwm=True, max_loop_turns=10)

        responses = [
            # T1: needs_verification
            {
                "content": "初步判断",
                "tool_calls": [{
                    "id": "tc1",
                    "name": "update_findings",
                    "arguments": {
                        "finding": "Statistical significance tests should use Bonferroni correction for multiple comparisons",
                        "status": "needs_verification",
                        "priority": "high",
                        "section": "results",
                        "evidence": "Table 4 reports 12 hypothesis tests without any multiple testing correction mentioned.",
                    },
                }],
                "usage": {},
            },
            # T2: 直接标 verified（无调查）→ 收到完整性提示
            {
                "content": "直接确认",
                "tool_calls": [{
                    "id": "tc2",
                    "name": "update_findings",
                    "arguments": {
                        "finding": "Statistical significance tests should use Bonferroni correction for multiple comparisons",
                        "status": "verified",
                        "priority": "high",
                        "section": "results",
                        "evidence": "Table 4 reports 12 tests without correction; this inflates family-wise error rate above 0.05.",
                        "_hdwm_hyp_id": "H001",
                    },
                }],
                "usage": {},
            },
            # T3: mark_complete → gate 拦截（假说仍活跃）
            {
                "content": "",
                "tool_calls": [{
                    "id": "tc3",
                    "name": "mark_complete",
                    "arguments": {"summary": "done"},
                }],
                "usage": {},
            },
            # T4: Agent 被引导去调查
            {
                "content": "追查原文",
                "tool_calls": [{
                    "id": "tc4",
                    "name": "read_section",
                    "arguments": {"section_name": "results", "offset": 0},
                }],
                "usage": {},
            },
            # T5: 再次标 verified（这次有调查记录）
            {
                "content": "确认已验证",
                "tool_calls": [{
                    "id": "tc5",
                    "name": "update_findings",
                    "arguments": {
                        "finding": "Statistical significance tests should use Bonferroni correction for multiple comparisons",
                        "status": "verified",
                        "priority": "high",
                        "section": "results",
                        "evidence": "Results Section 4.2 Table 4 reports 12 hypothesis tests without multiple testing correction; confirmed via re-reading.",
                        "_hdwm_hyp_id": "H001",
                    },
                }],
                "usage": {},
            },
            # T6: mark_complete → 通过
            {
                "content": "",
                "tool_calls": [{
                    "id": "tc6",
                    "name": "mark_complete",
                    "arguments": {"summary": "已追查验证，审阅完成"},
                }],
                "usage": {},
            },
        ]
        client = MockLLMClient(responses)
        messages = [
            {"role": "system", "content": "test system"},
            {"role": "user", "content": "审稿"},
        ]

        result = _run(cognitive_loop(
            messages=messages, harness=harness, tools=[], client=client, verbose=False,
        ))

        # 验证: 最终正常退出
        self.assertIsInstance(result, LoopDone)
        self.assertEqual(harness.state.loop_turns, 6)

        # 验证: 假说最终 resolve
        hyp = harness.hypothesis_module.get_hypothesis("H001")
        self.assertTrue(hyp.is_resolved)

        # 验证: T2 时假说未 resolve（通过检查 tool 结果中的完整性提示）
        # 我们通过检查 call_history 中 T3 发送给 LLM 的 messages 来验证
        # T3 的 messages 应该包含 gate nudge（因为 T2 没有 resolve 假说）
        t3_messages = client.call_history[2]  # 第3次调用 LLM 时传入的 messages
        # gate nudge 应该在 T3 的 mark_complete 结果中（作为 tool_result 返回给 LLM）
        # 实际上 nudge 是 mark_complete 的返回值，会被注入 messages
        t4_messages = client.call_history[3]  # T4 收到了 mark_complete 的 nudge 结果
        all_content = " ".join(
            m.get("content", "") for m in t4_messages if m.get("role") == "tool"
        )
        # gate nudge 应提及 "needs_verification"（unverified 类型 nudge）
        # 因为此时原 finding 仍为 needs_verification（完整性检查阻止了状态同步）
        self.assertIn("needs_verification", all_content)


# ============================================================
# Test: 多 findings 混合场景
# ============================================================

class TestMixedFindingsFlow(unittest.TestCase):
    """多种 status 的 findings 混合时，系统正确处理。"""

    def test_suggestion_findings_dont_trigger_integrity_check(self):
        """
        suggestion findings 不触发完整性检查，只有 needs_verification→verified 路径受影响。
        T1: update_findings(suggestion) → 正常记录
        T2: update_findings(needs_verification) → 假说生成
        T3: read_section → 调查
        T4: update_findings(verified) → resolve
        T5: mark_complete → exit
        """
        harness = Harness(enable_hdwm=True, max_loop_turns=10)

        responses = [
            # T1: suggestion (无 HD-WM 影响)
            {
                "content": "发现格式问题",
                "tool_calls": [{
                    "id": "tc1",
                    "name": "update_findings",
                    "arguments": {
                        "finding": "Figure 3 caption should clarify the y-axis units",
                        "status": "suggestion",
                        "priority": "low",
                        "section": "figures",
                        "evidence": "Figure 3 shows 'Performance' on y-axis without specifying if this is accuracy, F1, or AUC.",
                    },
                }],
                "usage": {},
            },
            # T2: needs_verification → 假说
            {
                "content": "发现方法论问题",
                "tool_calls": [{
                    "id": "tc2",
                    "name": "update_findings",
                    "arguments": {
                        "finding": "The loss function formulation should be consistent with the stated objective in Eq. 2",
                        "status": "needs_verification",
                        "priority": "high",
                        "section": "methods",
                        "evidence": "Eq. 5 minimizes cross-entropy but Section 3.1 states the objective is to maximize mutual information.",
                    },
                }],
                "usage": {},
            },
            # T3: 调查
            {
                "content": "读方法论章节",
                "tool_calls": [{
                    "id": "tc3",
                    "name": "read_section",
                    "arguments": {"section_name": "methods", "offset": 0},
                }],
                "usage": {},
            },
            # T4: verified
            {
                "content": "确认问题",
                "tool_calls": [{
                    "id": "tc4",
                    "name": "update_findings",
                    "arguments": {
                        "finding": "The loss function formulation should be consistent with the stated objective in Eq. 2",
                        "status": "verified",
                        "priority": "high",
                        "section": "methods",
                        "evidence": "Confirmed: Eq. 5 uses cross-entropy loss but Section 3.1 para 3 explicitly states mutual information maximization as the objective.",
                        "_hdwm_hyp_id": "H001",
                    },
                }],
                "usage": {},
            },
            # T5: exit
            {
                "content": "",
                "tool_calls": [{
                    "id": "tc5",
                    "name": "mark_complete",
                    "arguments": {"summary": "审阅完成"},
                }],
                "usage": {},
            },
        ]
        client = MockLLMClient(responses)
        messages = [
            {"role": "system", "content": "test system"},
            {"role": "user", "content": "审稿"},
        ]

        result = _run(cognitive_loop(
            messages=messages, harness=harness, tools=[], client=client, verbose=False,
        ))

        self.assertIsInstance(result, LoopDone)
        self.assertEqual(harness.state.loop_turns, 5)

        # suggestion finding 不生成假说（只有 H001 从 needs_verification）
        self.assertEqual(len(harness.hypothesis_module.hypotheses), 1)
        hyp = harness.hypothesis_module.get_hypothesis("H001")
        self.assertTrue(hyp.is_resolved)

        # Phase P1: suggestion + needs_verification(原地升级为verified) = 2 条
        self.assertEqual(len(harness.state.findings), 2)


# ============================================================
# Test: search_literature 也算调查行为
# ============================================================

class TestSearchLiteratureCountsAsInvestigation(unittest.TestCase):
    """search_literature 同样满足完整性约束。"""

    def test_search_literature_satisfies_integrity(self):
        """
        T1: needs_verification → H001
        T2: search_literature → 调查
        T3: verified → resolve
        T4: mark_complete → exit
        """
        harness = Harness(enable_hdwm=True, max_loop_turns=10)

        responses = [
            {
                "content": "需要验证引用",
                "tool_calls": [{
                    "id": "tc1",
                    "name": "update_findings",
                    "arguments": {
                        "finding": "The cited prior work [12] should support the claimed 15% improvement over baseline",
                        "status": "needs_verification",
                        "priority": "high",
                        "section": "related_work",
                        "evidence": "Section 2 para 4 claims [12] achieves 85% accuracy but [12] abstract reports only 78% on same benchmark.",
                    },
                }],
                "usage": {},
            },
            {
                "content": "搜索相关文献",
                "tool_calls": [{
                    "id": "tc2",
                    "name": "search_literature",
                    "arguments": {"query": "claimed improvement baseline comparison"},
                }],
                "usage": {},
            },
            {
                "content": "确认引用问题",
                "tool_calls": [{
                    "id": "tc3",
                    "name": "update_findings",
                    "arguments": {
                        "finding": "The cited prior work [12] should support the claimed 15% improvement over baseline",
                        "status": "verified",
                        "priority": "high",
                        "section": "related_work",
                        "evidence": "Confirmed via literature search: [12] reports 78% accuracy, not 85% as claimed in Section 2 paragraph 4.",
                        "_hdwm_hyp_id": "H001",
                    },
                }],
                "usage": {},
            },
            {
                "content": "",
                "tool_calls": [{
                    "id": "tc4",
                    "name": "mark_complete",
                    "arguments": {"summary": "完成"},
                }],
                "usage": {},
            },
        ]
        client = MockLLMClient(responses)
        messages = [
            {"role": "system", "content": "test"},
            {"role": "user", "content": "审稿"},
        ]

        result = _run(cognitive_loop(
            messages=messages, harness=harness, tools=[], client=client, verbose=False,
        ))

        self.assertIsInstance(result, LoopDone)
        hyp = harness.hypothesis_module.get_hypothesis("H001")
        self.assertTrue(hyp.is_resolved)


# ============================================================
# Test: 第二次 mark_complete 强制放行（坚持退出）
# ============================================================

class TestSecondMarkCompleteForceExit(unittest.TestCase):
    """Agent 坚持退出时（第二次 mark_complete），系统放行。"""

    def test_persistent_exit_succeeds(self):
        """
        Gate 有两种 nudge 类型（unverified + hdwm_active），每种最多触发一次。
        但 loop 有 nudge cooldown 机制（P3 #19）：如果 Agent 在收到 nudge 后
        不到 2 轮就再次 mark_complete，系统视为"Agent 坚持退出"并放行。

        实际流程：
        T1: needs_verification → H001 (触发 unverified + hdwm_active 两种 nudge 条件)
        T2: mark_complete → gate nudge #1 (unverified high priority finding)
        T3: mark_complete → nudge cooldown 触发 (turn 间隔 < 2) → 系统放行

        设计意图：cooldown 是"坚持退出 = 放行"语义的实现。
        Agent 不需要穷尽所有 nudge 类型才能退出。
        """
        harness = Harness(enable_hdwm=True, max_loop_turns=10)

        responses = [
            # T1: 创建假说
            {
                "content": "问题",
                "tool_calls": [{
                    "id": "tc1",
                    "name": "update_findings",
                    "arguments": {
                        "finding": "Sample size too small for claimed statistical power",
                        "status": "needs_verification",
                        "priority": "high",
                        "section": "methods",
                    },
                }],
                "usage": {},
            },
            # T2: 第一次 mark_complete → unverified nudge
            {
                "content": "",
                "tool_calls": [{
                    "id": "tc2",
                    "name": "mark_complete",
                    "arguments": {"summary": "初步完成"},
                }],
                "usage": {},
            },
            # T3: 第二次 mark_complete → cooldown 放行
            {
                "content": "",
                "tool_calls": [{
                    "id": "tc3",
                    "name": "mark_complete",
                    "arguments": {"summary": "坚持退出"},
                }],
                "usage": {},
            },
        ]
        client = MockLLMClient(responses)
        messages = [
            {"role": "system", "content": "test"},
            {"role": "user", "content": "审稿"},
        ]

        result = _run(cognitive_loop(
            messages=messages, harness=harness, tools=[], client=client, verbose=False,
        ))

        # Agent 坚持退出（cooldown 放行），系统尊重
        self.assertIsInstance(result, LoopDone)
        self.assertEqual(harness.state.loop_turns, 3)

        # 但假说仍未 resolve（Agent 选择了跳过）
        hyp = harness.hypothesis_module.get_hypothesis("H001")
        self.assertFalse(hyp.is_resolved)


if __name__ == "__main__":
    unittest.main()
