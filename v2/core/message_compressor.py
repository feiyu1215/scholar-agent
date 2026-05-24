"""
message_compressor.py — Context Window 压缩模块

从 harness.py 提取。负责 messages 列表的压缩以控制 context window 膨胀。

v2 策略分两层:
    Layer 1 (Smart Compaction): context window 占比超阈值时，
        使用 CompactionEngine 做带工作台恢复的结构化压缩。
    Layer 2 (Legacy/Fine-grained): 对保留区内消息做细粒度压缩。
"""

from __future__ import annotations

import json
from typing import Any

from core.state import WorkspaceState
from core.compaction import CompactionEngine
from core.session_memory import SessionMemoryManager
from core.hypothesis import HypothesisModule


def compress_messages(
    messages: list[dict],
    state: WorkspaceState,
    compaction_engine: CompactionEngine,
    session_memory: SessionMemoryManager,
    hypothesis_module: HypothesisModule | None,
    keep_recent: int = 6,
) -> list[dict]:
    """
    压缩 messages 列表以控制 context window 膨胀。

    设计要点:
    - 保留 system prompt（始终完整）
    - Smart Compaction 时注入工作台恢复（findings + 进度 + 最近操作）
    - 保留最近 keep_recent 组完整的 assistant+tool_result 交互
    - 更早的历史：压缩 tool_result 为摘要，保留 assistant 的 tool_call 元信息
    - 始终保留 user messages

    Args:
        messages: 原始 messages 列表（不会被 mutate）
        state: WorkspaceState
        compaction_engine: Smart Compaction 引擎
        session_memory: Session Memory Manager（用于恢复信息注入）
        hypothesis_module: HD-WM 假说模块（可为 None）
        keep_recent: 保留最近多少组完整交互

    Returns:
        压缩后的 messages 列表（新列表）
    """
    # v2: Smart Compaction — 判断是否需要结构化压缩（带工作台恢复）
    if compaction_engine.should_compact(state, messages):
        # Phase 13/M1: Session Memory 认知笔记
        sm_text = session_memory.format_for_restoration()

        # Phase 14/M2: HD-WM 假说状态
        hyp_text = ""
        if hypothesis_module is not None and hypothesis_module.has_active():
            hyp_text = hypothesis_module.format_for_restoration()

        # Phase 14/M2: 论文结构索引（精简版）
        paper_struct_text = ""
        if state.paper_structure_index is not None:
            paper_struct_text = state.paper_structure_index.format_for_context()

        snapshot = compaction_engine.build_snapshot(
            state, messages,
            session_memory_text=sm_text,
            hypothesis_text=hyp_text,
            paper_structure_text=paper_struct_text,
        )
        compacted = compaction_engine.compact(messages, snapshot, state)
        # Smart Compaction 后，对保留区内的消息继续做细粒度压缩
        return _fine_grained_compress(compacted, keep_recent=3)

    # Legacy path: 基于 context ratio 的 adaptive compression
    context_ratio = state.last_prompt_tokens / state.context_window if state.context_window else 0
    if context_ratio > 0.5:
        keep_recent = min(keep_recent, 4)
    if context_ratio > 0.7:
        keep_recent = min(keep_recent, 3)
    if len(messages) <= keep_recent * 2 + 2:
        return messages

    # 找到所有 assistant messages 的位置
    assistant_indices = [i for i, m in enumerate(messages) if m.get("role") == "assistant"]

    if len(assistant_indices) <= keep_recent:
        return messages

    # 确定压缩边界
    compress_before_idx = assistant_indices[-keep_recent]

    # 构建压缩后的 messages
    compressed = []
    i = 0
    while i < len(messages):
        msg = messages[i]

        if i >= compress_before_idx:
            compressed.append(msg)
            i += 1
            continue

        if msg.get("role") == "system":
            compressed.append(msg)
            i += 1
        elif msg.get("role") == "user":
            compressed.append(msg)
            i += 1
        elif msg.get("role") == "assistant":
            compressed_assistant = _compress_assistant_msg(msg)
            compressed.append(compressed_assistant)
            i += 1
        elif msg.get("role") == "tool":
            compressed_tool = _compress_tool_result(msg)
            compressed.append(compressed_tool)
            i += 1
        else:
            compressed.append(msg)
            i += 1

    return compressed


def _fine_grained_compress(messages: list[dict], keep_recent: int = 3) -> list[dict]:
    """
    对已经过 Smart Compaction 的消息做细粒度压缩。

    只压缩保留区中较旧的 tool results（截断长内容），
    最近 keep_recent 组保持完整。
    """
    assistant_indices = [i for i, m in enumerate(messages) if m.get("role") == "assistant"]
    if len(assistant_indices) <= keep_recent:
        return messages

    compress_before_idx = assistant_indices[-keep_recent]
    result = []
    for i, msg in enumerate(messages):
        if i >= compress_before_idx:
            result.append(msg)
        elif msg.get("role") == "tool" and len(msg.get("content", "")) > 200:
            result.append(_compress_tool_result(msg))
        elif msg.get("role") == "assistant" and "tool_calls" in msg:
            result.append(_compress_assistant_msg(msg))
        else:
            result.append(msg)
    return result


def _compress_assistant_msg(msg: dict) -> dict:
    """压缩 assistant message：保留 tool_call 元信息，精简 arguments。"""
    compressed = {"role": "assistant", "content": msg.get("content") or None}

    if "tool_calls" in msg:
        compressed_calls = []
        for tc in msg["tool_calls"]:
            func = tc.get("function", {})
            name = func.get("name", "unknown")
            args_str = func.get("arguments", "{}")
            try:
                args = json.loads(args_str)
                if name == "read_section":
                    short_args = json.dumps({"section": args.get("section", "?")}, ensure_ascii=False)
                elif name == "update_findings":
                    short_args = json.dumps({
                        "finding": args.get("finding", "")[:80] + "...",
                        "priority": args.get("priority", "?"),
                    }, ensure_ascii=False)
                elif name == "search_literature":
                    short_args = json.dumps({"query": args.get("query", "?")}, ensure_ascii=False)
                elif name == "edit_section":
                    short_args = json.dumps({
                        "section": args.get("section", "?"),
                        "reason": args.get("reason", "")[:60],
                    }, ensure_ascii=False)
                else:
                    short_args = args_str[:100] + "..." if len(args_str) > 100 else args_str
            except (json.JSONDecodeError, TypeError):
                short_args = args_str[:100] if len(args_str) > 100 else args_str

            compressed_calls.append({
                "id": tc["id"],
                "type": "function",
                "function": {"name": name, "arguments": short_args},
            })
        compressed["tool_calls"] = compressed_calls

    return compressed


def _compress_tool_result(msg: dict) -> dict:
    """压缩 tool result：长文本→摘要。"""
    content = msg.get("content", "")
    tool_call_id = msg.get("tool_call_id", "")

    if len(content) <= 200:
        return msg

    if content.startswith("[注意]"):
        summary = content
    elif content.startswith("搜索 '"):
        lines = content.split("\n")
        summary = lines[0] + f" [完整结果已压缩, 原文 {len(content)} 字符]"
    elif content.startswith("已记录发现"):
        summary = content
    elif content.startswith("发现回顾"):
        summary = content[:200] + f"... [已压缩, 原文 {len(content)} 字符]"
    elif content.startswith("可用 sections"):
        summary = content
    else:
        summary = (
            f"[历史读取, {len(content)} 字符] "
            + content[:150].replace("\n", " ")
            + "..."
        )

    return {"role": "tool", "tool_call_id": tool_call_id, "content": summary}
