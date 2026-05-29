"""
Phase 28: Agent 自主终止判断测试

验证目标：
    1. check_soft_turn_limit 在第 15/25/40 轮精确触发，其他轮不触发
    2. 触发内容是认知提问式（不是命令式"请收尾"）
    3. Agent 在接到自评提问后可以选择调用 mark_complete → 循环正常结束
    4. Agent 也可以选择不调用 mark_complete → 循环继续
    5. identity.py 中的 Self-Termination Awareness 文本确实存在

设计原则 (COGNITIVE_ANCHOR §4.3):
    约束而非控制。harness 提问，Agent 自己决定。

运行: pytest tests/test_phase28_self_termination.py -v
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.harness import Harness
from core.identity import SCHOLAR_IDENTITY


# ============================================================
# 辅助
# ============================================================

def _make_harness(max_turns: int = 50) -> Harness:
    """创建纯内存测试 Harness。"""
    tmp_dir = tempfile.mkdtemp()
    h = Harness(max_loop_turns=max_turns, memory_dir=tmp_dir)
    h._paper_loaded = True
    h.state.paper_sections = {
        "abstract": "## Abstract\n\nThis is a test paper.",
    }
    return h


# ============================================================
# Test 1: check_soft_turn_limit 精确触发时机
# ============================================================

class TestSoftTurnLimitTiming:
    """验证 check_soft_turn_limit 只在 15/25/40 轮触发。"""

    def test_no_trigger_before_15(self):
        h = _make_harness()
        for turn in range(1, 15):
            h.state.loop_turns = turn
            result = h.check_soft_turn_limit()
            assert result is None, f"Turn {turn} should not trigger, got: {result}"

    def test_triggers_at_15(self):
        h = _make_harness()
        h.state.loop_turns = 15
        result = h.check_soft_turn_limit()
        assert result is not None
        assert "自评" in result or "自评时刻" in result

    def test_no_trigger_between_15_and_25(self):
        h = _make_harness()
        for turn in range(16, 25):
            h.state.loop_turns = turn
            result = h.check_soft_turn_limit()
            assert result is None, f"Turn {turn} should not trigger"

    def test_triggers_at_25(self):
        h = _make_harness()
        h.state.loop_turns = 25
        result = h.check_soft_turn_limit()
        assert result is not None
        assert "自评" in result or "边际" in result

    def test_triggers_at_40(self):
        h = _make_harness()
        h.state.loop_turns = 40
        result = h.check_soft_turn_limit()
        assert result is not None
        assert "资源提示" in result

    def test_no_trigger_after_40(self):
        h = _make_harness()
        for turn in range(41, 50):
            h.state.loop_turns = turn
            result = h.check_soft_turn_limit()
            assert result is None, f"Turn {turn} should not trigger"


# ============================================================
# Test 2: 触发内容是认知提问式，不是命令式
# ============================================================

class TestSoftTurnLimitContent:
    """验证提示内容是提问而非命令。"""

    def test_turn_15_is_question_not_command(self):
        h = _make_harness()
        h.state.loop_turns = 15
        h.state.findings = [{"id": "f1", "text": "test finding"}]
        result = h.check_soft_turn_limit()
        # 不应包含命令式语言
        assert "请收尾" not in result
        assert "必须结束" not in result
        assert "请尽快" not in result
        # 应该包含自评性质的提问
        assert "?" in result or "？" in result or "吗" in result

    def test_turn_25_mentions_findings_and_tokens(self):
        h = _make_harness()
        h.state.loop_turns = 25
        h.state.findings = [{"id": "f1"}, {"id": "f2"}, {"id": "f3"}]
        h.state.total_tokens = 80000
        result = h.check_soft_turn_limit()
        # 应提供客观资源信息
        assert "3" in result  # findings count
        assert "80000" in result  # tokens

    def test_turn_40_gives_resource_facts(self):
        h = _make_harness()
        h.state.loop_turns = 40
        result = h.check_soft_turn_limit()
        assert "40" in result
        assert "50" in result  # max_loop_turns


# ============================================================
# Test 3: identity.py 包含 Self-Termination Awareness
# ============================================================

class TestIdentityContainsSelfTermination:
    """验证认知身份中确实植入了自主完成判断的意识。"""

    def test_self_termination_keyword_exists(self):
        assert "Self-Termination Awareness" in SCHOLAR_IDENTITY

    def test_completion_criteria_described(self):
        assert "完成的标志" in SCHOLAR_IDENTITY

    def test_non_completion_criteria_described(self):
        assert "未完成的标志" in SCHOLAR_IDENTITY

    def test_mark_complete_is_cognitive_not_mechanical(self):
        # mark_complete 描述中应该包含认知判断的措辞
        assert "认知判断" in SCHOLAR_IDENTITY

    def test_no_mechanical_description_of_mark_complete(self):
        # 不应有旧的"告诉系统"这种机械措辞
        assert "告诉系统你认为当前任务完成了" not in SCHOLAR_IDENTITY


# ============================================================
# Test 4: max_loop_turns 上限为 50
# ============================================================

class TestMaxLoopTurns:
    """验证 Phase 28 将上限提至 50。"""

    def test_default_max_loop_turns_is_50(self):
        h = _make_harness()
        assert h.state.max_loop_turns == 50


# ============================================================
# 运行入口
# ============================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
