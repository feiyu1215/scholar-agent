"""handlers/paper_ops.py — Paper parsing, section reading, and structural analysis handlers."""

import json
from pathlib import Path

from core.state import WORKSPACE


def _load_paper_metadata() -> dict:
    """Load paper metadata written by paper_parser at parse time.

    Contains discipline, language, and other paper-level info.
    Used by deai handlers for automatic scene routing.
    """
    meta_path = WORKSPACE / "paper" / "metadata.json"
    if not meta_path.exists():
        return None
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def handle_parse_paper(paper_path: str) -> str:
    from tools.paper_parser import parse_paper
    from core.state import adaptive_engine, session_budget
    result = parse_paper(paper_path, str(WORKSPACE))

    # Wave 3: Trigger adaptive strategy computation after paper is parsed
    if adaptive_engine and not result.startswith("Error:"):
        profile = adaptive_engine.analyze_paper()
        adaptive_engine.compute_strategy(profile, budget=session_budget or "full")

    return result


def handle_read_section_index() -> str:
    from tools.section_ops import read_section_index
    return read_section_index()


def handle_read_section(section_id: str) -> str:
    from tools.section_ops import read_section
    return read_section(section_id)


def handle_edit_section(section_id: str, old_text: str, new_text: str, reason: str) -> str:
    from tools.section_ops import edit_section
    return edit_section(section_id, old_text, new_text, reason)


def handle_diff_section(section_id: str) -> str:
    from tools.section_ops import diff_section
    return diff_section(section_id)


def handle_read_revision_log(section_id: str = None) -> str:
    from tools.section_ops import read_revision_log
    return read_revision_log(section_id)


def handle_consistency_check() -> str:
    from tools.section_ops import consistency_check
    return consistency_check()


def handle_read_issues() -> str:
    issues_path = WORKSPACE / "review" / "issues.json"
    if not issues_path.exists():
        return "No review issues found. Run review_paper first."
    return issues_path.read_text(encoding="utf-8")
