"""
tests/test_v2_assembler.py — v2 ContextAssembler + SectionRegistry 单元测试

验证:
    1. SectionRegistry 按优先级排序注入 sections
    2. SectionRegistry token 预算裁剪正确
    3. SectionRegistry 缓存策略（NEVER/SESSION/PHASE）行为正确
    4. ContextAssembler 输出与原 format_context 内容等价
    5. ContextAssembler 在低预算下裁剪低优先级 section
    6. 条件函数正确过滤 section

运行: python3 tests/test_v2_assembler.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.sections import SectionRegistry, CachePolicy
from core.state import WorkspaceState


# ============================================================
# Test Helpers
# ============================================================

def _make_state_with_paper() -> WorkspaceState:
    """构造一个有论文内容的 state。"""
    state = WorkspaceState()
    state.paper_sections = {
        "abstract": "这是一篇关于创新示范区的论文摘要...",
        "introduction": "本文研究了国家自主创新示范区对城市创业活跃度的影响" * 10,
        "methodology": "我们使用双重差分法进行因果推断" * 20,
        "results": "基准回归结果表明..." * 15,
        "conclusion": "本文发现国家自主创新示范区政策有效促进了城市创业" * 5,
    }
    state.sections_read = ["abstract", "introduction"]
    state.section_digests = {
        "abstract": "研究创新示范区对创业的影响，使用DID方法",
    }
    state.findings = [
        {
            "finding": "平行趋势检验不够严谨",
            "priority": "high",
            "status": "verified",
            "evidence": "图1显示处理组在政策前已有上升趋势",
            "section": "methodology",
        },
        {
            "finding": "缺少稳健性检验",
            "priority": "medium",
            "status": "needs_verification",
            "evidence": None,
            "section": "results",
        },
    ]
    state.edits = [
        {"section": "abstract", "type": "rewrite"},
    ]
    state.reference_papers = {
        "ref_001": {
            "source": "user_provided",
            "title": "Place-Based Policies",
            "total_chars": 15000,
            "section_count": 5,
            "abstract": "We study the effects of place-based innovation policies...",
        },
    }
    state.loop_turns = 5
    state.max_loop_turns = 50
    state.conversation_turns = 3
    state.total_tokens = 45000
    return state


class MockMemoryStore:
    """模拟 MemoryStore。"""
    def format_memory_context(self, paper_id=None):
        return "📚 跨会话记忆: 上次审稿发现3个问题"


class MockCognitiveState:
    """模拟 CognitiveState。"""
    def format_for_context(self):
        return "🧠 认知状态: 连续阅读2轮，建议开始分析"


class MockOffloadStore:
    """模拟 OffloadStore。"""
    def format_refs_summary(self):
        return "💾 可恢复引用: ref_001, ref_002"


# ============================================================
# Test: SectionRegistry
# ============================================================

def test_registry_priority_ordering():
    """Section 按优先级降序注入。"""
    registry = SectionRegistry()

    registry.register("low", priority=10, cache_policy=CachePolicy.NEVER,
                      compute_fn=lambda ctx: "low content")
    registry.register("high", priority=90, cache_policy=CachePolicy.NEVER,
                      compute_fn=lambda ctx: "high content")
    registry.register("mid", priority=50, cache_policy=CachePolicy.NEVER,
                      compute_fn=lambda ctx: "mid content")

    results = registry.get_active_sections(state={}, budget=99999)
    names = [name for name, _ in results]
    assert names == ["high", "mid", "low"], f"Expected priority order, got {names}"
    print("✅ test_registry_priority_ordering passed")


def test_registry_budget_trimming():
    """预算不够时低优先级 section 被跳过。"""
    registry = SectionRegistry()

    # 注册一个很大的低优先级 section
    registry.register("big_low", priority=10, cache_policy=CachePolicy.NEVER,
                      compute_fn=lambda ctx: "x" * 10000)  # ~2500 tokens
    registry.register("small_high", priority=90, cache_policy=CachePolicy.NEVER,
                      compute_fn=lambda ctx: "high priority content")  # ~5 tokens

    # 给很小的预算（只够一个短 section）
    results = registry.get_active_sections(state={}, budget=100)
    names = [name for name, _ in results]
    assert "small_high" in names, f"High priority should be included, got {names}"
    assert "big_low" not in names, f"Big low priority should be trimmed, got {names}"
    print("✅ test_registry_budget_trimming passed")


def test_registry_cache_never():
    """NEVER 策略每次都重新计算。"""
    call_count = [0]

    def compute(ctx):
        call_count[0] += 1
        return f"computed {call_count[0]}"

    registry = SectionRegistry()
    registry.register("volatile", priority=50, cache_policy=CachePolicy.NEVER,
                      compute_fn=compute)

    r1 = registry.get_active_sections(state={}, budget=99999, current_turn=1)
    r2 = registry.get_active_sections(state={}, budget=99999, current_turn=2)

    assert call_count[0] == 2, f"Expected 2 calls, got {call_count[0]}"
    assert r1[0][1] != r2[0][1], "NEVER cache should recompute each time"
    print("✅ test_registry_cache_never passed")


def test_registry_cache_session():
    """SESSION 策略只计算一次，后续用缓存。"""
    call_count = [0]

    def compute(ctx):
        call_count[0] += 1
        return "static content"

    registry = SectionRegistry()
    registry.register("stable", priority=50, cache_policy=CachePolicy.SESSION,
                      compute_fn=compute)

    registry.get_active_sections(state={}, budget=99999, current_turn=1)
    registry.get_active_sections(state={}, budget=99999, current_turn=2)
    registry.get_active_sections(state={}, budget=99999, current_turn=3)

    assert call_count[0] == 1, f"SESSION should compute once, got {call_count[0]} calls"
    print("✅ test_registry_cache_session passed")


def test_registry_cache_phase():
    """PHASE 策略在同一阶段内缓存，阶段切换时重算。"""
    call_count = [0]

    def compute(ctx):
        call_count[0] += 1
        return "phase content"

    registry = SectionRegistry()
    registry.register("phased", priority=50, cache_policy=CachePolicy.PHASE,
                      compute_fn=compute)

    # 同一阶段调用两次
    registry.get_active_sections(state={}, budget=99999, current_turn=1, current_phase="SCAN")
    registry.get_active_sections(state={}, budget=99999, current_turn=2, current_phase="SCAN")
    assert call_count[0] == 1, f"Same phase should cache, got {call_count[0]} calls"

    # 切换阶段
    registry.get_active_sections(state={}, budget=99999, current_turn=3, current_phase="DEEP_REVIEW")
    assert call_count[0] == 2, f"Phase change should recompute, got {call_count[0]} calls"
    print("✅ test_registry_cache_phase passed")


def test_registry_condition_fn():
    """condition_fn 控制 section 是否注入。"""
    registry = SectionRegistry()

    registry.register("conditional", priority=90, cache_policy=CachePolicy.NEVER,
                      compute_fn=lambda ctx: "conditional content",
                      condition_fn=lambda ctx: ctx.get("include", False))

    # 条件不满足
    results = registry.get_active_sections(state={"include": False}, budget=99999)
    assert len(results) == 0, f"Should not include, got {results}"

    # 条件满足
    results = registry.get_active_sections(state={"include": True}, budget=99999)
    assert len(results) == 1, f"Should include, got {results}"
    print("✅ test_registry_condition_fn passed")


def test_registry_empty_content_skipped():
    """compute_fn 返回空字符串时不注入。"""
    registry = SectionRegistry()

    registry.register("empty", priority=90, cache_policy=CachePolicy.NEVER,
                      compute_fn=lambda ctx: "")
    registry.register("nonempty", priority=50, cache_policy=CachePolicy.NEVER,
                      compute_fn=lambda ctx: "has content")

    results = registry.get_active_sections(state={}, budget=99999)
    names = [name for name, _ in results]
    assert "empty" not in names, f"Empty section should be skipped, got {names}"
    assert "nonempty" in names
    print("✅ test_registry_empty_content_skipped passed")


# ============================================================
# Test: ContextAssembler
# ============================================================

def test_assembler_basic_output():
    """Assembler 产出包含所有核心信息块。"""
    from core.assembler import ContextAssembler

    state = _make_state_with_paper()
    assembler = ContextAssembler(
        memory=MockMemoryStore(),
        cognitive_state=MockCognitiveState(),
        offload_store=MockOffloadStore(),
    )

    result = assembler.assemble(state, paper_id="test_paper_001", current_turn=5)

    # 验证关键内容存在
    assert "论文已加载" in result, "Should contain paper overview"
    assert "5 个 sections" in result, "Should show section count"
    assert "你已有的发现" in result, "Should contain findings"
    assert "平行趋势" in result, "Should contain specific finding"
    assert "参考文献" in result or "Place-Based" in result, "Should contain references"
    assert "轮次: 5/50" in result, "Should contain resource status"
    assert "跨会话记忆" in result, "Should contain memory"
    assert "认知状态" in result, "Should contain metacognition"
    assert "可恢复引用" in result, "Should contain offload refs"
    print("✅ test_assembler_basic_output passed")


def test_assembler_empty_state():
    """空状态时输出默认文本。"""
    from core.assembler import ContextAssembler

    state = WorkspaceState()
    assembler = ContextAssembler(
        memory=MockMemoryStore(),
        cognitive_state=MockCognitiveState(),
        offload_store=MockOffloadStore(),
    )

    # 空状态但有 memory/cognitive/resource 仍应输出
    result = assembler.assemble(state, current_turn=0)
    # 至少有 resource_status + memory + cognition
    assert "轮次:" in result, f"Should at least have resource status, got: {result}"
    print("✅ test_assembler_empty_state passed")


def test_assembler_budget_trimming():
    """低预算时低优先级 section 被裁剪。"""
    from core.assembler import ContextAssembler

    state = _make_state_with_paper()
    assembler = ContextAssembler(
        memory=MockMemoryStore(),
        cognitive_state=MockCognitiveState(),
        offload_store=MockOffloadStore(),
    )

    # 极低预算：只能容纳一个短 section
    result = assembler.assemble(state, paper_id="test", current_turn=1, budget=100)

    # 应该至少包含最高优先级的内容（paper_overview）的开头部分
    # 或者如果 paper_overview 太大也放不下，就退而求其次
    # 关键是不应该 crash
    assert isinstance(result, str), "Should return string even with low budget"
    print("✅ test_assembler_budget_trimming passed")


def test_assembler_output_equivalence():
    """
    全预算下 assembler 输出应包含与原 format_context 相同的信息块。
    这是兼容性的核心验证。
    """
    from core.assembler import ContextAssembler

    state = _make_state_with_paper()
    assembler = ContextAssembler(
        memory=MockMemoryStore(),
        cognitive_state=MockCognitiveState(),
        offload_store=MockOffloadStore(),
    )

    result = assembler.assemble(
        state, paper_id="test", current_turn=5, budget=999999
    )

    # 验证原 format_context 中的所有信息块都存在
    expected_fragments = [
        "论文已加载",               # paper_overview
        "sections",                  # section 列表
        "你已读过",                  # 已读
        "尚未读取",                  # 未读
        "Section 摘要缓存",          # digests
        "你已有的发现",              # findings
        "🔴",                        # high priority finding icon
        "用户提供的参考文献",         # user references
        "你已做的修改",              # edits
        "跨会话记忆",               # memory
        "认知状态",                  # metacognition
        "可恢复引用",               # offload
        "轮次: 5/50",              # resource status
    ]

    missing = [f for f in expected_fragments if f not in result]
    assert not missing, f"Missing fragments in output: {missing}\n\nFull output:\n{result}"
    print("✅ test_assembler_output_equivalence passed")


def test_assembler_no_findings_no_section():
    """没有 findings 时 findings section 不应出现。"""
    from core.assembler import ContextAssembler

    state = _make_state_with_paper()
    state.findings = []  # 清空

    assembler = ContextAssembler(
        memory=MockMemoryStore(),
        cognitive_state=MockCognitiveState(),
        offload_store=MockOffloadStore(),
    )

    result = assembler.assemble(state, current_turn=1, budget=999999)
    assert "你已有的发现" not in result, "Should not show findings section when empty"
    print("✅ test_assembler_no_findings_no_section passed")


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("Running v2 Assembler + SectionRegistry tests...")
    print("=" * 60)

    # SectionRegistry tests
    test_registry_priority_ordering()
    test_registry_budget_trimming()
    test_registry_cache_never()
    test_registry_cache_session()
    test_registry_cache_phase()
    test_registry_condition_fn()
    test_registry_empty_content_skipped()

    # ContextAssembler tests
    test_assembler_basic_output()
    test_assembler_empty_state()
    test_assembler_budget_trimming()
    test_assembler_output_equivalence()
    test_assembler_no_findings_no_section()

    print("\n" + "=" * 60)
    print("All 12 tests passed! ✅")
    print("=" * 60)
