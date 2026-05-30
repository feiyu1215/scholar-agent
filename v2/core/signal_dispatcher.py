"""
core/signal_dispatcher.py — Unified Signal Dispatcher

V3 Phase 0.5: 统一信号调度，取代 loop.py 中 4 个独立 check 叠加。

设计依据:
    - GODEL_AGENT_PLAN_V3 §3.3: Unified Signal Dispatcher
    - Anthropic "三元极简": 每轮最多 2 条 system message
    - 宪法层约束: SIGNAL_DISPATCHER_MAX_PER_TURN = 2

问题背景:
    长论文 40-60 轮场景中，若每轮 4-5 条 system message 叠加，
    Agent 注意力被严重稀释。Dispatcher 强制优先级调度 + 同源去重。

优先级:
    Priority 0 (doom): 始终通过，不受 MAX_PER_TURN 限制
    Priority 1 (high): budget/turn 限制警告
    Priority 2 (mid): cognitive nudge（认知催促）
    Priority 3 (low): reflection/suggestion

降级策略:
    GODEL_SIGNAL_DISPATCHER_ENABLED=0 → loop.py 保留原始 stacked checks 行为
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from core.godel_config import (
    SIGNAL_DISPATCHER_MAX_PER_TURN,
    SIGNAL_DEDUP_WINDOW,
)

logger = logging.getLogger(__name__)


# ==============================================================
# Data Structures
# ==============================================================

@dataclass
class HarnessSignal:
    """统一信号格式。

    Attributes:
        source: 信号来源标识（用于去重）
        priority: 优先级（0=doom, 1=high, 2=mid, 3=low）
        message: 注入给 Agent 的文本
        suppress_if: 如果这些 source 已经在本轮被选中，则抑制本信号
    """

    source: str
    priority: int
    message: str
    suppress_if: list[str] = field(default_factory=list)


# ==============================================================
# Dispatcher
# ==============================================================

class SignalDispatcher:
    """
    统一信号调度器。

    设计原则:
    - 每轮最多 MAX_SIGNALS_PER_TURN 条 system message（Priority 0 例外）
    - 高优先级抑制低优先级
    - 同源 DEDUP_WINDOW 轮内不重复

    使用方式:
        dispatcher.submit(HarnessSignal(source="turn", priority=1, message=...))
        dispatcher.submit(HarnessSignal(source="cognitive", priority=2, message=...))
        selected = dispatcher.dispatch(current_turn)
        for msg in selected:
            messages.append({"role": "system", "content": msg})
    """

    MAX_SIGNALS_PER_TURN: int = SIGNAL_DISPATCHER_MAX_PER_TURN
    DEDUP_WINDOW: int = SIGNAL_DEDUP_WINDOW

    def __init__(self) -> None:
        self._history: list[tuple[int, str]] = []  # (turn, source) 历史
        self._pending: list[HarnessSignal] = []    # 当前轮待选信号

    def submit(self, signal: HarnessSignal) -> None:
        """提交一个信号候选。

        在 dispatch() 调用前可以多次 submit。
        """
        self._pending.append(signal)

    def dispatch(self, current_turn: int) -> list[str]:
        """选择本轮实际注入的信号。

        选择策略:
        1. Priority 0 始终通过（安全不可协商）
        2. 去重: 检查 DEDUP_WINDOW 内同源
        3. 抑制: 检查 suppress_if 条件
        4. 截断: 保留 top MAX_SIGNALS_PER_TURN

        Args:
            current_turn: 当前循环轮次

        Returns:
            选中的 message 列表（按优先级排序）
        """
        # 按优先级排序（低数字 = 高优先级）
        self._pending.sort(key=lambda s: s.priority)

        selected: list[str] = []
        selected_sources: set[str] = set()
        non_doom_count = 0

        for signal in self._pending:
            # Priority 0: 始终通过
            if signal.priority == 0:
                selected.append(signal.message)
                self._history.append((current_turn, signal.source))
                selected_sources.add(signal.source)
                continue

            # 去重检查: DEDUP_WINDOW 内同源不重复
            recent_sources = {
                src for turn, src in self._history
                if current_turn - turn < self.DEDUP_WINDOW
            }
            if signal.source in recent_sources:
                continue

            # 抑制检查: suppress_if 中的源已被选中则跳过
            if signal.suppress_if and any(s in selected_sources for s in signal.suppress_if):
                continue

            # 数量限制
            if non_doom_count >= self.MAX_SIGNALS_PER_TURN:
                break

            selected.append(signal.message)
            self._history.append((current_turn, signal.source))
            selected_sources.add(signal.source)
            non_doom_count += 1

        self._pending.clear()

        # 清理过期历史（保留 DEDUP_WINDOW 内的记录即可）
        # 触发阈值 = DEDUP_WINDOW * 3，避免频繁裁剪
        if len(self._history) > self.DEDUP_WINDOW * 3:
            cutoff = current_turn - self.DEDUP_WINDOW
            self._history = [(t, s) for t, s in self._history if t >= cutoff]

        if selected:
            logger.debug(
                "[SignalDispatcher] Turn %d: dispatched %d signals (sources: %s)",
                current_turn, len(selected),
                ", ".join(selected_sources),
            )

        return selected

    def reset(self) -> None:
        """重置状态（新 session 时调用）。"""
        self._history.clear()
        self._pending.clear()
