"""
Phase 34 E2E 验证: 真实顶刊论文的方法论深度审稿能力

测试目标:
    用 Chan, Gentzkow & Yu (2022 QJE) "Selection with Variation in Diagnostic
    Skill: Evidence from Radiologists" 作为测试素材。

    这不是找"预设错误"——论文本身已发表在 QJE，方法论经过严格审稿。
    测试的是 Agent 能否像一个合格的经济学审稿人一样，对论文的关键假设
    和方法论选择提出有深度的追问。

评估标准（方法论追问清单）:
    一个合格的经济学审稿人应该能追问以下至少 3/7 个方向：

    Q1. Quasi-random assignment 假设的可信度
        - VA 医院的排班分配真的是准随机的吗？
        - 可能的 selection：复杂病例是否更可能被分配给资深医生？
        - 需要什么 balance tests / falsification tests？

    Q2. 后验信息的选择性偏差 (ascertainment bias)
        - "后续就诊确诊肺炎"来识别真阳性——但不是所有肺炎都会导致后续就诊
        - 轻症肺炎可能自愈不回来 → 低估 miss rate
        - 这种偏差对 skill vs preference 分离有何影响？

    Q3. 结构模型的函数形式假设 (functional form sensitivity)
        - ROC 曲线的参数化形式是否过于限制？
        - 正态分布 signal 假设对结果有多敏感？
        - 有没有非参数或半参数的替代方案？

    Q4. Skill 的时间稳定性假设
        - 模型假设每个放射科医生有一个固定的 skill level
        - 但 skill 可能随经验增长、疲劳、case load 变化
        - 忽略 skill dynamics 会如何影响估计？

    Q5. 外部有效性 (external validity)
        - VA 医院 → 一般医院的可推广性
        - 肺炎 → 其他疾病的可推广性
        - 胸片 → 其他影像学检查的可推广性

    Q6. Preferences 的内生性
        - 模型中 threshold 是外生参数，但阈值选择本身可能是内生的
        - 医生可能根据自己的 skill level 调整阈值
        - 论文是否充分讨论了 joint determination 的问题？

    Q7. Reduced-form vs Structural 的一致性
        - reduced-form evidence 支持 skill variation 的存在
        - structural model 量化了 skill 和 preferences 的相对重要性
        - 两组结果是否 internally consistent？有没有张力？

运行: python3 -m core.test_e2e_phase34_methodology

注意: 需要真实 API key（gpt-4.1），token 消耗预计 ~150k+。
"""

import asyncio
import json
import os
import re
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
# 方法论追问评估标准
# ============================================================

METHODOLOGY_QUESTIONS = {
    "Q1_quasi_random": {
        "description": "对 quasi-random assignment 假设的追问",
        "keywords": [
            "quasi-random", "random assignment", "selection",
            "balance test", "falsif", "排班", "scheduling",
            "endogen", "non-random", "sorting",
        ],
        "weight": 1.5,  # 最核心的识别假设
    },
    "Q2_ascertainment_bias": {
        "description": "对后验信息选择性偏差的追问",
        "keywords": [
            "ascertainment", "selection bias", "follow-up",
            "subsequent visit", "unobserved", "回诊",
            "miss.*not.*confirm", "self-heal", "mild case",
            "measurement error",
        ],
        "weight": 1.5,
    },
    "Q3_functional_form": {
        "description": "对结构模型函数形式假设的追问",
        "keywords": [
            "functional form", "parametric", "normality",
            "distribut", "ROC.*assum", "signal.*distribut",
            "non-parametric", "semi-parametric",
            "sensitivity.*model", "model.*specif",
        ],
        "weight": 1.0,
    },
    "Q4_skill_stability": {
        "description": "对 skill 时间稳定性假设的追问",
        "keywords": [
            "time-varying", "stability", "dynamic",
            "experience", "learning", "fatigue",
            "case load", "temporal", "固定",
        ],
        "weight": 0.8,
    },
    "Q5_external_validity": {
        "description": "对外部有效性的追问",
        "keywords": [
            "external valid", "generaliz", "VA.*hospital",
            "other disease", "other setting",
            "推广", "适用范围",
        ],
        "weight": 0.8,
    },
    "Q6_preference_endogeneity": {
        "description": "对偏好内生性的追问",
        "keywords": [
            "endogenous.*threshold", "joint determination",
            "preference.*endogen", "optimal threshold",
            "阈值.*内生", "选择.*阈值",
        ],
        "weight": 1.0,
    },
    "Q7_rf_structural_consistency": {
        "description": "对 reduced-form 和 structural 一致性的追问",
        "keywords": [
            "reduced.?form.*structural", "consistency",
            "internal consistency", "tension",
            "descriptive.*structural",
        ],
        "weight": 0.7,
    },
}


def evaluate_findings(findings: list[dict], full_report: str) -> dict:
    """
    评估 Agent 的 findings 是否覆盖了方法论追问标准。

    返回:
        {
            "score": float (0-10),
            "questions_hit": list[str],
            "questions_missed": list[str],
            "weighted_score": float,
            "max_possible": float,
            "detail": dict,
        }
    """
    # 合并所有文本进行匹配
    all_text = full_report.lower()
    for f in findings:
        all_text += " " + f.get("finding", "").lower()
        all_text += " " + f.get("evidence", "").lower()
        all_text += " " + f.get("suggestion", "").lower()

    detail = {}
    questions_hit = []
    questions_missed = []
    weighted_score = 0.0
    max_possible = 0.0

    for qid, qinfo in METHODOLOGY_QUESTIONS.items():
        max_possible += qinfo["weight"]
        hit = False
        matched_keywords = []
        for kw in qinfo["keywords"]:
            if re.search(kw, all_text, re.IGNORECASE):
                hit = True
                matched_keywords.append(kw)

        detail[qid] = {
            "description": qinfo["description"],
            "hit": hit,
            "matched_keywords": matched_keywords,
            "weight": qinfo["weight"],
        }

        if hit:
            questions_hit.append(qid)
            weighted_score += qinfo["weight"]
        else:
            questions_missed.append(qid)

    # Normalize to 0-10 scale
    score = (weighted_score / max_possible) * 10.0 if max_possible > 0 else 0

    return {
        "score": round(score, 2),
        "questions_hit": questions_hit,
        "questions_missed": questions_missed,
        "hit_count": len(questions_hit),
        "total_questions": len(METHODOLOGY_QUESTIONS),
        "weighted_score": round(weighted_score, 2),
        "max_possible": round(max_possible, 2),
        "detail": detail,
    }


def evaluate_cognitive_behavior(agent) -> dict:
    """
    评估 Agent 的认知行为质量（不只是结论内容）。
    """
    stats = agent.get_stats()
    findings = agent.get_findings()

    # Phase 34: 使用 harness.state.tool_call_history（逐条记录）
    tool_history = agent.harness.state.tool_call_history

    # 检查是否使用了战略性阅读（不是逐 section 扫描）
    read_sections_called = [
        t for t in tool_history if t.get("name") == "read_section"
    ]
    unique_sections_read = set(
        t.get("input", {}).get("section", "") for t in read_sections_called
    )

    # 检查 reflect_and_plan 使用
    reflect_calls = [
        t for t in tool_history if t.get("name") == "reflect_and_plan"
    ]
    cognitive_updates = [
        t for t in reflect_calls
        if t.get("input", {}).get("cognitive_update")
    ]

    # 检查 search_literature 使用
    search_calls = [
        t for t in tool_history if t.get("name") == "search_literature"
    ]

    # 检查意图跳转（非顺序阅读模式）
    section_order = [
        t.get("input", {}).get("section", "")
        for t in read_sections_called
    ]
    has_nonlinear_reading = _detect_nonlinear_reading(section_order)

    return {
        "loop_turns": stats.get("loop_turns_total", 0),
        "findings_count": len(findings),
        "high_priority_findings": sum(
            1 for f in findings if f.get("priority") == "high"
        ),
        "total_tokens": stats.get("total_tokens", 0),
        "sections_read": len(unique_sections_read),
        "total_sections_available": 25,  # 排除 full
        "reading_selectivity": (
            1.0 - len(unique_sections_read) / 25.0
            if unique_sections_read else 0
        ),
        "reflect_count": len(reflect_calls),
        "cognitive_update_count": len(cognitive_updates),
        "search_count": len(search_calls),
        "has_nonlinear_reading": has_nonlinear_reading,
        "section_read_order": section_order[:20],  # 截取前 20 次
    }


def _detect_nonlinear_reading(section_order: list[str]) -> bool:
    """
    检测非线性阅读模式（意图跳转的证据）。
    如果 Agent 先读了后面的 section 再回到前面，说明有意图驱动的跳转。
    """
    if len(section_order) < 3:
        return False

    # 简单启发式：论文大致顺序
    rough_order = [
        "abstract", "introduction", "empirical framework",
        "quasi-random assignment", "identification",
        "variables", "results", "main results",
        "structural analysis", "roc curves",
        "estimation", "welfare", "policy implications",
        "robustness", "extensions", "conclusion",
    ]

    # 检测是否有"回跳"模式
    positions = []
    for s in section_order:
        s_lower = s.lower()
        for i, ref in enumerate(rough_order):
            if ref in s_lower or s_lower in ref:
                positions.append(i)
                break

    # 如果有位置序列不是单调递增的，说明有跳转
    if len(positions) >= 3:
        for i in range(1, len(positions)):
            if positions[i] < positions[i-1] - 1:  # 允许小范围回看
                return True

    return False


async def main():
    print("=" * 70)
    print("  Phase 34: Methodology Depth Assessment")
    print("  Paper: Chan, Gentzkow & Yu (2022 QJE)")
    print("  'Selection with Variation in Diagnostic Skill'")
    print("=" * 70)

    paper_path = str(PROJECT_ROOT / "tests" / "papers" / "radiology_selection.pdf")
    model = os.environ.get("LLM_MODEL", "gpt-4.1")

    print(f"  Paper: {paper_path}")
    print(f"  Model: {model}")
    print(f"  Max turns: 20")
    print(f"  Token budget: 300000")
    print()

    # 初始化 Agent
    agent = ScholarAgent(
        paper_path=paper_path,
        model=None,  # 使用 .env 配置
        verbose=True,
        max_loop_turns=20,     # 给足探索空间
        token_budget=300000,   # 真实长论文需要更多 budget
    )

    # 运行审稿
    print("[Phase 34] Agent 启动对 QJE 论文的方法论审稿...")
    print("-" * 50)
    t0 = time.time()

    response = await agent.start()

    t1 = time.time()
    elapsed = t1 - t0

    # ========================================
    # 收集结果
    # ========================================
    print(f"\n{'=' * 70}")
    print(f"  审稿完成 ({elapsed:.1f}s)")
    print(f"{'=' * 70}")

    # Agent 的最终报告
    print("\n[Agent Report (前 3000 字)]")
    print("-" * 40)
    print(response[:3000])
    if len(response) > 3000:
        print(f"\n... [总计 {len(response)} 字符]")

    # ========================================
    # 评估方法论深度
    # ========================================
    findings = agent.get_findings()
    methodology_eval = evaluate_findings(findings, response)

    print(f"\n{'=' * 70}")
    print("  方法论深度评估")
    print(f"{'=' * 70}")
    print(f"  综合评分: {methodology_eval['score']}/10.0")
    print(f"  命中问题: {methodology_eval['hit_count']}/{methodology_eval['total_questions']}")
    print(f"  加权分数: {methodology_eval['weighted_score']}/{methodology_eval['max_possible']}")
    print()

    print("  命中的方法论追问:")
    for qid in methodology_eval["questions_hit"]:
        info = methodology_eval["detail"][qid]
        print(f"    ✓ {info['description']} (keywords: {info['matched_keywords'][:3]})")
    print()
    print("  未命中的方法论追问:")
    for qid in methodology_eval["questions_missed"]:
        info = methodology_eval["detail"][qid]
        print(f"    ✗ {info['description']}")

    # ========================================
    # 评估认知行为
    # ========================================
    behavior_eval = evaluate_cognitive_behavior(agent)

    print(f"\n{'=' * 70}")
    print("  认知行为评估")
    print(f"{'=' * 70}")
    print(f"  Loop 轮次: {behavior_eval['loop_turns']}")
    print(f"  Findings: {behavior_eval['findings_count']} (high: {behavior_eval['high_priority_findings']})")
    print(f"  Tokens: ~{behavior_eval['total_tokens']}")
    print(f"  Sections 读取: {behavior_eval['sections_read']}/{behavior_eval['total_sections_available']}")
    print(f"  阅读选择性: {behavior_eval['reading_selectivity']:.1%} (越高=越有策略)")
    print(f"  反思次数: {behavior_eval['reflect_count']}")
    print(f"  认知更新: {behavior_eval['cognitive_update_count']}")
    print(f"  文献搜索: {behavior_eval['search_count']}")
    print(f"  非线性阅读: {'是' if behavior_eval['has_nonlinear_reading'] else '否'}")
    print(f"  阅读顺序: {' → '.join(behavior_eval['section_read_order'][:10])}")

    # ========================================
    # 综合判定
    # ========================================
    print(f"\n{'=' * 70}")
    print("  Phase 34 综合判定")
    print(f"{'=' * 70}")

    # 通过标准
    methodology_pass = methodology_eval["hit_count"] >= 3
    behavior_pass = (
        behavior_eval["reflect_count"] >= 1
        and behavior_eval["high_priority_findings"] >= 2
    )
    strategic_reading = behavior_eval["reading_selectivity"] > 0.3

    verdict = "PASS" if (methodology_pass and behavior_pass) else "NEEDS_WORK"

    print(f"  方法论深度: {'PASS' if methodology_pass else 'FAIL'} ({methodology_eval['hit_count']}/7, 要求≥3)")
    print(f"  认知行为: {'PASS' if behavior_pass else 'FAIL'}")
    print(f"  战略性阅读: {'PASS' if strategic_reading else 'FAIL'} (选择性 {behavior_eval['reading_selectivity']:.0%})")
    print(f"  总判定: {verdict}")

    # ========================================
    # 保存详细报告
    # ========================================
    report = {
        "phase": 34,
        "paper": "Chan, Gentzkow & Yu (2022 QJE) - Selection with Variation in Diagnostic Skill",
        "model": model,
        "elapsed_seconds": round(elapsed, 1),
        "verdict": verdict,
        "methodology_eval": methodology_eval,
        "behavior_eval": behavior_eval,
        "findings": findings,
        "agent_report_length": len(response),
        "agent_report_preview": response[:5000],
    }

    report_path = PROJECT_ROOT / "core" / "e2e_report_phase34.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"\n  详细报告已保存: {report_path}")

    return verdict


if __name__ == "__main__":
    result = asyncio.run(main())
    sys.exit(0 if result == "PASS" else 1)
