"""
Phase 56 E2E 验证: 真实论文审稿 + Phase 52-55 机制生效检测

目标:
1. 用真实论文 (Chan, Gentzkow, Yu 2019) 跑 Scholar persona 审稿
2. 观察 Phase 52 边际产出信号是否在 reflect_and_plan 中出现
3. 观察 Phase 54 程序性记忆是否在 end_session 时提取
4. 观察 Phase 55 停滞检测是否在连续无产出时触发
5. 记录 Agent 的认知行为模式（工具调用分布、findings 质量）

验证标准:
- Agent 能正常完成审稿循环（不 crash）
- 产出 >= 3 条 findings
- 至少触发一次 reflect_and_plan（证明 Agent 有元认知）
- 记录 Phase 52-55 机制的触发情况（不要求必须触发，但记录是否有机会触发）
"""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from core.agent import ScholarAgent


async def run_e2e():
    """运行 E2E 审稿测试。"""
    paper_path = str(Path(__file__).resolve().parent.parent / "examples" / "radiology_chan_gentzkow_yu.pdf")
    
    print("=" * 70)
    print("Phase 56 E2E Validation: Real Paper Review")
    print(f"Paper: {paper_path}")
    print("=" * 70)
    
    # 创建 Agent (Scholar persona, max 25 turns to observe behavior)
    agent = ScholarAgent(
        paper_path=paper_path,
        persona="scholar",
        max_loop_turns=25,
        verbose=True,
    )
    
    start_time = time.time()
    
    # 启动审稿
    print("\n--- Starting Agent Review ---\n")
    try:
        result = await agent.start(
            user_intent="请审阅这篇关于放射科医生诊断技能差异的经济学论文。重点关注方法论的严谨性和核心假设的合理性。"
        )
    except Exception as e:
        print(f"\n❌ Agent crashed: {e}")
        import traceback
        traceback.print_exc()
        return
    
    elapsed = time.time() - start_time
    
    # ============================================================
    # 收集结果
    # ============================================================
    
    print("\n" + "=" * 70)
    print("E2E RESULTS")
    print("=" * 70)
    
    state = agent.harness.state
    
    # 基本指标
    print(f"\n📊 基本指标:")
    print(f"  - 总轮次: {state.loop_turns}")
    print(f"  - 耗时: {elapsed:.1f}s")
    print(f"  - Findings 数量: {len(state.findings)}")
    print(f"  - 已读 Sections: {len(state.sections_read)}/{len(state.paper_sections)}")
    
    # 工具调用分布
    tool_counts = {}
    for call in state.tool_call_history:
        name = call.get("name", "unknown")
        tool_counts[name] = tool_counts.get(name, 0) + 1
    
    print(f"\n🔧 工具调用分布:")
    for tool, count in sorted(tool_counts.items(), key=lambda x: -x[1]):
        print(f"  - {tool}: {count}")
    
    # Findings 详情
    print(f"\n📝 Findings:")
    for i, f in enumerate(state.findings, 1):
        priority = f.get("priority", "?")
        finding_text = f.get("finding", "")[:120]
        turn = f.get("recorded_at_turn", "?")
        print(f"  [{i}] (P:{priority}, Turn:{turn}) {finding_text}")
    
    # ============================================================
    # Phase 52-55 机制检测
    # ============================================================
    
    print(f"\n🔬 Phase 52-55 机制检测:")
    
    # Phase 52: 边际产出信号
    # 检查 findings 是否有 recorded_at_turn 字段
    has_turn_tracking = all("recorded_at_turn" in f for f in state.findings) if state.findings else False
    print(f"  Phase 52 (边际产出): recorded_at_turn 字段 = {'✓' if has_turn_tracking else '✗'}")
    
    # Phase 54: 程序性记忆
    # 检查 memory 模块是否有 ProceduralPattern
    from core.memory import ProceduralPattern
    has_procedural = ProceduralPattern is not None
    print(f"  Phase 54 (程序性记忆): ProceduralPattern 类可用 = {'✓' if has_procedural else '✗'}")
    
    # Phase 55: 停滞检测
    # 检查 harness 是否有 _check_stagnation 方法（机制存在）
    has_stagnation = hasattr(agent.harness, '_check_stagnation')
    # 检查是否实际触发过（属性名: _last_stagnation_signal_turn）
    stagnation_triggered = getattr(agent.harness, '_last_stagnation_signal_turn', 0) > 0
    print(f"  Phase 55 (停滞检测): 机制存在 = {'✓' if has_stagnation else '✗'}, 触发过 = {'✓' if stagnation_triggered else '✗'}")
    
    # 反思次数
    reflect_count = tool_counts.get("reflect_and_plan", 0)
    print(f"  反思次数 (reflect_and_plan): {reflect_count}")
    
    # ============================================================
    # Agent 回复
    # ============================================================
    
    print(f"\n💬 Agent 最终回复:")
    print("-" * 50)
    print(result[:2000] if result else "(empty)")
    print("-" * 50)
    
    # ============================================================
    # 验证标准
    # ============================================================
    
    print(f"\n✅ 验证标准:")
    checks = {
        "Agent 正常完成（不 crash）": True,
        "产出 >= 3 条 findings": len(state.findings) >= 3,
        "至少 1 次 reflect_and_plan": reflect_count >= 1,
        "recorded_at_turn 字段存在": has_turn_tracking,
        "停滞检测机制存在 (_check_stagnation)": has_stagnation,
    }
    
    all_pass = True
    for check, passed in checks.items():
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"  {status}: {check}")
        if not passed:
            all_pass = False
    
    # 保存报告
    report = {
        "phase": 56,
        "paper": "Chan, Gentzkow, Yu 2019 - Selection with Variation in Diagnostic Skill",
        "persona": "scholar",
        "elapsed_seconds": elapsed,
        "loop_turns": state.loop_turns,
        "findings_count": len(state.findings),
        "sections_read": len(state.sections_read),
        "sections_total": len(state.paper_sections),
        "tool_distribution": tool_counts,
        "findings": state.findings,
        "reflect_count": reflect_count,
        "phase52_turn_tracking": has_turn_tracking,
        "phase55_stagnation_triggered": stagnation_triggered,
        "all_checks_pass": all_pass,
        "agent_reply": result[:3000] if result else "",
    }
    
    report_path = Path(__file__).resolve().parent / "e2e_report_phase56.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n📄 报告已保存: {report_path}")
    
    if all_pass:
        print("\n🎉 Phase 56 E2E 验证通过!")
    else:
        print("\n⚠️  部分验证未通过，需要分析原因。")


if __name__ == "__main__":
    asyncio.run(run_e2e())
