"""
Phase 37 测试: 反思催促器 (Reflection Nudge)

测试目标:
1. 读 4+ sections 且从未 reflect_and_plan → 触发催促
2. 读 3 sections → 不触发
3. 调用过 reflect_and_plan → 不触发
4. 催促只触发一次（不重复）
5. 与 Phase 17 认知催促器互不干扰

运行: python3 -m pytest tests/test_phase37_reflection_nudge.py -v
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
# Test 1: 读 4 sections 且从未反思 → 触发催促
# ============================================================

def test_nudge_triggers_after_4_sections_without_reflection():
    """读 4 个 section 且从未调用 reflect_and_plan → 应触发反思催促。"""
    h = create_test_harness()
    
    # 模拟 Agent 读了 4 个 section
    h.execute_tool("read_section", {"section": "abstract"})
    h.execute_tool("read_section", {"section": "introduction"})
    h.execute_tool("read_section", {"section": "methodology"})
    h.execute_tool("read_section", {"section": "results"})
    
    # 检查反思催促器
    nudge = h.check_reflection_needed()
    assert nudge is not None, "应该在读 4 个 section 后触发反思催促"
    assert "reflect_and_plan" in nudge, f"催促应提到 reflect_and_plan，got: {nudge[:100]}"
    assert "轻提醒" in nudge, f"催促应是轻提醒语气，got: {nudge[:50]}"
    print(f"  ✓ 读 4 sections 后触发反思催促")


# ============================================================
# Test 2: 读 3 sections → 不触发
# ============================================================

def test_no_nudge_before_threshold():
    """读 3 个 section → 不应触发反思催促。"""
    h = create_test_harness()
    
    h.execute_tool("read_section", {"section": "abstract"})
    h.execute_tool("read_section", {"section": "introduction"})
    h.execute_tool("read_section", {"section": "methodology"})
    
    nudge = h.check_reflection_needed()
    assert nudge is None, f"3 sections 不应触发催促，got: {nudge}"
    print(f"  ✓ 3 sections 不触发")


# ============================================================
# Test 3: 调用过 reflect_and_plan → 不触发
# ============================================================

def test_no_nudge_if_already_reflected():
    """如果 Agent 已经调用过 reflect_and_plan，即使读了 5 sections 也不催促。"""
    h = create_test_harness()
    
    # 先读 2 个 section
    h.execute_tool("read_section", {"section": "abstract"})
    h.execute_tool("read_section", {"section": "introduction"})
    
    # Agent 主动反思
    h.execute_tool("reflect_and_plan", {
        "trigger": "读了两个 section，先看看全局方向"
    })
    
    # 继续读更多 sections
    h.execute_tool("read_section", {"section": "methodology"})
    h.execute_tool("read_section", {"section": "results"})
    h.execute_tool("read_section", {"section": "conclusion"})
    
    # 不应触发催促（因为已经反思过）
    nudge = h.check_reflection_needed()
    assert nudge is None, f"已反思过不应催促，got: {nudge}"
    print(f"  ✓ 已反思过不触发")


# ============================================================
# Test 4: 催促只触发一次
# ============================================================

def test_nudge_fires_only_once():
    """催促只触发一次，之后不再重复。"""
    h = create_test_harness()
    
    # 读 4 个 section
    h.execute_tool("read_section", {"section": "abstract"})
    h.execute_tool("read_section", {"section": "introduction"})
    h.execute_tool("read_section", {"section": "methodology"})
    h.execute_tool("read_section", {"section": "results"})
    
    # 第一次触发
    nudge1 = h.check_reflection_needed()
    assert nudge1 is not None, "首次应触发"
    
    # 继续读
    h.execute_tool("read_section", {"section": "conclusion"})
    
    # 第二次不应触发
    nudge2 = h.check_reflection_needed()
    assert nudge2 is None, f"催促应只触发一次，got: {nudge2}"
    print(f"  ✓ 催促只触发一次")


# ============================================================
# Test 5: 与 Phase 17 认知催促器互不干扰
# ============================================================

def test_reflection_nudge_independent_of_cognitive_nudge():
    """反思催促器和认知催促器是独立的两个机制。"""
    h = create_test_harness()
    
    # 模拟 Agent 读了 4 个 section 但有产出（update_findings）
    h.execute_tool("read_section", {"section": "abstract"})
    h.track_cognitive_output("read_section")
    h.increment_read_turn()
    
    h.execute_tool("read_section", {"section": "introduction"})
    h.track_cognitive_output("read_section")
    h.increment_read_turn()
    
    # Agent 产出了 finding（重置认知催促器）
    h.execute_tool("update_findings", {
        "finding": "Test finding", "priority": "medium", "evidence": "p.1"
    })
    h.track_cognitive_output("update_findings")
    
    h.execute_tool("read_section", {"section": "methodology"})
    h.track_cognitive_output("read_section")
    h.increment_read_turn()
    
    h.execute_tool("read_section", {"section": "results"})
    h.track_cognitive_output("read_section")
    h.increment_read_turn()
    
    # 认知催促器不应触发（因为有产出）
    cognitive_nudge = h.check_cognitive_output()
    # 反思催促器应该触发（因为从未 reflect_and_plan）
    reflection_nudge = h.check_reflection_needed()
    
    assert reflection_nudge is not None, "反思催促应独立于认知催促触发"
    print(f"  ✓ 两个催促器独立运作")
    print(f"    认知催促: {'触发' if cognitive_nudge else '未触发'}")
    print(f"    反思催促: {'触发' if reflection_nudge else '未触发'}")


# ============================================================
# Test 6: 初始状态不触发
# ============================================================

def test_no_nudge_on_fresh_harness():
    """刚创建的 Harness（0 sections read）不应触发。"""
    h = create_test_harness()
    
    nudge = h.check_reflection_needed()
    assert nudge is None, f"初始状态不应触发，got: {nudge}"
    print(f"  ✓ 初始状态不触发")
