#!/usr/bin/env python3
"""
Tool Coverage Test — Verifies that all 56 registered tools can be called without crash.

Strategy:
- Direct handler invocation (no LLM, no cost)
- Tests each tool with minimal valid arguments
- Paper must be pre-parsed into .workspace/paper/ (use the existing fixture)
- Groups tools into categories for clear reporting
- Reports: PASS / FAIL / SKIP (skip = tool requires external service)

Usage:
    python test_tool_coverage.py              # Run all tests
    python test_tool_coverage.py --group paper_ops   # Run specific group
    python test_tool_coverage.py --verbose    # Show outputs
"""

from __future__ import annotations

import os
import sys
import json
import time
import asyncio
import traceback
import argparse
from pathlib import Path
from typing import Dict, List, Tuple, Any
from dataclasses import dataclass, field
from datetime import datetime

# Load .env
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

sys.path.insert(0, str(Path(__file__).parent.parent))

# Initialize core state before importing handlers
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
from tools.revision_state import init_state


def setup_state():
    """Initialize all state required by handlers."""
    state.session_budget = "full"
    state.session_provider = "openai"
    state.session_model = "LongCat-Flash-Chat"

    init_state(budget="full")

    state.WORKSPACE.mkdir(parents=True, exist_ok=True)
    state.goal_tracker = GoalTracker(workspace=state.WORKSPACE)
    state.plan_store = PlanStore(workspace=state.WORKSPACE)
    state.reflection_engine = ReflectionEngine(tracker=state.goal_tracker)
    state.adaptive_engine = AdaptiveEngine(workspace=state.WORKSPACE)
    state.context_manager = ProactiveContextManager(max_tokens=128000)
    state.error_recovery = ErrorRecoveryEngine()
    state.output_quality = OutputQualityGate()
    state.session_memory = SessionMemory(workspace=state.WORKSPACE)
    state.meta_planner = MetaPlanner(memory=state.session_memory)
    state.session_memory.start_session(
        goal="Tool coverage test",
        paper_title="NIDZ Policy Test Paper",
    )


# ==============================================================
# Test Case Definitions
# ==============================================================

@dataclass
class ToolTest:
    """A single tool test case."""
    name: str
    group: str
    args: Dict[str, Any]
    skip_reason: str = ""  # If non-empty, test is skipped
    requires_llm: bool = False  # If True, requires actual LLM call
    expects_error_prefix: bool = False  # Some tools legitimately return "Error:..."


# Tool test definitions grouped by category
TOOL_TESTS: List[ToolTest] = [
    # -- Paper Operations --
    ToolTest("read_section_index", "paper_ops", {}),
    ToolTest("read_section", "paper_ops", {"section_id": "02_abstract"}),
    ToolTest("read_section", "paper_ops", {"section_id": "03_1_introduction"}),
    ToolTest("diff_section", "paper_ops", {"section_id": "02_abstract"}),
    ToolTest("read_revision_log", "paper_ops", {"section_id": "02_abstract"}),
    ToolTest("consistency_check", "paper_ops", {}),
    ToolTest("read_issues", "paper_ops", {}),

    # -- Review Operations (LLM-dependent) --
    ToolTest("presubmission_check", "review_ops", {}),
    ToolTest("architecture_diagnosis", "review_ops", {}, requires_llm=True),
    ToolTest("generate_focus_points", "review_ops", {
        "paper_metadata": {"title": "NIDZ Policy", "field": "economics", "word_count": 8000},
        "section_summaries": {
            "abstract": "This paper evaluates NIDZ policy using staggered DID.",
            "methodology": "We employ robust standard errors and staggered DID.",
            "results": "Results are statistically significant.",
        },
        "detected_methods": ["Staggered DID"],
    }, requires_llm=True),
    ToolTest("review_paper", "review_ops", {
        "reviewer_count": 3,
        "focus_dimensions": None,
        "custom_criteria": None,
    }, requires_llm=True),
    ToolTest("run_single_reviewer", "review_ops", {
        "reviewer_role": "methodology",
        "focus_dimensions": ["causal_identification"],
        "custom_criteria": None,
    }, requires_llm=True),
    ToolTest("consolidate_reviews", "review_ops", {"calibrate_scores": True}, requires_llm=True),
    ToolTest("route_issues", "review_ops", {"budget": "full"}),
    ToolTest("generate_fix_proposal", "review_ops", {
        "issue_id": "ISS-001",
        "section_id": "02_abstract",
    }, requires_llm=True),
    ToolTest("approve_fix", "review_ops", {"issue_id": "ISS-001"}, expects_error_prefix=True),
    ToolTest("revision_progress", "review_ops", {}),
    ToolTest("save_previous_issues", "review_ops", {}),
    ToolTest("reaudit", "review_ops", {"previous_issues_path": None}, requires_llm=True),
    ToolTest("session_status", "review_ops", {}),

    # -- Write Operations (LLM-dependent) --
    ToolTest("build_voice_profile", "write_ops", {}, requires_llm=True),
    ToolTest("show_author_profile", "write_ops", {}),
    ToolTest("generate_rewrite", "write_ops", {
        "section_id": "02_abstract",
        "custom_instructions": "Make it more concise",
        "provider": None,
        "model": None,
    }, requires_llm=True),
    ToolTest("verify_rewrite_quality", "write_ops", {
        "section_id": "02_abstract",
        "provider": None,
        "model": None,
    }, requires_llm=True),
    ToolTest("edit_section", "write_ops", {
        "section_id": "02_abstract",
        "old_text": "PLACEHOLDER_OLD",  # Will be filled dynamically
        "new_text": "PLACEHOLDER_NEW",
        "reason": "Test edit",
    }),
    ToolTest("commit_rewrite", "write_ops", {
        "section_id": "02_abstract",
        "proposed_text": "This is a test proposed rewrite text for the abstract section.",
        "changes_summary": "Test commit",
    }),
    ToolTest("rewrite_section", "write_ops", {
        "section_id": "02_abstract",
        "custom_instructions": "Test rewrite",
    }, requires_llm=True),
    ToolTest("parallel_rewrite", "write_ops", {
        "section_ids": ["02_abstract"],
        "custom_instructions": "Test",
    }, requires_llm=True),

    # -- DeAI Operations (LLM-dependent) --
    ToolTest("deai_detect", "deai_ops", {
        "text": "This groundbreaking study leverages cutting-edge methodologies to demonstrate compelling evidence.",
        "scene": "S1",
    }, requires_llm=True),
    ToolTest("deai_diagnose", "deai_ops", {
        "text": "This study leverages methodologies to demonstrate evidence.",
        "signals": [
            {"sentence": "This study leverages methodologies", "signal_type": "hedge_word",
             "confidence": 0.8, "fix_suggestion": "Be more specific", "location_hint": "sentence 1"},
        ],
        "scene": "S1",
    }),
    ToolTest("deai_rewrite", "deai_ops", {
        "text": "This groundbreaking study leverages cutting-edge methodologies.",
        "fix_strategy": ["reduce_hedging", "simplify_vocabulary"],
        "scene": "S1",
        "author_constraints": "",
    }),
    ToolTest("deai_verify", "deai_ops", {
        "original_text": "This study leverages methodologies.",
        "revised_text": "We use regression methods.",
        "scene": "S1",
    }, requires_llm=True),
    ToolTest("deai_audit", "deai_ops", {
        "section_id": "02_abstract",
        "scene": "S1",
    }, requires_llm=True),
    ToolTest("deai_closed_loop", "deai_ops", {
        "section_id": "02_abstract",
        "scene": "S1",
    }, requires_llm=True),

    # -- Search Operations --
    ToolTest("verify_citations", "search_ops", {"max_citations": 3}, requires_llm=True),
    ToolTest("check_citation_content", "search_ops", {}, requires_llm=True),
    ToolTest("check_citation_alignment", "search_ops", {}, requires_llm=True),
    ToolTest("verify_and_enrich_citations", "search_ops", {"bibliography": None}, requires_llm=True),
    ToolTest("search_literature", "search_ops", {"query": "staggered DID policy evaluation", "limit": 3},
             skip_reason="Requires external search API"),
    ToolTest("verify_doi", "search_ops", {"doi": "10.1257/aer.20130612"},
             skip_reason="Requires external DOI resolution API"),
    ToolTest("analyze_figures", "search_ops", {"figure_ids": None}),
    ToolTest("stata_verify", "search_ops", {"issue_id": "ISS-001"},
             skip_reason="Requires Stata installation"),

    # -- Meta Operations --
    ToolTest("read_agent_guidelines", "meta_ops", {"topic": "planning"}),
    ToolTest("read_agent_guidelines", "meta_ops", {"topic": "tool_selection"}),
    ToolTest("set_goal", "meta_ops", {"description": "Test coverage verification"}),
    ToolTest("complete_goal", "meta_ops", {"goal_id": "G-1", "note": "Test complete"}),
    ToolTest("save_plan", "meta_ops", {
        "goal": "Test plan",
        "plan_text": "1. First step\n2. Second step\n3. Final step",
    }),
    ToolTest("load_plan", "meta_ops", {"plan_id": None}),
    ToolTest("advance_plan", "meta_ops", {
        "plan_id": "PLACEHOLDER",  # Will be filled dynamically
        "step_index": 0,
        "result_summary": "Step passed",
        "success": True,
    }),
    ToolTest("self_critique", "meta_ops", {}),
    ToolTest("record_lesson", "meta_ops", {"lesson": "Test lesson", "category": "pitfall"}),
    ToolTest("observe_edit", "meta_ops", {"original": "We argue that", "edited": "This paper argues that"}),
    ToolTest("ask_user", "meta_ops", {"message": "Test question?", "options": ["Yes", "No"]}),
    ToolTest("load_skill", "meta_ops", {"skill_name": "review_criteria"}),

    # -- Dry Run / Cost Estimation --
    ToolTest("dry_run_estimate", "cost_ops", {
        "operations": [
            {"operation": "review_paper", "reviewer_count": 5},
            {"operation": "rewrite_section", "text_length_words": 500},
            {"operation": "deai_audit", "text_length_words": 500},
        ],
    }),
    ToolTest("estimate_single_operation", "cost_ops", {
        "operation": "review_paper",
        "text_length_words": 5000,
        "section_count": 6,
        "reviewer_count": 5,
    }),

    # -- Checkpoint --
    ToolTest("list_checkpoints", "checkpoint_ops", {}),
]


# ==============================================================
# Test Runner
# ==============================================================

@dataclass
class TestResult:
    """Result of a single tool test."""
    name: str
    group: str
    status: str  # "PASS", "FAIL", "SKIP", "ERROR"
    duration_ms: float
    output_preview: str = ""
    error: str = ""
    skipped_reason: str = ""


def run_single_test(test: ToolTest, handlers: dict, verbose: bool = False,
                    skip_llm: bool = False, event_loop=None) -> TestResult:
    """Run a single tool test case."""
    
    # Skip check
    if test.skip_reason:
        return TestResult(
            name=test.name, group=test.group, status="SKIP",
            duration_ms=0, skipped_reason=test.skip_reason,
        )
    if skip_llm and test.requires_llm:
        return TestResult(
            name=test.name, group=test.group, status="SKIP",
            duration_ms=0, skipped_reason="Requires LLM (--skip-llm mode)",
        )

    handler = handlers.get(test.name)
    if not handler:
        return TestResult(
            name=test.name, group=test.group, status="ERROR",
            duration_ms=0, error=f"Handler not found for '{test.name}'",
        )

    # Dynamic argument fixups
    args = dict(test.args)
    if test.name == "edit_section":
        # Need real text from the section — read the RAW file directly to avoid [REVISED] prefix
        try:
            section_id = args.get("section_id", "02_abstract")
            raw_path = state.WORKSPACE / "paper" / f"{section_id}.txt"
            if raw_path.exists():
                section_text = raw_path.read_text(encoding="utf-8").strip()
            else:
                from handlers.paper_ops import handle_read_section
                section_text = handle_read_section(section_id)
            # Get a short snippet that actually exists in the text
            if section_text and not section_text.startswith("Error:"):
                # Skip lines starting with [ (metadata) and empty lines
                lines = [l for l in section_text.split("\n") if l.strip() and not l.strip().startswith("[")]
                if lines:
                    snippet = lines[0][:60]
                    args["old_text"] = snippet
                    args["new_text"] = snippet  # No-op edit (same text = test passes)
                else:
                    args["old_text"] = section_text[:50]
                    args["new_text"] = section_text[:50]
            else:
                args["old_text"] = "test old"
                args["new_text"] = "test new"
        except Exception:
            args["old_text"] = "test old"
            args["new_text"] = "test new"

    if test.name == "advance_plan":
        # Need a real plan_id
        from core.state import plan_store
        if plan_store:
            active = plan_store.get_active_plan()
            if active:
                args["plan_id"] = active.plan_id
            else:
                args["plan_id"] = "nonexistent_plan"
        else:
            args["plan_id"] = "nonexistent_plan"

    # Execute
    t0 = time.time()
    try:
        result = handler(**args)

        # Handle async coroutines — await them in the event loop
        if asyncio.iscoroutine(result):
            if event_loop is None:
                event_loop = asyncio.new_event_loop()
            result = event_loop.run_until_complete(result)

        duration_ms = (time.time() - t0) * 1000

        output_str = str(result) if result is not None else "(None)"
        is_error = isinstance(result, str) and result.startswith("Error:")

        if is_error and not test.expects_error_prefix:
            status = "FAIL"
            error_msg = output_str[:200]
        else:
            status = "PASS"
            error_msg = ""

        if verbose:
            print(f"    Output: {output_str[:300]}")

        return TestResult(
            name=test.name, group=test.group, status=status,
            duration_ms=round(duration_ms, 1),
            output_preview=output_str[:200],
            error=error_msg,
        )

    except Exception as e:
        duration_ms = (time.time() - t0) * 1000
        tb = traceback.format_exc()
        if verbose:
            print(f"    EXCEPTION: {tb[-500:]}")

        return TestResult(
            name=test.name, group=test.group, status="ERROR",
            duration_ms=round(duration_ms, 1),
            error=f"{type(e).__name__}: {str(e)[:150]}",
        )


def run_all_tests(group_filter: str = None, verbose: bool = False,
                  skip_llm: bool = False) -> List[TestResult]:
    """Run all tool tests and return results."""
    from core.tool_dispatch import TOOL_HANDLERS

    tests = TOOL_TESTS
    if group_filter:
        tests = [t for t in tests if t.group == group_filter]

    print(f"\n{'=' * 70}")
    print(f"  TOOL COVERAGE TEST — {len(tests)} test cases")
    if group_filter:
        print(f"  Filter: group={group_filter}")
    if skip_llm:
        print(f"  Mode: skip-llm (only local tools)")
    print(f"{'=' * 70}\n")

    results: List[TestResult] = []
    current_group = ""

    # Create a shared event loop for async handlers
    event_loop = asyncio.new_event_loop()

    for test in tests:
        if test.group != current_group:
            current_group = test.group
            print(f"\n  [{current_group.upper()}]")
            print(f"  {'-' * 50}")

        # Run test
        r = run_single_test(test, TOOL_HANDLERS, verbose=verbose, skip_llm=skip_llm,
                            event_loop=event_loop)
        results.append(r)

        # Status indicator
        icon = {"PASS": "✓", "FAIL": "✗", "SKIP": "○", "ERROR": "!"}[r.status]
        color = {"PASS": "\033[32m", "FAIL": "\033[31m", "SKIP": "\033[33m", "ERROR": "\033[31m"}[r.status]
        reset = "\033[0m"
        duration_str = f"{r.duration_ms:.0f}ms" if r.duration_ms > 0 else ""
        print(f"  {color}{icon}{reset} {test.name:<35} {duration_str:>8}  {r.error[:50] or r.skipped_reason[:50]}")

    # Cleanup the event loop
    event_loop.close()

    return results


def print_summary(results: List[TestResult]):
    """Print final summary."""
    total = len(results)
    passed = sum(1 for r in results if r.status == "PASS")
    failed = sum(1 for r in results if r.status == "FAIL")
    errors = sum(1 for r in results if r.status == "ERROR")
    skipped = sum(1 for r in results if r.status == "SKIP")

    print(f"\n\n{'=' * 70}")
    print(f"  COVERAGE SUMMARY")
    print(f"{'=' * 70}")
    print(f"  Total:   {total}")
    print(f"  Passed:  \033[32m{passed}\033[0m")
    print(f"  Failed:  \033[31m{failed}\033[0m")
    print(f"  Errors:  \033[31m{errors}\033[0m")
    print(f"  Skipped: \033[33m{skipped}\033[0m")
    print(f"  Coverage: {passed}/{total - skipped} = {passed/(total-skipped)*100:.1f}%" if (total - skipped) > 0 else "  Coverage: N/A")

    # Group breakdown
    groups = {}
    for r in results:
        if r.group not in groups:
            groups[r.group] = {"pass": 0, "fail": 0, "error": 0, "skip": 0}
        groups[r.group][r.status.lower()] = groups[r.group].get(r.status.lower(), 0) + 1

    print(f"\n  {'Group':<20} {'Pass':<6} {'Fail':<6} {'Error':<6} {'Skip':<6}")
    print(f"  {'-' * 50}")
    for group, counts in sorted(groups.items()):
        print(f"  {group:<20} {counts.get('pass',0):<6} {counts.get('fail',0):<6} "
              f"{counts.get('error',0):<6} {counts.get('skip',0):<6}")

    # Failed details
    failures = [r for r in results if r.status in ("FAIL", "ERROR")]
    if failures:
        print(f"\n\n  FAILURES & ERRORS:")
        print(f"  {'-' * 50}")
        for r in failures:
            print(f"  [{r.status}] {r.name}: {r.error}")

    # All registered tools vs tested tools
    from core.tool_dispatch import TOOL_HANDLERS
    all_tools = set(TOOL_HANDLERS.keys())
    tested_tools = set(t.name for t in TOOL_TESTS)
    untested = all_tools - tested_tools
    if untested:
        print(f"\n\n  UNTESTED TOOLS (not covered by test cases):")
        for t in sorted(untested):
            print(f"    - {t}")

    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "skipped": skipped,
        "coverage_pct": round(passed / (total - skipped) * 100, 1) if (total - skipped) > 0 else 0,
        "untested_tools": sorted(untested),
    }


def main():
    parser = argparse.ArgumentParser(description="ScholarAgent Tool Coverage Test")
    parser.add_argument("--group", type=str, default=None,
                        help="Filter by group (paper_ops, review_ops, write_ops, deai_ops, search_ops, meta_ops, cost_ops, checkpoint_ops)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Show tool outputs")
    parser.add_argument("--skip-llm", action="store_true",
                        help="Skip tools that require LLM calls (local-only test)")
    parser.add_argument("--output", type=str, default=None,
                        help="Output JSON report path")
    args = parser.parse_args()

    # Setup state
    setup_state()

    # First, ensure set_goal is called so goal_tracker is populated
    from core.tool_dispatch import TOOL_HANDLERS
    TOOL_HANDLERS["set_goal"](description="Tool coverage test run")

    # Run tests
    t0 = time.time()
    results = run_all_tests(
        group_filter=args.group,
        verbose=args.verbose,
        skip_llm=args.skip_llm,
    )
    total_time = time.time() - t0

    # Print summary
    summary = print_summary(results)
    summary["total_time_seconds"] = round(total_time, 1)
    summary["timestamp"] = datetime.now().isoformat()

    print(f"\n  Total test time: {total_time:.1f}s")
    print(f"{'=' * 70}")

    # Save report
    if args.output:
        report = {
            "summary": summary,
            "results": [
                {
                    "name": r.name,
                    "group": r.group,
                    "status": r.status,
                    "duration_ms": r.duration_ms,
                    "output_preview": r.output_preview,
                    "error": r.error,
                    "skipped_reason": r.skipped_reason,
                }
                for r in results
            ],
        }
        output_path = Path(args.output)
        output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\n  Report saved to: {output_path}")


if __name__ == "__main__":
    main()
