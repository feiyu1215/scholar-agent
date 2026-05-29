"""
EDIT-1: generate_edit_plan 工具测试。

测试覆盖:
  1. 基本功能：正常 plan 生成 + state 存储
  2. 校验：空 steps、无效 action、finding_id 越界
  3. 边界：无 findings 时 plan 仍可创建（不引用 finding）
  4. 覆写：重复调用覆盖旧 plan
  5. Phase gating：deep_review 和 editing 阶段可用
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.harness import Harness
from core.state import EditPlan, EditStep


# ============================================================
# Test 1: 正常创建 plan
# ============================================================

def test_generate_edit_plan_basic():
    """正常 plan 生成，存入 state.edit_plan。"""
    h = Harness()
    # 先添加一些 findings
    h.state.findings = [
        {"finding": "[Overclaim] Abstract 过度声称", "priority": "high", "status": "verified"},
        {"finding": "[数据不一致] N=1000 vs N=856", "priority": "high", "status": "verified"},
        {"finding": "[建议] 可补充 robustness check", "priority": "low", "status": "suggestion"},
    ]

    result = h.execute_tool("generate_edit_plan", {
        "steps": [
            {
                "target_section": "Abstract",
                "action": "reword",
                "description": "删除 SOTA 声称，改为 competitive performance",
                "priority": "must",
                "finding_ids": [0],
            },
            {
                "target_section": "Section 3",
                "action": "verify_data",
                "description": "核实 N 值并统一",
                "priority": "must",
                "finding_ids": [1],
            },
            {
                "target_section": "Section 5",
                "action": "add_content",
                "description": "补充 robustness check 段落",
                "priority": "could",
                "finding_ids": [2],
            },
        ],
        "estimated_scope": "段落重组",
        "rationale": "三个问题按严重程度排序，先修正硬伤再锦上添花",
    })

    # 验证返回信息
    assert "修改计划已生成" in result
    assert "3 步" in result
    assert "段落重组" in result

    # 验证 state
    plan = h.state.edit_plan
    assert plan is not None
    assert isinstance(plan, EditPlan)
    assert len(plan.steps) == 3
    assert plan.estimated_scope == "段落重组"
    assert plan.rationale == "三个问题按严重程度排序，先修正硬伤再锦上添花"
    assert plan.source_finding_ids == [0, 1, 2]

    # 验证步骤细节
    step0 = plan.steps[0]
    assert step0.target_section == "Abstract"
    assert step0.action == "reword"
    assert step0.priority == "must"
    assert step0.status == "pending"
    assert step0.finding_ids == [0]

    step2 = plan.steps[2]
    assert step2.action == "add_content"
    assert step2.priority == "could"


# ============================================================
# Test 2: 空 steps 拒绝
# ============================================================

def test_generate_edit_plan_empty_steps():
    """空 steps 列表应返回错误。"""
    h = Harness()
    result = h.execute_tool("generate_edit_plan", {
        "steps": [],
        "estimated_scope": "局部措辞",
        "rationale": "test",
    })
    assert "计划生成失败" in result
    assert h.state.edit_plan is None


# ============================================================
# Test 3: 无效 action 降级为 reword
# ============================================================

def test_generate_edit_plan_invalid_action():
    """无效 action 导致该 step 被跳过（不再静默降级为 reword）。"""
    h = Harness()
    h.state.findings = [{"finding": "test", "priority": "high", "status": "verified"}]

    # 所有 step 都用了无效 action → plan 生成失败
    result = h.execute_tool("generate_edit_plan", {
        "steps": [
            {
                "target_section": "Introduction",
                "action": "invalid_action",
                "description": "测试无效 action",
                "priority": "should",
                "finding_ids": [0],
            },
        ],
        "estimated_scope": "局部措辞",
        "rationale": "测试",
    })

    assert "计划生成失败" in result
    assert "invalid_action" in result or "校验失败" in result
    assert h.state.edit_plan is None


def test_generate_edit_plan_invalid_action_partial():
    """混合有效和无效 action 时，无效 step 被跳过，有效 step 正常保留。"""
    h = Harness()
    h.state.findings = [{"finding": "test", "priority": "high", "status": "verified"}]
    h.state.paper_sections = {"Introduction": "Some intro text."}

    result = h.execute_tool("generate_edit_plan", {
        "steps": [
            {
                "target_section": "Introduction",
                "action": "invalid_action",
                "description": "无效步骤",
                "priority": "should",
                "finding_ids": [0],
            },
            {
                "target_section": "Introduction",
                "action": "reword",
                "description": "有效步骤",
                "priority": "must",
                "finding_ids": [0],
            },
        ],
        "estimated_scope": "局部措辞",
        "rationale": "测试",
    })

    # plan 创建成功（只保留了有效的 step）
    assert "计划已生成" in result or "1 步" in result
    assert "警告" in result
    assert h.state.edit_plan is not None
    assert len(h.state.edit_plan.steps) == 1
    assert h.state.edit_plan.steps[0].action == "reword"


# ============================================================
# Test 4: finding_id 越界警告（不阻塞）
# ============================================================

def test_generate_edit_plan_finding_id_out_of_range():
    """finding_id 越界只警告，不阻塞 plan 创建。"""
    h = Harness()
    h.state.findings = [{"finding": "唯一的 finding", "priority": "high", "status": "verified"}]

    result = h.execute_tool("generate_edit_plan", {
        "steps": [
            {
                "target_section": "Abstract",
                "action": "reword",
                "description": "测试越界引用",
                "priority": "must",
                "finding_ids": [0, 99],  # 99 越界
            },
        ],
        "estimated_scope": "局部措辞",
        "rationale": "测试",
    })

    assert "警告" in result
    assert "越界" in result
    # plan 仍创建，但只保留合法的 finding_id
    assert h.state.edit_plan is not None
    assert h.state.edit_plan.steps[0].finding_ids == [0]


# ============================================================
# Test 5: 无 findings 时仍可创建 plan（不引用 finding）
# ============================================================

def test_generate_edit_plan_no_findings():
    """即使没有 findings，也能创建不引用 finding 的 plan。"""
    h = Harness()
    # state.findings 为空

    result = h.execute_tool("generate_edit_plan", {
        "steps": [
            {
                "target_section": "Conclusion",
                "action": "restructure",
                "description": "重组结论段落结构",
                "priority": "should",
                # 不传 finding_ids
            },
        ],
        "estimated_scope": "段落重组",
        "rationale": "结论太散需要收拢",
    })

    assert "修改计划已生成" in result
    assert h.state.edit_plan is not None
    assert h.state.edit_plan.steps[0].finding_ids == []
    assert h.state.edit_plan.source_finding_ids == []


# ============================================================
# Test 6: 覆写旧 plan
# ============================================================

def test_generate_edit_plan_overwrites_old():
    """第二次调用覆盖第一次的 plan。"""
    h = Harness()

    h.execute_tool("generate_edit_plan", {
        "steps": [{"target_section": "A", "action": "reword", "description": "第一版", "priority": "must"}],
        "estimated_scope": "局部措辞",
        "rationale": "v1",
    })
    assert h.state.edit_plan.rationale == "v1"

    h.execute_tool("generate_edit_plan", {
        "steps": [{"target_section": "B", "action": "remove", "description": "第二版", "priority": "should"}],
        "estimated_scope": "章节重写",
        "rationale": "v2",
    })
    assert h.state.edit_plan.rationale == "v2"
    assert h.state.edit_plan.steps[0].target_section == "B"
    assert h.state.edit_plan.estimated_scope == "章节重写"


# ============================================================
# Test 7: Phase gating — deep_review 和 editing 可用
# ============================================================

def test_generate_edit_plan_phase_visibility():
    """generate_edit_plan 在 deep_review 和 editing 阶段可见。"""
    h = Harness()

    # 应该在 deep_review 可见
    deep_review_tools = h.tool_registry.get_tools_for_phase("deep_review")
    assert "generate_edit_plan" in deep_review_tools

    # 应该在 editing 可见
    editing_tools = h.tool_registry.get_tools_for_phase("editing")
    assert "generate_edit_plan" in editing_tools

    # 不应在 initial_scan 可见
    scan_tools = h.tool_registry.get_tools_for_phase("initial_scan")
    assert "generate_edit_plan" not in scan_tools

    # 不应在 synthesis 可见
    synth_tools = h.tool_registry.get_tools_for_phase("synthesis")
    assert "generate_edit_plan" not in synth_tools


# ============================================================
# Test 8: target_section 为空的步骤被跳过
# ============================================================

def test_generate_edit_plan_skips_invalid_steps():
    """target_section 或 description 为空的步骤被跳过（带警告）。"""
    h = Harness()

    result = h.execute_tool("generate_edit_plan", {
        "steps": [
            {"target_section": "", "action": "reword", "description": "空 section", "priority": "must"},
            {"target_section": "Valid", "action": "reword", "description": "", "priority": "must"},
            {"target_section": "Valid", "action": "reword", "description": "有效步骤", "priority": "must"},
        ],
        "estimated_scope": "局部措辞",
        "rationale": "测试",
    })

    assert "警告" in result
    # 只有第三步有效
    assert h.state.edit_plan is not None
    assert len(h.state.edit_plan.steps) == 1
    assert h.state.edit_plan.steps[0].description == "有效步骤"


# ============================================================
# Test 9: 所有步骤都无效时 plan 创建失败
# ============================================================

def test_generate_edit_plan_all_invalid_steps():
    """所有步骤都无效时返回失败。"""
    h = Harness()

    result = h.execute_tool("generate_edit_plan", {
        "steps": [
            {"target_section": "", "action": "reword", "description": "空 section", "priority": "must"},
            {"target_section": "X", "action": "reword", "description": "", "priority": "must"},
        ],
        "estimated_scope": "局部措辞",
        "rationale": "测试",
    })

    assert "计划生成失败" in result
    assert h.state.edit_plan is None


# ============================================================
# Test 10: EditStep 和 EditPlan 的默认值
# ============================================================

def test_edit_dataclass_defaults():
    """EditStep 和 EditPlan 的默认值正确。"""
    step = EditStep(target_section="A", action="reword", description="test")
    assert step.requires == []
    assert step.priority == "should"
    assert step.status == "pending"
    assert step.finding_ids == []

    plan = EditPlan()
    assert plan.steps == []
    assert plan.source_finding_ids == []
    assert plan.estimated_scope == "局部措辞"
    assert plan.rationale == ""
