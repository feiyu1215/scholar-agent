# Recall Verification Report (Post-P0 Fix)

**Generated**: 2026-05-28 00:24:01
**Model**: gpt-4.1
**Total Runtime**: 125.0s
**Papers**: 1

---

## Summary: Baseline vs Post-Fix

| Metric | Baseline | Post-Fix | Delta |
|--------|----------|----------|-------|
| Precision | 0.583 | 0.500 | -0.083 |
| Recall | 0.389 | 0.333 | -0.056 |
| F1 | 0.463 | 0.400 | -0.063 |

---

## paper_001: Environmental Externalities and Free-riding in the Household

**Metrics**: P=0.500 R=0.333 F1=0.400
**Baseline**: P=0.600 R=0.333 F1=0.426
**Agent produced**: 6 findings | Gold: 9 | Matched: 3
**Runtime**: 119.1s | Turns: N/A

### Matched Findings

- **Gold G001** ↔ Agent #1 (sim=0.455)
  - Gold: Result 2 推导对 γ_i 求导正确，但结论行误写为 ∂²w*_i/∂p∂α_i < 0（应为 γ_i）。同附录 semi-elasticity 部分正确写为 γ_i，确认是数学符号笔误。...
  - Agent: [数学推导] 附录模型推导符号和结论未给出详细中间步骤，部分符号定义不清，且部分推导仅以“负号”符号判断方向，缺乏严格性。例如 Result 1-3 仅通过符号判断 cross-partial 的符号...

- **Gold G003** ↔ Agent #3 (sim=0.353)
  - Gold: 弹性 -0.27 的三个转换假设（风险中性、离散≈连续、概率≈确定性）已在脚注列出，但代入 Table 11 公式(6)做政策校准时，假设偏离对 θ=0.23 的影响未被量化。缺敏感性分析而非缺透明度...
  - Agent: [校准参数敏感性分析缺失] 校准部分仅报告了两组参数（homogenous/heterogeneous），未对关键参数（如 E(θ), Var(θ), p, χ）进行敏感性分析，也未展示不同参数取值对...

- **Gold G006** ↔ Agent #2 (sim=0.259)
  - Gold: 部分 heterogeneity 结果对 information + incentive treatment pooling 敏感。Appendix Table A.11 显示异质性部分与 infor...
  - Agent: [数据一致性] 部分表格（如 Table A.8, A.9, A.11）中的效应量与主文中描述的效应量存在差异，且标准误和显著性标记不一致。例如 Table A.9 中 Incentive treat...

### Missed Gold Findings (False Negatives)

- **G002** [high] θ=1 归一化假设（设定高效率组无扭曲）无敏感性分析。论文承认 'underestimating the average distortion' 但未量化：若 θ_high=0.8，政策数字（最优价格高出64%等）如何变化。额外未检验假设：...
- **G004** [medium] Dictator game 作为 intrahousehold efficiency 的代理变量，全文无直接 construct validity 验证（DG sharing → 实际用水保护行为的因果路径）。间接证据（Table 2 相关...
- **G005** [medium] Table A.3（Information treatment balance）与 Table A.4（Credibility treatment balance）数据完全重复——所有均值、SD、p值完全一致。两个处理的分组方式不同（1/4...
- **G007** [medium] 13 个 survey measures（Table 6-8）、多重 heterogeneity 检验无多重检验校正（Bonferroni/FDR）。虽然预注册可部分缓解，但论文未明确区分预注册 vs 探索性分析。...
- **G008** [medium] 理论模型为两人家庭（bill-payer + spouse），但实际样本家庭平均约6人。模型推导的 θ（效率参数）在多人决策环境中的解释和适用性未被讨论。...
- **G009** [low] DID 平行趋势仅通过图形视觉检验，无正式 event-study 回归或前置期系数检验。但注意：这是 RCT 设计（AEARCTR-0000660），平行趋势由随机化在期望意义上保证，event-study 检验为 nice-to-hav...

### False Positives (Agent-only)

- [medium] [模型假设合理性] 模型假设水的个体消费不可观测，且家庭内部无法执行契约，但现实中部分水消费（如洗澡、洗衣）可能可被部分观测，且部分家庭存在一定程度的监督和惩罚机制。模型未讨论观测程度变化对结论的影响，可能高估了内生摩擦。...
- [medium] [方法论缺陷] 主文与附录表格的效应量计算方法未详细披露，主文仅报告回归系数及弹性，附录表格未说明是否采用相同回归模型、变量定义和样本筛选。缺乏透明度，影响结果可复现性。...
- [medium] [结论外推风险] 结论部分将实验结果外推至发展中国家和其他家庭成员（如儿童），但主文样本仅限于特定地区夫妻家庭，且未提供对不同文化、经济背景的异质性检验，存在外部有效性不足和过度外推风险。...

---

## P0 Fix Effectiveness Analysis

P0 修复目标瓶颈 vs 验证结果：

| 修复 | 目标 Gold IDs | 是否被新发现 | 备注 |
|------|--------------|-------------|------|
| AppendixMathAuditSkill | G001 (001), G005 (003) | 见上方匹配结果 | |
| ConsistencyValidator Rule9 | G005 (001) | 见上方匹配结果 | |
| PCG appendix weight | G001 (001), G005 (003) | 见上方匹配结果 | |
