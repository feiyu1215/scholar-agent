"""
tools/figure_analyzer.py — Multimodal figure & table analysis for academic papers.

Uses vision models to analyze figures and tables, checking:
- Caption clarity and completeness
- Axis labels and units
- Legend completeness
- Data integrity (misleading scales, truncated axes)
- Statistical notation (p-value, error bars, significance markers)
- Color accessibility
- Figure-claim alignment (whether the figure supports its stated conclusion)

Architecture:
    - All LLM calls go through llm/router.py → get_model_for_task("figure_analysis")
    - Graceful degradation: if vision model unavailable, outputs manual checklist
    - Integrates with paper_parser's extracted figures
    - Reports feed into review_engine's issue consolidation
"""

from __future__ import annotations

import os
import json
import base64
import asyncio
import re
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from pathlib import Path

from llm.client import LLMClient
from llm.router import get_model_for_task

# ============================================================
# Checklists
# ============================================================

FIGURE_CHECKLIST = [
    "caption_clarity",      # Caption clearly describes figure content
    "axis_labels",          # Axes have labels with units
    "legend_completeness",  # Legend explains all data series
    "data_integrity",       # No misleading scales/truncation
    "resolution_quality",   # Image quality adequate for print
    "color_accessibility",  # Color-blind friendly
    "statistical_notation", # p-values, error bars, significance markers
    "font_consistency",     # Fonts match body text conventions
]

TABLE_CHECKLIST = [
    "header_clarity",       # Column/row headers unambiguous
    "unit_specification",   # Units specified for numerical columns
    "alignment",            # Numbers properly aligned
    "note_completeness",    # All abbreviations explained in notes
    "statistical_notation", # p-values, SEs, CIs formatted consistently
]

SUPPORTED_IMAGE_FORMATS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

# Common figure review risks
COMMON_FIGURE_RISKS = [
    "y_axis_truncated",
    "missing_error_bars",
    "cherry_picked_timeframe",
    "inconsistent_scales",
    "missing_baseline",
    "unclear_statistical_test",
    "overcrowded_no_highlight",
    "color_encodes_significance",
]

# ============================================================
# Prompts
# ============================================================

FIGURE_REVIEW_PROMPT = """You are a journal reviewer conducting a thorough assessment of a figure from an academic manuscript.

Your task is to evaluate the figure against standard journal quality criteria. Be rigorous but fair.

## Evaluation Criteria (score each as pass/fail with comments):

1. **caption_clarity**: Does the caption fully describe what is shown? Can a reader understand the figure without reading the main text?
2. **axis_labels**: Are all axes clearly labeled with variable names AND units? Are tick marks readable?
3. **legend_completeness**: Are all data series, colors, symbols, and line styles explained in the legend?
4. **data_integrity**: Is the data presented honestly? Check for: y-axis truncation, misleading scales, cherry-picked ranges, 3D effects that distort perception.
5. **resolution_quality**: Is the image sharp and clear enough for print (typically 300+ DPI for raster)?
6. **color_accessibility**: Would the figure be readable by someone with color vision deficiency? Are there redundant cues (patterns, shapes)?
7. **statistical_notation**: Are p-values, confidence intervals, error bars, significance markers (*, **, ***) used correctly and consistently?
8. **font_consistency**: Are fonts readable and consistent with typical journal body text sizes?

## Output format (JSON):
{
    "overall_score": <1-10>,
    "checklist_results": {
        "<criterion>": {"pass": true/false, "comment": "specific observation"}
    },
    "suggestions": ["actionable improvement 1", ...],
    "critical_issues": ["issues that would likely cause reviewer rejection", ...]
}

Be specific. Reference visual elements directly. Do not fabricate issues that aren't visible."""

TABLE_REVIEW_PROMPT = """You are a journal reviewer evaluating a table from an academic manuscript.

Assess the table against standard publication criteria:

1. **header_clarity**: Are column/row headers unambiguous?
2. **unit_specification**: Are units clearly specified for all numerical columns?
3. **alignment**: Are numbers properly aligned (decimal alignment)?
4. **note_completeness**: Are all abbreviations and statistical markers explained?
5. **statistical_notation**: Are p-values, standard errors, CIs formatted consistently?

## Output format (JSON):
{
    "overall_score": <1-10>,
    "checklist_results": {
        "<criterion>": {"pass": true/false, "comment": "specific observation"}
    },
    "suggestions": ["actionable improvement 1", ...],
    "critical_issues": ["issues that would likely cause reviewer rejection", ...]
}

Be specific. Reference actual content from the table."""


# ============================================================
# Core Functions
# ============================================================

def encode_image(image_path: str) -> str:
    """Encode image to base64 for vision API calls."""
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"Image not found: {image_path}")
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_IMAGE_FORMATS:
        raise ValueError(
            f"Unsupported format: {suffix}. Supported: {', '.join(SUPPORTED_IMAGE_FORMATS)}"
        )
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _get_mime_type(image_path: str) -> str:
    """Return MIME type based on file extension."""
    suffix = Path(image_path).suffix.lower()
    mime_map = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
    return mime_map.get(suffix, "image/png")


def _check_vision_available() -> bool:
    """Check if vision model is available (API key configured)."""
    return bool(
        os.environ.get("SUB2API_API_KEY", "")
        or os.environ.get("FRIDAY_APP_ID", "")
        or os.environ.get("OPENAI_API_KEY", "")
    )


async def analyze_figure(
    image_path: str,
    caption: Optional[str] = None,
    paper_context: Optional[str] = None,
    provider: Optional[str] = None,
) -> Dict:
    """Analyze a single figure using vision model.

    Args:
        image_path: Path to the image file
        caption: Figure caption text (if available)
        paper_context: Surrounding text context
        provider: LLM provider override

    Returns:
        Analysis result dict with overall_score, checklist_results, suggestions, critical_issues
    """
    model = get_model_for_task("figure_analysis")

    if not _check_vision_available():
        return _graceful_degradation("figure", image_path)

    try:
        image_b64 = encode_image(image_path)
    except (FileNotFoundError, ValueError) as e:
        return {
            "overall_score": 0,
            "error": str(e),
            "checklist_results": {},
            "suggestions": [],
            "critical_issues": [f"Cannot analyze: {e}"],
            "model_used": model,
        }

    mime_type = _get_mime_type(image_path)

    # Build user message with vision content
    user_content = []

    text_parts = []
    if caption:
        text_parts.append(f'Figure caption: "{caption}"')
    if paper_context:
        text_parts.append(f"Paper context: {paper_context[:1000]}")
    if text_parts:
        user_content.append({
            "type": "text",
            "text": "\n\n".join(text_parts) + "\n\nPlease review the following figure:",
        })
    else:
        user_content.append({
            "type": "text",
            "text": "Please review the following figure from an academic paper:",
        })

    user_content.append({
        "type": "image_url",
        "image_url": {
            "url": f"data:{mime_type};base64,{image_b64}",
            "detail": "high",
        },
    })

    client = LLMClient(model=model, max_concurrent=2, provider=provider)

    messages = [
        {"role": "system", "content": FIGURE_REVIEW_PROMPT},
        {"role": "user", "content": user_content},
    ]

    try:
        response = await client.chat_messages(
            messages=messages,
            max_tokens=2000,
            temperature=0.1,
        )
    except Exception as e:
        return _graceful_degradation("figure", image_path, error=str(e))

    result = _parse_analysis_response(response, FIGURE_CHECKLIST)
    result["model_used"] = model
    result["image_path"] = image_path

    return result


async def analyze_table(
    table_text: str,
    caption: Optional[str] = None,
    paper_context: Optional[str] = None,
    provider: Optional[str] = None,
) -> Dict:
    """Analyze a table's content and formatting."""
    model = get_model_for_task("figure_analysis")

    if not _check_vision_available():
        return _graceful_degradation("table")

    user_parts = []
    if caption:
        user_parts.append(f'Table caption: "{caption}"')
    if paper_context:
        user_parts.append(f"Paper context: {paper_context[:1000]}")
    user_parts.append(f"Table content:\n\n{table_text}")

    client = LLMClient(model=model, max_concurrent=2, provider=provider)

    try:
        response = await client.chat(
            system=TABLE_REVIEW_PROMPT,
            user="\n\n".join(user_parts),
            max_tokens=2000,
            temperature=0.1,
        )
    except Exception as e:
        return _graceful_degradation("table", error=str(e))

    result = _parse_analysis_response(response, TABLE_CHECKLIST)
    result["model_used"] = model
    result["type"] = "table"

    return result


async def batch_analyze_figures(
    figure_paths: List[str],
    captions: Optional[List[str]] = None,
    paper_context: Optional[str] = None,
    max_concurrency: int = 2,
    provider: Optional[str] = None,
) -> List[Dict]:
    """Batch-analyze multiple figures with concurrency control."""
    if not _check_vision_available():
        return [_graceful_degradation("figure", p) for p in figure_paths]

    semaphore = asyncio.Semaphore(max_concurrency)

    async def _analyze_one(idx: int, path: str) -> Dict:
        async with semaphore:
            cap = captions[idx] if captions and idx < len(captions) else None
            result = await analyze_figure(
                image_path=path,
                caption=cap,
                paper_context=paper_context,
                provider=provider,
            )
            result["figure_index"] = idx + 1
            return result

    tasks = [_analyze_one(i, p) for i, p in enumerate(figure_paths)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    processed = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            processed.append({
                "figure_index": i + 1,
                "image_path": figure_paths[i],
                "overall_score": 0,
                "error": f"{type(r).__name__}: {r}",
                "checklist_results": {},
                "suggestions": [],
                "critical_issues": [f"Analysis failed: {r}"],
                "model_used": get_model_for_task("figure_analysis"),
            })
        else:
            processed.append(r)

    return processed


# ============================================================
# Figure Contract — Claim-Evidence Alignment
# ============================================================

# Archetype classification patterns
_ARCHETYPE_PATTERNS = {
    "quantitative_grid": [
        r"panel\s*[a-z]", r"subfig", r"bar\s*chart", r"line\s*plot",
        r"scatter", r"histogram", r"box\s*plot", r"violin",
        r"heatmap", r"correlation",
    ],
    "schematic_composite": [
        r"schematic", r"diagram", r"workflow", r"pipeline",
        r"architecture", r"framework", r"overview", r"flow\s*chart",
    ],
    "image_plate_quant": [
        r"microscop", r"stain", r"fluoresce", r"immuno",
        r"western\s*blot", r"gel", r"imaging", r"histolog",
        r"electron\s*micro", r"confocal",
    ],
    "mixed_modality": [
        r"left.*right", r"top.*bottom", r"qualitative.*quantitative",
        r"image.*graph", r"photo.*plot",
    ],
}


@dataclass
class FigureContract:
    """Defines what a figure must demonstrate to support its claim."""
    core_conclusion: str
    evidence_mapping: Dict[str, str]  # panel/section → relationship to claim
    archetype: str  # quantitative_grid | schematic_composite | image_plate_quant | mixed_modality
    review_risks: List[str] = field(default_factory=list)


def build_figure_contract(
    caption: str,
    paper_context: str,
    figure_description: Optional[str] = None,
) -> FigureContract:
    """Build a FigureContract from caption and paper context.

    Pure rule-based (no LLM call):
    1. Extract core conclusion from caption
    2. Detect multi-panel structure and map panels to evidence
    3. Classify figure archetype
    4. Predict reviewer attack vectors
    """
    # Step 1: Extract core conclusion (typically the last sentence of caption)
    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', caption) if s.strip()]
    core_conclusion = sentences[-1] if len(sentences) >= 2 else caption.strip()

    # Step 2: Multi-panel detection
    evidence_mapping: Dict[str, str] = {}
    panel_pattern = r'(?:panel\s*|[\(\[]?)([A-Za-z])(?:[\)\]]?)\s*[,:．.\-–—]\s*([^.;]+)'
    for m in re.finditer(panel_pattern, caption, re.IGNORECASE):
        evidence_mapping[f"Panel {m.group(1).upper()}"] = m.group(2).strip()

    if not evidence_mapping and figure_description:
        for m in re.finditer(panel_pattern, figure_description, re.IGNORECASE):
            evidence_mapping[f"Panel {m.group(1).upper()}"] = m.group(2).strip()

    if not evidence_mapping:
        evidence_mapping["whole_figure"] = core_conclusion

    # Step 3: Archetype classification
    combined_text = f"{caption} {paper_context} {figure_description or ''}".lower()
    archetype = _classify_archetype(combined_text)

    # Step 4: Predict review risks
    review_risks = _predict_review_risks(caption, paper_context, archetype)

    return FigureContract(
        core_conclusion=core_conclusion,
        evidence_mapping=evidence_mapping,
        archetype=archetype,
        review_risks=review_risks,
    )


def _classify_archetype(text: str) -> str:
    """Pattern-match archetype classification."""
    scores: Dict[str, int] = {k: 0 for k in _ARCHETYPE_PATTERNS}
    for archetype, patterns in _ARCHETYPE_PATTERNS.items():
        for pat in patterns:
            if re.search(pat, text, re.IGNORECASE):
                scores[archetype] += 1
    best = max(scores, key=lambda k: scores[k])
    return best if scores[best] > 0 else "quantitative_grid"


def _predict_review_risks(caption: str, paper_context: str, archetype: str) -> List[str]:
    """Predict reviewer attack vectors based on archetype and text signals."""
    risks: List[str] = []
    combined = f"{caption} {paper_context}".lower()

    # Universal checks
    if not re.search(r'error\s*bar|confidence|standard\s*(?:error|deviation)|s\.?e\.?|ci\b', combined):
        risks.append("missing_error_bars")
    if not re.search(r'control|baseline|reference|placebo|comparison', combined):
        risks.append("missing_baseline")
    if re.search(r'p\s*[<>=]|signif|statistic', combined) and \
       not re.search(r'(?:t-test|anova|mann.whitney|wilcox|chi.sq|bonferroni|tukey)', combined):
        risks.append("unclear_statistical_test")

    # Archetype-specific
    if archetype == "quantitative_grid":
        panel_count = len(re.findall(r'panel\s*[a-z]|[\(\[][a-z][\)\]]', combined, re.IGNORECASE))
        if panel_count >= 2:
            risks.append("inconsistent_scales")
        if re.search(r'over\s*time|temporal|longitudinal|year|month|trend', combined):
            risks.append("cherry_picked_timeframe")
    elif archetype == "image_plate_quant":
        if not re.search(r'control|wild.type|wt|sham|vehicle', combined):
            risks.append("missing_baseline")
        if re.search(r'quantif|measur|count', combined):
            risks.append("missing_error_bars")
    elif archetype == "schematic_composite":
        if len(combined) > 500:
            risks.append("overcrowded_no_highlight")

    return list(dict.fromkeys(risks))


def check_figure_claim_alignment(
    contract: FigureContract,
    analysis_result: Dict,
) -> Dict:
    """Check whether a figure adequately supports its stated claim.

    Args:
        contract: FigureContract from build_figure_contract
        analysis_result: Result from analyze_figure

    Returns:
        Dict with alignment_score, misalignments, confirmed_risks, overall_assessment
    """
    misalignments: List[Dict[str, str]] = []
    confirmed_risks: List[str] = []
    score = 1.0

    checklist = analysis_result.get("checklist_results", {})
    critical_issues = analysis_result.get("critical_issues", [])
    suggestions = analysis_result.get("suggestions", [])
    raw_text = json.dumps(analysis_result, ensure_ascii=False).lower()

    # Check 1: Critical checklist failures
    critical_for_claim = {"data_integrity", "statistical_notation", "axis_labels"}
    for criterion, result in checklist.items():
        if not result.get("pass", True):
            if criterion in critical_for_claim:
                misalignments.append({
                    "aspect": criterion,
                    "issue": f"Criterion '{criterion}' failed: {result.get('comment', '')}",
                    "suggestion": f"Fix {criterion} to strengthen claim: {contract.core_conclusion[:80]}",
                })
                score -= 0.15
            else:
                score -= 0.05

    # Check 2: Evidence mapping coverage
    if len(contract.evidence_mapping) > 1:
        for panel, expected in contract.evidence_mapping.items():
            panel_key = panel.lower().replace(" ", "")
            if panel_key not in raw_text and expected.lower()[:20] not in raw_text:
                misalignments.append({
                    "aspect": f"evidence_coverage:{panel}",
                    "issue": f"{panel} (expected: {expected[:60]}) not clearly addressed",
                    "suggestion": f"Ensure {panel} clearly supports the conclusion",
                })
                score -= 0.1

    # Check 3: Confirm predicted risks
    risk_signal_map = {
        "y_axis_truncated": ["truncat", "misleading scale", "y-axis", "exaggerat"],
        "missing_error_bars": ["error bar", "confidence interval", "uncertainty", "no error"],
        "cherry_picked_timeframe": ["time range", "cherry", "selected period", "narrow window"],
        "inconsistent_scales": ["inconsistent scale", "different scale", "axis mismatch"],
        "missing_baseline": ["baseline", "control", "reference", "comparison missing"],
        "unclear_statistical_test": ["statistical test", "unclear test", "which test"],
        "overcrowded_no_highlight": ["overcrowd", "cluttered", "too many", "hard to read"],
        "color_encodes_significance": ["color.*significance", "color.*p-value"],
    }

    for risk in contract.review_risks:
        signals = risk_signal_map.get(risk, [])
        combined_issues = " ".join(critical_issues + suggestions).lower()
        for signal in signals:
            if re.search(signal, combined_issues) or re.search(signal, raw_text):
                confirmed_risks.append(risk)
                score -= 0.1
                break

    score = max(0.0, min(1.0, score))

    if score >= 0.8:
        assessment = "supports_claim"
    elif score >= 0.5:
        assessment = "partially_supports"
    elif score >= 0.25:
        assessment = "weak_support"
    else:
        assessment = "contradicts"

    return {
        "alignment_score": round(score, 2),
        "misalignments": misalignments,
        "confirmed_risks": confirmed_risks,
        "overall_assessment": assessment,
    }


# ============================================================
# Report Generation
# ============================================================

def generate_figure_report(results: List[Dict]) -> str:
    """Generate a consolidated figure/table review report."""
    lines = ["=" * 60, "FIGURE/TABLE REVIEW SUMMARY", "=" * 60, ""]

    lines.append("| #       | Type   | Score  | Critical Issues |")
    lines.append("|---------|--------|--------|-----------------|")

    total_score = 0
    total_count = 0
    all_critical = []

    for r in results:
        idx = r.get("figure_index", "?")
        fig_type = r.get("type", "Figure")
        score = r.get("overall_score", 0)
        critical = r.get("critical_issues", [])
        error = r.get("error", "")

        if error:
            critical_text = f"Error: {error[:50]}"
        elif critical:
            critical_text = critical[0][:50] + ("..." if len(critical[0]) > 50 else "")
        else:
            critical_text = "None"

        label = f"Fig {idx}" if fig_type != "table" else f"Table {idx}"
        lines.append(f"| {label:<7} | {fig_type:<6} | {score}/10  | {critical_text} |")

        if score > 0:
            total_score += score
            total_count += 1
        all_critical.extend(critical)

    if total_count > 0:
        avg = total_score / total_count
        lines.append(f"\nAverage Score: {avg:.1f}/10 ({total_count} figures analyzed)")

    # Detailed findings
    lines.append("\n" + "─" * 60)
    lines.append("DETAILED FINDINGS")
    lines.append("─" * 60)

    for r in results:
        idx = r.get("figure_index", "?")
        fig_type = r.get("type", "Figure")
        score = r.get("overall_score", 0)
        path = r.get("image_path", "N/A")

        label = f"Fig {idx}" if fig_type != "table" else f"Table {idx}"
        lines.append(f"\n### {label} (Score: {score}/10)")
        if path != "N/A":
            lines.append(f"  File: {path}")

        checklist = r.get("checklist_results", {})
        if checklist:
            fails = [(k, v) for k, v in checklist.items() if not v.get("pass", True)]
            passes = [(k, v) for k, v in checklist.items() if v.get("pass", True)]
            if fails:
                lines.append("  Issues:")
                for k, v in fails:
                    lines.append(f"     - {k}: {v.get('comment', 'No comment')}")
            if passes:
                lines.append(f"  Passed: {', '.join(k for k, _ in passes)}")

        suggestions = r.get("suggestions", [])
        if suggestions:
            lines.append("  Suggestions:")
            for s in suggestions[:5]:
                lines.append(f"     - {s}")

        critical = r.get("critical_issues", [])
        if critical:
            lines.append("  Critical:")
            for c in critical:
                lines.append(f"     - {c}")

    lines.append("\n" + "─" * 60)
    if all_critical:
        lines.append(f"WARNING: {len(all_critical)} critical issue(s) — address before submission.")
    else:
        lines.append("No critical issues found in figures/tables.")

    return "\n".join(lines)


# ============================================================
# Helpers
# ============================================================

def _parse_analysis_response(response: str, checklist: List[str]) -> Dict:
    """Parse JSON analysis response from LLM, with fallback handling."""
    response = response.strip()

    # Strip markdown code fences
    if response.startswith("```"):
        parts = response.split("```")
        if len(parts) >= 2:
            response = parts[1]
            if response.startswith("json"):
                response = response[4:]
        response = response.strip()

    try:
        parsed = json.loads(response)
        if isinstance(parsed, dict):
            parsed.setdefault("overall_score", 5)
            parsed.setdefault("checklist_results", {})
            parsed.setdefault("suggestions", [])
            parsed.setdefault("critical_issues", [])
            return parsed
    except json.JSONDecodeError:
        pass

    return {
        "overall_score": 5,
        "checklist_results": {},
        "suggestions": ["Could not fully parse analysis — manual review recommended"],
        "critical_issues": [],
        "raw_response": response[:1500],
        "parse_warning": True,
    }


def _graceful_degradation(item_type: str, path: str = "", error: str = "") -> Dict:
    """Graceful degradation when vision model is unavailable."""
    msg = "Vision model unavailable"
    if error:
        msg += f" ({error})"

    checklist = FIGURE_CHECKLIST if item_type == "figure" else TABLE_CHECKLIST

    return {
        "overall_score": 0,
        "checklist_results": {},
        "suggestions": [
            f"⚠️ {msg}. Manual review recommended.",
            f"Checklist for manual {item_type} review:",
            *[f"  - {c}" for c in checklist],
        ],
        "critical_issues": [],
        "model_used": "none (degraded mode)",
        "image_path": path,
        "type": item_type,
        "degraded": True,
    }
