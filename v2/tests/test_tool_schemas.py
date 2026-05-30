"""
tests/test_tool_schemas.py — 工具 Schema 中心注册表的单元测试

验证:
    1. TOOL_REGISTRY 完整性（26 个工具，每个 schema 结构正确）
    2. Persona 工具名称列表正确性
    3. get_tools_for_persona() 基本功能
    4. get_tools_for_persona() 的 description 覆盖
    5. get_tools_for_persona() 的 property-level description 覆盖
    6. TOOL_REGISTRY 不可变性（调用不会修改原始 registry）
    7. validate_registry() 正常通过及错误检测
    8. 覆盖字典引用完整性（无过期引用）
    9. 跨 persona 无对象共享（独立性）
   10. identity.py 公共 API 与 tool_schemas 一致
"""

import copy
import pytest

from core.tool_schemas import (
    TOOL_REGISTRY,
    SCHOLAR_TOOL_NAMES,
    WRITER_TOOL_NAMES,
    CODE_REVIEWER_TOOL_NAMES,
    _WRITER_DESC_OVERRIDES,
    _CODE_REVIEWER_DESC_OVERRIDES,
    _WRITER_PROP_OVERRIDES,
    _CODE_REVIEWER_PROP_OVERRIDES,
    get_tools_for_persona,
    validate_registry,
)


# ============================================================
# 1. TOOL_REGISTRY 完整性
# ============================================================

class TestToolRegistryIntegrity:
    """TOOL_REGISTRY 结构校验。"""

    def test_registry_has_26_tools(self):
        """注册表包含 26 个工具。"""
        assert len(TOOL_REGISTRY) == 26

    def test_each_tool_has_required_keys(self):
        """每个 schema 包含 name, description, input_schema。"""
        for name, schema in TOOL_REGISTRY.items():
            assert "name" in schema, f"{name}: missing 'name'"
            assert "description" in schema, f"{name}: missing 'description'"
            assert "input_schema" in schema, f"{name}: missing 'input_schema'"
            assert schema["name"] == name, f"Key '{name}' != schema.name '{schema['name']}'"

    def test_input_schema_structure(self):
        """每个 input_schema 有 type=object 和 properties dict。"""
        for name, schema in TOOL_REGISTRY.items():
            input_schema = schema["input_schema"]
            assert input_schema.get("type") == "object", f"{name}: type != 'object'"
            assert isinstance(input_schema.get("properties"), dict), f"{name}: properties not dict"

    def test_all_properties_have_type(self):
        """每个 property 有 type 或 enum 或 oneOf 声明。"""
        for name, schema in TOOL_REGISTRY.items():
            for prop_name, prop_def in schema["input_schema"]["properties"].items():
                has_type_info = (
                    "type" in prop_def or
                    "enum" in prop_def or
                    "oneOf" in prop_def
                )
                assert has_type_info, f"{name}.{prop_name}: no type/enum/oneOf"

    def test_all_properties_have_description(self):
        """每个 property 有 description 字段。"""
        for name, schema in TOOL_REGISTRY.items():
            for prop_name, prop_def in schema["input_schema"]["properties"].items():
                assert "description" in prop_def, f"{name}.{prop_name}: no description"

    def test_required_field_references_valid_properties(self):
        """required 列表中的字段名都在 properties 中存在。"""
        for name, schema in TOOL_REGISTRY.items():
            required = schema["input_schema"].get("required", [])
            props = schema["input_schema"]["properties"]
            for req_name in required:
                assert req_name in props, (
                    f"{name}: required field '{req_name}' not in properties"
                )

    def test_descriptions_are_non_empty_strings(self):
        """所有 description 都是非空字符串。"""
        for name, schema in TOOL_REGISTRY.items():
            assert isinstance(schema["description"], str)
            assert len(schema["description"]) > 10, f"{name}: description too short"


# ============================================================
# 2. Persona 工具名称列表
# ============================================================

class TestPersonaToolNames:
    """Persona 名称列表正确性。"""

    def test_scholar_has_26_tools(self):
        assert len(SCHOLAR_TOOL_NAMES) == 26

    def test_writer_has_10_tools(self):
        assert len(WRITER_TOOL_NAMES) == 10

    def test_code_reviewer_has_8_tools(self):
        assert len(CODE_REVIEWER_TOOL_NAMES) == 8

    def test_no_duplicates_in_lists(self):
        """名称列表无重复。"""
        assert len(set(SCHOLAR_TOOL_NAMES)) == len(SCHOLAR_TOOL_NAMES)
        assert len(set(WRITER_TOOL_NAMES)) == len(WRITER_TOOL_NAMES)
        assert len(set(CODE_REVIEWER_TOOL_NAMES)) == len(CODE_REVIEWER_TOOL_NAMES)

    def test_writer_is_subset_of_scholar(self):
        """Writer 工具是 Scholar 工具的子集。"""
        assert set(WRITER_TOOL_NAMES).issubset(set(SCHOLAR_TOOL_NAMES))

    def test_code_reviewer_is_subset_of_scholar(self):
        """CodeReviewer 工具是 Scholar 工具的子集。"""
        assert set(CODE_REVIEWER_TOOL_NAMES).issubset(set(SCHOLAR_TOOL_NAMES))

    def test_all_names_in_registry(self):
        """所有名称列表中的工具都在 TOOL_REGISTRY 中。"""
        all_names = set(SCHOLAR_TOOL_NAMES) | set(WRITER_TOOL_NAMES) | set(CODE_REVIEWER_TOOL_NAMES)
        for name in all_names:
            assert name in TOOL_REGISTRY, f"'{name}' not in TOOL_REGISTRY"


# ============================================================
# 3. get_tools_for_persona — 基本功能
# ============================================================

class TestGetToolsForPersonaBasic:
    """get_tools_for_persona 基本行为。"""

    def test_returns_list_for_scholar(self):
        tools = get_tools_for_persona("scholar")
        assert isinstance(tools, list)
        assert len(tools) == 26

    def test_returns_list_for_writer(self):
        tools = get_tools_for_persona("writer")
        assert isinstance(tools, list)
        assert len(tools) == 10

    def test_returns_list_for_code_reviewer(self):
        tools = get_tools_for_persona("code_reviewer")
        assert isinstance(tools, list)
        assert len(tools) == 8

    def test_invalid_persona_raises_key_error(self):
        with pytest.raises(KeyError, match="Unknown persona"):
            get_tools_for_persona("hacker")

    def test_tool_order_matches_name_list(self):
        """返回的工具顺序与 NAME 列表一致。"""
        tools = get_tools_for_persona("scholar")
        names = [t["name"] for t in tools]
        assert names == SCHOLAR_TOOL_NAMES

    def test_each_returned_tool_has_full_schema(self):
        """返回的每个工具有完整 schema。"""
        for persona in ("scholar", "writer", "code_reviewer"):
            tools = get_tools_for_persona(persona)
            for tool in tools:
                assert "name" in tool
                assert "description" in tool
                assert "input_schema" in tool
                assert "type" in tool["input_schema"]
                assert "properties" in tool["input_schema"]


# ============================================================
# 4. get_tools_for_persona — 工具级 description 覆盖
# ============================================================

class TestDescriptionOverrides:
    """工具顶层 description 覆盖验证。"""

    def test_scholar_uses_registry_descriptions(self):
        """Scholar 使用 registry 原始 description。"""
        tools = get_tools_for_persona("scholar")
        for tool in tools:
            expected = TOOL_REGISTRY[tool["name"]]["description"]
            assert tool["description"] == expected, (
                f"Scholar/{tool['name']}: description differs from registry"
            )

    def test_writer_overrides_applied(self):
        """Writer 的 override 正确应用。"""
        tools = get_tools_for_persona("writer")
        by_name = {t["name"]: t for t in tools}
        for tool_name, expected_desc in _WRITER_DESC_OVERRIDES.items():
            assert by_name[tool_name]["description"] == expected_desc

    def test_writer_non_overridden_use_registry(self):
        """Writer 中没有 override 的工具使用 registry 原始值。"""
        tools = get_tools_for_persona("writer")
        by_name = {t["name"]: t for t in tools}
        for tool_name in WRITER_TOOL_NAMES:
            if tool_name not in _WRITER_DESC_OVERRIDES:
                assert by_name[tool_name]["description"] == TOOL_REGISTRY[tool_name]["description"]

    def test_code_reviewer_overrides_applied(self):
        """CodeReviewer 的 override 正确应用。"""
        tools = get_tools_for_persona("code_reviewer")
        by_name = {t["name"]: t for t in tools}
        for tool_name, expected_desc in _CODE_REVIEWER_DESC_OVERRIDES.items():
            assert by_name[tool_name]["description"] == expected_desc


# ============================================================
# 5. get_tools_for_persona — property-level description 覆盖
# ============================================================

class TestPropertyOverrides:
    """input_schema.properties 级别的 description 覆盖。"""

    def test_writer_prop_overrides_applied(self):
        """Writer 的 property description 正确应用。"""
        tools = get_tools_for_persona("writer")
        by_name = {t["name"]: t for t in tools}
        for tool_name, prop_map in _WRITER_PROP_OVERRIDES.items():
            props = by_name[tool_name]["input_schema"]["properties"]
            for prop_name, expected_desc in prop_map.items():
                actual_desc = props[prop_name]["description"]
                assert actual_desc == expected_desc, (
                    f"writer/{tool_name}.{prop_name}: "
                    f"expected={expected_desc[:50]}..., actual={actual_desc[:50]}..."
                )

    def test_code_reviewer_prop_overrides_applied(self):
        """CodeReviewer 的 property description 正确应用。"""
        tools = get_tools_for_persona("code_reviewer")
        by_name = {t["name"]: t for t in tools}
        for tool_name, prop_map in _CODE_REVIEWER_PROP_OVERRIDES.items():
            props = by_name[tool_name]["input_schema"]["properties"]
            for prop_name, expected_desc in prop_map.items():
                actual_desc = props[prop_name]["description"]
                assert actual_desc == expected_desc, (
                    f"code_reviewer/{tool_name}.{prop_name}: "
                    f"expected={expected_desc[:50]}..., actual={actual_desc[:50]}..."
                )

    def test_scholar_props_match_registry(self):
        """Scholar 的 property descriptions 完全等于 registry 原始值。"""
        tools = get_tools_for_persona("scholar")
        for tool in tools:
            registry_props = TOOL_REGISTRY[tool["name"]]["input_schema"]["properties"]
            for prop_name, prop_def in tool["input_schema"]["properties"].items():
                expected_desc = registry_props[prop_name].get("description", "")
                actual_desc = prop_def.get("description", "")
                assert actual_desc == expected_desc, (
                    f"scholar/{tool['name']}.{prop_name}: differs from registry"
                )

    def test_non_overridden_props_unchanged(self):
        """Writer 工具中未被 override 的 property 保持 registry 原始值。"""
        tools = get_tools_for_persona("writer")
        by_name = {t["name"]: t for t in tools}
        for tool_name in WRITER_TOOL_NAMES:
            registry_props = TOOL_REGISTRY[tool_name]["input_schema"]["properties"]
            tool_props = by_name[tool_name]["input_schema"]["properties"]
            overridden = set(_WRITER_PROP_OVERRIDES.get(tool_name, {}).keys())
            for prop_name in registry_props:
                if prop_name not in overridden:
                    expected = registry_props[prop_name].get("description", "")
                    actual = tool_props[prop_name].get("description", "")
                    assert actual == expected, (
                        f"writer/{tool_name}.{prop_name}: should be unchanged but differs"
                    )


# ============================================================
# 6. TOOL_REGISTRY 不可变性
# ============================================================

class TestRegistryImmutability:
    """get_tools_for_persona() 不会修改 TOOL_REGISTRY。"""

    def test_registry_unchanged_after_calls(self):
        """多次调用后 registry 不变。"""
        # 先做快照
        snapshot = copy.deepcopy(TOOL_REGISTRY)

        # 调用所有 persona
        _ = get_tools_for_persona("scholar")
        _ = get_tools_for_persona("writer")
        _ = get_tools_for_persona("code_reviewer")

        # 验证不变
        assert TOOL_REGISTRY == snapshot

    def test_modifying_returned_tool_doesnt_affect_registry(self):
        """修改返回的 tool dict 不影响 registry。"""
        tools = get_tools_for_persona("writer")
        original_desc = TOOL_REGISTRY["read_section"]["description"]

        # 修改返回的 tool
        read_section = next(t for t in tools if t["name"] == "read_section")
        read_section["description"] = "HACKED"

        # registry 应该不变
        assert TOOL_REGISTRY["read_section"]["description"] == original_desc

    def test_modifying_returned_props_doesnt_affect_registry(self):
        """修改返回工具的 properties 不影响 registry。"""
        tools = get_tools_for_persona("writer")
        read_section = next(t for t in tools if t["name"] == "read_section")

        # 获取 registry 原始值
        original_props = copy.deepcopy(TOOL_REGISTRY["read_section"]["input_schema"]["properties"])

        # 修改返回的 properties
        read_section["input_schema"]["properties"]["section"]["description"] = "HACKED_PROP"

        # registry 应该不变
        assert TOOL_REGISTRY["read_section"]["input_schema"]["properties"] == original_props


# ============================================================
# 7. validate_registry()
# ============================================================

class TestValidateRegistry:
    """validate_registry 功能验证。"""

    def test_passes_on_valid_state(self):
        """当前状态应该通过验证（import 时已调用一次）。"""
        # 不应该 raise
        validate_registry()

    def test_would_fail_with_bad_name(self, monkeypatch):
        """如果名称列表引用了不存在的工具，应该 raise ValueError。"""
        bad_names = SCHOLAR_TOOL_NAMES + ["nonexistent_fake_tool"]
        monkeypatch.setattr(
            "core.tool_schemas.SCHOLAR_TOOL_NAMES", bad_names
        )
        with pytest.raises(ValueError, match="nonexistent_fake_tool"):
            validate_registry()


# ============================================================
# 8. 覆盖字典引用完整性
# ============================================================

class TestOverrideDictIntegrity:
    """Override 字典不引用过期的工具名或 property 名。"""

    def test_writer_desc_overrides_reference_valid_tools(self):
        for tool_name in _WRITER_DESC_OVERRIDES:
            assert tool_name in WRITER_TOOL_NAMES, (
                f"_WRITER_DESC_OVERRIDES references '{tool_name}' not in WRITER_TOOL_NAMES"
            )

    def test_code_reviewer_desc_overrides_reference_valid_tools(self):
        for tool_name in _CODE_REVIEWER_DESC_OVERRIDES:
            assert tool_name in CODE_REVIEWER_TOOL_NAMES, (
                f"_CODE_REVIEWER_DESC_OVERRIDES references '{tool_name}' not in CODE_REVIEWER_TOOL_NAMES"
            )

    def test_writer_prop_overrides_reference_valid_tools_and_props(self):
        for tool_name, prop_map in _WRITER_PROP_OVERRIDES.items():
            assert tool_name in WRITER_TOOL_NAMES, (
                f"_WRITER_PROP_OVERRIDES references tool '{tool_name}' not in WRITER_TOOL_NAMES"
            )
            valid_props = set(TOOL_REGISTRY[tool_name]["input_schema"]["properties"].keys())
            for prop_name in prop_map:
                assert prop_name in valid_props, (
                    f"_WRITER_PROP_OVERRIDES['{tool_name}'] references "
                    f"property '{prop_name}' not in schema (valid: {valid_props})"
                )

    def test_code_reviewer_prop_overrides_reference_valid_tools_and_props(self):
        for tool_name, prop_map in _CODE_REVIEWER_PROP_OVERRIDES.items():
            assert tool_name in CODE_REVIEWER_TOOL_NAMES, (
                f"_CODE_REVIEWER_PROP_OVERRIDES references tool '{tool_name}' "
                f"not in CODE_REVIEWER_TOOL_NAMES"
            )
            valid_props = set(TOOL_REGISTRY[tool_name]["input_schema"]["properties"].keys())
            for prop_name in prop_map:
                assert prop_name in valid_props, (
                    f"_CODE_REVIEWER_PROP_OVERRIDES['{tool_name}'] references "
                    f"property '{prop_name}' not in schema (valid: {valid_props})"
                )


# ============================================================
# 9. 跨 Persona 独立性（无对象共享风险）
# ============================================================

class TestCrossPersonaIndependence:
    """不同 Persona 返回的同名工具是独立对象。"""

    def test_writer_and_scholar_read_section_are_different_objects(self):
        """Writer 和 Scholar 的 read_section 是不同对象（因为有 override）。"""
        s_tools = get_tools_for_persona("scholar")
        w_tools = get_tools_for_persona("writer")
        s_read = next(t for t in s_tools if t["name"] == "read_section")
        w_read = next(t for t in w_tools if t["name"] == "read_section")
        # 它们有不同的 description
        assert s_read["description"] != w_read["description"]
        # 它们应该是不同对象
        assert s_read is not w_read

    def test_code_reviewer_and_scholar_read_section_different(self):
        """CodeReviewer 和 Scholar 的 read_section 是不同对象。"""
        s_tools = get_tools_for_persona("scholar")
        cr_tools = get_tools_for_persona("code_reviewer")
        s_read = next(t for t in s_tools if t["name"] == "read_section")
        cr_read = next(t for t in cr_tools if t["name"] == "read_section")
        assert s_read["description"] != cr_read["description"]
        assert s_read is not cr_read

    def test_scholar_tools_without_override_share_registry_ref(self):
        """Scholar 无 override，返回 registry 原始引用（性能优化，调用者只读）。"""
        tools = get_tools_for_persona("scholar")
        for tool in tools:
            # Scholar 没有任何 override，直接返回 registry 原始引用（只读语义）
            assert tool is TOOL_REGISTRY[tool["name"]]


# ============================================================
# 10. identity.py 公共 API 一致性
# ============================================================

class TestIdentityAPIConsistency:
    """identity.py 导出的工具列表与 tool_schemas 一致。"""

    def test_scholar_tools_match(self):
        from core.identity import SCHOLAR_TOOLS
        expected = get_tools_for_persona("scholar")
        assert SCHOLAR_TOOLS == expected

    def test_writer_tools_match(self):
        from core.identity import WRITER_TOOLS
        expected = get_tools_for_persona("writer")
        assert WRITER_TOOLS == expected

    def test_code_reviewer_tools_match(self):
        from core.identity import CODE_REVIEWER_TOOLS
        expected = get_tools_for_persona("code_reviewer")
        assert CODE_REVIEWER_TOOLS == expected

    def test_sub_perspective_tools_count(self):
        from core.identity import SUB_PERSPECTIVE_TOOLS, _SUB_PERSPECTIVE_EXCLUDED_TOOLS
        expected_count = len(SCHOLAR_TOOL_NAMES) - len(
            [n for n in SCHOLAR_TOOL_NAMES if n in _SUB_PERSPECTIVE_EXCLUDED_TOOLS]
        )
        assert len(SUB_PERSPECTIVE_TOOLS) == expected_count
