"""
tests/test_signal_parser.py — Unit tests for core/signal_parser.py

测试覆盖:
    1. is_signal() 快速判断
    2. parse_signal() 对所有 7 种信号类型的解析
    3. payload 为空时的默认值
    4. JSON 解析失败时的降级处理
    5. 非信号字符串返回 None
    6. 边界情况（空字符串、只有前缀无分隔符等）
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure v2/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest

from core.signal_parser import (
    SignalType,
    ParsedSignal,
    is_signal,
    parse_signal,
)


class TestIsSignal(unittest.TestCase):
    """is_signal() 快速判断测试。"""

    def test_done_signal(self):
        self.assertTrue(is_signal("__DONE__|summary"))

    def test_nudge_signal(self):
        self.assertTrue(is_signal("__NUDGE__|reason"))

    def test_talk_signal(self):
        self.assertTrue(is_signal('__TALK__|{"message": "hi"}'))

    def test_spawn_signal(self):
        self.assertTrue(is_signal('__SPAWN__|{"lens": "x"}'))

    def test_parallel_spawn_signal(self):
        self.assertTrue(is_signal('__PARALLEL_SPAWN__|{"readers": []}'))

    def test_switch_signal(self):
        self.assertTrue(is_signal('__SWITCH__|{"target_persona": "writer"}'))

    def test_model_signal(self):
        self.assertTrue(is_signal('__MODEL__|{"target": "gpt-4"}'))

    def test_normal_tool_result(self):
        self.assertFalse(is_signal("Section content: Introduction..."))

    def test_empty_string(self):
        self.assertFalse(is_signal(""))

    def test_double_underscore_but_not_signal(self):
        """以 __ 开头但不是已知信号类型。"""
        self.assertFalse(is_signal("__UNKNOWN__|something"))

    def test_signal_without_pipe(self):
        """信号前缀但没有分隔符（仍应被识别为信号）。"""
        self.assertTrue(is_signal("__DONE__"))

    def test_parallel_spawn_not_confused_with_spawn(self):
        """确保 __PARALLEL_SPAWN__ 不会被误识为 __SPAWN__。"""
        self.assertTrue(is_signal("__PARALLEL_SPAWN__|{}"))
        # 也确保 __SPAWN__ 独立识别
        self.assertTrue(is_signal("__SPAWN__|{}"))


class TestParseDone(unittest.TestCase):
    """__DONE__ 信号解析。"""

    def test_with_summary(self):
        result = parse_signal("__DONE__|审稿完成，发现3个问题")
        self.assertIsNotNone(result)
        self.assertEqual(result.signal_type, SignalType.DONE)
        self.assertEqual(result.payload, "审稿完成，发现3个问题")
        self.assertIsNone(result.parse_error)

    def test_without_payload(self):
        result = parse_signal("__DONE__")
        self.assertIsNotNone(result)
        self.assertEqual(result.signal_type, SignalType.DONE)
        self.assertEqual(result.payload, "")

    def test_with_multiple_pipes(self):
        """payload 本身包含 | 字符。"""
        result = parse_signal("__DONE__|summary with | pipe | chars")
        self.assertIsNotNone(result)
        self.assertEqual(result.payload, "summary with | pipe | chars")

    def test_raw_preserved(self):
        raw = "__DONE__|test summary"
        result = parse_signal(raw)
        self.assertEqual(result.raw, raw)


class TestParseNudge(unittest.TestCase):
    """__NUDGE__ 信号解析。"""

    def test_with_reason(self):
        result = parse_signal("__NUDGE__|还有未检查的部分")
        self.assertIsNotNone(result)
        self.assertEqual(result.signal_type, SignalType.NUDGE)
        self.assertEqual(result.payload, "还有未检查的部分")

    def test_empty_reason(self):
        result = parse_signal("__NUDGE__|")
        self.assertIsNotNone(result)
        self.assertEqual(result.payload, "")


class TestParseTalk(unittest.TestCase):
    """__TALK__ 信号解析。"""

    def test_valid_json(self):
        result = parse_signal('__TALK__|{"message": "你好", "expects_reply": true}')
        self.assertIsNotNone(result)
        self.assertEqual(result.signal_type, SignalType.TALK)
        self.assertEqual(result.payload["message"], "你好")
        self.assertTrue(result.payload["expects_reply"])
        self.assertIsNone(result.parse_error)

    def test_invalid_json_fallback(self):
        """JSON 解析失败时，TALK 降级为纯文本 message。"""
        result = parse_signal("__TALK__|just a plain text message")
        self.assertIsNotNone(result)
        self.assertEqual(result.signal_type, SignalType.TALK)
        self.assertEqual(result.payload["message"], "just a plain text message")
        self.assertFalse(result.payload["expects_reply"])
        self.assertIsNone(result.parse_error)  # TALK 的降级不算错误

    def test_empty_payload(self):
        """TALK 空 payload 是合法的（降级为空 message）。"""
        result = parse_signal("__TALK__|")
        self.assertIsNotNone(result)
        self.assertEqual(result.payload, {"message": "", "expects_reply": False})
        self.assertIsNone(result.parse_error)  # TALK 空 payload 不算错误


class TestParseSpawn(unittest.TestCase):
    """__SPAWN__ 信号解析。"""

    def test_valid_json(self):
        payload = '{"lens": "methodology", "focus": "statistics", "question": "Are the methods valid?"}'
        result = parse_signal(f"__SPAWN__|{payload}")
        self.assertIsNotNone(result)
        self.assertEqual(result.signal_type, SignalType.SPAWN)
        self.assertEqual(result.payload["lens"], "methodology")
        self.assertEqual(result.payload["focus"], "statistics")
        self.assertEqual(result.payload["question"], "Are the methods valid?")

    def test_invalid_json(self):
        result = parse_signal("__SPAWN__|not valid json {")
        self.assertIsNotNone(result)
        self.assertEqual(result.signal_type, SignalType.SPAWN)
        self.assertIsNone(result.payload)
        self.assertIsNotNone(result.parse_error)
        self.assertIn("JSON", result.parse_error)

    def test_empty_payload_is_error(self):
        """空 payload 应视为解析错误（与旧代码行为一致）。"""
        result = parse_signal("__SPAWN__|")
        self.assertIsNotNone(result)
        self.assertEqual(result.signal_type, SignalType.SPAWN)
        self.assertIsNone(result.payload)
        self.assertIsNotNone(result.parse_error)
        self.assertIn("空", result.parse_error)

    def test_whitespace_only_payload_is_error(self):
        """纯空白 payload 也应视为解析错误。"""
        result = parse_signal("__SPAWN__|   ")
        self.assertIsNotNone(result)
        self.assertIsNone(result.payload)
        self.assertIsNotNone(result.parse_error)


class TestParseParallelSpawn(unittest.TestCase):
    """__PARALLEL_SPAWN__ 信号解析。"""

    def test_valid_json(self):
        payload = '{"readers": [{"lens": "a", "focus": "x"}, {"lens": "b", "focus": "y"}]}'
        result = parse_signal(f"__PARALLEL_SPAWN__|{payload}")
        self.assertIsNotNone(result)
        self.assertEqual(result.signal_type, SignalType.PARALLEL_SPAWN)
        self.assertEqual(len(result.payload["readers"]), 2)
        self.assertEqual(result.payload["readers"][0]["lens"], "a")

    def test_not_confused_with_spawn(self):
        """确保 __PARALLEL_SPAWN__ 不被解析为 __SPAWN__。"""
        result = parse_signal('__PARALLEL_SPAWN__|{"readers": []}')
        self.assertEqual(result.signal_type, SignalType.PARALLEL_SPAWN)


class TestParseSwitch(unittest.TestCase):
    """__SWITCH__ 信号解析。"""

    def test_valid_json(self):
        payload = '{"target_persona": "writer", "reason": "准备编辑", "nudge": "请注意格式"}'
        result = parse_signal(f"__SWITCH__|{payload}")
        self.assertIsNotNone(result)
        self.assertEqual(result.signal_type, SignalType.SWITCH)
        self.assertEqual(result.payload["target_persona"], "writer")
        self.assertEqual(result.payload["reason"], "准备编辑")
        self.assertEqual(result.payload["nudge"], "请注意格式")

    def test_empty_payload_is_error(self):
        """空 payload 应视为解析错误（与旧代码行为一致）。"""
        result = parse_signal("__SWITCH__|")
        self.assertIsNotNone(result)
        self.assertEqual(result.signal_type, SignalType.SWITCH)
        self.assertIsNone(result.payload)
        self.assertIsNotNone(result.parse_error)


class TestParseModel(unittest.TestCase):
    """__MODEL__ 信号解析。"""

    def test_valid_json(self):
        payload = '{"target": "claude-3-opus", "reason": "需要更强的推理能力"}'
        result = parse_signal(f"__MODEL__|{payload}")
        self.assertIsNotNone(result)
        self.assertEqual(result.signal_type, SignalType.MODEL)
        self.assertEqual(result.payload["target"], "claude-3-opus")
        self.assertEqual(result.payload["reason"], "需要更强的推理能力")


class TestNonSignals(unittest.TestCase):
    """确保普通 tool 返回值不被误解析。"""

    def test_normal_text(self):
        self.assertIsNone(parse_signal("Section content here."))

    def test_empty_string(self):
        self.assertIsNone(parse_signal(""))

    def test_similar_but_invalid_prefix(self):
        self.assertIsNone(parse_signal("__INVALID__|something"))

    def test_partial_prefix(self):
        self.assertIsNone(parse_signal("__DON|something"))

    def test_underscore_in_content(self):
        """内容中包含双下划线但不是信号。"""
        self.assertIsNone(parse_signal("Variable __name__ is special in Python"))


class TestEdgeCases(unittest.TestCase):
    """审核补充：边界情况测试。"""

    def test_payload_with_pipe_chars(self):
        """payload 中包含 | 字符时，只在第一个 | 处分割。"""
        result = parse_signal('__TALK__|{"message": "a|b|c", "expects_reply": false}')
        self.assertIsNotNone(result)
        self.assertEqual(result.payload["message"], "a|b|c")

    def test_done_payload_with_pipe(self):
        """DONE 的纯文本 payload 中包含 |。"""
        result = parse_signal("__DONE__|summary | with | pipes")
        self.assertEqual(result.payload, "summary | with | pipes")

    def test_talk_json_missing_keys(self):
        """TALK JSON 有效但缺少某些 key（不算错误，调用方会用 .get 取默认值）。"""
        result = parse_signal('__TALK__|{"message": "hello"}')
        self.assertIsNotNone(result)
        self.assertEqual(result.payload["message"], "hello")
        # expects_reply key 缺失，调用方 loop.py 用 .get("expects_reply", False) 处理
        self.assertNotIn("expects_reply", result.payload)
        self.assertIsNone(result.parse_error)

    def test_parallel_spawn_empty_payload_is_error(self):
        """PARALLEL_SPAWN 空 payload 也是错误。"""
        result = parse_signal("__PARALLEL_SPAWN__|")
        self.assertIsNotNone(result)
        self.assertEqual(result.signal_type, SignalType.PARALLEL_SPAWN)
        self.assertIsNone(result.payload)
        self.assertIsNotNone(result.parse_error)

    def test_model_empty_payload_is_error(self):
        """MODEL 空 payload 也是错误（虽然 loop.py 不使用 parsed.payload）。"""
        result = parse_signal("__MODEL__|")
        self.assertIsNotNone(result)
        self.assertEqual(result.signal_type, SignalType.MODEL)
        self.assertIsNone(result.payload)
        self.assertIsNotNone(result.parse_error)

    def test_signal_prefix_followed_by_non_pipe_char(self):
        """前缀后紧跟非 | 字符（如 __DONE__xyz）——和旧代码行为一致，仍识别为信号。"""
        result = parse_signal("__DONE__xyz")
        self.assertIsNotNone(result)
        self.assertEqual(result.signal_type, SignalType.DONE)
        # 没有 | 分隔符，payload 为空
        self.assertEqual(result.payload, "")

    def test_spawn_valid_json_with_extra_fields(self):
        """SPAWN JSON 包含额外字段不影响解析。"""
        result = parse_signal('__SPAWN__|{"lens": "x", "focus": "y", "question": "z", "extra": 1}')
        self.assertIsNotNone(result)
        self.assertEqual(result.payload["lens"], "x")
        self.assertEqual(result.payload["extra"], 1)
        self.assertIsNone(result.parse_error)


if __name__ == "__main__":
    unittest.main()
