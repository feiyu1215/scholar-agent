"""tool_handlers/editing.py — 编辑类工具的执行逻辑。

提取自 Harness._tool_generate_edit_plan, _resolve_section_key,
_run_edit_verification, _record_edit, _tool_edit_paragraph,
_tool_reword_sentence, _tool_insert_content, _tool_edit_section。
"""
from __future__ import annotations

from typing import Any

from core.state import EditPlan, EditStep
from core.post_edit_verify import verify_edit, format_verification_feedback
from core.edit_plan_validator import validate_edit_plan, format_validation_nudge


# EDIT-5 常量
_MAX_EDIT_RETRIES = 3


# ============================================================
# 辅助函数
# ============================================================

def resolve_section_key(section: str, paper_sections: dict) -> str | None:
    """模糊匹配 section 名，返回 paper_sections 中的实际 key。"""
    for key in paper_sections:
        if section.lower() in key.lower() or key.lower() in section.lower():
            return key
    return None


def run_edit_verification(section_key: str, old_text: str, new_text: str, reason: str,
                          state: Any, checker: Any) -> str:
    """编辑后验证 + EDIT-5 迭代修正闭环。"""
    all_text = "\n\n".join(state.paper_sections.values())
    verification = verify_edit(
        section_name=section_key,
        old_text=old_text,
        new_text=new_text,
        all_sections_text=all_text,
        voice_profile=state.voice_profile,
    )
    feedback = format_verification_feedback(verification, section_key)

    # Phase 50: 小模型快速校验
    checker_warning = checker.check_edit(new_text, reason)
    if checker_warning:
        feedback += checker_warning

    # ----------------------------------------------------------
    # EDIT-5: 三级反馈闭环
    # ----------------------------------------------------------
    if verification.passed and not verification.warnings:
        # PASS
        state.edit_retry_counts.pop(section_key, None)
        feedback += "\n\n[EDIT-PASS] 本次编辑验证通过，可继续下一步。"

    elif verification.passed and verification.warnings:
        # WARN
        feedback += (
            "\n\n[EDIT-WARN] 编辑已应用但有风格漂移警告。"
            "你可以选择调整以更贴近原文风格，也可以保持当前修改继续推进——这不是错误。"
        )

    else:
        # FAIL
        retry_count = state.edit_retry_counts.get(section_key, 0) + 1
        state.edit_retry_counts[section_key] = retry_count

        if retry_count >= _MAX_EDIT_RETRIES:
            feedback += (
                f"\n\n[EDIT-FAIL] 本次编辑引入了结构问题（第 {retry_count} 次失败）。"
                f"已达到最大重试次数（{_MAX_EDIT_RETRIES}），建议标记为「需人工介入」并跳过此步骤。"
                f"你可以使用 talk_to_user 告知用户这里需要手动检查。"
            )
        else:
            remaining = _MAX_EDIT_RETRIES - retry_count
            feedback += (
                f"\n\n[EDIT-FAIL] 本次编辑引入了结构问题（第 {retry_count} 次失败，还可重试 {remaining} 次）。"
                f"请检查上述问题并重新编辑此 section 以修复。如果无法修复，可跳过。"
            )

    return feedback


def record_edit(state: Any, section: str, reason: str, content_preview: str) -> None:
    """记录编辑到 state.edits。"""
    state.edits.append({
        "section": section,
        "reason": reason,
        "content_preview": content_preview[:200] + "..." if len(content_preview) > 200 else content_preview,
    })


# ============================================================
# tool_generate_edit_plan
# ============================================================

def tool_generate_edit_plan(args: dict, state: Any) -> str:
    """根据 findings 生成结构化修改计划，存入 state.edit_plan。"""
    steps_raw = args.get("steps", [])
    estimated_scope = args.get("estimated_scope", "局部措辞")
    rationale = args.get("rationale", "")

    if not steps_raw:
        return "[计划生成失败] steps 不能为空。请至少提供一个修改步骤。"

    # 校验 findings 引用是否合法
    max_finding_idx = len(state.findings) - 1
    all_finding_ids: list[int] = []

    # 构建 EditStep 列表
    edit_steps: list[EditStep] = []
    validation_warnings: list[str] = []

    for i, step_raw in enumerate(steps_raw):
        target_section = step_raw.get("target_section", "")
        action = step_raw.get("action", "reword")
        description = step_raw.get("description", "")
        priority = step_raw.get("priority", "should")
        finding_ids = step_raw.get("finding_ids", [])

        if not target_section:
            validation_warnings.append(f"步骤 {i+1}: target_section 为空")
            continue
        if not description:
            validation_warnings.append(f"步骤 {i+1}: description 为空")
            continue

        valid_actions = {"reword", "restructure", "add_content", "remove", "verify_data"}
        if action not in valid_actions:
            validation_warnings.append(
                f"步骤 {i+1}: action '{action}' 无效，"
                f"可选: {', '.join(sorted(valid_actions))}。该步骤已跳过。"
            )
            continue

        valid_priorities = {"must", "should", "could"}
        if priority not in valid_priorities:
            priority = "should"

        valid_fids: list[int] = []
        for fid in finding_ids:
            if isinstance(fid, int) and 0 <= fid <= max_finding_idx:
                valid_fids.append(fid)
                all_finding_ids.append(fid)
            elif max_finding_idx >= 0:
                validation_warnings.append(
                    f"步骤 {i+1}: finding_id {fid} 越界（当前 findings 范围 0~{max_finding_idx}）"
                )

        edit_steps.append(EditStep(
            target_section=target_section,
            action=action,
            description=description,
            requires=step_raw.get("requires", []),
            priority=priority,
            status="pending",
            finding_ids=valid_fids,
        ))

    if not edit_steps:
        return "[计划生成失败] 所有步骤均校验失败。请检查参数格式。"

    # 存入 state
    plan = EditPlan(
        steps=edit_steps,
        source_finding_ids=sorted(set(all_finding_ids)),
        estimated_scope=estimated_scope,
        rationale=rationale,
    )
    state.edit_plan = plan

    # 构建反馈
    summary_lines = [
        f"✓ 修改计划已生成（{len(edit_steps)} 步）",
        f"  范围评估: {estimated_scope}",
        f"  关联 findings: {len(set(all_finding_ids))} 条",
        "",
        "步骤概览:",
    ]
    for i, step in enumerate(edit_steps):
        fid_str = f" ← findings[{','.join(str(x) for x in step.finding_ids)}]" if step.finding_ids else ""
        summary_lines.append(
            f"  {i+1}. [{step.priority}] {step.action} @ {step.target_section}: "
            f"{step.description[:80]}{fid_str}"
        )

    if validation_warnings:
        summary_lines.append("")
        summary_lines.append(f"⚠️ {len(validation_warnings)} 条警告:")
        for w in validation_warnings[:5]:
            summary_lines.append(f"  - {w}")

    # EDIT-2: 计划验证器
    plan_validation = validate_edit_plan(plan, state)
    validation_nudge = format_validation_nudge(plan_validation)

    return "\n".join(summary_lines) + validation_nudge


# ============================================================
# tool_edit_paragraph
# ============================================================

def tool_edit_paragraph(args: dict, state: Any, checker: Any) -> str:
    """替换指定 section 中的某个段落。"""
    section = args.get("section", "")
    paragraph_index = args.get("paragraph_index", -1)
    new_content = args.get("new_content", "")
    reason = args.get("reason", "")

    if not section:
        return "[edit_paragraph 失败] section 参数不能为空。"
    if not new_content:
        return "[edit_paragraph 失败] new_content 参数不能为空。"
    if not isinstance(paragraph_index, int) or paragraph_index < 0:
        return "[edit_paragraph 失败] paragraph_index 必须是非负整数。"

    key = resolve_section_key(section, state.paper_sections)
    if key is None:
        return f"[edit_paragraph 失败] 未找到 section '{section}'。"

    old_section_text = state.paper_sections[key]
    paragraphs = old_section_text.split("\n\n")

    if paragraph_index >= len(paragraphs):
        return (
            f"[edit_paragraph 失败] section '{key}' 共 {len(paragraphs)} 个段落"
            f"（索引 0~{len(paragraphs)-1}），但你指定了 paragraph_index={paragraph_index}。"
        )

    # 执行替换
    old_paragraph = paragraphs[paragraph_index]
    paragraphs[paragraph_index] = new_content
    new_section_text = "\n\n".join(paragraphs)
    state.paper_sections[key] = new_section_text

    # 记录 + 验证
    record_edit(state, key, reason, new_content)
    feedback = run_edit_verification(key, old_section_text, new_section_text, reason, state, checker)

    return (
        f"已替换 section '{key}' 第 {paragraph_index} 段（原因: {reason}）\n"
        f"原段落: {old_paragraph[:100]}{'...' if len(old_paragraph) > 100 else ''}\n\n"
        f"{feedback}"
    )


# ============================================================
# tool_reword_sentence
# ============================================================

def tool_reword_sentence(args: dict, state: Any, checker: Any) -> str:
    """精确匹配并替换一个句子。"""
    section = args.get("section", "")
    sentence_match = args.get("sentence_match", "")
    new_sentence = args.get("new_sentence", "")
    reason = args.get("reason", "")

    if not section:
        return "[reword_sentence 失败] section 参数不能为空。"
    if not sentence_match:
        return "[reword_sentence 失败] sentence_match 参数不能为空。"
    if not new_sentence:
        return "[reword_sentence 失败] new_sentence 参数不能为空。"

    key = resolve_section_key(section, state.paper_sections)
    if key is None:
        return f"[reword_sentence 失败] 未找到 section '{section}'。"

    old_section_text = state.paper_sections[key]

    # 精确匹配（容忍首尾空格）
    match_stripped = sentence_match.strip()
    if match_stripped not in old_section_text:
        preview = old_section_text[:300]
        return (
            f"[reword_sentence 失败] 在 section '{key}' 中未找到精确匹配：\n"
            f"  你搜索的: \"{match_stripped[:80]}{'...' if len(match_stripped) > 80 else ''}\"\n"
            f"  section 开头: \"{preview}{'...' if len(old_section_text) > 300 else ''}\"\n\n"
            f"请确认原句是否完全一致（含标点），或先 read_section 重读确认。"
        )

    # 检查是否多次出现
    count = old_section_text.count(match_stripped)
    if count > 1:
        return (
            f"[reword_sentence 失败] 在 section '{key}' 中找到 {count} 处匹配。"
            f"请提供更长的上下文以唯一定位。"
        )

    # 执行替换
    new_section_text = old_section_text.replace(match_stripped, new_sentence.strip(), 1)
    state.paper_sections[key] = new_section_text

    # 记录 + 验证
    record_edit(state, key, reason, new_sentence)
    feedback = run_edit_verification(key, old_section_text, new_section_text, reason, state, checker)

    return (
        f"已替换 section '{key}' 中的句子（原因: {reason}）\n"
        f"原: {match_stripped[:100]}{'...' if len(match_stripped) > 100 else ''}\n"
        f"新: {new_sentence.strip()[:100]}{'...' if len(new_sentence.strip()) > 100 else ''}\n\n"
        f"{feedback}"
    )


# ============================================================
# tool_insert_content
# ============================================================

def tool_insert_content(args: dict, state: Any, checker: Any) -> str:
    """在指定 section 的指定位置插入内容。"""
    section = args.get("section", "")
    position = args.get("position", -1)
    content = args.get("content", "")
    reason = args.get("reason", "")

    if not section:
        return "[insert_content 失败] section 参数不能为空。"
    if not content:
        return "[insert_content 失败] content 参数不能为空。"

    key = resolve_section_key(section, state.paper_sections)
    if key is None:
        return f"[insert_content 失败] 未找到 section '{section}'。"

    old_section_text = state.paper_sections[key]
    paragraphs = old_section_text.split("\n\n")

    if not isinstance(position, int) or position < 0:
        return "[insert_content 失败] position 必须是非负整数。"
    if position > len(paragraphs):
        return (
            f"[insert_content 失败] section '{key}' 共 {len(paragraphs)} 段，"
            f"position 最大为 {len(paragraphs)}（末尾追加），但你指定了 {position}。"
        )

    # 执行插入
    paragraphs.insert(position, content)
    new_section_text = "\n\n".join(paragraphs)
    state.paper_sections[key] = new_section_text

    # 记录 + 验证
    record_edit(state, key, reason, content)
    feedback = run_edit_verification(key, old_section_text, new_section_text, reason, state, checker)

    pos_desc = "末尾" if position == len(paragraphs) - 1 else f"第 {position} 段之前"
    return (
        f"已在 section '{key}' 的{pos_desc}插入新段落（原因: {reason}）\n"
        f"插入内容: {content[:100]}{'...' if len(content) > 100 else ''}\n\n"
        f"{feedback}"
    )


# ============================================================
# tool_edit_section
# ============================================================

def tool_edit_section(args: dict, state: Any, checker: Any) -> str:
    """整体替换一个 section 的内容。"""
    section = args.get("section", "")
    new_content = args.get("new_content", "")
    reason = args.get("reason", "")

    key = resolve_section_key(section, state.paper_sections)
    if key is None:
        return f"未找到 section '{section}'，请用 list_sections 查看可用 section"

    # 先验证 section 存在，再记录编辑（避免无效 edit 污染记录）
    record_edit(state, key, reason, new_content)

    old_content = state.paper_sections[key]
    state.paper_sections[key] = new_content

    feedback = run_edit_verification(key, old_content, new_content, reason, state, checker)
    return f"已修改 section '{key}'（原因: {reason}）\n\n{feedback}"
