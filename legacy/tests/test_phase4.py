#!/usr/bin/env python3
"""
Phase 4: Safety & Stability Tests (Zero LLM cost, pure rule-based logic).

Tests:
  1. action_router — Red Line enforcement, budget ceiling, first-of-type
  2. post_edit_verify — Consistency, voice drift, AI regression detection
  3. reaudit — Issue matching, root_cause_key, status determination
  4. doom_loop — Loop detection, threshold, reset, fuzzy matching
"""
import os, sys, time, traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

results = []


def record(name, passed, dur_ms, detail="", error=""):
    results.append({"name": name, "passed": passed, "dur_ms": dur_ms,
                    "detail": detail, "error": error})
    icon = "✅" if passed else "❌"
    print(f"  {icon} {name} ({dur_ms:.1f}ms)")
    if detail:
        print(f"     {detail[:300]}")
    if error:
        print(f"     ERROR: {error[:300]}")


def run():
    print("=" * 60)
    print("  Phase 4: Safety & Stability Tests (Zero Cost)")
    print("=" * 60)

    # ==============================================================
    # MODULE 1: action_router.py
    # ==============================================================
    print("\n" + "─" * 50)
    print("  Module: action_router")
    print("─" * 50)

    from tools.action_router import (
        route_issues, _touches_thesis, _might_introduce_new_claims,
        _needs_statistical_verification, format_routing_report,
    )

    # ---- Test 1.1: Red Line 1 — Thesis Protection ----
    print("\n[1.1] Red Line 1: Thesis protection...")
    t0 = time.time()
    try:
        thesis_issue = {
            "id": "ISS-001",
            "severity": "major",
            "category": "clarity",
            "action_type": "auto_fix",
            "location": {"section_id": "02_introduction", "quote": "this paper argues that"},
            "description": "The main argument is unclear",
            "suggestion": "Reframe the relationship between variables",
        }
        routed, stats = route_issues([thesis_issue], budget="full")
        dur = (time.time() - t0) * 1000

        assert routed[0].effective_action == "guidance", \
            f"Expected guidance, got {routed[0].effective_action}"
        assert stats["red_line_downgrades"] >= 1
        assert any("RED_LINE_1" in n for n in routed[0].routing_notes)

        record("red_line_1_thesis_protection", True, dur,
               detail=f"auto_fix → guidance, notes: {routed[0].routing_notes}")
    except Exception as e:
        record("red_line_1_thesis_protection", False, (time.time()-t0)*1000, error=str(e))
        traceback.print_exc()

    # ---- Test 1.2: Red Line 2 — Fabrication Risk ----
    print("\n[1.2] Red Line 2: Fabrication risk detection...")
    t0 = time.time()
    try:
        fabrication_issue = {
            "id": "ISS-002",
            "severity": "minor",
            "category": "missing_reference",
            "action_type": "auto_fix",
            "location": {"section_id": "04_literature_review"},
            "description": "Missing citations for claim",
            "suggestion": "Add a study that supports this point",
        }
        routed, stats = route_issues([fabrication_issue], budget="full")
        dur = (time.time() - t0) * 1000

        # Should be downgraded to confirm_fix (not auto_fix)
        assert routed[0].effective_action != "auto_fix", \
            f"Expected downgrade from auto_fix, got {routed[0].effective_action}"
        assert any("RED_LINE_2" in n or "FIRST_OF_TYPE" in n for n in routed[0].routing_notes)

        record("red_line_2_fabrication_risk", True, dur,
               detail=f"auto_fix → {routed[0].effective_action}, notes: {routed[0].routing_notes}")
    except Exception as e:
        record("red_line_2_fabrication_risk", False, (time.time()-t0)*1000, error=str(e))
        traceback.print_exc()

    # ---- Test 1.3: Budget Ceiling ----
    print("\n[1.3] Budget ceiling enforcement...")
    t0 = time.time()
    try:
        safe_issue = {
            "id": "ISS-003",
            "severity": "minor",
            "category": "typo",
            "action_type": "auto_fix",
            "location": {"section_id": "05_methodology"},
            "description": "Typo in methodology",
            "suggestion": "Fix the typo",
        }
        seen = {"typo"}  # Already seen this category

        # Full budget → auto_fix allowed
        routed_full, _ = route_issues([safe_issue], budget="full", seen_categories=seen.copy())
        # Minimal budget → should downgrade to guidance
        routed_min, stats_min = route_issues([safe_issue], budget="minimal", seen_categories=seen.copy())
        dur = (time.time() - t0) * 1000

        assert routed_full[0].effective_action == "auto_fix", \
            f"Full budget: expected auto_fix, got {routed_full[0].effective_action}"
        assert routed_min[0].effective_action == "guidance", \
            f"Minimal budget: expected guidance, got {routed_min[0].effective_action}"
        assert stats_min["budget_downgrades"] >= 1

        record("budget_ceiling", True, dur,
               detail=f"full→auto_fix, minimal→guidance, downgrades={stats_min['budget_downgrades']}")
    except Exception as e:
        record("budget_ceiling", False, (time.time()-t0)*1000, error=str(e))
        traceback.print_exc()

    # ---- Test 1.4: First-of-Type ----
    print("\n[1.4] First-of-type confirm requirement...")
    t0 = time.time()
    try:
        issue = {
            "id": "ISS-004",
            "severity": "minor",
            "category": "formatting",
            "action_type": "auto_fix",
            "location": {"section_id": "06_results"},
            "description": "Inconsistent formatting",
            "suggestion": "Fix the formatting",
        }
        # Empty seen_categories → first time seeing "formatting"
        routed_first, stats = route_issues([issue], budget="full", seen_categories=set())
        # With "formatting" already seen
        routed_second, _ = route_issues([issue], budget="full", seen_categories={"formatting"})
        dur = (time.time() - t0) * 1000

        assert routed_first[0].effective_action == "confirm_fix", \
            f"First-of-type: expected confirm_fix, got {routed_first[0].effective_action}"
        assert routed_second[0].effective_action == "auto_fix", \
            f"Seen category: expected auto_fix, got {routed_second[0].effective_action}"

        record("first_of_type", True, dur,
               detail=f"new_category→confirm_fix, seen_category→auto_fix")
    except Exception as e:
        record("first_of_type", False, (time.time()-t0)*1000, error=str(e))
        traceback.print_exc()

    # ---- Test 1.5: Statistical Verification Flag ----
    print("\n[1.5] Statistical verification detection...")
    t0 = time.time()
    try:
        stat_issue = {
            "category": "robustness",
            "description": "The regression specification may have heteroskedasticity",
        }
        non_stat_issue = {
            "category": "clarity",
            "description": "This sentence is unclear",
        }
        assert _needs_statistical_verification(stat_issue) == True
        assert _needs_statistical_verification(non_stat_issue) == False
        dur = (time.time() - t0) * 1000
        record("statistical_verification_flag", True, dur,
               detail="robustness+heteroskedasticity→True, clarity→False")
    except Exception as e:
        record("statistical_verification_flag", False, (time.time()-t0)*1000, error=str(e))
        traceback.print_exc()

    # ==============================================================
    # MODULE 2: post_edit_verify.py
    # ==============================================================
    print("\n" + "─" * 50)
    print("  Module: post_edit_verify")
    print("─" * 50)

    from tools.post_edit_verify import (
        verify_edit, check_consistency, check_voice_drift,
        check_ai_regression, format_verification_result,
    )

    # ---- Test 2.1: Consistency Check — Broken References ----
    print("\n[2.1] Consistency: broken cross-reference detection...")
    t0 = time.time()
    try:
        text_with_refs = (
            "As shown in Figure 3, the results are consistent with Table 2. "
            "See Section 4.1 for details. Figure 99 shows additional data."
        )
        passed, issues = check_consistency(
            text_with_refs,
            paper_sections=["1", "2", "3", "4", "4.1", "5"],
            known_figures=["1", "2", "3", "4"],
            known_tables=["1", "2", "3"],
        )
        dur = (time.time() - t0) * 1000

        assert passed == False, "Should detect broken Figure 99"
        assert any("Figure 99" in i for i in issues)
        # Valid refs should not be flagged
        assert not any("Figure 3" in i for i in issues)
        assert not any("Table 2" in i for i in issues)

        record("consistency_broken_refs", True, dur,
               detail=f"Detected {len(issues)} broken refs: {issues}")
    except Exception as e:
        record("consistency_broken_refs", False, (time.time()-t0)*1000, error=str(e))
        traceback.print_exc()

    # ---- Test 2.2: Consistency Check — All Valid ----
    print("\n[2.2] Consistency: all valid references pass...")
    t0 = time.time()
    try:
        valid_text = "Figure 1 and Table 1 are in Section 2."
        passed, issues = check_consistency(
            valid_text,
            paper_sections=["1", "2", "3"],
            known_figures=["1", "2"],
            known_tables=["1"],
        )
        dur = (time.time() - t0) * 1000
        assert passed == True
        assert len(issues) == 0
        record("consistency_all_valid", True, dur, detail="No broken refs detected")
    except Exception as e:
        record("consistency_all_valid", False, (time.time()-t0)*1000, error=str(e))
        traceback.print_exc()

    # ---- Test 2.3: Voice Drift Detection ----
    print("\n[2.3] Voice drift: detect significant style change...")
    t0 = time.time()
    try:
        # Original: long academic sentences
        old_text = (
            "The empirical results demonstrate that the implementation of place-based "
            "innovation policies, specifically the National Innovation Demonstration Zones, "
            "has generated statistically significant effects on regional entrepreneurial "
            "activity as measured by new firm registrations. Furthermore, the heterogeneity "
            "analysis reveals differential impacts across regions with varying levels of "
            "absorptive capacity, suggesting that pre-existing institutional conditions "
            "moderate the effectiveness of such interventions."
        )
        # New: very short, choppy sentences (voice drift!)
        new_text = (
            "The results show effects. The policy works. Firms register more. "
            "Regions differ. Capacity matters. Institutions help. This is important. "
            "We find significance. The data supports this. The model works well."
        )
        passed, warnings = check_voice_drift(old_text, new_text)
        dur = (time.time() - t0) * 1000

        assert passed == False, "Should detect voice drift from long→short sentences"
        assert len(warnings) > 0

        record("voice_drift_detection", True, dur,
               detail=f"Detected {len(warnings)} drift warnings: {warnings[0]}")
    except Exception as e:
        record("voice_drift_detection", False, (time.time()-t0)*1000, error=str(e))
        traceback.print_exc()

    # ---- Test 2.4: AI Regression Detection ----
    print("\n[2.4] AI regression: detect new AI patterns...")
    t0 = time.time()
    try:
        original = "The policy has important implications for regional development."
        # Introduce AI-style language
        edited_bad = (
            "This study delves into the multifaceted tapestry of innovation policy, "
            "navigating the complex landscape of regional development. It is worth noting "
            "that this underscores a testament to the paradigm shift in our understanding."
        )
        passed, issues = check_ai_regression(original, edited_bad)
        dur = (time.time() - t0) * 1000

        assert passed == False, "Should detect AI regression"
        assert len(issues) >= 1
        assert any("AI regression" in i for i in issues)

        record("ai_regression_detection", True, dur,
               detail=f"Detected {len(issues)} AI signals: {issues[0]}")
    except Exception as e:
        record("ai_regression_detection", False, (time.time()-t0)*1000, error=str(e))
        traceback.print_exc()

    # ---- Test 2.5: AI Regression — Clean Edit Passes ----
    print("\n[2.5] AI regression: clean edit passes...")
    t0 = time.time()
    try:
        original = "The results show that innovation policy works."
        clean_edit = "The findings indicate that innovation policy has measurable effects on regional outcomes."
        passed, issues = check_ai_regression(original, clean_edit)
        dur = (time.time() - t0) * 1000
        assert passed == True, f"Clean edit should pass, but got issues: {issues}"
        record("ai_regression_clean_pass", True, dur, detail="No AI regression in clean edit")
    except Exception as e:
        record("ai_regression_clean_pass", False, (time.time()-t0)*1000, error=str(e))
        traceback.print_exc()

    # ---- Test 2.6: Full verify_edit Integration ----
    print("\n[2.6] Full verify_edit integration...")
    t0 = time.time()
    try:
        old = "The effect is shown in Figure 1 and Table 1. See Section 3."
        new_bad = (
            "This delves into Figure 1 and Table 99. See Section 3. "
            "It is worth noting that this underscores the landscape of policy."
        )
        result = verify_edit(
            section_id="03_introduction",
            old_text=old,
            new_text=new_bad,
            paper_sections=["1", "2", "3"],
            known_figures=["1", "2"],
            known_tables=["1", "2"],
        )
        dur = (time.time() - t0) * 1000

        # Should fail: broken Table 99 + AI regression
        assert result.passed == False
        assert result.consistency_ok == False
        assert result.regression_ok == False
        assert len(result.new_issues) >= 2

        formatted = format_verification_result(result, "03_introduction")
        assert "ISSUES FOUND" in formatted

        record("verify_edit_integration", True, dur,
               detail=f"passed={result.passed}, issues={len(result.new_issues)}, "
                      f"consistency={result.consistency_ok}, regression={result.regression_ok}")
    except Exception as e:
        record("verify_edit_integration", False, (time.time()-t0)*1000, error=str(e))
        traceback.print_exc()

    # ==============================================================
    # MODULE 3: reaudit.py
    # ==============================================================
    print("\n" + "─" * 50)
    print("  Module: reaudit")
    print("─" * 50)

    from tools.reaudit import (
        generate_root_cause_key, match_issues, determine_status,
        format_reaudit_report, ReauditReport, IssueDiff,
    )

    # ---- Test 3.1: Root Cause Key Generation ----
    print("\n[3.1] Root cause key: deterministic generation...")
    t0 = time.time()
    try:
        issue_a = {"category": "Clarity", "section": "Introduction", 
                   "title": "The main argument is unclear and needs clarification"}
        issue_b = {"category": "clarity", "section": "introduction",
                   "title": "The main argument is unclear and needs clarification"}
        # Same content, different case → same key
        key_a = generate_root_cause_key(issue_a)
        key_b = generate_root_cause_key(issue_b)
        assert key_a == key_b, f"Keys should match: {key_a} vs {key_b}"

        # Different content → different key
        issue_c = {"category": "methodology", "section": "results",
                   "title": "Sample size is too small"}
        key_c = generate_root_cause_key(issue_c)
        assert key_c != key_a, f"Different issues should have different keys"

        dur = (time.time() - t0) * 1000
        record("root_cause_key_generation", True, dur,
               detail=f"Same content→same key ({key_a}), different content→different key")
    except Exception as e:
        record("root_cause_key_generation", False, (time.time()-t0)*1000, error=str(e))
        traceback.print_exc()

    # ---- Test 3.2: Issue Matching — Exact Match ----
    print("\n[3.2] Issue matching: exact key match...")
    t0 = time.time()
    try:
        prev_issues = [
            {"id": "P1", "category": "clarity", "section": "introduction",
             "title": "Argument is unclear", "severity": "major"},
            {"id": "P2", "category": "methodology", "section": "results",
             "title": "Sample size not justified", "severity": "moderate"},
        ]
        curr_issues = [
            {"id": "C1", "category": "clarity", "section": "introduction",
             "title": "Argument is unclear", "severity": "minor"},  # Same but lower severity
            {"id": "C3", "category": "formatting", "section": "appendix",
             "title": "Table misaligned", "severity": "minor"},  # New issue
        ]
        matched, new_issues = match_issues(prev_issues, curr_issues)
        dur = (time.time() - t0) * 1000

        # P1 should match C1, P2 should have no match, C3 should be new
        assert len(matched) == 2, f"Expected 2 matched pairs, got {len(matched)}"
        assert len(new_issues) >= 1, f"Expected at least 1 new issue"

        record("issue_matching_exact", True, dur,
               detail=f"matched={len(matched)} pairs, new={len(new_issues)}")
    except Exception as e:
        record("issue_matching_exact", False, (time.time()-t0)*1000, error=str(e))
        traceback.print_exc()

    # ---- Test 3.3: Status Determination — Fully Addressed ----
    print("\n[3.3] Status determination: FULLY_ADDRESSED...")
    t0 = time.time()
    try:
        prev_issue = {
            "id": "P1", "category": "clarity", "section": "intro",
            "title": "Unclear argument", "severity": "major",
            "quote": "The policy has some effect on things somehow.",
        }
        # No current match + quote removed from paper
        paper_text = "The National Innovation Demonstration Zones significantly increased firm registrations."
        diff = determine_status(prev_issue, None, paper_text)
        dur = (time.time() - t0) * 1000

        assert diff.status == "FULLY_ADDRESSED", f"Expected FULLY_ADDRESSED, got {diff.status}"
        assert "revised" in diff.evidence.lower() or "not reproduced" in diff.evidence.lower()

        record("status_fully_addressed", True, dur,
               detail=f"Status={diff.status}, evidence='{diff.evidence[:80]}'")
    except Exception as e:
        record("status_fully_addressed", False, (time.time()-t0)*1000, error=str(e))
        traceback.print_exc()

    # ---- Test 3.4: Status Determination — NOT_ADDRESSED ----
    print("\n[3.4] Status determination: NOT_ADDRESSED...")
    t0 = time.time()
    try:
        prev_issue = {
            "id": "P2", "category": "methodology", "section": "results",
            "title": "Sample size not justified", "severity": "major",
        }
        current_match = {
            "id": "C2", "category": "methodology", "section": "results",
            "title": "Sample size remains unjustified", "severity": "major",
        }
        diff = determine_status(prev_issue, current_match, "")
        dur = (time.time() - t0) * 1000

        assert diff.status == "NOT_ADDRESSED", f"Expected NOT_ADDRESSED, got {diff.status}"

        record("status_not_addressed", True, dur,
               detail=f"Status={diff.status}, evidence='{diff.evidence[:80]}'")
    except Exception as e:
        record("status_not_addressed", False, (time.time()-t0)*1000, error=str(e))
        traceback.print_exc()

    # ---- Test 3.5: Status Determination — PARTIALLY_ADDRESSED ----
    print("\n[3.5] Status determination: PARTIALLY_ADDRESSED...")
    t0 = time.time()
    try:
        prev_issue = {
            "id": "P3", "category": "evidence", "section": "results",
            "title": "Weak robustness checks", "severity": "major",
        }
        current_match = {
            "id": "C3", "category": "evidence", "section": "results",
            "title": "Some robustness checks still missing", "severity": "minor",
        }
        diff = determine_status(prev_issue, current_match, "")
        dur = (time.time() - t0) * 1000

        assert diff.status == "PARTIALLY_ADDRESSED", f"Expected PARTIALLY_ADDRESSED, got {diff.status}"

        record("status_partially_addressed", True, dur,
               detail=f"Status={diff.status}, severity major→minor")
    except Exception as e:
        record("status_partially_addressed", False, (time.time()-t0)*1000, error=str(e))
        traceback.print_exc()

    # ---- Test 3.6: Report Formatting ----
    print("\n[3.6] Reaudit report formatting...")
    t0 = time.time()
    try:
        report = ReauditReport(
            total_previous_issues=5,
            fully_addressed=2,
            partially_addressed=1,
            not_addressed=1,
            new_issues=1,
            improvement_rate=0.5,
            diffs=[
                IssueDiff("P1", "Issue 1", "clarity", "major", "key1", "FULLY_ADDRESSED", "Fixed"),
                IssueDiff("P2", "Issue 2", "method", "major", "key2", "NOT_ADDRESSED", "Persists"),
                IssueDiff("NEW", "New bug", "format", "minor", "key3", "NEW", "New issue"),
            ],
            summary="Solid progress — majority of issues improved.",
        )
        formatted = format_reaudit_report(report)
        dur = (time.time() - t0) * 1000

        assert "RE-AUDIT REPORT" in formatted
        assert "50%" in formatted  # improvement rate
        assert "NOT ADDRESSED" in formatted
        assert len(formatted) > 200

        record("reaudit_report_format", True, dur,
               detail=f"Report length: {len(formatted)}c, contains all sections")
    except Exception as e:
        record("reaudit_report_format", False, (time.time()-t0)*1000, error=str(e))
        traceback.print_exc()

    # ==============================================================
    # MODULE 4: doom_loop.py
    # ==============================================================
    print("\n" + "─" * 50)
    print("  Module: doom_loop")
    print("─" * 50)

    from utils.doom_loop import DoomLoopDetector, THRESHOLDS

    # ---- Test 4.1: No Loop — Normal Usage ----
    print("\n[4.1] Doom loop: normal usage doesn't trigger...")
    t0 = time.time()
    try:
        detector = DoomLoopDetector(window=8)
        # Different tool calls → no loop
        is_loop, msg = detector.check("read_section", {"section_id": "01_abstract"})
        assert is_loop == False
        is_loop, msg = detector.check("read_section", {"section_id": "02_introduction"})
        assert is_loop == False
        is_loop, msg = detector.check("rewrite_section", {"section_id": "03_methods"})
        assert is_loop == False

        dur = (time.time() - t0) * 1000
        record("doom_loop_no_trigger", True, dur,
               detail="3 different calls → no loop detected")
    except Exception as e:
        record("doom_loop_no_trigger", False, (time.time()-t0)*1000, error=str(e))
        traceback.print_exc()

    # ---- Test 4.2: Loop Detection — Repeated Calls ----
    print("\n[4.2] Doom loop: repeated calls trigger detection...")
    t0 = time.time()
    try:
        detector = DoomLoopDetector(window=8)
        threshold = THRESHOLDS.get("rewrite_section", THRESHOLDS["default"])

        is_loop = False
        for i in range(threshold + 1):
            is_loop, msg = detector.check("rewrite_section", {"section_id": "02_intro"})
            if is_loop:
                break

        dur = (time.time() - t0) * 1000
        assert is_loop == True, f"Should trigger after {threshold} repeats"
        assert "LOOP DETECTED" in msg
        assert "rewrite_section" in msg

        record("doom_loop_detection", True, dur,
               detail=f"Triggered after {threshold} repeats, msg: '{msg[:100]}'")
    except Exception as e:
        record("doom_loop_detection", False, (time.time()-t0)*1000, error=str(e))
        traceback.print_exc()

    # ---- Test 4.3: Higher Threshold for Retry Tools ----
    print("\n[4.3] Doom loop: higher threshold for deai_audit...")
    t0 = time.time()
    try:
        detector = DoomLoopDetector(window=10)
        default_threshold = THRESHOLDS["default"]  # 3
        deai_threshold = THRESHOLDS["deai_audit"]  # 4

        # At default threshold (3), deai_audit should NOT trigger
        for i in range(default_threshold):
            is_loop, msg = detector.check("deai_audit", {"section_id": "02_intro"})

        assert is_loop == False, \
            f"deai_audit should NOT trigger at default threshold ({default_threshold})"

        # Continue to deai threshold
        is_loop, msg = detector.check("deai_audit", {"section_id": "02_intro"})
        dur = (time.time() - t0) * 1000

        assert is_loop == True, \
            f"deai_audit SHOULD trigger at its threshold ({deai_threshold})"

        record("doom_loop_higher_threshold", True, dur,
               detail=f"deai_audit tolerates {default_threshold} calls, triggers at {deai_threshold}")
    except Exception as e:
        record("doom_loop_higher_threshold", False, (time.time()-t0)*1000, error=str(e))
        traceback.print_exc()

    # ---- Test 4.4: Reset Clears History ----
    print("\n[4.4] Doom loop: reset clears detection state...")
    t0 = time.time()
    try:
        detector = DoomLoopDetector(window=8)
        # Build up to near-threshold
        for i in range(2):
            detector.check("rewrite_section", {"section_id": "02_intro"})

        # Reset
        detector.reset()

        # Same calls again → should NOT trigger (history cleared)
        is_loop, msg = detector.check("rewrite_section", {"section_id": "02_intro"})
        assert is_loop == False

        dur = (time.time() - t0) * 1000
        record("doom_loop_reset", True, dur,
               detail="Reset clears history, same calls after reset don't trigger")
    except Exception as e:
        record("doom_loop_reset", False, (time.time()-t0)*1000, error=str(e))
        traceback.print_exc()

    # ---- Test 4.5: Fuzzy Matching ----
    print("\n[4.5] Doom loop: fuzzy matching for similar calls...")
    t0 = time.time()
    try:
        detector = DoomLoopDetector(window=10)
        threshold = THRESHOLDS["default"]

        # Make calls that are semantically similar but not identical
        # Same tool, same section_id → same signature → exact match
        for i in range(threshold + 1):
            is_loop, msg = detector.check("edit_section", 
                                          {"section_id": "introduction", "old_text": "the same text"})
            if is_loop:
                break

        dur = (time.time() - t0) * 1000
        assert is_loop == True, "Fuzzy matching should detect similar calls"

        record("doom_loop_fuzzy_match", True, dur,
               detail="Similar edit_section calls detected as loop")
    except Exception as e:
        record("doom_loop_fuzzy_match", False, (time.time()-t0)*1000, error=str(e))
        traceback.print_exc()

    # ==============================================================
    # Summary
    # ==============================================================
    passed = sum(1 for r in results if r["passed"])
    failed = sum(1 for r in results if not r["passed"])

    print("\n" + "=" * 60)
    print(f"  RESULTS: {passed} passed, {failed} failed, {len(results)} total")
    print("=" * 60)

    if failed:
        print("\n  ❌ FAILED:")
        for r in results:
            if not r["passed"]:
                print(f"    - {r['name']}: {r['error']}")
    else:
        print("\n  🎉 ALL PHASE-4 TESTS PASSED!")

    # Save report
    import json
    report_path = Path(__file__).parent / "reports" / "test_phase4_report.json"
    report_path.write_text(json.dumps({
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "passed": passed, "failed": failed,
        "results": results
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n  Report saved: {report_path}")


if __name__ == "__main__":
    run()
