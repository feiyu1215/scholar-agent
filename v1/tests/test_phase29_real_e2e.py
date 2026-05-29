"""
Phase 29: Real LLM E2E — 测试 Agent 是否会主动与用户交流

场景设计：
    给 Agent 一个模糊的审阅意图（不指定重点方向），观察 Agent 是否会：
    1. 主动 talk_to_user 确认方向（理想）
    2. 或者自主决定方向后直接审完（也可接受）
    
    核心验证的不是"必须 talk"——而是验证 talk_to_user 路径在 real LLM 下不会出错。

使用:
    python3 tests/test_phase29_real_e2e.py
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


async def run_multi_turn_e2e():
    """运行多轮对话 E2E 测试。"""

    paper_path = str(PROJECT_ROOT / "examples" / "sample_paper.md")

    print("=" * 60)
    print("Phase 29: Multi-turn Dialogue E2E")
    print(f"Paper: {paper_path}")
    print("Intent: 模糊意图（不指定重点）")
    print("Max turns: 12 | Token budget: 60000")
    print("=" * 60)

    agent = ScholarAgent(
        paper_path=paper_path,
        verbose=True,
        max_loop_turns=12,
        token_budget=60000,
    )

    # 第一轮：模糊意图
    print("\n[User] 请审阅这篇论文，给我一些建设性的反馈。\n")
    response = await agent.start(user_intent="请审阅这篇论文，给我一些建设性的反馈。")

    print(f"\n{'─' * 50}")
    print(f"[Agent Response #1]:\n{response[:500]}")
    print(f"{'─' * 50}")

    # 检查 Agent 是否返回了 LoopTalk（即它主动要和用户对话）
    # 如果是，模拟用户回复
    stats = agent.get_stats()
    findings = agent.get_findings()
    
    print(f"\n[Stats after round 1]")
    print(f"  Findings: {len(findings)}")
    print(f"  Loop turns: {stats.get('loop_turns', '?')}")
    print(f"  Conversation turns: {stats.get('conversation_turns', '?')}")

    if findings:
        print(f"\n[Findings after round 1]:")
        for i, f in enumerate(findings, 1):
            print(f"  {i}. [{f.get('priority','?')}] {f.get('finding','')[:100]}")

    # 第二轮：用户追问
    print(f"\n{'=' * 60}")
    print("[User] 你发现的最严重问题是什么？能帮我改一下吗？\n")
    response2 = await agent.chat("你发现的最严重问题是什么？能帮我改一下吗？")

    print(f"\n{'─' * 50}")
    print(f"[Agent Response #2]:\n{response2[:500]}")
    print(f"{'─' * 50}")

    findings2 = agent.get_findings()
    edits = agent.get_edits()
    stats2 = agent.get_stats()

    print(f"\n[Stats after round 2]")
    print(f"  Total findings: {len(findings2)}")
    print(f"  Total edits: {len(edits)}")
    print(f"  Conversation turns: {stats2.get('conversation_turns', '?')}")

    # 验证结果
    print(f"\n{'=' * 60}")
    print("[Verification]")
    print(f"  ✓ Agent started successfully")
    print(f"  ✓ Multi-turn dialogue completed (2 rounds)")
    print(f"  {'✓' if len(findings2) > 0 else '✗'} Agent produced findings: {len(findings2)}")
    print(f"  {'✓' if stats2.get('conversation_turns', 0) >= 1 else '✗'} Conversation turns tracked")
    
    # 认知连贯性验证：第二轮的回复是否引用了第一轮的 findings
    has_reference = any(
        f.get("finding", "")[:30] in response2 
        for f in findings
    ) if findings else False
    print(f"  {'✓' if has_reference or len(findings2) > len(findings) else '~'} Cognitive continuity (references prior findings or adds new ones)")
    
    print(f"\n[Full stats]")
    print(json.dumps(stats2, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(run_multi_turn_e2e())
