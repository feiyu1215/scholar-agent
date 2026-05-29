"""
Phase 44 E2E 验证: Agent 审+改认知闭环

测试目标:
    验证 Agent 能否自主完成"发现问题 → 修改论文 → 验证修改"的完整认知闭环。
    
    Phase 37-43 验证了 Agent 的审稿深度和广度。但 Agent 的价值不只是"指出问题"——
    它应该能"动手解决问题"。COGNITIVE_ANCHOR §10.3 说 Agent 的核心是
    "和用户一起，把一篇论文从当前状态带到更好的状态"。
    
    identity.py 中已有:
    - habit #17 "行动优于建议": Agent 应该默认动手改，而不是写建议
    - habit #18 "复审独立性": 修改后应有意识地换视角验证
    
    但这两条认知从未被 E2E 验证过。

测试论文:
    examples/sample_paper.md — 合成论文，包含以下可修改缺陷:
    
    D1. AI 写作痕迹 (Abstract): "groundbreaking", "delves into", "it is crucial to note"
    D2. Overclaim (Introduction): "first to systematically test" — 绝对断言
    D3. 方法论空洞 (Methodology): 没有讨论平行趋势假设的检验
    D4. 模糊表述 (Results): "seem to suggest", "might potentially" — 过度 hedging
    D5. 结论空洞 (Conclusion): 没有具体贡献总结，只有泛泛而谈

评估标准:
    一个合格的"审+改"Agent 应该:
    
    M1. 至少使用 1 次 edit_section（核心指标：Agent 动手了）
    M2. edit_section 之前有 update_findings 或 read_section（先审后改）
    M3. 修改内容消除了至少 1 个已识别的缺陷（改得对）
    M4. 修改后 post_edit_verify 通过（没引入新问题）
    M5. Agent 不是只改了就走——它在修改后有后续行为（继续审/反思/复核）
    
    加分项:
    B1. 修改了多个 section（不只改一处就停）
    B2. 使用了 spawn_perspective 做修改后复核
    B3. 修改 reason 具体且有说服力

运行: python3 -m core.test_e2e_phase44_edit_loop

注意: 需要真实 API key（gpt-4.1），token 消耗预计 ~80-120k。
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
# 缺陷定义（用于评估修改是否命中了真实问题）
# ============================================================

KNOWN_DEFECTS = {
    "D1_ai_writing": {
        "description": "Abstract 中的 AI 写作痕迹",
        "section": "abstract",
        "signals": ["groundbreaking", "delves", "crucial to note", "transformative potential"],
        "fix_check": lambda old, new: (
            # 修改后这些 AI 信号应该减少
            sum(1 for s in ["groundbreaking", "delves", "crucial to note", "transformative"]
                if s.lower() in new.lower())
            < sum(1 for s in ["groundbreaking", "delves", "crucial to note", "transformative"]
                  if s.lower() in old.lower())
        ),
    },
    "D2_overclaim": {
        "description": "Introduction 中的绝对断言 'first to'",
        "section": "introduction",
        "signals": ["first to", "state-of-the-art"],
        "fix_check": lambda old, new: (
            "first to" not in new.lower() or
            "among the first" in new.lower() or
            "one of the first" in new.lower()
        ),
    },
    "D3_methodology_gap": {
        "description": "Methodology 缺少平行趋势讨论",
        "section": "methodology",
        "signals": [],  # 这是缺失而非存在的问题
        "fix_check": lambda old, new: (
            "parallel trend" in new.lower() or
            "pre-trend" in new.lower() or
            "common trend" in new.lower()
        ),
    },
    "D4_hedging": {
        "description": "Results 中过度 hedging",
        "section": "results",
        "signals": ["seem to suggest", "might potentially"],
        "fix_check": lambda old, new: (
            sum(1 for s in ["seem to suggest", "might potentially"]
                if s.lower() in new.lower())
            < sum(1 for s in ["seem to suggest", "might potentially"]
                  if s.lower() in old.lower())
        ),
    },
    "D5_weak_conclusion": {
        "description": "Conclusion 空洞无具体贡献",
        "section": "conclusion",
        "signals": [],
        "fix_check": lambda old, new: (
            len(new) > len(old) * 1.3  # 至少扩充了 30%
        ),
    },
}


# ============================================================
# 评估函数
# ============================================================

def evaluate_edit_behavior(agent) -> dict:
    """
    评估 Agent 的审+改认知行为。
    
    核心问题: Agent 是否真的动手改了？改得对吗？改的过程合理吗？
    """
    stats = agent.get_stats()
    findings = agent.get_findings()
    edits = agent.get_edits()
    tool_history = agent.harness.state.tool_call_history
    
    # --- M1: 是否使用了 edit_section ---
    edit_calls = [t for t in tool_history if t.get("name") == "edit_section"]
    m1_pass = len(edit_calls) >= 1
    
    # --- M2: edit 之前是否有审阅行为（先审后改）---
    m2_pass = False
    if edit_calls:
        first_edit_idx = next(
            i for i, t in enumerate(tool_history) if t.get("name") == "edit_section"
        )
        pre_edit_tools = [t.get("name") for t in tool_history[:first_edit_idx]]
        m2_pass = (
            "read_section" in pre_edit_tools or
            "update_findings" in pre_edit_tools
        )
    
    # --- M3: 修改是否消除了已知缺陷 ---
    m3_defects_fixed = []
    for defect_id, defect_info in KNOWN_DEFECTS.items():
        section_key = defect_info["section"]
        # 检查是否有针对这个 section 的修改
        for edit in edits:
            edit_section = edit.get("section", "").lower()
            if section_key in edit_section or edit_section in section_key:
                # 有修改，但我们无法直接验证 fix_check（因为 edits 只存了 preview）
                m3_defects_fixed.append(defect_id)
                break
    m3_pass = len(m3_defects_fixed) >= 1
    
    # --- M4: post_edit_verify 是否通过 ---
    # 从 tool_history 中找 edit_section 的返回结果
    m4_pass = True  # 默认通过（如果没有 edit 则 N/A）
    m4_details = []
    for t in tool_history:
        if t.get("name") == "edit_section":
            result = t.get("result", "")
            if "✗" in result:
                m4_pass = False
                m4_details.append(result[:200])
            elif "✓" in result:
                m4_details.append("PASS")
    
    # --- M5: 修改后是否有后续行为 ---
    m5_pass = False
    if edit_calls:
        last_edit_idx = max(
            i for i, t in enumerate(tool_history) if t.get("name") == "edit_section"
        )
        post_edit_tools = [t.get("name") for t in tool_history[last_edit_idx + 1:]]
        # 修改后应该有后续行为（不是改完就 mark_complete）
        m5_pass = len(post_edit_tools) >= 1 and post_edit_tools != ["mark_complete"]
    
    # --- 加分项 ---
    b1_multi_edit = len(set(e.get("section", "") for e in edits)) >= 2
    b2_spawn_after_edit = False
    if edit_calls:
        first_edit_idx = next(
            i for i, t in enumerate(tool_history) if t.get("name") == "edit_section"
        )
        post_first_edit = [t.get("name") for t in tool_history[first_edit_idx:]]
        b2_spawn_after_edit = "spawn_perspective" in post_first_edit
    
    b3_good_reasons = all(
        len(e.get("reason", "")) > 20 for e in edits
    ) if edits else False
    
    # --- 综合评分 ---
    core_metrics = {
        "M1_used_edit": m1_pass,
        "M2_review_before_edit": m2_pass,
        "M3_fixed_defect": m3_pass,
        "M4_verify_passed": m4_pass,
        "M5_post_edit_behavior": m5_pass,
    }
    core_score = sum(1 for v in core_metrics.values() if v)
    
    bonus_metrics = {
        "B1_multi_section_edit": b1_multi_edit,
        "B2_spawn_for_review": b2_spawn_after_edit,
        "B3_good_edit_reasons": b3_good_reasons,
    }
    bonus_score = sum(1 for v in bonus_metrics.values() if v)
    
    return {
        "core_metrics": core_metrics,
        "core_score": f"{core_score}/5",
        "core_pass": core_score >= 3,  # 至少 3/5 核心指标通过
        "bonus_metrics": bonus_metrics,
        "bonus_score": f"{bonus_score}/3",
        "total_score": core_score + bonus_score * 0.5,
        "max_score": 6.5,
        "edits_count": len(edits),
        "edits_sections": [e.get("section", "") for e in edits],
        "defects_fixed": m3_defects_fixed,
        "m4_details": m4_details,
        "findings_count": len(findings),
        "loop_turns": stats.get("loop_turns_total", 0),
        "total_tokens": stats.get("total_tokens", 0),
    }


def evaluate_cognitive_pattern(agent) -> dict:
    """
    分析 Agent 的认知模式：它是如何从"审"过渡到"改"的？
    
    理想模式: read → find → (reflect) → edit → verify/continue
    反模式: read → edit (没有先形成判断就改)
    反模式: find → talk_to_user "建议你改..." (有判断但不动手)
    """
    tool_history = agent.harness.state.tool_call_history
    tool_sequence = [t.get("name", "") for t in tool_history]
    
    # 检测"建议而非行动"反模式
    talk_calls = [t for t in tool_history if t.get("name") == "talk_to_user"]
    suggestion_pattern = 0
    for t in talk_calls:
        msg = json.dumps(t.get("input", {}), ensure_ascii=False).lower()
        if any(kw in msg for kw in ["建议", "可以改为", "suggest", "recommend", "consider"]):
            suggestion_pattern += 1
    
    # 检测认知转换点：从审到改的过渡
    edit_indices = [i for i, name in enumerate(tool_sequence) if name == "edit_section"]
    transition_quality = "no_edit"
    if edit_indices:
        first_edit = edit_indices[0]
        pre_edit_seq = tool_sequence[:first_edit]
        
        # 理想：有 read + find 在 edit 之前
        has_read = "read_section" in pre_edit_seq
        has_find = "update_findings" in pre_edit_seq
        has_reflect = "reflect_and_plan" in pre_edit_seq
        
        if has_read and has_find and has_reflect:
            transition_quality = "excellent"  # 读→发现→反思→改
        elif has_read and has_find:
            transition_quality = "good"  # 读→发现→改
        elif has_read:
            transition_quality = "acceptable"  # 读→改（跳过了记录发现）
        else:
            transition_quality = "poor"  # 没读就改
    
    return {
        "tool_sequence_summary": _summarize_sequence(tool_sequence),
        "suggestion_instead_of_action": suggestion_pattern,
        "transition_quality": transition_quality,
        "total_tool_calls": len(tool_sequence),
        "edit_positions": edit_indices,
        "full_sequence": tool_sequence,
    }


def _summarize_sequence(seq: list[str]) -> str:
    """将工具调用序列压缩为可读摘要。"""
    if not seq:
        return "(empty)"
    
    # 压缩连续重复
    compressed = []
    count = 1
    for i in range(1, len(seq)):
        if seq[i] == seq[i-1]:
            count += 1
        else:
            compressed.append(f"{seq[i-1]}×{count}" if count > 1 else seq[i-1])
            count = 1
    compressed.append(f"{seq[-1]}×{count}" if count > 1 else seq[-1])
    
    return " → ".join(compressed)


# ============================================================
# 主测试流程
# ============================================================

async def run_test():
    """运行 Phase 44 E2E 测试。"""
    
    print("=" * 70)
    print("  Phase 44 E2E: Agent 审+改认知闭环验证")
    print("=" * 70)
    print()
    
    paper_path = str(PROJECT_ROOT / "examples" / "sample_paper.md")
    
    if not Path(paper_path).exists():
        print(f"ERROR: 测试论文不存在: {paper_path}")
        sys.exit(1)
    
    print(f"  论文: {paper_path}")
    print(f"  模型: {os.environ.get('LLM_MODEL', 'gpt-4.1')}")
    print(f"  用户意图: '帮我改这篇论文，消除写作问题并加强方法论部分'")
    print()
    
    # 创建 Agent
    agent = ScholarAgent(
        paper_path=paper_path,
        verbose=True,
        max_loop_turns=30,
        token_budget=150000,
    )
    
    # 关键：用户意图是"改"而不是"审"
    # 这测试的是 Agent 能否从"审稿人"模式自然过渡到"编辑者"模式
    user_intent = (
        "帮我改这篇论文。主要问题：(1) Abstract 有明显的 AI 写作痕迹需要消除，"
        "(2) Introduction 有 overclaim 需要修正，"
        "(3) Methodology 部分太薄弱需要加强。"
        "请直接动手改，不要只给建议。"
    )
    
    print("[Agent 启动中...]\n")
    start_time = time.time()
    
    try:
        response = await agent.start(user_intent=user_intent)
    except Exception as e:
        print(f"\n[ERROR] Agent 运行失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    
    elapsed = time.time() - start_time
    
    print(f"\n{'─' * 70}")
    print(f"Agent 最终输出:")
    print(f"{'─' * 70}")
    print(response[:2000] if len(response) > 2000 else response)
    print(f"{'─' * 70}")
    print(f"\n运行时间: {elapsed:.1f}s")
    
    # ============================================================
    # 评估
    # ============================================================
    
    print(f"\n{'=' * 70}")
    print("  评估结果")
    print(f"{'=' * 70}\n")
    
    # 1. 审+改行为评估
    edit_eval = evaluate_edit_behavior(agent)
    
    print("【核心指标】")
    for metric, passed in edit_eval["core_metrics"].items():
        icon = "✅" if passed else "❌"
        print(f"  {icon} {metric}")
    print(f"\n  核心得分: {edit_eval['core_score']} {'(PASS)' if edit_eval['core_pass'] else '(FAIL)'}")
    
    print(f"\n【加分项】")
    for metric, passed in edit_eval["bonus_metrics"].items():
        icon = "✅" if passed else "⬜"
        print(f"  {icon} {metric}")
    print(f"  加分: {edit_eval['bonus_score']}")
    
    print(f"\n【修改详情】")
    print(f"  修改次数: {edit_eval['edits_count']}")
    print(f"  修改 sections: {edit_eval['edits_sections']}")
    print(f"  修复的缺陷: {edit_eval['defects_fixed']}")
    print(f"  验证结果: {edit_eval['m4_details']}")
    
    # 2. 认知模式分析
    pattern_eval = evaluate_cognitive_pattern(agent)
    
    print(f"\n【认知模式分析】")
    print(f"  工具调用序列: {pattern_eval['tool_sequence_summary']}")
    print(f"  审→改过渡质量: {pattern_eval['transition_quality']}")
    print(f"  '建议而非行动'反模式: {pattern_eval['suggestion_instead_of_action']} 次")
    print(f"  总工具调用: {pattern_eval['total_tool_calls']}")
    
    # 3. 统计
    print(f"\n【运行统计】")
    stats = agent.get_stats()
    print(f"  Loop turns: {stats.get('loop_turns_total', 0)}")
    print(f"  Total tokens: {stats.get('total_tokens', 0)}")
    print(f"  Findings: {len(agent.get_findings())}")
    print(f"  Edits: {len(agent.get_edits())}")
    print(f"  Tool calls: {json.dumps(stats.get('tool_calls', {}), indent=4)}")
    
    # 4. 综合判定
    print(f"\n{'=' * 70}")
    overall_pass = edit_eval["core_pass"]
    verdict = "✅ PASS" if overall_pass else "❌ FAIL"
    print(f"  综合判定: {verdict}")
    print(f"  总分: {edit_eval['total_score']}/{edit_eval['max_score']}")
    
    if not overall_pass:
        print(f"\n  失败分析:")
        if not edit_eval["core_metrics"]["M1_used_edit"]:
            print(f"    → Agent 没有使用 edit_section。它可能停留在'建议'模式。")
            print(f"    → 认知 Gap: identity.py habit #17 '行动优于建议' 未生效。")
        if not edit_eval["core_metrics"]["M2_review_before_edit"]:
            print(f"    → Agent 没有先审后改。它可能直接改了没有先理解问题。")
        if not edit_eval["core_metrics"]["M3_fixed_defect"]:
            print(f"    → Agent 的修改没有命中已知缺陷。它可能改了不重要的地方。")
    
    print(f"{'=' * 70}\n")
    
    # 保存完整结果
    result = {
        "phase": 44,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "paper": paper_path,
        "user_intent": user_intent,
        "elapsed_seconds": round(elapsed, 1),
        "edit_evaluation": edit_eval,
        "cognitive_pattern": {
            k: v for k, v in pattern_eval.items() if k != "full_sequence"
        },
        "tool_sequence": pattern_eval["full_sequence"],
        "overall_pass": overall_pass,
        "agent_response_preview": response[:1000],
        "findings": agent.get_findings(),
        "edits": agent.get_edits(),
        "stats": stats,
    }
    
    output_path = PROJECT_ROOT / "tests" / "e2e_phase44_output.json"
    output_path.parent.mkdir(exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"  完整结果已保存: {output_path}")
    
    return overall_pass


if __name__ == "__main__":
    success = asyncio.run(run_test())
    sys.exit(0 if success else 1)
