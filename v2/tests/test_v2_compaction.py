"""
tests/test_v2_compaction.py — v2 Smart Compaction Engine 单元测试

验证:
    1. should_compact 在正确条件下触发
    2. build_snapshot 正确构建工作台快照
    3. compact 产出的消息结构正确（system + restoration + recent）
    4. format_restoration 输出包含关键信息
    5. 压缩不丢失 findings 信息

运行: python3 tests/test_v2_compaction.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.compaction import CompactionEngine, CompactionConfig, WorkspaceSnapshot
from core.state import WorkspaceState


def _make_messages(n_turns: int) -> list[dict]:
    """构造 n_turns 组 (assistant + tool) 交互的消息列表。"""
    msgs: list[dict] = [{"role": "system", "content": "You are a reviewer."}]
    for i in range(n_turns):
        msgs.append({
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": f"call_{i}",
                "type": "function",
                "function": {
                    "name": "read_section",
                    "arguments": f'{{"section_id": "section_{i}"}}',
                },
            }],
        })
        msgs.append({
            "role": "tool",
            "tool_call_id": f"call_{i}",
            "content": f"Section {i} content here... " * 50,  # long content
        })
    return msgs


def _make_state(
    loop_turns: int = 10,
    last_prompt_tokens: int = 60000,
    context_window: int = 128000,
    sections_read: list | None = None,
    findings: list | None = None,
) -> WorkspaceState:
    """构造测试用的 WorkspaceState。"""
    state = WorkspaceState()
    state.loop_turns = loop_turns
    state.last_prompt_tokens = last_prompt_tokens
    state.context_window = context_window
    state.sections_read = sections_read or ["abstract", "methods", "results"]
    state.paper_sections = {s: f"content of {s}" for s in ["abstract", "methods", "results", "conclusion", "discussion"]}
    state.findings = findings or [
        {"finding": "DID parallel trends assumption not tested", "priority": "high", "status": "verified"},
        {"finding": "Sample selection bias in treatment group", "priority": "medium", "status": "verified"},
    ]
    state.tool_call_history = [
        {"name": "read_section", "input": {}, "turn": 1},
        {"name": "read_section", "input": {}, "turn": 2},
        {"name": "search_literature", "input": {}, "turn": 3},
        {"name": "update_findings", "input": {}, "turn": 4},
        {"name": "read_section", "input": {}, "turn": 5},
    ]
    return state


# ============================================================
# Test 1: should_compact 触发条件
# ============================================================

def test_should_compact_triggers():
    """context ratio >= 0.5 且消息足够多时触发压缩。"""
    engine = CompactionEngine()
    messages = _make_messages(10)  # 21 条消息 (1 system + 10*2)

    # 正常情况: ratio=0.5, 足够消息 → 应压缩
    state = _make_state(last_prompt_tokens=64000, context_window=128000)
    assert engine.should_compact(state, messages) is True

    # ratio 太低 → 不压缩
    state_low = _make_state(last_prompt_tokens=30000, context_window=128000)
    assert engine.should_compact(state_low, messages) is False

    # 消息太少 → 不压缩
    short_msgs = _make_messages(3)  # 7 条消息
    state_enough = _make_state(last_prompt_tokens=64000, context_window=128000)
    assert engine.should_compact(state_enough, short_msgs) is False

    print("  [PASS] test_should_compact_triggers")


# ============================================================
# Test 2: build_snapshot 正确构建
# ============================================================

def test_build_snapshot():
    """快照正确反映当前状态。"""
    engine = CompactionEngine()
    state = _make_state()
    messages = _make_messages(10)

    snapshot = engine.build_snapshot(state, messages)

    assert snapshot.sections_read == ["abstract", "methods", "results"]
    assert snapshot.total_sections == 5
    assert snapshot.loop_turns == 10
    assert snapshot.findings_count == 2
    assert len(snapshot.findings_summary) == 2
    assert "[high]" in snapshot.findings_summary[0]
    assert "DID" in snapshot.findings_summary[0]
    assert len(snapshot.recent_tools) == 5
    assert snapshot.recent_tools[-1] == "read_section"

    print("  [PASS] test_build_snapshot")


# ============================================================
# Test 3: compact 消息结构正确
# ============================================================

def test_compact_message_structure():
    """压缩后: system + restoration_user + restoration_assistant + recent turns。"""
    engine = CompactionEngine(CompactionConfig(recent_turns_to_keep=3))
    state = _make_state()
    messages = _make_messages(10)

    snapshot = engine.build_snapshot(state, messages)
    compacted = engine.compact(messages, snapshot, state)

    # 检查结构
    assert compacted[0]["role"] == "system"
    assert compacted[1]["role"] == "user"
    assert "[上下文恢复]" in compacted[1]["content"]
    assert compacted[2]["role"] == "assistant"
    assert "已理解" in compacted[2]["content"]

    # 最近 3 组交互应完整保留 (3 * 2 = 6 条 + system + 2 restoration = 9)
    assert len(compacted) == 1 + 2 + 6  # system + restoration pair + 3 turns * 2
    assert compacted[-1]["role"] == "tool"  # 最后一条是 tool result
    assert compacted[-2]["role"] == "assistant"  # 倒数第二是 assistant

    # 比原始消息少很多
    assert len(compacted) < len(messages)

    print("  [PASS] test_compact_message_structure")


# ============================================================
# Test 4: format_restoration 包含关键信息
# ============================================================

def test_format_restoration():
    """恢复文本包含 findings、进度、最近操作。"""
    snapshot = WorkspaceSnapshot(
        sections_read=["abstract", "methods"],
        total_sections=8,
        loop_turns=15,
        findings_count=3,
        findings_summary=[
            "[high] Critical issue A",
            "[medium] Minor issue B",
            "[low] Suggestion C",
        ],
        recent_tools=["read_section", "update_findings", "search_literature"],
        history_summary="历史中共执行了 20 次工具调用:\n  read_section: 12次",
    )

    text = snapshot.format_restoration()

    # 验证包含关键信息
    assert "2/8 sections" in text
    assert "轮次: 15" in text
    assert "3 个" in text
    assert "Critical issue A" in text
    assert "read_section→update_findings→search_literature" in text
    assert "20 次工具调用" in text

    print("  [PASS] test_format_restoration")


# ============================================================
# Test 5: 压缩不丢 findings
# ============================================================

def test_compact_preserves_findings():
    """压缩后的恢复信息中保留了所有 findings。"""
    engine = CompactionEngine(CompactionConfig(recent_turns_to_keep=2))
    findings = [
        {"finding": f"Finding number {i} about methodology", "priority": "high", "status": "verified"}
        for i in range(5)
    ]
    state = _make_state(findings=findings)
    messages = _make_messages(12)

    snapshot = engine.build_snapshot(state, messages)
    compacted = engine.compact(messages, snapshot, state)

    # 恢复消息中包含所有 5 个 findings
    restoration_content = compacted[1]["content"]
    for i in range(5):
        assert f"Finding number {i}" in restoration_content, f"Missing finding {i}"

    # assistant 的恢复确认中提到数量
    assert "5 个问题" in compacted[2]["content"]

    print("  [PASS] test_compact_preserves_findings")


# ============================================================
# Test 6: aggressive mode 在高 ratio 下保留更少
# ============================================================

def test_aggressive_compaction():
    """context ratio > 0.7 时使用 aggressive keep (3 turns)。"""
    engine = CompactionEngine()

    # ratio = 0.75 → aggressive
    state_aggressive = _make_state(
        last_prompt_tokens=96000, context_window=128000
    )
    assert engine.get_keep_recent(state_aggressive) == 3

    # ratio = 0.55 → normal (6 turns)
    state_normal = _make_state(
        last_prompt_tokens=70000, context_window=128000
    )
    assert engine.get_keep_recent(state_normal) == 6

    print("  [PASS] test_aggressive_compaction")


# ============================================================
# Test 7: M2 — 分层恢复包含所有层
# ============================================================

def test_m2_layered_restoration_all_layers():
    """所有层都有内容时，按优先级从高到低组装。"""
    snapshot = WorkspaceSnapshot(
        sections_read=["abstract", "methods"],
        total_sections=8,
        loop_turns=10,
        findings_count=2,
        findings_summary=["[high] Finding A", "[medium] Finding B"],
        recent_tools=["read_section", "update_findings"],
        history_summary="历史中共执行了 10 次工具调用:\n  read_section: 6次",
        session_memory_text="[审稿认知笔记]\n- 你之前判断 IV 策略较弱",
        hypothesis_text="[假说工作记忆恢复]\n假说工作记忆 | 总计 2 | 活跃 1 | 已解决 1",
        paper_structure_text="[论文结构索引]\n论文类型: empirical | Sections: 8",
    )

    text = snapshot.format_restoration(budget_tokens=6000)

    # 所有层都应该在（预算足够）
    assert "Finding A" in text, "Findings (critical) must be present"
    assert "认知笔记" in text, "Session memory must be present"
    assert "假说工作记忆" in text, "Hypotheses must be present"
    assert "论文结构索引" in text, "Paper structure must be present"
    assert "审稿进度" in text, "Progress must be present"

    # Findings 应该出现在最前面（priority 100）
    findings_pos = text.index("Finding A")
    memory_pos = text.index("认知笔记")
    assert findings_pos < memory_pos, "Findings should come before session memory"

    print("  [PASS] test_m2_layered_restoration_all_layers")


# ============================================================
# Test 8: M2 — token 预算裁剪
# ============================================================

def test_m2_budget_truncation():
    """预算不够时，从最低优先级层开始丢弃。"""
    # 构造一个每层内容都比较大的 snapshot
    large_text = "x" * 900  # ~300 tokens per layer

    snapshot = WorkspaceSnapshot(
        sections_read=["s1", "s2", "s3", "s4", "s5"],
        total_sections=10,
        loop_turns=20,
        findings_count=1,
        findings_summary=["[high] " + large_text],  # ~300 tokens (critical)
        session_memory_text="[认知笔记] " + large_text,  # ~300 tokens
        hypothesis_text="[假说] " + large_text,  # ~300 tokens
        paper_structure_text="[结构] " + large_text,  # ~300 tokens
        history_summary="历史 " + large_text,  # progress layer ~300 tokens
    )

    # 极小预算: 只够 critical (findings) + 可能 1 个 optional 层
    text_tight = snapshot.format_restoration(budget_tokens=600)

    # Findings 是 critical，必须保留
    assert "[high]" in text_tight, "Critical findings must survive any budget"

    # 在极小预算下，低优先级层应该被裁剪
    # progress (priority=40) 最先被裁剪
    # 我们不精确断言哪些被丢弃，但验证总长度 < 全量
    text_full = snapshot.format_restoration(budget_tokens=99999)
    assert len(text_tight) < len(text_full), "Tight budget should produce shorter text"

    print("  [PASS] test_m2_budget_truncation")


# ============================================================
# Test 9: M2 — critical 层永不裁剪
# ============================================================

def test_m2_critical_never_truncated():
    """即使预算极低，critical 层(findings)也不被裁剪。"""
    big_findings = [f"[high] Critical finding number {i} " + "a" * 80 for i in range(10)]

    snapshot = WorkspaceSnapshot(
        findings_count=10,
        findings_summary=big_findings,
        session_memory_text="some memory text",
        hypothesis_text="some hypothesis text",
    )

    # 预算设为 0 — 但 critical 层仍然保留
    text = snapshot.format_restoration(budget_tokens=0)

    # 所有 findings 都在
    for i in range(10):
        assert f"Critical finding number {i}" in text, f"Missing critical finding {i}"

    # optional 层应该被裁剪
    assert "some memory text" not in text
    assert "some hypothesis text" not in text

    print("  [PASS] test_m2_critical_never_truncated")


# ============================================================
# Test 10: M2 — build_snapshot 接收新参数
# ============================================================

def test_m2_build_snapshot_with_new_params():
    """build_snapshot 正确传递 hypothesis_text 和 paper_structure_text。"""
    engine = CompactionEngine()
    state = _make_state()
    messages = _make_messages(10)

    snapshot = engine.build_snapshot(
        state, messages,
        session_memory_text="session memory content",
        hypothesis_text="hypothesis content",
        paper_structure_text="paper structure content",
    )

    assert snapshot.session_memory_text == "session memory content"
    assert snapshot.hypothesis_text == "hypothesis content"
    assert snapshot.paper_structure_text == "paper structure content"

    # format_restoration 应包含这些内容
    text = snapshot.format_restoration()
    assert "session memory content" in text
    assert "hypothesis content" in text
    assert "paper structure content" in text

    print("  [PASS] test_m2_build_snapshot_with_new_params")


# ============================================================
# Test 11: M2 — _estimate_tokens 基本行为
# ============================================================

def test_m2_estimate_tokens():
    """token 估算函数基本正确。"""
    from core.compaction import _estimate_tokens

    assert _estimate_tokens("") == 0
    assert _estimate_tokens("hello") >= 1
    # 300 字符 ≈ 100 tokens (300 // 3)
    assert _estimate_tokens("a" * 300) == 100
    # 中文 600 字符 ≈ 200 tokens
    assert _estimate_tokens("你" * 600) == 200

    print("  [PASS] test_m2_estimate_tokens")


# ============================================================
# Test 12: M2 — 空 snapshot 不崩溃
# ============================================================

def test_m2_empty_snapshot_restoration():
    """空 snapshot（无 findings 无笔记）也能正常 format_restoration。"""
    snapshot = WorkspaceSnapshot()
    text = snapshot.format_restoration()

    # 至少有进度信息
    assert "审稿进度" in text
    assert "0/0 sections" in text

    print("  [PASS] test_m2_empty_snapshot_restoration")


# ============================================================
# Test 13: M2 — 恢复文本层级顺序验证
# ============================================================

def test_m2_layer_ordering():
    """验证恢复文本中各层的相对顺序正确（高优先级在前）。"""
    snapshot = WorkspaceSnapshot(
        findings_count=1,
        findings_summary=["[high] MARKER_FINDINGS"],
        session_memory_text="MARKER_SESSION_MEMORY",
        hypothesis_text="MARKER_HYPOTHESES",
        paper_structure_text="MARKER_PAPER_STRUCTURE",
        history_summary="MARKER_PROGRESS",
    )

    text = snapshot.format_restoration(budget_tokens=99999)

    # 获取各 marker 位置
    pos_findings = text.index("MARKER_FINDINGS")
    pos_session = text.index("MARKER_SESSION_MEMORY")
    pos_hyp = text.index("MARKER_HYPOTHESES")
    pos_paper = text.index("MARKER_PAPER_STRUCTURE")
    pos_progress = text.index("MARKER_PROGRESS")

    # 验证顺序: findings > session_memory > hypotheses > paper_structure > progress
    assert pos_findings < pos_session, "Findings before session memory"
    assert pos_session < pos_hyp, "Session memory before hypotheses"
    assert pos_hyp < pos_paper, "Hypotheses before paper structure"
    assert pos_paper < pos_progress, "Paper structure before progress"

    print("  [PASS] test_m2_layer_ordering")


# ============================================================
# B4 Tests: pre_compact_hook + get_capacity_pct
# ============================================================

def test_b4_hook_called_on_compact():
    """hook 在压缩触发时被调用（mock hook + 验证调用次数）。"""
    engine = CompactionEngine(CompactionConfig(
        trigger_token_ratio=0.5,
        recent_turns_to_keep=2,
        min_messages_for_compaction=6,
    ))

    call_log = []
    def my_hook(snapshot):
        call_log.append(snapshot)

    engine.register_pre_compact_hook(my_hook)

    state = _make_state()
    messages = _make_messages(5)  # 10 msgs + 1 system = 11

    snapshot = engine.build_snapshot(state, messages)
    engine.compact(messages, snapshot, state)

    assert len(call_log) == 1, f"Hook should be called once, got {len(call_log)}"
    assert call_log[0] is snapshot
    print("  [PASS] test_b4_hook_called_on_compact")


def test_b4_hook_exception_does_not_block():
    """hook 异常不阻断压缩流程（try-except + warning log）。"""
    engine = CompactionEngine(CompactionConfig(
        trigger_token_ratio=0.5,
        recent_turns_to_keep=2,
        min_messages_for_compaction=6,
    ))

    def bad_hook(snapshot):
        raise RuntimeError("I am a broken hook!")

    good_call_log = []
    def good_hook(snapshot):
        good_call_log.append(True)

    engine.register_pre_compact_hook(bad_hook)
    engine.register_pre_compact_hook(good_hook)

    state = _make_state()
    messages = _make_messages(5)

    snapshot = engine.build_snapshot(state, messages)
    # 不应抛出异常
    result = engine.compact(messages, snapshot, state)

    # 压缩正常完成
    assert len(result) < len(messages), "Compaction should reduce messages"
    # good_hook 仍被调用
    assert len(good_call_log) == 1, "Good hook should still be called"
    print("  [PASS] test_b4_hook_exception_does_not_block")


def test_b4_get_capacity_pct_correct():
    """get_capacity_pct() 计算正确（edge: 0%、50%、100%、超限）。"""
    config = CompactionConfig(total_context_window=100000)
    engine = CompactionEngine(config)

    # 0%
    assert engine.get_capacity_pct(0) == 0.0
    # 50%
    assert abs(engine.get_capacity_pct(50000) - 0.5) < 0.001
    # 100%
    assert engine.get_capacity_pct(100000) == 1.0
    # 超限 → cap 在 1.0
    assert engine.get_capacity_pct(150000) == 1.0
    print("  [PASS] test_b4_get_capacity_pct_correct")


def test_b4_get_capacity_pct_zero_window():
    """total_context_window=0 时返回 0.0（不 crash）。"""
    config = CompactionConfig(total_context_window=0)
    engine = CompactionEngine(config)

    assert engine.get_capacity_pct(50000) == 0.0
    print("  [PASS] test_b4_get_capacity_pct_zero_window")


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("Running: test_v2_compaction.py")
    print("=" * 60)

    tests = [
        test_should_compact_triggers,
        test_build_snapshot,
        test_compact_message_structure,
        test_format_restoration,
        test_compact_preserves_findings,
        test_aggressive_compaction,
        # M2 tests
        test_m2_layered_restoration_all_layers,
        test_m2_budget_truncation,
        test_m2_critical_never_truncated,
        test_m2_build_snapshot_with_new_params,
        test_m2_estimate_tokens,
        test_m2_empty_snapshot_restoration,
        test_m2_layer_ordering,
        # B4 tests
        test_b4_hook_called_on_compact,
        test_b4_hook_exception_does_not_block,
        test_b4_get_capacity_pct_correct,
        test_b4_get_capacity_pct_zero_window,
    ]

    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            print(f"  [FAIL] {t.__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed, {passed + failed} total")
    print("=" * 60)

    if failed > 0:
        sys.exit(1)
