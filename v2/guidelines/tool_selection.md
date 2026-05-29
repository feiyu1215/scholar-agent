# Tool Selection Guide

> Load this guideline via `read_agent_guidelines("tool_selection")` when choosing which tools to use.

## Tool Categories

### Zero-Cost (Always safe to run)
| Tool | Purpose | When |
|------|---------|------|
| parse_paper | Parse paper into sections | First action when user provides a paper |
| read_section_index | Get paper structure overview | Before targeted reads |
| read_section | Read one section's content | When you need actual text |
| presubmission_check | Mechanical formatting checks | Before expensive review |
| architecture_diagnosis | Structural skeleton analysis | Before review_paper |
| read_issues | View current issues list | After review or routing |
| revision_progress | Check fix progress | During revision loop |
| session_status | Overview of session state | Anytime |
| diff_section | See changes made | After rewrite/edit |

### Medium-Cost (LLM calls, use judiciously)
| Tool | Purpose | When |
|------|---------|------|
| review_paper | Multi-reviewer assessment | After structural analysis |
| run_single_reviewer | Single focused review | Targeted re-check |
| rewrite_section | Full section rewrite | Fixing major issues |
| edit_section | Surgical text edit | Small targeted fixes |
| deai_detect/diagnose | AI signal analysis | Before/after rewrites |
| generate_fix_proposal | Propose a fix for an issue | In routing phase |
| quality_gate | Meta-assessment of review | After review_paper |

### High-Cost (Multiple LLM calls, use sparingly)
| Tool | Purpose | When |
|------|---------|------|
| parallel_rewrite | Rewrite multiple sections | Batch processing |
| deai_closed_loop | Full de-AI pipeline | Per-section de-AI |
| consolidate_reviews | Merge reviewer outputs | After multi-reviewer |
| verify_and_enrich_citations | Full citation verification | Pre-submission |

## Decision Flowchart

```
User request arrives
  ├─ "review/check my paper"
  │   → presubmission_check + architecture_diagnosis (parallel)
  │   → review_paper (if structural issues are not blocking)
  │   → route_issues → fix loop
  │
  ├─ "fix/rewrite section X"
  │   → read_section(X) → rewrite_section(X)
  │   → verify via post-edit check
  │
  ├─ "de-AI my paper"
  │   → build_voice_profile (if not exists)
  │   → for each section: deai_detect → deai_diagnose → deai_rewrite → deai_verify
  │
  ├─ "check citations"
  │   → verify_citations → check_citation_alignment
  │
  └─ ambiguous request
      → ask_user for clarification (max 2-3 options)
```

## Tool Ordering Rules

1. **Always first**: parse_paper (if paper not yet parsed)
2. **Always second**: build_voice_profile (if not yet built, before any rewrite)
3. **Before review**: presubmission_check + architecture_diagnosis
4. **Before rewrite**: ensure review exists (route_issues done)
5. **After rewrite**: post-edit verification (automatic) + deai_audit (if needed)
6. **Before shipping**: reaudit + revision_progress check

## Parallel Execution

Call tools in the same turn when they're independent:
- ✅ presubmission_check + architecture_diagnosis
- ✅ read_section("03") + read_section("05") (different sections)
- ✅ deai_detect on section A + deai_detect on section B
- ❌ review_paper then route_issues (dependent)
- ❌ rewrite_section then diff_section (dependent)

## Error Handling

When a tool returns an error:
1. Report the error to the user concisely
2. Suggest an alternative approach
3. Do NOT retry with identical arguments
4. If the tool is critical to the workflow, ask user how to proceed
