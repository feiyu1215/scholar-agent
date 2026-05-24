"""
session_finalizer.py — 会话结束时的记忆沉淀模块

从 harness.py 提取。负责在审稿会话结束时：
1. 构建 SessionRecord（压缩 findings 为摘要）
2. 从 verified findings 中提取可积累的领域模式（Layer 2: WHAT）
3. 从工具调用序列中提取程序性模式（Layer 3: HOW, Phase 54）
4. 将 Agent 的认知提示持久化为跨会话经验
5. 记录审稿行为统计
6. 持久化到磁盘
"""

from __future__ import annotations

from typing import Any

from core.state import WorkspaceState
from core.memory import (
    MemoryStore,
    build_session_record,
    extract_domain_patterns,
    extract_procedural_patterns,
)
from core.cognition_graph import persist_cognitive_hints_as_experience
from core.gate_config import (
    record_review_stats,
    compute_idle_rounds_before_exit,
)


def end_session(
    state: WorkspaceState,
    memory: MemoryStore,
    paper_id: str | None,
    strategy_transitions: list | None,
    paper_title: str = "",
    user_messages: list[str] | None = None,
) -> None:
    """
    会话结束时调用: 将当前会话的认知产出沉淀到跨会话记忆。

    Args:
        state: 工作区状态
        memory: 记忆存储实例
        paper_id: 论文唯一标识（hash）
        strategy_transitions: 策略转换记录（用于程序性模式提取）
        paper_title: 论文标题（如果未提供，尝试从 paper_sections 推断）
        user_messages: 用户发送的消息列表（用于记录用户关注点）
    """
    if not state.findings:
        # 没有任何发现，不值得记录
        return

    # 确保 paper_id 存在
    if not paper_id and state.paper_sections:
        paper_id = MemoryStore.compute_paper_id(state.paper_sections)

    if not paper_id:
        return

    # 推断论文标题
    if not paper_title:
        paper_title = _infer_paper_title(state, paper_id)

    # 1. 构建 SessionRecord
    record = build_session_record(
        paper_id=paper_id,
        paper_title=paper_title,
        findings=state.findings,
        conversation_turns=state.conversation_turns,
        loop_turns=state.loop_turns,
        total_tokens=state.total_tokens,
        user_messages=user_messages,
    )
    memory.persist_session(record)

    # 2. 提取并积累领域模式（Layer 2: WHAT）
    patterns = extract_domain_patterns(state.findings, paper_id)
    for category, description in patterns:
        memory.add_or_reinforce_pattern(category, description, paper_id)

    # 3. Phase 54: 提取并积累程序性模式（Layer 3: HOW）
    tool_names = [t.get("name", "") for t in state.tool_call_history]
    procedural_patterns = extract_procedural_patterns(
        tool_call_history=tool_names,
        findings_count=len(state.findings),
        loop_turns=state.loop_turns,
        strategy_transitions=strategy_transitions if strategy_transitions else None,
    )
    for cat, desc, trigger, score in procedural_patterns:
        memory.add_or_reinforce_procedure(cat, desc, trigger, score)

    # 4. K1: 将 Agent 的认知提示持久化为跨会话经验
    if state.cognitive_hints:
        persist_cognitive_hints_as_experience(
            cognitive_hints=state.cognitive_hints,
            memory_store=memory,
            paper_id=paper_id,
            findings_count=len(state.findings),
        )

    # 5. B4: 记录审稿行为统计（用于长期数据驱动的 gate 参数优化）
    paper_type = ""
    if state.cognitive_hints and state.cognitive_hints.paper_type_description:
        paper_type = state.cognitive_hints.paper_type_description
    elif state.paper_structure_index and not state.paper_structure_index.is_empty():
        paper_type = state.paper_structure_index.paper_type

    if paper_type:
        idle_before_exit = compute_idle_rounds_before_exit(
            state.tool_call_history, state.findings
        )
        record_review_stats(
            memory_store=memory,
            paper_type=paper_type,
            total_turns=state.loop_turns,
            idle_rounds_before_exit=idle_before_exit,
            findings_count=len(state.findings),
        )

    # 6. 持久化
    memory.save()


def _infer_paper_title(state: WorkspaceState, paper_id: str) -> str:
    """从 paper_sections 推断论文标题。"""
    for key in state.paper_sections:
        if "title" in key.lower() or "abstract" in key.lower():
            content = state.paper_sections[key]
            for line in content.split("\n"):
                line = line.strip().strip("#").strip()
                if line and len(line) > 10:
                    return line[:100]
    return f"Paper_{paper_id[:8]}"
