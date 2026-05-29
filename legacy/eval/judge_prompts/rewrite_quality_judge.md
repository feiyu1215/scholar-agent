# Rewrite Quality Judge

You are evaluating the quality of an AI-assisted academic text rewrite. Compare the original text with the rewritten version and assess improvement.

## Scoring Dimensions (1-5 scale)

### D1: Issue Resolution
- 5: The identified issue is fully and elegantly resolved
- 4: Issue is resolved but solution could be more elegant
- 3: Issue is partially resolved; some aspect remains
- 2: Attempted but the core issue persists
- 1: Issue is not addressed or made worse

### D2: Voice Preservation
- 5: Indistinguishable from the author's original style
- 4: Very close to original style; minor deviations acceptable
- 3: Noticeable style shift but still reads as academic prose
- 2: Clearly different voice; reads like a different author
- 1: Complete voice replacement; reads like AI boilerplate

### D3: No Regression
- 5: No new issues introduced; all cross-references intact
- 4: One trivial new issue (easily fixed)
- 3: One non-trivial new issue introduced
- 2: Multiple new issues introduced
- 1: Rewrite introduces more problems than it solves

### D4: Naturalness
- 5: Reads naturally; no AI writing signals detectable
- 4: One minor AI-ism that most readers wouldn't notice
- 3: 2-3 AI patterns present but text is still usable
- 2: Clearly AI-written in places (hedging, over-formality, cliches)
- 1: Reads like unedited LLM output

### D5: Academic Precision
- 5: Technical claims are precise, caveats appropriate, logic tight
- 4: Technically sound with minor imprecision
- 3: Mostly correct but introduces some vagueness
- 2: Oversimplifies or introduces inaccuracies
- 1: Misrepresents the original content's meaning

## Output Format

```json
{
  "D1_issue_resolution": <1-5>,
  "D2_voice_preservation": <1-5>,
  "D3_no_regression": <1-5>,
  "D4_naturalness": <1-5>,
  "D5_academic_precision": <1-5>,
  "composite_score": <weighted average>,
  "rationale": "<2-3 sentence justification>",
  "net_improvement": true/false
}
```

## Weights
- D1 Issue Resolution: 0.30
- D2 Voice Preservation: 0.20
- D3 No Regression: 0.20
- D4 Naturalness: 0.15
- D5 Academic Precision: 0.15
