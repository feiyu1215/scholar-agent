"""
tests/test_v2_phases.py — Phase FSM + phase-aware 工具过滤测试

验证:
    - FSM 状态管理正确
    - 转换前置条件生效
    - phase-aware 工具过滤
    - 建议转换逻辑
    - ToolRegistry 集成
"""

import pytest
from core.phases import Phase, PhaseFSM, TransitionResult
from core.tools import ToolRegistry


# ==============================================================
# TestPhaseFSM: FSM 核心逻辑
# ==============================================================

class TestPhaseFSM:
    """Phase FSM 核心逻辑测试。"""

    def test_initial_state(self):
        """FSM 初始状态为 INITIAL_SCAN。"""
        fsm = PhaseFSM()
        assert fsm.current_phase == Phase.INITIAL_SCAN
        assert fsm.phase_name == "initial_scan"
        assert fsm.transition_count == 0
        assert fsm.history == []

    def test_custom_initial_phase(self):
        """支持自定义初始阶段。"""
        fsm = PhaseFSM(initial_phase=Phase.DEEP_REVIEW)
        assert fsm.current_phase == Phase.DEEP_REVIEW

    def test_transition_scan_to_deep_review_success(self):
        """INITIAL_SCAN → DEEP_REVIEW: 满足前置条件（已读 >= 2 sections）。"""
        fsm = PhaseFSM()
        result = fsm.request_transition(
            Phase.DEEP_REVIEW, sections_read=3
        )
        assert result.allowed is True
        assert result.from_phase == Phase.INITIAL_SCAN
        assert result.to_phase == Phase.DEEP_REVIEW
        assert fsm.current_phase == Phase.DEEP_REVIEW
        assert fsm.transition_count == 1
        assert fsm.history == [Phase.INITIAL_SCAN]

    def test_transition_scan_to_deep_review_with_nudge(self):
        """INITIAL_SCAN → DEEP_REVIEW: 条件不满足时仍允许，但附带 nudge。"""
        fsm = PhaseFSM()
        result = fsm.request_transition(
            Phase.DEEP_REVIEW, sections_read=1
        )
        # W2: 去 block 化 — 永远允许，但附带 nudge
        assert result.allowed is True
        assert "⚠️" in result.reason
        assert fsm.current_phase == Phase.DEEP_REVIEW  # 转换执行了

    def test_transition_deep_review_to_editing_success(self):
        """DEEP_REVIEW → EDITING: 满足前置条件（有 verified finding）。"""
        fsm = PhaseFSM(initial_phase=Phase.DEEP_REVIEW)
        result = fsm.request_transition(
            Phase.EDITING, verified_findings=2
        )
        assert result.allowed is True
        assert fsm.current_phase == Phase.EDITING

    def test_transition_deep_review_to_editing_with_nudge(self):
        """DEEP_REVIEW → EDITING: 条件不满足时仍允许，但附带 nudge。"""
        fsm = PhaseFSM(initial_phase=Phase.DEEP_REVIEW)
        result = fsm.request_transition(
            Phase.EDITING, verified_findings=0
        )
        # W2: 去 block 化 — 永远允许，但附带 nudge
        assert result.allowed is True
        assert "⚠️" in result.reason
        assert fsm.current_phase == Phase.EDITING  # 转换执行了

    def test_transition_to_synthesis_always_allowed(self):
        """→ SYNTHESIS: 从 DEEP_REVIEW 或 EDITING 都允许（宽松）。"""
        fsm = PhaseFSM(initial_phase=Phase.DEEP_REVIEW)
        result = fsm.request_transition(Phase.SYNTHESIS)
        assert result.allowed is True
        assert fsm.current_phase == Phase.SYNTHESIS

    def test_transition_backward_allowed(self):
        """允许回退：EDITING → DEEP_REVIEW。"""
        fsm = PhaseFSM(initial_phase=Phase.EDITING)
        result = fsm.request_transition(Phase.DEEP_REVIEW)
        assert result.allowed is True
        assert fsm.current_phase == Phase.DEEP_REVIEW

    def test_transition_to_same_phase_rejected(self):
        """请求转换到当前阶段被拒绝。"""
        fsm = PhaseFSM()
        result = fsm.request_transition(Phase.INITIAL_SCAN)
        assert result.allowed is False
        assert "Already in" in result.reason

    def test_force_transition_bypasses_checks(self):
        """force_transition 跳过前置条件检查。"""
        fsm = PhaseFSM()
        fsm.force_transition(Phase.EDITING)  # 正常情况下不允许直接跳
        assert fsm.current_phase == Phase.EDITING
        assert fsm.transition_count == 1

    def test_history_tracks_transitions(self):
        """历史记录多次转换。"""
        fsm = PhaseFSM()
        fsm.force_transition(Phase.DEEP_REVIEW)
        fsm.force_transition(Phase.EDITING)
        fsm.force_transition(Phase.SYNTHESIS)
        assert fsm.history == [
            Phase.INITIAL_SCAN,
            Phase.DEEP_REVIEW,
            Phase.EDITING,
        ]
        assert fsm.transition_count == 3


# ==============================================================
# TestPhaseSuggest: 转换建议
# ==============================================================

class TestPhaseSuggest:
    """转换建议逻辑测试。"""

    def test_suggest_deep_review_after_enough_reading(self):
        """INITIAL_SCAN 阶段：读了 >= 3 sections 时建议进入 DEEP_REVIEW。"""
        fsm = PhaseFSM()
        suggestion = fsm.suggest_transition(sections_read=3)
        assert suggestion == Phase.DEEP_REVIEW

    def test_no_suggest_if_not_enough_reading(self):
        """INITIAL_SCAN 阶段：读得不够，不建议转换。"""
        fsm = PhaseFSM()
        suggestion = fsm.suggest_transition(sections_read=1)
        assert suggestion is None

    def test_suggest_synthesis_after_stagnation(self):
        """DEEP_REVIEW 阶段：连续 3 轮无新发现，建议 SYNTHESIS。"""
        fsm = PhaseFSM(initial_phase=Phase.DEEP_REVIEW)
        suggestion = fsm.suggest_transition(
            consecutive_no_new_findings=3, total_findings=2
        )
        assert suggestion == Phase.SYNTHESIS

    def test_no_suggest_synthesis_if_too_few_findings(self):
        """DEEP_REVIEW 阶段：虽然停滞但发现太少，不建议。"""
        fsm = PhaseFSM(initial_phase=Phase.DEEP_REVIEW)
        suggestion = fsm.suggest_transition(
            consecutive_no_new_findings=5, total_findings=1
        )
        assert suggestion is None

    def test_no_suggest_in_editing(self):
        """EDITING 阶段不主动建议。"""
        fsm = PhaseFSM(initial_phase=Phase.EDITING)
        suggestion = fsm.suggest_transition(
            consecutive_no_new_findings=10, total_findings=5
        )
        assert suggestion is None


# ==============================================================
# TestPhaseToolMap: 阶段-工具映射
# ==============================================================

class TestPhaseToolMap:
    """阶段-工具映射测试。"""

    def test_initial_scan_has_reading_tools(self):
        """INITIAL_SCAN 阶段包含阅读工具。"""
        fsm = PhaseFSM()
        tools = fsm.get_phase_tools()
        assert "read_section" in tools
        assert "list_sections" in tools

    def test_initial_scan_no_editing_tools(self):
        """INITIAL_SCAN 阶段没有编辑工具。"""
        fsm = PhaseFSM()
        tools = fsm.get_phase_tools()
        assert "apply_edit" not in tools
        assert "propose_edit" not in tools

    def test_deep_review_has_analysis_tools(self):
        """DEEP_REVIEW 阶段包含分析工具。"""
        tools = PhaseFSM().get_phase_tools(Phase.DEEP_REVIEW)
        assert "fetch_paper_detail" in tools
        assert "read_reference" in tools
        assert "read_section" in tools  # 仍可阅读

    def test_editing_has_edit_tools(self):
        """EDITING 阶段包含编辑工具。"""
        tools = PhaseFSM().get_phase_tools(Phase.EDITING)
        assert "apply_edit" in tools
        assert "propose_edit" in tools

    def test_synthesis_has_report_tools(self):
        """SYNTHESIS 阶段包含收尾工具。"""
        tools = PhaseFSM().get_phase_tools(Phase.SYNTHESIS)
        assert "generate_report" in tools

    def test_universal_tools_in_all_phases(self):
        """通用工具在所有阶段都可用。"""
        fsm = PhaseFSM()
        universal = {"update_findings", "done", "reflect_and_plan", "talk_to_user"}
        for phase in Phase:
            tools = fsm.get_phase_tools(phase)
            assert universal.issubset(tools), (
                f"Phase {phase.value} missing universal tools"
            )

    def test_request_phase_transition_tool_always_available(self):
        """request_phase_transition 工具在所有阶段可用。"""
        fsm = PhaseFSM()
        for phase in Phase:
            tools = fsm.get_phase_tools(phase)
            assert "request_phase_transition" in tools


# ==============================================================
# TestToolRegistryPhaseAware: ToolRegistry phase 过滤
# ==============================================================

class TestToolRegistryPhaseAware:
    """ToolRegistry phase-aware 过滤测试。"""

    def setup_method(self):
        """创建带 phase 标注的工具注册表。"""
        self.registry = ToolRegistry()
        # 通用工具（所有阶段）
        self.registry.register(
            "done", handler=lambda args: "ok", phases=None
        )
        # 仅 initial_scan + deep_review
        self.registry.register(
            "read_section", handler=lambda args: "content",
            phases={"initial_scan", "deep_review", "editing"}
        )
        # 仅 editing
        self.registry.register(
            "apply_edit", handler=lambda args: "edited",
            phases={"editing"}
        )
        # 仅 synthesis
        self.registry.register(
            "generate_report", handler=lambda args: "report",
            phases={"synthesis"}
        )

    def test_universal_tool_in_all_phases(self):
        """phases=None 的工具在所有阶段可见。"""
        for phase in ["initial_scan", "deep_review", "editing", "synthesis"]:
            tools = self.registry.get_tools_for_phase(phase)
            assert "done" in tools

    def test_phase_specific_tool_visible_in_correct_phase(self):
        """带 phases 的工具只在指定阶段可见。"""
        tools = self.registry.get_tools_for_phase("editing")
        assert "apply_edit" in tools
        assert "read_section" in tools  # 也标注了 editing

    def test_phase_specific_tool_hidden_in_other_phase(self):
        """带 phases 的工具在其他阶段不可见。"""
        tools = self.registry.get_tools_for_phase("initial_scan")
        assert "apply_edit" not in tools
        assert "generate_report" not in tools

    def test_get_tool_schemas_returns_correct_structure(self):
        """get_tool_schemas_for_phase 返回正确结构。"""
        schemas = self.registry.get_tool_schemas_for_phase("editing")
        names = [s["name"] for s in schemas]
        assert "done" in names
        assert "apply_edit" in names
        assert "generate_report" not in names
        # 每个 schema 都有 name 和 description
        for s in schemas:
            assert "name" in s
            assert "description" in s

    def test_execute_ignores_phase(self):
        """execute 不做 phase 检查——工具一旦被 LLM 调用，总是执行。"""
        # 这是重要的设计决策：phase 过滤只影响工具可见性（给 LLM 的 schema），
        # 不影响执行（万一 LLM "幻觉"出一个当前阶段不可见的工具调用，我们仍然执行）
        result = self.registry.execute("apply_edit", {})
        assert result == "edited"


# ==============================================================
# TestHarnessPhaseIntegration: Harness 集成测试
# ==============================================================

class TestHarnessPhaseIntegration:
    """Harness 中 PhaseFSM 的集成测试。"""

    def setup_method(self):
        """创建无论文的 Harness 实例。"""
        from core.harness import Harness
        self.harness = Harness()

    def test_harness_has_phase_fsm(self):
        """Harness 初始化后有 phase_fsm 属性。"""
        assert hasattr(self.harness, "phase_fsm")
        assert self.harness.phase_fsm.current_phase == Phase.INITIAL_SCAN

    def test_phase_transition_tool_works(self):
        """request_phase_transition 工具可通过 execute_tool 调用。"""
        # 先模拟读了几个 sections
        self.harness.state.sections_read = ["abstract", "introduction", "methods"]
        result = self.harness.execute_tool(
            "request_phase_transition",
            {"target_phase": "deep_review", "reason": "done scanning"},
        )
        assert "成功" in result
        assert self.harness.phase_fsm.current_phase == Phase.DEEP_REVIEW

    def test_phase_transition_tool_with_nudge(self):
        """前置条件不满足时，转换仍执行但附带 nudge 信息。"""
        result = self.harness.execute_tool(
            "request_phase_transition",
            {"target_phase": "deep_review"},
        )
        # W2: 去 block 化 — 转换成功但带 nudge
        assert "成功" in result
        assert "⚠️" in result or "注意" in result
        assert self.harness.phase_fsm.current_phase == Phase.DEEP_REVIEW

    def test_phase_transition_invalid_target(self):
        """无效的目标阶段名返回错误。"""
        result = self.harness.execute_tool(
            "request_phase_transition",
            {"target_phase": "nonexistent"},
        )
        assert "无效" in result

    def test_phase_transition_invalidates_cache(self):
        """阶段转换后，SectionRegistry 的 PHASE 缓存被清除。"""
        # domain_skills 使用 PHASE 缓存策略（cognitive_habits 在
        # GODEL_HABIT_PROGRESSIVE_ENABLED=True 时使用 NEVER 缓存）
        # 先触发一次 format_context 使 PHASE 缓存被填入
        self.harness.format_context()
        # domain_skills 是 PHASE 缓存，应该已经被缓存
        assert "domain_skills" in self.harness.assembler.registry._cache

        # 模拟读了 sections 后请求转换
        self.harness.state.sections_read = ["a", "b", "c"]
        self.harness.execute_tool(
            "request_phase_transition",
            {"target_phase": "deep_review"},
        )
        # PHASE 缓存应该被清除（domain_skills 不再在缓存中）
        assert "domain_skills" not in self.harness.assembler.registry._cache

    def test_format_context_uses_current_phase(self):
        """format_context 使用 FSM 的当前阶段。"""
        # 默认 initial_scan
        ctx1 = self.harness.format_context()
        # 强制切换到 deep_review
        self.harness.phase_fsm.force_transition(Phase.DEEP_REVIEW)
        ctx2 = self.harness.format_context()
        # 两次调用的 context 可能因为 habits 不同（PHASE 缓存）而不同
        # 至少确认没崩溃 + FSM 状态正确
        assert self.harness.phase_fsm.phase_name == "deep_review"
        assert isinstance(ctx1, str)
        assert isinstance(ctx2, str)

    def test_tool_registry_phase_filtering(self):
        """Harness 的 tool_registry 支持 phase 过滤。"""
        # initial_scan 阶段不应有 edit_section
        tools_scan = self.harness.tool_registry.get_tools_for_phase("initial_scan")
        assert "edit_section" not in tools_scan
        assert "read_section" in tools_scan

        # editing 阶段应有 edit_section
        tools_edit = self.harness.tool_registry.get_tools_for_phase("editing")
        assert "edit_section" in tools_edit
        assert "read_section" in tools_edit


# ==============================================================
# TestLoopPhaseFiltering: Loop 层的工具过滤集成测试
# ==============================================================

class TestLoopPhaseFiltering:
    """测试 cognitive_loop 中的 _filter_tools_by_phase 逻辑。"""

    def setup_method(self):
        """设置测试 Harness 和模拟全量工具列表。"""
        from core.harness import Harness
        self.harness = Harness()

        # 模拟全量工具列表（简化版，只需 name 字段用于过滤）
        self.full_tools = [
            {"name": "read_section", "description": "读取论文", "input_schema": {}},
            {"name": "update_findings", "description": "记录发现", "input_schema": {}},
            {"name": "talk_to_user", "description": "交流", "input_schema": {}},
            {"name": "edit_section", "description": "编辑论文", "input_schema": {}},
            {"name": "search_literature", "description": "搜索文献", "input_schema": {}},
            {"name": "fetch_paper_detail", "description": "获取论文详情", "input_schema": {}},
            {"name": "spawn_perspective", "description": "视角分裂", "input_schema": {}},
            {"name": "mark_complete", "description": "完成", "input_schema": {}},
            {"name": "reflect_and_plan", "description": "反思", "input_schema": {}},
            {"name": "request_phase_transition", "description": "请求阶段转换", "input_schema": {}},
        ]

    def test_initial_scan_filters_out_edit(self):
        """INITIAL_SCAN 阶段: edit_section 不可见。"""
        from core.loop import _filter_tools_by_phase

        # 默认是 initial_scan
        assert self.harness.phase_fsm.phase_name == "initial_scan"
        filtered = _filter_tools_by_phase(self.full_tools, self.harness)

        names = {t["name"] for t in filtered}
        assert "edit_section" not in names, "edit_section should be hidden in INITIAL_SCAN"
        assert "read_section" in names, "read_section should be visible in INITIAL_SCAN"
        assert "update_findings" in names, "universal tools should always be visible"

    def test_initial_scan_filters_out_spawn(self):
        """INITIAL_SCAN 阶段: spawn_perspective 不可见。"""
        from core.loop import _filter_tools_by_phase

        filtered = _filter_tools_by_phase(self.full_tools, self.harness)
        names = {t["name"] for t in filtered}
        assert "spawn_perspective" not in names, "spawn_perspective should be hidden in INITIAL_SCAN"

    def test_deep_review_has_analysis_tools(self):
        """DEEP_REVIEW 阶段: 分析工具可见，编辑工具不可见。"""
        from core.loop import _filter_tools_by_phase

        self.harness.phase_fsm.force_transition(Phase.DEEP_REVIEW)
        filtered = _filter_tools_by_phase(self.full_tools, self.harness)

        names = {t["name"] for t in filtered}
        assert "fetch_paper_detail" in names, "analysis tools visible in DEEP_REVIEW"
        assert "spawn_perspective" in names, "spawn_perspective visible in DEEP_REVIEW"
        assert "edit_section" not in names, "edit_section hidden in DEEP_REVIEW"

    def test_editing_has_edit_tools(self):
        """EDITING 阶段: 编辑工具可见。"""
        from core.loop import _filter_tools_by_phase

        self.harness.phase_fsm.force_transition(Phase.EDITING)
        filtered = _filter_tools_by_phase(self.full_tools, self.harness)

        names = {t["name"] for t in filtered}
        assert "edit_section" in names, "edit_section visible in EDITING"
        assert "read_section" in names, "read_section still visible in EDITING"

    def test_synthesis_has_no_edit(self):
        """SYNTHESIS 阶段: 编辑工具不可见。"""
        from core.loop import _filter_tools_by_phase

        self.harness.phase_fsm.force_transition(Phase.SYNTHESIS)
        filtered = _filter_tools_by_phase(self.full_tools, self.harness)

        names = {t["name"] for t in filtered}
        assert "edit_section" not in names, "edit_section hidden in SYNTHESIS"
        assert "spawn_perspective" in names, "spawn_perspective visible in SYNTHESIS"

    def test_filtered_count_varies_by_phase(self):
        """不同阶段应产生不同数量的可见工具。"""
        from core.loop import _filter_tools_by_phase

        counts = {}
        for phase in Phase:
            self.harness.phase_fsm.force_transition(phase)
            filtered = _filter_tools_by_phase(self.full_tools, self.harness)
            counts[phase.value] = len(filtered)

        # 至少有一个阶段的工具数和另一个不同（证明过滤生效）
        assert len(set(counts.values())) > 1, (
            f"All phases have same tool count: {counts}. Filtering not working."
        )

    def test_fallback_when_no_phase_fsm(self):
        """如果 harness 没有 phase_fsm，返回全量工具（容错）。"""
        from core.loop import _filter_tools_by_phase

        # 模拟缺少 phase_fsm 的 harness
        class MinimalHarness:
            pass

        minimal = MinimalHarness()
        result = _filter_tools_by_phase(self.full_tools, minimal)
        assert result is self.full_tools, "Should return full tools when no phase_fsm"

    def test_sub_perspective_tools_unaffected(self):
        """子视角的精简工具集不会被 phase 过滤清空。

        子 agent 在 deep_review 阶段被 spawn，sub_harness 继承父 harness 的阶段，
        因此所有 SUB_PERSPECTIVE_TOOLS 在 deep_review 阶段应该都可见。
        """
        from core.loop import _filter_tools_by_phase
        from core.identity import SUB_PERSPECTIVE_TOOLS

        # 子 agent 只在 deep_review 阶段被 spawn，先将父 harness 切到 deep_review
        self.harness.phase_fsm.force_transition(Phase.DEEP_REVIEW)
        sub_harness = self.harness.create_sub_harness(["introduction"])

        # 验证子 harness 继承了父的 deep_review 阶段
        assert sub_harness.phase_fsm.phase_name == "deep_review", (
            f"Sub-harness should inherit parent phase, got {sub_harness.phase_fsm.phase_name}"
        )

        filtered = _filter_tools_by_phase(SUB_PERSPECTIVE_TOOLS, sub_harness)

        # 所有子视角工具在 deep_review 阶段应该都可见
        assert len(filtered) == len(SUB_PERSPECTIVE_TOOLS), (
            f"Sub-perspective tools should all pass filter, got {len(filtered)}/{len(SUB_PERSPECTIVE_TOOLS)}"
        )


# ==============================================================
# TestAutoPhaseTransition: 自动 Phase 转换逻辑
# ==============================================================

class TestAutoPhaseTransition:
    """验证 loop.py 中的 _try_auto_phase_transition 自动转换逻辑。"""

    @pytest.fixture
    def harness(self, tmp_path):
        """创建包含论文数据的 harness。"""
        paper = tmp_path / "paper.md"
        paper.write_text("# Abstract\n\nTest paper.\n\n# Methods\n\nSome methods.\n\n# Results\n\nSome results.")
        from core.harness import Harness
        h = Harness(paper_path=str(paper))
        return h

    def test_no_transition_when_insufficient_reads(self, harness):
        """读 section 不足时不转换。"""
        from core.loop import _try_auto_phase_transition
        # 没有读任何 section
        assert harness.phase_fsm.current_phase == Phase.INITIAL_SCAN
        result = _try_auto_phase_transition(harness, verbose=False)
        assert result is False
        assert harness.phase_fsm.current_phase == Phase.INITIAL_SCAN

    def test_transition_with_3_sections_and_1_finding(self, harness):
        """读了 3 个 sections 且有 1 条 finding 时自动转换。"""
        from core.loop import _try_auto_phase_transition
        # 模拟读了 3 个 sections
        harness.state.sections_read = {"abstract", "methods", "results"}
        # 模拟有 1 条 finding
        harness.state.findings = [{"finding": "test", "priority": "medium", "status": "verified"}]

        result = _try_auto_phase_transition(harness, verbose=False)
        assert result is True
        assert harness.phase_fsm.current_phase == Phase.DEEP_REVIEW

    def test_transition_with_5_sections_no_findings(self, harness):
        """读了 5 个 sections 无条件转换（即使没有 finding）。"""
        from core.loop import _try_auto_phase_transition
        harness.state.sections_read = {"abstract", "intro", "methods", "results", "discussion"}
        harness.state.findings = []

        result = _try_auto_phase_transition(harness, verbose=False)
        assert result is True
        assert harness.phase_fsm.current_phase == Phase.DEEP_REVIEW

    def test_no_double_transition(self, harness):
        """已在 DEEP_REVIEW 时不重复转换。"""
        from core.loop import _try_auto_phase_transition
        harness.phase_fsm.force_transition(Phase.DEEP_REVIEW)
        harness.state.sections_read = {"a", "b", "c", "d", "e"}
        harness.state.findings = [{"finding": "x", "priority": "high", "status": "verified"}]

        result = _try_auto_phase_transition(harness, verbose=False)
        assert result is False
        # 仍在 DEEP_REVIEW
        assert harness.phase_fsm.current_phase == Phase.DEEP_REVIEW

    def test_transition_only_from_initial_scan(self, harness):
        """从 EDITING 等其他阶段不触发自动转换。"""
        from core.loop import _try_auto_phase_transition
        harness.phase_fsm.force_transition(Phase.EDITING)
        harness.state.sections_read = {"a", "b", "c", "d", "e", "f"}
        harness.state.findings = [{"finding": "x", "priority": "high", "status": "verified"}] * 5

        result = _try_auto_phase_transition(harness, verbose=False)
        assert result is False
        assert harness.phase_fsm.current_phase == Phase.EDITING

    def test_3_sections_without_finding_no_transition(self, harness):
        """读了 3 个 sections 但没有 finding 时不转换。"""
        from core.loop import _try_auto_phase_transition
        harness.state.sections_read = {"abstract", "methods", "results"}
        harness.state.findings = []

        result = _try_auto_phase_transition(harness, verbose=False)
        assert result is False
        assert harness.phase_fsm.current_phase == Phase.INITIAL_SCAN
