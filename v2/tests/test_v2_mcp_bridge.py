"""
tests/test_v2_mcp_bridge.py — MCP Bridge 单元测试

测试策略:
    1. 降级路径 (import 不可用) — 最常见场景：无 Stata 环境
    2. 降级路径 (Stata CLI 不可用) — 有模块但无 stata-mcp
    3. 正常路径 (mock Stata 执行成功) — 验证端到端 happy path
    4. 异常路径 (执行超时/错误) — 确认不抛异常
    5. 参数校验 — 确认非法输入返回友好错误
    6. 注册集成 — verify_stata 出现在 harness 的 tool registry 中
    7. Phase gating — verify_stata 只在 deep_review/editing 可用
    8. Red Line 1 — 输出总包含 "guidance" 语义提醒

所有测试都 mock LLM 和 Stata 执行，确保离线可跑、不消耗 token。
"""

import sys
import asyncio
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock
import pytest

# 确保 v2/ 在 sys.path
_v2_dir = Path(__file__).resolve().parent.parent
if str(_v2_dir) not in sys.path:
    sys.path.insert(0, str(_v2_dir))

from core.mcp_bridge import (
    tool_verify_stata,
    register_mcp_tools,
    _ensure_stata_module,
    _format_unavailable,
    _format_result,
)
from core.tools import ToolRegistry


# ─── Fixtures ────────────────────────────────────────────────────────────

@pytest.fixture
def sample_issue():
    """标准的方法学问题 dict。"""
    return {
        "id": "METH-001",
        "description": "Sample size of 30 is insufficient for the claimed two-sample t-test with effect size d=0.3",
        "suggestion": "Run power analysis to determine minimum N; consider bootstrapped CI as alternative",
    }


@pytest.fixture
def sample_issue_minimal():
    """最小合法 issue。"""
    return {"id": "METH-002", "description": "Test assumption violated"}


@pytest.fixture
def mock_stata_result_verified():
    """Stata 验证成功的 mock 结果。"""
    return {
        "status": "verified",
        "do_code": "* Power analysis\npower twomeans 0 0.3, n(30)\n",
        "do_path": ".workspace/stata/METH-001.do",
        "stata_output": "Estimated power: 0.85",
        "interpretation": {
            "consistent": True,
            "paper_claims": "N=30 sufficient for d=0.3",
            "stata_result": "Power=0.85 at N=30",
            "discrepancy": None,
            "confidence": 0.9,
            "recommendation": "Sample size is adequate",
        },
        "guidance": "Stata verification confirms the paper's statistical claims.",
    }


@pytest.fixture
def mock_stata_result_discrepancy():
    """Stata 验证发现 discrepancy 的 mock 结果。"""
    return {
        "status": "discrepancy",
        "do_code": "* Power analysis\npower twomeans 0 0.3, n(30)\n",
        "do_path": ".workspace/stata/METH-001.do",
        "stata_output": "Estimated power: 0.42",
        "interpretation": {
            "consistent": False,
            "paper_claims": "N=30 sufficient",
            "stata_result": "Power=0.42 (underpowered)",
            "discrepancy": "Paper claims adequate power but Stata shows only 42%",
            "confidence": 0.95,
            "recommendation": "Increase sample size to N=90+",
        },
        "guidance": "⚠️ Stata results differ from paper claims: Power=0.42",
    }


@pytest.fixture
def mock_stata_result_unavailable():
    """Stata MCP 不可用的 mock 结果。"""
    return {
        "status": "unavailable",
        "do_code": "* Power analysis\npower twomeans 0 0.3, n(30)\n",
        "do_path": ".workspace/stata/METH-001.do",
        "interpretation": None,
        "guidance": "Stata MCP not available. Generated .do code saved to .workspace/stata/METH-001.do.",
    }


# ─── Test 1: 参数校验 ────────────────────────────────────────────────────

class TestParameterValidation:
    """测试参数校验和友好错误返回。"""

    def test_missing_issue(self):
        """缺少 issue 参数返回友好错误。"""
        result = tool_verify_stata({})
        assert "错误" in result
        assert "issue" in result

    def test_issue_not_dict(self):
        """issue 非 dict 类型返回友好错误。"""
        result = tool_verify_stata({"issue": "not a dict"})
        assert "错误" in result
        assert "dict" in result

    def test_issue_empty_description(self):
        """issue.description 为空返回友好错误。"""
        result = tool_verify_stata({"issue": {"id": "X", "description": ""}})
        assert "错误" in result
        assert "description" in result

    def test_issue_no_description_key(self):
        """issue 缺少 description 键返回友好错误。"""
        result = tool_verify_stata({"issue": {"id": "X"}})
        assert "错误" in result


# ─── Test 2: 降级路径 — import 不可用 ──────────────────────────────────────

class TestImportDegradation:
    """测试 tools/stata_verify.py 不可导入时的降级行为。"""

    def test_module_not_found_graceful(self, sample_issue):
        """stata_verify 模块不存在时优雅降级，不抛异常。"""
        import core.mcp_bridge as bridge
        # 模拟模块不可用
        original_module = bridge._stata_module
        original_error = bridge._import_error
        bridge._stata_module = None
        bridge._import_error = "tools/stata_verify.py not found (test)"

        try:
            result = tool_verify_stata({"issue": sample_issue})
            assert "降级" in result or "不可用" in result
            assert "METH-001" in result or sample_issue["description"][:30] in result
            # 不应抛异常
        finally:
            bridge._stata_module = original_module
            bridge._import_error = original_error

    def test_degradation_includes_issue_description(self, sample_issue):
        """降级输出包含问题描述以便人工跟进。"""
        result = _format_unavailable(sample_issue, "test reason")
        assert "Sample size" in result
        assert "降级" in result or "不可用" in result

    def test_degradation_includes_suggestion(self, sample_issue):
        """降级输出包含 suggestion 信息。"""
        result = _format_unavailable(sample_issue, "test")
        assert "power analysis" in result.lower() or "bootstrap" in result.lower()


# ─── Test 3: 降级路径 — Stata CLI 不可用 ──────────────────────────────────

class TestStataUnavailable:
    """测试 stata-mcp CLI 不可用时的降级行为。"""

    def test_stata_unavailable_returns_guidance(
        self, sample_issue, mock_stata_result_unavailable
    ):
        """Stata CLI 不可用时返回 .do 代码作为 guidance。"""
        import core.mcp_bridge as bridge

        # Mock the module as available but stata execution returns unavailable
        mock_module = MagicMock()
        mock_module.stata_verify = AsyncMock(return_value=mock_stata_result_unavailable)
        mock_module.format_stata_result = MagicMock(
            return_value="📋 Stata Verification: unavailable\n  .do file: .workspace/stata/METH-001.do"
        )

        original_module = bridge._stata_module
        original_error = bridge._import_error
        bridge._stata_module = mock_module
        bridge._import_error = None

        try:
            result = tool_verify_stata({"issue": sample_issue})
            assert "unavailable" in result.lower() or "不可用" in result
            # Red Line 1 提醒
            assert "Red Line 1" in result or "guidance" in result
        finally:
            bridge._stata_module = original_module
            bridge._import_error = original_error


# ─── Test 4: 正常路径 — 验证通过 ─────────────────────────────────────────

class TestHappyPath:
    """测试 Stata 正常执行并返回结果。"""

    def test_verified_result_formatted(
        self, sample_issue, mock_stata_result_verified
    ):
        """验证通过时输出正确格式化。"""
        import core.mcp_bridge as bridge

        mock_module = MagicMock()
        mock_module.stata_verify = AsyncMock(return_value=mock_stata_result_verified)
        mock_module.format_stata_result = MagicMock(
            return_value="✅ Stata Verification: verified\n  .do file: .workspace/stata/METH-001.do\n  Stata verification confirms the paper's statistical claims."
        )

        original_module = bridge._stata_module
        original_error = bridge._import_error
        bridge._stata_module = mock_module
        bridge._import_error = None

        try:
            result = tool_verify_stata({"issue": sample_issue})
            assert "verified" in result.lower() or "✅" in result
            # Red Line 1 仍然存在
            assert "Red Line 1" in result or "guidance" in result
        finally:
            bridge._stata_module = original_module
            bridge._import_error = original_error

    def test_discrepancy_result_formatted(
        self, sample_issue, mock_stata_result_discrepancy
    ):
        """发现 discrepancy 时输出包含差异描述和 Red Line 1。"""
        import core.mcp_bridge as bridge

        mock_module = MagicMock()
        mock_module.stata_verify = AsyncMock(return_value=mock_stata_result_discrepancy)
        mock_module.format_stata_result = MagicMock(
            return_value="⚠️ Stata Verification: discrepancy\n  .do file: .workspace/stata/METH-001.do\n  ⚠️ Discrepancy: Power=0.42"
        )

        original_module = bridge._stata_module
        original_error = bridge._import_error
        bridge._stata_module = mock_module
        bridge._import_error = None

        try:
            result = tool_verify_stata({"issue": sample_issue})
            assert "discrepancy" in result.lower() or "⚠️" in result
            assert "Red Line 1" in result or "guidance" in result
        finally:
            bridge._stata_module = original_module
            bridge._import_error = original_error


# ─── Test 5: 异常路径 — 执行超时/内部错误 ─────────────────────────────────

class TestExceptionHandling:
    """测试执行过程中的异常不传播到调用者。"""

    def test_async_exception_caught(self, sample_issue):
        """异步执行抛异常时返回友好错误信息。"""
        import core.mcp_bridge as bridge

        mock_module = MagicMock()
        mock_module.stata_verify = AsyncMock(side_effect=TimeoutError("Stata timed out"))

        original_module = bridge._stata_module
        original_error = bridge._import_error
        bridge._stata_module = mock_module
        bridge._import_error = None

        try:
            result = tool_verify_stata({"issue": sample_issue})
            # 不应抛异常
            assert "异常" in result or "TimeoutError" in result
            assert "手动检查" in result or "确认" in result
        finally:
            bridge._stata_module = original_module
            bridge._import_error = original_error

    def test_runtime_error_caught(self, sample_issue):
        """运行时错误被优雅捕获。"""
        import core.mcp_bridge as bridge

        mock_module = MagicMock()
        mock_module.stata_verify = AsyncMock(side_effect=RuntimeError("LLM client failed"))

        original_module = bridge._stata_module
        original_error = bridge._import_error
        bridge._stata_module = mock_module
        bridge._import_error = None

        try:
            result = tool_verify_stata({"issue": sample_issue})
            assert "RuntimeError" in result
        finally:
            bridge._stata_module = original_module
            bridge._import_error = original_error


# ─── Test 6: 注册集成 ────────────────────────────────────────────────────

class TestRegistration:
    """测试 MCP bridge 工具在 ToolRegistry 中的注册。"""

    def test_register_mcp_tools_adds_verify_stata(self):
        """register_mcp_tools 将 verify_stata 注册到 registry。"""
        registry = ToolRegistry()
        registered = register_mcp_tools(registry)
        assert "verify_stata" in registered
        assert registry.has_tool("verify_stata")

    def test_registered_tool_executable(self):
        """注册后的工具可通过 registry.execute 调用。"""
        registry = ToolRegistry()
        register_mcp_tools(registry)
        # 用无效参数触发参数校验（不应抛异常）
        result = registry.execute("verify_stata", {})
        assert "错误" in result

    def test_verify_stata_in_harness_registry(self):
        """Harness 初始化后 tool_registry 包含 verify_stata。"""
        from core.harness import Harness
        h = Harness()
        assert h.tool_registry.has_tool("verify_stata")

    def test_harness_execute_verify_stata(self):
        """通过 Harness.execute_tool 调用 verify_stata — 优雅处理无 Stata/无 API key 环境。"""
        from core.harness import Harness
        h = Harness()
        result = h.execute_tool("verify_stata", {"issue": {"id": "X", "description": "test"}})
        # 测试环境可能走：降级路径(无模块) 或 异常捕获路径(无API key) — 两者都是优雅降级
        graceful = (
            "降级" in result
            or "不可用" in result
            or "unavailable" in result.lower()
            or "执行异常" in result
        )
        assert graceful, f"Expected graceful degradation, got: {result[:200]}"


# ─── Test 7: Phase Gating ────────────────────────────────────────────────

class TestPhaseGating:
    """测试 verify_stata 的阶段门控。"""

    def test_available_in_deep_review(self):
        """verify_stata 在 deep_review 阶段可用。"""
        registry = ToolRegistry()
        register_mcp_tools(registry)
        tools = registry.get_tools_for_phase("deep_review")
        assert "verify_stata" in tools

    def test_available_in_editing(self):
        """verify_stata 在 editing 阶段可用。"""
        registry = ToolRegistry()
        register_mcp_tools(registry)
        tools = registry.get_tools_for_phase("editing")
        assert "verify_stata" in tools

    def test_not_available_in_initial_scan(self):
        """verify_stata 在 initial_scan 阶段不可用。"""
        registry = ToolRegistry()
        register_mcp_tools(registry)
        tools = registry.get_tools_for_phase("initial_scan")
        assert "verify_stata" not in tools

    def test_not_available_in_synthesis(self):
        """verify_stata 在 synthesis 阶段不可用。"""
        registry = ToolRegistry()
        register_mcp_tools(registry)
        tools = registry.get_tools_for_phase("synthesis")
        assert "verify_stata" not in tools


# ─── Test 8: Red Line 1 一致性 ───────────────────────────────────────────

class TestRedLine1:
    """确保所有输出路径都包含 Red Line 1 提醒。"""

    def test_degradation_mentions_red_line(self, sample_issue):
        """降级模式输出包含 Red Line 1。"""
        result = _format_unavailable(sample_issue, "test")
        assert "Red Line 1" in result

    def test_format_result_appends_red_line(self, mock_stata_result_verified):
        """正常结果格式化追加 Red Line 1。"""
        import core.mcp_bridge as bridge

        mock_module = MagicMock()
        mock_module.format_stata_result = MagicMock(return_value="✅ Verified")

        original_module = bridge._stata_module
        bridge._stata_module = mock_module
        try:
            result = _format_result(mock_stata_result_verified)
            assert "Red Line 1" in result
        finally:
            bridge._stata_module = original_module

    def test_discrepancy_never_says_auto_modify(
        self, sample_issue, mock_stata_result_discrepancy
    ):
        """discrepancy 结果绝不包含 auto-modify/自动修改 等字眼。"""
        import core.mcp_bridge as bridge

        mock_module = MagicMock()
        mock_module.stata_verify = AsyncMock(return_value=mock_stata_result_discrepancy)
        mock_module.format_stata_result = MagicMock(
            return_value="⚠️ Discrepancy found"
        )

        original_module = bridge._stata_module
        original_error = bridge._import_error
        bridge._stata_module = mock_module
        bridge._import_error = None

        try:
            result = tool_verify_stata({"issue": sample_issue})
            assert "自动修改" not in result
            assert "auto-modify" not in result.lower()
            assert "auto modify" not in result.lower()
        finally:
            bridge._stata_module = original_module
            bridge._import_error = original_error


# ─── Test 9: SCHOLAR_TOOLS Schema + Phase Filter Integration ─────────────

class TestScholarToolsSchemaIntegration:
    """验证 verify_stata 的 JSON schema 存在于 SCHOLAR_TOOLS，
    且 _filter_tools_by_phase 能在正确阶段暴露给 LLM。"""

    def test_verify_stata_schema_in_scholar_tools(self):
        """SCHOLAR_TOOLS 列表中包含 verify_stata 的 schema 定义。"""
        from core.identity import SCHOLAR_TOOLS
        names = [t["name"] for t in SCHOLAR_TOOLS]
        assert "verify_stata" in names

    def test_verify_stata_schema_has_required_fields(self):
        """verify_stata schema 包含必需的 input_schema 结构。"""
        from core.identity import SCHOLAR_TOOLS
        schema = next(t for t in SCHOLAR_TOOLS if t["name"] == "verify_stata")
        assert "description" in schema
        assert "input_schema" in schema
        props = schema["input_schema"]["properties"]
        assert "issue" in props
        assert props["issue"]["type"] == "object"
        assert "id" in props["issue"]["properties"]
        assert "description" in props["issue"]["properties"]

    def test_filter_tools_by_phase_includes_verify_stata_in_deep_review(self):
        """_filter_tools_by_phase 在 deep_review 阶段保留 verify_stata。"""
        from core.identity import SCHOLAR_TOOLS
        from core.loop import _filter_tools_by_phase
        from core.harness import Harness
        from core.phases import Phase

        h = Harness(enable_hdwm=False)
        h.phase_fsm._state.current = Phase.DEEP_REVIEW
        filtered = _filter_tools_by_phase(SCHOLAR_TOOLS, h)
        filtered_names = [t["name"] for t in filtered]
        assert "verify_stata" in filtered_names

    def test_filter_tools_by_phase_includes_verify_stata_in_editing(self):
        """_filter_tools_by_phase 在 editing 阶段保留 verify_stata。"""
        from core.identity import SCHOLAR_TOOLS
        from core.loop import _filter_tools_by_phase
        from core.harness import Harness
        from core.phases import Phase

        h = Harness(enable_hdwm=False)
        h.phase_fsm._state.current = Phase.EDITING
        filtered = _filter_tools_by_phase(SCHOLAR_TOOLS, h)
        filtered_names = [t["name"] for t in filtered]
        assert "verify_stata" in filtered_names

    def test_filter_tools_by_phase_excludes_verify_stata_in_initial_scan(self):
        """_filter_tools_by_phase 在 initial_scan 阶段过滤掉 verify_stata。"""
        from core.identity import SCHOLAR_TOOLS
        from core.loop import _filter_tools_by_phase
        from core.harness import Harness
        from core.phases import Phase

        h = Harness(enable_hdwm=False)
        # INITIAL_SCAN is the default, but be explicit
        h.phase_fsm._state.current = Phase.INITIAL_SCAN
        filtered = _filter_tools_by_phase(SCHOLAR_TOOLS, h)
        filtered_names = [t["name"] for t in filtered]
        assert "verify_stata" not in filtered_names
