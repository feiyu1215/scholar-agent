"""
eval/run_eval.py - Evaluation runner for Scholar-Agent quality benchmarks.

Runs benchmark cases through the system, evaluates outputs using judge prompts,
and produces quality reports. Used for development iteration (not runtime).

Usage:
    python -m eval.run_eval --level L1         # Run L1 format benchmarks
    python -m eval.run_eval --level all        # Run all levels
    python -m eval.run_eval --level L1 --dry-run  # List cases without running
    python -m eval.run_eval --level L2 --judge review  # Specify judge type

Architecture:
    - Loads benchmark cases from eval/benchmarks/L{1-4}_*/
    - L1: runs presubmission_check (zero LLM) + rule-based scoring
    - L2-L4: runs mini_review (1 LLM call) + LLM judge scoring
    - Produces JSON report with per-case and aggregate scores
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import traceback
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Dict, Optional, Tuple

# Ensure project root is importable
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

# Rate limiting for Friday API
os.environ.setdefault("SCHOLAR_MAX_CONCURRENT", "1")
os.environ.setdefault("SCHOLAR_MIN_INTERVAL", "12")

LLM_DELAY = 13  # seconds between LLM calls


# ============================================================
# Configuration
# ============================================================

EVAL_DIR = Path(__file__).parent
BENCHMARKS_DIR = EVAL_DIR / "benchmarks"
JUDGE_PROMPTS_DIR = EVAL_DIR / "judge_prompts"
RUBRICS_DIR = EVAL_DIR / "rubrics"
REPORTS_DIR = EVAL_DIR / "reports"


# ============================================================
# Data Classes
# ============================================================

@dataclass
class BenchmarkCase:
    """A single evaluation test case."""
    id: str
    level: str  # "L1" | "L2" | "L3" | "L4"
    category: str  # "format" | "logic" | "academic" | "domain"
    input_text: str
    tool: str = ""  # "presubmission_check" | "review" | "deai"
    expected_issues: List[str] = field(default_factory=list)
    gold_verdict: str = ""  # "not_ready" | "ready" etc.
    difficulty: str = "medium"  # "easy" | "medium" | "hard"
    metadata: Dict = field(default_factory=dict)


@dataclass
class JudgeScore:
    """Score from a single judge evaluation."""
    case_id: str
    judge_type: str  # "review" | "rewrite" | "deai" | "format_rule"
    dimension_scores: Dict[str, float] = field(default_factory=dict)
    composite_score: float = 0.0
    rationale: str = ""
    raw_tool_output: str = ""
    timestamp: str = ""


@dataclass
class EvalReport:
    """Aggregate evaluation report."""
    run_id: str
    timestamp: str
    level: str
    total_cases: int
    scores: List[JudgeScore] = field(default_factory=list)
    avg_composite: float = 0.0
    dimension_averages: Dict[str, float] = field(default_factory=dict)
    pass_rate: float = 0.0  # % of cases scoring above threshold


# ============================================================
# Benchmark Loading
# ============================================================

def load_benchmarks(level: Optional[str] = None) -> List[BenchmarkCase]:
    """Load benchmark cases from eval/benchmarks/ directory."""
    cases = []

    for level_dir in sorted(BENCHMARKS_DIR.iterdir()):
        if not level_dir.is_dir():
            continue

        dir_level = level_dir.name.split("_")[0].upper()
        if level and dir_level != level.upper():
            continue

        for case_file in sorted(level_dir.glob("*.json")):
            try:
                data = json.loads(case_file.read_text(encoding="utf-8"))
                cases.append(BenchmarkCase(
                    id=data.get("id", case_file.stem),
                    level=dir_level,
                    category=data.get("category", level_dir.name.split("_", 1)[1] if "_" in level_dir.name else "unknown"),
                    input_text=data.get("input_text", ""),
                    tool=data.get("tool", ""),
                    expected_issues=data.get("expected_issues", []),
                    gold_verdict=data.get("gold_verdict", ""),
                    difficulty=data.get("difficulty", "medium"),
                    metadata=data.get("metadata", {}),
                ))
            except (json.JSONDecodeError, KeyError) as e:
                print(f"Warning: Failed to load {case_file}: {e}")

    return cases


def load_judge_prompt(judge_type: str) -> str:
    """Load a judge prompt by type."""
    prompt_file = JUDGE_PROMPTS_DIR / f"{judge_type}_quality_judge.md"
    if not prompt_file.exists():
        raise FileNotFoundError(f"Judge prompt not found: {prompt_file}")
    return prompt_file.read_text(encoding="utf-8")


# ============================================================
# L1 Evaluation: Rule-based (Zero LLM cost)
# ============================================================

def evaluate_l1_case(case: BenchmarkCase) -> JudgeScore:
    """
    Evaluate an L1 format case using presubmission_check.
    
    Scoring is rule-based: did the tool correctly detect the expected issues?
    No LLM needed.
    """
    from tools.presubmission_check import run_presubmission_checks, format_presubmission_report

    # Run the tool
    report = run_presubmission_checks(case.input_text)
    formatted = format_presubmission_report(report)

    # Score based on detection of expected issues
    failed_checks = [r.check_name for r in report.results if not r.passed]
    
    # Dimension 1: Detection Recall — did we find the expected issues?
    expected = set(case.expected_issues)
    detected = set(failed_checks)
    recall_hits = expected & detected
    recall = len(recall_hits) / len(expected) if expected else 1.0

    # Dimension 2: Precision — are the failures relevant (not too many false positives)?
    # A few extra flags are okay, but too many false positives is bad
    if detected:
        true_positives = len(recall_hits)
        precision = true_positives / len(detected) if detected else 1.0
    else:
        precision = 0.0 if expected else 1.0

    # Dimension 3: Severity calibration — are the detected issues appropriately ranked?
    severity_scores = {"error": 3, "warning": 2, "info": 1}
    severity_appropriate = 1.0  # Default: assume appropriate
    for r in report.results:
        if not r.passed and r.check_name in expected:
            # Expected issues should be at least "warning"
            if severity_scores.get(r.severity, 0) < 2:
                severity_appropriate = 0.7

    # Dimension 4: Verdict correctness
    verdict_correct = 1.0
    if case.gold_verdict == "not_ready" and report.verdict == "ready":
        verdict_correct = 0.0
    elif case.gold_verdict == "ready" and report.verdict != "ready":
        verdict_correct = 0.0

    # Composite: weighted average → scale to 1-5
    raw = (recall * 0.40 + precision * 0.25 + severity_appropriate * 0.15 + verdict_correct * 0.20)
    composite = raw * 4.0 + 1.0  # Map [0,1] → [1,5]

    return JudgeScore(
        case_id=case.id,
        judge_type="format_rule",
        dimension_scores={
            "detection_recall": round(recall * 5, 2),
            "detection_precision": round(precision * 5, 2),
            "severity_calibration": round(severity_appropriate * 5, 2),
            "verdict_correctness": round(verdict_correct * 5, 2),
        },
        composite_score=round(composite, 2),
        rationale=(
            f"Expected: {sorted(expected)}. Detected: {sorted(detected)}. "
            f"Recall={recall:.0%}, Precision={precision:.0%}. "
            f"Verdict: tool={report.verdict}, gold={case.gold_verdict}."
        ),
        raw_tool_output=formatted[:2000],
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
    )


# ============================================================
# L2-L4 Evaluation: LLM-based (mini review + judge)
# ============================================================

MINI_REVIEW_SYSTEM = """You are a rigorous academic reviewer examining a paper excerpt. 
Your task is to identify ALL issues in this text, covering:
- Logical coherence and argumentation quality
- Methodology soundness
- Data consistency
- Citation adequacy and verifiability
- Academic standards compliance
- Domain-specific rigor (if applicable)

For each issue found, provide:
1. A short title
2. Severity: "critical" | "major" | "minor"
3. The exact quote or location
4. Why it's a problem
5. A specific suggestion for improvement

Output as JSON array:
[
  {
    "title": "...",
    "severity": "...",
    "quote": "...",
    "explanation": "...",
    "suggestion": "..."
  }
]

Be thorough but fair. Only flag genuine issues, not stylistic preferences."""


async def run_mini_review(text: str, depth: str = "standard") -> str:
    """Run a lightweight LLM review on a text excerpt. Returns raw LLM output."""
    from llm.client import LLMClient

    client = LLMClient(max_concurrent=1)

    extra_instruction = ""
    if depth == "deep":
        extra_instruction = "\nPay special attention to methodology, causal identification, and domain-specific standards."

    response = await client.chat(
        system=MINI_REVIEW_SYSTEM + extra_instruction,
        user=f"Review this paper excerpt:\n\n{text}",
        max_tokens=2000,
        temperature=0.1,
    )
    return response


JUDGE_EVAL_SYSTEM = """You are a meta-evaluator assessing the quality of a review.

You will be given:
1. The original paper text (with known injected defects)
2. The list of expected issues that SHOULD be found
3. The actual review output from the system being evaluated

Your job: Score how well the review identified the expected issues.

{judge_prompt}

IMPORTANT: Your output MUST be valid JSON matching the format specified above.
Do not include any text before or after the JSON object."""


async def run_judge(
    case: BenchmarkCase,
    review_output: str,
    judge_type: str = "review",
) -> Dict:
    """Use LLM to judge the quality of a review output."""
    from llm.client import LLMClient

    client = LLMClient(max_concurrent=1)
    judge_prompt = load_judge_prompt(judge_type)

    system = JUDGE_EVAL_SYSTEM.format(judge_prompt=judge_prompt)

    user_msg = f"""## Original Paper Text (with known defects):
{case.input_text[:3000]}

## Expected Issues (ground truth):
{json.dumps(case.expected_issues, ensure_ascii=False)}

## Gold Verdict: {case.gold_verdict}

## System's Review Output:
{review_output[:3000]}

Now score the review quality using the dimensions defined above. Output ONLY valid JSON."""

    response = await client.chat(
        system=system,
        user=user_msg,
        max_tokens=1000,
        temperature=0.0,
    )

    # Parse JSON response
    response = response.strip()
    if response.startswith("```"):
        # Remove markdown code fences
        lines = response.split("\n")
        response = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
    if response.startswith("json"):
        response = response[4:].strip()

    try:
        return json.loads(response)
    except json.JSONDecodeError:
        # Try to find JSON object in the response
        import re
        match = re.search(r"\{[^}]*\"composite_score\"[^}]*\}", response, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        return {"parse_error": True, "raw": response[:500]}


async def evaluate_llm_case(case: BenchmarkCase) -> JudgeScore:
    """Evaluate an L2-L4 case using mini_review + LLM judge."""
    
    # Step 1: Run mini review
    depth = "deep" if case.level in ("L3", "L4") else "standard"
    try:
        review_output = await run_mini_review(case.input_text, depth=depth)
    except Exception as e:
        return JudgeScore(
            case_id=case.id,
            judge_type="review",
            composite_score=0.0,
            rationale=f"mini_review failed: {e}",
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
        )

    # Rate limit before judge call
    await asyncio.sleep(LLM_DELAY)

    # Step 2: Run judge
    try:
        judge_result = await run_judge(case, review_output, judge_type="review")
    except Exception as e:
        return JudgeScore(
            case_id=case.id,
            judge_type="review",
            composite_score=0.0,
            rationale=f"judge failed: {e}",
            raw_tool_output=review_output[:1000],
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
        )

    # Step 3: Extract scores from judge result
    if judge_result.get("parse_error"):
        return JudgeScore(
            case_id=case.id,
            judge_type="review",
            composite_score=0.0,
            rationale=f"Judge output parse error. Raw: {judge_result.get('raw', '')[:200]}",
            raw_tool_output=review_output[:1000],
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
        )

    # Map judge output to JudgeScore
    dimension_scores = {}
    for key, val in judge_result.items():
        if key.startswith("D") and isinstance(val, (int, float)):
            dimension_scores[key] = float(val)
        elif "_" in key and isinstance(val, (int, float)) and key != "composite_score":
            dimension_scores[key] = float(val)

    composite = judge_result.get("composite_score", 0.0)
    if not composite and dimension_scores:
        composite = sum(dimension_scores.values()) / len(dimension_scores)

    return JudgeScore(
        case_id=case.id,
        judge_type="review",
        dimension_scores=dimension_scores,
        composite_score=round(float(composite), 2),
        rationale=judge_result.get("rationale", ""),
        raw_tool_output=review_output[:1500],
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
    )


# ============================================================
# Main Evaluation Pipeline
# ============================================================

async def run_evaluation(level: Optional[str] = None, judge_type: str = "review") -> EvalReport:
    """Run full evaluation pipeline for specified level(s)."""
    cases = load_benchmarks(level)
    if not cases:
        print(f"No benchmark cases found for level={level}.")
        return generate_report([], level or "all")

    print(f"\n{'='*60}")
    print(f"  Running Eval: {len(cases)} cases | Level: {level or 'all'}")
    print(f"{'='*60}")

    scores: List[JudgeScore] = []

    for i, case in enumerate(cases):
        print(f"\n  [{i+1}/{len(cases)}] {case.id} (L{case.level[-1]}, {case.difficulty})...")
        t0 = time.time()

        try:
            if case.level == "L1":
                # Zero-cost rule-based evaluation
                score = evaluate_l1_case(case)
            else:
                # LLM-based evaluation
                score = await evaluate_llm_case(case)
                # Rate limit between LLM cases
                if i < len(cases) - 1 and cases[i + 1].level != "L1":
                    print(f"    [Rate limit] Waiting {LLM_DELAY}s...")
                    await asyncio.sleep(LLM_DELAY)
        except Exception as e:
            score = JudgeScore(
                case_id=case.id,
                judge_type=judge_type,
                composite_score=0.0,
                rationale=f"Error: {traceback.format_exc()[-200:]}",
                timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
            )

        dur = time.time() - t0
        icon = "✅" if score.composite_score >= 3.5 else "⚠️" if score.composite_score >= 2.0 else "❌"
        print(f"    {icon} Score: {score.composite_score:.2f}/5.00 ({dur:.1f}s)")
        if score.rationale:
            print(f"    {score.rationale[:200]}")

        scores.append(score)

    # Generate and save report
    report = generate_report(scores, level or "all")
    save_report(report)

    # Print summary
    print(f"\n{'='*60}")
    print(format_report_summary(report))
    print(f"{'='*60}")

    return report


# ============================================================
# Report Generation
# ============================================================

def generate_report(scores: List[JudgeScore], level: str) -> EvalReport:
    """Generate aggregate report from individual judge scores."""
    if not scores:
        return EvalReport(
            run_id=f"eval_{int(time.time())}",
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
            level=level,
            total_cases=0,
        )

    # Aggregate dimension scores
    all_dimensions: Dict[str, List[float]] = {}
    composites = []

    for score in scores:
        composites.append(score.composite_score)
        for dim, val in score.dimension_scores.items():
            all_dimensions.setdefault(dim, []).append(val)

    avg_composite = sum(composites) / len(composites) if composites else 0.0
    dimension_averages = {
        dim: sum(vals) / len(vals)
        for dim, vals in all_dimensions.items()
    }

    # Pass rate: composite >= 3.5 out of 5.0
    pass_threshold = 3.5
    pass_count = sum(1 for c in composites if c >= pass_threshold)
    pass_rate = pass_count / len(composites) if composites else 0.0

    return EvalReport(
        run_id=f"eval_{int(time.time())}",
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
        level=level,
        total_cases=len(scores),
        scores=scores,
        avg_composite=round(avg_composite, 3),
        dimension_averages={k: round(v, 3) for k, v in dimension_averages.items()},
        pass_rate=round(pass_rate, 3),
    )


def save_report(report: EvalReport):
    """Save report to eval/reports/ directory."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / f"{report.run_id}.json"
    report_path.write_text(
        json.dumps(asdict(report), ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    print(f"\nReport saved: {report_path}")


def format_report_summary(report: EvalReport) -> str:
    """Format report as human-readable summary."""
    lines = [
        f"  Eval Report: {report.run_id}",
        f"  Level: {report.level} | Cases: {report.total_cases}",
        f"  Timestamp: {report.timestamp}",
        "",
        f"  Average Composite: {report.avg_composite:.2f} / 5.00",
        f"  Pass Rate (>= 3.5): {report.pass_rate*100:.0f}%",
        "",
        "  Dimension Averages:",
    ]

    for dim, avg in sorted(report.dimension_averages.items()):
        bar_len = int(avg * 4)  # 5 -> 20 chars
        bar = "█" * bar_len + "░" * (20 - bar_len)
        lines.append(f"    {dim:25s} [{bar}] {avg:.2f}")

    # Per-case breakdown
    lines.append("")
    lines.append("  Per-case scores:")
    for score in report.scores:
        icon = "✅" if score.composite_score >= 3.5 else "⚠️" if score.composite_score >= 2.0 else "❌"
        lines.append(f"    {icon} {score.case_id}: {score.composite_score:.2f}")

    return "\n".join(lines)


# ============================================================
# CLI Entry Point
# ============================================================

def main():
    """CLI entry point for running evaluations."""
    import argparse

    parser = argparse.ArgumentParser(description="Scholar-Agent Evaluation Runner")
    parser.add_argument("--level", default="all", help="Benchmark level (L1/L2/L3/L4/all)")
    parser.add_argument("--judge", default="review", help="Judge type (review/rewrite/deai/search)")
    parser.add_argument("--report", action="store_true", help="Show last report only")
    parser.add_argument("--dry-run", action="store_true", help="List cases without running")
    args = parser.parse_args()

    level = None if args.level == "all" else args.level
    cases = load_benchmarks(level)

    if args.dry_run:
        print(f"Found {len(cases)} benchmark cases:")
        for case in cases:
            print(f"  [{case.level}] {case.id} ({case.difficulty}) — tool: {case.tool or 'auto'}")
        return

    if args.report:
        # Show latest report
        if REPORTS_DIR.exists():
            reports = sorted(REPORTS_DIR.glob("*.json"))
            if reports:
                data = json.loads(reports[-1].read_text())
                print(f"Latest report: {reports[-1].name}")
                print(json.dumps(data, indent=2, ensure_ascii=False)[:3000])
            else:
                print("No reports found.")
        else:
            print("No reports directory.")
        return

    if not cases:
        print(f"No benchmark cases found for level={args.level}.")
        print(f"Add JSON case files to {BENCHMARKS_DIR}/L*_*/")
        return

    # Run evaluation
    asyncio.run(run_evaluation(level, args.judge))


if __name__ == "__main__":
    main()
