"""
EDIT-2: 计划验证器测试

覆盖四项零成本规则检查:
    1. 覆盖性: must-fix findings 是否全被引用
    2. 一致性: 有无矛盾步骤
    3. 范围合理性: 是否修改了 findings 未提及的 section
    4. 可执行性: requires 前置条件是否满足

验证标准:
    - 没有问题时返回空 issues（零噪声）
    - 有问题时输出 nudge（信息性，不 block）
    - 集成到 harness 后 generate_edit_plan 的反馈包含验证结果
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.state import WorkspaceState, EditPlan, EditStep
from core.edit_plan_validator import (
    validate_edit_plan,
    format_validation_nudge,
    _check_coverage,
    _check_consistency,
    _check_scope,
    _check_executability,
)


# ============================================================
# Helpers
# ============================================================

def _make_state(**kwargs) -> WorkspaceState:
    """创建测试用 WorkspaceState。"""
    return WorkspaceState(**kwargs)


def _make_plan(steps: list[dict], source_finding_ids: list[int] = None) -> EditPlan:
    """快速构建 EditPlan。"""
    edit_steps = []
    for s in steps:
        edit_steps.append(EditStep(
            target_section=s.get("section", "Introduction"),
            action=s.get("action", "reword"),
            description=s.get("description", "test"),
            requires=s.get("requires", []),
            priority=s.get("priority", "should"),
            finding_ids=s.get("finding_ids", []),
        ))
    return EditPlan(
        steps=edit_steps,
        source_finding_ids=source_finding_ids or [],
        estimated_scope="局部措辞",
    )


# ============================================================
# Test 1: 覆盖性检查
# ============================================================

class TestCoverage:
    """覆盖性: must-fix findings 是否全被引用。"""

    def test_all_must_fix_covered(self):
        """所有 high+verified findings 都被引用 → 无 issue。"""
        state = _make_state(findings=[
            {"finding": "问题A", "priority": "high", "status": "verified", "section": "Methods"},
            {"finding": "问题B", "priority": "high", "status": "verified", "section": "Results"},
        ])
        plan = _make_plan(
            steps=[
                {"section": "Methods", "finding_ids": [0]},
                {"section": "Results", "finding_ids": [1]},
            ],
            source_finding_ids=[0, 1],
        )
        result = validate_edit_plan(plan, state)
        assert result.passed

    def test_must_fix_uncovered(self):
        """有 high+verified finding 未被引用 → 报 issue。"""
        state = _make_state(findings=[
            {"finding": "严重问题", "priority": "high", "status": "verified", "section": "Methods"},
            {"finding": "小问题", "priority": "medium", "status": "verified", "section": "Results"},
        ])
        plan = _make_plan(
            steps=[{"section": "Results", "finding_ids": [1]}],
            source_finding_ids=[1],
        )
        result = validate_edit_plan(plan, state)
        assert not result.passed
        assert any("覆盖性" in issue for issue in result.issues)
        assert any("严重问题" in issue for issue in result.issues)

    def test_non_verified_high_not_required(self):
        """high 但 needs_verification 的 finding 不算 must-fix。"""
        state = _make_state(findings=[
            {"finding": "待验证问题", "priority": "high", "status": "needs_verification", "section": "Methods"},
        ])
        plan = _make_plan(steps=[{"section": "Results", "finding_ids": []}])
        result = validate_edit_plan(plan, state)
        # 不应因为 needs_verification 的 finding 未引用而报错
        assert not any("覆盖性" in issue for issue in result.issues)

    def test_medium_findings_not_required(self):
        """medium priority 不算 must-fix，不检查覆盖性。"""
        state = _make_state(findings=[
            {"finding": "中等问题", "priority": "medium", "status": "verified", "section": "Methods"},
        ])
        plan = _make_plan(steps=[{"section": "Results"}])
        result = validate_edit_plan(plan, state)
        assert not any("覆盖性" in issue for issue in result.issues)

    def test_no_findings_no_issue(self):
        """没有 findings 时不检查覆盖性。"""
        state = _make_state(findings=[])
        plan = _make_plan(steps=[{"section": "Introduction"}])
        result = validate_edit_plan(plan, state)
        assert result.passed


# ============================================================
# Test 2: 一致性检查
# ============================================================

class TestConsistency:
    """一致性: 有无矛盾步骤。"""

    def test_no_conflict(self):
        """不同 section 的不同 action → 无矛盾。"""
        plan = _make_plan(steps=[
            {"section": "Introduction", "action": "reword"},
            {"section": "Methods", "action": "remove"},
        ])
        state = _make_state()
        issues = _check_consistency(plan)
        assert len(issues) == 0

    def test_same_section_same_action_ok(self):
        """同一 section 多次 reword → 不矛盾（可能改不同段落）。"""
        plan = _make_plan(steps=[
            {"section": "Introduction", "action": "reword"},
            {"section": "Introduction", "action": "reword"},
        ])
        issues = _check_consistency(plan)
        assert len(issues) == 0

    def test_reword_and_remove_conflict(self):
        """同一 section 既 reword 又 remove → 矛盾。"""
        plan = _make_plan(steps=[
            {"section": "Introduction", "action": "reword"},
            {"section": "Introduction", "action": "remove"},
        ])
        issues = _check_consistency(plan)
        assert len(issues) == 1
        assert "一致性" in issues[0]
        assert "reword" in issues[0]
        assert "remove" in issues[0]

    def test_add_and_remove_conflict(self):
        """同一 section 既 add_content 又 remove → 矛盾。"""
        plan = _make_plan(steps=[
            {"section": "Results", "action": "add_content"},
            {"section": "Results", "action": "remove"},
        ])
        issues = _check_consistency(plan)
        assert len(issues) == 1
        assert "一致性" in issues[0]

    def test_restructure_and_remove_conflict(self):
        """同一 section 既 restructure 又 remove → 矛盾。"""
        plan = _make_plan(steps=[
            {"section": "Discussion", "action": "restructure"},
            {"section": "Discussion", "action": "remove"},
        ])
        issues = _check_consistency(plan)
        assert len(issues) == 1

    def test_case_insensitive_section_matching(self):
        """section 名大小写不同应视为同一 section。"""
        plan = _make_plan(steps=[
            {"section": "Introduction", "action": "reword"},
            {"section": "introduction", "action": "remove"},
        ])
        issues = _check_consistency(plan)
        assert len(issues) == 1


# ============================================================
# Test 3: 范围合理性检查
# ============================================================

class TestScope:
    """范围: plan 是否修改了 findings 未提及的 section。"""

    def test_all_in_scope(self):
        """plan 只修改 findings 提及的 sections → 无 issue。"""
        state = _make_state(findings=[
            {"finding": "问题", "section": "Methods", "priority": "high", "status": "verified"},
        ])
        plan = _make_plan(steps=[{"section": "Methods"}])
        issues = _check_scope(plan, state)
        assert len(issues) == 0

    def test_out_of_scope_section(self):
        """plan 修改了 findings 从未提及的 section → 报 issue。"""
        state = _make_state(findings=[
            {"finding": "问题", "section": "Methods", "priority": "high", "status": "verified"},
        ])
        plan = _make_plan(steps=[
            {"section": "Methods"},
            {"section": "Conclusion"},  # findings 从未提及
        ])
        issues = _check_scope(plan, state)
        assert len(issues) == 1
        assert "范围" in issues[0]
        assert "Conclusion" in issues[0]

    def test_fuzzy_match_section_name(self):
        """section 名模糊匹配：finding 说 'method'，plan 说 'Methods' → 视为同一。"""
        state = _make_state(findings=[
            {"finding": "问题", "section": "method", "priority": "high", "status": "verified"},
        ])
        plan = _make_plan(steps=[{"section": "Methods"}])
        issues = _check_scope(plan, state)
        assert len(issues) == 0  # "method" in "methods"

    def test_no_section_in_findings_skip_check(self):
        """findings 没有 section 信息时跳过范围检查。"""
        state = _make_state(findings=[
            {"finding": "问题", "priority": "high", "status": "verified"},  # 无 section 字段
        ])
        plan = _make_plan(steps=[{"section": "AnySection"}])
        issues = _check_scope(plan, state)
        assert len(issues) == 0


# ============================================================
# Test 4: 可执行性检查
# ============================================================

class TestExecutability:
    """可执行性: requires 前置条件是否满足。"""

    def test_no_requires_no_issue(self):
        """步骤没有 requires → 无 issue。"""
        state = _make_state()
        plan = _make_plan(steps=[{"section": "Methods", "requires": []}])
        issues = _check_executability(plan, state)
        assert len(issues) == 0

    def test_read_requirement_satisfied(self):
        """requires 要求读某 section，且已读 → 无 issue。"""
        state = _make_state(sections_read=["Methods"])
        plan = _make_plan(steps=[
            {"section": "Methods", "requires": ["需先读 Methods"]},
        ])
        issues = _check_executability(plan, state)
        assert len(issues) == 0

    def test_read_requirement_not_satisfied(self):
        """requires 要求读某 section，但未读 → 报 issue。"""
        state = _make_state(sections_read=["Introduction"])
        plan = _make_plan(steps=[
            {"section": "Methods", "requires": ["需先读 Results"]},
        ])
        issues = _check_executability(plan, state)
        assert len(issues) == 1
        assert "可执行性" in issues[0]
        assert "Results" in issues[0]

    def test_step_dependency_wrong_order(self):
        """步骤 1 依赖步骤 3（后续步骤）→ 顺序错误。"""
        state = _make_state()
        plan = _make_plan(steps=[
            {"section": "A", "requires": ["需先完成步骤 3"]},
            {"section": "B", "requires": []},
            {"section": "C", "requires": []},
        ])
        issues = _check_executability(plan, state)
        assert len(issues) == 1
        assert "可执行性" in issues[0]

    def test_step_dependency_correct_order(self):
        """步骤 3 依赖步骤 1（前序步骤）→ 无 issue。"""
        state = _make_state()
        plan = _make_plan(steps=[
            {"section": "A", "requires": []},
            {"section": "B", "requires": []},
            {"section": "C", "requires": ["需先完成步骤 1"]},
        ])
        issues = _check_executability(plan, state)
        assert len(issues) == 0


# ============================================================
# Test 5: 格式化输出
# ============================================================

class TestFormatting:
    """format_validation_nudge 的输出格式。"""

    def test_no_issues_empty_string(self):
        """无问题时返回空字符串（零噪声）。"""
        from core.edit_plan_validator import PlanValidationResult
        result = PlanValidationResult(issues=[])
        assert format_validation_nudge(result) == ""

    def test_has_issues_formatted(self):
        """有问题时输出包含 [计划审查] 标记和具体问题。"""
        from core.edit_plan_validator import PlanValidationResult
        result = PlanValidationResult(issues=["覆盖性: 问题A", "一致性: 问题B"])
        nudge = format_validation_nudge(result)
        assert "[计划审查]" in nudge
        assert "2 个潜在问题" in nudge
        assert "覆盖性: 问题A" in nudge
        assert "一致性: 问题B" in nudge
        assert "不阻塞执行" in nudge


# ============================================================
# Test 6: 集成测试 — generate_edit_plan 触发验证
# ============================================================

class TestIntegration:
    """验证器集成到 harness 的 generate_edit_plan 中。"""

    def test_plan_with_uncovered_finding_shows_nudge(self):
        """生成计划时漏了 must-fix finding → 反馈中包含验证 nudge。"""
        from core.harness import Harness
        h = Harness()
        h.state.paper_sections = {"Introduction": "Some text.", "Methods": "Method details."}
        h.state.findings = [
            {"finding": "严重逻辑错误", "priority": "high", "status": "verified", "section": "Methods"},
            {"finding": "小问题", "priority": "medium", "status": "verified", "section": "Introduction"},
        ]

        # 只引用了 finding[1]，漏了 finding[0]（must-fix）
        result = h.execute_tool("generate_edit_plan", {
            "steps": [
                {
                    "target_section": "Introduction",
                    "action": "reword",
                    "description": "修正小问题",
                    "finding_ids": [1],
                },
            ],
            "estimated_scope": "局部措辞",
            "rationale": "修复小问题",
        })

        assert "修改计划已生成" in result
        assert "计划审查" in result
        assert "覆盖性" in result

    def test_plan_all_good_no_nudge(self):
        """计划没有问题时反馈中不包含验证 nudge。"""
        from core.harness import Harness
        h = Harness()
        h.state.paper_sections = {"Methods": "Method details."}
        h.state.findings = [
            {"finding": "问题A", "priority": "high", "status": "verified", "section": "Methods"},
        ]

        result = h.execute_tool("generate_edit_plan", {
            "steps": [
                {
                    "target_section": "Methods",
                    "action": "reword",
                    "description": "修复问题A",
                    "finding_ids": [0],
                },
            ],
            "estimated_scope": "局部措辞",
            "rationale": "修复",
        })

        assert "修改计划已生成" in result
        assert "计划审查" not in result
