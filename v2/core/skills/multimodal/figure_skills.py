"""
Phase 9B SkillX Integration — Two Functional Skills for Figure Analysis.

1. FigureSemanticSkill
   - Extracts figure references from paper text
   - Classifies figure types (event study, DID, RD, coefficient plot, etc.)
   - Applies economics-specific analysis rules per figure type
   - Outputs structured figure data for downstream consumption

2. FigureConsistencySkill
   - Cross-validates figures against text claims
   - Checks magnitude/significance/trend consistency
   - Reports coverage issues (orphan figures, phantom references)
   - Produces findings for the review report

Both skills respect the Kill Switch: SCHOLAR_GODEL_FIGURE_SEMANTIC
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Optional

from core.skills.base import (
    Finding,
    Skill,
    SkillContext,
    SkillDescriptor,
    SkillLevel,
    SkillResult,
)

from .econ_figure import EconFigureAnalyzer
from .figure_extractor import (
    FigureClassification,
    FigureExtractor,
    FigureReference,
    FigureType,
)
from .figure_text_xref import (
    CoverageReport,
    FigureTextCrossValidator,
)

logger = logging.getLogger(__name__)


# ==============================================================
# Kill Switch
# ==============================================================


def _is_enabled() -> bool:
    """Check Kill Switch for figure semantic module.

    Uses the same logic as godel_config._env_flag: accepts '1', 'true', 'yes'.
    Default is enabled (1).
    """
    val = os.environ.get("SCHOLAR_GODEL_FIGURE_SEMANTIC", "1").strip().lower()
    return val in ("1", "true", "yes")


# ==============================================================
# 1. FigureSemanticSkill
# ==============================================================


class FigureSemanticSkill(Skill):
    """从论文中提取图表引用、分类类型、执行经济学专业分析的 Functional Skill.

    执行流程：
      1. 从 paper_text 中提取所有图表引用（captions + text mentions）
      2. 对每个图表进行类型分类（event study / DID / RD / coefficient plot 等）
      3. 对经济学图表执行专业规则分析
      4. 输出结构化图表数据供下游 Skill 使用

    输出 output_data:
      - "figures": list[dict] — 提取的图表引用
      - "classifications": list[dict] — 图表类型分类结果
      - "analysis_results": dict — 经济学专业分析结果
      - "extraction_stats": dict — 提取统计信息
    """

    _DESCRIPTOR = SkillDescriptor(
        name="figure_semantic_analysis",
        level=SkillLevel.FUNCTIONAL,
        description=(
            "图表语义理解：提取论文图表引用，分类图表类型"
            "（event study/DID/RD/coefficient plot），"
            "对经济学图表执行专业分析"
        ),
        prerequisites=(),
        input_schema={"paper_text": "str (required)"},
        output_schema={
            "figures": "list[dict] — extracted figure references",
            "classifications": "list[dict] — figure type classifications",
            "analysis_results": "dict — economics-specific analysis",
            "extraction_stats": "dict — extraction statistics",
        },
        applicable_phases=("deep_review", "methodology_check"),
        tags=("multimodal", "figure", "semantic", "economics"),
        token_cost_estimate=800,
        version="1.0",
    )

    def __init__(self) -> None:
        self._extractor = FigureExtractor()
        self._analyzer = EconFigureAnalyzer()

    @property
    def descriptor(self) -> SkillDescriptor:
        return self._DESCRIPTOR

    def can_apply(self, context: SkillContext) -> float:
        """Evaluate applicability based on presence of figure references.

        Signals:
          - Figure/Fig references in text (strong signal)
          - Paper claims about visual evidence (moderate signal)
          - Phase relevance (mild boost)
          - Kill switch check (hard gate)
        """
        if not _is_enabled():
            return 0.0

        if not context.paper_text:
            return 0.0

        text_lower = context.paper_text.lower()
        score = 0.0

        # Strong signal: explicit figure references
        fig_refs = len(re.findall(r"(?:figure|fig\.?)\s+\d", text_lower))
        if fig_refs >= 5:
            score += 0.6
        elif fig_refs >= 3:
            score += 0.45
        elif fig_refs >= 1:
            score += 0.3

        # Chinese figure references
        zh_fig_refs = len(re.findall(r"图\s*\d", context.paper_text))
        if zh_fig_refs >= 2:
            score += 0.2

        # Figure captions present (stronger signal)
        has_captions = bool(re.search(
            r"(?:^|\n)\s*(?:Figure|Fig\.?)\s+\d+\s*[:.—]",
            context.paper_text,
            re.IGNORECASE | re.MULTILINE,
        ))
        if has_captions:
            score += 0.2

        # Visual evidence language
        visual_keywords = sum(1 for kw in (
            "event study", "parallel trend", "coefficient plot",
            "scatter", "histogram", "density plot",
            "rd plot", "discontinuity plot",
        ) if kw in text_lower)
        score += min(visual_keywords * 0.1, 0.2)

        # Phase relevance
        if context.current_phase in ("deep_review", "methodology_check"):
            score += 0.1

        return min(score, 1.0)

    def execute(self, context: SkillContext) -> SkillResult:
        """Extract, classify, and analyze figures from the paper.

        Pipeline:
          1. Extract figure references
          2. Classify each figure's type
          3. Run economics-specific analysis per type
          4. Aggregate findings and output data
        """
        start_time = time.time()

        if not _is_enabled():
            return SkillResult(
                success=False,
                error_message=(
                    "Figure semantic analysis disabled by Kill Switch "
                    "(SCHOLAR_GODEL_FIGURE_SEMANTIC=0)"
                ),
            )

        if not context.paper_text:
            return SkillResult(
                success=True,
                output_data={"figures": [], "classifications": [], "analysis_results": {}},
                metadata={"figures_found": 0},
            )

        findings: list[Finding] = []

        # Step 1: Extract figure references
        figures: list[FigureReference] = []
        try:
            figures = self._extractor.extract_figures(context.paper_text)
            logger.info("Extracted %d figures from paper text", len(figures))
        except Exception as e:
            logger.warning("Figure extraction failed: %s", e)
            findings.append(Finding(
                category="figure",
                severity="minor",
                description=f"Figure extraction encountered an error: {e}",
                skill_source="figure_semantic_analysis",
                confidence=0.3,
            ))

        if not figures:
            return SkillResult(
                findings=findings,
                output_data={
                    "figures": [],
                    "classifications": [],
                    "analysis_results": {},
                    "extraction_stats": {"total_figures": 0},
                },
                success=True,
                execution_time_ms=(time.time() - start_time) * 1000,
                metadata={"figures_found": 0},
            )

        # Step 2: Classify each figure
        classifications: list[FigureClassification] = []
        try:
            for figure in figures:
                classification = self._extractor.classify_figure(
                    figure, context.paper_text
                )
                figure.figure_type = classification.primary_type
                classifications.append(classification)
        except Exception as e:
            logger.warning("Figure classification failed: %s", e)

        # Step 3: Economics-specific analysis
        analysis_findings: list[Finding] = []
        type_analysis_results: dict[str, list[dict]] = {}
        try:
            for figure in figures:
                if figure.figure_type != FigureType.OTHER:
                    fig_findings = self._analyzer.analyze(
                        figure, context.paper_text
                    )
                    analysis_findings.extend(fig_findings)

                    # Track analysis results by figure
                    type_analysis_results[figure.figure_id] = [
                        {
                            "category": f.category,
                            "severity": f.severity,
                            "description": f.description,
                        }
                        for f in fig_findings
                    ]
        except Exception as e:
            logger.warning("Economics figure analysis failed: %s", e)

        findings.extend(analysis_findings)

        # Step 4: Generate extraction-level findings
        findings.extend(self._generate_extraction_findings(figures, classifications))

        # Build output data
        execution_time = (time.time() - start_time) * 1000

        output_data = {
            "figures": [self._figure_to_dict(f) for f in figures],
            "classifications": [self._classification_to_dict(c) for c in classifications],
            "analysis_results": type_analysis_results,
            "extraction_stats": self._build_stats(figures, classifications),
        }

        return SkillResult(
            findings=findings,
            output_data=output_data,
            success=True,
            execution_time_ms=execution_time,
            metadata={
                "figures_found": len(figures),
                "types_detected": self._summarize_types(classifications),
            },
        )

    # ------------------------------------------------------------------
    # Helper: Output serialization
    # ------------------------------------------------------------------

    def _figure_to_dict(self, figure: FigureReference) -> dict:
        """Serialize a FigureReference to dict."""
        return {
            "figure_id": figure.figure_id,
            "caption": figure.caption[:500],
            "figure_type": figure.figure_type.value,
            "section": figure.section,
            "n_mentions": len(figure.text_mentions),
            "sub_figures": figure.sub_figures,
            "has_reported_values": bool(figure.reported_values),
            "reported_value_types": list(figure.reported_values.keys()),
        }

    def _classification_to_dict(self, cls: FigureClassification) -> dict:
        """Serialize a FigureClassification to dict."""
        result: dict = {
            "primary_type": cls.primary_type.value,
            "confidence": round(cls.confidence, 3),
            "reasoning": cls.reasoning,
            "matched_keywords": cls.matched_keywords[:5],
        }
        if cls.secondary_type:
            result["secondary_type"] = cls.secondary_type.value
        return result

    def _build_stats(
        self,
        figures: list[FigureReference],
        classifications: list[FigureClassification],
    ) -> dict:
        """Build extraction statistics summary."""
        type_counts: dict[str, int] = {}
        for cls in classifications:
            key = cls.primary_type.value
            type_counts[key] = type_counts.get(key, 0) + 1

        return {
            "total_figures": len(figures),
            "classified_figures": sum(
                1 for c in classifications if c.primary_type != FigureType.OTHER
            ),
            "unclassified_figures": sum(
                1 for c in classifications if c.primary_type == FigureType.OTHER
            ),
            "type_distribution": type_counts,
            "avg_confidence": (
                round(
                    sum(c.confidence for c in classifications) / len(classifications), 3
                )
                if classifications
                else 0.0
            ),
            "figures_with_values": sum(
                1 for f in figures if f.reported_values
            ),
        }

    def _summarize_types(self, classifications: list[FigureClassification]) -> str:
        """Create a summary string of detected types."""
        type_counts: dict[str, int] = {}
        for c in classifications:
            key = c.primary_type.value
            type_counts[key] = type_counts.get(key, 0) + 1
        return ", ".join(f"{k}={v}" for k, v in type_counts.items())

    def _generate_extraction_findings(
        self,
        figures: list[FigureReference],
        classifications: list[FigureClassification],
    ) -> list[Finding]:
        """Generate findings about the extraction process itself."""
        findings: list[Finding] = []

        # High ratio of unclassified figures
        if classifications:
            unclassified = sum(
                1 for c in classifications if c.primary_type == FigureType.OTHER
            )
            ratio = unclassified / len(classifications)
            if ratio > 0.7 and len(classifications) >= 3:
                findings.append(Finding(
                    category="figure",
                    severity="suggestion",
                    description=(
                        f"{unclassified} of {len(classifications)} figures could not "
                        f"be classified into standard economics figure types. "
                        f"Captions may need more descriptive language."
                    ),
                    skill_source="figure_semantic_analysis",
                    confidence=0.4,
                ))

        # Figures without captions
        captionless = sum(1 for f in figures if not f.caption)
        if captionless > 0:
            findings.append(Finding(
                category="figure",
                severity="minor",
                description=(
                    f"{captionless} figure(s) are referenced in text but "
                    f"have no detected caption."
                ),
                suggestion="Ensure all figures have descriptive captions.",
                skill_source="figure_semantic_analysis",
                confidence=0.5,
            ))

        return findings


# ==============================================================
# 2. FigureConsistencySkill
# ==============================================================


class FigureConsistencySkill(Skill):
    """验证图文一致性的 Functional Skill.

    执行流程：
      1. 接收 FigureSemanticSkill 的 output_data（或自行提取图表）
      2. 提取文中关于图表的所有声称
      3. 验证声称与图表描述/标题的一致性
      4. 检查图文覆盖率（孤立图表、幻影引用）
      5. 产出结构化 Finding 列表

    依赖：
      - 推荐先运行 figure_semantic_analysis skill，复用其 figures 输出
      - 也可独立运行（会自行提取图表）
    """

    _DESCRIPTOR = SkillDescriptor(
        name="figure_text_consistency",
        level=SkillLevel.FUNCTIONAL,
        description=(
            "图文一致性验证：验证文中关于图表的声称是否与图表描述/标题一致，"
            "检测量级不匹配、显著性矛盾、趋势描述偏差"
        ),
        prerequisites=("figure_semantic_analysis",),
        input_schema={
            "paper_text": "str (required)",
            "parameters.figures": "list[FigureReference] (optional, from upstream)",
        },
        output_schema={
            "inconsistencies": "list[dict] — detected inconsistencies",
            "coverage_report": "dict — figure-text coverage analysis",
        },
        applicable_phases=("deep_review", "cross_validation"),
        tags=("multimodal", "figure", "consistency", "cross_modal"),
        token_cost_estimate=600,
        version="1.0",
    )

    def __init__(self) -> None:
        self._cross_validator = FigureTextCrossValidator()
        self._extractor = FigureExtractor()

    @property
    def descriptor(self) -> SkillDescriptor:
        return self._DESCRIPTOR

    def can_apply(self, context: SkillContext) -> float:
        """Evaluate applicability — high if upstream provides figures.

        Signals:
          - Upstream figure data available (strong)
          - Figure references in text (moderate)
          - Phase relevance (mild)
          - Kill switch (hard gate)
        """
        if not _is_enabled():
            return 0.0

        score = 0.0

        # Strong signal: upstream already extracted figures
        upstream_figures = context.parameters.get("figures")
        if upstream_figures:
            score += 0.7

        if not context.paper_text:
            return min(score, 1.0)

        # Figure references in text
        text_lower = context.paper_text.lower()
        fig_refs = len(re.findall(r"(?:figure|fig\.?)\s+\d", text_lower))
        if fig_refs >= 3:
            score += 0.3
        elif fig_refs >= 1:
            score += 0.15

        # Phase relevance
        if context.current_phase in ("deep_review", "cross_validation"):
            score += 0.15

        # Claims language (suggests there are things to validate)
        claim_keywords = sum(1 for kw in (
            "shows that", "demonstrates", "reveals", "indicates",
            "as shown in", "consistent with", "confirms",
        ) if kw in text_lower)
        if claim_keywords >= 3:
            score += 0.15

        return min(score, 1.0)

    def execute(self, context: SkillContext) -> SkillResult:
        """Run figure-text cross-validation.

        Pipeline:
          1. Get or extract figure references
          2. Run cross-validation (claims + coverage)
          3. Aggregate findings
        """
        start_time = time.time()

        if not _is_enabled():
            return SkillResult(
                success=False,
                error_message=(
                    "Figure semantic analysis disabled by Kill Switch "
                    "(SCHOLAR_GODEL_FIGURE_SEMANTIC=0)"
                ),
            )

        # Get or extract figures
        figures = self._get_figures(context)

        if not figures and not context.paper_text:
            return SkillResult(
                findings=[],
                output_data={
                    "inconsistencies": [],
                    "coverage_report": {"message": "No figures to validate"},
                },
                success=True,
                execution_time_ms=(time.time() - start_time) * 1000,
            )

        findings: list[Finding] = []
        inconsistencies_data: list[dict] = []
        coverage_data: dict = {}

        # Run cross-validation
        try:
            xref_findings, coverage = self._cross_validator.validate(
                context.paper_text, figures
            )
            findings.extend(xref_findings)

            # Serialize inconsistencies for output
            inconsistencies_data = [
                {
                    "category": f.category,
                    "severity": f.severity,
                    "description": f.description,
                    "evidence": f.evidence,
                    "location": f.location,
                }
                for f in xref_findings
            ]

            coverage_data = {
                "total_figures": coverage.total_figures,
                "total_references": coverage.total_references,
                "orphan_figures": coverage.orphan_figures,
                "phantom_references": coverage.phantom_references,
                "under_discussed": coverage.under_discussed,
                "coverage_score": round(coverage.coverage_score, 3),
            }

            logger.info(
                "Figure-text cross-validation: %d findings, "
                "coverage=%.2f, orphans=%d, phantoms=%d",
                len(xref_findings),
                coverage.coverage_score,
                len(coverage.orphan_figures),
                len(coverage.phantom_references),
            )

        except Exception as e:
            logger.warning("Figure-text cross-validation failed: %s", e)
            findings.append(Finding(
                category="figure",
                severity="minor",
                description=f"Figure-text cross-validation encountered error: {e}",
                skill_source="figure_text_consistency",
                confidence=0.3,
            ))

        execution_time = (time.time() - start_time) * 1000

        return SkillResult(
            findings=findings,
            output_data={
                "inconsistencies": inconsistencies_data,
                "coverage_report": coverage_data,
            },
            success=True,
            execution_time_ms=execution_time,
            metadata={
                "figures_validated": len(figures),
                "inconsistencies_found": len(inconsistencies_data),
                "has_coverage_issues": bool(
                    coverage_data.get("orphan_figures")
                    or coverage_data.get("phantom_references")
                ),
            },
        )

    def _get_figures(self, context: SkillContext) -> list[FigureReference]:
        """Get FigureReference objects from upstream or extract fresh.

        Priority:
          1. Use upstream figures from parameters (from FigureSemanticSkill)
          2. Self-extract from paper_text
        """
        # Try upstream figures
        upstream = context.parameters.get("figures")
        if upstream and isinstance(upstream, list) and len(upstream) > 0:
            # Check if these are already FigureReference objects
            if isinstance(upstream[0], FigureReference):
                return upstream
            # If they're dicts (from serialization), reconstruct
            if isinstance(upstream[0], dict):
                return self._dicts_to_figures(upstream)

        # Self-extract from paper_text
        if context.paper_text:
            try:
                return self._extractor.extract_figures(context.paper_text)
            except Exception as e:
                logger.warning("Self-extraction of figures failed: %s", e)

        return []

    def _dicts_to_figures(self, dicts: list[dict]) -> list[FigureReference]:
        """Reconstruct FigureReference objects from serialized dicts."""
        figures: list[FigureReference] = []
        for d in dicts:
            if not isinstance(d, dict):
                continue
            fig = FigureReference(
                figure_id=d.get("figure_id", ""),
                caption=d.get("caption", ""),
            )
            # Restore figure type if available
            type_str = d.get("figure_type", "other")
            try:
                fig.figure_type = FigureType(type_str)
            except ValueError:
                fig.figure_type = FigureType.OTHER

            fig.sub_figures = d.get("sub_figures", [])
            figures.append(fig)

        return figures


# ==============================================================
# Re-export for __init__.py
# ==============================================================

__all__ = [
    "FigureSemanticSkill",
    "FigureConsistencySkill",
]
