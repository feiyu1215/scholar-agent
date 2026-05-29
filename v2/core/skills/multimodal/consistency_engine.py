"""
Table Numerical Consistency Validation Engine.

Implements 9 validation rules for detecting inconsistencies in
economics tables and between table content and paper text:

Rule 1: Coefficient-SE Consistency
  - |t| = |coeff/SE| should ≈ match implied significance stars
  - E.g., if coeff=0.05, SE=0.03, then t≈1.67 → should be * at most

Rule 2: R² Bounds
  - R² must be in [0, 1] (or slightly negative for some within-R²)
  - Adjusted R² ≤ R² for same specification

Rule 3: Sample Size Monotonicity
  - Adding restrictions/controls should not increase N
  - Subsample N ≤ Full sample N

Rule 4: Significance-Star Consistency
  - Stars must match claimed significance level in notes
  - E.g., *** with "p<0.01" note means |t| > 2.576 (or ~2.33 for one-sided)

Rule 5: Standard Error Positivity
  - SE must always be positive (> 0)
  - Confidence interval width must be positive

Rule 6: Column Progression Logic
  - Baseline → Extended specifications should show logical pattern
  - Coefficient signs should generally be stable across specifications

Rule 7: Descriptive Stats Internal Consistency
  - Mean must be between Min and Max
  - SD > 0 for non-constant variables
  - N should be consistent across variables in same sample

Rule 8: Text-Table Cross Reference
  - Numbers mentioned in text should match table values
  - "significant at 1%" in text → table shows *** for that coefficient

Rule 9: Cross-Table Comparison
  - Detect suspicious data duplication between different tables
  - E.g., Table A.3 and Table A.4 sharing identical coefficient vectors
  - Flag same-sample robustness tables with implausibly identical SEs
  - Detect appendix tables that are exact copies of main tables
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .econ_table import (
    CoefficientEntry,
    EconTable,
    EconTableType,
    RegressionColumn,
    SEType,
    StarConvention,
)

logger = logging.getLogger(__name__)


class Severity(Enum):
    """Severity level of a consistency violation."""
    ERROR = "error"          # Clear mathematical impossibility
    WARNING = "warning"      # Likely error but could have explanation
    INFO = "info"            # Unusual pattern worth noting


class RuleID(Enum):
    """Identifier for each validation rule."""
    COEFF_SE_CONSISTENCY = "R1_coeff_se"
    R_SQUARED_BOUNDS = "R2_bounds"
    SAMPLE_SIZE_MONOTONICITY = "R3_sample_size"
    STAR_SIGNIFICANCE_MATCH = "R4_star_significance"
    SE_POSITIVITY = "R5_se_positivity"
    COLUMN_PROGRESSION = "R6_column_progression"
    DESCRIPTIVE_INTERNAL = "R7_descriptive_internal"
    TEXT_TABLE_CROSS_REF = "R8_text_table_xref"
    CROSS_TABLE_COMPARISON = "R9_cross_table"
    CROSS_TABLE_DUPLICATION = "R10_cross_table_duplication"


@dataclass
class ConsistencyViolation:
    """A detected inconsistency in a table or between text and table."""
    rule_id: RuleID
    severity: Severity
    table_id: str
    description: str
    evidence: str = ""
    location: str = ""          # e.g., "Column 3, Row 'Treatment'"
    expected: str = ""          # What should have been
    actual: str = ""            # What was found
    confidence: float = 0.8    # How confident we are this is a real issue
    suggestion: str = ""       # Recommendation for the author


@dataclass
class ValidationReport:
    """Complete validation report for a set of tables."""
    violations: list[ConsistencyViolation] = field(default_factory=list)
    tables_checked: int = 0
    rules_applied: int = 0

    @property
    def error_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == Severity.ERROR)

    @property
    def warning_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == Severity.WARNING)

    @property
    def info_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == Severity.INFO)

    @property
    def has_critical_issues(self) -> bool:
        return self.error_count > 0

    def summary(self) -> str:
        """Human-readable summary of the validation report."""
        lines = [
            f"Validation Report: {self.tables_checked} tables checked, "
            f"{self.rules_applied} rules applied.",
            f"  Errors: {self.error_count}",
            f"  Warnings: {self.warning_count}",
            f"  Info: {self.info_count}",
        ]
        if self.violations:
            lines.append("\nTop issues:")
            # Show errors first, then warnings
            sorted_violations = sorted(
                self.violations,
                key=lambda v: (
                    0 if v.severity == Severity.ERROR else
                    1 if v.severity == Severity.WARNING else 2
                ),
            )
            for v in sorted_violations[:10]:
                lines.append(
                    f"  [{v.severity.value.upper()}] {v.rule_id.value}: "
                    f"{v.description}"
                )
                if v.location:
                    lines.append(f"    Location: {v.location}")
        return "\n".join(lines)


# ==============================================================
# Critical values for significance testing
# ==============================================================

# Two-tailed critical values for standard significance levels
CRITICAL_VALUES = {
    0.10: 1.645,   # * significance
    0.05: 1.960,   # ** significance
    0.01: 2.576,   # *** significance
}

# Tolerance for t-stat checks (some rounding in reported values)
T_STAT_TOLERANCE = 0.15


class ConsistencyValidator:
    """
    Validates numerical consistency in economics tables.

    Applies all 9 rules to detect potential errors, inconsistencies,
    and suspicious patterns.
    """

    def __init__(
        self,
        *,
        strict_mode: bool = False,
        t_stat_tolerance: float = T_STAT_TOLERANCE,
    ):
        """
        Args:
            strict_mode: If True, downgrades fewer issues to INFO.
            t_stat_tolerance: Tolerance for t-stat matching (accounts
                for rounding in reported coefficients/SEs).
        """
        self.strict_mode = strict_mode
        self.t_stat_tolerance = t_stat_tolerance

    def validate(
        self,
        econ_tables: list[EconTable],
        paper_text: str = "",
    ) -> ValidationReport:
        """
        Run all validation rules on a set of parsed economics tables.

        Args:
            econ_tables: Parsed economics tables to validate.
            paper_text: Full paper text for cross-reference checking.

        Returns:
            ValidationReport with all detected violations.
        """
        report = ValidationReport(tables_checked=len(econ_tables))

        rules_applied = set()

        for table in econ_tables:
            if table.table_type in (
                EconTableType.REGRESSION,
                EconTableType.ROBUSTNESS,
                EconTableType.FIRST_STAGE,
                EconTableType.HETEROGENEITY,
                EconTableType.UNKNOWN,
            ):
                # Rule 1: Coefficient-SE Consistency
                violations = self._rule_coeff_se_consistency(table)
                report.violations.extend(violations)
                rules_applied.add(RuleID.COEFF_SE_CONSISTENCY)

                # Rule 2: R² Bounds
                violations = self._rule_r_squared_bounds(table)
                report.violations.extend(violations)
                rules_applied.add(RuleID.R_SQUARED_BOUNDS)

                # Rule 3: Sample Size Monotonicity
                violations = self._rule_sample_size_monotonicity(table)
                report.violations.extend(violations)
                rules_applied.add(RuleID.SAMPLE_SIZE_MONOTONICITY)

                # Rule 4: Star-Significance Match
                violations = self._rule_star_significance_match(table)
                report.violations.extend(violations)
                rules_applied.add(RuleID.STAR_SIGNIFICANCE_MATCH)

                # Rule 5: SE Positivity
                violations = self._rule_se_positivity(table)
                report.violations.extend(violations)
                rules_applied.add(RuleID.SE_POSITIVITY)

                # Rule 6: Column Progression
                violations = self._rule_column_progression(table)
                report.violations.extend(violations)
                rules_applied.add(RuleID.COLUMN_PROGRESSION)

            if table.table_type == EconTableType.DESCRIPTIVE_STATS:
                # Rule 7: Descriptive Stats Internal Consistency
                violations = self._rule_descriptive_internal(table)
                report.violations.extend(violations)
                rules_applied.add(RuleID.DESCRIPTIVE_INTERNAL)

        # Rule 8: Text-Table Cross Reference (global)
        if paper_text and econ_tables:
            violations = self._rule_text_table_cross_ref(econ_tables, paper_text)
            report.violations.extend(violations)
            rules_applied.add(RuleID.TEXT_TABLE_CROSS_REF)

        # Rule 9: Cross-Table Comparison (global, requires ≥ 2 tables)
        if len(econ_tables) >= 2:
            violations = self._rule_cross_table_comparison(econ_tables)
            report.violations.extend(violations)
            rules_applied.add(RuleID.CROSS_TABLE_COMPARISON)

        # Rule 10: Cross-Table Duplication — cell-level full-matrix comparison
        # Covers balance/descriptive tables that Rule 9 misses
        if len(econ_tables) >= 2:
            violations = self._rule_cross_table_duplication(econ_tables)
            report.violations.extend(violations)
            rules_applied.add(RuleID.CROSS_TABLE_DUPLICATION)

        report.rules_applied = len(rules_applied)
        return report

    # ==================================================================
    # Rule 1: Coefficient-SE Consistency
    # ==================================================================

    def _rule_coeff_se_consistency(
        self, table: EconTable
    ) -> list[ConsistencyViolation]:
        """
        Check that reported significance stars match implied t-statistics.

        If coeff and SE are both reported, compute t = |coeff/SE|.
        The stars should be consistent with this t-value.
        """
        violations: list[ConsistencyViolation] = []

        for col in table.regression_columns:
            for entry in col.coefficients:
                if (
                    entry.coefficient is not None
                    and entry.standard_error is not None
                    and entry.standard_error > 0
                ):
                    t_stat = abs(entry.coefficient / entry.standard_error)

                    # Determine expected star level from t-stat
                    expected_stars = 0
                    if t_stat >= CRITICAL_VALUES[0.01] - self.t_stat_tolerance:
                        expected_stars = 3
                    elif t_stat >= CRITICAL_VALUES[0.05] - self.t_stat_tolerance:
                        expected_stars = 2
                    elif t_stat >= CRITICAL_VALUES[0.10] - self.t_stat_tolerance:
                        expected_stars = 1

                    # Check for over-starring (more stars than t-stat justifies)
                    if entry.stars > 0 and entry.stars > expected_stars + 1:
                        violations.append(ConsistencyViolation(
                            rule_id=RuleID.COEFF_SE_CONSISTENCY,
                            severity=Severity.WARNING,
                            table_id=table.table_id,
                            description=(
                                f"Reported significance ({entry.stars} stars) "
                                f"exceeds what implied t-stat ({t_stat:.2f}) "
                                f"would justify for variable '{entry.variable_name}'"
                            ),
                            evidence=(
                                f"coeff={entry.coefficient}, "
                                f"SE={entry.standard_error}, "
                                f"|t|={t_stat:.3f}"
                            ),
                            location=(
                                f"Column {col.column_index + 1} "
                                f"({col.column_header}), "
                                f"Variable '{entry.variable_name}'"
                            ),
                            expected=f"{expected_stars} stars (|t|={t_stat:.2f})",
                            actual=f"{entry.stars} stars",
                            confidence=0.75,
                            suggestion=(
                                "Verify the coefficient and standard error values. "
                                "The reported significance level appears inconsistent "
                                "with the implied t-statistic."
                            ),
                        ))

                    # Check for severe under-starring (t-stat clearly significant
                    # but no stars reported)
                    elif (
                        entry.stars == 0
                        and expected_stars >= 2
                        and t_stat > CRITICAL_VALUES[0.05] + 0.5
                    ):
                        violations.append(ConsistencyViolation(
                            rule_id=RuleID.COEFF_SE_CONSISTENCY,
                            severity=Severity.INFO,
                            table_id=table.table_id,
                            description=(
                                f"Variable '{entry.variable_name}' has implied "
                                f"t-stat of {t_stat:.2f} but no significance stars"
                            ),
                            evidence=(
                                f"coeff={entry.coefficient}, "
                                f"SE={entry.standard_error}"
                            ),
                            location=(
                                f"Column {col.column_index + 1}, "
                                f"Variable '{entry.variable_name}'"
                            ),
                            confidence=0.5,
                        ))

        return violations

    # ==================================================================
    # Rule 2: R² Bounds
    # ==================================================================

    def _rule_r_squared_bounds(
        self, table: EconTable
    ) -> list[ConsistencyViolation]:
        """
        Validate R² is within acceptable bounds.

        - Standard R² should be in [0, 1]
        - Adjusted R² should be ≤ R² for same column
        - Within R² can be slightly negative but > -0.1 is suspicious
        """
        violations: list[ConsistencyViolation] = []

        for col in table.regression_columns:
            # Check R² bounds
            if col.r_squared is not None:
                if col.r_squared > 1.0:
                    violations.append(ConsistencyViolation(
                        rule_id=RuleID.R_SQUARED_BOUNDS,
                        severity=Severity.ERROR,
                        table_id=table.table_id,
                        description=(
                            f"R-squared exceeds 1.0 "
                            f"(R²={col.r_squared:.4f})"
                        ),
                        location=f"Column {col.column_index + 1} ({col.column_header})",
                        expected="R² ∈ [0, 1]",
                        actual=f"R² = {col.r_squared:.4f}",
                        confidence=0.95,
                        suggestion=(
                            "R-squared cannot exceed 1.0 in a standard regression. "
                            "Check for transcription errors or formula issues."
                        ),
                    ))
                elif col.r_squared < -0.1:
                    violations.append(ConsistencyViolation(
                        rule_id=RuleID.R_SQUARED_BOUNDS,
                        severity=Severity.WARNING,
                        table_id=table.table_id,
                        description=(
                            f"R-squared is substantially negative "
                            f"(R²={col.r_squared:.4f})"
                        ),
                        location=f"Column {col.column_index + 1} ({col.column_header})",
                        expected="R² ≥ 0 (or slightly negative for within-R²)",
                        actual=f"R² = {col.r_squared:.4f}",
                        confidence=0.7,
                    ))

            # Check Adj R² ≤ R²
            if (
                col.r_squared is not None
                and col.adjusted_r_squared is not None
            ):
                if col.adjusted_r_squared > col.r_squared + 0.001:
                    violations.append(ConsistencyViolation(
                        rule_id=RuleID.R_SQUARED_BOUNDS,
                        severity=Severity.ERROR,
                        table_id=table.table_id,
                        description=(
                            f"Adjusted R² ({col.adjusted_r_squared:.4f}) "
                            f"exceeds R² ({col.r_squared:.4f})"
                        ),
                        location=f"Column {col.column_index + 1} ({col.column_header})",
                        expected="Adjusted R² ≤ R²",
                        actual=(
                            f"Adj R²={col.adjusted_r_squared:.4f}, "
                            f"R²={col.r_squared:.4f}"
                        ),
                        confidence=0.95,
                        suggestion=(
                            "Adjusted R-squared must be ≤ standard R-squared. "
                            "This is a mathematical impossibility—check for "
                            "swapped values or transcription error."
                        ),
                    ))

        return violations

    # ==================================================================
    # Rule 3: Sample Size Monotonicity
    # ==================================================================

    def _rule_sample_size_monotonicity(
        self, table: EconTable
    ) -> list[ConsistencyViolation]:
        """
        Check that sample sizes follow logical patterns:
        - Adding restrictions (controls, FE) shouldn't increase N
        - Later columns with more restrictions should have N ≤ earlier columns
          (unless explicitly described as different samples)
        """
        violations: list[ConsistencyViolation] = []

        cols_with_n = [
            (col.column_index, col.n_observations, col)
            for col in table.regression_columns
            if col.n_observations is not None
        ]

        if len(cols_with_n) < 2:
            return violations

        # Check: if columns progressively add controls/FE,
        # N should be non-increasing
        for i in range(len(cols_with_n) - 1):
            idx_i, n_i, col_i = cols_with_n[i]
            idx_j, n_j, col_j = cols_with_n[i + 1]

            # Only flag if later column has MORE controls/FE but LARGER N
            more_controls = (
                len(col_j.fixed_effects) > len(col_i.fixed_effects)
                or len(col_j.controls) > len(col_i.controls)
            )

            if more_controls and n_j > n_i:
                violations.append(ConsistencyViolation(
                    rule_id=RuleID.SAMPLE_SIZE_MONOTONICITY,
                    severity=Severity.WARNING,
                    table_id=table.table_id,
                    description=(
                        f"Column {idx_j + 1} adds restrictions but has "
                        f"larger N ({n_j:,}) than Column {idx_i + 1} "
                        f"(N={n_i:,})"
                    ),
                    location=(
                        f"Columns {idx_i + 1} vs {idx_j + 1}"
                    ),
                    expected=f"N ≤ {n_i:,} (restrictions should not increase sample)",
                    actual=f"N = {n_j:,}",
                    confidence=0.6,
                    suggestion=(
                        "Adding fixed effects or controls should not increase "
                        "the sample size. Verify that column ordering is correct "
                        "or that different samples are properly explained."
                    ),
                ))

        # Also check for implausibly large N jumps between adjacent columns
        # (suggests potential typo)
        for i in range(len(cols_with_n) - 1):
            _, n_i, _ = cols_with_n[i]
            idx_j, n_j, _ = cols_with_n[i + 1]
            if n_i > 0 and n_j > 0:
                ratio = max(n_i, n_j) / min(n_i, n_j)
                if ratio > 100:
                    violations.append(ConsistencyViolation(
                        rule_id=RuleID.SAMPLE_SIZE_MONOTONICITY,
                        severity=Severity.WARNING,
                        table_id=table.table_id,
                        description=(
                            f"Extreme N ratio ({ratio:.0f}x) between adjacent "
                            f"columns suggests possible transcription error"
                        ),
                        evidence=f"N values: {n_i:,} vs {n_j:,}",
                        location=f"Column {idx_j + 1}",
                        confidence=0.5,
                    ))

        return violations

    # ==================================================================
    # Rule 4: Star-Significance Match
    # ==================================================================

    def _rule_star_significance_match(
        self, table: EconTable
    ) -> list[ConsistencyViolation]:
        """
        Verify that star annotations are internally consistent with
        the declared significance convention in table notes.

        This rule complements Rule 1 by checking stars even when SE
        is not reported (e.g., when only p-values are given).
        """
        violations: list[ConsistencyViolation] = []

        # If convention is unknown, we can't validate much
        if table.star_convention == StarConvention.UNKNOWN:
            return violations

        for col in table.regression_columns:
            for entry in col.coefficients:
                # If we have both t-stat and stars, verify
                if entry.t_stat is not None and entry.stars > 0:
                    expected_stars = 0
                    abs_t = abs(entry.t_stat)
                    if abs_t >= CRITICAL_VALUES[0.01]:
                        expected_stars = 3
                    elif abs_t >= CRITICAL_VALUES[0.05]:
                        expected_stars = 2
                    elif abs_t >= CRITICAL_VALUES[0.10]:
                        expected_stars = 1

                    if abs(entry.stars - expected_stars) >= 2:
                        violations.append(ConsistencyViolation(
                            rule_id=RuleID.STAR_SIGNIFICANCE_MATCH,
                            severity=Severity.WARNING,
                            table_id=table.table_id,
                            description=(
                                f"Star level ({entry.stars}) inconsistent with "
                                f"t-statistic ({entry.t_stat:.2f}) for "
                                f"'{entry.variable_name}'"
                            ),
                            location=(
                                f"Column {col.column_index + 1}, "
                                f"Variable '{entry.variable_name}'"
                            ),
                            confidence=0.7,
                        ))

                # If we have p-value and stars, verify
                if entry.p_value is not None and entry.stars > 0:
                    expected_stars = 0
                    if entry.p_value < 0.01:
                        expected_stars = 3
                    elif entry.p_value < 0.05:
                        expected_stars = 2
                    elif entry.p_value < 0.10:
                        expected_stars = 1

                    if entry.stars != expected_stars:
                        violations.append(ConsistencyViolation(
                            rule_id=RuleID.STAR_SIGNIFICANCE_MATCH,
                            severity=Severity.WARNING,
                            table_id=table.table_id,
                            description=(
                                f"Stars ({entry.stars}) don't match p-value "
                                f"({entry.p_value:.4f}) for "
                                f"'{entry.variable_name}'"
                            ),
                            location=(
                                f"Column {col.column_index + 1}, "
                                f"Variable '{entry.variable_name}'"
                            ),
                            expected=f"{expected_stars} stars (p={entry.p_value})",
                            actual=f"{entry.stars} stars",
                            confidence=0.85,
                        ))

        return violations

    # ==================================================================
    # Rule 5: SE Positivity
    # ==================================================================

    def _rule_se_positivity(
        self, table: EconTable
    ) -> list[ConsistencyViolation]:
        """
        Standard errors must always be positive.
        Confidence intervals must have positive width.
        """
        violations: list[ConsistencyViolation] = []

        for col in table.regression_columns:
            for entry in col.coefficients:
                if entry.standard_error is not None and entry.standard_error <= 0:
                    violations.append(ConsistencyViolation(
                        rule_id=RuleID.SE_POSITIVITY,
                        severity=Severity.ERROR,
                        table_id=table.table_id,
                        description=(
                            f"Non-positive standard error "
                            f"(SE={entry.standard_error}) for "
                            f"'{entry.variable_name}'"
                        ),
                        location=(
                            f"Column {col.column_index + 1}, "
                            f"Variable '{entry.variable_name}'"
                        ),
                        expected="SE > 0",
                        actual=f"SE = {entry.standard_error}",
                        confidence=0.99,
                        suggestion=(
                            "Standard errors must always be positive. "
                            "This is likely a transcription error or "
                            "the parenthesized value is not an SE."
                        ),
                    ))

                if entry.confidence_interval is not None:
                    lo, hi = entry.confidence_interval
                    if hi <= lo:
                        violations.append(ConsistencyViolation(
                            rule_id=RuleID.SE_POSITIVITY,
                            severity=Severity.ERROR,
                            table_id=table.table_id,
                            description=(
                                f"Invalid confidence interval "
                                f"[{lo}, {hi}] for '{entry.variable_name}' "
                                f"(upper bound ≤ lower bound)"
                            ),
                            location=(
                                f"Column {col.column_index + 1}, "
                                f"Variable '{entry.variable_name}'"
                            ),
                            confidence=0.95,
                        ))

        return violations

    # ==================================================================
    # Rule 6: Column Progression Logic
    # ==================================================================

    def _rule_column_progression(
        self, table: EconTable
    ) -> list[ConsistencyViolation]:
        """
        Check that coefficient signs are reasonably stable across
        specifications (sign flips on key variables are suspicious).

        Also checks for implausible magnitude changes.
        """
        violations: list[ConsistencyViolation] = []

        if len(table.regression_columns) < 2:
            return violations

        # Track each variable across columns
        var_signs: dict[str, list[tuple[int, float]]] = {}  # var -> [(col_idx, coeff)]

        for col in table.regression_columns:
            for entry in col.coefficients:
                if entry.coefficient is not None and entry.coefficient != 0:
                    key = entry.variable_name.strip().lower()
                    if key not in var_signs:
                        var_signs[key] = []
                    var_signs[key].append((col.column_index, entry.coefficient))

        for var_name, values in var_signs.items():
            if len(values) < 2:
                continue

            # Check for sign flips
            signs = [(idx, 1 if v > 0 else -1) for idx, v in values]
            positive_cols = [idx for idx, s in signs if s > 0]
            negative_cols = [idx for idx, s in signs if s < 0]

            if positive_cols and negative_cols:
                # Sign flip detected - could be normal in heterogeneity
                # tables but suspicious in main regression
                if table.table_type != EconTableType.HETEROGENEITY:
                    violations.append(ConsistencyViolation(
                        rule_id=RuleID.COLUMN_PROGRESSION,
                        severity=Severity.INFO,
                        table_id=table.table_id,
                        description=(
                            f"Sign flip for '{var_name}' across "
                            f"specifications (positive in columns "
                            f"{[c+1 for c in positive_cols]}, "
                            f"negative in columns "
                            f"{[c+1 for c in negative_cols]})"
                        ),
                        location=f"Variable '{var_name}'",
                        confidence=0.4,
                        suggestion=(
                            "Coefficient sign changes across specifications "
                            "may indicate sensitivity to controls or "
                            "specification issues. Consider discussing "
                            "robustness of the sign."
                        ),
                    ))

            # Check for implausible magnitude changes (>10x between columns)
            magnitudes = [(idx, abs(v)) for idx, v in values if v != 0]
            if len(magnitudes) >= 2:
                max_mag = max(m for _, m in magnitudes)
                min_mag = min(m for _, m in magnitudes)
                if min_mag > 0 and max_mag / min_mag > 20:
                    violations.append(ConsistencyViolation(
                        rule_id=RuleID.COLUMN_PROGRESSION,
                        severity=Severity.WARNING,
                        table_id=table.table_id,
                        description=(
                            f"Extreme magnitude change for '{var_name}': "
                            f"ratio = {max_mag/min_mag:.1f}x across specifications"
                        ),
                        evidence=(
                            f"Range: [{min_mag:.4g}, {max_mag:.4g}]"
                        ),
                        location=f"Variable '{var_name}'",
                        confidence=0.5,
                        suggestion=(
                            "A coefficient magnitude change of >20x across "
                            "specifications is unusual. Check for unit "
                            "differences or transcription errors."
                        ),
                    ))

        return violations

    # ==================================================================
    # Rule 7: Descriptive Stats Internal Consistency
    # ==================================================================

    def _rule_descriptive_internal(
        self, table: EconTable
    ) -> list[ConsistencyViolation]:
        """
        Check internal consistency of descriptive statistics:
        - Mean should be between Min and Max
        - SD > 0 for non-constant variables
        - N should be consistent across rows (same sample)
        """
        violations: list[ConsistencyViolation] = []

        n_values: list[int] = []

        for desc in table.descriptive_columns:
            var = desc.variable_name

            # Mean between Min and Max
            if (
                desc.mean is not None
                and desc.minimum is not None
                and desc.maximum is not None
            ):
                if desc.mean < desc.minimum - 0.001:
                    violations.append(ConsistencyViolation(
                        rule_id=RuleID.DESCRIPTIVE_INTERNAL,
                        severity=Severity.ERROR,
                        table_id=table.table_id,
                        description=(
                            f"Mean ({desc.mean:.4g}) < Min ({desc.minimum:.4g}) "
                            f"for variable '{var}'"
                        ),
                        location=f"Variable '{var}'",
                        expected=f"Mean ≥ Min ({desc.minimum:.4g})",
                        actual=f"Mean = {desc.mean:.4g}",
                        confidence=0.95,
                    ))
                if desc.mean > desc.maximum + 0.001:
                    violations.append(ConsistencyViolation(
                        rule_id=RuleID.DESCRIPTIVE_INTERNAL,
                        severity=Severity.ERROR,
                        table_id=table.table_id,
                        description=(
                            f"Mean ({desc.mean:.4g}) > Max ({desc.maximum:.4g}) "
                            f"for variable '{var}'"
                        ),
                        location=f"Variable '{var}'",
                        expected=f"Mean ≤ Max ({desc.maximum:.4g})",
                        actual=f"Mean = {desc.mean:.4g}",
                        confidence=0.95,
                    ))

            # Min ≤ Max
            if desc.minimum is not None and desc.maximum is not None:
                if desc.minimum > desc.maximum + 0.001:
                    violations.append(ConsistencyViolation(
                        rule_id=RuleID.DESCRIPTIVE_INTERNAL,
                        severity=Severity.ERROR,
                        table_id=table.table_id,
                        description=(
                            f"Min ({desc.minimum:.4g}) > Max ({desc.maximum:.4g}) "
                            f"for variable '{var}'"
                        ),
                        location=f"Variable '{var}'",
                        confidence=0.99,
                    ))

            # SD > 0 (unless variable is constant)
            if desc.std_dev is not None:
                if desc.std_dev < 0:
                    violations.append(ConsistencyViolation(
                        rule_id=RuleID.DESCRIPTIVE_INTERNAL,
                        severity=Severity.ERROR,
                        table_id=table.table_id,
                        description=(
                            f"Negative standard deviation "
                            f"(SD={desc.std_dev:.4g}) for '{var}'"
                        ),
                        location=f"Variable '{var}'",
                        confidence=0.99,
                    ))
                elif desc.std_dev == 0:
                    # Constant variable—only INFO
                    if desc.minimum is not None and desc.maximum is not None:
                        if desc.minimum != desc.maximum:
                            violations.append(ConsistencyViolation(
                                rule_id=RuleID.DESCRIPTIVE_INTERNAL,
                                severity=Severity.WARNING,
                                table_id=table.table_id,
                                description=(
                                    f"SD=0 but Min≠Max for '{var}'"
                                ),
                                location=f"Variable '{var}'",
                                confidence=0.8,
                            ))

            # Collect N values for cross-row consistency
            if desc.n_observations is not None:
                n_values.append(desc.n_observations)

        # Check N consistency (all variables in same sample should have same N)
        if len(n_values) >= 3:
            unique_n = set(n_values)
            if len(unique_n) > 1:
                # Some variation is normal (missing values), but large
                # deviations are suspicious
                max_n = max(n_values)
                min_n = min(n_values)
                if max_n - min_n > max_n * 0.3:  # >30% difference
                    violations.append(ConsistencyViolation(
                        rule_id=RuleID.DESCRIPTIVE_INTERNAL,
                        severity=Severity.INFO,
                        table_id=table.table_id,
                        description=(
                            f"Large N variation across variables "
                            f"(range: {min_n:,} to {max_n:,}). "
                            f"Consider noting which variables have missing values."
                        ),
                        evidence=f"Unique N values: {sorted(unique_n)}",
                        confidence=0.4,
                    ))

        return violations

    # ==================================================================
    # Rule 8: Text-Table Cross Reference
    # ==================================================================

    def _rule_text_table_cross_ref(
        self,
        tables: list[EconTable],
        paper_text: str,
    ) -> list[ConsistencyViolation]:
        """
        Cross-reference claims in the paper text with table values.

        Detects:
        - Specific numbers quoted in text that don't match tables
        - Significance claims that conflict with table stars
        - "Table N" references with incorrect column claims
        """
        violations: list[ConsistencyViolation] = []

        # Pattern: "coefficient of X.XX" or "estimate of X.XX"
        coeff_in_text = re.findall(
            r"(?:coefficient|estimate|effect)\s+(?:of\s+|is\s+|=\s*)"
            r"([-+]?\d+\.?\d*)",
            paper_text,
            re.IGNORECASE,
        )

        # Pattern: "significant at the N% level"
        significance_claims = re.findall(
            r"significant\s+at\s+(?:the\s+)?(\d+)\s*(?:%|percent)\s*level",
            paper_text,
            re.IGNORECASE,
        )

        # Pattern: "Table N, Column M" references
        table_col_refs = re.findall(
            r"Table\s+(\d+).*?[Cc]olumn\s+\(?(\d+)\)?",
            paper_text,
        )

        # Check coefficient references against table values
        for coeff_text in coeff_in_text:
            try:
                text_value = float(coeff_text)
            except ValueError:
                continue

            # Search all tables for this coefficient value
            found_match = False
            for table in tables:
                for col in table.regression_columns:
                    for entry in col.coefficients:
                        if entry.coefficient is not None:
                            # Allow small rounding differences
                            if abs(entry.coefficient - text_value) < 0.005:
                                found_match = True
                                break
                    if found_match:
                        break
                if found_match:
                    break

            # Not finding a match isn't necessarily an error
            # (could reference a different table or appendix)
            # Only flag if we have good coverage of the paper's tables
            if not found_match and len(tables) >= 3:
                violations.append(ConsistencyViolation(
                    rule_id=RuleID.TEXT_TABLE_CROSS_REF,
                    severity=Severity.INFO,
                    table_id="text",
                    description=(
                        f"Text mentions coefficient value {text_value} "
                        f"but no matching value found in parsed tables"
                    ),
                    confidence=0.3,
                    suggestion=(
                        "This coefficient value from the text was not found "
                        "in the extracted tables. It may reference an appendix "
                        "table or the extraction may have missed it."
                    ),
                ))

        # Check significance claims
        for sig_level in significance_claims:
            try:
                level_pct = int(sig_level)
            except ValueError:
                continue

            expected_stars = {1: 3, 5: 2, 10: 1}.get(level_pct, 0)
            if expected_stars == 0:
                continue

            # This is informational—hard to pin to specific table cell
            # without more context. Just flag if no table has any coefficients
            # with that star level.
            has_matching_stars = any(
                entry.stars >= expected_stars
                for table in tables
                for col in table.regression_columns
                for entry in col.coefficients
            )

            if not has_matching_stars and len(tables) >= 2:
                violations.append(ConsistencyViolation(
                    rule_id=RuleID.TEXT_TABLE_CROSS_REF,
                    severity=Severity.INFO,
                    table_id="text",
                    description=(
                        f"Text claims significance at {level_pct}% level "
                        f"but no coefficient with ≥{expected_stars} stars "
                        f"found in extracted tables"
                    ),
                    confidence=0.3,
                ))

        return violations

    # ==================================================================
    # Rule 9: Cross-Table Comparison
    # ==================================================================

    def _rule_cross_table_comparison(
        self, tables: list[EconTable]
    ) -> list[ConsistencyViolation]:
        """
        Detect suspicious data duplication or implausible overlap between tables.

        Checks for:
        1. Identical coefficient vectors across different tables
           (e.g., Table A.3 and Table A.4 sharing the exact same numbers)
        2. Implausibly identical SEs across tables with different samples
        3. Appendix tables that are exact copies of main-body tables
        4. Same regression coefficients appearing in tables that claim
           different specifications or sub-samples
        """
        violations: list[ConsistencyViolation] = []

        # Extract coefficient vectors per table
        table_vectors: list[tuple[str, list[float], EconTable]] = []
        for table in tables:
            for col in table.regression_columns:
                coeff_vec = [
                    entry.coefficient
                    for entry in col.coefficients
                    if entry.coefficient is not None
                ]
                if len(coeff_vec) >= 3:  # Need at least 3 values to compare
                    label = f"{table.table_id}:col{col.column_index}"
                    table_vectors.append((label, coeff_vec, table))

        # Pairwise comparison
        for i in range(len(table_vectors)):
            for j in range(i + 1, len(table_vectors)):
                label_a, vec_a, table_a = table_vectors[i]
                label_b, vec_b, table_b = table_vectors[j]

                # Skip columns within the same table (normal to have overlap)
                if table_a.table_id == table_b.table_id:
                    continue

                # Check 1: Identical coefficient vectors
                overlap = self._vector_overlap_ratio(vec_a, vec_b)
                if overlap >= 0.95:
                    violations.append(ConsistencyViolation(
                        rule_id=RuleID.CROSS_TABLE_COMPARISON,
                        severity=Severity.WARNING,
                        table_id=f"{table_a.table_id} vs {table_b.table_id}",
                        description=(
                            f"Near-identical coefficient vectors between "
                            f"{label_a} and {label_b} "
                            f"(overlap ratio: {overlap:.0%}). "
                            f"This may indicate data duplication or a "
                            f"copy-paste error between tables."
                        ),
                        evidence=(
                            f"Vec A ({len(vec_a)} coeffs): "
                            f"{[f'{v:.4g}' for v in vec_a[:5]]}... | "
                            f"Vec B ({len(vec_b)} coeffs): "
                            f"{[f'{v:.4g}' for v in vec_b[:5]]}..."
                        ),
                        confidence=0.85 if overlap >= 0.99 else 0.65,
                        suggestion=(
                            "Verify that these tables report different "
                            "specifications/samples. If they use the same "
                            "sample, differences in controls should produce "
                            "different point estimates."
                        ),
                    ))
                elif overlap >= 0.80:
                    # High overlap — less certain but worth flagging
                    violations.append(ConsistencyViolation(
                        rule_id=RuleID.CROSS_TABLE_COMPARISON,
                        severity=Severity.INFO,
                        table_id=f"{table_a.table_id} vs {table_b.table_id}",
                        description=(
                            f"High coefficient overlap between "
                            f"{label_a} and {label_b} "
                            f"(overlap ratio: {overlap:.0%}). "
                            f"If these are different sub-samples or "
                            f"specifications, the similarity is unusual."
                        ),
                        confidence=0.4,
                    ))

        # Check 2: Identical SE vectors across different-sample tables
        se_vectors: list[tuple[str, list[float], EconTable]] = []
        for table in tables:
            for col in table.regression_columns:
                se_vec = [
                    entry.standard_error
                    for entry in col.coefficients
                    if entry.standard_error is not None
                ]
                if len(se_vec) >= 3:
                    label = f"{table.table_id}:col{col.column_index}"
                    se_vectors.append((label, se_vec, table))

        for i in range(len(se_vectors)):
            for j in range(i + 1, len(se_vectors)):
                label_a, se_a, table_a = se_vectors[i]
                label_b, se_b, table_b = se_vectors[j]

                if table_a.table_id == table_b.table_id:
                    continue

                se_overlap = self._vector_overlap_ratio(se_a, se_b)
                if se_overlap >= 0.95:
                    # Identical SEs across different tables is very suspicious
                    # (even same sample with different controls changes SEs)
                    violations.append(ConsistencyViolation(
                        rule_id=RuleID.CROSS_TABLE_COMPARISON,
                        severity=Severity.WARNING,
                        table_id=f"{table_a.table_id} vs {table_b.table_id}",
                        description=(
                            f"Near-identical standard errors between "
                            f"{label_a} and {label_b} "
                            f"(overlap: {se_overlap:.0%}). "
                            f"Adding/removing controls almost always changes SEs."
                        ),
                        confidence=0.75,
                        suggestion=(
                            "Identical SEs across tables are extremely unlikely "
                            "unless these are exact duplicates. Verify that each "
                            "table uses a distinct specification."
                        ),
                    ))

        # Check 3: Sample size anomalies across tables
        violations.extend(self._check_cross_table_sample_anomalies(tables))

        return violations

    def _vector_overlap_ratio(
        self, vec_a: list[float], vec_b: list[float]
    ) -> float:
        """Compute element-wise overlap ratio between two numeric vectors.

        Returns the fraction of elements that are approximately equal
        (within 0.1% relative tolerance or 1e-6 absolute tolerance).
        """
        min_len = min(len(vec_a), len(vec_b))
        if min_len == 0:
            return 0.0

        matches = 0
        for a, b in zip(vec_a[:min_len], vec_b[:min_len]):
            # Relative tolerance check
            if a == b == 0:
                matches += 1
            elif abs(a) > 1e-10:
                if abs(a - b) / abs(a) < 0.001:
                    matches += 1
            elif abs(a - b) < 1e-6:
                matches += 1

        return matches / min_len

    def _check_cross_table_sample_anomalies(
        self, tables: list[EconTable]
    ) -> list[ConsistencyViolation]:
        """Check for implausible sample size patterns across tables.

        E.g., two tables claim different sub-samples but report identical N,
        or an appendix table has more observations than the main table.
        """
        violations: list[ConsistencyViolation] = []

        # Collect (table_id, column_index, N) tuples
        sample_info: list[tuple[str, int, int]] = []
        for table in tables:
            for col in table.regression_columns:
                if col.n_observations is not None:
                    sample_info.append(
                        (table.table_id, col.column_index, col.n_observations)
                    )

        # Detect: appendix table N > main table max N
        # Heuristic: tables with "A" prefix or number > 5 are likely appendix
        main_ns = []
        appendix_ns = []
        for tid, _col_idx, n in sample_info:
            tid_lower = tid.lower()
            if any(pat in tid_lower for pat in ("appendix", "table a", "table_a", "online")):
                appendix_ns.append((tid, n))
            else:
                # Try to detect from table numbering
                num_match = re.search(r"(\d+)", tid)
                if num_match and int(num_match.group(1)) > 10:
                    appendix_ns.append((tid, n))
                else:
                    main_ns.append((tid, n))

        if main_ns and appendix_ns:
            max_main_n = max(n for _, n in main_ns)
            for app_tid, app_n in appendix_ns:
                if app_n > max_main_n * 1.5:
                    violations.append(ConsistencyViolation(
                        rule_id=RuleID.CROSS_TABLE_COMPARISON,
                        severity=Severity.INFO,
                        table_id=app_tid,
                        description=(
                            f"Appendix table '{app_tid}' reports N={app_n:,}, "
                            f"which exceeds max main-table N ({max_main_n:,}) "
                            f"by >50%. Verify sample definition."
                        ),
                        confidence=0.35,
                    ))

        return violations

    # ==================================================================
    # Rule 10: Cross-Table Duplication (Cell-Level Full Matrix Comparison)
    # ==================================================================

    def _rule_cross_table_duplication(
        self, tables: list[EconTable]
    ) -> list[ConsistencyViolation]:
        """
        Detect EXACT or near-exact data duplication between tables at cell level.

        Unlike Rule 9 which only checks regression coefficient/SE vectors,
        this rule compares the full numeric matrix of ALL table types —
        especially balance tables and descriptive statistics tables where
        the critical data is in mean/SD/p-value cells rather than regression
        columns.

        Target scenario (G005-001):
          Table A.3 (Information treatment balance) and Table A.4 (Credibility
          treatment balance) contain completely identical data — all means, SDs,
          and p-values are the same. Since the two treatments use different
          randomization splits, they cannot produce identical subsamples.
        """
        violations: list[ConsistencyViolation] = []

        # Strategy 1: Compare raw numeric matrices from source_raw
        raw_matrices = self._extract_numeric_matrices(tables)

        for i in range(len(raw_matrices)):
            for j in range(i + 1, len(raw_matrices)):
                tid_a, matrix_a = raw_matrices[i]
                tid_b, matrix_b = raw_matrices[j]

                if not matrix_a or not matrix_b:
                    continue

                overlap = self._matrix_overlap_ratio(matrix_a, matrix_b)

                if overlap >= 0.95:
                    violations.append(ConsistencyViolation(
                        rule_id=RuleID.CROSS_TABLE_DUPLICATION,
                        severity=Severity.ERROR,
                        table_id=f"{tid_a} vs {tid_b}",
                        description=(
                            f"Tables '{tid_a}' and '{tid_b}' share "
                            f"{overlap:.0%} identical numeric cells. "
                            f"This is almost certainly a tabulation error "
                            f"(copy-paste duplication). If these tables "
                            f"represent different treatments/samples, their "
                            f"data should differ."
                        ),
                        evidence=(
                            f"Matrix A: {len(matrix_a)} numeric cells | "
                            f"Matrix B: {len(matrix_b)} numeric cells | "
                            f"Overlap: {overlap:.1%}"
                        ),
                        confidence=0.95,
                        suggestion=(
                            "Verify that these tables report different "
                            "subsamples/treatments. Identical balance statistics "
                            "across different randomization arms is statistically "
                            "impossible and indicates a data/copy error."
                        ),
                    ))
                elif overlap >= 0.80:
                    violations.append(ConsistencyViolation(
                        rule_id=RuleID.CROSS_TABLE_DUPLICATION,
                        severity=Severity.WARNING,
                        table_id=f"{tid_a} vs {tid_b}",
                        description=(
                            f"Tables '{tid_a}' and '{tid_b}' share "
                            f"{overlap:.0%} identical numeric cells. "
                            f"If these represent different samples/treatments, "
                            f"this level of similarity is suspicious."
                        ),
                        evidence=(
                            f"Matrix A: {len(matrix_a)} cells | "
                            f"Matrix B: {len(matrix_b)} cells | "
                            f"Overlap: {overlap:.1%}"
                        ),
                        confidence=0.70,
                        suggestion=(
                            "Check whether the underlying data for these tables "
                            "was correctly split between treatment arms."
                        ),
                    ))

        # Strategy 2: Compare descriptive_columns semantically
        desc_violations = self._compare_descriptive_columns(tables)
        violations.extend(desc_violations)

        return violations

    def _extract_numeric_matrices(
        self, tables: list[EconTable]
    ) -> list[tuple[str, list[float]]]:
        """Extract flat numeric value lists from each table's raw body."""
        results = []
        for table in tables:
            numerics: list[float] = []
            if table.source_raw and table.source_raw.body:
                for row in table.source_raw.body:
                    for cell in row:
                        if cell.numeric is not None:
                            numerics.append(cell.numeric)
            # Fallback: extract from descriptive_columns
            if not numerics and table.descriptive_columns:
                for col in table.descriptive_columns:
                    for val in (
                        col.mean, col.std_dev, col.minimum, col.maximum,
                        col.median, col.percentile_25, col.percentile_75,
                    ):
                        if val is not None:
                            numerics.append(val)
                    if col.n_observations is not None:
                        numerics.append(float(col.n_observations))

            if numerics:
                results.append((table.table_id, numerics))
        return results

    def _matrix_overlap_ratio(
        self, matrix_a: list[float], matrix_b: list[float]
    ) -> float:
        """Compute cell-level overlap ratio between two numeric matrices.

        Compares values positionally. If matrices differ in length, uses
        the shorter length. Returns fraction of matching cells.
        """
        min_len = min(len(matrix_a), len(matrix_b))
        max_len = max(len(matrix_a), len(matrix_b))

        if min_len == 0:
            return 0.0

        # Minimum cell count threshold: tables with fewer than 8 numeric cells
        # are too small for meaningful duplication detection. Small calibration
        # tables (e.g., σ=4.4, θ=5.1, ε=3.0) trivially match at 100% and
        # produce false positives in theoretical/calibration papers.
        if max_len < 8:
            return 0.0

        # Require similar dimensions (within 20% length difference)
        if min_len < max_len * 0.8:
            return 0.0

        matches = 0
        for a, b in zip(matrix_a[:min_len], matrix_b[:min_len]):
            if a == b == 0:
                matches += 1
            elif abs(a) > 1e-10:
                if abs(a - b) / abs(a) < 0.001:
                    matches += 1
            elif abs(a - b) < 1e-6:
                matches += 1

        return matches / min_len

    def _compare_descriptive_columns(
        self, tables: list[EconTable]
    ) -> list[ConsistencyViolation]:
        """Compare descriptive/balance tables using semantic column data.

        This catches the case where source_raw is not available but
        descriptive_columns are parsed.
        """
        violations: list[ConsistencyViolation] = []

        # Filter to descriptive/balance tables
        desc_tables = [
            t for t in tables
            if t.table_type in (
                EconTableType.DESCRIPTIVE_STATS,
                EconTableType.BALANCE_TABLE,
            )
            and t.descriptive_columns
        ]

        for i in range(len(desc_tables)):
            for j in range(i + 1, len(desc_tables)):
                table_a = desc_tables[i]
                table_b = desc_tables[j]

                # Compare mean/SD vectors
                means_a = [
                    c.mean for c in table_a.descriptive_columns
                    if c.mean is not None
                ]
                means_b = [
                    c.mean for c in table_b.descriptive_columns
                    if c.mean is not None
                ]
                sds_a = [
                    c.std_dev for c in table_a.descriptive_columns
                    if c.std_dev is not None
                ]
                sds_b = [
                    c.std_dev for c in table_b.descriptive_columns
                    if c.std_dev is not None
                ]

                if len(means_a) >= 3 and len(means_b) >= 3:
                    mean_overlap = self._vector_overlap_ratio(means_a, means_b)
                    sd_overlap = (
                        self._vector_overlap_ratio(sds_a, sds_b)
                        if sds_a and sds_b else 0.0
                    )

                    # Both means AND SDs identical → almost certainly an error
                    if mean_overlap >= 0.95 and sd_overlap >= 0.95:
                        violations.append(ConsistencyViolation(
                            rule_id=RuleID.CROSS_TABLE_DUPLICATION,
                            severity=Severity.ERROR,
                            table_id=(
                                f"{table_a.table_id} vs {table_b.table_id}"
                            ),
                            description=(
                                f"Balance/descriptive tables '{table_a.table_id}' "
                                f"and '{table_b.table_id}' report identical "
                                f"means (overlap: {mean_overlap:.0%}) and SDs "
                                f"(overlap: {sd_overlap:.0%}). "
                                f"If these represent different treatment arms or "
                                f"samples, identical summary statistics are "
                                f"statistically impossible."
                            ),
                            evidence=(
                                f"Means A: {[f'{v:.4g}' for v in means_a[:5]]}... | "
                                f"Means B: {[f'{v:.4g}' for v in means_b[:5]]}... | "
                                f"SDs A: {[f'{v:.4g}' for v in sds_a[:5]]}... | "
                                f"SDs B: {[f'{v:.4g}' for v in sds_b[:5]]}..."
                            ),
                            confidence=0.95,
                            suggestion=(
                                "This is a confirmed tabulation error. The same "
                                "data appears in both tables. One table's data "
                                "should reflect a different treatment/control "
                                "subsample."
                            ),
                        ))
                    elif mean_overlap >= 0.80:
                        violations.append(ConsistencyViolation(
                            rule_id=RuleID.CROSS_TABLE_DUPLICATION,
                            severity=Severity.WARNING,
                            table_id=(
                                f"{table_a.table_id} vs {table_b.table_id}"
                            ),
                            description=(
                                f"Descriptive tables '{table_a.table_id}' and "
                                f"'{table_b.table_id}' have unusually similar "
                                f"means (overlap: {mean_overlap:.0%}). "
                                f"Verify that these report different subsamples."
                            ),
                            confidence=0.55,
                        ))

        return violations
