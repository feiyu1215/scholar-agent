#!/usr/bin/env python3
"""
evaluation/run_recall_verification.py — 验证 P0 修复后的 Recall 提升。

目的：
    在 P0 修复（AppendixMathAuditSkill + ConsistencyValidator Rule9 + PCG appendix weight）
    和搜索增强 + finding 去重完成后，对 gold_paper_001 和 gold_paper_003 重新运行 agent，
    对比基线 F1=46.3% (P=58.3%, R=38.9%)。

用法：
    cd v2/
    python3 -m evaluation.run_recall_verification

    # 只跑某篇：
    python3 -m evaluation.run_recall_verification --paper paper_001

    # 使用不同模型：
    python3 -m evaluation.run_recall_verification --model gpt-4.1-mini

输出：
    - Console: 实时进度 + 最终 P/R/F1 对比
    - evaluation/reports/recall_verification_<timestamp>.json: 完整 raw findings + 匹配明细
    - evaluation/reports/recall_verification_<timestamp>.md: 可读报告
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
from typing import Any

# Ensure v2/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Load .env
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())

from evaluation.metrics import (
    Finding,
    EvalMetrics,
    compute_metrics,
    compute_aggregate,
    compute_similarity,
)


# ============================================================
# Gold Standard Loading (gold_paper_XXX.json format)
# ============================================================

GOLD_DIR = Path(__file__).parent / "gold_standard"
TEST_PAPERS_DIR = Path(__file__).parent / "test_papers"
REPORTS_DIR = Path(__file__).parent / "reports"


def load_gold_papers(paper_id: str | None = None) -> list[dict]:
    """Load gold-standard papers (gold_paper_*.json format)."""
    papers = []
    for f in sorted(GOLD_DIR.glob("gold_paper_*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        if paper_id and data["paper_id"] != paper_id:
            continue
        papers.append(data)
    return papers


def gold_to_findings(gold_data: dict) -> list[Finding]:
    """Convert gold_findings to Finding objects."""
    raw = gold_data.get("gold_findings", gold_data.get("findings", []))
    return [
        Finding(
            text=f.get("description", f.get("text", "")),
            section=f.get("location", f.get("section", "")),
            priority=f.get("severity", f.get("priority", "medium")),
            category=f.get("category", ""),
        )
        for f in raw
    ]


# ============================================================
# Real Agent Runner
# ============================================================

async def run_agent_on_paper(paper_id: str, model: str, verbose: bool = False) -> tuple[list[dict], float]:
    """Run ScholarAgent on a paper and return raw findings + elapsed time.

    Returns:
        (raw_findings_list, elapsed_seconds)
    """
    from core.agent import ScholarAgent

    paper_file = TEST_PAPERS_DIR / f"{paper_id}.pdf"
    if not paper_file.exists():
        raise FileNotFoundError(f"Paper file not found: {paper_file}")

    # Isolated memory for this eval run
    eval_memory_dir = Path(__file__).parent / "eval_memory" / f"{paper_id}_verification"
    eval_memory_dir.mkdir(parents=True, exist_ok=True)

    print(f"  Initializing ScholarAgent (model={model})...")
    agent = ScholarAgent(
        paper_path=str(paper_file),
        model=model,
        verbose=verbose,
        max_loop_turns=60,  # 与 HANDOVER 文档中的评估配置一致
        token_budget=1_000_000,  # 1M token 限制
        context_window=128_000,
        persona="scholar",
        enable_hdwm=True,  # V3 features on
    )

    # Override memory to isolated location
    from core.memory import MemoryStore
    agent.harness._memory_dir = eval_memory_dir
    agent.harness.memory = MemoryStore(eval_memory_dir)
    agent.harness.memory.load()

    print(f"  Starting cognitive loop...")
    start_time = time.time()

    # S3: 移除 per-paper 硬编码 intent — 审稿策略由 S1-LLM 从论文内容自动生成
    # Agent 的 pre_generate_cognitive_hints 会基于 DomainTemplate + paper abstract
    # 自动产出针对性的审稿策略，不再需要人工为每篇论文编写详细 intent。
    # 使用通用 intent，让系统自行决定审稿重点。
    user_intent_text = (
        "请仔细审阅这篇论文，找出所有方法论、数据、逻辑、引用和写作方面的问题。"
        "依据你在初始化时生成的审稿认知策略，系统性地检查每个维度。"
    )

    try:
        output = await agent.start(
            user_intent=user_intent_text
        )
    except Exception as e:
        print(f"  ERROR during agent run: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return [], time.time() - start_time

    elapsed = time.time() - start_time
    print(f"  Cognitive loop completed in {elapsed:.1f}s")

    # Extract findings
    raw_findings = agent.get_findings()
    print(f"  Agent produced {len(raw_findings)} findings")

    # End session
    try:
        await agent.end_session_with_reflection()
    except Exception:
        agent.end_session()

    return raw_findings, elapsed


def findings_to_eval(raw_findings: list[dict]) -> list[Finding]:
    """Convert agent raw findings to Finding objects for metric computation."""
    return [
        Finding(
            text=f.get("finding", f.get("text", "")),
            section=f.get("section", ""),
            priority=f.get("priority", "medium"),
            category=f.get("category", ""),
        )
        for f in raw_findings
    ]


# ============================================================
# Report Generation
# ============================================================

BASELINE = {
    "paper_001": {"precision": 0.600, "recall": 0.333, "f1": 0.426},
    "paper_003": {"precision": 0.571, "recall": 0.444, "f1": 0.499},
    "aggregate": {"precision": 0.583, "recall": 0.389, "f1": 0.463},
}


def generate_report(
    results: list[dict],
    aggregate: dict,
    model: str,
    total_time: float,
) -> str:
    """Generate markdown verification report."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# Recall Verification Report (Post-P0 Fix)",
        "",
        f"**Generated**: {timestamp}",
        f"**Model**: {model}",
        f"**Total Runtime**: {total_time:.1f}s",
        f"**Papers**: {len(results)}",
        "",
        "---",
        "",
        "## Summary: Baseline vs Post-Fix",
        "",
        "| Metric | Baseline | Post-Fix | Delta |",
        "|--------|----------|----------|-------|",
        f"| Precision | {BASELINE['aggregate']['precision']:.3f} | {aggregate['precision']:.3f} | {aggregate['precision'] - BASELINE['aggregate']['precision']:+.3f} |",
        f"| Recall | {BASELINE['aggregate']['recall']:.3f} | {aggregate['recall']:.3f} | {aggregate['recall'] - BASELINE['aggregate']['recall']:+.3f} |",
        f"| F1 | {BASELINE['aggregate']['f1']:.3f} | {aggregate['f1']:.3f} | {aggregate['f1'] - BASELINE['aggregate']['f1']:+.3f} |",
        "",
        "---",
        "",
    ]

    for r in results:
        paper_id = r["paper_id"]
        bl = BASELINE.get(paper_id, {})
        lines.extend([
            f"## {paper_id}: {r.get('title', '')}",
            "",
            f"**Metrics**: P={r['precision']:.3f} R={r['recall']:.3f} F1={r['f1']:.3f}",
            f"**Baseline**: P={bl.get('precision', 0):.3f} R={bl.get('recall', 0):.3f} F1={bl.get('f1', 0):.3f}",
            f"**Agent produced**: {r['num_predicted']} findings | Gold: {r['num_gold']} | Matched: {r['num_matched']}",
            f"**Runtime**: {r['elapsed']:.1f}s | Turns: {r.get('loop_turns', 'N/A')}",
            "",
        ])

        # Matches detail
        if r.get("match_details"):
            lines.append("### Matched Findings")
            lines.append("")
            for m in r["match_details"]:
                lines.append(f"- **Gold {m['gold_id']}** ↔ Agent #{m['pred_idx']+1} (sim={m['similarity']:.3f})")
                lines.append(f"  - Gold: {m['gold_text'][:100]}...")
                lines.append(f"  - Agent: {m['pred_text'][:100]}...")
                lines.append("")

        # Unmatched gold
        if r.get("missed_gold"):
            lines.append("### Missed Gold Findings (False Negatives)")
            lines.append("")
            for mg in r["missed_gold"]:
                lines.append(f"- **{mg['id']}** [{mg['severity']}] {mg['text'][:120]}...")
            lines.append("")

        # Unmatched predicted (FP)
        if r.get("false_positives"):
            lines.append("### False Positives (Agent-only)")
            lines.append("")
            for fp in r["false_positives"]:
                lines.append(f"- [{fp['priority']}] {fp['text'][:120]}...")
            lines.append("")

        lines.append("---")
        lines.append("")

    # P0 fix effectiveness
    lines.extend([
        "## P0 Fix Effectiveness Analysis",
        "",
        "P0 修复目标瓶颈 vs 验证结果：",
        "",
        "| 修复 | 目标 Gold IDs | 是否被新发现 | 备注 |",
        "|------|--------------|-------------|------|",
    ])

    # Check which previously-missed gold findings are now found
    p0_targets = {
        "AppendixMathAuditSkill": ["G001 (001)", "G005 (003)"],
        "ConsistencyValidator Rule9": ["G005 (001)"],
        "PCG appendix weight": ["G001 (001)", "G005 (003)"],
    }
    for fix_name, targets in p0_targets.items():
        lines.append(f"| {fix_name} | {', '.join(targets)} | 见上方匹配结果 | |")

    lines.append("")
    return "\n".join(lines)


# ============================================================
# Main
# ============================================================

def _deduplicate_findings(all_runs_findings: list[list[dict]], threshold: float = 0.55) -> list[dict]:
    """Merge findings from multiple runs, deduplicating near-duplicates.

    Strategy: for each finding from later runs, check if a similar finding
    already exists in the merged set. If similarity >= threshold, skip it
    (keep the one already in the set). Otherwise add it.
    """
    if not all_runs_findings:
        return []
    if len(all_runs_findings) == 1:
        return all_runs_findings[0]

    merged: list[dict] = []
    merged_texts: list[str] = []

    for run_findings in all_runs_findings:
        for f in run_findings:
            f_text = f.get("finding", f.get("text", ""))
            if not f_text.strip():
                continue

            # Check against existing merged findings
            is_duplicate = False
            for existing_text in merged_texts:
                sim = compute_similarity(f_text, existing_text)
                if sim >= threshold:
                    is_duplicate = True
                    break

            if not is_duplicate:
                merged.append(f)
                merged_texts.append(f_text)

    return merged


async def main_async():
    parser = argparse.ArgumentParser(description="Recall Verification (Post-P0 Fix)")
    parser.add_argument("--paper", type=str, default=None,
                       help="Only run on one paper (paper_001 or paper_003)")
    parser.add_argument("--model", type=str, default=None,
                       help="Model to use (default: from .env or gpt-4.1)")
    parser.add_argument("--verbose", action="store_true",
                       help="Enable verbose agent output")
    parser.add_argument("--runs", type=int, default=1,
                       help="Number of runs per paper (findings merged across runs to reduce variance)")
    args = parser.parse_args()

    model = args.model or os.environ.get("LLM_MODEL", "gpt-4.1")

    print(f"\n{'='*60}")
    print(f"  Recall Verification — Post-P0 Fix")
    print(f"  Model: {model}")
    print(f"  Baseline: F1=46.3% (P=58.3%, R=38.9%)")
    print(f"{'='*60}\n")

    # Load gold papers
    gold_papers = load_gold_papers(args.paper)
    if not gold_papers:
        print("ERROR: No gold papers found. Expected gold_paper_*.json in evaluation/gold_standard/")
        sys.exit(1)
    print(f"Loaded {len(gold_papers)} gold paper(s): {[p['paper_id'] for p in gold_papers]}\n")

    # Run evaluation
    all_results = []
    all_metrics = []
    total_start = time.time()

    num_runs = max(1, args.runs)

    for gold_data in gold_papers:
        paper_id = gold_data["paper_id"]
        title = gold_data.get("paper_title", "")
        print(f"--- Evaluating: {paper_id} ({title}) [runs={num_runs}] ---")

        # Multi-run: collect findings from each run, then merge
        all_runs_raw: list[list[dict]] = []
        total_elapsed = 0.0

        for run_idx in range(num_runs):
            if num_runs > 1:
                print(f"\n  [Run {run_idx + 1}/{num_runs}]")
            raw_findings, elapsed = await run_agent_on_paper(paper_id, model, args.verbose)
            all_runs_raw.append(raw_findings)
            total_elapsed += elapsed

        # Merge and deduplicate across runs
        if num_runs > 1:
            raw_findings = _deduplicate_findings(all_runs_raw)
            elapsed = total_elapsed
            print(f"  Merged {sum(len(r) for r in all_runs_raw)} findings → {len(raw_findings)} unique (across {num_runs} runs)")
        else:
            raw_findings = all_runs_raw[0]
            elapsed = total_elapsed

        # Convert to Finding objects
        predicted = findings_to_eval(raw_findings)
        gold_findings = gold_to_findings(gold_data)

        # Compute metrics (threshold=0.25 for CJK-aware matching)
        metrics = compute_metrics(paper_id, predicted, gold_findings, threshold=0.25)
        all_metrics.append(metrics)

        # Build match details
        gold_raw = gold_data.get("gold_findings", gold_data.get("findings", []))
        match_details = []
        for m in metrics.matches:
            match_details.append({
                "gold_id": gold_raw[m.gold_idx]["id"],
                "gold_text": gold_raw[m.gold_idx].get("description", ""),
                "pred_idx": m.predicted_idx,
                "pred_text": predicted[m.predicted_idx].text if m.predicted_idx < len(predicted) else "",
                "similarity": m.similarity,
            })

        missed_gold = [
            {
                "id": gold_raw[i]["id"],
                "severity": gold_raw[i].get("severity", "medium"),
                "text": gold_raw[i].get("description", ""),
            }
            for i in metrics.unmatched_gold
        ]

        false_positives = [
            {"text": predicted[i].text, "priority": predicted[i].priority}
            for i in metrics.unmatched_predicted
        ]

        result = {
            "paper_id": paper_id,
            "title": title,
            "precision": metrics.precision,
            "recall": metrics.recall,
            "f1": metrics.f1,
            "num_predicted": metrics.num_predicted,
            "num_gold": metrics.num_gold,
            "num_matched": metrics.num_matched,
            "elapsed": elapsed,
            "raw_findings": raw_findings,
            "match_details": match_details,
            "missed_gold": missed_gold,
            "false_positives": false_positives,
        }
        all_results.append(result)

        # Print summary
        bl = BASELINE.get(paper_id, {})
        print(f"\n  Result: P={metrics.precision:.3f} R={metrics.recall:.3f} F1={metrics.f1:.3f}")
        print(f"  Baseline: P={bl.get('precision',0):.3f} R={bl.get('recall',0):.3f} F1={bl.get('f1',0):.3f}")
        print(f"  Delta F1: {metrics.f1 - bl.get('f1', 0):+.3f}")
        print(f"  Matched: {metrics.num_matched}/{metrics.num_gold} gold | {metrics.num_matched}/{metrics.num_predicted} predicted")
        print()

    total_time = time.time() - total_start

    # Aggregate
    agg = compute_aggregate(all_metrics)
    aggregate_dict = {
        "precision": agg.avg_precision,
        "recall": agg.avg_recall,
        "f1": agg.avg_f1,
    }

    print(f"{'='*60}")
    print(f"  AGGREGATE RESULTS")
    print(f"  Post-Fix: P={agg.avg_precision:.3f} R={agg.avg_recall:.3f} F1={agg.avg_f1:.3f}")
    print(f"  Baseline: P={BASELINE['aggregate']['precision']:.3f} R={BASELINE['aggregate']['recall']:.3f} F1={BASELINE['aggregate']['f1']:.3f}")
    print(f"  Delta:    ΔP={agg.avg_precision - BASELINE['aggregate']['precision']:+.3f} ΔR={agg.avg_recall - BASELINE['aggregate']['recall']:+.3f} ΔF1={agg.avg_f1 - BASELINE['aggregate']['f1']:+.3f}")
    print(f"  Total time: {total_time:.1f}s")
    print(f"{'='*60}\n")

    # Save reports
    REPORTS_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    # JSON (full data)
    json_path = REPORTS_DIR / f"recall_verification_{ts}.json"
    json_data = {
        "timestamp": datetime.now().isoformat(),
        "model": model,
        "baseline": BASELINE,
        "aggregate": aggregate_dict,
        "delta": {
            "precision": agg.avg_precision - BASELINE["aggregate"]["precision"],
            "recall": agg.avg_recall - BASELINE["aggregate"]["recall"],
            "f1": agg.avg_f1 - BASELINE["aggregate"]["f1"],
        },
        "per_paper": all_results,
        "total_time_seconds": total_time,
    }
    json_path.write_text(json.dumps(json_data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"JSON report saved: {json_path}")

    # Markdown (human-readable)
    md_path = REPORTS_DIR / f"recall_verification_{ts}.md"
    md_report = generate_report(all_results, aggregate_dict, model, total_time)
    md_path.write_text(md_report, encoding="utf-8")
    print(f"Markdown report saved: {md_path}")


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
