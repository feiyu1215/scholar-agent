# Recall Verification Report (Post-P0 Fix)

**Generated**: 2026-05-28 00:19:00
**Model**: gpt-4.1
**Total Runtime**: 296.5s
**Papers**: 1

---

## Summary: Baseline vs Post-Fix

| Metric | Baseline | Post-Fix | Delta |
|--------|----------|----------|-------|
| Precision | 0.583 | 0.375 | -0.208 |
| Recall | 0.389 | 0.300 | -0.089 |
| F1 | 0.463 | 0.333 | -0.130 |

---

## paper_003: A Second-Best Argument for Low Optimal Tariffs

**Metrics**: P=0.375 R=0.300 F1=0.333
**Baseline**: P=0.571 R=0.444 F1=0.499
**Agent produced**: 8 findings | Gold: 10 | Matched: 3
**Runtime**: 291.7s | Turns: N/A

### Matched Findings

- **Gold G001** ↔ Agent #2 (sim=0.421)
  - Gold: 核心定量结论'中位最优关税10%'依赖多个校准参数（σ₁=4.4, θ₁=5.1, ω=σ/1.25等），全文无任何形式的参数敏感性分析。搜索'sensitivity'、'robustness'、'b...
  - Agent: [参数敏感性分析缺失] 论文在校准参数（如σ_s, θ_s, γ_is等）时，未报告对关键参数的敏感性分析。Table 1 仅给出参数点估计和分位数，但未展示模型对参数变动的鲁棒性或敏感性（如弹性变化...

- **Gold G002** ↔ Agent #1 (sim=0.352)
  - Gold: Sections 2-4 所有理论推导基于标准 CES（ω=σ），Section 5 定量模型用嵌套 CES（ω=σ/1.25）。过渡仅两段话，未推导嵌套 CES 下 F(t*) 中 M(t) 和 R...
  - Agent: [数学推导] Theorem 1 的证明依赖于中间值定理和连续性假设，但部分区间的极值点（如 t^*, t^R, t^D, t^A）定义存在多解且未严格证明唯一性，部分 Lemma 仅给出“suffi...

- **Gold G003** ↔ Agent #4 (sim=0.304)
  - Gold: 定量模型对所有186国采用'one country at a time'单边最优关税计算，假设其他国家价格不变（小国假设）。脚注2仅提及大国文献但未做调整。对美国、中国等贸易大国，此假设的approx...
  - Agent: [模型假设合理性] 论文假设小国、完全竞争的非贸易部门、CES 偏好、Pareto 生产率分布、部分参数 exogenous（如 elasticities），但未充分讨论这些假设对最优关税结论的外推性...

### Missed Gold Findings (False Negatives)

- **G004** [medium] 标准 CES → 嵌套 CES 过渡缺乏显式符号映射。topt 公式从 θ₁/(θ₁-ρ₁) 变为 ω₁/[ω₁-(σ₁-1)/θ₁]，中间无桥接推导。理论与定量部分的参数定义（α_i, γ_i1, σ_s, θ_s）不统一。...
- **G005** [medium] 公式(44) sector 2 的 Pareto shape parameter 写为 θ₁，应为 θ₂。公式(43) sector 1 用 θ₁ 正确；公式(45) free entry 条件用 θ₂。确认为排版错误。...
- **G006** [medium] ωs=σs/1.25 校准仅一句话：'Setting ωs = σs/1.25 best reproduces global trade growth between 1990 and 2010.' 无 fit quality 度量、无替代...
- **G007** [medium] Grid search 步长为'mostly 2.5%'——何处不同、如何处理非grid最优值、收敛判据等细节不足。对于数值方法论文，方法透明度应更高。...
- **G008** [medium] 论文声称文献空白（'As far as we are aware, the literature has not addressed...'），但 Costinot et al. (2020) 已讨论多部门非均匀关税。本文 novelty ...
- **G009** [medium] 小国假设（价格接受者）应用于所有186国包括美国、中国。脚注2和OPEC讨论虽有提及但未量化：terms-of-trade effects 对大国最优关税的修正幅度为多少？...
- **G010** [low] 双重边际化（double-marginalization）的定量重要性在 variable markup（非CES）模型下可能显著不同。论文将此作为范围界定（scope limitation）是合理的，但可以更明确讨论。...

### False Positives (Agent-only)

- [medium] [数据一致性] Table 1、Figure 2 及正文对最优关税的数值描述存在轻微不一致：Table 1 的参数用于模型校准，Figure 2 显示 median optimal tariff 为 10%，正文称制造业 one-secto...
- [medium] [引用准确性] 论文在介绍相关文献时，部分引用表述存在不够精确的问题。例如，Costinot, Rodríguez-Clare and Werning (2020) 被描述为“first to extend the analysis to ...
- [medium] [写作表达] 论文部分公式和符号定义不够自洽，部分变量（如 λ、γ、T、D）在不同附录和正文中符号重用但含义变化，增加理解难度，建议统一符号表或在每次出现时明确定义。...
- [high] [数学推导] Melitz-Chaney 类模型中最优关税的固定点（如 Theorem 1 所述）在一般条件下存在唯一解，但在论文附录的具体推导中，部分区间（如 t^*, t^R, t^D, t^A）定义允许多解，且未严格证明唯一性。外部文...
- [high] [实证结果解释] 论文的实证部分（Section 5, Figure 2）报告了最优关税的国别分布和行业差异，但未充分讨论模型与现实的偏差、参数不确定性对结果的影响、以及高关税国家的特殊性。部分结果（如油气出口国高关税）仅用理论弹性解释，缺...

---

## P0 Fix Effectiveness Analysis

P0 修复目标瓶颈 vs 验证结果：

| 修复 | 目标 Gold IDs | 是否被新发现 | 备注 |
|------|--------------|-------------|------|
| AppendixMathAuditSkill | G001 (001), G005 (003) | 见上方匹配结果 | |
| ConsistencyValidator Rule9 | G005 (001) | 见上方匹配结果 | |
| PCG appendix weight | G001 (001), G005 (003) | 见上方匹配结果 | |
