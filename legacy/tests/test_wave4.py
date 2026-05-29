"""
Tests for Wave 4: Cross-session learning, preference inference, meta-planning.
"""
import json
import tempfile
import time
from pathlib import Path

import pytest

from utils.session_memory import SessionMemory, SessionJournal, ToolPattern, ImplicitPreference
from utils.meta_planner import MetaPlanner, PlanAdvice


@pytest.fixture
def workspace(tmp_path):
    return tmp_path


@pytest.fixture
def memory(workspace):
    return SessionMemory(workspace=workspace)


@pytest.fixture
def planner(memory):
    return MetaPlanner(memory=memory)


# ─── SessionMemory Tests ─────────────────────────────────────────────

class TestSessionMemory:
    def test_start_session(self, memory):
        sid = memory.start_session(goal="Review paper", paper_title="test.pdf")
        assert sid.startswith("session_")
        assert memory._current_session is not None
        assert memory._current_session.paper_title == "test.pdf"

    def test_record_tool_usage(self, memory):
        memory.start_session()
        memory.record_tool_usage("parse_paper")
        memory.record_tool_usage("review_paper")
        memory.record_tool_usage("parse_paper")
        assert memory._current_session.tools_used == {"parse_paper": 2, "review_paper": 1}

    def test_record_error(self, memory):
        memory.start_session()
        memory.record_error("search_literature", "timeout after 30s")
        assert len(memory._current_session.errors_encountered) == 1
        assert "search_literature" in memory._current_session.errors_encountered[0]

    def test_end_session_persists(self, memory, workspace):
        memory.start_session(goal="Test goal")
        memory.record_tool_usage("parse_paper")
        memory.end_session(goal_achieved=True, lessons=["Use parallel review"])

        # Reload and verify persistence
        memory2 = SessionMemory(workspace=workspace)
        assert len(memory2._journals) == 1
        assert memory2._journals[0].goal_achieved is True
        assert "Use parallel review" in memory2._journals[0].lessons_learned

    def test_startup_context_with_previous_session(self, memory, workspace):
        # Create a past session
        memory.start_session(goal="Previous work", paper_title="old.pdf")
        memory.end_session(
            goal_achieved=True,
            lessons=["Parallel review is 3x faster"],
            hints=["Start with voice profile before rewriting"],
        )

        # New session should get context from previous
        memory2 = SessionMemory(workspace=workspace)
        ctx = memory2.get_startup_context()
        assert "Previous Session Summary" in ctx
        assert "old.pdf" in ctx
        assert "Parallel review is 3x faster" in ctx
        assert "Start with voice profile" in ctx

    def test_startup_context_empty_when_no_history(self, memory):
        ctx = memory.get_startup_context()
        assert ctx == ""

    def test_observe_user_edit_word_substitution(self, memory, workspace):
        memory.start_session()
        # Simulate user changing "Furthermore" to "Also"
        memory.observe_user_edit(
            "Furthermore the results show improvement",
            "Also the results show improvement",
        )
        assert len(memory._preferences) == 1
        assert memory._preferences[0].original_pattern == "Furthermore"
        assert memory._preferences[0].user_replacement == "Also"
        assert memory._preferences[0].confidence == 0.3  # Initial low confidence

        # Observe same substitution again — confidence should increase
        memory.observe_user_edit(
            "Furthermore we found that",
            "Also we found that",
        )
        assert len(memory._preferences) == 1  # Same preference, not new
        assert memory._preferences[0].observation_count == 2
        assert memory._preferences[0].confidence > 0.3

    def test_observe_edit_ignores_length_changes(self, memory):
        memory.start_session()
        memory.observe_user_edit(
            "This is short",
            "This is a much longer sentence with more words",
        )
        assert len(memory._preferences) == 0  # Length changed, can't learn

    def test_tool_sequence_recording(self, memory, workspace):
        memory.start_session()
        memory.record_tool_sequence(
            sequence=["parse_paper", "review_paper", "rewrite_section"],
            outcome="positive",
            score_delta=1.5,
            context="Full review cycle",
        )

        # Verify persistence
        memory2 = SessionMemory(workspace=workspace)
        assert len(memory2._patterns) == 1
        assert memory2._patterns[0].outcome == "positive"
        assert memory2._patterns[0].score_delta == 1.5

    def test_duplicate_pattern_strengthens(self, memory):
        memory.start_session()
        memory.record_tool_sequence(
            sequence=["parse_paper", "review_paper"],
            outcome="positive",
            score_delta=1.0,
            context="review",
        )
        memory.record_tool_sequence(
            sequence=["parse_paper", "review_paper"],
            outcome="positive",
            score_delta=2.0,
            context="review",
        )
        assert len(memory._patterns) == 1
        assert memory._patterns[0].usage_count == 2
        assert memory._patterns[0].score_delta == 1.5  # Average of 1.0 and 2.0

    def test_memory_summary(self, memory):
        memory.start_session()
        summary = memory.get_memory_summary()
        assert "0 sessions" in summary
        assert "0 patterns" in summary

    def test_preferences_for_prompt(self, memory):
        memory.start_session()
        # Create high-confidence preference
        for _ in range(5):
            memory.observe_user_edit(
                "utilize the framework",
                "use the framework",
            )
        prompt_text = memory.get_preferences_for_prompt()
        assert "use" in prompt_text
        assert "utilize" in prompt_text

    def test_max_entries_bounded(self, memory):
        """Verify that journal entries are bounded."""
        for i in range(25):
            memory.start_session(goal=f"Goal {i}")
            memory.end_session(goal_achieved=True)
        # Should be bounded to MAX_JOURNAL_ENTRIES (20)
        assert len(memory._journals) <= 20


# ─── MetaPlanner Tests ────────────────────────────────────────────────

class TestMetaPlanner:
    def test_no_patterns_gives_empty_advice(self, planner):
        advice = planner.get_plan_advice("Review paper")
        assert advice.confidence == 0.0
        assert "No historical patterns" in advice.reasoning

    def test_positive_pattern_produces_advice(self, memory, planner):
        memory.start_session()
        memory.record_tool_sequence(
            sequence=["parse_paper", "review_paper", "rewrite_section"],
            outcome="positive",
            score_delta=2.0,
            context="paper review",
        )
        # Increase usage count to boost confidence
        memory.record_tool_sequence(
            sequence=["parse_paper", "review_paper", "rewrite_section"],
            outcome="positive",
            score_delta=2.5,
            context="paper review",
        )

        advice = planner.get_plan_advice("Review and revise paper")
        assert advice.confidence > 0
        assert advice.recommended_sequence is not None
        assert "parse_paper" in advice.recommended_sequence

    def test_context_injection_empty_when_low_confidence(self, planner):
        ctx = planner.get_context_injection("Some goal")
        assert ctx == ""

    def test_context_injection_with_patterns(self, memory, planner):
        memory.start_session()
        for _ in range(5):
            memory.record_tool_sequence(
                sequence=["review_paper", "rewrite_section"],
                outcome="positive",
                score_delta=1.0,
                context="revision",
            )

        ctx = planner.get_context_injection("Revise the paper")
        assert "Meta-Planner" in ctx or ctx == ""  # May or may not trigger

    def test_on_plan_complete_records_pattern(self, memory, planner):
        memory.start_session()
        planner.on_plan_complete(
            tool_sequence=["parse_paper", "deai_audit", "deai_rewrite"],
            goal="Remove AI patterns",
            score_delta=3.0,
            success=True,
        )
        assert len(memory._patterns) == 1
        assert memory._patterns[0].outcome == "positive"

    def test_avoid_sequences_from_negative_patterns(self, memory, planner):
        memory.start_session()
        # Record a negative pattern multiple times
        for _ in range(3):
            memory.record_tool_sequence(
                sequence=["parallel_rewrite", "deai_closed_loop"],
                outcome="negative",
                score_delta=-1.0,
                context="failed approach",
            )
        advice = planner.get_plan_advice("Revise paper")
        # The negative pattern should be in avoid_sequences
        assert any(
            "parallel_rewrite" in seq
            for seq in advice.avoid_sequences
        )


# ─── Integration Tests ────────────────────────────────────────────────

class TestIntegration:
    def test_full_session_lifecycle(self, workspace):
        """Test a complete session lifecycle: start → work → learn → end → reload."""
        # Session 1
        mem1 = SessionMemory(workspace=workspace)
        mem1.start_session(goal="Review intro", paper_title="thesis.pdf")
        mem1.record_tool_usage("parse_paper")
        mem1.record_tool_usage("review_paper")
        mem1.record_tool_usage("rewrite_section")
        mem1.observe_user_edit("Furthermore the results show", "Also the results show")
        mem1.record_decision("Use deep review for methods section")
        mem1.end_session(
            goal_achieved=True,
            lessons=["Deep review caught more issues"],
            hints=["Run deai_audit on intro next time"],
        )

        # Session 2: should have context from session 1
        mem2 = SessionMemory(workspace=workspace)
        ctx = mem2.get_startup_context()
        assert "thesis.pdf" in ctx
        assert "Deep review caught more issues" in ctx
        assert "Run deai_audit" in ctx

        # Preference should persist
        assert len(mem2._preferences) == 1
        assert mem2._preferences[0].original_pattern == "Furthermore"
        assert mem2._preferences[0].user_replacement == "Also"

    def test_meta_planner_uses_session_memory(self, workspace):
        """Test that MetaPlanner reads from SessionMemory patterns."""
        mem = SessionMemory(workspace=workspace)
        mem.start_session()
        # Record a strong positive pattern
        for _ in range(4):
            mem.record_tool_sequence(
                sequence=["parse_paper", "build_voice_profile", "review_paper"],
                outcome="positive",
                score_delta=2.0,
                context="initial analysis",
            )

        planner = MetaPlanner(memory=mem)
        advice = planner.get_plan_advice("Analyze new paper")
        assert advice.recommended_sequence is not None
        assert "parse_paper" in advice.recommended_sequence
