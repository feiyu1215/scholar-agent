# Budget Mode Rules

> Load this guideline via `read_agent_guidelines("budget_rules")` when making decisions about tool execution.

## Three Budget Modes

| Mode | Rewrites? | Confirmations? | Guidance? | Target Use Case |
|------|-----------|---------------|-----------|-----------------|
| full | Yes (auto) | Yes (ask user) | Yes | Full autonomous revision |
| medium | Yes (auto) | No (skip) | Yes | Faster autonomous revision |
| minimal | **No** | No | Yes only | Review + advice, zero edits |

## Mode-Specific Behavior

### full mode
- `auto_fix` issues: execute rewrite/edit without asking
- `confirm_fix` issues: show proposed fix, ask user to approve
- `guidance` issues: output instructions for manual fix
- De-AI: run full pipeline
- Max cost: unlimited (user accepted)

### medium mode
- `auto_fix` issues: execute rewrite/edit without asking
- `confirm_fix` issues: downgrade to guidance (show what to do, don't ask)
- `guidance` issues: output instructions
- De-AI: run pipeline but skip 2nd pass
- Max cost: moderate (skip expensive retries)

### minimal mode
- **ALL tools that modify paper content are BLOCKED**
- Blocked tools: `rewrite_section`, `edit_section`, `approve_fix`, `parallel_rewrite`, `deai_rewrite`, `deai_closed_loop`
- Allowed: review, analysis, checking, reporting
- Output: structured advice with exact quotes and suggested fixes
- De-AI: detect + diagnose only (report signals, no rewrite)

## Red Line Enforcement

In ANY budget mode:
1. Never modify author's core thesis
2. Never invent citations or data
3. Never exceed 3 rewrites per section per session

## Cost-Awareness Rules

- Before expensive operations, check budget mode
- In medium/minimal: prefer cheaper tool variants
  - Use `run_single_reviewer` (1 reviewer) instead of `review_paper` (5 reviewers) for targeted checks
  - Use `deai_detect` alone instead of full pipeline for quick assessment
- Token budget per issue: calculated by `utils/token_budget.py` based on severity
  - critical: 4000 tokens
  - major: 2500 tokens
  - moderate: 1500 tokens
  - minor: 800 tokens
