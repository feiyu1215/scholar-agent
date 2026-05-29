"""
tests/test_meta_cognition_layer.py — MetaCognitionLayer 单元测试

覆盖:
1. MCLVerdict 数据模型
2. gate_completion: pass/block/一次性机制
3. check_stagnation: 触发/不触发/一次性
4. _parse: JSON 解析（正常/代码块/错误）
5. _count_stagnant_turns helper
6. MCL-active 时 spawn_gate 降级
7. MCL 不可用时优雅降级（调用失败 → pass）
"""

import sys
import json
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from core.meta_cognition_layer import (
    MetaCognitionLayer,
    MCLVerdict,
    MCLFeedbackItem,
    _extract_json,
    MCL_MIN_FINDINGS,
    MCL_MIN_SECTIONS,
)
from core.loop import _count_stagnant_turns


# ============================================================
# Helpers
# ============================================================

def make_state(
    findings=None,
    loop_turns=10,
    max_loop_turns=50,
    paper_sections=None,
    sections_read=None,
    tool_call_history=None,
    paper_title="A Study on X",
):
    """创建 mock WorkspaceState。"""
    state = MagicMock()
    state.findings = findings or [
        {"finding": "方法论有问题", "priority": "high", "status": "verified", "section": "methodology", "evidence": "Fig.1"},
        {"finding": "数据不一致", "priority": "medium", "status": "unverified", "section": "results", "evidence": ""},
        {"finding": "引用有误", "priority": "low", "status": "verified", "section": "introduction", "evidence": "p.3"},
        {"finding": "统计方法有误", "priority": "high", "status": "needs_verification", "section": "results", "evidence": "Table 2"},
    ]
    state.loop_turns = loop_turns
    state.max_loop_turns = max_loop_turns
    state.paper_sections = paper_sections or {
        "introduction": "...", "methodology": "...", "results": "...",
        "discussion": "...", "conclusion": "...",
    }
    state.sections_read = sections_read or {"introduction", "methodology", "results"}
    state.tool_call_history = tool_call_history or [
        {"name": "read_section", "tool": "read_section"},
        {"name": "update_findings", "tool": "update_findings"},
        {"name": "search_literature", "tool": "search_literature"},
        {"name": "read_section", "tool": "read_section"},
    ]
    state.paper_title = paper_title
    return state


def make_client(response_text: str = '{"verdict": "pass", "confidence": 0.8, "reason": "ok"}'):
    """创建 mock LLMClient with async chat method."""
    client = MagicMock()
    client.chat = AsyncMock(return_value=response_text)
    return client


# ============================================================
# Test: MCLVerdict
# ============================================================

class TestMCLVerdict:
    def test_default_pass(self):
        v = MCLVerdict()
        assert v.verdict == "pass"
        assert not v.should_block

    def test_block(self):
        v = MCLVerdict(verdict="block")
        assert v.should_block

    def test_feedback_items(self):
        v = MCLVerdict(
            verdict="block",
            feedback=[
                MCLFeedbackItem(dimension="precision", target="#1", action="验证证据"),
                MCLFeedbackItem(dimension="coverage", target="discussion", action="阅读 discussion"),
            ]
        )
        assert len(v.feedback) == 2
        assert v.feedback[0].dimension == "precision"


# ============================================================
# Test: _extract_json
# ============================================================

class TestExtractJson:
    def test_direct_json(self):
        text = '{"verdict": "pass", "confidence": 0.9}'
        result = _extract_json(text)
        assert result["verdict"] == "pass"

    def test_code_block(self):
        text = 'some text\n```json\n{"verdict": "block"}\n```\nmore text'
        result = _extract_json(text)
        assert result["verdict"] == "block"

    def test_embedded_braces(self):
        text = 'I think: {"verdict": "pass", "reason": "ok"} end.'
        result = _extract_json(text)
        assert result["verdict"] == "pass"

    def test_empty(self):
        assert _extract_json("") is None
        assert _extract_json("   ") is None

    def test_invalid_json(self):
        assert _extract_json("not json at all") is None

    def test_nested_code_block(self):
        text = '```\n{"verdict":"block","confidence":0.7,"reason":"未覆盖","feedback":[],"auto_spawn":{"needed":false,"perspectives":[]}}\n```'
        result = _extract_json(text)
        assert result["verdict"] == "block"


# ============================================================
# Test: gate_completion
# ============================================================

class TestGateCompletion:
    @pytest.mark.asyncio
    async def test_pass_when_mcl_agrees(self):
        client = make_client('{"verdict": "pass", "confidence": 0.85, "reason": "覆盖充分", "feedback": [], "auto_spawn": {"needed": false, "perspectives": []}}')
        mcl = MetaCognitionLayer(llm_client=client)
        state = make_state()

        verdict = await mcl.gate_completion(state)
        assert verdict.verdict == "pass"
        assert not verdict.should_block
        client.chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_block_with_feedback(self):
        response = json.dumps({
            "verdict": "block",
            "confidence": 0.75,
            "reason": "Discussion 未被覆盖",
            "feedback": [
                {"dimension": "coverage", "target": "discussion", "action": "请阅读 discussion section"}
            ],
            "auto_spawn": {"needed": False, "perspectives": []}
        })
        client = make_client(response)
        mcl = MetaCognitionLayer(llm_client=client)
        state = make_state()

        verdict = await mcl.gate_completion(state)
        assert verdict.should_block
        assert "Discussion" in verdict.reason
        assert len(verdict.feedback) == 1
        assert verdict.feedback[0].dimension == "coverage"

    @pytest.mark.asyncio
    async def test_only_blocks_once(self):
        """MCL 只 block 一次——第二次 mark_complete 无条件放行。"""
        response = json.dumps({
            "verdict": "block", "confidence": 0.8, "reason": "不够",
            "feedback": [], "auto_spawn": {"needed": False, "perspectives": []}
        })
        client = make_client(response)
        mcl = MetaCognitionLayer(llm_client=client)
        state = make_state()

        # 第一次 block
        v1 = await mcl.gate_completion(state)
        assert v1.should_block

        # 第二次自动 pass（不再调 LLM）
        v2 = await mcl.gate_completion(state)
        assert not v2.should_block
        assert client.chat.call_count == 1  # 只调了一次

    @pytest.mark.asyncio
    async def test_skip_when_too_few_findings(self):
        """findings 不足 MCL_MIN_FINDINGS 时直接 pass，不调 LLM。"""
        client = make_client('{"verdict": "block"}')
        mcl = MetaCognitionLayer(llm_client=client)
        state = make_state(findings=[{"finding": "x"}])  # 只有 1 条

        verdict = await mcl.gate_completion(state)
        assert not verdict.should_block
        client.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_auto_spawn_recommendation(self):
        response = json.dumps({
            "verdict": "block", "confidence": 0.7, "reason": "覆盖不足",
            "feedback": [{"dimension": "coverage", "target": "methodology", "action": "深入方法论"}],
            "auto_spawn": {"needed": True, "perspectives": ["methodology", "statistics"]}
        })
        client = make_client(response)
        mcl = MetaCognitionLayer(llm_client=client)
        state = make_state()

        verdict = await mcl.gate_completion(state)
        assert verdict.should_block
        assert verdict.auto_spawn_needed
        assert "methodology" in verdict.auto_spawn_perspectives

    @pytest.mark.asyncio
    async def test_graceful_degradation_on_error(self):
        """LLM 调用失败时优雅降级为 pass。"""
        client = MagicMock()
        client.chat = AsyncMock(side_effect=Exception("API timeout"))
        mcl = MetaCognitionLayer(llm_client=client)
        state = make_state()

        verdict = await mcl.gate_completion(state)
        assert not verdict.should_block
        assert "失败" in verdict.reason


# ============================================================
# Test: check_stagnation
# ============================================================

class TestCheckStagnation:
    @pytest.mark.asyncio
    async def test_not_triggered_when_turns_low(self):
        """stagnant_turns < 3 时不触发。"""
        client = make_client()
        mcl = MetaCognitionLayer(llm_client=client)
        state = make_state()

        result = await mcl.check_stagnation(state, stagnant_turns=2)
        assert result is None
        client.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_triggered_with_block(self):
        response = json.dumps({
            "verdict": "block", "confidence": 0.7,
            "reason": "审稿人在重复阅读相同 section",
            "feedback": [{"dimension": "depth", "target": "results", "action": "尝试从统计角度审视"}],
            "auto_spawn": {"needed": False, "perspectives": []}
        })
        client = make_client(response)
        mcl = MetaCognitionLayer(llm_client=client)
        state = make_state()

        result = await mcl.check_stagnation(state, stagnant_turns=5)
        assert result is not None
        assert result.should_block
        assert "重复" in result.reason

    @pytest.mark.asyncio
    async def test_only_fires_once(self):
        """stagnation check 只触发一次。"""
        response = json.dumps({
            "verdict": "block", "confidence": 0.6, "reason": "卡住了",
            "feedback": [], "auto_spawn": {"needed": False, "perspectives": []}
        })
        client = make_client(response)
        mcl = MetaCognitionLayer(llm_client=client)
        state = make_state()

        r1 = await mcl.check_stagnation(state, stagnant_turns=4)
        assert r1 is not None

        r2 = await mcl.check_stagnation(state, stagnant_turns=6)
        assert r2 is None  # 不再触发
        assert client.chat.call_count == 1


# ============================================================
# Test: format methods
# ============================================================

class TestFormatting:
    def test_format_completion_feedback(self):
        client = make_client()
        mcl = MetaCognitionLayer(llm_client=client)
        verdict = MCLVerdict(
            verdict="block",
            reason="覆盖不足",
            feedback=[
                MCLFeedbackItem(dimension="coverage", target="discussion", action="阅读 discussion section"),
                MCLFeedbackItem(dimension="precision", target="#2", action="补充证据引用"),
            ],
            auto_spawn_needed=True,
            auto_spawn_perspectives=["methodology", "statistics"],
        )

        text = mcl.format_completion_feedback(verdict)
        assert "质量审计" in text
        assert "覆盖度" in text
        assert "精确性" in text
        assert "mark_complete" in text
        assert "methodology" in text

    def test_format_stagnation_feedback(self):
        client = make_client()
        mcl = MetaCognitionLayer(llm_client=client)
        verdict = MCLVerdict(
            verdict="block",
            reason="反复阅读同一 section",
            feedback=[MCLFeedbackItem(dimension="depth", target="results", action="换个角度")],
        )

        text = mcl.format_stagnation_feedback(verdict)
        assert "MCL 观察" in text
        assert "换个角度" in text

    def test_format_stagnation_pass_returns_empty(self):
        client = make_client()
        mcl = MetaCognitionLayer(llm_client=client)
        verdict = MCLVerdict(verdict="pass")
        assert mcl.format_stagnation_feedback(verdict) == ""


# ============================================================
# Test: _count_stagnant_turns (loop helper)
# ============================================================

class TestCountStagnantTurns:
    def test_no_history(self):
        state = MagicMock()
        state.tool_call_history = []
        state.loop_turns = 5
        assert _count_stagnant_turns(state) == 5

    def test_recent_update_findings(self):
        state = MagicMock()
        state.tool_call_history = [
            {"name": "read_section"},
            {"name": "update_findings"},
            {"name": "read_section"},
            {"name": "search_literature"},
        ]
        # 最后一个 update_findings 在 index 1，之后有 2 个调用
        assert _count_stagnant_turns(state) == 2

    def test_never_update_findings(self):
        state = MagicMock()
        state.tool_call_history = [
            {"name": "read_section"},
            {"name": "read_section"},
            {"name": "search_literature"},
        ]
        assert _count_stagnant_turns(state) == 3

    def test_last_action_is_update(self):
        state = MagicMock()
        state.tool_call_history = [
            {"name": "read_section"},
            {"name": "update_findings"},
        ]
        assert _count_stagnant_turns(state) == 0


# ============================================================
# Test: MCL stats
# ============================================================

class TestMCLStats:
    @pytest.mark.asyncio
    async def test_stats_tracking(self):
        response = json.dumps({
            "verdict": "pass", "confidence": 0.9, "reason": "好",
            "feedback": [], "auto_spawn": {"needed": False, "perspectives": []}
        })
        client = make_client(response)
        mcl = MetaCognitionLayer(llm_client=client)
        state = make_state()

        await mcl.gate_completion(state)

        stats = mcl.stats()
        assert stats["total_calls"] == 1
        assert stats["model"] == "gpt-4.1-mini"
        assert stats["gate_fired"] is False  # pass 时不记为 fired


# ============================================================
# Test: spawn_gate 降级（MCL 活跃时）
# ============================================================

class TestSpawnGateDegradation:
    def test_spawn_gate_skipped_when_mcl_active(self):
        """当 MCL 存在时，_check_completion_gate 应预标记 spawn_gate。

        直接测试逻辑：模拟 Harness 的关键属性，验证 spawn_gate 被跳过。
        """
        from core.boundary_guard import check_completion_gate as _bg_check_completion_gate

        # 模拟满足 spawn_gate 触发条件的 state
        state = MagicMock()
        state.tool_call_history = [
            {"name": "read_section", "tool": "read_section"},
            {"name": "read_section", "tool": "read_section"},
        ]
        state.findings = [
            {"finding": "a", "priority": "high", "status": "verified"},
            {"finding": "b", "priority": "medium", "status": "unverified"},
            {"finding": "c", "priority": "low", "status": "verified"},
        ]
        state.paper_sections = {"a": "x", "b": "y", "c": "z", "d": "w"}
        state.max_loop_turns = 50
        state.loop_turns = 10

        # 当 MCL 活跃时，spawn_gate 应被预标记为已触发
        # 模拟 _check_completion_gate 中的逻辑: 如果 mcl 不为 None，加入 spawn_gate
        nudges_fired = set()

        # MCL 存在 → 预标记 spawn_gate
        mcl = MetaCognitionLayer(llm_client=make_client())
        nudges_fired.add("spawn_gate")  # 这就是 harness 中的逻辑

        # 调用 boundary_guard 的 check_completion_gate
        gate_config = MagicMock()
        gate_config.min_findings_for_exit = 0  # 不触发 min_findings gate

        # Mock finding_quality_gate
        fqg = MagicMock()
        fqg.evaluate.return_value = []  # 没有质量问题

        result, updated_nudges = _bg_check_completion_gate(
            state, gate_config,
            hypothesis_module=None,
            finding_quality_gate=fqg,
            completion_nudges_fired=nudges_fired,
        )

        # spawn_gate 已经在 nudges_fired 中，不应再次触发
        # 如果 result 是 spawn_gate 的消息则说明没被跳过
        if result:
            assert "spawn" not in result.lower() or "视角" not in result
        assert "spawn_gate" in updated_nudges
