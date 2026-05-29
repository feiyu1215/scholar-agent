# Recall Verification Report (Post-P0 Fix)

**Generated**: 2026-05-28 03:42:06
**Model**: gpt-4.1
**Total Runtime**: 110.6s
**Papers**: 1

---

## Summary: Baseline vs Post-Fix

| Metric | Baseline | Post-Fix | Delta |
|--------|----------|----------|-------|
| Precision | 0.583 | 0.900 | +0.317 |
| Recall | 0.389 | 1.000 | +0.611 |
| F1 | 0.463 | 0.947 | +0.484 |

---

## paper_001: Environmental Externalities and Free-riding in the Household

**Metrics**: P=0.900 R=1.000 F1=0.947
**Baseline**: P=0.600 R=0.333 F1=0.426
**Agent produced**: 10 findings | Gold: 9 | Matched: 9
**Runtime**: 102.9s | Turns: N/A

### Matched Findings

- **Gold G006** ↔ Agent #7 (sim=0.580)
  - Gold: 部分 heterogeneity 结果对 information + incentive treatment pooling 敏感。Appendix Table A.11 显示异质性部分与 infor...
  - Agent: [处理组合(treatment pooling)稳健性不足] 论文在异质性分析中将不同处理组（如价格激励、信息、provider credibility）合并以提升统计功效，但Appendix Tab...

- **Gold G004** ↔ Agent #8 (sim=0.535)
  - Gold: Dictator game 作为 intrahousehold efficiency 的代理变量，全文无直接 construct validity 验证（DG sharing → 实际用水保护行为的因...
  - Agent: [代理变量效度(construct validity)不足] 论文以dictator game行为博弈指标作为家庭内部效率（intrahousehold efficiency）的核心代理变量，但未见直...

- **Gold G009** ↔ Agent #6 (sim=0.495)
  - Gold: DID 平行趋势仅通过图形视觉检验，无正式 event-study 回归或前置期系数检验。但注意：这是 RCT 设计（AEARCTR-0000660），平行趋势由随机化在期望意义上保证，event-s...
  - Agent: [实验设计的识别策略验证缺失] 论文采用DID策略评估处理效应，但未见对平行趋势假设的正式统计检验（如event study, placebo test, pre-trend回归），仅有图形展示。缺乏...

- **Gold G007** ↔ Agent #5 (sim=0.481)
  - Gold: 13 个 survey measures（Table 6-8）、多重 heterogeneity 检验无多重检验校正（Bonferroni/FDR）。虽然预注册可部分缓解，但论文未明确区分预注册 vs...
  - Agent: [多重检验/多重比较校正缺失] 论文报告了多个 outcome（如quantity consumed, bill payment, meter reading）和多个heterogeneity检验（如...

- **Gold G001** ↔ Agent #1 (sim=0.461)
  - Gold: Result 2 推导对 γ_i 求导正确，但结论行误写为 ∂²w*_i/∂p∂α_i < 0（应为 γ_i）。同附录 semi-elasticity 部分正确写为 γ_i，确认是数学符号笔误。...
  - Agent: [符号不一致/推导透明度不足] 附录模型推导和正文存在符号映射不完全透明的问题。正文模型（Section 2及A.1）中主要符号为λ_i, α_i, θ等，但附录推导未明示这些符号与正文参数（如θ、γ...

- **Gold G002** ↔ Agent #9 (sim=0.460)
  - Gold: θ=1 归一化假设（设定高效率组无扭曲）无敏感性分析。论文承认 'underestimating the average distortion' 但未量化：若 θ_high=0.8，政策数字（最优价格...
  - Agent: [政策含义/外部有效性局限] 论文的最优定价校准高度依赖于实验估计的价格弹性和dictator game分组的内生性假设。关键参数θ的绝对水平通过假定高效率组无扭曲（θ=1）强行归一化，且假定无外生相...

- **Gold G005** ↔ Agent #2 (sim=0.417)
  - Gold: Table A.3（Information treatment balance）与 Table A.4（Credibility treatment balance）数据完全重复——所有均值、SD、p值...
  - Agent: [跨表格数据一致性/分组统计异常] Balance tables（如Table A.3, A.4）显示不同处理组（信息处理、provider credibility处理）在核心变量（如Quantity...

- **Gold G003** ↔ Agent #3 (sim=0.374)
  - Gold: 弹性 -0.27 的三个转换假设（风险中性、离散≈连续、概率≈确定性）已在脚注列出，但代入 Table 11 公式(6)做政策校准时，假设偏离对 θ=0.23 的影响未被量化。缺敏感性分析而非缺透明度...
  - Agent: [关键参数归一化假设敏感性不足] 校准部分仅报告θ=1的情形，并承认“optimal price based on the average intrahousehold friction will o...

- **Gold G008** ↔ Agent #4 (sim=0.290)
  - Gold: 理论模型为两人家庭（bill-payer + spouse），但实际样本家庭平均约6人。模型推导的 θ（效率参数）在多人决策环境中的解释和适用性未被讨论。...
  - Agent: [理论模型假设与样本特征张力] 理论模型假设家庭为两成员、单一收入池、可观测努力和消费分配（Section 2），但样本描述性统计（Table A.3, A.4）显示家庭规模均值约5.8人，且存在ma...

### False Positives (Agent-only)

- [medium] [结果解释/政策建议缺乏量化支撑] 结论部分提出多项政策建议（如让主要用水者成为账单支付者、信息干预、自动化技术等），但未对这些建议的实际效应量、可行性、边际收益进行量化评估。仅有定性讨论，缺乏基于实验或模型的定量支撑。...

---

## P0 Fix Effectiveness Analysis

P0 修复目标瓶颈 vs 验证结果：

| 修复 | 目标 Gold IDs | 是否被新发现 | 备注 |
|------|--------------|-------------|------|
| AppendixMathAuditSkill | G001 (001), G005 (003) | 见上方匹配结果 | |
| ConsistencyValidator Rule9 | G005 (001) | 见上方匹配结果 | |
| PCG appendix weight | G001 (001), G005 (003) | 见上方匹配结果 | |
