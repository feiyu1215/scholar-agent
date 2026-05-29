# Recall Verification Report (Post-P0 Fix)

**Generated**: 2026-05-28 11:13:49
**Model**: gpt-4.1
**Total Runtime**: 158.0s
**Papers**: 1

---

## Summary: Baseline vs Post-Fix

| Metric | Baseline | Post-Fix | Delta |
|--------|----------|----------|-------|
| Precision | 0.583 | 0.857 | +0.274 |
| Recall | 0.389 | 0.600 | +0.211 |
| F1 | 0.463 | 0.706 | +0.243 |

---

## paper_003: A Second-Best Argument for Low Optimal Tariffs

**Metrics**: P=0.857 R=0.600 F1=0.706
**Baseline**: P=0.571 R=0.444 F1=0.499
**Agent produced**: 7 findings | Gold: 10 | Matched: 6
**Runtime**: 152.8s | Turns: N/A

### Matched Findings

- **Gold G004** ↔ Agent #5 (sim=0.509)
  - Gold: 标准 CES → 嵌套 CES 过渡缺乏显式符号映射。topt 公式从 θ₁/(θ₁-ρ₁) 变为 ω₁/[ω₁-(σ₁-1)/θ₁]，中间无桥接推导。理论与定量部分的参数定义（α_i, γ_i1, ...
  - Agent: [符号一致性缺陷] 定量模型与理论模型符号定义不统一，部分参数（如σ_s, θ_s, ω_s, γ_is等）跨section定义有混乱，附录公式与正文符号映射不明确。...

- **Gold G002** ↔ Agent #2 (sim=0.424)
  - Gold: Sections 2-4 所有理论推导基于标准 CES（ω=σ），Section 5 定量模型用嵌套 CES（ω=σ/1.25）。过渡仅两段话，未推导嵌套 CES 下 F(t*) 中 M(t) 和 R...
  - Agent: [理论与定量模型桥接缺陷] 定量模型参数与理论模型符号映射不透明，公式推导缺乏结构性桥接，导致定量结果与理论基础脱节风险。第5节定量校准采用多参数（σ_s, θ_s, ω_s等），但与第2节理论模型的...

- **Gold G007** ↔ Agent #4 (sim=0.386)
  - Gold: Grid search 步长为'mostly 2.5%'——何处不同、如何处理非grid最优值、收敛判据等细节不足。对于数值方法论文，方法透明度应更高。...
  - Agent: [数值求解透明度缺陷] 数值求解方法细节不透明，grid精度、收敛判据、非网格点处理等未详细披露，复现性存疑。第5节仅提及grid search和参数区间，未报告具体步长、收敛标准、非网格点最优值处理...

- **Gold G006** ↔ Agent #3 (sim=0.376)
  - Gold: ωs=σs/1.25 校准仅一句话：'Setting ωs = σs/1.25 best reproduces global trade growth between 1990 and 2010.' ...
  - Agent: [校准目标与参数敏感性缺陷] 校准目标选择与过程解释过于简略，缺乏fit quality度量或替代值比较，参数敏感性分析不足。第5节仅简要说明参数取值和来源，但未报告fit quality、未展示参数...

- **Gold G003** ↔ Agent #1 (sim=0.297)
  - Gold: 定量模型对所有186国采用'one country at a time'单边最优关税计算，假设其他国家价格不变（小国假设）。脚注2仅提及大国文献但未做调整。对美国、中国等贸易大国，此假设的approx...
  - Agent: [方法论缺陷] Small open economy假设未针对大国适用性进行充分讨论，模型外推性存疑。论文主要采用small open economy设定，但未对美国、中国等大国经济体是否适用进行系统...

- **Gold G001** ↔ Agent #6 (sim=0.259)
  - Gold: 核心定量结论'中位最优关税10%'依赖多个校准参数（σ₁=4.4, θ₁=5.1, ω=σ/1.25等），全文无任何形式的参数敏感性分析。搜索'sensitivity'、'robustness'、'b...
  - Agent: [理论与定量模型桥接缺陷] 附录C推导中的符号（如λ、γ、θ、σ等）与正文第5节定量模型参数映射不明确，导致公式与定量校准间桥接缺失。附录C大量符号未与定量模型参数表（Table 1）直接对应，部分推...

### Missed Gold Findings (False Negatives)

- **G005** [medium] 公式(44) sector 2 的 Pareto shape parameter 写为 θ₁，应为 θ₂。公式(43) sector 1 用 θ₁ 正确；公式(45) free entry 条件用 θ₂。确认为排版错误。...
- **G008** [medium] 论文声称文献空白（'As far as we are aware, the literature has not addressed...'），但 Costinot et al. (2020) 已讨论多部门非均匀关税。本文 novelty ...
- **G009** [medium] 小国假设（价格接受者）应用于所有186国包括美国、中国。脚注2和OPEC讨论虽有提及但未量化：terms-of-trade effects 对大国最优关税的修正幅度为多少？...
- **G010** [low] 双重边际化（double-marginalization）的定量重要性在 variable markup（非CES）模型下可能显著不同。论文将此作为范围界定（scope limitation）是合理的，但可以更明确讨论。...

### False Positives (Agent-only)

- [medium] [逻辑链条缺陷] 论文定量结果与理论模型的逻辑过渡存在跳跃，部分推导步骤未明示，导致结论的稳健性和解释力不足。第5节定量结果与第2节理论模型的映射缺乏详细说明，附录C推导未与定量模型参数直接对应。...

---

## P0 Fix Effectiveness Analysis

P0 修复目标瓶颈 vs 验证结果：

| 修复 | 目标 Gold IDs | 是否被新发现 | 备注 |
|------|--------------|-------------|------|
| AppendixMathAuditSkill | G001 (001), G005 (003) | 见上方匹配结果 | |
| ConsistencyValidator Rule9 | G005 (001) | 见上方匹配结果 | |
| PCG appendix weight | G001 (001), G005 (003) | 见上方匹配结果 | |
