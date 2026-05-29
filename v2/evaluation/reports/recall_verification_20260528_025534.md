# Recall Verification Report (Post-P0 Fix)

**Generated**: 2026-05-28 02:55:34
**Model**: gpt-4.1
**Total Runtime**: 169.9s
**Papers**: 1

---

## Summary: Baseline vs Post-Fix

| Metric | Baseline | Post-Fix | Delta |
|--------|----------|----------|-------|
| Precision | 0.583 | 1.000 | +0.417 |
| Recall | 0.389 | 0.444 | +0.055 |
| F1 | 0.463 | 0.615 | +0.152 |

---

## paper_001: Environmental Externalities and Free-riding in the Household

**Metrics**: P=1.000 R=0.444 F1=0.615
**Baseline**: P=0.600 R=0.333 F1=0.426
**Agent produced**: 4 findings | Gold: 9 | Matched: 4
**Runtime**: 164.4s | Turns: N/A

### Matched Findings

- **Gold G001** ↔ Agent #1 (sim=0.454)
  - Gold: Result 2 推导对 γ_i 求导正确，但结论行误写为 ∂²w*_i/∂p∂α_i < 0（应为 γ_i）。同附录 semi-elasticity 部分正确写为 γ_i，确认是数学符号笔误。...
  - Agent: [数学推导] 附录模型推导部分存在符号混乱和部分公式未明示变量定义，可能导致读者难以复现或理解推导。部分符号如 λ_i, α_i, w̄ 在不同公式中未明确其取值范围和经济含义，且部分推导仅给出符号方...

- **Gold G003** ↔ Agent #3 (sim=0.354)
  - Gold: 弹性 -0.27 的三个转换假设（风险中性、离散≈连续、概率≈确定性）已在脚注列出，但代入 Table 11 公式(6)做政策校准时，假设偏离对 θ=0.23 的影响未被量化。缺敏感性分析而非缺透明度...
  - Agent: [校准参数敏感性分析缺失] 校准部分仅报告了基于 Table 11 参数的最优价格计算结果，但未见对关键参数（如 E(θ), Var(θ), p, χ）变动±20-50%时对最优价格和福利结论的敏感性...

- **Gold G006** ↔ Agent #2 (sim=0.258)
  - Gold: 部分 heterogeneity 结果对 information + incentive treatment pooling 敏感。Appendix Table A.11 显示异质性部分与 infor...
  - Agent: [数据一致性] 部分表格（如 Table A.8, Table A.9, Table A.10）中关于激励处理效应和与 Dictator Game 指标的交互项的系数，在不同表格和不同样本/规格下的数...

- **Gold G002** ↔ Agent #4 (sim=0.253)
  - Gold: θ=1 归一化假设（设定高效率组无扭曲）无敏感性分析。论文承认 'underestimating the average distortion' 但未量化：若 θ_high=0.8，政策数字（最优价格...
  - Agent: [模型假设合理性] 2.4节对模型假设进行了讨论，但对关键假设（如家庭成员之间信息对称、效用函数线性、价格弹性均匀等）在现实中的适用性和局限性讨论不充分，尤其是在存在信息不对称、行为偏差或外部约束（如...

### Missed Gold Findings (False Negatives)

- **G004** [medium] Dictator game 作为 intrahousehold efficiency 的代理变量，全文无直接 construct validity 验证（DG sharing → 实际用水保护行为的因果路径）。间接证据（Table 2 相关...
- **G005** [medium] Table A.3（Information treatment balance）与 Table A.4（Credibility treatment balance）数据完全重复——所有均值、SD、p值完全一致。两个处理的分组方式不同（1/4...
- **G007** [medium] 13 个 survey measures（Table 6-8）、多重 heterogeneity 检验无多重检验校正（Bonferroni/FDR）。虽然预注册可部分缓解，但论文未明确区分预注册 vs 探索性分析。...
- **G008** [medium] 理论模型为两人家庭（bill-payer + spouse），但实际样本家庭平均约6人。模型推导的 θ（效率参数）在多人决策环境中的解释和适用性未被讨论。...
- **G009** [low] DID 平行趋势仅通过图形视觉检验，无正式 event-study 回归或前置期系数检验。但注意：这是 RCT 设计（AEARCTR-0000660），平行趋势由随机化在期望意义上保证，event-study 检验为 nice-to-hav...

---

## P0 Fix Effectiveness Analysis

P0 修复目标瓶颈 vs 验证结果：

| 修复 | 目标 Gold IDs | 是否被新发现 | 备注 |
|------|--------------|-------------|------|
| AppendixMathAuditSkill | G001 (001), G005 (003) | 见上方匹配结果 | |
| ConsistencyValidator Rule9 | G005 (001) | 见上方匹配结果 | |
| PCG appendix weight | G001 (001), G005 (003) | 见上方匹配结果 | |
