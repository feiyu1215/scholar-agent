"""
tests/test_v3_phase2_meta_reflect.py — V3 Phase 2: Tri-Frequency MetaReflector Tests

Covers:
- FastReflector: trigger conditions, decline detection, alert generation, apply logic
- EmergencyReflector: trigger conditions, confidence reduction, suspect habit identification
- DeepReflector: trigger conditions, context generation, LLM response parsing, decision application
- Kill switches: all three reflectors disabled via env vars
- Graceful degradation: failures don't crash session
- Integration with session_finalizer.end_session_with_reflection()
- Serialization/deserialization of Phase 2 state fields
"""

import asyncio
import json
import os
import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

from core.meta_reflect import FastReflector, EmergencyReflector, DeepReflector
from core.memory import MemoryStore, MemoryState
from core.state import WorkspaceState
from core.evolution import LearnedHabit


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def memory_store(tmp_path):
    """Fresh MemoryStore instance for testing."""
    store = MemoryStore(base_dir=str(tmp_path))
    store.load()
    return store


@pytest.fixture
def state_with_findings():
    """WorkspaceState with some findings and tool calls."""
    state = WorkspaceState()
    state.paper_sections = {"abstract": "Test paper about ML methods..."}
    state.findings = [
        {"finding": "Issue 1", "priority": "high", "section": "methods", "status": "verified"},
        {"finding": "Issue 2", "priority": "medium", "section": "results", "status": "verified"},
    ]
    state.tool_call_history = [
        {"name": "read_section", "input": {"section": "abstract"}},
        {"name": "read_section", "input": {"section": "methods"}},
        {"name": "update_findings", "input": {}},
    ]
    state.loop_turns = 10
    state.total_tokens = 50000
    state.sections_read = ["abstract", "methods", "results"]
    return state


@pytest.fixture
def state_emergency_idle():
    """WorkspaceState with high idle + low findings (emergency trigger)."""
    state = WorkspaceState()
    state.paper_sections = {"abstract": "Test paper"}
    state.findings = [{"finding": "Only one", "priority": "low", "section": "intro"}]
    # Simulate 15 idle tool calls after last finding
    state.tool_call_history = [
        {"name": "update_findings", "input": {}},
    ] + [
        {"name": "read_section", "input": {"section": f"sec_{i}"}}
        for i in range(15)
    ]
    state.loop_turns = 20
    state.total_tokens = 50000
    return state


@pytest.fixture
def state_emergency_tokens():
    """WorkspaceState with high tokens + low findings (emergency trigger)."""
    state = WorkspaceState()
    state.paper_sections = {"abstract": "Test paper"}
    state.findings = [
        {"finding": "Issue 1", "priority": "low", "section": "intro"},
        {"finding": "Issue 2", "priority": "low", "section": "results"},
    ]
    state.tool_call_history = [
        {"name": "read_section", "input": {"section": "abstract"}},
    ]
    state.loop_turns = 30
    state.total_tokens = 90000  # > 80K threshold
    return state


@pytest.fixture
def learned_habits_fixture():
    """Sample learned habits for testing."""
    return [
        LearnedHabit(
            id="habit_skepticism",
            name="质疑优先",
            phases=["DEEP_REVIEW"],
            priority=70,
            content="先质疑再验证",
            source_patterns=["p1", "p2"],
            confidence=0.8,
            generation=1,
        ),
        LearnedHabit(
            id="habit_evidence",
            name="证据链",
            phases=["DEEP_REVIEW", "SYNTHESIS"],
            priority=65,
            content="确保每个 finding 有完整证据链",
            source_patterns=["p3"],
            confidence=0.7,
            generation=1,
        ),
    ]


def _make_session_exp(findings_count=5, total_tokens=30000, pcg_coverage=0.6,
                      findings_per_1k_tokens=None, paper_type="empirical"):
    """Helper to create session experience dicts."""
    if findings_per_1k_tokens is None:
        findings_per_1k_tokens = findings_count / max(total_tokens / 1000, 0.1)
    return {
        "session_id": "test_session",
        "paper_type": paper_type,
        "paper_id": "abc123",
        "findings_count": findings_count,
        "total_tokens": total_tokens,
        "loop_turns": 15,
        "findings_per_1k_tokens": findings_per_1k_tokens,
        "pcg_coverage": pcg_coverage,
        "has_contrast": False,
        "contrast_target_habit": None,
        "sections_processed": 5,
    }


# ============================================================
# FastReflector Tests
# ============================================================

class TestFastReflector:
    """FastReflector trigger, analysis, and application tests."""

    def test_should_not_trigger_below_interval(self, memory_store):
        """Should not trigger with fewer than 3 sessions since last check."""
        fast = FastReflector()
        # Add only 2 sessions
        memory_store.state.session_experiences_v3 = [
            _make_session_exp(), _make_session_exp()
        ]
        assert fast.should_trigger(memory_store) is False

    def test_should_trigger_at_interval(self, memory_store):
        """Should trigger at exactly 3 sessions since last check (past cold start)."""
        fast = FastReflector()
        # Need >= COLD_START_SESSION_THRESHOLD (10) sessions to pass cold-start guard
        memory_store.state.session_experiences_v3 = [_make_session_exp()] * 13
        memory_store.state._last_fast_reflect_count = 10
        assert fast.should_trigger(memory_store) is True

    def test_should_trigger_respects_last_count(self, memory_store):
        """Should use _last_fast_reflect_count as offset (past cold start)."""
        fast = FastReflector()
        # 12 sessions total (past cold start threshold of 10)
        memory_store.state.session_experiences_v3 = [_make_session_exp()] * 12
        memory_store.state._last_fast_reflect_count = 10
        assert fast.should_trigger(memory_store) is False  # 12-10=2 < 3

        memory_store.state._last_fast_reflect_count = 9
        assert fast.should_trigger(memory_store) is True  # 12-9=3 >= 3

    def test_analyze_detects_declining_density(self, memory_store):
        """Should detect 3 consecutive declining findings_per_1k_tokens."""
        fast = FastReflector()
        memory_store.state.session_experiences_v3 = [
            _make_session_exp(findings_per_1k_tokens=0.5),
            _make_session_exp(findings_per_1k_tokens=0.3),
            _make_session_exp(findings_per_1k_tokens=0.1),
        ]
        alerts = fast.analyze(memory_store)
        assert len(alerts) >= 1
        assert "findings_density" in alerts[0]

    def test_analyze_detects_declining_findings_count(self, memory_store):
        """Should detect declining findings_count with low final value."""
        fast = FastReflector()
        memory_store.state.session_experiences_v3 = [
            _make_session_exp(findings_count=5, findings_per_1k_tokens=0.5),
            _make_session_exp(findings_count=3, findings_per_1k_tokens=0.5),
            _make_session_exp(findings_count=1, findings_per_1k_tokens=0.5),
        ]
        alerts = fast.analyze(memory_store)
        assert any("findings_count" in a for a in alerts)

    def test_analyze_detects_pcg_stagnation(self, memory_store):
        """Should detect PCG coverage stagnation."""
        fast = FastReflector()
        memory_store.state.session_experiences_v3 = [
            _make_session_exp(pcg_coverage=0.45, findings_per_1k_tokens=0.5),
            _make_session_exp(pcg_coverage=0.46, findings_per_1k_tokens=0.5),
            _make_session_exp(pcg_coverage=0.47, findings_per_1k_tokens=0.5),
        ]
        alerts = fast.analyze(memory_store)
        assert any("pcg_coverage" in a for a in alerts)

    def test_analyze_no_alerts_when_improving(self, memory_store):
        """Should return no alerts when metrics are improving."""
        fast = FastReflector()
        memory_store.state.session_experiences_v3 = [
            _make_session_exp(findings_count=3, findings_per_1k_tokens=0.1, pcg_coverage=0.3),
            _make_session_exp(findings_count=5, findings_per_1k_tokens=0.3, pcg_coverage=0.5),
            _make_session_exp(findings_count=7, findings_per_1k_tokens=0.5, pcg_coverage=0.7),
        ]
        alerts = fast.analyze(memory_store)
        assert len(alerts) == 0

    def test_analyze_max_3_alerts(self, memory_store):
        """Should return at most 3 alerts."""
        fast = FastReflector()
        # All declining (density + count + stagnation)
        memory_store.state.session_experiences_v3 = [
            _make_session_exp(findings_count=5, findings_per_1k_tokens=0.5, pcg_coverage=0.45),
            _make_session_exp(findings_count=3, findings_per_1k_tokens=0.3, pcg_coverage=0.46),
            _make_session_exp(findings_count=1, findings_per_1k_tokens=0.1, pcg_coverage=0.47),
        ]
        alerts = fast.analyze(memory_store)
        assert len(alerts) <= 3

    def test_apply_stores_alerts(self, memory_store):
        """Should store alerts in memory state."""
        fast = FastReflector()
        memory_store.state.session_experiences_v3 = [_make_session_exp()] * 3
        alerts = ["alert1", "alert2"]
        fast.apply(alerts, memory_store)
        assert memory_store.state.fast_reflect_alerts == ["alert1", "alert2"]

    def test_apply_updates_counter(self, memory_store):
        """Should update _last_fast_reflect_count."""
        fast = FastReflector()
        memory_store.state.session_experiences_v3 = [_make_session_exp()] * 5
        fast.apply(["alert"], memory_store)
        assert memory_store.state._last_fast_reflect_count == 5

    def test_apply_clears_alerts_when_empty(self, memory_store):
        """Should clear alerts when analysis finds no issues."""
        fast = FastReflector()
        memory_store.state.session_experiences_v3 = [_make_session_exp()] * 3
        memory_store.state.fast_reflect_alerts = ["old_alert"]
        fast.apply([], memory_store)
        assert memory_store.state.fast_reflect_alerts == []

    def test_is_declining_helper(self):
        """Test the _is_declining static method."""
        assert FastReflector._is_declining([5, 3, 1]) is True
        assert FastReflector._is_declining([1, 3, 5]) is False
        assert FastReflector._is_declining([3, 3, 3]) is False
        assert FastReflector._is_declining([5, 3]) is False  # needs 3+


# ============================================================
# EmergencyReflector Tests
# ============================================================

class TestEmergencyReflector:
    """EmergencyReflector trigger conditions and confidence reduction tests."""

    def test_no_emergency_normal_session(self, state_with_findings):
        """Should not trigger for normal sessions."""
        emergency = EmergencyReflector()
        result = emergency.check(state_with_findings)
        assert result is None

    def test_emergency_idle_low_findings(self, state_emergency_idle):
        """Should trigger on idle > 10 AND findings < 2."""
        emergency = EmergencyReflector()
        result = emergency.check(state_emergency_idle)
        assert result is not None
        assert "idle_before_exit" in result["reason"]
        assert result["trigger_type"] == "idle_low_findings"

    def test_emergency_high_tokens_low_findings(self, state_emergency_tokens):
        """Should trigger on tokens > 80K AND findings < 3."""
        emergency = EmergencyReflector()
        result = emergency.check(state_emergency_tokens)
        assert result is not None
        assert "total_tokens" in result["reason"]
        assert result["trigger_type"] == "high_tokens_low_findings"

    def test_no_emergency_enough_findings(self):
        """Should not trigger if findings count is sufficient."""
        state = WorkspaceState()
        state.paper_sections = {"abstract": "Test"}
        state.findings = [
            {"finding": f"Issue {i}", "priority": "high", "section": "methods"}
            for i in range(5)
        ]
        state.tool_call_history = [
            {"name": "read_section", "input": {"section": "abstract"}}
        ] * 20
        state.total_tokens = 100000
        state.loop_turns = 25

        emergency = EmergencyReflector()
        result = emergency.check(state)
        assert result is None

    def test_apply_emergency_reduces_confidence(self, memory_store, learned_habits_fixture):
        """Should reduce confidence of suspect habit by 0.1."""
        emergency = EmergencyReflector()
        result = {"suspect_habits": ["habit_skepticism"], "reason": "test"}

        old_confidence = learned_habits_fixture[0].confidence
        emergency.apply_emergency(result, memory_store, learned_habits_fixture)
        assert learned_habits_fixture[0].confidence == pytest.approx(old_confidence - 0.1)

    def test_apply_emergency_max_one_habit(self, memory_store, learned_habits_fixture):
        """Should reduce confidence of at most 1 habit per trigger."""
        emergency = EmergencyReflector()
        result = {"suspect_habits": ["habit_skepticism", "habit_evidence"], "reason": "test"}

        old_conf_1 = learned_habits_fixture[0].confidence
        old_conf_2 = learned_habits_fixture[1].confidence
        emergency.apply_emergency(result, memory_store, learned_habits_fixture)
        # Only first one should be reduced
        assert learned_habits_fixture[0].confidence == pytest.approx(old_conf_1 - 0.1)
        assert learned_habits_fixture[1].confidence == old_conf_2

    def test_apply_emergency_confidence_floor_zero(self, memory_store):
        """Confidence should not go below 0."""
        emergency = EmergencyReflector()
        habits = [
            LearnedHabit(
                id="habit_weak", name="弱习惯", phases=["DEEP_REVIEW"],
                priority=50, content="test", source_patterns=["p1"],
                confidence=0.05, generation=1,
            ),
        ]
        result = {"suspect_habits": ["habit_weak"], "reason": "test"}
        emergency.apply_emergency(result, memory_store, habits)
        assert habits[0].confidence == 0.0

    def test_suspect_habits_from_contrast_plan(self):
        """Should identify target habit from contrast plan."""
        state = WorkspaceState()
        state.contrast_plan = {"target_habit_id": "habit_xyz"}
        suspects = EmergencyReflector._identify_suspect_habits(state)
        assert suspects == ["habit_xyz"]

    def test_suspect_habits_empty_without_contrast(self):
        """Should return empty list without contrast plan."""
        state = WorkspaceState()
        state.contrast_plan = None
        suspects = EmergencyReflector._identify_suspect_habits(state)
        assert suspects == []


# ============================================================
# DeepReflector Tests
# ============================================================

# NOTE: TestDeepReflector and TestKillSwitches are defined below with
# self-contained helper methods (no fixture dependency for portability).


# ============================================================
# Graceful Degradation Tests
# ============================================================

class TestGracefulDegradation:
    """All reflectors should fail gracefully without crashing."""

    def test_fast_reflector_handles_empty_state(self, memory_store):
        """FastReflector should handle empty memory gracefully."""
        fast = FastReflector()
        assert fast.should_trigger(memory_store) is False
        assert fast.analyze(memory_store) == []

    def test_fast_reflector_handles_corrupt_data(self, memory_store):
        """FastReflector should handle corrupt session data."""
        fast = FastReflector()
        memory_store.state.session_experiences_v3 = [
            {"corrupt": True},
            {"corrupt": True},
            {"corrupt": True},
        ]
        # Should not raise, returns empty alerts
        alerts = fast.analyze(memory_store)
        assert isinstance(alerts, list)

    def test_emergency_handles_minimal_state(self):
        """EmergencyReflector should handle minimal state gracefully."""
        emergency = EmergencyReflector()
        state = WorkspaceState()
        state.findings = []
        state.tool_call_history = []
        state.total_tokens = 100000
        # Should not raise even with minimal state
        result = emergency.check(state)
        # Result depends on actual gate_config computation, but should not crash
        assert result is None or isinstance(result, dict)


# ============================================================
# DeepReflector Tests
# ============================================================

class TestDeepReflector:
    """Tests for DeepReflector (LLM-based, every 10 sessions)."""

    def _make_memory_store(self, session_count=0, alerts=None):
        store = MemoryStore(base_dir="/tmp/test_deep")
        store.state = MemoryState()
        for i in range(session_count):
            store.state.session_experiences_v3.append({
                "session_id": f"2024-01-{i+1:02d}_abc",
                "findings_count": 5 - (i % 3),
                "total_tokens": 50000,
                "findings_per_1k_tokens": (5 - (i % 3)) / 50.0,
                "pcg_coverage": 0.5 + i * 0.02,
                "paper_type": "econometrics",
            })
        if alerts:
            store.state.fast_reflect_alerts = alerts
        return store

    def test_should_trigger_v3_interval(self):
        """DeepReflector triggers after 10 sessions."""
        deep = DeepReflector()
        memory = self._make_memory_store(session_count=10)
        assert deep.should_trigger_v3(memory) is True

    def test_should_trigger_v3_not_enough_sessions(self):
        """DeepReflector does not trigger during cold start (< 10 sessions)."""
        deep = DeepReflector()
        memory = self._make_memory_store(session_count=9)
        assert deep.should_trigger_v3(memory) is False

    def test_should_trigger_v3_anomaly_alerts(self):
        """DeepReflector triggers on 2+ fast_reflect_alerts (past cold start)."""
        deep = DeepReflector()
        memory = self._make_memory_store(
            session_count=11,  # past cold start threshold
            alerts=["alert1", "alert2"],
        )
        memory.state._last_deep_reflect_count = 9  # not interval-triggered
        assert deep.should_trigger_v3(memory) is True

    def test_should_trigger_v3_single_alert_not_enough(self):
        """Single alert does not trigger DeepReflector (past cold start)."""
        deep = DeepReflector()
        memory = self._make_memory_store(
            session_count=11,  # past cold start threshold
            alerts=["alert1"],
        )
        memory.state._last_deep_reflect_count = 9  # not interval-triggered
        assert deep.should_trigger_v3(memory) is False

    def test_precompute_context_v3_format(self):
        """precompute_context_v3 returns a non-empty string."""
        deep = DeepReflector()
        memory = self._make_memory_store(session_count=5)
        memory.state.contrast_results = [
            {"target_habit_id": "h1", "recommendation": "boost", "findings_delta": 0.5}
        ]
        context = deep.precompute_context_v3(memory, [])
        assert isinstance(context, str)
        assert "Session 1" in context
        assert "contrast" in context.lower() or "Contrast" in context

    def test_precompute_context_v3_with_habits(self):
        """precompute_context_v3 includes habit info."""
        deep = DeepReflector()
        memory = self._make_memory_store(session_count=3)
        habits = [self._make_learned_habit("h1", 0.7)]
        context = deep.precompute_context_v3(memory, habits)
        assert "h1" in context
        assert "0.70" in context

    @pytest.mark.asyncio
    async def test_reflect_returns_none_without_llm(self):
        """reflect() returns None when no llm_call_fn."""
        deep = DeepReflector(llm_call_fn=None)
        result = await deep.reflect("some context")
        assert result is None

    @pytest.mark.asyncio
    async def test_reflect_parses_valid_response(self):
        """reflect() parses valid JSON from LLM."""
        response_json = json.dumps({
            "habit_decisions": [
                {"habit_id": "h1", "action": "boost", "confidence_delta": 0.1, "reasoning": "good"}
            ],
            "maturity_updates": [],
            "meta_note": "all good",
            "token_efficiency_assessment": "stable",
        })

        async def mock_llm(system, user, max_tokens):
            return response_json

        deep = DeepReflector(llm_call_fn=mock_llm)
        result = await deep.reflect("context")
        assert result is not None
        assert len(result["habit_decisions"]) == 1
        assert result["habit_decisions"][0]["action"] == "boost"
        assert result["meta_note"] == "all good"

    @pytest.mark.asyncio
    async def test_reflect_handles_invalid_json(self):
        """reflect() returns None on invalid JSON."""
        async def mock_llm(system, user, max_tokens):
            return "not valid json at all"

        deep = DeepReflector(llm_call_fn=mock_llm)
        result = await deep.reflect("context")
        assert result is None

    @pytest.mark.asyncio
    async def test_reflect_handles_markdown_wrapped_json(self):
        """reflect() handles markdown-wrapped JSON."""
        response_json = json.dumps({
            "habit_decisions": [],
            "maturity_updates": [],
            "meta_note": "维持现状",
            "token_efficiency_assessment": "stable",
        })

        async def mock_llm(system, user, max_tokens):
            return f"```json\n{response_json}\n```"

        deep = DeepReflector(llm_call_fn=mock_llm)
        result = await deep.reflect("context")
        assert result is not None
        assert result["meta_note"] == "维持现状"

    def test_apply_decisions_v3_boosts_confidence(self):
        """apply_decisions_v3 boosts habit confidence."""
        deep = DeepReflector()
        memory = self._make_memory_store(session_count=10)
        habits = [self._make_learned_habit("h1", 0.6)]

        result = {
            "habit_decisions": [
                {"habit_id": "h1", "action": "boost", "confidence_delta": 0.15}
            ],
            "maturity_updates": [],
            "meta_note": "boosted",
            "token_efficiency_assessment": "improving",
        }

        report = deep.apply_decisions_v3(result, memory, habits)
        assert report["habits_adjusted"] == 1
        assert habits[0].confidence == pytest.approx(0.75, abs=0.01)

    def test_apply_decisions_v3_reduces_confidence(self):
        """apply_decisions_v3 reduces habit confidence."""
        deep = DeepReflector()
        memory = self._make_memory_store(session_count=10)
        habits = [self._make_learned_habit("h1", 0.8)]

        result = {
            "habit_decisions": [
                {"habit_id": "h1", "action": "reduce", "confidence_delta": 0.1}
            ],
            "maturity_updates": [],
            "meta_note": "reduced",
            "token_efficiency_assessment": "declining",
        }

        report = deep.apply_decisions_v3(result, memory, habits)
        assert report["habits_adjusted"] == 1
        assert habits[0].confidence == pytest.approx(0.7, abs=0.01)

    def test_apply_decisions_v3_persists_evolution_record(self):
        """apply_decisions_v3 persists L2 evolution record."""
        deep = DeepReflector()
        memory = self._make_memory_store(session_count=10)

        result = {
            "habit_decisions": [],
            "maturity_updates": [{"paper_type": "econ", "new_maturity": 0.7, "reasoning": "x"}],
            "meta_note": "test note",
            "token_efficiency_assessment": "stable",
        }

        deep.apply_decisions_v3(result, memory, [])
        assert len(memory.state.evolution_records) == 1
        record = memory.state.evolution_records[0]
        assert record["trigger_type"] == "deep"
        assert record["meta_note"] == "test note"

    def test_apply_decisions_v3_clears_fast_alerts(self):
        """apply_decisions_v3 clears fast_reflect_alerts after processing."""
        deep = DeepReflector()
        memory = self._make_memory_store(session_count=10, alerts=["a1", "a2"])

        result = {
            "habit_decisions": [],
            "maturity_updates": [],
            "meta_note": "",
            "token_efficiency_assessment": "stable",
        }

        deep.apply_decisions_v3(result, memory, [])
        assert memory.state.fast_reflect_alerts == []

    def test_apply_decisions_v3_updates_counter(self):
        """apply_decisions_v3 updates _last_deep_reflect_count."""
        deep = DeepReflector()
        memory = self._make_memory_store(session_count=12)

        result = {
            "habit_decisions": [],
            "maturity_updates": [],
            "meta_note": "",
            "token_efficiency_assessment": "stable",
        }

        deep.apply_decisions_v3(result, memory, [])
        assert memory.state._last_deep_reflect_count == 12

    def test_confidence_bounded_at_1(self):
        """Boosting cannot exceed 1.0."""
        deep = DeepReflector()
        memory = self._make_memory_store(session_count=10)
        habits = [self._make_learned_habit("h1", 0.95)]

        result = {
            "habit_decisions": [
                {"habit_id": "h1", "action": "boost", "confidence_delta": 0.2}
            ],
            "maturity_updates": [],
            "meta_note": "",
            "token_efficiency_assessment": "stable",
        }

        deep.apply_decisions_v3(result, memory, habits)
        assert habits[0].confidence == 1.0

    def test_confidence_bounded_at_0(self):
        """Reducing cannot go below 0.0."""
        deep = DeepReflector()
        memory = self._make_memory_store(session_count=10)
        habits = [self._make_learned_habit("h1", 0.05)]

        result = {
            "habit_decisions": [
                {"habit_id": "h1", "action": "retire", "confidence_delta": 0.2}
            ],
            "maturity_updates": [],
            "meta_note": "",
            "token_efficiency_assessment": "stable",
        }

        deep.apply_decisions_v3(result, memory, habits)
        assert habits[0].confidence == 0.0

    @staticmethod
    def _make_learned_habit(habit_id: str, confidence: float):
        return LearnedHabit(
            id=habit_id,
            name=f"Test habit {habit_id}",
            phases=["DEEP_REVIEW"],
            priority=60,
            content=f"Content for {habit_id}",
            source_patterns=["p1"],
            confidence=confidence,
        )


# ============================================================
# Kill Switch Tests
# ============================================================

class TestKillSwitches:
    """Test that kill switches properly disable reflectors."""

    def test_fast_reflect_kill_switch(self, monkeypatch):
        """SCHOLAR_GODEL_FAST_REFLECT=0 disables FastReflector."""
        monkeypatch.setenv("SCHOLAR_GODEL_FAST_REFLECT", "0")
        # Re-import to pick up env change
        import importlib
        import core.godel_config as gc
        importlib.reload(gc)
        assert gc.GODEL_FAST_REFLECT_ENABLED is False
        # Restore
        monkeypatch.setenv("SCHOLAR_GODEL_FAST_REFLECT", "1")
        importlib.reload(gc)

    def test_emergency_kill_switch(self, monkeypatch):
        """SCHOLAR_GODEL_EMERGENCY=0 disables EmergencyReflector."""
        monkeypatch.setenv("SCHOLAR_GODEL_EMERGENCY", "0")
        import importlib
        import core.godel_config as gc
        importlib.reload(gc)
        assert gc.GODEL_EMERGENCY_REFLECT_ENABLED is False
        monkeypatch.setenv("SCHOLAR_GODEL_EMERGENCY", "1")
        importlib.reload(gc)

    def test_deep_reflect_kill_switch(self, monkeypatch):
        """SCHOLAR_GODEL_DEEP_REFLECT=0 disables DeepReflector."""
        monkeypatch.setenv("SCHOLAR_GODEL_DEEP_REFLECT", "0")
        import importlib
        import core.godel_config as gc
        importlib.reload(gc)
        assert gc.GODEL_DEEP_REFLECT_ENABLED is False
        monkeypatch.setenv("SCHOLAR_GODEL_DEEP_REFLECT", "1")
        importlib.reload(gc)


# ============================================================
# Integration Tests
# ============================================================

class TestTriFrequencyIntegration:
    """Integration tests for the full tri-frequency flow."""

    @pytest.mark.asyncio
    async def test_end_session_with_reflection_includes_v3_stats(self):
        """end_session_with_reflection returns V3 stats."""
        from core.session_finalizer import end_session_with_reflection
        from core.state import WorkspaceState

        state = WorkspaceState()
        state.findings = [{"finding": "test", "priority": "high", "status": "verified"}]
        state.paper_sections = {"title": "Test Paper", "abstract": "Abstract text here"}
        state.tool_call_history = [{"name": "read_section", "input": {"section": "methods"}}]
        state.sections_read = ["methods"]

        memory = MemoryStore(base_dir="/tmp/test_integration")
        memory.state = MemoryState()

        stats = await end_session_with_reflection(
            state=state,
            memory=memory,
            paper_id="test_paper_id",
            strategy_transitions=[],
            llm_call_fn=None,  # No LLM
        )

        assert "reflections_count" in stats
        assert stats["reflections_count"] == 0  # No LLM

    @pytest.mark.asyncio
    async def test_graceful_degradation_all_reflectors(self):
        """All reflectors gracefully handle errors without crashing session."""
        from core.session_finalizer import end_session_with_reflection
        from core.state import WorkspaceState

        state = WorkspaceState()
        state.findings = [{"finding": "test", "priority": "medium", "status": "verified"}]
        state.paper_sections = {"title": "Test", "abstract": "Abstract"}
        state.sections_read = ["intro"]
        state.tool_call_history = []

        memory = MemoryStore(base_dir="/tmp/test_graceful")
        memory.state = MemoryState()

        # Should not raise even with minimal state
        stats = await end_session_with_reflection(
            state=state,
            memory=memory,
            paper_id="graceful_test",
            strategy_transitions=None,
            llm_call_fn=None,
        )
        assert isinstance(stats, dict)

    def test_memory_serialization_roundtrip_with_phase2_fields(self):
        """MemoryState Phase 2 fields survive serialize/deserialize."""
        state = MemoryState()
        state.fast_reflect_alerts = ["alert1", "alert2"]
        state._last_fast_reflect_count = 6
        state._last_deep_reflect_count = 12

        serialized = MemoryStore._serialize(state)
        deserialized = MemoryStore._deserialize(serialized)

        assert deserialized.fast_reflect_alerts == ["alert1", "alert2"]
        assert deserialized._last_fast_reflect_count == 6
        assert deserialized._last_deep_reflect_count == 12

    def test_memory_deserialization_backward_compat(self):
        """Old memory files without Phase 2 fields deserialize cleanly."""
        raw = {
            "version": "3.0",
            "last_updated": "2024-01-01",
            "sessions": [],
            "patterns": [],
            "procedures": [],
        }
        state = MemoryStore._deserialize(raw)
        assert state.fast_reflect_alerts == []
        assert state._last_fast_reflect_count == 0
        assert state._last_deep_reflect_count == 0
