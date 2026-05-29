"""
session_finalizer.py — 会话结束时的记忆沉淀模块

从 harness.py 提取。负责在审稿会话结束时：
1. 构建 SessionRecord（压缩 findings 为摘要）
2. 从 verified findings 中提取可积累的领域模式（Layer 2: WHAT）
3. 从工具调用序列中提取程序性模式（Layer 3: HOW, Phase 54）
4. 将 Agent 的认知提示持久化为跨会话经验
5. 记录审稿行为统计
6. 持久化到磁盘
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from core.state import WorkspaceState
from core.memory import (
    MemoryStore,
    build_session_record,
    extract_domain_patterns,
    extract_procedural_patterns,
)
from core.cognition_graph import persist_cognitive_hints_as_experience
from core.gate_config import (
    record_review_stats,
    compute_idle_rounds_before_exit,
)

logger = logging.getLogger(__name__)


def end_session(
    state: WorkspaceState,
    memory: MemoryStore,
    paper_id: str | None,
    strategy_transitions: list | None,
    paper_title: str = "",
    user_messages: list[str] | None = None,
) -> None:
    """
    会话结束时调用: 将当前会话的认知产出沉淀到跨会话记忆。

    Args:
        state: 工作区状态
        memory: 记忆存储实例
        paper_id: 论文唯一标识（hash）
        strategy_transitions: 策略转换记录（用于程序性模式提取）
        paper_title: 论文标题（如果未提供，尝试从 paper_sections 推断）
        user_messages: 用户发送的消息列表（用于记录用户关注点）
    """
    if not state.findings:
        # 没有任何发现，不值得记录
        return

    # 确保 paper_id 存在
    if not paper_id and state.paper_sections:
        paper_id = MemoryStore.compute_paper_id(state.paper_sections)

    if not paper_id:
        return

    # 推断论文标题
    if not paper_title:
        paper_title = _infer_paper_title(state, paper_id)

    # 1. 构建 SessionRecord
    record = build_session_record(
        paper_id=paper_id,
        paper_title=paper_title,
        findings=state.findings,
        conversation_turns=state.conversation_turns,
        loop_turns=state.loop_turns,
        total_tokens=state.total_tokens,
        user_messages=user_messages,
    )
    memory.persist_session(record)

    # 2. 提取并积累领域模式（Layer 2: WHAT）
    patterns = extract_domain_patterns(state.findings, paper_id)
    for category, description in patterns:
        memory.add_or_reinforce_pattern(category, description, paper_id)

    # 3. Phase 54: 提取并积累程序性模式（Layer 3: HOW）
    tool_names = [t.get("name", "") for t in state.tool_call_history]
    procedural_patterns = extract_procedural_patterns(
        tool_call_history=tool_names,
        findings_count=len(state.findings),
        loop_turns=state.loop_turns,
        strategy_transitions=strategy_transitions if strategy_transitions else None,
    )
    for cat, desc, trigger, score in procedural_patterns:
        memory.add_or_reinforce_procedure(cat, desc, trigger, score)

    # 4. K1: 将 Agent 的认知提示持久化为跨会话经验
    if state.cognitive_hints:
        persist_cognitive_hints_as_experience(
            cognitive_hints=state.cognitive_hints,
            memory_store=memory,
            paper_id=paper_id,
            findings_count=len(state.findings),
        )

    # 5. P2: Agent 自省（如果提供了 reflector 则用 Agent 反思，否则跳过）
    # 注意: 旧的硬编码 _extract_edit_strategies 已移除。
    # 反思由调用者在 async 上下文中触发（见 end_session_async）。

    # 6. B4: 记录审稿行为统计（用于长期数据驱动的 gate 参数优化）
    paper_type = ""
    if state.cognitive_hints and state.cognitive_hints.paper_type_description:
        paper_type = state.cognitive_hints.paper_type_description
    elif state.paper_structure_index and not state.paper_structure_index.is_empty():
        paper_type = state.paper_structure_index.paper_type

    if paper_type:
        idle_before_exit = compute_idle_rounds_before_exit(
            state.tool_call_history, state.findings
        )
        record_review_stats(
            memory_store=memory,
            paper_type=paper_type,
            total_turns=state.loop_turns,
            idle_rounds_before_exit=idle_before_exit,
            findings_count=len(state.findings),
        )

    # 7. 【V3 NEW】Record section-level experiences (L0)
    from core.godel_config import (
        GODEL_SECTION_EXPERIENCE_ENABLED,
        GODEL_INTRA_CONTRAST_ENABLED,
    )
    if GODEL_SECTION_EXPERIENCE_ENABLED:
        _record_section_experiences(state=state, memory=memory, paper_type=paper_type)

    # 8. 【V3 NEW】Record V3 session experience (L1)
    if GODEL_SECTION_EXPERIENCE_ENABLED:
        _record_session_experience_v3(state=state, memory=memory, paper_type=paper_type)

    # 9. 【V3 NEW】Analyze IntraSession contrast (if plan exists)
    if GODEL_INTRA_CONTRAST_ENABLED:
        _analyze_and_persist_contrast(state=state, memory=memory)

    # 10. 持久化
    memory.save()


async def end_session_with_reflection(
    state: WorkspaceState,
    memory: MemoryStore,
    paper_id: str | None,
    strategy_transitions: list | None,
    llm_call_fn=None,
    paper_title: str = "",
    user_messages: list[str] | None = None,
    adaptive_config=None,
) -> dict:
    """
    带 Agent 自省的 session 结束流程。

    在 end_session 的基础上增加:
    - Agent 自省（LLM call）: 让 Agent 回顾会话行为，自己决定学到了什么
    - 将反思结果存入 ProceduralPattern（evidence=1）

    Args:
        state, memory, paper_id, strategy_transitions, paper_title, user_messages:
            与 end_session 相同
        llm_call_fn: 异步 LLM 调用函数。签名:
            async (system: str, user: str, max_tokens: int) -> str
            如果为 None，跳过反思步骤（退化为普通 end_session）
        adaptive_config: Optional AdaptiveConfig for evidence-based parameter tuning
            during DeepReflect. If None, config adjustments are skipped.

    Returns:
        反思统计: {"reflections_count": int, "stored_count": int}
    """
    # 先执行常规的 session 结束流程
    end_session(
        state=state,
        memory=memory,
        paper_id=paper_id,
        strategy_transitions=strategy_transitions,
        paper_title=paper_title,
        user_messages=user_messages,
    )

    # Agent 自省
    stats = {"reflections_count": 0, "stored_count": 0}
    if llm_call_fn is not None:
        from core.reflection import SessionReflector

        reflector = SessionReflector(llm_call_fn=llm_call_fn)
        results = await reflector.reflect(state)
        stats["reflections_count"] = len(results)

        if results:
            stored = reflector.persist_reflections(results, memory)
            stats["stored_count"] = stored

    # === V3 Phase 2: Tri-Frequency MetaReflector Integration ===
    from core.godel_config import (
        GODEL_EMERGENCY_REFLECT_ENABLED,
        GODEL_FAST_REFLECT_ENABLED,
        GODEL_DEEP_REFLECT_ENABLED,
    )

    # P2-fix8: 预计算 learned_habits 一次，复用于 Emergency/Deep reflect
    learned_habits: list | None = None

    # Step 3: Emergency check (zero LLM, runs every session)
    if GODEL_EMERGENCY_REFLECT_ENABLED:
        try:
            from core.meta_reflect import EmergencyReflector
            emergency = EmergencyReflector()
            emergency_result = emergency.check(state)
            if emergency_result:
                if learned_habits is None:
                    learned_habits = _get_learned_habits(memory)
                emergency.apply_emergency(emergency_result, memory, learned_habits)
                logger.info(
                    "V3 Emergency reflect triggered: %s",
                    emergency_result.get("reason", "unknown"),
                )
                stats["emergency_triggered"] = True
        except Exception as e:
            logger.warning("V3 Emergency reflect failed (non-fatal): %s", e)

    # Step 4: Fast reflect (zero LLM, every 3 sessions)
    if GODEL_FAST_REFLECT_ENABLED:
        try:
            from core.meta_reflect import FastReflector
            fast = FastReflector()
            if fast.should_trigger(memory):
                alerts = fast.analyze(memory)
                fast.apply(alerts, memory)
                if alerts:
                    logger.info("V3 Fast reflect: %d alerts", len(alerts))
                stats["fast_reflect_alerts"] = len(alerts) if alerts else 0
        except Exception as e:
            logger.warning("V3 Fast reflect failed (non-fatal): %s", e)

    # Step 5: Deep reflect (LLM call, every 10 sessions or anomaly)
    if GODEL_DEEP_REFLECT_ENABLED and llm_call_fn:
        try:
            from core.meta_reflect import DeepReflector
            deep = DeepReflector(llm_call_fn)
            if deep.should_trigger_v3(memory):
                if learned_habits is None:
                    learned_habits = _get_learned_habits(memory)
                context = deep.precompute_context_v3(memory, learned_habits)
                meta_result = await deep.reflect(context)
                if meta_result:
                    report = deep.apply_decisions_v3(
                        meta_result, memory, learned_habits,
                        adaptive_config=adaptive_config,
                    )
                    logger.info("V3 Deep reflect: %s", report)
                    stats["deep_reflect_report"] = report
                    stats["_deep_reflect_raw"] = meta_result  # C1: for metrics export
        except Exception as e:
            logger.warning("V3 Deep reflect failed (non-fatal): %s", e)

    # === C1: Metrics Export (JSON Lines) ===
    try:
        from core.metrics_export import export_all_session_metrics

        # Try to get contrast result from state (set by _analyze_and_persist_contrast)
        contrast_result = getattr(state, "_last_contrast_result", None)

        # Try to get deep reflect raw result
        deep_reflect_raw = stats.get("_deep_reflect_raw", None)

        export_all_session_metrics(
            session_id=paper_id or str(uuid.uuid4())[:8],
            state=state,
            memory=memory,
            reflection_stats=stats,
            contrast_result=contrast_result,
            deep_reflect_result=deep_reflect_raw,
            paper_id=paper_id,
        )
    except Exception as e:
        logger.warning("C1 Metrics export failed (non-fatal): %s", e)

    # Final save (covers reflection + V3 meta-reflect state)
    memory.save()

    return stats


def _extract_edit_strategies(state: WorkspaceState, memory: MemoryStore) -> None:
    """
    [DEPRECATED] 旧的硬编码编辑策略提取。

    已被 core/reflection.py 的 SessionReflector 替代。
    保留此函数签名是为了向后兼容（测试中可能直接调用），但不再被 end_session 使用。

    新设计: Agent 在 session 结束时做 LLM reflection，自己决定学什么。
    Harness 只负责存储和累积验证。
    """
    import warnings
    warnings.warn(
        "_extract_edit_strategies is deprecated. Use SessionReflector.reflect() instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    # 为保持测试兼容，保留基础逻辑但标记来源
    if not state.edits:
        return

    # 最小化兼容逻辑：只做编辑集中度检测（原策略1）
    edit_sections: dict[str, int] = {}
    for edit in state.edits:
        section = edit.get("section", "unknown")
        edit_sections[section] = edit_sections.get(section, 0) + 1

    for section, count in edit_sections.items():
        if count >= 2:
            desc = f"{section} 需要多次编辑({count}次)，可能存在系统性写作问题"
            trigger = f"当 findings 涉及 {section} 时"
            effectiveness = min(0.6 + count * 0.05, 0.9)
            memory.add_or_reinforce_procedure(
                category="edit_strategy",
                description=desc,
                trigger_context=trigger,
                effectiveness_score=effectiveness,
            )

    # 工具偏好和 edit-verify 配对不再硬编码提取
    # 这些判断现在由 Agent 自己在 reflection 中决定


def suggest_new_rules(
    progress_path: str | None = None,
    claude_md_path: str | None = None,
) -> str | None:
    """
    E0: 失败驱动规则生成 — 独立于 Agent 运行时的开发工具。

    扫描 PROGRESS.md 中重复出现的失败模式，与 CLAUDE.md 已有规则对比，
    产出新规则候选的人类可读报告。

    设计决策:
        - 不在 end_session() 中自动调用（触发时机不同）
        - 面向开发者：Phase 结束后手动调用以发现新规则
        - 产出建议供人类决策，不自动写入 CLAUDE.md（§4.3 constrain don't control）

    Usage:
        report = suggest_new_rules()
        if report:
            print(report)

    Returns:
        格式化的报告字符串，如果文件不存在则返回 None
    """
    from pathlib import Path
    from core.rule_extractor import extract_rule_candidates, format_report

    # 默认路径: 从 core/ 推算项目根
    project_root = Path(__file__).parent.parent.parent
    if not progress_path:
        progress_path = str(project_root / "docs" / "PROGRESS.md")
    if not claude_md_path:
        claude_md_path = str(project_root / "CLAUDE.md")

    progress = Path(progress_path)
    claude_md = Path(claude_md_path)

    if not progress.exists():
        return None

    result = extract_rule_candidates(
        progress,
        claude_md if claude_md.exists() else None,
    )
    return format_report(result)


def _record_section_experiences(
    state: WorkspaceState,
    memory: MemoryStore,
    paper_type: str,
) -> None:
    """
    【V3 Phase 1】Record L0 section-level experiences from session metrics.

    Each section the Agent processed generates an experience entry capturing:
    - Turns spent in that section
    - Findings produced while in that section
    - Active habit IDs (from contrast plan or all habits)
    - Token efficiency

    Data sources (priority order):
    1. state.section_metrics — if populated by the loop during session
    2. Derived from state.findings + state.sections_read + tool_call_history

    Gated by GODEL_SECTION_EXPERIENCE_ENABLED kill switch.
    """
    from datetime import datetime, timezone

    # Determine section metrics: use explicit data if available, else derive
    section_metrics = state.section_metrics
    if not section_metrics:
        section_metrics = _derive_section_metrics(state)

    if not section_metrics:
        logger.debug("V3: No section data available, skipping L0 recording")
        return

    # Generate a session_id consistent with build_session_record
    paper_id = MemoryStore.compute_paper_id(state.paper_sections) if state.paper_sections else "unknown"
    session_id = f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}_{paper_id[:8]}"

    contrast_plan = state.contrast_plan
    phase_b_sections = set(contrast_plan.get("phase_b_sections", [])) if contrast_plan else set()
    phase_b_habits = contrast_plan.get("phase_b_habits", []) if contrast_plan else None

    for metric in section_metrics:
        section_name = metric.get("section_name", "unknown")

        # Determine active habits for this section
        if contrast_plan and section_name in phase_b_sections:
            active_habit_ids = phase_b_habits or []
        elif contrast_plan:
            active_habit_ids = contrast_plan.get("phase_a_habits", [])
        else:
            active_habit_ids = []

        tokens_consumed = metric.get("tokens_consumed", 0)
        findings_produced = metric.get("findings_produced", 0)

        exp = {
            "session_id": session_id,
            "section_name": section_name,
            "paper_type": paper_type,
            "turns_spent": metric.get("turns_spent", 0),
            "findings_produced": findings_produced,
            "evidence_chains_built": metric.get("evidence_chains_built", 0),
            "hypotheses_generated": metric.get("hypotheses_generated", 0),
            "active_habit_ids": active_habit_ids,
            "tokens_consumed": tokens_consumed,
            "findings_per_token": (
                findings_produced / max(tokens_consumed, 1)
            ),
        }
        memory.persist_section_experience(exp)

    logger.info(
        "V3: Recorded %d section experiences (L0)", len(section_metrics)
    )


def _derive_section_metrics(state: WorkspaceState) -> list[dict]:
    """
    Derive per-section metrics from existing state data when section_metrics
    was not explicitly populated during the session.

    Strategy:
    - Use state.sections_read as the list of processed sections
    - Count findings per section from state.findings[*]["section"]
    - Estimate turns per section from tool_call_history (read_section calls)
    - Estimate tokens from total_tokens / num_sections (rough approximation)
    - Count evidence chains from state.evidence_chains
    """
    if not state.sections_read:
        return []

    from collections import Counter

    # Count findings per section
    findings_per_section: Counter = Counter()
    for f in state.findings:
        sec = f.get("section", "").lower().strip()
        if sec:
            # Match against sections_read (case-insensitive)
            for read_sec in state.sections_read:
                if sec in read_sec.lower() or read_sec.lower() in sec:
                    findings_per_section[read_sec] += 1
                    break
            else:
                findings_per_section[sec] += 1

    # Count evidence chains per section (from finding_id → section mapping)
    chains_per_section: Counter = Counter()
    for finding_id, chain_steps in state.evidence_chains.items():
        # Try to map finding_id to a section via findings list
        for f in state.findings:
            if f.get("id") == finding_id or f.get("finding", "")[:20] in finding_id:
                sec = f.get("section", "")
                if sec:
                    chains_per_section[sec] += 1
                break

    # Estimate turns and tokens (distribute evenly across read sections)
    num_sections = len(state.sections_read)
    tokens_per_section = state.total_tokens // max(num_sections, 1)

    # Count read_section tool calls per section for turns_spent estimation
    reads_per_section: Counter = Counter()
    for tc in state.tool_call_history:
        if tc.get("name") == "read_section":
            sec_arg = tc.get("input", {}).get("section", "")
            if sec_arg:
                reads_per_section[sec_arg.lower()] += 1

    metrics = []
    for section_name in state.sections_read:
        sec_lower = section_name.lower()
        # turns_spent: at minimum 1 (for the read), plus related tool calls
        turns_spent = max(reads_per_section.get(sec_lower, 1), 1)

        metrics.append({
            "section_name": section_name,
            "turns_spent": turns_spent,
            "findings_produced": findings_per_section.get(section_name, 0),
            "evidence_chains_built": chains_per_section.get(section_name, 0),
            "hypotheses_generated": 0,  # Cannot derive reliably
            "tokens_consumed": tokens_per_section,
        })

    return metrics


def _record_session_experience_v3(
    state: WorkspaceState,
    memory: MemoryStore,
    paper_type: str,
) -> None:
    """
    【V3 Phase 1】Record L1 session-level experience with V3 enhanced fields.

    Aggregates section metrics into a single session experience including:
    - PCG coverage metrics
    - Phase A/B split info (if contrast active)
    - Token efficiency relative to historical baseline

    Gated by GODEL_SECTION_EXPERIENCE_ENABLED kill switch.
    """
    from datetime import datetime, timezone

    paper_id = MemoryStore.compute_paper_id(state.paper_sections) if state.paper_sections else "unknown"
    session_id = f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}_{paper_id[:8]}"

    total_findings = len(state.findings)
    total_tokens = state.total_tokens or 1

    # PCG coverage
    pcg_coverage = 0.0
    if state.paper_cognition_graph:
        pcg = state.paper_cognition_graph
        if hasattr(pcg, "get_coverage"):
            pcg_coverage = pcg.get_coverage()
        elif hasattr(pcg, "coverage"):
            pcg_coverage = pcg.coverage

    # Contrast info
    contrast_plan = state.contrast_plan
    has_contrast = contrast_plan is not None

    exp = {
        "session_id": session_id,
        "paper_type": paper_type,
        "paper_id": paper_id,
        "findings_count": total_findings,
        "total_tokens": total_tokens,
        "loop_turns": state.loop_turns,
        "findings_per_1k_tokens": total_findings / max(total_tokens / 1000, 0.1),
        "pcg_coverage": pcg_coverage,
        "has_contrast": has_contrast,
        "contrast_target_habit": (
            contrast_plan.get("target_habit_id") if contrast_plan else None
        ),
        "sections_processed": len(state.sections_read),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    memory.persist_session_experience_v3(exp)
    logger.info(
        "V3: Recorded session experience (L1): findings=%d, tokens=%d, pcg=%.2f",
        total_findings, total_tokens, pcg_coverage,
    )


def _analyze_and_persist_contrast(
    state: WorkspaceState,
    memory: MemoryStore,
) -> None:
    """
    【V3 Phase 1】Run IntraSession contrast analysis and persist results.

    Uses section-level metrics from this session + the contrast plan to determine
    if the target habit actually contributed to review quality.

    Gated by GODEL_INTRA_CONTRAST_ENABLED kill switch.
    """
    contrast_plan = state.contrast_plan
    if not contrast_plan:
        logger.debug("V3: No contrast plan active, skipping analysis")
        return

    # Get section metrics: explicit or derived
    section_metrics = state.section_metrics
    if not section_metrics:
        section_metrics = _derive_section_metrics(state)

    if not section_metrics:
        logger.debug("V3: No section metrics available, skipping contrast analysis")
        return

    from core.evolution import IntraSessionContrastManager

    manager = IntraSessionContrastManager()
    result = manager.analyze_contrast(
        section_experiences=section_metrics,
        plan=contrast_plan,
    )

    if result:
        memory.persist_contrast_result(result)
        # C1: Store for metrics export (picked up by end_session_with_reflection)
        state._last_contrast_result = result
        recommendation = result.get("recommendation", "unknown")
        logger.info(
            "V3: Contrast analysis complete: target=%s, recommendation=%s",
            contrast_plan.get("target_habit_id", "unknown"),
            recommendation,
        )


def _get_learned_habits(memory: MemoryStore) -> list:
    """Retrieve learned habits from evolution engine for reflector use.

    Returns list of LearnedHabit objects. Falls back to empty list on failure.
    """
    try:
        from core.evolution import HabitLearner
        learner = HabitLearner(memory=memory)
        return learner.learn()
    except Exception:
        return []


def _infer_paper_title(state: WorkspaceState, paper_id: str) -> str:
    """从 paper_sections 推断论文标题。"""
    for key in state.paper_sections:
        if "title" in key.lower() or "abstract" in key.lower():
            content = state.paper_sections[key]
            for line in content.split("\n"):
                line = line.strip().strip("#").strip()
                if line and len(line) > 10:
                    return line[:100]
    return f"Paper_{paper_id[:8]}"
