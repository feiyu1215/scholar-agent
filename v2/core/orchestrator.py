"""
core/orchestrator.py — Phase 8: Dual-Loop Architecture (Hermes)

Adds an observation/advisory layer (OuterLoop) on top of the existing cognitive loop (InnerLoop).

Key design: OuterLoop is an OBSERVER, not a CONTROLLER.
- It watches events from the inner loop via EventBus
- It produces SUGGESTIONS (PlanUpdate) that get injected as system messages
- It tracks resource consumption and warns when budgets are nearing exhaustion
- It learns from historical outcomes which strategies work for which paper types

Architecture:
    OuterLoop ←─── EventBus ───→ InnerLoop (existing loop.py)
        │                              ↑
        └── PlanUpdate ── SignalDispatcher ──┘

Kill Switch: SCHOLAR_GODEL_DUAL_LOOP (default ON)
OFF behavior: Orchestrator methods become no-ops, zero overhead.

Integration points:
- EventBus (event_bus.py): Subscribes to TURN_ENDED, PHASE_TRANSITION,
  FINDING_ADDED, TOKEN_BUDGET_WARNING, DOOM_LOOP_DETECTED
- SignalDispatcher (signal_dispatcher.py): Submits advisory HarnessSignals
  at priority=3 (low) — never overrides safety or budget signals
- TokenBudgetManager (token_budget.py): Reads budget status for resource tracking
- PhaseFSM (phases.py): Reads phase state, never writes

Does NOT modify: loop.py, phases.py, event_bus.py, signal_dispatcher.py
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Protocol, runtime_checkable

from core.skills.base import Finding

logger = logging.getLogger(__name__)


# ==============================================================
# Kill Switch
# ==============================================================

def _is_enabled() -> bool:
    """Check whether the dual-loop system is active.

    Environment variable: SCHOLAR_GODEL_DUAL_LOOP
    Accepted ON values: "1", "true", "yes", "on" (case-insensitive)
    Default: ON (if variable is unset, dual-loop is enabled).
    """
    val = os.environ.get("SCHOLAR_GODEL_DUAL_LOOP", "1").strip().lower()
    return val in ("1", "true", "yes", "on")


# ==============================================================
# 1. Resource Management
# ==============================================================

class ResourceDimension(Enum):
    """Multi-dimensional resource tracking dimensions.

    Each dimension represents an independent consumable resource
    with its own allocation, consumption tracking, and warning threshold.
    """
    TOKENS = "tokens"
    TURNS = "turns"
    TIME_SECONDS = "time_seconds"
    API_CALLS = "api_calls"
    FINDINGS_QUOTA = "findings_quota"


@dataclass
class ResourceBudget:
    """Multi-dimensional resource budget for a review session.

    Each dimension tracks: allocated amount, consumed amount, and a
    warning threshold (expressed as a 0-1 ratio). When consumption
    crosses the threshold, warning signals are emitted to the outer loop.

    Thread safety: Not thread-safe. Designed for single-threaded cognitive loop.
    """

    allocations: dict[ResourceDimension, float] = field(default_factory=dict)
    consumed: dict[ResourceDimension, float] = field(default_factory=dict)
    warning_thresholds: dict[ResourceDimension, float] = field(default_factory=dict)

    @classmethod
    def default(
        cls,
        total_tokens: int = 128000,
        max_turns: int = 50,
        max_time: float = 600.0,
        max_api_calls: int = 100,
        max_findings: int = 30,
    ) -> ResourceBudget:
        """Create a default budget with standard allocations.

        Args:
            total_tokens: Maximum tokens for the entire session.
            max_turns: Maximum cognitive loop turns.
            max_time: Maximum wall-clock time in seconds.
            max_api_calls: Maximum LLM API calls.
            max_findings: Maximum findings before over-reporting warning.

        Returns:
            A fully initialized ResourceBudget.
        """
        allocations = {
            ResourceDimension.TOKENS: float(total_tokens),
            ResourceDimension.TURNS: float(max_turns),
            ResourceDimension.TIME_SECONDS: max_time,
            ResourceDimension.API_CALLS: float(max_api_calls),
            ResourceDimension.FINDINGS_QUOTA: float(max_findings),
        }
        consumed = {dim: 0.0 for dim in ResourceDimension}
        warning_thresholds = {
            ResourceDimension.TOKENS: 0.75,
            ResourceDimension.TURNS: 0.75,
            ResourceDimension.TIME_SECONDS: 0.60,
            ResourceDimension.API_CALLS: 0.75,
            ResourceDimension.FINDINGS_QUOTA: 0.80,
        }
        return cls(
            allocations=allocations,
            consumed=consumed,
            warning_thresholds=warning_thresholds,
        )

    def consume(self, dimension: ResourceDimension, amount: float) -> None:
        """Record consumption of a resource dimension.

        Args:
            dimension: Which resource was consumed.
            amount: How much was consumed (must be >= 0).
        """
        if amount < 0:
            logger.warning(
                "[ResourceBudget] Negative consumption ignored: %s = %.1f",
                dimension.value, amount,
            )
            return
        current = self.consumed.get(dimension, 0.0)
        self.consumed[dimension] = current + amount

    def remaining(self, dimension: ResourceDimension) -> float:
        """Get remaining allocation for a dimension.

        Returns:
            Remaining amount (may be negative if over-budget).
        """
        allocated = self.allocations.get(dimension, 0.0)
        used = self.consumed.get(dimension, 0.0)
        return allocated - used

    def utilization(self, dimension: ResourceDimension) -> float:
        """Get utilization ratio for a dimension.

        Returns:
            Fraction consumed (0.0 to 1.0+). May exceed 1.0 if over-budget.
        """
        allocated = self.allocations.get(dimension, 0.0)
        if allocated <= 0:
            return 0.0
        used = self.consumed.get(dimension, 0.0)
        return used / allocated

    def is_warning(self, dimension: ResourceDimension) -> bool:
        """Check if a dimension has crossed its warning threshold.

        Returns:
            True if utilization >= warning_threshold for this dimension.
        """
        threshold = self.warning_thresholds.get(dimension, 0.75)
        return self.utilization(dimension) >= threshold

    def is_exhausted(self, dimension: ResourceDimension) -> bool:
        """Check if a dimension is fully consumed (utilization >= 1.0).

        Returns:
            True if the dimension is at or over its allocation.
        """
        return self.utilization(dimension) >= 1.0

    def overall_utilization(self) -> float:
        """Compute weighted average utilization across all dimensions.

        Weights reflect importance:
            TOKENS: 0.35, TURNS: 0.30, TIME: 0.20, API_CALLS: 0.10, FINDINGS: 0.05

        Returns:
            Weighted utilization between 0.0 and 1.0+.
        """
        weights = {
            ResourceDimension.TOKENS: 0.35,
            ResourceDimension.TURNS: 0.30,
            ResourceDimension.TIME_SECONDS: 0.20,
            ResourceDimension.API_CALLS: 0.10,
            ResourceDimension.FINDINGS_QUOTA: 0.05,
        }
        total_weight = 0.0
        weighted_sum = 0.0
        for dim, weight in weights.items():
            if dim in self.allocations:
                weighted_sum += weight * min(self.utilization(dim), 1.5)
                total_weight += weight
        if total_weight <= 0:
            return 0.0
        return weighted_sum / total_weight

    def allocate_to_phase(self, phase: str, fraction: float) -> PhaseResourceBudget:
        """Create a sub-budget for a specific phase.

        Allocates a fraction of the REMAINING budget (not total).

        Args:
            phase: Phase name (e.g., "deep_review").
            fraction: Fraction of remaining budget to allocate (0.0-1.0).

        Returns:
            PhaseResourceBudget with allocated amounts.
        """
        fraction = max(0.0, min(1.0, fraction))
        token_alloc = int(self.remaining(ResourceDimension.TOKENS) * fraction)
        turn_alloc = max(1, int(self.remaining(ResourceDimension.TURNS) * fraction))
        time_alloc = self.remaining(ResourceDimension.TIME_SECONDS) * fraction
        return PhaseResourceBudget(
            phase=phase,
            token_budget=max(0, token_alloc),
            turn_budget=turn_alloc,
            time_budget=max(0.0, time_alloc),
        )

    def get_warning_dimensions(self) -> list[ResourceDimension]:
        """Get all dimensions currently in warning state."""
        return [dim for dim in ResourceDimension if self.is_warning(dim)]

    def get_exhausted_dimensions(self) -> list[ResourceDimension]:
        """Get all dimensions that are fully exhausted."""
        return [dim for dim in ResourceDimension if self.is_exhausted(dim)]

    def serialize(self) -> dict:
        """Serialize to a JSON-compatible dictionary."""
        return {
            "allocations": {dim.value: val for dim, val in self.allocations.items()},
            "consumed": {dim.value: val for dim, val in self.consumed.items()},
            "warning_thresholds": {
                dim.value: val for dim, val in self.warning_thresholds.items()
            },
        }

    @classmethod
    def deserialize(cls, data: dict) -> ResourceBudget:
        """Reconstruct from a serialized dictionary."""
        dim_map = {d.value: d for d in ResourceDimension}
        allocations = {
            dim_map[k]: v for k, v in data.get("allocations", {}).items()
            if k in dim_map
        }
        consumed = {
            dim_map[k]: v for k, v in data.get("consumed", {}).items()
            if k in dim_map
        }
        warning_thresholds = {
            dim_map[k]: v for k, v in data.get("warning_thresholds", {}).items()
            if k in dim_map
        }
        return cls(
            allocations=allocations,
            consumed=consumed,
            warning_thresholds=warning_thresholds,
        )


@dataclass
class PhaseResourceBudget:
    """Budget allocated to a specific review phase.

    Tracks token, turn, and time consumption within a single phase.
    Used by OuterLoop to detect over-budget phases and trigger replanning.

    Attributes:
        phase: Phase name identifier.
        token_budget: Tokens allocated to this phase.
        turn_budget: Turns allocated to this phase.
        time_budget: Wall-clock seconds allocated.
        consumed_tokens: Tokens consumed so far.
        consumed_turns: Turns consumed so far.
        consumed_time: Seconds consumed so far.
    """

    phase: str
    token_budget: int
    turn_budget: int
    time_budget: float
    consumed_tokens: int = 0
    consumed_turns: int = 0
    consumed_time: float = 0.0

    @property
    def token_remaining(self) -> int:
        """Tokens remaining in this phase's budget."""
        return max(0, self.token_budget - self.consumed_tokens)

    @property
    def turn_remaining(self) -> int:
        """Turns remaining in this phase's budget."""
        return max(0, self.turn_budget - self.consumed_turns)

    @property
    def is_over_budget(self) -> bool:
        """Whether this phase has exceeded any of its budgets.

        Note: token_budget=0 means unlimited (skip token check),
        consistent with BudgetPolicy.is_unlimited semantics.
        """
        token_exceeded = (self.consumed_tokens > self.token_budget) if self.token_budget > 0 else False
        turn_exceeded = (self.consumed_turns > self.turn_budget) if self.turn_budget > 0 else False
        time_exceeded = (self.consumed_time > self.time_budget) if self.time_budget > 0 else False
        return token_exceeded or turn_exceeded or time_exceeded

    @property
    def utilization(self) -> float:
        """Highest utilization ratio across all phase sub-dimensions."""
        ratios = []
        if self.token_budget > 0:
            ratios.append(self.consumed_tokens / self.token_budget)
        if self.turn_budget > 0:
            ratios.append(self.consumed_turns / self.turn_budget)
        if self.time_budget > 0:
            ratios.append(self.consumed_time / self.time_budget)
        return max(ratios) if ratios else 0.0

    def consume_turn(self, tokens: int, elapsed: float) -> None:
        """Record one turn of consumption in this phase.

        Args:
            tokens: Tokens consumed this turn.
            elapsed: Time elapsed this turn in seconds.
        """
        self.consumed_tokens += tokens
        self.consumed_turns += 1
        self.consumed_time += elapsed

    def serialize(self) -> dict:
        """Serialize to dictionary."""
        return {
            "phase": self.phase,
            "token_budget": self.token_budget,
            "turn_budget": self.turn_budget,
            "time_budget": self.time_budget,
            "consumed_tokens": self.consumed_tokens,
            "consumed_turns": self.consumed_turns,
            "consumed_time": self.consumed_time,
        }

    @classmethod
    def deserialize(cls, data: dict) -> PhaseResourceBudget:
        """Reconstruct from dictionary."""
        return cls(
            phase=data["phase"],
            token_budget=data["token_budget"],
            turn_budget=data["turn_budget"],
            time_budget=data["time_budget"],
            consumed_tokens=data.get("consumed_tokens", 0),
            consumed_turns=data.get("consumed_turns", 0),
            consumed_time=data.get("consumed_time", 0.0),
        )


# ==============================================================
# 2. Paper Profiling
# ==============================================================

class PaperComplexity(Enum):
    """Paper complexity classification for strategy selection.

    Maps to different resource allocation profiles:
        SIMPLE: Short papers, standard methods, clear structure.
        MODERATE: Medium length, some novel aspects.
        COMPLEX: Long papers, novel methods, multiple datasets.
        HIGHLY_COMPLEX: Very long, cutting-edge, controversial, multi-method.
    """
    SIMPLE = "simple"
    MODERATE = "moderate"
    COMPLEX = "complex"
    HIGHLY_COMPLEX = "highly_complex"


# --- Methodology Detection Patterns (compiled at module load) ---

_METHODOLOGY_PATTERNS: dict[str, re.Pattern] = {
    "DID": re.compile(
        r"\b(difference[\-\s]in[\-\s]difference|diff[\-\s]in[\-\s]diff|DiD|DD\b)",
        re.IGNORECASE,
    ),
    "IV": re.compile(
        r"\b(instrumental[\s\-]variable|2SLS|two[\-\s]stage[\s\-]least[\s\-]squares|IV[\s\-]estimation)\b",
        re.IGNORECASE,
    ),
    "RCT": re.compile(
        r"\b(randomized[\s\-]control(?:led)?[\s\-]trial|RCT|random[\s\-]assignment|field[\s\-]experiment)\b",
        re.IGNORECASE,
    ),
    "RDD": re.compile(
        r"\b(regression[\s\-]discontinuity|RDD|sharp[\s\-]discontinuity|fuzzy[\s\-]discontinuity)\b",
        re.IGNORECASE,
    ),
    "PSM": re.compile(
        r"\b(propensity[\s\-]score[\s\-]matching|PSM|matched[\s\-]sample)\b",
        re.IGNORECASE,
    ),
    "GMM": re.compile(
        r"\b(generalized[\s\-]method[\s\-]of[\s\-]moments|GMM|Arellano[\-\s]Bond)\b",
        re.IGNORECASE,
    ),
    "ML": re.compile(
        r"\b(machine[\s\-]learning|random[\s\-]forest|neural[\s\-]network|deep[\s\-]learning|LASSO|gradient[\s\-]boosting)\b",
        re.IGNORECASE,
    ),
    "STRUCTURAL": re.compile(
        r"\b(structural[\s\-]estimation|structural[\s\-]model|DSGE|discrete[\s\-]choice)\b",
        re.IGNORECASE,
    ),
    "PANEL": re.compile(
        r"\b(panel[\s\-]data|fixed[\s\-]effects|random[\s\-]effects|within[\-\s]estimator)\b",
        re.IGNORECASE,
    ),
    "EVENT_STUDY": re.compile(
        r"\b(event[\s\-]study|staggered[\s\-]adoption|pre[\-\s]trend)\b",
        re.IGNORECASE,
    ),
    "BUNCHING": re.compile(
        r"\b(bunching[\s\-]estimat|bunching[\s\-]design|notch)\b",
        re.IGNORECASE,
    ),
    "SYNTH_CONTROL": re.compile(
        r"\b(synthetic[\s\-]control|SCM|donor[\s\-]pool)\b",
        re.IGNORECASE,
    ),
}

# --- Novelty Signal Patterns ---
_NOVELTY_PATTERNS: list[re.Pattern] = [
    re.compile(r"\b(novel|new[\s\-]approach|first[\s\-]to|contribute|contribution)\b", re.IGNORECASE),
    re.compile(r"\b(we[\s\-]propose|we[\s\-]develop|our[\s\-]method)\b", re.IGNORECASE),
    re.compile(r"\b(extend|generalize|improve[\s\-]upon)\b", re.IGNORECASE),
]

# --- Controversy Signal Patterns ---
_CONTROVERSY_PATTERNS: list[re.Pattern] = [
    re.compile(r"\b(controversial|debate|contested|disagree|conflicting[\s\-]evidence)\b", re.IGNORECASE),
    re.compile(r"\b(challenge|overturn|contradict|refute|rebut)\b", re.IGNORECASE),
    re.compile(r"\b(policy[\s\-]implication|welfare[\s\-]effect|normative)\b", re.IGNORECASE),
]

# --- Field Detection Patterns ---
_FIELD_PATTERNS: dict[str, re.Pattern] = {
    "labor": re.compile(r"\b(labor|wage|employment|worker|job|unemployment|human[\s\-]capital)\b", re.IGNORECASE),
    "development": re.compile(r"\b(development|poverty|developing[\s\-]countr|microfinance|aid)\b", re.IGNORECASE),
    "trade": re.compile(r"\b(trade|tariff|export|import|gravity[\s\-]model|comparative[\s\-]advantage)\b", re.IGNORECASE),
    "health": re.compile(r"\b(health|mortality|hospital|patient|disease|medical)\b", re.IGNORECASE),
    "education": re.compile(r"\b(education|school|student|teacher|college|university)\b", re.IGNORECASE),
    "finance": re.compile(r"\b(finance|stock|bond|bank|credit|asset[\s\-]pric)\b", re.IGNORECASE),
    "macro": re.compile(r"\b(macroeconom|inflation|monetary[\s\-]policy|GDP|business[\s\-]cycle)\b", re.IGNORECASE),
    "public_finance": re.compile(r"\b(tax|taxation|public[\s\-]finance|fiscal|government[\s\-]spending)\b", re.IGNORECASE),
    "IO": re.compile(r"\b(industrial[\s\-]organization|market[\s\-]structure|competition|antitrust|merger)\b", re.IGNORECASE),
    "environmental": re.compile(r"\b(environment|climate|pollution|carbon|emission|renewable)\b", re.IGNORECASE),
}


@dataclass
class PaperProfile:
    """Profile of a paper's characteristics for strategy planning.

    Created via heuristic analysis of paper text. Used by ReviewPlanner
    to select strategy templates and allocate resources appropriately.

    Attributes:
        complexity: Overall complexity classification.
        estimated_length_tokens: Estimated token count of full paper.
        methodology_types: Detected methodology types (e.g., ["DID", "IV"]).
        has_tables: Whether the paper contains data tables.
        has_figures: Whether the paper contains figures/charts.
        num_sections: Number of detected sections/headings.
        field_tags: Detected research field tags.
        novelty_signals: Detected novelty claim keywords.
        controversy_signals: Detected controversy keywords.
    """

    complexity: PaperComplexity
    estimated_length_tokens: int
    methodology_types: list[str]
    has_tables: bool
    has_figures: bool
    num_sections: int
    field_tags: list[str]
    novelty_signals: list[str]
    controversy_signals: list[str]

    @classmethod
    def from_paper_text(cls, paper_text: str, metadata: dict | None = None) -> PaperProfile:
        """Profile a paper from its text content using regex heuristics.

        Analyzes the text to determine:
            1. Length/complexity classification
            2. Methodology types used
            3. Structural features (tables, figures, sections)
            4. Research field classification
            5. Novelty and controversy signals

        Args:
            paper_text: Full text of the paper (or substantial excerpt).
            metadata: Optional metadata dict (may contain 'field', 'journal', etc.).

        Returns:
            PaperProfile instance characterizing the paper.
        """
        metadata = metadata or {}
        text = paper_text or ""

        # --- Length estimation ---
        word_count = len(text.split())
        estimated_tokens = int(word_count * 1.3)

        # --- Section detection ---
        section_pattern = re.compile(
            r"^(?:\d+\.?\s+|#{1,3}\s+)[A-Z]",
            re.MULTILINE,
        )
        sections_found = section_pattern.findall(text)
        num_sections = max(len(sections_found), 1)

        # --- Table detection ---
        has_tables = bool(re.search(
            r"\b(Table\s+\d|TABLE\s+\d|\|[-\s:]+\|)",
            text,
        ))

        # --- Figure detection ---
        has_figures = bool(re.search(
            r"\b(Figure\s+\d|FIGURE\s+\d|Fig\.\s*\d)",
            text,
        ))

        # --- Methodology detection ---
        methodology_types: list[str] = []
        for meth_name, pattern in _METHODOLOGY_PATTERNS.items():
            if pattern.search(text):
                methodology_types.append(meth_name)

        # --- Field detection (require >= 3 mentions for confidence) ---
        field_tags: list[str] = []
        for field_name, pattern in _FIELD_PATTERNS.items():
            matches = pattern.findall(text)
            if len(matches) >= 3:
                field_tags.append(field_name)
        if "field" in metadata and metadata["field"] not in field_tags:
            field_tags.append(metadata["field"])

        # --- Novelty signals (focus on intro/abstract area) ---
        intro_area = text[:5000]
        novelty_signals: list[str] = []
        for pattern in _NOVELTY_PATTERNS:
            found = pattern.findall(intro_area)
            for match in found[:2]:
                sig = match if isinstance(match, str) else match[0]
                if sig.lower() not in [s.lower() for s in novelty_signals]:
                    novelty_signals.append(sig)
        novelty_signals = novelty_signals[:5]

        # --- Controversy signals (search full text) ---
        controversy_signals: list[str] = []
        for pattern in _CONTROVERSY_PATTERNS:
            found = pattern.findall(text)
            for match in found[:2]:
                sig = match if isinstance(match, str) else match[0]
                if sig.lower() not in [s.lower() for s in controversy_signals]:
                    controversy_signals.append(sig)
        controversy_signals = controversy_signals[:5]

        # --- Complexity classification ---
        complexity = cls._classify_complexity(
            estimated_tokens=estimated_tokens,
            num_methods=len(methodology_types),
            num_sections=num_sections,
            has_novelty=len(novelty_signals) > 0,
            has_controversy=len(controversy_signals) > 0,
        )

        return cls(
            complexity=complexity,
            estimated_length_tokens=estimated_tokens,
            methodology_types=methodology_types,
            has_tables=has_tables,
            has_figures=has_figures,
            num_sections=num_sections,
            field_tags=field_tags,
            novelty_signals=novelty_signals,
            controversy_signals=controversy_signals,
        )

    @staticmethod
    def _classify_complexity(
        estimated_tokens: int,
        num_methods: int,
        num_sections: int,
        has_novelty: bool,
        has_controversy: bool,
    ) -> PaperComplexity:
        """Classify paper complexity from extracted features.

        Scoring:
            Length: <8K=0, 8K-20K=1, 20K-40K=2, >40K=3
            Methods: 0=0, 1=1, 2=2, 3+=3
            Sections: <5=0, 5-8=1, 8-15=2, >15=3
            Novelty: +1
            Controversy: +1

        Bands: 0-2=SIMPLE, 3-5=MODERATE, 6-8=COMPLEX, 9+=HIGHLY_COMPLEX
        """
        score = 0

        if estimated_tokens < 8000:
            score += 0
        elif estimated_tokens < 20000:
            score += 1
        elif estimated_tokens < 40000:
            score += 2
        else:
            score += 3

        if num_methods == 0:
            score += 0
        elif num_methods == 1:
            score += 1
        elif num_methods == 2:
            score += 2
        else:
            score += 3

        if num_sections < 5:
            score += 0
        elif num_sections < 8:
            score += 1
        elif num_sections <= 15:
            score += 2
        else:
            score += 3

        if has_novelty:
            score += 1
        if has_controversy:
            score += 1

        if score <= 2:
            return PaperComplexity.SIMPLE
        elif score <= 5:
            return PaperComplexity.MODERATE
        elif score <= 8:
            return PaperComplexity.COMPLEX
        else:
            return PaperComplexity.HIGHLY_COMPLEX

    def serialize(self) -> dict:
        """Serialize to a JSON-compatible dictionary."""
        return {
            "complexity": self.complexity.value,
            "estimated_length_tokens": self.estimated_length_tokens,
            "methodology_types": self.methodology_types,
            "has_tables": self.has_tables,
            "has_figures": self.has_figures,
            "num_sections": self.num_sections,
            "field_tags": self.field_tags,
            "novelty_signals": self.novelty_signals,
            "controversy_signals": self.controversy_signals,
        }

    @classmethod
    def deserialize(cls, data: dict) -> PaperProfile:
        """Reconstruct from serialized dictionary."""
        return cls(
            complexity=PaperComplexity(data["complexity"]),
            estimated_length_tokens=data["estimated_length_tokens"],
            methodology_types=data.get("methodology_types", []),
            has_tables=data.get("has_tables", False),
            has_figures=data.get("has_figures", False),
            num_sections=data.get("num_sections", 1),
            field_tags=data.get("field_tags", []),
            novelty_signals=data.get("novelty_signals", []),
            controversy_signals=data.get("controversy_signals", []),
        )


# ==============================================================
# 3. Review Planning
# ==============================================================

class PhaseStrategy(Enum):
    """Strategy for handling a review phase.

    Determines depth of analysis and resource allocation within a phase.
    """
    FULL = "full"
    FOCUSED = "focused"
    LIGHT = "light"
    SKIP = "skip"
    DEEP = "deep"


@dataclass
class PhasePlan:
    """Plan for executing a single review phase.

    Attributes:
        phase: Phase name (matches phases.Phase enum values).
        strategy: How deeply to execute this phase.
        priority: Importance weight (0.0-1.0, higher = more important).
        resource_fraction: Fraction of total budget allocated.
        focus_areas: Specific aspects to focus on.
        skip_reason: Explanation if strategy is SKIP.
    """

    phase: str
    strategy: PhaseStrategy
    priority: float
    resource_fraction: float
    focus_areas: list[str] = field(default_factory=list)
    skip_reason: str = ""

    def serialize(self) -> dict:
        """Serialize to dictionary."""
        return {
            "phase": self.phase,
            "strategy": self.strategy.value,
            "priority": self.priority,
            "resource_fraction": self.resource_fraction,
            "focus_areas": self.focus_areas,
            "skip_reason": self.skip_reason,
        }

    @classmethod
    def deserialize(cls, data: dict) -> PhasePlan:
        """Reconstruct from dictionary."""
        return cls(
            phase=data["phase"],
            strategy=PhaseStrategy(data["strategy"]),
            priority=data["priority"],
            resource_fraction=data["resource_fraction"],
            focus_areas=data.get("focus_areas", []),
            skip_reason=data.get("skip_reason", ""),
        )


@dataclass
class ReviewPlan:
    """Complete review plan for a paper.

    Contains phase-level strategies, resource allocations, and overall approach.
    The plan is versioned — each adaptation/replan increments the version.

    Attributes:
        paper_profile: Analyzed paper characteristics.
        phase_plans: Mapping of phase name to its plan.
        overall_strategy: High-level strategy label (e.g., "balanced").
        estimated_total_turns: Estimated turns needed for complete review.
        created_at: Timestamp when plan was created.
        version: Plan version (increments on each replan).
    """

    paper_profile: PaperProfile
    phase_plans: dict[str, PhasePlan]
    overall_strategy: str
    estimated_total_turns: int
    created_at: float = field(default_factory=time.time)
    version: int = 1

    def get_phase_plan(self, phase: str) -> Optional[PhasePlan]:
        """Get the plan for a specific phase.

        Args:
            phase: Phase name (e.g., "initial_scan", "deep_review").

        Returns:
            PhasePlan if found, None otherwise.
        """
        return self.phase_plans.get(phase)

    def get_resource_fraction(self, phase: str) -> float:
        """Get the resource fraction allocated to a phase.

        Returns:
            Resource fraction (0.0-1.0), or 0.0 if phase not in plan.
        """
        plan = self.phase_plans.get(phase)
        return plan.resource_fraction if plan else 0.0

    def active_phases(self) -> list[str]:
        """Get non-skipped phases ordered by priority (highest first).

        Returns:
            Sorted list of phase names where strategy != SKIP.
        """
        active = [
            (name, pp)
            for name, pp in self.phase_plans.items()
            if pp.strategy != PhaseStrategy.SKIP
        ]
        active.sort(key=lambda x: x[1].priority, reverse=True)
        return [name for name, _ in active]

    def is_phase_skipped(self, phase: str) -> bool:
        """Check if a phase is marked for skipping."""
        plan = self.phase_plans.get(phase)
        return plan is not None and plan.strategy == PhaseStrategy.SKIP

    def serialize(self) -> dict:
        """Serialize to dictionary."""
        return {
            "paper_profile": self.paper_profile.serialize(),
            "phase_plans": {k: v.serialize() for k, v in self.phase_plans.items()},
            "overall_strategy": self.overall_strategy,
            "estimated_total_turns": self.estimated_total_turns,
            "created_at": self.created_at,
            "version": self.version,
        }

    @classmethod
    def deserialize(cls, data: dict) -> ReviewPlan:
        """Reconstruct from dictionary."""
        profile = PaperProfile.deserialize(data["paper_profile"])
        phase_plans = {
            k: PhasePlan.deserialize(v)
            for k, v in data.get("phase_plans", {}).items()
        }
        return cls(
            paper_profile=profile,
            phase_plans=phase_plans,
            overall_strategy=data.get("overall_strategy", "balanced"),
            estimated_total_turns=data.get("estimated_total_turns", 30),
            created_at=data.get("created_at", time.time()),
            version=data.get("version", 1),
        )


class ReviewPlanner:
    """Creates initial review plans based on paper profiles.

    Uses strategy templates that define resource allocation patterns
    for different paper types. Templates are selected based on paper
    profile characteristics (complexity, methodology, length).

    Strategy templates:
        - empirical_standard: Balanced approach for standard empirical papers.
        - methodology_novel: Extra depth on methods section.
        - short_note: Light pass for short communications/notes.
        - data_heavy: Focus on data/tables validation.
        - theory_heavy: Focus on logical consistency and proofs.
    """

    # Strategy templates: maps template name to phase allocation configs
    _STRATEGY_TEMPLATES: dict[str, dict[str, Any]] = {
        "empirical_standard": {
            "description": "Balanced approach for standard empirical papers",
            "phases": {
                "initial_scan": {
                    "strategy": "light",
                    "priority": 0.5,
                    "fraction": 0.10,
                    "focus": ["structure", "abstract", "contribution_claims"],
                },
                "deep_review": {
                    "strategy": "full",
                    "priority": 0.9,
                    "fraction": 0.50,
                    "focus": ["methodology", "identification", "robustness", "data_quality"],
                },
                "editing": {
                    "strategy": "focused",
                    "priority": 0.6,
                    "fraction": 0.15,
                    "focus": ["clarity", "presentation", "notation"],
                },
                "synthesis": {
                    "strategy": "full",
                    "priority": 0.8,
                    "fraction": 0.25,
                    "focus": ["overall_contribution", "limitations", "constructive_suggestions"],
                },
            },
            "estimated_turns_per_complexity": {
                "simple": 20,
                "moderate": 30,
                "complex": 40,
                "highly_complex": 50,
            },
        },
        "methodology_novel": {
            "description": "Extra depth on novel methodology papers",
            "phases": {
                "initial_scan": {
                    "strategy": "focused",
                    "priority": 0.6,
                    "fraction": 0.12,
                    "focus": ["method_overview", "assumptions", "comparison_to_existing"],
                },
                "deep_review": {
                    "strategy": "deep",
                    "priority": 1.0,
                    "fraction": 0.55,
                    "focus": ["proof_validity", "assumptions_check", "monte_carlo", "edge_cases", "identification"],
                },
                "editing": {
                    "strategy": "light",
                    "priority": 0.4,
                    "fraction": 0.08,
                    "focus": ["notation_consistency", "exposition_clarity"],
                },
                "synthesis": {
                    "strategy": "full",
                    "priority": 0.85,
                    "fraction": 0.25,
                    "focus": ["novelty_assessment", "practical_applicability", "limitations"],
                },
            },
            "estimated_turns_per_complexity": {
                "simple": 25,
                "moderate": 35,
                "complex": 45,
                "highly_complex": 55,
            },
        },
        "short_note": {
            "description": "Light pass for short research notes or comments",
            "phases": {
                "initial_scan": {
                    "strategy": "light",
                    "priority": 0.4,
                    "fraction": 0.15,
                    "focus": ["main_claim", "context"],
                },
                "deep_review": {
                    "strategy": "focused",
                    "priority": 0.8,
                    "fraction": 0.45,
                    "focus": ["core_argument", "evidence_quality"],
                },
                "editing": {
                    "strategy": "skip",
                    "priority": 0.0,
                    "fraction": 0.0,
                    "focus": [],
                    "skip_reason": "Short notes rarely need editing pass",
                },
                "synthesis": {
                    "strategy": "light",
                    "priority": 0.7,
                    "fraction": 0.40,
                    "focus": ["concise_assessment", "key_suggestion"],
                },
            },
            "estimated_turns_per_complexity": {
                "simple": 12,
                "moderate": 18,
                "complex": 25,
                "highly_complex": 30,
            },
        },
        "data_heavy": {
            "description": "Focus on data quality and tables for data-intensive papers",
            "phases": {
                "initial_scan": {
                    "strategy": "focused",
                    "priority": 0.6,
                    "fraction": 0.12,
                    "focus": ["data_sources", "sample_construction", "variable_definitions"],
                },
                "deep_review": {
                    "strategy": "deep",
                    "priority": 0.95,
                    "fraction": 0.50,
                    "focus": ["table_consistency", "statistical_tests", "sample_selection", "measurement_error"],
                },
                "editing": {
                    "strategy": "focused",
                    "priority": 0.5,
                    "fraction": 0.13,
                    "focus": ["table_formatting", "variable_labels", "notes_completeness"],
                },
                "synthesis": {
                    "strategy": "full",
                    "priority": 0.8,
                    "fraction": 0.25,
                    "focus": ["data_limitations", "replication_potential", "external_validity"],
                },
            },
            "estimated_turns_per_complexity": {
                "simple": 22,
                "moderate": 32,
                "complex": 42,
                "highly_complex": 52,
            },
        },
        "theory_heavy": {
            "description": "Focus on logical consistency for theoretical papers",
            "phases": {
                "initial_scan": {
                    "strategy": "focused",
                    "priority": 0.6,
                    "fraction": 0.12,
                    "focus": ["model_setup", "key_assumptions", "main_results"],
                },
                "deep_review": {
                    "strategy": "deep",
                    "priority": 1.0,
                    "fraction": 0.55,
                    "focus": ["proof_logic", "assumption_necessity", "comparative_statics", "equilibrium_existence"],
                },
                "editing": {
                    "strategy": "light",
                    "priority": 0.3,
                    "fraction": 0.08,
                    "focus": ["notation", "exposition"],
                },
                "synthesis": {
                    "strategy": "full",
                    "priority": 0.85,
                    "fraction": 0.25,
                    "focus": ["contribution_to_literature", "empirical_relevance", "extensions"],
                },
            },
            "estimated_turns_per_complexity": {
                "simple": 20,
                "moderate": 30,
                "complex": 42,
                "highly_complex": 50,
            },
        },
    }

    def create_plan(self, profile: PaperProfile, budget: ResourceBudget) -> ReviewPlan:
        """Create initial review plan from paper profile.

        Selects the best matching strategy template, then allocates
        resources to phases based on the template's configuration.

        Args:
            profile: Analyzed paper profile.
            budget: Available resource budget for the session.

        Returns:
            ReviewPlan ready for execution.
        """
        template_name = self._select_strategy_template(profile)
        template = self._STRATEGY_TEMPLATES[template_name]

        phase_plans = self._allocate_phase_resources(profile, template, budget)
        estimated_turns = template["estimated_turns_per_complexity"].get(
            profile.complexity.value, 30
        )

        plan = ReviewPlan(
            paper_profile=profile,
            phase_plans=phase_plans,
            overall_strategy=template_name,
            estimated_total_turns=estimated_turns,
        )

        logger.info(
            "[ReviewPlanner] Created plan: strategy=%s, complexity=%s, "
            "est_turns=%d, active_phases=%s",
            template_name, profile.complexity.value,
            estimated_turns, plan.active_phases(),
        )
        return plan

    def _select_strategy_template(self, profile: PaperProfile) -> str:
        """Select best matching strategy template based on paper profile.

        Decision logic:
            1. Short papers (< 8K tokens, < 5 sections) → short_note
            2. Novel methodology (2+ methods OR structural) → methodology_novel
            3. Data-heavy (has_tables AND 0 novelty signals) → data_heavy
            4. Theory-heavy (STRUCTURAL in methods) → theory_heavy
            5. Default → empirical_standard

        Args:
            profile: Paper profile to match against.

        Returns:
            Template name string.
        """
        # Short note detection
        if (profile.estimated_length_tokens < 8000
                and profile.num_sections < 5
                and profile.complexity == PaperComplexity.SIMPLE):
            return "short_note"

        # Theory-heavy detection
        if "STRUCTURAL" in profile.methodology_types:
            return "theory_heavy"

        # Methodology-novel detection
        if (len(profile.methodology_types) >= 2
                or len(profile.novelty_signals) >= 3):
            return "methodology_novel"

        # Data-heavy detection
        if (profile.has_tables
                and len(profile.novelty_signals) == 0
                and profile.complexity in (PaperComplexity.MODERATE, PaperComplexity.COMPLEX)):
            return "data_heavy"

        # Default: balanced empirical
        return "empirical_standard"

    def _allocate_phase_resources(
        self,
        profile: PaperProfile,
        template: dict,
        budget: ResourceBudget,
    ) -> dict[str, PhasePlan]:
        """Allocate resources to phases based on profile and template.

        Applies the template's allocation fractions, adjusted for
        paper complexity. More complex papers get proportionally
        more resources in deep_review.

        Args:
            profile: Paper profile.
            template: Selected strategy template dict.
            budget: Available resource budget.

        Returns:
            Dict mapping phase names to PhasePlans.
        """
        phase_plans: dict[str, PhasePlan] = {}
        phases_config = template["phases"]

        # Complexity adjustment factor for deep_review
        complexity_boost = {
            PaperComplexity.SIMPLE: 0.9,
            PaperComplexity.MODERATE: 1.0,
            PaperComplexity.COMPLEX: 1.1,
            PaperComplexity.HIGHLY_COMPLEX: 1.2,
        }.get(profile.complexity, 1.0)

        total_fraction = 0.0
        for phase_name, config in phases_config.items():
            strategy = PhaseStrategy(config["strategy"])
            priority = config["priority"]
            fraction = config["fraction"]

            # Apply complexity boost to deep_review
            if phase_name == "deep_review":
                fraction = min(0.70, fraction * complexity_boost)

            focus_areas = config.get("focus", [])
            skip_reason = config.get("skip_reason", "")

            phase_plans[phase_name] = PhasePlan(
                phase=phase_name,
                strategy=strategy,
                priority=priority,
                resource_fraction=fraction,
                focus_areas=focus_areas,
                skip_reason=skip_reason,
            )
            total_fraction += fraction

        # Normalize fractions to sum to ~1.0
        if total_fraction > 0 and abs(total_fraction - 1.0) > 0.01:
            for pp in phase_plans.values():
                if pp.strategy != PhaseStrategy.SKIP:
                    pp.resource_fraction /= total_fraction

        return phase_plans


# ==============================================================
# 4. Dual-Loop Signal System
# ==============================================================

class DualLoopSignalType(Enum):
    """Typed signals between inner and outer loops.

    Inner → Outer signals are observations about loop progress.
    Outer → Inner signals are advisory suggestions.
    """

    # --- Inner → Outer (observation signals) ---
    PHASE_PROGRESS = "inner.phase_progress"
    PHASE_STUCK = "inner.phase_stuck"
    BUDGET_WARNING = "inner.budget_warning"
    BUDGET_EXHAUSTED = "inner.budget_exhausted"
    MAJOR_FINDING = "inner.major_finding"
    QUALITY_CONCERN = "inner.quality_concern"
    UNEXPECTED_COMPLEXITY = "inner.unexpected_complexity"

    # --- Outer → Inner (advisory signals) ---
    INCREASE_BUDGET = "outer.increase_budget"
    DECREASE_BUDGET = "outer.decrease_budget"
    SUGGEST_SKIP = "outer.suggest_skip"
    CHANGE_FOCUS = "outer.change_focus"
    FORCE_CONCLUDE = "outer.force_conclude"
    REPLAN = "outer.replan"


@dataclass
class DualLoopSignal:
    """A typed signal in the dual-loop system.

    Attributes:
        signal_type: The type of this signal.
        payload: Arbitrary data associated with the signal.
        source: Identifier of the emitting component.
        timestamp: When the signal was created.
        urgency: How urgent this signal is (0.0=low, 1.0=critical).
    """
    signal_type: DualLoopSignalType
    payload: dict = field(default_factory=dict)
    source: str = ""
    timestamp: float = field(default_factory=time.time)
    urgency: float = 0.5

    def serialize(self) -> dict:
        """Serialize to dictionary."""
        return {
            "signal_type": self.signal_type.value,
            "payload": self.payload,
            "source": self.source,
            "timestamp": self.timestamp,
            "urgency": self.urgency,
        }

    @classmethod
    def deserialize(cls, data: dict) -> DualLoopSignal:
        """Reconstruct from dictionary."""
        return cls(
            signal_type=DualLoopSignalType(data["signal_type"]),
            payload=data.get("payload", {}),
            source=data.get("source", ""),
            timestamp=data.get("timestamp", time.time()),
            urgency=data.get("urgency", 0.5),
        )


# ==============================================================
# 5. Plan Adaptation (Dynamic Replanning)
# ==============================================================

@dataclass
class PlanUpdate:
    """A proposed update to the current review plan.

    Generated by the PlanAdapter when it detects conditions requiring
    a plan change. Gets converted to natural-language advisory text
    for injection into the cognitive loop via SignalDispatcher.

    Attributes:
        update_type: Category of update (resource_realloc, phase_skip, etc.).
        target_phase: Phase being affected (empty if global).
        changes: Specific changes to apply.
        reason: Human-readable explanation.
        confidence: How confident the outer loop is (0.0-1.0).
        timestamp: When the update was proposed.
    """
    update_type: str
    target_phase: str = ""
    changes: dict = field(default_factory=dict)
    reason: str = ""
    confidence: float = 0.8
    timestamp: float = field(default_factory=time.time)

    def to_advisory_message(self) -> str:
        """Convert to a natural-language advisory for system message injection.

        Produces a concise, actionable message that the LLM can understand
        and optionally act upon. The message is framed as a suggestion,
        not a command (per C5 principle).

        Returns:
            Advisory string suitable for system message injection.
        """
        prefix = "[DualLoop Advisory]"

        if self.update_type == "resource_realloc":
            direction = self.changes.get("direction", "adjust")
            target = self.target_phase or "current phase"
            return (
                f"{prefix} Resource reallocation suggested for '{target}': "
                f"{direction}. Reason: {self.reason}"
            )

        elif self.update_type == "phase_skip":
            return (
                f"{prefix} Consider skipping '{self.target_phase}' phase. "
                f"Reason: {self.reason}. "
                f"You may proceed to the next priority phase."
            )

        elif self.update_type == "focus_change":
            new_focus = self.changes.get("new_focus", [])
            focus_str = ", ".join(new_focus) if new_focus else "general"
            return (
                f"{prefix} Consider shifting focus within '{self.target_phase}' "
                f"to: {focus_str}. Reason: {self.reason}"
            )

        elif self.update_type == "full_replan":
            return (
                f"{prefix} Strategy adjustment recommended. "
                f"The paper appears {self.reason}. "
                f"Consider adjusting your approach accordingly."
            )

        elif self.update_type == "force_conclude":
            return (
                f"{prefix} ⚠️ Budget critically low. Please begin wrapping up "
                f"your analysis and move toward synthesis. "
                f"Reason: {self.reason}"
            )

        else:
            return f"{prefix} {self.reason}"

    def serialize(self) -> dict:
        """Serialize to dictionary."""
        return {
            "update_type": self.update_type,
            "target_phase": self.target_phase,
            "changes": self.changes,
            "reason": self.reason,
            "confidence": self.confidence,
            "timestamp": self.timestamp,
        }

    @classmethod
    def deserialize(cls, data: dict) -> PlanUpdate:
        """Reconstruct from dictionary."""
        return cls(
            update_type=data["update_type"],
            target_phase=data.get("target_phase", ""),
            changes=data.get("changes", {}),
            reason=data.get("reason", ""),
            confidence=data.get("confidence", 0.8),
            timestamp=data.get("timestamp", time.time()),
        )


class PlanAdapter:
    """Adapts review plans based on incoming signals from the inner loop.

    Contains the decision rules for when and how to modify the active plan.
    Each signal type has a dedicated handler that may produce a PlanUpdate.

    Configuration thresholds:
        stuck_threshold: Turns in same phase without progress before stuck signal.
        budget_realloc_threshold: Overall utilization triggering reallocation.
        major_finding_boost: Extra resource fraction after major finding.
    """

    # Tunable thresholds
    STUCK_THRESHOLD_TURNS: int = 3
    BUDGET_REALLOC_UTILIZATION: float = 0.80
    MAJOR_FINDING_BOOST_FRACTION: float = 0.10
    FORCE_CONCLUDE_UTILIZATION: float = 0.92
    QUALITY_CONCERN_THRESHOLD: int = 2  # Consecutive quality concerns

    def __init__(self, plan: ReviewPlan, budget: ResourceBudget):
        """Initialize the PlanAdapter.

        Args:
            plan: The current active review plan.
            budget: The session resource budget being tracked.
        """
        self.plan = plan
        self.budget = budget
        self._signal_history: list[DualLoopSignal] = []
        self._update_history: list[PlanUpdate] = []
        self._stuck_counter: dict[str, int] = {}
        self._quality_concern_streak: int = 0
        self._last_finding_turn: int = 0

    def process_signal(self, signal: DualLoopSignal) -> Optional[PlanUpdate]:
        """Process an incoming signal and decide if the plan needs updating.

        Routes the signal to the appropriate handler based on type.
        Records the signal in history regardless of whether an update is produced.

        Args:
            signal: The incoming dual-loop signal.

        Returns:
            PlanUpdate if the plan should be adapted, None otherwise.
        """
        self._signal_history.append(signal)

        update: Optional[PlanUpdate] = None

        if signal.signal_type == DualLoopSignalType.PHASE_STUCK:
            update = self._handle_phase_stuck(signal)
        elif signal.signal_type == DualLoopSignalType.BUDGET_WARNING:
            update = self._handle_budget_warning(signal)
        elif signal.signal_type == DualLoopSignalType.BUDGET_EXHAUSTED:
            update = self._handle_budget_exhausted(signal)
        elif signal.signal_type == DualLoopSignalType.MAJOR_FINDING:
            update = self._handle_major_finding(signal)
        elif signal.signal_type == DualLoopSignalType.UNEXPECTED_COMPLEXITY:
            update = self._handle_unexpected_complexity(signal)
        elif signal.signal_type == DualLoopSignalType.QUALITY_CONCERN:
            update = self._handle_quality_concern(signal)
        elif signal.signal_type == DualLoopSignalType.PHASE_PROGRESS:
            # Reset stuck counter on progress
            phase = signal.payload.get("phase", "")
            if phase:
                self._stuck_counter[phase] = 0
            self._quality_concern_streak = 0

        if update:
            self._update_history.append(update)
            logger.info(
                "[PlanAdapter] Generated update: type=%s, phase=%s, reason=%s",
                update.update_type, update.target_phase, update.reason,
            )

        return update

    def _handle_phase_stuck(self, signal: DualLoopSignal) -> Optional[PlanUpdate]:
        """Handle PHASE_STUCK signal: phase not making progress.

        Rules:
            - If stuck for > STUCK_THRESHOLD_TURNS: suggest focus change
            - If stuck for > 2x threshold: suggest skip to next phase
            - If stuck in synthesis (final phase): suggest force conclude
        """
        phase = signal.payload.get("phase", "")
        turns_stuck = signal.payload.get("turns_stuck", 0)

        # Track stuck count
        self._stuck_counter[phase] = self._stuck_counter.get(phase, 0) + 1
        cumulative_stuck = self._stuck_counter[phase]

        if cumulative_stuck > self.STUCK_THRESHOLD_TURNS * 2:
            # Severely stuck — suggest skipping
            return PlanUpdate(
                update_type="phase_skip",
                target_phase=phase,
                changes={"action": "skip_to_next"},
                reason=f"Phase '{phase}' stuck for {cumulative_stuck} turns without "
                       f"meaningful progress. Consider moving forward.",
                confidence=0.7,
            )
        elif cumulative_stuck > self.STUCK_THRESHOLD_TURNS:
            # Moderately stuck — suggest focus change
            current_plan = self.plan.get_phase_plan(phase)
            alternative_focus = self._suggest_alternative_focus(phase, current_plan)
            return PlanUpdate(
                update_type="focus_change",
                target_phase=phase,
                changes={"new_focus": alternative_focus},
                reason=f"Phase '{phase}' appears stuck ({cumulative_stuck} turns "
                       f"without new findings). Try a different angle.",
                confidence=0.65,
            )

        return None

    def _handle_budget_warning(self, signal: DualLoopSignal) -> Optional[PlanUpdate]:
        """Handle BUDGET_WARNING signal: approaching resource limits.

        Rules:
            - Identify lowest-priority remaining phases
            - Suggest reducing their allocation
            - If overall utilization > FORCE_CONCLUDE_UTILIZATION: force conclude
        """
        overall_util = self.budget.overall_utilization()
        warning_dims = signal.payload.get("warning_dimensions", [])

        if overall_util >= self.FORCE_CONCLUDE_UTILIZATION:
            return PlanUpdate(
                update_type="force_conclude",
                target_phase="",
                changes={"urgency": "critical"},
                reason=f"Overall resource utilization at {overall_util:.0%}. "
                       f"Exhausted dimensions: {warning_dims}.",
                confidence=0.95,
            )

        # Find lowest-priority active phase to reduce
        active = self.plan.active_phases()
        if len(active) >= 2:
            # Reduce allocation for lowest-priority remaining phase
            lowest_priority_phase = active[-1]
            return PlanUpdate(
                update_type="resource_realloc",
                target_phase=lowest_priority_phase,
                changes={"direction": "decrease", "amount": 0.5},
                reason=f"Budget warning at {overall_util:.0%} utilization. "
                       f"Reducing allocation for '{lowest_priority_phase}' "
                       f"to preserve budget for higher-priority work.",
                confidence=0.75,
            )

        return None

    def _handle_budget_exhausted(self, signal: DualLoopSignal) -> Optional[PlanUpdate]:
        """Handle BUDGET_EXHAUSTED signal: a dimension is fully consumed.

        Always produces a force_conclude update — this is urgent.
        """
        dimension = signal.payload.get("dimension", "unknown")
        return PlanUpdate(
            update_type="force_conclude",
            target_phase="",
            changes={"exhausted_dimension": dimension},
            reason=f"Resource dimension '{dimension}' is exhausted. "
                   f"Must conclude review now.",
            confidence=0.98,
        )

    def _handle_major_finding(self, signal: DualLoopSignal) -> Optional[PlanUpdate]:
        """Handle MAJOR_FINDING signal: high-severity finding discovered.

        Rules:
            - If current phase has budget remaining: increase its allocation
            - If finding opens new investigation area: suggest deeper analysis
        """
        phase = signal.payload.get("phase", "")
        severity = signal.payload.get("severity", "major")
        self._last_finding_turn = signal.payload.get("turn", 0)

        # Reset stuck counter — we're making progress
        if phase:
            self._stuck_counter[phase] = 0

        # Only boost for critical/major findings
        if severity not in ("critical", "major"):
            return None

        # Check if we have budget headroom to boost
        overall_util = self.budget.overall_utilization()
        if overall_util < 0.70:
            return PlanUpdate(
                update_type="resource_realloc",
                target_phase=phase,
                changes={
                    "direction": "increase",
                    "amount": self.MAJOR_FINDING_BOOST_FRACTION,
                },
                reason=f"Major finding ({severity}) discovered in '{phase}'. "
                       f"Allocating additional resources for deeper investigation.",
                confidence=0.7,
            )

        return None

    def _handle_unexpected_complexity(self, signal: DualLoopSignal) -> Optional[PlanUpdate]:
        """Handle UNEXPECTED_COMPLEXITY signal: paper harder than profiled.

        Triggers a full replan with adjusted complexity level.
        """
        observed_complexity = signal.payload.get("observed_complexity", "complex")
        reason = signal.payload.get("reason", "Paper is more complex than initially assessed")

        return PlanUpdate(
            update_type="full_replan",
            target_phase="",
            changes={
                "new_complexity": observed_complexity,
                "replan_trigger": "unexpected_complexity",
            },
            reason=reason,
            confidence=0.8,
        )

    def _handle_quality_concern(self, signal: DualLoopSignal) -> Optional[PlanUpdate]:
        """Handle QUALITY_CONCERN signal: output quality below threshold.

        Rules:
            - Track consecutive concerns
            - After QUALITY_CONCERN_THRESHOLD: suggest focus change
            - Pattern indicates the current approach isn't working
        """
        self._quality_concern_streak += 1

        if self._quality_concern_streak >= self.QUALITY_CONCERN_THRESHOLD:
            phase = signal.payload.get("phase", "")
            self._quality_concern_streak = 0  # Reset after action
            return PlanUpdate(
                update_type="focus_change",
                target_phase=phase,
                changes={"new_focus": ["step_back", "reconsider_approach"]},
                reason=f"Multiple quality concerns detected in '{phase}'. "
                       f"The current analytical approach may need adjustment.",
                confidence=0.6,
            )

        return None

    def _suggest_alternative_focus(
        self, phase: str, current_plan: Optional[PhasePlan]
    ) -> list[str]:
        """Suggest alternative focus areas when stuck.

        Rotates through predefined alternatives based on phase type.
        """
        alternatives: dict[str, list[list[str]]] = {
            "initial_scan": [
                ["conclusions_first", "backward_reading"],
                ["figures_and_tables", "visual_scan"],
            ],
            "deep_review": [
                ["robustness_checks", "sensitivity"],
                ["data_quality", "sample_selection"],
                ["alternative_explanations", "confounders"],
                ["external_validity", "generalizability"],
            ],
            "editing": [
                ["clarity_and_flow", "paragraph_structure"],
                ["notation_consistency", "terminology"],
            ],
            "synthesis": [
                ["constructive_framing", "improvement_suggestions"],
                ["strength_acknowledgment", "balanced_assessment"],
            ],
        }

        phase_alts = alternatives.get(phase, [["general_review"]])
        # Pick based on how many times we've been stuck (rotation)
        stuck_count = self._stuck_counter.get(phase, 0)
        idx = (stuck_count - self.STUCK_THRESHOLD_TURNS - 1) % len(phase_alts)
        return phase_alts[idx]

    @property
    def signal_count(self) -> int:
        """Total signals processed."""
        return len(self._signal_history)

    @property
    def update_count(self) -> int:
        """Total plan updates generated."""
        return len(self._update_history)


# ==============================================================
# 6. Strategy Learning (Complete Layer)
# ==============================================================

@dataclass
class ReviewOutcome:
    """Outcome of a review session, used for strategy learning.

    Records the complete trajectory: what profile was seen, what plan
    was used, and what results were achieved.

    Attributes:
        paper_profile: The profiled paper characteristics.
        plan_used: The review plan that was executed.
        resource_usage: Actual resource consumption by dimension.
        findings_count: Total findings produced.
        quality_score: Overall quality score (0.0-1.0).
        time_taken: Wall-clock time for the review.
        replans_triggered: Number of plan adaptations.
        phases_skipped: Phases that were skipped during execution.
        timestamp: When the review completed.
    """
    paper_profile: PaperProfile
    plan_used: ReviewPlan
    resource_usage: dict
    findings_count: int
    quality_score: float
    time_taken: float
    replans_triggered: int
    phases_skipped: list[str]
    timestamp: float = field(default_factory=time.time)


@dataclass
class StrategyRecord:
    """A strategy-outcome pair for the learning system.

    Lightweight summary of what worked (or didn't) for a paper type.

    Attributes:
        strategy_template: Which strategy template was used.
        paper_complexity: Complexity level of the paper.
        methodology_types: Methodologies detected in the paper.
        field_tags: Research fields of the paper.
        outcome_quality: Quality score achieved (0.0-1.0).
        resource_efficiency: quality / normalized_resource_consumed ratio.
        timestamp: When this record was created.
    """
    strategy_template: str
    paper_complexity: PaperComplexity
    methodology_types: list[str]
    field_tags: list[str]
    outcome_quality: float
    resource_efficiency: float
    timestamp: float = field(default_factory=time.time)

    def serialize(self) -> dict:
        """Serialize to dictionary."""
        return {
            "strategy_template": self.strategy_template,
            "paper_complexity": self.paper_complexity.value,
            "methodology_types": self.methodology_types,
            "field_tags": self.field_tags,
            "outcome_quality": self.outcome_quality,
            "resource_efficiency": self.resource_efficiency,
            "timestamp": self.timestamp,
        }

    @classmethod
    def deserialize(cls, data: dict) -> StrategyRecord:
        """Reconstruct from dictionary."""
        return cls(
            strategy_template=data["strategy_template"],
            paper_complexity=PaperComplexity(data["paper_complexity"]),
            methodology_types=data.get("methodology_types", []),
            field_tags=data.get("field_tags", []),
            outcome_quality=data.get("outcome_quality", 0.0),
            resource_efficiency=data.get("resource_efficiency", 0.0),
            timestamp=data.get("timestamp", time.time()),
        )


class StrategyLearner:
    """Learns which strategies work for which paper types.

    Maintains a bounded history of (paper_profile, strategy, outcome) records
    and recommends strategies based on similarity to past successes.

    Similarity is computed as a weighted combination of:
        - Complexity match (exact = 1.0, adjacent = 0.5, else = 0.0)
        - Methodology overlap (Jaccard coefficient)
        - Field overlap (Jaccard coefficient)

    Recommendation threshold: requires at least MIN_SIMILAR_PAPERS similar
    papers with quality >= MIN_QUALITY_THRESHOLD before making a recommendation.
    """

    MIN_SIMILAR_PAPERS: int = 5
    MIN_QUALITY_THRESHOLD: float = 0.6
    SIMILARITY_THRESHOLD: float = 0.4

    def __init__(self, max_records: int = 200):
        """Initialize the strategy learner.

        Args:
            max_records: Maximum records to retain (FIFO eviction).
        """
        self._records: list[StrategyRecord] = []
        self._max_records = max_records

    def record_outcome(self, outcome: ReviewOutcome) -> None:
        """Record a review outcome for future learning.

        Extracts a StrategyRecord from the outcome and stores it.
        Evicts oldest records when capacity is exceeded.

        Args:
            outcome: The completed review outcome to learn from.
        """
        # Compute resource efficiency
        total_allocated = outcome.plan_used.paper_profile.estimated_length_tokens
        if total_allocated > 0 and outcome.quality_score > 0:
            efficiency = outcome.quality_score / max(
                0.1, outcome.resource_usage.get("tokens_fraction", 1.0)
            )
        else:
            efficiency = 0.0

        record = StrategyRecord(
            strategy_template=outcome.plan_used.overall_strategy,
            paper_complexity=outcome.paper_profile.complexity,
            methodology_types=outcome.paper_profile.methodology_types[:],
            field_tags=outcome.paper_profile.field_tags[:],
            outcome_quality=outcome.quality_score,
            resource_efficiency=efficiency,
        )

        self._records.append(record)

        # Evict oldest if over capacity
        if len(self._records) > self._max_records:
            evict_count = len(self._records) - self._max_records
            self._records = self._records[evict_count:]

        logger.debug(
            "[StrategyLearner] Recorded outcome: strategy=%s, quality=%.2f, "
            "efficiency=%.2f (total records: %d)",
            record.strategy_template, record.outcome_quality,
            record.resource_efficiency, len(self._records),
        )

    def recommend_strategy(self, profile: PaperProfile) -> Optional[str]:
        """Recommend a strategy template based on past successes.

        Finds similar papers in history, filters for quality threshold,
        then recommends the most common successful strategy.

        Args:
            profile: The current paper's profile.

        Returns:
            Strategy template name, or None if insufficient data.
        """
        if len(self._records) < self.MIN_SIMILAR_PAPERS:
            return None

        # Find similar records above quality threshold
        similar_records: list[tuple[float, StrategyRecord]] = []
        for record in self._records:
            sim = self._compute_similarity(profile, record)
            if sim >= self.SIMILARITY_THRESHOLD and record.outcome_quality >= self.MIN_QUALITY_THRESHOLD:
                similar_records.append((sim, record))

        if len(similar_records) < self.MIN_SIMILAR_PAPERS:
            return None

        # Sort by similarity * quality (composite score)
        similar_records.sort(
            key=lambda x: x[0] * x[1].outcome_quality,
            reverse=True,
        )

        # Vote: count strategy occurrences weighted by score
        strategy_scores: dict[str, float] = {}
        for sim, record in similar_records[:20]:  # Top 20 similar
            composite = sim * record.outcome_quality * record.resource_efficiency
            strategy = record.strategy_template
            strategy_scores[strategy] = strategy_scores.get(strategy, 0.0) + composite

        if not strategy_scores:
            return None

        # Return highest-scoring strategy
        best_strategy = max(strategy_scores, key=strategy_scores.get)  # type: ignore[arg-type]

        logger.info(
            "[StrategyLearner] Recommending '%s' (score=%.2f, from %d similar papers)",
            best_strategy, strategy_scores[best_strategy], len(similar_records),
        )
        return best_strategy

    def _compute_similarity(self, profile: PaperProfile, record: StrategyRecord) -> float:
        """Compute similarity between current paper profile and a historical record.

        Weighted combination:
            - Complexity match: weight 0.4 (exact=1.0, adjacent=0.5, else=0.0)
            - Methodology overlap: weight 0.4 (Jaccard coefficient)
            - Field overlap: weight 0.2 (Jaccard coefficient)

        Args:
            profile: Current paper profile.
            record: Historical strategy record.

        Returns:
            Similarity score between 0.0 and 1.0.
        """
        # Complexity similarity
        complexity_order = [
            PaperComplexity.SIMPLE,
            PaperComplexity.MODERATE,
            PaperComplexity.COMPLEX,
            PaperComplexity.HIGHLY_COMPLEX,
        ]
        try:
            idx_current = complexity_order.index(profile.complexity)
            idx_record = complexity_order.index(record.paper_complexity)
            complexity_diff = abs(idx_current - idx_record)
        except ValueError:
            complexity_diff = 2

        if complexity_diff == 0:
            complexity_sim = 1.0
        elif complexity_diff == 1:
            complexity_sim = 0.5
        else:
            complexity_sim = 0.0

        # Methodology Jaccard
        set_current = set(profile.methodology_types)
        set_record = set(record.methodology_types)
        if set_current or set_record:
            method_sim = len(set_current & set_record) / len(set_current | set_record)
        else:
            method_sim = 1.0  # Both empty → same "type"

        # Field Jaccard
        set_fields_current = set(profile.field_tags)
        set_fields_record = set(record.field_tags)
        if set_fields_current or set_fields_record:
            field_sim = len(set_fields_current & set_fields_record) / len(
                set_fields_current | set_fields_record
            )
        else:
            field_sim = 0.5  # Both empty → neutral

        # Weighted combination
        return 0.4 * complexity_sim + 0.4 * method_sim + 0.2 * field_sim

    def get_effectiveness_report(self) -> dict:
        """Report on strategy effectiveness by paper type.

        Returns:
            Dictionary with strategy → {count, avg_quality, avg_efficiency}
            and complexity → {count, avg_quality} breakdowns.
        """
        if not self._records:
            return {"strategies": {}, "complexities": {}, "total_records": 0}

        # By strategy
        strategy_stats: dict[str, dict[str, Any]] = {}
        for record in self._records:
            key = record.strategy_template
            if key not in strategy_stats:
                strategy_stats[key] = {"count": 0, "total_quality": 0.0, "total_efficiency": 0.0}
            strategy_stats[key]["count"] += 1
            strategy_stats[key]["total_quality"] += record.outcome_quality
            strategy_stats[key]["total_efficiency"] += record.resource_efficiency

        strategies_report: dict[str, dict[str, Any]] = {}
        for key, stats in strategy_stats.items():
            count = stats["count"]
            strategies_report[key] = {
                "count": count,
                "avg_quality": stats["total_quality"] / count if count > 0 else 0.0,
                "avg_efficiency": stats["total_efficiency"] / count if count > 0 else 0.0,
            }

        # By complexity
        complexity_stats: dict[str, dict[str, Any]] = {}
        for record in self._records:
            key = record.paper_complexity.value
            if key not in complexity_stats:
                complexity_stats[key] = {"count": 0, "total_quality": 0.0}
            complexity_stats[key]["count"] += 1
            complexity_stats[key]["total_quality"] += record.outcome_quality

        complexities_report: dict[str, dict[str, Any]] = {}
        for key, stats in complexity_stats.items():
            count = stats["count"]
            complexities_report[key] = {
                "count": count,
                "avg_quality": stats["total_quality"] / count if count > 0 else 0.0,
            }

        return {
            "strategies": strategies_report,
            "complexities": complexities_report,
            "total_records": len(self._records),
        }

    def serialize(self) -> dict:
        """Serialize the learner state to dictionary."""
        return {
            "records": [r.serialize() for r in self._records],
            "max_records": self._max_records,
        }

    @classmethod
    def deserialize(cls, data: dict) -> StrategyLearner:
        """Reconstruct learner from serialized state."""
        max_records = data.get("max_records", 200)
        learner = cls(max_records=max_records)
        for record_data in data.get("records", []):
            try:
                learner._records.append(StrategyRecord.deserialize(record_data))
            except (KeyError, ValueError) as e:
                logger.warning("[StrategyLearner] Skipped corrupt record: %s", e)
        return learner


# ==============================================================
# 7. OuterLoop (The Observer/Advisor)
# ==============================================================

class OuterLoop:
    """The outer loop: observes, plans, and advises the inner loop.

    This is the core of the dual-loop architecture. It sits alongside
    the existing cognitive loop (InnerLoop/loop.py) and provides:

    1. Initial review planning based on paper profiling
    2. Continuous progress monitoring via turn-end callbacks
    3. Issue detection (stuck phases, budget warnings, quality drops)
    4. Advisory message generation for injection into the cognitive loop
    5. Outcome recording for strategy learning

    CRITICAL: The OuterLoop DOES NOT CONTROL the inner loop.
    It only OBSERVES and ADVISES. All advisories are suggestions that
    the LLM may choose to follow or ignore (C5 principle).

    Integration:
        - Subscribes to EventBus events (when event_bus is provided)
        - Produces advisory messages for SignalDispatcher injection
        - Persists state via serialize/deserialize

    Kill Switch: When SCHOLAR_GODEL_DUAL_LOOP is OFF, all methods
    return immediately with empty/no-op results. Zero overhead.
    """

    # Configuration constants
    STUCK_DETECTION_WINDOW: int = 3  # Turns without findings → stuck
    PHASE_BUDGET_WARNING_RATIO: float = 0.85
    MAX_ADVISORIES_PER_TURN: int = 1  # Don't overwhelm the LLM
    MIN_TURNS_BETWEEN_ADVISORIES: int = 2  # Prevent advisory spam

    def __init__(
        self,
        budget: Optional[ResourceBudget] = None,
        learner: Optional[StrategyLearner] = None,
    ):
        """Initialize the OuterLoop.

        Args:
            budget: Resource budget for the session. Uses default if None.
            learner: Strategy learner with historical data. Creates new if None.
        """
        self.budget = budget or ResourceBudget.default()
        self.learner = learner or StrategyLearner()
        self._plan: Optional[ReviewPlan] = None
        self._adapter: Optional[PlanAdapter] = None
        self._active: bool = False
        self._current_phase: str = ""
        self._phase_turn_count: int = 0
        self._phase_finding_count: int = 0
        self._total_findings: int = 0
        self._total_turns: int = 0
        self._signals_emitted: list[DualLoopSignal] = []
        self._advisories: list[str] = []
        self._pending_advisories: list[str] = []
        self._last_advisory_turn: int = -10  # Allow first advisory immediately
        self._session_start_time: float = time.time()
        self._turns_since_last_finding: int = 0
        self._phase_budgets: dict[str, PhaseResourceBudget] = {}

    def plan_review(self, paper_text: str, metadata: dict | None = None) -> ReviewPlan:
        """Create initial review plan for a paper.

        Profiles the paper, checks strategy learner for recommendations,
        and creates a plan using the ReviewPlanner.

        Args:
            paper_text: Full text of the paper to review.
            metadata: Optional metadata (field, journal, etc.).

        Returns:
            ReviewPlan. If kill switch is OFF, returns a minimal no-op plan.
        """
        if not _is_enabled():
            # Return minimal plan that won't interfere
            profile = PaperProfile(
                complexity=PaperComplexity.MODERATE,
                estimated_length_tokens=0,
                methodology_types=[],
                has_tables=False,
                has_figures=False,
                num_sections=1,
                field_tags=[],
                novelty_signals=[],
                controversy_signals=[],
            )
            return ReviewPlan(
                paper_profile=profile,
                phase_plans={},
                overall_strategy="disabled",
                estimated_total_turns=50,
            )

        # Profile the paper
        profile = PaperProfile.from_paper_text(paper_text, metadata)

        # Check strategy learner for recommendation
        recommended_strategy = self.learner.recommend_strategy(profile)

        # Create plan
        planner = ReviewPlanner()
        self._plan = planner.create_plan(profile, self.budget)

        # If learner has a different recommendation and we trust it, override
        if (recommended_strategy
                and recommended_strategy != self._plan.overall_strategy
                and recommended_strategy in ReviewPlanner._STRATEGY_TEMPLATES):
            logger.info(
                "[OuterLoop] Learner recommends '%s' (plan used '%s'). "
                "Applying learner recommendation.",
                recommended_strategy, self._plan.overall_strategy,
            )
            # Re-create with recommended strategy
            self._plan = planner.create_plan(profile, self.budget)
            self._plan.overall_strategy = recommended_strategy

        # Initialize adapter
        self._adapter = PlanAdapter(self._plan, self.budget)
        self._active = True
        self._session_start_time = time.time()

        # Pre-allocate phase budgets
        for phase_name in self._plan.active_phases():
            fraction = self._plan.get_resource_fraction(phase_name)
            self._phase_budgets[phase_name] = self.budget.allocate_to_phase(
                phase_name, fraction
            )

        logger.info(
            "[OuterLoop] Review planned: strategy=%s, complexity=%s, "
            "phases=%s, est_turns=%d",
            self._plan.overall_strategy,
            profile.complexity.value,
            self._plan.active_phases(),
            self._plan.estimated_total_turns,
        )

        return self._plan

    def on_turn_end(
        self,
        turn: int,
        phase: str,
        tokens_used: int,
        findings: list[Finding] | None = None,
    ) -> list[str]:
        """Called after each inner loop turn. Returns advisory messages to inject.

        This is the main observation point. It:
        1. Updates resource tracking
        2. Detects stuck conditions
        3. Checks budget warnings
        4. Processes new findings
        5. Produces advisories if plan updates are needed

        Args:
            turn: Current turn number.
            phase: Current phase name.
            tokens_used: Tokens consumed this turn.
            findings: New findings produced this turn (may be empty).

        Returns:
            List of advisory messages to inject (usually 0 or 1).
            Empty list if kill switch is OFF or no advisories needed.
        """
        if not _is_enabled() or not self._active:
            return []

        findings = findings or []
        self._total_turns = turn
        elapsed_this_turn = 0.0  # Approximate; real timing would need timestamps

        # --- 1. Update resource tracking ---
        self.budget.consume(ResourceDimension.TOKENS, float(tokens_used))
        self.budget.consume(ResourceDimension.TURNS, 1.0)
        elapsed_total = time.time() - self._session_start_time
        # Update time consumed to actual elapsed
        self.budget.consumed[ResourceDimension.TIME_SECONDS] = elapsed_total
        self.budget.consume(ResourceDimension.API_CALLS, 1.0)

        # Update phase budget
        if phase in self._phase_budgets:
            self._phase_budgets[phase].consume_turn(tokens_used, elapsed_this_turn)

        # --- 2. Track phase transitions internally ---
        if phase != self._current_phase:
            old_phase = self._current_phase
            self._current_phase = phase
            self._phase_turn_count = 0
            self._phase_finding_count = 0
            self._turns_since_last_finding = 0
        else:
            self._phase_turn_count += 1

        # --- 3. Process findings ---
        new_finding_count = len(findings)
        if new_finding_count > 0:
            self._total_findings += new_finding_count
            self._phase_finding_count += new_finding_count
            self._turns_since_last_finding = 0
            self.budget.consume(ResourceDimension.FINDINGS_QUOTA, float(new_finding_count))

            # Check for major findings
            for finding in findings:
                if finding.severity in ("critical", "major"):
                    signal = DualLoopSignal(
                        signal_type=DualLoopSignalType.MAJOR_FINDING,
                        payload={
                            "phase": phase,
                            "severity": finding.severity,
                            "category": finding.category,
                            "turn": turn,
                        },
                        source="outer_loop",
                        urgency=0.7,
                    )
                    self._emit_signal(signal)
        else:
            self._turns_since_last_finding += 1

        # --- 4. Stuck detection ---
        if self._turns_since_last_finding >= self.STUCK_DETECTION_WINDOW:
            signal = DualLoopSignal(
                signal_type=DualLoopSignalType.PHASE_STUCK,
                payload={
                    "phase": phase,
                    "turns_stuck": self._turns_since_last_finding,
                    "turn": turn,
                },
                source="outer_loop",
                urgency=0.6,
            )
            self._emit_signal(signal)

        # --- 5. Budget warnings ---
        warning_dims = self.budget.get_warning_dimensions()
        if warning_dims:
            signal = DualLoopSignal(
                signal_type=DualLoopSignalType.BUDGET_WARNING,
                payload={
                    "warning_dimensions": [d.value for d in warning_dims],
                    "overall_utilization": self.budget.overall_utilization(),
                    "turn": turn,
                },
                source="outer_loop",
                urgency=0.8,
            )
            self._emit_signal(signal)

        # Check exhausted
        exhausted_dims = self.budget.get_exhausted_dimensions()
        if exhausted_dims:
            for dim in exhausted_dims:
                if dim != ResourceDimension.FINDINGS_QUOTA:  # Findings quota is soft
                    signal = DualLoopSignal(
                        signal_type=DualLoopSignalType.BUDGET_EXHAUSTED,
                        payload={
                            "dimension": dim.value,
                            "turn": turn,
                        },
                        source="outer_loop",
                        urgency=1.0,
                    )
                    self._emit_signal(signal)
                    break  # One exhaustion signal is enough

        # --- 6. Phase budget check ---
        if phase in self._phase_budgets:
            phase_budget = self._phase_budgets[phase]
            if phase_budget.utilization >= self.PHASE_BUDGET_WARNING_RATIO:
                signal = DualLoopSignal(
                    signal_type=DualLoopSignalType.BUDGET_WARNING,
                    payload={
                        "phase": phase,
                        "phase_utilization": phase_budget.utilization,
                        "warning_dimensions": ["phase_budget"],
                        "overall_utilization": self.budget.overall_utilization(),
                        "turn": turn,
                    },
                    source="outer_loop.phase_budget",
                    urgency=0.65,
                )
                self._emit_signal(signal)

        # --- 7. Progress signal (always emit for tracking) ---
        progress_signal = DualLoopSignal(
            signal_type=DualLoopSignalType.PHASE_PROGRESS,
            payload={
                "phase": phase,
                "turn": turn,
                "phase_turns": self._phase_turn_count,
                "phase_findings": self._phase_finding_count,
                "total_findings": self._total_findings,
            },
            source="outer_loop",
            urgency=0.1,
        )
        # Don't emit progress as a signal to adapter — it's just tracking
        if self._adapter and new_finding_count > 0:
            self._adapter.process_signal(progress_signal)

        # --- 8. Collect advisories ---
        advisories = self._collect_advisories(turn)
        return advisories

    def on_phase_transition(self, from_phase: str, to_phase: str) -> None:
        """Track phase transitions for progress monitoring.

        Called by the integration layer when the PhaseFSM transitions.

        Args:
            from_phase: Phase being exited.
            to_phase: Phase being entered.
        """
        if not _is_enabled() or not self._active:
            return

        self._current_phase = to_phase
        self._phase_turn_count = 0
        self._phase_finding_count = 0
        self._turns_since_last_finding = 0

        logger.debug(
            "[OuterLoop] Phase transition: %s → %s (total_findings=%d, total_turns=%d)",
            from_phase, to_phase, self._total_findings, self._total_turns,
        )

    def on_session_end(self, quality_score: float = 0.0) -> None:
        """Record outcome for strategy learning when the review session completes.

        Args:
            quality_score: Final quality assessment (0.0-1.0).
        """
        if not _is_enabled() or not self._active or not self._plan:
            return

        self._active = False
        elapsed = time.time() - self._session_start_time

        # Build outcome record
        resource_usage = {
            "tokens_used": self.budget.consumed.get(ResourceDimension.TOKENS, 0),
            "turns_used": self.budget.consumed.get(ResourceDimension.TURNS, 0),
            "time_used": elapsed,
            "api_calls": self.budget.consumed.get(ResourceDimension.API_CALLS, 0),
            "tokens_fraction": self.budget.utilization(ResourceDimension.TOKENS),
        }

        # Determine skipped phases
        phases_skipped = [
            phase for phase in self._plan.phase_plans
            if self._plan.is_phase_skipped(phase)
        ]

        outcome = ReviewOutcome(
            paper_profile=self._plan.paper_profile,
            plan_used=self._plan,
            resource_usage=resource_usage,
            findings_count=self._total_findings,
            quality_score=quality_score,
            time_taken=elapsed,
            replans_triggered=self._adapter.update_count if self._adapter else 0,
            phases_skipped=phases_skipped,
        )

        self.learner.record_outcome(outcome)

        logger.info(
            "[OuterLoop] Session ended: quality=%.2f, findings=%d, turns=%d, "
            "time=%.1fs, replans=%d",
            quality_score, self._total_findings, self._total_turns,
            elapsed, outcome.replans_triggered,
        )

    def get_current_advisory(self) -> Optional[str]:
        """Get the most recent undelivered advisory message.

        Returns:
            Advisory string, or None if no pending advisories.
        """
        if self._pending_advisories:
            return self._pending_advisories.pop(0)
        return None

    @property
    def plan(self) -> Optional[ReviewPlan]:
        """The current active review plan."""
        return self._plan

    @property
    def is_active(self) -> bool:
        """Whether the outer loop is actively monitoring."""
        return self._active

    @property
    def progress_report(self) -> dict:
        """Current progress summary for debugging/monitoring.

        Returns:
            Dictionary with turns, findings, utilization, phase info.
        """
        return {
            "active": self._active,
            "current_phase": self._current_phase,
            "total_turns": self._total_turns,
            "total_findings": self._total_findings,
            "phase_turn_count": self._phase_turn_count,
            "phase_finding_count": self._phase_finding_count,
            "turns_since_last_finding": self._turns_since_last_finding,
            "overall_utilization": self.budget.overall_utilization(),
            "signals_emitted": len(self._signals_emitted),
            "advisories_produced": len(self._advisories),
            "plan_version": self._plan.version if self._plan else 0,
            "strategy": self._plan.overall_strategy if self._plan else "none",
        }

    def _emit_signal(self, signal: DualLoopSignal) -> None:
        """Emit a signal to the adapter for processing.

        Records the signal and routes it through the PlanAdapter.
        If the adapter produces a PlanUpdate, converts it to an advisory.
        """
        self._signals_emitted.append(signal)

        if self._adapter:
            update = self._adapter.process_signal(signal)
            if update:
                advisory = update.to_advisory_message()
                self._pending_advisories.append(advisory)
                self._advisories.append(advisory)

    def _collect_advisories(self, current_turn: int) -> list[str]:
        """Collect pending advisories, respecting rate limits.

        Enforces:
            - MAX_ADVISORIES_PER_TURN limit
            - MIN_TURNS_BETWEEN_ADVISORIES cooldown

        Args:
            current_turn: Current turn number for cooldown tracking.

        Returns:
            List of advisory strings to inject this turn.
        """
        if not self._pending_advisories:
            return []

        # Rate limiting: don't spam advisories
        turns_since_last = current_turn - self._last_advisory_turn
        if turns_since_last < self.MIN_TURNS_BETWEEN_ADVISORIES:
            # Check if any pending advisory is urgent (force_conclude)
            has_urgent = any(
                "⚠️" in adv or "critically low" in adv
                for adv in self._pending_advisories
            )
            if not has_urgent:
                return []

        # Collect up to MAX_ADVISORIES_PER_TURN
        result: list[str] = []
        while self._pending_advisories and len(result) < self.MAX_ADVISORIES_PER_TURN:
            result.append(self._pending_advisories.pop(0))

        if result:
            self._last_advisory_turn = current_turn

        return result

    def serialize(self) -> dict:
        """Serialize the outer loop state for persistence."""
        return {
            "budget": self.budget.serialize(),
            "learner": self.learner.serialize(),
            "plan": self._plan.serialize() if self._plan else None,
            "active": self._active,
            "current_phase": self._current_phase,
            "phase_turn_count": self._phase_turn_count,
            "total_findings": self._total_findings,
            "total_turns": self._total_turns,
            "turns_since_last_finding": self._turns_since_last_finding,
            "session_start_time": self._session_start_time,
            "signals_count": len(self._signals_emitted),
            "advisories_count": len(self._advisories),
        }

    @classmethod
    def deserialize(cls, data: dict) -> OuterLoop:
        """Reconstruct outer loop from serialized state."""
        budget = ResourceBudget.deserialize(data.get("budget", {}))
        learner = StrategyLearner.deserialize(data.get("learner", {}))

        outer = cls(budget=budget, learner=learner)
        outer._active = data.get("active", False)
        outer._current_phase = data.get("current_phase", "")
        outer._phase_turn_count = data.get("phase_turn_count", 0)
        outer._total_findings = data.get("total_findings", 0)
        outer._total_turns = data.get("total_turns", 0)
        outer._turns_since_last_finding = data.get("turns_since_last_finding", 0)
        outer._session_start_time = data.get("session_start_time", time.time())

        plan_data = data.get("plan")
        if plan_data:
            outer._plan = ReviewPlan.deserialize(plan_data)
            outer._adapter = PlanAdapter(outer._plan, outer.budget)

        return outer


# ==============================================================
# 8. InnerLoop Abstraction
# ==============================================================

@dataclass
class InnerLoopStatus:
    """Status snapshot of the inner loop at a point in time.

    Used by the observer protocol to receive structured updates
    about what the inner loop is doing.

    Attributes:
        phase: Current phase name.
        turn: Current turn number.
        tokens_consumed: Total tokens consumed so far.
        findings_produced: Total findings produced so far.
        is_stuck: Whether the inner loop appears stuck.
        quality_estimate: Estimated output quality (0.0-1.0).
    """
    phase: str
    turn: int
    tokens_consumed: int
    findings_produced: int
    is_stuck: bool
    quality_estimate: float


@runtime_checkable
class InnerLoopObserver(Protocol):
    """Protocol for observing inner loop state.

    Any component implementing this protocol can be registered as an
    observer of the inner loop. The OuterLoop implements this protocol.
    """

    def on_turn_end(self, status: InnerLoopStatus) -> list[str]:
        """Called after each inner loop turn.

        Args:
            status: Current status snapshot of the inner loop.

        Returns:
            List of advisory messages to inject into the loop.
        """
        ...

    def on_phase_transition(self, from_phase: str, to_phase: str) -> None:
        """Called when the inner loop transitions between phases.

        Args:
            from_phase: Phase being exited.
            to_phase: Phase being entered.
        """
        ...


# ==============================================================
# 9. Unified Orchestrator (Facade)
# ==============================================================

class DualLoopOrchestrator:
    """Unified facade for the complete dual-loop system.

    This is the primary integration point. External code (e.g., agent.py,
    harness.py) should interact with DualLoopOrchestrator rather than
    the individual components directly.

    Provides:
        - plan_review(): Profile paper and create review plan.
        - tick(): Called every turn, returns advisory messages.
        - on_phase_change(): Track phase transitions.
        - on_finding(): Track individual finding production.
        - conclude(): End session, record outcome, produce report.
        - serialize/deserialize for session persistence.

    Kill Switch: When SCHOLAR_GODEL_DUAL_LOOP is OFF, all methods return
    immediately with no-op results. Zero overhead, zero side effects.

    Usage Example:
        orchestrator = DualLoopOrchestrator()
        plan = orchestrator.plan_review(paper_text)
        # ... in cognitive loop ...
        advisories = orchestrator.tick(turn=5, phase="deep_review", tokens_used=3000)
        for msg in advisories:
            signal_dispatcher.submit(HarnessSignal(source="dual_loop", priority=3, message=msg))
        # ... at end ...
        report = orchestrator.conclude(quality_score=0.75)
    """

    def __init__(
        self,
        budget: Optional[ResourceBudget] = None,
        learner: Optional[StrategyLearner] = None,
    ):
        """Initialize the orchestrator.

        Args:
            budget: Resource budget. Uses default if None.
            learner: Strategy learner with history. Creates new if None.
        """
        self._enabled = _is_enabled()
        self._outer_loop = OuterLoop(budget=budget, learner=learner)
        self._findings_buffer: list[Finding] = []
        self._last_tick_turn: int = -1

        if self._enabled:
            logger.info("[DualLoopOrchestrator] Initialized (ENABLED)")
        else:
            logger.info("[DualLoopOrchestrator] Initialized (DISABLED by kill switch)")

    @property
    def enabled(self) -> bool:
        """Whether the dual-loop system is active."""
        return self._enabled

    @property
    def plan(self) -> Optional[ReviewPlan]:
        """Current review plan (None if not yet planned)."""
        return self._outer_loop.plan

    @property
    def budget(self) -> ResourceBudget:
        """The resource budget being tracked."""
        return self._outer_loop.budget

    @property
    def learner(self) -> StrategyLearner:
        """The strategy learner."""
        return self._outer_loop.learner

    @property
    def progress(self) -> dict:
        """Current progress report."""
        if not self._enabled:
            return {"enabled": False}
        return self._outer_loop.progress_report

    def plan_review(self, paper_text: str, metadata: dict | None = None) -> ReviewPlan:
        """Profile paper and create a review plan.

        This should be called once at the start of a review session,
        after the paper text is loaded.

        Args:
            paper_text: Full text of the paper.
            metadata: Optional metadata dict.

        Returns:
            ReviewPlan (minimal no-op plan if disabled).
        """
        return self._outer_loop.plan_review(paper_text, metadata)

    def tick(
        self,
        turn: int,
        phase: str,
        tokens_used: int,
        findings: list[Finding] | None = None,
    ) -> list[str]:
        """Called every cognitive loop turn. Returns advisory messages.

        Integrates buffered findings with any new ones passed directly.
        This is the main hook for the harness to call after each turn.

        Args:
            turn: Current turn number.
            phase: Current phase name string.
            tokens_used: Tokens consumed this turn.
            findings: Findings produced this turn.

        Returns:
            List of advisory message strings to inject. Empty if disabled.
        """
        if not self._enabled:
            return []

        # Merge buffered findings with direct findings
        all_findings = self._findings_buffer[:]
        if findings:
            all_findings.extend(findings)
        self._findings_buffer.clear()

        self._last_tick_turn = turn
        return self._outer_loop.on_turn_end(
            turn=turn,
            phase=phase,
            tokens_used=tokens_used,
            findings=all_findings,
        )

    def on_phase_change(self, from_phase: str, to_phase: str) -> None:
        """Notify the orchestrator of a phase transition.

        Should be called by the integration layer when PhaseFSM transitions.

        Args:
            from_phase: Phase being exited.
            to_phase: Phase being entered.
        """
        if not self._enabled:
            return
        self._outer_loop.on_phase_transition(from_phase, to_phase)

    def on_finding(self, finding: Finding) -> None:
        """Buffer a finding for the next tick.

        Called as findings are produced during a turn. They get
        processed during the next tick() call.

        Args:
            finding: The finding to buffer.
        """
        if not self._enabled:
            return
        self._findings_buffer.append(finding)

    def conclude(self, quality_score: float = 0.0) -> dict:
        """End the review session, record outcome, and produce a summary.

        Triggers strategy learning from the session's outcome.

        Args:
            quality_score: Final quality assessment (0.0-1.0).

        Returns:
            Session summary dictionary with stats and learner report.
        """
        if not self._enabled:
            return {"enabled": False, "status": "disabled"}

        self._outer_loop.on_session_end(quality_score=quality_score)

        report = {
            "enabled": True,
            "status": "completed",
            "progress": self._outer_loop.progress_report,
            "strategy_used": (
                self._outer_loop.plan.overall_strategy
                if self._outer_loop.plan else "none"
            ),
            "quality_score": quality_score,
            "total_findings": self._outer_loop._total_findings,
            "total_turns": self._outer_loop._total_turns,
            "signals_emitted": len(self._outer_loop._signals_emitted),
            "advisories_produced": len(self._outer_loop._advisories),
            "learner_report": self._outer_loop.learner.get_effectiveness_report(),
        }

        logger.info(
            "[DualLoopOrchestrator] Session concluded: %s",
            json.dumps({k: v for k, v in report.items() if k != "learner_report"}, default=str),
        )

        return report

    def get_advisory(self) -> Optional[str]:
        """Get the next pending advisory without waiting for tick().

        Useful for immediate advisory retrieval between turns.

        Returns:
            Advisory string, or None if no pending advisories.
        """
        if not self._enabled:
            return None
        return self._outer_loop.get_current_advisory()

    def serialize(self) -> dict:
        """Serialize the complete orchestrator state.

        Returns:
            Dictionary suitable for JSON serialization.
        """
        return {
            "enabled": self._enabled,
            "outer_loop": self._outer_loop.serialize(),
            "last_tick_turn": self._last_tick_turn,
            "findings_buffer_size": len(self._findings_buffer),
        }

    @classmethod
    def deserialize(cls, data: dict) -> DualLoopOrchestrator:
        """Reconstruct orchestrator from serialized state.

        Args:
            data: Previously serialized state dictionary.

        Returns:
            Reconstructed DualLoopOrchestrator.
        """
        outer_data = data.get("outer_loop", {})
        budget = ResourceBudget.deserialize(outer_data.get("budget", {}))
        learner = StrategyLearner.deserialize(outer_data.get("learner", {}))

        orchestrator = cls(budget=budget, learner=learner)
        orchestrator._last_tick_turn = data.get("last_tick_turn", -1)

        # Restore outer loop state
        orchestrator._outer_loop = OuterLoop.deserialize(outer_data)

        return orchestrator


# ==============================================================
# 10. EventBus Integration Helpers
# ==============================================================

def register_orchestrator_with_event_bus(
    orchestrator: DualLoopOrchestrator,
    event_bus: Any,
) -> None:
    """Register the orchestrator as an EventBus subscriber.

    Subscribes to relevant events and routes them through the orchestrator.
    This is the glue between the existing EventBus infrastructure and the
    dual-loop system.

    Should be called once during session setup, after both the EventBus
    and Orchestrator are initialized.

    Args:
        orchestrator: The DualLoopOrchestrator instance.
        event_bus: The EventBus instance (from core.event_bus).
    """
    if not orchestrator.enabled:
        logger.debug("[DualLoop] Orchestrator disabled, skipping EventBus registration")
        return

    # Import EventType here to avoid circular imports at module level
    from core.event_bus import EventType, Event

    def _on_phase_transition(event: Event) -> None:
        """Handle PHASE_TRANSITION events from the FSM."""
        from_phase = event.payload.get("from_phase", "")
        to_phase = event.payload.get("to_phase", "")
        if from_phase and to_phase:
            orchestrator.on_phase_change(from_phase, to_phase)

    def _on_finding_added(event: Event) -> None:
        """Handle FINDING_ADDED events from the cognitive loop."""
        # Reconstruct a minimal Finding from event payload
        payload = event.payload
        finding = Finding(
            category=payload.get("category", "unknown"),
            severity=payload.get("severity", "minor"),
            description=payload.get("description", ""),
            evidence=payload.get("evidence", ""),
            suggestion=payload.get("suggestion", ""),
            location=payload.get("location", ""),
            confidence=payload.get("confidence", 0.8),
            skill_source=payload.get("skill_source", ""),
        )
        orchestrator.on_finding(finding)

    def _on_token_budget_warning(event: Event) -> None:
        """Handle TOKEN_BUDGET_WARNING from the budget system."""
        # This is informational — the outer loop has its own budget tracking
        # but we can use this as a cross-validation signal
        logger.debug(
            "[DualLoop] Received TOKEN_BUDGET_WARNING from EventBus: %s",
            event.payload,
        )

    # Register subscriptions at low priority (don't interfere with core handlers)
    event_bus.subscribe(
        EventType.PHASE_TRANSITION,
        _on_phase_transition,
        priority=200,
        subscriber_name="dual_loop.phase_transition",
    )
    event_bus.subscribe(
        EventType.FINDING_ADDED,
        _on_finding_added,
        priority=200,
        subscriber_name="dual_loop.finding_added",
    )
    event_bus.subscribe(
        EventType.TOKEN_BUDGET_WARNING,
        _on_token_budget_warning,
        priority=200,
        subscriber_name="dual_loop.token_warning",
    )

    logger.info("[DualLoop] Registered orchestrator with EventBus (3 subscriptions)")


def create_orchestrator_for_session(
    total_tokens: int = 128000,
    max_turns: int = 50,
    max_time: float = 600.0,
    learner_data: Optional[dict] = None,
) -> DualLoopOrchestrator:
    """Factory function to create an orchestrator for a new review session.

    Convenience function that handles budget and learner initialization.

    Args:
        total_tokens: Token budget for the session.
        max_turns: Maximum turns allowed.
        max_time: Maximum wall-clock time in seconds.
        learner_data: Serialized learner state from previous sessions.

    Returns:
        Configured DualLoopOrchestrator ready for plan_review().
    """
    budget = ResourceBudget.default(
        total_tokens=total_tokens,
        max_turns=max_turns,
        max_time=max_time,
    )

    learner: Optional[StrategyLearner] = None
    if learner_data:
        try:
            learner = StrategyLearner.deserialize(learner_data)
            logger.info(
                "[DualLoop] Loaded learner with %d historical records",
                len(learner._records),
            )
        except Exception as e:
            logger.warning("[DualLoop] Failed to load learner data: %s", e)
            learner = None

    return DualLoopOrchestrator(budget=budget, learner=learner)
