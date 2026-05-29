# Overclaim Detection & Academic Phrase Cleanup Rules

## Overview

Overclaiming is one of the most common reasons for desk rejection in academic journals.
This module detects language that overstates findings and provides specific replacements.
Loaded on-demand by `review_engine` (overclaim reviewer role) and `literature_verify` (citation claim checking).

---

## Part 1: Overclaim Detection — English

### Signal Words to Flag and Soften

| Overclaim Word/Phrase | Safer Replacement(s) |
|---|---|
| prove(s) | show(s), provide(s) evidence, demonstrate(s) |
| conclusively | strongly, convincingly |
| unprecedented | novel, to our knowledge the first |
| best | among the strongest, state-of-the-art |
| superior | outperforms, compares favorably |
| first (as claim of priority) | to our knowledge, the first |
| undeniable | strong, compelling |
| irrefutable | robust, consistent |
| groundbreaking | novel, promising |
| revolutionary | significant, substantial |
| perfect (for models/results) | strong, high-performing |
| always/never (for empirical) | typically, in most cases / rarely, in few cases |
| optimal | near-optimal, well-performing |
| solves (the problem) | addresses, mitigates, reduces |

### Overclaim Context Rules

```
RULE OC-1: Causal language without identification
  FLAG: "X causes Y" when using observational data without causal identification strategy
  SUGGEST: "X is associated with Y" or "X predicts Y"
  EXCEPTION: Legitimate if paper employs IV/RDD/DID with valid first stage

RULE OC-2: Generalization beyond sample
  FLAG: "This shows that [universal claim]" based on single-country/period data
  SUGGEST: Add scope qualifier: "In the context of [sample], we find..."
  EXCEPTION: Meta-analysis or multi-site RCT with diverse samples

RULE OC-3: Mechanism claims without direct test
  FLAG: "The mechanism is X" without explicitly testing the channel
  SUGGEST: "Suggestive evidence points to X as a potential mechanism"
  EXCEPTION: Paper includes formal mediation analysis or structural model

RULE OC-4: Novelty overclaim
  FLAG: "No prior study has..." (often false; may just be unfamiliarity with literature)
  SUGGEST: "To our knowledge, limited prior work has..." or verify with literature search
  EXCEPTION: Verified via systematic literature review or meta-analysis

RULE OC-5: Statistical overclaim
  FLAG: "highly significant" (p-values don't have degrees of significance)
  SUGGEST: "statistically significant at the 1% level" (report the actual level)
  EXCEPTION: None — this is always imprecise language

RULE OC-6: Effect size language
  FLAG: "large effect" without benchmarking
  SUGGEST: Compare to relevant benchmarks: "an effect equivalent to X% of the mean"
  EXCEPTION: Paper explicitly defines "large" relative to prior literature
```

---

## Part 2: Overclaim Detection — 中文

### 中文过度宣称信号词

| 过度宣称表达 | 建议替换 |
|---|---|
| 证明了（observational study 中） | 发现/表明/提供了证据 |
| 首次提出（未经文献验证） | 据我们所知，有限的先前研究… |
| 显著优于（提升 <1%） | 略有提升/在部分指标上有改善 |
| 完美解决了 | 有效缓解了/在一定程度上解决了 |
| 填补了空白 | 拓展了已有研究/为…提供了新视角 |
| 具有重要的理论意义和实践价值 | （具体说明什么理论贡献、什么实际用途） |
| 开创性地 | 创新性地/在…方面做出了尝试 |
| 颠覆了传统认知 | 对传统观点提出了挑战/提供了不同的解释 |
| 必将推动 | 有望推动/可能对…产生积极影响 |

### 中文过度宣称语境规则

```
RULE OC-ZH-1: 因果表述缺乏识别策略
  FLAG: "X 导致了 Y 的变化" —— 在观测数据中使用因果动词
  SUGGEST: "X 与 Y 的变化存在显著的正向关联"
  EXCEPTION: 使用了工具变量/双重差分/断点回归等因果识别方法

RULE OC-ZH-2: 泛化超出样本范围
  FLAG: "这表明所有企业都应该…" —— 基于特定行业/地区样本得出普遍结论
  SUGGEST: "在本研究的样本范围内，结果表明…"

RULE OC-ZH-3: 贡献宣称空洞化
  FLAG: "丰富了现有文献" / "拓展了理论边界" —— 未说明具体贡献什么
  SUGGEST: 明确陈述：具体补充了什么视角、哪个理论的哪个方面被拓展

RULE OC-ZH-4: 政策建议过度
  FLAG: "建议政府应当…" —— 基于相关性研究直接给出政策建议
  SUGGEST: "如果因果关系成立，政策制定者或可考虑…"
  EXCEPTION: 基于 RCT 或准自然实验的严格因果推断
```

---

## Part 3: Phrases to Delete

These phrases add no content and are red flags for AI-generated or over-polished text.
They should be deleted entirely (not replaced).

### English — Delete-on-Sight List

```
Filler hedges (delete entirely):
- "It is worth noting that"
- "It should be noted that"
- "It is important to note that"
- "It is interesting to note that"
- "Interestingly,"
- "Notably,"
- "It bears mentioning that"

Empty contribution claims (rewrite to be specific):
- "This paper contributes to the literature by"
- "This paper fills a gap in the literature"
- "The contribution of this paper is twofold"
- "We make several contributions"
→ Instead: State the actual finding directly.

Throat-clearing openings (delete):
- "In recent years, there has been growing interest in"
- "The topic of X has attracted considerable attention"
- "X has become an increasingly important issue"
- "In today's rapidly changing world"
- "With the advent of"

Vague attributions (make specific or delete):
- "Previous research has shown that"
- "The literature suggests that"
- "It is widely believed that"
- "Scholars have long debated"
→ Instead: Name the specific author(s) and their specific finding.

Redundant intensifiers (delete the intensifier):
- "very unique" → "unique"
- "highly significant" → "significant at the X% level"
- "extremely important" → "important" or better: explain WHY it matters
- "quite clearly" → "clearly" or delete
- "fundamentally crucial" → pick one
```

### 中文 — 应删除的空洞表达

```
开头废话（直接删除，进入正题）：
- "随着…的不断发展"
- "近年来，…受到了广泛关注"
- "在当今…的背景下"
- "众所周知"
- "不言而喻"

空洞贡献声明（改为具体陈述）：
- "本文的贡献是多方面的"
- "丰富了现有文献"
- "具有重要的理论意义和实践价值"
→ 替代：直接说发现了什么、解决了什么问题。

冗余强调（删除修饰词）：
- "极其重要的" → "重要的"（或解释为什么重要）
- "非常显著" → "在 X% 水平上显著"
- "完全一致" → "一致"（除非在强调两个量的精确相等）
```

---

## Part 4: Domain-Specific Overclaim Patterns

### Economics

```
- "welfare-improving" without formal welfare analysis → "associated with improved outcomes"
- "efficient" without defining the efficiency criterion → specify Pareto/Kaldor-Hicks/etc.
- "optimal policy" from a reduced-form study → "effective policy" or "well-targeted"
- "causal" from panel FE alone without additional identification → "predictive" or add caveat
- "externality" as metaphor rather than formally defined → clarify or use descriptive language
```

### Computer Science / ML

```
- "state-of-the-art" without comprehensive comparison → "competitive with recent methods"
- "significantly outperforms" with <1% improvement → "marginally improves upon"
- "robust" without adversarial/distribution-shift tests → "performs consistently on tested benchmarks"
- "generalizes well" from single dataset → "performs well on [dataset]; further evaluation needed"
- "solves" an NP-hard problem → "approximates" or "heuristically addresses"
- "real-time" without latency measurements → "low-latency" or report actual ms
```

### Medical / Health Sciences

```
- "cure" from observational study → "associated with improved outcomes"
- "safe" without long-term follow-up → "well-tolerated in the short term"
- "effective treatment" from correlation → "potentially effective; RCT needed"
- "no side effects" from small N → "no side effects observed in this sample (N=...)"
- "breakthrough" for incremental improvement → "advance" or "promising approach"
```

### Social Sciences / Psychology

```
- "proves the theory" from correlational study → "provides evidence consistent with the theory"
- "replicates" without exact protocol match → "provides converging evidence" or "conceptually replicates"
- "X causes Y" from cross-sectional survey → "X is associated with Y"
- "universal" from WEIRD sample → "observed in [sample characteristics]"
- "significant effect" when practical significance is negligible → report effect size explicitly
```
