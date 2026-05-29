"""端到端功能验证脚本 — Layer 1 基础审稿循环"""
import asyncio
import os
import sys
import json
import time

os.environ.setdefault("MCL_ENABLED", "1")
# Flush prints immediately
sys.stdout.reconfigure(line_buffering=True) if hasattr(sys.stdout, 'reconfigure') else None

from core.agent import ScholarAgent


async def main():
    start_time = time.time()
    
    print("=" * 60, flush=True)
    print("ScholarAgent V2 — End-to-End Functional Walkthrough", flush=True)
    print("=" * 60, flush=True)
    
    # L1.1: 论文加载 + L1.2: 基本审稿循环
    print("[INIT] Creating ScholarAgent...", flush=True)
    agent = ScholarAgent(
        paper_path="evaluation/test_papers/paper_001.pdf",
        model=os.environ.get("LLM_MODEL", "gpt-4.1-mini"),
        persona="scholar",
        max_loop_turns=15,
        verbose=True,
    )
    print(f"[INIT] Agent created in {time.time()-start_time:.1f}s", flush=True)
    
    print("\n[L1.2] Starting review...", flush=True)
    result = await agent.start(
        "Please review this paper, focusing on methodology and data consistency."
    )
    print(f"[DONE] Review finished in {time.time()-start_time:.1f}s", flush=True)
    
    elapsed = time.time() - start_time
    findings = agent.get_findings()
    stats = agent.get_stats()
    
    print("\n" + "=" * 60)
    print("=== END-TO-END RESULT ===")
    print(f"  Elapsed: {elapsed:.1f}s")
    print(f"  Findings: {len(findings)}")
    print(f"  Stats: {json.dumps(stats, indent=2, default=str)}")
    print("\n--- Findings ---")
    for i, f in enumerate(findings[:15]):
        sev = f.get("severity", "?")
        title = f.get("title", f.get("description", "?"))[:100]
        print(f"  [{i+1}] [{sev}] {title}")
    
    print("\n--- Loop Result Type ---")
    print(f"  {type(result).__name__}: {str(result)[:200]}")
    
    # 保存结果
    report = {
        "test": "e2e_basic_review",
        "paper": "paper_001.pdf",
        "elapsed_seconds": elapsed,
        "findings_count": len(findings),
        "findings": findings[:15],
        "stats": stats,
        "result_type": type(result).__name__,
    }
    
    out_path = "evaluation/reports/walkthrough_e2e_result.json"
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\n[Saved] {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
