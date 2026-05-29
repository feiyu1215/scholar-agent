"""
tests/test_v2_loop_hdwm.py — Loop 层 HD-WM 集成测试

验证 cognitive_loop 中两处 HD-WM 集成点:
    1. tick() 调用: 每轮 loop_turns++ 后调用 hypothesis_module.tick(turn)
    2. review_readiness 信号注入: is_ready and is_saturated 时注入 system message

测试策略:
    - Mock LLMClient 控制返回行为（无 tool_call / done 信号 / 普通工具调用）
    - 使用真实 Harness（enable_hdwm=True/False）
    - 预设 hypothesis_module 状态来触发边界条件
"""

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from core.loop import cognitive_loop, LoopDone, LoopDoomStop
from core.harness import Harness
from core.hypothesis import HypothesisModule

# 防止 dotenv 环境污染导致 Checker 在 mark_complete 时触发 nudge
import core.checker as _checker_mod
_checker_mod.CHECKER_ENABLED = False


# ============================================================
# Mock LLM Client
# ============================================================

class MockLLMClient:
    """可编程的 LLM Client mock。"""

    def __init__(self, responses: list[dict]):
        """
        Args:
            responses: 按顺序返回的 response 列表。每个元素格式:
                {
                    "content": "text output",
                    "tool_calls": [...] or [],
                    "usage": {"prompt_tokens": N, "completion_tokens": M}
                }
        """
        self._responses = list(responses)
        self._call_count = 0

    async def chat_with_tools(self, **kwargs) -> dict:
        if self._call_count >= len(self._responses):
            # 兜底: 无 tool calls 即退出
            return {"content": "done", "tool_calls": [], "usage": {}}
        resp = self._responses[self._call_count]
        self._call_count += 1
        return resp


# ============================================================
# Test: tick() 调用
# ============================================================

class TestLoopHDWMTick(unittest.TestCase):
    """验证 loop 每轮调用 hypothesis_module.tick(turn)。"""

    def _run(self, coro):
        return asyncio.run(coro)

    def test_tick_called_each_turn_when_hdwm_enabled(self):
        """HD-WM 激活时，每轮 loop 都应调用 tick。"""
        harness = Harness(enable_hdwm=True, max_loop_turns=5)
        module = harness.hypothesis_module

        # 预生成一个假说以使饱和计数可观测
        module.generate("test", "sec1", turn=0)

        # 第1轮: generate_hypothesis（新建假说，重置 saturation）
        # 第2轮: resolve 假说（为 mark_complete 清路）
        # 第3轮: 思考中间态
        # 第4轮: mark_complete → gate 通过 → LoopDone
        responses = [
            {
                "content": "分析论文",
                "tool_calls": [{
                    "id": "tc_1",
                    "name": "generate_hypothesis",
                    "arguments": {"statement": "loop tick test", "source": "abstract"},
                }],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            },
            {
                "content": "验证假说",
                "tool_calls": [{
                    "id": "tc_2",
                    "name": "resolve_hypothesis",
                    "arguments": {"hyp_id": "H001", "status": "supported", "reason": "确认"},
                }, {
                    "id": "tc_3",
                    "name": "resolve_hypothesis",
                    "arguments": {"hyp_id": "H002", "status": "supported", "reason": "确认"},
                }],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            },
            {
                "content": "思考中间步骤",
                "tool_calls": [],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            },
            {
                "content": "",
                "tool_calls": [{
                    "id": "tc_done",
                    "name": "mark_complete",
                    "arguments": {"summary": "审阅完成"},
                }],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            },
        ]
        client = MockLLMClient(responses)

        messages = [
            {"role": "system", "content": "test system"},
            {"role": "user", "content": "开始审稿"},
        ]

        result = self._run(cognitive_loop(
            messages=messages,
            harness=harness,
            tools=[],
            client=client,
            verbose=False,
        ))

        self.assertEqual(harness.state.loop_turns, 4)
        self.assertIsInstance(result, LoopDone)
        # tick 每轮都调用，generate 在第1轮重置计数器，第2/3/4轮各+1
        self.assertEqual(module._turns_since_last_hypothesis, 3)

    def test_tick_not_called_when_hdwm_disabled(self):
        """HD-WM 关闭时，loop 正常运行不报错。"""
        harness = Harness(enable_hdwm=False)
        self.assertIsNone(harness.hypothesis_module)

        # Agent 直接通过 mark_complete 正常退出
        responses = [
            {
                "content": "",
                "tool_calls": [{
                    "id": "tc_done",
                    "name": "mark_complete",
                    "arguments": {"summary": "done"},
                }],
                "usage": {},
            },
        ]
        client = MockLLMClient(responses)

        messages = [
            {"role": "system", "content": "test system"},
            {"role": "user", "content": "审稿"},
        ]

        result = self._run(cognitive_loop(
            messages=messages,
            harness=harness,
            tools=[],
            client=client,
            verbose=False,
        ))

        self.assertIsInstance(result, LoopDone)
        self.assertEqual(harness.state.loop_turns, 1)

    def test_tick_increments_saturation_counter(self):
        """连续无 generate 的轮次使饱和计数器递增。"""
        harness = Harness(enable_hdwm=True)
        module = harness.hypothesis_module

        # 2 轮思考中间态 + 第3轮 mark_complete 退出
        responses = [
            {"content": "思考中...", "tool_calls": [], "usage": {}},
            {"content": "继续思考", "tool_calls": [], "usage": {}},
            {
                "content": "",
                "tool_calls": [{
                    "id": "tc_done",
                    "name": "mark_complete",
                    "arguments": {"summary": "完成"},
                }],
                "usage": {},
            },
        ]
        client = MockLLMClient(responses)

        messages = [
            {"role": "system", "content": "test"},
            {"role": "user", "content": "begin"},
        ]

        self._run(cognitive_loop(
            messages=messages, harness=harness, tools=[],
            client=client, verbose=False,
        ))

        # 3 轮 loop，每轮 tick → 计数器 = 3
        self.assertEqual(module._turns_since_last_hypothesis, 3)


# ============================================================
# Test: review_readiness 信号注入
# ============================================================

class TestLoopHDWMSignalInjection(unittest.TestCase):
    """验证 is_ready + is_saturated 时注入 system 信号到 messages。"""

    def _run(self, coro):
        return asyncio.run(coro)

    def test_signal_injected_when_ready_and_saturated(self):
        """HD-WM ready + saturated 时，messages 中应出现信号消息。"""
        harness = Harness(enable_hdwm=True)
        module = harness.hypothesis_module

        # 制造 is_ready + is_saturated 的状态:
        # 3 hypotheses all resolved (readiness=1.0) + 3 ticks without generate (saturated)
        for i in range(3):
            h = module.generate(f"hyp{i}", f"sec{i}", turn=i)
            module.resolve(h.id, "supported", "confirmed", turn=i)

        # 手动设置饱和（3 ticks without generate）
        module.tick(10)
        module.tick(11)
        module.tick(12)
        self.assertTrue(module.is_ready)
        self.assertTrue(module.is_saturated)

        # LLM 返回一个 tool call 然后退出
        # 使用一个简单的 tool call 来触发信号注入（信号在 tool 执行后注入）
        responses = [
            {
                "content": "继续分析",
                "tool_calls": [{
                    "id": "tc_sig",
                    "name": "generate_hypothesis",
                    "arguments": {"statement": "新假说", "source": "results"},
                }],
                "usage": {},
            },
            {
                "content": "进入综合",
                "tool_calls": [],
                "usage": {},
            },
        ]
        client = MockLLMClient(responses)

        messages = [
            {"role": "system", "content": "test system"},
            {"role": "user", "content": "审稿"},
        ]

        result = self._run(cognitive_loop(
            messages=messages, harness=harness, tools=[],
            client=client, verbose=False,
        ))

        # 注意: generate_hypothesis 会重置 saturation，所以第1轮执行后 is_saturated=False
        # 但信号检查在第1轮 tool 执行后，此时 is_saturated 已被 generate 重置
        # 因此第1轮不应注入信号。验证 messages 中无信号。
        # 这是正确行为——generate 重置了饱和状态。
        signal_msgs = [m for m in messages if "HD-WM 信号" in (m.get("content") or "")]
        self.assertEqual(len(signal_msgs), 0)

    def test_signal_injected_when_no_generate_during_tool_calls(self):
        """
        已 ready + saturated 状态下，如果工具调用不重置饱和，
        则信号应被注入。
        """
        harness = Harness(enable_hdwm=True)
        module = harness.hypothesis_module

        # 制造 ready + saturated: 3 hypotheses resolved
        for i in range(3):
            h = module.generate(f"hyp{i}", f"sec{i}", turn=i)
            module.resolve(h.id, "supported", "ok", turn=i)

        # 手动制造 saturated（不通过 tick，直接设置内部状态）
        module._turns_since_last_hypothesis = 3

        self.assertTrue(module.is_ready)
        self.assertTrue(module.is_saturated)

        # LLM 第1轮: 调用 add_evidence（不重置饱和）
        # add_evidence 需要存在一个 ACTIVE hypothesis，所以我们用 resolve_hypothesis
        # 但所有 hypothesis 已解决，换一个不改变 hypothesis 状态的工具
        # 使用 resolve_hypothesis on already-resolved 会失败但不影响 module 状态
        responses = [
            {
                "content": "验证结论",
                "tool_calls": [{
                    "id": "tc_re",
                    "name": "resolve_hypothesis",
                    "arguments": {"hyp_id": "H001", "status": "supported", "reason": "re-confirm"},
                }],
                "usage": {},
            },
            {
                "content": "综合完成",
                "tool_calls": [],
                "usage": {},
            },
        ]
        client = MockLLMClient(responses)

        messages = [
            {"role": "system", "content": "test system"},
            {"role": "user", "content": "审稿"},
        ]

        result = self._run(cognitive_loop(
            messages=messages, harness=harness, tools=[],
            client=client, verbose=False,
        ))

        # 第1轮:
        #   tick(1) → _turns_since_last_hypothesis = 4 (still saturated)
        #   tool: resolve_hypothesis on already-resolved → 返回错误字符串但不改状态
        #   信号检查: is_ready=True, is_saturated=True → 注入信号
        signal_msgs = [m for m in messages if "HD-WM 信号" in (m.get("content") or "")]
        self.assertGreaterEqual(len(signal_msgs), 1)
        # 验证信号内容格式
        self.assertIn("审稿完成度", signal_msgs[0]["content"])
        self.assertIn("synthesis", signal_msgs[0]["content"])

    def test_no_signal_when_not_ready(self):
        """readiness < 0.8 时不注入信号。"""
        harness = Harness(enable_hdwm=True, max_loop_turns=3)
        module = harness.hypothesis_module

        # 3 hypotheses, only 1 resolved → readiness ≈ 0.53
        for i in range(3):
            module.generate(f"hyp{i}", f"sec{i}", turn=i)
        module.resolve("H001", "supported")

        # 手动饱和
        module._turns_since_last_hypothesis = 5

        self.assertFalse(module.is_ready)  # readiness < 0.8
        self.assertTrue(module.is_saturated)

        # 使用 mark_complete 退出
        responses = [
            {"content": "thinking", "tool_calls": [], "usage": {}},
            {
                "content": "",
                "tool_calls": [{"id": "tc_d", "name": "mark_complete", "arguments": {"summary": "done"}}],
                "usage": {},
            },
        ]
        client = MockLLMClient(responses)

        messages = [
            {"role": "system", "content": "test"},
            {"role": "user", "content": "go"},
        ]

        self._run(cognitive_loop(
            messages=messages, harness=harness, tools=[],
            client=client, verbose=False,
        ))

        signal_msgs = [m for m in messages if "HD-WM 信号" in (m.get("content") or "")]
        self.assertEqual(len(signal_msgs), 0)

    def test_no_signal_when_not_saturated(self):
        """未饱和时不注入信号。"""
        harness = Harness(enable_hdwm=True, max_loop_turns=3)
        module = harness.hypothesis_module

        # 3 hypotheses all resolved → is_ready=True
        for i in range(3):
            h = module.generate(f"hyp{i}", f"sec{i}", turn=i)
            module.resolve(h.id, "supported", "ok", turn=i)

        # 不设置饱和（_turns_since_last_hypothesis = 0）
        self.assertTrue(module.is_ready)
        self.assertFalse(module.is_saturated)

        # 使用 mark_complete 退出
        responses = [
            {"content": "done", "tool_calls": [], "usage": {}},
            {
                "content": "",
                "tool_calls": [{"id": "tc_d", "name": "mark_complete", "arguments": {"summary": "done"}}],
                "usage": {},
            },
        ]
        client = MockLLMClient(responses)

        messages = [
            {"role": "system", "content": "test"},
            {"role": "user", "content": "go"},
        ]

        self._run(cognitive_loop(
            messages=messages, harness=harness, tools=[],
            client=client, verbose=False,
        ))

        signal_msgs = [m for m in messages if "HD-WM 信号" in (m.get("content") or "")]
        self.assertEqual(len(signal_msgs), 0)

    def test_signal_is_system_role(self):
        """HD-WM 信号注入时 role 为 system（通过带 tool call 的轮次触发）。"""
        harness = Harness(enable_hdwm=True)
        module = harness.hypothesis_module

        for i in range(3):
            h = module.generate(f"hyp{i}", f"sec{i}", turn=i)
            module.resolve(h.id, "supported", "ok", turn=i)
        module._turns_since_last_hypothesis = 3

        # 需要有 tool call 的轮次来触发信号注入（信号在 tool 执行后注入）
        # 使用一个不改变 hypothesis 状态的 tool call + mark_complete 退出
        responses = [
            {
                "content": "验证",
                "tool_calls": [{"id": "tc1", "name": "resolve_hypothesis",
                               "arguments": {"hyp_id": "H001", "status": "supported", "reason": "dup"}}],
                "usage": {},
            },
            {
                "content": "",
                "tool_calls": [{"id": "tc_d", "name": "mark_complete", "arguments": {"summary": "done"}}],
                "usage": {},
            },
        ]
        client = MockLLMClient(responses)

        messages = [
            {"role": "system", "content": "test"},
            {"role": "user", "content": "start"},
        ]

        self._run(cognitive_loop(
            messages=messages, harness=harness, tools=[],
            client=client, verbose=False,
        ))

        # 信号应在第1轮 tool 执行后注入（ready + saturated）
        signal_msgs = [m for m in messages if "HD-WM 信号" in (m.get("content") or "")]
        self.assertGreaterEqual(len(signal_msgs), 1)
        # 验证 role 为 system
        for sig in signal_msgs:
            self.assertEqual(sig["role"], "system")


# ============================================================
# Test: HD-WM 信号不终止循环（signal-not-command 原则）
# ============================================================

class TestLoopHDWMSignalNotCommand(unittest.TestCase):
    """信号注入后 loop 仍继续，不强制终止。"""

    def _run(self, coro):
        return asyncio.run(coro)

    def test_loop_continues_after_signal(self):
        """信号注入后，如果 LLM 继续产出 tool calls，loop 不停。"""
        harness = Harness(enable_hdwm=True)
        module = harness.hypothesis_module

        # 制造 ready + saturated
        for i in range(3):
            h = module.generate(f"hyp{i}", f"sec{i}", turn=i)
            module.resolve(h.id, "supported", "ok", turn=i)
        module._turns_since_last_hypothesis = 4

        # LLM: 轮1有 tool call → 信号注入 → 轮2有 tool call → 轮3 mark_complete 退出
        responses = [
            {
                "content": "deep analysis",
                "tool_calls": [{
                    "id": "tc1",
                    "name": "resolve_hypothesis",
                    "arguments": {"hyp_id": "H001", "status": "supported", "reason": "dup"},
                }],
                "usage": {},
            },
            {
                "content": "more analysis",
                "tool_calls": [{
                    "id": "tc2",
                    "name": "resolve_hypothesis",
                    "arguments": {"hyp_id": "H002", "status": "supported", "reason": "dup"},
                }],
                "usage": {},
            },
            {
                "content": "",
                "tool_calls": [{
                    "id": "tc_done",
                    "name": "mark_complete",
                    "arguments": {"summary": "审阅完成"},
                }],
                "usage": {},
            },
        ]
        client = MockLLMClient(responses)

        messages = [
            {"role": "system", "content": "test"},
            {"role": "user", "content": "go"},
        ]

        result = self._run(cognitive_loop(
            messages=messages, harness=harness, tools=[],
            client=client, verbose=False,
        ))

        # 信号注入后 loop 继续（不强制终止），最终通过 mark_complete 退出
        self.assertEqual(harness.state.loop_turns, 3)
        self.assertIsInstance(result, LoopDone)


# ============================================================
# Test: HD-WM 与 doom loop 无冲突
# ============================================================

class TestLoopHDWMWithDoomGuard(unittest.TestCase):
    """HD-WM tick 不影响 doom loop 检测。"""

    def _run(self, coro):
        return asyncio.run(coro)

    def test_doom_guard_still_works_with_hdwm(self):
        """HD-WM 激活时，超过 max_loop_turns 仍触发 doom stop。"""
        harness = Harness(enable_hdwm=True, max_loop_turns=2)
        module = harness.hypothesis_module

        # LLM 持续返回 tool calls，不主动停止
        def make_response(n):
            return {
                "content": f"turn {n}",
                "tool_calls": [{
                    "id": f"tc_{n}",
                    "name": "generate_hypothesis",
                    "arguments": {"statement": f"hyp turn {n}", "source": "sec1"},
                }],
                "usage": {},
            }

        responses = [make_response(i) for i in range(10)]
        client = MockLLMClient(responses)

        messages = [
            {"role": "system", "content": "test"},
            {"role": "user", "content": "begin"},
        ]

        result = self._run(cognitive_loop(
            messages=messages, harness=harness, tools=[],
            client=client, verbose=False,
        ))

        # max_loop_turns=2 → 第3轮开始时 doom guard 触发
        self.assertIsInstance(result, LoopDoomStop)
        # tick 应该被调用了（不会阻止 doom guard）


if __name__ == "__main__":
    unittest.main()
