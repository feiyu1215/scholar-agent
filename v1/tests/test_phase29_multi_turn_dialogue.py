"""
Phase 29: 多轮对话协作测试

验证目标：
    1. Agent 可以通过 talk_to_user 暂停循环，等待用户回复
    2. 用户回复后 Agent 恢复认知循环，保持之前的 findings
    3. chat() 恢复后 Agent 的 workspace state 正确（包含已有 findings）
    4. 完整路径: start → talk → chat(reply) → continue → done
    5. Agent 在多轮中维持连贯的认知（不会重复发现已有的 finding）

设计原则 (COGNITIVE_ANCHOR §10.3):
    Agent 与用户的关系是协作式、对话式的。
    在关键决策点暂停，和用户确认方向。

运行: pytest tests/test_phase29_multi_turn_dialogue.py -v
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.loop import cognitive_loop, LoopDone, LoopTalk, LoopDoomStop
from core.harness import Harness
from core.identity import SCHOLAR_IDENTITY, SCHOLAR_TOOLS, build_system_prompt

# 防止 dotenv 环境污染导致 Checker 在 mark_complete 时触发 nudge
import core.checker as _checker_mod
_checker_mod.CHECKER_ENABLED = False


# ============================================================
# MockLLMClient（复用 Phase 23 模式）
# ============================================================

class MockLLMClient:
    """脚本化 LLM 客户端。"""

    def __init__(self, script: list[dict]):
        self.script = list(script)
        self._tc_counter = 0
        self.total_calls = 0
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.model = "mock-scripted"

    async def chat_with_tools(self, messages, tools, **kwargs) -> dict:
        self.total_calls += 1
        if not self.script:
            return {"content": "(脚本耗尽)", "tool_calls": [], "finish_reason": "stop",
                    "usage": {"prompt_tokens": 100, "completion_tokens": 50}}

        item = self.script.pop(0)
        tool_calls = []
        for tc in item.get("tool_calls", []):
            self._tc_counter += 1
            tool_calls.append({
                "id": f"tc_{self._tc_counter:04d}",
                "name": tc["name"],
                "arguments": tc.get("arguments", {}),
            })

        return {
            "content": item.get("content", None),
            "tool_calls": tool_calls,
            "finish_reason": "tool_calls" if tool_calls else "stop",
            "usage": {"prompt_tokens": 200, "completion_tokens": 100},
        }

    def stats(self):
        return {"provider": "mock", "model": self.model, "total_calls": self.total_calls}


# ============================================================
# 辅助
# ============================================================

def _make_harness(max_turns: int = 50) -> Harness:
    tmp_dir = tempfile.mkdtemp()
    h = Harness(max_loop_turns=max_turns, memory_dir=tmp_dir)
    h._paper_loaded = True
    h.state.paper_sections = {
        "abstract": (
            "## Abstract\n\n"
            "We propose DeepFusion, a novel method that achieves state-of-the-art "
            "performance on image classification. Our method improves accuracy by "
            "3.2% over previous best results on ImageNet."
        ),
        "methodology": (
            "## Methodology\n\n"
            "DeepFusion uses a multi-scale attention mechanism combined with "
            "residual connections. We train on ImageNet-1K for 300 epochs."
        ),
        "results": (
            "## Results\n\n"
            "| Method | Top-1 Acc |\n|--------|----------|\n"
            "| ResNet-50 | 76.1 |\n| ViT-B/16 | 77.9 |\n"
            "| DeepFusion | 79.3 |\n\n"
            "DeepFusion achieves 79.3% top-1 accuracy."
        ),
    }
    return h


def _make_messages(harness: Harness) -> list[dict]:
    ws = harness.format_context()
    sp = build_system_prompt(identity=SCHOLAR_IDENTITY, workspace_state=ws)
    return [
        {"role": "system", "content": sp},
        {"role": "user", "content": "请审阅这篇论文。"},
    ]


def _run(coro):
    return asyncio.run(coro)


# ============================================================
# Test 1: talk_to_user 暂停并返回 LoopTalk
# ============================================================

class TestTalkToUserPausesLoop:
    """验证 talk_to_user 正确暂停认知循环。"""

    def test_talk_returns_loop_talk(self):
        """Agent 调用 talk_to_user 后循环暂停，返回 LoopTalk。"""
        h = _make_harness()
        script = [
            # Turn 1: 读 abstract
            {"content": "先看 abstract。", "tool_calls": [
                {"name": "read_section", "arguments": {"section": "abstract"}}
            ]},
            # Turn 2: 发现问题，记录 finding
            {"content": "发现 overclaim。", "tool_calls": [
                {"name": "update_findings", "arguments": {
                    "finding": "Abstract claims 3.2% improvement but this needs verification",
                    "priority": "high", "status": "needs_verification"
                }}
            ]},
            # Turn 3: 想和用户讨论
            {"content": "我需要确认用户最关心什么。", "tool_calls": [
                {"name": "talk_to_user", "arguments": {
                    "message": "我发现了一个可能的 overclaim。你最关心论文的哪个方面？",
                    "expects_reply": True
                }}
            ]},
        ]
        messages = _make_messages(h)
        result = _run(cognitive_loop(messages, h, SCHOLAR_TOOLS, MockLLMClient(script), verbose=False))

        # 应该返回 LoopTalk
        assert isinstance(result, LoopTalk)
        assert "overclaim" in result.message
        assert result.expects_reply is True

    def test_findings_preserved_after_talk(self):
        """talk_to_user 暂停后，之前记录的 findings 仍在 harness 中。"""
        h = _make_harness()
        script = [
            {"content": "读。", "tool_calls": [
                {"name": "read_section", "arguments": {"section": "abstract"}}
            ]},
            {"content": "记录。", "tool_calls": [
                {"name": "update_findings", "arguments": {
                    "finding": "Test finding", "priority": "medium", "status": "verified"
                }}
            ]},
            {"content": "问。", "tool_calls": [
                {"name": "talk_to_user", "arguments": {"message": "确认方向", "expects_reply": True}}
            ]},
        ]
        messages = _make_messages(h)
        _run(cognitive_loop(messages, h, SCHOLAR_TOOLS, MockLLMClient(script), verbose=False))

        # Findings 应该保留
        assert len(h.state.findings) == 1
        assert h.state.findings[0]["finding"] == "Test finding"


# ============================================================
# Test 2: 用户回复后 Agent 恢复认知循环
# ============================================================

class TestChatResumesAfterTalk:
    """验证 chat() 路径的恢复能力。"""

    def test_resume_after_user_reply(self):
        """模拟完整路径：start → talk → reply → continue → done。"""
        h = _make_harness()

        # 第一轮脚本：Agent 读 + 记录 + talk_to_user
        script_phase1 = [
            {"content": "开始审阅。", "tool_calls": [
                {"name": "read_section", "arguments": {"section": "abstract"}}
            ]},
            {"content": "有 overclaim。", "tool_calls": [
                {"name": "update_findings", "arguments": {
                    "finding": "Overclaim in abstract", "priority": "high",
                    "status": "needs_verification"
                }}
            ]},
            {"content": "需要确认方向。", "tool_calls": [
                {"name": "talk_to_user", "arguments": {
                    "message": "你希望我重点看哪部分？",
                    "expects_reply": True
                }}
            ]},
        ]

        messages = _make_messages(h)
        client = MockLLMClient(script_phase1)
        result = _run(cognitive_loop(messages, h, SCHOLAR_TOOLS, client, verbose=False))

        assert isinstance(result, LoopTalk)
        assert len(h.state.findings) == 1

        # --- 用户回复 ---
        h.new_conversation_turn()
        messages.append({"role": "user", "content": "请重点看 methodology 和 results。"})

        # 第二轮脚本：Agent 基于用户回复继续
        script_phase2 = [
            {"content": "用户让我看 methodology。", "tool_calls": [
                {"name": "read_section", "arguments": {"section": "methodology"}}
            ]},
            {"content": "再看 results。", "tool_calls": [
                {"name": "read_section", "arguments": {"section": "results"}}
            ]},
            {"content": "验证了 overclaim。", "tool_calls": [
                {"name": "update_findings", "arguments": {
                    "finding": "Results show 79.3-77.9=1.4%, not 3.2% as claimed",
                    "priority": "high", "status": "verified"
                }}
            ]},
            {"content": "完成。", "tool_calls": [
                {"name": "mark_complete", "arguments": {
                    "summary": "审阅完成，发现 overclaim 问题"
                }}
            ]},
        ]

        client2 = MockLLMClient(script_phase2)
        result2 = _run(cognitive_loop(messages, h, SCHOLAR_TOOLS, client2, verbose=False))

        # 应该正常完成
        assert isinstance(result2, LoopDone)
        # findings 应该累积（包含两轮的发现）
        assert len(h.state.findings) == 2
        # conversation_turns 应该递增
        assert h.state.conversation_turns == 1  # new_conversation_turn 被调用了一次

    def test_workspace_state_includes_findings_after_resume(self):
        """恢复后的 format_context 包含之前记录的 findings。"""
        h = _make_harness()

        # 第一轮：记录 finding 后 talk
        script = [
            {"content": "记录。", "tool_calls": [
                {"name": "update_findings", "arguments": {
                    "finding": "Important finding", "priority": "high", "status": "verified"
                }}
            ]},
            {"content": "问。", "tool_calls": [
                {"name": "talk_to_user", "arguments": {"message": "你怎么看？"}}
            ]},
        ]
        messages = _make_messages(h)
        _run(cognitive_loop(messages, h, SCHOLAR_TOOLS, MockLLMClient(script), verbose=False))

        # 恢复前检查 format_context
        h.new_conversation_turn()
        ctx = h.format_context()
        assert "Important finding" in ctx


# ============================================================
# Test 3: loop_turns 在恢复后正确重置
# ============================================================

class TestLoopTurnsReset:
    """验证多轮对话中 loop_turns 正确管理。"""

    def test_loop_turns_reset_after_new_conversation_turn(self):
        """new_conversation_turn 后 loop_turns 归零。"""
        h = _make_harness()
        h.state.loop_turns = 15  # 模拟第一轮用了 15 个 loop turns
        h.new_conversation_turn()
        assert h.state.loop_turns == 0
        assert h.state.conversation_turns == 1

    def test_conversation_turns_accumulate(self):
        """多次 new_conversation_turn 累加。"""
        h = _make_harness()
        h.new_conversation_turn()
        h.new_conversation_turn()
        h.new_conversation_turn()
        assert h.state.conversation_turns == 3


# ============================================================
# Test 4: talk_to_user 不需要 expects_reply 也能暂停
# ============================================================

class TestTalkWithoutExpectsReply:
    """talk_to_user 不设 expects_reply 时也应暂停循环。"""

    def test_talk_without_expects_reply_still_pauses(self):
        h = _make_harness()
        script = [
            {"content": "展示结论。", "tool_calls": [
                {"name": "talk_to_user", "arguments": {
                    "message": "以下是我的审阅结论..."
                    # 注意：没有 expects_reply
                }}
            ]},
        ]
        messages = _make_messages(h)
        result = _run(cognitive_loop(messages, h, SCHOLAR_TOOLS, MockLLMClient(script), verbose=False))

        assert isinstance(result, LoopTalk)
        assert result.expects_reply is False
        assert "审阅结论" in result.message


# ============================================================
# Test 5: identity 中对话能力的描述检查
# ============================================================

class TestIdentityDialogueAwareness:
    """验证 identity 中有对话协作相关的认知描述。"""

    def test_identity_mentions_dialogue(self):
        assert "对话" in SCHOLAR_IDENTITY

    def test_identity_mentions_user_interaction(self):
        assert "用户" in SCHOLAR_IDENTITY

    def test_talk_tool_description_is_cognitive(self):
        """talk_to_user 工具描述应该是认知性的（讨论、确认），不是机械性的。"""
        talk_tool = next(t for t in SCHOLAR_TOOLS if t["name"] == "talk_to_user")
        desc = talk_tool["description"]
        # 应该提到讨论、确认方向
        assert "讨论" in desc or "确认" in desc


# ============================================================
# 运行入口
# ============================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
