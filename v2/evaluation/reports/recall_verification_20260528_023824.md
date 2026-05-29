# Recall Verification Report (Post-P0 Fix)

**Generated**: 2026-05-28 02:38:24
**Model**: gpt-4.1
**Total Runtime**: 160.2s
**Papers**: 1

---

## Summary: Baseline vs Post-Fix

| Metric | Baseline | Post-Fix | Delta |
|--------|----------|----------|-------|
| Precision | 0.583 | 0.333 | -0.250 |
| Recall | 0.389 | 0.200 | -0.189 |
| F1 | 0.463 | 0.250 | -0.213 |

---

## paper_003: A Second-Best Argument for Low Optimal Tariffs

**Metrics**: P=0.333 R=0.200 F1=0.250
**Baseline**: P=0.571 R=0.444 F1=0.499
**Agent produced**: 6 findings | Gold: 10 | Matched: 2
**Runtime**: 153.6s | Turns: N/A

### Matched Findings

- **Gold G001** ↔ Agent #2 (sim=0.461)
  - Gold: 核心定量结论'中位最优关税10%'依赖多个校准参数（σ₁=4.4, θ₁=5.1, ω=σ/1.25等），全文无任何形式的参数敏感性分析。搜索'sensitivity'、'robustness'、'b...
  - Agent: [校准参数敏感性分析缺失] 定量部分（Section 5）未报告对关键参数（如 σ_s, θ_s, γ_is）的敏感性分析，未展示参数变动对最优关税和福利的影响。...

- **Gold G004** ↔ Agent #1 (sim=0.268)
  - Gold: 标准 CES → 嵌套 CES 过渡缺乏显式符号映射。topt 公式从 θ₁/(θ₁-ρ₁) 变为 ω₁/[ω₁-(σ₁-1)/θ₁]，中间无桥接推导。理论与定量部分的参数定义（α_i, γ_i1, ...
  - Agent: [数学推导复杂性高，符号定义和推导链条极长，部分变量如 E_4, E_5, l_i2, 等在主文和附录间跳跃，符号一致性和定义映射需进一步核查。当前未发现明显的推导错误，但部分中间步骤省略较多，读者难...

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

- [high] [模型假设合理性未充分讨论] 论文采用 small open economy 假设，但未系统讨论该假设对大国（如美国、中国）适用性的局限，尤其在定量模拟时未见敏感性或适用性分析。...
- [medium] [数据一致性初步通过] Section 5 Table 1 的参数（如 σ_s, θ_s, γ_is）与正文描述一致，未发现明显矛盾。后续需继续核查 welfare/optimal tariff 数值与摘要、结论、图表间的一致性。...
- [medium] [引用准确性待核查] 论文引用Melitz & Ottaviano 2008、Bagwell & Lee 2020等firm-delocation文献，但未提供详细文献列表，且部分引用年份/venue未明确。需进一步核查引用准确性及与论文核...
- [medium] [结论部分缺乏定量结果与政策含义的具体对应] 结论主要讨论模型与firm-delocation文献的理论差异，但未对Section 5定量结果（如最优关税、welfare变化）做具体政策含义或实际应用范围的分析。...

---

## P0 Fix Effectiveness Analysis

P0 修复目标瓶颈 vs 验证结果：

| 修复 | 目标 Gold IDs | 是否被新发现 | 备注 |
|------|--------------|-------------|------|
| AppendixMathAuditSkill | G001 (001), G005 (003) | 见上方匹配结果 | |
| ConsistencyValidator Rule9 | G005 (001) | 见上方匹配结果 | |
| PCG appendix weight | G001 (001), G005 (003) | 见上方匹配结果 | |
