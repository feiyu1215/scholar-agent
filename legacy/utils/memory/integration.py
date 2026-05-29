"""
utils/memory/integration.py - Integration helpers for review/write pipelines.

Provides lightweight functions to record and recall memories from the
review and rewrite workflows. All operations are non-blocking and non-fatal:
if memory fails, the main workflow continues unaffected.

Usage:
    from utils.memory.integration import remember_review, remember_rewrite, recall_paper_context
"""

from __future__ import annotations

import hashlib
import time
from typing import Dict, List, Optional

from .models import MemoryEntry, MemoryType, PaperMemory
from .store import get_memory_store


def remember_review(
    paper_id: str,
    title: str,
    field: str,
    issues: List[Dict],
    strengths: List[str],
    overall_score: float,
    verdict: str,
) -> None:
    """Record review results into memory after a review_paper call.

    Stores:
        - PaperMemory: per-paper tracking (issues, strengths, field)
        - REVIEW_PATTERN entries: recurring issue patterns across papers
    """
    try:
        store = get_memory_store()

        # 1. Update or create PaperMemory
        existing = store.get_paper(paper_id)
        if existing:
            # Merge new issues with existing
            new_issues = [i.get("title", i.get("description", ""))[:80] for i in issues[:10]]
            existing.key_issues = list(set(existing.key_issues + new_issues))[:20]
            existing.strengths = list(set(existing.strengths + strengths))[:10]
            existing.revision_history.append(
                f"Review #{existing.review_count + 1}: score={overall_score:.1f}, verdict={verdict}"
            )
            existing.review_count += 1
            existing.last_reviewed_at = time.time()
            existing.field = field or existing.field
            store.save_paper(existing)
        else:
            paper_mem = PaperMemory(
                paper_id=paper_id,
                title=title,
                field=field,
                key_issues=[i.get("title", i.get("description", ""))[:80] for i in issues[:10]],
                strengths=strengths[:5],
                revision_history=[f"Initial review: score={overall_score:.1f}, verdict={verdict}"],
                review_count=1,
                last_reviewed_at=time.time(),
            )
            store.save_paper(paper_mem)

        # 2. Record recurring patterns (high-severity issues)
        severe_issues = [i for i in issues if i.get("severity") in ("major", "critical")]
        for issue in severe_issues[:3]:
            category = issue.get("category", issue.get("comment_type", "general"))
            pattern_content = f"[{field}] {category}: {issue.get('title', '')[:100]}"

            # Check if we already have this pattern
            existing_patterns = store.search(
                pattern_content[:50],
                memory_type=MemoryType.REVIEW_PATTERN,
                limit=3,
            )
            if not existing_patterns:
                store.save(MemoryEntry(
                    id="",
                    memory_type=MemoryType.REVIEW_PATTERN,
                    content=pattern_content,
                    context=f"paper={paper_id}, score={overall_score}",
                    tags=[field, category],
                    confidence=0.7,
                ))

    except Exception:
        pass  # Non-fatal: memory failure should never block review


def remember_rewrite(
    paper_id: str,
    section_id: str,
    changes_summary: str,
    verify_passed: bool,
) -> None:
    """Record a rewrite result into memory.

    Tracks which sections were rewritten, what changes were made,
    and whether verification passed (user preference signal).
    """
    try:
        store = get_memory_store()

        # Update paper memory with revision history
        existing = store.get_paper(paper_id)
        if existing:
            entry = f"Rewrite {section_id}: {changes_summary[:60]}"
            if not verify_passed:
                entry += " [VERIFY_FAILED]"
            existing.revision_history.append(entry)
            # Keep last 30 revision entries
            existing.revision_history = existing.revision_history[-30:]
            existing.last_reviewed_at = time.time()
            store.save_paper(existing)

        # If verification failed, record as error_lesson
        if not verify_passed:
            store.save(MemoryEntry(
                id="",
                memory_type=MemoryType.ERROR_LESSON,
                content=f"Rewrite of {section_id} failed verification: {changes_summary[:100]}",
                context=f"paper={paper_id}",
                tags=["rewrite_failure", section_id],
                confidence=0.8,
            ))

    except Exception:
        pass  # Non-fatal


def recall_paper_context(paper_id: str) -> Optional[str]:
    """Recall previous review context for a paper.

    Returns a formatted string of past insights, or None if no history.
    Used to provide continuity when re-reviewing a paper.
    """
    try:
        store = get_memory_store()
        paper_mem = store.get_paper(paper_id)
        if not paper_mem:
            return None

        lines = [f"📝 Previous review context for: {paper_mem.title}"]
        lines.append(f"   Field: {paper_mem.field} | Reviews: {paper_mem.review_count}")

        if paper_mem.key_issues:
            lines.append(f"   Known issues: {'; '.join(paper_mem.key_issues[:5])}")
        if paper_mem.strengths:
            lines.append(f"   Strengths: {'; '.join(paper_mem.strengths[:3])}")
        if paper_mem.revision_history:
            lines.append(f"   Recent history: {paper_mem.revision_history[-1]}")

        return "\n".join(lines)

    except Exception:
        return None


def recall_field_patterns(field: str, limit: int = 5) -> List[str]:
    """Recall common review patterns for a given academic field.

    Returns list of pattern descriptions that recur across papers in this field.
    """
    try:
        store = get_memory_store()
        patterns = store.search(
            field,
            memory_type=MemoryType.REVIEW_PATTERN,
            tags=[field],
            limit=limit,
        )
        return [p.content for p in patterns]
    except Exception:
        return []


def get_paper_id(source_file: str, title: str = "") -> str:
    """Generate a stable paper_id from filename or title.

    Uses filename hash as default; incorporates title if available.
    """
    key = title.strip().lower() if title else source_file
    return hashlib.md5(key.encode()).hexdigest()[:12]
