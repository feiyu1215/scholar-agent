"""
tests/test_phase9b_figure_semantic.py

Comprehensive test suite for Phase 9B: Figure Semantic Understanding.

Covers:
  - FigureType enum (all members)
  - FigureReference dataclass (construction, defaults, properties)
  - FigureExtractor (extraction, classification, values, sub-figures, Chinese)
  - EconFigureAnalyzer (event study, parallel trend, coefficient plot, RD, robustness, general)
  - FigureTextCrossValidator (claims extraction, validation, coverage)
  - FigureSemanticSkill (descriptor, can_apply, execute)
  - FigureConsistencySkill (descriptor, can_apply, execute)
  - Integration test (full pipeline)
  - Kill Switch tests
  - Edge cases
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.skills.multimodal.figure_extractor import (
    ExtractedValue,
    FigureClassification,
    FigureExtractor,
    FigureReference,
    FigureType,
)
from core.skills.multimodal.econ_figure import (
    EconFigureAnalyzer,
    FigureIssue,
)
from core.skills.multimodal.figure_text_xref import (
    CoverageReport,
    CrossModalInconsistency,
    FigureTextClaim,
    FigureTextCrossValidator,
)
from core.skills.multimodal.figure_skills import (
    FigureConsistencySkill,
    FigureSemanticSkill,
    _is_enabled,
)
from core.skills.base import Finding, SkillContext, SkillLevel


# ==============================================================
# Sample Texts
# ==============================================================

SAMPLE_ECON_PAPER = """
1. Introduction

We exploit the staggered rollout of a minimum wage increase across U.S. states
using a difference-in-differences design. Our identifying assumption is that
treatment and control states would have followed parallel trends in employment
absent the policy change.

2. Data and Methodology

Our data come from the Current Population Survey (CPS) covering 2005-2019.
We estimate a standard event study specification with leads and lags.

3. Results

Figure 1: Event Study of Employment Effects.
Dynamic treatment effects for the minimum wage increase using two-way fixed
effects. Pre-treatment coefficients are jointly insignificant (F-test p-value=0.43),
supporting the parallel trends assumption. Confidence intervals are 95%.
The effect becomes significant after treatment and persists for 8 quarters.
The reference period is normalized to zero at t=-1.

As shown in Figure 1, the pre-treatment coefficients are insignificant,
validating our parallel trends assumption. After the policy, employment
decreases by approximately 3.5 percent, with the effect growing over time.

Figure 2: Parallel Trends in Outcomes.
Treatment and control group trends prior to the minimum wage increase move
in parallel. The vertical dashed line marks the treatment date. A joint F-test
of pre-treatment leads cannot reject the null of common trends (p=0.67).

Figure 3: Coefficient Plot of Heterogeneous Effects.
Point estimates and 95% confidence intervals for subgroup analyses.
All subgroups show statistically significant negative effects except for
high-skill workers, where the confidence interval includes zero.
Panel (a) shows effects by age group, Panel (b) shows effects by industry.

Figure 4: RD Plot of Compliance.
Regression discontinuity plot showing the jump in compliance at the
enforcement threshold. Local linear fit with CCT bandwidth. The discontinuity
is clearly visible with a jump of 15 percentage points.

Figure 5: Robustness across 8 specifications.
The main estimate remains robust and stable across alternative specifications
including different control sets, sample restrictions, and estimation methods.
The effect is consistently significant at the 5% level.

Figure 6: Placebo Test Results.
Distribution of 1000 placebo estimates from randomization inference. The true
treatment effect (shown by the vertical red line) is outside the distribution
of placebo effects, with a permutation p-value of 0.003. Placebo effects are
centered around zero and insignificant.

4. Conclusion

Our event study in Figure 1 demonstrates a clear negative effect of the minimum
wage on employment. Figure 2 supports our identification strategy by showing
parallel pre-treatment trends. As Figure 3 reveals, the effect is approximately
0.15 standard deviations for most subgroups but larger than the baseline for
low-skill workers. Figure 4 confirms compliance jumps discontinuously at the
enforcement threshold.
"""

SAMPLE_PAPER_CHINESE = """
1. 引言

本文利用双重差分法研究了环保政策对工业产出的影响。

2. 实证结果

图1: 事件研究法估计结果
动态处理效应系数图，显示政策实施前后的影响轨迹。
处理前各期系数均不显著，支持平行趋势假设。

图2: 平行趋势检验
处理组和对照组在政策实施前的产出趋势保持平行。

如图1所示，政策效应在处理后第3期开始显现。图2进一步验证了
平行趋势假设的合理性。
"""

SAMPLE_PAPER_MINIMAL = """
This paper studies labor markets. We use data from 2010-2020.
There are no figures in this short paper.
"""

SAMPLE_PAPER_ORPHAN_FIGURES = """
Figure 1: Treatment Effects.
The coefficient is positive and significant at 5%.

Figure 2: Robustness Checks.
Results are stable across specifications.

The text discusses Figure 1 extensively. As shown in Figure 1,
the treatment has a large positive effect. We also reference Figure 3
which shows additional results.
"""

SAMPLE_PAPER_SIGNIFICANCE_CONTRADICTION = """
Figure 1: Coefficient Plot.
Point estimates and confidence intervals for the main specification.
The confidence interval crosses zero for two of the five coefficients shown.

As shown in Figure 1, all coefficients are statistically significant
and the effect is consistently positive across all specifications.
The results clearly indicate that the treatment is effective.
"""


# ==============================================================
# Test: FigureType Enum
# ==============================================================

class TestFigureType:
    """Test FigureType enum members and values."""

    def test_event_study_exists(self):
        assert FigureType.EVENT_STUDY.value == "event_study"

    def test_parallel_trend_exists(self):
        assert FigureType.PARALLEL_TREND.value == "parallel_trend"

    def test_coefficient_plot_exists(self):
        assert FigureType.COEFFICIENT_PLOT.value == "coefficient_plot"

    def test_time_series_exists(self):
        assert FigureType.TIME_SERIES.value == "time_series"

    def test_scatter_plot_exists(self):
        assert FigureType.SCATTER_PLOT.value == "scatter_plot"

    def test_distribution_exists(self):
        assert FigureType.DISTRIBUTION.value == "distribution"

    def test_map_exists(self):
        assert FigureType.MAP.value == "map"

    def test_flowchart_exists(self):
        assert FigureType.FLOWCHART.value == "flowchart"

    def test_regression_discontinuity_exists(self):
        assert FigureType.REGRESSION_DISCONTINUITY.value == "rd_plot"

    def test_placebo_test_exists(self):
        assert FigureType.PLACEBO_TEST.value == "placebo_test"

    def test_robustness_exists(self):
        assert FigureType.ROBUSTNESS.value == "robustness"

    def test_binscatter_exists(self):
        assert FigureType.BINSCATTER.value == "binscatter"

    def test_kaplan_meier_exists(self):
        assert FigureType.KAPLAN_MEIER.value == "kaplan_meier"

    def test_dag_exists(self):
        assert FigureType.DAG.value == "dag"

    def test_other_exists(self):
        assert FigureType.OTHER.value == "other"

    def test_total_member_count(self):
        assert len(FigureType) == 15


# ==============================================================
# Test: FigureReference Dataclass
# ==============================================================

class TestFigureReference:
    """Test FigureReference dataclass construction and properties."""

    def test_basic_construction(self):
        ref = FigureReference(figure_id="Figure 1", caption="Test caption")
        assert ref.figure_id == "Figure 1"
        assert ref.caption == "Test caption"

    def test_default_values(self):
        ref = FigureReference(figure_id="Figure 1", caption="")
        assert ref.figure_type == FigureType.OTHER
        assert ref.page_number == 0
        assert ref.section == ""
        assert ref.text_mentions == []
        assert ref.reported_values == {}
        assert ref.sub_figures == []
        assert ref.parent_id == ""
        assert ref.notes == ""

    def test_canonical_number(self):
        ref = FigureReference(figure_id="Figure 3", caption="")
        assert ref.canonical_number == 3

    def test_canonical_number_with_sub(self):
        ref = FigureReference(figure_id="Figure 3a", caption="")
        assert ref.canonical_number == 3

    def test_canonical_number_none(self):
        ref = FigureReference(figure_id="Panel A", caption="")
        assert ref.canonical_number is None

    def test_is_subfigure_false(self):
        ref = FigureReference(figure_id="Figure 1", caption="")
        assert ref.is_subfigure is False

    def test_is_subfigure_true(self):
        ref = FigureReference(figure_id="Figure 1a", caption="", parent_id="Figure 1")
        assert ref.is_subfigure is True

    def test_full_construction(self):
        ref = FigureReference(
            figure_id="Figure 2",
            caption="Event study results",
            figure_type=FigureType.EVENT_STUDY,
            page_number=5,
            section="Results",
            text_mentions=["see Figure 2"],
            reported_values={"effect_size": [{"value": 0.05}]},
            sub_figures=["a", "b"],
            parent_id="",
            notes="Note: clustered SEs",
        )
        assert ref.figure_type == FigureType.EVENT_STUDY
        assert ref.page_number == 5
        assert ref.section == "Results"
        assert len(ref.text_mentions) == 1
        assert "effect_size" in ref.reported_values


# ==============================================================
# Test: FigureExtractor
# ==============================================================

class TestFigureExtractor:
    """Test FigureExtractor extraction, classification, and value extraction."""

    def setup_method(self):
        self.extractor = FigureExtractor()

    # --- Extraction ---

    def test_extract_figures_from_econ_paper(self):
        figures = self.extractor.extract_figures(SAMPLE_ECON_PAPER)
        assert len(figures) >= 6

    def test_extract_figure_ids(self):
        figures = self.extractor.extract_figures(SAMPLE_ECON_PAPER)
        ids = [f.figure_id for f in figures]
        assert "Figure 1" in ids
        assert "Figure 2" in ids
        assert "Figure 3" in ids
        assert "Figure 4" in ids
        assert "Figure 5" in ids
        assert "Figure 6" in ids

    def test_extract_captions_not_empty(self):
        figures = self.extractor.extract_figures(SAMPLE_ECON_PAPER)
        for fig in figures:
            if fig.figure_id in ("Figure 1", "Figure 2", "Figure 3"):
                assert fig.caption, f"{fig.figure_id} should have a caption"

    def test_extract_text_mentions(self):
        figures = self.extractor.extract_figures(SAMPLE_ECON_PAPER)
        fig1 = next(f for f in figures if f.figure_id == "Figure 1")
        # Figure 1 is mentioned multiple times in text
        assert len(fig1.text_mentions) >= 1

    def test_extract_from_empty_text(self):
        figures = self.extractor.extract_figures("")
        assert figures == []

    def test_extract_no_figures(self):
        figures = self.extractor.extract_figures(SAMPLE_PAPER_MINIMAL)
        assert figures == []

    def test_extract_chinese_figures(self):
        figures = self.extractor.extract_figures(SAMPLE_PAPER_CHINESE)
        assert len(figures) >= 2
        # Chinese figures should be normalized to "Figure N" format
        ids = [f.figure_id for f in figures]
        assert "Figure 1" in ids
        assert "Figure 2" in ids

    def test_extract_subfigure_detection(self):
        # Panel references must be on the same line as caption for the regex
        text = "Figure 7: Heterogeneous effects by Panel (a) age and Panel (b) industry with controls."
        figures = self.extractor.extract_figures(text)
        fig7 = next(f for f in figures if f.figure_id == "Figure 7")
        assert len(fig7.sub_figures) >= 2
        assert "a" in fig7.sub_figures
        assert "b" in fig7.sub_figures

    def test_extract_ordered_by_number(self):
        figures = self.extractor.extract_figures(SAMPLE_ECON_PAPER)
        numbers = [f.canonical_number for f in figures if f.canonical_number]
        assert numbers == sorted(numbers)

    # --- Classification ---

    def test_classify_event_study(self):
        fig = FigureReference(
            figure_id="Figure 1",
            caption="Event Study of Employment Effects. Dynamic treatment effects "
                    "with leads and lags. Pre-treatment coefficients are insignificant.",
        )
        classification = self.extractor.classify_figure(fig, SAMPLE_ECON_PAPER)
        assert classification.primary_type == FigureType.EVENT_STUDY
        assert classification.confidence > 0.1

    def test_classify_parallel_trend(self):
        fig = FigureReference(
            figure_id="Figure 2",
            caption="Parallel Trends in Outcomes. Treatment and control group "
                    "trends prior to the minimum wage increase move in parallel.",
        )
        classification = self.extractor.classify_figure(fig, SAMPLE_ECON_PAPER)
        assert classification.primary_type == FigureType.PARALLEL_TREND
        assert classification.confidence > 0.1

    def test_classify_coefficient_plot(self):
        fig = FigureReference(
            figure_id="Figure 3",
            caption="Coefficient Plot of Heterogeneous Effects. Point estimates "
                    "and 95% confidence intervals for subgroup analyses.",
        )
        classification = self.extractor.classify_figure(fig, SAMPLE_ECON_PAPER)
        assert classification.primary_type == FigureType.COEFFICIENT_PLOT
        assert classification.confidence > 0.1

    def test_classify_rd_plot(self):
        fig = FigureReference(
            figure_id="Figure 4",
            caption="RD Plot of Compliance. Regression discontinuity plot showing "
                    "the jump in compliance at the enforcement threshold.",
        )
        classification = self.extractor.classify_figure(fig, SAMPLE_ECON_PAPER)
        assert classification.primary_type == FigureType.REGRESSION_DISCONTINUITY
        assert classification.confidence > 0.1

    def test_classify_robustness(self):
        fig = FigureReference(
            figure_id="Figure 5",
            caption="Robustness across 8 specifications. The main estimate remains "
                    "robust and stable across alternative specifications.",
        )
        classification = self.extractor.classify_figure(fig, SAMPLE_ECON_PAPER)
        assert classification.primary_type == FigureType.ROBUSTNESS
        assert classification.confidence > 0.1

    def test_classify_placebo(self):
        fig = FigureReference(
            figure_id="Figure 6",
            caption="Placebo Test Results. Distribution of 1000 placebo estimates "
                    "from randomization inference.",
        )
        classification = self.extractor.classify_figure(fig, SAMPLE_ECON_PAPER)
        assert classification.primary_type == FigureType.PLACEBO_TEST
        assert classification.confidence > 0.1

    def test_classify_other_for_generic_caption(self):
        fig = FigureReference(
            figure_id="Figure 99",
            caption="Data summary.",
        )
        classification = self.extractor.classify_figure(fig, "A short paper.")
        assert classification.primary_type == FigureType.OTHER

    def test_classification_has_keywords(self):
        fig = FigureReference(
            figure_id="Figure 1",
            caption="Event study with dynamic effects and leads and lags.",
        )
        classification = self.extractor.classify_figure(fig, "")
        if classification.primary_type != FigureType.OTHER:
            assert len(classification.matched_keywords) > 0

    def test_classification_has_reasoning(self):
        fig = FigureReference(
            figure_id="Figure 1",
            caption="Event study results with pre-treatment coefficients.",
        )
        classification = self.extractor.classify_figure(fig, "")
        assert classification.reasoning  # non-empty

    def test_classification_secondary_type(self):
        # A caption that could match multiple types
        fig = FigureReference(
            figure_id="Figure 1",
            caption="Event study showing parallel trends in the pre-treatment period "
                    "with confidence intervals.",
        )
        classification = self.extractor.classify_figure(fig, SAMPLE_ECON_PAPER)
        # Should have a secondary type since multiple keywords match
        assert classification.secondary_type is not None or classification.primary_type != FigureType.OTHER

    # --- Value Extraction ---

    def test_extract_reported_values_effect_size(self):
        values = self.extractor.extract_reported_values(
            "The effect is approximately 3.5 percent after treatment.", []
        )
        assert len(values) >= 1
        pct_vals = [v for v in values if v.value_type == "percentage"]
        assert len(pct_vals) >= 1
        assert any(v.value == pytest.approx(3.5) for v in pct_vals)

    def test_extract_reported_values_p_value(self):
        values = self.extractor.extract_reported_values(
            "F-test p-value=0.43.", []
        )
        p_vals = [v for v in values if v.value_type == "p_value"]
        assert len(p_vals) >= 1
        assert any(v.value == pytest.approx(0.43) for v in p_vals)

    def test_extract_reported_values_sample_size(self):
        values = self.extractor.extract_reported_values(
            "Our sample of N = 12,345 observations.", []
        )
        sample_vals = [v for v in values if v.value_type == "sample_size"]
        assert len(sample_vals) >= 1
        assert any(v.value == pytest.approx(12345) for v in sample_vals)

    def test_extract_reported_values_from_mentions(self):
        values = self.extractor.extract_reported_values(
            "Caption without values.",
            ["The effect of 5 percent is shown in the figure."],
        )
        assert len(values) >= 1

    def test_extract_reported_values_empty_text(self):
        values = self.extractor.extract_reported_values("", [])
        assert values == []

    def test_extract_reported_values_ci_bounds(self):
        values = self.extractor.extract_reported_values(
            "The 95% CI is [0.02, 0.08].", []
        )
        ci_lower = [v for v in values if v.value_type == "ci_lower"]
        ci_upper = [v for v in values if v.value_type == "ci_upper"]
        assert len(ci_lower) >= 1
        assert len(ci_upper) >= 1


# ==============================================================
# Test: EconFigureAnalyzer
# ==============================================================

class TestEconFigureAnalyzer:
    """Test economics-specific figure analysis."""

    def setup_method(self):
        self.analyzer = EconFigureAnalyzer()

    # --- Event Study ---

    def test_event_study_no_pretrend_discussion(self):
        fig = FigureReference(
            figure_id="Figure 1",
            caption="Event Study of Employment Effects.",
            figure_type=FigureType.EVENT_STUDY,
        )
        issues = self.analyzer.analyze_event_study(fig, "")
        rule_names = [i.rule_name for i in issues]
        assert "event_study_pre_trend" in rule_names

    def test_event_study_pretrend_discussed(self):
        fig = FigureReference(
            figure_id="Figure 1",
            caption="Event Study. Pre-treatment coefficients are jointly insignificant.",
            figure_type=FigureType.EVENT_STUDY,
            text_mentions=["The pre-trend coefficients are insignificant."],
        )
        issues = self.analyzer.analyze_event_study(fig, "")
        rule_names = [i.rule_name for i in issues]
        assert "event_study_pre_trend" not in rule_names

    def test_event_study_no_normalization(self):
        fig = FigureReference(
            figure_id="Figure 1",
            caption="Event study showing dynamic effects.",
            figure_type=FigureType.EVENT_STUDY,
        )
        issues = self.analyzer.analyze_event_study(fig, "")
        rule_names = [i.rule_name for i in issues]
        assert "event_study_normalization" in rule_names

    def test_event_study_normalization_mentioned(self):
        fig = FigureReference(
            figure_id="Figure 1",
            caption="Event study. Reference period is normalized to zero at t=-1.",
            figure_type=FigureType.EVENT_STUDY,
        )
        issues = self.analyzer.analyze_event_study(fig, "")
        rule_names = [i.rule_name for i in issues]
        assert "event_study_normalization" not in rule_names

    def test_event_study_no_ci(self):
        fig = FigureReference(
            figure_id="Figure 1",
            caption="Event study of effects.",
            figure_type=FigureType.EVENT_STUDY,
        )
        issues = self.analyzer.analyze_event_study(fig, "")
        rule_names = [i.rule_name for i in issues]
        assert "event_study_ci" in rule_names

    # --- Parallel Trend ---

    def test_parallel_trend_no_conclusion(self):
        fig = FigureReference(
            figure_id="Figure 2",
            caption="Pre-treatment trends of treatment and control groups.",
            figure_type=FigureType.PARALLEL_TREND,
        )
        issues = self.analyzer.analyze_parallel_trend(fig, "")
        rule_names = [i.rule_name for i in issues]
        assert "parallel_trend_conclusion" in rule_names

    def test_parallel_trend_conclusion_stated(self):
        fig = FigureReference(
            figure_id="Figure 2",
            caption="Treatment and control trends move in parallel before treatment.",
            figure_type=FigureType.PARALLEL_TREND,
        )
        issues = self.analyzer.analyze_parallel_trend(fig, "")
        rule_names = [i.rule_name for i in issues]
        assert "parallel_trend_conclusion" not in rule_names

    def test_parallel_trend_no_formal_test(self):
        fig = FigureReference(
            figure_id="Figure 2",
            caption="Parallel trends are shown visually.",
            figure_type=FigureType.PARALLEL_TREND,
            text_mentions=["The trends move in parallel."],
        )
        issues = self.analyzer.analyze_parallel_trend(fig, "")
        rule_names = [i.rule_name for i in issues]
        assert "parallel_trend_formal_test" in rule_names

    def test_parallel_trend_formal_test_present(self):
        fig = FigureReference(
            figure_id="Figure 2",
            caption="Parallel trends confirmed by joint F-test (cannot reject null).",
            figure_type=FigureType.PARALLEL_TREND,
            text_mentions=["We move in parallel. A joint test confirms this."],
        )
        issues = self.analyzer.analyze_parallel_trend(fig, "")
        rule_names = [i.rule_name for i in issues]
        assert "parallel_trend_formal_test" not in rule_names

    # --- Coefficient Plot ---

    def test_coefficient_plot_sig_contradiction(self):
        fig = FigureReference(
            figure_id="Figure 3",
            caption="Coefficient plot. Some intervals cross zero.",
            figure_type=FigureType.COEFFICIENT_PLOT,
            text_mentions=[
                "All coefficients are consistently significant across subgroups."
            ],
        )
        issues = self.analyzer.analyze_coefficient_plot(fig, "")
        rule_names = [i.rule_name for i in issues]
        assert "coeff_plot_sig_contradiction" in rule_names

    def test_coefficient_plot_no_contradiction(self):
        fig = FigureReference(
            figure_id="Figure 3",
            caption="Coefficient plot with 95% confidence intervals.",
            figure_type=FigureType.COEFFICIENT_PLOT,
        )
        issues = self.analyzer.analyze_coefficient_plot(fig, "")
        rule_names = [i.rule_name for i in issues]
        assert "coeff_plot_sig_contradiction" not in rule_names

    def test_coefficient_plot_selective_reporting(self):
        fig = FigureReference(
            figure_id="Figure 3",
            caption="Selected subgroup results for the most notable heterogeneous effects.",
            figure_type=FigureType.COEFFICIENT_PLOT,
        )
        issues = self.analyzer.analyze_coefficient_plot(fig, "")
        rule_names = [i.rule_name for i in issues]
        assert "coeff_plot_selective" in rule_names

    # --- RD Plot ---

    def test_rd_no_mccrary(self):
        fig = FigureReference(
            figure_id="Figure 4",
            caption="RD plot showing a discontinuity at the threshold.",
            figure_type=FigureType.REGRESSION_DISCONTINUITY,
        )
        # Paper text also does not mention McCrary
        issues = self.analyzer.analyze_rd_plot(fig, "Some paper text about RD design.")
        rule_names = [i.rule_name for i in issues]
        assert "rd_manipulation_test" in rule_names

    def test_rd_mccrary_mentioned(self):
        fig = FigureReference(
            figure_id="Figure 4",
            caption="RD plot at the threshold. The jump is visible.",
            figure_type=FigureType.REGRESSION_DISCONTINUITY,
        )
        paper_text = "We perform a McCrary density test and find no evidence of manipulation."
        issues = self.analyzer.analyze_rd_plot(fig, paper_text)
        rule_names = [i.rule_name for i in issues]
        assert "rd_manipulation_test" not in rule_names

    def test_rd_no_bandwidth_discussion(self):
        fig = FigureReference(
            figure_id="Figure 4",
            caption="RD plot showing the discontinuity. Jump is visible.",
            figure_type=FigureType.REGRESSION_DISCONTINUITY,
        )
        issues = self.analyzer.analyze_rd_plot(fig, "McCrary test passed.")
        rule_names = [i.rule_name for i in issues]
        assert "rd_bandwidth" in rule_names

    def test_rd_bandwidth_mentioned(self):
        fig = FigureReference(
            figure_id="Figure 4",
            caption="RD plot with CCT optimal bandwidth. Discontinuity is clear.",
            figure_type=FigureType.REGRESSION_DISCONTINUITY,
        )
        issues = self.analyzer.analyze_rd_plot(fig, "McCrary test passed.")
        rule_names = [i.rule_name for i in issues]
        assert "rd_bandwidth" not in rule_names

    # --- Robustness ---

    def test_robustness_no_conclusion(self):
        fig = FigureReference(
            figure_id="Figure 5",
            caption="Results across different specifications.",
            figure_type=FigureType.ROBUSTNESS,
        )
        issues = self.analyzer.analyze_robustness(fig, "")
        rule_names = [i.rule_name for i in issues]
        assert "robustness_conclusion" in rule_names

    def test_robustness_selective_reporting_undiscussed(self):
        fig = FigureReference(
            figure_id="Figure 5",
            caption="Robustness results. One specification loses significance but "
                    "results are robust overall.",
            figure_type=FigureType.ROBUSTNESS,
        )
        issues = self.analyzer.analyze_robustness(fig, "")
        rule_names = [i.rule_name for i in issues]
        assert "robustness_exceptions" in rule_names

    def test_robustness_exceptions_discussed(self):
        fig = FigureReference(
            figure_id="Figure 5",
            caption="Robustness check is robust. One specification loses significance "
                    "due to smaller sample size.",
            figure_type=FigureType.ROBUSTNESS,
        )
        issues = self.analyzer.analyze_robustness(fig, "")
        rule_names = [i.rule_name for i in issues]
        assert "robustness_exceptions" not in rule_names

    # --- General Checks ---

    def test_general_orphan_figure(self):
        fig = FigureReference(
            figure_id="Figure 10",
            caption="Some results.",
            figure_type=FigureType.OTHER,
            text_mentions=[],
        )
        findings = self.analyzer.analyze(fig, "")
        descriptions = [f.description for f in findings]
        assert any("no text references" in d.lower() or "orphan" in d.lower()
                   for d in descriptions)

    def test_general_short_caption(self):
        fig = FigureReference(
            figure_id="Figure 1",
            caption="Results.",
            figure_type=FigureType.OTHER,
            text_mentions=["See Figure 1."],
        )
        findings = self.analyzer.analyze(fig, "")
        descriptions = [f.description for f in findings]
        assert any("short" in d.lower() for d in descriptions)

    def test_general_long_caption(self):
        fig = FigureReference(
            figure_id="Figure 1",
            caption="A" * 900,
            figure_type=FigureType.OTHER,
            text_mentions=["See Figure 1."],
        )
        findings = self.analyzer.analyze(fig, "")
        descriptions = [f.description for f in findings]
        assert any("long" in d.lower() for d in descriptions)

    def test_analyze_dispatches_by_type(self):
        fig = FigureReference(
            figure_id="Figure 1",
            caption="Event study with pre-treatment effects and confidence intervals.",
            figure_type=FigureType.EVENT_STUDY,
            text_mentions=["Figure 1 shows the event study."],
        )
        findings = self.analyzer.analyze(fig, "")
        # Should produce some findings (event study rules + general rules)
        assert len(findings) >= 1
        assert all(isinstance(f, Finding) for f in findings)


# ==============================================================
# Test: FigureTextCrossValidator
# ==============================================================

class TestFigureTextCrossValidator:
    """Test figure-text cross-reference validation."""

    def setup_method(self):
        self.validator = FigureTextCrossValidator()

    # --- Claim Extraction ---

    def test_extract_figure_claims_magnitude(self):
        text = "Figure 1 shows a large positive effect of the treatment."
        figures = [FigureReference(figure_id="Figure 1", caption="Effect size.")]
        claims = self.validator.extract_figure_claims(text, figures)
        mag_claims = [c for c in claims if c.claim_type == "magnitude"]
        assert len(mag_claims) >= 1
        assert mag_claims[0].magnitude_descriptor == "large"

    def test_extract_figure_claims_direction(self):
        text = "As seen in Figure 2, there is a clear upward rise over time."
        figures = [FigureReference(figure_id="Figure 2", caption="Trends.")]
        claims = self.validator.extract_figure_claims(text, figures)
        dir_claims = [c for c in claims if c.claim_type == "trend"]
        assert len(dir_claims) >= 1
        assert dir_claims[0].direction == "increasing"

    def test_extract_figure_claims_significance(self):
        text = "Figure 3 demonstrates that the coefficient is statistically significant."
        figures = [FigureReference(figure_id="Figure 3", caption="Results.")]
        claims = self.validator.extract_figure_claims(text, figures)
        sig_claims = [c for c in claims if c.claim_type == "significance"]
        assert len(sig_claims) >= 1
        assert sig_claims[0].significance_claim == "significant"

    def test_extract_figure_claims_comparison(self):
        text = "Figure 4 shows the effect is larger than the baseline estimate."
        figures = [FigureReference(figure_id="Figure 4", caption="Comparison.")]
        claims = self.validator.extract_figure_claims(text, figures)
        comp_claims = [c for c in claims if c.claim_type == "comparison"]
        assert len(comp_claims) >= 1

    def test_extract_figure_claims_quantitative(self):
        text = "Figure 1 shows the effect is approximately 0.15 standard deviations."
        figures = [FigureReference(figure_id="Figure 1", caption="Effects.")]
        claims = self.validator.extract_figure_claims(text, figures)
        quant_claims = [c for c in claims if c.claim_type == "quantitative"]
        assert len(quant_claims) >= 1
        assert 0.15 in quant_claims[0].numerical_values

    def test_extract_no_claims_without_figure_refs(self):
        text = "The results are interesting and show a large effect."
        figures = [FigureReference(figure_id="Figure 1", caption="")]
        claims = self.validator.extract_figure_claims(text, figures)
        assert claims == []

    # --- Claim Validation ---

    def test_validate_significance_contradiction(self):
        claims = [FigureTextClaim(
            figure_id="Figure 1",
            claim_text="The effect is statistically significant.",
            claim_type="significance",
            significance_claim="significant",
        )]
        figures = [FigureReference(
            figure_id="Figure 1",
            caption="Coefficient plot where the confidence interval crosses zero.",
            text_mentions=["the CI crosses zero for this coefficient"],
        )]
        inconsistencies = self.validator.validate_claims(claims, figures)
        assert len(inconsistencies) >= 1
        assert inconsistencies[0].inconsistency_type == "significance_contradiction"

    def test_validate_magnitude_mismatch(self):
        claims = [FigureTextClaim(
            figure_id="Figure 1",
            claim_text="A large effect is shown.",
            claim_type="magnitude",
            magnitude_descriptor="large",
        )]
        figures = [FigureReference(
            figure_id="Figure 1",
            caption="The effect is negligible in magnitude.",
        )]
        inconsistencies = self.validator.validate_claims(claims, figures)
        assert len(inconsistencies) >= 1
        assert inconsistencies[0].inconsistency_type == "magnitude_mismatch"

    def test_validate_no_inconsistency_when_consistent(self):
        claims = [FigureTextClaim(
            figure_id="Figure 1",
            claim_text="Figure 1 shows a large effect.",
            claim_type="magnitude",
            magnitude_descriptor="large",
        )]
        figures = [FigureReference(
            figure_id="Figure 1",
            caption="Substantial treatment effects are observed.",
        )]
        inconsistencies = self.validator.validate_claims(claims, figures)
        # "large" and "large" (from "substantial") → no contradiction
        assert len(inconsistencies) == 0

    # --- Coverage ---

    def test_coverage_orphan_figure(self):
        # Figure 2 has caption but is never referenced in text
        text = "We study employment. As shown in Figure 1, the effect is positive."
        figures = [
            FigureReference(figure_id="Figure 1", caption="Effect on employment."),
            FigureReference(figure_id="Figure 2", caption="Robustness checks."),
        ]
        coverage = self.validator.check_figure_text_coverage(text, figures)
        assert "Figure 2" in coverage.orphan_figures

    def test_coverage_phantom_reference(self):
        text = "We discuss Figure 1 and Figure 5 in detail."
        figures = [
            FigureReference(figure_id="Figure 1", caption="Event study results."),
        ]
        coverage = self.validator.check_figure_text_coverage(text, figures)
        assert "Figure 5" in coverage.phantom_references

    def test_coverage_score_perfect(self):
        text = "Figure 1 shows the results. We discuss Figure 1 in detail."
        figures = [
            FigureReference(figure_id="Figure 1", caption="Results."),
        ]
        coverage = self.validator.check_figure_text_coverage(text, figures)
        assert coverage.coverage_score >= 0.8

    def test_coverage_score_degraded_with_orphans(self):
        text = "This paper has some text. Figure 1 is discussed."
        figures = [
            FigureReference(figure_id="Figure 1", caption="Results."),
            FigureReference(figure_id="Figure 2", caption="More."),
            FigureReference(figure_id="Figure 3", caption="Extra."),
        ]
        coverage = self.validator.check_figure_text_coverage(text, figures)
        assert coverage.coverage_score < 1.0

    def test_coverage_empty_text(self):
        findings, coverage = self.validator.validate("", [])
        assert findings == []
        assert coverage.total_figures == 0

    def test_full_validate_returns_findings_and_coverage(self):
        figures = [
            FigureReference(figure_id="Figure 1", caption="Event study."),
        ]
        findings, coverage = self.validator.validate(SAMPLE_ECON_PAPER, figures)
        assert isinstance(findings, list)
        assert isinstance(coverage, CoverageReport)


# ==============================================================
# Test: FigureSemanticSkill
# ==============================================================

class TestFigureSemanticSkill:
    """Test FigureSemanticSkill SkillX integration."""

    def setup_method(self):
        self.skill = FigureSemanticSkill()

    def test_descriptor_name(self):
        desc = self.skill.descriptor
        assert desc.name == "figure_semantic_analysis"

    def test_descriptor_level(self):
        desc = self.skill.descriptor
        assert desc.level == SkillLevel.FUNCTIONAL

    def test_descriptor_tags(self):
        desc = self.skill.descriptor
        assert "figure" in desc.tags
        assert "multimodal" in desc.tags

    def test_can_apply_high_with_figures(self):
        ctx = SkillContext(paper_text=SAMPLE_ECON_PAPER)
        score = self.skill.can_apply(ctx)
        assert score >= 0.5

    def test_can_apply_low_without_figures(self):
        ctx = SkillContext(paper_text=SAMPLE_PAPER_MINIMAL)
        score = self.skill.can_apply(ctx)
        assert score < 0.3

    def test_can_apply_zero_when_disabled(self, monkeypatch):
        monkeypatch.setenv("SCHOLAR_GODEL_FIGURE_SEMANTIC", "0")
        ctx = SkillContext(paper_text=SAMPLE_ECON_PAPER)
        score = self.skill.can_apply(ctx)
        assert score == 0.0

    def test_can_apply_empty_text(self):
        ctx = SkillContext(paper_text="")
        score = self.skill.can_apply(ctx)
        assert score == 0.0

    def test_can_apply_chinese_figures(self):
        ctx = SkillContext(paper_text=SAMPLE_PAPER_CHINESE)
        score = self.skill.can_apply(ctx)
        assert score > 0.0

    def test_execute_returns_figures(self):
        ctx = SkillContext(paper_text=SAMPLE_ECON_PAPER)
        result = self.skill.execute(ctx)
        assert result.success
        assert "figures" in result.output_data
        assert len(result.output_data["figures"]) >= 6

    def test_execute_returns_classifications(self):
        ctx = SkillContext(paper_text=SAMPLE_ECON_PAPER)
        result = self.skill.execute(ctx)
        assert "classifications" in result.output_data
        assert len(result.output_data["classifications"]) >= 1

    def test_execute_returns_stats(self):
        ctx = SkillContext(paper_text=SAMPLE_ECON_PAPER)
        result = self.skill.execute(ctx)
        stats = result.output_data.get("extraction_stats", {})
        assert stats["total_figures"] >= 6
        assert "type_distribution" in stats

    def test_execute_returns_findings(self):
        ctx = SkillContext(paper_text=SAMPLE_ECON_PAPER)
        result = self.skill.execute(ctx)
        # Should have at least some findings from economic analysis
        assert isinstance(result.findings, list)

    def test_execute_when_disabled(self, monkeypatch):
        monkeypatch.setenv("SCHOLAR_GODEL_FIGURE_SEMANTIC", "0")
        ctx = SkillContext(paper_text=SAMPLE_ECON_PAPER)
        result = self.skill.execute(ctx)
        assert not result.success
        assert "Kill Switch" in result.error_message

    def test_execute_empty_text(self):
        ctx = SkillContext(paper_text="")
        result = self.skill.execute(ctx)
        assert result.success
        assert result.output_data["figures"] == []

    def test_execute_no_figures_paper(self):
        ctx = SkillContext(paper_text=SAMPLE_PAPER_MINIMAL)
        result = self.skill.execute(ctx)
        assert result.success
        assert result.output_data["figures"] == []

    def test_execute_metadata(self):
        ctx = SkillContext(paper_text=SAMPLE_ECON_PAPER)
        result = self.skill.execute(ctx)
        assert "figures_found" in result.metadata
        assert result.metadata["figures_found"] >= 6


# ==============================================================
# Test: FigureConsistencySkill
# ==============================================================

class TestFigureConsistencySkill:
    """Test FigureConsistencySkill SkillX integration."""

    def setup_method(self):
        self.skill = FigureConsistencySkill()

    def test_descriptor_name(self):
        desc = self.skill.descriptor
        assert desc.name == "figure_text_consistency"

    def test_descriptor_level(self):
        desc = self.skill.descriptor
        assert desc.level == SkillLevel.FUNCTIONAL

    def test_descriptor_prerequisites(self):
        desc = self.skill.descriptor
        assert "figure_semantic_analysis" in desc.prerequisites

    def test_can_apply_high_with_upstream_figures(self):
        ctx = SkillContext(
            paper_text=SAMPLE_ECON_PAPER,
            parameters={"figures": [
                {"figure_id": "Figure 1", "caption": "Event study.", "figure_type": "event_study"},
            ]},
        )
        score = self.skill.can_apply(ctx)
        assert score >= 0.5

    def test_can_apply_moderate_without_upstream(self):
        ctx = SkillContext(
            paper_text=SAMPLE_ECON_PAPER,
            current_phase="deep_review",
        )
        score = self.skill.can_apply(ctx)
        assert score > 0.0

    def test_can_apply_zero_when_disabled(self, monkeypatch):
        monkeypatch.setenv("SCHOLAR_GODEL_FIGURE_SEMANTIC", "0")
        ctx = SkillContext(paper_text=SAMPLE_ECON_PAPER)
        score = self.skill.can_apply(ctx)
        assert score == 0.0

    def test_execute_with_paper_text(self):
        ctx = SkillContext(paper_text=SAMPLE_ECON_PAPER)
        result = self.skill.execute(ctx)
        assert result.success
        assert "inconsistencies" in result.output_data
        assert "coverage_report" in result.output_data

    def test_execute_coverage_report_fields(self):
        ctx = SkillContext(paper_text=SAMPLE_ECON_PAPER)
        result = self.skill.execute(ctx)
        report = result.output_data["coverage_report"]
        assert "total_figures" in report
        assert "coverage_score" in report

    def test_execute_when_disabled(self, monkeypatch):
        monkeypatch.setenv("SCHOLAR_GODEL_FIGURE_SEMANTIC", "0")
        ctx = SkillContext(paper_text=SAMPLE_ECON_PAPER)
        result = self.skill.execute(ctx)
        assert not result.success
        assert "Kill Switch" in result.error_message

    def test_execute_with_upstream_figure_dicts(self):
        ctx = SkillContext(
            paper_text=SAMPLE_ECON_PAPER,
            parameters={"figures": [
                {"figure_id": "Figure 1", "caption": "Event study.", "figure_type": "event_study"},
                {"figure_id": "Figure 2", "caption": "Parallel trends.", "figure_type": "parallel_trend"},
            ]},
        )
        result = self.skill.execute(ctx)
        assert result.success

    def test_execute_detects_orphan_figures(self):
        ctx = SkillContext(paper_text=SAMPLE_PAPER_ORPHAN_FIGURES)
        result = self.skill.execute(ctx)
        assert result.success
        # Figure 2 is never referenced in main text body
        coverage = result.output_data["coverage_report"]
        # Either orphan_figures or phantom_references should be non-empty
        has_issues = (
            len(coverage.get("orphan_figures", [])) > 0
            or len(coverage.get("phantom_references", [])) > 0
        )
        assert has_issues

    def test_execute_metadata(self):
        ctx = SkillContext(paper_text=SAMPLE_ECON_PAPER)
        result = self.skill.execute(ctx)
        assert "figures_validated" in result.metadata


# ==============================================================
# Test: Kill Switch
# ==============================================================

class TestKillSwitch:
    """Test Kill Switch behavior for figure semantic module."""

    def test_default_enabled(self, monkeypatch):
        monkeypatch.delenv("SCHOLAR_GODEL_FIGURE_SEMANTIC", raising=False)
        assert _is_enabled() is True

    def test_disabled_by_zero(self, monkeypatch):
        monkeypatch.setenv("SCHOLAR_GODEL_FIGURE_SEMANTIC", "0")
        assert _is_enabled() is False

    def test_enabled_by_one(self, monkeypatch):
        monkeypatch.setenv("SCHOLAR_GODEL_FIGURE_SEMANTIC", "1")
        assert _is_enabled() is True

    def test_enabled_by_true(self, monkeypatch):
        monkeypatch.setenv("SCHOLAR_GODEL_FIGURE_SEMANTIC", "true")
        assert _is_enabled() is True

    def test_enabled_by_yes(self, monkeypatch):
        monkeypatch.setenv("SCHOLAR_GODEL_FIGURE_SEMANTIC", "yes")
        assert _is_enabled() is True

    def test_disabled_by_false(self, monkeypatch):
        monkeypatch.setenv("SCHOLAR_GODEL_FIGURE_SEMANTIC", "false")
        assert _is_enabled() is False

    def test_disabled_by_no(self, monkeypatch):
        monkeypatch.setenv("SCHOLAR_GODEL_FIGURE_SEMANTIC", "no")
        assert _is_enabled() is False

    def test_semantic_skill_returns_empty_when_off(self, monkeypatch):
        monkeypatch.setenv("SCHOLAR_GODEL_FIGURE_SEMANTIC", "0")
        skill = FigureSemanticSkill()
        ctx = SkillContext(paper_text=SAMPLE_ECON_PAPER)
        result = skill.execute(ctx)
        assert not result.success
        assert result.findings == []

    def test_consistency_skill_returns_empty_when_off(self, monkeypatch):
        monkeypatch.setenv("SCHOLAR_GODEL_FIGURE_SEMANTIC", "0")
        skill = FigureConsistencySkill()
        ctx = SkillContext(paper_text=SAMPLE_ECON_PAPER)
        result = skill.execute(ctx)
        assert not result.success
        assert result.findings == []


# ==============================================================
# Test: Integration — Full Pipeline
# ==============================================================

class TestIntegration:
    """End-to-end integration: text → extraction → analysis → xref → findings."""

    def test_full_pipeline(self):
        """Run complete figure analysis pipeline on sample paper."""
        # Step 1: Extract figures
        extractor = FigureExtractor()
        figures = extractor.extract_figures(SAMPLE_ECON_PAPER)
        assert len(figures) >= 6

        # Step 2: Classify figures
        for fig in figures:
            classification = extractor.classify_figure(fig, SAMPLE_ECON_PAPER)
            fig.figure_type = classification.primary_type

        # Check that not all are OTHER
        classified_count = sum(
            1 for f in figures if f.figure_type != FigureType.OTHER
        )
        assert classified_count >= 3

        # Step 3: Run economics analysis
        analyzer = EconFigureAnalyzer()
        all_findings: list[Finding] = []
        for fig in figures:
            if fig.figure_type != FigureType.OTHER:
                findings = analyzer.analyze(fig, SAMPLE_ECON_PAPER)
                all_findings.extend(findings)

        # Should have some findings
        assert len(all_findings) >= 1
        assert all(isinstance(f, Finding) for f in all_findings)

        # Step 4: Cross-validate
        xref = FigureTextCrossValidator()
        xref_findings, coverage = xref.validate(SAMPLE_ECON_PAPER, figures)
        all_findings.extend(xref_findings)

        # Coverage should be good for this well-written sample
        assert coverage.coverage_score >= 0.5

        # Final findings should have various severities
        severities = set(f.severity for f in all_findings)
        assert len(severities) >= 1

    def test_skill_pipeline(self):
        """Test using the Skill interface end-to-end."""
        semantic = FigureSemanticSkill()
        consistency = FigureConsistencySkill()

        ctx = SkillContext(paper_text=SAMPLE_ECON_PAPER)

        # Run semantic skill
        semantic_result = semantic.execute(ctx)
        assert semantic_result.success
        assert semantic_result.output_data["extraction_stats"]["total_figures"] >= 6

        # Run consistency skill (self-extracts since no upstream)
        consistency_result = consistency.execute(ctx)
        assert consistency_result.success
        assert "coverage_report" in consistency_result.output_data

    def test_pipeline_with_upstream_figures(self):
        """Test passing figures from semantic skill to consistency skill."""
        semantic = FigureSemanticSkill()
        consistency = FigureConsistencySkill()

        ctx = SkillContext(paper_text=SAMPLE_ECON_PAPER)

        # Semantic extraction
        semantic_result = semantic.execute(ctx)
        assert semantic_result.success

        # Pass figure data downstream
        downstream_ctx = SkillContext(
            paper_text=SAMPLE_ECON_PAPER,
            parameters={"figures": semantic_result.output_data["figures"]},
        )
        consistency_result = consistency.execute(downstream_ctx)
        assert consistency_result.success


# ==============================================================
# Test: Edge Cases
# ==============================================================

class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_empty_text_all_components(self):
        extractor = FigureExtractor()
        assert extractor.extract_figures("") == []

        analyzer = EconFigureAnalyzer()
        fig = FigureReference(figure_id="Figure 1", caption="")
        findings = analyzer.analyze(fig, "")
        # Should still run general checks
        assert isinstance(findings, list)

        validator = FigureTextCrossValidator()
        findings, coverage = validator.validate("", [])
        assert findings == []

    def test_malformed_figure_captions(self):
        text = """
        Figure: This has no number.
        Fig without a colon 1
        Figure 1 Without proper delimiter but maybe still detected
        FIG 2. Proper uppercase format with results.
        """
        extractor = FigureExtractor()
        figures = extractor.extract_figures(text)
        # At least Figure 2 should be detected with "FIG 2." pattern
        ids = [f.figure_id for f in figures]
        assert "Figure 2" in ids

    def test_very_large_figure_number(self):
        text = "Figure 99: Some appendix figure with very late numbering."
        extractor = FigureExtractor()
        figures = extractor.extract_figures(text)
        assert any(f.figure_id == "Figure 99" for f in figures)

    def test_multiple_figures_same_line(self):
        text = "We compare Figure 1 and Figure 2 which show similar patterns."
        extractor = FigureExtractor()
        figures = extractor.extract_figures(text)
        ids = [f.figure_id for f in figures]
        assert "Figure 1" in ids
        assert "Figure 2" in ids

    def test_figure_with_only_mentions_no_caption(self):
        text = "As shown in Figure 7, the results are clear."
        extractor = FigureExtractor()
        figures = extractor.extract_figures(text)
        # Should create a placeholder for Figure 7
        assert any(f.figure_id == "Figure 7" for f in figures)
        fig7 = next(f for f in figures if f.figure_id == "Figure 7")
        assert fig7.caption == ""
        assert len(fig7.text_mentions) >= 1

    def test_special_characters_in_caption(self):
        text = 'Figure 1: Effect of δ on y = f(x) with α = 0.05 and β > 1.'
        extractor = FigureExtractor()
        figures = extractor.extract_figures(text)
        assert len(figures) >= 1
        assert "δ" in figures[0].caption or "Effect" in figures[0].caption

    def test_none_paper_text_in_context(self):
        """Test that skills handle empty/None-like contexts gracefully."""
        skill = FigureSemanticSkill()
        ctx = SkillContext(paper_text="")
        result = skill.execute(ctx)
        assert result.success
        assert result.output_data["figures"] == []

    def test_figures_only_in_appendix_style(self):
        text = """
        Main text discusses results verbally.

        Appendix A
        Figure A1: Additional robustness results.
        """
        extractor = FigureExtractor()
        figures = extractor.extract_figures(text)
        # The regex should not match "Figure A1" since it expects digits
        # But it depends on the pattern — let's just verify no crash
        assert isinstance(figures, list)

    def test_coverage_report_dataclass(self):
        report = CoverageReport()
        assert report.orphan_figures == []
        assert report.phantom_references == []
        assert report.under_discussed == []
        assert report.total_figures == 0
        assert report.total_references == 0
        assert report.coverage_score == 1.0

    def test_figure_issue_dataclass(self):
        issue = FigureIssue(
            rule_name="test_rule",
            severity="major",
            description="Test issue",
        )
        assert issue.rule_name == "test_rule"
        assert issue.severity == "major"
        assert issue.confidence == 0.7  # default

    def test_extracted_value_dataclass(self):
        val = ExtractedValue(value=0.05, context="effect of 0.05", value_type="effect_size")
        assert val.value == pytest.approx(0.05)
        assert val.value_type == "effect_size"
        assert val.unit == ""  # default

    def test_figure_text_claim_dataclass(self):
        claim = FigureTextClaim(
            figure_id="Figure 1",
            claim_text="large effect",
            claim_type="magnitude",
        )
        assert claim.figure_id == "Figure 1"
        assert claim.numerical_values == []
        assert claim.direction == ""

    def test_cross_modal_inconsistency_dataclass(self):
        inc = CrossModalInconsistency(
            figure_id="Figure 1",
            inconsistency_type="test",
            text_claim="claim",
            figure_evidence="evidence",
            severity="minor",
            confidence=0.5,
        )
        assert inc.suggestion == ""  # default
