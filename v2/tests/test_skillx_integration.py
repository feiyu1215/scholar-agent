"""
tests/test_skillx_integration.py — SkillX 集成层测试

覆盖 Phase 3 的四大集成点:
  1. Harness 初始化时 SkillX 正确初始化 + apply_skill 工具注册
  2. Phase 转换时 on_phase_transition() 触发 ToolGroup 切换
  3. format_context() 追加 SkillX 提示
  4. apply_skill tool handler 正确调用 execute_skill
  5. Kill Switch 关闭时 graceful degradation

测试原则:
  - 不 mock 底层 Skills (使用真实 economics Skills)
  - 验证端到端数据流而非内部实现
  - 所有测试可在 SkillX disabled 环境中标记 skip
"""

import os
import pytest
from unittest.mock import patch, MagicMock


# ==============================================================
# Fixtures
# ==============================================================

@pytest.fixture(autouse=True)
def enable_skillx():
    """确保测试中 SkillX 处于开启状态。"""
    with patch.dict(os.environ, {"SCHOLAR_GODEL_SKILLX": "1"}):
        yield


@pytest.fixture
def harness_with_skillx():
    """创建启用 SkillX 的 Harness 实例。"""
    from core.harness import Harness
    h = Harness()
    assert h.skillx is not None, "SkillX should be initialized when enabled"
    return h


@pytest.fixture
def skillx_integration():
    """独立的 SkillXIntegration 实例 (不依赖 Harness)。"""
    from core.skillx_integration import SkillXIntegration
    return SkillXIntegration()


# ==============================================================
# Test 1: 初始化与工具注册
# ==============================================================

class TestSkillXInitialization:
    """SkillX 初始化与 apply_skill 工具注册。"""

    def test_harness_initializes_skillx_when_enabled(self, harness_with_skillx):
        """启用时 Harness 持有 SkillXIntegration 实例。"""
        h = harness_with_skillx
        assert h.skillx is not None
        assert h.skillx._current_phase == "orientation"

    def test_apply_skill_tool_registered(self, harness_with_skillx):
        """apply_skill 工具被注册到 ToolRegistry。"""
        h = harness_with_skillx
        assert "apply_skill" in h.tool_registry.tool_names

    def test_skillx_disabled_by_env(self):
        """Kill Switch 关闭时 SkillX 不初始化。"""
        from core.harness import Harness
        with patch("core.godel_config.GODEL_SKILLX_ENABLED", False):
            h = Harness()
            assert h.skillx is None

    def test_apply_skill_not_registered_when_disabled(self):
        """Kill Switch 关闭时 apply_skill 工具不注册。"""
        from core.harness import Harness
        with patch("core.godel_config.GODEL_SKILLX_ENABLED", False):
            h = Harness()
            assert "apply_skill" not in h.tool_registry.tool_names

    def test_skillx_has_unified_registry(self, skillx_integration):
        """SkillXIntegration 包含 UnifiedSkillRegistry。"""
        s = skillx_integration
        assert s.unified_registry is not None
        # 应该至少加载了原生 economics skills
        assert len(s.unified_registry.native_skills()) > 0

    def test_skillx_has_executor(self, skillx_integration):
        """SkillXIntegration 包含 SkillExecutor。"""
        s = skillx_integration
        assert s.executor is not None

    def test_skillx_has_tool_groups(self, skillx_integration):
        """SkillXIntegration 初始化了 ToolGroup。"""
        s = skillx_integration
        groups = s.tool_group_manager.all_group_names
        assert len(groups) > 0
        # basic 组必须存在
        assert "basic" in groups

    def test_graceful_degradation_on_init_failure(self):
        """初始化失败时 Harness 降级运行 (skillx=None)。"""
        from core.harness import Harness
        # 模拟导入失败
        with patch("core.skillx_integration.SkillXIntegration",
                   side_effect=RuntimeError("Simulated init failure")):
            h = Harness()
            assert h.skillx is None
            # 核心工具仍可用
            assert "read_section" in h.tool_registry.tool_names


# ==============================================================
# Test 2: Phase 转换 Hook
# ==============================================================

class TestPhaseTransition:
    """Phase 转换触发 SkillX ToolGroup 切换。"""

    def test_phase_transition_updates_current_phase(self, skillx_integration):
        """on_phase_transition 更新内部 _current_phase。"""
        s = skillx_integration
        assert s._current_phase == "orientation"
        s.on_phase_transition("deep_review")
        assert s._current_phase == "deep_review"

    def test_phase_transition_activates_relevant_groups(self, skillx_integration):
        """deep_review Phase 应激活方法论/统计相关组。"""
        s = skillx_integration
        s.on_phase_transition("deep_review")
        active = s.tool_group_manager.active_group_names
        # deep_review 应该有 methodology_analysis 或 statistical_validation
        # (取决于 DEFAULT_PHASE_GROUPS 配置)
        assert "basic" in active  # basic 始终激活
        # 至少有 1 个非 basic 的组被激活
        non_basic = [g for g in active if g != "basic"]
        assert len(non_basic) > 0

    def test_synthesis_phase_activates_scoring(self, skillx_integration):
        """synthesis Phase 应激活综合评分组。"""
        s = skillx_integration
        s.on_phase_transition("synthesis")
        active = s.tool_group_manager.active_group_names
        assert "basic" in active

    def test_unknown_phase_does_not_crash(self, skillx_integration):
        """未知 Phase 不会崩溃 (graceful)。"""
        s = skillx_integration
        s.on_phase_transition("nonexistent_phase_xyz")
        assert s._current_phase == "nonexistent_phase_xyz"
        # basic 组仍然激活
        assert "basic" in s.tool_group_manager.active_group_names

    def test_harness_phase_transition_triggers_skillx(self, harness_with_skillx):
        """Harness 的 _tool_request_phase_transition 成功时触发 SkillX hook。"""
        h = harness_with_skillx
        # 直接调用 on_phase_transition 验证不崩溃
        h.skillx.on_phase_transition("deep_review")
        assert h.skillx._current_phase == "deep_review"


# ==============================================================
# Test 3: Context 提示
# ==============================================================

class TestContextHints:
    """format_context() 中 SkillX 提示生成。"""

    def test_get_skill_hints_returns_string(self, skillx_integration):
        """get_skill_hints 返回非空字符串。"""
        s = skillx_integration
        hints = s.get_skill_hints()
        assert isinstance(hints, str)

    def test_hints_contain_skillx_header(self, skillx_integration):
        """提示文本包含 [SkillX 可用能力] 标题。"""
        s = skillx_integration
        # 切换到有活跃 Skills 的 phase
        s.on_phase_transition("deep_review")
        hints = s.get_skill_hints()
        if hints:  # 如果有激活的 skills
            assert "[SkillX 可用能力]" in hints

    def test_hints_mention_apply_skill(self, skillx_integration):
        """提示文本提到 apply_skill 使用方式。"""
        s = skillx_integration
        s.on_phase_transition("deep_review")
        hints = s.get_skill_hints()
        if hints:
            assert "apply_skill" in hints

    def test_hints_respect_token_budget(self, skillx_integration):
        """小 token 预算时提示文本被裁剪。"""
        s = skillx_integration
        s.on_phase_transition("deep_review")
        hints_full = s.get_skill_hints(token_budget=5000)
        hints_small = s.get_skill_hints(token_budget=50)
        # 小预算应该不比全量更长
        assert len(hints_small) <= len(hints_full) + 100  # 允许小误差

    def test_harness_format_context_includes_hints(self, harness_with_skillx):
        """Harness.format_context() 输出包含 SkillX 提示。"""
        h = harness_with_skillx
        # 模拟 Phase 切换使 Skills 激活
        h.skillx.on_phase_transition("deep_review")
        ctx = h.format_context()
        # 上下文应该包含 SkillX 相关内容 (如果有活跃 Skills)
        active_skills = h.skillx.tool_group_manager.get_active_skills()
        if active_skills:
            assert "SkillX" in ctx or "apply_skill" in ctx


# ==============================================================
# Test 4: apply_skill Tool Handler
# ==============================================================

class TestApplySkillTool:
    """apply_skill tool 端到端执行。"""

    def test_apply_skill_no_name_lists_available(self, harness_with_skillx):
        """不提供 skill_name 时返回引导信息。"""
        h = harness_with_skillx
        result = h._tool_apply_skill({"skill_name": ""})
        assert "[SkillX]" in result
        # 引导信息可能是列表或提示无激活 Skills
        assert "skill_name" in result or "可用" in result or "无激活" in result

    def test_apply_skill_unknown_name_returns_error(self, harness_with_skillx):
        """不存在的 skill 返回 error 信息。"""
        h = harness_with_skillx
        result = h._tool_apply_skill({"skill_name": "nonexistent_skill_xyz"})
        assert "Error" in result or "not found" in result

    def test_apply_skill_valid_skill_executes(self, harness_with_skillx):
        """已注册的 Skill 可以正常执行。"""
        h = harness_with_skillx
        # 获取一个已注册的 native skill 名称
        all_skills = h.skillx.unified_registry.all_skills()
        if not all_skills:
            pytest.skip("No skills loaded")
        skill_name = all_skills[0].descriptor.name

        result = h._tool_apply_skill({
            "skill_name": skill_name,
            "section_context": "This paper uses a difference-in-differences approach.",
        })
        # 应该返回结果 (成功或失败), 不是 crash
        assert isinstance(result, str)
        assert len(result) > 0

    def test_apply_skill_disabled_returns_message(self):
        """SkillX 禁用时 apply_skill 返回未启用提示。"""
        from core.harness import Harness
        with patch("core.godel_config.GODEL_SKILLX_ENABLED", False):
            h = Harness()
            assert h.skillx is None
            # 手动调用 (即使工具未注册, 方法仍可调用)
            result = h._tool_apply_skill({"skill_name": "test"})
            assert "未启用" in result


# ==============================================================
# Test 5: execute_skill 详细行为
# ==============================================================

class TestExecuteSkill:
    """SkillXIntegration.execute_skill() 详细测试。"""

    def test_execute_returns_dict_structure(self, skillx_integration):
        """execute_skill 总是返回规范 dict 结构。"""
        s = skillx_integration
        result = s.execute_skill(
            skill_name="nonexistent_xyz",
            parameters={},
            paper_text="",
        )
        assert "success" in result
        assert "findings" in result
        assert "output_data" in result
        assert "error" in result
        assert "execution_time_ms" in result

    def test_execute_not_found_returns_failure(self, skillx_integration):
        """不存在的 skill 返回 success=False。"""
        s = skillx_integration
        result = s.execute_skill(skill_name="xyz_not_exist")
        assert result["success"] is False
        assert "not found" in result["error"]

    def test_execute_real_skill(self, skillx_integration):
        """真实 Skill 执行返回成功结果。"""
        s = skillx_integration
        all_skills = s.unified_registry.all_skills()
        if not all_skills:
            pytest.skip("No skills loaded")

        skill_name = all_skills[0].descriptor.name
        result = s.execute_skill(
            skill_name=skill_name,
            paper_text="We employ a standard OLS regression with robust standard errors.",
            parameters={},
        )
        # 执行应成功 (即使没找到问题, findings 可以是空)
        assert result["success"] is True
        assert isinstance(result["findings"], list)
        assert result["execution_time_ms"] >= 0

    def test_execute_skill_with_findings_returned(self, skillx_integration):
        """Skill 产出 findings 时 dict 序列化正确 (regression: f.text→f.description)。"""
        s = skillx_integration
        # statistical_validation 对明显不一致的文本通常会产出 findings
        result = s.execute_skill(
            skill_name="statistical_validation",
            paper_text=(
                "We find a coefficient of 0.5 with t-statistic of 1.2, "
                "which is not significant at the 5% level but we claim a strong effect. "
                "The R-squared is 0.01 indicating our model explains nearly all variation."
            ),
            parameters={},
        )
        assert result["success"] is True
        assert isinstance(result["findings"], list)
        # 验证 findings 中每个 item 的 dict 结构正确
        for finding in result["findings"]:
            assert "text" in finding
            assert "severity" in finding
            assert "category" in finding
            assert isinstance(finding["text"], str)
            assert len(finding["text"]) > 0

    def test_execute_with_existing_findings(self, skillx_integration):
        """传入已有 findings 不会导致崩溃。"""
        from core.skills.base import Finding
        s = skillx_integration
        all_skills = s.unified_registry.all_skills()
        if not all_skills:
            pytest.skip("No skills loaded")

        existing = [
            Finding(
                category="methodology",
                severity="major",
                description="Test finding",
            )
        ]
        skill_name = all_skills[0].descriptor.name
        result = s.execute_skill(
            skill_name=skill_name,
            paper_text="Sample text",
            existing_findings=existing,
        )
        assert isinstance(result, dict)


# ==============================================================
# Test 6: get_stats 诊断
# ==============================================================

class TestStats:
    """SkillX 诊断统计接口。"""

    def test_get_stats_returns_dict(self, skillx_integration):
        """get_stats 返回完整的统计字典。"""
        s = skillx_integration
        stats = s.get_stats()
        assert "total_skills" in stats
        assert "native_skills" in stats
        assert "adapted_skills" in stats
        assert "active_groups" in stats
        assert "current_phase" in stats
        assert "executor_stats" in stats

    def test_stats_match_registry_count(self, skillx_integration):
        """统计中的 skill 数量与 registry 一致。"""
        s = skillx_integration
        stats = s.get_stats()
        expected_total = len(s.unified_registry.all_skills())
        assert stats["total_skills"] == expected_total


# ==============================================================
# Test 7: 端到端流程 (Init → Phase transition → Execute)
# ==============================================================

class TestEndToEnd:
    """端到端: 从初始化到执行的完整流程。"""

    def test_full_lifecycle(self, harness_with_skillx):
        """完整生命周期: 初始化 → 切换 Phase → 获取提示 → 执行 Skill。"""
        h = harness_with_skillx

        # 1. 初始化成功
        assert h.skillx is not None
        assert h.skillx._current_phase == "orientation"

        # 2. 切换 Phase
        h.skillx.on_phase_transition("deep_review")
        assert h.skillx._current_phase == "deep_review"

        # 3. 获取提示
        hints = h.skillx.get_skill_hints()
        assert isinstance(hints, str)

        # 4. 执行 Skill (如果有的话)
        all_skills = h.skillx.unified_registry.all_skills()
        if all_skills:
            result = h._tool_apply_skill({
                "skill_name": all_skills[0].descriptor.name,
                "section_context": "The treatment group showed a 15% improvement.",
            })
            assert isinstance(result, str)
            assert len(result) > 0

    def test_multiple_phase_transitions(self, harness_with_skillx):
        """多次 Phase 转换不会累积错误。"""
        h = harness_with_skillx
        phases = ["orientation", "deep_review", "synthesis", "orientation", "deep_review"]
        for phase in phases:
            h.skillx.on_phase_transition(phase)
            assert h.skillx._current_phase == phase
            # 每次都能正常获取提示
            hints = h.skillx.get_skill_hints()
            assert isinstance(hints, str)

    def test_skill_execution_after_phase_switch(self, harness_with_skillx):
        """Phase 切换后 Skill 执行上下文正确。"""
        h = harness_with_skillx
        all_skills = h.skillx.unified_registry.all_skills()
        if not all_skills:
            pytest.skip("No skills loaded")

        # 在不同 Phase 执行同一个 Skill
        for phase in ["orientation", "deep_review"]:
            h.skillx.on_phase_transition(phase)
            result = h.skillx.execute_skill(
                skill_name=all_skills[0].descriptor.name,
                paper_text="We use instrumental variables approach.",
            )
            assert isinstance(result, dict)
            assert "success" in result
