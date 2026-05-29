"""
Tests for core/tool_metadata.py — Tool metadata registry and risk assessment.

Covers:
- All tools have valid metadata
- Risk level computation
- Operation/scope classification
- Integration with action_router
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from core.tool_metadata import (
    ToolMeta,
    TOOL_META,
    get_tool_meta,
    assess_risk_level,
    get_risk_summary,
    get_tools_by_operation,
)


class TestToolMetaRegistry:
    """Test that all tools are properly registered."""

    def test_all_schema_tools_have_meta(self):
        """Every tool in tool_schemas.py should have metadata."""
        from core.tool_schemas import TOOLS

        schema_names = {t["name"] for t in TOOLS}
        meta_names = set(TOOL_META.keys())

        missing = schema_names - meta_names
        assert missing == set(), f"Tools missing metadata: {missing}"

    def test_no_orphan_meta(self):
        """No metadata entries for non-existent tools."""
        from core.tool_schemas import TOOLS

        schema_names = {t["name"] for t in TOOLS}
        meta_names = set(TOOL_META.keys())

        orphans = meta_names - schema_names
        assert orphans == set(), f"Orphan metadata entries: {orphans}"

    def test_valid_operation_values(self):
        """All operation values are from the allowed set."""
        valid_ops = {"read", "write", "verify", "meta"}
        for name, meta in TOOL_META.items():
            assert meta.operation in valid_ops, (
                f"Tool '{name}' has invalid operation: {meta.operation}"
            )

    def test_valid_scope_values(self):
        """All scope values are from the allowed set."""
        valid_scopes = {"sentence", "paragraph", "section", "paper", "external", "system"}
        for name, meta in TOOL_META.items():
            assert meta.scope in valid_scopes, (
                f"Tool '{name}' has invalid scope: {meta.scope}"
            )


class TestRiskAssessment:
    """Test risk level computation."""

    def test_read_tools_are_low_risk(self):
        """All read-only tools should be low risk."""
        for name, meta in TOOL_META.items():
            if meta.operation == "read":
                assert assess_risk_level(name) == "low", (
                    f"Read tool '{name}' should be low risk"
                )

    def test_verify_tools_are_low_risk(self):
        """Verification tools should be low risk."""
        for name, meta in TOOL_META.items():
            if meta.operation == "verify":
                assert assess_risk_level(name) == "low", (
                    f"Verify tool '{name}' should be low risk"
                )

    def test_requires_confirmation_is_high_risk(self):
        """Tools requiring confirmation are always high risk."""
        for name, meta in TOOL_META.items():
            if meta.requires_confirmation:
                assert assess_risk_level(name) == "high", (
                    f"Tool '{name}' requires confirmation but is not high risk"
                )

    def test_irreversible_write_is_high_risk(self):
        """Irreversible write operations are high risk."""
        for name, meta in TOOL_META.items():
            if meta.operation == "write" and not meta.reversible:
                assert assess_risk_level(name) == "high", (
                    f"Irreversible write '{name}' should be high risk"
                )

    def test_unknown_tool_is_medium(self):
        """Unknown tools default to medium risk."""
        assert assess_risk_level("nonexistent_tool_xyz") == "medium"

    def test_specific_known_risks(self):
        """Verify specific expected risk levels."""
        assert assess_risk_level("read_section") == "low"
        assert assess_risk_level("review_paper") == "low"
        assert assess_risk_level("rewrite_section") == "medium"
        assert assess_risk_level("approve_fix") == "high"
        assert assess_risk_level("commit_rewrite") == "high"


class TestHelperFunctions:
    """Test utility functions."""

    def test_get_tool_meta_found(self):
        meta = get_tool_meta("parse_paper")
        assert meta is not None
        assert meta.operation == "read"

    def test_get_tool_meta_not_found(self):
        meta = get_tool_meta("totally_fake_tool")
        assert meta is None

    def test_get_risk_summary(self):
        summary = get_risk_summary()
        assert "high" in summary
        assert "medium" in summary
        assert "low" in summary
        assert sum(summary.values()) == len(TOOL_META)

    def test_get_tools_by_operation(self):
        read_tools = get_tools_by_operation("read")
        assert "read_section" in read_tools
        assert "rewrite_section" not in read_tools

        write_tools = get_tools_by_operation("write")
        assert "rewrite_section" in write_tools
        assert "read_section" not in write_tools


class TestActionRouterIntegration:
    """Test that action_router can use metadata."""

    def test_assess_risk_from_meta_function_exists(self):
        """The _assess_risk_from_meta function is importable."""
        from tools.action_router import _assess_risk_from_meta
        assert callable(_assess_risk_from_meta)

    def test_high_risk_returns_confirm(self):
        from tools.action_router import _assess_risk_from_meta
        result = _assess_risk_from_meta("approve_fix")
        assert result == "confirm_fix"

    def test_low_risk_returns_none(self):
        from tools.action_router import _assess_risk_from_meta
        result = _assess_risk_from_meta("read_section")
        assert result is None

    def test_unknown_tool_returns_none(self):
        from tools.action_router import _assess_risk_from_meta
        result = _assess_risk_from_meta("unknown_tool_abc")
        assert result is None
