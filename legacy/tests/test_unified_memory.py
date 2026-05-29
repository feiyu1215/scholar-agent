"""
Tests for utils/memory/unified.py — Unified 3-tier memory layer.

Covers:
- MemoryTier classification and mapping
- UnifiedMemory CRUD operations
- Freshness/decay computation
- Tool pattern recording and retrieval
- Implicit preference learning
- Migration from legacy JSON
- Challenge/purge lifecycle
- Context generation
"""

import json
import math
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from utils.memory.models import MemoryEntry, MemoryType
from utils.memory.unified import (
    MemoryTier,
    UnifiedEntry,
    UnifiedMemory,
    TIER_HALF_LIFE,
    TYPE_TO_TIER,
    STALENESS_THRESHOLD,
)


@pytest.fixture
def tmp_workspace(tmp_path):
    """Create a temporary workspace with session_memory dir."""
    sm_dir = tmp_path / "session_memory"
    sm_dir.mkdir()
    return tmp_path


@pytest.fixture
def memory(tmp_path):
    """Create a fresh UnifiedMemory instance with temp DB."""
    db_path = str(tmp_path / "test_memory.db")
    mem = UnifiedMemory(db_path=db_path, workspace=tmp_path)
    yield mem
    mem.close()


class TestMemoryTierMapping:
    """Test that MemoryType → MemoryTier mapping is correct."""

    def test_identity_tier_types(self):
        assert TYPE_TO_TIER[MemoryType.USER_PREFERENCE] == MemoryTier.IDENTITY
        assert TYPE_TO_TIER[MemoryType.FIELD_KNOWLEDGE] == MemoryTier.IDENTITY

    def test_project_tier_types(self):
        assert TYPE_TO_TIER[MemoryType.PAPER_INSIGHT] == MemoryTier.PROJECT
        assert TYPE_TO_TIER[MemoryType.REVIEW_PATTERN] == MemoryTier.PROJECT
        assert TYPE_TO_TIER[MemoryType.ERROR_LESSON] == MemoryTier.PROJECT

    def test_ephemeral_tier_types(self):
        assert TYPE_TO_TIER[MemoryType.TOOL_USAGE] == MemoryTier.EPHEMERAL
        assert TYPE_TO_TIER[MemoryType.SESSION_NOTE] == MemoryTier.EPHEMERAL

    def test_all_types_mapped(self):
        """Every MemoryType should have a tier mapping."""
        for mt in MemoryType:
            assert mt in TYPE_TO_TIER, f"{mt} not mapped to a tier"

    def test_half_life_ordering(self):
        """Identity > Project > Ephemeral in half-life."""
        assert TIER_HALF_LIFE[MemoryTier.IDENTITY] > TIER_HALF_LIFE[MemoryTier.PROJECT]
        assert TIER_HALF_LIFE[MemoryTier.PROJECT] > TIER_HALF_LIFE[MemoryTier.EPHEMERAL]


class TestUnifiedMemoryBasicOps:
    """Test basic remember/recall operations."""

    def test_remember_and_recall(self, memory):
        entry_id = memory.remember(
            "User prefers active voice in results sections",
            MemoryType.USER_PREFERENCE,
            context="review session",
            tags=["voice", "results"],
        )
        assert entry_id is not None
        assert len(entry_id) > 0

        results = memory.recall("active voice")
        assert len(results) > 0
        assert results[0].entry.content == "User prefers active voice in results sections"
        assert results[0].tier == MemoryTier.IDENTITY

    def test_remember_with_explicit_tier(self, memory):
        """Override default tier classification."""
        entry_id = memory.remember(
            "Temporary note about citation format",
            MemoryType.FIELD_KNOWLEDGE,
            tier=MemoryTier.EPHEMERAL,  # Override: normally IDENTITY
        )
        results = memory.recall("citation format")
        assert results[0].tier == MemoryTier.EPHEMERAL

    def test_recall_filters_by_tier(self, memory):
        memory.remember("Identity item", MemoryType.USER_PREFERENCE)
        memory.remember("Project item", MemoryType.PAPER_INSIGHT)
        memory.remember("Ephemeral item", MemoryType.SESSION_NOTE)

        identity_only = memory.recall("item", tier=MemoryTier.IDENTITY)
        assert all(r.tier == MemoryTier.IDENTITY for r in identity_only)

    def test_recall_budget_limits_results(self, memory):
        for i in range(20):
            memory.remember(f"Memory number {i}", MemoryType.SESSION_NOTE)

        results = memory.recall("Memory", budget=5)
        assert len(results) <= 5


class TestFreshnessDecay:
    """Test exponential decay computation."""

    def test_fresh_entry_has_high_weight(self, memory):
        entry_id = memory.remember("Just created", MemoryType.SESSION_NOTE)
        results = memory.recall("Just created")
        assert results[0].freshness_weight > 0.95

    def test_decay_formula_correctness(self):
        """Verify the decay math directly."""
        entry = MemoryEntry(
            id="test",
            memory_type=MemoryType.SESSION_NOTE,
            content="test",
            updated_at=time.time() - (2 * 86400),  # 2 days old
        )
        ue = UnifiedEntry(entry=entry, tier=MemoryTier.EPHEMERAL)
        # Half-life for EPHEMERAL is 2 days, so at exactly 2 days:
        # freshness = exp(-ln(2) * 2d / 2d) = exp(-ln(2)) = 0.5
        assert abs(ue.freshness_weight - 0.5) < 0.05

    def test_identity_tier_decays_slowly(self):
        """Identity entries should still be fresh after weeks."""
        entry = MemoryEntry(
            id="test",
            memory_type=MemoryType.USER_PREFERENCE,
            content="test",
            updated_at=time.time() - (30 * 86400),  # 30 days old
        )
        ue = UnifiedEntry(entry=entry, tier=MemoryTier.IDENTITY)
        # 30 days with 90-day half-life → should be ~79% fresh
        expected = math.exp(-math.log(2) * 30 / 90)
        assert abs(ue.freshness_weight - expected) < 0.01

    def test_reinforcement_slows_decay(self):
        """Reinforced entries should decay slower."""
        entry = MemoryEntry(
            id="test",
            memory_type=MemoryType.PAPER_INSIGHT,
            content="test",
            updated_at=time.time() - (14 * 86400),  # 14 days old
        )
        ue_normal = UnifiedEntry(entry=entry, tier=MemoryTier.PROJECT,
                                  reinforcement_count=0)
        ue_reinforced = UnifiedEntry(entry=entry, tier=MemoryTier.PROJECT,
                                      reinforcement_count=5)

        assert ue_reinforced.freshness_weight > ue_normal.freshness_weight

    def test_staleness_detection(self):
        """Entries below threshold are stale."""
        entry = MemoryEntry(
            id="test",
            memory_type=MemoryType.SESSION_NOTE,
            content="test",
            updated_at=time.time() - (10 * 86400),  # 10 days old ephemeral
        )
        ue = UnifiedEntry(entry=entry, tier=MemoryTier.EPHEMERAL)
        assert ue.is_stale  # 10 days with 2-day half-life → very stale


class TestReinforcement:
    """Test memory reinforcement mechanism."""

    def test_reinforce_increments_count(self, memory):
        entry_id = memory.remember("Important fact", MemoryType.FIELD_KNOWLEDGE)
        memory.reinforce(entry_id)
        memory.reinforce(entry_id)

        count = memory._get_reinforcement_count(entry_id)
        assert count == 2

    def test_reinforce_updates_timestamp(self, memory):
        entry_id = memory.remember("Old fact", MemoryType.FIELD_KNOWLEDGE)
        time.sleep(0.01)
        memory.reinforce(entry_id)

        entry = memory.store.get(entry_id)
        assert entry.updated_at > entry.created_at


class TestToolPatterns:
    """Test tool pattern learning."""

    def test_record_and_retrieve_pattern(self, memory):
        memory.record_tool_pattern(
            tool_sequence=["parse_paper", "review_paper", "rewrite_section"],
            outcome="positive",
            score_delta=2.5,
            context="full review workflow",
        )

        patterns = memory.get_effective_patterns(context="review")
        assert len(patterns) > 0
        assert patterns[0]["tool_sequence"] == ["parse_paper", "review_paper", "rewrite_section"]
        assert patterns[0]["score_delta"] == 2.5

    def test_pattern_usage_count_increments(self, memory):
        seq = ["tool_a", "tool_b"]
        memory.record_tool_pattern(seq, "positive", 1.0, "ctx")
        memory.record_tool_pattern(seq, "positive", 3.0, "ctx")

        patterns = memory.get_effective_patterns()
        assert patterns[0]["usage_count"] == 2
        # Score delta should be blended: (1.0 + 3.0) / 2 = 2.0
        assert abs(patterns[0]["score_delta"] - 2.0) < 0.01

    def test_mixed_outcome_becomes_neutral(self, memory):
        seq = ["x", "y"]
        memory.record_tool_pattern(seq, "positive", 1.0)
        memory.record_tool_pattern(seq, "negative", -1.0)

        patterns = memory.get_effective_patterns(outcome="neutral")
        assert len(patterns) > 0
        assert patterns[0]["outcome"] == "neutral"


class TestImplicitPreferences:
    """Test preference learning."""

    def test_observe_and_retrieve(self, memory):
        memory.observe_preference("word_choice", "utilize", "use")
        prefs = memory.get_preferences(min_confidence=0.0)
        assert len(prefs) > 0
        assert prefs[0]["original"] == "utilize"
        assert prefs[0]["replacement"] == "use"

    def test_repeated_observation_increases_confidence(self, memory):
        for _ in range(5):
            memory.observe_preference("word_choice", "furthermore", "also")

        prefs = memory.get_preferences(min_confidence=0.0)
        assert prefs[0]["confidence"] > 0.7
        assert prefs[0]["observations"] == 5

    def test_min_confidence_filter(self, memory):
        memory.observe_preference("tone", "indeed", "really")  # Low conf (0.3)
        prefs = memory.get_preferences(min_confidence=0.6)
        assert len(prefs) == 0


class TestChallengeAndPurge:
    """Test the stale challenge and purge lifecycle."""

    def test_challenge_stale_marks_entries(self, memory):
        # Create an old ephemeral entry
        entry_id = memory.remember("Old note", MemoryType.SESSION_NOTE)
        # Manually age it
        memory.store._conn.execute(
            "UPDATE memories SET updated_at = ? WHERE id = ?",
            (time.time() - 30 * 86400, entry_id)  # 30 days old
        )
        memory.store._conn.commit()

        stale = memory.challenge_stale(limit=5)
        assert len(stale) > 0
        # Verify it's now marked
        assert memory._get_challenged(entry_id)

    def test_purge_removes_challenged_stale(self, memory):
        entry_id = memory.remember("Doomed note", MemoryType.SESSION_NOTE)
        # Age it and mark as challenged
        memory.store._conn.execute(
            "UPDATE memories SET updated_at = ?, challenged = 1 WHERE id = ?",
            (time.time() - 30 * 86400, entry_id)
        )
        memory.store._conn.commit()

        purged = memory.purge_expired()
        assert purged >= 1

        # Entry should be gone
        assert memory.store.get(entry_id) is None


class TestMigration:
    """Test JSON → SQLite migration."""

    def test_migrate_patterns(self, tmp_workspace):
        sm_dir = tmp_workspace / "session_memory"
        patterns = [
            {
                "pattern_id": "pat_001",
                "tool_sequence": ["review_paper", "rewrite_section"],
                "context": "standard review",
                "outcome": "positive",
                "score_delta": 1.5,
                "usage_count": 3,
                "last_used": "2025-01-15T10:00:00",
                "notes": "Works well for methodology sections",
            }
        ]
        (sm_dir / "tool_patterns.json").write_text(
            json.dumps(patterns, ensure_ascii=False), encoding="utf-8"
        )

        db_path = str(tmp_workspace / "test.db")
        mem = UnifiedMemory(db_path=db_path, workspace=tmp_workspace)
        result = mem.migrate_session_memory(sm_dir)

        assert result["patterns"] == 1
        retrieved = mem.get_effective_patterns()
        assert len(retrieved) > 0
        assert retrieved[0]["tool_sequence"] == ["review_paper", "rewrite_section"]
        mem.close()

    def test_migrate_preferences(self, tmp_workspace):
        sm_dir = tmp_workspace / "session_memory"
        prefs = [
            {
                "pref_id": "pref_001",
                "category": "word_choice",
                "original_pattern": "utilize",
                "user_replacement": "use",
                "confidence": 0.8,
                "observation_count": 4,
                "first_seen": "2025-01-01T00:00:00",
                "last_seen": "2025-01-10T00:00:00",
            }
        ]
        (sm_dir / "implicit_preferences.json").write_text(
            json.dumps(prefs, ensure_ascii=False), encoding="utf-8"
        )

        db_path = str(tmp_workspace / "test.db")
        mem = UnifiedMemory(db_path=db_path, workspace=tmp_workspace)
        result = mem.migrate_session_memory(sm_dir)

        assert result["preferences"] == 1
        retrieved = mem.get_preferences(min_confidence=0.0)
        assert len(retrieved) > 0
        assert retrieved[0]["original"] == "utilize"
        mem.close()

    def test_migrate_idempotent(self, tmp_workspace):
        """Running migration twice doesn't create duplicates."""
        sm_dir = tmp_workspace / "session_memory"
        patterns = [{"pattern_id": "p1", "tool_sequence": ["a", "b"],
                     "context": "x", "outcome": "positive", "score_delta": 1.0,
                     "usage_count": 1, "last_used": "", "notes": ""}]
        (sm_dir / "tool_patterns.json").write_text(
            json.dumps(patterns), encoding="utf-8"
        )

        db_path = str(tmp_workspace / "test.db")
        mem = UnifiedMemory(db_path=db_path, workspace=tmp_workspace)
        mem.migrate_session_memory(sm_dir)
        mem.migrate_session_memory(sm_dir)  # Second time

        # Should merge, not duplicate
        retrieved = mem.get_effective_patterns()
        assert len(retrieved) == 1
        assert retrieved[0]["usage_count"] == 2  # Merged
        mem.close()


class TestContextGeneration:
    """Test context injection generation."""

    def test_get_context_for_phase(self, memory):
        memory.remember("Citation formatting is critical in economics",
                        MemoryType.REVIEW_PATTERN, tags=["review"])
        memory.observe_preference("word_choice", "significant", "substantial")
        memory.observe_preference("word_choice", "significant", "substantial")
        memory.observe_preference("word_choice", "significant", "substantial")
        memory.observe_preference("word_choice", "significant", "substantial")
        # Need enough observations for 0.6 confidence

        context = memory.get_context_for_phase("review")
        # Should contain something (may or may not hit all sections)
        assert isinstance(context, str)

    def test_memory_digest(self, memory):
        memory.remember("Test 1", MemoryType.USER_PREFERENCE)
        memory.remember("Test 2", MemoryType.SESSION_NOTE)
        memory.record_tool_pattern(["a", "b"], "positive", 1.0)

        digest = memory.memory_digest()
        assert digest["total_memories"] >= 2
        assert digest["tool_patterns"] >= 1
        assert "by_tier" in digest

    def test_get_startup_context(self, memory):
        # With no data, should return empty string
        ctx = memory.get_startup_context()
        assert isinstance(ctx, str)


class TestBackwardCompatibility:
    """Test that old code paths still work."""

    def test_store_property_accessible(self, memory):
        """UnifiedMemory.store gives access to underlying MemoryStore."""
        assert memory.store is not None
        # Can still use old-style operations
        from utils.memory.models import PaperMemory
        paper = PaperMemory(
            paper_id="test123",
            title="Test Paper",
            field="economics",
        )
        memory.store.save_paper(paper)
        retrieved = memory.store.get_paper("test123")
        assert retrieved is not None
        assert retrieved.title == "Test Paper"

    def test_schema_migration_is_nondestructive(self, tmp_path):
        """Creating UnifiedMemory on existing DB doesn't corrupt data."""
        db_path = str(tmp_path / "existing.db")

        # First: create with old-style MemoryStore
        from utils.memory.store import MemoryStore
        store = MemoryStore(db_path)
        store.save(MemoryEntry(
            id="legacy_1",
            memory_type=MemoryType.PAPER_INSIGHT,
            content="Legacy content that must survive",
        ))
        store.close()

        # Then: open with UnifiedMemory (triggers migration)
        mem = UnifiedMemory(db_path=db_path, workspace=tmp_path)
        entry = mem.store.get("legacy_1")
        assert entry is not None
        assert entry.content == "Legacy content that must survive"
        mem.close()
