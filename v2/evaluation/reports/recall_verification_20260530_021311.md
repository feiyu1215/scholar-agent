# Recall Verification Report (Post-P0 Fix)

**Generated**: 2026-05-30 02:13:11
**Model**: gpt-4.1
**Total Runtime**: 261.0s
**Papers**: 1

---

## Summary: Baseline vs Post-Fix

| Metric | Baseline | Post-Fix | Delta |
|--------|----------|----------|-------|
| Precision | 0.583 | 0.833 | +0.250 |
| Recall | 0.389 | 0.385 | -0.004 |
| F1 | 0.463 | 0.526 | +0.063 |

---

## paper_001: Environmental Externalities and Free-riding in the Household

**Metrics**: P=0.833 R=0.385 F1=0.526
**Baseline**: P=0.600 R=0.333 F1=0.426
**Agent produced**: 6 findings | Gold: 13 | Matched: 5
**Runtime**: 255.4s | Turns: N/A

### Matched Findings

- **Gold G008** ↔ Agent #3 (sim=0.491)
  - Gold: 理论模型为两人家庭（bill-payer + spouse），但实际样本家庭平均约6人。模型推导的 θ（效率参数）在多人决策环境中的解释和适用性未被讨论。...
  - Agent: [模型假设适用性缺陷] 模型以两人家庭为基础，忽略了样本中平均约6人的实际家庭规模及多代同堂、儿童、非直系亲属等复杂结构。样本筛选仅纳入夫妻同住家庭，未覆盖复杂家庭结构。价格机制现实中存在多样化（如阶...

- **Gold G003** ↔ Agent #5 (sim=0.479)
  - Gold: 弹性 -0.27 的三个转换假设（风险中性、离散≈连续、概率≈确定性）已在脚注列出，但代入 Table 11 公式(6)做政策校准时，假设偏离对 θ=0.23 的影响未被量化。缺敏感性分析而非缺透明度...
  - Agent: [参数校准目标与敏感性分析不充分] 第5节及校准部分仅将表11参数代入模型计算最优价格，缺乏对目标矩选择的理论或经验合理性讨论，未进行关键参数（如水用量、价格弹性、外部性参数、异质性设定等）的敏感性分...

- **Gold G009** ↔ Agent #4 (sim=0.458)
  - Gold: DID 平行趋势仅通过图形视觉检验，无正式 event-study 回归或前置期系数检验。但注意：这是 RCT 设计（AEARCTR-0000660），平行趋势由随机化在期望意义上保证，event-s...
  - Agent: [方法论缺陷] 主要效应估计依赖差分中的差分（DID）方法，文中声称满足平行趋势假设，但未展示任何平行趋势检验结果（如事件研究图或前期趋势系数表），影响因果识别的可信度。...

- **Gold G007** ↔ Agent #1 (sim=0.321)
  - Gold: 13 个 survey measures（Table 6-8）、多重 heterogeneity 检验无多重检验校正（Bonferroni/FDR）。虽然预注册可部分缓解，但论文未明确区分预注册 vs...
  - Agent: [方法论缺陷] 论文在第5节校准最优价格时，假设高效家庭无内部分歧（θ=1），该假设过于武断且未充分讨论其现实合理性及异质性。2.4节虽讨论了水作为家庭公共品等假设，但未对θ=1的适用性、目标经济体的...

- **Gold G001** ↔ Agent #6 (sim=0.305)
  - Gold: Result 2 推导对 γ_i 求导正确，但结论行误写为 ∂²w*_i/∂p∂α_i < 0（应为 γ_i）。同附录 semi-elasticity 部分正确写为 γ_i，确认是数学符号笔误。...
  - Agent: [理论与定量模型桥接充分] 论文理论模型（第2节）与定量估计（第4节）之间桥接较为充分。理论部分对符号定义清晰，机制推导严密。定量部分采用DID设定，treat_it变量直接映射理论中的价格变动，并通...

### Missed Gold Findings (False Negatives)

- **G002** [high] θ=1 归一化假设（设定高效率组无扭曲）无敏感性分析。论文承认 'underestimating the average distortion' 但未量化：若 θ_high=0.8，政策数字（最优价格高出64%等）如何变化。额外未检验假设：...
- **G004** [medium] Dictator game 作为 intrahousehold efficiency 的代理变量，全文无直接 construct validity 验证（DG sharing → 实际用水保护行为的因果路径）。间接证据（Table 2 相关...
- **G005** [medium] Table A.3（Information treatment balance）与 Table A.4（Credibility treatment balance）数据完全重复——所有均值、SD、p值完全一致。两个处理的分组方式不同（1/4...
- **G006** [medium] 部分 heterogeneity 结果对 information + incentive treatment pooling 敏感。Appendix Table A.11 显示异质性部分与 information treatment 相关。...
- **G010** [high] 激励处理采用抽奖（1/20概率）而非确定性价格变化，模型假设风险中性，但实际效应可能受风险厌恶/概率权重影响，导致激励不等价于价格变化，影响价格弹性估计的外推性。论文在脚注承认此假设但未量化偏离的影响。...
- **G011** [medium] Pi处理（仅一人知晓激励）未必能完全隔离个人价格效应。家庭成员间可能沟通导致信息泄露，实际处理效应可能偏离理论设定的个人vs联合价格对比。论文承认此局限但未提供泄露率估计或稳健性检验。...
- **G012** [medium] 样本筛选严格（9868→7425→1817→1282户），排除了极低/极高用水户、异常账单户和非核心家庭。最终样本仅占初始的13%，样本代表性受限，外推到更广泛人群（尤其是极端用水行为的家庭）存疑。...
- **G013** [medium] 价格信息干预与激励处理完全重叠（所有激励组都接受价格信息），无法区分价格认知变化与激励本身的独立效应。这一设计选择使得机制识别受限——观察到的行为变化可能部分来自信息效应而非纯价格激励。...

### False Positives (Agent-only)

- [high] [样本代表性与外推风险] 样本筛选极为严格，最终有效样本仅占初始家庭的13%，高流失率主要因调查截止时间非随机失访，可能导致样本代表性不足。样本仅涵盖赞比亚Livingstone市有水表家庭，排除无表户和未成功抄表月份，且低收入及用水不规范...

---

## P0 Fix Effectiveness Analysis

P0 修复目标瓶颈 vs 验证结果：

| 修复 | 目标 Gold IDs | 是否被新发现 | 备注 |
|------|--------------|-------------|------|
| AppendixMathAuditSkill | G001 (001), G005 (003) | 见上方匹配结果 | |
| ConsistencyValidator Rule9 | G005 (001) | 见上方匹配结果 | |
| PCG appendix weight | G001 (001), G005 (003) | 见上方匹配结果 | |
