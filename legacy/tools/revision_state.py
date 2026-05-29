"""
tools/revision_state.py — File-based working memory for the revision session.

Tracks which issues have been processed, their current status, and what
categories have been confirmed by the user (for first-of-type validation).

Design choices:
- State is persisted as JSON so agent can resume across sessions
- Each issue has a lifecycle: pending → in_progress → done/skipped/failed
- seen_categories tracks which categories user has validated (enables auto_fix)
- Paper memory: stores voice profile + section checksums for drift detection
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, List, Optional, Set

WORKSPACE = Path(".workspace")
STATE_FILE = WORKSPACE / "revision_state.json"


def _default_state() -> dict:
    """Create a fresh revision state."""
    return {
        "version": 2,
        "created_at": time.time(),
        "updated_at": time.time(),
        "budget": "full",
        "paper_id": None,
        "issues": {},            # id → issue status record
        "seen_categories": [],   # categories confirmed by user
        "phase": "review",       # review | routing | revising | auditing | done
        "stats": {
            "total_issues": 0,
            "auto_fixed": 0,
            "confirmed_fixed": 0,
            "guidance_given": 0,
            "skipped": 0,
            "failed": 0,
        },
        "deai_results": {},      # section_id → DeAIVerdict summary
        "stata_results": {},     # issue_id → verification result
    }


def load_state() -> dict:
    """Load revision state from disk. Creates default if not exists."""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            # Corrupted file — backup and reset
            backup = STATE_FILE.with_suffix(".json.bak")
            STATE_FILE.rename(backup)
            return _default_state()
    return _default_state()


def save_state(state: dict) -> None:
    """Persist state to disk."""
    state["updated_at"] = time.time()
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def init_state(budget: str = "full", paper_id: str = None) -> dict:
    """Initialize a fresh revision state for a new session."""
    state = _default_state()
    state["budget"] = budget
    state["paper_id"] = paper_id
    save_state(state)
    return state


def register_issues(state: dict, routed_issues: List[dict]) -> dict:
    """Register routed issues into the state tracker."""
    for issue in routed_issues:
        issue_id = issue.get("id", f"ISS-{len(state['issues'])+1:03d}")
        state["issues"][issue_id] = {
            "id": issue_id,
            "effective_action": issue.get("effective_action", "guidance"),
            "category": issue.get("category", "unknown"),
            "severity": issue.get("severity", "minor"),
            "status": "pending",  # pending | in_progress | done | skipped | failed
            "attempts": 0,
            "deai_pass": None,
            "notes": [],
        }
    state["stats"]["total_issues"] = len(state["issues"])
    state["phase"] = "routing"
    save_state(state)
    return state


def update_issue_status(
    state: dict, 
    issue_id: str, 
    status: str, 
    note: str = None
) -> dict:
    """Update an issue's processing status."""
    if issue_id in state["issues"]:
        record = state["issues"][issue_id]
        old_status = record["status"]
        record["status"] = status
        record["attempts"] += 1
        if note:
            record["notes"].append(note)
        
        # Update stats
        if status == "done" and old_status != "done":
            action = record["effective_action"]
            if action == "auto_fix":
                state["stats"]["auto_fixed"] += 1
            elif action == "confirm_fix":
                state["stats"]["confirmed_fixed"] += 1
            elif action == "guidance":
                state["stats"]["guidance_given"] += 1
        elif status == "skipped":
            state["stats"]["skipped"] += 1
        elif status == "failed":
            state["stats"]["failed"] += 1

    save_state(state)
    return state


def mark_category_confirmed(state: dict, category: str) -> dict:
    """Mark a category as user-confirmed (enables future auto_fix)."""
    if category not in state["seen_categories"]:
        state["seen_categories"].append(category)
    save_state(state)
    return state


def get_pending_issues(state: dict, action_type: str = None) -> List[dict]:
    """Get all pending issues, optionally filtered by effective_action."""
    pending = []
    for issue_id, record in state["issues"].items():
        if record["status"] == "pending":
            if action_type is None or record["effective_action"] == action_type:
                pending.append(record)
    return pending


def get_next_issue(state: dict) -> Optional[dict]:
    """Get the next issue to process (priority: major > moderate > minor)."""
    severity_order = {"major": 0, "moderate": 1, "minor": 2}
    pending = [r for r in state["issues"].values() if r["status"] == "pending"]
    if not pending:
        return None
    pending.sort(key=lambda r: severity_order.get(r["severity"], 3))
    return pending[0]


def record_deai_result(state: dict, section_id: str, verdict: dict) -> dict:
    """Record de-AI audit result for a section."""
    state["deai_results"][section_id] = {
        "is_natural": verdict.get("is_natural", False),
        "score": verdict.get("overall_score", 0),
        "signal_count": len(verdict.get("signals", [])),
        "timestamp": time.time(),
    }
    save_state(state)
    return state


def record_stata_result(state: dict, issue_id: str, result: dict) -> dict:
    """Record Stata verification result for an issue."""
    state["stata_results"][issue_id] = {
        "status": result.get("status", "unknown"),
        "summary": result.get("summary", ""),
        "timestamp": time.time(),
    }
    save_state(state)
    return state


def get_seen_categories(state: dict) -> Set[str]:
    """Get set of user-confirmed categories."""
    return set(state.get("seen_categories", []))


def is_session_complete(state: dict) -> bool:
    """Check if all issues have been processed."""
    return all(
        r["status"] in ("done", "skipped", "failed")
        for r in state["issues"].values()
    )


def format_progress(state: dict) -> str:
    """Format a compact progress summary."""
    s = state["stats"]
    total = s["total_issues"]
    done = s["auto_fixed"] + s["confirmed_fixed"] + s["guidance_given"]
    remaining = total - done - s["skipped"] - s["failed"]
    
    lines = [
        f"Phase: {state['phase']} | Budget: {state['budget']}",
        f"Progress: {done}/{total} done, {remaining} remaining, "
        f"{s['skipped']} skipped, {s['failed']} failed",
        f"  auto_fixed: {s['auto_fixed']}, confirmed: {s['confirmed_fixed']}, "
        f"guidance: {s['guidance_given']}",
    ]
    
    # De-AI summary
    deai = state.get("deai_results", {})
    if deai:
        passed = sum(1 for v in deai.values() if v["is_natural"])
        lines.append(f"De-AI: {passed}/{len(deai)} sections passed")
    
    return "\n".join(lines)
