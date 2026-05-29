"""
Phase 6 E2E 验证: v2 HD-WM 集成 — 真实论文审稿

目标:
1. 用真实论文跑 v2 ScholarAgent (enable_hdwm=True)
2. 观察 Agent 是否自然使用假说工具 (generate_hypothesis, add_evidence, resolve_hypothesis)
3. 观察 review_readiness 信号是否在正确时机出现
4. 收集假说生命周期数据（产生/证据/解决率）
5. 与 HD-WM off 的 baseline 行为做简要对比

验证标准:
- Agent 能正常完成审稿循环（不 crash）
- HD-WM on 时 Agent 至少产生 1 个假说
- 假说生命周期正常工作（证据添加/状态转换）
- review_readiness 计算正确
- 无 regression（HD-WM on 不比 off 差）
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


async def run_hdwm_e2e(enable_hdwm: bool, max_turns: int = 15) -> dict:
    """运行一次审稿并收集指标。"""
    paper_path = str(Path(__file__).resolve().parent.parent / "examples" / "radiology_selection.pdf")

    label = "HD-WM ON" if enable_hdwm else "HD-WM OFF"
    print(f"\n{'='*70}")
    print(f" {label} — E2E Review (max {max_turns} turns)")
    print(f"{'='*70}\n")

    agent = ScholarAgent(
        paper_path=paper_path,
        persona="scholar",
        max_loop_turns=max_turns,
        verbose=True,
        enable_hdwm=enable_hdwm,
    )

    start_time = time.time()

    try:
        result = await agent.start(
            user_intent="请审阅这篇关于放射科医生诊断技能差异的经济学论文。重点关注方法论的严谨性和实证分析的可靠性。"
        )
    except Exception as e:
        print(f"\n❌ Agent crashed: {e}")
        import traceback
        traceback.print_exc()
        return {"error": str(e), "label": label}

    elapsed = time.time() - start_time
    state = agent.harness.state

    # 收集基本指标
    metrics = {
        "label": label,
        "enable_hdwm": enable_hdwm,
        "loop_turns": state.loop_turns,
        "elapsed_seconds": round(elapsed, 1),
        "findings_count": len(state.findings),
        "sections_read": len(state.sections_read),
        "total_sections": len(state.paper_sections),
        "total_tokens": state.total_tokens,
    }

    # 工具调用分布
    tool_counts: dict[str, int] = {}
    for call in state.tool_call_history:
        name = call.get("name", "unknown")
        tool_counts[name] = tool_counts.get(name, 0) + 1
    metrics["tool_counts"] = tool_counts

    # HD-WM 特有指标
    if enable_hdwm and agent.harness.hypothesis_module:
        module = agent.harness.hypothesis_module
        hypotheses = module.hypotheses
        metrics["hdwm"] = {
            "total_hypotheses": len(hypotheses),
            "active": sum(1 for h in hypotheses if h.status.value == "active"),
            "supported": sum(1 for h in hypotheses if h.status.value == "supported"),
            "refuted": sum(1 for h in hypotheses if h.status.value == "refuted"),
            "suspended": sum(1 for h in hypotheses if h.status.value == "suspended"),
            "review_readiness": round(module.review_readiness, 3),
            "is_ready": module.is_ready,
            "is_saturated": module.is_saturated,
            "resolution_rate": round(module.resolution_rate, 3),
        }
        # 每个假说的详情
        metrics["hdwm"]["hypotheses_detail"] = [
            {
                "id": h.id,
                "statement": h.statement[:100],
                "status": h.status.value,
                "evidence_for": len(h.evidence_for),
                "evidence_against": len(h.evidence_against),
                "balance": round(h.evidence_balance, 2),
                "created_turn": h.created_at_turn,
                "resolved_turn": h.resolved_at_turn,
            }
            for h in hypotheses
        ]
    else:
        metrics["hdwm"] = None

    # Findings
    metrics["findings"] = [
        {
            "finding": f.get("finding", "")[:150],
            "priority": f.get("priority", "?"),
            "turn": f.get("recorded_at_turn", "?"),
        }
        for f in state.findings
    ]

    return metrics


def print_report(metrics: dict):
    """打印人类可读的报告。"""
    print(f"\n{'='*70}")
    print(f" REPORT: {metrics['label']}")
    print(f"{'='*70}")

    print(f"\n📊 基本指标:")
    print(f"  轮次: {metrics['loop_turns']}")
    print(f"  耗时: {metrics['elapsed_seconds']}s")
    print(f"  Findings: {metrics['findings_count']}")
    print(f"  已读 Sections: {metrics['sections_read']}/{metrics['total_sections']}")
    print(f"  Token 消耗: {metrics['total_tokens']:,}")

    print(f"\n🔧 工具调用分布:")
    for tool, count in sorted(metrics["tool_counts"].items(), key=lambda x: -x[1]):
        print(f"  {tool}: {count}")

    if metrics.get("hdwm"):
        hdwm = metrics["hdwm"]
        print(f"\n🧠 HD-WM 状态:")
        print(f"  总假说数: {hdwm['total_hypotheses']}")
        print(f"  活跃/支持/反驳/搁置: {hdwm['active']}/{hdwm['supported']}/{hdwm['refuted']}/{hdwm['suspended']}")
        print(f"  解决率: {hdwm['resolution_rate']:.0%}")
        print(f"  review_readiness: {hdwm['review_readiness']:.0%}")
        print(f"  is_ready: {hdwm['is_ready']}")
        print(f"  is_saturated: {hdwm['is_saturated']}")

        if hdwm.get("hypotheses_detail"):
            print(f"\n  假说详情:")
            for h in hdwm["hypotheses_detail"]:
                print(f"    {h['id']}: [{h['status']}] {h['statement']}")
                print(f"         证据: +{h['evidence_for']}/-{h['evidence_against']}, balance={h['balance']}")

    print(f"\n📝 Findings:")
    for i, f in enumerate(metrics["findings"], 1):
        print(f"  [{i}] (P:{f['priority']}, Turn:{f['turn']}) {f['finding']}")


async def main():
    """运行 HD-WM on/off 对比测试。"""
    print("Phase 6 E2E: HD-WM Integration Validation")
    print("=" * 70)

    # Run HD-WM ON
    metrics_on = await run_hdwm_e2e(enable_hdwm=True, max_turns=15)
    print_report(metrics_on)

    # Run HD-WM OFF (baseline)
    metrics_off = await run_hdwm_e2e(enable_hdwm=False, max_turns=15)
    print_report(metrics_off)

    # 对比摘要
    print(f"\n{'='*70}")
    print(" A/B COMPARISON: HD-WM ON vs OFF")
    print(f"{'='*70}")

    if "error" not in metrics_on and "error" not in metrics_off:
        print(f"\n{'指标':<20} {'HD-WM ON':<15} {'HD-WM OFF':<15}")
        print(f"{'-'*50}")
        print(f"{'轮次':<20} {metrics_on['loop_turns']:<15} {metrics_off['loop_turns']:<15}")
        print(f"{'Findings':<20} {metrics_on['findings_count']:<15} {metrics_off['findings_count']:<15}")
        print(f"{'已读 Sections':<20} {metrics_on['sections_read']:<15} {metrics_off['sections_read']:<15}")
        print(f"{'耗时(s)':<20} {metrics_on['elapsed_seconds']:<15} {metrics_off['elapsed_seconds']:<15}")
        print(f"{'Token 消耗':<20} {metrics_on['total_tokens']:<15} {metrics_off['total_tokens']:<15}")

        if metrics_on.get("hdwm"):
            hdwm = metrics_on["hdwm"]
            print(f"\n🧠 HD-WM 专属指标:")
            print(f"  假说产出: {hdwm['total_hypotheses']}")
            print(f"  review_readiness: {hdwm['review_readiness']:.0%}")
            hyp_tools = sum(metrics_on["tool_counts"].get(t, 0) for t in
                          ["generate_hypothesis", "add_evidence", "resolve_hypothesis"])
            print(f"  假说工具调用总数: {hyp_tools}")

    # 保存结果
    report_path = Path(__file__).resolve().parent.parent / "core" / "e2e_report_phase6_hdwm.json"
    report = {
        "hdwm_on": metrics_on,
        "hdwm_off": metrics_off,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\n📄 报告已保存: {report_path}")


if __name__ == "__main__":
    asyncio.run(main())
