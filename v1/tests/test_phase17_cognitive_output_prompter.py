"""
Phase 17 测试: 认知产出催促器 (Cognitive Output Prompter)

测试目标:
1. 催促器在连续纯读取时正确触发
2. 产出型工具重置计数器
3. 触发阈值和递减间隔正确
4. 与 format_context / compress_messages 不冲突
5. 边界情况（0 sections_read、首次循环）

运行: python3 -m pytest tests/test_phase17_cognitive_output_prompter.py -v
"""

import sys
from pathlib import Path

# 确保项目根目录在 path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.harness import Harness


# ============================================================
# Test Utilities
# ============================================================

def create_test_harness() -> Harness:
    """创建一个有基本 sections 的 Harness 用于测试。"""
    h = Harness(token_budget=200_000)
    h._paper_loaded = True
    h.state.paper_sections = {
        "abstract": "This paper studies X. " * 20,
        "introduction": "Background info. " * 50,
        "methodology": "We use DID. " * 60,
        "results": "Table 1 shows that... " * 40,
        "conclusion": "We conclude that X causes Y. " * 20,
    }
    return h


# ============================================================
# Test 1: 催促器在连续纯读取时触发
# ============================================================

def test_prompter_triggers_after_3_read_turns():
    """连续 3 轮纯读取后应该触发首次催促。"""
    h = create_test_harness()
    
    # 模拟 Agent 读了 3 个 section（通过 execute_tool 触发 sections_read 追踪）
    h.execute_tool("read_section", {"section": "abstract"})
    h.track_cognitive_output("read_section")
    h.increment_read_turn()
    
    h.execute_tool("read_section", {"section": "introduction"})
    h.track_cognitive_output("read_section")
    h.increment_read_turn()
    
    h.execute_tool("read_section", {"section": "methodology"})
    h.track_cognitive_output("read_section")
    h.increment_read_turn()
    
    # 检查催促器
    nudge = h.check_cognitive_output()
    assert nudge is not None, "应该在 3 轮纯读取后触发催促"
    assert "认知提醒" in nudge, f"首次催促应是温和提醒，got: {nudge[:50]}"
    assert "边读边记" in nudge, f"应包含边读边记建议，got: {nudge[:100]}"
    print(f"  ✓ test_prompter_triggers_after_3_read_turns")


# ============================================================
# Test 2: 2 轮纯读取不触发
# ============================================================

def test_no_trigger_before_threshold():
    """2 轮纯读取不应触发催促。"""
    h = create_test_harness()
    
    h.execute_tool("read_section", {"section": "abstract"})
    h.track_cognitive_output("read_section")
    h.increment_read_turn()
    
    h.execute_tool("read_section", {"section": "introduction"})
    h.track_cognitive_output("read_section")
    h.increment_read_turn()
    
    nudge = h.check_cognitive_output()
    assert nudge is None, f"2 轮不应触发催促，got: {nudge}"
    print(f"  ✓ test_no_trigger_before_threshold")


# ============================================================
# Test 3: update_findings 重置计数器
# ============================================================

def test_output_tool_resets_counter():
    """update_findings 应重置连续读取计数。"""
    h = create_test_harness()
    
    # 先读 2 轮
    h.execute_tool("read_section", {"section": "abstract"})
    h.track_cognitive_output("read_section")
    h.increment_read_turn()
    
    h.execute_tool("read_section", {"section": "introduction"})
    h.track_cognitive_output("read_section")
    h.increment_read_turn()
    
    # 然后产出
    h.execute_tool("update_findings", {
        "finding": "论文存在X问题",
        "priority": "high",
        "status": "needs_verification",
    })
    h.track_cognitive_output("update_findings")
    
    # 计数器应已重置
    assert h.state.consecutive_read_turns == 0, f"expected 0, got {h.state.consecutive_read_turns}"
    
    # 再读 2 轮也不应触发
    h.execute_tool("read_section", {"section": "methodology"})
    h.track_cognitive_output("read_section")
    h.increment_read_turn()
    
    h.execute_tool("read_section", {"section": "results"})
    h.track_cognitive_output("read_section")
    h.increment_read_turn()
    
    nudge = h.check_cognitive_output()
    assert nudge is None, f"产出后再读 2 轮不应触发，got: {nudge}"
    print(f"  ✓ test_output_tool_resets_counter")


# ============================================================
# Test 4: 后续催促间隔（每 2 轮再触发）
# ============================================================

def test_repeat_nudge_interval():
    """首次触发后每 2 轮再催促一次。"""
    h = create_test_harness()
    
    # 读 3 轮 → 首次触发
    for sec in ["abstract", "introduction", "methodology"]:
        h.execute_tool("read_section", {"section": sec})
        h.track_cognitive_output("read_section")
        h.increment_read_turn()
    
    nudge = h.check_cognitive_output()
    assert nudge is not None and "认知提醒" in nudge
    
    # 第 4 轮（turns_since_first = 1）→ 不触发
    h.execute_tool("read_section", {"section": "results"})
    h.track_cognitive_output("read_section")
    h.increment_read_turn()
    
    nudge = h.check_cognitive_output()
    assert nudge is None, f"第 4 轮不应触发，got: {nudge}"
    
    # 第 5 轮（turns_since_first = 2, 2%2==0）→ 触发
    h.execute_tool("read_section", {"section": "conclusion"})
    h.track_cognitive_output("read_section")
    h.increment_read_turn()
    
    nudge = h.check_cognitive_output()
    assert nudge is not None, "第 5 轮应触发再次催促"
    assert "认知警告" in nudge, f"后续催促应更强烈，got: {nudge[:50]}"
    print(f"  ✓ test_repeat_nudge_interval")


# ============================================================
# Test 5: 0 个 sections_read 时不触发
# ============================================================

def test_no_trigger_with_empty_sections_read():
    """如果 sections_read 为空（Agent 还没读任何东西），不触发。"""
    h = create_test_harness()
    
    # 不读任何 section，只是增加计数器（不应该发生，但防御性测试）
    h.state.consecutive_read_turns = 5
    
    nudge = h.check_cognitive_output()
    assert nudge is None, "sections_read 为空时不应触发"
    print(f"  ✓ test_no_trigger_with_empty_sections_read")


# ============================================================
# Test 6: findings 增长自动重置
# ============================================================

def test_findings_growth_resets():
    """如果 findings 数量增长（通过其他途径），check 时自动重置。"""
    h = create_test_harness()
    
    # 模拟 3 轮读取
    for sec in ["abstract", "introduction", "methodology"]:
        h.execute_tool("read_section", {"section": sec})
        h.track_cognitive_output("read_section")
        h.increment_read_turn()
    
    # 在 check 之前，手动往 findings 里加一条（模拟子视角注入）
    h.state.findings.append({
        "finding": "外部注入的发现",
        "priority": "medium",
        "status": "verified",
        "evidence": "...",
        "section": "methodology",
    })
    
    # check 应该发现 findings 增长了，重置计数器
    nudge = h.check_cognitive_output()
    assert nudge is None, f"findings 增长后应重置，got: {nudge}"
    assert h.state.consecutive_read_turns == 0
    print(f"  ✓ test_findings_growth_resets")


# ============================================================
# Test 7: edit_section 也算产出
# ============================================================

def test_edit_section_counts_as_output():
    """edit_section 也是产出型工具，应重置计数器。"""
    h = create_test_harness()
    
    # 读 2 轮
    h.execute_tool("read_section", {"section": "abstract"})
    h.track_cognitive_output("read_section")
    h.increment_read_turn()
    
    h.execute_tool("read_section", {"section": "introduction"})
    h.track_cognitive_output("read_section")
    h.increment_read_turn()
    
    # 修改一个 section
    h.execute_tool("edit_section", {
        "section": "abstract",
        "new_content": "Revised abstract content.",
        "reason": "Clarify claim",
    })
    h.track_cognitive_output("edit_section")
    
    assert h.state.consecutive_read_turns == 0
    print(f"  ✓ test_edit_section_counts_as_output")


# ============================================================
# Test 8: reflect_and_plan 是中性的
# ============================================================

def test_reflect_is_neutral():
    """reflect_and_plan 不重置也不增加计数器。"""
    h = create_test_harness()
    
    # 读 2 轮
    h.execute_tool("read_section", {"section": "abstract"})
    h.track_cognitive_output("read_section")
    h.increment_read_turn()
    
    h.execute_tool("read_section", {"section": "introduction"})
    h.track_cognitive_output("read_section")
    h.increment_read_turn()
    
    # 反思
    h.execute_tool("reflect_and_plan", {"trigger": "mid-review"})
    h.track_cognitive_output("reflect_and_plan")
    # 注意：reflect 轮不调 increment_read_turn（由 loop 判断这轮有非读取工具）
    
    # 连续读取计数应还是 2（reflect 不影响）
    assert h.state.consecutive_read_turns == 2
    print(f"  ✓ test_reflect_is_neutral")


# ============================================================
# Test 9: 模拟真实 E2E 行为模式
# ============================================================

def test_simulated_e2e_pattern():
    """模拟 E2E 中观察到的"读 14 轮再记录"模式，验证催促器在 Turn 3 介入。"""
    h = create_test_harness()
    
    # 模拟 Agent 逐轮读取（和 E2E 日志一致的模式）
    sections_to_read = ["abstract", "results", "introduction", "methodology", "conclusion"]
    
    nudge_turns = []
    for i, sec in enumerate(sections_to_read):
        h.execute_tool("read_section", {"section": sec})
        h.track_cognitive_output("read_section")
        h.increment_read_turn()
        
        nudge = h.check_cognitive_output()
        if nudge:
            nudge_turns.append(i + 1)
    
    # 催促器应在第 3 轮（index 2 + 1）和第 5 轮（index 4 + 1）触发
    assert 3 in nudge_turns, f"应在第 3 轮触发，实际触发轮次: {nudge_turns}"
    assert 5 in nudge_turns, f"应在第 5 轮触发，实际触发轮次: {nudge_turns}"
    print(f"  ✓ test_simulated_e2e_pattern (nudge at turns: {nudge_turns})")


# ============================================================
# Test 10: 与 compress_messages 兼容
# ============================================================

def test_compatible_with_compression():
    """催促器状态存在 state 中，不受 compress_messages 影响。"""
    h = create_test_harness()
    
    # 读 3 轮
    for sec in ["abstract", "introduction", "methodology"]:
        h.execute_tool("read_section", {"section": sec})
        h.track_cognitive_output("read_section")
        h.increment_read_turn()
    
    # 模拟压缩
    messages = [
        {"role": "system", "content": "system prompt"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "1", "type": "function", "function": {"name": "read_section", "arguments": '{"section":"abstract"}'}}]},
        {"role": "tool", "tool_call_id": "1", "content": "abstract content " * 100},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "2", "type": "function", "function": {"name": "read_section", "arguments": '{"section":"introduction"}'}}]},
        {"role": "tool", "tool_call_id": "2", "content": "intro content " * 100},
    ]
    
    compressed = h.compress_messages(messages, keep_recent=2)
    
    # 催促器状态不受影响
    assert h.state.consecutive_read_turns == 3
    nudge = h.check_cognitive_output()
    assert nudge is not None, "压缩不应影响催促器状态"
    print(f"  ✓ test_compatible_with_compression")


# ============================================================
# 运行所有测试
# ============================================================

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("Phase 17: Cognitive Output Prompter Tests")
    print("=" * 60 + "\n")
    
    tests = [
        test_prompter_triggers_after_3_read_turns,
        test_no_trigger_before_threshold,
        test_output_tool_resets_counter,
        test_repeat_nudge_interval,
        test_no_trigger_with_empty_sections_read,
        test_findings_growth_resets,
        test_edit_section_counts_as_output,
        test_reflect_is_neutral,
        test_simulated_e2e_pattern,
        test_compatible_with_compression,
    ]
    
    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"  ✗ {test.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ✗ {test.__name__}: UNEXPECTED ERROR: {e}")
            failed += 1
    
    print(f"\n{'=' * 60}")
    print(f"Results: {passed}/{passed + failed} passed")
    if failed == 0:
        print("ALL TESTS PASSED ✓")
    else:
        print(f"{failed} FAILED ✗")
    print(f"{'=' * 60}\n")
    
    sys.exit(0 if failed == 0 else 1)
