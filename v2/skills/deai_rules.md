# De-AI Detection Rules — Full Rule Set

> Migrated from deai-writing Skill v2.0 (complete).
> S1 = CS/AI/NLP/CV English academic papers.
> S2 = Chinese academic papers (journals/theses).
> S3 = Economics/Finance/Business academic papers.
> S_GENERAL = Cross-scene universal patterns (always active as baseline).
> These rules are loaded on-demand during post-rewrite verification.

---

## Scene Routing

| Condition | Scene | Mode |
|-----------|-------|------|
| English + LaTeX/academic + CS/AI/NLP/CV direction | S1 | Strict (only) |
| Chinese + academic register + journal/thesis | S2 | Strict (only) |
| Economics/Finance/Business direction (English or Chinese) | S3 | Strict (only) |

Default: S1. Switch to S2 for Chinese academic text. Switch to S3 for economics/finance/business.

---

## S_GENERAL: Universal Baseline (Always Active)

These patterns are AI fingerprints regardless of language or discipline. They are checked BEFORE scene-specific rules.

### Structural Fingerprints

**【G1】Tricolon Detection**
Three-item symmetric parallel structures ("X, Y, and Z" / "X、Y 和 Z") used as argument scaffolding = high-confidence AI signal. Listing specific technical items is fine; using tricolon as rhetorical backbone is not.

**【G2】Resolution Closer**
Empty philosophical endings: "In the end, what truly matters is..." / "综上所述，XX具有重要的理论意义和实践价值" / "Ultimately, our work demonstrates..." Final sentence must be concrete.

**【G3】Repeated Rhetorical Skeleton**
Two or more consecutive paragraphs using the same sentence-level development pattern (concession→contrast, question→answer, etc.) = structural fingerprint. Any single skeleton appears ≤2 times in entire text.

**【G4】Hedge Opener Stacking**
"It is worth noting that..." / "It is important to note..." / "值得注意的是..." — empty filler. Delete and start from substance.

### Rhythm Fingerprints

**【G5】Uniform Sentence Length**
The fatal fingerprint: all sentences trending toward mean length. Coefficient of Variation (CV) of sentence word/character counts must be ≥ 0.35 for academic text, ≥ 0.40 for general text.

**【G6】Connector Stacking**
Furthermore / Moreover / Additionally / 此外 / 同时 / 与此同时 — max 1 per paragraph across all languages.

### Vocabulary Fingerprints

**【G7】Universal Banned Words**
These are AI-preferred regardless of scene: delve, leverage (as "use"), utilize, noteworthy, facilitates, showcases, multifaceted, landscape, pivotal, groundbreaking, shed light on, pave the way; 赋能, 助力, 深度赋能, 致力于, 引领, 毋庸置疑, 不可磨灭, 范式转移.

**【G8】Promotional Tone**
revolutionary / groundbreaking / unprecedented / 颠覆性 / 史无前例 — banned without specific supporting data.

**【G9】Perplexity Awareness**
Avoid always choosing the "most likely next word". Occasionally choose a semantically equivalent but less common alternative to break token-prediction patterns. NOT about using rare words.

### Meta Fingerprints

**【G10】Negation Parallel**
"It's not just X; it's Y" / "不仅仅是X，更是Y" — dramatic contrast flip. State Y directly.

**【G11】Shallow Progressive Description**
"X is transforming Y" / "正在重塑XX格局" / "正在引领XX变革" — empty progressive. Either explain HOW specifically, or don't say it.

**【G12】Copula Avoidance**
"X serves as Y" / "作为XX的重要组成部分" — just write "X is Y" / "是XX".

---

## S1: CS Academic Papers — Complete Rule Set (26 Rules)

Target voice: reads like a native-English CS researcher wrote it — precise but plain vocabulary, tight argumentation, rhythm variation but restrained.

### Vocabulary Rules

**【1】AI High-Frequency Word Ban**
Banned: delve, leverage, tapestry, underscore, pivotal, nuanced, landscape, utilize, foster, harness, elevate, intricate, multifaceted, cornerstone, testament, showcase, noteworthy, facilitate, encompass, realm, embark, streamline, bolster, cutting-edge, game-changer, accentuate, ameliorate, elucidate, endeavor, perpetuate, scrutinize, unveil, robust (banned outside statistical context), shed light on, pave the way, groundbreaking.

Replacements: leverage→use, delve into→investigate, utilize→use, noteworthy→notable, facilitates→enables, showcases→shows, elucidate→explain, endeavor→attempt, scrutinize→examine.

**【2】Simple & Clear Vocabulary**
Do not choose complex words to appear "advanced". Use: use (not utilize), show (not demonstrate, unless precise distinction needed), help (not facilitate), but (not however — however ≤1 per paragraph), so (not consequently).

**【3】Perplexity Awareness**
Avoid always choosing the "most likely next word". When a position has a standard expression and a semantically equivalent but less common alternative, occasionally choose the latter — break "best next token prediction" predictability. This is NOT about using rare words.

**【4】Promotional Tone Ban**
Never use revolutionary / groundbreaking / unprecedented without specific data. "significantly improves" must be followed by numbers or statistical significance.

**【5】Type-Token Ratio**
The same word/phrase must not appear in adjacent sentences (domain terms excepted). If previous sentence used "significantly", next sentence uses "notably" or no adverb at all.

### Sentence Rules

**【6】Mechanical Opener Ban**
Banned openers: First and foremost / It is worth noting that / It is important to note / In today's rapidly evolving / It bears mentioning that / It should be noted that / A comment is in order. Delete them; start from substance.

**【7】Hedge Opener Ban**
No vague qualifiers at sentence start. Distinguish "empty hedge" from "necessary academic qualifier" — "Our results suggest that..." is a legitimate qualifier; "It is worth noting that our results suggest..." is two layers of filler stacked.

**【8】Copula Avoidance Ban**
Do not use "X serves as Y" / "X stands as Y" / "X acts as Y" to avoid is/are. Just write "X is Y".

**【9】Negation Parallel Ban**
Do not use "It's not just X; it's Y" / "It's not X — it's Y" for dramatic contrast. State Y directly.

**【10】Shallow -ing Analysis Ban**
Do not use "X is transforming Y" / "Z is reshaping the landscape" as empty progressive descriptions. Either explain HOW specifically, or don't say it.

**【11】Vague Attribution Ban**
Do not use "many argue" / "some believe" / "experts say" without specific source. Either give citation, or rewrite as objective statement: "a common view is...".

**【12】Passive Voice Control**
Methods/Experimental Setup sections: passive allowed ("X was trained on..." is normal there). All other sections: ≤1 passive sentence per paragraph.

**【13】Throat-Clearing Sentence Ban**
Ban: "We investigate/examine/explore the relationship between..." — state what you found instead. Ban: "This paper contributes to the literature by..." — state what you did directly.

### Structure Rules

**【14】Tricolon Ban**
Do not use "X, Y, and Z" three-item parallel structures as argument scaffolding. This is one of the most prominent structural fingerprints of AI text. If three items genuinely need listing, expand with clauses of different lengths rather than symmetric parallels.

**【15】Resolution Closer Ban**
Do not use "In the end, what truly matters is..." / "Ultimately, our work demonstrates..." style empty endings. Final sentence of a paragraph must be a concrete conclusion or next step, not philosophical elevation.

**【16】One Topic Per Paragraph**
Each paragraph serves exactly one core point. When two topics compete, split into two paragraphs.

**【17】List Ban**
Must use flowing paragraphs; \begin{itemize} is prohibited. Only exception: algorithm steps (use algorithm environment).

**【18】Bold/Italic Emphasis Ban**
No \textbf or \emph to emphasize points in body text. Convey emphasis through sentence structure.

**【19】Repeated Rhetorical Skeleton Ban**
If two or more consecutive paragraphs use the same sentence-level development pattern (e.g., concession→contrast, question→answer), the pattern must be broken. Any single rhetorical skeleton appears ≤2 times in the entire paper.

### Rhythm Rules

**【20】Burstiness Enforcement**
Longest:shortest sentence word ratio ≥ 3:1. Within a paragraph, if 4+ consecutive sentences have similar length (±20%), must break. Academic writing is naturally slightly more uniform, so threshold is 4 sentences (not 3). But the 3:1 longest:shortest ratio is non-negotiable.

**【21】Uniform Rhythm Ban**
The fatal fingerprint of AI text is all sentences trending toward the mean length. Create long-short alternation during rewrites. But in academic contexts, extreme brevity is restrained — minimum short sentence: 10 words. No overly conversational staccato.

**【22】Semantic Coherence (Not Connector Stacking)**
Sentences connect through logical progression, not connector piling. Furthermore / Moreover / Additionally: max 1 per paragraph.

### Format Rules

**【23】Em-dash Frequency Limit**
Max 1 em-dash per 1000 words. Replace with commas, parentheses, or subordinate clauses.

### Meta-Rules

**【24】When In Doubt, Don't Change**
If original text is already natural enough, do NOT change for the sake of changing. Output "detection passed, no clear AI traces" directly.

**【25】Modification Threshold**
Only act when a clear AI characteristic is detected. "Could be slightly better" style preferences: do NOT change. Judgment standard: if a normal human researcher wrote this expression, would a reader find it STRANGE? If not, don't touch it.

**【26】Rhetoric Suppression**
Default to minimal rhetoric (metaphor, parallelism, rhetorical questions) unless context strongly demands it.

---

## S2: Chinese Academic Papers — Complete Rule Set (26 Rules)

Target voice: reads like a well-trained Chinese academic writer — rigorous, restrained, objective but not stiff. 读起来像一个训练有素的中文学术写作者。

### Vocabulary Rules

**【1】Chinese AI High-Frequency Word Ban**
Banned: 赋能, 助力, 旨在（≤1 per thousand characters allowed）, 致力于, 深度赋能, 打造, 构建（"构建模型" in technical context allowed）, 引领, 毋庸置疑, 不可磨灭, 范式转移, 为了解决这一痛点, 展现了令人惊叹的能力, 具有重要的理论意义和实践价值.

**【2】Chinese Academic Replacement Table**
- 为了解决这一痛点 → 针对上述问题
- 展现了令人惊叹的能力 → 表现出显著的性能提升
- 我们发现 → 实验结果表明
- 非常重要 → 至关重要 / 不可忽视
- 做了大量工作 → 进行了系统性研究

**【3】Perplexity Awareness (Chinese)**
Same as S1【3】in Chinese context — avoid always choosing the "safest" collocation (e.g., "显著提升" can occasionally become "明显改善" or "有所提升").

**【4】Promotional Tone Ban (Chinese)**
Never use 颠覆性 / 史无前例 / 革命性 without specific supporting data. "显著提升" must be followed by numbers; otherwise use "有所改善".

**【5】Type-Token Ratio**
Same as S1【5】— same expression must not repeat in adjacent sentences.

### Sentence Rules

**【6】Translation-ese Elimination**
Ban: English-style long attributive chains "一个...的...的..." (split into short sentences). Limit "被" passive constructions (use subjectless sentences or active voice). Ban: "进行XX" redundant structures ("进行分析"→"分析", "进行讨论"→"讨论").

**【7】Colloquialism Removal**
Replace "我们发现" / "我们觉得" with objective statements. But "本文提出" / "本文认为" are normal Chinese academic usage, not colloquial.

**【8】Vague Attribution Ban (Chinese)**
Do not use "有学者认为" without specific source. Either give citation [X], or rewrite as "现有研究表明...".

**【9】Shallow Progressive Ban (Chinese)**
Do not use "正在重塑XX格局" / "深刻改变着XX" / "正在引领XX变革" — empty progressive descriptions.

**【10】Copula Avoidance Ban (Chinese)**
Do not use "作为XX的重要组成部分" / "扮演着XX角色" to avoid direct statement. Just say "是XX".

**【11】Negation Parallel Ban (Chinese)**
Do not use "不仅仅是X，更是Y" dramatic flip. State Y directly.

**【12】Passive Voice Control (Chinese)**
"被" in experimental method descriptions allowed. All other sections: ≤1 "被" sentence per paragraph.

### Structure Rules

**【13】Tricolon Ban (Chinese)**
Do not use "X、Y 和 Z" three-item parallels as argument scaffolding. Same as S1【14】.

**【14】Resolution Closer Ban (Chinese)**
Do not use "综上所述，本文的研究具有重要的理论意义和实践价值" empty closers. NOTE: "综上所述" as a paragraph connector is normal academic Chinese — what's banned is empty elevation following it. If "综上所述" is followed by a concrete conclusion, it's allowed.

**【15】One Topic Per Paragraph**
Same as S1【16】.

**【16】Numbered List Control**
Chinese academic text occasionally needs "(1)...(2)...(3)..." numbering, but ONLY for listing specific steps/conditions. Never as argument scaffolding.

**【17】Repeated Rhetorical Skeleton Ban**
Same as S1【19】.

### Rhythm Rules

**【18】Burstiness Enforcement (Chinese)**
Longest:shortest sentence CHARACTER ratio ≥ 3:1. Within a paragraph, if 4+ consecutive sentences have similar character count (±20%), must break.

**【19】Uniform Rhythm Ban (Chinese)**
Same as S1【21】in Chinese — minimum short sentence: 15 characters.

**【20】Semantic Coherence (Chinese)**
此外 / 同时 / 与此同时 must not stack. Max 1 per paragraph.

### Format Rules

**【21】Dash Ban (Chinese Academic)**
Chinese academic papers do NOT use dashes (——). Use comma breaks or split into two sentences.

**【22】Colon Control**
Max 1 colon per paragraph. If what follows a colon is a phrase (not a complete sentence), consider replacing with a comma.

**【23】Double Quotes Only for First Occurrence**
Chinese double quotes ("" or "") only when a term first appears. Subsequent occurrences: no quotes.

### Meta-Rules

**【24】When In Doubt, Don't Change**
Same as S1【24】.

**【25】Modification Threshold**
Same as S1【25】.

**【26】Rhetoric Suppression**
Same as S1【26】.

---

## S3: Economics Academic Papers — Complete Rule Set (31 Rules)

Target voice: reads like a trained economist wrote it — clear causal narrative, personal style evident, measured rhetoric but not hollow. Economics writing differs significantly from CS: allows more rhetorical tools (em-dash, parenthetical insertions), emphasizes "simplest words for the most complex ideas".

### Vocabulary Rules

**【1】AI High-Frequency Word Ban (Economics-precise)**
Banned: delve, utilize, leverage (as verb meaning "use"), noteworthy, underscores, facilitates, showcases, it is important to note, multifaceted, landscape, pivotal, groundbreaking, shed light on, pave the way, robust (outside statistical context).
**Exception**: nuanced, intricate — these are disciplinary convention in economics, ALLOWED.

**【2】econ-write Phrases to Delete (mandatory check each one)**
- "It should be noted that" → just say it
- "It is easy to show that" → if easy, just show
- "A comment is in order" → just make the comment
- "In other words" → say it right the first time
- "It is worth noting that" → just say it
- "An important question in the literature is" → throat-clearing, delete
- "This paper contributes to the literature by" → state what you found
- "We investigate/examine/explore the relationship between" → state what you found
- "The remainder of this paper is organized as follows" → give roadmap directly
- "We perform/conduct/carry out a regression" → "I estimate" or "I regress Y on X"
- "Results are reported in Table X" → "Table X shows..."
- Search for "that" and delete everything before it when possible

**【3】Simple & Clear Vocabulary**
use not utilize, but not however, so not consequently. Use concrete words: people not "agents", workers not "labor market participants".

**【4】Self-Promotion Ban**
Never use adjectives describing your own work — "striking results", "very significant", "novel contribution". Let results speak. No double adjectives ("very novel").

**【5】Perplexity Awareness**
Same as S1【3】.

**【6】Type-Token Ratio**
Same as S1【5】.

**【7】Field-Specific Vocabulary Naturally**
Use domain terms where appropriate: "extensive margin" (labor), "pass-through" (IO), "treatment on the treated" (program evaluation). Generic phrasing signals AI.

### Sentence Rules

**【8】Sentence Structure Basics**
Use normal sentence structure: subject, verb, object. Keep sentences short. Keep down the number of clauses. Every sentence must say something — read each sentence and ask: does it mean what it says?

**【9】Voice and Perspective**
Single author: use "I" (not royal "we"). Multiple authors: "we" referring to author team, consistent throughout. Tables and figures can be subjects: "Table 5 presents...". Never write "one can see that...".

**【10】Passive Voice Control**
Methods and data description: fully allowed ("The sample was restricted to..." / "Wages were measured using administrative tax records"). All other sections: use active voice.

**【11】Vague Attribution Ban**
Same as S1【11】.

**【12】Clothe the Naked "this"**
Write "This regression shows..." not "This shows...". Always attach a noun to demonstrative pronouns.

**【13】Pronoun Precision**
"Where" refers to a place. "In which" refers to a model. Write "models in which consumers have shocks" not "models where consumers have shocks".

### Structure Rules

**【14】Tricolon Control**
Argument scaffolding must not depend on tricolon. BUT: listing specific variables/controls, "X, Y, and Z" parallel is allowed.

**【15】Resolution Closer Ban**
Same as S1【15】.

**【16】One Topic Per Paragraph**
Topic sentence first. Paragraphs should flow logically from one to the next.

**【17】Minimize Forward/Backward References**
"As we will see in Table 6" / "Recall from Section 2 that..." — these often signal material is in the wrong order. If a reader needs information now, present it now.

**【18】Footnotes Control**
Do not use footnotes for parenthetical comments. If important, put in text. If not, delete. Footnotes only for things typical readers can skip.

**【19】Repeated Rhetorical Skeleton Ban**
Same as S1【19】.

### Rhythm Rules

**【20】Burstiness Enforcement**
Mix short sentences (8-12 words) with longer ones (15-25 words). AI tends toward uniform medium-length sentences.

**【21】Allow Natural Roughness**
Not every transition needs to be perfectly smooth. Real papers have some friction between sections. A period and a new topic sentence is fine.

**【22】Avoid Perfect Parallel Structure in Every List**
Vary your constructions. Real writing is slightly irregular.

**【23】Semantic Coherence**
Same as S1【22】.

### Format Rules

**【24】Em-dash: FULLY ALLOWED**
Economics writing uses em-dash as a common insertion and explanation tool. No frequency limit — real academics use these for qualifications and side notes.

**【25】Parenthetical Insertions: ALLOWED**
Economics prose heavily uses parentheses for supplementary explanation. This is style, not AI fingerprint.

**【26】Numbers and Notation**
Use 2-3 significant digits. Use sensible units (percentages, not 0.0000023). Remind readers of definitions: "the elasticity of substitution, σ, equals 3".

### Hedging Rules (Economics-Specific)

**【27】Allow Measured Academic Hedging**
"appears to", "suggests that", "is consistent with", "This likely reflects...", "One interpretation is..." — these are NECESSARY tools for expressing causal carefulness in economics. NOT AI-flavor. But empty Hedge Openers ("It is worth noting that...") are still banned.

**【28】Hedge With Content, Not Filler**
Write "This likely reflects..." or "One interpretation is..." when warranted. AI either over-hedges everything or never hedges. The key: hedge with content, not with filler.

### Voice Profile Enforcement

**【29】Voice Profile Mandatory When Available**
If the user has provided writing samples, voice metrics MUST be quantified and injected. Economics papers have extremely strong personal style (Cochrane vs McCloskey are completely different). The goal of de-AI is "sounds like this author" not "sounds like generic academia".

### Meta-Rules

**【30】When In Doubt, Don't Change**
Same as S1【24】. Be specific about institutions: Name the actual dataset, agency, policy, or country. AI defaults to generic placeholder language.

**【31】Modification Threshold**
Same as S1【25】.

---

## S2 Extended: 12 Signal Categories with Examples (中文场景详细检测信号)

> 以下 12 类信号为 S2 场景的检测引擎提供具体判定依据，包含 AI 典型表现示例和自然写法对比。
> 每类信号附带密度判定标准，供 deai_engine 程序化检测时使用。

### 【S2-Sig01】AI高频词汇 (AI_VOCABULARY_ZH)

**禁用词清单**: 赋能、助力、旨在（≤每千字1次可保留）、致力于、深度赋能、打造、引领、毋庸置疑、不可磨灭、范式转移、不可忽视（作为句首引导语时）

**AI典型 → 自然替换**:

| AI模板化表达 | 自然学术表达 |
|---|---|
| 值得注意的是 | （删除引导语，直接陈述） |
| 本文旨在 | 本文提出 / 本文研究了 |
| 具有重要意义 | （用具体贡献替代抽象判断） |
| 深度赋能 | 支持 / 增强 / 提升了...的能力 |
| 致力于 | （删除，直接说做了什么） |
| 展现了令人惊叹的能力 | 表现出显著的性能提升 |
| 为解决这一痛点 | 针对上述问题 |

---

### 【S2-Sig02】机械化连接词堆砌 (MECHANICAL_CONNECTORS_ZH)

**判定标准**: 同一段内出现2个以上"此外/同时/与此同时/另外"判定为堆砌；全文"首先...其次...最后"模板出现≥2次判定为机械化。

**AI典型 → 自然替换**:

| AI机械连接 | 自然衔接方式 |
|---|---|
| 首先...其次...再次...最后 | 通过语义递进自然过渡，无需四连标记 |
| 此外 + 与此同时 + 同样 | 同一段内最多保留1个连接词 |
| 然而，值得注意的是 | 但 / 不过（直接转折，删除引导语） |
| 一方面...另一方面（每段） | 偶尔使用，不作为固定模板 |

---

### 【S2-Sig03】句长均匀化 (RHYTHM_UNIFORMITY_ZH)

**判定标准**: 一段内连续4句以上字数偏差在±20%以内判定为匀速；最长句与最短句字数比<3:1判定为节奏单一。学术中文最短句下限为15字。

**AI典型**（四句句长几乎一致）:
```
本文提出了一种基于注意力机制的文本分类方法。（22字）
该方法通过多头注意力捕获文本的语义特征信息。（22字）
实验结果表明该方法在多个基准数据集上效果良好。（23字）
与现有方法相比本文方法在准确率上有明显提升。（22字）
```

**自然写法**（句长交错 46-8-36-18）:
```
本文提出了一种基于多头注意力机制的文本分类方法，核心思路是将全局语义与局部特征在统一框架内对齐。（46字）
实验表明效果显著。（8字）
在 IMDB、SST-2 等五个基准集上，准确率分别比基线高出 2.1%、1.7%、3.4%、2.8% 和 1.9%。（36字）
这一提升主要来自注意力头的稀疏化设计。（18字）
```

---

### 【S2-Sig04】过度正式化/大词化 (INFLATED_FORMALITY_ZH)

**判定标准**: 动词前有2个以上修饰词（"深入而全面地"）、名词短语冗余叠加（"参考价值和借鉴意义"）判定为大词化。

| AI过度正式化 | 自然学术表达 |
|---|---|
| 进行了深入探讨 | 讨论了 / 分析了 |
| 具有一定的参考价值 | 可供参考 |
| 开展了系统性研究 | 研究了 / 系统地研究了 |
| 实现了性能的显著提升 | 性能提升了X% |
| 做出了重要贡献 | 贡献在于（具体说） |
| 取得了令人满意的效果 | 效果良好 / 准确率达到X |

---

### 【S2-Sig05】三段式/排比泛滥 (TRICOLON_PATTERN_ZH)

**判定标准**: 三项对称并列作为论证主干出现≥2次/千字判定为异常。

| AI三联式 | 自然表达 |
|---|---|
| 不仅A，而且B，更C | 主要改善了A。B方面也有提升，降幅约X% |
| 兼具A、B和C | 在A方面表现突出；B的改善来自于...（分层展开） |
| 从A、B到C | 覆盖了A和B阶段（不必凑三个） |

---

### 【S2-Sig06】空洞学术对冲 (EMPTY_HEDGING_ZH)

**判定标准**: "一定程度上""某种意义上""在某些情况下"不跟具体量化或条件说明时判定为空洞对冲。合理限定（"在本实验条件下""当样本量<100时"）不算。

| AI空洞对冲 | 自然学术限定 |
|---|---|
| 在一定程度上 | （删除，或给具体程度：提升了12%） |
| 某种意义上来说 | （删除，直接陈述） |
| 在某些情况下 | 在小样本场景下 / 当输入长度>512时 |
| 可能具有一定的局限性 | 在X场景下失效，原因是... |

---

### 【S2-Sig07】推销语气/过度宣称 (PROMOTIONAL_TONE_ZH)

**判定标准**: "首次/首创/开创性/突破性/颠覆性/革命性"无具体数据或文献支撑时判定为过度宣称；"显著提升/显著优于"不跟数字时判定为推销语气。

| AI推销语气 | 克制学术表达 |
|---|---|
| 首次提出 | 据我们所知，现有工作尚未... |
| 填补了空白 | 现有方法未涉及X场景，本文进行了初步探索 |
| 显著优于所有方法 | 在数据集A上F1提升2.3%，在B上提升1.8% |
| 开创性/突破性/颠覆性 | （删除形容词，直接报告结果） |
| 全新的研究范式 | 提出了一种不同于...的方法 |

---

### 【S2-Sig08】对称结构过度 (SYMMETRIC_STRUCTURE_ZH)

**判定标准**: 连续3段以上使用相同句式开头判定为对称过度；段落长度标准差<15%判定为结构刻板。

| AI对称结构 | 自然非对称写法 |
|---|---|
| 每段"在X方面" | 首段用"在X方面"，后续直接从内容切入 |
| 每段4-5句等长 | 有的段落2句点明即可，有的需7-8句展开 |
| Related Work每段"提出了...实现了...但存在..." | 重要工作详细讨论，次要工作一句带过 |
| 每节开头"本节将介绍" | 首节用引导语，后续直接进入内容 |

---

### 【S2-Sig09】名词化堆砌 (ABSTRACT_NOMINALIZATION_ZH)

**判定标准**: "对...进行了..."结构每千字出现≥3次判定为名词化堆砌；"实现了...的..."结构每千字≥2次同理。基于密度而非单次出现。

| AI名词化堆砌 | 直接动词表达 |
|---|---|
| 对X进行了分析 | 分析了X |
| 实现了X的提升 | X提升了 / 提升了X |
| 完成了对X的处理 | 处理了X |
| 对X进行了优化 | 优化了X |
| 开展了X方面的研究 | 研究了X |

---

### 【S2-Sig10】话语标记过度 (DISCOURSE_MARKER_OVERUSE_ZH)

**判定标准**: "然而/因此/此外/同时/另外/具体而言"类标记词每段首句出现率>50%判定为过度；同一段内出现≥2个同类标记判定为堆砌。

频率上限参考：全文"然而"≤3次，"因此"≤4次。

---

### 【S2-Sig11】具体性不足 (LACK_OF_SPECIFICITY_ZH)

**判定标准**: "相关研究/已有研究/有学者"不跟引用标号判定为模糊归因；"显著/明显/大幅"不跟具体数字判定为缺乏量化；"多个/大量/若干"不跟具体列举判定为抽象化。

| AI模糊表述 | 具体学术表达 |
|---|---|
| 相关研究表明 | Zhang et al. [12] 发现 |
| 显著提升了性能 | F1提升了2.3个百分点 |
| 多个数据集上 | 在GLUE、SQuAD 2.0和MNLI三个数据集上 |
| 近年来随着...快速发展 | 自2017年Transformer架构提出以来 |
| 大量实验 | 在4种设置下共12组对比实验 |

---

### 【S2-Sig12】模板骨架痕迹 (TEMPLATE_SKELETON_ZH)

**判定标准**: 出现3个以上模板句式判定为骨架痕迹重；每节开头都有"本节将..."判定为模板化严重。

**典型模板句式**:
- Introduction 末尾："本文的主要贡献如下：（1）...（2）...（3）..."
- 每节开头："本节将介绍..."
- Related Work 结尾："综上所述，现有方法仍存在以下不足：..."
- Conclusion 开头重述方法："本文提出了一种基于...的...方法"
- Conclusion 结尾："未来工作将从以下几个方面展开：..."

**自然替代**:
- 贡献在Introduction行文中自然引出，非必须列编号
- 节首直接进入内容，读者从标题已知主题
- 讨论具体方法时指出各自局限，不集中列举
- 提出1-2个最有价值的方向展开说明，而非泛泛列举

---

### S2 中文场景冲突裁决补充

| 冲突场景 | 裁决 |
|---|---|
| "综上所述" + 具体结论 | 允许——禁止的是 + 空洞升华 |
| "构建模型/数据集" | 技术描述中"构建"是合理动词，允许 |
| "旨在"出现≤每千字1次 | 低频允许 |
| "不难发现" | 学术论文正常用法，允许 |
| "被"字句在方法描述段 | 方法/实验段允许被动 |
| "首先...其次...最后"描述实验步骤 | 有序步骤允许——禁止用作论证骨架 |
| 编号列举"(1)(2)(3)" | 具体条件/步骤时允许，段落骨架时禁止 |

---

## Scoring

Each detected signal contributes to the overall naturalness score:
- **confidence**: 0.0-1.0 per signal (how certain it's AI-generated, not just unusual style)
- **overall_score**: 1.0 = fully natural, 0.0 = maximally AI-like
- **threshold**: overall_score >= 0.7 → PASS (no fix needed)
- Signal count alone is insufficient — a single high-confidence banned word matters less than 3 structural patterns
- Multiple structural patterns in one paragraph = high confidence (0.8+)
- Disciplinary conventions that happen to overlap with AI patterns are NOT signals

---

## Fix Principles

1. **Minimum slice**: fix only the flagged sentence, never rewrite surrounding context
2. **Preserve meaning**: the fix must be semantically equivalent to the original
3. **No quality loss** (Red Line 3): if the fix reduces readability or introduces error, keep original
4. **Academic register**: replacements must maintain appropriate formality
5. **Author voice**: if the paper has a consistent style (e.g., uses em-dashes throughout), respect it even if it conflicts with general rules
6. **Perplexity injection**: when fixing a flagged sentence, occasionally choose a less-predictable (but semantically equivalent) word over the "default" academic phrasing. Goal: break "best next token prediction" patterns without using rare words
7. **Minimum short sentence floor**: fixed sentences must be ≥ 10 words (English) or ≥ 15 characters (Chinese) in academic context
8. **Voice Profile priority**: when Voice Profile conflicts with scene rules (e.g., author's natural style has uniform sentence length), Voice Profile wins — goal is "sounds like this author" not "sounds like generic human"

---

## Priority Chain (Conflict Resolution)

```
User explicit request > Voice Profile > Scene rules > S_GENERAL > Default: don't change
```

When Voice Profile says the author naturally uses uniform rhythm but S1【20】says break it → Voice Profile wins.
When economics convention allows em-dash but S1 limits it → scene identification determines which rule applies.
When "could be better" but not clearly AI → default: don't change.

### Common Conflict Resolutions

| Conflict | Resolution |
|----------|-----------|
| Economics paper uses many em-dashes | S3 allows em-dash, keep |
| Chinese academic uses "旨在" | Low frequency (≤1 per 1000 chars) allowed |
| "综上所述" in Chinese academic | If followed by concrete conclusion, allowed; if followed by empty elevation, banned |
| Text seems both academic and blog-like | Default S1/S2 by language; user can override |
| Post-fix rhythm changed but less fluent than original | Fluency > rule compliance. Roll back fix |
| Chinese academic uses "不难发现" | Normal academic usage, keep |

### Absolute Prohibitions (All Scenes)

| Prohibited Action | Reason |
|------------------|--------|
| Fixing when original is already natural | Destroys author style; creates new "over-processed" AI flavor |
| Changing core argument or logic | De-AI is style processing, not content rewrite |
| Replacing one AI template with another | "首先...其次...最后" → "一方面...另一方面" is NOT de-AI |
| Inserting meaningless short sentences for rhythm | Short sentences must carry information |
| Replacing domain terminology | Transformer, attention, 回归分析 — NOT AI flavor |

---

## Four-Layer Post-Fix Self-Check Protocol

After rewrite completion, check in this order. Any layer failing → return to fix step.

**Layer 1: Structure Check**
- Any residual tricolons?
- Any Resolution Closers?
- Maintains one-topic-per-paragraph?
- Any repeated rhetorical skeletons (same pattern in consecutive paragraphs)?

**Layer 2: Rhythm Check**
- Longest:shortest ≥ 3:1?
- No 4+ consecutive sentences within ±20% length (academic) / 3+ (general)?
- Overall feel: no "uniform speed" sensation?
- CV ≥ 0.35 (programmatic validation)?

**Layer 3: Prohibited Zone Check**
- All banned words from current scene cleared?
- All banned punctuation patterns from current scene handled?
- All banned sentence patterns from current scene replaced?

**Layer 4: Breath Check**
- Reading the full text — does it feel human-written overall?
- No over-editing that made text stiff/unnatural?
- Core meaning and argument logic preserved?
- If Voice Profile exists: style metrics within ±20%?

---

## Runtime Self-Correction

If you observe any of these states, stop and correct:

| State Signal | Action |
|---|---|
| All sentences roughly same length after fix | Return to rhythm rules, create variation |
| 3+ connectors used consecutively | Delete excess connectors |
| Fixed text > 30% longer than original | Over-expansion. De-AI should be equal length or shorter |
| Fixed text contains banned words | Immediately replace |
| Unsure which scene rules to activate | Return to scene routing |
| Many changes but can't justify each one | Stop. Every change needs a rule-based justification |
| Original text already reads naturally | Output "detection passed" directly |
