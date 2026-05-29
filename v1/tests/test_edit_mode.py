"""
编辑模式深度验证 — ScholarAgent 的认知连贯性测试

测试场景:
    1. Agent 自主审阅论文 → 产出 findings
    2. 用户说 "根据你的发现，帮我把最严重的问题都改了"
    3. Agent 需要：回顾 findings → 决定改哪些 → 读相关 section → 逐个 edit → 自审

验证要点:
    - Agent 能根据自己的 findings 规划编辑策略（不是"一次改一句"的机械操作）
    - 修改后的内容确实修复了发现的问题
    - Agent 的修改是自洽的（不引入新矛盾）
    - 多次 edit 之间有逻辑关联（不是独立的 patch）

这是 "Agent 不是 workflow" 的终极验证：
    workflow 引擎会把"改论文"拆成固定 pipeline（找错 → 一个个改 → 检查）
    真正的 Agent 会根据发现的全局图景来决策"应该怎么改，改到什么程度"
"""

import os
import sys
import json
import asyncio
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from core.agent import ScholarAgent


async def main():
    paper_path = str(ROOT / "poc" / "test_paper.md")

    print("=" * 60)
    print("  ScholarAgent 编辑模式深度验证")
    print("=" * 60)
    print(f"  Paper: {paper_path}")
    print(f"  Model: {os.environ.get('LLM_MODEL', '?')}")
    print()

    agent = ScholarAgent(
        paper_path=paper_path,
        verbose=True,
        max_loop_turns=25,
        token_budget=120000,
    )

    # ---- Phase 1: Agent 自主审阅 ----
    print("\n" + "=" * 50)
    print("  PHASE 1: Agent 自主审阅论文")
    print("=" * 50)
    response1 = await agent.start()
    print(f"\n[Agent 审阅结论]:\n{response1[:1500]}")

    findings_after_review = agent.get_findings()
    print(f"\n[审阅完成] 共 {len(findings_after_review)} 条 findings:")
    for f in findings_after_review:
        print(f"  [{f['priority']}][{f['status']}] {f['finding'][:100]}")

    # 确保至少有 findings 才继续
    if len(findings_after_review) == 0:
        print("\n✗ FAIL: Agent 未产出任何 findings，无法测试编辑模式")
        return 1

    # ---- Phase 2: 用户要求全面修改 ----
    print("\n" + "=" * 50)
    print("  PHASE 2: 用户要求根据 findings 全面修改")
    print("=" * 50)

    edit_request = (
        "根据你刚才的审阅发现，帮我把论文里最严重的问题都改了。"
        "特别是数据不一致、overclaim 这些硬伤。"
        "请直接修改相关 section 的内容。"
    )
    print(f"\n[User]: {edit_request}")
    response2 = await agent.chat(edit_request)
    print(f"\n[Agent 修改报告]:\n{response2[:2000]}")

    edits_after_phase2 = agent.get_edits()
    print(f"\n[修改完成] 共 {len(edits_after_phase2)} 处修改:")
    for e in edits_after_phase2:
        print(f"  Section: {e['section']} | Reason: {e['reason'][:80]}")

    # ---- Phase 3: 要求 Agent 自审修改后的论文 ----
    print("\n" + "=" * 50)
    print("  PHASE 3: 用户要求自审修改后的内容")
    print("=" * 50)

    self_review_request = "你改完了吗？帮我检查一下修改后的内容有没有新的问题或不一致。"
    print(f"\n[User]: {self_review_request}")
    response3 = await agent.chat(self_review_request)
    print(f"\n[Agent 自审结论]:\n{response3[:1500]}")

    # ---- 最终统计与验证 ----
    print("\n" + "=" * 50)
    print("  FINAL STATS & VALIDATION")
    print("=" * 50)

    stats = agent.get_stats()
    print(json.dumps(stats, indent=2, ensure_ascii=False))

    # 验证
    checks = {}

    # 1. Agent 审阅产出了有效 findings
    checks["Agent 产出有效 findings"] = len(findings_after_review) >= 2

    # 2. Agent 实际执行了修改
    checks["Agent 执行了 ≥2 处修改"] = len(edits_after_phase2) >= 2

    # 3. 修改涵盖了 abstract（overclaim 最明显的地方）
    abstract_edited = any("abstract" in e["section"].lower() for e in edits_after_phase2)
    checks["Abstract 被修改（overclaim 主阵地）"] = abstract_edited

    # 4. 修改有明确理由
    all_have_reasons = all(len(e["reason"]) > 10 for e in edits_after_phase2)
    checks["每处修改都有充分理由"] = all_have_reasons

    # 5. 自审环节有实质回复
    checks["自审产出了实质性回复"] = len(response3) > 50

    # 6. Token 在预算内
    checks["Token 在预算内"] = stats["total_tokens"] < 120000

    # 7. 编辑内容不为空
    edits_have_content = all(len(e.get("content_preview", "")) > 20 for e in edits_after_phase2)
    checks["修改内容非空且有实质"] = edits_have_content

    print("\n[Validation Checks]:")
    all_pass = True
    for check, result in checks.items():
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"  {status}: {check}")
        if not result:
            all_pass = False

    # 额外深度检查: 验证修改后的 abstract 是否还有 "state-of-the-art"
    paper_sections = agent.harness.state.paper_sections
    abstract_content = paper_sections.get("abstract", "")
    if abstract_content:
        still_claims_sota = "state-of-the-art" in abstract_content.lower()
        if still_claims_sota:
            print(f"\n  ⚠️ 深度检查: 修改后 Abstract 仍声称 SOTA（表格明确显示 MetaOptNet 更优）")
            # 这不一定是 fail——Agent 可能选择用更温和的措辞保留了
        else:
            print(f"\n  ✓ 深度检查: 修改后 Abstract 已不再声称 SOTA")

    print(f"\n{'=' * 50}")
    print(f"  {'✓ ALL CHECKS PASSED' if all_pass else '✗ SOME CHECKS FAILED'}")
    print(f"{'=' * 50}")

    return 0 if all_pass else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
