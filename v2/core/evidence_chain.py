"""
core/evidence_chain.py — EvidenceChain 全链路追溯

V3 Phase 0.5: 记录每个 finding 的完整推理链。

设计依据:
    - GODEL_AGENT_PLAN_V3 §4.2.2: EvidenceChain Data Structure
    - C8: 外部度量锚点 — chain_length + pcg_edges_used 作为 finding 质量信号
    - 宪法层: 高优先级 finding 必须有 EvidenceChain >= 2 steps

用途:
    1. Compaction 时: 完整 chain offload 到 .workspace/refs/，context 只留 1-line summary
    2. 用户质疑时: 回溯完整 chain
    3. MetaReflector: chain_length + pcg_edges_used 评估 finding 质量
    4. Evolution: 高质量 chain pattern 可被学习

集成点:
    - finding 创建时: tracker.start_chain(finding_id, ...)
    - read_section 时: tracker.add_step_to_recent("read_section", ...)
    - hypothesis_formed 时: tracker.add_step_to_recent("hypothesis_formed", ...)
    - session 结束时: tracker.finalize_all() → list[EvidenceChain]

降级策略:
    GODEL_EVIDENCE_CHAIN_ENABLED=0 → 不记录推理链
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ==============================================================
# Data Structures
# ==============================================================

@dataclass
class EvidenceStep:
    """推理链中的一步。

    Attributes:
        action: 动作类型
        target: 目标（section name / literature title / hypothesis content）
        observation: <=120 char 关键发现
        turn: 发生的轮次
        pcg_edge_used: 使用了哪条 PCG 边（空字符串表示未使用）
    """

    action: str          # "read_section" | "hypothesis_formed" | "search_literature" | "cross_ref_followed"
    target: str
    observation: str = ""
    turn: int = 0
    pcg_edge_used: str = ""


@dataclass
class EvidenceChain:
    """
    一个 finding 的完整推理链。

    用于:
    - Compaction 时: offload 全文，context 只保留 summary
    - 用户质疑时: recall 完整链
    - MetaReflector: chain_length + pcg_edges_used 评估质量
    - Evolution: 高质量模式学习
    """

    finding_id: str
    finding_text: str = ""
    priority: str = "medium"     # "high" | "medium" | "low"
    steps: list[EvidenceStep] = field(default_factory=list)

    @property
    def chain_length(self) -> int:
        """推理链长度。"""
        return len(self.steps)

    @property
    def total_turns_span(self) -> int:
        """推理链跨越的轮次范围。"""
        if not self.steps:
            return 0
        turns = [s.turn for s in self.steps if s.turn > 0]
        if not turns:
            return 0
        return max(turns) - min(turns) + 1

    @property
    def pcg_edges_used(self) -> int:
        """使用了多少 PCG 边。"""
        return sum(1 for s in self.steps if s.pcg_edge_used)

    @property
    def summary(self) -> str:
        """1-line summary（compaction 后 context 保留用）。"""
        if not self.steps:
            return f"[{self.finding_id}] {self.finding_text[:80]}"
        first = self.steps[0]
        last = self.steps[-1]
        return (
            f"[{self.finding_id}] {self.finding_text[:60]} "
            f"(chain: {first.action}->...-> {last.action}, "
            f"{self.chain_length} steps, turns {first.turn}-{last.turn})"
        )

    def format_full(self) -> str:
        """完整格式（用于回溯或审计）。"""
        lines = [f"=== Evidence Chain: {self.finding_id} ==="]
        lines.append(f"Finding: {self.finding_text}")
        lines.append(
            f"Priority: {self.priority} | Steps: {self.chain_length} | "
            f"Turns: {self.total_turns_span} | PCG edges: {self.pcg_edges_used}"
        )
        lines.append("")
        for i, step in enumerate(self.steps, 1):
            edge_info = f" [PCG: {step.pcg_edge_used}]" if step.pcg_edge_used else ""
            lines.append(f"  {i}. [{step.action}] {step.target}{edge_info}")
            if step.observation:
                lines.append(f"     -> {step.observation}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """序列化为 dict（用于 JSON 存储）。"""
        return {
            "finding_id": self.finding_id,
            "finding_text": self.finding_text,
            "priority": self.priority,
            "chain_length": self.chain_length,
            "turns_span": self.total_turns_span,
            "pcg_edges_used": self.pcg_edges_used,
            "steps": [
                {
                    "action": s.action,
                    "target": s.target,
                    "observation": s.observation,
                    "turn": s.turn,
                    "pcg_edge": s.pcg_edge_used,
                }
                for s in self.steps
            ],
        }


# ==============================================================
# Tracker
# ==============================================================

class EvidenceChainTracker:
    """
    自动追踪 findings 的 evidence chains。

    集成方式:
        - On finding creation: tracker.start_chain(finding_id, text, priority)
        - On cognitive action: tracker.add_step_to_recent(action, target, ...)
        - On session end: tracker.finalize_all() → list[EvidenceChain]

    设计:
        - _active_chains: 当前正在构建的 chains（finding_id → EvidenceChain）
        - _completed_chains: 已 finalize 的 chains
        - add_step_to_recent: 将步骤添加到最近创建的 active chain
    """

    def __init__(self) -> None:
        self._active_chains: dict[str, EvidenceChain] = {}
        self._completed_chains: list[EvidenceChain] = []
        self._recent_finding_id: str | None = None  # 最近的 finding ID

    def start_chain(self, finding_id: str, finding_text: str = "", priority: str = "medium") -> None:
        """开始一条新的 evidence chain。

        在 Agent 创建 finding 时调用。
        """
        chain = EvidenceChain(
            finding_id=finding_id,
            finding_text=finding_text,
            priority=priority,
        )
        self._active_chains[finding_id] = chain
        self._recent_finding_id = finding_id
        logger.debug("[EvidenceChain] Started chain for finding '%s'", finding_id)

    def add_step(
        self,
        finding_id: str,
        action: str,
        target: str,
        observation: str = "",
        turn: int = 0,
        pcg_edge: str = "",
    ) -> None:
        """向指定 chain 添加一步。"""
        chain = self._active_chains.get(finding_id)
        if chain is None:
            return  # 无对应 chain，静默跳过

        step = EvidenceStep(
            action=action,
            target=target,
            observation=observation[:120],  # 硬限 120 chars
            turn=turn,
            pcg_edge_used=pcg_edge,
        )
        chain.steps.append(step)

    def add_step_to_recent(
        self,
        action: str,
        target: str,
        observation: str = "",
        turn: int = 0,
        pcg_edge: str = "",
    ) -> None:
        """向最近创建的 active chain 添加一步。

        简化调用——当不确定具体关联哪个 finding 时，
        追踪到最近创建的 chain（启发式：近期认知活动通常指向最新 finding）。
        """
        if self._recent_finding_id is None:
            return
        self.add_step(
            finding_id=self._recent_finding_id,
            action=action,
            target=target,
            observation=observation,
            turn=turn,
            pcg_edge=pcg_edge,
        )

    def complete_chain(self, finding_id: str) -> EvidenceChain | None:
        """完成一条 chain（从 active 移到 completed）。"""
        chain = self._active_chains.pop(finding_id, None)
        if chain:
            self._completed_chains.append(chain)
            logger.debug(
                "[EvidenceChain] Completed chain '%s': %d steps",
                finding_id, chain.chain_length,
            )
        return chain

    def finalize_all(self) -> list[EvidenceChain]:
        """Session 结束时：关闭所有 active chains。

        Returns:
            所有 chains（active + completed）
        """
        for finding_id in list(self._active_chains.keys()):
            self.complete_chain(finding_id)

        all_chains = list(self._completed_chains)
        logger.info(
            "[EvidenceChain] Finalized: %d chains, avg length %.1f",
            len(all_chains),
            sum(c.chain_length for c in all_chains) / max(len(all_chains), 1),
        )
        return all_chains

    def get_all_summaries(self) -> str:
        """获取所有 chains 的 1-line summaries（用于 compaction 后恢复）。"""
        all_chains = list(self._completed_chains) + list(self._active_chains.values())
        if not all_chains:
            return ""
        return "\n".join(c.summary for c in all_chains)

    def get_chain(self, finding_id: str) -> EvidenceChain | None:
        """获取指定 finding 的 chain（active 或 completed）。"""
        if finding_id in self._active_chains:
            return self._active_chains[finding_id]
        for chain in self._completed_chains:
            if chain.finding_id == finding_id:
                return chain
        return None

    @property
    def active_count(self) -> int:
        """当前活跃 chain 数量。"""
        return len(self._active_chains)

    @property
    def total_count(self) -> int:
        """所有 chain 数量（active + completed）。"""
        return len(self._active_chains) + len(self._completed_chains)

    def reset(self) -> None:
        """重置（新 session 时调用）。"""
        self._active_chains.clear()
        self._completed_chains.clear()
        self._recent_finding_id = None
