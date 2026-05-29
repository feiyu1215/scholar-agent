"""
tests/test_tool_consistency.py — 工具双注册一致性检查的单元测试

验证:
1. schema 有但 handler 没有 → strict 模式下 raise AssertionError
2. handler 有但 schema 没有 → 打印 WARNING 但不 raise
3. 完全一致时 → 静默通过
4. KNOWN_INTERNAL_ALIASES 中的工具不触发 WARNING
5. Kill Switch 环境变量可禁用检查
"""

import os
import pytest
from unittest.mock import patch

from core.tools import ToolRegistry
from core.tool_consistency import check_tool_consistency, KNOWN_INTERNAL_ALIASES


# ==============================================================
# Fixtures
# ==============================================================

@pytest.fixture
def registry():
    """创建一个带有几个工具的 ToolRegistry。"""
    reg = ToolRegistry()
    reg.register("read_section", handler=lambda args: "ok", phases={"initial_scan", "deep_review"})
    reg.register("update_findings", handler=lambda args: "ok", phases=None)
    reg.register("done", handler=lambda args: "ok", phases=None)  # 内部别名
    return reg


@pytest.fixture
def schemas_matching():
    """与 registry 完全匹配的 schemas（不含 done 别名，含 mark_complete）。"""
    return [
        {"name": "read_section", "description": "...", "input_schema": {}},
        {"name": "update_findings", "description": "...", "input_schema": {}},
    ]


# ==============================================================
# Tests: CHECK 1 — Schema 有但 Handler 没有
# ==============================================================

class TestSchemaWithoutHandler:
    """schema 中定义了工具但 registry 中没有 handler → 必须报错。"""

    def test_strict_mode_raises(self, registry):
        """strict=True 时应该 raise AssertionError。"""
        schemas = [
            {"name": "read_section", "description": "..."},
            {"name": "nonexistent_tool", "description": "..."},  # 没有 handler
        ]
        with pytest.raises(AssertionError, match="nonexistent_tool"):
            check_tool_consistency(schemas, registry, strict=True)

    def test_non_strict_mode_no_raise(self, registry, capsys):
        """strict=False 时不 raise，但打印 ERROR。"""
        schemas = [
            {"name": "read_section", "description": "..."},
            {"name": "ghost_tool", "description": "..."},
        ]
        # 不应该 raise
        check_tool_consistency(schemas, registry, strict=False)
        captured = capsys.readouterr()
        assert "ghost_tool" in captured.err
        assert "FATAL" in captured.err


# ==============================================================
# Tests: CHECK 2 — Handler 有但 Schema 没有
# ==============================================================

class TestHandlerWithoutSchema:
    """registry 中有 handler 但 schema 中没有 → WARNING。"""

    def test_warning_for_invisible_tool(self, registry, capsys):
        """非别名工具没有 schema 时应该打印 WARNING。"""
        # 只给 read_section 的 schema，update_findings 没有 schema
        schemas = [{"name": "read_section", "description": "..."}]
        check_tool_consistency(schemas, registry, strict=True)
        captured = capsys.readouterr()
        assert "update_findings" in captured.err
        assert "WARNING" in captured.err

    def test_known_alias_no_warning(self, registry, capsys):
        """KNOWN_INTERNAL_ALIASES 中的工具不触发 WARNING。"""
        # done 是已知别名，不应该出现在 WARNING 中
        schemas = [
            {"name": "read_section", "description": "..."},
            {"name": "update_findings", "description": "..."},
        ]
        check_tool_consistency(schemas, registry, strict=True)
        captured = capsys.readouterr()
        assert "done" not in captured.err


# ==============================================================
# Tests: 完全一致
# ==============================================================

class TestFullConsistency:
    """所有 schema 都有 handler，所有非别名 handler 都有 schema。"""

    def test_no_output_when_consistent(self, registry, schemas_matching, capsys):
        """完全一致时不打印任何 WARNING/ERROR。"""
        check_tool_consistency(schemas_matching, registry, strict=True)
        captured = capsys.readouterr()
        assert "FATAL" not in captured.err
        assert "WARNING" not in captured.err


# ==============================================================
# Tests: Kill Switch
# ==============================================================

class TestKillSwitch:
    """环境变量 SCHOLAR_TOOL_CONSISTENCY_CHECK=0 可禁用检查。"""

    def test_disabled_by_env(self, registry):
        """禁用后即使有不一致也不 raise。"""
        schemas = [{"name": "totally_fake_tool", "description": "..."}]
        with patch.dict(os.environ, {"SCHOLAR_TOOL_CONSISTENCY_CHECK": "0"}):
            # 不应该 raise
            check_tool_consistency(schemas, registry, strict=True)

    def test_enabled_by_default(self, registry):
        """默认启用。"""
        schemas = [{"name": "totally_fake_tool", "description": "..."}]
        with patch.dict(os.environ, {}, clear=False):
            # 确保没有设置禁用变量
            os.environ.pop("SCHOLAR_TOOL_CONSISTENCY_CHECK", None)
            with pytest.raises(AssertionError):
                check_tool_consistency(schemas, registry, strict=True)


# ==============================================================
# Tests: 真实 Agent 初始化一致性
# ==============================================================

class TestRealAgentConsistency:
    """验证真实的 ScholarAgent 初始化时一致性检查通过。"""

    def test_scholar_persona_consistent(self):
        """scholar persona 的 tools 和 registry 一致。"""
        from core.identity import get_persona
        from core.harness import Harness

        _, tools = get_persona("scholar")
        harness = Harness(paper_path=None, enable_hdwm=False)

        # 追加 action skill schemas
        action_schemas = harness.get_action_tool_schemas()
        if action_schemas:
            tools = list(tools) + action_schemas

        # 不应该 raise
        check_tool_consistency(tools, harness.tool_registry, strict=True)

    def test_scholar_with_hdwm_consistent(self):
        """scholar + HDWM 启用时一致。"""
        from core.identity import get_persona

        _, tools = get_persona("scholar")

        # 模拟 HDWM 启用: 追加 HDWM schemas
        from core.agent import _HDWM_TOOL_SCHEMAS
        tools = list(tools) + _HDWM_TOOL_SCHEMAS

        # HDWM harness
        from core.harness import Harness
        harness = Harness(paper_path=None, enable_hdwm=True)

        action_schemas = harness.get_action_tool_schemas()
        if action_schemas:
            tools = list(tools) + action_schemas

        check_tool_consistency(tools, harness.tool_registry, strict=True)

    def test_writer_persona_consistent(self):
        """writer persona 一致。"""
        from core.identity import get_persona
        from core.harness import Harness

        _, tools = get_persona("writer")
        harness = Harness(paper_path=None, persona="writer", enable_hdwm=False)

        action_schemas = harness.get_action_tool_schemas()
        if action_schemas:
            tools = list(tools) + action_schemas

        check_tool_consistency(tools, harness.tool_registry, strict=True)

    def test_code_reviewer_persona_consistent(self):
        """code_reviewer persona 一致。"""
        from core.identity import get_persona
        from core.harness import Harness

        _, tools = get_persona("code_reviewer")
        harness = Harness(paper_path=None, persona="code_reviewer", enable_hdwm=False)

        action_schemas = harness.get_action_tool_schemas()
        if action_schemas:
            tools = list(tools) + action_schemas

        check_tool_consistency(tools, harness.tool_registry, strict=True)
