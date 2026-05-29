# Recall Verification Report (Post-P0 Fix)

**Generated**: 2026-05-28 01:39:35
**Model**: gpt-4.1
**Total Runtime**: 144.4s
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
**Runtime**: 137.1s | Turns: N/A

### Matched Findings

- **Gold G001** ↔ Agent #3 (sim=0.506)
  - Gold: 核心定量结论'中位最优关税10%'依赖多个校准参数（σ₁=4.4, θ₁=5.1, ω=σ/1.25等），全文无任何形式的参数敏感性分析。搜索'sensitivity'、'robustness'、'b...
  - Agent: [方法论缺陷/敏感性分析不足] 对校准参数（如 σ_s, θ_s, γ_is, ω_s）仅给出单一设定和有限的参数来源说明，缺乏系统的敏感性分析。模型结果（如最优关税）高度依赖这些弹性和分配参数，但未...

- **Gold G004** ↔ Agent #4 (sim=0.334)
  - Gold: 标准 CES → 嵌套 CES 过渡缺乏显式符号映射。topt 公式从 θ₁/(θ₁-ρ₁) 变为 ω₁/[ω₁-(σ₁-1)/θ₁]，中间无桥接推导。理论与定量部分的参数定义（α_i, γ_i1, ...
  - Agent: [模型假设合理性缺陷] 主模型假设 small open economy、CES偏好、无消费/生产税、唯一政策工具为进口关税，且部分推导假设 θ_s/(σ_s−1)=1.5。实际经济体往往存在多重政策...

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

- [high] [方法论缺陷] 附录数学推导极度复杂，符号定义和主文、附录间频繁切换，部分符号（如 λ, θ, γ, T, R, D, M, E4, E5 等）在不同公式间含义变化，存在潜在符号混淆和跳跃推理风险，且部分关键步骤未详细展开，难以逐步验证每一...
- [high] [数据不一致] Table 1 中 Manufacturing 的 γ_is (median) 为 0.28，˜σ_is (median) 为 1.96，但文本描述“median effective elasticity in Manufa...
- [high] [方法论缺陷] 附录 Lemma 7-8 及相关推导对单调性和边界条件的论证存在跳跃，部分符号（如 λ, θ, γ, T, E4, E5）在不同公式间定义切换，未对所有参数区间给出严格证明。例如 Lemma 7 仅通过符号分析断言 T(t_...
- [medium] [引用遗漏/写作问题] 结论部分讨论 firm-delocation 文献（Melitz and Ottaviano, 2008; Bagwell and Lee, 2020; Ossa, 2011; Bagwell and Staiger...

---

## P0 Fix Effectiveness Analysis

P0 修复目标瓶颈 vs 验证结果：

| 修复 | 目标 Gold IDs | 是否被新发现 | 备注 |
|------|--------------|-------------|------|
| AppendixMathAuditSkill | G001 (001), G005 (003) | 见上方匹配结果 | |
| ConsistencyValidator Rule9 | G005 (001) | 见上方匹配结果 | |
| PCG appendix weight | G001 (001), G005 (003) | 见上方匹配结果 | |
