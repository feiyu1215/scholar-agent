#!/usr/bin/env python3
"""
evaluation/run_eval_llm_judge.py — 使用 LLM-as-Judge 的评估运行器。

与 run_eval.py 的区别:
    - 匹配逻辑从 Jaccard token overlap → LLM 语义判断
    - 支持跨语言匹配（agent 中文输出 vs 英文 gold standard）
    - 输出详细的匹配解释

Usage:
    # Real mode (runs actual agent + LLM judge):
    python3 -m evaluation.run_eval_llm_judge --paper paper_001

    # Judge-only mode (使用已有的 agent findings，只重新做匹配):
    python3 -m evaluation.run_eval_llm_judge --paper paper_001 --judge-only

    # Both papers:
    python3 -m evaluation.run_eval_llm_judge --paper paper_001 --paper paper_003
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Ensure v2/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluation.llm_judge import compute_metrics_llm


# ============================================================
# Gold Standard & Agent Findings Loading
# ============================================================

GOLD_DIR = Path(__file__).parent / "gold_standard"
EVAL_MEMORY_DIR = Path(__file__).parent / "eval_memory"
TEST_PAPERS_DIR = Path(__file__).parent / "test_papers"


def load_gold(paper_id: str) -> dict:
    """Load gold standard for a paper."""
    path = GOLD_DIR / f"{paper_id}.json"
    if not path.exists():
        print(f"Error: Gold standard not found: {path}", file=sys.stderr)
        sys.exit(1)
    data = json.loads(path.read_text(encoding="utf-8"))
    return data


def load_agent_findings(paper_id: str) -> list[dict]:
    """Load agent findings from eval_memory (last session)."""
    memory_file = EVAL_MEMORY_DIR / paper_id / "memory.json"
    if not memory_file.exists():
        return []

    data = json.loads(memory_file.read_text(encoding="utf-8"))
    sessions = data.get("sessions", [])
    if not sessions:
        return []

    # 从最近的 session 的 findings_summary 提取
    last_session = sessions[-1]
    findings_summary = last_session.get("findings_summary", [])

    # findings_summary 是字符串列表，格式为 "[priority] [category] description"
    findings = []
    for item in findings_summary:
        if isinstance(item, str):
            findings.append({"finding": item, "text": item})
        elif isinstance(item, dict):
            findings.append(item)

    return findings


def run_agent_and_get_findings(paper_id: str, gold_data: dict) -> list[dict]:
    """运行 Agent 审稿并返回 findings（同步包装）。"""
    from evaluation.run_eval import _find_paper_file, _run_agent_session

    paper_file = _find_paper_file(gold_data)
    if paper_file is None:
        print(f"  ERROR: No paper file for {paper_id}", file=sys.stderr)
        return []

    print(f"  Running agent on {paper_file.name}...")

    from core.agent import ScholarAgent
    from core.memory import MemoryStore

    model = os.environ.get("LLM_MODEL", "gpt-4.1")
    eval_memory_dir = EVAL_MEMORY_DIR / paper_id
    eval_memory_dir.mkdir(parents=True, exist_ok=True)

    agent = ScholarAgent(
        paper_path=str(paper_file),
        model=model,
        verbose=False,
        max_loop_turns=40,
        token_budget=0,  # Unlimited mode — let max_loop_turns be the throttle
        context_window=128_000,
        persona="scholar",
        enable_hdwm=True,
    )

    # Override memory
    agent.harness._memory_dir = eval_memory_dir
    agent.harness.memory = None
    agent.harness.memory = MemoryStore(eval_memory_dir)
    agent.harness.memory.load()

    try:
        asyncio.run(_run_agent_session(agent))
    except Exception as e:
        print(f"  ERROR during agent run: {e}", file=sys.stderr)
        return []

    raw_findings = agent.get_findings()
    print(f"  Agent produced {len(raw_findings)} findings")
    return raw_findings


# ============================================================
# Main Evaluation
# ============================================================

async def evaluate_paper(
    paper_id: str,
    judge_only: bool = False,
) -> dict:
    """评估单篇论文。

    Args:
        paper_id: 论文 ID
        judge_only: True = 使用已有的 agent findings，不重新运行 agent

    Returns:
        评估结果 dict
    """
    print(f"\n{'='*50}")
    print(f"  Evaluating: {paper_id}")
    print(f"{'='*50}")

    # Load gold
    gold_data = load_gold(paper_id)
    gold_findings = gold_data.get("findings", gold_data.get("gold_findings", []))
    paper_title = gold_data.get("title", paper_id)

    print(f"  Gold standard: {len(gold_findings)} findings")
    print(f"  Paper: {paper_title}")

    # Get agent findings
    if judge_only:
        print(f"  [Judge-only mode] Loading existing agent findings...")
        agent_findings = load_agent_findings(paper_id)
        if not agent_findings:
            print(f"  WARNING: No existing findings found. Run without --judge-only first.")
            return {"paper_id": paper_id, "error": "No agent findings found"}
    else:
        agent_findings = run_agent_and_get_findings(paper_id, gold_data)

    print(f"  Agent findings: {len(agent_findings)}")

    if not agent_findings:
        return {
            "paper_id": paper_id,
            "precision": 0.0, "recall": 0.0, "f1": 0.0,
            "num_predicted": 0, "num_gold": len(gold_findings),
            "num_matched": 0, "error": "No agent findings",
        }

    # LLM Judge
    print(f"  Running LLM-as-Judge matching...")
    start_time = time.time()

    result = await compute_metrics_llm(
        paper_id=paper_id,
        predicted_findings=agent_findings,
        gold_findings=gold_findings,
        paper_title=paper_title,
    )

    judge_time = time.time() - start_time
    result["judge_time_seconds"] = round(judge_time, 1)

    # Print results
    print(f"\n  --- Results ({judge_time:.1f}s) ---")
    print(f"  Precision:  {result['precision']:.4f} ({result['num_matched']}/{result['num_predicted']})")
    print(f"  Recall:     {result['recall']:.4f} ({result['num_matched']}/{result['num_gold']})")
    print(f"  F1:         {result['f1']:.4f}")
    print(f"  Wtd Recall: {result['weighted_recall']:.4f}")

    if result.get("matches"):
        print(f"\n  Matched pairs:")
        for m in result["matches"]:
            print(f"    [conf={m['confidence']:.2f}] pred[{m['predicted_idx']}] ↔ gold[{m['gold_idx']}]")
            print(f"      Pred: {m['predicted_text'][:80]}...")
            print(f"      Gold: {m['gold_text'][:80]}...")
            print(f"      Why:  {m['reason']}")

    if result.get("unmatched_gold"):
        print(f"\n  Missed gold findings (false negatives):")
        for ug in result["unmatched_gold"]:
            print(f"    gold[{ug['idx']}]: {ug['text'][:100]}...")

    if result.get("unmatched_predicted"):
        print(f"\n  Extra predicted findings (false positives):")
        for up in result["unmatched_predicted"]:
            print(f"    pred[{up['idx']}]: {up['text'][:100]}...")

    return result


async def main():
    parser = argparse.ArgumentParser(description="ScholarAgent Eval with LLM-as-Judge")
    parser.add_argument("--paper", action="append", dest="papers",
                       help="Paper ID(s) to evaluate (can be specified multiple times)")
    parser.add_argument("--judge-only", action="store_true",
                       help="Only run LLM judge on existing findings (don't re-run agent)")
    parser.add_argument("--output", type=str, default=None,
                       help="Output report path")
    args = parser.parse_args()

    if not args.papers:
        args.papers = ["paper_001", "paper_003"]

    print(f"\n{'#'*60}")
    print(f"  ScholarAgent Evaluation — LLM-as-Judge Mode")
    print(f"  Papers: {', '.join(args.papers)}")
    print(f"  Judge model: {os.environ.get('EVAL_JUDGE_MODEL', 'gpt-4.1-mini')}")
    print(f"  Judge-only: {args.judge_only}")
    print(f"{'#'*60}")

    # Evaluate each paper
    all_results = []
    for paper_id in args.papers:
        result = await evaluate_paper(paper_id, judge_only=args.judge_only)
        all_results.append(result)

    # Aggregate
    valid_results = [r for r in all_results if "error" not in r]
    if valid_results:
        avg_p = sum(r["precision"] for r in valid_results) / len(valid_results)
        avg_r = sum(r["recall"] for r in valid_results) / len(valid_results)
        avg_f1 = sum(r["f1"] for r in valid_results) / len(valid_results)
        avg_wr = sum(r["weighted_recall"] for r in valid_results) / len(valid_results)

        print(f"\n{'='*60}")
        print(f"  AGGREGATE ({len(valid_results)} papers)")
        print(f"{'='*60}")
        print(f"  Avg Precision:       {avg_p:.4f}")
        print(f"  Avg Recall:          {avg_r:.4f}")
        print(f"  Avg F1:              {avg_f1:.4f}")
        print(f"  Avg Weighted Recall: {avg_wr:.4f}")
        print()

    # Save report
    reports_dir = Path(__file__).parent / "reports"
    reports_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    if args.output:
        report_path = Path(args.output)
    else:
        report_path = reports_dir / f"eval_llm_judge_{timestamp}.json"

    report_data = {
        "timestamp": datetime.now().isoformat(),
        "mode": "judge-only" if args.judge_only else "full",
        "judge_model": JUDGE_MODEL,
        "papers": args.papers,
        "results": all_results,
        "aggregate": {
            "avg_precision": round(avg_p, 4) if valid_results else 0,
            "avg_recall": round(avg_r, 4) if valid_results else 0,
            "avg_f1": round(avg_f1, 4) if valid_results else 0,
            "avg_weighted_recall": round(avg_wr, 4) if valid_results else 0,
        } if valid_results else {},
    }

    report_path.write_text(json.dumps(report_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  Report saved to: {report_path}")


# Import the JUDGE_MODEL at module level for the report
from evaluation.llm_judge import JUDGE_MODEL


if __name__ == "__main__":
    asyncio.run(main())
