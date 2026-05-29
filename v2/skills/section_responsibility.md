# Section Responsibility Matrix

Section-aware reviewer assignment guide. Maps each paper section to its primary reviewer role(s) and expected focus areas.

## Purpose

When `review_engine.py` dispatches sections to reviewers, it uses this matrix to:
1. Route the RIGHT section to the RIGHT reviewer (no wasted tokens)
2. Tell each reviewer what to focus on FOR THAT SECTION (not generic advice)
3. Detect "nobody owns this" blind spots (e.g., author contributions, data availability)

## Responsibility Matrix

### Abstract
| Reviewer | Priority | Focus |
|----------|----------|-------|
| Editor | PRIMARY | Clarity, completeness (purpose/method/result/conclusion), word count, standalone readability |
| Logic | SECONDARY | Claims match body findings? Overclaiming in abstract vs actual results? |

### Introduction
| Reviewer | Priority | Focus |
|----------|----------|-------|
| Editor | PRIMARY | Narrative arc (broad → gap → contribution), motivation clarity, scope statement |
| Literature | PRIMARY | Gap claim supported? Key citations present? Positioning vs prior work accurate? |
| Logic | SECONDARY | Are listed contributions actually delivered later? Promise-delivery alignment |

### Literature Review / Related Work
| Reviewer | Priority | Focus |
|----------|----------|-------|
| Literature | PRIMARY | Coverage completeness, recency, fair representation of competing approaches, citation accuracy |
| Editor | SECONDARY | Organization (thematic vs chronological), transitions, synthesis vs listing |
| Theory | TERTIARY | Theoretical framing derived correctly from literature? |

### Theoretical Framework / Hypotheses
| Reviewer | Priority | Focus |
|----------|----------|-------|
| Theory | PRIMARY | Internal consistency, logical derivation, definition clarity, falsifiability |
| Logic | PRIMARY | Hypothesis follows from theory? Circular reasoning? |
| Methodology | SECONDARY | Operationalization possible given hypothesis structure? |

### Methodology / Methods / Research Design
| Reviewer | Priority | Focus |
|----------|----------|-------|
| Methodology | PRIMARY | Reproducibility, validity threats, sample adequacy, statistical appropriateness |
| Theory | SECONDARY | Method matches theoretical constructs? Construct validity? |
| Logic | TERTIARY | Logical flow: design → data collection → analysis → interpretation chain |

### Data Description
| Reviewer | Priority | Focus |
|----------|----------|-------|
| Methodology | PRIMARY | Sample characteristics, missing data handling, variable definitions, data access/ethics |
| Logic | SECONDARY | Exclusion criteria justified? Selection bias? |

### Results / Findings
| Reviewer | Priority | Focus |
|----------|----------|-------|
| Methodology | PRIMARY | Statistical reporting completeness (effect sizes, CIs, p-values), table/figure accuracy |
| Logic | PRIMARY | Results actually support claims? Cherry-picking? Ignored null results? |
| Theory | SECONDARY | Results speak to hypotheses/framework? Unexpected findings acknowledged? |

### Discussion
| Reviewer | Priority | Focus |
|----------|----------|-------|
| Logic | PRIMARY | Interpretation warranted by results? Overclaiming? Alternative explanations considered? |
| Theory | PRIMARY | Theoretical implications derived correctly? Contribution to field clearly stated? |
| Literature | SECONDARY | Comparison with prior findings fair? Contradictions acknowledged and explained? |
| Editor | SECONDARY | Limitations honest and specific? Not just "more data needed" |

### Conclusion
| Reviewer | Priority | Focus |
|----------|----------|-------|
| Editor | PRIMARY | Matches abstract? No new information? Practical implications concrete? |
| Logic | SECONDARY | Does conclusion follow from discussion? Scope creep beyond what was studied? |

### References / Bibliography
| Reviewer | Priority | Focus |
|----------|----------|-------|
| Literature | PRIMARY | Format consistency, completeness, all cited → in reference list and vice versa |
| (Mechanical) | PRIMARY | Citation format check delegated to presubmission_check.py |

### Figures and Tables
| Reviewer | Priority | Focus |
|----------|----------|-------|
| Methodology | PRIMARY | Statistical accuracy, appropriate visualization, axis labels, significance markers |
| Editor | SECONDARY | Readability, self-contained captions, color accessibility |
| (Figure Analyzer) | PRIMARY | Claim-caption alignment delegated to figure_analyzer.py |

### Appendices / Supplementary
| Reviewer | Priority | Focus |
|----------|----------|-------|
| Methodology | PRIMARY | Robustness checks complete? Alternative specifications? |
| Logic | SECONDARY | Do appendix results contradict main results? |

---

## Blind Spot Sections (Often Nobody Reviews)

These sections are commonly missed by reviewers but frequently cause desk rejections:

| Section | Who Should Own It | Common Issues |
|---------|-------------------|---------------|
| Author Contributions (CRediT) | Editor | Missing for multi-author papers, required by many journals |
| Data Availability Statement | Methodology | Increasingly required; vague "available upon request" problematic |
| Conflict of Interest | Editor | Must be explicit even if "none declared" |
| Ethics Approval | Methodology | Required for human subjects, often incomplete |
| Funding Statement | Editor | Many journals require even "no funding received" |
| Keywords | Editor | Missing, too broad, or not matching journal taxonomy |
| Highlights / Key Points | Editor | Required by some journals, often forgotten |

## Usage in review_engine.py

The review engine should:

1. **Parse section types** from `section_index.json` slugs
2. **Map each section** → primary reviewer(s) using this matrix
3. **Inject section-specific prompts**: "For this Methods section, focus on: {focus areas from matrix}"
4. **Flag blind spots**: If paper has human subjects but no Ethics section → auto-generate a warning
5. **Report coverage**: After review, show which sections were reviewed by whom and what was skipped

## Severity Escalation Rules

When a section gets flagged by its SECONDARY or TERTIARY reviewer but NOT its PRIMARY reviewer:
- This signals the PRIMARY reviewer missed something → escalate to re-review
- Example: Logic reviewer flags methodology issue that Methodology reviewer missed → re-dispatch to Methodology reviewer with specific attention hint

When NO reviewer flags issues in a section:
- Cross-check with presubmission_check results
- If presubmission_check also passes → section is likely fine
- If presubmission_check flags something → route to appropriate reviewer
