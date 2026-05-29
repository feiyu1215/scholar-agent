#!/usr/bin/env python3
"""
Stata MCP Module Tests.

Part A: Zero-cost structural tests (no LLM, no Stata server needed)
  - availability check returns False when no config exists
  - graceful degradation path (unavailable → outputs .do code as guidance)
  - format_stata_result() output for each status type
  - timeout/error handling path

Part B: LLM-powered tests (uses Friday API)
  - generate_stata_code() produces valid .do code for different issue types
  - interpret_stata_output() parses comparison correctly

Rate limiting: 12s between LLM calls (Friday ~10 req/min)
"""
import os, sys, json, asyncio, time, shutil, traceback
import pytest
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

os.environ["SCHOLAR_MAX_CONCURRENT"] = "1"
os.environ["SCHOLAR_MIN_INTERVAL"] = "12"

sys.path.insert(0, str(Path(__file__).parent.parent))

WORKSPACE = Path(__file__).parent / ".workspace"
RATE_LIMIT_DELAY = 15  # cooldown between LLM test sections

results = []


def record(name, passed, dur_ms, detail="", error=""):
    results.append({"name": name, "passed": passed, "dur_ms": dur_ms,
                    "detail": detail, "error": error})
    icon = "✅" if passed else "❌"
    print(f"  {icon} {name} ({dur_ms:.1f}ms)")
    if detail:
        print(f"     {detail[:300]}")
    if error:
        print(f"     ERROR: {error[:300]}")


# ══════════════════════════════════════════════════════════════════════════════
# PART A: Zero-Cost Structural Tests
# ══════════════════════════════════════════════════════════════════════════════

def test_part_a():
    print("\n" + "═" * 60)
    print("  Part A: Zero-Cost Structural Tests (Stata MCP)")
    print("═" * 60)

    from tools.stata_verify import (
        check_stata_availability,
        execute_stata,
        stata_verify,
        format_stata_result,
        STATA_TIMEOUT,
    )

    # ── A1: Availability check ────────────────────────────────────────────
    print("\n[A1] check_stata_availability (no config → False)...")
    t0 = time.time()
    try:
        # Reset cached state to force re-check
        import tools.stata_verify as sv
        sv._stata_available = None

        available = asyncio.run(check_stata_availability())
        dur = (time.time() - t0) * 1000
        record("A1_availability_check",
               passed=(available is False),
               dur_ms=dur,
               detail=f"available={available} (expected False)")
    except Exception as e:
        record("A1_availability_check", False, (time.time()-t0)*1000, error=str(e))

    # ── A2: execute_stata graceful degradation ────────────────────────────
    print("\n[A2] execute_stata (unavailable → graceful response)...")
    t0 = time.time()
    try:
        sv._stata_available = None  # Reset
        result = asyncio.run(execute_stata("display 42"))
        dur = (time.time() - t0) * 1000

        checks = [
            result.get("status") == "unavailable",
            "code" in result,
            result.get("code") == "display 42",
        ]
        record("A2_execute_graceful_degradation",
               passed=all(checks),
               dur_ms=dur,
               detail=f"status={result.get('status')}, has_code={'code' in result}")
    except Exception as e:
        record("A2_execute_graceful_degradation", False, (time.time()-t0)*1000, error=str(e))

    # ── A3: stata_verify full pipeline (unavailable path) ─────────────────
    print("\n[A3] stata_verify full pipeline (unavailable → guidance output)...")
    t0 = time.time()
    try:
        sv._stata_available = None  # Reset

        test_issue = {
            "id": "METH-001",
            "severity": "major",
            "category": "methodology",
            "description": "Sample size may be insufficient for detecting the claimed effect size of 0.15 SD",
            "suggestion": "Conduct ex-post power analysis",
            "needs_statistical_verification": True,
        }

        # We need to mock generate_stata_code to avoid LLM call
        # Instead, test with a pre-generated code string
        # Actually, stata_verify calls generate_stata_code which needs LLM
        # So for Part A, test only the post-generation path via execute + format

        result = asyncio.run(execute_stata("* Power analysis\npower twomeans 0 0.15, n(200)"))
        dur = (time.time() - t0) * 1000

        checks = [
            result["status"] == "unavailable",
            "power twomeans" in result.get("code", ""),
        ]
        record("A3_pipeline_unavailable_path",
               passed=all(checks),
               dur_ms=dur,
               detail=f"status={result['status']}, code_preserved={checks[1]}")
    except Exception as e:
        record("A3_pipeline_unavailable_path", False, (time.time()-t0)*1000, error=str(e))

    # ── A4: format_stata_result for each status ───────────────────────────
    print("\n[A4] format_stata_result (all status types)...")
    t0 = time.time()
    try:
        test_cases = [
            {
                "status": "verified",
                "do_code": "reg y x",
                "do_path": ".workspace/stata/ISS-001.do",
                "stata_output": "coef=0.15",
                "interpretation": {"consistent": True, "paper_claims": "β=0.15", "stata_result": "β=0.15"},
                "guidance": "Stata verification confirms the paper's statistical claims.",
            },
            {
                "status": "discrepancy",
                "do_code": "reg y x",
                "do_path": ".workspace/stata/ISS-002.do",
                "stata_output": "coef=0.08",
                "interpretation": {"consistent": False, "paper_claims": "β=0.15", "stata_result": "β=0.08",
                                   "discrepancy": "Coefficient 0.08 vs claimed 0.15"},
                "guidance": "⚠️ Stata results differ from paper claims.",
            },
            {
                "status": "unavailable",
                "do_code": "power twomeans 0 0.15",
                "do_path": ".workspace/stata/ISS-003.do",
                "interpretation": None,
                "guidance": "Stata MCP not available. Generated .do code saved.",
            },
            {
                "status": "timeout",
                "do_code": "bootstrap, reps(10000): reg y x",
                "do_path": ".workspace/stata/ISS-004.do",
                "error_message": "Execution timed out after 30s",
                "interpretation": None,
                "guidance": "Stata execution failed (timeout).",
            },
            {
                "status": "execution_error",
                "do_code": "invalid_command",
                "do_path": ".workspace/stata/ISS-005.do",
                "error_message": "unrecognized command",
                "interpretation": None,
                "guidance": "Stata execution failed.",
            },
        ]

        all_formatted_ok = True
        for case in test_cases:
            formatted = format_stata_result(case)
            # Basic checks: non-empty, contains status
            if not formatted or case["status"] not in formatted.lower().replace("_", " "):
                # "unavailable" → check icon or word
                if case["status"] == "unavailable" and "📋" not in formatted:
                    all_formatted_ok = False
                elif case["status"] == "verified" and "✅" not in formatted:
                    all_formatted_ok = False
                elif case["status"] == "discrepancy" and "⚠️" not in formatted:
                    all_formatted_ok = False

        dur = (time.time() - t0) * 1000
        record("A4_format_all_status_types",
               passed=all_formatted_ok,
               dur_ms=dur,
               detail=f"Formatted {len(test_cases)} result types, all valid")
    except Exception as e:
        record("A4_format_all_status_types", False, (time.time()-t0)*1000, error=str(e))

    # ── A5: STATA_TIMEOUT constant value ──────────────────────────────────
    print("\n[A5] STATA_TIMEOUT sanity check...")
    t0 = time.time()
    try:
        checks = [
            STATA_TIMEOUT == 30,
            isinstance(STATA_TIMEOUT, int),
        ]
        dur = (time.time() - t0) * 1000
        record("A5_timeout_constant",
               passed=all(checks),
               dur_ms=dur,
               detail=f"STATA_TIMEOUT={STATA_TIMEOUT}s")
    except Exception as e:
        record("A5_timeout_constant", False, (time.time()-t0)*1000, error=str(e))

    # ── A6: .do file saving path logic ────────────────────────────────────
    print("\n[A6] .do file directory creation logic...")
    t0 = time.time()
    try:
        stata_dir = WORKSPACE / "stata"
        # Clean first
        if stata_dir.exists():
            shutil.rmtree(stata_dir)

        # Simulate what stata_verify does (directory creation)
        stata_dir.mkdir(parents=True, exist_ok=True)
        test_do = stata_dir / "METH-TEST.do"
        test_do.write_text("* Test\ndisplay 42\n", encoding="utf-8")

        checks = [
            stata_dir.exists(),
            test_do.exists(),
            test_do.read_text().startswith("* Test"),
        ]
        dur = (time.time() - t0) * 1000
        record("A6_do_file_save_path",
               passed=all(checks),
               dur_ms=dur,
               detail=f"dir={stata_dir.exists()}, file={test_do.exists()}")

        # Cleanup
        if stata_dir.exists():
            shutil.rmtree(stata_dir)
    except Exception as e:
        record("A6_do_file_save_path", False, (time.time()-t0)*1000, error=str(e))

    # ── A7: Red Line enforcement — stata results never auto-modify ────────
    print("\n[A7] Red Line: discrepancy result still says 'guidance' only...")
    t0 = time.time()
    try:
        discrepancy_result = {
            "status": "discrepancy",
            "do_code": "reg y x",
            "do_path": ".workspace/stata/ISS-006.do",
            "stata_output": "coef=-0.02",
            "interpretation": {
                "consistent": False,
                "paper_claims": "positive significant effect (β=0.15, p<0.01)",
                "stata_result": "negative insignificant (β=-0.02, p=0.73)",
                "discrepancy": "Sign reversal and loss of significance",
                "recommendation": "Review data and specification",
            },
            "guidance": (
                "⚠️ Stata results differ from paper claims: Sign reversal\n"
                "[NOTE: Per Red Line 1, this issue remains as GUIDANCE — "
                "the agent will NOT auto-modify the paper's claims.]"
            ),
        }
        formatted = format_stata_result(discrepancy_result)

        checks = [
            "GUIDANCE" in formatted or "guidance" in formatted.lower(),
            "NOT auto-modify" in formatted or "not auto" in formatted.lower(),
            "auto_fix" not in formatted,
            "discrepancy" in formatted.lower() or "⚠️" in formatted,
        ]
        dur = (time.time() - t0) * 1000
        record("A7_red_line_no_auto_modify",
               passed=all(checks),
               dur_ms=dur,
               detail=f"Contains guidance warning: {checks[0]}, No auto_fix: {checks[2]}")
    except Exception as e:
        record("A7_red_line_no_auto_modify", False, (time.time()-t0)*1000, error=str(e))


# ══════════════════════════════════════════════════════════════════════════════
# PART B: LLM-Powered Tests (requires Friday API)
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.skip(reason="Requires Friday LLM API + long rate-limit delays; run manually via __main__")
async def test_part_b():
    print("\n" + "═" * 60)
    print("  Part B: LLM-Powered Tests (Stata MCP + Friday)")
    print("  Model: deepseek-v3-friday | Interval: 12s")
    print("═" * 60)

    from tools.stata_verify import generate_stata_code, interpret_stata_output

    # ── B1: generate_stata_code for DID issue ─────────────────────────────
    print("\n[B1] generate_stata_code (DID parallel trends)...")
    t0 = time.time()
    try:
        did_issue = {
            "id": "METH-DID-001",
            "severity": "major",
            "category": "methodology",
            "description": "No pre-trend test provided for DID estimation. Without evidence of parallel trends, the causal interpretation is threatened.",
            "suggestion": "Add event study plot or formal pre-trend test (e.g., leads test)",
            "needs_statistical_verification": True,
        }
        methods_context = """
        We use a difference-in-differences design exploiting the 2017 minimum wage reform 
        as a quasi-natural experiment. The treatment group consists of firms in industries 
        most affected by the wage increase (manufacturing, retail). Control firms are in 
        high-wage sectors (finance, tech). Panel data spans 2014-2020. 
        Main specification: Y_it = α + β(Treat_i × Post_t) + γ_i + δ_t + ε_it
        """

        code = await generate_stata_code(did_issue, methods_context)
        dur = (time.time() - t0) * 1000

        # Check for essential Stata elements
        code_lower = code.lower()
        checks = [
            len(code) > 50,  # Non-trivial output
            any(kw in code_lower for kw in ["gen", "generate", "forval", "foreach", "reg", "xtreg", "did", "diff"]),
            any(kw in code_lower for kw in ["pre", "trend", "lead", "event", "parallel"]),
            "capture" in code_lower or "cap" in code_lower or "*" in code,  # Has comments or error handling
        ]
        record("B1_generate_did_code",
               passed=all(checks),
               dur_ms=dur,
               detail=f"len={len(code)}, has_stata_cmds={checks[1]}, has_pretrend_ref={checks[2]}")
        if all(checks):
            print(f"     [Preview] {code[:200]}...")
    except Exception as e:
        record("B1_generate_did_code", False, (time.time()-t0)*1000, error=traceback.format_exc()[-300:])

    print(f"\n  [Cooldown] Waiting {RATE_LIMIT_DELAY}s...")
    await asyncio.sleep(RATE_LIMIT_DELAY)

    # ── B2: generate_stata_code for IV exclusion issue ────────────────────
    print("\n[B2] generate_stata_code (IV exclusion restriction)...")
    t0 = time.time()
    try:
        iv_issue = {
            "id": "METH-IV-001",
            "severity": "major",
            "category": "methodology",
            "description": "The instrument (distance to nearest port) may violate the exclusion restriction if proximity to ports affects outcomes through channels other than trade exposure.",
            "suggestion": "Provide over-identification test or falsification tests with placebo outcomes",
            "needs_statistical_verification": True,
        }
        methods_context = """
        We instrument trade exposure using geographic distance to the nearest major port.
        First stage: TradeExposure_i = π * DistPort_i + Controls + ε
        Second stage: Wage_i = β * TradeExposure_hat_i + Controls + ε
        Main specification uses 2SLS. First-stage F-stat = 23.4.
        """

        code = await generate_stata_code(iv_issue, methods_context)
        dur = (time.time() - t0) * 1000

        code_lower = code.lower()
        checks = [
            len(code) > 50,
            any(kw in code_lower for kw in ["ivregress", "ivreg", "2sls", "overid", "sargan", "hansen"]),
            any(kw in code_lower for kw in ["exclusion", "placebo", "falsif", "overid"]),
        ]
        record("B2_generate_iv_code",
               passed=all(checks),
               dur_ms=dur,
               detail=f"len={len(code)}, has_iv_cmds={checks[1]}, has_exclusion_test={checks[2]}")
        if all(checks):
            print(f"     [Preview] {code[:200]}...")
    except Exception as e:
        record("B2_generate_iv_code", False, (time.time()-t0)*1000, error=traceback.format_exc()[-300:])

    print(f"\n  [Cooldown] Waiting {RATE_LIMIT_DELAY}s...")
    await asyncio.sleep(RATE_LIMIT_DELAY)

    # ── B3: generate_stata_code for power analysis ────────────────────────
    print("\n[B3] generate_stata_code (power analysis)...")
    t0 = time.time()
    try:
        power_issue = {
            "id": "METH-PWR-001",
            "severity": "moderate",
            "category": "methodology",
            "description": "With N=150 per group and claimed effect size of 0.2 SD, statistical power may be below 80% threshold.",
            "suggestion": "Report ex-post power calculation or MDE at current sample size",
            "needs_statistical_verification": True,
        }
        methods_context = """
        Our sample consists of 300 firms (150 treatment, 150 control).
        The estimated treatment effect is 0.2 standard deviations (p=0.047).
        """

        code = await generate_stata_code(power_issue, methods_context)
        dur = (time.time() - t0) * 1000

        code_lower = code.lower()
        checks = [
            len(code) > 30,
            any(kw in code_lower for kw in ["power", "sampsi", "sample", "mde"]),
            any(kw in code_lower for kw in ["150", "0.2", "n("]),
        ]
        record("B3_generate_power_code",
               passed=all(checks),
               dur_ms=dur,
               detail=f"len={len(code)}, has_power_cmd={checks[1]}, has_params={checks[2]}")
    except Exception as e:
        record("B3_generate_power_code", False, (time.time()-t0)*1000, error=traceback.format_exc()[-300:])

    print(f"\n  [Cooldown] Waiting {RATE_LIMIT_DELAY}s...")
    await asyncio.sleep(RATE_LIMIT_DELAY)

    # ── B4: interpret_stata_output (consistent result) ────────────────────
    print("\n[B4] interpret_stata_output (consistent → verified)...")
    t0 = time.time()
    try:
        issue = {
            "id": "METH-REG-001",
            "description": "Paper claims β=0.15 (p<0.01) for trade exposure effect on wages",
            "suggestion": "Verify coefficient and significance level",
        }
        fake_stata_output = """
. reg wage trade_exposure controls, robust
                    
      Source |       SS           df       MS      
-------------+----------------------------------
       Model |  234.5678       3   78.189
    Residual |  1456.789     296    4.921
-------------+----------------------------------
       Total |  1691.357     299    5.656

trade_exposure |   .1523    .0412     3.70   0.000
     _cons     |  2.341    .156     15.01   0.000
"""
        interp = await interpret_stata_output(issue, fake_stata_output)
        dur = (time.time() - t0) * 1000

        checks = [
            isinstance(interp, dict),
            interp.get("consistent") is True or "0.15" in str(interp.get("stata_result", "")),
            "parse_error" not in interp or interp.get("parse_error") is not True,
        ]
        record("B4_interpret_consistent",
               passed=all(checks),
               dur_ms=dur,
               detail=f"consistent={interp.get('consistent')}, keys={list(interp.keys())[:5]}")
    except Exception as e:
        record("B4_interpret_consistent", False, (time.time()-t0)*1000, error=traceback.format_exc()[-300:])

    print(f"\n  [Cooldown] Waiting {RATE_LIMIT_DELAY}s...")
    await asyncio.sleep(RATE_LIMIT_DELAY)

    # ── B5: interpret_stata_output (discrepancy result) ───────────────────
    print("\n[B5] interpret_stata_output (discrepancy → flagged)...")
    t0 = time.time()
    try:
        issue = {
            "id": "METH-REG-002",
            "description": "Paper claims positive effect β=0.15 (p<0.01) but robustness check shows sensitivity",
            "suggestion": "Test with alternative specifications",
        }
        fake_stata_output = """
. reg wage trade_exposure controls if year >= 2015, robust
                    
trade_exposure |  -.0234    .0567    -0.41   0.680
     _cons     |  2.891    .203     14.24   0.000

Note: Using post-2015 subsample only. Effect reverses sign and loses significance.
"""
        interp = await interpret_stata_output(issue, fake_stata_output)
        dur = (time.time() - t0) * 1000

        checks = [
            isinstance(interp, dict),
            interp.get("consistent") is False or interp.get("discrepancy") is not None,
            "parse_error" not in interp or interp.get("parse_error") is not True,
        ]
        record("B5_interpret_discrepancy",
               passed=all(checks),
               dur_ms=dur,
               detail=f"consistent={interp.get('consistent')}, discrepancy={str(interp.get('discrepancy', ''))[:100]}")
    except Exception as e:
        record("B5_interpret_discrepancy", False, (time.time()-t0)*1000, error=traceback.format_exc()[-300:])


# ══════════════════════════════════════════════════════════════════════════════
# Main runner
# ══════════════════════════════════════════════════════════════════════════════

def run():
    print("=" * 60)
    print("  Stata MCP Module — Full Test Suite")
    print("=" * 60)

    # Part A: Zero-cost (always run)
    test_part_a()

    # Part B: LLM tests
    print(f"\n  [Part B prep] Waiting {RATE_LIMIT_DELAY}s before LLM tests...")
    time.sleep(RATE_LIMIT_DELAY)
    asyncio.run(test_part_b())

    # ── Summary ───────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    failed = total - passed

    print(f"  RESULTS: {passed}/{total} passed", end="")
    if failed:
        print(f" ({failed} FAILED)")
        for r in results:
            if not r["passed"]:
                print(f"    ❌ {r['name']}: {r.get('error', r.get('detail', ''))[:200]}")
    else:
        print(" ✅ ALL PASSED")

    print("=" * 60)

    # Save report
    report_path = Path(__file__).parent / "reports" / "test_stata_mcp_report.json"
    report_path.write_text(json.dumps({
        "total": total, "passed": passed, "failed": failed,
        "results": results
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n  Report saved: {report_path}")

    return failed == 0


if __name__ == "__main__":
    success = run()
    sys.exit(0 if success else 1)
