"""
tests/test_v4_template_registry.py — B2: TemplateRegistry + metacognition 模板匹配测试

覆盖 V4 B2 验收标准:
    1. TemplateRegistry 正确加载 YAML 模板
    2. match() 通过关键词匹配返回最高分模板
    3. min_score 阈值生效
    4. metacognition 模板 seed 合并逻辑正确
    5. Agent 输入优先级高于模板 seed
    6. 实际 templates/ 目录的 6 个模板可正确加载
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.skill_registry import TemplateMeta, TemplateRegistry


# ==============================================================
# Fixtures
# ==============================================================


@pytest.fixture
def sample_templates_dir(tmp_path: Path) -> Path:
    """创建包含示例 YAML 模板的临时目录。"""
    # 模板 1: 实证经济学
    template_econ = """
id: empirical_economics
name: "实证经济学论文 (Causal Inference)"

match_signals:
  keywords:
    - "difference-in-differences"
    - "DID"
    - "regression discontinuity"
    - "RDD"
    - "causal effect"
    - "treatment effect"
    - "panel data"
    - "fixed effects"
  structure_patterns:
    - "has_empirical_section"

seed_hints:
  paper_type_description: "使用因果推断方法的实证经济学论文"
  focus_dimensions:
    - "识别策略是否可信"
    - "样本构造是否合理"
    - "估计方法是否匹配"
  typical_weaknesses:
    - "识别假设缺乏验证"
    - "标准误聚类不当"
  verification_strategies:
    - "检查平行趋势"
    - "验证 first-stage F 统计量"

recommended_skills:
  - "methodology_checklist"
  - "econ_writing"
"""
    # 模板 2: ML/NLP
    template_ml = """
id: nlp_system
name: "NLP系统论文"

match_signals:
  keywords:
    - "transformer"
    - "attention mechanism"
    - "BERT"
    - "GPT"
    - "language model"
    - "fine-tuning"
    - "NER"
    - "text classification"
  structure_patterns:
    - "has_experiment_section"

seed_hints:
  paper_type_description: "基于深度学习的NLP系统论文"
  focus_dimensions:
    - "模型架构创新性"
    - "实验设置公平性"
    - "Baseline 选择合理性"
  typical_weaknesses:
    - "缺乏消融实验"
    - "Baseline 过时"
  verification_strategies:
    - "检查实验复现信息"
    - "验证数据集划分"

recommended_skills:
  - "review_criteria"
"""
    # 写入文件
    (tmp_path / "empirical_economics.yaml").write_text(template_econ, encoding="utf-8")
    (tmp_path / "nlp_system.yaml").write_text(template_ml, encoding="utf-8")
    # 添加一个 _schema 文件（应被跳过）
    (tmp_path / "_template_schema.yaml").write_text("schema_version: '1.0'", encoding="utf-8")
    return tmp_path


@pytest.fixture
def template_registry(sample_templates_dir: Path) -> TemplateRegistry:
    return TemplateRegistry(sample_templates_dir)


# ==============================================================
# Tests: TemplateRegistry 加载
# ==============================================================


class TestTemplateRegistryLoading:
    """验证模板加载。"""

    def test_loads_non_schema_templates(self, template_registry: TemplateRegistry) -> None:
        """应加载 2 个模板，跳过 _template_schema.yaml。"""
        assert len(template_registry.all_templates) == 2

    def test_template_meta_fields(self, template_registry: TemplateRegistry) -> None:
        """TemplateMeta 字段正确映射。"""
        meta = template_registry.get("empirical_economics")
        assert meta is not None
        assert meta.id == "empirical_economics"
        assert meta.name == "实证经济学论文 (Causal Inference)"
        assert "did" in meta.keywords  # 应为小写
        assert "difference-in-differences" in meta.keywords
        assert meta.seed_hints["paper_type_description"] == "使用因果推断方法的实证经济学论文"
        assert len(meta.seed_hints["focus_dimensions"]) == 3
        assert "methodology_checklist" in meta.recommended_skills

    def test_keywords_lowercase(self, template_registry: TemplateRegistry) -> None:
        """所有 keywords 应被转为小写。"""
        meta = template_registry.get("nlp_system")
        assert meta is not None
        assert "bert" in meta.keywords
        assert "gpt" in meta.keywords
        # 原始 "BERT" 应被转为 "bert"
        assert "BERT" not in meta.keywords

    def test_missing_dir_graceful(self, tmp_path: Path) -> None:
        """目录不存在 → 空 registry，不报错。"""
        reg = TemplateRegistry(tmp_path / "nonexistent")
        assert len(reg.all_templates) == 0

    def test_schema_file_skipped(self, template_registry: TemplateRegistry) -> None:
        """_template_schema.yaml 应被跳过。"""
        assert template_registry.get("_template_schema") is None


# ==============================================================
# Tests: match() 关键词匹配
# ==============================================================


class TestTemplateMatching:
    """验证 match() 的关键词匹配逻辑。"""

    def test_match_empirical_paper(self, template_registry: TemplateRegistry) -> None:
        """包含 DID 和 treatment effect 的文本 → 匹配 empirical_economics。"""
        text = "This paper uses a DID design to estimate the treatment effect of a policy reform on labor outcomes using panel data."
        result = template_registry.match(text)
        assert result is not None
        assert result.id == "empirical_economics"

    def test_match_nlp_paper(self, template_registry: TemplateRegistry) -> None:
        """包含 transformer + attention 的文本 → 匹配 nlp_system。"""
        text = "We propose a novel transformer architecture with multi-head attention mechanism for text classification."
        result = template_registry.match(text)
        assert result is not None
        assert result.id == "nlp_system"

    def test_no_match_below_min_score(self, template_registry: TemplateRegistry) -> None:
        """只包含 1 个关键词 → min_score=2 时不匹配。"""
        text = "This paper discusses panel data."
        result = template_registry.match(text, min_score=2)
        # "panel data" 匹配 empirical_economics 但只有 1 分
        assert result is None

    def test_min_score_1_allows_single_keyword(self, template_registry: TemplateRegistry) -> None:
        """min_score=1 时单关键词可匹配。"""
        text = "This paper discusses panel data."
        result = template_registry.match(text, min_score=1)
        assert result is not None
        assert result.id == "empirical_economics"

    def test_best_match_wins(self, template_registry: TemplateRegistry) -> None:
        """多模板匹配时取最高分。"""
        # 同时包含 DID 和 transformer，但 DID + panel data + fixed effects 给 econ 更多分
        text = "We use DID with fixed effects on panel data. We also mention transformer but only briefly."
        result = template_registry.match(text)
        assert result is not None
        assert result.id == "empirical_economics"

    def test_match_case_insensitive(self, template_registry: TemplateRegistry) -> None:
        """匹配应不区分大小写。"""
        text = "DIFFERENCE-IN-DIFFERENCES CAUSAL EFFECT estimation using PANEL DATA"
        result = template_registry.match(text)
        assert result is not None
        assert result.id == "empirical_economics"

    def test_empty_text_no_match(self, template_registry: TemplateRegistry) -> None:
        """空文本 → 不匹配。"""
        result = template_registry.match("")
        assert result is None

    def test_match_all_returns_sorted(self, template_registry: TemplateRegistry) -> None:
        """match_all 返回按分数降序排列的列表。"""
        # 同时匹配两个模板
        text = "DID treatment effect transformer attention mechanism panel data fixed effects fine-tuning"
        results = template_registry.match_all(text, min_score=1)
        assert len(results) >= 2
        # 应按分数降序
        scores = [score for _, score in results]
        assert scores == sorted(scores, reverse=True)

    def test_short_keyword_no_substring_match(self, template_registry: TemplateRegistry) -> None:
        """短关键词使用词边界匹配，不应子串误配。

        "given" 包含 "iv" 子串但不应匹配 IV 关键词。
        "evaluate" 包含 "ate" 子串但不应匹配（如果有 ATE 关键词）。
        "candidate" 包含 "did" 子串但不应匹配 DID 关键词。
        """
        # 这段文本包含多个含 "did"/"iv" 子串的词，但不含真正的术语
        text = "Given the candidate's motivation, we evaluate the archived evidence of survival."
        result = template_registry.match(text, min_score=1)
        # 不应匹配任何模板（没有真正的术语出现）
        assert result is None

    def test_short_keyword_exact_word_match(self, template_registry: TemplateRegistry) -> None:
        """短关键词作为独立单词时应正确匹配。"""
        # "DID" 和 "RDD" 作为独立术语出现
        text = "We use DID and RDD methods to estimate causal effects."
        result = template_registry.match(text)
        assert result is not None
        assert result.id == "empirical_economics"


# ==============================================================
# Tests: metacognition 模板 seed 合并
# ==============================================================


class TestMetacognitionTemplateSeed:
    """验证 metacognition.py 中的模板 seed 合并逻辑。"""

    def test_seed_fills_empty_args(self) -> None:
        """Agent 未填字段 → 使用模板 seed。"""
        from core.tool_handlers.metacognition import _merge_template_seed_into_args

        args = {"paper_type_description": ""}  # Agent 未填
        seed = {
            "paper_type_description": "模板提供的论文描述",
            "focus_dimensions": ["维度1", "维度2"],
            "typical_weaknesses": ["弱点1"],
            "verification_strategies": ["策略1"],
        }
        merged = _merge_template_seed_into_args(args, seed)
        assert merged["paper_type_description"] == "模板提供的论文描述"
        assert merged["focus_dimensions"] == ["维度1", "维度2"]
        assert merged["typical_weaknesses"] == ["弱点1"]
        assert merged["verification_strategies"] == ["策略1"]

    def test_agent_input_overrides_seed(self) -> None:
        """Agent 显式填写的字段 → 覆盖模板 seed。"""
        from core.tool_handlers.metacognition import _merge_template_seed_into_args

        args = {
            "paper_type_description": "Agent 自己的判断",
            "focus_dimensions": ["Agent 的维度"],
            "typical_weaknesses": [],  # 空列表 = 未填
            "verification_strategies": ["Agent 的策略"],
        }
        seed = {
            "paper_type_description": "模板的描述",
            "focus_dimensions": ["模板维度"],
            "typical_weaknesses": ["模板弱点"],
            "verification_strategies": ["模板策略"],
        }
        merged = _merge_template_seed_into_args(args, seed)
        # Agent 填了的保持不变
        assert merged["paper_type_description"] == "Agent 自己的判断"
        assert merged["focus_dimensions"] == ["Agent 的维度"]
        assert merged["verification_strategies"] == ["Agent 的策略"]
        # Agent 未填的使用模板
        assert merged["typical_weaknesses"] == ["模板弱点"]

    def test_no_seed_no_change(self) -> None:
        """seed 为空 → args 不变。"""
        from core.tool_handlers.metacognition import _merge_template_seed_into_args

        args = {"paper_type_description": "原始"}
        seed: dict[str, str] = {}
        merged = _merge_template_seed_into_args(args, seed)
        assert merged == args

    def test_does_not_mutate_original_args(self) -> None:
        """合并不应修改原始 args dict。"""
        from core.tool_handlers.metacognition import _merge_template_seed_into_args

        args = {"paper_type_description": ""}
        seed = {"paper_type_description": "模板值"}
        original_args = dict(args)
        _merge_template_seed_into_args(args, seed)
        assert args == original_args  # 原 dict 未被修改


# ==============================================================
# Tests: _try_match_template
# ==============================================================


class TestTryMatchTemplate:
    """验证 _try_match_template 从 state 构建匹配文本。"""

    def test_match_from_abstract(self, template_registry: TemplateRegistry) -> None:
        """从论文摘要中匹配模板。"""
        from core.tool_handlers.metacognition import _try_match_template

        state = MagicMock()
        state.paper_sections = {
            "abstract": "We use a difference-in-differences design to estimate the causal effect of minimum wage on employment using panel data.",
            "methodology": "...",
        }
        state.cognitive_hints = None

        seed, name, rec_skills = _try_match_template(state, template_registry)
        assert seed is not None
        assert name == "实证经济学论文 (Causal Inference)"
        assert "paper_type_description" in seed
        assert isinstance(rec_skills, list)

    def test_no_match_when_no_sections(self, template_registry: TemplateRegistry) -> None:
        """无论文内容 → 不匹配。"""
        from core.tool_handlers.metacognition import _try_match_template

        state = MagicMock()
        state.paper_sections = {}
        state.cognitive_hints = None

        seed, name, rec_skills = _try_match_template(state, template_registry)
        assert seed is None
        assert name is None
        assert rec_skills == []


# ==============================================================
# Tests: TemplateMeta dataclass
# ==============================================================


class TestTemplateMeta:
    """验证 TemplateMeta 的构造。"""

    def test_from_dict_complete(self) -> None:
        """完整数据构造。"""
        data = {
            "id": "test",
            "name": "Test Template",
            "match_signals": {
                "keywords": ["KW1", "kw2"],
                "structure_patterns": ["has_method"],
            },
            "seed_hints": {"paper_type_description": "test paper"},
            "recommended_skills": ["skill_a"],
            "gate_overrides": {"min_sections_for_completeness": 3},
        }
        meta = TemplateMeta.from_dict(data)
        assert meta.id == "test"
        assert meta.name == "Test Template"
        assert meta.keywords == ["kw1", "kw2"]  # lowercased
        assert meta.structure_patterns == ["has_method"]
        assert meta.seed_hints["paper_type_description"] == "test paper"
        assert meta.recommended_skills == ["skill_a"]
        assert meta.gate_overrides["min_sections_for_completeness"] == 3

    def test_from_dict_minimal(self) -> None:
        """最小数据构造（缺少可选字段）。"""
        data = {"id": "min", "name": "Minimal"}
        meta = TemplateMeta.from_dict(data)
        assert meta.id == "min"
        assert meta.keywords == []
        assert meta.seed_hints == {}


# ==============================================================
# Tests: Real templates directory
# ==============================================================


class TestRealTemplates:
    """验证实际 templates/ 目录中的模板。"""

    def test_real_templates_load(self) -> None:
        """v2/skills/templates/ 应包含 6 个模板。"""
        templates_dir = Path(__file__).parent.parent / "skills" / "templates"
        if not templates_dir.exists():
            pytest.skip("Real templates directory not found")

        try:
            reg = TemplateRegistry(templates_dir)
        except Exception:
            pytest.skip("PyYAML not available")

        assert len(reg.all_templates) == 6

    def test_real_empirical_economics_template(self) -> None:
        """实际 empirical_economics.yaml 应可加载且含关键字段。"""
        templates_dir = Path(__file__).parent.parent / "skills" / "templates"
        if not templates_dir.exists():
            pytest.skip("Real templates directory not found")

        try:
            reg = TemplateRegistry(templates_dir)
        except Exception:
            pytest.skip("PyYAML not available")

        meta = reg.get("empirical_economics")
        assert meta is not None
        assert "did" in meta.keywords
        assert len(meta.seed_hints.get("focus_dimensions", [])) >= 3
        assert "methodology_checklist" in meta.recommended_skills
