"""
core/token_budget.py — Three-Zone Token Budget Manager

V3 Phase 0.5: 主动 token 预分配，取代 V2 被动压缩策略。

设计依据:
    - GODEL_AGENT_PLAN_V3 §3.2: 三区 Token Budget 模型
    - TencentDB Context Offloading: 按需加载替代事后压缩
    - C4: 分层压缩（Token Pipeline）— Budget Manager 是 Pipeline 的预分配前端

三区模型:
    Zone A (Reserved, ~8K): identity + habits + PCG navigation + findings + CognitiveState
    Zone B (Paper, 0-40K): PCG 决定加载哪些 section（full/digest/name_only）
    Zone C (Dialogue, ~80K): 对话历史 + compaction

降级策略:
    GODEL_BUDGET_MANAGER_ENABLED=0 → 回退到 V2 被动压缩
    PCG 为空 → Zone B 返回空分配，不影响现有 assembler 行为
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from core.godel_config import (
    TOTAL_CONTEXT_WINDOW,
    ZONE_A_MIN_TOKENS,
    ZONE_A_DEFAULT_TOKENS,
    ZONE_B_MAX_TOKENS,
    compute_capacity_pct,
)

if TYPE_CHECKING:
    from core.paper_cognition_graph import PaperCognitionGraph

logger = logging.getLogger(__name__)


# ==============================================================
# Token Budget Manager
# ==============================================================

@dataclass
class ZoneBAllocation:
    """Zone B 分配结果。"""

    full_load: list[str] = field(default_factory=list)
    """完整加载的 sections（当前任务 section）"""

    digest_load: list[str] = field(default_factory=list)
    """摘要加载的 sections（逻辑依赖 + 假说相关）"""

    name_only: list[str] = field(default_factory=list)
    """仅名称的 sections（其余所有）"""

    estimated_tokens: int = 0
    """Zone B 预估 token 消耗"""


@dataclass
class TokenBudgetManager:
    """
    三区 Token Budget Manager。

    职责:
    - 计算 Zone B 内容分配（哪些 section 加载什么粒度）
    - 维护三区 token 预算约束
    - 为 Assembler 提供分配决策

    不做:
    - 不执行 compaction（CompactionEngine 的职责）
    - 不组装 context（Assembler 的职责）
    - 不控制 Agent 行为（C5）
    """

    total_budget: int = TOTAL_CONTEXT_WINDOW
    zone_a_budget: int = ZONE_A_DEFAULT_TOKENS
    zone_b_max: int = ZONE_B_MAX_TOKENS

    # 运行时状态
    _last_allocation: ZoneBAllocation | None = field(default=None, repr=False)

    @property
    def zone_c_budget(self) -> int:
        """Zone C 预算 = total - zone_a - zone_b_actual。"""
        zone_b_used = self._last_allocation.estimated_tokens if self._last_allocation else 0
        return self.total_budget - self.zone_a_budget - zone_b_used

    def compute_zone_b_allocation(
        self,
        pcg: "PaperCognitionGraph | None",
        current_task_section: str = "",
    ) -> ZoneBAllocation:
        """计算 Zone B 内容分配。

        策略:
        1. 当前任务 section → full_load
        2. PCG 边关联 sections → digest_load
        3. 假说相关 sections → digest_load
        4. 其余 → name_only
        5. 预算超限 → 将 digest_load 降级为 name_only（LRU 策略）

        Args:
            pcg: Paper Cognition Graph 实例（None 则返回空分配）
            current_task_section: 当前正在处理的 section 名称

        Returns:
            ZoneBAllocation 实例
        """
        if pcg is None or pcg.is_empty():
            self._last_allocation = ZoneBAllocation()
            return self._last_allocation

        full_load: list[str] = []
        digest_load: list[str] = []
        name_only: list[str] = []

        # 1. 当前 section → full load
        if current_task_section and current_task_section in pcg.nodes:
            full_load.append(current_task_section)

        # 2. 逻辑依赖 → digest load（1-hop edges）
        related = self._get_logically_related(pcg, current_task_section)
        for s in related:
            if s not in full_load:
                digest_load.append(s)

        # 3. 假说相关 → digest load
        hyp_sections = self._get_hypothesis_related_sections(pcg)
        for s in hyp_sections:
            if s not in full_load and s not in digest_load:
                digest_load.append(s)

        # 4. 其余 → name only
        for node_name in pcg.nodes:
            if node_name not in full_load and node_name not in digest_load:
                name_only.append(node_name)

        # 5. 预算约束: 超限时降级 digest → name_only
        estimated = self._estimate_tokens(pcg, full_load, digest_load, name_only)
        while estimated > self.zone_b_max and digest_load:
            lru_section = self._find_lru(pcg, digest_load)
            digest_load.remove(lru_section)
            name_only.append(lru_section)
            estimated = self._estimate_tokens(pcg, full_load, digest_load, name_only)

        allocation = ZoneBAllocation(
            full_load=full_load,
            digest_load=digest_load,
            name_only=name_only,
            estimated_tokens=estimated,
        )
        self._last_allocation = allocation

        logger.debug(
            "[TokenBudget] Zone B allocation: full=%d, digest=%d, name=%d, est=%dT",
            len(full_load), len(digest_load), len(name_only), estimated,
        )
        return allocation

    def get_budget_status(self, current_context_tokens: int = 0) -> dict:
        """返回当前三区预算状态（用于 CognitiveState 注入）。

        Args:
            current_context_tokens: 当前 context 实际已使用的 token 数。
                传入时会计算 used_pct 和 zone_label；不传则 used_pct=0.0, zone_label="green"。

        Returns:
            dict 包含: zone_a, zone_b_used, zone_b_max, zone_c_available, total,
                       used_pct (float 0~1), zone_label ("green"/"yellow"/"red")
        """
        zone_b_used = self._last_allocation.estimated_tokens if self._last_allocation else 0
        used_pct = self.compute_used_pct(current_context_tokens)
        return {
            "zone_a": self.zone_a_budget,
            "zone_b_used": zone_b_used,
            "zone_b_max": self.zone_b_max,
            "zone_c_available": self.total_budget - self.zone_a_budget - zone_b_used,
            "total": self.total_budget,
            "used_pct": used_pct,
            "zone_label": self._pct_to_zone_label(used_pct),
        }

    def compute_used_pct(self, current_context_tokens: int) -> float:
        """计算 context window 已用百分比 (0.0~1.0)。

        委托给 godel_config.compute_capacity_pct() 作为单一数据源，
        确保与 CompactionEngine.get_capacity_pct() 结果一致。

        当 total_budget <= 0（未配置）时安全返回 0.0，避免误触发压缩。

        Args:
            current_context_tokens: 当前已使用的 context token 数

        Returns:
            已用百分比，clamp 在 0.0~1.0 范围内
        """
        if self.total_budget <= 0:
            return 0.0
        return compute_capacity_pct(current_context_tokens, self.total_budget)

    @staticmethod
    def _pct_to_zone_label(used_pct: float) -> str:
        """将使用百分比映射为 zone label。

        阈值设计:
            - Green:  < 50%  — 充裕，正常工作
            - Yellow: 50%-80% — 注意，可主动做 session note
            - Red:    > 80%  — 紧急，应触发压缩
        """
        if used_pct < 0.5:
            return "green"
        elif used_pct < 0.8:
            return "yellow"
        else:
            return "red"

    def is_zone_a_safe(self, actual_zone_a_tokens: int) -> bool:
        """检查 Zone A 是否满足最低预算约束（宪法层）。"""
        return actual_zone_a_tokens >= ZONE_A_MIN_TOKENS

    # ==============================================================
    # Internal Helpers
    # ==============================================================

    @staticmethod
    def _get_logically_related(pcg: "PaperCognitionGraph", section: str) -> list[str]:
        """获取与当前 section 逻辑依赖的 sections（1-hop）。"""
        related: set[str] = set()
        for edge in pcg.edges:
            if edge.source == section and edge.target in pcg.nodes:
                related.add(edge.target)
            elif edge.target == section and edge.source in pcg.nodes:
                related.add(edge.source)
        return list(related)

    @staticmethod
    def _get_hypothesis_related_sections(pcg: "PaperCognitionGraph") -> list[str]:
        """获取有活跃假说关联的 sections。"""
        sections: set[str] = set()
        for node in pcg.nodes.values():
            if node.hypotheses_linked:
                sections.add(node.section_name)
        return list(sections)

    @staticmethod
    def _find_lru(pcg: "PaperCognitionGraph", sections: list[str]) -> str:
        """找到最近最少操作的 section（用于降级 digest → name_only）。

        活跃度 = findings_linked + hypotheses_linked 数量。最低者降级。
        """
        least_active = sections[0]
        least_activity = float("inf")
        for s in sections:
            node = pcg.nodes.get(s)
            if node:
                activity = len(node.findings_linked) + len(node.hypotheses_linked)
                if activity < least_activity:
                    least_activity = activity
                    least_active = s
        return least_active

    @staticmethod
    def _estimate_tokens(
        pcg: "PaperCognitionGraph",
        full: list[str],
        digest: list[str],
        names: list[str],
    ) -> int:
        """估算 Zone B token 消耗。

        粗估规则:
        - full load: word_count * 1.3（英文 word → token 约 1.3x）
        - digest: ~80 tokens/section（300 chars ≈ 80 tokens）
        - name only: ~5 tokens/section
        """
        total = 0
        for s in full:
            node = pcg.nodes.get(s)
            if node:
                total += int(node.word_count * 1.3)
        for _ in digest:
            total += 80
        for _ in names:
            total += 5
        return total
