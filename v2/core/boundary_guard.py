"""
boundary_guard.py — 边界守护模块

从 harness.py 提取。包含所有用于约束 Agent 行为边界的检查函数。

设计原则 (COGNITIVE_ANCHOR §4.3 约束-而非-控制):
    - 不告诉 Agent "你该做什么"
    - 而是呈现事实，让 Agent 自主决策
    - 每类 nudge 最多触发一次（防死循环）

所有函数接收必要的 state / config 对象作为参数，
Harness 方法变为 thin wrapper 调用这些函数。
"""

from __future__ import annotations

from typing import Any

from core.state import WorkspaceState
from core.gate_config import CompletionGateConfig
from core.hypothesis import HypothesisModule
from core.finding_quality import FindingQualityGate


# ==============================================================
# Doom Loop Guard
# ==============================================================

def check_doom_loop(state: WorkspaceState) -> str | None:
    """边界守护：接近/超过 max turns 时的行为。

    策略：
    - max_turns + 2: 硬截断（给 Agent 额外 2 轮做完总结）

    返回 None 表示正常，返回 str 表示硬截断。
    """
    hard_limit = state.max_loop_turns + 2
    if state.loop_turns >= hard_limit:
        return f"已达到硬性上限 ({hard_limit} 轮)。强制结束。"
    return None


# ==============================================================
# Soft Turn Limit (Self-Eval)
# ==============================================================

def check_soft_turn_limit(
    state: WorkspaceState,
    gate_config: CompletionGateConfig,
    tool_call_history: list[dict],
    search_log: list[dict],
) -> str | None:
    """认知自评提问（Phase 28: Agent 自主终止判断）。

    设计原则 (COGNITIVE_ANCHOR §4.3 约束-而非-控制):
    - 不告诉 Agent "你该收尾了"（那是控制）
    - 而是问 Agent "你准备好了吗？"（那是约束+信任）

    触发时机（B4: 动态化，由 gate_config 决定）：
    - self_eval_first：首次自评
    - self_eval_second：再次自评
    - self_eval_final：最后警告
    """
    turns = state.loop_turns

    if turns == gate_config.self_eval_first:
        findings_count = len(state.findings)
        search_count = len(search_log)
        search_note = ""
        if search_count == 0 and findings_count > 0:
            search_note = (
                "另外注意：你尚未使用 search_literature 查过外部文献——"
                "你的判断完全基于论文自身的叙述，缺少外部校准。"
            )
        # Phase 46: 学科能力边界提示
        discipline_note = ""
        if not any(t.get("name") == "spawn_perspective" for t in tool_call_history):
            paper_text = " ".join(
                content[:300] for content in state.paper_sections.values() if content
            ).lower()
            disc_signals = {
                "统计/计量": ["propensity", "causal inference", "double machine learning", "semiparametric"],
                "ML/深度学习": ["transformer", "neural network", "deep learning", "attention mechanism"],
                "临床/流行病学": ["patient", "clinical trial", "randomized", "ehr", "electronic health"],
            }
            detected = [name for name, sigs in disc_signals.items() if sum(1 for s in sigs if s in paper_text) >= 2]
            if len(detected) >= 2:
                discipline_note = (
                    f"这篇论文跨越了 {len(detected)} 个学科（{', '.join(detected)}）。"
                    f"问自己：你对每个学科的方法论判断是否同样有信心？"
                    f"如果某个学科你只能做表面判断，可以用 spawn_perspective 请独立专家审视。"
                )
        return (
            f"[自评时刻] 你已完成 {turns} 轮思考，产出 {findings_count} 条发现。"
            f"问自己：我对这篇论文的核心方法论理解够了吗？我的主要假说验证完了吗？"
            f"{search_note}{discipline_note}"
            f"如果够了，用 mark_complete 结束；如果不够，说明你还需要验证什么，然后继续。"
        )
    elif turns == gate_config.self_eval_second:
        findings_count = len(state.findings)
        tokens = state.total_tokens
        return (
            f"[自评时刻] 已用 {turns} 轮，{findings_count} 条发现，~{tokens} tokens。"
            f"你是否还有 high-priority 的未验证假说？如果所有关键问题已有答案，"
            f"继续深入的边际价值可能在递减。做出你的判断。"
        )
    elif turns == gate_config.self_eval_final:
        return (
            f"[资源提示] 已用 {turns}/{state.max_loop_turns} 轮。"
            f"灾难保底上限为 {state.max_loop_turns} 轮。"
            f"请评估：你的核心发现是否已足够支撑一份有价值的审阅意见？"
        )
    return None


# ==============================================================
# Cognitive Output Monitor (Phase 17)
# ==============================================================

def check_cognitive_output(state: WorkspaceState) -> str | None:
    """
    Phase 17: 认知产出催促器 (Cognitive Output Prompter)

    检测 Agent 是否陷入"只读不记"的认知模式——连续多轮 read_section
    而不 update_findings。

    设计原则 (COGNITIVE_ANCHOR §4.3 约束-而非-控制):
    - 不控制 Agent 做什么——Agent 仍然可以选择继续读
    - 模拟人类专家的"笔记习惯"
    - 防止"延迟记录"导致压缩后失忆的认知退化模式

    Returns:
        str | None: 催促消息，或 None
    """
    s = state

    # 检查 findings 是否增长
    current_findings = len(s.findings)
    if current_findings > s.last_findings_count:
        s.consecutive_read_turns = 0
        s.last_findings_count = current_findings
        return None

    # findings 没增长，检查是否已经读了足够多的 sections
    if not s.sections_read:
        return None

    threshold_first = 3
    threshold_repeat = 2

    if s.consecutive_read_turns < threshold_first:
        return None

    # 已达到首次阈值
    turns_since_first = s.consecutive_read_turns - threshold_first

    if turns_since_first == 0 or (turns_since_first > 0 and turns_since_first % threshold_repeat == 0):
        sections_read_count = len(s.sections_read)

        if s.consecutive_read_turns == threshold_first:
            return (
                f"[认知提醒] 你已连续读了 {s.consecutive_read_turns} 轮 "
                f"({sections_read_count} 个 sections) 但尚未记录任何发现。"
                f"建议：边读边记——每读 2-3 个 section 就用 update_findings 记录初步印象，"
                f"哪怕是暂定的 'needs_verification' 状态。"
                f"这样即使后续 context 被压缩，你的关键观察也不会丢失。"
            )
        else:
            return (
                f"[认知警告] 你已连续 {s.consecutive_read_turns} 轮纯读取，"
                f"仍有 0 条新发现。当前已读 {sections_read_count} 个 sections，"
                f"早期内容正在被压缩——如果你现在才开始总结，可能已经丢失了重要细节。"
                f"请立即用 update_findings 记录你到目前为止的核心观察，"
                f"即使不完美也好过遗忘。"
            )

    return None


def track_cognitive_output(state: WorkspaceState, tool_name: str):
    """
    Phase 17: 追踪每轮的工具使用类型，用于判断"只读不记"模式。

    - 产出型工具 (update_findings, edit_section): 重置计数器
    - 读取型工具: 增加计数器（在轮次边界由 loop 调用 increment_read_turn）
    - 元认知型工具 (reflect_and_plan): 不计数
    """
    s = state

    output_tools = {"update_findings", "edit_section"}
    neutral_tools = {"reflect_and_plan", "talk_to_user", "done", "mark_complete", "spawn_perspective"}

    if tool_name in output_tools:
        s.consecutive_read_turns = 0
        s.last_findings_count = len(s.findings)
    elif tool_name not in neutral_tools:
        pass  # 读取型，累加在 loop 层轮次边界做


def increment_read_turn(state: WorkspaceState):
    """Phase 17: 由 loop 在一轮结束且该轮无产出时调用。"""
    state.consecutive_read_turns += 1


# ==============================================================
# Reflection Nudge (Phase 37/40/41)
# ==============================================================

def check_reflection_needed(
    state: WorkspaceState,
    reflection_log: list[dict],
    search_log: list[dict],
) -> str | None:
    """
    Phase 37+40+41: 反思催促器 (Reflection Nudge)

    条件 A (Phase 37): 已读 4+ 个 sections 且从未调用 reflect_and_plan
    条件 B (Phase 40): 有 needs_verification findings + 距上次反思已过 4+ 轮
    条件 C (Phase 41): 从未搜索 + 已有 2+ findings + 已过 8+ 轮

    Returns:
        str | None: 催促消息，或 None
    """
    s = state

    # === 条件 A: 首次反思催促 ===
    if not getattr(s, '_reflection_nudge_fired', False):
        if reflection_log:
            pass  # 跳过条件 A
        elif len(s.sections_read) >= 4:
            s._reflection_nudge_fired = True
            return (
                "[轻提醒] 你已经连续读了好几个 section 了。"
                "要不要暂停一下，用 reflect_and_plan 看看全局？"
                "——确认一下方向对不对、接下来该把精力放在哪里。"
                "（这只是提醒，如果你觉得当前方向很清晰，继续行动也完全可以。）"
            )

    # === 条件 B: 追查缺口催促 ===
    if not getattr(s, '_verification_nudge_fired', False):
        unverified = [f for f in s.findings if f.get("status") == "needs_verification"]
        if unverified:
            last_reflect_turn = 0
            if reflection_log:
                last_reflect_turn = reflection_log[-1].get("turn", 0)

            turns_since_reflect = s.loop_turns - last_reflect_turn

            if turns_since_reflect >= 4:
                s._verification_nudge_fired = True
                return (
                    f"[追查提醒] 你有 {len(unverified)} 条发现标记为 needs_verification，"
                    f"但距离你上次反思已经过了 {turns_since_reflect} 轮。"
                    f"要不要用 reflect_and_plan 看看——这些怀疑是否值得追查？"
                    f"（一个好审稿人的 report 里不会有'我怀疑但没验证'的条目。）"
                )

    # === 条件 C: 搜索缺失催促 ===
    if not getattr(s, '_search_nudge_fired', False):
        search_count = len(search_log)
        if search_count == 0 and len(s.findings) >= 2 and s.loop_turns >= 8:
            s._search_nudge_fired = True
            return (
                f"[外部校准提醒] 你已审阅了 {s.loop_turns} 轮、产出了 {len(s.findings)} 条发现，"
                f"但尚未使用 search_literature 查过任何外部文献。"
                f"要不要用 reflect_and_plan 暂停一下，看看哪些判断值得用外部文献校准？"
                f"（这只是提醒——如果你的判断完全基于论文内部证据且你有信心，继续也可以。）"
            )

    return None


# ==============================================================
# Token Budget Guard (Phase 16/45)
# ==============================================================

def check_token_budget(state: WorkspaceState, cost_warned: bool) -> tuple[str | None, bool]:
    """检查 context window 和累计成本。

    Phase 16: 阈值 80%（上下文腐烂）。
    Phase 45: 基于 last_prompt_tokens / context_window。

    Returns:
        (warning_message, updated_cost_warned)
    """
    context_ratio = state.last_prompt_tokens / state.context_window if state.context_window else 0
    if context_ratio > 0.8:
        return (
            f"当前 context 占用 {context_ratio:.0%}（{state.last_prompt_tokens}/{state.context_window} tokens）。"
            f"注意力可能开始涣散，请聚焦核心问题并尽快总结结论。"
        ), cost_warned

    if state.total_tokens > state.token_budget and not cost_warned:
        return (
            f"累计 token 消耗已超过预算（{state.total_tokens}/{state.token_budget}）。建议尽快完成当前任务。"
        ), True

    return None, cost_warned


# ==============================================================
# Completion Quality Gate
# ==============================================================

def check_completion_gate(
    state: WorkspaceState,
    gate_config: CompletionGateConfig,
    hypothesis_module: HypothesisModule | None,
    finding_quality_gate: FindingQualityGate,
    completion_nudges_fired: set[str],
) -> tuple[str | None, set[str]]:
    """
    Completion Quality Gate: 当 Agent 想结束时，检查是否有未收尾的工作。

    设计原则（C5 约束-而非-控制）：
    - 不设硬性数量门槛
    - 只提醒 Agent 存在的"未收尾"状态信号
    - 每类 nudge 最多触发一次
    - Agent 坚持退出时放行

    Returns:
        (nudge_message, updated_nudges_fired)
    """
    # --- 未验证高优发现 ---
    if "unverified" not in completion_nudges_fired:
        unverified_high = [
            f for f in state.findings
            if f.get("priority") == "high" and f.get("status") == "needs_verification"
        ]
        if unverified_high:
            completion_nudges_fired.add("unverified")
            items = "; ".join(f["finding"][:60] for f in unverified_high[:3])
            return (
                f"你还有 {len(unverified_high)} 条高优先级发现标记为 needs_verification: "
                f"{items}。\n"
                f"建议：用 read_section 或 search_literature 追查原文证据，"
                f"确认后再 update_findings(status='verified')。"
                f"如果你确认可以结束，再次调用 mark_complete 即可。"
            ), completion_nudges_fired

    # --- Phase 10: HD-WM 自动假说提醒 ---
    if "hdwm_active" not in completion_nudges_fired:
        if hypothesis_module is not None:
            active_hyps = [
                h for h in hypothesis_module.hypotheses
                if h.status.value == "active"
            ]
            if active_hyps:
                completion_nudges_fired.add("hdwm_active")
                hyp_names = "; ".join(h.statement[:50] for h in active_hyps[:3])
                return (
                    f"你还有 {len(active_hyps)} 个待验证判断尚未了结: "
                    f"{hyp_names}。\n"
                    f"建议：用 read_section 或 search_literature 追查相关段落，"
                    f"获取证据后再 update_findings(status='verified') 确认。"
                    f"如果你确认可以结束，再次调用 mark_complete 即可。"
                ), completion_nudges_fired

    # --- B4: 最少 findings 数检查 ---
    if "min_findings" not in completion_nudges_fired:
        min_f = gate_config.min_findings_for_exit
        if min_f > 0 and len(state.findings) < min_f:
            completion_nudges_fired.add("min_findings")
            return (
                f"你目前有 {len(state.findings)} 条发现，"
                f"而你之前判断此类论文通常至少应有 {min_f} 条发现。"
                f"这只是你自己设定的参考标准——如果你认为已经充分审阅，"
                f"再次调用 mark_complete 即可。"
            ), completion_nudges_fired

    # --- Q1: Finding 质量自检 ---
    if "quality_check" not in completion_nudges_fired:
        if state.findings:
            issues = finding_quality_gate.evaluate(state.findings)
            if issues:
                completion_nudges_fired.add("quality_check")
                return finding_quality_gate.format_nudge(issues), completion_nudges_fired

    return None, completion_nudges_fired
