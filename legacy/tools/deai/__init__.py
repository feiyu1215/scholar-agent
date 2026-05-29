"""
De-AI Engine package.
Split from monolithic deai_engine.py for maintainability.
All public APIs remain importable from tools.deai_engine for backward compatibility.
"""

from tools.deai.constants import (
    # Constants
    WORKSPACE,
    RULES_PATH,
    MAX_RETRIES,
    PASS_THRESHOLD,
    IMPROVEMENT_THRESHOLD,
    SIGNAL_TOLERANCE_TIERS,
    DEFAULT_SIGNAL_TIER,
    CONDITIONAL_PASS_TOLERANCE,
    DIMENSION_WEIGHTS,
    DIMENSION_FLOOR,
    SIGNAL_TO_DIMENSION,
    DEFAULT_DIMENSION,
    AI_CLICHE_PATTERNS,
    HC_VOCABULARY_CAP,
    HC_VOCABULARY_THRESHOLD,
    HC_RHYTHM_CONSECUTIVE_CAP,
    HC_RHYTHM_CONSECUTIVE_THRESHOLD,
    HC_RHYTHM_BURSTINESS_CAP,
    HC_RHYTHM_BURSTINESS_CV_THRESHOLD,
    STRUCTURE_PATTERNS,
    FORBIDDEN_PATTERNS,
    DEAI_AUDIT_PROMPT,
    VOICE_AUDIT_ADDENDUM,
    DEAI_FIX_PROMPT,
    # Dataclasses
    AISignal,
    DimensionScores,
    HardCapResult,
    TieredJudgment,
    DeAIVerdict,
    SelfCheckResult,
    SelfCheckReport,
    DiagnosisResult,
)

from tools.deai.scene import (
    detect_scene,
    _is_chinese_text,
    _is_s3_discipline,
    _has_economics_keywords,
)

from tools.deai.signals import (
    detect_hard_caps,
    _detect_programmatic_signals,
    check_burstiness,
    compute_dimension_scores,
    apply_tiered_judgment,
    deai_audit,
    format_deai_result,
)

from tools.deai.fix import (
    fix_ai_signals,
    deai_audit_and_fix,
    closed_loop_fix,
    diagnose_signals,
    format_closed_loop_result,
)

from tools.deai.verify import (
    run_self_check,
)

from tools.deai.perplexity import (
    analyze_perplexity,
    get_perplexity_fix_hints,
    PerplexityScore,
    PerplexityReport,
)

from tools.deai.rules.loader import (
    load_scene_rules,
    load_rules_for_audit,
    get_scene_overrides,
    SceneRules,
    Rule,
    SceneOverride,
)

__all__ = [
    # Constants
    "WORKSPACE", "RULES_PATH", "MAX_RETRIES", "PASS_THRESHOLD",
    "IMPROVEMENT_THRESHOLD", "SIGNAL_TOLERANCE_TIERS", "DEFAULT_SIGNAL_TIER",
    "CONDITIONAL_PASS_TOLERANCE", "DIMENSION_WEIGHTS", "DIMENSION_FLOOR",
    "SIGNAL_TO_DIMENSION", "DEFAULT_DIMENSION", "AI_CLICHE_PATTERNS",
    "HC_VOCABULARY_CAP", "HC_VOCABULARY_THRESHOLD",
    "HC_RHYTHM_CONSECUTIVE_CAP", "HC_RHYTHM_CONSECUTIVE_THRESHOLD",
    "HC_RHYTHM_BURSTINESS_CAP", "HC_RHYTHM_BURSTINESS_CV_THRESHOLD",
    "STRUCTURE_PATTERNS", "FORBIDDEN_PATTERNS",
    "DEAI_AUDIT_PROMPT", "VOICE_AUDIT_ADDENDUM", "DEAI_FIX_PROMPT",
    # Dataclasses
    "AISignal", "DimensionScores", "HardCapResult", "TieredJudgment",
    "DeAIVerdict", "SelfCheckResult", "SelfCheckReport", "DiagnosisResult",
    # Scene
    "detect_scene",
    # Signals
    "detect_hard_caps", "check_burstiness",
    "compute_dimension_scores", "apply_tiered_judgment", "deai_audit",
    "format_deai_result",
    # Fix
    "fix_ai_signals", "deai_audit_and_fix", "closed_loop_fix",
    "diagnose_signals", "format_closed_loop_result",
    # Verify
    "run_self_check",
    # Perplexity
    "analyze_perplexity", "get_perplexity_fix_hints",
    "PerplexityScore", "PerplexityReport",
    # Rules (structured loader)
    "load_scene_rules", "load_rules_for_audit", "get_scene_overrides",
    "SceneRules", "Rule", "SceneOverride",
]
