"""
Phase 3 SkillX 渐进式加载器测试。

覆盖：
  - Layer 1: metadata prompt 生成
  - Layer 2: instruction 加载
  - Layer 3: resource 加载（含安全检查）
  - 缓存机制
"""

import pytest
import tempfile
from pathlib import Path
from core.skills.base import (
    Skill,
    SkillContext,
    SkillDescriptor,
    SkillLevel,
    SkillResult,
)
from core.skills.loader import ProgressiveSkillLoader, LoadedSkillContent


# ==============================================================
# Test Skills
# ==============================================================

class MinimalSkill(Skill):
    _DESCRIPTOR = SkillDescriptor(
        name="minimal",
        level=SkillLevel.ATOMIC,
        description="Minimal skill for loader tests",
        token_cost_estimate=100,
    )

    @property
    def descriptor(self):
        return self._DESCRIPTOR

    def can_apply(self, context):
        return 0.5

    def execute(self, context):
        return SkillResult(success=True)


class RichSkill(Skill):
    """Skill with custom instruction."""
    _DESCRIPTOR = SkillDescriptor(
        name="rich",
        level=SkillLevel.FUNCTIONAL,
        description="Rich skill with instruction",
        applicable_phases=("deep_review",),
        token_cost_estimate=300,
    )

    @property
    def descriptor(self):
        return self._DESCRIPTOR

    def can_apply(self, context):
        return 0.8

    def execute(self, context):
        return SkillResult(success=True)

    def get_instruction(self):
        return (
            "## Instructions\n"
            "1. Analyze the paper structure\n"
            "2. Identify methodological issues\n"
            "3. Report findings"
        )


# ==============================================================
# Fixtures
# ==============================================================

@pytest.fixture
def loader():
    skills = [MinimalSkill(), RichSkill()]
    return ProgressiveSkillLoader(skills)


@pytest.fixture
def loader_with_resources(tmp_path):
    """Create a loader with a real resources directory."""
    # Create resource files
    rich_dir = tmp_path / "rich"
    rich_dir.mkdir()
    (rich_dir / "checklist.md").write_text("# DID Checklist\n- item1\n- item2")
    (rich_dir / "nested" / "deep.md").parent.mkdir(parents=True, exist_ok=True)
    (rich_dir / "nested" / "deep.md").write_text("deep resource content")

    skills = [MinimalSkill(), RichSkill()]
    return ProgressiveSkillLoader(skills, resources_dir=tmp_path)


# ==============================================================
# Tests: Layer 1 - Metadata Prompt
# ==============================================================

class TestLayer1Metadata:
    def test_get_metadata_prompt_returns_string(self, loader):
        """Layer 1 should return a formatted string of metadata prompts."""
        prompt = loader.get_metadata_prompt()
        assert isinstance(prompt, str)
        assert "minimal" in prompt
        assert "rich" in prompt

    def test_metadata_prompt_contains_descriptions(self, loader):
        """Metadata prompt should contain skill descriptions."""
        prompt = loader.get_metadata_prompt()
        assert "Minimal skill for loader tests" in prompt
        assert "Rich skill with instruction" in prompt

    def test_metadata_prompt_contains_phases(self, loader):
        """Metadata prompt should contain phase information."""
        prompt = loader.get_metadata_prompt()
        assert "deep_review" in prompt

    def test_metadata_prompt_contains_levels(self, loader):
        """Metadata prompt with include_level should show level."""
        prompt = loader.get_metadata_prompt(include_level=True)
        assert "atomic" in prompt
        assert "functional" in prompt

    def test_metadata_prompt_no_levels(self, loader):
        """Metadata prompt without include_level should not show level markers."""
        prompt = loader.get_metadata_prompt(include_level=False)
        # Should still have skill names
        assert "minimal" in prompt
        assert "rich" in prompt

    def test_metadata_token_estimate(self, loader):
        """Token estimate should be proportional to skill count."""
        estimate = loader.get_metadata_token_estimate()
        assert estimate > 0
        # 2 skills * ~80 tokens each
        assert estimate == 2 * 80

    def test_skill_names_property(self, loader):
        """skill_names should list all registered skills."""
        assert set(loader.skill_names) == {"minimal", "rich"}


# ==============================================================
# Tests: Layer 2 - Instruction
# ==============================================================

class TestLayer2Instruction:
    def test_load_instruction_returns_content(self, loader):
        """Layer 2 should return LoadedSkillContent."""
        result = loader.load_instruction("rich")
        assert result is not None
        assert isinstance(result, LoadedSkillContent)
        assert "Analyze the paper structure" in result.content
        assert result.layer == 2
        assert result.skill_name == "rich"

    def test_load_instruction_caching(self, loader):
        """Second load should use cached value (same content object)."""
        r1 = loader.load_instruction("rich")
        r2 = loader.load_instruction("rich")
        # Content strings should be equal (cached)
        assert r1.content == r2.content

    def test_load_instruction_not_found(self, loader):
        """Nonexistent skill should return None."""
        result = loader.load_instruction("nonexistent")
        assert result is None

    def test_load_instruction_default(self, loader):
        """Skill without custom get_instruction uses descriptor description."""
        result = loader.load_instruction("minimal")
        assert result is not None
        assert result.content == "Minimal skill for loader tests"

    def test_load_instructions_batch(self, loader):
        """Batch loading should return multiple results."""
        results = loader.load_instructions_batch(["rich", "minimal"])
        assert len(results) == 2
        names = [r.skill_name for r in results]
        assert "rich" in names
        assert "minimal" in names

    def test_load_instructions_batch_skips_missing(self, loader):
        """Batch loading should skip nonexistent skills."""
        results = loader.load_instructions_batch(["rich", "nonexistent"])
        assert len(results) == 1
        assert results[0].skill_name == "rich"

    def test_token_estimate(self, loader):
        """Loaded content should have positive token estimate."""
        result = loader.load_instruction("rich")
        assert result.token_estimate > 0


# ==============================================================
# Tests: Layer 3 - Resources
# ==============================================================

class TestLayer3Resources:
    def test_load_resource_success(self, loader_with_resources):
        result = loader_with_resources.load_resource("rich", "checklist.md")
        assert result is not None
        assert isinstance(result, LoadedSkillContent)
        assert "DID Checklist" in result.content
        assert result.layer == 3

    def test_load_resource_nested(self, loader_with_resources):
        result = loader_with_resources.load_resource("rich", "nested/deep.md")
        assert result is not None
        assert "deep resource content" in result.content

    def test_load_resource_not_found_skill(self, loader_with_resources):
        result = loader_with_resources.load_resource("nonexistent", "file.md")
        assert result is None

    def test_load_resource_not_found_file(self, loader_with_resources):
        result = loader_with_resources.load_resource("rich", "missing.md")
        assert result is None

    def test_load_resource_no_resources_dir(self, loader):
        """Loader without resources_dir should return None."""
        result = loader.load_resource("rich", "checklist.md")
        assert result is None

    def test_load_resource_path_traversal_rejected(self, loader_with_resources):
        """Path traversal should be rejected."""
        result = loader_with_resources.load_resource("rich", "../../../etc/passwd")
        assert result is None

    def test_list_resources(self, loader_with_resources):
        resources = loader_with_resources.list_resources("rich")
        assert len(resources) >= 2
        assert "checklist.md" in resources

    def test_list_resources_empty(self, loader_with_resources):
        resources = loader_with_resources.list_resources("minimal")
        assert resources == []

    def test_list_resources_nonexistent(self, loader_with_resources):
        resources = loader_with_resources.list_resources("nonexistent")
        assert resources == []


# ==============================================================
# Tests: Cache Management
# ==============================================================

class TestCacheManagement:
    def test_clear_cache(self, loader):
        loader.load_instruction("rich")
        loader.clear_cache()
        # After clear, internal cache dict should be empty
        assert len(loader._instruction_cache) == 0

    def test_invalidate_specific(self, loader):
        loader.load_instruction("rich")
        loader.load_instruction("minimal")
        loader.invalidate("rich")
        assert "rich" not in loader._instruction_cache
        assert "minimal" in loader._instruction_cache
