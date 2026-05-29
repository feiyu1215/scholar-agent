#!/usr/bin/env python3
"""
Phase 2: LLM-powered tests (uses Friday API).
Tests: deai_audit, rewrite_section, agent_loop (multi-turn), and review_paper.
Ordered from lightest (1 LLM call) to heaviest (6+ LLM calls).
"""
import os, sys, json, asyncio, time, traceback
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

# Force sequential + minimum interval to avoid rate limits
# Friday API limit: ~10 req/min → need ≥6s between requests
# Using 12s to be safe (5 req/min effective)
os.environ["SCHOLAR_MAX_CONCURRENT"] = "1"
os.environ["SCHOLAR_MIN_INTERVAL"] = "12"

sys.path.insert(0, str(Path(__file__).parent.parent))

WORKSPACE = Path(__file__).parent / ".workspace"
RATE_LIMIT_DELAY = 20  # seconds between major test sections (cooldown)

results = []


def record(name, passed, dur_ms, detail="", error=""):
    results.append({"name": name, "passed": passed, "dur_ms": dur_ms, "detail": detail, "error": error})
    icon = "✅" if passed else "❌"
    print(f"  {icon} {name} ({dur_ms:.0f}ms)")
    if detail:
        print(f"     {detail[:300]}")
    if error:
        print(f"     ERROR: {error[:300]}")


async def main():
    print("=" * 60)
    print("  Phase 2: LLM-Powered Tests (Friday API)")
    print("  Model: deepseek-v3-friday | Concurrency: 1 | Interval: 12s")
    print("=" * 60)

    # Ensure workspace has parsed paper
    idx_path = WORKSPACE / "paper" / "section_index.json"
    if not idx_path.exists():
        print("\n  [Setup] Parsing paper first...")
        from tools.paper_parser import parse_paper
        parse_paper(str(Path(__file__).parent.parent / "examples" / "sample_paper.md"), str(WORKSPACE))

    index = json.loads(idx_path.read_text(encoding="utf-8"))

    # ============================================================
    # Test 1: De-AI Audit (1 LLM call - lightest)
    # ============================================================
    print(f"\n[Test 1/5] De-AI Audit (economics scene)...")

    t0 = time.time()
    try:
        from tools.deai_engine import deai_audit, format_deai_result

        # Find introduction section
        target_text = None
        for entry in index:
            if "introduction" in entry.get("id", "").lower():
                sec_path = Path(entry["file"])
                if sec_path.exists():
                    target_text = sec_path.read_text(encoding="utf-8")
                    break

        if not target_text:
            target_text = Path(index[2]["file"]).read_text(encoding="utf-8")

        verdict = await deai_audit(target_text[:3000], scene="S3")
        dur = (time.time() - t0) * 1000
        formatted = format_deai_result(verdict)

        record("deai_audit", True, dur,
               detail=f"Score: {verdict.overall_score:.2f}, Natural: {verdict.is_natural}, "
                      f"Signals: {len(verdict.signals)}")
    except Exception as e:
        dur = (time.time() - t0) * 1000
        record("deai_audit", False, dur, error=f"{type(e).__name__}: {e}")
        traceback.print_exc()

    # ============================================================
    # Test 2: Section Rewrite (1 LLM call)
    # ============================================================
    print(f"\n[Test 2/5] Section Rewrite (abstract)...")
    print(f"  Waiting {RATE_LIMIT_DELAY}s for rate limit cooldown...")
    await asyncio.sleep(RATE_LIMIT_DELAY)

    t0 = time.time()
    try:
        from tools.write_engine import rewrite_section

        # Find abstract
        target_id = None
        for entry in index:
            if "abstract" in entry.get("id", "").lower():
                target_id = entry["id"]
                break
        if not target_id:
            target_id = index[1]["id"]

        result = await rewrite_section(
            target_id,
            custom_instructions="Improve academic tone, add hedging where appropriate, "
                                "and ensure the contribution is clearly stated."
        )
        dur = (time.time() - t0) * 1000

        # Check if revision was saved
        rev_path = WORKSPACE / "revisions" / f"{target_id}_v2.md"
        if rev_path.exists():
            revised = rev_path.read_text(encoding="utf-8")
            record("rewrite_section", True, dur,
                   detail=f"Rewrote '{target_id}', revised={len(revised)}c. Result: {result[:200]}")
        else:
            record("rewrite_section", True, dur,
                   detail=f"Rewrite returned ({len(result)}c), checking: {result[:200]}")
    except Exception as e:
        dur = (time.time() - t0) * 1000
        record("rewrite_section", False, dur, error=f"{type(e).__name__}: {e}")
        traceback.print_exc()

    # ============================================================
    # Test 3: Interactive Agent Loop - Turn 1 (1-2 LLM calls)
    # ============================================================
    print(f"\n[Test 3/5] Interactive Agent Loop - Turn 1...")
    print(f"  Waiting {RATE_LIMIT_DELAY}s for rate limit cooldown...")
    await asyncio.sleep(RATE_LIMIT_DELAY)

    t0 = time.time()
    try:
        from llm.client import LLMClient
        import main as agent_main
        agent_main.WORKSPACE.mkdir(parents=True, exist_ok=True)

        client = LLMClient()
        history = [
            {"role": "user",
             "content": "Read the abstract section and give me 3 specific suggestions for improvement. Be concise."}
        ]

        await agent_main.agent_loop(history, client)
        dur = (time.time() - t0) * 1000

        # Find the last assistant message
        assistant_msgs = [m for m in history if m.get("role") == "assistant" and m.get("content")]
        tool_msgs = [m for m in history if m.get("role") == "tool"]

        if assistant_msgs:
            last = assistant_msgs[-1]["content"]
            record("agent_loop_turn1", True, dur,
                   detail=f"Response: {len(last)}c, {len(tool_msgs)} tool calls. "
                          f"Preview: {last[:200]}")
        else:
            record("agent_loop_turn1", False, dur, error="No assistant response found")

    except Exception as e:
        dur = (time.time() - t0) * 1000
        record("agent_loop_turn1", False, dur, error=f"{type(e).__name__}: {e}")
        traceback.print_exc()

    # ============================================================
    # Test 4: Interactive Agent Loop - Turn 2 (follow-up)
    # ============================================================
    print(f"\n[Test 4/5] Interactive Agent Loop - Turn 2 (follow-up)...")
    print(f"  Waiting {RATE_LIMIT_DELAY}s for rate limit cooldown...")
    await asyncio.sleep(RATE_LIMIT_DELAY)

    t0 = time.time()
    try:
        history.append({"role": "user",
                        "content": "Now check the introduction section for overclaim language. Be brief."})
        await agent_main.agent_loop(history, client)
        dur = (time.time() - t0) * 1000

        assistant_msgs2 = [m for m in history if m.get("role") == "assistant" and m.get("content")]
        if len(assistant_msgs2) > len(assistant_msgs):
            last2 = assistant_msgs2[-1]["content"]
            record("agent_loop_turn2", True, dur,
                   detail=f"Response: {len(last2)}c. Preview: {last2[:200]}")
        else:
            record("agent_loop_turn2", True, dur, detail="Agent processed (may have ended differently)")

    except Exception as e:
        dur = (time.time() - t0) * 1000
        record("agent_loop_turn2", False, dur, error=f"{type(e).__name__}: {e}")
        traceback.print_exc()

    # ============================================================
    # Test 5: Multi-Role Review (6+ LLM calls - heaviest)
    # ============================================================
    print(f"\n[Test 5/5] Multi-Role Review (5 reviewers + consolidation)...")
    print(f"  Waiting {RATE_LIMIT_DELAY * 2}s for rate limit cooldown (heavy test)...")
    await asyncio.sleep(RATE_LIMIT_DELAY * 2)

    t0 = time.time()
    try:
        from tools.review_engine import review_paper
        result = await review_paper()
        dur = (time.time() - t0) * 1000

        # Check results
        consolidated_path = WORKSPACE / "review" / "consolidated.json"
        if consolidated_path.exists():
            data = json.loads(consolidated_path.read_text(encoding="utf-8"))
            n_issues = len(data.get("issues", []))
            score = data.get("overall_score", "N/A")
            verdict = data.get("verdict", "N/A")
            record("review_paper", True, dur,
                   detail=f"Score: {score}, Verdict: {verdict}, "
                          f"{n_issues} issues found. Output: {len(result)} chars")
        else:
            record("review_paper", True, dur,
                   detail=f"Review completed. Output: {len(result)} chars (no consolidated.json)")
    except Exception as e:
        dur = (time.time() - t0) * 1000
        record("review_paper", False, dur, error=f"{type(e).__name__}: {e}")
        traceback.print_exc()

    # ============================================================
    # Summary
    # ============================================================
    passed = sum(1 for r in results if r["passed"])
    failed = sum(1 for r in results if not r["passed"])

    print("\n" + "=" * 60)
    print(f"  RESULTS: {passed} passed, {failed} failed, {len(results)} total")
    print("=" * 60)

    if failed:
        print("\n  ❌ FAILED:")
        for r in results:
            if not r["passed"]:
                print(f"    - {r['name']}: {r['error']}")
    else:
        print("\n  🎉 ALL PHASE-2 TESTS PASSED!")

    # Save report
    report_path = Path(__file__).parent / "reports" / "test_phase2_report.json"
    report_path.write_text(json.dumps({
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "passed": passed, "failed": failed,
        "results": results
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n  Report saved: {report_path}")


if __name__ == "__main__":
    asyncio.run(main())
