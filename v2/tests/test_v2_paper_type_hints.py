"""
tests/test_v2_paper_type_hints.py — S1: Paper-Type 自适应认知策略 单元测试（模板驱动版）

验证:
    1. CognitiveHints.is_empty() 逻辑
    2. CognitiveHints.format_for_context() 输出正确
    3. handle_generate_cognitive_hints 缺少 paper_type_description 时报错
    4. handle_generate_cognitive_hints 缺少 focus_dimensions 时报错
    5. handle_generate_cognitive_hints 正常参数返回结果
    6. handle_generate_cognitive_hints 字符串参数被规范化为列表
    7. _has_cognitive_hints condition 逻辑
    8. _compute_cognitive_hints 在 INITIAL_SCAN 注入
    9. _compute_cognitive_hints 在 DEEP_REVIEW 注入
    10. _compute_cognitive_hints 在 SYNTHESIS 阶段不注入
    11. _compute_cognitive_hints hints 为空时不注入
    12. format_for_context 认知辅助措辞检查
    13. get_gate_params 有自定义值时使用自定义值
    14. get_gate_params 无 hints 时返回默认值

运行: python3 tests/test_v2_paper_type_hints.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from dataclasses import dataclass, field

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.paper_type_hints import (
    CognitiveHints,
    handle_generate_cognitive_hints,
    get_gate_params,
    COGNITIVE_HINTS_EXAMPLES,
)
from core.assembler import _compute_cognitive_hints, _has_cognitive_hints


# ============================================================
# Mock State
# ============================================================

@dataclass
class MockState:
    cognitive_hints: CognitiveHints | None = None


# ============================================================
# Test 1: CognitiveHints.is_empty() 逻辑
# ============================================================

def test_is_empty():
    """空 hints = paper_type_description + focus_dimensions + typical_weaknesses 都空。"""
    h = CognitiveHints()
    assert h.is_empty() is True

    h2 = CognitiveHints(paper_type_description="empirical")
    assert h2.is_empty() is False

    h3 = CognitiveHints(focus_dimensions=["something"])
    assert h3.is_empty() is False

    h4 = CognitiveHints(typical_weaknesses=["weak"])
    assert h4.is_empty() is False

    print("✅ Test 1 passed: is_empty logic")


# ============================================================
# Test 2: format_for_context() 输出正确
# ============================================================

def test_format_for_context():
    """非空 hints 格式化包含所有字段。"""
    h = CognitiveHints(
        paper_type_description="使用DID的实证研究",
        focus_dimensions=["平行趋势", "数据质量"],
        typical_weaknesses=["pre-trends不满足"],
        verification_strategies=["检查event study图"],
    )
    result = h.format_for_context()
    assert "审稿认知提示" in result
    assert "使用DID的实证研究" in result
    assert "平行趋势" in result
    assert "数据质量" in result
    assert "pre-trends不满足" in result
    assert "检查event study图" in result
    assert "以上由你生成" in result
    print("✅ Test 2 passed: format_for_context output")


# ============================================================
# Test 3: handle 缺少 paper_type_description 时报错
# ============================================================

def test_handle_missing_description():
    """缺 paper_type_description → 返回错误提示 + 空 hints。"""
    response, hints = handle_generate_cognitive_hints({
        "focus_dimensions": ["something"],
    })
    assert hints.is_empty()
    assert "paper_type_description" in response
    print("✅ Test 3 passed: missing description → error")


# ============================================================
# Test 4: handle 缺少 focus_dimensions 时报错
# ============================================================

def test_handle_missing_focus():
    """缺 focus_dimensions → 返回错误提示 + 空 hints。"""
    response, hints = handle_generate_cognitive_hints({
        "paper_type_description": "实证论文",
    })
    assert hints.is_empty()
    assert "focus_dimensions" in response
    print("✅ Test 4 passed: missing focus_dimensions → error")


# ============================================================
# Test 5: handle 正常参数返回结果
# ============================================================

def test_handle_success():
    """正常参数 → 非空 hints + 成功反馈。"""
    response, hints = handle_generate_cognitive_hints({
        "paper_type_description": "RDD实证研究",
        "focus_dimensions": ["断点连续性", "带宽选择"],
        "typical_weaknesses": ["McCrary test缺失"],
        "verification_strategies": ["检查density test"],
    })
    assert not hints.is_empty()
    assert hints.paper_type_description == "RDD实证研究"
    assert len(hints.focus_dimensions) == 2
    assert len(hints.typical_weaknesses) == 1
    assert len(hints.verification_strategies) == 1
    assert "✅" in response
    print("✅ Test 5 passed: handle success")


# ============================================================
# Test 6: 字符串参数被规范化为列表
# ============================================================

def test_handle_string_normalization():
    """单个字符串参数被自动包装为列表。"""
    response, hints = handle_generate_cognitive_hints({
        "paper_type_description": "理论论文",
        "focus_dimensions": "假设合理性",  # 字符串而非列表
        "typical_weaknesses": "证明有gap",
    })
    assert not hints.is_empty()
    assert hints.focus_dimensions == ["假设合理性"]
    assert hints.typical_weaknesses == ["证明有gap"]
    print("✅ Test 6 passed: string normalization")


# ============================================================
# Test 7: _has_cognitive_hints condition 逻辑
# ============================================================

def test_condition_fn():
    """condition_fn 在各种状态下的行为。"""
    # 有非空 hints → True
    state = MockState(cognitive_hints=CognitiveHints(
        paper_type_description="test",
        focus_dimensions=["x"],
    ))
    assert _has_cognitive_hints({"state": state}) is True

    # 空 hints → False
    state = MockState(cognitive_hints=CognitiveHints())
    assert _has_cognitive_hints({"state": state}) is False

    # None → False
    state = MockState(cognitive_hints=None)
    assert _has_cognitive_hints({"state": state}) is False

    print("✅ Test 7 passed: condition_fn logic")


# ============================================================
# Test 8: _compute_cognitive_hints 在 INITIAL_SCAN 注入
# ============================================================

def test_compute_initial_scan():
    """INITIAL_SCAN 阶段 + 有 hints → 注入。"""
    hints = CognitiveHints(
        paper_type_description="DID实证",
        focus_dimensions=["平行趋势"],
    )
    state = MockState(cognitive_hints=hints)
    ctx = {"state": state, "current_phase": "initial_scan", "current_turn": 2}
    result = _compute_cognitive_hints(ctx)
    assert "审稿认知提示" in result
    assert "DID实证" in result
    print("✅ Test 8 passed: compute INITIAL_SCAN injects")


# ============================================================
# Test 9: _compute_cognitive_hints 在 DEEP_REVIEW 注入
# ============================================================

def test_compute_deep_review():
    """DEEP_REVIEW 阶段 → 注入。"""
    hints = CognitiveHints(
        paper_type_description="理论模型",
        focus_dimensions=["假设强度"],
    )
    state = MockState(cognitive_hints=hints)
    ctx = {"state": state, "current_phase": "deep_review", "current_turn": 10}
    result = _compute_cognitive_hints(ctx)
    assert "理论模型" in result
    print("✅ Test 9 passed: compute DEEP_REVIEW injects")


# ============================================================
# Test 10: _compute_cognitive_hints 在 SYNTHESIS 阶段不注入
# ============================================================

def test_compute_synthesis():
    """SYNTHESIS 阶段 → 不注入。"""
    hints = CognitiveHints(
        paper_type_description="test",
        focus_dimensions=["x"],
    )
    state = MockState(cognitive_hints=hints)
    ctx = {"state": state, "current_phase": "synthesis", "current_turn": 1}
    result = _compute_cognitive_hints(ctx)
    assert result == ""
    print("✅ Test 10 passed: compute SYNTHESIS → empty")


# ============================================================
# Test 11: _compute_cognitive_hints hints 为空时不注入
# ============================================================

def test_compute_empty_hints():
    """hints 为空 → 不注入。"""
    state = MockState(cognitive_hints=CognitiveHints())
    ctx = {"state": state, "current_phase": "initial_scan", "current_turn": 1}
    result = _compute_cognitive_hints(ctx)
    assert result == ""
    print("✅ Test 11 passed: compute empty hints → empty")


# ============================================================
# Test 12: format_for_context 认知辅助措辞检查
# ============================================================

def test_cognitive_framing():
    """输出必须包含认知辅助标志词。"""
    h = CognitiveHints(
        paper_type_description="某类论文",
        focus_dimensions=["维度A"],
    )
    result = h.format_for_context()
    assert "审稿认知提示" in result
    assert "由你" in result  # "由你基于论文内容生成" 或 "由你生成"
    assert "审稿应基于论文实际内容" in result
    print("✅ Test 12 passed: cognitive framing present")


# ============================================================
# Test 13: get_gate_params 有自定义值
# ============================================================

def test_gate_params_custom():
    """Agent 设定了自定义 gate 参数。"""
    h = CognitiveHints(gate_idle_rounds=6, min_findings_for_exit=5)
    params = get_gate_params(h)
    assert params["gate_idle_rounds"] == 6
    assert params["min_findings_for_exit"] == 5
    print("✅ Test 13 passed: gate_params custom")


# ============================================================
# Test 14: get_gate_params 无 hints 时返回默认值
# ============================================================

def test_gate_params_default():
    """无 hints → 默认值。"""
    params = get_gate_params(None)
    assert params["gate_idle_rounds"] == 4
    assert params["min_findings_for_exit"] == 3
    print("✅ Test 14 passed: gate_params default")


# ============================================================
# Run all tests
# ============================================================

if __name__ == "__main__":
    test_is_empty()
    test_format_for_context()
    test_handle_missing_description()
    test_handle_missing_focus()
    test_handle_success()
    test_handle_string_normalization()
    test_condition_fn()
    test_compute_initial_scan()
    test_compute_deep_review()
    test_compute_synthesis()
    test_compute_empty_hints()
    test_cognitive_framing()
    test_gate_params_custom()
    test_gate_params_default()
    print("\n🎉 All 14 S1 tests passed!")
