# Recall Verification Report (Post-P0 Fix)

**Generated**: 2026-05-28 02:16:48
**Model**: gpt-4.1
**Total Runtime**: 201.0s
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
**Runtime**: 193.2s | Turns: N/A

### Matched Findings

- **Gold G001** ↔ Agent #1 (sim=0.556)
  - Gold: 核心定量结论'中位最优关税10%'依赖多个校准参数（σ₁=4.4, θ₁=5.1, ω=σ/1.25等），全文无任何形式的参数敏感性分析。搜索'sensitivity'、'robustness'、'b...
  - Agent: [方法论缺陷] 论文未对校准参数（如σ、θ、γ等）进行敏感性分析，未报告主要定量结论对参数变动的稳健性。...

- **Gold G004** ↔ Agent #2 (sim=0.256)
  - Gold: 标准 CES → 嵌套 CES 过渡缺乏显式符号映射。topt 公式从 θ₁/(θ₁-ρ₁) 变为 ω₁/[ω₁-(σ₁-1)/θ₁]，中间无桥接推导。理论与定量部分的参数定义（α_i, γ_i1, ...
  - Agent: [数学推导透明度不足] 附录C和D的推导链条复杂，部分关键等式（如E4、E5、T(t_i)等）未给出详细中间步骤，符号定义和边界条件不够清晰，影响复现性。...

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

- [high] [模型假设合理性待验证] 论文采用nested CES、sectoral heterogeneity、roundabout production等假设，但未充分讨论这些假设在目标经济体（如油气出口国、服务主导国）下的适用性及局限。...
- [high] [数据一致性待核查] Section 5报告的最优关税数值（如制造业27.3%、矿业10.6%、全局中位数10%）需与图表、附录及摘要数据交叉核查，确认无矛盾。...
- [high] [数据一致性已验证] Section 5报告的最优关税数值（制造业27.3%、矿业10.6%、全局中位数10%）与正文、表格和图2一致，未发现矛盾。...
- [medium] [模型扩展动机不充分] 论文从一部门模型扩展到两部门、再到多部门嵌套CES结构，但未明确说明每一步扩展带来的新经济机制或定量结论的变化，缺乏对比分析，影响模型选择的正当性。...
- [medium] [政策含义解释不足] 论文在结论部分对模型结果的实际经济意义和政策建议的适用性讨论较为简略，未充分分析模型假设与现实政策环境的契合度及局限性。...

---

## P0 Fix Effectiveness Analysis

P0 修复目标瓶颈 vs 验证结果：

| 修复 | 目标 Gold IDs | 是否被新发现 | 备注 |
|------|--------------|-------------|------|
| AppendixMathAuditSkill | G001 (001), G005 (003) | 见上方匹配结果 | |
| ConsistencyValidator Rule9 | G005 (001) | 见上方匹配结果 | |
| PCG appendix weight | G001 (001), G005 (003) | 见上方匹配结果 | |
