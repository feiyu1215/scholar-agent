"""
Goal Tracker — Session-level goal and phase state machine.

Tracks:
- Current workflow phase (parsing → analysis → review → revision → verification → done)
- Active goals with progress (what needs to be accomplished)
- Phase transitions triggered by tool completions

Used by:
- agent_loop: injects phase context into system prompt
- phase_filter: decides which tools to expose
- plan_persistence: associates plans with goals
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Optional


class Phase(str, Enum):
    """Workflow phases in order of a typical review-revision session."""
    IDLE = "idle"               # No paper loaded yet
    PARSING = "parsing"         # Paper being parsed
    ANALYSIS = "analysis"       # Structural analysis (presubmission, architecture)
    REVIEW = "review"           # Multi-role review in progress
    ROUTING = "routing"         # Issues being routed
    REVISION = "revision"       # Active revision (rewrite/edit loops)
    VERIFICATION = "verification"  # Post-revision checks (deai, consistency)
    DONE = "done"               # All goals met, session complete


# Phase transition rules: which tool completions advance the phase
PHASE_TRANSITIONS = {
    Phase.IDLE: {
        "parse_paper": Phase.PARSING,
    },
    Phase.PARSING: {
        "parse_paper": Phase.ANALYSIS,  # After paper is parsed → analysis
        "build_voice_profile": Phase.ANALYSIS,
    },
    Phase.ANALYSIS: {
        "review_paper": Phase.REVIEW,
        "consolidate_reviews": Phase.REVIEW,
    },
    Phase.REVIEW: {
        "route_issues": Phase.ROUTING,
    },
    Phase.ROUTING: {
        "rewrite_section": Phase.REVISION,
        "generate_rewrite": Phase.REVISION,
        "edit_section": Phase.REVISION,
        "approve_fix": Phase.REVISION,
    },
    Phase.REVISION: {
        "verify_rewrite_quality": Phase.VERIFICATION,
        "deai_audit": Phase.VERIFICATION,
        "deai_closed_loop": Phase.VERIFICATION,
    },
    Phase.VERIFICATION: {
        # Can loop back to revision if issues found
        "rewrite_section": Phase.REVISION,
        "generate_rewrite": Phase.REVISION,
        "edit_section": Phase.REVISION,
    },
}

# Explicit backwards transitions (verification → revision is normal looping)
# Phase.DONE is only reached programmatically when all goals are completed.


@dataclass
class Goal:
    """A tracked goal within the session."""
    id: str
    description: str
    status: str = "active"  # active | completed | blocked | cancelled
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    progress_notes: list = field(default_factory=list)
    phase_when_created: str = "idle"

    def complete(self, note: str = ""):
        self.status = "completed"
        self.completed_at = time.time()
        if note:
            self.progress_notes.append(f"[DONE] {note}")

    def add_progress(self, note: str):
        self.progress_notes.append(note)

    def block(self, reason: str):
        self.status = "blocked"
        self.progress_notes.append(f"[BLOCKED] {reason}")


class GoalTracker:
    """Session-level state machine tracking phase and goals.

    Usage:
        tracker = GoalTracker()
        tracker.add_goal("Review paper and fix all major issues")
        tracker.on_tool_complete("parse_paper")  # advances phase
        context = tracker.get_context_injection()  # for system prompt
    """

    def __init__(self, workspace: Path = None):
        self.phase = Phase.IDLE
        self.goals: list[Goal] = []
        self._phase_history: list[tuple[float, str, str]] = []  # (timestamp, from, to)
        self._tool_count = 0
        self._workspace = workspace
        self._persist_path = (workspace / ".goal_state.json") if workspace else None

        # Try to restore from persistence
        self._restore()

    def add_goal(self, description: str, goal_id: str = None) -> Goal:
        """Register a new goal. Returns the Goal object."""
        gid = goal_id or f"G{len(self.goals) + 1:02d}"
        goal = Goal(
            id=gid,
            description=description,
            phase_when_created=self.phase.value,
        )
        self.goals.append(goal)
        self._persist()
        return goal

    def complete_goal(self, goal_id: str, note: str = "") -> bool:
        """Mark a goal as completed. Returns True if all goals are now done."""
        for g in self.goals:
            if g.id == goal_id:
                g.complete(note)
                self._persist()
                break
        return self.all_goals_done()

    def all_goals_done(self) -> bool:
        """Check if all active goals are completed."""
        active = [g for g in self.goals if g.status in ("active", "blocked")]
        return len(active) == 0 and len(self.goals) > 0

    def on_tool_complete(self, tool_name: str, args: dict = None):
        """Called after every tool execution to potentially advance phase.

        Args:
            tool_name: Name of the completed tool.
            args: Tool arguments (reserved for future arg-based transitions).
        """
        self._tool_count += 1

        # Check if this tool triggers a phase transition
        transitions = PHASE_TRANSITIONS.get(self.phase, {})
        new_phase = transitions.get(tool_name)

        if new_phase and new_phase != self.phase:
            old_phase = self.phase
            self.phase = new_phase
            self._phase_history.append((time.time(), old_phase.value, new_phase.value))
            self._persist()

        # Check if all goals done → advance to DONE
        if self.all_goals_done() and self.phase != Phase.DONE:
            self._phase_history.append((time.time(), self.phase.value, Phase.DONE.value))
            self.phase = Phase.DONE
            self._persist()

    @property
    def current_goal(self) -> Optional[Goal]:
        """Return the first active goal, or None if no active goals exist."""
        active = [g for g in self.goals if g.status == "active"]
        return active[0] if active else None

    def force_phase(self, phase: Phase):
        """Manually set phase (for edge cases or user override)."""
        if phase != self.phase:
            self._phase_history.append((time.time(), self.phase.value, phase.value))
            self.phase = phase
            self._persist()

    def get_context_injection(self) -> str:
        """Generate context string to inject into system prompt dynamic section.

        Compact format designed to minimize tokens while providing phase awareness.
        """
        lines = []
        lines.append(f"## Session State")
        lines.append(f"Phase: {self.phase.value} | Tools called: {self._tool_count}")

        if self.goals:
            active = [g for g in self.goals if g.status == "active"]
            completed = [g for g in self.goals if g.status == "completed"]
            blocked = [g for g in self.goals if g.status == "blocked"]

            if active:
                lines.append(f"Active goals ({len(active)}):")
                for g in active:
                    last_note = g.progress_notes[-1] if g.progress_notes else ""
                    lines.append(f"  - [{g.id}] {g.description}" +
                                 (f" — {last_note}" if last_note else ""))

            if blocked:
                lines.append(f"Blocked ({len(blocked)}):")
                for g in blocked:
                    lines.append(f"  - [{g.id}] {g.description}")

            if completed:
                lines.append(f"Completed: {len(completed)}/{len(self.goals)}")
        else:
            lines.append("No goals registered yet. Infer user's goal from their first message.")

        return "\n".join(lines)

    def get_status_dict(self) -> dict:
        """Return full status as a dict (for session_status tool)."""
        return {
            "phase": self.phase.value,
            "tool_count": self._tool_count,
            "goals": [asdict(g) for g in self.goals],
            "phase_history": [
                {"time": t, "from": f, "to": to}
                for t, f, to in self._phase_history[-10:]
            ],
        }

    def _persist(self):
        """Save state to workspace for recovery after context compression."""
        if not self._persist_path:
            return
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            state = {
                "phase": self.phase.value,
                "tool_count": self._tool_count,
                "goals": [asdict(g) for g in self.goals],
                "phase_history": self._phase_history[-20:],
            }
            self._persist_path.write_text(
                json.dumps(state, default=str, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            pass

    def _restore(self):
        """Restore state from persistence file if it exists."""
        if not self._persist_path or not self._persist_path.exists():
            return
        try:
            state = json.loads(self._persist_path.read_text(encoding="utf-8"))
            self.phase = Phase(state.get("phase", "idle"))
            self._tool_count = state.get("tool_count", 0)
            self._phase_history = state.get("phase_history", [])
            for g_data in state.get("goals", []):
                goal = Goal(
                    id=g_data["id"],
                    description=g_data["description"],
                    status=g_data.get("status", "active"),
                    created_at=g_data.get("created_at", 0),
                    completed_at=g_data.get("completed_at"),
                    progress_notes=g_data.get("progress_notes", []),
                    phase_when_created=g_data.get("phase_when_created", "idle"),
                )
                self.goals.append(goal)
        except (json.JSONDecodeError, OSError, KeyError):
            pass  # Start fresh if state is corrupted
