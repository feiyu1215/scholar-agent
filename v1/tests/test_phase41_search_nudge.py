"""
Phase 41 测试: 搜索缺失催促器 (Search Nudge)

测试目标:
1. 条件 C 基本触发：从未搜索 + 2+ findings + 8+ 轮 → 触发搜索催促
2. 不满足条件时不触发：findings < 2 或 turns < 8 或已搜索过
3. 催促只触发一次
4. 与条件 A/B 互不干扰
5. reflect_and_plan 镜子中的"外部验证"改进：读 4+ sections 无 findings 时也显示警告

运行: python3 -m pytest tests/test_phase41_search_nudge.py -v
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
        "methodology": "We use DID approach. " * 60,
        "results": "Table 1 shows that... " * 40,
        "4.1": "Subsection 4.1 content. " * 30,
        "4.2": "Subsection 4.2 content. " * 30,
        "4.3": "Subsection 4.3 content. " * 30,
        "4.4": "Subsection 4.4 content. " * 30,
        "conclusion": "We conclude that X causes Y. " * 20,
    }
    return h


def simulate_turns(h: Harness, n: int):
    """模拟 n 轮 loop turns（不做实际工具调用，只增加计数器）。"""
    for _ in range(n):
        h.increment_turn()


# ============================================================
# Test 1: 条件 C 基本触发
# ============================================================

def test_search_nudge_triggers_when_conditions_met():
    """从未搜索 + 2+ findings + 8+ 轮 → 应触发搜索催促。"""
    h = create_test_harness()
    
    # 模拟 Agent 读了几个 section 并产出 findings
    h.execute_tool("read_section", {"section": "abstract"})
    h.execute_tool("read_section", {"section": "introduction"})
    h.execute_tool("read_section", {"section": "methodology"})
    h.execute_tool("read_section", {"section": "results"})
    
    # Agent 反思过（条件 A 不会触发）
    h.execute_tool("reflect_and_plan", {"trigger": "看看全局"})
    
    # 产出 2 条 findings
    h.execute_tool("update_findings", {
        "finding": "方法论有问题", "priority": "high", "evidence": "p.5"
    })
    h.execute_tool("update_findings", {
        "finding": "数据不一致", "priority": "medium", "evidence": "Table 1"
    })
    
    # 模拟到 Turn 8+
    simulate_turns(h, 8)
    
    # 检查搜索催促
    nudge = h.check_reflection_needed()
    assert nudge is not None, "应该在满足条件 C 时触发搜索催促"
    assert "外部校准" in nudge or "外部文献" in nudge, f"催促应提到外部文献，got: {nudge[:100]}"
    assert "search_literature" in nudge, f"催促应提到 search_literature，got: {nudge[:150]}"
    print(f"  ✓ 条件 C 正确触发搜索催促")
    print(f"    催促内容: {nudge[:120]}...")


# ============================================================
# Test 2: findings < 2 时不触发
# ============================================================

def test_no_search_nudge_with_few_findings():
    """只有 1 条 finding → 不应触发条件 C。"""
    h = create_test_harness()
    
    # 反思过（跳过条件 A）
    h.execute_tool("read_section", {"section": "abstract"})
    h.execute_tool("reflect_and_plan", {"trigger": "初步看看"})
    
    # 只有 1 条 finding
    h.execute_tool("update_findings", {
        "finding": "只有一条发现", "priority": "medium", "evidence": "p.1"
    })
    
    # 模拟到 Turn 10
    simulate_turns(h, 10)
    
    nudge = h.check_reflection_needed()
    # 条件 C 需要 findings >= 2，所以不应触发
    # 但条件 B 可能触发（如果 finding 是 needs_verification）
    # 为了隔离测试条件 C，让 finding 是 verified 状态
    # 重新设置
    h2 = create_test_harness()
    h2.execute_tool("read_section", {"section": "abstract"})
    h2.execute_tool("reflect_and_plan", {"trigger": "初步看看"})
    h2.execute_tool("update_findings", {
        "finding": "已验证的发现", "priority": "medium", 
        "evidence": "p.1", "status": "verified"
    })
    simulate_turns(h2, 10)
    
    nudge2 = h2.check_reflection_needed()
    # 只有 1 条 finding，条件 C 不满足
    assert nudge2 is None, f"1 条 finding 不应触发条件 C，got: {nudge2}"
    print(f"  ✓ findings < 2 时不触发")


# ============================================================
# Test 3: turns < 8 时不触发
# ============================================================

def test_no_search_nudge_before_turn_8():
    """Turn 7 时即使有 2+ findings 也不触发条件 C。"""
    h = create_test_harness()
    
    h.execute_tool("read_section", {"section": "abstract"})
    h.execute_tool("reflect_and_plan", {"trigger": "看看"})
    
    h.execute_tool("update_findings", {
        "finding": "发现1", "priority": "high", 
        "evidence": "p.1", "status": "verified"
    })
    h.execute_tool("update_findings", {
        "finding": "发现2", "priority": "medium", 
        "evidence": "p.2", "status": "verified"
    })
    
    # 只到 Turn 7
    simulate_turns(h, 7)
    
    nudge = h.check_reflection_needed()
    assert nudge is None, f"Turn 7 不应触发条件 C，got: {nudge}"
    print(f"  ✓ turns < 8 时不触发")


# ============================================================
# Test 4: 已搜索过则不触发
# ============================================================

def test_no_search_nudge_if_already_searched():
    """如果 Agent 已经搜索过，条件 C 不触发。"""
    h = create_test_harness()
    
    h.execute_tool("read_section", {"section": "abstract"})
    h.execute_tool("reflect_and_plan", {"trigger": "看看"})
    
    # Agent 搜索了
    h.execute_tool("search_literature", {"query": "DID method limitations"})
    
    h.execute_tool("update_findings", {
        "finding": "发现1", "priority": "high", 
        "evidence": "p.1", "status": "verified"
    })
    h.execute_tool("update_findings", {
        "finding": "发现2", "priority": "medium", 
        "evidence": "p.2", "status": "verified"
    })
    
    simulate_turns(h, 10)
    
    nudge = h.check_reflection_needed()
    assert nudge is None, f"已搜索过不应触发条件 C，got: {nudge}"
    print(f"  ✓ 已搜索过不触发")


# ============================================================
# Test 5: 催促只触发一次
# ============================================================

def test_search_nudge_fires_only_once():
    """条件 C 催促只触发一次。"""
    h = create_test_harness()
    
    h.execute_tool("read_section", {"section": "abstract"})
    h.execute_tool("reflect_and_plan", {"trigger": "看看"})
    
    h.execute_tool("update_findings", {
        "finding": "发现1", "priority": "high", 
        "evidence": "p.1", "status": "verified"
    })
    h.execute_tool("update_findings", {
        "finding": "发现2", "priority": "medium", 
        "evidence": "p.2", "status": "verified"
    })
    
    simulate_turns(h, 8)
    
    # 第一次触发
    nudge1 = h.check_reflection_needed()
    assert nudge1 is not None, "首次应触发"
    
    # 继续几轮
    simulate_turns(h, 3)
    
    # 第二次不应触发
    nudge2 = h.check_reflection_needed()
    assert nudge2 is None, f"催促应只触发一次，got: {nudge2}"
    print(f"  ✓ 搜索催促只触发一次")


# ============================================================
# Test 6: 条件 B 和 C 的优先级——B 先触发
# ============================================================

def test_condition_b_takes_priority_over_c():
    """当条件 B 和 C 同时满足时，B 先触发（因为代码顺序）。"""
    h = create_test_harness()
    
    h.execute_tool("read_section", {"section": "abstract"})
    h.execute_tool("reflect_and_plan", {"trigger": "看看"})
    
    # 产出 needs_verification findings（满足条件 B）
    # 注意：必须显式传 status="needs_verification"，默认是 "suggestion"
    h.execute_tool("update_findings", {
        "finding": "待验证发现1", "priority": "high", 
        "evidence": "p.1", "status": "needs_verification"
    })
    h.execute_tool("update_findings", {
        "finding": "待验证发现2", "priority": "medium", 
        "evidence": "p.2", "status": "needs_verification"
    })
    
    # 模拟到 Turn 8+（同时满足条件 B 的 4+ 轮和条件 C 的 8+ 轮）
    simulate_turns(h, 8)
    
    # 条件 B 应该先触发（追查提醒）
    nudge = h.check_reflection_needed()
    assert nudge is not None, "应该触发催促"
    assert "追查提醒" in nudge, f"条件 B 应先触发，got: {nudge[:80]}"
    print(f"  ✓ 条件 B 优先于条件 C")


# ============================================================
# Test 7: reflect_and_plan 镜子中的"外部验证"改进
# ============================================================

def test_reflect_mirror_shows_search_warning_with_many_sections():
    """读 4+ sections 但无 findings 时，反思镜子也应显示搜索警告。"""
    h = create_test_harness()
    
    # 读 5 个 section，不产出 findings
    h.execute_tool("read_section", {"section": "abstract"})
    h.execute_tool("read_section", {"section": "introduction"})
    h.execute_tool("read_section", {"section": "methodology"})
    h.execute_tool("read_section", {"section": "results"})
    h.execute_tool("read_section", {"section": "conclusion"})
    
    # 调用 reflect_and_plan，检查镜子内容
    result = h.execute_tool("reflect_and_plan", {"trigger": "看看全局"})
    
    # 镜子应该包含搜索警告（Phase 41 新增的 elif 分支）
    assert "尚未查过外部文献" in result, f"镜子应显示搜索警告，got: {result[:300]}"
    assert "已读了" in result or "section" in result, f"应提到已读 section 数量"
    print(f"  ✓ 反思镜子在读多 sections 无 findings 时显示搜索警告")


# ============================================================
# Test 8: reflect_and_plan 镜子中有 findings 时的搜索警告
# ============================================================

def test_reflect_mirror_shows_stronger_warning_with_findings():
    """有 findings 但没搜索时，反思镜子显示更强的警告。"""
    h = create_test_harness()
    
    h.execute_tool("read_section", {"section": "abstract"})
    h.execute_tool("read_section", {"section": "methodology"})
    
    # 产出 finding
    h.execute_tool("update_findings", {
        "finding": "方法论问题", "priority": "high", "evidence": "p.3"
    })
    
    # 调用 reflect_and_plan
    result = h.execute_tool("reflect_and_plan", {"trigger": "检查方向"})
    
    # 应该显示"有发现但没搜索"的警告
    assert "有发现但尚未查过外部文献" in result, f"应显示有发现无搜索警告，got: {result[:300]}"
    assert "外部文献校准" in result, f"应提到外部校准"
    print(f"  ✓ 有 findings 时显示更强的搜索警告")


# ============================================================
# Test 9: 搜索后镜子不再显示警告
# ============================================================

def test_reflect_mirror_no_warning_after_search():
    """搜索过之后，反思镜子不应显示搜索警告。"""
    h = create_test_harness()
    
    h.execute_tool("read_section", {"section": "abstract"})
    h.execute_tool("read_section", {"section": "methodology"})
    
    h.execute_tool("update_findings", {
        "finding": "方法论问题", "priority": "high", "evidence": "p.3"
    })
    
    # Agent 搜索了
    h.execute_tool("search_literature", {"query": "DID limitations"})
    
    # 调用 reflect_and_plan
    result = h.execute_tool("reflect_and_plan", {"trigger": "检查方向"})
    
    # 不应显示搜索警告
    assert "尚未查过外部文献" not in result, f"搜索后不应显示警告，got: {result[:300]}"
    print(f"  ✓ 搜索后镜子不再显示警告")


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    print("\n=== Phase 41 搜索缺失催促器测试 ===\n")
    
    test_search_nudge_triggers_when_conditions_met()
    test_no_search_nudge_with_few_findings()
    test_no_search_nudge_before_turn_8()
    test_no_search_nudge_if_already_searched()
    test_search_nudge_fires_only_once()
    test_condition_b_takes_priority_over_c()
    test_reflect_mirror_shows_search_warning_with_many_sections()
    test_reflect_mirror_shows_stronger_warning_with_findings()
    test_reflect_mirror_no_warning_after_search()
    
    print(f"\n✅ 全部 9 个测试通过！")
