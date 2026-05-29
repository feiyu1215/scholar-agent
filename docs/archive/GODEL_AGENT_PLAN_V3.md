# ScholarAgent Godel Layer -- Bounded Recursive Self-Improvement (V3 Ideal State)

> **File Positioning**: This is the ideal-state design for ScholarAgent C3 (Godel Agent).
> Based on V2 Final cognitive philosophy + AI_Agent_Frontier_Report insights + deep optimization for 50-70 page papers.
>
> **Core Evolution**: V2 to V3 is not incremental patching, but an **architecture-level redesign** for the specific high-context scenario of "reviewing long academic papers."
>
> **Design Goals**: Enable Agent to (1) maintain global understanding of paper logic structure, (2) efficiently use the 128K context window, (3) produce traceable findings with evidence chains, (4) accumulate self-improvement data within a single review session.

---

## 1. Design Philosophy

### 1.1 Paradigm Shift from V2 to V3

| Dimension | V2 Final (General Agent) | V3 Ideal (Long Paper Cognition) |
|-----------|--------------------------|----------------------------------|
| Paper cognition model | Flat section list + linear reading | Paper Cognition Graph (graph + logic edges) |
| Token management | Single compaction threshold | Three-Zone Budget Manager (Reserved/Paper/Dialogue) |
| Self-improvement validation | 12% cross-session contrast (6/50 sessions) | IntraSession section-level A/B (50+ obs/session) |
| Metacognition frequency | Fixed every 10 sessions (~3-5 weeks) | Tri-frequency adaptive (per-turn/3-session/deep) |
| Traceability | None | EvidenceChain full tracing |
| Experience storage | 50 session-level records | Three-tier (500/100/permanent) |
| Signal mechanism | Stacked nudges | Unified Signal Dispatcher (priority queue) |
| Hypothesis system | Dual module redundancy | HypothesisModule as SoT + CognitiveState projection |

### 1.2 Core Principles (Inherited + New)

**P1-P4: Inherited from V2 Final (unchanged)**
- P1: Agent-as-Cognizer, not Agent-as-Subject-of-Optimization
- P2: Independent LLM calls, not independent Agents
- P3: Constrain-rather-than-Control
- P4: Minimal conflict with existing architecture

**P5: Graph over Linear (NEW)**

Papers are non-linear knowledge structures -- Methods assumptions affect Results interpretation, Introduction claims need cross-referencing with Conclusions. The Agent's cognition model must be a graph, not a linear summary.

**P6: Pre-computation over Real-time Exploration (NEW, from CodeGraph)**

The Agent should not re-read cross-section information every time it is needed. Build graph index at paper load time, then query by demand with zero LLM cost.

**P7: Single-Session Closed-Loop Validation (NEW, from Anthropic Budget-Awareness)**

Reviewing 50-70 page papers may take 40-60 turns per session -- enough data for self-improvement validation within one session. Don't waste 88% of sessions waiting for cross-session contrast.

**P8: Traceability as First-Class Citizen (NEW, from Harness R.E.S.T)**

Every high-priority finding must have a complete evidence chain. This serves both user trust and Meta-Reflector habit effectiveness evaluation.

### 1.3 Mapping to AI_Agent_Frontier_Report

| Industry Frontier | Application in This Design |
|-------------------|---------------------------|
| CodeGraph pre-computed knowledge graph | Paper Cognition Graph (PCG) -- paper-level pre-index |
| TencentDB Context Offloading (61% token save) | Three-Zone Token Budget + PCG-driven on-demand loading |
| TencentDB Mermaid infinite canvas | PCG Mermaid visualization + mid-session cognition graph |
| Harness R.E.S.T Traceability | EvidenceChain full tracing |
| Anthropic budget-awareness | Zone A/B/C allocation + logic-dependency LRU |
| 17 Architectures Metacognitive Agent | Unified Signal Dispatcher + Tri-frequency MetaReflector |
| GPTSwarm edge probability learning | IntraSession Contrast section-level habit observation |
| Karpathy precise constraints | Constitutional layer stays lean (< 15 invariants) |
| Implementation-Notes cognitive transparency | EvidenceChain + ReviewCognitionGraph full audit trail |

---

## 2. Three-Layer Architecture (V3 Redesign)

```
+---------------------------------------------------------------------+
| Layer 0: Constitutional -- Absolutely immutable by self-modification  |
|---------------------------------------------------------------------|
| C1: MAX_META_DEPTH = 2 (no Level 3 recursion)                       |
| C2: doom_loop_guard + token_budget (cannot be disabled)             |
| C3: evidence >= 3 required for habit promotion                      |
| C4: max 1 habit abandoned per meta_reflect                          |
| C5: doubt decay step <= 0.15                                        |
| C6: cooldown: 12 sessions after abandon before re-evaluation        |
| C7: MAX_LEARNED_HABITS = 10                                         |
| C8: High-priority finding must have EvidenceChain (>= 2 steps) [NEW]|
| C9: PCG integrity (no orphan claim nodes)                      [NEW]|
| C10: Zone A budget non-compressible (>= 6000 tokens)           [NEW]|
| C11: Signal Dispatcher max 2 system messages per turn          [NEW]|
| C12: IntraSession Contrast covers >= 30% sections as control   [NEW]|
| C13: JSON format + tool schema immutable                            |
+---------------------------------------------------------------------+

+---------------------------------------------------------------------+
| Layer 1: Cognitive -- Evolvable cognitive content                    |
|---------------------------------------------------------------------|
| - Paper Cognition Graph (per-paper, session-scoped)            [NEW]|
|   -- Inherits PaperStructureIndex skeleton                          |
|   -- NEW: semantic weight edges, cognitive progress markers         |
| - Learned habit library (LearnedHabit, confidence-weighted)         |
| - Domain review templates (per paper_type strategy experience)      |
| - Section Reading Strategy (learned from IntraSession Contrast)[NEW]|
| - EvidenceChain pattern library                                [NEW]|
| Modification: only through Layer 2 metacognitive processes          |
+---------------------------------------------------------------------+

+---------------------------------------------------------------------+
| Layer 2: Meta-Cognitive -- Examines Layer 1                         |
|---------------------------------------------------------------------|
| - Ability A: Habit Interrogation                                    |
| - Ability B: Maturity Awareness                                     |
| - Ability C: Meta-Cognitive Note                                    |
| - Ability D: PCG Strategy Review -- is reading path optimal?   [NEW]|
| - Ability E: EvidenceChain Quality Review                      [NEW]|
| Implementation: Tri-frequency adaptive trigger                [IMPROVED]|
|   Fast check: every 3 sessions, rules only, zero LLM               |
|   Deep reflect: every 10 sessions, full LLM call                   |
|   Emergency reflect: realtime, when single session severely inefficient|
+---------------------------------------------------------------------+
```

---

## 3. Infrastructure Layer (V3 New)

### 3.1 Paper Cognition Graph (PCG)

#### 3.1.1 Design Rationale

Existing `paper_index.py` `PaperStructureIndex` already implements:
- Regex extraction of sections list, word_counts
- CrossReference network (figure/table/equation/section refs)
- evidence_map (figure/table to citing sections)
- dependency_pairs (A references Section B)
- paper_type inference (empirical/theoretical/review)
- `get_reading_priority()`, `get_evidence_chain(claim_section)`

**V3 PCG adds cognitive layer enhancement on top**, no reinvention:

```python
@dataclass
class PCGNode:
    """PCG node = Section-level cognitive unit."""

    # === Inherited from PaperStructureIndex ===
    section_name: str
    word_count: int
    outgoing_refs: list[str]  # from cross_references
    incoming_refs: list[str]

    # === V3 NEW: Cognitive layer ===
    digest: str = ""                    # <=300 char summary (LLM generated after first read)
    claims: list[str] = field(default_factory=list)  # core claims of this section
    assumptions: list[str] = field(default_factory=list)  # assumptions this section depends on

    # === V3 NEW: Progress tracking ===
    read_depth: Literal["unread", "scanned", "read", "verified"] = "unread"
    findings_linked: list[str] = field(default_factory=list)  # attached finding IDs
    hypotheses_linked: list[str] = field(default_factory=list)  # related hypothesis IDs

    # === V3 NEW: IntraSession Contrast markers ===
    contrast_phase: Literal["A", "B", "none"] = "none"  # which contrast group
    habits_active_when_read: list[str] = field(default_factory=list)


@dataclass
class PCGEdge:
    """PCG edge = Logic dependency relationship (with semantic weight)."""

    source: str       # section name
    target: str       # section name or evidence ID
    edge_type: str    # CLAIM_SUPPORTS | ASSUMPTION_OF | CONTRADICTS |
                      # REFERENCES | VALIDATES | BUILDS_ON
    weight: float = 1.0   # semantic importance 0.0-1.0 (dynamically mutable)
    evidence: str = ""    # brief explanation of why this edge exists

    # === V3: Runtime evolution ===
    discovered_at_turn: int = 0
    verified: bool = False


@dataclass
class PaperCognitionGraph:
    """
    Paper Cognition Graph -- Agent's structured understanding model of a paper.

    Inheritance:
        PaperStructureIndex (regex skeleton, <1 sec)
            | enhancement
        PaperCognitionGraph (cognitive layer, needs LLM for digest/claims)

    Lifecycle:
        1. Paper load -> PaperIndexBuilder.build() -> PaperStructureIndex
        2. INITIAL_SCAN phase -> per-section digest generation -> PCG init
        3. DEEP_REVIEW phase -> Agent dynamically updates edges/claims/read_depth
        4. mark_complete -> PCG frozen -> persisted as ReviewCognitionGraph

    Context interaction:
        - Zone B (Paper Zone) content decided by PCG
        - context_for_task(finding_id) -> auto-pull related node digests + edges
        - coverage_gaps() -> unread/unverified nodes
    """

    nodes: dict[str, PCGNode] = field(default_factory=dict)
    edges: list[PCGEdge] = field(default_factory=list)
    paper_type: str = "unknown"
    _structure_index: PaperStructureIndex | None = None

    @classmethod
    def from_structure_index(cls, index: PaperStructureIndex) -> "PaperCognitionGraph":
        """Build initial PCG from existing PaperStructureIndex.

        This is the V3-to-existing-code bridge:
        - PaperStructureIndex data mapped to PCGNodes
        - dependency_pairs mapped to PCGEdges
        - evidence_map mapped to REFERENCES edges
        """
        pcg = cls()
        pcg._structure_index = index
        pcg.paper_type = index.paper_type

        # Map nodes
        for section_name in index.sections:
            pcg.nodes[section_name] = PCGNode(
                section_name=section_name,
                word_count=index.section_word_counts.get(section_name, 0),
                outgoing_refs=[
                    ref.target_id for ref in index.cross_references
                    if ref.source_section == section_name
                ],
                incoming_refs=[
                    ref.source_section for ref in index.cross_references
                    if ref.target_type == "section"
                    and section_name.lower() in ref.target_id.lower()
                ],
            )

        # Map edges from dependency_pairs
        for source, target in index.dependency_pairs:
            pcg.edges.append(PCGEdge(
                source=source,
                target=target,
                edge_type="REFERENCES",
                weight=0.5,
            ))

        # Map evidence_map to REFERENCES edges
        for evidence_id, citing_sections in index.evidence_map.items():
            for section in citing_sections:
                pcg.edges.append(PCGEdge(
                    source=section,
                    target=evidence_id,
                    edge_type="REFERENCES",
                    weight=0.7,
                ))

        return pcg

    def context_for_task(self, task_section: str, max_tokens: int = 3000) -> str:
        """Auto-assemble relevant context for current task.

        Strategy:
        1. Current section full digest + claims + assumptions
        2. Logically dependent sections (traversing edges) digests
        3. Related findings and hypotheses summaries
        4. Greedy fill within max_tokens
        """
        ...

    def impact_if_false(self, assumption: str) -> list[str]:
        """Trace impact chain if an assumption is false.

        From node containing assumption, propagate along ASSUMPTION_OF and
        CLAIM_SUPPORTS edges, return all affected claims.
        """
        ...

    def coverage_gaps(self) -> dict[str, list[str]]:
        """Return cognitive coverage gaps.

        Returns:
            {
                "unread": [unread sections],
                "unverified_claims": [sections with claims but unverified],
                "orphan_findings": [findings not linked to any claim],
            }
        """
        ...

    def format_for_zone_a(self, max_tokens: int = 1500) -> str:
        """Format as Zone A navigation summary (always-resident in context).

        Compact graph overview:
        - All section names + read_depth markers (~200 tokens)
        - Core edges (weight > 0.7) text description (~300 tokens)
        - Current findings/hypotheses distribution heatmap (~200 tokens)
        - coverage_gaps summary (~100 tokens)
        """
        ...

    def format_mermaid(self) -> str:
        """Output Mermaid flowchart (from TencentDB inspiration).

        For:
        1. User visualization -- Agent's understanding progress
        2. Compaction recovery -- Mermaid as cognitive compression carrier
        """
        ...

    # === Runtime update interfaces (Harness calls) ===

    def update_after_read(self, section: str, digest: str, claims: list[str]) -> None:
        """Update node after Agent reads a section."""
        ...

    def add_edge(self, source: str, target: str, edge_type: str,
                 weight: float = 0.5, evidence: str = "") -> None:
        """Add edge when Agent discovers new logical relationship."""
        ...

    def link_finding(self, finding_id: str, section: str) -> None:
        """Link finding to corresponding section node."""
        ...

    def serialize_for_compaction(self) -> str:
        """Serialize for compaction recovery.

        Design points:
        - Don't save full digests (too large), only claims + edges
        - Save read_depth state (Agent knows what's been read)
        - Save findings/hypotheses links
        - Target: < 2000 tokens to restore global paper understanding
        """
        ...
```

#### 3.1.2 Integration with Existing Modules

```
paper_loader.py
  +-- _load_paper()
        |-- PaperIndexBuilder.build()        [existing, unchanged]
        |   +-- -> PaperStructureIndex
        +-- PaperCognitionGraph.from_structure_index()  [NEW]
            +-- -> state.paper_cognition_graph

assembler.py
  +-- format_context()
        |-- _compute_paper_structure()       [existing, use PCG.format_for_zone_a()]
        +-- _compute_paper_subset()          [existing, use PCG.context_for_task()]

compaction.py
  +-- WorkspaceSnapshot
        +-- pcg_snapshot: str                [NEW, PCG.serialize_for_compaction()]

loop.py (zero modification)
```

---

### 3.2 Three-Zone Token Budget Manager

#### 3.2.1 Design Rationale

50-70 page paper token distribution (measured):
- Paper full text: 25,000-40,000 tokens
- System prompt + habits + memory: ~3,000 tokens
- Single-turn Agent output: ~2,000-4,000 tokens
- 128K context after 40 turns: cumulative dialogue reaches 80,000-120,000 tokens

**Problem**: Existing compaction is "post-hoc rescue" -- waits until context overflows then compresses. For long papers, should be "pre-managed" -- only load needed paper fragments from the start.

```python
@dataclass
class TokenBudgetManager:
    """
    Three-Zone Token Budget Model.

    Inspirations:
    - TencentDB Context Offloading: on-demand loading instead of compressing
    - CodeGraph: pre-computation replaces real-time exploration
    - Anthropic budget-awareness: explicit token consumption management

    Three Zones:

    +--------------------------------------------------------+
    | Zone A: Reserved (Always-Resident)            ~8,000T   |
    |--------------------------------------------------------|
    | - static_identity prompt                               |
    | - active habits (confidence-weighted)                   |
    | - PCG navigation summary (format_for_zone_a)           |
    | - session_memory restoration                           |
    | - current findings summary                             |
    | - metacognition CognitiveState                         |
    | Strategy: never compressed, Agent's "workbench"        |
    +--------------------------------------------------------+
    | Zone B: Paper Zone                    dynamic 0~40,000T |
    |--------------------------------------------------------|
    | - Active section: full text content                     |
    | - Related sections: digest + key claims (~50T/section)  |
    | - Inactive sections: name only (~5T/section)            |
    | Strategy: PCG decides what to load                      |
    |   - Current task section -> full load                   |
    |   - PCG edges dependencies -> digest load              |
    |   - LRU + logical dependency dual strategy             |
    +--------------------------------------------------------+
    | Zone C: Dialogue Zone                 remaining ~80,000T|
    |--------------------------------------------------------|
    | - Agent reasoning and interaction history               |
    | - Standard compaction (keep recent N turns)             |
    | - Finding refs replaced with PCG ref                   |
    |   (e.g. "see [PCG:Methods S3.2]")                      |
    | Strategy: protect EvidenceChain references on compact  |
    +--------------------------------------------------------+
    """

    total_budget: int = 128_000
    zone_a_min: int = 6_000     # Constitutional C10: cannot go below 6000
    zone_a_max: int = 10_000
    zone_b_max: int = 40_000

    pcg: PaperCognitionGraph | None = None

    def compute_allocation(self, current_task_section: str) -> dict:
        """Compute Zone B content allocation for current turn.

        Returns:
            {
                "full_load": [section_names],
                "digest_load": [section_names],
                "name_only": [section_names],
                "estimated_zone_b_tokens": int,
            }
        """
        if self.pcg is None:
            return {"full_load": [], "digest_load": [],
                    "name_only": [], "estimated_zone_b_tokens": 0}

        full_load = [current_task_section]
        digest_load = []
        name_only = []

        # 1. Logical dependencies: PCG edges related to current section
        related = self._get_logically_related(current_task_section)
        digest_load.extend(related)

        # 2. Active hypothesis referenced sections
        active_hyp_sections = self._get_hypothesis_related_sections()
        for s in active_hyp_sections:
            if s not in full_load and s not in digest_load:
                digest_load.append(s)

        # 3. Remaining -> name_only
        for node_name in self.pcg.nodes:
            if node_name not in full_load and node_name not in digest_load:
                name_only.append(node_name)

        # 4. Budget check: if Zone B over limit, downgrade digest_load -> name_only
        estimated = self._estimate_tokens(full_load, digest_load, name_only)
        while estimated > self.zone_b_max and digest_load:
            lru_section = self._find_lru(digest_load)
            digest_load.remove(lru_section)
            name_only.append(lru_section)
            estimated = self._estimate_tokens(full_load, digest_load, name_only)

        return {
            "full_load": full_load,
            "digest_load": digest_load,
            "name_only": name_only,
            "estimated_zone_b_tokens": estimated,
        }

    def _get_logically_related(self, section: str) -> list[str]:
        """Get sections logically dependent on current section (1-hop)."""
        related = set()
        for edge in self.pcg.edges:
            if edge.source == section:
                related.add(edge.target)
            elif edge.target == section:
                related.add(edge.source)
        return [r for r in related if r in self.pcg.nodes]

    def _get_hypothesis_related_sections(self) -> list[str]:
        """Get sections involved in active hypotheses."""
        sections = set()
        for node in self.pcg.nodes.values():
            if node.hypotheses_linked:
                sections.add(node.section_name)
        return list(sections)

    def _find_lru(self, sections: list[str]) -> str:
        """Find least recently operated section."""
        least_active = sections[0]
        least_activity = float('inf')
        for s in sections:
            node = self.pcg.nodes.get(s)
            if node:
                activity = len(node.findings_linked) + len(node.hypotheses_linked)
                if activity < least_activity:
                    least_activity = activity
                    least_active = s
        return least_active

    def _estimate_tokens(self, full: list, digest: list, names: list) -> int:
        """Estimate Zone B total token consumption."""
        total = 0
        for s in full:
            node = self.pcg.nodes.get(s)
            total += node.word_count * 1.3 if node else 0
        for s in digest:
            total += 80
        for s in names:
            total += 5
        return int(total)
```

#### 3.2.2 Relationship with Existing Compaction

**Key design decision**: PCG digests are compaction-safe -- they are injected into Zone A (reserved) after compaction, so Agent NEVER loses global understanding of paper logic structure. This solves the fundamental problem of existing compaction only recovering "data" but not "cognitive judgments."

```
Existing compaction.py (not deleted, enhanced)
+----------------------------------------------+
| CompactionConfig (existing)                    |
| restoration_budget = 6000 tokens              |
| + WorkingCognitionSnapshot (V3 NEW)           |
|   On compaction trigger:                      |
|   1. Serialize PCG active digests + edges     |
|   2. Serialize CognitiveState                 |
|   3. Write .workspace/cognition_snapshot.json |
|                                               |
| Recovery (RestorationLayer):                  |
|   Priority 0: findings + edits (existing)     |
|   Priority 1: hypothesis status (existing)    |
|   Priority 2: PCG active nodes + edges (NEW)  |
|   Priority 3: CognitiveState (NEW)           |
+----------------------------------------------+
```

---

### 3.3 Unified Signal Dispatcher

#### 3.3.1 Problem Background

Current `loop.py` has stacked Harness-to-Agent signal injection:

```python
# Current loop.py signal stacking problem
soft_turn_warning = harness.check_soft_turn_limit()     # may inject
budget_warning = harness.check_token_budget()           # may inject
cognitive_nudge = harness.check_cognitive_output()      # may inject
reflection_nudge = harness.check_reflection_needed()    # may inject
# V3 adds MetaReflector signals, PCG navigation suggestions... explosion
```

Worst case: Agent receives 4-5 system messages in a single turn, causing attention dilution.

#### 3.3.2 Design

```python
@dataclass
class HarnessSignal:
    """Unified signal format."""
    source: str          # "budget" | "cognitive" | "reflection" | "pcg" | "meta"
    priority: int        # 0=urgent(doom), 1=high(budget), 2=mid(cognitive), 3=low(suggestion)
    message: str
    suppress_if: list[str] = field(default_factory=list)


class SignalDispatcher:
    """
    Unified Signal Dispatcher.

    Design principles (from Anthropic "three-element minimalism"):
    - Max 2 system messages per turn (Priority 0 exceptions: doom must pass)
    - Higher priority suppresses lower priority
    - Same-source dedup (no repeat within 3 consecutive turns)

    Relationship with existing loop.py:
    - Replaces the 4 independent check_xxx() calls
    - All check functions still exist, but output goes through dispatcher.submit()
    """

    MAX_SIGNALS_PER_TURN = 2
    DEDUP_WINDOW = 3

    def __init__(self):
        self._history: list[tuple[int, str]] = []
        self._pending: list[HarnessSignal] = []

    def submit(self, signal: HarnessSignal) -> None:
        """Submit a signal candidate."""
        self._pending.append(signal)

    def dispatch(self, current_turn: int) -> list[str]:
        """Select signals to actually inject this turn.

        Selection strategy:
        1. Priority 0 always passes (safety non-negotiable)
        2. Dedup: check DEDUP_WINDOW for same-source
        3. Suppress: check suppress_if conditions
        4. Truncate: keep top MAX_SIGNALS_PER_TURN
        """
        self._pending.sort(key=lambda s: s.priority)

        selected = []
        for signal in self._pending:
            if signal.priority == 0:
                selected.append(signal.message)
                self._history.append((current_turn, signal.source))
                continue

            recent_sources = [
                src for turn, src in self._history
                if current_turn - turn < self.DEDUP_WINDOW
            ]
            if signal.source in recent_sources:
                continue

            if len(selected) >= self.MAX_SIGNALS_PER_TURN:
                break

            selected.append(signal.message)
            self._history.append((current_turn, signal.source))

        self._pending.clear()
        return selected
```

#### 3.3.3 Loop Integration

```python
# loop.py modification (minimal invasion)

dispatcher = harness.signal_dispatcher

if warning := harness.check_soft_turn_limit():
    dispatcher.submit(HarnessSignal(source="turn", priority=1, message=warning))
if warning := harness.check_token_budget():
    dispatcher.submit(HarnessSignal(source="budget", priority=1, message=warning))
if nudge := harness.check_cognitive_output():
    dispatcher.submit(HarnessSignal(source="cognitive", priority=2, message=nudge,
                                     suppress_if=["budget", "turn"]))
if nudge := harness.check_reflection_needed():
    dispatcher.submit(HarnessSignal(source="reflection", priority=3, message=nudge,
                                     suppress_if=["cognitive"]))

for msg in dispatcher.dispatch(harness.state.loop_turns):
    messages.append({"role": "system", "content": msg})
```

---

## 4. Godel Layer V3: Self-Improvement System Redesign

### 4.1 Observation Granularity: Section-as-Unit

| Dimension | V2 | V3 | Reason |
|-----------|----|----|--------|
| Observation unit | Session (1 review = 1 observation) | Section (1 section read = 1 observation) | 50+ observations per session vs 1 |
| Contrast method | 12% random cross-session | IntraSession section-level A/B | Controls confounders |
| Experience storage | 50 session records | L0(500)/L1(100)/L2(permanent) | Multi-granularity evidence |

### 4.2 Data Architecture

#### 4.2.1 Hierarchical Experience Store

```python
@dataclass
class SectionExperience:
    """
    L0: Section-level micro-experience.
    Sliding window: 500 records.
    """
    session_id: str
    section_name: str
    paper_type: str

    # Behavioral metrics
    turns_spent: int
    findings_produced: int
    evidence_chains_built: int

    # Cognitive metrics
    hypotheses_generated: int
    hypotheses_resolved: int
    cross_refs_followed: int

    # Evolution metrics
    active_habit_ids: list[str]
    pcg_edges_used: int

    # Efficiency metrics
    tokens_consumed: int
    findings_per_token: float  # core efficiency metric


@dataclass
class SessionExperience:
    """
    L1: Session-level macro-experience (V2 structure upgraded).
    Sliding window: 100 records.
    """
    # === V2 inherited fields ===
    session_id: str
    timestamp: str
    paper_type: str
    total_turns: int
    findings_count: int
    high_priority_count: int
    evidence_ratio: float
    actionable_ratio: float
    idle_before_exit: int
    turns_to_first_finding: int
    findings_per_turn: float
    strategy_transitions: int
    sections_read_ratio: float
    active_habit_ids: list[str]
    is_contrast_session: bool  # V3: backward compat only
    quality_signals: dict

    # === V3 NEW fields ===
    # IntraSession contrast
    phase_a_sections: list[str]
    phase_b_sections: list[str]
    phase_a_habits: list[str]
    phase_b_habits: list[str]
    phase_a_findings_density: float
    phase_b_findings_density: float

    # PCG usage
    pcg_nodes_total: int
    pcg_edges_total: int
    pcg_coverage: float

    # Evidence chains
    evidence_chains_total: int
    avg_chain_length: float

    # Efficiency
    total_tokens: int
    findings_per_1k_tokens: float


@dataclass
class EvolutionRecord:
    """
    L2: Evolution-level metadata. Permanently retained.
    One record per meta_reflect invocation.
    """
    timestamp: str
    trigger_type: str  # "fast" | "deep" | "emergency"
    habit_decisions: list[dict]
    maturity_updates: list[dict]
    contrast_evidence: dict
    meta_note: str
    token_efficiency_trend: str  # "improving" | "stable" | "degrading"
```

#### 4.2.2 EvidenceChain Data Structure

```python
@dataclass
class EvidenceStep:
    """One step in the evidence chain."""
    action: str          # "read_section" | "hypothesis_formed" | "search_literature" | "cross_ref_followed"
    target: str          # section name / literature title / hypothesis content
    observation: str     # <=120 char key finding
    turn: int            # turn number when this happened
    pcg_edge_used: str   # which PCG edge was used (can be empty)


@dataclass
class EvidenceChain:
    """
    Complete evidence chain for a finding.

    Uses:
    - On compaction: full chain offloaded to .workspace/refs/, only 1-line summary in context
    - On user challenge: recall full chain
    - On meta-reflect: chain_length and pcg_edges_used signal finding quality
    - Evolution: high-quality chain patterns can be learned
    """
    finding_id: str
    finding_text: str
    priority: str         # "high" | "medium" | "low"
    steps: list[EvidenceStep]
    total_turns_span: int
    pcg_edges_used: int

    @property
    def chain_length(self) -> int:
        return len(self.steps)

    @property
    def summary(self) -> str:
        """1-line summary for post-compaction context retention."""
        if not self.steps:
            return f"[{self.finding_id}] {self.finding_text[:80]}"
        first = self.steps[0]
        last = self.steps[-1]
        return (
            f"[{self.finding_id}] {self.finding_text[:60]} "
            f"(chain: {first.action}->...-> {last.action}, "
            f"{self.chain_length} steps, turns {first.turn}-{last.turn})"
        )

    def format_full(self) -> str:
        """Full format for recall or audit."""
        lines = [f"=== Evidence Chain: {self.finding_id} ==="]
        lines.append(f"Finding: {self.finding_text}")
        lines.append(f"Priority: {self.priority} | Steps: {self.chain_length} | Turns: {self.total_turns_span}")
        lines.append("")
        for i, step in enumerate(self.steps, 1):
            edge_info = f" [PCG: {step.pcg_edge_used}]" if step.pcg_edge_used else ""
            lines.append(f"  {i}. [{step.action}] {step.target}{edge_info}")
            lines.append(f"     -> {step.observation}")
        return "\n".join(lines)
```

---

### 4.3 IntraSession Contrast Design

#### 4.3.1 Core Concept

Abandon V2's "12% cross-session random contrast" -- for 50-70 page papers, this has three fatal problems:
1. 12% x 50 records = 6 contrast points, statistically almost meaningless
2. Contrast sessions waste 12% review quality (no habits = Agent regresses)
3. Cross-session contrast cannot control "paper difficulty variance" as confounding variable

**V3 Approach**: Compare between different sections within the SAME paper.

#### 4.3.2 Implementation

```python
class IntraSessionContrastManager:
    """
    Intra-session cognitive contrast manager.

    Design rationale:
    - 50-70 page papers have 15-25 sections
    - Split sections into Phase A and Phase B
    - Phase A: inject full habit set
    - Phase B: inject habit set minus 1 target habit under validation
    - Same paper -> controls paper quality confound
    - Same session -> controls Agent state confound
    - N section observations > 1 session observation

    Constraints:
    - Does not affect review quality (Phase B only removes 1 habit, not all)
    - Does not affect findings completeness (contrast is post-hoc analysis)
    - Only habits with confidence in [0.4, 0.7] are selected as targets
    """

    PHASE_SPLIT_RATIO = 0.5
    TARGET_CONFIDENCE_RANGE = (0.4, 0.7)

    def plan_contrast(self, sections: list[str], habits: list) -> dict | None:
        """Plan contrast at session start.

        Returns:
        {
            "target_habit_id": "learned_003",
            "phase_a_sections": [...],
            "phase_b_sections": [...],
            "phase_a_habits": [...],
            "phase_b_habits": [...],
        }
        Returns None if no suitable habit to validate.
        """
        target = self._select_target_habit(habits)
        if target is None:
            return None

        split_point = int(len(sections) * self.PHASE_SPLIT_RATIO)
        phase_a = sections[:split_point]
        phase_b = sections[split_point:]

        all_habit_ids = [h.id for h in habits]
        phase_b_habits = [h.id for h in habits if h.id != target.id]

        return {
            "target_habit_id": target.id,
            "phase_a_sections": phase_a,
            "phase_b_sections": phase_b,
            "phase_a_habits": all_habit_ids,
            "phase_b_habits": phase_b_habits,
        }

    def _select_target_habit(self, habits: list):
        """Select most suitable habit for validation (confidence in validation range)."""
        candidates = [
            h for h in habits
            if self.TARGET_CONFIDENCE_RANGE[0] <= h.confidence <= self.TARGET_CONFIDENCE_RANGE[1]
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda h: abs(h.confidence - 0.5))

    def analyze_contrast(self, section_experiences: list, plan: dict) -> dict:
        """Post-session contrast analysis.

        Returns:
        {
            "target_habit_id": "learned_003",
            "phase_a_findings_density": 0.8,
            "phase_b_findings_density": 0.6,
            "delta": +0.2,    # A better than B -> habit effective
            "recommendation": "reinforce" | "doubt" | "insufficient_data"
        }
        """
        phase_a_sections = set(plan["phase_a_sections"])
        phase_b_sections = set(plan["phase_b_sections"])

        a_exps = [e for e in section_experiences if e.section_name in phase_a_sections]
        b_exps = [e for e in section_experiences if e.section_name in phase_b_sections]

        if len(a_exps) < 3 or len(b_exps) < 3:
            return {"recommendation": "insufficient_data"}

        a_density = sum(e.findings_produced for e in a_exps) / len(a_exps)
        b_density = sum(e.findings_produced for e in b_exps) / len(b_exps)
        delta = a_density - b_density

        if delta > 0.15:
            recommendation = "reinforce"
        elif delta < -0.15:
            recommendation = "doubt"
        else:
            recommendation = "insufficient_data"

        return {
            "target_habit_id": plan["target_habit_id"],
            "phase_a_findings_density": a_density,
            "phase_b_findings_density": b_density,
            "delta": delta,
            "statistical_note": f"N_a={len(a_exps)}, N_b={len(b_exps)}",
            "recommendation": recommendation,
        }
```

**Backward compatibility with V2 12% contrast**: V3 does not delete `is_contrast_session` field, but defaults to disabled (`CONTRAST_PROBABILITY = 0.0`). IntraSession contrast fully replaces it with superior data quality.

---

### 4.4 Tri-Frequency Adaptive MetaReflector

#### 4.4.1 Fast Reflect (Every 3 sessions, zero LLM)

```python
class FastReflector:
    """
    Pure rules, zero LLM calls. Trend detection only:
    - findings_density declining 3 sessions -> flag
    - evidence_ratio declining 3 sessions -> flag
    - New habit + metrics worsen in 3 sessions -> auto-doubt that habit

    Output: alert list (injected as notice in next session system prompt)
    """

    TRIGGER_INTERVAL = 3
    DECLINE_THRESHOLD = 0.15

    def should_trigger(self, experience_count: int, last_fast_reflect: int) -> bool:
        return experience_count - last_fast_reflect >= self.TRIGGER_INTERVAL

    def analyze(self, recent_experiences: list[dict]) -> list[str]:
        """Pure rule analysis, returns alert list."""
        alerts = []
        if len(recent_experiences) < 3:
            return alerts

        last_3 = recent_experiences[-3:]

        # Trend: findings_density
        densities = [e.get("findings_per_turn", 0) for e in last_3]
        if all(densities[i] > densities[i+1] for i in range(len(densities)-1)):
            decline = (densities[0] - densities[-1]) / max(densities[0], 0.01)
            if decline > self.DECLINE_THRESHOLD:
                alerts.append(f"findings_density declining 3 sessions: {decline:.0%}")

        # Trend: evidence_ratio
        ev_ratios = [e.get("evidence_ratio", 0) for e in last_3]
        if all(ev_ratios[i] > ev_ratios[i+1] for i in range(len(ev_ratios)-1)):
            decline = (ev_ratios[0] - ev_ratios[-1]) / max(ev_ratios[0], 0.01)
            if decline > self.DECLINE_THRESHOLD:
                alerts.append(f"evidence_ratio declining 3 sessions: {decline:.0%}")

        return alerts
```

#### 4.4.2 Deep Reflect (Every 10 sessions, full LLM)

Inherits V2 `MetaReflector` with enhanced data inputs:

```python
class DeepReflector(MetaReflector):  # Inherits V2 MetaReflector
    """
    V3 enhanced deep reflection.

    Differences from V2:
    1. Data source expanded from SessionExperience to L0+L1 dual layer
    2. IntraSession contrast data as evidence
    3. PCG usage efficiency analysis
    4. EvidenceChain pattern recognition

    Trigger conditions:
    - Every 10 sessions (inherited from V2)
    - OR maturity sudden change (> 0.2 within 3 sessions)
    - OR FastReflector reports anomalies 2 consecutive times
    """

    def precompute_context_v3(self, memory_store, learned_habits: list,
                              section_experiences: list, contrast_results: list[dict]) -> str:
        """V3 enhanced context pre-computation."""
        base_context = super().precompute_context(memory_store, learned_habits)
        contrast_text = self._format_contrast_evidence(contrast_results)
        section_text = self._format_section_efficiency(section_experiences, learned_habits)
        return base_context + f"\n\n## IntraSession Contrast Evidence\n{contrast_text}\n\n## Section Efficiency\n{section_text}"
```

#### 4.4.3 Emergency Reflect (Realtime, zero LLM)

```python
class EmergencyReflector:
    """
    Realtime trigger. Zero LLM.

    Trigger: idle_before_exit > 10 AND findings < 2, OR tokens > 80K AND findings < 3

    Action:
    - Mark session as "anomaly"
    - Check if recently added habit is suspect
    - If yes: immediate confidence -= 0.1 (don't wait for DeepReflect)
    - Inject warning to next session system prompt
    """

    def check(self, session_stats: dict) -> dict | None:
        idle = session_stats.get("idle_before_exit", 0)
        findings = session_stats.get("findings_count", 0)
        tokens = session_stats.get("total_tokens", 0)

        is_emergency = (
            (idle > 10 and findings < 2) or
            (tokens > 80_000 and findings < 3)
        )

        if not is_emergency:
            return None

        return {
            "type": "emergency",
            "reason": f"idle={idle}, findings={findings}, tokens={tokens}",
            "suspect_habits": session_stats.get("active_habit_ids", []),
        }
```

---

### 4.5 Hypothesis Dual-Module Unification

#### Problem

Two hypothesis systems exist in current code:
- `hypothesis.py` `HypothesisModule`: structured evidence management
- `metacognition.py` `CognitiveState.hypotheses`: metacognitive mirror

With 15+ active hypotheses in a 50-70 page paper, sync overhead is significant.

#### Solution: HypothesisModule as Single Source of Truth

```python
# metacognition.py modification

class CognitiveState:
    # Remove hypotheses field, replace with reference
    _hypothesis_module_ref: Any = None

    def format_for_context(self) -> str:
        """Hypothesis part directly projected from HypothesisModule."""
        if self._hypothesis_module_ref is not None:
            hyp_status = self._hypothesis_module_ref.format_status()
            if hyp_status:
                parts.append(hyp_status)

    def update_from_reflection(self, reflection_output: dict) -> None:
        """Hypothesis updates forwarded to HypothesisModule."""
        if "hypotheses" in reflection_output and self._hypothesis_module_ref:
            for h_data in reflection_output["hypotheses"]:
                if h_data.get("status") in ("confirmed", "refuted", "suspended"):
                    self._hypothesis_module_ref.resolve(
                        h_data["id"], h_data["status"], h_data.get("reason", ""))
                else:
                    self._hypothesis_module_ref.generate(
                        h_data["claim"], source="metacognition_update")
```

Benefits: single source of truth, no sync issues, CognitiveState stays lightweight, 15+ hypotheses have only one data copy.

---

### 4.6 Experience Persistence Fix

#### Problem

`persist_cognitive_hints_as_experience()` uses `effectiveness = min(findings_count / 5.0, 1.0)`. Papers with 20+ findings all score 1.0, losing discrimination.

#### Solution: Relative Efficiency Scoring

```python
def compute_relative_effectiveness(
    findings_count: int,
    tokens_consumed: int,
    sections_covered: int,
    paper_type: str,
    historical_baseline: dict,  # {paper_type: avg_findings_per_1k_tokens}
) -> float:
    """
    Relative efficiency: this review vs historical average for this paper type.

    Formula:
      raw_efficiency = findings_count / (tokens_consumed / 1000)
      baseline = historical_baseline.get(paper_type, 3.0)
      relative = raw_efficiency / baseline
      effectiveness = clip(relative, 0.0, 1.0)

    This way:
    - 20 findings with 100K tokens -> efficiency = 0.2/1k -> may be below baseline
    - 5 findings with 10K tokens -> efficiency = 0.5/1k -> may be above baseline
    """
    raw_efficiency = findings_count / max(tokens_consumed / 1000, 0.1)
    baseline = historical_baseline.get(paper_type, 3.0)
    relative = raw_efficiency / max(baseline, 0.01)
    return min(max(relative, 0.0), 1.0)
```

---

## 5. Implementation Plan (V2-Style Detailed)

### Phase 0: Foundation (COMPLETED)

- [x] `core/memory.py` added `gc_procedures()` (L345-412)
- [x] 70 independent unit tests
- [x] All 710 existing tests pass

---

### Phase 0.5: Paper Cognition Infrastructure (~3 days)

**Goal**: Implement PCG + TokenBudgetManager + SignalDispatcher + EvidenceChain as reliable observation foundation for all subsequent phases.

#### 0.5.1 PCG Data Structures + Bridge

**New file**: `core/paper_cognition_graph.py`

PCG data structures as defined in §3.1. Core class `PaperCognitionGraph` with `from_structure_index()` bridge.

**Existing file modification**: `core/state.py` (WorkspaceState, L51-114)

Add PCG field after `cognition_graph` (L104):

```python
# state.py L104 之后新增
class WorkspaceState:
    # ... existing fields ...
    cognition_graph: ReviewCognitionGraph | None = None  # L104, existing

    # === V3 NEW ===
    paper_cognition_graph: "PaperCognitionGraph | None" = None  # V3: session-scoped PCG
    evidence_chains: list["EvidenceChain"] = field(default_factory=list)  # V3: active evidence chains
```

**Existing file modification**: `core/harness.py` (Harness.__init__, L222-231 region)

After `EvolutionEngine` initialization (L222-231), add PCG initialization:

```python
# harness.py L231 之后新增
class Harness:
    def __init__(self, ...):
        # ... existing init through EvolutionEngine (L222-231) ...

        # === V3 NEW: Paper Cognition Graph ===
        from core.paper_cognition_graph import PaperCognitionGraph
        from core.token_budget import TokenBudgetManager
        from core.signal_dispatcher import SignalDispatcher
        from core.evidence_chain import EvidenceChainTracker

        self.token_budget_manager = TokenBudgetManager()
        self.signal_dispatcher = SignalDispatcher()
        self.evidence_tracker = EvidenceChainTracker()

        # PCG 在 load_paper 时构建（依赖 PaperStructureIndex）
        # ... existing ContextAssembler init (L234-241) ...
```

**Existing file modification**: `core/harness.py` (load_paper, L247-254)

After `PaperIndexBuilder.build()` succeeds, build PCG:

```python
# harness.py load_paper() 方法内，PaperStructureIndex 构建后
def load_paper(self, path=None):
    # ... existing: paper_sections loaded, PaperIndexBuilder.build() ...
    # existing L~258: self.state.paper_structure_index = index

    # === V3 NEW: Build PCG from structure index ===
    if GODEL_PCG_ENABLED and self.state.paper_structure_index:
        from core.paper_cognition_graph import PaperCognitionGraph
        pcg = PaperCognitionGraph.from_structure_index(self.state.paper_structure_index)
        self.state.paper_cognition_graph = pcg
        self.token_budget_manager.pcg = pcg
```

#### 0.5.2 TokenBudgetManager

**New file**: `core/token_budget.py`

Implementation as defined in §3.2. Standalone module, no existing file dependency beyond PCG.

**Existing file modification**: `core/assembler.py` (L272-293: `_compute_paper_structure`)

Replace PaperStructureIndex direct formatting with PCG-aware loading:

```python
# assembler.py _compute_paper_structure 修改
def _compute_paper_structure(ctx) -> str | None:
    """论文预索引 section (V3: PCG-aware)."""
    state = ctx["state"]

    # V3: prefer PCG format if available
    pcg = state.paper_cognition_graph
    if pcg is not None and GODEL_PCG_ENABLED:
        return pcg.format_for_zone_a(max_tokens=1500)

    # Fallback: original PaperStructureIndex format
    index = state.paper_structure_index
    if index is None or index.is_empty():
        return None
    return index.format_for_context()
```

**Existing file modification**: `core/assembler.py` (_register_default_sections, L404-538)

Add new PCG navigation section (priority 89, between paper_overview=90 and paper_structure=88):

```python
# assembler.py _register_default_sections() 内新增
self.register(DynamicSection(
    name="pcg_navigation",
    priority=89,
    compute_fn=_compute_pcg_navigation,
    cache_strategy=CacheStrategy.NEVER,
    condition_fn=lambda ctx: (
        GODEL_PCG_ENABLED
        and ctx["state"].paper_cognition_graph is not None
    ),
))
```

#### 0.5.3 SignalDispatcher

**New file**: `core/signal_dispatcher.py`

Implementation as defined in §3.3.

**Existing file modification**: `core/loop.py` (L101-130: signal injection zone)

Replace stacked `if` checks with dispatcher pattern:

```python
# loop.py L101-130 修改（SignalDispatcher 集成）
async def cognitive_loop(messages, harness, tools, client, verbose=True):
    # ... existing setup ...

    while True:
        # === V3: Unified Signal Dispatch (replaces L101-130 stacked checks) ===
        if GODEL_SIGNAL_DISPATCHER_ENABLED:
            dispatcher = harness.signal_dispatcher

            if doom := harness.check_doom_loop():
                # Priority 0: still direct return (safety non-negotiable)
                return LoopDoomStop(reason=doom)

            if warning := harness.check_soft_turn_limit():
                dispatcher.submit(HarnessSignal(source="turn", priority=1, message=warning))
            if warning := harness.check_token_budget():
                dispatcher.submit(HarnessSignal(source="budget", priority=1, message=warning))
            if nudge := harness.check_cognitive_output():
                dispatcher.submit(HarnessSignal(source="cognitive", priority=2, message=nudge,
                                                 suppress_if=["budget", "turn"]))
            if nudge := harness.check_reflection_needed():
                dispatcher.submit(HarnessSignal(source="reflection", priority=3, message=nudge,
                                                 suppress_if=["cognitive"]))

            for msg in dispatcher.dispatch(harness.state.loop_turns):
                messages.append({"role": "system", "content": msg})
        else:
            # === Fallback: original stacked checks (unchanged) ===
            if doom := harness.check_doom_loop():
                return LoopDoomStop(reason=doom)
            if warning := harness.check_soft_turn_limit():
                messages.append({"role": "system", "content": warning})
            if warning := harness.check_token_budget():
                messages.append({"role": "system", "content": warning})
            if nudge := harness.check_cognitive_output():
                messages.append({"role": "system", "content": nudge})
            if nudge := harness.check_reflection_needed():
                messages.append({"role": "system", "content": nudge})
```

#### 0.5.4 EvidenceChain Tracker

**New file**: `core/evidence_chain.py`

Data structures as defined in §4.2.2, plus tracker class:

```python
# core/evidence_chain.py (关键接口)

class EvidenceChainTracker:
    """Auto-tracks evidence chains for findings.

    Integration:
        - On finding creation (tool_handlers/finding.py): tracker.start_chain(finding_id)
        - On read_section (tool_handlers/reader.py): tracker.add_step("read_section", ...)
        - On hypothesis_formed: tracker.add_step("hypothesis_formed", ...)
        - On session end: tracker.finalize_all() -> list[EvidenceChain]
    """

    def __init__(self):
        self._active_chains: dict[str, EvidenceChain] = {}
        self._completed_chains: list[EvidenceChain] = []

    def start_chain(self, finding_id: str, finding_text: str, priority: str) -> None:
        """Called when Agent creates a new finding."""
        ...

    def add_step(self, finding_id: str, action: str, target: str,
                 observation: str, turn: int, pcg_edge: str = "") -> None:
        """Called on significant cognitive action that may contribute to findings."""
        ...

    def finalize_all(self) -> list[EvidenceChain]:
        """End of session: close all active chains."""
        ...
```

**Existing file modification**: `core/harness.py` (execute_tool, L401-422)

After tool execution, track steps for evidence chains:

```python
# harness.py execute_tool() 末尾新增
def execute_tool(self, name, args) -> str:
    result = self._tool_registry.execute(name, args)

    # === V3 NEW: Evidence chain tracking ===
    if GODEL_EVIDENCE_CHAIN_ENABLED and self.evidence_tracker:
        if name == "read_section":
            section = args.get("section_name", "")
            self.evidence_tracker.add_step_to_recent(
                action="read_section", target=section,
                observation=result[:120], turn=self.state.loop_turns)
        elif name == "search_literature":
            self.evidence_tracker.add_step_to_recent(
                action="search_literature", target=args.get("query", ""),
                observation=result[:120], turn=self.state.loop_turns)

    return result
```

#### 0.5.5 Integration + Tests

**Existing file modification**: `core/compaction.py` (WorkspaceSnapshot, L100-135)

Add PCG and CognitiveState to compaction snapshot:

```python
# compaction.py WorkspaceSnapshot 新增字段 (L112-135 区域)
@dataclass
class WorkspaceSnapshot:
    # ... existing fields (L112-135) ...
    sections_read: list[str]
    findings_count: int
    findings_summary: str
    session_memory_text: str
    hypothesis_text: str
    paper_structure_text: str

    # === V3 NEW ===
    pcg_snapshot: str = ""         # PaperCognitionGraph.serialize_for_compaction()
    cognitive_state_snapshot: str = ""  # CognitiveState.format_for_context()
    evidence_chain_refs: str = ""  # EvidenceChain summary lines
```

**Existing file modification**: `core/compaction.py` (_build_layers, L136-196)

Add new restoration layers:

```python
# compaction.py _build_layers() 新增恢复层
def _build_layers(self) -> list[RestorationLayer]:
    layers = [
        # ... existing layers (priority 1-4) ...

        # === V3 NEW: PCG restoration (priority 5) ===
        RestorationLayer(
            name="pcg_cognition",
            priority=5,
            content=self.pcg_snapshot,
            critical=True,  # Agent must not lose paper structure understanding
        ),
        # === V3 NEW: Evidence chain refs (priority 6) ===
        RestorationLayer(
            name="evidence_refs",
            priority=6,
            content=self.evidence_chain_refs,
            critical=False,
        ),
    ]
    return [l for l in layers if l.content]
```

#### 0.5 Storage Format

```json
// .workspace/cognition_snapshot.json (new, per-session)
{
  "pcg": {
    "nodes": {
      "Introduction": {"read_depth": "verified", "claims": ["A causes B"], "digest": "..."},
      "Methods": {"read_depth": "read", "claims": ["DID design with..."], "digest": "..."}
    },
    "edges": [
      {"source": "Methods", "target": "Results", "type": "VALIDATES", "weight": 0.9},
      {"source": "Introduction", "target": "Methods", "type": "ASSUMPTION_OF", "weight": 0.7}
    ],
    "coverage": 0.72
  },
  "evidence_chains": [
    {
      "finding_id": "F001",
      "summary": "[F001] Parallel trends assumption not tested (chain: read_section->...->cross_ref, 4 steps)",
      "chain_length": 4
    }
  ]
}
```

#### 0.5 Verification Checklist

- [ ] `PaperCognitionGraph.from_structure_index()` correctly maps all sections + dependency_pairs + evidence_map
- [ ] PCG `context_for_task()` returns <= `max_tokens` content
- [ ] PCG `format_for_zone_a()` output <= 1500 tokens
- [ ] `TokenBudgetManager.compute_allocation()` never exceeds `zone_b_max`
- [ ] `TokenBudgetManager` Zone A minimum enforced (Constitutional C10: >= 6000)
- [ ] `SignalDispatcher.dispatch()` returns max 2 messages (except Priority 0)
- [ ] `SignalDispatcher` dedup: same source not repeated within 3 consecutive turns
- [ ] `EvidenceChainTracker.start_chain()` links to existing finding
- [ ] `EvidenceChainTracker.finalize_all()` produces valid chains
- [ ] `WorkspaceSnapshot` correctly serializes/restores PCG state
- [ ] Existing compaction tests still pass (zero regression)
- [ ] Kill switch: `SCHOLAR_GODEL_PCG=0` → fallback to PaperStructureIndex
- [ ] Kill switch: `SCHOLAR_GODEL_DISPATCHER=0` → original stacked checks
- [ ] All existing 710 tests pass
- [ ] >= 30 new tests covering PCG, TokenBudget, SignalDispatcher, EvidenceChain

---

### Phase 1: Hierarchical Experience + IntraSession Contrast (~2 days)

**Goal**: Implement dual-layer experience recording and intra-session A/B testing for habit validation.

#### 1.1 SectionExperience + SessionExperience(V3)

**Existing file modification**: `core/memory.py` (MemoryState, L117-131)

Extend `MemoryState` with V3 experience fields:

```python
# memory.py MemoryState 扩展 (L117-131)
@dataclass
class MemoryState:
    sessions: list[dict] = field(default_factory=list)
    domain_patterns: list[dict] = field(default_factory=list)
    procedural_patterns: list[dict] = field(default_factory=list)
    version: str = "1.1"  # existing

    # === V3 NEW (version bump to "3.0") ===
    section_experiences: list[dict] = field(default_factory=list)    # L0: window 500
    session_experiences_v3: list[dict] = field(default_factory=list) # L1: window 100
    evolution_records: list[dict] = field(default_factory=list)      # L2: permanent
    contrast_results: list[dict] = field(default_factory=list)       # IntraSession results
    maturity_levels: dict = field(default_factory=dict)              # paper_type -> float
```

**Existing file modification**: `core/memory.py` (MemoryStore class, L138-516)

Add new persistence methods after `get_relevant_procedures()` (L322-339):

```python
# memory.py MemoryStore 新增方法
class MemoryStore:
    MAX_SECTION_EXPERIENCES = 500  # L0
    MAX_SESSION_EXPERIENCES_V3 = 100  # L1

    def persist_section_experience(self, exp: dict) -> None:
        """Store L0 section-level experience, maintain sliding window."""
        self.state.section_experiences.append(exp)
        if len(self.state.section_experiences) > self.MAX_SECTION_EXPERIENCES:
            self.state.section_experiences = self.state.section_experiences[-self.MAX_SECTION_EXPERIENCES:]

    def persist_session_experience_v3(self, exp: dict) -> None:
        """Store L1 session-level experience (V3 enhanced), maintain sliding window."""
        self.state.session_experiences_v3.append(exp)
        if len(self.state.session_experiences_v3) > self.MAX_SESSION_EXPERIENCES_V3:
            self.state.session_experiences_v3 = self.state.session_experiences_v3[-self.MAX_SESSION_EXPERIENCES_V3:]

    def persist_evolution_record(self, record: dict) -> None:
        """Store L2 evolution record. Permanent (no window)."""
        self.state.evolution_records.append(record)

    def persist_contrast_result(self, result: dict) -> None:
        """Store IntraSession contrast result."""
        self.state.contrast_results.append(result)

    def get_section_experiences_for_habit(self, habit_id: str) -> tuple[list[dict], list[dict]]:
        """Get section experiences split by whether habit was active.

        Returns: (with_habit, without_habit)
        """
        with_h = [e for e in self.state.section_experiences
                  if habit_id in e.get("active_habit_ids", [])]
        without_h = [e for e in self.state.section_experiences
                     if habit_id not in e.get("active_habit_ids", [])]
        return with_h, without_h

    def get_historical_baseline(self) -> dict[str, float]:
        """Compute per-paper_type avg findings_per_1k_tokens baseline.

        Used by compute_relative_effectiveness() in §4.6.
        """
        from collections import defaultdict
        totals = defaultdict(lambda: {"findings": 0, "tokens": 0})
        for exp in self.state.session_experiences_v3:
            pt = exp.get("paper_type", "unknown")
            totals[pt]["findings"] += exp.get("findings_count", 0)
            totals[pt]["tokens"] += exp.get("total_tokens", 1)
        return {
            pt: data["findings"] / max(data["tokens"] / 1000, 0.1)
            for pt, data in totals.items()
        }
```

#### 1.2 IntraSessionContrastManager

**Existing file modification**: `core/evolution.py` (EvolutionEngine, L494-642)

Add `IntraSessionContrastManager` as inner component of `EvolutionEngine`:

```python
# evolution.py EvolutionEngine 扩展 (L532-553: initialize 方法)
class EvolutionEngine:
    CONTRAST_PROBABILITY = 0.0  # V3: disable V2 random contrast (backward compat)

    def __init__(self, memory: MemoryStore, ablation: AblationConfig | None = None):
        # ... existing init ...
        self._intra_contrast_manager = IntraSessionContrastManager()  # V3 NEW
        self._current_contrast_plan: dict | None = None  # V3 NEW

    def initialize(self, existing_habit_ids: set[str] | None = None) -> None:
        """V3 enhanced: adds IntraSession contrast planning."""
        # existing: self.learned_habits = self._habit_learner.learn()
        self.learned_habits = self._habit_learner.learn()

        # === V3 NEW: Plan IntraSession contrast ===
        if GODEL_INTRA_CONTRAST_ENABLED and self.learned_habits:
            sections = self._get_paper_sections()  # from memory/state
            self._current_contrast_plan = self._intra_contrast_manager.plan_contrast(
                sections=sections, habits=self.learned_habits
            )

        # V2 compat: random cross-session contrast (default OFF)
        self.is_contrast_session = (
            GODEL_V2_CONTRAST_ENABLED and self._should_do_contrast()
        )

    def get_contrast_plan(self) -> dict | None:
        """Return current session's contrast plan for Assembler/Finalizer."""
        return self._current_contrast_plan
```

**New class in `core/evolution.py`** (or new file `core/intra_contrast.py`):

Implementation as defined in §4.3.2 (`IntraSessionContrastManager`).

#### 1.3 Dual-Layer Experience Recording

**Existing file modification**: `core/session_finalizer.py` (end_session, L31-48)

Add dual-layer recording steps after existing step 7:

```python
# session_finalizer.py end_session() 末尾新增
def end_session(state, memory, paper_id, strategy_transitions, ...):
    # ... existing steps 1-7 (L50-100) ...

    # 8. 【V3 NEW】Record section-level experiences (L0)
    if GODEL_SECTION_EXPERIENCE_ENABLED:
        _record_section_experiences(state=state, memory=memory)

    # 9. 【V3 NEW】Record V3 session experience (L1)
    if GODEL_SECTION_EXPERIENCE_ENABLED:
        _record_session_experience_v3(
            state=state, memory=memory,
            paper_type=_infer_paper_type(state),
            contrast_plan=_get_contrast_plan(state),
        )

    # 10. 【V3 NEW】Analyze IntraSession contrast (if plan exists)
    if GODEL_INTRA_CONTRAST_ENABLED:
        _analyze_and_persist_contrast(state=state, memory=memory)

    # 11. Save (was step 7, moved to end)
    memory.save()


def _record_section_experiences(state: WorkspaceState, memory: MemoryStore) -> None:
    """Build SectionExperience for each read section."""
    for section_name in state.sections_read:
        exp = {
            "session_id": state.session_id,
            "section_name": section_name,
            "paper_type": _infer_paper_type(state),
            "turns_spent": _count_turns_in_section(state, section_name),
            "findings_produced": _count_findings_in_section(state, section_name),
            "evidence_chains_built": _count_chains_in_section(state, section_name),
            "hypotheses_generated": _count_hypotheses_in_section(state, section_name),
            "active_habit_ids": _get_active_habits_for_section(state, section_name),
            "tokens_consumed": _estimate_tokens_for_section(state, section_name),
            "findings_per_token": 0.0,  # computed below
        }
        tokens = max(exp["tokens_consumed"], 1)
        exp["findings_per_token"] = exp["findings_produced"] / (tokens / 1000)
        memory.persist_section_experience(exp)
```

#### 1.4 Storage Format

```json
// memory.json V3 structure (version "3.0")
{
  "version": "3.0",
  "sessions": [...],
  "domain_patterns": [...],
  "procedural_patterns": [...],

  "section_experiences": [
    {
      "session_id": "sess_20250601_001",
      "section_name": "Methods",
      "paper_type": "DID",
      "turns_spent": 4,
      "findings_produced": 2,
      "evidence_chains_built": 1,
      "hypotheses_generated": 1,
      "active_habit_ids": ["learned_001", "learned_003"],
      "tokens_consumed": 8500,
      "findings_per_token": 0.235
    }
  ],

  "session_experiences_v3": [
    {
      "session_id": "sess_20250601_001",
      "timestamp": "2025-06-01T14:30:00",
      "paper_type": "DID",
      "total_turns": 35,
      "findings_count": 8,
      "high_priority_count": 3,
      "evidence_ratio": 0.75,
      "total_tokens": 95000,
      "findings_per_1k_tokens": 0.084,
      "phase_a_sections": ["Introduction", "Methods", "Results"],
      "phase_b_sections": ["Discussion", "Robustness", "Conclusion"],
      "phase_a_habits": ["learned_001", "learned_003"],
      "phase_b_habits": ["learned_001"],
      "phase_a_findings_density": 0.67,
      "phase_b_findings_density": 0.33,
      "pcg_coverage": 0.85,
      "evidence_chains_total": 5,
      "avg_chain_length": 3.2
    }
  ],

  "contrast_results": [
    {
      "session_id": "sess_20250601_001",
      "target_habit_id": "learned_003",
      "phase_a_findings_density": 0.67,
      "phase_b_findings_density": 0.33,
      "delta": 0.34,
      "statistical_note": "N_a=3, N_b=3",
      "recommendation": "reinforce"
    }
  ],

  "evolution_records": [],
  "maturity_levels": {}
}
```

#### 1.5 Verification Checklist

- [ ] 10 sessions 后 `section_experiences` 累积 100+ 条（avg 10+ sections/paper）
- [ ] `session_experiences_v3` 累积 10 条，包含 phase_a/phase_b 字段
- [ ] IntraSession contrast 自动激活（papers with 15+ sections）
- [ ] contrast_results 产出 delta 值和 recommendation
- [ ] Sliding window: `section_experiences` max 500, `session_experiences_v3` max 100
- [ ] `get_historical_baseline()` 正确计算各 paper_type 的 findings/1k_tokens
- [ ] V2 backward compat: `SCHOLAR_GODEL_V2_CONTRAST=1` 可重启旧模式
- [ ] Kill switch: `SCHOLAR_GODEL_INTRA_CONTRAST=0` → no phase split
- [ ] Kill switch: `SCHOLAR_GODEL_SECTION_EXP=0` → no L0 recording
- [ ] All existing 710 tests pass
- [ ] >= 20 new tests covering experience recording, contrast planning, analysis

---

### Phase 2: Tri-Frequency MetaReflector (~2 days)

**Goal**: Implement three-tier metacognitive reflection with different trigger conditions and LLM costs.

#### 2.1 FastReflector + EmergencyReflector

**Existing file modification**: `core/meta_reflect.py` (if exists from V2; otherwise new file)

Add `FastReflector` and `EmergencyReflector` as defined in §4.4.1 and §4.4.3.

```python
# core/meta_reflect.py 新增类

class FastReflector:
    """Every 3 sessions, pure rules, zero LLM.

    Integration point: called in session_finalizer.end_session_with_reflection()
    after SessionReflector.reflect() completes.
    """
    TRIGGER_INTERVAL = 3

    def should_trigger(self, memory_store: MemoryStore) -> bool:
        """Check if fast reflect should run."""
        exps = memory_store.state.session_experiences_v3
        last_fast = getattr(memory_store.state, "_last_fast_reflect_count", 0)
        return len(exps) - last_fast >= self.TRIGGER_INTERVAL

    def analyze(self, memory_store: MemoryStore) -> list[str]:
        """Pure rule analysis. Returns alert messages."""
        # Implementation as §4.4.1
        ...

    def apply(self, alerts: list[str], memory_store: MemoryStore) -> None:
        """Persist alerts as meta_note for next session."""
        if alerts:
            # Inject into next session's system prompt via memory context
            memory_store.state.fast_reflect_alerts = alerts[-3:]  # keep last 3
        memory_store.state._last_fast_reflect_count = len(memory_store.state.session_experiences_v3)


class EmergencyReflector:
    """Realtime trigger within session. Zero LLM.

    Integration point: called in session_finalizer.end_session() BEFORE
    SessionReflector, so that emergency can reduce habit confidence immediately.
    """

    def check(self, state: WorkspaceState) -> dict | None:
        """Check if emergency conditions met."""
        # Implementation as §4.4.3
        ...

    def apply_emergency(self, result: dict, memory_store: MemoryStore,
                        learned_habits: list) -> None:
        """Immediate confidence reduction for suspect habits."""
        for habit_id in result.get("suspect_habits", [])[:1]:  # max 1
            for h in learned_habits:
                if h.id == habit_id:
                    h.confidence = max(0.0, h.confidence - 0.1)
                    break
```

#### 2.2 DeepReflector (V2 MetaReflector Enhanced)

**Existing file**: `core/meta_reflect.py` (V2 MetaReflector, L473+)

Extend with V3 data sources:

```python
# core/meta_reflect.py MetaReflector 子类化
class DeepReflector(MetaReflector):
    """V3 enhanced deep reflection. Inherits V2 MetaReflector fully.

    Differences from V2:
    1. precompute_context_v3() adds IntraSession contrast evidence
    2. should_trigger_v3() adds anomaly-based triggers
    3. apply_decisions_v3() updates L2 evolution_records
    """

    def should_trigger_v3(self, memory_store: MemoryStore) -> bool:
        """V3 trigger conditions (superset of V2).

        Trigger if:
        - V2 condition (every 10 sessions) OR
        - FastReflector reported anomalies 2 consecutive times OR
        - maturity sudden change (> 0.2 within 3 sessions)
        """
        if super().should_trigger(memory_store):
            return True
        alerts = getattr(memory_store.state, "fast_reflect_alerts", [])
        return len(alerts) >= 2  # consecutive anomaly signals

    def precompute_context_v3(self, memory_store: MemoryStore,
                              learned_habits: list) -> str:
        """V3 enhanced context with contrast evidence + section efficiency."""
        base = super().precompute_context(memory_store, learned_habits)

        # Add IntraSession contrast results
        contrast_results = memory_store.state.contrast_results[-10:]
        contrast_text = self._format_contrast_evidence(contrast_results)

        # Add section-level efficiency analysis
        section_exps = memory_store.state.section_experiences[-100:]
        efficiency_text = self._format_section_efficiency(section_exps, learned_habits)

        return (base +
                f"\n\n## IntraSession Contrast Evidence (V3)\n{contrast_text}"
                f"\n\n## Section-Level Efficiency Analysis (V3)\n{efficiency_text}")

    def apply_decisions_v3(self, result, memory_store: MemoryStore,
                           learned_habits: list) -> dict:
        """V3: also persist L2 EvolutionRecord."""
        # V2 logic: apply confidence changes
        report = super().apply_decisions(result, memory_store, learned_habits)

        # V3 NEW: persist L2 evolution record
        evolution_record = {
            "timestamp": _now_iso(),
            "trigger_type": "deep",
            "habit_decisions": [asdict(d) for d in result.habit_decisions],
            "maturity_updates": [asdict(m) for m in result.maturity_updates],
            "contrast_evidence": memory_store.state.contrast_results[-5:],
            "meta_note": result.meta_note,
            "token_efficiency_trend": self._compute_trend(memory_store),
        }
        memory_store.persist_evolution_record(evolution_record)

        return report
```

#### 2.3 Tri-Frequency Integration in session_finalizer

**Existing file modification**: `core/session_finalizer.py` (end_session_with_reflection, L129-182)

```python
# session_finalizer.py end_session_with_reflection() 扩展
async def end_session_with_reflection(state, memory, ..., llm_call_fn=None, ...):
    # 1. Call sync end_session (existing, L156-163)
    end_session(state, memory, paper_id, strategy_transitions, ...)

    # 2. SessionReflector (existing, L165-175)
    reflector = SessionReflector(llm_call_fn)
    results = await reflector.reflect(state)
    reflector.persist_reflections(results, memory)

    # === V3 NEW: Tri-frequency MetaReflector integration ===

    # 3. Emergency check (zero LLM, runs every session)
    if GODEL_EMERGENCY_REFLECT_ENABLED:
        emergency = EmergencyReflector()
        emergency_result = emergency.check(state)
        if emergency_result:
            emergency.apply_emergency(emergency_result, memory, _get_learned_habits(state))
            logger.info(f"Emergency reflect triggered: {emergency_result['reason']}")

    # 4. Fast reflect (zero LLM, every 3 sessions)
    if GODEL_FAST_REFLECT_ENABLED:
        fast = FastReflector()
        if fast.should_trigger(memory):
            alerts = fast.analyze(memory)
            fast.apply(alerts, memory)
            logger.info(f"Fast reflect: {len(alerts)} alerts")

    # 5. Deep reflect (LLM call, every 10 sessions or anomaly)
    if GODEL_DEEP_REFLECT_ENABLED and llm_call_fn:
        deep = DeepReflector(llm_call_fn)
        if deep.should_trigger_v3(memory):
            context = deep.precompute_context_v3(memory, _get_learned_habits(state))
            meta_result = await deep.reflect(context)
            if meta_result:
                report = deep.apply_decisions_v3(meta_result, memory, _get_learned_habits(state))
                logger.info(f"Deep reflect: {report}")

    # 6. Final save
    memory.save()
```

#### 2.4 Verification Checklist

- [ ] `FastReflector` triggers after exactly 3 sessions, not before
- [ ] `FastReflector` detects declining trend (3 consecutive sessions findings_density decreasing)
- [ ] `EmergencyReflector` triggers when idle_before_exit > 10 AND findings < 2
- [ ] `EmergencyReflector` confidence reduction max 0.1 per trigger
- [ ] `DeepReflector.should_trigger_v3()` fires on V2 conditions OR anomaly
- [ ] `DeepReflector.precompute_context_v3()` includes contrast evidence
- [ ] `DeepReflector.apply_decisions_v3()` persists L2 evolution_record
- [ ] Graceful degradation: all reflectors return None on failure, don't crash session
- [ ] Kill switch: `SCHOLAR_GODEL_FAST_REFLECT=0` → skip fast
- [ ] Kill switch: `SCHOLAR_GODEL_EMERGENCY=0` → skip emergency
- [ ] Kill switch: `SCHOLAR_GODEL_DEEP_REFLECT=0` → skip deep (V2 MetaReflector also disabled)
- [ ] All existing 710 tests pass
- [ ] >= 25 new tests covering trigger conditions, analysis, application, graceful failure

---

### Phase 3: Habit Evolution + Closed Loop (~1.5 days)

**Goal**: Complete the evolution closed loop with enhanced habit learning and hypothesis unification.

#### 3.1 HabitLearner Enhanced

**Existing file modification**: `core/evolution.py` (HabitLearner, L59-248)

Enhance `_select_mature_patterns()` (L133-154) to incorporate IntraSession evidence:

```python
# evolution.py HabitLearner._select_mature_patterns() 增强
def _select_mature_patterns(self) -> list:
    """V3 enhanced: filter by both evidence_count AND IntraSession contrast results."""
    patterns = self._memory.state.procedural_patterns

    # Existing filter: evidence >= 3, effectiveness >= 0.6
    candidates = [
        p for p in patterns
        if p.get("evidence_count", 0) >= 3
        and p.get("effectiveness_score", 0) >= 0.6
    ]

    # === V3 NEW: Cross-reference with contrast results ===
    if GODEL_INTRA_CONTRAST_ENABLED:
        contrast_results = self._memory.state.contrast_results
        # Boost patterns whose associated habits have positive contrast delta
        for pattern in candidates:
            related_contrast = [
                r for r in contrast_results
                if r.get("target_habit_id") in pattern.get("source_habit_ids", [])
            ]
            if related_contrast:
                avg_delta = sum(r["delta"] for r in related_contrast) / len(related_contrast)
                # Inject contrast signal into effectiveness
                pattern["_contrast_boost"] = avg_delta

    # Sort by effectiveness + contrast boost
    candidates.sort(key=lambda p: (
        p.get("effectiveness_score", 0) + p.get("_contrast_boost", 0)
    ), reverse=True)

    return candidates
```

**Existing file modification**: `core/evolution.py` (HabitLearner._pattern_to_habit, L156-199)

Use `compute_relative_effectiveness()` instead of absolute scoring:

```python
# evolution.py HabitLearner._pattern_to_habit() 修改
def _pattern_to_habit(self, pattern) -> LearnedHabit | None:
    # ... existing logic ...

    # === V3: Initial confidence from relative effectiveness ===
    baseline = self._memory.get_historical_baseline()
    initial_confidence = compute_relative_effectiveness(
        findings_count=pattern.get("_avg_findings", 5),
        tokens_consumed=pattern.get("_avg_tokens", 50000),
        sections_covered=pattern.get("_avg_sections", 10),
        paper_type=pattern.get("paper_type", "unknown"),
        historical_baseline=baseline,
    )
    # Clamp initial confidence to [0.3, 0.7] (new habits start uncertain)
    initial_confidence = max(0.3, min(0.7, initial_confidence))

    return LearnedHabit(
        id=f"learned_{self._next_id():03d}",
        name=pattern["description"][:60],
        phases=["DEEP_REVIEW"],
        priority=int(pattern.get("effectiveness_score", 0.5) * 100),
        content=pattern["description"],
        source_patterns=[pattern.get("category", "")],
        confidence=initial_confidence,  # V3: relative, not absolute
        generation=0,
    )
```

#### 3.2 Habit Combination Effect Tracking

**Existing file modification**: `core/habits.py` (HabitSelector)

Track which habit combinations were active during high-quality sections:

```python
# habits.py HabitSelector 新增方法
class HabitSelector:
    def record_combination_effectiveness(
        self, active_habit_ids: list[str], section_findings_density: float
    ) -> None:
        """Track combination effectiveness for future analysis.

        Called per-section by session_finalizer.
        Stored in memory for DeepReflector pattern analysis.
        """
        if not hasattr(self, "_combination_log"):
            self._combination_log = []
        self._combination_log.append({
            "combination": sorted(active_habit_ids),
            "density": section_findings_density,
        })

    def get_combination_insights(self) -> list[dict]:
        """Analyze: do certain combinations outperform individual habits?"""
        from collections import defaultdict
        combo_stats = defaultdict(list)
        for entry in getattr(self, "_combination_log", []):
            key = tuple(entry["combination"])
            combo_stats[key].append(entry["density"])
        return [
            {"combination": list(k), "avg_density": sum(v)/len(v), "n": len(v)}
            for k, v in combo_stats.items() if len(v) >= 3
        ]
```

#### 3.3 Hypothesis Dual-Module Unification

**Existing file modification**: `core/metacognition.py` (CognitiveState, L51-62)

Replace `hypotheses` field with reference to HypothesisModule:

```python
# metacognition.py CognitiveState 修改 (L51-62)
@dataclass
class CognitiveState:
    current_strategy: Literal[...] = "initial_scan"
    strategy_rationale: str = ""
    # hypotheses: list[Hypothesis] = field(default_factory=list)  # V3: REMOVED
    open_questions: list[str] = field(default_factory=list)
    overall_confidence: float = 0.0
    assessment_note: str = ""
    last_updated_turn: int = 0

    # === V3 NEW: Reference instead of copy ===
    _hypothesis_module_ref: Any = None  # Set by Harness after init

    def set_hypothesis_module(self, module: "HypothesisModule") -> None:
        """Called by Harness to establish reference."""
        self._hypothesis_module_ref = module
```

**Existing file modification**: `core/metacognition.py` (format_for_context, L88-146)

Modify hypothesis section to project from HypothesisModule:

```python
# metacognition.py format_for_context() 修改 (L88-146)
def format_for_context(self) -> str:
    parts = []
    # ... existing strategy + confidence formatting ...

    # === V3: Hypothesis projected from HypothesisModule (not local copy) ===
    if self._hypothesis_module_ref is not None:
        hyp_status = self._hypothesis_module_ref.format_status()
        if hyp_status:
            parts.append(hyp_status)
    # Fallback for backward compat (if ref not set)
    elif hasattr(self, "hypotheses") and self.hypotheses:
        for h in self.hypotheses[:3]:
            parts.append(f"  H: {h.claim} [{h.status}]")

    # ... existing open_questions + assessment_note ...
    return "\n".join(parts)
```

**Existing file modification**: `core/harness.py` (__init__, L184 area)

After CognitiveState and HypothesisModule both initialized, link them:

```python
# harness.py __init__ 中，L204 之后（HD-WM 初始化完成后）
class Harness:
    def __init__(self, ...):
        # L184: self.cognitive_state = CognitiveState()
        # L202-204: self.hypothesis_module = HypothesisModule()

        # === V3 NEW: Link hypothesis module to cognitive state ===
        self.cognitive_state.set_hypothesis_module(self.hypothesis_module)
```

#### 3.4 Closed Loop E2E Verification

**Full lifecycle test scenario**:

```python
# tests/test_godel_v3_e2e.py
async def test_full_evolution_lifecycle():
    """E2E: learn -> accumulate -> contrast -> reflect -> evolve.

    Steps:
    1. Run 3 sessions -> HabitLearner produces habits (confidence 0.5)
    2. Run 7 more sessions -> L0/L1 experiences accumulate
    3. IntraSession contrast produces delta values per session
    4. After session 10 -> DeepReflector fires
    5. DeepReflector sees contrast evidence -> reinforce/doubt habits
    6. Reinforced habit confidence rises to 0.6
    7. Doubted habit confidence drops to 0.35
    8. After 3 more sessions -> FastReflector detects trend
    9. After session 20 -> DeepReflector fires again
    10. Habit with confidence < 0.3 -> abandoned

    Verification:
    - L2 evolution_records has 2 entries
    - Abandoned habit no longer in get_habits_for_selector()
    - Maturity levels updated for paper_type
    """
```

#### 3.5 Verification Checklist

- [ ] `HabitLearner` uses `compute_relative_effectiveness()` for initial confidence
- [ ] New habits start with confidence in [0.3, 0.7], never 1.0
- [ ] IntraSession contrast delta influences habit confidence via DeepReflector
- [ ] `CognitiveState` no longer duplicates hypotheses (projects from HypothesisModule)
- [ ] `format_for_context()` output unchanged (backward compat)
- [ ] `update_from_reflection()` correctly forwards to HypothesisModule
- [ ] Combination tracking records >= 3 observations before producing insights
- [ ] E2E lifecycle: learn → accumulate → contrast → reflect → evolve → abandon
- [ ] Abandoned habit: confidence set to 0, filtered from `get_habits_for_selector()`
- [ ] Cooldown: abandoned habit not re-evaluated for 12 sessions (Constitutional C6)
- [ ] All existing 710 tests pass (especially hypothesis-related tests)
- [ ] >= 15 new tests covering E2E lifecycle + combination tracking + unification

---

## 6. Module Relationship Map (with Source Line References)

```
session_finalizer.py (L31-182)
├── end_session() [L31]
│   ├── build_session_record()              [memory.py L549, 不变]
│   ├── extract_domain_patterns()           [memory.py L778, 不变]
│   ├── extract_procedural_patterns()       [memory.py L623, 不变]
│   ├── persist_cognitive_hints()           [cognition_graph.py L361, 不变]
│   ├── record_review_stats()              [不变]
│   ├── _record_section_experiences()       [V3 NEW Phase 1]
│   ├── _record_session_experience_v3()     [V3 NEW Phase 1]
│   └── _analyze_and_persist_contrast()     [V3 NEW Phase 1]
│
└── end_session_with_reflection() [L129]
    ├── SessionReflector.reflect()          [reflection.py L153, 不变]
    ├── EmergencyReflector.check()          [V3 NEW Phase 2]
    ├── FastReflector.analyze()             [V3 NEW Phase 2]
    └── DeepReflector.reflect()             [V3 NEW Phase 2, inherits V2 MetaReflector]

harness.py (L139-704)
├── __init__() [L151]
│   ├── WorkspaceState                     [state.py L51, V3: +pcg +evidence_chains fields]
│   ├── MemoryStore                        [memory.py L138, V3: +L0/L1/L2 methods]
│   ├── CognitiveState                     [metacognition.py L51, V3: +_hypothesis_module_ref]
│   ├── HypothesisModule                   [hypothesis.py L132, 不变 (SoT)]
│   ├── CognitiveState.set_hypothesis_module()  [V3 NEW Phase 3]
│   ├── CompactionEngine                   [compaction.py L248, V3: +PCG snapshot]
│   ├── EvolutionEngine                    [evolution.py L494, V3: +IntraContrast]
│   ├── TokenBudgetManager                 [V3 NEW Phase 0.5]
│   ├── SignalDispatcher                   [V3 NEW Phase 0.5]
│   ├── EvidenceChainTracker               [V3 NEW Phase 0.5]
│   └── ContextAssembler                   [assembler.py L368, V3: +pcg_navigation section]
│
├── load_paper() [L247]
│   ├── PaperIndexBuilder.build()          [paper_index.py L214, 不变]
│   └── PaperCognitionGraph.from_structure_index()  [V3 NEW Phase 0.5]
│
├── execute_tool() [L401]
│   └── EvidenceChainTracker.add_step_to_recent()   [V3 NEW Phase 0.5]
│
└── check_xxx() methods [L574-606]
    └── → SignalDispatcher.submit()         [V3 Phase 0.5, loop.py 调用]

loop.py (L73-728)
├── cognitive_loop() [L73]
│   ├── L101: check_doom_loop()            [不变, priority 0 直接 return]
│   ├── L108-130: signal injection zone    [V3: → SignalDispatcher pattern]
│   ├── L136: hypothesis_module.tick()     [不变]
│   ├── L150-159: format_context()         [不变, 委托 assembler]
│   └── L171: _filter_tools_by_phase()     [不变]
│
└── Zero modification to sub-perspective logic (L486-728)

assembler.py (L368-598)
├── _register_default_sections() [L404-538]
│   ├── priority 100: static_identity       [SESSION cache, 不变]
│   ├── priority 95:  cognitive_habits       [PHASE cache, 不变]
│   ├── priority 90:  paper_overview         [不变]
│   ├── priority 89:  pcg_navigation         [V3 NEW]
│   ├── priority 88:  paper_structure        [V3: uses PCG.format_for_zone_a()]
│   ├── priority 86:  cognitive_hints        [不变]
│   ├── priority 85:  findings               [不变]
│   ├── priority 82:  hypothesis_status      [不变]
│   ├── priority 80:  references             [不变]
│   ├── priority 75:  section_digests        [不变]
│   ├── priority 70:  metacognition          [不变]
│   ├── priority 65:  memory                 [不变]
│   ├── priority 60:  offload_refs           [不变]
│   ├── priority 55:  edits                  [不变]
│   ├── priority 52:  evolution_context      [不变]
│   └── priority 50:  resource_status        [不变]
│
└── assemble() [L540] — 不变, section 系统自动 pick up 新注册 section

evolution.py (L494-642)
├── EvolutionEngine [L494]
│   ├── .initialize() [L532]
│   │   ├── HabitLearner.learn()            [V3: enhanced _select_mature_patterns]
│   │   └── IntraSessionContrastManager.plan_contrast()  [V3 NEW Phase 1]
│   ├── .get_habits_for_selector() [L555]   [V3: filters confidence<0.3]
│   └── .get_contrast_plan()                [V3 NEW Phase 1]
│
├── HabitLearner [L59-248]
│   ├── ._select_mature_patterns() [L133]   [V3: +contrast_boost]
│   └── ._pattern_to_habit() [L156]         [V3: relative effectiveness]
│
└── IntraSessionContrastManager             [V3 NEW Phase 1]
    ├── .plan_contrast()
    └── .analyze_contrast()

memory.py (L138-516)
├── MemoryState [L117-131]
│   ├── sessions, domain_patterns, procedural_patterns  [不变]
│   ├── section_experiences                  [V3 NEW: L0, window 500]
│   ├── session_experiences_v3               [V3 NEW: L1, window 100]
│   ├── evolution_records                    [V3 NEW: L2, permanent]
│   ├── contrast_results                     [V3 NEW]
│   └── maturity_levels                      [V3 NEW]
│
└── MemoryStore [L138]
    ├── persist_section_experience()          [V3 NEW Phase 1]
    ├── persist_session_experience_v3()       [V3 NEW Phase 1]
    ├── persist_evolution_record()            [V3 NEW Phase 2]
    ├── persist_contrast_result()             [V3 NEW Phase 1]
    ├── get_section_experiences_for_habit()   [V3 NEW Phase 1]
    ├── get_historical_baseline()             [V3 NEW Phase 3]
    └── gc_procedures() [L345]               [Phase 0 ✅, 不变]

meta_reflect.py (V2→V3 restructured)
├── MetaReflector [V2, L473+]               [不变, DeepReflector 的父类]
├── FastReflector                            [V3 NEW Phase 2, zero LLM]
├── DeepReflector(MetaReflector)             [V3 NEW Phase 2, inherits V2]
└── EmergencyReflector                       [V3 NEW Phase 2, zero LLM]

compaction.py (L248-463)
├── WorkspaceSnapshot [L100]
│   ├── existing fields [L112-135]           [不变]
│   ├── pcg_snapshot                         [V3 NEW Phase 0.5]
│   ├── cognitive_state_snapshot             [V3 NEW Phase 0.5]
│   └── evidence_chain_refs                  [V3 NEW Phase 0.5]
│
└── CompactionEngine [L248]
    └── .build_snapshot() [L294]             [V3: includes PCG serialization]

NEW FILES (V3):
├── core/paper_cognition_graph.py            [Phase 0.5] ~200 lines
├── core/token_budget.py                     [Phase 0.5] ~120 lines
├── core/signal_dispatcher.py                [Phase 0.5] ~80 lines
├── core/evidence_chain.py                   [Phase 0.5] ~150 lines
└── core/intra_contrast.py (or in evolution.py) [Phase 1] ~100 lines

ZERO-CHANGE FILES:
  agent.py, identity.py, identity_static.py, phases.py, tools.py,
  tool_handlers/*.py (except evidence tracking hooks),
  llm/*.py, reflection.py (SessionReflector 不变)
```

### 6.1 Data Flow: Single Review Session

```
Paper Load:
  harness.load_paper()
    → PaperIndexBuilder.build()           [existing, <1s]
    → PaperCognitionGraph.from_structure_index()  [V3, <0.5s]
    → TokenBudgetManager.pcg = pcg

Session Start:
  EvolutionEngine.initialize()
    → HabitLearner.learn()                [existing + V3 enhancement]
    → IntraSessionContrastManager.plan_contrast()  [V3]
    → HabitSelector.extend_with_learned() [existing]

Each Turn (loop.py):
  1. SignalDispatcher.dispatch()           [V3, replaces stacked checks]
  2. Assembler.assemble()                 [uses PCG.format_for_zone_a()]
  3. TokenBudgetManager.compute_allocation() [V3, per-turn Zone B]
  4. Agent LLM call                       [unchanged]
  5. execute_tool()                       [+ EvidenceChainTracker steps]
  6. PCG.update_after_read()              [V3, on read_section]

Session End:
  session_finalizer.end_session()
    → [existing steps 1-7]
    → _record_section_experiences()        [V3: L0 per section]
    → _record_session_experience_v3()      [V3: L1 per session]
    → _analyze_and_persist_contrast()      [V3: delta + recommendation]

  session_finalizer.end_session_with_reflection()
    → SessionReflector.reflect()           [existing]
    → EmergencyReflector.check()           [V3: zero LLM]
    → FastReflector.analyze()              [V3: every 3, zero LLM]
    → DeepReflector.reflect()              [V3: every 10, LLM call]
    → memory.save()
```

---

## 7. Cost Estimate

| Phase | New Code | New Tests | LLM Runtime Cost |
|-------|----------|-----------|------------------|
| Phase 0.5 | ~600 lines | ~30 | Zero (pure data + dispatch) |
| Phase 1 | ~300 lines | ~20 | Zero (pure data recording) |
| Phase 2 | ~400 lines | ~25 | DeepReflect: 1x per 10 sessions (~1500 tokens) |
| Phase 3 | ~200 lines | ~15 | Zero (pure logic) |
| **Total** | **~1500 lines** | **~90** | **~150 tokens/session average increase** |

---

## 8. Risks and Mitigations

| Risk | Prob | Impact | Mitigation |
|------|------|--------|------------|
| PCG build errors (incomplete regex) | Med | Med | Inherits mature PaperStructureIndex regex; PCG is enhancement layer, fails gracefully to V2 |
| IntraSession contrast unfair splits | Low | Low | Random split + multiple contrasts averaged; constitutional requires N>=3 |
| SignalDispatcher over-suppresses | Low | High | Priority 0 never suppressed; doom_loop_guard independent |
| Tri-frequency MetaReflector conflicts | Low | Med | Fast only flags; Deep decides; Emergency limits confidence change <= 0.1 |
| EvidenceChain token bloat | Med | Med | Chain offloaded on finalize; only summary in context |
| Hypothesis unification regression | Low | Med | CognitiveState preserves existing interface |

---

## 9. Environment Variable Kill Switches

**File location**: `core/godel_config.py` (new file, imported by all V3 modules)

```python
"""
core/godel_config.py — V3 Godel Layer feature flags.

All V3 features controlled via environment variables.
Any flag set to "0" disables the corresponding feature silently.
All OFF = system degrades to Phase 0 baseline (gc_procedures only).

Usage in other modules:
    from core.godel_config import GODEL_PCG_ENABLED, ...
"""
import os

# Phase 0.5: Paper Cognition Infrastructure
GODEL_PCG_ENABLED = os.getenv("SCHOLAR_GODEL_PCG", "1") == "1"
GODEL_BUDGET_MANAGER_ENABLED = os.getenv("SCHOLAR_GODEL_BUDGET", "1") == "1"
GODEL_SIGNAL_DISPATCHER_ENABLED = os.getenv("SCHOLAR_GODEL_DISPATCHER", "1") == "1"
GODEL_EVIDENCE_CHAIN_ENABLED = os.getenv("SCHOLAR_GODEL_EVIDENCE_CHAIN", "1") == "1"

# Phase 1: Experience + Contrast
GODEL_SECTION_EXPERIENCE_ENABLED = os.getenv("SCHOLAR_GODEL_SECTION_EXP", "1") == "1"
GODEL_INTRA_CONTRAST_ENABLED = os.getenv("SCHOLAR_GODEL_INTRA_CONTRAST", "1") == "1"

# Phase 2: Tri-Frequency MetaReflector
GODEL_FAST_REFLECT_ENABLED = os.getenv("SCHOLAR_GODEL_FAST_REFLECT", "1") == "1"
GODEL_DEEP_REFLECT_ENABLED = os.getenv("SCHOLAR_GODEL_DEEP_REFLECT", "1") == "1"
GODEL_EMERGENCY_REFLECT_ENABLED = os.getenv("SCHOLAR_GODEL_EMERGENCY", "1") == "1"

# Backward compat: V2 random contrast (default OFF in V3)
GODEL_V2_CONTRAST_ENABLED = os.getenv("SCHOLAR_GODEL_V2_CONTRAST", "0") == "1"
```

**Degradation behavior**:

| All OFF | System behavior |
|---------|----------------|
| PCG=0 | `assembler.py` uses `PaperStructureIndex.format_for_context()` (original) |
| DISPATCHER=0 | `loop.py` uses original stacked `if` checks (L101-130) |
| EVIDENCE_CHAIN=0 | `execute_tool()` skips tracking; findings still work |
| SECTION_EXP=0 | `end_session()` skips L0 recording; V2 `session_experiences` still work |
| INTRA_CONTRAST=0 | `EvolutionEngine.initialize()` skips contrast planning |
| All Phase 2=0 | `end_session_with_reflection()` only runs SessionReflector (V2 behavior) |

All switches are checked at call-site with `if FLAG:` guard. No import-time side effects.

---

## 10. Acceptance Criteria (Overall)

### Functional

1. **PCG Build**: Loading 50+ page paper auto-builds PCG with sections + edges + read_depth
2. **Token Budget**: Zone A stays <=8K with full PCG digest; Zone B loads dynamically via PCG
3. **Evidence Chains**: Every high-priority finding has >= 2 step EvidenceChain
4. **IntraSession Contrast**: Papers with 15+ sections auto-activate, produce delta values
5. **Tri-frequency**: Fast/3 sessions, Deep/10 sessions, Emergency/realtime
6. **Closed Loop**: habit learn -> L0+L1 accumulate -> contrast -> reflect -> evolve

### Engineering

1. **Zero regression**: All 710+ existing tests pass
2. **Kill Switches**: All new features independently disableable
3. **Graceful Degradation**: PCG build failure degrades to PaperStructureIndex
4. **Code style**: dataclass + type hints + docstrings, consistent with existing
5. **Performance**: PCG build < 2 sec; TokenBudgetManager allocation < 10ms

---

## 11. Timeline

```
Phase 0: COMPLETED (gc_procedures + 70 tests)
    |
Phase 0.5: Paper Cognition Infrastructure (~3 days)
    |-- 0.5.1 core/paper_cognition_graph.py + state.py 扩展 (0.5d)
    |-- 0.5.2 core/token_budget.py + assembler.py 集成 (0.5d)
    |-- 0.5.3 core/signal_dispatcher.py + loop.py 集成 (0.5d)
    |-- 0.5.4 core/evidence_chain.py + harness.py 追踪 hook (0.5d)
    +-- 0.5.5 compaction.py 扩展 + core/godel_config.py + 30 tests (1d)
    |  Verify: 30 tests pass + manual 1 session confirm PCG build + Zone B allocation
    |
Phase 1: Hierarchical Experience + IntraSession Contrast (~2 days)
    |-- 1.1 memory.py MemoryState V3 扩展 + 6 new methods (0.5d)
    |-- 1.2 evolution.py IntraSessionContrastManager (0.5d)
    |-- 1.3 session_finalizer.py dual-layer recording (0.5d)
    +-- 1.4 20 tests + manual 2 sessions confirm data + contrast (0.5d)
    |  Verify: memory.json 有 section_experiences + contrast_results
    |
Phase 2: Tri-Frequency MetaReflector (~2 days)
    |-- 2.1 meta_reflect.py FastReflector + EmergencyReflector (0.5d)
    |-- 2.2 meta_reflect.py DeepReflector(MetaReflector) (0.5d)
    |-- 2.3 session_finalizer.py tri-frequency integration (0.5d)
    +-- 2.4 25 tests + mock LLM full pipeline (0.5d)
    |  Verify: Fast triggers at 3 sessions, Deep at 10, Emergency on anomaly
    |
Phase 3: Habit Evolution + Closed Loop (~1.5 days)
    |-- 3.1 evolution.py HabitLearner enhance + relative scoring (0.5d)
    |-- 3.2 habits.py combination tracking (0.25d)
    |-- 3.3 metacognition.py hypothesis unification + harness.py link (0.25d)
    +-- 3.4 E2E lifecycle test + 15 tests (0.5d)
    |  Verify: learn -> accumulate -> contrast -> reflect -> evolve -> abandon
    |
Total: ~8.5 days, ~1500 new lines, ~90 new tests
```

---

## 12. V2 to V3 Differences Summary

| Dimension | V2 Final | V3 | Reason |
|-----------|---------|-----|--------|
| Cognitive infrastructure | None (relies on paper_index) | PCG + TokenBudget + SignalDispatcher + EvidenceChain | 50-70 page papers need graph cognition |
| Contrast method | 12% cross-session random | IntraSession section-level | Statistical validity + zero quality loss |
| Observation granularity | Session-level | Section + Session dual-layer | Long papers have enough intra-session data |
| Experience storage | 50 SessionExperience | L0(500) + L1(100) + L2(permanent) | Multi-granularity evidence |
| Metacognition frequency | Fixed 10 sessions | Tri-frequency adaptive | 3-5 weeks between reflections too slow |
| Traceability | None | EvidenceChain | Academic review needs evidence support |
| Hypothesis management | Two parallel systems | Single SoT + projection | Eliminate sync overhead |
| Signal injection | Stacked | Unified dispatch (max 2/turn) | Prevent attention dilution |
| Engineering effort | 580 lines / 4.5 days | 1500 lines / 8.5 days | Added infrastructure layer |

---

## 13. Future Outlook (Not in scope)

1. **PCG Semantic Enhancement**: Current edges from regex cross-refs. Future: LLM generates semantic logic edges during first full scan.
2. **Cross-Paper PCG Transfer**: Same paper types (e.g., DID) share PCG structure patterns. After accumulation, form "DID paper typical PCG template."
3. **Multi-Agent Distributed Review**: Assign different PCG subgraphs to different sub-Agents for parallel reading, merge findings at PCG level.
4. **User Feedback Loop**: User accept/reject of findings as ultimate EvidenceChain quality validation signal.
5. **Mermaid Visualization**: Real-time render PCG as Mermaid graph for user to visualize Agent's paper cognition progress.

---

## 14. Reference Sources

| Design Decision | Source |
|----------------|--------|
| PCG graph cognition | CodeGraph "pre-computed memory replaces real-time exploration" + TencentDB "layered memory" |
| Three-Zone Token Budget | TencentDB Context Offloading (61% savings) + Harness Engineering Token Pipeline |
| IntraSession Contrast | Agent-as-a-Judge structured eval + GPTSwarm edge probability validation |
| Unified Signal Dispatch | Anthropic "three-element minimalism" + Karpathy "precise constraints" |
| EvidenceChain | Harness Engineering R.E.S.T "T(Traceability)" |
| Tri-frequency adaptive | 17 Architectures "when to upgrade" + Self-Evolving Survey "when to evolve" |
| Hypothesis unification | State separation principle (Harness Engineering S4.5) |
| Relative efficiency scoring | Implementation-Notes "cognitive transparency" |
| Kill Switch progressive control | Simon Willison "Hoard things you know how to do" |

---

*Version: V3 Ideal State (Implementation-Ready) | Based on V2 Final + AI_Agent_Frontier_Report + Deep codebase review*
*Designed for 50-70 page academic paper review scenario | Exact file paths verified against source (v2/core/)*
*前置依赖: Phase 0 ✅ | 下一步: Phase 0.5 实施*