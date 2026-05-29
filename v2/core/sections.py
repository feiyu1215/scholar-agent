"""
core/v2/sections.py — Section 数据模型 + SectionRegistry

设计依据:
    - Claude Code: Section 注册 + memoization + 按优先级裁剪
    - TencentDB: 不同信息有不同的"注意力层级"
    - Anthropic: "模型对世界的理解可能压在 1-2 万 token 里"

核心原则:
    - 每个 section 是独立信息单元（可注册、可缓存、可裁剪）
    - priority 越高越先注入，token 不够时低优先级被丢弃
    - cache_policy 控制重算频率：NEVER 每轮重算，SESSION 整会话缓存，PHASE 阶段内缓存

与 format_context 的关系:
    - 原 format_context 中的 9 个信息块被拆分为独立 section
    - ContextAssembler 使用 SectionRegistry 组装 system prompt
    - 保持输出兼容: 迁移后输出内容相同，只是组装方式变了
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from core.state import WorkspaceState

logger = logging.getLogger(__name__)


class CachePolicy(Enum):
    """Section 缓存策略。"""
    NEVER = "never"        # 每轮都重算（进度、findings 等变化频繁的信息）
    SESSION = "session"    # 整个会话只算一次（论文元数据、身份等不变信息）
    PHASE = "phase"        # 阶段内缓存（阶段切换时失效）


@dataclass
class SectionDefinition:
    """Section 定义。"""
    name: str
    priority: int                        # 0-100，越高越先注入
    cache_policy: CachePolicy
    compute_fn: Callable[..., str]       # 接受 state，返回该 section 的文本
    condition_fn: Callable[..., bool] | None = None  # 可选：决定是否应该注入

    def should_include(self, state: Any) -> bool:
        """判断当前状态下是否应该注入此 section。"""
        if self.condition_fn is None:
            return True
        return self.condition_fn(state)


@dataclass
class CacheEntry:
    """缓存条目。"""
    content: str
    computed_at_turn: int
    phase_at_compute: str = ""


class SectionRegistry:
    """
    动态 Section 注册表。

    用法:
        registry = SectionRegistry()
        registry.register("review_progress", priority=90,
                          cache_policy=CachePolicy.NEVER,
                          compute_fn=compute_review_progress)
        sections = registry.get_active_sections(state, budget=8000)
    """

    def __init__(self) -> None:
        self._sections: list[SectionDefinition] = []
        self._cache: dict[str, CacheEntry] = {}

    def register(
        self,
        name: str,
        priority: int,
        cache_policy: CachePolicy,
        compute_fn: Callable[..., str],
        condition_fn: Callable[..., bool] | None = None,
    ) -> None:
        """注册一个 section。"""
        self._sections.append(SectionDefinition(
            name=name,
            priority=priority,
            cache_policy=cache_policy,
            compute_fn=compute_fn,
            condition_fn=condition_fn,
        ))

    def get_active_sections(
        self,
        state: Any,
        budget: int = 8000,
        current_turn: int = 0,
        current_phase: str = "",
    ) -> list[tuple[str, str]]:
        """
        返回当前应该注入的 sections（按优先级降序，受预算约束）。

        Args:
            state: WorkspaceState 实例
            budget: token 预算（近似为字符数 / 2，中文约 1 token/字）
            current_turn: 当前轮次（用于缓存判断）
            current_phase: 当前阶段名（用于 PHASE 缓存策略）

        Returns:
            list of (section_name, section_content) 按优先级降序排列
        """
        results: list[tuple[str, str]] = []
        # budget <= 0 表示无限制（不做 token 裁剪）
        unlimited = (budget <= 0)
        budget_remaining = budget if not unlimited else float('inf')

        # 按优先级降序排列
        sorted_sections = sorted(
            self._sections, key=lambda s: s.priority, reverse=True
        )

        for sec_def in sorted_sections:
            # 条件检查
            if not sec_def.should_include(state):
                continue

            # 计算或读缓存
            content = self._compute_or_cache(
                sec_def, state, current_turn, current_phase
            )

            # 空内容跳过
            if not content or not content.strip():
                continue

            # 预算检查（用字符数近似 token 数）
            estimated_tokens = self._estimate_tokens(content)
            if estimated_tokens <= budget_remaining:
                results.append((sec_def.name, content))
                budget_remaining -= estimated_tokens
            else:
                # 预算不够了，尝试下一个更小的 section
                # 不直接 break，因为后面可能有更小的 section 能塞进去
                logger.debug(
                    "Section '%s' skipped: needs ~%d tokens, only %d remaining",
                    sec_def.name, estimated_tokens, budget_remaining,
                )
                continue

        return results

    def invalidate_cache(self, name: str | None = None) -> None:
        """清除缓存。name=None 清除全部。"""
        if name is None:
            self._cache.clear()
        else:
            self._cache.pop(name, None)

    def invalidate_phase_cache(self) -> None:
        """清除所有 PHASE 策略的缓存（阶段切换时调用）。"""
        phase_sections = {
            s.name for s in self._sections
            if s.cache_policy == CachePolicy.PHASE
        }
        for name in phase_sections:
            self._cache.pop(name, None)

    @property
    def section_names(self) -> list[str]:
        """返回所有已注册 section 名。"""
        return [s.name for s in self._sections]

    def __len__(self) -> int:
        return len(self._sections)

    # ----------------------------------------------------------
    # 内部方法
    # ----------------------------------------------------------

    def _compute_or_cache(
        self,
        sec_def: SectionDefinition,
        state: Any,
        current_turn: int,
        current_phase: str,
    ) -> str:
        """根据缓存策略决定重算还是用缓存。"""
        name = sec_def.name

        # NEVER: 每轮都重算
        if sec_def.cache_policy == CachePolicy.NEVER:
            content = sec_def.compute_fn(state)
            return content

        # SESSION: 只要有缓存就用
        if sec_def.cache_policy == CachePolicy.SESSION:
            cached = self._cache.get(name)
            if cached is not None:
                return cached.content
            content = sec_def.compute_fn(state)
            self._cache[name] = CacheEntry(
                content=content,
                computed_at_turn=current_turn,
                phase_at_compute=current_phase,
            )
            return content

        # PHASE: 阶段不变就用缓存
        if sec_def.cache_policy == CachePolicy.PHASE:
            cached = self._cache.get(name)
            if cached is not None and cached.phase_at_compute == current_phase:
                return cached.content
            content = sec_def.compute_fn(state)
            self._cache[name] = CacheEntry(
                content=content,
                computed_at_turn=current_turn,
                phase_at_compute=current_phase,
            )
            return content

        # fallback
        return sec_def.compute_fn(state)

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """
        估算 token 数。

        简单启发式：
        - 中文字符约 1 token/字
        - 英文约 4 字符/token
        - 混合取中间值：len(text) * 0.6 作为近似
        """
        if not text:
            return 0
        # 统计中文字符占比
        chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        total_chars = len(text)
        if total_chars == 0:
            return 0

        chinese_ratio = chinese_chars / total_chars
        # 中文多时 token ≈ 字符数，英文多时 token ≈ 字符数/4
        tokens_per_char = chinese_ratio * 1.0 + (1 - chinese_ratio) * 0.25
        return int(total_chars * tokens_per_char)
