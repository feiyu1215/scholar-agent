"""Quick test: guided spawn trigger."""
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
        max_loop_turns=18,
        token_budget=100000,
    )
    tracker = SpawnTracker(agent.harness)
    intent = (
        "请对这篇论文进行多视角审阅。"
        "我特别希望你能用 spawn_perspective 发起至少一个独立视角，"
        "比如让一个统计方法专家专门审查 methodology section 的因果识别策略，"
        "或者让一个写作审查者评估论文的表达质量。"
    )
    print("[Agent starting with multi-perspective intent...]")
    t0 = time.time()
    response = await agent.start(user_intent=intent)
    elapsed = time.time() - t0
    stats = agent.get_stats()
    findings = agent.get_findings()
    perspective_findings = [f for f in findings if f.get("perspective")]
    print(f"\n=== RESULTS ===")
    print(f"Spawn count: {len(tracker.spawn_calls)}")
    print(f"Total findings: {len(findings)}")
    print(f"Perspective findings: {len(perspective_findings)}")
    print(f"Loop turns: {stats['loop_turns_total']}")
    print(f"Total tokens: {stats['total_tokens']}")
    print(f"Elapsed: {elapsed:.1f}s")
    if tracker.spawn_calls:
        for i, sc in enumerate(tracker.spawn_calls, 1):
            print(f"  Spawn [{i}]: lens={sc['lens']}, focus={sc['focus']}")
            print(f"             question={sc['question'][:80]}")
    if perspective_findings:
        print(f"\nPerspective findings detail:")
        for f in perspective_findings:
            print(f"  [{f['perspective']}][{f['priority']}] {f['finding'][:100]}")
    print(f"\nAgent response (first 500 chars):")
    print(response[:500])

if __name__ == "__main__":
    asyncio.run(run())
