"""
Phase 9B: Figure-text cross-reference validation.

Validates consistency between what figures show (as described in captions
and text references) and what the paper claims in the main body.

Focus areas:
  1. Magnitude consistency: "effect is large" vs figure showing small effect
  2. Significance claims: "statistically significant" vs CI crossing zero
  3. Trend claims: "increasing trend" vs described pattern
  4. Comparison claims: "A > B" vs figure showing otherwise
  5. Completeness: figures mentioned but never discussed, or vice versa
  6. Numbering: phantom references to non-existent figures
  7. Quantitative accuracy: specific values cited from figures

All analysis works on text signals only (no image pixel analysis).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from core.skills.base import Finding

from .figure_extractor import FigureReference, FigureType

logger = logging.getLogger(__name__)


# ==============================================================
# Data Types
# ==============================================================


@dataclass
class FigureTextClaim:
    """A claim about a figure made in the text.

    Attributes:
        figure_id: Which figure this claim references.
        claim_text: The textual claim (relevant sentence fragment).
        claim_type: Category of claim.
        location: Where in text this claim appears (sentence context).
        numerical_values: Any specific numbers mentioned.
        direction: Direction claim ("positive", "negative", "increasing", etc.)
        magnitude_descriptor: Magnitude claim ("large", "small", "negligible", etc.)
        significance_claim: Significance claim ("significant", "insignificant").
        comparison: Comparison claim ("larger than", "smaller than").
    """

    figure_id: str
    claim_text: str
    claim_type: str  # "magnitude", "significance", "trend", "comparison", "quantitative"
    location: str = ""
    numerical_values: list[float] = field(default_factory=list)
    direction: str = ""
    magnitude_descriptor: str = ""
    significance_claim: str = ""
    comparison: str = ""


@dataclass
class CrossModalInconsistency:
    """An inconsistency between figure and text.

    Attributes:
        figure_id: The figure involved.
        inconsistency_type: Category of inconsistency.
        text_claim: What the text says.
        figure_evidence: What the figure (caption/context) says.
        severity: critical / major / minor.
        confidence: How confident we are (0.0-1.0).
        suggestion: How to resolve the inconsistency.
    """

    figure_id: str
    inconsistency_type: str
    text_claim: str
    figure_evidence: str
    severity: str
    confidence: float
    suggestion: str = ""


@dataclass
class CoverageReport:
    """Report on figure-text coverage completeness.

    Attributes:
        orphan_figures: Figures never referenced in text.
        phantom_references: Text references to non-existent figures.
        under_discussed: Figures mentioned but key features not explained.
        total_figures: Total figures found.
        total_references: Total text references found.
        coverage_score: Overall coverage quality (0.0-1.0).
    """

    orphan_figures: list[str] = field(default_factory=list)
    phantom_references: list[str] = field(default_factory=list)
    under_discussed: list[str] = field(default_factory=list)
    total_figures: int = 0
    total_references: int = 0
    coverage_score: float = 1.0


# ==============================================================
# Figure-Text Cross-Validator
# ==============================================================


class FigureTextCrossValidator:
    """Cross-validate figure descriptions against text claims.

    Performs multi-level validation:
      1. Coverage: all figures discussed, no phantom references
      2. Consistency: claims about figures match figure descriptions
      3. Completeness: important figure features are discussed
      4. Accuracy: specific values cited from figures are verifiable

    All analysis is text-based — we compare claims made in the body text
    against information available in figure captions and surrounding context.
    """

    # ------------------------------------------------------------------
    # Claim extraction patterns
    # ------------------------------------------------------------------

    # Figure reference patterns
    _FIG_REF_RE = re.compile(
        r"(?:Figure|Fig\.?|FIGURE|FIG\.?|图)\s*(\d+)([a-zA-Z])?",
        re.IGNORECASE,
    )

    # Magnitude descriptors
    _MAGNITUDE_WORDS = {
        "large": "large",
        "substantial": "large",
        "sizable": "large",
        "considerable": "large",
        "significant": "large",  # In colloquial use (not statistical)
        "small": "small",
        "modest": "small",
        "negligible": "negligible",
        "trivial": "negligible",
        "minimal": "negligible",
        "moderate": "moderate",
    }

    # Direction words
    _DIRECTION_PATTERNS = [
        (r"(?:increase|rise|grow|upward|positive\s+trend)", "increasing"),
        (r"(?:decrease|decline|fall|drop|downward|negative\s+trend)", "decreasing"),
        (r"(?:flat|stable|constant|no\s+change|unchanged)", "flat"),
        (r"(?:converge|convergence)", "converging"),
        (r"(?:diverge|divergence)", "diverging"),
    ]

    # Significance patterns
    _SIG_PATTERNS = [
        (r"(?:statistically\s+)?significant", "significant"),
        (r"(?:not\s+significant|insignificant|statistically\s+insignificant)", "insignificant"),
    ]

    # Comparison patterns
    _COMPARISON_RE = re.compile(
        r"(?:larger|greater|bigger|higher|more)\s+than|"
        r"(?:smaller|less|lower|fewer)\s+than|"
        r"(?:similar\s+to|comparable\s+to|same\s+as)",
        re.IGNORECASE,
    )

    # Value citation patterns (values attributed to figures)
    _VALUE_CITATION_RE = re.compile(
        r"(?:Figure|Fig\.?)\s*\d+[a-z]?\s+(?:shows?|indicates?|reports?|reveals?)"
        r"[^.]*?([-+]?\d+\.?\d*)",
        re.IGNORECASE,
    )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate(
        self,
        paper_text: str,
        figures: list[FigureReference],
    ) -> tuple[list[Finding], CoverageReport]:
        """Run full figure-text cross-validation.

        Performs:
          1. Coverage analysis (orphans, phantoms)
          2. Claim extraction from text
          3. Consistency validation of claims vs figure content
          4. Completeness checks

        Args:
            paper_text: Full paper text.
            figures: List of extracted FigureReference objects.

        Returns:
            Tuple of (findings list, coverage report).
        """
        if not paper_text:
            return [], CoverageReport()

        findings: list[Finding] = []

        # Step 1: Coverage analysis
        coverage = self.check_figure_text_coverage(paper_text, figures)
        findings.extend(self._coverage_to_findings(coverage))

        # Step 2: Extract claims about figures
        claims = self.extract_figure_claims(paper_text, figures)

        # Step 3: Validate claims
        inconsistencies = self.validate_claims(claims, figures)
        findings.extend(self._inconsistencies_to_findings(inconsistencies))

        # Step 4: Completeness checks
        completeness_findings = self._check_completeness(figures, paper_text)
        findings.extend(completeness_findings)

        logger.info(
            "Figure-text cross-validation: %d findings, coverage=%.2f",
            len(findings),
            coverage.coverage_score,
        )

        return findings, coverage

    def extract_figure_claims(
        self, paper_text: str, figures: list[FigureReference]
    ) -> list[FigureTextClaim]:
        """Extract all claims about figures from the text.

        Scans sentences that reference figures and extracts:
          - Magnitude claims ("Figure 3 shows a large effect")
          - Direction claims ("as seen in Figure 2, the trend increases")
          - Significance claims ("Figure 4 demonstrates significance")
          - Comparison claims ("Figure 5 shows A is larger than B")
          - Quantitative claims ("approximately 0.15 as shown in Figure 1")

        Args:
            paper_text: Full paper text.
            figures: Known figure references.

        Returns:
            List of extracted claims.
        """
        claims: list[FigureTextClaim] = []
        sentences = self._split_sentences(paper_text)

        for sentence in sentences:
            # Check if sentence references a figure
            fig_refs = self._FIG_REF_RE.findall(sentence)
            if not fig_refs:
                continue

            for num, sub in fig_refs:
                fig_id = f"Figure {num}{sub}" if sub else f"Figure {num}"

                # Extract magnitude claims
                mag_claim = self._extract_magnitude_claim(sentence, fig_id)
                if mag_claim:
                    claims.append(mag_claim)

                # Extract direction claims
                dir_claim = self._extract_direction_claim(sentence, fig_id)
                if dir_claim:
                    claims.append(dir_claim)

                # Extract significance claims
                sig_claim = self._extract_significance_claim(sentence, fig_id)
                if sig_claim:
                    claims.append(sig_claim)

                # Extract comparison claims
                comp_claim = self._extract_comparison_claim(sentence, fig_id)
                if comp_claim:
                    claims.append(comp_claim)

                # Extract quantitative claims
                quant_claims = self._extract_quantitative_claims(sentence, fig_id)
                claims.extend(quant_claims)

        return claims

    def validate_claims(
        self,
        claims: list[FigureTextClaim],
        figures: list[FigureReference],
    ) -> list[CrossModalInconsistency]:
        """Validate claims against figure evidence.

        For each claim, checks whether the figure caption/context
        supports or contradicts the claim.

        Args:
            claims: Extracted text claims about figures.
            figures: Known figure references with captions.

        Returns:
            List of detected inconsistencies.
        """
        inconsistencies: list[CrossModalInconsistency] = []
        figure_map = {f.figure_id: f for f in figures}

        # Also build a map without sub-figure suffixes
        for f in figures:
            base_id = re.sub(r"[a-z]$", "", f.figure_id, flags=re.IGNORECASE)
            if base_id not in figure_map:
                figure_map[base_id] = f

        for claim in claims:
            figure = figure_map.get(claim.figure_id)
            if not figure:
                continue

            # Validate based on claim type
            if claim.claim_type == "magnitude":
                inc = self._validate_magnitude(claim, figure)
                if inc:
                    inconsistencies.append(inc)

            elif claim.claim_type == "significance":
                inc = self._validate_significance(claim, figure)
                if inc:
                    inconsistencies.append(inc)

            elif claim.claim_type == "trend":
                inc = self._validate_trend(claim, figure)
                if inc:
                    inconsistencies.append(inc)

            elif claim.claim_type == "comparison":
                inc = self._validate_comparison(claim, figure)
                if inc:
                    inconsistencies.append(inc)

            elif claim.claim_type == "quantitative":
                inc = self._validate_quantitative(claim, figure)
                if inc:
                    inconsistencies.append(inc)

        return inconsistencies

    def check_figure_text_coverage(
        self, paper_text: str, figures: list[FigureReference]
    ) -> CoverageReport:
        """Check that all figures are discussed and all references exist.

        Detects:
          - Orphan figures: captions found but never referenced in text
          - Phantom references: text mentions figures that don't exist
          - Under-discussed: figure referenced only in passing

        Args:
            paper_text: Full paper text.
            figures: Known figure references.

        Returns:
            CoverageReport with details.
        """
        report = CoverageReport(total_figures=len(figures))

        # Build set of figure IDs
        known_ids: set[str] = set()
        known_numbers: set[int] = set()
        for f in figures:
            known_ids.add(f.figure_id)
            num = f.canonical_number
            if num is not None:
                known_numbers.add(num)

        # Find all figure references in text
        all_refs: set[str] = set()
        ref_counts: dict[str, int] = {}
        for match in self._FIG_REF_RE.finditer(paper_text):
            num = match.group(1)
            sub = match.group(2) or ""
            fig_id = f"Figure {num}{sub}"
            all_refs.add(fig_id)
            ref_counts[fig_id] = ref_counts.get(fig_id, 0) + 1

        report.total_references = len(all_refs)

        # Orphan figures: have caption but no text reference
        for f in figures:
            if f.figure_id not in all_refs and f.caption:
                # Check if the base figure is referenced (sub-figures)
                base = re.sub(r"[a-z]$", "", f.figure_id, flags=re.IGNORECASE)
                if base not in all_refs:
                    report.orphan_figures.append(f.figure_id)

        # Phantom references: text references figures that have no caption
        # (These may be created as placeholders by the extractor, or may
        # reference figures not found at all)
        captioned_numbers: set[int] = set()
        for f in figures:
            if f.caption:
                num = f.canonical_number
                if num is not None:
                    captioned_numbers.add(num)

        for ref_id in all_refs:
            num_match = re.search(r"(\d+)", ref_id)
            if num_match:
                ref_num = int(num_match.group(1))
                if ref_num not in captioned_numbers:
                    report.phantom_references.append(ref_id)

        # Under-discussed: referenced only once and not in a substantive way
        for f in figures:
            count = ref_counts.get(f.figure_id, 0)
            if 0 < count <= 1 and len(f.text_mentions) <= 1:
                # Check if the mention is substantive
                if f.text_mentions:
                    mention = f.text_mentions[0].lower()
                    is_passing = any(kw in mention for kw in (
                        "see figure", "shown in figure", "presented in figure",
                        "as in figure", "appendix figure",
                    )) and len(mention) < 80
                    if is_passing:
                        report.under_discussed.append(f.figure_id)

        # Compute coverage score
        if report.total_figures > 0:
            problems = (
                len(report.orphan_figures)
                + len(report.phantom_references)
                + len(report.under_discussed) * 0.5
            )
            report.coverage_score = max(
                0.0, 1.0 - problems / max(report.total_figures, 1)
            )

        return report

    # ------------------------------------------------------------------
    # Internal: Claim extraction methods
    # ------------------------------------------------------------------

    def _extract_magnitude_claim(
        self, sentence: str, fig_id: str
    ) -> Optional[FigureTextClaim]:
        """Extract magnitude descriptor claims about a figure."""
        sentence_lower = sentence.lower()

        for word, category in self._MAGNITUDE_WORDS.items():
            if word in sentence_lower:
                # Check if this magnitude word is about the figure's content
                # (not just "statistically significant" which is different)
                if word == "significant" and "statistically" in sentence_lower:
                    continue

                return FigureTextClaim(
                    figure_id=fig_id,
                    claim_text=sentence[:200],
                    claim_type="magnitude",
                    location=sentence[:100],
                    magnitude_descriptor=category,
                )

        return None

    def _extract_direction_claim(
        self, sentence: str, fig_id: str
    ) -> Optional[FigureTextClaim]:
        """Extract trend/direction claims about a figure."""
        sentence_lower = sentence.lower()

        for pattern, direction in self._DIRECTION_PATTERNS:
            if re.search(pattern, sentence_lower):
                return FigureTextClaim(
                    figure_id=fig_id,
                    claim_text=sentence[:200],
                    claim_type="trend",
                    location=sentence[:100],
                    direction=direction,
                )

        return None

    def _extract_significance_claim(
        self, sentence: str, fig_id: str
    ) -> Optional[FigureTextClaim]:
        """Extract statistical significance claims about a figure."""
        sentence_lower = sentence.lower()

        for pattern, sig_type in self._SIG_PATTERNS:
            if re.search(pattern, sentence_lower):
                return FigureTextClaim(
                    figure_id=fig_id,
                    claim_text=sentence[:200],
                    claim_type="significance",
                    location=sentence[:100],
                    significance_claim=sig_type,
                )

        return None

    def _extract_comparison_claim(
        self, sentence: str, fig_id: str
    ) -> Optional[FigureTextClaim]:
        """Extract comparison claims about a figure."""
        match = self._COMPARISON_RE.search(sentence)
        if match:
            return FigureTextClaim(
                figure_id=fig_id,
                claim_text=sentence[:200],
                claim_type="comparison",
                location=sentence[:100],
                comparison=match.group(0),
            )
        return None

    def _extract_quantitative_claims(
        self, sentence: str, fig_id: str
    ) -> list[FigureTextClaim]:
        """Extract specific numerical values cited from a figure."""
        claims: list[FigureTextClaim] = []

        # Look for numbers in sentences that describe figure content
        describing_figure = any(kw in sentence.lower() for kw in (
            "shows", "indicates", "reveals", "demonstrates",
            "displays", "reports", "approximately", "about",
            "roughly", "around",
        ))

        if not describing_figure:
            return claims

        # Extract numbers from the sentence
        numbers = re.findall(r"([-+]?\d+\.?\d*)", sentence)
        # Filter out figure numbers themselves and very large numbers (likely page refs)
        fig_num_match = re.search(r"(\d+)", fig_id)
        fig_num_str = fig_num_match.group(1) if fig_num_match else ""

        meaningful_values: list[float] = []
        for n in numbers:
            if n == fig_num_str:
                continue
            try:
                val = float(n)
                # Filter: reasonable effect sizes/statistics, not page/section numbers
                if abs(val) < 1000 and val != 0:
                    meaningful_values.append(val)
            except ValueError:
                continue

        if meaningful_values:
            claims.append(FigureTextClaim(
                figure_id=fig_id,
                claim_text=sentence[:200],
                claim_type="quantitative",
                location=sentence[:100],
                numerical_values=meaningful_values[:5],
            ))

        return claims

    # ------------------------------------------------------------------
    # Internal: Claim validation methods
    # ------------------------------------------------------------------

    def _validate_magnitude(
        self, claim: FigureTextClaim, figure: FigureReference
    ) -> Optional[CrossModalInconsistency]:
        """Check if magnitude claims are consistent with figure caption."""
        caption_lower = figure.caption.lower()

        # Check for contradictory magnitude language in caption
        caption_magnitude = None
        for word, category in self._MAGNITUDE_WORDS.items():
            if word in caption_lower:
                if word == "significant" and "statistically" in caption_lower:
                    continue
                caption_magnitude = category
                break

        if caption_magnitude and claim.magnitude_descriptor:
            # Check for contradictions
            contradictions = {
                ("large", "small"), ("large", "negligible"),
                ("small", "large"), ("negligible", "large"),
            }
            pair = (claim.magnitude_descriptor, caption_magnitude)
            if pair in contradictions:
                return CrossModalInconsistency(
                    figure_id=claim.figure_id,
                    inconsistency_type="magnitude_mismatch",
                    text_claim=(
                        f"Text describes effect as '{claim.magnitude_descriptor}'"
                    ),
                    figure_evidence=(
                        f"Caption suggests '{caption_magnitude}' magnitude"
                    ),
                    severity="minor",
                    confidence=0.5,
                    suggestion=(
                        "Reconcile the magnitude language between text and "
                        "figure caption."
                    ),
                )

        return None

    def _validate_significance(
        self, claim: FigureTextClaim, figure: FigureReference
    ) -> Optional[CrossModalInconsistency]:
        """Check if significance claims are consistent with figure content."""
        caption_lower = figure.caption.lower()
        mentions_text = " ".join(figure.text_mentions).lower()
        full_context = caption_lower + " " + mentions_text

        # Check for "significant" claim vs "crosses zero" / "includes zero" evidence
        if claim.significance_claim == "significant":
            contradicting = any(kw in full_context for kw in (
                "crosses zero", "includes zero", "contains zero",
                "not significant", "insignificant",
                "confidence interval includes zero",
                "cannot reject the null",
            ))
            if contradicting:
                return CrossModalInconsistency(
                    figure_id=claim.figure_id,
                    inconsistency_type="significance_contradiction",
                    text_claim="Text claims the effect is statistically significant",
                    figure_evidence=(
                        "Figure context mentions CI crossing zero or insignificance"
                    ),
                    severity="major",
                    confidence=0.7,
                    suggestion=(
                        "Verify whether the confidence interval truly excludes "
                        "zero at the claimed significance level."
                    ),
                )

        elif claim.significance_claim == "insignificant":
            contradicting = any(kw in full_context for kw in (
                "highly significant", "strongly significant",
                "statistically significant at",
                "significant at the 1%", "significant at the 5%",
            ))
            if contradicting:
                return CrossModalInconsistency(
                    figure_id=claim.figure_id,
                    inconsistency_type="significance_contradiction",
                    text_claim="Text claims the effect is insignificant",
                    figure_evidence=(
                        "Figure context suggests statistical significance"
                    ),
                    severity="major",
                    confidence=0.65,
                    suggestion=(
                        "Clarify the discrepancy — perhaps different "
                        "specifications or subsamples are being compared."
                    ),
                )

        return None

    def _validate_trend(
        self, claim: FigureTextClaim, figure: FigureReference
    ) -> Optional[CrossModalInconsistency]:
        """Check if trend/direction claims are consistent."""
        caption_lower = figure.caption.lower()
        mentions_text = " ".join(figure.text_mentions).lower()
        full_context = caption_lower + " " + mentions_text

        if not claim.direction:
            return None

        # Map opposite directions
        opposites = {
            "increasing": ["decrease", "declining", "downward", "fall", "drop"],
            "decreasing": ["increase", "rising", "upward", "growth", "rise"],
            "flat": ["increase", "decrease", "trend", "growth", "decline"],
            "converging": ["diverge", "diverging", "divergence"],
            "diverging": ["converge", "converging", "convergence"],
        }

        opposite_words = opposites.get(claim.direction, [])
        contradicting = any(word in full_context for word in opposite_words)

        if contradicting:
            return CrossModalInconsistency(
                figure_id=claim.figure_id,
                inconsistency_type="trend_contradiction",
                text_claim=f"Text claims '{claim.direction}' pattern",
                figure_evidence=(
                    f"Figure context contains language suggesting opposite direction"
                ),
                severity="minor",
                confidence=0.5,
                suggestion=(
                    "Verify the trend description — different time periods "
                    "or subgroups may show different patterns."
                ),
            )

        return None

    def _validate_comparison(
        self, claim: FigureTextClaim, figure: FigureReference
    ) -> Optional[CrossModalInconsistency]:
        """Check if comparison claims have supporting evidence."""
        # For comparisons, we mainly flag cases where the comparison
        # is made but no supporting evidence in the figure description
        caption_lower = figure.caption.lower()

        # Check if caption provides any comparative information
        has_comparison_info = any(kw in caption_lower for kw in (
            "larger", "smaller", "greater", "less",
            "higher", "lower", "compared", "versus",
            "relative", "ratio", "difference",
        ))

        if not has_comparison_info and "comparison" not in caption_lower:
            # Not necessarily an inconsistency, but worth noting if
            # the text makes strong comparative claims
            strong_claim = any(kw in claim.claim_text.lower() for kw in (
                "much larger", "significantly larger", "substantially",
                "dramatically", "far exceeds",
            ))
            if strong_claim:
                return CrossModalInconsistency(
                    figure_id=claim.figure_id,
                    inconsistency_type="unsupported_comparison",
                    text_claim=f"Text makes strong comparison: '{claim.comparison}'",
                    figure_evidence="Caption does not contain comparative language",
                    severity="suggestion",
                    confidence=0.4,
                    suggestion=(
                        "Ensure the figure caption or notes support "
                        "the comparative claim made in the text."
                    ),
                )

        return None

    def _validate_quantitative(
        self, claim: FigureTextClaim, figure: FigureReference
    ) -> Optional[CrossModalInconsistency]:
        """Check if quantitative values cited from figures are verifiable."""
        if not claim.numerical_values:
            return None

        # Check if the cited values appear in the figure's reported values
        reported = figure.reported_values
        if not reported:
            return None

        # Flatten reported values for comparison
        reported_numbers: set[float] = set()
        for values_list in reported.values():
            if isinstance(values_list, list):
                for item in values_list:
                    if isinstance(item, dict) and "value" in item:
                        reported_numbers.add(item["value"])
                    elif isinstance(item, (int, float)):
                        reported_numbers.add(float(item))

        if not reported_numbers:
            return None

        # Check each claimed value
        for claimed_val in claim.numerical_values:
            # Check if value is close to any reported value
            matches = any(
                abs(claimed_val - reported) < max(abs(claimed_val) * 0.05, 0.005)
                for reported in reported_numbers
            )
            if not matches and abs(claimed_val) > 0.001:
                # Value not found in figure data
                return CrossModalInconsistency(
                    figure_id=claim.figure_id,
                    inconsistency_type="value_mismatch",
                    text_claim=f"Text cites value {claimed_val} from figure",
                    figure_evidence=(
                        f"Value not found among reported values: "
                        f"{sorted(reported_numbers)[:5]}"
                    ),
                    severity="minor",
                    confidence=0.45,
                    suggestion=(
                        "Verify that the cited numerical value matches "
                        "what is actually shown in the figure."
                    ),
                )

        return None

    # ------------------------------------------------------------------
    # Internal: Completeness checks
    # ------------------------------------------------------------------

    def _check_completeness(
        self, figures: list[FigureReference], paper_text: str
    ) -> list[Finding]:
        """Check that figures are adequately discussed.

        Rules:
          1. Event study figures should have pre-trend discussion
          2. RD figures should have discontinuity description
          3. All data figures should have source mentioned somewhere
        """
        findings: list[Finding] = []

        for figure in figures:
            if not figure.text_mentions:
                continue

            mentions_text = " ".join(figure.text_mentions).lower()

            # Check if discussion is purely parenthetical
            all_parenthetical = all(
                self._is_parenthetical_reference(m) for m in figure.text_mentions
            )
            if all_parenthetical and len(figure.text_mentions) <= 2:
                findings.append(Finding(
                    category="figure",
                    severity="suggestion",
                    description=(
                        f"{figure.figure_id}: Only referenced parenthetically "
                        f"(e.g., '(see Figure N)') without substantive discussion "
                        f"of what it shows."
                    ),
                    suggestion=(
                        "Discuss the figure's content explicitly — key patterns, "
                        "magnitudes, and implications."
                    ),
                    location=figure.figure_id,
                    confidence=0.5,
                    skill_source="figure_text_consistency",
                ))

            # For event study / parallel trend: check depth of discussion
            if figure.figure_type in (
                FigureType.EVENT_STUDY, FigureType.PARALLEL_TREND
            ):
                key_elements = sum(1 for kw in (
                    "pre-treatment", "pre-trend", "post-treatment",
                    "dynamic", "lead", "lag", "confidence interval",
                    "insignificant", "significant",
                ) if kw in mentions_text)

                if key_elements < 2:
                    findings.append(Finding(
                        category="figure",
                        severity="minor",
                        description=(
                            f"{figure.figure_id}: Event study/parallel trend "
                            f"figure has limited text discussion. Key elements "
                            f"(pre-trends, post-effects, CIs) should be covered."
                        ),
                        suggestion=(
                            "Discuss pre-treatment insignificance, "
                            "post-treatment dynamics, and CI coverage."
                        ),
                        location=figure.figure_id,
                        confidence=0.55,
                        skill_source="figure_text_consistency",
                    ))

        return findings

    # ------------------------------------------------------------------
    # Internal: Result conversion
    # ------------------------------------------------------------------

    def _coverage_to_findings(self, coverage: CoverageReport) -> list[Finding]:
        """Convert coverage report to findings."""
        findings: list[Finding] = []

        for fig_id in coverage.orphan_figures:
            findings.append(Finding(
                category="figure",
                severity="minor",
                description=(
                    f"{fig_id}: Figure has a caption but is never "
                    f"referenced in the paper text."
                ),
                suggestion="Reference and discuss this figure in the text.",
                location=fig_id,
                confidence=0.7,
                skill_source="figure_text_consistency",
            ))

        for fig_id in coverage.phantom_references:
            findings.append(Finding(
                category="figure",
                severity="major",
                description=(
                    f"{fig_id}: Text references this figure but no "
                    f"corresponding figure/caption was found."
                ),
                suggestion=(
                    "Verify figure numbering — this may be a typo "
                    "or the figure may be missing."
                ),
                location=fig_id,
                confidence=0.8,
                skill_source="figure_text_consistency",
            ))

        for fig_id in coverage.under_discussed:
            findings.append(Finding(
                category="figure",
                severity="suggestion",
                description=(
                    f"{fig_id}: Figure is only mentioned in passing "
                    f"without substantive discussion of its content."
                ),
                suggestion=(
                    "Add discussion of key patterns shown in the figure."
                ),
                location=fig_id,
                confidence=0.5,
                skill_source="figure_text_consistency",
            ))

        return findings

    def _inconsistencies_to_findings(
        self, inconsistencies: list[CrossModalInconsistency]
    ) -> list[Finding]:
        """Convert inconsistencies to findings."""
        findings: list[Finding] = []

        for inc in inconsistencies:
            findings.append(Finding(
                category="figure",
                severity=inc.severity,
                description=(
                    f"{inc.figure_id}: {inc.inconsistency_type.replace('_', ' ').title()} — "
                    f"{inc.text_claim}"
                ),
                evidence=inc.figure_evidence,
                suggestion=inc.suggestion,
                location=inc.figure_id,
                confidence=inc.confidence,
                skill_source="figure_text_consistency",
            ))

        return findings

    # ------------------------------------------------------------------
    # Internal: Text utilities
    # ------------------------------------------------------------------

    def _split_sentences(self, text: str) -> list[str]:
        """Split text into sentences."""
        # Protect abbreviations
        protected = text
        for abbr in ("Fig.", "fig.", "et al.", "i.e.", "e.g.", "vs.", "etc.", "Eq."):
            protected = protected.replace(abbr, abbr.replace(".", "<DOT>"))

        sentences = re.split(r"(?<=[.!?])\s+", protected)
        return [s.replace("<DOT>", ".").strip() for s in sentences if s.strip()]

    def _is_parenthetical_reference(self, text: str) -> bool:
        """Check if a figure reference is purely parenthetical."""
        text_lower = text.lower().strip()
        parenthetical_patterns = [
            r"^\(.*figure.*\)$",
            r"^\(see\s+fig",
            r"^see\s+fig",
            r"^\(.*fig\.?\s*\d.*\)$",
            r"^as\s+shown\s+in\s+fig",
            r"^presented\s+in\s+fig",
        ]
        return any(
            re.search(p, text_lower) for p in parenthetical_patterns
        )


# ==============================================================
# Module exports
# ==============================================================

__all__ = [
    "FigureTextCrossValidator",
    "FigureTextClaim",
    "CrossModalInconsistency",
    "CoverageReport",
]
