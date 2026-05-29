"""
tools/literature_verify.py — Citation cross-verification for academic papers.

Verifies that citations in a paper are:
1. Real (paper exists with correct title + authors)
2. Year-accurate (correct publication year)
3. Venue-accurate (published where stated)
4. DOI-valid (if provided)
5. Content-accurate (claims about cited papers match reality)
6. Consistent (inline citations match reference list)

Architecture:
    - Zero external dependencies (HTTP via urllib only)
    - search_fn injection pattern: caller provides search capability
    - All LLM calls go through llm/router.py if needed (currently rule-based only)
    - Graceful degradation: works offline with format-only checks
    - Integrates with review_engine's issue consolidation
"""

from __future__ import annotations

import re
import asyncio
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field


# ============================================================
# Data Classes
# ============================================================

@dataclass
class Citation:
    """A parsed reference entry."""
    raw_text: str
    authors: List[str]
    title: str
    year: Optional[int]
    venue: Optional[str]
    doi: Optional[str]
    index: int


@dataclass
class VerificationResult:
    """Verification result for a single citation."""
    citation: Citation
    status: str             # "verified" | "suspicious" | "not_found" | "error"
    confidence: float       # 0-1
    issues: List[str]
    search_evidence: str
    suggestion: str


@dataclass
class CitationClaim:
    """A claim made about a citation in the paper body."""
    citation: Citation
    claim_text: str
    claim_type: str         # "finding" | "method" | "definition" | "data" | "theory"
    position_in_paper: int


@dataclass
class ContentAccuracyResult:
    """Content accuracy verification for a citation claim."""
    citation_claim: CitationClaim
    status: str             # "accurate" | "inaccurate" | "unverifiable" | "overclaimed"
    confidence: float
    issue: str
    suggestion: str


# ============================================================
# DOI Validation
# ============================================================

_DOI_PATTERN = re.compile(r"10\.\d{4,9}/[^\s]+")


def _is_valid_doi_format(doi: str) -> bool:
    """Check DOI format validity (no network)."""
    if not doi:
        return False
    return bool(_DOI_PATTERN.fullmatch(doi.strip()))


def _extract_doi(text: str) -> Optional[str]:
    """Extract DOI from text."""
    patterns = [
        r"(?:doi[:\s]*)(10\.\d{4,9}/[^\s,;}\]]+)",
        r"(?:https?://doi\.org/)(10\.\d{4,9}/[^\s,;}\]]+)",
        r"(10\.\d{4,9}/[^\s,;}\]]+)",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(1).rstrip(".")
    return None


# ============================================================
# Reference Parsing
# ============================================================

def parse_references(text: str) -> List[Citation]:
    """Parse reference list from paper text.

    Supports formats:
    - [1] Author et al. (2020). Title. Journal, vol(issue), pages.
    - Author, A., Author, B. (2020). Title. In Proceedings of...
    - Chinese: [1] 张三, 李四. 标题[J]. 期刊, 年份.
    """
    citations = []
    ref_section = _extract_references_section(text) or text
    raw_refs = _split_into_individual_refs(ref_section)

    for idx, raw in enumerate(raw_refs, 1):
        citation = _parse_single_reference(raw.strip(), idx)
        if citation.title or citation.authors:
            citations.append(citation)

    return citations


def _extract_references_section(text: str) -> Optional[str]:
    """Extract the references section text."""
    patterns = [
        r"(?:^|\n)\s*(?:References|REFERENCES|参考文献|Bibliography|BIBLIOGRAPHY)\s*\n",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            return text[m.end():]
    return None


def _split_into_individual_refs(text: str) -> List[str]:
    """Split reference text into individual entries."""
    refs = []

    # Pattern 1: [1], [2], etc.
    numbered = list(re.finditer(r"(?:^|\n)\s*\[(\d+)\]\s*", text))
    if numbered:
        for i, m in enumerate(numbered):
            start = m.end()
            end = numbered[i + 1].start() if i + 1 < len(numbered) else len(text)
            content = text[start:end].strip()
            if content:
                refs.append(content)
        if refs:
            return refs

    # Pattern 2: "1." "2." etc.
    dotted = list(re.finditer(r"(?:^|\n)\s*(\d+)\.\s+", text))
    if dotted:
        for i, m in enumerate(dotted):
            start = m.end()
            end = dotted[i + 1].start() if i + 1 < len(dotted) else len(text)
            content = text[start:end].strip()
            if content:
                refs.append(content)
        if refs:
            return refs

    # Pattern 3: Paragraph-separated
    paragraphs = re.split(r"\n\s*\n", text)
    if len(paragraphs) > 1:
        return [p.strip() for p in paragraphs if p.strip()]

    # Fallback: line-based with merge
    lines = text.strip().split("\n")
    merged = []
    current = ""
    for line in lines:
        line = line.strip()
        if not line:
            if current:
                merged.append(current)
                current = ""
            continue
        if re.match(r"^(?:[A-Z\u4e00-\u9fff]|[①②③④⑤⑥⑦⑧⑨⑩])", line) and current:
            merged.append(current)
            current = line
        else:
            current = (current + " " + line) if current else line
    if current:
        merged.append(current)
    return [r for r in merged if r]


def _parse_single_reference(raw: str, index: int) -> Citation:
    """Parse a single reference string into structured Citation."""
    authors = []
    title = ""
    year = None
    venue = None
    cleaned = raw.strip()

    doi = _extract_doi(cleaned)

    year_match = re.search(r"\b((?:19|20)\d{2})\b", cleaned)
    if year_match:
        year = int(year_match.group(1))

    # Strategy 1: Chinese format
    zh_match = re.match(
        r"^([\u4e00-\u9fff\w,，、\s]+?)[.．。]\s*(.+?)\[([JCDM])\][.．。]\s*(.+?)(?:,|，)\s*(?:\d{4})",
        cleaned
    )
    if zh_match:
        title = zh_match.group(2).strip()
        venue = zh_match.group(4).strip().rstrip(",，.")
        authors = _parse_author_string(zh_match.group(1), is_chinese=True)
        return Citation(raw_text=raw, authors=authors, title=title,
                        year=year, venue=venue, doi=doi, index=index)

    # Strategy 2: APA-like
    apa_match = re.match(
        r"^(.+?)\s*\((\d{4})\)[.．,，]?\s*(.+?)(?:\.\s*(.+?))?$",
        cleaned, re.DOTALL
    )
    if apa_match:
        title_and_rest = apa_match.group(3)
        title_parts = re.split(r"\.\s+", title_and_rest, maxsplit=1)
        title = title_parts[0].strip().rstrip(".")
        if len(title_parts) > 1:
            venue = title_parts[1].strip().rstrip(".")
        elif apa_match.group(4):
            venue = apa_match.group(4).strip().rstrip(".")
        authors = _parse_author_string(apa_match.group(1))
        return Citation(raw_text=raw, authors=authors, title=title,
                        year=year, venue=venue, doi=doi, index=index)

    # Strategy 3: Generic fallback
    quoted = re.search(r"[\"\"](.*?)[\"\"]", cleaned)
    if quoted:
        title = quoted.group(1)
    if not title:
        italic = re.search(r"\*(.+?)\*", cleaned)
        if italic:
            title = italic.group(1)
    if not title:
        remainder = re.sub(r"\(\d{4}\)", "", cleaned)
        sentences = re.split(r"\.\s+", remainder)
        for sent in sentences:
            sent = sent.strip().strip(",").strip()
            if len(sent) > 10 and not re.match(r"^[A-Z][a-z]+,", sent):
                title = sent.rstrip(".")
                break

    if year_match:
        pre_year = cleaned[:year_match.start()].strip().rstrip("(,. ")
        if pre_year:
            authors = _parse_author_string(pre_year)

    if title:
        title_pos = cleaned.find(title)
        if title_pos >= 0:
            after_title = cleaned[title_pos + len(title):].strip(" .,")
            venue_match = re.match(r"[.．]\s*(.+?)(?:,\s*\d|$)", after_title)
            if venue_match:
                venue = venue_match.group(1).strip().rstrip(".,")
            elif after_title and not venue:
                in_proc = re.search(
                    r"(?:In\s+)?(?:Proceedings of\s+)?(.+?)(?:,\s*(?:vol|pp|\d)|\.$|$)",
                    after_title, re.IGNORECASE
                )
                if in_proc:
                    candidate = in_proc.group(1).strip().rstrip(".,")
                    if len(candidate) > 3:
                        venue = candidate

    return Citation(raw_text=raw, authors=authors, title=title,
                    year=year, venue=venue, doi=doi, index=index)


def _parse_author_string(text: str, is_chinese: bool = False) -> List[str]:
    """Parse author string into list of authors."""
    if not text:
        return []
    text = text.strip().rstrip(",;.")

    if is_chinese:
        parts = re.split(r"[,，、]\s*", text)
        return [p.strip() for p in parts if p.strip()]

    text = re.sub(r"\s*et\s+al\.?", "", text)
    text = re.sub(r"\s*(?:and|&)\s*", ", ", text)

    if ";" in text:
        parts = text.split(";")
        return [p.strip() for p in parts if p.strip()]

    parts = re.split(r",\s*(?=[A-Z\u4e00-\u9fff])", text)
    if len(parts) > 1:
        merged = []
        i = 0
        while i < len(parts):
            part = parts[i].strip()
            if i + 1 < len(parts) and re.match(r"^[A-Z]\.?\s*[A-Z]?\.?$", parts[i + 1].strip()):
                merged.append(f"{part}, {parts[i + 1].strip()}")
                i += 2
            else:
                merged.append(part)
                i += 1
        return [a.strip() for a in merged if a.strip()]

    return [text.strip()] if text.strip() else []


# ============================================================
# Verification Logic
# ============================================================

async def _default_search_fn(query: str) -> List[Dict[str, str]]:
    """Default search function: graceful degradation when no search available."""
    return []


def _compute_title_similarity(title1: str, title2: str) -> float:
    """Compute word-level Jaccard similarity between two titles."""
    if not title1 or not title2:
        return 0.0

    def normalize(t):
        t = t.lower().strip()
        t = re.sub(r"[^\w\s]", "", t)
        return set(t.split())

    words1 = normalize(title1)
    words2 = normalize(title2)
    if not words1 or not words2:
        return 0.0
    intersection = words1 & words2
    union = words1 | words2
    return len(intersection) / len(union)


def _check_author_match(citation_authors: List[str], evidence_text: str) -> bool:
    """Check if citation authors appear in search evidence."""
    if not citation_authors:
        return False
    evidence_lower = evidence_text.lower()
    for author in citation_authors[:3]:
        last_name = author.split(",")[0].strip().split()[-1].lower()
        if len(last_name) > 2 and last_name in evidence_lower:
            return True
    return False


def _check_year_match(citation_year: Optional[int], evidence_text: str) -> Tuple[bool, Optional[int]]:
    """Check year match, return (matches, evidence_year)."""
    if not citation_year:
        return True, None
    years_found = re.findall(r"\b((?:19|20)\d{2})\b", evidence_text)
    if not years_found:
        return True, None
    evidence_years = [int(y) for y in years_found]
    if citation_year in evidence_years:
        return True, citation_year
    from collections import Counter
    most_common = Counter(evidence_years).most_common(1)[0][0]
    return False, most_common


async def verify_single_citation(
    citation: Citation,
    search_fn: Optional[callable] = None,
) -> VerificationResult:
    """Verify a single citation via search.

    Strategy:
    1. Search by exact title
    2. If no results, search by author + year + keywords
    3. Analyze search results for match confidence

    Args:
        citation: Citation to verify
        search_fn: async (query: str) -> List[Dict[str, str]]
                   Each dict has {"title": ..., "snippet": ..., "url": ...}
    """
    if search_fn is None:
        search_fn = _default_search_fn

    issues = []
    evidence_parts = []
    confidence = 0.0
    status = "not_found"

    # Format-level checks (always available)
    if citation.doi and not _is_valid_doi_format(citation.doi):
        issues.append(f"DOI format invalid: '{citation.doi}'")
    if citation.year and (citation.year < 1900 or citation.year > 2025):
        issues.append(f"Year {citation.year} seems implausible.")

    # Step 1: Search by title
    search_results = []
    if citation.title:
        search_results = await search_fn(f'"{citation.title}"')

    # Step 2: Fallback search
    if not search_results and citation.authors:
        author_part = citation.authors[0].split(",")[0].strip() if citation.authors else ""
        year_part = str(citation.year) if citation.year else ""
        title_keywords = ""
        if citation.title:
            words = [w for w in citation.title.split() if len(w) > 3][:4]
            title_keywords = " ".join(words)
        query = f"{author_part} {year_part} {title_keywords}".strip()
        if query:
            search_results = await search_fn(query)

    # Step 3: Analyze results
    if not search_results:
        if issues:
            status = "suspicious"
            confidence = 0.4
        else:
            status = "not_found"
            confidence = 0.2
        evidence_parts.append("No search results available.")
    else:
        best_similarity = 0.0
        best_result = None

        for result in search_results:
            sim = _compute_title_similarity(citation.title, result.get("title", ""))
            if sim > best_similarity:
                best_similarity = sim
                best_result = result

        if best_result:
            combined_evidence = f"{best_result.get('title', '')} {best_result.get('snippet', '')}"
            evidence_parts.append(
                f"Best match: \"{best_result.get('title', '')}\" (similarity: {best_similarity:.2f})"
            )

            author_ok = _check_author_match(citation.authors, combined_evidence)
            year_ok, evidence_year = _check_year_match(citation.year, combined_evidence)

            if best_similarity >= 0.8:
                status = "verified"
                confidence = min(0.95, 0.7 + best_similarity * 0.3)
                if not year_ok and evidence_year:
                    issues.append(f"Year mismatch — published in {evidence_year}, not {citation.year}.")
                    status = "suspicious"
                    confidence = 0.7
                if not author_ok and citation.authors:
                    issues.append("Author names not confirmed in search results.")
                    if status == "verified":
                        confidence = max(confidence - 0.1, 0.6)
            elif best_similarity >= 0.4:
                status = "suspicious"
                confidence = 0.3 + best_similarity
                issues.append(
                    f"Partial title match (similarity {best_similarity:.2f}). "
                    f"Found: \"{best_result.get('title', '')}\""
                )
                if not year_ok and evidence_year:
                    issues.append(f"Year mismatch — evidence suggests {evidence_year}, not {citation.year}.")
            else:
                status = "not_found"
                confidence = 0.2
                evidence_parts.append(
                    f"No close match. Best: \"{best_result.get('title', '')}\" "
                    f"(similarity: {best_similarity:.2f})"
                )

    suggestion = _generate_suggestion(citation, status, issues)

    return VerificationResult(
        citation=citation,
        status=status,
        confidence=confidence,
        issues=issues,
        search_evidence=" | ".join(evidence_parts) if evidence_parts else "No evidence.",
        suggestion=suggestion,
    )


def _generate_suggestion(citation: Citation, status: str, issues: List[str]) -> str:
    """Generate fix suggestion based on verification status."""
    if status == "verified" and not issues:
        return "No action needed."
    if status == "not_found":
        return (
            "Verify this citation exists; may be hallucinated. "
            "Search for the exact title in Google Scholar or the publisher's website."
        )
    suggestions = []
    for issue in issues:
        if "Year mismatch" in issue:
            year_match = re.search(r"published in (\d{4})|suggests (\d{4})", issue)
            if year_match:
                correct_year = year_match.group(1) or year_match.group(2)
                suggestions.append(f'Change "({citation.year})" to "({correct_year})"')
        elif "DOI format invalid" in issue:
            suggestions.append("Fix or remove the DOI.")
        elif "Author names not confirmed" in issue:
            suggestions.append("Double-check author names spelling.")
        elif "Partial title match" in issue:
            suggestions.append("Verify the exact title; possible typo or abbreviation error.")
        elif "implausible" in issue:
            suggestions.append(f"Verify the publication year ({citation.year}).")
    return " ".join(suggestions) if suggestions else "Review this citation for accuracy."


# ============================================================
# Batch Verification
# ============================================================

async def verify_citations_batch(
    citations: List[Citation],
    max_concurrency: int = 3,
    search_fn: Optional[callable] = None,
) -> List[VerificationResult]:
    """Batch-verify citations with concurrency control."""
    if not citations:
        return []

    semaphore = asyncio.Semaphore(max_concurrency)

    async def _verify_with_limit(citation: Citation) -> VerificationResult:
        async with semaphore:
            try:
                return await verify_single_citation(citation, search_fn=search_fn)
            except Exception as e:
                return VerificationResult(
                    citation=citation,
                    status="error",
                    confidence=0.0,
                    issues=[f"Verification error: {str(e)}"],
                    search_evidence="",
                    suggestion="Manual verification required due to error.",
                )

    tasks = [_verify_with_limit(c) for c in citations]
    results = await asyncio.gather(*tasks)
    return list(results)


# ============================================================
# Citation Verification Report
# ============================================================

def generate_verification_report(results: List[VerificationResult]) -> str:
    """Generate a formatted citation verification report."""
    if not results:
        return "## Citation Verification Report\n\nNo citations to verify."

    verified = sum(1 for r in results if r.status == "verified")
    suspicious = sum(1 for r in results if r.status == "suspicious")
    not_found = sum(1 for r in results if r.status == "not_found")
    errors = sum(1 for r in results if r.status == "error")
    total = len(results)

    lines = [
        "## Citation Verification Report",
        "",
        f"Total: {total} citations checked",
        f"✓ Verified: {verified} | ⚠ Suspicious: {suspicious} | ✗ Not Found: {not_found}"
        + (f" | ⊘ Errors: {errors}" if errors else ""),
        "",
    ]

    problem_results = [r for r in results if r.status != "verified"]
    if not problem_results:
        lines.append("### All Citations Verified ✓")
        lines.append("")
        lines.append("No issues found. All citations appear to be valid.")
    else:
        lines.append("### Issues Found")
        lines.append("")
        for r in problem_results:
            c = r.citation
            if c.authors:
                author_display = (
                    f"{c.authors[0]} et al." if len(c.authors) > 2
                    else f"{c.authors[0]} & {c.authors[1]}" if len(c.authors) == 2
                    else c.authors[0]
                )
            else:
                author_display = "Unknown"
            year_display = f"({c.year})" if c.year else ""
            status_map = {
                "suspicious": "⚠ SUSPICIOUS",
                "not_found": "✗ NOT FOUND",
                "error": "⊘ ERROR",
            }
            lines.append(f"[{c.index}] {author_display} {year_display} → {status_map.get(r.status, r.status.upper())}")
            for issue in r.issues:
                lines.append(f"    Issue: {issue}")
            if r.suggestion and r.suggestion != "No action needed.":
                lines.append(f"    Suggestion: {r.suggestion}")
            if r.search_evidence and r.search_evidence != "No evidence.":
                lines.append(f"    Evidence: {r.search_evidence}")
            lines.append("")

    lines.append("---")
    lines.append(
        f"Confidence: High(≥0.8): {sum(1 for r in results if r.confidence >= 0.8)} | "
        f"Medium(0.4-0.8): {sum(1 for r in results if 0.4 <= r.confidence < 0.8)} | "
        f"Low(<0.4): {sum(1 for r in results if r.confidence < 0.4)}"
    )
    return "\n".join(lines)


# ============================================================
# Inline Citation & Consistency Check
# ============================================================

def extract_inline_citations(text: str) -> List[Tuple[str, int]]:
    """Extract inline citation markers from paper body.

    Identifies: (Author, Year), [1], [2,3], [1-5], Author (Year)
    """
    citations = []
    ref_start = None
    for pat in [r"\n\s*(?:References|REFERENCES|参考文献|Bibliography)\s*\n"]:
        m = re.search(pat, text)
        if m:
            ref_start = m.start()
            break
    body_text = text[:ref_start] if ref_start else text

    for m in re.finditer(r"\[(\d+(?:\s*[,\-–]\s*\d+)*)\]", body_text):
        citations.append((m.group(0), m.start()))
    for m in re.finditer(
        r"\(([A-Z][a-z]+(?:\s+(?:and|&)\s+[A-Z][a-z]+)?(?:\s+et\s+al\.?)?,\s*\d{4}(?:[a-z])?)\)",
        body_text
    ):
        citations.append((m.group(0), m.start()))
    for m in re.finditer(
        r"([A-Z][a-z]+(?:\s+(?:and|&)\s+[A-Z][a-z]+)?(?:\s+et\s+al\.?)?)\s+\((\d{4}(?:[a-z])?)\)",
        body_text
    ):
        citations.append((m.group(0), m.start()))

    return citations


def check_citation_consistency(
    inline_citations: List[Tuple[str, int]],
    references: List[Citation],
) -> List[Dict]:
    """Check consistency between inline citations and reference list.

    Detects:
    - Dangling references ([7] but only 6 refs exist)
    - Uncited references (in ref list but never cited)
    - Author-year mismatches
    """
    issues = []
    max_ref_index = len(references)
    cited_indices: set = set()

    for marker, pos in inline_citations:
        if not marker.startswith("["):
            continue
        inner = marker.strip("[]")
        range_match = re.match(r"(\d+)\s*[-–]\s*(\d+)", inner)
        if range_match:
            nums = list(range(int(range_match.group(1)), int(range_match.group(2)) + 1))
        else:
            nums = [int(n) for n in re.findall(r"\d+", inner)]
        for idx in nums:
            cited_indices.add(idx)
            if idx > max_ref_index:
                issues.append({
                    "type": "dangling_reference",
                    "marker": marker,
                    "position": pos,
                    "detail": f"Citation [{idx}] referenced but only {max_ref_index} references exist.",
                })
            elif idx < 1:
                issues.append({
                    "type": "invalid_index",
                    "marker": marker,
                    "position": pos,
                    "detail": f"Citation index {idx} is invalid (must be >= 1).",
                })

    # Check uncited references
    for ref in references:
        if ref.index not in cited_indices:
            issues.append({
                "type": "uncited_reference",
                "reference_index": ref.index,
                "title": ref.title,
                "detail": f"Reference [{ref.index}] ('{ref.title[:50]}...') is never cited in the text.",
            })

    # Check (Author, Year) consistency
    for marker, pos in inline_citations:
        if marker.startswith("["):
            continue
        ay_match = re.match(
            r"\(?([A-Z\u4e00-\u9fff][^,()]*?)(?:,?\s*)(\d{4}[a-z]?)\)?",
            marker
        )
        if not ay_match:
            continue
        cited_author = ay_match.group(1).strip()
        cited_year = ay_match.group(2)

        found_match = False
        for ref in references:
            if ref.year and str(ref.year) == cited_year[:4]:
                for author in ref.authors:
                    surname = author.split(",")[0].split()[-1] if author else ""
                    if surname and surname.lower() in cited_author.lower():
                        found_match = True
                        break
            if found_match:
                break

        if not found_match and references:
            issues.append({
                "type": "author_year_mismatch",
                "marker": marker,
                "position": pos,
                "detail": f"Inline citation '{marker}' does not match any reference in the list.",
            })

    return issues


# ============================================================
# Citation Content Accuracy (Overclaim Detection)
# ============================================================

OVERCLAIM_PATTERNS: Dict[str, List[str]] = {
    "proves": ["suggests", "indicates", "provides evidence"],
    "conclusively shows": ["shows", "demonstrates", "finds"],
    "establishes": ["suggests", "argues", "proposes"],
    "confirms": ["is consistent with", "supports", "provides evidence for"],
    "is well-established": ["has been suggested", "is debated", "remains contested"],
    "definitively demonstrates": ["demonstrates", "shows", "provides evidence"],
    "has proven": ["has suggested", "has shown", "has provided evidence"],
    "unequivocally shows": ["shows", "suggests", "indicates"],
}

_FINDING_VERBS = (
    r"(?:show|shows|showed|find|finds|found|demonstrate|demonstrates|demonstrated|"
    r"report|reports|reported|reveal|reveals|revealed|discover|discovers|discovered|"
    r"prove|proves|proved|confirm|confirms|confirmed|establish|establishes|established|"
    r"indicate|indicates|indicated|suggest|suggests|suggested|argue|argues|argued|"
    r"conclude|concludes|concluded|observe|observes|observed|document|documents|documented)"
)

_METHOD_VERBS = (
    r"(?:propose|proposes|proposed|develop|develops|developed|introduce|introduces|introduced|"
    r"design|designs|designed|implement|implements|implemented|present|presents|presented|"
    r"use|uses|used|employ|employs|employed|apply|applies|applied|build|builds|built|"
    r"construct|constructs|constructed|create|creates|created|extend|extends|extended)"
)

_DEFINITION_VERBS = (
    r"(?:define|defines|defined|characterize|characterizes|characterized|"
    r"describe|describes|described|formalize|formalizes|formalized|"
    r"conceptualize|conceptualizes|conceptualized|term|terms|termed)"
)


def _classify_claim_type(claim_text: str) -> str:
    """Classify claim type based on verb patterns."""
    lower = claim_text.lower()
    if re.search(_FINDING_VERBS, lower):
        return "finding"
    if re.search(_METHOD_VERBS, lower):
        return "method"
    if re.search(_DEFINITION_VERBS, lower):
        return "definition"
    if re.search(r"\b(?:data|dataset|corpus|sample|survey|experiment)\b", lower):
        return "data"
    if re.search(r"\b(?:theory|framework|model|hypothesis|conjecture)\b", lower):
        return "theory"
    return "finding"


def _extract_sentence_around(text: str, pos: int, context_chars: int = 300) -> str:
    """Extract complete sentence around a position."""
    start = max(0, pos - context_chars)
    end = min(len(text), pos + context_chars)
    segment = text[start:end]

    sentence_start = 0
    for pat in [r"[.!?]\s+", r"\n\s*\n"]:
        matches = list(re.finditer(pat, segment[:pos - start]))
        if matches:
            sentence_start = max(sentence_start, matches[-1].end())

    sentence_end = len(segment)
    end_match = re.search(r"[.!?](?:\s|$)", segment[pos - start:])
    if end_match:
        sentence_end = (pos - start) + end_match.end()

    return segment[sentence_start:sentence_end].strip()


def extract_citation_claims(text: str, citations: List[Citation]) -> List[CitationClaim]:
    """Extract specific claims about each citation from the paper body.

    Identifies patterns like:
    - "Author (Year) find/show/demonstrate that [CLAIM]"
    - "According to Author (Year), [CLAIM]"
    - "[CLAIM] (Author, Year)"
    - "Author (Year) propose/develop [METHOD]"
    """
    claims: List[CitationClaim] = []

    # Only process body text (exclude references section)
    ref_start = None
    for pat in [r"\n\s*(?:References|REFERENCES|\u53c2\u8003\u6587\u732e|Bibliography)\s*\n"]:
        m = re.search(pat, text)
        if m:
            ref_start = m.start()
            break
    body_text = text[:ref_start] if ref_start else text

    for citation in citations:
        if not citation.authors:
            continue

        first_author = citation.authors[0]
        surname = first_author.split(",")[0].strip().split()[-1]
        if not surname or len(surname) < 2:
            continue

        year_str = str(citation.year) if citation.year else r"\d{4}"
        surname_escaped = re.escape(surname)

        # Multiple matching patterns
        patterns = [
            # Pattern A: "Author (Year) verb..."
            r"(" + surname_escaped + r"(?:\s+(?:and|&)\s+\w+)?(?:\s+et\s+al\.?)?\s*)\(" + year_str + r"[a-z]?\)",
            # Pattern B: "(Author, Year)"
            r"\(" + surname_escaped + r"(?:\s+(?:and|&)\s+\w+)?(?:\s+et\s+al\.?)?,\s*" + year_str + r"[a-z]?\)",
            # Pattern C: "According to Author (Year)"
            r"[Aa]ccording\s+to\s+" + surname_escaped + r"(?:\s+(?:and|&)\s+\w+)?(?:\s+et\s+al\.?)?\s*\(" + year_str + r"[a-z]?\)",
            # Pattern D: "Following Author (Year)"
            r"[Ff]ollowing\s+" + surname_escaped + r"(?:\s+(?:and|&)\s+\w+)?(?:\s+et\s+al\.?)?\s*\(" + year_str + r"[a-z]?\)",
        ]
        # Pattern E: numbered citation
        if citation.index:
            patterns.append(r"\[" + str(citation.index) + r"\]")

        for pat in patterns:
            for m in re.finditer(pat, body_text, re.IGNORECASE):
                pos = m.start()
                claim_text = _extract_sentence_around(body_text, pos)
                if not claim_text or len(claim_text) < 15:
                    continue
                claim_type = _classify_claim_type(claim_text)
                claims.append(CitationClaim(
                    citation=citation,
                    claim_text=claim_text,
                    claim_type=claim_type,
                    position_in_paper=pos,
                ))

    # Deduplicate
    seen: set = set()
    deduped: List[CitationClaim] = []
    for claim in claims:
        key = (claim.citation.index, claim.position_in_paper)
        if key not in seen:
            seen.add(key)
            deduped.append(claim)

    return deduped


# ============================================================
# Overclaim Detection (rule-based, no LLM needed)
# ============================================================

# Common overclaim transformation patterns
OVERCLAIM_PATTERNS: Dict[str, List[str]] = {
    "proves": ["suggests", "indicates", "provides evidence"],
    "conclusively shows": ["shows", "demonstrates", "finds"],
    "establishes": ["suggests", "argues", "proposes"],
    "confirms": ["is consistent with", "supports", "provides evidence for"],
    "is well-established": ["has been suggested", "is debated", "remains contested"],
    "definitively demonstrates": ["demonstrates", "shows", "provides evidence"],
    "has proven": ["has suggested", "has shown", "has provided evidence"],
    "unequivocally shows": ["shows", "suggests", "indicates"],
}


def check_overclaim_in_citations(claims: List[CitationClaim]) -> List[ContentAccuracyResult]:
    """Detect overclaim language patterns in citation descriptions.

    Pure rule-based check (no network needed):
    - Flags strong claim verbs from OVERCLAIM_PATTERNS
    - Detects working paper vs published mismatch
    """
    results: List[ContentAccuracyResult] = []

    for claim in claims:
        lower_text = claim.claim_text.lower()
        found_overclaim = False

        for strong_word, soft_alternatives in OVERCLAIM_PATTERNS.items():
            strong_pattern = r"\b" + re.escape(strong_word) + r"\b"
            if re.search(strong_pattern, lower_text):
                suggested_word = soft_alternatives[0]
                alternatives_str = '", "'.join(soft_alternatives)
                results.append(ContentAccuracyResult(
                    citation_claim=claim,
                    status="overclaimed",
                    confidence=0.6,
                    issue=(
                        f"Strong claim language: \"{strong_word}\" \u2014 "
                        f"original work likely uses softer language "
                        f"(e.g., \"{alternatives_str}\")."
                    ),
                    suggestion=(
                        f"Consider replacing \"{strong_word}\" with "
                        f"\"{suggested_word}\" to accurately reflect "
                        f"the original paper's conclusions."
                    ),
                ))
                found_overclaim = True
                break

        if not found_overclaim:
            wp_issue = _check_working_paper_mismatch(claim)
            if wp_issue:
                results.append(wp_issue)

    return results


def _check_working_paper_mismatch(claim: CitationClaim) -> Optional[ContentAccuracyResult]:
    """Check if working paper is described with published-paper language."""
    citation = claim.citation
    venue_lower = (citation.venue or "").lower()
    is_working_paper = any(
        kw in venue_lower
        for kw in ["working paper", "wp", "nber", "ssrn", "arxiv", "preprint",
                   "mimeo", "unpublished", "manuscript"]
    )
    if not is_working_paper:
        return None

    lower_text = claim.claim_text.lower()
    published_language = [r"\bpublished\b", r"\bjournal\b", r"\bpeer[- ]reviewed\b", r"\bforthcoming\b"]
    for pat in published_language:
        if re.search(pat, lower_text):
            return ContentAccuracyResult(
                citation_claim=claim,
                status="overclaimed",
                confidence=0.7,
                issue=(
                    f"Citation is a working paper (venue: \"{citation.venue}\") "
                    f"but description implies formal publication."
                ),
                suggestion=(
                    "Verify if paper has been formally published. "
                    "If not, adjust language to reflect working paper status."
                ),
            )
    return None


async def check_citation_content_accuracy(
    claims: List[CitationClaim],
    verify_fn: Optional[callable] = None
) -> List[ContentAccuracyResult]:
    """Verify citation content accuracy.

    With verify_fn: deep verification against original paper content.
    Without verify_fn: graceful degradation to rule-based overclaim detection.
    """
    if verify_fn is None:
        results = check_overclaim_in_citations(claims)
        flagged_claims = {id(r.citation_claim) for r in results}
        for claim in claims:
            if id(claim) not in flagged_claims:
                results.append(ContentAccuracyResult(
                    citation_claim=claim,
                    status="unverifiable",
                    confidence=0.3,
                    issue="No verify_fn provided; cannot check against original paper.",
                    suggestion="Manually verify this claim against the original paper.",
                ))
        return results

    results: List[ContentAccuracyResult] = []
    for claim in claims:
        title = claim.citation.title
        if not title:
            results.append(ContentAccuracyResult(
                citation_claim=claim,
                status="unverifiable",
                confidence=0.2,
                issue="Citation has no title; cannot query original content.",
                suggestion="Add the correct title for this citation.",
            ))
            continue

        try:
            original_content = await verify_fn(title, claim.claim_text)
        except Exception:
            original_content = None

        if not original_content:
            overclaim_results = check_overclaim_in_citations([claim])
            if overclaim_results:
                results.extend(overclaim_results)
            else:
                results.append(ContentAccuracyResult(
                    citation_claim=claim,
                    status="unverifiable",
                    confidence=0.3,
                    issue="Could not retrieve original paper content.",
                    suggestion="Manually verify this claim.",
                ))
            continue

        accuracy_result = _compare_claim_with_original(claim, original_content)
        results.append(accuracy_result)

    return results


def _compare_claim_with_original(
    claim: CitationClaim,
    original_content: str
) -> ContentAccuracyResult:
    """Compare claim against original paper content."""
    claim_lower = claim.claim_text.lower()
    original_lower = original_content.lower()

    # Check overclaim patterns against original
    for strong_word, soft_alternatives in OVERCLAIM_PATTERNS.items():
        strong_pattern = r"\b" + re.escape(strong_word) + r"\b"
        if re.search(strong_pattern, claim_lower):
            for soft in soft_alternatives:
                if re.search(r"\b" + re.escape(soft) + r"\b", original_lower):
                    return ContentAccuracyResult(
                        citation_claim=claim,
                        status="overclaimed",
                        confidence=0.8,
                        issue=f"Paper claims \"{strong_word}\" but original uses \"{soft}\".",
                        suggestion=f"Replace \"{strong_word}\" with \"{soft}\".",
                    )

    # Check if original has hedging that claim ignores
    hedging_words = [
        "however", "although", "but", "caveat", "limitation",
        "may not", "does not necessarily", "under certain conditions",
        "with some exceptions", "remains unclear", "further research",
    ]
    original_has_hedging = any(h in original_lower for h in hedging_words)
    claim_has_hedging = any(h in claim_lower for h in hedging_words)

    if original_has_hedging and not claim_has_hedging:
        found_hedges = [h for h in hedging_words if h in original_lower]
        return ContentAccuracyResult(
            citation_claim=claim,
            status="overclaimed",
            confidence=0.65,
            issue=(
                f"Original includes caveats ({', '.join(found_hedges[:3])}) "
                f"not reflected in citation description."
            ),
            suggestion="Add qualifications to reflect caveats in the original work.",
        )

    return ContentAccuracyResult(
        citation_claim=claim,
        status="accurate",
        confidence=0.7,
        issue="No obvious accuracy issues detected.",
        suggestion="No action needed.",
    )


# ============================================================
# Claim-Citation Alignment Scoring (v3 Enhancement)
# ============================================================

@dataclass
class AlignmentScore:
    """Score representing how well a claim aligns with its citation."""
    citation_claim: CitationClaim
    score: float            # 0-1 alignment score
    components: Dict[str, float]  # breakdown by dimension
    flags: List[str]
    recommendation: str


# Alignment dimensions and their weight
_ALIGNMENT_WEIGHTS = {
    "specificity_match": 0.25,   # Does specificity of claim match cited work's scope?
    "temporal_coherence": 0.15,  # Is the cited work recent enough for the claim type?
    "hedging_alignment": 0.25,  # Is hedging level appropriate?
    "claim_type_fit": 0.20,     # Does the citation type support this claim type?
    "contextual_proximity": 0.15,  # Is the citation placed at the right textual location?
}

# Claim types that require recent citations (< 5 years)
_RECENCY_SENSITIVE_CLAIMS = {"finding", "data", "method"}

# Claim types that tolerate older citations
_RECENCY_TOLERANT_CLAIMS = {"theory", "definition"}

# Venue quality signals for different claim types
_VENUE_QUALITY_PATTERNS = {
    "finding": [r"journal", r"proceedings", r"transactions"],
    "method": [r"conference", r"proceedings", r"symposium", r"journal"],
    "theory": [r"review", r"handbook", r"journal", r"book"],
    "definition": [r"handbook", r"textbook", r"encyclopedia", r"standard"],
    "data": [r"journal", r"report", r"survey", r"proceedings"],
}


def compute_alignment_scores(
    claims: List[CitationClaim],
    current_year: int = 2024
) -> List[AlignmentScore]:
    """Compute alignment scores for all citation claims.

    Measures how well each claim is supported by its citation,
    beyond simple overclaim detection. This is about logical fit,
    not just language.
    """
    results: List[AlignmentScore] = []

    for claim in claims:
        components = {}
        flags = []

        # 1. Specificity Match
        specificity = _score_specificity_match(claim)
        components["specificity_match"] = specificity
        if specificity < 0.5:
            flags.append("vague_attribution")

        # 2. Temporal Coherence
        temporal = _score_temporal_coherence(claim, current_year)
        components["temporal_coherence"] = temporal
        if temporal < 0.4:
            flags.append("outdated_citation")

        # 3. Hedging Alignment
        hedging = _score_hedging_alignment(claim)
        components["hedging_alignment"] = hedging
        if hedging < 0.4:
            flags.append("hedging_mismatch")

        # 4. Claim-Type Fit
        type_fit = _score_claim_type_fit(claim)
        components["claim_type_fit"] = type_fit
        if type_fit < 0.4:
            flags.append("type_mismatch")

        # 5. Contextual Proximity (simplified: based on claim position patterns)
        proximity = _score_contextual_proximity(claim)
        components["contextual_proximity"] = proximity

        # Weighted final score
        final_score = sum(
            components[dim] * weight
            for dim, weight in _ALIGNMENT_WEIGHTS.items()
        )

        # Generate recommendation
        recommendation = _generate_alignment_recommendation(claim, components, flags)

        results.append(AlignmentScore(
            citation_claim=claim,
            score=final_score,
            components=components,
            flags=flags,
            recommendation=recommendation,
        ))

    return results


def _score_specificity_match(claim: CitationClaim) -> float:
    """Score: Does the claim's specificity match what a citation can support?

    A very specific quantitative claim (e.g., "reduces error by 35%") citing
    a broad survey paper = low alignment.
    """
    text = claim.claim_text.lower()

    # Detect specificity level of claim
    has_numbers = bool(re.search(r"\d+\.?\d*\s*%", text))
    has_comparison = bool(re.search(
        r"\b(outperform|better|worse|higher|lower|more|less|exceed|surpass)\b", text
    ))
    has_specific_method = bool(re.search(
        r"\b(using|via|through|employing|with|based on)\s+[A-Z]", claim.claim_text
    ))

    specificity_level = sum([has_numbers, has_comparison, has_specific_method])

    # Detect citation scope
    venue = (claim.citation.venue or "").lower()
    is_broad = any(kw in venue for kw in ["survey", "review", "handbook", "tutorial", "overview"])
    is_narrow = any(kw in venue for kw in ["letter", "short paper", "workshop", "note"])

    if specificity_level >= 2 and is_broad:
        return 0.3  # Specific claim from broad source
    elif specificity_level == 0 and is_narrow:
        return 0.6  # Vague claim from narrow source (waste, but not wrong)
    elif specificity_level >= 2 and not is_broad:
        return 0.9  # Specific claim from specific source
    else:
        return 0.7  # Default moderate alignment


def _score_temporal_coherence(claim: CitationClaim, current_year: int) -> float:
    """Score: Is the citation recent enough for the type of claim being made?"""
    year = claim.citation.year
    if not year:
        return 0.5  # Unknown year, neutral

    age = current_year - year
    claim_type = claim.claim_type

    if claim_type in _RECENCY_SENSITIVE_CLAIMS:
        if age <= 3:
            return 1.0
        elif age <= 5:
            return 0.8
        elif age <= 10:
            return 0.5
        else:
            return 0.3
    elif claim_type in _RECENCY_TOLERANT_CLAIMS:
        if age <= 20:
            return 1.0
        elif age <= 40:
            return 0.7
        else:
            return 0.5
    else:
        # Default decay
        if age <= 5:
            return 1.0
        elif age <= 10:
            return 0.7
        else:
            return 0.4


def _score_hedging_alignment(claim: CitationClaim) -> float:
    """Score: Is the hedging level in the claim appropriate?

    Strong claims need strong evidence (recent, top venue).
    Hedged claims are always safe.
    """
    text = claim.claim_text.lower()

    # Detect hedging in claim
    strong_signals = [
        r"\b(proves?|confirms?|establishes?|demonstrates? conclusively)\b",
        r"\b(it is well[- ]known|undeniably|unequivocally|definitively)\b",
        r"\b(always|never|all cases|without exception)\b",
    ]
    hedge_signals = [
        r"\b(suggests?|indicates?|may|might|could|appears?|seems?)\b",
        r"\b(evidence for|consistent with|supports?|partially)\b",
        r"\b(under (certain|some) conditions?|in (some|many) cases?)\b",
    ]

    strong_count = sum(1 for pat in strong_signals if re.search(pat, text))
    hedge_count = sum(1 for pat in hedge_signals if re.search(pat, text))

    # Check citation strength indicators
    venue = (claim.citation.venue or "").lower()
    year = claim.citation.year or 2020
    age = 2024 - year

    is_top_venue = any(kw in venue for kw in [
        "nature", "science", "lancet", "nejm", "cell",
        "econometrica", "qje", "aer", "jpe", "restud",
        "neurips", "icml", "iclr", "cvpr", "acl",
    ])

    if strong_count > 0 and not is_top_venue and age > 5:
        return 0.3  # Strong claim, weak/old source
    elif strong_count > 0 and is_top_venue and age <= 5:
        return 0.9  # Strong claim, strong recent source
    elif hedge_count > 0:
        return 0.9  # Hedged claims are generally well-aligned
    else:
        return 0.7  # Neutral default


def _score_claim_type_fit(claim: CitationClaim) -> float:
    """Score: Does the venue type fit the claim type?

    E.g., citing a conference paper for a "definition" claim is odd;
    citing a handbook would be better.
    """
    venue = (claim.citation.venue or "").lower()
    claim_type = claim.claim_type

    if not venue:
        return 0.6  # Unknown venue, slightly below neutral

    expected_patterns = _VENUE_QUALITY_PATTERNS.get(claim_type, [])
    matches = sum(1 for pat in expected_patterns if re.search(pat, venue))

    if matches >= 2:
        return 1.0
    elif matches == 1:
        return 0.8
    else:
        return 0.5


def _score_contextual_proximity(claim: CitationClaim) -> float:
    """Score: Is the citation in a contextually appropriate location?

    Based on claim position patterns — later in paper = more specific claims expected.
    """
    # Without full paper structure, use position as rough proxy
    pos = claim.position_in_paper

    # A "definition" claim late in the paper (pos > 10000) is unusual
    if claim.claim_type == "definition" and pos > 10000:
        return 0.5
    # A "finding" claim very early (in abstract/intro) is fine
    if claim.claim_type == "finding" and pos < 2000:
        return 0.8
    return 0.8  # Default: position is fine


def _generate_alignment_recommendation(
    claim: CitationClaim,
    components: Dict[str, float],
    flags: List[str]
) -> str:
    """Generate actionable recommendation based on alignment analysis."""
    if not flags:
        return "Citation is well-aligned with claim. No action needed."

    recommendations = []

    if "vague_attribution" in flags:
        recommendations.append(
            "Claim is too vague for this citation — either make the claim more specific "
            "or cite a broader source (e.g., a review paper)."
        )
    if "outdated_citation" in flags:
        recommendations.append(
            f"Citation ({claim.citation.year}) may be outdated for a "
            f"{claim.claim_type} claim. Consider finding a more recent source "
            f"or adding 'as of {claim.citation.year}' qualifier."
        )
    if "hedging_mismatch" in flags:
        recommendations.append(
            "Claim strength exceeds what this citation can support. "
            "Either hedge the claim or cite a stronger source."
        )
    if "type_mismatch" in flags:
        recommendations.append(
            f"Citation venue may not be ideal for a '{claim.claim_type}' claim. "
            f"Consider a source from: {', '.join(_VENUE_QUALITY_PATTERNS.get(claim.claim_type, ['relevant venue']))}"
        )

    return " ".join(recommendations)


def generate_alignment_report(scores: List[AlignmentScore]) -> str:
    """Generate claim-citation alignment report."""
    if not scores:
        return "## Claim-Citation Alignment Report\n\nNo claims to analyze."

    # Compute stats
    avg_score = sum(s.score for s in scores) / len(scores)
    low_scores = [s for s in scores if s.score < 0.5]
    medium_scores = [s for s in scores if 0.5 <= s.score < 0.7]
    high_scores = [s for s in scores if s.score >= 0.7]

    lines = [
        "## Claim-Citation Alignment Report",
        "",
        f"Analyzed: {len(scores)} citation claims",
        f"Average alignment: {avg_score:.2f}",
        f"  Well-aligned (≥0.7): {len(high_scores)} | "
        f"Moderate (0.5-0.7): {len(medium_scores)} | "
        f"Misaligned (<0.5): {len(low_scores)}",
        "",
    ]

    if low_scores:
        lines.append("### Misaligned Citations (Action Required)")
        lines.append("")
        for s in sorted(low_scores, key=lambda x: x.score):
            c = s.citation_claim.citation
            author = c.authors[0] if c.authors else "Unknown"
            claim_preview = s.citation_claim.claim_text[:80]
            if len(s.citation_claim.claim_text) > 80:
                claim_preview += "..."

            lines.append(
                f"[{c.index}] {author} ({c.year or '?'}) — "
                f"Score: {s.score:.2f} | Flags: {', '.join(s.flags)}"
            )
            lines.append(f"    Claim: \"{claim_preview}\"")
            lines.append(f"    → {s.recommendation}")
            # Show component breakdown
            worst_dim = min(s.components, key=s.components.get)
            lines.append(f"    Weakest dimension: {worst_dim} ({s.components[worst_dim]:.2f})")
            lines.append("")

    if medium_scores:
        lines.append("### Moderate Alignment (Consider Improving)")
        lines.append("")
        for s in sorted(medium_scores, key=lambda x: x.score)[:5]:  # Top 5
            c = s.citation_claim.citation
            author = c.authors[0] if c.authors else "Unknown"
            lines.append(
                f"[{c.index}] {author} ({c.year or '?'}) — "
                f"Score: {s.score:.2f} | Flags: {', '.join(s.flags) or 'none'}"
            )
            if s.recommendation != "Citation is well-aligned with claim. No action needed.":
                lines.append(f"    → {s.recommendation}")
            lines.append("")

    lines.append("---")
    lines.append(
        f"Alignment distribution: "
        f"High(≥0.7): {len(high_scores)} | "
        f"Medium(0.5-0.7): {len(medium_scores)} | "
        f"Low(<0.5): {len(low_scores)}"
    )

    return "\n".join(lines)


# ============================================================
# Report Generation
# ============================================================

def generate_verification_report(results: List[VerificationResult]) -> str:
    """Generate citation verification report."""
    if not results:
        return "## Citation Verification Report\n\nNo citations to verify."

    verified = sum(1 for r in results if r.status == "verified")
    suspicious = sum(1 for r in results if r.status == "suspicious")
    not_found = sum(1 for r in results if r.status == "not_found")
    errors = sum(1 for r in results if r.status == "error")
    total = len(results)

    lines = [
        "## Citation Verification Report",
        "",
        f"Total: {total} citations checked",
        f"\u2713 Verified: {verified} | \u26a0 Suspicious: {suspicious} | \u2717 Not Found: {not_found}"
        + (f" | \u2298 Errors: {errors}" if errors else ""),
        "",
    ]

    problem_results = [r for r in results if r.status != "verified"]
    if not problem_results:
        lines.append("### All Citations Verified \u2713")
        lines.append("")
        lines.append("No issues found. All citations appear to be valid.")
    else:
        lines.append("### Issues Found")
        lines.append("")

        for r in problem_results:
            c = r.citation
            if c.authors:
                if len(c.authors) > 2:
                    author_display = f"{c.authors[0]} et al."
                elif len(c.authors) == 2:
                    author_display = f"{c.authors[0]} & {c.authors[1]}"
                else:
                    author_display = c.authors[0]
            else:
                author_display = "Unknown"

            year_display = f"({c.year})" if c.year else ""
            status_map = {
                "suspicious": "\u26a0 SUSPICIOUS",
                "not_found": "\u2717 NOT FOUND",
                "error": "\u2298 ERROR",
            }
            status_display = status_map.get(r.status, r.status.upper())

            lines.append(f"[{c.index}] {author_display} {year_display} \u2192 {status_display}")
            for issue in r.issues:
                lines.append(f"    Issue: {issue}")
            if r.suggestion and r.suggestion != "No action needed.":
                lines.append(f"    Suggestion: {r.suggestion}")
            if r.search_evidence and r.search_evidence != "No evidence.":
                lines.append(f"    Evidence: {r.search_evidence}")
            lines.append("")

    lines.append("---")
    lines.append(
        f"Confidence distribution: "
        f"High(\u22650.8): {sum(1 for r in results if r.confidence >= 0.8)} | "
        f"Medium(0.4-0.8): {sum(1 for r in results if 0.4 <= r.confidence < 0.8)} | "
        f"Low(<0.4): {sum(1 for r in results if r.confidence < 0.4)}"
    )

    return "\n".join(lines)


def generate_content_accuracy_report(results: List[ContentAccuracyResult]) -> str:
    """Generate citation content accuracy report."""
    if not results:
        return "## Citation Content Accuracy Report\n\nNo citation claims to verify."

    accurate = sum(1 for r in results if r.status == "accurate")
    overclaimed = sum(1 for r in results if r.status == "overclaimed")
    inaccurate = sum(1 for r in results if r.status == "inaccurate")
    unverifiable = sum(1 for r in results if r.status == "unverifiable")
    total = len(results)

    lines = [
        "## Citation Content Accuracy Report",
        "",
        f"Checked: {total} citation claims",
        (
            f"\u2713 Accurate: {accurate} | "
            f"\u26a0 Overclaimed: {overclaimed} | "
            f"\u2717 Inaccurate: {inaccurate} | "
            f"? Unverifiable: {unverifiable}"
        ),
        "",
    ]

    problem_results = [r for r in results if r.status not in ("accurate", "unverifiable")]
    if not problem_results:
        lines.append("### No Content Accuracy Issues Detected")
        if unverifiable > 0:
            lines.append(f"\nNote: {unverifiable} claim(s) could not be verified.")
    else:
        lines.append("### Issues Found")
        lines.append("")

        for r in problem_results:
            claim = r.citation_claim
            c = claim.citation
            if c.authors:
                if len(c.authors) > 2:
                    author_display = f"{c.authors[0]} et al."
                elif len(c.authors) == 2:
                    author_display = f"{c.authors[0]} & {c.authors[1]}"
                else:
                    author_display = c.authors[0]
            else:
                author_display = "Unknown"

            year_display = f"({c.year})" if c.year else ""
            status_labels = {"overclaimed": "Overclaim", "inaccurate": "Inaccurate"}
            label = status_labels.get(r.status, r.status.capitalize())

            claim_preview = claim.claim_text
            if len(claim_preview) > 120:
                claim_preview = claim_preview[:117] + "..."

            lines.append(
                f"[{label}] Ref [{c.index}] {author_display} {year_display}: "
                f"\"{claim_preview}\""
            )
            lines.append(f"    \u2192 {r.issue}")
            if r.suggestion and r.suggestion != "No action needed.":
                lines.append(f"    \u2192 Suggestion: {r.suggestion}")
            lines.append(f"    Confidence: {r.confidence:.2f}")
            lines.append("")

    lines.append("---")
    lines.append(
        f"Summary: {total} claims checked | "
        f"{overclaimed + inaccurate} issue(s) found | "
        f"{unverifiable} unverifiable"
    )

    return "\n".join(lines)
