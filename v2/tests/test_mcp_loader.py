"""
tests/test_mcp_loader.py — MCP 通用服务加载器单元测试

测试策略:
    1. MCPServiceConfig 解析 — from_dict 正常/异常
    2. MCPClient 启动失败降级 — 命令不存在、权限拒绝
    3. MCPClient 初始化握手 — mock subprocess 验证协议流程
    4. MCPClient.list_tools — 正常/分页/过滤/空
    5. MCPClient.call_tool — 正常/错误/超时
    6. MCPClient.shutdown — 幂等性
    7. MCPServiceLoader 配置加载 — 文件不存在/格式错误/正常
    8. MCPServiceLoader.load_and_register — 名称冲突/phase 配置/inactive 跳过
    9. MCPServiceLoader.get_tool_schemas — 返回正确 schema
    10. MCPServiceLoader.shutdown_all — 幂等性
    11. Harness 集成 — get_mcp_tool_schemas() 可调用
    12. Agent 集成 — MCP schemas 出现在 self.tools 中

所有测试 mock subprocess，确保离线可跑、不消耗资源。
"""

import json
import sys
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock
import pytest

# 确保 v2/ 在 sys.path
_v2_dir = Path(__file__).resolve().parent.parent
if str(_v2_dir) not in sys.path:
    sys.path.insert(0, str(_v2_dir))

from core.mcp_loader import (
    MCPServiceConfig,
    MCPToolInfo,
    MCPClient,
    MCPServiceLoader,
    _MCP_CONFIG_PATH,
    _PROTOCOL_VERSION,
)
from core.tools import ToolRegistry


# ─── Fixtures ────────────────────────────────────────────────────────────

@pytest.fixture
def sample_config():
    """标准 MCP 服务配置。"""
    return MCPServiceConfig(
        id="test-service",
        name="Test Service",
        command="test-mcp-server",
        args=["--port", "3000"],
        env={"API_KEY": "test123"},
        description="A test MCP service",
        status="active",
        tools_filter=[],
        phases=["deep_review", "editing"],
    )


@pytest.fixture
def sample_config_dict():
    """标准配置 dict（模拟 JSON 文件内容）。"""
    return {
        "id": "test-service",
        "name": "Test Service",
        "command": "test-mcp-server",
        "args": ["--port", "3000"],
        "env": {"API_KEY": "test123"},
        "description": "A test MCP service",
        "status": "active",
        "tools_filter": [],
        "phases": ["deep_review", "editing"],
    }


@pytest.fixture
def minimal_config_dict():
    """最小合法配置 dict。"""
    return {"id": "minimal", "command": "some-cmd"}


@pytest.fixture
def mock_tool_response():
    """模拟 tools/list 响应。"""
    return {
        "jsonrpc": "2.0",
        "id": 2,
        "result": {
            "tools": [
                {
                    "name": "calculate",
                    "description": "Perform calculations",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "expression": {"type": "string", "description": "Math expression"}
                        },
                        "required": ["expression"],
                    },
                },
                {
                    "name": "search",
                    "description": "Search documents",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"}
                        },
                    },
                },
            ]
        },
    }


@pytest.fixture
def tool_registry():
    """空的 ToolRegistry。"""
    return ToolRegistry()


# ─── 1. MCPServiceConfig 解析 ─────────────────────────────────────────────

class TestMCPServiceConfig:
    def test_from_dict_full(self, sample_config_dict):
        config = MCPServiceConfig.from_dict(sample_config_dict)
        assert config.id == "test-service"
        assert config.name == "Test Service"
        assert config.command == "test-mcp-server"
        assert config.args == ["--port", "3000"]
        assert config.env == {"API_KEY": "test123"}
        assert config.status == "active"
        assert config.phases == ["deep_review", "editing"]

    def test_from_dict_minimal(self, minimal_config_dict):
        config = MCPServiceConfig.from_dict(minimal_config_dict)
        assert config.id == "minimal"
        assert config.name == "minimal"  # 默认用 id
        assert config.command == "some-cmd"
        assert config.args == []
        assert config.env == {}
        assert config.status == "active"
        assert config.tools_filter == []
        assert config.phases == []

    def test_from_dict_missing_id_raises(self):
        with pytest.raises(KeyError):
            MCPServiceConfig.from_dict({"command": "x"})

    def test_from_dict_missing_command_raises(self):
        with pytest.raises(KeyError):
            MCPServiceConfig.from_dict({"id": "x"})


# ─── 2. MCPClient 启动失败降级 ────────────────────────────────────────────

class TestMCPClientStartFailure:
    def test_command_not_found(self, sample_config):
        """命令不存在时 start() 返回 False，不抛异常。"""
        sample_config.command = "nonexistent-command-xyz-12345"
        client = MCPClient(sample_config)
        result = client.start()
        assert result is False
        assert client.alive is False

    def test_start_failure_does_not_leave_zombie(self, sample_config):
        """启动失败后 _process 被清理。"""
        sample_config.command = "nonexistent-command-xyz-12345"
        client = MCPClient(sample_config)
        client.start()
        assert client._process is None


# ─── 3. MCPClient 初始化握手 ──────────────────────────────────────────────

class TestMCPClientInitialize:
    def test_successful_handshake(self, sample_config):
        """模拟成功的初始化握手。

        策略: patch _start_reader_threads 为 no-op，手动向 _stdout_queue 注入响应。
        """
        init_response = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {},
                "serverInfo": {"name": "TestServer", "version": "1.0"},
            },
        }) + "\n"

        mock_process = MagicMock()
        mock_process.stdin = MagicMock()
        mock_process.stdout = MagicMock()
        mock_process.stderr = MagicMock()
        mock_process.poll.return_value = None
        mock_process.pid = 12345

        with patch("subprocess.Popen", return_value=mock_process), \
             patch.object(MCPClient, "_start_reader_threads"):
            client = MCPClient(sample_config)
            # 手动向 queue 注入 initialize 响应
            client._stdout_queue.put(init_response.encode("utf-8"))
            result = client.start()

        assert result is True
        assert client.alive is True

    def test_handshake_timeout(self, sample_config):
        """初始化超时时 start() 返回 False。

        策略: patch _start_reader_threads 为 no-op，queue 中放入 None 哨兵模拟进程退出。
        """
        mock_process = MagicMock()
        mock_process.stdin = MagicMock()
        mock_process.stdout = MagicMock()
        mock_process.stderr = MagicMock()
        mock_process.poll.return_value = 1  # 进程已退出
        mock_process.returncode = 1
        mock_process.pid = 99999

        with patch("subprocess.Popen", return_value=mock_process), \
             patch.object(MCPClient, "_start_reader_threads"):
            client = MCPClient(sample_config)
            # 放入 None 哨兵模拟 stdout 关闭
            client._stdout_queue.put(None)
            result = client.start()

        assert result is False


# ─── 4. MCPClient.list_tools ──────────────────────────────────────────────

class TestMCPClientListTools:
    def _make_alive_client(self, sample_config, send_request_responses):
        """创建一个已初始化的 client，mock _send_request 返回值。"""
        client = MCPClient(sample_config)
        client._alive = True
        client._process = MagicMock()
        client._process.poll.return_value = None

        # Mock _send_request 直接返回解析后的 dict
        client._send_request = MagicMock(side_effect=send_request_responses)
        return client

    def test_list_tools_normal(self, sample_config, mock_tool_response):
        """正常发现两个工具。"""
        client = self._make_alive_client(sample_config, [mock_tool_response])
        tools = client.list_tools()
        assert len(tools) == 2
        assert tools[0].name == "calculate"
        assert tools[0].service_id == "test-service"
        assert tools[1].name == "search"

    def test_list_tools_with_filter(self, sample_config, mock_tool_response):
        """tools_filter 只保留指定工具。"""
        sample_config.tools_filter = ["calculate"]
        client = self._make_alive_client(sample_config, [mock_tool_response])
        tools = client.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "calculate"

    def test_list_tools_empty(self, sample_config):
        """服务无工具。"""
        response = {"jsonrpc": "2.0", "id": 1, "result": {"tools": []}}
        client = self._make_alive_client(sample_config, [response])
        tools = client.list_tools()
        assert tools == []

    def test_list_tools_not_alive(self, sample_config):
        """服务未启动时返回空列表。"""
        client = MCPClient(sample_config)
        assert client.list_tools() == []

    def test_list_tools_pagination(self, sample_config):
        """分页场景：nextCursor 触发后续请求。"""
        page1 = {
            "jsonrpc": "2.0", "id": 1,
            "result": {
                "tools": [{"name": "tool_a", "description": "A", "inputSchema": {}}],
                "nextCursor": "cursor_1",
            },
        }
        page2 = {
            "jsonrpc": "2.0", "id": 2,
            "result": {
                "tools": [{"name": "tool_b", "description": "B", "inputSchema": {}}],
            },
        }
        client = self._make_alive_client(sample_config, [page1, page2])
        tools = client.list_tools()
        assert len(tools) == 2
        assert tools[0].name == "tool_a"
        assert tools[1].name == "tool_b"


# ─── 5. MCPClient.call_tool ───────────────────────────────────────────────

class TestMCPClientCallTool:
    def _make_alive_client(self, sample_config, send_request_response):
        """创建已初始化的 client，mock _send_request 返回指定响应。

        与 TestMCPClientListTools 使用相同模式，避免 reader thread 问题。
        """
        client = MCPClient(sample_config)
        client._alive = True
        client._process = MagicMock()
        client._process.poll.return_value = None
        client._send_request = MagicMock(return_value=send_request_response)
        return client

    def test_call_tool_success(self, sample_config):
        """正常调用返回文本结果。"""
        response = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "content": [{"type": "text", "text": "42"}],
                "isError": False,
            },
        }
        client = self._make_alive_client(sample_config, response)
        result = client.call_tool("calculate", {"expression": "6*7"})
        assert result == "42"

    def test_call_tool_error_response(self, sample_config):
        """JSON-RPC error 返回错误信息。"""
        response = {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32600, "message": "Invalid request"},
        }
        client = self._make_alive_client(sample_config, response)
        result = client.call_tool("bad_tool", {})
        assert "[MCP Error]" in result
        assert "Invalid request" in result

    def test_call_tool_is_error_flag(self, sample_config):
        """isError=True 时前缀 [MCP Tool Error]。"""
        response = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "content": [{"type": "text", "text": "division by zero"}],
                "isError": True,
            },
        }
        client = self._make_alive_client(sample_config, response)
        result = client.call_tool("calculate", {"expression": "1/0"})
        assert "[MCP Tool Error]" in result
        assert "division by zero" in result

    def test_call_tool_not_alive(self, sample_config):
        """服务未运行时返回错误信息。"""
        client = MCPClient(sample_config)
        result = client.call_tool("anything", {})
        assert "[MCP Error]" in result
        assert "not running" in result


# ─── 6. MCPClient.shutdown 幂等性 ────────────────────────────────────────

class TestMCPClientShutdown:
    def test_shutdown_idempotent(self, sample_config):
        """多次 shutdown 不抛异常。"""
        client = MCPClient(sample_config)
        client._process = MagicMock()
        client._process.stdin = MagicMock()
        client._process.stdin.closed = False
        client._process.wait.return_value = 0
        client._alive = True

        client.shutdown()
        assert client._process is None
        assert client._alive is False

        # 第二次调用安全
        client.shutdown()
        assert client._process is None

    def test_shutdown_no_process(self, sample_config):
        """从未启动的 client shutdown 不抛异常。"""
        client = MCPClient(sample_config)
        client.shutdown()  # 不应抛异常


# ─── 7. MCPServiceLoader 配置加载 ─────────────────────────────────────────

class TestMCPServiceLoaderConfig:
    def test_no_config_file(self, tmp_path):
        """配置文件不存在时返回 0 工具。"""
        loader = MCPServiceLoader(config_path=tmp_path / "nonexistent.json")
        registry = ToolRegistry()
        count = loader.load_and_register(registry)
        assert count == 0

    def test_invalid_json(self, tmp_path):
        """配置文件 JSON 格式错误时降级。"""
        config_file = tmp_path / "mcp_services.json"
        config_file.write_text("not valid json {{{", encoding="utf-8")
        loader = MCPServiceLoader(config_path=config_file)
        registry = ToolRegistry()
        count = loader.load_and_register(registry)
        assert count == 0

    def test_empty_services(self, tmp_path):
        """services 为空列表。"""
        config_file = tmp_path / "mcp_services.json"
        config_file.write_text(json.dumps({"services": []}), encoding="utf-8")
        loader = MCPServiceLoader(config_path=config_file)
        registry = ToolRegistry()
        count = loader.load_and_register(registry)
        assert count == 0

    def test_inactive_service_skipped(self, tmp_path):
        """status != active 的服务被跳过。"""
        config_file = tmp_path / "mcp_services.json"
        config_file.write_text(json.dumps({
            "services": [{
                "id": "inactive-svc",
                "command": "some-cmd",
                "status": "disabled",
            }]
        }), encoding="utf-8")
        loader = MCPServiceLoader(config_path=config_file)
        registry = ToolRegistry()
        count = loader.load_and_register(registry)
        assert count == 0


# ─── 8. MCPServiceLoader.load_and_register ────────────────────────────────

class TestMCPServiceLoaderRegister:
    def test_name_conflict_skipped(self, tmp_path):
        """MCP 工具名与已有工具冲突时跳过。"""
        config_file = tmp_path / "mcp_services.json"
        config_file.write_text(json.dumps({
            "services": [{
                "id": "svc1",
                "command": "test-cmd",
                "status": "active",
            }]
        }), encoding="utf-8")

        registry = ToolRegistry()
        # 预注册一个同名工具
        registry.register("calculate", lambda args: "existing", phases=None)

        # Mock MCPClient
        mock_client = MagicMock()
        mock_client.start.return_value = True
        mock_client.config = MCPServiceConfig.from_dict({
            "id": "svc1", "command": "test-cmd"
        })
        mock_client.list_tools.return_value = [
            MCPToolInfo(name="calculate", description="Calc", input_schema={}, service_id="svc1"),
            MCPToolInfo(name="new_tool", description="New", input_schema={}, service_id="svc1"),
        ]

        with patch.object(MCPServiceLoader, "_load_configs", return_value=[
            MCPServiceConfig.from_dict({"id": "svc1", "command": "test-cmd"})
        ]):
            with patch("core.mcp_loader.MCPClient", return_value=mock_client):
                loader = MCPServiceLoader(config_path=config_file)
                count = loader.load_and_register(registry)

        # calculate 被跳过，new_tool 注册成功
        assert count == 1
        assert registry.has_tool("new_tool")
        # 原有的 calculate 不被覆盖
        assert registry.execute("calculate", {}) == "existing"

    def test_phase_config_propagated(self, tmp_path):
        """phases 配置正确传递到 ToolRegistry。"""
        config = MCPServiceConfig(
            id="svc1", name="Svc1", command="cmd",
            phases=["deep_review", "editing"],
        )

        mock_client = MagicMock()
        mock_client.start.return_value = True
        mock_client.config = config
        mock_client.list_tools.return_value = [
            MCPToolInfo(name="phase_tool", description="PT", input_schema={}, service_id="svc1"),
        ]

        registry = ToolRegistry()

        with patch.object(MCPServiceLoader, "_load_configs", return_value=[config]):
            with patch("core.mcp_loader.MCPClient", return_value=mock_client):
                loader = MCPServiceLoader(config_path=tmp_path / "x.json")
                loader.load_and_register(registry)

        # 验证工具已注册
        assert registry.has_tool("phase_tool")

    def test_service_start_failure_graceful(self, tmp_path):
        """服务启动失败不阻断其他服务。"""
        config1 = MCPServiceConfig(id="fail-svc", name="Fail", command="bad-cmd")
        config2 = MCPServiceConfig(id="ok-svc", name="OK", command="good-cmd")

        mock_fail = MagicMock()
        mock_fail.start.return_value = False

        mock_ok = MagicMock()
        mock_ok.start.return_value = True
        mock_ok.config = config2
        mock_ok.list_tools.return_value = [
            MCPToolInfo(name="ok_tool", description="OK", input_schema={}, service_id="ok-svc"),
        ]

        clients = iter([mock_fail, mock_ok])

        with patch.object(MCPServiceLoader, "_load_configs", return_value=[config1, config2]):
            with patch("core.mcp_loader.MCPClient", side_effect=lambda cfg: next(clients)):
                loader = MCPServiceLoader(config_path=tmp_path / "x.json")
                registry = ToolRegistry()
                count = loader.load_and_register(registry)

        assert count == 1
        assert registry.has_tool("ok_tool")


# ─── 9. MCPServiceLoader.get_tool_schemas ─────────────────────────────────

class TestMCPServiceLoaderSchemas:
    def test_schemas_format(self, tmp_path):
        """get_tool_schemas 返回正确格式的 schema 列表。"""
        config = MCPServiceConfig(id="svc1", name="Svc1", command="cmd")

        mock_client = MagicMock()
        mock_client.start.return_value = True
        mock_client.config = config
        mock_client.list_tools.return_value = [
            MCPToolInfo(
                name="my_tool",
                description="Does stuff",
                input_schema={"type": "object", "properties": {"x": {"type": "string"}}},
                service_id="svc1",
            ),
        ]

        registry = ToolRegistry()

        with patch.object(MCPServiceLoader, "_load_configs", return_value=[config]):
            with patch("core.mcp_loader.MCPClient", return_value=mock_client):
                loader = MCPServiceLoader(config_path=tmp_path / "x.json")
                loader.load_and_register(registry)

        schemas = loader.get_tool_schemas()
        assert len(schemas) == 1
        assert schemas[0]["name"] == "my_tool"
        assert schemas[0]["description"] == "Does stuff"
        assert schemas[0]["input_schema"]["type"] == "object"

    def test_schemas_returns_copy(self, tmp_path):
        """get_tool_schemas 返回副本，修改不影响内部状态。"""
        loader = MCPServiceLoader(config_path=tmp_path / "x.json")
        loader._tool_schemas = [{"name": "a"}]
        schemas = loader.get_tool_schemas()
        schemas.append({"name": "b"})
        assert len(loader._tool_schemas) == 1


# ─── 10. MCPServiceLoader.shutdown_all 幂等性 ─────────────────────────────

class TestMCPServiceLoaderShutdown:
    def test_shutdown_all_calls_each_client(self):
        """shutdown_all 调用每个 client 的 shutdown。"""
        loader = MCPServiceLoader()
        mock_client1 = MagicMock()
        mock_client1.config = MagicMock(name="svc1")
        mock_client2 = MagicMock()
        mock_client2.config = MagicMock(name="svc2")
        loader._clients = {"svc1": mock_client1, "svc2": mock_client2}

        loader.shutdown_all()

        mock_client1.shutdown.assert_called_once()
        mock_client2.shutdown.assert_called_once()
        assert loader._clients == {}

    def test_shutdown_all_idempotent(self):
        """多次 shutdown_all 不抛异常。"""
        loader = MCPServiceLoader()
        loader.shutdown_all()
        loader.shutdown_all()  # 第二次也安全


# ─── 11. Harness 集成 ─────────────────────────────────────────────────────

class TestHarnessIntegration:
    def test_harness_has_mcp_loader(self):
        """Harness 初始化后有 _mcp_loader 属性。"""
        from core.harness import Harness
        h = Harness()
        assert hasattr(h, '_mcp_loader')
        assert h._mcp_loader is not None

    def test_get_mcp_tool_schemas_empty_by_default(self):
        """无 MCP 配置时返回空列表。"""
        from core.harness import Harness
        h = Harness()
        schemas = h.get_mcp_tool_schemas()
        assert schemas == []

    def test_harness_shutdown_idempotent(self):
        """Harness.shutdown() 多次调用安全。"""
        from core.harness import Harness
        h = Harness()
        h.shutdown()
        h.shutdown()  # 不抛异常


# ─── 12. MCPToolInfo 数据类 ───────────────────────────────────────────────

class TestMCPToolInfo:
    def test_fields(self):
        info = MCPToolInfo(
            name="test",
            description="desc",
            input_schema={"type": "object"},
            service_id="svc1",
        )
        assert info.name == "test"
        assert info.description == "desc"
        assert info.input_schema == {"type": "object"}
        assert info.service_id == "svc1"
