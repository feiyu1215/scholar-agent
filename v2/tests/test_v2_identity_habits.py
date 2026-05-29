"""
tests/test_v2_identity_habits.py — Phase 3.3/3.4 测试

验证:
1. STATIC_IDENTITY 的内容和大小约束
2. COGNITIVE_HABITS 的结构完整性
3. HabitSelector 的选取逻辑（阶段过滤、优先级排序、数量截断）
4. build_system_prompt_v2 的组装逻辑
5. identity + habits 在 assembler 中的注册和输出
"""

import pytest

from core.identity_static import STATIC_IDENTITY, build_system_prompt_v2
from core.habits import (
    CognitiveHabit,
    COGNITIVE_HABITS,
    HabitSelector,
)
from core.sections import SectionRegistry, CachePolicy


# ============================================================
# STATIC_IDENTITY 测试
# ============================================================

class TestStaticIdentity:
    """静态身份区测试。"""

    def test_static_identity_exists_and_nonempty(self):
        """STATIC_IDENTITY 非空且包含关键内容。"""
        assert STATIC_IDENTITY
        assert len(STATIC_IDENTITY) > 100

    def test_static_identity_contains_core_elements(self):
        """静态身份包含核心身份要素。"""
        assert "审稿人" in STATIC_IDENTITY
        assert "NeurIPS" in STATIC_IDENTITY or "ICML" in STATIC_IDENTITY
        assert "claim" in STATIC_IDENTITY
        assert "{workspace_state}" in STATIC_IDENTITY

    def test_static_identity_size_constraint(self):
        """静态身份大小在合理范围内（~500字 = ~800-1200 字符）。"""
        # 蓝图目标: ~500 字核心身份
        # 允许一些余量（中文+英文混合）
        assert len(STATIC_IDENTITY) < 2000, f"Too large: {len(STATIC_IDENTITY)} chars"
        assert len(STATIC_IDENTITY) > 500, f"Too small: {len(STATIC_IDENTITY)} chars"

    def test_static_identity_does_not_contain_habits(self):
        """静态身份不包含具体的认知习惯（那些在 habits.py 中）。"""
        # 这些是习惯关键词，不应该在静态区
        assert "深度追查" not in STATIC_IDENTITY
        assert "完成前自检" not in STATIC_IDENTITY
        assert "视角分裂" not in STATIC_IDENTITY
        assert "主动反思" not in STATIC_IDENTITY

    def test_static_identity_has_workspace_placeholder(self):
        """静态身份包含 workspace_state 占位符。"""
        assert "{workspace_state}" in STATIC_IDENTITY

    def test_build_system_prompt_v2_basic(self):
        """build_system_prompt_v2 正确注入 workspace_state。"""
        result = build_system_prompt_v2(workspace_state="测试状态")
        assert "测试状态" in result
        assert "{workspace_state}" not in result

    def test_build_system_prompt_v2_with_habits(self):
        """build_system_prompt_v2 正确注入习惯文本。"""
        habits_text = "## 当前阶段的认知习惯\n\n- 测试习惯"
        result = build_system_prompt_v2(
            habits_text=habits_text,
            workspace_state="状态"
        )
        assert "测试习惯" in result
        assert "状态" in result

    def test_build_system_prompt_v2_habits_before_state(self):
        """习惯文本在 workspace_state 之前。"""
        habits_text = "HABITS_MARKER"
        result = build_system_prompt_v2(
            habits_text=habits_text,
            workspace_state="STATE_MARKER"
        )
        habits_pos = result.index("HABITS_MARKER")
        state_pos = result.index("STATE_MARKER")
        assert habits_pos < state_pos, "Habits should appear before workspace state"


# ============================================================
# COGNITIVE_HABITS 测试
# ============================================================

class TestCognitiveHabits:
    """认知习惯库测试。"""

    def test_habits_count(self):
        """习惯库包含合理数量的习惯（原始 19 条 ± 1）。"""
        # 拆分后可能是 19-20 条（use_chinese 作为独立条目）
        assert 18 <= len(COGNITIVE_HABITS) <= 25

    def test_all_habits_have_required_fields(self):
        """每条习惯都有完整的必填字段。"""
        for h in COGNITIVE_HABITS:
            assert h.id, f"Habit missing id"
            assert h.name, f"Habit {h.id} missing name"
            assert h.phases, f"Habit {h.id} missing phases"
            assert h.priority > 0, f"Habit {h.id} has invalid priority"
            assert h.content, f"Habit {h.id} missing content"

    def test_habit_ids_unique(self):
        """习惯 ID 唯一。"""
        ids = [h.id for h in COGNITIVE_HABITS]
        assert len(ids) == len(set(ids)), f"Duplicate IDs found: {ids}"

    def test_habits_cover_all_phases(self):
        """习惯库覆盖所有核心阶段。"""
        all_phases = set()
        for h in COGNITIVE_HABITS:
            all_phases.update(h.phases)
        # 至少覆盖这些核心阶段
        for phase in ["ORIENTATION", "DEEP_REVIEW", "SYNTHESIS", "EDITING", "COMPLETION"]:
            assert phase in all_phases, f"Phase {phase} not covered by any habit"

    def test_habits_priority_range(self):
        """习惯优先级在合理范围内。"""
        for h in COGNITIVE_HABITS:
            assert 0 < h.priority <= 100, f"Habit {h.id} priority {h.priority} out of range"


# ============================================================
# HabitSelector 测试
# ============================================================

class TestHabitSelector:
    """习惯选择器测试。"""

    def setup_method(self):
        self.selector = HabitSelector()

    def test_select_respects_phase_filter(self):
        """选取只返回适用于当前阶段的习惯。"""
        selected = self.selector.select(phase="ORIENTATION")
        for h in selected:
            assert "ORIENTATION" in h.phases, f"Habit {h.id} not applicable to ORIENTATION"

    def test_select_max_per_turn_limit(self):
        """选取数量不超过 max_per_turn。"""
        # DEEP_REVIEW 有很多习惯，应该被截断为 5
        selected = self.selector.select(phase="DEEP_REVIEW")
        assert len(selected) <= self.selector.max_per_turn

    def test_select_priority_ordering(self):
        """选取结果按优先级降序排列。"""
        selected = self.selector.select(phase="DEEP_REVIEW")
        priorities = [h.priority for h in selected]
        assert priorities == sorted(priorities, reverse=True)

    def test_select_empty_phase_returns_top_global(self):
        """无阶段信息时返回全局最高优先级的习惯。"""
        selected = self.selector.select(phase="")
        assert len(selected) == self.selector.max_per_turn
        assert len(selected) > 0

    def test_select_custom_max(self):
        """自定义 max_per_turn 生效。"""
        selector = HabitSelector(max_per_turn=3)
        selected = selector.select(phase="DEEP_REVIEW")
        assert len(selected) <= 3

    def test_select_and_format_returns_markdown(self):
        """格式化输出是有效的 Markdown。"""
        text = self.selector.select_and_format(phase="DEEP_REVIEW")
        assert text.startswith("## 当前阶段的认知习惯")
        assert "- **" in text  # 至少有一条习惯

    def test_select_and_format_empty_for_unknown_phase(self):
        """未知阶段使用 fallback（全量库），不返回空。"""
        text = self.selector.select_and_format(phase="NONEXISTENT_PHASE")
        # fallback: 用全量库
        assert text  # 不应为空

    def test_get_habit_by_id(self):
        """按 ID 查找习惯。"""
        h = self.selector.get_habit_by_id("skepticism_first")
        assert h is not None
        assert h.name == "质疑优先"

    def test_get_habit_by_id_not_found(self):
        """查找不存在的 ID 返回 None。"""
        h = self.selector.get_habit_by_id("nonexistent_habit")
        assert h is None

    def test_trigger_boost(self):
        """触发词匹配时对应习惯被优先选入。"""
        # 创建一个低优先级的习惯有 trigger
        test_habits = [
            CognitiveHabit(
                id="high_prio",
                name="高优先级",
                phases=["TEST"],
                priority=90,
                content="高优先级习惯",
            ),
            CognitiveHabit(
                id="low_prio_with_trigger",
                name="低优先级但有触发词",
                phases=["TEST"],
                priority=50,
                content="低优先级习惯",
                triggers=["special_context"],
            ),
        ]
        selector = HabitSelector(habits=test_habits, max_per_turn=1)

        # 无触发词时，高优先级胜出
        selected = selector.select(phase="TEST")
        assert selected[0].id == "high_prio"

        # 有触发词时，低优先级被提升（50+20=70 < 90，但展示触发机制有效）
        selected = selector.select(phase="TEST", triggers=["special_context"])
        # 70 still < 90, so high_prio still wins in this case
        assert selected[0].id == "high_prio"

        # 如果 bonus 够大（修改测试使差距更小）
        test_habits2 = [
            CognitiveHabit(id="a", name="A", phases=["T"], priority=60, content="A"),
            CognitiveHabit(
                id="b", name="B", phases=["T"], priority=50,
                content="B", triggers=["boost_me"]
            ),
        ]
        selector2 = HabitSelector(habits=test_habits2, max_per_turn=1)
        # Without trigger: A wins (60 > 50)
        assert selector2.select(phase="T")[0].id == "a"
        # With trigger: B gets +20 = 70 > 60, B wins
        assert selector2.select(phase="T", triggers=["boost_me"])[0].id == "b"


# ============================================================
# Assembler Integration 测试
# ============================================================

class TestAssemblerIntegration:
    """identity + habits 在 assembler 中的注册和输出测试。"""

    def test_assembler_includes_identity_section(self):
        """Assembler 输出包含静态身份内容。"""
        from core.assembler import ContextAssembler
        from unittest.mock import MagicMock

        memory = MagicMock()
        memory.format_memory_context.return_value = ""
        cognitive_state = MagicMock()
        cognitive_state.format_for_context.return_value = ""
        offload_store = MagicMock()
        offload_store.format_refs_summary.return_value = ""

        assembler = ContextAssembler(
            memory=memory,
            cognitive_state=cognitive_state,
            offload_store=offload_store,
        )

        # 创建一个最小的 state mock
        state = MagicMock()
        state.paper_sections = {}
        state.section_digests = {}
        state.findings = []
        state.reference_papers = {}
        state.edits = []
        state.loop_turns = 1
        state.max_loop_turns = 30
        state.conversation_turns = 1
        state.total_tokens = 100
        state.current_phase = "ORIENTATION"
        state.cognitive_hints = None
        state.paper_structure_index = None

        result = assembler.assemble(
            state=state,
            current_turn=1,
            current_phase="ORIENTATION",
            budget=15000,
        )

        # 身份应该在输出中
        assert "审稿人" in result
        assert "NeurIPS" in result or "ICML" in result

    def test_assembler_includes_habits_for_phase(self):
        """Assembler 输出包含当前阶段的认知习惯。"""
        from core.assembler import ContextAssembler
        from unittest.mock import MagicMock

        memory = MagicMock()
        memory.format_memory_context.return_value = ""
        cognitive_state = MagicMock()
        cognitive_state.format_for_context.return_value = ""
        offload_store = MagicMock()
        offload_store.format_refs_summary.return_value = ""

        assembler = ContextAssembler(
            memory=memory,
            cognitive_state=cognitive_state,
            offload_store=offload_store,
        )

        state = MagicMock()
        state.paper_sections = {}
        state.section_digests = {}
        state.findings = []
        state.reference_papers = {}
        state.edits = []
        state.loop_turns = 3
        state.max_loop_turns = 30
        state.conversation_turns = 2
        state.total_tokens = 500
        state.current_phase = "DEEP_REVIEW"
        state.cognitive_hints = None
        state.paper_structure_index = None

        result = assembler.assemble(
            state=state,
            current_turn=3,
            current_phase="DEEP_REVIEW",
            budget=15000,
        )

        # 应该包含 DEEP_REVIEW 阶段的习惯（完整版或渐进摘要版）
        assert "认知习惯" in result
        # 至少包含一条 DEEP_REVIEW 习惯的关键词
        assert "质疑" in result or "数据" in result or "方法论" in result

    def test_assembler_identity_has_highest_priority(self):
        """静态身份在输出的最前面（最高优先级）。"""
        from core.assembler import ContextAssembler
        from unittest.mock import MagicMock

        memory = MagicMock()
        memory.format_memory_context.return_value = ""
        cognitive_state = MagicMock()
        cognitive_state.format_for_context.return_value = ""
        offload_store = MagicMock()
        offload_store.format_refs_summary.return_value = ""

        assembler = ContextAssembler(
            memory=memory,
            cognitive_state=cognitive_state,
            offload_store=offload_store,
        )

        state = MagicMock()
        state.paper_sections = {"Abstract": "test paper content"}
        state.section_digests = {}
        state.sections_read = ["Abstract"]
        state.findings = [{"finding": "test", "priority": "high", "status": "verified"}]
        state.reference_papers = {}
        state.edits = []
        state.loop_turns = 5
        state.max_loop_turns = 30
        state.conversation_turns = 3
        state.total_tokens = 1000
        state.current_phase = "DEEP_REVIEW"
        state.cognitive_hints = None
        state.paper_structure_index = None

        result = assembler.assemble(
            state=state,
            current_turn=5,
            current_phase="DEEP_REVIEW",
            budget=15000,
        )

        # 身份应该出现在最前面
        identity_pos = result.index("审稿人")
        # 动态状态应该在后面
        if "论文已加载" in result:
            paper_pos = result.index("论文已加载")
            assert identity_pos < paper_pos, "Identity should come before paper overview"
