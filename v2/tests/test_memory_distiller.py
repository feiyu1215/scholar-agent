"""
tests/test_memory_distiller.py — Phase 2 Memory Distiller 单元测试
"""

import unittest
import asyncio

from core.memory_distiller import (
    MemoryDistiller,
    DomainFact,
    ProceduralRule,
    TokenEstimator,
)


class TestTokenEstimator(unittest.TestCase):
    """测试 Token 估算器"""

    def setUp(self):
        self.estimator = TokenEstimator()

    def test_empty_string(self):
        """空字符串返回 0"""
        self.assertEqual(self.estimator.estimate(""), 0)

    def test_english_text(self):
        """英文文本估算应合理"""
        text = "The quick brown fox jumps over the lazy dog"
        tokens = self.estimator.estimate(text)
        # 9 个单词 × ~1.3 + 3 ≈ 15
        self.assertGreater(tokens, 5)
        self.assertLess(tokens, 30)

    def test_chinese_text(self):
        """中文文本估算应合理"""
        text = "这是一段中文测试文本用于验证估算器的准确性"
        tokens = self.estimator.estimate(text)
        # CJK 字符 × ~1.5 + 3
        self.assertGreater(tokens, 10)
        self.assertLess(tokens, 60)

    def test_cache_works(self):
        """缓存命中时应返回相同结果"""
        text = "cached text for testing"
        t1 = self.estimator.estimate(text)
        t2 = self.estimator.estimate(text)
        self.assertEqual(t1, t2)

    def test_estimate_messages(self):
        """消息列表估算"""
        messages = [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "Hello world"},
        ]
        total = self.estimator.estimate_messages(messages)
        self.assertGreater(total, 5)

    def test_estimate_messages_multimodal(self):
        """多模态消息列表估算"""
        messages = [
            {"role": "user", "content": [{"type": "text", "text": "Describe this image"}]},
        ]
        total = self.estimator.estimate_messages(messages)
        self.assertGreater(total, 3)

    def test_clear_cache(self):
        """clear_cache 应清空缓存"""
        text = "test"
        self.estimator.estimate(text)
        self.estimator.clear_cache()
        self.assertEqual(len(self.estimator._cache), 0)

    def test_mixed_content(self):
        """中英混合文本"""
        text = "这是一段 mixed content 用于测试 token 估算"
        tokens = self.estimator.estimate(text)
        self.assertGreater(tokens, 10)


class TestDistillSessionToDomain(unittest.TestCase):
    """测试 L1→L2 蒸馏（规则模式，无 LLM）"""

    def setUp(self):
        self.distiller = MemoryDistiller(llm=None)

    def test_empty_findings(self):
        """空 findings 返回空列表"""
        result = asyncio.run(self.distiller.distill_session_to_domain([], "test_session"))
        self.assertEqual(result, [])

    def test_extract_from_verified_findings(self):
        """应从 verified findings 中提取事实"""
        findings = [
            {
                "category": "methodology",
                "finding": "DID 论文缺少 parallel trends 检验",
                "evidence": "论文 Section 3 未报告处理前趋势",
                "status": "verified",
                "priority": "high",
            },
            {
                "category": "statistics",
                "finding": "样本量仅 50 人，统计检验力不足",
                "evidence": "Table 1 报告 N=50",
                "status": "verified",
                "priority": "high",
            },
        ]

        facts = asyncio.run(self.distiller.distill_session_to_domain(findings, "session_1"))

        self.assertEqual(len(facts), 2)
        self.assertIsInstance(facts[0], DomainFact)
        self.assertEqual(facts[0].category, "methodology")
        self.assertEqual(facts[0].source_session_id, "session_1")
        self.assertEqual(facts[0].confidence, 0.7)  # verified → 0.7

    def test_needs_verification_lower_confidence(self):
        """needs_verification 状态应有较低 confidence"""
        findings = [
            {
                "category": "logic",
                "finding": "因果推断链条可能有缺口",
                "status": "needs_verification",
                "priority": "medium",
            },
        ]
        facts = asyncio.run(self.distiller.distill_session_to_domain(findings, "s1"))
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0].confidence, 0.5)

    def test_skip_non_qualified_findings(self):
        """非 verified/needs_verification 且非 high priority 的应被跳过"""
        findings = [
            {
                "category": "writing",
                "finding": "语法有小错误",
                "status": "tentative",
                "priority": "low",
            },
        ]

        facts = asyncio.run(self.distiller.distill_session_to_domain(findings, "s1"))
        self.assertEqual(len(facts), 0)

    def test_high_priority_always_extracted(self):
        """high priority 无论 status 都应被提取"""
        findings = [
            {
                "category": "methodology",
                "finding": "严重的方法论缺陷",
                "status": "tentative",
                "priority": "high",
            },
        ]
        facts = asyncio.run(self.distiller.distill_session_to_domain(findings, "s1"))
        self.assertEqual(len(facts), 1)

    def test_max_facts_per_session(self):
        """应遵守 max_facts_per_session 限制"""
        distiller = MemoryDistiller(llm=None, max_facts_per_session=3)
        findings = [
            {
                "category": f"cat_{i}",
                "finding": f"finding_{i} with enough description",
                "status": "verified",
                "priority": "high",
            }
            for i in range(10)
        ]

        facts = asyncio.run(distiller.distill_session_to_domain(findings, "s1"))
        self.assertLessEqual(len(facts), 3)

    def test_empty_finding_text_skipped(self):
        """finding 描述为空时应跳过"""
        findings = [
            {
                "category": "methodology",
                "finding": "",
                "status": "verified",
                "priority": "high",
            },
        ]
        facts = asyncio.run(self.distiller.distill_session_to_domain(findings, "s1"))
        self.assertEqual(len(facts), 0)


class TestDistillDomainToProcedural(unittest.TestCase):
    """测试 L2→L3 规则归纳（规则模式）"""

    def setUp(self):
        self.distiller = MemoryDistiller(llm=None)

    def test_below_threshold_no_rules(self):
        """performance_signal 低于阈值时不产出规则"""
        facts = [DomainFact(category="methodology", description="test")] * 5
        rules = asyncio.run(
            self.distiller.distill_domain_to_procedural(facts, performance_signal=0.3)
        )
        self.assertEqual(rules, [])

    def test_empty_facts_no_rules(self):
        """空 facts 返回空列表"""
        rules = asyncio.run(
            self.distiller.distill_domain_to_procedural([], performance_signal=0.8)
        )
        self.assertEqual(rules, [])

    def test_rules_induced_from_frequency(self):
        """同一类别 3+ 次应归纳出规则"""
        facts = [
            DomainFact(
                category="methodology",
                description=f"方法论问题 {i}",
                source_session_id=f"session_{i}",
            )
            for i in range(5)
        ]

        rules = asyncio.run(
            self.distiller.distill_domain_to_procedural(facts, performance_signal=0.8)
        )

        self.assertGreater(len(rules), 0)
        self.assertIsInstance(rules[0], ProceduralRule)
        self.assertIn("methodology", rules[0].trigger)
        self.assertTrue(rules[0].rationale)

    def test_below_frequency_threshold_no_rules(self):
        """同类别不到 3 次不应归纳"""
        facts = [
            DomainFact(category="methodology", description="issue 1"),
            DomainFact(category="statistics", description="issue 2"),
        ]
        rules = asyncio.run(
            self.distiller.distill_domain_to_procedural(facts, performance_signal=0.8)
        )
        self.assertEqual(len(rules), 0)

    def test_no_duplicate_rules(self):
        """与已有规则 trigger 相同时不应再次产出"""
        facts = [
            DomainFact(category="stats", description=f"统计问题 {i}")
            for i in range(5)
        ]
        existing = [ProceduralRule(
            trigger="审阅论文时遇到 stats 相关内容",
            action="检查统计假设",
        )]

        rules = asyncio.run(
            self.distiller.distill_domain_to_procedural(
                facts, performance_signal=0.8, existing_rules=existing
            )
        )
        # 不应产出与现有 trigger 相同的规则
        triggers = [r.trigger for r in rules]
        self.assertNotIn("审阅论文时遇到 stats 相关内容", triggers)

    def test_max_rules_per_batch(self):
        """应遵守 max_rules_per_batch 限制"""
        distiller = MemoryDistiller(llm=None, max_rules_per_batch=2)
        # 创建多个类别各 5 个 facts
        facts = []
        for cat in ["methodology", "statistics", "logic", "writing", "citation"]:
            for i in range(5):
                facts.append(DomainFact(category=cat, description=f"{cat} issue {i}"))

        rules = asyncio.run(
            distiller.distill_domain_to_procedural(facts, performance_signal=0.9)
        )
        self.assertLessEqual(len(rules), 2)


class TestDeduplication(unittest.TestCase):
    """测试去重与合并"""

    def setUp(self):
        self.distiller = MemoryDistiller(llm=None)

    def test_exact_duplicate_merged(self):
        """完全相同描述的 facts 应被合并"""
        existing = [
            DomainFact(category="methodology", description="DID 缺少平行趋势检验", confidence=0.6)
        ]
        new = [
            DomainFact(category="methodology", description="DID 缺少平行趋势检验", confidence=0.8)
        ]

        result, merged_count = self.distiller.deduplicate_and_merge(new, existing)
        self.assertEqual(merged_count, 1)
        self.assertEqual(len(result), 1)
        # 高 confidence 的描述应被保留
        self.assertEqual(result[0].confidence, 0.8)

    def test_different_category_not_merged(self):
        """不同类别的 facts 不应合并"""
        existing = [DomainFact(category="methodology", description="test problem")]
        new = [DomainFact(category="statistics", description="test problem")]

        result, merged_count = self.distiller.deduplicate_and_merge(new, existing)
        self.assertEqual(merged_count, 0)
        self.assertEqual(len(result), 2)

    def test_truly_new_facts_added(self):
        """全新的 facts 应被添加"""
        existing = [DomainFact(category="writing", description="grammar issues in methods section")]
        new = [DomainFact(category="methodology", description="completely different approach needed")]

        result, merged_count = self.distiller.deduplicate_and_merge(new, existing)
        self.assertEqual(merged_count, 0)
        self.assertEqual(len(result), 2)

    def test_similar_descriptions_merged(self):
        """高相似度的同类别描述应被合并"""
        existing = [DomainFact(
            category="statistics",
            description="sample size is too small for reliable inference",
            confidence=0.6,
        )]
        new = [DomainFact(
            category="statistics",
            description="sample size is too small for reliable statistical inference",
            confidence=0.9,
        )]

        result, merged_count = self.distiller.deduplicate_and_merge(new, existing)
        # jaccard 很高 → 应合并
        self.assertEqual(merged_count, 1)
        self.assertEqual(len(result), 1)

    def test_merge_empty_existing(self):
        """existing 为空时所有 new 都应加入"""
        new = [
            DomainFact(category="x", description="fact 1"),
            DomainFact(category="y", description="fact 2"),
        ]
        result, merged_count = self.distiller.deduplicate_and_merge(new, [])
        self.assertEqual(merged_count, 0)
        self.assertEqual(len(result), 2)

    def test_merge_empty_new(self):
        """new 为空时应返回 existing 不变"""
        existing = [DomainFact(category="x", description="fact 1")]
        result, merged_count = self.distiller.deduplicate_and_merge([], existing)
        self.assertEqual(merged_count, 0)
        self.assertEqual(len(result), 1)


class TestDomainFact(unittest.TestCase):
    """DomainFact 数据类测试"""

    def test_creation_defaults(self):
        fact = DomainFact(category="methodology")
        self.assertEqual(fact.category, "methodology")
        self.assertEqual(fact.subcategory, "")
        self.assertEqual(fact.confidence, 0.8)
        self.assertEqual(fact.source_finding_ids, [])

    def test_creation_full(self):
        fact = DomainFact(
            category="statistics",
            subcategory="regression",
            description="OLS with heteroscedasticity",
            evidence_text="Table 2 shows residual pattern",
            confidence=0.9,
            source_session_id="s1",
            source_finding_ids=[0, 1],
        )
        self.assertEqual(fact.subcategory, "regression")
        self.assertEqual(fact.source_finding_ids, [0, 1])


class TestProceduralRule(unittest.TestCase):
    """ProceduralRule 数据类测试"""

    def test_creation(self):
        rule = ProceduralRule(
            trigger="审阅论文时遇到 DID 方法",
            action="检查 parallel trends assumption",
            rationale="DID 的核心假设容易被忽略",
        )
        self.assertEqual(rule.trigger, "审阅论文时遇到 DID 方法")
        self.assertEqual(rule.effectiveness, 0.0)
        self.assertEqual(rule.counter_evidence, 0)


if __name__ == "__main__":
    unittest.main()
