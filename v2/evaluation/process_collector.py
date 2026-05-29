"""
evaluation/process_collector.py — 从 session 运行数据中收集过程质量指标。

数据来源:
    1. .workspace/metrics/session_summary.jsonl  — loop_turns, tokens, coverage
    2. Agent 运行时 state 对象                  — tool calls, phase transitions
    3. loop_guard 统计                          — doom loop 事件

本模块提供两种收集方式:
    - from_session_state(): 从内存中的 state 对象直接收集（实时评估）
    - from_metrics_file(): 从 JSONL 文件回放收集（离线评估/回归测试）
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from evaluation.quality_metrics import ProcessMetrics

logger = logging.getLogger(__name__)


# ============================================================
# From live session state (real-time collection)
# ============================================================


def collect_from_state(
    state: Any,
    loop_guard_stats: dict[str, Any] | None = None,
    tool_call_stats: dict[str, Any] | None = None,
) -> ProcessMetrics:
    """从 Agent 运行结束后的 state 对象收集过程指标。

    Args:
        state: WorkspaceState 实例（core/state.py）
        loop_guard_stats: LoopGuard 产生的统计数据
            {
                "doom_loop_triggered": bool,
                "doom_loop_count": int,
                "recovery_attempts": int,
                "recovery_successes": int,
            }
        tool_call_stats: 工具调用统计
            {
                "total_calls": int,
                "successful_calls": int,
                "phase_transitions": int,
                "phase_regressions": int,
            }

    Returns:
        ProcessMetrics 实例
    """
    lg = loop_guard_stats or {}
    tc = tool_call_stats or {}

    # 从 state 提取基础数据
    findings_count = len(getattr(state, "findings", []))
    loop_turns = getattr(state, "loop_turns", 0)
    total_tokens = getattr(state, "total_tokens", 0)
    sections_read = len(getattr(state, "sections_read", []))
    total_sections = len(getattr(state, "paper_sections", {}) or {})

    # 计算衍生指标
    findings_per_turn = findings_count / loop_turns if loop_turns > 0 else 0.0
    findings_per_1k = (
        findings_count / (total_tokens / 1000) if total_tokens > 0 else 0.0
    )
    read_coverage = sections_read / total_sections if total_sections > 0 else 0.0

    # PCG 覆盖率
    pcg = getattr(state, "paper_cognition_graph", None)
    pcg_coverage = 0.0
    if pcg and hasattr(pcg, "coverage_ratio"):
        try:
            pcg_coverage = pcg.coverage_ratio()
        except Exception:
            pass

    # 工具成功率
    tool_total = tc.get("total_calls", 0)
    tool_success = tc.get("successful_calls", 0)
    tool_success_rate = tool_success / tool_total if tool_total > 0 else 1.0

    # Doom loop 恢复率
    doom_count = lg.get("doom_loop_count", 0)
    recovery_attempts = lg.get("recovery_attempts", 0)
    recovery_successes = lg.get("recovery_successes", 0)
    recovery_rate = (
        recovery_successes / recovery_attempts if recovery_attempts > 0 else 0.0
    )

    # 反思系统
    reflection_stats = getattr(state, "reflection_stats", {}) or {}

    return ProcessMetrics(
        loop_turns=loop_turns,
        total_tokens=total_tokens,
        findings_per_turn=findings_per_turn,
        findings_per_1k_tokens=findings_per_1k,
        doom_loop_triggered=lg.get("doom_loop_triggered", False),
        doom_loop_count=doom_count,
        recovery_success_rate=recovery_rate,
        phase_transitions=tc.get("phase_transitions", 0),
        phase_regressions=tc.get("phase_regressions", 0),
        tool_calls_total=tool_total,
        tool_calls_success=tool_success,
        tool_success_rate=tool_success_rate,
        sections_read=sections_read,
        total_sections=total_sections,
        read_coverage=read_coverage,
        pcg_coverage=pcg_coverage,
        emergency_reflect_triggered=reflection_stats.get(
            "emergency_triggered", False
        ),
        fast_reflect_alerts=reflection_stats.get("fast_reflect_alerts", 0),
        deep_reflect_ran=reflection_stats.get("deep_reflect_ran", False),
    )


# ============================================================
# From JSONL metrics file (offline / retrospective collection)
# ============================================================


def collect_from_metrics_file(
    metrics_dir: Path,
    session_id: str,
) -> ProcessMetrics:
    """从 .workspace/metrics/session_summary.jsonl 中回放收集过程指标。

    Args:
        metrics_dir: metrics 目录路径
        session_id: 要检索的 session ID

    Returns:
        ProcessMetrics 实例（若未找到则返回空 ProcessMetrics）
    """
    summary_file = metrics_dir / "session_summary.jsonl"
    if not summary_file.exists():
        logger.warning("Session summary file not found: %s", summary_file)
        return ProcessMetrics()

    # 找到对应 session 的最新记录
    target_record: dict[str, Any] | None = None
    try:
        with summary_file.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("session_id") == session_id:
                    target_record = record
    except OSError as e:
        logger.warning("Failed to read metrics file: %s", e)
        return ProcessMetrics()

    if target_record is None:
        logger.info("No session_summary record found for session_id=%s", session_id)
        return ProcessMetrics()

    payload = target_record.get("payload", {})

    loop_turns = payload.get("loop_turns", 0)
    total_tokens = payload.get("total_tokens", 0)
    sections_read = payload.get("sections_read", 0)
    total_sections = payload.get("total_sections", 0)

    return ProcessMetrics(
        loop_turns=loop_turns,
        total_tokens=total_tokens,
        findings_per_turn=payload.get("findings_per_turn", 0.0),
        findings_per_1k_tokens=payload.get("findings_per_1k_tokens", 0.0),
        doom_loop_triggered=payload.get("emergency_triggered", False),
        doom_loop_count=0,  # Not available in current session_summary format
        recovery_success_rate=0.0,
        phase_transitions=0,  # Not available in current format
        phase_regressions=0,
        tool_calls_total=0,  # Not available in current format
        tool_calls_success=0,
        tool_success_rate=0.0,
        sections_read=sections_read,
        total_sections=total_sections,
        read_coverage=payload.get("read_ratio", 0.0),
        pcg_coverage=payload.get("pcg_coverage", 0.0),
        emergency_reflect_triggered=payload.get("emergency_triggered", False),
        fast_reflect_alerts=payload.get("fast_reflect_alerts", 0),
        deep_reflect_ran=payload.get("deep_reflect_ran", False),
    )


# ============================================================
# Batch collection from all sessions in a metrics dir
# ============================================================


def collect_all_sessions(metrics_dir: Path) -> dict[str, ProcessMetrics]:
    """从 metrics 目录中读取所有 session 的过程指标。

    Returns:
        {session_id: ProcessMetrics} 字典
    """
    summary_file = metrics_dir / "session_summary.jsonl"
    if not summary_file.exists():
        return {}

    results: dict[str, ProcessMetrics] = {}
    try:
        with summary_file.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                sid = record.get("session_id")
                if sid:
                    # 保留最新记录（后覆盖前）
                    results[sid] = collect_from_metrics_file(metrics_dir, sid)
    except OSError as e:
        logger.warning("Failed to read metrics file: %s", e)

    return results
