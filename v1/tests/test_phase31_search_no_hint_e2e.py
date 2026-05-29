"""
Phase 31 对比实验: 无搜索提示下 Agent 是否自主搜索

与 test_phase31_search_cognition_e2e.py 的区别:
    - 用户 intent 中不提"搜索"或"验证"
    - 只给一个普通的审阅请求
    - 看 Agent 是否在纯认知驱动下自主决定搜索

这验证的是 ANCHOR §2.1 "工具只是手"的真正含义:
    Agent 应该在认知需要时自然使用搜索，不需要外部提示。

使用:
    python3 tests/test_phase31_search_no_hint_e2e.py
"""

import asyncio
import sys
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from core.agent import ScholarAgent


async def run_no_hint_e2e():
    """无搜索提示：Agent 是否仍会自主搜索？"""

    paper_path = str(PROJECT_ROOT / "tests" / "fixtures" / "paper_with_verifiable_claims.md")

    print("=" * 60)
    print("Phase 31 对比实验: 无搜索提示下的自主搜索能力")
    print(f"Paper: {paper_path}")
    print("用户 intent: 纯审稿请求，不提搜索/验证")
    print("Max turns: 15 | Token budget: 80000")
    print("=" * 60)

    agent = ScholarAgent(
        paper_path=paper_path,
        verbose=True,
        max_loop_turns=15,
        token_budget=80000,
    )

    # ──────────────────────────────────────────────────────────
    # 关键区别：用户不提搜索，只说"审阅"
    # ──────────────────────────────────────────────────────────
    user_intent = "请审阅这篇论文，重点关注方法论贡献和实验设计。"

    print(f"\n[User Intent] {user_intent}\n")
    response = await agent.start(user_intent=user_intent)

    print(f"\n{'─' * 50}")
    print(f"[Agent Response]:\n{response[:1200]}")
    print(f"{'─' * 50}")

    findings = agent.get_findings()
    stats = agent.get_stats()
    tool_calls = stats.get("tool_calls", {})
    search_count = tool_calls.get("search_literature", 0)

    # ──────────────────────────────────────────────────────────
    # 结果
    # ──────────────────────────────────────────────────────────
    print(f"\n{'=' * 60}")
    print("[Phase 31 对比实验结果]")

    used_search = search_count > 0
    print(f"  {'✓' if used_search else '✗'} Agent 自主搜索 (无提示): {search_count} 次")
    print(f"  总 findings: {len(findings)}")
    print(f"  Loop turns: {stats.get('loop_turns_total', '?')}")

    if used_search:
        print("\n  ✅ 认知自主性确认 — Agent 不需要外部提示就会搜索")
    else:
        print("\n  ⚠️  认知惰性 — Agent 在没有提示时选择不搜索")
        print("     这说明搜索行为仍依赖外部引导而非内在认知需要")

    # 所有 findings
    if findings:
        print(f"\n[Findings ({len(findings)})]:")
        for i, f in enumerate(findings, 1):
            print(f"  {i}. [{f.get('priority','?')}] {f.get('finding','')[:120]}")

    print(f"\n[Tool calls]")
    print(json.dumps(tool_calls, indent=2, ensure_ascii=False))

    total_tokens = stats.get("total_tokens", 0)
    estimated_cost = total_tokens / 1_000_000 * 2
    print(f"\n[Cost] ~{total_tokens} tokens ≈ ${estimated_cost:.4f}")

    # 保存
    output_path = PROJECT_ROOT / "tests" / "e2e_phase31_no_hint.json"
    output = {
        "test": "phase31_search_no_hint",
        "user_intent": user_intent,
        "metrics": {
            "used_search": used_search,
            "search_count": search_count,
            "total_findings": len(findings),
        },
        "findings": findings,
        "tool_calls": tool_calls,
        "stats": stats,
    }
    output_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n[Output saved to] {output_path}")


if __name__ == "__main__":
    asyncio.run(run_no_hint_e2e())
