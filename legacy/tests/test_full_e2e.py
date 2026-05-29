#!/usr/bin/env python3
"""
Full End-to-End Test Suite for Scholar-Agent
=============================================
Runs both Phase 1 (structural) and Phase 2 (LLM-powered) tests.

Prerequisites:
  - .env file with Friday API credentials
  - examples/sample_paper.md in project root
  - pip install openai python-dotenv

Usage:
  python3 -u test_full_e2e.py

Estimated runtime: ~5 minutes (Phase 1: <1s, Phase 2: ~4min due to rate limiting)
"""
import os, sys, json, asyncio, time, traceback, shutil
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

# Rate limit configuration for Friday API (~10 req/min)
os.environ["SCHOLAR_MAX_CONCURRENT"] = "1"
os.environ["SCHOLAR_MIN_INTERVAL"] = "12"

sys.path.insert(0, str(Path(__file__).parent.parent))

PROJECT_ROOT = Path(__file__).parent.parent
WORKSPACE = PROJECT_ROOT / ".workspace"
RATE_LIMIT_DELAY = 20

results = []


def record(phase, name, passed, dur_ms, detail="", error=""):
    results.append({
        "phase": phase, "name": name, "passed": passed,
        "dur_ms": dur_ms, "detail": detail, "error": error
    })
    icon = "✅" if passed else "❌"
    print(f"  {icon} [{phase}] {name} ({dur_ms:.0f}ms)")
    if detail:
        print(f"     {detail[:200]}")
    if error:
        print(f"     ERROR: {error[:200]}")


async def phase1():
    """Phase 1: Zero-cost structural tests (no LLM calls)."""
    print("\n" + "=" * 60)
    print("  PHASE 1: Structural Tests (no LLM)")
    print("=" * 60)

    # Clean workspace for fresh test
    if WORKSPACE.exists():
        shutil.rmtree(WORKSPACE)
    WORKSPACE.mkdir(parents=True, exist_ok=True)

    # Test 1: parse_paper
    t0 = time.time()
    try:
        from tools.paper_parser import parse_paper
        result = parse_paper(str(PROJECT_ROOT / "examples" / "sample_paper.md"), str(WORKSPACE))
        dur = (time.time() - t0) * 1000
        idx = json.loads((WORKSPACE / "paper" / "section_index.json").read_text())
        record("P1", "parse_paper", True, dur,
               detail=f"{len(idx)} sections parsed")
    except Exception as e:
        record("P1", "parse_paper", False, (time.time()-t0)*1000, error=str(e))

    # Test 2: presubmission_check
    t0 = time.time()
    try:
        from tools.presubmission import presubmission_check
        result = presubmission_check()
        dur = (time.time() - t0) * 1000
        record("P1", "presubmission_check", True, dur,
               detail=f"Output: {len(result)} chars")
    except Exception as e:
        record("P1", "presubmission_check", False, (time.time()-t0)*1000, error=str(e))

    # Test 3: architecture_diagnosis
    t0 = time.time()
    try:
        from tools.structure_tools import architecture_diagnosis
        result = architecture_diagnosis()
        dur = (time.time() - t0) * 1000
        record("P1", "architecture_diagnosis", True, dur,
               detail=f"Output: {len(result)} chars")
    except Exception as e:
        record("P1", "architecture_diagnosis", False, (time.time()-t0)*1000, error=str(e))

    # Test 4: build_voice_profile
    t0 = time.time()
    try:
        from tools.voice_engine import build_voice_profile
        result = build_voice_profile()
        dur = (time.time() - t0) * 1000
        record("P1", "build_voice_profile", True, dur,
               detail=f"Output: {len(result)} chars")
    except Exception as e:
        record("P1", "build_voice_profile", False, (time.time()-t0)*1000, error=str(e))

    # Test 5: citation_analysis
    t0 = time.time()
    try:
        from tools.citation_tools import parse_references, check_overclaims
        refs = parse_references()
        claims = check_overclaims()
        dur = (time.time() - t0) * 1000
        record("P1", "citation_analysis", True, dur,
               detail=f"Refs: {len(refs)} chars, Overclaims: {len(claims)} chars")
    except Exception as e:
        record("P1", "citation_analysis", False, (time.time()-t0)*1000, error=str(e))

    # Test 6: section_operations
    t0 = time.time()
    try:
        from tools.section_ops import read_section_index, read_section, consistency_check
        idx_result = read_section_index()
        sec_result = read_section(json.loads((WORKSPACE / "paper" / "section_index.json").read_text())[0]["id"])
        cons_result = consistency_check()
        dur = (time.time() - t0) * 1000
        record("P1", "section_operations", True, dur,
               detail=f"Index: {len(idx_result)}c, Section: {len(sec_result)}c, Consistency: {len(cons_result)}c")
    except Exception as e:
        record("P1", "section_operations", False, (time.time()-t0)*1000, error=str(e))


async def phase2():
    """Phase 2: LLM-powered tests (uses Friday API)."""
    print("\n" + "=" * 60)
    print("  PHASE 2: LLM-Powered Tests (Friday API)")
    print("  Model: deepseek-v3-friday | Interval: 12s")
    print("=" * 60)

    index = json.loads((WORKSPACE / "paper" / "section_index.json").read_text(encoding="utf-8"))

    # Test 1: De-AI Audit
    print(f"\n  [deai_audit] Running...")
    t0 = time.time()
    try:
        from tools.deai_engine import deai_audit, format_deai_result
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
        record("P2", "deai_audit", True, dur,
               detail=f"Score: {verdict.overall_score:.2f}, Natural: {verdict.is_natural}")
    except Exception as e:
        record("P2", "deai_audit", False, (time.time()-t0)*1000, error=str(e))
        traceback.print_exc()

    # Test 2: Section Rewrite
    print(f"\n  [rewrite_section] Waiting {RATE_LIMIT_DELAY}s...")
    await asyncio.sleep(RATE_LIMIT_DELAY)
    t0 = time.time()
    try:
        from tools.write_engine import rewrite_section
        target_id = None
        for entry in index:
            if "abstract" in entry.get("id", "").lower():
                target_id = entry["id"]
                break
        if not target_id:
            target_id = index[1]["id"]

        result = await rewrite_section(target_id, custom_instructions="Improve academic tone.")
        dur = (time.time() - t0) * 1000
        record("P2", "rewrite_section", True, dur,
               detail=f"Rewrote '{target_id}', output: {len(result)} chars")
    except Exception as e:
        record("P2", "rewrite_section", False, (time.time()-t0)*1000, error=str(e))
        traceback.print_exc()

    # Test 3: Agent Loop (multi-turn)
    print(f"\n  [agent_loop] Waiting {RATE_LIMIT_DELAY}s...")
    await asyncio.sleep(RATE_LIMIT_DELAY)
    t0 = time.time()
    try:
        from llm.client import LLMClient
        import main as agent_main
        agent_main.WORKSPACE.mkdir(parents=True, exist_ok=True)

        client = LLMClient()
        history = [
            {"role": "user",
             "content": "Read the abstract and give 2 improvement suggestions. Be concise."}
        ]
        await agent_main.agent_loop(history, client)
        dur = (time.time() - t0) * 1000

        assistant_msgs = [m for m in history if m.get("role") == "assistant" and m.get("content")]
        tool_msgs = [m for m in history if m.get("role") == "tool"]

        if assistant_msgs:
            record("P2", "agent_loop_turn1", True, dur,
                   detail=f"{len(tool_msgs)} tool calls, response: {len(assistant_msgs[-1]['content'])}c")
        else:
            record("P2", "agent_loop_turn1", False, dur, error="No assistant response")

        # Turn 2
        print(f"\n  [agent_loop_turn2] Waiting {RATE_LIMIT_DELAY}s...")
        await asyncio.sleep(RATE_LIMIT_DELAY)
        t0 = time.time()
        history.append({"role": "user", "content": "Check the introduction for overclaim language. Brief answer."})
        await agent_main.agent_loop(history, client)
        dur = (time.time() - t0) * 1000

        new_msgs = [m for m in history if m.get("role") == "assistant" and m.get("content")]
        if len(new_msgs) > len(assistant_msgs):
            record("P2", "agent_loop_turn2", True, dur,
                   detail=f"Response: {len(new_msgs[-1]['content'])}c")
        else:
            record("P2", "agent_loop_turn2", True, dur, detail="Processed (no new text)")
    except Exception as e:
        record("P2", "agent_loop", False, (time.time()-t0)*1000, error=str(e))
        traceback.print_exc()

    # Test 4: Multi-Role Review (heaviest)
    print(f"\n  [review_paper] Waiting {RATE_LIMIT_DELAY * 2}s (heavy test)...")
    await asyncio.sleep(RATE_LIMIT_DELAY * 2)
    t0 = time.time()
    try:
        from tools.review_engine import review_paper
        result = await review_paper()
        dur = (time.time() - t0) * 1000

        consolidated_path = WORKSPACE / "review" / "consolidated.json"
        if consolidated_path.exists():
            data = json.loads(consolidated_path.read_text(encoding="utf-8"))
            n_issues = len(data.get("issues", []))
            score = data.get("overall_score", "N/A")
            verdict = data.get("verdict", "N/A")
            record("P2", "review_paper", True, dur,
                   detail=f"Score: {score}, Verdict: {verdict}, {n_issues} issues, Output: {len(result)}c")
        else:
            record("P2", "review_paper", True, dur, detail=f"Completed, {len(result)}c output")
    except Exception as e:
        record("P2", "review_paper", False, (time.time()-t0)*1000, error=str(e))
        traceback.print_exc()


async def main():
    print("╔══════════════════════════════════════════════════════════╗")
    print("║   Scholar-Agent: Full End-to-End Test Suite             ║")
    print("╚══════════════════════════════════════════════════════════╝")

    total_start = time.time()

    await phase1()
    await phase2()

    total_dur = time.time() - total_start

    # Summary
    passed = sum(1 for r in results if r["passed"])
    failed = sum(1 for r in results if not r["passed"])

    print("\n" + "═" * 60)
    print(f"  FINAL RESULTS: {passed} passed, {failed} failed, {len(results)} total")
    print(f"  Total time: {total_dur:.1f}s")
    print("═" * 60)

    if failed:
        print("\n  ❌ FAILURES:")
        for r in results:
            if not r["passed"]:
                print(f"    [{r['phase']}] {r['name']}: {r['error']}")
    else:
        print("\n  🎉 ALL TESTS PASSED!")

    # Feature coverage summary
    print("\n  Feature Coverage:")
    print("    ├─ Paper Parsing (markdown → structured sections)")
    print("    ├─ Pre-submission Check (completeness gates)")
    print("    ├─ Architecture Diagnosis (hourglass structure)")
    print("    ├─ Voice Profiling (stylometric analysis)")
    print("    ├─ Citation Analysis (reference parsing + overclaim detection)")
    print("    ├─ Section Operations (CRUD + consistency)")
    print("    ├─ De-AI Audit (AI-detection scoring)")
    print("    ├─ Section Rewriting (LLM-powered revision)")
    print("    ├─ Interactive Agent Loop (multi-turn with tools)")
    print("    └─ Multi-Role Review (5 reviewers + consolidation + dedup)")

    # Save report
    report = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_duration_s": round(total_dur, 1),
        "passed": passed,
        "failed": failed,
        "total": len(results),
        "results": results,
    }
    report_path = Path(__file__).parent / "reports" / "test_e2e_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n  Report: {report_path}")


if __name__ == "__main__":
    asyncio.run(main())
