"""
core/signal_parser.py — Agent Signal Protocol Parser

Phase 3-2 提取自 loop.py: 将认知循环中的信号解析逻辑统一管理。

信号协议:
    Agent 通过 tool 返回值传递控制信号。格式为:
        __SIGNAL_TYPE__|payload

    payload 可以是:
        - 纯文本 (如 __DONE__|Some summary text)
        - JSON 字符串 (如 __TALK__|{"message": "...", "expects_reply": true})

信号类型:
    DONE            - Agent 宣布任务完成
    NUDGE           - Agent 请求继续（被 harness 评估是否允许）
    TALK            - Agent 想与用户交互
    SPAWN           - 单视角分裂（子循环）
    PARALLEL_SPAWN  - 并行多视角分裂
    SWITCH          - 认知人格切换
    MODEL           - LLM 模型切换

设计原则:
    - 纯函数，无副作用: parse_signal() 只做字符串 → 数据结构转换
    - 容错: JSON 解析失败时 payload 降级为原始字符串
    - 向后兼容: is_signal() 快速判断是否为信号（用于优化普通 tool 结果路径）
    - 类型安全: ParsedSignal.payload 字段的类型随 signal_type 不同而不同
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


# ==============================================================
# Signal Types
# ==============================================================

class SignalType(Enum):
    """Agent 信号类型枚举。"""
    DONE = "__DONE__"
    NUDGE = "__NUDGE__"
    TALK = "__TALK__"
    SWITCH = "__SWITCH__"
    SPAWN = "__SPAWN__"
    PARALLEL_SPAWN = "__PARALLEL_SPAWN__"
    MODEL = "__MODEL__"


# 快速前缀查找表（按长度降序排列，确保 __PARALLEL_SPAWN__ 在 __SPAWN__ 之前匹配）
_SIGNAL_PREFIXES: list[tuple[str, SignalType]] = sorted(
    [(st.value, st) for st in SignalType],
    key=lambda pair: len(pair[0]),
    reverse=True,
)


# ==============================================================
# Parsed Signal
# ==============================================================

@dataclass
class ParsedSignal:
    """解析后的信号数据。

    Attributes:
        signal_type: 信号类型
        raw: 原始完整字符串
        payload: 解析后的负载数据，类型因信号而异:
            - DONE/NUDGE: str (纯文本摘要/原因)
            - TALK: dict with {message: str, expects_reply: bool}
            - SPAWN: dict with {lens: str, focus: str, question: str}
            - PARALLEL_SPAWN: dict with {readers: list[dict]}
            - SWITCH: dict with {target_persona: str, reason: str, nudge: str}
            - MODEL: dict with {target: str, reason: str}
        parse_error: 如果 JSON 解析失败，记录错误信息
    """
    signal_type: SignalType
    raw: str
    payload: Any = None
    parse_error: Optional[str] = None


# ==============================================================
# Public API
# ==============================================================

def is_signal(result: str) -> bool:
    """快速判断 tool 返回值是否为 Agent 信号。

    用于 loop.py 的快路径: 绝大多数 tool 返回值不是信号，
    这个函数比完整的 parse_signal() 快很多。

    Args:
        result: tool call 返回的原始字符串

    Returns:
        True 如果 result 以任意已知信号前缀开头
    """
    # 所有信号都以 "__" 开头，先做最便宜的检查
    if not result.startswith("__"):
        return False
    for prefix, _ in _SIGNAL_PREFIXES:
        if result.startswith(prefix):
            return True
    return False


def parse_signal(result: str) -> Optional[ParsedSignal]:
    """解析 Agent 信号字符串为结构化数据。

    Args:
        result: tool call 返回的原始字符串

    Returns:
        ParsedSignal 如果是有效信号，None 如果不是信号

    Examples:
        >>> parse_signal("__DONE__|审稿完成")
        ParsedSignal(signal_type=SignalType.DONE, payload="审稿完成", ...)

        >>> parse_signal('__TALK__|{"message": "hi", "expects_reply": true}')
        ParsedSignal(signal_type=SignalType.TALK, payload={"message": "hi", ...}, ...)

        >>> parse_signal("Normal tool output")
        None
    """
    if not result.startswith("__"):
        return None

    # 识别信号类型
    matched_type: Optional[SignalType] = None
    for prefix, signal_type in _SIGNAL_PREFIXES:
        if result.startswith(prefix):
            matched_type = signal_type
            break

    if matched_type is None:
        return None

    # 提取 payload 字符串（分隔符 "|" 后的部分）
    payload_str = result.split("|", 1)[1] if "|" in result else ""

    # 根据信号类型解析 payload
    payload, parse_error = _parse_payload(matched_type, payload_str)

    return ParsedSignal(
        signal_type=matched_type,
        raw=result,
        payload=payload,
        parse_error=parse_error,
    )


# ==============================================================
# Internal Helpers
# ==============================================================

def _parse_payload(
    signal_type: SignalType,
    payload_str: str,
) -> tuple[Any, Optional[str]]:
    """根据信号类型解析 payload 内容。

    Returns:
        (parsed_payload, error_message_or_none)
    """
    # 纯文本类型：直接返回字符串
    if signal_type in (SignalType.DONE, SignalType.NUDGE):
        return payload_str, None

    # JSON 类型：空 payload 的处理
    if not payload_str.strip():
        # TALK 空 payload 是合法的（兼容旧格式的降级行为）
        if signal_type == SignalType.TALK:
            return {"message": "", "expects_reply": False}, None
        # 其他 JSON 类型空 payload 是格式错误（与旧代码行为一致：json.loads("") 会抛异常）
        return None, "payload 为空，无法解析 JSON"

    try:
        parsed = json.loads(payload_str)
    except json.JSONDecodeError as e:
        # 降级处理：TALK 特殊处理（兼容旧格式）
        if signal_type == SignalType.TALK:
            return {"message": payload_str, "expects_reply": False}, None
        return None, f"JSON 解析失败: {e}"

    return parsed, None
