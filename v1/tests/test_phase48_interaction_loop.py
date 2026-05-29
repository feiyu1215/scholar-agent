"""
Phase 48: 用户交互循环 E2E 测试

核心验证:
    Agent 能否在多轮对话中理解作者的 rebuttal、区分"有效辩护"和"回避问题"、
    并据此调整自己的判断？

测试设计:
    1. Agent 先对论文进行初步审阅（限制轮次，快速产出 findings）
    2. 模拟作者对 Agent 的核心发现提出三种类型的 rebuttal:
       - 有效辩护：提供论文中已有的证据（Appendix F sensitivity analysis）
       - 回避问题：用无关论据回应（balance test 通过 ≠ 无 selection bias）
       - 新信息：提供论文外的补充数据
    3. 检查 Agent 的响应是否体现了认知调整能力

使用:
    python3 tests/test_phase48_interaction_loop.py 2>&1 | tee tests/e2e_phase48_output.log
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


# ============================================================
# Rebuttal 消息设计
# ============================================================

# Rebuttal 1: 有效辩护 — 作者指出 Appendix F 已做 sensitivity analysis
REBUTTAL_VALID_DEFENSE = """感谢审稿人的细致审阅。关于您提到的 ascertainment bias 问题（即轻症患者自愈后不回来复查导致 miss rate 测量偏差），我们在 Appendix F 中已经做了详细的 sensitivity analysis：

1. 我们构建了一个 bounding exercise：假设所有未回来复查的患者中有 X% 实际上是真阳性（X 从 0% 到 50%），重新估计 skill variation。结果显示即使在最极端的假设下（50% 的未复查者为真阳性），skill 的 variance 仍然显著且量级变化不超过 15%。

2. 我们还利用了 VA 系统的特殊性——VA 患者通常在同一系统内持续就医，因此"完全失访"的比例远低于一般医疗系统。我们在 Table F.2 中报告了 follow-up rate 为 87%。

3. 此外，即使存在 ascertainment bias，它对所有放射科医生的影响是对称的（因为患者分配是准随机的），因此不会系统性地偏向高技能或低技能医生。

请问审稿人是否认为这些分析充分回应了您的关切？"""

# Rebuttal 2: 回避问题 — 作者用 balance test 回应，但这不能解决不可观测 selection
REBUTTAL_EVASION = """关于审稿人提到的 quasi-random assignment 假设可能被违反的问题，我们认为这个担忧已被充分回应：

我们在 Table 3 中报告了详尽的 balance test，检验了患者的年龄、性别、BMI、既往病史、就诊时间等 12 个可观测特征在不同放射科医生之间的分布。所有 p-value 均大于 0.1，联合 F-test 的 p-value 为 0.73。这说明患者分配确实是准随机的。

此外，我们的识别策略不依赖于完美的随机化——只需要条件独立性（conditional on observables）即可。我们在 Table 4 中加入了丰富的控制变量后，结果几乎不变，进一步支持了我们的识别假设。

因此我们认为 quasi-random assignment 假设是可信的。"""

# Rebuttal 3: 新信息 — 作者提供论文外的补充数据
REBUTTAL_NEW_INFO = """审稿人提到了关于结构模型中 functional form 假设的敏感性问题。我们在修改稿中新增了以下分析（尚未出现在当前版本中）：

1. 我们用 semi-parametric 方法（不假设 signal 的具体分布形式）重新估计了 skill distribution，结果与 parametric 版本高度一致（rank correlation = 0.94）。

2. 我们还尝试了 heterogeneous threshold model（允许每个医生有不同的 loss function 参数），发现 skill variance 的估计值变化不超过 8%。

3. 一位合作者最近获取了 2019-2022 年的新数据（样本量增加 40%），preliminary results 显示 skill ranking 在时间上高度稳定（Spearman ρ = 0.91）。

这些新结果是否能回应审稿人对 functional form sensitivity 的关切？"""


# ============================================================
# 评估标准
# ============================================================

def evaluate_response(response: str, rebuttal_type: str) -> dict:
    """
    评估 Agent 对 rebuttal 的响应质量。
    
    不做自动化判断（那需要另一个 LLM），只提取关键信号供人工审查。
    """
    response_lower = response.lower()
    
    signals = {
        "rebuttal_type": rebuttal_type,
        "response_length": len(response),
        "response_preview": response[:1500],
        
        # 认知调整信号
        "acknowledges_point": any(kw in response for kw in [
            "确实", "有道理", "充分", "合理", "接受", "降级", "修正",
            "acknowledge", "valid", "sufficient", "convincing",
            "感谢", "说明了", "回应了",
        ]),
        "maintains_position": any(kw in response for kw in [
            "但是", "然而", "不过", "仍然", "依然", "不足以",
            "however", "nevertheless", "still", "insufficient",
            "没有回应", "回避了", "核心问题",
        ]),
        "asks_followup": any(kw in response for kw in [
            "？", "能否", "是否", "请问", "进一步",
            "could you", "can you", "would you",
        ]),
        "evaluates_evidence": any(kw in response for kw in [
            "证据", "数据", "表", "图", "appendix",
            "evidence", "data", "table", "figure",
            "sensitivity", "robustness",
        ]),
        "distinguishes_quality": any(kw in response for kw in [
            "充分回应", "部分回应", "未回应", "核心",
            "directly address", "partially", "does not address",
        ]),
    }
    
    return signals


# ============================================================
# 主测试流程
# ============================================================

async def run_interaction_loop_test():
    """运行完整的用户交互循环测试。"""
    
    paper_path = str(PROJECT_ROOT / "tests" / "papers" / "radiology_selection.pdf")
    
    print("=" * 70)
    print("Phase 48: User Interaction Loop E2E Test")
    print(f"Paper: Chan, Gentzkow, Yu (2019) - Radiology Diagnostic Skill")
    print("=" * 70)
    
    # 创建 Agent（较低的 max_loop_turns 让初步审阅快速完成）
    agent = ScholarAgent(
        paper_path=paper_path,
        verbose=True,
        max_loop_turns=15,       # 初步审阅限制在 15 轮
        token_budget=250000,     # 多轮对话需要更多预算
    )
    
    results = {
        "phase": 48,
        "test": "interaction_loop",
        "paper": "Chan, Gentzkow, Yu (2019)",
        "rounds": [],
    }
    
    # === Round 1: 初步审阅 ===
    print("\n" + "=" * 70)
    print("ROUND 1: 初步审阅（Agent 自主审稿，限 15 轮）")
    print("=" * 70 + "\n")
    
    response_r1 = await agent.start(
        user_intent=(
            "请审阅这篇经济学论文。这是关于放射科医生诊断技能差异的实证研究，"
            "发表在 QJE。请按你的审稿经验自主决定策略，完成后告诉我你的主要发现。"
        )
    )
    
    round1_data = {
        "round": 1,
        "type": "initial_review",
        "response_preview": response_r1[:2000],
        "findings_count": len(agent.harness.state.findings),
        "findings": agent.harness.state.findings,
        "loop_turns": agent.harness.state.loop_turns,
        "total_tokens": agent.harness.state.total_tokens,
        "sections_read": agent.harness.state.sections_read,
    }
    results["rounds"].append(round1_data)
    
    print(f"\n[Round 1 完成] Findings: {round1_data['findings_count']}, "
          f"Turns: {round1_data['loop_turns']}, Tokens: {round1_data['total_tokens']}")
    print(f"[Agent 响应预览]: {response_r1[:500]}...")
    
    # === Round 2: 有效辩护 ===
    print("\n" + "=" * 70)
    print("ROUND 2: 作者 Rebuttal — 有效辩护（Appendix F sensitivity analysis）")
    print("=" * 70 + "\n")
    
    response_r2 = await agent.chat(REBUTTAL_VALID_DEFENSE)
    
    eval_r2 = evaluate_response(response_r2, "valid_defense")
    round2_data = {
        "round": 2,
        "type": "rebuttal_valid_defense",
        "rebuttal_summary": "作者指出 Appendix F 已做 sensitivity analysis 控制 ascertainment bias",
        "response_preview": response_r2[:2000],
        "evaluation": eval_r2,
        "findings_count": len(agent.harness.state.findings),
        "loop_turns": agent.harness.state.loop_turns,
        "total_tokens": agent.harness.state.total_tokens,
    }
    results["rounds"].append(round2_data)
    
    print(f"\n[Round 2 完成] Tokens: {round2_data['total_tokens']}")
    print(f"[评估信号] acknowledges={eval_r2['acknowledges_point']}, "
          f"maintains={eval_r2['maintains_position']}, "
          f"evaluates_evidence={eval_r2['evaluates_evidence']}")
    print(f"[Agent 响应预览]: {response_r2[:500]}...")
    
    # === Round 3: 回避问题 ===
    print("\n" + "=" * 70)
    print("ROUND 3: 作者 Rebuttal — 回避问题（用 balance test 回应不可观测 selection）")
    print("=" * 70 + "\n")
    
    response_r3 = await agent.chat(REBUTTAL_EVASION)
    
    eval_r3 = evaluate_response(response_r3, "evasion")
    round3_data = {
        "round": 3,
        "type": "rebuttal_evasion",
        "rebuttal_summary": "作者用 balance test 通过来回应，但未回应不可观测 selection 的核心问题",
        "response_preview": response_r3[:2000],
        "evaluation": eval_r3,
        "findings_count": len(agent.harness.state.findings),
        "loop_turns": agent.harness.state.loop_turns,
        "total_tokens": agent.harness.state.total_tokens,
    }
    results["rounds"].append(round3_data)
    
    print(f"\n[Round 3 完成] Tokens: {round3_data['total_tokens']}")
    print(f"[评估信号] acknowledges={eval_r3['acknowledges_point']}, "
          f"maintains={eval_r3['maintains_position']}, "
          f"evaluates_evidence={eval_r3['evaluates_evidence']}")
    print(f"[Agent 响应预览]: {response_r3[:500]}...")
    
    # === Round 4: 新信息 ===
    print("\n" + "=" * 70)
    print("ROUND 4: 作者 Rebuttal — 新信息（论文外的补充数据）")
    print("=" * 70 + "\n")
    
    response_r4 = await agent.chat(REBUTTAL_NEW_INFO)
    
    eval_r4 = evaluate_response(response_r4, "new_info")
    round4_data = {
        "round": 4,
        "type": "rebuttal_new_info",
        "rebuttal_summary": "作者提供 semi-parametric 重估、heterogeneous model、新数据等论文外证据",
        "response_preview": response_r4[:2000],
        "evaluation": eval_r4,
        "findings_count": len(agent.harness.state.findings),
        "loop_turns": agent.harness.state.loop_turns,
        "total_tokens": agent.harness.state.total_tokens,
    }
    results["rounds"].append(round4_data)
    
    print(f"\n[Round 4 完成] Tokens: {round4_data['total_tokens']}")
    print(f"[评估信号] acknowledges={eval_r4['acknowledges_point']}, "
          f"maintains={eval_r4['maintains_position']}, "
          f"evaluates_evidence={eval_r4['evaluates_evidence']}")
    print(f"[Agent 响应预览]: {response_r4[:500]}...")
    
    # === 总结 ===
    print("\n" + "=" * 70)
    print("PHASE 48 TEST SUMMARY")
    print("=" * 70)
    
    # 期望行为判断
    expected_behaviors = {
        "round2_valid_defense": {
            "expected": "Agent 应该承认 sensitivity analysis 的有效性，降级或修正 ascertainment bias 相关 finding",
            "acknowledges": eval_r2["acknowledges_point"],
            "evaluates_evidence": eval_r2["evaluates_evidence"],
        },
        "round3_evasion": {
            "expected": "Agent 应该坚持立场——balance test 无法检测不可观测 selection，指出作者回避了核心问题",
            "maintains_position": eval_r3["maintains_position"],
            "distinguishes": eval_r3["distinguishes_quality"],
        },
        "round4_new_info": {
            "expected": "Agent 应该评估新证据的充分性，可能部分接受但指出 preliminary results 的局限",
            "evaluates_evidence": eval_r4["evaluates_evidence"],
            "asks_followup": eval_r4["asks_followup"],
        },
    }
    results["expected_behaviors"] = expected_behaviors
    results["final_stats"] = {
        "total_loop_turns": agent.harness.state.loop_turns,
        "total_tokens": agent.harness.state.total_tokens,
        "total_findings": len(agent.harness.state.findings),
        "conversation_turns": agent.harness.state.conversation_turns,
    }
    
    print(f"\n总 Loop Turns: {results['final_stats']['total_loop_turns']}")
    print(f"总 Tokens: {results['final_stats']['total_tokens']}")
    print(f"总 Findings: {results['final_stats']['total_findings']}")
    print(f"对话轮次: {results['final_stats']['conversation_turns']}")
    
    print("\n--- 期望行为检查 ---")
    for key, behavior in expected_behaviors.items():
        print(f"\n  [{key}]")
        print(f"    期望: {behavior['expected']}")
        for k, v in behavior.items():
            if k != "expected":
                status = "✅" if v else "⚠️"
                print(f"    {status} {k}: {v}")
    
    # 保存结果
    report_path = PROJECT_ROOT / "tests" / "e2e_phase48_interaction_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n[Saved] Report: {report_path}")
    
    return results


if __name__ == "__main__":
    results = asyncio.run(run_interaction_loop_test())
    print("\n\n" + "=" * 70)
    print("E2E TEST COMPLETE")
    print("=" * 70)
