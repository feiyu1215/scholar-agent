"""
Text-Table Cross-Validation Module.

Performs deep cross-referencing between paper text claims and table content.
Goes beyond Rule 8 in consistency_engine.py by implementing:

1. Precise Number Extraction from Text
   - Coefficient values mentioned in specific contexts
   - Sample sizes mentioned in methodology sections
   - R² / fit statistics quoted in results discussion

2. Contextual Matching
   - Resolves "Table N" references to actual tables
   - Handles "Column (M)" references
   - Understands "baseline specification" / "preferred specification"

3. Claim Verification
   - "The effect is large/small" vs actual magnitude
   - "Robust to controlling for X" vs coefficient stability
   - "Sample restricted to X reduces N by Y" vs actual N differences

4. Direction Verification
   - "Positive/negative effect" matches sign in table
   - "Increases/decreases" matches direction claim
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from .consistency_engine import ConsistencyViolation, RuleID, Severity
from .econ_table import EconTable, EconTableType

logger = logging.getLogger(__name__)


@dataclass
class TextClaim:
    """A quantitative or qualitative claim extracted from paper text."""
    claim_type: str  # "coefficient", "significance", "sample_size", "r_squared", "direction"
    value: Optional[float] = None
    text_fragment: str = ""
    table_ref: Optional[int] = None  # "Table N" reference
    column_ref: Optional[int] = None  # "Column M" reference
    variable_name: str = ""
    direction: str = ""  # "positive", "negative", "increases", "decreases"
    significance_level: Optional[int] = None  # 1, 5, or 10 (percent)
    context_sentence: str = ""


@dataclass
class CrossValidationResult:
    """Result of cross-validating a text claim against tables."""
    claim: TextClaim
    matched: bool = False
    matched_table_id: str = ""
    matched_value: Optional[float] = None
    discrepancy: str = ""
    severity: Severity = Severity.INFO


class TextTableCrossValidator:
    """
    Cross-validates quantitative claims in paper text against
    extracted table data.
    """

    def __init__(
        self,
        *,
        tolerance_pct: float = 5.0,
        tolerance_abs: float = 0.005,
    ):
        """
        Args:
            tolerance_pct: Percentage tolerance for numeric matching (rounding).
            tolerance_abs: Absolute tolerance for numeric matching.
        """
        self.tolerance_pct = tolerance_pct
        self.tolerance_abs = tolerance_abs

    def cross_validate(
        self,
        paper_text: str,
        econ_tables: list[EconTable],
    ) -> list[ConsistencyViolation]:
        """
        Extract claims from text and validate against tables.

        Args:
            paper_text: Full paper text.
            econ_tables: Parsed economics tables.

        Returns:
            List of violations found during cross-validation.
        """
        if not paper_text or not econ_tables:
            return []

        violations: list[ConsistencyViolation] = []

        # Step 1: Extract claims from text
        claims = self._extract_claims(paper_text)

        # Step 2: Build table lookup
        table_map = self._build_table_map(econ_tables)

        # Step 3: Validate each claim
        for claim in claims:
            result = self._validate_claim(claim, econ_tables, table_map)
            if result and result.discrepancy:
                violations.append(ConsistencyViolation(
                    rule_id=RuleID.TEXT_TABLE_CROSS_REF,
                    severity=result.severity,
                    table_id=result.matched_table_id or "text-xref",
                    description=result.discrepancy,
                    evidence=f"Text: \"{claim.text_fragment}\"",
                    location=(
                        f"Table {claim.table_ref}, Column {claim.column_ref}"
                        if claim.table_ref
                        else "Text reference"
                    ),
                    confidence=0.6,
                    suggestion=(
                        "Verify that the claim in the text matches "
                        "the corresponding table value."
                    ),
                ))

        return violations

    # ==================================================================
    # Claim Extraction
    # ==================================================================

    def _extract_claims(self, text: str) -> list[TextClaim]:
        """Extract quantitative and qualitative claims from paper text."""
        claims: list[TextClaim] = []

        # Split into sentences for context
        sentences = self._split_sentences(text)

        for sentence in sentences:
            # Extract coefficient claims
            claims.extend(self._extract_coefficient_claims(sentence))

            # Extract significance claims
            claims.extend(self._extract_significance_claims(sentence))

            # Extract sample size claims
            claims.extend(self._extract_sample_size_claims(sentence))

            # Extract R² claims
            claims.extend(self._extract_r_squared_claims(sentence))

            # Extract direction claims
            claims.extend(self._extract_direction_claims(sentence))

        return claims

    def _extract_coefficient_claims(self, sentence: str) -> list[TextClaim]:
        """Extract claims about specific coefficient values."""
        claims: list[TextClaim] = []

        # Patterns: "coefficient of 0.05", "estimate is -0.032",
        # "effect of 1.23 standard deviations"
        patterns = [
            # "coefficient of X.XX"
            r"(?:coefficient|estimate|effect(?:\s+size)?)\s+(?:of\s+|is\s+|=\s*|equals?\s+)"
            r"([-+]?\d+\.?\d*)",
            # "X.XX percentage points"
            r"([-+]?\d+\.?\d*)\s+(?:percentage\s+points?|pp\.?|percent)",
            # "X.XX standard deviations"
            r"([-+]?\d+\.?\d*)\s+standard\s+deviations?",
            # "β = X.XX" or "β̂ = X.XX"
            r"(?:β|beta|β̂)\s*[=≈]\s*([-+]?\d+\.?\d*)",
        ]

        for pattern in patterns:
            matches = re.finditer(pattern, sentence, re.IGNORECASE)
            for match in matches:
                try:
                    value = float(match.group(1))
                except (ValueError, IndexError):
                    continue

                # Extract table/column reference from same sentence
                table_ref = self._find_table_ref(sentence)
                col_ref = self._find_column_ref(sentence)

                claims.append(TextClaim(
                    claim_type="coefficient",
                    value=value,
                    text_fragment=match.group(0),
                    table_ref=table_ref,
                    column_ref=col_ref,
                    context_sentence=sentence[:200],
                ))

        return claims

    def _extract_significance_claims(self, sentence: str) -> list[TextClaim]:
        """Extract claims about statistical significance."""
        claims: list[TextClaim] = []

        # "significant at the 1/5/10% level"
        sig_match = re.search(
            r"(?:statistically\s+)?significant\s+at\s+(?:the\s+)?"
            r"(\d+)\s*(?:%|percent)\s*(?:level|significance)",
            sentence,
            re.IGNORECASE,
        )
        if sig_match:
            try:
                level = int(sig_match.group(1))
                if level in (1, 5, 10):
                    claims.append(TextClaim(
                        claim_type="significance",
                        significance_level=level,
                        text_fragment=sig_match.group(0),
                        table_ref=self._find_table_ref(sentence),
                        column_ref=self._find_column_ref(sentence),
                        variable_name=self._find_variable_ref(sentence),
                        context_sentence=sentence[:200],
                    ))
            except ValueError:
                pass

        # "not significant" / "insignificant" / "statistically insignificant"
        insig_match = re.search(
            r"(?:not\s+(?:statistically\s+)?significant|"
            r"(?:statistically\s+)?insignificant)",
            sentence,
            re.IGNORECASE,
        )
        if insig_match:
            claims.append(TextClaim(
                claim_type="significance",
                significance_level=None,  # means "not significant"
                text_fragment=insig_match.group(0),
                table_ref=self._find_table_ref(sentence),
                column_ref=self._find_column_ref(sentence),
                variable_name=self._find_variable_ref(sentence),
                context_sentence=sentence[:200],
            ))

        return claims

    def _extract_sample_size_claims(self, sentence: str) -> list[TextClaim]:
        """Extract claims about sample sizes."""
        claims: list[TextClaim] = []

        # "sample of N observations/firms/individuals"
        n_match = re.search(
            r"(?:sample\s+(?:of|includes?|contains?|consists?\s+of)\s+)"
            r"([\d,]+)\s*"
            r"(?:observations?|firms?|individuals?|households?|"
            r"students?|countries?|banks?|years?|obs\.?)",
            sentence,
            re.IGNORECASE,
        )
        if n_match:
            try:
                n_str = n_match.group(1).replace(",", "")
                n_value = int(n_str)
                claims.append(TextClaim(
                    claim_type="sample_size",
                    value=float(n_value),
                    text_fragment=n_match.group(0),
                    table_ref=self._find_table_ref(sentence),
                    context_sentence=sentence[:200],
                ))
            except ValueError:
                pass

        # "N = X,XXX"
        n_eq_match = re.search(
            r"[Nn]\s*=\s*([\d,]+)",
            sentence,
        )
        if n_eq_match:
            try:
                n_str = n_eq_match.group(1).replace(",", "")
                n_value = int(n_str)
                if n_value > 10:  # avoid matching "N=1" in equations
                    claims.append(TextClaim(
                        claim_type="sample_size",
                        value=float(n_value),
                        text_fragment=n_eq_match.group(0),
                        table_ref=self._find_table_ref(sentence),
                        context_sentence=sentence[:200],
                    ))
            except ValueError:
                pass

        return claims

    def _extract_r_squared_claims(self, sentence: str) -> list[TextClaim]:
        """Extract claims about R² / model fit."""
        claims: list[TextClaim] = []

        # "R² of 0.XX" / "R-squared = 0.XX"
        r2_match = re.search(
            r"(?:R-squared|R²|R\^2|R2|adjusted\s+R²?)\s*"
            r"(?:of\s+|is\s+|=\s*|equals?\s+)"
            r"(0?\.\d+|\d+\.?\d*)",
            sentence,
            re.IGNORECASE,
        )
        if r2_match:
            try:
                value = float(r2_match.group(1))
                claims.append(TextClaim(
                    claim_type="r_squared",
                    value=value,
                    text_fragment=r2_match.group(0),
                    table_ref=self._find_table_ref(sentence),
                    column_ref=self._find_column_ref(sentence),
                    context_sentence=sentence[:200],
                ))
            except ValueError:
                pass

        return claims

    def _extract_direction_claims(self, sentence: str) -> list[TextClaim]:
        """Extract claims about effect direction (positive/negative)."""
        claims: list[TextClaim] = []

        # "positive/negative effect/relationship/association"
        dir_match = re.search(
            r"(positive|negative)\s+"
            r"(?:and\s+(?:statistically\s+)?significant\s+)?"
            r"(?:effect|relationship|association|coefficient|impact|correlation)",
            sentence,
            re.IGNORECASE,
        )
        if dir_match:
            direction = dir_match.group(1).lower()
            var_name = self._find_variable_ref(sentence)
            if var_name:  # Only if we can identify which variable
                claims.append(TextClaim(
                    claim_type="direction",
                    direction=direction,
                    text_fragment=dir_match.group(0),
                    table_ref=self._find_table_ref(sentence),
                    column_ref=self._find_column_ref(sentence),
                    variable_name=var_name,
                    context_sentence=sentence[:200],
                ))

        # "X increases/decreases Y"
        verb_match = re.search(
            r"(\w+)\s+(increase[sd]?|decrease[sd]?|raise[sd]?|"
            r"reduce[sd]?|lower[sd]?)\s+",
            sentence,
            re.IGNORECASE,
        )
        if verb_match:
            verb = verb_match.group(2).lower()
            direction = (
                "positive"
                if any(v in verb for v in ("increase", "raise"))
                else "negative"
            )
            claims.append(TextClaim(
                claim_type="direction",
                direction=direction,
                text_fragment=verb_match.group(0),
                table_ref=self._find_table_ref(sentence),
                variable_name=verb_match.group(1),
                context_sentence=sentence[:200],
            ))

        return claims

    # ==================================================================
    # Claim Validation
    # ==================================================================

    def _validate_claim(
        self,
        claim: TextClaim,
        tables: list[EconTable],
        table_map: dict[int, EconTable],
    ) -> Optional[CrossValidationResult]:
        """Validate a single claim against table data."""

        if claim.claim_type == "coefficient":
            return self._validate_coefficient_claim(claim, tables, table_map)
        elif claim.claim_type == "significance":
            return self._validate_significance_claim(claim, tables, table_map)
        elif claim.claim_type == "sample_size":
            return self._validate_sample_size_claim(claim, tables, table_map)
        elif claim.claim_type == "r_squared":
            return self._validate_r_squared_claim(claim, tables, table_map)
        elif claim.claim_type == "direction":
            return self._validate_direction_claim(claim, tables, table_map)

        return None

    def _validate_coefficient_claim(
        self,
        claim: TextClaim,
        tables: list[EconTable],
        table_map: dict[int, EconTable],
    ) -> Optional[CrossValidationResult]:
        """Check if a coefficient value claim matches table data."""
        if claim.value is None:
            return None

        target_tables = self._resolve_tables(claim, tables, table_map)
        if not target_tables:
            return None

        for table in target_tables:
            for col in table.regression_columns:
                # If column reference specified, filter
                if claim.column_ref and col.column_index != claim.column_ref - 1:
                    continue

                for entry in col.coefficients:
                    if entry.coefficient is None:
                        continue

                    if self._values_match(claim.value, entry.coefficient):
                        return CrossValidationResult(
                            claim=claim,
                            matched=True,
                            matched_table_id=table.table_id,
                            matched_value=entry.coefficient,
                        )

        # No match found
        return CrossValidationResult(
            claim=claim,
            matched=False,
            discrepancy=(
                f"Text claims coefficient = {claim.value}, "
                f"but no matching value found in "
                f"{'Table ' + str(claim.table_ref) if claim.table_ref else 'any table'}"
            ),
            severity=Severity.INFO,
        )

    def _validate_significance_claim(
        self,
        claim: TextClaim,
        tables: list[EconTable],
        table_map: dict[int, EconTable],
    ) -> Optional[CrossValidationResult]:
        """Check if significance claims match table stars."""
        target_tables = self._resolve_tables(claim, tables, table_map)
        if not target_tables:
            return None

        expected_stars = {1: 3, 5: 2, 10: 1}.get(
            claim.significance_level or 0, 0
        )

        for table in target_tables:
            for col in table.regression_columns:
                if claim.column_ref and col.column_index != claim.column_ref - 1:
                    continue

                for entry in col.coefficients:
                    # Try to match by variable name if specified
                    if claim.variable_name:
                        if not self._variable_name_matches(
                            claim.variable_name, entry.variable_name
                        ):
                            continue

                    # Check significance claim
                    if claim.significance_level is None:
                        # Claim: "not significant"
                        if entry.stars > 0:
                            return CrossValidationResult(
                                claim=claim,
                                matched=False,
                                matched_table_id=table.table_id,
                                discrepancy=(
                                    f"Text claims '{entry.variable_name}' is "
                                    f"insignificant, but table shows "
                                    f"{entry.stars} star(s)"
                                ),
                                severity=Severity.WARNING,
                            )
                    else:
                        # Claim: "significant at N%"
                        if entry.stars < expected_stars:
                            return CrossValidationResult(
                                claim=claim,
                                matched=False,
                                matched_table_id=table.table_id,
                                discrepancy=(
                                    f"Text claims significance at "
                                    f"{claim.significance_level}% "
                                    f"(needs ≥{expected_stars} stars), "
                                    f"but table shows {entry.stars} star(s) "
                                    f"for '{entry.variable_name}'"
                                ),
                                severity=Severity.WARNING,
                            )

        return None  # No clear mismatch found

    def _validate_sample_size_claim(
        self,
        claim: TextClaim,
        tables: list[EconTable],
        table_map: dict[int, EconTable],
    ) -> Optional[CrossValidationResult]:
        """Check if sample size claims match table N values."""
        if claim.value is None:
            return None

        claimed_n = int(claim.value)
        target_tables = self._resolve_tables(claim, tables, table_map)
        if not target_tables:
            return None

        for table in target_tables:
            for col in table.regression_columns:
                if col.n_observations is not None:
                    # Allow 1% tolerance for rounding
                    if abs(col.n_observations - claimed_n) <= max(
                        claimed_n * 0.01, 5
                    ):
                        return CrossValidationResult(
                            claim=claim,
                            matched=True,
                            matched_table_id=table.table_id,
                            matched_value=float(col.n_observations),
                        )

        # Check if claimed N is far from any table N
        all_n_values = [
            col.n_observations
            for table in target_tables
            for col in table.regression_columns
            if col.n_observations is not None
        ]

        if all_n_values:
            closest_n = min(all_n_values, key=lambda n: abs(n - claimed_n))
            if abs(closest_n - claimed_n) > claimed_n * 0.1:
                return CrossValidationResult(
                    claim=claim,
                    matched=False,
                    matched_table_id=target_tables[0].table_id,
                    matched_value=float(closest_n),
                    discrepancy=(
                        f"Text claims N={claimed_n:,}, "
                        f"closest table value is N={closest_n:,} "
                        f"(difference: {abs(closest_n - claimed_n):,})"
                    ),
                    severity=Severity.WARNING,
                )

        return None

    def _validate_r_squared_claim(
        self,
        claim: TextClaim,
        tables: list[EconTable],
        table_map: dict[int, EconTable],
    ) -> Optional[CrossValidationResult]:
        """Check if R² claims match table values."""
        if claim.value is None:
            return None

        target_tables = self._resolve_tables(claim, tables, table_map)
        if not target_tables:
            return None

        for table in target_tables:
            for col in table.regression_columns:
                if claim.column_ref and col.column_index != claim.column_ref - 1:
                    continue

                for r2_val in (col.r_squared, col.adjusted_r_squared):
                    if r2_val is not None:
                        if self._values_match(claim.value, r2_val):
                            return CrossValidationResult(
                                claim=claim,
                                matched=True,
                                matched_table_id=table.table_id,
                                matched_value=r2_val,
                            )

        return None

    def _validate_direction_claim(
        self,
        claim: TextClaim,
        tables: list[EconTable],
        table_map: dict[int, EconTable],
    ) -> Optional[CrossValidationResult]:
        """Check if direction claims (positive/negative) match table signs."""
        if not claim.variable_name or not claim.direction:
            return None

        target_tables = self._resolve_tables(claim, tables, table_map)
        if not target_tables:
            return None

        for table in target_tables:
            for col in table.regression_columns:
                if claim.column_ref and col.column_index != claim.column_ref - 1:
                    continue

                for entry in col.coefficients:
                    if not self._variable_name_matches(
                        claim.variable_name, entry.variable_name
                    ):
                        continue

                    if entry.coefficient is None or entry.coefficient == 0:
                        continue

                    actual_direction = (
                        "positive" if entry.coefficient > 0 else "negative"
                    )

                    if claim.direction != actual_direction:
                        return CrossValidationResult(
                            claim=claim,
                            matched=False,
                            matched_table_id=table.table_id,
                            matched_value=entry.coefficient,
                            discrepancy=(
                                f"Text claims '{claim.direction}' effect "
                                f"for '{claim.variable_name}', but table "
                                f"shows coefficient = {entry.coefficient:.4g} "
                                f"({actual_direction})"
                            ),
                            severity=Severity.WARNING,
                        )

        return None

    # ==================================================================
    # Utility Methods
    # ==================================================================

    def _build_table_map(
        self, tables: list[EconTable]
    ) -> dict[int, EconTable]:
        """
        Build a mapping from table number to EconTable.

        Attempts to extract table numbers from table_id or caption.
        """
        table_map: dict[int, EconTable] = {}

        for i, table in enumerate(tables):
            # Try to extract number from caption
            num_match = re.search(r"Table\s+(\d+)", table.caption, re.IGNORECASE)
            if num_match:
                table_map[int(num_match.group(1))] = table
            else:
                # Fallback: sequential numbering
                table_map[i + 1] = table

        return table_map

    def _resolve_tables(
        self,
        claim: TextClaim,
        all_tables: list[EconTable],
        table_map: dict[int, EconTable],
    ) -> list[EconTable]:
        """Resolve which tables a claim refers to."""
        if claim.table_ref and claim.table_ref in table_map:
            return [table_map[claim.table_ref]]
        # If no specific reference, search all tables
        return all_tables

    def _values_match(self, expected: float, actual: float) -> bool:
        """Check if two values match within tolerance."""
        if expected == 0 and actual == 0:
            return True
        abs_diff = abs(expected - actual)
        if abs_diff <= self.tolerance_abs:
            return True
        if abs(expected) > 0:
            pct_diff = abs_diff / abs(expected) * 100
            if pct_diff <= self.tolerance_pct:
                return True
        return False

    def _variable_name_matches(self, text_ref: str, table_var: str) -> bool:
        """
        Fuzzy match between a variable name referenced in text
        and a variable name in the table.
        """
        text_lower = text_ref.strip().lower()
        table_lower = table_var.strip().lower()

        # Exact match
        if text_lower == table_lower:
            return True

        # Substring match
        if text_lower in table_lower or table_lower in text_lower:
            return True

        # Common abbreviation handling
        abbrevs = {
            "gdp": "gross domestic product",
            "fdi": "foreign direct investment",
            "ln": "log",
            "log": "ln",
        }
        for abbr, full in abbrevs.items():
            if abbr in text_lower and full in table_lower:
                return True
            if full in text_lower and abbr in table_lower:
                return True

        return False

    def _find_table_ref(self, text: str) -> Optional[int]:
        """Find "Table N" reference in text."""
        match = re.search(r"Table\s+(\d+)", text, re.IGNORECASE)
        if match:
            return int(match.group(1))
        return None

    def _find_column_ref(self, text: str) -> Optional[int]:
        """Find "Column (N)" or "column N" reference in text."""
        match = re.search(
            r"[Cc]olumn\s*\(?(\d+)\)?",
            text,
        )
        if match:
            return int(match.group(1))
        return None

    def _find_variable_ref(self, sentence: str) -> str:
        """
        Attempt to identify which variable is being discussed.

        Heuristic: look for italicized terms, quoted terms, or
        recognizable variable patterns.
        """
        # Quoted or emphasized variable: "treatment", *treatment*
        quoted = re.search(r'[""\']([\w\s]+)[""\'"]', sentence)
        if quoted:
            return quoted.group(1).strip()

        # Common patterns: "the effect of X on Y" → X is the variable
        effect_of = re.search(
            r"(?:effect|impact|influence)\s+of\s+([\w\s]+?)\s+(?:on|is)",
            sentence,
            re.IGNORECASE,
        )
        if effect_of:
            return effect_of.group(1).strip()

        return ""

    def _split_sentences(self, text: str) -> list[str]:
        """Split text into sentences."""
        # Simple sentence splitter (handles common cases)
        # Avoid splitting on abbreviations like "e.g.", "i.e.", "et al."
        text = re.sub(r"(e\.g|i\.e|et al|vs|etc|Fig|Tab)\.", r"\1<DOT>", text)
        sentences = re.split(r"[.!?]\s+", text)
        sentences = [s.replace("<DOT>", ".").strip() for s in sentences if s.strip()]
        return sentences
