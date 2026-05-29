"""
llm/cost_tracker.py - Token usage and cost tracking across providers.

Tracks per-provider and per-task token usage with cost estimation.
Provides session summaries and budget alerting.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .provider import get_registry


@dataclass
class UsageRecord:
    """A single LLM call usage record."""
    provider: str
    model: str
    task: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    timestamp: float = field(default_factory=time.time)
    latency_ms: float = 0.0


class CostTracker:
    """
    Tracks token usage and estimated costs across all providers.
    
    Usage:
        tracker = CostTracker(budget_usd=1.0)
        tracker.record("openai", "gpt-4.1-mini", "review_paper", 1500, 800)
        print(tracker.summary())
    """

    def __init__(self, budget_usd: Optional[float] = None):
        self._records: List[UsageRecord] = []
        self._budget_usd = budget_usd
        self._session_start = time.time()

    def record(
        self,
        provider: str,
        model: str,
        task: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: float = 0.0,
    ):
        """Record a single LLM call."""
        cost = self._estimate_cost(provider, input_tokens, output_tokens)
        self._records.append(UsageRecord(
            provider=provider,
            model=model,
            task=task,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            latency_ms=latency_ms,
        ))

    def _estimate_cost(self, provider: str, input_tokens: int, output_tokens: int) -> float:
        """Estimate cost based on provider pricing."""
        registry = get_registry()
        config = registry.get(provider)
        if not config:
            return 0.0
        cost = (
            input_tokens * config.cost_per_1k_input / 1000 +
            output_tokens * config.cost_per_1k_output / 1000
        )
        return round(cost, 6)

    @property
    def total_cost(self) -> float:
        return sum(r.cost_usd for r in self._records)

    @property
    def total_input_tokens(self) -> int:
        return sum(r.input_tokens for r in self._records)

    @property
    def total_output_tokens(self) -> int:
        return sum(r.output_tokens for r in self._records)

    @property
    def total_calls(self) -> int:
        return len(self._records)

    @property
    def is_over_budget(self) -> bool:
        if self._budget_usd is None:
            return False
        return self.total_cost >= self._budget_usd

    @property
    def budget_remaining(self) -> Optional[float]:
        if self._budget_usd is None:
            return None
        return max(0, self._budget_usd - self.total_cost)

    def by_provider(self) -> Dict[str, dict]:
        """Breakdown by provider."""
        breakdown: Dict[str, dict] = {}
        for r in self._records:
            if r.provider not in breakdown:
                breakdown[r.provider] = {
                    "calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cost_usd": 0.0,
                }
            b = breakdown[r.provider]
            b["calls"] += 1
            b["input_tokens"] += r.input_tokens
            b["output_tokens"] += r.output_tokens
            b["cost_usd"] += r.cost_usd
        # Round costs
        for b in breakdown.values():
            b["cost_usd"] = round(b["cost_usd"], 6)
        return breakdown

    def by_task(self) -> Dict[str, dict]:
        """Breakdown by task type."""
        breakdown: Dict[str, dict] = {}
        for r in self._records:
            if r.task not in breakdown:
                breakdown[r.task] = {
                    "calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cost_usd": 0.0,
                }
            b = breakdown[r.task]
            b["calls"] += 1
            b["input_tokens"] += r.input_tokens
            b["output_tokens"] += r.output_tokens
            b["cost_usd"] += r.cost_usd
        for b in breakdown.values():
            b["cost_usd"] = round(b["cost_usd"], 6)
        return breakdown

    def summary(self) -> dict:
        """Full session summary."""
        elapsed = time.time() - self._session_start
        return {
            "session_duration_s": round(elapsed, 1),
            "total_calls": self.total_calls,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cost_usd": round(self.total_cost, 6),
            "budget_usd": self._budget_usd,
            "budget_remaining_usd": (
                round(self.budget_remaining, 6) if self.budget_remaining is not None else None
            ),
            "over_budget": self.is_over_budget,
            "by_provider": self.by_provider(),
            "by_task": self.by_task(),
            "avg_latency_ms": (
                round(sum(r.latency_ms for r in self._records) / len(self._records), 1)
                if self._records else 0.0
            ),
        }

    def check_budget(self, warn_threshold: float = 0.8) -> Optional[str]:
        """
        Check budget status and return warning message if needed.
        
        Args:
            warn_threshold: Fraction of budget at which to warn (0.8 = 80%)
            
        Returns:
            Warning message string, or None if within budget
        """
        if self._budget_usd is None:
            return None
        fraction_used = self.total_cost / self._budget_usd
        if fraction_used >= 1.0:
            return (
                f"BUDGET EXCEEDED: ${self.total_cost:.4f} / ${self._budget_usd:.4f} "
                f"({fraction_used*100:.1f}% used)"
            )
        elif fraction_used >= warn_threshold:
            return (
                f"Budget warning: ${self.total_cost:.4f} / ${self._budget_usd:.4f} "
                f"({fraction_used*100:.1f}% used)"
            )
        return None
