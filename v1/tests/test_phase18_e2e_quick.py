"""
Phase 18: 快速 E2E 验证

目的: 用 8 轮循环快速验证：
1. Agent 是否能感知到续读能力（看到截断提示中的 offset 信息）
2. Agent 在没有优先级分类的情况下是否仍能做出合理的阅读策略选择
3. 催促器+续读的组合是否协调

不是完整审稿——只观察前 8 轮的行为模式。

使用:
    python3 tests/test_phase18_e2e_quick.py
"""

import asyncio
import json
import sys
import os
from pathlib import Path

# 设置环境
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from core.agent import ScholarAgent


async def run_quick_e2e():
    """运行限制为 8 轮的快速 E2E 验证。"""
    
    paper_path = str(PROJECT_ROOT / "tests" / "papers" / "radiology_selection.pdf")
    
    print("=" * 70)
    print("Phase 18: Quick E2E Verification (max 8 turns)")
    print(f"Paper: Chan, Gentzkow, Yu (2025)")
    print("Focus: Agent autonomy (offset usage + self-directed strategy)")
    print("=" * 70)
    
    # 限制 8 轮——够看初始策略 + 是否使用续读
    agent = ScholarAgent(
        paper_path=paper_path,
        verbose=True,
        max_loop_turns=8,
        token_budget=50000,
    )
    
    # 确认论文加载
    print(f"\n[Setup] Sections loaded: {len(agent.harness.state.paper_sections)}")
    sections_info = []
    for k, v in agent.harness.state.paper_sections.items():
        if k != "full":
            sections_info.append(f"{k}: {len(v)} chars")
    print(f"[Setup] Sections:\n  " + "\n  ".join(sections_info[:15]))
    
    # 找出哪些 section > 6000 字符（会触发截断+续读提示）
    long_sections = [k for k, v in agent.harness.state.paper_sections.items() 
                     if k != "full" and len(v) > 6000]
    print(f"\n[Setup] Sections > 6000 chars (will trigger offset hint): {long_sections}")
    print()
    
    # 运行
    response = await agent.start(
        user_intent=(
            "请审阅这篇论文。这是一篇关于放射科医生诊断技能选择效应的经济学实证论文。"
            "请自主决定阅读策略和重点。"
        )
    )
    
    # === 分析结果 ===
    print("\n" + "=" * 70)
    print("PHASE 18 E2E RESULTS")
    print("=" * 70)
    
    state = agent.harness.state
    
    print(f"\n[Metrics]")
    print(f"  Loop turns used: {state.loop_turns}/8")
    print(f"  Total tokens: {state.total_tokens}")
    print(f"  Findings: {len(state.findings)}")
    print(f"  Sections read: {state.sections_read}")
    print(f"  Consecutive read turns at end: {state.consecutive_read_turns}")
    
    # Phase 18 特有观察：Agent 是否使用了续读？
    # 通过检查 messages 中是否有 offset 参数来判断
    offset_usage = 0
    for msg in agent.messages:
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                if tc.get("function", {}).get("name") == "read_section":
                    args = json.loads(tc["function"].get("arguments", "{}"))
                    if args.get("offset", 0) > 0:
                        offset_usage += 1
    
    print(f"\n[Phase 18 Specific Observations]")
    print(f"  Agent used offset (continuation reads): {offset_usage} times")
    print(f"  Agent was exposed to truncation hints: {len([s for s in state.sections_read if s in long_sections])} long sections read")
    
    # Agent 回复
    print(f"\n[Agent Response (first 1500 chars)]:")
    print(response[:1500])
    
    # Findings 详情
    if state.findings:
        print(f"\n[Findings]:")
        for i, f in enumerate(state.findings, 1):
            print(f"  [{i}] [{f.get('priority', '?')}] {f.get('finding', '')[:150]}")
    
    # 保存结果
    report = {
        "phase": 18,
        "max_turns": 8,
        "turns_used": state.loop_turns,
        "total_tokens": state.total_tokens,
        "findings_count": len(state.findings),
        "findings": state.findings,
        "sections_read": state.sections_read,
        "offset_usage_count": offset_usage,
        "long_sections_encountered": long_sections,
        "response_preview": response[:2000],
        "consecutive_read_turns_at_end": state.consecutive_read_turns,
    }
    
    report_path = PROJECT_ROOT / "tests" / "e2e_phase18_quick_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n[Saved] Report: {report_path}")
    
    return report


if __name__ == "__main__":
    report = asyncio.run(run_quick_e2e())
    print("\n" + "=" * 70)
    print("QUICK E2E COMPLETE")
    print("=" * 70)
    print(f"  Turns: {report['turns_used']}/8")
    print(f"  Tokens: {report['total_tokens']}")
    print(f"  Findings: {report['findings_count']}")
    print(f"  Offset used: {report['offset_usage_count']} times")
    
    # 评估
    if report['offset_usage_count'] > 0:
        print("\n  ✅ Agent USED offset continuation — autonomy restored!")
    else:
        print("\n  ⚠️ Agent did NOT use offset — may need longer run or different paper")
        print("     (8 turns may not be enough for Agent to encounter + decide to continue)")
