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
"""

from __future__ import annotations

import json
import sys
from typing import Any, AsyncGenerator

from llm.client import LLMClient
from core.harness import Harness
from core.identity import (
    SUB_PERSPECTIVE_TOOLS,
    build_sub_perspective_prompt,
)


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
# Cognitive Loop
# ============================================================

async def cognitive_loop(
    messages: list[dict],
    harness: Harness,
    tools: list[dict],
    client: LLMClient,
    verbose: bool = True,
) -> LoopResult:
    """
    执行一轮认知循环（从用户消息到 Agent 完成或暂停）。

    Args:
        messages: 完整的对话 messages 列表（会被 mutate）
        harness: 状态守护层
        tools: Agent 可用的工具定义
        client: LLM 客户端
        verbose: 是否打印过程信息

    Returns:
        LoopResult: Done / Talk / DoomStop
    """

    accumulated_content = ""
    # 用于追踪 done 被 nudge 拦截的次数（防止无限循环）
    nudge_count = 0
    max_nudges = 2  # 最多 nudge 2 次，之后强制允许 done
    # Phase 35: 追踪"计划但未行动"的 nudge 次数
    plan_nudge_count = 0
    max_plan_nudges = 2  # 最多催促 2 次计划性文本

    while True:
        # ---- 边界检查 ----
        doom = harness.check_doom_loop()
        if doom:
            if verbose:
                print(f"\n[Harness] {doom}", file=sys.stderr)
            return LoopDoomStop(reason=doom, content=accumulated_content)

        # 软提醒（接近 max turns 或 token budget 时注入 system 消息）
        soft_turn_warning = harness.check_soft_turn_limit()
        if soft_turn_warning:
            if verbose:
                print(f"  [Harness 提醒] {soft_turn_warning}", file=sys.stderr)
            messages.append({"role": "system", "content": f"[Harness 提醒] {soft_turn_warning}"})

        budget_warning = harness.check_token_budget()
        if budget_warning:
            messages.append({"role": "system", "content": f"[Harness 提示] {budget_warning}"})

        # Phase 17: 认知产出催促器 — 检测"只读不记"模式
        cognitive_nudge = harness.check_cognitive_output()
        if cognitive_nudge:
            if verbose:
                print(f"  [认知催促] {cognitive_nudge[:100]}", file=sys.stderr)
            messages.append({"role": "system", "content": cognitive_nudge})

        # Phase 37: 反思催促器 — 检测"连续行动不抬头"模式
        reflection_nudge = harness.check_reflection_needed()
        if reflection_nudge:
            if verbose:
                print(f"  [反思催促] {reflection_nudge[:60]}", file=sys.stderr)
            messages.append({"role": "system", "content": reflection_nudge})

        # ---- LLM 思考 ----
        harness.state.loop_turns += 1

        if verbose:
            print(f"\n--- Loop Turn {harness.state.loop_turns} ---", file=sys.stderr)

        # Context Window 管理：压缩历史 messages 以控制 prompt token 膨胀
        # 原始 messages 不变（保留完整历史），只压缩发给 LLM 的副本
        compressed_messages = harness.compress_messages(messages)
        
        # 防止 mutate 原始 messages（compress_messages 可能返回原引用）
        if compressed_messages is messages:
            compressed_messages = list(messages)
        
        # 动态刷新 system prompt 中的 workspace state（让 Agent 看到最新的 findings/edits 状态）
        # 这补偿了早期 tool_result 被压缩后 Agent 可能遗忘的信息
        if compressed_messages and compressed_messages[0].get("role") == "system":
            from core.identity import build_system_prompt
            fresh_state = harness.format_context()
            compressed_messages[0] = {
                "role": "system",
                "content": build_system_prompt(workspace_state=fresh_state),
            }
        
        if verbose:
            orig_chars = sum(len(m.get("content", "") or "") for m in messages)
            comp_chars = sum(len(m.get("content", "") or "") for m in compressed_messages)
            if comp_chars < orig_chars * 0.9:  # 压缩超过 10% 才报告
                print(f"  [Context] 压缩 {orig_chars} → {comp_chars} chars ({100-comp_chars*100//orig_chars}% saved)", file=sys.stderr)

        response = await client.chat_with_tools(
            messages=compressed_messages,
            tools=tools,
            temperature=0.3,
            max_tokens=4096,
        )

        # 统计
        usage = response.get("usage", {})
        harness.state.total_tokens += usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)
        # Phase 45: 记录最近一次 prompt 大小，用于认知带宽判断
        if usage.get("prompt_tokens"):
            harness.state.last_prompt_tokens = usage["prompt_tokens"]

        # ---- 处理文本输出 ----
        content = response.get("content") or ""
        if content:
            accumulated_content += content + "\n"
            if verbose:
                print(f"  [思考] {content[:300]}{'...' if len(content) > 300 else ''}", file=sys.stderr)

        # ---- 处理 tool calls ----
        tool_calls = response.get("tool_calls", [])

        if not tool_calls:
            # 没有 tool calls → 检查是否是"计划但未行动"或"分析但未记录"的模式
            # Phase 35/35b: 如果 Agent 产出了包含计划性语言或审阅发现的文本，
            # 说明它还有未完成的外化工作。给它有限次机会继续行动。
            if content and _has_unfinished_intent(content) and plan_nudge_count < max_plan_nudges:
                plan_nudge_count += 1
                if verbose:
                    print(f"  [Phase 35] 检测到未外化的审阅内容，催促用工具记录 ({plan_nudge_count}/{max_plan_nudges})", file=sys.stderr)
                messages.append({"role": "assistant", "content": content})
                messages.append({
                    "role": "system",
                    "content": (
                        "你刚才在文本中描述了审阅发现或下一步计划，但没有调用任何工具。"
                        "你的分析只有通过 update_findings 工具调用才能被正式记录。"
                        "请立即用 update_findings 将你发现的问题记录下来（包括 finding 描述、priority、evidence），"
                        "或用其他工具（read_section 等）继续审阅。不要仅在文本中列出发现而不记录。"
                    )
                })
                continue  # 回到 while True 循环顶部，让 LLM 再次思考
            else:
                if verbose:
                    print("  (无工具调用，思考结束)", file=sys.stderr)
                return LoopDone(summary="Agent 完成思考（无 tool call）", content=accumulated_content)

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

            result = harness.execute_tool(tc["name"], tc["arguments"])

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
                return LoopDone(summary=summary, content=accumulated_content)

            elif result.startswith("__NUDGE__"):
                nudge_count += 1
                nudge_reason = result.split("|", 1)[1] if "|" in result else ""
                if nudge_count > max_nudges:
                    # 已经 nudge 够了，强制允许完成
                    messages.append({"role": "tool", "tool_call_id": tc["id"], "content": "任务完成。"})
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
                )

                # 将子循环消耗的 token 计入主 budget
                # (sub_harness 的 token 已单独统计，这里汇入主 harness)

                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": sub_result})
                if verbose:
                    print(f"  [视角分裂完成] {sub_result[:150]}", file=sys.stderr)

            else:
                # 普通 tool 结果
                if verbose:
                    print(f"     → {result[:120]}{'...' if len(result) > 120 else ''}", file=sys.stderr)
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})

        # Phase 17: 轮次结束时，如果本轮无产出则递增连续读取计数
        if not _turn_had_output and harness.state.sections_read:
            harness.increment_read_turn()


# ============================================================
# Phase 35: 计划性文本检测
# ============================================================

def _has_unfinished_intent(content: str) -> bool:
    """
    检测 Agent 的文本输出是否包含未执行的计划/意图信号，
    或者包含审阅发现但未通过工具调用外化（Phase 35b）。

    两类检测：
    1. 计划性文本: Agent 写了"下一步"但没执行
    2. 发现性文本: Agent 列出了审阅发现但没调用 update_findings
       这种情况下 Agent 应该被催促将分析转为工具调用。
    """
    content_lower = content.lower()

    # --- 类型 1: 计划性信号 ---
    cn_plan_signals = ["下一步", "接下来", "将继续", "需进一步", "待验证", "需追查", "计划"]
    en_plan_signals = ["next step", "will continue", "need to verify", "plan to"]
    for signal in cn_plan_signals + en_plan_signals:
        if signal in content_lower:
            return True

    # --- 类型 2: 发现性信号 (Phase 35b) ---
    # Agent 产出了包含明确审阅发现的文本但没用 update_findings 记录。
    # 检测：文本中同时包含 "问题指示词" 和 "具体证据/引用"。
    cn_finding_markers = ["不一致", "overclaim", "矛盾", "不符", "数据不一致", "不匹配"]
    en_finding_markers = ["inconsisten", "mismatch", "discrepan", "contradict", "overclaim"]
    cn_evidence_markers = ["表格", "abstract", "结果", "声称", "实际", "但"]
    en_evidence_markers = ["table", "abstract", "claims", "actually", "however", "but "]

    has_finding = any(m in content_lower for m in cn_finding_markers + en_finding_markers)
    has_evidence = any(m in content_lower for m in cn_evidence_markers + en_evidence_markers)

    if has_finding and has_evidence:
        return True

    return False


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
) -> str:
    """
    运行一个独立的子视角循环。

    特点：
    - 独立的 messages（不共享主 Agent 的对话历史）
    - 独立的 Harness state（不知道主 Agent 的 findings）
    - 精简的 tools（只能 read + find + done）
    - 较短的 max_turns（默认 8 轮，快速聚焦）

    完成后将 findings 注入主 harness 并返回摘要。
    """
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
        client=client,
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
