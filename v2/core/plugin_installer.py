"""
core/plugin_installer.py — 统一插件安装器 (LLM 可调用)

设计目标:
    让 LLM 能引导用户安装/卸载/查看三种类型的插件:
    1. Knowledge Skill: 领域知识 (Markdown 文件，注入 system prompt)
    2. Action Skill: 操作型技能 (Python handler + tool schema)
    3. MCP Service: 外部 MCP 服务器连接配置

    LLM 通过 `manage_plugins` 工具调用本模块，用户无需了解内部文件结构。

架构:
    manage_plugins (tool handler)
        |
        +-- action="list"     -> 列出已安装插件
        +-- action="install"  -> 安装新插件
        +-- action="uninstall" -> 卸载插件
        +-- action="inspect"  -> 查看插件详情
        |
        +-- plugin_type="knowledge_skill" -> 知识型 Skill
        +-- plugin_type="action_skill"    -> 操作型 Skill
        +-- plugin_type="mcp_service"     -> MCP 服务

设计决策:
    - 安装器本身是一个 tool handler，注册到 ToolRegistry
    - 安装后需要重启 Agent 才能生效 (明确告知用户)
    - 知识型 Skill 安装最简单: 只需要 name + description + content
    - MCP 服务安装写入 mcp_config.json，下次启动时加载
    - 所有操作都有 dry_run 模式 (预览不执行)
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SKILLS_DIR = _PROJECT_ROOT / "skills"
_MCP_CONFIG_PATH = _PROJECT_ROOT / "config" / "mcp_services.json"


# ==============================================================
# 数据类
# ==============================================================

@dataclass
class PluginInfo:
    """插件信息摘要。"""
    id: str
    name: str
    plugin_type: str  # "knowledge_skill" | "action_skill" | "mcp_service"
    status: str  # "active" | "inactive"
    description: str = ""
    version: str = "1.0.0"
    installed_at: str = ""


# ==============================================================
# Tool Handler: manage_plugins
# ==============================================================

def tool_manage_plugins(args: dict) -> str:
    """
    统一插件管理工具的 handler。

    LLM 通过此工具引导用户安装/卸载/查看插件。

    Args (from LLM tool_call):
        action: "list" | "install" | "uninstall" | "inspect"
        plugin_type: "knowledge_skill" | "action_skill" | "mcp_service" (install/uninstall 必填)
        plugin_id: 插件 ID (uninstall/inspect 必填)
        config: 安装配置 (install 必填，内容因 plugin_type 而异)
        dry_run: bool (默认 False，True 时只预览不执行)

    Returns:
        操作结果的描述字符串。
    """
    action = args.get("action", "").strip().lower()

    if action == "list":
        return _handle_list(args)
    elif action == "install":
        return _handle_install(args)
    elif action == "uninstall":
        return _handle_uninstall(args)
    elif action == "inspect":
        return _handle_inspect(args)
    else:
        return (
            "[manage_plugins] 未知 action。支持的操作:\n"
            "- list: 列出已安装插件\n"
            "- install: 安装新插件\n"
            "- uninstall: 卸载插件\n"
            "- inspect: 查看插件详情\n\n"
            "示例: manage_plugins(action='list')"
        )


# ==============================================================
# Action Handlers
# ==============================================================

def _handle_list(args: dict) -> str:
    """列出已安装的插件。"""
    plugin_type = (args.get("plugin_type") or "").strip().lower() or None

    # 别名归一化: "tool" → action_skill, "skill" → 只显示 skills (不含 mcp)
    _skill_only = False
    if plugin_type == "tool":
        plugin_type = "action_skill"
    elif plugin_type == "skill":
        _skill_only = True  # 显示 knowledge + action，不显示 mcp
        plugin_type = None

    plugins: list[PluginInfo] = []

    # 1. 从 registry.json 读取 Skills
    registry = _load_skill_registry()
    for skill in registry.get("skills", []):
        ptype = "action_skill" if skill.get("type") == "action" else "knowledge_skill"
        if plugin_type and ptype != plugin_type:
            continue
        plugins.append(PluginInfo(
            id=skill["id"],
            name=skill.get("name", skill["id"]),
            plugin_type=ptype,
            status=skill.get("status", "active"),
            description=skill.get("description", ""),
            version=skill.get("version", "?"),
            installed_at=skill.get("installed_at", "?"),
        ))

    # 2. 从 mcp_services.json 读取 MCP 服务
    if (not plugin_type and not _skill_only) or plugin_type == "mcp_service":
        mcp_config = _load_mcp_config()
        for svc in mcp_config.get("services", []):
            plugins.append(PluginInfo(
                id=svc["id"],
                name=svc.get("name", svc["id"]),
                plugin_type="mcp_service",
                status=svc.get("status", "active"),
                description=svc.get("description", ""),
                version=svc.get("version", "1.0.0"),
                installed_at=svc.get("installed_at", "?"),
            ))

    if not plugins:
        return "[manage_plugins] 当前没有已安装的插件。"

    # 格式化输出
    lines = [f"已安装插件 ({len(plugins)} 个):"]
    lines.append("")

    # 按类型分组
    by_type: dict[str, list[PluginInfo]] = {}
    for p in plugins:
        by_type.setdefault(p.plugin_type, []).append(p)

    type_labels = {
        "knowledge_skill": "知识型 Skill",
        "action_skill": "Tool / 操作型 Skill",
        "mcp_service": "MCP 服务",
    }

    for ptype, items in by_type.items():
        lines.append(f"## {type_labels.get(ptype, ptype)} ({len(items)} 个)")
        for p in items:
            status_icon = "✓" if p.status == "active" else "○"
            lines.append(f"  {status_icon} {p.id} — {p.name} (v{p.version})")
            if p.description:
                lines.append(f"    {p.description[:80]}")
        lines.append("")

    return "\n".join(lines)


def _handle_install(args: dict) -> str:
    """安装新插件。"""
    plugin_type = (args.get("plugin_type") or "").strip().lower()
    config = args.get("config") or {}  # 防御 None
    dry_run = args.get("dry_run", False)

    if not plugin_type:
        return (
            "[manage_plugins install] 缺少 plugin_type。支持:\n"
            "- skill: 安装 Skill (纯知识型只需 name+description+content；带脚本的需要额外提供 handler_code+tool_schema)\n"
            "- tool: 安装独立工具 (需要 name, description, handler_code, tool_schema)\n"
            "- mcp_service: 安装 MCP 服务 (需要 name, command, args)"
        )

    # --- 别名归一化 ---
    # "tool" 是 "action_skill" 的用户友好别名
    # "skill" 根据 config 内容自动判断: 有 handler_code → action_skill, 否则 → knowledge_skill
    if plugin_type == "tool":
        plugin_type = "action_skill"
    elif plugin_type == "skill":
        if not config:
            # config 为空时，返回统一的 skill 模板，让 LLM 判断用户意图
            return _get_install_template("skill")
        # config 非空时，根据内容自动路由
        if config.get("handler_code") or config.get("tool_schema"):
            plugin_type = "action_skill"
        else:
            plugin_type = "knowledge_skill"

    if not config:
        return _get_install_template(plugin_type)

    if plugin_type == "knowledge_skill":
        return _install_knowledge_skill(config, dry_run)
    elif plugin_type == "action_skill":
        return _install_action_skill(config, dry_run)
    elif plugin_type == "mcp_service":
        return _install_mcp_service(config, dry_run)
    else:
        return f"[manage_plugins install] 不支持的 plugin_type: {plugin_type}"


def _handle_uninstall(args: dict) -> str:
    """卸载插件。

    统一在本模块内处理卸载逻辑，不依赖 skills.installer.SkillInstaller，
    避免两套代码操作同一个 registry.json 的竞态问题。
    """
    plugin_id = args.get("plugin_id", "").strip()
    if not plugin_id:
        return "[manage_plugins uninstall] 缺少 plugin_id。请指定要卸载的插件 ID。"

    # 1. 尝试从 Skill registry 卸载
    registry = _load_skill_registry()
    skills = registry.get("skills", [])
    target_skill = None
    for i, skill in enumerate(skills):
        if skill.get("id") == plugin_id:
            target_skill = skills.pop(i)
            break

    if target_skill:
        # 删除关联文件
        skill_file = target_skill.get("file")
        if skill_file:
            file_path = _SKILLS_DIR / skill_file
            if file_path.exists():
                file_path.unlink()

        # 如果是 action skill，删除 handler 文件
        if target_skill.get("type") == "action":
            tools_list = target_skill.get("tools", [])
            for tool_entry in tools_list:
                handler_ref = tool_entry.get("handler", "")
                if "::" in handler_ref:
                    handler_file = handler_ref.split("::")[0]
                    handler_path = _SKILLS_DIR / handler_file
                    if handler_path.exists():
                        handler_path.unlink()

        _save_skill_registry(registry)
        return f"[manage_plugins] 已卸载 Skill '{plugin_id}'。重启 Agent 后生效。"

    # 2. 尝试从 MCP config 卸载
    mcp_config = _load_mcp_config()
    services = mcp_config.get("services", [])
    for i, svc in enumerate(services):
        if svc.get("id") == plugin_id:
            services.pop(i)
            _save_mcp_config(mcp_config)
            return f"[manage_plugins] 已卸载 MCP 服务 '{plugin_id}'。重启 Agent 后生效。"

    return f"[manage_plugins uninstall] 未找到 ID 为 '{plugin_id}' 的插件。"


def _handle_inspect(args: dict) -> str:
    """查看插件详情。"""
    plugin_id = args.get("plugin_id", "").strip()
    if not plugin_id:
        return "[manage_plugins inspect] 缺少 plugin_id。"

    # 从 Skill registry 查找
    registry = _load_skill_registry()
    for skill in registry.get("skills", []):
        if skill["id"] == plugin_id:
            return json.dumps(skill, ensure_ascii=False, indent=2)

    # 从 MCP config 查找
    mcp_config = _load_mcp_config()
    for svc in mcp_config.get("services", []):
        if svc["id"] == plugin_id:
            return json.dumps(svc, ensure_ascii=False, indent=2)

    return f"[manage_plugins inspect] 未找到 ID 为 '{plugin_id}' 的插件。"


# ==============================================================
# Install Implementations
# ==============================================================

def _install_knowledge_skill(config: dict, dry_run: bool) -> str:
    """
    安装知识型 Skill。

    所需 config 字段:
        name: str — 显示名称
        description: str — 描述
        content: str — Markdown 内容 (将注入 system prompt)
        tags: list[str] — 标签 (可选，默认 ["user_custom"])
        applicable_paper_types: list[str] — 适用论文类型 (可选，默认 all)
        applicable_phases: list[str] — 适用阶段 (可选，默认 ["deep_review", "synthesis"])
        priority_hint: int — 优先级 0-100 (可选，默认 65)
    """
    # 参数校验
    name = config.get("name", "").strip()
    description = config.get("description", "").strip()
    content = config.get("content", "").strip()

    if not name:
        return "[install knowledge_skill] 缺少 'name' 字段。"
    if not description:
        return "[install knowledge_skill] 缺少 'description' 字段。"
    if not content:
        return "[install knowledge_skill] 缺少 'content' 字段 (Markdown 知识内容)。"

    # 生成 ID
    skill_id = _generate_id(name)

    # 检查冲突
    registry = _load_skill_registry()
    existing_ids = {s["id"] for s in registry.get("skills", [])}
    if skill_id in existing_ids:
        return f"[install knowledge_skill] ID '{skill_id}' 已存在。请换一个名称或先卸载旧版本。"

    # 构建安装包
    tags = config.get("tags", ["user_custom"])
    applicable_paper_types = config.get("applicable_paper_types",
        ["empirical", "empirical_econ", "structural_econ", "theoretical", "review", "mixed"])
    applicable_phases = config.get("applicable_phases", ["deep_review", "synthesis"])
    priority_hint = config.get("priority_hint", 65)
    token_estimate = _estimate_tokens(content)

    if dry_run:
        return (
            f"[dry_run] 将安装知识型 Skill:\n"
            f"  ID: {skill_id}\n"
            f"  Name: {name}\n"
            f"  Description: {description}\n"
            f"  Content length: {len(content)} chars (~{token_estimate} tokens)\n"
            f"  Tags: {tags}\n"
            f"  Phases: {applicable_phases}\n"
            f"  Priority: {priority_hint}\n\n"
            f"确认安装请去掉 dry_run=True 重新调用。"
        )

    # 写入 content.md
    content_path = _SKILLS_DIR / f"{skill_id}.md"
    content_path.write_text(content, encoding="utf-8")

    # 更新 registry.json
    entry = {
        "id": skill_id,
        "version": "1.0.0",
        "status": "active",
        "installed_at": date.today().isoformat(),
        "last_updated": date.today().isoformat(),
        "type": "knowledge",
        "file": f"{skill_id}.md",
        "name": name,
        "description": description,
        "tags": tags,
        "applicable_paper_types": applicable_paper_types,
        "applicable_phases": applicable_phases,
        "token_estimate": token_estimate,
        "priority_hint": priority_hint,
    }
    registry.setdefault("skills", []).append(entry)
    _save_skill_registry(registry)

    return (
        f"[manage_plugins] 知识型 Skill '{name}' 安装成功!\n"
        f"  ID: {skill_id}\n"
        f"  文件: skills/{skill_id}.md\n"
        f"  适用阶段: {applicable_phases}\n\n"
        f"重启 Agent 后，该知识将在对应阶段自动注入 system prompt。"
    )


def _install_action_skill(config: dict, dry_run: bool) -> str:
    """
    安装操作型 Skill。

    所需 config 字段:
        name: str — 显示名称
        description: str — 描述
        handler_code: str — Python handler 代码
        tool_schema: dict — 工具的 JSON schema (含 name, description, input_schema)
        tags: list[str] — 标签 (可选)
        applicable_phases: list[str] — 适用阶段 (可选，默认 all)
    """
    name = config.get("name", "").strip()
    description = config.get("description", "").strip()
    handler_code = config.get("handler_code", "").strip()
    tool_schema = config.get("tool_schema", {})

    if not name:
        return "[install action_skill] 缺少 'name' 字段。"
    if not description:
        return "[install action_skill] 缺少 'description' 字段。"
    if not handler_code:
        return "[install action_skill] 缺少 'handler_code' 字段 (Python 代码)。"
    if not tool_schema or "name" not in tool_schema:
        return "[install action_skill] 缺少 'tool_schema' 字段 (需含 name, description, input_schema)。"

    # --- 安全校验 ---
    if not config.get("_skip_safety_check", False):
        safety_result = _validate_handler_code(handler_code)
        if safety_result:
            return f"[install action_skill] 安全校验失败:\n{safety_result}"

    skill_id = _generate_id(name)
    tool_name = tool_schema["name"]
    handler_func_name = config.get("handler_function", f"handle_{tool_name}")

    # 检查冲突
    registry = _load_skill_registry()
    existing_ids = {s["id"] for s in registry.get("skills", [])}
    if skill_id in existing_ids:
        return f"[install action_skill] ID '{skill_id}' 已存在。"

    # --- 强制 dry_run 首次调用 (安全确认) ---
    if not config.get("_confirmed", False):
        return (
            f"[安全确认] 将安装操作型 Skill (含可执行代码):\n"
            f"  ID: {skill_id}\n"
            f"  Tool name: {tool_name}\n"
            f"  Handler function: {handler_func_name}\n"
            f"  Handler code: {len(handler_code)} chars\n\n"
            f"⚠️ 操作型 Skill 包含可执行 Python 代码，安装后将在 Agent 启动时加载。\n"
            f"请确认用户已审阅代码内容。确认安装请在 config 中添加 '_confirmed': true。"
        )

    if dry_run:
        return (
            f"[dry_run] 将安装操作型 Skill:\n"
            f"  ID: {skill_id}\n"
            f"  Tool name: {tool_name}\n"
            f"  Handler function: {handler_func_name}\n"
            f"  Handler code: {len(handler_code)} chars\n\n"
            f"确认安装请去掉 dry_run=True。"
        )

    # 写入 handler.py
    handlers_dir = _SKILLS_DIR / "skill_handlers"
    handlers_dir.mkdir(parents=True, exist_ok=True)
    handler_path = handlers_dir / f"{skill_id}.py"
    handler_path.write_text(handler_code, encoding="utf-8")

    # 写入 content.md (操作型 Skill 的说明文档)
    content_path = _SKILLS_DIR / f"{skill_id}.md"
    content_path.write_text(
        f"# {name}\n\n{description}\n\n"
        f"## Tool: {tool_name}\n\n"
        f"This is an action skill that provides the `{tool_name}` tool.\n",
        encoding="utf-8",
    )

    # 更新 registry.json
    applicable_phases = config.get("applicable_phases",
        ["initial_scan", "deep_review", "editing", "synthesis"])
    entry = {
        "id": skill_id,
        "version": "1.0.0",
        "status": "active",
        "installed_at": date.today().isoformat(),
        "last_updated": date.today().isoformat(),
        "type": "action",
        "file": f"{skill_id}.md",
        "name": name,
        "description": description,
        "tags": config.get("tags", ["user_custom", "action"]),
        "applicable_paper_types": config.get("applicable_paper_types",
            ["empirical", "empirical_econ", "structural_econ", "theoretical", "review", "mixed"]),
        "applicable_phases": applicable_phases,
        "token_estimate": 200,
        "priority_hint": config.get("priority_hint", 60),
        "tools": [{
            "name": tool_name,
            "description": tool_schema.get("description", description),
            "input_schema": tool_schema.get("input_schema", {"type": "object", "properties": {}}),
            "handler": f"skill_handlers/{skill_id}.py::{handler_func_name}",
        }],
    }
    registry.setdefault("skills", []).append(entry)
    _save_skill_registry(registry)

    return (
        f"[manage_plugins] 操作型 Skill '{name}' 安装成功!\n"
        f"  ID: {skill_id}\n"
        f"  Tool: {tool_name}\n"
        f"  Handler: skills/skill_handlers/{skill_id}.py::{handler_func_name}\n\n"
        f"重启 Agent 后，LLM 将能看到并调用 `{tool_name}` 工具。"
    )


def _install_mcp_service(config: dict, dry_run: bool) -> str:
    """
    安装 MCP 服务。

    所需 config 字段:
        name: str — 服务显示名称
        description: str — 描述
        command: str — 启动命令 (如 "npx", "python3")
        args: list[str] — 命令参数 (如 ["-m", "mcp_server_stata"])
        env: dict[str, str] — 环境变量 (可选)
        tools_filter: list[str] — 只暴露这些工具 (可选，默认暴露全部)
        phases: list[str] — 工具可见阶段 (可选，默认 all)
    """
    name = config.get("name", "").strip()
    description = config.get("description", "").strip()
    command = config.get("command", "").strip()
    cmd_args = config.get("args", [])

    if not name:
        return "[install mcp_service] 缺少 'name' 字段。"
    if not command:
        return "[install mcp_service] 缺少 'command' 字段 (MCP server 启动命令)。"

    service_id = _generate_id(name)

    # 检查冲突
    mcp_config = _load_mcp_config()
    existing_ids = {s["id"] for s in mcp_config.get("services", [])}
    if service_id in existing_ids:
        return f"[install mcp_service] ID '{service_id}' 已存在。"

    if dry_run:
        return (
            f"[dry_run] 将安装 MCP 服务:\n"
            f"  ID: {service_id}\n"
            f"  Name: {name}\n"
            f"  Command: {command} {' '.join(cmd_args)}\n"
            f"  Description: {description}\n\n"
            f"确认安装请去掉 dry_run=True。"
        )

    # 写入 mcp_services.json
    entry = {
        "id": service_id,
        "name": name,
        "description": description or f"MCP service: {name}",
        "status": "active",
        "installed_at": date.today().isoformat(),
        "version": "1.0.0",
        "command": command,
        "args": cmd_args,
        "env": config.get("env", {}),
        "tools_filter": config.get("tools_filter", []),
        "phases": config.get("phases", []),  # 空 = 所有阶段
    }
    mcp_config.setdefault("services", []).append(entry)
    _save_mcp_config(mcp_config)

    return (
        f"[manage_plugins] MCP 服务 '{name}' 安装成功!\n"
        f"  ID: {service_id}\n"
        f"  Command: {command} {' '.join(cmd_args)}\n"
        f"  Config: config/mcp_services.json\n\n"
        f"重启 Agent 后，该 MCP 服务将自动启动并暴露其工具给 LLM。"
    )


# ==============================================================
# Install Templates (引导 LLM 收集信息)
# ==============================================================

def _get_install_template(plugin_type: str) -> str:
    """返回安装所需的配置模板，引导 LLM 向用户收集信息。"""
    templates = {
        "skill": (
            "[manage_plugins] 安装 Skill——请先判断用户要安装的是哪种类型:\n\n"
            "## 类型 A: 纯知识型 Skill (注入审稿 prompt)\n"
            "用户提供的是审稿规则、领域知识、检查清单等文本内容，没有可执行代码。\n"
            "所需字段:\n"
            "```json\n"
            "{\n"
            '  "name": "Skill 名称",\n'
            '  "description": "一句话描述",\n'
            '  "content": "Markdown 格式的知识内容"\n'
            "}\n"
            "```\n\n"
            "## 类型 B: 带脚本的 Skill (注册为 LLM 可调用工具)\n"
            "用户提供的 Skill 包含可执行的 Python 代码，需要注册为工具让 LLM 调用。\n"
            "所需字段:\n"
            "```json\n"
            "{\n"
            '  "name": "Skill 名称",\n'
            '  "description": "描述",\n'
            '  "handler_code": "def handle_xxx(args, state):\\n    ...",\n'
            '  "tool_schema": {"name": "tool_name", "description": "...", "input_schema": {...}}\n'
            "}\n"
            "```\n\n"
            "判断方法: 问用户'这个 Skill 是提供知识参考，还是需要执行代码/脚本？'\n"
            "如果用户不确定，看他给的内容——有 Python 代码就是类型 B，纯文本就是类型 A。\n"
            "系统会根据 config 中是否包含 handler_code 自动路由，你只需收集信息后传入 config。"
        ),
        "knowledge_skill": (
            "[manage_plugins] 安装知识型 Skill 需要以下信息:\n\n"
            "```json\n"
            "{\n"
            '  "name": "Skill 显示名称",\n'
            '  "description": "一句话描述这个知识做什么",\n'
            '  "content": "Markdown 格式的知识内容 (将注入审稿 prompt)",\n'
            '  "tags": ["标签1", "标签2"],\n'
            '  "applicable_phases": ["deep_review", "synthesis"],\n'
            '  "priority_hint": 65\n'
            "}\n"
            "```\n\n"
            "最简安装只需 name + description + content 三个字段。\n"
            "请向用户收集这些信息后，用 config 参数传入。"
        ),
        "action_skill": (
            "[manage_plugins] 安装 Tool / 操作型 Skill 需要以下信息:\n\n"
            "```json\n"
            "{\n"
            '  "name": "工具显示名称",\n'
            '  "description": "描述 (LLM 会看到这段文字来决定何时调用)",\n'
            '  "handler_code": "def handle_xxx(args, state):\\n    ...",\n'
            '  "tool_schema": {\n'
            '    "name": "tool_name",\n'
            '    "description": "LLM 看到的工具描述",\n'
            '    "input_schema": {"type": "object", "properties": {...}}\n'
            "  }\n"
            "}\n"
            "```\n\n"
            "handler_code 必须包含一个函数，签名为 (args: dict, state: Any) -> str。\n"
            "请向用户收集这些信息后，用 config 参数传入。"
        ),
        "mcp_service": (
            "[manage_plugins] 安装 MCP 服务需要以下信息:\n\n"
            "```json\n"
            "{\n"
            '  "name": "服务名称",\n'
            '  "description": "描述",\n'
            '  "command": "启动命令 (如 npx, python3, node)",\n'
            '  "args": ["参数1", "参数2"],\n'
            '  "env": {"KEY": "value"},\n'
            '  "phases": ["deep_review", "editing"]\n'
            "}\n"
            "```\n\n"
            "最简安装只需 name + command。\n"
            "请向用户收集这些信息后，用 config 参数传入。"
        ),
    }
    return templates.get(plugin_type,
        f"[manage_plugins] 不支持的 plugin_type: {plugin_type}")


# ==============================================================
# Helpers
# ==============================================================

def _estimate_tokens(text: str) -> int:
    """估算文本的 token 数量。

    策略:
        - ASCII 字符: ~4 字符/token (英文文本的经验值)
        - 非 ASCII 字符 (中文等): ~1.5 字符/token (CJK 字符通常 1-2 token/字)
        - 最低返回 100
    """
    ascii_chars = sum(1 for c in text if ord(c) < 128)
    non_ascii_chars = len(text) - ascii_chars

    ascii_tokens = ascii_chars / 4.0
    non_ascii_tokens = non_ascii_chars * 1.5  # 中文每字约 1-2 token，取中间值

    return max(100, int(ascii_tokens + non_ascii_tokens))


# 危险函数/模块黑名单 (在 handler_code 中禁止出现)
_DANGEROUS_CALLS: set[str] = {
    "os.system", "os.popen", "os.exec", "os.execl", "os.execle",
    "os.execlp", "os.execv", "os.execve", "os.execvp", "os.execvpe",
    "os.spawn", "os.remove", "os.unlink", "os.rmdir", "os.removedirs",
    "subprocess.run", "subprocess.call", "subprocess.Popen",
    "subprocess.check_output", "subprocess.check_call",
    "eval", "exec", "compile", "__import__",
    "shutil.rmtree", "shutil.move",
    "open",  # 文件操作需要审慎
}

# 危险 import 模块
_DANGEROUS_IMPORTS: set[str] = {
    "subprocess", "shutil", "ctypes", "socket", "http",
    "urllib", "requests", "httpx", "aiohttp",
}


def _validate_handler_code(code: str) -> str:
    """
    校验 handler 代码的安全性。

    Returns:
        空字符串表示通过；非空字符串为错误描述。
    """
    import ast

    # Step 1: AST 解析 — 确保是合法 Python
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return f"代码语法错误 (非合法 Python): {e}"

    # Step 2: 检查危险调用
    warnings: list[str] = []

    for node in ast.walk(tree):
        # 检查 import 语句
        if isinstance(node, ast.Import):
            for alias in node.names:
                module_root = alias.name.split(".")[0]
                if module_root in _DANGEROUS_IMPORTS:
                    warnings.append(f"  - 危险 import: '{alias.name}' (line {node.lineno})")
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                module_root = node.module.split(".")[0]
                if module_root in _DANGEROUS_IMPORTS:
                    warnings.append(f"  - 危险 import: 'from {node.module}' (line {node.lineno})")

        # 检查函数调用
        elif isinstance(node, ast.Call):
            call_name = _get_call_name(node)
            if call_name and call_name in _DANGEROUS_CALLS:
                warnings.append(f"  - 危险调用: '{call_name}()' (line {node.lineno})")

        # 检查 Name 节点中的 eval/exec
        elif isinstance(node, ast.Name):
            if node.id in ("eval", "exec", "compile", "__import__"):
                warnings.append(f"  - 危险内置函数引用: '{node.id}' (line {node.lineno})")

    if warnings:
        return (
            "检测到以下潜在危险操作:\n"
            + "\n".join(warnings)
            + "\n\n如果确认这些操作是安全的，请在 config 中添加 '_skip_safety_check': true。"
        )

    return ""


def _get_call_name(node) -> str:
    """从 AST Call 节点提取函数名 (如 'os.system')。"""
    import ast
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    elif isinstance(func, ast.Attribute):
        parts = []
        current = func
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
        return ".".join(reversed(parts))
    return ""


def _generate_id(name: str) -> str:
    """从名称生成合法的 skill ID (小写字母+数字+下划线)。

    策略:
        1. 提取 ASCII 部分作为可读前缀 (最多 48 字符)
        2. 如果原始名称包含非 ASCII 字符或可读前缀为空，
           追加 '_' + md5(原始名称)[:8] 确保唯一性
        3. 纯 ASCII 名称且清洗后非空时，不加 hash (保持简洁)
    """
    # 提取 ASCII 可读部分
    ascii_part = re.sub(r"[^a-z0-9_\s]", "", name.lower())
    ascii_part = re.sub(r"\s+", "_", ascii_part.strip())
    ascii_part = re.sub(r"_+", "_", ascii_part)  # 合并连续下划线
    ascii_part = ascii_part[:48]  # 留空间给 hash 后缀

    # 判断是否需要 hash 后缀
    has_non_ascii = any(ord(c) > 127 for c in name)
    if not ascii_part or has_non_ascii:
        # 用原始名称的 md5 前 8 位确保唯一性
        name_hash = hashlib.md5(name.encode("utf-8")).hexdigest()[:8]
        if ascii_part:
            return f"{ascii_part}_{name_hash}"
        else:
            return f"plugin_{name_hash}"

    return ascii_part or "unnamed_plugin"


def _load_skill_registry() -> dict:
    """加载 skills/registry.json。"""
    registry_path = _SKILLS_DIR / "registry.json"
    if not registry_path.exists():
        return {"version": "1.1", "skills": []}
    try:
        return json.loads(registry_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"version": "1.1", "skills": []}


def _save_skill_registry(registry: dict) -> None:
    """保存 skills/registry.json。"""
    registry_path = _SKILLS_DIR / "registry.json"
    registry_path.write_text(
        json.dumps(registry, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _load_mcp_config() -> dict:
    """加载 config/mcp_services.json。"""
    if not _MCP_CONFIG_PATH.exists():
        return {"version": "1.0", "services": []}
    try:
        return json.loads(_MCP_CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"version": "1.0", "services": []}


def _save_mcp_config(config: dict) -> None:
    """保存 config/mcp_services.json。"""
    _MCP_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _MCP_CONFIG_PATH.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


# ==============================================================
# Tool Schema (给 LLM 看的 JSON schema)
# ==============================================================

MANAGE_PLUGINS_SCHEMA: dict = {
    "name": "manage_plugins",
    "description": (
        "管理 ScholarAgent 的扩展能力——安装、卸载、查看 Skill、Tool 和 MCP 服务。"
        "\n\n路由指南（重要）："
        "\n- 用户说'安装 skill'时：如果用户提供的是纯知识/文档/审稿规则，用 plugin_type='skill'；"
        "如果用户提供的 skill 包含可执行脚本/代码，也用 plugin_type='skill'（系统会根据是否有 handler_code 自动判断）。"
        "\n- 用户说'安装 tool/工具'时：用 plugin_type='tool'，需要收集 handler_code 和 tool_schema。"
        "\n- 用户说'连接 MCP 服务'时：用 plugin_type='mcp_service'。"
        "\n\n本质上：skill = 知识注入 prompt OR 知识+脚本工具的包；tool = 独立的可调用工具函数。"
        "安装后需要重启 Agent 才能生效。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "install", "uninstall", "inspect"],
                "description": "操作类型: list(列出插件), install(安装), uninstall(卸载), inspect(查看详情)",
            },
            "plugin_type": {
                "type": "string",
                "enum": ["skill", "tool", "mcp_service"],
                "description": (
                    "插件类型 (install/uninstall 时必填)。"
                    "'skill': Skill（纯知识型 or 带脚本的，系统自动判断）；"
                    "'tool': 独立工具（用户只想加一个 LLM 可调用的函数）；"
                    "'mcp_service': 外部 MCP 服务器连接。"
                    "兼容旧值 'knowledge_skill'/'action_skill' 但不推荐直接使用。"
                ),
            },
            "plugin_id": {
                "type": "string",
                "description": "插件 ID (uninstall/inspect 时必填)",
            },
            "config": {
                "type": "object",
                "description": (
                    "安装配置 (install 时必填)。"
                    "skill(纯知识): {name, description, content}；"
                    "skill(带脚本) 或 tool: {name, description, handler_code, tool_schema}；"
                    "mcp_service: {name, command, args}"
                ),
            },
            "dry_run": {
                "type": "boolean",
                "description": "预览模式: True 时只显示将要执行的操作，不实际安装",
            },
        },
        "required": ["action"],
    },
}
