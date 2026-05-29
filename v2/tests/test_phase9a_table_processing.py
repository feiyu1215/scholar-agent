"""
tests/test_phase9a_table_processing.py

Comprehensive test suite for Phase 9A: Table Processing & Numerical Validation.

Covers:
  - TextTableParser (LaTeX, Markdown, space-aligned extraction)
  - CellValue parsing (numerics, stars, parentheses, brackets)
  - EconTableParser (regression/descriptive classification + parsing)
  - ConsistencyValidator (8 rules)
  - TextTableCrossValidator
  - TableExtractionSkill / TableConsistencySkill (SkillX integration)
  - Kill Switch behavior
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.skills.multimodal.table_parser import CellValue, RawTable, TextTableParser
from core.skills.multimodal.econ_table import (
    CoefficientEntry,
    DescriptiveColumn,
    EconTable,
    EconTableParser,
    EconTableType,
    RegressionColumn,
    SEType,
    StarConvention,
)
from core.skills.multimodal.consistency_engine import (
    ConsistencyValidator,
    ConsistencyViolation,
    RuleID,
    Severity,
    ValidationReport,
)
from core.skills.multimodal.text_table_xref import TextTableCrossValidator
from core.skills.multimodal.skills import (
    TableConsistencySkill,
    TableExtractionSkill,
    _is_enabled,
)
from core.skills.base import SkillContext, SkillLevel


# ==============================================================
# Fixtures
# ==============================================================

SAMPLE_LATEX_TABLE = (
    "\\begin{table}[t]\n"
    "\\caption{Effect of Treatment on Outcomes}\n"
    "\\begin{tabular}{lcccc}\n"
    "\\hline\n"
    " & (1) & (2) & (3) & (4) \\\\\n"
    " & OLS & OLS & IV & IV \\\\\n"
    "\\hline\n"
    "Treatment & 0.053*** & 0.048** & 0.072*** & 0.065** \\\\\n"
    " & (0.012) & (0.015) & (0.018) & (0.022) \\\\\n"
    "Age & & -0.003 & & -0.005 \\\\\n"
    " & & (0.002) & & (0.003) \\\\\n"
    "\\hline\n"
    "Observations & 12,345 & 12,345 & 12,100 & 12,100 \\\\\n"
    "R-squared & 0.15 & 0.18 & 0.12 & 0.14 \\\\\n"
    "Controls & No & Yes & No & Yes \\\\\n"
    "Year FE & Yes & Yes & Yes & Yes \\\\\n"
    "\\hline\n"
    "\\end{tabular}\n"
    "\\begin{tablenotes}\n"
    "Standard errors in parentheses. *** p<0.01, ** p<0.05, * p<0.1.\n"
    "Robust standard errors clustered at the firm level.\n"
    "\\end{tablenotes}\n"
    "\\end{table}\n"
)

SAMPLE_MARKDOWN_TABLE = (
    "| Variable | Mean | SD | Min | Max | N |\n"
    "|----------|------|-----|-----|-----|-------|\n"
    "| Income | 45000 | 12000 | 8000 | 250000 | 5432 |\n"
    "| Age | 35.2 | 8.5 | 18 | 65 | 5432 |\n"
    "| Education | 14.3 | 2.1 | 8 | 22 | 5400 |\n"
    "| Treatment | 0.52 | 0.50 | 0 | 1 | 5432 |\n"
)


# ==============================================================
# Test: CellValue Parsing
# ==============================================================

class TestCellValue:
    """Test CellValue dataclass parsing capabilities."""

    def test_plain_number(self):
        cell = CellValue(raw="0.053")
        assert cell.numeric == pytest.approx(0.053)
        assert cell.has_stars == 0
        assert not cell.is_parenthesized

    def test_number_with_three_stars(self):
        cell = CellValue(raw="0.053***")
        assert cell.numeric == pytest.approx(0.053)
        assert cell.has_stars == 3

    def test_number_with_two_stars(self):
        cell = CellValue(raw="0.048**")
        assert cell.numeric == pytest.approx(0.048)
        assert cell.has_stars == 2

    def test_number_with_one_star(self):
        cell = CellValue(raw="-0.032*")
        assert cell.numeric == pytest.approx(-0.032)
        assert cell.has_stars == 1

    def test_parenthesized_se(self):
        cell = CellValue(raw="(0.012)")
        assert cell.numeric == pytest.approx(0.012)
        assert cell.is_parenthesized

    def test_bracketed_ci(self):
        cell = CellValue(raw="[0.03, 0.09]")
        assert cell.is_bracketed

    def test_integer_with_comma(self):
        cell = CellValue(raw="12,345")
        assert cell.numeric == pytest.approx(12345)

    def test_negative_number(self):
        cell = CellValue(raw="-0.005")
        assert cell.numeric == pytest.approx(-0.005)

    def test_empty_cell(self):
        cell = CellValue(raw="")
        assert cell.is_empty

    def test_text_no_numeric(self):
        cell = CellValue(raw="Yes")
        assert cell.numeric is None
        assert not cell.is_empty


# ==============================================================
# Test: TextTableParser
# ==============================================================

class TestTextTableParser:
    """Test multi-strategy text table extraction."""

    def setup_method(self):
        self.parser = TextTableParser()

    def test_extract_latex_table(self):
        tables = self.parser.extract_all(SAMPLE_LATEX_TABLE)
        assert len(tables) >= 1

    def test_extract_markdown_table(self):
        tables = self.parser.extract_all(SAMPLE_MARKDOWN_TABLE)
        assert len(tables) >= 1
        table = tables[0]
        assert table.n_cols >= 5

    def test_extract_from_mixed_text(self):
        mixed = "Some introductory text.\n\n" + SAMPLE_MARKDOWN_TABLE + "\n\nMore prose."
        tables = self.parser.extract_all(mixed)
        assert len(tables) >= 1

    def test_no_tables_in_plain_text(self):
        plain = "This is just a paragraph with no tables at all."
        tables = self.parser.extract_all(plain)
        assert len(tables) == 0

    def test_raw_table_structure(self):
        tables = self.parser.extract_all(SAMPLE_MARKDOWN_TABLE)
        if tables:
            table = tables[0]
            assert isinstance(table, RawTable)
            assert table.table_id
            assert len(table.body) >= 3
            for row in table.body:
                for cell in row:
                    assert isinstance(cell, CellValue)


# ==============================================================
# Test: EconTableParser
# ==============================================================

class TestEconTableParser:
    """Test economics table semantic parsing."""

    def setup_method(self):
        self.econ_parser = EconTableParser()

    def test_classify_regression_table(self):
        raw = RawTable(
            table_id="test_reg",
            caption="Effect of Treatment on Outcomes",
            headers=[["", "(1)", "(2)", "(3)"]],
            body=[
                [CellValue(raw="Treatment"), CellValue(raw="0.05***"),
                 CellValue(raw="0.04**"), CellValue(raw="0.06***")],
                [CellValue(raw=""), CellValue(raw="(0.01)"),
                 CellValue(raw="(0.015)"), CellValue(raw="(0.02)")],
                [CellValue(raw="Observations"), CellValue(raw="10000"),
                 CellValue(raw="10000"), CellValue(raw="8000")],
                [CellValue(raw="R-squared"), CellValue(raw="0.15"),
                 CellValue(raw="0.18"), CellValue(raw="0.12")],
            ],
            notes="*** p<0.01, ** p<0.05, * p<0.1",
            source_format="latex",
        )
        econ = self.econ_parser.parse(raw)
        assert econ.table_type == EconTableType.REGRESSION

    def test_classify_descriptive_table(self):
        raw = RawTable(
            table_id="test_desc",
            caption="Descriptive Statistics",
            headers=[["Variable", "Mean", "SD", "Min", "Max", "N"]],
            body=[
                [CellValue(raw="Income"), CellValue(raw="45000"),
                 CellValue(raw="12000"), CellValue(raw="8000"),
                 CellValue(raw="250000"), CellValue(raw="5432")],
                [CellValue(raw="Age"), CellValue(raw="35.2"),
                 CellValue(raw="8.5"), CellValue(raw="18"),
                 CellValue(raw="65"), CellValue(raw="5432")],
            ],
            notes="",
            source_format="markdown",
        )
        econ = self.econ_parser.parse(raw)
        assert econ.table_type == EconTableType.DESCRIPTIVE_STATS

    def test_regression_columns_parsed(self):
        raw = RawTable(
            table_id="reg_test",
            caption="Regression Results",
            headers=[["", "(1)", "(2)"]],
            body=[
                [CellValue(raw="X"), CellValue(raw="0.05***"), CellValue(raw="0.04**")],
                [CellValue(raw=""), CellValue(raw="(0.01)"), CellValue(raw="(0.015)")],
                [CellValue(raw="Observations"), CellValue(raw="5000"), CellValue(raw="5000")],
                [CellValue(raw="R-squared"), CellValue(raw="0.25"), CellValue(raw="0.30")],
            ],
            notes="*** p<0.01",
            source_format="test",
        )
        econ = self.econ_parser.parse(raw)
        assert len(econ.regression_columns) == 2
        col1 = econ.regression_columns[0]
        assert col1.n_observations == 5000
        assert col1.r_squared == pytest.approx(0.25)

    def test_star_convention_detection(self):
        raw = RawTable(
            table_id="star_test",
            caption="Test",
            headers=[["", "(1)"]],
            body=[[CellValue(raw="X"), CellValue(raw="0.05***")]],
            notes="*** p<0.01, ** p<0.05, * p<0.1",
            source_format="test",
        )
        econ = self.econ_parser.parse(raw)
        assert econ.star_convention == StarConvention.STANDARD

    def test_se_type_clustered(self):
        raw = RawTable(
            table_id="se_test",
            caption="",
            headers=[["", "(1)"]],
            body=[[CellValue(raw="X"), CellValue(raw="0.05")]],
            notes="Robust standard errors clustered at the firm level",
            source_format="test",
        )
        econ = self.econ_parser.parse(raw)
        assert econ.se_type == SEType.CLUSTERED


# ==============================================================
# Test: ConsistencyValidator
# ==============================================================

class TestConsistencyValidator:
    """Test the 8-rule consistency validation engine."""

    def setup_method(self):
        self.validator = ConsistencyValidator()

    def _make_regression_table(self, coeffs_ses_stars, n_obs=1000, r_sq=0.5):
        col = RegressionColumn(
            column_index=0,
            column_header="(1)",
            n_observations=n_obs,
            r_squared=r_sq,
        )
        for i, (coeff, se, stars) in enumerate(coeffs_ses_stars):
            entry = CoefficientEntry(
                variable_name=f"X{i+1}",
                coefficient=coeff,
                standard_error=se,
                stars=stars,
            )
            col.coefficients.append(entry)
        return EconTable(
            table_id="test_table",
            table_type=EconTableType.REGRESSION,
            regression_columns=[col],
        )

    # Rule 1: Coefficient-SE Consistency

    def test_rule1_valid_stars(self):
        table = self._make_regression_table([(0.05, 0.01, 3)])
        report = self.validator.validate([table])
        r1 = [v for v in report.violations if v.rule_id == RuleID.COEFF_SE_CONSISTENCY]
        assert len(r1) == 0

    def test_rule1_over_starred(self):
        # t = 0.05/0.04 = 1.25, should be 0 stars but has 3
        table = self._make_regression_table([(0.05, 0.04, 3)])
        report = self.validator.validate([table])
        r1 = [v for v in report.violations if v.rule_id == RuleID.COEFF_SE_CONSISTENCY]
        assert len(r1) >= 1
        assert r1[0].severity == Severity.WARNING

    # Rule 2: R-squared Bounds

    def test_rule2_valid(self):
        table = self._make_regression_table([], r_sq=0.85)
        report = self.validator.validate([table])
        r2 = [v for v in report.violations if v.rule_id == RuleID.R_SQUARED_BOUNDS]
        assert len(r2) == 0

    def test_rule2_exceeds_1(self):
        table = self._make_regression_table([], r_sq=1.05)
        report = self.validator.validate([table])
        r2 = [v for v in report.violations if v.rule_id == RuleID.R_SQUARED_BOUNDS]
        assert len(r2) >= 1
        assert r2[0].severity == Severity.ERROR

    def test_rule2_adj_exceeds_r2(self):
        col = RegressionColumn(column_index=0, r_squared=0.5, adjusted_r_squared=0.6)
        table = EconTable(
            table_id="test",
            table_type=EconTableType.REGRESSION,
            regression_columns=[col],
        )
        report = self.validator.validate([table])
        r2 = [v for v in report.violations if v.rule_id == RuleID.R_SQUARED_BOUNDS]
        assert len(r2) >= 1

    # Rule 3: Sample Size Monotonicity

    def test_rule3_valid(self):
        col1 = RegressionColumn(column_index=0, n_observations=10000, fixed_effects=[])
        col2 = RegressionColumn(column_index=1, n_observations=9500, fixed_effects=["Year FE"])
        table = EconTable(
            table_id="test",
            table_type=EconTableType.REGRESSION,
            regression_columns=[col1, col2],
        )
        report = self.validator.validate([table])
        r3 = [v for v in report.violations if v.rule_id == RuleID.SAMPLE_SIZE_MONOTONICITY]
        assert len(r3) == 0

    def test_rule3_n_increases_with_fe(self):
        col1 = RegressionColumn(column_index=0, n_observations=5000, fixed_effects=[])
        col2 = RegressionColumn(column_index=1, n_observations=8000, fixed_effects=["Year FE"])
        table = EconTable(
            table_id="test",
            table_type=EconTableType.REGRESSION,
            regression_columns=[col1, col2],
        )
        report = self.validator.validate([table])
        r3 = [v for v in report.violations if v.rule_id == RuleID.SAMPLE_SIZE_MONOTONICITY]
        assert len(r3) >= 1

    # Rule 5: SE Positivity

    def test_rule5_negative_se(self):
        table = self._make_regression_table([(0.05, -0.01, 1)])
        report = self.validator.validate([table])
        r5 = [v for v in report.violations if v.rule_id == RuleID.SE_POSITIVITY]
        assert len(r5) >= 1
        assert r5[0].severity == Severity.ERROR

    def test_rule5_zero_se(self):
        table = self._make_regression_table([(0.05, 0.0, 1)])
        report = self.validator.validate([table])
        r5 = [v for v in report.violations if v.rule_id == RuleID.SE_POSITIVITY]
        assert len(r5) >= 1

    # Rule 7: Descriptive Stats

    def test_rule7_mean_below_min(self):
        desc = DescriptiveColumn(variable_name="X", mean=5000, minimum=10000, maximum=100000)
        table = EconTable(
            table_id="desc",
            table_type=EconTableType.DESCRIPTIVE_STATS,
            descriptive_columns=[desc],
        )
        report = self.validator.validate([table])
        r7 = [v for v in report.violations if v.rule_id == RuleID.DESCRIPTIVE_INTERNAL]
        assert len(r7) >= 1
        assert r7[0].severity == Severity.ERROR

    def test_rule7_negative_sd(self):
        desc = DescriptiveColumn(variable_name="X", std_dev=-5.0)
        table = EconTable(
            table_id="desc",
            table_type=EconTableType.DESCRIPTIVE_STATS,
            descriptive_columns=[desc],
        )
        report = self.validator.validate([table])
        r7 = [v for v in report.violations if v.rule_id == RuleID.DESCRIPTIVE_INTERNAL]
        assert len(r7) >= 1

    # ValidationReport

    def test_report_summary(self):
        report = ValidationReport(tables_checked=3, rules_applied=5)
        report.violations.append(ConsistencyViolation(
            rule_id=RuleID.R_SQUARED_BOUNDS,
            severity=Severity.ERROR,
            table_id="t1",
            description="Test violation",
        ))
        summary = report.summary()
        assert "Errors: 1" in summary
        assert "3 tables checked" in summary


# ==============================================================
# Test: TextTableCrossValidator
# ==============================================================

class TestTextTableCrossValidator:
    """Test text-table cross-reference validation."""

    def setup_method(self):
        self.xref = TextTableCrossValidator()

    def test_direction_mismatch(self):
        col = RegressionColumn(column_index=0)
        col.coefficients.append(CoefficientEntry(
            variable_name="Treatment",
            coefficient=-0.05,
            stars=2,
        ))
        table = EconTable(
            table_id="table_1",
            table_type=EconTableType.REGRESSION,
            caption="Table 1",
            regression_columns=[col],
        )
        text = 'The "Treatment" has a positive effect on outcomes.'
        violations = self.xref.cross_validate(text, [table])
        direction_v = [
            v for v in violations
            if "positive" in v.description.lower() or "negative" in v.description.lower()
        ]
        assert len(direction_v) >= 1


# ==============================================================
# Test: SkillX Integration
# ==============================================================

class TestTableExtractionSkill:
    """Test TableExtractionSkill SkillX integration."""

    def setup_method(self):
        self.skill = TableExtractionSkill()

    def test_descriptor(self):
        desc = self.skill.descriptor
        assert desc.name == "table_extraction"
        assert desc.level == SkillLevel.FUNCTIONAL
        assert "table" in desc.tags

    def test_can_apply_with_latex(self):
        ctx = SkillContext(paper_text=SAMPLE_LATEX_TABLE)
        score = self.skill.can_apply(ctx)
        assert score >= 0.5

    def test_can_apply_with_plain_text(self):
        ctx = SkillContext(paper_text="No tables here, just prose.")
        score = self.skill.can_apply(ctx)
        assert score < 0.3

    def test_execute_extracts_tables(self):
        ctx = SkillContext(paper_text=SAMPLE_LATEX_TABLE)
        result = self.skill.execute(ctx)
        assert result.success
        assert "raw_tables" in result.output_data
        assert "econ_tables" in result.output_data
        assert result.output_data["extraction_stats"]["total_tables"] >= 1

    def test_kill_switch_disables(self, monkeypatch):
        monkeypatch.setenv("SCHOLAR_GODEL_TABLE_PROCESSING", "0")
        ctx = SkillContext(paper_text=SAMPLE_LATEX_TABLE)
        assert self.skill.can_apply(ctx) == 0.0
        result = self.skill.execute(ctx)
        assert not result.success
        assert "Kill Switch" in result.error_message


class TestTableConsistencySkill:
    """Test TableConsistencySkill SkillX integration."""

    def setup_method(self):
        self.skill = TableConsistencySkill()

    def test_descriptor(self):
        desc = self.skill.descriptor
        assert desc.name == "table_consistency"
        assert desc.level == SkillLevel.FUNCTIONAL
        assert "table_extraction" in desc.prerequisites

    def test_can_apply_high_with_tables(self):
        ctx = SkillContext(
            paper_text="Table 1 shows... Table 2 presents... Table 3 reports... coefficient",
            current_phase="deep_review",
        )
        score = self.skill.can_apply(ctx)
        assert score >= 0.4

    def test_execute_produces_report(self):
        ctx = SkillContext(paper_text=SAMPLE_LATEX_TABLE)
        result = self.skill.execute(ctx)
        assert result.success
        assert "validation_report" in result.output_data

    def test_kill_switch_disables(self, monkeypatch):
        monkeypatch.setenv("SCHOLAR_GODEL_TABLE_PROCESSING", "0")
        ctx = SkillContext(paper_text=SAMPLE_LATEX_TABLE)
        assert self.skill.can_apply(ctx) == 0.0


# ==============================================================
# Test: Kill Switch
# ==============================================================

class TestKillSwitch:
    """Test Kill Switch behavior."""

    def test_default_enabled(self, monkeypatch):
        monkeypatch.delenv("SCHOLAR_GODEL_TABLE_PROCESSING", raising=False)
        assert _is_enabled() is True

    def test_disabled_by_env(self, monkeypatch):
        monkeypatch.setenv("SCHOLAR_GODEL_TABLE_PROCESSING", "0")
        assert _is_enabled() is False

    def test_enabled_by_env(self, monkeypatch):
        monkeypatch.setenv("SCHOLAR_GODEL_TABLE_PROCESSING", "1")
        assert _is_enabled() is True

    def test_enabled_by_true(self, monkeypatch):
        monkeypatch.setenv("SCHOLAR_GODEL_TABLE_PROCESSING", "true")
        assert _is_enabled() is True

    def test_enabled_by_yes(self, monkeypatch):
        monkeypatch.setenv("SCHOLAR_GODEL_TABLE_PROCESSING", "yes")
        assert _is_enabled() is True

    def test_disabled_by_false(self, monkeypatch):
        monkeypatch.setenv("SCHOLAR_GODEL_TABLE_PROCESSING", "false")
        assert _is_enabled() is False


# ==============================================================
# Test: Full Pipeline Integration
# ==============================================================

class TestFullPipeline:
    """End-to-end integration: text -> extraction -> validation."""

    def test_extraction_to_consistency(self):
        extraction = TableExtractionSkill()
        consistency = TableConsistencySkill()

        ctx = SkillContext(paper_text=SAMPLE_LATEX_TABLE)

        # Extraction
        extract_result = extraction.execute(ctx)
        assert extract_result.success
        assert extract_result.output_data["extraction_stats"]["total_tables"] >= 1

        # Consistency
        validate_result = consistency.execute(ctx)
        assert validate_result.success
        assert "validation_report" in validate_result.output_data

    def test_multiple_tables(self):
        multi = SAMPLE_LATEX_TABLE + "\n\n" + SAMPLE_MARKDOWN_TABLE
        extraction = TableExtractionSkill()
        ctx = SkillContext(paper_text=multi)
        result = extraction.execute(ctx)
        assert result.success
        assert result.output_data["extraction_stats"]["total_tables"] >= 2
