"""
tests/test_middleware.py — Middleware 框架单元测试
"""

import unittest

from core.middleware import (
    MiddlewareBase,
    MiddlewareManager,
    MiddlewareContext,
    MiddlewareResult,
    HookPoint,
    DoomLoopMiddleware,
)
from core.state import WorkspaceState


class DummyMiddleware(MiddlewareBase):
    """测试用中间件"""

    def __init__(self, mw_name: str = "dummy", mw_priority: int = 100):
        self._name = mw_name
        self._priority = mw_priority
        self.calls: list[str] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def priority(self) -> int:
        return self._priority

    @property
    def hooks(self) -> list[HookPoint]:
        return [HookPoint.AFTER_TOOL_CALL, HookPoint.ON_TURN_END]

    def after_tool_call(self, ctx: MiddlewareContext) -> MiddlewareResult:
        self.calls.append(f"after_tool_call:{ctx.tool_name}")
        return MiddlewareResult()

    def on_turn_end(self, ctx: MiddlewareContext) -> MiddlewareResult:
        self.calls.append("on_turn_end")
        return MiddlewareResult()


class SafetyMiddleware(MiddlewareBase):
    """安全兜底中间件（不可禁用）"""

    @property
    def name(self) -> str:
        return "safety"

    @property
    def priority(self) -> int:
        return 1

    @property
    def is_safety_critical(self) -> bool:
        return True

    @property
    def hooks(self) -> list[HookPoint]:
        return [HookPoint.AFTER_TOOL_CALL]

    def after_tool_call(self, ctx: MiddlewareContext) -> MiddlewareResult:
        return MiddlewareResult(
            inject_messages=[{"role": "system", "content": "safety check"}]
        )


class TestMiddlewareManager(unittest.TestCase):
    """测试 MiddlewareManager 核心功能"""

    def test_register_and_run(self):
        """注册中间件并在 hook 点运行"""
        mgr = MiddlewareManager()
        mw = DummyMiddleware()
        mgr.register(mw)

        ctx = MiddlewareContext(
            state=WorkspaceState(),
            messages=[],
            tool_name="read_section",
        )

        result = mgr.run_hook(HookPoint.AFTER_TOOL_CALL, ctx)
        self.assertEqual(mw.calls, ["after_tool_call:read_section"])
        self.assertIsInstance(result, MiddlewareResult)

    def test_priority_ordering(self):
        """中间件按 priority 顺序执行"""
        mgr = MiddlewareManager()
        calls_order = []

        class OrderTracker(MiddlewareBase):
            def __init__(self, n, p):
                self._n = n
                self._p = p

            @property
            def name(self):
                return self._n

            @property
            def priority(self):
                return self._p

            @property
            def hooks(self):
                return [HookPoint.ON_TURN_END]

            def on_turn_end(self, ctx):
                calls_order.append(self._n)
                return MiddlewareResult()

        mgr.register(OrderTracker("c", 300))
        mgr.register(OrderTracker("a", 100))
        mgr.register(OrderTracker("b", 200))

        ctx = MiddlewareContext(state=WorkspaceState(), messages=[])
        mgr.run_hook(HookPoint.ON_TURN_END, ctx)

        self.assertEqual(calls_order, ["a", "b", "c"])

    def test_duplicate_name_raises(self):
        """重复名称应抛出异常"""
        mgr = MiddlewareManager()
        mgr.register(DummyMiddleware("dup"))
        with self.assertRaises(ValueError):
            mgr.register(DummyMiddleware("dup"))

    def test_disable_and_enable(self):
        """禁用后不执行，启用后恢复"""
        mgr = MiddlewareManager()
        mw = DummyMiddleware("test_mw")
        mgr.register(mw)

        ctx = MiddlewareContext(state=WorkspaceState(), messages=[], tool_name="x")

        mgr.disable("test_mw")
        mgr.run_hook(HookPoint.AFTER_TOOL_CALL, ctx)
        self.assertEqual(mw.calls, [])

        mgr.enable("test_mw")
        mgr.run_hook(HookPoint.AFTER_TOOL_CALL, ctx)
        self.assertEqual(mw.calls, ["after_tool_call:x"])

    def test_safety_critical_cannot_disable(self):
        """安全兜底中间件不可禁用"""
        mgr = MiddlewareManager()
        mgr.register(SafetyMiddleware())

        result = mgr.disable("safety")
        self.assertFalse(result)

    def test_safety_critical_cannot_unregister(self):
        """安全兜底中间件不可注销"""
        mgr = MiddlewareManager()
        mgr.register(SafetyMiddleware())

        result = mgr.unregister("safety")
        self.assertFalse(result)

    def test_inject_messages_merged(self):
        """多个中间件的注入消息应合并"""
        mgr = MiddlewareManager()

        class Injector(MiddlewareBase):
            def __init__(self, n, msg):
                self._n = n
                self._msg = msg

            @property
            def name(self):
                return self._n

            @property
            def hooks(self):
                return [HookPoint.AFTER_TOOL_CALL]

            def after_tool_call(self, ctx):
                return MiddlewareResult(
                    inject_messages=[{"role": "system", "content": self._msg}]
                )

        mgr.register(Injector("a", "msg_a"))
        mgr.register(Injector("b", "msg_b"))

        ctx = MiddlewareContext(state=WorkspaceState(), messages=[])
        result = mgr.run_hook(HookPoint.AFTER_TOOL_CALL, ctx)

        self.assertEqual(len(result.inject_messages), 2)
        contents = [m["content"] for m in result.inject_messages]
        self.assertIn("msg_a", contents)
        self.assertIn("msg_b", contents)

    def test_halt_chain(self):
        """halt_chain 应阻止后续中间件执行"""
        mgr = MiddlewareManager()
        calls = []

        class Halter(MiddlewareBase):
            @property
            def name(self):
                return "halter"

            @property
            def priority(self):
                return 1

            @property
            def hooks(self):
                return [HookPoint.AFTER_TOOL_CALL]

            def after_tool_call(self, ctx):
                calls.append("halter")
                return MiddlewareResult(halt_chain=True)

        class Follower(MiddlewareBase):
            @property
            def name(self):
                return "follower"

            @property
            def priority(self):
                return 100

            @property
            def hooks(self):
                return [HookPoint.AFTER_TOOL_CALL]

            def after_tool_call(self, ctx):
                calls.append("follower")
                return MiddlewareResult()

        mgr.register(Halter())
        mgr.register(Follower())

        ctx = MiddlewareContext(state=WorkspaceState(), messages=[])
        mgr.run_hook(HookPoint.AFTER_TOOL_CALL, ctx)

        self.assertEqual(calls, ["halter"])  # follower 不应被调用


class TestDoomLoopMiddleware(unittest.TestCase):
    """测试 DoomLoopMiddleware 集成"""

    def test_records_and_detects(self):
        """正常记录调用且在循环时检测到"""
        mw = DoomLoopMiddleware(available_tools=["search_literature", "reflect_and_plan"])
        ctx = MiddlewareContext(
            state=WorkspaceState(),
            messages=[],
            current_turn=1,
            tool_name="read_section",
            tool_params={"section": "intro"},
            tool_result="some content",
            tool_success=True,
        )

        # 不到窗口大小 → 无反应
        for i in range(4):
            result = mw.after_tool_call(ctx)
            self.assertEqual(result.inject_messages, [])

        # 第 5 次 → 触发检测
        result = mw.after_tool_call(ctx)
        self.assertTrue(len(result.inject_messages) > 0)
        self.assertIn("循环检测", result.inject_messages[0]["content"])

    def test_no_false_positive_on_varied_calls(self):
        """不同的工具调用不应误报"""
        mw = DoomLoopMiddleware()
        state = WorkspaceState()

        tools = ["read_section", "update_findings", "search_literature",
                 "reflect_and_plan", "read_section"]
        for i, tool in enumerate(tools):
            ctx = MiddlewareContext(
                state=state,
                messages=[],
                current_turn=i,
                tool_name=tool,
                tool_params={"p": i},
                tool_result=f"result_{i}",
                tool_success=True,
            )
            result = mw.after_tool_call(ctx)
            self.assertEqual(result.inject_messages, [])

    def test_is_safety_critical(self):
        """DoomLoopMiddleware 应标记为安全兜底"""
        mw = DoomLoopMiddleware()
        self.assertTrue(mw.is_safety_critical)


if __name__ == "__main__":
    unittest.main()
