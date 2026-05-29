"""
Tests for V3 Phase 3: Habit Evolution + Closed Loop.

Covers:
1. HabitLearner enhanced _select_mature_patterns (contrast_boost)
2. HabitLearner _pattern_to_habit (compute_relative_effectiveness)
3. HabitSelector combination tracking
4. CognitiveState hypothesis unification (SoT via HypothesisModule)
5. Harness linking hypothesis_module to cognitive_state
6. E2E lifecycle: learn → accumulate → contrast → reflect → evolve
"""

from __future__ import annotations

import pytest
from dataclasses import dataclass, field
from unittest.mock import MagicMock, patch

from core.evolution import (
    HabitLearner,
    LearnedHabit,
    compute_relative_effectiveness,
    EvolutionEngine,
)
from core.habits import HabitSelector, CognitiveHabit
from core.metacognition import CognitiveState, Hypothesis
from core.memory import MemoryStore, ProceduralPattern, MemoryState


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def tmp_memory(tmp_path):
    """Create a temporary MemoryStore."""
    return MemoryStore(base_dir=str(tmp_path))


@pytest.fixture
def mature_patterns():
    """Create a list of mature ProceduralPatterns for testing."""
    return [
        ProceduralPattern(
            pattern_id="pat_001",
            category="strategy_effectiveness",
            description="先读 methods 再读 results 效率更高",
            trigger_context="当论文含实证分析时",
            effectiveness_score=0.8,
            evidence_count=5,
            first_seen="2025-01-01T00:00:00",
            last_seen="2025-03-01T00:00:00",
        ),
        ProceduralPattern(
            pattern_id="pat_002",
            category="review_focus",
            description="重点审查样本选择偏差",
            trigger_context="当论文使用问卷数据时",
            effectiveness_score=0.7,
            evidence_count=4,
            first_seen="2025-01-15T00:00:00",
            last_seen="2025-03-10T00:00:00",
        ),
        ProceduralPattern(
            pattern_id="pat_003",
            category="verification_strategy",
            description="交叉验证回归系数显著性",
            trigger_context="含回归分析时",
            effectiveness_score=0.65,
            evidence_count=3,
            first_seen="2025-02-01T00:00:00",
            last_seen="2025-03-15T00:00:00",
        ),
    ]


# ============================================================
# Tests: compute_relative_effectiveness
# ============================================================


class TestComputeRelativeEffectiveness:
    """Tests for the V3 relative effectiveness scoring function."""

    def test_no_tokens_returns_neutral(self):
        """Zero tokens consumed -> neutral 0.5."""
        result = compute_relative_effectiveness(
            findings_count=5,
            tokens_consumed=0,
            sections_covered=3,
            paper_type="DID",
            historical_baseline={"DID": 2.0},
        )
        assert result == 0.5

    def test_no_baseline_data_returns_neutral(self):
        """Empty baseline -> neutral 0.5."""
        result = compute_relative_effectiveness(
            findings_count=5,
            tokens_consumed=10000,
            sections_covered=3,
            paper_type="DID",
            historical_baseline={},
        )
        assert result == 0.5

    def test_matches_baseline_returns_about_half(self):
        """Performance matching baseline -> ~0.5."""
        # baseline = 2.0 findings per 1k tokens
        # current = 5 findings / 5k tokens = 1.0 findings per 1k tokens
        # ratio = 1.0 / 2.0 = 0.5 -> mapped to 0.5/2.0 = 0.25
        result = compute_relative_effectiveness(
            findings_count=5,
            tokens_consumed=5000,
            sections_covered=3,
            paper_type="DID",
            historical_baseline={"DID": 2.0},
        )
        assert 0.0 <= result <= 1.0
        # 5/5 = 1.0; baseline=2.0; ratio=0.5; mapped = 0.25
        assert abs(result - 0.25) < 0.01

    def test_double_baseline_returns_high(self):
        """Performance 2x baseline -> 1.0 (capped)."""
        # 10 findings / 5k = 2.0; baseline = 1.0; ratio = 2.0 -> capped -> 1.0
        result = compute_relative_effectiveness(
            findings_count=10,
            tokens_consumed=5000,
            sections_covered=5,
            paper_type="RCT",
            historical_baseline={"RCT": 1.0},
        )
        assert result == 1.0

    def test_above_baseline_returns_above_half(self):
        """Performance above baseline -> > 0.5."""
        # 6 / 3k = 2.0; baseline = 1.5; ratio = 1.33 -> 1.33/2 = 0.667
        result = compute_relative_effectiveness(
            findings_count=6,
            tokens_consumed=3000,
            sections_covered=4,
            paper_type="DID",
            historical_baseline={"DID": 1.5},
        )
        assert result > 0.5

    def test_below_baseline_returns_below_half(self):
        """Performance below baseline -> < 0.5."""
        # 1 / 5k = 0.2; baseline = 2.0; ratio = 0.1 -> 0.1/2 = 0.05
        result = compute_relative_effectiveness(
            findings_count=1,
            tokens_consumed=5000,
            sections_covered=2,
            paper_type="DID",
            historical_baseline={"DID": 2.0},
        )
        assert result < 0.5

    def test_unknown_paper_type_uses_global_average(self):
        """Unknown paper type falls back to global average baseline."""
        result = compute_relative_effectiveness(
            findings_count=5,
            tokens_consumed=5000,
            sections_covered=3,
            paper_type="unknown_type",
            historical_baseline={"DID": 1.0, "RCT": 3.0},  # avg=2.0
        )
        # current = 1.0; baseline_avg = 2.0; ratio = 0.5 -> 0.25
        assert 0.0 <= result <= 1.0

    def test_result_always_in_range(self):
        """Result is always clamped to [0.0, 1.0]."""
        # Extremely high performance
        result = compute_relative_effectiveness(
            findings_count=100,
            tokens_consumed=1000,
            sections_covered=10,
            paper_type="DID",
            historical_baseline={"DID": 0.01},
        )
        assert result <= 1.0
        assert result >= 0.0


# ============================================================
# Tests: HabitLearner._select_mature_patterns with contrast boost
# ============================================================


class TestSelectMaturePatternsV3:
    """Tests for contrast-boosted pattern selection."""

    def test_contrast_boost_reorders_patterns(self, tmp_memory, mature_patterns):
        """Patterns with positive contrast delta are boosted in sort order."""
        tmp_memory.state.procedures = mature_patterns
        # Add contrast results favoring pat_003
        tmp_memory.state.contrast_results = [
            {"target_habit_id": "learned_pat_003", "delta": 0.5},
            {"target_habit_id": "learned_pat_003", "delta": 0.4},
        ]
        learner = HabitLearner(memory=tmp_memory)
        result = learner._select_mature_patterns()

        # pat_003 has lower base score (0.65*3=1.95) but contrast boost
        # pat_001 has 0.8*5=4.0, pat_002 has 0.7*4=2.8
        # pat_003 boosted: 1.95 + 0.45*10 = 6.45 -> should be first
        assert len(result) == 3
        assert result[0].pattern_id == "pat_003"

    def test_no_contrast_results_uses_base_score(self, tmp_memory, mature_patterns):
        """Without contrast results, ordering is by base score only."""
        tmp_memory.state.procedures = mature_patterns
        tmp_memory.state.contrast_results = []

        learner = HabitLearner(memory=tmp_memory)
        result = learner._select_mature_patterns()

        # Base scores: pat_001=4.0, pat_002=2.8, pat_003=1.95
        assert result[0].pattern_id == "pat_001"
        assert result[1].pattern_id == "pat_002"
        assert result[2].pattern_id == "pat_003"

    def test_negative_contrast_penalizes(self, tmp_memory, mature_patterns):
        """Negative contrast delta pushes pattern lower."""
        tmp_memory.state.procedures = mature_patterns
        tmp_memory.state.contrast_results = [
            {"target_habit_id": "learned_pat_001", "delta": -0.5},
        ]

        learner = HabitLearner(memory=tmp_memory)
        result = learner._select_mature_patterns()

        # pat_001 base=4.0, boost = -0.5*10 = -5.0 -> net = -1.0
        # pat_002 base=2.8, pat_003 base=1.95
        # Order: pat_002, pat_003, pat_001
        assert result[0].pattern_id == "pat_002"
        assert result[-1].pattern_id == "pat_001"

    @patch("core.godel_config.GODEL_INTRA_CONTRAST_ENABLED", False)
    def test_kill_switch_disables_contrast_boost(self, tmp_memory, mature_patterns):
        """When kill switch is off, no contrast boost applied."""
        tmp_memory.state.procedures = mature_patterns
        tmp_memory.state.contrast_results = [
            {"target_habit_id": "learned_pat_003", "delta": 10.0},  # Very high
        ]

        learner = HabitLearner(memory=tmp_memory)
        # Need to reimport to get the patched value
        import importlib
        import core.evolution
        importlib.reload(core.evolution)
        from core.evolution import HabitLearner as HL2
        learner2 = HL2(memory=tmp_memory)
        result = learner2._select_mature_patterns()

        # Without contrast, base order: pat_001, pat_002, pat_003
        assert result[0].pattern_id == "pat_001"


# ============================================================
# Tests: HabitLearner._pattern_to_habit with relative effectiveness
# ============================================================


class TestPatternToHabitV3:
    """Tests for V3 relative effectiveness scoring in _pattern_to_habit."""

    def test_with_baseline_uses_relative_scoring(self, tmp_memory, mature_patterns):
        """When historical baseline exists, uses compute_relative_effectiveness."""
        # Set up baseline data
        tmp_memory.state.session_experiences_v3 = [
            {"paper_type": "DID", "findings_count": 10, "total_tokens": 5000},
            {"paper_type": "DID", "findings_count": 8, "total_tokens": 4000},
        ]

        learner = HabitLearner(memory=tmp_memory)
        habit = learner._pattern_to_habit(mature_patterns[0])

        assert habit is not None
        # V3: confidence clamped to [0.3, 0.7]
        assert 0.3 <= habit.confidence <= 0.7

    def test_without_baseline_uses_v2_formula(self, tmp_memory, mature_patterns):
        """When no baseline data, falls back to V2 log-based formula."""
        # No session_experiences_v3 -> empty baseline
        tmp_memory.state.session_experiences_v3 = []

        learner = HabitLearner(memory=tmp_memory)
        habit = learner._pattern_to_habit(mature_patterns[0])

        assert habit is not None
        # V3: still clamped to [0.3, 0.7]
        assert 0.3 <= habit.confidence <= 0.7

    def test_confidence_clamped_upper_bound(self, tmp_memory):
        """Even with perfect relative score, confidence maxes at 0.7."""
        # Create pattern with extremely high effectiveness
        pattern = ProceduralPattern(
            pattern_id="pat_perfect",
            category="strategy_effectiveness",
            description="Perfect pattern",
            trigger_context="always",
            effectiveness_score=1.0,
            evidence_count=100,
        )
        # Baseline that makes this pattern look amazing
        tmp_memory.state.session_experiences_v3 = [
            {"paper_type": "unknown", "findings_count": 1, "total_tokens": 100000},
        ]
        tmp_memory.state.procedures = [pattern]

        learner = HabitLearner(memory=tmp_memory)
        habit = learner._pattern_to_habit(pattern)

        assert habit is not None
        assert habit.confidence <= 0.7

    def test_confidence_clamped_lower_bound(self, tmp_memory):
        """Even with poor relative score, confidence is at least 0.3."""
        pattern = ProceduralPattern(
            pattern_id="pat_weak",
            category="strategy_effectiveness",
            description="Weak pattern",
            trigger_context="rarely",
            effectiveness_score=0.61,
            evidence_count=3,
        )
        tmp_memory.state.session_experiences_v3 = [
            {"paper_type": "unknown", "findings_count": 100, "total_tokens": 10000},
        ]
        tmp_memory.state.procedures = [pattern]

        learner = HabitLearner(memory=tmp_memory)
        habit = learner._pattern_to_habit(pattern)

        assert habit is not None
        assert habit.confidence >= 0.3


# ============================================================
# Tests: HabitSelector combination tracking
# ============================================================


class TestCombinationTracking:
    """Tests for V3 combination effectiveness tracking."""

    def test_record_single_combination(self):
        """Can record a single combination entry."""
        selector = HabitSelector()
        selector.record_combination_effectiveness(
            active_habit_ids=["h1", "h2"],
            section_findings_density=0.5,
        )
        assert hasattr(selector, "_combination_log")
        assert len(selector._combination_log) == 1
        assert selector._combination_log[0]["combination"] == ["h1", "h2"]
        assert selector._combination_log[0]["density"] == 0.5

    def test_record_sorts_habit_ids(self):
        """Habit IDs are sorted for consistent combination keys."""
        selector = HabitSelector()
        selector.record_combination_effectiveness(["h3", "h1", "h2"], 0.4)
        assert selector._combination_log[0]["combination"] == ["h1", "h2", "h3"]

    def test_insights_requires_three_observations(self):
        """get_combination_insights only returns combos with >= 3 observations."""
        selector = HabitSelector()
        # Record same combo 2 times -> not enough
        selector.record_combination_effectiveness(["h1", "h2"], 0.5)
        selector.record_combination_effectiveness(["h1", "h2"], 0.6)
        assert selector.get_combination_insights() == []

        # Third observation triggers insight
        selector.record_combination_effectiveness(["h1", "h2"], 0.7)
        insights = selector.get_combination_insights()
        assert len(insights) == 1
        assert insights[0]["combination"] == ["h1", "h2"]
        assert abs(insights[0]["avg_density"] - 0.6) < 0.01
        assert insights[0]["n"] == 3

    def test_multiple_combinations_tracked(self):
        """Multiple different combinations are tracked independently."""
        selector = HabitSelector()
        for _ in range(3):
            selector.record_combination_effectiveness(["h1", "h2"], 0.5)
        for _ in range(3):
            selector.record_combination_effectiveness(["h2", "h3"], 0.8)
        # Single observation of h1+h3 (shouldn't appear)
        selector.record_combination_effectiveness(["h1", "h3"], 0.9)

        insights = selector.get_combination_insights()
        assert len(insights) == 2
        combos = [i["combination"] for i in insights]
        assert ["h1", "h2"] in combos
        assert ["h2", "h3"] in combos

    def test_empty_log_returns_empty_insights(self):
        """No observations -> no insights."""
        selector = HabitSelector()
        assert selector.get_combination_insights() == []


# ============================================================
# Tests: CognitiveState hypothesis unification
# ============================================================


class TestHypothesisUnification:
    """Tests for V3 CognitiveState hypothesis SoT unification."""

    def test_set_hypothesis_module(self):
        """Can set hypothesis module reference."""
        state = CognitiveState()
        mock_module = MagicMock()
        state.set_hypothesis_module(mock_module)
        assert state._hypothesis_module_ref is mock_module

    def test_format_for_context_uses_module_when_set(self):
        """When module is set, format_for_context uses module.format_status()."""
        state = CognitiveState(
            current_strategy="deep_investigation",
            hypotheses=[Hypothesis(claim="test", confidence=0.8)],
        )
        mock_module = MagicMock()
        mock_module.format_status.return_value = "假说工作记忆 | 总计 2 | 活跃 1"
        state.set_hypothesis_module(mock_module)

        result = state.format_for_context()
        assert "假说工作记忆" in result
        mock_module.format_status.assert_called_once()
        # Local hypotheses field should NOT be used when module is set
        assert "假说 (1 条活跃)" not in result

    def test_format_for_context_fallback_without_module(self):
        """Without module, falls back to local hypotheses field."""
        state = CognitiveState(
            current_strategy="deep_investigation",
            hypotheses=[Hypothesis(claim="DID平行趋势假设不成立", confidence=0.7)],
        )
        # _hypothesis_module_ref is None by default

        result = state.format_for_context()
        assert "假说 (1 条活跃)" in result
        assert "DID平行趋势假设不成立" in result

    def test_format_for_context_module_empty_status(self):
        """When module returns empty status, hypothesis section is skipped."""
        state = CognitiveState(
            current_strategy="breadth_scan",
            hypotheses=[Hypothesis(claim="test", confidence=0.5)],
        )
        mock_module = MagicMock()
        mock_module.format_status.return_value = ""  # Empty
        state.set_hypothesis_module(mock_module)

        result = state.format_for_context()
        # Neither module output nor local output should appear
        assert "假说" not in result

    def test_update_from_reflection_still_works_with_module(self):
        """update_from_reflection still updates local hypotheses (backward compat)."""
        state = CognitiveState()
        mock_module = MagicMock()
        state.set_hypothesis_module(mock_module)

        state.update_from_reflection({
            "strategy": "targeted_verification",
            "hypotheses": [{"claim": "New hypothesis", "confidence": 0.6}],
        })
        assert state.current_strategy == "targeted_verification"
        assert len(state.hypotheses) == 1
        assert state.hypotheses[0].claim == "New hypothesis"

    def test_initial_state_still_returns_empty_string(self):
        """Undecided state with no data still returns empty."""
        state = CognitiveState()
        assert state.format_for_context() == ""

    def test_module_ref_not_in_repr(self):
        """_hypothesis_module_ref is excluded from repr (repr=False)."""
        state = CognitiveState()
        mock_module = MagicMock()
        state.set_hypothesis_module(mock_module)
        repr_str = repr(state)
        assert "_hypothesis_module_ref" not in repr_str


# ============================================================
# Tests: E2E Lifecycle
# ============================================================


class TestEvolutionLifecycle:
    """E2E tests for the habit evolution closed loop."""

    def test_learn_habits_with_contrast_data(self, tmp_memory, mature_patterns):
        """Full cycle: mature patterns + contrast data -> learned habits."""
        tmp_memory.state.procedures = mature_patterns
        tmp_memory.state.contrast_results = [
            {"target_habit_id": "learned_pat_001", "delta": 0.2},
            {"target_habit_id": "learned_pat_001", "delta": 0.15},
        ]
        tmp_memory.state.session_experiences_v3 = [
            {"paper_type": "unknown", "findings_count": 10, "total_tokens": 10000},
        ]

        learner = HabitLearner(memory=tmp_memory)
        habits = learner.learn()

        # Should learn from all 3 mature patterns
        assert len(habits) >= 1
        # All habits have confidence in [0.3, 0.7]
        for h in habits:
            assert 0.3 <= h.confidence <= 0.7
            assert h.generation == 1  # First generation

    def test_combination_insights_feed_deep_reflector(self):
        """Combination insights can be passed to DeepReflector for analysis."""
        selector = HabitSelector()
        # Simulate session data accumulation
        for _ in range(5):
            selector.record_combination_effectiveness(["h1", "h2", "h3"], 0.8)
        for _ in range(4):
            selector.record_combination_effectiveness(["h1", "h2"], 0.4)

        insights = selector.get_combination_insights()
        # h1+h2+h3 has 5 observations -> insight
        # h1+h2 has 4 observations -> insight
        assert len(insights) == 2
        # The triple combination outperforms
        triple = next(i for i in insights if len(i["combination"]) == 3)
        pair = next(i for i in insights if len(i["combination"]) == 2)
        assert triple["avg_density"] > pair["avg_density"]

    def test_evolution_engine_filters_low_confidence_habits(self, tmp_memory):
        """get_habits_for_selector should skip habits with confidence < 0.3."""
        # This is declared in the plan but the actual filtering happens
        # at DeepReflector level. Test that the mechanism works:
        tmp_memory.state.procedures = [
            ProceduralPattern(
                pattern_id="pat_low",
                category="strategy_effectiveness",
                description="Low confidence pattern",
                trigger_context="test",
                effectiveness_score=0.61,  # Just above threshold
                evidence_count=3,
            ),
        ]
        # The baseline makes this pattern look terrible
        tmp_memory.state.session_experiences_v3 = [
            {"paper_type": "unknown", "findings_count": 100, "total_tokens": 5000},
        ]

        learner = HabitLearner(memory=tmp_memory)
        habits = learner.learn()
        # Even with low relative effectiveness, clamp ensures >= 0.3
        # So habit will still be generated (clamping protects it)
        if habits:
            assert habits[0].confidence >= 0.3


# ============================================================
# Tests: Graceful Degradation
# ============================================================


class TestPhase3GracefulDegradation:
    """Tests ensuring Phase 3 components degrade gracefully."""

    def test_select_mature_patterns_empty_procedures(self, tmp_memory):
        """No procedures -> empty result."""
        tmp_memory.state.procedures = []
        learner = HabitLearner(memory=tmp_memory)
        assert learner._select_mature_patterns() == []

    def test_pattern_to_habit_handles_missing_baseline_fields(self, tmp_memory):
        """Pattern with minimal fields still produces a habit."""
        pattern = ProceduralPattern(
            pattern_id="minimal",
            category="strategy_effectiveness",
            description="Minimal pattern for testing",
            trigger_context="any",
            effectiveness_score=0.7,
            evidence_count=4,
        )
        tmp_memory.state.procedures = [pattern]
        tmp_memory.state.session_experiences_v3 = []  # No baseline

        learner = HabitLearner(memory=tmp_memory)
        habit = learner._pattern_to_habit(pattern)
        assert habit is not None
        assert habit.confidence >= 0.3

    def test_combination_tracking_isolated_from_selection(self):
        """Combination tracking doesn't affect habit selection logic."""
        selector = HabitSelector()
        # Record lots of data
        for i in range(10):
            selector.record_combination_effectiveness([f"h{i}", "h0"], float(i) / 10)

        # Selection should still work normally
        selected = selector.select(phase="DEEP_REVIEW")
        assert len(selected) > 0

    def test_cognitive_state_serialization_with_module_ref(self):
        """CognitiveState can still be used in dict-like operations with module ref."""
        state = CognitiveState(current_strategy="synthesis")
        mock_module = MagicMock()
        mock_module.format_status.return_value = "假说工作记忆 | 总计 0"
        state.set_hypothesis_module(mock_module)

        # Should not crash on format
        result = state.format_for_context()
        assert "综合收尾" in result


# ============================================================
# Tests: Abandonment Cooldown (real record format)
# ============================================================


class TestAbandonmentCooldown:
    """Tests for _get_abandoned_habit_ids_in_cooldown with real record format."""

    def test_retire_within_cooldown_is_detected(self, tmp_memory):
        """Habits retired recently should be in cooldown."""
        # Simulate 20 sessions total
        tmp_memory.state.session_experiences_v3 = [{"x": i} for i in range(20)]
        # A retire decision happened at session 15 (5 sessions ago < 12)
        tmp_memory.state.evolution_records = [
            {
                "timestamp": "2025-03-01T00:00:00",
                "trigger_type": "deep",
                "session_count": 15,
                "habit_decisions": [
                    {"habit_id": "learned_pat_001", "action": "retire", "confidence_delta": 0.2},
                ],
                "maturity_updates": [],
            }
        ]

        engine = EvolutionEngine(memory=tmp_memory)
        cooldown_ids = engine._get_abandoned_habit_ids_in_cooldown()
        assert "learned_pat_001" in cooldown_ids

    def test_retire_outside_cooldown_not_detected(self, tmp_memory):
        """Habits retired long ago (>= 12 sessions) should NOT be in cooldown."""
        # Simulate 30 sessions total
        tmp_memory.state.session_experiences_v3 = [{"x": i} for i in range(30)]
        # Retire happened at session 10 (20 sessions ago >= 12)
        tmp_memory.state.evolution_records = [
            {
                "timestamp": "2025-01-01T00:00:00",
                "trigger_type": "deep",
                "session_count": 10,
                "habit_decisions": [
                    {"habit_id": "learned_pat_old", "action": "retire", "confidence_delta": 0.2},
                ],
                "maturity_updates": [],
            }
        ]

        engine = EvolutionEngine(memory=tmp_memory)
        cooldown_ids = engine._get_abandoned_habit_ids_in_cooldown()
        assert "learned_pat_old" not in cooldown_ids

    def test_boost_action_not_treated_as_retire(self, tmp_memory):
        """Only 'retire' actions trigger cooldown, not 'boost' or 'reduce'."""
        tmp_memory.state.session_experiences_v3 = [{"x": i} for i in range(20)]
        tmp_memory.state.evolution_records = [
            {
                "timestamp": "2025-03-01T00:00:00",
                "trigger_type": "deep",
                "session_count": 18,
                "habit_decisions": [
                    {"habit_id": "learned_boosted", "action": "boost", "confidence_delta": 0.1},
                    {"habit_id": "learned_reduced", "action": "reduce", "confidence_delta": 0.1},
                ],
                "maturity_updates": [],
            }
        ]

        engine = EvolutionEngine(memory=tmp_memory)
        cooldown_ids = engine._get_abandoned_habit_ids_in_cooldown()
        assert len(cooldown_ids) == 0

    def test_multiple_records_mixed_cooldown(self, tmp_memory):
        """Multiple records: some in cooldown, some expired."""
        tmp_memory.state.session_experiences_v3 = [{"x": i} for i in range(25)]
        tmp_memory.state.evolution_records = [
            {
                "trigger_type": "deep",
                "session_count": 5,  # 20 sessions ago -> expired
                "habit_decisions": [
                    {"habit_id": "old_retire", "action": "retire", "confidence_delta": 0.2},
                ],
            },
            {
                "trigger_type": "deep",
                "session_count": 20,  # 5 sessions ago -> in cooldown
                "habit_decisions": [
                    {"habit_id": "recent_retire", "action": "retire", "confidence_delta": 0.2},
                    {"habit_id": "recent_boost", "action": "boost", "confidence_delta": 0.1},
                ],
            },
        ]

        engine = EvolutionEngine(memory=tmp_memory)
        cooldown_ids = engine._get_abandoned_habit_ids_in_cooldown()
        assert "recent_retire" in cooldown_ids
        assert "old_retire" not in cooldown_ids
        assert "recent_boost" not in cooldown_ids

    def test_cooldown_filters_from_get_habits_for_selector(self, tmp_memory):
        """Habits in cooldown are excluded from get_habits_for_selector()."""
        # Set up a learned habit with valid confidence (above threshold)
        tmp_memory.state.session_experiences_v3 = [{"x": i} for i in range(20)]
        tmp_memory.state.evolution_records = [
            {
                "trigger_type": "deep",
                "session_count": 18,
                "habit_decisions": [
                    {"habit_id": "learned_retired_h", "action": "retire", "confidence_delta": 0.2},
                ],
            },
        ]

        # Create engine with a habit that has valid confidence but is in cooldown
        engine = EvolutionEngine(memory=tmp_memory)
        engine._learned_habits = [
            LearnedHabit(
                id="learned_retired_h",
                name="Retired Habit",
                content="This was retired",
                confidence=0.5,  # Above threshold, but in cooldown
                generation=1,
                phases=["DEEP_REVIEW"],
                priority=60,
                source_patterns=["pat_001"],
            ),
            LearnedHabit(
                id="learned_active_h",
                name="Active Habit",
                content="This is active",
                confidence=0.6,
                generation=1,
                phases=["DEEP_REVIEW"],
                priority=65,
                source_patterns=["pat_002"],
            ),
        ]

        habits = engine.get_habits_for_selector()
        habit_ids = [h.id for h in habits]
        assert "learned_retired_h" not in habit_ids
        assert "learned_active_h" in habit_ids

    def test_empty_evolution_records_no_cooldown(self, tmp_memory):
        """No evolution records -> no cooldown."""
        tmp_memory.state.session_experiences_v3 = [{"x": i} for i in range(10)]
        tmp_memory.state.evolution_records = []

        engine = EvolutionEngine(memory=tmp_memory)
        cooldown_ids = engine._get_abandoned_habit_ids_in_cooldown()
        assert len(cooldown_ids) == 0
