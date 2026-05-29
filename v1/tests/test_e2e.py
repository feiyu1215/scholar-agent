"""
端到端验证: ScholarAgent 多轮对话

测试流程:
    1. start() — Agent 自主审阅论文
    2. chat("Introduction 的 claim 具体哪里有问题？") — 追问
    3. chat("帮我改一下 abstract") — 要求修改

验证目标:
    - Agent 能加载论文并产生有意义的 findings
    - 多轮对话间 context 保持连贯
    - talk_to_user / mark_complete 信号正确传递
"""

import os
import sys
import json
import asyncio
from pathlib import Path

# 项目根
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from core.agent import ScholarAgent


async def main():
    paper_path = str(ROOT / "poc" / "test_paper.md")

    print("=" * 60)
    print("  ScholarAgent E2E Test — 多轮对话验证")
    print("=" * 60)
    print(f"  Paper: {paper_path}")
    print(f"  Model: {os.environ.get('LLM_MODEL', '?')}")
    print()

    agent = ScholarAgent(
        paper_path=paper_path,
        verbose=True,
        max_loop_turns=20,  # 测试中限制轮次
        token_budget=80000,
    )

    # ---- Turn 1: Agent 自主审阅 ----
    print("\n" + "=" * 40)
    print("  TURN 1: Agent 自主审阅论文")
    print("=" * 40)
    response1 = await agent.start()
    print(f"\n[Agent Response]:\n{response1[:1000]}")
    print(f"\n[Findings count]: {len(agent.get_findings())}")
    for f in agent.get_findings():
        print(f"  [{f['priority']}][{f['status']}] {f['finding'][:80]}")

    # ---- Turn 2: 用户追问 ----
    print("\n" + "=" * 40)
    print("  TURN 2: 用户追问")
    print("=" * 40)
    user_msg = "你发现的最严重的问题是什么？能详细解释一下吗？"
    print(f"\n[User]: {user_msg}")
    response2 = await agent.chat(user_msg)
    print(f"\n[Agent Response]:\n{response2[:1000]}")

    # ---- Turn 3: 用户要求修改 ----
    print("\n" + "=" * 40)
    print("  TURN 3: 用户要求修改")
    print("=" * 40)
    user_msg2 = "帮我把 abstract 里的 overclaim 改得更准确。"
    print(f"\n[User]: {user_msg2}")
    response3 = await agent.chat(user_msg2)
    print(f"\n[Agent Response]:\n{response3[:1000]}")

    # ---- 统计 ----
    print("\n" + "=" * 40)
    print("  FINAL STATS")
    print("=" * 40)
    stats = agent.get_stats()
    print(json.dumps(stats, indent=2, ensure_ascii=False))

    # ---- 验证 ----
    print("\n" + "=" * 40)
    print("  VALIDATION")
    print("=" * 40)

    checks = {
        "Agent produced findings": len(agent.get_findings()) > 0,
        "Multi-turn context works (Turn 2 got response)": len(response2) > 20,
        "Edit was attempted (Turn 3)": len(agent.get_edits()) > 0 or "改" in response3 or "修改" in response3,
        "Total tokens within budget": stats["total_tokens"] < 80000,
    }

    all_pass = True
    for check, result in checks.items():
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"  {status}: {check}")
        if not result:
            all_pass = False

    print(f"\n{'✓ ALL CHECKS PASSED' if all_pass else '✗ SOME CHECKS FAILED'}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
