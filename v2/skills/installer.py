"""
skills/installer.py — Skill 安装/卸载流程

B6: 最小可工作的 Skill 包安装原型。

职责:
    - validate(): 验证 Skill 包合法性（不修改任何文件）
    - install(): 验证 → 复制文件 → 注册到 registry.json
    - uninstall(): 从 registry 移除 → 删除文件

设计原则:
    - 安装失败不留残留（原子性：要么全部成功，要么回滚）
    - 错误信息具体可操作（告诉用户哪里错了、怎么修）
    - 不依赖 LLM，纯 rule-based 逻辑
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ==============================================================
# 常量
# ==============================================================

_VALID_ID_PATTERN = re.compile(r"^[a-z0-9_]{1,64}$")
_VALID_TYPES = frozenset({"knowledge", "action"})
_REQUIRED_FIELDS = ("id", "type", "name", "description", "version", "tags",
                    "applicable_paper_types", "applicable_phases",
                    "token_estimate", "priority_hint")


# ==============================================================
# 数据类
# ==============================================================

@dataclass
class InstallResult:
    """安装结果。"""

    success: bool
    skill_id: str = ""
    errors: list[str] = field(default_factory=list)
    message: str = ""


# ==============================================================
# SkillInstaller
# ==============================================================

class SkillInstaller:
    """Skill 包安装/卸载管理器。

    Usage:
        installer = SkillInstaller(Path("v2/skills"))
        errors = installer.validate(Path("/tmp/my-skill"))
        if not errors:
            result = installer.install(Path("/tmp/my-skill"))
    """

    def __init__(self, skills_dir: Path) -> None:
        """初始化安装器。

        Args:
            skills_dir: v2/skills/ 目录路径（registry.json 所在目录）。
        """
        self._skills_dir = skills_dir
        self._registry_path = skills_dir / "registry.json"
        self._handlers_dir = skills_dir / "skill_handlers"

    # ----------------------------------------------------------
    # Public API
    # ----------------------------------------------------------

    def validate(self, skill_dir: Path) -> list[str]:
        """验证 Skill 包合法性。

        仅检查，不修改任何文件。

        Args:
            skill_dir: Skill 包目录路径。

        Returns:
            错误列表。空列表表示验证通过。
        """
        errors: list[str] = []

        # 1. 目录存在性
        if not skill_dir.exists():
            errors.append(f"Skill directory does not exist: {skill_dir}")
            return errors
        if not skill_dir.is_dir():
            errors.append(f"Path is not a directory: {skill_dir}")
            return errors

        # 2. skill.json 存在性
        skill_json_path = skill_dir / "skill.json"
        if not skill_json_path.exists():
            errors.append("Missing required file: skill.json")
            return errors

        # 3. 解析 skill.json
        try:
            skill_data = json.loads(skill_json_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"skill.json is not valid JSON: {exc}")
            return errors

        if not isinstance(skill_data, dict):
            errors.append("skill.json root must be a JSON object")
            return errors

        # 4. 必填字段检查
        for field_name in _REQUIRED_FIELDS:
            if field_name not in skill_data:
                errors.append(f"Missing required field: '{field_name}'")

        if errors:
            return errors

        # 5. 字段类型和值验证
        errors.extend(self._validate_fields(skill_data))

        # 6. content.md 存在性
        content_path = skill_dir / "content.md"
        if not content_path.exists():
            errors.append("Missing required file: content.md")

        # 7. Action Skill 额外检查
        if skill_data.get("type") == "action":
            handler_path = skill_dir / "handler.py"
            if not handler_path.exists():
                errors.append(
                    "Action Skill requires handler.py but file is missing"
                )
            # tools 字段检查
            tools = skill_data.get("tools")
            if not tools:
                errors.append(
                    "Action Skill requires 'tools' array in skill.json"
                )
            elif isinstance(tools, list):
                for i, tool in enumerate(tools):
                    if not isinstance(tool, dict):
                        errors.append(f"tools[{i}] must be an object")
                        continue
                    for req in ("name", "description", "input_schema", "handler"):
                        if req not in tool:
                            errors.append(f"tools[{i}] missing required field: '{req}'")

        return errors

    def install(self, skill_dir: Path) -> InstallResult:
        """安装 Skill 包。

        流程: validate → 检查 id 冲突 → 复制文件 → 更新 registry.json。
        失败时回滚已复制的文件。

        Args:
            skill_dir: Skill 包目录路径。

        Returns:
            InstallResult 包含成功/失败状态和详细信息。
        """
        # 1. 验证
        errors = self.validate(skill_dir)
        if errors:
            return InstallResult(
                success=False,
                errors=errors,
                message="Validation failed",
            )

        # 2. 读取 skill.json
        skill_data = json.loads(
            (skill_dir / "skill.json").read_text(encoding="utf-8")
        )
        skill_id = skill_data["id"]

        # 3. 检查 id 冲突
        registry = self._load_registry()
        existing_ids = {s["id"] for s in registry.get("skills", [])}
        if skill_id in existing_ids:
            return InstallResult(
                success=False,
                skill_id=skill_id,
                errors=[
                    f"Skill id '{skill_id}' already exists in registry. "
                    f"Uninstall it first with uninstall('{skill_id}')."
                ],
                message="ID conflict",
            )

        # 4. 复制文件（记录已复制文件用于回滚）
        copied_files: list[Path] = []
        try:
            # 4a. 复制 content.md
            content_src = skill_dir / "content.md"
            content_dest = self._skills_dir / f"{skill_id}.md"
            shutil.copy2(content_src, content_dest)
            copied_files.append(content_dest)

            # 4b. 复制 handler.py（如果存在）
            handler_src = skill_dir / "handler.py"
            if handler_src.exists() and skill_data.get("type") == "action":
                handler_dest = self._handlers_dir / f"{skill_id}.py"
                self._handlers_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(handler_src, handler_dest)
                copied_files.append(handler_dest)

        except OSError as exc:
            # 回滚已复制的文件
            self._rollback(copied_files)
            return InstallResult(
                success=False,
                skill_id=skill_id,
                errors=[f"File copy failed: {exc}"],
                message="Installation failed during file copy",
            )

        # 5. 更新 registry.json
        try:
            registry_entry = self._build_registry_entry(skill_data)
            registry["skills"].append(registry_entry)
            self._write_registry(registry)
        except OSError as exc:
            # 回滚文件 + registry
            self._rollback(copied_files)
            return InstallResult(
                success=False,
                skill_id=skill_id,
                errors=[f"Registry update failed: {exc}"],
                message="Installation failed during registry update",
            )

        logger.info("[SkillInstaller] Installed skill '%s' successfully", skill_id)
        return InstallResult(
            success=True,
            skill_id=skill_id,
            message=f"Skill '{skill_id}' installed successfully",
        )

    def uninstall(self, skill_id: str) -> InstallResult:
        """卸载 Skill。

        流程: 从 registry 移除条目 → 删除文件。

        Args:
            skill_id: 要卸载的 Skill ID。

        Returns:
            InstallResult 包含成功/失败状态。
        """
        # 1. 检查 skill 是否存在
        registry = self._load_registry()
        skills_list = registry.get("skills", [])
        target_entry: Optional[dict] = None
        target_index: int = -1

        for i, entry in enumerate(skills_list):
            if entry.get("id") == skill_id:
                target_entry = entry
                target_index = i
                break

        if target_entry is None:
            return InstallResult(
                success=False,
                skill_id=skill_id,
                errors=[f"Skill '{skill_id}' not found in registry"],
                message="Uninstall failed: skill not found",
            )

        # 2. 从 registry 移除
        skills_list.pop(target_index)
        self._write_registry(registry)

        # 3. 删除文件
        removed_files: list[str] = []

        # 3a. 删除 content 文件
        content_file = target_entry.get("file", f"{skill_id}.md")
        content_path = self._skills_dir / content_file
        if content_path.exists():
            content_path.unlink()
            removed_files.append(str(content_path))

        # 3b. 删除 handler 文件（如果是 action skill）
        if target_entry.get("type") == "action":
            handler_path = self._handlers_dir / f"{skill_id}.py"
            if handler_path.exists():
                handler_path.unlink()
                removed_files.append(str(handler_path))

        logger.info(
            "[SkillInstaller] Uninstalled skill '%s', removed files: %s",
            skill_id, removed_files,
        )
        return InstallResult(
            success=True,
            skill_id=skill_id,
            message=f"Skill '{skill_id}' uninstalled successfully",
        )

    # ----------------------------------------------------------
    # Private helpers
    # ----------------------------------------------------------

    def _validate_fields(self, data: dict) -> list[str]:
        """验证 skill.json 字段的类型和值。"""
        errors: list[str] = []

        # id 格式
        skill_id = data.get("id", "")
        if not isinstance(skill_id, str) or not _VALID_ID_PATTERN.match(skill_id):
            errors.append(
                f"'id' must match pattern [a-z0-9_]{{1,64}}, got: '{skill_id}'"
            )

        # type 值
        skill_type = data.get("type", "")
        if skill_type not in _VALID_TYPES:
            errors.append(
                f"'type' must be 'knowledge' or 'action', got: '{skill_type}'"
            )

        # name / description 非空字符串
        for str_field in ("name", "description", "version"):
            val = data.get(str_field)
            if not isinstance(val, str) or not val.strip():
                errors.append(f"'{str_field}' must be a non-empty string")

        # tags / applicable_paper_types / applicable_phases 必须是 list
        for list_field in ("tags", "applicable_paper_types", "applicable_phases"):
            val = data.get(list_field)
            if not isinstance(val, list):
                errors.append(f"'{list_field}' must be an array")
            elif not all(isinstance(item, str) for item in val):
                errors.append(f"'{list_field}' must contain only strings")

        # token_estimate 正整数
        token_est = data.get("token_estimate")
        if not isinstance(token_est, int) or token_est <= 0:
            errors.append(
                f"'token_estimate' must be a positive integer, got: {token_est}"
            )

        # priority_hint 0-100
        priority = data.get("priority_hint")
        if not isinstance(priority, int) or not (0 <= priority <= 100):
            errors.append(
                f"'priority_hint' must be an integer 0-100, got: {priority}"
            )

        return errors

    def _load_registry(self) -> dict:
        """加载 registry.json。不存在时返回空结构。"""
        if not self._registry_path.exists():
            return {"version": "1.1", "skills": []}
        try:
            return json.loads(
                self._registry_path.read_text(encoding="utf-8")
            )
        except (json.JSONDecodeError, OSError):
            return {"version": "1.1", "skills": []}

    def _write_registry(self, registry: dict) -> None:
        """写入 registry.json。"""
        self._registry_path.write_text(
            json.dumps(registry, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _build_registry_entry(self, skill_data: dict) -> dict:
        """从 skill.json 数据构建 registry 条目。"""
        today = date.today().isoformat()
        entry: dict = {
            "id": skill_data["id"],
            "version": skill_data["version"],
            "status": "active",
            "installed_at": today,
            "last_updated": today,
            "type": skill_data["type"],
            "file": f"{skill_data['id']}.md",
            "name": skill_data["name"],
            "description": skill_data["description"],
            "tags": skill_data["tags"],
            "applicable_paper_types": skill_data["applicable_paper_types"],
            "applicable_phases": skill_data["applicable_phases"],
            "token_estimate": skill_data["token_estimate"],
            "priority_hint": skill_data["priority_hint"],
        }

        # Action Skill: 添加 tools 并更新 handler 路径
        if skill_data.get("type") == "action" and "tools" in skill_data:
            tools = []
            for tool in skill_data["tools"]:
                tool_entry = dict(tool)
                # 将 handler 路径更新为安装后的位置
                original_handler = tool.get("handler", "")
                if "::" in original_handler:
                    func_name = original_handler.split("::")[-1]
                    tool_entry["handler"] = (
                        f"skill_handlers/{skill_data['id']}.py::{func_name}"
                    )
                tools.append(tool_entry)
            entry["tools"] = tools

        return entry

    @staticmethod
    def _rollback(files: list[Path]) -> None:
        """回滚已复制的文件。"""
        for f in files:
            try:
                if f.exists():
                    f.unlink()
            except OSError:
                pass  # best-effort rollback
