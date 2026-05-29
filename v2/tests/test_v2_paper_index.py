"""
tests/test_v2_paper_index.py — Paper Structure Pre-indexing 单元测试

验证:
1. PaperIndexBuilder 正则提取交叉引用
2. 论文类型推断
3. 证据映射构建
4. 依赖对推断
5. format_for_context 输出格式
6. format_subset_for_section 子集输出
7. 阅读优先级推荐
8. 空输入 / 边界情况
"""

import pytest

from core.paper_index import (
    CrossReference,
    PaperStructureIndex,
    PaperIndexBuilder,
)


# ============================================================
# 测试用论文 sections
# ============================================================

EMPIRICAL_PAPER = {
    "introduction": (
        "This paper studies the effect of minimum wage on employment. "
        "We use a difference-in-differences design (Section 3) with data from "
        "the CPS. Our main results are shown in Figure 1 and Table 2. "
        "The identification strategy follows Section 2.1 of the model."
    ),
    "model": (
        "We specify the following model. The key equation is Eq. (1). "
        "We also draw on theoretical predictions from Section 4.2. "
        "The dependent variable is defined in Table 1."
    ),
    "identification": (
        "Our identification relies on parallel trends (see Fig. 2a). "
        "The first stage results are in Table 3. "
        "Robustness checks in Section 5 confirm our findings."
    ),
    "results": (
        "Table 4 presents the main results. Figure 3 shows the event study. "
        "The coefficient from Eq. (1) is -0.03 (SE 0.01). "
        "We also report results by subgroup in Table 5 and Figure 4."
    ),
    "robustness": (
        "We conduct several checks. First, Figure 5 shows placebo tests. "
        "Second, Table 6 varies the bandwidth. "
        "The results are consistent with Section 4."
    ),
}

THEORETICAL_PAPER = {
    "introduction": "We prove a new theorem about equilibrium existence.",
    "model": "The model environment is as follows.",
    "theorem": "Theorem 1: Under Assumption A1, equilibrium exists. Proof: see Lemma 2.",
    "proof": "Lemma 2: The mapping is a contraction. Proof follows from Eq. (3).",
    "conclusion": "We have shown existence. Extensions in Section 3.1.",
}

MINIMAL_PAPER = {
    "abstract": "Short paper.",
    "body": "Some text without any cross-references at all.",
}


# ============================================================
# PaperIndexBuilder 测试
# ============================================================

class TestPaperIndexBuilder:
    """Builder 的核心解析能力。"""

    def setup_method(self):
        self.builder = PaperIndexBuilder()

    def test_basic_build(self):
        """基本构建应返回非空索引。"""
        index = self.builder.build(EMPIRICAL_PAPER)
        assert not index.is_empty()
        assert len(index.sections) == 5
        assert "introduction" in index.sections
        assert "results" in index.sections

    def test_extracts_figures(self):
        """应提取 Figure 引用。"""
        index = self.builder.build(EMPIRICAL_PAPER)
        figure_refs = [
            r for r in index.cross_references
            if r.target_type == "figure"
        ]
        # 至少应找到 Figure 1, 2a, 3, 4, 5
        figure_ids = {r.target_id for r in figure_refs}
        assert "Figure 1" in figure_ids
        assert "Figure 3" in figure_ids
        assert "Figure 2a" in figure_ids

    def test_extracts_tables(self):
        """应提取 Table 引用。"""
        index = self.builder.build(EMPIRICAL_PAPER)
        table_refs = [
            r for r in index.cross_references
            if r.target_type == "table"
        ]
        table_ids = {r.target_id for r in table_refs}
        assert "Table 2" in table_ids
        assert "Table 3" in table_ids
        assert "Table 4" in table_ids

    def test_extracts_equations(self):
        """应提取 Equation 引用。"""
        index = self.builder.build(EMPIRICAL_PAPER)
        eq_refs = [
            r for r in index.cross_references
            if r.target_type == "equation"
        ]
        eq_ids = {r.target_id for r in eq_refs}
        assert "Eq. 1" in eq_ids

    def test_extracts_section_refs(self):
        """应提取 Section 交叉引用。"""
        index = self.builder.build(EMPIRICAL_PAPER)
        sec_refs = [
            r for r in index.cross_references
            if r.target_type == "section"
        ]
        sec_ids = {r.target_id for r in sec_refs}
        assert "Section 3" in sec_ids
        assert "Section 5" in sec_ids

    def test_evidence_map(self):
        """证据映射应关联 figure/table 到引用它的 sections。"""
        index = self.builder.build(EMPIRICAL_PAPER)
        # Table 4 在 results 中
        assert "Table 4" in index.evidence_map
        assert "results" in index.evidence_map["Table 4"]

    def test_dependency_pairs(self):
        """应推断 section 间依赖。"""
        index = self.builder.build(EMPIRICAL_PAPER)
        # introduction 引用了 Section 3 → ("introduction", "Section 3")
        assert ("introduction", "Section 3") in index.dependency_pairs

    def test_word_counts(self):
        """word count 应为正整数。"""
        index = self.builder.build(EMPIRICAL_PAPER)
        for sec, count in index.section_word_counts.items():
            assert count > 0
            assert isinstance(count, int)

    def test_context_snippet(self):
        """CrossReference 应有非空的 context_snippet。"""
        index = self.builder.build(EMPIRICAL_PAPER)
        for ref in index.cross_references[:5]:
            assert len(ref.context_snippet) > 0
            assert len(ref.context_snippet) <= 64  # max_len=60 + "..."


# ============================================================
# 论文类型推断测试
# ============================================================

class TestPaperTypeDetection:
    """论文类型启发式判断。"""

    def setup_method(self):
        self.builder = PaperIndexBuilder()

    def test_empirical_paper(self):
        """含 results/identification 的论文应判为 empirical。"""
        index = self.builder.build(EMPIRICAL_PAPER)
        assert index.paper_type == "empirical"

    def test_theoretical_paper(self):
        """含 theorem/proof 但无 results 的论文应判为 theoretical。"""
        index = self.builder.build(THEORETICAL_PAPER)
        assert index.paper_type == "theoretical"

    def test_review_paper(self):
        """>25 sections 的论文应判为 survey。"""
        many_sections = {f"section_{i}": f"content {i}" for i in range(30)}
        index = self.builder.build(many_sections)
        assert index.paper_type == "survey"

    def test_unknown_paper(self):
        """无明显特征的论文应判为 unknown。"""
        index = self.builder.build(MINIMAL_PAPER)
        assert index.paper_type == "unknown"


# ============================================================
# format_for_context 测试
# ============================================================

class TestFormatForContext:
    """Context 注入格式化。"""

    def setup_method(self):
        self.builder = PaperIndexBuilder()

    def test_format_includes_disclaimer(self):
        """输出应包含噪音警告（UPGRADE_PLAN_FINAL 要求）。"""
        index = self.builder.build(EMPIRICAL_PAPER)
        output = index.format_for_context()
        assert "仅供导航参考" in output
        assert "可能存在噪音" in output

    def test_format_includes_structure(self):
        """输出应包含 section 列表。"""
        index = self.builder.build(EMPIRICAL_PAPER)
        output = index.format_for_context()
        assert "introduction" in output
        assert "results" in output

    def test_format_includes_evidence(self):
        """输出应包含证据使用信息。"""
        index = self.builder.build(EMPIRICAL_PAPER)
        output = index.format_for_context()
        assert "核心证据使用" in output

    def test_format_includes_paper_type(self):
        """输出应包含论文类型。"""
        index = self.builder.build(EMPIRICAL_PAPER)
        output = index.format_for_context()
        assert "empirical" in output

    def test_format_is_not_directive(self):
        """输出不应包含指令性语言。"""
        index = self.builder.build(EMPIRICAL_PAPER)
        output = index.format_for_context()
        assert "你必须" not in output
        assert "你应该" not in output

    def test_empty_index_returns_empty(self):
        """空索引返回空字符串。"""
        index = PaperStructureIndex()
        assert index.format_for_context() == ""


# ============================================================
# format_subset_for_section 测试
# ============================================================

class TestFormatSubset:
    """DEEP_REVIEW 阶段的子集注入。"""

    def setup_method(self):
        self.builder = PaperIndexBuilder()

    def test_subset_for_results(self):
        """results section 的子集应显示其引用的 figures/tables。"""
        index = self.builder.build(EMPIRICAL_PAPER)
        output = index.format_subset_for_section("results")
        assert "引用了" in output
        assert "Figure 3" in output or "Table 4" in output

    def test_subset_for_unreferenced_section(self):
        """无交叉引用的 section 返回空字符串。"""
        index = self.builder.build(MINIMAL_PAPER)
        output = index.format_subset_for_section("body")
        assert output == ""


# ============================================================
# 阅读优先级测试
# ============================================================

class TestReadingPriority:
    """基于引用密度的阅读建议。"""

    def setup_method(self):
        self.builder = PaperIndexBuilder()

    def test_priority_returns_sections(self):
        """应返回被引用最多的 sections。"""
        index = self.builder.build(EMPIRICAL_PAPER)
        priority = index.get_reading_priority()
        # Section 5 被 robustness 和 identification 引用
        # Section 3 被 introduction 引用
        assert len(priority) > 0
        # 所有都是 "Section X" 格式
        for p in priority:
            assert p.startswith("Section")


# ============================================================
# 边界情况
# ============================================================

class TestEdgeCases:
    """异常输入处理。"""

    def setup_method(self):
        self.builder = PaperIndexBuilder()

    def test_empty_sections(self):
        """空 sections dict 应返回空索引。"""
        index = self.builder.build({})
        assert index.is_empty()

    def test_only_full_key(self):
        """只有 'full' key 应返回空索引（full 被跳过）。"""
        index = self.builder.build({"full": "This is the full text of the paper."})
        assert index.is_empty()

    def test_no_cross_references(self):
        """无交叉引用的论文应正常构建（只有骨架）。"""
        index = self.builder.build(MINIMAL_PAPER)
        assert not index.is_empty()
        assert len(index.cross_references) == 0
        assert index.evidence_map == {}

    def test_get_evidence_chain_no_match(self):
        """不存在的 section 名应返回空列表。"""
        index = self.builder.build(EMPIRICAL_PAPER)
        chain = index.get_evidence_chain("nonexistent_section")
        assert chain == []

    def test_fig_abbreviation_match(self):
        """'Fig. 2a' 应被正确提取。"""
        index = self.builder.build(EMPIRICAL_PAPER)
        fig_ids = {r.target_id for r in index.cross_references if r.target_type == "figure"}
        assert "Figure 2a" in fig_ids
