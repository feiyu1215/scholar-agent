"""
tests/test_v2_cognition_graph.py — K1: 审稿认知图谱 测试

测试内容:
    1. ReviewCognitionGraph 数据结构
    2. build_cognition_graph() 零 LLM 构建
    3. persist_cognitive_hints_as_experience() 持久化到 MemoryStore
    4. 与 harness 的集成（_tool_done 构建 + end_session 持久化）
"""

import sys
import os
import unittest
import unittest.mock
import tempfile
from pathlib import Path
from dataclasses import dataclass, field

# 确保能找到 core 模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.cognition_graph import (
    ReviewCognitionGraph,
    build_cognition_graph,
    persist_cognitive_hints_as_experience,
    _extract_core_claims,
    _extract_evidence_chains,
    _cluster_findings,
    _build_review_strategy,
    _assess_depth,
    _finding_priority_to_strength,
)
from core.paper_type_hints import CognitiveHints
from core.memory import MemoryStore
from core.state import WorkspaceState


# ============================================================
# Mock 对象
# ============================================================

class MockHypothesisModule:
    """模拟 HypothesisModule，用于测试 hypothesis_outcomes 提取。"""

    def __init__(self, hypotheses=None):
        self.hypotheses = hypotheses or []


@dataclass
class MockHypothesis:
    """模拟一个假说。"""
    statement: str = "test hypothesis"
    status: "MockStatus" = None
    evidence: list = field(default_factory=list)

    def __post_init__(self):
        if self.status is None:
            self.status = MockStatus("confirmed")


@dataclass
class MockStatus:
    value: str


@dataclass
class MockEvidence:
    content: str = "evidence content"


# ============================================================
# 测试: ReviewCognitionGraph 数据结构
# ============================================================

class TestReviewCognitionGraph(unittest.TestCase):
    """测试 ReviewCognitionGraph 基本功能。"""

    def test_empty_graph(self):
        """空图谱应正确报告 is_empty。"""
        graph = ReviewCognitionGraph()
        self.assertTrue(graph.is_empty())
        self.assertEqual(graph.format_for_output(), "")

    def test_non_empty_graph(self):
        """有内容的图谱不应为空。"""
        graph = ReviewCognitionGraph(
            total_findings=3,
            core_claims=[{"claim": "X causes Y", "evidence_sections": ["methods"], "assessed_strength": "weak"}],
        )
        self.assertFalse(graph.is_empty())

    def test_format_for_output_basic(self):
        """format_for_output 应返回人可读字符串。"""
        graph = ReviewCognitionGraph(
            paper_type="DID 因果推断论文",
            total_findings=5,
            verified_findings=3,
            core_claims=[
                {"claim": "政策X导致产出Y提升", "evidence_sections": ["methods"], "assessed_strength": "weak"},
            ],
            hypothesis_outcomes=[
                {"statement": "平行趋势假设可能不成立", "outcome": "confirmed", "key_evidence": "图3显示处理前趋势发散"},
            ],
            finding_clusters=[
                {"theme": "methodology", "finding_indices": [0, 1, 2], "cluster_severity": "high"},
            ],
            review_strategy={"lessons": ["实际产出集中在 methodology", "高优发现涉及 methods"]},
            sections_read_ratio=0.75,
            review_depth="standard",
            loop_turns_used=20,
        )
        output = graph.format_for_output()
        self.assertIn("审稿认知图谱", output)
        self.assertIn("DID 因果推断论文", output)
        self.assertIn("核心论点", output)
        self.assertIn("假说追查", output)
        self.assertIn("发现聚类", output)
        self.assertIn("审稿经验", output)
        self.assertIn("75%", output)

    def test_to_dict_roundtrip(self):
        """to_dict 应返回可 JSON 序列化的 dict。"""
        graph = ReviewCognitionGraph(
            paper_type="RCT",
            total_findings=2,
            review_depth="deep",
        )
        d = graph.to_dict()
        self.assertEqual(d["paper_type"], "RCT")
        self.assertEqual(d["total_findings"], 2)
        self.assertEqual(d["review_depth"], "deep")
        self.assertIsInstance(d["core_claims"], list)


# ============================================================
# 测试: build_cognition_graph（核心构建函数）
# ============================================================

class TestBuildCognitionGraph(unittest.TestCase):
    """测试 build_cognition_graph 零 LLM 构建。"""

    def _make_state(self, findings=None, sections_read=None, paper_sections=None, loop_turns=10):
        """创建一个最小化的 WorkspaceState 用于测试。"""
        state = WorkspaceState()
        state.findings = findings or []
        state.sections_read = sections_read or []
        state.paper_sections = paper_sections or {"abstract": "...", "methods": "...", "results": "..."}
        state.loop_turns = loop_turns
        return state

    def test_empty_state_produces_surface_graph(self):
        """空 state 应产出 surface 深度图谱。"""
        state = self._make_state(findings=[], sections_read=[], loop_turns=2)
        graph = build_cognition_graph(state)
        self.assertTrue(graph.is_empty())
        self.assertEqual(graph.review_depth, "surface")

    def test_basic_findings_extraction(self):
        """有 findings 时应正确提取 core_claims 和 clusters。"""
        findings = [
            {"finding": "DID 平行趋势检验缺失", "priority": "high", "section": "methods", "status": "verified", "evidence": "无相关检验代码"},
            {"finding": "样本量偏小影响外部效度", "priority": "medium", "section": "data", "status": "verified", "evidence": "N=50"},
            {"finding": "结论措辞过度", "priority": "high", "section": "conclusion", "status": "needs_verification", "evidence": ""},
        ]
        state = self._make_state(
            findings=findings,
            sections_read=["abstract", "methods", "data"],
            loop_turns=18,
        )
        graph = build_cognition_graph(state)

        # 应有 core_claims（high + medium）
        self.assertGreaterEqual(len(graph.core_claims), 2)
        # 应有 finding_clusters（按 section 分组）
        self.assertGreaterEqual(len(graph.finding_clusters), 2)
        # sections_read_ratio = 3/3 (排除 "full") = 1.0
        self.assertAlmostEqual(graph.sections_read_ratio, 1.0)
        # 3 findings, 2 verified
        self.assertEqual(graph.total_findings, 3)
        self.assertEqual(graph.verified_findings, 2)

    def test_with_hypothesis_module(self):
        """传入 hypothesis_module 时应提取 hypothesis_outcomes。"""
        state = self._make_state(
            findings=[{"finding": "X", "priority": "high", "section": "methods", "status": "verified", "evidence": "Y"}],
            loop_turns=20,
        )
        hyp_module = MockHypothesisModule(hypotheses=[
            MockHypothesis(
                statement="平行趋势假设不成立",
                status=MockStatus("confirmed"),
                evidence=[MockEvidence("图3显示趋势发散")],
            ),
            MockHypothesis(
                statement="外部效度受限",
                status=MockStatus("rejected"),
                evidence=[],
            ),
        ])
        graph = build_cognition_graph(state, hypothesis_module=hyp_module)
        self.assertEqual(len(graph.hypothesis_outcomes), 2)
        self.assertEqual(graph.hypothesis_outcomes[0]["outcome"], "confirmed")
        self.assertEqual(graph.hypothesis_outcomes[1]["outcome"], "rejected")

    def test_with_cognitive_hints(self):
        """传入 cognitive_hints 时应填充 review_strategy。"""
        state = self._make_state(
            findings=[
                {"finding": "F1", "priority": "high", "section": "methods", "status": "verified", "evidence": "E1"},
                {"finding": "F2", "priority": "medium", "section": "methods", "status": "verified", "evidence": "E2"},
            ],
            loop_turns=15,
        )
        hints = CognitiveHints(
            paper_type_description="差分-因-差分 (DID) 论文",
            focus_dimensions=["平行趋势检验", "处理效应异质性", "安慰剂检验"],
            typical_weaknesses=["选择偏差", "事前趋势拟合不足"],
            verification_strategies=["检查 event study 图", "核实样本构建过程"],
        )
        graph = build_cognition_graph(state, cognitive_hints=hints)
        self.assertEqual(graph.paper_type, "差分-因-差分 (DID) 论文")
        self.assertIn("平行趋势检验", graph.review_strategy["focus_dimensions"])
        self.assertEqual(len(graph.review_strategy["effective_approaches"]), 2)

    def test_depth_assessment(self):
        """深度评估应基于阅读覆盖、findings 数、loop 轮次。"""
        self.assertEqual(_assess_depth(0.9, 6, 20), "deep")
        self.assertEqual(_assess_depth(0.6, 4, 10), "standard")
        self.assertEqual(_assess_depth(0.2, 1, 3), "surface")


# ============================================================
# 测试: 内部构建函数
# ============================================================

class TestInternalBuilders(unittest.TestCase):
    """测试零 LLM 内部构建逻辑。"""

    def test_extract_core_claims_filters_low_priority(self):
        """只应提取 high 和 medium priority。"""
        findings = [
            {"finding": "critical issue", "priority": "high", "section": "methods"},
            {"finding": "minor typo", "priority": "low", "section": "writing"},
            {"finding": "moderate concern", "priority": "medium", "section": "results"},
        ]
        claims = _extract_core_claims(findings)
        # 只有 high + medium = 2 条
        self.assertEqual(len(claims), 2)
        self.assertIn("critical issue", claims[0]["claim"])

    def test_extract_evidence_chains_needs_evidence(self):
        """只有有实质证据的 findings 才会产生证据链。"""
        findings = [
            {"finding": "F1", "evidence": "A very detailed piece of evidence that is longer than 20 chars", "status": "verified"},
            {"finding": "F2", "evidence": "", "status": "verified"},  # 无证据
            {"finding": "F3", "evidence": "short", "status": "verified"},  # 证据太短
        ]
        chains = _extract_evidence_chains(findings)
        self.assertEqual(len(chains), 1)
        self.assertEqual(chains[0]["chain_integrity"], "verified")

    def test_cluster_findings_by_section(self):
        """应按 section 分组，且按严重度排序。"""
        findings = [
            {"finding": "F1", "priority": "low", "section": "intro"},
            {"finding": "F2", "priority": "high", "section": "methods"},
            {"finding": "F3", "priority": "high", "section": "methods"},
            {"finding": "F4", "priority": "medium", "section": "results"},
        ]
        clusters = _cluster_findings(findings)
        # 应有 3 个 cluster（intro, methods, results）
        self.assertEqual(len(clusters), 3)
        # methods cluster（high）应排第一
        self.assertEqual(clusters[0]["theme"], "methods")
        self.assertEqual(clusters[0]["cluster_severity"], "high")
        self.assertEqual(len(clusters[0]["finding_indices"]), 2)

    def test_build_review_strategy_empty_hints(self):
        """空 hints 时 strategy 应只有 findings 分布信息。"""
        findings = [
            {"finding": "F1", "priority": "high", "section": "methods"},
            {"finding": "F2", "priority": "low", "section": "methods"},
        ]
        strategy = _build_review_strategy(None, findings)
        self.assertEqual(strategy["paper_type_description"], "")
        self.assertGreater(len(strategy["lessons"]), 0)
        # 应包含 findings 分布
        self.assertTrue(any("methods" in l for l in strategy["lessons"]))

    def test_finding_priority_to_strength(self):
        """priority → strength 映射应正确。"""
        self.assertEqual(_finding_priority_to_strength("high"), "weak")
        self.assertEqual(_finding_priority_to_strength("medium"), "questionable")
        self.assertEqual(_finding_priority_to_strength("low"), "adequate")


# ============================================================
# 测试: persist_cognitive_hints_as_experience
# ============================================================

class TestPersistCognitiveHints(unittest.TestCase):
    """测试认知提示持久化到 MemoryStore。"""

    def setUp(self):
        """创建临时 MemoryStore。"""
        self.tmp_dir = tempfile.mkdtemp()
        self.store = MemoryStore(self.tmp_dir)

    def test_empty_hints_not_persisted(self):
        """空 hints 不应产生任何持久化。"""
        hints = CognitiveHints()  # 全空
        count = persist_cognitive_hints_as_experience(hints, self.store, "paper123", 5)
        self.assertEqual(count, 0)

    def test_none_hints_not_persisted(self):
        """None hints 不应产生任何持久化。"""
        count = persist_cognitive_hints_as_experience(None, self.store, "paper123", 5)
        self.assertEqual(count, 0)

    def test_full_hints_persisted_correctly(self):
        """完整 hints 应正确持久化到不同 Layer。"""
        hints = CognitiveHints(
            paper_type_description="RCT 临床试验论文",
            focus_dimensions=["随机化质量", "盲法执行", "ITT分析"],
            typical_weaknesses=["失访率高", "亚组分析过多"],
            verification_strategies=["检查 CONSORT 流程图", "核实样本量计算"],
        )
        count = persist_cognitive_hints_as_experience(hints, self.store, "paper_rct_001", 7)

        # 3 focus_dimensions + 2 verification_strategies + 2 typical_weaknesses = 7
        self.assertEqual(count, 7)

        # 检查 ProceduralPattern（review_focus + verification_strategy）
        procedures = self.store.state.procedures
        self.assertEqual(len(procedures), 5)  # 3 + 2

        review_focus = [p for p in procedures if p.category == "review_focus"]
        self.assertEqual(len(review_focus), 3)
        self.assertIn("随机化质量", review_focus[0].description)
        # trigger_context 应包含论文类型
        self.assertIn("RCT", review_focus[0].trigger_context)
        # effectiveness 应 = min(7/5, 1.0) = 1.0
        self.assertAlmostEqual(review_focus[0].effectiveness_score, 1.0)

        # 检查 DomainPattern（typical_weakness）
        patterns = self.store.state.patterns
        self.assertEqual(len(patterns), 2)
        self.assertEqual(patterns[0].category, "typical_weakness")
        self.assertIn("paper_rct_001", patterns[0].examples)

    def test_reinforcement_on_repeat(self):
        """重复持久化应强化已有模式（evidence_count +1）。"""
        hints = CognitiveHints(
            paper_type_description="DID论文",
            focus_dimensions=["平行趋势"],
            typical_weaknesses=[],
            verification_strategies=[],
        )
        persist_cognitive_hints_as_experience(hints, self.store, "paper1", 5)
        persist_cognitive_hints_as_experience(hints, self.store, "paper2", 3)

        # review_focus "平行趋势" 应 evidence_count = 2
        procs = self.store.state.procedures
        self.assertEqual(len(procs), 1)
        self.assertEqual(procs[0].evidence_count, 2)

    def test_persistence_to_disk(self):
        """持久化后 save + load 应完整恢复。"""
        hints = CognitiveHints(
            paper_type_description="计量经济学论文",
            focus_dimensions=["工具变量有效性"],
            typical_weaknesses=["弱工具变量"],
            verification_strategies=["检查第一阶段F统计量"],
        )
        persist_cognitive_hints_as_experience(hints, self.store, "paper_iv", 4)
        self.store.save()

        # 重新加载
        new_store = MemoryStore(self.tmp_dir)
        new_store.load()
        self.assertEqual(len(new_store.state.procedures), 2)
        self.assertEqual(len(new_store.state.patterns), 1)


# ============================================================
# 测试: 与 harness 的集成
# ============================================================

class TestHarnessIntegration(unittest.TestCase):
    """测试 K1 在 harness flow 中的集成。"""

    def test_tool_done_builds_cognition_graph(self):
        """_tool_done 成功返回 __DONE__ 时应构建 cognition_graph。"""
        from core.harness import Harness

        # 创建一个 minimal harness（无需真实论文）
        with tempfile.TemporaryDirectory() as tmp:
            paper_path = Path(tmp) / "test_paper.md"
            paper_path.write_text("# Abstract\nThis is a test paper about methods.\n\n# Methods\nWe use DID.\n")

            harness = Harness(paper_path=str(paper_path), max_loop_turns=50)

            # 禁用 checker 的 pre-completion 检查，避免外部依赖（LLM API 不可用时行为不一致）
            harness.checker.check_pre_completion = lambda **kwargs: None

            # 手动添加 findings（模拟审稿过程）
            harness.state.findings = [
                {"finding": "缺少平行趋势检验", "priority": "high", "section": "methods", "status": "verified", "evidence": "全文未出现 parallel trends test"},
                {"finding": "样本期偏短", "priority": "medium", "section": "data", "status": "verified", "evidence": "仅3年数据"},
            ]
            harness.state.sections_read = ["abstract", "methods"]
            harness.state.loop_turns = 15

            # 调用 _tool_done
            result = harness._tool_done({"summary": "审稿完成"})

            # 如果有 nudge（quality gate），循环重试直到通过
            # （不同 quality gate 可能连续触发多次 nudge）
            max_retries = 5
            retries = 0
            while result.startswith("__NUDGE__") and retries < max_retries:
                result = harness._tool_done({"summary": "审稿完成"})
                retries += 1

            # 应返回 __DONE__
            self.assertTrue(
                result.startswith("__DONE__"),
                f"Expected __DONE__ but got: {result[:100]}",
            )

            # cognition_graph 应已构建
            graph = harness.state.cognition_graph
            self.assertIsNotNone(graph)
            self.assertEqual(graph.total_findings, 2)
            self.assertEqual(graph.verified_findings, 2)
            self.assertGreater(len(graph.core_claims), 0)

    def test_end_session_persists_cognitive_hints(self):
        """end_session 应将 cognitive_hints 持久化到 memory。"""
        from core.harness import Harness

        with tempfile.TemporaryDirectory() as tmp:
            paper_path = Path(tmp) / "test_paper.md"
            paper_path.write_text("# Abstract\nA DID study on X.\n\n# Methods\nWe apply DID.\n")

            harness = Harness(paper_path=str(paper_path), max_loop_turns=50)

            # 设置 findings
            harness.state.findings = [
                {"finding": "F1", "priority": "high", "section": "methods", "status": "verified", "evidence": "E1 detailed evidence"},
            ]
            harness.state.loop_turns = 10

            # 设置 cognitive_hints（模拟 Agent 之前生成了认知提示）
            harness.state.cognitive_hints = CognitiveHints(
                paper_type_description="DID论文",
                focus_dimensions=["平行趋势", "事件窗口选择"],
                typical_weaknesses=["pre-trend violation"],
                verification_strategies=["检查动态效应图"],
            )

            # 调用 end_session
            harness.end_session(paper_title="Test DID Paper")

            # 验证 memory 中有持久化的经验
            # 2 focus + 1 verification + 从 tool_call 提取的程序性模式
            procs = harness.memory.state.procedures
            # 至少有 cognitive_hints 带来的 3 个 (2 focus + 1 strat)
            hint_procs = [p for p in procs if p.category in ("review_focus", "verification_strategy")]
            self.assertGreaterEqual(len(hint_procs), 3)

            # 验证 typical_weakness 写入了 DomainPattern
            # (来自 cognitive_hints 和 extract_domain_patterns 两个来源)
            weakness_patterns = [p for p in harness.memory.state.patterns if p.category == "typical_weakness"]
            self.assertGreaterEqual(len(weakness_patterns), 1)


# ============================================================
# 运行
# ============================================================

if __name__ == "__main__":
    unittest.main()
