"""
Phase 9B: Figure extraction and classification.

Extracts figure references, captions, and metadata from paper text.
Provides structural analysis of figure content without requiring actual
image data (works on text descriptions, captions, and references).

Key capabilities:
  - Multi-pattern figure caption extraction (Figure/Fig/图)
  - Text mention aggregation per figure
  - Economics figure type classification via keyword heuristics
  - Reported value extraction from captions and surrounding text
  - Sub-figure detection (Figure 3a, 3b) with parent grouping
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


# ==============================================================
# Figure Type Classification
# ==============================================================


class FigureType(Enum):
    """Economics paper figure classification.

    Covers the most common figure types in empirical economics papers,
    particularly those using causal inference methodologies.
    """

    EVENT_STUDY = "event_study"
    PARALLEL_TREND = "parallel_trend"
    COEFFICIENT_PLOT = "coefficient_plot"
    TIME_SERIES = "time_series"
    SCATTER_PLOT = "scatter_plot"
    DISTRIBUTION = "distribution"
    MAP = "map"
    FLOWCHART = "flowchart"
    REGRESSION_DISCONTINUITY = "rd_plot"
    PLACEBO_TEST = "placebo_test"
    ROBUSTNESS = "robustness"
    BINSCATTER = "binscatter"
    KAPLAN_MEIER = "kaplan_meier"
    DAG = "dag"
    OTHER = "other"


# ==============================================================
# Data Types
# ==============================================================


@dataclass
class FigureReference:
    """A figure referenced in the paper.

    Attributes:
        figure_id: Canonical identifier (e.g. "Figure 1", "Fig. 3a").
        caption: Full caption text extracted from the paper.
        figure_type: Classified type based on caption and context.
        page_number: Approximate page (0 if unknown).
        section: Section in which the figure caption appears.
        text_mentions: All text fragments that mention this figure.
        reported_values: Numerical values mentioned in caption or nearby text.
        sub_figures: Sub-figure labels detected (e.g. ["a", "b", "c"]).
        parent_id: If this is a sub-figure, the parent figure id.
        notes: Any notes below the figure caption.
    """

    figure_id: str
    caption: str
    figure_type: FigureType = FigureType.OTHER
    page_number: int = 0
    section: str = ""
    text_mentions: list[str] = field(default_factory=list)
    reported_values: dict = field(default_factory=dict)
    sub_figures: list[str] = field(default_factory=list)
    parent_id: str = ""
    notes: str = ""

    @property
    def canonical_number(self) -> Optional[int]:
        """Extract the numeric portion of the figure id."""
        match = re.search(r"(\d+)", self.figure_id)
        return int(match.group(1)) if match else None

    @property
    def is_subfigure(self) -> bool:
        """Whether this is a sub-figure (e.g. Figure 3a)."""
        return bool(self.parent_id)


@dataclass
class FigureClassification:
    """Result of classifying a figure's type.

    Attributes:
        primary_type: The most likely figure type.
        confidence: Confidence score 0.0-1.0.
        secondary_type: Alternative classification (if ambiguous).
        reasoning: Human-readable explanation of classification.
        matched_keywords: Keywords that triggered classification.
    """

    primary_type: FigureType
    confidence: float
    secondary_type: Optional[FigureType] = None
    reasoning: str = ""
    matched_keywords: list[str] = field(default_factory=list)


@dataclass
class ExtractedValue:
    """A numerical value extracted from figure context.

    Attributes:
        value: The numeric value.
        context: Surrounding text fragment.
        value_type: Semantic type (e.g. "effect_size", "p_value", "sample_size").
        unit: Unit of measurement if detected.
    """

    value: float
    context: str = ""
    value_type: str = ""
    unit: str = ""


# ==============================================================
# Figure Extractor
# ==============================================================


class FigureExtractor:
    """Extract and classify figures from paper text.

    Does NOT require actual image data — works from:
      - Figure captions (Figure N: ..., Fig. N. ..., 图N ...)
      - Text references to figures ("as shown in Figure 3")
      - Contextual clues (section headings, methodology descriptions)

    Extraction pipeline:
      1. Scan for figure captions → FigureReference objects
      2. Scan for text mentions → attach to corresponding figures
      3. Detect sub-figures and group them
      4. Extract reported values from captions and mentions
      5. Classify figure types via keyword heuristics
    """

    # ------------------------------------------------------------------
    # Caption detection patterns
    # ------------------------------------------------------------------

    # English patterns: "Figure 1:", "Figure 1.", "Fig. 1:", "FIGURE 1 —"
    # Caption extends to end-of-line only (single line); multiline captions
    # are joined if the next line is indented or clearly a continuation.
    _CAPTION_RE = re.compile(
        r"(?:^|\n)\s*"
        r"(?P<prefix>(?:Figure|Fig\.?|FIGURE|FIG\.?)\s+"
        r"(?P<num>\d+)(?P<sub>[a-zA-Z])?)"
        r"\s*[:.—–\-\s]+"
        r"(?P<caption>[^\n]+)",
        re.IGNORECASE | re.MULTILINE,
    )

    # Chinese patterns: "图1:", "图 1.", "图1 "
    _CAPTION_ZH_RE = re.compile(
        r"(?:^|\n)\s*"
        r"(?P<prefix>图\s*(?P<num>\d+)(?P<sub>[a-zA-Z])?)"
        r"\s*[:.：。—–\-\s]+"
        r"(?P<caption>[^\n]+)",
        re.MULTILINE,
    )

    # Text mention patterns: "Figure 1", "Fig. 2a", "Figures 3 and 4"
    _MENTION_RE = re.compile(
        r"(?:Figure|Fig\.?|FIGURE|FIG\.?|图)\s*(\d+)([a-zA-Z])?",
        re.IGNORECASE,
    )

    # Section heading patterns
    _SECTION_RE = re.compile(
        r"(?:^|\n)\s*(?:\d+\.?\s+)?([A-Z][^\n]{2,80})\s*\n",
        re.MULTILINE,
    )

    # Sub-figure patterns in captions: "Panel A:", "(a)", "Panel (a)"
    _SUBFIG_RE = re.compile(
        r"(?:Panel|panel)\s*[\(]?([A-Za-z])[\)]?|"
        r"\(([a-z])\)\s",
        re.IGNORECASE,
    )

    # ------------------------------------------------------------------
    # Classification keyword maps
    # ------------------------------------------------------------------

    _TYPE_KEYWORDS: dict[FigureType, list[str]] = {
        FigureType.EVENT_STUDY: [
            "event study", "event-study", "dynamic effect",
            "leads and lags", "lead and lag", "pre-trend",
            "pre-treatment", "post-treatment", "treatment effect",
            "relative time", "periods before", "periods after",
            "t-1", "t-2", "t+1", "t+2", "time relative",
            "event time", "event window",
        ],
        FigureType.PARALLEL_TREND: [
            "parallel trend", "common trend", "pre-treatment trend",
            "parallel assumption", "parallel path", "pre-period",
            "pre-treatment period", "treatment and control group",
            "common pre-trend", "diverge", "no differential trend",
        ],
        FigureType.COEFFICIENT_PLOT: [
            "coefficient plot", "coefficient estimate",
            "forest plot", "point estimate", "confidence interval",
            "ci ", "95% confidence", "90% confidence",
            "heterogeneous effect", "subgroup analysis",
            "coefficient and confidence", "whisker",
        ],
        FigureType.TIME_SERIES: [
            "time series", "trend", "over time", "annual",
            "monthly", "quarterly", "yearly", "temporal",
            "time path", "evolution over", "historical",
        ],
        FigureType.SCATTER_PLOT: [
            "scatter", "correlation", "relationship between",
            "plot of", "fitted line", "regression line",
            "bivariate", "x-axis", "y-axis",
        ],
        FigureType.BINSCATTER: [
            "binscatter", "binned scatter", "bin scatter",
            "conditional mean", "residualized",
        ],
        FigureType.DISTRIBUTION: [
            "distribution", "histogram", "density",
            "kernel density", "cdf", "cumulative",
            "frequency", "probability", "pdf",
        ],
        FigureType.MAP: [
            "map", "geographic", "spatial", "region",
            "county", "state-level", "province", "gis",
            "choropleth", "heat map", "heatmap",
        ],
        FigureType.FLOWCHART: [
            "flowchart", "flow chart", "research design",
            "identification strategy", "diagram", "schematic",
            "conceptual framework", "mechanism", "timeline",
            "sample selection", "sample construction",
        ],
        FigureType.REGRESSION_DISCONTINUITY: [
            "regression discontinuity", "rd design", "rd plot",
            "discontinuity", "running variable", "cutoff",
            "threshold", "bandwidth", "mccrary", "manipulation",
            "local polynomial", "donut",
        ],
        FigureType.PLACEBO_TEST: [
            "placebo", "falsification", "placebo test",
            "fake treatment", "permutation test",
            "randomization inference", "null distribution",
            "pseudo treatment",
        ],
        FigureType.ROBUSTNESS: [
            "robustness", "sensitivity", "sensitivity analysis",
            "specification curve", "alternative specification",
            "stability", "specification chart",
        ],
        FigureType.KAPLAN_MEIER: [
            "kaplan-meier", "kaplan meier", "survival",
            "hazard", "duration", "survival curve",
        ],
        FigureType.DAG: [
            "directed acyclic graph", "dag", "causal graph",
            "causal diagram", "path diagram",
        ],
    }

    # Numerical value extraction patterns
    _VALUE_PATTERNS: list[tuple[str, str]] = [
        # Effect sizes
        (r"(?:effect\s+(?:of|is|=)\s*)([-+]?\d+\.?\d*)", "effect_size"),
        (r"(?:coefficient\s+(?:of|is|=)\s*)([-+]?\d+\.?\d*)", "coefficient"),
        (r"(?:estimate\s+(?:of|is|=)\s*)([-+]?\d+\.?\d*)", "estimate"),
        # P-values
        (r"(?:p\s*[<>=≤≥]\s*)(0?\.\d+)", "p_value"),
        (r"(?:p-value\s*(?:of|is|=)\s*)(0?\.\d+)", "p_value"),
        # Percentage effects
        (r"([-+]?\d+\.?\d*)\s*(?:percent(?:age)?|%)\s*(?:point)?", "percentage"),
        # Standard deviations
        (r"([-+]?\d+\.?\d*)\s*(?:standard\s+deviation|s\.?d\.?|σ)", "std_dev_units"),
        # Sample sizes
        (r"(?:N\s*=\s*|n\s*=\s*|sample\s+(?:of|size)\s+)([\d,]+)", "sample_size"),
        # Confidence intervals
        (r"\[?\s*([-+]?\d+\.?\d*)\s*,\s*([-+]?\d+\.?\d*)\s*\]?", "ci_bounds"),
        # Bandwidth / threshold
        (r"(?:bandwidth|threshold|cutoff)\s*(?:of|=|is)\s*([-+]?\d+\.?\d*)", "bandwidth"),
    ]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract_figures(self, paper_text: str) -> list[FigureReference]:
        """Extract all figure references from paper text.

        Pipeline:
          1. Extract captions (English + Chinese patterns)
          2. Collect text mentions for each figure
          3. Detect sub-figures
          4. Extract reported values
          5. Identify section context

        Args:
            paper_text: Full paper text content.

        Returns:
            List of FigureReference objects, ordered by figure number.
        """
        if not paper_text:
            return []

        figures: dict[str, FigureReference] = {}

        # Step 1: Extract figure captions
        self._extract_captions(paper_text, figures)

        # Step 2: Collect text mentions
        self._collect_mentions(paper_text, figures)

        # Step 3: Detect sub-figures within captions
        self._detect_subfigures(figures)

        # Step 4: Extract reported values
        for fig in figures.values():
            fig.reported_values = self._extract_reported_values_dict(
                fig.caption, fig.text_mentions
            )

        # Step 5: Identify section context
        self._assign_sections(paper_text, figures)

        # Sort by figure number
        result = sorted(
            figures.values(),
            key=lambda f: (f.canonical_number or 999, f.figure_id),
        )

        logger.info("Extracted %d figures from paper text", len(result))
        return result

    def classify_figure(
        self, figure: FigureReference, paper_text: str
    ) -> FigureClassification:
        """Classify figure type based on caption + context.

        Uses a keyword scoring approach:
          1. Score each FigureType by keyword matches in caption + mentions
          2. Apply contextual boosting (e.g., DID paper → parallel trend likely)
          3. Return the highest-scoring type with confidence

        Args:
            figure: The figure to classify.
            paper_text: Full paper text for contextual clues.

        Returns:
            FigureClassification with primary type and confidence.
        """
        # Build the text to analyze: caption + all mentions + nearby context
        analysis_text = self._build_analysis_text(figure, paper_text)
        analysis_lower = analysis_text.lower()

        # Score each type
        scores: dict[FigureType, float] = {}
        matched: dict[FigureType, list[str]] = {}

        for fig_type, keywords in self._TYPE_KEYWORDS.items():
            type_score = 0.0
            type_matched: list[str] = []
            for kw in keywords:
                if kw in analysis_lower:
                    # Keywords in caption are worth more
                    if kw in figure.caption.lower():
                        type_score += 2.0
                    else:
                        type_score += 1.0
                    type_matched.append(kw)
            scores[fig_type] = type_score
            matched[fig_type] = type_matched

        # Apply contextual boosting from paper methodology
        self._apply_contextual_boost(scores, paper_text)

        # Find top two types
        sorted_types = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        best_type = sorted_types[0][0] if sorted_types[0][1] > 0 else FigureType.OTHER
        best_score = sorted_types[0][1]

        secondary_type = None
        if len(sorted_types) > 1 and sorted_types[1][1] > 0:
            secondary_type = sorted_types[1][0]

        # Compute confidence based on score magnitude and gap
        confidence = self._compute_classification_confidence(sorted_types)

        # Build reasoning
        reasoning_parts: list[str] = []
        if matched.get(best_type):
            reasoning_parts.append(
                f"Matched keywords: {', '.join(matched[best_type][:5])}"
            )
        if best_type == FigureType.OTHER:
            reasoning_parts.append("No strong keyword matches found")

        return FigureClassification(
            primary_type=best_type,
            confidence=confidence,
            secondary_type=secondary_type,
            reasoning="; ".join(reasoning_parts),
            matched_keywords=matched.get(best_type, []),
        )

    def extract_reported_values(
        self, caption: str, mentions: list[str]
    ) -> list[ExtractedValue]:
        """Extract numerical values reported about a figure.

        Looks for values in:
          - The figure caption itself
          - Text mentions of the figure

        Args:
            caption: Figure caption text.
            mentions: List of text fragments mentioning this figure.

        Returns:
            List of ExtractedValue objects.
        """
        values: list[ExtractedValue] = []
        texts = [caption] + mentions

        for text in texts:
            if not text:
                continue
            for pattern, value_type in self._VALUE_PATTERNS:
                for match in re.finditer(pattern, text, re.IGNORECASE):
                    try:
                        if value_type == "ci_bounds":
                            # Two values for confidence interval
                            lower = float(match.group(1))
                            upper = float(match.group(2))
                            values.append(ExtractedValue(
                                value=lower,
                                context=match.group(0)[:80],
                                value_type="ci_lower",
                            ))
                            values.append(ExtractedValue(
                                value=upper,
                                context=match.group(0)[:80],
                                value_type="ci_upper",
                            ))
                        elif value_type == "sample_size":
                            raw_val = match.group(1).replace(",", "")
                            values.append(ExtractedValue(
                                value=float(int(raw_val)),
                                context=match.group(0)[:80],
                                value_type=value_type,
                            ))
                        else:
                            val = float(match.group(1))
                            values.append(ExtractedValue(
                                value=val,
                                context=match.group(0)[:80],
                                value_type=value_type,
                            ))
                    except (ValueError, IndexError):
                        continue

        return values

    # ------------------------------------------------------------------
    # Internal: Caption extraction
    # ------------------------------------------------------------------

    def _extract_captions(
        self, paper_text: str, figures: dict[str, FigureReference]
    ) -> None:
        """Extract figure captions from paper text."""
        # English captions
        for match in self._CAPTION_RE.finditer(paper_text):
            num = match.group("num")
            sub = match.group("sub") or ""
            caption = match.group("caption").strip()
            fig_id = f"Figure {num}{sub}"

            if fig_id not in figures:
                figures[fig_id] = FigureReference(
                    figure_id=fig_id,
                    caption=self._clean_caption(caption),
                )

        # Chinese captions
        for match in self._CAPTION_ZH_RE.finditer(paper_text):
            num = match.group("num")
            sub = match.group("sub") or ""
            caption = match.group("caption").strip()
            fig_id = f"图 {num}{sub}"

            # Normalize to English for consistency
            canonical_id = f"Figure {num}{sub}"
            if canonical_id not in figures:
                figures[canonical_id] = FigureReference(
                    figure_id=canonical_id,
                    caption=self._clean_caption(caption),
                )

    def _collect_mentions(
        self, paper_text: str, figures: dict[str, FigureReference]
    ) -> None:
        """Collect all text mentions of each figure."""
        sentences = self._split_to_sentences(paper_text)

        for sentence in sentences:
            for match in self._MENTION_RE.finditer(sentence):
                num = match.group(1)
                sub = match.group(2) or ""
                fig_id = f"Figure {num}{sub}"

                # Also try parent figure if this is a sub-reference
                parent_id = f"Figure {num}" if sub else None

                target_id = fig_id if fig_id in figures else parent_id
                if target_id and target_id in figures:
                    mention_text = sentence.strip()[:300]
                    if mention_text not in figures[target_id].text_mentions:
                        figures[target_id].text_mentions.append(mention_text)
                elif fig_id not in figures:
                    # Create a placeholder for figures mentioned but not captioned
                    figures[fig_id] = FigureReference(
                        figure_id=fig_id,
                        caption="",
                        text_mentions=[sentence.strip()[:300]],
                    )

    def _detect_subfigures(self, figures: dict[str, FigureReference]) -> None:
        """Detect sub-figures mentioned within captions."""
        for fig_id, fig in list(figures.items()):
            # Look for panel references in caption
            panels = set()
            for match in self._SUBFIG_RE.finditer(fig.caption):
                panel_label = match.group(1) or match.group(2)
                if panel_label:
                    panels.add(panel_label.lower())

            if panels:
                fig.sub_figures = sorted(panels)

    def _assign_sections(
        self, paper_text: str, figures: dict[str, FigureReference]
    ) -> None:
        """Assign section context to each figure based on position."""
        # Build section map: (start_pos, section_name)
        sections: list[tuple[int, str]] = []
        for match in self._SECTION_RE.finditer(paper_text):
            sections.append((match.start(), match.group(1).strip()))

        if not sections:
            return

        # For each figure, find the section it appears in
        for fig in figures.values():
            if fig.caption:
                cap_pos = paper_text.find(fig.caption[:50])
                if cap_pos >= 0:
                    # Find the last section before this caption
                    current_section = ""
                    for sec_pos, sec_name in sections:
                        if sec_pos <= cap_pos:
                            current_section = sec_name
                        else:
                            break
                    fig.section = current_section

    # ------------------------------------------------------------------
    # Internal: Classification helpers
    # ------------------------------------------------------------------

    def _build_analysis_text(
        self, figure: FigureReference, paper_text: str
    ) -> str:
        """Build combined text for figure type analysis."""
        parts = [figure.caption, figure.notes]
        parts.extend(figure.text_mentions[:10])

        # Add surrounding context from paper (paragraph containing caption)
        if figure.caption and paper_text:
            cap_start = paper_text.find(figure.caption[:40])
            if cap_start >= 0:
                # Get 500 chars before and after
                context_start = max(0, cap_start - 500)
                context_end = min(len(paper_text), cap_start + len(figure.caption) + 500)
                surrounding = paper_text[context_start:context_end]
                parts.append(surrounding)

        return " ".join(p for p in parts if p)

    def _apply_contextual_boost(
        self, scores: dict[FigureType, float], paper_text: str
    ) -> None:
        """Apply contextual boosting based on paper methodology.

        If the paper uses DID, boost parallel_trend scores.
        If the paper uses RD, boost rd_plot scores.
        Etc.
        """
        text_lower = paper_text.lower()[:5000]  # Check methodology sections early

        # DID context
        if any(kw in text_lower for kw in (
            "difference-in-difference", "difference in difference",
            "did ", "diff-in-diff", "triple difference",
        )):
            scores[FigureType.PARALLEL_TREND] = scores.get(
                FigureType.PARALLEL_TREND, 0
            ) + 1.0
            scores[FigureType.EVENT_STUDY] = scores.get(
                FigureType.EVENT_STUDY, 0
            ) + 0.5

        # RD context
        if any(kw in text_lower for kw in (
            "regression discontinuity", "rd design", "sharp rd",
            "fuzzy rd", "running variable",
        )):
            scores[FigureType.REGRESSION_DISCONTINUITY] = scores.get(
                FigureType.REGRESSION_DISCONTINUITY, 0
            ) + 1.0

        # IV context
        if any(kw in text_lower for kw in (
            "instrumental variable", "2sls", "two-stage",
            "first stage", "exclusion restriction",
        )):
            scores[FigureType.COEFFICIENT_PLOT] = scores.get(
                FigureType.COEFFICIENT_PLOT, 0
            ) + 0.5

    def _compute_classification_confidence(
        self, sorted_types: list[tuple[FigureType, float]]
    ) -> float:
        """Compute confidence based on score distribution."""
        if not sorted_types or sorted_types[0][1] <= 0:
            return 0.1  # Fallback to OTHER with low confidence

        best_score = sorted_types[0][1]
        second_score = sorted_types[1][1] if len(sorted_types) > 1 else 0.0

        # Confidence factors:
        # 1. Absolute score (more keywords = more confident)
        abs_factor = min(best_score / 6.0, 1.0)

        # 2. Gap between best and second (wider gap = more confident)
        if best_score > 0:
            gap_factor = (best_score - second_score) / best_score
        else:
            gap_factor = 0.0

        confidence = 0.4 * abs_factor + 0.6 * gap_factor
        return max(0.1, min(confidence, 0.95))

    # ------------------------------------------------------------------
    # Internal: Value extraction helper
    # ------------------------------------------------------------------

    def _extract_reported_values_dict(
        self, caption: str, mentions: list[str]
    ) -> dict:
        """Extract reported values and return as a dict keyed by type."""
        values = self.extract_reported_values(caption, mentions)
        result: dict = {}
        for v in values:
            key = v.value_type
            if key not in result:
                result[key] = []
            result[key].append({"value": v.value, "context": v.context})
        return result

    # ------------------------------------------------------------------
    # Internal: Text utilities
    # ------------------------------------------------------------------

    def _clean_caption(self, caption: str) -> str:
        """Clean up extracted caption text."""
        # Remove excessive whitespace
        caption = re.sub(r"\s+", " ", caption).strip()
        # Remove trailing references like [insert Figure here]
        caption = re.sub(r"\[.*?insert.*?\]", "", caption, flags=re.IGNORECASE)
        return caption

    def _split_to_sentences(self, text: str) -> list[str]:
        """Split text into sentences, preserving abbreviations."""
        # Protect common abbreviations
        protected = text
        for abbr in ("Fig.", "fig.", "et al.", "i.e.", "e.g.", "vs.", "etc."):
            protected = protected.replace(abbr, abbr.replace(".", "<DOT>"))

        # Split on sentence boundaries
        sentences = re.split(r"(?<=[.!?])\s+", protected)
        return [s.replace("<DOT>", ".").strip() for s in sentences if s.strip()]


# ==============================================================
# Module exports
# ==============================================================

__all__ = [
    "FigureType",
    "FigureReference",
    "FigureClassification",
    "ExtractedValue",
    "FigureExtractor",
]
