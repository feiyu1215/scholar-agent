"""
core/v2/mcp_bridge.py — MCP Bridge: 外部 MCP 工具的 v2 桥接层

设计原则 (EDIT-4):
    - 桥接 tools/ 目录下的 MCP 工具到 v2 的 ToolRegistry
    - 薄层 wrapper：同步接口 → 异步实现
    - 优雅降级：MCP 不可用时返回 guidance（.do 代码 + 人工执行建议）
    - 绝不 auto-modify：与 Red Line 1 对齐，统计验证结果只作 guidance
    - 不引入硬依赖：如果 tools/stata_verify.py 不存在，bridge 仍能加载

集成方式:
    Harness._init_tool_registry() 调用 register_mcp_tools(registry)
    → 注册 verify_stata 到 {"deep_review", "editing"} phases

降级场景:
    1. tools/stata_verify.py 不在 sys.path → 返回 IMPORT_UNAVAILABLE
    2. stata-mcp CLI 不可用 → 生成 .do 代码作为 guidance 返回
    3. 执行超时 / 错误 → 返回错误 + .do 代码

工具参数:
    verify_stata:
        issue: dict       — 包含 id, description, suggestion 的方法学问题
        methods_context: str (可选) — 论文方法/数据章节摘要
        provider: str (可选)
        model: str (可选)
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ─── 动态导入 tools/stata_verify ─────────────────────────────────────────

_stata_module = None
_import_error: str | None = None


def _ensure_stata_module():
    """延迟导入 tools/stata_verify.py。导入失败不阻断加载。"""
    global _stata_module, _import_error
    if _stata_module is not None:
        return True
    if _import_error is not None:
        return False

    # tools/ 在项目根目录 (v2 的上两级)
    project_root = Path(__file__).resolve().parent.parent.parent
    tools_dir = project_root / "tools"

    if not (tools_dir / "stata_verify.py").exists():
        _import_error = f"tools/stata_verify.py not found at {tools_dir}"
        logger.info("MCP bridge: stata_verify module not available — %s", _import_error)
        return False

    # 临时把 project_root 加入 sys.path（tools/ 里的模块通过 from tools.xxx 或直接导入）
    root_str = str(project_root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)

    try:
        from tools import stata_verify as _mod
        _stata_module = _mod
        logger.info("MCP bridge: stata_verify module loaded successfully")
        return True
    except Exception as e:
        _import_error = f"Import failed: {type(e).__name__}: {e}"
        logger.warning("MCP bridge: stata_verify import failed — %s", _import_error)
        return False


# ─── 同步 wrapper ────────────────────────────────────────────────────────

def _run_async(coro):
    """在同步上下文中运行 async 函数。处理 event loop 已存在的情况。"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is None:
        # 没有正在运行的 event loop，直接 asyncio.run()
        return asyncio.run(coro)
    else:
        # 已有 event loop（如在 Jupyter 或某些框架中），创建新线程
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result(timeout=60)


# ─── Tool Handler ────────────────────────────────────────────────────────

def tool_verify_stata(args: dict) -> str:
    """
    v2 tool handler: 对方法学问题进行 Stata 统计验证。

    参数:
        issue: dict — 必需。至少包含 {id, description}
        methods_context: str — 可选。论文方法/数据章节摘要
        provider: str — 可选。LLM provider
        model: str — 可选。LLM model

    返回格式化的验证结果字符串。
    遵守 Red Line 1：结果 action_type 永远是 guidance，绝不 auto-modify。
    """
    # 参数校验
    issue = args.get("issue")
    if not issue:
        return "[verify_stata 错误] 缺少必需参数 'issue'。请提供包含 {id, description} 的 dict。"
    if not isinstance(issue, dict):
        return "[verify_stata 错误] 'issue' 参数必须是 dict 类型。"
    if not issue.get("description"):
        return "[verify_stata 错误] issue.description 不能为空。"

    methods_context = args.get("methods_context", "")
    provider = args.get("provider")
    model = args.get("model")

    # 检查模块可用性
    if not _ensure_stata_module():
        return _format_unavailable(issue, reason=_import_error or "Unknown import error")

    # 调用异步验证
    try:
        result = _run_async(
            _stata_module.stata_verify(
                issue=issue,
                methods_context=methods_context,
                provider=provider,
                model=model,
            )
        )
    except Exception as e:
        return (
            f"[verify_stata 执行异常] {type(e).__name__}: {e}\n"
            f"Issue: {issue.get('description', '')[:100]}\n"
            f"建议: 请手动检查此方法学问题，或确认 Stata MCP 环境配置正确。"
        )

    # 格式化输出
    return _format_result(result)


def _format_unavailable(issue: dict, reason: str) -> str:
    """MCP 不可用时的降级输出。"""
    lines = [
        "📋 Stata 验证 [降级模式]",
        f"  状态: MCP 不可用 — {reason}",
        f"  问题: {issue.get('description', '')[:200]}",
        "",
        "  建议: 此方法学问题需要统计验证，但当前环境无法执行 Stata。",
        "  请将以下问题描述交给有 Stata 环境的研究者手动检验:",
        f"    - {issue.get('description', '')}",
    ]
    if issue.get("suggestion"):
        lines.append(f"    - 建议: {issue['suggestion']}")
    lines.append("")
    lines.append("  [Red Line 1: 即使验证结果与论文不一致，也只作为 guidance，绝不自动修改论文数据/结论]")
    return "\n".join(lines)


def _format_result(result: dict) -> str:
    """格式化 stata_verify 的完整结果。"""
    # 复用 tools/stata_verify.py 的 format_stata_result（如果可用）
    if _stata_module and hasattr(_stata_module, "format_stata_result"):
        formatted = _stata_module.format_stata_result(result)
    else:
        formatted = _format_result_fallback(result)

    # 追加 Red Line 1 提醒
    formatted += "\n\n  [Red Line 1: 验证结果仅作为 guidance — 如有 discrepancy，由作者人工决策是否修正]"
    return formatted


def _format_result_fallback(result: dict) -> str:
    """无法使用原始 format 函数时的备选格式化。"""
    status = result.get("status", "unknown")
    status_icons = {
        "verified": "✅",
        "discrepancy": "⚠️",
        "unavailable": "📋",
        "timeout": "⏱️",
        "execution_error": "❌",
    }
    icon = status_icons.get(status, "❓")
    lines = [f"{icon} Stata 验证: {status}"]

    if result.get("do_path"):
        lines.append(f"  .do 文件: {result['do_path']}")
    if result.get("guidance"):
        lines.append(f"  {result['guidance']}")
    if result.get("error_message"):
        lines.append(f"  错误: {result['error_message']}")

    return "\n".join(lines)


# ─── 注册接口 ────────────────────────────────────────────────────────────

def register_mcp_tools(registry) -> list[str]:
    """
    将所有 MCP bridge 工具注册到 ToolRegistry。

    Args:
        registry: ToolRegistry 实例

    Returns:
        已注册的工具名列表
    """
    registered = []

    registry.register(
        name="verify_stata",
        handler=tool_verify_stata,
        description=(
            "对方法学问题进行 Stata 统计验证。"
            "输入一个 issue dict（含 id, description），生成 .do 代码并尝试执行。"
            "如果 Stata 不可用则降级为 guidance 模式（输出 .do 代码供人工执行）。"
            "结果永远只作为 guidance，绝不自动修改论文。"
        ),
        phases={"deep_review", "editing"},
    )
    registered.append("verify_stata")

    logger.info("MCP bridge: registered %d tools: %s", len(registered), registered)
    return registered
