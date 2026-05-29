"""
core/v2/phases.py — Phase FSM: 阶段管理与转换

设计原则:
    - Agent 的审稿过程自然分为几个"认知区域"
    - FSM 不控制 Agent 做什么，只管理"当前在哪个区域"
    - 阶段转换由 Agent 信号触发 + FSM 检查前置条件
    - 工具可见性随阶段变化（核心价值：减少噪声，防止过早行为）
    - 转换条件宽松：LLM 有自主权，FSM 只做最小守护

阶段定义:
    INITIAL_SCAN — 快速浏览论文结构，建立全局理解
    DEEP_REVIEW — 深入分析方法论/数据/逻辑
    EDITING     — 修改论文内容（审改一体）
    SYNTHESIS   — 整合发现，产出最终报告

转换规则:
    INITIAL_SCAN → DEEP_REVIEW: 已读 >= 2 个 sections（说明有了基本理解）
    DEEP_REVIEW  → EDITING:     有 >= 1 个 verified finding（有具体改进点）
    DEEP_REVIEW  → SYNTHESIS:   Agent 显式请求（无硬性前置）
    EDITING      → SYNTHESIS:   Agent 显式请求（无硬性前置）
    ANY          → ANY:         Agent 可以请求回退（FSM 允许非线性跳转）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class Phase(Enum):
    """Agent 的认知阶段。"""
    INITIAL_SCAN = "initial_scan"
    DEEP_REVIEW = "deep_review"
    EDITING = "editing"
    SYNTHESIS = "synthesis"


@dataclass
class TransitionResult:
    """阶段转换的结果。"""
    allowed: bool
    from_phase: Phase
    to_phase: Phase
    reason: str = ""


@dataclass
class PhaseState:
    """FSM 的内部状态。"""
    current: Phase = Phase.INITIAL_SCAN
    history: list[Phase] = field(default_factory=list)
    transition_count: int = 0


class PhaseFSM:
    """
    Phase 有限状态机。

    职责:
        - 管理当前阶段
        - 检查转换前置条件
        - 记录阶段历史
        - 提供转换建议信号（__NUDGE__）

    不做:
        - 不强制 Agent 停留在某个阶段
        - 不自动触发转换（只有 Agent 请求时才检查）
        - 不影响工具执行（只影响工具可见性）
    """

    def __init__(self, initial_phase: Phase = Phase.INITIAL_SCAN) -> None:
        self._state = PhaseState(current=initial_phase)

    @property
    def current_phase(self) -> Phase:
        """当前阶段。"""
        return self._state.current

    @property
    def phase_name(self) -> str:
        """当前阶段名（字符串）。"""
        return self._state.current.value

    @property
    def transition_count(self) -> int:
        """已发生的转换次数。"""
        return self._state.transition_count

    @property
    def history(self) -> list[Phase]:
        """阶段历史。"""
        return self._state.history.copy()

    def request_transition(
        self,
        target: Phase,
        sections_read: int = 0,
        verified_findings: int = 0,
    ) -> TransitionResult:
        """
        Agent 请求阶段转换。

        设计 (C2): 永远允许转换（除了幂等保护——已在目标阶段）。
        条件不满足时 reason 中包含 nudge 文本，由调用者传递给 Agent。

        Args:
            target: 目标阶段
            sections_read: 已读 section 数量
            verified_findings: 已验证的 finding 数量

        Returns:
            TransitionResult (allowed 仅在幂等保护时为 False)
        """
        current = self._state.current

        # 幂等保护：已在目标阶段
        if current == target:
            return TransitionResult(
                allowed=False,
                from_phase=current,
                to_phase=target,
                reason=f"Already in {target.value}",
            )

        # 检查条件（永远允许，可能附带 nudge）
        _ok, reason = self._check_precondition(
            current, target, sections_read, verified_findings
        )

        self._execute_transition(target)
        return TransitionResult(
            allowed=True,
            from_phase=current,
            to_phase=target,
            reason=reason,
        )

    def force_transition(self, target: Phase) -> None:
        """强制转换（用于测试或系统级操作，跳过前置条件检查）。"""
        self._execute_transition(target)

    def suggest_transition(
        self,
        sections_read: int = 0,
        verified_findings: int = 0,
        total_findings: int = 0,
        consecutive_no_new_findings: int = 0,
    ) -> Phase | None:
        """
        根据当前状态建议是否应该转换。返回建议的目标阶段，或 None 表示不建议。

        这个方法只是"建议"——Harness 可以把它作为 __NUDGE__ 信号发给 Agent，
        但 Agent 可以忽略。

        Args:
            sections_read: 已读 section 数
            verified_findings: 已验证 finding 数
            total_findings: 总 finding 数
            consecutive_no_new_findings: 连续多少轮没有新发现
        """
        current = self._state.current

        if current == Phase.INITIAL_SCAN:
            # 读了够多 sections，建议深入
            if sections_read >= 3:
                return Phase.DEEP_REVIEW

        elif current == Phase.DEEP_REVIEW:
            # 连续多轮没新发现，建议收尾
            if consecutive_no_new_findings >= 3 and total_findings >= 2:
                return Phase.SYNTHESIS

        elif current == Phase.EDITING:
            # 编辑阶段不主动建议——Agent 自己决定何时完成
            pass

        return None

    def get_phase_tools(self, phase: Phase | None = None) -> set[str]:
        """
        返回指定阶段可用的工具名集合。

        工具分类:
            - 通用工具: 所有阶段都可用
            - 阅读工具: INITIAL_SCAN + DEEP_REVIEW
            - 分析工具: DEEP_REVIEW + SYNTHESIS
            - 编辑工具: EDITING
            - 收尾工具: SYNTHESIS

        Args:
            phase: 目标阶段，默认为当前阶段
        """
        if phase is None:
            phase = self._state.current
        # 始终返回副本，防止调用者修改污染全局 _PHASE_TOOL_MAP
        tools = _PHASE_TOOL_MAP.get(phase)
        if tools is not None:
            return tools.copy()
        return _UNIVERSAL_TOOLS.copy()

    # ----------------------------------------------------------
    # 内部方法
    # ----------------------------------------------------------

    def _check_precondition(
        self,
        current: Phase,
        target: Phase,
        sections_read: int,
        verified_findings: int,
    ) -> tuple[bool, str]:
        """
        检查转换前置条件。返回 (是否允许, 原因/nudge)。

        设计原则 (C2: Constrain, don't control):
            永远返回 allowed=True。条件不满足时附带 nudge 文本，
            Agent 自主决定是否继续。代码不做阻断。
        """

        # INITIAL_SCAN → DEEP_REVIEW: 建议先建立全局理解
        if current == Phase.INITIAL_SCAN and target == Phase.DEEP_REVIEW:
            if sections_read >= 2:
                return True, "Sufficient sections read for deep review"
            return True, (
                f"⚠️ 你只读了 {sections_read} 个 sections（通常建议 >= 2）。"
                "审稿人一般先建立全局理解再深入分析。"
                "如果你确信当前信息足够开始深入，可以继续。"
            )

        # DEEP_REVIEW → EDITING: 建议先有具体改进点
        if current == Phase.DEEP_REVIEW and target == Phase.EDITING:
            if verified_findings >= 1:
                return True, "Has verified findings ready for editing"
            return True, (
                f"⚠️ 当前没有 verified finding（{verified_findings} 个）。"
                "通常进入编辑阶段前需要明确知道要改什么。"
                "如果你已有清晰的改进思路（只是尚未正式记录为 finding），可以继续。"
            )

        # 所有其他转换: 宽松允许（Agent 自主权）
        # 包括: DEEP_REVIEW→SYNTHESIS, EDITING→SYNTHESIS, 任何回退
        return True, "Agent-initiated transition (no hard precondition)"

    def _execute_transition(self, target: Phase) -> None:
        """执行阶段转换。"""
        old = self._state.current
        self._state.history.append(old)
        self._state.current = target
        self._state.transition_count += 1
        logger.info(
            "Phase transition: %s → %s (transition #%d)",
            old.value, target.value, self._state.transition_count,
        )


# ==============================================================
# 阶段-工具映射表
# ==============================================================

# 通用工具: 所有阶段都可用
_UNIVERSAL_TOOLS: set[str] = {
    "update_findings",
    "search_literature",
    "talk_to_user",
    "done",
    "reflect_and_plan",
    "cognitive_update",
    "request_phase_transition",
}

# 阅读相关工具
_READING_TOOLS: set[str] = {
    "read_section",
    "list_sections",
}

# 深度分析工具
_ANALYSIS_TOOLS: set[str] = {
    "fetch_paper_detail",
    "read_reference",
}

# 编辑工具
_EDITING_TOOLS: set[str] = {
    "apply_edit",
    "propose_edit",
}

# 收尾工具
_SYNTHESIS_TOOLS: set[str] = {
    "generate_report",
}

# 每个阶段的完整工具集
_PHASE_TOOL_MAP: dict[Phase, set[str]] = {
    Phase.INITIAL_SCAN: _UNIVERSAL_TOOLS | _READING_TOOLS,
    Phase.DEEP_REVIEW: _UNIVERSAL_TOOLS | _READING_TOOLS | _ANALYSIS_TOOLS,
    Phase.EDITING: _UNIVERSAL_TOOLS | _READING_TOOLS | _EDITING_TOOLS,
    Phase.SYNTHESIS: _UNIVERSAL_TOOLS | _ANALYSIS_TOOLS | _SYNTHESIS_TOOLS,
}
