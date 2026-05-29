# Recall Verification Report (Post-P0 Fix)

**Generated**: 2026-05-28 10:59:44
**Model**: gpt-4.1
**Total Runtime**: 131.5s
**Papers**: 1

---

## Summary: Baseline vs Post-Fix

| Metric | Baseline | Post-Fix | Delta |
|--------|----------|----------|-------|
| Precision | 0.583 | 0.444 | -0.139 |
| Recall | 0.389 | 0.444 | +0.055 |
| F1 | 0.463 | 0.444 | -0.019 |

---

## paper_001: Environmental Externalities and Free-riding in the Household

**Metrics**: P=0.444 R=0.444 F1=0.444
**Baseline**: P=0.600 R=0.333 F1=0.426
**Agent produced**: 9 findings | Gold: 9 | Matched: 4
**Runtime**: 127.0s | Turns: N/A

### Matched Findings

- **Gold G004** ↔ Agent #8 (sim=0.451)
  - Gold: Dictator game 作为 intrahousehold efficiency 的代理变量，全文无直接 construct validity 验证（DG sharing → 实际用水保护行为的因...
  - Agent: [异质性结果解释边界不清] 论文将dictator game度量的intrahousehold efficiency与价格弹性异质性直接关联，但未充分讨论该度量的外部效度及其与实际家庭决策的对应关系，...

- **Gold G002** ↔ Agent #9 (sim=0.403)
  - Gold: θ=1 归一化假设（设定高效率组无扭曲）无敏感性分析。论文承认 'underestimating the average distortion' 但未量化：若 θ_high=0.8，政策数字（最优价格...
  - Agent: [政策含义解释不充分] 论文在最优价格校准和政策含义部分，关于关键假设（如高intrahousehold efficiency组无扭曲、elasticity无相关性）对最优税率和福利结论的影响讨论不足...

- **Gold G003** ↔ Agent #3 (sim=0.364)
  - Gold: 弹性 -0.27 的三个转换假设（风险中性、离散≈连续、概率≈确定性）已在脚注列出，但代入 Table 11 公式(6)做政策校准时，假设偏离对 θ=0.23 的影响未被量化。缺敏感性分析而非缺透明度...
  - Agent: [参数敏感性分析缺失] 校准过程未报告关键参数变动对最优价格、福利等核心结论的影响，缺乏敏感性分析。...

- **Gold G006** ↔ Agent #4 (sim=0.274)
  - Gold: 部分 heterogeneity 结果对 information + incentive treatment pooling 敏感。Appendix Table A.11 显示异质性部分与 infor...
  - Agent: [混淆变量控制不足] 主要异质性分析（如dictator game度量的intrahousehold efficiency）与多项家庭特征（如财富、家庭规模、雇佣女佣等）显著相关，但主文未充分控制这些...

### Missed Gold Findings (False Negatives)

- **G001** [high] Result 2 推导对 γ_i 求导正确，但结论行误写为 ∂²w*_i/∂p∂α_i < 0（应为 γ_i）。同附录 semi-elasticity 部分正确写为 γ_i，确认是数学符号笔误。...
- **G005** [medium] Table A.3（Information treatment balance）与 Table A.4（Credibility treatment balance）数据完全重复——所有均值、SD、p值完全一致。两个处理的分组方式不同（1/4...
- **G007** [medium] 13 个 survey measures（Table 6-8）、多重 heterogeneity 检验无多重检验校正（Bonferroni/FDR）。虽然预注册可部分缓解，但论文未明确区分预注册 vs 探索性分析。...
- **G008** [medium] 理论模型为两人家庭（bill-payer + spouse），但实际样本家庭平均约6人。模型推导的 θ（效率参数）在多人决策环境中的解释和适用性未被讨论。...
- **G009** [low] DID 平行趋势仅通过图形视觉检验，无正式 event-study 回归或前置期系数检验。但注意：这是 RCT 设计（AEARCTR-0000660），平行趋势由随机化在期望意义上保证，event-study 检验为 nice-to-hav...

### False Positives (Agent-only)

- [high] [方法论缺陷] 理论模型假设家庭成员水使用不可观察，未充分讨论该假设在目标经济体（如大国或不同文化背景）下的合理性与外推性。...
- [high] [数据处理透明度不足] 样本筛选和构建过程存在多重筛选标准，但未报告每一步筛选后剩余样本数，且未充分说明筛选标准对最终样本代表性的影响。...
- [medium] [引用不准确/遗漏] 论文声称首次将环境外部性与家庭决策文献结合，未充分讨论Rungie et al. (2014)等已涉及家庭内水使用异质性的文献，可能存在prior work遗漏或引用不充分。...
- [medium] [引用不准确/遗漏] Rungie et al. (2014)已实质性讨论家庭成员对水使用的异质性及其对家庭层面需求估计的影响，本文声称首次结合环境外部性与家庭决策文献存在夸大。...
- [medium] [结果解释不充分] 论文报告短期价格弹性为-0.27，略低于Dalhuisen et al. (2003)综述均值，但未充分讨论弹性低于均值的原因及其对政策含义的影响。...

---

## P0 Fix Effectiveness Analysis

P0 修复目标瓶颈 vs 验证结果：

| 修复 | 目标 Gold IDs | 是否被新发现 | 备注 |
|------|--------------|-------------|------|
| AppendixMathAuditSkill | G001 (001), G005 (003) | 见上方匹配结果 | |
| ConsistencyValidator Rule9 | G005 (001) | 见上方匹配结果 | |
| PCG appendix weight | G001 (001), G005 (003) | 见上方匹配结果 | |
