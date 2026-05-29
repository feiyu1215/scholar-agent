# Examples

This directory contains a sample paper and demonstration outputs showing ScholarAgent's capabilities.

## Quick Demo

```bash
# From project root:
./examples/run_demo.sh
```

This runs the agent in review-only mode (`--budget minimal`) on the sample paper — no rewrites are performed, and you can see the full review output without any API cost beyond the review itself.

## Files

### Input Papers

- **`sample_paper.md`** — A synthetic economics paper with intentional issues (overclaims, hedging, AI-style writing, methodological gaps). Designed to trigger all three action types. Good for quick testing.

- **`sample_paper_economics.pdf`** — *"The Short-Term Effects of Generative AI on Employment"* (Hui, Reshef & Zhou, 2023). CESifo Working Paper. Real DiD study using ChatGPT's release as exogenous shock on Upwork labor market. ~35 pages.

- **`sample_paper_rdd.pdf`** — *"Firm Responses to State Hiring Subsidies"* (Hyman et al., 2022). NBER Working Paper #30664. Real RDD study using California tax credit scoring cutoff. ~50 pages.

Both PDFs are publicly available working papers used solely for demonstration purposes.

### Output Snapshots (`demo_output/`)

These are captured outputs from a full-budget revision session:

- **`review_consolidated.json`** — Consolidated review from 5 parallel reviewers (Editor, Theory, Methodology, Logic, Literature). Shows 10 issues with severity, category, action_type classification, and a revision roadmap.

- **`routed_issues.json`** — After action routing: shows how Red Lines, budget ceiling, and first-of-type rules modify the raw action_types. Includes routing stats.

- **`deai_verdict.json`** — De-AI audit results for two sections: one clean PASS, one FAIL that triggers the PEV fix loop and resolves in 1 iteration.

- **`before_after_diff.md`** — A concrete before/after comparison of one section revision, showing the agent decision trace, De-AI score improvement, and specific textual changes.

- **`score_progression.md`** — Full session timeline showing how the paper's review score improves from 4.5 → 6.5 across three phases, with token usage and cost breakdown.

## Understanding the Output

The review pipeline produces three types of actions:

| Action Type | What Happens | Example |
|-------------|-------------|---------|
| `auto_fix` | Agent fixes directly + de-AI audit | Grammar, hedging, formatting |
| `confirm_fix` | Shows proposal → waits for approval | Core claims, conclusion structure |
| `guidance` | Instructions only (no rewrite) | Missing data, new analysis needed |

Safety is enforced by code (Red Lines), not by prompts. See `tools/action_router.py` for implementation.
