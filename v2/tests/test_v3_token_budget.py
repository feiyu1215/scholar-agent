"""
tests/test_v3_token_budget.py — Tests for core/token_budget.py

Covers:
    - ZoneBAllocation dataclass field access
    - TokenBudgetManager defaults and budget computation
    - Empty PCG returns empty allocation
    - Single section within budget → full_load
    - Large section exceeding budget → digest_load degradation
    - Multiple sections prioritized by current_task_section and edges
    - Budget overflow pushes digest_load → name_only
    - Dependency boosting via PCG edges
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import pytest

from core.token_budget import TokenBudgetManager, ZoneBAllocation
from core.paper_cognition_graph import PaperCognitionGraph, PCGNode, PCGEdge
from core.godel_config import ZONE_A_DEFAULT_TOKENS, ZONE_B_MAX_TOKENS


# ==============================================================
# Helpers
# ==============================================================

def _make_pcg(
    sections: Optional[Dict[str, int]] = None,
    edges: Optional[List[Tuple[str, str]]] = None,
    hypotheses: Optional[Dict[str, List[str]]] = None,
) -> PaperCognitionGraph:
    """Build a minimal PCG for testing.

    Args:
        sections: {section_name: word_count}
        edges: [(source, target)] — creates REFERENCES edges with weight=0.5
        hypotheses: {section_name: [hypothesis_ids]} — links hypotheses to nodes
    """
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


# ==============================================================
# Test: ZoneBAllocation field access
# ==============================================================

class TestZoneBAllocation:
    """Test ZoneBAllocation dataclass."""

    def test_default_fields(self):
        """ZoneBAllocation defaults to empty lists and zero tokens."""
        alloc = ZoneBAllocation()
        assert alloc.full_load == []
        assert alloc.digest_load == []
        assert alloc.name_only == []
        assert alloc.estimated_tokens == 0

    def test_field_assignment(self):
        """ZoneBAllocation fields can be set at construction."""
        alloc = ZoneBAllocation(
            full_load=["intro"],
            digest_load=["methods"],
            name_only=["appendix"],
            estimated_tokens=500,
        )
        assert alloc.full_load == ["intro"]
        assert alloc.digest_load == ["methods"]
        assert alloc.name_only == ["appendix"]
        assert alloc.estimated_tokens == 500


# ==============================================================
# Test: TokenBudgetManager defaults
# ==============================================================

class TestTokenBudgetManagerDefaults:
    """Test TokenBudgetManager initialization and defaults."""

    def test_zone_a_default(self):
        """zone_a_budget defaults to ZONE_A_DEFAULT_TOKENS (8000)."""
        mgr = TokenBudgetManager()
        assert mgr.zone_a_budget == ZONE_A_DEFAULT_TOKENS
        assert mgr.zone_a_budget == 8000

    def test_total_budget_default(self):
        """total_budget defaults to 128000."""
        mgr = TokenBudgetManager()
        assert mgr.total_budget == 128_000

    def test_zone_b_max_default(self):
        """zone_b_max defaults to ZONE_B_MAX_TOKENS (40000)."""
        mgr = TokenBudgetManager()
        assert mgr.zone_b_max == ZONE_B_MAX_TOKENS
        assert mgr.zone_b_max == 40_000

    def test_custom_budget(self):
        """TokenBudgetManager accepts custom budget values."""
        mgr = TokenBudgetManager(total_budget=64_000, zone_a_budget=4000, zone_b_max=20_000)
        assert mgr.total_budget == 64_000
        assert mgr.zone_a_budget == 4000
        assert mgr.zone_b_max == 20_000


# ==============================================================
# Test: Empty PCG returns empty allocation
# ==============================================================

class TestEmptyPCG:
    """Test behavior when PCG is None or empty."""

    def test_none_pcg_returns_empty(self):
        """Passing None as PCG returns empty ZoneBAllocation."""
        mgr = TokenBudgetManager()
        alloc = mgr.compute_zone_b_allocation(pcg=None)
        assert alloc.full_load == []
        assert alloc.digest_load == []
        assert alloc.name_only == []
        assert alloc.estimated_tokens == 0

    def test_empty_pcg_returns_empty(self):
        """PCG with no nodes returns empty ZoneBAllocation."""
        mgr = TokenBudgetManager()
        pcg = PaperCognitionGraph()  # no nodes
        alloc = mgr.compute_zone_b_allocation(pcg=pcg)
        assert alloc.full_load == []
        assert alloc.digest_load == []
        assert alloc.name_only == []
        assert alloc.estimated_tokens == 0


# ==============================================================
# Test: Single section within budget → full_load
# ==============================================================

class TestSingleSectionWithinBudget:
    """Test that a single section fitting in budget goes to full_load."""

    def test_single_section_full_load(self):
        """A section specified as current_task_section goes to full_load when within budget."""
        pcg = _make_pcg(sections={"introduction": 500})
        mgr = TokenBudgetManager()
        alloc = mgr.compute_zone_b_allocation(pcg=pcg, current_task_section="introduction")

        assert "introduction" in alloc.full_load
        assert alloc.digest_load == []
        assert alloc.name_only == []
        # estimated: 500 * 1.3 = 650 tokens
        assert alloc.estimated_tokens == int(500 * 1.3)

    def test_section_not_current_goes_to_name_only(self):
        """A section not specified as current_task and not related goes to name_only."""
        pcg = _make_pcg(sections={"introduction": 500, "conclusion": 200})
        mgr = TokenBudgetManager()
        alloc = mgr.compute_zone_b_allocation(pcg=pcg, current_task_section="introduction")

        assert "introduction" in alloc.full_load
        assert "conclusion" in alloc.name_only


# ==============================================================
# Test: Large section exceeding budget → digest degradation
# ==============================================================

class TestLargeSectionExceedsBudget:
    """Test that digest_load sections get degraded to name_only when budget overflows."""

    def test_digest_degraded_when_over_budget(self):
        """When total estimated tokens exceed zone_b_max, digest sections degrade to name_only."""
        # Create a PCG where current section is huge and related sections push over budget
        # zone_b_max = 100 tokens (very small for testing)
        # current section: 80 words → 80*1.3=104 tokens (full_load)
        # related section via edge: would add 80 tokens as digest
        # Total would be 104 + 80 = 184 > 100, so digest should degrade
        pcg = _make_pcg(
            sections={"methods": 80, "results": 200},
            edges=[("methods", "results")],
        )
        mgr = TokenBudgetManager(zone_b_max=100)
        alloc = mgr.compute_zone_b_allocation(pcg=pcg, current_task_section="methods")

        # methods is current → full_load (104 tokens)
        assert "methods" in alloc.full_load
        # results is related via edge → initially digest, but 104+80=184 > 100
        # so it should be degraded to name_only
        assert "results" in alloc.name_only
        assert "results" not in alloc.digest_load


# ==============================================================
# Test: Multiple sections prioritized by current_task_section
# ==============================================================

class TestMultipleSectionsPrioritization:
    """Test that sections are correctly categorized based on relationships."""

    def test_current_section_full_related_digest_rest_name(self):
        """Current section → full, edge-related → digest, rest → name_only."""
        pcg = _make_pcg(
            sections={
                "introduction": 100,
                "methods": 200,
                "results": 300,
                "discussion": 150,
            },
            edges=[("introduction", "methods")],
        )
        mgr = TokenBudgetManager()  # default zone_b_max=40000, plenty of room
        alloc = mgr.compute_zone_b_allocation(pcg=pcg, current_task_section="introduction")

        assert "introduction" in alloc.full_load
        assert "methods" in alloc.digest_load
        assert "results" in alloc.name_only
        assert "discussion" in alloc.name_only

    def test_bidirectional_edge_both_related(self):
        """Edges in either direction make sections related (digest_load)."""
        pcg = _make_pcg(
            sections={"A": 100, "B": 100, "C": 100},
            edges=[("B", "A")],  # B → A edge, so B is related to A
        )
        mgr = TokenBudgetManager()
        alloc = mgr.compute_zone_b_allocation(pcg=pcg, current_task_section="A")

        assert "A" in alloc.full_load
        assert "B" in alloc.digest_load  # related via edge target=A
        assert "C" in alloc.name_only


# ==============================================================
# Test: Budget overflow pushes to name_only
# ==============================================================

class TestBudgetOverflow:
    """Test that budget constraints push digest sections to name_only."""

    def test_multiple_digest_degraded_by_lru(self):
        """When multiple digest sections exceed budget, least active ones degrade first."""
        # Create PCG with current section + multiple related sections
        pcg = _make_pcg(
            sections={"main": 50, "dep1": 100, "dep2": 100, "dep3": 100},
            edges=[("main", "dep1"), ("main", "dep2"), ("main", "dep3")],
        )
        # dep1 has findings (more active), dep2/dep3 have none
        pcg.nodes["dep1"].findings_linked = ["f1", "f2"]

        # Budget: main full = 50*1.3=65, each digest=80, total with 3 digests = 65+240=305
        # Set zone_b_max to allow only 1 digest: 65 + 80 = 145 + some margin
        mgr = TokenBudgetManager(zone_b_max=160)
        alloc = mgr.compute_zone_b_allocation(pcg=pcg, current_task_section="main")

        assert "main" in alloc.full_load
        # dep1 should survive (more active due to findings_linked)
        assert "dep1" in alloc.digest_load
        # dep2 and dep3 should be degraded (less active)
        assert "dep2" in alloc.name_only or "dep3" in alloc.name_only

    def test_all_digest_degraded_when_full_load_exceeds(self):
        """If full_load alone exceeds budget, all digest sections go to name_only."""
        # current section is very large
        pcg = _make_pcg(
            sections={"huge_section": 35000, "related": 100},
            edges=[("huge_section", "related")],
        )
        # full_load: 35000*1.3=45500 > zone_b_max=40000
        # But the while loop only degrades digest, not full_load
        mgr = TokenBudgetManager(zone_b_max=40000)
        alloc = mgr.compute_zone_b_allocation(pcg=pcg, current_task_section="huge_section")

        assert "huge_section" in alloc.full_load
        # related was initially digest but gets degraded since budget is exceeded
        assert "related" in alloc.name_only


# ==============================================================
# Test: Dependency boosting via PCG edges
# ==============================================================

class TestDependencyBoosting:
    """Test that edge relationships boost sections into digest_load."""

    def test_edge_from_current_boosts_target_to_digest(self):
        """If current_task_section has an outgoing edge to X, X goes to digest_load."""
        pcg = _make_pcg(
            sections={"A": 100, "B": 200, "C": 300},
            edges=[("A", "B")],
        )
        mgr = TokenBudgetManager()
        alloc = mgr.compute_zone_b_allocation(pcg=pcg, current_task_section="A")

        assert "A" in alloc.full_load
        assert "B" in alloc.digest_load  # boosted by edge from A
        assert "C" in alloc.name_only    # no edge connection

    def test_edge_to_current_boosts_source_to_digest(self):
        """If X has an edge pointing to current_task_section, X goes to digest_load."""
        pcg = _make_pcg(
            sections={"A": 100, "B": 200, "C": 300},
            edges=[("B", "A")],  # B points to A
        )
        mgr = TokenBudgetManager()
        alloc = mgr.compute_zone_b_allocation(pcg=pcg, current_task_section="A")

        assert "A" in alloc.full_load
        assert "B" in alloc.digest_load  # boosted by edge targeting A
        assert "C" in alloc.name_only

    def test_multi_hop_not_boosted(self):
        """Only 1-hop edges boost sections; 2-hop connections stay in name_only."""
        pcg = _make_pcg(
            sections={"A": 100, "B": 100, "C": 100},
            edges=[("A", "B"), ("B", "C")],  # A→B→C
        )
        mgr = TokenBudgetManager()
        alloc = mgr.compute_zone_b_allocation(pcg=pcg, current_task_section="A")

        assert "A" in alloc.full_load
        assert "B" in alloc.digest_load  # 1-hop from A
        assert "C" in alloc.name_only    # 2-hop, not directly connected to A


# ==============================================================
# Test: Hypothesis-related sections go to digest_load
# ==============================================================

class TestHypothesisRelated:
    """Test that sections with linked hypotheses get boosted to digest_load."""

    def test_hypothesis_linked_section_in_digest(self):
        """Sections with hypotheses_linked go to digest_load even without direct edge."""
        pcg = _make_pcg(
            sections={"A": 100, "B": 200, "C": 300},
            hypotheses={"C": ["hyp_1"]},
        )
        mgr = TokenBudgetManager()
        alloc = mgr.compute_zone_b_allocation(pcg=pcg, current_task_section="A")

        assert "A" in alloc.full_load
        assert "C" in alloc.digest_load  # boosted by hypothesis link
        assert "B" in alloc.name_only    # no edge, no hypothesis

    def test_hypothesis_section_not_duplicated_if_already_full(self):
        """If a hypothesis-linked section is already in full_load, it's not added to digest."""
        pcg = _make_pcg(
            sections={"A": 100},
            hypotheses={"A": ["hyp_1"]},
        )
        mgr = TokenBudgetManager()
        alloc = mgr.compute_zone_b_allocation(pcg=pcg, current_task_section="A")

        assert "A" in alloc.full_load
        assert "A" not in alloc.digest_load
        assert "A" not in alloc.name_only


# ==============================================================
# Test: Budget status and zone_c computation
# ==============================================================

class TestBudgetStatus:
    """Test get_budget_status and zone_c_budget property."""

    def test_zone_c_budget_before_allocation(self):
        """Before any allocation, zone_c = total - zone_a (zone_b_used=0)."""
        mgr = TokenBudgetManager()
        assert mgr.zone_c_budget == 128_000 - 8000  # 120_000

    def test_zone_c_budget_after_allocation(self):
        """After allocation, zone_c accounts for zone_b estimated tokens."""
        pcg = _make_pcg(sections={"intro": 1000})
        mgr = TokenBudgetManager()
        alloc = mgr.compute_zone_b_allocation(pcg=pcg, current_task_section="intro")

        expected_zone_b = int(1000 * 1.3)  # 1300
        assert mgr.zone_c_budget == 128_000 - 8000 - expected_zone_b

    def test_get_budget_status_dict(self):
        """get_budget_status returns correct dict structure."""
        pcg = _make_pcg(sections={"intro": 500})
        mgr = TokenBudgetManager()
        mgr.compute_zone_b_allocation(pcg=pcg, current_task_section="intro")

        status = mgr.get_budget_status()
        assert status["zone_a"] == 8000
        assert status["zone_b_used"] == int(500 * 1.3)
        assert status["zone_b_max"] == 40_000
        assert status["total"] == 128_000
        assert status["zone_c_available"] == 128_000 - 8000 - int(500 * 1.3)


# ==============================================================
# Test: Token estimation logic
# ==============================================================

class TestTokenEstimation:
    """Test the internal token estimation logic via observable allocation results."""

    def test_full_load_estimation(self):
        """Full load tokens = word_count * 1.3."""
        pcg = _make_pcg(sections={"sec": 1000})
        mgr = TokenBudgetManager()
        alloc = mgr.compute_zone_b_allocation(pcg=pcg, current_task_section="sec")
        assert alloc.estimated_tokens == int(1000 * 1.3)

    def test_digest_estimation(self):
        """Each digest section adds ~80 tokens."""
        pcg = _make_pcg(
            sections={"A": 100, "B": 100, "C": 100},
            edges=[("A", "B"), ("A", "C")],
        )
        mgr = TokenBudgetManager()
        alloc = mgr.compute_zone_b_allocation(pcg=pcg, current_task_section="A")

        # A full: 100*1.3=130, B+C digest: 2*80=160, total=290
        assert alloc.estimated_tokens == int(100 * 1.3) + 2 * 80

    def test_name_only_estimation(self):
        """Each name_only section adds ~5 tokens."""
        pcg = _make_pcg(sections={"A": 100, "B": 100, "C": 100, "D": 100})
        mgr = TokenBudgetManager()
        # No current task section → all go to name_only
        alloc = mgr.compute_zone_b_allocation(pcg=pcg, current_task_section="")

        # All 4 sections in name_only: 4 * 5 = 20
        assert alloc.estimated_tokens == 4 * 5
        assert len(alloc.name_only) == 4
