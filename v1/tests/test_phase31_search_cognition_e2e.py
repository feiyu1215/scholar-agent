"""
Phase 31: Search Cognition — Real LLM E2E

核心验证：
    给 Agent 一篇含有可搜索验证的 claim 的论文（Transformer pruning），
    观察 Agent 是否在审阅过程中**自主**决定调用 search_literature。

    这验证的是 COGNITIVE_ANCHOR §4.2 的意图链：
        看到 claim → 产生疑问"这对吗？" → 决定验证 → 需要搜索 → 搜到结果 → 新理解

    植入的可搜索问题：
    1. SOTA claim 对比了 unstructured 方法（类别不匹配）
    2. "Frankle & Carlin, 2018" — 作者/年份有误
    3. "No prior work" on head-level dynamic pruning — DynaBERT 等已存在
    4. 基线数字可疑（Movement Pruning 83.1% 偏低）

    Agent 不需要找到所有问题。关键指标是：它是否**至少一次**主动搜索。

使用:
    python3 tests/test_phase31_search_cognition_e2e.py
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


async def run_search_cognition_e2e():
    """测试 Agent 是否在审阅中自主发起文献搜索。"""

    paper_path = str(PROJECT_ROOT / "tests" / "fixtures" / "paper_with_verifiable_claims.md")

    print("=" * 60)
    print("Phase 31: Search Cognition — Real LLM E2E")
    print(f"Paper: {paper_path}")
    print("测试目标: Agent 是否在审阅 CS 论文时自主调用 search_literature")
    print("植入问题: SOTA overclaim, 引用错误, novelty overclaim, 可疑数字")
    print("Max turns: 15 | Token budget: 80000")
    print("=" * 60)

    agent = ScholarAgent(
        paper_path=paper_path,
        verbose=True,
        max_loop_turns=15,
        token_budget=80000,
    )

    # ──────────────────────────────────────────────────────────
    # 用户意图：审阅这篇论文，特别关注 claims 的可信度
    # ──────────────────────────────────────────────────────────
    user_intent = (
        "请审阅这篇关于 Transformer pruning 的论文。重点关注：\n"
        "1. 方法论的 novelty claim 是否成立\n"
        "2. 实验比较是否公平\n"
        "3. 引用是否准确\n"
        "如果你对某个 claim 不确定，可以搜索文献验证。"
    )

    print(f"\n[User Intent] {user_intent}\n")
    response = await agent.start(user_intent=user_intent)

    print(f"\n{'─' * 50}")
    print(f"[Agent Response]:\n{response[:1200]}")
    print(f"{'─' * 50}")

    findings = agent.get_findings()
    edits = agent.get_edits()
    stats = agent.get_stats()

    # ──────────────────────────────────────────────────────────
    # 获取搜索记录
    # ──────────────────────────────────────────────────────────
    search_history = agent.harness.state.search_history if hasattr(agent.harness.state, 'search_history') else []
    
    # 也从 tool_calls 统计搜索次数
    tool_calls = stats.get("tool_calls", {})
    search_count = tool_calls.get("search_literature", 0)

    # ──────────────────────────────────────────────────────────
    # 验证
    # ──────────────────────────────────────────────────────────
    print(f"\n{'=' * 60}")
    print("[Verification — Phase 31 Core Metrics]")
    print(f"{'─' * 50}")

    # Metric 1: Agent 是否调用了 search_literature
    used_search = search_count > 0
    print(f"  {'✓' if used_search else '✗'} [CRITICAL] Agent called search_literature: {search_count} times")

    # Metric 2: 是否生成了与搜索相关的 findings
    search_related_findings = [
        f for f in findings
        if any(kw in f.get("finding", "").lower() for kw in [
            "search", "搜索", "verify", "验证", "found", "发现",
            "actually", "实际", "incorrect", "错误", "exist", "已有",
            "prior work", "相关工作", "dynab", "lottery"
        ])
    ]
    has_verification_findings = len(search_related_findings) > 0
    print(f"  {'✓' if has_verification_findings else '~'} Findings based on external verification: {len(search_related_findings)}")

    # Metric 3: 是否发现了引用错误
    found_citation_issue = any(
        any(kw in f.get("finding", "").lower() for kw in ["frankle", "carlin", "lottery", "引用", "citation", "author"])
        for f in findings
    )
    print(f"  {'✓' if found_citation_issue else '~'} Detected citation error (Frankle & Carlin): {found_citation_issue}")

    # Metric 4: 是否质疑了 novelty claim
    found_novelty_issue = any(
        any(kw in f.get("finding", "").lower() for kw in ["novelty", "prior work", "first", "novel", "已有", "dynab", "exist"])
        for f in findings
    )
    print(f"  {'✓' if found_novelty_issue else '~'} Questioned novelty claim: {found_novelty_issue}")

    # Metric 5: 是否发现了不公平比较
    found_comparison_issue = any(
        any(kw in f.get("finding", "").lower() for kw in [
            "unstructured", "structured", "unfair", "不公平", "apple", "comparison",
            "sparsegpt", "wanda", "类别"
        ])
        for f in findings
    )
    print(f"  {'✓' if found_comparison_issue else '~'} Identified unfair comparison: {found_comparison_issue}")

    # Metric 6: 总 findings 数量
    print(f"  {'✓' if len(findings) >= 3 else '~'} Total findings: {len(findings)} (expect >= 3)")

    # ──────────────────────────────────────────────────────────
    # 结果汇总
    # ──────────────────────────────────────────────────────────
    print(f"\n[RESULT]")
    if used_search:
        print("  ✅ PASS — Agent autonomously initiated literature search")
        if has_verification_findings:
            print("  ✅ BONUS — Agent's findings reflect search-based verification")
    else:
        print("  ❌ FAIL — Agent did NOT use search_literature")
        print("         This suggests the agent's cognition defaults to internal reasoning only")
        print("         Next step: strengthen search awareness in identity or investigate why")

    # Extra: 列出所有 findings
    if findings:
        print(f"\n[All Findings ({len(findings)})]:")
        for i, f in enumerate(findings, 1):
            priority = f.get("priority", "?")
            status = f.get("status", "?")
            section = f.get("section", "?")
            print(f"  {i}. [{priority}|{status}] (§{section}) {f.get('finding','')[:150]}")

    # Cost
    total_tokens = stats.get("total_tokens", 0)
    estimated_cost = total_tokens / 1_000_000 * 2  # $2/M for gpt-4.1
    print(f"\n[Cost] ~{total_tokens} tokens ≈ ${estimated_cost:.4f}")
    print(f"[Loop turns] {stats.get('loop_turns_total', '?')}")

    print(f"\n[Full tool_calls stats]")
    print(json.dumps(tool_calls, indent=2, ensure_ascii=False))

    # 保存完整结果
    output_path = PROJECT_ROOT / "tests" / "e2e_phase31_search_cognition.json"
    output = {
        "test": "phase31_search_cognition",
        "paper": "paper_with_verifiable_claims.md",
        "metrics": {
            "used_search": used_search,
            "search_count": search_count,
            "verification_findings": len(search_related_findings),
            "citation_issue_found": found_citation_issue,
            "novelty_issue_found": found_novelty_issue,
            "comparison_issue_found": found_comparison_issue,
            "total_findings": len(findings),
        },
        "findings": findings,
        "stats": stats,
        "agent_response": response,
    }
    output_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n[Output saved to] {output_path}")


if __name__ == "__main__":
    asyncio.run(run_search_cognition_e2e())
