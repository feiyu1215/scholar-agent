# Changelog

All notable changes to ScholarAgent are documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/).

## [5.13.0] — 2026-05-27

### Added — Phase 9A: Table Processing & Numerical Validation (表格处理与数值一致性验证)

Multi-source table extraction + economics table semantic parsing + 8-rule numerical consistency validation engine. Milestone M2 final item.

#### v2/core/skills/multimodal/table_parser.py — 文本表格解析器
- `CellValue` dataclass: numeric/star/parenthesis/bracket/empty parsing with comma-formatted integer support
- `RawTable` dataclass: table_id, caption, headers, body (CellValue grid), notes, source_format
- `TextTableParser`: 3-strategy extraction (LaTeX `\begin{tabular}`, pipe/markdown, space-aligned columns)
- LaTeX: handles `\\hline`, `\\\\` row breaks, `\begin{tablenotes}` for notes extraction
- Markdown: separator-line detection, header/body split, auto-column-count
- Space-aligned: runs of 3+ consistent-column lines detected as table blocks

#### v2/core/skills/multimodal/econ_table.py — 经济学表格语义解析器
- `EconTableType` enum: REGRESSION, DESCRIPTIVE_STATS, BALANCE_TABLE, PANEL, OTHER
- `StarConvention` enum: STANDARD (1/5/10%), INVERTED (10/5/1%), UNKNOWN
- `SEType` enum: ROBUST, CLUSTERED, HETEROSKEDASTIC, BOOTSTRAP, UNKNOWN
- `CoefficientEntry`, `RegressionColumn`, `DescriptiveColumn` dataclasses
- `EconTable` composite structure with table_type, regression_columns, descriptive_columns
- `EconTableParser.classify()`: heuristic scoring for regression vs descriptive detection
- `EconTableParser.parse()`: full pipeline from RawTable → EconTable with star convention & SE type detection

#### v2/core/skills/multimodal/pdf_table_extractor.py — PDF 表格提取
- `PDFTableExtractor`: dual-engine extraction via pdfplumber (primary) + pymupdf heuristic (fallback)
- Line-based region detection for pdfplumber table finding
- Text-block alignment detection for pymupdf fallback (gap-analysis column detection)
- Graceful degradation: missing pdfplumber → pymupdf only; both missing → empty result

#### v2/core/skills/multimodal/consistency_engine.py — 8 规则一致性验证引擎
- `RuleID` enum: 8 validation rules
- Rule 1 (COEFF_SE_CONSISTENCY): t-statistic vs star significance mismatch detection
- Rule 2 (R_SQUARED_BOUNDS): R² ∈ [0,1], adjusted R² ≤ R² check
- Rule 3 (SAMPLE_SIZE_MONOTONICITY): N should not increase when controls/FE added
- Rule 4 (STAR_SIGNIFICANCE): star notation vs stated convention cross-check
- Rule 5 (SE_POSITIVITY): standard errors must be > 0
- Rule 6 (COLUMN_PROGRESSION): nested model structure validation
- Rule 7 (DESCRIPTIVE_INTERNAL): mean ∈ [min, max], SD ≥ 0, min ≤ max
- Rule 8 (TEXT_TABLE_XREF): cross-validate text claims against table values
- `ConsistencyValidator.validate()`: runs all 8 rules, returns `ValidationReport`
- `ValidationReport`: tables_checked, rules_applied, violations list, summary()

#### v2/core/skills/multimodal/text_table_xref.py — 文本-表格交叉验证
- `Claim` dataclass: claim_type (coefficient/significance/sample_size/r_squared/direction), variable, value, text_location
- `TextTableCrossValidator.extract_claims()`: regex-based claim extraction from paper text
- `TextTableCrossValidator.cross_validate()`: matches claims against EconTable data, reports direction/magnitude mismatches

#### v2/core/skills/multimodal/skills.py — SkillX 集成
- `TableExtractionSkill`: descriptor (level=FUNCTIONAL, tags=[table, multimodal]), can_apply (table signal detection), execute (full extraction pipeline)
- `TableConsistencySkill`: descriptor (prerequisite=table_extraction), can_apply (phase-aware), execute (validation + xref pipeline)
- Both respect `SCHOLAR_GODEL_TABLE_PROCESSING` kill switch (default ON)
- Output: raw_tables, econ_tables, extraction_stats, validation_report, xref_violations, findings

#### v2/core/godel_config.py — Kill Switch 注册
- `GODEL_TABLE_PROCESSING_ENABLED = _env_flag("SCHOLAR_GODEL_TABLE_PROCESSING", default="1")`
- Registered in `log_config_status()`

#### Tests
- `v2/tests/test_phase9a_table_processing.py`: 47 tests covering CellValue parsing, TextTableParser (all strategies), EconTableParser (classification + regression + descriptive + star + SE), ConsistencyValidator (8 rules), TextTableCrossValidator (direction mismatch), SkillX integration (descriptor, can_apply, execute), Kill Switch, full pipeline E2E
- **All 47 tests pass** (0.10s)

---

## [5.12.0] — 2025-07-16

### Added — E0: Failure-Driven Rule Generation (失败驱动规则生成)

#### v2/core/rule_extractor.py — 新文件
- `FAILURE_CATEGORIES`: 10 个失败模式类别定义（satisfy_early, read_not_record, no_search, understand_not_question, optional_unused, identity_insufficient, no_meta_cognition, shortest_path, tool_not_used, content_repeat），每个包含关键词列表和根因描述
- `extract_failure_entries(progress_text)`: 从 PROGRESS.md 文本中提取失败/bug 条目，追踪所属 Phase，按类别归类
- `cluster_entries(entries)`: 按 category 聚类，过滤 <2 次的模式
- `generate_rule_candidates(clusters)`: 生成 CLAUDE.md 兼容格式的规则候选（"当{条件}时，不要{错误行为}，而应{正确行为}"）
- `diff_with_existing_rules(candidates, claude_md_text)`: 三层匹配（字面/regex/fuzzy）与已有规则对比，区分 covered vs new
- `extract_rule_candidates(progress_path, claude_md_path)`: 端到端 API
- `format_report(result)`: 人类可读报告格式化
- CLI 入口: `python3 v2/core/rule_extractor.py [progress_path] [claude_md_path]`

#### v2/core/session_finalizer.py — 扩展
- `suggest_new_rules(progress_path, claude_md_path)`: 开发者工具入口，不自动调用（对齐 §4.3 constrain don't control）

#### Tests
- `v2/tests/test_v2_rule_extractor.py`: 32 tests covering extraction, clustering, generation, diff, helpers, E2E (synthetic + real files), edge cases
- **All 491 V2 tests pass** (zero regression from 459 pre-E0)

#### 真实文件运行结果
- 扫描 PROGRESS.md: 85 条失败条目 → 8 个重复模式聚类 → 8 条规则候选
- 与 CLAUDE.md 对比: 3 条已覆盖, 5 条新增候选
- 新增候选: 不会抬头(21次), 满足即停(16次), 内容重复(9次), 理解不质疑(5次), 身份不够(5次)

---

## [5.11.0] — 2025-07-16

### Added — DEAI-2: Semantic Preservation Check (保语义改表达)

#### post_edit_verify.py — Layer 4: 语义保持
- `_NUMERIC_PATTERNS`: 9 regex patterns for p-values, percentages, N-values, coefficients, CIs, t/z/F stats, R², generic numbers with units, standalone decimals
- `_extract_numeric_values(text)`: extracts all numeric/statistical expressions with normalization
- `_CAUSAL_STRONG` / `_CAUSAL_WEAK`: bilingual (EN+CN) causal direction vocabulary sets
- `_detect_causal_direction(text)`: returns (strong_causal_set, weak_association_set)
- `check_semantic_preservation(old_text, new_text)`: returns (passed, issues, warnings)
  - Check 1: numbers disappeared → FAIL (with smart filtering: trivial numbers ignored, format-only changes tolerated via core-number cross-validation)
  - Check 2: causal direction shift → WARN (strong→weak = weakening, weak→strong = overclaim)
- Integrated as Layer 4 in `verify_edit()` — semantic_ok is a hard gate alongside consistency and AI regression
- `VerificationResult.semantic_ok` field added
- `format_verification_feedback()` updated to show semantic status

#### Tests
- `v2/tests/test_v2_semantic_preservation.py`: 48 tests covering numeric extraction, causal detection, preservation checks (FAIL/WARN/PASS), degree qualifiers, normal edits, verify_edit integration, format feedback, edge cases (Chinese, long text, format variations)
- **All 459 V2 tests pass** (zero regression from 411 pre-DEAI-2)

---

## [5.10.0] — 2025-07-15

### Added — Phase 58: User-Provided References (用户参考文献)

#### Layer 1: Reference Loading Infrastructure
- `core/harness.py`: Added `user_reference_docs: dict[str, dict]` field to `WorkspaceState` — stores full content of user-provided documents (sections + metadata)
- `core/harness.py`: Added `reference_paths: list[str] | None` parameter to `Harness.__init__` — accepts user reference file paths at construction time
- `core/harness.py`: Added `_load_user_references(paths)` method — loads PDF/Markdown/text files, splits into sections, stores both full content and metadata
- `core/harness.py`: Added `load_references(paths)` public interface — allows runtime addition of references

#### Layer 2: `read_reference` Tool
- `core/harness.py`: Added `_tool_read_reference(args)` method — Agent can browse user-provided references by ref_id/section/offset
- `core/harness.py`: Tool routing for `read_reference` in `execute_tool()`
- Features: list all refs (no args), list sections (ref_id only), read content (ref_id + section), offset pagination, fuzzy section name matching

#### Layer 3: Unified Literature Mental Model
- `core/identity.py`: Added `read_reference` tool definition in SCHOLAR_TOOLS — positioned as "手边的参考论文"
- `core/identity.py`: Rewrote cognitive habit #7 from "跨文献对比验证" to "文献使用心智模型（Literature as Cognitive Extension）" — three-depth spectrum (验证性搜索 / 参考文献深读 / 主动探索), Agent self-selects depth based on context
- `core/harness.py`: Updated `format_context()` to distinguish user-provided refs (📎) from Agent-fetched papers (📚), with `read_reference` usage hint

#### Layer 4: Constructor Integration
- `core/agent.py`: Added `reference_paths` parameter to `ScholarAgent.__init__` — passes through to Harness
- `core/agent.py`: Added `reference_paths` parameter to `CollaborativeReview.__init__` — shared Harness also supports references

### E2E Validation Results
- `core/test_e2e_phase58_user_refs.py`: 8-test validation suite (loading, list all, list sections, read section, offset, format_context, agent constructor, error handling)
- **All 8 tests passed** — complete user reference capability verified

---

## [5.9.0] — 2025-07-15

### Added — Phase 57: Cross-Document Cognition (多文档交叉审)

#### Layer 1: API Capability — `fetch_paper_detail()`
- `core/web_search.py`: Added `PaperDetail` dataclass — full paper metadata beyond search-level (abstract, TLDR, key_references, key_citations, fields_of_study, influential_citation_count)
- `core/web_search.py`: Added `fetch_paper_detail(paper_id=, doi=, title=)` — Semantic Scholar Paper Detail API integration with 3-tier lookup (paper_id > DOI > title search)
- `core/web_search.py`: Rate limit retry logic (429 → wait 3s → retry once) for both search and detail endpoints
- `core/web_search.py`: Fixed `fieldsOfStudy` parsing — handles both string lists and dict-with-category formats from API

#### Layer 2: Workspace State — Reference Papers
- `core/harness.py`: Added `reference_papers: dict[str, dict]` field to `WorkspaceState` — independent knowledge space for external papers
- `core/harness.py`: Added reference workspace display in `format_context()` — shows up to 5 papers with TLDR, always visible to Agent
- `core/harness.py`: Added `_tool_fetch_paper_detail()` method — fetches detail, stores in workspace, formats rich output, supports offload
- `core/harness.py`: Tool routing for `fetch_paper_detail` in `execute_tool()`

#### Layer 3: Cognitive Identity Enhancement
- `core/identity.py`: Added `fetch_paper_detail` tool definition in SCHOLAR_TOOLS (after search_literature)
- `core/identity.py`: Added cognitive habit #7: "跨文献对比验证（Cross-Document Validation）" — describes when/why to use fetch_paper_detail
- `core/identity.py`: Added "交叉对比检查" to self-check list (item 8)

### E2E Validation Results
- `core/test_e2e_phase57_cross_doc.py`: 6-test validation suite (paper_id lookup, title lookup, DOI lookup, error handling, harness integration, E2E review)
- **Key Result**: Agent **naturally called `fetch_paper_detail` 3 times** in 11-turn review without explicit instruction — proving cognitive identity drives behavior
- **API Verification**: Successfully fetched "Attention Is All You Need" (177,095 citations, 10 key references, 10 key citations)
- **Harness Integration**: Reference workspace correctly stores and displays fetched papers in format_context
- **Rate Limiting**: Graceful handling — Agent's cognitive intent was correct even when API was rate-limited

### Design Principles
- §4.3 compliance: Tool availability + cognitive habit = emergent behavior (no "you must use this tool" instructions)
- Reference workspace is read-only context (Agent sees it but doesn't need to manage it)
- Graceful degradation: Rate limiting doesn't crash the review — Agent adapts and continues with available information
- Three-layer design: API capability alone is insufficient; workspace visibility + cognitive identity are what make the Agent actually use it

## [5.8.0] — 2025-07-15

### Validated — Phase 56 E2E: Real Paper Review with Phase 52-55 Mechanisms

- `core/test_e2e_phase56_validation.py`: E2E test script — runs real paper review (Chan, Gentzkow, Yu 2019) and validates Phase 52-55 mechanisms
- **Results**: 13 turns, 134.4s, 12 findings, 7/42 sections read strategically
- **Phase 52 (Marginal Productivity)**: ✅ All findings have `recorded_at_turn`, density computable in reflect_and_plan
- **Phase 54 (Procedural Memory)**: ✅ ProceduralPattern class available for end_session extraction
- **Phase 55 (Stagnation Detection)**: ✅ `_check_stagnation` method present; not triggered because Agent maintained high output density (12 findings in 13 turns)
- **Phase 55 (CognitiveChecker)**: ✅ Triggered at Turn 12 mark_complete — nudged "缺少对结果的深入验证"
- **Cognitive Prompter**: ✅ Three distinct prompts fired (Turn 3, 4, 8)
- **Duplicate Detection**: ✅ Blocked 6 redundant findings (71-100% term overlap)
- **Fix**: Test script attribute name mismatch (`_stagnation_last_triggered` → `_last_stagnation_signal_turn`)
- **Output**: Production-quality referee report (Overall Assessment + Major/Minor Issues + Strengths + Questions for Authors)

## [5.7.0] — 2025-05-24

### Fixed — CognitiveChecker Persona Adaptation + Stagnation Self-Awareness (Phase 55)

#### Fix 1: CognitiveChecker Persona 适配
- `core/checker.py`: Added generic prompt templates (`POST_EDIT_CHECK_PROMPT_GENERIC`, `PRE_COMPLETION_CHECK_PROMPT_GENERIC`, `CONSISTENCY_CHECK_PROMPT_GENERIC`) — domain-neutral versions without academic-specific language
- `core/checker.py`: Added `PERSONA_TASK_CONTEXTS` mapping — `{"scholar": {"task_domain": "学术", "reviewer_role": "审稿人"}, "code_reviewer": {"task_domain": "代码", "reviewer_role": "代码审阅者"}}`
- `core/checker.py`: `CognitiveChecker.__init__` now accepts `persona` parameter (default: "scholar" for backward compatibility)
- `core/checker.py`: Added `set_persona()` method for runtime persona switching
- `core/checker.py`: All three check methods dynamically select prompt template based on persona
- `core/agent.py`: Passes `persona` to Harness constructor

#### Fix 2: 产出密度主动呈现（停滞自我感知）
- `core/harness.py`: Added `_check_stagnation(current_tool) -> str | None` method
- `core/harness.py`: Stagnation detection logic: skips meta tools, requires 6+ turns warmup, checks last 5 tool calls for `update_findings`, uses `recorded_at_turn` on findings for precise detection
- `core/harness.py`: 3-turn cooldown after triggering to avoid signal fatigue
- `core/harness.py`: Signal format follows §4.3 (data presentation, not instructions): `📉 产出观察: 最近 N 轮未产出新发现。当前共 X 条 findings，已读 Y 个 sections。`
- `core/harness.py`: `execute_tool` refactored — all branches use `result = ...` pattern, appends stagnation signal before returning

### Design Principles
- Persona adaptation: Same cognitive architecture, different domain vocabulary — eliminates false positives in non-academic scenarios
- §4.3 compliance: Stagnation signal presents facts ("最近 N 轮未产出"), not directives ("你应该切换策略") — Agent autonomously decides response
- Graceful degradation: Unknown persona falls back to empty context (no crash); stagnation detection is additive (no behavior change if not triggered)
- Minimal intervention: Cooldown + warmup prevent over-signaling; meta tools excluded to avoid interfering with legitimate reflection

### Tests
- 11-test suite (`tests/test_phase55_checker_stagnation.py`): default scholar persona, code_reviewer persona, dynamic set_persona, harness persona passthrough, warmup no-trigger, recent update_findings no-trigger, stagnation trigger on no-output, cooldown enforcement, meta tools exclusion, signal data-presentation verification, PERSONA_TASK_CONTEXTS completeness

## [5.6.0] — 2025-05-24

### Added — Procedural Memory: Layer 3 (Phase 54)
- `core/memory.py`: Added `ProceduralPattern` dataclass — records "HOW to work efficiently" knowledge (vs DomainPattern's "WHAT problems exist")
- `core/memory.py`: Three categories: `strategy_effectiveness` (策略切换时机), `tool_sequence` (高产工具序列), `anti_pattern` (低效行为模式)
- `core/memory.py`: `MemoryStore.add_or_reinforce_procedure()` — weighted-average reinforcement for effectiveness_score
- `core/memory.py`: `MemoryStore.get_relevant_procedures()` — ranked by effectiveness × evidence_count
- `core/memory.py`: `extract_procedural_patterns()` — automatic extraction from tool_call_history + strategy_transitions
- `core/memory.py`: Helper functions `_find_productive_sequences()` (3-gram analysis) and `_find_anti_patterns()` (repetition detection)
- `core/memory.py`: Updated `format_memory_context()` to include "⚡ 你的高效工作模式" section (Layer 3 injection, < 150 tokens)
- `core/memory.py`: Updated `_serialize()` / `_deserialize()` for procedures (backward compatible with v1.0)
- `core/memory.py`: Bumped MemoryState version to "1.1"
- `core/harness.py`: Added `_strategy_transitions` tracking in `reflect_and_plan`
- `core/harness.py`: Updated `end_session()` to call `extract_procedural_patterns` and persist Layer 3

### Design Principles
- Automatic extraction: Agent doesn't need to explicitly record procedural knowledge — it's mined from behavioral data
- Information presentation (§4.3): Injected as "你过去的有效策略", not as instructions — Agent autonomously decides whether to adopt
- Graceful degradation: System works perfectly without any procedural patterns
- Capacity limit: 50 patterns max, pruned by effectiveness × evidence_count

### Three-Layer Memory Architecture (Complete)
- Layer 1 — Session Memory: "上次审到哪了" (per-paper recall)
- Layer 2 — Domain Knowledge: "什么问题存在" (declarative, WHAT)
- Layer 3 — Procedural Memory: "如何高效工作" (procedural, HOW) ← **NEW**

### Tests
- 14-test suite (`tests/test_phase54_procedural_memory.py`): dataclass, add/reinforce, sorting, productive sequence detection, anti-pattern detection, strategy effectiveness, format_context, serialization roundtrip, backward compatibility, end_session integration, capacity limit, edge cases

## [5.5.0] — 2025-05-24

### Added — Task Generalization: CodeReviewer Persona (Phase 53)
- `core/identity.py`: Added `CODE_REVIEWER_IDENTITY` (~90 lines cognitive identity prompt for code review) and `CODE_REVIEWER_TOOLS` (7 tools: read_section, search_literature, update_findings, talk_to_user, review_findings, reflect_and_plan, mark_complete)
- `core/identity.py`: Registered `code_reviewer` in PERSONAS registry
- `core/agent.py`: Made `paper_path` optional (default `None`), added `content_sections` parameter for directly passing content segments without file loading
- `core/agent.py`: Updated class docstring to reflect multi-persona architecture

### Core Proof — Zero Changes to Engine
- `core/harness.py`: **ZERO modifications** — `paper_sections` dict naturally stores code files, all mechanisms (findings, reflect, marginal productivity, doom loop, compress_messages) are task-agnostic
- `core/loop.py`: **ZERO modifications** — cognitive loop engine is fully generic
- This proves the architecture's core thesis: behavior differences come entirely from identity + tools, not from the engine

### Known Limitation
- `CognitiveChecker` (Phase 50) uses academic-review prompts; in code review scenarios it may produce false-positive nudges. Checker persona adaptation is future work.

### Tests
- 12-test suite (`tests/test_phase53_task_generalization.py`): persona loading, harness compatibility, tool routing, findings, read_section, reflect_and_plan, quality gate, agent entry point, format_context, marginal productivity, cross-persona proof, template injection

## [5.4.0] — 2025-05-23

### Added — Paragraph-Level Structure Diagnosis (Phase 3 C-5)
- `tools/paragraph_diagnosis.py`: section-level structural analysis engine
- `ParagraphProfile` / `SectionStructureReport` dataclasses with health scoring
- Topic sentence, evidence, and transition detection (EN + ZH pattern banks)
- Claim-evidence alignment analysis per paragraph
- Structural role classification: claim / evidence / mixed / transition / unclear
- Section-wide issue detection (all-claims-no-evidence, missing transitions, orphan evidence)
- Auto-generated fix hints (prioritized, capped at configurable maximum)
- Health score: weighted formula balancing role diversity, alignment ratio, and transitions
- 27-test suite (`tests/test_paragraph_diagnosis.py`): patterns, alignment, section analysis, hints, scoring

## [5.3.0] — 2025-05-23

### Added — Re-Audit Severity Tracking & Revision Quality Scoring (Phase 3 C-4)
- `tools/reaudit.py` enhanced: `previous_severity`, `current_severity`, `severity_delta`, `revision_quality` fields
- `_severity_to_numeric()`: maps severity labels to ordinal scale for delta computation
- `_compute_revision_quality()`: aggregates per-finding deltas into 0-1 quality score
- `generate_revision_report()`: human-readable revision progress summary with per-finding change tracking
- 25-test suite (`tests/test_reaudit_enhanced.py`): severity mapping, quality scoring, delta computation, report generation

## [5.2.0] — 2025-05-23

### Added — Local Bibliography Search (Phase 3 C-3)
- Local .bib file parser (`tools/bib_search.py`): robust BibTeX/BibLaTeX parser handling braced/quoted values, multi-line fields, Zotero exports
- `BibLibrary` class: in-memory bibliography with file deduplication, directory scanning, singleton pattern
- Compact query language: `author:name year>=YYYY type:article has:doi keyword:X <free text>`
- Multi-signal relevance scoring: title match (0.5w) + abstract (0.3w) + keywords (0.2w) + venue + recency boost
- `find_relevant_uncited()`: recommends papers from user's library that are relevant but not yet cited
- `search_local_bibliography` tool: agent-callable interface for .bib search with auto-discovery
- `find_uncited_relevant` tool: agent-callable interface for citation gap detection
- Tool registration: schemas, dispatch handlers, metadata (both tools classified as low-risk read operations)
- 42-test suite (`tests/test_bib_search.py`): parser (11), query language (9), search (12), uncited (3), interface (4), registration (3)

## [5.1.0] — 2025-05-23

### Added — De-AI Rule Engine Upgrade (Phase 3 C-1)
- Structured YAML rule files per scene (`tools/deai/rules/`): s_general.yaml (12 universal rules), s1_cs_english.yaml (26 rules), s2_chinese.yaml (26 rules + 12 signal categories + 7 conflict resolutions), s3_economics.yaml (31 rules + 2 scene_overrides)
- Rule loader with caching (`tools/deai/rules/loader.py`): `SceneRules`, `Rule`, `SceneOverride` dataclasses; `load_scene_rules()`, `load_rules_for_audit()`, `get_scene_overrides()`; LRU cache via `_cache` dict
- Perplexity-aware detection (`tools/deai/perplexity.py`): bigram-based proxy scoring (zero-LLM-cost), `PerplexityScore`/`PerplexityReport` dataclasses, `analyze_perplexity()`, `get_perplexity_fix_hints()`
- Absolute hit count penalty in perplexity scoring: compensates for long sentences where ratio stays low despite multiple AI-pattern bigram hits
- Backward-compatible `_load_rules()` in `tools/deai/signals.py`: tries YAML loader first, falls back to legacy Markdown parser
- 36-test suite (`tests/test_deai_rules_engine.py`): covers loader (19 tests), perplexity detection (11 tests), integration (6 tests)

### Changed
- `tools/deai/__init__.py`: exports perplexity module and rules loader
- `tools/deai/signals.py`: `_load_rules()` now attempts structured YAML loading before Markdown fallback

## [5.0.0] — 2025-05-22

### Added — Unified 3-Tier Memory System (Wave 5 / Phase 3 C-6)
- Unified memory layer (`utils/memory/unified.py`): single interface replacing dual-island architecture
- MemoryTier classification: IDENTITY (90d half-life), PROJECT (14d), EPHEMERAL (2d)
- Exponential decay with reinforcement: `freshness_weight` auto-computed per entry
- Stale challenge/purge lifecycle: entries below 0.3 freshness are challenged then purged
- Tool pattern and implicit preference learning migrated from JSON to SQLite
- Phase-aware context injection via `get_context_for_phase()` with token budget
- Migration script (`utils/memory/migrate_v2.py`) for legacy session_memory JSON data
- 32-test comprehensive test suite (`tests/test_unified_memory.py`)

### Added — Tool Metadata Registry (C-7)
- Declarative tool metadata (`core/tool_metadata.py`): operation/scope/reversible/requires_confirmation for all 56 tools
- `assess_risk_level()`: automatic risk classification (high/medium/low) from metadata
- `_assess_risk_from_meta()` in action_router: metadata-driven fallback risk assessment
- 18-test coverage for metadata registry and router integration

### Added — Decision Observability (C-8)
- `DecisionTrace` dataclass: structured per-issue routing trace with why-not explanations
- `_build_decision_summary()`: one-line human-readable decision explanation (bid explanation style)
- `_get_meta_risk_for_category()`: maps issue categories → tool metadata risk levels
- JSONL trace writing to `.workspace/trace/routing_decisions.jsonl` (append, best-effort)
- `tools/decision_report.py`: post-pipeline decision summary generator
  - `generate_decision_report()`: aggregates routing traces into cohesive report
  - Score attribution (weighted heuristic: auto_fix=1.0, confirm_fix=0.6, guidance=0.1)
  - Capability boundary identification (issues that exceeded auto-handling)
  - `DecisionReport.save()`: outputs both JSON and Markdown
  - `format_decision_report_compact()`: single-paragraph inline summary
- 34-test coverage (`tests/test_decision_observability.py`)
- Interview narrative: "execution/decision separation" framework in INTERVIEW_PREP.md

### Changed
- `core/state.py`: added `unified_memory` instance (Wave 5)
- `core/agent_loop.py`: prefers unified memory context over legacy session_memory
- `main.py`: initializes UnifiedMemory at startup alongside legacy SessionMemory
- `tools/action_router.py`: imports and uses tool metadata for risk assessment

## [4.0.0] — 2025-05-22

### Added — 100% Agent Architecture (Wave 1–4)
- Phase state machine with 8 phases and auto-transitions (`utils/goal_tracker.py`)
- Phase-aware dynamic tool filtering: 15–25 tools visible per phase (`utils/phase_filter.py`)
- Persistent plan objects that survive context compression (`utils/plan_persistence.py`)
- Self-reflection injection at milestones and after errors (`utils/self_reflection.py`)
- Error recovery engine: classification → retry → fallback → circuit breaker → escalate (`utils/error_recovery.py`)
- Proactive context compression with CJK-aware token estimation and retention policies (`utils/context_manager.py`)
- Output quality gate for rewrite/deai/review validation (`utils/output_quality.py`)
- Adaptive strategy engine: paper-aware automatic configuration (`utils/adaptive_strategy.py`)
- Cross-session memory: journals, tool patterns, preference inference (`utils/session_memory.py`)
- Meta-planner: historical pattern advice for tool sequencing (`utils/meta_planner.py`)
- Learning tools: `record_lesson` + `observe_edit` for real-time preference capture
- Streaming output via async generator + `/pause` `/resume` `/takeover` REPL
- On-demand strategy guidelines loaded via `read_agent_guidelines` tool

### Changed
- System prompt slimmed from ~2000 tokens to ~50 lines (guidelines moved to tools)
- main.py refactored: 2529-line monolith → 250-line entry + `core/` (6 modules) + `handlers/` (6 modules)

## [3.2.0] — 2025-05-21

### Added — Agent Architecture Refactor
- Unified voice drift detection (single source: `utils/voice_profile.py`)
- Author Profile → De-AI fix injection (learned rules auto-applied)
- Centralized thresholds (`config/thresholds.yaml` replaces 50+ magic numbers)
- De-AI engine split from God module into `tools/deai/` package (5 submodules)
- 4-layer JSON parsing recovery (`utils/json_repair.py`)
- Checkpoint + Resume for long pipelines (`utils/checkpoint.py`)
- Dry Run cost/time estimation (9 operation profiles × 3 model tiers)
- Dynamic focus point generation (paper-specific → per-reviewer)
- Impact Estimator tool (pre-edit risk assessment)
- Intent Classifier tool (user intent disambiguation)
- Multimodal figure/table analysis via vision model
- Literature verification via Semantic Scholar + CrossRef
- Citation synergy layer (unified citation_graph + literature_verify)
- Review Engine configurable: reviewer_count, focus_dimensions, custom_criteria
- Listwise comparative scoring (force differentiation when scores cluster)

### Changed
- Split `closed_loop_fix` into 4 independent agent-orchestrated pipeline tools
- Structured warnings from tools (errors no longer silently swallowed)

## [3.0.0] — 2025-05-20

### Added — Quality & Efficiency
- Voice Profile: quantify author style, inject into rewrite constraints
- Author Profile: cross-session memory of style preferences + rejected patterns
- Section-level parallel processing (concurrent rewrite, max parallelism 3)
- Gold standard sedimentation (few-shot examples from past successes)
- Doom loop detection + tool result recall cache (TTL-based)
- Model routing: 3-tier (HIGH/MEDIUM/LOW) based on task complexity
- Per-issue token budget calculator
- Structured execution trace (`.workspace/trace.jsonl`)
- De-AI pre-check gate (L1 regex/stats, saves 80%+ LLM calls)
- Skill registry with frontmatter-based auto-suggestion
- Burstiness hard-validation post-fix (CV≥0.35)
- Full S1/S2/S3 + S_GENERAL rules (perplexity awareness included)
- 12-discipline English keyword field detector

## [2.0.0] — 2025-05-19

### Added — Core Agent Capabilities
- Issue-based action routing: `auto_fix` / `confirm_fix` / `guidance`
- Red Line enforcement (code-level, not prompt-level)
- Budget-aware mode (full / medium / minimal)
- De-AI audit: independent post-rewrite PEV Loop (max 2 retries)
- Stata MCP statistical verification (graceful degradation)
- Section-level paper parsing (PDF/LaTeX → filesystem)
- Multi-role parallel review (5 reviewers with isolated context)
- Context compression (micro_compact: old results → placeholders)
- Human-in-the-loop via `ask_user` tool
- File-based working memory (`.workspace/revision_state.json`)

## [1.0.0] — 2025-05-18

### Added — Initial Release
- Basic agent loop with LLM tool calling
- Paper parsing and section extraction
- Single-pass review and rewrite
- Simple prompt-based guidance
