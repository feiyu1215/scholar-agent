"""
core/tool_schemas.py — Tool schema definitions for ScholarAgent.

All tool definitions (name, description, input_schema) live here.
The agent_loop and TOOL_HANDLERS reference these by name.
"""

TOOLS = [
    # -- Agent Self-Navigation --
    {
        "name": "read_agent_guidelines",
        "description": "Load a detailed behavioral guideline by topic. Available topics: planning, tool_selection, iteration_protocol, deai_strategy, budget_rules. Call this BEFORE starting a workflow that matches the topic. Returns the full guideline text. Zero cost.",
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "Guideline topic to load",
                    "enum": ["planning", "tool_selection", "iteration_protocol", "deai_strategy", "budget_rules"],
                },
            },
            "required": ["topic"],
        },
    },
    # -- Paper Management --
    {
        "name": "parse_paper",
        "description": "Parse a PDF/tex/md paper into section-level files. Stores in .workspace/paper/. Returns a section index summary with IDs, titles, and word counts. ALWAYS call this first when user provides a paper. After this, call build_voice_profile.",
        "input_schema": {
            "type": "object",
            "properties": {
                "paper_path": {"type": "string", "description": "Path to the paper file (PDF, tex, md, txt)"},
            },
            "required": ["paper_path"],
        },
    },
    {
        "name": "read_section_index",
        "description": "Read the section index showing all sections with IDs, titles, and word counts. Use to understand paper structure without loading content. Returns JSON array. Do NOT call read_section on every section. Use this for overview.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "read_section",
        "description": "Read ONE section by ID. Returns that section content (revised version if exists). Use for targeted reads when you need actual text. Output: markdown text of the section.",
        "input_schema": {
            "type": "object",
            "properties": {
                "section_id": {"type": "string", "description": "Section ID from index (e.g., '01_abstract', '03_methodology')"},
            },
            "required": ["section_id"],
        },
    },
    {
        "name": "diff_section",
        "description": "Show unified diff between original and revised version of a section. Use after rewrite to verify changes. Output: unified diff text, or 'no revision exists'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "section_id": {"type": "string"},
            },
            "required": ["section_id"],
        },
    },
    {
        "name": "read_revision_log",
        "description": "Read the revision log showing all changes made, when, and why. Optionally filter by section. Output: formatted log entries.",
        "input_schema": {
            "type": "object",
            "properties": {
                "section_id": {"type": "string", "description": "Optional: filter by section ID"},
            },
        },
    },
    # -- Structural Analysis (cheap, run first) --
    {
        "name": "architecture_diagnosis",
        "description": "Diagnose paper structural skeleton BEFORE sentence-level work. Detects 6 failure modes: missing_gap, claim_without_evidence, evidence_without_claim, results_discussion_contamination, missing_boundary, hourglass_violation. Zero LLM cost. Output: JSON with failure_modes[], paper_type, section_responsibilities. Use BEFORE review_paper.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "presubmission_check",
        "description": "Zero-LLM-cost mechanical checks catching desk-reject issues: citation format mixing, figure/table reference gaps, abstract structure, missing sections. Run BEFORE review_paper. Output: JSON with checks[], pass_count, fail_count, warnings.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "consistency_check",
        "description": "Quick scan across all sections for logical flow issues. Reads first/last lines of each section. Zero LLM cost. Output: list of inconsistencies found.",
        "input_schema": {"type": "object", "properties": {}},
    },
    # -- Multi-Role Review --
    {
        "name": "review_paper",
        "description": "Run multi-role review with parallel reviewers (editor, theory, methodology, logic, literature). Each reviewer sees only relevant sections. Expensive (up to 5 LLM calls). Output: JSON with issues[] (each has id, severity, category, section_id, description, suggestion), overall_score, roadmap. Do NOT call for minor requests. Use targeted tools instead. Supports optional reviewer_count to limit reviewers, focus_dimensions to direct attention, and custom_criteria for additional evaluation criteria.",
        "input_schema": {
            "type": "object",
            "properties": {
                "reviewer_count": {
                    "type": "integer",
                    "description": "Number of reviewers to use (1-5). If omitted, all 5 run. For short drafts, 2-3 is sufficient.",
                },
                "focus_dimensions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Aspects to focus on, e.g. ['clarity', 'methodology', 'novelty', 'structure', 'logic', 'literature']. Influences reviewer selection and prompts.",
                },
                "custom_criteria": {
                    "type": "string",
                    "description": "Free-form additional criteria for reviewers to consider (e.g. 'Check if the sample size justification is adequate').",
                },
                "calibrate_scores": {
                    "description": "Controls listwise calibration to combat score clustering. true = statistical calibration (free), 'llm' = LLM-based comparative calibration (1 extra call), false = no calibration (legacy).",
                    "default": True,
                },
            },
        },
    },
    {
        "name": "route_issues",
        "description": "Route review issues through Red Line checks and budget ceiling. Each issue gets effective_action (auto_fix/confirm_fix/guidance). Call AFTER review_paper. Output: routing report with per-issue actions and stats.",
        "input_schema": {
            "type": "object",
            "properties": {
                "budget": {"type": "string", "enum": ["full", "medium", "minimal"],
                           "description": "Budget override (default: session budget)"},
            },
        },
    },
    {
        "name": "generate_fix_proposal",
        "description": "Generate fix proposal for one issue WITHOUT executing. For confirm_fix: shows before/after text. For guidance: gives instructions. Call AFTER route_issues. Output: JSON with current_text, proposed_text, rationale.",
        "input_schema": {
            "type": "object",
            "properties": {
                "issue_id": {"type": "string", "description": "Issue ID (e.g., 'ISS-001')"},
                "section_id": {"type": "string", "description": "Optional: override section (auto-detected from issue if omitted)"},
            },
            "required": ["issue_id"],
        },
    },
    {
        "name": "approve_fix",
        "description": "Execute an approved fix proposal. Marks the issue done and learns the preference (future same-category auto_fix). Call AFTER user approves a generate_fix_proposal result.",
        "input_schema": {
            "type": "object",
            "properties": {
                "issue_id": {"type": "string", "description": "Issue ID to approve"},
            },
            "required": ["issue_id"],
        },
    },
    {
        "name": "revision_progress",
        "description": "Show revision progress: issues done/pending/failed, de-AI audit results, Stata verification status. Output: formatted progress report.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "read_issues",
        "description": "Read raw review issues from .workspace/review/issues.json. Use when you need to inspect issue details. Output: JSON array of all issues.",
        "input_schema": {"type": "object", "properties": {}},
    },
    # -- Literature and Citation --
    {
        "name": "search_literature",
        "description": "Search Semantic Scholar + CrossRef for academic papers. Use to: verify a cited paper exists, find related work for gap analysis, check paper metadata. Output: JSON array with title, authors, year, venue, doi, citation_count. If 0 results: try shorter query or partial title. Do NOT retry same query.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query (paper title, author, or topic keywords). Be specific."},
                "limit": {"type": "integer", "description": "Max results (default: 5, max: 20)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "verify_doi",
        "description": "Verify a specific DOI via CrossRef API. Returns title, authors, year, venue if valid. Use for citation spot-checks. Output: metadata dict if valid, error message if invalid. If verification fails due to network, report as unverifiable. Do NOT remove the citation.",
        "input_schema": {
            "type": "object",
            "properties": {
                "doi": {"type": "string", "description": "DOI to verify (e.g., '10.1145/1234567.1234568')"},
            },
            "required": ["doi"],
        },
    },
    {
        "name": "verify_citations",
        "description": "Batch-verify the reference list: existence, year accuracy, venue accuracy, DOI validity, inline consistency. Network-dependent. Output: structured report with per-citation confidence and overall stats.",
        "input_schema": {
            "type": "object",
            "properties": {
                "max_citations": {"type": "integer", "description": "Max citations to verify (default: all). Lower for quick spot-check."},
            },
        },
    },
    {
        "name": "check_citation_content",
        "description": "Detect overclaim language in citation usage. Checks: strong language (proves vs suggests), working-paper-as-established, missing hedging. Pure rule-based, no network. Output: list of overclaim findings with severity.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "check_citation_alignment",
        "description": "Score claim-citation alignment on 5 dimensions: specificity, temporal coherence, hedging, claim-type fit, contextual proximity. Zero LLM cost, rule-based. Output: per-citation scores + actionable recommendations.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "verify_and_enrich_citations",
        "description": "Unified citation synergy tool combining citation_graph and literature_verify. Extracts all citation mentions from text, cross-references with bibliography, identifies: missing citations (referenced but not in bib), orphan entries (in bib but never cited), suspicious author/year combinations, overclaim language, and claim-citation alignment issues. Returns coverage score (0-1) and actionable suggestions. Zero LLM cost. Use for comprehensive one-shot citation health check.",
        "input_schema": {
            "type": "object",
            "properties": {
                "bibliography": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "authors": {"type": "array", "items": {"type": "string"}},
                            "title": {"type": "string"},
                            "year": {"type": "integer"},
                            "venue": {"type": "string"},
                            "doi": {"type": "string"},
                        },
                    },
                    "description": "Optional pre-parsed bibliography. If omitted, references are auto-parsed from the paper text.",
                },
            },
        },
    },
    # -- Revision and Writing --
    {
        "name": "rewrite_section",
        "description": "[Legacy] Monolithic rewrite — prefer the 3-step pipeline (generate_rewrite → commit_rewrite → verify_rewrite_quality) for better control. This tool performs all three steps atomically with no intermediate inspection. Use only for quick single-shot rewrites where you don't need to review the proposal first. NOT available in minimal budget.",
        "input_schema": {
            "type": "object",
            "properties": {
                "section_id": {"type": "string", "description": "Section to rewrite"},
                "custom_instructions": {"type": "string", "description": "Optional user-specified focus or constraints"},
            },
            "required": ["section_id"],
        },
    },
    # -- Atomized Rewrite Pipeline (v4: preferred over monolithic rewrite_section) --
    {
        "name": "generate_rewrite",
        "description": "Step 1/3: Generate a rewrite proposal WITHOUT saving. Returns JSON with proposed_text, changes_summary, token stats. Use this to inspect the proposed rewrite before committing. If unsatisfied, call again with different custom_instructions. NOT available in minimal budget.",
        "input_schema": {
            "type": "object",
            "properties": {
                "section_id": {"type": "string", "description": "Section to rewrite"},
                "custom_instructions": {"type": "string", "description": "Optional focus/constraints for this rewrite attempt"},
            },
            "required": ["section_id"],
        },
    },
    {
        "name": "commit_rewrite",
        "description": "Step 2/3: Commit a proposed rewrite to filesystem. Call ONLY after reviewing generate_rewrite output. Saves to .workspace/revisions/, logs change, updates score tracker. Returns confirmation JSON. NOT available in minimal budget.",
        "input_schema": {
            "type": "object",
            "properties": {
                "section_id": {"type": "string", "description": "Section ID being committed"},
                "proposed_text": {"type": "string", "description": "The full revised text to save (from generate_rewrite output)"},
                "changes_summary": {"type": "string", "description": "Brief summary of changes (from generate_rewrite output)"},
            },
            "required": ["section_id", "proposed_text"],
        },
    },
    {
        "name": "verify_rewrite_quality",
        "description": "Step 3/3: Verify quality of a committed rewrite. Runs de-AI audit + post-edit verification. Returns JSON with overall_passed, deai_verdict, post_edit_verdict, fix_hints[]. If failed, fix_hints tells you what to try next. Call AFTER commit_rewrite.",
        "input_schema": {
            "type": "object",
            "properties": {
                "section_id": {"type": "string", "description": "Section to verify"},
            },
            "required": ["section_id"],
        },
    },
    {
        "name": "edit_section",
        "description": "Surgical string replacement within a section. Use for small targeted fixes (1-2 sentences). NOT available in minimal budget. old_text must be an exact match. Output: confirmation with line changed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "section_id": {"type": "string"},
                "old_text": {"type": "string", "description": "Exact text to find and replace"},
                "new_text": {"type": "string", "description": "Replacement text"},
                "reason": {"type": "string", "description": "Why this edit is needed (logged)"},
            },
            "required": ["section_id", "old_text", "new_text", "reason"],
        },
    },
    {
        "name": "parallel_rewrite",
        "description": "Rewrite multiple INDEPENDENT sections concurrently (max 3). Validates independence (no cross-references). Conflicting sections queued sequentially. Output: per-section rewrite summary. NOT for sections that reference each other.",
        "input_schema": {
            "type": "object",
            "properties": {
                "section_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Section IDs to rewrite in parallel (max 3)",
                },
                "custom_instructions": {"type": "string", "description": "Optional shared instructions"},
            },
            "required": ["section_ids"],
        },
    },
    # -- De-AI Quality Assurance --
    {
        "name": "deai_audit",
        "description": "Standard AI signal detection on a section. Returns naturalness score (0-1) and detected signals. Run AFTER rewrite_section, not before. If score >= 0.8: section is clean. If < 0.7: use deai_closed_loop for deeper fix. Scene (S1/S2/S3) is auto-detected from paper metadata — you do NOT need to specify it. Output: JSON with score, signals[], suggestions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "section_id": {"type": "string", "description": "Section to audit"},
                "scene": {"type": "string", "enum": ["S1", "S2", "S3"], "description": "Override auto-detection. S1=CS English, S2=Chinese academic, S3=Economics. Usually omit this — auto-detected from paper metadata."},
            },
            "required": ["section_id"],
        },
    },
    {
        "name": "deai_closed_loop",
        "description": "Enhanced de-AI with four-step closed loop (detect, diagnose, rewrite, verify) and 4-layer self-check (structure/rhythm/forbidden/voice). More thorough than deai_audit. Use when deai_audit score < 0.7 or user explicitly requests deep de-AI. Scene is auto-detected — you do NOT need to specify it. Output: final score, fixes applied, self-check results.",
        "input_schema": {
            "type": "object",
            "properties": {
                "section_id": {"type": "string", "description": "Section to fix"},
                "scene": {"type": "string", "enum": ["S1", "S2", "S3"], "description": "Override auto-detection. S1=CS English, S2=Chinese academic, S3=Economics. Usually omit this."},
            },
            "required": ["section_id"],
        },
    },
    {
        "name": "build_voice_profile",
        "description": "Analyze original paper sections to build a voice fingerprint (sentence length, passive ratio, hedging, transitions). Run ONCE after parse_paper. Constraints auto-injected into future rewrites. Output: profile metrics + constraints summary.",
        "input_schema": {"type": "object", "properties": {}},
    },
    # -- De-AI Pipeline (Agent-orchestrated, independent steps) --
    {
        "name": "deai_detect",
        "description": "Step 1 of de-AI pipeline: Detect AI signals in text. Returns signals with dimension scores and overall naturalness score. Use this when you want to assess AI-ness without automatically fixing. Agent decides next step based on results. Output: JSON with signals[], dimension_scores, overall_score, is_natural, summary.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The text to audit for AI writing signals"},
                "scene": {"type": "string", "enum": ["S1", "S2", "S3"], "description": "S1=CS English, S2=Chinese academic, S3=Economics. Default: S1"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "deai_diagnose",
        "description": "Step 2 of de-AI pipeline: Analyze detected signals to produce diagnosis and fix strategies. Takes signals from deai_detect and returns root causes, fix strategies, and priority ordering. Agent can filter which signals to fix based on this. Output: JSON with diagnosis[], fix_strategy[], priority_order[].",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The original text containing the signals"},
                "signals": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Signal list from deai_detect output (the 'signals' field)",
                },
                "scene": {"type": "string", "enum": ["S1", "S2", "S3"], "description": "Scene context. Default: S1"},
            },
            "required": ["text", "signals"],
        },
    },
    {
        "name": "deai_rewrite",
        "description": "Step 3 of de-AI pipeline: Apply fixes to text according to strategy. Takes text and a list of signals to fix (Agent can filter/modify from diagnose step). Returns revised text and changes made. Agent controls which signals get fixed. Output: JSON with revised_text, changes_made[], warning.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The text to rewrite"},
                "fix_strategy": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Signals to fix (from deai_detect or deai_diagnose output). Each needs: sentence, signal_type, confidence, fix_suggestion.",
                },
                "scene": {"type": "string", "enum": ["S1", "S2", "S3"], "description": "Scene context. Default: S1"},
                "author_constraints": {"type": "string", "description": "Optional constraints for the rewrite (e.g., 'keep under 25 words per sentence')"},
            },
            "required": ["text", "fix_strategy"],
        },
    },
    {
        "name": "deai_verify",
        "description": "Step 4 of de-AI pipeline: Verify the rewrite didn't introduce regressions. Runs 4-layer self-check (structure/rhythm/forbidden/voice) and computes score delta. Use after deai_rewrite to confirm quality. Output: JSON with passed, new_score, delta, voice_drift, self_check, warnings[].",
        "input_schema": {
            "type": "object",
            "properties": {
                "original_text": {"type": "string", "description": "The text BEFORE rewriting (for comparison)"},
                "revised_text": {"type": "string", "description": "The text AFTER rewriting (to verify)"},
                "scene": {"type": "string", "enum": ["S1", "S2", "S3"], "description": "Scene context. Default: S1"},
            },
            "required": ["original_text", "revised_text"],
        },
    },
    # -- Statistical and Specialized --
    {
        "name": "stata_verify",
        "description": "Run Stata statistical verification for methodology issues. Generates .do code, attempts execution, interprets results. If Stata unavailable: outputs .do code as guidance. Output: verification result + .do code.",
        "input_schema": {
            "type": "object",
            "properties": {
                "issue_id": {"type": "string", "description": "Methodology issue ID to verify"},
            },
            "required": ["issue_id"],
        },
    },
    {
        "name": "analyze_figures",
        "description": "Analyze paper figures for claim alignment: axis labels, caption-claim consistency, statistical visualization. Requires figure files in workspace. Output: per-figure assessment.",
        "input_schema": {
            "type": "object",
            "properties": {
                "figure_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional: specific figure IDs. If omitted, analyzes all.",
                },
            },
        },
    },
    # -- Iterative Review --
    {
        "name": "save_previous_issues",
        "description": "Snapshot current review issues as baseline for future re-audit comparison. Call BEFORE the author revises. Output: confirmation with issue count saved.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "reaudit",
        "description": "Compare current vs. previous review to track revision progress. Matches issues by root_cause_key, classifies as FULLY_ADDRESSED/PARTIALLY_ADDRESSED/NOT_ADDRESSED/NEW. Output: JSON with comparison[], improvement_rate, summary.",
        "input_schema": {
            "type": "object",
            "properties": {
                "previous_issues_path": {"type": "string", "description": "Optional: path to previous issues JSON. Default: .workspace/review/previous_issues.json"},
            },
        },
    },
    {
        "name": "show_author_profile",
        "description": "Show learned author preferences: approved/rejected categories, explicit style preferences, interaction history. Output: formatted profile summary.",
        "input_schema": {"type": "object", "properties": {}},
    },
    # -- User Interaction --
    {
        "name": "ask_user",
        "description": "Pause and ask the user a question. MUST use when: (1) user expresses uncertainty ('不确定', 'not sure', 'I don't know which'), (2) user presents a dilemma between options, (3) user asks for your opinion/recommendation on a choice, (4) scope is genuinely unclear. Do NOT use for routine technical decisions you can make autonomously. Output: the user response text.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "What to ask the user (be specific about options)"},
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional: suggested options for user to pick from",
                },
            },
            "required": ["message"],
        },
    },
    {
        "name": "load_skill",
        "description": "Load domain-specific knowledge on demand. Available: review_criteria, econ_writing, deai_rules, chinese_academic_standards, data_availability, section_responsibility. Load when paper domain requires specialized knowledge. Output: skill content (guidelines text, max 4000 chars).",
        "input_schema": {
            "type": "object",
            "properties": {
                "skill_name": {"type": "string", "description": "Skill to load (e.g., 'econ_writing', 'chinese_academic_standards')"},
            },
            "required": ["skill_name"],
        },
    },
    # -- Dry Run / Cost Estimation --
    {
        "name": "dry_run_estimate",
        "description": "Estimate the cost/time of a multi-step plan BEFORE executing it. Shows estimated LLM calls, tokens, cost, and time for each operation. Use this to help users decide whether to proceed, adjust scope, or pick a cheaper approach. Output: formatted human-readable report with per-step and total estimates.",
        "input_schema": {
            "type": "object",
            "properties": {
                "operations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "operation": {"type": "string", "description": "Operation name (e.g., 'review_paper', 'deai_full_pipeline', 'rewrite_section', 'presubmission_check')"},
                            "text_length_words": {"type": "integer", "description": "Approximate word count of input text (scales token estimate)"},
                            "section_count": {"type": "integer", "description": "Number of sections (for parallel operations)"},
                            "reviewer_count": {"type": "integer", "description": "Number of reviewers (for review operations)"},
                        },
                        "required": ["operation"],
                    },
                    "description": "List of operations to estimate. Each needs 'operation' name, optionally 'text_length_words', 'section_count', 'reviewer_count'.",
                },
            },
            "required": ["operations"],
        },
    },
    {
        "name": "estimate_single_operation",
        "description": "Estimate the cost/time of a SINGLE operation. Simpler than dry_run_estimate when you only need one operation's estimate. Output: formatted report for one operation.",
        "input_schema": {
            "type": "object",
            "properties": {
                "operation": {"type": "string", "description": "Operation name (e.g., 'review_paper', 'deai_closed_loop', 'rewrite_section')"},
                "text_length_words": {"type": "integer", "description": "Approximate word count of input text"},
                "section_count": {"type": "integer", "description": "Number of sections (for parallel operations)"},
                "reviewer_count": {"type": "integer", "description": "Number of reviewers (for review operations)"},
            },
            "required": ["operation"],
        },
    },
    # -- Checkpoint / Resume --
    {
        "name": "list_checkpoints",
        "description": "List all in-progress pipeline checkpoints that can be resumed. Returns run_id, pipeline_name, status, progress, and metadata for each resumable checkpoint.",
        "input_schema": {"type": "object", "properties": {}},
    },
    # -- Dynamic Focus Point Generation --
    {
        "name": "generate_focus_points",
        "description": "Generate paper-specific review focus points BEFORE detailed review. Scans paper metadata and section summaries to produce: (1) focus points unique to THIS paper, (2) potential confusion areas where terminology/claims overlap, (3) methodology-specific verification checklists. Output is injected into reviewer prompts to improve discrimination and reduce generic feedback. Zero LLM cost. Run AFTER parse_paper, BEFORE review_paper.",
        "input_schema": {
            "type": "object",
            "properties": {
                "paper_metadata": {
                    "type": "object",
                    "description": "Paper metadata: {title, abstract, paper_type, field, word_count}. If omitted, loaded from workspace.",
                },
                "section_summaries": {
                    "type": "object",
                    "description": "Section summaries keyed by role: {introduction: '...', methodology: '...', results: '...', ...}. If omitted, auto-extracted from parsed sections.",
                },
                "detected_methods": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of detected methods (e.g., ['DID', 'IV', 'PSM']). Auto-detected if omitted.",
                },
            },
        },
    },
    # -- Agent Orchestration (granular review control) --
    {
        "name": "run_single_reviewer",
        "description": "Run a single reviewer role from the review pipeline. Lets the agent run reviewers one at a time, inspect results, and decide whether to continue. Returns that reviewer's issues list.",
        "input_schema": {
            "type": "object",
            "properties": {
                "reviewer_role": {
                    "type": "string",
                    "enum": ["editor", "methodology", "theory", "logic", "literature"],
                    "description": "Which reviewer role to run.",
                },
                "focus_dimensions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional focus aspects for this reviewer.",
                },
                "custom_criteria": {
                    "type": "string",
                    "description": "Optional additional criteria for this reviewer.",
                },
            },
            "required": ["reviewer_role"],
        },
    },
    {
        "name": "consolidate_reviews",
        "description": "Consolidate saved reviewer outputs into a final assessment. Loads reviewer_*.json from .workspace/review/, runs LLM consolidation + calibration + post-processing. Use after running individual reviewers.",
        "input_schema": {
            "type": "object",
            "properties": {
                "calibrate_scores": {
                    "type": "boolean",
                    "description": "Whether to apply listwise score calibration (default true).",
                    "default": True,
                },
            },
        },
    },
    # -- Session Status --
    {
        "name": "session_status",
        "description": "Show current session progress: sections processed, pending issues, budget consumption, active checkpoints.",
        "input_schema": {"type": "object", "properties": {}},
    },
    # -- Goal & Plan Management (Wave 2) --
    {
        "name": "set_goal",
        "description": "Register a session goal to track. Call this after understanding what the user wants to accomplish. The goal tracker will monitor progress and phase transitions. Returns goal ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "description": {"type": "string", "description": "What the user wants to achieve (e.g., 'Review paper and fix all major issues')"},
            },
            "required": ["description"],
        },
    },
    {
        "name": "complete_goal",
        "description": "Mark a goal as completed. Call when the goal has been fully achieved. Advances session phase to done if all goals are complete.",
        "input_schema": {
            "type": "object",
            "properties": {
                "goal_id": {"type": "string", "description": "Goal ID to mark complete (e.g., 'G01')"},
                "note": {"type": "string", "description": "Brief completion summary"},
            },
            "required": ["goal_id"],
        },
    },
    {
        "name": "save_plan",
        "description": "Persist an execution plan to disk for recovery. Call after creating a <plan>...</plan> to ensure it survives context compression. Returns plan ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "goal": {"type": "string", "description": "What this plan achieves"},
                "plan_text": {"type": "string", "description": "The plan text (numbered steps with tool/dependency info)"},
            },
            "required": ["goal", "plan_text"],
        },
    },
    {
        "name": "load_plan",
        "description": "Load a persisted plan (useful after context compression). Returns the plan with current progress. If no plan_id given, loads the most recent active plan.",
        "input_schema": {
            "type": "object",
            "properties": {
                "plan_id": {"type": "string", "description": "Optional: specific plan ID to load"},
            },
        },
    },
    {
        "name": "advance_plan",
        "description": "Mark a plan step as completed and advance to next. Call after successfully executing a plan step.",
        "input_schema": {
            "type": "object",
            "properties": {
                "plan_id": {"type": "string", "description": "Plan ID"},
                "step_index": {"type": "integer", "description": "0-based index of the completed step"},
                "result_summary": {"type": "string", "description": "Brief outcome of this step"},
                "success": {"type": "boolean", "description": "Whether the step succeeded (default true)"},
            },
            "required": ["plan_id", "step_index"],
        },
    },
    {
        "name": "self_critique",
        "description": "Trigger a self-reflection checkpoint. Use when: (1) you're unsure about next steps, (2) a tool returned unexpected results, (3) you want to reassess goal alignment. Returns a structured reflection prompt. Low cost.",
        "input_schema": {"type": "object", "properties": {}},
    },
    # -- Learning & Memory (Wave 4) --
    {
        "name": "record_lesson",
        "description": "Record a lesson learned during this session for future reference. Call when: (1) a tool sequence produced unexpectedly good/bad results, (2) you discover a user preference, (3) you find an effective approach worth remembering. Persists across sessions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "lesson": {"type": "string", "description": "What was learned (e.g., 'User prefers shorter sentences in methods section')"},
                "category": {"type": "string", "enum": ["tool_pattern", "user_preference", "approach", "pitfall"], "description": "Category of lesson"},
            },
            "required": ["lesson", "category"],
        },
    },
    {
        "name": "observe_edit",
        "description": "Record a user's manual edit for preference learning. Call when the user modifies or corrects your output and you want to learn their preference for next time.",
        "input_schema": {
            "type": "object",
            "properties": {
                "original": {"type": "string", "description": "What you (the agent) wrote"},
                "edited": {"type": "string", "description": "What the user changed it to"},
            },
            "required": ["original", "edited"],
        },
    },
    # -- Local Bibliography Search (C-3) --
    {
        "name": "search_local_bibliography",
        "description": "Search the user's local .bib file for relevant references. Supports compact query syntax (e.g., 'author:zhang year>=2022 transformer attention') and free-text topic search. Use this BEFORE suggesting new citations — check if the user already has relevant papers in their library. Returns formatted results with citation keys.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query. Supports: free text, author:name, year:YYYY, year>=YYYY, year<=YYYY, type:article, venue:name, has:doi/abstract/url/keywords, keyword:term.",
                },
                "bib_path": {
                    "type": "string",
                    "description": "Path to .bib file or directory. If omitted, auto-discovers .bib files in workspace.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results to return (default 10).",
                    "default": 10,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "find_uncited_relevant",
        "description": "Find papers in user's .bib library that are relevant to a topic but NOT yet cited in the paper. Use when a reviewer flags 'missing citations' — recommends specific papers the user already has. Returns citation keys + relevance scores.",
        "input_schema": {
            "type": "object",
            "properties": {
                "cited_keys": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of citation keys already used in the paper (e.g., ['zhang2023', 'vaswani2017']).",
                },
                "topic": {
                    "type": "string",
                    "description": "Topic or keywords to match against (e.g., 'transformer attention mechanism').",
                },
                "bib_path": {
                    "type": "string",
                    "description": "Path to .bib file or directory. If omitted, auto-discovers.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max recommendations (default 5).",
                    "default": 5,
                },
            },
            "required": ["cited_keys", "topic"],
        },
    },
    # --- LaTeX / Bibliography Verification (C-2) ---
    {
        "name": "latex_verify",
        "description": "Verify LaTeX compilation for the paper project. Runs latexmk in draft mode and reports errors/warnings from the .log file. If LaTeX is not installed, outputs manual compilation guidance. Use after making changes that could break compilation (e.g., adding packages, modifying math, restructuring sections).",
        "input_schema": {
            "type": "object",
            "properties": {
                "tex_path": {
                    "type": "string",
                    "description": "Path to the main .tex file. If omitted, auto-discovers in workspace.",
                },
                "project_dir": {
                    "type": "string",
                    "description": "Directory containing the LaTeX project. Defaults to .workspace/paper/.",
                },
                "draft_mode": {
                    "type": "boolean",
                    "description": "If true, skip PDF generation (faster). Default true.",
                    "default": True,
                },
            },
            "required": [],
        },
    },
    {
        "name": "bib_verify",
        "description": "Verify bibliography completeness and citation consistency. Checks .bib entries for required fields, finds undefined references (cited but not in .bib), and orphaned entries (in .bib but never cited). Use when reviewer flags citation issues or before submission.",
        "input_schema": {
            "type": "object",
            "properties": {
                "bib_path": {
                    "type": "string",
                    "description": "Path to .bib file. If omitted, auto-discovers in workspace.",
                },
                "tex_path": {
                    "type": "string",
                    "description": "Path to main .tex file for citation extraction. If omitted, auto-discovers.",
                },
                "project_dir": {
                    "type": "string",
                    "description": "Directory to search. Defaults to .workspace/paper/.",
                },
                "check_orphaned": {
                    "type": "boolean",
                    "description": "Whether to report uncited entries. Default true.",
                    "default": True,
                },
            },
            "required": [],
        },
    },
]

# Public alias for external access (e.g., from tools.deai_pipeline or tests)
tool_schemas = TOOLS
