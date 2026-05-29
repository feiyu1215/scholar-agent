"""
Economics Table Semantic Parser.

Interprets raw extracted tables through the lens of econometric conventions:
  - Regression result tables (coefficients, SEs, stars, controls, FEs)
  - Descriptive statistics tables (mean, sd, min, max, N)
  - Summary/balance tables (treatment vs control group comparisons)
  - First-stage / IV tables
  - Robustness check tables

This module transforms RawTable → EconTable with full semantic annotation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .table_parser import CellValue, RawTable


class EconTableType(Enum):
    """Classification of economic table types."""
    REGRESSION = "regression"
    DESCRIPTIVE_STATS = "descriptive_stats"
    BALANCE_TABLE = "balance_table"
    FIRST_STAGE = "first_stage"
    ROBUSTNESS = "robustness"
    HETEROGENEITY = "heterogeneity"
    UNKNOWN = "unknown"


class SEType(Enum):
    """Standard error type used in the table."""
    REGULAR = "regular"
    ROBUST = "robust"
    CLUSTERED = "clustered"
    BOOTSTRAPPED = "bootstrapped"
    HAC = "hac"  # Heteroskedasticity and Autocorrelation Consistent
    UNKNOWN = "unknown"


class StarConvention(Enum):
    """Significance star convention."""
    STANDARD = "standard"  # *p<0.1, **p<0.05, ***p<0.01
    REVERSED = "reversed"  # *p<0.01, **p<0.05, ***p<0.1 (rare)
    CUSTOM = "custom"
    UNKNOWN = "unknown"


@dataclass
class CoefficientEntry:
    """A single coefficient with its standard error and significance."""
    variable_name: str
    coefficient: Optional[float] = None
    standard_error: Optional[float] = None
    t_stat: Optional[float] = None
    p_value: Optional[float] = None
    stars: int = 0  # 0-3
    confidence_interval: Optional[tuple[float, float]] = None
    is_significant_at_10: bool = False
    is_significant_at_05: bool = False
    is_significant_at_01: bool = False
    column_index: int = 0  # which regression column

    def __post_init__(self) -> None:
        # Derive significance from stars if not explicitly set
        if self.stars >= 1:
            self.is_significant_at_10 = True
        if self.stars >= 2:
            self.is_significant_at_05 = True
        if self.stars >= 3:
            self.is_significant_at_01 = True


@dataclass
class RegressionColumn:
    """A single regression specification (one column of a regression table)."""
    column_index: int
    column_header: str = ""
    dependent_variable: str = ""
    coefficients: list[CoefficientEntry] = field(default_factory=list)
    n_observations: Optional[int] = None
    r_squared: Optional[float] = None
    adjusted_r_squared: Optional[float] = None
    f_statistic: Optional[float] = None
    fixed_effects: list[str] = field(default_factory=list)
    controls: list[str] = field(default_factory=list)
    se_type: SEType = SEType.UNKNOWN
    cluster_variable: str = ""
    sample_description: str = ""


@dataclass
class DescriptiveColumn:
    """A column in a descriptive statistics table."""
    column_header: str = ""
    variable_name: str = ""
    mean: Optional[float] = None
    std_dev: Optional[float] = None
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    median: Optional[float] = None
    n_observations: Optional[int] = None
    percentile_25: Optional[float] = None
    percentile_75: Optional[float] = None


@dataclass
class EconTable:
    """A fully interpreted economics table."""
    table_id: str
    table_type: EconTableType
    caption: str = ""
    notes: str = ""

    # Regression-specific
    regression_columns: list[RegressionColumn] = field(default_factory=list)
    star_convention: StarConvention = StarConvention.UNKNOWN
    se_type: SEType = SEType.UNKNOWN

    # Descriptive stats-specific
    descriptive_columns: list[DescriptiveColumn] = field(default_factory=list)

    # Metadata
    n_columns: int = 0
    panel_labels: list[str] = field(default_factory=list)
    source_raw: Optional[RawTable] = None

    @property
    def all_sample_sizes(self) -> list[int]:
        """Collect all N values across columns."""
        sizes = []
        for col in self.regression_columns:
            if col.n_observations is not None:
                sizes.append(col.n_observations)
        return sizes

    @property
    def all_r_squared(self) -> list[float]:
        """Collect all R² values across columns."""
        return [
            col.r_squared
            for col in self.regression_columns
            if col.r_squared is not None
        ]


# ==============================================================
# Economics Table Semantic Parser
# ==============================================================


class EconTableParser:
    """
    Interprets RawTable objects as economics tables with full
    semantic understanding of econometric conventions.
    """

    # Patterns for detecting table type
    _REGRESSION_INDICATORS = [
        r"(?:dep(?:endent)?\.?\s*var|dependent\s+variable)",
        r"(?:ols|2sls|iv|logit|probit|tobit|gmm|fe|re)\b",
        r"(?:coefficient|coeff?\.?|beta|estimate)",
        r"(?:standard\s*error|s\.?e\.?|robust)",
        r"(?:observations|obs\.?|N\b|n\b)",
        r"(?:r-squared|r²|r\^2|adj\.?\s*r)",
        r"(?:fixed\s*effect|f\.?e\.?|cluster)",
        r"(?:controls?|control\s*var)",
    ]

    _DESCRIPTIVE_INDICATORS = [
        r"(?:mean|average|avg\.?)",
        r"(?:std\.?\s*dev|s\.?d\.?|standard\s+deviation)",
        r"(?:min(?:imum)?|max(?:imum)?)",
        r"(?:median|p50|percentile)",
        r"(?:observations|obs\.?|N\b|count)",
    ]

    _BALANCE_INDICATORS = [
        r"(?:treatment|control|placebo)",
        r"(?:difference|diff\.?|p-value)",
        r"(?:balance|baseline|pre-treatment)",
        r"(?:t-test|t-stat)",
    ]

    # Patterns for row labels
    _N_ROW_RE = re.compile(
        r"^(?:N|Observations?|Obs\.?|n|Sample\s*size|# Obs\.?)\s*$",
        re.IGNORECASE,
    )
    _R2_ROW_RE = re.compile(
        r"^(?:R-squared|R²|R\^2|R2|Adj\.?\s*R²?|Adjusted\s*R²?|"
        r"Within\s*R²?|R-sq\.?)\s*$",
        re.IGNORECASE,
    )
    _ADJ_R2_ROW_RE = re.compile(
        r"(?:adj|adjusted)", re.IGNORECASE
    )
    _FE_ROW_RE = re.compile(
        r"^(?:.*(?:fixed\s*effect|f\.?e\.?|FE|dummies).*|"
        r"(?:year|time|firm|industry|region|state|country|individual|entity)\s*"
        r"(?:fixed\s*effect|f\.?e\.?|FE|dummies)?)\s*$",
        re.IGNORECASE,
    )
    _CONTROLS_ROW_RE = re.compile(
        r"^(?:controls?|control\s*var(?:iable)?s?|additional\s*controls?)\s*$",
        re.IGNORECASE,
    )
    _SE_TYPE_RE = re.compile(
        r"(?:robust|clustered|cluster|HAC|Newey.West|bootstrap|heteroskedastic)",
        re.IGNORECASE,
    )
    _YES_NO_RE = re.compile(r"^(?:yes|no|x|✓|√|✗|×)\s*$", re.IGNORECASE)

    def parse(self, raw: RawTable) -> EconTable:
        """Parse a RawTable into a semantically-interpreted EconTable."""
        table_type = self._classify_table_type(raw)

        econ_table = EconTable(
            table_id=raw.table_id,
            table_type=table_type,
            caption=raw.caption,
            notes=raw.notes,
            source_raw=raw,
        )

        if table_type == EconTableType.REGRESSION:
            self._parse_regression_table(raw, econ_table)
        elif table_type == EconTableType.DESCRIPTIVE_STATS:
            self._parse_descriptive_table(raw, econ_table)
        elif table_type == EconTableType.BALANCE_TABLE:
            self._parse_balance_table(raw, econ_table)
        else:
            # Try regression as default for unknown
            self._parse_regression_table(raw, econ_table)

        # Detect star convention from notes
        econ_table.star_convention = self._detect_star_convention(raw.notes + " " + raw.caption)
        # Detect SE type from notes
        econ_table.se_type = self._detect_se_type(raw.notes + " " + raw.caption)
        econ_table.n_columns = len(econ_table.regression_columns) or len(econ_table.descriptive_columns)

        return econ_table

    def parse_all(self, raw_tables: list[RawTable]) -> list[EconTable]:
        """Parse multiple raw tables."""
        return [self.parse(raw) for raw in raw_tables]

    # ------------------------------------------------------------------
    # Table type classification
    # ------------------------------------------------------------------

    def _classify_table_type(self, raw: RawTable) -> EconTableType:
        """Classify the table type based on content analysis."""
        text_content = self._table_to_text(raw)

        scores = {
            EconTableType.REGRESSION: self._score_indicators(
                text_content, self._REGRESSION_INDICATORS
            ),
            EconTableType.DESCRIPTIVE_STATS: self._score_indicators(
                text_content, self._DESCRIPTIVE_INDICATORS
            ),
            EconTableType.BALANCE_TABLE: self._score_indicators(
                text_content, self._BALANCE_INDICATORS
            ),
        }

        # Additional heuristic: if body rows alternate coeff/SE pattern
        if self._has_coeff_se_pattern(raw):
            scores[EconTableType.REGRESSION] += 3

        best_type = max(scores, key=scores.get)  # type: ignore
        if scores[best_type] >= 2:
            return best_type
        return EconTableType.UNKNOWN

    def _score_indicators(self, text: str, patterns: list[str]) -> int:
        """Count how many indicator patterns match in the text."""
        score = 0
        for pattern in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                score += 1
        return score

    def _has_coeff_se_pattern(self, raw: RawTable) -> bool:
        """Check if body rows alternate between coefficient and SE rows."""
        if len(raw.body) < 4:
            return False

        # In regression tables, odd rows have coefficients (with stars),
        # even rows have SEs (in parentheses)
        paren_count = 0
        star_count = 0
        for i, row in enumerate(raw.body[:10]):
            for cell in row[1:]:  # Skip first column (variable names)
                if cell.is_parenthesized:
                    paren_count += 1
                if cell.has_stars > 0:
                    star_count += 1

        return paren_count >= 2 and star_count >= 1

    # ------------------------------------------------------------------
    # Regression table parsing
    # ------------------------------------------------------------------

    def _parse_regression_table(self, raw: RawTable, econ: EconTable) -> None:
        """Parse a regression results table."""
        # Determine number of regression columns from headers
        n_data_cols = raw.n_cols - 1 if raw.n_cols > 1 else 1  # First col = row labels
        headers = raw.flat_headers

        # Initialize regression columns
        for col_idx in range(n_data_cols):
            col_header = headers[col_idx + 1] if col_idx + 1 < len(headers) else f"({col_idx + 1})"
            reg_col = RegressionColumn(
                column_index=col_idx,
                column_header=col_header,
            )
            econ.regression_columns.append(reg_col)

        # Parse body rows
        i = 0
        while i < len(raw.body):
            row = raw.body[i]
            row_label = row[0].raw if row else ""

            # Check if this is a metadata row (N, R², FE, Controls)
            if self._N_ROW_RE.match(row_label):
                self._parse_n_row(row, econ)
            elif self._R2_ROW_RE.match(row_label):
                self._parse_r2_row(row, econ, is_adjusted=bool(self._ADJ_R2_ROW_RE.search(row_label)))
            elif self._FE_ROW_RE.match(row_label):
                self._parse_fe_row(row, econ)
            elif self._CONTROLS_ROW_RE.match(row_label):
                self._parse_controls_row(row, econ)
            elif self._is_coefficient_row(row):
                # This is a coefficient row, possibly followed by SE row
                se_row = None
                if i + 1 < len(raw.body):
                    next_row = raw.body[i + 1]
                    if self._is_se_row(next_row):
                        se_row = next_row
                        i += 1  # Skip SE row in next iteration

                self._parse_coefficient_row(row, se_row, econ)

            i += 1

    def _is_coefficient_row(self, row: list[CellValue]) -> bool:
        """Check if this row contains coefficients (numbers, possibly with stars)."""
        if not row:
            return False
        # First cell is variable name (text), rest should be numeric
        numeric_cells = sum(
            1 for c in row[1:] if c.numeric is not None or c.has_stars > 0
        )
        return numeric_cells > 0 and not row[0].is_empty

    def _is_se_row(self, row: list[CellValue]) -> bool:
        """Check if this row contains standard errors (parenthesized numbers)."""
        if not row:
            return False
        paren_cells = sum(1 for c in row[1:] if c.is_parenthesized)
        bracket_cells = sum(1 for c in row[1:] if c.is_bracketed)
        return paren_cells >= 1 or bracket_cells >= 1

    def _parse_coefficient_row(
        self,
        coeff_row: list[CellValue],
        se_row: Optional[list[CellValue]],
        econ: EconTable,
    ) -> None:
        """Parse a coefficient row (and its paired SE row if present)."""
        variable_name = coeff_row[0].raw

        for col_idx, reg_col in enumerate(econ.regression_columns):
            data_idx = col_idx + 1  # offset for row label column

            coeff_val = None
            stars = 0
            se_val = None

            if data_idx < len(coeff_row):
                cell = coeff_row[data_idx]
                coeff_val = cell.numeric
                stars = cell.has_stars

            if se_row and data_idx < len(se_row):
                se_cell = se_row[data_idx]
                se_val = se_cell.numeric

            if coeff_val is not None or stars > 0:
                entry = CoefficientEntry(
                    variable_name=variable_name,
                    coefficient=coeff_val,
                    standard_error=se_val,
                    stars=stars,
                    column_index=col_idx,
                )
                reg_col.coefficients.append(entry)

    def _parse_n_row(self, row: list[CellValue], econ: EconTable) -> None:
        """Parse the Observations/N row."""
        for col_idx, reg_col in enumerate(econ.regression_columns):
            data_idx = col_idx + 1
            if data_idx < len(row):
                cell = row[data_idx]
                if cell.numeric is not None:
                    reg_col.n_observations = int(cell.numeric)
                else:
                    # Try parsing with comma removal (e.g., "12,345")
                    cleaned = cell.raw.replace(",", "").replace(" ", "").strip()
                    try:
                        reg_col.n_observations = int(float(cleaned))
                    except (ValueError, TypeError):
                        pass

    def _parse_r2_row(
        self, row: list[CellValue], econ: EconTable, is_adjusted: bool = False
    ) -> None:
        """Parse the R-squared row."""
        for col_idx, reg_col in enumerate(econ.regression_columns):
            data_idx = col_idx + 1
            if data_idx < len(row):
                cell = row[data_idx]
                val = cell.numeric
                if val is not None:
                    if is_adjusted:
                        reg_col.adjusted_r_squared = val
                    else:
                        reg_col.r_squared = val

    def _parse_fe_row(self, row: list[CellValue], econ: EconTable) -> None:
        """Parse a fixed effects indicator row."""
        row_label = row[0].raw if row else ""
        # Extract the FE type from the label
        fe_name = row_label.strip()

        for col_idx, reg_col in enumerate(econ.regression_columns):
            data_idx = col_idx + 1
            if data_idx < len(row):
                cell = row[data_idx]
                if self._YES_NO_RE.match(cell.raw) and cell.raw.lower() in (
                    "yes", "x", "✓", "√"
                ):
                    reg_col.fixed_effects.append(fe_name)

    def _parse_controls_row(self, row: list[CellValue], econ: EconTable) -> None:
        """Parse a controls indicator row."""
        row_label = row[0].raw if row else ""
        for col_idx, reg_col in enumerate(econ.regression_columns):
            data_idx = col_idx + 1
            if data_idx < len(row):
                cell = row[data_idx]
                if self._YES_NO_RE.match(cell.raw) and cell.raw.lower() in (
                    "yes", "x", "✓", "√"
                ):
                    reg_col.controls.append(row_label.strip())

    # ------------------------------------------------------------------
    # Descriptive statistics parsing
    # ------------------------------------------------------------------

    def _parse_descriptive_table(self, raw: RawTable, econ: EconTable) -> None:
        """Parse a descriptive statistics table."""
        headers = raw.flat_headers
        # Common layouts:
        # A) rows = variables, columns = Mean, SD, Min, Max, N
        # B) rows = stats, columns = variables

        # Detect layout by checking if column headers look like stat names
        stat_cols = self._detect_stat_columns(headers)

        if stat_cols:
            # Layout A: rows = variables
            self._parse_descriptive_layout_a(raw, econ, stat_cols)
        else:
            # Layout B or unknown
            self._parse_descriptive_layout_b(raw, econ)

    def _detect_stat_columns(self, headers: list[str]) -> dict[str, int]:
        """Detect which columns correspond to which statistics."""
        mapping: dict[str, int] = {}
        stat_patterns = {
            "mean": r"(?:mean|average|avg\.?)",
            "std_dev": r"(?:std\.?\s*dev\.?|s\.?d\.?|standard\s*dev)",
            "min": r"(?:min(?:imum)?)",
            "max": r"(?:max(?:imum)?)",
            "median": r"(?:median|p50)",
            "n": r"(?:N|obs|observations?|count|n)",
            "p25": r"(?:p25|25th|q1)",
            "p75": r"(?:p75|75th|q3)",
        }

        for col_idx, header in enumerate(headers):
            for stat_name, pattern in stat_patterns.items():
                if re.search(pattern, header, re.IGNORECASE):
                    mapping[stat_name] = col_idx
                    break

        return mapping

    def _parse_descriptive_layout_a(
        self, raw: RawTable, econ: EconTable, stat_cols: dict[str, int]
    ) -> None:
        """Parse descriptive table where rows = variables, cols = statistics."""
        for row in raw.body:
            if not row or row[0].is_empty:
                continue

            desc = DescriptiveColumn(variable_name=row[0].raw)

            for stat_name, col_idx in stat_cols.items():
                if col_idx < len(row):
                    val = row[col_idx].numeric
                    if stat_name == "mean":
                        desc.mean = val
                    elif stat_name == "std_dev":
                        desc.std_dev = val
                    elif stat_name == "min":
                        desc.minimum = val
                    elif stat_name == "max":
                        desc.maximum = val
                    elif stat_name == "median":
                        desc.median = val
                    elif stat_name == "n" and val is not None:
                        desc.n_observations = int(val)
                    elif stat_name == "p25":
                        desc.percentile_25 = val
                    elif stat_name == "p75":
                        desc.percentile_75 = val

            econ.descriptive_columns.append(desc)

    def _parse_descriptive_layout_b(self, raw: RawTable, econ: EconTable) -> None:
        """Fallback: parse descriptive table with unknown layout."""
        # Simple approach: treat each body row as a variable's stats
        for row in raw.body:
            if not row or row[0].is_empty:
                continue
            desc = DescriptiveColumn(variable_name=row[0].raw)
            # Assign numbers in order to common stats
            numbers = [c.numeric for c in row[1:] if c.numeric is not None]
            if len(numbers) >= 1:
                desc.mean = numbers[0]
            if len(numbers) >= 2:
                desc.std_dev = numbers[1]
            if len(numbers) >= 3:
                desc.minimum = numbers[2]
            if len(numbers) >= 4:
                desc.maximum = numbers[3]
            if len(numbers) >= 5:
                desc.n_observations = int(numbers[4])
            econ.descriptive_columns.append(desc)

    # ------------------------------------------------------------------
    # Balance table parsing
    # ------------------------------------------------------------------

    def _parse_balance_table(self, raw: RawTable, econ: EconTable) -> None:
        """Parse a balance/comparison table (treatment vs control)."""
        # Balance tables are structurally similar to descriptive stats
        # but with treatment/control columns + difference + p-value
        self._parse_descriptive_table(raw, econ)
        econ.table_type = EconTableType.BALANCE_TABLE

    # ------------------------------------------------------------------
    # Metadata detection
    # ------------------------------------------------------------------

    def _detect_star_convention(self, text: str) -> StarConvention:
        """Detect the significance star convention from table notes."""
        # Standard: ***p<0.01, **p<0.05, *p<0.1
        standard_match = re.search(
            r"\*{3}\s*(?:p\s*[<≤]\s*0\.01|1%)", text, re.IGNORECASE
        )
        if standard_match:
            return StarConvention.STANDARD

        # Reversed (rare): *p<0.01 or *1%  (single star = most significant)
        reversed_match = re.search(
            r"(?<!\*)\*\s*(?:p\s*[<≤]\s*0\.01|1%)", text, re.IGNORECASE
        )
        if reversed_match:
            return StarConvention.REVERSED

        # Check for any star notation — default to STANDARD
        any_star = re.search(r"\*\s*(?:p\s*[<≤]|significant)", text, re.IGNORECASE)
        if any_star:
            return StarConvention.STANDARD

        return StarConvention.UNKNOWN

    def _detect_se_type(self, text: str) -> SEType:
        """Detect the standard error type from table notes."""
        text_lower = text.lower()

        if "cluster" in text_lower:
            return SEType.CLUSTERED
        if "robust" in text_lower or "heteroskedastic" in text_lower:
            return SEType.ROBUST
        if "bootstrap" in text_lower:
            return SEType.BOOTSTRAPPED
        if "hac" in text_lower or "newey" in text_lower:
            return SEType.HAC

        return SEType.UNKNOWN

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def _table_to_text(self, raw: RawTable) -> str:
        """Convert table to searchable text for classification."""
        parts = [raw.caption, raw.notes]
        for row in raw.headers:
            parts.extend(row)
        for row in raw.body:
            parts.extend(c.raw for c in row)
        return " ".join(parts)
