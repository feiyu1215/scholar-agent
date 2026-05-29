# De-AI Detection Rules — English Academic Papers (S1/S3)

> Extracted from deai-writing Skill for ScholarAgent's deai_audit module.
> These rules are loaded on-demand during post-rewrite verification.

## Detection Signals (what to look for)

### Signal Category: AI_VOCABULARY
High-frequency AI words that flag text as machine-generated:
- **Banned words (S1)**: delve, leverage, tapestry, underscore, pivotal, nuanced, landscape, utilize, foster, harness, elevate, intricate, multifaceted, cornerstone, testament, showcase, noteworthy, facilitate, encompass, realm, embark, streamline, bolster, cutting-edge, game-changer, accentuate, ameliorate, elucidate, endeavor, perpetuate, scrutinize, unveil
- **S3 exception**: economics papers may use "nuanced", "intricate" (disciplinary convention)
- **Replacements**: leverage→use, delve into→investigate, utilize→use, noteworthy→notable, facilitates→enables, showcases→shows

### Signal Category: MECHANICAL_OPENER
Formulaic sentence starters that add no content:
- First and foremost / It is worth noting that / It is important to note
- In today's rapidly evolving / It bears mentioning that
- What truly matters is / The real question is / At its core

### Signal Category: TRICOLON
Three-item parallel structures used as argument scaffolding:
- "X, Y, and Z" as the backbone of reasoning (not when listing specific variables/controls)
- Present in ~82% of AI-generated academic text
- Exception: listing specific control variables in econometrics is fine

### Signal Category: RESOLUTION_CLOSER
Vacuous philosophical endings:
- "In the end, what truly matters is..."
- "Ultimately, this underscores the importance of..."
- Final sentences that elevate to abstract philosophy rather than stating concrete conclusions

### Signal Category: RHYTHM_UNIFORMITY
All sentences approximately the same length (±20%):
- Longest:shortest sentence word ratio < 3:1
- 4+ consecutive sentences of similar length (S1/S3 threshold: 4, general: 3)
- Minimum short sentence: 10 words for academic (6 for general)

### Signal Category: CONNECTOR_OVERUSE
Mechanical transition words stacking:
- Furthermore / Moreover / Additionally appearing >1 per paragraph
- "However, it is important to note that..." (hedge + connector combo)

### Signal Category: EM_DASH_OVERUSE
- S1: max 1 em-dash per 1000 words
- S3: em-dashes fully allowed (economics convention)

### Signal Category: VAGUE_ATTRIBUTION
- "Many scholars argue..." / "Some believe..." without citation
- "Experts say..." / "Research shows..." without specific reference

### Signal Category: COPULA_AVOIDANCE
Unnecessarily avoiding is/are:
- "X serves as Y" / "X stands as Y" / "X acts as Y" when "X is Y" is clearer

### Signal Category: NEGATION_PARALLEL
Dramatic contrast for false depth:
- "It's not just X; it's Y" / "It's not X — it's Y"
- Direct statement of Y is preferred

### Signal Category: INFLATED_SYMBOLISM
Assigning excessive philosophical weight to simple findings:
- "This finding fundamentally reshapes our understanding..."
- "...represents a paradigm shift in..."
- Unless backed by specific transformative evidence

### Signal Category: SHALLOW_ING_ANALYSIS
Progressive tense as substitute for actual explanation:
- "X is transforming Y" / "Z is reshaping the landscape"
- Without explaining HOW or providing evidence

## Scoring

Each detected signal contributes to the overall naturalness score:
- **confidence**: 0.0-1.0 per signal (how certain it's AI-generated, not just unusual style)
- **overall_score**: 1.0 = fully natural, 0.0 = maximally AI-like
- **threshold**: overall_score >= 0.7 → PASS (no fix needed)
- Signal count alone is insufficient — a single high-confidence banned word matters less than 3 structural patterns

## Fix Principles

1. **Minimum slice**: fix only the flagged sentence, never rewrite surrounding context
2. **Preserve meaning**: the fix must be semantically equivalent to the original
3. **No quality loss** (Red Line 3): if the fix reduces readability or introduces error, keep original
4. **Academic register**: replacements must maintain appropriate formality for academic writing
5. **Author voice**: if the paper has a consistent style (e.g., uses em-dashes throughout), respect it
