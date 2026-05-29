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
# S2a: Role-Based Spawn Plan (不同审稿视角)
# ==============================================================
#
# 设计原则（约束-而非-控制）：
#   spawn 决策完全由 Agent 自己的 CognitiveHints 驱动。
#   boundary_guard 不做任何"论文内容分析"——那是 Agent 的工作。
#   本模块只负责：
#     1. 将 Agent 已有的认知判断（CognitiveHints）转化为 spawn 建议格式
#     2. 在 Agent 遗忘 spawn 时提醒它（不告诉它 spawn 什么）
#     3. 将 needs_verification 的 findings 转化为验证任务
#
# ==============================================================

# Auto-Spawn Scheduling Thresholds
SPAWN_PHASE1_PROGRESS_THRESHOLD = 0.15
SPAWN_PHASE2_PROGRESS_THRESHOLD = 0.45
SPAWN_PHASE2_MIN_UNVERIFIED = 2
SPAWN_MIN_PAPER_SECTIONS = 4
SPAWN_FALLBACK_PROGRESS_THRESHOLD = 0.3


def _build_role_based_spawn_plan(
    state: WorkspaceState,
) -> list[str]:
    """S2a-Phase1: 完全基于 Agent 的 CognitiveHints 生成 role-based 视角 spawn 建议。

    设计原则:
        Agent 在 initial_scan 阶段已经分析了论文类型、关键维度、典型弱点。
        这些判断存储在 CognitiveHints 中。本函数只是将这些判断转化为
        spawn_parallel_readers 可用的格式——不做任何额外的"论文内容分析"。

        如果 CognitiveHints 为空（Agent 还没生成），返回空列表。
        决策权始终在 Agent 手中。

    Returns:
        role-based spawn 建议列表（最多 8 条）
    """
    suggestions: list[str] = []
    seen_lenses: set[str] = set()

    hints = state.cognitive_hints

    # 如果 Agent 还没有生成 CognitiveHints，不做任何建议
    if hints is None or hints.is_empty():
        return []

    # 1. 从 focus_dimensions 生成审稿视角
    #    Agent 认为的关键关注维度 → 每个维度一个独立审稿人
    for dim in hints.focus_dimensions[:6]:
        if len(dim) < 10:
            continue
        # lens_key: 来源类型 + 前 40 字符（避免短 key 碰撞）
        lens_key = f"dim:{dim[:40]}".lower()
        if lens_key in seen_lenses:
            continue
        seen_lenses.add(lens_key)

        # lens_name 用于显示（取前 20 字符，清理特殊字符）
        lens_name = dim[:20].replace('"', '').replace("'", "").replace(" ", "_")
        dim_escaped = dim[:60].replace('"', "'")
        suggestions.append(
            f'lens="{lens_name}_reviewer", focus="full", '
            f'question="从「{dim_escaped}」的角度审视这篇论文：'
            f'(1) 这个维度上论文做得是否充分？'
            f'(2) 是否存在可疑之处或明显缺陷？'
            f'(3) 列出所有具体的证据位置。"'
        )

    # 2. 从 typical_weaknesses 生成弱点猎手视角
    #    Agent 判断的此类论文典型弱点 → 定向搜寻
    for weakness in hints.typical_weaknesses[:3]:
        if len(weakness) < 10:
            continue
        lens_key = f"weak:{weakness[:40]}".lower()
        if lens_key in seen_lenses:
            continue
        seen_lenses.add(lens_key)

        lens_name = weakness[:20].replace('"', '').replace("'", "").replace(" ", "_")
        weakness_escaped = weakness[:60].replace('"', "'")
        suggestions.append(
            f'lens="{lens_name}_hunter", focus="full", '
            f'question="这篇论文是否存在「{weakness_escaped}」的问题？'
            f'请搜寻所有相关证据，包括正文、附录、表格中的具体位置。"'
        )

    # 注意：verification_strategies 只在 Phase 2 使用，Phase 1 不重复消费
    # 这避免了 Agent 在 Phase 1 和 Phase 2 看到基于相同策略的重复建议

    return suggestions[:8]


# ==============================================================
# S2b: Content-Specific Spawn Plan (逐行验证)
# ==============================================================

def _build_verify_spawn_plan(
    state: WorkspaceState,
    tool_call_history: list[dict],
) -> list[str]:
    """S2b-Phase2: 基于已有 findings 中的 needs_verification 条目，
    生成 content-specific 的逐行验证 spawn 建议。

    Args:
        state: 当前工作区状态
        tool_call_history: 工具调用历史（用于检查已 spawn 的视角）

    Returns:
        content-specific 验证建议列表（最多 8 条）
    """
    suggestions: list[str] = []
    seen_keys: set[str] = set()

    # 从 findings 中找 needs_verification 的条目
    for finding in state.findings:
        if not isinstance(finding, dict):
            continue
        if finding.get("status") != "needs_verification":
            continue
        if finding.get("priority") == "low":
            continue

        finding_text = finding.get("finding", "")
        if len(finding_text) < 15:
            continue

        section = finding.get("section", "")
        section_focus = section.replace('"', "'") if section else "full"
        finding_escaped = finding_text[:80].replace('"', "'")

        # 用 section + finding 前缀做去重 key
        dedup_key = f"{section_focus}:{finding_text[:30]}".lower()
        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)

        suggestions.append(
            f'lens="verifier", focus="{section_focus}", '
            f'question="逐行验证: {finding_escaped}"'
        )

    # 从 CognitiveHints.verification_strategies 补充
    # （Phase 1 不再消费 verification_strategies，只在 Phase 2 使用）
    hints = state.cognitive_hints
    if hints and hints.verification_strategies:
        for strat in hints.verification_strategies[:3]:
            if len(strat) < 15:
                continue
            strat_escaped = strat[:60].replace('"', "'")
            dedup_key = f"strat:{strat[:40]}".lower()
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)
            suggestions.append(
                f'lens="strategy_verifier", focus="full", '
                f'question="{strat_escaped}"'
            )

    return suggestions[:8]


# ==============================================================
# Auto-Spawn Scheduling — 两阶段调度
# ==============================================================

def check_auto_spawn_needed(
    state: WorkspaceState,
    current_phase: str,
    tool_call_history: list[dict],
) -> str | None:
    """
    两阶段 spawn 调度 + 兜底提醒：

    阶段 1 (role-based, 进度 ≥15%):
        将 Agent 的 CognitiveHints 转化为 spawn 建议呈现给 Agent。
        如果 CognitiveHints 为空，标记为已触发（不重复尝试）。

    阶段 2 (content-specific, 进度 ≥45%):
        将 needs_verification 的 findings 转化为验证 spawn 建议。

    兜底 (进度 ≥30%, 从未 spawn):
        不告诉 Agent spawn 什么，只提醒它"你还没有 spawn 过"。
        决策权完全在 Agent 手中。

    设计原则:
    - 不做论文内容分析（那是 Agent 的工作）
    - 只做格式转换和时机提醒
    - 每阶段最多触发一次
    - Agent 可以选择忽略
    """
    if current_phase != "deep_review":
        return None

    if len(state.paper_sections) < SPAWN_MIN_PAPER_SECTIONS:
        return None

    progress_ratio = state.loop_turns / state.max_loop_turns if state.max_loop_turns else 0
    spawn_tools = {"spawn_perspective", "spawn_parallel_readers"}
    spawn_count = sum(1 for t in tool_call_history if t.get("name") in spawn_tools)

    # === 阶段 1: Role-Based Spawn ===
    if not state._role_spawn_nudge_fired and progress_ratio >= SPAWN_PHASE1_PROGRESS_THRESHOLD and spawn_count == 0:
        suggestions = _build_role_based_spawn_plan(state)
        # 无论 suggestions 是否为空，都标记为已触发（防止重复计算）
        state._role_spawn_nudge_fired = True

        if suggestions:
            suggestion_text = "\n  ".join(suggestions)
            return (
                f"[多视角 Spawn 建议] 你已进入 DEEP_REVIEW 初段"
                f"（{state.loop_turns}/{state.max_loop_turns} 轮），"
                f"是时候从不同审稿视角并行审视了。\n\n"
                f"核心逻辑：你一个人线性阅读时，无法同时从方法论/统计/假设/数据等多个"
                f"认知框架深入审视——多视角并行能发现你的盲点。\n\n"
                f"基于你之前的认知分析，建议用 spawn_parallel_readers 发起以下视角：\n"
                f"  {suggestion_text}\n\n"
                f"每个视角会独立从自己的专业角度审视全文，汇报可疑之处。"
                f"之后你可以对它们报告的嫌疑做逐行验证。\n"
                f"这些子视角各自只需 ~30s，总体投入很低，收益是覆盖你的认知盲区。"
            )
        # suggestions 为空（CognitiveHints 为空）→ 不返回消息，等 Fallback

    # === 阶段 2: Content-Specific Verify Spawn ===
    if not state._verify_spawn_nudge_fired and progress_ratio >= SPAWN_PHASE2_PROGRESS_THRESHOLD and spawn_count >= 1:
        unverified = [f for f in state.findings
                      if isinstance(f, dict)
                      and f.get("status") == "needs_verification"
                      and f.get("priority") in ("high", "medium")]
        if len(unverified) >= SPAWN_PHASE2_MIN_UNVERIFIED:
            suggestions = _build_verify_spawn_plan(state, tool_call_history)
            # 与 Phase 1 保持一致：无论 suggestions 是否为空都标记 fired
            state._verify_spawn_nudge_fired = True
            if suggestions:
                suggestion_text = "\n  ".join(suggestions)
                return (
                    f"[逐行验证 Spawn 建议] 你有 {len(unverified)} 条 needs_verification "
                    f"的 finding 尚未确认（{state.loop_turns}/{state.max_loop_turns} 轮）。\n\n"
                    f"这些嫌疑来自之前的审稿视角，需要精准逐行验证才能确认或排除。"
                    f"每个验证任务搜索空间被收窄到特定 section 的特定内容。\n\n"
                    f"建议用 spawn_parallel_readers 做定向验证：\n  {suggestion_text}\n\n"
                    f"每个验证任务只需 ~30s，能帮你把嫌疑快速确认或排除。"
                )

    # === 兜底: Fallback Spawn ===
    # 触发条件：Phase 1 已标记但没给出具体建议（CognitiveHints 为空），
    # 且 Agent 到了 30% 进度仍未 spawn。此时给一个通用提醒。
    # 设计原则：不硬编码任何具体视角，只提醒 Agent 考虑是否需要 spawn。
    if not state._fallback_spawn_nudge_fired and state._role_spawn_nudge_fired:
        if progress_ratio >= SPAWN_FALLBACK_PROGRESS_THRESHOLD and spawn_count == 0:
            state._fallback_spawn_nudge_fired = True
            unread_sections = [
                s for s in state.paper_sections
                if s not in state.sections_read
            ]
            unread_info = ""
            if unread_sections:
                unread_info = (
                    f"\n\n你还有 {len(unread_sections)} 个 section 尚未阅读: "
                    f"{', '.join(unread_sections[:5])}{'...' if len(unread_sections) > 5 else ''}。"
                )
            return (
                f"[Spawn 时机提示] 你已进入 DEEP_REVIEW 的中段"
                f"（{state.loop_turns}/{state.max_loop_turns} 轮），"
                f"但尚未使用 spawn_perspective/spawn_parallel_readers。\n\n"
                f"多视角并行审视能发现你线性阅读时的认知盲区。"
                f"请根据你对这篇论文的理解，决定是否需要从不同审稿视角并行审视。"
                f"{unread_info}\n\n"
                f"如果你认为当前的审阅已经足够全面，可以忽略此提示。"
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

    # --- Spawn Gate: 从未 spawn 过时提醒 ---
    if "spawn_gate" not in completion_nudges_fired:
        spawn_tools = {"spawn_perspective", "spawn_parallel_readers"}
        spawn_count = sum(1 for t in state.tool_call_history if t.get("name") in spawn_tools)
        if spawn_count == 0 and len(state.findings) >= 3 and len(state.paper_sections) >= 4:
            completion_nudges_fired.add("spawn_gate")
            unread_sections = [
                s for s in state.paper_sections
                if s not in state.sections_read
            ]
            unread_info = ""
            if unread_sections:
                unread_info = (
                    f"\n你还有 {len(unread_sections)} 个 section 尚未阅读: "
                    f"{', '.join(unread_sections[:5])}{'...' if len(unread_sections) > 5 else ''}。"
                )
            return (
                f"你尚未使用 spawn_parallel_readers 进行交叉审视。"
                f"多视角并行能发现单人线性阅读的认知盲区。{unread_info}\n"
                f"如果你确认当前审阅已经足够全面，再次调用 mark_complete 即可。"
            ), completion_nudges_fired

    # --- DEAI Unchecked: 有编辑但未做 de-AI 检查 ---
    if "deai_unchecked" not in completion_nudges_fired:
        if state.edits and state.deai_check_count == 0:
            completion_nudges_fired.add("deai_unchecked")
            edited_sections = list({e.get("section", "unknown") for e in state.edits})[:5]
            return (
                f"你对 {', '.join(edited_sections)} 做了编辑，"
                f"但尚未执行 detect_ai_signals 检查。"
                f"建议在结束前对已编辑的 section 做一次 de-AI 检查，"
                f"确保修改后的文本不含明显的 AI 痕迹。\n"
                f"如果你确认可以结束，再次调用 mark_complete 即可。"
            ), completion_nudges_fired

    return None, completion_nudges_fired
