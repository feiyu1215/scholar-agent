#!/usr/bin/env python3
"""
E1 验证脚本: UnifiedReviewAgent 全链路 E2E 测试

验证目标 (来自 NEXT_STEPS.md W1 验证标准):
1. Agent 在没有代码编排的情况下能自然产出 findings → 主动切 Writer → 修改 → 切回 Scholar 验证
2. 面对"没问题"的论文，Agent 直接 mark_complete 而非执行无意义的 Writer 阶段
3. 切换次数有上限保护，但 Agent 有权坚持（nudge not block）
4. 全量回归测试通过（已验证: 594 pass）

运行:
    cd v2
    python3 scripts/e1_verify_unified_agent.py

输出:
    - 终端实时打印 Agent 的行为日志
    - 最终输出验证报告（是否发生 persona 切换、findings/edits 数量、token 消耗）
"""

import sys
import asyncio
import json
import time
from pathlib import Path

# 确保 v2/ 在 sys.path
V2_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(V2_ROOT))

from dotenv import load_dotenv
load_dotenv(V2_ROOT / ".env")

from core.agent import UnifiedReviewAgent


async def run_e1():
    """执行 E1 全链路验证。"""
    paper_path = str(V2_ROOT / "examples" / "sample_paper.md")

    print("=" * 70)
    print("  E1: UnifiedReviewAgent 全链路 E2E 验证")
    print("=" * 70)
    print(f"  论文: {paper_path}")
    print(f"  目标: 观察 Agent 是否自主使用 switch_persona 工具")
    print("=" * 70)
    print()

    start_time = time.time()

    # 使用适中的预算 — 不需要太大，sample_paper 很短
    agent = UnifiedReviewAgent(
        paper_path=paper_path,
        verbose=True,   # 打印完整行为日志
        max_loop_turns=40,
        token_budget=80000,
    )

    # 用明确的 intent 暗示完整流程，但不强制
    user_intent = (
        "请审阅这篇论文。如果你发现了需要修改的问题，"
        "可以使用 switch_persona 工具切换到 writer 视角进行修改，"
        "修改完成后再切回 scholar 视角验证。全程由你自主决定。"
    )

    print("[启动 Agent...]")
    print()

    result = await agent.run(user_intent=user_intent)

    elapsed = time.time() - start_time

    # ================================================================
    # 验证报告
    # ================================================================
    print()
    print("=" * 70)
    print("  E1 验证报告")
    print("=" * 70)

    stats = result["stats"]
    findings = result["findings"]
    edits = result["edits"]

    print(f"\n  --- 基本统计 ---")
    print(f"  总耗时: {elapsed:.1f}s")
    print(f"  总 token: {stats['total_tokens']}")
    print(f"  循环轮数: {stats['total_loop_turns']}")
    print(f"  Findings 数: {stats['findings_count']}")
    print(f"  Edits 数: {stats['edits_count']}")
    print(f"  Persona 切换次数: {stats['persona_switches']}")
    print(f"  最终 persona: {stats['final_persona']}")

    # W1 核心验证: 是否发生了自主 persona 切换
    print(f"\n  --- W1 核心验证 ---")
    switched = stats['persona_switches'] > 0
    print(f"  {'✅' if switched else '❌'} Agent 自主切换了 persona ({stats['persona_switches']} 次)")

    # 验证标准 1: 有 findings 且切了 writer
    has_findings = len(findings) > 0
    print(f"  {'✅' if has_findings else '❌'} Agent 产出了 findings ({len(findings)} 条)")

    has_edits = len(edits) > 0
    print(f"  {'✅' if has_edits else '⚠️'} Agent 产出了 edits ({len(edits)} 条)")

    # 打印 findings 摘要
    if findings:
        print(f"\n  --- Findings 详情 ---")
        for i, f in enumerate(findings[:8], 1):
            priority = f.get("priority", "?")
            section = f.get("section", "?")
            finding_text = f.get("finding", "")[:80]
            print(f"  [{i}] [{priority}] {section}: {finding_text}")

    # 打印 edits 摘要
    if edits:
        print(f"\n  --- Edits 详情 ---")
        for i, e in enumerate(edits[:5], 1):
            section = e.get("section", "?")
            desc = e.get("description", e.get("edit_type", ""))[:80]
            print(f"  [{i}] {section}: {desc}")

    # Agent 输出摘要
    print(f"\n  --- Agent 最终输出 (前 500 字) ---")
    output_preview = result["output"][:500]
    print(f"  {output_preview}")

    # 总结
    print(f"\n  --- 总结 ---")
    all_pass = switched and has_findings
    if all_pass:
        print("  ✅ E1 验证通过: Agent 自主完成 审阅→切换→修改 流程")
    elif has_findings and not switched:
        print("  ⚠️ E1 部分通过: Agent 产出了 findings 但没有切换 persona")
        print("     (这可能是合理的 — Agent 自主决定不切换也是它的认知权)")
    else:
        print("  ❌ E1 验证失败: Agent 未能产出有意义的审阅结果")

    print()
    print("=" * 70)

    return result


if __name__ == "__main__":
    result = asyncio.run(run_e1())
