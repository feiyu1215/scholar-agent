"""
Phase 55 单元测试: CognitiveChecker Persona 适配 + 停滞检测主动呈现

两个已知限制的修复:
1. CognitiveChecker 在非学术场景（如代码审阅）下使用学术 prompt 导致误判
   → 解决: Checker 根据 persona 动态选择 prompt 模板
2. Agent 缺乏"原地打转"自我感知（只在 reflect_and_plan 中看到产出密度）
   → 解决: 在 execute_tool 层面主动检测停滞并注入信号

测试场景:
1. CognitiveChecker 默认使用学术 prompt（向后兼容）
2. CognitiveChecker 在 code_reviewer persona 下使用通用 prompt
3. CognitiveChecker.set_persona 动态切换
4. Harness 接受 persona 参数并传递给 Checker
5. 停滞检测: 热身期内不触发（< 6 轮）
6. 停滞检测: 最近有 update_findings 时不触发
7. 停滞检测: 连续无产出时触发信号
8. 停滞检测: 冷却期内不重复触发
9. 停滞检测: 元认知工具上不触发
10. 停滞检测: 信号内容是数据呈现（不含指令）
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.checker import (
    CognitiveChecker,
    PERSONA_TASK_CONTEXTS,
    POST_EDIT_CHECK_PROMPT,
    POST_EDIT_CHECK_PROMPT_GENERIC,
    PRE_COMPLETION_CHECK_PROMPT_GENERIC,
)
from core.harness import Harness


# ============================================================
# Test 1: CognitiveChecker 默认使用学术 prompt
# ============================================================

def test_1_checker_default_scholar():
    """默认 persona='scholar'，使用学术审稿 prompt。"""
    checker = CognitiveChecker()
    assert checker._persona == "scholar"
    assert checker._task_context == PERSONA_TASK_CONTEXTS["scholar"]
    assert checker._task_context["task_domain"] == "学术"
    assert checker._task_context["reviewer_role"] == "审稿人"
    print("✓ Test 1 passed: CognitiveChecker defaults to scholar persona")


# ============================================================
# Test 2: CognitiveChecker code_reviewer persona
# ============================================================

def test_2_checker_code_reviewer():
    """code_reviewer persona 使用代码审阅 prompt。"""
    checker = CognitiveChecker(persona="code_reviewer")
    assert checker._persona == "code_reviewer"
    assert checker._task_context["task_domain"] == "代码"
    assert checker._task_context["reviewer_role"] == "代码审阅者"
    print("✓ Test 2 passed: CognitiveChecker uses code_reviewer context")


# ============================================================
# Test 3: set_persona 动态切换
# ============================================================

def test_3_set_persona_dynamic():
    """set_persona 可以在运行时动态切换 persona。"""
    checker = CognitiveChecker(persona="scholar")
    assert checker._persona == "scholar"

    checker.set_persona("code_reviewer")
    assert checker._persona == "code_reviewer"
    assert checker._task_context["task_domain"] == "代码"

    # 切换到未知 persona 时 task_context 为空（降级到学术默认）
    checker.set_persona("unknown_persona")
    assert checker._persona == "unknown_persona"
    assert checker._task_context == {}

    print("✓ Test 3 passed: set_persona dynamically switches persona")


# ============================================================
# Test 4: Harness 传递 persona 给 Checker
# ============================================================

def test_4_harness_passes_persona():
    """Harness 初始化时将 persona 传递给 CognitiveChecker。"""
    # 默认 scholar
    h1 = Harness(paper_path=None)
    assert h1._persona == "scholar"
    assert h1.checker._persona == "scholar"

    # code_reviewer
    h2 = Harness(paper_path=None, persona="code_reviewer")
    assert h2._persona == "code_reviewer"
    assert h2.checker._persona == "code_reviewer"
    assert h2.checker._task_context["task_domain"] == "代码"

    print("✓ Test 4 passed: Harness passes persona to CognitiveChecker")


# ============================================================
# Test 5: 停滞检测 — 热身期不触发
# ============================================================

def test_5_stagnation_warmup_no_trigger():
    """前 6 轮不触发停滞信号（热身期）。"""
    h = Harness(paper_path=None)
    h.state.paper_sections = {"intro": "Some content here for testing."}
    h._paper_loaded = True
    h.state.loop_turns = 5  # < 6

    signal = h._check_stagnation("read_section")
    assert signal is None
    print("✓ Test 5 passed: no stagnation signal during warmup (< 6 turns)")


# ============================================================
# Test 6: 停滞检测 — 最近有 update_findings 不触发
# ============================================================

def test_6_stagnation_recent_update_no_trigger():
    """最近 5 轮内有 update_findings 时不触发。"""
    h = Harness(paper_path=None)
    h.state.paper_sections = {"intro": "Content"}
    h._paper_loaded = True
    h.state.loop_turns = 10

    # 模拟最近有 update_findings
    h.state.tool_call_history = [
        {"name": "read_section", "input": {}},
        {"name": "read_section", "input": {}},
        {"name": "read_section", "input": {}},
        {"name": "update_findings", "input": {}},  # 在最近 5 轮内
        {"name": "read_section", "input": {}},
    ]

    signal = h._check_stagnation("read_section")
    assert signal is None
    print("✓ Test 6 passed: no stagnation signal when recent update_findings exists")


# ============================================================
# Test 7: 停滞检测 — 连续无产出时触发
# ============================================================

def test_7_stagnation_triggers_on_no_output():
    """连续多轮无 update_findings 且无新 findings 时触发信号。"""
    h = Harness(paper_path=None)
    h.state.paper_sections = {"intro": "Content", "methods": "Methods"}
    h._paper_loaded = True
    h.state.loop_turns = 12
    h.state.sections_read = ["intro", "methods"]

    # 有一些早期 findings
    h.state.findings = [
        {"finding": "Issue A", "priority": "high", "status": "verified", "recorded_at_turn": 3},
        {"finding": "Issue B", "priority": "medium", "status": "verified", "recorded_at_turn": 4},
    ]

    # 最近 7 轮都是 read_section，没有 update_findings
    h.state.tool_call_history = [
        {"name": "update_findings", "input": {}},  # turn 4
        {"name": "read_section", "input": {}},
        {"name": "read_section", "input": {}},
        {"name": "read_section", "input": {}},
        {"name": "read_section", "input": {}},
        {"name": "read_section", "input": {}},
        {"name": "read_section", "input": {}},
        {"name": "read_section", "input": {}},
    ]

    signal = h._check_stagnation("read_section")
    assert signal is not None, "Expected stagnation signal"
    assert "产出观察" in signal
    assert "未产出新发现" in signal
    assert "2 条 findings" in signal
    # 确认是数据呈现，不含指令性语言
    assert "应该" not in signal
    assert "必须" not in signal
    assert "请" not in signal

    print("✓ Test 7 passed: stagnation signal triggers on prolonged no-output")


# ============================================================
# Test 8: 停滞检测 — 冷却期不重复触发
# ============================================================

def test_8_stagnation_cooldown():
    """触发后 3 轮内不再重复触发。"""
    h = Harness(paper_path=None)
    h.state.paper_sections = {"intro": "Content"}
    h._paper_loaded = True
    h.state.loop_turns = 12
    h.state.sections_read = ["intro"]
    h.state.findings = [
        {"finding": "Issue", "priority": "high", "status": "verified", "recorded_at_turn": 3},
    ]
    h.state.tool_call_history = [
        {"name": "read_section", "input": {}},
        {"name": "read_section", "input": {}},
        {"name": "read_section", "input": {}},
        {"name": "read_section", "input": {}},
        {"name": "read_section", "input": {}},
        {"name": "read_section", "input": {}},
    ]

    # 第一次触发
    signal1 = h._check_stagnation("read_section")
    assert signal1 is not None

    # 同一轮再次调用（冷却期内）
    signal2 = h._check_stagnation("read_section")
    assert signal2 is None, "Should not trigger again within cooldown"

    # 模拟过了 3 轮
    h.state.loop_turns = 16
    h.state.tool_call_history.extend([
        {"name": "read_section", "input": {}},
        {"name": "read_section", "input": {}},
        {"name": "read_section", "input": {}},
    ])
    signal3 = h._check_stagnation("read_section")
    assert signal3 is not None, "Should trigger again after cooldown"

    print("✓ Test 8 passed: stagnation signal respects cooldown period")


# ============================================================
# Test 9: 停滞检测 — 元认知工具不触发
# ============================================================

def test_9_stagnation_no_trigger_on_meta_tools():
    """reflect_and_plan, review_findings, mark_complete 等不触发停滞信号。"""
    h = Harness(paper_path=None)
    h.state.paper_sections = {"intro": "Content"}
    h._paper_loaded = True
    h.state.loop_turns = 15
    h.state.findings = [
        {"finding": "Issue", "priority": "high", "status": "verified", "recorded_at_turn": 3},
    ]
    h.state.tool_call_history = [{"name": "read_section", "input": {}}] * 10

    meta_tools = ["reflect_and_plan", "review_findings", "mark_complete", "done", "talk_to_user"]
    for tool in meta_tools:
        signal = h._check_stagnation(tool)
        assert signal is None, f"Should not trigger on {tool}"

    print("✓ Test 9 passed: stagnation signal not triggered on meta tools")


# ============================================================
# Test 10: 停滞信号是数据呈现（不含指令）
# ============================================================

def test_10_stagnation_signal_is_data_presentation():
    """信号内容遵循 §4.3 原则：只呈现事实，不下指令。"""
    h = Harness(paper_path=None)
    h.state.paper_sections = {"intro": "Content"}
    h._paper_loaded = True
    h.state.loop_turns = 15
    h.state.sections_read = ["intro"]
    h.state.findings = [
        {"finding": "Issue A", "priority": "high", "status": "verified", "recorded_at_turn": 2},
    ]
    h.state.tool_call_history = [{"name": "read_section", "input": {}}] * 10

    signal = h._check_stagnation("read_section")
    assert signal is not None

    # 验证信号内容
    assert "📉" in signal  # 有视觉标记
    assert "产出观察" in signal  # 是"观察"不是"命令"
    assert "轮" in signal  # 包含轮次数据
    assert "findings" in signal  # 包含 findings 数量

    # 不含指令性语言
    directive_words = ["应该", "必须", "请", "建议你", "你需要", "切换到"]
    for word in directive_words:
        assert word not in signal, f"Signal should not contain directive word: {word}"

    print("✓ Test 10 passed: stagnation signal is pure data presentation (§4.3)")


# ============================================================
# Test 11: PERSONA_TASK_CONTEXTS 完整性
# ============================================================

def test_11_persona_contexts_complete():
    """所有已注册的 persona 都有对应的 task context。"""
    assert "scholar" in PERSONA_TASK_CONTEXTS
    assert "code_reviewer" in PERSONA_TASK_CONTEXTS

    for persona, ctx in PERSONA_TASK_CONTEXTS.items():
        assert "task_domain" in ctx, f"{persona} missing task_domain"
        assert "reviewer_role" in ctx, f"{persona} missing reviewer_role"
        assert len(ctx["task_domain"]) > 0
        assert len(ctx["reviewer_role"]) > 0

    print("✓ Test 11 passed: PERSONA_TASK_CONTEXTS is complete for all personas")


# ============================================================
# 运行所有测试
# ============================================================

if __name__ == "__main__":
    tests = [
        test_1_checker_default_scholar,
        test_2_checker_code_reviewer,
        test_3_set_persona_dynamic,
        test_4_harness_passes_persona,
        test_5_stagnation_warmup_no_trigger,
        test_6_stagnation_recent_update_no_trigger,
        test_7_stagnation_triggers_on_no_output,
        test_8_stagnation_cooldown,
        test_9_stagnation_no_trigger_on_meta_tools,
        test_10_stagnation_signal_is_data_presentation,
        test_11_persona_contexts_complete,
    ]

    passed = 0
    failed = 0
    for test_fn in tests:
        try:
            test_fn()
            passed += 1
        except Exception as e:
            print(f"✗ {test_fn.__name__} FAILED: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\n{'='*60}")
    print(f"Phase 55 Tests: {passed} passed, {failed} failed, {passed + failed} total")
    if failed == 0:
        print("🎉 All Phase 55 tests passed!")
    else:
        print(f"⚠️  {failed} test(s) failed")
        sys.exit(1)
