"""
tests/test_v2_skill_lifecycle.py — B5 Skill Lifecycle 单元测试

覆盖 V2 B5 验收标准:
    1. registry.json 添加 lifecycle 字段后可被正确 parse（向后兼容）
    2. status: "inactive" 的 Skill 不被 load_all_active() 加载
    3. activate_skill() / deactivate_skill() 正确修改 registry 并持久化
    4. 多个 action skill 能被 load_all_active() 自动发现并注册
    5. SkillRegistryManager list_skills / get_skill 正确工作
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.skill_lifecycle import SkillLifecycleMeta, SkillRegistryManager
from core.skill_handler_loader import SkillHandlerLoader


# ==============================================================
# Fixtures
# ==============================================================


def _make_registry(tmp_path: Path, skills: list[dict]) -> Path:
    """创建临时 registry.json。"""
    data = {"version": "1.1", "skills": skills}
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return registry_path


def _make_handler_file(tmp_path: Path, module_name: str, func_name: str) -> None:
    """在 tmp_path/skill_handlers/ 下创建 mock handler 文件。"""
    handlers_dir = tmp_path / "skill_handlers"
    handlers_dir.mkdir(exist_ok=True)
    handler_file = handlers_dir / f"{module_name}.py"
    handler_file.write_text(
        f"def {func_name}(args: dict, state) -> str:\n"
        f"    return 'mock_result_from_{func_name}'\n",
        encoding="utf-8",
    )


@pytest.fixture
def registry_with_lifecycle(tmp_path: Path) -> Path:
    """创建包含 lifecycle 字段的完整 registry。"""
    skills = [
        {
            "id": "active_knowledge",
            "version": "1.0.0",
            "status": "active",
            "installed_at": "2025-07-01",
            "last_updated": "2025-07-01",
            "type": "knowledge",
            "file": "active_knowledge.md",
            "name": "Active Knowledge Skill",
            "description": "An active knowledge skill",
            "tags": ["test"],
            "applicable_paper_types": ["empirical"],
            "applicable_phases": ["deep_review"],
            "token_estimate": 500,
            "priority_hint": 70,
        },
        {
            "id": "inactive_action",
            "version": "1.2.0",
            "status": "inactive",
            "installed_at": "2025-06-15",
            "last_updated": "2025-07-01",
            "type": "action",
            "file": "inactive_action.md",
            "name": "Inactive Action Skill",
            "description": "An inactive action skill",
            "tags": ["export"],
            "applicable_paper_types": [],
            "applicable_phases": ["synthesis"],
            "token_estimate": 300,
            "priority_hint": 60,
            "tools": [
                {
                    "name": "inactive_tool",
                    "description": "Should not be loaded",
                    "handler": "skill_handlers/inactive_handler.py::handle_inactive",
                }
            ],
        },
        {
            "id": "active_action",
            "version": "2.0.0",
            "status": "active",
            "installed_at": "2025-07-01",
            "last_updated": "2025-07-01",
            "type": "action",
            "file": "active_action.md",
            "name": "Active Action Skill",
            "description": "An active action skill",
            "tags": ["export", "report"],
            "applicable_paper_types": ["empirical", "theoretical"],
            "applicable_phases": ["synthesis"],
            "token_estimate": 200,
            "priority_hint": 65,
            "tools": [
                {
                    "name": "active_tool_1",
                    "description": "First tool",
                    "handler": "skill_handlers/active_handler.py::handle_active_1",
                },
                {
                    "name": "active_tool_2",
                    "description": "Second tool",
                    "handler": "skill_handlers/active_handler.py::handle_active_2",
                },
            ],
        },
        {
            "id": "deprecated_skill",
            "version": "0.9.0",
            "status": "deprecated",
            "installed_at": "2025-05-01",
            "last_updated": "2025-06-30",
            "type": "knowledge",
            "file": "deprecated.md",
            "name": "Deprecated Skill",
            "description": "A deprecated skill",
            "tags": ["old"],
            "applicable_paper_types": [],
            "applicable_phases": [],
            "token_estimate": 100,
            "priority_hint": 40,
        },
    ]
    registry_path = _make_registry(tmp_path, skills)

    # 创建 handler 文件
    _make_handler_file(tmp_path, "active_handler", "handle_active_1")
    # 追加第二个函数
    handlers_dir = tmp_path / "skill_handlers"
    handler_file = handlers_dir / "active_handler.py"
    handler_file.write_text(
        "def handle_active_1(args: dict, state) -> str:\n"
        "    return 'result_1'\n\n"
        "def handle_active_2(args: dict, state) -> str:\n"
        "    return 'result_2'\n",
        encoding="utf-8",
    )
    _make_handler_file(tmp_path, "inactive_handler", "handle_inactive")

    return registry_path


@pytest.fixture
def legacy_registry(tmp_path: Path) -> Path:
    """创建不含 lifecycle 字段的遗留 registry（向后兼容测试）。"""
    skills = [
        {
            "id": "legacy_skill",
            "type": "action",
            "file": "legacy.md",
            "name": "Legacy Skill",
            "description": "No lifecycle fields",
            "tags": ["legacy"],
            "applicable_paper_types": [],
            "applicable_phases": [],
            "token_estimate": 300,
            "priority_hint": 55,
            "tools": [
                {
                    "name": "legacy_tool",
                    "description": "Legacy tool",
                    "handler": "skill_handlers/legacy_handler.py::handle_legacy",
                }
            ],
        }
    ]
    registry_path = _make_registry(tmp_path, skills)
    _make_handler_file(tmp_path, "legacy_handler", "handle_legacy")
    return registry_path


# ==============================================================
# Tests: Lifecycle field parsing
# ==============================================================


class TestLifecycleParsing:
    """验证带 lifecycle 字段的 registry.json 可被正确 parse。"""

    def test_list_all_skills(self, registry_with_lifecycle: Path) -> None:
        """应列出全部 4 个 skill。"""
        mgr = SkillRegistryManager(registry_with_lifecycle)
        all_skills = mgr.list_skills()
        assert len(all_skills) == 4

    def test_lifecycle_fields_present(self, registry_with_lifecycle: Path) -> None:
        """每个 Skill 应有 version/status/installed_at/last_updated。"""
        mgr = SkillRegistryManager(registry_with_lifecycle)
        skill = mgr.get_skill("active_action")
        assert skill is not None
        assert skill.version == "2.0.0"
        assert skill.status == "active"
        assert skill.installed_at == "2025-07-01"
        assert skill.last_updated == "2025-07-01"

    def test_filter_by_status(self, registry_with_lifecycle: Path) -> None:
        """按 status 过滤应正确。"""
        mgr = SkillRegistryManager(registry_with_lifecycle)
        active = mgr.list_skills(status_filter="active")
        assert len(active) == 2  # active_knowledge + active_action
        inactive = mgr.list_skills(status_filter="inactive")
        assert len(inactive) == 1
        deprecated = mgr.list_skills(status_filter="deprecated")
        assert len(deprecated) == 1

    def test_backward_compatible_no_lifecycle_fields(self, legacy_registry: Path) -> None:
        """无 lifecycle 字段时使用默认值（version=0.0.0, status=active）。"""
        mgr = SkillRegistryManager(legacy_registry)
        skill = mgr.get_skill("legacy_skill")
        assert skill is not None
        assert skill.version == "0.0.0"
        assert skill.status == "active"  # 默认 active
        assert skill.installed_at == ""
        assert skill.last_updated == ""


# ==============================================================
# Tests: load_all_active() filtering
# ==============================================================


class TestLoadAllActive:
    """验证 SkillHandlerLoader.load_all_active() 的 active 过滤。"""

    def test_only_active_action_skills_loaded(self, registry_with_lifecycle: Path) -> None:
        """只有 status=active + type=action 的 Skill 被加载。"""
        skills_dir = registry_with_lifecycle.parent
        loader = SkillHandlerLoader(skills_dir)
        handlers = loader.load_all_active()

        # active_action 有 2 个 tools → 2 个 handler
        assert "active_tool_1" in handlers
        assert "active_tool_2" in handlers
        # inactive_action 的 tool 不应被加载
        assert "inactive_tool" not in handlers

    def test_handler_functions_callable(self, registry_with_lifecycle: Path) -> None:
        """加载的 handler 函数应可调用。"""
        skills_dir = registry_with_lifecycle.parent
        loader = SkillHandlerLoader(skills_dir)
        handlers = loader.load_all_active()

        result = handlers["active_tool_1"]({}, None)
        assert result == "result_1"
        result2 = handlers["active_tool_2"]({}, None)
        assert result2 == "result_2"

    def test_legacy_no_status_defaults_active(self, legacy_registry: Path) -> None:
        """无 status 字段的 action skill 默认 active，被加载。"""
        skills_dir = legacy_registry.parent
        loader = SkillHandlerLoader(skills_dir)
        handlers = loader.load_all_active()

        assert "legacy_tool" in handlers
        result = handlers["legacy_tool"]({}, None)
        assert result == "mock_result_from_handle_legacy"

    def test_load_failure_doesnt_block_others(self, tmp_path: Path) -> None:
        """单个 handler 加载失败不阻塞其他 Skill 加载。"""
        skills = [
            {
                "id": "broken_action",
                "version": "1.0.0",
                "status": "active",
                "type": "action",
                "file": "broken.md",
                "name": "Broken",
                "description": "handler file missing",
                "tags": [],
                "applicable_paper_types": [],
                "applicable_phases": [],
                "token_estimate": 100,
                "priority_hint": 50,
                "tools": [{"name": "broken_tool", "handler": "skill_handlers/nonexistent.py::fn"}],
            },
            {
                "id": "good_action",
                "version": "1.0.0",
                "status": "active",
                "type": "action",
                "file": "good.md",
                "name": "Good",
                "description": "works fine",
                "tags": [],
                "applicable_paper_types": [],
                "applicable_phases": [],
                "token_estimate": 100,
                "priority_hint": 55,
                "tools": [{"name": "good_tool", "handler": "skill_handlers/good_handler.py::handle_good"}],
            },
        ]
        _make_registry(tmp_path, skills)
        _make_handler_file(tmp_path, "good_handler", "handle_good")

        loader = SkillHandlerLoader(tmp_path)
        handlers = loader.load_all_active()

        # broken_tool 失败但 good_tool 成功
        assert "broken_tool" not in handlers
        assert "good_tool" in handlers


# ==============================================================
# Tests: activate / deactivate API
# ==============================================================


class TestActivateDeactivate:
    """验证 activate_skill() / deactivate_skill() 持久化。"""

    def test_deactivate_persists(self, registry_with_lifecycle: Path) -> None:
        """deactivate 后 registry.json 中 status 变为 inactive。"""
        mgr = SkillRegistryManager(registry_with_lifecycle)
        result = mgr.deactivate_skill("active_action")
        assert result is True

        # 重新读取验证持久化
        data = json.loads(registry_with_lifecycle.read_text(encoding="utf-8"))
        entry = next(s for s in data["skills"] if s["id"] == "active_action")
        assert entry["status"] == "inactive"

    def test_activate_persists(self, registry_with_lifecycle: Path) -> None:
        """activate 后 registry.json 中 status 变为 active。"""
        mgr = SkillRegistryManager(registry_with_lifecycle)
        result = mgr.activate_skill("inactive_action")
        assert result is True

        data = json.loads(registry_with_lifecycle.read_text(encoding="utf-8"))
        entry = next(s for s in data["skills"] if s["id"] == "inactive_action")
        assert entry["status"] == "active"

    def test_deactivate_updates_last_updated(self, registry_with_lifecycle: Path) -> None:
        """deactivate 后 last_updated 被更新为今天。"""
        from datetime import date

        mgr = SkillRegistryManager(registry_with_lifecycle)
        mgr.deactivate_skill("active_knowledge")

        data = json.loads(registry_with_lifecycle.read_text(encoding="utf-8"))
        entry = next(s for s in data["skills"] if s["id"] == "active_knowledge")
        assert entry["last_updated"] == date.today().isoformat()

    def test_activate_nonexistent_returns_false(self, registry_with_lifecycle: Path) -> None:
        """activate 不存在的 skill_id 返回 False。"""
        mgr = SkillRegistryManager(registry_with_lifecycle)
        result = mgr.activate_skill("nonexistent_skill")
        assert result is False

    def test_deactivate_nonexistent_returns_false(self, registry_with_lifecycle: Path) -> None:
        """deactivate 不存在的 skill_id 返回 False。"""
        mgr = SkillRegistryManager(registry_with_lifecycle)
        result = mgr.deactivate_skill("nonexistent_skill")
        assert result is False

    def test_idempotent_activate(self, registry_with_lifecycle: Path) -> None:
        """activate 已经 active 的 skill 幂等（返回 True，无副作用）。"""
        mgr = SkillRegistryManager(registry_with_lifecycle)
        result = mgr.activate_skill("active_action")
        assert result is True

        skill = mgr.get_skill("active_action")
        assert skill is not None
        assert skill.status == "active"

    def test_deactivate_then_load_all_active_excludes(self, registry_with_lifecycle: Path) -> None:
        """deactivate 后 load_all_active() 不再加载该 Skill。"""
        skills_dir = registry_with_lifecycle.parent
        mgr = SkillRegistryManager(registry_with_lifecycle)
        mgr.deactivate_skill("active_action")

        loader = SkillHandlerLoader(skills_dir)
        handlers = loader.load_all_active()
        assert "active_tool_1" not in handlers
        assert "active_tool_2" not in handlers


# ==============================================================
# Tests: Real registry.json lifecycle fields
# ==============================================================


class TestRealRegistryLifecycle:
    """验证实际 registry.json 中的 lifecycle 字段。"""

    def test_real_registry_has_lifecycle_fields(self) -> None:
        """v2/skills/registry.json 每个条目应有 version/status/installed_at/last_updated。"""
        skills_dir = Path(__file__).parent.parent / "skills"
        registry_path = skills_dir / "registry.json"
        if not registry_path.exists():
            pytest.skip("Real registry.json not found")

        data = json.loads(registry_path.read_text(encoding="utf-8"))
        for entry in data["skills"]:
            assert "version" in entry, f"Skill '{entry['id']}' missing 'version'"
            assert "status" in entry, f"Skill '{entry['id']}' missing 'status'"
            assert "installed_at" in entry, f"Skill '{entry['id']}' missing 'installed_at'"
            assert "last_updated" in entry, f"Skill '{entry['id']}' missing 'last_updated'"

    def test_real_registry_all_active(self) -> None:
        """初始状态下所有 Skill 应为 active。"""
        skills_dir = Path(__file__).parent.parent / "skills"
        registry_path = skills_dir / "registry.json"
        if not registry_path.exists():
            pytest.skip("Real registry.json not found")

        mgr = SkillRegistryManager(registry_path)
        all_skills = mgr.list_skills()
        for skill in all_skills:
            assert skill.status == "active", f"Skill '{skill.id}' is not active"
