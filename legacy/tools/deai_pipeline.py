"""
De-AI pipeline: 4 independent steps the Agent can orchestrate.

Each step is a standalone tool that takes explicit inputs and returns structured outputs.
The Agent can call them individually, skip steps, or adjust strategy between steps.

This replaces the rigid closed_loop_fix pipeline with Agent-driven orchestration.
"""

from __future__ import annotations

import asyncio
from typing import List, Dict, Optional
from dataclasses import asdict


def detect_ai_signals(text: str, scene: str = "S1") -> dict:
    """Step 1: Detect AI signals in text.

    Runs the full deai_audit pipeline (L1 precheck + L2 LLM audit + programmatic
    signal injection + dimension scoring + tiered judgment + hard caps).

    Args:
        text: The text to audit for AI writing signals.
        scene: "S1" (CS English), "S2" (Chinese academic), "S3" (Economics).

    Returns:
        {
            "signals": [{"sentence": str, "signal_type": str, "confidence": float, "fix_suggestion": str, "location_hint": str}, ...],
            "dimension_scores": {"vocabulary": float, "rhythm": float, "connectors": float, "punctuation": float, "voice": float, "weighted_overall": float},
            "overall_score": float,
            "is_natural": bool,
            "scene": str,
            "summary": str,
            "tiered_judgment": {"verdict": str, "reason": str} | None,
        }
    """
    try:
        from tools.deai_engine import deai_audit, detect_scene

        # Use provided scene or auto-detect
        effective_scene = scene or detect_scene(text)

        # deai_audit is async, run it in an event loop
        loop = _get_or_create_event_loop()
        verdict = loop.run_until_complete(
            deai_audit(text, scene=effective_scene)
        )

        # Build structured output
        signals_out = []
        for sig in verdict.signals:
            if hasattr(sig, "__dict__"):
                signals_out.append(asdict(sig))
            elif isinstance(sig, dict):
                signals_out.append(sig)

        dimension_scores = None
        if verdict.dimensions is not None:
            dimension_scores = verdict.dimensions.to_dict()

        tiered = None
        if verdict.tiered_judgment is not None:
            tiered = {
                "verdict": verdict.tiered_judgment.verdict,
                "reason": verdict.tiered_judgment.reason,
            }

        return {
            "signals": signals_out,
            "dimension_scores": dimension_scores,
            "overall_score": verdict.overall_score,
            "is_natural": verdict.is_natural,
            "scene": effective_scene,
            "summary": verdict.summary,
            "tiered_judgment": tiered,
        }

    except Exception as e:
        return {
            "error": f"{type(e).__name__}: {str(e)}",
            "signals": [],
            "dimension_scores": None,
            "overall_score": 0.0,
            "is_natural": False,
            "scene": scene,
            "summary": f"Detection failed: {e}",
            "tiered_judgment": None,
        }


def diagnose_signals(text: str, signals: list, scene: str = "S1") -> dict:
    """Step 2: Analyze detected signals and produce a diagnosis/fix strategy.

    Takes the signals from Step 1, determines root cause and fix approach for each.
    The Agent can use this to decide WHICH signals to fix and HOW.

    Args:
        text: The original text containing the signals.
        signals: List of signal dicts (from detect_ai_signals output).
        scene: Scene context (for potential future use).

    Returns:
        {
            "diagnosis": [{"signal_type": str, "sentence": str, "root_cause": str, "fix_strategy": str, "priority": int, "context_dependency": str}, ...],
            "fix_strategy": [str, ...],  # Ordered list of unique strategies
            "priority_order": [int, ...],  # Signal indices sorted by priority
        }
    """
    try:
        from tools.deai_engine import (
            diagnose_signals as _diagnose_signals,
            AISignal,
        )

        # Convert signal dicts to AISignal objects
        ai_signals = []
        for sig in signals:
            if isinstance(sig, dict):
                ai_signals.append(AISignal(
                    sentence=sig.get("sentence", ""),
                    signal_type=sig.get("signal_type", ""),
                    confidence=sig.get("confidence", 0.5),
                    fix_suggestion=sig.get("fix_suggestion", ""),
                    location_hint=sig.get("location_hint", ""),
                ))
            else:
                ai_signals.append(sig)

        if not ai_signals:
            return {
                "diagnosis": [],
                "fix_strategy": [],
                "priority_order": [],
            }

        # Run diagnosis
        results = _diagnose_signals(ai_signals, text)

        # Build structured output
        diagnosis_out = []
        strategies = []
        for diag in results:
            entry = {
                "signal_type": diag.signal.signal_type,
                "sentence": diag.signal.sentence,
                "root_cause": diag.root_cause,
                "fix_strategy": diag.fix_strategy,
                "priority": diag.priority,
                "context_dependency": diag.context_dependency,
            }
            diagnosis_out.append(entry)
            if diag.fix_strategy not in strategies:
                strategies.append(diag.fix_strategy)

        # Priority order: indices sorted by priority (already sorted by _diagnose_signals)
        priority_order = list(range(len(diagnosis_out)))

        return {
            "diagnosis": diagnosis_out,
            "fix_strategy": strategies,
            "priority_order": priority_order,
        }

    except Exception as e:
        return {
            "error": f"{type(e).__name__}: {str(e)}",
            "diagnosis": [],
            "fix_strategy": [],
            "priority_order": [],
        }


def rewrite_text(text: str, fix_strategy: list, scene: str = "S1",
                 author_constraints: str = "") -> dict:
    """Step 3: Apply fixes according to strategy.

    Takes the text and a list of signal dicts (with enriched fix_suggestion from
    diagnosis), applies minimum-slice fixes via the deai fix engine.

    Args:
        text: The text to rewrite.
        fix_strategy: List of signal dicts to fix. Each should have at minimum
                     "sentence", "signal_type", "confidence", "fix_suggestion".
                     The Agent can filter/modify these from the diagnose step.
        scene: Scene context (S1/S2/S3).
        author_constraints: Optional additional constraints for the rewrite
                           (e.g., "keep sentence under 25 words").

    Returns:
        {
            "revised_text": str,
            "changes_made": [{"original": str, "fixed": str}, ...],
            "warning": str,
        }
    """
    try:
        from tools.deai_engine import fix_ai_signals, AISignal

        # Convert strategy dicts to AISignal objects
        signals_to_fix = []
        for sig in fix_strategy:
            if isinstance(sig, dict):
                suggestion = sig.get("fix_suggestion", "")
                if author_constraints:
                    suggestion = f"[Constraints: {author_constraints}] {suggestion}"
                signals_to_fix.append(AISignal(
                    sentence=sig.get("sentence", ""),
                    signal_type=sig.get("signal_type", ""),
                    confidence=sig.get("confidence", 0.7),
                    fix_suggestion=suggestion,
                    location_hint=sig.get("location_hint", ""),
                ))
            else:
                signals_to_fix.append(sig)

        if not signals_to_fix:
            return {
                "revised_text": text,
                "changes_made": [],
                "warning": "No signals to fix.",
            }

        # fix_ai_signals is async
        loop = _get_or_create_event_loop()
        fixed_text, fixes, warning = loop.run_until_complete(
            fix_ai_signals(text, signals_to_fix)
        )

        # Red Line 3: reject if excessive reduction
        if len(fixed_text) < len(text) * 0.7:
            return {
                "revised_text": text,
                "changes_made": [],
                "warning": "Fix rejected: excessive text reduction (>30% shorter). Original preserved.",
            }

        return {
            "revised_text": fixed_text,
            "changes_made": fixes if isinstance(fixes, list) else [],
            "warning": warning or "",
        }

    except Exception as e:
        return {
            "error": f"{type(e).__name__}: {str(e)}",
            "revised_text": text,
            "changes_made": [],
            "warning": f"Rewrite failed: {e}",
        }


def verify_rewrite(original_text: str, revised_text: str, scene: str = "S1") -> dict:
    """Step 4: Verify the rewrite didn't introduce regressions.

    Runs the 4-layer self-check protocol (structure, rhythm, forbidden, voice)
    and a fresh detection pass to compute score delta.

    Args:
        original_text: The text BEFORE rewriting (for voice drift comparison).
        revised_text: The text AFTER rewriting (to verify).
        scene: Scene context (S1/S2/S3).

    Returns:
        {
            "passed": bool,
            "new_score": float,
            "delta": float,  # new_score - baseline (positive = improvement)
            "voice_drift": bool,
            "self_check": {"all_passed": bool, "overall_score": float, "blocking_layers": [str]},
            "warnings": [str],
        }
    """
    try:
        from tools.deai_engine import (
            deai_audit, run_self_check, detect_scene,
        )
        from utils.voice_profile import load_voice_profile, check_voice_drift

        effective_scene = scene or detect_scene(revised_text)
        warnings = []

        # Run self-check (4-layer protocol)
        self_check = run_self_check(revised_text, original_text)

        # Run fresh detection on the revised text for score
        loop = _get_or_create_event_loop()
        new_verdict = loop.run_until_complete(
            deai_audit(revised_text, original_text=original_text, scene=effective_scene)
        )
        new_score = new_verdict.overall_score

        # Compute delta against original
        orig_verdict = loop.run_until_complete(
            deai_audit(original_text, scene=effective_scene)
        )
        delta = new_score - orig_verdict.overall_score

        # Voice drift check
        voice_drift = False
        voice_fp = load_voice_profile()
        if voice_fp.total_words_analyzed > 0 and original_text:
            drift_result = check_voice_drift(original_text, revised_text, voice_fp)
            voice_drift = drift_result.get("drift_detected", False)
            if voice_drift:
                for w in drift_result.get("warnings", []):
                    warnings.append(f"Voice drift: {w}")

        # Collect self-check warnings
        if not self_check.all_passed:
            for layer in self_check.layers:
                if not layer.passed:
                    for v in layer.violations:
                        warnings.append(f"[{layer.layer}] {v}")

        # Overall pass: self-check passes AND score improved (or stays high)
        passed = self_check.all_passed and new_score >= 0.7 and not voice_drift

        return {
            "passed": passed,
            "new_score": round(new_score, 3),
            "delta": round(delta, 3),
            "voice_drift": voice_drift,
            "self_check": {
                "all_passed": self_check.all_passed,
                "overall_score": round(self_check.overall_score, 3),
                "blocking_layers": self_check.blocking_layers,
            },
            "warnings": warnings,
        }

    except Exception as e:
        return {
            "error": f"{type(e).__name__}: {str(e)}",
            "passed": False,
            "new_score": 0.0,
            "delta": 0.0,
            "voice_drift": False,
            "self_check": {"all_passed": False, "overall_score": 0.0, "blocking_layers": []},
            "warnings": [f"Verification failed: {e}"],
        }


# ─── Helper ──────────────────────────────────────────────────────────────────

def _get_or_create_event_loop():
    """Get the running event loop or create a new one.

    Handles the case where we're called from sync context (Agent tool dispatch)
    vs already inside an async context.
    """
    try:
        loop = asyncio.get_running_loop()
        # We're inside an async context — create a new loop in a thread
        # This shouldn't normally happen since tool dispatch is sync
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, asyncio.sleep(0))
            future.result()
        # Fallback: create new loop
        loop = asyncio.new_event_loop()
        return loop
    except RuntimeError:
        # No running loop — normal case for sync tool dispatch
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            return loop
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            return loop
