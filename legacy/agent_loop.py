"""
core/agent_loop.py — The main agent loop with native function calling.

Protocol:
1. Send messages + tools to LLM via native function calling API
2. If response has tool_calls: execute each, append tool results as role=tool
3. If response is text only: display to user, return
4. Loop until model stops calling tools
"""

from __future__ import annotations

import json
import asyncio
import time

from core.state import (
    WORKSPACE,
    goal_tracker, plan_store, reflection_engine,
    adaptive_engine, context_manager, error_recovery, output_quality,
    session_memory, meta_planner,
)
from core.prompts import SYSTEM_PROMPT
from core.tool_schemas import TOOLS
from core.tool_dispatch import TOOL_HANDLERS
from core.context_pipeline import (
    smart_compact, auto_compact, estimate_tokens, RETENTION_POLICY,
    TOKEN_THRESHOLD_HARD,
)

from utils.doom_loop import DoomLoopDetector
from utils.recall import recall_get, recall_store, recall_invalidate
from utils.trace import log_trace
from utils.goal_tracker import Phase
from utils.phase_filter import filter_tools_for_phase, get_hidden_tools_hint
from utils.ambiguity_detector import detect_ambiguity

# ============================================================
# Doom Loop Detection
# ============================================================

_doom_detector = DoomLoopDetector()
_consecutive_doom_blocks = 0
MAX_CONSECUTIVE_DOOM_BLOCKS = 3

# ============================================================
# Streaming / Pause State
# ============================================================

_paused = False
_takeover = False


def pause_agent():
    """Pause the agent loop (called from REPL on /pause)."""
    global _paused
    _paused = True


def resume_agent():
    """Resume the agent loop (called from REPL on /resume)."""
    global _paused
    _paused = False


def takeover_agent():
    """Signal takeover mode — agent stops and yields control to user."""
    global _takeover
    _takeover = True


def reset_control():
    """Reset pause/takeover flags at the start of each agent_loop invocation."""
    global _paused, _takeover
    _paused = False
    _takeover = False


# ============================================================
# Agent Loop
# ============================================================

async def agent_loop(messages: list, client, *, stream: bool = False):
    """The agent loop. Model decides via native tool_use, harness executes.

    Args:
        messages: Conversation history (mutated in place).
        client: LLMClient instance.
        stream: If True, stream text output token-by-token.
    """
    global _consecutive_doom_blocks, _paused, _takeover

    # Import state at call time (they may be initialized after module load)
    from core import state

    while True:
        # Check pause/takeover
        if _takeover:
            print("\n\033[33m[Takeover: Agent yielding control to user.]\033[0m")
            reset_control()
            return
        if _paused:
            print("\n\033[33m[Paused. Use /resume to continue or /takeover to take control.]\033[0m")
            while _paused and not _takeover:
                await asyncio.sleep(0.2)
            if _takeover:
                print("\n\033[33m[Takeover: Agent yielding control to user.]\033[0m")
                reset_control()
                return
            print("\033[33m[Resumed.]\033[0m")

        # Layer 1: smart compact based on retention policies
        smart_compact(messages)

        # Layer 2: auto compress if context still too large
        token_count = estimate_tokens(messages)
        if token_count > TOKEN_THRESHOLD_HARD:
            print(f"[Auto-compressing context ({token_count} tokens > {TOKEN_THRESHOLD_HARD} hard limit)...]")
            messages[:] = await auto_compact(messages, client)

        # Wave 3: Proactive context compression
        if state.context_manager:
            state.context_manager.update(messages)
            if state.context_manager.must_compress():
                print("[Proactive context compression (budget at " +
                      f"{state.context_manager.get_budget().usage_ratio:.0%})...]")
                messages[:] = state.context_manager.compress(messages, RETENTION_POLICY)
            elif state.context_manager.should_compress():
                state.context_manager.update(messages)
                if state.context_manager.should_compress():
                    messages[:] = state.context_manager.compress(
                        messages, RETENTION_POLICY, recent_window=10
                    )

        # Build system message with goal/phase context injection
        system_content = SYSTEM_PROMPT.format(
            workspace=str(WORKSPACE),
            budget=state.session_budget,
        )

        # Ambiguity detection: check the latest user message
        # Only inject on the first turn after a user message (not after tool results)
        _last_user_msg = None
        for m in reversed(messages):
            if m.get("role") == "user" and not m.get("content", "").startswith("["):
                _last_user_msg = m.get("content", "")
                break
            elif m.get("role") == "assistant":
                break  # Already responded, don't re-inject

        if _last_user_msg:
            ambiguity_signal = detect_ambiguity(_last_user_msg)
            if ambiguity_signal.is_ambiguous:
                system_content += ambiguity_signal.injection_text
        # Wave 2: Inject goal tracker state
        if state.goal_tracker:
            system_content += "\n\n" + state.goal_tracker.get_context_injection()
            if state.plan_store:
                active_plan = state.plan_store.get_active_plan()
                if active_plan:
                    system_content += "\n" + active_plan.progress_summary()

        # Wave 3: Inject adaptive strategy context
        if state.adaptive_engine:
            strategy_ctx = state.adaptive_engine.get_context_injection()
            if strategy_ctx:
                system_content += "\n\n" + strategy_ctx

        # Wave 3: Inject error recovery warnings
        if state.error_recovery:
            recovery_ctx = state.error_recovery.get_recovery_context()
            if recovery_ctx:
                system_content += recovery_ctx

        # Wave 5: Unified memory context injection (preferred over legacy)
        if state.unified_memory:
            phase_str = state.goal_tracker.phase.value if (
                state.goal_tracker and state.goal_tracker.phase) else "general"
            memory_ctx = state.unified_memory.get_context_for_phase(phase_str)
            if memory_ctx:
                system_content += "\n\n" + memory_ctx
        elif state.session_memory:
            # Fallback to legacy session memory
            memory_ctx = state.session_memory.get_startup_context()
            if memory_ctx:
                system_content += "\n\n" + memory_ctx

        # Wave 4: Meta-planner advice (still uses SessionMemory patterns)
        if state.meta_planner and state.goal_tracker and state.goal_tracker.current_goal:
            plan_advice = state.meta_planner.get_context_injection(
                state.goal_tracker.current_goal.description,
                state.goal_tracker.phase.value if state.goal_tracker.phase else "",
            )
            if plan_advice:
                system_content += "\n\n" + plan_advice

        # Wave 2: Phase-aware tool filtering
        active_tools = TOOLS
        if state.goal_tracker and state.goal_tracker.phase != Phase.IDLE:
            active_tools = filter_tools_for_phase(TOOLS, state.goal_tracker.phase)
            hidden_hint = get_hidden_tools_hint(TOOLS, state.goal_tracker.phase)
            if hidden_hint:
                system_content += hidden_hint

        # Wave 3: Update context manager with system prompt overhead
        if state.context_manager:
            state.context_manager.set_system_overhead(system_content)

        # Call LLM with native function calling
        full_messages = [{"role": "system", "content": system_content}] + messages

        if stream and hasattr(client, 'chat_with_tools_stream'):
            response = await _stream_response(client, full_messages, active_tools, messages)
        else:
            response = await client.chat_with_tools(
                messages=full_messages,
                tools=active_tools,
                max_tokens=4096,
                temperature=0.1,
            )

        content = response["content"]
        tool_calls = response["tool_calls"]
        finish_reason = response["finish_reason"]

        # Case 1: No tool calls - model is responding to user
        if not tool_calls:
            if content:
                messages.append({"role": "assistant", "content": content})
                if not stream:  # Stream already printed
                    print("\n\033[32m" + content + "\033[0m\n")
            elif finish_reason == "length":
                messages.append({"role": "assistant", "content": content or ""})
                messages.append({"role": "user", "content": "[Your output was truncated. Please continue from where you left off.]"})
                continue
            _consecutive_doom_blocks = 0
            return

        # Case 2: Tool calls - execute and loop
        assistant_msg = {"role": "assistant", "content": content}
        assistant_msg["tool_calls"] = [
            {
                "id": tc["id"],
                "type": "function",
                "function": {
                    "name": tc["name"],
                    "arguments": json.dumps(tc["arguments"], ensure_ascii=False),
                },
            }
            for tc in tool_calls
        ]
        messages.append(assistant_msg)

        # Execute each tool call and append results
        for tc in tool_calls:
            tool_name = tc["name"]
            tool_call_id = tc["id"]
            args = tc["arguments"]

            # Check pause between tool calls
            if _paused:
                print(f"\n\033[33m[Paused before executing {tool_name}. /resume to continue.]\033[0m")
                while _paused and not _takeover:
                    await asyncio.sleep(0.2)
                if _takeover:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "name": tool_name,
                        "content": "[Takeover: tool execution cancelled by user]",
                    })
                    print("\n\033[33m[Takeover: Agent yielding control.]\033[0m")
                    reset_control()
                    return

            # Doom Loop check
            is_looping, loop_msg = _doom_detector.check(tool_name, args)
            if is_looping:
                _consecutive_doom_blocks += 1
                output = loop_msg
                log_trace(tool_name, args, output, 0.0, error="doom_loop_blocked")
                print("\033[31m> " + tool_name + " BLOCKED (doom loop, #" + str(_consecutive_doom_blocks) + ")\033[0m")

                if _consecutive_doom_blocks >= MAX_CONSECUTIVE_DOOM_BLOCKS:
                    output += (
                        "\n\nHARD EXIT: Agent has been blocked "
                        + str(_consecutive_doom_blocks) + " consecutive times. "
                        "Stopping to prevent infinite loop. "
                        "Please reformulate your request or try a different approach."
                    )
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "name": tool_name,
                        "content": output,
                    })
                    messages.append({"role": "user", "content": "[SYSTEM: Tool loop detected. You MUST respond to the user with text explaining what happened and what they can do. Do NOT call any more tools.]"})
                    _consecutive_doom_blocks = 0
                    break
            else:
                _consecutive_doom_blocks = 0

                # Wave 3: Capture original text BEFORE handler runs (for quality gate)
                _pre_rewrite_text = ""
                if state.output_quality and tool_name in (
                    "rewrite_section", "generate_rewrite", "deai_rewrite"
                ):
                    section_id = args.get("section_id", "")
                    if section_id:
                        sec_path = WORKSPACE / "paper" / f"{section_id}.txt"
                        if sec_path.exists():
                            try:
                                _pre_rewrite_text = sec_path.read_text(encoding="utf-8")
                            except OSError:
                                pass

                # Recall Path: check cache
                cached = recall_get(tool_name, args)
                if cached is not None:
                    output = cached
                    log_trace(tool_name, args, output, 0.0, note="recall_hit")
                    print("\033[35m> " + tool_name + " (recall hit)\033[0m")
                    print("  " + str(output)[:300])
                else:
                    handler = TOOL_HANDLERS.get(tool_name)
                    if handler:
                        t0 = time.time()
                        tokens_before = client.stats()
                        error_msg = None
                        try:
                            result = handler(**args)
                            if asyncio.iscoroutine(result):
                                output = await result
                            else:
                                output = result
                        except Exception as e:
                            output = "Error: " + type(e).__name__ + ": " + str(e)
                            error_msg = str(e)

                        duration_ms = (time.time() - t0) * 1000
                        tokens_after = client.stats()
                        log_trace(tool_name, args, output, duration_ms,
                                  tokens_before, tokens_after, error_msg)

                        # Store in recall cache
                        recall_store(tool_name, args, output)

                        # Invalidate related caches on write operations
                        if tool_name in ("rewrite_section", "edit_section", "approve_fix", "commit_rewrite"):
                            section_id = args.get("section_id", "")
                            recall_invalidate(section_id=section_id)
                            recall_invalidate(tool_name="read_section")
                            recall_invalidate(tool_name="consistency_check")

                        args_str = ", ".join(k + "=" + repr(v)[:50] for k, v in args.items())
                        print("\033[33m> " + tool_name + "(" + args_str + ")\033[0m")
                        print("  " + str(output)[:300])
                    else:
                        output = "Error: Unknown tool '" + tool_name + "'. Available: " + str(list(TOOL_HANDLERS.keys()))

            # Detect error status
            had_error = isinstance(output, str) and output.startswith("Error:")

            # Wave 3: Error recovery
            if had_error and state.error_recovery:
                recovery = state.error_recovery.handle_error(tool_name, str(output))
                action = recovery.get("action", "report")
                if action == "retry":
                    delay = recovery.get("retry_delay", 1)
                    print(f"\033[33m  [Recovery: retrying in {delay}s...]\033[0m")
                    await asyncio.sleep(delay)
                    try:
                        handler = TOOL_HANDLERS.get(tool_name)
                        if handler:
                            result = handler(**args)
                            if asyncio.iscoroutine(result):
                                output = await result
                            else:
                                output = result
                            had_error = isinstance(output, str) and output.startswith("Error:")
                            if not had_error:
                                state.error_recovery.record_success(tool_name)
                                print(f"\033[32m  [Recovery: retry succeeded]\033[0m")
                    except Exception as retry_err:
                        print(f"\033[31m  [Recovery: retry failed: {type(retry_err).__name__}: {retry_err}]\033[0m")
                elif action == "fallback":
                    fallbacks = recovery.get("fallbacks", [])
                    if fallbacks:
                        output += (
                            f"\n\n[RECOVERY SUGGESTION: {tool_name} failed. "
                            f"Consider using {fallbacks[0]} as an alternative.]"
                        )
            elif not had_error and state.error_recovery:
                state.error_recovery.record_success(tool_name)
            if not had_error and state.output_quality and tool_name not in (
                "rewrite_section", "generate_rewrite", "deai_rewrite"
            ):
                state.output_quality.reset_retries()

            # Wave 3: Output quality gate (for critical operations)
            if not had_error and state.output_quality and tool_name in (
                "rewrite_section", "generate_rewrite", "deai_rewrite"
            ):
                original_text = _pre_rewrite_text
                if original_text and isinstance(output, str) and not output.startswith("Error:"):
                    qc = state.output_quality.check_rewrite(original_text, output)
                    if not qc.passed and qc.should_retry:
                        output += (
                            f"\n\n[QUALITY WARNING: {qc.summary()}. "
                            f"Recommendations: {'; '.join(qc.recommendations)}]"
                        )

            # Append tool result in native format
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "name": tool_name,
                "content": str(output) if output else "",
            })

            # Wave 4: Record tool usage in session memory
            if state.session_memory:
                state.session_memory.record_tool_usage(tool_name)
                if had_error:
                    state.session_memory.record_error(tool_name, str(output)[:200])

            # Wave 2: Phase transitions + reflection
            if state.goal_tracker:
                state.goal_tracker.on_tool_complete(tool_name, args)
            if state.reflection_engine:
                reflection = state.reflection_engine.on_tool_complete(tool_name, had_error)
                if reflection:
                    messages.append({
                        "role": "user",
                        "content": "[INTERNAL REFLECTION — not from user, do not address directly]\n" + reflection,
                    })


async def _stream_response(client, full_messages, active_tools, messages):
    """Stream the LLM response, printing tokens as they arrive.

    Falls back to non-streaming if streaming is not available.
    """
    try:
        collected_content = ""
        tool_calls = []
        finish_reason = "stop"
        usage = {}

        print("\n\033[32m", end="", flush=True)
        async for chunk in client.chat_with_tools_stream(
            messages=full_messages,
            tools=active_tools,
            max_tokens=4096,
            temperature=0.1,
        ):
            if chunk.get("type") == "content_delta":
                text = chunk.get("text", "")
                collected_content += text
                print(text, end="", flush=True)
            elif chunk.get("type") == "tool_calls":
                tool_calls = chunk.get("tool_calls", [])
            elif chunk.get("type") == "finish":
                finish_reason = chunk.get("finish_reason", "stop")
                usage = chunk.get("usage", {})
        print("\033[0m\n", flush=True)

        return {
            "content": collected_content if collected_content else None,
            "tool_calls": tool_calls,
            "finish_reason": finish_reason,
            "usage": usage,
        }
    except (AttributeError, NotImplementedError):
        # Fallback to non-streaming
        return await client.chat_with_tools(
            messages=full_messages,
            tools=active_tools,
            max_tokens=4096,
            temperature=0.1,
        )
