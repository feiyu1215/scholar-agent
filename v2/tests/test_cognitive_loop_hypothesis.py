"""
tests/test_cognitive_loop_hypothesis.py — Integration tests for Hypothesis-Driven Working Memory (HD-WM)
within the cognitive_loop.

Verifies:
    1. generate_hypothesis tool creates a hypothesis via the loop
    2. add_evidence + resolve_hypothesis lifecycle works end-to-end
    3. tick() is called each turn (saturation counter advances)
    4. HD-WM is inactive when enable_hdwm=False

运行: pytest tests/test_cognitive_loop_hypothesis.py -v
"""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch, MagicMock

from core.loop import cognitive_loop, LoopDone, LoopDoomStop
from core.harness import Harness

# 禁用 checker 以避免环境变量污染
import core.checker as _checker_mod
_checker_mod.CHECKER_ENABLED = False


# ============================================================
# Helpers
# ============================================================

def _run(coro):
    """运行异步协程。"""
    return asyncio.run(coro)


class MockLLMClient:
    """可编程的 LLM Client mock — 按顺序返回预设 responses。"""

    def __init__(self, responses: list[dict]):
        self._responses = list(responses)
        self._call_count = 0

    async def chat_with_tools(self, **kwargs) -> dict:
        if self._call_count >= len(self._responses):
            # 兜底: 防止无限循环
            return {
                "content": "",
                "tool_calls": [{"id": "tc_fallback", "name": "mark_complete", "arguments": {"summary": "[mock exhausted]"}}],
                "usage": {},
            }
        resp = self._responses[self._call_count]
        self._call_count += 1
        return resp


def _make_harness(max_loop_turns: int = 50, enable_hdwm: bool = True) -> Harness:
    """创建测试用 Harness (不加载真实论文)。"""
    with patch("core.harness._pl_load_paper"):
        h = Harness(paper_path="fake.md", max_loop_turns=max_loop_turns, enable_hdwm=enable_hdwm)
    h.state.paper_sections = {"introduction": "...", "methodology": "...", "results": "..."}
    h.state.paper_overview = "Test paper"
    h.state.sections_read = []
    h.state.findings = []
    return h


def _make_messages() -> list[dict]:
    """构建最小 messages 列表。"""
    return [
        {"role": "system", "content": "test system prompt"},
        {"role": "user", "content": "开始审稿"},
    ]


# ============================================================
# Test Case 1: generate_hypothesis via loop
# ============================================================

class TestGenerateHypothesisViaLoop(unittest.TestCase):
    """Agent 通过 generate_hypothesis 工具在循环中创建假说。"""

    def test_generate_hypothesis_via_loop(self):
        """
        T1: Agent calls generate_hypothesis
        T2: Agent reads a section
        T3: Agent adds a finding and marks complete

        Assert:
            - hypothesis_module.hypotheses has 1 entry
            - hypothesis statement matches
            - LoopDone returned
        """
        harness = _make_harness(enable_hdwm=True)

        responses = [
            # T1: generate_hypothesis
            {
                "content": "发现可能的方法论问题，生成假说",
                "tool_calls": [{
                    "id": "tc1",
                    "name": "generate_hypothesis",
                    "arguments": {
                        "statement": "The pruning method may cause accuracy degradation",
                        "source": "methodology",
                    },
                }],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            },
            # T2: read_section
            {
                "content": "阅读 methodology 部分确认",
                "tool_calls": [{
                    "id": "tc2",
                    "name": "read_section",
                    "arguments": {"section_name": "methodology", "offset": 0},
                }],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            },
            # T3: add a finding then mark_complete
            {
                "content": "记录发现并完成审阅",
                "tool_calls": [
                    {
                        "id": "tc3a",
                        "name": "update_findings",
                        "arguments": {
                            "finding": "Pruning may degrade accuracy without fine-tuning",
                            "section": "methodology",
                            "priority": "medium",
                            "status": "suggestion",
                            "evidence": "Section 3.2 describes pruning without subsequent fine-tuning step.",
                        },
                    },
                    {
                        "id": "tc3b",
                        "name": "mark_complete",
                        "arguments": {"summary": "审阅完成，记录了关键假说和发现"},
                    },
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            },
        ]

        client = MockLLMClient(responses)
        messages = _make_messages()

        result = _run(cognitive_loop(
            messages=messages, harness=harness, tools=[], client=client, verbose=False,
        ))

        # Assert: LoopDone returned
        self.assertIsInstance(result, LoopDone)

        # Assert: hypothesis_module has 1 hypothesis
        self.assertEqual(len(harness.hypothesis_module.hypotheses), 1)

        # Assert: hypothesis statement matches
        hyp = harness.hypothesis_module.hypotheses[0]
        self.assertEqual(hyp.statement, "The pruning method may cause accuracy degradation")
        self.assertEqual(hyp.source, "methodology")


# ============================================================
# Test Case 2: add_evidence and resolve
# ============================================================

class TestAddEvidenceAndResolve(unittest.TestCase):
    """Agent 生成假说后添加证据并解决。"""

    def test_add_evidence_and_resolve(self):
        """
        T1: generate_hypothesis
        T2: add_evidence (for)
        T3: add_evidence (against)
        T4: resolve_hypothesis as "supported"
        T5: update_findings + mark_complete

        Assert:
            - hypothesis has 2 evidence items
            - hypothesis status == "supported"
            - LoopDone returned
        """
        harness = _make_harness(enable_hdwm=True)

        responses = [
            # T1: generate hypothesis
            {
                "content": "假说: baseline 对比可能不公平",
                "tool_calls": [{
                    "id": "tc1",
                    "name": "generate_hypothesis",
                    "arguments": {
                        "statement": "Baseline comparison is unfair due to different compute budgets",
                        "source": "results",
                    },
                }],
                "usage": {},
            },
            # T2: add supporting evidence
            {
                "content": "添加支持证据",
                "tool_calls": [{
                    "id": "tc2",
                    "name": "add_evidence",
                    "arguments": {
                        "hyp_id": "H001",
                        "content": "Table 3 shows proposed method uses 4x more GPU hours than baseline",
                        "direction": "for",
                        "strength": 0.8,
                        "source": "results",
                    },
                }],
                "usage": {},
            },
            # T3: add refuting evidence
            {
                "content": "添加反面证据",
                "tool_calls": [{
                    "id": "tc3",
                    "name": "add_evidence",
                    "arguments": {
                        "hyp_id": "H001",
                        "content": "Section 4.3 mentions cost-normalized comparison in supplementary",
                        "direction": "against",
                        "strength": 0.4,
                        "source": "results",
                    },
                }],
                "usage": {},
            },
            # T4: resolve as supported
            {
                "content": "综合证据，支持假说",
                "tool_calls": [{
                    "id": "tc4",
                    "name": "resolve_hypothesis",
                    "arguments": {
                        "hyp_id": "H001",
                        "status": "supported",
                        "reason": "Strong evidence of compute budget mismatch outweighs supplementary mention",
                    },
                }],
                "usage": {},
            },
            # T5: finding + mark_complete
            {
                "content": "完成审阅",
                "tool_calls": [
                    {
                        "id": "tc5a",
                        "name": "update_findings",
                        "arguments": {
                            "finding": "Baseline comparison uses unfair compute budget",
                            "section": "results",
                            "priority": "high",
                            "status": "verified",
                            "evidence": "Table 3 confirms 4x compute disparity",
                        },
                    },
                    {
                        "id": "tc5b",
                        "name": "mark_complete",
                        "arguments": {"summary": "审阅完成，核心假说已验证"},
                    },
                ],
                "usage": {},
            },
        ]

        client = MockLLMClient(responses)
        messages = _make_messages()

        result = _run(cognitive_loop(
            messages=messages, harness=harness, tools=[], client=client, verbose=False,
        ))

        # Assert: LoopDone
        self.assertIsInstance(result, LoopDone)

        # Assert: hypothesis has 2 evidence items
        hyp = harness.hypothesis_module.get_hypothesis("H001")
        self.assertIsNotNone(hyp)
        self.assertEqual(len(hyp.evidence), 2)

        # Assert: evidence directions are correct
        self.assertEqual(len(hyp.evidence_for), 1)
        self.assertEqual(len(hyp.evidence_against), 1)

        # Assert: hypothesis status is "supported"
        self.assertEqual(hyp.status.value, "supported")


# ============================================================
# Test Case 3: tick called each turn
# ============================================================

class TestTickCalledEachTurn(unittest.TestCase):
    """验证 hypothesis_module.tick() 在每轮 loop 中被调用。"""

    def test_tick_called_each_turn(self):
        """
        使用 unittest.mock.patch 包装 hypothesis_module.tick，计数调用次数。

        T1: generate_hypothesis (创建假说)
        T2-T5: text-only responses (4 轮无工具调用 → 中间思考态继续 loop)
        T6: mark_complete (with finding)

        tick 应在每轮 loop_turns++ 后调用一次。
        由于 text-only 不产生 tool_calls，loop 继续但 loop_turns 仍递增，
        所以 tick 也应该被调用。

        注意: 无 tool_calls 的轮次仍然递增 loop_turns（见 loop.py L170）。
        """
        harness = _make_harness(enable_hdwm=True, max_loop_turns=20)

        responses = [
            # T1: generate hypothesis
            {
                "content": "初步假说",
                "tool_calls": [{
                    "id": "tc1",
                    "name": "generate_hypothesis",
                    "arguments": {
                        "statement": "Data augmentation scheme may introduce label noise",
                        "source": "methodology",
                    },
                }],
                "usage": {},
            },
            # T2-T5: text-only (no tool calls → loop treats as intermediate thinking)
            {"content": "Thinking about evidence...", "tool_calls": [], "usage": {}},
            {"content": "Still analyzing...", "tool_calls": [], "usage": {}},
            {"content": "Considering implications...", "tool_calls": [], "usage": {}},
            {"content": "Almost ready to conclude...", "tool_calls": [], "usage": {}},
            # T6: finding + mark_complete
            {
                "content": "完成",
                "tool_calls": [
                    {
                        "id": "tc6a",
                        "name": "update_findings",
                        "arguments": {
                            "finding": "Data augmentation may introduce label noise",
                            "section": "methodology",
                            "priority": "medium",
                            "status": "suggestion",
                            "evidence": "Section 3 describes random augmentation without label preservation guarantee.",
                        },
                    },
                    {
                        "id": "tc6b",
                        "name": "mark_complete",
                        "arguments": {"summary": "完成"},
                    },
                ],
                "usage": {},
            },
        ]

        client = MockLLMClient(responses)
        messages = _make_messages()

        # Wrap tick with a mock to count calls
        original_tick = harness.hypothesis_module.tick
        tick_call_count = 0
        tick_call_turns: list[int] = []

        def counting_tick(turn: int) -> None:
            nonlocal tick_call_count
            tick_call_count += 1
            tick_call_turns.append(turn)
            original_tick(turn)

        with patch.object(harness.hypothesis_module, "tick", side_effect=counting_tick):
            result = _run(cognitive_loop(
                messages=messages, harness=harness, tools=[], client=client, verbose=False,
            ))

        # Assert: LoopDone
        self.assertIsInstance(result, LoopDone)

        # Assert: tick was called once per loop turn.
        # The loop runs 7 turns: T1 (generate_hypothesis), T2-T5 (text-only),
        # T6 (finding + mark_complete → nudged due to active hypothesis),
        # T7 (fallback mark_complete → forced exit).
        self.assertEqual(harness.state.loop_turns, 7)
        self.assertEqual(tick_call_count, 7)

        # Assert: tick was called with sequential turn numbers
        self.assertEqual(tick_call_turns, [1, 2, 3, 4, 5, 6, 7])


# ============================================================
# Test Case 4: hypothesis not active without HD-WM
# ============================================================

class TestHypothesisNotActiveWithoutHDWM(unittest.TestCase):
    """enable_hdwm=False 时 hypothesis_module 为 None，loop 仍正常工作。"""

    def test_hypothesis_not_active_without_hdwm(self):
        """
        创建 Harness(enable_hdwm=False)。
        Agent 做一次 read_section + finding + mark_complete。

        Assert:
            - harness.hypothesis_module is None
            - LoopDone returned (loop works without HD-WM)
        """
        harness = _make_harness(enable_hdwm=False)

        # Confirm hypothesis_module is None before running loop
        self.assertIsNone(harness.hypothesis_module)

        responses = [
            # T1: read_section
            {
                "content": "阅读论文",
                "tool_calls": [{
                    "id": "tc1",
                    "name": "read_section",
                    "arguments": {"section_name": "introduction", "offset": 0},
                }],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            },
            # T2: finding + mark_complete
            {
                "content": "完成审阅",
                "tool_calls": [
                    {
                        "id": "tc2a",
                        "name": "update_findings",
                        "arguments": {
                            "finding": "Introduction lacks clear research gap statement",
                            "section": "introduction",
                            "priority": "medium",
                            "status": "suggestion",
                            "evidence": "No explicit gap identified in paragraphs 1-3.",
                        },
                    },
                    {
                        "id": "tc2b",
                        "name": "mark_complete",
                        "arguments": {"summary": "审阅完成"},
                    },
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            },
        ]

        client = MockLLMClient(responses)
        messages = _make_messages()

        result = _run(cognitive_loop(
            messages=messages, harness=harness, tools=[], client=client, verbose=False,
        ))

        # Assert: LoopDone returned
        self.assertIsInstance(result, LoopDone)

        # Assert: hypothesis_module remains None
        self.assertIsNone(harness.hypothesis_module)

        # Assert: findings were still recorded
        self.assertGreaterEqual(len(harness.state.findings), 1)


if __name__ == "__main__":
    unittest.main()
