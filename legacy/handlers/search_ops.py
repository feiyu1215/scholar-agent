"""handlers/search_ops.py — Citation verification, literature search, figure analysis, and Stata handlers."""

import json
from pathlib import Path

from core.state import WORKSPACE


def _load_full_paper_text() -> str:
    """Load full paper text from parsed sections."""
    index_path = WORKSPACE / "paper" / "section_index.json"
    if not index_path.exists():
        return ""
    index = json.loads(index_path.read_text(encoding="utf-8"))
    parts = []
    for entry in index:
        sec_path = Path(entry["file"])
        if sec_path.exists():
            parts.append(sec_path.read_text(encoding="utf-8"))
    return "\n\n".join(parts)


async def handle_verify_citations(max_citations: int = None) -> str:
    from tools.literature_verify import (
        parse_references, verify_citations_batch,
        extract_inline_citations, check_citation_consistency,
        generate_verification_report,
    )

    paper_text = _load_full_paper_text()
    if not paper_text:
        return "Error: No paper parsed. Run parse_paper first."

    citations = parse_references(paper_text)
    if not citations:
        return "No references found in the paper."

    if max_citations and max_citations < len(citations):
        citations = citations[:max_citations]

    results = await verify_citations_batch(citations, max_concurrency=3)

    inline_cites = extract_inline_citations(paper_text)
    consistency_issues = check_citation_consistency(inline_cites, citations)

    report = generate_verification_report(results)
    if consistency_issues:
        report += "\n\n### Inline Citation Consistency Issues\n\n"
        for issue in consistency_issues:
            report += "- [" + issue["type"] + "] " + issue["detail"] + "\n"

    return report


def handle_check_citation_content() -> str:
    from tools.literature_verify import (
        parse_references, extract_citation_claims,
        check_overclaim_in_citations, generate_content_accuracy_report,
    )

    paper_text = _load_full_paper_text()
    if not paper_text:
        return "Error: No paper parsed. Run parse_paper first."

    citations = parse_references(paper_text)
    if not citations:
        return "No references found."

    claims = extract_citation_claims(paper_text, citations)
    if not claims:
        return "No specific claims about citations found in the paper body."

    results = check_overclaim_in_citations(claims)
    return generate_content_accuracy_report(results)


def handle_check_citation_alignment() -> str:
    from tools.literature_verify import (
        parse_references, extract_citation_claims,
        compute_alignment_scores, generate_alignment_report,
    )
    paper_text = _load_full_paper_text()
    if not paper_text:
        return "Error: No paper parsed. Run parse_paper first."

    citations = parse_references(paper_text)
    if not citations:
        return "No references found in the paper."

    claims = extract_citation_claims(paper_text, citations)
    if not claims:
        return "No citation claims found in the paper body."

    scores = compute_alignment_scores(claims)
    return generate_alignment_report(scores)


def handle_verify_and_enrich_citations(bibliography: list = None) -> str:
    from tools.citation_synergy import verify_and_enrich_citations, format_synergy_report

    paper_text = _load_full_paper_text()
    if not paper_text:
        return "Error: No paper parsed. Run parse_paper first."

    result = verify_and_enrich_citations(paper_text, bibliography=bibliography)
    return format_synergy_report(result)


def handle_search_literature(query: str, limit: int = 5) -> str:
    from tools.web_search import search_literature
    return search_literature(query, limit=limit)


def handle_verify_doi(doi: str) -> str:
    from tools.web_search import verify_doi
    return verify_doi(doi)


async def handle_analyze_figures(figure_ids: list = None) -> str:
    from tools.figure_analyzer import batch_analyze_figures, generate_figure_report
    from core.state import WORKSPACE

    # Discover figure files in workspace
    figures_dir = WORKSPACE / "figures"
    if not figures_dir.exists():
        return "No figures directory found in workspace. Upload figures to .workspace/figures/ first."

    all_figures = sorted(
        p for p in figures_dir.iterdir()
        if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".pdf", ".svg")
    )
    if not all_figures:
        return "No figure files found in .workspace/figures/."

    # Filter by IDs if specified
    if figure_ids:
        selected = [p for p in all_figures if p.stem in figure_ids or p.name in figure_ids]
        if not selected:
            return f"No figures matched IDs {figure_ids}. Available: {[p.name for p in all_figures]}"
    else:
        selected = all_figures

    figure_paths = [str(p) for p in selected]
    results = await batch_analyze_figures(figure_paths=figure_paths)
    return generate_figure_report(results)


async def handle_stata_verify(issue_id: str, provider: str = None, model: str = None) -> str:
    from tools.stata_verify import stata_verify, format_stata_result
    from tools.revision_state import load_state, record_stata_result

    routed_path = WORKSPACE / "review" / "routed_issues.json"
    if not routed_path.exists():
        return "Error: No routed issues. Run route_issues first."

    routed = json.loads(routed_path.read_text(encoding="utf-8"))
    issue = next((i for i in routed if i.get("id") == issue_id), None)
    if not issue:
        return "Error: Issue '" + issue_id + "' not found."

    methods_context = ""
    index_path = WORKSPACE / "paper" / "section_index.json"
    if index_path.exists():
        index = json.loads(index_path.read_text(encoding="utf-8"))
        for entry in index:
            if any(kw in entry.get("slug", "").lower() for kw in ["method", "data", "empiric"]):
                sec_path = Path(entry["file"])
                if sec_path.exists():
                    methods_context += sec_path.read_text(encoding="utf-8")[:2000]
                break

    result = await stata_verify(
        issue, methods_context=methods_context, provider=provider, model=model
    )

    state = load_state()
    record_stata_result(state, issue_id, result)

    return format_stata_result(result)


def handle_search_local_bibliography(query: str, bib_path=None, limit: int = 10) -> str:
    """Search user's local .bib library."""
    from tools.bib_search import search_local_bibliography
    return search_local_bibliography(query, bib_path=bib_path, limit=limit)


def handle_find_uncited_relevant(
    cited_keys: list, topic: str, bib_path=None, limit: int = 5
) -> str:
    """Find relevant but uncited papers in user's library."""
    from tools.bib_search import find_uncited_relevant
    return find_uncited_relevant(cited_keys, topic, bib_path=bib_path, limit=limit)


# ─── LaTeX / Bibliography Verification (C-2) ─────────────────────────────────


def handle_latex_verify(
    tex_path=None, project_dir=None, draft_mode: bool = True
) -> str:
    """Verify LaTeX compilation and report errors/warnings."""
    from tools.latex_verify import latex_verify, format_latex_result
    result = latex_verify(tex_path=tex_path, project_dir=project_dir, draft_mode=draft_mode)
    return format_latex_result(result)


def handle_bib_verify(
    bib_path=None, tex_path=None, project_dir=None, check_orphaned: bool = True
) -> str:
    """Verify bibliography completeness and citation consistency."""
    from tools.bib_verify import bib_verify, format_bib_result
    result = bib_verify(
        bib_path=bib_path, tex_path=tex_path,
        project_dir=project_dir, check_orphaned=check_orphaned,
    )
    return format_bib_result(result)
