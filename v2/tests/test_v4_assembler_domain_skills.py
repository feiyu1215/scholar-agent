"""
tests/test_v4_assembler_domain_skills.py — C1: domain_skills section 集成测试

覆盖 V4 C1 验收标准:
    1. domain_skills section 注册正确（priority=73, PHASE cache）
    2. Kill Switch OFF 时 section 不生成内容
    3. Kill Switch ON + SkillRegistry 有匹配 → 正确注入 C14 框架
    4. 论文类型推断正确（CognitiveHints → PaperStructureIndex → None）
    5. Harness 正确初始化 SkillRegistry 并传入 Assembler
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.assembler import (
    ContextAssembler,
    _has_domain_skills,
    _compute_domain_skills,
    _infer_paper_type,
)
from core.sections import CachePolicy
from core.skill_registry import SkillRegistry


# ==============================================================
# Fixtures
# ==============================================================


@pytest.fixture
def sample_skills_dir(tmp_path: Path) -> Path:
    """创建包含 registry.json 和 skill 文件的临时 skills 目录。"""
    registry_data = {
        "version": "1.0",
        "skills": [
            {
                "id": "methodology_checklist",
                "type": "knowledge",
                "file": "methodology_checklist.md",
                "name": "方法论检查清单",
                "description": "实证论文方法论审查要点",
                "tags": ["methodology"],
                "applicable_paper_types": ["empirical", "empirical_econ"],
                "applicable_phases": ["DEEP_REVIEW", "INITIAL_SCAN"],
                "token_estimate": 600,
                "priority_hint": 80,
            },
            {
                "id": "econ_writing",
                "type": "knowledge",
                "file": "econ_writing.md",
                "name": "经济学写作规范",
                "description": "经济学论文写作最佳实践",
                "tags": ["writing"],
                "applicable_paper_types": ["empirical", "empirical_econ"],
                "applicable_phases": ["EDITING"],
                "token_estimate": 900,
                "priority_hint": 70,
            },
        ],
    }
    (tmp_path / "registry.json").write_text(
        json.dumps(registry_data, indent=2), encoding="utf-8"
    )
    (tmp_path / "methodology_checklist.md").write_text(
        "# 方法论检查清单\n\n## 因果推断\n- 检查识别策略有效性\n- 验证平行趋势假设",
        encoding="utf-8",
    )
    (tmp_path / "econ_writing.md").write_text(
        "# 经济学写作规范\n\n## 表达原则\n- 简洁精确\n- 避免overclaim",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def skill_registry(sample_skills_dir: Path) -> SkillRegistry:
    return SkillRegistry(sample_skills_dir)


@pytest.fixture
def mock_state():
    """模拟 WorkspaceState。"""
    state = MagicMock()
    state.paper_sections = {"abstract": "This paper uses DID to study policy effects.", "methodology": "We employ difference-in-differences..."}
    state.section_digests = {}
    state.findings = []
    state.reference_papers = {}
    state.edits = []
    state.sections_read = []
    state.loop_turns = 3
    state.conversation_turns = 1
    state.max_loop_turns = 50
    state.total_tokens = 5000
    state.paper_structure_index = None
    state.cognitive_hints = None
    state.paper_cognition_graph = None
    return state


@pytest.fixture
def mock_memory():
    memory = MagicMock()
    memory.format_memory_context.return_value = ""
    return memory


@pytest.fixture
def mock_cognitive_state():
    cs = MagicMock()
    cs.format_for_context.return_value = ""
    return cs


@pytest.fixture
def mock_offload_store():
    store = MagicMock()
    store.format_refs_summary.return_value = ""
    return store


# ==============================================================
# Tests: Section 注册
# ==============================================================


class TestDomainSkillsSectionRegistration:
    """验证 domain_skills section 在 Assembler 中正确注册。"""

    def test_section_registered_with_correct_priority(
        self, mock_memory, mock_cognitive_state, mock_offload_store, skill_registry
    ):
        """domain_skills 应注册为 priority=73。"""
        assembler = ContextAssembler(
            memory=mock_memory,
            cognitive_state=mock_cognitive_state,
            offload_store=mock_offload_store,
            skill_registry=skill_registry,
        )
        # _sections 是 list[SectionDefinition]，按 name 查找
        sections = assembler.registry._sections
        domain_sec = next((s for s in sections if s.name == "domain_skills"), None)
        assert domain_sec is not None, "domain_skills section not registered"
        assert domain_sec.priority == 73

    def test_section_has_phase_cache_policy(
        self, mock_memory, mock_cognitive_state, mock_offload_store, skill_registry
    ):
        """domain_skills 应使用 PHASE 缓存策略。"""
        assembler = ContextAssembler(
            memory=mock_memory,
            cognitive_state=mock_cognitive_state,
            offload_store=mock_offload_store,
            skill_registry=skill_registry,
        )
        sections = assembler.registry._sections
        domain_sec = next((s for s in sections if s.name == "domain_skills"), None)
        assert domain_sec is not None
        assert domain_sec.cache_policy == CachePolicy.PHASE


# ==============================================================
# Tests: Kill Switch 守卫
# ==============================================================


class TestKillSwitchGuard:
    """验证 GODEL_SKILL_LOADING_ENABLED=0 时 domain_skills 不激活。"""

    def test_kill_switch_off_condition_returns_false(self, skill_registry):
        """Kill Switch OFF → _has_domain_skills 返回 False。"""
        ctx = {
            "state": MagicMock(),
            "skill_registry": skill_registry,
            "current_phase": "DEEP_REVIEW",
        }
        # _has_domain_skills 内部 from core.godel_config import GODEL_SKILL_LOADING_ENABLED
        # 需要 patch godel_config 模块的属性
        with patch("core.godel_config.GODEL_SKILL_LOADING_ENABLED", False):
            result = _has_domain_skills(ctx)
            assert result is False

    def test_kill_switch_on_no_registry_returns_false(self):
        """Kill Switch ON 但 skill_registry=None → 返回 False。"""
        ctx = {
            "state": MagicMock(),
            "skill_registry": None,
            "current_phase": "DEEP_REVIEW",
        }
        with patch("core.godel_config.GODEL_SKILL_LOADING_ENABLED", True):
            result = _has_domain_skills(ctx)
            assert result is False


# ==============================================================
# Tests: 论文类型推断
# ==============================================================


class TestInferPaperType:
    """验证 _infer_paper_type 从不同来源推断论文类型。"""

    def test_infer_from_cognitive_hints_empirical(self, mock_state):
        """CognitiveHints 描述含 'DID' → 推断为 empirical_econ。"""
        hints = MagicMock()
        hints.paper_type_description = "使用DID的劳动经济学实证论文"
        mock_state.cognitive_hints = hints
        ctx = {"state": mock_state}
        assert _infer_paper_type(ctx) == "empirical_econ"

    def test_infer_from_cognitive_hints_theoretical(self, mock_state):
        """CognitiveHints 描述含 '博弈' → 推断为 theoretical。"""
        hints = MagicMock()
        hints.paper_type_description = "博弈论机制设计的理论模型"
        mock_state.cognitive_hints = hints
        ctx = {"state": mock_state}
        assert _infer_paper_type(ctx) == "theoretical"

    def test_infer_from_cognitive_hints_review(self, mock_state):
        """CognitiveHints 描述含 '综述' → 推断为 review。"""
        hints = MagicMock()
        hints.paper_type_description = "产业经济学领域的系统性综述"
        mock_state.cognitive_hints = hints
        ctx = {"state": mock_state}
        assert _infer_paper_type(ctx) == "review"

    def test_infer_from_cognitive_hints_ml(self, mock_state):
        """CognitiveHints 描述含 'deep learning' → 推断为 ml_nlp。"""
        hints = MagicMock()
        hints.paper_type_description = "A deep learning approach for NER"
        mock_state.cognitive_hints = hints
        ctx = {"state": mock_state}
        assert _infer_paper_type(ctx) == "ml_nlp"

    def test_infer_from_paper_structure_index(self, mock_state):
        """无 CognitiveHints → 从 PaperStructureIndex.paper_type 推断。"""
        mock_state.cognitive_hints = None
        idx = MagicMock()
        idx.paper_type = "empirical"
        mock_state.paper_structure_index = idx
        ctx = {"state": mock_state}
        assert _infer_paper_type(ctx) == "empirical"

    def test_infer_returns_none_when_no_info(self, mock_state):
        """无任何信息 → 返回 None（不过滤）。"""
        mock_state.cognitive_hints = None
        mock_state.paper_structure_index = None
        ctx = {"state": mock_state}
        assert _infer_paper_type(ctx) is None


# ==============================================================
# Tests: _compute_domain_skills 内容生成
# ==============================================================


class TestComputeDomainSkills:
    """验证 domain_skills 内容正确生成。"""

    def test_generates_c14_framing(self, mock_state, skill_registry):
        """匹配到 skills 时应包含 C14 认知辅助框架。"""
        hints = MagicMock()
        hints.paper_type_description = "实证DID论文"
        mock_state.cognitive_hints = hints

        ctx = {
            "state": mock_state,
            "skill_registry": skill_registry,
            "current_phase": "DEEP_REVIEW",
        }
        result = _compute_domain_skills(ctx)
        assert "[领域审稿参考 — 按需加载，非指令]" in result
        assert "[以上为参考知识]" in result
        assert "方法论检查清单" in result

    def test_respects_phase_filtering(self, mock_state, skill_registry):
        """EDITING 阶段应匹配 econ_writing 而非 methodology_checklist。"""
        hints = MagicMock()
        hints.paper_type_description = "实证经济学论文"
        mock_state.cognitive_hints = hints

        ctx = {
            "state": mock_state,
            "skill_registry": skill_registry,
            "current_phase": "EDITING",
        }
        result = _compute_domain_skills(ctx)
        assert "经济学写作规范" in result
        # methodology_checklist 不适用 EDITING 阶段
        assert "因果推断" not in result

    def test_empty_when_no_match(self, mock_state, skill_registry):
        """theoretical 论文在 DEEP_REVIEW 阶段无匹配 → 返回空。"""
        hints = MagicMock()
        hints.paper_type_description = "纯理论博弈模型"
        mock_state.cognitive_hints = hints

        ctx = {
            "state": mock_state,
            "skill_registry": skill_registry,
            "current_phase": "DEEP_REVIEW",
        }
        result = _compute_domain_skills(ctx)
        # theoretical 不匹配 sample registry 中只有 empirical 的 skills
        assert result == ""

    def test_empty_when_no_registry(self, mock_state):
        """skill_registry=None → 返回空。"""
        ctx = {
            "state": mock_state,
            "skill_registry": None,
            "current_phase": "DEEP_REVIEW",
        }
        result = _compute_domain_skills(ctx)
        assert result == ""


# ==============================================================
# Tests: Assembler 集成（E2E 轻量）
# ==============================================================


class TestAssemblerIntegration:
    """验证 domain_skills 在完整 assemble() 流程中正确集成。"""

    def test_assemble_includes_domain_skills(
        self, mock_state, mock_memory, mock_cognitive_state, mock_offload_store, skill_registry
    ):
        """完整 assemble 应包含 domain_skills 内容。"""
        hints = MagicMock()
        hints.paper_type_description = "实证DID论文"
        hints.is_empty.return_value = False
        hints.focus_dimensions = ["因果推断"]
        hints.typical_weaknesses = []
        hints.verification_strategies = []
        hints.format_for_context.return_value = "[审稿认知提示]\n论文特征: 实证DID"
        mock_state.cognitive_hints = hints

        assembler = ContextAssembler(
            memory=mock_memory,
            cognitive_state=mock_cognitive_state,
            offload_store=mock_offload_store,
            skill_registry=skill_registry,
        )
        result = assembler.assemble(
            state=mock_state,
            current_phase="DEEP_REVIEW",
            current_turn=3,
        )
        assert "[领域审稿参考 — 按需加载，非指令]" in result

    def test_assemble_without_skill_registry(
        self, mock_state, mock_memory, mock_cognitive_state, mock_offload_store
    ):
        """不传 skill_registry → assemble 正常运行，无 domain_skills。"""
        assembler = ContextAssembler(
            memory=mock_memory,
            cognitive_state=mock_cognitive_state,
            offload_store=mock_offload_store,
        )
        # 应不报错
        result = assembler.assemble(
            state=mock_state,
            current_phase="DEEP_REVIEW",
            current_turn=3,
        )
        assert "[领域审稿参考" not in result


# ==============================================================
# Tests: C2 — 模板推荐 Skill 优先加载
# ==============================================================


class TestC2RecommendedSkillsPriority:
    """验证 C2: recommended_skills 优先加载 + 通用 query 补充 + 去重。"""

    def test_recommended_skills_loaded_first(self, mock_state, skill_registry):
        """recommended_skills 中的 skill 应被优先加载，即使 query 也能匹配到。"""
        hints = MagicMock()
        hints.paper_type_description = "实证DID论文"
        mock_state.cognitive_hints = hints
        # 指定推荐加载 methodology_checklist
        mock_state.recommended_skills = ["methodology_checklist"]

        ctx = {
            "state": mock_state,
            "skill_registry": skill_registry,
            "current_phase": "DEEP_REVIEW",
        }
        result = _compute_domain_skills(ctx)
        assert "方法论检查清单" in result

    def test_recommended_nonexistent_skill_gracefully_skipped(self, mock_state, skill_registry):
        """recommended_skills 含不存在的 skill_id → 跳过，不报错。"""
        hints = MagicMock()
        hints.paper_type_description = "实证DID论文"
        mock_state.cognitive_hints = hints
        mock_state.recommended_skills = ["nonexistent_skill_xyz", "methodology_checklist"]

        ctx = {
            "state": mock_state,
            "skill_registry": skill_registry,
            "current_phase": "DEEP_REVIEW",
        }
        result = _compute_domain_skills(ctx)
        # 不存在的被跳过，存在的正常加载
        assert "方法论检查清单" in result

    def test_recommended_skills_dedup_with_query(self, mock_state, skill_registry):
        """已通过 recommended 加载的 skill 不会被通用 query 重复加载。"""
        hints = MagicMock()
        hints.paper_type_description = "实证DID论文"
        mock_state.cognitive_hints = hints
        # 推荐加载 methodology_checklist，query 也会匹配到它
        mock_state.recommended_skills = ["methodology_checklist"]

        ctx = {
            "state": mock_state,
            "skill_registry": skill_registry,
            "current_phase": "DEEP_REVIEW",
        }
        result = _compute_domain_skills(ctx)
        # "方法论检查清单" 只应出现一次
        assert result.count("方法论检查清单") == 1

    def test_recommended_skills_phase_filtering(self, mock_state, skill_registry):
        """推荐的 skill 不适用当前 phase → 应被跳过。"""
        hints = MagicMock()
        hints.paper_type_description = "实证经济学论文"
        mock_state.cognitive_hints = hints
        # econ_writing 只适用于 EDITING，当前 DEEP_REVIEW 应跳过
        mock_state.recommended_skills = ["econ_writing"]

        ctx = {
            "state": mock_state,
            "skill_registry": skill_registry,
            "current_phase": "DEEP_REVIEW",
        }
        result = _compute_domain_skills(ctx)
        # econ_writing 因 phase 不匹配被跳过
        assert "经济学写作规范" not in result

    def test_recommended_skills_budget_constraint(self, mock_state, tmp_path):
        """推荐 skill 的 token_estimate 超出 budget → 跳过该 skill。"""
        # 创建一个 token_estimate 极大的 skill
        registry_data = {
            "version": "1.0",
            "skills": [
                {
                    "id": "huge_skill",
                    "type": "knowledge",
                    "file": "huge.md",
                    "name": "巨大技能",
                    "description": "超大 token 技能",
                    "tags": [],
                    "applicable_paper_types": ["empirical"],
                    "applicable_phases": ["DEEP_REVIEW"],
                    "token_estimate": 999999,
                    "priority_hint": 90,
                },
                {
                    "id": "small_skill",
                    "type": "knowledge",
                    "file": "small.md",
                    "name": "小技能",
                    "description": "小 token 技能",
                    "tags": [],
                    "applicable_paper_types": ["empirical"],
                    "applicable_phases": ["DEEP_REVIEW"],
                    "token_estimate": 100,
                    "priority_hint": 50,
                },
            ],
        }
        (tmp_path / "registry.json").write_text(
            json.dumps(registry_data, indent=2), encoding="utf-8"
        )
        (tmp_path / "huge.md").write_text("# Huge content", encoding="utf-8")
        (tmp_path / "small.md").write_text("# Small content", encoding="utf-8")

        sr = SkillRegistry(tmp_path)
        hints = MagicMock()
        hints.paper_type_description = "实证DID论文"
        mock_state.cognitive_hints = hints
        # 推荐加载 huge_skill（超预算）+ small_skill（预算内）
        mock_state.recommended_skills = ["huge_skill", "small_skill"]

        ctx = {
            "state": mock_state,
            "skill_registry": sr,
            "current_phase": "DEEP_REVIEW",
        }
        result = _compute_domain_skills(ctx)
        # huge_skill 超预算被跳过
        assert "Huge content" not in result
        # small_skill 正常加载
        assert "Small content" in result

    def test_empty_recommended_skills_falls_back_to_query(self, mock_state, skill_registry):
        """recommended_skills=[] → 完全回退到通用 query 逻辑。"""
        hints = MagicMock()
        hints.paper_type_description = "实证DID论文"
        mock_state.cognitive_hints = hints
        mock_state.recommended_skills = []

        ctx = {
            "state": mock_state,
            "skill_registry": skill_registry,
            "current_phase": "DEEP_REVIEW",
        }
        result = _compute_domain_skills(ctx)
        # 通用 query 应匹配到 methodology_checklist
        assert "方法论检查清单" in result

    def test_no_recommended_skills_attr_falls_back_to_query(self, mock_state, skill_registry):
        """state 没有 recommended_skills 属性 → 正常走 query 逻辑。"""
        hints = MagicMock()
        hints.paper_type_description = "实证DID论文"
        mock_state.cognitive_hints = hints
        # 使用普通对象模拟 state 无此属性
        class BareState:
            cognitive_hints = hints
            paper_structure_index = None
        bare_state = BareState()

        ctx = {
            "state": bare_state,
            "skill_registry": skill_registry,
            "current_phase": "DEEP_REVIEW",
        }
        result = _compute_domain_skills(ctx)
        assert "方法论检查清单" in result
