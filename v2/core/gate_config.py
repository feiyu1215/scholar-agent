"""
core/v2/gate_config.py — B4: Completion Gate 动态配置

认知循环的"节奏感"不应是硬编码常量。Agent 审理论论文需要更多轮次，
审临床 RCT 可能更快收敛。B4 让这些参数动态化。

三层参数来源（优先级递减）:
    1. Agent 自主生成的 CognitiveHints (S1) — gate_idle_rounds / min_findings_for_exit
    2. 跨会话经验积累 — 过去审同类论文的实际统计
    3. 系统默认值 — 兜底常量

设计原则:
    - C5（约束-而非-控制）: 配置影响"信号的触发时机"，不影响 Agent 的决策权
    - C6（先跑通再优化）: 短期用 S1 静态覆盖即可，长期准备统计学习
    - 渐进退化: 没有 S1 结果、没有历史数据时，退回系统默认值
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.paper_type_hints import CognitiveHints
    from core.memory import MemoryStore


# ============================================================
# 默认值常量
# ============================================================

# 停滞检测：连续多少轮无新 finding 触发信号
DEFAULT_IDLE_ROUNDS = 5

# 自评时刻触发轮次
DEFAULT_SELF_EVAL_FIRST = 15
DEFAULT_SELF_EVAL_SECOND = 25
DEFAULT_SELF_EVAL_FINAL = 40

# 最少 findings 数才允许无阻碍退出（0 = 不设下限）
DEFAULT_MIN_FINDINGS_FOR_EXIT = 0


# ============================================================
# CompletionGateConfig
# ============================================================

@dataclass
class CompletionGateConfig:
    """
    Completion Gate 的动态参数集。

    Harness 在审稿开始时构建一次，中途如果 Agent 调用
    generate_cognitive_hints 可以更新。
    """

    # 停滞检测
    idle_rounds: int = DEFAULT_IDLE_ROUNDS

    # 自评时刻（三个梯度）
    self_eval_first: int = DEFAULT_SELF_EVAL_FIRST
    self_eval_second: int = DEFAULT_SELF_EVAL_SECOND
    self_eval_final: int = DEFAULT_SELF_EVAL_FINAL

    # 退出下限
    min_findings_for_exit: int = DEFAULT_MIN_FINDINGS_FOR_EXIT

    # 来源标记（用于调试/日志）
    source: str = "default"  # "default" | "cognitive_hints" | "experience" | "mixed"

    def describe(self) -> str:
        """人可读的参数描述（供调试）。"""
        return (
            f"GateConfig[{self.source}]: "
            f"idle={self.idle_rounds}, "
            f"eval@{self.self_eval_first}/{self.self_eval_second}/{self.self_eval_final}, "
            f"min_findings={self.min_findings_for_exit}"
        )


# ============================================================
# 构建函数: 从三层来源计算最终配置
# ============================================================

def compute_gate_config(
    cognitive_hints: "CognitiveHints | None" = None,
    memory_store: "MemoryStore | None" = None,
    paper_type: str = "",
) -> CompletionGateConfig:
    """
    根据当前可用的信息源计算 Completion Gate 配置。

    优先级:
        1. cognitive_hints（Agent 自主判断，最精确）
        2. 跨会话经验（同类论文的历史统计）
        3. 系统默认值（兜底）

    Args:
        cognitive_hints: S1 生成的认知提示（可为 None）
        memory_store: 跨会话记忆（可为 None）
        paper_type: 论文类型标识（用于查询历史经验）

    Returns:
        CompletionGateConfig 实例
    """
    config = CompletionGateConfig()
    source_parts = []

    # --- Layer 1: CognitiveHints（Agent 自主生成，最高优先级）---
    if cognitive_hints and not cognitive_hints.is_empty():
        if cognitive_hints.gate_idle_rounds is not None:
            config.idle_rounds = _clamp(cognitive_hints.gate_idle_rounds, 3, 10)
            source_parts.append("hints")

        if cognitive_hints.min_findings_for_exit is not None:
            config.min_findings_for_exit = _clamp(cognitive_hints.min_findings_for_exit, 0, 10)
            source_parts.append("hints")

    # --- Layer 2: 跨会话经验（同类论文历史统计）---
    if memory_store and paper_type:
        experience = _query_experience(memory_store, paper_type)
        if experience:
            # 只在 Layer 1 没有覆盖时使用经验值
            if not source_parts:
                config.idle_rounds = experience["avg_idle_rounds"]
                source_parts.append("experience")

            # 自评时刻根据经验调整（如果历史平均轮次偏长/偏短）
            avg_turns = experience.get("avg_total_turns", 0)
            if avg_turns > 0:
                config.self_eval_first = _clamp(int(avg_turns * 0.35), 8, 20)
                config.self_eval_second = _clamp(int(avg_turns * 0.6), 15, 35)
                config.self_eval_final = _clamp(int(avg_turns * 0.85), 25, 45)
                source_parts.append("experience")

    # --- Layer 3: paper_type 兜底 min_findings（仅在前两层都没设置时生效）---
    # 复杂论文类型需要更多 findings 才能覆盖多个维度
    if config.min_findings_for_exit == 0 and paper_type:
        _TYPE_MIN_FINDINGS: dict[str, int] = {
            "structural_econ": 6,
            "empirical_econ": 5,
            "ml_experiment": 5,
            "clinical": 5,
            "theoretical": 4,
            "survey": 3,
        }
        type_min = _TYPE_MIN_FINDINGS.get(paper_type, 0)
        if type_min > 0:
            config.min_findings_for_exit = type_min
            source_parts.append("paper_type_default")

    # --- 确定来源标记 ---
    if not source_parts:
        config.source = "default"
    elif len(set(source_parts)) == 1:
        config.source = source_parts[0]
    else:
        config.source = "mixed"

    return config


# ============================================================
# 经验查询
# ============================================================

def _query_experience(memory_store: "MemoryStore", paper_type: str) -> dict | None:
    """
    从跨会话记忆中查询同类论文的审稿行为统计。

    查找条件: ProceduralPattern 中 category="review_stats" 且
    trigger_context 包含当前 paper_type。

    Returns:
        {avg_idle_rounds: int, avg_total_turns: int, sample_count: int}
        或 None（无相关经验）
    """
    # 查询与 paper_type 相关的统计 pattern
    relevant_procs = [
        p for p in memory_store.state.procedures
        if p.category == "review_stats" and paper_type.lower() in p.trigger_context.lower()
    ]

    if not relevant_procs:
        return None

    # 取最近（evidence_count 最高）的统计
    best = max(relevant_procs, key=lambda p: p.evidence_count)

    # description 格式: "idle_avg=X,turns_avg=Y"
    stats = _parse_stats_description(best.description)
    if stats:
        stats["sample_count"] = best.evidence_count
        return stats

    return None


def _parse_stats_description(description: str) -> dict | None:
    """
    解析 review_stats pattern 的 description 字段。

    格式: "idle_avg=5,turns_avg=22"
    """
    try:
        parts = {}
        for segment in description.split(","):
            key, val = segment.strip().split("=")
            parts[key.strip()] = float(val.strip())

        if "idle_avg" in parts and "turns_avg" in parts:
            return {
                "avg_idle_rounds": _clamp(int(parts["idle_avg"]), 3, 10),
                "avg_total_turns": int(parts["turns_avg"]),
            }
    except (ValueError, KeyError):
        pass
    return None


# ============================================================
# 审稿行为统计记录（end_session 时调用）
# ============================================================

def record_review_stats(
    memory_store: "MemoryStore",
    paper_type: str,
    total_turns: int,
    idle_rounds_before_exit: int,
    findings_count: int,
) -> None:
    """
    记录本次审稿的行为统计到跨会话记忆。

    积累这些数据后，compute_gate_config 可以用经验数据
    替代硬编码默认值。

    Args:
        memory_store: 跨会话记忆
        paper_type: 论文类型
        total_turns: 本次审稿总轮次
        idle_rounds_before_exit: 退出前的连续无产出轮次
        findings_count: 产出的 findings 总数
    """
    if not paper_type or total_turns < 3:
        return

    description = f"idle_avg={idle_rounds_before_exit},turns_avg={total_turns}"
    trigger_context = f"论文类型: {paper_type}, findings={findings_count}"

    # effectiveness_score 基于产出密度（findings/turns）
    density = findings_count / max(total_turns, 1)
    effectiveness = min(density * 2, 1.0)  # 0.5 findings/turn = 满分

    memory_store.add_or_reinforce_procedure(
        category="review_stats",
        description=description,
        trigger_context=trigger_context,
        effectiveness_score=effectiveness,
    )


# ============================================================
# Helpers
# ============================================================

def _clamp(value: int, min_val: int, max_val: int) -> int:
    """将值约束在 [min_val, max_val] 范围内。"""
    return max(min_val, min(value, max_val))


def compute_idle_rounds_before_exit(tool_call_history: list[dict], findings: list[dict]) -> int:
    """
    计算退出前的连续无产出轮次。

    从 tool_call_history 倒序扫描，找到最后一次 update_findings 的位置，
    之后的轮次数即为 idle_rounds_before_exit。
    """
    if not tool_call_history:
        return 0

    last_update_idx = -1
    for i in range(len(tool_call_history) - 1, -1, -1):
        if tool_call_history[i].get("name") == "update_findings":
            last_update_idx = i
            break

    if last_update_idx == -1:
        # 从未产出 findings
        return len(tool_call_history)

    return len(tool_call_history) - 1 - last_update_idx
