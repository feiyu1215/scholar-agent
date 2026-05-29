# Recall Verification Report (Post-P0 Fix)

**Generated**: 2026-05-28 01:46:09
**Model**: gpt-4.1
**Total Runtime**: 145.4s
**Papers**: 1

---

## Summary: Baseline vs Post-Fix

| Metric | Baseline | Post-Fix | Delta |
|--------|----------|----------|-------|
| Precision | 0.583 | 0.286 | -0.297 |
| Recall | 0.389 | 0.200 | -0.189 |
| F1 | 0.463 | 0.235 | -0.228 |

---

## paper_003: A Second-Best Argument for Low Optimal Tariffs

**Metrics**: P=0.286 R=0.200 F1=0.235
**Baseline**: P=0.571 R=0.444 F1=0.499
**Agent produced**: 7 findings | Gold: 10 | Matched: 2
**Runtime**: 140.5s | Turns: N/A

### Matched Findings

- **Gold G001** ↔ Agent #1 (sim=0.522)
  - Gold: 核心定量结论'中位最优关税10%'依赖多个校准参数（σ₁=4.4, θ₁=5.1, ω=σ/1.25等），全文无任何形式的参数敏感性分析。搜索'sensitivity'、'robustness'、'b...
  - Agent: [方法论缺陷] 论文在5 Second-best Uniform Tariffs in a General, Calibrated Model部分报告了大量参数校准和弹性数值（如σ、θ、γ等），但未见...

- **Gold G004** ↔ Agent #2 (sim=0.282)
  - Gold: 标准 CES → 嵌套 CES 过渡缺乏显式符号映射。topt 公式从 θ₁/(θ₁-ρ₁) 变为 ω₁/[ω₁-(σ₁-1)/θ₁]，中间无桥接推导。理论与定量部分的参数定义（α_i, γ_i1, ...
  - Agent: [数学推导/符号一致性] 附录C和D的数学推导中符号极为复杂，部分符号（如λ、γ、θ、σ、ρ、T(t_i)、Λ_i1、˜γ等）在不同公式间有变体，部分定义仅在脚注或正文一处出现，存在符号定义不一致和跳...

### Missed Gold Findings (False Negatives)

- **G002** [high] Sections 2-4 所有理论推导基于标准 CES（ω=σ），Section 5 定量模型用嵌套 CES（ω=σ/1.25）。过渡仅两段话，未推导嵌套 CES 下 F(t*) 中 M(t) 和 R(t) 项的对应形式，未证明 Theor...
- **G003** [high] 定量模型对所有186国采用'one country at a time'单边最优关税计算，假设其他国家价格不变（小国假设）。脚注2仅提及大国文献但未做调整。对美国、中国等贸易大国，此假设的approximation quality未被讨论或...
- **G005** [medium] 公式(44) sector 2 的 Pareto shape parameter 写为 θ₁，应为 θ₂。公式(43) sector 1 用 θ₁ 正确；公式(45) free entry 条件用 θ₂。确认为排版错误。...
- **G006** [medium] ωs=σs/1.25 校准仅一句话：'Setting ωs = σs/1.25 best reproduces global trade growth between 1990 and 2010.' 无 fit quality 度量、无替代...
- **G007** [medium] Grid search 步长为'mostly 2.5%'——何处不同、如何处理非grid最优值、收敛判据等细节不足。对于数值方法论文，方法透明度应更高。...
- **G008** [medium] 论文声称文献空白（'As far as we are aware, the literature has not addressed...'），但 Costinot et al. (2020) 已讨论多部门非均匀关税。本文 novelty ...
- **G009** [medium] 小国假设（价格接受者）应用于所有186国包括美国、中国。脚注2和OPEC讨论虽有提及但未量化：terms-of-trade effects 对大国最优关税的修正幅度为多少？...
- **G010** [low] 双重边际化（double-marginalization）的定量重要性在 variable markup（非CES）模型下可能显著不同。论文将此作为范围界定（scope limitation）是合理的，但可以更明确讨论。...

### False Positives (Agent-only)

- [high] [数学推导/符号定义/一致性] 附录C和D中符号定义高度依赖跨节回溯，部分符号（如˜γ、Λ_i1、T(t_i)、λ、E_4、E_5等）在不同公式间切换，且定义分散于正文、脚注和多个附录，存在符号跳跃和表述不清的问题。部分符号（如˜γ、Λ_i...
- [medium] [文献综述/novelty overclaim] Introduction声称“the literature has not addressed the realistic case that we examine here: second...
- [high] [数学推导/符号一致性] 附录C和D的数学推导中符号极为复杂，部分符号（如λ、γ、θ、σ、ρ、T(t_i)、Λ_i1、˜γ等）在不同公式间有变体，部分定义仅在脚注或正文一处出现，存在符号定义不一致和跳跃的风险。最佳实践要求所有符号在主文和附...
- [high] [模型假设/合理性] 论文在4.2节和附录C/D中对最优关税的充分条件（如γ_i1、α_i、κ_i等）提出了复杂的正则性约束，但这些条件仅为充分条件，部分区域（如Figure 1白色区域）作者承认无法判定最优关税方向，说明模型假设未能覆盖全...
- [medium] [数据一致性/跨表格] 5节报告的最优关税数值（如制造业27.3%、矿业10.6%、农业16.0%、服务业10.6%）与Figure 2及表格数值一致，未发现明显数据矛盾。但部分描述（如“median optimal tariff is 1...

---

## P0 Fix Effectiveness Analysis

P0 修复目标瓶颈 vs 验证结果：

| 修复 | 目标 Gold IDs | 是否被新发现 | 备注 |
|------|--------------|-------------|------|
| AppendixMathAuditSkill | G001 (001), G005 (003) | 见上方匹配结果 | |
| ConsistencyValidator Rule9 | G005 (001) | 见上方匹配结果 | |
| PCG appendix weight | G001 (001), G005 (003) | 见上方匹配结果 | |
