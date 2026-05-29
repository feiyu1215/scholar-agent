#!/usr/bin/env python3
"""
Multi-scenario test runner for ask_user behavior analysis.

Runs 4 scenarios sequentially with different prompts/budgets to measure
when and why the agent triggers ask_user.

Usage:
    python test_scenarios.py --model LongCat-Flash-Chat
"""

import os
import sys
import json
import time
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent.parent


# ============================================================
# Scenario Definitions
# ============================================================

SCENARIOS = [
    {
        "id": "S1_ambiguous",
        "name": "模糊指令 (Ambiguous Instruction)",
        "description": "极其模糊的指令，不明确要审哪些维度、不确定论文语言",
        "budget": "minimal",
        "prompt": "帮我看看这篇论文。",
        "max_turns": 20,
        "expect_ask_user": True,
        "rationale": "Agent 没有明确审稿维度，可能问用户想要什么类型的反馈",
    },
    {
        "id": "S2_full_budget_rewrite",
        "name": "Full Budget 改写模式 (Confirm Fix)",
        "description": "full budget 下要求修改论文，触发 confirm_fix 的用户确认流程",
        "budget": "full",
        "prompt": (
            "论文已经在 .workspace/paper/ 目录中完成解析。"
            "请帮我修改论文的 Abstract 部分，使其更加简洁有力。"
            "直接帮我改写，不需要先做全面审稿。"
        ),
        "max_turns": 20,
        "expect_ask_user": True,
        "rationale": "full budget + rewrite = confirm_fix 触发，agent 需要用户确认改写方案",
    },
    {
        "id": "S3_conflict_choice",
        "name": "多路径冲突选择 (Conflicting Paths)",
        "description": "给出矛盾需求，让 agent 面对无法同时满足的多路径",
        "budget": "minimal",
        "prompt": (
            "论文已经在 .workspace/paper/ 目录中完成解析。"
            "我有两个互相矛盾的需求：\n"
            "1. 审稿人 A 说我的方法论部分太长了，需要大幅删减\n"
            "2. 审稿人 B 说我的方法论部分缺少细节，需要补充说明\n"
            "请帮我决定应该听谁的，然后给出修改建议。"
        ),
        "max_turns": 20,
        "expect_ask_user": True,
        "rationale": "两个矛盾需求，agent 需要决定优先级或问用户偏好",
    },
    {
        "id": "S4_clear_minimal",
        "name": "对照组: 明确指令 + minimal (Control)",
        "description": "清晰明确的审稿指令，minimal 模式，预期不需要 ask_user",
        "budget": "minimal",
        "prompt": (
            "论文已经在 .workspace/paper/ 目录中完成解析，你可以直接用 read_section_index 查看章节索引。"
            "请帮我全面审稿这篇关于国家自主创新示范区(NIDZ)政策效果的经济学论文。"
            "请从以下维度进行评审：\n"
            "1. 研究设计与因果识别策略的严谨性\n"
            "2. 实证方法（Staggered DID, PSM-DID, CSDID）的合理性\n"
            "3. 论文结构与逻辑流\n"
            "4. 文献综述的覆盖度和定位\n"
            "5. 结论与政策建议的合理性\n"
            "请给出详细的审稿意见。"
        ),
        "max_turns": 30,
        "expect_ask_user": False,
        "rationale": "指令完全明确，不存在歧义，agent 应自主完成",
    },
]


def clean_state():
    """Remove recall cache, goal state, review output."""
    (ROOT / ".workspace" / "recall" / "tool_results.json").unlink(missing_ok=True)
    (ROOT / ".workspace" / ".goal_state.json").unlink(missing_ok=True)
    checkpoints_dir = ROOT / ".workspace" / "checkpoints"
    if checkpoints_dir.exists():
        for f in checkpoints_dir.glob("*.json"):
            f.unlink()
    review_dir = ROOT / ".workspace" / "review"
    if review_dir.exists():
        for f in review_dir.glob("*"):
            f.unlink()


def run_scenario(scenario: dict, model: str, rate_limit: float) -> dict:
    """Run a single scenario and return the report."""
    scenario_id = scenario["id"]
    output_file = f"test_report_{scenario_id}.json"

    print(f"\n{'=' * 70}")
    print(f"  SCENARIO: {scenario['name']}")
    print(f"  Budget: {scenario['budget']} | Expect ask_user: {scenario['expect_ask_user']}")
    print(f"  Rationale: {scenario['rationale']}")
    print(f"{'=' * 70}\n")

    # Clean state
    clean_state()

    # Build command
    cmd = [
        sys.executable, str(ROOT / "test_harness.py"),
        "--budget", scenario["budget"],
        "--model", model,
        "--max-turns", str(scenario["max_turns"]),
        "--output", output_file,
        "--prompt", scenario["prompt"],
    ]

    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["RATE_LIMIT_DELAY"] = str(rate_limit)

    t0 = time.time()
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=600,
        cwd=str(ROOT), env=env,
    )
    elapsed = time.time() - t0

    # Print output for monitoring
    if result.stdout:
        # Print last 30 lines of stdout
        lines = result.stdout.strip().split("\n")
        for line in lines[-40:]:
            print(line)
    if result.returncode != 0 and result.stderr:
        print(f"\n[STDERR]: {result.stderr[-500:]}")

    # Load report
    report_path = ROOT / output_file
    if report_path.exists():
        report = json.loads(report_path.read_text(encoding="utf-8"))
    else:
        report = {"error": "No report generated", "returncode": result.returncode}

    report["_scenario"] = {
        "id": scenario_id,
        "name": scenario["name"],
        "budget": scenario["budget"],
        "expect_ask_user": scenario["expect_ask_user"],
        "actual_elapsed": round(elapsed, 1),
    }

    return report


def print_summary(results: list):
    """Print a summary comparison table."""
    print("\n\n" + "=" * 80)
    print("  ALL SCENARIOS SUMMARY")
    print("=" * 80)
    print(f"\n{'Scenario':<35} {'Budget':<8} {'ask_user':<10} {'Expected':<10} {'Match':<6} {'Tools':<6} {'Time':<8}")
    print("-" * 80)

    for r in results:
        sc = r.get("_scenario", {})
        ask_count = r.get("ask_user_metrics", {}).get("total_count", "?")
        expected = sc.get("expect_ask_user", "?")
        match = "✓" if (ask_count > 0) == expected else "✗"
        tools = r.get("tool_metrics", {}).get("total_calls", "?")
        elapsed = sc.get("actual_elapsed", "?")
        name = sc.get("name", "?")[:33]
        budget = sc.get("budget", "?")
        print(f"  {name:<33} {budget:<8} {ask_count:<10} {expected!s:<10} {match:<6} {tools:<6} {elapsed:<8}")

    print("-" * 80)

    # Detailed ask_user analysis
    print("\n\n  DETAILED ask_user ANALYSIS:")
    print("-" * 60)
    for r in results:
        sc = r.get("_scenario", {})
        calls = r.get("ask_user_metrics", {}).get("calls", [])
        print(f"\n  [{sc.get('id', '?')}] {sc.get('name', '?')}")
        if calls:
            for i, call in enumerate(calls, 1):
                q = call.get("question", "")[:100]
                a = call.get("response", "")[:80]
                print(f"    Q{i}: {q}")
                print(f"    A{i}: {a}")
        else:
            print(f"    (no ask_user calls)")

    # Final output previews
    print("\n\n  FINAL OUTPUT PREVIEWS (first 300 chars):")
    print("-" * 60)
    for r in results:
        sc = r.get("_scenario", {})
        preview = r.get("final_output_preview", "")[:300]
        print(f"\n  [{sc.get('id', '?')}]:")
        print(f"    {preview}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Multi-scenario ask_user test")
    parser.add_argument("--model", default="LongCat-Flash-Chat", help="Model to use")
    parser.add_argument("--rate-limit", type=float, default=2.0, help="Seconds between LLM calls")
    parser.add_argument("--scenarios", nargs="*", default=None,
                        help="Run specific scenarios by ID (e.g., S1_ambiguous S3_conflict_choice)")
    args = parser.parse_args()

    # Filter scenarios if specified
    scenarios_to_run = SCENARIOS
    if args.scenarios:
        scenarios_to_run = [s for s in SCENARIOS if s["id"] in args.scenarios]
        if not scenarios_to_run:
            print(f"No matching scenarios found. Available: {[s['id'] for s in SCENARIOS]}")
            sys.exit(1)

    print(f"\nRunning {len(scenarios_to_run)} scenarios with model={args.model}, rate_limit={args.rate_limit}s")
    print(f"Scenarios: {[s['id'] for s in scenarios_to_run]}\n")

    results = []
    for scenario in scenarios_to_run:
        try:
            report = run_scenario(scenario, args.model, args.rate_limit)
            results.append(report)
        except subprocess.TimeoutExpired:
            results.append({
                "error": "Timeout (600s)",
                "_scenario": {
                    "id": scenario["id"],
                    "name": scenario["name"],
                    "budget": scenario["budget"],
                    "expect_ask_user": scenario["expect_ask_user"],
                    "actual_elapsed": 600.0,
                },
                "ask_user_metrics": {"total_count": 0, "calls": []},
                "tool_metrics": {"total_calls": 0},
            })
        except Exception as e:
            results.append({
                "error": str(e),
                "_scenario": {
                    "id": scenario["id"],
                    "name": scenario["name"],
                    "budget": scenario["budget"],
                    "expect_ask_user": scenario["expect_ask_user"],
                    "actual_elapsed": 0,
                },
                "ask_user_metrics": {"total_count": 0, "calls": []},
                "tool_metrics": {"total_calls": 0},
            })

    # Print summary
    print_summary(results)

    # Save full results
    full_report_path = ROOT / "test_scenarios_full_report.json"
    full_report_path.write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"\n\n  Full report saved to: {full_report_path}")


if __name__ == "__main__":
    main()
