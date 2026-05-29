"""
Tests for V3 Phase 0.5: SignalDispatcher + EvidenceChain.
"""

import pytest

from core.signal_dispatcher import SignalDispatcher, HarnessSignal
from core.evidence_chain import EvidenceStep, EvidenceChain, EvidenceChainTracker


# ==============================================================
# SignalDispatcher Tests
# ==============================================================


class TestSignalDispatcherEmpty:
    """空输入测试。"""

    def test_dispatch_no_signals(self):
        dispatcher = SignalDispatcher()
        result = dispatcher.dispatch(current_turn=1)
        assert result == []

    def test_dispatch_after_submit_nothing(self):
        dispatcher = SignalDispatcher()
        # No submit, just dispatch
        assert dispatcher.dispatch(current_turn=0) == []


class TestSignalDispatcherDoom:
    """Priority 0 (doom) 始终通过。"""

    def test_doom_always_passes(self):
        dispatcher = SignalDispatcher()
        dispatcher.submit(HarnessSignal(source="doom_check", priority=0, message="STOP"))
        result = dispatcher.dispatch(current_turn=1)
        assert "STOP" in result

    def test_doom_does_not_count_against_max(self):
        """Doom 信号不占用 MAX_SIGNALS_PER_TURN 配额。"""
        dispatcher = SignalDispatcher()
        # Submit doom + 2 non-doom (max is 2 by default)
        dispatcher.submit(HarnessSignal(source="doom1", priority=0, message="doom_msg"))
        dispatcher.submit(HarnessSignal(source="src_a", priority=1, message="msg_a"))
        dispatcher.submit(HarnessSignal(source="src_b", priority=2, message="msg_b"))
        result = dispatcher.dispatch(current_turn=1)
        # All 3 should pass: doom + 2 non-doom within max
        assert len(result) == 3
        assert "doom_msg" in result


class TestSignalDispatcherMaxPerTurn:
    """MAX_SIGNALS_PER_TURN 限制非 doom 信号。"""

    def test_limits_non_doom(self):
        dispatcher = SignalDispatcher()
        # Submit 4 non-doom signals, default max is 2
        dispatcher.submit(HarnessSignal(source="a", priority=1, message="a_msg"))
        dispatcher.submit(HarnessSignal(source="b", priority=2, message="b_msg"))
        dispatcher.submit(HarnessSignal(source="c", priority=3, message="c_msg"))
        dispatcher.submit(HarnessSignal(source="d", priority=3, message="d_msg"))
        result = dispatcher.dispatch(current_turn=1)
        # Only top 2 by priority should pass
        assert len(result) == 2
        assert "a_msg" in result
        assert "b_msg" in result

    def test_custom_max_via_class_attr(self):
        """修改类属性 MAX_SIGNALS_PER_TURN 改变限制。"""
        dispatcher = SignalDispatcher()
        original_max = dispatcher.MAX_SIGNALS_PER_TURN
        dispatcher.MAX_SIGNALS_PER_TURN = 1
        try:
            dispatcher.submit(HarnessSignal(source="x", priority=1, message="x1"))
            dispatcher.submit(HarnessSignal(source="y", priority=2, message="x2"))
            result = dispatcher.dispatch(current_turn=1)
            assert len(result) == 1
        finally:
            dispatcher.MAX_SIGNALS_PER_TURN = original_max


class TestSignalDispatcherDedup:
    """去重窗口测试。"""

    def test_dedup_within_window(self):
        dispatcher = SignalDispatcher()
        # Turn 1: submit and dispatch
        dispatcher.submit(HarnessSignal(source="checker", priority=1, message="msg1"))
        dispatcher.dispatch(current_turn=1)

        # Turn 2: same source within DEDUP_WINDOW (default 3) should be deduped
        dispatcher.submit(HarnessSignal(source="checker", priority=1, message="msg2"))
        result = dispatcher.dispatch(current_turn=2)
        assert result == []

    def test_dedup_expires_after_window(self):
        dispatcher = SignalDispatcher()
        # Turn 1: dispatch
        dispatcher.submit(HarnessSignal(source="checker", priority=1, message="msg1"))
        dispatcher.dispatch(current_turn=1)

        # Turn 4: DEDUP_WINDOW=3 means turns 1,2,3 are within window,
        # turn 4 is outside (4-1=3 which is NOT < 3), so should pass
        dispatcher.submit(HarnessSignal(source="checker", priority=1, message="msg_back"))
        result = dispatcher.dispatch(current_turn=4)
        assert "msg_back" in result


class TestSignalDispatcherSuppressIf:
    """suppress_if 逻辑测试。"""

    def test_suppress_if_removes_signal(self):
        dispatcher = SignalDispatcher()
        # 'nudge' should be suppressed if 'budget_warn' is selected
        dispatcher.submit(HarnessSignal(source="budget_warn", priority=1, message="budget!"))
        dispatcher.submit(HarnessSignal(
            source="nudge", priority=2, message="nudge_msg",
            suppress_if=["budget_warn"]
        ))
        result = dispatcher.dispatch(current_turn=1)
        assert "budget!" in result
        assert "nudge_msg" not in result

    def test_no_suppression_when_blocker_absent(self):
        dispatcher = SignalDispatcher()
        # suppress_if references 'budget_warn' but it's not in this batch
        dispatcher.submit(HarnessSignal(
            source="nudge", priority=2, message="nudge_msg",
            suppress_if=["budget_warn"]
        ))
        result = dispatcher.dispatch(current_turn=1)
        assert "nudge_msg" in result


class TestSignalDispatcherPrioritySorting:
    """优先级排序: 数字越小优先级越高。"""

    def test_sorted_by_priority_asc(self):
        dispatcher = SignalDispatcher()
        dispatcher.submit(HarnessSignal(source="low", priority=3, message="low_msg"))
        dispatcher.submit(HarnessSignal(source="high", priority=1, message="high_msg"))
        dispatcher.submit(HarnessSignal(source="mid", priority=2, message="mid_msg"))
        result = dispatcher.dispatch(current_turn=1)
        # Max is 2, so only priority 1 and 2 pass
        assert result == ["high_msg", "mid_msg"]


class TestSignalDispatcherReset:
    """重置历史。"""

    def test_reset_clears_history(self):
        dispatcher = SignalDispatcher()
        dispatcher.submit(HarnessSignal(source="checker", priority=1, message="msg"))
        dispatcher.dispatch(current_turn=1)

        # Should be deduped
        dispatcher.submit(HarnessSignal(source="checker", priority=1, message="msg2"))
        assert dispatcher.dispatch(current_turn=2) == []

        # After reset, should pass again
        dispatcher.reset()
        dispatcher.submit(HarnessSignal(source="checker", priority=1, message="msg3"))
        result = dispatcher.dispatch(current_turn=3)
        assert "msg3" in result


# ==============================================================
# EvidenceChain Tests
# ==============================================================


class TestEvidenceStepDefaults:
    """EvidenceStep 数据结构。"""

    def test_default_fields(self):
        step = EvidenceStep(action="read_section", target="Introduction")
        assert step.action == "read_section"
        assert step.target == "Introduction"
        assert step.observation == ""
        assert step.turn == 0
        assert step.pcg_edge_used == ""


class TestEvidenceChainProperties:
    """EvidenceChain 属性。"""

    def test_empty_chain(self):
        chain = EvidenceChain(finding_id="f1", finding_text="Test finding")
        assert chain.chain_length == 0
        assert chain.total_turns_span == 0
        assert chain.pcg_edges_used == 0

    def test_chain_with_steps(self):
        chain = EvidenceChain(
            finding_id="f1",
            finding_text="A problem",
            steps=[
                EvidenceStep(action="read_section", target="Intro", turn=1),
                EvidenceStep(action="hypothesis_formed", target="H1", turn=3,
                             pcg_edge_used="Intro->Methods"),
            ]
        )
        assert chain.chain_length == 2
        assert chain.total_turns_span == 3  # turns 1-3
        assert chain.pcg_edges_used == 1

    def test_summary_format(self):
        chain = EvidenceChain(
            finding_id="f1",
            finding_text="Some issue found",
            steps=[
                EvidenceStep(action="read_section", target="X", turn=1),
                EvidenceStep(action="cross_ref", target="Y", turn=5),
            ]
        )
        s = chain.summary
        assert "f1" in s
        assert "read_section" in s
        assert "cross_ref" in s

    def test_format_full(self):
        chain = EvidenceChain(
            finding_id="f1",
            finding_text="Issue",
            priority="high",
            steps=[
                EvidenceStep(action="read_section", target="Methods", observation="found gap", turn=2),
            ]
        )
        full = chain.format_full()
        assert "Evidence Chain: f1" in full
        assert "found gap" in full
        assert "high" in full

    def test_to_dict(self):
        chain = EvidenceChain(
            finding_id="f1",
            finding_text="Issue",
            steps=[
                EvidenceStep(action="read_section", target="Intro", turn=1),
            ]
        )
        d = chain.to_dict()
        assert d["finding_id"] == "f1"
        assert d["chain_length"] == 1
        assert len(d["steps"]) == 1
        assert d["steps"][0]["action"] == "read_section"


class TestEvidenceChainTracker:
    """EvidenceChainTracker 核心功能。"""

    def test_start_chain(self):
        tracker = EvidenceChainTracker()
        tracker.start_chain("f1", "Test finding", "high")
        assert tracker.active_count == 1
        chain = tracker.get_chain("f1")
        assert chain is not None
        assert chain.finding_text == "Test finding"
        assert chain.priority == "high"

    def test_add_step(self):
        tracker = EvidenceChainTracker()
        tracker.start_chain("f1")
        tracker.add_step("f1", action="read_section", target="Intro", observation="ok", turn=1)
        chain = tracker.get_chain("f1")
        assert chain.chain_length == 1
        assert chain.steps[0].action == "read_section"

    def test_add_step_nonexistent_chain(self):
        """向不存在的 chain 添加步骤应静默跳过。"""
        tracker = EvidenceChainTracker()
        # Should not raise
        tracker.add_step("nonexistent", action="read", target="X")
        assert tracker.active_count == 0

    def test_add_step_to_recent(self):
        tracker = EvidenceChainTracker()
        tracker.start_chain("f1")
        tracker.start_chain("f2")
        # add_step_to_recent adds to most recently created chain (f2)
        tracker.add_step_to_recent(action="search", target="lit")
        chain_f1 = tracker.get_chain("f1")
        chain_f2 = tracker.get_chain("f2")
        assert chain_f1.chain_length == 0
        assert chain_f2.chain_length == 1

    def test_add_step_to_recent_no_chain(self):
        """没有 active chain 时 add_step_to_recent 应静默。"""
        tracker = EvidenceChainTracker()
        # Should not raise
        tracker.add_step_to_recent(action="read", target="X")

    def test_complete_chain(self):
        tracker = EvidenceChainTracker()
        tracker.start_chain("f1")
        tracker.add_step("f1", action="read", target="X", turn=1)
        chain = tracker.complete_chain("f1")
        assert chain is not None
        assert tracker.active_count == 0
        # Should still be retrievable from completed
        assert tracker.get_chain("f1") is not None

    def test_finalize_all(self):
        tracker = EvidenceChainTracker()
        tracker.start_chain("f1", "Finding 1")
        tracker.start_chain("f2", "Finding 2")
        tracker.add_step("f1", action="read", target="X", turn=1)
        tracker.add_step("f2", action="search", target="Y", turn=2)

        all_chains = tracker.finalize_all()
        assert len(all_chains) == 2
        assert tracker.active_count == 0
        # All moved to completed
        assert tracker.total_count == 2

    def test_get_all_summaries(self):
        tracker = EvidenceChainTracker()
        tracker.start_chain("f1", "First issue")
        tracker.add_step("f1", action="read", target="Intro", turn=1)
        summaries = tracker.get_all_summaries()
        assert "f1" in summaries
        assert "First issue" in summaries

    def test_reset(self):
        tracker = EvidenceChainTracker()
        tracker.start_chain("f1")
        tracker.start_chain("f2")
        tracker.reset()
        assert tracker.active_count == 0
        assert tracker.total_count == 0
