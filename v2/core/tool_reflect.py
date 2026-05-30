"""
tool_reflect.py — 元认知反思工具模块

从 harness.py 提取。包含:
- reflect_and_plan: Agent 主动触发的结构化反思
- compute_marginal_productivity: 边际产出信号计算 (Phase 52)
- check_stagnation: 停滞检测 (Phase 55)

辅助函数 _detect_finding_overlaps 保留在 harness.py（被 reflect 和 update_findings 共用）。
"""

from __future__ import annotations

import re
from typing import Any

from core.state import WorkspaceState
from core.metacognition import CognitiveState
from core.text_utils import extract_terms


def reflect_and_plan(
    state: WorkspaceState,
    cognitive_state: CognitiveState,
    strategy_transitions: list,
    last_strategy: str,
    search_log: list,
    gate_config: Any,
    args: dict,
) -> tuple[str, str]:
    """
    元认知工具：Agent 主动触发反思。

    Returns:
        (result_text, new_last_strategy) — 反思结果文本 + 更新后的策略标识
    """
    trigger = args.get("trigger", "自主反思")
    current_thinking = args.get("current_thinking", "")

    s = state

    # 1. 进度摘要
    findings_by_priority = {
        "high": [f for f in s.findings if f.get("priority") == "high"],
        "medium": [f for f in s.findings if f.get("priority") == "medium"],
        "low": [f for f in s.findings if f.get("priority") == "low"],
    }
    unverified = [f for f in s.findings if f.get("status") == "needs_verification"]

    # 2. 资源状态
    turns_remaining = s.max_loop_turns - s.loop_turns
    token_pct = (s.total_tokens / s.token_budget * 100) if s.token_budget > 0 else 0
    is_unlimited = (s.token_budget <= 0)

    # 3. 覆盖度分析
    all_sections = set(k for k in s.paper_sections if k != "full")
    touched_sections = set()
    for f in s.findings:
        sec = f.get("section", "")
        if sec:
            for name in all_sections:
                if sec.lower() in name.lower() or name.lower() in sec.lower():
                    touched_sections.add(name)
                    break
    untouched = sorted(all_sections - touched_sections)

    # 4. 开放问题
    open_questions = []
    for f in unverified:
        open_questions.append(f"- [{f.get('priority', '?')}] {f['finding'][:100]}")

    # 5. 组装反思上下文
    lines = [
        "═══ 反思时刻 ═══",
        f"触发原因: {trigger}",
        "",
        "【进度】",
        f"  已记录 {len(s.findings)} 条发现: {len(findings_by_priority['high'])} high, "
        f"{len(findings_by_priority['medium'])} medium, {len(findings_by_priority['low'])} low",
        f"  已修改 {len(s.edits)} 个 section",
        "",
        "【资源】",
        f"  轮次: 已用 {s.loop_turns}/{s.max_loop_turns} (剩余 {turns_remaining})",
        f"  Token: ~{s.total_tokens} (无上限模式)" if is_unlimited else f"  Token: ~{s.total_tokens} / {s.token_budget} ({token_pct:.0f}% 已消耗)",
        "",
        "【覆盖度】",
        f"  论文共 {len(all_sections)} sections",
        f"  已触及 {len(touched_sections)} sections: {', '.join(sorted(touched_sections)[:8])}",
    ]

    if untouched:
        lines.append(f"  尚未阅读: {', '.join(untouched[:10])}"
                     + (f" ...等 {len(untouched)} 个" if len(untouched) > 10 else ""))

    if open_questions:
        lines.append("")
        lines.append(f"【待验证 ({len(unverified)} 条)】")
        lines.extend(open_questions[:5])
        if len(open_questions) > 5:
            lines.append(f"  ...还有 {len(open_questions) - 5} 条")

    # Phase 39+41: 外部验证状态
    search_count = len(search_log)
    lines.append("")
    lines.append("【外部验证】")
    lines.append(f"  search_literature 已调用 {search_count} 次")
    if search_count == 0 and len(s.findings) > 0:
        lines.append("  ⚠ 你有发现但尚未查过外部文献——你的判断完全基于论文自身的叙述。")
        lines.append("  一个好审稿人会用外部文献校准自己的判断——尤其是对方法论和核心 claim 的判断。")
    elif search_count == 0 and len(s.sections_read) >= 4:
        lines.append(f"  ⚠ 你已读了 {len(s.sections_read)} 个 section 但尚未查过外部文献。")
        lines.append("  即使你还在形成判断，外部文献可以帮你更快定位论文的真正弱点。")
    elif search_count > 0 and len(s.findings) >= 2:
        # Phase P1: 方法论判断的外部校准检查
        methodology_keywords = {
            "bandwidth", "cluster", "bootstrap", "robustness", "identification",
            "instrument", "validity", "assumption", "estimat", "specif",
            "heterogeneity", "sensitivity", "placebo", "falsif",
            "synthetic", "bunching", "shift-share", "discontinuity",
        }
        search_queries_text = " ".join(
            entry.get("query", "") for entry in search_log
        ).lower()

        uncalibrated_method_findings = []
        for f in s.findings:
            if f.get("priority") != "high":
                continue
            finding_lower = f["finding"].lower()
            is_methodology = any(kw in finding_lower for kw in methodology_keywords)
            if not is_methodology:
                continue
            # 检查搜索历史中是否有相关查询
            finding_terms = set(re.findall(r'[a-zA-Z]{5,}', finding_lower))
            query_terms = set(re.findall(r'[a-zA-Z]{5,}', search_queries_text))
            overlap = len(finding_terms & query_terms)
            if overlap < 2:  # 搜索历史中几乎没有覆盖这个 finding 的查询
                uncalibrated_method_findings.append(f["finding"][:60])

        if uncalibrated_method_findings:
            lines.append(f"  💡 你搜索了文献，但有 {len(uncalibrated_method_findings)} 条高优方法论判断"
                        f"似乎没有对应的外部校准：")
            for desc in uncalibrated_method_findings[:2]:
                lines.append(f"    • {desc}")
            lines.append("  你对这些判断的信心是基于确切知识还是'大概记得'？")

    # Phase 40: 追查缺口事实
    unverified_findings = [f for f in s.findings if f.get("status") == "needs_verification"]
    if unverified_findings:
        lines.append("")
        lines.append(f"【追查缺口】")
        lines.append(f"  你有 {len(unverified_findings)} 条发现标记为 needs_verification:")
        for uf in unverified_findings[:4]:
            lines.append(f"    • {uf['finding'][:80]}")
        lines.append(f"  这些发现目前只是你的怀疑——你还没有回去验证它们是否真的成立。")
        lines.append(f"  一个好审稿人不会把'我怀疑有问题'写进 report——他会追查到'确认有问题'或'排除了这个怀疑'。")

    # Phase 40: Findings 重叠检测
    if len(s.findings) >= 2:
        overlaps = _detect_finding_overlaps(s.findings)
        if overlaps:
            lines.append("")
            lines.append("【发现重叠警告】")
            for pair_desc in overlaps[:3]:
                lines.append(f"  ⚠ {pair_desc}")
            lines.append("  重复的发现不增加审稿价值——考虑合并它们，然后去找新的角度。")

    # Phase 43: 维度覆盖度分析
    if len(s.findings) >= 2:
        finding_texts = " ".join(f.get("finding", "") for f in s.findings).lower()
        dimension_keywords = {
            "识别假设/因果推断": ["quasi-random", "random assignment", "selection", "identification", "causal", "endogen"],
            "结构模型/函数形式": ["structural", "functional form", "parametric", "distribut", "model specif"],
            "外部有效性": ["external valid", "generaliz", "other setting", "推广"],
            "数据质量/测量": ["measurement", "ascertainment", "data quality", "missing", "attrition"],
            "时间稳定性/动态": ["time-varying", "stability", "dynamic", "temporal", "learning"],
        }
        covered_dims = []
        uncovered_dims = []
        for dim_name, keywords in dimension_keywords.items():
            if any(kw in finding_texts for kw in keywords):
                covered_dims.append(dim_name)
            else:
                uncovered_dims.append(dim_name)

        if covered_dims and uncovered_dims and len(covered_dims) <= 2:
            lines.append("")
            lines.append("【维度覆盖度】")
            lines.append(f"  你当前的发现集中在: {', '.join(covered_dims)}")
            lines.append(f"  尚未触及的维度: {', '.join(uncovered_dims)}")
            lines.append(f"  （这不是要求你覆盖所有维度——只是让你知道你目前的视角范围。）")

    # Phase 46: 学科能力边界提示
    if len(s.findings) >= 2 and not any(
        t.get("name") == "spawn_perspective" for t in s.tool_call_history
    ):
        paper_text_sample = " ".join(
            content[:500] for content in s.paper_sections.values() if content
        ).lower()
        discipline_signals = {
            "统计/计量方法": ["propensity", "causal inference", "double machine learning", "dml", "semiparametric", "asymptotic"],
            "机器学习/深度学习": ["transformer", "neural network", "deep learning", "attention", "gradient", "training"],
            "临床医学/流行病学": ["patient", "clinical", "treatment effect", "randomized trial", "rct", "ehr", "electronic health"],
            "经济学/社会科学": ["difference-in-differences", "instrumental variable", "regression discontinuity", "welfare"],
        }
        detected_disciplines = []
        for disc_name, signals in discipline_signals.items():
            if sum(1 for sig in signals if sig in paper_text_sample) >= 2:
                detected_disciplines.append(disc_name)

        if len(detected_disciplines) >= 2:
            findings_text = " ".join(f.get("finding", "") for f in s.findings).lower()
            findings_disciplines = []
            for disc_name, signals in discipline_signals.items():
                if any(sig in findings_text for sig in signals):
                    findings_disciplines.append(disc_name)

            uncovered_disciplines = [d for d in detected_disciplines if d not in findings_disciplines]

            if uncovered_disciplines:
                lines.append("")
                lines.append("【学科覆盖度】")
                lines.append(f"  这篇论文涉及 {len(detected_disciplines)} 个学科: {', '.join(detected_disciplines)}")
                covered_str = ', '.join(findings_disciplines) if findings_disciplines else '(尚未明确)'
                lines.append(f"  你的发现目前覆盖: {covered_str}")
                lines.append(f"  尚未深入审视: {', '.join(uncovered_disciplines)}")
                lines.append(f"  （你可以用 spawn_perspective 请一个该领域的独立专家来审视你不确定的部分。）")

    # Phase 52: 边际产出信号
    if s.loop_turns >= 6 and len(s.findings) >= 2:
        productivity_signal = compute_marginal_productivity(state, cognitive_state)
        if productivity_signal:
            lines.append("")
            lines.append("【边际产出】")
            lines.extend(f"  {line}" for line in productivity_signal)

    lines.append("")
    lines.append("【反思提示】")
    lines.append("  基于以上信息，思考:")
    lines.append("  1. 我的主要假说是否已被充分验证/推翻？")
    lines.append("  2. 剩余资源够做什么？该深入还是该收尾？")
    lines.append("  3. 有没有我遗漏的重要角度？")
    lines.append("  4. 我的判断有没有外部校准？（是否需要搜索文献确认？）")
    lines.append("  5. 这篇论文是否跨学科？我对每个学科的判断置信度是否一样？")
    lines.append("  6. 我在当前方向上的边际产出是否在递减？是否该换个角度？")

    if current_thinking:
        lines.append(f"\n你当前的思路: {current_thinking}")

    # Phase 32: 处理认知状态更新
    cognitive_update = args.get("cognitive_update")
    if cognitive_update and isinstance(cognitive_update, dict):
        cognitive_state.update_from_reflection(cognitive_update)
        cognitive_state.last_updated_turn = s.loop_turns
        lines.append("\n[认知状态已更新]")
    else:
        # 自动推断策略
        cognitive_state.auto_infer_strategy({
            "sections_read_count": len(s.sections_read),
            "total_sections": len([k for k in s.paper_sections if k != "full"]),
            "findings_count": len(s.findings),
            "edits_count": len(s.edits),
            "loop_turns": s.loop_turns,
        })
        cognitive_state.last_updated_turn = s.loop_turns

    # Phase 54: 追踪策略切换
    new_strategy = cognitive_state.current_strategy
    if new_strategy != last_strategy and last_strategy != "undecided":
        strategy_transitions.append((last_strategy, new_strategy))

    return "\n".join(lines), new_strategy


def compute_marginal_productivity(
    state: WorkspaceState,
    cognitive_state: CognitiveState,
) -> list[str] | None:
    """
    Phase 52: 计算边际产出信号。

    核心逻辑:
    - 将 findings 按 recorded_at_turn 分布到时间轴上
    - 计算"最近 window 轮"的产出密度 vs "之前所有轮"的产出密度
    - 当最近窗口的密度显著低于历史平均时，生成信号
    """
    s = state
    current_turn = s.loop_turns

    findings_with_turn = [
        f for f in s.findings if "recorded_at_turn" in f
    ]

    if len(findings_with_turn) < 2:
        return None

    # 动态窗口
    window_size = max(4, current_turn // 3)
    window_start = current_turn - window_size

    recent_findings = [f for f in findings_with_turn if f["recorded_at_turn"] >= window_start]
    earlier_findings = [f for f in findings_with_turn if f["recorded_at_turn"] < window_start]

    recent_density = len(recent_findings) / window_size if window_size > 0 else 0
    earlier_turns = window_start
    earlier_density = len(earlier_findings) / earlier_turns if earlier_turns > 0 else 0

    if earlier_density <= 0:
        return None

    decay_ratio = recent_density / earlier_density if earlier_density > 0 else 1.0

    if decay_ratio >= 0.4:
        return None

    lines = []
    lines.append(f"最近 {window_size} 轮 (Turn {window_start+1}~{current_turn}): "
                 f"产出 {len(recent_findings)} 条新发现 "
                 f"(密度 {recent_density:.2f} 条/轮)")
    lines.append(f"之前 {earlier_turns} 轮 (Turn 1~{window_start}): "
                 f"产出 {len(earlier_findings)} 条新发现 "
                 f"(密度 {earlier_density:.2f} 条/轮)")

    if decay_ratio == 0:
        lines.append(f"⚠ 你在最近 {window_size} 轮中没有产出任何新发现。")
    else:
        lines.append(f"近期产出密度降至早期的 {decay_ratio*100:.0f}%。")

    strategy = cognitive_state.current_strategy
    if strategy != "undecided":
        strategy_labels = {
            "deep_investigation": "深度追查",
            "breadth_scan": "广度扫描",
            "targeted_verification": "定向验证",
            "revision_mode": "修改模式",
            "synthesis": "综合收尾",
        }
        label = strategy_labels.get(strategy, strategy)
        lines.append(f"当前策略: {label}")

    lines.append("（这是客观产出数据。是否需要调整方向，由你判断。）")

    return lines


def check_stagnation(
    state: WorkspaceState,
    gate_config: Any,
    last_stagnation_signal_turn: int,
    current_tool: str,
) -> tuple[str | None, int]:
    """
    Phase 55: 停滞检测 — 主动呈现产出密度信号。

    Returns:
        (signal_text_or_none, updated_last_stagnation_signal_turn)
    """
    meta_tools = {"reflect_and_plan", "review_findings", "mark_complete", "done", "talk_to_user"}
    if current_tool in meta_tools:
        return None, last_stagnation_signal_turn

    s = state
    current_turn = s.loop_turns

    if current_turn < 6:
        return None, last_stagnation_signal_turn

    if current_turn - last_stagnation_signal_turn < 3:
        return None, last_stagnation_signal_turn

    idle_threshold = gate_config.idle_rounds

    recent_window = idle_threshold
    recent_history = s.tool_call_history[-recent_window:] if len(s.tool_call_history) >= recent_window else s.tool_call_history
    recent_tool_names = [t.get("name", "") for t in recent_history]

    if "update_findings" in recent_tool_names:
        return None, last_stagnation_signal_turn

    if len(s.findings) == 0 and current_turn < 8:
        return None, last_stagnation_signal_turn

    findings_with_turn = [f for f in s.findings if "recorded_at_turn" in f]
    if findings_with_turn:
        last_finding_turn = max(f["recorded_at_turn"] for f in findings_with_turn)
        turns_since_last = current_turn - last_finding_turn
        if turns_since_last < idle_threshold:
            return None, last_stagnation_signal_turn
    else:
        if len(s.findings) > 0 and current_turn < 10:
            return None, last_stagnation_signal_turn

    # 触发信号
    turns_without = current_turn - (max(f["recorded_at_turn"] for f in findings_with_turn) if findings_with_turn else 0)
    signal = (
        f"\n\n---\n"
        f"📉 产出观察: 最近 {turns_without} 轮未产出新发现。"
        f"当前共 {len(s.findings)} 条 findings，已读 {len(s.sections_read)} 个 sections。"
    )
    return signal, current_turn


def _detect_finding_overlaps(findings: list[dict]) -> list[str]:
    """检测 findings 之间的重叠（用于反思时警告）。"""
    overlaps = []
    for i in range(len(findings)):
        terms_i = extract_terms(findings[i].get("finding", ""), include_cjk=False, extended_stopwords=False)
        if len(terms_i) < 3:
            continue
        for j in range(i + 1, len(findings)):
            terms_j = extract_terms(findings[j].get("finding", ""), include_cjk=False, extended_stopwords=False)
            if len(terms_j) < 3:
                continue
            intersection = terms_i & terms_j
            overlap = len(intersection) / min(len(terms_i), len(terms_j))
            if overlap >= 0.65:
                overlaps.append(
                    f"发现 #{i+1} 和 #{j+1} 高度重叠 ({overlap:.0%}): "
                    f"#{i+1}='{findings[i]['finding'][:50]}...' "
                    f"#{j+1}='{findings[j]['finding'][:50]}...'"
                )
    return overlaps
