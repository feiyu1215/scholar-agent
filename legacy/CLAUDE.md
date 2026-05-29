# CLAUDE.md — ScholarAgent

## What this is
Academic paper revision agent — a true 100% Agent with autonomous goal management, dynamic tool filtering, persistent planning, self-reflection, error recovery, adaptive strategy, and cross-session learning. Harness pattern: model decides, code executes. Papers live as section files in `.workspace/paper/`, never loaded into context whole.

## Architecture (do not violate)
- Entry: `main.py` (~250 lines, thin REPL + init) → `core/agent_loop.py` → `core/tool_dispatch.py` (56 tools registered)
- Core modules: `core/state.py` (shared state), `core/prompts.py` (system prompt), `core/tool_schemas.py` (tool definitions), `core/context_pipeline.py` (token mgmt)
- Handlers: `handlers/` (paper_ops, review_ops, write_ops, deai_ops, search_ops, meta_ops)
- LLM calls only through `llm/client.py` (supports streaming via `chat_with_tools_stream()`); routing via `llm/router.py` (HIGH/MEDIUM/LOW tiers)
- Tools in `tools/`; utilities in `utils/`; skill prompts in `skills/`; thresholds in `config/`
- Guidelines loaded on-demand via `read_agent_guidelines` (progressive disclosure, not monolithic prompt)
- De-AI engine lives in `tools/deai/` package (scene, signals, fix, verify, constants) with backward-compat shim at `tools/deai_engine.py`
- Pipeline tools (`tools/deai_pipeline.py`): 4 independent steps Agent can orchestrate (detect → diagnose → rewrite → verify)
- Streaming: `--stream` flag enables async generator streaming; `/pause` `/resume` `/takeover` REPL commands for runtime control

## Agent Infrastructure (100% Agent Upgrade, 2025-07)

| Module | Role | Wave |
|--------|------|------|
| `utils/goal_tracker.py` | Phase state machine (IDLE→PARSING→ANALYSIS→REVIEW→ROUTING→REVISION→VERIFICATION→DONE) with auto-transitions | Wave 2 |
| `utils/phase_filter.py` | Phase-aware tool filtering — only exposes relevant tools per phase (~15-25 of 50+) | Wave 2 |
| `utils/plan_persistence.py` | Persistent plan objects (survives context compression, file-backed) | Wave 2 |
| `utils/self_reflection.py` | Periodic reflection injection at milestones + self_critique tool | Wave 3 |
| `utils/error_recovery.py` | Error classification, circuit breaker, fallback chain, retry with backoff | Wave 3 |
| `utils/context_manager.py` | Proactive context compression (soft/hard limits, CJK-aware token estimation, retention policies) | Wave 3 |
| `utils/output_quality.py` | Heuristic quality gate (rewrite/deai/review validation without LLM cost) | Wave 3 |
| `utils/adaptive_strategy.py` | Dynamic strategy adjustment based on paper characteristics (length, quality, language) | Wave 4 |
| `utils/session_memory.py` | Cross-session learning: journals, tool patterns, implicit preference inference | Wave 4 |
| `utils/meta_planner.py` | Plan optimization from historical patterns (advises, doesn't decide) | Wave 4 |
| `guidelines/` | 5 topic files loaded via `read_agent_guidelines` (planning, tool_selection, iteration_protocol, deai_strategy, budget_rules) | Wave 1 |

## Key Modules (pre-upgrade, still active)

| Module | Role |
|--------|------|
| `tools/deai/` | De-AI package: scene detection, signal detection, fix pipeline, self-check |
| `tools/deai_pipeline.py` | 4 independent Agent-orchestrated tools (deai_detect, deai_diagnose, deai_rewrite, deai_verify) |
| `tools/review_engine.py` | Multi-role parallel review (configurable reviewer_count, focus_dimensions, custom_criteria) |
| `tools/citation_synergy.py` | Unified citation verification + literature cross-reference |
| `tools/dry_run.py` | Pre-execution cost/time estimation (9 operation profiles × 3 model tiers) |
| `tools/focus_generator.py` | Dynamic focus point generation — paper-specific review focus injected per reviewer role |
| `tools/post_edit_verify.py` | Post-edit regression check (delegates voice drift to utils/voice_profile) |
| `config/thresholds.yaml` | Centralized magic numbers (deai_engine, post_edit_verify, review_engine, quality_gate) |
| `utils/voice_profile.py` | Single source of truth for voice drift detection |
| `utils/json_repair.py` | 4-layer JSON parsing recovery (direct → boundaries → repair → regex fallback) |
| `utils/checkpoint.py` | Checkpoint + Resume for long-running pipelines (.workspace/checkpoints/) |
| `utils/author_profile.py` | Cross-session preference learning — learned_rules auto-injected into DeAI fix prompts |
| `utils/token_budget.py` | Per-issue token budget calculator (prevents output bloat) |

## Red Lines (hard-coded, never bypass)
1. Never modify core thesis — force to guidance only
2. Never fabricate citations or data — downgrade auto_fix to confirm_fix
3. De-AI fix must not degrade expression — length check + kept_original enforced in code

## Do NOT
- Put full paper text in context. Use section_ops to read/write individual sections.
- Call LLM without routing through `llm/router.py`. Every task has a tier.
- Skip deai_precheck before deai_audit. L1 gate saves 80%+ unnecessary LLM calls.
- Let the model self-evaluate its own rewrite. Examiner ≠ examinee (separate calls).
- Exceed token budget from `utils/token_budget.py`. It's a ceiling, not a suggestion.
- Bypass first-of-type confirmation. First auto_fix in any new category needs user OK.
- Ignore Voice Profile drift. Rewrites must stay within ±20% of author fingerprint metrics.
- Run parallel_rewrite on sections with cross-references without checking independence first.
- Duplicate threshold constants — always read from `config/thresholds.yaml`.
- Implement voice drift detection outside `utils/voice_profile.py` — it's the single source of truth.
- Call tools that are not exposed in the current phase (the harness filters automatically).

## Agent Behavior Patterns (100% Agent)

- **Phase State Machine**: Tools are filtered by current phase; phase advances automatically on tool completion
- **Goal Tracking**: Agent tracks goals and progress; harness injects periodic progress checks
- **Persistent Plans**: Plans survive context compression via file-backed storage; use `save_plan`/`load_plan`/`advance_plan`
- **Self-Reflection**: Injected at phase transitions, after errors, and every N tool calls
- **Error Recovery**: Circuit breaker on repeated failures; exponential backoff; fallback tool suggestions
- **Adaptive Strategy**: Paper characteristics (length, language, polish level) auto-configure strategy
- **Cross-Session Memory**: Session journals, tool patterns, and implicit preferences persist between sessions
- **Meta-Planning**: Historical patterns advise tool sequencing; negative patterns trigger warnings
- **Quality Gate**: Heuristic validation of rewrites/reviews before returning to user (no LLM cost)
- **Proactive Context**: Smart compression with retention policies (ALWAYS_KEEP, KEEP_UNTIL_SUPERSEDED, COMPRESS_TO_SUMMARY)

## Legacy Patterns (still active)
- **PEV Loop**: rewrite → deai_audit → fix → re-audit (max 2 retries, exit on plateau <0.05 improvement)
- **Context compression**: smart_compact at 30K tokens (retention-aware), then LLM summary at 45K
- **Doom loop detection**: sliding window (8), threshold 3 same-tool calls → block + guidance
- **Recall cache**: file-backed TTL cache in `.workspace/recall/`, skips volatile tools
- **Gold standard**: resolved sections stored as few-shot examples in `.workspace/gold_standards/`
- **Author Profile injection**: learned_rules from rejection history auto-appended to fix prompts

## Testing & running
```bash
cp .env.example .env  # fill OPENAI_API_KEY
pip install -r requirements.txt
python3 main.py --paper path/to/paper.pdf
python3 main.py --stream --paper paper.pdf   # streaming mode
```

REPL commands: `/pause` (pause agent mid-stream), `/resume` (resume), `/takeover` (stop agent, user takes over)

Tests: `pytest tests/` (168 tests; test_stata_mcp::test_part_b needs pytest-asyncio)

## File conventions
- All state in `.workspace/` (gitignored). Trace at `.workspace/trace.jsonl`.
- Section files: `.workspace/paper/sections/{01_..., 02_...}.md`
- Session memory persists at `.workspace/session_memory/` (journals, patterns, preferences)
- Author profile at `.workspace/author_profile.json`
- Voice profile at `.workspace/voice_profile.json`
- Plans at `.workspace/.plans/`
- Thresholds: `config/thresholds.yaml` (single source, `config.load_thresholds()` to access)
- Guidelines: `guidelines/*.md` (loaded on-demand via `read_agent_guidelines`)
