"""
EDIT-3: 增量编辑工具测试（edit_paragraph, reword_sentence, insert_content）。

测试覆盖:
  1. edit_paragraph: 正常替换、索引越界、section 不存在
  2. reword_sentence: 精确匹配、未找到、多处匹配、首尾空格容忍
  3. insert_content: 正常插入、末尾追加、position 越界
  4. 共用验证: 每个编辑都触发 post_edit_verify + 记录到 state.edits
  5. Phase gating: 三个工具仅 editing 阶段可用
  6. edit_section 重构后仍然正常工作
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.harness import Harness


def _make_harness_with_paper() -> Harness:
    """创建一个带论文内容的 Harness，用于编辑测试。"""
    h = Harness()
    h.state.paper_sections = {
        "Abstract": (
            "This paper studies the effect of minimum wage on employment.\n\n"
            "We use a difference-in-differences design with county-level data.\n\n"
            "Our results show a significant negative effect on teen employment."
        ),
        "Section 3: Data": (
            "We collect data from the BLS Quarterly Census.\n\n"
            "The sample covers 2010-2020 with N=1000 county-year observations.\n\n"
            "Table 1 presents summary statistics."
        ),
        "Section 5: Conclusion": (
            "In conclusion, our findings suggest that minimum wage increases "
            "reduce teen employment by 2-3 percent.\n\n"
            "Future research should explore heterogeneous effects across industries."
        ),
    }
    return h


# ============================================================
# edit_paragraph 测试
# ============================================================

class TestEditParagraph:
    def test_basic_replacement(self):
        """正常段落替换。"""
        h = _make_harness_with_paper()
        result = h.execute_tool("edit_paragraph", {
            "section": "Abstract",
            "paragraph_index": 2,
            "new_content": "Our results show a modest but statistically insignificant effect on teen employment.",
            "reason": "修正 overclaim",
        })
        assert "已替换" in result
        assert "第 2 段" in result
        # 验证 state 中内容已更新
        paragraphs = h.state.paper_sections["Abstract"].split("\n\n")
        assert "modest but statistically insignificant" in paragraphs[2]
        # 验证编辑记录
        assert len(h.state.edits) == 1
        assert h.state.edits[0]["section"] == "Abstract"

    def test_index_out_of_range(self):
        """段落索引越界。"""
        h = _make_harness_with_paper()
        result = h.execute_tool("edit_paragraph", {
            "section": "Abstract",
            "paragraph_index": 99,
            "new_content": "新段落",
            "reason": "测试",
        })
        assert "失败" in result
        assert "99" in result
        # state 应该没变
        assert "minimum wage" in h.state.paper_sections["Abstract"]

    def test_section_not_found(self):
        """section 名不存在。"""
        h = _make_harness_with_paper()
        result = h.execute_tool("edit_paragraph", {
            "section": "Nonexistent",
            "paragraph_index": 0,
            "new_content": "test",
            "reason": "test",
        })
        assert "失败" in result
        assert "未找到" in result

    def test_first_paragraph(self):
        """替换第一段。"""
        h = _make_harness_with_paper()
        result = h.execute_tool("edit_paragraph", {
            "section": "Section 3",
            "paragraph_index": 0,
            "new_content": "Data are sourced from the CPS monthly files.",
            "reason": "更换数据来源描述",
        })
        assert "已替换" in result
        assert h.state.paper_sections["Section 3: Data"].startswith("Data are sourced from the CPS")


# ============================================================
# reword_sentence 测试
# ============================================================

class TestRewordSentence:
    def test_basic_replacement(self):
        """正常句子替换。"""
        h = _make_harness_with_paper()
        result = h.execute_tool("reword_sentence", {
            "section": "Abstract",
            "sentence_match": "Our results show a significant negative effect on teen employment.",
            "new_sentence": "Our estimates indicate a moderate negative effect on teen employment.",
            "reason": "弱化表述",
        })
        assert "已替换" in result
        assert "moderate negative effect" in h.state.paper_sections["Abstract"]
        assert "significant negative effect" not in h.state.paper_sections["Abstract"]

    def test_not_found(self):
        """精确匹配失败。"""
        h = _make_harness_with_paper()
        result = h.execute_tool("reword_sentence", {
            "section": "Abstract",
            "sentence_match": "This sentence does not exist in the paper.",
            "new_sentence": "replacement",
            "reason": "test",
        })
        assert "失败" in result
        assert "未找到" in result

    def test_multiple_matches(self):
        """多处匹配拒绝。"""
        h = Harness()
        # 构造一个有重复句子的 section
        h.state.paper_sections = {
            "Test Section": "The result is robust. Some text here. The result is robust."
        }
        result = h.execute_tool("reword_sentence", {
            "section": "Test Section",
            "sentence_match": "The result is robust.",
            "new_sentence": "The finding is reliable.",
            "reason": "test",
        })
        assert "失败" in result
        assert "2 处" in result

    def test_whitespace_tolerance(self):
        """首尾空格容忍。"""
        h = _make_harness_with_paper()
        # 带首尾空格的 sentence_match 应该能匹配
        result = h.execute_tool("reword_sentence", {
            "section": "Section 3",
            "sentence_match": "  Table 1 presents summary statistics.  ",
            "new_sentence": "Table 1 reports descriptive statistics.",
            "reason": "措辞调整",
        })
        assert "已替换" in result
        assert "descriptive statistics" in h.state.paper_sections["Section 3: Data"]

    def test_empty_params(self):
        """空参数校验。"""
        h = _make_harness_with_paper()
        result = h.execute_tool("reword_sentence", {
            "section": "Abstract",
            "sentence_match": "",
            "new_sentence": "something",
            "reason": "test",
        })
        assert "失败" in result


# ============================================================
# insert_content 测试
# ============================================================

class TestInsertContent:
    def test_insert_at_beginning(self):
        """在开头插入。"""
        h = _make_harness_with_paper()
        result = h.execute_tool("insert_content", {
            "section": "Section 5",
            "position": 0,
            "content": "This section summarizes our key contributions.",
            "reason": "补充开头概述",
        })
        assert "已在" in result
        paragraphs = h.state.paper_sections["Section 5: Conclusion"].split("\n\n")
        assert paragraphs[0] == "This section summarizes our key contributions."
        assert len(paragraphs) == 3  # 原来 2 段 + 新插入 1 段

    def test_append_at_end(self):
        """在末尾追加。"""
        h = _make_harness_with_paper()
        # Section 5 有 2 段，position=2 表示末尾追加
        result = h.execute_tool("insert_content", {
            "section": "Section 5",
            "position": 2,
            "content": "We also acknowledge several limitations of our study.",
            "reason": "补充局限性讨论",
        })
        assert "已在" in result
        paragraphs = h.state.paper_sections["Section 5: Conclusion"].split("\n\n")
        assert len(paragraphs) == 3
        assert "limitations" in paragraphs[2]

    def test_position_out_of_range(self):
        """position 越界。"""
        h = _make_harness_with_paper()
        result = h.execute_tool("insert_content", {
            "section": "Section 5",
            "position": 99,
            "content": "test",
            "reason": "test",
        })
        assert "失败" in result
        assert "99" in result

    def test_insert_in_middle(self):
        """在中间插入。"""
        h = _make_harness_with_paper()
        result = h.execute_tool("insert_content", {
            "section": "Section 3",
            "position": 1,
            "content": "We apply standard cleaning procedures to handle missing values.",
            "reason": "补充数据清洗说明",
        })
        assert "已在" in result
        paragraphs = h.state.paper_sections["Section 3: Data"].split("\n\n")
        assert len(paragraphs) == 4  # 原 3 段 + 1 新段
        assert "cleaning procedures" in paragraphs[1]
        # 原来的第二段现在是第三段
        assert "N=1000" in paragraphs[2]


# ============================================================
# 验证 + 编辑记录
# ============================================================

class TestEditVerification:
    def test_edits_recorded(self):
        """所有编辑工具都记录到 state.edits。"""
        h = _make_harness_with_paper()

        h.execute_tool("edit_paragraph", {
            "section": "Abstract", "paragraph_index": 0,
            "new_content": "New first paragraph.", "reason": "r1",
        })
        h.execute_tool("reword_sentence", {
            "section": "Section 3",
            "sentence_match": "Table 1 presents summary statistics.",
            "new_sentence": "Table 1 shows summary stats.",
            "reason": "r2",
        })
        h.execute_tool("insert_content", {
            "section": "Section 5", "position": 0,
            "content": "Inserted paragraph.", "reason": "r3",
        })

        assert len(h.state.edits) == 3
        assert h.state.edits[0]["reason"] == "r1"
        assert h.state.edits[1]["reason"] == "r2"
        assert h.state.edits[2]["reason"] == "r3"

    def test_verification_runs(self):
        """编辑后返回中应包含验证反馈。"""
        h = _make_harness_with_paper()
        result = h.execute_tool("edit_paragraph", {
            "section": "Abstract", "paragraph_index": 0,
            "new_content": "Revised opening paragraph.", "reason": "test",
        })
        # verify_edit 和 format_verification_feedback 会产出包含 section 名的反馈
        # 具体反馈内容取决于 post_edit_verify 实现，这里只确认函数被调用了
        assert "Abstract" in result


# ============================================================
# Phase gating
# ============================================================

class TestPhaseGating:
    def test_editing_tools_only_in_editing_phase(self):
        """三个增量编辑工具只在 editing 阶段可见。"""
        h = Harness()
        editing_tools = h.tool_registry.get_tools_for_phase("editing")
        assert "edit_paragraph" in editing_tools
        assert "reword_sentence" in editing_tools
        assert "insert_content" in editing_tools

        # 不应在其他阶段可见
        for phase in ("initial_scan", "deep_review", "synthesis"):
            tools = h.tool_registry.get_tools_for_phase(phase)
            assert "edit_paragraph" not in tools
            assert "reword_sentence" not in tools
            assert "insert_content" not in tools


# ============================================================
# edit_section 重构验证
# ============================================================

class TestEditSectionRefactored:
    def test_basic_full_replacement(self):
        """重构后的 edit_section 仍然正常工作。"""
        h = _make_harness_with_paper()
        result = h.execute_tool("edit_section", {
            "section": "Abstract",
            "new_content": "Completely new abstract content.",
            "reason": "全面重写",
        })
        assert "已修改" in result
        assert h.state.paper_sections["Abstract"] == "Completely new abstract content."
        assert len(h.state.edits) == 1

    def test_section_not_found(self):
        """section 不存在时返回错误，不记录无效编辑。"""
        h = _make_harness_with_paper()
        result = h.execute_tool("edit_section", {
            "section": "Nonexistent",
            "new_content": "test",
            "reason": "test",
        })
        assert "未找到" in result
        # section 不存在时不应记录编辑（避免无效 edit 污染记录）
        assert len(h.state.edits) == 0
