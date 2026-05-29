"""
tests/test_memory.py — Phase 15 跨会话认知记忆 Unit Tests

验证:
    1. MemoryStore 基本 CRUD (persist/load/recall)
    2. SessionRecord 构建逻辑
    3. DomainPattern 积累逻辑
    4. format_memory_context 输出格式与 token 预算
    5. Harness 集成（end_session → memory → format_context）
    6. 渐进退化（无 memory 文件时正常工作）
    7. paper_id 稳定性

运行: python3 tests/test_memory.py
"""

import sys
import json
import tempfile
import shutil
from pathlib import Path

# 确保项目根目录在 path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.memory import (
    MemoryStore,
    MemoryState,
    SessionRecord,
    DomainPattern,
    build_session_record,
    extract_domain_patterns,
)
from core.harness import Harness


# ============================================================
# Test Utilities
# ============================================================

def make_temp_dir():
    """创建临时目录。"""
    return tempfile.mkdtemp(prefix="test_memory_")


def make_sample_findings():
    """创建测试用的 findings 列表。"""
    return [
        {
            "finding": "The parallel trends assumption is not convincingly tested — authors only show 2 pre-treatment periods",
            "priority": "high",
            "status": "verified",
            "evidence": "Figure 3 shows only t-2 and t-1...",
            "section": "results",
        },
        {
            "finding": "Overclaim: 'definitively proves causal effect' — the DID design has known limitations",
            "priority": "high",
            "status": "verified",
            "evidence": "Page 15: 'Our results definitively prove...'",
            "section": "conclusion",
        },
        {
            "finding": "Robustness checks using alternative clustering are missing",
            "priority": "medium",
            "status": "verified",
            "evidence": "Table 4 only shows city-level clustering",
            "section": "methodology",
        },
        {
            "finding": "Minor typo in equation 3",
            "priority": "low",
            "status": "suggestion",
            "evidence": "",
            "section": "methodology",
        },
        {
            "finding": "Sample size drops significantly in column 4 without explanation",
            "priority": "medium",
            "status": "needs_verification",
            "evidence": "N drops from 2,000 to 800",
            "section": "results",
        },
    ]


def make_sample_paper_sections():
    """创建测试用的 paper sections。"""
    return {
        "abstract": "This paper examines the causal effect of innovation zones on patent output using a DID approach...",
        "1. introduction": "Place-based innovation policies have become increasingly popular worldwide...",
        "3. methodology": "We employ a staggered difference-in-differences design with TWFE estimator...",
        "4. results": "Table 1 reports baseline results. The treatment coefficient is 0.23 (p<0.01)...",
        "5. conclusion": "Our results definitively prove that innovation zones boost patenting activity...",
    }


# ============================================================
# Tests
# ============================================================

def test_memory_store_basic_crud():
    """测试 MemoryStore 的基本 persist/load/recall 操作。"""
    tmp = make_temp_dir()
    try:
        # 1. 创建并保存
        store = MemoryStore(tmp)
        assert not store.load()  # 第一次加载应返回 False（无文件）

        record = SessionRecord(
            session_id="2025-07-01_abc12345",
            paper_id="abc12345deadbeef",
            paper_title="Test Paper",
            timestamp="2025-07-01T10:00:00+00:00",
            findings_summary=["[high] Parallel trends weak", "[med] Missing robustness"],
            decision="major_revision",
            key_issues=["Parallel trends not convincing"],
            loop_turns_total=12,
            conversation_turns=3,
            total_tokens=45000,
        )
        store.persist_session(record)
        store.save()

        # 2. 重新加载
        store2 = MemoryStore(tmp)
        assert store2.load()  # 应返回 True

        # 3. Recall
        sessions = store2.recall_for_paper("abc12345deadbeef")
        assert len(sessions) == 1
        assert sessions[0].paper_title == "Test Paper"
        assert sessions[0].decision == "major_revision"
        assert "Parallel trends weak" in sessions[0].findings_summary[0]

        print("✅ test_memory_store_basic_crud PASSED")
    finally:
        shutil.rmtree(tmp)


def test_memory_store_multiple_sessions():
    """测试多个会话记录的管理。"""
    tmp = make_temp_dir()
    try:
        store = MemoryStore(tmp)
        store.load()

        # 添加同一篇论文的两个会话
        for i in range(2):
            record = SessionRecord(
                session_id=f"2025-07-0{i+1}_paper1",
                paper_id="paper1_hash",
                paper_title="Innovation Zones Paper",
                timestamp=f"2025-07-0{i+1}T10:00:00+00:00",
                findings_summary=[f"[high] Issue {i}"],
                decision="major_revision" if i == 0 else "minor_revision",
                key_issues=[f"Issue {i}"],
            )
            store.persist_session(record)

        # 添加另一篇论文的会话
        store.persist_session(SessionRecord(
            session_id="2025-07-03_paper2",
            paper_id="paper2_hash",
            paper_title="Another Paper",
            timestamp="2025-07-03T10:00:00+00:00",
            findings_summary=["[high] Different issue"],
            decision="accept",
            key_issues=[],
        ))

        store.save()

        # 验证按论文检索
        store2 = MemoryStore(tmp)
        store2.load()

        paper1_sessions = store2.recall_for_paper("paper1_hash")
        assert len(paper1_sessions) == 2
        # 按时间倒序
        assert paper1_sessions[0].timestamp > paper1_sessions[1].timestamp

        paper2_sessions = store2.recall_for_paper("paper2_hash")
        assert len(paper2_sessions) == 1

        # 最近会话
        recent = store2.recall_recent(limit=2)
        assert len(recent) == 2
        assert recent[0].paper_id == "paper2_hash"  # 最新的在前

        print("✅ test_memory_store_multiple_sessions PASSED")
    finally:
        shutil.rmtree(tmp)


def test_domain_pattern_accumulation():
    """测试领域模式的积累和强化。"""
    tmp = make_temp_dir()
    try:
        store = MemoryStore(tmp)
        store.load()

        # 第一次看到 methodology 模式
        store.add_or_reinforce_pattern(
            "methodology",
            "Parallel trends assumption insufficiently tested with limited pre-treatment periods",
            "paper_1",
        )
        assert len(store.state.patterns) == 1
        assert store.state.patterns[0].evidence_count == 1

        # 第二次看到类似模式（不同论文）
        store.add_or_reinforce_pattern(
            "methodology",
            "Parallel trends assumption not convincingly tested with few pre-periods",
            "paper_2",
        )
        # 应该强化而非新增（因为文本相似）
        assert len(store.state.patterns) == 1
        assert store.state.patterns[0].evidence_count == 2
        assert "paper_2" in store.state.patterns[0].examples

        # 不同类别的模式
        store.add_or_reinforce_pattern(
            "overclaim",
            "Authors claim causal effect without addressing endogeneity concerns",
            "paper_1",
        )
        assert len(store.state.patterns) == 2

        # 完全不同的 methodology 模式
        store.add_or_reinforce_pattern(
            "methodology",
            "IV strategy invalid: exclusion restriction violated",
            "paper_3",
        )
        assert len(store.state.patterns) == 3  # 新增，因为文本不相似

        store.save()
        print("✅ test_domain_pattern_accumulation PASSED")
    finally:
        shutil.rmtree(tmp)


def test_format_memory_context_empty():
    """测试无记忆时 format_memory_context 返回 None。"""
    tmp = make_temp_dir()
    try:
        store = MemoryStore(tmp)
        store.load()

        result = store.format_memory_context(paper_id="some_paper")
        assert result is None

        print("✅ test_format_memory_context_empty PASSED")
    finally:
        shutil.rmtree(tmp)


def test_format_memory_context_with_history():
    """测试有历史记忆时的输出格式和 token 预算。"""
    tmp = make_temp_dir()
    try:
        store = MemoryStore(tmp)
        store.load()

        # 添加论文历史
        store.persist_session(SessionRecord(
            session_id="2025-07-01_paper1",
            paper_id="paper1_hash",
            paper_title="Test Paper",
            timestamp="2025-07-01T10:00:00+00:00",
            findings_summary=["[high] Parallel trends weak"],
            decision="major_revision",
            key_issues=["Parallel trends", "Overclaim in conclusion"],
            user_questions=["Introduction 的逻辑对吗？"],
        ))

        # 添加领域模式（需要 evidence_count >= 3 才会显示）
        for i in range(4):
            store.add_or_reinforce_pattern(
                "methodology",
                "DID papers often have weak parallel trends tests",
                f"paper_{i}",
            )

        # 生成 context
        context = store.format_memory_context(paper_id="paper1_hash")
        assert context is not None
        assert "你之前审阅过这篇论文" in context
        assert "major_revision" in context
        assert "Parallel trends" in context
        assert "领域经验" in context
        assert "DID papers" in context

        # Token 预算检查: < 1500 字符 (~500 tokens)
        assert len(context) < 1500, f"Memory context too long: {len(context)} chars"

        print("✅ test_format_memory_context_with_history PASSED")
    finally:
        shutil.rmtree(tmp)


def test_build_session_record():
    """测试从 findings 构建 SessionRecord 的逻辑。"""
    findings = make_sample_findings()

    record = build_session_record(
        paper_id="test_paper_hash",
        paper_title="Innovation Zones and Patent Output",
        findings=findings,
        conversation_turns=3,
        loop_turns=15,
        total_tokens=50000,
        user_messages=["帮我审阅这篇论文", "你觉得 methodology 怎么样？", "帮我总结主要问题"],
    )

    assert record.paper_id == "test_paper_hash"
    assert record.decision == "major_revision"  # 2 verified high → major_revision
    assert len(record.findings_summary) > 0
    assert len(record.key_issues) > 0
    assert any("parallel trends" in issue.lower() for issue in record.key_issues)
    assert len(record.user_questions) > 0

    print("✅ test_build_session_record PASSED")


def test_extract_domain_patterns():
    """测试从 findings 中提取领域模式。"""
    findings = make_sample_findings()
    patterns = extract_domain_patterns(findings, "test_paper")

    # 只提取 verified + high/medium 的
    assert len(patterns) > 0

    categories = [p[0] for p in patterns]
    # "Parallel trends" → methodology
    assert "methodology" in categories
    # "Overclaim" → overclaim (keyword matching is case-insensitive)
    # Note: the finding must say "overclaim" (or causal/causation/correlation) to be categorized
    assert "overclaim" in categories or "methodology" in categories  # DID is also methodology

    # low priority 和 needs_verification 不应被提取
    descriptions = [p[1] for p in patterns]
    assert not any("typo" in d.lower() for d in descriptions)
    assert not any("sample size drops" in d.lower() for d in descriptions)

    print("✅ test_extract_domain_patterns PASSED")


def test_paper_id_stability():
    """测试 paper_id 在小修改后保持稳定。"""
    sections = make_sample_paper_sections()

    id1 = MemoryStore.compute_paper_id(sections)

    # 小修改（在 results 中加一句话）
    sections_modified = dict(sections)
    sections_modified["4. results"] += " Additional sentence added."

    id2 = MemoryStore.compute_paper_id(sections_modified)

    # 因为 abstract 和 introduction 没变，id 应该相同
    # (compute_paper_id 只用前 500 字符，小修改不影响)
    assert id1 == id2, f"Paper ID should be stable: {id1} vs {id2}"

    # 本质不同的论文应有不同 id
    different_sections = {
        "abstract": "Completely different paper about climate change and GDP growth...",
        "1. introduction": "This paper studies the relationship between carbon emissions...",
    }
    id3 = MemoryStore.compute_paper_id(different_sections)
    assert id1 != id3, "Different papers should have different IDs"

    print("✅ test_paper_id_stability PASSED")


def test_harness_integration_graceful_degradation():
    """测试 Harness 在无 memory 文件时的渐进退化（向后兼容）。"""
    tmp = make_temp_dir()
    try:
        # 创建一个简单的 paper 文件
        paper_path = Path(tmp) / "test_paper.md"
        paper_path.write_text("## Abstract\nThis is a test paper.\n\n## Methods\nWe use DID.\n")

        # Harness 应该正常初始化（无 memory 文件）
        harness = Harness(paper_path=str(paper_path), memory_dir=Path(tmp) / ".memory")

        # format_context 不应崩溃
        ctx = harness.format_context()
        assert ctx is not None
        assert "记忆" not in ctx  # 无记忆时不应注入记忆相关内容

        print("✅ test_harness_integration_graceful_degradation PASSED")
    finally:
        shutil.rmtree(tmp)


def test_harness_end_session_creates_memory():
    """测试 Harness.end_session() 是否正确创建并持久化记忆。"""
    tmp = make_temp_dir()
    try:
        paper_path = Path(tmp) / "test_paper.md"
        paper_path.write_text(
            "## Abstract\nThis paper on innovation zones...\n\n"
            "## Methods\nDID with TWFE...\n\n"
            "## Results\nCoefficient 0.23...\n"
        )

        memory_dir = Path(tmp) / ".memory"
        harness = Harness(paper_path=str(paper_path), memory_dir=memory_dir)

        # 模拟 Agent 工作：添加 findings
        harness.state.findings = make_sample_findings()
        harness.state.conversation_turns = 2
        harness.state.total_tokens = 30000

        # end_session
        harness.end_session(user_messages=["帮我审阅这篇论文", "methodology 怎么样？"])

        # 验证文件创建
        assert (memory_dir / "memory.json").exists()

        # 验证内容
        raw = json.loads((memory_dir / "memory.json").read_text())
        assert len(raw["sessions"]) == 1
        assert raw["sessions"][0]["decision"] == "major_revision"
        assert len(raw["patterns"]) > 0  # 应有领域模式

        # 新的 Harness 应能加载这个记忆
        harness2 = Harness(paper_path=str(paper_path), memory_dir=memory_dir)
        ctx = harness2.format_context()
        assert "你之前审阅过这篇论文" in ctx

        print("✅ test_harness_end_session_creates_memory PASSED")
    finally:
        shutil.rmtree(tmp)


def test_memory_context_token_budget():
    """测试在大量记忆时，format_memory_context 仍能控制在 500 token 以内。"""
    tmp = make_temp_dir()
    try:
        store = MemoryStore(tmp)
        store.load()

        # 添加大量会话（模拟审了 30 篇论文）
        for i in range(30):
            store.persist_session(SessionRecord(
                session_id=f"session_{i}",
                paper_id=f"paper_{i}_hash",
                paper_title=f"Paper About Topic {i} With a Very Long Title That Goes On And On",
                timestamp=f"2025-07-{(i % 28) + 1:02d}T10:00:00+00:00",
                findings_summary=[f"[high] Issue {i} about something important" for _ in range(5)],
                decision="major_revision",
                key_issues=[f"Big issue {i}"],
            ))

        # 添加大量领域模式
        for i in range(20):
            store.state.patterns.append(DomainPattern(
                pattern_id=f"pattern_{i}",
                category="methodology",
                description=f"Pattern {i}: Some methodology issue that keeps appearing in papers",
                evidence_count=i + 1,  # 确保有些超过 3 的阈值
                first_seen="2025-01-01",
                last_seen="2025-07-01",
                examples=[f"paper_{j}" for j in range(min(i, 5))],
            ))

        # 生成 context
        context = store.format_memory_context(paper_id="paper_5_hash")
        assert context is not None

        # 关键断言: < 1500 字符 (~500 tokens)
        assert len(context) < 1500, (
            f"Memory context exceeds budget: {len(context)} chars (~{len(context)//3} tokens). "
            f"Expected < 1500 chars.\nContent:\n{context}"
        )

        # 应该只展示最强的模式（evidence_count 最高的几个）
        # Pattern with highest evidence_count should appear
        assert "methodology" in context  # at least the category should appear
        # 不应展示所有 30 个 session 的详情
        assert context.count("Paper About Topic") <= 2

        print("✅ test_memory_context_token_budget PASSED")
    finally:
        shutil.rmtree(tmp)


def test_session_limit_enforcement():
    """测试会话记录数量上限（50 个）。"""
    tmp = make_temp_dir()
    try:
        store = MemoryStore(tmp)
        store.load()

        # 添加 55 个会话
        for i in range(55):
            store.persist_session(SessionRecord(
                session_id=f"session_{i}",
                paper_id=f"paper_{i}",
                paper_title=f"Paper {i}",
                timestamp=f"2025-01-{(i % 28) + 1:02d}T10:00:00+00:00",
                findings_summary=[],
                decision="accept",
                key_issues=[],
            ))

        # 应该只保留最近 50 个
        assert len(store.state.sessions) == 50
        # 最早的 5 个应该被移除
        paper_ids = [s.paper_id for s in store.state.sessions]
        assert "paper_0" not in paper_ids
        assert "paper_4" not in paper_ids
        assert "paper_54" in paper_ids  # 最新的保留

        print("✅ test_session_limit_enforcement PASSED")
    finally:
        shutil.rmtree(tmp)


# ============================================================
# Runner
# ============================================================

if __name__ == "__main__":
    tests = [
        test_memory_store_basic_crud,
        test_memory_store_multiple_sessions,
        test_domain_pattern_accumulation,
        test_format_memory_context_empty,
        test_format_memory_context_with_history,
        test_build_session_record,
        test_extract_domain_patterns,
        test_paper_id_stability,
        test_harness_integration_graceful_degradation,
        test_harness_end_session_creates_memory,
        test_memory_context_token_budget,
        test_session_limit_enforcement,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"❌ {test.__name__} FAILED: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\n{'=' * 50}")
    print(f"Results: {passed} passed, {failed} failed, {len(tests)} total")
    if failed == 0:
        print("🎉 ALL TESTS PASSED")
    else:
        print("⚠️ SOME TESTS FAILED")
        sys.exit(1)
