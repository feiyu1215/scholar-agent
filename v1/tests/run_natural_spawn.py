"""Quick test: does Agent spawn naturally without explicit guidance?"""
import asyncio, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")
from core.agent import ScholarAgent

class SpawnTracker:
    def __init__(self, harness):
        self.spawn_calls = []
        self.original = harness.execute_tool
        def tracked(name, args):
            if name == "spawn_perspective":
                self.spawn_calls.append({"lens": args.get("lens",""), "focus": args.get("focus",""), "question": args.get("question","")})
                print(f"  SPAWN DETECTED: lens={args.get('lens')}, focus={args.get('focus')}")
            return self.original(name, args)
        harness.execute_tool = tracked

async def run():
    agent = ScholarAgent(
        paper_path=str(Path(__file__).parent.parent / "examples" / "sample_paper.md"),
        verbose=True,
        max_loop_turns=15,
        token_budget=80000,
    )
    tracker = SpawnTracker(agent.harness)
    # 无 intent，让 Agent 完全自主
    print("[Agent starting WITHOUT intent -- natural behavior...]")
    t0 = time.time()
    response = await agent.start()
    elapsed = time.time() - t0
    stats = agent.get_stats()
    findings = agent.get_findings()
    perspective_findings = [f for f in findings if f.get("perspective")]
    print(f"\n=== NATURAL SPAWN RESULTS ===")
    print(f"Spawn count: {len(tracker.spawn_calls)}")
    print(f"Total findings: {len(findings)}")
    print(f"Perspective findings: {len(perspective_findings)}")
    print(f"Loop turns: {stats['loop_turns_total']}")
    print(f"Total tokens: {stats['total_tokens']}")
    print(f"Elapsed: {elapsed:.1f}s")
    if tracker.spawn_calls:
        for i, sc in enumerate(tracker.spawn_calls, 1):
            print(f"  Spawn [{i}]: lens={sc['lens']}, focus={sc['focus']}")
    else:
        print("  (No spawns -- Agent handled everything in single perspective)")
    print(f"\nAll findings:")
    for i, f in enumerate(findings, 1):
        src = f"[{f['perspective']}]" if f.get("perspective") else "[main]"
        print(f"  {i}. {src}[{f['priority']}] {f['finding'][:100]}")

if __name__ == "__main__":
    asyncio.run(run())
