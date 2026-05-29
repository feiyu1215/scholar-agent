"""
core/skill_lifecycle.py — Skill 生命周期管理 API

V2 B5: 提供 activate / deactivate / list_skills 管理接口。
允许用户通过编程方式管理 registry.json 中 Skill 的状态，
无需手动编辑 JSON 文件。

设计原则:
    - 所有操作直接修改 registry.json（持久化）
    - 操作幂等：activate 已 active 的 Skill → 无副作用，返回 True
    - 缺少 lifecycle 字段时使用默认值（向后兼容）
    - 不改变 registry 中 skills 的顺序（防止 diff 噪音）
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 合法 status 值
VALID_STATUSES = frozenset({"active", "inactive", "deprecated", "draft"})


@dataclass
class SkillLifecycleMeta:
    """Skill 生命周期元数据摘要（只读视图）。"""

    id: str
    name: str
    version: str
    status: str
    type: str
    installed_at: str
    last_updated: str
    tags: list[str] = field(default_factory=list)
    description: str = ""


class SkillRegistryManager:
    """Skill 注册表生命周期管理器。

    提供:
        - activate_skill(skill_id) → bool
        - deactivate_skill(skill_id) → bool
        - list_skills(status_filter) → list[SkillLifecycleMeta]
        - get_skill(skill_id) → SkillLifecycleMeta | None
    """

    def __init__(self, registry_path: Path) -> None:
        """初始化管理器。

        Args:
            registry_path: registry.json 的绝对路径。
        """
        self._registry_path = registry_path

    def _read_registry(self) -> dict:
        """读取并解析 registry.json。"""
        if not self._registry_path.exists():
            raise FileNotFoundError(
                f"registry.json not found: {self._registry_path}"
            )
        return json.loads(self._registry_path.read_text(encoding="utf-8"))

    def _write_registry(self, data: dict) -> None:
        """将修改后的数据写回 registry.json（保持格式化）。"""
        self._registry_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _find_skill_entry(self, data: dict, skill_id: str) -> Optional[dict]:
        """在 registry data 中查找指定 id 的 skill 条目。"""
        for entry in data.get("skills", []):
            if entry.get("id") == skill_id:
                return entry
        return None

    def activate_skill(self, skill_id: str) -> bool:
        """激活指定 Skill。

        Args:
            skill_id: Skill 的 id 字段值。

        Returns:
            True 如果操作成功（包括已经是 active），False 如果 skill_id 不存在。
        """
        data = self._read_registry()
        entry = self._find_skill_entry(data, skill_id)
        if entry is None:
            logger.warning(
                "[SkillRegistryManager] Skill not found: '%s'", skill_id
            )
            return False

        entry["status"] = "active"
        entry["last_updated"] = date.today().isoformat()
        self._write_registry(data)
        logger.info("[SkillRegistryManager] Activated skill: '%s'", skill_id)
        return True

    def deactivate_skill(self, skill_id: str) -> bool:
        """停用指定 Skill。

        Args:
            skill_id: Skill 的 id 字段值。

        Returns:
            True 如果操作成功（包括已经是 inactive），False 如果 skill_id 不存在。
        """
        data = self._read_registry()
        entry = self._find_skill_entry(data, skill_id)
        if entry is None:
            logger.warning(
                "[SkillRegistryManager] Skill not found: '%s'", skill_id
            )
            return False

        entry["status"] = "inactive"
        entry["last_updated"] = date.today().isoformat()
        self._write_registry(data)
        logger.info("[SkillRegistryManager] Deactivated skill: '%s'", skill_id)
        return True

    def list_skills(self, status_filter: Optional[str] = None) -> list[SkillLifecycleMeta]:
        """列出 Skill 元数据。

        Args:
            status_filter: 如果提供，只返回匹配 status 的条目。None 返回全部。

        Returns:
            SkillLifecycleMeta 列表。
        """
        data = self._read_registry()
        result: list[SkillLifecycleMeta] = []

        for entry in data.get("skills", []):
            status = entry.get("status", "active")  # 向后兼容
            if status_filter is not None and status != status_filter:
                continue
            result.append(
                SkillLifecycleMeta(
                    id=entry.get("id", ""),
                    name=entry.get("name", ""),
                    version=entry.get("version", "0.0.0"),
                    status=status,
                    type=entry.get("type", "knowledge"),
                    installed_at=entry.get("installed_at", ""),
                    last_updated=entry.get("last_updated", ""),
                    tags=entry.get("tags", []),
                    description=entry.get("description", ""),
                )
            )

        return result

    def get_skill(self, skill_id: str) -> Optional[SkillLifecycleMeta]:
        """获取单个 Skill 的元数据。

        Args:
            skill_id: Skill 的 id 字段值。

        Returns:
            SkillLifecycleMeta 或 None（不存在时）。
        """
        data = self._read_registry()
        entry = self._find_skill_entry(data, skill_id)
        if entry is None:
            return None
        return SkillLifecycleMeta(
            id=entry.get("id", ""),
            name=entry.get("name", ""),
            version=entry.get("version", "0.0.0"),
            status=entry.get("status", "active"),
            type=entry.get("type", "knowledge"),
            installed_at=entry.get("installed_at", ""),
            last_updated=entry.get("last_updated", ""),
            tags=entry.get("tags", []),
            description=entry.get("description", ""),
        )
