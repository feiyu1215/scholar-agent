# Recall Verification Report (Post-P0 Fix)

**Generated**: 2026-05-28 02:51:57
**Model**: gpt-4.1
**Total Runtime**: 129.6s
**Papers**: 1

---

## Summary: Baseline vs Post-Fix

| Metric | Baseline | Post-Fix | Delta |
|--------|----------|----------|-------|
| Precision | 0.583 | 0.714 | +0.131 |
| Recall | 0.389 | 0.500 | +0.111 |
| F1 | 0.463 | 0.588 | +0.125 |

---

## paper_003: A Second-Best Argument for Low Optimal Tariffs

**Metrics**: P=0.714 R=0.500 F1=0.588
**Baseline**: P=0.571 R=0.444 F1=0.499
**Agent produced**: 7 findings | Gold: 10 | Matched: 5
**Runtime**: 119.8s | Turns: N/A

### Matched Findings

- **Gold G001** ↔ Agent #2 (sim=0.545)
  - Gold: 核心定量结论'中位最优关税10%'依赖多个校准参数（σ₁=4.4, θ₁=5.1, ω=σ/1.25等），全文无任何形式的参数敏感性分析。搜索'sensitivity'、'robustness'、'b...
  - Agent: [数据不一致/敏感性分析缺失] Section 5 的校准参数（如 σ_s, θ_s, γ_is）只报告了分位数和来源，但未展示参数变动对最优关税和福利结果的敏感性分析。表格和正文未报告±20-50%...

- **Gold G008** ↔ Agent #3 (sim=0.427)
  - Gold: 论文声称文献空白（'As far as we are aware, the literature has not addressed...'），但 Costinot et al. (2020) 已讨论...
  - Agent: [文献综述/novelty overclaim] Introduction 声称“as far as we are aware, then, the literature has not addres...

- **Gold G009** ↔ Agent #6 (sim=0.336)
  - Gold: 小国假设（价格接受者）应用于所有186国包括美国、中国。脚注2和OPEC讨论虽有提及但未量化：terms-of-trade effects 对大国最优关税的修正幅度为多少？...
  - Agent: [结果与模型验证] Section 5 的定量结果表明最优关税在不同国家和行业间差异较大，但对模型预测的外部有效性、与现实数据的拟合优度、以及主要机制（如terms of trade effect、r...

- **Gold G004** ↔ Agent #1 (sim=0.287)
  - Gold: 标准 CES → 嵌套 CES 过渡缺乏显式符号映射。topt 公式从 θ₁/(θ₁-ρ₁) 变为 ω₁/[ω₁-(σ₁-1)/θ₁]，中间无桥接推导。理论与定量部分的参数定义（α_i, γ_i1, ...
  - Agent: [方法论缺陷] 附录 C 和 D 的数学推导极为复杂，符号定义和变量关系高度嵌套，部分公式（如E4、E5、E3、T(t_i)、R(t_i)等）在不同处的定义和使用不够透明，容易造成符号混淆和逻辑跳跃，...

- **Gold G003** ↔ Agent #5 (sim=0.257)
  - Gold: 定量模型对所有186国采用'one country at a time'单边最优关税计算，假设其他国家价格不变（小国假设）。脚注2仅提及大国文献但未做调整。对美国、中国等贸易大国，此假设的approx...
  - Agent: [结论与结果支持度] 结论部分强调 Lerner symmetry 和一般均衡机制是本模型区别于 firm-delocation 文献的核心，但未量化报告这些机制对最优关税和福利结果的具体贡献，也未展...

### Missed Gold Findings (False Negatives)

- **G002** [high] Sections 2-4 所有理论推导基于标准 CES（ω=σ），Section 5 定量模型用嵌套 CES（ω=σ/1.25）。过渡仅两段话，未推导嵌套 CES 下 F(t*) 中 M(t) 和 R(t) 项的对应形式，未证明 Theor...
- **G005** [medium] 公式(44) sector 2 的 Pareto shape parameter 写为 θ₁，应为 θ₂。公式(43) sector 1 用 θ₁ 正确；公式(45) free entry 条件用 θ₂。确认为排版错误。...
- **G006** [medium] ωs=σs/1.25 校准仅一句话：'Setting ωs = σs/1.25 best reproduces global trade growth between 1990 and 2010.' 无 fit quality 度量、无替代...
- **G007** [medium] Grid search 步长为'mostly 2.5%'——何处不同、如何处理非grid最优值、收敛判据等细节不足。对于数值方法论文，方法透明度应更高。...
- **G010** [low] 双重边际化（double-marginalization）的定量重要性在 variable markup（非CES）模型下可能显著不同。论文将此作为范围界定（scope limitation）是合理的，但可以更明确讨论。...

### False Positives (Agent-only)

- [high] [文献综述/novelty overclaim] 外部文献搜索显示，已有文献（如Caliendo, Feenstra, Romalis et al. 2021/2023; Haaland and Venables 2016; Flam an...
- [medium] [引用核查] References 部分引用了关键文献（如 Caliendo et al. 2020, Haaland and Venables 2016, Flam and Helpman 1987, Lashkaripour and L...

---

## P0 Fix Effectiveness Analysis

P0 修复目标瓶颈 vs 验证结果：

| 修复 | 目标 Gold IDs | 是否被新发现 | 备注 |
|------|--------------|-------------|------|
| AppendixMathAuditSkill | G001 (001), G005 (003) | 见上方匹配结果 | |
| ConsistencyValidator Rule9 | G005 (001) | 见上方匹配结果 | |
| PCG appendix weight | G001 (001), G005 (003) | 见上方匹配结果 | |
