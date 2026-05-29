#!/usr/bin/env python3
"""Run real agent on all 5 papers and save results summary."""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

# Setup
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from core.agent import ScholarAgent
from core.memory import MemoryStore

PAPERS = ["paper_001", "paper_002", "paper_003", "paper_004", "paper_005"]
RESULTS_FILE = Path(__file__).parent / "reports" / "real_run_results.json"
RAW_FINDINGS_DIR = Path(__file__).parent / "reports" / "raw_findings"
RAW_FINDINGS_DIR.mkdir(parents=True, exist_ok=True)


async def run_one_paper(paper_id: str) -> dict:
    """Run agent on a single paper and return result dict."""
    print(f"\n{'='*60}")
    print(f"  Starting: {paper_id}")
    print(f"{'='*60}")
    
    t0 = time.time()
    try:
        paper_path = f"evaluation/test_papers/{paper_id}.pdf"
        mem_dir = Path(f"evaluation/eval_memory/{paper_id}")
        mem_dir.mkdir(parents=True, exist_ok=True)
        
        agent = ScholarAgent(
            paper_path=paper_path,
            model="gpt-4.1",
            verbose=False,
            max_loop_turns=40,
            token_budget=0,  # Unlimited mode — let max_loop_turns be the throttle
            persona="scholar",
            enable_hdwm=True,
        )
        
        # Isolated memory
        agent.harness._memory_dir = mem_dir
        agent.harness.memory = MemoryStore(mem_dir)
        agent.harness.memory.load()
        
        # Run review
        output = await agent.start(
            "请仔细审阅这篇论文，找出所有方法论、数据、逻辑、引用和写作方面的问题。"
        )
        
        findings = agent.get_findings()
        stats = agent.get_stats()
        elapsed = time.time() - t0
        
        print(f"  -> {len(findings)} findings in {elapsed:.1f}s")
        print(f"  -> Loop turns: {stats.get('loop_turns_total', '?')}")
        
        # Save raw findings
        raw_path = RAW_FINDINGS_DIR / f"{paper_id}_findings.json"
        raw_path.write_text(
            json.dumps(findings, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        
        # End session with reflection
        reflection_stats = {}
        try:
            reflection_stats = await agent.end_session_with_reflection()
            print(f"  -> Reflection: {reflection_stats}")
        except Exception as e:
            print(f"  -> Reflection failed (non-fatal): {e}")
            agent.end_session()
        
        return {
            "paper_id": paper_id,
            "status": "OK",
            "findings_count": len(findings),
            "time_seconds": round(elapsed, 1),
            "loop_turns": stats.get("loop_turns_total", 0),
            "total_tokens": stats.get("total_tokens", 0),
            "reflection": reflection_stats,
            "error": None,
        }
        
    except Exception as e:
        elapsed = time.time() - t0
        import traceback
        tb = traceback.format_exc()
        print(f"  -> FAILED: {e}")
        print(tb)
        return {
            "paper_id": paper_id,
            "status": "FAILED",
            "findings_count": 0,
            "time_seconds": round(elapsed, 1),
            "loop_turns": 0,
            "total_tokens": 0,
            "reflection": {},
            "error": str(e)[:500],
        }


async def main():
    print("=" * 60)
    print("  ScholarAgent V2 — Real Mode Full Evaluation")
    print(f"  Papers: {len(PAPERS)}")
    print(f"  Model: {os.environ.get('LLM_MODEL', 'gpt-4.1')}")
    print("=" * 60)
    
    all_results = []
    total_start = time.time()
    
    for paper_id in PAPERS:
        result = await run_one_paper(paper_id)
        all_results.append(result)
        # Flush to file after each paper (in case of crash)
        RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        RESULTS_FILE.write_text(
            json.dumps(all_results, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
    
    total_elapsed = time.time() - total_start
    
    # Summary
    print("\n" + "=" * 60)
    print("  FINAL SUMMARY")
    print("=" * 60)
    ok = [r for r in all_results if r["status"] == "OK"]
    failed = [r for r in all_results if r["status"] == "FAILED"]
    print(f"  Total: {len(all_results)} papers")
    print(f"  OK: {len(ok)}, FAILED: {len(failed)}")
    print(f"  Total time: {total_elapsed:.1f}s ({total_elapsed/60:.1f} min)")
    print(f"  Total findings: {sum(r['findings_count'] for r in all_results)}")
    if ok:
        avg_findings = sum(r['findings_count'] for r in ok) / len(ok)
        avg_time = sum(r['time_seconds'] for r in ok) / len(ok)
        print(f"  Avg findings/paper: {avg_findings:.1f}")
        print(f"  Avg time/paper: {avg_time:.1f}s")
    if failed:
        print(f"\n  FAILURES:")
        for r in failed:
            print(f"    {r['paper_id']}: {r['error'][:100]}")
    
    print(f"\n  Results saved to: {RESULTS_FILE}")
    print(f"  Raw findings in: {RAW_FINDINGS_DIR}")


if __name__ == "__main__":
    asyncio.run(main())
