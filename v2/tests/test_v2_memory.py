"""
Tests for core/memory.py — 跨会话认知记忆独立单元测试

Phase 0 of C3 Gödel Agent: 在 memory.py 上建 meta 层之前，
必须先确保这个持久化基座的每个公开 API 在正常和边界情况下都正确。

覆盖:
- MemoryStore: load/save 持久化 round-trip
- MemoryStore: persist_session + recall_for_paper + recall_recent
- MemoryStore: add_or_reinforce_pattern + get_relevant_patterns
- MemoryStore: add_or_reinforce_procedure + get_relevant_procedures
- MemoryStore: format_memory_context
- MemoryStore: _is_similar 相似度判断
- MemoryStore: compute_paper_id 论文指纹
- MemoryStore: 容量限制（sessions max 50, patterns max 100, procedures max 50）
- build_session_record: 从 findings 构建会话记录
- extract_procedural_patterns: 工具序列分析
- extract_domain_patterns: finding 领域分类
- _gc_procedures: 记忆淘汰机制 (Phase 0 新增)
"""

import sys
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from core.memory import (
    MemoryStore,
    MemoryState,
    SessionRecord,
    DomainPattern,
    ProceduralPattern,
    build_session_record,
    extract_procedural_patterns,
    extract_domain_patterns,
    _find_productive_sequences,
    _find_anti_patterns,
    _infer_decision,
    _categorize_finding,
    _generalize_finding,
)


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def store(tmp_path):
    """空的 MemoryStore，使用临时目录。"""
    return MemoryStore(tmp_path / ".memory")


@pytest.fixture
def store_with_data(tmp_path):
    """预填充数据的 MemoryStore。"""
    s = MemoryStore(tmp_path / ".memory")
    # 添加 3 个 sessions
    for i in range(3):
        s.persist_session(SessionRecord(
            session_id=f"session_{i}",
            paper_id=f"paper_{i % 2}",  # 两篇论文
            paper_title=f"Test Paper {i}",
            timestamp=f"2025-01-0{i+1}T00:00:00+00:00",
            findings_summary=[f"[high] Finding {i}"],
            decision="major_revision",
            key_issues=[f"Issue {i}"],
            loop_turns_total=10 + i,
            conversation_turns=5,
            total_tokens=1000 * (i + 1),
        ))
    # 添加 patterns
    s.add_or_reinforce_pattern("methodology", "DID papers often lack parallel trend tests", "paper_0")
    s.add_or_reinforce_pattern("methodology", "DID papers often lack parallel trend tests", "paper_1")
    s.add_or_reinforce_pattern("methodology", "DID papers often lack parallel trend tests", "paper_2")
    s.add_or_reinforce_pattern("overclaim", "Causal claims from observational data", "paper_0")
    # 添加 procedures
    s.add_or_reinforce_procedure(
        "strategy_effectiveness",
        "deep_investigation after 3+ findings is most efficient",
        "when findings >= 3",
        0.8,
    )
    return s


# ============================================================
# MemoryStore: Load / Save Round-trip
# ============================================================

class TestMemoryStorePersistence:
    """测试 MemoryStore 的持久化 round-trip。"""

    def test_save_and_load_empty(self, store):
        """空状态可以保存和加载。"""
        store.save()
        store2 = MemoryStore(store.base_dir)
        loaded = store2.load()
        assert loaded is True
        assert store2.state.version == "3.0"
        assert store2.state.sessions == []
        assert store2.state.patterns == []
        assert store2.state.procedures == []

    def test_save_and_load_with_data(self, store_with_data):
        """带数据的状态保存后可以完整恢复。"""
        store_with_data.save()
        store2 = MemoryStore(store_with_data.base_dir)
        store2.load()
        assert len(store2.state.sessions) == 3
        assert len(store2.state.patterns) == 2  # 2 distinct patterns
        assert len(store2.state.procedures) == 1
        # 验证 procedure 数据完整性
        proc = store2.state.procedures[0]
        assert proc.category == "strategy_effectiveness"
        assert proc.effectiveness_score == 0.8
        assert proc.evidence_count == 1

    def test_load_nonexistent_file(self, tmp_path):
        """加载不存在的文件应返回 False 且使用空状态。"""
        store = MemoryStore(tmp_path / "nonexistent")
        result = store.load()
        assert result is False
        assert store.state.sessions == []

    def test_load_corrupted_file(self, tmp_path):
        """损坏的 JSON 文件应返回 False 且使用空状态（渐进退化）。"""
        mem_dir = tmp_path / ".memory"
        mem_dir.mkdir()
        (mem_dir / "memory.json").write_text("not valid json {{{", encoding="utf-8")
        store = MemoryStore(mem_dir)
        result = store.load()
        assert result is False
        assert store.state.sessions == []

    def test_load_v1_format_backward_compatible(self, tmp_path):
        """v1.0 格式（无 procedures 字段）应向后兼容。"""
        mem_dir = tmp_path / ".memory"
        mem_dir.mkdir()
        v1_data = {
            "version": "1.0",
            "last_updated": "2025-01-01T00:00:00+00:00",
            "sessions": [],
            "patterns": [],
            # 注意：没有 "procedures" 字段
        }
        (mem_dir / "memory.json").write_text(json.dumps(v1_data), encoding="utf-8")
        store = MemoryStore(mem_dir)
        result = store.load()
        assert result is True
        assert store.state.procedures == []
        assert store.state.version == "1.0"

    def test_save_creates_directory(self, tmp_path):
        """save() 应自动创建不存在的目录。"""
        deep_path = tmp_path / "a" / "b" / "c"
        store = MemoryStore(deep_path)
        store.persist_session(SessionRecord(
            session_id="s1", paper_id="p1", paper_title="T",
            timestamp="2025-01-01", findings_summary=[], decision="accept",
            key_issues=[],
        ))
        store.save()
        assert (deep_path / "memory.json").exists()


# ============================================================
# MemoryStore: Session Layer
# ============================================================

class TestSessionLayer:
    """测试 Session 层的 CRUD 操作。"""

    def test_persist_and_recall_for_paper(self, store):
        """存储后可以按 paper_id 召回。"""
        record = SessionRecord(
            session_id="s1", paper_id="p_abc", paper_title="Paper ABC",
            timestamp="2025-06-01T00:00:00+00:00",
            findings_summary=["[high] Test"], decision="major_revision",
            key_issues=["issue1"],
        )
        store.persist_session(record)
        results = store.recall_for_paper("p_abc")
        assert len(results) == 1
        assert results[0].session_id == "s1"

    def test_recall_for_paper_empty(self, store):
        """没有匹配时返回空列表。"""
        assert store.recall_for_paper("nonexistent") == []

    def test_recall_recent(self, store_with_data):
        """recall_recent 按时间倒序返回。"""
        results = store_with_data.recall_recent(limit=2)
        assert len(results) == 2
        # 最新的在前
        assert results[0].timestamp > results[1].timestamp

    def test_session_capacity_limit(self, store):
        """超过 50 个 session 时自动裁剪旧的。"""
        for i in range(55):
            store.persist_session(SessionRecord(
                session_id=f"s_{i:03d}", paper_id=f"p_{i}", paper_title=f"P {i}",
                timestamp=f"2025-01-{(i % 28) + 1:02d}T00:00:00+00:00",
                findings_summary=[], decision="accept", key_issues=[],
            ))
        assert len(store.state.sessions) == 50
        # 应该保留最后 50 个（按追加顺序裁剪前面的）
        assert store.state.sessions[0].session_id == "s_005"
        assert store.state.sessions[-1].session_id == "s_054"


# ============================================================
# MemoryStore: Domain Pattern Layer
# ============================================================

class TestDomainPatternLayer:
    """测试 Domain Pattern 层操作。"""

    def test_add_new_pattern(self, store):
        """添加新模式。"""
        store.add_or_reinforce_pattern("methodology", "RDD needs bandwidth selection", "p1")
        assert len(store.state.patterns) == 1
        p = store.state.patterns[0]
        assert p.category == "methodology"
        assert p.evidence_count == 1
        assert p.examples == ["p1"]

    def test_reinforce_similar_pattern(self, store):
        """相似的模式应被 reinforce 而非重复创建。"""
        store.add_or_reinforce_pattern("methodology", "DID papers lack parallel trend tests", "p1")
        store.add_or_reinforce_pattern("methodology", "DID papers often lack parallel trend tests", "p2")
        # 应该只有 1 个 pattern（因为相似度 > 50%）
        assert len(store.state.patterns) == 1
        p = store.state.patterns[0]
        assert p.evidence_count == 2
        assert "p2" in p.examples

    def test_different_category_not_merged(self, store):
        """不同 category 的相似描述不应合并。"""
        store.add_or_reinforce_pattern("methodology", "Sample selection bias", "p1")
        store.add_or_reinforce_pattern("statistics", "Sample selection bias in statistics", "p2")
        assert len(store.state.patterns) == 2

    def test_examples_capped_at_10(self, store):
        """每个 pattern 的 examples 最多 10 个。"""
        for i in range(15):
            store.add_or_reinforce_pattern("methodology", "Common DID issue", f"paper_{i}")
        p = store.state.patterns[0]
        assert len(p.examples) <= 10

    def test_pattern_capacity_limit(self, store):
        """超过 100 个 patterns 时保留 evidence_count 最高的。"""
        # 先添加 1 个高 evidence 的
        for _ in range(5):
            store.add_or_reinforce_pattern("methodology", "High evidence pattern", "p_high")
        # 再添加 105 个不同的（每个 evidence=1）
        for i in range(105):
            store.state.patterns.append(DomainPattern(
                pattern_id=f"filler_{i}",
                category="logic",
                description=f"Unique filler pattern number {i} that is definitely different",
                evidence_count=1,
                first_seen="2025-01-01",
                last_seen="2025-01-01",
                examples=[],
            ))
        # 触发容量检查（通过添加一个新的）
        store.add_or_reinforce_pattern("statistics", "Triggering capacity check unique xyz", "p_trigger")
        # 高 evidence 的应该被保留
        high_ev = [p for p in store.state.patterns if p.evidence_count >= 5]
        assert len(high_ev) >= 1
        assert len(store.state.patterns) <= 100

    def test_get_relevant_patterns_by_category(self, store_with_data):
        """按 category 过滤 patterns。"""
        results = store_with_data.get_relevant_patterns(categories=["overclaim"])
        assert len(results) == 1
        assert results[0].category == "overclaim"

    def test_get_relevant_patterns_sorted_by_evidence(self, store_with_data):
        """结果按 evidence_count 排序。"""
        results = store_with_data.get_relevant_patterns()
        if len(results) >= 2:
            assert results[0].evidence_count >= results[1].evidence_count


# ============================================================
# MemoryStore: Procedural Pattern Layer
# ============================================================

class TestProceduralPatternLayer:
    """测试 Procedural Pattern 层操作。"""

    def test_add_new_procedure(self, store):
        """添加新的程序性模式。"""
        store.add_or_reinforce_procedure(
            "tool_sequence", "read→search→update is productive", "when starting review", 0.7
        )
        assert len(store.state.procedures) == 1
        p = store.state.procedures[0]
        assert p.category == "tool_sequence"
        assert p.effectiveness_score == 0.7
        assert p.evidence_count == 1

    def test_reinforce_similar_procedure(self, store):
        """相似的 procedure 应 reinforce（加权平均 effectiveness）。"""
        store.add_or_reinforce_procedure(
            "strategy_effectiveness", "deep investigation after findings", "when findings >= 3", 0.8
        )
        store.add_or_reinforce_procedure(
            "strategy_effectiveness", "deep investigation after many findings", "when findings >= 3", 0.6
        )
        assert len(store.state.procedures) == 1
        p = store.state.procedures[0]
        assert p.evidence_count == 2
        # 加权平均: (0.8 * 1 + 0.6 * 1) / 2 = 0.7
        assert abs(p.effectiveness_score - 0.7) < 0.01

    def test_reinforce_weighted_average_with_high_evidence(self, store):
        """高 evidence 时，新值的权重较低。"""
        store.add_or_reinforce_procedure("strategy_effectiveness", "test pattern", "ctx", 0.8)
        # reinforce 4 次，全是 0.8
        for _ in range(4):
            store.add_or_reinforce_procedure("strategy_effectiveness", "test pattern weighted", "ctx", 0.8)
        # 现在 evidence=5, effectiveness=0.8
        # 再来一个 0.0 的：新权重 = (0.8*5 + 0.0*1) / 6 = 0.667
        store.add_or_reinforce_procedure("strategy_effectiveness", "test pattern weighted", "ctx", 0.0)
        p = [pp for pp in store.state.procedures if pp.evidence_count >= 5][0]
        # 高 evidence 的记录不会被单次低分大幅拉低
        assert p.effectiveness_score > 0.6

    def test_procedure_capacity_limit(self, store):
        """超过 50 个 procedures 时按 effectiveness*evidence 排序裁剪。"""
        # 添加 1 个高分的
        store.add_or_reinforce_procedure("strategy_effectiveness", "best pattern ever", "always", 0.95)
        for _ in range(4):
            store.add_or_reinforce_procedure("strategy_effectiveness", "best pattern ever", "always", 0.95)
        # 添加 55 个低分的
        for i in range(55):
            store.state.procedures.append(ProceduralPattern(
                pattern_id=f"filler_{i}",
                category="anti_pattern",
                description=f"Low value filler unique {i} xyzzy",
                trigger_context="never",
                effectiveness_score=0.1,
                evidence_count=1,
                first_seen="2025-01-01",
                last_seen="2025-01-01",
            ))
        # 触发容量检查
        store.add_or_reinforce_procedure("tool_sequence", "Trigger capacity unique abcdef", "ctx", 0.2)
        assert len(store.state.procedures) <= 50
        # 高分的应该保留
        best = [p for p in store.state.procedures if p.effectiveness_score >= 0.9]
        assert len(best) >= 1

    def test_get_relevant_procedures_sorted(self, store):
        """结果按 effectiveness * evidence 排序。"""
        store.add_or_reinforce_procedure("strategy_effectiveness", "good pattern", "ctx", 0.9)
        for _ in range(2):
            store.add_or_reinforce_procedure("strategy_effectiveness", "good pattern", "ctx", 0.9)
        store.add_or_reinforce_procedure("tool_sequence", "okay pattern different unique", "ctx", 0.5)
        results = store.get_relevant_procedures(limit=5)
        assert len(results) == 2
        # 第一个应该是 effectiveness * evidence 更高的
        assert results[0].effectiveness_score * results[0].evidence_count >= \
               results[1].effectiveness_score * results[1].evidence_count

    def test_get_relevant_procedures_by_category(self, store):
        """按 category 过滤 procedures。"""
        store.add_or_reinforce_procedure("strategy_effectiveness", "strat pattern", "ctx", 0.8)
        store.add_or_reinforce_procedure("anti_pattern", "anti pattern unique", "ctx", 0.2)
        results = store.get_relevant_procedures(categories=["anti_pattern"])
        assert len(results) == 1
        assert results[0].category == "anti_pattern"

    def test_effectiveness_score_bounds(self, store):
        """effectiveness_score 在 0.0~1.0 范围内不会越界。"""
        store.add_or_reinforce_procedure("strategy_effectiveness", "test bounds", "ctx", 1.0)
        store.add_or_reinforce_procedure("strategy_effectiveness", "test bounds", "ctx", 1.0)
        p = store.state.procedures[0]
        assert 0.0 <= p.effectiveness_score <= 1.0


# ============================================================
# MemoryStore: format_memory_context
# ============================================================

class TestFormatMemoryContext:
    """测试记忆上下文生成。"""

    def test_empty_memory_returns_none(self, store):
        """空记忆返回 None。"""
        assert store.format_memory_context() is None

    def test_with_paper_history(self, store_with_data):
        """有论文历史时包含历史信息。"""
        ctx = store_with_data.format_memory_context(paper_id="paper_0")
        assert ctx is not None
        assert "审阅过" in ctx or "📚" in ctx

    def test_with_strong_patterns(self, store_with_data):
        """有高频模式时包含领域经验。"""
        ctx = store_with_data.format_memory_context()
        assert ctx is not None
        assert "领域经验" in ctx or "🧠" in ctx

    def test_with_procedures(self, store_with_data):
        """有程序性记忆时包含高效模式。"""
        ctx = store_with_data.format_memory_context()
        assert "高效" in ctx or "⚡" in ctx

    def test_context_not_too_long(self, store_with_data):
        """上下文长度 < 1500 字符（~500 tokens 目标）。"""
        ctx = store_with_data.format_memory_context(paper_id="paper_0")
        assert len(ctx) < 1500


# ============================================================
# MemoryStore: Helpers
# ============================================================

class TestHelpers:
    """测试辅助方法。"""

    def test_is_similar_high_overlap(self):
        """高重叠率应判为相似。"""
        assert MemoryStore._is_similar(
            "DID papers often lack parallel trend tests",
            "DID papers frequently lack parallel trend validation",
        ) is True

    def test_is_similar_low_overlap(self):
        """低重叠率应判为不相似。"""
        assert MemoryStore._is_similar(
            "DID papers often lack parallel trend tests",
            "RCT sample size calculation is important",
        ) is False

    def test_is_similar_empty_strings(self):
        """空字符串应返回 False。"""
        assert MemoryStore._is_similar("", "something") is False
        assert MemoryStore._is_similar("something", "") is False

    def test_is_similar_identical(self):
        """完全相同的字符串应判为相似。"""
        assert MemoryStore._is_similar("exact same text", "exact same text") is True

    def test_compute_paper_id_deterministic(self):
        """相同输入应产生相同 paper_id。"""
        sections = {"Abstract": "This paper studies...", "Introduction": "We investigate..."}
        id1 = MemoryStore.compute_paper_id(sections)
        id2 = MemoryStore.compute_paper_id(sections)
        assert id1 == id2

    def test_compute_paper_id_different_papers(self):
        """不同论文应产生不同 paper_id。"""
        id1 = MemoryStore.compute_paper_id({"Abstract": "Paper about DID"})
        id2 = MemoryStore.compute_paper_id({"Abstract": "Paper about RCT"})
        assert id1 != id2

    def test_compute_paper_id_length(self):
        """paper_id 应为 16 字符 hex。"""
        pid = MemoryStore.compute_paper_id({"Abstract": "Test"})
        assert len(pid) == 16
        assert all(c in "0123456789abcdef" for c in pid)


# ============================================================
# build_session_record
# ============================================================

class TestBuildSessionRecord:
    """测试会话记录构建。"""

    def test_basic_build(self):
        """基本构建。"""
        findings = [
            {"finding": "Major issue with identification", "priority": "high", "status": "verified"},
            {"finding": "Minor typo in abstract", "priority": "low", "status": "verified"},
        ]
        record = build_session_record(
            paper_id="p123",
            paper_title="Test Paper",
            findings=findings,
            conversation_turns=5,
            loop_turns=20,
            total_tokens=5000,
        )
        assert record.paper_id == "p123"
        assert record.paper_title == "Test Paper"
        assert record.loop_turns_total == 20
        assert record.total_tokens == 5000
        assert len(record.findings_summary) >= 1

    def test_decision_inference_reject(self):
        """3+ verified high → reject。"""
        findings = [
            {"finding": f"Critical issue {i}", "priority": "high", "status": "verified"}
            for i in range(3)
        ]
        record = build_session_record("p", "T", findings, 5, 20, 5000)
        assert record.decision == "reject"

    def test_decision_inference_accept(self):
        """无 high findings → accept。"""
        findings = [
            {"finding": "Minor issue", "priority": "low", "status": "verified"},
        ]
        record = build_session_record("p", "T", findings, 5, 20, 5000)
        assert record.decision == "accept"

    def test_decision_inference_incomplete(self):
        """无 findings → incomplete。"""
        record = build_session_record("p", "T", [], 0, 0, 0)
        assert record.decision == "incomplete"

    def test_user_questions_extraction(self):
        """用户问题被正确提取。"""
        record = build_session_record(
            "p", "T", [], 5, 20, 5000,
            user_messages=["What about the methodology?", "Please check the data section"]
        )
        assert len(record.user_questions) >= 1

    def test_findings_summary_truncation(self):
        """超长 finding 应被截断。"""
        findings = [
            {"finding": "A" * 200, "priority": "high", "status": "verified"},
        ]
        record = build_session_record("p", "T", findings, 5, 20, 5000)
        for s in record.findings_summary:
            # [high] prefix + 80 chars max
            assert len(s) <= 90


# ============================================================
# extract_procedural_patterns
# ============================================================

class TestExtractProceduralPatterns:
    """测试程序性模式提取。"""

    def test_empty_history(self):
        """空工具历史返回空。"""
        result = extract_procedural_patterns([], 0, 0)
        assert result == []

    def test_productive_sequence_detection(self):
        """检测高产工具序列。"""
        # 创建一个 read→search→read→update_findings 的重复模式
        history = ["read_section", "search_literature", "read_section", "update_findings"] * 3
        result = extract_procedural_patterns(history, findings_count=3, loop_turns=12)
        tool_seq_patterns = [p for p in result if p[0] == "tool_sequence"]
        assert len(tool_seq_patterns) >= 1

    def test_anti_pattern_detection(self):
        """检测低效重复模式。"""
        # 连续 6 次 read_section 无 update_findings
        history = ["read_section"] * 6 + ["update_findings"]
        result = extract_procedural_patterns(history, findings_count=1, loop_turns=7)
        anti_patterns = [p for p in result if p[0] == "anti_pattern"]
        assert len(anti_patterns) >= 1

    def test_strategy_transitions(self):
        """策略切换被正确提取。"""
        history = ["read_section"] * 5 + ["update_findings"] * 2
        transitions = [("systematic_scan", "deep_investigation")]
        result = extract_procedural_patterns(
            history, findings_count=3, loop_turns=7,
            strategy_transitions=transitions,
        )
        strat_patterns = [p for p in result if p[0] == "strategy_effectiveness"]
        assert len(strat_patterns) >= 1

    def test_short_history_no_patterns(self):
        """过短的历史不产出 patterns。"""
        result = extract_procedural_patterns(["read_section", "update_findings"], 1, 2)
        # 太短不应产出 anti_pattern 或 tool_sequence
        anti = [p for p in result if p[0] == "anti_pattern"]
        assert len(anti) == 0


# ============================================================
# extract_domain_patterns
# ============================================================

class TestExtractDomainPatterns:
    """测试领域模式提取。"""

    def test_methodology_detection(self):
        """方法论 finding 被正确分类。"""
        findings = [
            {"finding": "The identification strategy has endogeneity concerns",
             "priority": "high", "status": "verified"},
        ]
        result = extract_domain_patterns(findings, "p1")
        assert len(result) >= 1
        assert result[0][0] == "methodology"

    def test_overclaim_detection(self):
        """过度声明被正确分类。"""
        findings = [
            {"finding": "The paper makes a causal claim but only has correlational evidence",
             "priority": "high", "status": "verified"},
        ]
        result = extract_domain_patterns(findings, "p1")
        assert len(result) >= 1
        assert result[0][0] == "overclaim"

    def test_unverified_findings_skipped(self):
        """未验证的 findings 不提取。"""
        findings = [
            {"finding": "Endogeneity problem", "priority": "high", "status": "unverified"},
        ]
        result = extract_domain_patterns(findings, "p1")
        assert result == []

    def test_low_priority_findings_skipped(self):
        """低优先级 findings 不提取。"""
        findings = [
            {"finding": "Minor endogeneity concern", "priority": "low", "status": "verified"},
        ]
        result = extract_domain_patterns(findings, "p1")
        assert result == []


# ============================================================
# Internal helpers
# ============================================================

class TestInternalHelpers:
    """测试内部辅助函数。"""

    def test_infer_decision_levels(self):
        """测试不同 findings 组合的决定推断。"""
        # 0 findings → incomplete
        assert _infer_decision([]) == "incomplete"
        # 3 verified high → reject
        assert _infer_decision([
            {"priority": "high", "status": "verified"} for _ in range(3)
        ]) == "reject"
        # 1 verified high → major_revision
        assert _infer_decision([
            {"priority": "high", "status": "verified"},
            {"priority": "medium", "status": "verified"},
        ]) == "major_revision"

    def test_categorize_finding_returns_none_for_uncategorizable(self):
        """无法分类的 finding 返回 None。"""
        result = _categorize_finding("The paper is well written overall")
        assert result is None

    def test_generalize_finding_short_text(self):
        """过短的文本返回 None。"""
        assert _generalize_finding("Short") is None

    def test_generalize_finding_truncation(self):
        """超长文本被截断到合理长度。"""
        long_text = "This is a very long finding " * 20
        result = _generalize_finding(long_text)
        assert result is not None
        assert len(result) <= 120

    def test_find_productive_sequences_minimum_length(self):
        """过短序列不产出结果。"""
        assert _find_productive_sequences(["a", "b"]) == []

    def test_find_anti_patterns_threshold(self):
        """低于 4 次重复不触发。"""
        history = ["read_section"] * 3 + ["update_findings"]
        assert _find_anti_patterns(history) == []


# ============================================================
# Serialization edge cases
# ============================================================

class TestSerializationEdgeCases:
    """测试序列化边界情况。"""

    def test_unicode_content(self, store):
        """中文内容正确序列化。"""
        store.add_or_reinforce_pattern("methodology", "DID论文缺少平行趋势检验", "p1")
        store.save()
        store2 = MemoryStore(store.base_dir)
        store2.load()
        assert store2.state.patterns[0].description == "DID论文缺少平行趋势检验"

    def test_special_characters_in_description(self, store):
        """特殊字符不破坏序列化。"""
        store.add_or_reinforce_procedure(
            "tool_sequence",
            'read→search→"update" (with quotes & ampersands)',
            "when reviewing",
            0.7,
        )
        store.save()
        store2 = MemoryStore(store.base_dir)
        store2.load()
        assert "→" in store2.state.procedures[0].description
        assert '"' in store2.state.procedures[0].description


# ============================================================
# gc_procedures — 记忆淘汰机制 (Phase 0 新增)
# ============================================================

class TestGcProcedures:
    """测试程序性记忆垃圾回收机制。"""

    def test_gc_empty_memory(self, store):
        """空记忆 GC 返回 0。"""
        removed = store.gc_procedures()
        assert removed == 0

    def test_gc_rule1_low_effectiveness_low_evidence(self, store):
        """规则1：低效(< 0.3) + 低验证(= 1) → 删除。"""
        store.state.procedures = [
            ProceduralPattern(
                pattern_id="low1",
                category="anti_pattern",
                description="bad pattern",
                trigger_context="ctx",
                effectiveness_score=0.2,  # < 0.3
                evidence_count=1,  # <= 1
                first_seen="2025-07-01T00:00:00+00:00",
                last_seen="2025-07-01T00:00:00+00:00",  # 今天，不触发规则2
            ),
        ]
        removed = store.gc_procedures()
        assert removed == 1
        assert len(store.state.procedures) == 0

    def test_gc_rule1_not_triggered_if_evidence_high(self, store):
        """规则1不触发：effectiveness 低但 evidence >= 2。"""
        store.state.procedures = [
            ProceduralPattern(
                pattern_id="low_eff_high_ev",
                category="anti_pattern",
                description="low eff but verified twice",
                trigger_context="ctx",
                effectiveness_score=0.2,  # < 0.3
                evidence_count=2,  # > 1, 不触发规则1
                first_seen="2025-07-01T00:00:00+00:00",
                last_seen=datetime.now(timezone.utc).isoformat(),
            ),
        ]
        removed = store.gc_procedures()
        assert removed == 0

    def test_gc_rule2_old_pattern_removed(self, store):
        """规则2：超过 60 天未 reinforce → 删除。"""
        old_date = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        store.state.procedures = [
            ProceduralPattern(
                pattern_id="old1",
                category="strategy_effectiveness",
                description="ancient pattern",
                trigger_context="ctx",
                effectiveness_score=0.5,  # 高于 0.3（不触发规则1）
                evidence_count=2,  # > 1（不触发规则1）
                first_seen=old_date,
                last_seen=old_date,  # 90 天前 > 60
            ),
        ]
        removed = store.gc_procedures()
        assert removed == 1
        assert len(store.state.procedures) == 0

    def test_gc_rule2_not_triggered_if_recent(self, store):
        """规则2不触发：last_seen 在 60 天内。"""
        recent_date = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        store.state.procedures = [
            ProceduralPattern(
                pattern_id="recent1",
                category="strategy_effectiveness",
                description="recent pattern",
                trigger_context="ctx",
                effectiveness_score=0.5,
                evidence_count=2,
                first_seen="2025-01-01T00:00:00+00:00",
                last_seen=recent_date,
            ),
        ]
        removed = store.gc_procedures()
        assert removed == 0

    def test_gc_protection_rule_evidence_gte_3(self, store):
        """保护规则：evidence >= 3 的永不被规则1/2删除。"""
        old_date = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
        store.state.procedures = [
            ProceduralPattern(
                pattern_id="protected1",
                category="anti_pattern",
                description="low eff but well verified",
                trigger_context="ctx",
                effectiveness_score=0.1,  # 触发规则1的条件
                evidence_count=3,  # >= 3 → 受保护
                first_seen=old_date,
                last_seen=old_date,  # 200 天前 → 触发规则2的条件
            ),
        ]
        removed = store.gc_procedures()
        assert removed == 0
        assert len(store.state.procedures) == 1

    def test_gc_rule3_hard_capacity_limit(self, store):
        """规则3：超过 max_size 后按分数裁剪（包括 evidence >= 3 的）。"""
        now = datetime.now(timezone.utc).isoformat()
        # 添加 60 个高 evidence 的（全部受规则1/2保护）
        store.state.procedures = [
            ProceduralPattern(
                pattern_id=f"high_ev_{i}",
                category="strategy_effectiveness",
                description=f"well verified pattern {i}",
                trigger_context="ctx",
                effectiveness_score=0.5 + (i * 0.005),  # 略有差异
                evidence_count=3 + (i % 3),
                first_seen=now,
                last_seen=now,
            )
            for i in range(60)
        ]
        removed = store.gc_procedures(max_size=50)
        assert removed == 10
        assert len(store.state.procedures) == 50
        # 保留的应该是分数最高的
        scores = [p.effectiveness_score * p.evidence_count for p in store.state.procedures]
        assert scores == sorted(scores, reverse=True)

    def test_gc_combined_rules(self, store):
        """综合测试：多种规则同时作用。"""
        now = datetime.now(timezone.utc).isoformat()
        old_date = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()

        store.state.procedures = [
            # 应被保留：高效且有验证
            ProceduralPattern(
                pattern_id="keep1", category="strategy_effectiveness",
                description="good recent pattern", trigger_context="ctx",
                effectiveness_score=0.8, evidence_count=2,
                first_seen=now, last_seen=now,
            ),
            # 应被保留：受保护（evidence >= 3）
            ProceduralPattern(
                pattern_id="keep2", category="strategy_effectiveness",
                description="protected pattern", trigger_context="ctx",
                effectiveness_score=0.1, evidence_count=5,
                first_seen=old_date, last_seen=old_date,
            ),
            # 应被删除：规则1（低效 + evidence=1）
            ProceduralPattern(
                pattern_id="delete1", category="anti_pattern",
                description="low quality single evidence", trigger_context="ctx",
                effectiveness_score=0.1, evidence_count=1,
                first_seen=now, last_seen=now,
            ),
            # 应被删除：规则2（太旧 + evidence < 3）
            ProceduralPattern(
                pattern_id="delete2", category="tool_sequence",
                description="forgotten old pattern", trigger_context="ctx",
                effectiveness_score=0.6, evidence_count=2,
                first_seen=old_date, last_seen=old_date,
            ),
        ]
        removed = store.gc_procedures()
        assert removed == 2
        remaining_ids = {p.pattern_id for p in store.state.procedures}
        assert "keep1" in remaining_ids
        assert "keep2" in remaining_ids
        assert "delete1" not in remaining_ids
        assert "delete2" not in remaining_ids

    def test_gc_returns_correct_count(self, store):
        """返回值正确反映删除数量。"""
        now = datetime.now(timezone.utc).isoformat()
        store.state.procedures = [
            ProceduralPattern(
                pattern_id=f"p{i}", category="anti_pattern",
                description=f"bad pattern {i}", trigger_context="ctx",
                effectiveness_score=0.1, evidence_count=1,
                first_seen=now, last_seen=now,
            )
            for i in range(5)
        ]
        removed = store.gc_procedures()
        assert removed == 5

    def test_gc_custom_parameters(self, store):
        """自定义参数正确生效。"""
        now = datetime.now(timezone.utc).isoformat()
        store.state.procedures = [
            ProceduralPattern(
                pattern_id="borderline", category="strategy_effectiveness",
                description="borderline effectiveness", trigger_context="ctx",
                effectiveness_score=0.25,  # < 0.3 but >= 0.2
                evidence_count=1,
                first_seen=now, last_seen=now,
            ),
        ]
        # 默认 min_effectiveness=0.3 会删除
        removed = store.gc_procedures(min_effectiveness=0.2)
        # 0.25 >= 0.2 → 不删除
        assert removed == 0

    def test_gc_idempotent(self, store):
        """连续调用 GC 结果不变（幂等性）。"""
        now = datetime.now(timezone.utc).isoformat()
        store.state.procedures = [
            ProceduralPattern(
                pattern_id="survivor", category="strategy_effectiveness",
                description="healthy pattern", trigger_context="ctx",
                effectiveness_score=0.7, evidence_count=2,
                first_seen=now, last_seen=now,
            ),
        ]
        removed1 = store.gc_procedures()
        removed2 = store.gc_procedures()
        assert removed1 == 0
        assert removed2 == 0
        assert len(store.state.procedures) == 1
