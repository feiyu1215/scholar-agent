"""
core/tool_consistency.py — 工具双注册一致性检查

解决的问题:
    ScholarAgent 的工具系统有两个注册点:
    1. ToolRegistry (harness.py): 注册 handler 函数 + 阶段可见性
    2. SCHOLAR_TOOLS / self.tools (identity.py + agent.py): 注册 JSON schema (LLM 可见)

    这两个注册点必须手动保持同步。历史上已经出现过 3 次"handler 存在但 schema 缺失"
    导致 LLM 永远不会调用某个工具的 bug (apply_skill, request_phase_transition,
    generate_cognitive_hints)。

    本模块在 Agent 启动时执行一致性检查，确保:
    - 每个 schema 都有对应的 handler (否则 LLM 调用会失败 → ERROR)
    - 每个 handler 都有对应的 schema (否则 LLM 看不到 → WARNING)

设计决策:
    - schema 无 handler → AssertionError (启动失败，必须修复)
    - handler 无 schema → WARNING 日志 (可能是内部别名，不阻塞启动)
    - 通过 KNOWN_INTERNAL_ALIASES 豁免已知的内部别名
    - 可通过环境变量 SCHOLAR_TOOL_CONSISTENCY_CHECK=0 禁用 (CI 场景)
"""

from __future__ import annotations

import logging
import os
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.tools import ToolRegistry

logger = logging.getLogger(__name__)

# 已知的内部别名: 这些工具在 registry 中注册了 handler，
# 但故意不暴露给 LLM (通过另一个名字暴露)。
# 格式: {registry_name: "说明"}
KNOWN_INTERNAL_ALIASES = {
    "done": "mark_complete 的内部别名，LLM 通过 mark_complete 调用",
}


def check_tool_consistency(
    tool_schemas: list[dict],
    tool_registry: "ToolRegistry",
    *,
    strict: bool = True,
) -> None:
    """
    检查 tool schemas (LLM 可见) 与 tool_registry (handler 执行) 的一致性。

    Args:
        tool_schemas: 传给 LLM 的工具定义列表 (每个 dict 含 "name" 字段)
        tool_registry: 工具注册表 (含所有 handler)
        strict: True 时 schema 无 handler 会 raise AssertionError;
                False 时只打印 ERROR 日志

    Raises:
        AssertionError: strict=True 且发现 schema 无对应 handler
    """
    # Kill Switch: 允许在特殊场景下跳过检查
    if os.environ.get("SCHOLAR_TOOL_CONSISTENCY_CHECK", "1").strip().lower() in ("0", "false", "no"):
        return

    schema_names = {t["name"] for t in tool_schemas if "name" in t}
    registry_names = set(tool_registry.tool_names)

    # ============================================================
    # CHECK 1: Schema 有但 Registry 没有 → LLM 会调用一个不存在的 handler
    # 这是 P0 级别的 bug，必须阻断启动
    # ============================================================
    schema_only = schema_names - registry_names
    if schema_only:
        msg = (
            f"[ToolConsistency] FATAL: {len(schema_only)} tool(s) have schema "
            f"but NO handler registered. LLM will call them but execution will fail.\n"
            f"  Missing handlers: {sorted(schema_only)}\n"
            f"  Fix: Register handlers in harness._init_tool_registry() for these tools."
        )
        if strict:
            raise AssertionError(msg)
        else:
            print(msg, file=sys.stderr)

    # ============================================================
    # CHECK 2: Registry 有但 Schema 没有 → LLM 永远看不到这个工具
    # 可能是内部别名 (合法)，也可能是忘记加 schema (bug)
    # ============================================================
    registry_only = registry_names - schema_names - set(KNOWN_INTERNAL_ALIASES.keys())
    if registry_only:
        msg = (
            f"[ToolConsistency] WARNING: {len(registry_only)} tool(s) have handler "
            f"but NO schema. LLM cannot see or call them.\n"
            f"  Invisible tools: {sorted(registry_only)}\n"
            f"  If intentional, add to KNOWN_INTERNAL_ALIASES in tool_consistency.py.\n"
            f"  If not, add JSON schema to SCHOLAR_TOOLS in identity.py."
        )
        print(msg, file=sys.stderr)
        logger.warning(msg)

    # ============================================================
    # CHECK 3: Phase 一致性 (信息性检查，不阻断)
    # 如果一个工具的 schema 存在，但它在所有 Phase 中都不可见，
    # 那它虽然在 self.tools 中，但 _filter_tools_by_phase 永远不会选中它
    # ============================================================
    all_phases = {"initial_scan", "deep_review", "editing", "synthesis"}
    never_visible = []
    for name in schema_names & registry_names:
        if not tool_registry.has_tool(name):
            continue
        phases = tool_registry.get_phases(name)
        if phases is not None:
            # 检查是否至少在一个 Phase 中可见
            if not phases & all_phases:
                never_visible.append(name)

    if never_visible:
        msg = (
            f"[ToolConsistency] INFO: {len(never_visible)} tool(s) have schema and handler "
            f"but are not visible in any standard phase.\n"
            f"  Tools: {sorted(never_visible)}\n"
            f"  They can only be used via direct execute_tool() calls."
        )
        print(msg, file=sys.stderr)

    # 成功时的确认日志
    if not schema_only and not registry_only and not never_visible:
        logger.info(
            "[ToolConsistency] OK: %d schemas ↔ %d handlers (+ %d aliases) — all consistent",
            len(schema_names), len(registry_names), len(KNOWN_INTERNAL_ALIASES),
        )
