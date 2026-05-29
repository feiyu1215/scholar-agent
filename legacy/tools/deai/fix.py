"""
tools/deai/fix.py - Fix pipeline: fix_ai_signals, deai_audit_and_fix, closed_loop_fix.

Contains the repair functions that apply minimum-slice fixes to AI-flagged sentences.
"""

from __future__ import annotations

import json
from typing import List, Dict, Optional, Tuple
from dataclasses import asdict

from llm.client import LLMClient
from llm.router import get_model_for_task
from utils.voice_profile import load_voice_profile, get_voice_constraints, check_voice_drift
from utils.author_profile import load_profile, get_profile_context_for_prompt
from utils.json_repair import robust_json_parse

from tools.deai.constants import (
    MAX_RETRIES,
    IMPROVEMENT_THRESHOLD,
    CONDITIONAL_PASS_TOLERANCE,
    DEAI_FIX_PROMPT,
    AISignal,
    DimensionScores,
    TieredJudgment,
    DeAIVerdict,
    SelfCheckReport,
    DiagnosisResult,
)
from tools.deai.signals import (
    deai_audit,
    apply_tiered_judgment,
    check_burstiness,
    format_deai_result,
)
from tools.deai.verify import run_self_check


async def fix_ai_signals(
    text: str,
    signals: List[AISignal],
    provider: str = None,
    model: str = None,
) -> Tuple[str, List[Dict]]:
    """
    Apply minimum-slice fixes to flagged sentences.
    Returns (fixed_text, list_of_fixes_applied).
    """
    if not signals:
        return text, []

    # Only fix high-confidence signals
    to_fix = [s for s in signals if s.confidence >= 0.6]
    if not to_fix:
        return text, []

    client = LLMClient(model=model, max_concurrent=3, provider=provider)

    # Load Voice Profile constraints for fix guidance
    voice_fp = load_voice_profile()
    voice_fix_constraints = ""
    if voice_fp.total_words_analyzed > 0:
        voice_fix_constraints = (
            "\n## Author Voice Constraints (preserve these during fix):\n"
            + get_voice_constraints(voice_fp)
            + "\nKeep revised metrics within ±20% of these values."
        )

    # Load Author Profile constraints (learned rules + explicit preferences)
    profile = load_profile()
    profile_context = get_profile_context_for_prompt(profile)
    author_constraints = ""
    if profile_context:
        author_constraints = (
            "\n\n## Author Constraints (MUST respect):\n"
            + profile_context
            + "\n"
        )

    signals_json = json.dumps([asdict(s) for s in to_fix], indent=2, ensure_ascii=False)
    system = DEAI_FIX_PROMPT.format(
        signals_json=signals_json, text=text, voice_fix_constraints=voice_fix_constraints
    )
    # Inject author profile constraints after prompt construction
    if author_constraints:
        system += author_constraints

    response = await client.chat(
        system=system,
        user="Apply the fixes now.",
        max_tokens=3000,
        temperature=0.1,
        model=get_model_for_task("deai_fix"),
    )

    # Parse fix response
    fixed_text, fixes, warning = _parse_fix_response(response, text)
    return fixed_text, fixes, warning


def _parse_fix_response(response: str, original_text: str) -> Tuple[str, List[Dict], str]:
    """Parse LLM fix response using robust 4-layer JSON parsing.
    
    Returns:
        (fixed_text, fixes_applied, warning)
        warning is empty string on success, non-empty if parsing failed.
    """
    parsed = robust_json_parse(
        response,
        expected_keys=["fixed_text", "fixes_applied"],
        fallback_patterns={
            "fixed_text": r'"fixed_text"\s*:\s*"((?:[^"\\]|\\.)*)"',
        },
    )
    
    data = parsed["data"]
    
    if not data or (parsed["is_fallback"] and "fixed_text" not in data):
        warning = parsed["error"] or "Fix response JSON parse failed; original text unchanged."
        return original_text, [], warning
    
    fixed_text = data.get("fixed_text", original_text)
    fixes = data.get("fixes_applied", [])
    
    # Filter out kept_original items
    if isinstance(fixes, list):
        actual_fixes = [f for f in fixes if isinstance(f, dict) and not f.get("kept_original", False)]
    else:
        actual_fixes = []
    
    warning = ""
    if parsed["is_fallback"]:
        warning = f"JSON parsed via layer {parsed['layer']} (fallback): {parsed['error']}"
    
    return fixed_text, actual_fixes, warning


async def deai_audit_and_fix(
    text: str,
    original_text: str = None,
    scene: str = "S1",
    provider: str = None,
    model: str = None,
    baseline_score: Optional[float] = None,
    review_hints: str = "",
    review_hints_structured: Optional[List] = None,
) -> Tuple[str, DeAIVerdict, List[Dict]]:
    """
    Full PEV loop: audit → fix → re-audit (max 2 retries).
    
    Returns: (final_text, final_verdict, all_fixes_applied)
    
    Failure handling per DESIGN.md §5.6:
    - If 2 consecutive passes show score improvement < 0.05: stop, return as-is
    - If fix degrades a sentence: keep original sentence (Red Line 3)
    - If rewrite has no substantial change from original: skip audit entirely
    
    TODO-1 enhancements:
    - Tiered judgment replaces flat threshold for PASS/FAIL decisions
    - baseline_score enables "conditional PASS" when retries exhausted but close to baseline
    - Dimension scores track which aspects improved/degraded across retries
    
    Args:
        review_hints: Pre-formatted reviewer context for DeAI awareness (passed to deai_audit)
        review_hints_structured: Structured ReviewHint list for dimension bias computation
    """
    all_fixes = []
    current_text = text
    prev_score = 0.0

    # Capture initial baseline if not provided — first audit score becomes baseline
    initial_baseline = baseline_score

    def _apply_post_fix_checks(verdict: DeAIVerdict, current_text: str, original_text: str) -> None:
        """Apply voice drift + burstiness checks (mutates verdict in place)."""
        voice_fp = load_voice_profile()
        if voice_fp.total_words_analyzed > 0 and original_text:
            drift = check_voice_drift(original_text, current_text, voice_fp)
            if drift["drift_detected"]:
                verdict.summary += f" [Voice drift warning: {'; '.join(drift['warnings'])}]"

        burstiness = check_burstiness(current_text)
        if not burstiness["passed"]:
            verdict.summary += f" [Burstiness FAIL: CV={burstiness['cv']}, need >=0.35]"
            verdict.signals.append(AISignal(
                sentence="(paragraph-level)",
                signal_type="RHYTHM_UNIFORMITY",
                confidence=0.85,
                fix_suggestion=burstiness["warning"],
                location_hint="global",
            ))

    for attempt in range(MAX_RETRIES + 1):
        # Audit (pass review_hints only on first attempt — subsequent audits are fix-verification)
        verdict = await deai_audit(
            current_text, original_text=original_text, 
            scene=scene, provider=provider, model=model,
            review_hints=review_hints if attempt == 0 else "",
            review_hints_structured=review_hints_structured if attempt == 0 else None,
        )

        # Set baseline from first audit if not externally provided
        if attempt == 0 and initial_baseline is None:
            initial_baseline = verdict.overall_score

        # Pass (using tiered judgment)? Done.
        if verdict.is_natural:
            return current_text, verdict, all_fixes

        # CONDITIONAL_PASS: tiered judgment allows near-baseline pass
        if (verdict.tiered_judgment and
                verdict.tiered_judgment.verdict == "CONDITIONAL_PASS"):
            verdict.summary += (
                f" [Conditional PASS: {verdict.tiered_judgment.reason}]"
            )
            verdict.is_natural = True  # Grant conditional pass
            return current_text, verdict, all_fixes

        # Check if improvement plateau (after first attempt)
        if attempt > 0 and (verdict.overall_score - prev_score) < IMPROVEMENT_THRESHOLD:
            # Re-evaluate with baseline awareness for conditional pass
            if initial_baseline is not None and verdict.dimensions:
                retry_judgment = apply_tiered_judgment(
                    verdict.signals, verdict.dimensions,
                    baseline_score=initial_baseline,
                )
                if retry_judgment.verdict == "CONDITIONAL_PASS":
                    verdict.tiered_judgment = retry_judgment
                    verdict.is_natural = True
                    verdict.summary += (
                        f" [Conditional PASS on plateau: within {CONDITIONAL_PASS_TOLERANCE*100:.0f}% of baseline]"
                    )
                    return current_text, verdict, all_fixes

            _apply_post_fix_checks(verdict, current_text, original_text)
            verdict.summary += (
                f" [Stopped: score plateau after {attempt} attempts. "
                f"Remaining signals flagged for manual review.]"
            )
            return current_text, verdict, all_fixes

        prev_score = verdict.overall_score

        # Last attempt? Check for conditional pass before giving up
        if attempt == MAX_RETRIES:
            if initial_baseline is not None and verdict.dimensions:
                retry_judgment = apply_tiered_judgment(
                    verdict.signals, verdict.dimensions,
                    baseline_score=initial_baseline,
                )
                if retry_judgment.verdict == "CONDITIONAL_PASS":
                    verdict.tiered_judgment = retry_judgment
                    verdict.is_natural = True
                    verdict.summary += (
                        f" [Conditional PASS after {MAX_RETRIES} retries: near baseline]"
                    )
                    return current_text, verdict, all_fixes

            _apply_post_fix_checks(verdict, current_text, original_text)
            verdict.summary += (
                f" [Max retries ({MAX_RETRIES}) reached. "
                f"Remaining {len(verdict.signals)} signals flagged for manual review.]"
            )
            return current_text, verdict, all_fixes

        # Fix
        signals_to_fix = [
            AISignal(**s) if isinstance(s, dict) else s
            for s in verdict.signals
        ]
        fixed_text, fixes, fix_warning = await fix_ai_signals(
            current_text, signals_to_fix, provider=provider, model=model
        )

        if fix_warning:
            verdict.summary += f" [WARNING: {fix_warning}]"

        # Red Line 3 check: ensure fix didn't degrade quality
        # (Simple heuristic: if fixed text is significantly shorter, something went wrong)
        if len(fixed_text) < len(current_text) * 0.7:
            verdict.summary += " [Fix rejected: excessive text reduction detected.]"
            return current_text, verdict, all_fixes

        all_fixes.extend(fixes)
        current_text = fixed_text

    # Fallback (shouldn't normally reach here given loop structure)
    return current_text, verdict, all_fixes


def diagnose_signals(signals: List[AISignal], text: str) -> List[DiagnosisResult]:
    """Step 2 of closed loop: diagnose each signal to determine root cause and fix strategy.

    This is the critical thinking step that prevents blind rewrites.
    For each signal, we determine:
    - WHY it triggers detection (root cause)
    - HOW to fix it (strategy)
    - Whether fixing it might cascade to neighbors (dependency)
    """
    results = []

    for signal in signals:
        # Determine root cause from signal_type
        root_cause, strategy, priority = _infer_fix_approach(signal)

        # Check context dependency
        context_dep = "independent"
        sent_idx = text.find(signal.sentence)
        if sent_idx >= 0:
            # If sentence is short and sandwiched, might need neighbor context
            if len(signal.sentence.split()) < 10:
                context_dep = "requires_neighbor"
            # If signal_type is about rhythm, it's paragraph-level
            if signal.signal_type in ("RHYTHM_UNIFORMITY", "PARALLEL_STRUCTURE"):
                context_dep = "paragraph_level"

        results.append(DiagnosisResult(
            signal=signal,
            root_cause=root_cause,
            fix_strategy=strategy,
            priority=priority,
            context_dependency=context_dep,
        ))

    # Sort by priority (fix most critical first)
    results.sort(key=lambda d: d.priority)
    return results


def _infer_fix_approach(signal: AISignal) -> tuple:
    """Infer root cause, strategy, and priority from signal type."""
    signal_type = signal.signal_type.upper()

    FIX_MAP = {
        "AI_VOCABULARY": (
            "Banned/overused AI word detected",
            "lexical_replacement",
            1,  # Must fix — zero tolerance
        ),
        "TRICOLON": (
            "Three-part parallel list (AI fingerprint)",
            "restructure_to_prose",
            2,
        ),
        "RHYTHM_UNIFORMITY": (
            "Sentence lengths too uniform — low burstiness",
            "vary_sentence_length",
            2,
        ),
        "PROMOTIONAL_LANGUAGE": (
            "Unjustified superlative or value judgment",
            "hedging_or_removal",
            1,
        ),
        "EM_DASH_OVERUSE": (
            "Em-dash parenthetical chain (AI marker)",
            "convert_to_comma_or_period",
            2,
        ),
        "PARALLEL_STRUCTURE": (
            "Overly parallel syntactic structure",
            "restructure_asymmetric",
            3,
        ),
        "VAGUE_ATTRIBUTION": (
            "Vague subject (researchers, experts, studies) without citation",
            "add_specificity_or_cite",
            2,
        ),
        "INFLATED_SYMBOLISM": (
            "Unwarranted metaphor or symbolic language",
            "literal_restatement",
            2,
        ),
    }

    default = (
        f"General AI signal: {signal.signal_type}",
        "sentence_level_rewrite",
        2,
    )

    return FIX_MAP.get(signal_type, default)


# DEPRECATED: Use tools/deai_pipeline.py individual steps for Agent-orchestrated flow
async def closed_loop_fix(
    text: str,
    original_text: str = None,
    scene: str = "S1",
    provider: str = None,
    model: str = None,
    review_hints: str = "",
    review_hints_structured: Optional[List] = None,
) -> Tuple[str, DeAIVerdict, SelfCheckReport, List[Dict]]:
    """Full 四步闭环: detect → diagnose → rewrite → verify.

    Enhanced version of deai_audit_and_fix that:
    1. DETECT: Run audit (same as before)
    2. DIAGNOSE: Analyze each signal's root cause and fix strategy
    3. REWRITE: Apply fixes with strategy-specific guidance
    4. VERIFY: Run 四层自检 on the result

    Args:
        review_hints: Pre-formatted reviewer context (passed to first deai_audit call)
        review_hints_structured: Structured ReviewHint list for dimension bias computation

    Returns: (final_text, verdict, self_check_report, all_fixes)
    """
    all_fixes = []
    current_text = text
    prev_score = 0.0

    for attempt in range(MAX_RETRIES + 1):
        # ── Step 1: DETECT ──
        verdict = await deai_audit(
            current_text, original_text=original_text,
            scene=scene, provider=provider, model=model,
            review_hints=review_hints if attempt == 0 else "",
            review_hints_structured=review_hints_structured if attempt == 0 else None,
        )

        if verdict.is_natural:
            # Still run self-check even on PASS for quality assurance
            self_check = run_self_check(current_text, original_text)
            return current_text, verdict, self_check, all_fixes

        # Check plateau
        if attempt > 0 and (verdict.overall_score - prev_score) < IMPROVEMENT_THRESHOLD:
            self_check = run_self_check(current_text, original_text)
            verdict.summary += f" [Stopped: plateau after {attempt} attempts]"
            return current_text, verdict, self_check, all_fixes

        prev_score = verdict.overall_score

        if attempt == MAX_RETRIES:
            self_check = run_self_check(current_text, original_text)
            verdict.summary += f" [Max retries. {len(verdict.signals)} signals for manual review.]"
            return current_text, verdict, self_check, all_fixes

        # ── Step 2: DIAGNOSE ──
        signals_to_fix = [
            AISignal(**s) if isinstance(s, dict) else s
            for s in verdict.signals
        ]
        diagnoses = diagnose_signals(signals_to_fix, current_text)

        # Filter: only fix priority 1-2, confidence >= 0.6
        actionable = [
            d for d in diagnoses
            if d.priority <= 2 and d.signal.confidence >= 0.6
        ]

        if not actionable:
            self_check = run_self_check(current_text, original_text)
            verdict.summary += " [No actionable signals (all low-priority or low-confidence)]"
            return current_text, verdict, self_check, all_fixes

        # ── Step 3: REWRITE (strategy-aware) ──
        # Inject diagnosis into fix prompt for smarter fixes
        enhanced_signals = []
        for diag in actionable:
            sig = diag.signal
            # Enrich the fix_suggestion with strategy guidance
            strategy_hint = f"[Strategy: {diag.fix_strategy}] "
            enhanced_sig = AISignal(
                sentence=sig.sentence,
                signal_type=sig.signal_type,
                confidence=sig.confidence,
                fix_suggestion=strategy_hint + (sig.fix_suggestion or ""),
                location_hint=sig.location_hint,
            )
            enhanced_signals.append(enhanced_sig)

        fixed_text, fixes, fix_warning = await fix_ai_signals(
            current_text, enhanced_signals, provider=provider, model=model
        )

        if fix_warning:
            verdict.summary += f" [WARNING: {fix_warning}]"

        # Red Line 3: reject if excessive reduction
        if len(fixed_text) < len(current_text) * 0.7:
            self_check = run_self_check(current_text, original_text)
            verdict.summary += " [Fix rejected: excessive text reduction]"
            return current_text, verdict, self_check, all_fixes

        all_fixes.extend(fixes)
        current_text = fixed_text

    # ── Step 4: VERIFY (四层自检) ──
    self_check = run_self_check(current_text, original_text)

    # If self-check fails on L3 (forbidden), we have a problem
    if not self_check.all_passed:
        verdict.summary += f" [Self-check BLOCKED: {', '.join(self_check.blocking_layers)}]"

    return current_text, verdict, self_check, all_fixes


def format_closed_loop_result(
    verdict: DeAIVerdict,
    self_check: SelfCheckReport,
    fixes: List[Dict] = None,
) -> str:
    """Format the full closed-loop result for display."""
    lines = []

    # De-AI verdict
    lines.append(format_deai_result(verdict, fixes))

    # Self-check report
    lines.append("")
    lines.append(self_check.summary())

    return "\n".join(lines)
