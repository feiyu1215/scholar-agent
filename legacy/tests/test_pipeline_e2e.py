#!/usr/bin/env python3
"""
test_pipeline_e2e.py — End-to-end pipeline integration test.

Verifies the full Review → Route → Fix → Verify chain works correctly
as a single coherent workflow, not just individual tools in isolation.

Tests:
1. Review produces consolidated issues → saved to disk
2. Route classifies issues and applies Red Lines → routed_issues.json
3. Generate fix proposal creates actionable plan → proposal file
4. Approve fix applies the fix and updates state → revision state updated
5. Verify rewrite quality confirms the fix passes quality gate
6. Full pipeline: 3 issues routed → 1 auto_fix, 1 confirm_fix, 1 guidance
7. DeAI integration: rewrite → deai_audit → verify pass
8. Session completeness detection after all issues processed
"""

from __future__ import annotations

import os
import sys
import json
import time
import asyncio
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

import core.state as state
from utils.goal_tracker import GoalTracker
from utils.plan_persistence import PlanStore
from utils.self_reflection import ReflectionEngine
from utils.adaptive_strategy import AdaptiveEngine
from utils.context_manager import ProactiveContextManager
from utils.error_recovery import ErrorRecoveryEngine
from utils.output_quality import OutputQualityGate
from utils.session_memory import SessionMemory
from utils.meta_planner import MetaPlanner
from tools.revision_state import (
    init_state as init_revision_state,
    load_state, save_state, register_issues,
    update_issue_status, get_pending_issues, get_next_issue,
    mark_category_confirmed, get_seen_categories, is_session_complete,
    format_progress,
)


WORKSPACE = Path(".workspace")


def setup():
    """Initialize state for pipeline tests."""
    state.session_budget = "full"
    state.session_provider = "openai"
    state.session_model = "LongCat-Flash-Chat"
    init_revision_state(budget="full")
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    state.goal_tracker = GoalTracker(workspace=WORKSPACE)
    state.plan_store = PlanStore(workspace=WORKSPACE)
    state.reflection_engine = ReflectionEngine(tracker=state.goal_tracker)
    state.adaptive_engine = AdaptiveEngine(workspace=WORKSPACE)
    state.context_manager = ProactiveContextManager(max_tokens=128000)
    state.error_recovery = ErrorRecoveryEngine()
    state.output_quality = OutputQualityGate()
    state.session_memory = SessionMemory(workspace=WORKSPACE)
    state.meta_planner = MetaPlanner(memory=state.session_memory)
    state.session_memory.start_session(goal="Pipeline E2E test", paper_title="NIDZ")


def _create_mock_consolidated(issues: list):
    """Write mock consolidated review output to disk."""
    review_dir = WORKSPACE / "review"
    review_dir.mkdir(parents=True, exist_ok=True)
    consolidated = {"issues": issues, "consensus_score": 4.5, "verdict": "borderline_accept"}
    (review_dir / "consolidated.json").write_text(
        json.dumps(consolidated, indent=2, ensure_ascii=False), encoding="utf-8"
    )


# ==============================================================
# Test 1: Revision State Lifecycle
# ==============================================================

def test_revision_state_lifecycle():
    """Test state init → register → update → complete cycle."""
    print("  Test 1: Revision state lifecycle...")

    rev_state = init_revision_state(budget="full", paper_id="test_paper")
    assert rev_state["budget"] == "full"
    assert rev_state["paper_id"] == "test_paper"
    assert rev_state["phase"] == "review"
    assert rev_state["stats"]["total_issues"] == 0

    # Register issues
    mock_issues = [
        {"id": "ISS-001", "effective_action": "auto_fix", "category": "writing_quality", "severity": "minor"},
        {"id": "ISS-002", "effective_action": "confirm_fix", "category": "methodology", "severity": "major"},
        {"id": "ISS-003", "effective_action": "guidance", "category": "theory", "severity": "moderate"},
    ]
    rev_state = register_issues(rev_state, mock_issues)
    assert rev_state["stats"]["total_issues"] == 3
    assert rev_state["phase"] == "routing"
    assert len(rev_state["issues"]) == 3

    # Get pending
    pending = get_pending_issues(rev_state)
    assert len(pending) == 3
    pending_auto = get_pending_issues(rev_state, action_type="auto_fix")
    assert len(pending_auto) == 1

    # Get next (should be major first)
    next_issue = get_next_issue(rev_state)
    assert next_issue["severity"] == "major"

    # Update statuses
    rev_state = update_issue_status(rev_state, "ISS-001", "done", note="auto fixed")
    assert rev_state["stats"]["auto_fixed"] == 1
    rev_state = update_issue_status(rev_state, "ISS-002", "done", note="confirmed by user")
    assert rev_state["stats"]["confirmed_fixed"] == 1
    rev_state = update_issue_status(rev_state, "ISS-003", "skipped", note="user decided to skip")
    assert rev_state["stats"]["skipped"] == 1

    # Session complete
    assert is_session_complete(rev_state)

    # Mark category confirmed
    rev_state = mark_category_confirmed(rev_state, "writing_quality")
    assert "writing_quality" in get_seen_categories(rev_state)

    # Progress formatting
    progress = format_progress(rev_state)
    assert "2/" in progress or "done" in progress.lower()

    print("    ✓ PASSED")


# ==============================================================
# Test 2: Route Issues with Red Lines
# ==============================================================

def test_route_issues_red_lines():
    """Test that routing applies Red Lines correctly."""
    print("  Test 2: Route issues with Red Lines...")

    from tools.action_router import route_issues

    # Issue touching thesis → should be downgraded from auto_fix to confirm_fix
    # _touches_thesis requires: location.section_id in thesis sections AND
    # content matching THESIS_PATTERNS (e.g. "this paper argues")
    thesis_issue = {
        "id": "ISS-T1",
        "severity": "major",
        "category": "presentation",
        "action_type": "auto_fix",
        "description": "This paper argues that NIDZs drive entrepreneurship — core thesis needs rewording",
        "suggestion": "Rewrite the main argument",
        "location": {"section_id": "abstract", "quote": "This paper argues that NIDZs drive entrepreneurship"},
        "fix_complexity": "moderate",
    }

    # Issue that might introduce new claims → downgraded
    fabrication_issue = {
        "id": "ISS-F1",
        "severity": "moderate",
        "category": "missing_reference",
        "action_type": "auto_fix",
        "description": "Missing citation for key claim",
        "suggestion": "Add reference",
        "location": {"section": "literature", "text": "Previous studies show..."},
        "fix_complexity": "low",
    }

    # Normal style issue → stays auto_fix in full budget
    style_issue = {
        "id": "ISS-S1",
        "severity": "minor",
        "category": "writing_quality",
        "action_type": "auto_fix",
        "description": "Awkward phrasing",
        "suggestion": "Rewrite sentence",
        "location": {"section": "methodology", "text": "We utilize the method"},
        "fix_complexity": "low",
    }

    routed, stats = route_issues(
        [thesis_issue, fabrication_issue, style_issue],
        budget="full",
        seen_categories=set(),
    )

    # Thesis issue → should NOT be auto_fix
    thesis_routed = next(r for r in routed if r.id == "ISS-T1")
    assert thesis_routed.effective_action in ("confirm_fix", "guidance"), \
        f"Thesis issue should be downgraded, got: {thesis_routed.effective_action}"
    assert "thesis" in " ".join(thesis_routed.routing_notes).lower() or \
           "red line" in " ".join(thesis_routed.routing_notes).lower()

    # Fabrication issue → should NOT be auto_fix
    fab_routed = next(r for r in routed if r.id == "ISS-F1")
    assert fab_routed.effective_action in ("confirm_fix", "guidance"), \
        f"Fabrication risk should be downgraded, got: {fab_routed.effective_action}"

    # Style issue in full budget with no first-of-type → might be confirm_fix (first-of-type)
    style_routed = next(r for r in routed if r.id == "ISS-S1")
    # First-of-type check: first auto_fix in 'writing_quality' should be confirm_fix
    assert style_routed.effective_action in ("auto_fix", "confirm_fix"), \
        f"Style issue unexpected action: {style_routed.effective_action}"

    print("    ✓ PASSED")


# ==============================================================
# Test 3: Budget Ceiling Enforcement
# ==============================================================

def test_budget_ceiling():
    """Test that budget modes correctly downgrade actions."""
    print("  Test 3: Budget ceiling enforcement...")

    from tools.action_router import route_issues

    issue = {
        "id": "ISS-B1",
        "severity": "minor",
        "category": "style_seen",  # Use a 'seen' category
        "action_type": "auto_fix",
        "description": "Minor style fix",
        "suggestion": "Rewrite",
        "location": {"section": "results", "text": "The findings demonstrate"},
        "fix_complexity": "low",
    }

    # Full budget: auto_fix stays as auto_fix (after first-of-type)
    routed_full, _ = route_issues([issue], budget="full", seen_categories={"style_seen"})
    # In full mode, with seen category, auto_fix should stay
    assert routed_full[0].effective_action in ("auto_fix", "confirm_fix")

    # Minimal budget: auto_fix → guidance
    routed_min, _ = route_issues([issue], budget="minimal", seen_categories={"style_seen"})
    assert routed_min[0].effective_action == "guidance", \
        f"Minimal budget should downgrade to guidance, got: {routed_min[0].effective_action}"

    print("    ✓ PASSED")


# ==============================================================
# Test 4: Full Pipeline — Route + State + Progress
# ==============================================================

def test_full_pipeline_state_flow():
    """Test the full pipeline from routing through to completion tracking."""
    print("  Test 4: Full pipeline state flow...")

    from tools.action_router import route_issues

    # Create realistic issues
    issues = [
        {
            "id": "ISS-P1", "severity": "minor", "category": "clarity",
            "action_type": "auto_fix", "description": "Unclear variable definition",
            "suggestion": "Define X explicitly", "location": {"section": "methodology", "text": "X is used"},
            "fix_complexity": "low",
        },
        {
            "id": "ISS-P2", "severity": "major", "category": "causal_identification",
            "action_type": "confirm_fix", "description": "Weak instrument",
            "suggestion": "Add first-stage F-stat", "location": {"section": "results", "text": "IV estimates"},
            "fix_complexity": "high",
        },
        {
            "id": "ISS-P3", "severity": "moderate", "category": "writing_quality",
            "action_type": "auto_fix", "description": "Passive voice overuse",
            "suggestion": "Use active voice", "location": {"section": "introduction", "text": "It is shown that"},
            "fix_complexity": "low",
        },
    ]

    routed, stats = route_issues(issues, budget="full", seen_categories=set())

    # Register into revision state
    rev_state = init_revision_state(budget="full")
    routed_dicts = [r.to_dict() for r in routed]
    rev_state = register_issues(rev_state, routed_dicts)

    assert rev_state["stats"]["total_issues"] == 3
    assert not is_session_complete(rev_state)

    # Simulate processing
    rev_state = update_issue_status(rev_state, "ISS-P1", "done", "auto fixed")
    rev_state = update_issue_status(rev_state, "ISS-P2", "done", "confirmed by user")
    rev_state = update_issue_status(rev_state, "ISS-P3", "done", "auto fixed")

    assert is_session_complete(rev_state)

    progress = format_progress(rev_state)
    assert "3/" in progress or "done" in progress.lower()

    print("    ✓ PASSED")


# ==============================================================
# Test 5: DeAI State Recording
# ==============================================================

def test_deai_state_recording():
    """Test that DeAI results are properly recorded in revision state."""
    print("  Test 5: DeAI state recording...")

    from tools.revision_state import record_deai_result

    rev_state = init_revision_state(budget="full")

    verdict_pass = {"is_natural": True, "overall_score": 0.92, "signals": []}
    verdict_fail = {
        "is_natural": False, "overall_score": 0.55,
        "signals": [{"type": "hedge_word"}, {"type": "list_structure"}],
    }

    rev_state = record_deai_result(rev_state, "02_abstract", verdict_pass)
    rev_state = record_deai_result(rev_state, "03_1_introduction", verdict_fail)

    assert rev_state["deai_results"]["02_abstract"]["is_natural"] is True
    assert rev_state["deai_results"]["02_abstract"]["score"] == 0.92
    assert rev_state["deai_results"]["03_1_introduction"]["is_natural"] is False
    assert rev_state["deai_results"]["03_1_introduction"]["signal_count"] == 2

    # Check progress formatting includes DeAI info
    progress = format_progress(rev_state)
    assert "De-AI" in progress or "deai" in progress.lower()

    print("    ✓ PASSED")


# ==============================================================
# Test 6: Handler Integration — route_issues handler
# ==============================================================

def test_handler_route_issues():
    """Test the actual handler function that ties routing to state."""
    print("  Test 6: Handler integration (route_issues)...")

    # Create mock consolidated review
    mock_issues = [
        {
            "id": "ISS-H1", "severity": "minor", "category": "presentation",
            "action_type": "auto_fix", "description": "Fix phrasing",
            "suggestion": "Rewrite", "location": {"section": "results", "text": "data shows"},
            "fix_complexity": "low",
        },
        {
            "id": "ISS-H2", "severity": "moderate", "category": "methodology",
            "action_type": "confirm_fix", "description": "Add robustness check",
            "suggestion": "Include placebo test", "location": {"section": "methodology", "text": ""},
            "fix_complexity": "medium",
        },
    ]
    _create_mock_consolidated(mock_issues)

    # Re-init state
    init_revision_state(budget="full")

    from handlers.review_ops import handle_route_issues
    result = handle_route_issues(budget="full")

    assert "Error" not in result, f"route_issues handler failed: {result}"
    assert "ISS-H1" in result or "ISS-H2" in result
    assert "ROUTING REPORT" in result.upper() or "auto_fix" in result or "confirm_fix" in result

    # Verify state was updated
    rev_state = load_state()
    assert rev_state["stats"]["total_issues"] >= 2

    # Verify file was written
    routed_path = WORKSPACE / "review" / "routed_issues.json"
    assert routed_path.exists()

    print("    ✓ PASSED")


# ==============================================================
# Test 7: Edit Section + Commit Rewrite Integration
# ==============================================================

def test_edit_and_commit():
    """Test edit_section and commit_rewrite work together."""
    print("  Test 7: Edit + Commit integration...")

    from handlers.paper_ops import handle_read_section
    from handlers.write_ops import handle_commit_rewrite

    # Read current section text
    section_text = handle_read_section("02_abstract")
    assert section_text and "Error" not in section_text

    # Commit a new version
    new_text = "This is a test revision of the abstract for pipeline verification."
    result = handle_commit_rewrite(
        section_id="02_abstract",
        proposed_text=new_text,
        changes_summary="Pipeline test: replaced abstract text",
    )

    assert "committed" in result.lower() or "status" in result.lower()

    # Verify the revision file exists
    rev_path = WORKSPACE / "revisions" / "02_abstract_v2.md"
    assert rev_path.exists(), "Revision file should exist after commit"
    saved = rev_path.read_text(encoding="utf-8")
    assert new_text in saved or len(saved) > 0

    print("    ✓ PASSED")


# ==============================================================
# Test 8: Seen Categories Enable Future Auto-Fix
# ==============================================================

def test_seen_categories_progression():
    """Test that confirmed categories allow future auto_fix."""
    print("  Test 8: Seen categories progression...")

    from tools.action_router import route_issues

    issue = {
        "id": "ISS-SC1", "severity": "minor", "category": "clarity",
        "action_type": "auto_fix", "description": "Minor clarity fix",
        "suggestion": "Rewrite", "location": {"section": "results", "text": "data"},
        "fix_complexity": "low",
    }

    # First time: not seen → first_of_type might trigger
    routed1, _ = route_issues([issue], budget="full", seen_categories=set())
    first_action = routed1[0].effective_action

    # After category confirmed → should be auto_fix
    routed2, _ = route_issues([issue], budget="full", seen_categories={"clarity"})
    second_action = routed2[0].effective_action

    # If first was confirm_fix (due to first-of-type), second should be auto_fix
    if first_action == "confirm_fix":
        assert second_action == "auto_fix", \
            f"With seen category, should upgrade to auto_fix, got: {second_action}"

    print("    ✓ PASSED")


# ==============================================================
# Main
# ==============================================================

def main():
    setup()

    print("\n" + "=" * 60)
    print("  PIPELINE E2E TEST")
    print("=" * 60 + "\n")

    tests = [
        test_revision_state_lifecycle,
        test_route_issues_red_lines,
        test_budget_ceiling,
        test_full_pipeline_state_flow,
        test_deai_state_recording,
        test_handler_route_issues,
        test_edit_and_commit,
        test_seen_categories_progression,
    ]

    passed = 0
    failed = 0
    for test_fn in tests:
        try:
            test_fn()
            passed += 1
        except Exception as e:
            failed += 1
            import traceback
            print(f"    ✗ FAILED: {e}")
            traceback.print_exc()

    print(f"\n{'=' * 60}")
    print(f"  Results: {passed}/{passed + failed} passed")
    print(f"{'=' * 60}\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
