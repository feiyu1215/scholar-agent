# Recall Verification Report (Post-P0 Fix)

**Generated**: 2026-05-28 11:09:34
**Model**: gpt-4.1
**Total Runtime**: 120.8s
**Papers**: 1

---

## Summary: Baseline vs Post-Fix

| Metric | Baseline | Post-Fix | Delta |
|--------|----------|----------|-------|
| Precision | 0.583 | 0.375 | -0.208 |
| Recall | 0.389 | 0.333 | -0.056 |
| F1 | 0.463 | 0.353 | -0.110 |

---

## paper_001: Environmental Externalities and Free-riding in the Household

**Metrics**: P=0.375 R=0.333 F1=0.353
**Baseline**: P=0.600 R=0.333 F1=0.426
**Agent produced**: 8 findings | Gold: 9 | Matched: 3
**Runtime**: 111.7s | Turns: N/A

### Matched Findings

- **Gold G002** ↔ Agent #3 (sim=0.527)
  - Gold: θ=1 归一化假设（设定高效率组无扭曲）无敏感性分析。论文承认 'underestimating the average distortion' 但未量化：若 θ_high=0.8，政策数字（最优价格...
  - Agent: [参数识别与外推假设不充分] 经验部分将高 dictator game sharing 家庭的价格弹性作为无摩擦基准（θ=1），但未有外部证据支持该组确实无内部摩擦，且未检验该假设对最优价格校准的敏感...

- **Gold G008** ↔ Agent #2 (sim=0.499)
  - Gold: 理论模型为两人家庭（bill-payer + spouse），但实际样本家庭平均约6人。模型推导的 θ（效率参数）在多人决策环境中的解释和适用性未被讨论。...
  - Agent: [理论模型与定量模型桥接不充分] 理论模型推导与经验部分的映射存在跳跃，符号与假设未系统对应。例如，理论模型以两人家庭为单位，符号 λ_i, α_i 代表个体参数，但经验部分实际操作为家庭平均/二元指...

- **Gold G003** ↔ Agent #4 (sim=0.445)
  - Gold: 弹性 -0.27 的三个转换假设（风险中性、离散≈连续、概率≈确定性）已在脚注列出，但代入 Table 11 公式(6)做政策校准时，假设偏离对 θ=0.23 的影响未被量化。缺敏感性分析而非缺透明度...
  - Agent: [稳健性检验覆盖有限] 虽有多种稳健性检验（如不同回归规格、样本扩展、变量聚合方式），但对关键假设（如高效家庭=无摩擦、外部性参数选取、价格弹性异质性来源）缺乏系统敏感性分析。未报告假设变动对最优价格...

### Missed Gold Findings (False Negatives)

- **G001** [high] Result 2 推导对 γ_i 求导正确，但结论行误写为 ∂²w*_i/∂p∂α_i < 0（应为 γ_i）。同附录 semi-elasticity 部分正确写为 γ_i，确认是数学符号笔误。...
- **G004** [medium] Dictator game 作为 intrahousehold efficiency 的代理变量，全文无直接 construct validity 验证（DG sharing → 实际用水保护行为的因果路径）。间接证据（Table 2 相关...
- **G005** [medium] Table A.3（Information treatment balance）与 Table A.4（Credibility treatment balance）数据完全重复——所有均值、SD、p值完全一致。两个处理的分组方式不同（1/4...
- **G006** [medium] 部分 heterogeneity 结果对 information + incentive treatment pooling 敏感。Appendix Table A.11 显示异质性部分与 information treatment 相关。...
- **G007** [medium] 13 个 survey measures（Table 6-8）、多重 heterogeneity 检验无多重检验校正（Bonferroni/FDR）。虽然预注册可部分缓解，但论文未明确区分预注册 vs 探索性分析。...
- **G009** [low] DID 平行趋势仅通过图形视觉检验，无正式 event-study 回归或前置期系数检验。但注意：这是 RCT 设计（AEARCTR-0000660），平行趋势由随机化在期望意义上保证，event-study 检验为 nice-to-hav...

### False Positives (Agent-only)

- [high] [模型假设适用性不足] 对代表性家庭、二人模型、价格机制等假设在目标经济体（赞比亚城市家庭）下的适用性讨论不充分，尤其未充分分析家庭结构复杂性（如有佣人、多人户）、水费支付责任分散等对模型外推的影响。模型采用两人家庭设定，但样本平均户规模为...
- [medium] [实验操控与模型假设适配性有限] 实验干预（价格激励）通过抽奖形式实现，导致实际价格变化为离散且期望值不确定，且激励仅适用于特定消费区间。模型假设为连续边际价格变化，但实验与模型的映射存在结构性差异，外推到一般价格弹性时有偏风险。...
- [medium] [符号与参数定义不一致风险] 理论模型（Section 2, 2.4）与定量分析（Section 4.2, 5）中参数符号（如θ, λ, α）定义和用途未始终一致，部分符号在经验部分未明确定义，可能导致读者对模型与实证映射产生混淆。...
- [medium] [引用与文献综述不充分] 虽然参考文献详尽，但对关键理论假设（如家庭决策模型、价格机制、外部性校准参数χ）的文献基础和经验支持讨论不足，未系统对比同类研究的参数选取与识别策略。...
- [high] [实证结果解释存在 overclaim 风险] 论文将 6.2-6.7% 的消费下降归因于激励处理，并声称短期价格弹性为-0.27，但实验采用离散抽奖激励，且激励区间有限，实际价格变化与模型假设的连续边际价格变动不符，elasticity ...

---

## P0 Fix Effectiveness Analysis

P0 修复目标瓶颈 vs 验证结果：

| 修复 | 目标 Gold IDs | 是否被新发现 | 备注 |
|------|--------------|-------------|------|
| AppendixMathAuditSkill | G001 (001), G005 (003) | 见上方匹配结果 | |
| ConsistencyValidator Rule9 | G005 (001) | 见上方匹配结果 | |
| PCG appendix weight | G001 (001), G005 (003) | 见上方匹配结果 | |
