"""
tests/test_d2_structured_export.py — D2: structured_export handler E2E 测试

覆盖:
    1. handler 直接执行（各参数组合）
    2. 参数校验（无效 format / group_by → 错误消息）
    3. 空状态处理（no findings / no edits）
    4. 分组正确性（priority / section / status）
    5. JSON 输出可解析
    6. Markdown 输出结构完整
    7. 覆盖率计算正确
    8. 统计信息正确
    9. Skill Markdown 中 tools YAML 块可解析
   10. registry.json 中 structured_export 条目正确
   11. SkillHandlerLoader 可成功加载 handler
   12. Harness 集成: action tool schema 出现在 agent tools 列表中
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# 项目根目录（v2/）— conftest.py 已确保在 sys.path 中
_project_root = Path(__file__).parent.parent

from core.skill_registry import SkillRegistry
from core.skill_handler_loader import SkillHandlerLoader
from skills.skill_handlers.structured_export import handle_export_review


# ==============================================================
# Fixtures: Mock WorkspaceState
# ==============================================================


@dataclass
class MockState:
    """模拟 WorkspaceState 的关键字段。"""

    paper_path: str = "/tmp/test_paper.pdf"
    findings: list = field(default_factory=list)
    edits: list = field(default_factory=list)
    sections_read: list = field(default_factory=list)
    paper_sections: dict = field(default_factory=dict)
    conversation_turns: int = 10
    loop_turns: int = 8
    total_tokens: int = 45000


@pytest.fixture
def rich_state() -> MockState:
    """包含多个 findings 和 edits 的丰富状态。"""
    return MockState(
        paper_path="/papers/economics_2024.pdf",
        findings=[
            {
                "finding": "Identification strategy lacks exogeneity argument",
                "priority": "major",
                "status": "open",
                "section": "methodology",
                "evidence": "Table 3 shows no first-stage F-stat",
                "recorded_at_turn": 3,
            },
            {
                "finding": "Abstract overstates causal claim",
                "priority": "critical",
                "status": "open",
                "section": "abstract",
                "evidence": "'proves' should be 'suggests'",
                "recorded_at_turn": 2,
            },
            {
                "finding": "Minor typo in equation 4",
                "priority": "minor",
                "status": "addressed",
                "section": "methodology",
                "evidence": "subscript i vs j",
                "recorded_at_turn": 5,
            },
            {
                "finding": "Literature review missing key reference",
                "priority": "moderate",
                "status": "open",
                "section": "introduction",
                "evidence": "Smith (2020) is not cited",
                "recorded_at_turn": 4,
            },
            {
                "finding": "Conclusion repeats abstract verbatim",
                "priority": "minor",
                "status": "open",
                "section": "conclusion",
                "evidence": "Paragraph 1 is identical",
                "recorded_at_turn": 7,
            },
        ],
        edits=[
            {
                "section": "abstract",
                "reason": "Replace 'proves' with 'suggests'",
                "content_preview": "Our findings suggest that fiscal policy...",
            },
            {
                "section": "methodology",
                "reason": "Fix subscript in equation 4",
                "content_preview": "y_{i,t} = \\beta x_{i,t} + ...",
            },
        ],
        sections_read=["abstract", "introduction", "methodology", "conclusion"],
        paper_sections={
            "abstract": "...",
            "introduction": "...",
            "methodology": "...",
            "results": "...",
            "conclusion": "...",
            "references": "...",
        },
        conversation_turns=12,
        loop_turns=10,
        total_tokens=68000,
    )


@pytest.fixture
def empty_state() -> MockState:
    """空状态：无 findings/edits。"""
    return MockState(
        paper_path=None,
        findings=[],
        edits=[],
        sections_read=[],
        paper_sections={},
        conversation_turns=0,
        loop_turns=0,
        total_tokens=0,
    )


# ==============================================================
# Test: 参数校验
# ==============================================================


class TestParameterValidation:
    """测试参数校验逻辑。"""

    def test_invalid_format(self, rich_state: MockState):
        """无效 format 返回错误消息。"""
        result = handle_export_review({"format": "pdf"}, rich_state)
        assert "[ERROR]" in result
        assert "pdf" in result

    def test_invalid_group_by(self, rich_state: MockState):
        """无效 group_by 返回错误消息。"""
        result = handle_export_review({"group_by": "author"}, rich_state)
        assert "[ERROR]" in result
        assert "author" in result

    def test_defaults_all_params(self, rich_state: MockState):
        """不传任何参数使用默认值（不报错）。"""
        result = handle_export_review({}, rich_state)
        assert "[ERROR]" not in result
        assert "Structured Review Report" in result


# ==============================================================
# Test: Markdown 输出
# ==============================================================


class TestMarkdownOutput:
    """测试 Markdown 格式输出。"""

    def test_contains_report_header(self, rich_state: MockState):
        """Markdown 输出包含报告标题。"""
        result = handle_export_review({"format": "markdown"}, rich_state)
        assert "# Structured Review Report" in result

    def test_contains_overview(self, rich_state: MockState):
        """包含 Overview 元信息。"""
        result = handle_export_review({"format": "markdown"}, rich_state)
        assert "## Overview" in result
        assert "economics_2024.pdf" in result
        assert "Findings**: 5" in result
        assert "Edits Applied**: 2" in result

    def test_group_by_priority(self, rich_state: MockState):
        """按 priority 分组时显示 CRITICAL/MAJOR/MODERATE/MINOR。"""
        result = handle_export_review(
            {"format": "markdown", "group_by": "priority"}, rich_state
        )
        assert "### CRITICAL" in result
        assert "### MAJOR" in result
        assert "### MODERATE" in result
        assert "### MINOR" in result

    def test_group_by_section(self, rich_state: MockState):
        """按 section 分组时显示 section 名称。"""
        result = handle_export_review(
            {"format": "markdown", "group_by": "section"}, rich_state
        )
        assert "### ABSTRACT" in result
        assert "### METHODOLOGY" in result
        assert "### INTRODUCTION" in result
        assert "### CONCLUSION" in result

    def test_group_by_status(self, rich_state: MockState):
        """按 status 分组时显示 OPEN/ADDRESSED。"""
        result = handle_export_review(
            {"format": "markdown", "group_by": "status"}, rich_state
        )
        assert "### OPEN" in result
        assert "### ADDRESSED" in result

    def test_edits_section(self, rich_state: MockState):
        """包含 Edits Applied 部分。"""
        result = handle_export_review({"format": "markdown"}, rich_state)
        assert "## Edits Applied" in result
        assert "abstract" in result
        assert "Replace 'proves' with 'suggests'" in result

    def test_coverage_section(self, rich_state: MockState):
        """包含 Coverage Analysis 部分。"""
        result = handle_export_review({"format": "markdown"}, rich_state)
        assert "## Coverage Analysis" in result
        # 4/6 sections read = 66.7%
        assert "66.7%" in result
        # results 和 references 未读
        assert "references" in result
        assert "results" in result

    def test_stats_section(self, rich_state: MockState):
        """include_stats=True 时包含统计信息。"""
        result = handle_export_review(
            {"format": "markdown", "include_stats": True}, rich_state
        )
        assert "## Session Statistics" in result
        assert "68,000" in result  # total_tokens formatted
        assert "12" in result  # conversation_turns

    def test_no_stats_when_disabled(self, rich_state: MockState):
        """include_stats=False 时不包含统计部分。"""
        result = handle_export_review(
            {"format": "markdown", "include_stats": False}, rich_state
        )
        assert "## Session Statistics" not in result


# ==============================================================
# Test: JSON 输出
# ==============================================================


class TestJsonOutput:
    """测试 JSON 格式输出。"""

    def test_valid_json(self, rich_state: MockState):
        """JSON 输出可被正确解析。"""
        result = handle_export_review({"format": "json"}, rich_state)
        parsed = json.loads(result)
        assert isinstance(parsed, dict)

    def test_json_structure(self, rich_state: MockState):
        """JSON 包含预期的顶层字段。"""
        result = handle_export_review({"format": "json"}, rich_state)
        parsed = json.loads(result)
        assert "meta" in parsed
        assert "grouped_findings" in parsed
        assert "group_by" in parsed
        assert "edits" in parsed
        assert "coverage" in parsed

    def test_json_meta_fields(self, rich_state: MockState):
        """JSON meta 字段正确。"""
        result = handle_export_review({"format": "json"}, rich_state)
        parsed = json.loads(result)
        meta = parsed["meta"]
        assert meta["total_findings"] == 5
        assert meta["total_edits"] == 2
        assert meta["sections_covered"] == 4
        assert meta["total_sections"] == 6

    def test_json_group_by_priority(self, rich_state: MockState):
        """JSON 按 priority 分组正确。"""
        result = handle_export_review(
            {"format": "json", "group_by": "priority"}, rich_state
        )
        parsed = json.loads(result)
        grouped = parsed["grouped_findings"]
        assert "critical" in grouped
        assert "major" in grouped
        assert len(grouped["critical"]) == 1
        assert len(grouped["major"]) == 1
        assert len(grouped["minor"]) == 2

    def test_json_stats_present(self, rich_state: MockState):
        """include_stats=True 时 JSON 包含 stats 字段。"""
        result = handle_export_review(
            {"format": "json", "include_stats": True}, rich_state
        )
        parsed = json.loads(result)
        assert parsed["stats"] is not None
        assert parsed["stats"]["total_tokens"] == 68000
        assert parsed["stats"]["priority_distribution"]["critical"] == 1

    def test_json_stats_null_when_disabled(self, rich_state: MockState):
        """include_stats=False 时 JSON 中 stats 为 null。"""
        result = handle_export_review(
            {"format": "json", "include_stats": False}, rich_state
        )
        parsed = json.loads(result)
        assert parsed["stats"] is None


# ==============================================================
# Test: 空状态 / 边界情况
# ==============================================================


class TestEdgeCases:
    """测试空状态和边界情况。"""

    def test_empty_state_markdown(self, empty_state: MockState):
        """空状态不报错，生成有效 Markdown。"""
        result = handle_export_review({"format": "markdown"}, empty_state)
        assert "# Structured Review Report" in result
        assert "Findings**: 0" in result
        assert "Edits Applied**: 0" in result

    def test_empty_state_json(self, empty_state: MockState):
        """空状态生成有效 JSON。"""
        result = handle_export_review({"format": "json"}, empty_state)
        parsed = json.loads(result)
        assert parsed["meta"]["total_findings"] == 0
        assert len(parsed["edits"]) == 0
        assert parsed["coverage"]["percentage"] == 0.0

    def test_finding_without_priority(self, empty_state: MockState):
        """finding 缺少 priority 字段不会报错。"""
        empty_state.findings = [{"finding": "test", "section": "intro"}]
        result = handle_export_review(
            {"format": "json", "group_by": "priority"}, empty_state
        )
        parsed = json.loads(result)
        assert "unspecified" in parsed["grouped_findings"]

    def test_finding_without_section(self, empty_state: MockState):
        """finding 缺少 section 字段不会报错。"""
        empty_state.findings = [{"finding": "test", "priority": "major"}]
        result = handle_export_review(
            {"format": "json", "group_by": "section"}, empty_state
        )
        parsed = json.loads(result)
        assert "unspecified" in parsed["grouped_findings"]

    def test_state_missing_attribute(self):
        """state 对象缺少某些属性时 graceful 处理。"""
        # 用简单的 object 模拟缺属性的 state
        bare_state = type("BareState", (), {})()
        result = handle_export_review({"format": "json"}, bare_state)
        parsed = json.loads(result)
        assert parsed["meta"]["total_findings"] == 0

    def test_coverage_with_extra_reads(self):
        """sections_read 包含 paper_sections 中不存在的 section。"""
        state = MockState(
            sections_read=["abstract", "appendix_a"],  # appendix_a 不在 paper_sections
            paper_sections={"abstract": "...", "introduction": "..."},
        )
        result = handle_export_review({"format": "json"}, state)
        parsed = json.loads(result)
        # 只有 abstract 算覆盖（交集）
        assert parsed["coverage"]["percentage"] == 50.0
        assert "introduction" in parsed["coverage"]["unread"]


# ==============================================================
# Test: Skill 注册集成
# ==============================================================


class TestSkillRegistryIntegration:
    """测试 Skill 在 registry/loader 层面的集成。"""

    def test_registry_json_has_structured_export(self):
        """registry.json 中包含 structured_export 条目。"""
        registry_path = _project_root / "skills" / "registry.json"
        raw = json.loads(registry_path.read_text(encoding="utf-8"))
        skill_ids = [s["id"] for s in raw["skills"]]
        assert "structured_export" in skill_ids

    def test_registry_entry_is_action_type(self):
        """structured_export 条目 type='action'。"""
        registry_path = _project_root / "skills" / "registry.json"
        raw = json.loads(registry_path.read_text(encoding="utf-8"))
        entry = next(s for s in raw["skills"] if s["id"] == "structured_export")
        assert entry["type"] == "action"
        assert len(entry["tools"]) == 1
        assert entry["tools"][0]["name"] == "export_structured_review"

    def test_skill_markdown_tools_block_parseable(self):
        """structured_export.md 中的 <!-- tools --> 块可被 SkillRegistry 解析。"""
        skills_dir = _project_root / "skills"
        registry = SkillRegistry(skills_dir)
        tools = registry.load_tools_from_markdown("structured_export")
        assert len(tools) == 1
        assert tools[0].name == "export_structured_review"
        assert tools[0].handler == "skill_handlers/structured_export.py::handle_export_review"
        assert "format" in tools[0].input_schema.get("properties", {})

    def test_handler_loader_can_load(self):
        """SkillHandlerLoader 可以成功加载 structured_export handler。"""
        skills_dir = _project_root / "skills"
        loader = SkillHandlerLoader(skills_dir)
        fn = loader.load("skill_handlers/structured_export.py::handle_export_review")
        assert fn is not None
        assert callable(fn)

        # 验证实际执行
        mock_state = MockState(findings=[{"finding": "test", "priority": "major", "status": "open", "section": "intro"}])
        result = fn({"format": "json"}, mock_state)
        parsed = json.loads(result)
        assert parsed["meta"]["total_findings"] == 1

    def test_get_action_skills_includes_structured_export(self):
        """SkillRegistry.get_action_skills() 包含 structured_export。"""
        skills_dir = _project_root / "skills"
        registry = SkillRegistry(skills_dir)
        action_skills = registry.get_action_skills()
        ids = [s.id for s in action_skills]
        assert "structured_export" in ids

    def test_action_skill_tool_schema(self):
        """Action skill 的 ToolDef.to_api_schema() 生成正确的 API schema。"""
        skills_dir = _project_root / "skills"
        registry = SkillRegistry(skills_dir)
        meta = registry.get("structured_export")
        assert meta is not None
        assert len(meta.tools) == 1

        schema = meta.tools[0].to_api_schema()
        assert schema["name"] == "export_structured_review"
        assert "description" in schema
        assert schema["input_schema"]["type"] == "object"
        assert "format" in schema["input_schema"]["properties"]
        assert "group_by" in schema["input_schema"]["properties"]
        assert "include_stats" in schema["input_schema"]["properties"]


# ==============================================================
# Test: Harness 集成（mock 级别）
# ==============================================================


class TestHarnessIntegration:
    """测试 Harness 级别的集成（验证 action tool 注册流程）。"""

    def test_harness_registers_action_tool(self):
        """Harness._register_action_skill_tools() 成功注册 export_structured_review。"""
        skills_dir = _project_root / "skills"
        registry = SkillRegistry(skills_dir)
        loader = SkillHandlerLoader(skills_dir)

        # 模拟 Harness 的注册逻辑
        action_skills = registry.get_action_skills()
        registered_tools = {}

        for skill in action_skills:
            for tool_def in skill.tools:
                fn = loader.load(tool_def.handler)
                if fn is not None:
                    registered_tools[tool_def.name] = {
                        "handler": fn,
                        "schema": tool_def.to_api_schema(),
                    }

        # 验证 export_structured_review 被注册
        assert "export_structured_review" in registered_tools
        entry = registered_tools["export_structured_review"]
        assert callable(entry["handler"])
        assert entry["schema"]["name"] == "export_structured_review"

        # 验证 handler 可以执行
        state = MockState(findings=[{"finding": "test", "priority": "critical", "status": "open"}])
        result = entry["handler"]({"format": "markdown"}, state)
        assert "Structured Review Report" in result
        assert "CRITICAL" in result
