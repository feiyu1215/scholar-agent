"""
core/skills/executor.py — Skill 执行器与编排

将 Selector + Loader + ToolGroup 整合为统一的执行入口：
  1. SkillExecutor: 单个 Skill 的执行封装（含计时、异常处理、事件发布）
  2. SkillOrchestrator: 多 Skill 协调执行（串联/并联/条件组合）

与 EventBus 集成：执行前后发布事件，供 Meta-Harness 追踪。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from core.skills.base import (
    Finding,
    Skill,
    SkillContext,
    SkillLevel,
    SkillResult,
)

logger = logging.getLogger(__name__)


# ==============================================================
# 执行记录
# ==============================================================

@dataclass
class SkillExecutionRecord:
    """单次 Skill 执行的完整记录（供性能追踪）。

    Attributes:
        skill_name: 执行的 Skill 名称
        skill_level: Skill 层次
        success: 是否成功
        execution_time_ms: 耗时（毫秒）
        findings_count: 产出的 findings 数量
        tokens_used: token 消耗
        error: 错误信息（失败时）
        timestamp: 执行时间戳
    """
    skill_name: str
    skill_level: str
    success: bool
    execution_time_ms: float
    findings_count: int = 0
    tokens_used: int = 0
    error: str = ""
    timestamp: float = field(default_factory=time.time)


# ==============================================================
# Skill 执行器
# ==============================================================

class SkillExecutor:
    """单个 Skill 的执行封装。

    职责：
    - 执行前验证（validate_context）
    - 计时和异常处理
    - 执行后记录
    - 事件发布（当 EventBus 可用时）

    Usage:
        executor = SkillExecutor()
        result = executor.run(skill, context)
        records = executor.get_history()
    """

    def __init__(self, event_bus=None):
        """
        Args:
            event_bus: EventBus 实例（可选，用于发布执行事件）
        """
        self._event_bus = event_bus
        self._history: list[SkillExecutionRecord] = []

    def run(self, skill: Skill, context: SkillContext) -> SkillResult:
        """执行单个 Skill（带完整的生命周期管理）。

        Args:
            skill: 要执行的 Skill 实例
            context: 执行上下文

        Returns:
            SkillResult（失败时 success=False + error_message）
        """
        skill_name = skill.descriptor.name

        # 1. 执行前验证
        is_valid, error_msg = skill.validate_context(context)
        if not is_valid:
            result = SkillResult(
                success=False,
                error_message=f"Context validation failed: {error_msg}",
            )
            self._record(skill, result, 0.0, error_msg)
            return result

        # 2. 发布开始事件
        self._emit_start(skill_name)

        # 3. 执行（带计时和异常处理）
        start_time = time.time()
        try:
            result = skill.execute(context)
        except Exception as exc:
            elapsed_ms = (time.time() - start_time) * 1000
            error = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "[SkillExecutor] Skill '%s' failed: %s", skill_name, error
            )
            result = SkillResult(
                success=False,
                error_message=error,
                execution_time_ms=elapsed_ms,
            )
            self._record(skill, result, elapsed_ms, error)
            self._emit_end(skill_name, success=False, error=error)
            return result

        elapsed_ms = (time.time() - start_time) * 1000
        result.execution_time_ms = elapsed_ms

        # 4. 记录成功执行
        self._record(skill, result, elapsed_ms)
        self._emit_end(skill_name, success=True)

        return result

    def run_batch(
        self,
        skills: list[Skill],
        context: SkillContext,
        stop_on_failure: bool = False,
    ) -> list[SkillResult]:
        """批量串行执行多个 Skill。

        Args:
            skills: Skill 列表（按顺序执行）
            context: 共享的执行上下文
            stop_on_failure: 遇到失败是否终止后续

        Returns:
            结果列表（与输入 skills 一一对应）
        """
        results: list[SkillResult] = []
        for skill in skills:
            result = self.run(skill, context)
            results.append(result)

            # 将前一个 Skill 的 output_data 传递给后续 Skill
            if result.success and result.output_data:
                context.parameters.update(result.output_data)

            # 将 Findings 累积到 context
            if result.findings:
                context.existing_findings.extend(result.findings)

            if not result.success and stop_on_failure:
                break

        return results

    @property
    def history(self) -> list[SkillExecutionRecord]:
        """执行历史记录。"""
        return list(self._history)

    def get_stats(self) -> dict:
        """获取执行统计。"""
        if not self._history:
            return {"total": 0, "success": 0, "failure": 0}

        success_count = sum(1 for r in self._history if r.success)
        total_time = sum(r.execution_time_ms for r in self._history)
        total_findings = sum(r.findings_count for r in self._history)

        return {
            "total": len(self._history),
            "success": success_count,
            "failure": len(self._history) - success_count,
            "total_time_ms": total_time,
            "total_findings": total_findings,
            "avg_time_ms": total_time / len(self._history),
        }

    def clear_history(self) -> None:
        """清除历史记录。"""
        self._history.clear()

    # --- 内部方法 ---

    def _record(
        self, skill: Skill, result: SkillResult, elapsed_ms: float, error: str = ""
    ) -> None:
        """记录执行结果。"""
        record = SkillExecutionRecord(
            skill_name=skill.descriptor.name,
            skill_level=skill.descriptor.level.value,
            success=result.success,
            execution_time_ms=elapsed_ms,
            findings_count=len(result.findings),
            tokens_used=result.tokens_used,
            error=error,
        )
        self._history.append(record)

    def _emit_start(self, skill_name: str) -> None:
        """发布 Skill 开始事件。"""
        if self._event_bus is None:
            return
        try:
            from core.event_bus import EventType
            self._event_bus.emit(
                EventType.TOOL_CALL_STARTED,
                source="skill_executor",
                skill_name=skill_name,
            )
        except Exception:
            pass  # EventBus 不可用不影响执行

    def _emit_end(self, skill_name: str, success: bool, error: str = "") -> None:
        """发布 Skill 结束事件。"""
        if self._event_bus is None:
            return
        try:
            from core.event_bus import EventType
            event_type = EventType.TOOL_CALL_COMPLETED if success else EventType.TOOL_CALL_FAILED
            self._event_bus.emit(
                event_type,
                source="skill_executor",
                skill_name=skill_name,
                error=error,
            )
        except Exception:
            pass
