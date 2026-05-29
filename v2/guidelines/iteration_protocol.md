# Iteration & Verification Protocol

> Load this guideline via `read_agent_guidelines("iteration_protocol")` when performing revision loops.

## Core Iteration Loop

```
review → route → fix → verify → (loop if needed)
```

After completing a round of modifications:
1. Call `revision_progress` to check remaining must_fix issues
2. If must_fix_remaining > 0, continue fixing (do NOT ask user unless blocked)
3. After fixing all must_fix, call `reaudit` to verify
4. If reaudit shows regressions, prioritize fixing regressions
5. Maximum 3 iterations per session

## Post-Edit Verification

After EVERY `rewrite_section` or `edit_section`:
1. Post-Edit Verification runs automatically inside the tool
2. Check the result:
   - **PASS**: proceed to next issue
   - **FAIL (AI regression)**: try deai_detect + targeted fix
   - **FAIL (consistency break)**: flag as must-fix, investigate cross-refs
   - **FAIL (semantic drift)**: revert mentally, try less aggressive rewrite
3. Max 3 attempts per section — if still failing, report with diff to user

## Quality Gate Integration

After `review_paper` completes:
1. Quality Gate auto-evaluates across 5 dimensions
2. Check verdict:
   - **"ship"**: proceed to routing + fixing
   - **"deepen"**: re-review weak dimensions (max 2 rounds deepening)
   - **"restart"**: full re-review with adjusted focus (max 1 restart)

## Quality Trajectory

Scores MUST improve monotonically:
- Track: initial_score → current_score → target_score
- Target = min(initial + 2.0, 8.0)
- If score drops after modification: STOP, report regression, suggest revert
- Use `score_tracker` to record each checkpoint

## Doom Loop Escape

If you receive a "doom loop blocked" message:
1. You MUST NOT retry the same call
2. Try a genuinely different approach (different tool, different strategy)
3. If stuck after 2 alternative attempts, ask the user
4. Never call a blocked tool with identical arguments

## Score-Based Prioritization

Fix issues in this order:
1. gate_blocker issues (always first)
2. major + methodology/logic (highest impact)
3. major + other types
4. moderate issues with concrete fix proposals
5. minor issues (only if time/budget allows)

## Iteration Limits

| Action | Max Iterations | On Limit |
|--------|---------------|----------|
| Review deepening | 2 rounds | Report best-effort |
| Rewrite per section | 3 attempts | Mark needs_manual_fix |
| De-AI per section | 2 passes | Accept remaining signals |
| Full session cycle | 3 cycles | Summarize and ask user |

## Regression Handling

If score_tracker.check_regression() fires:
1. DO NOT proceed with more edits
2. Report: "Score regression detected: X.X → Y.Y"
3. Suggest: "Consider reverting the last modification"
4. Wait for user decision before continuing
