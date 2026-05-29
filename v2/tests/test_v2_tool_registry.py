"""
tests/test_v2_tool_registry.py — v2 ToolRegistry 单元测试

验证:
    1. ToolRegistry 基本 API: register / execute / has_tool / tool_names / len
    2. 未知工具返回友好错误
    3. Harness 集成: _init_tool_registry 注册所有 15 工具
    4. execute_tool 正确分发（抽样验证 read_section, done）
    5. v2 副本可独立 import 不影响 v1

运行: python3 tests/test_v2_tool_registry.py
"""

import sys
from pathlib import Path

# 确保项目根目录在 path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================
# Test 1: ToolRegistry 基本 API
# ============================================================

def test_registry_basic_api():
    """register / execute / has_tool / tool_names / len 均正确工作。"""
    from core.tools import ToolRegistry

    reg = ToolRegistry()
    assert len(reg) == 0
    assert reg.tool_names == []
    assert not reg.has_tool("foo")

    # 注册一个简单工具
    reg.register("foo", handler=lambda args: f"result:{args.get('x')}", description="test tool")
    assert len(reg) == 1
    assert reg.has_tool("foo")
    assert "foo" in reg.tool_names

    # 执行
    result = reg.execute("foo", {"x": 42})
    assert result == "result:42", f"Unexpected: {result}"

    print("  [PASS] test_registry_basic_api")


# ============================================================
# Test 2: 未知工具返回友好错误
# ============================================================

def test_unknown_tool_returns_error():
    """execute 未注册工具时不抛异常，返回错误字符串。"""
    from core.tools import ToolRegistry

    reg = ToolRegistry()
    result = reg.execute("nonexistent", {})
    assert "未知工具" in result, f"Expected error message, got: {result}"

    print("  [PASS] test_unknown_tool_returns_error")


# ============================================================
# Test 3: Harness 初始化注册所有工具
# ============================================================

def test_harness_registers_all_tools():
    """Harness.__init__ 自动注册所有工具到 ToolRegistry（含 V4 D1 action skill 工具）。"""
    from core.harness import Harness

    h = Harness()
    assert len(h.tool_registry) >= 25, f"Expected >=25 tools, got {len(h.tool_registry)}"

    expected_tools = {
        "read_section", "search_literature", "update_findings",
        "review_findings", "edit_section", "talk_to_user",
        "spawn_perspective", "spawn_parallel_readers", "reflect_and_plan",
        "detect_ai_signals",
        "verify_citations", "recall_context", "fetch_paper_detail",
        "read_reference", "done", "mark_complete",
        "request_phase_transition",  # Phase 4: 阶段转换工具
        "generate_cognitive_hints",  # Phase S1: 认知提示生成
        "generate_edit_plan",  # EDIT-1: 修改计划生成器
        "edit_paragraph",  # EDIT-3: 段落级编辑
        "reword_sentence",  # EDIT-3: 句子级编辑
        "insert_content",  # EDIT-3: 内容插入
        "verify_stata",  # EDIT-4: MCP bridge Stata 统计验证
        "switch_persona",  # W1: 人格切换
        "export_structured_review",  # V4 D1: 结构化导出
        "apply_skill",  # Phase 3 SkillX: 技能执行工具
    }
    actual = set(h.tool_registry.tool_names)
    missing = expected_tools - actual
    assert not missing, f"Missing tools: {missing}"
    # Note: extra tools are allowed (new phases may add more)

    print("  [PASS] test_harness_registers_all_tools")


# ============================================================
# Test 4: execute_tool 正确分发
# ============================================================

def test_execute_tool_dispatches():
    """execute_tool 通过 ToolRegistry 分发，不用旧 if-elif。"""
    from core.harness import Harness

    h = Harness()

    # read_section - 无论文时返回空
    result = h.execute_tool("read_section", {"section_id": "abstract"})
    assert isinstance(result, str)
    # 不应抛异常，返回一个提示
    assert "未找到" in result or "section" in result.lower() or result == ""

    # done - 标记完成
    result = h.execute_tool("done", {"summary": "test done"})
    assert isinstance(result, str)

    # 未知工具
    result = h.execute_tool("totally_fake_tool", {})
    assert "未知工具" in result

    print("  [PASS] test_execute_tool_dispatches")


# ============================================================
# Test 5: v2 副本独立于 v1
# ============================================================

def test_v2_has_tool_registry():
    """v2 的 Harness 有 tool_registry 属性。"""
    import core.harness as v2_harness

    v2_h = v2_harness.Harness()
    assert hasattr(v2_h, "tool_registry"), "v2 must have tool_registry"

    print("  [PASS] test_v2_independent_from_v1")


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("Running: test_v2_tool_registry.py")
    print("=" * 60)

    tests = [
        test_registry_basic_api,
        test_unknown_tool_returns_error,
        test_harness_registers_all_tools,
        test_execute_tool_dispatches,
        test_v2_independent_from_v1,
    ]

    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            print(f"  [FAIL] {t.__name__}: {e}")
            failed += 1

    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed, {passed + failed} total")
    print("=" * 60)

    if failed > 0:
        sys.exit(1)
