"""
core/stream_events.py — Streaming 事件定义

方案 B（回调注入模式）的事件类型。cognitive_loop 在关键节点
通过 on_stream 回调将事件推送给调用方，实现实时进度通知。

设计原则:
    - 事件是纯数据对象，不包含行为逻辑
    - 调用方通过 event.type 判断事件类型并做对应处理
    - 所有字段有安全默认值，不会因缺失字段而 crash
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal, Optional


StreamEventType = Literal[
    "thinking",      # Agent 产出思考文本（逐 chunk 或完整段）
    "tool_start",    # 即将执行一个工具
    "tool_result",   # 工具执行完成
    "turn_start",    # 新一轮 loop 开始
    "done",          # 认知循环结束
]


@dataclass(frozen=True)
class StreamEvent:
    """认知循环的流式事件。

    Attributes:
        type: 事件类型
        text: 文本内容（thinking 时为 LLM 输出片段，tool_result 时为执行结果摘要）
        tool_name: 工具名称（tool_start/tool_result 时有值）
        turn: 当前循环轮次
        metadata: 额外元数据（如 token 用量、finish_reason 等）
    """
    type: StreamEventType
    text: str = ""
    tool_name: str = ""
    turn: int = 0
    metadata: dict = field(default_factory=dict)


# 类型别名：on_stream 回调的签名
OnStreamCallback = Optional[Callable[[StreamEvent], None]]
