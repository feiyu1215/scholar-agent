"""
Phase 9: Multimodal paper understanding.

Phase 9A: Table processing and numerical validation.
Phase 9B: Figure semantic understanding.
Phase 9C: Cross-modal validation (future).

Module Structure (9A):
  - table_parser.py: Multi-strategy text table extraction (LaTeX/Markdown/space-aligned)
  - econ_table.py: Economics table semantic parser (regression/descriptive/balance)
  - pdf_table_extractor.py: PDF table extraction (pdfplumber/pymupdf)
  - consistency_engine.py: 9-rule numerical consistency validation (incl. cross-table comparison)
  - text_table_xref.py: Text-table cross-reference validation
  - skills.py: SkillX integration (TableExtractionSkill, TableConsistencySkill)

Module Structure (9B):
  - figure_extractor.py: Figure extraction and type classification
  - econ_figure.py: Economics-specific figure analysis rules
  - figure_text_xref.py: Figure-text cross-reference validation
  - figure_skills.py: SkillX integration (FigureSemanticSkill, FigureConsistencySkill)
"""

# Phase 9A: Table processing
from .skills import TableConsistencySkill, TableExtractionSkill
from .table_parser import CellValue, RawTable, TextTableParser
from .econ_table import EconTable, EconTableParser, EconTableType
from .consistency_engine import (
    ConsistencyValidator,
    ConsistencyViolation,
    Severity,
    ValidationReport,
)
from .text_table_xref import TextTableCrossValidator

# Phase 9B: Figure semantic understanding
from .figure_skills import FigureConsistencySkill, FigureSemanticSkill
from .figure_extractor import (
    FigureExtractor,
    FigureReference,
    FigureClassification,
    FigureType,
    ExtractedValue,
)
from .econ_figure import EconFigureAnalyzer
from .figure_text_xref import (
    FigureTextCrossValidator,
    FigureTextClaim,
    CrossModalInconsistency,
    CoverageReport,
)

__all__ = [
    # Phase 9A Skills (SkillX interface)
    "TableExtractionSkill",
    "TableConsistencySkill",
    # Phase 9A Core components
    "TextTableParser",
    "EconTableParser",
    "ConsistencyValidator",
    "TextTableCrossValidator",
    # Phase 9A Data types
    "RawTable",
    "CellValue",
    "EconTable",
    "EconTableType",
    "ConsistencyViolation",
    "ValidationReport",
    "Severity",
    # Phase 9B Skills (SkillX interface)
    "FigureSemanticSkill",
    "FigureConsistencySkill",
    # Phase 9B Core components
    "FigureExtractor",
    "EconFigureAnalyzer",
    "FigureTextCrossValidator",
    # Phase 9B Data types
    "FigureReference",
    "FigureClassification",
    "FigureType",
    "ExtractedValue",
    "FigureTextClaim",
    "CrossModalInconsistency",
    "CoverageReport",
]
