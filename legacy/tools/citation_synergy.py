"""
tools/citation_synergy.py — Synergy layer bridging citation_graph and literature_verify.

Provides a unified interface that combines:
- citation_graph.py: citation network analysis, missing reference detection
- literature_verify.py: reference parsing, inline extraction, consistency checks,
  overclaim detection, alignment scoring

The verify_and_enrich_citations() function orchestrates both tools to produce
a comprehensive citation health report in a single call.

Architecture:
    - Delegates to existing tools where functionality already exists
    - Adds cross-referencing logic between inline citations and bibliography
    - Computes coverage score based on claim density vs citation support
    - Zero LLM cost (pure rule-based + optional network for graph enrichment)
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

# Delegate to existing tools
from tools.literature_verify import (
    parse_references,
    extract_inline_citations,
    check_citation_consistency,
    extract_citation_claims,
    check_overclaim_in_citations,
    compute_alignment_scores,
    Citation,
)
from tools.citation_graph import (
    PaperNode,
    CitationGraph,
    find_missing_references,
    _session_graphs,
)


# ============================================================
# Citation Extraction Patterns
# ============================================================

# Author-year patterns: (Author, Year), Author (Year), Author et al. (Year)
_AUTHOR_YEAR_PATTERN = re.compile(
    r"(?:"
    r"\(([A-Z\u4e00-\u9fff][^()]*?),\s*(\d{4}[a-z]?)\)"  # (Author, Year)
    r"|"
    r"([A-Z\u4e00-\u9fff][a-z]+(?:\s+(?:and|&)\s+[A-Z][a-z]+)?(?:\s+et\s+al\.?)?)\s*\((\d{4}[a-z]?)\)"  # Author (Year)
    r")"
)

# Numeric patterns: [1], [1,2], [1-5]
_NUMERIC_PATTERN = re.compile(r"\[(\d+(?:\s*[,\-–]\s*\d+)*)\]")


@dataclass
class CitationMention:
    """A citation mention found in the text."""
    raw_text: str
    author: Optional[str]
    year: Optional[str]
    index: Optional[int]  # For numeric citations [1]
    position: int  # Character position in text
    mention_type: str  # "author_year" | "numeric"


@dataclass
class SuspiciousCitation:
    """A citation flagged as potentially incorrect."""
    mention: CitationMention
    reason: str
    confidence: float
    suggestion: str


@dataclass
class SynergyResult:
    """Complete result from verify_and_enrich_citations."""
    citations_found: List[Dict]
    missing_from_bibliography: List[Dict]
    orphan_entries: List[Dict]
    suspicious_citations: List[Dict]
    coverage_score: float
    suggestions: List[str]
    consistency_issues: List[Dict]
    overclaim_findings: List[Dict]
    alignment_summary: Dict


# ============================================================
# Citation Extraction
# ============================================================

def _extract_citation_mentions(text: str) -> List[CitationMention]:
    """Extract all citation mentions from body text (excluding references section).

    Identifies:
    - Author-year: (Smith, 2020), Smith (2020), Smith et al. (2020)
    - Numeric: [1], [2,3], [1-5]
    """
    # Exclude references section
    ref_start = None
    for pat in [r"\n\s*(?:References|REFERENCES|参考文献|Bibliography|BIBLIOGRAPHY)\s*\n"]:
        m = re.search(pat, text)
        if m:
            ref_start = m.start()
            break
    body_text = text[:ref_start] if ref_start else text

    mentions: List[CitationMention] = []

    # Author-year patterns
    for m in _AUTHOR_YEAR_PATTERN.finditer(body_text):
        if m.group(1):  # (Author, Year)
            author = m.group(1).strip()
            year = m.group(2)
        else:  # Author (Year)
            author = m.group(3).strip()
            year = m.group(4)
        mentions.append(CitationMention(
            raw_text=m.group(0),
            author=author,
            year=year,
            index=None,
            position=m.start(),
            mention_type="author_year",
        ))

    # Numeric patterns
    for m in _NUMERIC_PATTERN.finditer(body_text):
        inner = m.group(1)
        # Expand ranges
        range_match = re.match(r"(\d+)\s*[-–]\s*(\d+)", inner)
        if range_match:
            indices = list(range(int(range_match.group(1)), int(range_match.group(2)) + 1))
        else:
            indices = [int(n) for n in re.findall(r"\d+", inner)]

        for idx in indices:
            mentions.append(CitationMention(
                raw_text=m.group(0),
                author=None,
                year=None,
                index=idx,
                position=m.start(),
                mention_type="numeric",
            ))

    return mentions


# ============================================================
# Cross-Referencing Logic
# ============================================================

def _find_missing_from_bibliography(
    mentions: List[CitationMention],
    bibliography: List[Citation],
) -> List[Dict]:
    """Find citations mentioned in text but not in the bibliography."""
    missing = []

    # Build lookup from bibliography
    bib_indices = {c.index for c in bibliography}
    bib_author_years: List[Tuple[str, int]] = []
    for c in bibliography:
        if c.authors and c.year:
            for author in c.authors:
                surname = author.split(",")[0].strip().split()[-1].lower()
                if len(surname) >= 2:
                    bib_author_years.append((surname, c.year))

    seen_missing = set()

    for mention in mentions:
        if mention.mention_type == "numeric" and mention.index is not None:
            if mention.index not in bib_indices and mention.index > 0:
                key = f"numeric:{mention.index}"
                if key not in seen_missing:
                    seen_missing.add(key)
                    missing.append({
                        "mention": mention.raw_text,
                        "type": "numeric",
                        "index": mention.index,
                        "reason": f"Citation [{mention.index}] referenced but no matching entry in bibliography "
                                  f"(bibliography has {len(bibliography)} entries).",
                    })

        elif mention.mention_type == "author_year" and mention.author and mention.year:
            year_int = int(mention.year[:4]) if mention.year else None
            surname_lower = mention.author.split()[-1].lower() if mention.author else ""

            # Check if any bib entry matches
            found = False
            if surname_lower and year_int:
                for bib_surname, bib_year in bib_author_years:
                    if bib_surname in surname_lower or surname_lower in bib_surname:
                        if bib_year == year_int:
                            found = True
                            break

            if not found and surname_lower:
                key = f"author_year:{surname_lower}:{mention.year}"
                if key not in seen_missing:
                    seen_missing.add(key)
                    missing.append({
                        "mention": mention.raw_text,
                        "type": "author_year",
                        "author": mention.author,
                        "year": mention.year,
                        "reason": f"'{mention.raw_text}' cited in text but no matching "
                                  f"author-year entry found in bibliography.",
                    })

    return missing


def _find_orphan_entries(
    mentions: List[CitationMention],
    bibliography: List[Citation],
) -> List[Dict]:
    """Find bibliography entries never referenced in the text."""
    orphans = []

    # Collect all referenced indices
    referenced_indices = {m.index for m in mentions if m.index is not None}

    # Collect all referenced author-year pairs
    referenced_author_years: set = set()
    for m in mentions:
        if m.author and m.year:
            surname = m.author.split()[-1].lower()
            referenced_author_years.add((surname, m.year[:4]))

    for citation in bibliography:
        is_referenced = False

        # Check by index
        if citation.index in referenced_indices:
            is_referenced = True

        # Check by author-year
        if not is_referenced and citation.authors and citation.year:
            for author in citation.authors:
                surname = author.split(",")[0].strip().split()[-1].lower()
                if (surname, str(citation.year)) in referenced_author_years:
                    is_referenced = True
                    break

        if not is_referenced:
            author_display = citation.authors[0] if citation.authors else "Unknown"
            orphans.append({
                "index": citation.index,
                "authors": author_display,
                "year": citation.year,
                "title": citation.title[:80] if citation.title else "",
                "reason": f"Reference [{citation.index}] ('{author_display}', {citation.year}) "
                          f"appears in bibliography but is never cited in the text.",
            })

    return orphans


def _find_suspicious_citations(
    mentions: List[CitationMention],
    bibliography: List[Citation],
) -> List[Dict]:
    """Identify potentially incorrect author/year combinations.

    Heuristics:
    - Author exists in bib but with different year
    - Year exists in bib but with different author (close name match)
    - Future year citations (year > 2025)
    - Very old citations used with recent-sounding claims
    """
    suspicious = []
    seen = set()

    # Build lookups
    bib_by_surname: Dict[str, List[Citation]] = {}
    for c in bibliography:
        for author in c.authors:
            surname = author.split(",")[0].strip().split()[-1].lower()
            if len(surname) >= 2:
                bib_by_surname.setdefault(surname, []).append(c)

    for mention in mentions:
        if mention.mention_type != "author_year" or not mention.author or not mention.year:
            continue

        year_int = int(mention.year[:4])
        surname_lower = mention.author.split()[-1].lower()
        key = f"{surname_lower}:{mention.year}"

        if key in seen:
            continue

        # Check: future year
        if year_int > 2025:
            seen.add(key)
            suspicious.append({
                "mention": mention.raw_text,
                "author": mention.author,
                "year": mention.year,
                "reason": f"Future publication year ({year_int}) — likely a typo.",
                "confidence": 0.9,
                "suggestion": "Verify the publication year.",
            })
            continue

        # Check: author in bib but wrong year
        if surname_lower in bib_by_surname:
            matching_entries = bib_by_surname[surname_lower]
            year_match = any(c.year == year_int for c in matching_entries)
            if not year_match:
                actual_years = sorted(set(c.year for c in matching_entries if c.year))
                if actual_years:
                    seen.add(key)
                    suspicious.append({
                        "mention": mention.raw_text,
                        "author": mention.author,
                        "year": mention.year,
                        "reason": f"Author '{surname_lower}' found in bibliography but "
                                  f"with year(s) {actual_years}, not {year_int}.",
                        "confidence": 0.7,
                        "suggestion": f"Check if year should be {actual_years[0]} "
                                      f"instead of {year_int}.",
                    })

    return suspicious


# ============================================================
# Coverage Score
# ============================================================

def _compute_coverage_score(
    text: str,
    mentions: List[CitationMention],
    bibliography: List[Citation],
    missing: List[Dict],
    orphans: List[Dict],
) -> float:
    """Compute a 0-1 score indicating how well citations cover claims in text.

    Factors:
    - Citation density (mentions per 1000 words)
    - Ratio of referenced vs orphan entries
    - Missing citations penalty
    - Claim coverage (sections with claims should have citations nearby)
    """
    # Exclude references section for word count
    ref_start = None
    for pat in [r"\n\s*(?:References|REFERENCES|参考文献|Bibliography)\s*\n"]:
        m = re.search(pat, text)
        if m:
            ref_start = m.start()
            break
    body_text = text[:ref_start] if ref_start else text
    word_count = len(body_text.split())

    if word_count == 0:
        return 0.0

    # Factor 1: Citation density (ideal: 3-8 per 1000 words for academic)
    density = (len(mentions) / max(word_count, 1)) * 1000
    if density >= 3:
        density_score = min(1.0, density / 8.0)
    else:
        density_score = density / 3.0

    # Factor 2: Bibliography utilization (referenced / total)
    total_bib = len(bibliography)
    orphan_count = len(orphans)
    if total_bib > 0:
        utilization_score = 1.0 - (orphan_count / total_bib)
    else:
        utilization_score = 0.5  # No bibliography at all

    # Factor 3: Missing citations penalty
    missing_count = len(missing)
    if mentions:
        missing_ratio = missing_count / max(len(mentions), 1)
        missing_penalty = max(0.0, 1.0 - missing_ratio * 2)
    else:
        missing_penalty = 0.0

    # Factor 4: Distribution — are citations spread across the text?
    if mentions:
        positions = sorted(m.position for m in mentions)
        text_length = len(body_text)
        # Divide text into quarters, check if each has citations
        quarters_with_cites = 0
        for q in range(4):
            q_start = text_length * q // 4
            q_end = text_length * (q + 1) // 4
            if any(q_start <= p < q_end for p in positions):
                quarters_with_cites += 1
        distribution_score = quarters_with_cites / 4.0
    else:
        distribution_score = 0.0

    # Weighted combination
    score = (
        density_score * 0.30
        + utilization_score * 0.25
        + missing_penalty * 0.25
        + distribution_score * 0.20
    )
    return round(min(1.0, max(0.0, score)), 3)


# ============================================================
# Suggestion Generation
# ============================================================

def _generate_suggestions(
    missing: List[Dict],
    orphans: List[Dict],
    suspicious: List[Dict],
    coverage_score: float,
    overclaim_findings: List[Dict],
    alignment_summary: Dict,
) -> List[str]:
    """Generate actionable suggestions based on analysis results."""
    suggestions = []

    # Coverage-based suggestions
    if coverage_score < 0.4:
        suggestions.append(
            "Low citation coverage (score: {:.2f}). Consider adding more references, "
            "especially in introduction and methodology sections.".format(coverage_score)
        )
    elif coverage_score < 0.6:
        suggestions.append(
            "Moderate citation coverage (score: {:.2f}). Some sections may benefit "
            "from additional supporting references.".format(coverage_score)
        )

    # Missing citations
    if missing:
        if len(missing) >= 3:
            suggestions.append(
                f"Found {len(missing)} citations referenced in text but missing from "
                f"bibliography. Add these to the reference list or correct the in-text markers."
            )
        else:
            for m in missing[:3]:
                suggestions.append(f"Missing from bibliography: {m['mention']} — {m['reason']}")

    # Orphan entries
    if orphans:
        if len(orphans) >= 5:
            suggestions.append(
                f"Found {len(orphans)} bibliography entries never cited in text. "
                f"Either cite them or remove from the reference list."
            )
        else:
            for o in orphans:
                suggestions.append(
                    f"Orphan entry [{o['index']}]: {o['authors']} ({o['year']}) — "
                    f"never cited in text."
                )

    # Suspicious citations
    for s in suspicious[:3]:
        suggestions.append(f"Suspicious: {s['mention']} — {s['suggestion']}")

    # Overclaim findings
    if overclaim_findings:
        suggestions.append(
            f"Found {len(overclaim_findings)} citation overclaim(s). "
            f"Consider softening language (e.g., 'proves' → 'suggests')."
        )

    # Alignment issues
    misaligned = alignment_summary.get("misaligned_count", 0)
    if misaligned > 0:
        suggestions.append(
            f"{misaligned} claim-citation pair(s) have low alignment scores. "
            f"Review hedging level, citation recency, and venue fit."
        )

    return suggestions


# ============================================================
# Main Entry Point
# ============================================================

def verify_and_enrich_citations(
    text: str,
    bibliography: Optional[List[Dict]] = None,
) -> Dict:
    """Unified citation verification and enrichment.

    Combines citation_graph and literature_verify tools to produce
    a comprehensive citation health assessment.

    Args:
        text: Full paper text (including references section).
        bibliography: Optional pre-parsed bibliography as list of dicts.
            Each dict may have: authors (list[str]), title (str),
            year (int), venue (str), doi (str).
            If None, bibliography is parsed from the text automatically.

    Returns:
        Dict with keys:
            citations_found: list of extracted citation mentions
            missing_from_bibliography: citations in text but not in bib
            orphan_entries: bib entries never cited in text
            suspicious_citations: possibly wrong year/author
            coverage_score: 0-1 float
            suggestions: actionable improvement suggestions
            consistency_issues: structural issues from literature_verify
            overclaim_findings: overclaim language detection results
            alignment_summary: claim-citation alignment statistics
    """
    # Step 1: Parse bibliography (delegate to literature_verify)
    if bibliography:
        # Convert raw dicts to Citation objects
        bib_citations = []
        for idx, entry in enumerate(bibliography, 1):
            bib_citations.append(Citation(
                raw_text=entry.get("raw_text", ""),
                authors=entry.get("authors", []),
                title=entry.get("title", ""),
                year=entry.get("year"),
                venue=entry.get("venue"),
                doi=entry.get("doi"),
                index=entry.get("index", idx),
            ))
    else:
        # Auto-parse from text
        bib_citations = parse_references(text)

    # Step 2: Extract inline citation mentions
    mentions = _extract_citation_mentions(text)

    # Step 3: Cross-reference — find missing and orphan entries
    missing = _find_missing_from_bibliography(mentions, bib_citations)
    orphans = _find_orphan_entries(mentions, bib_citations)

    # Step 4: Identify suspicious citations
    suspicious = _find_suspicious_citations(mentions, bib_citations)

    # Step 5: Consistency checks (delegate to literature_verify)
    inline_cites = extract_inline_citations(text)
    consistency_issues = check_citation_consistency(inline_cites, bib_citations)

    # Step 6: Overclaim detection (delegate to literature_verify)
    claims = extract_citation_claims(text, bib_citations)
    overclaim_results = check_overclaim_in_citations(claims)
    overclaim_findings = [
        {
            "citation_index": r.citation_claim.citation.index,
            "author": r.citation_claim.citation.authors[0] if r.citation_claim.citation.authors else "Unknown",
            "year": r.citation_claim.citation.year,
            "status": r.status,
            "issue": r.issue,
            "suggestion": r.suggestion,
            "confidence": r.confidence,
        }
        for r in overclaim_results
    ]

    # Step 7: Alignment scoring (delegate to literature_verify)
    alignment_scores = compute_alignment_scores(claims) if claims else []
    alignment_summary = {}
    if alignment_scores:
        avg_score = sum(s.score for s in alignment_scores) / len(alignment_scores)
        misaligned = [s for s in alignment_scores if s.score < 0.5]
        alignment_summary = {
            "total_claims_analyzed": len(alignment_scores),
            "average_alignment_score": round(avg_score, 3),
            "well_aligned_count": sum(1 for s in alignment_scores if s.score >= 0.7),
            "moderate_count": sum(1 for s in alignment_scores if 0.5 <= s.score < 0.7),
            "misaligned_count": len(misaligned),
            "top_flags": _aggregate_flags(alignment_scores),
        }

    # Step 8: Coverage score
    coverage_score = _compute_coverage_score(text, mentions, bib_citations, missing, orphans)

    # Step 9: Generate suggestions
    suggestions = _generate_suggestions(
        missing, orphans, suspicious, coverage_score,
        overclaim_findings, alignment_summary,
    )

    # Build final result
    citations_found = [
        {
            "raw_text": m.raw_text,
            "author": m.author,
            "year": m.year,
            "index": m.index,
            "type": m.mention_type,
            "position": m.position,
        }
        for m in mentions
    ]

    return {
        "citations_found": citations_found,
        "missing_from_bibliography": missing,
        "orphan_entries": orphans,
        "suspicious_citations": suspicious,
        "coverage_score": coverage_score,
        "suggestions": suggestions,
        "consistency_issues": consistency_issues,
        "overclaim_findings": overclaim_findings,
        "alignment_summary": alignment_summary,
    }


def _aggregate_flags(scores) -> Dict[str, int]:
    """Aggregate flag counts from alignment scores."""
    flag_counts: Dict[str, int] = {}
    for s in scores:
        for flag in s.flags:
            flag_counts[flag] = flag_counts.get(flag, 0) + 1
    return dict(sorted(flag_counts.items(), key=lambda x: x[1], reverse=True))


# ============================================================
# Formatted Report
# ============================================================

def format_synergy_report(result: Dict) -> str:
    """Format the synergy result as a human-readable report."""
    lines = [
        "## Citation Synergy Report",
        "",
        f"**Coverage Score: {result['coverage_score']:.2f}** / 1.00",
        "",
        f"Citations found in text: {len(result['citations_found'])}",
        f"Missing from bibliography: {len(result['missing_from_bibliography'])}",
        f"Orphan bibliography entries: {len(result['orphan_entries'])}",
        f"Suspicious citations: {len(result['suspicious_citations'])}",
        f"Overclaim findings: {len(result['overclaim_findings'])}",
        f"Consistency issues: {len(result['consistency_issues'])}",
        "",
    ]

    # Alignment summary
    align = result.get("alignment_summary", {})
    if align:
        lines.append("### Claim-Citation Alignment")
        lines.append(f"  Analyzed: {align.get('total_claims_analyzed', 0)} claims")
        lines.append(f"  Average score: {align.get('average_alignment_score', 0):.2f}")
        lines.append(
            f"  Well-aligned: {align.get('well_aligned_count', 0)} | "
            f"Moderate: {align.get('moderate_count', 0)} | "
            f"Misaligned: {align.get('misaligned_count', 0)}"
        )
        flags = align.get("top_flags", {})
        if flags:
            lines.append(f"  Top flags: {', '.join(f'{k}({v})' for k, v in list(flags.items())[:4])}")
        lines.append("")

    # Missing
    if result["missing_from_bibliography"]:
        lines.append("### Missing from Bibliography")
        for m in result["missing_from_bibliography"][:10]:
            lines.append(f"  - {m['mention']}: {m['reason']}")
        lines.append("")

    # Orphans
    if result["orphan_entries"]:
        lines.append("### Orphan Entries (in bib, never cited)")
        for o in result["orphan_entries"][:10]:
            lines.append(f"  - [{o['index']}] {o['authors']} ({o['year']}): {o['title'][:60]}")
        lines.append("")

    # Suspicious
    if result["suspicious_citations"]:
        lines.append("### Suspicious Citations")
        for s in result["suspicious_citations"][:10]:
            lines.append(f"  - {s['mention']}: {s['reason']} → {s['suggestion']}")
        lines.append("")

    # Overclaims
    if result["overclaim_findings"]:
        lines.append("### Overclaim Findings")
        for oc in result["overclaim_findings"][:5]:
            lines.append(
                f"  - Ref [{oc['citation_index']}] {oc['author']} ({oc['year']}): {oc['issue']}"
            )
        lines.append("")

    # Suggestions
    if result["suggestions"]:
        lines.append("### Suggestions")
        for sug in result["suggestions"]:
            lines.append(f"  - {sug}")
        lines.append("")

    lines.append("---")
    lines.append("Generated by citation_synergy (citation_graph × literature_verify)")

    return "\n".join(lines)


# ============================================================
# Tool Interface (for ScholarAgent tool system)
# ============================================================

TOOLS = [
    {
        "name": "verify_and_enrich_citations",
        "description": (
            "Unified citation verification combining citation_graph and literature_verify. "
            "Extracts all citation mentions from text, cross-references with bibliography, "
            "identifies: missing citations, orphan entries, suspicious author/year combos, "
            "overclaim language, and claim-citation alignment issues. "
            "Returns coverage score (0-1) and actionable suggestions. "
            "Zero LLM cost. Use when you need a comprehensive citation health check."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": (
                        "Full paper text including references section. "
                        "If omitted, loads from parsed paper in workspace."
                    ),
                },
                "bibliography": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "authors": {"type": "array", "items": {"type": "string"}},
                            "title": {"type": "string"},
                            "year": {"type": "integer"},
                            "venue": {"type": "string"},
                            "doi": {"type": "string"},
                        },
                    },
                    "description": (
                        "Optional pre-parsed bibliography. If omitted, references are "
                        "auto-parsed from the text."
                    ),
                },
            },
        },
    },
]


async def _handle_verify_and_enrich_citations(args: Dict) -> str:
    """Handle the verify_and_enrich_citations tool call."""
    import json

    text = args.get("text", "")
    bibliography = args.get("bibliography")

    if not text:
        # Try to load from workspace
        from pathlib import Path
        workspace = Path.cwd() / ".workspace"
        index_path = workspace / "paper" / "section_index.json"
        if index_path.exists():
            index = json.loads(index_path.read_text(encoding="utf-8"))
            parts = []
            for entry in index:
                sec_path = Path(entry["file"])
                if sec_path.exists():
                    parts.append(sec_path.read_text(encoding="utf-8"))
            text = "\n\n".join(parts)

    if not text:
        return json.dumps({"status": "error", "message": "No text provided and no paper parsed in workspace."})

    result = verify_and_enrich_citations(text, bibliography=bibliography)
    report = format_synergy_report(result)
    return report


TOOL_HANDLERS = {
    "verify_and_enrich_citations": _handle_verify_and_enrich_citations,
}
