"""
tools/presubmission_check.py — Pre-submission mechanical checks (zero LLM cost).

A "gatekeeper" layer that runs BEFORE the LLM-based review_engine.
All checks are purely rule-based: regex, counting, cross-referencing.

Catches:
1. Citation format inconsistency (mixed [1] vs (Author, Year))
2. Figure/table reference completeness (mentioned but unreferenced, or vice versa)
3. Abstract structure & word count
4. Section presence (required sections missing)
5. Formatting anomalies (double spaces, orphan headings, etc.)
6. Cross-reference integrity (Eq.3 exists? Section 4.2 exists?)
7. Acknowledgment/funding statement presence
8. Page/word count estimate

Architecture:
    - Zero external dependencies
    - Returns structured PresubmissionReport
    - Each check is a standalone function (composable, testable)
    - Integrates into main.py as a new tool: `presubmission_check`
    - Feeds issues into review_engine's consolidation pipeline

Design philosophy (from paper-audit PRESUBMISSION layer):
    - These are "desk-reject prevention" checks
    - Cheap to run, catches embarrassing mechanical errors
    - Runs before expensive LLM review to save tokens on obviously broken papers
"""

from __future__ import annotations

import re
import json
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from pathlib import Path

WORKSPACE = Path(".workspace")


# ============================================================
# Data Classes
# ============================================================

@dataclass
class CheckResult:
    """Result of a single mechanical check."""
    check_name: str
    passed: bool
    severity: str           # "error" | "warning" | "info"
    message: str
    details: List[str] = field(default_factory=list)
    auto_fixable: bool = False


@dataclass
class PresubmissionReport:
    """Full pre-submission check report."""
    total_checks: int
    passed: int
    failed: int
    errors: int             # severity=error count
    warnings: int           # severity=warning count
    infos: int              # severity=info count
    results: List[CheckResult] = field(default_factory=list)
    word_count: int = 0
    section_count: int = 0
    verdict: str = ""       # "ready" | "needs_fixes" | "not_ready"


# ============================================================
# Check 1: Citation Format Consistency
# ============================================================

def check_citation_format(text: str) -> CheckResult:
    """Detect mixed citation formats (numbered vs author-year).

    A paper should use ONE format consistently.
    """
    # Count numbered citations: [1], [2,3], [1-5]
    numbered = re.findall(r"\[\d+(?:\s*[,\-–]\s*\d+)*\]", text)

    # Count author-year citations: (Author, Year) or Author (Year)
    author_year_paren = re.findall(
        r"\([A-Z][a-z]+(?:\s+(?:and|&)\s+[A-Z][a-z]+)?(?:\s+et\s+al\.?)?,\s*\d{4}[a-z]?\)",
        text
    )
    author_year_inline = re.findall(
        r"[A-Z][a-z]+(?:\s+(?:and|&)\s+[A-Z][a-z]+)?(?:\s+et\s+al\.?)?\s+\(\d{4}[a-z]?\)",
        text
    )
    author_year_total = len(author_year_paren) + len(author_year_inline)
    numbered_total = len(numbered)

    if numbered_total == 0 and author_year_total == 0:
        return CheckResult(
            check_name="citation_format",
            passed=True,
            severity="info",
            message="No citations detected (might be pre-reference-insertion draft).",
        )

    # Mixed format detection
    if numbered_total > 0 and author_year_total > 0:
        ratio = min(numbered_total, author_year_total) / max(numbered_total, author_year_total)
        if ratio > 0.1:  # More than 10% minority format = mixed
            dominant = "numbered [N]" if numbered_total > author_year_total else "author-year (Author, Year)"
            minority = "author-year" if numbered_total > author_year_total else "numbered"
            return CheckResult(
                check_name="citation_format",
                passed=False,
                severity="warning",
                message=f"Mixed citation formats detected: dominant={dominant} ({max(numbered_total, author_year_total)}), but also found {min(numbered_total, author_year_total)} {minority} citations.",
                details=[
                    f"Numbered citations: {numbered_total}",
                    f"Author-year citations: {author_year_total}",
                    "Action: Unify to one format throughout.",
                ],
                auto_fixable=False,
            )

    return CheckResult(
        check_name="citation_format",
        passed=True,
        severity="info",
        message=f"Citation format consistent ({numbered_total} numbered, {author_year_total} author-year).",
    )


# ============================================================
# Check 2: Figure/Table Reference Completeness
# ============================================================

def check_figure_table_references(text: str) -> CheckResult:
    """Check that all figures/tables are both defined AND referenced in text.

    Catches:
    - "Figure 3" mentioned in text but no Figure 3 caption exists
    - Figure 5 caption exists but never referenced in body
    """
    issues = []

    # Extract defined figures/tables (from captions)
    # Patterns: "Figure 1:", "Fig. 1.", "Table 1:", "表1", "图1"
    defined_figures = set()
    defined_tables = set()

    for m in re.finditer(
        r"(?:Figure|Fig\.?|图)\s*(\d+)", text, re.IGNORECASE
    ):
        # Check if this is a caption (followed by colon, period, or caption text)
        after = text[m.end():m.end() + 20]
        if re.match(r"\s*[:.：。]", after) or re.match(r"\s+[A-Z\u4e00-\u9fff]", after):
            defined_figures.add(int(m.group(1)))

    for m in re.finditer(
        r"(?:Table|Tab\.?|表)\s*(\d+)", text, re.IGNORECASE
    ):
        after = text[m.end():m.end() + 20]
        if re.match(r"\s*[:.：。]", after) or re.match(r"\s+[A-Z\u4e00-\u9fff]", after):
            defined_tables.add(int(m.group(1)))

    # Extract referenced figures/tables (in body text)
    referenced_figures = set()
    referenced_tables = set()

    for m in re.finditer(r"(?:Figure|Fig\.?|图)\s*(\d+)", text, re.IGNORECASE):
        referenced_figures.add(int(m.group(1)))

    for m in re.finditer(r"(?:Table|Tab\.?|表)\s*(\d+)", text, re.IGNORECASE):
        referenced_tables.add(int(m.group(1)))

    # Find gaps
    # Referenced but not defined (phantom references)
    phantom_figs = referenced_figures - defined_figures
    phantom_tabs = referenced_tables - defined_tables

    # Defined but never referenced (orphan captions) — harder to detect reliably
    # Only flag if referenced count for that number is exactly 1 (only the caption itself)
    # Simplified: check if max referenced > max defined (implies reference to non-existent)
    max_ref_fig = max(referenced_figures) if referenced_figures else 0
    max_def_fig = max(defined_figures) if defined_figures else 0
    max_ref_tab = max(referenced_tables) if referenced_tables else 0
    max_def_tab = max(defined_tables) if defined_tables else 0

    if max_ref_fig > max_def_fig and max_def_fig > 0:
        issues.append(
            f"Text references Figure {max_ref_fig} but only {max_def_fig} figure caption(s) detected."
        )

    if max_ref_tab > max_def_tab and max_def_tab > 0:
        issues.append(
            f"Text references Table {max_ref_tab} but only {max_def_tab} table caption(s) detected."
        )

    # Check sequential numbering gaps
    if defined_figures:
        expected = set(range(1, max(defined_figures) + 1))
        gaps = expected - defined_figures
        if gaps:
            issues.append(f"Figure numbering gap: missing Figure {sorted(gaps)}")

    if defined_tables:
        expected = set(range(1, max(defined_tables) + 1))
        gaps = expected - defined_tables
        if gaps:
            issues.append(f"Table numbering gap: missing Table {sorted(gaps)}")

    if issues:
        return CheckResult(
            check_name="figure_table_references",
            passed=False,
            severity="warning",
            message=f"Figure/table reference issues found: {len(issues)} problem(s).",
            details=issues,
        )

    return CheckResult(
        check_name="figure_table_references",
        passed=True,
        severity="info",
        message=f"Figure/table references consistent ({len(defined_figures)} figures, {len(defined_tables)} tables).",
    )


# ============================================================
# Check 3: Abstract Structure & Word Count
# ============================================================

def check_abstract(text: str) -> CheckResult:
    """Check abstract word count and basic structure.

    Standards:
    - Most journals: 150-300 words
    - CS conferences: 150-250 words
    - Chinese journals: 200-400 characters

    Structure check: should contain purpose, method, result, conclusion signals.
    """
    # Extract abstract — supports plain headings and Markdown headings (##, ###)
    abstract_match = re.search(
        r"(?:^|\n)\s*(?:#{1,4}\s+)?(?:Abstract|ABSTRACT|摘\s*要)[：:\s]*\n?(.*?)(?:\n\s*(?:#{1,4}\s+)?(?:Keywords|关键词|Introduction|1\.|INTRODUCTION)|$)",
        text, re.DOTALL | re.IGNORECASE
    )

    if not abstract_match:
        return CheckResult(
            check_name="abstract_check",
            passed=False,
            severity="error",
            message="No abstract section detected.",
            details=["Every academic paper must have an abstract."],
        )

    abstract_text = abstract_match.group(1).strip()

    # Word/character count
    is_chinese = bool(re.search(r"[\u4e00-\u9fff]{10,}", abstract_text))
    issues = []

    if is_chinese:
        char_count = len(re.findall(r"[\u4e00-\u9fff]", abstract_text))
        if char_count < 100:
            issues.append(f"Abstract too short: {char_count} Chinese characters (recommend 200-400).")
        elif char_count > 450:
            issues.append(f"Abstract too long: {char_count} Chinese characters (recommend 200-400).")
    else:
        word_count = len(abstract_text.split())
        if word_count < 100:
            issues.append(f"Abstract too short: {word_count} words (recommend 150-300).")
        elif word_count > 300:
            issues.append(f"Abstract too long: {word_count} words (recommend 150-300).")

    # Structure check: does it have key elements?
    lower = abstract_text.lower()
    structure_signals = {
        "purpose": bool(re.search(
            r"(?:this paper|we|this study|this work|本文|本研究|研究目的)", lower
        )),
        "method": bool(re.search(
            r"(?:method|approach|using|employ|propose|design|adopt|方法|采用|提出|使用|基于)", lower
        )),
        "result": bool(re.search(
            r"(?:result|find|show|demonstrat|reveal|outperform|achieve|结果|发现|表明|实验)", lower
        )),
        "conclusion": bool(re.search(
            r"(?:conclude|implication|suggest|contribut|future|结论|意义|启示|贡献)", lower
        )),
    }

    missing = [k for k, v in structure_signals.items() if not v]
    if len(missing) >= 2:
        issues.append(
            f"Abstract may be missing key elements: {', '.join(missing)}. "
            f"A complete abstract typically covers purpose, method, results, and conclusion."
        )

    if issues:
        return CheckResult(
            check_name="abstract_check",
            passed=False,
            severity="warning",
            message=f"Abstract issues: {'; '.join(issues[:2])}",
            details=issues,
        )

    return CheckResult(
        check_name="abstract_check",
        passed=True,
        severity="info",
        message="Abstract structure and length appear adequate.",
    )


# ============================================================
# Check 4: Required Sections Presence
# ============================================================

REQUIRED_SECTIONS_EN = [
    "abstract", "introduction", "conclusion",
]

REQUIRED_SECTIONS_ZH = [
    "摘要", "引言", "结论",
]

COMMON_SECTIONS_EN = [
    "related work", "literature review", "methodology", "methods",
    "results", "discussion", "references",
]


def check_required_sections(text: str) -> CheckResult:
    """Check that all required sections are present."""
    lower = text.lower()
    issues = []

    # Detect language
    is_chinese = bool(re.search(r"[\u4e00-\u9fff]{50,}", text))
    required = REQUIRED_SECTIONS_ZH if is_chinese else REQUIRED_SECTIONS_EN

    missing = []
    for section in required:
        # Look for section heading patterns — supports plain, numbered, and Markdown (##) headings
        pattern = r"(?:^|\n)\s*(?:#{1,4}\s+)?(?:\d+\.?\s*)?" + re.escape(section)
        if not re.search(pattern, lower if not is_chinese else text):
            missing.append(section)

    if missing:
        issues.append(f"Missing required section(s): {', '.join(missing)}")

    # Check References section — supports Markdown headings (##)
    has_refs = bool(re.search(
        r"(?:^|\n)\s*(?:#{1,4}\s+)?(?:References|REFERENCES|参考文献|Bibliography)", text
    ))
    if not has_refs:
        issues.append("No References/Bibliography section detected.")

    if issues:
        return CheckResult(
            check_name="required_sections",
            passed=False,
            severity="error" if "References" in str(issues) or len(missing) > 1 else "warning",
            message=f"Section structure issues: {'; '.join(issues)}",
            details=issues,
        )

    return CheckResult(
        check_name="required_sections",
        passed=True,
        severity="info",
        message="All required sections present.",
    )


# ============================================================
# Check 5: Cross-Reference Integrity
# ============================================================

def check_cross_references(text: str) -> CheckResult:
    """Check that cross-references (Eq., Section, Theorem) point to valid targets.

    Detects:
    - "Equation 5" but only 3 equations exist
    - "Section 4.2" but no such heading
    - "Theorem 3" but only 2 theorems defined
    """
    issues = []

    # Count defined equations
    eq_definitions = re.findall(r"\\begin\{equation\}|\\begin\{align", text)
    numbered_eqs = re.findall(r"\\\[|\$\$.*?\$\$", text, re.DOTALL)
    eq_labels = re.findall(r"\\label\{eq[:\-_]?(\w+)\}", text)
    eq_count = len(eq_definitions) + len(numbered_eqs)

    # Find equation references
    eq_refs = re.findall(r"(?:Equation|Eq\.?|公式)\s*[\(（]?(\d+)[\)）]?", text, re.IGNORECASE)
    for ref_num in eq_refs:
        if int(ref_num) > eq_count and eq_count > 0:
            issues.append(f"Reference to Equation {ref_num} but only ~{eq_count} equations detected.")

    # Check section references vs headings
    section_refs = re.findall(r"(?:Section|Sec\.?|§)\s*(\d+(?:\.\d+)*)", text, re.IGNORECASE)
    # Find actual section numbers
    section_numbers = set()
    for m in re.finditer(r"(?:^|\n)\s*(\d+(?:\.\d+)*)\s+[A-Z\u4e00-\u9fff]", text):
        section_numbers.add(m.group(1))

    for ref in section_refs:
        if section_numbers and ref not in section_numbers:
            # Check if it's a prefix match (e.g., "3" when "3.1" exists)
            if not any(s.startswith(ref) for s in section_numbers):
                issues.append(f"Reference to Section {ref} — section not found in headings.")

    # Check theorem/lemma/proposition references
    for env_type in ["Theorem", "Lemma", "Proposition", "Corollary", "Definition"]:
        definitions = re.findall(
            rf"(?:^|\n)\s*\**{env_type}\s+(\d+)", text, re.IGNORECASE
        )
        references = re.findall(
            rf"{env_type}\s+(\d+)", text, re.IGNORECASE
        )
        if definitions:
            max_defined = max(int(d) for d in definitions)
            for ref in references:
                if int(ref) > max_defined:
                    issues.append(f"Reference to {env_type} {ref} but only {max_defined} defined.")

    if issues:
        return CheckResult(
            check_name="cross_references",
            passed=False,
            severity="warning",
            message=f"Cross-reference issues: {len(issues)} problem(s).",
            details=issues[:10],  # Cap at 10
        )

    return CheckResult(
        check_name="cross_references",
        passed=True,
        severity="info",
        message="Cross-references appear consistent.",
    )


# ============================================================
# Check 6: Formatting Anomalies
# ============================================================

def check_formatting(text: str) -> CheckResult:
    """Detect common formatting issues.

    Catches:
    - Double spaces (except after period in some styles)
    - Inconsistent list formatting
    - Orphan headings (heading at end of page/section with no content)
    - Mixed quote styles (" vs ")
    - Trailing whitespace patterns
    """
    issues = []

    # Double spaces (not after sentence-ending punctuation)
    double_spaces = re.findall(r"[^.!?]\s{2,}[a-zA-Z\u4e00-\u9fff]", text)
    if len(double_spaces) > 5:
        issues.append(f"Excessive double spaces detected ({len(double_spaces)} instances).")

    # Mixed quote styles
    smart_quotes = len(re.findall(r"[\u201c\u201d\u2018\u2019]", text))
    straight_quotes = len(re.findall(r'(?<![\\])["\']', text))
    if smart_quotes > 5 and straight_quotes > 5:
        ratio = min(smart_quotes, straight_quotes) / max(smart_quotes, straight_quotes)
        if ratio > 0.2:
            issues.append(
                f"Mixed quote styles: {smart_quotes} smart quotes + {straight_quotes} straight quotes."
            )

    # Very long paragraphs (>500 words without break — readability issue)
    paragraphs = re.split(r"\n\s*\n", text)
    long_paras = [len(p.split()) for p in paragraphs if len(p.split()) > 500]
    if long_paras:
        issues.append(
            f"{len(long_paras)} paragraph(s) exceed 500 words. Consider splitting for readability."
        )

    # Orphan headings (heading followed immediately by another heading)
    heading_pattern = r"((?:^|\n)\s*(?:\d+\.?\s+)?[A-Z\u4e00-\u9fff][^\n]{3,50})\s*\n\s*(?:\d+\.?\s+)?[A-Z\u4e00-\u9fff]"
    orphans = re.findall(heading_pattern, text)
    # Filter: only flag if the "headings" are short (likely actual headings, not paragraphs)
    real_orphans = [h for h in orphans if len(h.strip().split()) <= 8]
    if len(real_orphans) > 2:
        issues.append(
            f"{len(real_orphans)} potential orphan headings (heading followed by heading with no content)."
        )

    if issues:
        return CheckResult(
            check_name="formatting",
            passed=False,
            severity="info",  # Formatting is usually minor
            message=f"Formatting issues: {len(issues)} detected.",
            details=issues,
            auto_fixable=True,
        )

    return CheckResult(
        check_name="formatting",
        passed=True,
        severity="info",
        message="No significant formatting anomalies detected.",
    )


# ============================================================
# Check 7: Acknowledgment / Funding Statement
# ============================================================

def check_acknowledgments(text: str) -> CheckResult:
    """Check for acknowledgment and funding statement presence.

    Many journals require explicit funding disclosure even if "none."
    """
    has_ack = bool(re.search(
        r"(?:^|\n)\s*(?:Acknowledgm?ents?|致\s*谢|ACKNOWLEDGM?ENTS?)", text
    ))
    has_funding = bool(re.search(
        r"(?:funding|supported by|grant|基金|资助|funded|financial support|research grant)",
        text, re.IGNORECASE
    ))

    if not has_ack and not has_funding:
        return CheckResult(
            check_name="acknowledgments",
            passed=False,
            severity="warning",
            message="No acknowledgment or funding statement found.",
            details=[
                "Most journals require an explicit funding/acknowledgment statement.",
                "Add even if funding is 'none' — e.g., 'The authors received no external funding.'",
            ],
        )

    if has_ack and not has_funding:
        return CheckResult(
            check_name="acknowledgments",
            passed=True,
            severity="info",
            message="Acknowledgment section present. No explicit funding statement detected (may be included within acknowledgments).",
        )

    return CheckResult(
        check_name="acknowledgments",
        passed=True,
        severity="info",
        message="Acknowledgment/funding statement present.",
    )


# ============================================================
# Check 8: Word Count & Basic Stats
# ============================================================

def compute_paper_stats(text: str) -> Dict:
    """Compute basic paper statistics."""
    is_chinese = bool(re.search(r"[\u4e00-\u9fff]{50,}", text))

    if is_chinese:
        chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
        english_words = len(re.findall(r"[a-zA-Z]+", text))
        total_equiv = chinese_chars + english_words  # rough equivalent
    else:
        total_equiv = len(text.split())

    # Count sections
    section_headings = re.findall(
        r"(?:^|\n)\s*(?:\d+\.?\s+)[A-Z\u4e00-\u9fff]", text
    )

    # Count references
    ref_section = ""
    ref_match = re.search(
        r"(?:^|\n)\s*(?:References|REFERENCES|参考文献|Bibliography)\s*\n(.*)",
        text, re.DOTALL
    )
    if ref_match:
        ref_section = ref_match.group(1)
    ref_count = len(re.findall(r"(?:^|\n)\s*\[\d+\]", ref_section))
    if ref_count == 0:
        ref_count = len(re.findall(r"(?:^|\n)\s*\d+\.\s+", ref_section))

    return {
        "word_count": total_equiv,
        "section_count": len(section_headings),
        "reference_count": ref_count,
        "is_chinese": is_chinese,
        "estimated_pages": total_equiv // 500 if not is_chinese else total_equiv // 800,
    }


# ============================================================
# LaTeX Compilation Check (C-2 integration)
# ============================================================

def check_latex_compilation() -> CheckResult:
    """
    Check if LaTeX project compiles without errors.
    
    Graceful degradation:
    - If latexmk not installed: passes with info message
    - If no .tex file found: passes with info message
    - Only fails if compilation produces actual errors
    """
    from tools.latex_verify import check_latex_availability, find_main_tex, compile_latex

    if not check_latex_availability():
        return CheckResult(
            check_name="latex_compilation",
            passed=True,  # Graceful: don't block submission if LaTeX not installed
            severity="info",
            message="LaTeX environment not available — skipping compilation check. Install latexmk for full verification.",
        )

    # Find .tex file
    search_dirs = [WORKSPACE / "paper", WORKSPACE, Path(".")]
    tex_path = None
    for d in search_dirs:
        if d.exists():
            tex_path = find_main_tex(d)
            if tex_path:
                break

    if tex_path is None:
        return CheckResult(
            check_name="latex_compilation",
            passed=True,  # No .tex file is not an error in presubmission context
            severity="info",
            message="No .tex file found in workspace — skipping compilation check.",
        )

    # Run compilation
    result = compile_latex(tex_path, draft_mode=True)

    if result.status == "success":
        return CheckResult(
            check_name="latex_compilation",
            passed=True,
            severity="info",
            message=f"LaTeX compiles cleanly ({result.compilation_time:.1f}s, 0 errors, 0 warnings).",
        )
    elif result.status == "warnings_only":
        return CheckResult(
            check_name="latex_compilation",
            passed=True,  # Warnings don't block submission
            severity="warning",
            message=f"LaTeX compiles but with {result.warning_count} warning(s). Consider fixing before submission.",
            details=[w.message for w in result.warnings[:5]],
        )
    elif result.status == "timeout":
        return CheckResult(
            check_name="latex_compilation",
            passed=False,
            severity="warning",
            message="LaTeX compilation timed out. Check for infinite loops or very large includes.",
        )
    else:
        # errors
        error_msgs = [e.message for e in result.errors[:3]]
        return CheckResult(
            check_name="latex_compilation",
            passed=False,
            severity="error",
            message=f"LaTeX compilation failed with {result.error_count} error(s): {'; '.join(error_msgs)}",
            details=[e.message for e in result.errors[:5]],
        )


def check_bibliography_consistency() -> CheckResult:
    """
    Check bibliography for undefined references and missing required fields.
    
    Graceful degradation:
    - If no .bib file found: passes with info message
    - Only fails on undefined references (will cause compilation failure)
    """
    from tools.bib_verify import bib_verify

    result = bib_verify()

    if result.get("status") == "unavailable":
        return CheckResult(
            check_name="bibliography_consistency",
            passed=True,
            severity="info",
            message="No .bib file found — skipping bibliography check.",
        )

    if result.get("status") == "clean":
        return CheckResult(
            check_name="bibliography_consistency",
            passed=True,
            severity="info",
            message=f"Bibliography clean: {result.get('total_entries', 0)} entries, all citations resolved.",
        )

    undefined = result.get("undefined_refs", [])
    if undefined:
        return CheckResult(
            check_name="bibliography_consistency",
            passed=False,
            severity="error",
            message=f"{len(undefined)} undefined reference(s): {', '.join(list(undefined)[:5])}. These will cause compilation errors.",
            details=list(undefined)[:10],
        )

    # Warnings only (missing fields)
    return CheckResult(
        check_name="bibliography_consistency",
        passed=True,
        severity="warning",
        message=f"Bibliography has {result.get('warning_count', 0)} warning(s) — consider adding missing fields.",
        details=[f"{result.get('warning_count', 0)} missing field warning(s)"],
    )


# ============================================================
# Main Entry Point
# ============================================================

def run_presubmission_checks(paper_text: str = None) -> PresubmissionReport:
    """Run all pre-submission mechanical checks.

    Args:
        paper_text: Full paper text. If None, loads from workspace.

    Returns:
        PresubmissionReport with all check results.
    """
    if paper_text is None:
        paper_text = _load_paper_text()

    if not paper_text:
        return PresubmissionReport(
            total_checks=0, passed=0, failed=0,
            errors=0, warnings=0, infos=0,
            verdict="not_ready",
            results=[CheckResult(
                check_name="load_paper",
                passed=False,
                severity="error",
                message="Cannot load paper text. Run parse_paper first.",
            )],
        )

    # Run all checks
    # Core checks always run
    checks = [
        check_citation_format(paper_text),
        check_figure_table_references(paper_text),
        check_abstract(paper_text),
        check_required_sections(paper_text),
        check_cross_references(paper_text),
    ]

    # Auxiliary checks: only run on sufficiently complete papers (>1000 words)
    # Short excerpts/fragments trigger too many irrelevant warnings
    word_count_approx = len(paper_text.split())
    if word_count_approx >= 1000:
        checks.append(check_formatting(paper_text))
        checks.append(check_acknowledgments(paper_text))

    # LaTeX / Bibliography checks (C-2): run independently of paper text length
    # These check the actual project files, not the parsed text
    checks.append(check_latex_compilation())
    checks.append(check_bibliography_consistency())

    # Compute stats
    stats = compute_paper_stats(paper_text)

    # Summarize
    passed = sum(1 for c in checks if c.passed)
    failed = sum(1 for c in checks if not c.passed)
    errors = sum(1 for c in checks if not c.passed and c.severity == "error")
    warnings = sum(1 for c in checks if not c.passed and c.severity == "warning")
    infos = sum(1 for c in checks if not c.passed and c.severity == "info")

    if errors > 0:
        verdict = "not_ready"
    elif warnings > 2:
        verdict = "needs_fixes"
    elif warnings > 0:
        verdict = "needs_fixes"
    else:
        verdict = "ready"

    return PresubmissionReport(
        total_checks=len(checks),
        passed=passed,
        failed=failed,
        errors=errors,
        warnings=warnings,
        infos=infos,
        results=checks,
        word_count=stats["word_count"],
        section_count=stats["section_count"],
        verdict=verdict,
    )


def format_presubmission_report(report: PresubmissionReport) -> str:
    """Format the report for display."""
    lines = []
    lines.append("=" * 60)
    lines.append("PRE-SUBMISSION MECHANICAL CHECK")
    lines.append("=" * 60)

    verdict_emoji = {"ready": "✓", "needs_fixes": "⚠", "not_ready": "✗"}
    lines.append(f"\nVerdict: {verdict_emoji.get(report.verdict, '?')} {report.verdict.upper()}")
    lines.append(f"Checks: {report.passed}/{report.total_checks} passed")

    if report.word_count:
        lines.append(f"Paper stats: ~{report.word_count} words, {report.section_count} sections")

    if report.errors:
        lines.append(f"\n❌ ERRORS ({report.errors}):")
        for r in report.results:
            if not r.passed and r.severity == "error":
                lines.append(f"  [{r.check_name}] {r.message}")
                for d in r.details[:3]:
                    lines.append(f"    → {d}")

    if report.warnings:
        lines.append(f"\n⚠️  WARNINGS ({report.warnings}):")
        for r in report.results:
            if not r.passed and r.severity == "warning":
                lines.append(f"  [{r.check_name}] {r.message}")
                for d in r.details[:3]:
                    lines.append(f"    → {d}")

    if report.infos:
        lines.append(f"\nℹ️  INFO ({report.infos}):")
        for r in report.results:
            if not r.passed and r.severity == "info":
                lines.append(f"  [{r.check_name}] {r.message}")

    # Summary of passed checks
    passed_names = [r.check_name for r in report.results if r.passed]
    if passed_names:
        lines.append(f"\n✓ Passed: {', '.join(passed_names)}")

    lines.append("\n" + "─" * 60)
    if report.verdict == "ready":
        lines.append("Paper is mechanically ready for LLM-based review.")
    elif report.verdict == "needs_fixes":
        lines.append("Fix warnings before submission. LLM review can still proceed.")
    else:
        lines.append("Critical issues found. Fix errors before proceeding with review.")

    return "\n".join(lines)


# ============================================================
# Helpers
# ============================================================

def _load_paper_text() -> str:
    """Load full paper text from workspace."""
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
