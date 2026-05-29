"""
core/skill_registry.py — Skill 知识注册表

V4 Phase A1: registry.json 解析 + 多维查询（paper_type × phase × budget）。
提供 SkillMeta / ToolDef 数据类和 SkillRegistry 查询引擎。

设计原则:
    - 加载失败 → 空 registry（graceful 降级）
    - query() 按 priority_hint 降序排列，贪心填充 token budget
    - 只读接口；lifecycle 管理在 skill_lifecycle.py
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolDef:
    """Action Skill 的 Tool 定义（不可变）。"""

    name: str
    description: str
    input_schema: dict
    handler: str

    def to_api_schema(self) -> dict:
        """导出为 LLM API 兼容的 schema（不暴露 handler 路径）。"""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ToolDef":
        """从 registry.json tools 条目构造。"""
        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            input_schema=data.get("input_schema", {}),
            handler=data.get("handler", ""),
        )


@dataclass(frozen=True)
class SkillMeta:
    """Skill 元数据（不可变）。"""

    id: str
    type: str
    file: str
    name: str
    description: str
    tags: tuple[str, ...]
    applicable_paper_types: tuple[str, ...]
    applicable_phases: tuple[str, ...]
    token_estimate: int
    priority_hint: int
    source: str = "internal"
    path: str = ""
    extract_sections: tuple[str, ...] = ()
    tools: tuple[ToolDef, ...] = ()

    @classmethod
    def from_dict(cls, data: dict) -> "SkillMeta":
        """从 registry.json 条目字典构造 SkillMeta。"""
        tools_data = data.get("tools", [])
        tools = tuple(ToolDef.from_dict(t) for t in tools_data)
        return cls(
            id=data.get("id", ""),
            type=data.get("type", "knowledge"),
            file=data.get("file", ""),
            name=data.get("name", data.get("id", "")),
            description=data.get("description", ""),
            tags=tuple(data.get("tags", [])),
            applicable_paper_types=tuple(data.get("applicable_paper_types", [])),
            applicable_phases=tuple(data.get("applicable_phases", [])),
            token_estimate=data.get("token_estimate", 0),
            priority_hint=data.get("priority_hint", 50),
            source=data.get("source", "internal"),
            path=data.get("path", ""),
            extract_sections=tuple(data.get("extract_sections", [])),
            tools=tools,
        )


class SkillRegistry:
    """Skill 知识注册表 — 解析 registry.json 并提供多维查询。

    Usage:
        registry = SkillRegistry(Path("v2/skills"))
        skills = registry.query(paper_type="empirical", phase="DEEP_REVIEW", budget_tokens=4500)
    """

    def __init__(self, skills_dir: Path) -> None:
        """初始化注册表。

        Args:
            skills_dir: v2/skills/ 目录路径。registry.json 位于此目录下。
        """
        self._skills_dir = skills_dir
        self._skills: list[SkillMeta] = []
        self._index: dict[str, SkillMeta] = {}
        self._load()

    def _load(self) -> None:
        """加载并解析 registry.json。失败时保持空状态。"""
        registry_path = self._skills_dir / "registry.json"
        if not registry_path.exists():
            logger.warning("[SkillRegistry] registry.json not found: %s", registry_path)
            return

        try:
            data = json.loads(registry_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("[SkillRegistry] Failed to parse registry.json: %s", exc)
            return

        for entry in data.get("skills", []):
            meta = SkillMeta.from_dict(entry)
            self._skills.append(meta)
            self._index[meta.id] = meta

        # 按 priority_hint 降序排列
        self._skills.sort(key=lambda s: s.priority_hint, reverse=True)

    @property
    def all_skills(self) -> list[SkillMeta]:
        """返回所有 Skill（按 priority_hint 降序）。"""
        return list(self._skills)

    def get(self, skill_id: str) -> Optional[SkillMeta]:
        """按 id 获取单个 SkillMeta。"""
        return self._index.get(skill_id)

    def query(
        self,
        paper_type: Optional[str] = None,
        phase: Optional[str] = None,
        budget_tokens: int = 4500,
    ) -> list[SkillMeta]:
        """多维查询：paper_type × phase 过滤 + token budget 贪心裁剪。

        Args:
            paper_type: 论文类型过滤（None 表示不过滤）。
            phase: 审稿阶段过滤（None 表示不过滤）。
            budget_tokens: token 预算上限。

        Returns:
            按 priority_hint 降序排列的、在 budget 内的 SkillMeta 列表。
        """
        candidates = self._skills

        # 1. paper_type 过滤（case-insensitive）
        if paper_type is not None:
            pt_lower = paper_type.lower()
            candidates = [
                s for s in candidates
                if not s.applicable_paper_types or pt_lower in [p.lower() for p in s.applicable_paper_types]
            ]

        # 2. phase 过滤（case-insensitive）
        if phase is not None:
            phase_lower = phase.lower()
            candidates = [
                s for s in candidates
                if not s.applicable_phases or phase_lower in [p.lower() for p in s.applicable_phases]
            ]

        # 3. 贪心填充 budget（已按 priority 降序）
        result: list[SkillMeta] = []
        remaining = budget_tokens
        for skill in candidates:
            if skill.token_estimate <= remaining:
                result.append(skill)
                remaining -= skill.token_estimate

        return result

    def get_action_skills(self) -> list[SkillMeta]:
        """返回所有 type=action 的 SkillMeta（按 priority_hint 降序）。"""
        return [s for s in self._skills if s.type == "action"]

    def load_tools_from_markdown(self, skill_id: str) -> list[ToolDef]:
        """从 Skill 的 registry 条目中获取 ToolDef 列表。

        优先使用 registry.json 中 tools 字段已解析好的数据。

        Args:
            skill_id: Skill 的 id。

        Returns:
            ToolDef 列表。
        """
        meta = self.get(skill_id)
        if meta is None:
            return []
        return list(meta.tools)

    @staticmethod
    def _extract_sections(content: str, sections: tuple[str, ...]) -> str:
        """从 Markdown 内容中提取指定 heading 的 section（含子 heading）。

        Args:
            content: 完整 Markdown 文本。
            sections: 要提取的 section 标题（大小写不敏感）。

        Returns:
            拼接后的 section 文本。无匹配时返回空字符串。
        """
        if not content or not sections:
            return ""

        # 将 content 按 heading 分块
        # 匹配 ## Heading 或 ## Heading ##（ATX trailing style）
        heading_pattern = re.compile(r'^(#{1,6})\s+(.+?)\s*#*\s*$', re.MULTILINE)

        # 找到所有 heading 的位置
        headings: list[tuple[int, int, str, int]] = []  # (start, level, title, pos)
        for m in heading_pattern.finditer(content):
            level = len(m.group(1))
            title = m.group(2).strip().rstrip('#').strip()
            headings.append((m.start(), level, title, m.end()))

        sections_lower = [s.lower() for s in sections]
        extracted_parts: list[str] = []

        for i, (start, level, title, _) in enumerate(headings):
            if title.lower() not in sections_lower:
                continue
            # 找到该 section 的结束位置（下一个同级或更高级 heading）
            end = len(content)
            for j in range(i + 1, len(headings)):
                if headings[j][1] <= level:
                    end = headings[j][0]
                    break
            extracted_parts.append(content[start:end].rstrip())

        return "\n\n".join(extracted_parts)

    def load_content(self, skill_id: str) -> Optional[str]:
        """读取指定 Skill 的 Markdown 文件内容。

        支持 internal（从 skills_dir/file 读取）和 external（从绝对 path 读取）。
        如果 extract_sections 非空，只返回匹配 heading 下的内容。

        Args:
            skill_id: Skill 的 id。

        Returns:
            文件内容字符串，不存在或读取失败返回 None。
        """
        meta = self.get(skill_id)
        if meta is None:
            return None

        # 确定文件路径
        if meta.source == "external":
            # 安全检查：拒绝网络 URL
            if not meta.path or meta.path.startswith(("http://", "https://")):
                logger.warning(
                    "[SkillRegistry] External skill '%s': invalid or network path rejected: '%s'",
                    skill_id, meta.path,
                )
                return None
            file_path = Path(meta.path)
        else:
            if not meta.file:
                return None
            file_path = self._skills_dir / meta.file

        if not file_path.exists():
            logger.warning(
                "[SkillRegistry] Skill file not found: %s", file_path
            )
            return None

        try:
            content = file_path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning(
                "[SkillRegistry] Failed to read skill file '%s': %s",
                file_path, exc,
            )
            return None

        # 应用 section 提取
        if meta.extract_sections:
            extracted = self._extract_sections(content, meta.extract_sections)
            return extracted if extracted else None

        return content


# ==============================================================
# TemplateMeta + TemplateRegistry — YAML 模板匹配引擎
# ==============================================================


@dataclass
class TemplateMeta:
    """论文类型模板元数据。"""

    id: str
    name: str
    keywords: list[str]
    structure_patterns: list[str]
    seed_hints: dict
    recommended_skills: list[str]
    gate_overrides: dict

    @classmethod
    def from_dict(cls, data: dict) -> "TemplateMeta":
        """从 YAML 解析后的 dict 构造。"""
        match_signals = data.get("match_signals", {})
        keywords_raw = match_signals.get("keywords", [])
        keywords = [k.lower() for k in keywords_raw]
        structure_patterns = match_signals.get("structure_patterns", [])
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            keywords=keywords,
            structure_patterns=structure_patterns,
            seed_hints=data.get("seed_hints", {}),
            recommended_skills=data.get("recommended_skills", []),
            gate_overrides=data.get("gate_overrides", {}),
        )


class TemplateRegistry:
    """YAML 模板注册表 — 通过关键词匹配论文类型。

    Usage:
        registry = TemplateRegistry(Path("v2/skills/templates"))
        template = registry.match("DID estimation with panel data")
    """

    def __init__(self, templates_dir: Path) -> None:
        self._templates_dir = templates_dir
        self._templates: list[TemplateMeta] = []
        self._index: dict[str, TemplateMeta] = {}
        self._load()

    def _load(self) -> None:
        """加载所有 YAML 模板文件（跳过 _ 开头的文件）。"""
        if not self._templates_dir.exists():
            return

        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError:
            logger.warning("[TemplateRegistry] PyYAML not installed, skipping templates")
            return

        for f in sorted(self._templates_dir.iterdir()):
            if not f.is_file():
                continue
            if f.name.startswith("_"):
                continue
            if f.suffix not in (".yaml", ".yml"):
                continue
            try:
                data = yaml.safe_load(f.read_text(encoding="utf-8"))
                if data and isinstance(data, dict):
                    meta = TemplateMeta.from_dict(data)
                    self._templates.append(meta)
                    self._index[meta.id] = meta
            except Exception as exc:
                logger.warning(
                    "[TemplateRegistry] Failed to load template '%s': %s", f, exc
                )

    @property
    def all_templates(self) -> list[TemplateMeta]:
        return list(self._templates)

    def get(self, template_id: str) -> Optional[TemplateMeta]:
        return self._index.get(template_id)

    def _score_template(self, template: TemplateMeta, text: str) -> int:
        """计算文本与模板的匹配分数（关键词命中数）。"""
        text_lower = text.lower()
        score = 0
        for kw in template.keywords:
            # 对于短关键词（<=4 字符）使用词边界匹配
            if len(kw) <= 4:
                pattern = r'\b' + re.escape(kw) + r'\b'
                if re.search(pattern, text_lower):
                    score += 1
            else:
                if kw in text_lower:
                    score += 1
        return score

    def match(self, text: str, min_score: int = 2) -> Optional[TemplateMeta]:
        """返回最高分匹配模板，低于 min_score 返回 None。"""
        if not text:
            return None
        best: Optional[TemplateMeta] = None
        best_score = 0
        for t in self._templates:
            s = self._score_template(t, text)
            if s > best_score:
                best_score = s
                best = t
        if best_score >= min_score:
            return best
        return None

    def match_all(
        self, text: str, min_score: int = 1
    ) -> list[tuple[TemplateMeta, int]]:
        """返回所有匹配模板（分数 ≥ min_score），按分数降序。"""
        if not text:
            return []
        results: list[tuple[TemplateMeta, int]] = []
        for t in self._templates:
            s = self._score_template(t, text)
            if s >= min_score:
                results.append((t, s))
        results.sort(key=lambda x: x[1], reverse=True)
        return results
