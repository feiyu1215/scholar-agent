"""
core/adaptive_config.py — Runtime 参数自适应引擎 (B3)

设计原则 (来自 COGNITIVE_ANCHOR §5):
    - C5 约束-而非-控制: AdaptiveConfig 调整"信号触发阈值与 LLM 参数"，不干预 Agent 的决策权
    - 渐进退化: 没有足够的 state signals 时，回退到静态默认值
    - 可观察性: 所有自适应决策通过 adaptation_log 记录，可追溯

架构:
    AdaptiveConfig 是一个轻量级对象，由 Harness 持有。
    每轮 loop 调用 tick() 方法，读取 WorkspaceState 信号并调整参数。
    cognitive_loop 从 AdaptiveConfig 读取当前值（temperature、max_tokens 等），
    而非使用硬编码常量。

自适应策略:
    1. temperature: 根据当前 Phase 动态调整
       - INITIAL_SCAN: 0.2（精确扫描）
       - DEEP_REVIEW: 0.4（创造性发现）
       - EDITING: 0.1（精确修改）
       - SYNTHESIS: 0.3（综合平衡）
    2. max_nudges: 根据论文复杂度（sections 数量）调整
    3. keep_recent: 根据 context 利用率调整压缩策略
    4. signal_max_per_turn: 根据 session 进度调整

接口承诺:
    - tick() 是纯计算 O(1)，无 I/O
    - 所有属性有 sensible defaults，即使从不调用 tick() 也能正常工作
    - adaptation_log 记录所有非默认的参数变更（供调试/评测）
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.state import WorkspaceState

logger = logging.getLogger(__name__)

# Evidence threshold for config modification (宪法层约束)
EVIDENCE_MIN_FOR_ADJUST = 3
# Max adjustment per application (±20%)
MAX_ADJUST_RATIO = 0.20


# ============================================================
# 默认值
# ============================================================

# Temperature by phase
DEFAULT_TEMPERATURE = 0.3
TEMPERATURE_BY_PHASE = {
    "initial_scan": 0.2,
    "deep_review": 0.4,
    "editing": 0.1,
    "synthesis": 0.3,
}

# Nudge limits
DEFAULT_MAX_NUDGES = 2
COMPLEX_PAPER_SECTIONS_THRESHOLD = 15  # 超过此数认为是"复杂论文"

# Context compression
DEFAULT_KEEP_RECENT = 6
CONTEXT_PRESSURE_RATIO = 0.6  # context 利用率超过此值时压缩更激进

# Signal dispatcher
DEFAULT_SIGNAL_MAX_PER_TURN = 2
LATE_SESSION_TURN_RATIO = 0.75  # session 进度超过此比例后减少 signal


# ============================================================
# AdaptiveConfig
# ============================================================

@dataclass
class AdaptiveParam:
    """A single evolvable parameter with bounded constraints.

    DeepReflector can adjust current_value within [min_bound, max_bound]
    if evidence_count >= EVIDENCE_MIN_FOR_ADJUST. Each adjustment is clamped to ±20%.
    """
    name: str
    current_value: float
    min_bound: float
    max_bound: float
    evidence_count: int = 0  # accumulated evidence supporting adjustment

    def propose_adjustment(self, direction: float, evidence: int) -> float | None:
        """Propose a bounded adjustment.

        Args:
            direction: positive (+1) or negative (-1) signal
            evidence: number of evidence items supporting this change

        Returns:
            New value if adjustment is valid, None if blocked (insufficient evidence).
        """
        if evidence < EVIDENCE_MIN_FOR_ADJUST:
            return None

        # ±20% of current value
        delta = self.current_value * MAX_ADJUST_RATIO * (1 if direction > 0 else -1)
        new_value = self.current_value + delta

        # Clamp to bounds
        new_value = max(self.min_bound, min(self.max_bound, new_value))

        # If clamping made no change, skip
        if abs(new_value - self.current_value) < 1e-6:
            return None

        return new_value

    def apply_adjustment(self, new_value: float, evidence: int) -> None:
        """Apply an approved adjustment."""
        self.current_value = new_value
        self.evidence_count += evidence


@dataclass
class AdaptationEntry:
    """One adaptation decision log entry."""
    turn: int
    param: str
    old_value: float | int
    new_value: float | int
    reason: str


@dataclass
class AdaptiveConfig:
    """
    Runtime 自适应参数集。

    Harness 持有此对象，cognitive_loop 从中读取当前参数值。
    每轮开始时调用 tick(state) 更新参数。
    """

    # ---- 可自适应参数（当前值）----
    temperature: float = DEFAULT_TEMPERATURE
    max_tokens: int = 4096
    max_nudges: int = DEFAULT_MAX_NUDGES
    keep_recent: int = DEFAULT_KEEP_RECENT
    signal_max_per_turn: int = DEFAULT_SIGNAL_MAX_PER_TURN

    # ---- 内部状态 ----
    _last_phase: str = field(default="", repr=False)
    _tick_count: int = field(default=0, repr=False)
    adaptation_log: list[AdaptationEntry] = field(default_factory=list, repr=False)

    # ---- 冻结标记（测试或手动覆盖时使用）----
    frozen: bool = False

    def tick(self, state: "WorkspaceState") -> None:
        """
        每轮认知循环开始时调用，根据当前 state 调整参数。

        Args:
            state: Harness 的 WorkspaceState（只读访问）
        """
        if self.frozen:
            return

        self._tick_count += 1
        turn = state.loop_turns

        # ---- Strategy 1: Phase-based temperature ----
        self._adapt_temperature(state, turn)

        # ---- Strategy 2: Complexity-based max_nudges ----
        self._adapt_max_nudges(state, turn)

        # ---- Strategy 3: Context pressure-based compression ----
        self._adapt_keep_recent(state, turn)

        # ---- Strategy 4: Session progress-based signal limit ----
        self._adapt_signal_max(state, turn)

    def _adapt_temperature(self, state: "WorkspaceState", turn: int) -> None:
        """根据当前认知阶段调整 temperature。"""
        # Phase 由外部通过 set_phase() 注入，避免耦合 PhaseFSM 实现
        phase = getattr(self, "_phase_hint", "")
        if not phase:
            return

        phase_lower = phase.lower().replace(" ", "_")
        new_temp = TEMPERATURE_BY_PHASE.get(phase_lower, DEFAULT_TEMPERATURE)

        if abs(new_temp - self.temperature) > 0.01:
            self._log(turn, "temperature", self.temperature, new_temp,
                      f"phase={phase_lower}")
            self.temperature = new_temp
            self._last_phase = phase_lower

    def _adapt_max_nudges(self, state: "WorkspaceState", turn: int) -> None:
        """根据论文复杂度调整最大 nudge 次数。"""
        # 只有在实际加载了论文时才根据 section 数量调整
        if not state.paper_sections:
            return  # 无论文数据，保持当前值

        section_count = len(state.paper_sections)

        if section_count >= COMPLEX_PAPER_SECTIONS_THRESHOLD:
            new_val = 3
        elif section_count <= 5:
            new_val = 1
        else:
            new_val = DEFAULT_MAX_NUDGES

        if new_val != self.max_nudges:
            self._log(turn, "max_nudges", self.max_nudges, new_val,
                      f"sections={section_count}")
            self.max_nudges = new_val

    def _adapt_keep_recent(self, state: "WorkspaceState", turn: int) -> None:
        """根据 context window 利用率调整消息保留数。"""
        if state.context_window <= 0:
            return

        # 估算 context 利用率：last_prompt_tokens / context_window
        if state.last_prompt_tokens <= 0:
            return

        utilization = state.last_prompt_tokens / state.context_window

        if utilization > CONTEXT_PRESSURE_RATIO:
            new_val = 4  # 压力大，压缩更激进
        elif utilization < 0.3:
            new_val = 8  # 充裕，保留更多
        else:
            new_val = DEFAULT_KEEP_RECENT

        if new_val != self.keep_recent:
            self._log(turn, "keep_recent", self.keep_recent, new_val,
                      f"ctx_util={utilization:.2f}")
            self.keep_recent = new_val

    def _adapt_signal_max(self, state: "WorkspaceState", turn: int) -> None:
        """Session 后期减少信号注入，避免干扰收尾。"""
        if state.max_loop_turns <= 0:
            return

        progress = turn / state.max_loop_turns

        if progress > LATE_SESSION_TURN_RATIO:
            new_val = 1  # 后期只保留 1 个信号
        else:
            new_val = DEFAULT_SIGNAL_MAX_PER_TURN

        if new_val != self.signal_max_per_turn:
            self._log(turn, "signal_max_per_turn", self.signal_max_per_turn, new_val,
                      f"progress={progress:.2f}")
            self.signal_max_per_turn = new_val

    def _log(self, turn: int, param: str, old: float | int, new: float | int, reason: str) -> None:
        """记录自适应变更到 log。"""
        self.adaptation_log.append(AdaptationEntry(
            turn=turn, param=param, old_value=old, new_value=new, reason=reason,
        ))

    # ---- 外部注入 phase（由 Harness 调用）----
    def set_phase(self, phase_name: str) -> None:
        """由 Harness 或 loop 调用，通知当前 phase 变更。"""
        # 这个方法不直接改 temperature，而是设置 _current_phase_hint
        # tick() 下一轮会读取并调整
        self._phase_hint = phase_name

    # ---- 查询接口 ----
    def get_adaptation_summary(self) -> dict:
        """返回当前参数快照 + 变更统计（供评测/日志）。"""
        return {
            "current": {
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
                "max_nudges": self.max_nudges,
                "keep_recent": self.keep_recent,
                "signal_max_per_turn": self.signal_max_per_turn,
            },
            "total_adaptations": len(self.adaptation_log),
            "tick_count": self._tick_count,
            "frozen": self.frozen,
        }

    def describe(self) -> str:
        """人可读的当前参数描述。"""
        return (
            f"AdaptiveConfig[ticks={self._tick_count}]: "
            f"temp={self.temperature}, nudges={self.max_nudges}, "
            f"keep={self.keep_recent}, "
            f"signals={self.signal_max_per_turn}"
        )

    # ---- B3 Enhancement: Evolvable Parameters ----

    # Registry of evolvable params with bounds (宪法层)
    evolvable_params: dict[str, AdaptiveParam] = field(default_factory=dict, repr=False)

    def __post_init__(self):
        """Initialize evolvable param registry with defaults and bounds."""
        if not self.evolvable_params:
            self.evolvable_params = {
                "temperature": AdaptiveParam(
                    name="temperature",
                    current_value=self.temperature,
                    min_bound=0.05, max_bound=0.7,
                ),
                "max_nudges": AdaptiveParam(
                    name="max_nudges",
                    current_value=float(self.max_nudges),
                    min_bound=1.0, max_bound=5.0,
                ),
                "keep_recent": AdaptiveParam(
                    name="keep_recent",
                    current_value=float(self.keep_recent),
                    min_bound=2.0, max_bound=12.0,
                ),
                "signal_max_per_turn": AdaptiveParam(
                    name="signal_max_per_turn",
                    current_value=float(self.signal_max_per_turn),
                    min_bound=1.0, max_bound=4.0,
                ),
            }

    def adjust_from_evidence(
        self, param_name: str, direction: float, evidence_count: int
    ) -> bool:
        """Adjust a parameter based on accumulated evidence.

        Called by DeepReflector when 'adjust_config' decisions are made.

        Args:
            param_name: Name of the evolvable parameter
            direction: +1 (increase) or -1 (decrease)
            evidence_count: Number of evidence items supporting this change

        Returns:
            True if adjustment was applied, False if blocked or invalid.
        """
        param = self.evolvable_params.get(param_name)
        if param is None:
            logger.warning("adjust_from_evidence: unknown param '%s'", param_name)
            return False

        new_value = param.propose_adjustment(direction, evidence_count)
        if new_value is None:
            logger.info(
                "adjust_from_evidence: blocked for '%s' (evidence=%d, need≥%d)",
                param_name, evidence_count, EVIDENCE_MIN_FOR_ADJUST,
            )
            return False

        old_value = param.current_value
        param.apply_adjustment(new_value, evidence_count)

        # Sync back to the actual config fields
        self._sync_param_to_field(param_name, new_value)

        self._log(
            turn=-1,  # -1 indicates evolution-driven (not turn-based)
            param=param_name,
            old=old_value,
            new=new_value,
            reason=f"evidence={evidence_count}, direction={'↑' if direction > 0 else '↓'}",
        )

        logger.info(
            "AdaptiveConfig adjusted '%s': %.3f → %.3f (evidence=%d)",
            param_name, old_value, new_value, evidence_count,
        )
        return True

    def _sync_param_to_field(self, param_name: str, value: float) -> None:
        """Sync evolvable param value back to the actual dataclass field."""
        if param_name == "temperature":
            self.temperature = value
        elif param_name == "max_nudges":
            self.max_nudges = int(round(value))
        elif param_name == "keep_recent":
            self.keep_recent = int(round(value))
        elif param_name == "signal_max_per_turn":
            self.signal_max_per_turn = int(round(value))

    # ---- B3 Enhancement: Persistence ----

    def persist(self, path: Path | str) -> None:
        """Save current evolvable params to JSON for cross-session persistence.

        Only saves params that have accumulated evidence (i.e., have been adjusted).
        """
        path = Path(path)
        data = {}
        for name, param in self.evolvable_params.items():
            if param.evidence_count > 0:
                data[name] = {
                    "current_value": param.current_value,
                    "evidence_count": param.evidence_count,
                }

        if data:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            logger.info("AdaptiveConfig persisted %d params to %s", len(data), path)

    def load_persisted(self, path: Path | str) -> int:
        """Load previously persisted param adjustments.

        Returns number of params loaded.
        """
        path = Path(path)
        if not path.exists():
            return 0

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError) as e:
            logger.warning("Failed to load persisted config from %s: %s", path, e)
            return 0

        loaded = 0
        for name, values in data.items():
            param = self.evolvable_params.get(name)
            if param is None:
                continue

            new_value = values.get("current_value", param.current_value)
            # Validate against bounds (safety: file may be stale)
            new_value = max(param.min_bound, min(param.max_bound, new_value))
            param.current_value = new_value
            param.evidence_count = values.get("evidence_count", 0)

            # Sync to actual fields
            self._sync_param_to_field(name, new_value)
            loaded += 1

        if loaded:
            logger.info("AdaptiveConfig loaded %d persisted params from %s", loaded, path)
        return loaded

    # ---- 工厂方法 ----
    @classmethod
    def frozen_default(cls) -> "AdaptiveConfig":
        """创建一个冻结的默认配置（用于测试或不需要自适应的场景）。"""
        config = cls()
        config.frozen = True
        return config

    @classmethod
    def from_overrides(cls, **kwargs) -> "AdaptiveConfig":
        """从显式覆盖创建（用于测试特定参数组合）。"""
        valid_fields = {
            "temperature", "max_tokens", "max_nudges",
            "keep_recent", "signal_max_per_turn", "frozen",
        }
        filtered = {k: v for k, v in kwargs.items() if k in valid_fields}
        return cls(**filtered)
