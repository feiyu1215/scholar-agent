"""
tests/test_v4_action_skill_tools.py — D1: 操作型 Skill 动态 Tool 注册测试

覆盖 V4 D1 验收标准:
    1. action skill 的 tool 被正确注册到 ToolRegistry（可执行）
    2. handler 签名 (args, state) 通过 wrapper 正确桥接为 (args)
    3. handler 加载失败时 graceful 降级（跳过、不中断）
    4. get_action_tool_schemas() 返回正确的 API schema 列表
    5. Agent 侧 tools 列表正确包含 action skill schemas
    6. SkillHandlerLoader 安全约束生效（路径遍历拒绝）
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.skill_registry import SkillRegistry, SkillMeta, ToolDef
from core.skill_handler_loader import SkillHandlerLoader


# ==============================================================
# Fixtures
# ==============================================================


@pytest.fixture
def action_skills_dir(tmp_path: Path) -> Path:
    """创建包含 action skill 的临时 skills 目录。"""
    # 创建 skill_handlers 目录和 handler 文件
    handlers_dir = tmp_path / "skill_handlers"
    handlers_dir.mkdir()
    (handlers_dir / "__init__.py").write_text("")

    # 写入一个简单的 handler 模块
    handler_code = '''
def handle_export(args: dict, state) -> str:
    """导出评审报告。"""
    fmt = args.get("format", "markdown")
    return f"exported in {fmt} format"

def handle_summarize(args: dict, state) -> str:
    """总结发现。"""
    return f"summary of {len(state.findings) if hasattr(state, 'findings') else 0} findings"
'''
    (handlers_dir / "export_review.py").write_text(handler_code)

    # 写入带语法错误的 handler 模块（测试 graceful 降级）
    bad_handler_code = """
def this is not valid python code!!!
"""
    (handlers_dir / "broken_handler.py").write_text(bad_handler_code)

    # 创建 registry.json
    registry_data = {
        "version": "1.0",
        "skills": [
            {
                "id": "export_tools",
                "type": "action",
                "file": "export_tools.md",
                "name": "导出工具集",
                "description": "导出评审报告的操作型 Skill",
                "tags": ["export"],
                "applicable_paper_types": [],
                "applicable_phases": [],
                "token_estimate": 0,
                "priority_hint": 80,
                "tools": [
                    {
                        "name": "export_review_report",
                        "description": "导出评审报告",
                        "input_schema": {
                            "type": "object",
                            "properties": {
                                "format": {
                                    "type": "string",
                                    "enum": ["markdown", "json", "pdf"],
                                    "description": "输出格式"
                                }
                            },
                            "required": ["format"]
                        },
                        "handler": "skill_handlers/export_review.py::handle_export"
                    },
                    {
                        "name": "summarize_findings",
                        "description": "总结审阅发现",
                        "input_schema": {
                            "type": "object",
                            "properties": {},
                        },
                        "handler": "skill_handlers/export_review.py::handle_summarize"
                    }
                ]
            },
            {
                "id": "broken_skill",
                "type": "action",
                "file": "broken.md",
                "name": "损坏的 Skill",
                "description": "handler 加载会失败的 Skill",
                "tags": [],
                "applicable_paper_types": [],
                "applicable_phases": [],
                "token_estimate": 0,
                "priority_hint": 50,
                "tools": [
                    {
                        "name": "broken_tool",
                        "description": "这个 tool 的 handler 加载会失败",
                        "input_schema": {"type": "object", "properties": {}},
                        "handler": "skill_handlers/broken_handler.py::nonexistent_fn"
                    }
                ]
            },
            {
                "id": "knowledge_only",
                "type": "knowledge",
                "file": "knowledge.md",
                "name": "知识型 Skill",
                "description": "无 tools 的知识型 Skill",
                "tags": ["test"],
                "applicable_paper_types": [],
                "applicable_phases": [],
                "token_estimate": 200,
                "priority_hint": 60,
            },
        ],
    }
    (tmp_path / "registry.json").write_text(json.dumps(registry_data, ensure_ascii=False))

    # 创建 skill markdown 文件（内容不重要）
    (tmp_path / "export_tools.md").write_text("# Export Tools\n")
    (tmp_path / "broken.md").write_text("# Broken\n")
    (tmp_path / "knowledge.md").write_text("# Knowledge\n")

    return tmp_path


@pytest.fixture
def loader(action_skills_dir: Path) -> SkillHandlerLoader:
    """创建 SkillHandlerLoader 实例。"""
    return SkillHandlerLoader(action_skills_dir)


@pytest.fixture
def registry(action_skills_dir: Path) -> SkillRegistry:
    """创建 SkillRegistry 实例。"""
    return SkillRegistry(action_skills_dir)


# ==============================================================
# Test: SkillHandlerLoader 基本加载
# ==============================================================


class TestSkillHandlerLoader:
    """测试 SkillHandlerLoader 的加载功能。"""

    def test_load_valid_handler(self, loader: SkillHandlerLoader):
        """成功加载合法的 handler 函数。"""
        fn = loader.load("skill_handlers/export_review.py::handle_export")
        assert fn is not None
        assert callable(fn)
        # 验证签名: (args, state) -> str
        result = fn({"format": "json"}, None)
        assert result == "exported in json format"

    def test_load_second_handler_same_module(self, loader: SkillHandlerLoader):
        """同一模块中加载第二个函数。"""
        fn = loader.load("skill_handlers/export_review.py::handle_summarize")
        assert fn is not None

        # 用 mock state 测试
        mock_state = MagicMock()
        mock_state.findings = ["a", "b", "c"]
        result = fn({}, mock_state)
        assert "3 findings" in result

    def test_load_nonexistent_function(self, loader: SkillHandlerLoader):
        """模块存在但函数不存在 → 返回 None。"""
        fn = loader.load("skill_handlers/export_review.py::nonexistent_function")
        assert fn is None

    def test_load_nonexistent_module(self, loader: SkillHandlerLoader):
        """模块文件不存在 → 返回 None。"""
        fn = loader.load("skill_handlers/does_not_exist.py::some_fn")
        assert fn is None

    def test_load_broken_module(self, loader: SkillHandlerLoader):
        """模块有语法错误 → 返回 None（不 raise）。"""
        fn = loader.load("skill_handlers/broken_handler.py::nonexistent_fn")
        assert fn is None

    def test_security_reject_path_traversal(self, loader: SkillHandlerLoader):
        """路径遍历攻击 → 拒绝加载。"""
        fn = loader.load("skill_handlers/../../../etc/passwd::read")
        assert fn is None

    def test_security_reject_non_skill_handlers_prefix(self, loader: SkillHandlerLoader):
        """非 skill_handlers/ 前缀 → 拒绝加载。"""
        fn = loader.load("core/tools.py::ToolRegistry")
        assert fn is None

    def test_missing_double_colon(self, loader: SkillHandlerLoader):
        """缺少 :: 分隔符 → 拒绝加载。"""
        fn = loader.load("skill_handlers/export_review.py")
        assert fn is None

    def test_cache_hit(self, loader: SkillHandlerLoader):
        """多次加载同一 handler 应命中缓存，返回同一函数对象。"""
        fn1 = loader.load("skill_handlers/export_review.py::handle_export")
        fn2 = loader.load("skill_handlers/export_review.py::handle_export")
        assert fn1 is fn2


# ==============================================================
# Test: SkillRegistry.get_action_skills
# ==============================================================


class TestGetActionSkills:
    """测试 SkillRegistry.get_action_skills() 方法。"""

    def test_returns_only_action_type(self, registry: SkillRegistry):
        """只返回 type='action' 的 skills。"""
        action_skills = registry.get_action_skills()
        for skill in action_skills:
            assert skill.type == "action"
        # knowledge_only 不应出现
        ids = {s.id for s in action_skills}
        assert "knowledge_only" not in ids

    def test_returns_skills_with_tools(self, registry: SkillRegistry):
        """返回的 action skills 必须有 tools 定义。"""
        action_skills = registry.get_action_skills()
        for skill in action_skills:
            assert len(skill.tools) > 0

    def test_includes_export_tools(self, registry: SkillRegistry):
        """export_tools skill 应在结果中。"""
        action_skills = registry.get_action_skills()
        ids = {s.id for s in action_skills}
        assert "export_tools" in ids

    def test_sorted_by_priority(self, registry: SkillRegistry):
        """结果按 priority_hint 降序。"""
        action_skills = registry.get_action_skills()
        if len(action_skills) >= 2:
            for i in range(len(action_skills) - 1):
                assert action_skills[i].priority_hint >= action_skills[i + 1].priority_hint


# ==============================================================
# Test: Harness 动态注册（集成测试）
# ==============================================================


class TestHarnessActionToolRegistration:
    """测试 Harness._register_action_skill_tools() 集成行为。"""

    @pytest.fixture
    def mock_harness(self, action_skills_dir: Path):
        """构造一个最小化的 mock Harness 来测试 _register_action_skill_tools。"""
        from core.tools import ToolRegistry
        from core.skill_handler_loader import SkillHandlerLoader
        from core.skill_registry import SkillRegistry

        class MinimalHarness:
            def __init__(self):
                self.state = MagicMock()
                self.state.findings = ["finding1", "finding2"]
                self.tool_registry = ToolRegistry()
                self.skill_registry = SkillRegistry(action_skills_dir)
                self._skill_handler_loader = SkillHandlerLoader(action_skills_dir)
                self._action_tool_schemas: list[dict] = []

            # 绑定真实方法
            _register_action_skill_tools = None
            get_action_tool_schemas = None

        # 从 Harness 类借用方法
        from core.harness import Harness
        harness = MinimalHarness()
        harness._register_action_skill_tools = Harness._register_action_skill_tools.__get__(harness)
        harness.get_action_tool_schemas = Harness.get_action_tool_schemas.__get__(harness)
        return harness

    def test_tools_registered_to_tool_registry(self, mock_harness):
        """action skill tools 被成功注册到 ToolRegistry。"""
        mock_harness._register_action_skill_tools()

        # export_review_report 应该已注册
        result = mock_harness.tool_registry.execute("export_review_report", {"format": "markdown"})
        assert "exported in markdown format" in result

    def test_handler_receives_state(self, mock_harness):
        """handler 通过 wrapper 正确接收 self.state。"""
        mock_harness._register_action_skill_tools()

        # summarize_findings handler 应该能访问 state.findings
        result = mock_harness.tool_registry.execute("summarize_findings", {})
        assert "2 findings" in result

    def test_get_action_tool_schemas_returns_correct_format(self, mock_harness):
        """get_action_tool_schemas() 返回 LLM API 兼容的 schema 列表。"""
        mock_harness._register_action_skill_tools()

        schemas = mock_harness.get_action_tool_schemas()
        assert len(schemas) >= 2  # export_review_report + summarize_findings

        # 验证 schema 格式
        for schema in schemas:
            assert "name" in schema
            assert "description" in schema
            assert "input_schema" in schema

        # 验证具体 tool
        names = {s["name"] for s in schemas}
        assert "export_review_report" in names
        assert "summarize_findings" in names

    def test_broken_handler_graceful_skip(self, mock_harness):
        """handler 加载失败的 tool 被跳过，不影响其他 tool 注册。"""
        mock_harness._register_action_skill_tools()

        # broken_tool 不应被注册
        result = mock_harness.tool_registry.execute("broken_tool", {})
        assert "未知工具" in result or "unknown" in result.lower()

        # 但有效的 tool 应该正常工作
        result = mock_harness.tool_registry.execute("export_review_report", {"format": "json"})
        assert "exported in json format" in result

    def test_no_skill_registry_noop(self):
        """skill_registry 为 None 时，_register_action_skill_tools 静默返回。"""
        from core.tools import ToolRegistry
        from core.harness import Harness

        class NoRegHarness:
            def __init__(self):
                self.state = MagicMock()
                self.tool_registry = ToolRegistry()
                self.skill_registry = None
                self._skill_handler_loader = None
                self._action_tool_schemas: list[dict] = []

        harness = NoRegHarness()
        harness._register_action_skill_tools = Harness._register_action_skill_tools.__get__(harness)
        harness.get_action_tool_schemas = Harness.get_action_tool_schemas.__get__(harness)

        # 不应 raise
        harness._register_action_skill_tools()
        assert harness.get_action_tool_schemas() == []

    def test_schemas_are_defensive_copy(self, mock_harness):
        """get_action_tool_schemas() 返回的是副本，修改不影响内部状态。"""
        mock_harness._register_action_skill_tools()

        schemas1 = mock_harness.get_action_tool_schemas()
        schemas1.append({"name": "injected", "description": "bad", "input_schema": {}})

        schemas2 = mock_harness.get_action_tool_schemas()
        names = {s["name"] for s in schemas2}
        assert "injected" not in names


# ==============================================================
# Test: I2 Fix — 阶段约束传递
# ==============================================================


class TestPhaseConstraintPropagation:
    """测试 action skill 的 applicable_phases 正确传递给 ToolRegistry。"""

    @pytest.fixture
    def phased_skills_dir(self, tmp_path: Path) -> Path:
        """创建带阶段约束的 action skill 目录。"""
        handlers_dir = tmp_path / "skill_handlers"
        handlers_dir.mkdir()
        (handlers_dir / "__init__.py").write_text("")
        (handlers_dir / "phased.py").write_text(
            'def handle(args: dict, state) -> str:\n    return "phased result"\n'
        )

        registry_data = {
            "version": "1.0",
            "skills": [
                {
                    "id": "editing_only_skill",
                    "type": "action",
                    "file": "editing_only.md",
                    "name": "编辑专用 Skill",
                    "description": "只在编辑阶段可用",
                    "tags": [],
                    "applicable_paper_types": [],
                    "applicable_phases": ["editing"],
                    "token_estimate": 0,
                    "priority_hint": 70,
                    "tools": [
                        {
                            "name": "editing_tool",
                            "description": "编辑工具",
                            "input_schema": {"type": "object", "properties": {}},
                            "handler": "skill_handlers/phased.py::handle"
                        }
                    ]
                },
                {
                    "id": "all_phase_skill",
                    "type": "action",
                    "file": "all_phase.md",
                    "name": "全阶段 Skill",
                    "description": "无阶段限制",
                    "tags": [],
                    "applicable_paper_types": [],
                    "applicable_phases": [],
                    "token_estimate": 0,
                    "priority_hint": 60,
                    "tools": [
                        {
                            "name": "universal_tool",
                            "description": "通用工具",
                            "input_schema": {"type": "object", "properties": {}},
                            "handler": "skill_handlers/phased.py::handle"
                        }
                    ]
                },
            ],
        }
        (tmp_path / "registry.json").write_text(json.dumps(registry_data, ensure_ascii=False))
        (tmp_path / "editing_only.md").write_text("# Editing Only\n")
        (tmp_path / "all_phase.md").write_text("# All Phase\n")
        return tmp_path

    def test_phase_constrained_tool_only_in_declared_phases(self, phased_skills_dir: Path):
        """声明了 applicable_phases 的 skill 的 tool 只在该阶段可用。"""
        from core.tools import ToolRegistry
        from core.skill_handler_loader import SkillHandlerLoader
        from core.skill_registry import SkillRegistry
        from core.harness import Harness

        class PhasedHarness:
            def __init__(self):
                self.state = MagicMock()
                self.tool_registry = ToolRegistry()
                self.skill_registry = SkillRegistry(phased_skills_dir)
                self._skill_handler_loader = SkillHandlerLoader(phased_skills_dir)
                self._action_tool_schemas: list[dict] = []

        harness = PhasedHarness()
        harness._register_action_skill_tools = Harness._register_action_skill_tools.__get__(harness)  # type: ignore[attr-defined]
        harness._register_action_skill_tools()  # type: ignore[attr-defined]

        # editing_tool 只在 editing 阶段可用
        editing_tools = harness.tool_registry.get_tools_for_phase("editing")
        assert "editing_tool" in editing_tools

        initial_tools = harness.tool_registry.get_tools_for_phase("initial_scan")
        assert "editing_tool" not in initial_tools

    def test_no_phase_constraint_means_all_phases(self, phased_skills_dir: Path):
        """没有声明 applicable_phases 的 skill 的 tool 在所有阶段可用。"""
        from core.tools import ToolRegistry
        from core.skill_handler_loader import SkillHandlerLoader
        from core.skill_registry import SkillRegistry
        from core.harness import Harness

        class PhasedHarness:
            def __init__(self):
                self.state = MagicMock()
                self.tool_registry = ToolRegistry()
                self.skill_registry = SkillRegistry(phased_skills_dir)
                self._skill_handler_loader = SkillHandlerLoader(phased_skills_dir)
                self._action_tool_schemas: list[dict] = []

        harness = PhasedHarness()
        harness._register_action_skill_tools = Harness._register_action_skill_tools.__get__(harness)  # type: ignore[attr-defined]
        harness._register_action_skill_tools()  # type: ignore[attr-defined]

        # universal_tool 在所有阶段可用
        for phase in ["initial_scan", "deep_review", "editing", "synthesis"]:
            tools = harness.tool_registry.get_tools_for_phase(phase)
            assert "universal_tool" in tools, f"universal_tool should be in {phase}"


# ==============================================================
# Test: I3 Fix — 名称冲突保护
# ==============================================================


class TestNameConflictProtection:
    """测试 action skill tool 不会覆盖内置工具。"""

    def test_conflicting_tool_name_skipped(self, action_skills_dir: Path):
        """与内置工具同名的 action tool 被跳过。"""
        from core.tools import ToolRegistry
        from core.skill_handler_loader import SkillHandlerLoader
        from core.skill_registry import SkillRegistry
        from core.harness import Harness

        class ConflictHarness:
            def __init__(self):
                self.state = MagicMock()
                self.tool_registry = ToolRegistry()
                self.skill_registry = SkillRegistry(action_skills_dir)
                self._skill_handler_loader = SkillHandlerLoader(action_skills_dir)
                self._action_tool_schemas: list[dict] = []

        harness = ConflictHarness()
        harness._register_action_skill_tools = Harness._register_action_skill_tools.__get__(harness)  # type: ignore[attr-defined]

        # 预先注册一个与 action skill 中 tool 同名的内置工具
        harness.tool_registry.register(
            "export_review_report",
            handler=lambda args: "BUILTIN RESULT",
            phases=None,
        )

        harness._register_action_skill_tools()  # type: ignore[attr-defined]

        # 内置工具不应被覆盖
        result = harness.tool_registry.execute("export_review_report", {"format": "json"})
        assert result == "BUILTIN RESULT"

    def test_non_conflicting_tools_still_registered(self, action_skills_dir: Path):
        """无冲突的 action tools 仍然正常注册。"""
        from core.tools import ToolRegistry
        from core.skill_handler_loader import SkillHandlerLoader
        from core.skill_registry import SkillRegistry
        from core.harness import Harness

        class ConflictHarness:
            def __init__(self):
                self.state = MagicMock()
                self.state.findings = ["f1"]
                self.tool_registry = ToolRegistry()
                self.skill_registry = SkillRegistry(action_skills_dir)
                self._skill_handler_loader = SkillHandlerLoader(action_skills_dir)
                self._action_tool_schemas: list[dict] = []

        harness = ConflictHarness()
        harness._register_action_skill_tools = Harness._register_action_skill_tools.__get__(harness)  # type: ignore[attr-defined]

        # 只注册一个冲突的名称
        harness.tool_registry.register(
            "export_review_report",
            handler=lambda args: "BUILTIN",
            phases=None,
        )

        harness._register_action_skill_tools()  # type: ignore[attr-defined]

        # summarize_findings 不冲突，应该被注册
        result = harness.tool_registry.execute("summarize_findings", {})
        assert "1 findings" in result


# ==============================================================
# Test: ToolDef.to_api_schema()
# ==============================================================


class TestToolDefApiSchema:
    """测试 ToolDef.to_api_schema() 输出格式。"""

    def test_basic_schema(self):
        """基本 schema 输出正确。"""
        tool = ToolDef(
            name="test_tool",
            description="A test tool",
            input_schema={"type": "object", "properties": {"x": {"type": "string"}}},
            handler="skill_handlers/mod.py::fn",
        )
        schema = tool.to_api_schema()
        assert schema == {
            "name": "test_tool",
            "description": "A test tool",
            "input_schema": {"type": "object", "properties": {"x": {"type": "string"}}},
        }

    def test_schema_does_not_include_handler(self):
        """API schema 不应暴露 handler 路径。"""
        tool = ToolDef(
            name="t",
            description="d",
            input_schema={},
            handler="skill_handlers/secret.py::internal_fn",
        )
        schema = tool.to_api_schema()
        assert "handler" not in schema
        assert "secret" not in json.dumps(schema)
