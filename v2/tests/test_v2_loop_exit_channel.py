"""
tests/test_v2_loop_exit_channel.py — Phase 7: mark_complete 唯一出口测试

验证 loop.py 中 Phase 7 的核心设计：
    无 tool call 不等于退出。Agent 的唯一正式退出通道是 mark_complete，
    它经过 completion quality gate 检查。无 tool call 的文本被视为
    Agent 的"思考中间态"（类似 chain-of-thought），追加回 messages 后继续 loop。

    安全网由 doom loop guard 提供（max_loop_turns + 2 缓冲）。

设计原则：
    一个人在审稿时，脑子里冒出想法但还没落笔行动 ≠ 审完了。
    真正的结束是一个显式决定——对应 mark_complete。
"""

import asyncio
import unittest

from core.loop import cognitive_loop, LoopDone, LoopDoomStop
from core.harness import Harness

# 防止 dotenv 环境污染（其他测试加载 .env 后注入 API key）导致 Checker
# 在 mark_complete 时触发 nudge，干扰 loop 退出语义测试。
import core.checker as _checker_mod
_checker_mod.CHECKER_ENABLED = False


def _run_async(coro):
    """运行异步协程（每次创建独立 event loop，确保测试隔离）。"""
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
# 核心语义: 无 tool call = 思考中间态，不退出
# ============================================================

class TestNoToolCallIsNotExit(unittest.TestCase):
    """验证无 tool call 的文本不导致 loop 退出。"""

    def test_text_without_tool_call_continues_loop(self):
        """Agent 产出文本但不调工具时，loop 继续而非退出。"""
        harness = Harness(enable_hdwm=False, max_loop_turns=4)

        # 第1轮: 只有文本（思考中间态）→ 继续
        # 第2轮: mark_complete 退出
        responses = [
            {"content": "让我先思考一下论文的整体结构...", "tool_calls": [], "usage": {}},
            {
                "content": "",
                "tool_calls": [{"id": "tc_d", "name": "mark_complete",
                               "arguments": {"summary": "审阅完成"}}],
                "usage": {},
            },
        ]
        client = MockLLMClient(responses)
        messages = [{"role": "system", "content": "test"}, {"role": "user", "content": "审稿"}]

        result = _run_async(cognitive_loop(
            messages=messages, harness=harness, tools=[], client=client, verbose=False,
        ))

        # 关键断言: loop 跑了 2 轮（没有在第1轮退出）
        self.assertEqual(harness.state.loop_turns, 2)
        self.assertIsInstance(result, LoopDone)

    def test_thinking_text_appended_to_messages(self):
        """思考中间态的文本被追加回 messages（Agent 下轮能看到）。"""
        harness = Harness(enable_hdwm=False, max_loop_turns=4)

        thinking_text = "这篇论文的方法论部分需要仔细审查..."
        responses = [
            {"content": thinking_text, "tool_calls": [], "usage": {}},
            {
                "content": "",
                "tool_calls": [{"id": "tc_d", "name": "mark_complete",
                               "arguments": {"summary": "done"}}],
                "usage": {},
            },
        ]
        client = MockLLMClient(responses)
        messages = [{"role": "system", "content": "test"}, {"role": "user", "content": "go"}]

        _run_async(cognitive_loop(
            messages=messages, harness=harness, tools=[], client=client, verbose=False,
        ))

        # 思考文本应作为 assistant message 出现在 messages 中
        assistant_msgs = [m for m in messages if m.get("role") == "assistant"]
        self.assertTrue(any(thinking_text in (m.get("content") or "") for m in assistant_msgs))

    def test_multiple_thinking_turns_before_exit(self):
        """多轮思考中间态后 Agent 用 mark_complete 正式退出。"""
        harness = Harness(enable_hdwm=False, max_loop_turns=10)

        responses = [
            {"content": "第一步思考", "tool_calls": [], "usage": {}},
            {"content": "第二步推理", "tool_calls": [], "usage": {}},
            {"content": "第三步整理", "tool_calls": [], "usage": {}},
            {
                "content": "",
                "tool_calls": [{"id": "tc_d", "name": "mark_complete",
                               "arguments": {"summary": "经过三步思考完成审阅"}}],
                "usage": {},
            },
        ]
        client = MockLLMClient(responses)
        messages = [{"role": "system", "content": "test"}, {"role": "user", "content": "go"}]

        result = _run_async(cognitive_loop(
            messages=messages, harness=harness, tools=[], client=client, verbose=False,
        ))

        self.assertEqual(harness.state.loop_turns, 4)
        self.assertIsInstance(result, LoopDone)

    def test_empty_content_also_continues(self):
        """即使 content 为空也继续 loop（不退出）。"""
        harness = Harness(enable_hdwm=False, max_loop_turns=4)

        responses = [
            {"content": "", "tool_calls": [], "usage": {}},
            {
                "content": "",
                "tool_calls": [{"id": "tc_d", "name": "mark_complete",
                               "arguments": {"summary": "done"}}],
                "usage": {},
            },
        ]
        client = MockLLMClient(responses)
        messages = [{"role": "system", "content": "test"}, {"role": "user", "content": "go"}]

        result = _run_async(cognitive_loop(
            messages=messages, harness=harness, tools=[], client=client, verbose=False,
        ))

        self.assertEqual(harness.state.loop_turns, 2)
        self.assertIsInstance(result, LoopDone)


# ============================================================
# 唯一正式出口: mark_complete
# ============================================================

class TestMarkCompleteIsOnlyExit(unittest.TestCase):
    """Agent 只能通过 mark_complete 正式退出（经 quality gate）。"""

    def test_mark_complete_triggers_gate_and_exits(self):
        """mark_complete 走正式退出通道。"""
        harness = Harness(enable_hdwm=False)

        responses = [
            {
                "content": "",
                "tool_calls": [{"id": "tc_d", "name": "mark_complete",
                               "arguments": {"summary": "审阅完成，论文质量良好"}}],
                "usage": {},
            },
        ]
        client = MockLLMClient(responses)
        messages = [{"role": "system", "content": "test"}, {"role": "user", "content": "审稿"}]

        result = _run_async(cognitive_loop(
            messages=messages, harness=harness, tools=[], client=client, verbose=False,
        ))

        self.assertIsInstance(result, LoopDone)
        self.assertIn("论文质量良好", result.summary)

    def test_gate_nudges_back_on_active_hypotheses(self):
        """gate 检测到活跃假说时 nudge 回来，Agent 再次 mark_complete 才退出。"""
        harness = Harness(enable_hdwm=True)
        module = harness.hypothesis_module
        module.generate("未验证的统计方法假设", "methods", turn=0)

        # 第1轮: mark_complete → gate nudge (活跃假说)
        # 第2轮: resolve 假说
        # 第3轮: 再次 mark_complete → gate 通过
        responses = [
            {
                "content": "",
                "tool_calls": [{"id": "tc1", "name": "mark_complete",
                               "arguments": {"summary": "初步完成"}}],
                "usage": {},
            },
            {
                "content": "处理假说",
                "tool_calls": [{"id": "tc2", "name": "resolve_hypothesis",
                               "arguments": {"hyp_id": "H001", "status": "refuted",
                                            "reason": "数据不支持"}}],
                "usage": {},
            },
            {
                "content": "",
                "tool_calls": [{"id": "tc3", "name": "mark_complete",
                               "arguments": {"summary": "假说已解决，审阅完成"}}],
                "usage": {},
            },
        ]
        client = MockLLMClient(responses)
        messages = [{"role": "system", "content": "test"}, {"role": "user", "content": "审稿"}]

        result = _run_async(cognitive_loop(
            messages=messages, harness=harness, tools=[], client=client, verbose=False,
        ))

        self.assertEqual(harness.state.loop_turns, 3)
        self.assertIsInstance(result, LoopDone)


# ============================================================
# 安全网: doom loop guard 兜底
# ============================================================

class TestDoomGuardAsBackstop(unittest.TestCase):
    """当 Agent 持续不调 mark_complete 时，doom guard 作为安全网。"""

    def test_doom_guard_terminates_endless_thinking(self):
        """Agent 持续产出思考文本不行动时，doom guard 最终兜底。"""
        harness = Harness(enable_hdwm=False, max_loop_turns=3)

        # MockLLMClient 永远返回无 tool call 的文本
        responses = [
            {"content": f"思考第{i}步", "tool_calls": [], "usage": {}}
            for i in range(20)
        ]
        client = MockLLMClient(responses)
        messages = [{"role": "system", "content": "test"}, {"role": "user", "content": "go"}]

        result = _run_async(cognitive_loop(
            messages=messages, harness=harness, tools=[], client=client, verbose=False,
        ))

        # doom guard 在 max_loop_turns + 2 = 5 轮时触发
        self.assertIsInstance(result, LoopDoomStop)
        self.assertLessEqual(harness.state.loop_turns, 5)

    def test_doom_guard_with_hdwm(self):
        """HD-WM 启用时 doom guard 同样有效。"""
        harness = Harness(enable_hdwm=True, max_loop_turns=2)

        responses = [
            {"content": "thinking", "tool_calls": [], "usage": {}}
            for _ in range(10)
        ]
        client = MockLLMClient(responses)
        messages = [{"role": "system", "content": "test"}, {"role": "user", "content": "go"}]

        result = _run_async(cognitive_loop(
            messages=messages, harness=harness, tools=[], client=client, verbose=False,
        ))

        self.assertIsInstance(result, LoopDoomStop)


# ============================================================
# 混合场景: tool calls + 思考中间态 + mark_complete
# ============================================================

class TestMixedFlow(unittest.TestCase):
    """验证正常审稿流程: 工具调用和思考中间态交替，最终 mark_complete。"""

    def test_typical_review_flow(self):
        """典型审稿: 读取→思考→分析→思考→结论→mark_complete。"""
        harness = Harness(enable_hdwm=False, max_loop_turns=10)

        responses = [
            # 轮1: 调工具读取
            {
                "content": "开始审阅",
                "tool_calls": [{"id": "tc1", "name": "read_section",
                               "arguments": {"section": "abstract"}}],
                "usage": {},
            },
            # 轮2: 思考中间态
            {"content": "摘要提出了有趣的方法论创新...", "tool_calls": [], "usage": {}},
            # 轮3: 调工具继续
            {
                "content": "检查方法",
                "tool_calls": [{"id": "tc2", "name": "read_section",
                               "arguments": {"section": "methods"}}],
                "usage": {},
            },
            # 轮4: 思考中间态
            {"content": "方法论部分有几个需要注意的点...", "tool_calls": [], "usage": {}},
            # 轮5: 正式退出
            {
                "content": "",
                "tool_calls": [{"id": "tc3", "name": "mark_complete",
                               "arguments": {"summary": "审阅完成"}}],
                "usage": {},
            },
        ]
        client = MockLLMClient(responses)
        messages = [{"role": "system", "content": "test"}, {"role": "user", "content": "审稿"}]

        result = _run_async(cognitive_loop(
            messages=messages, harness=harness, tools=[], client=client, verbose=False,
        ))

        self.assertEqual(harness.state.loop_turns, 5)
        self.assertIsInstance(result, LoopDone)

    def test_tool_calls_reset_after_thinking(self):
        """思考中间态不影响之后的 tool call 执行。"""
        harness = Harness(enable_hdwm=False, max_loop_turns=10)

        responses = [
            {"content": "让我想想...", "tool_calls": [], "usage": {}},
            {"content": "继续想...", "tool_calls": [], "usage": {}},
            {
                "content": "好，现在行动",
                "tool_calls": [{"id": "tc1", "name": "read_section",
                               "arguments": {"section": "results"}}],
                "usage": {},
            },
            {
                "content": "",
                "tool_calls": [{"id": "tc2", "name": "mark_complete",
                               "arguments": {"summary": "done"}}],
                "usage": {},
            },
        ]
        client = MockLLMClient(responses)
        messages = [{"role": "system", "content": "test"}, {"role": "user", "content": "go"}]

        result = _run_async(cognitive_loop(
            messages=messages, harness=harness, tools=[], client=client, verbose=False,
        ))

        self.assertEqual(harness.state.loop_turns, 4)
        self.assertIsInstance(result, LoopDone)


if __name__ == "__main__":
    unittest.main()
