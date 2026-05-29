# Recall Verification Report (Post-P0 Fix)

**Generated**: 2026-05-28 03:47:11
**Model**: gpt-4.1
**Total Runtime**: 295.8s
**Papers**: 1

---

## Summary: Baseline vs Post-Fix

| Metric | Baseline | Post-Fix | Delta |
|--------|----------|----------|-------|
| Precision | 0.583 | 0.556 | -0.027 |
| Recall | 0.389 | 0.500 | +0.111 |
| F1 | 0.463 | 0.526 | +0.063 |

---

## paper_003: A Second-Best Argument for Low Optimal Tariffs

**Metrics**: P=0.556 R=0.500 F1=0.526
**Baseline**: P=0.571 R=0.444 F1=0.499
**Agent produced**: 9 findings | Gold: 10 | Matched: 5
**Runtime**: 290.2s | Turns: N/A

### Matched Findings

- **Gold G001** ↔ Agent #2 (sim=0.545)
  - Gold: 核心定量结论'中位最优关税10%'依赖多个校准参数（σ₁=4.4, θ₁=5.1, ω=σ/1.25等），全文无任何形式的参数敏感性分析。搜索'sensitivity'、'robustness'、'b...
  - Agent: [校准参数敏感性分析缺失] 只报告了参数点估计和分位数（如Table 1），未见对主要结论（如最优关税、福利变化）在参数变动下的敏感性分析。...

- **Gold G004** ↔ Agent #4 (sim=0.522)
  - Gold: 标准 CES → 嵌套 CES 过渡缺乏显式符号映射。topt 公式从 θ₁/(θ₁-ρ₁) 变为 ω₁/[ω₁-(σ₁-1)/θ₁]，中间无桥接推导。理论与定量部分的参数定义（α_i, γ_i1, ...
  - Agent: [理论到定量模型过渡跳步] 定量模型采用嵌套CES结构（ω_s ≠ σ_s），但理论部分主要为单层CES，未见详细推导两者映射关系，参数如何传递、机制是否一致未交代清楚。...

- **Gold G007** ↔ Agent #1 (sim=0.396)
  - Gold: Grid search 步长为'mostly 2.5%'——何处不同、如何处理非grid最优值、收敛判据等细节不足。对于数值方法论文，方法透明度应更高。...
  - Agent: [数值方法透明度不足] 定量模型部分仅提及“grid search over positive and negative tariffs... increments mostly of 2.5%”，但...

- **Gold G003** ↔ Agent #3 (sim=0.274)
  - Gold: 定量模型对所有186国采用'one country at a time'单边最优关税计算，假设其他国家价格不变（小国假设）。脚注2仅提及大国文献但未做调整。对美国、中国等贸易大国，此假设的approx...
  - Agent: [模型假设适用边界未充分讨论] 理论模型基于 small open economy 假设，但定量分析覆盖如西班牙、香港等大经济体，未见对小国假设失效时的适用边界讨论。...

- **Gold G002** ↔ Agent #6 (sim=0.261)
  - Gold: Sections 2-4 所有理论推导基于标准 CES（ω=σ），Section 5 定量模型用嵌套 CES（ω=σ/1.25）。过渡仅两段话，未推导嵌套 CES 下 F(t*) 中 M(t) 和 R...
  - Agent: [符号和下标定义不一致，存在混淆] γ_is 在正文和表格中既指 labor share（理论模型），又指 value-added share（定量校准），但两者在实际数据中可能有较大差异。σ_s、θ...

### Missed Gold Findings (False Negatives)

- **G005** [medium] 公式(44) sector 2 的 Pareto shape parameter 写为 θ₁，应为 θ₂。公式(43) sector 1 用 θ₁ 正确；公式(45) free entry 条件用 θ₂。确认为排版错误。...
- **G006** [medium] ωs=σs/1.25 校准仅一句话：'Setting ωs = σs/1.25 best reproduces global trade growth between 1990 and 2010.' 无 fit quality 度量、无替代...
- **G008** [medium] 论文声称文献空白（'As far as we are aware, the literature has not addressed...'），但 Costinot et al. (2020) 已讨论多部门非均匀关税。本文 novelty ...
- **G009** [medium] 小国假设（价格接受者）应用于所有186国包括美国、中国。脚注2和OPEC讨论虽有提及但未量化：terms-of-trade effects 对大国最优关税的修正幅度为多少？...
- **G010** [low] 双重边际化（double-marginalization）的定量重要性在 variable markup（非CES）模型下可能显著不同。论文将此作为范围界定（scope limitation）是合理的，但可以更明确讨论。...

### False Positives (Agent-only)

- [medium] [符号和下标不一致风险] σ_s、θ_s、γ_is、λ_ij等符号在正文、表格、附录间多次出现，部分定义有变动（如γ_is既指labor share又指value-added share），需系统核查是否全程一致。...
- [high] [附录数学推导链条存在跳步和符号漂移] Theorem 1 及其证明（附录 D）大量依赖于 T(t_i)、A(t_i)、D(t_i)、M(t_i) 等复合函数的递归定义和符号变换，部分关键步骤（如 Lemma 8、Lemma 9 的 suf...
- [high] [定量结果稳健性和实证支持不足] 论文报告了最优关税的数值分布（如10%、7.5%、20%等），但未见对结果的稳健性检验（如参数变动、alternative calibration、不同年份数据）或与实证政策/历史关税的对比。仅用2010年...
- [medium] [实证结果解释合理性不足] 论文在结论部分未对定量结果的现实意义、政策相关性、与历史关税政策的对比进行充分讨论。虽然报告了最优关税的数值分布，但未解释这些结果为何与实际政策存在差异、或为何部分国家出现负最优关税。缺乏对模型结果与现实经济现象...

---

## P0 Fix Effectiveness Analysis

P0 修复目标瓶颈 vs 验证结果：

| 修复 | 目标 Gold IDs | 是否被新发现 | 备注 |
|------|--------------|-------------|------|
| AppendixMathAuditSkill | G001 (001), G005 (003) | 见上方匹配结果 | |
| ConsistencyValidator Rule9 | G005 (001) | 见上方匹配结果 | |
| PCG appendix weight | G001 (001), G005 (003) | 见上方匹配结果 | |
