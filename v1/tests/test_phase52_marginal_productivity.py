"""
Phase 52 单元测试: 边际产出信号 (Marginal Productivity Signal)

测试场景:
1. 正常产出 — 不触发信号
2. 早期高产、近期零产出 — 触发强信号
3. 早期高产、近期低产出 — 触发弱信号
4. 数据不足 — 不触发
5. 早期也没产出 — 不触发（不是"衰减"）
6. finding 没有 recorded_at_turn 字段 — 兼容旧数据，不崩溃
7. 信号文本包含策略信息
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.harness import Harness


def make_harness_with_findings(findings: list[dict], loop_turns: int, strategy: str = "undecided") -> Harness:
    """构造一个带有预设 findings 和 loop_turns 的 Harness（不加载论文）。"""
    h = Harness(paper_path=None, max_loop_turns=50)
    h.state.findings = findings
    h.state.loop_turns = loop_turns
    h.cognitive_state.current_strategy = strategy
    return h


def test_no_signal_when_normal_productivity():
    """场景1: 产出均匀分布，不触发信号。"""
    # 12 轮，每 2 轮产出 1 条 finding (共 6 条)
    findings = [
        {"finding": f"Finding {i}", "recorded_at_turn": i * 2}
        for i in range(1, 7)
    ]
    h = make_harness_with_findings(findings, loop_turns=12)
    result = h._compute_marginal_productivity()
    assert result is None, f"Expected None (no signal), got: {result}"
    print("✅ 场景1: 正常产出不触发信号")


def test_strong_signal_when_zero_recent_output():
    """场景2: 早期高产，近期完全无产出 — 触发强信号。"""
    # 18 轮，前 6 轮产出 5 条，后 12 轮产出 0 条
    findings = [
        {"finding": f"Early finding {i}", "recorded_at_turn": i}
        for i in range(1, 6)
    ]
    h = make_harness_with_findings(findings, loop_turns=18)
    result = h._compute_marginal_productivity()
    
    assert result is not None, "Expected signal to trigger"
    signal_text = "\n".join(result)
    assert "⚠" in signal_text, f"Expected warning marker, got: {signal_text}"
    assert "没有产出任何新发现" in signal_text, f"Expected zero-output message, got: {signal_text}"
    assert "由你判断" in signal_text, "Signal should end with §4.3 non-directive"
    print("✅ 场景2: 零产出触发强信号")


def test_weak_signal_when_low_recent_output():
    """场景3: 早期高产，近期低产出 — 触发弱信号（密度降至 <40%）。"""
    # 15 轮，前 5 轮产出 4 条 (密度 0.8)，后 10 轮产出 1 条 (密度 0.1)
    # window_size = max(4, 15//3) = 5, window_start = 10
    # recent (turn >= 10): 1 条, density = 1/5 = 0.2
    # earlier (turn < 10): 4 条 in 10 turns, density = 0.4
    # decay_ratio = 0.2/0.4 = 0.5 → 不触发 (>= 0.4)
    # 调整: 让衰减更明显
    # 21 轮, 前 7 轮产出 5 条 (密度 ~0.71), 后 14 轮产出 1 条
    # window_size = max(4, 21//3) = 7, window_start = 14
    # recent (turn >= 14): 1 条 at turn 15, density = 1/7 = 0.14
    # earlier (turn < 14): 5 条 in 14 turns, density = 0.36
    # decay_ratio = 0.14/0.36 = 0.39 → 触发! (< 0.4)
    findings = [
        {"finding": f"Early finding {i}", "recorded_at_turn": i + 1}
        for i in range(5)
    ] + [
        {"finding": "Late finding", "recorded_at_turn": 15}
    ]
    h = make_harness_with_findings(findings, loop_turns=21)
    result = h._compute_marginal_productivity()
    
    assert result is not None, "Expected signal to trigger for low productivity"
    signal_text = "\n".join(result)
    # 不应该有"没有产出任何新发现"（因为有 1 条）
    assert "没有产出任何新发现" not in signal_text
    assert "降至早期的" in signal_text, f"Expected decay percentage, got: {signal_text}"
    print("✅ 场景3: 低产出触发弱信号")


def test_no_signal_when_insufficient_data():
    """场景4: findings 不足 2 条，不触发。"""
    findings = [{"finding": "Only one", "recorded_at_turn": 3}]
    h = make_harness_with_findings(findings, loop_turns=10)
    result = h._compute_marginal_productivity()
    assert result is None, f"Expected None with insufficient data, got: {result}"
    print("✅ 场景4: 数据不足不触发")


def test_no_signal_when_early_also_empty():
    """场景5: 早期也没产出（所有 findings 都在近期窗口内），不触发。"""
    # 8 轮, window_size = max(4, 8//3) = 4, window_start = 4
    # 所有 findings 在 turn 5, 6 → 都在近期窗口内
    # earlier_findings = 0, earlier_density = 0 → 不触发
    findings = [
        {"finding": "Recent 1", "recorded_at_turn": 5},
        {"finding": "Recent 2", "recorded_at_turn": 6},
    ]
    h = make_harness_with_findings(findings, loop_turns=8)
    result = h._compute_marginal_productivity()
    assert result is None, f"Expected None when early period is empty, got: {result}"
    print("✅ 场景5: 早期无产出不触发")


def test_backward_compat_no_turn_field():
    """场景6: 旧 findings 没有 recorded_at_turn 字段，不崩溃。"""
    findings = [
        {"finding": "Old finding without turn info"},
        {"finding": "Another old finding"},
    ]
    h = make_harness_with_findings(findings, loop_turns=15)
    result = h._compute_marginal_productivity()
    # 没有 recorded_at_turn 的 findings 被过滤掉，数据不足 → None
    assert result is None, f"Expected None for legacy findings, got: {result}"
    print("✅ 场景6: 兼容旧数据不崩溃")


def test_signal_includes_strategy_info():
    """场景7: 信号文本包含当前策略信息。"""
    findings = [
        {"finding": f"Early {i}", "recorded_at_turn": i}
        for i in range(1, 6)
    ]
    h = make_harness_with_findings(findings, loop_turns=18, strategy="deep_investigation")
    result = h._compute_marginal_productivity()
    
    assert result is not None, "Expected signal to trigger"
    signal_text = "\n".join(result)
    assert "深度追查" in signal_text, f"Expected strategy label in signal, got: {signal_text}"
    print("✅ 场景7: 信号包含策略信息")


def test_no_signal_when_turns_too_few():
    """额外场景: loop_turns < 6 时不触发（在 reflect_and_plan 中有前置条件）。"""
    # 这个测试直接调用 _compute_marginal_productivity，
    # 但在实际使用中 reflect_and_plan 有 `if s.loop_turns >= 6` 的前置条件
    findings = [
        {"finding": "F1", "recorded_at_turn": 1},
        {"finding": "F2", "recorded_at_turn": 2},
    ]
    h = make_harness_with_findings(findings, loop_turns=5)
    # window_size = max(4, 5//3) = 4, window_start = 1
    # recent (turn >= 1): 2 条, earlier (turn < 1): 0 条
    # earlier_density = 0 → None
    result = h._compute_marginal_productivity()
    assert result is None, f"Expected None for early turns, got: {result}"
    print("✅ 额外: 轮次过少不触发")


def test_integration_with_reflect_and_plan():
    """集成测试: 验证 reflect_and_plan 输出中包含边际产出信号。"""
    # 构造一个有论文的 Harness
    h = Harness(paper_path=None, max_loop_turns=50)
    h.state.paper_sections = {"introduction": "Some intro text", "methodology": "Some method"}
    h.state.loop_turns = 20
    h.state.sections_read = ["introduction", "methodology"]
    
    # 模拟早期高产、近期零产出
    h.state.findings = [
        {"finding": f"Finding {i}", "priority": "high", "status": "verified",
         "section": "methodology", "evidence": "", "recorded_at_turn": i + 1}
        for i in range(5)
    ]
    
    # 调用 reflect_and_plan
    result = h._tool_reflect_and_plan({"trigger": "test", "current_thinking": "testing"})
    
    assert "边际产出" in result, f"Expected '边际产出' section in reflect output"
    assert "没有产出任何新发现" in result or "降至早期的" in result, \
        f"Expected productivity signal content in reflect output"
    print("✅ 集成测试: reflect_and_plan 正确包含边际产出信号")


if __name__ == "__main__":
    test_no_signal_when_normal_productivity()
    test_strong_signal_when_zero_recent_output()
    test_weak_signal_when_low_recent_output()
    test_no_signal_when_insufficient_data()
    test_no_signal_when_early_also_empty()
    test_backward_compat_no_turn_field()
    test_signal_includes_strategy_info()
    test_no_signal_when_turns_too_few()
    test_integration_with_reflect_and_plan()
    
    print("\n" + "=" * 60)
    print("Phase 52 全部测试通过! 边际产出信号机制工作正常。")
    print("=" * 60)
