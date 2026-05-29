# Recall Verification Report (Post-P0 Fix)

**Generated**: 2026-05-29 10:59:40
**Model**: gpt-4.1
**Total Runtime**: 934.0s
**Papers**: 1

---

## Summary: Baseline vs Post-Fix

| Metric | Baseline | Post-Fix | Delta |
|--------|----------|----------|-------|
| Precision | 0.583 | 0.500 | -0.083 |
| Recall | 0.389 | 0.556 | +0.167 |
| F1 | 0.463 | 0.526 | +0.063 |

---

## paper_001: Environmental Externalities and Free-riding in the Household

**Metrics**: P=0.500 R=0.556 F1=0.526
**Baseline**: P=0.600 R=0.333 F1=0.426
**Agent produced**: 10 findings | Gold: 9 | Matched: 5
**Runtime**: 927.0s | Turns: N/A

### Matched Findings

- **Gold G009** ↔ Agent #2 (sim=0.374)
  - Gold: DID 平行趋势仅通过图形视觉检验，无正式 event-study 回归或前置期系数检验。但注意：这是 RCT 设计（AEARCTR-0000660），平行趋势由随机化在期望意义上保证，event-s...
  - Agent: [Methodological limitation: Parallel trends assumption inadequately tested] The DiD estimation relie...

- **Gold G006** ↔ Agent #6 (sim=0.364)
  - Gold: 部分 heterogeneity 结果对 information + incentive treatment pooling 敏感。Appendix Table A.11 显示异质性部分与 infor...
  - Agent: [Robustness checks comprehensive but lacking some sensitivity analyses] The paper conducts multiple ...

- **Gold G003** ↔ Agent #9 (sim=0.350)
  - Gold: 弹性 -0.27 的三个转换假设（风险中性、离散≈连续、概率≈确定性）已在脚注列出，但代入 Table 11 公式(6)做政策校准时，假设偏离对 θ=0.23 的影响未被量化。缺敏感性分析而非缺透明度...
  - Agent: [Discussion on assumption limitations and policy extrapolation boundaries insufficient] The conclusi...

- **Gold G004** ↔ Agent #5 (sim=0.271)
  - Gold: Dictator game 作为 intrahousehold efficiency 的代理变量，全文无直接 construct validity 验证（DG sharing → 实际用水保护行为的因...
  - Agent: [Main results support and treatment effect heterogeneity] The incentive treatment leads to a statist...

- **Gold G002** ↔ Agent #4 (sim=0.256)
  - Gold: θ=1 归一化假设（设定高效率组无扭曲）无敏感性分析。论文承认 'underestimating the average distortion' 但未量化：若 θ_high=0.8，政策数字（最优价格...
  - Agent: [Price elasticity estimation and literature comparison limitations] The estimated short-run price el...

### Missed Gold Findings (False Negatives)

- **G001** [high] Result 2 推导对 γ_i 求导正确，但结论行误写为 ∂²w*_i/∂p∂α_i < 0（应为 γ_i）。同附录 semi-elasticity 部分正确写为 γ_i，确认是数学符号笔误。...
- **G005** [medium] Table A.3（Information treatment balance）与 Table A.4（Credibility treatment balance）数据完全重复——所有均值、SD、p值完全一致。两个处理的分组方式不同（1/4...
- **G007** [medium] 13 个 survey measures（Table 6-8）、多重 heterogeneity 检验无多重检验校正（Bonferroni/FDR）。虽然预注册可部分缓解，但论文未明确区分预注册 vs 探索性分析。...
- **G008** [medium] 理论模型为两人家庭（bill-payer + spouse），但实际样本家庭平均约6人。模型推导的 θ（效率参数）在多人决策环境中的解释和适用性未被讨论。...

### False Positives (Agent-only)

- [high] [Sample selection and attrition bias] The sample construction applies multiple exclusion criteria (e.g., households with...
- [high] [Assumption validity and discussion limitation on household water consumption observability] The core model assumes indi...
- [medium] [Measurement error risk addressed and limited impact] Water use is measured via monthly meter readings, excluding months...
- [medium] [Data consistency confirmed] The sample size, means, standard deviations, and treatment/control group counts reported in...
- [high] [Parameter notation consistency] Key parameters (λ, α, θ, χ, τ) are consistently defined across Sections 2.1, 2.2, 2.3, ...

---

## P0 Fix Effectiveness Analysis

P0 修复目标瓶颈 vs 验证结果：

| 修复 | 目标 Gold IDs | 是否被新发现 | 备注 |
|------|--------------|-------------|------|
| AppendixMathAuditSkill | G001 (001), G005 (003) | 见上方匹配结果 | |
| ConsistencyValidator Rule9 | G005 (001) | 见上方匹配结果 | |
| PCG appendix weight | G001 (001), G005 (003) | 见上方匹配结果 | |
