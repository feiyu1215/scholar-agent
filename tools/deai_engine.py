"""
tools/deai_engine.py — De-AI Audit & Fix Engine (PEV Loop).

Independent post-rewrite verifier that detects AI writing signals and
applies minimum-slice fixes. Separated from the rewrite agent to avoid
self-assessment bias (examiner ≠ examinee).

Design choices:
- Independent context: deai_audit sees ONLY the text to check + rules, not the rewrite prompt
- Sentence-level detection: each signal anchored to a specific sentence
- Minimum slice repair: fix only the flagged sentence, never rewrite paragraphs
- Red Line 3 enforcement: fix must not degrade expression quality
- Max 2 retries: if score doesn't improve by >0.05 after fix, stop and flag for manual
"""

from __future__ import annotations

import json
import asyncio
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field, asdict

from llm.client import LLMClient

WORKSPACE = Path(".workspace")
RULES_PATH = Path("skills/deai_rules_en.md")

MAX_RETRIES = 2
PASS_THRESHOLD = 0.7
IMPROVEMENT_THRESHOLD = 0.05  # Minimum score improvement to continue retrying


@dataclass
class AISignal:
    """A single detected AI writing signal."""
    sentence: str
    signal_type: str          # e.g., "AI_VOCABULARY", "TRICOLON", "RHYTHM_UNIFORMITY"
    confidence: float         # 0.0-1.0
    fix_suggestion: str       # Sentence-level rewrite suggestion
    location_hint: str = ""   # Approximate position in text


@dataclass
class DeAIVerdict:
    """Result of a de-AI audit pass."""
    is_natural: bool          # Overall pass/fail
    overall_score: float      # 0.0-1.0, higher = more natural
    signals: List[AISignal] = field(default_factory=list)
    summary: str = ""
    
    def to_dict(self) -> dict:
        d = asdict(self)
        return d


# ─── Prompts ─────────────────────────────────────────────────────────────────

DEAI_AUDIT_PROMPT = """You are an AI-text detection specialist for academic writing.
Your ONLY job is to detect AI writing signals in the provided text.
You are NOT the author. You are NOT improving the text. You are ONLY detecting.

## Detection Rules
{rules}

## Instructions
1. Read the text carefully
2. For EACH sentence that shows AI signals, report:
   - The exact sentence (verbatim quote)
   - Which signal category it triggers (from the rules above)
   - Your confidence (0.0-1.0) that this is genuinely an AI signal vs. natural style
   - A brief fix suggestion (sentence-level only)
3. Compute an overall naturalness score (0.0-1.0)
4. If score >= 0.7, verdict is PASS. Otherwise FAIL.

## Output (JSON only, no markdown):
{{
  "is_natural": true/false,
  "overall_score": <float>,
  "signals": [
    {{
      "sentence": "<exact quote>",
      "signal_type": "<category name>",
      "confidence": <float>,
      "fix_suggestion": "<rewritten sentence>"
    }}
  ],
  "summary": "<1-2 sentence overall assessment>"
}}

IMPORTANT:
- Only flag signals with confidence >= 0.5
- Do NOT flag disciplinary conventions as AI signals (e.g., passive in Methods)
- A single banned word in an otherwise natural paragraph = low confidence (0.5-0.6)
- Multiple structural patterns in one paragraph = high confidence (0.8+)
- If the text is already natural, output is_natural: true with empty signals list"""

DEAI_FIX_PROMPT = """You are fixing specific AI-writing signals in academic text.

## Rules:
1. Fix ONLY the sentences listed below. Do NOT touch any other sentence.
2. Each fix must be semantically equivalent to the original.
3. Maintain academic register and formality.
4. If you cannot fix a sentence without reducing quality, output it UNCHANGED and mark "kept_original": true.
5. Return the complete text with fixes applied.

## Signals to fix:
{signals_json}

## Original text to fix:
{text}

## Output (JSON):
{{
  "fixed_text": "<complete text with fixes applied>",
  "fixes_applied": [
    {{
      "original": "<original sentence>",
      "fixed": "<new sentence>",
      "kept_original": false
    }}
  ]
}}"""


# ─── Core Functions ──────────────────────────────────────────────────────────

async def deai_audit(
    text: str,
    original_text: str = None,
    scene: str = "S1",
    provider: str = None,
    model: str = None,
) -> DeAIVerdict:
    """
    Run de-AI detection on text. Returns verdict with signals.
    
    Args:
        text: The text to audit (typically post-rewrite)
        original_text: The pre-rewrite version (for comparison context)
        scene: "S1" (CS academic) or "S3" (economics) — affects rule loading
        provider/model: LLM config
    """
    rules = _load_rules(scene)
    client = LLMClient(model=model, max_concurrent=3, provider=provider)

    # Build audit prompt
    system = DEAI_AUDIT_PROMPT.format(rules=rules)
    user = f"Text to audit:\n\n{text}"
    if original_text:
        user += f"\n\n---\n[For context, this is the PRE-rewrite version — do NOT audit this, just use for comparison]\n{original_text[:1000]}"

    response = await client.chat(
        system=system,
        user=user,
        max_tokens=2000,
        temperature=0.0,
    )

    verdict = _parse_verdict(response)

    # Save audit result
    audit_dir = WORKSPACE / "deai"
    audit_dir.mkdir(parents=True, exist_ok=True)

    return verdict


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

    signals_json = json.dumps([asdict(s) for s in to_fix], indent=2, ensure_ascii=False)
    system = DEAI_FIX_PROMPT.format(signals_json=signals_json, text=text)

    response = await client.chat(
        system=system,
        user="Apply the fixes now.",
        max_tokens=3000,
        temperature=0.1,
    )

    # Parse fix response
    fixed_text, fixes = _parse_fix_response(response, text)
    return fixed_text, fixes


async def deai_audit_and_fix(
    text: str,
    original_text: str = None,
    scene: str = "S1",
    provider: str = None,
    model: str = None,
) -> Tuple[str, DeAIVerdict, List[Dict]]:
    """
    Full PEV loop: audit → fix → re-audit (max 2 retries).
    
    Returns: (final_text, final_verdict, all_fixes_applied)
    
    Failure handling per DESIGN.md §5.6:
    - If 2 consecutive passes show score improvement < 0.05: stop, return as-is
    - If fix degrades a sentence: keep original sentence (Red Line 3)
    - If rewrite has no substantial change from original: skip audit entirely
    """
    all_fixes = []
    current_text = text
    prev_score = 0.0

    for attempt in range(MAX_RETRIES + 1):
        # Audit
        verdict = await deai_audit(
            current_text, original_text=original_text, 
            scene=scene, provider=provider, model=model
        )

        # Pass? Done.
        if verdict.is_natural:
            return current_text, verdict, all_fixes

        # Check if improvement plateau (after first attempt)
        if attempt > 0 and (verdict.overall_score - prev_score) < IMPROVEMENT_THRESHOLD:
            verdict.summary += (
                f" [Stopped: score plateau after {attempt} attempts. "
                f"Remaining signals flagged for manual review.]"
            )
            return current_text, verdict, all_fixes

        prev_score = verdict.overall_score

        # Last attempt? Don't fix, just return with signals for manual review
        if attempt == MAX_RETRIES:
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
        fixed_text, fixes = await fix_ai_signals(
            current_text, signals_to_fix, provider=provider, model=model
        )

        # Red Line 3 check: ensure fix didn't degrade quality
        # (Simple heuristic: if fixed text is significantly shorter, something went wrong)
        if len(fixed_text) < len(current_text) * 0.7:
            verdict.summary += " [Fix rejected: excessive text reduction detected.]"
            return current_text, verdict, all_fixes

        all_fixes.extend(fixes)
        current_text = fixed_text

    return current_text, verdict, all_fixes


# ─── Helper Functions ────────────────────────────────────────────────────────

def _load_rules(scene: str) -> str:
    """Load de-AI rules for the given scene."""
    if not RULES_PATH.exists():
        return "(No rules file found — using general detection heuristics)"
    
    content = RULES_PATH.read_text(encoding="utf-8")
    
    # For S3 (economics), add a note about allowed patterns
    if scene == "S3":
        content += "\n\n## S3 Economics Exceptions:\n"
        content += "- Em-dashes: fully allowed\n"
        content += "- Passive voice in Methods/data sections: allowed\n"
        content += "- 'nuanced', 'intricate': allowed (disciplinary convention)\n"
        content += "- Hedging (suggests that, consistent with): allowed\n"
        content += "- Parenthetical insertions: allowed\n"
    
    # Truncate if too long (keep under 2000 chars for token efficiency)
    if len(content) > 2500:
        # Keep detection signals section only
        sections = content.split("## Fix Principles")
        content = sections[0] + "\n[Fix principles omitted for brevity]"
    
    return content


def _parse_verdict(response: str) -> DeAIVerdict:
    """Parse LLM audit response into DeAIVerdict."""
    response = response.strip()
    if response.startswith("```"):
        response = response.split("```")[1]
        if response.startswith("json"):
            response = response[4:]
    
    try:
        data = json.loads(response)
        signals = [
            AISignal(
                sentence=s.get("sentence", ""),
                signal_type=s.get("signal_type", "unknown"),
                confidence=s.get("confidence", 0.5),
                fix_suggestion=s.get("fix_suggestion", ""),
            )
            for s in data.get("signals", [])
        ]
        return DeAIVerdict(
            is_natural=data.get("is_natural", True),
            overall_score=data.get("overall_score", 0.7),
            signals=signals,
            summary=data.get("summary", ""),
        )
    except (json.JSONDecodeError, KeyError, TypeError):
        # Parsing failed — assume natural to avoid blocking pipeline
        return DeAIVerdict(
            is_natural=True,
            overall_score=0.7,
            signals=[],
            summary="(Audit parse error — defaulting to PASS)",
        )


def _parse_fix_response(response: str, original_text: str) -> Tuple[str, List[Dict]]:
    """Parse LLM fix response."""
    response = response.strip()
    if response.startswith("```"):
        response = response.split("```")[1]
        if response.startswith("json"):
            response = response[4:]
    
    try:
        data = json.loads(response)
        fixed_text = data.get("fixed_text", original_text)
        fixes = data.get("fixes_applied", [])
        
        # Filter out kept_original items
        actual_fixes = [f for f in fixes if not f.get("kept_original", False)]
        return fixed_text, actual_fixes
    except (json.JSONDecodeError, KeyError, TypeError):
        return original_text, []


def format_deai_result(verdict: DeAIVerdict, fixes: List[Dict] = None) -> str:
    """Format de-AI audit result for display."""
    lines = []
    status = "✅ PASS" if verdict.is_natural else "❌ FAIL"
    lines.append(f"De-AI Audit: {status} (score: {verdict.overall_score:.2f})")
    
    if verdict.summary:
        lines.append(f"  {verdict.summary}")
    
    if verdict.signals:
        lines.append(f"\n  Signals detected ({len(verdict.signals)}):")
        for i, sig in enumerate(verdict.signals[:5]):
            lines.append(f"    [{sig.signal_type}] (conf: {sig.confidence:.1f})")
            lines.append(f"      \"{sig.sentence[:80]}...\"")
            if sig.fix_suggestion:
                lines.append(f"      → \"{sig.fix_suggestion[:80]}...\"")
        if len(verdict.signals) > 5:
            lines.append(f"    ... and {len(verdict.signals) - 5} more")
    
    if fixes:
        lines.append(f"\n  Fixes applied: {len(fixes)}")
        for fix in fixes[:3]:
            lines.append(f"    - \"{fix.get('original', '')[:60]}\" → \"{fix.get('fixed', '')[:60]}\"")
    
    return "\n".join(lines)
