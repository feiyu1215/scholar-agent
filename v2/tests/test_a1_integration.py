"""
tests/test_a1_integration.py — Integration & Degradation tests for A1 (Token Budget Manager)

Covers:
    1. Integration: assemble() includes Zone B full_load content from paper_sections
    2. Integration: assemble() includes Zone B digest content for 1-hop sections
    3. Degradation: kill switch SCHOLAR_GODEL_BUDGET=0 → no Zone B content in output
    4. Degradation: no TokenBudgetManager → fallback to basic assembly (no crash)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from unittest.mock import MagicMock, patch
from typing import Dict, List, Optional, Tuple

import pytest

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.token_budget import TokenBudgetManager, ZoneBAllocation
from core.paper_cognition_graph import PaperCognitionGraph, PCGNode, PCGEdge
from core.assembler import ContextAssembler
from core.state import WorkspaceState
import core.godel_config  # noqa: F401 — ensure attribute exists on `core` for patch()


# ==============================================================
# Helpers
# ==============================================================

def _make_pcg(
    sections: Optional[Dict[str, int]] = None,
    edges: Optional[List[Tuple[str, str]]] = None,
    hypotheses: Optional[Dict[str, List[str]]] = None,
) -> PaperCognitionGraph:
    """Build a minimal PCG for testing."""
    pcg = PaperCognitionGraph()
    if sections:
        for name, wc in sections.items():
            pcg.nodes[name] = PCGNode(section_name=name, word_count=wc)
    if edges:
        for src, tgt in edges:
            pcg.edges.append(PCGEdge(source=src, target=tgt, edge_type="REFERENCES", weight=0.5))
    if hypotheses:
        for section, hyp_ids in hypotheses.items():
            if section in pcg.nodes:
                pcg.nodes[section].hypotheses_linked = hyp_ids
    return pcg


def _make_mock_dependencies():
    """Create mock memory, cognitive_state, offload_store for ContextAssembler."""
    memory = MagicMock()
    memory.format_memory_context.return_value = ""

    cognitive_state = MagicMock()
    cognitive_state.format_for_context.return_value = ""

    offload_store = MagicMock()
    offload_store.format_refs_summary.return_value = ""

    return memory, cognitive_state, offload_store


def _make_state_with_paper(
    paper_sections: Dict[str, str],
    sections_read: List[str],
    section_digests: Optional[Dict[str, str]] = None,
    pcg: Optional[PaperCognitionGraph] = None,
) -> WorkspaceState:
    """Create a WorkspaceState with paper content for integration testing."""
    state = WorkspaceState()
    state.paper_sections = paper_sections
    state.sections_read = sections_read
    state.section_digests = section_digests or {}
    state.paper_cognition_graph = pcg
    return state


# ==============================================================
# Test: Integration — assemble() includes full_load content
# ==============================================================

class TestAssembleIncludesFullLoad:
    """Verify that assemble() output includes Zone B full_load section content."""

    def test_full_load_section_content_appears_in_output(self):
        """When current_task_section is set and PCG has it, full content is injected."""
        # Arrange: Paper with 3 sections, current section is "Methods"
        paper_sections = {
            "Introduction": "This paper introduces a novel approach to machine learning optimization.",
            "Methods": "We propose a gradient-free optimization method based on evolutionary strategies. " * 20,
            "Results": "Our method achieves state-of-the-art performance on three benchmarks.",
        }
        pcg = _make_pcg(
            sections={"Introduction": 50, "Methods": 400, "Results": 60},
            edges=[("Methods", "Results")],
        )
        state = _make_state_with_paper(
            paper_sections=paper_sections,
            sections_read=["Introduction", "Methods"],  # Methods is last read → current_task_section
            pcg=pcg,
        )

        memory, cognitive_state, offload_store = _make_mock_dependencies()
        budget_mgr = TokenBudgetManager(total_budget=128_000)

        assembler = ContextAssembler(
            memory=memory,
            cognitive_state=cognitive_state,
            offload_store=offload_store,
            token_budget_manager=budget_mgr,
        )

        # Act
        output = assembler.assemble(state=state, current_turn=1)

        # Assert: Zone B Full marker + actual content from Methods section
        assert "[Zone B Full]" in output
        assert "Methods" in output
        assert "gradient-free optimization" in output

    def test_full_load_includes_real_section_text(self):
        """Full load should contain the actual paper_sections content, not just a label."""
        paper_sections = {
            "Abstract": "Short abstract here.",
            "Methodology": "Detailed methodology content with specific technical details about "
                          "transformer architectures and attention mechanisms for sequence modeling.",
        }
        pcg = _make_pcg(sections={"Abstract": 10, "Methodology": 200})
        state = _make_state_with_paper(
            paper_sections=paper_sections,
            sections_read=["Methodology"],  # current task section
            pcg=pcg,
        )

        memory, cognitive_state, offload_store = _make_mock_dependencies()
        budget_mgr = TokenBudgetManager(total_budget=128_000)

        assembler = ContextAssembler(
            memory=memory,
            cognitive_state=cognitive_state,
            offload_store=offload_store,
            token_budget_manager=budget_mgr,
        )

        output = assembler.assemble(state=state, current_turn=2)

        # Assert: actual text from the section is present
        assert "transformer architectures" in output
        assert "attention mechanisms" in output


# ==============================================================
# Test: Integration — digest_load for 1-hop sections
# ==============================================================

class TestAssembleIncludesDigestLoad:
    """Verify that 1-hop neighbor sections appear as digest in output."""

    def test_one_hop_section_appears_as_digest(self):
        """Section connected by edge to current_task_section gets digest treatment."""
        paper_sections = {
            "Introduction": "Introduction content here.",
            "Methods": "Methods content here " * 50,
            "Discussion": "Discussion content here " * 30,
        }
        pcg = _make_pcg(
            sections={"Introduction": 30, "Methods": 500, "Discussion": 300},
            edges=[("Methods", "Discussion")],  # Discussion is 1-hop from Methods
        )
        state = _make_state_with_paper(
            paper_sections=paper_sections,
            sections_read=["Methods"],
            section_digests={"Discussion": "This section discusses implications of the findings."},
            pcg=pcg,
        )

        memory, cognitive_state, offload_store = _make_mock_dependencies()
        budget_mgr = TokenBudgetManager(total_budget=128_000)

        assembler = ContextAssembler(
            memory=memory,
            cognitive_state=cognitive_state,
            offload_store=offload_store,
            token_budget_manager=budget_mgr,
        )

        output = assembler.assemble(state=state, current_turn=3)

        # Assert: Zone B Digest section present with digest content
        assert "[Zone B Digest]" in output
        assert "Discussion" in output
        assert "implications of the findings" in output

    def test_no_digest_when_no_edges(self):
        """Sections without edges to current task section → name_only (not in Zone B Digest)."""
        paper_sections = {
            "Introduction": "Intro content.",
            "Methods": "Methods content " * 50,
            "Appendix": "Appendix supplementary material.",
        }
        pcg = _make_pcg(
            sections={"Introduction": 20, "Methods": 500, "Appendix": 200},
            # No edges → Appendix is isolated
        )
        state = _make_state_with_paper(
            paper_sections=paper_sections,
            sections_read=["Methods"],
            section_digests={"Appendix": "Supplementary data tables."},
            pcg=pcg,
        )

        memory, cognitive_state, offload_store = _make_mock_dependencies()
        budget_mgr = TokenBudgetManager(total_budget=128_000)

        assembler = ContextAssembler(
            memory=memory,
            cognitive_state=cognitive_state,
            offload_store=offload_store,
            token_budget_manager=budget_mgr,
        )

        output = assembler.assemble(state=state, current_turn=1)

        # Assert: Zone B Digest section does NOT mention Appendix
        # (Note: Appendix digest may appear in the regular section_digests block,
        #  but it should NOT be in Zone B Digest which only includes 1-hop neighbors)
        assert "[Zone B Digest]" not in output or "Appendix" not in output.split("[Zone B Digest]")[-1].split("\n📄")[0]


# ==============================================================
# Test: Degradation — kill switch disables Zone B
# ==============================================================

class TestKillSwitchDegradation:
    """Verify kill switch SCHOLAR_GODEL_BUDGET=0 disables Zone B entirely."""

    def test_kill_switch_no_zone_b_content(self):
        """With SCHOLAR_GODEL_BUDGET=0, assemble() produces no Zone B markers."""
        paper_sections = {
            "Introduction": "Intro text.",
            "Methods": "Methods detailed content about novel algorithms " * 30,
        }
        pcg = _make_pcg(
            sections={"Introduction": 20, "Methods": 400},
            edges=[("Introduction", "Methods")],
        )
        state = _make_state_with_paper(
            paper_sections=paper_sections,
            sections_read=["Methods"],
            pcg=pcg,
        )

        memory, cognitive_state, offload_store = _make_mock_dependencies()
        budget_mgr = TokenBudgetManager(total_budget=128_000)

        # Patch the godel_config flag to simulate kill switch=0
        with patch("core.godel_config.GODEL_BUDGET_MANAGER_ENABLED", False):
            assembler = ContextAssembler(
                memory=memory,
                cognitive_state=cognitive_state,
                offload_store=offload_store,
                token_budget_manager=budget_mgr,
            )

            output = assembler.assemble(state=state, current_turn=1)

        # Assert: No Zone B content at all
        assert "[Zone B Full]" not in output
        assert "[Zone B Digest]" not in output

    def test_kill_switch_does_not_crash(self):
        """Kill switch disabled path should still produce valid output."""
        paper_sections = {
            "Abstract": "This is the abstract of the paper.",
        }
        state = _make_state_with_paper(
            paper_sections=paper_sections,
            sections_read=["Abstract"],
        )

        memory, cognitive_state, offload_store = _make_mock_dependencies()

        with patch("core.godel_config.GODEL_BUDGET_MANAGER_ENABLED", False):
            assembler = ContextAssembler(
                memory=memory,
                cognitive_state=cognitive_state,
                offload_store=offload_store,
                token_budget_manager=None,  # No budget manager at all
            )

            output = assembler.assemble(state=state, current_turn=0)

        # Should still produce some output (at minimum resource_status)
        assert output
        assert len(output) > 10


# ==============================================================
# Test: Degradation — no TokenBudgetManager → graceful fallback
# ==============================================================

class TestNoBudgetManagerFallback:
    """Verify assembler works without TokenBudgetManager (V2 compat mode)."""

    def test_no_budget_manager_no_zone_b(self):
        """Without TokenBudgetManager, no Zone B content is generated."""
        paper_sections = {
            "Introduction": "Intro text about the research.",
            "Methods": "Detailed methods description " * 20,
        }
        state = _make_state_with_paper(
            paper_sections=paper_sections,
            sections_read=["Methods"],
        )

        memory, cognitive_state, offload_store = _make_mock_dependencies()

        assembler = ContextAssembler(
            memory=memory,
            cognitive_state=cognitive_state,
            offload_store=offload_store,
            token_budget_manager=None,  # V2 mode: no budget manager
        )

        output = assembler.assemble(state=state, current_turn=1)

        # Assert: No Zone B markers
        assert "[Zone B Full]" not in output
        assert "[Zone B Digest]" not in output
        # But assembler still works
        assert output
        assert "轮次" in output  # resource_status section always present

    def test_assembler_still_has_paper_overview(self):
        """Without budget manager, paper_overview section still works."""
        paper_sections = {
            "Introduction": "Introduction to quantum computing applications.",
            "Background": "Background on qubit implementations.",
        }
        state = _make_state_with_paper(
            paper_sections=paper_sections,
            sections_read=["Introduction"],
        )

        memory, cognitive_state, offload_store = _make_mock_dependencies()

        assembler = ContextAssembler(
            memory=memory,
            cognitive_state=cognitive_state,
            offload_store=offload_store,
            token_budget_manager=None,
        )

        output = assembler.assemble(state=state, current_turn=0)

        # Paper overview should list section names
        assert "Introduction" in output
