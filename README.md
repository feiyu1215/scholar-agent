# ScholarAgent v2 — Academic Paper Review & Revision Agent

A real agent for academic paper review and revision, built on the **Harness pattern** from Claude Code architecture.

Not a prompt chain. Not a workflow builder. A model with domain-specific tools, doing what agents do: perceive → reason → act → pause → communicate → iterate.

## Why This Architecture

Most "AI paper review tools" do one of two things:
1. Dump the entire paper into an LLM and ask "review this" — **token-expensive, attention-scattered, not traceable**
2. Build a rigid pipeline with hardcoded steps — **brittle, can't adapt, GOFAI in disguise**

ScholarAgent takes a different approach:

> **Same model, better harness, better results.**

The model decides what to do. The harness gives it focused context, efficient tools, and structured external memory. This is the same principle that makes Claude Code outperform other coding assistants with the same underlying model.

## Architecture (v2)

```
                    THE AGENT LOOP
                    ==============

    User --> messages[] --> LLM --> response
                                      |
                            tool_call detected?
                           /                    \
                         yes                     no
                          |                       |
                    execute tool               return text
                    append result
                    loop back ---------> messages[]


    The MODEL decides when to call tools and when to stop.
    The CODE just executes what the model asks for.
```

### What the Harness Provides

```
ScholarAgent Harness = Domain Tools + Action Routing + De-AI Audit + Statistical Verification
                     + Structured Memory + Context Compression + Human-in-the-Loop

    Domain Tools:     parse_paper, read_section, review_paper, rewrite_section,
                      edit_section, diff_section, consistency_check

    v2 Tools:         route_issues (Red Line enforcement + budget ceiling),
                      generate_fix_proposal (Dry-Run mode),
                      approve_fix (first-of-type confirmation),
                      deai_audit (AI signal detection + minimum-slice fix),
                      stata_verify (statistical verification via MCP),
                      revision_progress (session state dashboard)

    Action Routing:   Each review issue is classified into auto_fix / confirm_fix / guidance
                      and routed through Red Line checks + budget-mode ceiling.

    De-AI Audit:      Independent post-rewrite verifier (PEV Loop).
                      Detects AI writing signals → sentence-level fix → re-audit.
                      Max 2 retries; stops on plateau.

    Structured Memory: Paper lives in filesystem as section-level files, NOT in context.
                       Revision state (JSON) tracks issue lifecycle + de-AI results.
                       Section index provides O(1) navigation without loading content.

    Context Compression: Layer 1 (micro): old tool results → placeholders
                         Layer 2 (auto): summarize when tokens > threshold
                         Layer 3 (manual): model calls compact when it wants

    Human-in-the-Loop:  ask_user tool pauses the agent at key decision points.
                         Model decides WHEN to pause (not hardcoded steps).
```

## v2 Features

### 1. Issue-Based Action Routing

Every review issue gets a classified `action_type`:

| action_type | Behavior | When |
|-------------|----------|------|
| `auto_fix` | Agent fixes directly, then runs de-AI audit | Clear technical issues (grammar, format, hedging) |
| `confirm_fix` | Shows proposal, waits for approval, then executes | Touches core argument or author intent |
| `guidance` | Outputs instructions only, zero rewrite cost | Needs new data/experiments/references |

Red Lines are enforced in code (not by model judgment):
- **Red Line 1**: Never modify core thesis or causal direction
- **Red Line 2**: Never invent citations, data, or factual claims
- **Red Line 3**: De-AI fix must not degrade expression quality

### 2. Budget-Aware Mode

```bash
python3 main.py --budget full      # auto_fix + confirm + guidance (default)
python3 main.py --budget medium    # auto_fix + guidance (no user confirmation)
python3 main.py --budget minimal   # guidance only (zero rewrite, pure reviewer)
```

| Budget | auto_fix | confirm_fix | guidance |
|--------|----------|-------------|----------|
| full | executes | proposes → confirm → executes | outputs instructions |
| medium | executes | downgrades to guidance | outputs instructions |
| minimal | downgrades to guidance | downgrades to guidance | outputs instructions |

### 3. De-AI Audit (PEV Loop)

After every rewrite, an independent verifier checks for AI writing signals:

```
rewrite_section → deai_audit → [PASS] → done
                      ↓
                   [FAIL] → fix_ai_signals → deai_audit → ... (max 2 retries)
```

12 signal categories detected (see `skills/deai_rules_en.md`): AI vocabulary, mechanical openers, tricolon patterns, rhythm uniformity, connector overuse, and more.

### 4. Stata MCP Statistical Verification

Methodology issues flagged `needs_statistical_verification` trigger Stata code generation. Graceful degradation: if Stata MCP is unavailable, outputs `.do` code as guidance for manual execution.

## Key Design Decisions

### Section-Level Granularity (Token Efficiency)

The paper never lives in context as a whole. After parsing:

```
.workspace/paper/
    section_index.json     ← lightweight index (agent reads this)
    sections/
        01_abstract.md     ← agent reads ONE at a time
        02_introduction.md
        ...
```

### Multi-Role Review via Subagent Isolation (Accuracy)

Five reviewers run in parallel, each with isolated context:

| Reviewer | Sees Only | Focus |
|----------|-----------|-------|
| Editor | Abstract + Introduction | Desk-reject screening |
| Theory | Intro + Theory + Discussion | Novelty of contribution |
| Methodology | Methods + Data + Results | Reproducibility & rigor |
| Logic | Intro + Results + Discussion + Conclusion | Argument coherence |
| Literature | Intro + Related Work + Discussion | Gap authenticity |

### First-of-Type Validation

The first `auto_fix` in each issue category is promoted to `confirm_fix` so the user validates the direction. Once confirmed, subsequent same-category issues auto-execute without asking.

### File-Based Working Memory

Session state persists in `.workspace/revision_state.json` — survives context compression and enables resume across sessions.

## Quick Start

```bash
git clone <this-repo>
cd scholar-agent
pip install -r requirements.txt
cp .env.example .env   # Add your API key

# Interactive mode (full budget)
python3 main.py

# Start with a paper
python3 main.py --paper my_paper.pdf

# Use specific model
python3 main.py --model gpt-4o

# Review-only mode (zero rewrite cost)
python3 main.py --budget minimal --paper my_paper.pdf
```

## Project Structure

```
scholar-agent/
├── main.py                     # Agent loop + REPL + tool dispatch + context compression
├── DESIGN.md                   # v2 architecture design document (detailed rationale)
├── llm/
│   └── client.py               # Async LLM client (multi-provider, retry, token tracking)
├── tools/
│   ├── paper_parser.py         # PDF/tex → section files
│   ├── section_ops.py          # Read/edit/diff at section level
│   ├── review_engine.py        # Multi-role parallel review (5 subagents + consolidation)
│   ├── write_engine.py         # Section rewriting + de-AI post-hook + fix proposal
│   ├── action_router.py        # [v2] Issue routing: Red Lines + budget ceiling + first-of-type
│   ├── deai_engine.py          # [v2] De-AI audit + fix (PEV Loop, max 2 retries)
│   ├── revision_state.py       # [v2] JSON-persisted session state (issue lifecycle)
│   └── stata_verify.py         # [v2] Stata MCP integration (graceful degradation)
├── skills/                     # Domain knowledge (loaded on demand per phase)
│   ├── review_criteria.md      # Review Phase: scoring rubric + guidelines
│   ├── econ_writing.md         # Rewrite Phase: economics writing conventions
│   └── deai_rules_en.md        # [v2] De-AI Phase: 12 signal categories for English academic
├── .workspace/                 # Runtime: parsed paper, reviews, revisions (git-ignored)
├── .env.example                # Environment variable template
└── requirements.txt            # Python dependencies
```

## Comparison: v1 → v2 Evolution

| Aspect | v1 | v2 |
|--------|----|----|
| Issue handling | All issues → auto rewrite | 3 action types (auto/confirm/guidance) |
| Budget flexibility | One mode only | 3 budget modes (full/medium/minimal) |
| Safety | Model-level "be careful" | Code-enforced Red Lines |
| Post-rewrite QA | None | De-AI PEV Loop (independent verifier) |
| Statistical claims | Verbal suggestions only | Stata code generation + MCP execution |
| Session memory | Conversation history only | File-based revision_state.json |
| User fatigue | Confirm every fix | First-of-type then auto-batch |

## Design Philosophy

> "Agency comes from the model. The harness makes agency real." — learn-claude-code

This project demonstrates that a well-designed harness can make a standard model perform expert-level academic review — not by adding more prompt engineering, but by giving the model focused context, structured memory, appropriate tools, and permission to pause.

The v2 additions prove a further principle: **control flow design is product design**. The same review output becomes three different products depending on budget mode. The same rewrite engine gains quality assurance through an independent verification loop. The same model never crosses safety boundaries because Red Lines are code, not prompts.

> "I didn't make the model smarter. I made the harness know when to act, when to ask, and when to stop."

## TODO

- [ ] `deai_rules_zh.md` — Chinese academic de-AI rules (S3 scene)
- [ ] `methodology_checklist.md` — Stata verification checklist
- [ ] Multimodal: figure/table analysis via vision model
- [ ] Literature verification: cross-reference cited papers via search
- [ ] Voice Profile: quantify author style and inject into rewrite constraints
- [ ] Author Profile: cross-session memory of style preferences + rejected patterns
- [ ] Section-level parallel processing (concurrent rewrite of independent sections)
- [ ] Web UI with split-pane (execution trace | output)
- [ ] Self-improvement: gold standard memory + skill auto-evolution

## License

GPL-3.0 — 你可以自由使用、修改和分发，但衍生作品必须以相同许可证开源。详见 [LICENSE](./LICENSE)。
