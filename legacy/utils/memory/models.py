"""
utils/memory/models.py - Data models for cross-session memory.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field as dataclass_field
from enum import Enum
from typing import Dict, List, Optional


class MemoryType(str, Enum):
    """Categories of memorable information."""
    PAPER_INSIGHT = "paper_insight"       # Insights about a specific paper
    USER_PREFERENCE = "user_preference"   # User's style/preference patterns
    REVIEW_PATTERN = "review_pattern"     # Recurring review issues
    FIELD_KNOWLEDGE = "field_knowledge"   # Domain-specific knowledge
    TOOL_USAGE = "tool_usage"            # Effective tool usage patterns
    ERROR_LESSON = "error_lesson"        # Lessons from past errors
    SESSION_NOTE = "session_note"        # General session notes


@dataclass
class MemoryEntry:
    """A single memory entry."""
    id: str                         # Unique ID (auto-generated if empty)
    memory_type: MemoryType
    content: str                    # The actual memory content
    context: str = ""               # What triggered this memory
    tags: List[str] = dataclass_field(default_factory=list)
    confidence: float = 1.0         # How reliable (0-1)
    access_count: int = 0           # Times retrieved
    created_at: float = dataclass_field(default_factory=time.time)
    updated_at: float = dataclass_field(default_factory=time.time)
    expires_at: Optional[float] = None  # TTL (None = permanent)
    metadata: Dict[str, str] = dataclass_field(default_factory=dict)

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return time.time() > self.expires_at

    @property
    def age_days(self) -> float:
        return (time.time() - self.created_at) / 86400

    def touch(self):
        """Mark as accessed."""
        self.access_count += 1
        self.updated_at = time.time()


@dataclass
class PaperMemory:
    """Memory specific to a paper being reviewed."""
    paper_id: str                    # DOI, filename, or hash
    title: str
    field: str = ""                  # Detected academic field
    key_issues: List[str] = dataclass_field(default_factory=list)
    strengths: List[str] = dataclass_field(default_factory=list)
    revision_history: List[str] = dataclass_field(default_factory=list)
    voice_profile_hash: str = ""     # For continuity across sessions
    last_reviewed_at: float = dataclass_field(default_factory=time.time)
    review_count: int = 0
    metadata: Dict[str, str] = dataclass_field(default_factory=dict)


@dataclass
class SessionSummary:
    """Summary of an agent session for future reference."""
    session_id: str
    started_at: float
    ended_at: float = 0.0
    paper_ids: List[str] = dataclass_field(default_factory=list)
    tools_used: List[str] = dataclass_field(default_factory=list)
    issues_found: int = 0
    rewrites_made: int = 0
    key_decisions: List[str] = dataclass_field(default_factory=list)
    outcome: str = ""               # "completed" | "partial" | "error"
    notes: str = ""
