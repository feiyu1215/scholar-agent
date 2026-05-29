"""
tests/test_event_bus.py — EventBus 单元测试
"""

import unittest

from core.event_bus import EventBus, Event, EventType, create_session_bus


class TestEventBus(unittest.TestCase):
    """测试 EventBus 核心功能"""

    def setUp(self):
        self.bus = EventBus(max_history=100)

    def test_publish_and_subscribe(self):
        """基本发布/订阅"""
        received = []
        self.bus.subscribe(EventType.TOOL_CALL_COMPLETED, lambda e: received.append(e))

        event = Event(type=EventType.TOOL_CALL_COMPLETED, payload={"tool": "read_section"})
        self.bus.publish(event)

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].payload["tool"], "read_section")

    def test_subscribe_type_isolation(self):
        """订阅特定类型时不收到其他类型的事件"""
        received = []
        self.bus.subscribe(EventType.TOOL_CALL_COMPLETED, lambda e: received.append(e))

        self.bus.publish(Event(type=EventType.TURN_STARTED))
        self.bus.publish(Event(type=EventType.TOOL_CALL_COMPLETED))

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].type, EventType.TOOL_CALL_COMPLETED)

    def test_subscribe_all(self):
        """全局订阅收到所有事件"""
        received = []
        self.bus.subscribe_all(lambda e: received.append(e))

        self.bus.publish(Event(type=EventType.TURN_STARTED))
        self.bus.publish(Event(type=EventType.TOOL_CALL_COMPLETED))
        self.bus.publish(Event(type=EventType.PHASE_ENTERED))

        self.assertEqual(len(received), 3)

    def test_priority_ordering(self):
        """高优先级订阅者先收到事件"""
        order = []
        self.bus.subscribe(EventType.TURN_STARTED, lambda e: order.append("low"), priority=200)
        self.bus.subscribe(EventType.TURN_STARTED, lambda e: order.append("high"), priority=10)
        self.bus.subscribe(EventType.TURN_STARTED, lambda e: order.append("mid"), priority=100)

        self.bus.publish(Event(type=EventType.TURN_STARTED))
        self.assertEqual(order, ["high", "mid", "low"])

    def test_unsubscribe(self):
        """取消订阅后不再收到事件"""
        received = []
        sub = self.bus.subscribe(EventType.TURN_STARTED, lambda e: received.append(e))

        self.bus.publish(Event(type=EventType.TURN_STARTED))
        self.assertEqual(len(received), 1)

        self.bus.unsubscribe(sub)
        self.bus.publish(Event(type=EventType.TURN_STARTED))
        self.assertEqual(len(received), 1)  # 没有新增

    def test_emit_convenience(self):
        """emit 便捷方法创建并发布事件"""
        received = []
        self.bus.subscribe(EventType.FINDING_ADDED, lambda e: received.append(e))

        event = self.bus.emit(EventType.FINDING_ADDED, source="test", turn=3, finding="x")
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].payload["finding"], "x")
        self.assertEqual(event.source, "test")
        self.assertEqual(event.turn, 3)

    def test_history_recording(self):
        """事件应自动记录到历史"""
        for i in range(5):
            self.bus.emit(EventType.TURN_STARTED, turn=i)

        history = self.bus.get_history()
        self.assertEqual(len(history), 5)

    def test_history_max_limit(self):
        """历史超过上限时应裁剪"""
        bus = EventBus(max_history=20)
        for i in range(30):
            bus.emit(EventType.TURN_STARTED, turn=i)

        # 裁剪了 10% = 2 条，剩余 <= 20
        self.assertLessEqual(bus.count(), 20)

    def test_replay_with_filter(self):
        """重放可以按类型过滤"""
        self.bus.emit(EventType.TURN_STARTED, turn=1)
        self.bus.emit(EventType.TOOL_CALL_COMPLETED, turn=2)
        self.bus.emit(EventType.TURN_STARTED, turn=3)

        replayed = self.bus.replay(filter_type=EventType.TURN_STARTED)
        self.assertEqual(len(replayed), 2)

    def test_replay_since_turn(self):
        """重放可以指定起始轮次"""
        self.bus.emit(EventType.TURN_STARTED, turn=1)
        self.bus.emit(EventType.TURN_STARTED, turn=3)
        self.bus.emit(EventType.TURN_STARTED, turn=5)

        replayed = self.bus.replay(since_turn=3)
        self.assertEqual(len(replayed), 2)

    def test_pause_and_resume(self):
        """暂停时不通知订阅者，但仍记录历史"""
        received = []
        self.bus.subscribe(EventType.TURN_STARTED, lambda e: received.append(e))

        self.bus.pause()
        self.bus.emit(EventType.TURN_STARTED, turn=1)
        self.assertEqual(len(received), 0)
        self.assertEqual(self.bus.count(), 1)  # 历史仍记录

        self.bus.resume()
        self.bus.emit(EventType.TURN_STARTED, turn=2)
        self.assertEqual(len(received), 1)

    def test_handler_exception_isolation(self):
        """订阅者异常不影响其他订阅者"""
        received = []

        def bad_handler(e):
            raise RuntimeError("oops")

        def good_handler(e):
            received.append(e)

        self.bus.subscribe(EventType.TURN_STARTED, bad_handler, priority=10)
        self.bus.subscribe(EventType.TURN_STARTED, good_handler, priority=100)

        self.bus.emit(EventType.TURN_STARTED)
        self.assertEqual(len(received), 1)

    def test_count_by_type(self):
        """按类型统计事件数量"""
        self.bus.emit(EventType.TURN_STARTED)
        self.bus.emit(EventType.TURN_STARTED)
        self.bus.emit(EventType.TOOL_CALL_COMPLETED)

        self.assertEqual(self.bus.count(EventType.TURN_STARTED), 2)
        self.assertEqual(self.bus.count(EventType.TOOL_CALL_COMPLETED), 1)
        self.assertEqual(self.bus.count(), 3)

    def test_reset(self):
        """reset 清空一切"""
        self.bus.subscribe(EventType.TURN_STARTED, lambda e: None)
        self.bus.emit(EventType.TURN_STARTED)

        self.bus.reset()
        self.assertEqual(self.bus.count(), 0)
        self.assertEqual(len(self.bus._subscriptions), 0)

    def test_create_session_bus(self):
        """工厂函数正常工作"""
        bus = create_session_bus(max_history=50)
        self.assertIsInstance(bus, EventBus)
        bus.emit(EventType.LOOP_STARTED)
        self.assertEqual(bus.count(), 1)


class TestEventImmutability(unittest.TestCase):
    """测试 Event 的不可变性"""

    def test_frozen(self):
        """Event 是 frozen dataclass，不可修改"""
        event = Event(type=EventType.TURN_STARTED, payload={"x": 1}, turn=5)
        with self.assertRaises(Exception):
            event.turn = 10  # type: ignore

    def test_event_id_auto_generated(self):
        """event_id 自动生成"""
        event = Event(type=EventType.TURN_STARTED)
        self.assertTrue(len(event.event_id) > 0)


if __name__ == "__main__":
    unittest.main()
