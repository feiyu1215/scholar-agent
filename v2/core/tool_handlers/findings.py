"""tool_handlers/findings.py — 发现管理类工具的执行逻辑。

提取自 Harness._tool_update_findings, _hdwm_auto_enhance,
_check_verification_integrity, _hdwm_match_and_resolve,
_check_finding_overlap, _tool_review_findings。
"""
from __future__ import annotations

import re
from typing import Any

from core.text_utils import extract_terms


# ============================================================
# tool_update_findings
# ============================================================

def _jaccard_word_overlap(text_a: str, text_b: str) -> float:
    """Compute Jaccard similarity on word sets (simple, fast duplicate check)."""
    words_a = set(text_a.lower().split())
    words_b = set(text_b.lower().split())
    if not words_a or not words_b:
        return 0.0
    intersection = words_a & words_b
    union = words_a | words_b
    return len(intersection) / len(union) if union else 0.0


def tool_update_findings(args: dict, state: Any, enable_hdwm: bool, hypothesis_module: Any) -> str:
    """记录一条审稿发现。"""
    finding = {
        "finding": args["finding"],
        "priority": args.get("priority", "medium"),
        "status": args.get("status", "suggestion"),
        "evidence": args.get("evidence", ""),
        "section": args.get("section", ""),
        "recorded_at_turn": state.loop_turns,
    }

    # P3 #20: fast Jaccard duplicate check before detailed overlap analysis
    # 注意：如果 new status 比 existing status 更高级，属于"状态升级"场景，
    # 应放行到 check_finding_overlap 执行完整的升级逻辑（含 HD-WM 完整性校验）。
    _STATUS_PRIORITY = {"needs_verification": 0, "suggestion": 1, "verified": 2}
    new_text = args["finding"]
    new_status_prio = _STATUS_PRIORITY.get(finding["status"], 1)
    for existing in state.findings:
        if _jaccard_word_overlap(new_text, existing.get("finding", "")) > 0.7:
            old_status_prio = _STATUS_PRIORITY.get(existing.get("status", "suggestion"), 1)
            if new_status_prio > old_status_prio:
                # 状态升级意图——不拦截，交给 check_finding_overlap 处理
                break
            return (
                f"⚠️ 未记录：与已有发现高度重复（Jaccard > 0.7）。"
                f" (当前仍为 {len(state.findings)} 条)"
            )

    # Phase 47: 前置去重检查
    if state.findings:
        overlap_warning = check_finding_overlap(finding, state, enable_hdwm, hypothesis_module)
        if overlap_warning:
            return overlap_warning

    state.findings.append(finding)
    finding_idx = len(state.findings) - 1
    evidence_note = f" (含原文证据, 来自 '{finding['section']}')" if finding['evidence'] else ""
    base_msg = f"已记录发现{evidence_note} (当前共 {len(state.findings)} 条)"

    # === S3: ReviewChecklist 自动匹配 ===
    if hasattr(state, 'review_checklist') and not state.review_checklist.is_empty():
        finding_text = finding.get("finding", "") + " " + finding.get("evidence", "")
        matched = state.review_checklist.try_match_finding(finding_idx, finding_text)
        if matched:
            base_msg += f"\n[Checklist] 覆盖审查维度: {len(matched)} 项"

    # === Phase 10: HD-WM 自动增强层 ===
    hdwm_note = hdwm_auto_enhance(finding, state, enable_hdwm, hypothesis_module)
    if hdwm_note:
        base_msg += f"\n{hdwm_note}"

    return base_msg


# ============================================================
# hdwm_auto_enhance
# ============================================================

def hdwm_auto_enhance(finding: dict, state: Any, enable_hdwm: bool, hypothesis_module: Any) -> str:
    """
    Phase 10: HD-WM 自动增强——在 update_findings 路径上自动维护假说生命周期。

    规则:
    1. status=needs_verification → 自动 generate_hypothesis
    2. status=verified/suggestion + 与已有假说匹配 → 自动 add_evidence + resolve
    3. HD-WM 未启用时静默返回空字符串（零副作用）
    """
    if not enable_hdwm or hypothesis_module is None:
        return ""

    status = finding.get("status", "suggestion")
    statement = finding.get("finding", "")
    source = finding.get("section", "unknown") or "unknown"

    # --- 规则 1: needs_verification → 自动生成假说 ---
    if status == "needs_verification":
        hyp = hypothesis_module.generate(
            statement=statement,
            source=source,
            turn=state.loop_turns,
        )
        finding["_hdwm_hyp_id"] = hyp.id
        return (
            f"[HD-WM] 自动跟踪待验证判断 → 假说 [{hyp.id}] "
            f"(活跃假说: {len(hypothesis_module.active_hypotheses)})"
        )

    # --- 规则 2: verified → 尝试匹配并解决之前的假说 ---
    if status == "verified":
        # Phase 11: Verification Integrity Constraint
        integrity_issue = check_verification_integrity(finding, state, hypothesis_module)
        if integrity_issue:
            return integrity_issue

        matched_hyp = hdwm_match_and_resolve(finding, state, hypothesis_module)
        if matched_hyp:
            return (
                f"[HD-WM] 验证完成 → 假说 [{matched_hyp.id}] 已确认 (supported) "
                f"| 审稿完成度: {hypothesis_module.review_readiness:.0%}"
            )

    # --- 规则 2b: suggestion + 高优先级 + 有证据 → 也尝试匹配解决 ---
    if status == "suggestion" and finding.get("priority") == "high" and finding.get("evidence"):
        matched_hyp = hdwm_match_and_resolve(finding, state, hypothesis_module)
        if matched_hyp:
            return (
                f"[HD-WM] 高优发现有充分证据 → 假说 [{matched_hyp.id}] 已确认 "
                f"| 审稿完成度: {hypothesis_module.review_readiness:.0%}"
            )

    return ""


# ============================================================
# check_verification_integrity
# ============================================================

def check_verification_integrity(finding: dict, state: Any, hypothesis_module: Any) -> str:
    """
    Phase 11: Verification Integrity Constraint

    当 Agent 提交 status=verified 且该 finding 匹配一个之前的 needs_verification 假说时，
    检查 Agent 在假说创建之后是否实际执行了调查性行为。
    """
    if hypothesis_module is None:
        return ""

    # 找到对应的假说
    hyp_id = finding.get("_hdwm_hyp_id", "")
    target_hyp = None

    if hyp_id:
        target_hyp = hypothesis_module.get_hypothesis(hyp_id)
    else:
        statement = finding.get("finding", "")
        active_hyps = hypothesis_module.active_hypotheses
        if not active_hyps:
            return ""

        finding_terms = extract_terms(statement, include_cjk=False, extended_stopwords=False)
        if len(finding_terms) >= 3:
            best_match = None
            best_overlap = 0.0
            for hyp in active_hyps:
                hyp_terms = extract_terms(hyp.statement, include_cjk=False, extended_stopwords=False)
                if len(hyp_terms) < 3:
                    continue
                intersection = finding_terms & hyp_terms
                overlap = len(intersection) / min(len(finding_terms), len(hyp_terms))
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_match = hyp
            if best_match and best_overlap >= 0.60:
                target_hyp = best_match

    if target_hyp is None or target_hyp.is_resolved:
        return ""  # 没有匹配的活跃假说，放行

    # 检查 tool_call_history 中，在假说创建之后是否有调查性行为
    investigative_tools = {"read_section", "search_literature"}
    history = state.tool_call_history

    hyp_creation_idx = -1
    for i, call in enumerate(history):
        if call.get("name") == "update_findings":
            call_input = call.get("input", {})
            if (call_input.get("status") == "needs_verification" and
                call_input.get("finding", "")[:50] == target_hyp.statement[:50]):
                hyp_creation_idx = i
                break

    if hyp_creation_idx < 0:
        return ""

    subsequent_calls = history[hyp_creation_idx + 1:]
    has_investigation = any(
        call.get("name") in investigative_tools
        for call in subsequent_calls
    )

    if has_investigation:
        return ""

    return (
        f"[HD-WM 完整性提示] 你将「{target_hyp.statement[:60]}」标记为 verified，"
        f"但自假说创建以来尚未观察到 read_section 或 search_literature 调用。"
        f"建议先追查原文证据再确认验证状态。"
        f"（finding 已正常记录，但假说暂不自动 resolve）"
    )


# ============================================================
# hdwm_match_and_resolve
# ============================================================

def hdwm_match_and_resolve(finding: dict, state: Any, hypothesis_module: Any):
    """
    尝试将一条 finding 与已有的活跃假说匹配。
    匹配成功后自动 add_evidence + resolve。

    Returns: matched Hypothesis or None
    """
    if hypothesis_module is None:
        return None

    active_hyps = hypothesis_module.active_hypotheses
    if not active_hyps:
        return None

    statement = finding.get("finding", "")
    evidence_text = finding.get("evidence", "")

    # --- 策略 1: 精确匹配（通过 _hdwm_hyp_id） ---
    hyp_id = finding.get("_hdwm_hyp_id", "")
    if hyp_id:
        hyp = hypothesis_module.get_hypothesis(hyp_id)
        if hyp and not hyp.is_resolved:
            if evidence_text:
                hypothesis_module.add_evidence(
                    hyp_id=hyp.id,
                    content=evidence_text[:200],
                    direction="for",
                    strength=0.8,
                    source=finding.get("section", ""),
                    turn=state.loop_turns,
                )
            hypothesis_module.resolve(
                hyp_id=hyp.id,
                status="supported",
                reason=f"Finding verified: {statement[:80]}",
                turn=state.loop_turns,
            )
            return hyp

    # --- 策略 2: 模糊匹配（关键词重叠） ---
    finding_terms = extract_terms(statement, include_cjk=False, extended_stopwords=False)
    if len(finding_terms) < 3:
        return None

    best_match = None
    best_overlap = 0.0

    for hyp in active_hyps:
        hyp_terms = extract_terms(hyp.statement, include_cjk=False, extended_stopwords=False)
        if len(hyp_terms) < 3:
            continue
        intersection = finding_terms & hyp_terms
        overlap = len(intersection) / min(len(finding_terms), len(hyp_terms))
        if overlap > best_overlap:
            best_overlap = overlap
            best_match = hyp

    if best_match and best_overlap >= 0.60:
        if evidence_text:
            hypothesis_module.add_evidence(
                hyp_id=best_match.id,
                content=evidence_text[:200],
                direction="for",
                strength=0.7,
                source=finding.get("section", ""),
                turn=state.loop_turns,
            )
        hypothesis_module.resolve(
            hyp_id=best_match.id,
            status="supported",
            reason=f"Fuzzy-matched finding verified (overlap={best_overlap:.0%}): {statement[:80]}",
            turn=state.loop_turns,
        )
        return best_match

    return None


# ============================================================
# check_finding_overlap
# ============================================================

def check_finding_overlap(new_finding: dict, state: Any, enable_hdwm: bool, hypothesis_module: Any) -> str | None:
    """
    Phase P1: 检查新 finding 是否与已有 findings 高度重叠。

    去重策略（多信号融合，支持中英文混合）：
    1. 术语重叠 >= 70%（英文+CJK关键词匹配）
    2. 术语重叠 >= 60% + 数字/表格引用重叠 >= 1
    3. 术语重叠 >= 50% + 同 section + 数字/表格引用重叠 >= 1

    行为变化：
    - 状态升级（needs_verification → verified）时：更新原记录，不追加新记录
    - 同一问题补充证据时：追加证据到原记录，不创建新记录
    """

    def _extract_numeric_refs(text: str) -> set[str]:
        """提取数字引用信号：表格编号、具体数值、方程编号等。"""
        refs = set()
        # 表格/图引用：Table 1, Figure 2, Eq. 3
        for m in re.finditer(r'(?:table|figure|fig|eq|equation)\s*\.?\s*(\d+)', text.lower()):
            refs.add(f"ref_{m.group(0).strip()}")
        # 显著数值（小数点数字，如 0.067, 6.2%, 3.2pp）
        for m in re.finditer(r'(\d+\.?\d*)\s*(?:%|pp|percentage)', text.lower()):
            refs.add(f"num_{m.group(1)}")
        for m in re.finditer(r'(?<!\d)0\.\d{2,}', text):
            refs.add(f"num_{m.group(0)}")
        return refs

    new_terms = extract_terms(new_finding["finding"])
    new_nums = _extract_numeric_refs(new_finding["finding"])
    new_section = new_finding.get("section", "").lower().strip()

    if len(new_terms) < 3:
        return None

    for i, existing in enumerate(state.findings):
        existing_terms = extract_terms(existing.get("finding", ""))
        if len(existing_terms) < 3:
            continue

        # --- 多信号计算 ---
        intersection = new_terms & existing_terms
        term_overlap = len(intersection) / min(len(new_terms), len(existing_terms))

        existing_nums = _extract_numeric_refs(existing.get("finding", ""))
        num_overlap = len(new_nums & existing_nums) if (new_nums and existing_nums) else 0

        same_section = (new_section and new_section == existing.get("section", "").lower().strip())

        # --- 判定是否为同一问题 ---
        is_duplicate = False
        if term_overlap >= 0.70:
            is_duplicate = True
        elif term_overlap >= 0.60 and num_overlap >= 1:
            # 术语中等重叠 + 引用了相同数字/表格 → 高度可能是同一问题
            is_duplicate = True
        elif term_overlap >= 0.50 and same_section and num_overlap >= 1:
            # 同一 section + 一定术语重叠 + 相同数字 → 几乎确定是同一问题
            is_duplicate = True

        if not is_duplicate:
            continue

        # --- 处理重复 ---
        new_status = new_finding.get("status", "suggestion")
        old_status = existing.get("status", "suggestion")
        new_evidence = new_finding.get("evidence", "")
        old_evidence = existing.get("evidence", "")

        # 状态优先级
        status_priority = {"needs_verification": 0, "suggestion": 1, "verified": 2}
        new_prio = status_priority.get(new_status, 1)
        old_prio = status_priority.get(old_status, 1)

        if new_prio > old_prio:
            # --- 状态升级：原地更新 ---
            # HD-WM 联动（先检查完整性，再决定是否更新状态）
            hdwm_note = ""
            hyp_id = existing.get("_hdwm_hyp_id") or new_finding.get("_hdwm_hyp_id")
            if hyp_id and new_status == "verified":
                new_finding["_hdwm_hyp_id"] = hyp_id
                existing["_hdwm_hyp_id"] = hyp_id
                hdwm_note = hdwm_auto_enhance(new_finding, state, enable_hdwm, hypothesis_module)
                # 如果完整性检查失败（返回了完整性提示），不更新 status
                if hdwm_note and "完整性提示" in hdwm_note:
                    msg = (
                        f"✓ 检测到与已有发现 #{i+1} 的重叠度 {term_overlap:.0%}。"
                        f"\n{hdwm_note}"
                        f" (当前仍为 {len(state.findings)} 条)"
                    )
                    return msg

            # 完整性通过或无 HD-WM：正式更新状态
            existing["status"] = new_status
            if new_evidence and new_evidence != old_evidence:
                if old_evidence:
                    existing["evidence"] = old_evidence + " | " + new_evidence
                else:
                    existing["evidence"] = new_evidence

            msg = (
                f"✓ 已更新发现 #{i+1} 的状态: {old_status} → {new_status}"
                f" (检测到与已有发现的重叠度 {term_overlap:.0%}"
                f"{f', 共同引用 {num_overlap} 个数值/表格' if num_overlap else ''})"
                f" (当前仍为 {len(state.findings)} 条)"
            )
            if new_evidence and new_evidence != old_evidence:
                msg += "\n  新证据已追加到原记录。"
            if hdwm_note:
                msg += f"\n{hdwm_note}"
            return msg

        elif new_prio == old_prio:
            # --- 同状态重复 ---
            if new_evidence and new_evidence != old_evidence and not old_evidence:
                # 新的有证据，旧的没有：补充证据到原记录
                existing["evidence"] = new_evidence
                return (
                    f"✓ 已为发现 #{i+1} 补充证据（检测到重复，未创建新记录）。"
                    f" (当前仍为 {len(state.findings)} 条)"
                )
            elif new_evidence and old_evidence and new_evidence != old_evidence:
                # 都有证据但不同：追加
                existing["evidence"] = old_evidence + " | " + new_evidence
                return (
                    f"✓ 已为发现 #{i+1} 追加额外证据（检测到重复，未创建新记录）。"
                    f" (当前仍为 {len(state.findings)} 条)"
                )
            else:
                # 纯重复，不追加
                return (
                    f"⚠️ 未记录：这条发现与已有发现 #{i+1} 高度重叠 "
                    f"(术语重合 {term_overlap:.0%}"
                    f"{f', 共同引用 {num_overlap} 个数值/表格' if num_overlap else ''})。"
                    f"重复的发现不增加审稿价值。"
                    f"如果你想补充新维度的判断，请确保措辞体现与已有发现的区别。"
                    f" (当前仍为 {len(state.findings)} 条)"
                )

        else:
            # --- 状态降级（罕见） ---
            return (
                f"⚠️ 未记录：已有发现 #{i+1} 状态更高 ({old_status})，"
                f"当前提交为 {new_status}。如果需要撤销之前的验证结论，请显式说明。"
                f" (当前仍为 {len(state.findings)} 条)"
            )

    return None


# ============================================================
# tool_review_findings
# ============================================================

def tool_review_findings(args: dict, state: Any) -> str:
    """回顾已有发现，支持按过滤器查看。"""
    filter_type = args.get("filter", "all")
    findings = state.findings

    if not findings:
        return "当前没有任何发现记录。"

    # 过滤
    if filter_type == "high":
        filtered = [f for f in findings if f.get("priority") == "high"]
    elif filter_type == "needs_verification":
        filtered = [f for f in findings if f.get("status") == "needs_verification"]
    elif filter_type == "verified":
        filtered = [f for f in findings if f.get("status") == "verified"]
    else:
        filtered = findings

    if not filtered:
        return f"按 filter='{filter_type}' 筛选后无匹配项。全部 {len(findings)} 条发现中: " + \
               f"high={sum(1 for f in findings if f.get('priority')=='high')}, " + \
               f"needs_verification={sum(1 for f in findings if f.get('status')=='needs_verification')}。"

    lines = [f"发现回顾 (filter='{filter_type}', 共 {len(filtered)}/{len(findings)} 条):"]
    lines.append("=" * 60)
    for i, f in enumerate(filtered, 1):
        icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(f["priority"], "⚪")
        status_label = {"verified": "✓已验证", "needs_verification": "?待验证", "suggestion": "→建议"}.get(f["status"], f["status"])
        lines.append(f"\n[{i}] {icon} [{status_label}] {f['finding']}")
        if f.get("section"):
            lines.append(f"    📍 出处: {f['section']}")
        if f.get("evidence"):
            ev = f['evidence']
            if len(ev) > 300:
                ev = ev[:300] + "..."
            lines.append(f"    📄 原文证据: \"{ev}\"")
        else:
            lines.append(f"    ⚠️ 无原文证据 — 建议重新查阅 section 补充")
    lines.append("\n" + "=" * 60)
    lines.append(f"提示: 对 '待验证' 的发现，可 read_section 重读原文核实，再 update_findings 更新状态。")
    return "\n".join(lines)
