"""
utils/recall.py - Tool Result Recall Path.

Stores key tool outputs so the agent can recall prior results
without re-executing expensive tools. A lightweight session memory layer.

Design (v2 - redesigned for reliability):
- IN-MEMORY dict as primary store (zero I/O per tool call)
- Periodic ATOMIC flush to disk (crash-safe via tempfile + rename)
- Keyed by (tool_name, content_hash) so identical calls return cached results
- TTL-based expiry for results that may become stale
- Size-bounded: evicts oldest entries when cap exceeded

Usage in agent_loop:
1. Before executing a tool -> check recall_get()
2. After executing a tool -> call recall_store() with result
3. Agent can explicitly query recall via recall_search()
"""

from __future__ import annotations

import json
import time
import hashlib
import tempfile
import os
from pathlib import Path
from typing import Optional, Dict, List

WORKSPACE = Path(".workspace")
RECALL_FILE = WORKSPACE / "recall" / "tool_results.json"
MAX_ENTRIES = 50
DEFAULT_TTL = 3600  # 1 hour

# Tools whose results should NEVER be cached (side-effectful or user-dependent)
NO_CACHE_TOOLS = {"ask_user", "approve_fix", "edit_section", "rewrite_section"}

# Tools whose results are stable (long TTL)
STABLE_TOOLS = {
    "read_section_index": 7200,
    "parse_paper": 7200,
    "load_skill": 7200,
    "consistency_check": 1800,
    "architecture_diagnosis": 3600,
    "presubmission_check": 3600,
}

# ============================================================
# In-Memory Store (primary)
# ============================================================

_memory_store: List[Dict] = []
_store_dirty = False
_last_flush_time = 0.0
FLUSH_INTERVAL = 30.0  # Flush to disk every 30 seconds max


def _content_hash(args: dict) -> str:
    """Deterministic hash of tool arguments for deduplication."""
    normalized = json.dumps(args, sort_keys=True, ensure_ascii=False)
    return hashlib.md5(normalized.encode()).hexdigest()[:12]


def _ensure_loaded():
    """Load from disk into memory on first access."""
    global _memory_store, _last_flush_time
    if _memory_store:
        return  # Already loaded
    if RECALL_FILE.exists():
        try:
            _memory_store = json.loads(RECALL_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            _memory_store = []
    _last_flush_time = time.time()


def _flush_to_disk():
    """Atomic write: write to tempfile then rename (crash-safe)."""
    global _store_dirty, _last_flush_time
    if not _store_dirty:
        return

    RECALL_FILE.parent.mkdir(parents=True, exist_ok=True)

    # Write to temp file in same directory, then atomic rename
    try:
        fd, tmp_path = tempfile.mkstemp(
            dir=str(RECALL_FILE.parent),
            prefix=".recall_",
            suffix=".tmp"
        )
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(_memory_store, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, str(RECALL_FILE))
        _store_dirty = False
        _last_flush_time = time.time()
    except OSError:
        # If atomic write fails, try regular write as fallback
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass


def _maybe_flush():
    """Flush to disk if enough time has passed since last flush."""
    if _store_dirty and (time.time() - _last_flush_time) > FLUSH_INTERVAL:
        _flush_to_disk()


def recall_get(tool_name: str, args: dict) -> Optional[str]:
    """
    Check if a previous result exists for this tool call.

    Returns:
        The cached result string if found and not expired, else None.
    """
    if tool_name in NO_CACHE_TOOLS:
        return None

    _ensure_loaded()
    global _store_dirty

    key = _content_hash(args)
    now = time.time()

    for entry in _memory_store:
        if entry["tool"] == tool_name and entry["key"] == key:
            if now - entry["stored_at"] < entry["ttl"]:
                entry["last_accessed"] = now
                entry["access_count"] = entry.get("access_count", 0) + 1
                _store_dirty = True
                _maybe_flush()
                return entry["result"]
            else:
                # Expired - remove
                _memory_store.remove(entry)
                _store_dirty = True
                _maybe_flush()
                return None

    return None


def recall_store(tool_name: str, args: dict, result: str, ttl: int = None):
    """
    Store a tool result in recall.
    """
    if tool_name in NO_CACHE_TOOLS:
        return

    # Don't cache error results
    if isinstance(result, str) and result.startswith("Error:"):
        return

    # Don't cache very short results
    if isinstance(result, str) and len(result) < 50:
        return

    _ensure_loaded()
    global _store_dirty

    key = _content_hash(args)
    effective_ttl = ttl or STABLE_TOOLS.get(tool_name, DEFAULT_TTL)
    now = time.time()

    # Remove any existing entry for same tool+key
    _memory_store[:] = [e for e in _memory_store
                        if not (e["tool"] == tool_name and e["key"] == key)]

    # Add new entry
    _memory_store.append({
        "tool": tool_name,
        "key": key,
        "args_summary": _summarize_args(args),
        "result": result[:5000],  # Cap stored result size
        "stored_at": now,
        "last_accessed": now,
        "access_count": 0,
        "ttl": effective_ttl,
    })

    # Evict oldest if over cap
    if len(_memory_store) > MAX_ENTRIES:
        _memory_store.sort(key=lambda e: e["last_accessed"])
        _memory_store[:] = _memory_store[-MAX_ENTRIES:]

    _store_dirty = True
    _maybe_flush()


def recall_search(query: str) -> List[Dict]:
    """
    Search recall store by tool name or args content.
    """
    _ensure_loaded()
    now = time.time()
    query_lower = query.lower()

    matches = []
    for entry in _memory_store:
        if now - entry["stored_at"] >= entry["ttl"]:
            continue

        if (query_lower in entry["tool"].lower() or
                query_lower in entry.get("args_summary", "").lower() or
                query_lower in entry.get("result", "")[:200].lower()):
            matches.append({
                "tool": entry["tool"],
                "args_summary": entry["args_summary"],
                "result_preview": entry["result"][:200],
                "stored_ago": str(int((now - entry["stored_at"]) / 60)) + "m ago",
                "access_count": entry.get("access_count", 0),
            })

    return matches


def recall_invalidate(tool_name: str = None, section_id: str = None):
    """
    Invalidate cached results when underlying data changes.
    """
    _ensure_loaded()
    global _store_dirty

    original_len = len(_memory_store)

    if tool_name:
        _memory_store[:] = [e for e in _memory_store if e["tool"] != tool_name]
    elif section_id:
        _memory_store[:] = [e for e in _memory_store
                            if not (e["tool"] == "read_section" and
                                    section_id in e.get("args_summary", ""))]

    if len(_memory_store) != original_len:
        _store_dirty = True
        _flush_to_disk()  # Immediate flush on invalidation (data consistency)


def recall_summary() -> Dict:
    """Summary stats for /recall command."""
    _ensure_loaded()
    now = time.time()

    active = [e for e in _memory_store if now - e["stored_at"] < e["ttl"]]
    expired = len(_memory_store) - len(active)

    by_tool = {}
    for e in active:
        by_tool[e["tool"]] = by_tool.get(e["tool"], 0) + 1

    total_size = sum(len(e.get("result", "")) for e in active)

    return {
        "active_entries": len(active),
        "expired_entries": expired,
        "by_tool": by_tool,
        "total_cached_chars": total_size,
        "max_entries": MAX_ENTRIES,
        "dirty": _store_dirty,
    }


def recall_flush():
    """Force flush to disk. Call on graceful shutdown."""
    _flush_to_disk()


def _summarize_args(args: dict) -> str:
    """Short human-readable summary of tool args."""
    parts = []
    for k, v in args.items():
        v_str = str(v)[:40]
        parts.append(k + "=" + v_str)
    return ", ".join(parts)
