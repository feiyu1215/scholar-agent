"""handlers/review_ops.py — Review, routing, fix proposals, and iterative review handlers."""

import json
import os
from pathlib import Path

from core.state import WORKSPACE


async def handle_review_paper(
    provider: str = None,
    model: str = None,
    reviewer_count: int = None,
    focus_dimensions: list = None,
    custom_criteria: str = None,
    calibrate_scores=True,
) -> str:
    from tools.review_engine import review_paper
    return await review_paper(
        provider=provider,
        model=model,
        reviewer_count=reviewer_count,
        focus_dimensions=focus_dimensions,
        custom_criteria=custom_criteria,
        calibrate_scores=calibrate_scores,
    )


def handle_route_issues(budget: str = None) -> str:
    from tools.action_router import route_issues, format_routing_report
    from tools.revision_state import load_state, register_issues, get_seen_categories
    from core.state import session_budget

    effective_budget = budget or session_budget

    issues_path = WORKSPACE / "review" / "consolidated.json"
    if not issues_path.exists():
        return "Error: No consolidated review found. Run review_paper first."

    consolidated = json.loads(issues_path.read_text(encoding="utf-8"))
    issues = consolidated.get("issues", [])
    if not issues:
        return "No issues found in consolidated review."

    state = load_state()
    seen = get_seen_categories(state)
    routed, stats = route_issues(issues, budget=effective_budget, seen_categories=seen)

    routed_dicts = [r.to_dict() for r in routed]
    register_issues(state, routed_dicts)

    routed_path = WORKSPACE / "review" / "routed_issues.json"
    routed_path.write_text(
        json.dumps(routed_dicts, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    return format_routing_report(routed, stats)


async def handle_generate_fix_proposal(issue_id: str, section_id: str = None,
                                       provider: str = None, model: str = None) -> str:
    from tools.write_engine import generate_fix_proposal, format_proposal_for_user

    routed_path = WORKSPACE / "review" / "routed_issues.json"
    if not routed_path.exists():
        return "Error: No routed issues. Run route_issues first."

    routed = json.loads(routed_path.read_text(encoding="utf-8"))
    issue = next((i for i in routed if i.get("id") == issue_id), None)
    if not issue:
        return "Error: Issue '" + issue_id + "' not found in routed issues."

    proposal = await generate_fix_proposal(
        issue, section_id=section_id, provider=provider, model=model
    )
    return format_proposal_for_user(proposal)


async def handle_approve_fix(issue_id: str, provider: str = None, model: str = None) -> str:
    from tools.revision_state import (
        load_state, update_issue_status, mark_category_confirmed
    )
    from tools.write_engine import rewrite_section

    proposal_path = WORKSPACE / "proposals" / (issue_id + ".json")
    if not proposal_path.exists():
        return "Error: No proposal found for " + issue_id + ". Run generate_fix_proposal first."

    proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
    section_id = proposal.get("section_id", "")

    custom = (
        "Apply this APPROVED fix for " + issue_id + ":\n"
        "Current: " + proposal.get("current_text", "") + "\n"
        "Change to: " + proposal.get("proposed_text", "") + "\n"
        "Rationale: " + proposal.get("rationale", "")
    )
    result = await rewrite_section(
        section_id, provider=provider, model=model, custom_instructions=custom
    )

    state = load_state()
    update_issue_status(state, issue_id, "done", note="approved by user")

    routed_path = WORKSPACE / "review" / "routed_issues.json"
    if routed_path.exists():
        routed = json.loads(routed_path.read_text(encoding="utf-8"))
        issue = next((i for i in routed if i.get("id") == issue_id), None)
        if issue:
            mark_category_confirmed(state, issue.get("category", ""))

    return "Fix approved and applied for " + issue_id + ".\n" + result


def handle_revision_progress() -> str:
    from tools.revision_state import load_state, format_progress
    state = load_state()
    return format_progress(state)


def handle_presubmission_check() -> str:
    from tools.presubmission_check import run_presubmission_checks, format_presubmission_report
    from handlers.search_ops import _load_full_paper_text
    paper_text = _load_full_paper_text()
    if not paper_text:
        return "Error: No paper parsed. Run parse_paper first."
    report = run_presubmission_checks(paper_text)
    return format_presubmission_report(report)


def handle_architecture_diagnosis() -> str:
    from tools.architecture_diagnosis import run_architecture_diagnosis, format_architecture_report
    from handlers.search_ops import _load_full_paper_text
    paper_text = _load_full_paper_text()
    report = run_architecture_diagnosis(paper_text=paper_text)
    return format_architecture_report(report)


def handle_reaudit(previous_issues_path: str = None) -> str:
    from tools.reaudit import run_reaudit, format_reaudit_report
    from handlers.search_ops import _load_full_paper_text
    paper_text = _load_full_paper_text()
    report = run_reaudit(previous_issues_path=previous_issues_path, current_paper_text=paper_text)
    return format_reaudit_report(report)


def handle_save_previous_issues() -> str:
    from tools.reaudit import save_previous_issues
    return save_previous_issues()


def handle_generate_focus_points(paper_metadata: dict = None,
                                 section_summaries: dict = None,
                                 detected_methods: list = None) -> str:
    """Generate paper-specific focus points for reviewers."""
    from tools.focus_generator import generate_focus_points, format_focus_report
    from handlers.paper_ops import _load_paper_metadata

    # Auto-load metadata from workspace if not provided
    if not paper_metadata:
        paper_metadata = _load_paper_metadata() or {}

    # Auto-extract section summaries from parsed sections if not provided
    if not section_summaries:
        section_summaries = {}
        index_path = WORKSPACE / "paper" / "section_index.json"
        if index_path.exists():
            index = json.loads(index_path.read_text(encoding="utf-8"))
            # Map sections to roles by keyword matching
            role_keywords = {
                "introduction": ["intro"],
                "methodology": ["method", "empiric", "data", "model"],
                "results": ["result", "finding"],
                "discussion": ["discuss", "conclusion"],
                "literature": ["literature", "review"],
            }
            for entry in index:
                slug = entry.get("slug", "").lower()
                sec_path = Path(entry["file"])
                if not sec_path.exists():
                    continue
                text = sec_path.read_text(encoding="utf-8")[:1500]
                for role, keywords in role_keywords.items():
                    if role not in section_summaries and any(kw in slug for kw in keywords):
                        section_summaries[role] = text
                        break

    result = generate_focus_points(paper_metadata, section_summaries, detected_methods)
    return format_focus_report(result)


async def handle_run_single_reviewer(
    reviewer_role: str,
    focus_dimensions: list = None,
    custom_criteria: str = None,
) -> str:
    """Run a single reviewer role and return its issues."""
    from tools.review_engine import _run_reviewer, REVIEWER_ROLES, _load_index
    from llm.client import LLMClient

    index = _load_index()
    if not index:
        return "Error: No paper parsed. Use parse_paper first."

    if reviewer_role not in REVIEWER_ROLES:
        return f"Error: Unknown reviewer role '{reviewer_role}'. Choose from: {list(REVIEWER_ROLES.keys())}"

    role_config = REVIEWER_ROLES[reviewer_role]
    from core.state import session_model
    max_conc = int(os.environ.get("SCHOLAR_MAX_CONCURRENT", "2"))
    client = LLMClient(model=session_model, max_concurrent=max_conc)

    issues = await _run_reviewer(
        client, reviewer_role, role_config, index,
        focus_dimensions=focus_dimensions,
        custom_criteria=custom_criteria,
    )

    # Save output
    review_dir = WORKSPACE / "review"
    review_dir.mkdir(parents=True, exist_ok=True)
    (review_dir / f"reviewer_{reviewer_role}.json").write_text(
        json.dumps(issues, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    return json.dumps({"reviewer": reviewer_role, "issues_count": len(issues), "issues": issues},
                      indent=2, ensure_ascii=False)


async def handle_consolidate_reviews(calibrate_scores: bool = True) -> str:
    """Load saved reviewer outputs and run consolidation + postprocessing."""
    from tools.review_engine import review_paper

    # Delegate to review_paper with all reviewers already saved (it will skip re-running via checkpoint)
    # Simpler: just call review_paper which now has checkpoint support and will resume from saved outputs
    return await review_paper(calibrate_scores=calibrate_scores)


def handle_session_status() -> str:
    """Show current session progress."""
    from utils.checkpoint import list_checkpoints
    from utils.trace import get_trace_summary
    from core.state import (
        goal_tracker, plan_store, context_manager,
        error_recovery, adaptive_engine, session_memory,
    )

    lines = ["=== Session Status ==="]

    # 1. Revision state
    try:
        from tools.revision_state import load_state
        state = load_state()
        if state:
            total_issues = len(state.get("issues", []))
            resolved = sum(1 for i in state.get("issues", []) if i.get("status") == "resolved")
            lines.append(f"Issues: {resolved}/{total_issues} resolved")
            lines.append(f"Budget mode: {state.get('budget', 'unknown')}")
    except Exception:
        lines.append("Revision state: not initialized")

    # 2. Paper info
    try:
        if (WORKSPACE / "paper/metadata.json").exists():
            meta = json.loads((WORKSPACE / "paper/metadata.json").read_text())
            lines.append(f"Paper: {meta.get('title', meta.get('source_file', 'unknown'))}")
        if (WORKSPACE / "paper/section_index.json").exists():
            idx = json.loads((WORKSPACE / "paper/section_index.json").read_text())
            lines.append(f"Sections: {len(idx)} parsed")
    except Exception:
        lines.append("Paper: not parsed")

    # 3. Active checkpoints
    cps = list_checkpoints()
    if cps:
        lines.append(f"Active checkpoints: {len(cps)}")
        for cp in cps:
            lines.append(f"  - {cp['pipeline_name']} [{cp['status']}] step {cp['completed_step']}/{cp['total_steps_estimate']}")

    # 4. Goal tracker (Wave 2)
    if goal_tracker:
        lines.append(f"Phase: {goal_tracker.phase.value}")
        active_goals = [g for g in goal_tracker.goals if g.status == "active"]
        done_goals = [g for g in goal_tracker.goals if g.status == "completed"]
        if goal_tracker.goals:
            lines.append(f"Goals: {len(done_goals)}/{len(goal_tracker.goals)} completed")
            for g in active_goals:
                lines.append(f"  - [{g.id}] {g.description} ({g.status})")

    # 5. Active plan (Wave 2)
    if plan_store:
        plan = plan_store.get_active_plan()
        if plan:
            lines.append(f"Active plan: {plan.progress_summary()}")

    # 6. Wave 3 status
    if context_manager:
        lines.append(context_manager.get_status_string())
    if error_recovery:
        err_summary = error_recovery.get_error_summary()
        if "none" not in err_summary:
            lines.append(err_summary)
    if adaptive_engine and adaptive_engine.get_strategy():
        strategy = adaptive_engine.get_strategy()
        lines.append(f"Strategy: depth={strategy.review_depth}, deai={strategy.deai_enabled}")

    # 7. Wave 4 memory status
    if session_memory:
        lines.append(session_memory.get_memory_summary())

    # 8. Trace summary
    try:
        trace = get_trace_summary()
        if trace:
            lines.append(f"Tool calls this session: {trace.get('total_calls', 0)}")
            lines.append(f"Total tokens: {trace.get('total_tokens', 0)}")
    except Exception:
        pass

    return "\n".join(lines)
