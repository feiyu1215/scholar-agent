"""
tools/section_ops.py — Section-level read/edit/diff operations.

Core principle: the agent never loads the full paper into context.
It reads one section at a time, edits one section at a time, and diffs one section at a time.
This keeps token usage minimal and attention focused.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from difflib import unified_diff
from typing import Optional


WORKSPACE = Path(".workspace")


def read_section(section_id: str) -> str:
    """Read a single section by ID. Returns the section content."""
    index = _load_index()
    if not index:
        return "Error: No paper parsed yet. Use parse_paper first."

    entry = _find_section(section_id, index)
    if not entry:
        available = [s["id"] for s in index]
        return f"Error: Section '{section_id}' not found. Available: {available}"

    # Check if revised version exists
    rev_path = WORKSPACE / "revisions" / f"{section_id}_v2.md"
    if rev_path.exists():
        content = rev_path.read_text(encoding="utf-8")
        return f"[Reading REVISED version of {section_id}]\n\n{content}"

    sec_path = Path(entry["file"])
    if not sec_path.exists():
        return f"Error: Section file not found: {sec_path}"

    return sec_path.read_text(encoding="utf-8")


def read_section_index() -> str:
    """Read the section index — lightweight overview of all sections."""
    index = _load_index()
    if not index:
        return "Error: No paper parsed yet."
    lines = ["Section Index:"]
    for s in index:
        rev_marker = " [REVISED]" if (WORKSPACE / "revisions" / f"{s['id']}_v2.md").exists() else ""
        lines.append(f"  {s['id']}: {s['title']} ({s['word_count']} words){rev_marker}")
    return "\n".join(lines)


def edit_section(section_id: str, old_text: str, new_text: str, reason: str = "") -> str:
    """Edit a section using string replacement. Writes to revisions/ and logs the change.

    Args:
        section_id: Which section to edit
        old_text: Exact text to find and replace
        new_text: Replacement text
        reason: Why this edit was made (for revision log)
    """
    index = _load_index()
    entry = _find_section(section_id, index)
    if not entry:
        return f"Error: Section '{section_id}' not found."

    # Read current version (revised if exists, original otherwise)
    rev_dir = WORKSPACE / "revisions"
    rev_dir.mkdir(parents=True, exist_ok=True)
    rev_path = rev_dir / f"{section_id}_v2.md"

    if rev_path.exists():
        content = rev_path.read_text(encoding="utf-8")
    else:
        content = Path(entry["file"]).read_text(encoding="utf-8")

    if old_text not in content:
        # Show a snippet around potential matches for debugging
        return f"Error: Text not found in {section_id}. Make sure old_text matches exactly."

    # Apply edit
    new_content = content.replace(old_text, new_text, 1)
    rev_path.write_text(new_content, encoding="utf-8")

    # Log the revision
    _log_revision(section_id, old_text, new_text, reason)

    return f"Edited {section_id}: replaced {len(old_text)} chars with {len(new_text)} chars. Reason: {reason}"


def diff_section(section_id: str) -> str:
    """Show unified diff between original and revised version of a section."""
    index = _load_index()
    entry = _find_section(section_id, index)
    if not entry:
        return f"Error: Section '{section_id}' not found."

    original_path = Path(entry["file"])
    revised_path = WORKSPACE / "revisions" / f"{section_id}_v2.md"

    if not revised_path.exists():
        return f"No revisions for {section_id} yet."

    original = original_path.read_text(encoding="utf-8").splitlines(keepends=True)
    revised = revised_path.read_text(encoding="utf-8").splitlines(keepends=True)

    diff = unified_diff(original, revised,
                        fromfile=f"{section_id} (original)",
                        tofile=f"{section_id} (revised)")
    result = "".join(diff)
    return result if result else "No differences found."


def read_revision_log(section_id: str = None) -> str:
    """Read the revision log. Optionally filter by section_id."""
    log_path = WORKSPACE / "revisions" / "revision_log.jsonl"
    if not log_path.exists():
        return "No revisions yet."

    entries = []
    for line in log_path.read_text(encoding="utf-8").strip().splitlines():
        if line:
            entry = json.loads(line)
            if section_id is None or entry.get("section_id") == section_id:
                entries.append(entry)

    if not entries:
        return f"No revisions for section '{section_id}'." if section_id else "No revisions."

    lines = [f"Revision Log ({len(entries)} entries):"]
    for e in entries:
        lines.append(f"  [{e['timestamp']}] {e['section_id']}: {e['reason']}")
        lines.append(f"    -{len(e.get('old_text',''))} chars / +{len(e.get('new_text',''))} chars")
    return "\n".join(lines)


def consistency_check() -> str:
    """Quick consistency scan: read first/last sentences of all revised sections
    and the revision log summaries. Returns potential inconsistencies.
    
    This is a lightweight operation — does NOT re-read full sections.
    """
    index = _load_index()
    if not index:
        return "No paper loaded."

    rev_dir = WORKSPACE / "revisions"
    fragments = []
    for entry in index:
        rev_path = rev_dir / f"{entry['id']}_v2.md"
        if rev_path.exists():
            text = rev_path.read_text(encoding="utf-8")
            lines = [l.strip() for l in text.splitlines() if l.strip()]
            first = lines[0] if lines else ""
            last = lines[-1] if len(lines) > 1 else ""
            fragments.append({
                "section": entry["id"],
                "title": entry["title"],
                "first_line": first[:200],
                "last_line": last[:200],
                "revised": True,
            })
        else:
            # Read original first/last line
            orig_path = Path(entry["file"])
            if orig_path.exists():
                text = orig_path.read_text(encoding="utf-8")
                lines = [l.strip() for l in text.splitlines() if l.strip()]
                first = lines[0] if lines else ""
                last = lines[-1] if len(lines) > 1 else ""
                fragments.append({
                    "section": entry["id"],
                    "title": entry["title"],
                    "first_line": first[:200],
                    "last_line": last[:200],
                    "revised": False,
                })

    return json.dumps(fragments, indent=2, ensure_ascii=False)


# ============================================================
# Internal helpers
# ============================================================

def _load_index() -> list:
    index_path = WORKSPACE / "paper" / "section_index.json"
    if not index_path.exists():
        return []
    return json.loads(index_path.read_text(encoding="utf-8"))


def _find_section(section_id: str, index: list) -> Optional[dict]:
    for entry in index:
        if entry["id"] == section_id or section_id in entry["id"]:
            return entry
    return None


def _log_revision(section_id: str, old_text: str, new_text: str, reason: str):
    log_path = WORKSPACE / "revisions" / "revision_log.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "section_id": section_id,
        "reason": reason,
        "old_text": old_text[:500],  # Truncate for log readability
        "new_text": new_text[:500],
    }
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
