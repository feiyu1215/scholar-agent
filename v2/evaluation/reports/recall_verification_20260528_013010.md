# Recall Verification Report (Post-P0 Fix)

**Generated**: 2026-05-28 01:30:10
**Model**: gpt-4.1
**Total Runtime**: 126.0s
**Papers**: 1

---

## Summary: Baseline vs Post-Fix

| Metric | Baseline | Post-Fix | Delta |
|--------|----------|----------|-------|
| Precision | 0.583 | 0.571 | -0.012 |
| Recall | 0.389 | 0.444 | +0.055 |
| F1 | 0.463 | 0.500 | +0.037 |

---

## paper_001: Environmental Externalities and Free-riding in the Household

**Metrics**: P=0.571 R=0.444 F1=0.500
**Baseline**: P=0.600 R=0.333 F1=0.426
**Agent produced**: 7 findings | Gold: 9 | Matched: 4
**Runtime**: 121.2s | Turns: N/A

### Matched Findings

- **Gold G003** ↔ Agent #3 (sim=0.373)
  - Gold: 弹性 -0.27 的三个转换假设（风险中性、离散≈连续、概率≈确定性）已在脚注列出，但代入 Table 11 公式(6)做政策校准时，假设偏离对 θ=0.23 的影响未被量化。缺敏感性分析而非缺透明度...
  - Agent: [敏感性分析不足] 校准参数的敏感性分析不充分。Calibration部分仅展示了两组参数（homogenous/heterogeneous），但未系统检验关键参数（如 \chi, E(\theta)...

- **Gold G002** ↔ Agent #7 (sim=0.343)
  - Gold: θ=1 归一化假设（设定高效率组无扭曲）无敏感性分析。论文承认 'underestimating the average distortion' 但未量化：若 θ_high=0.8，政策数字（最优价格...
  - Agent: [政策含义外推局限] 5节最优定价的政策推导高度依赖于一组强假设：高效率组无家庭内部扭曲（θ=1）、无扭曲弹性与家庭效率无关、价格弹性估计无偏。这些假设未被实证检验，且现实中高效率组也可能存在扭曲，弹...

- **Gold G001** ↔ Agent #1 (sim=0.337)
  - Gold: Result 2 推导对 γ_i 求导正确，但结论行误写为 ∂²w*_i/∂p∂α_i < 0（应为 γ_i）。同附录 semi-elasticity 部分正确写为 γ_i，确认是数学符号笔误。...
  - Agent: [数学推导/模型] 附录数学推导部分存在符号和推导步骤简化，部分符号定义不清，且部分推导跳步较多，影响可验证性。例如，Result 1-3 的一阶条件和交叉偏导的符号分析，部分变量（如 \bar{w}...

- **Gold G006** ↔ Agent #5 (sim=0.315)
  - Gold: 部分 heterogeneity 结果对 information + incentive treatment pooling 敏感。Appendix Table A.11 显示异质性部分与 infor...
  - Agent: [结果解释不清] 论文主文与附录表格对处理效应的解释存在模糊。主文称平均处理效应为6.2-6.7%（Table 3），但附录表格（如Table A.9/A.11）显示不同样本和处理组下效应量有较大波动...

### Missed Gold Findings (False Negatives)

- **G004** [medium] Dictator game 作为 intrahousehold efficiency 的代理变量，全文无直接 construct validity 验证（DG sharing → 实际用水保护行为的因果路径）。间接证据（Table 2 相关...
- **G005** [medium] Table A.3（Information treatment balance）与 Table A.4（Credibility treatment balance）数据完全重复——所有均值、SD、p值完全一致。两个处理的分组方式不同（1/4...
- **G007** [medium] 13 个 survey measures（Table 6-8）、多重 heterogeneity 检验无多重检验校正（Bonferroni/FDR）。虽然预注册可部分缓解，但论文未明确区分预注册 vs 探索性分析。...
- **G008** [medium] 理论模型为两人家庭（bill-payer + spouse），但实际样本家庭平均约6人。模型推导的 θ（效率参数）在多人决策环境中的解释和适用性未被讨论。...
- **G009** [low] DID 平行趋势仅通过图形视觉检验，无正式 event-study 回归或前置期系数检验。但注意：这是 RCT 设计（AEARCTR-0000660），平行趋势由随机化在期望意义上保证，event-study 检验为 nice-to-hav...

### False Positives (Agent-only)

- [high] [数据不一致] 部分表格（如 Table A.9, Table A.11）中的处理组数量与主文描述不一致。例如，主文称最终样本为1,282户，但Table A.9、A.11等表格的Observations (HH)为1,275，且部分表格出...
- [medium] [模型假设合理性] 2.4讨论的模型假设存在现实约束未充分讨论。例如，假设家庭成员对水消费和节约努力有完全信息且能独立决策，但现实中水消费常受家庭结构、文化、性别分工等影响，且信息不对称和合作/冲突机制复杂。模型未充分考虑这些异质性和动态互...
- [medium] [异质性解释局限] 4.4节和Table 5-7关于家庭内部异质性（如dictator game、bill payer、gender）与价格弹性关联的解释存在识别局限。部分异质性效应仅在特定子样本（如传统性别分工家庭）显著，且交互项估计不精...

---

## P0 Fix Effectiveness Analysis

P0 修复目标瓶颈 vs 验证结果：

| 修复 | 目标 Gold IDs | 是否被新发现 | 备注 |
|------|--------------|-------------|------|
| AppendixMathAuditSkill | G001 (001), G005 (003) | 见上方匹配结果 | |
| ConsistencyValidator Rule9 | G005 (001) | 见上方匹配结果 | |
| PCG appendix weight | G001 (001), G005 (003) | 见上方匹配结果 | |
