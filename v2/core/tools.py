"""
core/v2/tools.py - ToolRegistry: 工具注册与分发

设计原则:
    - 替代 harness.py 中的 14 个 if-elif 分发
    - 每个工具声明自己属于哪些阶段
    - 工具执行的副作用反映到 WorkspaceState
    - description 是 LLM 理解工具的唯一途径

当前阶段 (Phase 1): 先实现注册 + 分发机制，工具实现仍在 Harness 上。
后续阶段: 工具实现逐步迁移到独立函数。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from core.phases import Phase

logger = logging.getLogger(__name__)


@dataclass
class ToolDefinition:
    """工具定义。"""
    name: str
    handler: Callable[[dict], str]
    description: str = ""
    phases: set[str] | None = None  # None = 所有阶段可用; set = 限定阶段


class ToolRegistry:
    """
    工具注册表。

    用法:
        registry = ToolRegistry()
        registry.register("read_section", handler=harness._tool_read_section)
        result = registry.execute("read_section", {"section_id": "abstract"})
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, name: str, handler: Callable[[dict], str],
                 description: str = "",
                 phases: set[str] | None = None) -> None:
        """注册一个工具。phases=None 表示所有阶段可用。"""
        self._tools[name] = ToolDefinition(
            name=name,
            handler=handler,
            description=description,
            phases=phases,
        )

    def execute(self, name: str, args: dict) -> str:
        """执行工具，返回结果字符串。未知工具返回错误提示。"""
        tool = self._tools.get(name)
        if tool is None:
            return f"未知工具: {name}"
        return tool.handler(args)

    def has_tool(self, name: str) -> bool:
        """检查工具是否已注册。"""
        return name in self._tools

    def get_phases(self, name: str) -> set[str] | None:
        """返回指定工具的可见阶段集合。None 表示所有阶段可用。

        如果工具不存在，返回 None。
        """
        tool = self._tools.get(name)
        if tool is None:
            return None
        return tool.phases

    @property
    def tool_names(self) -> list[str]:
        """返回所有已注册工具名。"""
        return list(self._tools.keys())

    def get_tools_for_phase(self, phase_name: str) -> list[str]:
        """返回指定阶段可用的工具名列表。

        工具可用条件:
            - phases 为 None（通用工具，所有阶段可用）
            - phases 包含 phase_name（大小写不敏感比较）
        """
        normalized = phase_name.lower()
        result: list[str] = []
        for name, tool_def in self._tools.items():
            if tool_def.phases is None or normalized in tool_def.phases:
                result.append(name)
        return result

    def get_tool_schemas_for_phase(self, phase_name: str) -> list[dict[str, Any]]:
        """返回指定阶段可用的工具 schema 列表（给 LLM 看的）。

        与 get_tools_for_phase 保持一致：大小写不敏感比较。
        """
        normalized = phase_name.lower()
        schemas: list[dict[str, Any]] = []
        for name, tool_def in self._tools.items():
            if tool_def.phases is None or normalized in tool_def.phases:
                schemas.append({
                    "name": name,
                    "description": tool_def.description,
                })
        return schemas

    def __len__(self) -> int:
        return len(self._tools)
