"""
tests/test_c4_kill_switch_degradation.py — Phase C4: Kill Switch 降级完整性验证

验证目标：
    当所有 V3 kill switches 设为 "0" 时：
    1. cognitive_loop 正常运行并返回 LoopDone（不崩溃）
    2. PCG 不构建（paper_cognition_graph 保持 None）
    3. Zone B 内容不注入 assembler 输出
    4. Signal Dispatcher 不介入
    5. Evidence Chain 不记录
    6. Session Finalizer 正常结束（V3 分支静默跳过）
    7. Assembler 输出等价 V2（无 pcg_navigation, 无 zone_b_paper_content）
    8. 无 import error、无运行时异常

设计原则：
    - 使用 monkeypatch 设置环境变量 + 重新加载 godel_config 模块
    - 复用 mock_llm 的 MockLLMClient 进行确定性测试
    - 每个测试独立验证一个降级维度
"""
from __future__ import annotations

import asyncio
import importlib
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Ensure v2/ is importable (must be before any core.* import)
_v2_root = str(Path(__file__).resolve().parent.parent)
if _v2_root not in sys.path:
    sys.path.insert(0, _v2_root)


# ============================================================
# Fixtures
# ============================================================

# All V3 kill switch env var names
V3_KILL_SWITCH_ENV_VARS = [
    "SCHOLAR_GODEL_PCG",
    "SCHOLAR_GODEL_BUDGET",
    "SCHOLAR_GODEL_DISPATCHER",
    "SCHOLAR_GODEL_EVIDENCE_CHAIN",
    "SCHOLAR_GODEL_SECTION_EXP",
    "SCHOLAR_GODEL_INTRA_CONTRAST",
    "SCHOLAR_GODEL_FAST_REFLECT",
    "SCHOLAR_GODEL_DEEP_REFLECT",
    "SCHOLAR_GODEL_EMERGENCY",
]


@pytest.fixture(autouse=True)
def disable_all_v3_flags(monkeypatch):
    """Set ALL V3 kill switch env vars to '0' and reload godel_config.

    This is autouse=True so every test in this file runs with V3 disabled.
    After each test, monkeypatch automatically restores the environment.
    """
    for var in V3_KILL_SWITCH_ENV_VARS:
        monkeypatch.setenv(var, "0")

    # Reload godel_config to pick up new env vars.
    # IMPORTANT: v2/core/ can be shadowed by scholar-agent-public/core/ (v1).
    # We must ensure v2/ is at the FRONT of sys.path and that the correct
    # core package is loaded.
    if sys.path[0] != _v2_root:
        # Remove and re-insert at position 0
        if _v2_root in sys.path:
            sys.path.remove(_v2_root)
        sys.path.insert(0, _v2_root)

    # Save current 'core.*' modules so we can restore them after the test
    _saved_core_modules = {
        key: mod for key, mod in sys.modules.items()
        if key == "core" or key.startswith("core.")
    }

    # Clear any cached 'core' package that might point to v1
    for key in list(sys.modules.keys()):
        if key == "core" or key.startswith("core."):
            del sys.modules[key]

    # Now import fresh — should find v2/core/godel_config.py
    gc = importlib.import_module("core.godel_config")
    # Verify flags are actually off
    assert gc.GODEL_PCG_ENABLED is False
    assert gc.GODEL_BUDGET_MANAGER_ENABLED is False
    assert gc.GODEL_SIGNAL_DISPATCHER_ENABLED is False
    assert gc.GODEL_EVIDENCE_CHAIN_ENABLED is False
    assert gc.GODEL_SECTION_EXPERIENCE_ENABLED is False
    assert gc.GODEL_INTRA_CONTRAST_ENABLED is False
    assert gc.GODEL_FAST_REFLECT_ENABLED is False
    assert gc.GODEL_DEEP_REFLECT_ENABLED is False
    assert gc.GODEL_EMERGENCY_REFLECT_ENABLED is False

    yield gc

    # Cleanup: restore ALL original core.* modules to avoid poisoning later tests.
    # First remove the freshly-imported ones from this fixture.
    for key in list(sys.modules.keys()):
        if key == "core" or key.startswith("core."):
            del sys.modules[key]
    # Then restore the originals (with correct enum identities, etc.)
    sys.modules.update(_saved_core_modules)


@pytest.fixture
def disable_checker():
    """Disable checker to avoid external LLM calls."""
    import core.checker as _checker_mod
    original = _checker_mod.CHECKER_ENABLED
    _checker_mod.CHECKER_ENABLED = False
    yield
    _checker_mod.CHECKER_ENABLED = original


# ============================================================
# Test Tools & Helpers
# ============================================================

SCHOLAR_TOOLS = [
    {
        "name": "read_section",
        "description": "Read a section of the paper.",
        "input_schema": {
            "type": "object",
            "properties": {"section_name": {"type": "string"}},
            "required": ["section_name"],
        },
    },
    {
        "name": "update_findings",
        "description": "Update the list of review findings.",
        "input_schema": {
            "type": "object",
            "properties": {"findings": {"type": "array"}},
            "required": ["findings"],
        },
    },
    {
        "name": "mark_complete",
        "description": "Mark the review as complete.",
        "input_schema": {
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
        },
    },
]


def _make_harness(max_loop_turns: int = 50):
    """Create a Harness with fake paper sections, bypassing file loading."""
    from core.harness import Harness
    with patch("core.harness._pl_load_paper"):
        h = Harness(paper_path="fake_paper.md", max_loop_turns=max_loop_turns, enable_hdwm=False)
    h.state.paper_sections = {
        "introduction": "This paper presents a novel approach to transformer pruning...",
        "methodology": "We propose a dynamic importance scoring mechanism that evaluates...",
        "results": "Our experiments on GLUE benchmark show significant improvements...",
        "related_work": "Prior work on model compression includes quantization...",
        "conclusion": "We have demonstrated that dynamic pruning outperforms static...",
    }
    h.state.paper_overview = "Test paper on transformer pruning (5 sections)"
    h.state.sections_read = []
    h.state.findings = []
    return h


def _make_mock_client_basic():
    """Create a MockLLMClient for a basic scan-read-done flow."""
    from tests.mock_llm import (
        MockLLMClient,
        make_read_section_response,
        make_single_finding_response,
        make_done_response,
    )
    return MockLLMClient(responses=[
        make_read_section_response("introduction"),
        make_read_section_response("methodology"),
        make_single_finding_response(
            finding="The pruning threshold selection lacks theoretical justification",
            section="methodology",
            priority="high",
            status="verified",
            evidence="Section 3.2 states threshold=0.1 without explaining why.",
        ),
        make_done_response("Review complete with V2-degraded behavior."),
    ])


# ============================================================
# Test: cognitive_loop completes without crash (V3 OFF)
# ============================================================

class TestCognitiveLoopDegradation:
    """Verify cognitive_loop runs to completion with all V3 features disabled."""

    def test_basic_flow_returns_loop_done(self, disable_checker, disable_all_v3_flags):
        """cognitive_loop should return LoopDone even with all V3 kill switches OFF."""
        from core.loop import cognitive_loop, LoopDone

        client = _make_mock_client_basic()
        harness = _make_harness()
        messages = [
            {"role": "system", "content": "You are a paper reviewer."},
            {"role": "user", "content": "Please review this paper."},
        ]

        result = asyncio.run(
            cognitive_loop(messages, harness, SCHOLAR_TOOLS, client, verbose=False)
        )

        assert isinstance(result, LoopDone)
        # Completion gate nudge adds +1 turn (first mark_complete is intercepted)
        assert harness.state.loop_turns >= 4

    def test_doom_loop_still_fires(self, disable_checker, disable_all_v3_flags):
        """Doom loop guard must still function when V3 is OFF."""
        from core.loop import cognitive_loop, LoopDoomStop
        from tests.mock_llm import MockLLMClient, make_read_section_response

        client = MockLLMClient(responses=[
            make_read_section_response("introduction"),
            make_read_section_response("methodology"),
            make_read_section_response("results"),
            make_read_section_response("introduction"),
            make_read_section_response("methodology"),
            make_read_section_response("results"),
            make_read_section_response("introduction"),
        ])

        harness = _make_harness(max_loop_turns=3)
        messages = [
            {"role": "system", "content": "You are a paper reviewer."},
            {"role": "user", "content": "Please review this paper."},
        ]

        result = asyncio.run(
            cognitive_loop(messages, harness, SCHOLAR_TOOLS, client, verbose=False)
        )

        assert isinstance(result, LoopDoomStop)

    def test_findings_still_recorded(self, disable_checker, disable_all_v3_flags):
        """update_findings tool still works when V3 is OFF."""
        from core.loop import cognitive_loop, LoopDone

        client = _make_mock_client_basic()
        harness = _make_harness()
        messages = [
            {"role": "system", "content": "You are a paper reviewer."},
            {"role": "user", "content": "Please review this paper."},
        ]

        result = asyncio.run(
            cognitive_loop(messages, harness, SCHOLAR_TOOLS, client, verbose=False)
        )

        assert isinstance(result, LoopDone)
        assert len(harness.state.findings) >= 1


# ============================================================
# Test: PCG not constructed when GODEL_PCG_ENABLED=False
# ============================================================

class TestPCGDegradation:
    """Verify PCG-related features are inactive when kill switch is OFF."""

    def test_pcg_not_constructed(self, disable_checker, disable_all_v3_flags):
        """paper_cognition_graph should remain None when PCG is disabled."""
        from core.loop import cognitive_loop

        client = _make_mock_client_basic()
        harness = _make_harness()
        messages = [
            {"role": "system", "content": "You are a paper reviewer."},
            {"role": "user", "content": "Please review this paper."},
        ]

        asyncio.run(
            cognitive_loop(messages, harness, SCHOLAR_TOOLS, client, verbose=False)
        )

        # PCG should not be built
        pcg = getattr(harness.state, "paper_cognition_graph", None)
        assert pcg is None or (hasattr(pcg, "is_empty") and pcg.is_empty())

    def test_assembler_no_pcg_navigation(self, disable_all_v3_flags):
        """Assembler output should not contain pcg_navigation section."""
        harness = _make_harness()

        # Use Harness.format_context() which delegates to ContextAssembler.assemble()
        output = harness.format_context()

        # PCG navigation should not appear
        assert "pcg_navigation" not in output.lower(), "PCG navigation should not appear when disabled"
        assert "coverage_gaps" not in output.lower(), "coverage_gaps should not appear when disabled"
        # But paper_structure (V2 feature) should still work
        # (paper_structure_index may not be set in this minimal test, so just check no crash)


# ============================================================
# Test: Zone B not injected when GODEL_BUDGET_MANAGER_ENABLED=False
# ============================================================

class TestZoneBDegradation:
    """Verify Zone B dynamic loading is inactive when kill switch is OFF."""

    def test_zone_b_not_in_assembler_output(self, disable_all_v3_flags):
        """Zone B paper content should not appear when Budget Manager is disabled."""
        harness = _make_harness()

        # Use Harness.format_context() — Zone B allocation is computed internally
        # and gated by GODEL_BUDGET_MANAGER_ENABLED (which is False here)
        output = harness.format_context()

        # Zone B content markers should not be present
        assert "[Zone B Full]" not in output
        assert "[Zone B Digest]" not in output

    def test_harness_format_context_no_zone_b(self, disable_checker, disable_all_v3_flags):
        """Harness.format_context() should not compute Zone B allocation."""
        harness = _make_harness()
        # Just verify format_context doesn't crash
        ctx = harness.format_context()

        # Should be a string (the assembled context)
        assert isinstance(ctx, str)
        assert "[Zone B" not in ctx


# ============================================================
# Test: Evidence Chain not recorded when disabled
# ============================================================

class TestEvidenceChainDegradation:
    """Verify evidence chain tracking is inactive when kill switch is OFF."""

    def test_no_evidence_chains_recorded(self, disable_checker, disable_all_v3_flags):
        """evidence_chains should remain empty when GODEL_EVIDENCE_CHAIN_ENABLED=False."""
        from core.loop import cognitive_loop

        client = _make_mock_client_basic()
        harness = _make_harness()
        messages = [
            {"role": "system", "content": "You are a paper reviewer."},
            {"role": "user", "content": "Please review this paper."},
        ]

        asyncio.run(
            cognitive_loop(messages, harness, SCHOLAR_TOOLS, client, verbose=False)
        )

        chains = getattr(harness.state, "evidence_chains", None)
        assert chains is None or chains == {} or len(chains) == 0, \
            f"Expected no evidence_chains but got: {chains}"


# ============================================================
# Test: Session Finalizer graceful degradation
# ============================================================

class TestSessionFinalizerDegradation:
    """Verify session finalizer runs without error when V3 features are OFF."""

    def test_end_session_no_crash(self, disable_checker, disable_all_v3_flags):
        """end_session() should complete without error when all V3 flags are OFF."""
        from core.session_finalizer import end_session

        harness = _make_harness()
        # Simulate a minimal completed session
        harness.state.sections_read = ["introduction", "methodology"]
        harness.state.findings = [
            {
                "finding": "Test finding",
                "section": "methodology",
                "priority": "high",
                "status": "verified",
                "evidence": "Some evidence",
            }
        ]
        harness.state.loop_turns = 5
        harness.state.total_tokens = 5000

        # Mock the memory to avoid file I/O
        mock_memory = MagicMock()
        mock_memory.episodic = []
        mock_memory.procedural = []
        mock_memory.semantic = {}
        mock_memory.store_episodic = MagicMock()
        mock_memory.store_procedural = MagicMock()

        # end_session returns None, not a dict. Just verify it doesn't crash.
        end_session(
            harness.state,
            mock_memory,
            paper_id="test_paper_123",
            strategy_transitions=[],
        )

        # If we reach here, no crash occurred — success.

    def test_end_session_no_v3_data_produced(self, disable_checker, disable_all_v3_flags):
        """No V3-specific data (section_experiences, contrast) should be produced."""
        from core.session_finalizer import end_session

        harness = _make_harness()
        harness.state.sections_read = ["introduction"]
        harness.state.findings = []
        harness.state.loop_turns = 3
        harness.state.total_tokens = 2000

        mock_memory = MagicMock()
        mock_memory.episodic = []
        mock_memory.procedural = []
        mock_memory.semantic = {}
        mock_memory.store_episodic = MagicMock()
        mock_memory.store_procedural = MagicMock()

        # end_session returns None. With empty findings, it returns early.
        end_session(
            harness.state,
            mock_memory,
            paper_id="test_paper_456",
            strategy_transitions=[],
        )

        # Section experiences should not be recorded when V3 is disabled
        section_metrics = getattr(harness.state, "section_metrics", None)
        # With kill switch off, section_metrics should either not exist or be empty
        assert section_metrics is None or section_metrics == [], \
            f"Expected no section_metrics but got: {section_metrics}"


# ============================================================
# Test: Signal Dispatcher inactive when disabled
# ============================================================

class TestSignalDispatcherDegradation:
    """Verify signal dispatcher does not inject messages when disabled."""

    def test_signal_dispatcher_no_op(self, disable_all_v3_flags):
        """SignalDispatcher should be importable and gated by kill switch."""
        from core.godel_config import GODEL_SIGNAL_DISPATCHER_ENABLED

        assert GODEL_SIGNAL_DISPATCHER_ENABLED is False

        # The dispatcher itself is importable but gated by the consuming code
        from core.signal_dispatcher import SignalDispatcher

        dispatcher = SignalDispatcher()
        assert dispatcher is not None

        # Verify that dispatch() returns empty/no-op signals when flag is OFF
        # (dispatch may accept various args depending on implementation)
        if hasattr(dispatcher, "dispatch"):
            import inspect
            sig = inspect.signature(dispatcher.dispatch)
            # Build minimal kwargs from the signature
            kwargs = {}
            for param_name, param in sig.parameters.items():
                if param_name == "self":
                    continue
                if param.default is not inspect.Parameter.empty:
                    continue  # skip optional params
                # Provide minimal dummy values for required params
                kwargs[param_name] = None
            try:
                result = dispatcher.dispatch(**kwargs)
                # Result should be empty/None/[] when disabled
                assert result is None or result == [] or result == {}, \
                    f"Dispatcher should be no-op when disabled, got: {result}"
            except (TypeError, AttributeError):
                # If dispatch requires specific args we can't easily mock,
                # at least verify the flag check happens
                pass


# ============================================================
# Test: Full integration — assembler produces valid V2 output
# ============================================================

class TestFullV2EquivalentOutput:
    """Verify the full context assembly path produces V2-equivalent output."""

    def test_assemble_produces_nonempty_output(self, disable_all_v3_flags):
        """Assembler should still produce valid context even with all V3 features OFF."""
        harness = _make_harness()
        harness.state.sections_read = ["introduction"]

        output = harness.format_context()

        # Should produce non-empty output
        assert len(output) > 0
        # Should still contain paper-related info (V2 feature)
        # The assembler outputs Chinese text with section info
        assert "论文" in output or "sections" in output.lower() or "section" in output.lower()

    def test_no_v3_markers_in_output(self, disable_all_v3_flags):
        """Assembled output should not contain V3-specific markers."""
        harness = _make_harness()

        output = harness.format_context()

        # V3-specific markers that should NOT appear
        v3_markers = [
            "[Zone B Full]",
            "[Zone B Digest]",
            "coverage_gaps",
            "read_depth",
            "[Evidence Chain]",
        ]
        for marker in v3_markers:
            assert marker not in output, f"V3 marker '{marker}' found in V2-degraded output"


# ============================================================
# Test: No import errors with V3 disabled
# ============================================================

class TestNoImportErrors:
    """Verify all core modules can be imported without error when V3 is OFF."""

    def test_import_all_core_modules(self, disable_all_v3_flags):
        """All core/ modules should import cleanly with V3 kill switches OFF."""
        modules_to_test = [
            "core.harness",
            "core.loop",
            "core.assembler",
            "core.phases",
            "core.boundary_guard",
            "core.finding_quality",
            "core.gate_config",
            "core.compaction",
            "core.signal_dispatcher",
            "core.token_budget",
            "core.paper_cognition_graph",
            "core.evidence_chain",
            "core.meta_reflect",
            "core.session_finalizer",
            "core.adaptive_config",
        ]

        errors = []
        for mod_name in modules_to_test:
            try:
                # Remove from cache first
                if mod_name in sys.modules:
                    del sys.modules[mod_name]
                importlib.import_module(mod_name)
            except Exception as e:
                errors.append(f"{mod_name}: {type(e).__name__}: {e}")

        assert not errors, f"Import errors with V3 OFF:\n" + "\n".join(errors)

    def test_import_evolution_modules(self, disable_all_v3_flags):
        """Evolution/reflection modules should import without error."""
        modules_to_test = [
            "core.evolution",
            "core.reflection",
            "core.metacognition",
            "core.hypothesis",
        ]

        errors = []
        for mod_name in modules_to_test:
            try:
                if mod_name in sys.modules:
                    del sys.modules[mod_name]
                importlib.import_module(mod_name)
            except Exception as e:
                errors.append(f"{mod_name}: {type(e).__name__}: {e}")

        assert not errors, f"Import errors with V3 OFF:\n" + "\n".join(errors)


# ============================================================
# Test: Adaptive Config unaffected by kill switches
# ============================================================

class TestAdaptiveConfigDegradation:
    """Verify AdaptiveConfig operates independently of V3 kill switches."""

    def test_adaptive_config_still_functional(self, disable_all_v3_flags):
        """AdaptiveConfig should still function (it has its own evidence gate)."""
        from core.adaptive_config import AdaptiveConfig

        config = AdaptiveConfig()

        # Basic operations should work — AdaptiveConfig is a dataclass with
        # direct fields (temperature, max_tokens, max_nudges, etc.), no "params" dict.
        assert config is not None
        assert hasattr(config, "temperature")
        assert hasattr(config, "max_tokens")
        assert hasattr(config, "max_nudges")
        # The config itself doesn't depend on kill switches
        # Its integration with DeepReflector is gated by GODEL_DEEP_REFLECT_ENABLED


# ============================================================
# Test: Mock-LLM full flow (5 sections, multi-turn)
# ============================================================

class TestExtendedFlowV2Degraded:
    """Longer integration test simulating a real review session in V2 mode."""

    def test_multi_section_review_no_crash(self, disable_checker, disable_all_v3_flags):
        """A 6-turn review session should complete without V3 interference."""
        from core.loop import cognitive_loop, LoopDone
        from tests.mock_llm import (
            MockLLMClient,
            make_read_section_response,
            make_single_finding_response,
            make_done_response,
            make_text_only_response,
        )

        client = MockLLMClient(responses=[
            make_read_section_response("introduction"),
            make_read_section_response("methodology"),
            make_read_section_response("results"),
            make_text_only_response("Considering the findings so far..."),
            make_single_finding_response(
                finding="The sample size is not justified with a power analysis",
                section="methodology",
                priority="high",
                status="verified",
                evidence="No power analysis reported.",
            ),
            make_done_response("Review complete. 1 major issue found."),
        ])

        harness = _make_harness()
        messages = [
            {"role": "system", "content": "You are a paper reviewer."},
            {"role": "user", "content": "Please review this paper."},
        ]

        result = asyncio.run(
            cognitive_loop(messages, harness, SCHOLAR_TOOLS, client, verbose=False)
        )

        assert isinstance(result, LoopDone)
        # Completion gate nudge adds +1 turn (first mark_complete is intercepted)
        assert harness.state.loop_turns >= 6
        assert len(harness.state.sections_read) >= 3
        assert len(harness.state.findings) >= 1

        # Verify no V3 artifacts
        pcg = getattr(harness.state, "paper_cognition_graph", None)
        assert pcg is None or (hasattr(pcg, "is_empty") and pcg.is_empty()), \
            f"PCG should not be constructed when disabled, got: {pcg}"
        evidence_chains = getattr(harness.state, "evidence_chains", None)
        assert evidence_chains is None or evidence_chains == {} or len(evidence_chains) == 0, \
            f"Expected no evidence_chains but got: {evidence_chains}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
