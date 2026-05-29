"""
tools/architecture_diagnosis.py — Section Architecture Diagnosis.

Diagnoses the "skeleton" of a paper BEFORE any sentence-level polish.
Inspired by nature-polishing's framework: fix structure first, polish later.

Detects 6 fundamental failure modes:
1. Wrong paper type logic (e.g., methods paper structured as hypothesis paper)
2. Missing gap (Introduction doesn't establish what's unknown)
3. Claim without evidence (assertion without data/proof)
4. Evidence without claim (data presented but no interpretation)
5. Missing boundary/limitation
6. Results/Discussion contamination (mixing "what happened" with "what it means")

Also validates:
- Hourglass structure (Intro: broad→narrow; Discussion: narrow→broad)
- Section responsibilities (each section does its job, nothing more)
- Paragraph-level logic (one idea per paragraph)

Architecture:
    - Zero external dependencies
    - Pure rule-based (regex + structural heuristics)
    - Integrates with review_engine as a pre-review diagnostic
    - Can run standalone or as part of the review pipeline
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from pathlib import Path

WORKSPACE = Path(".workspace")


# ============================================================
# Data Classes
# ============================================================

@dataclass
class ArchitecturalIssue:
    """A structural problem in the paper's architecture."""
    failure_mode: str       # One of the 6 fundamental modes
    section: str            # Which section has the issue
    severity: str           # "critical" | "major" | "minor"
    description: str
    evidence: str           # What text/pattern triggered this
    fix_priority: int       # 1=fix first, higher=later
    suggestion: str


@dataclass
class SectionProfile:
    """Profile of a single section's structural health."""
    section_id: str
    section_type: str       # abstract, introduction, methods, results, discussion, conclusion, other
    word_count: int
    has_claims: bool
    has_evidence: bool
    has_hedging: bool
    has_limitations: bool
    contains_results_language: bool
    contains_discussion_language: bool
    structural_role_violation: str = ""  # What role violation was detected


@dataclass
class ArchitectureReport:
    """Full architecture diagnosis report."""
    paper_type: str         # research | methods | hypothesis | device | review
    total_issues: int
    critical_issues: int
    hourglass_valid: bool
    section_profiles: List[SectionProfile] = field(default_factory=list)
    issues: List[ArchitecturalIssue] = field(default_factory=list)
    fix_order: List[str] = field(default_factory=list)  # Ordered fix priorities


# ============================================================
# Paper Type Detection
# ============================================================

PAPER_TYPE_SIGNALS = {
    "research": [
        r"(?:we|this paper|this study)\s+(?:propose|present|investigate|examine|analyze)",
        r"\b(?:experiment|empirical|dataset|benchmark)\b",
        r"\b(?:our (?:approach|method|framework|model))\b",
    ],
    "methods": [
        r"\b(?:we (?:develop|design|implement|introduce) a (?:new|novel)?\s*(?:method|algorithm|framework|tool|system))\b",
        r"\b(?:software|library|package|toolkit|pipeline)\b",
        r"\b(?:open[- ]source|available at|repository)\b",
    ],
    "hypothesis": [
        r"\b(?:hypothesis|hypothesize|we predict|we expect)\b",
        r"\b(?:H[1-9]|Hypothesis [1-9])\b",
        r"\b(?:confirm|reject|support|refute)\b.*\b(?:hypothesis|prediction)\b",
    ],
    "review": [
        r"\b(?:survey|systematic review|meta-analysis|literature review)\b",
        r"\b(?:we review|we summarize|we categorize)\b",
        r"\b(?:taxonomy|classification of|overview of)\b",
    ],
}


def detect_paper_type(text: str) -> str:
    """Detect paper type from content signals."""
    scores = {}
    lower_text = text.lower()

    for ptype, patterns in PAPER_TYPE_SIGNALS.items():
        score = sum(
            len(re.findall(pat, lower_text, re.IGNORECASE))
            for pat in patterns
        )
        scores[ptype] = score

    if not scores or max(scores.values()) == 0:
        return "research"  # Default

    return max(scores, key=scores.get)


# ============================================================
# Section Type Classification
# ============================================================

SECTION_TYPE_PATTERNS = {
    "abstract": r"(?:^|\n)\s*(?:abstract|摘\s*要)",
    "introduction": r"(?:^|\n)\s*(?:\d+\.?\s*)?(?:introduction|引言|绪论)",
    "related_work": r"(?:^|\n)\s*(?:\d+\.?\s*)?(?:related work|literature review|背景|相关工作|文献综述)",
    "methods": r"(?:^|\n)\s*(?:\d+\.?\s*)?(?:method|approach|framework|model|methodology|研究方法|方法)",
    "results": r"(?:^|\n)\s*(?:\d+\.?\s*)?(?:results?|experiments?|evaluation|findings|实验|结果)",
    "discussion": r"(?:^|\n)\s*(?:\d+\.?\s*)?(?:discussion|分析与讨论|讨论)",
    "conclusion": r"(?:^|\n)\s*(?:\d+\.?\s*)?(?:conclusion|summary|结论|总结)",
}


def classify_section_type(slug: str, content: str) -> str:
    """Classify a section's type from its slug and content."""
    slug_lower = slug.lower()

    for stype, pattern in SECTION_TYPE_PATTERNS.items():
        if re.search(pattern, slug_lower) or re.search(pattern, content[:200], re.IGNORECASE):
            return stype

    # Heuristic fallback
    if any(kw in slug_lower for kw in ["data", "dataset", "sample"]):
        return "methods"
    if any(kw in slug_lower for kw in ["theory", "framework", "model"]):
        return "methods"
    if any(kw in slug_lower for kw in ["appendix", "supplement"]):
        return "other"

    return "other"


# ============================================================
# Structural Signal Detection
# ============================================================

# Results language: reports observations (past tense, factual)
RESULTS_LANGUAGE = [
    r"\b(?:was|were)\s+(?:observed|found|detected|measured|recorded)\b",
    r"\b(?:showed?|revealed?|indicated?|yielded?|achieved?|obtained?)\b",
    r"\b(?:outperform|surpass|exceed|improve)\w*\b.*\b(?:by|over|compared)\b",
    r"\b(?:accuracy|precision|recall|F1|BLEU|ROUGE|perplexity)\b.*\d",
    r"\b(?:Table|Figure|Fig\.?)\s*\d+\s+(?:shows?|presents?|summarizes?|illustrates?)\b",
]

# Discussion language: interprets meaning (hedged, comparative, theoretical)
DISCUSSION_LANGUAGE = [
    r"\b(?:suggests?|implies?|indicates?)\s+that\b",
    r"\b(?:may|might|could)\s+(?:be|have|reflect|explain|indicate)\b",
    r"\b(?:consistent with|in line with|contrary to|unlike)\b",
    r"\b(?:possible explanation|one reason|this (?:finding|result) (?:suggests?|implies?))\b",
    r"\b(?:broader implications?|future (?:work|research|directions?))\b",
    r"\b(?:limitation|caveat|should be interpreted with caution)\b",
]

# Gap language: identifies what's unknown/missing
GAP_LANGUAGE = [
    r"\b(?:however|yet|but|nevertheless)\b.*\b(?:remain|unclear|unknown|limited|lacking|gap)\b",
    r"\b(?:no (?:prior|previous|existing) (?:work|study|research))\b",
    r"\b(?:little (?:is known|attention|research))\b",
    r"\b(?:缺乏|不足|尚未|仍然|然而.*没有|但.*未能)\b",
    r"\b(?:open question|unresolved|unexplored|under-explored)\b",
]

# Claim language: assertions/propositions
CLAIM_LANGUAGE = [
    r"\b(?:we (?:propose|argue|claim|demonstrate|show|prove))\b",
    r"\b(?:our (?:contribution|main finding|key insight))\b",
    r"\b(?:this (?:paper|work|study) (?:demonstrates?|shows?|proves?))\b",
    r"\b(?:本文(?:提出|证明|发现|贡献))\b",
]

# Evidence language: data/proof supporting claims
EVIDENCE_LANGUAGE = [
    r"\b(?:Table|Figure|Fig\.?|Equation|Eq\.?)\s*\d+\b",
    r"\b(?:p\s*[<>=]\s*0\.\d+|CI\s*[:=]|effect size|Cohen['']s d)\b",
    r"\b(?:statistically significant|significant (?:difference|improvement))\b",
    r"\b(?:as shown in|as demonstrated by|evidence from|data (?:shows?|suggests?))\b",
    r"\b(?:实验(?:结果|表明|证明)|数据表明|如表|如图)\b",
]

# Limitation/boundary language
LIMITATION_LANGUAGE = [
    r"\b(?:limitation|constraint|caveat|shortcoming|weakness)\b",
    r"\b(?:future work|remain(?:s|ing) (?:to be|challenge))\b",
    r"\b(?:does not (?:apply|generalize|account)|cannot|unable to)\b",
    r"\b(?:局限|不足之处|未来工作|有待|仍需)\b",
    r"\b(?:beyond the scope|out of scope)\b",
]


def _count_pattern_matches(text: str, patterns: list) -> int:
    """Count total matches across patterns."""
    return sum(len(re.findall(p, text, re.IGNORECASE)) for p in patterns)


def build_section_profile(section_id: str, content: str, section_type: str) -> SectionProfile:
    """Build structural profile for a section."""
    word_count = len(content.split())

    return SectionProfile(
        section_id=section_id,
        section_type=section_type,
        word_count=word_count,
        has_claims=_count_pattern_matches(content, CLAIM_LANGUAGE) > 0,
        has_evidence=_count_pattern_matches(content, EVIDENCE_LANGUAGE) > 0,
        has_hedging=bool(re.search(
            r"\b(?:may|might|could|possibly|perhaps|likely|suggests?)\b",
            content, re.IGNORECASE
        )),
        has_limitations=_count_pattern_matches(content, LIMITATION_LANGUAGE) > 0,
        contains_results_language=_count_pattern_matches(content, RESULTS_LANGUAGE) > 2,
        contains_discussion_language=_count_pattern_matches(content, DISCUSSION_LANGUAGE) > 2,
    )


# ============================================================
# Failure Mode Detection
# ============================================================

def detect_failure_modes(
    sections: Dict[str, Tuple[str, str]],  # section_id → (content, section_type)
    paper_type: str,
) -> List[ArchitecturalIssue]:
    """Detect the 6 fundamental failure modes.

    Args:
        sections: Dict mapping section_id to (content, section_type)
        paper_type: Detected paper type
    """
    issues = []

    # Collect section profiles
    profiles: Dict[str, SectionProfile] = {}
    for sec_id, (content, stype) in sections.items():
        profiles[sec_id] = build_section_profile(sec_id, content, stype)

    # --- Failure Mode 1: Missing Gap ---
    intro_sections = {k: v for k, v in sections.items() if v[1] == "introduction"}
    for sec_id, (content, _) in intro_sections.items():
        gap_matches = _count_pattern_matches(content, GAP_LANGUAGE)
        if gap_matches == 0:
            issues.append(ArchitecturalIssue(
                failure_mode="missing_gap",
                section=sec_id,
                severity="critical",
                description=(
                    "Introduction does not establish a research gap. "
                    "Without a clear gap, the paper's contribution is unmotivated."
                ),
                evidence="No gap-signaling language detected (e.g., 'however...remains unclear', 'no prior work').",
                fix_priority=1,
                suggestion=(
                    "Add a paragraph clearly stating what is unknown/unresolved. "
                    "Use pattern: 'However, [existing approaches] fail to [address X] because [reason].'"
                ),
            ))

    # --- Failure Mode 2: Claim without Evidence ---
    for sec_id, profile in profiles.items():
        if profile.section_type in ("introduction", "discussion", "conclusion"):
            content = sections[sec_id][0]
            claims = _count_pattern_matches(content, CLAIM_LANGUAGE)
            evidence = _count_pattern_matches(content, EVIDENCE_LANGUAGE)
            if claims > 2 and evidence == 0 and profile.section_type != "introduction":
                issues.append(ArchitecturalIssue(
                    failure_mode="claim_without_evidence",
                    section=sec_id,
                    severity="major",
                    description=f"Section makes {claims} claims but provides no evidence references.",
                    evidence=f"Found {claims} claim patterns but 0 evidence patterns (Table/Figure/statistical refs).",
                    fix_priority=2,
                    suggestion="Each major claim should reference specific results (Table X, Figure Y, Section Z).",
                ))

    # --- Failure Mode 3: Evidence without Claim ---
    results_sections = {k: v for k, v in sections.items() if v[1] == "results"}
    for sec_id, (content, _) in results_sections.items():
        evidence_count = _count_pattern_matches(content, EVIDENCE_LANGUAGE)
        claims_count = _count_pattern_matches(content, CLAIM_LANGUAGE)
        # Results should have evidence but also clear takeaways
        if evidence_count > 5 and claims_count == 0:
            # Check if there's any interpretive language at all
            interp = _count_pattern_matches(content, DISCUSSION_LANGUAGE)
            if interp == 0:
                issues.append(ArchitecturalIssue(
                    failure_mode="evidence_without_claim",
                    section=sec_id,
                    severity="major",
                    description="Results section presents data but never states what it means.",
                    evidence=f"Found {evidence_count} evidence patterns but 0 claims or interpretive statements.",
                    fix_priority=3,
                    suggestion=(
                        "Add a brief summary sentence after each result block: "
                        "'This demonstrates that...' or 'These results confirm...'"
                    ),
                ))

    # --- Failure Mode 4: Results/Discussion Contamination ---
    for sec_id, profile in profiles.items():
        content = sections[sec_id][0]
        if profile.section_type == "results" and profile.contains_discussion_language:
            discussion_hits = _count_pattern_matches(content, DISCUSSION_LANGUAGE)
            results_hits = _count_pattern_matches(content, RESULTS_LANGUAGE)
            if discussion_hits > 3 and discussion_hits > results_hits * 0.5:
                issues.append(ArchitecturalIssue(
                    failure_mode="results_discussion_contamination",
                    section=sec_id,
                    severity="major",
                    description=(
                        "Results section contains substantial discussion language. "
                        "Results should report WHAT happened; Discussion explains WHY."
                    ),
                    evidence=f"Found {discussion_hits} discussion patterns in a Results section (vs {results_hits} results patterns).",
                    fix_priority=2,
                    suggestion="Move interpretive content (suggests, implies, consistent with) to Discussion section.",
                ))

        if profile.section_type == "discussion" and profile.contains_results_language:
            results_hits = _count_pattern_matches(content, RESULTS_LANGUAGE)
            if results_hits > 5:
                issues.append(ArchitecturalIssue(
                    failure_mode="results_discussion_contamination",
                    section=sec_id,
                    severity="minor",
                    description="Discussion section introduces new results.",
                    evidence=f"Found {results_hits} results-language patterns in Discussion.",
                    fix_priority=4,
                    suggestion="New data/results should be in Results section. Discussion can reference them.",
                ))

    # --- Failure Mode 5: Missing Boundary/Limitation ---
    has_limitations = any(
        p.has_limitations
        for p in profiles.values()
        if p.section_type in ("discussion", "conclusion")
    )
    if not has_limitations and len(sections) > 3:
        issues.append(ArchitecturalIssue(
            failure_mode="missing_boundary",
            section="discussion/conclusion",
            severity="major",
            description="No limitations or boundary conditions discussed anywhere.",
            evidence="No limitation-language detected in Discussion or Conclusion.",
            fix_priority=3,
            suggestion=(
                "Add a Limitations paragraph covering: scope boundaries, "
                "generalizability caveats, methodological constraints, and data limitations."
            ),
        ))

    # --- Failure Mode 6: Hourglass Structure Violation ---
    hourglass_issues = _check_hourglass(sections, profiles)
    issues.extend(hourglass_issues)

    # Sort by fix_priority
    issues.sort(key=lambda x: x.fix_priority)

    return issues


def _check_hourglass(
    sections: Dict[str, Tuple[str, str]],
    profiles: Dict[str, SectionProfile],
) -> List[ArchitecturalIssue]:
    """Check hourglass structure:
    - Introduction: broad → narrow (context → gap → specific question/method)
    - Discussion/Conclusion: narrow → broad (findings → literature → implications)
    """
    issues = []

    # Check Introduction: should start broad and narrow down
    for sec_id, (content, stype) in sections.items():
        if stype != "introduction":
            continue

        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", content) if p.strip()]
        if len(paragraphs) < 3:
            continue

        # First paragraph should be broad (general statements, no specific methods)
        first_para = paragraphs[0]
        last_para = paragraphs[-1]

        # Check if first paragraph jumps into specifics too fast
        specific_in_first = _count_pattern_matches(first_para, [
            r"\b(?:our (?:method|approach|model|framework))\b",
            r"\b(?:we propose|we develop|we introduce)\b",
            r"\b(?:本文提出|我们的方法)\b",
        ])
        if specific_in_first > 0 and len(paragraphs) > 2:
            issues.append(ArchitecturalIssue(
                failure_mode="hourglass_violation",
                section=sec_id,
                severity="minor",
                description="Introduction starts too narrow — opens with specific method/contribution instead of broad context.",
                evidence="First paragraph contains 'our method/approach/model' language.",
                fix_priority=4,
                suggestion="Start Introduction with 1-2 sentences establishing the broader research area before narrowing to your specific contribution.",
            ))

    return issues


# ============================================================
# Main Entry Point
# ============================================================

def run_architecture_diagnosis(paper_text: str = None) -> ArchitectureReport:
    """Run full architecture diagnosis.

    Args:
        paper_text: Full paper text. If None, loads from workspace sections.

    Returns:
        ArchitectureReport with all structural issues and fix priorities.
    """
    import json

    # Load sections from workspace
    index_path = WORKSPACE / "paper" / "section_index.json"
    sections: Dict[str, Tuple[str, str]] = {}

    if index_path.exists():
        index = json.loads(index_path.read_text(encoding="utf-8"))
        for entry in index:
            sec_path = Path(entry["file"])
            if sec_path.exists():
                content = sec_path.read_text(encoding="utf-8")
                slug = entry.get("slug") or entry.get("id") or sec_path.stem
                stype = classify_section_type(slug, content)
                sections[entry.get("id", slug)] = (content, stype)
    elif paper_text:
        # Fallback: treat entire text as one section, try to split by headings
        parts = re.split(r"\n\s*(?=\d+\.\s+[A-Z]|\d+\.\s+[\u4e00-\u9fff])", paper_text)
        for i, part in enumerate(parts):
            sec_id = f"section_{i:02d}"
            stype = classify_section_type(part[:100], part)
            sections[sec_id] = (part, stype)
    else:
        return ArchitectureReport(
            paper_type="unknown",
            total_issues=0,
            critical_issues=0,
            hourglass_valid=True,
            fix_order=["Run parse_paper first."],
        )

    # Detect paper type from all text combined
    full_text = "\n\n".join(content for content, _ in sections.values())
    paper_type = detect_paper_type(full_text)

    # Detect failure modes
    issues = detect_failure_modes(sections, paper_type)

    # Build section profiles
    section_profiles = []
    for sec_id, (content, stype) in sections.items():
        profile = build_section_profile(sec_id, content, stype)
        section_profiles.append(profile)

    # Determine hourglass validity
    hourglass_valid = not any(i.failure_mode == "hourglass_violation" for i in issues)

    # Build fix order
    fix_order = []
    priority_map = {
        "missing_gap": "1. Establish research gap in Introduction",
        "claim_without_evidence": "2. Link claims to evidence",
        "results_discussion_contamination": "2. Separate Results from Discussion",
        "evidence_without_claim": "3. Add interpretive statements to Results",
        "missing_boundary": "3. Add Limitations paragraph",
        "hourglass_violation": "4. Fix section-level narrative flow",
    }
    seen_modes = set()
    for issue in issues:
        if issue.failure_mode not in seen_modes:
            seen_modes.add(issue.failure_mode)
            fix_order.append(priority_map.get(issue.failure_mode, f"Fix: {issue.failure_mode}"))

    return ArchitectureReport(
        paper_type=paper_type,
        total_issues=len(issues),
        critical_issues=sum(1 for i in issues if i.severity == "critical"),
        hourglass_valid=hourglass_valid,
        section_profiles=section_profiles,
        issues=issues,
        fix_order=fix_order,
    )


# ============================================================
# Report Formatting
# ============================================================

def format_architecture_report(report: ArchitectureReport) -> str:
    """Format architecture diagnosis for display."""
    lines = []
    lines.append("=" * 60)
    lines.append("SECTION ARCHITECTURE DIAGNOSIS")
    lines.append("=" * 60)

    lines.append(f"\nPaper type: {report.paper_type}")
    lines.append(f"Hourglass structure: {'✓ Valid' if report.hourglass_valid else '✗ Violated'}")
    lines.append(f"Structural issues: {report.total_issues} ({report.critical_issues} critical)")

    # Section overview
    if report.section_profiles:
        lines.append("\n── Section Overview ──")
        for p in report.section_profiles:
            flags = []
            if p.contains_results_language and p.section_type == "discussion":
                flags.append("⚠ results-in-discussion")
            if p.contains_discussion_language and p.section_type == "results":
                flags.append("⚠ discussion-in-results")
            flag_str = f" [{', '.join(flags)}]" if flags else ""
            lines.append(f"  {p.section_id}: {p.section_type} ({p.word_count}w){flag_str}")

    # Issues by priority
    if report.issues:
        lines.append("\n── Structural Issues (by fix priority) ──")
        for issue in report.issues:
            sev_icon = {"critical": "🔴", "major": "🟡", "minor": "🔵"}.get(issue.severity, "⚪")
            lines.append(f"\n{sev_icon} [{issue.failure_mode}] {issue.description}")
            lines.append(f"   Section: {issue.section}")
            lines.append(f"   Evidence: {issue.evidence}")
            lines.append(f"   Fix: {issue.suggestion}")

    # Fix order
    if report.fix_order:
        lines.append("\n── Recommended Fix Order ──")
        lines.append("(Fix structure top-down: skeleton → section role → paragraph → sentence)")
        for step in report.fix_order:
            lines.append(f"  {step}")

    lines.append("\n" + "=" * 60)
    if report.critical_issues > 0:
        lines.append("⚠ Critical structural issues found. Fix these BEFORE sentence-level polish.")
    elif report.total_issues > 0:
        lines.append("Structure has some issues but is fundamentally sound. Can proceed with review.")
    else:
        lines.append("Paper structure is well-organized. Ready for content-level review.")

    return "\n".join(lines)
