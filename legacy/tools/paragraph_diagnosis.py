"""
tools/paragraph_diagnosis.py — Paragraph-Level Structure Diagnosis (C-5).

Pre-rewrite diagnostic: analyzes paragraph structure within a section and
returns actionable fix hints for injection into the rewrite prompt.

Complements architecture_diagnosis.py (paper-level skeleton) by zooming in
to paragraph-level coherence BEFORE any sentence-level rewrite occurs.

Detects:
- Missing topic sentences (paragraphs that never state their point)
- Evidence deserts (consecutive claim-only paragraphs)
- Orphan evidence (data without interpretation)
- Transition gaps (abrupt paragraph shifts)
- Claim-evidence misalignment

Architecture:
    - Zero external dependencies (pure stdlib: re, dataclasses)
    - Zero LLM calls — purely rule-based (regex + heuristics)
    - Integrates with write_engine.generate_rewrite() as a pre-step
    - Can run standalone for any section text
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List


# ============================================================
# Pattern Banks (English + Chinese)
# ============================================================

TOPIC_SENTENCE_INDICATORS_EN = [
    r"\b(?:we argue|we show|we demonstrate|we propose|we find)\b",
    r"\b(?:this shows|this demonstrates|this suggests|this indicates)\b",
    r"\b(?:the key|the main|the central|the primary|the critical)\b",
    r"\b(?:our results|our analysis|our approach|our findings)\b",
    r"\b(?:this section|this paragraph|in this section|here we)\b",
    r"\b(?:in this paper|in this work|in this study)\b",
    r"\b(?:the (?:first|second|third|final) (?:step|point|aspect|factor))\b",
    r"\b(?:importantly|crucially|significantly|notably)\b",
]

TOPIC_SENTENCE_INDICATORS_ZH = [
    r"(?:本文|本研究|本节|本段)",
    r"(?:我们认为|我们发现|我们提出|我们证明)",
    r"(?:关键在于|核心是|重点是|根本原因)",
    r"(?:结果表明|研究发现|分析显示|数据证实)",
    r"(?:首先|其次|最后|第[一二三四五])",
    r"(?:值得注意的是|重要的是|具体而言)",
]

EVIDENCE_INDICATORS_EN = [
    r"\d+\.?\d*\s*%",
    r"\bTable\s+\d+\b",
    r"\bFigure\s+\d+\b",
    r"\bFig\.\s*\d+\b",
    r"\bEquation\s+\d+\b",
    r"\bEq\.\s*\d+\b",
    r"\b(?:results show|data indicates?|as shown in|experiment)\b",
    r"\b(?:p\s*[<>=]\s*0\.\d+|CI\s*[:=]|effect size)\b",
    r"\b(?:accuracy|precision|recall|F1|BLEU|ROUGE)\b.*\d",
    r"\b(?:significant(?:ly)?|outperform|surpass)\b.*\d",
    r"\[\d+\]",  # Numeric citations [1], [2]
    r"\([A-Z][a-z]+(?:\s+(?:et al\.|&))?,?\s*\d{4}\)",  # (Author, 2020)
    r"\b(?:for (?:example|instance)|e\.g\.,|such as)\b.*\b(?:the|a|an)\b",
]

EVIDENCE_INDICATORS_ZH = [
    r"\d+\.?\d*\s*%",
    r"表\s*\d+",
    r"图\s*\d+",
    r"(?:实验结果|数据表明|如表|如图|实验证明)",
    r"(?:准确率|精确率|召回率|F1值).*\d",
    r"(?:例如|比如|以.*为例|具体来说)",
    r"\[\d+\]",
]

TRANSITION_MARKERS_EN = [
    r"^(?:However|Furthermore|Moreover|In contrast|Similarly)",
    r"^(?:Nevertheless|Consequently|Therefore|Thus|Hence)",
    r"^(?:Building on|To address|Given that|In addition)",
    r"^(?:On the other hand|As a result|In particular)",
    r"^(?:More (?:specifically|importantly|generally))",
    r"^(?:Meanwhile|Alternatively|Conversely|Accordingly)",
    r"^(?:That said|Having established|With this in mind)",
]

TRANSITION_MARKERS_ZH = [
    r"^(?:然而|此外|相比之下|类似地|因此)",
    r"^(?:基于此|为了解决|鉴于|与此同时)",
    r"^(?:另一方面|具体而言|更重要的是|总的来说)",
    r"^(?:不过|进一步地|除此之外|在此基础上)",
    r"^(?:值得注意的是|综上所述|由此可见)",
]


# ============================================================
# Data Classes
# ============================================================

@dataclass
class ParagraphProfile:
    """Structure analysis of a single paragraph."""
    index: int                      # 0-based paragraph position
    text: str                       # First 200 chars for reference
    word_count: int
    has_topic_sentence: bool        # First sentence makes a claim/states the point
    has_evidence: bool              # Contains data, citations, or specific examples
    has_transition: bool            # Starts with transition markers
    claim_evidence_aligned: bool    # Claims are supported by nearby evidence
    structural_role: str            # "claim", "evidence", "transition", "mixed", "unclear"
    issues: List[str] = field(default_factory=list)


@dataclass
class SectionStructureReport:
    """Structural diagnosis of a section's paragraphs."""
    section_id: str
    total_paragraphs: int
    paragraphs: List[ParagraphProfile]
    structural_issues: List[str]    # Section-level problems
    fix_hints: List[str]            # Actionable hints for rewrite prompt injection
    health_score: float             # 0.0-1.0, higher = healthier structure


# ============================================================
# Internal Detection Functions
# ============================================================

def _split_paragraphs(text: str) -> List[str]:
    """Split text into paragraphs by double newline or single newline + indent."""
    # Primary split: double newline
    parts = re.split(r"\n\s*\n", text)
    # Secondary: single newline followed by indent (common in some formats)
    result = []
    for part in parts:
        sub = re.split(r"\n(?=[ \t]{2,}\S)", part)
        result.extend(sub)
    # Filter empty and strip
    return [p.strip() for p in result if p.strip() and len(p.strip()) > 20]


def _get_first_sentence(paragraph: str) -> str:
    """Extract the first sentence of a paragraph."""
    # Handle both English (period) and Chinese (。) sentence endings
    match = re.match(r"^(.+?[.。!?！？])\s", paragraph + " ")
    if match:
        return match.group(1)
    # Fallback: first 150 chars or up to first newline
    first_line = paragraph.split("\n")[0]
    return first_line[:150]


def has_topic_sentence(paragraph: str) -> bool:
    """Check if the first sentence makes a claim or states the paragraph's purpose."""
    first_sent = _get_first_sentence(paragraph)
    first_sent_lower = first_sent.lower()

    # Check English indicators
    for pattern in TOPIC_SENTENCE_INDICATORS_EN:
        if re.search(pattern, first_sent_lower):
            return True

    # Check Chinese indicators
    for pattern in TOPIC_SENTENCE_INDICATORS_ZH:
        if re.search(pattern, first_sent):
            return True

    # Heuristic: short declarative first sentence (< 30 words) that's not a question
    words = first_sent.split()
    if 5 <= len(words) <= 30 and not first_sent.rstrip().endswith("?"):
        # Contains a strong verb suggesting a statement of purpose
        if re.search(r"\b(?:is|are|was|were|has|have|shows?|reveals?|provides?)\b", first_sent_lower):
            return True

    return False


def has_evidence(paragraph: str) -> bool:
    """Check if paragraph contains data, citations, or specific examples."""
    # Check English evidence indicators
    for pattern in EVIDENCE_INDICATORS_EN:
        if re.search(pattern, paragraph, re.IGNORECASE):
            return True

    # Check Chinese evidence indicators
    for pattern in EVIDENCE_INDICATORS_ZH:
        if re.search(pattern, paragraph):
            return True

    return False


def has_transition(paragraph: str) -> bool:
    """Check if paragraph starts with a transition marker."""
    first_line = paragraph.split("\n")[0].strip()

    # Check English transition markers (match at start)
    for pattern in TRANSITION_MARKERS_EN:
        if re.search(pattern, first_line, re.IGNORECASE):
            return True

    # Check Chinese transition markers (match at start)
    for pattern in TRANSITION_MARKERS_ZH:
        if re.search(pattern, first_line):
            return True

    return False


def detect_claim_evidence_alignment(paragraph: str) -> bool:
    """Determine if claims and evidence are aligned within the paragraph.

    Aligned means:
    - Has both claim language and evidence = aligned
    - Has only claims with no evidence = unaligned
    - Has only evidence with no interpretation = unaligned
    - Has neither = vacuously aligned (no misalignment detected)
    """
    has_claim_lang = any(
        re.search(p, paragraph, re.IGNORECASE)
        for p in TOPIC_SENTENCE_INDICATORS_EN + TOPIC_SENTENCE_INDICATORS_ZH
    )
    has_ev = has_evidence(paragraph)

    if has_claim_lang and has_ev:
        return True
    if not has_claim_lang and not has_ev:
        return True  # Vacuously aligned (e.g., transitional paragraph)
    return False


def _determine_structural_role(paragraph: str, has_topic: bool, has_ev: bool, has_trans: bool) -> str:
    """Classify paragraph's structural role."""
    if has_trans and not has_topic and not has_ev:
        return "transition"
    if has_topic and has_ev:
        return "mixed"  # Self-contained: makes a point and supports it
    if has_topic and not has_ev:
        return "claim"
    if has_ev and not has_topic:
        return "evidence"
    # Fallback heuristics
    word_count = len(paragraph.split())
    if word_count < 40:
        return "transition"
    return "unclear"


# ============================================================
# Section-Level Analysis
# ============================================================

def _detect_section_issues(paragraphs: List[ParagraphProfile]) -> List[str]:
    """Detect section-level structural problems from paragraph patterns."""
    issues = []
    n = len(paragraphs)

    if n == 0:
        return ["Section is empty — no paragraphs detected."]

    # Issue: All paragraphs are claims with no evidence anywhere
    claim_count = sum(1 for p in paragraphs if p.structural_role == "claim")
    evidence_count = sum(1 for p in paragraphs if p.has_evidence)
    if claim_count >= 3 and evidence_count == 0:
        issues.append(
            f"All {claim_count} paragraphs make claims but none provide evidence. "
            "Section reads as unsupported assertions."
        )

    # Issue: Evidence desert — 3+ consecutive claim-only paragraphs
    consecutive_claims = 0
    for p in paragraphs:
        if p.structural_role == "claim" and not p.has_evidence:
            consecutive_claims += 1
            if consecutive_claims >= 3:
                issues.append(
                    f"Evidence desert detected: {consecutive_claims} consecutive "
                    "claim-only paragraphs without supporting data."
                )
                break
        else:
            consecutive_claims = 0

    # Issue: Orphan evidence — data without any interpretation around it
    for i, p in enumerate(paragraphs):
        if p.structural_role == "evidence":
            # Check if neighbors provide context
            prev_has_claim = (i > 0 and paragraphs[i - 1].has_topic_sentence)
            next_has_claim = (i < n - 1 and paragraphs[i + 1].has_topic_sentence)
            if not prev_has_claim and not next_has_claim:
                issues.append(
                    f"Paragraph {p.index + 1} is orphan evidence — presents data "
                    "without any neighboring paragraph interpreting it."
                )

    # Issue: No transitions at all in multi-paragraph section
    transition_count = sum(1 for p in paragraphs if p.has_transition)
    if n >= 4 and transition_count == 0:
        issues.append(
            "No transition markers found in a section with "
            f"{n} paragraphs — flow may feel disjointed."
        )

    # Issue: Missing topic sentences
    no_topic = sum(1 for p in paragraphs if not p.has_topic_sentence)
    if no_topic > n * 0.6 and n >= 3:
        issues.append(
            f"{no_topic}/{n} paragraphs lack a clear topic sentence. "
            "Readers may struggle to grasp each paragraph's point."
        )

    # Issue: Uniform paragraph lengths (lack of variation)
    if n >= 4:
        lengths = [p.word_count for p in paragraphs]
        avg = sum(lengths) / len(lengths)
        if avg > 0:
            variation = max(lengths) / avg - min(lengths) / avg
            if variation < 0.3:
                issues.append(
                    "Paragraph lengths are very uniform — consider varying "
                    "structure for better readability and emphasis."
                )

    return issues


def compute_health_score(paragraphs: List[ParagraphProfile]) -> float:
    """Compute a 0.0-1.0 health score for the section's paragraph structure.

    Scoring factors:
    - Topic sentences present (weight: 0.3)
    - Evidence present (weight: 0.25)
    - Transitions present (weight: 0.15)
    - Claim-evidence alignment (weight: 0.2)
    - Structural clarity — not 'unclear' (weight: 0.1)
    """
    if not paragraphs:
        return 0.0

    n = len(paragraphs)

    # Topic sentence ratio
    topic_ratio = sum(1 for p in paragraphs if p.has_topic_sentence) / n

    # Evidence ratio (at least some paragraphs should have evidence)
    evidence_ratio = min(sum(1 for p in paragraphs if p.has_evidence) / max(n * 0.5, 1), 1.0)

    # Transition ratio (not every paragraph needs one; ~30-50% is good)
    trans_count = sum(1 for p in paragraphs if p.has_transition)
    trans_target = n * 0.4
    trans_ratio = min(trans_count / max(trans_target, 1), 1.0)

    # Alignment ratio
    aligned_ratio = sum(1 for p in paragraphs if p.claim_evidence_aligned) / n

    # Clarity ratio (not 'unclear')
    clear_ratio = sum(1 for p in paragraphs if p.structural_role != "unclear") / n

    score = (
        0.30 * topic_ratio
        + 0.25 * evidence_ratio
        + 0.15 * trans_ratio
        + 0.20 * aligned_ratio
        + 0.10 * clear_ratio
    )

    # Penalty: evidence desert (3+ consecutive claims)
    consecutive_claims = 0
    max_consecutive = 0
    for p in paragraphs:
        if p.structural_role == "claim" and not p.has_evidence:
            consecutive_claims += 1
            max_consecutive = max(max_consecutive, consecutive_claims)
        else:
            consecutive_claims = 0
    if max_consecutive >= 3:
        score -= 0.10 * min(max_consecutive - 2, 3)

    return max(0.0, min(1.0, round(score, 3)))


def generate_structure_fix_hints(report: SectionStructureReport) -> List[str]:
    """Convert structural issues into natural-language instructions for the rewrite prompt.

    Returns at most 5 hints, prioritized by impact.
    """
    hints: List[str] = []

    # Priority 1: Evidence deserts
    consecutive_claims = 0
    desert_start = -1
    for p in report.paragraphs:
        if p.structural_role == "claim" and not p.has_evidence:
            if consecutive_claims == 0:
                desert_start = p.index + 1
            consecutive_claims += 1
        else:
            if consecutive_claims >= 3:
                desert_end = desert_start + consecutive_claims - 1
                hints.append(
                    f"Paragraphs {desert_start}-{desert_end} are all claims without evidence — "
                    "interleave supporting data, citations, or examples between them."
                )
            consecutive_claims = 0
            desert_start = -1
    if consecutive_claims >= 3:
        desert_end = desert_start + consecutive_claims - 1
        hints.append(
            f"Paragraphs {desert_start}-{desert_end} are all claims without evidence — "
            "interleave supporting data, citations, or examples between them."
        )

    # Priority 2: Missing topic sentences
    no_topic_indices = [p.index + 1 for p in report.paragraphs if not p.has_topic_sentence]
    if no_topic_indices:
        if len(no_topic_indices) <= 3:
            idx_str = ", ".join(str(i) for i in no_topic_indices)
            hints.append(
                f"Paragraph(s) {idx_str} lack a topic sentence — "
                "add an opening sentence stating what each paragraph demonstrates or argues."
            )
        else:
            hints.append(
                f"{len(no_topic_indices)} paragraphs lack topic sentences — "
                "each paragraph should open with a sentence stating its main point."
            )

    # Priority 3: Orphan evidence
    for p in report.paragraphs:
        if p.structural_role == "evidence" and not p.claim_evidence_aligned:
            hints.append(
                f"Paragraph {p.index + 1} presents evidence without interpretation — "
                "add a sentence explaining what the data means or which claim it supports."
            )

    # Priority 4: No transitions
    trans_count = sum(1 for p in report.paragraphs if p.has_transition)
    if report.total_paragraphs >= 4 and trans_count == 0:
        hints.append(
            "Section lacks transition markers between paragraphs — "
            "add connective phrases (However, Furthermore, Building on this) "
            "to signal logical relationships."
        )

    # Priority 5: Uniform structure
    if report.total_paragraphs >= 4:
        roles = [p.structural_role for p in report.paragraphs]
        unique_roles = set(roles)
        if len(unique_roles) == 1 and unique_roles != {"mixed"}:
            hints.append(
                f"All paragraphs have the same structural role ('{roles[0]}') — "
                "vary the structure by alternating claims with evidence and interpretation."
            )

    # Cap at 5 hints
    return hints[:5]


# ============================================================
# Main Entry Point
# ============================================================

def analyze_section_structure(section_id: str, text: str) -> SectionStructureReport:
    """Analyze paragraph-level structure of a section.

    Args:
        section_id: Identifier of the section being analyzed.
        text: Full text content of the section.

    Returns:
        SectionStructureReport with paragraph profiles, issues, and fix hints.
    """
    raw_paragraphs = _split_paragraphs(text)
    profiles: List[ParagraphProfile] = []

    for i, para in enumerate(raw_paragraphs):
        has_topic = has_topic_sentence(para)
        has_ev = has_evidence(para)
        has_trans = has_transition(para)
        aligned = detect_claim_evidence_alignment(para)
        role = _determine_structural_role(para, has_topic, has_ev, has_trans)

        # Paragraph-level issues
        para_issues: List[str] = []
        if not has_topic:
            para_issues.append("missing_topic_sentence")
        if role == "claim" and not has_ev:
            para_issues.append("claim_without_evidence")
        if role == "evidence" and not aligned:
            para_issues.append("orphan_evidence")
        if role == "unclear":
            para_issues.append("unclear_role")

        profiles.append(ParagraphProfile(
            index=i,
            text=para[:200],
            word_count=len(para.split()),
            has_topic_sentence=has_topic,
            has_evidence=has_ev,
            has_transition=has_trans,
            claim_evidence_aligned=aligned,
            structural_role=role,
            issues=para_issues,
        ))

    # Section-level analysis
    structural_issues = _detect_section_issues(profiles)
    health_score = compute_health_score(profiles)

    report = SectionStructureReport(
        section_id=section_id,
        total_paragraphs=len(profiles),
        paragraphs=profiles,
        structural_issues=structural_issues,
        fix_hints=[],  # Populated below
        health_score=health_score,
    )

    # Generate fix hints from the report
    report.fix_hints = generate_structure_fix_hints(report)

    return report


# ============================================================
# Report Formatting (for display / debugging)
# ============================================================

def format_structure_report(report: SectionStructureReport) -> str:
    """Format paragraph structure diagnosis for display."""
    lines = []
    lines.append("=" * 60)
    lines.append(f"PARAGRAPH STRUCTURE DIAGNOSIS — {report.section_id}")
    lines.append("=" * 60)

    lines.append(f"\nParagraphs: {report.total_paragraphs}")
    lines.append(f"Health score: {report.health_score:.2f} / 1.00")

    # Paragraph overview
    lines.append("\n── Paragraph Profiles ──")
    for p in report.paragraphs:
        flags = []
        if p.has_topic_sentence:
            flags.append("T")
        if p.has_evidence:
            flags.append("E")
        if p.has_transition:
            flags.append("→")
        flag_str = "[" + "".join(flags) + "]" if flags else "[  ]"
        issue_str = f" ⚠ {', '.join(p.issues)}" if p.issues else ""
        lines.append(
            f"  P{p.index + 1}: {flag_str} {p.structural_role:<10} "
            f"({p.word_count}w){issue_str}"
        )

    # Section-level issues
    if report.structural_issues:
        lines.append("\n── Section-Level Issues ──")
        for issue in report.structural_issues:
            lines.append(f"  • {issue}")

    # Fix hints
    if report.fix_hints:
        lines.append("\n── Fix Hints (for rewrite prompt) ──")
        for i, hint in enumerate(report.fix_hints, 1):
            lines.append(f"  {i}. {hint}")

    lines.append("\n" + "=" * 60)
    if report.health_score >= 0.8:
        lines.append("Structure is healthy. Proceed with sentence-level polish.")
    elif report.health_score >= 0.5:
        lines.append("Structure has issues. Address fix hints before polishing prose.")
    else:
        lines.append("Structure needs significant rework. Apply fix hints as priority.")

    return "\n".join(lines)


# ============================================================
# Backward-compatible aliases (deprecated private names)
# ============================================================
_has_topic_sentence = has_topic_sentence
_has_evidence = has_evidence
_has_transition = has_transition
_detect_claim_evidence_alignment = detect_claim_evidence_alignment
_compute_health_score = compute_health_score
