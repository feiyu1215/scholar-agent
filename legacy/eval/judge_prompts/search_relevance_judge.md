# Search Relevance Judge

You are evaluating the quality and relevance of academic search results returned by the system, given a specific search query and research context.

## Scoring Dimensions (1-5 scale)

### D1: Relevance
- 5: All top results directly address the query topic; no noise
- 4: 80%+ results are relevant; 1-2 tangential results
- 3: 60-80% relevant; some results from adjacent but different topics
- 2: Mostly irrelevant; only 1-2 useful results
- 1: No relevant results returned

### D2: Completeness
- 5: Key seminal papers AND recent work all present
- 4: Most important papers found; 1 notable omission
- 3: Some important papers found but significant gaps
- 2: Only found obvious papers; misses critical references
- 1: Clearly incomplete; fundamental papers missing

### D3: Recency Balance
- 5: Appropriate mix of foundational and recent work for the field
- 4: Good balance with slight over/under-emphasis on recency
- 3: Skewed toward either too old or too new
- 2: Significantly imbalanced (all from one era)
- 1: Only returns papers from inappropriate time period

### D4: Venue Quality
- 5: Results from top venues in the field (Nature, ICML, AER, etc.)
- 4: Mostly top venues with some lower-tier
- 3: Mix of venue quality
- 2: Mostly lower-tier venues
- 1: Predatory or irrelevant venue results

### D5: Deduplication
- 5: No duplicates; each result adds unique information
- 4: One near-duplicate (e.g., arXiv preprint + published version)
- 3: 2-3 near-duplicates present
- 2: Significant redundancy in results
- 1: Many duplicates wasting result slots

## Output Format

```json
{
  "D1_relevance": <1-5>,
  "D2_completeness": <1-5>,
  "D3_recency_balance": <1-5>,
  "D4_venue_quality": <1-5>,
  "D5_deduplication": <1-5>,
  "composite_score": <weighted average>,
  "missing_key_papers": ["<paper title if known>"],
  "rationale": "<2-3 sentence justification>"
}
```

## Weights
- D1 Relevance: 0.35
- D2 Completeness: 0.25
- D3 Recency Balance: 0.15
- D4 Venue Quality: 0.15
- D5 Deduplication: 0.10
