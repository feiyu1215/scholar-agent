"""
core/loop.py — 认知循环: Agent 的思考引擎

设计原则 (来自 COGNITIVE_ANCHOR §5.1):
    Loop 本身不控制 Agent 做什么。它只做：
    1. 把当前 context 给 LLM
    2. LLM 产出 text + tool_calls
    3. 如果有 tool_calls → 交给 Harness 执行 → 结果注入 → 回到 2
    4. 如果 LLM 决定 done 或没有 tool_calls → 结束本轮

    多轮对话支持:
    - messages 列表在多轮对话间持续累积
    - 用户每次发新消息，append 到 messages 里，然后重新进入 loop
    - 不重建 client，不清空 context（Agent 记得之前聊了什么）

    信号协议:
    - "__DONE__|summary" → Agent 认为当前任务完成
    - "__TALK__|json" → Agent 想和用户说话（loop yield 回上层）
    - "__NUDGE__|reason" → Harness 的 quality gate 拦截了 done，给 Agent 一个提示
    - "__SPAWN__|json" → Agent 请求视角分裂（loop 驱动子循环）
    - "__SWITCH__|json" → Agent 请求切换认知人格（loop 更新 persona + system prompt）
"""

from __future__ import annotations

import json
import sys
from typing import Any, AsyncGenerator

from llm.client import LLMClient
from core.harness import Harness
from core.godel_config import GODEL_STREAMING_ENABLED
from core.identity import (
    SUB_PERSPECTIVE_TOOLS,
    build_sub_perspective_prompt,
)
from core.stream_events import StreamEvent, OnStreamCallback


# ============================================================
# Loop Result Types
# ============================================================

class LoopResult:
    """认知循环的一轮结果。"""
    pass


class LoopDone(LoopResult):
    """Agent 宣布完成。"""
    def __init__(self, summary: str, content: str = ""):
        self.summary = summary
        self.content = content  # 最终轮次的文本输出


class LoopTalk(LoopResult):
    """Agent 想和用户交流（暂停循环等用户回复）。"""
    def __init__(self, message: str, expects_reply: bool = False, content: str = ""):
        self.message = message
        self.expects_reply = expects_reply
        self.content = content


class LoopDoomStop(LoopResult):
    """Harness 强制停止（doom loop 或 token budget）。"""
    def __init__(self, reason: str, content: str = ""):
        self.reason = reason
        self.content = content


# ============================================================
# MCL Helper
# ============================================================

def _count_stagnant_turns(state: Any) -> int:
    """
    计算从最后一次 update_findings 到现在的轮数。
    用于判断审稿人是否"卡住"（连续执行非产出性工具）。
    """
    history = state.tool_call_history or []
    if not history:
        return state.loop_turns
    # 从尾部向前找最后一次 update_findings
    for i in range(len(history) - 1, -1, -1):
        tool_name = history[i].get("name") or history[i].get("tool", "")
        if tool_name == "update_findings":
            return len(history) - 1 - i
    # 从未 update_findings
    return len(history)


# ============================================================
# Cognitive Loop
# ============================================================

async def cognitive_loop(
    messages: list[dict],
    harness: Harness,
    tools: list[dict],
    client: LLMClient,
    verbose: bool = True,
    on_stream: OnStreamCallback = None,
    session_model_mgr=None,  # Optional[SessionModelManager] — None 时跳过所有多模型逻辑
) -> LoopResult:
    """
    执行一轮认知循环（从用户消息到 Agent 完成或暂停）。

    Args:
        messages: 完整的对话 messages 列表（会被 mutate）
        harness: 状态守护层
        tools: Agent 可用的工具定义（全量列表，会根据 Phase 动态过滤）
        client: LLM 客户端
        verbose: 是否打印过程信息
        on_stream: 可选的流式事件回调。传入时启用 streaming 模式，
                   在 LLM 产出文本时实时推送 StreamEvent。不传时行为不变。

    Returns:
        LoopResult: Done / Talk / DoomStop
    """

    accumulated_content = ""
    # 用于追踪 done 被 nudge 拦截的次数（防止无限循环）
    nudge_count = 0
    # P3 #19: cooldown — 上一次 nudge 发生的 turn（防止连续 nudge 干扰）
    last_nudge_turn = -10  # 初始值足够小，首次 nudge 不受限
    # B3: max_nudges 从 AdaptiveConfig 动态获取（默认 2）
    adaptive = getattr(harness, 'adaptive_config', None)

    # V5: 流式模式判定（循环外计算一次，值在整个循环生命周期内不变）
    _use_streaming = on_stream is not None and GODEL_STREAMING_ENABLED

    while True:
        # ---- 边界检查 ----
        doom = harness.check_doom_loop()
        if doom:
            if verbose:
                print(f"\n[Harness] {doom}", file=sys.stderr)
            if _use_streaming:
                on_stream(StreamEvent(type="done", text=doom, turn=harness.state.loop_turns))
            return LoopDoomStop(reason=doom, content=accumulated_content)

        # ---- Token Budget 硬截断 ----
        if harness.is_budget_exceeded():
            reason = f"Token budget 已耗尽（{harness.state.total_tokens:,}/{harness.budget_policy.token_limit:,}）"
            if verbose:
                print(f"\n[Budget] {reason}", file=sys.stderr)
            if _use_streaming:
                on_stream(StreamEvent(type="done", text=reason, turn=harness.state.loop_turns))
            return LoopDoomStop(reason=reason, content=accumulated_content)

        # V3 Phase 0.5: 信号调度（SignalDispatcher 替代 stacked checks）
        from core.godel_config import GODEL_SIGNAL_DISPATCHER_ENABLED
        if GODEL_SIGNAL_DISPATCHER_ENABLED and hasattr(harness, 'signal_dispatcher'):
            from core.signal_dispatcher import HarnessSignal
            dispatcher = harness.signal_dispatcher

            soft_turn_warning = harness.check_soft_turn_limit()
            if soft_turn_warning:
                dispatcher.submit(HarnessSignal(
                    source="turn", priority=1,
                    message=f"[Harness 提醒] {soft_turn_warning}",
                ))
            # budget_warning 已由硬截断机制处理，不再注入 soft signal
            cognitive_nudge = harness.check_cognitive_output()
            if cognitive_nudge:
                dispatcher.submit(HarnessSignal(
                    source="cognitive", priority=2,
                    message=cognitive_nudge,
                    suppress_if=["budget", "turn"],  # H4 fix: budget/turn 警告优先时抑制 cognitive nudge
                ))
            reflection_nudge = harness.check_reflection_needed()
            if reflection_nudge:
                dispatcher.submit(HarnessSignal(
                    source="reflection", priority=3,
                    message=reflection_nudge,
                    suppress_if=["cognitive"],
                ))
            # S4: Auto-spawn scheduling
            spawn_nudge = harness.check_auto_spawn_needed()
            if spawn_nudge:
                dispatcher.submit(HarnessSignal(
                    source="spawn", priority=2,
                    message=spawn_nudge,
                    suppress_if=["budget"],
                ))

            selected_signals = dispatcher.dispatch(harness.state.loop_turns)
            for sig_msg in selected_signals:
                if verbose:
                    print(f"  [Signal] {sig_msg[:80]}", file=sys.stderr)
                messages.append({"role": "system", "content": sig_msg})
        else:
            # Fallback: V2 stacked checks 行为（GODEL_SIGNAL_DISPATCHER_ENABLED=0）
            soft_turn_warning = harness.check_soft_turn_limit()
            if soft_turn_warning:
                if verbose:
                    print(f"  [Harness 提醒] {soft_turn_warning}", file=sys.stderr)
                messages.append({"role": "system", "content": f"[Harness 提醒] {soft_turn_warning}"})

            # budget_warning 已由硬截断机制处理，不再注入 system message

            cognitive_nudge = harness.check_cognitive_output()
            if cognitive_nudge:
                if verbose:
                    print(f"  [认知催促] {cognitive_nudge[:100]}", file=sys.stderr)
                messages.append({"role": "system", "content": cognitive_nudge})

            reflection_nudge = harness.check_reflection_needed()
            if reflection_nudge:
                if verbose:
                    print(f"  [反思催促] {reflection_nudge[:60]}", file=sys.stderr)
                messages.append({"role": "system", "content": reflection_nudge})

            # S4: Auto-spawn scheduling
            spawn_nudge = harness.check_auto_spawn_needed()
            if spawn_nudge:
                if verbose:
                    print(f"  [Spawn催促] {spawn_nudge[:80]}", file=sys.stderr)
                messages.append({"role": "system", "content": spawn_nudge})

        # MCL Stagnation Check: 检测连续无新 finding 的轮数
        # 当审稿人"转圈"时，MCL 给出具体建议帮助突破
        if harness.mcl is not None and harness.state.loop_turns >= 3:
            # 计算连续无新 finding 的轮次（从尾部向前计，看最后一个 update_findings 之后的轮数）
            _stagnant_turns = _count_stagnant_turns(harness.state)
            if _stagnant_turns >= 3:
                stagnation_result = await harness.mcl.check_stagnation(harness.state, _stagnant_turns)
                if stagnation_result and stagnation_result.should_block:
                    stag_feedback = harness.mcl.format_stagnation_feedback(stagnation_result)
                    if verbose:
                        print(f"  [MCL Stagnation] {stag_feedback[:100]}", file=sys.stderr)
                    messages.append({"role": "system", "content": f"[元认知反馈] {stag_feedback}"})

        # ---- LLM 思考 ----
        harness.state.loop_turns += 1

        # B3: AdaptiveConfig tick — 根据 state 信号调整参数
        if adaptive is not None:
            # 通知当前 phase
            if hasattr(harness, 'phase_fsm'):
                adaptive.set_phase(harness.phase_fsm.phase_name)
            adaptive.tick(harness.state)

        # Phase 4+: 自动 Phase Transition
        # 设计原则: Agent 的 identity 说"不存在阶段"——它专注审稿内容，
        # FSM 转换由 Harness 自动完成（Agent 不需要知道/主动请求）。
        # 这确保 two-phase spawn、工具可见性扩展等下游机制正确触发。
        if hasattr(harness, 'phase_fsm'):
            _auto_transition_result = _try_auto_phase_transition(harness, verbose)

        # Phase 5: HD-WM tick — 更新饱和检测计数器
        if harness.hypothesis_module is not None:
            harness.hypothesis_module.tick(harness.state.loop_turns)

        if verbose:
            print(f"\n--- Loop Turn {harness.state.loop_turns} ---", file=sys.stderr)

        # Context Window 管理：压缩历史 messages 以控制 prompt token 膨胀
        # 原始 messages 不变（保留完整历史），只压缩发给 LLM 的副本
        compressed_messages = harness.compress_messages(messages)
        
        # 防止 mutate 原始 messages（compress_messages 可能返回原引用）
        if compressed_messages is messages:
            compressed_messages = list(messages)
        
        # 动态刷新 system prompt（让 Agent 看到最新的 identity + habits + findings/edits 状态）
        # Phase 3.3/3.4: 身份+习惯+动态状态全部由 assembler 统一组装
        # 这补偿了早期 tool_result 被压缩后 Agent 可能遗忘的信息
        if compressed_messages and compressed_messages[0].get("role") == "system":
            # v2: assembler 输出已包含 static_identity + cognitive_habits + 动态 sections
            fresh_system_prompt = harness.format_context(include_identity=True)
            compressed_messages[0] = {
                "role": "system",
                "content": fresh_system_prompt,
            }
        
        if verbose:
            orig_chars = sum(len(m.get("content", "") or "") for m in messages)
            comp_chars = sum(len(m.get("content", "") or "") for m in compressed_messages)
            if comp_chars < orig_chars * 0.9:  # 压缩超过 10% 才报告
                print(f"  [Context] 压缩 {orig_chars} → {comp_chars} chars ({100-comp_chars*100//orig_chars}% saved)", file=sys.stderr)

        # Phase 4: 根据当前认知阶段动态过滤工具可见性
        # 核心 Agent 设计: LLM 只看到当前阶段允许的工具，减少噪声、防止过早行为
        # 过滤条件: harness.tool_registry 中注册的 phases 信息
        # 容错: 如果 harness 没有 phase_fsm（如子视角循环），退化为全量工具
        phase_filtered_tools = _filter_tools_by_phase(tools, harness)
        if verbose and len(phase_filtered_tools) < len(tools):
            phase_name = harness.phase_fsm.phase_name if hasattr(harness, 'phase_fsm') else "?"
            print(f"  [Phase] {phase_name}: {len(phase_filtered_tools)}/{len(tools)} tools visible", file=sys.stderr)

        # B3: temperature 和 max_tokens 从 AdaptiveConfig 动态获取
        _temperature = adaptive.temperature if adaptive else 0.3
        _max_tokens = adaptive.max_tokens if adaptive else 4096

        # ---- V5: 流式/非流式分支 ----
        # 条件: on_stream 已传入 AND kill switch 开启
        # 流式路径: 逐 chunk 推送 StreamEvent，最终组装出与非流式相同的 response dict
        # 非流式路径: 原有逻辑不变（零侵入）
        if _use_streaming:
            # --- Streaming path ---
            on_stream(StreamEvent(type="turn_start", turn=harness.state.loop_turns))

            _stream_content = ""
            _stream_tool_calls: list = []
            _stream_usage: dict = {}

            async for chunk in client.chat_with_tools_stream(
                messages=compressed_messages,
                tools=phase_filtered_tools,
                temperature=_temperature,
                max_tokens=_max_tokens,
            ):
                chunk_type = chunk.get("type")

                if chunk_type == "content_delta":
                    _stream_content += chunk["text"]
                    on_stream(StreamEvent(
                        type="thinking",
                        text=chunk["text"],
                        turn=harness.state.loop_turns,
                    ))

                elif chunk_type == "tool_calls":
                    _stream_tool_calls = chunk["tool_calls"]

                elif chunk_type == "finish":
                    _stream_usage = chunk.get("usage", {})
                    # finish chunk 也携带完整 content 和 tool_calls 作为兜底
                    if not _stream_content and chunk.get("content"):
                        _stream_content = chunk["content"]
                    if not _stream_tool_calls and chunk.get("tool_calls"):
                        _stream_tool_calls = chunk["tool_calls"]

            # 组装成与非流式 chat_with_tools 相同的 response dict
            response: dict[str, Any] = {
                "content": _stream_content or None,
                "tool_calls": _stream_tool_calls,
                "usage": _stream_usage,
            }
        else:
            # --- Non-streaming path (原有逻辑，零变更) ---
            response = await client.chat_with_tools(
                messages=compressed_messages,
                tools=phase_filtered_tools,
                temperature=_temperature,
                max_tokens=_max_tokens,
            )

        # 统计
        usage = response.get("usage", {})
        harness.state.total_tokens += usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)
        # Phase 45: 记录最近一次 prompt 大小，用于认知带宽判断
        if usage.get("prompt_tokens"):
            harness.state.last_prompt_tokens = usage["prompt_tokens"]
        # Multi-model: 按模型维度追踪 token 消耗
        if session_model_mgr is not None:
            session_model_mgr.record_tokens(
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
                model_id=client.model,
            )

        # ---- 处理文本输出 ----
        content = response.get("content") or ""
        if content:
            accumulated_content += content + "\n"
            if verbose:
                print(f"  [思考] {content[:300]}{'...' if len(content) > 300 else ''}", file=sys.stderr)

        # ---- 处理 tool calls ----
        tool_calls = response.get("tool_calls", [])

        if not tool_calls:
            # Phase 7: 无 tool call ≠ 退出。
            #
            # 设计思考：一个人在审稿时，如果脑子里冒出一段想法但还没落笔行动，
            # 那只是"思考中间态"——他不会因为想了一段话就认为自己审完了。
            # 真正的结束应该是一个显式决定（"我审完了"），对应 mark_complete。
            #
            # 因此：无 tool call 的文本被视为 Agent 的中间推理/思考，
            # 追加回 messages 后继续 loop。退出只能走 mark_complete（经 gate）
            # 或 doom loop guard 兜底。
            if content:
                messages.append({"role": "assistant", "content": content})
            if verbose:
                print("  (无工具调用，视为思考中间态，继续 loop)", file=sys.stderr)
            continue

        # 将 assistant message 加入 messages
        assistant_msg: dict[str, Any] = {"role": "assistant", "content": content or None}
        assistant_msg["tool_calls"] = [
            {
                "id": tc["id"],
                "type": "function",
                "function": {
                    "name": tc["name"],
                    "arguments": json.dumps(tc["arguments"], ensure_ascii=False),
                }
            }
            for tc in tool_calls
        ]
        messages.append(assistant_msg)

        # Phase 17: 追踪本轮是否有产出型工具调用
        _turn_had_output = False

        # 执行每个 tool call
        for tc in tool_calls:
            if verbose:
                args_preview = json.dumps(tc["arguments"], ensure_ascii=False)[:80]
                print(f"  [调用] {tc['name']}({args_preview})", file=sys.stderr)

            # V5: streaming tool_start 通知
            if _use_streaming:
                on_stream(StreamEvent(
                    type="tool_start",
                    tool_name=tc["name"],
                    turn=harness.state.loop_turns,
                    metadata={"arguments": tc["arguments"]},
                ))

            # MCL 拦截: 当 agent 调用 done/mark_complete 且 MCL 可用时，
            # 先异步询问 MCL 是否同意完成。MCL 可以 block + 提供反馈，
            # 也可以推荐 auto_spawn（视角分裂）。
            if tc["name"] in ("done", "mark_complete") and harness.mcl is not None:
                mcl_verdict = await harness.mcl.gate_completion(harness.state)
                if mcl_verdict.should_block:
                    # MCL 阻止完成：将反馈直接注入，不调用 execute_tool
                    mcl_feedback = harness.mcl.format_completion_feedback(mcl_verdict)
                    if verbose:
                        print(f"  [MCL Block] {mcl_verdict.reason}", file=sys.stderr)
                    # 如果 MCL 同时推荐 auto_spawn，构建 readers 并执行视角分裂
                    if mcl_verdict.auto_spawn_needed and mcl_verdict.auto_spawn_perspectives:
                        readers = [
                            {"lens": p, "focus": p, "question": f"从 {p} 角度审视论文的潜在问题"}
                            for p in mcl_verdict.auto_spawn_perspectives[:3]
                        ]
                        if verbose:
                            print(f"  [MCL Auto-Spawn] {len(readers)} 个子视角: {mcl_verdict.auto_spawn_perspectives[:3]}", file=sys.stderr)
                        parallel_result = await _run_parallel_perspectives(
                            harness=harness,
                            client=client,
                            readers=readers,
                            verbose=verbose,
                            session_model_mgr=session_model_mgr,
                        )
                        mcl_feedback += f"\n\n[视角分裂结果]\n{parallel_result}"
                    messages.append({"role": "tool", "tool_call_id": tc["id"], "content": mcl_feedback})
                    continue  # 跳过 execute_tool，让 agent 继续工作
                # verdict == "pass" → 放行，正常执行 tool_done

            result = harness.execute_tool(tc["name"], tc["arguments"])

            # V5: streaming tool_result 通知
            if _use_streaming:
                on_stream(StreamEvent(
                    type="tool_result",
                    tool_name=tc["name"],
                    text=result[:500] if result else "",
                    turn=harness.state.loop_turns,
                ))

            # Phase 17: 追踪认知产出
            harness.track_cognitive_output(tc["name"])
            if tc["name"] in {"update_findings", "edit_section"}:
                _turn_had_output = True

            # 解析信号
            if result.startswith("__DONE__"):
                summary = result.split("|", 1)[1] if "|" in result else ""
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": "任务完成。"})
                if verbose:
                    print(f"  [完成] {summary[:150]}", file=sys.stderr)
                # V5: streaming done 通知
                if _use_streaming:
                    on_stream(StreamEvent(
                        type="done",
                        text=summary,
                        turn=harness.state.loop_turns,
                    ))
                return LoopDone(summary=summary, content=accumulated_content)

            elif result.startswith("__NUDGE__"):
                # P3 #19: cooldown — skip nudge if within 2 turns of the previous one
                current_turn = harness.state.loop_turns
                if current_turn - last_nudge_turn < 2:
                    # Cooldown active: treat as normal completion (skip nudge)
                    messages.append({"role": "tool", "tool_call_id": tc["id"], "content": "任务完成。"})
                    if _use_streaming:
                        on_stream(StreamEvent(type="done", text="Nudge cooldown — completing", turn=current_turn))
                    return LoopDone(summary="Nudge cooldown — Agent completed", content=accumulated_content)

                last_nudge_turn = current_turn
                nudge_count += 1
                nudge_reason = result.split("|", 1)[1] if "|" in result else ""
                max_nudges = adaptive.max_nudges if adaptive else 2
                if nudge_count > max_nudges:
                    # 已经 nudge 够了，强制允许完成
                    messages.append({"role": "tool", "tool_call_id": tc["id"], "content": "任务完成。"})
                    if _use_streaming:
                        on_stream(StreamEvent(type="done", text="Agent 坚持完成", turn=harness.state.loop_turns))
                    return LoopDone(summary="Agent 坚持完成", content=accumulated_content)
                else:
                    # 给 Agent 一个 nudge，让它继续
                    if verbose:
                        print(f"  [Harness Nudge] {nudge_reason[:100]}", file=sys.stderr)
                    messages.append({"role": "tool", "tool_call_id": tc["id"], "content": nudge_reason})

            elif result.startswith("__TALK__"):
                payload_str = result.split("|", 1)[1] if "|" in result else "{}"
                try:
                    payload = json.loads(payload_str)
                except json.JSONDecodeError:
                    payload = {"message": payload_str, "expects_reply": False}

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": "消息已展示给用户。等待用户回复...",
                })
                return LoopTalk(
                    message=payload.get("message", ""),
                    expects_reply=payload.get("expects_reply", False),
                    content=accumulated_content,
                )

            elif result.startswith("__SPAWN__"):
                # 视角分裂：驱动独立子循环
                spawn_str = result.split("|", 1)[1] if "|" in result else "{}"
                try:
                    spawn_payload = json.loads(spawn_str)
                except json.JSONDecodeError:
                    messages.append({"role": "tool", "tool_call_id": tc["id"], "content": "spawn 参数解析失败。"})
                    continue

                lens = spawn_payload.get("lens", "specialist")
                focus = spawn_payload.get("focus", "")
                question = spawn_payload.get("question", "")

                if verbose:
                    print(f"  [视角分裂] lens={lens}, focus={focus}", file=sys.stderr)
                    print(f"             question={question[:80]}", file=sys.stderr)

                # 运行子循环
                sub_result = await _run_sub_perspective(
                    harness=harness,
                    client=client,
                    lens=lens,
                    focus=focus,
                    question=question,
                    verbose=verbose,
                    session_model_mgr=session_model_mgr,
                )

                # 将子循环消耗的 token 计入主 budget
                # (sub_harness 的 token 已单独统计，这里汇入主 harness)

                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": sub_result})
                if verbose:
                    print(f"  [视角分裂完成] {sub_result[:150]}", file=sys.stderr)

            elif result.startswith("__PARALLEL_SPAWN__"):
                # C4 认知分裂：并行多视角深读
                pspawn_str = result.split("|", 1)[1] if "|" in result else "{}"
                try:
                    pspawn_payload = json.loads(pspawn_str)
                except json.JSONDecodeError:
                    messages.append({"role": "tool", "tool_call_id": tc["id"], "content": "parallel spawn 参数解析失败。"})
                    continue

                readers = pspawn_payload.get("readers", [])

                if verbose:
                    print(f"  [C4 并行分裂] {len(readers)} 个子视角", file=sys.stderr)
                    for ri, r in enumerate(readers):
                        print(f"    [{ri+1}] lens={r['lens']}, focus={r['focus']}", file=sys.stderr)

                # 并行执行所有子视角
                parallel_result = await _run_parallel_perspectives(
                    harness=harness,
                    client=client,
                    readers=readers,
                    verbose=verbose,
                    session_model_mgr=session_model_mgr,
                )

                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": parallel_result})
                if verbose:
                    print(f"  [C4 并行分裂完成] {parallel_result[:200]}", file=sys.stderr)

            elif result.startswith("__SWITCH__"):
                # W1: Agent 主动切换认知人格
                switch_str = result.split("|", 1)[1] if "|" in result else "{}"
                try:
                    switch_payload = json.loads(switch_str)
                except json.JSONDecodeError:
                    messages.append({"role": "tool", "tool_call_id": tc["id"], "content": "switch 参数解析失败。"})
                    continue

                target_persona = switch_payload.get("target_persona", "scholar")
                reason = switch_payload.get("reason", "")
                nudge = switch_payload.get("nudge", "")

                # P3 #27: validate target persona before switching
                from core.identity import PERSONAS
                if target_persona not in PERSONAS:
                    _valid_names = ", ".join(sorted(PERSONAS.keys()))
                    if verbose:
                        print(f"  [人格切换] ⚠️ 无效目标 '{target_persona}'，有效值: {_valid_names}", file=sys.stderr)
                    messages.append({
                        "role": "tool", "tool_call_id": tc["id"],
                        "content": f"切换失败：'{target_persona}' 不是有效人格。有效选项: {_valid_names}。",
                    })
                    continue

                if verbose:
                    print(f"  [人格切换] → {target_persona} (原因: {reason})", file=sys.stderr)

                # 更新 harness 的当前 persona
                harness.state.current_persona = target_persona

                # Phase 跟随 persona: writer 需要编辑工具，自动推进到 EDITING
                # 设计原则 (C2): Agent 主动切换到 writer 本身就是"我要编辑"的意图表达，
                # phase 应跟随这个决定，而非 block 它。
                if target_persona == "writer" and hasattr(harness, 'phase_fsm'):
                    from core.phases import Phase
                    if harness.phase_fsm.current_phase != Phase.EDITING:
                        harness.phase_fsm.force_transition(Phase.EDITING)
                        if verbose:
                            print(f"  [Phase] 自动推进到 EDITING（跟随 writer persona）", file=sys.stderr)

                # 重建 system prompt（新 persona 的 identity + 当前 workspace state）
                from core.identity import get_persona, build_system_prompt
                new_identity, new_tools = get_persona(target_persona)
                workspace_state = harness.format_context()
                new_system_prompt = build_system_prompt(
                    identity=new_identity,
                    workspace_state=workspace_state,
                )

                # 更新 messages[0] (system prompt)
                messages[0] = {"role": "system", "content": new_system_prompt}

                # 更新可用工具列表（就地替换）
                tools.clear()
                tools.extend(new_tools)

                # P2: Writer 激活时注入编辑经验
                edit_exp_text = ""
                if target_persona == "writer" and hasattr(harness, 'evolution_engine'):
                    # 从 findings 中推断待编辑的 sections
                    target_sections = list({
                        f.get("section", "")
                        for f in harness.state.findings
                        if f.get("section")
                    }) or None
                    edit_exp_text = harness.evolution_engine.get_edit_experience_context(
                        target_sections=target_sections
                    ) or ""
                    if edit_exp_text and verbose:
                        print(f"  [P2] 注入 {len(edit_exp_text)} 字符编辑经验", file=sys.stderr)

                # 告知 Agent 切换已完成
                switch_ack = f"已切换到 {target_persona} 视角。{reason}"
                if nudge:
                    switch_ack += f"\n\n{nudge}"
                if edit_exp_text:
                    switch_ack += f"\n\n{edit_exp_text}"
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": switch_ack})

            elif result.startswith("__MODEL__"):
                # Multi-model: Agent 请求切换 LLM 模型
                model_ack = await _handle_model_signal(
                    result=result,
                    client=client,
                    messages=messages,
                    session_model_mgr=session_model_mgr,
                    verbose=verbose,
                )
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": model_ack})

            else:
                # 普通 tool 结果
                if verbose:
                    print(f"     → {result[:120]}{'...' if len(result) > 120 else ''}", file=sys.stderr)
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})

        # Phase 17: 轮次结束时，如果本轮无产出则递增连续读取计数
        if not _turn_had_output and harness.state.sections_read:
            harness.increment_read_turn()

        # Phase 5: HD-WM review_readiness 信号注入
        # 设计原则 (§4.3 约束-而非-控制): 这是环境信号，不是终止指令。
        # Agent 看到后可以自主决定是否结束或继续深入。
        if harness.hypothesis_module is not None:
            hdwm = harness.hypothesis_module
            if hdwm.is_ready and hdwm.is_saturated:
                readiness_signal = (
                    f"[HD-WM 信号] 审稿完成度 {hdwm.review_readiness:.0%}，"
                    f"假说饱和（连续 {hdwm._turns_since_last_hypothesis} 轮无新假说）。"
                    f"你可以考虑进入 synthesis 阶段。"
                )
                messages.append({"role": "system", "content": readiness_signal})
                if verbose:
                    print(f"  [HD-WM] {readiness_signal}", file=sys.stderr)


# ============================================================
# Phase 4+: Automatic Phase Transition
# ============================================================

def _try_auto_phase_transition(harness: Harness, verbose: bool = False) -> bool:
    """
    根据当前状态自动执行 Phase 转换。

    设计原则 (C2: Constrain, don't control):
        - Agent 的 identity 说"不存在阶段"——它专注于审稿内容
        - Phase FSM 是 Harness 层的"隐式守护"，Agent 无需感知
        - 转换由客观指标触发（sections_read >= 3），而非依赖 LLM 主动调用
        - 这确保 two-phase spawn、deep_review 工具可见性等下游机制正常工作

    转换规则:
        INITIAL_SCAN → DEEP_REVIEW:
            已读 >= 3 个 sections 且已有 >= 1 条 finding
            (或已读 >= 5 个 sections，无论 finding 数)

    Returns:
        True 如果发生了转换，False 否则
    """
    from core.phases import Phase

    fsm = harness.phase_fsm
    current = fsm.current_phase

    # 只处理 INITIAL_SCAN → DEEP_REVIEW 的自动转换
    # 其他转换仍由 Agent 行为隐式触发（如 switch_persona → EDITING）
    if current != Phase.INITIAL_SCAN:
        return False

    sections_read = len(harness.state.sections_read)
    findings_count = len(harness.state.findings)

    # 条件: 读了足够的 sections 并有了初步判断
    # 宽松策略: 5 个 sections 无条件转换（防止 agent 读很多但没记录 finding）
    should_transition = (
        (sections_read >= 3 and findings_count >= 1)
        or sections_read >= 5
    )

    if should_transition:
        result = fsm.request_transition(
            target=Phase.DEEP_REVIEW,
            sections_read=sections_read,
            verified_findings=findings_count,
        )
        if result.allowed and verbose:
            print(
                f"  [Phase Auto] {Phase.INITIAL_SCAN.value} → {Phase.DEEP_REVIEW.value} "
                f"(sections_read={sections_read}, findings={findings_count})",
                file=sys.stderr,
            )
        return result.allowed

    return False


# ============================================================
# Phase 4: Phase-Aware Tool Filtering
# ============================================================

def _filter_tools_by_phase(tools: list[dict], harness: Harness) -> list[dict]:
    """
    根据当前认知阶段过滤 LLM 可见的工具列表。

    设计原则:
        - 减少噪声: LLM 在 INITIAL_SCAN 阶段不需要看到 edit_section
        - 防止过早行为: 编辑工具只在 EDITING 阶段可见
        - 容错: 如果 harness 没有 phase_fsm 或 tool_registry，返回全量工具
        - 工具执行不受影响: execute_tool() 仍可执行所有工具（即使不可见）

    Args:
        tools: 全量工具定义列表（每个元素是 dict，含 name/description/input_schema）
        harness: 状态守护层（提供 phase_fsm 和 tool_registry）

    Returns:
        当前阶段可见的工具子集
    """
    # 容错: 子视角循环的 sub_harness 可能没有完整的 phase_fsm
    if not hasattr(harness, 'phase_fsm') or not hasattr(harness, 'tool_registry'):
        return tools

    phase_name = harness.phase_fsm.phase_name
    visible_names = set(harness.tool_registry.get_tools_for_phase(phase_name))

    # 从全量 tools 中筛选当前阶段可见的
    filtered = [t for t in tools if t.get("name") in visible_names]

    # 安全兜底: 如果过滤后为空（配置错误），返回全量工具而非让 LLM 无工具可用
    if not filtered:
        return tools

    return filtered


# ============================================================
# Sub-Perspective Loop — 独立视角的子循环
# ============================================================

async def _run_sub_perspective(
    harness: Harness,
    client: LLMClient,
    lens: str,
    focus: str,
    question: str,
    verbose: bool = True,
    session_model_mgr=None,
) -> str:
    """
    运行一个独立的子视角循环。

    特点：
    - 独立的 messages（不共享主 Agent 的对话历史）
    - 独立的 Harness state（不知道主 Agent 的 findings）
    - 精简的 tools（只能 read + find + done）
    - 较短的 max_turns（默认 8 轮，快速聚焦）
    - MCL 驱动的模型路由：根据视角难度选择合适模型

    完成后将 findings 注入主 harness 并返回摘要。
    """
    from core.godel_config import GODEL_SUB_READER_ROUTING_ENABLED
    from llm.router import get_tier_model

    # 0. Phase 4: 优先检查 session_model_mgr 的角色分配
    sub_client = client
    sub_model_resolved = None
    if session_model_mgr is not None:
        sub_model_resolved = session_model_mgr.resolve_model_for_role("sub_perspective")

    if sub_model_resolved is not None:
        # 用户指定或 inherit → 直接用
        sub_model = sub_model_resolved
        sub_client = client.with_model_override(sub_model) if sub_model != client.model else client
        if verbose and sub_model != client.model:
            print(f"    [Model Assignment] sub_perspective → {sub_model}", file=sys.stderr)
    elif GODEL_SUB_READER_ROUTING_ENABLED and hasattr(harness, 'mcl') and harness.mcl is not None:
        # "auto" 且无 user_override → MCL 路由
        reader_desc = [{"lens": lens, "focus": focus, "question": question}]
        tier_map = await harness.mcl.assess_reader_difficulty(
            readers=reader_desc,
            state=harness.state,
            paper_type=getattr(harness, '_inferred_paper_type', None),
        )
        tier = tier_map.get(lens, "high")
        sub_model = get_tier_model(tier, session_model_mgr=session_model_mgr)
        sub_client = client.with_model_override(sub_model)
        if verbose and sub_model != client.model:
            print(f"    [MCL Routing] {lens} → tier={tier}, model={sub_model}", file=sys.stderr)

    # 1. 解析 focus 为 section 列表
    focus_sections = [s.strip() for s in focus.split(",") if s.strip()]
    if not focus_sections:
        focus_sections = ["full"]

    # 2. 创建独立子 Harness
    sub_harness = harness.create_sub_harness(focus_sections)

    # 3. 构建子视角 system prompt
    sub_workspace_state = sub_harness.format_context()
    sub_system_prompt = build_sub_perspective_prompt(
        lens=lens,
        focus=focus,
        question=question,
        workspace_state=sub_workspace_state,
    )

    # 4. 构建初始 messages
    sub_messages = [
        {"role": "system", "content": sub_system_prompt},
        {"role": "user", "content": f"请开始审视。关注: {focus}。问题: {question}"},
    ]

    # 5. 运行子循环（复用 cognitive_loop，但用子 harness 和精简 tools）
    if verbose:
        print(f"    [Sub-Loop 开始] lens={lens}, sections={len(sub_harness.state.paper_sections)}", file=sys.stderr)

    sub_result = await cognitive_loop(
        messages=sub_messages,
        harness=sub_harness,
        tools=SUB_PERSPECTIVE_TOOLS,
        client=sub_client,
        verbose=verbose,
    )

    # 6. 提取子视角的 findings 和 summary
    sub_findings = sub_harness.state.findings
    sub_summary = ""
    sub_content = ""
    if isinstance(sub_result, LoopDone):
        sub_summary = sub_result.summary or ""
        sub_content = sub_result.content.strip() if sub_result.content else ""
        # 优先使用 content（包含完整分析），summary 可能只是短标题
        if not sub_summary or len(sub_summary) < 50:
            sub_summary = sub_content or sub_summary
    elif isinstance(sub_result, LoopDoomStop):
        sub_summary = f"(子视角因资源限制提前终止: {sub_result.reason})"
        sub_content = sub_result.content.strip() if sub_result.content else ""

    # 6.5 兜底：如果子视角产出了分析文本但 0 findings，将其分析结论作为 finding 注入
    # 这处理子 LLM 直接在 content 中写分析但不调 update_findings 的情况
    fallback_text = sub_content or sub_summary
    if not sub_findings and fallback_text and len(fallback_text) > 50:
        fallback_finding = {
            "finding": f"[{lens} 视角分析结论] {fallback_text[:500]}",
            "priority": "medium",
            "status": "needs_verification",
            "evidence": "",
            "section": focus,
        }
        sub_findings = [fallback_finding]
        if verbose:
            print(f"    [Sub-Loop 兜底] 子视角未调用 update_findings，从 content 提取结论 ({len(fallback_text)} chars)", file=sys.stderr)

    # 7. 将子 token 消耗计入主 harness
    harness.state.total_tokens += sub_harness.state.total_tokens

    # 8. 注入 findings 到主 harness，生成摘要
    result_summary = harness.ingest_perspective_findings(
        findings=sub_findings,
        lens=lens,
        summary=sub_summary,
    )

    if verbose:
        print(f"    [Sub-Loop 完成] findings={len(sub_findings)}, tokens={sub_harness.state.total_tokens}", file=sys.stderr)

    return result_summary


# ============================================================
# C4: Parallel Perspectives — 并行多视角深读
# ============================================================

async def _run_parallel_perspectives(
    harness: Harness,
    client: LLMClient,
    readers: list[dict],
    verbose: bool = True,
    session_model_mgr=None,
) -> str:
    """
    C4 认知分裂：并行运行多个子视角。

    与 _run_sub_perspective 的区别：
    - 同时 spawn N 个子视角（asyncio.gather）
    - 完成后统一合并 findings 并生成汇总报告

    设计原则：
    - 子 Agent 能力等同主 Agent（只是 context 更聚焦）
    - 信息回流结构化（findings + 置信度 + 证据）
    - 不嵌套（子视角工具集不含 spawn）
    - MCL 驱动的模型智能路由：按子视角难度选择模型层级
    - 子视角终止由 max_loop_turns 硬约束保证，子消耗事后回流父级
    - 入口处有轻量级 guard：父级剩余预算过低时跳过并行分裂
    """
    import asyncio
    from core.godel_config import GODEL_SUB_READER_ROUTING_ENABLED
    from llm.router import get_tier_model

    # ====== 轻量级预算 guard ======
    # 如果父级剩余预算已不足以让任何子视角产出有意义的结果，
    # 直接跳过并行分裂，避免无谓的开销。
    # 阈值: 每个子视角至少需要约 1 轮 LLM 调用才有价值（~4000 tokens）
    # 注意: token_budget=0 表示无限制模式，此时跳过此 guard
    _MIN_BUDGET_TO_SPAWN = 8000
    if harness.state.token_budget > 0:
        remaining = harness.state.token_budget - harness.state.total_tokens
        if remaining < _MIN_BUDGET_TO_SPAWN:
            if verbose:
                print(f"    [并行分裂跳过] 父级剩余预算不足 ({remaining:,} < {_MIN_BUDGET_TO_SPAWN:,})", file=sys.stderr)
            return (
                f"⚠️ 并行深读已跳过：父级剩余 token 预算仅 {remaining:,}，"
                f"不足以支撑有意义的子视角分析。建议直接用 read_section 做定向阅读。"
            )

    # ====== Phase 4: 模型分配解析 ======
    sub_model_resolved = None
    if session_model_mgr is not None:
        sub_model_resolved = session_model_mgr.resolve_model_for_role("sub_perspective")

    # ====== MCL 驱动的模型智能路由 (仅当 sub_model_resolved 为 None 时启用) ======
    tier_map: dict[str, str] = {}
    if sub_model_resolved is None and GODEL_SUB_READER_ROUTING_ENABLED and hasattr(harness, 'mcl') and harness.mcl is not None:
        tier_map = await harness.mcl.assess_reader_difficulty(
            readers=readers,
            state=harness.state,
            paper_type=getattr(harness, '_inferred_paper_type', None),
        )
        if verbose and tier_map:
            tiers_summary = ", ".join(f"{k}={v}" for k, v in tier_map.items())
            print(f"    [MCL Routing] {tiers_summary}", file=sys.stderr)
    elif sub_model_resolved is not None and verbose:
        print(f"    [Model Assignment] sub_perspective → {sub_model_resolved} (跳过 MCL 路由)", file=sys.stderr)

    async def _run_single(reader: dict) -> dict:
        """运行单个子视角，返回结构化结果。"""
        lens = reader["lens"]
        focus = reader["focus"]
        question = reader["question"]

        # 创建独立子 Harness（终止由 max_loop_turns 保证）
        focus_sections = [s.strip() for s in focus.split(",") if s.strip()]
        if not focus_sections or focus_sections == ["full"]:
            focus_sections = list(harness.state.paper_sections.keys())

        sub_harness = harness.create_sub_harness(focus_sections)

        # Phase 4: 模型选择 — 优先用户分配，其次 MCL 路由
        if sub_model_resolved is not None:
            sub_model = sub_model_resolved
        else:
            tier = tier_map.get(lens, "high")
            sub_model = get_tier_model(tier, session_model_mgr=session_model_mgr)
        sub_client = client.with_model_override(sub_model) if sub_model != client.model else client

        # 构建子视角 prompt
        sub_workspace_state = sub_harness.format_context()
        sub_system_prompt = build_sub_perspective_prompt(
            lens=lens,
            focus=focus,
            question=question,
            workspace_state=sub_workspace_state,
        )

        sub_messages = [
            {"role": "system", "content": sub_system_prompt},
            {"role": "user", "content": f"请开始审视。关注: {focus}。问题: {question}"},
        ]

        if verbose:
            model_info = f" model={sub_model}" if sub_model != client.model else ""
            tier_info = tier if sub_model_resolved is None else "user_override"
            print(f"    [并行子视角启动] lens={lens} tier={tier_info}{model_info}", file=sys.stderr)

        # 运行子循环
        sub_result = await cognitive_loop(
            messages=sub_messages,
            harness=sub_harness,
            tools=SUB_PERSPECTIVE_TOOLS,
            client=sub_client,
            verbose=False,  # 并行时静默子视角的过程输出，避免混乱
        )

        # 提取结果
        sub_findings = sub_harness.state.findings
        sub_summary = ""
        sub_content = ""
        if isinstance(sub_result, LoopDone):
            sub_summary = sub_result.summary or ""
            sub_content = sub_result.content.strip() if sub_result.content else ""
            if not sub_summary or len(sub_summary) < 50:
                sub_summary = sub_content or sub_summary
        elif isinstance(sub_result, LoopDoomStop):
            sub_summary = f"(因资源限制终止: {sub_result.reason})"
            sub_content = sub_result.content.strip() if sub_result.content else ""

        # 兜底：content 有分析但 0 findings
        fallback_text = sub_content or sub_summary
        if not sub_findings and fallback_text and len(fallback_text) > 50:
            sub_findings = [{
                "finding": f"[{lens} 视角分析结论] {fallback_text[:500]}",
                "priority": "medium",
                "status": "needs_verification",
                "evidence": "",
                "section": focus,
            }]

        return {
            "lens": lens,
            "focus": focus,
            "findings": sub_findings,
            "summary": sub_summary,
            "tokens_used": sub_harness.state.total_tokens,
            "turns_used": sub_harness.state.loop_turns,
        }

    # 并行执行所有子视角
    results = await asyncio.gather(*[_run_single(r) for r in readers], return_exceptions=True)

    # 汇总结果
    total_new_findings = 0
    total_tokens_used = 0
    report_lines = [f"[C4 并行深读完成] {len(readers)} 个子视角结果：\n"]

    for i, res in enumerate(results):
        if isinstance(res, BaseException):
            report_lines.append(f"  [{i+1}] ⚠️ 子视角异常: {str(res)[:100]}")
            continue

        lens = res["lens"]
        findings = res["findings"]
        summary = res["summary"]
        tokens = res["tokens_used"]
        turns = res["turns_used"]

        # 注入 findings 到主 harness
        if findings:
            harness.ingest_perspective_findings(
                findings=findings,
                lens=lens,
                summary=summary,
            )
            total_new_findings += len(findings)

        # Token 汇入主 state
        harness.state.total_tokens += tokens
        total_tokens_used += tokens

        report_lines.append(
            f"  [{i+1}] {lens}: {len(findings)} findings, {turns} turns, {tokens} tokens"
        )
        if summary:
            report_lines.append(f"      摘要: {summary[:200]}")

    report_lines.append(f"\n共新增 {total_new_findings} 条 findings，消耗 {total_tokens_used} tokens。")

    if verbose:
        print(f"    [C4 并行完成] findings={total_new_findings}, tokens={total_tokens_used}", file=sys.stderr)

    return "\n".join(report_lines)


# ============================================================
# Multi-Model Signal Handler
# ============================================================

async def _handle_model_signal(
    result: str,
    client: "LLMClient",
    messages: list[dict],
    session_model_mgr,
    verbose: bool,
) -> str:
    """处理 __MODEL__|{json} 信号，执行模型切换。

    信号格式: __MODEL__|{"target": "model-id", "reason": "..."}

    Args:
        result: 原始信号字符串
        client: 当前 LLMClient 实例（会被就地修改）
        messages: 对话历史（用于生成上下文摘要）
        session_model_mgr: SessionModelManager 实例（可能为 None）
        verbose: 是否打印调试信息

    Returns:
        给 Agent 的确认/错误消息
    """
    import json as _json

    # 解析 payload
    payload_str = result.split("|", 1)[1] if "|" in result else "{}"
    try:
        payload = _json.loads(payload_str)
    except _json.JSONDecodeError:
        return "模型切换失败：信号格式错误，无法解析 JSON。"

    target_model = payload.get("target", "").strip()
    reason = payload.get("reason", "")

    if not target_model:
        return "模型切换失败：未指定目标模型 (target)。"

    # 如果没有 SessionModelManager，多模型功能未启用
    if session_model_mgr is None:
        return (
            f"模型切换失败：多模型功能未启用。"
            f"请先配置 config/providers.json 或运行 bootstrap。"
        )

    if verbose:
        print(f"  [模型切换] → {target_model} (原因: {reason})", file=sys.stderr)

    # 执行切换
    try:
        switch_msg = await session_model_mgr.switch_model(
            target_model_id=target_model,
            reason=reason or "Agent 请求切换",
            client=client,
            messages=messages,
        )
    except ValueError as e:
        if verbose:
            print(f"  [模型切换] ✗ {e}", file=sys.stderr)
        return f"模型切换失败：{e}"
    except Exception as e:
        if verbose:
            print(f"  [模型切换] ✗ 意外错误: {e}", file=sys.stderr)
        return f"模型切换失败（内部错误）：{type(e).__name__}: {e}"

    if verbose:
        print(f"  [模型切换] ✓ 当前模型: {client.model}", file=sys.stderr)

    return switch_msg
