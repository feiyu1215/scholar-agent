"""
eval/run_deai_gold.py — De-AI Gold Test Set Evaluation Runner.

Evaluates deai_engine against the gold standard test set to measure:
1. Detection recall: annotated signals that were correctly detected
2. Detection precision: detected signals that match real annotations
3. Fix quality: LLM-judged similarity between fixed text and human_reference
4. Voice preservation: whether academic register/content survives the fix

Usage:
    python -m eval.run_deai_gold                    # Full evaluation
    python -m eval.run_deai_gold --scene S1         # Only S1 cases
    python -m eval.run_deai_gold --signal TRICOLON  # Cases with specific signal
    python -m eval.run_deai_gold --audit-only       # Skip fix, only measure detection
    python -m eval.run_deai_gold --dry-run          # List cases without running
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field, asdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import List, Dict, Optional

# Ensure project root is importable
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

# Rate limiting for Friday API
os.environ.setdefault("SCHOLAR_MAX_CONCURRENT", "1")
os.environ.setdefault("SCHOLAR_MIN_INTERVAL", "12")

LLM_DELAY = 13  # seconds between LLM calls

# Paths
EVAL_DIR = Path(__file__).parent
GOLD_DIR = EVAL_DIR / "gold_deai"
REPORTS_DIR = EVAL_DIR / "reports"


# ============================================================
# Data Classes
# ============================================================

@dataclass
class GoldCase:
    """A single De-AI gold test case."""
    id: str
    scene: str
    difficulty: str
    primary_signals: List[str]
    secondary_signals: List[str]
    description: str
    ai_text: str
    human_reference: str
    signal_annotations: List[Dict]
    metadata: Dict


@dataclass
class DetectionResult:
    """Result of running deai_audit on a gold case."""
    case_id: str
    # Detection metrics
    annotated_signals: List[str]  # Expected signal types
    detected_signals: List[str]  # Actually detected signal types
    recall: float  # annotated detected / total annotated
    precision: float  # true positives / total detected
    f1: float
    # Raw audit result
    overall_score: float
    is_natural: bool
    signal_count: int


@dataclass
class FixResult:
    """Result of running deai_audit_and_fix on a gold case."""
    case_id: str
    # Fix quality metrics
    text_similarity: float  # SequenceMatcher ratio with human_reference
    content_preservation: float  # Word overlap with original factual content
    score_improvement: float  # Post-fix score - pre-fix score
    # Texts
    fixed_text: str
    human_reference: str


@dataclass
class CaseResult:
    """Combined result for one gold case."""
    case_id: str
    scene: str
    difficulty: str
    detection: DetectionResult
    fix: Optional[FixResult] = None
    # Composite score (0-5 scale to match main eval)
    composite_score: float = 0.0
    error: str = ""


@dataclass
class GoldEvalReport:
    """Full evaluation report."""
    run_id: str
    timestamp: str
    total_cases: int
    scenes_tested: List[str]
    # Aggregate scores
    avg_detection_recall: float = 0.0
    avg_detection_precision: float = 0.0
    avg_detection_f1: float = 0.0
    avg_fix_similarity: float = 0.0
    avg_score_improvement: float = 0.0
    avg_composite: float = 0.0
    # Breakdown by signal type
    signal_type_recall: Dict[str, float] = field(default_factory=dict)
    # Per-case results
    cases: List[Dict] = field(default_factory=list)


# ============================================================
# Case Loading
# ============================================================

def load_gold_cases(
    scene_filter: str = None,
    signal_filter: str = None,
) -> List[GoldCase]:
    """Load gold test cases from eval/gold_deai/ directory."""
    cases = []
    for case_file in sorted(GOLD_DIR.glob("deai_gold_*.json")):
        try:
            data = json.loads(case_file.read_text(encoding="utf-8"))
            case = GoldCase(
                id=data["id"],
                scene=data["scene"],
                difficulty=data["difficulty"],
                primary_signals=data["primary_signals"],
                secondary_signals=data.get("secondary_signals", []),
                description=data["description"],
                ai_text=data["ai_text"],
                human_reference=data["human_reference"],
                signal_annotations=data.get("signal_annotations", []),
                metadata=data.get("metadata", {}),
            )

            # Apply filters
            if scene_filter and case.scene != scene_filter:
                continue
            if signal_filter:
                all_signals = case.primary_signals + case.secondary_signals
                if signal_filter not in all_signals:
                    continue

            cases.append(case)
        except (json.JSONDecodeError, KeyError) as e:
            print(f"  Warning: Skipping {case_file.name}: {e}")

    return cases


# ============================================================
# Signal Type Matching
# ============================================================

# Map from annotated signal types to patterns in deai_engine output
SIGNAL_TYPE_MAP = {
    # --- G-code mappings (LLM sometimes returns 【Gx】 codes from deai_rules.md) ---
    # G1=Tricolon, G2=Resolution Closer, G3=Repeated Rhetorical Skeleton,
    # G4=Hedge Opener Stacking, G5=Uniform Sentence Length, G6=Connector Stacking,
    # G7=Universal Banned Words (AI_VOCABULARY), G8=Promotional Tone,
    # G9=Perplexity Awareness, G10=Negation Parallel, G11=Shallow Progressive,
    # G12=Copula Avoidance
    "AI_VOCABULARY": ["ai_vocabulary", "ai high-frequency", "banned word", "ai_high_freq",
                      "ai_word", "ai vocabulary", "inflated_symbol", "ai_cliche",
                      "universal banned", "g7"],
    "TRICOLON": ["tricolon", "three-item", "rule_of_three", "three_part", "triple",
                 "tricolon ban", "tricolon detection", "g1"],
    "RHYTHM_UNIFORMITY": ["rhythm", "uniform", "sentence length", "cv", "burstiness",
                          "sentence_length_variation", "monoton", "rhythm_uniform", "g5"],
    "CONNECTOR_STACKING": ["connector", "furthermore", "moreover", "additionally",
                           "connector_stacking", "transition_overuse", "g6"],
    "HEDGE_OPENERS": ["hedge_opener", "worth noting", "important to note", "throat_clear",
                      "it is worth", "it should be noted", "it is important",
                      "hedging", "hedge opener", "opening hedge",
                      "hedge opener stacking", "g4"],
    "PROMOTIONAL_TONE": ["promotional", "groundbreaking", "unprecedented", "revolutionary",
                         "promotional_language", "hype", "inflated",
                         "promotional tone", "g8"],
    "NEGATION_PARALLEL": ["negation_parallel", "not just", "not x; it's y", "contrast flip",
                          "not merely", "not only", "negation_flip",
                          "negation parallel ban", "g10"],
    "PASSIVE_VOICE_OVERUSE": ["passive_voice", "passive overuse", "passive_overuse",
                              "excessive passive", "overuse of passive", "passive construction"],
    "COPULA_AVOIDANCE": ["copula", "serves as", "stands as", "acts as", "functions as",
                         "copula avoidance", "g12"],
    "EMPTY_PROGRESSIVE": ["progressive", "transforming", "reshaping", "shallow_progressive",
                          "empty_progressive", "ing_without_evidence",
                          "shallow progressive", "g11"],
    "VAGUE_ATTRIBUTION": ["vague_attribution", "many argue", "experts believe", "attribution",
                          "unattributed", "vague source", "researchers suggest"],
    "FORMULAIC_TRANSITIONS": ["formulaic", "transition", "mechanical_connector",
                              "formulaic_transition", "mechanical transition",
                              "overused connector", "transition_word"],
    "TYPE_TOKEN_RATIO": ["type-token", "repetit", "same word", "type_token",
                         "word repetition", "lexical_repetition", "vocabulary_repetition",
                         "repeated_word", "over-repetit"],
    "RESOLUTION_CLOSER": ["resolution_closer", "ultimately", "in the end",
                          "philosophical_ending", "closing platitude", "wrap-up",
                          "resolution closer", "g2"],
    "THROAT_CLEARING": ["throat_clear", "filler sentence", "empty opener", "throat",
                        "throat clearing", "filler_phrase", "empty_opening",
                        "it is clear that", "needless to say", "as we know"],
    "HEDGE_STACKING": ["hedge_stack", "qualifier_stack", "over_hedging",
                       "hedge stacking", "multiple_hedges", "over-hedge",
                       "hedge opener stacking"],
    "PROMOTIONAL": ["promotional", "groundbreaking", "unprecedented"],
    "INFLATED_SYMBOLISM": ["inflated_symbolism", "tapestry", "testament to",
                           "beacon of", "cornerstone", "pillar of",
                           "inflated symbolism", "inflated_symbol"],
    "EM_DASH_OVERUSE": ["em_dash", "em dash", "dash_overuse", "dash overuse",
                        "excessive dash", "punctuation_dash",
                        "em-dash", "frequency limit"],
    "PARALLEL_STRUCTURE": ["parallel_structure", "parallel structure",
                           "repetitive structure", "structural repetition",
                           "parallel_pattern"],
}


def _match_signal(detected_type: str, annotated_type: str) -> bool:
    """Check if a detected signal type matches an annotated signal type.

    Uses pattern matching since deai_engine output names may differ from annotation labels.
    Avoids overly broad substring matches (e.g., "voice" matching "passive_voice_overuse").
    """
    detected_lower = detected_type.lower().strip()
    annotated_lower = annotated_type.lower().strip()

    # Direct match (exact or near-exact)
    if detected_lower == annotated_lower:
        return True

    # Underscore-normalized match
    det_norm = detected_lower.replace(" ", "_").replace("-", "_")
    ann_norm = annotated_lower.replace(" ", "_").replace("-", "_")
    if det_norm == ann_norm:
        return True

    # Check via pattern map (specific patterns per annotated type)
    patterns = SIGNAL_TYPE_MAP.get(annotated_type, [])
    for pattern in patterns:
        if pattern in detected_lower:
            return True

    # Fallback: annotated_lower fully contained in detected (but not vice versa
    # to avoid short strings like "ai" matching everything)
    if len(annotated_lower) >= 6 and annotated_lower in detected_lower:
        return True

    return False


def _dedupe_detected_types(detected_types: List[str]) -> List[str]:
    """Deduplicate detected signal types by normalized name.

    Multiple instances of the same signal type (e.g., 8x "AI High-Frequency Word Ban")
    are collapsed into one unique type for precision calculation.
    This prevents inflated FP counts when the engine correctly identifies multiple
    instances of the same underlying signal.
    """
    seen_normalized = set()
    unique_types = []
    for dt in detected_types:
        norm = dt.lower().strip().replace(" ", "_").replace("-", "_")
        if norm not in seen_normalized:
            seen_normalized.add(norm)
            unique_types.append(dt)
    return unique_types


def compute_detection_metrics(
    annotated_signals: List[str],
    detected_signals: List[Dict],
) -> tuple:
    """Compute recall, precision, F1 for signal detection.

    Uses TYPE-LEVEL evaluation:
    - Recall: fraction of annotated signal types that were detected
    - Precision: fraction of unique detected signal types that match an annotation

    This avoids penalizing the engine for finding multiple instances of the same
    signal type (e.g., 8 AI vocabulary violations count as 1 type hit).

    Args:
        annotated_signals: Expected signal types from gold annotations
        detected_signals: Raw signals from deai_audit (list of AISignal dicts)

    Returns:
        (recall, precision, f1, matched_annotated, detected_types)
    """
    if not annotated_signals:
        return (1.0, 1.0, 1.0, [], [])

    detected_types = [s.get("signal_type", "") for s in detected_signals]

    # Deduplicate detected signals by type for fair precision measurement
    unique_detected = _dedupe_detected_types(detected_types)

    # Recall: how many annotated signals were detected?
    # Each annotated signal can only be "claimed" by one detected signal (greedy match)
    matched_annotated = set()
    claimed_detected = set()  # Track which detected signals have been used
    for ann in annotated_signals:
        for i, det in enumerate(unique_detected):
            if i in claimed_detected:
                continue
            if _match_signal(det, ann):
                matched_annotated.add(ann)
                claimed_detected.add(i)
                break

    recall = len(matched_annotated) / len(annotated_signals) if annotated_signals else 0.0

    # Precision: how many unique detected types match something annotated?
    # Each annotated signal can only "absorb" one detected type
    true_positives = 0
    claimed_annotated = set()
    for det in unique_detected:
        for j, ann in enumerate(annotated_signals):
            if j in claimed_annotated:
                continue
            if _match_signal(det, ann):
                true_positives += 1
                claimed_annotated.add(j)
                break

    precision = true_positives / len(unique_detected) if unique_detected else 1.0

    # F1
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0

    return (recall, precision, f1, list(matched_annotated), detected_types)


# ============================================================
# Fix Quality Evaluation
# ============================================================

def compute_text_similarity(fixed_text: str, human_reference: str) -> float:
    """Compute text similarity between fixed output and human reference.

    Uses a combination of:
    1. SequenceMatcher ratio (structural similarity)
    2. Word overlap (content preservation)
    """
    # Structural similarity
    seq_ratio = SequenceMatcher(None, fixed_text, human_reference).ratio()

    # Word-level overlap (Jaccard)
    words_fixed = set(fixed_text.lower().split())
    words_ref = set(human_reference.lower().split())
    if not words_fixed or not words_ref:
        word_overlap = 0.0
    else:
        word_overlap = len(words_fixed & words_ref) / len(words_fixed | words_ref)

    # Weighted combination (structural matters more for style)
    return 0.6 * seq_ratio + 0.4 * word_overlap


def compute_content_preservation(original_text: str, fixed_text: str) -> float:
    """Check that factual content is preserved after fix.

    Measures what fraction of content words from original survive in fixed text.
    (Content words = words > 4 chars, excluding stopwords)
    """
    stopwords = {
        "that", "this", "with", "from", "have", "been", "were", "which",
        "their", "these", "those", "about", "would", "could", "should",
        "through", "between", "after", "before", "other", "another",
    }

    def content_words(text: str) -> set:
        words = set(text.lower().split())
        return {w for w in words if len(w) > 4 and w not in stopwords}

    orig_content = content_words(original_text)
    fixed_content = content_words(fixed_text)

    if not orig_content:
        return 1.0

    preserved = len(orig_content & fixed_content) / len(orig_content)
    return preserved


# ============================================================
# Main Evaluation Pipeline
# ============================================================

async def evaluate_case(case: GoldCase, audit_only: bool = False) -> CaseResult:
    """Evaluate a single gold case through deai_engine.

    Steps:
    1. Run deai_audit on ai_text → measure detection recall/precision
    2. (If not audit_only) Run deai_audit_and_fix → compare with human_reference
    3. Compute composite score
    """
    from tools.deai_engine import deai_audit, deai_audit_and_fix, AISignal

    # Step 1: Detection audit (skip_precheck=True to ensure LLM always runs in eval)
    try:
        verdict = await deai_audit(
            text=case.ai_text,
            scene=case.scene if case.scene != "S_GENERAL" else "S1",
            skip_precheck=True,
        )
    except Exception as e:
        return CaseResult(
            case_id=case.id,
            scene=case.scene,
            difficulty=case.difficulty,
            detection=DetectionResult(
                case_id=case.id,
                annotated_signals=case.primary_signals,
                detected_signals=[],
                recall=0.0, precision=0.0, f1=0.0,
                overall_score=0.0, is_natural=True, signal_count=0,
            ),
            error=f"deai_audit failed: {e}",
        )

    # Detect parse-failure PASS (engine defaulted to is_natural=True due to JSON parse error)
    parse_failed = (
        verdict.is_natural
        and len(verdict.signals) == 0
        and "(Audit parse error" in verdict.summary
    )

    # Compute detection metrics
    annotated = case.primary_signals + case.secondary_signals
    detected_raw = [asdict(s) if hasattr(s, '__dataclass_fields__') else s
                    for s in verdict.signals]
    recall, precision, f1, matched, detected_types = compute_detection_metrics(
        annotated, detected_raw
    )

    detection = DetectionResult(
        case_id=case.id,
        annotated_signals=annotated,
        detected_signals=detected_types,
        recall=recall,
        precision=precision,
        f1=f1,
        overall_score=verdict.overall_score,
        is_natural=verdict.is_natural,
        signal_count=len(verdict.signals),
    )

    parse_error_msg = "[WARN] LLM response parse failed — verdict defaulted to PASS" if parse_failed else ""

    # Step 2: Fix (if not audit_only)
    fix_result = None
    if not audit_only and not verdict.is_natural:
        try:
            await asyncio.sleep(LLM_DELAY)  # Rate limiting
            fixed_text, final_verdict, fixes = await deai_audit_and_fix(
                text=case.ai_text,
                scene=case.scene if case.scene != "S_GENERAL" else "S1",
            )

            text_sim = compute_text_similarity(fixed_text, case.human_reference)
            content_pres = compute_content_preservation(case.ai_text, fixed_text)
            score_improvement = final_verdict.overall_score - verdict.overall_score

            fix_result = FixResult(
                case_id=case.id,
                text_similarity=text_sim,
                content_preservation=content_pres,
                score_improvement=score_improvement,
                fixed_text=fixed_text,
                human_reference=case.human_reference,
            )
        except Exception as e:
            fix_result = FixResult(
                case_id=case.id,
                text_similarity=0.0,
                content_preservation=0.0,
                score_improvement=0.0,
                fixed_text="",
                human_reference=case.human_reference,
            )

    # Step 3: Composite score (0-5 scale)
    # Detection: 50% weight, Fix: 50% weight (or 100% detection if audit_only)
    detection_score = f1 * 5.0  # F1 → 0-5

    if fix_result and not audit_only:
        # Fix quality: weighted combination of similarity + content preservation + improvement
        fix_score = (
            fix_result.text_similarity * 0.4 +
            fix_result.content_preservation * 0.3 +
            min(fix_result.score_improvement / 0.3, 1.0) * 0.3  # Cap improvement contribution
        ) * 5.0
        composite = 0.5 * detection_score + 0.5 * fix_score
    else:
        composite = detection_score

    return CaseResult(
        case_id=case.id,
        scene=case.scene,
        difficulty=case.difficulty,
        detection=detection,
        fix=fix_result,
        composite_score=round(composite, 3),
        error=parse_error_msg,
    )


async def run_gold_eval(
    scene_filter: str = None,
    signal_filter: str = None,
    audit_only: bool = False,
    dry_run: bool = False,
) -> Optional[GoldEvalReport]:
    """Run full gold evaluation.

    Args:
        scene_filter: Only run cases for specific scene (S1, S3, S_GENERAL)
        signal_filter: Only run cases containing specific signal type
        audit_only: Skip fix step, only measure detection
        dry_run: List cases without running
    """
    cases = load_gold_cases(scene_filter=scene_filter, signal_filter=signal_filter)

    if not cases:
        print("No gold cases found matching filters.")
        return None

    if dry_run:
        print(f"Found {len(cases)} gold cases:")
        for case in cases:
            signals = ", ".join(case.primary_signals)
            print(f"  [{case.scene}] {case.id} ({case.difficulty}) — {signals}")
        return None

    print(f"Running De-AI Gold Eval: {len(cases)} cases"
          f"{' (audit-only)' if audit_only else ''}")
    print("=" * 60)

    results: List[CaseResult] = []
    for i, case in enumerate(cases):
        print(f"  [{i+1}/{len(cases)}] {case.id} ({case.scene}, {case.difficulty})...", end=" ")
        result = await evaluate_case(case, audit_only=audit_only)
        results.append(result)

        if result.error:
            print(f"ERROR: {result.error}")
        else:
            det = result.detection
            print(f"R={det.recall:.2f} P={det.precision:.2f} F1={det.f1:.2f} "
                  f"score={det.overall_score:.2f}", end="")
            if result.fix:
                print(f" | fix_sim={result.fix.text_similarity:.2f}", end="")
            print(f" | composite={result.composite_score:.2f}")

        # Rate limiting between cases
        if i < len(cases) - 1:
            await asyncio.sleep(LLM_DELAY)

    # Aggregate results
    report = _build_report(results, scene_filter)

    # Print summary
    _print_summary(report)

    # Save report
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / f"deai_gold_{report.run_id}.json"
    report_path.write_text(
        json.dumps(asdict(report), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\nReport saved: {report_path}")

    return report


def _build_report(results: List[CaseResult], scene_filter: str = None) -> GoldEvalReport:
    """Build aggregate report from individual case results."""
    run_id = str(int(time.time()))
    scenes = list(set(r.scene for r in results))

    # Aggregate detection metrics
    recalls = [r.detection.recall for r in results if not r.error]
    precisions = [r.detection.precision for r in results if not r.error]
    f1s = [r.detection.f1 for r in results if not r.error]
    composites = [r.composite_score for r in results if not r.error]

    # Fix metrics (only where fix was attempted)
    fix_sims = [r.fix.text_similarity for r in results if r.fix and not r.error]
    score_imps = [r.fix.score_improvement for r in results if r.fix and not r.error]

    # Per signal-type recall breakdown
    signal_recall: Dict[str, List[float]] = {}
    for r in results:
        if r.error:
            continue
        for sig in r.detection.annotated_signals:
            if sig not in signal_recall:
                signal_recall[sig] = []
            detected = 1.0 if sig in [
                ann for ann in r.detection.annotated_signals
                if any(_match_signal(d, ann) for d in r.detection.detected_signals)
            ] else 0.0
            signal_recall[sig].append(detected)

    signal_type_recall = {
        sig: sum(vals) / len(vals) if vals else 0.0
        for sig, vals in signal_recall.items()
    }

    return GoldEvalReport(
        run_id=run_id,
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
        total_cases=len(results),
        scenes_tested=scenes,
        avg_detection_recall=_safe_avg(recalls),
        avg_detection_precision=_safe_avg(precisions),
        avg_detection_f1=_safe_avg(f1s),
        avg_fix_similarity=_safe_avg(fix_sims),
        avg_score_improvement=_safe_avg(score_imps),
        avg_composite=_safe_avg(composites),
        signal_type_recall=signal_type_recall,
        cases=[asdict(r) for r in results],
    )


def _safe_avg(values: list) -> float:
    """Safe average that handles empty lists."""
    return round(sum(values) / len(values), 4) if values else 0.0


def _print_summary(report: GoldEvalReport) -> None:
    """Print formatted summary."""
    print("\n" + "=" * 60)
    print("DE-AI GOLD EVAL REPORT")
    print("=" * 60)
    print(f"\n  Run ID: {report.run_id}")
    print(f"  Timestamp: {report.timestamp}")
    print(f"  Cases: {report.total_cases} | Scenes: {', '.join(report.scenes_tested)}")

    print(f"\n  Detection Metrics:")
    print(f"    Recall:    {report.avg_detection_recall:.3f}")
    print(f"    Precision: {report.avg_detection_precision:.3f}")
    print(f"    F1:        {report.avg_detection_f1:.3f}")

    if report.avg_fix_similarity > 0:
        print(f"\n  Fix Metrics:")
        print(f"    Text Similarity to Reference: {report.avg_fix_similarity:.3f}")
        print(f"    Score Improvement:           {report.avg_score_improvement:+.3f}")

    print(f"\n  Composite Score: {report.avg_composite:.3f} / 5.00")

    if report.signal_type_recall:
        print(f"\n  Signal-Type Recall Breakdown:")
        for sig, recall in sorted(report.signal_type_recall.items(), key=lambda x: x[1]):
            bar = "█" * int(recall * 10) + "░" * (10 - int(recall * 10))
            print(f"    {sig:30s} [{bar}] {recall:.2f}")

    print("=" * 60)


# ============================================================
# CLI Entry Point
# ============================================================

def main():
    import argparse
    parser = argparse.ArgumentParser(description="De-AI Gold Test Set Evaluation")
    parser.add_argument("--scene", type=str, default=None,
                        help="Filter by scene (S1, S3, S_GENERAL)")
    parser.add_argument("--signal", type=str, default=None,
                        help="Filter by signal type")
    parser.add_argument("--audit-only", action="store_true",
                        help="Only run detection, skip fix evaluation")
    parser.add_argument("--dry-run", action="store_true",
                        help="List cases without running evaluation")
    args = parser.parse_args()

    asyncio.run(run_gold_eval(
        scene_filter=args.scene,
        signal_filter=args.signal,
        audit_only=args.audit_only,
        dry_run=args.dry_run,
    ))


if __name__ == "__main__":
    main()
