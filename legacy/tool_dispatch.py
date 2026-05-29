"""
core/tool_dispatch.py — Tool dispatch map connecting tool names to handler functions.

Maps each tool name to a lambda that calls the appropriate handler from handlers/*.
"""

from handlers.paper_ops import (
    handle_parse_paper, handle_read_section_index, handle_read_section,
    handle_edit_section, handle_diff_section, handle_read_revision_log,
    handle_consistency_check, handle_read_issues,
)
from handlers.review_ops import (
    handle_review_paper, handle_route_issues, handle_generate_fix_proposal,
    handle_approve_fix, handle_revision_progress, handle_presubmission_check,
    handle_architecture_diagnosis, handle_reaudit, handle_save_previous_issues,
    handle_generate_focus_points, handle_run_single_reviewer,
    handle_consolidate_reviews, handle_session_status,
)
from handlers.write_ops import (
    handle_rewrite_section, handle_generate_rewrite, handle_commit_rewrite,
    handle_verify_rewrite_quality, handle_parallel_rewrite,
    handle_build_voice_profile, handle_show_author_profile,
)
from handlers.deai_ops import (
    handle_deai_audit, handle_deai_closed_loop,
    handle_deai_detect, handle_deai_diagnose, handle_deai_rewrite, handle_deai_verify,
)
from handlers.search_ops import (
    handle_verify_citations, handle_check_citation_content,
    handle_check_citation_alignment, handle_verify_and_enrich_citations,
    handle_search_literature, handle_verify_doi, handle_analyze_figures,
    handle_stata_verify, handle_latex_verify, handle_bib_verify,
    handle_search_local_bibliography, handle_find_uncited_relevant,
)
from handlers.meta_ops import (
    handle_read_agent_guidelines, handle_set_goal, handle_complete_goal,
    handle_save_plan, handle_load_plan, handle_advance_plan,
    handle_self_critique, handle_record_lesson, handle_observe_edit,
    handle_ask_user, handle_load_skill, handle_dry_run_estimate,
    handle_estimate_single_operation, handle_list_checkpoints,
)


TOOL_HANDLERS = {
    "read_agent_guidelines": lambda **kw: handle_read_agent_guidelines(kw["topic"]),
    "parse_paper": lambda **kw: handle_parse_paper(kw["paper_path"]),
    "read_section_index": lambda **kw: handle_read_section_index(),
    "read_section": lambda **kw: handle_read_section(kw["section_id"]),
    "review_paper": lambda **kw: handle_review_paper(
        reviewer_count=kw.get("reviewer_count"),
        focus_dimensions=kw.get("focus_dimensions"),
        custom_criteria=kw.get("custom_criteria"),
    ),
    "rewrite_section": lambda **kw: handle_rewrite_section(
        kw["section_id"], kw.get("custom_instructions", "")),
    "generate_rewrite": lambda **kw: handle_generate_rewrite(
        kw["section_id"], kw.get("custom_instructions", ""),
        kw.get("provider"), kw.get("model")),
    "commit_rewrite": lambda **kw: handle_commit_rewrite(
        kw["section_id"], kw["proposed_text"], kw.get("changes_summary", "")),
    "verify_rewrite_quality": lambda **kw: handle_verify_rewrite_quality(
        kw["section_id"], kw.get("provider"), kw.get("model")),
    "edit_section": lambda **kw: handle_edit_section(
        kw["section_id"], kw["old_text"], kw["new_text"], kw["reason"]),
    "diff_section": lambda **kw: handle_diff_section(kw["section_id"]),
    "read_revision_log": lambda **kw: handle_read_revision_log(kw.get("section_id")),
    "consistency_check": lambda **kw: handle_consistency_check(),
    "read_issues": lambda **kw: handle_read_issues(),
    "ask_user": lambda **kw: handle_ask_user(kw["message"], kw.get("options")),
    "load_skill": lambda **kw: handle_load_skill(kw["skill_name"]),
    "route_issues": lambda **kw: handle_route_issues(kw.get("budget")),
    "generate_fix_proposal": lambda **kw: handle_generate_fix_proposal(
        kw["issue_id"], kw.get("section_id")),
    "approve_fix": lambda **kw: handle_approve_fix(kw["issue_id"]),
    "revision_progress": lambda **kw: handle_revision_progress(),
    "stata_verify": lambda **kw: handle_stata_verify(kw["issue_id"]),
    "deai_audit": lambda **kw: handle_deai_audit(
        kw["section_id"], kw.get("scene")),
    "parallel_rewrite": lambda **kw: handle_parallel_rewrite(
        kw["section_ids"], kw.get("custom_instructions", "")),
    "build_voice_profile": lambda **kw: handle_build_voice_profile(),
    "show_author_profile": lambda **kw: handle_show_author_profile(),
    "verify_citations": lambda **kw: handle_verify_citations(kw.get("max_citations")),
    "check_citation_content": lambda **kw: handle_check_citation_content(),
    "analyze_figures": lambda **kw: handle_analyze_figures(kw.get("figure_ids")),
    "presubmission_check": lambda **kw: handle_presubmission_check(),
    "check_citation_alignment": lambda **kw: handle_check_citation_alignment(),
    "verify_and_enrich_citations": lambda **kw: handle_verify_and_enrich_citations(
        kw.get("bibliography")),
    "reaudit": lambda **kw: handle_reaudit(kw.get("previous_issues_path")),
    "save_previous_issues": lambda **kw: handle_save_previous_issues(),
    "architecture_diagnosis": lambda **kw: handle_architecture_diagnosis(),
    "search_literature": lambda **kw: handle_search_literature(
        kw["query"], kw.get("limit", 5)),
    "verify_doi": lambda **kw: handle_verify_doi(kw["doi"]),
    "deai_closed_loop": lambda **kw: handle_deai_closed_loop(
        kw["section_id"], kw.get("scene")),
    # De-AI Pipeline (Agent-orchestrated individual steps)
    "deai_detect": lambda **kw: handle_deai_detect(
        kw["text"], kw.get("scene", "S1")),
    "deai_diagnose": lambda **kw: handle_deai_diagnose(
        kw["text"], kw["signals"], kw.get("scene", "S1")),
    "deai_rewrite": lambda **kw: handle_deai_rewrite(
        kw["text"], kw["fix_strategy"], kw.get("scene", "S1"),
        kw.get("author_constraints", "")),
    "deai_verify": lambda **kw: handle_deai_verify(
        kw["original_text"], kw["revised_text"], kw.get("scene", "S1")),
    # Dry Run / Cost Estimation
    "dry_run_estimate": lambda **kw: handle_dry_run_estimate(kw["operations"]),
    "estimate_single_operation": lambda **kw: handle_estimate_single_operation(
        kw["operation"], kw.get("text_length_words", 0),
        kw.get("section_count", 1), kw.get("reviewer_count", 5)),
    # Checkpoint / Resume
    "list_checkpoints": lambda **kw: handle_list_checkpoints(),
    # Dynamic Focus Point Generation
    "generate_focus_points": lambda **kw: handle_generate_focus_points(
        kw.get("paper_metadata"), kw.get("section_summaries"), kw.get("detected_methods")),
    # Agent Orchestration
    "run_single_reviewer": lambda **kw: handle_run_single_reviewer(
        kw["reviewer_role"], kw.get("focus_dimensions"), kw.get("custom_criteria")),
    "consolidate_reviews": lambda **kw: handle_consolidate_reviews(
        kw.get("calibrate_scores", True)),
    # Session Status
    "session_status": lambda **kw: handle_session_status(),
    # Goal & Plan Management (Wave 2)
    "set_goal": lambda **kw: handle_set_goal(kw["description"]),
    "complete_goal": lambda **kw: handle_complete_goal(kw["goal_id"], kw.get("note", "")),
    "save_plan": lambda **kw: handle_save_plan(kw["goal"], kw["plan_text"]),
    "load_plan": lambda **kw: handle_load_plan(kw.get("plan_id")),
    "advance_plan": lambda **kw: handle_advance_plan(
        kw["plan_id"], kw["step_index"], kw.get("result_summary", ""), kw.get("success", True)),
    "self_critique": lambda **kw: handle_self_critique(),
    # Wave 4: Learning
    "record_lesson": lambda **kw: handle_record_lesson(kw["lesson"], kw["category"]),
    "observe_edit": lambda **kw: handle_observe_edit(kw["original"], kw["edited"]),
    # Local Bibliography Search (C-3)
    "search_local_bibliography": lambda **kw: handle_search_local_bibliography(
        kw["query"], kw.get("bib_path"), kw.get("limit", 10)),
    "find_uncited_relevant": lambda **kw: handle_find_uncited_relevant(
        kw["cited_keys"], kw["topic"], kw.get("bib_path"), kw.get("limit", 5)),
    # LaTeX / Bibliography Verification (C-2)
    "latex_verify": lambda **kw: handle_latex_verify(
        kw.get("tex_path"), kw.get("project_dir"), kw.get("draft_mode", True)),
    "bib_verify": lambda **kw: handle_bib_verify(
        kw.get("bib_path"), kw.get("tex_path"), kw.get("project_dir"),
        kw.get("check_orphaned", True)),
}
