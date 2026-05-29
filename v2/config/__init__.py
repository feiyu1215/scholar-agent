"""
config — Centralized threshold and configuration loader.

Provides a single function `load_thresholds()` that reads
config/thresholds.yaml and returns a nested dict. The result
is cached (loaded once per process). If the YAML file is missing,
falls back to hardcoded defaults so the system still works.
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Dict, Any

_CONFIG_DIR = Path(__file__).resolve().parent
_THRESHOLDS_FILE = _CONFIG_DIR / "thresholds.yaml"


# ============================================================
# Hardcoded Defaults (fallback if YAML is missing)
# ============================================================

_DEFAULTS: Dict[str, Any] = {
    "deai_engine": {
        "max_retries": 2,
        "pass_threshold": 0.70,
        "improvement_threshold": 0.05,
        "conditional_pass_tolerance": 0.05,
        "dimension_floor": 0.40,
        "hard_caps": {
            "vocabulary_cap": 0.60,
            "vocabulary_threshold": 2,
            "rhythm_consecutive_cap": 0.50,
            "rhythm_consecutive_threshold": 3,
            "rhythm_burstiness_cap": 0.40,
            "rhythm_burstiness_cv_threshold": 0.20,
        },
        "signals": {
            "formulaic_transition_threshold": 3,
            "ttr_window": 5,
            "ttr_repeat_threshold": 3,
            "throat_clearing_threshold": 2,
            "promotional_threshold": 2,
            "inflated_symbolism_threshold": 2,
            "passive_voice_ratio_threshold": 0.50,
            "methods_verb_threshold": 4,
            "parallel_structure_threshold": 3,
        },
        "signals_zh": {
            "throat_clearing_zh_threshold": 3,
            "promotional_zh_threshold": 2,
            "connector_zh_threshold": 5,
            "parallel_zh_threshold": 2,
            "inflated_zh_threshold": 2,
            "econ_threshold": 3,
        },
    },
    "post_edit_verify": {
        "voice_drift": {
            "sentence_length_threshold": 0.30,
            "passive_ratio_threshold": 0.15,
            "formality_threshold": 0.30,
        },
    },
    "review_engine": {
        "consensus": {
            "start_score": 9.0,
            "major_deduction": 1.5,
            "moderate_deduction": 0.7,
            "minor_deduction": 0.2,
            "score_floor": 1.0,
            "desk_reject_cap": 4.0,
        },
        "verdict_boundaries": {
            "strong_accept": 8.0,
            "accept": 7.0,
            "weak_accept": 6.0,
            "borderline": 5.0,
            "weak_reject": 4.0,
            "reject": 2.5,
        },
    },
    "quality_gate": {
        "gate_pass_threshold": 0.65,
        "restart_threshold": 0.35,
        "restart_weak_dimension_count": 4,
        "dimensions": {
            "specificity": {"weight": 0.25, "threshold": 0.60},
            "coverage": {"weight": 0.20, "threshold": 0.50},
            "actionability": {"weight": 0.20, "threshold": 0.60},
            "calibration": {"weight": 0.15, "threshold": 0.50},
            "evidence": {"weight": 0.20, "threshold": 0.60},
        },
        "calibration_penalties": {
            "major_ratio_severe": 0.70,
            "major_ratio_moderate": 0.50,
            "minor_ratio_severe": 0.70,
            "minor_ratio_moderate": 0.50,
            "severe_penalty": 0.40,
            "moderate_penalty": 0.20,
            "single_issue_penalty": 0.10,
            "balanced_bonus": 0.10,
        },
    },
}


# ============================================================
# Public API
# ============================================================

@functools.lru_cache(maxsize=1)
def load_thresholds() -> Dict[str, Any]:
    """Load thresholds from YAML file, falling back to hardcoded defaults.

    The result is cached so the YAML is only parsed once per process.
    To force a reload (e.g. in tests), call `load_thresholds.cache_clear()`.

    Returns:
        Nested dict with keys: deai_engine, post_edit_verify,
        review_engine, quality_gate.
    """
    if _THRESHOLDS_FILE.exists():
        try:
            import yaml  # type: ignore[import-untyped]

            with open(_THRESHOLDS_FILE, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if isinstance(data, dict):
                return data
        except Exception:
            # Any parse error → fall back to defaults silently
            pass

    # Return a deep copy of defaults to prevent mutation
    import copy
    return copy.deepcopy(_DEFAULTS)
