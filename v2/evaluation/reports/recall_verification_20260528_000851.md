# Recall Verification Report (Post-P0 Fix)

**Generated**: 2026-05-28 00:08:51
**Model**: gpt-4.1
**Total Runtime**: 166.6s
**Papers**: 1

---

## Summary: Baseline vs Post-Fix

| Metric | Baseline | Post-Fix | Delta |
|--------|----------|----------|-------|
| Precision | 0.583 | 0.000 | -0.583 |
| Recall | 0.389 | 0.000 | -0.389 |
| F1 | 0.463 | 0.000 | -0.463 |

---

## paper_001: Environmental Externalities and Free-riding in the Household

**Metrics**: P=0.000 R=0.000 F1=0.000
**Baseline**: P=0.600 R=0.333 F1=0.426
**Agent produced**: 8 findings | Gold: 9 | Matched: 0
**Runtime**: 157.3s | Turns: N/A

### Missed Gold Findings (False Negatives)

- **G001** [high] Result 2 推导对 γ_i 求导正确，但结论行误写为 ∂²w*_i/∂p∂α_i < 0（应为 γ_i）。同附录 semi-elasticity 部分正确写为 γ_i，确认是数学符号笔误。...
- **G002** [high] θ=1 归一化假设（设定高效率组无扭曲）无敏感性分析。论文承认 'underestimating the average distortion' 但未量化：若 θ_high=0.8，政策数字（最优价格高出64%等）如何变化。额外未检验假设：...
- **G003** [high] 弹性 -0.27 的三个转换假设（风险中性、离散≈连续、概率≈确定性）已在脚注列出，但代入 Table 11 公式(6)做政策校准时，假设偏离对 θ=0.23 的影响未被量化。缺敏感性分析而非缺透明度。...
- **G004** [medium] Dictator game 作为 intrahousehold efficiency 的代理变量，全文无直接 construct validity 验证（DG sharing → 实际用水保护行为的因果路径）。间接证据（Table 2 相关...
- **G005** [medium] Table A.3（Information treatment balance）与 Table A.4（Credibility treatment balance）数据完全重复——所有均值、SD、p值完全一致。两个处理的分组方式不同（1/4...
- **G006** [medium] 部分 heterogeneity 结果对 information + incentive treatment pooling 敏感。Appendix Table A.11 显示异质性部分与 information treatment 相关。...
- **G007** [medium] 13 个 survey measures（Table 6-8）、多重 heterogeneity 检验无多重检验校正（Bonferroni/FDR）。虽然预注册可部分缓解，但论文未明确区分预注册 vs 探索性分析。...
- **G008** [medium] 理论模型为两人家庭（bill-payer + spouse），但实际样本家庭平均约6人。模型推导的 θ（效率参数）在多人决策环境中的解释和适用性未被讨论。...
- **G009** [low] DID 平行趋势仅通过图形视觉检验，无正式 event-study 回归或前置期系数检验。但注意：这是 RCT 设计（AEARCTR-0000660），平行趋势由随机化在期望意义上保证，event-study 检验为 nice-to-hav...

### False Positives (Agent-only)

- [high] [数学推导] 附录模型推导符号混乱、部分推导步骤跳跃，变量定义不清，且部分一阶条件未详细展开，难以复现。...
- [high] [模型假设] 对“水的个体消费不可观测”假设的合理性缺乏实证支持，且未充分讨论该假设对模型外推性的影响。...
- [high] [数据一致性] 部分表格（如Table A.8, A.9, A.11）中的样本量（Observations）与主文/其他表格描述不一致，且部分变量均值在不同表格间有显著差异，可能存在样本选择或数据处理不一致问题，需进一步核查。...
- [high] [校准参数敏感性] 校准部分（Calibration）仅报告了基于Table 11参数的最优价格，未进行参数敏感性分析，无法判断结论对关键参数（如价格弹性、效用函数参数等）的稳健性。...
- [high] [数据一致性] 主文与附录表格对样本量定义不统一：主文描述最终完成调查的 households 为 1,282，附录 Table A.8、A.9、A.11 对 Observations 的数值分别为 1,282、6,594/1,282、1,...
- [high] [结果稳健性] 核心结论（激励处理导致6.2-6.7%水消费下降，弹性-0.27）未报告标准误、置信区间及多种稳健性检验，且未讨论处理效应对不同群体的异质性。对其他处理（价格信息、可信度）仅以“无显著效应”略述，未展示完整估计结果。...
- [medium] [理论贡献与创新] 论文将 intrahousehold free-riding 问题与消费外部性结合，提出家庭内部异质性影响价格激励有效性，并通过实地实验验证。创新点在于将家庭成员间的利他性度量与价格弹性结合，且提出针对家庭内部最大水用户...
- [medium] [结果异质性分析] 论文对处理效应的异质性分析较为充分，显示家庭内部利他性（dictator game贡献）与价格弹性高度相关，且针对家庭成员身份（bill payer、water user）进行了交互分析。对激励定向（妻子/丈夫/夫妇）和...

---

## P0 Fix Effectiveness Analysis

P0 修复目标瓶颈 vs 验证结果：

| 修复 | 目标 Gold IDs | 是否被新发现 | 备注 |
|------|--------------|-------------|------|
| AppendixMathAuditSkill | G001 (001), G005 (003) | 见上方匹配结果 | |
| ConsistencyValidator Rule9 | G005 (001) | 见上方匹配结果 | |
| PCG appendix weight | G001 (001), G005 (003) | 见上方匹配结果 | |
