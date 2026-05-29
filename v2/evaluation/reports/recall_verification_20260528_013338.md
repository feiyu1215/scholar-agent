# Recall Verification Report (Post-P0 Fix)

**Generated**: 2026-05-28 01:33:38
**Model**: gpt-4.1
**Total Runtime**: 145.3s
**Papers**: 1

---

## Summary: Baseline vs Post-Fix

| Metric | Baseline | Post-Fix | Delta |
|--------|----------|----------|-------|
| Precision | 0.583 | 0.429 | -0.154 |
| Recall | 0.389 | 0.333 | -0.056 |
| F1 | 0.463 | 0.375 | -0.088 |

---

## paper_001: Environmental Externalities and Free-riding in the Household

**Metrics**: P=0.429 R=0.333 F1=0.375
**Baseline**: P=0.600 R=0.333 F1=0.426
**Agent produced**: 7 findings | Gold: 9 | Matched: 3
**Runtime**: 138.8s | Turns: N/A

### Matched Findings

- **Gold G001** ↔ Agent #1 (sim=0.453)
  - Gold: Result 2 推导对 γ_i 求导正确，但结论行误写为 ∂²w*_i/∂p∂α_i < 0（应为 γ_i）。同附录 semi-elasticity 部分正确写为 γ_i，确认是数学符号笔误。...
  - Agent: [数学推导] 附录模型推导中，关于交叉偏导数的符号判定（Result 1, 2, 3）与主文模型一致，且在半弹性形式下也给出推导，未发现明显数学错误。...

- **Gold G003** ↔ Agent #3 (sim=0.394)
  - Gold: 弹性 -0.27 的三个转换假设（风险中性、离散≈连续、概率≈确定性）已在脚注列出，但代入 Table 11 公式(6)做政策校准时，假设偏离对 θ=0.23 的影响未被量化。缺敏感性分析而非缺透明度...
  - Agent: [参数敏感性] 校准部分仅用Table 11参数代入公式，未报告对关键参数（如E(θ), Var(θ), χ）的敏感性分析，无法判断最优价格对参数变化的鲁棒性。...

- **Gold G006** ↔ Agent #7 (sim=0.276)
  - Gold: 部分 heterogeneity 结果对 information + incentive treatment pooling 敏感。Appendix Table A.11 显示异质性部分与 infor...
  - Agent: [结果与结论] 论文主结果（激励组水消费下降6.2-6.7%，弹性-0.27）与文献范围一致，且主结果对回归规格、样本选择、变量定义等多种稳健性检验结果稳健。结论未出现overclaim，且对异质性和...

### Missed Gold Findings (False Negatives)

- **G002** [high] θ=1 归一化假设（设定高效率组无扭曲）无敏感性分析。论文承认 'underestimating the average distortion' 但未量化：若 θ_high=0.8，政策数字（最优价格高出64%等）如何变化。额外未检验假设：...
- **G004** [medium] Dictator game 作为 intrahousehold efficiency 的代理变量，全文无直接 construct validity 验证（DG sharing → 实际用水保护行为的因果路径）。间接证据（Table 2 相关...
- **G005** [medium] Table A.3（Information treatment balance）与 Table A.4（Credibility treatment balance）数据完全重复——所有均值、SD、p值完全一致。两个处理的分组方式不同（1/4...
- **G007** [medium] 13 个 survey measures（Table 6-8）、多重 heterogeneity 检验无多重检验校正（Bonferroni/FDR）。虽然预注册可部分缓解，但论文未明确区分预注册 vs 探索性分析。...
- **G008** [medium] 理论模型为两人家庭（bill-payer + spouse），但实际样本家庭平均约6人。模型推导的 θ（效率参数）在多人决策环境中的解释和适用性未被讨论。...
- **G009** [low] DID 平行趋势仅通过图形视觉检验，无正式 event-study 回归或前置期系数检验。但注意：这是 RCT 设计（AEARCTR-0000660），平行趋势由随机化在期望意义上保证，event-study 检验为 nice-to-hav...

### False Positives (Agent-only)

- [medium] [数据一致性] 表格A.3、A.4、A.5等的样本量（Observations）与主文描述一致，未发现跨表格样本量矛盾，但部分变量均值与主文描述略有出入，需进一步核查主文与附录表格的数值一致性。...
- [high] [模型假设合理性] 2.4节讨论水消费的不可观测性和家庭支付机制，合理解释了为何采用非合作模型，但对“完全不可观测”假设未给出实证支持，且未讨论部分可观测或技术进步（如智能水表）对模型预测的影响。...
- [medium] [数据一致性] 主文Table 1、2与附录表格（如A.3、A.4等）在样本量（N=1,282/1,275）和主要变量均值（如household size, share sent to spouse等）基本一致，部分变量（如W: Effec...
- [high] [模型假设合理性] 论文假设“水消费不可观测性”作为采用非合作模型的核心理由，但未引用直接实证文献支持该假设。外部文献（如Rungie et al. 2014, Castilla & Walker 2013）讨论了家庭成员对水消费的认知和信...

---

## P0 Fix Effectiveness Analysis

P0 修复目标瓶颈 vs 验证结果：

| 修复 | 目标 Gold IDs | 是否被新发现 | 备注 |
|------|--------------|-------------|------|
| AppendixMathAuditSkill | G001 (001), G005 (003) | 见上方匹配结果 | |
| ConsistencyValidator Rule9 | G005 (001) | 见上方匹配结果 | |
| PCG appendix weight | G001 (001), G005 (003) | 见上方匹配结果 | |
