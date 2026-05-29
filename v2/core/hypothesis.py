"""
core/v2/hypothesis.py — Hypothesis-Driven Working Memory (HD-WM)

Phase 5: 可插拔认知模块，为学术审阅任务提供假说驱动的工作记忆。

设计依据:
    - CoALA (2023): 认知架构中的 Working Memory 特化
    - 学术审阅是假说驱动的认知活动：阅读时产生假说 → 寻找证据 → 验证/推翻
    - PLAN_D_EXPLORATION.md §2.1: 核心数据结构设计

核心原则:
    - 可插拔: enable_hdwm=True 激活，False 时零副作用
    - 建议而非强制: LLM 可以不用假说工具，Harness 只管理生命周期
    - 假说质量完全依赖 LLM，本模块只做结构化管理
    - 最小版本: 假说 + 证据 + 解决率，队列优化后续再加

与其他模块的关系:
    - Harness: 通过 _init_tool_registry 注册 3 个假说工具
    - Assembler: hypothesis_status section 条件注入假说状态
    - PhaseFSM: generate_hypothesis 在 INITIAL_SCAN + DEEP_REVIEW 可见
    - Loop: review_readiness 可作为终止条件的参考信号
"""

from __future__ import annotations

import uuid
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ============================================================
# Enums
# ============================================================

class HypothesisStatus(Enum):
    """假说状态。"""
    ACTIVE = "active"           # 正在验证中
    SUPPORTED = "supported"     # 有充分证据支持
    REFUTED = "refuted"         # 被证据推翻
    SUSPENDED = "suspended"     # 暂时搁置（证据不足）


class EvidenceType(Enum):
    """证据类型。"""
    DIRECT = "direct"           # 直接证据（原文引用）
    INDIRECT = "indirect"       # 间接证据（推理得出）
    ABSENCE = "absence"         # 缺失型证据（应该有但没有）


# ============================================================
# Data Classes
# ============================================================

@dataclass
class Evidence:
    """支持或反对假说的证据。"""
    content: str                # 证据内容
    source: str                 # 来源（section 名 / 外部文献）
    direction: str              # "for" 或 "against"
    strength: float             # 证据强度 0.0 - 1.0
    type: EvidenceType = EvidenceType.DIRECT
    added_at_turn: int = 0


@dataclass
class Hypothesis:
    """一个可验证的学术假说。"""
    id: str
    statement: str              # "该论文的 baseline 对比不公平"
    source: str                 # 假说产生时正在读的 section
    status: HypothesisStatus = HypothesisStatus.ACTIVE
    evidence: list[Evidence] = field(default_factory=list)
    created_at_turn: int = 0
    resolved_at_turn: int | None = None
    resolution_reason: str = ""

    @property
    def evidence_for(self) -> list[Evidence]:
        """支持证据列表。"""
        return [e for e in self.evidence if e.direction == "for"]

    @property
    def evidence_against(self) -> list[Evidence]:
        """反对证据列表。"""
        return [e for e in self.evidence if e.direction == "against"]

    @property
    def evidence_balance(self) -> float:
        """
        证据平衡度: 正值倾向 supported, 负值倾向 refuted。
        范围: [-1.0, 1.0]

        计算方式: (加权正面 - 加权反面) / max(加权正面 + 加权反面, 1.0)
        """
        weighted_for = sum(e.strength for e in self.evidence_for)
        weighted_against = sum(e.strength for e in self.evidence_against)
        total = weighted_for + weighted_against
        if total == 0:
            return 0.0
        return (weighted_for - weighted_against) / total

    @property
    def is_resolved(self) -> bool:
        """假说是否已解决（非 ACTIVE 状态）。"""
        return self.status != HypothesisStatus.ACTIVE

    def to_dict(self) -> dict[str, Any]:
        """序列化为 dict（用于 JSON 存储/日志）。"""
        return {
            "id": self.id,
            "statement": self.statement,
            "source": self.source,
            "status": self.status.value,
            "evidence_count": len(self.evidence),
            "evidence_for_count": len(self.evidence_for),
            "evidence_against_count": len(self.evidence_against),
            "evidence_balance": round(self.evidence_balance, 3),
            "created_at_turn": self.created_at_turn,
            "resolved_at_turn": self.resolved_at_turn,
            "resolution_reason": self.resolution_reason,
        }


# ============================================================
# HypothesisModule — 生命周期管理
# ============================================================

class HypothesisModule:
    """
    Hypothesis-Driven Working Memory 模块。

    职责:
        1. 假说生命周期管理（生成/添加证据/解决）
        2. review_readiness 计算（基于假说解决率）
        3. 饱和检测（saturation signal）
        4. 格式化假说状态（供 assembler section 注入）

    用法:
        module = HypothesisModule()
        hyp = module.generate("baseline 对比不公平", source="experiments")
        module.add_evidence(hyp.id, "表1只对比了2个baseline", "for", 0.8)
        module.resolve(hyp.id, "supported", "证据充分")
        readiness = module.review_readiness
    """

    # --- 终止条件参数 ---
    READINESS_THRESHOLD = 0.8       # 解决率达到此值视为"可以结束"
    SATURATION_WINDOW = 3           # 连续 N 轮无新假说产生视为饱和

    def __init__(self) -> None:
        self._hypotheses: list[Hypothesis] = []
        self._turns_since_last_hypothesis: int = 0
        self._current_turn: int = 0

    # ----------------------------------------------------------
    # Properties
    # ----------------------------------------------------------

    @property
    def hypotheses(self) -> list[Hypothesis]:
        """所有假说（按创建时间排序）。"""
        return list(self._hypotheses)

    @property
    def active_hypotheses(self) -> list[Hypothesis]:
        """当前活跃（未解决）的假说。"""
        return [h for h in self._hypotheses if h.status == HypothesisStatus.ACTIVE]

    @property
    def resolved_hypotheses(self) -> list[Hypothesis]:
        """已解决的假说。"""
        return [h for h in self._hypotheses if h.is_resolved]

    @property
    def resolution_rate(self) -> float:
        """假说解决率: resolved / total。无假说时返回 0.0。"""
        total = len(self._hypotheses)
        if total == 0:
            return 0.0
        return len(self.resolved_hypotheses) / total

    @property
    def review_readiness(self) -> float:
        """
        审稿完成度。

        计算逻辑:
            - 无假说: 0.0（还没开始假说驱动的认知）
            - 解决率 * 0.7 + 覆盖度奖励 * 0.3
            - 覆盖度奖励: 假说数量达到阈值（>=3）时给满分

        范围: [0.0, 1.0]
        """
        total = len(self._hypotheses)
        if total == 0:
            return 0.0

        # 解决率贡献 (70%)
        resolution_component = self.resolution_rate * 0.7

        # 覆盖度贡献 (30%) — 至少产生 3 个假说才给满分
        coverage_component = min(total / 3.0, 1.0) * 0.3

        return min(resolution_component + coverage_component, 1.0)

    @property
    def is_saturated(self) -> bool:
        """饱和信号: 连续 N 轮未产生新假说。"""
        return self._turns_since_last_hypothesis >= self.SATURATION_WINDOW

    @property
    def is_ready(self) -> bool:
        """是否达到 review_readiness 阈值。"""
        return self.review_readiness >= self.READINESS_THRESHOLD

    # ----------------------------------------------------------
    # Lifecycle Operations
    # ----------------------------------------------------------

    def generate(self, statement: str, source: str, turn: int = 0) -> Hypothesis:
        """
        产生一个新假说。

        Args:
            statement: 假说陈述
            source: 产生假说时正在读的 section
            turn: 当前轮次

        Returns:
            新创建的 Hypothesis 实例
        """
        hyp_id = f"H{len(self._hypotheses) + 1:03d}"
        hyp = Hypothesis(
            id=hyp_id,
            statement=statement,
            source=source,
            created_at_turn=turn,
        )
        self._hypotheses.append(hyp)
        self._turns_since_last_hypothesis = 0
        self._current_turn = turn
        logger.debug(f"HD-WM: 产生假说 {hyp_id}: {statement[:60]}")
        return hyp

    def add_evidence(
        self,
        hyp_id: str,
        content: str,
        direction: str,
        strength: float,
        source: str = "",
        evidence_type: str = "direct",
        turn: int = 0,
    ) -> Evidence | None:
        """
        为假说添加证据。

        Args:
            hyp_id: 假说 ID
            content: 证据内容
            direction: "for" 或 "against"
            strength: 证据强度 0.0 - 1.0
            source: 证据来源
            evidence_type: "direct" / "indirect" / "absence"
            turn: 当前轮次

        Returns:
            新创建的 Evidence 实例，如果假说不存在或已解决则返回 None
        """
        hyp = self._get_hypothesis(hyp_id)
        if hyp is None:
            logger.warning(f"HD-WM: 假说 {hyp_id} 不存在")
            return None
        if hyp.is_resolved:
            logger.warning(f"HD-WM: 假说 {hyp_id} 已解决，不能添加证据")
            return None

        # 校验参数
        direction = direction.lower()
        if direction not in ("for", "against"):
            logger.warning(f"HD-WM: direction 必须是 'for' 或 'against'，得到 '{direction}'")
            return None
        strength = max(0.0, min(1.0, strength))

        # 解析 evidence_type
        try:
            etype = EvidenceType(evidence_type.lower())
        except ValueError:
            etype = EvidenceType.DIRECT

        evidence = Evidence(
            content=content,
            source=source,
            direction=direction,
            strength=strength,
            type=etype,
            added_at_turn=turn,
        )
        hyp.evidence.append(evidence)
        self._current_turn = turn
        logger.debug(f"HD-WM: 为 {hyp_id} 添加证据 ({direction}, {strength:.2f})")
        return evidence

    def resolve(
        self,
        hyp_id: str,
        status: str,
        reason: str = "",
        turn: int = 0,
    ) -> bool:
        """
        解决一个假说。

        Args:
            hyp_id: 假说 ID
            status: "supported" / "refuted" / "suspended"
            reason: 解决理由
            turn: 当前轮次

        Returns:
            True 如果成功解决，False 如果假说不存在/已解决/状态无效
        """
        hyp = self._get_hypothesis(hyp_id)
        if hyp is None:
            logger.warning(f"HD-WM: 假说 {hyp_id} 不存在")
            return False
        if hyp.is_resolved:
            logger.warning(f"HD-WM: 假说 {hyp_id} 已经解决")
            return False

        # 解析状态
        status_map = {
            "supported": HypothesisStatus.SUPPORTED,
            "refuted": HypothesisStatus.REFUTED,
            "suspended": HypothesisStatus.SUSPENDED,
        }
        new_status = status_map.get(status.lower())
        if new_status is None:
            logger.warning(f"HD-WM: 无效状态 '{status}'")
            return False

        hyp.status = new_status
        hyp.resolved_at_turn = turn
        hyp.resolution_reason = reason
        self._current_turn = turn
        logger.debug(f"HD-WM: 解决 {hyp_id} → {status}")
        return True

    def tick(self, turn: int) -> None:
        """
        每轮调用一次，更新饱和检测计数器。

        在 loop.py 的每轮开始时调用。如果本轮没有 generate() 被调用，
        turns_since_last_hypothesis 递增。
        """
        old_turn = self._current_turn
        self._current_turn = turn
        if turn > old_turn:
            self._turns_since_last_hypothesis += 1

    # ----------------------------------------------------------
    # Query
    # ----------------------------------------------------------

    def get_hypothesis(self, hyp_id: str) -> Hypothesis | None:
        """根据 ID 获取假说（公开接口）。"""
        return self._get_hypothesis(hyp_id)

    def format_status(self) -> str:
        """
        格式化假说状态，供 ContextAssembler 的 hypothesis_status section 使用。

        输出紧凑的文本摘要，让 LLM 了解当前假说工作记忆的状态。
        """
        if not self._hypotheses:
            return ""

        parts = []
        total = len(self._hypotheses)
        active = self.active_hypotheses
        resolved = self.resolved_hypotheses

        # 概要行
        parts.append(
            f"假说工作记忆 | 总计 {total} | "
            f"活跃 {len(active)} | 已解决 {len(resolved)} | "
            f"解决率 {self.resolution_rate:.0%} | "
            f"完成度 {self.review_readiness:.0%}"
        )

        # 活跃假说（最多展示 5 个）
        if active:
            parts.append("  活跃假说:")
            for h in active[:5]:
                ev_summary = f"+{len(h.evidence_for)}/-{len(h.evidence_against)}"
                balance_arrow = "→" if abs(h.evidence_balance) < 0.3 else ("↑" if h.evidence_balance > 0 else "↓")
                parts.append(
                    f"    [{h.id}] {h.statement[:80]} "
                    f"(证据{ev_summary} {balance_arrow})"
                )
            if len(active) > 5:
                parts.append(f"    ... 还有 {len(active) - 5} 个活跃假说")

        # 已解决假说（最多展示 3 个最近的）
        if resolved:
            recent_resolved = sorted(resolved, key=lambda h: h.resolved_at_turn or 0, reverse=True)[:3]
            parts.append("  最近解决:")
            for h in recent_resolved:
                icon = {"supported": "✓", "refuted": "✗", "suspended": "~"}.get(h.status.value, "?")
                parts.append(f"    [{h.id}] {icon} {h.statement[:60]} — {h.resolution_reason[:40]}")

        # 元信号
        signals = []
        if self.is_saturated:
            signals.append("饱和(连续无新假说)")
        if self.is_ready:
            signals.append("可结束(完成度达标)")
        if signals:
            parts.append(f"  信号: {', '.join(signals)}")

        return "\n".join(parts)

    def format_for_restoration(self) -> str:
        """
        格式化假说状态用于 Compaction 恢复注入。

        直接复用 format_status() 的结构化格式——该格式已验证过，
        结构化信息（ID、证据计数、balance、resolution_reason）
        能让模型精确恢复对假说的追踪。
        """
        status = self.format_status()
        if not status:
            return ""
        return f"[假说工作记忆恢复]\n{status}"

    def has_active(self) -> bool:
        """是否有活跃假说（供 compaction 判断是否需要注入）。"""
        return len(self.active_hypotheses) > 0

    # ----------------------------------------------------------
    # Internal
    # ----------------------------------------------------------

    def _get_hypothesis(self, hyp_id: str) -> Hypothesis | None:
        """根据 ID 查找假说。"""
        for h in self._hypotheses:
            if h.id == hyp_id:
                return h
        return None

    def reset(self) -> None:
        """重置模块状态（用于测试或新会话）。"""
        self._hypotheses.clear()
        self._turns_since_last_hypothesis = 0
        self._current_turn = 0
