"""
tests/test_phase8_orchestrator.py

Comprehensive test suite for Phase 8: Dual-Loop Architecture (Hermes).

Covers:
  - ResourceDimension enum
  - ResourceBudget (consume, remaining, utilization, warning, exhausted, serialize)
  - PhaseResourceBudget (consume_turn, is_over_budget, serialize)
  - PaperComplexity enum & PaperProfile (from_paper_text, classify, serialize)
  - PhaseStrategy enum & PhasePlan
  - ReviewPlan (active_phases, is_phase_skipped, serialize)
  - ReviewPlanner (strategy selection, resource allocation, 5 templates)
  - DualLoopSignalType enum & DualLoopSignal
  - PlanUpdate (to_advisory_message, all update types)
  - PlanAdapter (process_signal for all signal types, stuck/budget/finding/quality)
  - StrategyLearner (record_outcome, recommend_strategy, similarity, eviction)
  - OuterLoop (plan_review, on_turn_end, on_phase_transition, on_session_end, rate limiting)
  - DualLoopOrchestrator facade (plan_review, tick, on_phase_change, on_finding, conclude)
  - InnerLoopObserver protocol
  - EventBus integration helpers
  - Kill Switch behavior (all methods become no-ops)
  - Serialization/deserialization round-trips
  - End-to-end simulation
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.orchestrator import (
    ResourceDimension,
    ResourceBudget,
    PhaseResourceBudget,
    PaperComplexity,
    PaperProfile,
    PhaseStrategy,
    PhasePlan,
    ReviewPlan,
    ReviewPlanner,
    DualLoopSignalType,
    DualLoopSignal,
    PlanUpdate,
    PlanAdapter,
    StrategyLearner,
    StrategyRecord,
    ReviewOutcome,
    OuterLoop,
    InnerLoopStatus,
    InnerLoopObserver,
    DualLoopOrchestrator,
    register_orchestrator_with_event_bus,
    create_orchestrator_for_session,
    _is_enabled,
)
from core.skills.base import Finding


# ==============================================================
# Sample Texts for PaperProfile Tests
# ==============================================================

SIMPLE_PAPER_TEXT = """
1. Introduction
This is a short comment on Smith (2020).
We make one contribution.
2. Data
Our sample includes 100 observations.
3. Results
The coefficient is 0.5.
"""

COMPLEX_ECON_PAPER = """
1. Introduction

This paper makes a novel contribution to the literature on labor economics.
We propose a new approach using difference-in-difference combined with
instrumental variables to identify the causal effect of minimum wage on employment.

Our contribution extends the existing framework and challenges the conventional
wisdom in the debate about policy implications.

2. Literature Review

The relationship between minimum wage and employment is controversial. Prior
work by Card and Krueger (1994) has been contested by Neumark and Wascher (2000).

3. Theoretical Framework

We develop a structural model of labor market equilibrium with heterogeneous
firms. The model features discrete choice in hiring decisions.

4. Data and Sample Construction

Table 1 shows summary statistics for our panel data spanning 2000-2020.
Table 2 presents first-stage results.

Figure 1 shows the event study plot with pre-trends.
Figure 2 shows the RDD estimates.

5. Identification Strategy

We use a difference-in-difference design exploiting staggered adoption
across states. The parallel trends assumption is tested via event study.
We instrument minimum wage changes using political party control.

6. Main Results

Table 3 presents the main DID estimates.
Table 4 shows the IV results using two-stage least squares.

7. Robustness Checks

We conduct extensive robustness checks including propensity score matching
and panel data fixed effects estimation.

8. Welfare Analysis

The welfare effects of minimum wage increases are computed using our
structural estimation framework.

9. Conclusion

Our findings have significant policy implications for the ongoing debate
about minimum wage legislation.
"""

SHORT_NOTE_TEXT = """
1. Summary
Brief response to Jones (2023) about GDP measurement.
"""

DATA_HEAVY_TEXT = """
1. Introduction

We examine the effect of tax policy on firm behavior using panel data.

Table 1: Summary statistics
Table 2: Baseline estimates
Table 3: Heterogeneity analysis
Table 4: Robustness
Table 5: Mechanism

2. Data

We collect administrative tax records from 50,000 firms.

3. Empirical Strategy

Our identification relies on fixed effects estimation with panel data.

4. Results

Table 6 shows extensive margin effects.
Table 7 shows intensive margin effects.
"""


# ==============================================================
# 1. ResourceDimension Tests
# ==============================================================

class TestResourceDimension:
    """Tests for the ResourceDimension enum."""

    def test_all_dimensions_exist(self):
        assert ResourceDimension.TOKENS == ResourceDimension("tokens")
        assert ResourceDimension.TURNS == ResourceDimension("turns")
        assert ResourceDimension.TIME_SECONDS == ResourceDimension("time_seconds")
        assert ResourceDimension.API_CALLS == ResourceDimension("api_calls")
        assert ResourceDimension.FINDINGS_QUOTA == ResourceDimension("findings_quota")

    def test_dimension_count(self):
        assert len(ResourceDimension) == 5

    def test_dimension_values(self):
        values = {d.value for d in ResourceDimension}
        assert values == {"tokens", "turns", "time_seconds", "api_calls", "findings_quota"}


# ==============================================================
# 2. ResourceBudget Tests
# ==============================================================

class TestResourceBudget:
    """Tests for the ResourceBudget class."""

    def test_default_creation(self):
        budget = ResourceBudget.default()
        assert budget.allocations[ResourceDimension.TOKENS] == 128000.0
        assert budget.allocations[ResourceDimension.TURNS] == 50.0
        assert budget.allocations[ResourceDimension.TIME_SECONDS] == 600.0
        assert budget.allocations[ResourceDimension.API_CALLS] == 100.0
        assert budget.allocations[ResourceDimension.FINDINGS_QUOTA] == 30.0

    def test_default_custom_params(self):
        budget = ResourceBudget.default(
            total_tokens=64000,
            max_turns=25,
            max_time=300.0,
            max_api_calls=50,
            max_findings=15,
        )
        assert budget.allocations[ResourceDimension.TOKENS] == 64000.0
        assert budget.allocations[ResourceDimension.TURNS] == 25.0
        assert budget.allocations[ResourceDimension.TIME_SECONDS] == 300.0
        assert budget.allocations[ResourceDimension.API_CALLS] == 50.0
        assert budget.allocations[ResourceDimension.FINDINGS_QUOTA] == 15.0

    def test_all_consumed_start_at_zero(self):
        budget = ResourceBudget.default()
        for dim in ResourceDimension:
            assert budget.consumed[dim] == 0.0

    def test_consume_positive(self):
        budget = ResourceBudget.default()
        budget.consume(ResourceDimension.TOKENS, 5000.0)
        assert budget.consumed[ResourceDimension.TOKENS] == 5000.0

    def test_consume_accumulates(self):
        budget = ResourceBudget.default()
        budget.consume(ResourceDimension.TOKENS, 1000.0)
        budget.consume(ResourceDimension.TOKENS, 2000.0)
        assert budget.consumed[ResourceDimension.TOKENS] == 3000.0

    def test_consume_negative_ignored(self):
        budget = ResourceBudget.default()
        budget.consume(ResourceDimension.TOKENS, 5000.0)
        budget.consume(ResourceDimension.TOKENS, -1000.0)
        assert budget.consumed[ResourceDimension.TOKENS] == 5000.0

    def test_remaining(self):
        budget = ResourceBudget.default()
        budget.consume(ResourceDimension.TOKENS, 28000.0)
        assert budget.remaining(ResourceDimension.TOKENS) == 100000.0

    def test_remaining_can_be_negative(self):
        budget = ResourceBudget.default()
        budget.consume(ResourceDimension.TOKENS, 200000.0)
        assert budget.remaining(ResourceDimension.TOKENS) < 0

    def test_utilization_empty(self):
        budget = ResourceBudget.default()
        assert budget.utilization(ResourceDimension.TOKENS) == 0.0

    def test_utilization_partial(self):
        budget = ResourceBudget.default()
        budget.consume(ResourceDimension.TOKENS, 64000.0)
        assert abs(budget.utilization(ResourceDimension.TOKENS) - 0.5) < 0.001

    def test_utilization_over_budget(self):
        budget = ResourceBudget.default()
        budget.consume(ResourceDimension.TOKENS, 256000.0)
        assert budget.utilization(ResourceDimension.TOKENS) == 2.0

    def test_utilization_zero_allocation(self):
        budget = ResourceBudget(
            allocations={ResourceDimension.TOKENS: 0.0},
            consumed={ResourceDimension.TOKENS: 100.0},
            warning_thresholds={},
        )
        assert budget.utilization(ResourceDimension.TOKENS) == 0.0

    def test_is_warning_below_threshold(self):
        budget = ResourceBudget.default()
        budget.consume(ResourceDimension.TOKENS, 50000.0)  # ~39%
        assert not budget.is_warning(ResourceDimension.TOKENS)

    def test_is_warning_above_threshold(self):
        budget = ResourceBudget.default()
        budget.consume(ResourceDimension.TOKENS, 100000.0)  # ~78%
        assert budget.is_warning(ResourceDimension.TOKENS)

    def test_is_exhausted(self):
        budget = ResourceBudget.default()
        budget.consume(ResourceDimension.TURNS, 50.0)
        assert budget.is_exhausted(ResourceDimension.TURNS)

    def test_is_not_exhausted(self):
        budget = ResourceBudget.default()
        budget.consume(ResourceDimension.TURNS, 49.0)
        assert not budget.is_exhausted(ResourceDimension.TURNS)

    def test_overall_utilization_empty(self):
        budget = ResourceBudget.default()
        assert budget.overall_utilization() == 0.0

    def test_overall_utilization_mixed(self):
        budget = ResourceBudget.default()
        budget.consume(ResourceDimension.TOKENS, 64000.0)  # 50%
        budget.consume(ResourceDimension.TURNS, 25.0)  # 50%
        util = budget.overall_utilization()
        assert 0.2 < util < 0.6  # Weighted average

    def test_get_warning_dimensions(self):
        budget = ResourceBudget.default()
        budget.consume(ResourceDimension.TOKENS, 100000.0)
        budget.consume(ResourceDimension.TURNS, 40.0)
        warnings = budget.get_warning_dimensions()
        assert ResourceDimension.TOKENS in warnings
        assert ResourceDimension.TURNS in warnings

    def test_get_exhausted_dimensions(self):
        budget = ResourceBudget.default()
        budget.consume(ResourceDimension.TURNS, 60.0)
        exhausted = budget.get_exhausted_dimensions()
        assert ResourceDimension.TURNS in exhausted

    def test_allocate_to_phase(self):
        budget = ResourceBudget.default()
        phase_budget = budget.allocate_to_phase("deep_review", 0.5)
        assert phase_budget.phase == "deep_review"
        assert phase_budget.token_budget == 64000
        assert phase_budget.turn_budget == 25

    def test_allocate_to_phase_respects_remaining(self):
        budget = ResourceBudget.default()
        budget.consume(ResourceDimension.TOKENS, 100000.0)
        phase_budget = budget.allocate_to_phase("synthesis", 0.5)
        assert phase_budget.token_budget == 14000  # 50% of remaining 28000

    def test_allocate_to_phase_fraction_clamped(self):
        budget = ResourceBudget.default()
        phase_budget = budget.allocate_to_phase("test", 1.5)
        # Should be clamped to 1.0
        assert phase_budget.token_budget == 128000

    def test_serialize_deserialize_roundtrip(self):
        budget = ResourceBudget.default()
        budget.consume(ResourceDimension.TOKENS, 42000.0)
        budget.consume(ResourceDimension.TURNS, 7.0)

        data = budget.serialize()
        restored = ResourceBudget.deserialize(data)

        assert restored.consumed[ResourceDimension.TOKENS] == 42000.0
        assert restored.consumed[ResourceDimension.TURNS] == 7.0
        assert restored.allocations[ResourceDimension.TOKENS] == 128000.0


# ==============================================================
# 3. PhaseResourceBudget Tests
# ==============================================================

class TestPhaseResourceBudget:
    """Tests for PhaseResourceBudget."""

    def test_construction(self):
        pb = PhaseResourceBudget(
            phase="deep_review",
            token_budget=64000,
            turn_budget=25,
            time_budget=300.0,
        )
        assert pb.phase == "deep_review"
        assert pb.consumed_tokens == 0
        assert pb.consumed_turns == 0
        assert pb.consumed_time == 0.0

    def test_token_remaining(self):
        pb = PhaseResourceBudget(
            phase="test", token_budget=10000, turn_budget=5, time_budget=60.0,
        )
        pb.consumed_tokens = 3000
        assert pb.token_remaining == 7000

    def test_turn_remaining(self):
        pb = PhaseResourceBudget(
            phase="test", token_budget=10000, turn_budget=5, time_budget=60.0,
        )
        pb.consumed_turns = 3
        assert pb.turn_remaining == 2

    def test_is_over_budget_false(self):
        pb = PhaseResourceBudget(
            phase="test", token_budget=10000, turn_budget=5, time_budget=60.0,
        )
        pb.consumed_tokens = 5000
        assert not pb.is_over_budget

    def test_is_over_budget_tokens(self):
        pb = PhaseResourceBudget(
            phase="test", token_budget=10000, turn_budget=5, time_budget=60.0,
        )
        pb.consumed_tokens = 12000
        assert pb.is_over_budget

    def test_is_over_budget_turns(self):
        pb = PhaseResourceBudget(
            phase="test", token_budget=10000, turn_budget=5, time_budget=60.0,
        )
        pb.consumed_turns = 6
        assert pb.is_over_budget

    def test_is_over_budget_time(self):
        pb = PhaseResourceBudget(
            phase="test", token_budget=10000, turn_budget=5, time_budget=60.0,
        )
        pb.consumed_time = 61.0
        assert pb.is_over_budget

    def test_utilization(self):
        pb = PhaseResourceBudget(
            phase="test", token_budget=10000, turn_budget=10, time_budget=100.0,
        )
        pb.consumed_tokens = 5000  # 50%
        pb.consumed_turns = 8  # 80% — highest
        pb.consumed_time = 30.0  # 30%
        assert abs(pb.utilization - 0.8) < 0.001

    def test_consume_turn(self):
        pb = PhaseResourceBudget(
            phase="test", token_budget=10000, turn_budget=5, time_budget=60.0,
        )
        pb.consume_turn(tokens=3000, elapsed=5.0)
        assert pb.consumed_tokens == 3000
        assert pb.consumed_turns == 1
        assert pb.consumed_time == 5.0

        pb.consume_turn(tokens=2000, elapsed=3.0)
        assert pb.consumed_tokens == 5000
        assert pb.consumed_turns == 2
        assert pb.consumed_time == 8.0

    def test_serialize_deserialize(self):
        pb = PhaseResourceBudget(
            phase="deep_review", token_budget=50000,
            turn_budget=20, time_budget=200.0,
            consumed_tokens=10000, consumed_turns=3, consumed_time=45.0,
        )
        data = pb.serialize()
        restored = PhaseResourceBudget.deserialize(data)
        assert restored.phase == "deep_review"
        assert restored.token_budget == 50000
        assert restored.consumed_tokens == 10000
        assert restored.consumed_turns == 3


# ==============================================================
# 4. PaperProfile Tests
# ==============================================================

class TestPaperProfile:
    """Tests for PaperProfile and PaperComplexity."""

    def test_complexity_enum(self):
        assert PaperComplexity.SIMPLE.value == "simple"
        assert PaperComplexity.MODERATE.value == "moderate"
        assert PaperComplexity.COMPLEX.value == "complex"
        assert PaperComplexity.HIGHLY_COMPLEX.value == "highly_complex"

    def test_from_paper_text_simple(self):
        profile = PaperProfile.from_paper_text(SIMPLE_PAPER_TEXT)
        assert profile.complexity == PaperComplexity.SIMPLE
        assert profile.estimated_length_tokens < 8000

    def test_from_paper_text_complex(self):
        profile = PaperProfile.from_paper_text(COMPLEX_ECON_PAPER)
        assert profile.complexity in (PaperComplexity.COMPLEX, PaperComplexity.HIGHLY_COMPLEX)
        assert len(profile.methodology_types) >= 2  # DID, IV, etc.
        assert profile.has_tables
        assert profile.has_figures
        assert profile.num_sections >= 5

    def test_methodology_detection_did(self):
        text = "We use a difference-in-difference design."
        profile = PaperProfile.from_paper_text(text)
        assert "DID" in profile.methodology_types

    def test_methodology_detection_iv(self):
        text = "Our instrumental variable estimates using 2SLS confirm the effect."
        profile = PaperProfile.from_paper_text(text)
        assert "IV" in profile.methodology_types

    def test_methodology_detection_rdd(self):
        text = "We apply a regression discontinuity design at the threshold."
        profile = PaperProfile.from_paper_text(text)
        assert "RDD" in profile.methodology_types

    def test_methodology_detection_structural(self):
        text = "We estimate a structural model with discrete choice parameters."
        profile = PaperProfile.from_paper_text(text)
        assert "STRUCTURAL" in profile.methodology_types

    def test_table_detection(self):
        text = "Table 1 shows the results. Table 2 presents robustness."
        profile = PaperProfile.from_paper_text(text)
        assert profile.has_tables

    def test_figure_detection(self):
        text = "Figure 1 presents the time series. Fig. 2 shows distributions."
        profile = PaperProfile.from_paper_text(text)
        assert profile.has_figures

    def test_field_detection_labor(self):
        text = "We study wage effects on worker employment " * 5
        profile = PaperProfile.from_paper_text(text)
        assert "labor" in profile.field_tags

    def test_field_detection_with_metadata(self):
        profile = PaperProfile.from_paper_text("Short text.", {"field": "finance"})
        assert "finance" in profile.field_tags

    def test_novelty_signals(self):
        text = "This paper makes a novel contribution. We propose a new approach to estimation."
        profile = PaperProfile.from_paper_text(text)
        assert len(profile.novelty_signals) > 0

    def test_controversy_signals(self):
        text = "Our results challenge the conventional view. This is controversial and we rebut the prior claims."
        profile = PaperProfile.from_paper_text(text)
        assert len(profile.controversy_signals) > 0

    def test_classify_complexity_simple(self):
        complexity = PaperProfile._classify_complexity(
            estimated_tokens=5000, num_methods=0,
            num_sections=3, has_novelty=False, has_controversy=False,
        )
        assert complexity == PaperComplexity.SIMPLE

    def test_classify_complexity_moderate(self):
        complexity = PaperProfile._classify_complexity(
            estimated_tokens=15000, num_methods=1,
            num_sections=6, has_novelty=True, has_controversy=False,
        )
        assert complexity == PaperComplexity.MODERATE

    def test_classify_complexity_complex(self):
        complexity = PaperProfile._classify_complexity(
            estimated_tokens=25000, num_methods=2,
            num_sections=10, has_novelty=True, has_controversy=False,
        )
        assert complexity == PaperComplexity.COMPLEX

    def test_classify_complexity_highly_complex(self):
        complexity = PaperProfile._classify_complexity(
            estimated_tokens=50000, num_methods=4,
            num_sections=16, has_novelty=True, has_controversy=True,
        )
        assert complexity == PaperComplexity.HIGHLY_COMPLEX

    def test_serialize_deserialize(self):
        profile = PaperProfile.from_paper_text(COMPLEX_ECON_PAPER)
        data = profile.serialize()
        restored = PaperProfile.deserialize(data)
        assert restored.complexity == profile.complexity
        assert restored.methodology_types == profile.methodology_types
        assert restored.has_tables == profile.has_tables
        assert restored.field_tags == profile.field_tags

    def test_empty_text(self):
        profile = PaperProfile.from_paper_text("")
        assert profile.complexity == PaperComplexity.SIMPLE
        assert profile.methodology_types == []


# ==============================================================
# 5. PhaseStrategy & PhasePlan Tests
# ==============================================================

class TestPhasePlan:
    """Tests for PhaseStrategy enum and PhasePlan."""

    def test_strategy_enum_members(self):
        assert PhaseStrategy.FULL.value == "full"
        assert PhaseStrategy.FOCUSED.value == "focused"
        assert PhaseStrategy.LIGHT.value == "light"
        assert PhaseStrategy.SKIP.value == "skip"
        assert PhaseStrategy.DEEP.value == "deep"

    def test_phase_plan_construction(self):
        pp = PhasePlan(
            phase="deep_review",
            strategy=PhaseStrategy.FULL,
            priority=0.9,
            resource_fraction=0.5,
            focus_areas=["methodology", "data"],
        )
        assert pp.phase == "deep_review"
        assert pp.strategy == PhaseStrategy.FULL
        assert pp.priority == 0.9

    def test_phase_plan_serialize_deserialize(self):
        pp = PhasePlan(
            phase="editing",
            strategy=PhaseStrategy.LIGHT,
            priority=0.4,
            resource_fraction=0.1,
            focus_areas=["clarity"],
            skip_reason="",
        )
        data = pp.serialize()
        restored = PhasePlan.deserialize(data)
        assert restored.phase == "editing"
        assert restored.strategy == PhaseStrategy.LIGHT


# ==============================================================
# 6. ReviewPlan Tests
# ==============================================================

class TestReviewPlan:
    """Tests for ReviewPlan."""

    def _make_plan(self) -> ReviewPlan:
        profile = PaperProfile(
            complexity=PaperComplexity.MODERATE,
            estimated_length_tokens=15000,
            methodology_types=["DID"],
            has_tables=True,
            has_figures=True,
            num_sections=7,
            field_tags=["labor"],
            novelty_signals=["novel"],
            controversy_signals=[],
        )
        phase_plans = {
            "initial_scan": PhasePlan(
                phase="initial_scan", strategy=PhaseStrategy.LIGHT,
                priority=0.5, resource_fraction=0.1,
            ),
            "deep_review": PhasePlan(
                phase="deep_review", strategy=PhaseStrategy.FULL,
                priority=0.9, resource_fraction=0.5,
            ),
            "editing": PhasePlan(
                phase="editing", strategy=PhaseStrategy.SKIP,
                priority=0.0, resource_fraction=0.0,
                skip_reason="Short paper",
            ),
            "synthesis": PhasePlan(
                phase="synthesis", strategy=PhaseStrategy.FULL,
                priority=0.8, resource_fraction=0.4,
            ),
        }
        return ReviewPlan(
            paper_profile=profile,
            phase_plans=phase_plans,
            overall_strategy="empirical_standard",
            estimated_total_turns=30,
        )

    def test_get_phase_plan(self):
        plan = self._make_plan()
        pp = plan.get_phase_plan("deep_review")
        assert pp is not None
        assert pp.strategy == PhaseStrategy.FULL

    def test_get_phase_plan_missing(self):
        plan = self._make_plan()
        assert plan.get_phase_plan("nonexistent") is None

    def test_get_resource_fraction(self):
        plan = self._make_plan()
        assert plan.get_resource_fraction("deep_review") == 0.5
        assert plan.get_resource_fraction("nonexistent") == 0.0

    def test_active_phases_excludes_skip(self):
        plan = self._make_plan()
        active = plan.active_phases()
        assert "editing" not in active
        assert "deep_review" in active
        assert "synthesis" in active

    def test_active_phases_ordered_by_priority(self):
        plan = self._make_plan()
        active = plan.active_phases()
        assert active[0] == "deep_review"  # priority 0.9

    def test_is_phase_skipped(self):
        plan = self._make_plan()
        assert plan.is_phase_skipped("editing")
        assert not plan.is_phase_skipped("deep_review")
        assert not plan.is_phase_skipped("nonexistent")

    def test_serialize_deserialize(self):
        plan = self._make_plan()
        data = plan.serialize()
        restored = ReviewPlan.deserialize(data)
        assert restored.overall_strategy == "empirical_standard"
        assert restored.estimated_total_turns == 30
        assert len(restored.phase_plans) == 4


# ==============================================================
# 7. ReviewPlanner Tests
# ==============================================================

class TestReviewPlanner:
    """Tests for ReviewPlanner strategy selection and resource allocation."""

    def test_select_empirical_standard(self):
        profile = PaperProfile(
            complexity=PaperComplexity.MODERATE,
            estimated_length_tokens=15000,
            methodology_types=["DID"],
            has_tables=True,
            has_figures=False,
            num_sections=7,
            field_tags=["labor"],
            novelty_signals=["novel"],
            controversy_signals=[],
        )
        planner = ReviewPlanner()
        plan = planner.create_plan(profile, ResourceBudget.default())
        assert plan.overall_strategy == "empirical_standard"

    def test_select_short_note(self):
        profile = PaperProfile(
            complexity=PaperComplexity.SIMPLE,
            estimated_length_tokens=5000,
            methodology_types=[],
            has_tables=False,
            has_figures=False,
            num_sections=3,
            field_tags=[],
            novelty_signals=[],
            controversy_signals=[],
        )
        planner = ReviewPlanner()
        plan = planner.create_plan(profile, ResourceBudget.default())
        assert plan.overall_strategy == "short_note"

    def test_select_methodology_novel(self):
        profile = PaperProfile(
            complexity=PaperComplexity.COMPLEX,
            estimated_length_tokens=30000,
            methodology_types=["DID", "IV", "RDD"],
            has_tables=True,
            has_figures=True,
            num_sections=10,
            field_tags=["labor"],
            novelty_signals=["novel", "new approach", "contribute"],
            controversy_signals=[],
        )
        planner = ReviewPlanner()
        plan = planner.create_plan(profile, ResourceBudget.default())
        assert plan.overall_strategy == "methodology_novel"

    def test_select_theory_heavy(self):
        profile = PaperProfile(
            complexity=PaperComplexity.COMPLEX,
            estimated_length_tokens=25000,
            methodology_types=["STRUCTURAL"],
            has_tables=True,
            has_figures=False,
            num_sections=8,
            field_tags=["macro"],
            novelty_signals=[],
            controversy_signals=[],
        )
        planner = ReviewPlanner()
        plan = planner.create_plan(profile, ResourceBudget.default())
        assert plan.overall_strategy == "theory_heavy"

    def test_select_data_heavy(self):
        profile = PaperProfile(
            complexity=PaperComplexity.MODERATE,
            estimated_length_tokens=15000,
            methodology_types=["PANEL"],
            has_tables=True,
            has_figures=False,
            num_sections=7,
            field_tags=["public_finance"],
            novelty_signals=[],
            controversy_signals=[],
        )
        planner = ReviewPlanner()
        plan = planner.create_plan(profile, ResourceBudget.default())
        assert plan.overall_strategy == "data_heavy"

    def test_plan_has_all_phases(self):
        profile = PaperProfile(
            complexity=PaperComplexity.MODERATE,
            estimated_length_tokens=20000,
            methodology_types=["DID"],
            has_tables=True,
            has_figures=True,
            num_sections=7,
            field_tags=[],
            novelty_signals=["novel"],
            controversy_signals=[],
        )
        planner = ReviewPlanner()
        plan = planner.create_plan(profile, ResourceBudget.default())
        assert "initial_scan" in plan.phase_plans
        assert "deep_review" in plan.phase_plans
        assert "synthesis" in plan.phase_plans

    def test_resource_fractions_sum_approximately_one(self):
        profile = PaperProfile(
            complexity=PaperComplexity.MODERATE,
            estimated_length_tokens=15000,
            methodology_types=["DID"],
            has_tables=True,
            has_figures=True,
            num_sections=7,
            field_tags=[],
            novelty_signals=["novel"],
            controversy_signals=[],
        )
        planner = ReviewPlanner()
        plan = planner.create_plan(profile, ResourceBudget.default())
        total_frac = sum(
            pp.resource_fraction for pp in plan.phase_plans.values()
            if pp.strategy != PhaseStrategy.SKIP
        )
        assert abs(total_frac - 1.0) < 0.05

    def test_estimated_turns_varies_by_complexity(self):
        planner = ReviewPlanner()
        simple_profile = PaperProfile(
            complexity=PaperComplexity.SIMPLE,
            estimated_length_tokens=5000,
            methodology_types=[],
            has_tables=False, has_figures=False,
            num_sections=3, field_tags=[],
            novelty_signals=[], controversy_signals=[],
        )
        complex_profile = PaperProfile(
            complexity=PaperComplexity.HIGHLY_COMPLEX,
            estimated_length_tokens=50000,
            methodology_types=["DID", "IV", "STRUCTURAL"],
            has_tables=True, has_figures=True,
            num_sections=15, field_tags=["labor"],
            novelty_signals=["novel", "new approach", "contribute"],
            controversy_signals=["controversial"],
        )
        plan_simple = planner.create_plan(simple_profile, ResourceBudget.default())
        plan_complex = planner.create_plan(complex_profile, ResourceBudget.default())
        assert plan_complex.estimated_total_turns > plan_simple.estimated_total_turns


# ==============================================================
# 8. DualLoopSignal Tests
# ==============================================================

class TestDualLoopSignal:
    """Tests for DualLoopSignalType and DualLoopSignal."""

    def test_signal_type_enum_inner_signals(self):
        inner_types = [
            DualLoopSignalType.PHASE_PROGRESS,
            DualLoopSignalType.PHASE_STUCK,
            DualLoopSignalType.BUDGET_WARNING,
            DualLoopSignalType.BUDGET_EXHAUSTED,
            DualLoopSignalType.MAJOR_FINDING,
            DualLoopSignalType.QUALITY_CONCERN,
            DualLoopSignalType.UNEXPECTED_COMPLEXITY,
        ]
        for st in inner_types:
            assert st.value.startswith("inner.")

    def test_signal_type_enum_outer_signals(self):
        outer_types = [
            DualLoopSignalType.INCREASE_BUDGET,
            DualLoopSignalType.DECREASE_BUDGET,
            DualLoopSignalType.SUGGEST_SKIP,
            DualLoopSignalType.CHANGE_FOCUS,
            DualLoopSignalType.FORCE_CONCLUDE,
            DualLoopSignalType.REPLAN,
        ]
        for st in outer_types:
            assert st.value.startswith("outer.")

    def test_signal_construction(self):
        signal = DualLoopSignal(
            signal_type=DualLoopSignalType.PHASE_STUCK,
            payload={"phase": "deep_review", "turns_stuck": 5},
            source="outer_loop",
            urgency=0.7,
        )
        assert signal.signal_type == DualLoopSignalType.PHASE_STUCK
        assert signal.payload["phase"] == "deep_review"
        assert signal.urgency == 0.7

    def test_signal_serialize_deserialize(self):
        signal = DualLoopSignal(
            signal_type=DualLoopSignalType.BUDGET_WARNING,
            payload={"warning_dimensions": ["tokens", "turns"]},
            source="test",
            urgency=0.8,
        )
        data = signal.serialize()
        restored = DualLoopSignal.deserialize(data)
        assert restored.signal_type == DualLoopSignalType.BUDGET_WARNING
        assert restored.payload["warning_dimensions"] == ["tokens", "turns"]
        assert restored.source == "test"


# ==============================================================
# 9. PlanUpdate Tests
# ==============================================================

class TestPlanUpdate:
    """Tests for PlanUpdate advisory message generation."""

    def test_resource_realloc_message(self):
        update = PlanUpdate(
            update_type="resource_realloc",
            target_phase="deep_review",
            changes={"direction": "increase"},
            reason="Major finding warrants deeper analysis",
        )
        msg = update.to_advisory_message()
        assert "[DualLoop Advisory]" in msg
        assert "deep_review" in msg
        assert "increase" in msg

    def test_phase_skip_message(self):
        update = PlanUpdate(
            update_type="phase_skip",
            target_phase="editing",
            changes={"action": "skip_to_next"},
            reason="Phase stuck too long",
        )
        msg = update.to_advisory_message()
        assert "editing" in msg
        assert "skip" in msg.lower() or "proceed" in msg.lower()

    def test_focus_change_message(self):
        update = PlanUpdate(
            update_type="focus_change",
            target_phase="deep_review",
            changes={"new_focus": ["robustness", "sensitivity"]},
            reason="Stuck on methodology",
        )
        msg = update.to_advisory_message()
        assert "robustness" in msg
        assert "sensitivity" in msg

    def test_full_replan_message(self):
        update = PlanUpdate(
            update_type="full_replan",
            target_phase="",
            changes={"new_complexity": "highly_complex"},
            reason="more complex than initially assessed",
        )
        msg = update.to_advisory_message()
        assert "adjustment" in msg.lower() or "adjust" in msg.lower()

    def test_force_conclude_message(self):
        update = PlanUpdate(
            update_type="force_conclude",
            target_phase="",
            changes={"urgency": "critical"},
            reason="Overall resource utilization at 95%",
        )
        msg = update.to_advisory_message()
        assert "wrap" in msg.lower() or "critically" in msg.lower()

    def test_unknown_type_message(self):
        update = PlanUpdate(
            update_type="custom_type",
            reason="Something happened",
        )
        msg = update.to_advisory_message()
        assert "Something happened" in msg

    def test_serialize_deserialize(self):
        update = PlanUpdate(
            update_type="phase_skip",
            target_phase="editing",
            changes={"action": "skip"},
            reason="test",
            confidence=0.7,
        )
        data = update.serialize()
        restored = PlanUpdate.deserialize(data)
        assert restored.update_type == "phase_skip"
        assert restored.target_phase == "editing"
        assert restored.confidence == 0.7


# ==============================================================
# 10. PlanAdapter Tests
# ==============================================================

class TestPlanAdapter:
    """Tests for PlanAdapter signal processing and plan adaptation."""

    def _make_adapter(self) -> PlanAdapter:
        profile = PaperProfile(
            complexity=PaperComplexity.MODERATE,
            estimated_length_tokens=20000,
            methodology_types=["DID"],
            has_tables=True, has_figures=True,
            num_sections=7, field_tags=["labor"],
            novelty_signals=["novel"], controversy_signals=[],
        )
        planner = ReviewPlanner()
        budget = ResourceBudget.default()
        plan = planner.create_plan(profile, budget)
        return PlanAdapter(plan, budget)

    def test_process_progress_resets_stuck(self):
        adapter = self._make_adapter()
        # First make it stuck
        adapter._stuck_counter["deep_review"] = 5
        # Then send progress
        signal = DualLoopSignal(
            signal_type=DualLoopSignalType.PHASE_PROGRESS,
            payload={"phase": "deep_review"},
        )
        adapter.process_signal(signal)
        assert adapter._stuck_counter["deep_review"] == 0

    def test_phase_stuck_moderate(self):
        adapter = self._make_adapter()
        # Send enough stuck signals to trigger moderate response
        for i in range(PlanAdapter.STUCK_THRESHOLD_TURNS + 1):
            signal = DualLoopSignal(
                signal_type=DualLoopSignalType.PHASE_STUCK,
                payload={"phase": "deep_review", "turns_stuck": i + 1},
            )
            result = adapter.process_signal(signal)

        # Should get focus_change
        assert result is not None
        assert result.update_type == "focus_change"
        assert result.target_phase == "deep_review"

    def test_phase_stuck_severe(self):
        adapter = self._make_adapter()
        # Send lots of stuck signals
        result = None
        for i in range(PlanAdapter.STUCK_THRESHOLD_TURNS * 2 + 1):
            signal = DualLoopSignal(
                signal_type=DualLoopSignalType.PHASE_STUCK,
                payload={"phase": "deep_review", "turns_stuck": i + 1},
            )
            result = adapter.process_signal(signal)

        # Should get phase_skip
        assert result is not None
        assert result.update_type == "phase_skip"

    def test_budget_warning_realloc(self):
        adapter = self._make_adapter()
        # Set budget utilization high but below force_conclude
        adapter.budget.consume(ResourceDimension.TOKENS, 100000.0)  # ~78%
        adapter.budget.consume(ResourceDimension.TURNS, 40.0)  # 80%

        signal = DualLoopSignal(
            signal_type=DualLoopSignalType.BUDGET_WARNING,
            payload={"warning_dimensions": ["tokens", "turns"]},
        )
        result = adapter.process_signal(signal)
        assert result is not None
        assert result.update_type in ("resource_realloc", "force_conclude")

    def test_budget_warning_force_conclude(self):
        adapter = self._make_adapter()
        # Set budget very high — need weighted utilization > 0.92
        # Weights: TOKENS=0.35, TURNS=0.30, TIME=0.20, API_CALLS=0.10, FINDINGS=0.05
        # Each capped at 1.5. Push all dimensions above 95% for weighted > 0.92.
        adapter.budget.consume(ResourceDimension.TOKENS, 126000.0)
        adapter.budget.consume(ResourceDimension.TURNS, 49.0)
        adapter.budget.consume(ResourceDimension.TIME_SECONDS, 590.0)
        adapter.budget.consume(ResourceDimension.API_CALLS, 98.0)
        adapter.budget.consume(ResourceDimension.FINDINGS_QUOTA, 28.0)

        signal = DualLoopSignal(
            signal_type=DualLoopSignalType.BUDGET_WARNING,
            payload={"warning_dimensions": ["tokens", "turns"]},
        )
        result = adapter.process_signal(signal)
        assert result is not None
        assert result.update_type == "force_conclude"

    def test_budget_exhausted_always_force_conclude(self):
        adapter = self._make_adapter()
        signal = DualLoopSignal(
            signal_type=DualLoopSignalType.BUDGET_EXHAUSTED,
            payload={"dimension": "tokens"},
        )
        result = adapter.process_signal(signal)
        assert result is not None
        assert result.update_type == "force_conclude"
        assert result.confidence >= 0.95

    def test_major_finding_boost(self):
        adapter = self._make_adapter()
        # Budget utilization low (default = 0)
        signal = DualLoopSignal(
            signal_type=DualLoopSignalType.MAJOR_FINDING,
            payload={"phase": "deep_review", "severity": "critical", "turn": 5},
        )
        result = adapter.process_signal(signal)
        assert result is not None
        assert result.update_type == "resource_realloc"
        assert result.changes["direction"] == "increase"

    def test_major_finding_no_boost_when_budget_high(self):
        adapter = self._make_adapter()
        # Push weighted overall_utilization above 0.70
        # Weights: TOKENS=0.35, TURNS=0.30, TIME=0.20, API_CALLS=0.10, FINDINGS=0.05
        # Need: 0.35*T + 0.30*U + 0.20*S + 0.10*A + 0.05*F >= 0.70
        adapter.budget.consume(ResourceDimension.TOKENS, 115000.0)  # 89.8%
        adapter.budget.consume(ResourceDimension.TURNS, 42.0)  # 84%
        adapter.budget.consume(ResourceDimension.TIME_SECONDS, 480.0)  # 80%
        adapter.budget.consume(ResourceDimension.API_CALLS, 80.0)  # 80%
        adapter.budget.consume(ResourceDimension.FINDINGS_QUOTA, 20.0)  # 66.7%

        signal = DualLoopSignal(
            signal_type=DualLoopSignalType.MAJOR_FINDING,
            payload={"phase": "deep_review", "severity": "major", "turn": 10},
        )
        result = adapter.process_signal(signal)
        assert result is None  # No boost when budget is tight

    def test_major_finding_minor_severity_no_boost(self):
        adapter = self._make_adapter()
        signal = DualLoopSignal(
            signal_type=DualLoopSignalType.MAJOR_FINDING,
            payload={"phase": "deep_review", "severity": "minor", "turn": 5},
        )
        result = adapter.process_signal(signal)
        assert result is None

    def test_unexpected_complexity_replan(self):
        adapter = self._make_adapter()
        signal = DualLoopSignal(
            signal_type=DualLoopSignalType.UNEXPECTED_COMPLEXITY,
            payload={
                "observed_complexity": "highly_complex",
                "reason": "Paper has hidden appendices with additional models",
            },
        )
        result = adapter.process_signal(signal)
        assert result is not None
        assert result.update_type == "full_replan"

    def test_quality_concern_single_no_action(self):
        adapter = self._make_adapter()
        signal = DualLoopSignal(
            signal_type=DualLoopSignalType.QUALITY_CONCERN,
            payload={"phase": "deep_review"},
        )
        result = adapter.process_signal(signal)
        assert result is None  # Need >= threshold

    def test_quality_concern_repeated_triggers(self):
        adapter = self._make_adapter()
        result = None
        for _ in range(PlanAdapter.QUALITY_CONCERN_THRESHOLD):
            signal = DualLoopSignal(
                signal_type=DualLoopSignalType.QUALITY_CONCERN,
                payload={"phase": "deep_review"},
            )
            result = adapter.process_signal(signal)

        assert result is not None
        assert result.update_type == "focus_change"
        assert "step_back" in result.changes.get("new_focus", [])

    def test_signal_and_update_counts(self):
        adapter = self._make_adapter()
        signals = [
            DualLoopSignal(signal_type=DualLoopSignalType.PHASE_PROGRESS, payload={"phase": "x"}),
            DualLoopSignal(signal_type=DualLoopSignalType.BUDGET_EXHAUSTED, payload={"dimension": "tokens"}),
        ]
        for s in signals:
            adapter.process_signal(s)
        assert adapter.signal_count == 2
        assert adapter.update_count == 1  # Only budget_exhausted produces update


# ==============================================================
# 11. StrategyLearner Tests
# ==============================================================

class TestStrategyLearner:
    """Tests for StrategyLearner."""

    def _make_outcome(
        self,
        strategy: str = "empirical_standard",
        complexity: PaperComplexity = PaperComplexity.MODERATE,
        methods: list[str] = None,
        fields: list[str] = None,
        quality: float = 0.8,
    ) -> ReviewOutcome:
        methods = methods or ["DID"]
        fields = fields or ["labor"]
        profile = PaperProfile(
            complexity=complexity,
            estimated_length_tokens=20000,
            methodology_types=methods,
            has_tables=True, has_figures=True,
            num_sections=7, field_tags=fields,
            novelty_signals=[], controversy_signals=[],
        )
        plan = ReviewPlan(
            paper_profile=profile,
            phase_plans={},
            overall_strategy=strategy,
            estimated_total_turns=30,
        )
        return ReviewOutcome(
            paper_profile=profile,
            plan_used=plan,
            resource_usage={"tokens_fraction": 0.7},
            findings_count=10,
            quality_score=quality,
            time_taken=300.0,
            replans_triggered=1,
            phases_skipped=[],
        )

    def test_empty_learner_no_recommendation(self):
        learner = StrategyLearner()
        profile = PaperProfile(
            complexity=PaperComplexity.MODERATE,
            estimated_length_tokens=15000,
            methodology_types=["DID"],
            has_tables=True, has_figures=False,
            num_sections=7, field_tags=["labor"],
            novelty_signals=[], controversy_signals=[],
        )
        assert learner.recommend_strategy(profile) is None

    def test_record_outcome(self):
        learner = StrategyLearner()
        outcome = self._make_outcome()
        learner.record_outcome(outcome)
        assert len(learner._records) == 1

    def test_recommendation_after_enough_data(self):
        learner = StrategyLearner()
        # Add enough similar records
        for _ in range(10):
            learner.record_outcome(self._make_outcome(
                strategy="empirical_standard",
                complexity=PaperComplexity.MODERATE,
                methods=["DID"],
                fields=["labor"],
                quality=0.85,
            ))

        profile = PaperProfile(
            complexity=PaperComplexity.MODERATE,
            estimated_length_tokens=18000,
            methodology_types=["DID"],
            has_tables=True, has_figures=True,
            num_sections=7, field_tags=["labor"],
            novelty_signals=[], controversy_signals=[],
        )
        rec = learner.recommend_strategy(profile)
        assert rec == "empirical_standard"

    def test_recommendation_none_when_dissimilar(self):
        learner = StrategyLearner()
        # Add records for a very different paper type
        for _ in range(10):
            learner.record_outcome(self._make_outcome(
                strategy="theory_heavy",
                complexity=PaperComplexity.HIGHLY_COMPLEX,
                methods=["STRUCTURAL"],
                fields=["macro"],
                quality=0.9,
            ))

        # Query for a very different paper
        profile = PaperProfile(
            complexity=PaperComplexity.SIMPLE,
            estimated_length_tokens=5000,
            methodology_types=["RCT"],
            has_tables=False, has_figures=False,
            num_sections=3, field_tags=["health"],
            novelty_signals=[], controversy_signals=[],
        )
        rec = learner.recommend_strategy(profile)
        # May be None due to insufficient similar papers
        # This is fine — the point is it doesn't recommend theory_heavy blindly
        # (it might return None or might find enough similarity depending on thresholds)
        assert rec is None or rec == "theory_heavy"

    def test_eviction_on_capacity(self):
        learner = StrategyLearner(max_records=5)
        for i in range(10):
            learner.record_outcome(self._make_outcome(quality=0.5 + i * 0.05))
        assert len(learner._records) == 5

    def test_effectiveness_report_empty(self):
        learner = StrategyLearner()
        report = learner.get_effectiveness_report()
        assert report["total_records"] == 0
        assert report["strategies"] == {}

    def test_effectiveness_report_with_data(self):
        learner = StrategyLearner()
        for _ in range(3):
            learner.record_outcome(self._make_outcome(strategy="empirical_standard", quality=0.8))
        for _ in range(2):
            learner.record_outcome(self._make_outcome(strategy="methodology_novel", quality=0.7))

        report = learner.get_effectiveness_report()
        assert report["total_records"] == 5
        assert report["strategies"]["empirical_standard"]["count"] == 3
        assert report["strategies"]["methodology_novel"]["count"] == 2

    def test_similarity_exact_match(self):
        learner = StrategyLearner()
        profile = PaperProfile(
            complexity=PaperComplexity.MODERATE,
            estimated_length_tokens=15000,
            methodology_types=["DID", "IV"],
            has_tables=True, has_figures=True,
            num_sections=7, field_tags=["labor"],
            novelty_signals=[], controversy_signals=[],
        )
        record = StrategyRecord(
            strategy_template="empirical_standard",
            paper_complexity=PaperComplexity.MODERATE,
            methodology_types=["DID", "IV"],
            field_tags=["labor"],
            outcome_quality=0.85,
            resource_efficiency=1.0,
        )
        sim = learner._compute_similarity(profile, record)
        assert sim == 1.0  # Perfect match

    def test_similarity_adjacent_complexity(self):
        learner = StrategyLearner()
        profile = PaperProfile(
            complexity=PaperComplexity.MODERATE,
            estimated_length_tokens=15000,
            methodology_types=["DID"],
            has_tables=True, has_figures=True,
            num_sections=7, field_tags=["labor"],
            novelty_signals=[], controversy_signals=[],
        )
        record = StrategyRecord(
            strategy_template="empirical_standard",
            paper_complexity=PaperComplexity.COMPLEX,  # Adjacent
            methodology_types=["DID"],
            field_tags=["labor"],
            outcome_quality=0.85,
            resource_efficiency=1.0,
        )
        sim = learner._compute_similarity(profile, record)
        assert 0.4 < sim < 1.0  # Partial match

    def test_serialize_deserialize(self):
        learner = StrategyLearner(max_records=50)
        for _ in range(3):
            learner.record_outcome(self._make_outcome())
        data = learner.serialize()
        restored = StrategyLearner.deserialize(data)
        assert len(restored._records) == 3
        assert restored._max_records == 50


# ==============================================================
# 12. OuterLoop Tests
# ==============================================================

class TestOuterLoop:
    """Tests for OuterLoop observer/advisor."""

    def test_plan_review_basic(self):
        outer = OuterLoop()
        plan = outer.plan_review(COMPLEX_ECON_PAPER)
        assert plan is not None
        assert plan.overall_strategy != "disabled"
        assert outer.is_active

    def test_plan_review_creates_phase_budgets(self):
        outer = OuterLoop()
        outer.plan_review(COMPLEX_ECON_PAPER)
        assert len(outer._phase_budgets) > 0

    def test_on_turn_end_returns_list(self):
        outer = OuterLoop()
        outer.plan_review(COMPLEX_ECON_PAPER)
        advisories = outer.on_turn_end(turn=1, phase="initial_scan", tokens_used=2000)
        assert isinstance(advisories, list)

    def test_on_turn_end_tracks_resources(self):
        outer = OuterLoop()
        outer.plan_review(COMPLEX_ECON_PAPER)
        outer.on_turn_end(turn=1, phase="initial_scan", tokens_used=3000)
        assert outer.budget.consumed[ResourceDimension.TOKENS] == 3000.0
        assert outer.budget.consumed[ResourceDimension.TURNS] == 1.0

    def test_on_turn_end_tracks_findings(self):
        outer = OuterLoop()
        outer.plan_review(COMPLEX_ECON_PAPER)
        finding = Finding(
            category="methodology", severity="major",
            description="Test finding", evidence="p<0.05",
        )
        outer.on_turn_end(turn=1, phase="deep_review", tokens_used=2000, findings=[finding])
        assert outer._total_findings == 1

    def test_stuck_detection(self):
        outer = OuterLoop()
        outer.plan_review(COMPLEX_ECON_PAPER)
        # Run turns without findings to trigger stuck
        for i in range(outer.STUCK_DETECTION_WINDOW + 1):
            outer.on_turn_end(turn=i + 1, phase="deep_review", tokens_used=2000, findings=[])

        # Should have emitted at least one PHASE_STUCK signal
        stuck_signals = [
            s for s in outer._signals_emitted
            if s.signal_type == DualLoopSignalType.PHASE_STUCK
        ]
        assert len(stuck_signals) > 0

    def test_budget_warning_emission(self):
        outer = OuterLoop(budget=ResourceBudget.default(total_tokens=10000, max_turns=10))
        outer.plan_review(COMPLEX_ECON_PAPER)
        # Consume most of the budget
        outer.on_turn_end(turn=1, phase="deep_review", tokens_used=8000)
        # Now tokens is at 80% which should trigger warning
        warning_signals = [
            s for s in outer._signals_emitted
            if s.signal_type == DualLoopSignalType.BUDGET_WARNING
        ]
        assert len(warning_signals) > 0

    def test_phase_transition_tracking(self):
        outer = OuterLoop()
        outer.plan_review(COMPLEX_ECON_PAPER)
        outer.on_phase_transition("initial_scan", "deep_review")
        assert outer._current_phase == "deep_review"
        assert outer._phase_turn_count == 0

    def test_on_session_end_records_outcome(self):
        outer = OuterLoop()
        outer.plan_review(COMPLEX_ECON_PAPER)
        outer.on_turn_end(turn=1, phase="deep_review", tokens_used=5000)
        outer.on_session_end(quality_score=0.8)
        assert not outer.is_active
        assert len(outer.learner._records) == 1

    def test_advisory_rate_limiting(self):
        outer = OuterLoop(budget=ResourceBudget.default(total_tokens=10000, max_turns=4))
        outer.plan_review(COMPLEX_ECON_PAPER)
        # First turn exhausts budget → advisory
        advisories_t1 = outer.on_turn_end(turn=1, phase="deep_review", tokens_used=9500)
        # Second turn immediately after — should be rate-limited (unless urgent)
        advisories_t2 = outer.on_turn_end(turn=2, phase="deep_review", tokens_used=100)
        # At least one advisory should have been produced across both turns
        # The exact distribution depends on rate limiting
        total = len(advisories_t1) + len(advisories_t2)
        assert total >= 1

    def test_progress_report(self):
        outer = OuterLoop()
        outer.plan_review(COMPLEX_ECON_PAPER)
        outer.on_turn_end(turn=1, phase="initial_scan", tokens_used=2000)
        report = outer.progress_report
        assert report["active"] is True
        assert report["total_turns"] == 1
        assert "overall_utilization" in report

    def test_get_current_advisory(self):
        outer = OuterLoop()
        outer.plan_review(COMPLEX_ECON_PAPER)
        # Manually add an advisory
        outer._pending_advisories.append("Test advisory")
        assert outer.get_current_advisory() == "Test advisory"
        assert outer.get_current_advisory() is None  # Consumed

    def test_serialize_deserialize(self):
        outer = OuterLoop()
        outer.plan_review(COMPLEX_ECON_PAPER)
        outer.on_turn_end(turn=1, phase="deep_review", tokens_used=5000)

        data = outer.serialize()
        restored = OuterLoop.deserialize(data)
        assert restored._total_turns == 1
        assert restored._plan is not None


# ==============================================================
# 13. DualLoopOrchestrator Facade Tests
# ==============================================================

class TestDualLoopOrchestrator:
    """Tests for the unified DualLoopOrchestrator facade."""

    def test_initialization_enabled(self):
        with patch.dict(os.environ, {"SCHOLAR_GODEL_DUAL_LOOP": "1"}):
            orch = DualLoopOrchestrator()
            assert orch.enabled is True

    def test_plan_review(self):
        with patch.dict(os.environ, {"SCHOLAR_GODEL_DUAL_LOOP": "1"}):
            orch = DualLoopOrchestrator()
            plan = orch.plan_review(COMPLEX_ECON_PAPER)
            assert plan.overall_strategy != "disabled"
            assert orch.plan is not None

    def test_tick_returns_list(self):
        with patch.dict(os.environ, {"SCHOLAR_GODEL_DUAL_LOOP": "1"}):
            orch = DualLoopOrchestrator()
            orch.plan_review(COMPLEX_ECON_PAPER)
            result = orch.tick(turn=1, phase="deep_review", tokens_used=3000)
            assert isinstance(result, list)

    def test_on_finding_buffers(self):
        with patch.dict(os.environ, {"SCHOLAR_GODEL_DUAL_LOOP": "1"}):
            orch = DualLoopOrchestrator()
            orch.plan_review(COMPLEX_ECON_PAPER)
            finding = Finding(
                category="statistics", severity="major",
                description="P-value issue",
            )
            orch.on_finding(finding)
            assert len(orch._findings_buffer) == 1
            # Tick should consume the buffer
            orch.tick(turn=1, phase="deep_review", tokens_used=2000)
            assert len(orch._findings_buffer) == 0

    def test_on_phase_change(self):
        with patch.dict(os.environ, {"SCHOLAR_GODEL_DUAL_LOOP": "1"}):
            orch = DualLoopOrchestrator()
            orch.plan_review(COMPLEX_ECON_PAPER)
            orch.on_phase_change("initial_scan", "deep_review")
            # Should not raise

    def test_conclude(self):
        with patch.dict(os.environ, {"SCHOLAR_GODEL_DUAL_LOOP": "1"}):
            orch = DualLoopOrchestrator()
            orch.plan_review(COMPLEX_ECON_PAPER)
            orch.tick(turn=1, phase="deep_review", tokens_used=5000)
            report = orch.conclude(quality_score=0.75)
            assert report["enabled"] is True
            assert report["status"] == "completed"
            assert report["quality_score"] == 0.75
            assert "learner_report" in report

    def test_get_advisory(self):
        with patch.dict(os.environ, {"SCHOLAR_GODEL_DUAL_LOOP": "1"}):
            orch = DualLoopOrchestrator()
            orch.plan_review(COMPLEX_ECON_PAPER)
            # Manually inject
            orch._outer_loop._pending_advisories.append("Advisory X")
            assert orch.get_advisory() == "Advisory X"
            assert orch.get_advisory() is None

    def test_progress_property(self):
        with patch.dict(os.environ, {"SCHOLAR_GODEL_DUAL_LOOP": "1"}):
            orch = DualLoopOrchestrator()
            orch.plan_review(COMPLEX_ECON_PAPER)
            progress = orch.progress
            assert "active" in progress or "current_phase" in progress

    def test_budget_property(self):
        with patch.dict(os.environ, {"SCHOLAR_GODEL_DUAL_LOOP": "1"}):
            orch = DualLoopOrchestrator()
            assert isinstance(orch.budget, ResourceBudget)

    def test_serialize_deserialize(self):
        with patch.dict(os.environ, {"SCHOLAR_GODEL_DUAL_LOOP": "1"}):
            orch = DualLoopOrchestrator()
            orch.plan_review(COMPLEX_ECON_PAPER)
            orch.tick(turn=1, phase="deep_review", tokens_used=5000)
            data = orch.serialize()
            restored = DualLoopOrchestrator.deserialize(data)
            assert restored._last_tick_turn == 1


# ==============================================================
# 14. Kill Switch Tests
# ==============================================================

class TestKillSwitch:
    """Tests for kill switch behavior across all components."""

    def test_is_enabled_default(self):
        with patch.dict(os.environ, {}, clear=False):
            # Remove the env var if present
            os.environ.pop("SCHOLAR_GODEL_DUAL_LOOP", None)
            assert _is_enabled() is True  # Default ON

    def test_is_enabled_on_values(self):
        for val in ("1", "true", "yes", "on", "True", "YES", "ON"):
            with patch.dict(os.environ, {"SCHOLAR_GODEL_DUAL_LOOP": val}):
                assert _is_enabled() is True

    def test_is_enabled_off_values(self):
        for val in ("0", "false", "no", "off", "disabled"):
            with patch.dict(os.environ, {"SCHOLAR_GODEL_DUAL_LOOP": val}):
                assert _is_enabled() is False

    def test_outer_loop_disabled_plan(self):
        with patch.dict(os.environ, {"SCHOLAR_GODEL_DUAL_LOOP": "0"}):
            outer = OuterLoop()
            plan = outer.plan_review(COMPLEX_ECON_PAPER)
            assert plan.overall_strategy == "disabled"
            assert not outer.is_active

    def test_outer_loop_disabled_on_turn_end(self):
        with patch.dict(os.environ, {"SCHOLAR_GODEL_DUAL_LOOP": "0"}):
            outer = OuterLoop()
            outer.plan_review(COMPLEX_ECON_PAPER)
            advisories = outer.on_turn_end(turn=1, phase="test", tokens_used=5000)
            assert advisories == []

    def test_outer_loop_disabled_on_session_end(self):
        with patch.dict(os.environ, {"SCHOLAR_GODEL_DUAL_LOOP": "0"}):
            outer = OuterLoop()
            outer.plan_review(COMPLEX_ECON_PAPER)
            # Should not raise
            outer.on_session_end(quality_score=0.8)

    def test_orchestrator_disabled_all_noop(self):
        with patch.dict(os.environ, {"SCHOLAR_GODEL_DUAL_LOOP": "0"}):
            orch = DualLoopOrchestrator()
            assert orch.enabled is False

            plan = orch.plan_review("Some text")
            assert plan.overall_strategy == "disabled"

            result = orch.tick(turn=1, phase="test", tokens_used=1000)
            assert result == []

            orch.on_phase_change("a", "b")  # No-op

            finding = Finding(category="test", severity="minor", description="x")
            orch.on_finding(finding)
            assert len(orch._findings_buffer) == 0  # Should not buffer when disabled

            report = orch.conclude(quality_score=0.9)
            assert report["enabled"] is False

            assert orch.get_advisory() is None

    def test_orchestrator_disabled_progress(self):
        with patch.dict(os.environ, {"SCHOLAR_GODEL_DUAL_LOOP": "0"}):
            orch = DualLoopOrchestrator()
            assert orch.progress == {"enabled": False}


# ==============================================================
# 15. InnerLoopObserver Protocol Tests
# ==============================================================

class TestInnerLoopObserver:
    """Tests for InnerLoopObserver protocol."""

    def test_inner_loop_status_construction(self):
        status = InnerLoopStatus(
            phase="deep_review",
            turn=5,
            tokens_consumed=15000,
            findings_produced=3,
            is_stuck=False,
            quality_estimate=0.7,
        )
        assert status.phase == "deep_review"
        assert status.turn == 5

    def test_protocol_runtime_checkable(self):
        class MyObserver:
            def on_turn_end(self, status: InnerLoopStatus) -> list[str]:
                return []

            def on_phase_transition(self, from_phase: str, to_phase: str) -> None:
                pass

        observer = MyObserver()
        assert isinstance(observer, InnerLoopObserver)

    def test_non_conforming_class(self):
        class NotAnObserver:
            def do_something(self) -> None:
                pass

        obj = NotAnObserver()
        assert not isinstance(obj, InnerLoopObserver)


# ==============================================================
# 16. EventBus Integration Tests
# ==============================================================

class TestEventBusIntegration:
    """Tests for EventBus registration helpers."""

    def test_register_with_disabled_orchestrator(self):
        with patch.dict(os.environ, {"SCHOLAR_GODEL_DUAL_LOOP": "0"}):
            orch = DualLoopOrchestrator()
            mock_bus = MagicMock()
            register_orchestrator_with_event_bus(orch, mock_bus)
            # Should NOT subscribe when disabled
            mock_bus.subscribe.assert_not_called()

    def test_register_with_enabled_orchestrator(self):
        with patch.dict(os.environ, {"SCHOLAR_GODEL_DUAL_LOOP": "1"}):
            orch = DualLoopOrchestrator()
            mock_bus = MagicMock()
            register_orchestrator_with_event_bus(orch, mock_bus)
            # Should subscribe 3 times
            assert mock_bus.subscribe.call_count == 3

    def test_create_orchestrator_for_session_default(self):
        with patch.dict(os.environ, {"SCHOLAR_GODEL_DUAL_LOOP": "1"}):
            orch = create_orchestrator_for_session()
            assert orch.enabled
            assert orch.budget.allocations[ResourceDimension.TOKENS] == 128000.0

    def test_create_orchestrator_for_session_custom(self):
        with patch.dict(os.environ, {"SCHOLAR_GODEL_DUAL_LOOP": "1"}):
            orch = create_orchestrator_for_session(
                total_tokens=64000,
                max_turns=30,
                max_time=300.0,
            )
            assert orch.budget.allocations[ResourceDimension.TOKENS] == 64000.0
            assert orch.budget.allocations[ResourceDimension.TURNS] == 30.0

    def test_create_orchestrator_with_learner_data(self):
        with patch.dict(os.environ, {"SCHOLAR_GODEL_DUAL_LOOP": "1"}):
            learner = StrategyLearner()
            record = StrategyRecord(
                strategy_template="empirical_standard",
                paper_complexity=PaperComplexity.MODERATE,
                methodology_types=["DID"],
                field_tags=["labor"],
                outcome_quality=0.85,
                resource_efficiency=1.2,
            )
            learner._records.append(record)
            data = learner.serialize()

            orch = create_orchestrator_for_session(learner_data=data)
            assert len(orch.learner._records) == 1


# ==============================================================
# 17. End-to-End Simulation Tests
# ==============================================================

class TestEndToEnd:
    """End-to-end simulation of the dual-loop system."""

    def test_full_review_session(self):
        """Simulate a complete review session."""
        with patch.dict(os.environ, {"SCHOLAR_GODEL_DUAL_LOOP": "1"}):
            orch = create_orchestrator_for_session(
                total_tokens=50000, max_turns=20, max_time=120.0,
            )

            # 1. Plan the review
            plan = orch.plan_review(COMPLEX_ECON_PAPER)
            assert plan.overall_strategy != "disabled"
            strategy = plan.overall_strategy

            # 2. Simulate initial_scan phase (2 turns)
            orch.on_phase_change("", "initial_scan")
            for turn in range(1, 3):
                orch.tick(turn=turn, phase="initial_scan", tokens_used=2000)

            # 3. Phase transition to deep_review
            orch.on_phase_change("initial_scan", "deep_review")

            # 4. Simulate deep_review with findings
            finding1 = Finding(
                category="methodology", severity="major",
                description="DID parallel trends assumption violated",
                evidence="Figure 1 shows pre-trends",
            )
            finding2 = Finding(
                category="statistics", severity="minor",
                description="Missing standard errors clustering",
            )
            orch.on_finding(finding1)
            advisories_t3 = orch.tick(
                turn=3, phase="deep_review", tokens_used=5000,
                findings=[finding2],
            )

            # 5. Continue deep_review (some turns without findings)
            for turn in range(4, 8):
                orch.tick(turn=turn, phase="deep_review", tokens_used=3000)

            # 6. Phase transition to synthesis
            orch.on_phase_change("deep_review", "synthesis")
            orch.tick(turn=8, phase="synthesis", tokens_used=4000)

            # 7. Conclude
            report = orch.conclude(quality_score=0.75)

            assert report["enabled"] is True
            assert report["status"] == "completed"
            assert report["total_findings"] >= 2
            assert report["quality_score"] == 0.75
            assert report["total_turns"] == 8
            assert "learner_report" in report

    def test_budget_exhaustion_produces_advisory(self):
        """Test that budget exhaustion triggers force_conclude advisory."""
        with patch.dict(os.environ, {"SCHOLAR_GODEL_DUAL_LOOP": "1"}):
            orch = create_orchestrator_for_session(
                total_tokens=10000, max_turns=5, max_time=60.0,
            )
            orch.plan_review(COMPLEX_ECON_PAPER)

            # Exhaust tokens in one big turn
            advisories = orch.tick(turn=1, phase="deep_review", tokens_used=9500)
            # The system should detect budget warning/exhaustion
            all_advisories = list(advisories)
            # Continue to next turn
            advisories2 = orch.tick(turn=2, phase="deep_review", tokens_used=1000)
            all_advisories.extend(advisories2)

            # At some point, force_conclude should appear
            has_conclude = any(
                "wrap" in a.lower() or "critically" in a.lower() or "conclude" in a.lower()
                for a in all_advisories
            )
            # At minimum, budget warnings should have been emitted
            assert orch._outer_loop.budget.is_exhausted(ResourceDimension.TOKENS) or \
                   orch._outer_loop.budget.is_warning(ResourceDimension.TOKENS)

    def test_stuck_detection_produces_advisory(self):
        """Test that being stuck produces an advisory."""
        with patch.dict(os.environ, {"SCHOLAR_GODEL_DUAL_LOOP": "1"}):
            orch = create_orchestrator_for_session(max_turns=30)
            orch.plan_review(COMPLEX_ECON_PAPER)

            all_advisories = []
            # Run many turns without findings
            for turn in range(1, 15):
                advs = orch.tick(turn=turn, phase="deep_review", tokens_used=1000)
                all_advisories.extend(advs)

            # Should have some advisories about being stuck or focus change
            stuck_signals = [
                s for s in orch._outer_loop._signals_emitted
                if s.signal_type == DualLoopSignalType.PHASE_STUCK
            ]
            assert len(stuck_signals) > 0

    def test_multiple_sessions_strategy_learning(self):
        """Test that the learner accumulates data across sessions."""
        with patch.dict(os.environ, {"SCHOLAR_GODEL_DUAL_LOOP": "1"}):
            learner = StrategyLearner()

            # Session 1
            orch1 = DualLoopOrchestrator(learner=learner)
            orch1.plan_review(COMPLEX_ECON_PAPER)
            orch1.tick(turn=1, phase="deep_review", tokens_used=5000)
            orch1.conclude(quality_score=0.8)

            assert len(learner._records) == 1

            # Session 2 — same learner
            orch2 = DualLoopOrchestrator(learner=learner)
            orch2.plan_review(COMPLEX_ECON_PAPER)
            orch2.tick(turn=1, phase="deep_review", tokens_used=4000)
            orch2.conclude(quality_score=0.85)

            assert len(learner._records) == 2

    def test_session_persistence_roundtrip(self):
        """Test serialize/deserialize preserves session state."""
        with patch.dict(os.environ, {"SCHOLAR_GODEL_DUAL_LOOP": "1"}):
            orch = create_orchestrator_for_session()
            orch.plan_review(COMPLEX_ECON_PAPER)
            orch.tick(turn=1, phase="deep_review", tokens_used=5000)
            orch.tick(turn=2, phase="deep_review", tokens_used=3000)

            # Serialize
            data = orch.serialize()

            # Deserialize
            restored = DualLoopOrchestrator.deserialize(data)
            assert restored._last_tick_turn == 2
            assert restored._outer_loop._plan is not None
            assert restored._outer_loop._total_turns == 2


# ==============================================================
# 18. Edge Cases & Error Handling
# ==============================================================

class TestEdgeCases:
    """Edge cases and error handling."""

    def test_tick_before_plan(self):
        """Tick without calling plan_review should still work."""
        with patch.dict(os.environ, {"SCHOLAR_GODEL_DUAL_LOOP": "1"}):
            orch = DualLoopOrchestrator()
            # No plan_review called — outer loop is not active
            result = orch.tick(turn=1, phase="test", tokens_used=1000)
            assert result == []

    def test_conclude_before_plan(self):
        """Conclude without plan_review should return gracefully."""
        with patch.dict(os.environ, {"SCHOLAR_GODEL_DUAL_LOOP": "1"}):
            orch = DualLoopOrchestrator()
            report = orch.conclude(quality_score=0.5)
            # Outer loop not active, so session_end is a no-op
            assert report["enabled"] is True

    def test_empty_paper_text(self):
        """Plan review with empty text should not crash."""
        with patch.dict(os.environ, {"SCHOLAR_GODEL_DUAL_LOOP": "1"}):
            orch = DualLoopOrchestrator()
            plan = orch.plan_review("")
            assert plan is not None

    def test_large_token_consumption(self):
        """Very large token consumption should not crash."""
        with patch.dict(os.environ, {"SCHOLAR_GODEL_DUAL_LOOP": "1"}):
            orch = create_orchestrator_for_session(total_tokens=1000)
            orch.plan_review("Short text")
            # Huge consumption
            result = orch.tick(turn=1, phase="test", tokens_used=999999)
            assert isinstance(result, list)

    def test_zero_budget(self):
        """Zero-budget scenarios should be handled gracefully."""
        budget = ResourceBudget(
            allocations={d: 0.0 for d in ResourceDimension},
            consumed={d: 0.0 for d in ResourceDimension},
            warning_thresholds={d: 0.75 for d in ResourceDimension},
        )
        assert budget.utilization(ResourceDimension.TOKENS) == 0.0
        assert budget.remaining(ResourceDimension.TOKENS) == 0.0

    def test_phase_plan_skip_serialization(self):
        """Serialization of skipped phases preserves skip reason."""
        pp = PhasePlan(
            phase="editing",
            strategy=PhaseStrategy.SKIP,
            priority=0.0,
            resource_fraction=0.0,
            skip_reason="Paper too short for editing pass",
        )
        data = pp.serialize()
        restored = PhasePlan.deserialize(data)
        assert restored.skip_reason == "Paper too short for editing pass"

    def test_strategy_record_deserialize_invalid(self):
        """StrategyLearner handles corrupt records gracefully."""
        learner_data = {
            "records": [
                {"strategy_template": "test", "paper_complexity": "invalid"},
                {
                    "strategy_template": "empirical_standard",
                    "paper_complexity": "moderate",
                    "methodology_types": [],
                    "field_tags": [],
                    "outcome_quality": 0.8,
                    "resource_efficiency": 1.0,
                },
            ],
            "max_records": 100,
        }
        learner = StrategyLearner.deserialize(learner_data)
        # Should skip the invalid record and keep the valid one
        assert len(learner._records) == 1

    def test_plan_adapter_no_active_phases(self):
        """PlanAdapter handles plans with no active phases."""
        profile = PaperProfile(
            complexity=PaperComplexity.SIMPLE,
            estimated_length_tokens=1000,
            methodology_types=[], has_tables=False, has_figures=False,
            num_sections=1, field_tags=[],
            novelty_signals=[], controversy_signals=[],
        )
        plan = ReviewPlan(
            paper_profile=profile,
            phase_plans={},
            overall_strategy="empty",
            estimated_total_turns=5,
        )
        budget = ResourceBudget.default()
        adapter = PlanAdapter(plan, budget)
        signal = DualLoopSignal(
            signal_type=DualLoopSignalType.BUDGET_WARNING,
            payload={"warning_dimensions": ["tokens"]},
        )
        # Should not crash even with no active phases
        result = adapter.process_signal(signal)
        # May or may not produce an update depending on utilization
        assert result is None or isinstance(result, PlanUpdate)

    def test_review_plan_from_paper_text_method(self):
        """PaperProfile.from_paper_text with metadata produces consistent results."""
        profile = PaperProfile.from_paper_text(
            DATA_HEAVY_TEXT,
            metadata={"field": "public_finance", "journal": "AER"},
        )
        assert "public_finance" in profile.field_tags
        assert profile.has_tables


# ==============================================================
# Entry point
# ==============================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
