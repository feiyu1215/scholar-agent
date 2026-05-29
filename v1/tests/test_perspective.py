"""
端到端验证: 视角分裂 (Perspective Split)

测试目标:
    1. Agent 在审阅中是否会自主触发 spawn_perspective
    2. 子视角是否独立运行并产出有意义的 findings
    3. 子视角的 findings 是否被正确注入主 Agent 的 state
    4. 视角分裂是否帮助发现了单视角遗漏的问题

测试策略:
    - 给 Agent 一个明确的 user_intent 暗示需要统计视角
    - 观察 Agent 是否自主使用 spawn_perspective
    - 如果 Agent 没有自主使用，用 chat() 明确要求它使用
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
    print("  ScholarAgent — Perspective Split E2E Test")
    print("=" * 60)
    print(f"  Paper: {paper_path}")
    print(f"  Model: {os.environ.get('LLM_MODEL', 'gpt-4.1-mini')}")
    print()

    agent = ScholarAgent(
        paper_path=paper_path,
        verbose=True,
        max_loop_turns=25,
        token_budget=120000,
    )

    # ---- Turn 1: 用暗示性意图启动 ----
    print("\n" + "=" * 40)
    print("  TURN 1: Agent 审阅（暗示需要关注统计方面）")
    print("=" * 40)

    # 这个 intent 暗示了需要统计视角审视
    user_intent = (
        "请帮我审阅这篇论文。特别注意：(1) 数据和 claim 的一致性，"
        "(2) 实验设计的方法论问题，(3) 统计显著性和样本量是否足够支撑结论。"
        "如果你觉得某些方面需要专门的视角来审视，可以使用视角分裂。"
    )
    print(f"\n[User Intent]: {user_intent}")

    response1 = await agent.start(user_intent=user_intent)
    print(f"\n[Agent Response]:\n{response1[:1500]}")

    # ---- 检查是否自主触发了分裂 ----
    findings = agent.get_findings()
    perspective_findings = [f for f in findings if f.get("perspective")]

    print(f"\n[Findings总数]: {len(findings)}")
    print(f"[来自视角分裂的findings]: {len(perspective_findings)}")

    for f in findings:
        perspective_tag = f" [视角: {f['perspective']}]" if f.get("perspective") else ""
        print(f"  [{f['priority']}][{f['status']}]{perspective_tag} {f['finding'][:100]}")

    # ---- Turn 2: 如果没自主分裂，明确要求 ----
    if not perspective_findings:
        print("\n" + "=" * 40)
        print("  TURN 2: 明确要求统计视角审视")
        print("=" * 40)
        user_msg = (
            "请用 spawn_perspective 发起一个统计方法专家的独立视角，"
            "让它专门审查 experiments section 的统计方法：样本量是否足够？"
            "1-shot 和 5-shot 的差异是否有统计显著性？是否缺少置信区间？"
        )
        print(f"\n[User]: {user_msg}")
        response2 = await agent.chat(user_msg)
        print(f"\n[Agent Response]:\n{response2[:1500]}")

        # 重新检查
        findings = agent.get_findings()
        perspective_findings = [f for f in findings if f.get("perspective")]
        print(f"\n[更新后 Findings总数]: {len(findings)}")
        print(f"[来自视角分裂的findings]: {len(perspective_findings)}")

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
        "Agent produced findings": len(findings) > 0,
        "Perspective split was used": len(perspective_findings) > 0,
        "Sub-perspective findings have content": all(
            f.get("finding") for f in perspective_findings
        ) if perspective_findings else False,
        "Total tokens within budget": stats["total_tokens"] < 120000,
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
