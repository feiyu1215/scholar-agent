"""
core/mcp_loader.py — 通用 MCP 服务加载器

在 Agent 启动时读取 config/mcp_services.json，为每个 active 服务:
    1. Spawn 子进程 (stdio transport)
    2. 完成 MCP 初始化握手 (initialize + notifications/initialized)
    3. 发现工具列表 (tools/list)
    4. 为每个工具注册 handler 到 ToolRegistry + 生成 LLM schema

协议: JSON-RPC 2.0 over stdio (newline-delimited JSON)
参考: MCP Specification 2025-03-26

设计原则:
    - 降级优先: 任何 MCP 服务启动失败不阻断 Agent 启动
    - 超时保护: 所有 I/O 操作有超时（持久 reader thread + queue）
    - 名称冲突: MCP 工具名与内置工具冲突时跳过，不覆盖
    - 优雅关闭: Agent 退出时正确终止所有 MCP 子进程
    - 无死锁: stderr 由 daemon thread 异步消费，不阻塞管道
"""

from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# 项目路径
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_MCP_CONFIG_PATH = _PROJECT_ROOT / "config" / "mcp_services.json"

# 协议常量
_PROTOCOL_VERSION = "2025-03-26"
_CLIENT_INFO = {"name": "ScholarAgent", "version": "2.0.0"}

# 超时配置 (秒)
_INIT_TIMEOUT = 15.0        # 初始化握手超时
_TOOLS_LIST_TIMEOUT = 10.0  # tools/list 超时
_TOOL_CALL_TIMEOUT = 60.0   # tools/call 超时
_SHUTDOWN_TIMEOUT = 5.0     # 关闭等待超时


# ==============================================================
# 数据类
# ==============================================================

@dataclass
class MCPServiceConfig:
    """从 mcp_services.json 解析的服务配置。"""
    id: str
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    description: str = ""
    status: str = "active"
    tools_filter: list[str] = field(default_factory=list)
    phases: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> MCPServiceConfig:
        return cls(
            id=d["id"],
            name=d.get("name", d["id"]),
            command=d["command"],
            args=d.get("args", []),
            env=d.get("env", {}),
            description=d.get("description", ""),
            status=d.get("status", "active"),
            tools_filter=d.get("tools_filter", []),
            phases=d.get("phases", []),
        )


@dataclass
class MCPToolInfo:
    """从 MCP 服务发现的工具信息。"""
    name: str
    description: str
    input_schema: dict
    service_id: str  # 所属服务 ID


# ==============================================================
# MCP 客户端协议实现
# ==============================================================

class MCPClient:
    """单个 MCP 服务的客户端，管理 subprocess + JSON-RPC 通信。

    I/O 模型:
        - stdout: 由持久 daemon reader thread 读取，放入 queue
        - stderr: 由独立 daemon thread 异步消费（避免管道缓冲区死锁）
        - stdin: 主线程同步写入
    """

    def __init__(self, config: MCPServiceConfig):
        self.config = config
        self._process: Optional[subprocess.Popen] = None
        self._request_id: int = 0
        self._alive: bool = False
        # Reader thread + queue（启动后初始化）
        self._stdout_queue: queue.Queue[Optional[bytes]] = queue.Queue()
        self._stdout_thread: Optional[threading.Thread] = None
        self._stderr_thread: Optional[threading.Thread] = None

    @property
    def alive(self) -> bool:
        return self._alive and self._process is not None and self._process.poll() is None

    def start(self) -> bool:
        """启动 MCP 服务子进程并完成初始化握手。

        Returns:
            True 如果成功启动并完成握手，False 如果失败。
        """
        try:
            self._spawn_process()
            self._start_reader_threads()
            self._do_initialize()
            self._alive = True
            logger.info(
                "[MCPClient] Service '%s' started successfully (pid=%d)",
                self.config.name, self._process.pid,
            )
            return True
        except Exception as e:
            logger.warning(
                "[MCPClient] Service '%s' failed to start: %s: %s",
                self.config.name, type(e).__name__, e,
            )
            self._cleanup()
            return False

    def list_tools(self) -> list[MCPToolInfo]:
        """发现服务提供的工具列表。"""
        if not self.alive:
            return []

        try:
            response = self._send_request("tools/list")
        except Exception as e:
            logger.warning(
                "[MCPClient] tools/list failed for '%s': %s",
                self.config.name, e,
            )
            return []

        tools = []
        result = response.get("result", {})
        self._collect_tools_from_result(result, tools)

        # 处理分页
        next_cursor = result.get("nextCursor")
        while next_cursor:
            try:
                response = self._send_request("tools/list", {"cursor": next_cursor})
                result = response.get("result", {})
                self._collect_tools_from_result(result, tools)
                next_cursor = result.get("nextCursor")
            except Exception:
                break

        return tools

    def call_tool(self, tool_name: str, arguments: dict) -> str:
        """调用 MCP 工具并返回结果文本。"""
        if not self.alive:
            return f"[MCP Error] Service '{self.config.name}' is not running."

        try:
            response = self._send_request(
                "tools/call",
                {"name": tool_name, "arguments": arguments},
                timeout=_TOOL_CALL_TIMEOUT,
            )
        except TimeoutError:
            return f"[MCP Timeout] Tool '{tool_name}' on service '{self.config.name}' timed out after {_TOOL_CALL_TIMEOUT}s."
        except Exception as e:
            return f"[MCP Error] Tool '{tool_name}' call failed: {type(e).__name__}: {e}"

        # 处理 JSON-RPC error
        if "error" in response:
            err = response["error"]
            return f"[MCP Error] {err.get('message', 'Unknown error')} (code: {err.get('code', '?')})"

        # 提取 result.content
        result = response.get("result", {})
        is_error = result.get("isError", False)
        content_parts = result.get("content", [])

        text_parts = []
        for part in content_parts:
            if part.get("type") == "text":
                text_parts.append(part.get("text", ""))
            elif part.get("type") == "image":
                text_parts.append(f"[Image: {part.get('mimeType', 'image/*')}]")
            elif part.get("type") == "resource":
                resource = part.get("resource", {})
                text_parts.append(resource.get("text", f"[Resource: {resource.get('uri', '?')}]"))
            else:
                text_parts.append(f"[{part.get('type', 'unknown')} content]")

        output = "\n".join(text_parts) if text_parts else "(empty response)"

        if is_error:
            return f"[MCP Tool Error] {output}"
        return output

    def shutdown(self) -> None:
        """优雅关闭 MCP 服务。多次调用安全（幂等）。"""
        self._alive = False
        if self._process is None:
            return

        try:
            # 1. 关闭 stdin（触发子进程感知 EOF）
            if self._process.stdin and not self._process.stdin.closed:
                self._process.stdin.close()

            # 2. 等待退出
            try:
                self._process.wait(timeout=_SHUTDOWN_TIMEOUT)
                return
            except subprocess.TimeoutExpired:
                pass

            # 3. SIGTERM
            self._process.terminate()
            try:
                self._process.wait(timeout=2.0)
                return
            except subprocess.TimeoutExpired:
                pass

            # 4. SIGKILL
            self._process.kill()
            self._process.wait(timeout=1.0)

        except Exception as e:
            logger.debug("[MCPClient] Error during shutdown of '%s': %s", self.config.name, e)
        finally:
            self._process = None
            # Reader threads 是 daemon，进程结束后 readline 会返回 b""，
            # 线程自然退出。无需 join。

    # ----------------------------------------------------------
    # 内部方法
    # ----------------------------------------------------------

    def _spawn_process(self) -> None:
        """启动子进程。"""
        env = os.environ.copy()
        env.update(self.config.env)

        cmd = [self.config.command] + self.config.args

        try:
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                bufsize=0,  # unbuffered
            )
        except FileNotFoundError:
            raise RuntimeError(
                f"Command not found: '{self.config.command}'. "
                f"Ensure it is installed and in PATH."
            )
        except PermissionError:
            raise RuntimeError(
                f"Permission denied for command: '{self.config.command}'."
            )

    def _start_reader_threads(self) -> None:
        """启动 stdout/stderr 的持久 daemon reader threads。

        stdout reader: 逐行读取放入 queue，进程退出时放入 None 哨兵。
        stderr reader: 逐行读取写入 debug 日志，防止管道缓冲区满死锁。
        """
        # stdout reader
        self._stdout_thread = threading.Thread(
            target=self._stdout_reader_loop,
            name=f"mcp-stdout-{self.config.id}",
            daemon=True,
        )
        self._stdout_thread.start()

        # stderr reader（防止管道缓冲区满导致子进程阻塞）
        self._stderr_thread = threading.Thread(
            target=self._stderr_reader_loop,
            name=f"mcp-stderr-{self.config.id}",
            daemon=True,
        )
        self._stderr_thread.start()

    def _stdout_reader_loop(self) -> None:
        """持久 stdout reader — 逐行读取放入 queue。"""
        try:
            while True:
                line = self._process.stdout.readline()
                if not line:
                    # 进程 stdout 关闭（进程退出）
                    self._stdout_queue.put(None)
                    break
                self._stdout_queue.put(line)
        except Exception:
            # 进程被 kill 或 stdout 已关闭
            self._stdout_queue.put(None)

    def _stderr_reader_loop(self) -> None:
        """持久 stderr reader — 消费 stderr 防止管道满死锁。"""
        try:
            while True:
                line = self._process.stderr.readline()
                if not line:
                    break
                # 写入 debug 日志（限制长度避免日志爆炸）
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    logger.debug("[MCP/%s stderr] %s", self.config.id, text[:200])
        except Exception:
            pass

    def _readline_with_timeout(self, timeout: float) -> str:
        """从 stdout queue 中读取一行，带超时保护。

        不创建新线程，直接从 queue.get() 获取数据。
        """
        try:
            data = self._stdout_queue.get(timeout=timeout)
            if data is None:
                # 哨兵：进程已退出
                return ""
            return data.decode("utf-8", errors="replace")
        except queue.Empty:
            return ""

    def _do_initialize(self) -> None:
        """执行 MCP 初始化握手。"""
        # Step 1: 发送 initialize 请求
        response = self._send_request("initialize", {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": _CLIENT_INFO,
        }, timeout=_INIT_TIMEOUT)

        # 验证响应
        result = response.get("result")
        if not result:
            error = response.get("error", {})
            raise RuntimeError(
                f"Initialize failed: {error.get('message', 'No result in response')}"
            )

        server_version = result.get("protocolVersion", "unknown")
        server_info = result.get("serverInfo", {})
        logger.info(
            "[MCPClient] '%s' initialized — server: %s v%s, protocol: %s",
            self.config.name,
            server_info.get("name", "?"),
            server_info.get("version", "?"),
            server_version,
        )

        # Step 2: 发送 initialized 通知
        self._send_notification("notifications/initialized")

    def _send_request(self, method: str, params: dict | None = None, timeout: float = _TOOLS_LIST_TIMEOUT) -> dict:
        """发送 JSON-RPC 请求并等待响应。"""
        self._request_id += 1
        request: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": method,
        }
        if params is not None:
            request["params"] = params
        self._write_message(request)
        return self._read_response(self._request_id, timeout=timeout)

    def _send_notification(self, method: str, params: dict | None = None) -> None:
        """发送 JSON-RPC 通知 (无 id，无响应)。"""
        notification: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params:
            notification["params"] = params
        self._write_message(notification)

    def _write_message(self, message: dict) -> None:
        """写入一条 JSON-RPC 消息到子进程 stdin。"""
        if not self._process or not self._process.stdin:
            raise RuntimeError("Process stdin not available")

        line = json.dumps(message, ensure_ascii=False) + "\n"
        self._process.stdin.write(line.encode("utf-8"))
        self._process.stdin.flush()

    def _read_response(self, expected_id: int, timeout: float) -> dict:
        """从 stdout queue 读取匹配指定 id 的响应。

        跳过 notifications (无 id 的消息)。
        """
        deadline = time.monotonic() + timeout

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"Timeout waiting for response to request id={expected_id} "
                    f"(method timed out after {timeout}s)"
                )

            line = self._readline_with_timeout(remaining)
            if not line:
                # 进程可能已退出
                if self._process and self._process.poll() is not None:
                    raise RuntimeError(
                        f"MCP process exited unexpectedly (code={self._process.returncode})."
                    )
                continue

            line = line.strip()
            if not line:
                continue

            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                logger.debug("[MCPClient] Non-JSON line from '%s': %s", self.config.name, line[:100])
                continue

            # 跳过 notifications (无 id)
            if "id" not in msg:
                continue

            # 匹配 id
            if msg.get("id") == expected_id:
                return msg

            # id 不匹配 — 可能是旧的响应，跳过
            logger.debug(
                "[MCPClient] Unexpected response id=%s (expected %d)",
                msg.get("id"), expected_id,
            )

    def _collect_tools_from_result(self, result: dict, tools: list[MCPToolInfo]) -> None:
        """从 tools/list 响应的 result 中提取工具信息（去重逻辑）。"""
        for tool_data in result.get("tools", []):
            name = tool_data.get("name", "")
            if not name:
                continue
            # 应用 tools_filter
            if self.config.tools_filter and name not in self.config.tools_filter:
                continue
            tools.append(MCPToolInfo(
                name=name,
                description=tool_data.get("description", ""),
                input_schema=tool_data.get("inputSchema", {"type": "object", "properties": {}}),
                service_id=self.config.id,
            ))

    def _cleanup(self) -> None:
        """清理失败的进程。"""
        if self._process:
            try:
                self._process.kill()
                self._process.wait(timeout=2.0)
            except Exception:
                pass
            self._process = None
        self._alive = False


# ==============================================================
# MCPServiceLoader — 管理所有 MCP 服务的生命周期
# ==============================================================

class MCPServiceLoader:
    """
    在 Agent 启动时加载所有 MCP 服务，发现工具，注册到 ToolRegistry。

    使用方式:
        loader = MCPServiceLoader()
        tools_registered = loader.load_and_register(tool_registry)
        schemas = loader.get_tool_schemas()  # 追加到 Agent.self.tools

    关闭:
        loader.shutdown_all()  # Agent 退出时调用
    """

    def __init__(self, config_path: Path | None = None):
        self._config_path = config_path or _MCP_CONFIG_PATH
        self._clients: dict[str, MCPClient] = {}  # service_id → MCPClient
        self._tool_schemas: list[dict] = []  # LLM 可见的 tool schemas
        self._tool_service_map: dict[str, str] = {}  # tool_name → service_id

    def load_and_register(self, tool_registry) -> int:
        """
        加载所有 active MCP 服务，发现工具，注册到 ToolRegistry。

        如果中途发生意外异常，已启动的服务会被正确清理。

        Args:
            tool_registry: ToolRegistry 实例

        Returns:
            成功注册的工具数量
        """
        configs = self._load_configs()
        if not configs:
            logger.info("[MCPLoader] No MCP services configured.")
            return 0

        total_registered = 0

        try:
            for config in configs:
                if config.status != "active":
                    logger.info("[MCPLoader] Skipping inactive service: '%s'", config.name)
                    continue

                # 启动服务
                client = MCPClient(config)
                if not client.start():
                    continue

                self._clients[config.id] = client

                # 发现工具
                tools = client.list_tools()
                if not tools:
                    logger.info("[MCPLoader] Service '%s' provides no tools.", config.name)
                    continue

                # 注册工具
                registered_from_service = 0
                for tool_info in tools:
                    # 名称冲突检测
                    if tool_registry.has_tool(tool_info.name):
                        logger.warning(
                            "[MCPLoader] Tool '%s' from service '%s' conflicts with existing tool — skipped.",
                            tool_info.name, config.name,
                        )
                        continue

                    # 确定 phases
                    phases: set[str] | None = None
                    if config.phases:
                        phases = set(p.lower() for p in config.phases)

                    # 创建 handler 闭包
                    handler = self._make_handler(client, tool_info.name)

                    # 注册到 ToolRegistry
                    tool_registry.register(
                        name=tool_info.name,
                        handler=handler,
                        description=tool_info.description,
                        phases=phases,
                    )

                    # 收集 schema
                    schema = {
                        "name": tool_info.name,
                        "description": tool_info.description or f"MCP tool from {config.name}",
                        "input_schema": tool_info.input_schema,
                    }
                    self._tool_schemas.append(schema)
                    self._tool_service_map[tool_info.name] = config.id
                    registered_from_service += 1
                    total_registered += 1

                logger.info(
                    "[MCPLoader] Service '%s': registered %d/%d tools.",
                    config.name, registered_from_service, len(tools),
                )

        except Exception as e:
            # 意外异常：清理已启动的服务，避免孤儿进程
            logger.error(
                "[MCPLoader] Unexpected error during load_and_register: %s. "
                "Cleaning up started services.",
                e,
            )
            self.shutdown_all()
            raise

        if total_registered > 0:
            logger.info("[MCPLoader] Total: %d MCP tools registered from %d services.",
                        total_registered, len(self._clients))
        return total_registered

    def get_tool_schemas(self) -> list[dict]:
        """返回所有 MCP 工具的 LLM schema，供追加到 Agent.self.tools。"""
        return list(self._tool_schemas)

    def shutdown_all(self) -> None:
        """关闭所有 MCP 服务子进程。多次调用安全（幂等）。"""
        if not self._clients:
            return
        for service_id, client in list(self._clients.items()):
            logger.info("[MCPLoader] Shutting down service '%s'...", client.config.name)
            client.shutdown()
        self._clients.clear()
        logger.info("[MCPLoader] All MCP services shut down.")

    # ----------------------------------------------------------
    # 内部方法
    # ----------------------------------------------------------

    def _load_configs(self) -> list[MCPServiceConfig]:
        """从 mcp_services.json 加载服务配置。"""
        if not self._config_path.exists():
            return []

        try:
            data = json.loads(self._config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("[MCPLoader] Failed to read %s: %s", self._config_path, e)
            return []

        configs = []
        for svc_data in data.get("services", []):
            try:
                configs.append(MCPServiceConfig.from_dict(svc_data))
            except (KeyError, TypeError) as e:
                logger.warning("[MCPLoader] Invalid service config: %s — %s", svc_data.get("id", "?"), e)
        return configs

    @staticmethod
    def _make_handler(client: MCPClient, tool_name: str) -> Callable[[dict], str]:
        """为 MCP 工具创建 ToolRegistry handler 闭包。"""
        def handler(args: dict) -> str:
            return client.call_tool(tool_name, args)
        return handler
