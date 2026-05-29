"""
Session Memory — Cross-session learning and knowledge transfer.

This module bridges the gap between per-session state (lost on exit) and
long-term persistent profiles (author_profile, voice_profile). It provides:

1. Session Journal: End-of-session summary of what happened, what worked,
   what failed, and what was learned — persisted for next session startup.
2. Pattern Memory: Learned tool sequence patterns that led to good/bad outcomes.
3. Preference Inference: Detects implicit preferences from user behavior
   (e.g., user always edits certain patterns → record as preference).
4. Session Context Loader: At startup, loads the most recent journal entry
   to give the agent continuity between sessions.

Design principles:
- Append-only journal (never rewrite history)
- Bounded size (max N entries, oldest dropped)
- Fast startup loading (only load most recent + summary)
- Non-blocking writes (errors in memory don't crash the agent)
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


MAX_JOURNAL_ENTRIES = 20  # Keep last N session journals
MAX_PATTERNS = 50         # Keep last N learned patterns
MAX_IMPLICIT_PREFS = 30   # Keep last N implicit preferences


@dataclass
class SessionJournal:
    """Summary of a single session's activity and learning."""
    session_id: str                    # Timestamp-based ID
    started_at: str                    # ISO timestamp
    ended_at: str = ""                 # ISO timestamp (filled at session end)
    paper_title: str = ""              # Which paper was worked on
    goal_description: str = ""         # Primary goal for this session
    goal_achieved: bool = False        # Whether the goal was completed
    tools_used: dict = field(default_factory=dict)    # tool_name → call_count
    errors_encountered: list = field(default_factory=list)  # Brief error descriptions
    quality_delta: float = 0.0         # Score improvement during session
    key_decisions: list = field(default_factory=list)  # Important decisions made
    lessons_learned: list = field(default_factory=list)  # What to do differently
    next_session_hints: list = field(default_factory=list)  # Suggestions for next time


@dataclass
class ToolPattern:
    """A learned tool sequence pattern and its outcome."""
    pattern_id: str
    tool_sequence: list[str]           # Ordered list of tool names
    context: str                       # When this pattern is useful
    outcome: str                       # "positive" | "negative" | "neutral"
    score_delta: float = 0.0           # Quality improvement from this pattern
    usage_count: int = 1               # Times this pattern has been used
    last_used: str = ""                # ISO timestamp
    notes: str = ""                    # Why this pattern works/fails


@dataclass
class ImplicitPreference:
    """A preference inferred from user behavior (not explicitly stated)."""
    pref_id: str
    category: str                      # "word_choice" | "structure" | "tone" | "formatting"
    original_pattern: str              # What the agent/AI produced
    user_replacement: str              # What the user changed it to
    confidence: float = 0.5            # 0-1, increases with repeated observations
    observation_count: int = 1         # Times we've seen this pattern
    first_seen: str = ""               # ISO timestamp
    last_seen: str = ""                # ISO timestamp


class SessionMemory:
    """Cross-session learning memory.

    Provides continuity between sessions by:
    1. Journaling what happened each session
    2. Learning which tool sequences work
    3. Inferring preferences from user edits
    4. Loading context at startup for seamless continuation

    Usage:
        memory = SessionMemory(workspace_path)
        # At startup:
        context = memory.get_startup_context()
        # During session:
        memory.record_tool_usage(tool_name)
        memory.record_error(tool_name, error_msg)
        memory.observe_user_edit(original, edited)
        memory.record_tool_sequence(sequence, outcome, score_delta)
        # At session end:
        memory.end_session(goal_achieved, lessons, hints)
    """

    def __init__(self, workspace: Path):
        self._workspace = workspace
        self._memory_dir = workspace / "session_memory"
        self._memory_dir.mkdir(parents=True, exist_ok=True)

        # File paths
        self._journal_path = self._memory_dir / "journals.json"
        self._patterns_path = self._memory_dir / "tool_patterns.json"
        self._prefs_path = self._memory_dir / "implicit_preferences.json"

        # Load existing data
        self._journals: list[SessionJournal] = self._load_journals()
        self._patterns: list[ToolPattern] = self._load_patterns()
        self._preferences: list[ImplicitPreference] = self._load_preferences()

        # Current session state
        self._current_session: Optional[SessionJournal] = None
        self._current_tool_sequence: list[str] = []

    # ─── Startup ───────────────────────────────────────────────────────

    def start_session(self, goal: str = "", paper_title: str = "") -> str:
        """Begin a new session. Returns session_id."""
        session_id = f"session_{int(time.time())}"
        self._current_session = SessionJournal(
            session_id=session_id,
            started_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            paper_title=paper_title,
            goal_description=goal,
        )
        self._current_tool_sequence = []
        return session_id

    def get_startup_context(self) -> str:
        """Generate context injection for session start.

        Provides the agent with memory of previous sessions to maintain
        continuity and apply lessons learned.
        """
        parts = []

        # Most recent session journal
        if self._journals:
            last = self._journals[-1]
            parts.append("[Previous Session Summary]")
            if last.paper_title:
                parts.append(f"Paper: {last.paper_title}")
            if last.goal_description:
                parts.append(f"Goal: {last.goal_description} "
                             f"({'achieved' if last.goal_achieved else 'incomplete'})")
            if last.lessons_learned:
                parts.append("Lessons: " + "; ".join(last.lessons_learned[:3]))
            if last.next_session_hints:
                parts.append("Recommendations for this session: " +
                             "; ".join(last.next_session_hints[:3]))

        # Top positive tool patterns
        positive_patterns = [p for p in self._patterns if p.outcome == "positive"]
        positive_patterns.sort(key=lambda p: p.usage_count * p.score_delta, reverse=True)
        if positive_patterns[:3]:
            parts.append("\n[Effective Tool Patterns]")
            for p in positive_patterns[:3]:
                parts.append(f"• {' → '.join(p.tool_sequence)} "
                             f"(context: {p.context}, score +{p.score_delta:.1f})")

        # High-confidence implicit preferences
        strong_prefs = [p for p in self._preferences if p.confidence >= 0.7]
        if strong_prefs:
            parts.append("\n[Learned User Preferences]")
            for pref in strong_prefs[:5]:
                parts.append(
                    f"• [{pref.category}] \"{pref.original_pattern}\" → "
                    f"\"{pref.user_replacement}\" (seen {pref.observation_count}x)"
                )

        return "\n".join(parts) if parts else ""

    # ─── Recording (During Session) ───────────────────────────────────

    def record_tool_usage(self, tool_name: str):
        """Record a tool being called in this session."""
        if not self._current_session:
            return
        counts = self._current_session.tools_used
        counts[tool_name] = counts.get(tool_name, 0) + 1
        self._current_tool_sequence.append(tool_name)
        # Keep sequence bounded
        if len(self._current_tool_sequence) > 20:
            self._current_tool_sequence = self._current_tool_sequence[-20:]

    def record_error(self, tool_name: str, error_msg: str):
        """Record an error that occurred during the session."""
        if not self._current_session:
            return
        brief = f"{tool_name}: {error_msg[:100]}"
        self._current_session.errors_encountered.append(brief)

    def record_decision(self, decision: str):
        """Record a key decision made during the session."""
        if not self._current_session:
            return
        self._current_session.key_decisions.append(decision[:200])

    def record_quality_delta(self, delta: float):
        """Record quality improvement in this session."""
        if not self._current_session:
            return
        self._current_session.quality_delta += delta

    def record_tool_sequence(self, sequence: list[str], outcome: str,
                             score_delta: float = 0.0, context: str = "",
                             notes: str = ""):
        """Record a tool sequence pattern and its outcome.

        Called when a meaningful sequence of tools completes with
        a clear positive or negative outcome.
        """
        # Check if we already have this pattern
        seq_key = "→".join(sequence)
        existing = next(
            (p for p in self._patterns if "→".join(p.tool_sequence) == seq_key),
            None
        )

        if existing:
            # Update existing pattern
            existing.usage_count += 1
            existing.last_used = time.strftime("%Y-%m-%dT%H:%M:%S")
            # Blend score delta (weighted average)
            existing.score_delta = (
                existing.score_delta * (existing.usage_count - 1) + score_delta
            ) / existing.usage_count
            # Outcome: if mixed, mark neutral
            if existing.outcome != outcome:
                existing.outcome = "neutral"
            if notes:
                existing.notes = notes
        else:
            # New pattern
            pattern = ToolPattern(
                pattern_id=f"pat_{int(time.time())}_{len(self._patterns)}",
                tool_sequence=sequence,
                context=context or "general",
                outcome=outcome,
                score_delta=score_delta,
                usage_count=1,
                last_used=time.strftime("%Y-%m-%dT%H:%M:%S"),
                notes=notes,
            )
            self._patterns.append(pattern)
            # Trim to max size
            if len(self._patterns) > MAX_PATTERNS:
                # Remove least-used patterns
                self._patterns.sort(key=lambda p: p.usage_count)
                self._patterns = self._patterns[-MAX_PATTERNS:]

        self._save_patterns()

    def observe_user_edit(self, original: str, edited: str):
        """Observe a user's manual edit to infer preferences.

        Called when the user modifies agent output. Extracts
        word-level or phrase-level substitutions.
        """
        if not original or not edited or original == edited:
            return

        # Simple diff: find changed words/phrases
        # For now, detect single-word substitutions in short texts
        orig_words = original.split()
        edit_words = edited.split()

        if len(orig_words) != len(edit_words):
            # Length changed — structural edit, harder to learn from
            return

        substitutions = []
        for ow, ew in zip(orig_words, edit_words):
            if ow.lower() != ew.lower() and len(ow) > 2 and len(ew) > 2:
                substitutions.append((ow, ew))

        # Only learn from clean single-substitution edits
        if len(substitutions) == 1:
            orig_word, new_word = substitutions[0]
            self._record_implicit_preference(
                category="word_choice",
                original_pattern=orig_word,
                user_replacement=new_word,
            )

    def _record_implicit_preference(self, category: str, original_pattern: str,
                                     user_replacement: str):
        """Record or strengthen an implicit preference."""
        # Check if we've seen this substitution before
        existing = next(
            (p for p in self._preferences
             if p.original_pattern.lower() == original_pattern.lower()
             and p.user_replacement.lower() == user_replacement.lower()),
            None
        )

        if existing:
            existing.observation_count += 1
            existing.last_seen = time.strftime("%Y-%m-%dT%H:%M:%S")
            # Confidence increases with observations (asymptotic to 1.0)
            existing.confidence = min(0.95, 1 - (1 / (existing.observation_count + 1)))
        else:
            pref = ImplicitPreference(
                pref_id=f"pref_{int(time.time())}_{len(self._preferences)}",
                category=category,
                original_pattern=original_pattern,
                user_replacement=user_replacement,
                confidence=0.3,  # Low initial confidence
                observation_count=1,
                first_seen=time.strftime("%Y-%m-%dT%H:%M:%S"),
                last_seen=time.strftime("%Y-%m-%dT%H:%M:%S"),
            )
            self._preferences.append(pref)
            # Trim to max
            if len(self._preferences) > MAX_IMPLICIT_PREFS:
                # Remove lowest confidence
                self._preferences.sort(key=lambda p: p.confidence)
                self._preferences = self._preferences[-MAX_IMPLICIT_PREFS:]

        self._save_preferences()

    # ─── Session End ──────────────────────────────────────────────────

    def end_session(self, goal_achieved: bool = False,
                    lessons: list[str] = None,
                    hints: list[str] = None):
        """Finalize the current session and persist journal.

        Args:
            goal_achieved: Whether the session's primary goal was met
            lessons: Things learned this session (for future reference)
            hints: Suggestions for next session's startup
        """
        if not self._current_session:
            return

        self._current_session.ended_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        self._current_session.goal_achieved = goal_achieved
        if lessons:
            self._current_session.lessons_learned = lessons
        if hints:
            self._current_session.next_session_hints = hints

        # Auto-generate lessons from errors if none provided
        if not self._current_session.lessons_learned and self._current_session.errors_encountered:
            self._current_session.lessons_learned = [
                f"Encountered {len(self._current_session.errors_encountered)} errors; "
                "consider checking prerequisites before tool calls"
            ]

        # Record current tool sequence as a pattern if meaningful
        if len(self._current_tool_sequence) >= 3 and goal_achieved:
            # Deduplicate consecutive same-tool calls
            deduped = []
            for t in self._current_tool_sequence:
                if not deduped or deduped[-1] != t:
                    deduped.append(t)
            if 3 <= len(deduped) <= 10:
                self.record_tool_sequence(
                    sequence=deduped,
                    outcome="positive" if goal_achieved else "neutral",
                    score_delta=self._current_session.quality_delta,
                    context=self._current_session.goal_description[:100],
                )

        # Save journal
        self._journals.append(self._current_session)
        if len(self._journals) > MAX_JOURNAL_ENTRIES:
            self._journals = self._journals[-MAX_JOURNAL_ENTRIES:]
        self._save_journals()
        self._current_session = None

    # ─── Query Methods ────────────────────────────────────────────────

    def get_effective_patterns(self, context: str = "") -> list[ToolPattern]:
        """Get tool patterns relevant to current context."""
        positive = [p for p in self._patterns if p.outcome == "positive"]
        if context:
            # Simple keyword matching
            context_lower = context.lower()
            relevant = [p for p in positive if context_lower in p.context.lower()
                        or any(context_lower in t.lower() for t in p.tool_sequence)]
            if relevant:
                return sorted(relevant, key=lambda p: p.score_delta, reverse=True)[:5]
        return sorted(positive, key=lambda p: p.usage_count * p.score_delta, reverse=True)[:5]

    def get_preferences_for_prompt(self) -> str:
        """Format high-confidence preferences for prompt injection."""
        strong = [p for p in self._preferences if p.confidence >= 0.6]
        if not strong:
            return ""
        lines = ["[Learned Preferences (apply automatically)]"]
        for p in strong[:10]:
            lines.append(f"• Prefer \"{p.user_replacement}\" over \"{p.original_pattern}\" "
                         f"({p.category}, confidence {p.confidence:.0%})")
        return "\n".join(lines)

    def get_session_count(self) -> int:
        """Return number of recorded sessions."""
        return len(self._journals)

    def get_memory_summary(self) -> str:
        """Return a brief summary of memory state for session_status."""
        return (
            f"Memory: {len(self._journals)} sessions, "
            f"{len(self._patterns)} patterns, "
            f"{len(self._preferences)} preferences"
        )

    # ─── Persistence ──────────────────────────────────────────────────

    def _load_journals(self) -> list[SessionJournal]:
        if not self._journal_path.exists():
            return []
        try:
            data = json.loads(self._journal_path.read_text(encoding="utf-8"))
            return [SessionJournal(**entry) for entry in data]
        except (json.JSONDecodeError, TypeError, OSError):
            return []

    def _save_journals(self):
        try:
            data = [asdict(j) for j in self._journals]
            self._journal_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
        except OSError:
            pass  # Non-critical: don't crash if write fails

    def _load_patterns(self) -> list[ToolPattern]:
        if not self._patterns_path.exists():
            return []
        try:
            data = json.loads(self._patterns_path.read_text(encoding="utf-8"))
            return [ToolPattern(**entry) for entry in data]
        except (json.JSONDecodeError, TypeError, OSError):
            return []

    def _save_patterns(self):
        try:
            data = [asdict(p) for p in self._patterns]
            self._patterns_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
        except OSError:
            pass

    def _load_preferences(self) -> list[ImplicitPreference]:
        if not self._prefs_path.exists():
            return []
        try:
            data = json.loads(self._prefs_path.read_text(encoding="utf-8"))
            return [ImplicitPreference(**entry) for entry in data]
        except (json.JSONDecodeError, TypeError, OSError):
            return []

    def _save_preferences(self):
        try:
            data = [asdict(p) for p in self._preferences]
            self._prefs_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
        except OSError:
            pass
