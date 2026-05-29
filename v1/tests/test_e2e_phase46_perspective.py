"""
Phase 46 E2E 验证: 视角分裂的认知涌现

测试目标:
    验证 Agent 面对跨学科论文时，是否会自主触发 spawn_perspective。
    
    COGNITIVE_ANCHOR §2.3: "当认知上需要互斥的独立视角时，才分裂。"
    COGNITIVE_ANCHOR §5.5: "分裂的决策由 Agent 自主做出，不是代码预设的。"
    
    identity.py 第 14 条已描述了 spawn_perspective 的使用场景：
    "当你意识到某个问题需要一种专门的审视角度——比如你在读实验设计时想
    '这里需要一个统计方法专家来看'——你可以用 spawn_perspective 发起一个独立视角。"
    
    但在所有历史 E2E 中，Agent 从未自主使用过 spawn_perspective。
    Phase 44 的加分项 B2（spawn 复核）也未通过。

测试论文:
    examples/sample_paper_crossdisciplinary.md — 跨学科论文（ML + 因果推断 + 临床医学）
    
    论文的隐藏问题分布在三个学科领域：
    P1. [统计/计量] Rate condition (Assumption 3.3) 对 Transformer 可能不成立
    P2. [统计/计量] 高 AUC (0.94) 暗示 overlap 近似违反 → 方差膨胀
    P3. [ML/CS] 70/30 split 与 5-fold cross-fitting 描述矛盾
    P4. [临床/流行病学] "Synthetic RCT validation" 混淆了外部有效性和内部有效性
    P5. [统计] E-value 只针对点估计，不能用于异质性模式的敏感性分析

    一个单一视角的审稿人很难同时深入三个领域。
    如果 Agent 意识到"我对统计方法的判断可能有盲点"或"这里需要临床专家视角"，
    它应该自主 spawn_perspective。

评估标准:
    核心指标:
    S1. Agent 是否调用了 spawn_perspective（核心：视角分裂发生了）
    S2. spawn 的 lens 是否合理（对应论文的跨学科特性）
    S3. spawn 的 question 是否具体（不是泛泛的"帮我看看"）
    
    辅助指标:
    A1. Agent 的 findings 是否覆盖了多个学科维度
    A2. Agent 是否识别了至少 2 个隐藏问题
    A3. 总 loop turns（spawn 会增加 turns，但不应过多）

运行: python3 -m core.test_e2e_phase46_perspective

注意: 需要真实 API key（gpt-4.1），token 消耗预计 ~150-250k。
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

# 确保项目根目录在 path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from core.agent import ScholarAgent


# ============================================================
# 隐藏问题定义（用于评估 Agent 是否发现了跨学科问题）
# ============================================================

HIDDEN_PROBLEMS = {
    "P1_rate_condition": {
        "domain": "statistics",
        "description": "Rate condition (Assumption 3.3) 对 Transformer 可能不成立——Transformer 的收敛速率未必满足 o_P(n^{-1/4})",
        "keywords": ["rate condition", "convergence rate", "n^{-1/4}", "assumption 3.3", "regularity"],
    },
    "P2_overlap_violation": {
        "domain": "statistics",
        "description": "AUC=0.94 暗示 propensity score 接近 0 或 1，overlap assumption 近似违反",
        "keywords": ["overlap", "positivity", "propensity", "near-violation", "extreme", "AUC.*0.94"],
    },
    "P3_split_contradiction": {
        "domain": "ml_cs",
        "description": "70/30 train/test split 与 5-fold cross-fitting 描述矛盾",
        "keywords": ["70%", "30%", "cross-fitting", "5-fold", "contradiction", "inconsisten"],
    },
    "P4_synthetic_rct_logic": {
        "domain": "clinical_epi",
        "description": "Synthetic RCT validation 混淆了外部有效性和内部有效性",
        "keywords": ["external validity", "internal validity", "synthetic RCT", "validation", "generalizab"],
    },
    "P5_evalue_misuse": {
        "domain": "statistics",
        "description": "E-value 只针对点估计，不能用于异质性模式的敏感性分析",
        "keywords": ["E-value", "heterogene", "sensitivity", "point estimate", "pattern"],
    },
}


# ============================================================
# 评估函数
# ============================================================

def evaluate_perspective_behavior(agent) -> dict:
    """
    评估 Agent 的视角分裂认知行为。
    
    核心问题: Agent 是否自主决定了"我需要另一个视角"？
    """
    stats = agent.get_stats()
    findings = agent.get_findings()
    tool_history = agent.harness.state.tool_call_history
    
    # --- S1: 是否调用了 spawn_perspective ---
    spawn_calls = [t for t in tool_history if t.get("name") == "spawn_perspective"]
    s1_pass = len(spawn_calls) >= 1
    
    # --- S2: spawn 的 lens 是否合理 ---
    s2_pass = False
    spawn_lenses = []
    if spawn_calls:
        for sc in spawn_calls:
            args = sc.get("input", {})
            lens = args.get("lens", "")
            spawn_lenses.append(lens)
            # 合理的 lens 应该对应论文的跨学科特性
            reasonable_lenses = [
                "statistic", "econometric", "causal", "clinical", "epidemiolog",
                "machine_learning", "deep_learning", "method", "biostatistic"
            ]
            if any(rl in lens.lower() for rl in reasonable_lenses):
                s2_pass = True
    
    # --- S3: spawn 的 question 是否具体 ---
    s3_pass = False
    spawn_questions = []
    if spawn_calls:
        for sc in spawn_calls:
            args = sc.get("input", {})
            question = args.get("question", "")
            spawn_questions.append(question)
            # 具体的 question 应该超过 20 个字符且包含技术术语
            if len(question) > 20:
                s3_pass = True
    
    # --- A1: findings 是否覆盖多个学科维度 ---
    domains_covered = set()
    for f in findings:
        content = f.get("content", "").lower() + " " + f.get("finding", "").lower()
        if any(kw in content for kw in ["rate condition", "convergence", "overlap", "propensity", "e-value"]):
            domains_covered.add("statistics")
        if any(kw in content for kw in ["transformer", "cross-fitting", "fold", "train", "split"]):
            domains_covered.add("ml_cs")
        if any(kw in content for kw in ["clinical", "rct", "trial", "external validity", "patient"]):
            domains_covered.add("clinical_epi")
    a1_domains = len(domains_covered)
    
    # --- A2: 是否识别了隐藏问题 ---
    problems_found = []
    for pid, pinfo in HIDDEN_PROBLEMS.items():
        for f in findings:
            content = f.get("content", "").lower() + " " + f.get("finding", "").lower()
            if any(kw.lower() in content for kw in pinfo["keywords"]):
                problems_found.append(pid)
                break
    a2_count = len(problems_found)
    
    return {
        "s1_spawn_triggered": s1_pass,
        "s1_spawn_count": len(spawn_calls),
        "s2_lens_reasonable": s2_pass,
        "s2_lenses": spawn_lenses,
        "s3_question_specific": s3_pass,
        "s3_questions": spawn_questions,
        "a1_domains_covered": a1_domains,
        "a1_domains": list(domains_covered),
        "a2_problems_found": a2_count,
        "a2_problems": problems_found,
        "total_findings": len(findings),
        "total_turns": stats.get("loop_turns_total", 0),
        "total_tokens": stats.get("total_tokens", 0),
        "search_count": len([t for t in tool_history if t.get("name") == "search_literature"]),
    }


def print_report(result: dict):
    """打印评估报告。"""
    print("\n" + "=" * 70)
    print("Phase 46 E2E 评估报告: 视角分裂认知涌现")
    print("=" * 70)
    
    print("\n--- 核心指标 (Spawn Behavior) ---")
    print(f"  S1 spawn_perspective 触发: {'✅' if result['s1_spawn_triggered'] else '❌'} (共 {result['s1_spawn_count']} 次)")
    print(f"  S2 lens 合理性: {'✅' if result['s2_lens_reasonable'] else '❌'}")
    if result['s2_lenses']:
        for lens in result['s2_lenses']:
            print(f"     → lens: {lens}")
    print(f"  S3 question 具体性: {'✅' if result['s3_question_specific'] else '❌'}")
    if result['s3_questions']:
        for q in result['s3_questions']:
            print(f"     → question: {q[:100]}{'...' if len(q) > 100 else ''}")
    
    print("\n--- 辅助指标 (Review Quality) ---")
    print(f"  A1 学科维度覆盖: {result['a1_domains_covered']}/3 ({', '.join(result['a1_domains'])})")
    print(f"  A2 隐藏问题命中: {result['a2_problems_found']}/5 ({', '.join(result['a2_problems'])})")
    print(f"  Findings 总数: {result['total_findings']}")
    print(f"  搜索次数: {result['search_count']}")
    
    print("\n--- 资源消耗 ---")
    print(f"  Loop turns: {result['total_turns']}")
    print(f"  Total tokens: {result['total_tokens']:,}")
    
    # 总判定
    print("\n--- 总判定 ---")
    core_pass = result['s1_spawn_triggered']
    if core_pass:
        quality_pass = result['s2_lens_reasonable'] and result['s3_question_specific']
        if quality_pass:
            print("  🎉 PASS — Agent 自主触发了视角分裂，且 lens/question 合理")
        else:
            print("  ⚠️  PARTIAL — Agent 触发了 spawn 但质量待提升")
    else:
        print("  ❌ FAIL — Agent 未自主触发 spawn_perspective")
        if result['a2_problems_found'] >= 3:
            print("       (但 Agent 仍发现了多个跨学科问题，说明单视角也有一定能力)")
        print("       → 需要认知层面干预让视角分裂自然涌现")
    
    print("\n" + "=" * 70)


# ============================================================
# 主测试流程
# ============================================================

async def run_test():
    """运行 Phase 46 E2E 测试。"""
    paper_path = PROJECT_ROOT / "examples" / "sample_paper_crossdisciplinary.md"
    
    if not paper_path.exists():
        print(f"❌ 测试论文不存在: {paper_path}")
        return
    
    print("=" * 70)
    print("Phase 46: 视角分裂认知涌现 E2E 测试")
    print("=" * 70)
    print(f"\n论文: {paper_path.name}")
    print(f"场景: 跨学科论文（ML + 因果推断 + 临床医学）")
    print(f"核心观察: Agent 是否自主触发 spawn_perspective")
    print()
    
    # 创建 Agent
    agent = ScholarAgent(
        paper_path=str(paper_path),
        max_loop_turns=30,  # 给足空间让 Agent 自主决策
        token_budget=300_000,
    )
    
    # 用户指令：开放式审稿（不暗示需要多视角）
    user_message = (
        "请审阅这篇论文。这是一篇跨学科的论文，结合了深度学习、因果推断和临床医学。"
        "请给出你的专业审稿意见。"
    )
    
    print(f"用户指令: {user_message}")
    print("-" * 70)
    
    start_time = time.time()
    
    # 运行 Agent
    result = await agent.start(user_intent=user_message)
    
    elapsed = time.time() - start_time
    print(f"\n[完成] 耗时 {elapsed:.1f}s")
    
    # 评估
    eval_result = evaluate_perspective_behavior(agent)
    eval_result["elapsed_seconds"] = elapsed
    
    # 打印报告
    print_report(eval_result)
    
    # 保存详细结果
    report_path = PROJECT_ROOT / "core" / "e2e_report_phase46.json"
    with open(report_path, "w", encoding="utf-8") as f:
        # 序列化时处理不可序列化的对象
        serializable = {k: v for k, v in eval_result.items()}
        json.dump(serializable, f, ensure_ascii=False, indent=2)
    print(f"\n详细报告已保存: {report_path}")
    
    return eval_result


if __name__ == "__main__":
    result = asyncio.run(run_test())
