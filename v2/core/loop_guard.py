"""
core/loop_guard.py — Doom Loop 模式检测器 (Phase 1 MVP + Complete)

从简单的硬性计数器升级为模式感知的循环检测。
检测三类重复模式：
  1. EXACT_REPEAT — 完全相同的工具+参数重复
  2. PARAM_DRIFT — 同一工具、参数微变但结果相同（如换关键词但都失败）
  3. OSCILLATION — A→B→A→B 交替死锁

Complete 层增强：
  - 策略注册表：将恢复策略做成 pluggable handler，不同 phase 可注册不同恢复行为
  - 上下文感知恢复：根据 phase 选择不同话术和备选工具
  - 恢复效果追踪：记录每次恢复事件，持久化并导出给 evolution 系统
  - 死亡螺旋升级机制（MVP 已有，Complete 层增强追踪）

设计原则（COGNITIVE_ANCHOR §4.3 约束-而非-控制）：
  - 检测到模式后不直接终止，而是注入恢复 message，给 Agent 机会自救
  - 恢复策略按模式类型和当前 phase 选择不同的干预力度
  - 死亡螺旋升级：恢复后仍循环 → 二次干预 → 最终硬截断
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Any, Protocol, runtime_checkable
import hashlib
import json
import os
import time


# ==============================================================
# Kill Switch (默认 ON — 即不设置环境变量时功能开启)
# ==============================================================

def _env_enabled(key: str, default: bool = True) -> bool:
    """读取环境变量控制开关。'0'/'false'/'off'/'no'/'disabled' 为关闭。"""
    val = os.environ.get(key, "")
    if not val:
        return default
    return val.lower() not in ("0", "false", "off", "no", "disabled")


LOOP_GUARD_ENABLED = _env_enabled("SCHOLAR_GODEL_LOOP_GUARD", True)
"""循环模式检测 + 恢复干预。关闭时 detect() 始终返回 None，record_call 仍正常记录。"""


# ==============================================================
# 数据类型定义
# ==============================================================

class LoopPattern(Enum):
    """检测到的循环模式类型"""
    EXACT_REPEAT = "exact_repeat"       # 完全相同的调用反复出现
    PARAM_DRIFT = "param_drift"         # 同一工具，参数微变但结果均失败
    OSCILLATION = "oscillation"         # A→B→A→B 交替模式


@dataclass
class ToolCallRecord:
    """一次工具调用的完整记录"""
    tool_name: str
    params: dict
    success: bool
    result_hash: str    # 结果内容的哈希（用于快速比较）
    turn: int = 0       # 发生的轮次

    def signature(self) -> str:
        """工具名+参数的规范化签名，用于精确匹配"""
        params_str = json.dumps(self.params, sort_keys=True, ensure_ascii=False)
        return f"{self.tool_name}::{params_str}"

    def tool_signature(self) -> str:
        """仅工具名签名，用于宽泛匹配"""
        return self.tool_name


@dataclass
class RecoveryAction:
    """恢复动作描述"""
    pattern: LoopPattern
    message: str                   # 注入到对话历史的恢复消息
    escalation_level: int = 0     # 升级次数（0=首次，1=二次，2=强制终止）
    suggest_tools: list[str] = field(default_factory=list)  # 建议使用的替代工具


# ==============================================================
# Complete 层: 恢复上下文
# ==============================================================

@dataclass
class RecoveryContext:
    """传递给恢复策略的完整上下文信息。

    让策略 handler 能根据当前 phase、论文特征、历史记录等
    产生上下文感知的恢复话术和工具建议。
    """
    pattern: LoopPattern
    current_phase: str
    current_turn: int
    escalation_count: int
    repeated_tool: str              # 导致循环的工具名
    repeated_params: dict           # 最近一次的参数
    recent_calls: list[ToolCallRecord]  # 最近 window_size 次调用
    available_tools: list[str]      # 当前可用工具列表
    window_size: int = 5


# ==============================================================
# Complete 层: 恢复策略 Protocol + 注册表
# ==============================================================

@runtime_checkable
class RecoveryStrategyHandler(Protocol):
    """可插拔恢复策略的 Protocol。

    任何实现了此 Protocol 的 callable 或类都可以注册为恢复策略。
    """

    def handle(self, ctx: RecoveryContext) -> RecoveryAction:
        """根据上下文生成恢复动作。"""
        ...


class RecoveryRegistry:
    """恢复策略注册表 (Phase 1 Complete)。

    支持按 (pattern, phase) 注册策略 handler：
    - 精确匹配：(EXACT_REPEAT, "methodology_analysis") → 特定 handler
    - Phase 通配：(EXACT_REPEAT, "*") → 对所有 phase 生效的 fallback
    - 查找优先级：精确匹配 > Phase 通配 > 内置默认

    不同 phase 可以注册不同的恢复行为，实现上下文感知恢复。
    """

    def __init__(self):
        # key: (LoopPattern, phase_name)，phase_name = "*" 表示通配
        self._handlers: dict[tuple[LoopPattern, str], RecoveryStrategyHandler] = {}

    def register(
        self,
        pattern: LoopPattern,
        handler: RecoveryStrategyHandler,
        phase: str = "*",
    ) -> None:
        """注册一个恢复策略 handler。

        Args:
            pattern: 适用的循环模式
            handler: 实现了 RecoveryStrategyHandler Protocol 的对象
            phase: 适用的 phase 名称，"*" 表示通配所有 phase
        """
        self._handlers[(pattern, phase)] = handler

    def unregister(self, pattern: LoopPattern, phase: str = "*") -> bool:
        """注销一个恢复策略。Returns True if found and removed."""
        key = (pattern, phase)
        if key in self._handlers:
            del self._handlers[key]
            return True
        return False

    def get_handler(
        self, pattern: LoopPattern, phase: str = ""
    ) -> RecoveryStrategyHandler | None:
        """根据模式和 phase 查找最匹配的 handler。

        优先级：精确匹配 (pattern, phase) > 通配匹配 (pattern, "*")
        """
        # 精确匹配
        if phase and (pattern, phase) in self._handlers:
            return self._handlers[(pattern, phase)]
        # 通配匹配
        if (pattern, "*") in self._handlers:
            return self._handlers[(pattern, "*")]
        return None

    @property
    def registered_count(self) -> int:
        return len(self._handlers)

    def list_registered(self) -> list[tuple[str, str]]:
        """列出所有注册的 (pattern_name, phase) 对。"""
        return [(p.value, ph) for (p, ph) in self._handlers.keys()]


# ==============================================================
# Complete 层: 恢复效果追踪
# ==============================================================

class RecoveryOutcome(Enum):
    """恢复尝试的最终结果"""
    SUCCESS = "success"             # 成功脱离循环
    FAILED = "failed"              # 恢复后仍循环
    ESCALATED = "escalated"        # 触发了升级
    TERMINATED = "terminated"      # 达到最大升级，强制终止


@dataclass
class RecoveryRecord:
    """一次恢复事件的完整记录。用于效果追踪和 evolution 系统学习。"""
    timestamp: float                    # 发生时间
    pattern: LoopPattern                # 检测到的模式
    phase: str                          # 发生时的 phase
    turn: int                           # 轮次
    escalation_level: int               # 升级级别
    recovery_message: str               # 使用的恢复消息
    suggested_tools: list[str]          # 建议的替代工具
    outcome: RecoveryOutcome = RecoveryOutcome.FAILED  # 默认未确认，后续更新
    turns_until_resolved: int = -1      # 恢复后多少轮脱离（-1=未脱离）
    handler_name: str = ""              # 使用的策略 handler 名称


class RecoveryTracker:
    """恢复效果追踪器 (Phase 1 Complete)。

    记录每次恢复事件，追踪是否成功脱离循环，
    提供持久化和导出给 evolution 系统的接口。
    """

    def __init__(self):
        self._records: list[RecoveryRecord] = []
        self._pending_record: RecoveryRecord | None = None  # 等待确认结果的记录

    def record_recovery_attempt(
        self,
        pattern: LoopPattern,
        phase: str,
        turn: int,
        escalation_level: int,
        recovery_message: str,
        suggested_tools: list[str],
        handler_name: str = "",
    ) -> None:
        """记录一次恢复尝试。结果稍后通过 confirm_outcome 更新。"""
        # 如果之前有未确认的记录，标记为 FAILED
        if self._pending_record is not None:
            self._pending_record.outcome = RecoveryOutcome.FAILED
            self._records.append(self._pending_record)

        self._pending_record = RecoveryRecord(
            timestamp=time.time(),
            pattern=pattern,
            phase=phase,
            turn=turn,
            escalation_level=escalation_level,
            recovery_message=recovery_message,
            suggested_tools=suggested_tools,
            handler_name=handler_name,
        )

    def confirm_success(self, current_turn: int) -> None:
        """确认上次恢复成功（agent 成功脱离了循环）。"""
        if self._pending_record is not None:
            self._pending_record.outcome = RecoveryOutcome.SUCCESS
            self._pending_record.turns_until_resolved = (
                current_turn - self._pending_record.turn
            )
            self._records.append(self._pending_record)
            self._pending_record = None

    def confirm_escalation(self) -> None:
        """确认上次恢复失败并触发了升级。"""
        if self._pending_record is not None:
            self._pending_record.outcome = RecoveryOutcome.ESCALATED
            self._records.append(self._pending_record)
            self._pending_record = None

    def confirm_termination(self) -> None:
        """确认达到最大升级，任务被强制终止。"""
        if self._pending_record is not None:
            self._pending_record.outcome = RecoveryOutcome.TERMINATED
            self._records.append(self._pending_record)
            self._pending_record = None

    def flush_pending(self) -> None:
        """将未确认的记录作为 FAILED 归档（如 session 结束时）。"""
        if self._pending_record is not None:
            self._pending_record.outcome = RecoveryOutcome.FAILED
            self._records.append(self._pending_record)
            self._pending_record = None

    @property
    def records(self) -> list[RecoveryRecord]:
        """所有已确认的恢复记录。"""
        return list(self._records)

    @property
    def pending(self) -> RecoveryRecord | None:
        """当前等待确认的记录。"""
        return self._pending_record

    def get_stats(self) -> dict[str, Any]:
        """获取恢复效果统计摘要。"""
        if not self._records:
            return {
                "total_attempts": 0,
                "success_rate": 0.0,
                "avg_turns_to_resolve": 0.0,
                "by_pattern": {},
                "by_phase": {},
            }

        total = len(self._records)
        successes = [r for r in self._records if r.outcome == RecoveryOutcome.SUCCESS]
        success_rate = len(successes) / total if total > 0 else 0.0

        resolved_turns = [r.turns_until_resolved for r in successes if r.turns_until_resolved > 0]
        avg_turns = sum(resolved_turns) / len(resolved_turns) if resolved_turns else 0.0

        # 按模式统计
        by_pattern: dict[str, dict[str, int]] = {}
        for r in self._records:
            p = r.pattern.value
            if p not in by_pattern:
                by_pattern[p] = {"total": 0, "success": 0, "failed": 0, "escalated": 0, "terminated": 0}
            by_pattern[p]["total"] += 1
            by_pattern[p][r.outcome.value] += 1

        # 按 phase 统计
        by_phase: dict[str, dict[str, int]] = {}
        for r in self._records:
            ph = r.phase or "unknown"
            if ph not in by_phase:
                by_phase[ph] = {"total": 0, "success": 0, "failed": 0, "escalated": 0, "terminated": 0}
            by_phase[ph]["total"] += 1
            by_phase[ph][r.outcome.value] += 1

        return {
            "total_attempts": total,
            "success_rate": success_rate,
            "avg_turns_to_resolve": avg_turns,
            "by_pattern": by_pattern,
            "by_phase": by_phase,
        }

    def export_for_evolution(self) -> list[dict[str, Any]]:
        """导出恢复记录给 evolution 系统作为学习素材。

        Returns:
            可序列化为 JSON 的记录列表，包含：
            - 恢复事件的完整上下文
            - 结果（成功/失败/升级/终止）
            - 效率指标（脱离所需轮次）

        evolution 系统可以从中学习：
            - 哪些 phase 容易循环
            - 哪些恢复话术有效
            - 哪些工具建议被采纳
        """
        exported = []
        for r in self._records:
            exported.append({
                "timestamp": r.timestamp,
                "pattern": r.pattern.value,
                "phase": r.phase,
                "turn": r.turn,
                "escalation_level": r.escalation_level,
                "recovery_message_preview": r.recovery_message[:200],
                "suggested_tools": r.suggested_tools,
                "outcome": r.outcome.value,
                "turns_until_resolved": r.turns_until_resolved,
                "handler_name": r.handler_name,
                # 衍生特征（供 evolution 分析）
                "was_effective": r.outcome == RecoveryOutcome.SUCCESS,
                "required_escalation": r.outcome in (RecoveryOutcome.ESCALATED, RecoveryOutcome.TERMINATED),
            })
        return exported

    def serialize(self) -> list[dict[str, Any]]:
        """序列化所有记录为可持久化的 JSON 格式（无信息丢失）。"""
        exported = []
        for r in self._records:
            exported.append({
                "timestamp": r.timestamp,
                "pattern": r.pattern.value,
                "phase": r.phase,
                "turn": r.turn,
                "escalation_level": r.escalation_level,
                "recovery_message": r.recovery_message,  # 完整保留
                "suggested_tools": r.suggested_tools,
                "outcome": r.outcome.value,
                "turns_until_resolved": r.turns_until_resolved,
                "handler_name": r.handler_name,
            })
        return exported

    @classmethod
    def deserialize(cls, data: list[dict[str, Any]]) -> "RecoveryTracker":
        """从 JSON 数据恢复 tracker。兼容旧格式(recovery_message_preview)和新格式(recovery_message)。"""
        tracker = cls()
        for item in data:
            # 兼容旧格式：优先使用完整的 recovery_message，降级用 preview
            msg = item.get("recovery_message", item.get("recovery_message_preview", ""))
            record = RecoveryRecord(
                timestamp=item.get("timestamp", 0.0),
                pattern=LoopPattern(item["pattern"]),
                phase=item.get("phase", ""),
                turn=item.get("turn", 0),
                escalation_level=item.get("escalation_level", 0),
                recovery_message=msg,
                suggested_tools=item.get("suggested_tools", []),
                outcome=RecoveryOutcome(item.get("outcome", "failed")),
                turns_until_resolved=item.get("turns_until_resolved", -1),
                handler_name=item.get("handler_name", ""),
            )
            tracker._records.append(record)
        return tracker


# ==============================================================
# Complete 层: 内置 Phase-Aware 恢复策略
# ==============================================================

# Phase 分类及对应的工具优先级和话术风格
_PHASE_TOOL_PRIORITIES: dict[str, list[str]] = {
    "methodology_analysis": ["read_section", "search_literature", "reflect_and_plan"],
    "statistical_validation": ["read_section", "search_literature", "update_findings"],
    "literature_check": ["search_literature", "read_section", "reflect_and_plan"],
    "overall_assessment": ["reflect_and_plan", "update_findings", "read_section"],
    "deep_dive": ["read_section", "search_literature", "update_findings"],
}

_PHASE_RECOVERY_HINTS: dict[str, dict[str, str]] = {
    "methodology_analysis": {
        "exact_repeat": "在方法论分析阶段，如果反复阅读同一节无法获取新信息，考虑换到相关的实验设计或数据描述部分。",
        "param_drift": "方法论分析中搜索失败，可能是关键词选择问题。试试用论文中的具体方法名而非通用术语。",
        "oscillation": "方法论分析中的振荡通常表明你在「理解方法」和「评估方法」之间犹豫。先完成理解再评估。",
    },
    "statistical_validation": {
        "exact_repeat": "统计验证阶段如果反复读同一部分，可能是数据本身不足以支持验证。考虑在 findings 中如实记录。",
        "param_drift": "统计搜索失败可能是因为论文使用了非标准的统计术语。尝试直接引用论文中的表述。",
        "oscillation": "统计验证中的振荡可能是在「找原始数据」和「验证统计方法」之间摇摆。选择一个先做完。",
    },
    "literature_check": {
        "exact_repeat": "文献检索阶段的重复通常意味着当前数据库查询策略已穷尽。考虑改用不同的检索角度或记录检索局限性。",
        "param_drift": "多次搜索失败说明可能是领域术语问题。尝试使用作者自己在论文中引用的关键词。",
        "oscillation": "文献检索中的振荡表明你在多个相关领域之间无法抉择。聚焦到与论文核心贡献最相关的一个方向。",
    },
    "overall_assessment": {
        "exact_repeat": "综合评估阶段的重复说明你已经获取了足够信息但可能在犹豫如何组织结论。直接输出你的判断。",
        "param_drift": "综合评估中的漂移可能是尝试获取额外信息来支撑结论。当前信息已足够，请基于已有证据做判断。",
        "oscillation": "综合评估中的振荡是你在「补充分析」和「输出结论」之间犹豫。现在就写出你的评估，即使不完美。",
    },
}


class DefaultExactRepeatHandler:
    """EXACT_REPEAT 的默认恢复策略（内置，phase-aware）。"""

    def handle(self, ctx: RecoveryContext) -> RecoveryAction:
        repeated_tool = ctx.repeated_tool
        repeated_params = ctx.repeated_params

        # Phase-aware 工具建议
        phase_tools = _PHASE_TOOL_PRIORITIES.get(ctx.current_phase, [])
        suggestions = self._build_suggestions(
            repeated_tool, ctx.available_tools, phase_tools
        )

        # Phase-aware 话术
        phase_hint = _PHASE_RECOVERY_HINTS.get(
            ctx.current_phase, {}
        ).get("exact_repeat", "")

        message = (
            f"[循环检测 — 精确重复] 你已连续 {ctx.window_size} 次以相同参数调用 "
            f"`{repeated_tool}`，结果没有变化。\n"
            f"重复的参数: {json.dumps(repeated_params, ensure_ascii=False)[:200]}\n\n"
            f"诊断：这条路径已证明无效。请换一种方法。\n"
        )
        if phase_hint:
            message += f"[{ctx.current_phase}] {phase_hint}\n"
        if suggestions:
            message += f"建议尝试: {', '.join(suggestions)}\n"
        message += (
            "如果你认为这个子任务无法完成，可以在 findings 中记录原因后继续下一步。"
        )

        return RecoveryAction(
            pattern=LoopPattern.EXACT_REPEAT,
            message=message,
            escalation_level=ctx.escalation_count,
            suggest_tools=suggestions,
        )

    @staticmethod
    def _build_suggestions(
        current_tool: str,
        available_tools: list[str],
        phase_preferred: list[str],
    ) -> list[str]:
        """结合 phase 优先级和可用工具构建建议列表。"""
        if not available_tools:
            return []
        alternatives = [t for t in available_tools if t != current_tool]
        # Phase 优先工具排前面
        sorted_alts = []
        for t in phase_preferred:
            if t in alternatives and t not in sorted_alts:
                sorted_alts.append(t)
        for t in alternatives:
            if t not in sorted_alts:
                sorted_alts.append(t)
        return sorted_alts[:3]


class DefaultParamDriftHandler:
    """PARAM_DRIFT 的默认恢复策略（内置，phase-aware）。"""

    def handle(self, ctx: RecoveryContext) -> RecoveryAction:
        tool_name = ctx.repeated_tool
        recent_params = [r.params for r in ctx.recent_calls]
        param_summary = json.dumps(recent_params[:3], ensure_ascii=False)[:300]

        # Phase-aware 工具建议
        phase_tools = _PHASE_TOOL_PRIORITIES.get(ctx.current_phase, [])
        suggestions = DefaultExactRepeatHandler._build_suggestions(
            tool_name, ctx.available_tools, phase_tools
        )

        # Phase-aware 话术
        phase_hint = _PHASE_RECOVERY_HINTS.get(
            ctx.current_phase, {}
        ).get("param_drift", "")

        message = (
            f"[循环检测 — 参数漂移] 你已连续 {ctx.window_size} 次调用 `{tool_name}`，"
            f"每次换不同参数但全部失败，结果相似。\n"
            f"尝试过的参数样例: {param_summary}\n\n"
            f"诊断：问题可能不在参数选择上，而是工具本身不适合当前任务。\n"
        )
        if phase_hint:
            message += f"[{ctx.current_phase}] {phase_hint}\n"
        if suggestions:
            message += f"建议换用: {', '.join(suggestions)}\n"
        message += "或者重新审视你的目标——也许需要从不同角度切入。"

        return RecoveryAction(
            pattern=LoopPattern.PARAM_DRIFT,
            message=message,
            escalation_level=ctx.escalation_count,
            suggest_tools=suggestions,
        )


class DefaultOscillationHandler:
    """OSCILLATION 的默认恢复策略（内置，phase-aware）。"""

    def handle(self, ctx: RecoveryContext) -> RecoveryAction:
        tools_involved = list(set(r.tool_name for r in ctx.recent_calls))
        if len(tools_involved) < 2:
            tools_involved = [ctx.repeated_tool, "unknown"]

        # Phase-aware 话术
        phase_hint = _PHASE_RECOVERY_HINTS.get(
            ctx.current_phase, {}
        ).get("oscillation", "")

        message = (
            f"[循环检测 — 振荡] 你在 `{tools_involved[0]}` 和 `{tools_involved[1]}` "
            f"之间交替调用，形成了死循环。\n\n"
            f"请暂停并进行 mini-reflection：\n"
            f"1. 你在 `{tools_involved[0]}` 中遇到了什么问题导致切换？\n"
            f"2. 你在 `{tools_involved[1]}` 中遇到了什么问题导致切回？\n"
            f"3. 这两个方向各自的障碍是什么？\n\n"
        )
        if phase_hint:
            message += f"[{ctx.current_phase}] {phase_hint}\n\n"
        message += (
            "做出决定：选择一个方向坚持推进，接受另一个方向的不完美。"
            "或者，如果两条路都不通，记录原因后跳过此子任务。"
        )

        return RecoveryAction(
            pattern=LoopPattern.OSCILLATION,
            message=message,
            escalation_level=ctx.escalation_count,
        )


# ==============================================================
# 核心检测器
# ==============================================================

class ToolCallPatternDetector:
    """工具调用循环模式检测器 (Phase 1 MVP + Complete)

    特性：
    - 滑动窗口检测（窗口大小可配）
    - 三种模式识别
    - 可插拔恢复策略注册表（Complete）
    - 上下文感知的恢复（Complete）
    - 恢复效果追踪（Complete）
    - 死亡螺旋升级机制
    """

    def __init__(
        self,
        window_size: int = 5,
        similarity_threshold: float = 0.85,
        max_escalation: int = 2,
    ):
        self.window_size = window_size
        self.similarity_threshold = similarity_threshold
        self.max_escalation = max_escalation

        self.call_history: list[ToolCallRecord] = []
        self._escalation_count: int = 0
        self._last_recovery_turn: int = -1
        self._recovery_effective: bool = True  # 上次恢复是否有效

        # Complete 层: 策略注册表
        self._registry = RecoveryRegistry()
        self._register_default_handlers()

        # Complete 层: 恢复效果追踪
        self._tracker = RecoveryTracker()

    # ----------------------------------------------------------
    # 公开接口
    # ----------------------------------------------------------

    @property
    def registry(self) -> RecoveryRegistry:
        """暴露策略注册表，允许外部注册自定义策略。"""
        return self._registry

    @property
    def tracker(self) -> RecoveryTracker:
        """暴露恢复追踪器，允许外部读取和导出追踪数据。"""
        return self._tracker

    def record_call(
        self,
        tool_name: str,
        params: dict,
        result: Any,
        success: bool = True,
        turn: int = 0,
    ) -> None:
        """记录一次工具调用。由 loop.py 在每次工具执行后调用。"""
        result_hash = self._hash_result(result)
        record = ToolCallRecord(
            tool_name=tool_name,
            params=params,
            success=success,
            result_hash=result_hash,
            turn=turn,
        )
        self.call_history.append(record)

    def detect(self) -> Optional[LoopPattern]:
        """检测当前是否存在循环模式。

        当 Kill Switch 关闭 (SCHOLAR_GODEL_LOOP_GUARD=0) 时，始终返回 None（不检测）。

        Returns:
            LoopPattern 或 None
        """
        if not LOOP_GUARD_ENABLED:
            return None

        if len(self.call_history) < self.window_size:
            return None

        recent = self.call_history[-self.window_size:]

        # 优先检测最严重的模式
        if self._check_exact_repeat(recent):
            return LoopPattern.EXACT_REPEAT

        if self._check_param_drift(recent):
            return LoopPattern.PARAM_DRIFT

        if self._check_oscillation(recent):
            return LoopPattern.OSCILLATION

        # 未检测到模式 → 恢复有效（如果之前在恢复中，且 window 已满足判断条件）
        if (not self._recovery_effective
                and self._last_recovery_turn >= 0
                and len(self.call_history) >= self.window_size):
            self._recovery_effective = True
            # Complete: 确认恢复成功
            current_turn = self.call_history[-1].turn if self.call_history else 0
            self._tracker.confirm_success(current_turn)

        return None

    def get_recovery_action(
        self,
        pattern: LoopPattern,
        current_turn: int,
        current_phase: str = "",
        available_tools: list[str] | None = None,
    ) -> RecoveryAction:
        """根据模式类型和上下文生成恢复动作。

        使用策略注册表查找最匹配的 handler，支持 phase-aware 恢复。

        Args:
            pattern: 检测到的循环模式
            current_turn: 当前轮次
            current_phase: 当前 Phase（用于上下文感知恢复）
            available_tools: 可用工具列表（用于建议替代）

        Returns:
            RecoveryAction 描述如何干预
        """
        # 检查是否需要升级（恢复后仍在循环 → 计数递增）
        if self._last_recovery_turn >= 0 and not self._recovery_effective:
            # 此前已尝试恢复但未成功脱离 → 升级
            self._escalation_count += 1
            # Complete: 追踪升级
            self._tracker.confirm_escalation()
        elif self._recovery_effective:
            # 此前恢复后曾成功脱离循环，现在又陷入 → 重新计数
            self._escalation_count = 0
        # else: 首次恢复，保持 escalation_count = 0

        self._last_recovery_turn = current_turn
        self._recovery_effective = False  # 等待下次 detect() 确认

        # 超过最大升级次数 → 强制终止信号
        if self._escalation_count >= self.max_escalation:
            action = RecoveryAction(
                pattern=pattern,
                message=(
                    "[死亡螺旋] 已连续多次尝试恢复但均未脱离循环。"
                    "当前子任务标记为 incomplete，跳过继续。"
                    "请用 mark_complete 结束当前任务，在 findings 中注明未完成的部分。"
                ),
                escalation_level=self._escalation_count,
            )
            # Complete: 追踪终止
            self._tracker.record_recovery_attempt(
                pattern=pattern,
                phase=current_phase,
                turn=current_turn,
                escalation_level=self._escalation_count,
                recovery_message=action.message,
                suggested_tools=[],
                handler_name="__termination__",
            )
            self._tracker.confirm_termination()
            return action

        # Complete: 构建恢复上下文
        recent = self.call_history[-self.window_size:] if self.call_history else []
        ctx = RecoveryContext(
            pattern=pattern,
            current_phase=current_phase,
            current_turn=current_turn,
            escalation_count=self._escalation_count,
            repeated_tool=self.call_history[-1].tool_name if self.call_history else "",
            repeated_params=self.call_history[-1].params if self.call_history else {},
            recent_calls=recent,
            available_tools=available_tools or [],
            window_size=self.window_size,
        )

        # Complete: 从注册表查找策略 handler
        handler = self._registry.get_handler(pattern, current_phase)
        handler_name = ""
        if handler is not None:
            action = handler.handle(ctx)
            handler_name = type(handler).__name__
        else:
            # Fallback: 通用消息
            action = RecoveryAction(
                pattern=pattern,
                message="[循环检测] 检测到重复行为模式，请尝试不同的方法。",
                escalation_level=self._escalation_count,
            )
            handler_name = "__fallback__"

        # Complete: 追踪恢复尝试
        self._tracker.record_recovery_attempt(
            pattern=pattern,
            phase=current_phase,
            turn=current_turn,
            escalation_level=self._escalation_count,
            recovery_message=action.message,
            suggested_tools=action.suggest_tools,
            handler_name=handler_name,
        )

        return action

    def reset(self) -> None:
        """重置检测器状态（如 phase 切换时）。"""
        # 刷新未确认的追踪记录
        self._tracker.flush_pending()
        self.call_history.clear()
        self._escalation_count = 0
        self._last_recovery_turn = -1
        self._recovery_effective = True

    @property
    def is_in_recovery(self) -> bool:
        """当前是否处于恢复尝试中。"""
        return not self._recovery_effective and self._last_recovery_turn >= 0

    # ----------------------------------------------------------
    # 模式检测实现
    # ----------------------------------------------------------

    def _check_exact_repeat(self, recent: list[ToolCallRecord]) -> bool:
        """检测完全相同的工具调用重复。

        条件：最近 window_size 次调用的签名完全一致。
        """
        signatures = [r.signature() for r in recent]
        # 所有签名相同
        if len(set(signatures)) == 1:
            return True
        # 或者最近 3 次相同（更短的重复也算）
        if len(recent) >= 3 and len(set(signatures[-3:])) == 1:
            return True
        return False

    def _check_param_drift(self, recent: list[ToolCallRecord]) -> bool:
        """检测同一工具、参数微变但结果均失败。

        条件：
        - 最近 N 次都是同一个工具
        - 全部失败
        - 结果哈希都相同（说明换参数没有效果）
        """
        tool_names = [r.tool_name for r in recent]
        if len(set(tool_names)) != 1:
            return False

        # 全部失败
        if not all(not r.success for r in recent):
            return False

        # 结果都相同（虽然参数不同）
        result_hashes = [r.result_hash for r in recent]
        unique_results = len(set(result_hashes))
        # 允许少量变化（相似度阈值）
        similarity = 1.0 - (unique_results - 1) / len(recent)
        return similarity >= self.similarity_threshold

    def _check_oscillation(self, recent: list[ToolCallRecord]) -> bool:
        """检测 A→B→A→B 交替模式。

        条件：工具签名形成长度为 2 的周期。
        """
        if len(recent) < 4:
            return False

        tool_sigs = [r.tool_signature() for r in recent]

        # 检测周期为 2 的重复
        if len(set(tool_sigs)) == 2:
            # 验证是否真的交替
            for i in range(len(tool_sigs) - 2):
                if tool_sigs[i] != tool_sigs[i + 2]:
                    return False
            return True

        return False

    # ----------------------------------------------------------
    # 内部辅助
    # ----------------------------------------------------------

    def _register_default_handlers(self) -> None:
        """注册内置的默认恢复策略（通配所有 phase）。"""
        self._registry.register(LoopPattern.EXACT_REPEAT, DefaultExactRepeatHandler())
        self._registry.register(LoopPattern.PARAM_DRIFT, DefaultParamDriftHandler())
        self._registry.register(LoopPattern.OSCILLATION, DefaultOscillationHandler())

    @staticmethod
    def _hash_result(result: Any) -> str:
        """对工具调用结果计算哈希（用于快速比较）。"""
        if result is None:
            return "none"
        text = str(result)[:2000]  # 截断，避免对超长结果哈希
        return hashlib.md5(text.encode()).hexdigest()[:12]
