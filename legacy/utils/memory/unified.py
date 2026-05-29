"""
utils/memory/unified.py — Unified 3-tier memory abstraction.

Replaces the previous dual-island architecture (SQLite MemoryStore + JSON SessionMemory)
with a single coherent interface. All memory types are now classified into three tiers:

    IDENTITY  — Slow-decay (half-life ~90 days). User preferences, field knowledge,
                style patterns. Rarely changes, survives across projects.
    PROJECT   — Medium-decay (half-life ~14 days). Paper insights, review patterns,
                error lessons. Bound to current project/paper lifecycle.
    EPHEMERAL — Fast-decay (half-life ~2 days). Session notes, tool usage stats,
                transient context. Quickly fades unless reinforced.

The tier determines default decay behavior:
- `freshness_weight` is computed from age + tier half-life
- Entries below freshness threshold (0.3) become "stale" and are deprioritized
- "Challenge" mechanism: stale entries are surfaced once with low weight before purge

Migration path:
    1. New schema adds `memory_tier` column to `memories` table
    2. Existing entries are classified by MemoryType → MemoryTier mapping
    3. SessionMemory's ToolPattern & ImplicitPreference become SQLite records
    4. AuthorProfile data is referenced (not moved) via a thin adapter

Design principles:
    - Zero data loss during migration
    - Existing code paths (integration.py, meta_planner.py) work unchanged
    - UnifiedMemory is the new recommended entry point for all memory operations
"""

from __future__ import annotations

import json
import math
import time
import uuid
from enum import Enum
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from .models import MemoryEntry, MemoryType, PaperMemory, SessionSummary
from .store import MemoryStore, get_memory_store


# ============================================================
# Tier Classification
# ============================================================

class MemoryTier(str, Enum):
    """Three-tier memory classification with distinct decay profiles."""
    IDENTITY = "identity"     # User prefs, field knowledge (half-life 90d)
    PROJECT = "project"       # Paper insights, patterns (half-life 14d)
    EPHEMERAL = "ephemeral"   # Session notes, tool usage (half-life 2d)


# Half-life in seconds for each tier
TIER_HALF_LIFE: Dict[MemoryTier, float] = {
    MemoryTier.IDENTITY: 90 * 86400,    # 90 days
    MemoryTier.PROJECT: 14 * 86400,     # 14 days
    MemoryTier.EPHEMERAL: 2 * 86400,    # 2 days
}

# Default MemoryType → MemoryTier mapping
TYPE_TO_TIER: Dict[MemoryType, MemoryTier] = {
    MemoryType.USER_PREFERENCE: MemoryTier.IDENTITY,
    MemoryType.FIELD_KNOWLEDGE: MemoryTier.IDENTITY,
    MemoryType.PAPER_INSIGHT: MemoryTier.PROJECT,
    MemoryType.REVIEW_PATTERN: MemoryTier.PROJECT,
    MemoryType.ERROR_LESSON: MemoryTier.PROJECT,
    MemoryType.TOOL_USAGE: MemoryTier.EPHEMERAL,
    MemoryType.SESSION_NOTE: MemoryTier.EPHEMERAL,
}

# Freshness threshold — below this, entry is "stale"
STALENESS_THRESHOLD = 0.3


# ============================================================
# Extended MemoryEntry with tier and decay
# ============================================================

@dataclass
class UnifiedEntry:
    """Extended memory entry with tier and freshness semantics.

    Wraps MemoryEntry and adds:
    - tier: which decay profile governs this entry
    - freshness_weight: current relevance score (0-1)
    - reinforcement_count: times this entry was re-confirmed/accessed
    - challenged: whether the stale-challenge has been issued
    """
    entry: MemoryEntry
    tier: MemoryTier
    reinforcement_count: int = 0
    challenged: bool = False

    @property
    def freshness_weight(self) -> float:
        """Compute current freshness based on age and tier half-life.

        Uses exponential decay: w = exp(-ln(2) * age / half_life)
        Access reinforcement slows decay by resetting the clock partially.
        """
        half_life = TIER_HALF_LIFE[self.tier]
        age_seconds = time.time() - self.entry.updated_at

        # Reinforcement bonus: each access effectively shaves 10% off age
        effective_age = age_seconds * (0.9 ** self.reinforcement_count)

        return math.exp(-math.log(2) * effective_age / half_life)

    @property
    def is_stale(self) -> bool:
        return self.freshness_weight < STALENESS_THRESHOLD

    @property
    def is_expired(self) -> bool:
        """Truly expired: stale AND already challenged."""
        return self.is_stale and self.challenged


# ============================================================
# Unified Memory Interface
# ============================================================

class UnifiedMemory:
    """Single entry point for all memory operations.

    Combines:
    - MemoryStore (SQLite) for persistent structured memories
    - Tier-based decay for automatic relevance management
    - SessionMemory-style pattern learning (now in SQLite)
    - AuthorProfile awareness (thin read adapter)

    Usage:
        mem = UnifiedMemory()

        # Store a memory
        mem.remember("User prefers active voice", MemoryType.USER_PREFERENCE)

        # Recall with tier-aware freshness
        results = mem.recall("voice preference", budget=5)

        # Get context injection for prompts
        context = mem.get_context_for_phase("review")
    """

    def __init__(self, db_path: Optional[str] = None, workspace: Optional[Path] = None):
        """Initialize unified memory.

        Args:
            db_path: Custom SQLite path (default: .cache/memory.db)
            workspace: Workspace root for loading legacy session_memory data
        """
        if db_path:
            # Direct instantiation with explicit path (e.g., in tests)
            self._store = MemoryStore(db_path)
        else:
            # Use the global singleton for production use
            self._store = get_memory_store()
        self._workspace = workspace or Path.cwd()
        self._ensure_schema_v2()

    # ─── Schema Migration ──────────────────────────────────────────────

    def _ensure_schema_v2(self):
        """Add tier-related columns if not present (non-destructive)."""
        conn = self._store._conn

        # Check if memory_tier column exists
        cursor = conn.execute("PRAGMA table_info(memories)")
        columns = {row[1] for row in cursor.fetchall()}

        if "memory_tier" not in columns:
            conn.execute(
                "ALTER TABLE memories ADD COLUMN memory_tier TEXT DEFAULT 'project'"
            )
            conn.execute(
                "ALTER TABLE memories ADD COLUMN reinforcement_count INTEGER DEFAULT 0"
            )
            conn.execute(
                "ALTER TABLE memories ADD COLUMN challenged INTEGER DEFAULT 0"
            )
            conn.commit()

            # Backfill existing entries based on TYPE_TO_TIER mapping
            for mem_type, tier in TYPE_TO_TIER.items():
                conn.execute(
                    "UPDATE memories SET memory_tier = ? WHERE memory_type = ?",
                    (tier.value, mem_type.value)
                )
            conn.commit()

        # Create tool_patterns table (migrated from JSON)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tool_patterns (
                pattern_id TEXT PRIMARY KEY,
                tool_sequence TEXT NOT NULL,
                context TEXT DEFAULT '',
                outcome TEXT DEFAULT 'neutral',
                score_delta REAL DEFAULT 0.0,
                usage_count INTEGER DEFAULT 1,
                last_used REAL NOT NULL,
                notes TEXT DEFAULT ''
            )
        """)

        # Create implicit_preferences table (migrated from JSON)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS implicit_preferences (
                pref_id TEXT PRIMARY KEY,
                category TEXT NOT NULL,
                original_pattern TEXT NOT NULL,
                user_replacement TEXT NOT NULL,
                confidence REAL DEFAULT 0.3,
                observation_count INTEGER DEFAULT 1,
                first_seen REAL NOT NULL,
                last_seen REAL NOT NULL
            )
        """)

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_memories_tier ON memories(memory_tier)"
        )
        conn.commit()

    # ─── Core Operations ───────────────────────────────────────────────

    def remember(
        self,
        content: str,
        memory_type: MemoryType,
        context: str = "",
        tags: Optional[List[str]] = None,
        confidence: float = 1.0,
        tier: Optional[MemoryTier] = None,
        metadata: Optional[Dict[str, str]] = None,
    ) -> str:
        """Store a new memory with automatic tier classification.

        Args:
            content: The information to remember
            memory_type: Semantic type category
            context: What triggered this memory
            tags: Searchable tags
            confidence: Reliability score (0-1)
            tier: Override auto-tier (default: inferred from memory_type)
            metadata: Additional key-value pairs

        Returns:
            Entry ID for future reference
        """
        resolved_tier = tier or TYPE_TO_TIER.get(memory_type, MemoryTier.PROJECT)

        entry = MemoryEntry(
            id=str(uuid.uuid4())[:12],
            memory_type=memory_type,
            content=content,
            context=context,
            tags=tags or [],
            confidence=confidence,
            metadata=metadata or {},
        )

        entry_id = self._store.save(entry)

        # Set tier in the extended column
        self._store._conn.execute(
            "UPDATE memories SET memory_tier = ? WHERE id = ?",
            (resolved_tier.value, entry_id)
        )
        self._store._conn.commit()

        return entry_id

    def recall(
        self,
        query: str,
        memory_type: Optional[MemoryType] = None,
        tier: Optional[MemoryTier] = None,
        tags: Optional[List[str]] = None,
        budget: int = 10,
        include_stale: bool = False,
    ) -> List[UnifiedEntry]:
        """Recall memories ranked by freshness-weighted relevance.

        Args:
            query: Search keywords
            memory_type: Filter by type
            tier: Filter by tier
            tags: Filter by tags
            budget: Maximum entries to return
            include_stale: Whether to include entries below freshness threshold
            
        Returns:
            List of UnifiedEntry, sorted by freshness_weight descending
        """
        # Get raw entries from store (fetch more than budget for filtering)
        raw_entries = self._store.search(
            query, memory_type=memory_type, tags=tags, limit=budget * 3
        )

        # Wrap with tier information and compute freshness
        unified = []
        for entry in raw_entries:
            entry_tier = self._get_entry_tier(entry)
            if tier and entry_tier != tier:
                continue

            reinforcement = self._get_reinforcement_count(entry.id)
            challenged = self._get_challenged(entry.id)

            ue = UnifiedEntry(
                entry=entry,
                tier=entry_tier,
                reinforcement_count=reinforcement,
                challenged=challenged,
            )

            if include_stale or not ue.is_stale:
                unified.append(ue)

        # Sort by freshness weight (higher = more relevant)
        unified.sort(key=lambda u: u.freshness_weight, reverse=True)

        return unified[:budget]

    def reinforce(self, entry_id: str) -> None:
        """Reinforce a memory (accessed and confirmed useful).

        Resets effective age partially, slowing decay.
        """
        conn = self._store._conn
        conn.execute(
            "UPDATE memories SET reinforcement_count = reinforcement_count + 1, "
            "updated_at = ? WHERE id = ?",
            (time.time(), entry_id)
        )
        conn.commit()

    def challenge_stale(self, limit: int = 5) -> List[UnifiedEntry]:
        """Surface stale entries for one final relevance check.

        Returns unchallenged stale entries. After this call, they are
        marked as challenged. If not reinforced, they'll be purged on
        next cleanup.
        """
        conn = self._store._conn
        # Fetch candidates directly without triggering touch() via store.get()
        rows = conn.execute(
            """SELECT id, memory_type, content, context, tags, confidence,
                      access_count, created_at, updated_at, expires_at, metadata,
                      memory_tier, reinforcement_count
               FROM memories
               WHERE challenged = 0
               AND memory_tier != 'identity'
               ORDER BY updated_at ASC LIMIT ?""",
            (limit * 3,)
        ).fetchall()

        stale_entries = []
        for row in rows:
            entry = MemoryEntry(
                id=row[0],
                memory_type=MemoryType(row[1]),
                content=row[2],
                context=row[3],
                tags=json.loads(row[4]) if row[4] else [],
                confidence=row[5],
                access_count=row[6],
                created_at=row[7],
                updated_at=row[8],
                expires_at=row[9],
                metadata=json.loads(row[10]) if row[10] else {},
            )
            tier_str = row[11]
            tier = MemoryTier(tier_str) if tier_str in [t.value for t in MemoryTier] else MemoryTier.PROJECT
            reinforcement = row[12] or 0

            ue = UnifiedEntry(entry=entry, tier=tier,
                              reinforcement_count=reinforcement, challenged=False)
            if ue.is_stale:
                stale_entries.append(ue)
                conn.execute(
                    "UPDATE memories SET challenged = 1 WHERE id = ?",
                    (entry.id,)
                )

        conn.commit()
        return stale_entries[:limit]

    def purge_expired(self) -> int:
        """Remove entries that are stale AND already challenged.

        Returns count of purged entries.
        """
        conn = self._store._conn

        # Find challenged stale entries
        rows = conn.execute(
            "SELECT id, memory_tier, updated_at, reinforcement_count FROM memories "
            "WHERE challenged = 1"
        ).fetchall()

        purged = 0
        for row in rows:
            entry_id, tier_str, updated_at, reinforcement = row
            tier = MemoryTier(tier_str) if tier_str in [t.value for t in MemoryTier] else MemoryTier.PROJECT
            half_life = TIER_HALF_LIFE[tier]
            age = time.time() - updated_at
            effective_age = age * (0.9 ** reinforcement)
            freshness = math.exp(-math.log(2) * effective_age / half_life)

            if freshness < STALENESS_THRESHOLD:
                conn.execute("DELETE FROM memories WHERE id = ?", (entry_id,))
                purged += 1

        conn.commit()

        # Also run basic expiry cleanup
        purged += self._store.cleanup_expired()

        return purged

    # ─── Pattern Learning (migrated from SessionMemory) ─────────────────

    def record_tool_pattern(
        self,
        tool_sequence: List[str],
        outcome: str,
        score_delta: float = 0.0,
        context: str = "",
        notes: str = "",
    ) -> None:
        """Record a tool usage pattern (moved from JSON to SQLite).

        Args:
            tool_sequence: Ordered list of tool names
            outcome: "positive" | "negative" | "neutral"
            score_delta: Quality score change
            context: When this pattern applies
            notes: Additional observations
        """
        conn = self._store._conn
        seq_key = json.dumps(tool_sequence)

        # Check existing
        row = conn.execute(
            "SELECT pattern_id, usage_count, score_delta, outcome FROM tool_patterns "
            "WHERE tool_sequence = ?", (seq_key,)
        ).fetchone()

        if row:
            pattern_id, usage_count, old_delta, old_outcome = row
            new_count = usage_count + 1
            blended_delta = (old_delta * usage_count + score_delta) / new_count
            new_outcome = outcome if old_outcome == outcome else "neutral"
            conn.execute(
                "UPDATE tool_patterns SET usage_count = ?, score_delta = ?, "
                "outcome = ?, last_used = ?, notes = ? WHERE pattern_id = ?",
                (new_count, blended_delta, new_outcome, time.time(),
                 notes or "", pattern_id)
            )
        else:
            pattern_id = f"pat_{uuid.uuid4().hex[:8]}"
            conn.execute(
                "INSERT INTO tool_patterns "
                "(pattern_id, tool_sequence, context, outcome, score_delta, "
                "usage_count, last_used, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (pattern_id, seq_key, context, outcome, score_delta,
                 1, time.time(), notes)
            )
        conn.commit()

    def get_effective_patterns(
        self, context: str = "", outcome: str = "positive", limit: int = 5
    ) -> List[Dict]:
        """Get tool patterns relevant to current context.

        Returns list of dicts with: tool_sequence, score_delta, usage_count, context, notes
        """
        conn = self._store._conn

        if context:
            # Try context-filtered first
            rows = conn.execute(
                "SELECT * FROM tool_patterns WHERE outcome = ? AND context LIKE ? "
                "ORDER BY usage_count * score_delta DESC LIMIT ?",
                (outcome, f"%{context}%", limit)
            ).fetchall()
            if rows:
                return [self._pattern_row_to_dict(r) for r in rows]

        # Fallback: top patterns by impact
        rows = conn.execute(
            "SELECT * FROM tool_patterns WHERE outcome = ? "
            "ORDER BY usage_count * score_delta DESC LIMIT ?",
            (outcome, limit)
        ).fetchall()
        return [self._pattern_row_to_dict(r) for r in rows]

    # ─── Preference Learning (migrated from SessionMemory) ───────────────

    def observe_preference(
        self,
        category: str,
        original_pattern: str,
        user_replacement: str,
    ) -> None:
        """Record or strengthen an implicit preference.

        Args:
            category: "word_choice" | "structure" | "tone" | "formatting"
            original_pattern: What AI produced
            user_replacement: What user changed it to
        """
        conn = self._store._conn

        row = conn.execute(
            "SELECT pref_id, observation_count FROM implicit_preferences "
            "WHERE LOWER(original_pattern) = LOWER(?) AND LOWER(user_replacement) = LOWER(?)",
            (original_pattern, user_replacement)
        ).fetchone()

        if row:
            pref_id, count = row
            new_count = count + 1
            confidence = min(0.95, 1 - (1 / (new_count + 1)))
            conn.execute(
                "UPDATE implicit_preferences SET observation_count = ?, "
                "confidence = ?, last_seen = ? WHERE pref_id = ?",
                (new_count, confidence, time.time(), pref_id)
            )
        else:
            pref_id = f"pref_{uuid.uuid4().hex[:8]}"
            conn.execute(
                "INSERT INTO implicit_preferences "
                "(pref_id, category, original_pattern, user_replacement, "
                "confidence, observation_count, first_seen, last_seen) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (pref_id, category, original_pattern, user_replacement,
                 0.3, 1, time.time(), time.time())
            )
        conn.commit()

    def get_preferences(self, min_confidence: float = 0.6, limit: int = 10) -> List[Dict]:
        """Get high-confidence user preferences for prompt injection."""
        conn = self._store._conn
        rows = conn.execute(
            "SELECT * FROM implicit_preferences WHERE confidence >= ? "
            "ORDER BY confidence DESC, observation_count DESC LIMIT ?",
            (min_confidence, limit)
        ).fetchall()
        return [
            {
                "category": r[1],
                "original": r[2],
                "replacement": r[3],
                "confidence": r[4],
                "observations": r[5],
            }
            for r in rows
        ]

    # ─── Context Generation ────────────────────────────────────────────

    def get_context_for_phase(self, phase: str, budget_tokens: int = 500) -> str:
        """Generate context injection string for a given workflow phase.

        Selects memories most relevant to the current phase, respecting
        a rough token budget (estimated at 4 chars/token).

        Args:
            phase: Current workflow phase (e.g., "review", "rewrite", "deai")
            budget_tokens: Approximate token budget for context injection

        Returns:
            Formatted context string for prompt injection
        """
        char_budget = budget_tokens * 4
        parts = []

        # 1. Identity-tier preferences (always include, compact)
        prefs = self.get_preferences(min_confidence=0.6, limit=5)
        if prefs:
            pref_lines = ["[User Preferences]"]
            for p in prefs:
                pref_lines.append(
                    f"  \"{p['original']}\" → \"{p['replacement']}\" "
                    f"({p['category']}, {p['confidence']:.0%})"
                )
            parts.append("\n".join(pref_lines))

        # 2. Phase-relevant project memories
        phase_entries = self.recall(phase, tier=MemoryTier.PROJECT, budget=5)
        if phase_entries:
            entry_lines = [f"[Relevant Memories ({phase})]"]
            for ue in phase_entries:
                entry_lines.append(f"  • {ue.entry.content[:120]}")
            parts.append("\n".join(entry_lines))

        # 3. Effective tool patterns (for planning)
        patterns = self.get_effective_patterns(context=phase, limit=3)
        if patterns:
            pat_lines = ["[Effective Patterns]"]
            for p in patterns:
                seq = " → ".join(p["tool_sequence"])
                pat_lines.append(f"  {seq} (+{p['score_delta']:.1f})")
            parts.append("\n".join(pat_lines))

        # Assemble within budget
        result = "\n\n".join(parts)
        if len(result) > char_budget:
            result = result[:char_budget] + "\n  [truncated]"

        return result

    def memory_digest(self) -> Dict:
        """Return a summary of memory state for diagnostics.

        Returns dict with counts per tier, stale count, total entries.
        """
        conn = self._store._conn

        tier_counts = {}
        for tier in MemoryTier:
            row = conn.execute(
                "SELECT COUNT(*) FROM memories WHERE memory_tier = ?",
                (tier.value,)
            ).fetchone()
            tier_counts[tier.value] = row[0] if row else 0

        stale_row = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE challenged = 1"
        ).fetchone()

        pattern_row = conn.execute("SELECT COUNT(*) FROM tool_patterns").fetchone()
        pref_row = conn.execute("SELECT COUNT(*) FROM implicit_preferences").fetchone()

        return {
            "total_memories": sum(tier_counts.values()),
            "by_tier": tier_counts,
            "challenged_stale": stale_row[0] if stale_row else 0,
            "tool_patterns": pattern_row[0] if pattern_row else 0,
            "implicit_preferences": pref_row[0] if pref_row else 0,
            "papers": self._store._conn.execute(
                "SELECT COUNT(*) FROM paper_memories"
            ).fetchone()[0],
        }

    # ─── Compatibility Layer ───────────────────────────────────────────

    @property
    def store(self) -> MemoryStore:
        """Access the underlying MemoryStore for backward compatibility.

        Existing code using store.save_paper(), store.search() etc.
        continues to work through this property.
        """
        return self._store

    def get_startup_context(self) -> str:
        """Generate startup context (replaces SessionMemory.get_startup_context).

        Combines recent session notes, learned preferences, and
        effective patterns into a single context string.
        """
        parts = []

        # Recent session summary
        sessions = self._store.recent_sessions(limit=1)
        if sessions:
            s = sessions[0]
            parts.append("[Previous Session]")
            if s.notes:
                parts.append(f"  Notes: {s.notes[:200]}")
            if s.key_decisions:
                parts.append(f"  Decisions: {'; '.join(s.key_decisions[:3])}")

        # Preferences
        prefs = self.get_preferences(min_confidence=0.6, limit=5)
        if prefs:
            parts.append("[Learned Preferences]")
            for p in prefs:
                parts.append(
                    f"  Prefer \"{p['replacement']}\" over \"{p['original']}\" "
                    f"({p['category']})"
                )

        # Top patterns
        patterns = self.get_effective_patterns(limit=3)
        if patterns:
            parts.append("[Effective Tool Sequences]")
            for p in patterns:
                seq = " → ".join(p["tool_sequence"])
                parts.append(f"  {seq} (used {p['usage_count']}x, +{p['score_delta']:.1f})")

        return "\n".join(parts) if parts else ""

    # ─── Migration Helpers ─────────────────────────────────────────────

    def migrate_session_memory(self, session_memory_dir: Optional[Path] = None) -> Dict[str, int]:
        """Migrate JSON-based SessionMemory data into unified SQLite store.

        Reads tool_patterns.json and implicit_preferences.json from the
        workspace's session_memory/ directory and imports them.

        Returns dict with counts of migrated records.
        """
        sm_dir = session_memory_dir or (self._workspace / "session_memory")
        migrated = {"patterns": 0, "preferences": 0}

        # Migrate tool patterns
        patterns_file = sm_dir / "tool_patterns.json"
        if patterns_file.exists():
            try:
                data = json.loads(patterns_file.read_text(encoding="utf-8"))
                for entry in data:
                    self.record_tool_pattern(
                        tool_sequence=entry.get("tool_sequence", []),
                        outcome=entry.get("outcome", "neutral"),
                        score_delta=entry.get("score_delta", 0.0),
                        context=entry.get("context", ""),
                        notes=entry.get("notes", ""),
                    )
                    migrated["patterns"] += 1
            except (json.JSONDecodeError, OSError):
                pass

        # Migrate implicit preferences
        prefs_file = sm_dir / "implicit_preferences.json"
        if prefs_file.exists():
            try:
                data = json.loads(prefs_file.read_text(encoding="utf-8"))
                for entry in data:
                    self.observe_preference(
                        category=entry.get("category", "word_choice"),
                        original_pattern=entry.get("original_pattern", ""),
                        user_replacement=entry.get("user_replacement", ""),
                    )
                    migrated["preferences"] += 1
            except (json.JSONDecodeError, OSError):
                pass

        return migrated

    # ─── Private Helpers ───────────────────────────────────────────────

    def _get_entry_tier(self, entry: MemoryEntry) -> MemoryTier:
        """Get tier for an entry (from DB column or inferred from type)."""
        conn = self._store._conn
        row = conn.execute(
            "SELECT memory_tier FROM memories WHERE id = ?", (entry.id,)
        ).fetchone()
        if row and row[0]:
            try:
                return MemoryTier(row[0])
            except ValueError:
                pass
        # Fallback: infer from type
        return TYPE_TO_TIER.get(entry.memory_type, MemoryTier.PROJECT)

    def _get_reinforcement_count(self, entry_id: str) -> int:
        """Get reinforcement count from DB."""
        conn = self._store._conn
        row = conn.execute(
            "SELECT reinforcement_count FROM memories WHERE id = ?", (entry_id,)
        ).fetchone()
        return row[0] if row and row[0] else 0

    def _get_challenged(self, entry_id: str) -> bool:
        """Check if entry has been challenged."""
        conn = self._store._conn
        row = conn.execute(
            "SELECT challenged FROM memories WHERE id = ?", (entry_id,)
        ).fetchone()
        return bool(row[0]) if row else False

    def _pattern_row_to_dict(self, row) -> Dict:
        """Convert a tool_patterns row to dict."""
        return {
            "pattern_id": row[0],
            "tool_sequence": json.loads(row[1]),
            "context": row[2],
            "outcome": row[3],
            "score_delta": row[4],
            "usage_count": row[5],
            "last_used": row[6],
            "notes": row[7],
        }

    def close(self):
        """Close underlying store."""
        self._store.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


# ============================================================
# Module-level singleton
# ============================================================

_unified: Optional[UnifiedMemory] = None


def get_unified_memory(
    db_path: Optional[str] = None,
    workspace: Optional[Path] = None,
) -> UnifiedMemory:
    """Get or create the global UnifiedMemory singleton."""
    global _unified
    if _unified is None:
        _unified = UnifiedMemory(db_path=db_path, workspace=workspace)
    return _unified
