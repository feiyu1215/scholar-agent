"""
tests/test_loop_guard.py — Phase 1 ToolCallPatternDetector 单元测试 (MVP + Complete)

覆盖：
  - 三种模式检测 (EXACT_REPEAT, PARAM_DRIFT, OSCILLATION)
  - 策略注册表 (RecoveryRegistry)
  - 上下文感知恢复 (phase-aware)
  - 恢复效果追踪 (RecoveryTracker)
  - 死亡螺旋升级机制
"""

import unittest
import time

from core.loop_guard import (
    ToolCallPatternDetector,
    ToolCallRecord,
    LoopPattern,
    RecoveryAction,
    RecoveryContext,
    RecoveryRegistry,
    RecoveryStrategyHandler,
    RecoveryTracker,
    RecoveryOutcome,
    RecoveryRecord,
    DefaultExactRepeatHandler,
    DefaultParamDriftHandler,
    DefaultOscillationHandler,
)


# ==============================================================
# MVP 层测试：模式检测
# ==============================================================

class TestExactRepeatDetection(unittest.TestCase):
    """测试 EXACT_REPEAT 模式检测"""

    def test_no_detection_below_window(self):
        """窗口未满时不应检测到模式"""
        detector = ToolCallPatternDetector(window_size=5)
        for i in range(3):
            detector.record_call("read_section", {"section": "intro"}, "some result")
        self.assertIsNone(detector.detect())

    def test_detect_exact_repeat_full_window(self):
        """完全相同的调用重复 window_size 次应检测到"""
        detector = ToolCallPatternDetector(window_size=5)
        for i in range(5):
            detector.record_call("read_section", {"section": "intro"}, "same result")
        self.assertEqual(detector.detect(), LoopPattern.EXACT_REPEAT)

    def test_detect_exact_repeat_3_consecutive(self):
        """连续 3 次相同也应检测到（即使窗口更大）"""
        detector = ToolCallPatternDetector(window_size=5)
        detector.record_call("search_literature", {"query": "DID"}, "result1")
        detector.record_call("update_findings", {"finding": "x"}, "result2")
        # 接下来 3 次完全相同
        for i in range(3):
            detector.record_call("read_section", {"section": "methods"}, "same")
        self.assertEqual(detector.detect(), LoopPattern.EXACT_REPEAT)

    def test_no_detect_with_varied_calls(self):
        """参数不同时不应误检测"""
        detector = ToolCallPatternDetector(window_size=5)
        for i in range(5):
            detector.record_call("read_section", {"section": f"section_{i}"}, f"result_{i}")
        self.assertIsNone(detector.detect())


class TestParamDriftDetection(unittest.TestCase):
    """测试 PARAM_DRIFT 模式检测"""

    def test_detect_param_drift(self):
        """同一工具、参数微变但全部失败且结果相同 -> PARAM_DRIFT"""
        detector = ToolCallPatternDetector(window_size=5)
        for i in range(5):
            detector.record_call(
                "search_literature",
                {"query": f"keyword_{i}"},
                "Error: no results found",
                success=False,
            )
        self.assertEqual(detector.detect(), LoopPattern.PARAM_DRIFT)

    def test_no_drift_when_success(self):
        """有成功的调用时不应检测为 drift"""
        detector = ToolCallPatternDetector(window_size=5)
        for i in range(4):
            detector.record_call(
                "search_literature", {"query": f"q_{i}"}, "error", success=False
            )
        detector.record_call(
            "search_literature", {"query": "q_4"}, "found something", success=True
        )
        self.assertNotEqual(detector.detect(), LoopPattern.PARAM_DRIFT)

    def test_no_drift_different_tools(self):
        """不同工具不应被检测为 drift"""
        detector = ToolCallPatternDetector(window_size=5)
        tools = ["read_section", "search_literature", "read_section", "update_findings", "read_section"]
        for tool in tools:
            detector.record_call(tool, {}, "error", success=False)
        self.assertNotEqual(detector.detect(), LoopPattern.PARAM_DRIFT)


class TestOscillationDetection(unittest.TestCase):
    """测试 OSCILLATION 模式检测"""

    def test_detect_oscillation(self):
        """A->B->A->B->A 交替模式应被检测"""
        detector = ToolCallPatternDetector(window_size=5)
        tools = ["read_section", "search_literature"] * 3
        for tool in tools[:5]:
            detector.record_call(tool, {"x": 1}, "result")
        self.assertEqual(detector.detect(), LoopPattern.OSCILLATION)

    def test_no_oscillation_with_three_tools(self):
        """三种工具交替不应被检测为二元振荡"""
        detector = ToolCallPatternDetector(window_size=6)
        tools = ["a", "b", "c", "a", "b", "c"]
        for tool in tools:
            detector.record_call(tool, {}, "result")
        self.assertNotEqual(detector.detect(), LoopPattern.OSCILLATION)


# ==============================================================
# MVP 层测试：恢复动作基础
# ==============================================================

class TestRecoveryActions(unittest.TestCase):
    """测试恢复策略生成"""

    def test_exact_repeat_recovery(self):
        """EXACT_REPEAT 应产生包含替代工具建议的恢复"""
        detector = ToolCallPatternDetector(window_size=3)
        for i in range(3):
            detector.record_call("read_section", {"section": "intro"}, "same")

        pattern = detector.detect()
        self.assertEqual(pattern, LoopPattern.EXACT_REPEAT)

        recovery = detector.get_recovery_action(
            pattern, current_turn=5, available_tools=["search_literature", "reflect_and_plan"]
        )
        self.assertIsInstance(recovery, RecoveryAction)
        self.assertIn("重复", recovery.message)
        self.assertEqual(recovery.pattern, LoopPattern.EXACT_REPEAT)

    def test_escalation_mechanism(self):
        """连续恢复失败应触发升级"""
        detector = ToolCallPatternDetector(window_size=3, max_escalation=2)

        # 第一次恢复
        for i in range(3):
            detector.record_call("read_section", {"section": "intro"}, "same")
        pattern = detector.detect()
        r1 = detector.get_recovery_action(pattern, current_turn=5)
        self.assertEqual(r1.escalation_level, 0)

        # 恢复后仍然循环 -> 第二次恢复（升级）
        for i in range(3):
            detector.record_call("read_section", {"section": "intro"}, "same")
        pattern = detector.detect()
        r2 = detector.get_recovery_action(pattern, current_turn=7)
        self.assertEqual(r2.escalation_level, 1)

        # 第三次 -> 达到 max_escalation，强制终止
        for i in range(3):
            detector.record_call("read_section", {"section": "intro"}, "same")
        pattern = detector.detect()
        r3 = detector.get_recovery_action(pattern, current_turn=9)
        self.assertEqual(r3.escalation_level, 2)
        self.assertIn("死亡螺旋", r3.message)

    def test_reset_clears_state(self):
        """reset 后检测器应回到初始状态"""
        detector = ToolCallPatternDetector(window_size=3)
        for i in range(3):
            detector.record_call("read_section", {"section": "intro"}, "same")
        self.assertIsNotNone(detector.detect())

        detector.reset()
        self.assertIsNone(detector.detect())
        self.assertEqual(len(detector.call_history), 0)


class TestToolCallRecord(unittest.TestCase):
    """测试 ToolCallRecord 辅助功能"""

    def test_signature(self):
        """signature 应该包含工具名和参数"""
        record = ToolCallRecord(
            tool_name="read_section",
            params={"section": "intro"},
            success=True,
            result_hash="abc123",
        )
        sig = record.signature()
        self.assertIn("read_section", sig)
        self.assertIn("intro", sig)

    def test_tool_signature(self):
        """tool_signature 只包含工具名"""
        record = ToolCallRecord(
            tool_name="search_literature",
            params={"query": "test"},
            success=True,
            result_hash="xyz",
        )
        self.assertEqual(record.tool_signature(), "search_literature")


# ==============================================================
# Complete 层测试：策略注册表
# ==============================================================

class TestRecoveryRegistry(unittest.TestCase):
    """测试可插拔策略注册表"""

    def test_register_and_get_wildcard(self):
        """通配注册应对所有 phase 生效"""
        registry = RecoveryRegistry()
        handler = DefaultExactRepeatHandler()
        registry.register(LoopPattern.EXACT_REPEAT, handler, phase="*")

        result = registry.get_handler(LoopPattern.EXACT_REPEAT, "any_phase")
        self.assertIs(result, handler)

    def test_register_phase_specific(self):
        """Phase 特定注册应优先于通配"""
        registry = RecoveryRegistry()
        generic = DefaultExactRepeatHandler()
        specific = DefaultParamDriftHandler()  # 故意用不同 handler 区分

        registry.register(LoopPattern.EXACT_REPEAT, generic, phase="*")
        registry.register(LoopPattern.EXACT_REPEAT, specific, phase="methodology_analysis")

        # 精确匹配
        result = registry.get_handler(LoopPattern.EXACT_REPEAT, "methodology_analysis")
        self.assertIs(result, specific)

        # 非精确匹配 fallback 到通配
        result2 = registry.get_handler(LoopPattern.EXACT_REPEAT, "other_phase")
        self.assertIs(result2, generic)

    def test_unregister(self):
        """注销后 handler 不再可用"""
        registry = RecoveryRegistry()
        handler = DefaultExactRepeatHandler()
        registry.register(LoopPattern.EXACT_REPEAT, handler)

        self.assertTrue(registry.unregister(LoopPattern.EXACT_REPEAT))
        self.assertIsNone(registry.get_handler(LoopPattern.EXACT_REPEAT))

    def test_unregister_nonexistent(self):
        """注销不存在的 handler 返回 False"""
        registry = RecoveryRegistry()
        self.assertFalse(registry.unregister(LoopPattern.OSCILLATION, "nonexistent"))

    def test_registered_count(self):
        """注册数量应正确"""
        registry = RecoveryRegistry()
        self.assertEqual(registry.registered_count, 0)
        registry.register(LoopPattern.EXACT_REPEAT, DefaultExactRepeatHandler())
        registry.register(LoopPattern.PARAM_DRIFT, DefaultParamDriftHandler())
        self.assertEqual(registry.registered_count, 2)

    def test_list_registered(self):
        """列出注册列表"""
        registry = RecoveryRegistry()
        registry.register(LoopPattern.EXACT_REPEAT, DefaultExactRepeatHandler(), "phase_a")
        registry.register(LoopPattern.OSCILLATION, DefaultOscillationHandler(), "*")

        registered = registry.list_registered()
        self.assertEqual(len(registered), 2)
        self.assertIn(("exact_repeat", "phase_a"), registered)
        self.assertIn(("oscillation", "*"), registered)

    def test_custom_handler(self):
        """自定义 handler 应正常工作"""

        class MyHandler:
            def handle(self, ctx: RecoveryContext) -> RecoveryAction:
                return RecoveryAction(
                    pattern=ctx.pattern,
                    message="Custom recovery!",
                    escalation_level=ctx.escalation_count,
                    suggest_tools=["my_tool"],
                )

        registry = RecoveryRegistry()
        registry.register(LoopPattern.EXACT_REPEAT, MyHandler(), phase="deep_dive")

        handler = registry.get_handler(LoopPattern.EXACT_REPEAT, "deep_dive")
        self.assertIsNotNone(handler)

        ctx = RecoveryContext(
            pattern=LoopPattern.EXACT_REPEAT,
            current_phase="deep_dive",
            current_turn=10,
            escalation_count=0,
            repeated_tool="read_section",
            repeated_params={"section": "intro"},
            recent_calls=[],
            available_tools=["read_section", "search_literature"],
        )
        action = handler.handle(ctx)
        self.assertEqual(action.message, "Custom recovery!")
        self.assertEqual(action.suggest_tools, ["my_tool"])


# ==============================================================
# Complete 层测试：上下文感知恢复
# ==============================================================

class TestPhaseAwareRecovery(unittest.TestCase):
    """测试不同 phase 产生不同的恢复话术和工具建议"""

    def test_methodology_phase_hint_in_message(self):
        """methodology_analysis phase 应产生特定话术"""
        detector = ToolCallPatternDetector(window_size=3)
        for i in range(3):
            detector.record_call("read_section", {"section": "methods"}, "same")

        pattern = detector.detect()
        recovery = detector.get_recovery_action(
            pattern, current_turn=5,
            current_phase="methodology_analysis",
            available_tools=["search_literature", "reflect_and_plan"],
        )

        self.assertIn("methodology_analysis", recovery.message)
        self.assertIn("方法论分析", recovery.message)

    def test_statistical_phase_hint_in_message(self):
        """statistical_validation phase 应产生特定话术"""
        detector = ToolCallPatternDetector(window_size=3)
        for i in range(3):
            detector.record_call("read_section", {"section": "results"}, "same")

        pattern = detector.detect()
        recovery = detector.get_recovery_action(
            pattern, current_turn=5,
            current_phase="statistical_validation",
            available_tools=["search_literature", "update_findings"],
        )

        self.assertIn("statistical_validation", recovery.message)
        self.assertIn("统计", recovery.message)

    def test_unknown_phase_no_hint(self):
        """未知 phase 不应产生 phase 特定话术"""
        detector = ToolCallPatternDetector(window_size=3)
        for i in range(3):
            detector.record_call("read_section", {"section": "intro"}, "same")

        pattern = detector.detect()
        recovery = detector.get_recovery_action(
            pattern, current_turn=5,
            current_phase="unknown_phase",
            available_tools=["search_literature"],
        )

        # 应该有基本消息但没有 phase 特定建议
        self.assertIn("重复", recovery.message)
        self.assertNotIn("[unknown_phase]", recovery.message)

    def test_phase_aware_tool_suggestions(self):
        """phase 特定的工具建议应优先于通用建议"""
        detector = ToolCallPatternDetector(window_size=3)
        for i in range(3):
            detector.record_call(
                "search_literature", {"query": f"kw_{i}"},
                "error", success=False,
            )

        pattern = detector.detect()
        # literature_check phase 优先推荐 search_literature, read_section
        recovery = detector.get_recovery_action(
            pattern, current_turn=5,
            current_phase="literature_check",
            available_tools=["read_section", "reflect_and_plan", "update_findings"],
        )
        # read_section 应在建议中（literature_check 的优先工具）
        self.assertIn("read_section", recovery.suggest_tools)

    def test_param_drift_phase_aware(self):
        """PARAM_DRIFT 也应该有 phase-aware 话术"""
        detector = ToolCallPatternDetector(window_size=3)
        for i in range(3):
            detector.record_call(
                "search_literature", {"query": f"stat_{i}"},
                "error", success=False,
            )

        pattern = detector.detect()
        recovery = detector.get_recovery_action(
            pattern, current_turn=5,
            current_phase="statistical_validation",
            available_tools=["read_section"],
        )
        self.assertIn("statistical_validation", recovery.message)

    def test_oscillation_phase_aware(self):
        """OSCILLATION 也应该有 phase-aware 话术"""
        detector = ToolCallPatternDetector(window_size=4)
        tools = ["read_section", "search_literature"] * 3
        for tool in tools[:4]:
            detector.record_call(tool, {"x": 1}, "result")

        pattern = detector.detect()
        recovery = detector.get_recovery_action(
            pattern, current_turn=5,
            current_phase="overall_assessment",
            available_tools=["reflect_and_plan"],
        )
        self.assertIn("overall_assessment", recovery.message)
        self.assertIn("综合评估", recovery.message)


# ==============================================================
# Complete 层测试：恢复效果追踪
# ==============================================================

class TestRecoveryTracker(unittest.TestCase):
    """测试恢复效果追踪器"""

    def test_record_and_confirm_success(self):
        """记录恢复尝试并确认成功"""
        tracker = RecoveryTracker()
        tracker.record_recovery_attempt(
            pattern=LoopPattern.EXACT_REPEAT,
            phase="methodology_analysis",
            turn=5,
            escalation_level=0,
            recovery_message="test recovery",
            suggested_tools=["search_literature"],
            handler_name="DefaultExactRepeatHandler",
        )

        self.assertIsNotNone(tracker.pending)
        self.assertEqual(len(tracker.records), 0)

        tracker.confirm_success(current_turn=8)

        self.assertIsNone(tracker.pending)
        self.assertEqual(len(tracker.records), 1)
        self.assertEqual(tracker.records[0].outcome, RecoveryOutcome.SUCCESS)
        self.assertEqual(tracker.records[0].turns_until_resolved, 3)

    def test_record_and_confirm_escalation(self):
        """记录恢复尝试并确认升级"""
        tracker = RecoveryTracker()
        tracker.record_recovery_attempt(
            pattern=LoopPattern.PARAM_DRIFT,
            phase="literature_check",
            turn=10,
            escalation_level=1,
            recovery_message="second attempt",
            suggested_tools=[],
        )

        tracker.confirm_escalation()

        self.assertEqual(len(tracker.records), 1)
        self.assertEqual(tracker.records[0].outcome, RecoveryOutcome.ESCALATED)

    def test_record_and_confirm_termination(self):
        """记录恢复尝试并确认终止"""
        tracker = RecoveryTracker()
        tracker.record_recovery_attempt(
            pattern=LoopPattern.OSCILLATION,
            phase="deep_dive",
            turn=15,
            escalation_level=2,
            recovery_message="termination",
            suggested_tools=[],
        )

        tracker.confirm_termination()

        self.assertEqual(len(tracker.records), 1)
        self.assertEqual(tracker.records[0].outcome, RecoveryOutcome.TERMINATED)

    def test_flush_pending_marks_failed(self):
        """flush_pending 将未确认记录标记为 FAILED"""
        tracker = RecoveryTracker()
        tracker.record_recovery_attempt(
            pattern=LoopPattern.EXACT_REPEAT,
            phase="test",
            turn=1,
            escalation_level=0,
            recovery_message="msg",
            suggested_tools=[],
        )

        tracker.flush_pending()

        self.assertEqual(len(tracker.records), 1)
        self.assertEqual(tracker.records[0].outcome, RecoveryOutcome.FAILED)
        self.assertIsNone(tracker.pending)

    def test_consecutive_records_auto_fail_previous(self):
        """连续记录时，之前未确认的自动标记为 FAILED"""
        tracker = RecoveryTracker()
        tracker.record_recovery_attempt(
            pattern=LoopPattern.EXACT_REPEAT, phase="a", turn=1,
            escalation_level=0, recovery_message="first", suggested_tools=[],
        )
        tracker.record_recovery_attempt(
            pattern=LoopPattern.PARAM_DRIFT, phase="b", turn=3,
            escalation_level=1, recovery_message="second", suggested_tools=[],
        )

        # 第一个应该已自动标记为 FAILED
        self.assertEqual(len(tracker.records), 1)
        self.assertEqual(tracker.records[0].outcome, RecoveryOutcome.FAILED)
        self.assertEqual(tracker.records[0].pattern, LoopPattern.EXACT_REPEAT)

    def test_get_stats_empty(self):
        """空 tracker 应返回零值统计"""
        tracker = RecoveryTracker()
        stats = tracker.get_stats()
        self.assertEqual(stats["total_attempts"], 0)
        self.assertEqual(stats["success_rate"], 0.0)

    def test_get_stats_with_data(self):
        """有数据时统计应正确"""
        tracker = RecoveryTracker()

        # 添加成功记录
        tracker.record_recovery_attempt(
            pattern=LoopPattern.EXACT_REPEAT, phase="methodology_analysis",
            turn=5, escalation_level=0, recovery_message="msg1", suggested_tools=[],
        )
        tracker.confirm_success(current_turn=7)

        # 添加失败记录
        tracker.record_recovery_attempt(
            pattern=LoopPattern.PARAM_DRIFT, phase="methodology_analysis",
            turn=10, escalation_level=0, recovery_message="msg2", suggested_tools=[],
        )
        tracker.flush_pending()

        stats = tracker.get_stats()
        self.assertEqual(stats["total_attempts"], 2)
        self.assertAlmostEqual(stats["success_rate"], 0.5)
        self.assertEqual(stats["by_pattern"]["exact_repeat"]["success"], 1)
        self.assertEqual(stats["by_pattern"]["param_drift"]["failed"], 1)
        self.assertEqual(stats["by_phase"]["methodology_analysis"]["total"], 2)

    def test_export_for_evolution(self):
        """导出格式应包含 evolution 需要的所有字段"""
        tracker = RecoveryTracker()
        tracker.record_recovery_attempt(
            pattern=LoopPattern.EXACT_REPEAT, phase="deep_dive",
            turn=5, escalation_level=0, recovery_message="test msg",
            suggested_tools=["tool_a", "tool_b"],
            handler_name="DefaultExactRepeatHandler",
        )
        tracker.confirm_success(current_turn=8)

        exported = tracker.export_for_evolution()
        self.assertEqual(len(exported), 1)

        record = exported[0]
        self.assertEqual(record["pattern"], "exact_repeat")
        self.assertEqual(record["phase"], "deep_dive")
        self.assertEqual(record["outcome"], "success")
        self.assertEqual(record["turns_until_resolved"], 3)
        self.assertTrue(record["was_effective"])
        self.assertFalse(record["required_escalation"])
        self.assertEqual(record["handler_name"], "DefaultExactRepeatHandler")
        self.assertEqual(record["suggested_tools"], ["tool_a", "tool_b"])

    def test_serialize_and_deserialize(self):
        """序列化/反序列化应保持数据完整（包括长消息不截断）"""
        long_msg = "A" * 500  # 超过 200 字符的消息
        tracker = RecoveryTracker()
        tracker.record_recovery_attempt(
            pattern=LoopPattern.OSCILLATION, phase="overall_assessment",
            turn=12, escalation_level=1, recovery_message=long_msg,
            suggested_tools=["reflect_and_plan"],
            handler_name="DefaultOscillationHandler",
        )
        tracker.confirm_success(current_turn=15)

        # 序列化
        data = tracker.serialize()
        self.assertEqual(len(data), 1)
        # serialize 保留完整消息
        self.assertEqual(data[0]["recovery_message"], long_msg)

        # 反序列化
        restored = RecoveryTracker.deserialize(data)
        self.assertEqual(len(restored.records), 1)
        self.assertEqual(restored.records[0].pattern, LoopPattern.OSCILLATION)
        self.assertEqual(restored.records[0].outcome, RecoveryOutcome.SUCCESS)
        self.assertEqual(restored.records[0].phase, "overall_assessment")
        # 完整消息往返不丢失
        self.assertEqual(restored.records[0].recovery_message, long_msg)

    def test_deserialize_backward_compat_old_format(self):
        """反序列化兼容旧格式（recovery_message_preview 截断字段）"""
        old_data = [{
            "timestamp": 100.0,
            "pattern": "exact_repeat",
            "phase": "deep_dive",
            "turn": 5,
            "escalation_level": 0,
            "recovery_message_preview": "truncated preview...",
            "suggested_tools": ["search_literature"],
            "outcome": "success",
            "turns_until_resolved": 2,
            "handler_name": "DefaultExactRepeatHandler",
        }]
        restored = RecoveryTracker.deserialize(old_data)
        self.assertEqual(len(restored.records), 1)
        self.assertEqual(restored.records[0].recovery_message, "truncated preview...")


# ==============================================================
# Complete 层测试：集成 — 检测器使用注册表和追踪器
# ==============================================================

class TestDetectorWithRegistryAndTracker(unittest.TestCase):
    """测试检测器与策略注册表、追踪器的集成"""

    def test_detector_has_default_handlers(self):
        """检测器初始化时应注册 3 个默认 handler"""
        detector = ToolCallPatternDetector()
        self.assertEqual(detector.registry.registered_count, 3)

    def test_detector_uses_registry_handler(self):
        """检测器应通过注册表获取 handler"""
        detector = ToolCallPatternDetector(window_size=3)

        # 注册自定义 handler
        class CustomHandler:
            def handle(self, ctx: RecoveryContext) -> RecoveryAction:
                return RecoveryAction(
                    pattern=ctx.pattern,
                    message="CUSTOM HANDLER FIRED",
                    escalation_level=ctx.escalation_count,
                )

        detector.registry.register(
            LoopPattern.EXACT_REPEAT, CustomHandler(), phase="special_phase"
        )

        # 触发循环
        for i in range(3):
            detector.record_call("read_section", {"section": "x"}, "same")

        pattern = detector.detect()
        recovery = detector.get_recovery_action(
            pattern, current_turn=5, current_phase="special_phase"
        )

        self.assertEqual(recovery.message, "CUSTOM HANDLER FIRED")

    def test_detector_tracks_recovery_attempts(self):
        """检测器应自动记录恢复尝试到 tracker"""
        detector = ToolCallPatternDetector(window_size=3)

        for i in range(3):
            detector.record_call("read_section", {"section": "intro"}, "same")

        pattern = detector.detect()
        detector.get_recovery_action(pattern, current_turn=5, current_phase="test_phase")

        # tracker 应有一个 pending 记录
        self.assertIsNotNone(detector.tracker.pending)
        self.assertEqual(detector.tracker.pending.phase, "test_phase")

    def test_detector_confirms_success_on_no_pattern(self):
        """检测器在恢复后检测到无循环时应确认成功"""
        detector = ToolCallPatternDetector(window_size=3)

        # 触发循环
        for i in range(3):
            detector.record_call("read_section", {"section": "intro"}, "same")
        pattern = detector.detect()
        detector.get_recovery_action(pattern, current_turn=5)

        # 之后正常调用，不再循环
        detector.record_call("search_literature", {"query": "new"}, "result1", turn=6)
        detector.record_call("update_findings", {"f": "x"}, "result2", turn=7)
        detector.record_call("read_section", {"section": "methods"}, "different", turn=8)

        # detect 应返回 None 且确认成功
        self.assertIsNone(detector.detect())
        self.assertEqual(len(detector.tracker.records), 1)
        self.assertEqual(detector.tracker.records[0].outcome, RecoveryOutcome.SUCCESS)

    def test_detector_tracks_escalation(self):
        """升级时 tracker 应记录升级事件"""
        detector = ToolCallPatternDetector(window_size=3, max_escalation=2)

        # 第一次恢复
        for i in range(3):
            detector.record_call("read_section", {"section": "intro"}, "same")
        pattern = detector.detect()
        detector.get_recovery_action(pattern, current_turn=5, current_phase="test")

        # 恢复后仍循环 -> 升级
        for i in range(3):
            detector.record_call("read_section", {"section": "intro"}, "same")
        pattern = detector.detect()
        detector.get_recovery_action(pattern, current_turn=7, current_phase="test")

        # 应有 escalation 记录
        records = detector.tracker.records
        self.assertTrue(len(records) >= 1)
        escalated = [r for r in records if r.outcome == RecoveryOutcome.ESCALATED]
        self.assertTrue(len(escalated) >= 1)

    def test_detector_tracks_termination(self):
        """死亡螺旋终止时 tracker 应记录"""
        detector = ToolCallPatternDetector(window_size=3, max_escalation=2)

        # 连续 3 次恢复失败 -> 终止
        for round_num in range(3):
            for i in range(3):
                detector.record_call("read_section", {"section": "intro"}, "same")
            pattern = detector.detect()
            detector.get_recovery_action(pattern, current_turn=5 + round_num * 2)

        # 应有终止记录
        records = detector.tracker.records
        terminated = [r for r in records if r.outcome == RecoveryOutcome.TERMINATED]
        self.assertTrue(len(terminated) >= 1)

    def test_reset_flushes_tracker(self):
        """reset 应 flush tracker 的 pending 记录"""
        detector = ToolCallPatternDetector(window_size=3)

        for i in range(3):
            detector.record_call("read_section", {"section": "intro"}, "same")
        pattern = detector.detect()
        detector.get_recovery_action(pattern, current_turn=5)

        # 有 pending 记录
        self.assertIsNotNone(detector.tracker.pending)

        # reset 后 pending 应被 flush
        detector.reset()
        self.assertIsNone(detector.tracker.pending)
        self.assertEqual(len(detector.tracker.records), 1)
        self.assertEqual(detector.tracker.records[0].outcome, RecoveryOutcome.FAILED)

    def test_export_after_full_session(self):
        """完整 session 后 export 应包含所有事件"""
        detector = ToolCallPatternDetector(window_size=3)

        # 事件 1：恢复成功
        for i in range(3):
            detector.record_call("read_section", {"section": "intro"}, "same", turn=i+1)
        pattern = detector.detect()
        detector.get_recovery_action(pattern, current_turn=3, current_phase="methodology_analysis")
        # 正常调用脱离循环
        detector.record_call("search_literature", {"query": "new"}, "ok", turn=4)
        detector.record_call("update_findings", {"f": "x"}, "ok", turn=5)
        detector.record_call("reflect_and_plan", {}, "ok", turn=6)
        detector.detect()  # 确认成功

        # 事件 2：恢复失败（session 结束时 flush）
        for i in range(3):
            detector.record_call("search_literature", {"q": f"kw_{i}"}, "err", success=False, turn=7+i)
        pattern = detector.detect()
        detector.get_recovery_action(pattern, current_turn=9, current_phase="literature_check")

        # Session 结束
        detector.reset()

        exported = detector.tracker.export_for_evolution()
        self.assertEqual(len(exported), 2)
        self.assertTrue(exported[0]["was_effective"])
        self.assertFalse(exported[1]["was_effective"])


# ==============================================================
# Kill Switch 测试
# ==============================================================

class TestKillSwitch(unittest.TestCase):
    """测试 SCHOLAR_GODEL_LOOP_GUARD 环境变量 Kill Switch"""

    def test_kill_switch_disables_detection(self):
        """Kill Switch 关闭时 detect() 始终返回 None"""
        import core.loop_guard as lg_module
        original_value = lg_module.LOOP_GUARD_ENABLED
        try:
            lg_module.LOOP_GUARD_ENABLED = False
            detector = ToolCallPatternDetector(window_size=3)
            # 构造一个必定被检测到的 exact_repeat
            for i in range(5):
                detector.record_call("read_section", {"s": "x"}, "same", turn=i+1)
            result = detector.detect()
            self.assertIsNone(result)
        finally:
            lg_module.LOOP_GUARD_ENABLED = original_value

    def test_kill_switch_on_detects_normally(self):
        """Kill Switch 开启时正常检测"""
        import core.loop_guard as lg_module
        original_value = lg_module.LOOP_GUARD_ENABLED
        try:
            lg_module.LOOP_GUARD_ENABLED = True
            detector = ToolCallPatternDetector(window_size=3)
            for i in range(5):
                detector.record_call("read_section", {"s": "x"}, "same", turn=i+1)
            result = detector.detect()
            self.assertEqual(result, LoopPattern.EXACT_REPEAT)
        finally:
            lg_module.LOOP_GUARD_ENABLED = original_value

    def test_record_call_still_works_when_disabled(self):
        """Kill Switch 关闭时 record_call 仍正常记录"""
        import core.loop_guard as lg_module
        original_value = lg_module.LOOP_GUARD_ENABLED
        try:
            lg_module.LOOP_GUARD_ENABLED = False
            detector = ToolCallPatternDetector(window_size=3)
            detector.record_call("read_section", {"s": "x"}, "res", turn=1)
            detector.record_call("search_lit", {"q": "y"}, "res", turn=2)
            self.assertEqual(len(detector.call_history), 2)
        finally:
            lg_module.LOOP_GUARD_ENABLED = original_value


if __name__ == "__main__":
    unittest.main()
