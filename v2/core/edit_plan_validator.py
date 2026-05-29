"""
core/edit_plan_validator.py — EDIT-2: 计划验证器

零成本规则检查（不调 LLM）。在 generate_edit_plan 成功后自动执行。
输出是 nudge（建议），不是 block（阻塞）——Agent 收到提醒后仍有最终决策权。

四项检查:
    1. 覆盖性: must-fix findings 是否全被 plan 步骤引用？
    2. 一致性: 有无矛盾步骤（如同一 section 同时 reword 和 remove）？
    3. 范围合理性: plan 是否修改了 findings 未提及的 section？
    4. 可执行性: requires 中声明的前置条件是否满足？

设计原则 (COGNITIVE_ANCHOR §4.3):
    - 这是环境给 Agent 的事实性信号，不是指令
    - Agent 可以完全忽略所有 nudge 继续执行
    - 没有问题时不输出任何东西（零噪声）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from core.state import EditPlan, WorkspaceState


# ============================================================
# Validation Result
# ============================================================

@dataclass
class PlanValidationResult:
    """验证结果。issues 为空表示通过。"""
    issues: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return len(self.issues) == 0


# ============================================================
# Core Validation Logic
# ============================================================

def validate_edit_plan(
    plan: EditPlan,
    state: WorkspaceState,
) -> PlanValidationResult:
    """
    对 Agent 产出的 edit_plan 执行四项零成本规则检查。

    Args:
        plan: Agent 生成的修改计划
        state: 当前工作状态（含 findings、paper_sections、sections_read）

    Returns:
        PlanValidationResult，issues 为空表示无问题
    """
    issues: list[str] = []

    issues.extend(_check_coverage(plan, state))
    issues.extend(_check_consistency(plan))
    issues.extend(_check_scope(plan, state))
    issues.extend(_check_executability(plan, state))

    return PlanValidationResult(issues=issues)


def format_validation_nudge(result: PlanValidationResult) -> str:
    """将验证结果格式化为 nudge 文本。无问题时返回空字符串。"""
    if result.passed:
        return ""

    lines = [
        "",
        f"[计划审查] 检测到 {len(result.issues)} 个潜在问题（仅供参考，不阻塞执行）:",
    ]
    for i, issue in enumerate(result.issues, 1):
        lines.append(f"  {i}. {issue}")
    lines.append("你可以选择调整计划，也可以忽略这些提醒直接执行。")

    return "\n".join(lines)


# ============================================================
# Check 1: 覆盖性 — must-fix findings 是否全被引用
# ============================================================

def _check_coverage(plan: EditPlan, state: WorkspaceState) -> list[str]:
    """检查 priority='high' + status='verified' 的 findings 是否被 plan 引用。"""
    issues: list[str] = []

    # 找出所有 must-fix findings 的索引
    must_fix_indices: list[int] = []
    for i, f in enumerate(state.findings):
        if f.get("priority") == "high" and f.get("status") == "verified":
            must_fix_indices.append(i)

    if not must_fix_indices:
        return issues  # 没有 must-fix，无需检查

    # 收集 plan 中所有被引用的 finding ids
    referenced_ids: set[int] = set(plan.source_finding_ids)
    for step in plan.steps:
        referenced_ids.update(step.finding_ids)

    # 找出未覆盖的 must-fix
    uncovered = [idx for idx in must_fix_indices if idx not in referenced_ids]
    if uncovered:
        uncovered_summaries = []
        for idx in uncovered[:3]:  # 最多展示 3 条
            finding_text = state.findings[idx].get("finding", "")[:60]
            uncovered_summaries.append(f"#{idx+1}: {finding_text}")
        issues.append(
            f"覆盖性: {len(uncovered)} 条高优先级已验证发现未被计划引用 — "
            f"{'; '.join(uncovered_summaries)}"
        )

    return issues


# ============================================================
# Check 2: 一致性 — 有无矛盾步骤
# ============================================================

# 互斥 action 对：同一 section 上这些 action 组合通常是矛盾的
_CONFLICTING_ACTIONS: list[tuple[str, str]] = [
    ("reword", "remove"),       # 改了又删，多余
    ("add_content", "remove"),  # 加了又删，矛盾
    ("restructure", "remove"),  # 重组了又删，矛盾
]


def _check_consistency(plan: EditPlan) -> list[str]:
    """检查同一 section 上是否有矛盾的 action 组合。"""
    issues = []

    # 按 section 分组收集 actions
    section_actions: dict[str, list[str]] = {}
    for step in plan.steps:
        key = step.target_section.lower().strip()
        if key not in section_actions:
            section_actions[key] = []
        section_actions[key].append(step.action)

    # 检查每个 section 的 action 组合
    for section, actions in section_actions.items():
        action_set = set(actions)
        for a1, a2 in _CONFLICTING_ACTIONS:
            if a1 in action_set and a2 in action_set:
                issues.append(
                    f"一致性: section '{section}' 同时有 '{a1}' 和 '{a2}' 步骤，可能矛盾"
                )

    return issues


# ============================================================
# Check 3: 范围合理性 — plan 是否修改了 findings 未提及的 section
# ============================================================

def _check_scope(plan: EditPlan, state: WorkspaceState) -> list[str]:
    """检查 plan 是否涉及了 findings 从未提及的 section。"""
    issues: list[str] = []

    # 收集 findings 中提及的 sections
    findings_sections: set[str] = set()
    for f in state.findings:
        section = f.get("section", "")
        if section:
            findings_sections.add(section.lower().strip())

    if not findings_sections:
        return issues  # findings 没有 section 信息，无法检查

    # 检查 plan 中每个步骤的 target_section
    out_of_scope_sections: set[str] = set()
    for step in plan.steps:
        target = step.target_section.lower().strip()
        # 模糊匹配：plan 中的 section 名是否与任何 finding section 有包含关系
        matched = any(
            target in fs or fs in target
            for fs in findings_sections
        )
        if not matched:
            out_of_scope_sections.add(step.target_section)

    if out_of_scope_sections:
        sections_str = ", ".join(sorted(out_of_scope_sections)[:3])
        issues.append(
            f"范围: 计划涉及 {len(out_of_scope_sections)} 个 findings 未提及的 section"
            f"（{sections_str}）— 确认这些修改有必要？"
        )

    return issues


# ============================================================
# Check 4: 可执行性 — requires 中的前置条件是否满足
# ============================================================

def _check_executability(plan: EditPlan, state: WorkspaceState) -> list[str]:
    """检查 steps 中 requires 声明的前置条件是否满足。"""
    issues = []

    for i, step in enumerate(plan.steps):
        if not step.requires:
            continue

        for req in step.requires:
            req_lower = req.lower().strip()

            # 类型 1: "需先读 Section X" — 检查是否已读
            if "读" in req_lower or "read" in req_lower:
                # 从 requires 文本中尝试提取 section 名
                # 粗略匹配：看 state.sections_read 中是否有相关 section
                section_mentioned = _extract_section_from_requirement(req)
                if section_mentioned:
                    read_lower = [s.lower() for s in state.sections_read]
                    if not any(section_mentioned.lower() in r or r in section_mentioned.lower()
                               for r in read_lower):
                        issues.append(
                            f"可执行性: 步骤 {i+1} 要求「{req}」，但该 section 尚未读取"
                        )

            # 类型 2: "需先完成步骤 N" — 检查依赖顺序是否合理
            # （当前步骤声明依赖后续步骤 = 顺序错误）
            if "步骤" in req_lower or "step" in req_lower:
                dep_idx = _extract_step_index(req)
                if dep_idx is not None and dep_idx > i:
                    issues.append(
                        f"可执行性: 步骤 {i+1} 依赖步骤 {dep_idx+1}，但后者排在其后"
                    )

    return issues


# ============================================================
# Helper Functions
# ============================================================

def _extract_section_from_requirement(req: str) -> str | None:
    """从 requires 文本中粗略提取 section 名。

    示例: "需先读 Introduction" → "introduction"
          "read Results section" → "results"
    """
    import re
    # 匹配 "Section X" 或 "读 X" 模式
    m = re.search(r'(?:section|读|read)\s+["\']?(\w[\w\s]*\w)["\']?', req, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return None


def _extract_step_index(req: str) -> int | None:
    """从 requires 文本中提取步骤编号（0-indexed）。

    示例: "步骤 2" → 1, "step 3" → 2
    """
    import re
    m = re.search(r'(?:步骤|step)\s*(\d+)', req, re.IGNORECASE)
    if m:
        return int(m.group(1)) - 1  # 转为 0-indexed
    return None
