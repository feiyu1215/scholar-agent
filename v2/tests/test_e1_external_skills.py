"""
tests/test_e1_external_skills.py — 外部 Skill 加载测试

覆盖 V4 Phase E1 验收标准:
    1. 外部路径文件存在 → 内容正确加载
    2. extract_sections → 只返回指定 heading 的内容
    3. 文件不存在 → 返回 None 不崩溃
    4. 网络 URL → 拒绝加载（返回 None）
    5. source="internal" 行为不变（向后兼容）
    6. extract_sections 大小写不敏感
    7. extract_sections 指定的 heading 不存在 → 返回 None
    8. 外部操作型 Skill 的 handler 仍需在本地实现（安全约束）
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.skill_registry import SkillMeta, SkillRegistry


# ==============================================================
# Fixtures
# ==============================================================


EXTERNAL_SKILL_CONTENT = """\
# External Skill

This is an external skill file.

## Core Rules

These are the core rules for this domain.

Rule 1: Always cite sources.
Rule 2: Use formal language.

## Writing Patterns

Pattern A: Topic sentence first.
Pattern B: Evidence follows claim.

## Advanced Topics

This section should NOT be extracted when requesting only "Core Rules" and "Writing Patterns".
"""


@pytest.fixture
def external_skill_file(tmp_path: Path) -> Path:
    """创建一个模拟的外部 Skill Markdown 文件。"""
    ext_dir = tmp_path / "external_skills"
    ext_dir.mkdir()
    skill_file = ext_dir / "econ_write_ext.md"
    skill_file.write_text(EXTERNAL_SKILL_CONTENT, encoding="utf-8")
    return skill_file


@pytest.fixture
def registry_with_external(tmp_path: Path, external_skill_file: Path) -> SkillRegistry:
    """创建包含 internal + external skill 的 registry。"""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    # Internal skill 文件
    (skills_dir / "internal_skill.md").write_text(
        "# Internal Skill\n\nInternal content here.", encoding="utf-8"
    )

    registry_data = {
        "version": "1.0",
        "skills": [
            {
                "id": "internal_one",
                "type": "knowledge",
                "file": "internal_skill.md",
                "name": "Internal Skill",
                "description": "A standard internal skill.",
                "applicable_paper_types": ["empirical"],
                "applicable_phases": ["deep_review"],
                "token_estimate": 500,
                "priority_hint": 70,
            },
            {
                "id": "external_full",
                "type": "knowledge",
                "source": "external",
                "path": str(external_skill_file),
                "name": "External Full Load",
                "description": "An external skill loaded in full.",
                "applicable_paper_types": ["empirical"],
                "applicable_phases": ["editing", "synthesis"],
                "token_estimate": 1500,
                "priority_hint": 65,
            },
            {
                "id": "external_sections",
                "type": "knowledge",
                "source": "external",
                "path": str(external_skill_file),
                "name": "External Sections Only",
                "description": "An external skill with section extraction.",
                "extract_sections": ["Core Rules", "Writing Patterns"],
                "applicable_paper_types": ["empirical"],
                "applicable_phases": ["editing"],
                "token_estimate": 800,
                "priority_hint": 63,
            },
            {
                "id": "external_missing",
                "type": "knowledge",
                "source": "external",
                "path": "/nonexistent/path/to/skill.md",
                "name": "Missing External Skill",
                "description": "External skill whose file does not exist.",
                "applicable_paper_types": ["empirical"],
                "applicable_phases": ["editing"],
                "token_estimate": 300,
                "priority_hint": 55,
            },
            {
                "id": "external_url",
                "type": "knowledge",
                "source": "external",
                "path": "https://example.com/skill.md",
                "name": "URL External Skill",
                "description": "External skill with network URL (should be rejected).",
                "applicable_paper_types": ["empirical"],
                "applicable_phases": ["synthesis"],
                "token_estimate": 400,
                "priority_hint": 50,
            },
            {
                "id": "external_empty_path",
                "type": "knowledge",
                "source": "external",
                "path": "",
                "name": "Empty Path External",
                "description": "External skill with empty path.",
                "applicable_paper_types": [],
                "applicable_phases": [],
                "token_estimate": 200,
                "priority_hint": 45,
            },
            {
                "id": "external_sections_nomatch",
                "type": "knowledge",
                "source": "external",
                "path": str(external_skill_file),
                "name": "External No Match Sections",
                "description": "Extract sections that don't exist in the file.",
                "extract_sections": ["Nonexistent Section", "Also Missing"],
                "applicable_paper_types": ["empirical"],
                "applicable_phases": ["deep_review"],
                "token_estimate": 300,
                "priority_hint": 40,
            },
        ],
    }
    (skills_dir / "registry.json").write_text(
        json.dumps(registry_data, indent=2), encoding="utf-8"
    )

    return SkillRegistry(skills_dir)


# ==============================================================
# Tests: External Skill Loading (Full)
# ==============================================================


class TestExternalSkillFullLoad:
    """验证 source='external' 的 Skill 可以从绝对路径正确加载全部内容。"""

    def test_external_skill_loads_full_content(
        self, registry_with_external: SkillRegistry
    ) -> None:
        """外部 Skill 文件存在时，load_content 返回完整内容。"""
        content = registry_with_external.load_content("external_full")
        assert content is not None
        assert "# External Skill" in content
        assert "## Core Rules" in content
        assert "## Writing Patterns" in content
        assert "## Advanced Topics" in content

    def test_external_skill_meta_fields(
        self, registry_with_external: SkillRegistry
    ) -> None:
        """外部 Skill 的元数据字段正确映射。"""
        meta = registry_with_external.get("external_full")
        assert meta is not None
        assert meta.source == "external"
        assert meta.path != ""
        assert meta.file == ""  # 外部 Skill 没有 file 字段
        assert meta.type == "knowledge"


# ==============================================================
# Tests: extract_sections
# ==============================================================


class TestExtractSections:
    """验证 extract_sections 只提取指定 heading 下的内容。"""

    def test_extracts_specified_sections_only(
        self, registry_with_external: SkillRegistry
    ) -> None:
        """只返回 Core Rules 和 Writing Patterns 的内容。"""
        content = registry_with_external.load_content("external_sections")
        assert content is not None
        assert "## Core Rules" in content
        assert "Rule 1: Always cite sources." in content
        assert "## Writing Patterns" in content
        assert "Pattern A: Topic sentence first." in content
        # Advanced Topics 不应出现
        assert "## Advanced Topics" not in content
        assert "should NOT be extracted" not in content

    def test_extract_sections_case_insensitive(
        self, tmp_path: Path, external_skill_file: Path
    ) -> None:
        """heading 匹配不区分大小写。"""
        skills_dir = tmp_path / "ci_skills"
        skills_dir.mkdir()

        registry_data = {
            "version": "1.0",
            "skills": [
                {
                    "id": "case_test",
                    "type": "knowledge",
                    "source": "external",
                    "path": str(external_skill_file),
                    "name": "Case Test",
                    "description": "Test case-insensitive section matching.",
                    "extract_sections": ["core rules", "WRITING PATTERNS"],
                    "token_estimate": 500,
                    "priority_hint": 50,
                }
            ],
        }
        (skills_dir / "registry.json").write_text(
            json.dumps(registry_data, indent=2), encoding="utf-8"
        )
        reg = SkillRegistry(skills_dir)
        content = reg.load_content("case_test")
        assert content is not None
        assert "Rule 1: Always cite sources." in content
        assert "Pattern A: Topic sentence first." in content

    def test_extract_sections_no_match_returns_none(
        self, registry_with_external: SkillRegistry
    ) -> None:
        """请求的 section 不存在时返回 None。"""
        content = registry_with_external.load_content("external_sections_nomatch")
        assert content is None

    def test_extract_sections_preserves_subsection_content(
        self, tmp_path: Path
    ) -> None:
        """提取的 section 应包含其下的子 heading 内容，直到同级 heading。"""
        skills_dir = tmp_path / "sub_skills"
        skills_dir.mkdir()

        md_content = """\
# Top

## Target Section

Intro text.

### Subsection A

Detail A.

### Subsection B

Detail B.

## Next Section

Should not appear.
"""
        ext_file = tmp_path / "subsection_test.md"
        ext_file.write_text(md_content, encoding="utf-8")

        registry_data = {
            "version": "1.0",
            "skills": [
                {
                    "id": "subsection_test",
                    "type": "knowledge",
                    "source": "external",
                    "path": str(ext_file),
                    "extract_sections": ["Target Section"],
                    "token_estimate": 300,
                    "priority_hint": 50,
                }
            ],
        }
        (skills_dir / "registry.json").write_text(
            json.dumps(registry_data, indent=2), encoding="utf-8"
        )
        reg = SkillRegistry(skills_dir)
        content = reg.load_content("subsection_test")
        assert content is not None
        assert "## Target Section" in content
        assert "### Subsection A" in content
        assert "Detail A." in content
        assert "### Subsection B" in content
        assert "Detail B." in content
        # 同级 heading 后的内容不应出现
        assert "## Next Section" not in content
        assert "Should not appear." not in content


# ==============================================================
# Tests: Graceful Degradation
# ==============================================================


class TestGracefulDegradation:
    """验证各种异常情况下的 graceful 处理。"""

    def test_missing_file_returns_none(
        self, registry_with_external: SkillRegistry
    ) -> None:
        """外部文件不存在时返回 None，不崩溃。"""
        content = registry_with_external.load_content("external_missing")
        assert content is None

    def test_network_url_rejected(
        self, registry_with_external: SkillRegistry
    ) -> None:
        """网络 URL 被拒绝，返回 None。"""
        content = registry_with_external.load_content("external_url")
        assert content is None

    def test_empty_path_rejected(
        self, registry_with_external: SkillRegistry
    ) -> None:
        """空 path 被拒绝，返回 None。"""
        content = registry_with_external.load_content("external_empty_path")
        assert content is None

    def test_ftp_url_rejected(self, tmp_path: Path) -> None:
        """FTP URL 同样被拒绝。"""
        skills_dir = tmp_path / "ftp_skills"
        skills_dir.mkdir()
        registry_data = {
            "version": "1.0",
            "skills": [
                {
                    "id": "ftp_skill",
                    "type": "knowledge",
                    "source": "external",
                    "path": "ftp://server.com/skill.md",
                    "token_estimate": 200,
                    "priority_hint": 50,
                }
            ],
        }
        (skills_dir / "registry.json").write_text(
            json.dumps(registry_data, indent=2), encoding="utf-8"
        )
        reg = SkillRegistry(skills_dir)
        assert reg.load_content("ftp_skill") is None

    def test_http_url_rejected(self, tmp_path: Path) -> None:
        """HTTP URL 被拒绝（非 HTTPS 也拒绝）。"""
        skills_dir = tmp_path / "http_skills"
        skills_dir.mkdir()
        registry_data = {
            "version": "1.0",
            "skills": [
                {
                    "id": "http_skill",
                    "type": "knowledge",
                    "source": "external",
                    "path": "http://example.com/skill.md",
                    "token_estimate": 200,
                    "priority_hint": 50,
                }
            ],
        }
        (skills_dir / "registry.json").write_text(
            json.dumps(registry_data, indent=2), encoding="utf-8"
        )
        reg = SkillRegistry(skills_dir)
        assert reg.load_content("http_skill") is None


# ==============================================================
# Tests: Backward Compatibility
# ==============================================================


class TestBackwardCompatibility:
    """验证 internal source 行为不变。"""

    def test_internal_skill_still_works(
        self, registry_with_external: SkillRegistry
    ) -> None:
        """source='internal'（默认）的 Skill 仍能正常加载。"""
        content = registry_with_external.load_content("internal_one")
        assert content is not None
        assert "# Internal Skill" in content
        assert "Internal content here." in content

    def test_internal_skill_default_source(
        self, registry_with_external: SkillRegistry
    ) -> None:
        """未指定 source 时默认为 'internal'。"""
        meta = registry_with_external.get("internal_one")
        assert meta is not None
        assert meta.source == "internal"

    def test_query_includes_external_skills(
        self, registry_with_external: SkillRegistry
    ) -> None:
        """query() 正常返回外部 Skill（不区分 source 进行过滤）。"""
        results = registry_with_external.query(
            paper_type="empirical",
            phase="editing",
            budget_tokens=5000,
        )
        ids = [s.id for s in results]
        assert "internal_one" not in ids  # internal_one 阶段是 deep_review
        assert "external_full" in ids
        assert "external_sections" in ids

    def test_internal_skill_with_extract_sections(self, tmp_path: Path) -> None:
        """internal source 也可以使用 extract_sections。"""
        skills_dir = tmp_path / "int_extract"
        skills_dir.mkdir()

        (skills_dir / "multi_section.md").write_text(
            "# Multi\n\n## First\n\nContent 1.\n\n## Second\n\nContent 2.\n\n## Third\n\nContent 3.\n",
            encoding="utf-8",
        )

        registry_data = {
            "version": "1.0",
            "skills": [
                {
                    "id": "int_extract",
                    "type": "knowledge",
                    "file": "multi_section.md",
                    "extract_sections": ["First", "Third"],
                    "token_estimate": 300,
                    "priority_hint": 50,
                }
            ],
        }
        (skills_dir / "registry.json").write_text(
            json.dumps(registry_data, indent=2), encoding="utf-8"
        )
        reg = SkillRegistry(skills_dir)
        content = reg.load_content("int_extract")
        assert content is not None
        assert "## First" in content
        assert "Content 1." in content
        assert "## Third" in content
        assert "Content 3." in content
        assert "## Second" not in content
        assert "Content 2." not in content


# ==============================================================
# Tests: SkillMeta from_dict with new fields
# ==============================================================


class TestSkillMetaNewFields:
    """验证 SkillMeta.from_dict 正确解析新字段。"""

    def test_source_field_parsed(self) -> None:
        """source 字段被正确解析。"""
        data = {
            "id": "test",
            "type": "knowledge",
            "source": "external",
            "path": "/some/path.md",
            "extract_sections": ["Section A"],
        }
        meta = SkillMeta.from_dict(data)
        assert meta.source == "external"
        assert meta.path == "/some/path.md"
        assert meta.extract_sections == ("Section A",)

    def test_defaults_when_fields_missing(self) -> None:
        """未提供新字段时使用默认值。"""
        data = {
            "id": "legacy",
            "type": "knowledge",
            "file": "legacy.md",
        }
        meta = SkillMeta.from_dict(data)
        assert meta.source == "internal"
        assert meta.path == ""
        assert meta.extract_sections == ()

    def test_file_optional_for_external(self) -> None:
        """external skill 可以不提供 file 字段。"""
        data = {
            "id": "ext_no_file",
            "type": "knowledge",
            "source": "external",
            "path": "/external/path.md",
        }
        meta = SkillMeta.from_dict(data)
        assert meta.file == ""
        assert meta.source == "external"
        assert meta.path == "/external/path.md"


# ==============================================================
# Tests: _extract_sections helper
# ==============================================================


class TestExtractSectionsHelper:
    """直接测试 _extract_sections 静态方法。"""

    def test_basic_extraction(self) -> None:
        """基本 section 提取。"""
        content = "# Title\n\n## Intro\n\nHello.\n\n## Methods\n\nDid stuff.\n\n## Results\n\nGot stuff.\n"
        result = SkillRegistry._extract_sections(content, ("Methods",))
        assert "## Methods" in result
        assert "Did stuff." in result
        assert "## Results" not in result
        assert "## Intro" not in result

    def test_multiple_sections(self) -> None:
        """提取多个 section。"""
        content = "# Doc\n\n## A\n\nContent A.\n\n## B\n\nContent B.\n\n## C\n\nContent C.\n"
        result = SkillRegistry._extract_sections(content, ("A", "C"))
        assert "## A" in result
        assert "Content A." in result
        assert "## C" in result
        assert "Content C." in result
        assert "## B" not in result

    def test_nested_subsections_included(self) -> None:
        """子 heading 被包含在提取结果中。"""
        content = "## Parent\n\nIntro.\n\n### Child\n\nChild content.\n\n## Sibling\n\nOther.\n"
        result = SkillRegistry._extract_sections(content, ("Parent",))
        assert "## Parent" in result
        assert "### Child" in result
        assert "Child content." in result
        assert "## Sibling" not in result

    def test_empty_content(self) -> None:
        """空内容返回空字符串。"""
        result = SkillRegistry._extract_sections("", ("Section",))
        assert result == ""

    def test_no_matching_sections(self) -> None:
        """无匹配 section 返回空字符串。"""
        content = "## Intro\n\nSome text.\n\n## Methods\n\nMore text.\n"
        result = SkillRegistry._extract_sections(content, ("Nonexistent",))
        assert result == ""

    def test_case_insensitive_matching(self) -> None:
        """heading 匹配不区分大小写。"""
        content = "## Core Rules\n\nRule content.\n\n## Other\n\nOther content.\n"
        result = SkillRegistry._extract_sections(content, ("core rules",))
        assert "## Core Rules" in result
        assert "Rule content." in result

    def test_trailing_hash_stripped(self) -> None:
        """ATX-style trailing # 被正确处理。"""
        content = "## Core Rules ##\n\nRule content.\n\n## Other\n\nOther.\n"
        result = SkillRegistry._extract_sections(content, ("Core Rules",))
        assert "Rule content." in result
