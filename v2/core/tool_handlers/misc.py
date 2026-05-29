"""tool_handlers/misc.py -- 杂项工具的执行逻辑。

提取自 Harness._tool_talk_to_user, _tool_spawn_perspective,
_tool_detect_ai_signals, _tool_verify_citations, _tool_recall_context,
_tool_request_phase_transition, _tool_done。
"""
from __future__ import annotations

import json
from typing import Any

from core.phases import Phase
from core.cognition_graph import build_cognition_graph


# ============================================================
# tool_talk_to_user
# ============================================================

def tool_talk_to_user(args: dict) -> str:
    """向用户发送消息。"""
    message = args.get("message", "")
    expects_reply = args.get("expects_reply", False)
    return f"__TALK__|{json.dumps({'message': message, 'expects_reply': expects_reply}, ensure_ascii=False)}"


# ============================================================
# tool_spawn_perspective
# ============================================================

def tool_spawn_perspective(args: dict) -> str:
    """发起子视角审视。返回 __SPAWN__ 信号给 loop 层驱动子循环。"""
    lens = args.get("lens", "")
    focus = args.get("focus", "")
    question = args.get("question", "")

    if not lens or not question:
        return "spawn_perspective 需要 lens 和 question 参数。"

    spawn_payload = json.dumps({
        "lens": lens,
        "focus": focus,
        "question": question,
    }, ensure_ascii=False)
    return f"__SPAWN__|{spawn_payload}"


# ============================================================
# tool_spawn_parallel_readers (C4: 认知分裂)
# ============================================================

_MAX_PARALLEL_READERS = 8  # 硬约束：最多并行 8 个子视角（召回率 > 成本效率）


def tool_spawn_parallel_readers(args: dict) -> str:
    """
    C4: 并行深读分裂。

    当 Agent 判断"这篇论文有多个独立维度需要深入审视，串行会信息损耗"时，
    一次发起多个并行子视角。每个子视角独立运行，完成后结果统一合并回主 Agent。

    与 spawn_perspective 的区别：
    - spawn_perspective: 一次一个，串行
    - spawn_parallel_readers: 一次多个，asyncio.gather 并行

    注意：子视角不设独立的 token budget 限制。子视角的终止由
    max_loop_turns 硬约束保证（12 轮），子消耗事后回流父级用于
    后续预算决策。_run_parallel_perspectives 入口有轻量级 guard，
    父级剩余预算过低时会直接跳过并行分裂。
    """
    readers = args.get("readers", [])

    if not readers:
        return "spawn_parallel_readers 需要至少一个 reader 目标。"

    if not isinstance(readers, list):
        return "readers 必须是一个数组。"

    # 硬约束：最多 N 个
    if len(readers) > _MAX_PARALLEL_READERS:
        return (
            f"最多并行 {_MAX_PARALLEL_READERS} 个子视角，你提交了 {len(readers)} 个。"
            f"请减少到 {_MAX_PARALLEL_READERS} 个以内，优先保留最不确定的维度。"
        )

    # 校验每个 reader 的参数完整性
    validated_readers = []
    for i, r in enumerate(readers):
        if not isinstance(r, dict):
            return f"readers[{i}] 不是有效对象。"
        lens = r.get("lens", "").strip()
        focus = r.get("focus", "").strip()
        question = r.get("question", "").strip()
        if not lens or not question:
            return f"readers[{i}] 缺少 lens 或 question 参数。"
        validated_readers.append({
            "lens": lens,
            "focus": focus or "full",
            "question": question,
        })

    spawn_payload = json.dumps({
        "readers": validated_readers,
    }, ensure_ascii=False)

    return f"__PARALLEL_SPAWN__|{spawn_payload}"


# ============================================================
# tool_detect_ai_signals (DEAI-1)
# ============================================================

_MAX_DEAI_CHECKS = 3


def tool_detect_ai_signals(args: dict, state: Any) -> str:
    """DEAI-1: AI 信号检测 + 迭代追踪 + 可操作定位。"""
    text = args.get("text", "")
    section = args.get("section", "")

    # 支持两种调用方式
    if not text and section:
        key = _resolve_section_key_simple(section, state.paper_sections)
        if key and key in state.paper_sections:
            text = state.paper_sections[key]
        else:
            return f"错误: section '{section}' 未找到。可用 sections: {list(state.paper_sections.keys())[:10]}"
    elif not text and not section:
        edited_sections = list({e["section"] for e in state.edits if "section" in e})
        if not edited_sections:
            return "错误: 'text' 和 'section' 均为空，且无编辑记录。请指定要检测的文本或 section。"
        text = "\n\n".join(
            state.paper_sections[s]
            for s in edited_sections
            if s in state.paper_sections
        )
        section = f"[已编辑 sections: {', '.join(edited_sections[:5])}]"

    from core.deai_detector import detect_ai_signals
    result = detect_ai_signals(text)

    # 迭代追踪
    state.deai_check_count += 1
    state.deai_last_result = {
        "verdict": result.verdict,
        "signal_count": result.signal_count,
        "critical_count": result.critical_count,
        "major_count": result.major_count,
        "overall_score": result.overall_score,
        "check_round": state.deai_check_count,
    }

    # 构建增强输出
    output_lines = [result.summary()]

    if result.signals:
        output_lines.append("")
        output_lines.append("--- 可操作修改建议 ---")
        for i, sig in enumerate(result.signals[:6], 1):
            output_lines.append(f"  [{i}] {sig.signal_type} ({sig.tier}, conf={sig.confidence:.2f})")
            if sig.evidence:
                output_lines.append(f"      定位: \"{sig.evidence[:100]}\"")
            output_lines.append(f"      建议: {sig.fix_suggestion}")
        if len(result.signals) > 6:
            output_lines.append(f"  ... 还有 {len(result.signals) - 6} 个信号")

    # 迭代进度
    output_lines.append("")
    output_lines.append(f"--- de-AI 迭代进度: 第 {state.deai_check_count} 轮 ---")
    if result.verdict == "PASS":
        output_lines.append("文本已通过 AI 信号检测，无需进一步修改。")
    elif state.deai_check_count >= _MAX_DEAI_CHECKS:
        output_lines.append(
            f"已达到 de-AI 检查最大轮次（{_MAX_DEAI_CHECKS}）。"
            f"当前结果: {result.verdict} (score={result.overall_score:.3f})。"
            f"剩余的 AI 信号可标记为可接受或交由用户处理。"
        )
    else:
        remaining = _MAX_DEAI_CHECKS - state.deai_check_count
        output_lines.append(
            f"结果: {result.verdict}。建议针对上述 critical/major 信号使用 reword_sentence 修改后重新检测。"
            f"（剩余 {remaining} 轮）"
        )

    return "\n".join(output_lines)


# ============================================================
# tool_verify_citations
# ============================================================

def tool_verify_citations(args: dict) -> str:
    """Phase 22: 验证参考文献完整性和引用一致性。"""
    bib_content = args.get("bib_content", "")
    tex_content = args.get("tex_content", "")
    project_dir = args.get("project_dir", "")
    check_orphaned = args.get("check_orphaned", True)

    if not bib_content and not project_dir:
        return "错误: 请传入 bib_content（.bib 文件内容）或 project_dir（项目目录路径）。"

    from core.bib_verify import verify_citations
    result = verify_citations(
        bib_content=bib_content or None,
        tex_content=tex_content or None,
        project_dir=project_dir or None,
        check_orphaned=check_orphaned,
    )
    return result.summary()


# ============================================================
# tool_recall_context
# ============================================================

def tool_recall_context(args: dict, offload_store: Any) -> str:
    """Phase 32: 从 offload store 回查之前卸载的完整内容。"""
    ref_id = args.get("ref_id", "")
    key = args.get("key", "")

    if ref_id:
        content = offload_store.recall(ref_id)
        if content:
            return f"[回查 {ref_id}] 完整内容 ({len(content)} chars):\n\n{content}"
        return f"[回查失败] 找不到 ref_id='{ref_id}'。请检查可用的 ref_id 列表。"
    elif key:
        content = offload_store.recall_by_key(key)
        if content:
            return f"[回查 '{key}'] 完整内容 ({len(content)} chars):\n\n{content}"
        return f"[回查失败] 找不到 key='{key}' 的卸载内容。"
    else:
        return "错误: 请传入 ref_id (如 'ref_003') 或 key (如 section 名)。"


# ============================================================
# tool_request_phase_transition
# ============================================================

def tool_request_phase_transition(args: dict, state: Any, phase_fsm: Any, assembler: Any) -> str:
    """Agent 请求阶段转换。"""
    target_name = args.get("target_phase", "").strip().lower()
    reason = args.get("reason", "")

    try:
        target = Phase(target_name)
    except ValueError:
        valid = [p.value for p in Phase]
        return (
            f"无效的目标阶段: '{target_name}'。"
            f"有效选项: {', '.join(valid)}"
        )

    sections_read = len(state.sections_read)
    verified_findings = sum(
        1 for f in state.findings
        if f.get("status") == "verified"
    )

    result = phase_fsm.request_transition(
        target=target,
        sections_read=sections_read,
        verified_findings=verified_findings,
    )

    if result.allowed:
        assembler.registry.invalidate_phase_cache()
        # 如果 reason 包含 nudge（⚠️），转换仍然执行但把建议传达给 Agent
        nudge_prefix = ""
        if "⚠️" in result.reason:
            nudge_prefix = f"[注意] {result.reason}\n\n"

        # S1b: 转入 DEEP_REVIEW 时，如果 cognitive_hints 为空/seed质量不足，
        # 催促 Agent 先生成审稿策略。不阻断转换，但显式提醒。
        cognitive_hints = getattr(state, "cognitive_hints", None)
        if target == Phase.DEEP_REVIEW and (
            cognitive_hints is None or cognitive_hints.is_empty()
        ):
            nudge_prefix += (
                "[建议] 你尚未生成审稿认知策略。在深度审阅前，"
                "建议先调用 generate_cognitive_hints 工具——基于你对论文的初步理解，"
                "制定针对性的审查维度和验证策略。这能帮助你更系统地发现问题。\n\n"
            )

        return (
            f"{nudge_prefix}"
            f"阶段转换成功: {result.from_phase.value} -> {result.to_phase.value}。"
        )
    else:
        # 仅在幂等保护时触发（already in target phase）
        return (
            f"阶段转换被拒绝: 无法从 {result.from_phase.value} "
            f"转到 {result.to_phase.value}。原因: {result.reason}"
        )


# ============================================================
# tool_done
# ============================================================

def tool_done(args: dict, state: Any, checker: Any, hypothesis_module: Any,
              check_completion_gate_fn) -> str:
    """Agent 声明任务完成。"""
    summary = args.get("summary", "")

    # Phase 50: Pre-Completion Check
    abstract = state.paper_sections.get("abstract", "")
    if not abstract:
        for key in state.paper_sections:
            if "abstract" in key.lower():
                abstract = state.paper_sections[key]
                break
    checker_nudge = checker.check_pre_completion(
        abstract=abstract,
        findings=state.findings,
    )
    if checker_nudge:
        return f"__NUDGE__|[Checker 校验] {checker_nudge}"

    # Completion quality gate
    gate_result = check_completion_gate_fn()
    if gate_result:
        return f"__NUDGE__|{gate_result}"

    # K1: 构建审稿认知图谱
    state.cognition_graph = build_cognition_graph(
        state=state,
        hypothesis_module=hypothesis_module,
        cognitive_hints=state.cognitive_hints,
    )

    return f"__DONE__|{summary}"


# ============================================================
# tool_switch_persona
# ============================================================

VALID_PERSONAS = {"scholar", "writer", "code_reviewer"}
MAX_SWITCHES = 5


def tool_switch_persona(args: dict, state: Any) -> str:
    """
    Agent 主动切换认知人格。

    C2 设计: nudge not block — 超过切换上限时发 nudge，不阻止切换。
    """
    target_persona = args.get("target_persona", "").lower().strip()
    reason = args.get("reason", "")

    if not target_persona:
        return "请指定目标人格 (scholar / writer / code_reviewer)。"

    if target_persona not in VALID_PERSONAS:
        valid = ", ".join(sorted(VALID_PERSONAS))
        return f"无效的人格: '{target_persona}'。有效选项: {valid}"

    # 获取当前 persona（如果有的话）
    current_persona = getattr(state, "current_persona", None)
    if current_persona == target_persona:
        return f"你已经是 {target_persona} 了，无需切换。"

    # 追踪切换次数
    switch_count = getattr(state, "persona_switch_count", 0)
    switch_count += 1
    state.persona_switch_count = switch_count

    # Nudge: 切换次数过多时警告（但不阻止）
    nudge_text = ""
    if switch_count > MAX_SWITCHES:
        nudge_text = (
            f"⚠️ 你已经切换了 {switch_count} 次人格。频繁切换可能表明任务边界不清晰。"
            f"建议先完成当前视角的工作再切换。"
        )

    # Nudge: 切到 writer 但没有 findings 时
    if target_persona == "writer" and not state.findings:
        nudge_text += (
            "\n⚠️ 当前没有任何 findings。通常先完成审阅产出 findings，"
            "再切换到 writer 进行修改会更高效。"
        )

    payload = json.dumps({
        "target_persona": target_persona,
        "reason": reason,
        "nudge": nudge_text,
    }, ensure_ascii=False)

    return f"__SWITCH__|{payload}"


# ============================================================
# tool_switch_model
# ============================================================

def tool_switch_model(args: dict, state: Any) -> str:
    """
    Agent 请求切换 LLM 模型。

    返回 __MODEL__|{json} 信号，由 cognitive_loop 捕获并分发给
    _handle_model_signal 处理实际切换逻辑。

    设计: 与 switch_persona 模式一致 —— handler 只负责验证参数格式
    并生成信号字符串，实际切换逻辑在 loop.py 中完成。
    """
    target = args.get("target_model", "").strip()
    reason = args.get("reason", "")

    if not target:
        return "请指定目标模型 ID（可通过 system prompt 中的模型列表查看可用模型）。"

    payload = json.dumps({
        "target": target,
        "reason": reason,
    }, ensure_ascii=False)

    return f"__MODEL__|{payload}"


# ============================================================
# 辅助
# ============================================================

def _resolve_section_key_simple(section: str, paper_sections: dict) -> str | None:
    """简易 section key 解析（与 editing.py 的 resolve_section_key 逻辑一致）。"""
    for key in paper_sections:
        if section.lower() in key.lower() or key.lower() in section.lower():
            return key
    return None
