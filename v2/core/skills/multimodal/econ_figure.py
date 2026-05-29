"""
Phase 9B: Economics-specific figure analysis.

Specialized validators for economics paper figures:
  - Event study: pre-trend validation, dynamic effects pattern
  - DID: parallel trends assumption checks
  - RD: bandwidth sensitivity, manipulation tests
  - Coefficient plots: significance patterns, magnitude checks
  - Placebo/falsification: null result verification
  - Robustness: selective reporting detection

All analysis works on TEXT-level signals (captions, references, descriptions).
We do NOT inspect image pixels — instead we validate what the text SAYS
about each figure against known econometric best practices.
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
# Analysis Rule Results
# ==============================================================


@dataclass
class FigureIssue:
    """An issue found during economics figure analysis.

    Attributes:
        rule_name: Short identifier for the check that produced this issue.
        severity: critical / major / minor / suggestion.
        description: Human-readable issue description.
        evidence: Supporting evidence from the text.
        suggestion: Recommended fix or improvement.
        confidence: How confident we are that this is a real issue.
    """

    rule_name: str
    severity: str
    description: str
    evidence: str = ""
    suggestion: str = ""
    confidence: float = 0.7


# ==============================================================
# Economics Figure Analyzer
# ==============================================================


class EconFigureAnalyzer:
    """Economics-specific figure analysis engine.

    Applies domain-specific validation rules based on figure type.
    Each rule checks whether the text description of a figure follows
    econometric best practices and whether claims about figures are
    well-supported.

    All methods work purely on text signals — no image processing.
    """

    # ------------------------------------------------------------------
    # Main dispatch
    # ------------------------------------------------------------------

    def analyze(
        self, figure: FigureReference, paper_text: str
    ) -> list[Finding]:
        """Run all applicable analyses on a figure.

        Dispatches to type-specific analyzers based on figure_type.

        Args:
            figure: The figure to analyze.
            paper_text: Full paper text for contextual checks.

        Returns:
            List of Finding objects for the review report.
        """
        findings: list[Finding] = []

        dispatch = {
            FigureType.EVENT_STUDY: self.analyze_event_study,
            FigureType.PARALLEL_TREND: self.analyze_parallel_trend,
            FigureType.COEFFICIENT_PLOT: self.analyze_coefficient_plot,
            FigureType.REGRESSION_DISCONTINUITY: self.analyze_rd_plot,
            FigureType.PLACEBO_TEST: self.analyze_placebo_test,
            FigureType.ROBUSTNESS: self.analyze_robustness,
            FigureType.BINSCATTER: self.analyze_scatter,
            FigureType.SCATTER_PLOT: self.analyze_scatter,
            FigureType.DISTRIBUTION: self.analyze_distribution,
            FigureType.TIME_SERIES: self.analyze_time_series,
        }

        analyzer_fn = dispatch.get(figure.figure_type)
        if analyzer_fn:
            issues = analyzer_fn(figure, paper_text)
            findings.extend(self._issues_to_findings(issues, figure))

        # Always run general checks
        general_issues = self._general_checks(figure, paper_text)
        findings.extend(self._issues_to_findings(general_issues, figure))

        return findings

    # ------------------------------------------------------------------
    # Event Study Analysis
    # ------------------------------------------------------------------

    def analyze_event_study(
        self, figure: FigureReference, paper_text: str
    ) -> list[FigureIssue]:
        """Check event study figures for common issues.

        Rules:
          1. Pre-treatment coefficients should be insignificant
          2. Check for suspicious omission of reference period
          3. Verify dynamic effects pattern is discussed
          4. Check if confidence intervals are reported
          5. Verify normalization period is clearly stated
        """
        issues: list[FigureIssue] = []
        context = self._build_context(figure, paper_text)
        context_lower = context.lower()

        # Rule 1: Pre-treatment insignificance should be discussed
        pre_treat_discussed = any(kw in context_lower for kw in (
            "pre-treatment", "pre-trend", "prior to treatment",
            "before the treatment", "insignificant before",
            "no significant effect before", "pre-period",
            "leads are insignificant", "leads are not significant",
        ))
        if not pre_treat_discussed:
            issues.append(FigureIssue(
                rule_name="event_study_pre_trend",
                severity="major",
                description=(
                    f"{figure.figure_id}: Event study shown but text does not "
                    f"explicitly discuss whether pre-treatment coefficients are "
                    f"insignificant (crucial for parallel trends assumption)."
                ),
                evidence=figure.caption[:150],
                suggestion=(
                    "Discuss whether pre-treatment coefficients are jointly "
                    "insignificant and interpret this as evidence for parallel trends."
                ),
                confidence=0.65,
            ))

        # Rule 2: Reference period / normalization
        normalization_mentioned = any(kw in context_lower for kw in (
            "normalized to zero", "omitted period", "reference period",
            "base period", "t=-1", "t = -1", "period -1",
            "one period before", "normalize",
        ))
        if not normalization_mentioned:
            issues.append(FigureIssue(
                rule_name="event_study_normalization",
                severity="minor",
                description=(
                    f"{figure.figure_id}: Event study does not clearly state "
                    f"which period is used as the reference/normalization point."
                ),
                evidence=figure.caption[:150],
                suggestion=(
                    "Explicitly state which period is normalized to zero "
                    "(conventionally t=-1, the period immediately before treatment)."
                ),
                confidence=0.6,
            ))

        # Rule 3: Confidence intervals mentioned
        ci_mentioned = any(kw in context_lower for kw in (
            "confidence interval", "confidence band", "95%", "90%",
            "standard error band", "error bar", "ci ",
        ))
        if not ci_mentioned:
            issues.append(FigureIssue(
                rule_name="event_study_ci",
                severity="minor",
                description=(
                    f"{figure.figure_id}: Event study figure description does not "
                    f"mention confidence intervals or error bands."
                ),
                suggestion=(
                    "Report confidence intervals (typically 95%) to show "
                    "statistical uncertainty of dynamic treatment effects."
                ),
                confidence=0.5,
            ))

        # Rule 4: Check for claims about post-treatment effects
        post_effect_claimed = any(kw in context_lower for kw in (
            "effect emerges", "becomes significant", "post-treatment",
            "after treatment", "after the policy", "effect grows",
            "persistent", "immediate effect",
        ))
        if not post_effect_claimed:
            issues.append(FigureIssue(
                rule_name="event_study_post_discussion",
                severity="suggestion",
                description=(
                    f"{figure.figure_id}: Event study shown but text does not "
                    f"characterize the post-treatment dynamic pattern."
                ),
                suggestion=(
                    "Discuss the post-treatment pattern: immediate vs. gradual "
                    "effects, persistence, magnitude evolution."
                ),
                confidence=0.45,
            ))

        # Rule 5: Endpoint effects / long-run
        if "long-run" in context_lower or "long run" in context_lower:
            # If long-run claims are made, check if window is discussed
            window_mentioned = any(kw in context_lower for kw in (
                "window", "horizon", "periods", "quarters", "years",
                "months", "time frame",
            ))
            if not window_mentioned:
                issues.append(FigureIssue(
                    rule_name="event_study_window",
                    severity="minor",
                    description=(
                        f"{figure.figure_id}: Long-run effects claimed but "
                        f"the event window length is not clearly specified."
                    ),
                    confidence=0.5,
                ))

        return issues

    # ------------------------------------------------------------------
    # Parallel Trend Analysis
    # ------------------------------------------------------------------

    def analyze_parallel_trend(
        self, figure: FigureReference, paper_text: str
    ) -> list[FigureIssue]:
        """Check parallel trend figures for DID papers.

        Rules:
          1. Pre-treatment trends should be discussed as parallel
          2. Divergence timing should match claimed treatment timing
          3. Statistical test for parallel trends should be mentioned
          4. Time period coverage should be adequate
        """
        issues: list[FigureIssue] = []
        context = self._build_context(figure, paper_text)
        context_lower = context.lower()

        # Rule 1: Parallel trends conclusion stated
        parallel_stated = any(kw in context_lower for kw in (
            "move in parallel", "similar trend", "common trend",
            "no differential pre-trend", "pre-treatment trends are similar",
            "parallel prior to", "confirms parallel",
            "supports the parallel", "validates the parallel",
        ))
        if not parallel_stated:
            issues.append(FigureIssue(
                rule_name="parallel_trend_conclusion",
                severity="major",
                description=(
                    f"{figure.figure_id}: Figure shows pre-treatment trends "
                    f"but text does not explicitly conclude whether trends "
                    f"are parallel."
                ),
                evidence=figure.caption[:150],
                suggestion=(
                    "Explicitly state whether the visual evidence supports "
                    "the parallel trends assumption."
                ),
                confidence=0.7,
            ))

        # Rule 2: Formal test mentioned
        formal_test = any(kw in context_lower for kw in (
            "joint test", "f-test", "chi-square", "wald test",
            "joint significance", "joint f", "p-value for joint",
            "cannot reject", "fail to reject",
            "placebo regression", "leads test",
        ))
        if not formal_test:
            issues.append(FigureIssue(
                rule_name="parallel_trend_formal_test",
                severity="minor",
                description=(
                    f"{figure.figure_id}: Parallel trends shown visually but "
                    f"no formal statistical test (e.g., joint F-test of "
                    f"pre-treatment leads) is reported."
                ),
                suggestion=(
                    "Report a formal test of pre-treatment coefficient "
                    "joint significance (e.g., F-test that all leads = 0)."
                ),
                confidence=0.6,
            ))

        # Rule 3: Treatment timing clarity
        treatment_timing = any(kw in context_lower for kw in (
            "vertical line", "dashed line", "treatment date",
            "policy implementation", "reform date", "cutoff date",
            "event date", "intervention at", "treatment begins",
        ))
        if not treatment_timing:
            issues.append(FigureIssue(
                rule_name="parallel_trend_timing",
                severity="suggestion",
                description=(
                    f"{figure.figure_id}: Treatment timing is not clearly "
                    f"marked or discussed in relation to the figure."
                ),
                suggestion=(
                    "Mark the treatment date clearly (e.g., vertical dashed line) "
                    "and discuss when divergence begins relative to treatment."
                ),
                confidence=0.5,
            ))

        return issues

    # ------------------------------------------------------------------
    # Coefficient Plot Analysis
    # ------------------------------------------------------------------

    def analyze_coefficient_plot(
        self, figure: FigureReference, paper_text: str
    ) -> list[FigureIssue]:
        """Check coefficient/forest plots.

        Rules:
          1. CIs crossing zero = insignificant — check text claims
          2. Magnitude consistency with table values
          3. Check for selective reporting
          4. Reference line at zero should be present
        """
        issues: list[FigureIssue] = []
        context = self._build_context(figure, paper_text)
        context_lower = context.lower()

        # Rule 1: Zero reference line
        zero_line = any(kw in context_lower for kw in (
            "zero line", "null effect", "reference line",
            "dashed line at zero", "horizontal line",
            "vertical line at zero", "crosses zero",
        ))
        # Don't flag this — it's too common to omit explicit mention

        # Rule 2: Selective reporting check
        if "heterogene" in context_lower or "subgroup" in context_lower:
            # Heterogeneity analysis — check if all subgroups reported
            selective_indicators = any(kw in context_lower for kw in (
                "selected", "subset of", "most notable",
                "interesting", "key subgroup",
            ))
            if selective_indicators:
                issues.append(FigureIssue(
                    rule_name="coeff_plot_selective",
                    severity="minor",
                    description=(
                        f"{figure.figure_id}: Coefficient plot appears to show "
                        f"selected subgroups rather than all specifications. "
                        f"This may indicate selective reporting."
                    ),
                    evidence=figure.caption[:150],
                    suggestion=(
                        "Report all pre-specified subgroup analyses to avoid "
                        "the appearance of cherry-picking significant results."
                    ),
                    confidence=0.5,
                ))

        # Rule 3: Inconsistency between "all significant" claims and CI
        all_sig_claim = any(kw in context_lower for kw in (
            "all significant", "all statistically significant",
            "consistently significant", "uniformly significant",
        ))
        crosses_zero_mentioned = any(kw in context_lower for kw in (
            "cross zero", "crosses zero", "include zero",
            "includes zero", "contain zero", "contains zero",
            "insignificant",
        ))
        if all_sig_claim and crosses_zero_mentioned:
            issues.append(FigureIssue(
                rule_name="coeff_plot_sig_contradiction",
                severity="major",
                description=(
                    f"{figure.figure_id}: Text claims all coefficients are "
                    f"significant, but also mentions CIs crossing zero. "
                    f"This is contradictory."
                ),
                confidence=0.75,
            ))

        # Rule 4: CI level stated
        ci_level_stated = any(kw in context_lower for kw in (
            "95%", "90%", "99%", "confidence level",
        ))
        if not ci_level_stated and "confidence interval" in context_lower:
            issues.append(FigureIssue(
                rule_name="coeff_plot_ci_level",
                severity="suggestion",
                description=(
                    f"{figure.figure_id}: Confidence intervals shown but "
                    f"the confidence level is not explicitly stated."
                ),
                suggestion="State the confidence level (e.g., 95% CI).",
                confidence=0.5,
            ))

        return issues

    # ------------------------------------------------------------------
    # RD Plot Analysis
    # ------------------------------------------------------------------

    def analyze_rd_plot(
        self, figure: FigureReference, paper_text: str
    ) -> list[FigureIssue]:
        """Check RD design figures.

        Rules:
          1. Is discontinuity discussed?
          2. McCrary / manipulation test mentioned?
          3. Multiple bandwidths for robustness?
          4. Polynomial order discussed?
          5. Bin size / number of bins mentioned?
        """
        issues: list[FigureIssue] = []
        context = self._build_context(figure, paper_text)
        context_lower = context.lower()

        # Rule 1: Discontinuity discussion
        discontinuity_discussed = any(kw in context_lower for kw in (
            "jump", "discontinuity", "gap", "discrete change",
            "visual break", "clear break", "visible discontinuity",
        ))
        if not discontinuity_discussed:
            issues.append(FigureIssue(
                rule_name="rd_discontinuity_discussion",
                severity="major",
                description=(
                    f"{figure.figure_id}: RD plot shown but text does not "
                    f"discuss the visual evidence of discontinuity at the cutoff."
                ),
                suggestion=(
                    "Discuss the visual discontinuity at the threshold — "
                    "its magnitude, direction, and statistical significance."
                ),
                confidence=0.65,
            ))

        # Rule 2: McCrary test or manipulation check
        manipulation_check = any(kw in context_lower for kw in (
            "mccrary", "manipulation", "density test", "bunching",
            "sorting", "cattaneo", "manipulation test",
            "no evidence of manipulation", "density discontinuity",
        ))
        # Check in broader paper text too
        if not manipulation_check:
            broad_lower = paper_text.lower()
            manipulation_check = any(kw in broad_lower for kw in (
                "mccrary", "manipulation test", "density test",
                "cattaneo.*manipulation",
            ))

        if not manipulation_check:
            issues.append(FigureIssue(
                rule_name="rd_manipulation_test",
                severity="major",
                description=(
                    f"{figure.figure_id}: RD design figure without mention of "
                    f"a manipulation/density test (McCrary or Cattaneo et al.). "
                    f"This is a standard requirement for RD validity."
                ),
                suggestion=(
                    "Include a McCrary density test or Cattaneo-Jansson-Ma "
                    "manipulation test to rule out strategic sorting around cutoff."
                ),
                confidence=0.7,
            ))

        # Rule 3: Bandwidth sensitivity
        bandwidth_discussed = any(kw in context_lower for kw in (
            "bandwidth", "multiple bandwidth", "optimal bandwidth",
            "ik bandwidth", "cct bandwidth", "bandwidth sensitivity",
            "half the bandwidth", "double the bandwidth",
        ))
        if not bandwidth_discussed:
            issues.append(FigureIssue(
                rule_name="rd_bandwidth",
                severity="minor",
                description=(
                    f"{figure.figure_id}: RD plot does not discuss bandwidth "
                    f"choice or sensitivity to alternative bandwidths."
                ),
                suggestion=(
                    "Discuss the bandwidth selection method (e.g., CCT optimal) "
                    "and show robustness to alternative bandwidths."
                ),
                confidence=0.6,
            ))

        # Rule 4: Polynomial order
        polynomial_discussed = any(kw in context_lower for kw in (
            "polynomial", "linear", "quadratic", "local linear",
            "local polynomial", "order", "degree",
        ))
        if not polynomial_discussed:
            issues.append(FigureIssue(
                rule_name="rd_polynomial",
                severity="suggestion",
                description=(
                    f"{figure.figure_id}: Polynomial order for RD estimation "
                    f"is not mentioned in the figure context."
                ),
                suggestion=(
                    "State the polynomial order used for estimation "
                    "(local linear is currently recommended)."
                ),
                confidence=0.5,
            ))

        return issues

    # ------------------------------------------------------------------
    # Placebo Test Analysis
    # ------------------------------------------------------------------

    def analyze_placebo_test(
        self, figure: FigureReference, paper_text: str
    ) -> list[FigureIssue]:
        """Check placebo/falsification test figures.

        Rules:
          1. Null result should be explicitly stated
          2. True effect should be compared to placebo distribution
          3. Number of placebo iterations mentioned
        """
        issues: list[FigureIssue] = []
        context = self._build_context(figure, paper_text)
        context_lower = context.lower()

        # Rule 1: Null result stated
        null_stated = any(kw in context_lower for kw in (
            "no significant", "insignificant", "near zero",
            "close to zero", "centered around zero", "null",
            "fails to reject", "cannot reject",
            "no effect", "no evidence",
        ))
        if not null_stated:
            issues.append(FigureIssue(
                rule_name="placebo_null_result",
                severity="minor",
                description=(
                    f"{figure.figure_id}: Placebo test figure shown but text "
                    f"does not explicitly state that placebo effects are "
                    f"insignificant/near zero."
                ),
                suggestion=(
                    "Explicitly state that placebo coefficients are "
                    "statistically indistinguishable from zero."
                ),
                confidence=0.6,
            ))

        # Rule 2: Comparison to true effect
        comparison_made = any(kw in context_lower for kw in (
            "true effect", "actual effect", "real treatment",
            "true estimate", "actual estimate", "baseline estimate",
            "compared to", "relative to the actual",
            "outside the distribution", "extreme",
        ))
        if not comparison_made:
            issues.append(FigureIssue(
                rule_name="placebo_comparison",
                severity="suggestion",
                description=(
                    f"{figure.figure_id}: Placebo test does not compare "
                    f"the true treatment effect to the placebo distribution."
                ),
                suggestion=(
                    "Show where the true estimate falls relative to the "
                    "placebo distribution (e.g., p-value from permutation)."
                ),
                confidence=0.5,
            ))

        # Rule 3: Number of iterations
        iterations_mentioned = re.search(
            r"(\d+)\s*(?:iteration|permutation|repetition|placebo|simulation)",
            context_lower,
        )
        if not iterations_mentioned:
            issues.append(FigureIssue(
                rule_name="placebo_iterations",
                severity="suggestion",
                description=(
                    f"{figure.figure_id}: Number of placebo/permutation "
                    f"iterations is not mentioned."
                ),
                suggestion=(
                    "Report the number of permutation iterations "
                    "(typically 500-10000 for reliable inference)."
                ),
                confidence=0.45,
            ))

        return issues

    # ------------------------------------------------------------------
    # Robustness Analysis
    # ------------------------------------------------------------------

    def analyze_robustness(
        self, figure: FigureReference, paper_text: str
    ) -> list[FigureIssue]:
        """Check robustness check figures.

        Rules:
          1. Do results support main claims?
          2. Selective reporting indicators
          3. Specification diversity
        """
        issues: list[FigureIssue] = []
        context = self._build_context(figure, paper_text)
        context_lower = context.lower()

        # Rule 1: Conclusion about robustness stated
        robustness_conclusion = any(kw in context_lower for kw in (
            "robust", "stable", "consistent", "qualitatively similar",
            "remains significant", "unchanged", "confirms",
            "holds across", "insensitive to",
        ))
        if not robustness_conclusion:
            issues.append(FigureIssue(
                rule_name="robustness_conclusion",
                severity="minor",
                description=(
                    f"{figure.figure_id}: Robustness figure shown but text "
                    f"does not explicitly conclude whether results are robust."
                ),
                suggestion="State explicitly whether results are robust to these checks.",
                confidence=0.55,
            ))

        # Rule 2: Selective reporting — look for language about exceptions
        exception_language = any(kw in context_lower for kw in (
            "except", "with the exception", "one specification",
            "marginally", "loses significance", "weaker",
            "not robust to", "sensitive to",
        ))
        if exception_language:
            # Check if exceptions are properly discussed
            discussion_of_exception = any(kw in context_lower for kw in (
                "due to", "because", "likely because", "driven by",
                "smaller sample", "power", "attenuation",
            ))
            if not discussion_of_exception:
                issues.append(FigureIssue(
                    rule_name="robustness_exceptions",
                    severity="minor",
                    description=(
                        f"{figure.figure_id}: Some robustness checks appear "
                        f"to not support main results, but the reasons are "
                        f"not discussed."
                    ),
                    suggestion=(
                        "Discuss why certain specifications yield different "
                        "results (e.g., smaller sample, measurement error)."
                    ),
                    confidence=0.6,
                ))

        # Rule 3: Number of specifications
        spec_count = re.search(
            r"(\d+)\s*(?:specification|model|regression|check|test)",
            context_lower,
        )
        if spec_count:
            n_specs = int(spec_count.group(1))
            if n_specs < 3:
                issues.append(FigureIssue(
                    rule_name="robustness_breadth",
                    severity="suggestion",
                    description=(
                        f"{figure.figure_id}: Only {n_specs} specifications "
                        f"shown for robustness. Consider more diverse checks."
                    ),
                    confidence=0.4,
                ))

        return issues

    # ------------------------------------------------------------------
    # Scatter/Binscatter Analysis
    # ------------------------------------------------------------------

    def analyze_scatter(
        self, figure: FigureReference, paper_text: str
    ) -> list[FigureIssue]:
        """Check scatter plot / binscatter figures.

        Rules:
          1. Axes should be labeled / described
          2. Fit line type should be stated (linear/nonparametric)
          3. For binscatter: number of bins and controls mentioned
        """
        issues: list[FigureIssue] = []
        context = self._build_context(figure, paper_text)
        context_lower = context.lower()

        # Rule 1: For binscatter, controls mentioned
        if figure.figure_type == FigureType.BINSCATTER:
            controls_mentioned = any(kw in context_lower for kw in (
                "residualized", "controlling for", "conditional on",
                "partialling out", "absorbing", "after removing",
            ))
            if not controls_mentioned:
                issues.append(FigureIssue(
                    rule_name="binscatter_controls",
                    severity="minor",
                    description=(
                        f"{figure.figure_id}: Binscatter plot shown but does "
                        f"not state what variables are controlled for "
                        f"(residualized out)."
                    ),
                    suggestion=(
                        "State which covariates are partialled out "
                        "before binning (following Cattaneo et al. 2024 recommendations)."
                    ),
                    confidence=0.6,
                ))

            # Number of bins
            bins_mentioned = re.search(r"(\d+)\s*bin", context_lower)
            if not bins_mentioned:
                issues.append(FigureIssue(
                    rule_name="binscatter_bins",
                    severity="suggestion",
                    description=(
                        f"{figure.figure_id}: Number of bins in binscatter "
                        f"not reported."
                    ),
                    suggestion="Report the number of bins used (typically 20-50).",
                    confidence=0.5,
                ))

        return issues

    # ------------------------------------------------------------------
    # Distribution Analysis
    # ------------------------------------------------------------------

    def analyze_distribution(
        self, figure: FigureReference, paper_text: str
    ) -> list[FigureIssue]:
        """Check distribution figures (histogram/density/CDF)."""
        issues: list[FigureIssue] = []
        context = self._build_context(figure, paper_text)
        context_lower = context.lower()

        # Rule: If showing treatment vs control distributions,
        # check for overlap discussion
        comparison = any(kw in context_lower for kw in (
            "treatment", "control", "comparison", "overlap",
            "common support", "propensity",
        ))
        if comparison:
            overlap_discussed = any(kw in context_lower for kw in (
                "overlap", "common support", "sufficient overlap",
                "lack of overlap", "trimming",
            ))
            if not overlap_discussed:
                issues.append(FigureIssue(
                    rule_name="distribution_overlap",
                    severity="minor",
                    description=(
                        f"{figure.figure_id}: Distribution comparison shown "
                        f"but overlap/common support not discussed."
                    ),
                    suggestion=(
                        "Discuss the degree of overlap between distributions "
                        "and implications for identification."
                    ),
                    confidence=0.5,
                ))

        return issues

    # ------------------------------------------------------------------
    # Time Series Analysis
    # ------------------------------------------------------------------

    def analyze_time_series(
        self, figure: FigureReference, paper_text: str
    ) -> list[FigureIssue]:
        """Check time series figures."""
        issues: list[FigureIssue] = []
        context = self._build_context(figure, paper_text)
        context_lower = context.lower()

        # Rule: Structural breaks should be noted
        structural_break_visible = any(kw in context_lower for kw in (
            "break", "shift", "regime change", "structural change",
            "sudden change", "abrupt",
        ))
        treatment_event = any(kw in context_lower for kw in (
            "policy", "reform", "intervention", "treatment",
            "event", "shock",
        ))

        if treatment_event and not structural_break_visible:
            issues.append(FigureIssue(
                rule_name="time_series_break",
                severity="suggestion",
                description=(
                    f"{figure.figure_id}: Time series figure references a "
                    f"policy/treatment event but does not discuss whether "
                    f"a structural break is visible at the event date."
                ),
                confidence=0.4,
            ))

        return issues

    # ------------------------------------------------------------------
    # General Checks (apply to all figures)
    # ------------------------------------------------------------------

    def _general_checks(
        self, figure: FigureReference, paper_text: str
    ) -> list[FigureIssue]:
        """General checks applicable to all figure types.

        Rules:
          1. Figure should be discussed in text (not orphaned)
          2. Caption should be informative (not too short)
          3. Source/note should be present for data figures
        """
        issues: list[FigureIssue] = []

        # Rule 1: Figure discussed in text
        if not figure.text_mentions:
            issues.append(FigureIssue(
                rule_name="figure_orphan",
                severity="minor",
                description=(
                    f"{figure.figure_id}: Figure caption found but no "
                    f"text references to this figure detected."
                ),
                suggestion="Ensure every figure is referenced and discussed in the text.",
                confidence=0.6,
            ))

        # Rule 2: Caption informativeness
        if figure.caption and len(figure.caption) < 20:
            issues.append(FigureIssue(
                rule_name="figure_caption_short",
                severity="suggestion",
                description=(
                    f"{figure.figure_id}: Caption is very short "
                    f"({len(figure.caption)} chars). Informative captions "
                    f"help readers understand figures independently."
                ),
                suggestion=(
                    "Expand the caption to include: what is plotted, "
                    "data source, sample period, and key takeaway."
                ),
                confidence=0.5,
            ))

        # Rule 3: Very long caption might indicate the figure is doing too much
        if figure.caption and len(figure.caption) > 800:
            issues.append(FigureIssue(
                rule_name="figure_caption_long",
                severity="suggestion",
                description=(
                    f"{figure.figure_id}: Caption is unusually long "
                    f"({len(figure.caption)} chars). This may indicate the "
                    f"figure is too complex or should be split."
                ),
                confidence=0.35,
            ))

        return issues

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def _build_context(
        self, figure: FigureReference, paper_text: str
    ) -> str:
        """Build combined context text for analysis."""
        parts = [figure.caption, figure.notes]
        parts.extend(figure.text_mentions[:10])
        return " ".join(p for p in parts if p)

    def _issues_to_findings(
        self, issues: list[FigureIssue], figure: FigureReference
    ) -> list[Finding]:
        """Convert FigureIssue objects to Finding objects."""
        findings: list[Finding] = []
        for issue in issues:
            findings.append(Finding(
                category="figure",
                severity=issue.severity,
                description=issue.description,
                evidence=issue.evidence,
                suggestion=issue.suggestion,
                location=figure.figure_id,
                confidence=issue.confidence,
                skill_source="figure_semantic_analysis",
            ))
        return findings


# ==============================================================
# Module exports
# ==============================================================

__all__ = [
    "EconFigureAnalyzer",
    "FigureIssue",
]
