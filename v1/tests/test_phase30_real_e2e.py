"""
Phase 30: Real LLM E2E — 验证 Agent 在用户请求修改时是否选择行动

核心验证：
    给 Agent 一篇有明显问题的论文，先让它审阅，然后用户说"帮我改"。
    观察 Agent 是否调用 edit_section（行动）而非 talk_to_user（建议）。

    这是 Phase 30 "行动优于建议" 认知注入的真正检验——
    在 Phase 29 的测试中，Agent 对"帮我改一下"给出了文字建议而非行动。
    Phase 30 强化了 §15 的认知描述，这个测试验证强化是否有效。

使用:
    python3 tests/test_phase30_real_e2e.py
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


async def run_action_over_suggestion_e2e():
    """测试 Agent 在明确修改请求下是否选择行动。"""

    paper_path = str(PROJECT_ROOT / "examples" / "sample_paper.md")

    print("=" * 60)
    print("Phase 30: Action Over Suggestion — Real LLM E2E")
    print(f"Paper: {paper_path}")
    print("测试目标: Agent 收到'帮我改'后是否调用 edit_section")
    print("Max turns: 10 per round | Token budget: 60000")
    print("=" * 60)

    agent = ScholarAgent(
        paper_path=paper_path,
        verbose=True,
        max_loop_turns=10,
        token_budget=60000,
    )

    # ──────────────────────────────────────────────────────────
    # 第一轮：聚焦审阅 abstract（给出具体的、可修改的问题）
    # ──────────────────────────────────────────────────────────
    round1_intent = (
        "请快速审阅 abstract 和 introduction，找出最明显的 overclaim 或逻辑问题。"
        "不需要读全文，找到 1-2 个具体问题就可以向我报告。"
    )

    print(f"\n[User Round 1] {round1_intent}\n")
    response1 = await agent.start(user_intent=round1_intent)

    print(f"\n{'─' * 50}")
    print(f"[Agent Response #1]:\n{response1[:600]}")
    print(f"{'─' * 50}")

    findings_r1 = agent.get_findings()
    edits_r1 = agent.get_edits()
    stats_r1 = agent.get_stats()

    print(f"\n[Stats after round 1]")
    print(f"  Findings: {len(findings_r1)}")
    print(f"  Edits: {len(edits_r1)}")
    print(f"  Loop turns: {stats_r1.get('loop_turns_total', '?')}")

    if findings_r1:
        print(f"\n[Findings]:")
        for i, f in enumerate(findings_r1, 1):
            print(f"  {i}. [{f.get('priority','?')}] {f.get('finding','')[:120]}")

    # ──────────────────────────────────────────────────────────
    # 第二轮：明确的修改请求
    # ──────────────────────────────────────────────────────────
    round2_message = (
        "好的，请直接帮我改一下 abstract 中的问题。"
        "不用问我意见，直接改——你是专家，我信任你的判断。"
    )

    print(f"\n{'=' * 60}")
    print(f"[User Round 2] {round2_message}\n")
    response2 = await agent.chat(round2_message)

    print(f"\n{'─' * 50}")
    print(f"[Agent Response #2]:\n{response2[:800]}")
    print(f"{'─' * 50}")

    findings_r2 = agent.get_findings()
    edits_r2 = agent.get_edits()
    stats_r2 = agent.get_stats()

    print(f"\n[Stats after round 2]")
    print(f"  Total findings: {len(findings_r2)}")
    print(f"  Total edits: {len(edits_r2)}")
    print(f"  Conversation turns: {stats_r2.get('conversation_turns', '?')}")

    if edits_r2:
        print(f"\n[Edits made]:")
        for i, e in enumerate(edits_r2, 1):
            print(f"  {i}. Section: {e.get('section','?')}")
            print(f"     Reason: {e.get('reason','?')[:150]}")

    # ──────────────────────────────────────────────────────────
    # 验证
    # ──────────────────────────────────────────────────────────
    print(f"\n{'=' * 60}")
    print("[Verification — Phase 30 Core Metrics]")

    # Metric 1: Agent 在第二轮中是否使用了 edit_section
    used_edit = len(edits_r2) > 0
    print(f"  {'✓' if used_edit else '✗'} [CRITICAL] Agent used edit_section: {len(edits_r2)} edits")

    # Metric 2: Agent 没有仅用 talk 给建议（如果有 edit 就算通过）
    suggestion_only = len(edits_r2) == 0 and "建议" in response2
    print(f"  {'✓' if not suggestion_only else '✗'} [CRITICAL] Agent did NOT fall into suggestion-only mode")

    # Metric 3: abstract 的内容是否真的被修改了
    if edits_r2:
        abstract_edited = any("abstract" in e.get("section", "").lower() for e in edits_r2)
        print(f"  {'✓' if abstract_edited else '~'} Abstract specifically was edited: {abstract_edited}")

    # Metric 4: edit 有 reason
    edits_with_reason = [e for e in edits_r2 if e.get("reason")]
    print(f"  {'✓' if len(edits_with_reason) == len(edits_r2) else '~'} All edits have reason: {len(edits_with_reason)}/{len(edits_r2)}")

    # 总结
    print(f"\n[RESULT]")
    if used_edit and not suggestion_only:
        print("  ✅ PASS — Agent chose ACTION over suggestion when user requested modification")
    elif used_edit:
        print("  ⚠️  PARTIAL — Agent used edit but also gave suggestions")
    else:
        print("  ❌ FAIL — Agent fell back to suggestion-only mode (Phase 29 anti-pattern persists)")

    # Cost
    total_tokens = stats_r2.get("total_tokens", 0)
    estimated_cost = total_tokens / 1_000_000 * 2  # rough estimate $2/M tokens for gpt-4.1
    print(f"\n[Cost] ~{total_tokens} tokens ≈ ${estimated_cost:.4f}")

    print(f"\n[Full stats]")
    print(json.dumps(stats_r2, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(run_action_over_suggestion_e2e())
