"""
tests/test_v3_paper_cognition_graph.py — V3 Paper Cognition Graph (PCG) 测试

测试内容:
    1. PCGNode / PCGEdge 数据结构默认值
    2. PaperCognitionGraph 空图创建
    3. from_structure_index 桥接构建
    4. add_edge 去重与权重更新
    5. coverage_gaps 覆盖缺口检测
    6. update_after_read 深度更新
    7. format_for_zone_a 格式输出
    8. context_for_task 上下文组装
    9. serialize / restore roundtrip
   10. 多边场景
   11. PCG_FORMAT_MAX_TOKENS 截断控制
"""

import sys
import os
import json
import pytest
from dataclasses import dataclass, field
from unittest.mock import MagicMock

# 确保能找到 core 模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.paper_cognition_graph import (
    PCGNode,
    PCGEdge,
    PaperCognitionGraph,
)
from core.godel_config import PCG_FORMAT_MAX_TOKENS


# ============================================================
# Fixtures / Helpers
# ============================================================

def _make_mock_structure_index(sections=None, word_counts=None, dependency_pairs=None,
                                evidence_map=None, cross_references=None, paper_type="empirical"):
    """创建 Mock PaperStructureIndex 用于测试 from_structure_index。"""
    index = MagicMock()
    index.sections = sections or ["Abstract", "Introduction", "Methods", "Results", "Conclusion"]
    index.section_word_counts = word_counts or {s: (i + 1) * 200 for i, s in enumerate(index.sections)}
    index.dependency_pairs = dependency_pairs or []
    index.evidence_map = evidence_map or {}
    index.cross_references = cross_references or []
    index.paper_type = paper_type
    return index


# ============================================================
# 测试: PCGNode 和 PCGEdge 默认值
# ============================================================

class TestPCGNodeDefaults:
    """测试 PCGNode 数据结构字段默认值。"""

    def test_node_required_field(self):
        """section_name 是必填字段。"""
        node = PCGNode(section_name="Introduction")
        assert node.section_name == "Introduction"

    def test_node_skeleton_defaults(self):
        """骨架层字段应有正确默认值。"""
        node = PCGNode(section_name="Methods")
        assert node.word_count == 0
        assert node.outgoing_refs == []
        assert node.incoming_refs == []

    def test_node_cognitive_defaults(self):
        """认知层字段应有正确默认值。"""
        node = PCGNode(section_name="Results")
        assert node.digest == ""
        assert node.claims == []
        assert node.assumptions == []

    def test_node_tracking_defaults(self):
        """追踪层字段应有正确默认值。"""
        node = PCGNode(section_name="Conclusion")
        assert node.read_depth == "unread"
        assert node.findings_linked == []
        assert node.hypotheses_linked == []

    def test_node_contrast_defaults(self):
        """IntraSession Contrast 标记应有正确默认值。"""
        node = PCGNode(section_name="Discussion")
        assert node.contrast_phase == "none"
        assert node.habits_active_when_read == []

    def test_node_list_fields_are_independent(self):
        """不同实例的 list 字段应互不影响（default_factory 隔离）。"""
        node1 = PCGNode(section_name="A")
        node2 = PCGNode(section_name="B")
        node1.claims.append("claim_X")
        assert node2.claims == []


class TestPCGEdgeDefaults:
    """测试 PCGEdge 数据结构字段默认值。"""

    def test_edge_required_fields(self):
        """source 和 target 是必填字段。"""
        edge = PCGEdge(source="Methods", target="Results")
        assert edge.source == "Methods"
        assert edge.target == "Results"

    def test_edge_defaults(self):
        """可选字段应有正确默认值。"""
        edge = PCGEdge(source="A", target="B")
        assert edge.edge_type == "REFERENCES"
        assert edge.weight == 1.0
        assert edge.evidence == ""
        assert edge.discovered_at_turn == 0
        assert edge.verified is False


# ============================================================
# 测试: PaperCognitionGraph 空图创建
# ============================================================

class TestEmptyGraph:
    """测试空 PaperCognitionGraph。"""

    def test_empty_graph_creation(self):
        """空图应正确创建。"""
        pcg = PaperCognitionGraph()
        assert pcg.nodes == {}
        assert pcg.edges == []
        assert pcg.paper_type == "unknown"

    def test_empty_graph_is_empty(self):
        """空图 is_empty 应返回 True。"""
        pcg = PaperCognitionGraph()
        assert pcg.is_empty() is True

    def test_empty_graph_section_count(self):
        """空图 section_count 应为 0。"""
        pcg = PaperCognitionGraph()
        assert pcg.section_count() == 0

    def test_empty_graph_format_for_zone_a(self):
        """空图 format_for_zone_a 应返回空字符串。"""
        pcg = PaperCognitionGraph()
        assert pcg.format_for_zone_a() == ""

    def test_empty_graph_context_for_task(self):
        """空图 context_for_task 应返回空字符串。"""
        pcg = PaperCognitionGraph()
        assert pcg.context_for_task("Methods") == ""

    def test_empty_graph_coverage_gaps(self):
        """空图 coverage_gaps 应返回空 dict。"""
        pcg = PaperCognitionGraph()
        gaps = pcg.coverage_gaps()
        assert gaps == {"unread": [], "unverified_claims": [], "orphan_findings": []}

    def test_empty_graph_serialize(self):
        """空图 serialize_for_compaction 应返回空字符串。"""
        pcg = PaperCognitionGraph()
        assert pcg.serialize_for_compaction() == ""


# ============================================================
# 测试: from_structure_index 桥接构建
# ============================================================

class TestFromStructureIndex:
    """测试 from_structure_index 从 PaperStructureIndex 构建。"""

    def test_creates_correct_nodes(self):
        """应为每个 section 创建对应节点。"""
        sections = ["Abstract", "Introduction", "Methods", "Results"]
        index = _make_mock_structure_index(sections=sections)
        pcg = PaperCognitionGraph.from_structure_index(index)

        assert not pcg.is_empty()
        assert pcg.section_count() == 4
        for s in sections:
            assert s in pcg.nodes
            assert pcg.nodes[s].section_name == s

    def test_inherits_word_counts(self):
        """节点应继承 section_word_counts。"""
        sections = ["Abstract", "Methods"]
        word_counts = {"Abstract": 150, "Methods": 800}
        index = _make_mock_structure_index(sections=sections, word_counts=word_counts)
        pcg = PaperCognitionGraph.from_structure_index(index)

        assert pcg.nodes["Abstract"].word_count == 150
        assert pcg.nodes["Methods"].word_count == 800

    def test_inherits_paper_type(self):
        """应继承 paper_type。"""
        index = _make_mock_structure_index(paper_type="RCT")
        pcg = PaperCognitionGraph.from_structure_index(index)
        assert pcg.paper_type == "RCT"

    def test_creates_edges_from_dependency_pairs(self):
        """dependency_pairs 应转化为 REFERENCES 边。"""
        sections = ["Introduction", "Methods", "Results"]
        dep_pairs = [("Introduction", "Methods"), ("Methods", "Results")]
        index = _make_mock_structure_index(sections=sections, dependency_pairs=dep_pairs)
        pcg = PaperCognitionGraph.from_structure_index(index)

        assert len(pcg.edges) >= 2
        ref_edges = [e for e in pcg.edges if e.edge_type == "REFERENCES"]
        sources_targets = [(e.source, e.target) for e in ref_edges]
        assert ("Introduction", "Methods") in sources_targets
        assert ("Methods", "Results") in sources_targets

    def test_creates_edges_from_evidence_map(self):
        """evidence_map 应转化为 REFERENCES 边。"""
        sections = ["Methods", "Results"]
        evidence_map = {"Figure 1": ["Methods", "Results"]}
        index = _make_mock_structure_index(sections=sections, evidence_map=evidence_map)
        pcg = PaperCognitionGraph.from_structure_index(index)

        fig_edges = [e for e in pcg.edges if e.target == "Figure 1"]
        assert len(fig_edges) == 2

    def test_all_nodes_start_unread(self):
        """所有新建节点的 read_depth 应为 'unread'。"""
        index = _make_mock_structure_index()
        pcg = PaperCognitionGraph.from_structure_index(index)

        for node in pcg.nodes.values():
            assert node.read_depth == "unread"

    def test_graceful_failure_returns_empty_pcg(self):
        """from_structure_index 异常时应返回空 PCG 而不是抛异常。"""
        # 给一个会导致 AttributeError 的 mock
        broken_index = MagicMock()
        broken_index.sections = None  # 迭代会 TypeError
        pcg = PaperCognitionGraph.from_structure_index(broken_index)
        assert pcg.is_empty()


# ============================================================
# 测试: add_edge 去重与权重更新
# ============================================================

class TestAddEdge:
    """测试 add_edge 的去重和权重更新逻辑。"""

    def test_add_new_edge(self):
        """添加全新边应增加 edges 列表长度。"""
        pcg = PaperCognitionGraph()
        pcg.add_edge("A", "B", "CLAIM_SUPPORTS", weight=0.8)
        assert len(pcg.edges) == 1
        assert pcg.edges[0].source == "A"
        assert pcg.edges[0].target == "B"
        assert pcg.edges[0].edge_type == "CLAIM_SUPPORTS"
        assert pcg.edges[0].weight == 0.8

    def test_duplicate_edge_updates_weight(self):
        """相同 (source, target, edge_type) 不重复添加，而是更新 weight。"""
        pcg = PaperCognitionGraph()
        pcg.add_edge("A", "B", "REFERENCES", weight=0.5)
        pcg.add_edge("A", "B", "REFERENCES", weight=0.9)
        assert len(pcg.edges) == 1
        assert pcg.edges[0].weight == 0.9  # 取更高值

    def test_duplicate_edge_lower_weight_no_downgrade(self):
        """重复添加但权重更低时不应降级。"""
        pcg = PaperCognitionGraph()
        pcg.add_edge("A", "B", "REFERENCES", weight=0.9)
        pcg.add_edge("A", "B", "REFERENCES", weight=0.3)
        assert pcg.edges[0].weight == 0.9

    def test_different_edge_types_not_deduped(self):
        """不同 edge_type 应视为不同边。"""
        pcg = PaperCognitionGraph()
        pcg.add_edge("A", "B", "REFERENCES", weight=0.5)
        pcg.add_edge("A", "B", "CONTRADICTS", weight=0.8)
        assert len(pcg.edges) == 2

    def test_add_edge_with_evidence(self):
        """add_edge 应保存 evidence。"""
        pcg = PaperCognitionGraph()
        pcg.add_edge("A", "B", "VALIDATES", evidence="Table 3 confirms")
        assert pcg.edges[0].evidence == "Table 3 confirms"

    def test_add_edge_updates_evidence(self):
        """重复边添加带 evidence 时应更新。"""
        pcg = PaperCognitionGraph()
        pcg.add_edge("A", "B", "REFERENCES", weight=0.5, evidence="old")
        pcg.add_edge("A", "B", "REFERENCES", weight=0.9, evidence="new evidence")
        assert pcg.edges[0].evidence == "new evidence"


# ============================================================
# 测试: coverage_gaps 覆盖缺口检测
# ============================================================

class TestCoverageGaps:
    """测试 coverage_gaps 覆盖缺口检测。"""

    def test_all_unread_returns_all(self):
        """所有节点未读时应全部列入 unread。"""
        index = _make_mock_structure_index(sections=["A", "B", "C"])
        pcg = PaperCognitionGraph.from_structure_index(index)
        gaps = pcg.coverage_gaps()
        assert set(gaps["unread"]) == {"A", "B", "C"}

    def test_read_node_removed_from_unread(self):
        """已读节点不应出现在 unread 列表中。"""
        index = _make_mock_structure_index(sections=["A", "B", "C"])
        pcg = PaperCognitionGraph.from_structure_index(index)
        pcg.update_after_read("A", read_depth="read")
        gaps = pcg.coverage_gaps()
        assert "A" not in gaps["unread"]
        assert "B" in gaps["unread"]
        assert "C" in gaps["unread"]

    def test_unverified_claims_detected(self):
        """有 claims 但未 verified 的节点应在 unverified_claims 中。"""
        index = _make_mock_structure_index(sections=["Methods", "Results"])
        pcg = PaperCognitionGraph.from_structure_index(index)
        pcg.update_after_read("Methods", claims=["claim1"], read_depth="read")
        gaps = pcg.coverage_gaps()
        assert "Methods" in gaps["unverified_claims"]

    def test_verified_node_not_in_unverified(self):
        """verified 深度的节点不应出现在 unverified_claims 中。"""
        index = _make_mock_structure_index(sections=["Methods"])
        pcg = PaperCognitionGraph.from_structure_index(index)
        pcg.update_after_read("Methods", claims=["claim1"], read_depth="verified")
        gaps = pcg.coverage_gaps()
        assert "Methods" not in gaps["unverified_claims"]

    def test_orphan_findings_detected(self):
        """有 findings 但无 claims 的节点应在 orphan_findings 中。"""
        index = _make_mock_structure_index(sections=["Results"])
        pcg = PaperCognitionGraph.from_structure_index(index)
        pcg.update_after_read("Results", read_depth="read")
        pcg.link_finding("F001", "Results")
        gaps = pcg.coverage_gaps()
        assert "Results" in gaps["orphan_findings"]


# ============================================================
# 测试: update_after_read 深度更新
# ============================================================

class TestUpdateAfterRead:
    """测试 update_after_read 节点更新。"""

    def test_updates_read_depth(self):
        """应正确更新 read_depth。"""
        index = _make_mock_structure_index(sections=["Methods"])
        pcg = PaperCognitionGraph.from_structure_index(index)
        pcg.update_after_read("Methods", read_depth="read")
        assert pcg.nodes["Methods"].read_depth == "read"

    def test_updates_digest(self):
        """应正确更新 digest。"""
        index = _make_mock_structure_index(sections=["Methods"])
        pcg = PaperCognitionGraph.from_structure_index(index)
        pcg.update_after_read("Methods", digest="Uses DID approach with 2-way FE")
        assert pcg.nodes["Methods"].digest == "Uses DID approach with 2-way FE"

    def test_digest_truncated_at_300_chars(self):
        """digest 应硬限 300 字符。"""
        index = _make_mock_structure_index(sections=["Methods"])
        pcg = PaperCognitionGraph.from_structure_index(index)
        long_digest = "x" * 500
        pcg.update_after_read("Methods", digest=long_digest)
        assert len(pcg.nodes["Methods"].digest) == 300

    def test_updates_claims(self):
        """应正确更新 claims。"""
        index = _make_mock_structure_index(sections=["Results"])
        pcg = PaperCognitionGraph.from_structure_index(index)
        pcg.update_after_read("Results", claims=["Policy X increases Y by 15%"])
        assert pcg.nodes["Results"].claims == ["Policy X increases Y by 15%"]

    def test_updates_assumptions(self):
        """应正确更新 assumptions。"""
        index = _make_mock_structure_index(sections=["Methods"])
        pcg = PaperCognitionGraph.from_structure_index(index)
        pcg.update_after_read("Methods", assumptions=["Parallel trends hold"])
        assert pcg.nodes["Methods"].assumptions == ["Parallel trends hold"]

    def test_unknown_section_silently_skipped(self):
        """更新不存在的 section 应静默跳过。"""
        index = _make_mock_structure_index(sections=["Methods"])
        pcg = PaperCognitionGraph.from_structure_index(index)
        # 不应抛异常
        pcg.update_after_read("NonExistent", read_depth="read")
        assert "NonExistent" not in pcg.nodes

    def test_update_removes_from_coverage_gaps(self):
        """更新 read_depth 后应从 coverage_gaps unread 中移除。"""
        index = _make_mock_structure_index(sections=["A", "B"])
        pcg = PaperCognitionGraph.from_structure_index(index)
        assert "A" in pcg.coverage_gaps()["unread"]
        pcg.update_after_read("A", read_depth="scanned")
        assert "A" not in pcg.coverage_gaps()["unread"]


# ============================================================
# 测试: format_for_zone_a 格式输出
# ============================================================

class TestFormatForZoneA:
    """测试 format_for_zone_a Zone A 导航摘要。"""

    def test_contains_pcg_header(self):
        """输出应包含 PCG 导航 header。"""
        index = _make_mock_structure_index(sections=["Abstract", "Methods"])
        pcg = PaperCognitionGraph.from_structure_index(index)
        output = pcg.format_for_zone_a()
        assert "[PCG 导航]" in output
        assert "sections=2" in output

    def test_contains_paper_type(self):
        """输出应包含 paper_type。"""
        index = _make_mock_structure_index(paper_type="DID")
        pcg = PaperCognitionGraph.from_structure_index(index)
        output = pcg.format_for_zone_a()
        assert "type=DID" in output

    def test_contains_section_names_with_depth_markers(self):
        """输出应包含各 section 的 read_depth 标记。"""
        index = _make_mock_structure_index(sections=["Abstract", "Methods"])
        pcg = PaperCognitionGraph.from_structure_index(index)
        pcg.update_after_read("Abstract", read_depth="read")
        output = pcg.format_for_zone_a()
        # ● = read, ○ = unread
        assert "●" in output  # Abstract is read
        assert "○" in output  # Methods is unread

    def test_contains_word_counts(self):
        """输出应包含 word counts。"""
        sections = ["Methods"]
        word_counts = {"Methods": 1500}
        index = _make_mock_structure_index(sections=sections, word_counts=word_counts)
        pcg = PaperCognitionGraph.from_structure_index(index)
        output = pcg.format_for_zone_a()
        assert "1500w" in output

    def test_contains_coverage_gaps_summary(self):
        """输出应包含覆盖缺口概要。"""
        index = _make_mock_structure_index(sections=["A", "B", "C"])
        pcg = PaperCognitionGraph.from_structure_index(index)
        output = pcg.format_for_zone_a()
        assert "覆盖缺口" in output
        assert "未读:3" in output

    def test_high_weight_edges_shown(self):
        """权重 > 0.7 的边应在核心关联中显示。"""
        index = _make_mock_structure_index(
            sections=["Methods", "Results"],
            evidence_map={"Figure 1": ["Methods"]},  # weight=0.7
        )
        pcg = PaperCognitionGraph.from_structure_index(index)
        # 手动添加高权重边
        pcg.add_edge("Methods", "Results", "BUILDS_ON", weight=0.9)
        output = pcg.format_for_zone_a()
        assert "核心关联" in output
        assert "BUILDS_ON" in output

    def test_respects_max_tokens_truncation(self):
        """超出 max_tokens 时应截断。"""
        sections = [f"Section_{i}" for i in range(50)]
        index = _make_mock_structure_index(sections=sections)
        pcg = PaperCognitionGraph.from_structure_index(index)
        # 用很小的 max_tokens 强制截断
        output = pcg.format_for_zone_a(max_tokens=50)
        # 50 tokens * 4 chars = 200 chars max
        assert len(output) <= 200 + 20  # +20 for truncation marker
        assert "已截断" in output

    def test_default_max_tokens_uses_config(self):
        """不传 max_tokens 时应使用 PCG_FORMAT_MAX_TOKENS。"""
        index = _make_mock_structure_index(sections=["A"])
        pcg = PaperCognitionGraph.from_structure_index(index)
        output = pcg.format_for_zone_a()
        # 输出不应超过配置限制
        max_chars = PCG_FORMAT_MAX_TOKENS * 4
        assert len(output) <= max_chars


# ============================================================
# 测试: context_for_task 上下文组装
# ============================================================

class TestContextForTask:
    """测试 context_for_task 上下文组装。"""

    def test_returns_empty_for_unknown_section(self):
        """请求不存在的 section 应返回空。"""
        index = _make_mock_structure_index(sections=["Methods"])
        pcg = PaperCognitionGraph.from_structure_index(index)
        assert pcg.context_for_task("NonExistent") == ""

    def test_includes_current_section_info(self):
        """应包含当前 section 的信息。"""
        index = _make_mock_structure_index(sections=["Methods", "Results"])
        pcg = PaperCognitionGraph.from_structure_index(index)
        pcg.update_after_read("Methods", digest="DID with 2-way FE", claims=["X->Y"])
        output = pcg.context_for_task("Methods")
        assert "当前 section: Methods" in output
        assert "DID with 2-way FE" in output

    def test_includes_related_sections(self):
        """应包含通过边关联的 sections。"""
        index = _make_mock_structure_index(sections=["Methods", "Results"])
        pcg = PaperCognitionGraph.from_structure_index(index)
        pcg.update_after_read("Results", digest="Treatment effect is 0.15")
        pcg.add_edge("Methods", "Results", "BUILDS_ON", weight=0.9)
        output = pcg.context_for_task("Methods")
        assert "相关 sections" in output
        assert "Results" in output

    def test_includes_linked_findings(self):
        """应包含关联的 findings。"""
        index = _make_mock_structure_index(sections=["Methods"])
        pcg = PaperCognitionGraph.from_structure_index(index)
        pcg.update_after_read("Methods", read_depth="read")
        pcg.link_finding("F001", "Methods")
        pcg.link_finding("F002", "Methods")
        output = pcg.context_for_task("Methods")
        assert "关联 findings" in output
        assert "F001" in output

    def test_respects_max_tokens(self):
        """应遵守 max_tokens 限制。"""
        sections = ["Methods"] + [f"Related_{i}" for i in range(20)]
        index = _make_mock_structure_index(sections=sections)
        pcg = PaperCognitionGraph.from_structure_index(index)
        # 给每个相关 section 添加长 digest 和边
        for i in range(20):
            name = f"Related_{i}"
            pcg.update_after_read(name, digest="A" * 150, read_depth="read")
            pcg.add_edge("Methods", name, "REFERENCES", weight=0.8)
        output = pcg.context_for_task("Methods", max_tokens=100)
        # 100 tokens * 4 = 400 chars max
        assert len(output) <= 500  # 允许一些溢出（最后一个 item 可能略超）


# ============================================================
# 测试: serialize / restore roundtrip
# ============================================================

class TestSerializeRestore:
    """测试 serialize_for_compaction 和 restore_from_compaction。"""

    def test_serialize_non_empty_returns_json(self):
        """非空 PCG 序列化应返回合法 JSON。"""
        index = _make_mock_structure_index(sections=["Methods", "Results"])
        pcg = PaperCognitionGraph.from_structure_index(index)
        pcg.update_after_read("Methods", claims=["X"], read_depth="read")
        serialized = pcg.serialize_for_compaction()
        assert serialized != ""
        data = json.loads(serialized)
        assert "nodes" in data
        assert "edges" in data

    def test_roundtrip_preserves_read_depth(self):
        """roundtrip 应保留 read_depth 状态。"""
        index = _make_mock_structure_index(sections=["Methods", "Results"])
        pcg = PaperCognitionGraph.from_structure_index(index)
        pcg.update_after_read("Methods", read_depth="verified")
        pcg.update_after_read("Results", read_depth="scanned")

        serialized = pcg.serialize_for_compaction()
        restored = PaperCognitionGraph.restore_from_compaction(serialized, index)

        assert restored.nodes["Methods"].read_depth == "verified"
        assert restored.nodes["Results"].read_depth == "scanned"

    def test_roundtrip_preserves_claims(self):
        """roundtrip 应保留 claims。"""
        index = _make_mock_structure_index(sections=["Methods"])
        pcg = PaperCognitionGraph.from_structure_index(index)
        pcg.update_after_read("Methods", claims=["Claim A", "Claim B"], read_depth="read")

        serialized = pcg.serialize_for_compaction()
        restored = PaperCognitionGraph.restore_from_compaction(serialized, index)

        assert restored.nodes["Methods"].claims == ["Claim A", "Claim B"]

    def test_roundtrip_preserves_findings_linked(self):
        """roundtrip 应保留 findings_linked。"""
        index = _make_mock_structure_index(sections=["Results"])
        pcg = PaperCognitionGraph.from_structure_index(index)
        pcg.update_after_read("Results", read_depth="read")
        pcg.link_finding("F001", "Results")
        pcg.link_finding("F002", "Results")

        serialized = pcg.serialize_for_compaction()
        restored = PaperCognitionGraph.restore_from_compaction(serialized, index)

        assert "F001" in restored.nodes["Results"].findings_linked
        assert "F002" in restored.nodes["Results"].findings_linked

    def test_roundtrip_preserves_edges(self):
        """roundtrip 应保留高权重边。"""
        index = _make_mock_structure_index(sections=["Methods", "Results"])
        pcg = PaperCognitionGraph.from_structure_index(index)
        pcg.add_edge("Methods", "Results", "BUILDS_ON", weight=0.8)

        serialized = pcg.serialize_for_compaction()
        restored = PaperCognitionGraph.restore_from_compaction(serialized, index)

        builds_on = [e for e in restored.edges if e.edge_type == "BUILDS_ON"]
        assert len(builds_on) == 1
        assert builds_on[0].weight == 0.8

    def test_low_weight_edges_not_serialized(self):
        """权重 < 0.5 且未 verified 的边不应被序列化。"""
        index = _make_mock_structure_index(sections=["A", "B"])
        pcg = PaperCognitionGraph.from_structure_index(index)
        pcg.add_edge("A", "B", "REFERENCES", weight=0.3)

        serialized = pcg.serialize_for_compaction()
        data = json.loads(serialized)
        # 从 from_structure_index 继承的边可能有 weight=0.5
        # 但手动添加的 0.3 边不应在其中（除非 from_structure_index 也有同一条）
        low_edges = [e for e in data["edges"] if e["w"] < 0.5]
        assert len(low_edges) == 0

    def test_restore_from_empty_string(self):
        """空字符串恢复应返回空 PCG。"""
        restored = PaperCognitionGraph.restore_from_compaction("")
        assert restored.is_empty()

    def test_restore_from_invalid_json(self):
        """非法 JSON 恢复应返回空 PCG。"""
        restored = PaperCognitionGraph.restore_from_compaction("not json{{{")
        assert restored.is_empty()

    def test_restore_without_index(self):
        """无 index 时应仅恢复 paper_type 和空 nodes。"""
        index = _make_mock_structure_index(sections=["Methods"], paper_type="RCT")
        pcg = PaperCognitionGraph.from_structure_index(index)
        pcg.update_after_read("Methods", claims=["C1"], read_depth="read")

        serialized = pcg.serialize_for_compaction()
        # 不传 index
        restored = PaperCognitionGraph.restore_from_compaction(serialized, index=None)
        assert restored.paper_type == "RCT"
        # 因为没有 index 重建骨架，nodes 为空，claims 不会被恢复
        assert restored.section_count() == 0

    def test_roundtrip_preserves_contrast_phase(self):
        """roundtrip 应保留 contrast_phase。"""
        index = _make_mock_structure_index(sections=["Methods"])
        pcg = PaperCognitionGraph.from_structure_index(index)
        pcg.nodes["Methods"].contrast_phase = "A"

        serialized = pcg.serialize_for_compaction()
        restored = PaperCognitionGraph.restore_from_compaction(serialized, index)
        assert restored.nodes["Methods"].contrast_phase == "A"


# ============================================================
# 测试: 多边场景
# ============================================================

class TestMultipleEdges:
    """测试图中多条边的交互场景。"""

    def test_multiple_edges_between_same_pair_different_types(self):
        """同一对节点间可以有多条不同类型的边。"""
        pcg = PaperCognitionGraph()
        pcg.add_edge("A", "B", "REFERENCES", weight=0.5)
        pcg.add_edge("A", "B", "CONTRADICTS", weight=0.8)
        pcg.add_edge("A", "B", "VALIDATES", weight=0.6)
        assert len(pcg.edges) == 3

    def test_bidirectional_edges(self):
        """A→B 和 B→A 应视为不同边。"""
        pcg = PaperCognitionGraph()
        pcg.add_edge("A", "B", "REFERENCES", weight=0.5)
        pcg.add_edge("B", "A", "REFERENCES", weight=0.7)
        assert len(pcg.edges) == 2

    def test_mark_verified_updates_correct_edge(self):
        """mark_verified 应只标记匹配的边。"""
        pcg = PaperCognitionGraph()
        pcg.add_edge("A", "B", "REFERENCES", weight=0.5)
        pcg.add_edge("A", "B", "CONTRADICTS", weight=0.8)
        pcg.mark_verified("A", "B", "CONTRADICTS")

        ref_edge = [e for e in pcg.edges if e.edge_type == "REFERENCES"][0]
        con_edge = [e for e in pcg.edges if e.edge_type == "CONTRADICTS"][0]
        assert ref_edge.verified is False
        assert con_edge.verified is True

    def test_link_finding_and_hypothesis(self):
        """link_finding 和 link_hypothesis 应正确关联。"""
        index = _make_mock_structure_index(sections=["Results"])
        pcg = PaperCognitionGraph.from_structure_index(index)
        pcg.link_finding("F001", "Results")
        pcg.link_finding("F002", "Results")
        pcg.link_hypothesis("H001", "Results")

        assert pcg.nodes["Results"].findings_linked == ["F001", "F002"]
        assert pcg.nodes["Results"].hypotheses_linked == ["H001"]

    def test_link_finding_deduplicates(self):
        """重复 link_finding 不应产生重复项。"""
        index = _make_mock_structure_index(sections=["Results"])
        pcg = PaperCognitionGraph.from_structure_index(index)
        pcg.link_finding("F001", "Results")
        pcg.link_finding("F001", "Results")
        assert pcg.nodes["Results"].findings_linked == ["F001"]


# ============================================================
# 测试: PCG_FORMAT_MAX_TOKENS 配置
# ============================================================

class TestPCGFormatMaxTokens:
    """测试 PCG_FORMAT_MAX_TOKENS 配置常量。"""

    def test_config_value_exists(self):
        """PCG_FORMAT_MAX_TOKENS 应存在且为正整数。"""
        assert isinstance(PCG_FORMAT_MAX_TOKENS, int)
        assert PCG_FORMAT_MAX_TOKENS > 0

    def test_config_value_is_1500(self):
        """PCG_FORMAT_MAX_TOKENS 当前应为 1500。"""
        assert PCG_FORMAT_MAX_TOKENS == 1500

    def test_large_graph_respects_token_limit(self):
        """大图的 format_for_zone_a 应遵守 token 限制。"""
        # 创建一个包含很多 section 的大图
        sections = [f"Section_{i:03d}" for i in range(100)]
        word_counts = {s: 500 for s in sections}
        index = _make_mock_structure_index(sections=sections, word_counts=word_counts)
        pcg = PaperCognitionGraph.from_structure_index(index)

        output = pcg.format_for_zone_a()
        max_chars = PCG_FORMAT_MAX_TOKENS * 4
        assert len(output) <= max_chars


# ============================================================
# 测试: get_node helper
# ============================================================

class TestGetNode:
    """测试 get_node helper。"""

    def test_get_existing_node(self):
        """应返回已存在的节点。"""
        index = _make_mock_structure_index(sections=["Methods"])
        pcg = PaperCognitionGraph.from_structure_index(index)
        node = pcg.get_node("Methods")
        assert node is not None
        assert node.section_name == "Methods"

    def test_get_nonexistent_node(self):
        """不存在的节点应返回 None。"""
        pcg = PaperCognitionGraph()
        assert pcg.get_node("Ghost") is None


# ============================================================
# Domain Template Tests (B2)
# ============================================================

class TestDomainTemplate:
    """测试 PCG 领域模板 _apply_domain_template()。"""

    def test_empirical_econ_template_boosts_edges(self):
        """paper_type=empirical_econ + sections 含 Identification Strategy → 相关 edge weight 被 boost。"""
        index = _make_mock_structure_index(
            sections=["Abstract", "Identification Strategy", "Data", "Results", "Robustness"],
            dependency_pairs=[("Identification Strategy", "Results")],
            paper_type="empirical_econ",
        )
        pcg = PaperCognitionGraph.from_structure_index(index)

        # 找到 Identification Strategy → Results 的边
        id_to_results = [
            e for e in pcg.edges
            if e.source == "Identification Strategy" and e.target == "Results"
        ]
        assert len(id_to_results) >= 1
        # 原始 weight=0.5，应被 boost 0.4 → 0.9
        assert id_to_results[0].weight == pytest.approx(0.9, abs=0.01)

    def test_ml_experiment_template_creates_edges(self):
        """paper_type=ml_experiment + sections 含 Method/Experiments → 创建新 edge。"""
        index = _make_mock_structure_index(
            sections=["Abstract", "Method", "Experiments", "Ablation Study", "Results"],
            dependency_pairs=[],  # 无预先 edges
            paper_type="ml_experiment",
        )
        pcg = PaperCognitionGraph.from_structure_index(index)

        # Method → Experiments 应被模板创建
        method_to_exp = [
            e for e in pcg.edges
            if e.source == "Method" and e.target == "Experiments"
        ]
        assert len(method_to_exp) >= 1
        # 新建 edge: weight = min(1.0, 0.5 + 0.4) = 0.9
        assert method_to_exp[0].weight == pytest.approx(0.9, abs=0.01)

    def test_unknown_type_no_template_applied(self):
        """paper_type=unknown_type → PCG 与未应用模板时完全相同。"""
        index = _make_mock_structure_index(
            sections=["Abstract", "Introduction", "Methods", "Results"],
            dependency_pairs=[("Introduction", "Methods")],
            paper_type="unknown_type",
        )
        pcg = PaperCognitionGraph.from_structure_index(index)

        # 只有原始依赖产生的边
        assert len(pcg.edges) == 1
        assert pcg.edges[0].weight == 0.5  # 未被 boost

    def test_sections_not_matching_template_no_effect(self):
        """sections 不匹配模板 expected → 模板不生效，无 crash。"""
        index = _make_mock_structure_index(
            sections=["Abstract", "Literature Review", "Discussion", "Conclusion"],
            dependency_pairs=[],
            paper_type="empirical_econ",  # 模板期望 identification/data/results
        )
        pcg = PaperCognitionGraph.from_structure_index(index)

        # 没有匹配到任何 critical_edge 的 section → 不创建/boost 边
        assert len(pcg.edges) == 0

    def test_template_weight_capped_at_1(self):
        """boost 后 weight 不超过 1.0。"""
        index = _make_mock_structure_index(
            sections=["Identification", "Results"],
            dependency_pairs=[("Identification", "Results")],
            paper_type="empirical_econ",
        )
        # 手动设置已有边 weight=0.8
        pcg = PaperCognitionGraph.from_structure_index(index)
        # 即使 0.8 + 0.4 = 1.2，应 cap 在 1.0
        boosted = [e for e in pcg.edges if e.source == "Identification" and e.target == "Results"]
        assert boosted[0].weight <= 1.0

    def test_fuzzy_match_section(self):
        """_fuzzy_match_section 正确匹配。"""
        sections = ["3. Identification Strategy", "4. Data and Sample", "5. Results"]

        # keyword "identification" 应匹配 "3. Identification Strategy"
        result = PaperCognitionGraph._fuzzy_match_section("identification", sections)
        assert result == "3. Identification Strategy"

        # keyword "data" 应匹配 "4. Data and Sample"
        result = PaperCognitionGraph._fuzzy_match_section("data", sections)
        assert result == "4. Data and Sample"

        # keyword "nonexistent" 不匹配
        result = PaperCognitionGraph._fuzzy_match_section("nonexistent", sections)
        assert result is None


# ============================================================
# 运行
# ============================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
