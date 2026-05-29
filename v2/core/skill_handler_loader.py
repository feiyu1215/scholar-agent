"""
core/skill_handler_loader.py — 操作型 Skill Handler 动态加载器

V4 Phase D1: 通过 importlib 从 skill_handlers/ 目录动态加载 handler 函数。
V2 B5: 新增 load_all_active() — 扫描 registry 中所有 active action skill，
        批量加载 handler 并返回 tool_name → handler_fn 映射。

设计原则:
    - handler 路径格式: "skill_handlers/module.py::function_name"
    - 所有 handler 统一签名: (args: dict, state: Any) -> str
    - 加载失败 → graceful 降级（warn log + 返回 None，不中断启动）
    - handler 必须位于 v2/skills/skill_handlers/ 目录下（安全约束）
    - 不执行任意外部路径代码

使用:
    loader = SkillHandlerLoader(skills_dir=Path("v2/skills"))
    handler_fn = loader.load("skill_handlers/export_review.py::handle_export")
    if handler_fn:
        result = handler_fn(args, state)

    # 批量加载所有 active action skills:
    all_handlers = loader.load_all_active()
    # → {"export_structured_review": <handler_fn>, ...}
"""

from __future__ import annotations

import importlib.util
import json
import logging
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# Handler 统一签名类型
HandlerFn = Callable[[dict, Any], str]


class SkillHandlerLoader:
    """动态加载 Skill handler 函数。

    安全约束:
        - 只从指定 skills_dir 下的 skill_handlers/ 目录加载
        - handler 路径必须匹配 "skill_handlers/<module>.py::<function>" 格式
        - 不支持嵌套目录（防止路径遍历攻击）
    """

    def __init__(self, skills_dir: Path) -> None:
        """初始化 Handler 加载器。

        Args:
            skills_dir: v2/skills/ 目录路径。handler 文件位于 skills_dir/skill_handlers/ 下。
        """
        self._skills_dir = skills_dir
        self._handlers_dir = skills_dir / "skill_handlers"
        self._cache: dict[str, Optional[HandlerFn]] = {}

    def load(self, handler_path: str) -> Optional[HandlerFn]:
        """加载指定路径的 handler 函数。

        Args:
            handler_path: 格式为 "skill_handlers/<module>.py::<function_name>"

        Returns:
            Handler 函数 (args: dict, state: Any) -> str，加载失败返回 None。
        """
        # 缓存命中
        if handler_path in self._cache:
            return self._cache[handler_path]

        fn = self._do_load(handler_path)
        self._cache[handler_path] = fn
        return fn

    def _do_load(self, handler_path: str) -> Optional[HandlerFn]:
        """实际加载逻辑。"""
        # 1. 解析路径格式
        if "::" not in handler_path:
            logger.warning(
                "[SkillHandlerLoader] Invalid handler path (missing '::'): '%s'",
                handler_path,
            )
            return None

        module_path_str, func_name = handler_path.rsplit("::", 1)

        # 2. 安全校验: 必须以 "skill_handlers/" 开头
        if not module_path_str.startswith("skill_handlers/"):
            logger.warning(
                "[SkillHandlerLoader] Handler path must start with 'skill_handlers/': '%s'",
                handler_path,
            )
            return None

        # 3. 安全校验: 不允许路径遍历
        if ".." in module_path_str:
            logger.warning(
                "[SkillHandlerLoader] Path traversal detected in handler path: '%s'",
                handler_path,
            )
            return None

        # 4. 构造文件绝对路径
        file_path = self._skills_dir / module_path_str
        if not file_path.exists():
            logger.warning(
                "[SkillHandlerLoader] Handler file not found: %s",
                file_path,
            )
            return None

        if not file_path.is_file():
            logger.warning(
                "[SkillHandlerLoader] Handler path is not a file: %s",
                file_path,
            )
            return None

        # 4b. 安全校验: 解析 symlink 后仍必须在 handlers 目录下（防止 symlink 逃逸）
        resolved = file_path.resolve()
        handlers_resolved = self._handlers_dir.resolve()
        if not str(resolved).startswith(str(handlers_resolved) + "/") and resolved != handlers_resolved:
            logger.warning(
                "[SkillHandlerLoader] Symlink escape detected: '%s' resolves to '%s' "
                "which is outside '%s'",
                file_path,
                resolved,
                handlers_resolved,
            )
            return None

        # 5. 用 importlib 动态加载模块
        module_name = f"skill_handlers.{file_path.stem}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, file_path)
            if spec is None or spec.loader is None:
                logger.warning(
                    "[SkillHandlerLoader] Cannot create module spec for: %s",
                    file_path,
                )
                return None

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)  # type: ignore[union-attr]
        except Exception as exc:
            logger.warning(
                "[SkillHandlerLoader] Failed to import module '%s': %s",
                file_path,
                exc,
            )
            return None

        # 6. 获取函数
        fn = getattr(module, func_name, None)
        if fn is None:
            logger.warning(
                "[SkillHandlerLoader] Function '%s' not found in module '%s'.",
                func_name,
                file_path,
            )
            return None

        if not callable(fn):
            logger.warning(
                "[SkillHandlerLoader] '%s' in '%s' is not callable.",
                func_name,
                file_path,
            )
            return None

        logger.info(
            "[SkillHandlerLoader] Successfully loaded handler: %s",
            handler_path,
        )
        return fn  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # B5: 批量加载所有 active action skills
    # ------------------------------------------------------------------

    def load_all_active(self) -> dict[str, HandlerFn]:
        """扫描 registry.json 中所有 type=action + status=active 的 Skill，
        加载其 handler 函数并返回 tool_name → handler_fn 映射。

        单个 Skill 加载失败不阻断其他 Skill（warn + skip）。

        Returns:
            dict[str, HandlerFn]: tool_name → handler 函数。
        """
        registry_path = self._skills_dir / "registry.json"
        if not registry_path.exists():
            logger.warning(
                "[SkillHandlerLoader] registry.json not found at %s",
                registry_path,
            )
            return {}

        try:
            registry_data = json.loads(registry_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "[SkillHandlerLoader] Failed to parse registry.json: %s", exc
            )
            return {}

        skills = registry_data.get("skills", [])
        result: dict[str, HandlerFn] = {}

        for entry in skills:
            # 只加载 action type + active status
            if entry.get("type") != "action":
                continue
            status = entry.get("status", "active")  # 向后兼容：无 status 默认 active
            if status != "active":
                logger.debug(
                    "[SkillHandlerLoader] Skipping inactive action skill: %s (status=%s)",
                    entry.get("id", "?"),
                    status,
                )
                continue

            # 遍历该 Skill 的 tools 列表
            tools = entry.get("tools", [])
            for tool in tools:
                tool_name = tool.get("name")
                handler_path = tool.get("handler")
                if not tool_name or not handler_path:
                    logger.warning(
                        "[SkillHandlerLoader] Tool entry missing name/handler in skill '%s'",
                        entry.get("id", "?"),
                    )
                    continue

                handler_fn = self.load(handler_path)
                if handler_fn is not None:
                    result[tool_name] = handler_fn
                else:
                    logger.warning(
                        "[SkillHandlerLoader] Failed to load handler for tool '%s' "
                        "in skill '%s'",
                        tool_name,
                        entry.get("id", "?"),
                    )

        logger.info(
            "[SkillHandlerLoader] load_all_active() loaded %d handler(s)", len(result)
        )
        return result
