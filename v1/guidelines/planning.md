# Planning Protocol

> Load this guideline via `read_agent_guidelines("planning")` when handling complex multi-step requests.

## When to Plan

Plan explicitly (output `<plan>...</plan>`) when the request involves:
- Multi-step edits across 2+ sections
- Full paper processing (review → route → fix → verify)
- Any sequence of 3+ tool calls with dependencies

For single-tool requests or direct questions, skip planning and act immediately.

## Plan Structure

```
<plan>
1. [Step] — tool: X — depends_on: none
2. [Step] — tool: Y — depends_on: step 1
3. [Step] — tool: Z — depends_on: step 2
Success criteria: [what "done" looks like]
</plan>
```

## Cost-Awareness

Before executing a plan involving expensive tools (review_paper, rewrite_section, deai_closed_loop):
1. Call `dry_run_estimate` with your planned operations
2. Show the user the estimated cost/time
3. Proceed unless user objects

## Progressive Depth

Always escalate complexity gradually:

| Level | Tools | Token Cost |
|-------|-------|-----------|
| Quick | presubmission_check, architecture_diagnosis | Zero |
| Standard | review_paper (3 reviewers), rewrite_section | Medium |
| Deep | review_paper (5 reviewers), parallel_rewrite, deai_closed_loop | High |

Default to Quick + Standard. Only escalate to Deep if:
- User explicitly requests thorough/deep review
- Quality Gate returns "deepen" verdict
- Budget mode is "full"

## Parallel vs Sequential

- **Parallel**: Independent tools (e.g., presubmission_check + architecture_diagnosis)
- **Sequential**: Dependent tools (e.g., review_paper → route_issues → rewrite_section)

## Plan Persistence

After creating a plan, track progress mentally. If interrupted (context compression, error), recover by:
1. Checking `revision_progress` for current state
2. Checking `session_status` for what's been done
3. Resuming from the next incomplete step
