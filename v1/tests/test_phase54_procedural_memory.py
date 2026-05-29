"""
Phase 54 单元测试: 程序性记忆 (Procedural Memory)

核心证明: Agent 不仅积累"什么问题存在"（Layer 2），还积累"如何高效工作"（Layer 3）。
程序性记忆从 tool_call_history + strategy_transitions 中自动提取，
注入时遵循信息呈现原则（不是指令），Agent 自主决定是否采纳。

测试场景:
1. ProceduralPattern 数据模型正确创建
2. MemoryStore.add_or_reinforce_procedure 新增模式
3. MemoryStore.add_or_reinforce_procedure 强化已有模式（加权平均）
4. MemoryStore.get_relevant_procedures 按 effectiveness*evidence 排序
5. extract_procedural_patterns 检测高产工具序列
6. extract_procedural_patterns 检测低效重复模式
7. extract_procedural_patterns 检测策略切换有效性
8. format_memory_context 包含程序性记忆摘要
9. 序列化/反序列化正确保存和恢复 procedures
10. 向后兼容: v1.0 格式（无 procedures）正常加载
11. end_session 集成: 程序性记忆在会话结束时沉淀
12. 程序性记忆容量限制（50 条上限）
"""
import sys
import json
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.memory import (
    ProceduralPattern,
    MemoryState,
    MemoryStore,
    extract_procedural_patterns,
    build_session_record,
    extract_domain_patterns,
)


# ============================================================
# 测试数据
# ============================================================

SAMPLE_TOOL_HISTORY_PRODUCTIVE = [
    # 高产序列: read_section → search_literature → reflect_and_plan → update_findings
    "read_section", "search_literature", "reflect_and_plan", "update_findings",
    "read_section", "search_literature", "reflect_and_plan", "update_findings",
    "read_section", "search_literature", "reflect_and_plan", "update_findings",
    "read_section", "review_findings", "mark_complete",
]

SAMPLE_TOOL_HISTORY_ANTI_PATTERN = [
    # 低效模式: 连续 5 次 read_section 无产出
    "read_section", "read_section", "read_section", "read_section", "read_section",
    "reflect_and_plan",
    "search_literature", "update_findings",
]

SAMPLE_TOOL_HISTORY_MIXED = [
    "read_section", "read_section", "search_literature", "update_findings",
    "read_section", "reflect_and_plan", "search_literature", "update_findings",
    "review_findings", "mark_complete",
]


# ============================================================
# Test 1: ProceduralPattern 数据模型
# ============================================================

def test_1_procedural_pattern_dataclass():
    """ProceduralPattern 数据模型正确创建，字段默认值合理。"""
    p = ProceduralPattern(
        pattern_id="test123",
        category="tool_sequence",
        description="高产序列: read→search→update",
        trigger_context="当需要产出 findings 时",
    )
    assert p.pattern_id == "test123"
    assert p.category == "tool_sequence"
    assert p.effectiveness_score == 0.0
    assert p.evidence_count == 1
    assert p.first_seen == ""
    assert p.last_seen == ""
    print("✓ Test 1 passed: ProceduralPattern dataclass works correctly")


# ============================================================
# Test 2: add_or_reinforce_procedure 新增
# ============================================================

def test_2_add_new_procedure():
    """新增程序性模式正确创建。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = MemoryStore(tmpdir)

        store.add_or_reinforce_procedure(
            category="tool_sequence",
            description="高产序列: read_section→search_literature→update_findings",
            trigger_context="当需要产出 findings 时",
            effectiveness_score=0.8,
        )

        assert len(store.state.procedures) == 1
        proc = store.state.procedures[0]
        assert proc.category == "tool_sequence"
        assert proc.effectiveness_score == 0.8
        assert proc.evidence_count == 1
        assert proc.first_seen != ""
        assert proc.last_seen != ""
        assert proc.pattern_id != ""
        print("✓ Test 2 passed: add_or_reinforce_procedure creates new pattern")


# ============================================================
# Test 3: add_or_reinforce_procedure 强化（加权平均）
# ============================================================

def test_3_reinforce_existing_procedure():
    """强化已有模式时 effectiveness_score 做加权平均。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = MemoryStore(tmpdir)

        # 第一次添加
        store.add_or_reinforce_procedure(
            category="tool_sequence",
            description="高产序列: read_section→search_literature→update_findings",
            trigger_context="当需要产出 findings 时",
            effectiveness_score=0.8,
        )

        # 第二次强化（相似描述）
        store.add_or_reinforce_procedure(
            category="tool_sequence",
            description="高产序列: read_section→search_literature→update_findings 效率高",
            trigger_context="当需要产出 findings 时",
            effectiveness_score=0.6,
        )

        assert len(store.state.procedures) == 1  # 没有新增，是强化
        proc = store.state.procedures[0]
        assert proc.evidence_count == 2
        # 加权平均: (0.8 * 1 + 0.6 * 1) / 2 = 0.7
        assert abs(proc.effectiveness_score - 0.7) < 0.01
        print("✓ Test 3 passed: reinforce uses weighted average for effectiveness_score")


# ============================================================
# Test 4: get_relevant_procedures 排序
# ============================================================

def test_4_get_relevant_procedures_sorted():
    """按 effectiveness_score * evidence_count 排序。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = MemoryStore(tmpdir)

        # 添加三个不同模式
        store.add_or_reinforce_procedure("tool_sequence", "序列A", "ctx", 0.9)
        store.add_or_reinforce_procedure("anti_pattern", "反模式B", "ctx", 0.3)
        store.add_or_reinforce_procedure("strategy_effectiveness", "策略C", "ctx", 0.7)

        # 强化序列A两次（evidence=3, score≈0.9）
        store.add_or_reinforce_procedure("tool_sequence", "序列A", "ctx", 0.9)
        store.add_or_reinforce_procedure("tool_sequence", "序列A", "ctx", 0.9)

        results = store.get_relevant_procedures(limit=3)
        assert len(results) == 3
        # 序列A 应该排第一（0.9 * 3 = 2.7）
        assert results[0].description == "序列A"
        # 策略C 排第二（0.7 * 1 = 0.7）
        assert results[1].description == "策略C"
        # 反模式B 排最后（0.3 * 1 = 0.3）
        assert results[2].description == "反模式B"

        # 按类别过滤
        anti_only = store.get_relevant_procedures(categories=["anti_pattern"])
        assert len(anti_only) == 1
        assert anti_only[0].category == "anti_pattern"

        print("✓ Test 4 passed: get_relevant_procedures sorts by effectiveness*evidence")


# ============================================================
# Test 5: extract_procedural_patterns 高产序列检测
# ============================================================

def test_5_extract_productive_sequences():
    """检测紧跟 update_findings 的高产 3-gram 工具序列。"""
    patterns = extract_procedural_patterns(
        tool_call_history=SAMPLE_TOOL_HISTORY_PRODUCTIVE,
        findings_count=3,
        loop_turns=14,
    )

    # 应该检测到 read_section→search_literature→reflect_and_plan 序列
    tool_seq_patterns = [p for p in patterns if p[0] == "tool_sequence"]
    assert len(tool_seq_patterns) >= 1, f"Expected tool_sequence patterns, got {patterns}"

    # 检查描述中包含序列信息
    desc = tool_seq_patterns[0][1]
    assert "→" in desc, f"Expected arrow in description, got: {desc}"
    assert "read_section" in desc

    print("✓ Test 5 passed: extract_procedural_patterns detects productive sequences")


# ============================================================
# Test 6: extract_procedural_patterns 低效重复检测
# ============================================================

def test_6_extract_anti_patterns():
    """检测连续重复同一工具超过 4 次的低效模式。"""
    patterns = extract_procedural_patterns(
        tool_call_history=SAMPLE_TOOL_HISTORY_ANTI_PATTERN,
        findings_count=1,
        loop_turns=8,
    )

    anti_patterns = [p for p in patterns if p[0] == "anti_pattern"]
    assert len(anti_patterns) >= 1, f"Expected anti_pattern, got {patterns}"

    desc = anti_patterns[0][1]
    assert "read_section" in desc
    assert "5" in desc  # 连续 5 次
    # effectiveness_score 应该低
    assert anti_patterns[0][3] <= 0.3

    print("✓ Test 6 passed: extract_procedural_patterns detects anti-patterns")


# ============================================================
# Test 7: extract_procedural_patterns 策略切换有效性
# ============================================================

def test_7_extract_strategy_effectiveness():
    """检测策略切换在高产出会话中的有效性。"""
    patterns = extract_procedural_patterns(
        tool_call_history=SAMPLE_TOOL_HISTORY_MIXED,
        findings_count=5,  # 高产出
        loop_turns=10,
        strategy_transitions=[("breadth_scan", "deep_investigation")],
    )

    strategy_patterns = [p for p in patterns if p[0] == "strategy_effectiveness"]
    assert len(strategy_patterns) >= 1, f"Expected strategy patterns, got {patterns}"

    # 检查描述包含策略名
    found_transition = False
    for p in strategy_patterns:
        if "breadth_scan" in p[1] and "deep_investigation" in p[1]:
            found_transition = True
            break
    assert found_transition, f"Expected strategy transition in patterns: {strategy_patterns}"

    print("✓ Test 7 passed: extract_procedural_patterns detects strategy effectiveness")


# ============================================================
# Test 8: format_memory_context 包含程序性记忆
# ============================================================

def test_8_format_memory_context_includes_procedures():
    """format_memory_context 输出包含程序性记忆摘要。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = MemoryStore(tmpdir)

        # 添加一个高效模式
        store.add_or_reinforce_procedure(
            category="tool_sequence",
            description="read→search→update 是高产序列",
            trigger_context="当需要产出 findings 时",
            effectiveness_score=0.85,
        )

        context = store.format_memory_context()
        assert context is not None
        assert "高效工作模式" in context
        assert "read→search→update" in context
        assert "85%" in context  # effectiveness_score 显示为百分比
        assert "验证 1 次" in context

        print("✓ Test 8 passed: format_memory_context includes procedural memory")


# ============================================================
# Test 9: 序列化/反序列化
# ============================================================

def test_9_serialization_roundtrip():
    """procedures 正确序列化和反序列化。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = MemoryStore(tmpdir)

        store.add_or_reinforce_procedure("tool_sequence", "序列A", "ctx_a", 0.8)
        store.add_or_reinforce_procedure("anti_pattern", "反模式B", "ctx_b", 0.2)
        store.add_or_reinforce_procedure("tool_sequence", "序列A", "ctx_a", 0.9)  # 强化

        # 保存
        store.save()

        # 重新加载
        store2 = MemoryStore(tmpdir)
        loaded = store2.load()
        assert loaded is True

        assert len(store2.state.procedures) == 2
        # 找到序列A
        seq_a = [p for p in store2.state.procedures if "序列A" in p.description]
        assert len(seq_a) == 1
        assert seq_a[0].evidence_count == 2
        assert abs(seq_a[0].effectiveness_score - 0.85) < 0.01  # (0.8*1 + 0.9*1)/2

        print("✓ Test 9 passed: serialization roundtrip preserves procedures")


# ============================================================
# Test 10: 向后兼容 v1.0 格式
# ============================================================

def test_10_backward_compatibility_v1():
    """v1.0 格式（无 procedures 字段）正常加载，procedures 为空列表。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # 手动写入 v1.0 格式的 memory.json
        v1_data = {
            "version": "1.0",
            "last_updated": "2024-01-01T00:00:00",
            "sessions": [],
            "patterns": [
                {
                    "pattern_id": "abc123",
                    "category": "methodology",
                    "description": "DID 论文常见平行趋势问题",
                    "evidence_count": 5,
                    "first_seen": "2024-01-01T00:00:00",
                    "last_seen": "2024-06-01T00:00:00",
                    "examples": ["paper_001", "paper_002"],
                }
            ],
            # 注意: 没有 "procedures" 字段
        }
        memory_path = Path(tmpdir) / "memory.json"
        memory_path.write_text(json.dumps(v1_data), encoding="utf-8")

        store = MemoryStore(tmpdir)
        loaded = store.load()
        assert loaded is True
        assert len(store.state.patterns) == 1
        assert len(store.state.procedures) == 0  # 向后兼容: 空列表
        assert store.state.patterns[0].description == "DID 论文常见平行趋势问题"

        print("✓ Test 10 passed: backward compatible with v1.0 format (no procedures)")


# ============================================================
# Test 11: end_session 集成测试
# ============================================================

def test_11_end_session_integration():
    """end_session 正确调用程序性记忆提取并沉淀。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        from core.harness import Harness

        h = Harness(paper_path=None)
        h.memory = MemoryStore(tmpdir)

        # 模拟会话数据
        h.state.paper_sections = {
            "abstract": "This paper studies the effect of X on Y using DID.",
            "methods": "We use difference-in-differences with panel data.",
        }
        h._paper_id = MemoryStore.compute_paper_id(h.state.paper_sections)

        # 模拟 findings
        h.state.findings = [
            {"finding": "Parallel trends assumption not tested", "priority": "high", "status": "verified"},
            {"finding": "Overclaim: causal language without identification", "priority": "high", "status": "verified"},
            {"finding": "Sample size too small for subgroup analysis", "priority": "medium", "status": "verified"},
        ]

        # 模拟工具调用历史（高产序列）
        h.state.tool_call_history = [
            {"name": "read_section", "input": {}},
            {"name": "search_literature", "input": {}},
            {"name": "reflect_and_plan", "input": {}},
            {"name": "update_findings", "input": {}},
            {"name": "read_section", "input": {}},
            {"name": "search_literature", "input": {}},
            {"name": "reflect_and_plan", "input": {}},
            {"name": "update_findings", "input": {}},
            {"name": "read_section", "input": {}},
            {"name": "search_literature", "input": {}},
            {"name": "reflect_and_plan", "input": {}},
            {"name": "update_findings", "input": {}},
            {"name": "review_findings", "input": {}},
            {"name": "mark_complete", "input": {}},
        ]
        h.state.loop_turns = 14
        h.state.conversation_turns = 2
        h.state.total_tokens = 5000

        # 模拟策略切换
        h._strategy_transitions = [("breadth_scan", "deep_investigation")]

        # 执行 end_session
        h.end_session(paper_title="Test Paper on DID")

        # 验证: 程序性记忆被沉淀
        assert len(h.memory.state.procedures) > 0, "Expected procedural patterns to be extracted"

        # 验证: 领域模式也被沉淀
        assert len(h.memory.state.patterns) > 0, "Expected domain patterns to be extracted"

        # 验证: 持久化到磁盘
        memory_path = Path(tmpdir) / "memory.json"
        assert memory_path.exists()
        raw = json.loads(memory_path.read_text())
        assert "procedures" in raw
        assert len(raw["procedures"]) > 0

        print("✓ Test 11 passed: end_session integrates procedural memory extraction")


# ============================================================
# Test 12: 容量限制（50 条上限）
# ============================================================

def test_12_capacity_limit():
    """procedures 超过 50 条时自动裁剪，保留最高 effectiveness*evidence 的。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = MemoryStore(tmpdir)

        # 添加 55 个不同模式
        for i in range(55):
            store.add_or_reinforce_procedure(
                category="tool_sequence",
                description=f"模式_{i:03d}_unique_description_for_dedup",
                trigger_context=f"context_{i}",
                effectiveness_score=i / 55.0,  # 0.0 ~ 1.0 递增
            )

        # 应该被裁剪到 50 条
        assert len(store.state.procedures) == 50

        # 最低 effectiveness 的应该被淘汰（前 5 个: 0/55 ~ 4/55）
        scores = [p.effectiveness_score for p in store.state.procedures]
        min_score = min(scores)
        assert min_score >= 4 / 55.0, f"Expected lowest score >= {4/55:.3f}, got {min_score:.3f}"

        print("✓ Test 12 passed: capacity limit enforced at 50 procedures")


# ============================================================
# Test 13: 空历史不崩溃
# ============================================================

def test_13_empty_history_graceful():
    """空的 tool_call_history 不崩溃，返回空列表。"""
    patterns = extract_procedural_patterns(
        tool_call_history=[],
        findings_count=0,
        loop_turns=0,
    )
    assert patterns == []

    # 极短历史也不崩溃
    patterns2 = extract_procedural_patterns(
        tool_call_history=["read_section"],
        findings_count=0,
        loop_turns=1,
    )
    assert isinstance(patterns2, list)

    print("✓ Test 13 passed: empty/short history handled gracefully")


# ============================================================
# Test 14: format_memory_context 仅有 procedures 时也能输出
# ============================================================

def test_14_format_context_procedures_only():
    """即使没有 sessions 和 patterns，只有 procedures 也能输出上下文。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = MemoryStore(tmpdir)

        # 只添加 procedures，不添加 sessions 或 patterns
        store.add_or_reinforce_procedure(
            "strategy_effectiveness",
            "deep_investigation 在 findings>=3 后切入效率最高",
            "当 findings>=3 时",
            0.9,
        )

        context = store.format_memory_context()
        assert context is not None
        assert "高效工作模式" in context
        assert "deep_investigation" in context

        print("✓ Test 14 passed: format_memory_context works with procedures only")


# ============================================================
# 运行所有测试
# ============================================================

if __name__ == "__main__":
    tests = [
        test_1_procedural_pattern_dataclass,
        test_2_add_new_procedure,
        test_3_reinforce_existing_procedure,
        test_4_get_relevant_procedures_sorted,
        test_5_extract_productive_sequences,
        test_6_extract_anti_patterns,
        test_7_extract_strategy_effectiveness,
        test_8_format_memory_context_includes_procedures,
        test_9_serialization_roundtrip,
        test_10_backward_compatibility_v1,
        test_11_end_session_integration,
        test_12_capacity_limit,
        test_13_empty_history_graceful,
        test_14_format_context_procedures_only,
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
    print(f"Phase 54 Tests: {passed} passed, {failed} failed, {passed + failed} total")
    if failed == 0:
        print("🎉 All Phase 54 tests passed!")
    else:
        print(f"⚠️  {failed} test(s) failed")
        sys.exit(1)
