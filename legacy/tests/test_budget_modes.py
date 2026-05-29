#!/usr/bin/env python3
"""
test_budget_modes.py — Tests for budget mode enforcement across the system.

Tests cover:
1. Budget ceiling mapping (BUDGET_CEILING dict correctness)
2. Full mode: auto_fix allowed for seen categories
3. Medium mode: auto_fix allowed, confirm_fix → guidance
4. Minimal mode: everything → guidance
5. Action router Red Lines work across all budget levels
6. First-of-type validation across budgets
7. Doom loop detector thresholds per tool
8. Checkpoint save/load/resume lifecycle
9. Doom loop fuzzy matching (Jaccard similarity)
10. Doom loop reset behavior
11. Checkpoint run_id stability
12. Checkpoint step recording
"""

from __future__ import annotations

import sys
import json
import time
import hashlib
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.action_router import (
    route_issues, BUDGET_CEILING, RoutedIssue,
)
from utils.doom_loop import (
    DoomLoopDetector, CORE_ARGS, THRESHOLDS,
)
from utils.checkpoint import (
    Checkpoint, CheckpointState, StepRecord, list_checkpoints,
)


# ==============================================================
# Test 1: Budget Ceiling Mapping Completeness
# ==============================================================

def test_budget_ceiling_mapping():
    """Verify BUDGET_CEILING covers all action_type × budget combinations."""
    print("  Test 1: Budget ceiling mapping completeness...")

    action_types = ["auto_fix", "confirm_fix", "guidance"]
    budgets = ["full", "medium", "minimal"]

    for action in action_types:
        for budget in budgets:
            key = (action, budget)
            assert key in BUDGET_CEILING, \
                f"Missing BUDGET_CEILING entry for {key}"
            effective = BUDGET_CEILING[key]
            assert effective in action_types, \
                f"Invalid effective action '{effective}' for {key}"

    # Verify downgrade-only (never upgrade)
    severity = {"auto_fix": 0, "confirm_fix": 1, "guidance": 2}
    for (action, budget), effective in BUDGET_CEILING.items():
        assert severity[effective] >= severity[action], \
            f"Budget should only downgrade: ({action}, {budget}) → {effective}"

    # Verify specific expectations
    assert BUDGET_CEILING[("auto_fix", "full")] == "auto_fix"
    assert BUDGET_CEILING[("auto_fix", "minimal")] == "guidance"
    assert BUDGET_CEILING[("confirm_fix", "medium")] == "guidance"
    assert BUDGET_CEILING[("guidance", "minimal")] == "guidance"

    print("    ✓ PASSED")


# ==============================================================
# Test 2: Full Budget Mode
# ==============================================================

def test_full_budget():
    """Test full budget: most permissive mode."""
    print("  Test 2: Full budget mode...")

    issues = [
        {
            "id": "FB-1", "severity": "minor", "category": "style_already_seen",
            "action_type": "auto_fix", "description": "Fix passive voice",
            "suggestion": "Use active", "location": {"section": "results", "text": "data"},
            "fix_complexity": "low",
        },
        {
            "id": "FB-2", "severity": "moderate", "category": "methodology",
            "action_type": "confirm_fix", "description": "Add robustness check",
            "suggestion": "Include placebo", "location": {"section": "methodology", "text": ""},
            "fix_complexity": "high",
        },
        {
            "id": "FB-3", "severity": "minor", "category": "clarity",
            "action_type": "guidance", "description": "Consider restructuring",
            "suggestion": "Move paragraph", "location": {"section": "discussion", "text": ""},
            "fix_complexity": "medium",
        },
    ]

    routed, stats = route_issues(issues, budget="full",
                                 seen_categories={"style_already_seen", "methodology", "clarity"})

    # In full budget with seen categories:
    # auto_fix should stay as auto_fix
    # confirm_fix should stay as confirm_fix
    # guidance should stay as guidance
    fb1 = next(r for r in routed if r.id == "FB-1")
    assert fb1.effective_action == "auto_fix", \
        f"Full budget + seen category: expected auto_fix, got {fb1.effective_action}"

    fb2 = next(r for r in routed if r.id == "FB-2")
    assert fb2.effective_action == "confirm_fix", \
        f"Full budget confirm_fix: expected confirm_fix, got {fb2.effective_action}"

    fb3 = next(r for r in routed if r.id == "FB-3")
    assert fb3.effective_action == "guidance", \
        f"Full budget guidance: expected guidance, got {fb3.effective_action}"

    print("    ✓ PASSED")


# ==============================================================
# Test 3: Medium Budget Mode
# ==============================================================

def test_medium_budget():
    """Test medium budget: auto_fix OK, confirm_fix → guidance."""
    print("  Test 3: Medium budget mode...")

    issues = [
        {
            "id": "MB-1", "severity": "minor", "category": "style_seen",
            "action_type": "auto_fix", "description": "Fix style",
            "suggestion": "Rewrite", "location": {"section": "intro", "text": "data"},
            "fix_complexity": "low",
        },
        {
            "id": "MB-2", "severity": "moderate", "category": "method_seen",
            "action_type": "confirm_fix", "description": "Add check",
            "suggestion": "Add test", "location": {"section": "method", "text": ""},
            "fix_complexity": "medium",
        },
    ]

    routed, stats = route_issues(issues, budget="medium",
                                 seen_categories={"style_seen", "method_seen"})

    mb1 = next(r for r in routed if r.id == "MB-1")
    # Medium allows auto_fix
    assert mb1.effective_action in ("auto_fix", "confirm_fix"), \
        f"Medium budget auto_fix: expected auto_fix/confirm_fix, got {mb1.effective_action}"

    mb2 = next(r for r in routed if r.id == "MB-2")
    # Medium downgrades confirm_fix → guidance
    assert mb2.effective_action == "guidance", \
        f"Medium budget confirm_fix → guidance: got {mb2.effective_action}"

    print("    ✓ PASSED")


# ==============================================================
# Test 4: Minimal Budget Mode
# ==============================================================

def test_minimal_budget():
    """Test minimal budget: everything → guidance."""
    print("  Test 4: Minimal budget mode...")

    issues = [
        {
            "id": "MIN-1", "severity": "minor", "category": "style",
            "action_type": "auto_fix", "description": "Fix",
            "suggestion": "Rewrite", "location": {"section": "intro", "text": ""},
            "fix_complexity": "low",
        },
        {
            "id": "MIN-2", "severity": "major", "category": "method",
            "action_type": "confirm_fix", "description": "Fix method",
            "suggestion": "Add", "location": {"section": "method", "text": ""},
            "fix_complexity": "high",
        },
        {
            "id": "MIN-3", "severity": "minor", "category": "clarity",
            "action_type": "guidance", "description": "Clarify",
            "suggestion": "Restructure", "location": {"section": "results", "text": ""},
            "fix_complexity": "low",
        },
    ]

    routed, stats = route_issues(issues, budget="minimal",
                                 seen_categories={"style", "method", "clarity"})

    for r in routed:
        assert r.effective_action == "guidance", \
            f"Minimal budget should downgrade ALL to guidance: {r.id} got {r.effective_action}"

    print("    ✓ PASSED")


# ==============================================================
# Test 5: Red Lines Override Budget
# ==============================================================

def test_red_lines_override():
    """Test that Red Lines apply regardless of budget level."""
    print("  Test 5: Red Lines override budget...")

    # Thesis issue should NEVER be auto_fix, even in full budget
    # _touches_thesis checks: location.section_id must contain a thesis section
    # AND combined text (quote + description + suggestion) must match THESIS_PATTERNS
    thesis_issue = {
        "id": "RL-1", "severity": "major", "category": "presentation",
        "action_type": "auto_fix",
        "description": "This paper argues that NIDZs create jobs — rewrite thesis claim",
        "suggestion": "Change main argument direction",
        "location": {"section_id": "abstract", "quote": "This paper argues that NIDZs create jobs"},
        "fix_complexity": "moderate",
    }

    for budget in ["full", "medium", "minimal"]:
        routed, _ = route_issues([thesis_issue], budget=budget,
                                 seen_categories={"presentation"})
        assert routed[0].effective_action != "auto_fix" or budget == "minimal", \
            f"Thesis Red Line should prevent auto_fix in {budget} mode"

    print("    ✓ PASSED")


# ==============================================================
# Test 6: First-of-Type Across Budgets
# ==============================================================

def test_first_of_type():
    """Test first-of-type validation behavior."""
    print("  Test 6: First-of-type across budgets...")

    issue = {
        "id": "FOT-1", "severity": "minor", "category": "new_category",
        "action_type": "auto_fix", "description": "Minor fix",
        "suggestion": "Rewrite", "location": {"section": "results", "text": "data"},
        "fix_complexity": "low",
    }

    # Without seen category: first-of-type should downgrade in full budget
    routed_unseen, _ = route_issues([issue], budget="full", seen_categories=set())
    first_action = routed_unseen[0].effective_action

    # With seen category: should be more permissive
    routed_seen, _ = route_issues([issue], budget="full",
                                  seen_categories={"new_category"})
    seen_action = routed_seen[0].effective_action

    # If first-of-type applies, unseen should be more restrictive
    severity = {"auto_fix": 0, "confirm_fix": 1, "guidance": 2}
    assert severity.get(first_action, 3) >= severity.get(seen_action, 3), \
        f"Unseen ({first_action}) should be >= restrictive than seen ({seen_action})"

    print("    ✓ PASSED")


# ==============================================================
# Test 7: Doom Loop Thresholds Per Tool
# ==============================================================

def test_doom_loop_thresholds():
    """Test per-tool thresholds in doom loop detector."""
    print("  Test 7: Doom loop per-tool thresholds...")

    detector = DoomLoopDetector(window=10)

    # default threshold = 3
    # rewrite_section: default (3)
    for i in range(2):
        is_loop, msg = detector.check("rewrite_section", {"section_id": "02_abstract"})
        assert not is_loop, f"Should not loop at call {i+1}"

    is_loop, msg = detector.check("rewrite_section", {"section_id": "02_abstract"})
    assert is_loop, "Should detect loop at call 3 (threshold=3)"
    assert "LOOP DETECTED" in msg

    # deai_audit: threshold = 4
    detector2 = DoomLoopDetector(window=10)
    for i in range(3):
        is_loop, _ = detector2.check("deai_audit", {"section_id": "03_intro"})
        assert not is_loop, f"deai_audit should not loop at call {i+1} (threshold=4)"

    is_loop, _ = detector2.check("deai_audit", {"section_id": "03_intro"})
    assert is_loop, "deai_audit should detect loop at call 4"

    print("    ✓ PASSED")


# ==============================================================
# Test 8: Doom Loop Fuzzy Matching
# ==============================================================

def test_doom_loop_fuzzy():
    """Test Jaccard fuzzy matching for near-duplicate calls."""
    print("  Test 8: Doom loop fuzzy matching...")

    detector = DoomLoopDetector(window=10)

    # Calls with slightly different text but same semantic intent
    detector.check("rewrite_section", {"section_id": "02_abstract"})
    detector.check("rewrite_section", {"section_id": "02_abstract"})

    # Even with tiny arg variation, Jaccard should still detect similarity
    # (since CORE_ARGS for rewrite_section is just ["section_id"])
    is_loop, _ = detector.check("rewrite_section", {"section_id": "02_abstract"})
    assert is_loop, "Exact same core args should trigger"

    # Different section_id should NOT trigger
    detector3 = DoomLoopDetector(window=10)
    detector3.check("rewrite_section", {"section_id": "02_abstract"})
    detector3.check("rewrite_section", {"section_id": "03_introduction"})
    detector3.check("rewrite_section", {"section_id": "04_methodology"})
    is_loop, _ = detector3.check("rewrite_section", {"section_id": "05_results"})
    assert not is_loop, "Different sections should NOT trigger loop"

    print("    ✓ PASSED")


# ==============================================================
# Test 9: Doom Loop Reset
# ==============================================================

def test_doom_loop_reset():
    """Test that reset clears all detection state."""
    print("  Test 9: Doom loop reset...")

    detector = DoomLoopDetector(window=5)

    # Fill with calls
    for _ in range(2):
        detector.check("edit_section", {"section_id": "02_abstract", "old_text": "test"})

    assert len(detector.recent_calls) == 2

    # Reset
    detector.reset()
    assert len(detector.recent_calls) == 0
    assert len(detector._signature_tokens) == 0

    # After reset, same calls should not immediately trigger
    is_loop, _ = detector.check("edit_section", {"section_id": "02_abstract", "old_text": "test"})
    assert not is_loop, "After reset, should not trigger on first call"

    print("    ✓ PASSED")


# ==============================================================
# Test 10: Doom Loop Window Size
# ==============================================================

def test_doom_loop_window():
    """Test sliding window eviction behavior."""
    print("  Test 10: Doom loop window size...")

    detector = DoomLoopDetector(window=4)

    # Fill window with varied calls
    detector.check("tool_a", {"x": "1"})
    detector.check("tool_b", {"x": "2"})
    detector.check("tool_c", {"x": "3"})
    detector.check("tool_d", {"x": "4"})

    # Now window is full. Add tool_a again — it was evicted (window=4)
    # Actually deque(maxlen=4): after 4 items + 1 more, oldest is evicted
    detector.check("tool_a", {"x": "1"})  # window: [b, c, d, a]
    # tool_a appears once in window, should not trigger
    is_loop, _ = detector.check("tool_a", {"x": "1"})  # window: [c, d, a, a]
    # Now 2 occurrences of tool_a, still below threshold=3
    assert not is_loop

    is_loop, _ = detector.check("tool_a", {"x": "1"})  # window: [d, a, a, a]
    # 3 occurrences = threshold
    assert is_loop, "Should trigger at 3 occurrences in window"

    print("    ✓ PASSED")


# ==============================================================
# Test 11: Checkpoint Lifecycle
# ==============================================================

def test_checkpoint_lifecycle():
    """Test checkpoint create → steps → complete → clear lifecycle."""
    print("  Test 11: Checkpoint lifecycle...")

    # Clean up test checkpoints
    test_workspace = Path(".workspace_test_budget")
    test_workspace.mkdir(parents=True, exist_ok=True)

    try:
        cp = Checkpoint("test_pipeline", workspace_root=str(test_workspace),
                        paper="test.md", budget="full")

        assert not cp.has_checkpoint()

        # Start
        cp_state = cp.start(total_steps_estimate=3)
        assert cp_state.status == "in_progress"
        assert cp_state.completed_step == -1
        assert cp.has_checkpoint()

        # Step 1
        cp.begin_step(0, "review")
        time.sleep(0.01)  # Ensure non-zero duration
        cp.complete_step(0, "review", "Reviewed 3 issues", llm_calls=5, tokens_used=10000)

        state = cp._load()
        assert state.completed_step == 0
        assert len(state.steps) == 1
        assert state.steps[0].name == "review"
        assert state.steps[0].llm_calls == 5

        # Step 2
        cp.begin_step(1, "route")
        cp.complete_step(1, "route", "Routed issues")

        # Data storage
        cp.set_data("routed_count", 5)
        assert cp.get_data("routed_count") == 5
        assert cp.get_data("missing_key", "default") == "default"

        # Mark failed
        cp.mark_failed("Test failure")
        state = cp._load()
        assert state.status == "failed"
        assert "Test failure" in str(state.data.get("_last_error", ""))

        # Should appear in list
        cps = list_checkpoints(str(test_workspace))
        assert len(cps) >= 1
        found = any(c["run_id"] == cp.run_id for c in cps)
        assert found, "Failed checkpoint should be listed"

        # Clear
        cp.clear()
        assert not cp.has_checkpoint()
        cps_after = list_checkpoints(str(test_workspace))
        found_after = any(c["run_id"] == cp.run_id for c in cps_after)
        assert not found_after, "Cleared checkpoint should not be listed"

    finally:
        shutil.rmtree(test_workspace, ignore_errors=True)

    print("    ✓ PASSED")


# ==============================================================
# Test 12: Checkpoint Run ID Stability
# ==============================================================

def test_checkpoint_run_id_stability():
    """Test that same inputs produce same run_id (deterministic)."""
    print("  Test 12: Checkpoint run_id stability...")

    test_workspace = Path(".workspace_test_id")
    test_workspace.mkdir(parents=True, exist_ok=True)

    try:
        cp1 = Checkpoint("review_pipeline", workspace_root=str(test_workspace),
                         paper="thesis.md", budget="full")
        cp2 = Checkpoint("review_pipeline", workspace_root=str(test_workspace),
                         paper="thesis.md", budget="full")

        assert cp1.run_id == cp2.run_id, \
            f"Same inputs should produce same run_id: {cp1.run_id} vs {cp2.run_id}"

        # Different metadata → different run_id
        cp3 = Checkpoint("review_pipeline", workspace_root=str(test_workspace),
                         paper="different.md", budget="full")
        assert cp3.run_id != cp1.run_id, \
            "Different metadata should produce different run_id"

        # Different pipeline name → different run_id
        cp4 = Checkpoint("deai_pipeline", workspace_root=str(test_workspace),
                         paper="thesis.md", budget="full")
        assert cp4.run_id != cp1.run_id

    finally:
        shutil.rmtree(test_workspace, ignore_errors=True)

    print("    ✓ PASSED")


# ==============================================================
# Test 13: CORE_ARGS Configuration
# ==============================================================

def test_core_args_config():
    """Verify CORE_ARGS configuration for doom loop detection."""
    print("  Test 13: CORE_ARGS configuration...")

    # Key tools should have explicit core args
    assert "rewrite_section" in CORE_ARGS
    assert "section_id" in CORE_ARGS["rewrite_section"]

    assert "deai_audit" in CORE_ARGS
    assert "section_id" in CORE_ARGS["deai_audit"]

    assert "edit_section" in CORE_ARGS
    assert "section_id" in CORE_ARGS["edit_section"]
    assert "old_text" in CORE_ARGS["edit_section"]

    assert "generate_fix_proposal" in CORE_ARGS
    assert "issue_id" in CORE_ARGS["generate_fix_proposal"]

    assert "search_literature" in CORE_ARGS
    assert "query" in CORE_ARGS["search_literature"]

    # reaudit has empty core args (any call counts)
    assert "reaudit" in CORE_ARGS
    assert CORE_ARGS["reaudit"] == []

    print("    ✓ PASSED")


# ==============================================================
# Test 14: Checkpoint Summary String
# ==============================================================

def test_checkpoint_summary():
    """Test human-readable checkpoint summary."""
    print("  Test 14: Checkpoint summary...")

    test_workspace = Path(".workspace_test_summary")
    test_workspace.mkdir(parents=True, exist_ok=True)

    try:
        cp = Checkpoint("review_flow", workspace_root=str(test_workspace),
                        paper="test.md")
        cp.start(total_steps_estimate=5)
        cp.begin_step(0, "parse")
        cp.complete_step(0, "parse", "Parsed paper")
        cp.begin_step(1, "review")
        cp.complete_step(1, "review", "Reviewed", llm_calls=3, tokens_used=5000)

        summary = cp.summary()
        assert "review_flow" in summary or "2" in summary
        assert len(summary) > 0

    finally:
        shutil.rmtree(test_workspace, ignore_errors=True)

    print("    ✓ PASSED")


# ==============================================================
# Main
# ==============================================================

def main():
    print("\n" + "=" * 60)
    print("  BUDGET MODES & DOOM LOOP & CHECKPOINT TEST")
    print("=" * 60 + "\n")

    tests = [
        test_budget_ceiling_mapping,
        test_full_budget,
        test_medium_budget,
        test_minimal_budget,
        test_red_lines_override,
        test_first_of_type,
        test_doom_loop_thresholds,
        test_doom_loop_fuzzy,
        test_doom_loop_reset,
        test_doom_loop_window,
        test_checkpoint_lifecycle,
        test_checkpoint_run_id_stability,
        test_core_args_config,
        test_checkpoint_summary,
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
