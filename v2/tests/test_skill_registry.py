"""
tests/test_skill_registry.py — SkillRegistry 单元测试

覆盖 V4 Phase A1 验收标准:
    1. registry.json 可被正确 parse
    2. query() 按 paper_type 过滤正确
    3. query() 按 phase 过滤正确
    4. query() 按 budget 裁剪正确
    5. load_content() 正确读取 Markdown
    6. Kill Switch OFF 时 query() 被跳过（在调用方守卫，此处测试 registry 本身行为）
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from core.skill_registry import SkillMeta, SkillRegistry


# ==============================================================
# Fixtures
# ==============================================================


@pytest.fixture
def sample_registry_dir(tmp_path: Path) -> Path:
    """创建一个包含 sample registry.json 和若干 Markdown 文件的临时目录。"""
    # 创建 registry.json
    registry_data = {
        "version": "1.0",
        "skills": [
            {
                "id": "skill_a",
                "type": "knowledge",
                "file": "skill_a.md",
                "name": "Skill A",
                "description": "A high-priority skill for empirical papers in DEEP_REVIEW",
                "tags": ["methodology"],
                "applicable_paper_types": ["empirical"],
                "applicable_phases": ["DEEP_REVIEW"],
                "token_estimate": 800,
                "priority_hint": 75,
            },
            {
                "id": "skill_b",
                "type": "knowledge",
                "file": "skill_b.md",
                "name": "Skill B",
                "description": "A medium-priority skill for all papers in EDITING",
                "tags": ["writing"],
                "applicable_paper_types": ["empirical", "theoretical", "review"],
                "applicable_phases": ["EDITING"],
                "token_estimate": 1200,
                "priority_hint": 70,
            },
            {
                "id": "skill_c",
                "type": "knowledge",
                "file": "skill_c.md",
                "name": "Skill C",
                "description": "A low-priority skill for all papers in all phases",
                "tags": ["general"],
                "applicable_paper_types": [],
                "applicable_phases": [],
                "token_estimate": 500,
                "priority_hint": 60,
            },
            {
                "id": "skill_big",
                "type": "knowledge",
                "file": "skill_big.md",
                "name": "Skill Big",
                "description": "A large skill that exceeds typical budgets alone",
                "tags": ["deai"],
                "applicable_paper_types": ["empirical", "theoretical"],
                "applicable_phases": ["EDITING"],
                "token_estimate": 3500,
                "priority_hint": 62,
            },
        ],
    }
    (tmp_path / "registry.json").write_text(
        json.dumps(registry_data, indent=2), encoding="utf-8"
    )

    # 创建对应的 Markdown 文件
    (tmp_path / "skill_a.md").write_text("# Skill A\n\nContent of skill A.", encoding="utf-8")
    (tmp_path / "skill_b.md").write_text("# Skill B\n\nContent of skill B.", encoding="utf-8")
    (tmp_path / "skill_c.md").write_text("# Skill C\n\nContent of skill C.", encoding="utf-8")
    (tmp_path / "skill_big.md").write_text(
        "# Skill Big\n\n" + "x " * 1750, encoding="utf-8"
    )

    return tmp_path


@pytest.fixture
def registry(sample_registry_dir: Path) -> SkillRegistry:
    """构造 SkillRegistry 实例。"""
    return SkillRegistry(sample_registry_dir)


# ==============================================================
# Tests: registry.json parse
# ==============================================================


class TestRegistryParsing:
    """验证 registry.json 的 parse 和 SkillMeta 构造。"""

    def test_loads_all_skills(self, registry: SkillRegistry) -> None:
        """应加载全部 4 个 skill 条目。"""
        assert len(registry.all_skills) == 4

    def test_skill_meta_fields(self, registry: SkillRegistry) -> None:
        """SkillMeta 各字段正确映射。"""
        meta = registry.get("skill_a")
        assert meta is not None
        assert meta.id == "skill_a"
        assert meta.type == "knowledge"
        assert meta.file == "skill_a.md"
        assert meta.name == "Skill A"
        assert meta.token_estimate == 800
        assert meta.priority_hint == 75
        assert "empirical" in meta.applicable_paper_types
        assert "DEEP_REVIEW" in meta.applicable_phases
        assert "methodology" in meta.tags

    def test_empty_applicable_means_universal(self, registry: SkillRegistry) -> None:
        """applicable_paper_types 为空 → 匹配所有论文类型（不过滤）。"""
        meta = registry.get("skill_c")
        assert meta is not None
        assert meta.applicable_paper_types == ()
        assert meta.applicable_phases == ()

    def test_missing_registry_json_graceful(self, tmp_path: Path) -> None:
        """registry.json 不存在时 → 空 registry，不报错。"""
        reg = SkillRegistry(tmp_path)
        assert len(reg.all_skills) == 0
        assert reg.get("anything") is None

    def test_malformed_registry_json_graceful(self, tmp_path: Path) -> None:
        """registry.json 格式错误时 → 空 registry，不报错。"""
        (tmp_path / "registry.json").write_text("not valid json {{{", encoding="utf-8")
        reg = SkillRegistry(tmp_path)
        assert len(reg.all_skills) == 0


# ==============================================================
# Tests: query() filtering
# ==============================================================


class TestQueryFiltering:
    """验证 query() 的 paper_type + phase + budget 三维过滤。"""

    def test_filter_by_paper_type(self, registry: SkillRegistry) -> None:
        """empirical 论文应匹配 skill_a、skill_b、skill_big，但不匹配仅 review 的。"""
        results = registry.query(paper_type="empirical", budget_tokens=99999)
        result_ids = [s.id for s in results]
        assert "skill_a" in result_ids
        assert "skill_b" in result_ids
        assert "skill_c" in result_ids  # 空 applicable_paper_types = universal
        assert "skill_big" in result_ids

    def test_filter_by_paper_type_theoretical(self, registry: SkillRegistry) -> None:
        """theoretical 论文不应匹配 skill_a（只适用 empirical）。"""
        results = registry.query(paper_type="theoretical", budget_tokens=99999)
        result_ids = [s.id for s in results]
        assert "skill_a" not in result_ids
        assert "skill_b" in result_ids
        assert "skill_c" in result_ids

    def test_filter_by_phase(self, registry: SkillRegistry) -> None:
        """DEEP_REVIEW 阶段应匹配 skill_a 和 skill_c（universal），不匹配 skill_b（EDITING only）。"""
        results = registry.query(phase="DEEP_REVIEW", budget_tokens=99999)
        result_ids = [s.id for s in results]
        assert "skill_a" in result_ids
        assert "skill_c" in result_ids
        assert "skill_b" not in result_ids
        assert "skill_big" not in result_ids

    def test_filter_combined(self, registry: SkillRegistry) -> None:
        """empirical + DEEP_REVIEW → 只有 skill_a 和 skill_c。"""
        results = registry.query(
            paper_type="empirical", phase="DEEP_REVIEW", budget_tokens=99999
        )
        result_ids = [s.id for s in results]
        assert "skill_a" in result_ids
        assert "skill_c" in result_ids
        assert "skill_b" not in result_ids  # phase 不匹配

    def test_no_filter_returns_all(self, registry: SkillRegistry) -> None:
        """不传参数 → 返回所有 skills（受 budget 限制）。"""
        results = registry.query(budget_tokens=99999)
        assert len(results) == 4


# ==============================================================
# Tests: query() budget trimming
# ==============================================================


class TestQueryBudget:
    """验证 query() 的 token 预算裁剪逻辑。"""

    def test_budget_respects_limit(self, registry: SkillRegistry) -> None:
        """budget=2000 时，不能同时加载 skill_a(800) + skill_b(1200) + skill_c(500)。"""
        results = registry.query(budget_tokens=2000)
        total_tokens = sum(s.token_estimate for s in results)
        assert total_tokens <= 2000

    def test_budget_priority_order(self, registry: SkillRegistry) -> None:
        """高 priority_hint 的 skill 优先占用 budget。"""
        results = registry.query(budget_tokens=1500)
        result_ids = [s.id for s in results]
        # priority: skill_a(75) > skill_b(70) > skill_big(62) > skill_c(60)
        # skill_a(800) fits → remaining 700
        # skill_b(1200) doesn't fit
        # skill_big(3500) doesn't fit
        # skill_c(500) fits → remaining 200
        assert "skill_a" in result_ids
        assert "skill_c" in result_ids
        assert "skill_b" not in result_ids

    def test_budget_zero_returns_empty(self, registry: SkillRegistry) -> None:
        """budget=0 → 什么都加载不了。"""
        results = registry.query(budget_tokens=0)
        assert results == []

    def test_large_skill_excluded_when_budget_tight(self, registry: SkillRegistry) -> None:
        """budget=2000 时 skill_big(3500 tokens) 永远不会被加载。"""
        results = registry.query(paper_type="empirical", phase="EDITING", budget_tokens=2000)
        result_ids = [s.id for s in results]
        assert "skill_big" not in result_ids

    def test_budget_exact_fit(self, registry: SkillRegistry) -> None:
        """budget 恰好等于 skill token 时应该能加载。"""
        results = registry.query(paper_type="empirical", phase="DEEP_REVIEW", budget_tokens=800)
        result_ids = [s.id for s in results]
        assert "skill_a" in result_ids


# ==============================================================
# Tests: load_content()
# ==============================================================


class TestLoadContent:
    """验证 load_content() 读取 Markdown 文件。"""

    def test_load_existing_skill(self, registry: SkillRegistry) -> None:
        """正常读取已存在的 skill 文件。"""
        content = registry.load_content("skill_a")
        assert content is not None
        assert "# Skill A" in content
        assert "Content of skill A" in content

    def test_load_nonexistent_skill_id(self, registry: SkillRegistry) -> None:
        """不存在的 skill_id → 返回 None。"""
        content = registry.load_content("nonexistent")
        assert content is None

    def test_load_missing_file(self, sample_registry_dir: Path) -> None:
        """skill_id 存在但文件被删除 → 返回 None，不报错。"""
        # 删除 skill_a.md
        (sample_registry_dir / "skill_a.md").unlink()
        reg = SkillRegistry(sample_registry_dir)
        content = reg.load_content("skill_a")
        assert content is None


# ==============================================================
# Tests: all_skills ordering
# ==============================================================


class TestAllSkillsOrdering:
    """验证 all_skills 的排序。"""

    def test_sorted_by_priority_desc(self, registry: SkillRegistry) -> None:
        """all_skills 按 priority_hint 降序排列。"""
        skills = registry.all_skills
        priorities = [s.priority_hint for s in skills]
        assert priorities == sorted(priorities, reverse=True)


# ==============================================================
# Tests: SkillMeta dataclass
# ==============================================================


class TestSkillMeta:
    """验证 SkillMeta 的构造和不可变性。"""

    def test_from_dict_minimal(self) -> None:
        """最小字段即可构造 SkillMeta。"""
        data = {"id": "test", "file": "test.md"}
        meta = SkillMeta.from_dict(data)
        assert meta.id == "test"
        assert meta.type == "knowledge"  # default
        assert meta.name == "test"  # fallback to id
        assert meta.token_estimate == 0
        assert meta.priority_hint == 50

    def test_frozen(self) -> None:
        """SkillMeta 是 frozen dataclass，不可修改。"""
        data = {"id": "test", "file": "test.md", "token_estimate": 100}
        meta = SkillMeta.from_dict(data)
        with pytest.raises(Exception):  # FrozenInstanceError
            meta.id = "changed"  # type: ignore[misc]


# ==============================================================
# Tests: godel_config integration
# ==============================================================


class TestGodelConfigIntegration:
    """验证 godel_config.py 中的 Skill Loading flag 和常量。"""

    def test_skill_loading_flag_exists(self) -> None:
        """GODEL_SKILL_LOADING_ENABLED 应存在且默认为 True。"""
        from core.godel_config import GODEL_SKILL_LOADING_ENABLED
        assert isinstance(GODEL_SKILL_LOADING_ENABLED, bool)
        # 默认 "1" → True（除非环境变量被设为 "0"）
        # 在测试环境中如果未设置环境变量，应为 True
        assert GODEL_SKILL_LOADING_ENABLED is True

    def test_skill_zone_budget_exists(self) -> None:
        """SKILL_ZONE_BUDGET 应存在且为 4500（允许 methodology_checklist 等核心 skill 加载）。"""
        from core.godel_config import SKILL_ZONE_BUDGET
        assert SKILL_ZONE_BUDGET == 4500

    def test_log_config_includes_skill_loading(self, capsys) -> None:
        """log_config_status() 输出应包含 SkillLoading。"""
        import logging
        from core.godel_config import log_config_status

        # 配置 logger 输出到 capsys
        godel_logger = logging.getLogger("core.godel_config")
        handler = logging.StreamHandler()
        handler.setLevel(logging.INFO)
        godel_logger.addHandler(handler)
        godel_logger.setLevel(logging.INFO)

        log_config_status()
        captured = capsys.readouterr()
        assert "SkillLoading" in captured.err or "SkillLoading" in captured.out

        # Cleanup
        godel_logger.removeHandler(handler)


# ==============================================================
# Tests: Real registry.json parse
# ==============================================================


class TestRealRegistry:
    """验证实际项目中的 registry.json 可被正确加载。"""

    def test_real_registry_loads(self) -> None:
        """v2/skills/registry.json 应包含 8 个 skills。"""
        skills_dir = Path(__file__).parent.parent / "skills"
        if not (skills_dir / "registry.json").exists():
            pytest.skip("Real registry.json not found (running in isolated env)")

        reg = SkillRegistry(skills_dir)
        # 8 knowledge + 1 action (structured_export) + 1 external example = 10
        assert len(reg.all_skills) == 10

    def test_real_skills_have_valid_files(self) -> None:
        """每个 internal skill 的 file 字段指向一个存在的文件。
        外部 skill (source='external') 跳过，因为其路径在测试环境中可能不存在。
        """
        skills_dir = Path(__file__).parent.parent / "skills"
        if not (skills_dir / "registry.json").exists():
            pytest.skip("Real registry.json not found (running in isolated env)")

        reg = SkillRegistry(skills_dir)
        for skill in reg.all_skills:
            if skill.source == "external":
                continue  # 外部 Skill 路径在测试环境中可能无效（graceful skip 是正确行为）
            content = reg.load_content(skill.id)
            assert content is not None, f"Skill '{skill.id}' file not found: {skill.file}"
            assert len(content) > 0, f"Skill '{skill.id}' file is empty"

    def test_real_query_empirical_deep_review(self) -> None:
        """empirical + DEEP_REVIEW 应至少匹配 methodology_checklist。"""
        skills_dir = Path(__file__).parent.parent / "skills"
        if not (skills_dir / "registry.json").exists():
            pytest.skip("Real registry.json not found (running in isolated env)")

        reg = SkillRegistry(skills_dir)
        results = reg.query(paper_type="empirical", phase="DEEP_REVIEW", budget_tokens=5000)
        result_ids = [s.id for s in results]
        assert "methodology_checklist" in result_ids

    def test_real_query_budget_2000_excludes_deai(self) -> None:
        """budget=2000 时 deai_rules(3500 tokens) 不应被加载。"""
        skills_dir = Path(__file__).parent.parent / "skills"
        if not (skills_dir / "registry.json").exists():
            pytest.skip("Real registry.json not found (running in isolated env)")

        reg = SkillRegistry(skills_dir)
        results = reg.query(paper_type="empirical", phase="EDITING", budget_tokens=2000)
        result_ids = [s.id for s in results]
        assert "deai_rules" not in result_ids
