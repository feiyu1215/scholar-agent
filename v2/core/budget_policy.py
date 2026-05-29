"""
core/budget_policy.py -- Token Budget 策略（极简版）

设计原则:
    1. Budget 是安全网/止损线，不是行为引导
    2. Agent 永远不知道 budget 存在
    3. 只有一个判断: is_exceeded? -> 硬停
    4. 支持 allow_pause: 截断后保存状态供断点续传
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class BudgetPolicy:
    """Token Budget 策略。用户通过入口层设置，Harness 在运行时执行。

    Attributes:
        token_limit: 累计 token 消耗上限。0 表示不限制（无限模式）。
        allow_pause: 截断后是否保存 checkpoint 供 resume（True = 保存）。
    """

    token_limit: int = 0
    allow_pause: bool = True

    @property
    def is_unlimited(self) -> bool:
        """是否为无限制模式。"""
        return self.token_limit <= 0

    def is_exceeded(self, total_tokens_used: int) -> bool:
        """当前累计消耗是否已超出限制。"""
        if self.is_unlimited:
            return False
        return total_tokens_used >= self.token_limit

    def format_report(
        self,
        total_tokens_used: int,
        findings_count: int = 0,
        sections_read: int = 0,
        total_sections: int = 0,
        loop_turns: int = 0,
    ) -> str:
        """格式化 post-hoc 进度报告（给用户看，不给 Agent 看）。"""
        if self.is_unlimited:
            parts = [f"已消耗 {total_tokens_used:,} tokens（无上限模式）"]
        else:
            pct = total_tokens_used / self.token_limit * 100
            parts = [f"Token: {total_tokens_used:,}/{self.token_limit:,} ({pct:.0f}%)"]

        if total_sections > 0:
            parts.append(f"进度: {sections_read}/{total_sections} sections")
        if findings_count > 0:
            parts.append(f"产出: {findings_count} findings")
        if loop_turns > 0:
            parts.append(f"轮次: {loop_turns}")

        return " | ".join(parts)


# ==============================================================
# 序列化（用于 checkpoint / resume）
# ==============================================================

def serialize_budget_policy(policy: BudgetPolicy) -> dict:
    """序列化 BudgetPolicy 为 JSON-safe dict。"""
    return {
        "token_limit": policy.token_limit,
        "allow_pause": policy.allow_pause,
    }


def deserialize_budget_policy(data: dict) -> BudgetPolicy:
    """从 JSON dict 反序列化 BudgetPolicy。"""
    return BudgetPolicy(
        token_limit=data.get("token_limit", 0),
        allow_pause=data.get("allow_pause", True),
    )
