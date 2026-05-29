"""
utils/score_tracker.py — Review score trajectory tracking.

Maintains a history of quality scores across review iterations, enabling:
- Monotonic improvement enforcement (detect score regressions)
- Target score estimation
- Revision progress visualization
- Improvement rate analysis for planning

Persistence:
    - Stored in .workspace/score_history.json (same directory as recall.json)
    - Atomic writes to prevent corruption
    - TTL-free (score history is permanent for the session)

Integration:
    - record_score() called after review_paper and reaudit
    - get_score_trend() exposed via revision_progress tool
    - estimate_improvement() used by agent for fix prioritization
"""

from __future__ import annotations

import json
import time
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Dict, Optional


# ============================================================
# Data Classes
# ============================================================

@dataclass
class ScoreSnapshot:
    """A single point-in-time quality score measurement."""
    timestamp: str  # ISO format
    overall_score: float  # 0-10 scale (from review_engine consensus)
    dimension_scores: Dict[str, float] = field(default_factory=dict)
    issues_remaining: int = 0
    must_fix_remaining: int = 0
    trigger: str = "initial_review"  # what caused this snapshot
    sections_modified: List[str] = field(default_factory=list)  # since last snapshot


@dataclass
class ScoreTrend:
    """Analysis of score progression."""
    snapshots: List[ScoreSnapshot]
    current_score: float
    initial_score: float
    target_score: float
    improvement_rate: float  # points per iteration
    is_improving: bool  # monotonically improving?
    regressions: List[int]  # indices where score dropped


# ============================================================
# Persistence
# ============================================================

_WORKSPACE_DIR = Path(".workspace")
_SCORE_FILE = _WORKSPACE_DIR / "score_history.json"


def _ensure_workspace():
    _WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)


def _load_history() -> List[ScoreSnapshot]:
    """Load score history from disk."""
    if not _SCORE_FILE.exists():
        return []
    try:
        data = json.loads(_SCORE_FILE.read_text(encoding="utf-8"))
        return [
            ScoreSnapshot(
                timestamp=s["timestamp"],
                overall_score=s["overall_score"],
                dimension_scores=s.get("dimension_scores", {}),
                issues_remaining=s.get("issues_remaining", 0),
                must_fix_remaining=s.get("must_fix_remaining", 0),
                trigger=s.get("trigger", "unknown"),
                sections_modified=s.get("sections_modified", []),
            )
            for s in data
        ]
    except (json.JSONDecodeError, KeyError, OSError):
        return []


def _save_history(snapshots: List[ScoreSnapshot]):
    """Atomically save score history to disk."""
    _ensure_workspace()
    data = [asdict(s) for s in snapshots]
    tmp_path = _SCORE_FILE.with_suffix(".tmp")
    try:
        tmp_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        tmp_path.replace(_SCORE_FILE)
    except OSError:
        pass  # Non-fatal


# ============================================================
# Public API
# ============================================================

def record_score(snapshot: ScoreSnapshot):
    """
    Append a new score snapshot to history.

    Called after:
    - review_paper completes (trigger="initial_review")
    - reaudit completes (trigger="re-review")
    - A batch of rewrites completes (trigger="after_rewrite_<section>")
    """
    history = _load_history()
    history.append(snapshot)
    _save_history(history)


def get_score_trend() -> Optional[ScoreTrend]:
    """
    Analyze the score trajectory across all snapshots.

    Returns None if no history exists.
    """
    history = _load_history()
    if not history:
        return None

    scores = [s.overall_score for s in history]
    initial = scores[0]
    current = scores[-1]

    # Target: initial + 2.0 or 8.0, whichever is lower
    target = min(initial + 2.0, 8.0)

    # Detect regressions (score drops)
    regressions = []
    for i in range(1, len(scores)):
        if scores[i] < scores[i - 1] - 0.1:  # Allow tiny fluctuation
            regressions.append(i)

    # Improvement rate
    if len(scores) >= 2:
        improvement_rate = (current - initial) / (len(scores) - 1)
    else:
        improvement_rate = 0.0

    return ScoreTrend(
        snapshots=history,
        current_score=current,
        initial_score=initial,
        target_score=target,
        improvement_rate=improvement_rate,
        is_improving=len(regressions) == 0,
        regressions=regressions,
    )


def estimate_improvement(proposed_fixes: List[str]) -> float:
    """
    Estimate expected score improvement from a set of proposed fixes.

    Based on historical data: how much did similar fix types improve the score?
    Falls back to heuristic estimates if no history is available.

    Heuristic weights (points per fix type):
        major methodology fix: +0.8
        major logic fix: +0.6
        moderate presentation fix: +0.3
        minor fix: +0.1
    """
    # Heuristic estimate (no ML model needed)
    HEURISTIC_WEIGHTS = {
        "major": 0.6,
        "moderate": 0.3,
        "minor": 0.1,
    }

    total_estimate = 0.0
    for fix_desc in proposed_fixes:
        fix_lower = fix_desc.lower()
        if "major" in fix_lower or "methodology" in fix_lower or "logic" in fix_lower:
            total_estimate += HEURISTIC_WEIGHTS["major"]
        elif "moderate" in fix_lower or "presentation" in fix_lower:
            total_estimate += HEURISTIC_WEIGHTS["moderate"]
        else:
            total_estimate += HEURISTIC_WEIGHTS["minor"]

    # Cap at 3.0 (diminishing returns)
    return min(total_estimate, 3.0)


def get_latest_score() -> Optional[float]:
    """Get the most recent overall score, or None."""
    history = _load_history()
    return history[-1].overall_score if history else None


def check_regression(new_score: float) -> Optional[str]:
    """
    Check if a new score represents a regression.

    Returns a warning message if regression detected, None otherwise.
    """
    history = _load_history()
    if not history:
        return None

    previous = history[-1].overall_score
    if new_score < previous - 0.2:
        return (
            f"⚠️ SCORE REGRESSION: {previous:.1f} → {new_score:.1f} "
            f"(dropped {previous - new_score:.1f} points). "
            f"Consider reverting the last modification and trying a different approach."
        )
    return None


def format_score_trend(trend: ScoreTrend) -> str:
    """Format score trend for agent/user display."""
    lines = [
        f"## Score Trajectory",
        f"Initial: {trend.initial_score:.1f} → Current: {trend.current_score:.1f} "
        f"(Target: {trend.target_score:.1f})",
        f"Improvement Rate: {trend.improvement_rate:+.2f} points/iteration",
        f"Monotonically Improving: {'Yes ✓' if trend.is_improving else 'No ✗'}",
    ]

    if trend.regressions:
        lines.append(f"Regressions detected at iterations: {trend.regressions}")

    lines.append("")
    lines.append("### History:")
    for i, snap in enumerate(trend.snapshots):
        marker = "→" if i == len(trend.snapshots) - 1 else " "
        lines.append(
            f"  {marker} [{snap.trigger}] Score: {snap.overall_score:.1f} "
            f"| Issues: {snap.issues_remaining} "
            f"| Must-fix: {snap.must_fix_remaining}"
        )

    # Progress bar
    progress = (trend.current_score - trend.initial_score) / max(
        trend.target_score - trend.initial_score, 0.1
    )
    progress = max(0.0, min(1.0, progress))
    bar_len = 20
    filled = int(bar_len * progress)
    bar = "█" * filled + "░" * (bar_len - filled)
    lines.append(f"\nProgress: [{bar}] {progress*100:.0f}%")

    return "\n".join(lines)
