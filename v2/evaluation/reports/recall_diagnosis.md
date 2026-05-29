# Recall 诊断报告

生成时间：2025-05-27
基准：DEEP_REVIEW_REPORT.md 人工审稿 + 交叉验证后的 Gold Standard

---

## 一、匹配方法论

**匹配规则**：agent finding 与 gold finding 匹配需满足：
1. 指向同一论文位置/同一段落的同类问题
2. 问题核心相同（允许措辞差异和 severity 差异）
3. 部分匹配（agent 触及了问题但描述不准确/不完整）单独标记

**Precision 计算**：True Positive / (True Positive + False Positive)
**Recall 计算**：True Positive / (True Positive + False Negative)
**F1**：2 × P × R / (P + R)

注：部分匹配按 0.5 计入 TP。

---

## 二、Paper 001 逐条匹配

### Gold Standard（9条）vs Agent Findings（5条去重后）

| Gold ID | Gold 内容 | Agent 匹配 | 匹配类型 |
|---------|-----------|------------|----------|
| G001 | 附录 Result 2 符号 γ_i→α_i 笔误 | ❌ 未发现 | MISS |
| G002 | θ=1 归一化无 sensitivity analysis | ❌ 未发现 | MISS |
| G003 | 弹性-0.27 校准无 sensitivity analysis | ❌ 未发现 | MISS |
| G004 | Dictator game construct validity | ✅ Agent #3 | FULL HIT |
| G005 | Table A.3/A.4 数据重复 | ❌ 未发现 | MISS |
| G006 | Treatment pooling 敏感性 | ✅ Agent #7 | FULL HIT |
| G007 | 多重检验未校正 | ❌ 未发现 | MISS |
| G008 | 两人模型 vs 6人家庭张力 | ❌ 未发现 | MISS |
| G009 | DID 平行趋势无正式检验 | ✅ Agent #1 | FULL HIT (但 severity 被高估) |

### Paper 001 Agent False Positives（agent 报告但 gold 中不存在/被推翻的）

| Agent # | 内容 | 判定 |
|---------|------|------|
| #2/#4/#5 | 系数与 6.2-6.7% 换算"不透明" | **FALSE POSITIVE** — 标准 log-level 转换，完全透明 |
| #6 | 政策建议"仅基于40户" | **FALSE POSITIVE** — 混淆了定性访谈与定量校准的论据来源 |

### Paper 001 指标计算

```
True Positive (full):  3 (G004, G006, G009)
True Positive (partial): 0
False Positive:        2 (#2系列, #6)
False Negative (miss): 6 (G001, G002, G003, G005, G007, G008)

Precision = 3 / (3 + 2) = 60.0%
Recall    = 3 / 9 = 33.3%
F1        = 2 × 0.60 × 0.33 / (0.60 + 0.33) = 42.6%
```

### Paper 001 遗漏分类分析

| 遗漏 Gold | 类型 | 遗漏原因分析 |
|-----------|------|--------------|
| G001 (符号错误) | mathematical_error | **深度阅读不足**：需要逐行跟踪附录推导，对比变量名。Agent 的 cognitive loop 未进入附录数学推导层 |
| G002 (θ=1 无sensitivity) | missing_sensitivity_analysis | **政策校准理解不足**：需要理解 Section 5 是在用 Section 4 的估计做政策模拟，并评估关键参数假设的稳健性 |
| G003 (弹性无sensitivity) | missing_sensitivity_analysis | **同上**：需要理解弹性转换的假设链条及其对下游政策数字的影响 |
| G005 (Table重复) | data_error | **跨表对比能力缺失**：Phase 9A 表格处理已激活（kill switch 默认 ON），但 ConsistencyValidator 仅有单表内验证（8条规则），缺少跨表数据重复检测能力 |
| G007 (多重检验) | multiple_testing | **统计方法论知识不足**：未触发"多重比较"检查的 Skill |
| G008 (模型-现实差距) | model_reality_gap | **高阶推理缺失**：需要将理论模型设定与样本特征交叉对比 |

---

## 三、Paper 003 逐条匹配

### Gold Standard（10条）vs Agent Findings（8条去重后）

| Gold ID | Gold 内容 | Agent 匹配 | 匹配类型 |
|---------|-----------|------------|----------|
| G001 | 定量模型无敏感性分析 | ✅ Agent #5/#7 | FULL HIT |
| G002 | 标准CES→嵌套CES结构性脱节 | ❌ 未发现 | MISS |
| G003 | 小国假设对186国适用性 | ⚠️ Agent #11 (partial) | PARTIAL — agent 提到"大国小国未区分"但未指出核心问题（对大国的quantitative approximation quality） |
| G004 | 符号系统不统一 | ✅ Agent #4/#9 | FULL HIT |
| G005 | 公式(44) θ₁→θ₂ 排版错误 | ❌ 未发现 | MISS |
| G006 | ω=σ/1.25 校准方法论不足 | ❌ 未发现 | MISS |
| G007 | Grid search 细节不足 | ❌ 未发现 | MISS |
| G008 | 文献空白声称 vs CRW覆盖 | ✅ Agent #3/#6 | FULL HIT (但 severity 被高估为 High) |
| G009 | 小国假设量化影响 | ⚠️ 与 G003 合并 | 与G003是同一问题的不同层面，合并为一条（partial hit）。Recall 分母按9计 |
| G010 | 双重边际化scope limitation | ⚠️ Agent #10 (partial) | PARTIAL — agent 方向正确但 severity 高估 |

### Paper 003 Agent False Positives

| Agent # | 内容 | 判定 |
|---------|------|------|
| #1/#8 | "参数约束是否普遍成立未讨论" | **FALSE POSITIVE** — Figure 1 整页展示186国全满足，论文有充分讨论 |
| #2 | "摘要10%与正文数值关系不清" | **FALSE POSITIVE** — 正文多处一致报告 median 10%，Table 3 有完整结果 |
| #12 | "所有结果为点估计无置信区间" | **FALSE POSITIVE** — calibration 文献规范不要求置信区间（与 sensitivity analysis 是不同 concern） |

### Paper 003 指标计算

```
Gold Standard 有效条目：9条（G003+G009合并为一条）

True Positive (full):     3 (G001, G004, G008)
True Positive (partial):  2 × 0.5 = 1.0 (G003/G009合并, G010)
False Positive:           3 (#1系列, #2, #12)
False Negative (miss):    4 (G002, G005, G006, G007)

Precision = 4.0 / (4.0 + 3) = 57.1%
Recall    = 4.0 / 9 = 44.4%
F1        = 2 × 0.571 × 0.444 / (0.571 + 0.444) = 49.9%
```

**修正说明**：G003 和 G009 指向同一问题（小国假设）的不同表述层面，合并计入一条。Recall 分母由 10 调整为 9。

### Paper 003 遗漏分类分析

| 遗漏 Gold | 类型 | 遗漏原因分析 |
|-----------|------|--------------|
| G002 (CES结构脱节) | structural_disconnect | **理论深度不足**：需理解 ω=σ 与 ω≠σ 在数学推导中的根本区别，及 Theorem 适用性的条件 |
| G005 (公式typo) | typographical_error | **公式逐行校对能力缺失**：需要逐公式对比变量一致性。与 Paper 001 G001 同类 |
| G006 (ω校准) | calibration_justification | **校准方法论审查缺失**：未触发"校准参数合理性"检查 |
| G007 (grid search) | computational_detail | **计算方法细节审查缺失**：未触发数值方法透明度检查 |
| G009 (小国量化) | assumption_validity | **定量推理缺失**：能发现"小国假设"但无法进一步追问"对大国的近似质量如何" |

---

## 四、综合诊断

### 总体指标

| 指标 | Paper 001 | Paper 003 | 加权平均 |
|------|-----------|-----------|----------|
| Precision | 60.0% | 57.1% | **58.3%** |
| Recall | 33.3% | 44.4% | **38.9%** |
| F1 | 42.6% | 49.9% | **46.3%** |

### Recall 瓶颈分类（按遗漏原因汇总）

| 遗漏原因类别 | 频次 | 占比 | 涉及 Gold IDs |
|-------------|------|------|---------------|
| **深度阅读不足**（附录/推导/表格逐行） | 3 | 30% | 001-G001, 001-G005, 003-G005 |
| **高阶推理/跨section关联** | 4 | 40% | 001-G002, 001-G003, 001-G008, 003-G002 |
| **领域方法论知识** | 3 | 30% | 001-G007(多重检验), 003-G006(校准方法论), 003-G007(数值方法审查意识) |

注：G003/G009 已合并且有 partial hit，不再计入 miss。总 miss 数为 10 条（Paper 001: 6, Paper 003: 4）。003-G007（grid search）从"深度阅读"重新归类为"领域方法论知识"——因为该问题不需要逐行阅读才能发现，而是需要有"数值方法应报告充分细节"的审查意识。

### False Positive 根因分析（共5条）

| 根因类别 | 频次 | 具体表现 |
|---------|------|----------|
| **浅读/未看到论文已有讨论** | 2 | 003-#1(忽略Figure 1), 003-#2(未核实Table 3) |
| **领域规范不熟悉** | 2 | 001-#2(log-level转换是标准做法), 003-#12(calibration不报CI) |
| **论文结构误读** | 1 | 001-#6(混淆定性访谈与定量校准来源) |

---

## 五、改进方向建议（优先级排序）

### 优先级 1：深度阅读能力（解决 36% 的遗漏）

**问题**：Agent 的 cognitive loop 不进入附录数学推导、不逐表格对比数据、不逐公式校对变量名。

**修复方案**：
1. ~~激活 Phase 9A~~ Phase 9A 已激活；需增强 ConsistencyValidator 跨表对比能力（Rule 9）— 解决 001-G005 类问题 ✅ 已实现
2. 新增"附录数学审查" skill (AppendixMathAuditSkill) — 扫描附录推导，对比变量名一致性 ✅ 已实现
3. 增加公式校对 pass — 逐公式检查下标/上标/变量名与前后文一致 ✅ 已包含在 AppendixMathAuditSkill 中
4. PCG 领域模板增加 appendix 权重 — 确保 Zone B 上下文加载覆盖附录 ✅ 已实现

**预估 Recall 提升**：+10-15%

### 优先级 2：高阶推理/跨section关联（解决 36% 的遗漏）

**问题**：Agent 能分 section 阅读但不善于跨 section 关联（如"Section 4 的估计 → Section 5 的政策模拟 → 估计假设的敏感性如何传导到政策结论"）。

**修复方案**：
1. 新增"assumption propagation" 策略 — 完成一轮审稿后，专门追问"每个关键假设如果偏离会怎样"
2. 增强 hypothesis-driven 深挖 — 在识别出模型假设后，自动生成"如果这个假设不成立，哪些结论受影响"的假设
3. 新增"model-data consistency" 检查 — 将理论模型设定与样本描述性统计交叉对比

**预估 Recall 提升**：+10-12%

### 优先级 3：领域方法论知识（解决 18% 的遗漏）

**问题**：Agent 不知道"多重检验校正"是标准审稿关注点、不知道"校准参数需要 sensitivity analysis"是该领域规范。

**修复方案**：
1. 构建"审稿 checklist" skill — 覆盖常见方法论审查点（多重检验、弱IV、过度识别检验、敏感性分析等）
2. 在 cognitive habits 中嵌入"方法论 checklist trigger" — 每次遇到 calibration/estimation 时自动触发相关检查

**预估 Recall 提升**：+5-8%

### 优先级 4：减少 False Positive（提升 Precision）

**问题**：Agent 在不确定论文是否已讨论时倾向于"有疑必报"。

**修复方案**：
1. 新增"二次确认" pass — 在 finding 被记录后，专门搜索全文是否已有对应讨论
2. 增加领域规范知识库 — 让 agent 知道哪些做法在特定领域是标准的（不需要报告为问题）

**预估 Precision 提升**：+15-20%

---

## 六、修复优先级与 Phase 对应

| 修复方向 | 对应系统 Phase | 优先级 | 预估影响 | 状态 |
|---------|---------------|--------|----------|------|
| 跨表对比（Rule 9） | Phase 9A ConsistencyValidator | P0 | Recall +5% | ✅ 已完成 |
| 附录/公式审查 | 新增 AppendixMathAuditSkill | P0 | Recall +8% | ✅ 已完成 |
| PCG appendix 权重 | paper_cognition_graph.py | P0 | Zone B 覆盖 | ✅ 已完成 |
| Assumption propagation | 新增策略 in cognitive_loop | P1 | Recall +10% | 待实现 |
| 审稿 checklist | 新增 Skill | P1 | Recall +6% | 待实现 |
| 二次确认 pass | 修改 finding 记录逻辑 | P2 | Precision +15% | 待实现 |
| 领域规范知识库 | 修改 prompt / habit | P2 | Precision +8% | 待实现 |

**综合预期**：
- Recall: 37% → 55-65%（优先级 1+2 完成后）
- Precision: 58% → 75-80%（优先级 4 完成后）
- F1: 45% → 65-72%

---

## 七、与 EXECUTION_PLAN 的衔接

基于诊断结果，阶段 1 的执行优先级调整为：

```
[P0 — 深度阅读能力提升] ✅ 已完成 (2025-05-27)
  F.3-a: AppendixMathAuditSkill — 附录推导审查 + 公式符号校对
  F.3-a': ConsistencyValidator Rule 9 — 跨表数据重复检测
  F.3-a'': PCG empirical_econ/theoretical 模板 appendix 权重提升

[P1 — 高阶推理 + 方法论知识] ⏳ 待实现
  F.3-b: 新增"assumption propagation" 策略 [解决跨section推理]
  F.3-c: 新增"审稿 checklist" skill [解决方法论知识]

[P2 — Precision 提升] ⏳ 待实现
  F.3-d: 新增"二次确认 + 去重" pass [解决 FP 和重复]
      ↓
验证：重新跑 paper_001 + paper_003，计算新 P/R/F1
```
