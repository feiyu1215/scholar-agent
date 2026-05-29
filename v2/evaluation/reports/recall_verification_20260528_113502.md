# Recall Verification Report (Post-P0 Fix)

**Generated**: 2026-05-28 11:35:02
**Model**: gpt-4.1
**Total Runtime**: 98.6s
**Papers**: 1

---

## Summary: Baseline vs Post-Fix

| Metric | Baseline | Post-Fix | Delta |
|--------|----------|----------|-------|
| Precision | 0.583 | 0.714 | +0.131 |
| Recall | 0.389 | 0.556 | +0.167 |
| F1 | 0.463 | 0.625 | +0.162 |

---

## paper_001: Environmental Externalities and Free-riding in the Household

**Metrics**: P=0.714 R=0.556 F1=0.625
**Baseline**: P=0.600 R=0.333 F1=0.426
**Agent produced**: 7 findings | Gold: 9 | Matched: 5
**Runtime**: 82.8s | Turns: N/A

### Matched Findings

- **Gold G009** ↔ Agent #1 (sim=0.487)
  - Gold: DID 平行趋势仅通过图形视觉检验，无正式 event-study 回归或前置期系数检验。但注意：这是 RCT 设计（AEARCTR-0000660），平行趋势由随机化在期望意义上保证，event-s...
  - Agent: [方法论缺陷] DID 估计的平行趋势假设仅通过平均水消费趋势图和部分表格展示，没有做正式的统计检验（如 event study 或 placebo test），无法排除未观测异质性导致的偏误。...

- **Gold G003** ↔ Agent #4 (sim=0.473)
  - Gold: 弹性 -0.27 的三个转换假设（风险中性、离散≈连续、概率≈确定性）已在脚注列出，但代入 Table 11 公式(6)做政策校准时，假设偏离对 θ=0.23 的影响未被量化。缺敏感性分析而非缺透明度...
  - Agent: [参数敏感性分析缺失] 校准和政策模拟部分未报告关键参数（如弹性、外部性、异质性）变动对最优价格和福利结论的影响区间，无法判断结论的稳健性。...

- **Gold G007** ↔ Agent #5 (sim=0.295)
  - Gold: 13 个 survey measures（Table 6-8）、多重 heterogeneity 检验无多重检验校正（Bonferroni/FDR）。虽然预注册可部分缓解，但论文未明确区分预注册 vs...
  - Agent: [理论模型与定量实现脱节] 理论模型（2节）设定较为复杂（含λ、α等异质性），但定量部分（5节及校准）大幅简化为二元类型，未充分讨论理论机制在定量实现中是否保留，桥接推导不充分。...

- **Gold G006** ↔ Agent #2 (sim=0.252)
  - Gold: 部分 heterogeneity 结果对 information + incentive treatment pooling 敏感。Appendix Table A.11 显示异质性部分与 infor...
  - Agent: [模型假设外推性不足] 2.4节讨论的核心假设——如家庭内部水消费不可观测、用水决策为家庭层面、个体间完全信息/无强制力——未针对目标经济体的异质性（如家庭结构、文化差异、市场环境）做充分讨论，外推到...

- **Gold G001** ↔ Agent #6 (sim=0.251)
  - Gold: Result 2 推导对 γ_i 求导正确，但结论行误写为 ∂²w*_i/∂p∂α_i < 0（应为 γ_i）。同附录 semi-elasticity 部分正确写为 γ_i，确认是数学符号笔误。...
  - Agent: [符号与定义不一致风险] 理论部分（2节、附录）和定量部分（4-5节）参数符号（如λ、α、θ、η）未见统一映射表，部分符号（如θ、η）在不同章节含义变化，可能导致理解混淆。...

### Missed Gold Findings (False Negatives)

- **G002** [high] θ=1 归一化假设（设定高效率组无扭曲）无敏感性分析。论文承认 'underestimating the average distortion' 但未量化：若 θ_high=0.8，政策数字（最优价格高出64%等）如何变化。额外未检验假设：...
- **G004** [medium] Dictator game 作为 intrahousehold efficiency 的代理变量，全文无直接 construct validity 验证（DG sharing → 实际用水保护行为的因果路径）。间接证据（Table 2 相关...
- **G005** [medium] Table A.3（Information treatment balance）与 Table A.4（Credibility treatment balance）数据完全重复——所有均值、SD、p值完全一致。两个处理的分组方式不同（1/4...
- **G008** [medium] 理论模型为两人家庭（bill-payer + spouse），但实际样本家庭平均约6人。模型推导的 θ（效率参数）在多人决策环境中的解释和适用性未被讨论。...

### False Positives (Agent-only)

- [high] [校准合理性不足] 5节和calibration节的校准目标（如只用两类家庭的弹性）选择过于简化，未报告拟合优度，也未与替代校准目标（如更多分组、其他弹性来源）进行比较，可能低估了模型不确定性。...
- [low] [数据与表述一致性良好] figures and tables节的主要数值（回归系数、样本量、均值）与正文描述一致，未发现明显数据矛盾。...

---

## P0 Fix Effectiveness Analysis

P0 修复目标瓶颈 vs 验证结果：

| 修复 | 目标 Gold IDs | 是否被新发现 | 备注 |
|------|--------------|-------------|------|
| AppendixMathAuditSkill | G001 (001), G005 (003) | 见上方匹配结果 | |
| ConsistencyValidator Rule9 | G005 (001) | 见上方匹配结果 | |
| PCG appendix weight | G001 (001), G005 (003) | 见上方匹配结果 | |
