"""
core/middleware.py — 中间件框架 (Infrastructure)

提供可插拔的 hook 机制，让功能模块以非侵入式的方式介入认知循环。
避免 loop.py 无限膨胀 —— 每个功能（doom loop 检测、token 监控、事件记录等）
注册为独立的 Middleware，由框架在适当时机调用。

Hook 点（生命周期）：
  - before_llm_call: LLM 调用前（可修改 messages）
  - after_llm_call: LLM 响应后（可读取 response）
  - before_tool_call: 工具执行前（可拦截/修改参数）
  - after_tool_call: 工具执行后（可记录结果/注入恢复）
  - on_turn_end: 一轮结束时（用于统计/状态更新）
  - on_loop_end: 整个循环结束时（用于清理/持久化）

设计原则：
  - 中间件之间无直接依赖，通过 EventBus 通信（当 EventBus 就绪时）
  - 执行顺序由 priority 控制（数字小 = 先执行）
  - 中间件可以请求 "注入消息" 到对话历史（通过返回值）
  - 安全兜底中间件（DoomLoop、TokenBudget）不可被用户禁用
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Protocol, runtime_checkable

from core.state import WorkspaceState


# ==============================================================
# 类型定义
# ==============================================================

class HookPoint(Enum):
    """中间件可以注册的 hook 点"""
    BEFORE_LLM_CALL = "before_llm_call"
    AFTER_LLM_CALL = "after_llm_call"
    BEFORE_TOOL_CALL = "before_tool_call"
    AFTER_TOOL_CALL = "after_tool_call"
    ON_TURN_END = "on_turn_end"
    ON_LOOP_END = "on_loop_end"


@dataclass
class MiddlewareContext:
    """传递给中间件 hook 的上下文对象"""
    state: WorkspaceState
    messages: list[dict]           # 当前对话历史（可读可写）
    current_turn: int = 0
    current_phase: str = ""

    # Hook 特定数据（根据 hook 点不同，部分字段可能为 None）
    llm_response: Any = None       # after_llm_call 时填充
    tool_name: str = ""            # before/after_tool_call 时填充
    tool_params: dict = field(default_factory=dict)
    tool_result: Any = None        # after_tool_call 时填充
    tool_success: bool = True      # after_tool_call 时填充


@dataclass
class MiddlewareResult:
    """中间件 hook 的返回值"""
    # 请求注入到对话历史的消息（列表，可多条）
    inject_messages: list[dict] = field(default_factory=list)
    # 是否阻止后续中间件执行（短路）
    halt_chain: bool = False
    # 是否请求终止当前循环
    request_loop_stop: bool = False
    stop_reason: str = ""
    # 修改后的工具参数（仅 before_tool_call 时有效）
    modified_params: dict | None = None


# ==============================================================
# 中间件基类
# ==============================================================

class MiddlewareBase(ABC):
    """所有中间件的抽象基类。

    子类实现需要的 hook 方法，无需实现全部。
    通过 `hooks` 属性声明自己注册到哪些 hook 点。
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """中间件名称（唯一标识）"""
        ...

    @property
    def priority(self) -> int:
        """执行优先级。数字越小越先执行。默认 100。"""
        return 100

    @property
    def is_safety_critical(self) -> bool:
        """是否为安全兜底中间件（不可被用户禁用）。"""
        return False

    @property
    @abstractmethod
    def hooks(self) -> list[HookPoint]:
        """声明此中间件注册到哪些 hook 点。"""
        ...

    # --- Hook 方法（子类按需 override） ---

    def before_llm_call(self, ctx: MiddlewareContext) -> MiddlewareResult:
        return MiddlewareResult()

    def after_llm_call(self, ctx: MiddlewareContext) -> MiddlewareResult:
        return MiddlewareResult()

    def before_tool_call(self, ctx: MiddlewareContext) -> MiddlewareResult:
        return MiddlewareResult()

    def after_tool_call(self, ctx: MiddlewareContext) -> MiddlewareResult:
        return MiddlewareResult()

    def on_turn_end(self, ctx: MiddlewareContext) -> MiddlewareResult:
        return MiddlewareResult()

    def on_loop_end(self, ctx: MiddlewareContext) -> MiddlewareResult:
        return MiddlewareResult()


# ==============================================================
# 中间件管理器
# ==============================================================

class MiddlewareManager:
    """管理所有注册的中间件，在适当 hook 点调用它们。

    使用方式：
        manager = MiddlewareManager()
        manager.register(DoomLoopMiddleware(...))
        manager.register(TokenBudgetMiddleware(...))

        # 在 loop.py 中：
        result = manager.run_hook(HookPoint.AFTER_TOOL_CALL, ctx)
    """

    def __init__(self):
        self._middlewares: list[MiddlewareBase] = []
        self._disabled: set[str] = set()  # 被用户禁用的中间件名称

    def register(self, middleware: MiddlewareBase) -> None:
        """注册一个中间件。"""
        # 检查名称唯一性
        existing_names = {m.name for m in self._middlewares}
        if middleware.name in existing_names:
            raise ValueError(f"Middleware '{middleware.name}' already registered")
        self._middlewares.append(middleware)
        # 按 priority 排序
        self._middlewares.sort(key=lambda m: m.priority)

    def unregister(self, name: str) -> bool:
        """注销一个中间件。安全兜底中间件不可注销。"""
        for m in self._middlewares:
            if m.name == name:
                if m.is_safety_critical:
                    return False
                self._middlewares.remove(m)
                return True
        return False

    def disable(self, name: str) -> bool:
        """禁用一个中间件（不删除，只是不执行）。安全兜底不可禁用。"""
        for m in self._middlewares:
            if m.name == name:
                if m.is_safety_critical:
                    return False
                self._disabled.add(name)
                return True
        return False

    def enable(self, name: str) -> None:
        """重新启用一个被禁用的中间件。"""
        self._disabled.discard(name)

    def run_hook(self, hook: HookPoint, ctx: MiddlewareContext) -> MiddlewareResult:
        """执行指定 hook 点上所有注册的中间件。

        Returns:
            合并后的 MiddlewareResult（注入消息合并，stop/halt 取 OR）
        """
        combined = MiddlewareResult()

        for mw in self._middlewares:
            # 跳过未注册此 hook 的
            if hook not in mw.hooks:
                continue
            # 跳过被禁用的（安全兜底除外）
            if mw.name in self._disabled and not mw.is_safety_critical:
                continue

            # 调用对应 hook
            result = self._call_hook(mw, hook, ctx)

            # 合并结果
            combined.inject_messages.extend(result.inject_messages)
            if result.halt_chain:
                combined.halt_chain = True
            if result.request_loop_stop:
                combined.request_loop_stop = True
                combined.stop_reason = result.stop_reason
            if result.modified_params is not None:
                combined.modified_params = result.modified_params
                # 更新 ctx 供后续中间件使用
                ctx.tool_params = result.modified_params

            # 短路
            if combined.halt_chain:
                break

        return combined

    @property
    def registered_names(self) -> list[str]:
        """已注册的中间件名称列表。"""
        return [m.name for m in self._middlewares]

    # ----------------------------------------------------------
    # 内部
    # ----------------------------------------------------------

    @staticmethod
    def _call_hook(
        mw: MiddlewareBase, hook: HookPoint, ctx: MiddlewareContext
    ) -> MiddlewareResult:
        """分发到对应的 hook 方法。"""
        handler = {
            HookPoint.BEFORE_LLM_CALL: mw.before_llm_call,
            HookPoint.AFTER_LLM_CALL: mw.after_llm_call,
            HookPoint.BEFORE_TOOL_CALL: mw.before_tool_call,
            HookPoint.AFTER_TOOL_CALL: mw.after_tool_call,
            HookPoint.ON_TURN_END: mw.on_turn_end,
            HookPoint.ON_LOOP_END: mw.on_loop_end,
        }.get(hook)

        if handler is None:
            return MiddlewareResult()

        try:
            return handler(ctx)
        except Exception:
            # 中间件异常不应影响主循环
            return MiddlewareResult()


# ==============================================================
# 内置中间件：DoomLoopMiddleware (Phase 1 集成)
# ==============================================================

class DoomLoopMiddleware(MiddlewareBase):
    """将 ToolCallPatternDetector 集成到中间件框架中。

    这是 Phase 1 的核心集成点：
    - after_tool_call: 记录调用 + 检测模式
    - on_turn_end: 检查是否脱离了循环
    """

    def __init__(self, available_tools: list[str] | None = None):
        from core.loop_guard import ToolCallPatternDetector
        self._detector = ToolCallPatternDetector()
        self._available_tools = available_tools or []

    @property
    def name(self) -> str:
        return "doom_loop_pattern"

    @property
    def priority(self) -> int:
        return 10  # 高优先级，安全兜底

    @property
    def is_safety_critical(self) -> bool:
        return True

    @property
    def hooks(self) -> list[HookPoint]:
        return [HookPoint.AFTER_TOOL_CALL, HookPoint.ON_TURN_END]

    def after_tool_call(self, ctx: MiddlewareContext) -> MiddlewareResult:
        """工具调用后记录并检测模式。"""
        self._detector.record_call(
            tool_name=ctx.tool_name,
            params=ctx.tool_params,
            result=ctx.tool_result,
            success=ctx.tool_success,
            turn=ctx.current_turn,
        )

        pattern = self._detector.detect()
        if pattern is None:
            return MiddlewareResult()

        # 检测到循环模式 → 生成恢复动作
        recovery = self._detector.get_recovery_action(
            pattern=pattern,
            current_turn=ctx.current_turn,
            current_phase=ctx.current_phase,
            available_tools=self._available_tools,
        )

        # 死亡螺旋升级到终止级别
        if recovery.escalation_level >= self._detector.max_escalation:
            return MiddlewareResult(
                inject_messages=[{
                    "role": "system",
                    "content": recovery.message,
                }],
                request_loop_stop=True,
                stop_reason=f"doom_loop_pattern:{pattern.value}:escalation_{recovery.escalation_level}",
            )

        # 正常恢复：注入恢复消息
        return MiddlewareResult(
            inject_messages=[{
                "role": "system",
                "content": recovery.message,
            }],
        )

    def on_turn_end(self, ctx: MiddlewareContext) -> MiddlewareResult:
        """轮次结束时无额外操作（状态已在 after_tool_call 中更新）。"""
        return MiddlewareResult()

    @property
    def detector(self) -> "ToolCallPatternDetector":
        """暴露检测器实例，供测试使用。"""
        from core.loop_guard import ToolCallPatternDetector
        return self._detector
