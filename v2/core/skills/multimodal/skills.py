"""
core/skills/multimodal/skills.py — Phase 9A SkillX Integration

Two Functional Skills for table processing and numerical validation:

1. TableExtractionSkill
   - Extracts tables from paper_text (text-formatted)
   - Optionally extracts from PDF (if paper_path available)
   - Applies economics semantic parsing (RawTable → EconTable)
   - Outputs structured table data for downstream consumption

2. TableConsistencySkill
   - Validates internal consistency of economics tables (8 rules)
   - Cross-validates text claims against table values
   - Produces findings for the review report

Both skills respect the Kill Switch pattern (SCHOLAR_GODEL_TABLE_PROCESSING).
"""

from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path
from typing import Optional

from core.skills.base import (
    Finding,
    Skill,
    SkillContext,
    SkillDescriptor,
    SkillLevel,
    SkillResult,
)

from .consistency_engine import (
    ConsistencyValidator,
    ConsistencyViolation,
    Severity,
    ValidationReport,
)
from .econ_table import EconTable, EconTableParser, EconTableType
from .pdf_table_extractor import PDFTableExtractor
from .table_parser import RawTable, TextTableParser
from .text_table_xref import TextTableCrossValidator

logger = logging.getLogger(__name__)


def _is_enabled() -> bool:
    """Check Kill Switch for table processing module.

    Uses the same logic as godel_config._env_flag: accepts '1', 'true', 'yes'.
    """
    val = os.environ.get("SCHOLAR_GODEL_TABLE_PROCESSING", "1").strip().lower()
    return val in ("1", "true", "yes")


# ==============================================================
# 1. TableExtractionSkill
# ==============================================================


class TableExtractionSkill(Skill):
    """从论文中提取并解析表格的 Functional Skill.

    三源提取策略：
      1. 从 paper_text 中提取文本格式表格（LaTeX / Markdown / 空格对齐）
      2. 从 PDF 文件中提取 PDF 格式表格（如有 paper_path）
      3. 对所有提取的 RawTable 进行经济学语义解析

    输出 output_data:
      - "raw_tables": list[dict] — 原始表格数据
      - "econ_tables": list[dict] — 经济学语义解析结果
      - "extraction_stats": dict — 提取统计信息
    """

    _DESCRIPTOR = SkillDescriptor(
        name="table_extraction",
        level=SkillLevel.FUNCTIONAL,
        description="从论文文本和PDF中提取表格，解析经济学表格语义结构（回归表、描述统计等）",
        prerequisites=(),
        input_schema={
            "paper_text": "str (required)",
            "paper_metadata.paper_path": "str (optional, PDF path)",
        },
        output_schema={
            "raw_tables": "list[dict] — extracted raw tables",
            "econ_tables": "list[dict] — economics-parsed tables",
            "extraction_stats": "dict — summary statistics",
        },
        applicable_phases=("deep_review", "statistics_validation", "table_processing"),
        tags=("multimodal", "table", "extraction", "economics"),
        token_cost_estimate=500,
        version="1.0",
    )

    def __init__(self) -> None:
        self._text_parser = TextTableParser()
        self._pdf_extractor = PDFTableExtractor()
        self._econ_parser = EconTableParser()

    @property
    def descriptor(self) -> SkillDescriptor:
        return self._DESCRIPTOR

    def can_apply(self, context: SkillContext) -> float:
        """Evaluate applicability based on presence of tables."""
        if not _is_enabled():
            return 0.0

        if not context.paper_text:
            return 0.0

        text_lower = context.paper_text.lower()
        score = 0.0

        # Strong signals: LaTeX table environments
        if r"\begin{tabular" in text_lower or r"\begin{table" in text_lower:
            score += 0.6

        # Table references in text
        table_refs = len(re.findall(r"table\s+\d", text_lower))
        if table_refs >= 2:
            score += 0.3
        elif table_refs >= 1:
            score += 0.15

        # Pipe-separated tables
        pipe_lines = sum(1 for line in context.paper_text.split("\n") if "|" in line and line.count("|") >= 3)
        if pipe_lines >= 3:
            score += 0.3

        # PDF availability
        paper_path = context.paper_metadata.get("paper_path", "")
        if paper_path and paper_path.endswith(".pdf"):
            score += 0.1

        # Phase relevance
        if context.current_phase in ("deep_review", "statistics_validation"):
            score += 0.1

        return min(score, 1.0)

    def execute(self, context: SkillContext) -> SkillResult:
        """Extract and parse tables from the paper."""
        start_time = time.time()

        if not _is_enabled():
            return SkillResult(
                success=False,
                error_message="Table processing disabled by Kill Switch "
                "(SCHOLAR_GODEL_TABLE_PROCESSING=0)",
            )

        raw_tables: list[RawTable] = []
        findings: list[Finding] = []

        # Source 1: Text table extraction
        text_tables: list[RawTable] = []
        try:
            text_tables = self._text_parser.extract_all(context.paper_text)
            raw_tables.extend(text_tables)
            logger.info("Extracted %d tables from text", len(text_tables))
        except Exception as e:
            logger.warning("Text table extraction failed: %s", e)
            findings.append(Finding(
                category="methodology",
                severity="minor",
                description=f"Text table extraction encountered an error: {e}",
                skill_source="table_extraction",
                confidence=0.3,
            ))

        # Source 2: PDF table extraction (optional)
        paper_path = context.paper_metadata.get("paper_path", "")
        pdf_tables_count = 0
        if paper_path and Path(paper_path).exists() and paper_path.endswith(".pdf"):
            try:
                pdf_tables = self._pdf_extractor.extract(paper_path)
                # Deduplicate: skip PDF tables that look like already-extracted text tables
                new_pdf_tables = self._deduplicate_across_sources(
                    text_tables, pdf_tables
                )
                raw_tables.extend(new_pdf_tables)
                pdf_tables_count = len(new_pdf_tables)
                logger.info(
                    "Extracted %d additional tables from PDF (after dedup)",
                    pdf_tables_count,
                )
            except Exception as e:
                logger.warning("PDF table extraction failed: %s", e)

        # Source 3: Economics semantic parsing
        econ_tables: list[EconTable] = []
        if raw_tables:
            try:
                econ_tables = self._econ_parser.parse_all(raw_tables)
                logger.info(
                    "Parsed %d economics tables: %s",
                    len(econ_tables),
                    self._summarize_table_types(econ_tables),
                )
            except Exception as e:
                logger.warning("Economics table parsing failed: %s", e)

        # Generate findings about table quality
        findings.extend(self._generate_extraction_findings(raw_tables, econ_tables))

        # Build output data
        execution_time = (time.time() - start_time) * 1000

        output_data = {
            "raw_tables": [self._raw_table_to_dict(t) for t in raw_tables],
            "econ_tables": [self._econ_table_to_dict(t) for t in econ_tables],
            "extraction_stats": {
                "total_tables": len(raw_tables),
                "from_text": len(raw_tables) - pdf_tables_count,
                "from_pdf": pdf_tables_count,
                "regression_tables": sum(
                    1 for t in econ_tables
                    if t.table_type == EconTableType.REGRESSION
                ),
                "descriptive_tables": sum(
                    1 for t in econ_tables
                    if t.table_type == EconTableType.DESCRIPTIVE_STATS
                ),
            },
        }

        return SkillResult(
            findings=findings,
            output_data=output_data,
            success=True,
            execution_time_ms=execution_time,
            metadata={"tables_found": len(raw_tables)},
        )

    def _deduplicate_across_sources(
        self,
        text_tables: list[RawTable],
        pdf_tables: list[RawTable],
    ) -> list[RawTable]:
        """Remove PDF tables that duplicate text-extracted tables."""
        if not text_tables:
            return pdf_tables

        # Build fingerprints from text tables
        text_fingerprints = set()
        for t in text_tables:
            fp = self._table_fingerprint(t)
            if fp:
                text_fingerprints.add(fp)

        unique_pdf = []
        for t in pdf_tables:
            fp = self._table_fingerprint(t)
            if fp and fp not in text_fingerprints:
                unique_pdf.append(t)
            elif not fp:
                unique_pdf.append(t)  # Can't fingerprint, keep it

        return unique_pdf

    def _table_fingerprint(self, table: RawTable) -> str:
        """Create a rough fingerprint from first few body cells."""
        if not table.body or len(table.body) < 2:
            return ""
        # Use first 3 rows, first 3 cells each
        parts = []
        for row in table.body[:3]:
            for cell in row[:3]:
                if cell.numeric is not None:
                    parts.append(f"{cell.numeric:.4g}")
                elif cell.raw.strip():
                    parts.append(cell.raw.strip()[:20])
        return "|".join(parts)

    def _generate_extraction_findings(
        self,
        raw_tables: list[RawTable],
        econ_tables: list[EconTable],
    ) -> list[Finding]:
        """Generate findings about the extraction process itself."""
        findings: list[Finding] = []

        if not raw_tables:
            findings.append(Finding(
                category="methodology",
                severity="suggestion",
                description=(
                    "No tables could be extracted from the paper. "
                    "This may indicate the paper is text-only or "
                    "tables are in an unsupported format."
                ),
                skill_source="table_extraction",
                confidence=0.5,
            ))

        # Tables that couldn't be classified
        unknown_count = sum(
            1 for t in econ_tables
            if t.table_type == EconTableType.UNKNOWN
        )
        if unknown_count > 0 and len(econ_tables) > 0:
            findings.append(Finding(
                category="clarity",
                severity="suggestion",
                description=(
                    f"{unknown_count} of {len(econ_tables)} tables could not "
                    f"be classified (missing headers or unusual structure)"
                ),
                skill_source="table_extraction",
                confidence=0.4,
            ))

        return findings

    def _summarize_table_types(self, tables: list[EconTable]) -> str:
        """Summarize table types for logging."""
        type_counts: dict[str, int] = {}
        for t in tables:
            key = t.table_type.value
            type_counts[key] = type_counts.get(key, 0) + 1
        return ", ".join(f"{k}={v}" for k, v in type_counts.items())

    def _raw_table_to_dict(self, table: RawTable) -> dict:
        """Serialize RawTable to dict for output_data."""
        return {
            "table_id": table.table_id,
            "caption": table.caption,
            "n_cols": table.n_cols,
            "n_rows": len(table.body),
            "source_format": table.source_format,
            "headers": table.headers,
        }

    def _econ_table_to_dict(self, table: EconTable) -> dict:
        """Serialize EconTable to dict for output_data."""
        result: dict = {
            "table_id": table.table_id,
            "table_type": table.table_type.value,
            "caption": table.caption,
            "n_columns": table.n_columns,
            "star_convention": table.star_convention.value,
            "se_type": table.se_type.value,
        }

        if table.regression_columns:
            result["regression_columns"] = [
                {
                    "column_index": col.column_index,
                    "column_header": col.column_header,
                    "n_observations": col.n_observations,
                    "r_squared": col.r_squared,
                    "n_coefficients": len(col.coefficients),
                    "fixed_effects": col.fixed_effects,
                }
                for col in table.regression_columns
            ]

        if table.descriptive_columns:
            result["descriptive_columns"] = [
                {
                    "variable_name": desc.variable_name,
                    "mean": desc.mean,
                    "std_dev": desc.std_dev,
                    "n_observations": desc.n_observations,
                }
                for desc in table.descriptive_columns
            ]

        return result


# ==============================================================
# 2. TableConsistencySkill
# ==============================================================


class TableConsistencySkill(Skill):
    """验证表格数值一致性的 Functional Skill.

    执行流程：
      1. 接收 TableExtractionSkill 的 output_data（或自行提取）
      2. 对所有 EconTable 运行 8 条验证规则
      3. 对论文文本和表格进行交叉验证
      4. 产出结构化 Finding 列表

    依赖：
      - 推荐先运行 table_extraction skill，复用其 econ_tables 输出
      - 也可独立运行（会自行提取表格）
    """

    _DESCRIPTOR = SkillDescriptor(
        name="table_consistency",
        level=SkillLevel.FUNCTIONAL,
        description="验证经济学表格的数值一致性：系数-SE匹配、R²边界、样本量递减、显著性星号、文本交叉验证",
        prerequisites=("table_extraction",),
        input_schema={
            "paper_text": "str (required)",
            "parameters.econ_tables": "list[EconTable] (optional, from upstream)",
        },
        output_schema={
            "findings": "list[Finding]",
            "validation_report": "dict",
        },
        applicable_phases=("deep_review", "statistics_validation", "table_processing"),
        tags=("multimodal", "table", "consistency", "statistics", "validation"),
        token_cost_estimate=600,
        version="1.0",
    )

    def __init__(self) -> None:
        self._validator = ConsistencyValidator()
        self._cross_validator = TextTableCrossValidator()
        self._text_parser = TextTableParser()
        self._econ_parser = EconTableParser()

    @property
    def descriptor(self) -> SkillDescriptor:
        return self._DESCRIPTOR

    def can_apply(self, context: SkillContext) -> float:
        """Evaluate applicability — high if upstream provides econ_tables."""
        if not _is_enabled():
            return 0.0

        score = 0.0

        # Strong signal: upstream already extracted tables
        upstream_tables = context.parameters.get("econ_tables")
        if upstream_tables:
            score += 0.7

        if not context.paper_text:
            return min(score, 1.0)

        # Table references in text
        text_lower = context.paper_text.lower()
        table_refs = len(re.findall(r"table\s+\d", text_lower))
        if table_refs >= 3:
            score += 0.3
        elif table_refs >= 1:
            score += 0.15

        # Phase relevance
        if context.current_phase in ("deep_review", "statistics_validation"):
            score += 0.15

        # Regression keywords (suggests tables worth validating)
        if any(kw in text_lower for kw in (
            "coefficient", "standard error", "significant",
            "regression", "r-squared",
        )):
            score += 0.1

        return min(score, 1.0)

    def execute(self, context: SkillContext) -> SkillResult:
        """Run consistency validation on economics tables."""
        start_time = time.time()

        if not _is_enabled():
            return SkillResult(
                success=False,
                error_message="Table processing disabled by Kill Switch "
                "(SCHOLAR_GODEL_TABLE_PROCESSING=0)",
            )

        # Get or extract EconTable objects
        econ_tables = self._get_econ_tables(context)

        if not econ_tables:
            return SkillResult(
                findings=[],
                output_data={"validation_report": {"message": "No tables to validate"}},
                success=True,
                execution_time_ms=(time.time() - start_time) * 1000,
            )

        findings: list[Finding] = []

        # Phase 1: Internal consistency validation
        try:
            report = self._validator.validate(econ_tables, context.paper_text)
            findings.extend(self._violations_to_findings(report.violations))
            logger.info(
                "Consistency validation: %d violations (%d errors, %d warnings)",
                len(report.violations),
                report.error_count,
                report.warning_count,
            )
        except Exception as e:
            logger.warning("Consistency validation failed: %s", e)
            findings.append(Finding(
                category="statistics",
                severity="minor",
                description=f"Table consistency validation encountered error: {e}",
                skill_source="table_consistency",
                confidence=0.3,
            ))
            report = ValidationReport()

        # Phase 2: Text-table cross-validation
        try:
            xref_violations = self._cross_validator.cross_validate(
                context.paper_text, econ_tables
            )
            findings.extend(self._violations_to_findings(xref_violations))
            logger.info(
                "Cross-validation: %d issues found",
                len(xref_violations),
            )
        except Exception as e:
            logger.warning("Cross-validation failed: %s", e)

        execution_time = (time.time() - start_time) * 1000

        return SkillResult(
            findings=findings,
            output_data={
                "validation_report": {
                    "tables_checked": report.tables_checked,
                    "rules_applied": report.rules_applied,
                    "errors": report.error_count,
                    "warnings": report.warning_count,
                    "info": report.info_count,
                    "summary": report.summary(),
                },
            },
            success=True,
            execution_time_ms=execution_time,
            metadata={
                "violations_found": len(report.violations),
                "has_critical": report.has_critical_issues,
            },
        )

    def _get_econ_tables(self, context: SkillContext) -> list[EconTable]:
        """Get EconTable objects from upstream or extract fresh."""
        # Try to get from upstream (table_extraction skill output)
        upstream = context.parameters.get("econ_tables")
        if upstream and isinstance(upstream, list) and len(upstream) > 0:
            # Check if these are already EconTable objects
            if isinstance(upstream[0], EconTable):
                return upstream
            # Otherwise, they might be dicts from serialization — skip

        # Self-extract from paper_text
        try:
            raw_tables = self._text_parser.extract_all(context.paper_text)
            if raw_tables:
                return self._econ_parser.parse_all(raw_tables)
        except Exception as e:
            logger.warning("Self-extraction failed: %s", e)

        return []

    def _violations_to_findings(
        self, violations: list[ConsistencyViolation]
    ) -> list[Finding]:
        """Convert ConsistencyViolation objects to Finding objects."""
        findings: list[Finding] = []

        for v in violations:
            severity_map = {
                Severity.ERROR: "critical",
                Severity.WARNING: "major",
                Severity.INFO: "minor",
            }

            finding = Finding(
                category="statistics",
                severity=severity_map.get(v.severity, "minor"),
                description=v.description,
                evidence=v.evidence,
                suggestion=v.suggestion,
                location=v.location or f"Table: {v.table_id}",
                confidence=v.confidence,
                skill_source="table_consistency",
            )
            findings.append(finding)

        return findings


# ==============================================================
# Re-export for __init__.py
# ==============================================================

__all__ = [
    "TableExtractionSkill",
    "TableConsistencySkill",
]
