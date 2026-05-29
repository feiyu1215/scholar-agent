"""
Phase 17: 真实论文 E2E 审稿测试

目的:
    用 Chan, Gentzkow, Yu (2025) "Selection with Variation in Diagnostic Skill"
    这篇经济学顶刊论文，端到端跑一次完整审稿流程。
    
    观察重点:
    1. Agent 是否能正确识别论文结构并选择性阅读
    2. Agent 产出的 findings 是否有假阳性（"错误"实际上论文已解答）
    3. Agent 的认知深度——是浅扫还是真的有洞察
    4. Section Digest / compress_messages 在真实场景下的表现
    5. 为后续"Cross-Verification 审查机制"提供设计依据

使用:
    python3 -m pytest tests/test_real_radiology_e2e.py -v -s 2>&1 | tee tests/e2e_radiology_output.log
    
    或直接运行:
    python3 tests/test_real_radiology_e2e.py
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


async def run_e2e_review():
    """运行完整的 E2E 审稿流程并记录观察。"""
    
    paper_path = str(PROJECT_ROOT / "tests" / "papers" / "radiology_selection.pdf")
    
    print("=" * 70)
    print("Phase 17: E2E Real Paper Review")
    print(f"Paper: Selection with Variation in Diagnostic Skill (Chan et al.)")
    print(f"Path: {paper_path}")
    print("=" * 70)
    
    # 创建 Agent
    agent = ScholarAgent(
        paper_path=paper_path,
        verbose=True,
        max_loop_turns=25,       # 给足够的轮次让 Agent 深入
        token_budget=150000,     # 真实论文需要更多预算
    )
    
    # 确认论文加载
    print(f"\n[Setup] Paper sections loaded: {len(agent.harness.state.paper_sections)}")
    print(f"[Setup] Section keys: {[k for k in agent.harness.state.paper_sections.keys() if k != 'full']}")
    print()
    
    # === Round 1: 自主审稿 ===
    print("\n" + "=" * 70)
    print("ROUND 1: 自主审稿（Agent 自由决定策略）")
    print("=" * 70 + "\n")
    
    response = await agent.start(
        user_intent=(
            "请审阅这篇论文。这是一篇发表在经济学顶刊的实证论文，"
            "关于放射科医生的诊断技能选择效应。"
            "请按照你的审稿经验自主决定阅读策略和重点。"
        )
    )
    
    # 记录结果
    print("\n" + "=" * 70)
    print("ROUND 1 RESULTS")
    print("=" * 70)
    print(f"\n[Agent Response]:\n{response[:2000]}")
    print(f"\n[Findings Count]: {len(agent.harness.state.findings)}")
    print(f"[Loop Turns Used]: {agent.harness.state.loop_turns}")
    print(f"[Total Tokens]: {agent.harness.state.total_tokens}")
    print(f"[Sections Read]: {agent.harness.state.sections_read}")
    print(f"[Section Digests]: {list(agent.harness.state.section_digests.keys())}")
    
    # 打印每条 finding
    print("\n--- Findings Detail ---")
    for i, f in enumerate(agent.harness.state.findings, 1):
        print(f"\n  [{i}] {f.get('title', 'untitled')}")
        print(f"      Priority: {f.get('priority', '?')} | Status: {f.get('status', '?')}")
        print(f"      Description: {f.get('description', '')[:200]}")
        if f.get('evidence'):
            print(f"      Evidence: {f['evidence'][:150]}")
    
    # === Phase 37 认知行为观察 ===
    print("\n--- Phase 37: Reflection Behavior ---")
    reflection_log = getattr(agent.harness, '_reflection_log', [])
    nudge_fired = getattr(agent.harness.state, '_reflection_nudge_fired', False)
    print(f"  reflect_and_plan 调用次数: {len(reflection_log)}")
    print(f"  反思催促器是否触发: {nudge_fired}")
    if reflection_log:
        for i, r in enumerate(reflection_log, 1):
            print(f"  [{i}] trigger: {r.get('trigger', '?')[:100]}")
            if r.get('assessment'):
                print(f"      assessment: {r['assessment'][:150]}")
            if r.get('next_actions'):
                print(f"      next_actions: {r['next_actions'][:150]}")
    else:
        print("  ⚠️ Agent 从未调用 reflect_and_plan")
    
    # === Phase 39 搜索行为观察 ===
    print("\n--- Phase 39: Literature Search Behavior ---")
    search_log = getattr(agent.harness, '_search_log', [])
    print(f"  search_literature 调用次数: {len(search_log)}")
    if search_log:
        for i, s in enumerate(search_log, 1):
            print(f"  [{i}] query: {s.get('query', '?')}")
            print(f"      reason: {s.get('reason', '?')[:150]}")
            print(f"      results_count: {s.get('results_count', 0)}")
    else:
        print("  ⚠️ Agent 从未调用 search_literature")
    
    # === 认知产出统计 ===
    print("\n--- Cognitive Output Stats ---")
    print(f"  consecutive_read_turns (final): {agent.harness.state.consecutive_read_turns}")
    cognitive_state = getattr(agent.harness.state, 'cognitive_state', None)
    if cognitive_state:
        print(f"  cognitive_state: {json.dumps(cognitive_state, ensure_ascii=False)[:300]}")
    
    # === 结果保存 ===
    report = {
        "paper": "Chan, Gentzkow, Yu (2025) - Selection with Variation in Diagnostic Skill",
        "round": 1,
        "response_preview": response[:3000],
        "findings": agent.harness.state.findings,
        "loop_turns": agent.harness.state.loop_turns,
        "total_tokens": agent.harness.state.total_tokens,
        "sections_read": agent.harness.state.sections_read,
        "section_digests": agent.harness.state.section_digests,
        "reflection_log": reflection_log,
        "reflection_nudge_fired": nudge_fired,
        "search_log": search_log,
    }
    
    report_path = PROJECT_ROOT / "tests" / "e2e_radiology_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n[Saved] Report: {report_path}")
    
    return report


if __name__ == "__main__":
    report = asyncio.run(run_e2e_review())
    print("\n\n" + "=" * 70)
    print("E2E TEST COMPLETE")
    print("=" * 70)
    print(f"Findings: {len(report['findings'])}")
    print(f"Loop turns: {report['loop_turns']}")
    print(f"Tokens used: {report['total_tokens']}")
