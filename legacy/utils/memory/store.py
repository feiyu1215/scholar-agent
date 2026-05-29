"""
utils/memory/store.py - SQLite-backed persistent memory store.

Provides CRUD operations on memory entries with:
- Full-text keyword search
- Tag-based filtering
- Automatic expiry cleanup
- Session-scoped and global memories
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from .models import MemoryEntry, MemoryType, PaperMemory, SessionSummary


# Default DB location
_DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", ".cache", "memory.db"
)


class MemoryStore:
    """
    SQLite-backed persistent memory store.
    
    Usage:
        store = MemoryStore()
        store.save(MemoryEntry(id="", memory_type=MemoryType.PAPER_INSIGHT, content="..."))
        results = store.search("citation format")
    """

    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path or os.environ.get("MEMORY_DB_PATH", _DEFAULT_DB_PATH)
        # Ensure parent directory exists
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        """Create tables if they don't exist."""
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                memory_type TEXT NOT NULL,
                content TEXT NOT NULL,
                context TEXT DEFAULT '',
                tags TEXT DEFAULT '[]',
                confidence REAL DEFAULT 1.0,
                access_count INTEGER DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                expires_at REAL,
                metadata TEXT DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS paper_memories (
                paper_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                field TEXT DEFAULT '',
                key_issues TEXT DEFAULT '[]',
                strengths TEXT DEFAULT '[]',
                revision_history TEXT DEFAULT '[]',
                voice_profile_hash TEXT DEFAULT '',
                last_reviewed_at REAL NOT NULL,
                review_count INTEGER DEFAULT 0,
                metadata TEXT DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS session_summaries (
                session_id TEXT PRIMARY KEY,
                started_at REAL NOT NULL,
                ended_at REAL DEFAULT 0,
                paper_ids TEXT DEFAULT '[]',
                tools_used TEXT DEFAULT '[]',
                issues_found INTEGER DEFAULT 0,
                rewrites_made INTEGER DEFAULT 0,
                key_decisions TEXT DEFAULT '[]',
                outcome TEXT DEFAULT '',
                notes TEXT DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(memory_type);
            CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at);
            CREATE INDEX IF NOT EXISTS idx_memories_tags ON memories(tags);
        """)
        self._conn.commit()

    # ========================================================
    # Memory CRUD
    # ========================================================

    def save(self, entry: MemoryEntry) -> str:
        """Save or update a memory entry. Returns the entry ID."""
        if not entry.id:
            entry.id = str(uuid.uuid4())[:12]
        entry.updated_at = time.time()

        self._conn.execute("""
            INSERT OR REPLACE INTO memories 
            (id, memory_type, content, context, tags, confidence,
             access_count, created_at, updated_at, expires_at, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            entry.id,
            entry.memory_type.value if isinstance(entry.memory_type, MemoryType) else entry.memory_type,
            entry.content,
            entry.context,
            json.dumps(entry.tags),
            entry.confidence,
            entry.access_count,
            entry.created_at,
            entry.updated_at,
            entry.expires_at,
            json.dumps(entry.metadata),
        ))
        self._conn.commit()
        return entry.id

    def get(self, entry_id: str) -> Optional[MemoryEntry]:
        """Get a memory entry by ID."""
        row = self._conn.execute(
            "SELECT * FROM memories WHERE id = ?", (entry_id,)
        ).fetchone()
        if not row:
            return None
        entry = self._row_to_entry(row)
        # Touch on access
        entry.touch()
        self._conn.execute(
            "UPDATE memories SET access_count = ?, updated_at = ? WHERE id = ?",
            (entry.access_count, entry.updated_at, entry.id)
        )
        self._conn.commit()
        return entry

    def delete(self, entry_id: str) -> bool:
        """Delete a memory entry."""
        cursor = self._conn.execute("DELETE FROM memories WHERE id = ?", (entry_id,))
        self._conn.commit()
        return cursor.rowcount > 0

    def search(
        self,
        query: str,
        memory_type: Optional[MemoryType] = None,
        tags: Optional[List[str]] = None,
        limit: int = 20,
    ) -> List[MemoryEntry]:
        """
        Search memories by keyword matching on content and context.
        
        Args:
            query: Search keywords (space-separated, OR logic)
            memory_type: Filter by type
            tags: Filter by tags (AND logic)
            limit: Max results
        """
        conditions = ["(expires_at IS NULL OR expires_at > ?)"]
        params: list = [time.time()]

        if memory_type:
            conditions.append("memory_type = ?")
            params.append(memory_type.value)

        # Keyword search (simple LIKE matching)
        keywords = query.strip().split()
        if keywords:
            keyword_conditions = []
            for kw in keywords:
                keyword_conditions.append("(content LIKE ? OR context LIKE ? OR tags LIKE ?)")
                pattern = f"%{kw}%"
                params.extend([pattern, pattern, pattern])
            conditions.append(f"({' OR '.join(keyword_conditions)})")

        # Tag filter
        if tags:
            for tag in tags:
                conditions.append("tags LIKE ?")
                params.append(f'%"{tag}"%')

        sql = f"""
            SELECT * FROM memories
            WHERE {' AND '.join(conditions)}
            ORDER BY updated_at DESC
            LIMIT ?
        """
        params.append(limit)

        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_entry(row) for row in rows]

    def list_by_type(self, memory_type: MemoryType, limit: int = 50) -> List[MemoryEntry]:
        """List all memories of a given type."""
        rows = self._conn.execute(
            "SELECT * FROM memories WHERE memory_type = ? ORDER BY updated_at DESC LIMIT ?",
            (memory_type.value, limit)
        ).fetchall()
        return [self._row_to_entry(row) for row in rows]

    def count(self, memory_type: Optional[MemoryType] = None) -> int:
        """Count memories, optionally filtered by type."""
        if memory_type:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM memories WHERE memory_type = ?",
                (memory_type.value,)
            ).fetchone()
        else:
            row = self._conn.execute("SELECT COUNT(*) FROM memories").fetchone()
        return row[0] if row else 0

    def cleanup_expired(self) -> int:
        """Remove expired entries. Returns count removed."""
        cursor = self._conn.execute(
            "DELETE FROM memories WHERE expires_at IS NOT NULL AND expires_at < ?",
            (time.time(),)
        )
        self._conn.commit()
        return cursor.rowcount

    # ========================================================
    # Paper Memory
    # ========================================================

    def save_paper(self, paper: PaperMemory):
        """Save or update paper-specific memory."""
        self._conn.execute("""
            INSERT OR REPLACE INTO paper_memories
            (paper_id, title, field, key_issues, strengths, revision_history,
             voice_profile_hash, last_reviewed_at, review_count, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            paper.paper_id,
            paper.title,
            paper.field,
            json.dumps(paper.key_issues),
            json.dumps(paper.strengths),
            json.dumps(paper.revision_history),
            paper.voice_profile_hash,
            paper.last_reviewed_at,
            paper.review_count,
            json.dumps(paper.metadata),
        ))
        self._conn.commit()

    def get_paper(self, paper_id: str) -> Optional[PaperMemory]:
        """Retrieve paper memory by ID."""
        row = self._conn.execute(
            "SELECT * FROM paper_memories WHERE paper_id = ?", (paper_id,)
        ).fetchone()
        if not row:
            return None
        return PaperMemory(
            paper_id=row["paper_id"],
            title=row["title"],
            field=row["field"],
            key_issues=json.loads(row["key_issues"]),
            strengths=json.loads(row["strengths"]),
            revision_history=json.loads(row["revision_history"]),
            voice_profile_hash=row["voice_profile_hash"],
            last_reviewed_at=row["last_reviewed_at"],
            review_count=row["review_count"],
            metadata=json.loads(row["metadata"]),
        )

    def list_papers(self, limit: int = 50) -> List[PaperMemory]:
        """List all paper memories."""
        rows = self._conn.execute(
            "SELECT * FROM paper_memories ORDER BY last_reviewed_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [
            PaperMemory(
                paper_id=r["paper_id"],
                title=r["title"],
                field=r["field"],
                key_issues=json.loads(r["key_issues"]),
                strengths=json.loads(r["strengths"]),
                revision_history=json.loads(r["revision_history"]),
                voice_profile_hash=r["voice_profile_hash"],
                last_reviewed_at=r["last_reviewed_at"],
                review_count=r["review_count"],
                metadata=json.loads(r["metadata"]),
            )
            for r in rows
        ]

    # ========================================================
    # Session Summaries
    # ========================================================

    def save_session(self, summary: SessionSummary):
        """Save a session summary."""
        self._conn.execute("""
            INSERT OR REPLACE INTO session_summaries
            (session_id, started_at, ended_at, paper_ids, tools_used,
             issues_found, rewrites_made, key_decisions, outcome, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            summary.session_id,
            summary.started_at,
            summary.ended_at,
            json.dumps(summary.paper_ids),
            json.dumps(summary.tools_used),
            summary.issues_found,
            summary.rewrites_made,
            json.dumps(summary.key_decisions),
            summary.outcome,
            summary.notes,
        ))
        self._conn.commit()

    def get_session(self, session_id: str) -> Optional[SessionSummary]:
        """Get a session summary."""
        row = self._conn.execute(
            "SELECT * FROM session_summaries WHERE session_id = ?", (session_id,)
        ).fetchone()
        if not row:
            return None
        return SessionSummary(
            session_id=row["session_id"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            paper_ids=json.loads(row["paper_ids"]),
            tools_used=json.loads(row["tools_used"]),
            issues_found=row["issues_found"],
            rewrites_made=row["rewrites_made"],
            key_decisions=json.loads(row["key_decisions"]),
            outcome=row["outcome"],
            notes=row["notes"],
        )

    def recent_sessions(self, limit: int = 10) -> List[SessionSummary]:
        """Get recent session summaries."""
        rows = self._conn.execute(
            "SELECT * FROM session_summaries ORDER BY started_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [
            SessionSummary(
                session_id=r["session_id"],
                started_at=r["started_at"],
                ended_at=r["ended_at"],
                paper_ids=json.loads(r["paper_ids"]),
                tools_used=json.loads(r["tools_used"]),
                issues_found=r["issues_found"],
                rewrites_made=r["rewrites_made"],
                key_decisions=json.loads(r["key_decisions"]),
                outcome=r["outcome"],
                notes=r["notes"],
            )
            for r in rows
        ]

    # ========================================================
    # Utilities
    # ========================================================

    def stats(self) -> Dict:
        """Get memory store statistics."""
        return {
            "total_memories": self.count(),
            "by_type": {
                mt.value: self.count(mt) for mt in MemoryType
            },
            "total_papers": self._conn.execute(
                "SELECT COUNT(*) FROM paper_memories"
            ).fetchone()[0],
            "total_sessions": self._conn.execute(
                "SELECT COUNT(*) FROM session_summaries"
            ).fetchone()[0],
            "db_path": self._db_path,
        }

    def _row_to_entry(self, row) -> MemoryEntry:
        """Convert a database row to MemoryEntry."""
        return MemoryEntry(
            id=row["id"],
            memory_type=MemoryType(row["memory_type"]),
            content=row["content"],
            context=row["context"],
            tags=json.loads(row["tags"]),
            confidence=row["confidence"],
            access_count=row["access_count"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            expires_at=row["expires_at"],
            metadata=json.loads(row["metadata"]),
        )

    def close(self):
        """Close the database connection."""
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


# Module-level singleton
_store: Optional[MemoryStore] = None


def get_memory_store(db_path: Optional[str] = None) -> MemoryStore:
    """Get or create the global memory store singleton."""
    global _store
    if _store is None:
        _store = MemoryStore(db_path)
    return _store
