# ScholarAgent — Autonomous Academic Paper Review Agent

<p align="center">
  <strong>A cognitive agent that reads academic papers and produces structured peer-review findings.</strong><br>
  Not a prompt chain. Not a workflow builder. A model with domain-specific tools, doing what agents do:<br>
  <em>perceive → reason → act → reflect → iterate.</em>
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a> •
  <a href="#architecture">Architecture</a> •
  <a href="#performance">Performance</a> •
  <a href="#configuration">Configuration</a> •
  <a href="docs/USER_GUIDE.md">User Guide</a> •
  <a href="docs/EXAMPLE_OUTPUT.md">Example Output</a> •
  <a href="LICENSE">GPL-3.0</a>
</p>

---

## What Is This

ScholarAgent is an **LLM-powered academic paper review agent**. You give it a PDF (or Markdown) paper, and it autonomously reads, reasons about methodology, checks data consistency, and produces structured review findings — each with priority, evidence, and section location.

It is designed as a **cognitive system**, not a pipeline. The agent decides what to read next, what hypotheses to form, when to dig deeper, and when to stop. The harness provides tools, memory, and constraints — the model provides judgment.

**Key metrics** (evaluated against human-annotated gold standard, 2 economics papers, 22 gold findings):

| Metric | Baseline | Current | Δ |
|--------|----------|---------|---|
| Precision | 58.3% | 91.7% | +33.4pp |
| Recall | 38.9% | 50.0% | +11.1pp |
| **F1** | **46.3%** | **63.2%** | **+16.9pp** |

---

## Quick Start

```bash
# 1. Clone and install
git clone https://github.com/feiyu1215/scholar-agent.git
cd scholar-agent
pip install -r v2/requirements.txt    # Only 3 deps: openai, pymupdf, python-dotenv

# 2. Configure API key
cp .env.example .env
# Edit .env: add your OpenAI API key (or any OpenAI-compatible endpoint)

# 3. Run
python v2/main.py path/to/your-paper.pdf
```

That's it. The agent will begin an interactive review session — reading sections, forming hypotheses, and reporting findings. You can ask follow-up questions at any point.

### Docker (One-Command Deployment)

```bash
# Copy .env.example → .env and add your API key, then:
docker-compose up -d

# Review a paper:
docker exec -it scholar-agent python v2/main.py /papers/your-paper.pdf
```

See [Docker Setup](#docker-setup) for details.

---

## Architecture

ScholarAgent uses a **5-layer cognitive architecture** with 31 kill switches for independent feature control:

```mermaid
graph TB
    %% Layer 0: Constitutional Constants
    subgraph "Layer 0 — Constitutional Constants (Immutable)"
        CONST[/"MAX_META_DEPTH=2<br/>EVIDENCE_CHAIN_MIN=3<br/>ZONE_A_MIN=6000 tokens<br/>SUB_PERSPECTIVE_CAP=40"/]
    end

    %% Layer 1: Core Framework
    subgraph "Layer 1 — Core Framework"
        MAIN[main.py<br/>CLI Entry] --> AGENT[agent.py<br/>ScholarAgent]
        AGENT --> LOOP[loop.py<br/>Cognitive Loop]
        LOOP --> HARNESS[harness.py<br/>State + Execution]
        HARNESS --> PHASES[phases.py<br/>Phase FSM]
        AGENT --> IDENTITY[identity.py<br/>3 Personas]
        LOOP --> BUDGET[budget_policy.py<br/>Token Safety Net]
        LOOP --> COMPACTION[compaction.py<br/>State Preservation]
    end

    %% Layer 2: Tool Chain
    subgraph "Layer 2 — Tool Chain (26 tools)"
        direction LR
        READ[Reading<br/>navigate / search /<br/>load references]
        FIND[Findings<br/>record / dedup /<br/>prioritize]
        EDIT[Editing<br/>section / paragraph /<br/>sentence ops]
        META[Metacognition<br/>reflect / plan /<br/>cognitive hints]
        HYPO[Hypothesis<br/>HD-WM: generate →<br/>evidence → resolve]
        MISC[Misc<br/>stats / checkpoint /<br/>consolidate]
    end

    %% Layer 3: Advanced Cognition
    subgraph "Layer 3 — Advanced Cognition"
        MCL[Meta-Cognition Layer<br/>Model Routing: HIGH/MED/LOW]
        HDWM[HD-WM<br/>Hypothesis-Driven<br/>Working Memory]
        PCG[Paper Cognition Graph<br/>Zone A/B/C Navigation]
        CONSOL[Consolidation<br/>Semantic Dedup<br/>60% Retention Guard]
        DUALLOOP[Dual-Loop Orchestrator<br/>Outer: observe + advise<br/>Inner: execute review]
        SIGNAL[Signal Dispatcher<br/>Unified message bus<br/>max 2/turn]
        GUARD[Loop Guard<br/>Pattern Detection +<br/>Phase-Aware Recovery]
    end

    %% Layer 4: Domain Skills
    subgraph "Layer 4 — Domain Skills (SkillX)"
        ECON[Economics Skills<br/>math_audit / planning /<br/>functional / atomic]
        TABLE[Table Processing<br/>8-rule consistency /<br/>PDF extraction /<br/>text cross-validation]
        FIGURE[Figure Semantic<br/>14 chart types /<br/>cross-modal validation /<br/>coverage analysis]
        KNOW[Knowledge Skills<br/>9 markdown skills /<br/>6 YAML templates]
        SYNTH[Skill Synthesis<br/>Runtime skill generation<br/>from failure patterns]
    end

    %% Layer 5: Training & Evolution
    subgraph "Layer 5 — Training & Evolution"
        ADV[Adversarial Training<br/>Red-Blue Arena]
        REFLECT[Tri-Frequency Reflector<br/>Fast(0 LLM) / Deep(LLM) /<br/>Emergency(realtime)]
        EVO[Evolution Engine<br/>Cross-session learning]
        METAH[Meta-Harness<br/>Process metrics +<br/>Bottleneck analysis]
    end

    %% Connections
    HARNESS --> READ
    HARNESS --> FIND
    HARNESS --> EDIT
    HARNESS --> META
    HARNESS --> HYPO
    HARNESS --> MISC

    LOOP --> MCL
    MCL --> HDWM
    HARNESS --> PCG
    LOOP --> CONSOL
    LOOP --> DUALLOOP
    LOOP --> SIGNAL
    LOOP --> GUARD

    HARNESS --> ECON
    HARNESS --> TABLE
    HARNESS --> FIGURE
    HARNESS --> KNOW
    REFLECT --> SYNTH

    ADV --> REFLECT
    EVO --> METAH

    CONST -.->|"Bounds all layers"| LOOP

    %% Styling
    classDef constitutional fill:#1a1a2e,color:#e0e0e0,stroke:#16213e
    classDef core fill:#0f3460,color:#e0e0e0,stroke:#533483
    classDef tools fill:#533483,color:#e0e0e0,stroke:#e94560
    classDef cognition fill:#16213e,color:#e0e0e0,stroke:#0f3460
    classDef skills fill:#1a1a2e,color:#e0e0e0,stroke:#533483
    classDef training fill:#0f3460,color:#e0e0e0,stroke:#e94560

    class CONST constitutional
    class MAIN,AGENT,LOOP,HARNESS,PHASES,IDENTITY,BUDGET,COMPACTION core
    class READ,FIND,EDIT,META,HYPO,MISC tools
    class MCL,HDWM,PCG,CONSOL,DUALLOOP,SIGNAL,GUARD cognition
    class ECON,TABLE,FIGURE,KNOW,SYNTH skills
    class ADV,REFLECT,EVO,METAH training
```

### Design Philosophy

The core insight: **agency comes from the model; the harness makes agency real.**

- **Agent = Cognition, not Orchestration.** The model decides what to do. The code executes what the model asks for.
- **Depth emerges autonomously.** No hardcoded "read section 1, then section 2" — the agent navigates based on what it finds.
- **Constrain, don't control.** Phase transitions are nudges, not blocks. The agent can override if it has good reason.
- **LLM = stateless CPU; Harness = registers + memory + bus.** State lives in the harness (findings, hypotheses, paper sections), not in the conversation history.
- **Every feature is a kill switch.** 31 independent environment variables control subsystems. Any feature can be disabled without affecting others.

### Execution Flow

```
main.py → ScholarAgent.start()
    → load_paper() (PDF/MD → sections via pymupdf)
    → build_system_prompt() (persona + identity + skills)
    → pre_generate_cognitive_hints() (LLM strategy planning)
    → cognitive_loop() (autonomous review, configurable turns)
        ├── Tool calls (26 tools, parallel-safe)
        ├── Phase FSM nudges (INITIAL_SCAN → DEEP_REVIEW → EDITING → SYNTHESIS)
        ├── Compaction Engine (state preservation on context overflow)
        ├── Loop Guard (doom loop detection + recovery)
        └── Signal Dispatcher (max 2 injections/turn)
    → deep_verify() (heuristic skill validation)
    → consolidation_pass() (semantic deduplication, 60% retention floor)
    → return structured findings
```

---

## Features

### Cognitive Loop with Phase FSM

The agent progresses through phases: `INITIAL_SCAN → DEEP_REVIEW → EDITING → SYNTHESIS`. Transitions are nudge-based — the agent decides when it has enough evidence to move forward.

### 26 Domain-Specific Tools

Reading (section navigation, literature search, reference reading), Findings (structured issue recording with deduplication), Editing (section/paragraph/sentence level), Metacognition (reflection, planning, cognitive hints), Hypothesis (HD-WM: generate → evidence → resolve), and more.

### Multi-Model Routing (MCL)

The Meta-Cognition Layer routes sub-tasks to appropriate model tiers: HIGH (gpt-4.1) for deep reasoning, MEDIUM (gpt-4.1-mini) for structured tasks, LOW for simple extraction. Model behavior profiles in `config/model_profiles.json` encode per-model characteristics (token efficiency, expected first-finding turn, cognitive nudge thresholds).

### Table Processing & Numerical Validation

8-rule consistency engine validates regression tables, descriptive statistics, and cross-references text claims against table values. Detects coefficient-SE mismatches, R² bound violations, sample size monotonicity issues, and more.

### Figure Semantic Understanding

14-type chart classifier with economics-specialized analysis (event study, DID, RD, coefficient plots). Cross-modal validation checks magnitude/significance/trend consistency between figures and text.

### Semantic Consolidation

Post-loop LLM pass that merges semantically duplicate findings while preserving distinct issues. Includes a 60% minimum retention guard to prevent over-aggressive merging.

### 31 Kill Switches

Every major subsystem can be independently enabled/disabled via environment variables (`SCHOLAR_GODEL_*`). Enables controlled experiments and graceful degradation. Full list: [`v2/core/godel_config.py`](v2/core/godel_config.py).

### Three Personas

`scholar` (default reviewer), `writer` (revision mode), `code_reviewer` (code analysis). Same cognitive loop, different identity and tool permissions.

### Budget Control & Checkpoint Resume

Token budget acts as a safety net (agent is unaware of it). When exceeded, state is checkpointed and can be resumed with additional budget.

### Adversarial Self-Training

Red-Blue Arena with ELO scoring, season management, and weakness-driven curriculum design. Red Team generates adversarial review challenges; Blue Team learns defensive strategies.

### Optimization Engineering (V2.1)

Five production-hardening optimizations implemented on the core loop:

1. **Compaction State Preservation** — findings and hypotheses survive context window compression without loss
2. **Tool Circuit Breaker** — consecutive failures trigger graceful degradation instead of doom loops
3. **Sub-Perspective Deadline Signals** — last-N-turns window forces synthesis before timeout
4. **Model Behavior Profiles** — per-model JSON configs adapt loop parameters to model characteristics
5. **Global Model Override** — CLI `--model` flag overrides all role assignments for single-model deployments

---

## Performance

Evaluated on 2 economics papers with human-annotated gold standard (13 + 9 findings):

| Paper | Agent Findings | Precision | Recall | F1 |
|-------|---------------|-----------|--------|------|
| Paper 001 (DID methodology) | 5 | 80.0% | 30.8% | 44.4% |
| Paper 003 (Innovation policy) | 7 | 100.0% | 77.8% | 87.5% |
| **Combined** | **12** | **91.7%** | **50.0%** | **63.2%** |

Key characteristics:

- **High precision**: 91.7% of reported findings are genuine issues (vs 58.3% baseline)
- **Complementary across runs**: Different runs discover different findings; multi-run union improves recall
- **Stable on well-structured papers**: Paper 003 achieves 87.5% F1 consistently
- **Economics-specialized**: Trained on methodology patterns (DID, RDD, IV, panel data), table validation, and overclaim detection

---

## Configuration

### Environment Variables (.env)

```bash
# Required
OPENAI_API_KEY=your-api-key-here

# Optional: endpoint (default: https://api.openai.com/v1)
OPENAI_BASE_URL=https://api.openai.com/v1

# Optional: models
LLM_MODEL=gpt-4.1              # Primary model (default: gpt-4.1-mini)
LLM_MODEL_HIGH=gpt-4.1         # Deep reasoning tasks
LLM_MODEL_MEDIUM=gpt-4.1-mini  # Structured tasks (consolidation, routing)
LLM_MODEL_LOW=gpt-4.1-mini     # Simple extraction
```

Compatible with any OpenAI-compatible API (Together, Groq, Ollama, vLLM, etc.).

### CLI Options

```bash
python v2/main.py <paper> [options]

Options:
  --mode {interactive,full}   Run mode (default: interactive)
  --persona {scholar,writer,code_reviewer}  Cognitive identity
  --hdwm                      Enable Hypothesis-Driven Working Memory
  --max-turns N               Maximum loop turns (default: 30)
  --budget N                  Token budget (default: 100000)
  --context-window N          Model context window size (default: 128000)
  --model MODEL               Override LLM model (all roles)
  --references FILE [FILE...] Reference papers for comparison
  --stream                    Enable streaming output
  --quiet                     Reduce output verbosity
```

### Kill Switches

All features default ON (except Streaming and V2Contrast). Disable any with:

```bash
export SCHOLAR_GODEL_TABLE_PROCESSING=0   # Disable table validation
export SCHOLAR_GODEL_DUAL_LOOP=0          # Disable dual-loop orchestration
export SCHOLAR_GODEL_ADVERSARIAL_TRAINING=0  # Disable adversarial training
```

Full list of 31 switches: see [`v2/core/godel_config.py`](v2/core/godel_config.py).

---

## Project Structure

```
scholar-agent/
├── v2/                              # Active codebase (self-contained)
│   ├── main.py                      # CLI entry point
│   ├── core/                        # Core engine (70+ modules)
│   │   ├── agent.py                 # ScholarAgent + CollaborativeReview
│   │   ├── loop.py                  # Cognitive loop driver
│   │   ├── harness.py               # State + tool execution + sub-harness spawning
│   │   ├── consolidation.py         # Semantic finding deduplication
│   │   ├── compaction.py            # Context compression with state preservation
│   │   ├── phases.py                # Phase FSM (nudge-based transitions)
│   │   ├── identity.py              # Persona system + tool schemas
│   │   ├── godel_config.py          # 31 kill switches + constitutional constants
│   │   ├── loop_guard.py            # Doom loop detection + recovery
│   │   ├── signal_dispatcher.py     # Unified system message bus
│   │   ├── skills/                  # SkillX programmatic skills
│   │   │   ├── economics/           # 4 economics-specific skills
│   │   │   └── multimodal/          # 10 table/figure modules
│   │   └── tool_handlers/           # 6 handler modules (26 tools)
│   ├── llm/                         # LLM client + multi-model routing
│   │   ├── client.py                # Unified LLM interface
│   │   ├── session_model_manager.py # Runtime model switching + budget tracking
│   │   └── router.py                # Model tier routing
│   ├── config/                      # YAML/JSON configuration
│   │   ├── model_profiles.json      # Per-model behavior profiles
│   │   ├── providers.json           # Multi-model provider configs
│   │   └── thresholds.yaml          # Quality gate thresholds
│   ├── evaluation/                  # Gold standard evaluation system
│   │   ├── gold_standard/           # Human-annotated findings (7 papers)
│   │   ├── test_papers/             # 5 test papers (PDF)
│   │   └── reports/                 # 90+ evaluation reports
│   ├── training/                    # Adversarial training subsystem
│   ├── skills/                      # Knowledge skills (9 markdown + 6 YAML templates)
│   └── guidelines/                  # Agent behavioral guidelines
├── docs/                            # Documentation
│   ├── USER_GUIDE.md                # Complete user documentation
│   ├── EXAMPLE_OUTPUT.md            # Real review output samples
│   ├── COGNITIVE_ANCHOR.md          # First-principles constraints
│   └── HANDOVER_PROMPT.md           # Session handover context
├── .env.example                     # Environment template
├── Dockerfile                       # Container build (python:3.11-slim)
├── docker-compose.yml               # One-command deployment
├── .github/workflows/ci.yml         # CI: lint + import check + test + E2E smoke
└── LICENSE                          # GPL-3.0
```

Legacy directories (`v1/`, `legacy/`, `poc/`) are preserved for reference but not used by v2.

---

## Docker Setup

### Quick Start

```bash
# 1. Configure
cp .env.example .env
# Edit .env with your API key

# 2. Build and launch
docker-compose up -d

# 3. Review a paper (mount your papers directory)
docker exec -it scholar-agent python v2/main.py /papers/your-paper.pdf

# Or run directly with volume mount:
docker run --env-file .env -v $(pwd)/papers:/papers scholar-agent \
    python v2/main.py /papers/paper.pdf
```

### What's Included

The Docker image (`python:3.11-slim` base):

- All Python dependencies pre-installed
- Source code at `/app/v2/`
- Papers mount point at `/papers/`
- Workspace persistence via volume mount
- Interactive mode via `stdin_open: true` + `tty: true`

### docker-compose.yml

```yaml
services:
  scholar-agent:
    build: .
    container_name: scholar-agent
    env_file: .env
    volumes:
      - ./papers:/papers              # Your papers here
      - ./v2/.workspace:/app/v2/.workspace  # Persist state
    stdin_open: true
    tty: true
```

---

## Development

### Prerequisites

- Python ≥ 3.10
- Dependencies: `openai`, `pymupdf`, `python-dotenv` (that's it — intentionally minimal)

### Running Tests

```bash
cd v2/
pip install pytest pytest-asyncio
pytest tests/ -m "not e2e" --tb=short
```

### Running Evaluation

```bash
cd v2/
python -m evaluation.run_recall_verification --paper paper_001 --model gpt-4.1
```

### Code Style

- Type hints everywhere
- Async/await for all LLM calls
- Docstrings on public interfaces
- Ruff for linting (`ruff check v2/ --select=F401,F811`)

---

## Design Decisions

| Decision | Rationale |
|----------|-----------|
| Single agent, not multi-agent | Review is one cognitive task deepened, not multiple roles collaborating |
| Phase nudges, not blocks | Agent autonomy > rigid workflow |
| Budget as safety net (invisible to agent) | Agent should think freely; budget prevents runaway cost |
| Term-overlap + LLM consolidation (layered dedup) | Cheap filter catches obvious duplicates; LLM catches semantic ones |
| Kill switches on everything | Enables A/B experiments and graceful degradation |
| No LangChain/CrewAI/AutoGen | Cognitive depth requires custom architecture, not workflow frameworks |
| 3 runtime deps only | Minimal attack surface; fast install; no dependency hell |
| Constitutional constants immutable | Layer 0 bounds prevent self-modifying recursion spirals |

---

## Roadmap

- [x] **Stage 1**: Core review + recall improvement (F1: 46.3% → 63.2%)
- [x] **Stage 2**: Self-evolution framework (adversarial training, skill synthesis, tri-frequency reflection)
- [x] **Stage 3**: Engineering polish (README, Docker, CI, user docs, example outputs)
- [ ] Future: Web UI, multi-paper comparison, citation graph verification, cross-discipline expansion

---

## License

GPL-3.0 — You may freely use, modify, and distribute this software, but derivative works must be open-sourced under the same license. See [LICENSE](./LICENSE).

---

## Acknowledgments

Built on the cognitive harness pattern. Uses OpenAI-compatible endpoints (any provider).

> "I didn't make the model smarter. I made the harness know when to perceive, when to reason, and when to stop."
