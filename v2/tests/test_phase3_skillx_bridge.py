"""
Phase 3 SkillX Bridge 测试 — 向后兼容层。

覆盖：
  - KnowledgeSkillAdapter: legacy SkillMeta (knowledge) → new Skill ABC
  - ActionSkillAdapter: legacy SkillMeta (action) → new Skill ABC
  - UnifiedSkillRegistry: 合并新旧 registry 的统一查询接口
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from core.skills.base import (
    Skill,
    SkillContext,
    SkillDescriptor,
    SkillLevel,
    SkillResult,
)
from core.skills.bridge import (
    KnowledgeSkillAdapter,
    ActionSkillAdapter,
    UnifiedSkillRegistry,
)
from core.skill_registry import SkillMeta, ToolDef


# ==============================================================
# Mock Data: Legacy SkillMeta
# ==============================================================

def make_knowledge_meta():
    return SkillMeta(
        id="econ_methodology",
        type="knowledge",
        file="econ_methodology.md",
        name="Economics Methodology",
        description="Economics methodology knowledge base",
        tags=("economics", "methodology"),
        applicable_paper_types=("empirical", "theoretical"),
        applicable_phases=("DEEP_REVIEW",),
        token_estimate=400,
        priority_hint=80,
    )


def make_action_meta():
    return SkillMeta(
        id="citation_formatter",
        type="action",
        file="citation_formatter.md",
        name="Citation Formatter",
        description="Format citations to APA style",
        tags=("citation",),
        applicable_paper_types=("empirical",),
        applicable_phases=("EDITING",),
        token_estimate=150,
        priority_hint=50,
        tools=(
            ToolDef(
                name="format_citation",
                description="Format a citation",
                input_schema={"style": "string"},
                handler="handlers.citation.format",
            ),
        ),
    )


# ==============================================================
# New-style skill for comparison
# ==============================================================

class NewStyleSkill(Skill):
    _DESCRIPTOR = SkillDescriptor(
        name="new_style",
        level=SkillLevel.FUNCTIONAL,
        description="A native SkillX skill",
        applicable_phases=("deep_review",),
        token_cost_estimate=200,
    )

    @property
    def descriptor(self):
        return self._DESCRIPTOR

    def can_apply(self, ctx):
        return 0.7

    def execute(self, ctx):
        return SkillResult(success=True, output_data={"from": "new"})


# ==============================================================
# Tests: KnowledgeSkillAdapter
# ==============================================================

class TestKnowledgeSkillAdapter:
    @pytest.fixture
    def mock_registry(self):
        registry = MagicMock()
        registry.load_content.return_value = "# Methodology Guidelines\n\nCheck DID assumptions..."
        return registry

    @pytest.fixture
    def adapted(self, mock_registry):
        return KnowledgeSkillAdapter(make_knowledge_meta(), mock_registry)

    def test_is_skill_instance(self, adapted):
        assert isinstance(adapted, Skill)

    def test_descriptor_fields(self, adapted):
        d = adapted.descriptor
        assert d.name == "knowledge_econ_methodology"
        assert d.level == SkillLevel.FUNCTIONAL
        assert "deep_review" in d.applicable_phases
        assert d.token_cost_estimate == 400
        assert "knowledge" in d.tags
        assert "legacy" in d.tags

    def test_can_apply_phase_match(self, adapted):
        ctx = SkillContext(
            paper_text="some paper",
            current_phase="deep_review",
        )
        score = adapted.can_apply(ctx)
        assert score == 0.8

    def test_can_apply_phase_mismatch(self, adapted):
        ctx = SkillContext(
            paper_text="some paper",
            current_phase="synthesis",
        )
        score = adapted.can_apply(ctx)
        assert score == 0.3

    def test_can_apply_no_phase(self, adapted):
        ctx = SkillContext(paper_text="some paper")
        score = adapted.can_apply(ctx)
        # No current_phase set, so falls through to 0.3
        assert score == 0.3

    def test_execute_returns_content(self, adapted, mock_registry):
        ctx = SkillContext(
            paper_text="some paper",
            current_phase="deep_review",
        )
        result = adapted.execute(ctx)
        assert result.success is True
        assert "content" in result.output_data
        assert "Methodology Guidelines" in result.output_data["content"]
        mock_registry.load_content.assert_called_with("econ_methodology")

    def test_execute_failure(self, mock_registry):
        mock_registry.load_content.return_value = None
        adapted = KnowledgeSkillAdapter(make_knowledge_meta(), mock_registry)
        ctx = SkillContext(current_phase="deep_review")
        result = adapted.execute(ctx)
        assert result.success is False

    def test_get_instruction(self, adapted, mock_registry):
        """Knowledge skill content should be accessible as instruction."""
        instr = adapted.get_instruction()
        assert "Methodology Guidelines" in instr


# ==============================================================
# Tests: ActionSkillAdapter
# ==============================================================

class TestActionSkillAdapter:
    @pytest.fixture
    def mock_handler_loader(self):
        loader = MagicMock()
        handler_fn = MagicMock(return_value="formatted citation")
        loader.load.return_value = handler_fn
        return loader

    @pytest.fixture
    def adapted(self, mock_handler_loader):
        return ActionSkillAdapter(make_action_meta(), mock_handler_loader)

    def test_is_skill_instance(self, adapted):
        assert isinstance(adapted, Skill)

    def test_descriptor_fields(self, adapted):
        d = adapted.descriptor
        assert d.name == "action_citation_formatter"
        assert d.level == SkillLevel.ATOMIC
        assert "editing" in d.applicable_phases
        assert d.token_cost_estimate == 150
        assert "action" in d.tags
        assert "legacy" in d.tags

    def test_can_apply_phase_match(self, adapted):
        ctx = SkillContext(
            paper_text="text with citations",
            current_phase="editing",
        )
        score = adapted.can_apply(ctx)
        assert score == 0.7

    def test_can_apply_phase_mismatch(self, adapted):
        ctx = SkillContext(
            paper_text="text",
            current_phase="deep_review",
        )
        score = adapted.can_apply(ctx)
        assert score == 0.2

    def test_execute_success(self, adapted, mock_handler_loader):
        ctx = SkillContext(
            paper_text="text with citations",
            current_phase="editing",
        )
        result = adapted.execute(ctx)
        assert result.success is True
        assert "result" in result.output_data
        mock_handler_loader.load.assert_called_with("handlers.citation.format")

    def test_execute_handler_failure(self, mock_handler_loader):
        mock_handler_loader.load.side_effect = ImportError("handler not found")
        adapted = ActionSkillAdapter(make_action_meta(), mock_handler_loader)
        ctx = SkillContext(current_phase="editing")
        result = adapted.execute(ctx)
        assert result.success is False
        assert "handler" in result.error_message.lower()

    def test_execute_no_tools(self):
        """Action skill without tools should fail."""
        meta = SkillMeta(
            id="no_tools",
            type="action",
            file="",
            name="No Tools",
            description="",
            tags=(),
            applicable_paper_types=(),
            applicable_phases=(),
            token_estimate=50,
            priority_hint=10,
            tools=(),
        )
        loader = MagicMock()
        adapted = ActionSkillAdapter(meta, loader)
        ctx = SkillContext()
        result = adapted.execute(ctx)
        assert result.success is False


# ==============================================================
# Tests: UnifiedSkillRegistry
# ==============================================================

class TestUnifiedSkillRegistry:
    @pytest.fixture
    def mock_legacy_registry(self):
        registry = MagicMock()
        registry.all_skills = [make_knowledge_meta(), make_action_meta()]
        registry.load_content.return_value = "knowledge content"
        return registry

    @pytest.fixture
    def mock_handler_loader(self):
        loader = MagicMock()
        loader.load.return_value = MagicMock(return_value="result")
        return loader

    @pytest.fixture
    def unified(self, mock_legacy_registry, mock_handler_loader):
        return UnifiedSkillRegistry(
            skillx_skills=[NewStyleSkill()],
            legacy_registry=mock_legacy_registry,
            handler_loader=mock_handler_loader,
        )

    def test_all_skills_includes_native(self, unified):
        names = [s.descriptor.name for s in unified.all_skills()]
        assert "new_style" in names

    def test_all_skills_includes_adapted_knowledge(self, unified):
        names = [s.descriptor.name for s in unified.all_skills()]
        assert "knowledge_econ_methodology" in names

    def test_all_skills_includes_adapted_action(self, unified):
        names = [s.descriptor.name for s in unified.all_skills()]
        assert "action_citation_formatter" in names

    def test_native_skills(self, unified):
        native = unified.native_skills()
        assert len(native) == 1
        assert native[0].descriptor.name == "new_style"

    def test_adapted_skills(self, unified):
        adapted = unified.adapted_skills()
        assert len(adapted) == 2

    def test_get_by_name(self, unified):
        skill = unified.get_by_name("new_style")
        assert skill is not None
        assert skill.descriptor.name == "new_style"

    def test_get_by_name_adapted(self, unified):
        skill = unified.get_by_name("knowledge_econ_methodology")
        assert skill is not None
        assert isinstance(skill, KnowledgeSkillAdapter)

    def test_get_by_name_nonexistent(self, unified):
        assert unified.get_by_name("nonexistent") is None

    def test_get_by_level(self, unified):
        atomic_skills = unified.get_by_level(SkillLevel.ATOMIC)
        names = [s.descriptor.name for s in atomic_skills]
        assert "action_citation_formatter" in names
        assert "new_style" not in names

    def test_register_native(self, unified):
        class AnotherSkill(Skill):
            _DESCRIPTOR = SkillDescriptor(
                name="another", level=SkillLevel.ATOMIC, description="Another",
                token_cost_estimate=50,
            )

            @property
            def descriptor(self):
                return self._DESCRIPTOR

            def can_apply(self, ctx):
                return 0.5

            def execute(self, ctx):
                return SkillResult(success=True)

        unified.register_native(AnotherSkill())
        names = [s.descriptor.name for s in unified.all_skills()]
        assert "another" in names

    def test_register_native_duplicate_skipped(self, unified):
        initial_count = len(unified.all_skills())
        unified.register_native(NewStyleSkill())
        assert len(unified.all_skills()) == initial_count

    def test_no_legacy_registry(self):
        """Should work fine without legacy registry."""
        unified = UnifiedSkillRegistry(skillx_skills=[NewStyleSkill()])
        assert len(unified.all_skills()) == 1
        assert len(unified.adapted_skills()) == 0
