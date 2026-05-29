"""
端到端验证: 用真实 51-section 经济学论文测试 ScholarAgent。

测试目标:
    1. Agent 能加载长论文（51 sections, ~138k chars）
    2. Token Pipeline 生效 — 不注入全文，按需读取
    3. Agent 自主审阅，调用 read_section / search_literature
    4. 产生有意义的 findings
    5. 用 talk_to_user 和用户沟通

运行: python3 -m core.test_real_paper
"""

import asyncio
import sys
import os
import time
from pathlib import Path

# 确保项目根目录在 path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from core.agent import ScholarAgent


async def main():
    print("=" * 60)
    print("  E2E Validation: Real Paper + Real Search")
    print("=" * 60)

    workspace_path = str(PROJECT_ROOT / ".workspace")
    print(f"  Paper: {workspace_path}")
    print(f"  Model: {os.environ.get('LLM_MODEL', 'gpt-4.1')}")
    print()

    agent = ScholarAgent(
        paper_path=workspace_path,
        model=None,  # 使用 .env 中的 LLM_MODEL
        verbose=True,
        max_loop_turns=15,    # 给足空间让 Agent 自由探索
        token_budget=200000,  # 真实论文需要更多 budget
    )

    # Phase 1: Agent 自主启动审阅
    print("[Phase 1] Agent 启动自主审阅...")
    print("-" * 40)
    t0 = time.time()

    response = await agent.start()

    t1 = time.time()
    print(f"\n{'=' * 40}")
    print(f"Agent 初步审阅 ({t1 - t0:.1f}s):")
    print(f"{'=' * 40}")
    print(response[:2000])  # 截取前 2000 字
    if len(response) > 2000:
        print(f"\n... [总计 {len(response)} 字符]")

    # 打印统计
    stats = agent.get_stats()
    print(f"\n[统计]")
    print(f"  Loop 轮次: {stats['loop_turns_total']}")
    print(f"  Findings: {stats['findings_count']}")
    print(f"  Edits: {stats['edits_count']}")
    print(f"  Tokens: ~{stats['total_tokens']}")

    # 打印 findings
    findings = agent.get_findings()
    if findings:
        print(f"\n[Findings ({len(findings)} 条)]")
        for i, f in enumerate(findings, 1):
            print(f"  {i}. [{f['priority']}][{f['status']}] {f['finding'][:100]}")
    else:
        print("\n[没有发现 — Agent 可能还在形成初步印象]")

    # Phase 2: 追问一个 follow-up
    print(f"\n{'=' * 40}")
    print("[Phase 2] 用户追问...")
    print("-" * 40)

    follow_up = "这篇论文的 identification strategy 有什么潜在问题吗？特别是 parallel trends assumption。"
    print(f"User: {follow_up}")
    print()

    t2 = time.time()
    response2 = await agent.chat(follow_up)
    t3 = time.time()

    print(f"\n{'=' * 40}")
    print(f"Agent 回复 ({t3 - t2:.1f}s):")
    print(f"{'=' * 40}")
    print(response2[:2000])
    if len(response2) > 2000:
        print(f"\n... [总计 {len(response2)} 字符]")

    # 最终统计
    final_stats = agent.get_stats()
    print(f"\n{'=' * 60}")
    print("[最终统计]")
    print(f"  总 Loop 轮次: {final_stats['loop_turns_total']}")
    print(f"  对话轮次: {final_stats['conversation_turns']}")
    print(f"  总 Findings: {final_stats['findings_count']}")
    print(f"  总 Edits: {final_stats['edits_count']}")
    print(f"  总 Tokens: ~{final_stats['total_tokens']}")
    print(f"  总耗时: {t3 - t0:.1f}s")
    print(f"{'=' * 60}")

    # 验证标准
    print("\n[验证标准]")
    checks = [
        ("Agent 产生了回复", bool(response.strip())),
        ("Agent 产生了 findings", final_stats['findings_count'] > 0),
        ("Token Pipeline 生效 (没注入全文)", final_stats['total_tokens'] < 180000),
        ("多轮对话正常", bool(response2.strip())),
    ]
    all_pass = True
    for desc, passed in checks:
        icon = "✅" if passed else "❌"
        print(f"  {icon} {desc}")
        if not passed:
            all_pass = False

    if all_pass:
        print("\n🎉 全部验证通过！ScholarAgent 可以处理真实长论文。")
    else:
        print("\n⚠️ 部分验证未通过，需要检查。")

    return all_pass


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
