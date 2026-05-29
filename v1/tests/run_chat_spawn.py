"""Quick test: spawn triggered via multi-turn chat."""
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
        max_loop_turns=10,
        token_budget=100000,
    )
    tracker = SpawnTracker(agent.harness)

    # Round 1: 自主审阅（不 spawn）
    print("[Round 1: Agent autonomous review...]")
    t0 = time.time()
    r1 = await agent.start()
    t1 = time.time()
    f1 = len(agent.get_findings())
    s1 = len(tracker.spawn_calls)
    print(f"  Round 1 done: {f1} findings, {s1} spawns, {t1-t0:.1f}s")

    # Round 2: 要求 spawn
    print("\n[Round 2: User asks for spawn_perspective...]")
    chat_msg = (
        "你的审阅很好。现在请用 spawn_perspective 请一个 experimental_design_critic "
        "专门审查这篇论文的实验设计——特别是它是否有足够的 robustness checks。"
    )
    r2 = await agent.chat(chat_msg)
    t2 = time.time()
    f2 = len(agent.get_findings())
    s2 = len(tracker.spawn_calls)
    print(f"  Round 2 done: {f2} findings (+{f2-f1}), {s2} spawns (+{s2-s1}), {t2-t1:.1f}s")

    # Results
    stats = agent.get_stats()
    perspective_findings = [f for f in agent.get_findings() if f.get("perspective")]
    print(f"\n=== CHAT SPAWN RESULTS ===")
    print(f"Total spawns: {s2}")
    print(f"Spawns in chat: {s2 - s1}")
    print(f"Total findings: {f2}")
    print(f"Perspective findings: {len(perspective_findings)}")
    print(f"Total tokens: {stats['total_tokens']}")
    if tracker.spawn_calls:
        for i, sc in enumerate(tracker.spawn_calls, 1):
            print(f"  Spawn [{i}]: lens={sc['lens']}, focus={sc['focus']}")
    if perspective_findings:
        print(f"\nPerspective findings:")
        for f in perspective_findings:
            print(f"  [{f['perspective']}][{f['priority']}] {f['finding'][:100]}")

if __name__ == "__main__":
    asyncio.run(run())
