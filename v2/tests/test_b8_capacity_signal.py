"""
tests/test_b8_capacity_signal.py — B8 Token Budget capacity % 信号测试

验证:
    1. used_pct 计算正确性（各种 token 数值）
    2. zone_label 阈值映射正确 (green < 50% < yellow < 80% < red)
    3. get_budget_status() 返回新字段格式正确
    4. 与 B4 CompactionEngine.get_capacity_pct() 计算结果一致
    5. 向后兼容：不传 current_context_tokens 时默认行为正确
    6. 边界情况：0 tokens, total tokens, 超出 total tokens
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.token_budget import TokenBudgetManager
from core.compaction import CompactionEngine, CompactionConfig
from core.godel_config import TOTAL_CONTEXT_WINDOW


# ============================================================
# Test: used_pct 计算
# ============================================================

class TestUsedPctCalculation:
    """used_pct 精确度验证。"""

    def test_zero_tokens_returns_zero(self):
        """0 tokens 使用 → 0.0%。"""
        mgr = TokenBudgetManager()
        assert mgr.compute_used_pct(0) == 0.0

    def test_half_tokens(self):
        """使用一半 → 0.5。"""
        mgr = TokenBudgetManager()
        half = TOTAL_CONTEXT_WINDOW // 2
        pct = mgr.compute_used_pct(half)
        assert abs(pct - 0.5) < 0.01

    def test_full_tokens(self):
        """使用全部 → 1.0。"""
        mgr = TokenBudgetManager()
        pct = mgr.compute_used_pct(TOTAL_CONTEXT_WINDOW)
        assert pct == 1.0

    def test_over_total_clamped_to_one(self):
        """超出总量 → clamp 到 1.0。"""
        mgr = TokenBudgetManager()
        pct = mgr.compute_used_pct(TOTAL_CONTEXT_WINDOW + 10000)
        assert pct == 1.0

    def test_negative_tokens_clamped_to_zero(self):
        """负数 → clamp 到 0.0。"""
        mgr = TokenBudgetManager()
        pct = mgr.compute_used_pct(-100)
        assert pct == 0.0

    def test_zero_budget_returns_zero(self):
        """total_budget = 0 → 安全返回 0.0。"""
        mgr = TokenBudgetManager(total_budget=0)
        pct = mgr.compute_used_pct(5000)
        assert pct == 0.0

    def test_specific_values(self):
        """精确数值验证。"""
        mgr = TokenBudgetManager()
        # 64000 / 128000 = 0.5
        assert mgr.compute_used_pct(64000) == 0.5
        # 96000 / 128000 = 0.75
        assert mgr.compute_used_pct(96000) == 0.75
        # 102400 / 128000 = 0.8
        assert mgr.compute_used_pct(102400) == 0.8


# ============================================================
# Test: zone_label 阈值
# ============================================================

class TestZoneLabelThresholds:
    """zone_label 与 pct 的映射关系验证。"""

    def test_green_below_50_percent(self):
        """< 50% → green。"""
        mgr = TokenBudgetManager()
        status = mgr.get_budget_status(current_context_tokens=60000)  # 46.9%
        assert status["zone_label"] == "green"

    def test_yellow_at_50_percent(self):
        """= 50% → yellow（边界含左不含右）。"""
        mgr = TokenBudgetManager()
        status = mgr.get_budget_status(current_context_tokens=64000)  # exactly 50%
        assert status["zone_label"] == "yellow"

    def test_yellow_between_50_and_80(self):
        """50%-80% → yellow。"""
        mgr = TokenBudgetManager()
        status = mgr.get_budget_status(current_context_tokens=89600)  # 70%
        assert status["zone_label"] == "yellow"

    def test_red_at_80_percent(self):
        """= 80% → red（边界含左不含右）。"""
        mgr = TokenBudgetManager()
        status = mgr.get_budget_status(current_context_tokens=102400)  # exactly 80%
        assert status["zone_label"] == "red"

    def test_red_above_80_percent(self):
        """> 80% → red。"""
        mgr = TokenBudgetManager()
        status = mgr.get_budget_status(current_context_tokens=115000)  # ~89.8%
        assert status["zone_label"] == "red"

    def test_green_at_zero(self):
        """0% → green。"""
        mgr = TokenBudgetManager()
        status = mgr.get_budget_status(current_context_tokens=0)
        assert status["zone_label"] == "green"

    def test_red_at_100_percent(self):
        """100% → red。"""
        mgr = TokenBudgetManager()
        status = mgr.get_budget_status(current_context_tokens=128000)
        assert status["zone_label"] == "red"

    def test_boundary_just_below_50(self):
        """刚好低于 50% → green。"""
        mgr = TokenBudgetManager()
        # 63999 / 128000 = 0.49999... < 0.5
        status = mgr.get_budget_status(current_context_tokens=63999)
        assert status["zone_label"] == "green"

    def test_boundary_just_below_80(self):
        """刚好低于 80% → yellow。"""
        mgr = TokenBudgetManager()
        # 102399 / 128000 = 0.79999... < 0.8
        status = mgr.get_budget_status(current_context_tokens=102399)
        assert status["zone_label"] == "yellow"


# ============================================================
# Test: get_budget_status() 返回格式
# ============================================================

class TestGetBudgetStatusFormat:
    """get_budget_status() 返回值结构验证。"""

    def test_all_fields_present(self):
        """返回 dict 包含所有必要字段。"""
        mgr = TokenBudgetManager()
        status = mgr.get_budget_status(current_context_tokens=50000)
        required_keys = {"zone_a", "zone_b_used", "zone_b_max",
                         "zone_c_available", "total", "used_pct", "zone_label"}
        assert required_keys.issubset(status.keys())

    def test_used_pct_is_float(self):
        """used_pct 是 float 类型。"""
        mgr = TokenBudgetManager()
        status = mgr.get_budget_status(current_context_tokens=50000)
        assert isinstance(status["used_pct"], float)

    def test_zone_label_is_string(self):
        """zone_label 是 str 类型。"""
        mgr = TokenBudgetManager()
        status = mgr.get_budget_status(current_context_tokens=50000)
        assert isinstance(status["zone_label"], str)
        assert status["zone_label"] in ("green", "yellow", "red")

    def test_original_fields_unchanged(self):
        """原有字段（zone_a, zone_b_used, zone_b_max, zone_c_available, total）不受影响。"""
        mgr = TokenBudgetManager()
        status = mgr.get_budget_status(current_context_tokens=50000)
        assert status["zone_a"] == 8000  # ZONE_A_DEFAULT_TOKENS
        assert status["zone_b_used"] == 0  # no allocation yet
        assert status["zone_b_max"] == 40000  # ZONE_B_MAX_TOKENS
        assert status["zone_c_available"] == 128000 - 8000 - 0
        assert status["total"] == 128000


# ============================================================
# Test: 与 B4 CompactionEngine.get_capacity_pct() 一致性
# ============================================================

class TestConsistencyWithB4:
    """确保 TokenBudgetManager 与 CompactionEngine 计算结果一致。"""

    def test_same_result_at_various_levels(self):
        """多个 token 值下两者计算结果一致。"""
        mgr = TokenBudgetManager()
        engine = CompactionEngine()

        test_values = [0, 10000, 32000, 64000, 96000, 102400, 128000]
        for tokens in test_values:
            budget_pct = mgr.compute_used_pct(tokens)
            engine_pct = engine.get_capacity_pct(tokens)
            assert abs(budget_pct - engine_pct) < 1e-10, (
                f"Mismatch at {tokens} tokens: "
                f"budget={budget_pct}, engine={engine_pct}"
            )

    def test_same_total_context_window(self):
        """两者使用相同的 TOTAL_CONTEXT_WINDOW 值。"""
        mgr = TokenBudgetManager()
        engine = CompactionEngine()
        assert mgr.total_budget == engine.config.total_context_window == TOTAL_CONTEXT_WINDOW

    def test_both_clamp_overflow(self):
        """溢出时两者都 clamp 到 1.0。"""
        mgr = TokenBudgetManager()
        engine = CompactionEngine()
        overflow = TOTAL_CONTEXT_WINDOW + 50000
        assert mgr.compute_used_pct(overflow) == 1.0
        assert engine.get_capacity_pct(overflow) == 1.0

    def test_both_handle_zero_budget(self):
        """total=0 时两者安全返回 0.0。"""
        mgr = TokenBudgetManager(total_budget=0)
        engine = CompactionEngine(config=CompactionConfig(total_context_window=0))
        assert mgr.compute_used_pct(5000) == 0.0
        assert engine.get_capacity_pct(5000) == 0.0


# ============================================================
# Test: 向后兼容
# ============================================================

class TestBackwardCompatibility:
    """不传 current_context_tokens 时的默认行为。"""

    def test_default_no_arg_returns_green(self):
        """不传参时 used_pct=0.0, zone_label='green'。"""
        mgr = TokenBudgetManager()
        status = mgr.get_budget_status()
        assert status["used_pct"] == 0.0
        assert status["zone_label"] == "green"

    def test_default_preserves_original_fields(self):
        """不传参时原有字段值不变。"""
        mgr = TokenBudgetManager()
        status = mgr.get_budget_status()
        assert status["zone_a"] == 8000
        assert status["zone_b_used"] == 0
        assert status["total"] == 128000


# ============================================================
# Test: 边界和异常情况
# ============================================================

class TestEdgeCases:
    """边界情况和异常输入。"""

    def test_custom_total_budget(self):
        """自定义 total_budget 的 used_pct 计算正确。"""
        mgr = TokenBudgetManager(total_budget=32000)
        pct = mgr.compute_used_pct(16000)
        assert pct == 0.5

    def test_very_small_budget(self):
        """极小 total_budget。"""
        mgr = TokenBudgetManager(total_budget=100)
        status = mgr.get_budget_status(current_context_tokens=90)
        assert status["used_pct"] == 0.9
        assert status["zone_label"] == "red"

    def test_pct_to_zone_label_static(self):
        """_pct_to_zone_label 是纯函数，可独立验证。"""
        assert TokenBudgetManager._pct_to_zone_label(0.0) == "green"
        assert TokenBudgetManager._pct_to_zone_label(0.49) == "green"
        assert TokenBudgetManager._pct_to_zone_label(0.5) == "yellow"
        assert TokenBudgetManager._pct_to_zone_label(0.79) == "yellow"
        assert TokenBudgetManager._pct_to_zone_label(0.8) == "red"
        assert TokenBudgetManager._pct_to_zone_label(1.0) == "red"
