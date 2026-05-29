"""
tests/test_phase22_integration.py — Phase 22 Integration Tests

Verifies that Phase 22 domain tools (detect_ai_signals, verify_citations)
work correctly through the Agent's cognitive loop infrastructure:
1. Harness.execute_tool dispatches correctly
2. Tool definitions in identity match implementation signatures
3. Error handling is graceful (empty input, missing params)
4. Tools return Agent-consumable summaries
5. No regression on existing tool dispatch

No LLM calls required — tests only the Harness layer.
"""

import pytest
from core.harness import Harness
from core.identity import SCHOLAR_TOOLS


# ============================================================
# Tool Registration Verification
# ============================================================

class TestToolRegistration:
    """Verify tool definitions match implementation."""

    def test_detect_ai_signals_in_tools(self):
        """detect_ai_signals should be in SCHOLAR_TOOLS."""
        names = [t["name"] for t in SCHOLAR_TOOLS]
        assert "detect_ai_signals" in names

    def test_verify_citations_in_tools(self):
        """verify_citations should be in SCHOLAR_TOOLS."""
        names = [t["name"] for t in SCHOLAR_TOOLS]
        assert "verify_citations" in names

    def test_tool_count_phase22(self):
        """Should have 14 tools after Phase 10-v2 (added generate_hypothesis, resolve_hypothesis)."""
        assert len(SCHOLAR_TOOLS) == 14

    def test_detect_ai_signals_schema(self):
        """detect_ai_signals should require 'text' parameter."""
        tool = next(t for t in SCHOLAR_TOOLS if t["name"] == "detect_ai_signals")
        assert "text" in tool["input_schema"]["properties"]
        assert "text" in tool["input_schema"]["required"]

    def test_verify_citations_schema(self):
        """verify_citations should have bib_content, tex_content, project_dir, check_orphaned."""
        tool = next(t for t in SCHOLAR_TOOLS if t["name"] == "verify_citations")
        props = tool["input_schema"]["properties"]
        assert "bib_content" in props
        assert "tex_content" in props
        assert "project_dir" in props
        assert "check_orphaned" in props
        # No required params — flexible usage
        assert tool["input_schema"]["required"] == []


# ============================================================
# Harness Dispatch: detect_ai_signals
# ============================================================

class TestHarnessDetectAiSignals:
    """Test detect_ai_signals through Harness.execute_tool."""

    def setup_method(self):
        self.harness = Harness()

    def test_dispatch_returns_string(self):
        """Should return a string summary."""
        result = self.harness.execute_tool("detect_ai_signals", {
            "text": "This is a simple test sentence for AI detection."
        })
        assert isinstance(result, str)
        assert len(result) > 0

    def test_empty_text_error(self):
        """Should return error message for empty text."""
        result = self.harness.execute_tool("detect_ai_signals", {"text": ""})
        assert "错误" in result

    def test_missing_text_error(self):
        """Should return error message for missing text param."""
        result = self.harness.execute_tool("detect_ai_signals", {})
        assert "错误" in result

    def test_ai_heavy_text_detected(self):
        """Should detect AI signals in heavily AI-sounding text."""
        ai_text = (
            "It is worth noting that this groundbreaking study leverages a "
            "multifaceted approach to delve into the intricacies of the domain. "
            "Furthermore, the findings underscore the paramount importance of "
            "navigating the complexities inherent in this rapidly evolving landscape. "
            "In conclusion, this research not only advances our understanding but "
            "also paves the way for future endeavors in this pivotal field."
        )
        result = self.harness.execute_tool("detect_ai_signals", {"text": ai_text})
        assert "FAIL" in result or "信号" in result

    def test_natural_text_passes(self):
        """Should pass for natural-sounding text."""
        natural = (
            "We ran the model on 50 test cases. Three crashed due to memory limits. "
            "The remaining 47 produced outputs within 2 standard deviations of baseline. "
            "Not great, not terrible — about what you'd expect from a first iteration."
        )
        result = self.harness.execute_tool("detect_ai_signals", {"text": natural})
        assert "PASS" in result


# ============================================================
# Harness Dispatch: verify_citations
# ============================================================

class TestHarnessVerifyCitations:
    """Test verify_citations through Harness.execute_tool."""

    def setup_method(self):
        self.harness = Harness()

    def test_dispatch_returns_string(self):
        """Should return a string summary."""
        result = self.harness.execute_tool("verify_citations", {
            "bib_content": "@article{x,\n  author={A},\n  title={Test Title},\n  journal={J},\n  year={2023},\n}"
        })
        assert isinstance(result, str)
        assert len(result) > 0

    def test_no_input_error(self):
        """Should return error when neither bib_content nor project_dir provided."""
        result = self.harness.execute_tool("verify_citations", {})
        assert "错误" in result

    def test_clean_bib(self):
        """Should report clean when all citations match."""
        bib = "@article{smith2023,\n  author={Smith},\n  title={Good Paper},\n  journal={J},\n  year={2023},\n}"
        tex = r"\cite{smith2023}"
        result = self.harness.execute_tool("verify_citations", {
            "bib_content": bib,
            "tex_content": tex,
        })
        assert "✅" in result or "通过" in result

    def test_undefined_ref_detected(self):
        """Should detect undefined references."""
        bib = "@article{exists2023,\n  author={A},\n  title={Paper Title},\n  journal={J},\n  year={2023},\n}"
        tex = r"\cite{exists2023} \cite{ghost2023}"
        result = self.harness.execute_tool("verify_citations", {
            "bib_content": bib,
            "tex_content": tex,
        })
        assert "ghost2023" in result
        assert "❌" in result or "错误" in result

    def test_orphaned_entry_reported(self):
        """Should report orphaned entries."""
        bib = (
            "@article{used2023,\n  author={A},\n  title={Used Paper},\n  journal={J},\n  year={2023},\n}\n"
            "@article{unused2023,\n  author={B},\n  title={Unused Paper},\n  journal={J},\n  year={2023},\n}"
        )
        tex = r"\cite{used2023}"
        result = self.harness.execute_tool("verify_citations", {
            "bib_content": bib,
            "tex_content": tex,
        })
        assert "unused2023" in result

    def test_bib_only_completeness(self):
        """Should check field completeness even without tex content."""
        bib = "@article{incomplete2023,\n  author={A},\n  title={Incomplete},\n}"  # Missing journal, year
        result = self.harness.execute_tool("verify_citations", {
            "bib_content": bib,
        })
        assert "⚠️" in result or "警告" in result or "缺失" in result

    def test_check_orphaned_false(self):
        """Should suppress orphaned warnings when check_orphaned=False."""
        bib = (
            "@article{x,\n  author={A},\n  title={Paper X},\n  journal={J},\n  year={2023},\n}\n"
            "@article{y,\n  author={B},\n  title={Paper Y},\n  journal={J},\n  year={2023},\n}"
        )
        tex = r"\cite{x}"
        result = self.harness.execute_tool("verify_citations", {
            "bib_content": bib,
            "tex_content": tex,
            "check_orphaned": False,
        })
        # 'y' should not appear since orphaned checking is disabled
        assert "未被引用" not in result


# ============================================================
# No Regression on Existing Tools
# ============================================================

class TestNoRegression:
    """Ensure existing tools still work after Phase 22 additions."""

    def setup_method(self):
        self.harness = Harness()

    def test_unknown_tool(self):
        """Unknown tool should return error message."""
        result = self.harness.execute_tool("nonexistent_tool", {})
        assert "未知工具" in result

    def test_read_section_still_works(self):
        """read_section should still function (returns not-found for empty paper)."""
        result = self.harness.execute_tool("read_section", {"section": "introduction"})
        # Empty paper → fuzzy match or section not found
        assert isinstance(result, str)

    def test_reflect_and_plan_still_works(self):
        """reflect_and_plan should still function."""
        result = self.harness.execute_tool("reflect_and_plan", {
            "reflection": "Testing Phase 22 integration",
            "next_action": "Continue verification"
        })
        assert isinstance(result, str)
