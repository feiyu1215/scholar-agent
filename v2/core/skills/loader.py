"""
core/skills/loader.py — 渐进式 Skill 加载器 (Progressive Disclosure)

三层加载策略：
  Layer 1 (启动时): 仅加载 SkillDescriptor 元数据 (~100 tokens/skill)
  Layer 2 (触发时): 加载完整 SOP 指令 (~2k tokens/skill)
  Layer 3 (执行时): 加载参考资源、示例数据、外部知识

设计原则：
  - 上下文 token 是审稿的核心稀缺资源
  - 15 个 Skill × 2000 tokens = 30000 tokens 不可接受
  - 三层渐进加载保证启动时仅 ~1500 tokens（仅元数据）
  - 与 ToolGroup 配合：ToolGroup 决定"哪些 Skill 可用"，
    ProgressiveLoader 决定"可用的 Skill 怎么呈现给 LLM"
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from core.skills.base import Skill, SkillLevel

logger = logging.getLogger(__name__)


# ==============================================================
# 加载层级
# ==============================================================

@dataclass
class LoadedSkillContent:
    """Skill 内容的加载结果。"""
    skill_name: str
    layer: int  # 1=metadata, 2=instruction, 3=resource
    content: str
    token_estimate: int = 0


# ==============================================================
# 渐进式加载器
# ==============================================================

class ProgressiveSkillLoader:
    """三层渐进式 Skill 加载器。

    Usage:
        loader = ProgressiveSkillLoader(skills, resources_dir)

        # Layer 1: 启动时 — 注入 system prompt
        metadata_prompt = loader.get_metadata_prompt()

        # Layer 2: Agent 请求时 — 加载特定 Skill 的完整指令
        instruction = loader.load_instruction("methodology_analysis")

        # Layer 3: 执行时 — 加载参考资源
        resource = loader.load_resource("methodology_analysis", "did_checklist.md")
    """

    def __init__(
        self,
        skills: list[Skill],
        resources_dir: Optional[Path] = None,
    ):
        """
        Args:
            skills: 所有已注册的 Skill 实例
            resources_dir: 资源文件目录（Layer 3 加载源）
        """
        self._skills: dict[str, Skill] = {s.descriptor.name: s for s in skills}
        self._resources_dir = resources_dir
        # 缓存已加载的 Layer 2 内容（避免重复加载）
        self._instruction_cache: dict[str, str] = {}

    @property
    def skill_names(self) -> list[str]:
        """所有已注册的 Skill 名称。"""
        return list(self._skills.keys())

    # ----------------------------------------------------------
    # Layer 1: 元数据 Prompt
    # ----------------------------------------------------------

    def get_metadata_prompt(
        self,
        skills: Optional[list[Skill]] = None,
        include_level: bool = True,
    ) -> str:
        """生成所有 Skill 的元数据摘要，注入 system prompt。

        每个 Skill 占 ~100 tokens（名称 + 描述 + 适用阶段）。
        这是渐进式披露的第 1 层。

        Args:
            skills: 指定 Skill 列表（None 表示全部）
            include_level: 是否包含层级信息

        Returns:
            适合注入 system prompt 的多行文本
        """
        target_skills = skills or list(self._skills.values())

        lines = ["[可用审稿技能]"]
        for skill in target_skills:
            desc = skill.descriptor
            phases = ", ".join(desc.applicable_phases) if desc.applicable_phases else "全阶段"
            if include_level:
                lines.append(
                    f"- {desc.name} [{desc.level.value}]: {desc.description} (适用: {phases})"
                )
            else:
                lines.append(f"- {desc.name}: {desc.description} (适用: {phases})")

        lines.append("")
        lines.append("使用 load_skill(name) 获取特定技能的详细指令。")
        return "\n".join(lines)

    def get_metadata_token_estimate(self) -> int:
        """估算 Layer 1 元数据 prompt 的 token 消耗。"""
        # 粗估：每个 Skill 约 30-40 中文字符 ≈ 50-80 tokens
        return len(self._skills) * 80

    # ----------------------------------------------------------
    # Layer 2: 完整指令
    # ----------------------------------------------------------

    def load_instruction(self, skill_name: str) -> Optional[LoadedSkillContent]:
        """加载特定 Skill 的完整 SOP 指令（第 2 层）。

        此方法在 Agent 判断需要某 Skill 时调用，加载详细执行指令。
        约 ~2k tokens/skill。

        Args:
            skill_name: Skill 名称

        Returns:
            LoadedSkillContent 或 None（Skill 不存在时）
        """
        skill = self._skills.get(skill_name)
        if skill is None:
            logger.warning("[ProgressiveLoader] Skill not found: %s", skill_name)
            return None

        # 检查缓存
        if skill_name in self._instruction_cache:
            content = self._instruction_cache[skill_name]
        else:
            content = skill.get_instruction()
            self._instruction_cache[skill_name] = content

        # 估算 token（中文约 1.5 字符/token）
        token_estimate = max(len(content) // 2, 1)

        return LoadedSkillContent(
            skill_name=skill_name,
            layer=2,
            content=content,
            token_estimate=token_estimate,
        )

    def load_instructions_batch(
        self, skill_names: list[str]
    ) -> list[LoadedSkillContent]:
        """批量加载多个 Skill 指令。"""
        results = []
        for name in skill_names:
            loaded = self.load_instruction(name)
            if loaded is not None:
                results.append(loaded)
        return results

    # ----------------------------------------------------------
    # Layer 3: 参考资源
    # ----------------------------------------------------------

    def load_resource(
        self, skill_name: str, resource_path: str
    ) -> Optional[LoadedSkillContent]:
        """加载 Skill 的参考资源文件（第 3 层）。

        资源文件位于 resources_dir/<skill_name>/<resource_path>

        Args:
            skill_name: Skill 名称
            resource_path: 资源相对路径

        Returns:
            LoadedSkillContent 或 None
        """
        if self._resources_dir is None:
            logger.warning(
                "[ProgressiveLoader] resources_dir not set, cannot load resource"
            )
            return None

        if skill_name not in self._skills:
            logger.warning("[ProgressiveLoader] Skill not found: %s", skill_name)
            return None

        # 安全检查：拒绝路径遍历
        if ".." in resource_path:
            logger.warning(
                "[ProgressiveLoader] Path traversal rejected: %s", resource_path
            )
            return None

        resource_file = self._resources_dir / skill_name / resource_path
        if not resource_file.exists():
            logger.warning(
                "[ProgressiveLoader] Resource not found: %s", resource_file
            )
            return None

        # Symlink 逃逸检测
        try:
            resolved = resource_file.resolve()
            if not str(resolved).startswith(str(self._resources_dir.resolve())):
                logger.warning(
                    "[ProgressiveLoader] Symlink escape detected: %s -> %s",
                    resource_file, resolved,
                )
                return None
        except (OSError, ValueError):
            return None

        try:
            content = resource_file.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning(
                "[ProgressiveLoader] Failed to read resource '%s': %s",
                resource_file, exc,
            )
            return None

        token_estimate = max(len(content) // 2, 1)

        return LoadedSkillContent(
            skill_name=skill_name,
            layer=3,
            content=content,
            token_estimate=token_estimate,
        )

    def list_resources(self, skill_name: str) -> list[str]:
        """列出某 Skill 的所有可用资源文件。"""
        if self._resources_dir is None:
            return []
        skill_dir = self._resources_dir / skill_name
        if not skill_dir.exists():
            return []
        return [
            str(f.relative_to(skill_dir))
            for f in skill_dir.rglob("*")
            if f.is_file() and not f.name.startswith(".")
        ]

    # ----------------------------------------------------------
    # 缓存管理
    # ----------------------------------------------------------

    def clear_cache(self) -> None:
        """清除 Layer 2 指令缓存。"""
        self._instruction_cache.clear()

    def invalidate(self, skill_name: str) -> None:
        """使特定 Skill 的缓存失效。"""
        self._instruction_cache.pop(skill_name, None)
