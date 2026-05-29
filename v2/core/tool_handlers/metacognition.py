"""tool_handlers/metacognition.py — 元认知工具的执行逻辑。

提取自 Harness._tool_generate_cognitive_hints, _tool_reflect_and_plan, _check_stagnation。
注意: reflect_and_plan 和 check_stagnation 已经在 core/tool_reflect.py 中实现，
本模块只是提供与 Harness 解耦的 thin wrapper 函数。

V4 B2: 集成 TemplateRegistry 模板匹配，在 Agent 生成 CognitiveHints 前尝试
用匹配到的模板 seed 初始值（Agent 的输入优先级高于模板 seed）。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from core.gate_config import compute_gate_config
from core.tool_reflect import (
    reflect_and_plan as _tr_reflect_and_plan,
    check_stagnation as _tr_check_stagnation,
)

logger = logging.getLogger(__name__)


# ============================================================
# tool_generate_cognitive_hints
# ============================================================

def tool_generate_cognitive_hints(
    args: dict,
    state: Any,
    memory: Any,
    gate_config_holder: Any,
    template_registry: Optional[Any] = None,
) -> str:
    """
    S1: Agent 自主生成审稿认知提示。

    V4 B2 增强: 如果提供了 template_registry，在 Agent 生成 hints 之前
    尝试通过论文摘要/结构进行模板匹配。匹配到的模板 seed_hints 作为默认值，
    Agent 的显式输入具有更高优先级（覆盖模板 seed）。

    Args:
        args: 工具参数
        state: WorkspaceState
        memory: MemoryStore
        gate_config_holder: 一个可变对象（如 list 或 dict），用于传回新的 gate_config
        template_registry: V4 TemplateRegistry 实例（可选）

    Returns:
        给 Agent 的反馈
    """
    from core.godel_config import GODEL_SKILL_LOADING_ENABLED

    # V4 B2: 模板匹配 → seed hints + recommended_skills
    template_seed = None
    matched_template_name = None
    recommended_skills: list[str] = []
    if (
        GODEL_SKILL_LOADING_ENABLED
        and template_registry is not None
    ):
        template_seed, matched_template_name, recommended_skills = (
            _try_match_template(state, template_registry)
        )

    # 如果有模板 seed，将其作为 fallback 注入 args
    if template_seed is not None:
        args = _merge_template_seed_into_args(args, template_seed)

    # V4 C2: 将模板推荐的 skills 存入 state，供 Assembler domain_skills 优先加载
    # 无论匹配成功与否都更新（匹配失败时清空旧值，防止跨论文残留）
    state.recommended_skills = recommended_skills

    from core.paper_type_hints import handle_generate_cognitive_hints
    response, hints = handle_generate_cognitive_hints(args)
    if not hints.is_empty():
        state.cognitive_hints = hints
        # B4: Agent 生成 hints 后，更新 Completion Gate 配置
        new_config = compute_gate_config(
            cognitive_hints=hints,
            memory_store=memory,
            paper_type=hints.paper_type_description,
        )
        gate_config_holder[0] = new_config

    # V4 B2: 在响应中附加模板匹配信息
    if matched_template_name and not hints.is_empty():
        response += (
            f"\n📋 [模板辅助] 检测到匹配模板: {matched_template_name}。"
            "已用模板 seed 补充未填字段，你可以随时修正。"
        )

    return response


def _try_match_template(
    state: Any, template_registry: Any
) -> tuple[Optional[dict], Optional[str], list[str]]:
    """尝试从论文内容匹配模板。

    构建匹配文本 = 摘要 + section 名列表 + 已有 cognitive_hints 描述。

    Returns:
        (seed_hints_dict, template_name, recommended_skills) 或 (None, None, [])
    """
    # 构建匹配文本
    text_parts: list[str] = []

    # 论文摘要
    if state.paper_sections:
        abstract = state.paper_sections.get("abstract", "")
        if abstract:
            text_parts.append(abstract[:2000])
        # section 名列表作为结构信号
        section_names = [k for k in state.paper_sections if k != "full"]
        text_parts.append(" ".join(section_names))

    # 已有的 cognitive_hints 描述
    hints = getattr(state, "cognitive_hints", None)
    if hints and hints.paper_type_description:
        text_parts.append(hints.paper_type_description)

    match_text = " ".join(text_parts)
    if not match_text.strip():
        return None, None, []

    # 执行匹配
    matched = template_registry.match(match_text)
    if matched is None:
        return None, None, []

    logger.info(
        "[Metacognition/B2] Template matched: '%s' (%s). recommended_skills=%s",
        matched.id,
        matched.name,
        matched.recommended_skills,
    )
    return matched.seed_hints, matched.name, matched.recommended_skills


def _merge_template_seed_into_args(args: dict, seed: dict) -> dict:
    """将模板 seed_hints 合并到 args 中（Agent 输入优先）。

    规则: Agent 提供的字段保持不变，只补充 Agent 未填的字段。
    这确保 Agent 具有最高 override 优先级。

    Args:
        args: Agent 原始工具参数
        seed: 模板的 seed_hints dict

    Returns:
        合并后的新 args dict（不修改原 args）
    """
    merged = dict(args)  # shallow copy

    # paper_type_description: Agent 未填时使用模板 seed
    if not merged.get("paper_type_description", "").strip():
        if seed.get("paper_type_description"):
            merged["paper_type_description"] = seed["paper_type_description"]

    # focus_dimensions: Agent 未填时使用模板 seed
    if not merged.get("focus_dimensions"):
        if seed.get("focus_dimensions"):
            merged["focus_dimensions"] = seed["focus_dimensions"]

    # typical_weaknesses: Agent 未填时使用模板 seed
    if not merged.get("typical_weaknesses"):
        if seed.get("typical_weaknesses"):
            merged["typical_weaknesses"] = seed["typical_weaknesses"]

    # verification_strategies: Agent 未填时使用模板 seed
    if not merged.get("verification_strategies"):
        if seed.get("verification_strategies"):
            merged["verification_strategies"] = seed["verification_strategies"]

    return merged


# ============================================================
# tool_reflect_and_plan
# ============================================================

def tool_reflect_and_plan(args: dict, state: Any, cognitive_state: Any,
                          strategy_transitions: list, last_strategy: str,
                          search_log: list, gate_config: Any,
                          reflection_log: list) -> tuple[str, str]:
    """
    元认知工具：Agent 主动触发反思。委托 tool_reflect 模块。

    Returns:
        (result_text, new_strategy)
    """
    result, new_strategy = _tr_reflect_and_plan(
        state=state,
        cognitive_state=cognitive_state,
        strategy_transitions=strategy_transitions,
        last_strategy=last_strategy,
        search_log=search_log,
        gate_config=gate_config,
        args=args,
    )

    # 记录反思事件
    reflection_log.append({
        "turn": state.loop_turns,
        "trigger": args.get("trigger", "自主反思"),
        "findings_count": len(state.findings),
        "current_thinking": args.get("current_thinking", "")[:100],
        "cognitive_strategy": cognitive_state.current_strategy,
    })

    return result, new_strategy


# ============================================================
# check_stagnation
# ============================================================

def check_stagnation(state: Any, gate_config: Any,
                     last_stagnation_signal_turn: int,
                     current_tool: str) -> tuple[str | None, int]:
    """
    Phase 55: 停滞检测。委托 tool_reflect 模块。

    Returns:
        (signal_or_none, updated_last_signal_turn)
    """
    signal, new_turn = _tr_check_stagnation(
        state=state,
        gate_config=gate_config,
        last_stagnation_signal_turn=last_stagnation_signal_turn,
        current_tool=current_tool,
    )
    return signal, new_turn
