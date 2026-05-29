"""
utils/memory/ - Cross-session memory for ScholarAgent.

Provides persistent memory across agent sessions using SQLite.
Stores paper-level insights, user preferences, review history,
and learned patterns that improve over time.

Architecture (v2 — Unified 3-Tier):
- MemoryTier: IDENTITY / PROJECT / EPHEMERAL with distinct decay profiles
- UnifiedMemory: Single interface replacing the previous dual-island setup
- MemoryStore: SQLite-backed persistence layer (still available for direct use)
- Integration helpers: Non-blocking memory recording from review/rewrite pipelines
"""

from .models import MemoryEntry, MemoryType, PaperMemory, SessionSummary
from .store import MemoryStore, get_memory_store
from .unified import (
    MemoryTier,
    UnifiedEntry,
    UnifiedMemory,
    get_unified_memory,
    TIER_HALF_LIFE,
    TYPE_TO_TIER,
    STALENESS_THRESHOLD,
)

__all__ = [
    # Models
    "MemoryEntry",
    "MemoryType",
    "PaperMemory",
    "SessionSummary",
    # Store (legacy, still functional)
    "MemoryStore",
    "get_memory_store",
    # Unified (recommended)
    "MemoryTier",
    "UnifiedEntry",
    "UnifiedMemory",
    "get_unified_memory",
    "TIER_HALF_LIFE",
    "TYPE_TO_TIER",
    "STALENESS_THRESHOLD",
]
