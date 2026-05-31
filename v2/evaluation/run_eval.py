#!/usr/bin/env python3
"""
evaluation/run_eval.py — ScholarAgent Evaluation Runner.

Runs the agent against gold-standard papers and computes precision/recall/F1.

Usage:
    # Mock mode (uses deterministic mock responses, no API key needed):
    python3 -m evaluation.run_eval --mode mock

    # Real mode (requires API key, runs actual agent):
    python3 -m evaluation.run_eval --mode real

    # Compare V3 vs V2 (kill switches toggled):
    python3 -m evaluation.run_eval --mode mock --compare

    # Single paper:
    python3 -m evaluation.run_eval --mode mock --paper paper_001

Output:
    - Console summary table
    - Detailed report in evaluation/reports/eval_<timestamp>.md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from datetime import datetime
from typing import Any

# 确保 stderr 不被缓冲（解决管道模式下无实时输出的问题）
if not os.environ.get("PYTHONUNBUFFERED"):
    # 让 stderr 行缓冲，确保 verbose 输出实时可见
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(line_buffering=True)
    else:
        import io
        sys.stderr = io.TextIOWrapper(
            sys.stderr.buffer, line_buffering=True, write_through=True
        )

# Ensure v2/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Load .env for API keys
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from evaluation.metrics import (
    Finding,
    EvalMetrics,
    AggregateMetrics,
    compute_aggregate,
)
from evaluation.llm_judge import compute_metrics_llm


# ============================================================
# Gold Standard Loading
# ============================================================

GOLD_DIR = Path(__file__).parent / "gold_standard"


def load_gold_standard(paper_id: str | None = None) -> list[dict]:
    """Load gold standard data files.

    Args:
        paper_id: If specified, load only this paper. Otherwise load all.

    Returns:
        List of gold standard dicts (each with paper_id, findings, etc.)
    """
    papers = []
    for f in sorted(GOLD_DIR.glob("*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        # Skip files without 'findings' key (old annotation format)
        if "findings" not in data:
            continue
        if paper_id and data["paper_id"] != paper_id:
            continue
        papers.append(data)

    if paper_id and not papers:
        print(f"Error: Paper '{paper_id}' not found in {GOLD_DIR}", file=sys.stderr)
        sys.exit(1)

    return papers


def gold_to_findings(gold_data: dict) -> list[Finding]:
    """Convert gold standard JSON to Finding objects."""
    return [
        Finding(
            text=f["text"],
            section=f.get("section", ""),
            priority=f.get("priority", "medium"),
            category=f.get("category", ""),
        )
        for f in gold_data["findings"]
    ]


# ============================================================
# Mock Agent (deterministic, for CI)
# ============================================================

def run_mock_agent(gold_data: dict, v3_enabled: bool = True) -> list[Finding]:
    """Simulate agent findings using partial gold data + noise.

    Strategy:
    - Returns ~70% of gold findings (simulating recall < 1.0)
    - Adds 1-2 "false positive" findings (simulating precision < 1.0)
    - Slightly paraphrases matched findings to test fuzzy matching
    - When v3_enabled=False, returns fewer findings (simulating V2 baseline)

    This provides a realistic evaluation scenario without needing API calls.
    """
    import random
    random.seed(42 + hash(gold_data["paper_id"]))

    gold_findings = gold_data["findings"]

    # Decide how many gold findings to "discover"
    if v3_enabled:
        discover_rate = 0.7  # V3 finds ~70% of issues
    else:
        discover_rate = 0.5  # V2 baseline finds ~50%

    discovered_indices = random.sample(
        range(len(gold_findings)),
        k=max(1, int(len(gold_findings) * discover_rate)),
    )

    predicted = []
    for idx in sorted(discovered_indices):
        gf = gold_findings[idx]
        # Paraphrase slightly (swap some words, keep core meaning)
        text = _paraphrase(gf["text"], random)
        predicted.append(Finding(
            text=text,
            section=gf.get("section", ""),
            priority=gf.get("priority", "medium"),
            category=gf.get("category", ""),
        ))

    # Add false positives
    num_fp = random.randint(1, 2)
    fp_templates = [
        "The writing style in the {section} section could be more concise in several paragraphs.",
        "Minor formatting inconsistencies in tables throughout the paper.",
        "The abstract could better highlight the contribution relative to existing work.",
    ]
    for i in range(num_fp):
        sections = gold_data.get("sections", ["introduction"])
        sec = random.choice(sections)
        predicted.append(Finding(
            text=fp_templates[i % len(fp_templates)].format(section=sec),
            section=sec,
            priority="low",
            category="presentation",
        ))

    return predicted


def _paraphrase(text: str, rng: Any) -> str:
    """Slightly modify text to simulate imperfect agent output."""
    # Simple paraphrase: occasionally swap word order or add filler
    words = text.split()
    if len(words) > 10 and rng.random() < 0.3:
        # Swap two adjacent words
        idx = rng.randint(2, len(words) - 3)
        words[idx], words[idx + 1] = words[idx + 1], words[idx]
    if rng.random() < 0.2:
        # Add a qualifier
        words.insert(0, "Notably,")
    return " ".join(words)


# ============================================================
# Real Agent (requires API key)
# ============================================================

# Directory for test paper files (PDF/MD)
TEST_PAPERS_DIR = Path(__file__).parent / "test_papers"


def _find_paper_file(gold_data: dict) -> Path | None:
    """Locate the actual paper file for a gold standard entry.

    Search order:
    1. gold_data["paper_path"] (explicit path in JSON)
    2. test_papers/<paper_id>.pdf
    3. test_papers/<paper_id>.md
    """
    # 1. Explicit path
    if "paper_path" in gold_data and gold_data["paper_path"]:
        p = Path(gold_data["paper_path"])
        if not p.is_absolute():
            p = Path(__file__).parent / p
        if p.exists():
            return p

    # 2. Convention-based lookup
    paper_id = gold_data["paper_id"]
    for ext in (".pdf", ".md"):
        candidate = TEST_PAPERS_DIR / f"{paper_id}{ext}"
        if candidate.exists():
            return candidate

    return None


def run_real_agent(gold_data: dict, v3_enabled: bool = True, verbose: bool = True) -> list[Finding]:
    """Run the actual ScholarAgent on a paper.

    Requires:
    - OPENAI_API_KEY or equivalent set in environment
    - Paper file available (PDF/MD) in evaluation/test_papers/

    The function drives a full ScholarAgent session:
    1. Locate paper file
    2. Initialize ScholarAgent with the paper
    3. Run the cognitive loop (agent.start())
    4. Extract findings and convert to Finding objects
    5. End session (with reflection for evolution pipeline)
    """
    import asyncio

    paper_id = gold_data["paper_id"]
    print(f"  [Real mode] Running agent on {paper_id}...")

    # Find paper file
    paper_file = _find_paper_file(gold_data)
    if paper_file is None:
        print(f"  [Real mode] ERROR: No paper file found for {paper_id}.", file=sys.stderr)
        print(f"  [Real mode] Expected at: {TEST_PAPERS_DIR}/{paper_id}.pdf or .md", file=sys.stderr)
        print(f"  [Real mode] Falling back to mock agent.", file=sys.stderr)
        return run_mock_agent(gold_data, v3_enabled)

    print(f"  [Real mode] Paper file: {paper_file}")

    # Import ScholarAgent (deferred to avoid circular imports at module load)
    from core.agent import ScholarAgent

    # Determine model (use LLM_MODEL env or default gpt-4.1)
    model = os.environ.get("LLM_MODEL", "gpt-4.1")

    # Create isolated memory directory for evaluation (avoid polluting user data)
    eval_memory_dir = Path(__file__).parent / "eval_memory" / paper_id
    eval_memory_dir.mkdir(parents=True, exist_ok=True)

    # Initialize agent
    agent = ScholarAgent(
        paper_path=str(paper_file),
        model=model,
        verbose=verbose,  # Controlled by --verbose flag
        max_loop_turns=40,  # Allow enough turns for thorough review
        token_budget=0,  # Unlimited mode — let max_loop_turns be the throttle
        context_window=128_000,
        persona="scholar",
        enable_hdwm=v3_enabled,  # V3 uses HD-WM for hypothesis-driven review
    )

    # Override memory directory to isolated eval location
    agent.harness._memory_dir = eval_memory_dir
    agent.harness.memory = None  # Will be re-initialized on load
    from core.memory import MemoryStore
    agent.harness.memory = MemoryStore(eval_memory_dir)
    agent.harness.memory.load()

    # Run the agent (synchronous wrapper around async session)
    try:
        result = asyncio.run(_run_agent_session(agent))
    except Exception as e:
        print(f"  [Real mode] ERROR during agent run: {type(e).__name__}: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        return []

    # Extract findings from agent state
    raw_findings = agent.get_findings()
    print(f"  [Real mode] Agent produced {len(raw_findings)} findings")

    # Convert agent findings to evaluation Finding objects
    predicted = []
    for f in raw_findings:
        predicted.append(Finding(
            text=f.get("finding", f.get("text", "")),
            section=f.get("section", ""),
            priority=f.get("priority", "medium"),
            category=f.get("category", ""),
        ))

    return predicted


async def _run_agent_session(agent) -> str:
    """Run a full agent session (start + end with reflection).

    Returns the agent's output text.
    """
    # 安装 asyncio exception handler 以抑制 GC 阶段 "Event loop is closed" 噪声。
    # 这类警告来自 httpx AsyncClient 的 __del__（Python 3.9 + httpx 已知行为），
    # 不影响功能正确性，但会污染 stderr。
    # NOTE: 同一逻辑也存在于 main.py:_install_event_loop_closed_filter，如需修改请同步。
    loop = asyncio.get_running_loop()
    _original_handler = loop.get_exception_handler()

    def _suppress_event_loop_closed(loop, context):
        exc = context.get("exception")
        if isinstance(exc, RuntimeError) and "Event loop is closed" in str(exc):
            return  # 静默忽略
        if _original_handler:
            _original_handler(loop, context)
        else:
            loop.default_exception_handler(context)

    loop.set_exception_handler(_suppress_event_loop_closed)

    # Start the agent — it will autonomously review the paper
    output = await agent.start(
        user_intent="请仔细审阅这篇论文，找出所有方法论、数据、逻辑、引用和写作方面的问题。"
    )

    # End session with reflection (triggers evolution pipeline)
    try:
        await agent.end_session_with_reflection()
    except Exception as e:
        # Reflection failure shouldn't block evaluation
        print(f"  [Real mode] Warning: reflection failed: {e}", file=sys.stderr)
        agent.end_session()

    # 显式关闭 LLM client，避免 GC 时触发 "Event loop is closed" 警告
    try:
        if hasattr(agent, "client") and hasattr(agent.client, "close"):
            await agent.client.close()
    except Exception:
        pass

    return output


# ============================================================
# Report Generation
# ============================================================

def generate_report(
    aggregate: AggregateMetrics,
    gold_papers: list[dict],
    mode: str,
    compare_aggregate: AggregateMetrics | None = None,
) -> str:
    """Generate a markdown evaluation report."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"# ScholarAgent Evaluation Report",
        f"",
        f"**Generated**: {timestamp}",
        f"**Mode**: {mode}",
        f"**Papers evaluated**: {aggregate.num_papers}",
        f"",
        f"---",
        f"",
        f"## Aggregate Metrics",
        f"",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Precision | {aggregate.avg_precision:.4f} |",
        f"| Recall | {aggregate.avg_recall:.4f} |",
        f"| F1 | {aggregate.avg_f1:.4f} |",
        f"| Weighted Recall (high/critical 2x) | {aggregate.avg_weighted_recall:.4f} |",
        f"| Total Predicted | {aggregate.total_predicted} |",
        f"| Total Gold | {aggregate.total_gold} |",
        f"| Total Matched | {aggregate.total_matched} |",
        f"",
    ]

    if compare_aggregate:
        lines.extend([
            f"## V3 vs V2 Comparison",
            f"",
            f"| Metric | V3 | V2 | Delta |",
            f"|--------|----|----|-------|",
            f"| Precision | {aggregate.avg_precision:.4f} | {compare_aggregate.avg_precision:.4f} | {aggregate.avg_precision - compare_aggregate.avg_precision:+.4f} |",
            f"| Recall | {aggregate.avg_recall:.4f} | {compare_aggregate.avg_recall:.4f} | {aggregate.avg_recall - compare_aggregate.avg_recall:+.4f} |",
            f"| F1 | {aggregate.avg_f1:.4f} | {compare_aggregate.avg_f1:.4f} | {aggregate.avg_f1 - compare_aggregate.avg_f1:+.4f} |",
            f"| Weighted Recall | {aggregate.avg_weighted_recall:.4f} | {compare_aggregate.avg_weighted_recall:.4f} | {aggregate.avg_weighted_recall - compare_aggregate.avg_weighted_recall:+.4f} |",
            f"",
        ])

    lines.append("## Per-Paper Results\n")

    for metrics in aggregate.per_paper:
        paper_data = next((p for p in gold_papers if p["paper_id"] == metrics.paper_id), None)
        title = paper_data.get("title", metrics.paper_id) if paper_data else metrics.paper_id

        lines.extend([
            f"### {metrics.paper_id}: {title}",
            f"",
            f"- Precision: {metrics.precision:.4f} ({metrics.num_matched}/{metrics.num_predicted})",
            f"- Recall: {metrics.recall:.4f} ({metrics.num_matched}/{metrics.num_gold})",
            f"- F1: {metrics.f1:.4f}",
            f"- Weighted Recall: {metrics.weighted_recall:.4f}",
            f"",
        ])

        if metrics.unmatched_gold and paper_data:
            lines.append("**Missed findings (false negatives):**\n")
            gold_findings_list = paper_data.get("findings", [])
            for idx in metrics.unmatched_gold:
                if idx < len(gold_findings_list):
                    gf = gold_findings_list[idx]
                    lines.append(f"- [{gf.get('priority', '?')}] [{gf.get('category', '?')}] {gf.get('text', '')[:120]}...")
                else:
                    lines.append(f"- [index {idx} out of range]")
            lines.append("")

        if metrics.category_breakdown:
            lines.append("**Category breakdown:**\n")
            lines.append("| Category | P | R | F1 | #Pred | #Gold |")
            lines.append("|----------|---|---|----|----|-----|")
            for cat, vals in sorted(metrics.category_breakdown.items()):
                lines.append(
                    f"| {cat} | {vals['precision']:.2f} | {vals['recall']:.2f} | "
                    f"{vals['f1']:.2f} | {vals['num_predicted']} | {vals['num_gold']} |"
                )
            lines.append("")

    return "\n".join(lines)


# ============================================================
# Main
# ============================================================

async def _eval_with_llm_judge(
    paper_id: str,
    predicted: list[Finding],
    gold_data: dict,
) -> EvalMetrics:
    """使用 LLM-as-Judge 语义匹配计算单篇论文的 P/R/F1。"""
    # 将 Finding 对象转为 llm_judge 接受的 dict 格式
    pred_dicts = [{"finding": f.text, "section": f.section, "priority": f.priority, "category": f.category} for f in predicted]
    gold_findings_raw = gold_data.get("findings", [])
    paper_title = gold_data.get("title", paper_id)

    result = await compute_metrics_llm(
        paper_id=paper_id,
        predicted_findings=pred_dicts,
        gold_findings=gold_findings_raw,
        paper_title=paper_title,
    )

    # 适配为 EvalMetrics dataclass
    return EvalMetrics(
        paper_id=paper_id,
        precision=result["precision"],
        recall=result["recall"],
        f1=result["f1"],
        weighted_recall=result["weighted_recall"],
        num_predicted=result["num_predicted"],
        num_gold=result["num_gold"],
        num_matched=result["num_matched"],
        matches=[],
        unmatched_predicted=[],
        unmatched_gold=[u["idx"] for u in result.get("unmatched_gold", [])],
        category_breakdown={},
    )


async def main():
    parser = argparse.ArgumentParser(description="ScholarAgent Evaluation Runner")
    parser.add_argument("--mode", choices=["mock", "real"], default="mock",
                       help="Mock mode (deterministic) or real mode (requires API key)")
    parser.add_argument("--paper", type=str, default=None,
                       help="Evaluate a single paper by ID (e.g., paper_001)")
    parser.add_argument("--compare", action="store_true",
                       help="Run V3 vs V2 comparison")
    parser.add_argument("--output", type=str, default=None,
                       help="Output report path (default: evaluation/reports/eval_<timestamp>.md)")
    parser.add_argument("--verbose", action="store_true", default=True,
                       help="Enable verbose output from cognitive loop (default: True)")
    parser.add_argument("--quiet", action="store_true", default=False,
                       help="Suppress verbose output from cognitive loop")
    args = parser.parse_args()
    # --quiet overrides --verbose
    verbose = not args.quiet

    print(f"\n{'='*60}")
    print(f"  ScholarAgent Evaluation Framework")
    print(f"  Mode: {args.mode} | Judge: LLM-as-Judge (semantic matching)")
    print(f"{'='*60}\n")

    # Load gold standard
    gold_papers = load_gold_standard(args.paper)
    print(f"Loaded {len(gold_papers)} gold-standard paper(s)\n")

    # Run agent
    if args.mode == "mock":
        agent_fn = lambda gd, v3_enabled: run_mock_agent(gd, v3_enabled)
    else:
        agent_fn = lambda gd, v3_enabled: run_real_agent(gd, v3_enabled=v3_enabled, verbose=verbose)

    # V3 evaluation
    print("--- V3 (all features enabled) ---")
    v3_metrics = []
    for paper in gold_papers:
        gold_findings = gold_to_findings(paper)
        predicted = agent_fn(paper, v3_enabled=True)
        print(f"  {paper['paper_id']}: Running LLM-as-Judge matching ({len(predicted)} pred vs {len(gold_findings)} gold)...")
        metrics = await _eval_with_llm_judge(paper["paper_id"], predicted, paper)
        v3_metrics.append(metrics)
        print(f"  {paper['paper_id']}: P={metrics.precision:.3f} R={metrics.recall:.3f} F1={metrics.f1:.3f} "
              f"({metrics.num_matched}/{metrics.num_gold} matched)")

    v3_aggregate = compute_aggregate(v3_metrics)
    print(f"\n  Aggregate: P={v3_aggregate.avg_precision:.3f} R={v3_aggregate.avg_recall:.3f} "
          f"F1={v3_aggregate.avg_f1:.3f}\n")

    # V2 comparison (optional)
    v2_aggregate = None
    if args.compare:
        print("--- V2 (kill switches off) ---")
        v2_metrics = []
        for paper in gold_papers:
            gold_findings = gold_to_findings(paper)
            predicted = agent_fn(paper, v3_enabled=False)
            print(f"  {paper['paper_id']}: Running LLM-as-Judge matching...")
            metrics = await _eval_with_llm_judge(paper["paper_id"], predicted, paper)
            v2_metrics.append(metrics)
            print(f"  {paper['paper_id']}: P={metrics.precision:.3f} R={metrics.recall:.3f} F1={metrics.f1:.3f} "
                  f"({metrics.num_matched}/{metrics.num_gold} matched)")

        v2_aggregate = compute_aggregate(v2_metrics)
        print(f"\n  Aggregate: P={v2_aggregate.avg_precision:.3f} R={v2_aggregate.avg_recall:.3f} "
              f"F1={v2_aggregate.avg_f1:.3f}\n")

        # Delta summary
        print("--- V3 vs V2 Delta ---")
        print(f"  ΔPrecision:  {v3_aggregate.avg_precision - v2_aggregate.avg_precision:+.4f}")
        print(f"  ΔRecall:     {v3_aggregate.avg_recall - v2_aggregate.avg_recall:+.4f}")
        print(f"  ΔF1:         {v3_aggregate.avg_f1 - v2_aggregate.avg_f1:+.4f}")
        print(f"  ΔWtd Recall: {v3_aggregate.avg_weighted_recall - v2_aggregate.avg_weighted_recall:+.4f}")
        print()

    # Generate report
    report = generate_report(v3_aggregate, gold_papers, args.mode, v2_aggregate)

    # Save report
    reports_dir = Path(__file__).parent / "reports"
    reports_dir.mkdir(exist_ok=True)
    if args.output:
        report_path = Path(args.output)
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path = reports_dir / f"eval_{timestamp}.md"

    report_path.write_text(report, encoding="utf-8")
    print(f"Report saved to: {report_path}")

    return v3_aggregate


if __name__ == "__main__":
    asyncio.run(main())
