"""
core/metrics_export.py — 结构化 Metrics Export (Phase C1)

设计目标:
    - 将 Evolution confidence 变化、IntraContrast delta、DeepReflector decisions
      统一输出到 `.workspace/metrics/` 目录
    - JSON Lines 格式，每条带 timestamp + session_id
    - 人可以肉眼看到"系统在变好还是变差"

文件结构:
    .workspace/metrics/
    ├── evolution.jsonl       # LearnedHabit confidence 变化
    ├── contrast.jsonl        # IntraSession A/B delta
    ├── deep_reflect.jsonl    # DeepReflector boost/reduce/retire 决策
    └── session_summary.jsonl # 每次 session 的综合 metrics

格式约定:
    每条 JSON Line 包含:
    - timestamp: ISO 8601 格式
    - session_id: 当次会话 ID (paper_id 或随机 UUID)
    - event_type: 事件类型标识
    - payload: 事件具体数据
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default metrics directory (relative to working directory)
DEFAULT_METRICS_DIR = Path(".workspace/metrics")


def _ensure_metrics_dir(metrics_dir: Path | None = None) -> Path:
    """确保 metrics 目录存在并返回路径。"""
    d = metrics_dir or DEFAULT_METRICS_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _make_record(
    session_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """构建一条标准 metrics 记录。"""
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "event_type": event_type,
        "payload": payload,
    }


def _append_jsonl(filepath: Path, record: dict[str, Any]) -> None:
    """追加一条 JSON record 到文件（append 模式，非并发安全）。"""
    try:
        with filepath.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except OSError as e:
        logger.warning("Metrics write failed (%s): %s", filepath, e)


# ============================================================
# Public API: Export Functions
# ============================================================


def export_evolution_metrics(
    session_id: str,
    habits_generated: int = 0,
    habits_injected: int = 0,
    total_learned_habits: int = 0,
    habit_details: list[dict[str, Any]] | None = None,
    metrics_dir: Path | None = None,
) -> None:
    """
    导出 Evolution (习惯学习) 指标。

    Args:
        session_id: 当次会话标识
        habits_generated: 本次新生成的习惯数
        habits_injected: 本次注入到 context 的习惯数
        total_learned_habits: 当前习惯库总数
        habit_details: 各习惯的 confidence 快照
            [{"habit_id": str, "confidence": float, "generation": int}, ...]
        metrics_dir: 输出目录（默认 .workspace/metrics/）
    """
    d = _ensure_metrics_dir(metrics_dir)
    record = _make_record(
        session_id=session_id,
        event_type="evolution_snapshot",
        payload={
            "habits_generated": habits_generated,
            "habits_injected": habits_injected,
            "total_learned_habits": total_learned_habits,
            "habit_details": habit_details or [],
        },
    )
    _append_jsonl(d / "evolution.jsonl", record)


def export_contrast_metrics(
    session_id: str,
    target_habit_id: str,
    phase_a_density: float,
    phase_b_density: float,
    delta: float,
    recommendation: str,
    statistical_note: str = "",
    metrics_dir: Path | None = None,
) -> None:
    """
    导出 IntraSession Contrast (A/B 对比实验) 指标。

    Args:
        session_id: 当次会话标识
        target_habit_id: 被测试的习惯 ID
        phase_a_density: Phase A findings 密度 (all habits)
        phase_b_density: Phase B findings 密度 (target removed)
        delta: A - B (正值 = 该习惯有益)
        recommendation: "reinforce" | "doubt" | "insufficient_data"
        statistical_note: 样本量备注
        metrics_dir: 输出目录
    """
    d = _ensure_metrics_dir(metrics_dir)
    record = _make_record(
        session_id=session_id,
        event_type="intra_contrast_result",
        payload={
            "target_habit_id": target_habit_id,
            "phase_a_density": phase_a_density,
            "phase_b_density": phase_b_density,
            "delta": delta,
            "recommendation": recommendation,
            "statistical_note": statistical_note,
        },
    )
    _append_jsonl(d / "contrast.jsonl", record)


def export_deep_reflect_metrics(
    session_id: str,
    habit_decisions: list[dict[str, Any]] | None = None,
    maturity_updates: list[dict[str, Any]] | None = None,
    config_decisions: list[dict[str, Any]] | None = None,
    token_efficiency: str = "stable",
    meta_note: str = "",
    metrics_dir: Path | None = None,
) -> None:
    """
    导出 DeepReflector 决策指标。

    Args:
        session_id: 当次会话标识
        habit_decisions: [{habit_id, action, confidence_delta, reasoning}, ...]
        maturity_updates: [{paper_type, new_maturity, reasoning}, ...]
        config_decisions: [{param, direction, evidence_count}, ...]
        token_efficiency: "improving" | "stable" | "declining"
        meta_note: 给下一次反思的备忘
        metrics_dir: 输出目录
    """
    d = _ensure_metrics_dir(metrics_dir)
    record = _make_record(
        session_id=session_id,
        event_type="deep_reflect_decisions",
        payload={
            "habit_decisions": habit_decisions or [],
            "maturity_updates": maturity_updates or [],
            "config_decisions": config_decisions or [],
            "token_efficiency": token_efficiency,
            "meta_note": meta_note,
        },
    )
    _append_jsonl(d / "deep_reflect.jsonl", record)


def export_session_summary(
    session_id: str,
    paper_id: str | None = None,
    paper_type: str = "",
    findings_count: int = 0,
    loop_turns: int = 0,
    total_tokens: int = 0,
    sections_read: int = 0,
    total_sections: int = 0,
    pcg_coverage: float = 0.0,
    emergency_triggered: bool = False,
    fast_reflect_alerts: int = 0,
    deep_reflect_ran: bool = False,
    v3_features_enabled: list[str] | None = None,
    metrics_dir: Path | None = None,
) -> None:
    """
    导出 session 级别综合摘要。

    此摘要让人可以"一行看出这次 session 发生了什么"。

    Args:
        session_id: 当次会话标识
        paper_id: 论文 ID
        paper_type: 论文类型描述
        findings_count: 发现总数
        loop_turns: 循环轮次
        total_tokens: 消耗 token 总数
        sections_read: 已读 section 数
        total_sections: 论文总 section 数
        pcg_coverage: PCG 覆盖率
        emergency_triggered: 紧急反思是否触发
        fast_reflect_alerts: 快速反思 alert 数
        deep_reflect_ran: 深度反思是否执行
        v3_features_enabled: 当前启用的 V3 feature 列表
        metrics_dir: 输出目录
    """
    d = _ensure_metrics_dir(metrics_dir)

    # 计算 efficiency 指标
    findings_per_turn = findings_count / loop_turns if loop_turns > 0 else 0.0
    findings_per_1k_tokens = (
        findings_count / (total_tokens / 1000) if total_tokens > 0 else 0.0
    )
    read_ratio = sections_read / total_sections if total_sections > 0 else 0.0

    record = _make_record(
        session_id=session_id,
        event_type="session_summary",
        payload={
            "paper_id": paper_id,
            "paper_type": paper_type,
            "findings_count": findings_count,
            "loop_turns": loop_turns,
            "total_tokens": total_tokens,
            "sections_read": sections_read,
            "total_sections": total_sections,
            "read_ratio": round(read_ratio, 3),
            "pcg_coverage": round(pcg_coverage, 3),
            "findings_per_turn": round(findings_per_turn, 3),
            "findings_per_1k_tokens": round(findings_per_1k_tokens, 3),
            "emergency_triggered": emergency_triggered,
            "fast_reflect_alerts": fast_reflect_alerts,
            "deep_reflect_ran": deep_reflect_ran,
            "v3_features_enabled": v3_features_enabled or [],
        },
    )
    _append_jsonl(d / "session_summary.jsonl", record)


# ============================================================
# Convenience: Collect-all-and-export
# ============================================================


def export_all_session_metrics(
    session_id: str | None = None,
    state: Any = None,
    memory: Any = None,
    reflection_stats: dict[str, Any] | None = None,
    contrast_result: dict[str, Any] | None = None,
    deep_reflect_result: dict[str, Any] | None = None,
    paper_id: str | None = None,
    metrics_dir: Path | None = None,
) -> str:
    """
    一站式导出：从 session state 和 reflection stats 中收集所有 metrics 并写入。

    在 end_session_with_reflection 的末尾调用此函数即可完成所有 metrics 导出。

    Returns:
        session_id 字符串（供日志打印）
    """
    from core.godel_config import (
        GODEL_PCG_ENABLED,
        GODEL_BUDGET_MANAGER_ENABLED,
        GODEL_SIGNAL_DISPATCHER_ENABLED,
        GODEL_EVIDENCE_CHAIN_ENABLED,
        GODEL_SECTION_EXPERIENCE_ENABLED,
        GODEL_INTRA_CONTRAST_ENABLED,
        GODEL_FAST_REFLECT_ENABLED,
        GODEL_DEEP_REFLECT_ENABLED,
        GODEL_EMERGENCY_REFLECT_ENABLED,
    )

    sid = session_id or (paper_id if paper_id else str(uuid.uuid4())[:8])
    stats = reflection_stats or {}

    # 1. Evolution snapshot
    if memory is not None:
        try:
            learned_habits = getattr(memory, "learned_habits", None)
            if learned_habits and callable(getattr(learned_habits, "__iter__", None)):
                habit_details = []
                for h in learned_habits:
                    habit_details.append({
                        "habit_id": getattr(h, "habit_id", str(h)[:20]),
                        "confidence": getattr(h, "confidence", 0.0),
                        "generation": getattr(h, "generation", 0),
                    })
                export_evolution_metrics(
                    session_id=sid,
                    habits_generated=stats.get("habits_generated", 0),
                    habits_injected=stats.get("habits_injected", 0),
                    total_learned_habits=len(habit_details),
                    habit_details=habit_details,
                    metrics_dir=metrics_dir,
                )
            else:
                export_evolution_metrics(
                    session_id=sid,
                    metrics_dir=metrics_dir,
                )
        except Exception as e:
            logger.warning("Evolution metrics export failed (non-fatal): %s", e)

    # 2. Contrast result
    if contrast_result:
        try:
            export_contrast_metrics(
                session_id=sid,
                target_habit_id=contrast_result.get("target_habit_id", ""),
                phase_a_density=contrast_result.get("phase_a_findings_density", 0.0),
                phase_b_density=contrast_result.get("phase_b_findings_density", 0.0),
                delta=contrast_result.get("delta", 0.0),
                recommendation=contrast_result.get("recommendation", "unknown"),
                statistical_note=contrast_result.get("statistical_note", ""),
                metrics_dir=metrics_dir,
            )
        except Exception as e:
            logger.warning("Contrast metrics export failed (non-fatal): %s", e)

    # 3. Deep reflect decisions
    if deep_reflect_result:
        try:
            export_deep_reflect_metrics(
                session_id=sid,
                habit_decisions=deep_reflect_result.get("habit_decisions", []),
                maturity_updates=deep_reflect_result.get("maturity_updates", []),
                config_decisions=deep_reflect_result.get("config_decisions", []),
                token_efficiency=deep_reflect_result.get(
                    "token_efficiency_assessment", "stable"
                ),
                meta_note=deep_reflect_result.get("meta_note", ""),
                metrics_dir=metrics_dir,
            )
        except Exception as e:
            logger.warning("DeepReflect metrics export failed (non-fatal): %s", e)

    # 4. Session summary
    if state is not None:
        try:
            v3_enabled = []
            if GODEL_PCG_ENABLED:
                v3_enabled.append("pcg")
            if GODEL_BUDGET_MANAGER_ENABLED:
                v3_enabled.append("budget")
            if GODEL_SIGNAL_DISPATCHER_ENABLED:
                v3_enabled.append("dispatcher")
            if GODEL_EVIDENCE_CHAIN_ENABLED:
                v3_enabled.append("evidence_chain")
            if GODEL_SECTION_EXPERIENCE_ENABLED:
                v3_enabled.append("section_exp")
            if GODEL_INTRA_CONTRAST_ENABLED:
                v3_enabled.append("intra_contrast")
            if GODEL_FAST_REFLECT_ENABLED:
                v3_enabled.append("fast_reflect")
            if GODEL_DEEP_REFLECT_ENABLED:
                v3_enabled.append("deep_reflect")
            if GODEL_EMERGENCY_REFLECT_ENABLED:
                v3_enabled.append("emergency_reflect")

            pcg = getattr(state, "paper_cognition_graph", None)
            pcg_coverage = 0.0
            if pcg and hasattr(pcg, "coverage_ratio"):
                pcg_coverage = pcg.coverage_ratio()

            export_session_summary(
                session_id=sid,
                paper_id=paper_id,
                paper_type=getattr(
                    getattr(state, "cognitive_hints", None),
                    "paper_type_description",
                    "",
                ) or "",
                findings_count=len(getattr(state, "findings", [])),
                loop_turns=getattr(state, "loop_turns", 0),
                total_tokens=getattr(state, "total_tokens", 0),
                sections_read=len(getattr(state, "sections_read", [])),
                total_sections=len(getattr(state, "paper_sections", {}) or {}),
                pcg_coverage=pcg_coverage,
                emergency_triggered=stats.get("emergency_triggered", False),
                fast_reflect_alerts=stats.get("fast_reflect_alerts", 0),
                deep_reflect_ran="deep_reflect_report" in stats,
                v3_features_enabled=v3_enabled,
                metrics_dir=metrics_dir,
            )
        except Exception as e:
            logger.warning("Session summary metrics export failed (non-fatal): %s", e)

    logger.info("Metrics exported for session: %s", sid)
    return sid
