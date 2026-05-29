"""
tests/test_b6_skill_installer.py — B6 Skill 安装流程原型测试

覆盖场景:
    1. 合法 Skill 包 → 安装成功 + registry 更新 + 文件就位
    2. id 冲突 → 安装失败 + 明确错误信息
    3. schema 不合法 → validate() 返回具体错误列表
    4. uninstall → registry 移除条目 + 文件删除
    5. 安装后 Agent 下次启动能加载该 Skill（集成验证）
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

# 确保 v2/ 在 sys.path 中
_v2_root = Path(__file__).parent.parent
if str(_v2_root) not in sys.path:
    sys.path.insert(0, str(_v2_root))

from skills.installer import SkillInstaller, InstallResult


# ==============================================================
# Fixtures
# ==============================================================

@pytest.fixture
def tmp_skills_dir(tmp_path: Path) -> Path:
    """创建临时 skills 目录（含空 registry.json）。"""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    handlers_dir = skills_dir / "skill_handlers"
    handlers_dir.mkdir()
    (handlers_dir / "__init__.py").write_text("")

    registry = {
        "version": "1.1",
        "description": "Test registry",
        "skills": [],
    }
    (skills_dir / "registry.json").write_text(
        json.dumps(registry, indent=2), encoding="utf-8"
    )
    return skills_dir


@pytest.fixture
def valid_knowledge_skill(tmp_path: Path) -> Path:
    """创建一个合法的 knowledge Skill 包。"""
    skill_dir = tmp_path / "my_test_skill"
    skill_dir.mkdir()

    skill_json = {
        "id": "my_test_skill",
        "type": "knowledge",
        "name": "My Test Skill",
        "description": "A test skill for unit testing.",
        "version": "1.0.0",
        "tags": ["test", "unit"],
        "applicable_paper_types": ["empirical", "theoretical"],
        "applicable_phases": ["deep_review", "synthesis"],
        "token_estimate": 1500,
        "priority_hint": 65,
    }
    (skill_dir / "skill.json").write_text(
        json.dumps(skill_json, indent=2), encoding="utf-8"
    )
    (skill_dir / "content.md").write_text(
        "# My Test Skill\n\nThis is test content for the skill.\n\n"
        "## Rules\n\n- Rule 1: Always check methodology\n- Rule 2: Verify data\n",
        encoding="utf-8",
    )
    return skill_dir


@pytest.fixture
def valid_action_skill(tmp_path: Path) -> Path:
    """创建一个合法的 action Skill 包。"""
    skill_dir = tmp_path / "my_action_skill"
    skill_dir.mkdir()

    skill_json = {
        "id": "my_action_skill",
        "type": "action",
        "name": "My Action Skill",
        "description": "An action skill for testing.",
        "version": "1.0.0",
        "tags": ["action", "test"],
        "applicable_paper_types": ["empirical"],
        "applicable_phases": ["synthesis"],
        "token_estimate": 200,
        "priority_hint": 55,
        "tools": [
            {
                "name": "do_something",
                "description": "Does something useful.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "input_text": {"type": "string"},
                    },
                    "required": ["input_text"],
                },
                "handler": "handler.py::handle_do_something",
            }
        ],
    }
    (skill_dir / "skill.json").write_text(
        json.dumps(skill_json, indent=2), encoding="utf-8"
    )
    (skill_dir / "content.md").write_text(
        "# My Action Skill\n\nAction skill content.\n", encoding="utf-8"
    )
    (skill_dir / "handler.py").write_text(
        'def handle_do_something(args: dict, state) -> str:\n'
        '    return f"Processed: {args.get(\'input_text\', \'\')}"\n',
        encoding="utf-8",
    )
    return skill_dir


# ==============================================================
# 场景 1: 合法 Skill 包 → 安装成功 + registry 更新 + 文件就位
# ==============================================================

class TestInstallValidSkill:
    """合法 Skill 包安装成功。"""

    def test_install_knowledge_skill_success(
        self, tmp_skills_dir: Path, valid_knowledge_skill: Path
    ) -> None:
        """Knowledge Skill 安装成功。"""
        installer = SkillInstaller(tmp_skills_dir)
        result = installer.install(valid_knowledge_skill)

        assert result.success is True
        assert result.skill_id == "my_test_skill"
        assert not result.errors

    def test_install_creates_content_file(
        self, tmp_skills_dir: Path, valid_knowledge_skill: Path
    ) -> None:
        """安装后 content.md 被复制为 {id}.md。"""
        installer = SkillInstaller(tmp_skills_dir)
        installer.install(valid_knowledge_skill)

        content_file = tmp_skills_dir / "my_test_skill.md"
        assert content_file.exists()
        content = content_file.read_text(encoding="utf-8")
        assert "My Test Skill" in content
        assert "Rule 1" in content

    def test_install_updates_registry(
        self, tmp_skills_dir: Path, valid_knowledge_skill: Path
    ) -> None:
        """安装后 registry.json 包含新条目。"""
        installer = SkillInstaller(tmp_skills_dir)
        installer.install(valid_knowledge_skill)

        registry = json.loads(
            (tmp_skills_dir / "registry.json").read_text(encoding="utf-8")
        )
        skill_ids = [s["id"] for s in registry["skills"]]
        assert "my_test_skill" in skill_ids

        entry = next(s for s in registry["skills"] if s["id"] == "my_test_skill")
        assert entry["status"] == "active"
        assert entry["type"] == "knowledge"
        assert entry["version"] == "1.0.0"
        assert entry["token_estimate"] == 1500
        assert entry["priority_hint"] == 65
        assert "installed_at" in entry
        assert "last_updated" in entry

    def test_install_action_skill_copies_handler(
        self, tmp_skills_dir: Path, valid_action_skill: Path
    ) -> None:
        """Action Skill 安装后 handler.py 被复制到 skill_handlers/。"""
        installer = SkillInstaller(tmp_skills_dir)
        result = installer.install(valid_action_skill)

        assert result.success is True
        handler_file = tmp_skills_dir / "skill_handlers" / "my_action_skill.py"
        assert handler_file.exists()
        handler_content = handler_file.read_text(encoding="utf-8")
        assert "handle_do_something" in handler_content

    def test_install_action_skill_updates_handler_path(
        self, tmp_skills_dir: Path, valid_action_skill: Path
    ) -> None:
        """Action Skill 的 tools.handler 路径被更新为安装后位置。"""
        installer = SkillInstaller(tmp_skills_dir)
        installer.install(valid_action_skill)

        registry = json.loads(
            (tmp_skills_dir / "registry.json").read_text(encoding="utf-8")
        )
        entry = next(s for s in registry["skills"] if s["id"] == "my_action_skill")
        assert "tools" in entry
        assert entry["tools"][0]["handler"] == "skill_handlers/my_action_skill.py::handle_do_something"


# ==============================================================
# 场景 2: id 冲突 → 安装失败 + 明确错误信息
# ==============================================================

class TestInstallIdConflict:
    """ID 冲突时安装失败。"""

    def test_duplicate_id_fails(
        self, tmp_skills_dir: Path, valid_knowledge_skill: Path
    ) -> None:
        """重复安装同一 id 失败。"""
        installer = SkillInstaller(tmp_skills_dir)

        # 第一次安装成功
        result1 = installer.install(valid_knowledge_skill)
        assert result1.success is True

        # 第二次安装失败
        result2 = installer.install(valid_knowledge_skill)
        assert result2.success is False
        assert result2.skill_id == "my_test_skill"
        assert any("already exists" in e for e in result2.errors)

    def test_conflict_does_not_modify_registry(
        self, tmp_skills_dir: Path, valid_knowledge_skill: Path
    ) -> None:
        """冲突时 registry 不被修改。"""
        installer = SkillInstaller(tmp_skills_dir)
        installer.install(valid_knowledge_skill)

        registry_before = (tmp_skills_dir / "registry.json").read_text(encoding="utf-8")
        installer.install(valid_knowledge_skill)
        registry_after = (tmp_skills_dir / "registry.json").read_text(encoding="utf-8")

        assert registry_before == registry_after


# ==============================================================
# 场景 3: schema 不合法 → validate() 返回具体错误列表
# ==============================================================

class TestValidateInvalidSchema:
    """Schema 不合法时 validate 返回具体错误。"""

    def test_missing_skill_json(self, tmp_path: Path) -> None:
        """缺少 skill.json。"""
        skill_dir = tmp_path / "bad_skill"
        skill_dir.mkdir()
        (skill_dir / "content.md").write_text("content")

        installer = SkillInstaller(tmp_path / "skills")
        errors = installer.validate(skill_dir)
        assert any("skill.json" in e for e in errors)

    def test_missing_content_md(self, tmp_path: Path) -> None:
        """缺少 content.md。"""
        skill_dir = tmp_path / "bad_skill"
        skill_dir.mkdir()
        skill_json = {
            "id": "bad_skill",
            "type": "knowledge",
            "name": "Bad",
            "description": "Bad skill",
            "version": "1.0.0",
            "tags": [],
            "applicable_paper_types": ["empirical"],
            "applicable_phases": ["deep_review"],
            "token_estimate": 100,
            "priority_hint": 50,
        }
        (skill_dir / "skill.json").write_text(json.dumps(skill_json))

        installer = SkillInstaller(tmp_path / "skills")
        errors = installer.validate(skill_dir)
        assert any("content.md" in e for e in errors)

    def test_invalid_id_format(self, tmp_path: Path) -> None:
        """id 格式不合法（含大写/特殊字符）。"""
        skill_dir = tmp_path / "bad_skill"
        skill_dir.mkdir()
        skill_json = {
            "id": "Bad-Skill!",
            "type": "knowledge",
            "name": "Bad",
            "description": "Bad skill",
            "version": "1.0.0",
            "tags": [],
            "applicable_paper_types": ["empirical"],
            "applicable_phases": ["deep_review"],
            "token_estimate": 100,
            "priority_hint": 50,
        }
        (skill_dir / "skill.json").write_text(json.dumps(skill_json))
        (skill_dir / "content.md").write_text("content")

        installer = SkillInstaller(tmp_path / "skills")
        errors = installer.validate(skill_dir)
        assert any("[a-z0-9_]" in e for e in errors)

    def test_invalid_type(self, tmp_path: Path) -> None:
        """type 不是 knowledge/action。"""
        skill_dir = tmp_path / "bad_skill"
        skill_dir.mkdir()
        skill_json = {
            "id": "bad_skill",
            "type": "unknown",
            "name": "Bad",
            "description": "Bad skill",
            "version": "1.0.0",
            "tags": [],
            "applicable_paper_types": ["empirical"],
            "applicable_phases": ["deep_review"],
            "token_estimate": 100,
            "priority_hint": 50,
        }
        (skill_dir / "skill.json").write_text(json.dumps(skill_json))
        (skill_dir / "content.md").write_text("content")

        installer = SkillInstaller(tmp_path / "skills")
        errors = installer.validate(skill_dir)
        assert any("'knowledge' or 'action'" in e for e in errors)

    def test_negative_token_estimate(self, tmp_path: Path) -> None:
        """token_estimate 为负数。"""
        skill_dir = tmp_path / "bad_skill"
        skill_dir.mkdir()
        skill_json = {
            "id": "bad_skill",
            "type": "knowledge",
            "name": "Bad",
            "description": "Bad skill",
            "version": "1.0.0",
            "tags": [],
            "applicable_paper_types": ["empirical"],
            "applicable_phases": ["deep_review"],
            "token_estimate": -100,
            "priority_hint": 50,
        }
        (skill_dir / "skill.json").write_text(json.dumps(skill_json))
        (skill_dir / "content.md").write_text("content")

        installer = SkillInstaller(tmp_path / "skills")
        errors = installer.validate(skill_dir)
        assert any("positive integer" in e for e in errors)

    def test_priority_hint_out_of_range(self, tmp_path: Path) -> None:
        """priority_hint 超出 0-100 范围。"""
        skill_dir = tmp_path / "bad_skill"
        skill_dir.mkdir()
        skill_json = {
            "id": "bad_skill",
            "type": "knowledge",
            "name": "Bad",
            "description": "Bad skill",
            "version": "1.0.0",
            "tags": [],
            "applicable_paper_types": ["empirical"],
            "applicable_phases": ["deep_review"],
            "token_estimate": 100,
            "priority_hint": 150,
        }
        (skill_dir / "skill.json").write_text(json.dumps(skill_json))
        (skill_dir / "content.md").write_text("content")

        installer = SkillInstaller(tmp_path / "skills")
        errors = installer.validate(skill_dir)
        assert any("0-100" in e for e in errors)

    def test_missing_required_fields(self, tmp_path: Path) -> None:
        """缺少多个必填字段。"""
        skill_dir = tmp_path / "bad_skill"
        skill_dir.mkdir()
        skill_json = {"id": "bad_skill", "type": "knowledge"}
        (skill_dir / "skill.json").write_text(json.dumps(skill_json))
        (skill_dir / "content.md").write_text("content")

        installer = SkillInstaller(tmp_path / "skills")
        errors = installer.validate(skill_dir)
        # 应该报告多个缺失字段
        assert len(errors) >= 3
        assert any("name" in e for e in errors)

    def test_invalid_json(self, tmp_path: Path) -> None:
        """skill.json 不是合法 JSON。"""
        skill_dir = tmp_path / "bad_skill"
        skill_dir.mkdir()
        (skill_dir / "skill.json").write_text("{invalid json!!!")
        (skill_dir / "content.md").write_text("content")

        installer = SkillInstaller(tmp_path / "skills")
        errors = installer.validate(skill_dir)
        assert any("not valid JSON" in e for e in errors)

    def test_action_skill_missing_handler(self, tmp_path: Path) -> None:
        """Action Skill 缺少 handler.py。"""
        skill_dir = tmp_path / "bad_action"
        skill_dir.mkdir()
        skill_json = {
            "id": "bad_action",
            "type": "action",
            "name": "Bad Action",
            "description": "Missing handler",
            "version": "1.0.0",
            "tags": [],
            "applicable_paper_types": ["empirical"],
            "applicable_phases": ["synthesis"],
            "token_estimate": 100,
            "priority_hint": 50,
            "tools": [
                {
                    "name": "tool1",
                    "description": "desc",
                    "input_schema": {},
                    "handler": "handler.py::func",
                }
            ],
        }
        (skill_dir / "skill.json").write_text(json.dumps(skill_json))
        (skill_dir / "content.md").write_text("content")

        installer = SkillInstaller(tmp_path / "skills")
        errors = installer.validate(skill_dir)
        assert any("handler.py" in e for e in errors)

    def test_nonexistent_directory(self, tmp_path: Path) -> None:
        """Skill 目录不存在。"""
        installer = SkillInstaller(tmp_path / "skills")
        errors = installer.validate(tmp_path / "nonexistent")
        assert any("does not exist" in e for e in errors)


# ==============================================================
# 场景 4: uninstall → registry 移除条目 + 文件删除
# ==============================================================

class TestUninstall:
    """卸载 Skill。"""

    def test_uninstall_removes_from_registry(
        self, tmp_skills_dir: Path, valid_knowledge_skill: Path
    ) -> None:
        """卸载后 registry 中不再有该条目。"""
        installer = SkillInstaller(tmp_skills_dir)
        installer.install(valid_knowledge_skill)

        result = installer.uninstall("my_test_skill")
        assert result.success is True

        registry = json.loads(
            (tmp_skills_dir / "registry.json").read_text(encoding="utf-8")
        )
        skill_ids = [s["id"] for s in registry["skills"]]
        assert "my_test_skill" not in skill_ids

    def test_uninstall_deletes_content_file(
        self, tmp_skills_dir: Path, valid_knowledge_skill: Path
    ) -> None:
        """卸载后 content 文件被删除。"""
        installer = SkillInstaller(tmp_skills_dir)
        installer.install(valid_knowledge_skill)

        content_file = tmp_skills_dir / "my_test_skill.md"
        assert content_file.exists()

        installer.uninstall("my_test_skill")
        assert not content_file.exists()

    def test_uninstall_deletes_handler_file(
        self, tmp_skills_dir: Path, valid_action_skill: Path
    ) -> None:
        """卸载 Action Skill 后 handler 文件被删除。"""
        installer = SkillInstaller(tmp_skills_dir)
        installer.install(valid_action_skill)

        handler_file = tmp_skills_dir / "skill_handlers" / "my_action_skill.py"
        assert handler_file.exists()

        installer.uninstall("my_action_skill")
        assert not handler_file.exists()

    def test_uninstall_nonexistent_skill_fails(
        self, tmp_skills_dir: Path
    ) -> None:
        """卸载不存在的 Skill 失败。"""
        installer = SkillInstaller(tmp_skills_dir)
        result = installer.uninstall("nonexistent_skill")

        assert result.success is False
        assert any("not found" in e for e in result.errors)

    def test_reinstall_after_uninstall(
        self, tmp_skills_dir: Path, valid_knowledge_skill: Path
    ) -> None:
        """卸载后可以重新安装。"""
        installer = SkillInstaller(tmp_skills_dir)

        # 安装 → 卸载 → 重新安装
        installer.install(valid_knowledge_skill)
        installer.uninstall("my_test_skill")
        result = installer.install(valid_knowledge_skill)

        assert result.success is True


# ==============================================================
# 场景 5: 安装后 Agent 下次启动能加载该 Skill（集成验证）
# ==============================================================

class TestIntegrationWithRegistry:
    """安装后 SkillRegistry 能正确加载。"""

    def test_installed_skill_queryable(
        self, tmp_skills_dir: Path, valid_knowledge_skill: Path
    ) -> None:
        """安装后通过 SkillRegistry.query() 能查到该 Skill。"""
        from core.skill_registry import SkillRegistry

        installer = SkillInstaller(tmp_skills_dir)
        installer.install(valid_knowledge_skill)

        # 模拟 Agent 重启：新建 SkillRegistry 实例
        registry = SkillRegistry(tmp_skills_dir)
        results = registry.query(
            paper_type="empirical", phase="deep_review", budget_tokens=5000
        )
        result_ids = [s.id for s in results]
        assert "my_test_skill" in result_ids

    def test_installed_skill_content_loadable(
        self, tmp_skills_dir: Path, valid_knowledge_skill: Path
    ) -> None:
        """安装后通过 SkillRegistry.load_content() 能读取内容。"""
        from core.skill_registry import SkillRegistry

        installer = SkillInstaller(tmp_skills_dir)
        installer.install(valid_knowledge_skill)

        registry = SkillRegistry(tmp_skills_dir)
        content = registry.load_content("my_test_skill")
        assert content is not None
        assert "My Test Skill" in content
        assert "Rule 1" in content

    def test_installed_action_skill_tools_registered(
        self, tmp_skills_dir: Path, valid_action_skill: Path
    ) -> None:
        """安装后 Action Skill 的 tools 能被 SkillRegistry 解析。"""
        from core.skill_registry import SkillRegistry

        installer = SkillInstaller(tmp_skills_dir)
        installer.install(valid_action_skill)

        registry = SkillRegistry(tmp_skills_dir)
        tools = registry.load_tools_from_markdown("my_action_skill")
        assert len(tools) == 1
        assert tools[0].name == "do_something"
        assert "skill_handlers/my_action_skill.py" in tools[0].handler

    def test_uninstalled_skill_not_queryable(
        self, tmp_skills_dir: Path, valid_knowledge_skill: Path
    ) -> None:
        """卸载后 SkillRegistry 查不到该 Skill。"""
        from core.skill_registry import SkillRegistry

        installer = SkillInstaller(tmp_skills_dir)
        installer.install(valid_knowledge_skill)
        installer.uninstall("my_test_skill")

        # 模拟 Agent 重启
        registry = SkillRegistry(tmp_skills_dir)
        results = registry.query(
            paper_type="empirical", phase="deep_review", budget_tokens=5000
        )
        result_ids = [s.id for s in results]
        assert "my_test_skill" not in result_ids


# ==============================================================
# 边界情况
# ==============================================================

class TestEdgeCases:
    """边界情况测试。"""

    def test_validate_returns_empty_for_valid_skill(
        self, tmp_skills_dir: Path, valid_knowledge_skill: Path
    ) -> None:
        """合法 Skill 包 validate 返回空列表。"""
        installer = SkillInstaller(tmp_skills_dir)
        errors = installer.validate(valid_knowledge_skill)
        assert errors == []

    def test_install_result_dataclass(self) -> None:
        """InstallResult 数据类基本行为。"""
        result = InstallResult(success=True, skill_id="test", message="ok")
        assert result.success is True
        assert result.errors == []

    def test_install_with_no_registry_file(
        self, tmp_path: Path, valid_knowledge_skill: Path
    ) -> None:
        """skills 目录存在但无 registry.json 时仍能安装。"""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "skill_handlers").mkdir()

        installer = SkillInstaller(skills_dir)
        result = installer.install(valid_knowledge_skill)

        assert result.success is True
        # registry.json 被创建
        assert (skills_dir / "registry.json").exists()
