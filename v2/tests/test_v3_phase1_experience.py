"""
Tests for V3 Phase 1: Hierarchical Experience Store + IntraSession Contrast.

Covers:
- L0 section-level experience recording
- L1 session-level experience recording (V3 enhanced)
- IntraSession contrast planning and analysis
- Kill switch gating
- Sliding window limits
- Integration with session_finalizer
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import patch

import pytest

from core.memory import MemoryStore, MemoryState
from core.state import WorkspaceState
from core.evolution import IntraSessionContrastManager
from core.session_finalizer import (
    end_session,
    _record_section_experiences,
    _record_session_experience_v3,
    _analyze_and_persist_contrast,
)


@pytest.fixture
def memory_store(tmp_path):
    """Create a fresh MemoryStore for testing."""
    store = MemoryStore(str(tmp_path))
    return store


@pytest.fixture
def state_with_sections():
    """WorkspaceState with paper_sections and section_metrics."""
    state = WorkspaceState()
    state.paper_sections = {
        "title": "Test Paper Title for V3 Phase 1",
        "abstract": "This is an abstract.",
        "introduction": "Introduction content here.",
        "methodology": "Methodology details.",
        "results": "Results section.",
        "discussion": "Discussion section.",
        "conclusion": "Conclusion text.",
    }
    state.findings = [
        {"finding": "Issue in methodology", "priority": "high", "section": "methodology"},
        {"finding": "Citation error", "priority": "medium", "section": "introduction"},
        {"finding": "Logic flaw", "priority": "high", "section": "results"},
    ]
    state.loop_turns = 25
    state.total_tokens = 50000
    state.sections_read = ["introduction", "methodology", "results", "discussion", "conclusion"]
    state.section_metrics = [
        {
            "section_name": "introduction",
            "turns_spent": 5,
            "findings_produced": 1,
            "evidence_chains_built": 0,
            "hypotheses_generated": 1,
            "tokens_consumed": 10000,
        },
        {
            "section_name": "methodology",
            "turns_spent": 8,
            "findings_produced": 1,
            "evidence_chains_built": 1,
            "hypotheses_generated": 2,
            "tokens_consumed": 15000,
        },
        {
            "section_name": "results",
            "turns_spent": 7,
            "findings_produced": 1,
            "evidence_chains_built": 0,
            "hypotheses_generated": 1,
            "tokens_consumed": 12000,
        },
        {
            "section_name": "discussion",
            "turns_spent": 3,
            "findings_produced": 0,
            "evidence_chains_built": 0,
            "hypotheses_generated": 0,
            "tokens_consumed": 8000,
        },
        {
            "section_name": "conclusion",
            "turns_spent": 2,
            "findings_produced": 0,
            "evidence_chains_built": 0,
            "hypotheses_generated": 0,
            "tokens_consumed": 5000,
        },
    ]
    return state


class TestSectionExperienceRecording:
    """Tests for L0 section-level experience recording."""

    def test_record_section_experiences_basic(self, memory_store, state_with_sections):
        """Section experiences are recorded from section_metrics."""
        _record_section_experiences(
            state=state_with_sections, memory=memory_store, paper_type="empirical"
        )
        exps = memory_store.state.section_experiences
        assert len(exps) == 5
        assert exps[0]["section_name"] == "introduction"
        assert exps[0]["paper_type"] == "empirical"
        assert exps[0]["turns_spent"] == 5
        assert exps[0]["findings_produced"] == 1
        assert exps[0]["tokens_consumed"] == 10000
        assert exps[0]["findings_per_token"] == 1 / 10000

    def test_record_section_experiences_with_contrast_plan(self, memory_store, state_with_sections):
        """Phase A/B habit assignment works with contrast plan."""
        state_with_sections.contrast_plan = {
            "target_habit_id": "learned_003",
            "phase_a_sections": ["introduction", "methodology"],
            "phase_b_sections": ["results", "discussion", "conclusion"],
            "phase_a_habits": ["habit_1", "habit_2", "learned_003"],
            "phase_b_habits": ["habit_1", "habit_2"],
        }
        _record_section_experiences(
            state=state_with_sections, memory=memory_store, paper_type="empirical"
        )
        exps = memory_store.state.section_experiences
        # Phase A sections get all habits
        intro_exp = next(e for e in exps if e["section_name"] == "introduction")
        assert "learned_003" in intro_exp["active_habit_ids"]
        # Phase B sections get all minus target
        results_exp = next(e for e in exps if e["section_name"] == "results")
        assert "learned_003" not in results_exp["active_habit_ids"]
        assert "habit_1" in results_exp["active_habit_ids"]

    def test_record_section_experiences_empty_metrics(self, memory_store):
        """No-op when section_metrics is empty and no sections_read."""
        state = WorkspaceState()
        state.paper_sections = {"title": "Test"}
        _record_section_experiences(state=state, memory=memory_store, paper_type="")
        assert len(memory_store.state.section_experiences) == 0

    def test_record_section_experiences_derived_from_state(self, memory_store):
        """Section metrics are derived from sections_read + findings when section_metrics is empty."""
        state = WorkspaceState()
        state.paper_sections = {
            "title": "Test Paper",
            "introduction": "Intro content",
            "methods": "Methods content",
            "results": "Results content",
        }
        state.sections_read = ["introduction", "methods", "results"]
        state.findings = [
            {"finding": "Issue A", "priority": "high", "section": "introduction"},
            {"finding": "Issue B", "priority": "medium", "section": "methods"},
            {"finding": "Issue C", "priority": "high", "section": "methods"},
        ]
        state.total_tokens = 30000
        state.tool_call_history = [
            {"name": "read_section", "input": {"section": "introduction"}},
            {"name": "read_section", "input": {"section": "methods"}},
            {"name": "read_section", "input": {"section": "results"}},
        ]
        # section_metrics is explicitly empty
        state.section_metrics = []

        _record_section_experiences(state=state, memory=memory_store, paper_type="empirical")
        exps = memory_store.state.section_experiences
        assert len(exps) == 3
        intro_exp = next(e for e in exps if e["section_name"] == "introduction")
        assert intro_exp["findings_produced"] == 1
        methods_exp = next(e for e in exps if e["section_name"] == "methods")
        assert methods_exp["findings_produced"] == 2
        results_exp = next(e for e in exps if e["section_name"] == "results")
        assert results_exp["findings_produced"] == 0

    def test_section_experience_sliding_window(self, memory_store):
        """L0 window caps at MAX_SECTION_EXPERIENCES (500)."""
        for i in range(510):
            memory_store.persist_section_experience({
                "session_id": f"session_{i}",
                "section_name": f"section_{i}",
                "paper_type": "test",
                "turns_spent": 1,
                "findings_produced": 0,
                "evidence_chains_built": 0,
                "hypotheses_generated": 0,
                "active_habit_ids": [],
                "tokens_consumed": 100,
                "findings_per_token": 0.0,
            })
        assert len(memory_store.state.section_experiences) == 500
        # Most recent should be section_509
        assert memory_store.state.section_experiences[-1]["section_name"] == "section_509"

    def test_session_id_format(self, memory_store, state_with_sections):
        """Session ID uses date + paper_id[:8] format."""
        _record_section_experiences(
            state=state_with_sections, memory=memory_store, paper_type="empirical"
        )
        session_id = memory_store.state.section_experiences[0]["session_id"]
        # Format: YYYY-MM-DD_xxxxxxxx
        parts = session_id.split("_")
        assert len(parts) == 2
        assert len(parts[0]) == 10  # YYYY-MM-DD
        assert len(parts[1]) == 8   # paper_id[:8]


class TestSessionExperienceV3:
    """Tests for L1 session-level experience recording."""

    def test_record_session_experience_v3_basic(self, memory_store, state_with_sections):
        """V3 session experience records aggregated metrics."""
        _record_session_experience_v3(
            state=state_with_sections, memory=memory_store, paper_type="empirical"
        )
        exps = memory_store.state.session_experiences_v3
        assert len(exps) == 1
        exp = exps[0]
        assert exp["paper_type"] == "empirical"
        assert exp["findings_count"] == 3
        assert exp["total_tokens"] == 50000
        assert exp["loop_turns"] == 25
        assert exp["sections_processed"] == 5
        assert exp["has_contrast"] is False
        assert exp["contrast_target_habit"] is None
        # findings_per_1k_tokens = 3 / 50
        assert abs(exp["findings_per_1k_tokens"] - 0.06) < 0.001

    def test_record_session_experience_v3_with_contrast(self, memory_store, state_with_sections):
        """V3 session experience records contrast target when plan active."""
        state_with_sections.contrast_plan = {
            "target_habit_id": "learned_007",
            "phase_a_sections": ["intro"],
            "phase_b_sections": ["results"],
            "phase_a_habits": ["h1", "learned_007"],
            "phase_b_habits": ["h1"],
        }
        _record_session_experience_v3(
            state=state_with_sections, memory=memory_store, paper_type="review"
        )
        exp = memory_store.state.session_experiences_v3[0]
        assert exp["has_contrast"] is True
        assert exp["contrast_target_habit"] == "learned_007"

    def test_session_experience_v3_sliding_window(self, memory_store):
        """L1 window caps at MAX_SESSION_EXPERIENCES_V3 (100)."""
        for i in range(110):
            memory_store.persist_session_experience_v3({
                "session_id": f"session_{i}",
                "paper_type": "test",
                "findings_count": i,
                "total_tokens": 1000,
            })
        assert len(memory_store.state.session_experiences_v3) == 100
        # Most recent should be session_109
        assert memory_store.state.session_experiences_v3[-1]["session_id"] == "session_109"

    def test_pcg_coverage_recorded(self, memory_store, state_with_sections):
        """PCG coverage is extracted from paper_cognition_graph."""
        class FakePCG:
            def get_coverage(self):
                return 0.85
        state_with_sections.paper_cognition_graph = FakePCG()
        _record_session_experience_v3(
            state=state_with_sections, memory=memory_store, paper_type="empirical"
        )
        assert memory_store.state.session_experiences_v3[0]["pcg_coverage"] == 0.85


class TestIntraSessionContrast:
    """Tests for IntraSession Contrast Manager."""

    def test_plan_contrast_sufficient_sections(self):
        """Contrast plan is created when enough sections and suitable habit."""
        from core.evolution import LearnedHabit
        manager = IntraSessionContrastManager()
        sections = [f"section_{i}" for i in range(20)]
        habits = [
            LearnedHabit(id="h1", name="test1", phases=["read"], priority=50, content="do x", source_patterns=["p1"], confidence=0.55),
            LearnedHabit(id="h2", name="test2", phases=["read"], priority=50, content="do y", source_patterns=["p2"], confidence=0.9),
        ]
        plan = manager.plan_contrast(sections=sections, habits=habits)
        assert plan is not None
        assert plan["target_habit_id"] == "h1"  # closest to 0.55
        assert len(plan["phase_a_sections"]) == 10
        assert len(plan["phase_b_sections"]) == 10
        assert "h1" in plan["phase_a_habits"]
        assert "h1" not in plan["phase_b_habits"]

    def test_plan_contrast_insufficient_sections(self):
        """Returns None when sections < INTRA_CONTRAST_MIN_SECTIONS."""
        from core.evolution import LearnedHabit
        manager = IntraSessionContrastManager()
        sections = [f"s_{i}" for i in range(5)]  # too few
        habits = [LearnedHabit(id="h1", name="t", phases=[], priority=50, content="x", source_patterns=[], confidence=0.5)]
        with patch.dict(os.environ, {"SCHOLAR_GODEL_INTRA_CONTRAST_MIN_SECTIONS": "15"}):
            plan = manager.plan_contrast(sections=sections, habits=habits)
        assert plan is None

    def test_plan_contrast_no_suitable_habit(self):
        """Returns None when no habit in confidence range [0.4, 0.7]."""
        from core.evolution import LearnedHabit
        manager = IntraSessionContrastManager()
        sections = [f"s_{i}" for i in range(20)]
        habits = [
            LearnedHabit(id="h1", name="t", phases=[], priority=50, content="x", source_patterns=[], confidence=0.9),
            LearnedHabit(id="h2", name="t", phases=[], priority=50, content="x", source_patterns=[], confidence=0.1),
        ]
        plan = manager.plan_contrast(sections=sections, habits=habits)
        assert plan is None

    def test_analyze_contrast_positive_delta(self):
        """Positive delta means habit is effective → reinforce."""
        manager = IntraSessionContrastManager()
        plan = {
            "target_habit_id": "learned_003",
            "phase_a_sections": ["s1", "s2", "s3", "s4"],
            "phase_b_sections": ["s5", "s6", "s7", "s8"],
            "phase_a_habits": ["h1", "learned_003"],
            "phase_b_habits": ["h1"],
        }
        # Phase A (with habit) → high findings
        section_experiences = [
            {"section_name": "s1", "findings_produced": 3},
            {"section_name": "s2", "findings_produced": 2},
            {"section_name": "s3", "findings_produced": 3},
            {"section_name": "s4", "findings_produced": 2},
            # Phase B (without habit) → low findings
            {"section_name": "s5", "findings_produced": 1},
            {"section_name": "s6", "findings_produced": 0},
            {"section_name": "s7", "findings_produced": 1},
            {"section_name": "s8", "findings_produced": 0},
        ]
        result = manager.analyze_contrast(section_experiences, plan)
        assert result["recommendation"] == "reinforce"
        assert result["delta"] > 0

    def test_analyze_contrast_negative_delta(self):
        """Negative delta means habit is hurting → doubt."""
        manager = IntraSessionContrastManager()
        plan = {
            "target_habit_id": "learned_005",
            "phase_a_sections": ["a1", "a2", "a3"],
            "phase_b_sections": ["b1", "b2", "b3"],
            "phase_a_habits": ["h1", "learned_005"],
            "phase_b_habits": ["h1"],
        }
        section_experiences = [
            # Phase A low (habit hurts)
            {"section_name": "a1", "findings_produced": 0},
            {"section_name": "a2", "findings_produced": 1},
            {"section_name": "a3", "findings_produced": 0},
            # Phase B high
            {"section_name": "b1", "findings_produced": 3},
            {"section_name": "b2", "findings_produced": 2},
            {"section_name": "b3", "findings_produced": 3},
        ]
        result = manager.analyze_contrast(section_experiences, plan)
        assert result["recommendation"] == "doubt"
        assert result["delta"] < 0

    def test_analyze_contrast_insufficient_data(self):
        """Insufficient data when < 3 observations per phase."""
        manager = IntraSessionContrastManager()
        plan = {
            "target_habit_id": "h1",
            "phase_a_sections": ["a1", "a2"],
            "phase_b_sections": ["b1", "b2"],
            "phase_a_habits": ["h1"],
            "phase_b_habits": [],
        }
        section_experiences = [
            {"section_name": "a1", "findings_produced": 2},
            {"section_name": "a2", "findings_produced": 1},
            {"section_name": "b1", "findings_produced": 1},
            {"section_name": "b2", "findings_produced": 0},
        ]
        result = manager.analyze_contrast(section_experiences, plan)
        assert result["recommendation"] == "insufficient_data"

    def test_select_target_habit_closest_to_055(self):
        """Selects habit closest to 0.55 confidence."""
        from core.evolution import LearnedHabit
        manager = IntraSessionContrastManager()
        habits = [
            LearnedHabit(id="h1", name="t", phases=[], priority=50, content="x", source_patterns=[], confidence=0.4),
            LearnedHabit(id="h2", name="t", phases=[], priority=50, content="x", source_patterns=[], confidence=0.56),
            LearnedHabit(id="h3", name="t", phases=[], priority=50, content="x", source_patterns=[], confidence=0.7),
        ]
        selected = manager._select_target_habit(habits)
        assert selected.id == "h2"


class TestContrastAnalysisPersistence:
    """Tests for _analyze_and_persist_contrast integration."""

    def test_analyze_and_persist_no_plan(self, memory_store):
        """No-op when contrast_plan is None."""
        state = WorkspaceState()
        state.contrast_plan = None
        _analyze_and_persist_contrast(state=state, memory=memory_store)
        assert len(memory_store.state.contrast_results) == 0

    def test_analyze_and_persist_no_metrics(self, memory_store):
        """No-op when section_metrics is empty."""
        state = WorkspaceState()
        state.contrast_plan = {
            "target_habit_id": "h1",
            "phase_a_sections": ["a1"],
            "phase_b_sections": ["b1"],
            "phase_a_habits": ["h1"],
            "phase_b_habits": [],
        }
        state.section_metrics = []
        _analyze_and_persist_contrast(state=state, memory=memory_store)
        assert len(memory_store.state.contrast_results) == 0

    def test_analyze_and_persist_full_flow(self, memory_store):
        """Full flow: plan exists + metrics → result persisted."""
        state = WorkspaceState()
        state.contrast_plan = {
            "target_habit_id": "learned_003",
            "phase_a_sections": ["s1", "s2", "s3", "s4"],
            "phase_b_sections": ["s5", "s6", "s7", "s8"],
            "phase_a_habits": ["h1", "learned_003"],
            "phase_b_habits": ["h1"],
        }
        state.section_metrics = [
            {"section_name": "s1", "findings_produced": 2},
            {"section_name": "s2", "findings_produced": 3},
            {"section_name": "s3", "findings_produced": 2},
            {"section_name": "s4", "findings_produced": 2},
            {"section_name": "s5", "findings_produced": 1},
            {"section_name": "s6", "findings_produced": 0},
            {"section_name": "s7", "findings_produced": 1},
            {"section_name": "s8", "findings_produced": 0},
        ]
        _analyze_and_persist_contrast(state=state, memory=memory_store)
        assert len(memory_store.state.contrast_results) == 1
        result = memory_store.state.contrast_results[0]
        assert result["target_habit_id"] == "learned_003"
        assert result["recommendation"] == "reinforce"


class TestKillSwitchGating:
    """Tests that V3 features are properly gated by kill switches."""

    def test_section_experience_disabled(self, memory_store, state_with_sections):
        """No section experiences recorded when kill switch is off."""
        state_with_sections.tool_call_history = []
        with patch.dict(os.environ, {
            "SCHOLAR_GODEL_SECTION_EXP": "0",
            "SCHOLAR_GODEL_INTRA_CONTRAST": "0",
        }):
            # Need to reimport after env change
            import importlib
            import core.godel_config
            importlib.reload(core.godel_config)
            try:
                end_session(
                    state=state_with_sections,
                    memory=memory_store,
                    paper_id="test_paper_12345678",
                    strategy_transitions=[],
                )
            finally:
                # Restore defaults
                with patch.dict(os.environ, {
                    "SCHOLAR_GODEL_SECTION_EXP": "1",
                    "SCHOLAR_GODEL_INTRA_CONTRAST": "1",
                }):
                    importlib.reload(core.godel_config)

        assert len(memory_store.state.section_experiences) == 0
        assert len(memory_store.state.session_experiences_v3) == 0

    def test_contrast_disabled_no_analysis(self, memory_store, state_with_sections):
        """No contrast analysis when GODEL_INTRA_CONTRAST_ENABLED is off."""
        state_with_sections.contrast_plan = {
            "target_habit_id": "h1",
            "phase_a_sections": ["introduction", "methodology"],
            "phase_b_sections": ["results", "discussion", "conclusion"],
            "phase_a_habits": ["h1"],
            "phase_b_habits": [],
        }
        with patch.dict(os.environ, {"SCHOLAR_GODEL_INTRA_CONTRAST": "0"}):
            import importlib
            import core.godel_config
            importlib.reload(core.godel_config)
            try:
                end_session(
                    state=state_with_sections,
                    memory=memory_store,
                    paper_id="test_paper_12345678",
                    strategy_transitions=[],
                )
            finally:
                with patch.dict(os.environ, {"SCHOLAR_GODEL_INTRA_CONTRAST": "1"}):
                    importlib.reload(core.godel_config)

        assert len(memory_store.state.contrast_results) == 0


class TestMemoryPersistenceV3Fields:
    """Tests for V3 field serialization/deserialization."""

    def test_save_and_load_v3_fields(self, memory_store):
        """V3 fields survive save/load cycle."""
        memory_store.persist_section_experience({
            "session_id": "2025-01-15_abcd1234",
            "section_name": "intro",
            "paper_type": "empirical",
            "turns_spent": 5,
            "findings_produced": 2,
            "evidence_chains_built": 1,
            "hypotheses_generated": 1,
            "active_habit_ids": ["h1", "h2"],
            "tokens_consumed": 10000,
            "findings_per_token": 0.0002,
        })
        memory_store.persist_session_experience_v3({
            "session_id": "2025-01-15_abcd1234",
            "paper_type": "empirical",
            "findings_count": 5,
            "total_tokens": 50000,
        })
        memory_store.persist_contrast_result({
            "target_habit_id": "learned_003",
            "recommendation": "reinforce",
            "delta": 0.25,
        })
        memory_store.persist_evolution_record({
            "event": "habit_promoted",
            "habit_id": "learned_003",
        })
        memory_store.save()

        # Load into new store
        store2 = MemoryStore(memory_store.base_dir)
        store2.load()
        assert len(store2.state.section_experiences) == 1
        assert store2.state.section_experiences[0]["section_name"] == "intro"
        assert len(store2.state.session_experiences_v3) == 1
        assert store2.state.session_experiences_v3[0]["findings_count"] == 5
        assert len(store2.state.contrast_results) == 1
        assert store2.state.contrast_results[0]["recommendation"] == "reinforce"
        assert len(store2.state.evolution_records) == 1

    def test_backward_compat_load_without_v3_fields(self, tmp_path):
        """Loading a pre-V3 memory file doesn't crash."""
        import json
        # Write a minimal V1-style memory file
        mem_file = tmp_path / "memory.json"
        mem_file.write_text(json.dumps({
            "version": "1.1",
            "sessions": [],
            "patterns": [],
            "procedures": [],
        }))
        store = MemoryStore(str(tmp_path))
        store.load()
        # V3 fields should have defaults
        assert store.state.section_experiences == []
        assert store.state.session_experiences_v3 == []
        assert store.state.contrast_results == []
        assert store.state.evolution_records == []


class TestHistoricalBaseline:
    """Tests for get_historical_baseline()."""

    def test_baseline_from_session_experiences(self, memory_store):
        """Computes per-paper_type average findings per 1k tokens."""
        memory_store.persist_session_experience_v3({
            "paper_type": "empirical",
            "findings_count": 10,
            "total_tokens": 50000,
        })
        memory_store.persist_session_experience_v3({
            "paper_type": "empirical",
            "findings_count": 8,
            "total_tokens": 40000,
        })
        memory_store.persist_session_experience_v3({
            "paper_type": "review",
            "findings_count": 5,
            "total_tokens": 30000,
        })
        baseline = memory_store.get_historical_baseline()
        # empirical: (10+8) / ((50000+40000)/1000) = 18/90 = 0.2
        assert abs(baseline["empirical"] - 0.2) < 0.001
        # review: 5 / (30000/1000) = 5/30 = 0.1667
        assert abs(baseline["review"] - 0.1667) < 0.001

    def test_baseline_empty(self, memory_store):
        """Empty baseline returns empty dict."""
        assert memory_store.get_historical_baseline() == {}


class TestExperienceQueryMethods:
    """Tests for get_section_experiences_for_habit()."""

    def test_split_by_habit(self, memory_store):
        """Correctly splits experiences by whether habit was active."""
        memory_store.persist_section_experience({
            "session_id": "s1", "section_name": "a",
            "active_habit_ids": ["h1", "h2"],
            "findings_produced": 2,
        })
        memory_store.persist_section_experience({
            "session_id": "s1", "section_name": "b",
            "active_habit_ids": ["h1"],
            "findings_produced": 1,
        })
        memory_store.persist_section_experience({
            "session_id": "s1", "section_name": "c",
            "active_habit_ids": ["h2"],
            "findings_produced": 3,
        })
        with_h2, without_h2 = memory_store.get_section_experiences_for_habit("h2")
        assert len(with_h2) == 2  # a, c
        assert len(without_h2) == 1  # b

    def test_split_by_nonexistent_habit(self, memory_store):
        """Non-existent habit returns all in without."""
        memory_store.persist_section_experience({
            "session_id": "s1", "section_name": "x",
            "active_habit_ids": ["h1"],
            "findings_produced": 1,
        })
        with_h, without_h = memory_store.get_section_experiences_for_habit("nonexistent")
        assert len(with_h) == 0
        assert len(without_h) == 1
