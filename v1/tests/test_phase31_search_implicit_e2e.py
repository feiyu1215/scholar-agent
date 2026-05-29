"""
Phase 31 验证实验 #3: 隐式触发搜索的 intent

用户说"帮我确认这篇论文的 novelty claim 是否成立"——
这要求 Agent 判断"是否真的没有 prior work"。
一个负责任的审稿人不会仅凭自己的记忆下结论，而会搜索验证。

不提"搜索"二字，但 intent 本身暗示需要外部验证。

使用:
    python3 tests/test_phase31_search_implicit_e2e.py
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


async def run_implicit_trigger_e2e():
    """隐式搜索触发：Intent 暗示需要验证但不提搜索。"""

    paper_path = str(PROJECT_ROOT / "tests" / "fixtures" / "paper_with_verifiable_claims.md")

    print("=" * 60)
    print("Phase 31 验证 #3: 隐式搜索触发")
    print(f"Paper: {paper_path}")
    print("Intent: 暗示需要外部验证（确认 novelty），但不提搜索")
    print("Max turns: 15 | Token budget: 80000")
    print("=" * 60)

    agent = ScholarAgent(
        paper_path=paper_path,
        verbose=True,
        max_loop_turns=15,
        token_budget=80000,
    )

    # 关键：不提搜索，但 intent 暗含"需要确认 novelty 是否成立"
    user_intent = (
        "这篇论文声称是第一个做 head-level dynamic pruning 的工作。"
        "帮我确认这个 novelty claim 是否成立——是否真的没有人做过类似的事。"
        "另外引用部分有没有错误，作者名和年份对不对。"
    )

    print(f"\n[User Intent] {user_intent}\n")
    response = await agent.start(user_intent=user_intent)

    print(f"\n{'─' * 50}")
    print(f"[Agent Response]:\n{response[:1200]}")
    print(f"{'─' * 50}")

    findings = agent.get_findings()
    stats = agent.get_stats()
    tool_calls = stats.get("tool_calls", {})
    search_count = tool_calls.get("search_literature", 0)

    print(f"\n{'=' * 60}")
    print("[Phase 31 验证 #3 结果]")

    used_search = search_count > 0
    print(f"  {'✓' if used_search else '✗'} Agent 搜索了: {search_count} 次")
    print(f"  总 findings: {len(findings)}")
    print(f"  Loop turns: {stats.get('loop_turns_total', '?')}")

    # 发现引用错误？
    found_citation = any(
        any(kw in f.get("finding", "").lower() for kw in ["frankle", "carlin", "lottery", "author", "作者"])
        for f in findings
    )
    print(f"  {'✓' if found_citation else '✗'} 发现引用错误: {found_citation}")

    if used_search:
        print("\n  ✅ Agent 在 novelty 验证场景下自主搜索")
    else:
        print("\n  ⚠️  Agent 未搜索——仍依赖内部知识判断")

    if findings:
        print(f"\n[Findings ({len(findings)})]:")
        for i, f in enumerate(findings, 1):
            print(f"  {i}. [{f.get('priority','?')}] {f.get('finding','')[:150]}")

    print(f"\n[Tool calls]")
    print(json.dumps(tool_calls, indent=2, ensure_ascii=False))

    total_tokens = stats.get("total_tokens", 0)
    estimated_cost = total_tokens / 1_000_000 * 2
    print(f"\n[Cost] ~{total_tokens} tokens ≈ ${estimated_cost:.4f}")


if __name__ == "__main__":
    asyncio.run(run_implicit_trigger_e2e())
