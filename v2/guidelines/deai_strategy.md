# De-AI Strategy Guide

> Load this guideline via `read_agent_guidelines("deai_strategy")` when performing de-AI work.

## Overview

De-AI (去AI化) removes detectable AI-writing patterns while preserving academic rigor and author voice.

## Pipeline Choice

### Agent-Orchestrated Pipeline (Preferred)
Use individual `deai_detect` → `deai_diagnose` → `deai_rewrite` → `deai_verify` tools when:
- You need fine-grained control over each step
- You want to inspect intermediate results
- The section has complex issues requiring custom strategies
- You're in budget=full mode and want to maximize quality

### Closed-Loop Pipeline (Legacy, will be deprecated)
`deai_closed_loop` runs all 4 steps internally. Use only when:
- Budget=medium and you want a quick pass
- Section is short (<300 words) with minor issues

## Scene Selection

Scenes determine detection sensitivity and rewrite style:

| Scene | Triggers | Strategy |
|-------|----------|----------|
| S1 (General) | Default for most sections | Balanced detection |
| S2 (Technical/Method) | Methodology, data, results sections | Preserve technical precision, less aggressive |
| S3 (Lit Review) | Literature review, related work | Focus on citation flow, attribution patterns |
| S4 (Discussion) | Discussion, conclusion | Allow more authorial voice |

## Voice Profile Integration

**Always check** `show_author_profile` before any rewrite. The voice profile contains:
- Sentence length distribution (mean, std)
- Vocabulary preferences
- Transition patterns
- Hedging style

Use these as constraints in `deai_rewrite(author_constraints=...)`.

## Quality Thresholds (from config/thresholds.yaml)

- `ai_probability_threshold`: 0.6 — below this, section passes
- `min_improvement_delta`: 0.15 — rewrite must reduce AI score by at least this
- `max_semantic_drift`: 0.12 — rewrite must stay semantically close

## Iteration Protocol for De-AI

1. Run `deai_detect` → if ai_probability < threshold, STOP (no work needed)
2. Run `deai_diagnose` → get specific signals and fix strategies
3. Run `deai_rewrite` with author_constraints from voice_profile
4. Run `deai_verify`:
   - If PASS: done
   - If PARTIAL: accept (diminishing returns) unless user requests perfection
   - If FAIL: try ONE more pass with different strategy, then report to user

Max 2 passes per section. After that, report remaining signals and let user decide.

## Red Lines

- NEVER rewrite to change factual claims or causal direction
- NEVER remove hedging that the author intentionally placed
- NEVER make prose "more interesting" at the cost of precision
- Keep all statistical language verbatim (p-values, coefficients, test names)
