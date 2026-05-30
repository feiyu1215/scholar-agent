"""
core/event_bus.py — 结构化事件总线 (Infrastructure)

提供认知循环内所有模块间的松耦合通信机制。
与 stream_events.py 的关系：stream_events 是面向外部消费者的简单流式推送，
EventBus 是面向内部模块间通信的完整发布/订阅系统。

核心特性：
  - 类型化事件（每种事件有明确的 payload schema）
  - 订阅/发布模式（多个订阅者可以监听同一事件）
  - 事件重放（支持 audit/debug：回放整个 session 的事件序列）
  - 优先级订阅（高优先级订阅者先收到事件）
  - 同步执行（当前为单线程设计，事件处理同步完成）

设计原则：
  - EventBus 不拥有任何业务逻辑，只负责路由
  - 事件是不可变的（frozen dataclass）
  - 订阅者异常不影响其他订阅者（隔离 + 日志）
  - 事件历史自动记录，支持上限裁剪（防无限膨胀）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Protocol, runtime_checkable
from collections import defaultdict
import time

logger = logging.getLogger(__name__)


# ==============================================================
# 事件类型枚举
# ==============================================================

class EventType(Enum):
    """系统内所有结构化事件的类型"""

    # --- 循环生命周期 ---
    LOOP_STARTED = "loop.started"
    LOOP_ENDED = "loop.ended"
    TURN_STARTED = "turn.started"
    TURN_ENDED = "turn.ended"

    # --- LLM 交互 ---
    LLM_CALL_STARTED = "llm.call_started"
    LLM_CALL_COMPLETED = "llm.call_completed"
    LLM_CALL_FAILED = "llm.call_failed"

    # --- 工具执行 ---
    TOOL_CALL_STARTED = "tool.call_started"
    TOOL_CALL_COMPLETED = "tool.call_completed"
    TOOL_CALL_FAILED = "tool.call_failed"

    # --- Phase FSM ---
    PHASE_ENTERED = "phase.entered"
    PHASE_EXITED = "phase.exited"
    PHASE_TRANSITION = "phase.transition"

    # --- 认知事件 ---
    FINDING_ADDED = "cognition.finding_added"
    FINDING_UPDATED = "cognition.finding_updated"
    HYPOTHESIS_CREATED = "cognition.hypothesis_created"
    HYPOTHESIS_RESOLVED = "cognition.hypothesis_resolved"
    REFLECTION_TRIGGERED = "cognition.reflection_triggered"
    REFLECTION_COMPLETED = "cognition.reflection_completed"

    # --- 安全/约束 ---
    DOOM_LOOP_DETECTED = "safety.doom_loop_detected"
    DOOM_LOOP_RECOVERED = "safety.doom_loop_recovered"
    TOKEN_BUDGET_WARNING = "safety.token_budget_warning"
    NUDGE_INJECTED = "safety.nudge_injected"

    # --- Memory ---
    MEMORY_DISTILLED = "memory.distilled"
    MEMORY_RECALLED = "memory.recalled"
    MEMORY_GC = "memory.gc"

    # --- Middleware ---
    MIDDLEWARE_FIRED = "middleware.fired"

    # --- 对抗训练 (Phase 7) ---
    ARENA_MATCH_STARTED = "arena.match_started"
    ARENA_MATCH_COMPLETED = "arena.match_completed"
    ARENA_RED_CHALLENGE = "arena.red_challenge"
    ARENA_BLUE_RESPONSE = "arena.blue_response"
    ARENA_JUDGE_VERDICT = "arena.judge_verdict"
    ARENA_ELO_UPDATED = "arena.elo_updated"
    ARENA_SEASON_STARTED = "arena.season_started"
    ARENA_SEASON_ENDED = "arena.season_ended"
    ARENA_BALANCE_ADJUSTED = "arena.balance_adjusted"
    TRAINING_SESSION_STARTED = "training.session_started"
    TRAINING_SESSION_COMPLETED = "training.session_completed"
    TRAINING_WEAKNESS_DETECTED = "training.weakness_detected"
    TRAINING_WEAKNESS_RESOLVED = "training.weakness_resolved"
    TRAINING_CURRICULUM_UPDATED = "training.curriculum_updated"
    TRAINING_CONVERGENCE_DETECTED = "training.convergence_detected"
    TRAINING_DIVERGENCE_DETECTED = "training.divergence_detected"

    # --- 自定义扩展 ---
    CUSTOM = "custom"


# ==============================================================
# 事件数据
# ==============================================================

@dataclass(frozen=True)
class Event:
    """不可变的事件对象。

    Attributes:
        type: 事件类型
        payload: 事件携带的数据（自由 dict，由发布者决定内容）
        source: 事件来源模块名称
        turn: 发生时的循环轮次
        timestamp: 事件生成时间戳（秒级精度）
        event_id: 唯一标识（用于去重/追踪）
    """
    type: EventType
    payload: dict = field(default_factory=dict)
    source: str = ""
    turn: int = 0
    timestamp: float = field(default_factory=time.time)
    event_id: str = ""

    def __post_init__(self):
        # frozen dataclass 需要用 object.__setattr__
        if not self.event_id:
            object.__setattr__(
                self, 'event_id',
                f"{self.type.value}_{self.turn}_{id(self)}"
            )


# ==============================================================
# 订阅者协议
# ==============================================================

# 回调签名
EventHandler = Callable[[Event], None]


@dataclass
class Subscription:
    """一个订阅记录"""
    handler: EventHandler
    event_type: EventType | None  # None = 订阅所有事件
    priority: int = 100            # 数字小 = 先执行
    subscriber_name: str = ""      # 用于 debug


# ==============================================================
# EventBus 核心
# ==============================================================

class EventBus:
    """事件总线：发布/订阅/重放。

    使用方式：
        bus = EventBus()

        # 订阅特定事件
        bus.subscribe(EventType.TOOL_CALL_COMPLETED, handler, priority=10)

        # 订阅所有事件（如 logger）
        bus.subscribe_all(audit_logger)

        # 发布事件
        bus.publish(Event(type=EventType.TOOL_CALL_COMPLETED, payload={...}))

        # 重放历史
        bus.replay(filter_type=EventType.DOOM_LOOP_DETECTED)
    """

    def __init__(self, max_history: int = 5000):
        """
        Args:
            max_history: 事件历史最大保留条数（FIFO 裁剪）
        """
        self._subscriptions: dict[EventType, list[Subscription]] = defaultdict(list)
        self._global_subscriptions: list[Subscription] = []  # 订阅所有事件的
        self._history: list[Event] = []
        self._max_history = max_history
        self._paused: bool = False

    # ----------------------------------------------------------
    # 订阅
    # ----------------------------------------------------------

    def subscribe(
        self,
        event_type: EventType,
        handler: EventHandler,
        priority: int = 100,
        subscriber_name: str = "",
    ) -> Subscription:
        """订阅特定类型的事件。

        Args:
            event_type: 要订阅的事件类型
            handler: 事件处理回调
            priority: 优先级（小 = 先执行）
            subscriber_name: 订阅者名称（用于 debug）

        Returns:
            Subscription 对象（可用于取消订阅）
        """
        sub = Subscription(
            handler=handler,
            event_type=event_type,
            priority=priority,
            subscriber_name=subscriber_name,
        )
        self._subscriptions[event_type].append(sub)
        self._subscriptions[event_type].sort(key=lambda s: s.priority)
        return sub

    def subscribe_all(
        self,
        handler: EventHandler,
        priority: int = 100,
        subscriber_name: str = "",
    ) -> Subscription:
        """订阅所有事件（适用于日志/审计）。"""
        sub = Subscription(
            handler=handler,
            event_type=None,
            priority=priority,
            subscriber_name=subscriber_name,
        )
        self._global_subscriptions.append(sub)
        self._global_subscriptions.sort(key=lambda s: s.priority)
        return sub

    def unsubscribe(self, subscription: Subscription) -> bool:
        """取消订阅。"""
        if subscription.event_type is None:
            if subscription in self._global_subscriptions:
                self._global_subscriptions.remove(subscription)
                return True
        else:
            subs = self._subscriptions.get(subscription.event_type, [])
            if subscription in subs:
                subs.remove(subscription)
                return True
        return False

    # ----------------------------------------------------------
    # 发布
    # ----------------------------------------------------------

    def publish(self, event: Event) -> None:
        """发布一个事件。所有匹配的订阅者将同步收到通知。

        订阅者异常会被捕获并记录，不影响其他订阅者。
        """
        if self._paused:
            # 暂停时仍记录历史，但不通知订阅者
            self._record(event)
            return

        self._record(event)

        # 通知特定类型的订阅者
        for sub in self._subscriptions.get(event.type, []):
            self._safe_call(sub, event)

        # 通知全局订阅者
        for sub in self._global_subscriptions:
            self._safe_call(sub, event)

    def emit(self, event_type: EventType, source: str = "", turn: int = 0, **payload) -> Event:
        """便捷方法：创建并发布事件。

        Args:
            event_type: 事件类型
            source: 来源模块
            turn: 当前轮次
            **payload: 事件数据

        Returns:
            创建的 Event 对象
        """
        event = Event(
            type=event_type,
            payload=payload,
            source=source,
            turn=turn,
        )
        self.publish(event)
        return event

    # ----------------------------------------------------------
    # 重放与查询
    # ----------------------------------------------------------

    def replay(
        self,
        filter_type: EventType | None = None,
        filter_source: str | None = None,
        since_turn: int = 0,
        handler: EventHandler | None = None,
    ) -> list[Event]:
        """重放历史事件。

        如果提供 handler，对每个匹配事件调用 handler。
        无论是否提供 handler，都返回匹配的事件列表。

        Args:
            filter_type: 只重放特定类型
            filter_source: 只重放特定来源
            since_turn: 只重放指定轮次之后的事件
            handler: 重放处理器

        Returns:
            匹配的事件列表
        """
        matched = []
        for event in self._history:
            if filter_type and event.type != filter_type:
                continue
            if filter_source and event.source != filter_source:
                continue
            if event.turn < since_turn:
                continue
            matched.append(event)
            if handler:
                self._safe_call_raw(handler, event)
        return matched

    def get_history(
        self,
        last_n: int | None = None,
        event_type: EventType | None = None,
    ) -> list[Event]:
        """获取事件历史。

        Args:
            last_n: 只返回最近 N 条
            event_type: 只返回特定类型

        Returns:
            匹配的事件列表
        """
        events = self._history
        if event_type:
            events = [e for e in events if e.type == event_type]
        if last_n:
            events = events[-last_n:]
        return events

    def count(self, event_type: EventType | None = None) -> int:
        """统计事件数量。"""
        if event_type is None:
            return len(self._history)
        return sum(1 for e in self._history if e.type == event_type)

    # ----------------------------------------------------------
    # 控制
    # ----------------------------------------------------------

    def pause(self) -> None:
        """暂停事件通知（仍记录历史）。用于批量操作时避免频繁通知。"""
        self._paused = True

    def resume(self) -> None:
        """恢复事件通知。"""
        self._paused = False

    def clear_history(self) -> None:
        """清空事件历史。"""
        self._history.clear()

    def reset(self) -> None:
        """完全重置（清空历史 + 所有订阅）。"""
        self._history.clear()
        self._subscriptions.clear()
        self._global_subscriptions.clear()
        self._paused = False

    # ----------------------------------------------------------
    # 内部
    # ----------------------------------------------------------

    def _record(self, event: Event) -> None:
        """记录事件到历史，执行 FIFO 裁剪。"""
        self._history.append(event)
        if len(self._history) > self._max_history:
            # 裁剪最旧的 10%
            trim_count = self._max_history // 10
            self._history = self._history[trim_count:]

    @staticmethod
    def _safe_call(sub: Subscription, event: Event) -> None:
        """安全调用订阅者（异常隔离）。"""
        try:
            sub.handler(event)
        except Exception:
            logger.warning(
                "[EventBus] subscriber %r raised on event %s",
                sub.handler, event.type.value,
                exc_info=True,
            )

    @staticmethod
    def _safe_call_raw(handler: EventHandler, event: Event) -> None:
        """安全调用裸 handler。"""
        try:
            handler(event)
        except Exception:
            logger.warning(
                "[EventBus] raw handler %r raised on event %s",
                handler, event.type.value,
                exc_info=True,
            )


# ==============================================================
# 便捷工厂：预配置的 EventBus 实例
# ==============================================================

def create_session_bus(max_history: int = 5000) -> EventBus:
    """创建一个 session 级别的 EventBus。

    每个审稿 session 一个 bus，session 结束时随之销毁。
    """
    return EventBus(max_history=max_history)
