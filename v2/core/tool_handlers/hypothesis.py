"""tool_handlers/hypothesis.py — HD-WM 假说工具的执行逻辑。

提取自 Harness._tool_generate_hypothesis, _tool_add_evidence, _tool_resolve_hypothesis。
"""
from __future__ import annotations

from typing import Any


# ============================================================
# tool_generate_hypothesis
# ============================================================

def tool_generate_hypothesis(args: dict, state: Any, hypothesis_module: Any) -> str:
    """产生一个可验证的学术假说。"""
    if hypothesis_module is None:
        return "[HD-WM 未激活] 当前未启用假说驱动工作记忆。"

    statement = args.get("statement", "").strip()
    source = args.get("source", "").strip()
    if not statement:
        return "generate_hypothesis 需要 statement 参数（假说陈述）。"
    if not source:
        source = "unknown"

    hyp = hypothesis_module.generate(
        statement=statement,
        source=source,
        turn=state.loop_turns,
    )
    return (
        f"假说已生成: [{hyp.id}] {hyp.statement}\n"
        f"来源 section: {hyp.source} | 状态: {hyp.status.value}\n"
        f"当前活跃假说数: {len(hypothesis_module.active_hypotheses)}"
    )


# ============================================================
# tool_add_evidence
# ============================================================

def tool_add_evidence(args: dict, state: Any, hypothesis_module: Any) -> str:
    """为某个假说添加支持或反对的证据。"""
    if hypothesis_module is None:
        return "[HD-WM 未激活] 当前未启用假说驱动工作记忆。"

    hyp_id = args.get("hyp_id", "").strip()
    content = args.get("content", "").strip()
    direction = args.get("direction", "").strip()
    strength = args.get("strength", 0.5)

    if not hyp_id:
        return "add_evidence 需要 hyp_id 参数。"
    if not content:
        return "add_evidence 需要 content 参数（证据内容）。"
    if direction not in ("for", "against"):
        return "add_evidence 的 direction 必须是 'for' 或 'against'。"

    try:
        strength = float(strength)
    except (TypeError, ValueError):
        strength = 0.5

    evidence = hypothesis_module.add_evidence(
        hyp_id=hyp_id,
        content=content,
        direction=direction,
        strength=strength,
        source=args.get("source", ""),
        evidence_type=args.get("type", "direct"),
        turn=state.loop_turns,
    )
    if evidence is None:
        return f"添加证据失败: 假说 {hyp_id} 不存在或已解决。"

    hyp = hypothesis_module.get_hypothesis(hyp_id)
    balance_desc = ""
    if hyp:
        b = hyp.evidence_balance
        balance_desc = f" | 证据平衡: {b:+.2f}"

    return (
        f"证据已添加到 [{hyp_id}]: {direction} (强度 {strength:.1f})\n"
        f"证据内容: {content[:100]}\n"
        f"当前证据: +{len(hyp.evidence_for)}/-{len(hyp.evidence_against)}{balance_desc}"
    )


# ============================================================
# tool_resolve_hypothesis
# ============================================================

def tool_resolve_hypothesis(args: dict, state: Any, hypothesis_module: Any) -> str:
    """解决一个假说——标记为 supported/refuted/suspended。"""
    if hypothesis_module is None:
        return "[HD-WM 未激活] 当前未启用假说驱动工作记忆。"

    hyp_id = args.get("hyp_id", "").strip()
    status = args.get("status", "").strip()
    reason = args.get("reason", "").strip()

    if not hyp_id:
        return "resolve_hypothesis 需要 hyp_id 参数。"
    if status not in ("supported", "refuted", "suspended"):
        return "resolve_hypothesis 的 status 必须是 'supported'、'refuted' 或 'suspended'。"

    success = hypothesis_module.resolve(
        hyp_id=hyp_id,
        status=status,
        reason=reason,
        turn=state.loop_turns,
    )
    if not success:
        return f"解决假说失败: {hyp_id} 不存在或已解决。"

    readiness = hypothesis_module.review_readiness
    return (
        f"假说 [{hyp_id}] 已解决 → {status}\n"
        f"理由: {reason}\n"
        f"审稿完成度: {readiness:.0%} | "
        f"解决率: {hypothesis_module.resolution_rate:.0%}"
    )
