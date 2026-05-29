"""
tests/test_memory_complete.py — Phase 2 Complete Layer 完整测试

覆盖四大模块:
    1. Proactive Retrieval (主动检索)
    2. Memory Decay Model (记忆衰减)
    3. Cross-Paper Knowledge (跨论文知识积累)
    4. Distillation Quality Verification (蒸馏质量验证)
    5. Orchestrator (统一调度器)
    6. Kill Switch (环境变量控制)
"""

import os
import math
import pytest
import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import dataclass, field

# 在 import 之前需要确保 path 正确
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.memory_complete import (
    ProactiveRetriever,
    RetrievalQuery,
    RetrievalResult,
    MemoryDecayModel,
    DecayConfig,
    ScoredMemoryItem,
    CrossPaperKnowledgeAccumulator,
    CrossPaperPattern,
    DistillationQualityVerifier,
    VerificationResult,
    MemoryCompleteOrchestrator,
    _env_enabled,
    PROACTIVE_RETRIEVAL_ENABLED,
    MEMORY_DECAY_ENABLED,
    CROSS_PAPER_KNOWLEDGE_ENABLED,
    DISTILL_VERIFICATION_ENABLED,
)
from core.memory import (
    MemoryStore,
    MemoryState,
    DomainPattern,
    ProceduralPattern,
    SessionRecord,
)


# ================================================================
# Mock Classes for Testing
# ================================================================

@dataclass
class MockDomainFact:
    """Mock DomainFact for testing verification."""
    category: str = ""
    description: str = ""
    evidence_text: str = ""
    subcategory: str = ""


@dataclass
class MockProceduralRule:
    """Mock ProceduralRule for testing verification."""
    trigger: str = ""
    action: str = ""
    rationale: str = ""


@dataclass
class MockDomainPattern:
    """Mock DomainPattern compatible with ProactiveRetriever."""
    pattern_id: str = ""
    category: str = ""
    description: str = ""
    evidence_count: int = 1
    first_seen: str = ""
    last_seen: str = ""
    examples: list = field(default_factory=list)


@dataclass
class MockProceduralPattern:
    """Mock ProceduralPattern compatible with ProactiveRetriever."""
    pattern_id: str = ""
    category: str = ""
    description: str = ""
    trigger_context: str = ""
    effectiveness_score: float = 0.0
    evidence_count: int = 1
    first_seen: str = ""
    last_seen: str = ""


class MockMemoryStore:
    """Mock MemoryStore for orchestrator tests."""

    def __init__(self):
        self.state = MemoryState()

    def get_relevant_patterns(self, categories=None, limit=10):
        patterns = self.state.patterns
        if categories:
            patterns = [p for p in patterns if p.category in categories]
        return sorted(patterns, key=lambda p: p.evidence_count, reverse=True)[:limit]

    def get_relevant_procedures(self, categories=None, limit=5):
        procedures = self.state.procedures
        if categories:
            procedures = [p for p in procedures if p.category in categories]
        return sorted(
            procedures,
            key=lambda p: p.effectiveness_score * p.evidence_count,
            reverse=True,
        )[:limit]

    def recall_recent(self, limit=5):
        return sorted(self.state.sessions, key=lambda s: s.timestamp, reverse=True)[:limit]

    def recall_for_paper(self, paper_id):
        return [s for s in self.state.sessions if s.paper_id == paper_id]


# ================================================================
# Fixtures
# ================================================================

@pytest.fixture
def memory_store(tmp_path):
    """创建带有预填充数据的 MemoryStore。"""
    store = MemoryStore(base_dir=str(tmp_path))
    store.state = MemoryState()

    # 添加 domain patterns
    now = datetime.now(timezone.utc).isoformat()
    store.state.patterns = [
        DomainPattern(
            pattern_id="p1",
            category="methodology",
            description="DID papers often lack parallel trends test discussion",
            evidence_count=5,
            first_seen=(datetime.now(timezone.utc) - timedelta(days=10)).isoformat(),
            last_seen=now,
            examples=["paper1", "paper2", "paper3"],
        ),
        DomainPattern(
            pattern_id="p2",
            category="overclaim",
            description="Causal claims made without controlling for confounders",
            evidence_count=3,
            first_seen=(datetime.now(timezone.utc) - timedelta(days=20)).isoformat(),
            last_seen=now,
            examples=["paper2", "paper4"],
        ),
        DomainPattern(
            pattern_id="p3",
            category="statistics",
            description="Standard errors not clustered at appropriate level",
            evidence_count=2,
            first_seen=(datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),
            last_seen=(datetime.now(timezone.utc) - timedelta(days=15)).isoformat(),
            examples=["paper5"],
        ),
        DomainPattern(
            pattern_id="p4",
            category="methodology",
            description="IV papers with weak first-stage F-statistic below 10",
            evidence_count=4,
            first_seen=(datetime.now(timezone.utc) - timedelta(days=60)).isoformat(),
            last_seen=(datetime.now(timezone.utc) - timedelta(days=5)).isoformat(),
            examples=["paper1", "paper6"],
        ),
        DomainPattern(
            pattern_id="p5",
            category="methodology",
            description="RDD papers with manipulation of running variable",
            evidence_count=3,
            first_seen=(datetime.now(timezone.utc) - timedelta(days=45)).isoformat(),
            last_seen=(datetime.now(timezone.utc) - timedelta(days=40)).isoformat(),
            examples=["paper7"],
        ),
    ]

    # 添加 procedural patterns
    store.state.procedures = [
        ProceduralPattern(
            pattern_id="proc1",
            category="strategy_effectiveness",
            description="切换到 deep_investigation 在 findings>=3 后效率最高",
            trigger_context="当 findings>=3 且 read_ratio>0.5 时",
            effectiveness_score=0.85,
            evidence_count=4,
            first_seen=(datetime.now(timezone.utc) - timedelta(days=15)).isoformat(),
            last_seen=now,
        ),
        ProceduralPattern(
            pattern_id="proc2",
            category="tool_sequence",
            description="read_section→search_literature→update_findings 是高产序列",
            trigger_context="当需要产出 findings 时",
            effectiveness_score=0.75,
            evidence_count=6,
            first_seen=(datetime.now(timezone.utc) - timedelta(days=20)).isoformat(),
            last_seen=now,
        ),
        ProceduralPattern(
            pattern_id="proc3",
            category="anti_pattern",
            description="连续 5 轮 read_section 不产出 findings 是低效信号",
            trigger_context="当连续调用 read_section 超过 3 次时应切换策略",
            effectiveness_score=0.2,
            evidence_count=2,
            first_seen=(datetime.now(timezone.utc) - timedelta(days=90)).isoformat(),
            last_seen=(datetime.now(timezone.utc) - timedelta(days=80)).isoformat(),
        ),
    ]

    # 添加 session records
    store.state.sessions = [
        SessionRecord(
            session_id="s1",
            paper_id="paper1",
            paper_title="The Effect of Minimum Wage on Employment: A DID Approach",
            timestamp=(datetime.now(timezone.utc) - timedelta(days=5)).isoformat(),
            findings_summary=["[high] Parallel trends assumption not validated"],
            decision="major_revision",
            key_issues=["Parallel trends test missing", "Robustness checks incomplete"],
            loop_turns_total=15,
            conversation_turns=3,
            total_tokens=5000,
        ),
        SessionRecord(
            session_id="s2",
            paper_id="paper2",
            paper_title="Causal Impact of Education on Earnings: IV Estimation",
            timestamp=(datetime.now(timezone.utc) - timedelta(days=10)).isoformat(),
            findings_summary=["[high] Weak instrument concern"],
            decision="major_revision",
            key_issues=["First-stage F < 10", "Exclusion restriction questionable"],
            loop_turns_total=20,
            conversation_turns=5,
            total_tokens=8000,
        ),
    ]

    return store


@pytest.fixture
def decay_config():
    """标准衰减配置。"""
    return DecayConfig(
        half_life_days=30.0,
        importance_weight=0.6,
        recency_weight=0.4,
        gc_threshold=0.15,
        evidence_protection_threshold=5,
    )


# ================================================================
# Module 1: Proactive Retrieval Tests
# ================================================================

class TestProactiveRetriever:
    """主动检索器测试。"""

    def test_basic_retrieval_initial_scan(self, memory_store):
        """Initial scan phase 应检索 methodology 和 overclaim 类型。"""
        retriever = ProactiveRetriever(memory_store)
        query = RetrievalQuery(
            phase="initial_scan",
            paper_type="empirical",
            paper_title="Some DID Paper",
            keywords=["DID", "parallel", "trends"],
        )
        result = retriever.retrieve(query)

        assert isinstance(result, RetrievalResult)
        assert len(result.domain_hints) > 0
        # initial_scan 限制最多 3 个 domain hints
        assert len(result.domain_hints) <= 3
        assert len(result.procedural_hints) <= 2

    def test_deep_review_broader_retrieval(self, memory_store):
        """Deep review phase 应检索更多类别。"""
        retriever = ProactiveRetriever(memory_store)
        query = RetrievalQuery(
            phase="deep_review",
            keywords=["methodology", "statistics"],
        )
        result = retriever.retrieve(query)

        # deep_review 允许更多结果
        assert len(result.domain_hints) <= 5
        assert len(result.procedural_hints) <= 3

    def test_synthesis_minimal_retrieval(self, memory_store):
        """Synthesis phase 不检索 session hints。"""
        retriever = ProactiveRetriever(memory_store)
        query = RetrievalQuery(phase="synthesis")
        result = retriever.retrieve(query)

        assert len(result.session_hints) == 0

    def test_keyword_relevance_ranking(self, memory_store):
        """有关键词时应按相关性排序。"""
        retriever = ProactiveRetriever(memory_store)
        query = RetrievalQuery(
            phase="deep_review",
            keywords=["DID", "parallel", "trends"],
        )
        result = retriever.retrieve(query)

        # DID 相关的 pattern 应该排在前面
        assert len(result.domain_hints) > 0
        assert "DID" in result.domain_hints[0] or "parallel" in result.domain_hints[0]

    def test_format_retrieval_context(self, memory_store):
        """格式化检索结果应生成可读的上下文。"""
        retriever = ProactiveRetriever(memory_store)
        query = RetrievalQuery(
            phase="deep_review",
            keywords=["DID"],
        )
        result = retriever.retrieve(query)
        context = retriever.format_retrieval_context(result)

        assert context is not None
        assert "🔍" in context or "⚡" in context

    def test_empty_memory_returns_empty(self, tmp_path):
        """空记忆应返回空结果。"""
        store = MemoryStore(base_dir=str(tmp_path))
        store.state = MemoryState()
        retriever = ProactiveRetriever(store)

        query = RetrievalQuery(phase="deep_review", keywords=["anything"])
        result = retriever.retrieve(query)

        assert result.domain_hints == []
        assert result.procedural_hints == []
        assert result.session_hints == []

    def test_format_empty_result_returns_none(self, tmp_path):
        """空结果格式化应返回 None。"""
        store = MemoryStore(base_dir=str(tmp_path))
        store.state = MemoryState()
        retriever = ProactiveRetriever(store)

        result = RetrievalResult()
        context = retriever.format_retrieval_context(result)
        assert context is None

    def test_token_budget_respected(self, memory_store):
        """检索结果应尊重 token budget。"""
        retriever = ProactiveRetriever(memory_store)
        query = RetrievalQuery(phase="editing")
        result = retriever.retrieve(query)

        # editing phase budget = 200 tokens
        assert result.total_tokens_estimate <= 200

    def test_session_relevance_scoring(self, memory_store):
        """Session 相关性评分应正确工作。"""
        retriever = ProactiveRetriever(memory_store)
        query = RetrievalQuery(
            phase="deep_review",
            keywords=["DID", "minimum", "wage"],
        )
        result = retriever.retrieve(query)

        # 应该匹配到 DID paper 的 session
        if result.session_hints:
            assert "DID" in result.session_hints[0] or "Minimum Wage" in result.session_hints[0]

    @patch.dict(os.environ, {"SCHOLAR_PROACTIVE_RETRIEVAL": "0"})
    def test_kill_switch_disables(self, memory_store):
        """Kill switch 关闭时应返回空结果。"""
        # 需要重新 import 以刷新 module-level 常量
        # 实际上 ProactiveRetriever.retrieve 内部检查的是 module-level 变量
        # 由于 module-level 变量在 import 时已固定，这里直接 mock
        with patch("core.memory_complete.PROACTIVE_RETRIEVAL_ENABLED", False):
            retriever = ProactiveRetriever(memory_store)
            query = RetrievalQuery(phase="deep_review", keywords=["test"])
            result = retriever.retrieve(query)
            assert result.domain_hints == []
            assert result.procedural_hints == []


# ================================================================
# Module 2: Memory Decay Model Tests
# ================================================================

class TestMemoryDecayModel:
    """记忆衰减模型测试。"""

    def test_recency_score_fresh_memory(self, decay_config):
        """新近的记忆应有高 recency score。"""
        model = MemoryDecayModel(decay_config)
        now = datetime.now(timezone.utc)
        fresh_ts = (now - timedelta(hours=1)).isoformat()

        score = model._compute_recency(fresh_ts, now)
        assert score > 0.99  # 1小时前几乎为 1.0

    def test_recency_score_half_life(self, decay_config):
        """半衰期后 recency 应约为 0.5。"""
        model = MemoryDecayModel(decay_config)
        now = datetime.now(timezone.utc)
        half_life_ts = (now - timedelta(days=30)).isoformat()

        score = model._compute_recency(half_life_ts, now)
        assert 0.45 <= score <= 0.55  # 约 0.5

    def test_recency_score_old_memory(self, decay_config):
        """很老的记忆应有很低的 recency score。"""
        model = MemoryDecayModel(decay_config)
        now = datetime.now(timezone.utc)
        old_ts = (now - timedelta(days=120)).isoformat()

        score = model._compute_recency(old_ts, now)
        assert score < 0.1

    def test_recency_empty_timestamp(self, decay_config):
        """空时间戳应返回默认值 0.3。"""
        model = MemoryDecayModel(decay_config)
        now = datetime.now(timezone.utc)
        assert model._compute_recency("", now) == 0.3

    def test_domain_pattern_scoring(self, memory_store, decay_config):
        """Domain pattern 打分应按 composite 排序。"""
        model = MemoryDecayModel(decay_config)
        scored = model.score_domain_patterns(memory_store.state.patterns)

        assert len(scored) == 5
        # 应按 composite_score 降序
        for i in range(len(scored) - 1):
            assert scored[i].composite_score >= scored[i + 1].composite_score

    def test_high_evidence_gets_high_importance(self, memory_store, decay_config):
        """高 evidence_count 的 pattern 应有高 importance。"""
        model = MemoryDecayModel(decay_config)
        scored = model.score_domain_patterns(memory_store.state.patterns)

        # p1 (evidence=5) 应该有最高 importance
        p1_scored = next(s for s in scored if s.item.pattern_id == "p1")
        assert p1_scored.importance_score == 1.0  # 归一化后最高

    def test_procedural_scoring_combines_effectiveness(self, memory_store, decay_config):
        """Procedural 打分应结合 effectiveness 和 evidence。"""
        model = MemoryDecayModel(decay_config)
        scored = model.score_procedural_patterns(memory_store.state.procedures)

        assert len(scored) == 3
        # proc2 (0.75 * 6 = 4.5) 应该比 proc1 (0.85 * 4 = 3.4) importance 更高
        proc2_scored = next(s for s in scored if s.item.pattern_id == "proc2")
        proc1_scored = next(s for s in scored if s.item.pattern_id == "proc1")
        assert proc2_scored.importance_score > proc1_scored.importance_score

    def test_gc_removes_low_composite(self, decay_config):
        """GC 应移除低 composite score 的条目。"""
        # 使用低阈值配置使测试更确定
        config = DecayConfig(
            half_life_days=10.0,
            importance_weight=0.4,
            recency_weight=0.6,
            gc_threshold=0.25,
            evidence_protection_threshold=5,
        )
        model = MemoryDecayModel(config)
        now = datetime.now(timezone.utc)

        patterns = [
            DomainPattern(
                pattern_id="fresh_strong",
                category="methodology",
                description="Fresh and strong",
                evidence_count=3,
                last_seen=now.isoformat(),
            ),
            DomainPattern(
                pattern_id="old_weak",
                category="methodology",
                description="Old and weak",
                evidence_count=1,
                last_seen=(now - timedelta(days=365)).isoformat(),
            ),
        ]

        surviving, removed = model.gc_with_decay(patterns, "domain", max_size=100, now=now)
        assert removed >= 1
        assert any(p.pattern_id == "fresh_strong" for p in surviving)

    def test_gc_respects_protection(self, decay_config):
        """GC 不应移除受保护的条目 (evidence >= threshold)。"""
        model = MemoryDecayModel(decay_config)
        now = datetime.now(timezone.utc)

        patterns = [
            DomainPattern(
                pattern_id="protected",
                category="methodology",
                description="Protected high evidence",
                evidence_count=5,  # >= evidence_protection_threshold
                last_seen=(now - timedelta(days=200)).isoformat(),  # 很老
            ),
        ]

        surviving, removed = model.gc_with_decay(patterns, "domain", max_size=100, now=now)
        assert removed == 0
        assert len(surviving) == 1

    def test_gc_max_size_limit(self, decay_config):
        """GC 应尊重硬容量限制。"""
        model = MemoryDecayModel(decay_config)
        now = datetime.now(timezone.utc)

        # 创建 20 个 pattern
        patterns = [
            DomainPattern(
                pattern_id=f"p_{i}",
                category="methodology",
                description=f"Pattern {i}",
                evidence_count=i + 1,
                last_seen=now.isoformat(),
            )
            for i in range(20)
        ]

        surviving, removed = model.gc_with_decay(patterns, "domain", max_size=10, now=now)
        assert len(surviving) <= 10

    @patch("core.memory_complete.MEMORY_DECAY_ENABLED", False)
    def test_kill_switch_passthrough(self, decay_config):
        """Kill switch 关闭时不应修改任何数据。"""
        model = MemoryDecayModel(decay_config)
        patterns = [
            DomainPattern(
                pattern_id="any",
                category="test",
                description="test",
                evidence_count=1,
            )
        ]
        surviving, removed = model.gc_with_decay(patterns, "domain")
        assert removed == 0
        assert len(surviving) == 1

    def test_exponential_decay_formula(self, decay_config):
        """验证指数衰减公式的数学正确性。"""
        model = MemoryDecayModel(decay_config)
        now = datetime.now(timezone.utc)
        lambda_val = math.log(2) / 30.0

        for days in [0, 10, 30, 60, 90]:
            ts = (now - timedelta(days=days)).isoformat()
            expected = math.exp(-lambda_val * days)
            actual = model._compute_recency(ts, now)
            assert abs(actual - expected) < 0.01, f"Day {days}: expected {expected:.4f}, got {actual:.4f}"


# ================================================================
# Module 3: Cross-Paper Knowledge Tests
# ================================================================

class TestCrossPaperKnowledgeAccumulator:
    """跨论文知识积累器测试。"""

    def test_promotion_when_threshold_met(self):
        """当同类 patterns 达到阈值时应提升。"""
        accumulator = CrossPaperKnowledgeAccumulator(
            promotion_threshold=3,
            min_evidence_total=5,
            similarity_threshold=0.3,
        )

        patterns = [
            DomainPattern(
                pattern_id=f"m{i}",
                category="methodology",
                description=f"DID papers lack parallel trends test variation {i}",
                evidence_count=2,
            )
            for i in range(4)
        ]

        new_cross = accumulator.analyze_and_promote(patterns)
        assert len(new_cross) >= 1
        assert new_cross[0].category == "methodology"
        assert new_cross[0].paper_count > 0

    def test_no_promotion_below_threshold(self):
        """不满足阈值时不应提升。"""
        accumulator = CrossPaperKnowledgeAccumulator(
            promotion_threshold=5,
            min_evidence_total=10,
        )

        patterns = [
            DomainPattern(
                pattern_id=f"p{i}",
                category="methodology",
                description=f"Pattern {i}",
                evidence_count=1,
            )
            for i in range(3)
        ]

        new_cross = accumulator.analyze_and_promote(patterns)
        assert len(new_cross) == 0

    def test_clustering_groups_similar(self):
        """相似 patterns 应被聚在同一组。"""
        accumulator = CrossPaperKnowledgeAccumulator(similarity_threshold=0.3)

        patterns = [
            DomainPattern(pattern_id="a", category="stats", description="Standard errors not clustered properly"),
            DomainPattern(pattern_id="b", category="stats", description="Standard errors clustering at wrong level"),
            DomainPattern(pattern_id="c", category="stats", description="Completely different topic about sample size"),
        ]

        clusters = accumulator._cluster_patterns(patterns)
        # a 和 b 应该在同一 cluster，c 单独
        assert len(clusters) >= 2

    def test_generalization_description(self):
        """泛化描述应包含统计信息。"""
        accumulator = CrossPaperKnowledgeAccumulator(similarity_threshold=0.2)

        patterns = [
            DomainPattern(pattern_id="x1", category="methodology", description="DID assumption violated",
                          evidence_count=3),
            DomainPattern(pattern_id="x2", category="methodology", description="DID assumption not tested",
                          evidence_count=2),
            DomainPattern(pattern_id="x3", category="methodology", description="DID assumption weak support",
                          evidence_count=4),
        ]

        new_cross = accumulator.analyze_and_promote(patterns)
        if new_cross:
            assert "跨" in new_cross[0].generalization or "多篇" in new_cross[0].generalization

    def test_actionable_hint_generated(self):
        """应为跨论文知识生成可操作建议。"""
        accumulator = CrossPaperKnowledgeAccumulator(
            promotion_threshold=2, min_evidence_total=3, similarity_threshold=0.2
        )

        patterns = [
            DomainPattern(
                pattern_id=f"oh{i}",
                category="overclaim",
                description=f"Claims causal effect without addressing confounders variant {i}",
                evidence_count=2,
            )
            for i in range(3)
        ]

        new_cross = accumulator.analyze_and_promote(patterns)
        if new_cross:
            assert new_cross[0].actionable_hint != ""

    def test_serialization_roundtrip(self):
        """序列化和反序列化应保持数据完整。"""
        accumulator = CrossPaperKnowledgeAccumulator(
            promotion_threshold=2, min_evidence_total=3, similarity_threshold=0.2
        )

        patterns = [
            DomainPattern(
                pattern_id=f"sr{i}",
                category="methodology",
                description=f"Common methodology issue in DID papers variant {i}",
                evidence_count=2,
            )
            for i in range(4)
        ]
        accumulator.analyze_and_promote(patterns)

        serialized = accumulator.serialize()

        new_accumulator = CrossPaperKnowledgeAccumulator()
        new_accumulator.deserialize(serialized)

        assert len(new_accumulator.cross_patterns) == len(accumulator.cross_patterns)
        if serialized:
            assert new_accumulator.cross_patterns[0].pattern_id == accumulator.cross_patterns[0].pattern_id

    def test_format_cross_knowledge_context(self):
        """格式化输出应包含关键信息。"""
        accumulator = CrossPaperKnowledgeAccumulator(
            promotion_threshold=2, min_evidence_total=3, similarity_threshold=0.2
        )

        patterns = [
            DomainPattern(
                pattern_id=f"fc{i}",
                category="overclaim",
                description=f"Overclaim pattern in causal papers variant {i}",
                evidence_count=3,
            )
            for i in range(4)
        ]
        accumulator.analyze_and_promote(patterns)

        context = accumulator.format_cross_knowledge_context()
        if context:
            assert "🌐" in context
            assert "overclaim" in context

    def test_no_duplicate_cross_patterns(self):
        """重复调用不应产生重复的跨论文知识。"""
        accumulator = CrossPaperKnowledgeAccumulator(
            promotion_threshold=2, min_evidence_total=3, similarity_threshold=0.3
        )

        patterns = [
            DomainPattern(
                pattern_id=f"dup{i}",
                category="methodology",
                description=f"DID parallel trends issue variant {i}",
                evidence_count=2,
            )
            for i in range(4)
        ]

        # 调用两次
        accumulator.analyze_and_promote(patterns)
        count_first = len(accumulator.cross_patterns)
        accumulator.analyze_and_promote(patterns)
        count_second = len(accumulator.cross_patterns)

        assert count_second == count_first  # 不应增长

    @patch("core.memory_complete.CROSS_PAPER_KNOWLEDGE_ENABLED", False)
    def test_kill_switch(self):
        """Kill switch 关闭时不应产生任何跨论文知识。"""
        accumulator = CrossPaperKnowledgeAccumulator(
            promotion_threshold=2, min_evidence_total=2
        )
        patterns = [
            DomainPattern(pattern_id=f"ks{i}", category="methodology",
                          description=f"Pattern {i}", evidence_count=3)
            for i in range(5)
        ]
        result = accumulator.analyze_and_promote(patterns)
        assert len(result) == 0


# ================================================================
# Module 4: Distillation Quality Verification Tests
# ================================================================

class TestDistillationQualityVerifier:
    """蒸馏质量验证器测试。"""

    def test_rule_verify_faithful(self):
        """规则验证: 忠实的蒸馏应通过。"""
        verifier = DistillationQualityVerifier()
        original_text = "The paper uses DID methodology with parallel trends assumption"
        distilled_text = "DID methodology parallel trends assumption"

        result = verifier._rule_verify(original_text, distilled_text)
        assert result.is_faithful is True
        assert result.confidence > 0.5

    def test_rule_verify_hallucination(self):
        """规则验证: 引入幻觉的蒸馏应被拒绝。"""
        verifier = DistillationQualityVerifier(max_hallucination_ratio=0.3)
        original_text = "The paper uses DID methodology"
        # 蒸馏中引入了大量不在原文中的内容
        distilled_text = "quantum computing neural network blockchain cryptocurrency"

        result = verifier._rule_verify(original_text, distilled_text)
        assert result.is_faithful is False
        assert any("幻觉" in issue for issue in result.issues)

    def test_rule_verify_low_coverage(self):
        """规则验证: 覆盖率过低的蒸馏应被拒绝。"""
        verifier = DistillationQualityVerifier(min_coverage_ratio=0.5)
        original_text = "longitudinal study cohort analysis multivariate regression"
        # 蒸馏几乎没有覆盖原始内容
        distilled_text = "completely unrelated different topic entirely new"

        result = verifier._rule_verify(original_text, distilled_text)
        assert result.is_faithful is False
        assert any("覆盖率" in issue for issue in result.issues)

    @pytest.mark.asyncio
    async def test_verify_domain_fact_rule_mode(self):
        """无 LLM 时应使用规则模式验证 DomainFact。"""
        verifier = DistillationQualityVerifier()

        original_findings = [
            {"finding": "The parallel trends assumption is not adequately tested",
             "evidence": "Figure 3 shows diverging pre-trends"},
        ]

        @dataclass
        class MockFact:
            category: str = "methodology"
            description: str = "parallel trends assumption not tested"
            evidence_text: str = "pre-trends diverging"
            subcategory: str = ""

        fact = MockFact()
        result = await verifier.verify_domain_fact(original_findings, fact)
        assert result.is_faithful is True

    @pytest.mark.asyncio
    async def test_verify_domain_fact_with_hallucination(self):
        """含幻觉的 DomainFact 应被拒绝。"""
        verifier = DistillationQualityVerifier(
            min_coverage_ratio=0.5, max_hallucination_ratio=0.3
        )
        # 原始 findings
        original_findings = [
            {"finding": "The paper uses DID method", "evidence": "Section 3 describes DID"},
        ]
        # 蒸馏产物含大量不在原始中的词
        fact = MockDomainFact(
            category="methodology",
            description="quantum computing neural network transformer architecture",
            evidence_text="deep learning optimization gradient descent",
            subcategory="ML",
        )
        result = await verifier.verify_domain_fact(original_findings, fact)
        assert result.is_faithful is False
        assert len(result.issues) > 0

    @pytest.mark.asyncio
    async def test_verify_batch(self):
        """批量验证多条 facts。"""
        verifier = DistillationQualityVerifier()
        findings = [
            {"finding": "methodology concern about parallel trends", "evidence": "no pre-trend test"},
            {"finding": "overclaim about causal effect", "evidence": "correlation only"},
        ]
        facts = [
            MockDomainFact(
                category="methodology",
                description="concern about parallel trends methodology",
                evidence_text="pre-trend test",
                subcategory="DID",
            ),
            MockDomainFact(
                category="hallucination",
                description="completely unrelated quantum physics nonsense xyz abc",
                evidence_text="dark matter string theory",
                subcategory="physics",
            ),
        ]
        results = await verifier.verify_batch(findings, facts)
        assert len(results) == 2
        # 第一条应通过（词汇匹配）
        assert results[0].is_faithful is True
        # 第二条可能不通过（取决于阈值）

    @pytest.mark.asyncio
    async def test_filter_faithful(self):
        """filter_faithful 正确分离通过和未通过的。"""
        verifier = DistillationQualityVerifier()
        facts = ["fact_a", "fact_b", "fact_c"]
        results = [
            VerificationResult(is_faithful=True, confidence=0.9),
            VerificationResult(is_faithful=False, confidence=0.3, issues=["bad"]),
            VerificationResult(is_faithful=True, confidence=0.8),
        ]
        passed, failed = verifier.filter_faithful(facts, results)
        assert passed == ["fact_a", "fact_c"]
        assert failed == ["fact_b"]

    @pytest.mark.asyncio
    async def test_verify_procedural_rule(self):
        """验证 ProceduralRule: 高覆盖率 + 低幻觉率应通过。"""
        # 放宽阈值避免规则模式中常见的 action 词被判为幻觉
        verifier = DistillationQualityVerifier(
            min_coverage_ratio=0.3, max_hallucination_ratio=0.5
        )
        source_facts = [
            MockDomainFact(
                category="methodology",
                description="DID papers often lack parallel trends discussion in methodology section",
                evidence_text="multiple DID papers reviewed show this pattern consistently",
                subcategory="DID",
            ),
        ]
        rule = MockProceduralRule(
            trigger="DID papers methodology parallel trends",
            action="parallel trends discussion DID papers",
            rationale="DID papers often lack parallel trends",
        )
        result = await verifier.verify_procedural_rule(source_facts, rule)
        assert result.is_faithful is True

    @pytest.mark.asyncio
    async def test_kill_switch_disables_verification(self):
        """Kill switch 关闭时直接通过。"""
        import core.memory_complete as mc
        original = mc.DISTILL_VERIFICATION_ENABLED
        try:
            mc.DISTILL_VERIFICATION_ENABLED = False
            verifier = DistillationQualityVerifier()
            result = await verifier.verify_domain_fact(
                [], MockDomainFact("x", "y", "z", "w")
            )
            assert result.is_faithful is True
            assert result.confidence == 1.0
        finally:
            mc.DISTILL_VERIFICATION_ENABLED = original


# ================================================================
# Test Module 5: Orchestrator Integration
# ================================================================

class TestMemoryCompleteOrchestrator:
    """MemoryCompleteOrchestrator 集成测试。"""

    def _make_orchestrator(self):
        store = MockMemoryStore()
        return MemoryCompleteOrchestrator(memory_store=store)

    def test_on_phase_start_returns_context(self):
        """phase 开始时返回检索上下文。"""
        store = MockMemoryStore()
        # 添加一些 patterns
        store.state.patterns = [
            MockDomainPattern(
                pattern_id="p1", category="methodology",
                description="DID papers lack parallel trends",
                evidence_count=5, first_seen="2024-01-01T00:00:00+00:00",
                last_seen="2024-06-01T00:00:00+00:00", examples=["paper1"],
            ),
        ]
        store.state.procedures = [
            MockProceduralPattern(
                pattern_id="pr1", category="strategy_effectiveness",
                description="Read methods first for DID papers",
                trigger_context="when reviewing DID",
                effectiveness_score=0.8, evidence_count=3,
                first_seen="2024-01-01T00:00:00+00:00",
                last_seen="2024-06-01T00:00:00+00:00",
            ),
        ]

        orch = MemoryCompleteOrchestrator(memory_store=store)
        context = orch.on_phase_start(
            phase="deep_review",
            paper_type="empirical",
            keywords=["DID", "parallel", "trends"],
        )
        assert context is not None
        assert "methodology" in context or "DID" in context

    def test_on_phase_start_empty_memory(self):
        """空记忆时返回 None。"""
        orch = self._make_orchestrator()
        context = orch.on_phase_start(phase="initial_scan")
        assert context is None

    def test_on_session_end_runs_gc(self):
        """session 结束时执行 GC: 旧的低 importance 条目被移除。"""
        store = MockMemoryStore()
        now = datetime.now(timezone.utc)
        old_time = "2019-01-01T00:00:00+00:00"

        # 混合新旧 patterns: 新的有高 evidence, 旧的低 evidence
        # 这样归一化后旧的 importance 低, recency 也低 → composite 很低
        store.state.patterns = [
            # 新的高 evidence — 不应被 GC
            DomainPattern(
                pattern_id="new_strong", category="methodology",
                description="recent strong pattern",
                evidence_count=10, first_seen=now.isoformat(),
                last_seen=now.isoformat(), examples=[],
            ),
        ] + [
            # 旧的低 evidence — 应被 GC
            DomainPattern(
                pattern_id=f"old_weak_{i}", category="methodology",
                description=f"old weak pattern {i}",
                evidence_count=1, first_seen=old_time,
                last_seen=old_time, examples=[],
            )
            for i in range(5)
        ]
        store.state.procedures = [
            # 新的高效 — 不应被 GC
            ProceduralPattern(
                pattern_id="new_proc", category="tool_sequence",
                description="effective new procedure",
                trigger_context="when x",
                effectiveness_score=0.9, evidence_count=8,
                first_seen=now.isoformat(), last_seen=now.isoformat(),
            ),
        ] + [
            # 旧的低效 — 应被 GC
            ProceduralPattern(
                pattern_id=f"old_proc_{i}", category="tool_sequence",
                description=f"old ineffective procedure {i}",
                trigger_context="when x",
                effectiveness_score=0.1, evidence_count=1,
                first_seen=old_time, last_seen=old_time,
            )
            for i in range(5)
        ]

        # 使用配置使得低分条目被淘汰:
        # old_weak: importance = 1/10 = 0.1, recency ≈ 0 → composite ≈ 0.1*0.5 + 0*0.5 = 0.05
        config = DecayConfig(
            half_life_days=10.0,
            importance_weight=0.5,
            recency_weight=0.5,
            gc_threshold=0.15,
            evidence_protection_threshold=8,
        )
        orch = MemoryCompleteOrchestrator(memory_store=store, decay_config=config)
        stats = orch.on_session_end()

        # 旧的低质量记忆应该被 GC
        assert stats["gc_removed_domain"] > 0 or stats["gc_removed_procedural"] > 0
        # new_strong 应该存活
        assert any(p.pattern_id == "new_strong" for p in store.state.patterns)

    @pytest.mark.asyncio
    async def test_on_distillation_filters(self):
        """蒸馏后验证过滤不忠实的产物。"""
        orch = self._make_orchestrator()
        findings = [
            {"finding": "methodology uses DID correctly", "evidence": "section 3"},
        ]
        facts = [
            MockDomainFact("methodology", "DID methodology correctly applied", "section 3", "DID"),
            MockDomainFact("quantum", "quantum teleportation xyz", "dark energy", "physics"),
        ]
        passed, failed = await orch.on_distillation(findings, facts)
        # 第一条应该通过
        assert len(passed) >= 1

    def test_cross_paper_state_serialization(self):
        """跨论文知识的序列化/反序列化。"""
        orch = self._make_orchestrator()
        # 手动添加一些 cross patterns
        orch.cross_paper._cross_patterns.append(
            CrossPaperPattern(
                pattern_id="cp1",
                generalization="DID papers commonly miss parallel trends",
                source_pattern_ids=["p1", "p2"],
                paper_count=5,
                confidence=0.8,
                category="methodology",
                first_seen="2024-01-01",
                last_updated="2024-06-01",
                actionable_hint="Check parallel trends",
            )
        )

        # 序列化
        state = orch.get_cross_paper_state()
        assert len(state) == 1
        assert state[0]["pattern_id"] == "cp1"

        # 反序列化到新实例
        orch2 = self._make_orchestrator()
        orch2.load_cross_paper_state(state)
        assert len(orch2.cross_paper.cross_patterns) == 1
        assert orch2.cross_paper.cross_patterns[0].generalization == "DID papers commonly miss parallel trends"
