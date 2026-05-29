"""
core/meta_reflect.py — V3 Phase 2: Tri-Frequency MetaReflector

三频元反思系统:
    - FastReflector: 每 3 sessions, 纯规则, zero LLM
      检测趋势下降 (findings_density / evidence_ratio 连续下降)
    - EmergencyReflector: 实时, zero LLM
      紧急触发条件: idle > 10 & findings < 2 OR tokens > 80K & findings < 3
    - DeepReflector: 每 10 sessions 或异常时, full LLM
      基于 SessionReflector 扩展, 加入 V3 contrast evidence + section efficiency

设计原则:
    - 所有 Reflector 失败时 gracefully 返回 None/空列表, 不影响 session 结束
    - Kill switch 控制 (godel_config.py): GODEL_FAST_REFLECT_ENABLED,
      GODEL_EMERGENCY_REFLECT_ENABLED, GODEL_DEEP_REFLECT_ENABLED
    - Fast/Emergency: 零 LLM 开销, 纯统计规则
    - Deep: 一次 LLM call (~2000 tokens input), 用于高层次决策
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable, Awaitable, Any

if TYPE_CHECKING:
    from core.adaptive_config import AdaptiveConfig
    from core.memory import MemoryStore
    from core.state import WorkspaceState
    from core.evolution import LearnedHabit

logger = logging.getLogger(__name__)


# ============================================================
# FastReflector: 每 3 sessions, zero LLM
# ============================================================

class FastReflector:
    """Every 3 sessions, pure rules, zero LLM.

    Integration point: called in session_finalizer.end_session_with_reflection()
    after SessionReflector.reflect() completes.

    Detects:
    1. findings_density declining for 3 consecutive sessions
    2. evidence_ratio (findings_per_1k_tokens) declining trend
    3. pcg_coverage stagnation (no improvement for 3 sessions)
    """

    TRIGGER_INTERVAL = 3

    def should_trigger(self, memory_store: "MemoryStore") -> bool:
        """Check if fast reflect should run.

        Triggers when number of session experiences since last fast reflect >= 3.
        P2-fix9: Respects COLD_START_SESSION_THRESHOLD — no-op during cold start.
        """
        try:
            from core.godel_config import COLD_START_SESSION_THRESHOLD
            exps = memory_store.state.session_experiences_v3
            if len(exps) < COLD_START_SESSION_THRESHOLD:
                return False  # Cold start: not enough data for meaningful reflection
            last_fast = getattr(memory_store.state, "_last_fast_reflect_count", 0)
            return len(exps) - last_fast >= self.TRIGGER_INTERVAL
        except Exception:
            return False

    def analyze(self, memory_store: "MemoryStore") -> list[str]:
        """Pure rule analysis. Returns alert messages.

        Checks last 3 session experiences for declining trends.
        Returns up to 3 alert strings.
        """
        try:
            exps = memory_store.state.session_experiences_v3
            if len(exps) < 3:
                return []

            recent = exps[-3:]
            alerts = []

            # Check 1: findings_per_1k_tokens declining 3 consecutive sessions
            densities = [e.get("findings_per_1k_tokens", 0) for e in recent]
            if self._is_declining(densities):
                alerts.append(
                    f"⚠️ findings_density 连续下降: "
                    f"{densities[0]:.2f} → {densities[1]:.2f} → {densities[2]:.2f}"
                )

            # Check 2: findings_count declining
            findings_counts = [e.get("findings_count", 0) for e in recent]
            if self._is_declining(findings_counts) and findings_counts[-1] <= 2:
                alerts.append(
                    f"⚠️ findings_count 持续下降且低于阈值: "
                    f"{findings_counts[0]} → {findings_counts[1]} → {findings_counts[2]}"
                )

            # Check 3: pcg_coverage stagnation
            coverages = [e.get("pcg_coverage", 0) for e in recent]
            if all(c > 0 for c in coverages):
                max_diff = max(coverages) - min(coverages)
                if max_diff < 0.05 and coverages[-1] < 0.7:
                    alerts.append(
                        f"⚠️ pcg_coverage 停滞不前 ({coverages[-1]:.2f}), "
                        f"3 sessions 内变化 < 5%"
                    )

            return alerts[:3]  # Max 3 alerts
        except Exception as e:
            logger.warning("FastReflector.analyze failed: %s", e)
            return []

    def apply(self, alerts: list[str], memory_store: "MemoryStore") -> None:
        """Persist alerts as meta_note for next session.

        Stores alerts in memory state and updates trigger counter.
        """
        try:
            if alerts:
                # Keep last 3 alerts for next session injection
                memory_store.state.fast_reflect_alerts = alerts[-3:]
            else:
                # Clear alerts if analysis found no issues
                memory_store.state.fast_reflect_alerts = []
            # Update the counter to prevent re-triggering
            memory_store.state._last_fast_reflect_count = len(
                memory_store.state.session_experiences_v3
            )
        except Exception as e:
            logger.warning("FastReflector.apply failed: %s", e)

    @staticmethod
    def _is_declining(values: list[float | int]) -> bool:
        """Check if a list of 3+ values is strictly declining."""
        if len(values) < 3:
            return False
        return all(values[i] > values[i + 1] for i in range(len(values) - 1))


# ============================================================
# EmergencyReflector: Realtime, zero LLM
# ============================================================

class EmergencyReflector:
    """Realtime trigger within session. Zero LLM.

    Integration point: called in session_finalizer.end_session_with_reflection()
    BEFORE Deep reflect, so that emergency can reduce habit confidence immediately.

    Trigger conditions (any one → trigger):
    1. idle_before_exit > 10 AND findings < 2
    2. total_tokens > 80_000 AND findings < 3

    Action: reduce confidence of most-recently-used habit by 0.1 (max 1 per session).
    """

    # Thresholds (constitutional layer constants)
    IDLE_THRESHOLD = 10
    FINDINGS_LOW_THRESHOLD = 2
    TOKEN_THRESHOLD = 80_000
    FINDINGS_TOKEN_THRESHOLD = 3
    CONFIDENCE_REDUCTION = 0.1

    def check(self, state: "WorkspaceState") -> dict | None:
        """Check if emergency conditions met.

        Args:
            state: Current session's WorkspaceState

        Returns:
            Emergency result dict with 'reason' and 'suspect_habits', or None.
        """
        try:
            from core.gate_config import compute_idle_rounds_before_exit

            findings_count = len(state.findings)
            idle_before_exit = compute_idle_rounds_before_exit(
                state.tool_call_history, state.findings
            )

            # Condition 1: High idle + low findings
            if idle_before_exit > self.IDLE_THRESHOLD and findings_count < self.FINDINGS_LOW_THRESHOLD:
                suspect_habits = self._identify_suspect_habits(state)
                return {
                    "reason": (
                        f"idle_before_exit={idle_before_exit} > {self.IDLE_THRESHOLD} "
                        f"AND findings={findings_count} < {self.FINDINGS_LOW_THRESHOLD}"
                    ),
                    "suspect_habits": suspect_habits,
                    "trigger_type": "idle_low_findings",
                }

            # Condition 2: High tokens + low findings
            total_tokens = state.total_tokens or 0
            if total_tokens > self.TOKEN_THRESHOLD and findings_count < self.FINDINGS_TOKEN_THRESHOLD:
                suspect_habits = self._identify_suspect_habits(state)
                return {
                    "reason": (
                        f"total_tokens={total_tokens} > {self.TOKEN_THRESHOLD} "
                        f"AND findings={findings_count} < {self.FINDINGS_TOKEN_THRESHOLD}"
                    ),
                    "suspect_habits": suspect_habits,
                    "trigger_type": "high_tokens_low_findings",
                }

            return None
        except Exception as e:
            logger.warning("EmergencyReflector.check failed: %s", e)
            return None

    def apply_emergency(
        self,
        result: dict,
        memory_store: "MemoryStore",
        learned_habits: list,
    ) -> None:
        """Immediate confidence reduction for suspect habits.

        Reduces confidence of at most 1 suspect habit by CONFIDENCE_REDUCTION.
        """
        try:
            suspect_ids = result.get("suspect_habits", [])[:1]  # Max 1 per session
            for habit_id in suspect_ids:
                for h in learned_habits:
                    if h.id == habit_id:
                        old_conf = h.confidence
                        h.confidence = max(0.0, h.confidence - self.CONFIDENCE_REDUCTION)
                        logger.info(
                            "Emergency: reduced confidence of habit '%s' "
                            "from %.2f to %.2f",
                            habit_id, old_conf, h.confidence,
                        )
                        break
        except Exception as e:
            logger.warning("EmergencyReflector.apply_emergency failed: %s", e)

    @staticmethod
    def _identify_suspect_habits(state: "WorkspaceState") -> list[str]:
        """Identify habits that might be causing inefficiency.

        Strategy: If a contrast plan was active, the target habit is suspect.
        Otherwise, return empty (no habit to blame).
        """
        contrast_plan = state.contrast_plan
        if contrast_plan:
            target = contrast_plan.get("target_habit_id")
            if target:
                return [target]
        return []


# ============================================================
# DeepReflector: Every 10 sessions or anomaly, full LLM
# ============================================================

_DEEP_REFLECT_SYSTEM_PROMPT = """\
你是一个经验丰富的审稿 Agent 的元认知系统。你正在回顾最近 10 次审稿会话的表现，做深度反思。

你的任务是：
1. 评估当前的审稿习惯是否有效
2. 基于 IntraSession Contrast 证据，判断哪些习惯应该加强/削弱/淘汰
3. 产出高层次决策建议

输出格式（严格 JSON）：
```json
{
  "habit_decisions": [
    {
      "habit_id": "xxx",
      "action": "boost|reduce|retire",
      "confidence_delta": 0.05-0.2,
      "reasoning": "简要理由（<80字）"
    }
  ],
  "maturity_updates": [
    {
      "paper_type": "xxx",
      "new_maturity": 0.0-1.0,
      "reasoning": "简要理由"
    }
  ],
  "meta_note": "给下一次 DeepReflect 的备忘（<150字）",
  "token_efficiency_assessment": "improving|stable|declining"
}
```

约束：
- habit_decisions 最多 3 条（只处理最需要调整的）
- confidence_delta: boost 为正（+0.05~+0.2），reduce/retire 为负 (-0.05~-0.2)
- maturity_updates: 只在有明确证据时调整
- 如果一切正常无需调整，返回空 habit_decisions 和 meta_note="维持现状"
"""

_DEEP_REFLECT_USER_TEMPLATE = """\
## 最近会话统计

{session_summary}

## IntraSession Contrast Evidence (V3)

{contrast_text}

## Section-Level Efficiency Analysis (V3)

{efficiency_text}

## 当前习惯状态

{habits_text}

## 上次 FastReflector 警报

{fast_alerts}

---

请做深度反思，产出决策建议。如果一切正常，返回空建议。
"""


class DeepReflector:
    """V3 enhanced deep reflection. Every 10 sessions or on anomaly.

    Differences from V2 SessionReflector:
    1. precompute_context_v3() adds IntraSession contrast evidence
    2. should_trigger_v3() adds anomaly-based triggers
    3. apply_decisions_v3() updates L2 evolution_records + habit confidence

    Graceful degradation: returns empty results on any failure.
    """

    TRIGGER_INTERVAL = 10  # Every 10 sessions

    def __init__(
        self,
        llm_call_fn: Callable[[str, str, int], Awaitable[str]] | None = None,
    ):
        """
        Args:
            llm_call_fn: Async LLM call function. Signature:
                async (system: str, user: str, max_tokens: int) -> str
        """
        self._llm_call_fn = llm_call_fn

    def should_trigger_v3(self, memory_store: "MemoryStore") -> bool:
        """V3 trigger conditions.

        Trigger if:
        - Every 10 sessions (interval-based) OR
        - FastReflector reported anomalies (2+ alerts present) OR
        - maturity sudden change (would require multi-session tracking)

        P2-fix9: Respects COLD_START_SESSION_THRESHOLD — no-op during cold start.
        """
        try:
            from core.godel_config import COLD_START_SESSION_THRESHOLD
            exps = memory_store.state.session_experiences_v3
            if len(exps) < COLD_START_SESSION_THRESHOLD:
                return False  # Cold start: not enough data for deep reflection

            last_deep = getattr(memory_store.state, "_last_deep_reflect_count", 0)

            # Condition 1: interval
            if len(exps) - last_deep >= self.TRIGGER_INTERVAL:
                return True

            # Condition 2: FastReflector raised alerts (consecutive anomaly)
            alerts = getattr(memory_store.state, "fast_reflect_alerts", [])
            if len(alerts) >= 2:
                return True

            return False
        except Exception:
            return False

    def precompute_context_v3(
        self,
        memory_store: "MemoryStore",
        learned_habits: list,
    ) -> str:
        """V3 enhanced context with contrast evidence + section efficiency.

        Builds the user prompt for the LLM DeepReflect call.
        """
        try:
            # Session summary (last 10)
            exps = memory_store.state.session_experiences_v3[-10:]
            session_lines = []
            for i, exp in enumerate(exps, 1):
                session_lines.append(
                    f"  Session {i}: findings={exp.get('findings_count', 0)}, "
                    f"tokens={exp.get('total_tokens', 0)}, "
                    f"density={exp.get('findings_per_1k_tokens', 0):.2f}/1k, "
                    f"pcg={exp.get('pcg_coverage', 0):.2f}, "
                    f"type={exp.get('paper_type', 'unknown')}"
                )
            session_summary = "\n".join(session_lines) if session_lines else "  (无会话数据)"

            # Contrast evidence
            contrast_results = memory_store.state.contrast_results[-10:]
            contrast_text = self._format_contrast_evidence(contrast_results)

            # Section efficiency
            section_exps = memory_store.state.section_experiences[-100:]
            efficiency_text = self._format_section_efficiency(section_exps, learned_habits)

            # Current habits
            habits_text = self._format_habits(learned_habits)

            # Fast alerts
            fast_alerts = getattr(memory_store.state, "fast_reflect_alerts", [])
            fast_alerts_text = "\n".join(f"  - {a}" for a in fast_alerts) if fast_alerts else "  (无警报)"

            return _DEEP_REFLECT_USER_TEMPLATE.format(
                session_summary=session_summary,
                contrast_text=contrast_text,
                efficiency_text=efficiency_text,
                habits_text=habits_text,
                fast_alerts=fast_alerts_text,
            )
        except Exception as e:
            logger.warning("DeepReflector.precompute_context_v3 failed: %s", e)
            return "(context computation failed)"

    async def reflect(self, context: str) -> dict | None:
        """Execute deep reflection via LLM call.

        Args:
            context: The user prompt (from precompute_context_v3)

        Returns:
            Parsed result dict, or None on failure.
        """
        if self._llm_call_fn is None:
            return None

        try:
            response = await self._llm_call_fn(
                _DEEP_REFLECT_SYSTEM_PROMPT,
                context,
                1000,  # max_tokens
            )
            return self._parse_response(response)
        except Exception as e:
            logger.warning("DeepReflector.reflect LLM call failed: %s", e)
            return None

    def apply_decisions_v3(
        self,
        result: dict,
        memory_store: "MemoryStore",
        learned_habits: list,
        adaptive_config: "AdaptiveConfig | None" = None,
    ) -> dict:
        """V3: Apply habit decisions + config adjustments + persist L2 EvolutionRecord.

        Args:
            result: Parsed LLM output dict
            memory_store: Memory store instance
            learned_habits: Current learned habits list
            adaptive_config: Optional AdaptiveConfig instance for evidence-based tuning

        Returns:
            Report dict with applied changes summary.
        """
        report = {
            "habits_adjusted": 0,
            "config_adjusted": 0,
            "evolution_recorded": False,
        }

        try:
            # Apply habit confidence changes
            habit_decisions = result.get("habit_decisions", [])
            for decision in habit_decisions[:3]:  # Max 3
                habit_id = decision.get("habit_id", "")
                action = decision.get("action", "")
                delta = decision.get("confidence_delta", 0)

                for h in learned_habits:
                    if h.id == habit_id:
                        if action == "boost":
                            h.confidence = min(1.0, h.confidence + abs(delta))
                        elif action in ("reduce", "retire"):
                            h.confidence = max(0.0, h.confidence - abs(delta))
                        report["habits_adjusted"] += 1
                        break

            # B3 Enhancement: Apply config adjustment decisions
            if adaptive_config is not None:
                config_decisions = result.get("config_decisions", [])
                for cd in config_decisions[:3]:  # Max 3 adjustments per reflect
                    param_name = cd.get("param", "")
                    direction = cd.get("direction", 0)
                    evidence = cd.get("evidence_count", 0)
                    if param_name and direction != 0:
                        applied = adaptive_config.adjust_from_evidence(
                            param_name, direction, evidence
                        )
                        if applied:
                            report["config_adjusted"] += 1

            # P2-fix10: Apply maturity_updates to memory state
            maturity_updates = result.get("maturity_updates", [])
            for mu in maturity_updates[:3]:
                if isinstance(mu, dict) and "paper_type" in mu:
                    pt = mu["paper_type"]
                    new_mat = mu.get("new_maturity", 0.5)
                    if isinstance(new_mat, (int, float)):
                        new_mat = max(0.0, min(1.0, float(new_mat)))
                    else:
                        new_mat = 0.5
                    memory_store.state.maturity_levels[pt] = new_mat
            report["maturity_updated"] = len(maturity_updates[:3])

            # Persist L2 evolution record
            now = datetime.now(timezone.utc).isoformat()
            evolution_record = {
                "timestamp": now,
                "trigger_type": "deep",
                "session_count": len(memory_store.state.session_experiences_v3),
                "habit_decisions": habit_decisions,
                "config_decisions": result.get("config_decisions", []),
                "maturity_updates": result.get("maturity_updates", []),
                "contrast_evidence_count": len(memory_store.state.contrast_results),
                "meta_note": result.get("meta_note", ""),
                "token_efficiency_assessment": result.get(
                    "token_efficiency_assessment", "unknown"
                ),
            }
            memory_store.persist_evolution_record(evolution_record)
            report["evolution_recorded"] = True

            # Update trigger counter
            memory_store.state._last_deep_reflect_count = len(
                memory_store.state.session_experiences_v3
            )

            # Clear fast_reflect_alerts after deep processing
            memory_store.state.fast_reflect_alerts = []

        except Exception as e:
            logger.warning("DeepReflector.apply_decisions_v3 failed: %s", e)

        return report

    # ----------------------------------------------------------
    # Internal helpers
    # ----------------------------------------------------------

    @staticmethod
    def _format_contrast_evidence(contrast_results: list[dict]) -> str:
        """Format contrast results for LLM context."""
        if not contrast_results:
            return "  (无 contrast 证据)"

        lines = []
        for r in contrast_results[-5:]:  # Show last 5
            target = r.get("target_habit_id", "unknown")
            rec = r.get("recommendation", "unknown")
            delta = r.get("findings_delta", 0)
            lines.append(
                f"  - habit={target}: recommendation={rec}, "
                f"findings_delta={delta:+.1f}"
            )
        return "\n".join(lines)

    @staticmethod
    def _format_section_efficiency(
        section_exps: list[dict],
        learned_habits: list,
    ) -> str:
        """Format section-level efficiency analysis."""
        if not section_exps:
            return "  (无 section 数据)"

        # Aggregate by habit presence
        from collections import defaultdict
        habit_stats: dict[str, dict] = defaultdict(
            lambda: {"findings": 0, "tokens": 0, "sections": 0}
        )

        for exp in section_exps:
            active_ids = exp.get("active_habit_ids", [])
            findings = exp.get("findings_produced", 0)
            tokens = exp.get("tokens_consumed", 1)

            key = ",".join(sorted(active_ids)) if active_ids else "(no habits)"
            habit_stats[key]["findings"] += findings
            habit_stats[key]["tokens"] += tokens
            habit_stats[key]["sections"] += 1

        lines = []
        for combo, stats in sorted(
            habit_stats.items(), key=lambda x: -x[1]["sections"]
        )[:5]:
            density = stats["findings"] / max(stats["tokens"] / 1000, 0.1)
            lines.append(
                f"  habits=[{combo}]: {stats['sections']} sections, "
                f"{stats['findings']} findings, {density:.2f}/1k tokens"
            )
        return "\n".join(lines)

    @staticmethod
    def _format_habits(learned_habits: list) -> str:
        """Format current habit status."""
        if not learned_habits:
            return "  (无学习习惯)"

        lines = []
        for h in learned_habits:
            lines.append(
                f"  - {h.id}: confidence={h.confidence:.2f}, "
                f"name={h.name}"
            )
        return "\n".join(lines)

    @staticmethod
    def _parse_response(response: str) -> dict | None:
        """Parse LLM JSON response."""
        import json

        text = response.strip()

        # Handle markdown code block wrapping
        if "```json" in text:
            start = text.index("```json") + 7
            end = text.index("```", start) if "```" in text[start:] else len(text)
            text = text[start:end].strip()
        elif "```" in text:
            start = text.index("```") + 3
            end = text.index("```", start) if "```" in text[start:] else len(text)
            text = text[start:end].strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("DeepReflector: failed to parse JSON response")
            return None

        if not isinstance(data, dict):
            return None

        # Validate and sanitize
        result = {
            "habit_decisions": [],
            "maturity_updates": [],
            "meta_note": data.get("meta_note", "")[:200],
            "token_efficiency_assessment": data.get(
                "token_efficiency_assessment", "unknown"
            ),
        }

        # Validate habit_decisions
        for d in data.get("habit_decisions", [])[:3]:
            if isinstance(d, dict) and "habit_id" in d and "action" in d:
                action = d["action"]
                if action in ("boost", "reduce", "retire"):
                    delta = d.get("confidence_delta", 0.1)
                    if isinstance(delta, (int, float)):
                        delta = max(0.05, min(0.2, abs(float(delta))))
                    else:
                        delta = 0.1
                    result["habit_decisions"].append({
                        "habit_id": d["habit_id"],
                        "action": action,
                        "confidence_delta": delta,
                        "reasoning": d.get("reasoning", "")[:100],
                    })

        # Validate maturity_updates
        for m in data.get("maturity_updates", [])[:3]:
            if isinstance(m, dict) and "paper_type" in m:
                new_mat = m.get("new_maturity", 0.5)
                if isinstance(new_mat, (int, float)):
                    new_mat = max(0.0, min(1.0, float(new_mat)))
                else:
                    new_mat = 0.5
                result["maturity_updates"].append({
                    "paper_type": m["paper_type"],
                    "new_maturity": new_mat,
                    "reasoning": m.get("reasoning", "")[:100],
                })

        return result
