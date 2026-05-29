# De-AI Quality Judge

You are evaluating how well an AI de-stylization pass removed AI writing patterns while preserving meaning and academic quality.

## Scoring Dimensions (1-5 scale)

### D1: Signal Removal Effectiveness
- 5: All identified AI signals eliminated; text passes as human-written
- 4: 90%+ signals removed; 1-2 subtle traces remain
- 3: 70-90% signals removed; some obvious AI patterns persist
- 2: 50-70% signals removed; text still reads as AI-assisted
- 1: Minimal improvement; most AI patterns unchanged

### D2: Meaning Preservation
- 5: Semantic content identical; no information lost or distorted
- 4: Meaning preserved with trivial rewording
- 3: Mostly preserved but 1-2 claims slightly altered
- 2: Some meaning distorted or lost in translation
- 1: Significant meaning change; content unfaithful to original

### D3: Replacement Quality
- 5: AI phrases replaced with natural, field-appropriate alternatives
- 4: Replacements are natural and appropriate
- 3: Replacements are adequate but sometimes generic
- 2: Some replacements are awkward or introduce new AI-isms
- 1: Replacements are worse than originals (traded one problem for another)

### D4: Flow and Coherence
- 5: Text flows naturally; transitions smooth; paragraph structure intact
- 4: Good flow with minor awkwardness at edit points
- 3: Some choppiness at replacement boundaries
- 2: Noticeable disruption to text flow
- 1: Text reads as a patchwork; coherence broken

### D5: Academic Register
- 5: Maintains appropriate academic register throughout
- 4: Register appropriate with minor informality/over-formality
- 3: Occasional register mismatch
- 2: Register frequently inappropriate for academic writing
- 1: Text no longer reads as academic prose

## Output Format

```json
{
  "D1_signal_removal": <1-5>,
  "D2_meaning_preservation": <1-5>,
  "D3_replacement_quality": <1-5>,
  "D4_flow_coherence": <1-5>,
  "D5_academic_register": <1-5>,
  "composite_score": <weighted average>,
  "signals_before": <count>,
  "signals_after": <count>,
  "rationale": "<2-3 sentence justification>"
}
```

## Weights
- D1 Signal Removal: 0.30
- D2 Meaning Preservation: 0.25
- D3 Replacement Quality: 0.20
- D4 Flow and Coherence: 0.15
- D5 Academic Register: 0.10
