"""evaluation/ — ScholarAgent V3 Evaluation Framework.

Quantitative evaluation of review quality using precision/recall/F1
against human-annotated gold-standard findings.

Phase 5 (Meta-Harness) extends with:
    - Process quality metrics (efficiency, stability, coverage)
    - Bottleneck identification across batch evaluations
    - Combined ReviewQualityMetrics (content + process)
    - EvaluationHarness for unified batch evaluation

Modules:
    - metrics.py: Finding-level P/R/F1 computation (MVP)
    - quality_metrics.py: ReviewQualityMetrics dataclass (Phase 5)
    - process_collector.py: Process metrics collection (Phase 5)
    - bottleneck_analyzer.py: Systematic bottleneck detection (Phase 5)
    - eval_harness.py: Batch evaluation orchestrator (Phase 5)
    - run_eval.py: CLI runner (MVP + Phase 5)
"""
