# Recall Verification Report (Post-P0 Fix)

**Generated**: 2026-05-28 22:57:25
**Model**: gpt-4.1-mini
**Total Runtime**: 87.7s
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
**Agent produced**: 5 findings | Gold: 9 | Matched: 0
**Runtime**: 77.6s | Turns: N/A

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

- [high] [方法论缺陷] 模型假设家庭成员之间存在非合作博弈，基于道德风险团队模型，但对目标经济体（赞比亚Livingstone）家庭结构复杂性（如多成员家庭、佣人存在）的适用性讨论不足，可能影响模型外推性。...
- [medium] [数据一致性] 论文中水价弹性估计值与文献中短期弹性范围相符，但缺乏对长期弹性及弹性估计稳健性的充分讨论。...
- [high] [方法论缺陷] 估计策略采用差分中的差分模型，未报告平行趋势检验，可能影响因果推断的有效性。...
- [medium] [方法论缺陷] 利用夫妻间修改版独裁者博弈作为内生变量测量家庭内部利他性，但该测量可能混淆利他性与执行力，且未充分讨论测量误差及其对估计的影响。...
- [medium] [逻辑漏洞] 结论部分提出家庭未采用将大用水者设为账单付款人的安排，解释为信息不对称和习惯问题，但缺乏量化分析支持该推断。...

---

## P0 Fix Effectiveness Analysis

P0 修复目标瓶颈 vs 验证结果：

| 修复 | 目标 Gold IDs | 是否被新发现 | 备注 |
|------|--------------|-------------|------|
| AppendixMathAuditSkill | G001 (001), G005 (003) | 见上方匹配结果 | |
| ConsistencyValidator Rule9 | G005 (001) | 见上方匹配结果 | |
| PCG appendix weight | G001 (001), G005 (003) | 见上方匹配结果 | |
