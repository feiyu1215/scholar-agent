"""
Tests for F.3-P1 features:
  - Rule 10: Cross-table duplication detection (G005-001 scenario)
  - Sequential subscript error detection (G005-003 scenario)
"""

import pytest
from core.skills.multimodal.consistency_engine import (
    ConsistencyValidator,
    RuleID,
    Severity,
)
from core.skills.multimodal.econ_table import (
    DescriptiveColumn,
    EconTable,
    EconTableType,
)
from core.skills.multimodal.table_parser import CellValue, RawTable
from core.skills.economics.math_audit import AppendixMathAuditSkill
from core.skills.base import SkillContext


# ==============================================================
# Rule 10: Cross-Table Duplication
# ==============================================================


class TestRule10CrossTableDuplication:
    """Test Rule 10 — detects identical data across balance/descriptive tables."""

    def setup_method(self):
        self.validator = ConsistencyValidator()

    def test_identical_balance_tables_detected(self):
        """G005(001) scenario: two balance tables with identical means/SDs."""
        table_a = EconTable(
            table_id="Table_A3",
            table_type=EconTableType.BALANCE_TABLE,
            descriptive_columns=[
                DescriptiveColumn(variable_name="Age", mean=35.2, std_dev=8.1, n_observations=500),
                DescriptiveColumn(variable_name="Income", mean=52000.0, std_dev=15000.0, n_observations=500),
                DescriptiveColumn(variable_name="Education", mean=14.3, std_dev=2.5, n_observations=500),
                DescriptiveColumn(variable_name="Female", mean=0.48, std_dev=0.50, n_observations=500),
            ],
        )
        table_b = EconTable(
            table_id="Table_A4",
            table_type=EconTableType.BALANCE_TABLE,
            descriptive_columns=[
                DescriptiveColumn(variable_name="Age", mean=35.2, std_dev=8.1, n_observations=500),
                DescriptiveColumn(variable_name="Income", mean=52000.0, std_dev=15000.0, n_observations=500),
                DescriptiveColumn(variable_name="Education", mean=14.3, std_dev=2.5, n_observations=500),
                DescriptiveColumn(variable_name="Female", mean=0.48, std_dev=0.50, n_observations=500),
            ],
        )

        report = self.validator.validate([table_a, table_b])
        r10 = [v for v in report.violations if v.rule_id == RuleID.CROSS_TABLE_DUPLICATION]

        assert len(r10) >= 1, "Should detect identical balance tables"
        assert r10[0].severity == Severity.ERROR
        assert r10[0].confidence >= 0.9
        assert "Table_A3" in r10[0].table_id
        assert "Table_A4" in r10[0].table_id

    def test_different_balance_tables_no_false_positive(self):
        """Different subsamples should not trigger Rule 10."""
        table_a = EconTable(
            table_id="Table_A3",
            table_type=EconTableType.BALANCE_TABLE,
            descriptive_columns=[
                DescriptiveColumn(variable_name="Age", mean=35.2, std_dev=8.1, n_observations=500),
                DescriptiveColumn(variable_name="Income", mean=52000.0, std_dev=15000.0, n_observations=500),
                DescriptiveColumn(variable_name="Education", mean=14.3, std_dev=2.5, n_observations=500),
            ],
        )
        table_b = EconTable(
            table_id="Table_A4",
            table_type=EconTableType.BALANCE_TABLE,
            descriptive_columns=[
                DescriptiveColumn(variable_name="Age", mean=33.7, std_dev=7.9, n_observations=490),
                DescriptiveColumn(variable_name="Income", mean=48500.0, std_dev=14200.0, n_observations=490),
                DescriptiveColumn(variable_name="Education", mean=13.8, std_dev=2.3, n_observations=490),
            ],
        )

        report = self.validator.validate([table_a, table_b])
        r10 = [v for v in report.violations if v.rule_id == RuleID.CROSS_TABLE_DUPLICATION]

        assert len(r10) == 0, "Different data should not trigger Rule 10"

    def test_raw_matrix_duplication_detected(self):
        """Test detection via raw numeric matrix comparison."""
        # Create raw tables with identical numeric cells
        cells_a = [
            [CellValue("35.2"), CellValue("8.1"), CellValue("500")],
            [CellValue("52000"), CellValue("15000"), CellValue("500")],
            [CellValue("14.3"), CellValue("2.5"), CellValue("500")],
            [CellValue("0.48"), CellValue("0.50"), CellValue("500")],
        ]
        cells_b = [
            [CellValue("35.2"), CellValue("8.1"), CellValue("500")],
            [CellValue("52000"), CellValue("15000"), CellValue("500")],
            [CellValue("14.3"), CellValue("2.5"), CellValue("500")],
            [CellValue("0.48"), CellValue("0.50"), CellValue("500")],
        ]

        raw_a = RawTable(table_id="Table_A3", body=cells_a)
        raw_b = RawTable(table_id="Table_A4", body=cells_b)

        table_a = EconTable(
            table_id="Table_A3",
            table_type=EconTableType.BALANCE_TABLE,
            source_raw=raw_a,
        )
        table_b = EconTable(
            table_id="Table_A4",
            table_type=EconTableType.BALANCE_TABLE,
            source_raw=raw_b,
        )

        report = self.validator.validate([table_a, table_b])
        r10 = [v for v in report.violations if v.rule_id == RuleID.CROSS_TABLE_DUPLICATION]

        assert len(r10) >= 1, "Should detect identical raw matrices"
        assert r10[0].severity == Severity.ERROR

    def test_partial_overlap_warning(self):
        """~85% overlap should trigger WARNING, not ERROR."""
        table_a = EconTable(
            table_id="Table_1",
            table_type=EconTableType.BALANCE_TABLE,
            descriptive_columns=[
                DescriptiveColumn(variable_name="V1", mean=10.0, std_dev=2.0),
                DescriptiveColumn(variable_name="V2", mean=20.0, std_dev=4.0),
                DescriptiveColumn(variable_name="V3", mean=30.0, std_dev=6.0),
                DescriptiveColumn(variable_name="V4", mean=40.0, std_dev=8.0),
                DescriptiveColumn(variable_name="V5", mean=50.0, std_dev=10.0),
            ],
        )
        # 4 out of 5 means identical, but one different
        table_b = EconTable(
            table_id="Table_2",
            table_type=EconTableType.BALANCE_TABLE,
            descriptive_columns=[
                DescriptiveColumn(variable_name="V1", mean=10.0, std_dev=2.0),
                DescriptiveColumn(variable_name="V2", mean=20.0, std_dev=4.0),
                DescriptiveColumn(variable_name="V3", mean=30.0, std_dev=6.0),
                DescriptiveColumn(variable_name="V4", mean=40.0, std_dev=8.0),
                DescriptiveColumn(variable_name="V5", mean=55.0, std_dev=11.0),
            ],
        )

        report = self.validator.validate([table_a, table_b])
        r10 = [v for v in report.violations if v.rule_id == RuleID.CROSS_TABLE_DUPLICATION]

        # 4/5 = 80% overlap in means → should be WARNING level
        assert len(r10) >= 1
        assert r10[0].severity == Severity.WARNING


# ==============================================================
# Sequential Subscript Error Detection
# ==============================================================


class TestSequentialSubscriptErrors:
    """Test the sequential subscript error detection for G005(003) scenario."""

    def setup_method(self):
        self.skill = AppendixMathAuditSkill()

    def test_g005_003_theta_sequence_error(self):
        """
        G005(003): theta_1 in eq43, theta_1 in eq44 (should be theta_2),
        theta_2 in eq45.
        Pattern: [1, 1, 2] → middle should be 2.
        """
        equations = [
            r"\\theta_1 = \\frac{\\sigma - 1}{\\sigma} L_1",    # eq43: sector 1
            r"\\theta_1 = \\frac{\\sigma - 1}{\\sigma} L_2",    # eq44: sector 2 (TYPO!)
            r"f_2 \\theta_2^{\\sigma-1} = f_e",                 # eq45: correct
        ]

        findings = self.skill._check_sequential_subscript_errors(equations)

        assert len(findings) >= 1, "Should detect subscript stagnation"
        # The finding should mention theta and the sequence pattern
        desc = findings[0].description.lower()
        assert "theta" in desc or "subscript" in desc

    def test_reversion_pattern_detected(self):
        """
        Pattern: theta_2 → theta_1 → theta_2 (middle is typo).
        This is the "down then up" reversion pattern.
        """
        equations = [
            r"\\theta_2 \\cdot p = w",
            r"\\theta_1 \\cdot q = w",  # Should be theta_2
            r"\\theta_2 \\cdot r = w",
        ]

        findings = self.skill._check_sequential_subscript_errors(equations)

        assert len(findings) >= 1, "Should detect reversion pattern"
        assert findings[0].confidence >= 0.7
        assert findings[0].severity == "major"

    def test_no_false_positive_on_legitimate_sequence(self):
        """theta_1, theta_2, theta_3 is a legitimate progression."""
        equations = [
            r"\\theta_1 = f(x_1)",
            r"\\theta_2 = f(x_2)",
            r"\\theta_3 = f(x_3)",
        ]

        findings = self.skill._check_sequential_subscript_errors(equations)

        assert len(findings) == 0, "Legitimate progression should not be flagged"

    def test_no_false_positive_on_repeated_same_subscript(self):
        """theta_1 used in all equations is fine (single-sector discussion)."""
        equations = [
            r"\\theta_1 = a + b",
            r"\\theta_1 \\cdot c = d",
            r"p = \\theta_1 \\cdot e",
        ]

        findings = self.skill._check_sequential_subscript_errors(equations)

        assert len(findings) == 0, "Consistent subscript should not be flagged"

    def test_minimum_equations_required(self):
        """With fewer than 3 equations, no detection attempted."""
        equations = [
            r"\\theta_1 = a",
            r"\\theta_2 = b",
        ]

        findings = self.skill._check_sequential_subscript_errors(equations)

        assert len(findings) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
