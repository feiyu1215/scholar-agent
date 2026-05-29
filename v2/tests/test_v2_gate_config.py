"""
tests/test_v2_gate_config.py — B4: Completion Gate 动态配置 测试

测试内容:
    1. CompletionGateConfig 默认值和 describe()
    2. compute_gate_config 三层优先级逻辑
    3. _check_stagnation 使用动态 idle_rounds
    4. check_soft_turn_limit 使用动态 self_eval 轮次
    5. min_findings_for_exit nudge 机制
    6. record_review_stats + experience-driven config
    7. compute_idle_rounds_before_exit
"""

import sys
import os
import unittest
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.gate_config import (
    CompletionGateConfig,
    compute_gate_config,
    record_review_stats,
    compute_idle_rounds_before_exit,
    _clamp,
    _parse_stats_description,
    DEFAULT_IDLE_ROUNDS,
    DEFAULT_SELF_EVAL_FIRST,
    DEFAULT_SELF_EVAL_SECOND,
    DEFAULT_SELF_EVAL_FINAL,
)
from core.paper_type_hints import CognitiveHints
from core.memory import MemoryStore


# ============================================================
# 测试: CompletionGateConfig 基本功能
# ============================================================

class TestCompletionGateConfig(unittest.TestCase):
    """测试 CompletionGateConfig 数据结构。"""

    def test_defaults(self):
        """默认配置应使用系统常量。"""
        config = CompletionGateConfig()
        self.assertEqual(config.idle_rounds, DEFAULT_IDLE_ROUNDS)
        self.assertEqual(config.self_eval_first, DEFAULT_SELF_EVAL_FIRST)
        self.assertEqual(config.self_eval_second, DEFAULT_SELF_EVAL_SECOND)
        self.assertEqual(config.self_eval_final, DEFAULT_SELF_EVAL_FINAL)
        self.assertEqual(config.min_findings_for_exit, 0)
        self.assertEqual(config.source, "default")

    def test_describe(self):
        """describe() 应返回可读字符串。"""
        config = CompletionGateConfig(idle_rounds=7, source="hints")
        desc = config.describe()
        self.assertIn("hints", desc)
        self.assertIn("7", desc)


# ============================================================
# 测试: compute_gate_config 优先级
# ============================================================

class TestComputeGateConfig(unittest.TestCase):
    """测试三层优先级的 config 计算。"""

    def test_no_inputs_returns_default(self):
        """无任何输入时返回默认配置。"""
        config = compute_gate_config()
        self.assertEqual(config.idle_rounds, DEFAULT_IDLE_ROUNDS)
        self.assertEqual(config.source, "default")

    def test_cognitive_hints_override(self):
        """CognitiveHints 的 gate 参数应覆盖默认值。"""
        hints = CognitiveHints(
            paper_type_description="DID 论文",
            focus_dimensions=["平行趋势"],
            gate_idle_rounds=7,
            min_findings_for_exit=3,
        )
        config = compute_gate_config(cognitive_hints=hints)
        self.assertEqual(config.idle_rounds, 7)
        self.assertEqual(config.min_findings_for_exit, 3)
        self.assertIn("hints", config.source)

    def test_hints_clamped_to_valid_range(self):
        """极端值应被 clamp 到合理范围。"""
        hints = CognitiveHints(
            paper_type_description="X",
            focus_dimensions=["Y"],
            gate_idle_rounds=100,  # 过大
            min_findings_for_exit=-5,  # 负数
        )
        config = compute_gate_config(cognitive_hints=hints)
        self.assertEqual(config.idle_rounds, 10)  # clamped to max
        self.assertEqual(config.min_findings_for_exit, 0)  # clamped to min

    def test_experience_used_when_no_hints(self):
        """无 hints 时使用跨会话经验。"""
        tmp_dir = tempfile.mkdtemp()
        store = MemoryStore(tmp_dir)

        # 手动注入一个 review_stats pattern
        store.add_or_reinforce_procedure(
            category="review_stats",
            description="idle_avg=6,turns_avg=28",
            trigger_context="论文类型: DID论文, findings=5",
            effectiveness_score=0.7,
        )

        config = compute_gate_config(
            cognitive_hints=None,
            memory_store=store,
            paper_type="DID论文",
        )
        self.assertEqual(config.idle_rounds, 6)
        # self_eval_first ≈ 28 * 0.35 = 9.8 → 10 (clamped ≥ 8)
        self.assertGreaterEqual(config.self_eval_first, 8)
        self.assertIn("experience", config.source)

    def test_hints_override_experience(self):
        """有 hints 时，hints 优先于 experience。"""
        tmp_dir = tempfile.mkdtemp()
        store = MemoryStore(tmp_dir)
        store.add_or_reinforce_procedure(
            category="review_stats",
            description="idle_avg=4,turns_avg=20",
            trigger_context="论文类型: RCT论文, findings=3",
            effectiveness_score=0.5,
        )

        hints = CognitiveHints(
            paper_type_description="RCT论文",
            focus_dimensions=["随机化"],
            gate_idle_rounds=8,
        )
        config = compute_gate_config(
            cognitive_hints=hints,
            memory_store=store,
            paper_type="RCT论文",
        )
        # hints 的 8 应覆盖 experience 的 4
        self.assertEqual(config.idle_rounds, 8)


# ============================================================
# 测试: record_review_stats
# ============================================================

class TestRecordReviewStats(unittest.TestCase):
    """测试审稿行为统计记录。"""

    def test_records_stats_to_memory(self):
        """应正确记录统计到 ProceduralPattern。"""
        tmp_dir = tempfile.mkdtemp()
        store = MemoryStore(tmp_dir)

        record_review_stats(
            memory_store=store,
            paper_type="DID论文",
            total_turns=22,
            idle_rounds_before_exit=4,
            findings_count=6,
        )

        procs = [p for p in store.state.procedures if p.category == "review_stats"]
        self.assertEqual(len(procs), 1)
        self.assertIn("idle_avg=4", procs[0].description)
        self.assertIn("turns_avg=22", procs[0].description)
        self.assertIn("DID", procs[0].trigger_context)

    def test_skips_trivial_sessions(self):
        """轮次太少或无 paper_type 不记录。"""
        tmp_dir = tempfile.mkdtemp()
        store = MemoryStore(tmp_dir)

        record_review_stats(store, "", 20, 5, 3)  # 无 paper_type
        record_review_stats(store, "DID", 2, 1, 0)  # 轮次太少
        self.assertEqual(len(store.state.procedures), 0)

    def test_accumulation_across_sessions(self):
        """多次记录同类型论文会合并为一条并增加 evidence_count（数值差异被模板化忽略）。"""
        tmp_dir = tempfile.mkdtemp()
        store = MemoryStore(tmp_dir)

        record_review_stats(store, "DID论文", 20, 4, 5)
        record_review_stats(store, "DID论文", 25, 5, 7)

        procs = [p for p in store.state.procedures if p.category == "review_stats"]
        # P2-fix: _is_similar 现在会将数值差异模板化，同类模式合并为一条
        # 这是正确行为——review_stats 应该累积 evidence 而不是创建重复条目
        self.assertEqual(len(procs), 1)
        # 合并后 evidence_count 应为 2
        self.assertEqual(procs[0].evidence_count, 2)
        # 记录包含 DID
        self.assertIn("DID", procs[0].trigger_context)


# ============================================================
# 测试: compute_idle_rounds_before_exit
# ============================================================

class TestComputeIdleRoundsBeforeExit(unittest.TestCase):
    """测试退出前空转轮次计算。"""

    def test_empty_history(self):
        """空历史应返回 0。"""
        self.assertEqual(compute_idle_rounds_before_exit([], []), 0)

    def test_last_action_is_update(self):
        """最后一个动作是 update_findings → idle=0。"""
        history = [
            {"name": "read_section"},
            {"name": "update_findings"},
        ]
        self.assertEqual(compute_idle_rounds_before_exit(history, [{"f": 1}]), 0)

    def test_several_rounds_after_last_update(self):
        """update_findings 后还有 3 轮其他工具 → idle=3。"""
        history = [
            {"name": "read_section"},
            {"name": "update_findings"},
            {"name": "read_section"},
            {"name": "search_literature"},
            {"name": "reflect_and_plan"},
        ]
        # len=5, last_update_idx=1, 5-1-1=3
        self.assertEqual(compute_idle_rounds_before_exit(history, [{"f": 1}]), 3)

    def test_no_update_ever(self):
        """从未 update_findings → idle = len(history)。"""
        history = [
            {"name": "read_section"},
            {"name": "read_section"},
            {"name": "read_section"},
        ]
        self.assertEqual(compute_idle_rounds_before_exit(history, []), 3)


# ============================================================
# 测试: _parse_stats_description
# ============================================================

class TestParseStatsDescription(unittest.TestCase):
    """测试统计描述解析。"""

    def test_valid_format(self):
        """正确格式应解析成功。"""
        result = _parse_stats_description("idle_avg=5,turns_avg=22")
        self.assertEqual(result["avg_idle_rounds"], 5)
        self.assertEqual(result["avg_total_turns"], 22)

    def test_invalid_format(self):
        """无效格式应返回 None。"""
        self.assertIsNone(_parse_stats_description("garbage"))
        self.assertIsNone(_parse_stats_description("idle_avg=abc"))
        self.assertIsNone(_parse_stats_description(""))


# ============================================================
# 测试: Harness 集成
# ============================================================

class TestHarnessGateConfigIntegration(unittest.TestCase):
    """测试 B4 与 harness 的集成行为。"""

    def _make_harness(self):
        """创建 minimal harness。"""
        from core.harness import Harness
        tmp = tempfile.mkdtemp()
        paper_path = Path(tmp) / "paper.md"
        paper_path.write_text("# Abstract\nTest paper.\n\n# Methods\nWe do DID.\n")
        return Harness(paper_path=str(paper_path), max_loop_turns=50)

    def test_default_gate_config_on_init(self):
        """初始化后 gate_config 应为默认值。"""
        harness = self._make_harness()
        self.assertEqual(harness.gate_config.idle_rounds, DEFAULT_IDLE_ROUNDS)
        self.assertEqual(harness.gate_config.source, "default")

    def test_gate_config_updates_after_hints(self):
        """Agent 生成 cognitive_hints 后 gate_config 应更新。"""
        harness = self._make_harness()
        # 模拟 Agent 调用 generate_cognitive_hints
        harness._tool_generate_cognitive_hints({
            "paper_type_description": "理论经济学论文",
            "focus_dimensions": ["证明完备性", "假设合理性", "推广性讨论"],
            "typical_weaknesses": ["假设过强"],
            "verification_strategies": ["逐步验证证明"],
            "gate_idle_rounds": "8",
            "min_findings_for_exit": "2",
        })
        self.assertEqual(harness.gate_config.idle_rounds, 8)
        self.assertEqual(harness.gate_config.min_findings_for_exit, 2)
        self.assertIn("hints", harness.gate_config.source)

    def test_stagnation_uses_dynamic_idle(self):
        """_check_stagnation 应使用动态 idle_rounds。"""
        harness = self._make_harness()
        # 设置较大的 idle_rounds（8 轮）
        harness.gate_config.idle_rounds = 8

        # 模拟: 第 15 轮，最后一次 finding 在第 6 轮（距离=9 >= 8），
        # 且最近 8 轮 tool_call_history 都没有 update_findings
        harness.state.loop_turns = 15
        harness.state.findings = [{"finding": "X", "recorded_at_turn": 6}]
        harness.state.tool_call_history = [{"name": "read_section"} for _ in range(15)]
        # turns_since_last_finding = 15 - 6 = 9 >= idle_threshold=8，且 recent 8 轮无 update_findings
        result = harness._check_stagnation("read_section")
        self.assertIsNotNone(result)
        self.assertIn("产出观察", result)

    def test_stagnation_not_triggered_within_idle_window(self):
        """在 idle_rounds 窗口内不应触发停滞信号。"""
        harness = self._make_harness()
        harness.gate_config.idle_rounds = 8

        # 第 10 轮，最后一次 finding 在第 5 轮（距离=5 < 8）→ 不触发
        harness.state.loop_turns = 10
        harness.state.findings = [{"finding": "X", "recorded_at_turn": 5}]
        harness.state.tool_call_history = [{"name": "read_section"} for _ in range(10)]
        # turns_since_last_finding = 10 - 5 = 5 < 8 → 不触发
        result = harness._check_stagnation("read_section")
        self.assertIsNone(result)

    def test_soft_turn_limit_uses_dynamic_turns(self):
        """check_soft_turn_limit 应在动态轮次触发。"""
        harness = self._make_harness()
        # 设置非默认的自评轮次
        harness.gate_config.self_eval_first = 10
        harness.gate_config.self_eval_second = 20

        # 第 10 轮应触发首次自评
        harness.state.loop_turns = 10
        result = harness.check_soft_turn_limit()
        self.assertIsNotNone(result)
        self.assertIn("自评时刻", result)

        # 第 15 轮（原默认）不应触发（因为已改为 10）
        harness.state.loop_turns = 15
        result = harness.check_soft_turn_limit()
        self.assertIsNone(result)

        # 第 20 轮应触发第二次自评
        harness.state.loop_turns = 20
        result = harness.check_soft_turn_limit()
        self.assertIsNotNone(result)
        self.assertIn("边际价值", result)

    def test_min_findings_nudge(self):
        """findings 数不足时 _check_completion_gate 应产出 nudge。"""
        harness = self._make_harness()
        harness.gate_config.min_findings_for_exit = 3
        harness.state.findings = [
            {"finding": "F1", "priority": "high", "status": "verified"},
        ]

        result = harness._check_completion_gate()
        self.assertIsNotNone(result)
        # 消息格式: "当前 X 条发现" + "至少应有 Y 条"
        self.assertIn("1 条发现", result)
        self.assertIn("3 条", result)
        self.assertIn("mark_complete", result)

        # 第二次调用: min_findings nudge 不应重复（只触发一次）
        result2 = harness._check_completion_gate()
        if result2 is not None:
            # 可能是其他 nudge（Q1 quality_check 等），但不应再是 min_findings
            self.assertNotIn("至少应有", result2)

    def test_end_session_records_stats(self):
        """end_session 应记录审稿行为统计。"""
        harness = self._make_harness()
        harness.state.findings = [
            {"finding": "F1", "priority": "high", "status": "verified", "evidence": "E1"},
        ]
        harness.state.loop_turns = 18
        harness.state.tool_call_history = [
            {"name": "read_section"},
            {"name": "update_findings"},
            {"name": "read_section"},
            {"name": "read_section"},
            {"name": "reflect_and_plan"},
        ]
        harness.state.cognitive_hints = CognitiveHints(
            paper_type_description="DID论文",
            focus_dimensions=["平行趋势"],
        )

        harness.end_session(paper_title="Test Paper")

        # 检查 review_stats 是否记录
        stats_procs = [
            p for p in harness.memory.state.procedures
            if p.category == "review_stats"
        ]
        self.assertGreaterEqual(len(stats_procs), 1)
        self.assertIn("DID", stats_procs[0].trigger_context)


# ============================================================
# 测试: Helpers
# ============================================================

class TestHelpers(unittest.TestCase):
    """测试辅助函数。"""

    def test_clamp(self):
        """_clamp 应正确约束值。"""
        self.assertEqual(_clamp(5, 3, 10), 5)
        self.assertEqual(_clamp(1, 3, 10), 3)
        self.assertEqual(_clamp(15, 3, 10), 10)


# ============================================================
# 运行
# ============================================================

if __name__ == "__main__":
    unittest.main()
