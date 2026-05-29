# Recall Verification Report (Post-P0 Fix)

**Generated**: 2026-05-28 01:36:12
**Model**: gpt-4.1
**Total Runtime**: 126.3s
**Papers**: 1

---

## Summary: Baseline vs Post-Fix

| Metric | Baseline | Post-Fix | Delta |
|--------|----------|----------|-------|
| Precision | 0.583 | 0.500 | -0.083 |
| Recall | 0.389 | 0.444 | +0.055 |
| F1 | 0.463 | 0.471 | +0.008 |

---

## paper_001: Environmental Externalities and Free-riding in the Household

**Metrics**: P=0.500 R=0.444 F1=0.471
**Baseline**: P=0.600 R=0.333 F1=0.426
**Agent produced**: 8 findings | Gold: 9 | Matched: 4
**Runtime**: 120.2s | Turns: N/A

### Matched Findings

- **Gold G003** ↔ Agent #4 (sim=0.518)
  - Gold: 弹性 -0.27 的三个转换假设（风险中性、离散≈连续、概率≈确定性）已在脚注列出，但代入 Table 11 公式(6)做政策校准时，假设偏离对 θ=0.23 的影响未被量化。缺敏感性分析而非缺透明度...
  - Agent: [校准参数敏感性] 校准部分（calibration）仅用主文Table 11参数代入公式，未报告对关键参数（如价格弹性、家庭规模、效用权重等）的敏感性分析。缺乏敏感性分析会影响政策建议的稳健性。...

- **Gold G001** ↔ Agent #1 (sim=0.362)
  - Gold: Result 2 推导对 γ_i 求导正确，但结论行误写为 ∂²w*_i/∂p∂α_i < 0（应为 γ_i）。同附录 semi-elasticity 部分正确写为 γ_i，确认是数学符号笔误。...
  - Agent: [数学推导] 附录模型推导存在符号和变量定义不清的问题。部分一阶条件的符号与主文公式不一致，且部分变量（如 λ_i, ↵_i, p, ¯w）未明确说明其取值范围和经济含义，导致后续结果的解释不够严谨。...

- **Gold G002** ↔ Agent #7 (sim=0.336)
  - Gold: θ=1 归一化假设（设定高效率组无扭曲）无敏感性分析。论文承认 'underestimating the average distortion' 但未量化：若 θ_high=0.8，政策数字（最优价格...
  - Agent: [政策建议合理性] 论文在政策校准时假设高intrahousehold效率组的价格弹性等于无扭曲弹性（即θ=1），并假定无扭曲弹性与intrahousehold效率无关。这一假设缺乏外部证据支持，可能...

- **Gold G006** ↔ Agent #6 (sim=0.298)
  - Gold: 部分 heterogeneity 结果对 information + incentive treatment pooling 敏感。Appendix Table A.11 显示异质性部分与 infor...
  - Agent: [实证稳健性] 论文对核心结论进行了多维度稳健性检验，包括不同效应异质性来源、回归规格、样本选择、面板长度、效应度量方式等。大部分主效应在不同规格下稳健，但部分异质性结果（如dictator game...

### Missed Gold Findings (False Negatives)

- **G004** [medium] Dictator game 作为 intrahousehold efficiency 的代理变量，全文无直接 construct validity 验证（DG sharing → 实际用水保护行为的因果路径）。间接证据（Table 2 相关...
- **G005** [medium] Table A.3（Information treatment balance）与 Table A.4（Credibility treatment balance）数据完全重复——所有均值、SD、p值完全一致。两个处理的分组方式不同（1/4...
- **G007** [medium] 13 个 survey measures（Table 6-8）、多重 heterogeneity 检验无多重检验校正（Bonferroni/FDR）。虽然预注册可部分缓解，但论文未明确区分预注册 vs 探索性分析。...
- **G008** [medium] 理论模型为两人家庭（bill-payer + spouse），但实际样本家庭平均约6人。模型推导的 θ（效率参数）在多人决策环境中的解释和适用性未被讨论。...
- **G009** [low] DID 平行趋势仅通过图形视觉检验，无正式 event-study 回归或前置期系数检验。但注意：这是 RCT 设计（AEARCTR-0000660），平行趋势由随机化在期望意义上保证，event-study 检验为 nice-to-hav...

### False Positives (Agent-only)

- [high] [模型假设] 关键假设“水消费不可观测”在现实中可能不成立，且未充分讨论部分可观测情形对模型结论的影响。主文2.4节虽讨论了可观测性提升（如智能水表）会提升价格敏感性，但模型未显式纳入部分观测或惩罚机制，导致外推性受限。...
- [high] [数据一致性] 部分表格（如Table A.8, A.9, A.11）中处理组效应与主文报告的效应量存在差异，且标准误标注方式不统一，可能导致读者误解效应显著性。需进一步核查主文与附录表格的数值是否一致。...
- [high] [数据一致性] 主文4.3节报告激励处理效应为6.2-6.7%月用水减少（Table 3），而附录表A.8、A.9、A.11报告效应分别为-1.025、-0.067、-0.022（单位不同，标准误标注方式不一）。主文与附录表格效应量未统一单...
- [high] [数据一致性] 主文4.3节报告激励处理效应为6.2-6.7%月用水减少（Table 3），而附录表A.8、A.9、A.11报告效应分别为-1.025、-0.067、-0.022（单位不同，标准误标注方式不一）。主文与附录表格效应量未统一单...

---

## P0 Fix Effectiveness Analysis

P0 修复目标瓶颈 vs 验证结果：

| 修复 | 目标 Gold IDs | 是否被新发现 | 备注 |
|------|--------------|-------------|------|
| AppendixMathAuditSkill | G001 (001), G005 (003) | 见上方匹配结果 | |
| ConsistencyValidator Rule9 | G005 (001) | 见上方匹配结果 | |
| PCG appendix weight | G001 (001), G005 (003) | 见上方匹配结果 | |
