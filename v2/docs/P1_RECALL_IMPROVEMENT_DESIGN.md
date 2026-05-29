# P1 Recall 提升方案设计

**日期**: 2026-05-28
**背景**: P0 验证完成，P0 目标命中 4/6。本文档规划 P1 阶段的方案，目标是解决剩余 miss + 2个未命中 P0 目标。

---

## 一、问题全景

### Post-Fix 仍然 Miss 的 Gold Findings

| Gold ID | 问题 | 遗漏类别 | 需要的能力 |
|---------|------|----------|-----------|
| **G005(001)** | Table A.3/A.4 数据完全重复 | 跨表对比 | 逐表逐行数据对比 |
| **G005(003)** | 公式(44) θ₁→θ₂ 排版错误 | 公式逐行校对 | 逐公式变量名一致性检查 |
| **G002(001)** | θ=1 归一化假设无 sensitivity analysis | 跨section关联 | 假设传播 + 政策影响推理 |
| **G004(001)** | Dictator game construct validity | 跨section关联 | 实证检验设计 ↔ 声明一致性 |
| **G007(001)** | 多重检验未校正 | 领域方法论 | 统计学审查 checklist |
| **G008(001)** | 两人模型 vs 6人家庭 | 模型-现实对比 | 假设 ↔ 样本特征交叉 |
| **G009(001)** | DID 平行趋势无正式检验 | 领域方法论 | 因果推断 checklist |
| **G004(003)** | 符号系统不统一 | 公式校对 | 全文变量名一致性 |
| **G006(003)** | ω=σ/1.25 校准方法不足 | 校准方法论 | 校准合理性 checklist |
| **G007(003)** | Grid search 细节不足 | 数值方法 | 计算方法透明度 checklist |
| **G008(003)** | 文献空白声称 vs CRW覆盖 | 文献覆盖 | 文献审查 + 查证 |
| **G009/G010(003)** | 小国量化 + 双重边际化 | 深度追问 | 假设影响量化推理 |

### 根因聚类

1. **逐行精确校对** (3条): G005(001), G005(003), G004(003)
2. **假设传播/跨section推理** (4条): G002(001), G004(001), G008(001), G009/G010(003)
3. **领域审查 Checklist** (4条): G007(001), G009(001), G006(003), G007(003)
4. **文献查证** (1条): G008(003)

---

## 二、参考外部 Agent 架构

### 2.1 MARG（Multi-Agent Review Generation, Allen AI 2024）

MARG 的核心架构：
- **Leader agent**: 协调整体审稿方向
- **Worker agents**: 按文本 chunk 分配，确保全文覆盖
- **Expert agents**: 按审查维度并行——实验设计、表达清晰度、影响力

**对 ScholarAgent 的启发**：当前 ScholarAgent 是**单 agent 多轮**架构。MARG 证明了**维度级并行专家**比单 agent 遍历更能保证覆盖。我们不需要变成完全的 multi-agent，但可以用已有的 `__PARALLEL_SPAWN__` 机制来实现**维度并行深审**。

### 2.2 Anthropic Multi-Agent Research System (2025)

Anthropic 的做法：
- Lead agent 做规划和综合，**不做主体研究**
- 多个 subagent 并行搜索不同方向
- 所有结果写入共享文件系统
- Citation agent 做事后验证

**对 ScholarAgent 的启发**：我们的 cognitive loop lead agent 既做规划也做执行，注意力被分散。可以让 lead agent 在 DEEP_REVIEW 中期**显式规划覆盖策略**，然后 spawn 专项 subagents。

### 2.3 Multi-Agent Code Review（Qodo / CodeRabbit 2026）

Code review 领域的做法：
- 每个 agent 负责一个质量维度（security / performance / correctness / design / test）
- **Orchestrator 去重 + 冲突解决**
- **对抗验证**：一个 agent 的 finding 被另一个 agent 尝试推翻
- **Confidence scoring**：每个 finding 带置信度，低置信的不 block merge

**对 ScholarAgent 的启发**：
1. **维度覆盖保证**——每个维度至少被一个专项 pass 覆盖
2. **对抗验证降低 FP**——finding 记录后，反方 agent 搜索论文是否已有回应
3. **置信度分级呈报**——不是所有 finding 都同等重要

### 2.4 PaperQA2（Future House, 2024）

PaperQA2 的做法：
- 多步迭代：search → gather evidence → rank relevance → synthesize → **refine query if insufficient**
- **不满意就重新搜索**的循环机制

**对 ScholarAgent 的启发**：Agent 的假设验证过程可以借鉴"不满足就重查"的迭代。当前 agent 对一个问题只做一次浅探就放弃。

---

## 三、解决方案架构

### 总体策略：三层覆盖保证

```
Layer 1: 维度驱动的 Mandatory Checklist（解决"什么都没查"问题）
Layer 2: 专项 Sub-Agent 并行深审（解决"查了但没查到位"问题）
Layer 3: 对抗验证 Pass（解决"查到了但是错的"问题）
```

---

### Layer 1: 维度驱动 Mandatory Checklist

**核心思想**：不依赖 agent 的自主注意力，而是**系统级强制要求**在 DEEP_REVIEW 完成前，必须对每个维度至少做过一次检查。

**实现位置**：扩展 `PCG coverage_gaps()` + 新增 `ReviewChecklist` 数据结构

**数据结构**：
```python
@dataclass
class ReviewChecklist:
    """论文审查维度清单——PCG 的补充，追踪分析维度而非section"""
    
    dimensions: dict[str, ChecklistItem] = field(default_factory=dict)
    
@dataclass  
class ChecklistItem:
    name: str
    description: str
    status: Literal["unchecked", "checked", "finding_recorded", "na"]
    checked_at_turn: int | None = None
    finding_ids: list[str] = field(default_factory=list)
    notes: str = ""
```

**维度列表**（根据 paper_type 动态生成）：

对于 `empirical_econ` 类型论文：
```python
EMPIRICAL_ECON_CHECKLIST = [
    # 数据层
    ("data_consistency", "跨表数据一致性：同一统计量在不同表格中是否相同"),
    ("sample_selection", "样本选择：attrition、筛选标准、代表性"),
    ("measurement_validity", "测量有效性：代理变量 → 理论构念的因果路径"),
    
    # 方法论层
    ("identification_strategy", "识别策略：关键假设是否有正式检验"),
    ("multiple_testing", "多重比较：Bonferroni/FDR/permutation 校正"),
    ("robustness_checks", "稳健性：规格敏感性、替代度量、子样本"),
    ("parallel_trends", "平行趋势/预处理检验（DID/RCT）"),
    
    # 理论层
    ("model_assumptions", "模型假设 ↔ 数据样本特征匹配"),
    ("parameter_sensitivity", "参数敏感性分析充分性"),
    ("assumption_propagation", "假设偏离 → 结论影响的量化"),
    
    # 数学层
    ("formula_consistency", "公式变量名/下标一致性"),
    ("cross_table_consistency", "跨表数值一致性"),
    
    # 文献层
    ("novelty_claims", "创新性声称 ↔ 已有文献覆盖"),
]
```

**触发机制**：
1. Paper load 时根据 paper_type 初始化 Checklist
2. 每次 finding 记录时自动匹配对应维度标记为 "finding_recorded"
3. Agent 读取/搜索相关 section 时标记为 "checked"
4. **Phase 转换门控**：DEEP_REVIEW → SYNTHESIS 时，若有 unchecked 维度，注入 nudge
5. **Completion Gate 扩展**：done 前检查所有 high-priority 维度是否至少 "checked"

---

### Layer 2: 专项 Sub-Agent 并行深审

**核心思想**：利用已有的 `__PARALLEL_SPAWN__` 机制，对 Checklist 中高难度维度 spawn 带有专项 system prompt 的子 agent。

#### Sub-Agent A: Formula & Symbol Auditor

**解决**: G005(001), G005(003), G004(003)

```
你是一个公式校对专家。你的唯一任务是逐公式检查：
1. 同一变量是否在不同位置使用了不同的下标/上标
2. 推导链中变量是否保持一致（如连续公式从 θ₁ 突变为 θ₂）
3. 跨 section 的变量定义是否统一（正文 vs 附录）
4. 附录公式与正文公式的参数对应关系

方法：建立 symbol table，逐公式更新，检测 anomaly
输出：每个不一致列出 [位置A, 公式X] vs [位置B, 公式Y] + 具体差异
```

**触发条件**：paper 包含 appendix 且有 ≥5 个公式

#### Sub-Agent B: Cross-Table Data Validator

**解决**: G005(001)

```
你是一个数据一致性审计员。你的唯一任务是：
1. 提取所有表格中的数值数据（均值、SD、N、p值）
2. 检查同一统计量在不同表格中的值是否一致
3. 检查样本量 N 在不同表格间是否对应
4. 重点：检查 balance table 间的数据是否存在不合理的完全重复

方法：提取 → 建矩阵 → pairwise 比较
输出：每个不一致/可疑重复列出 [Table X, Row/Col] vs [Table Z, Row/Col]
```

**触发条件**：paper 有 ≥3 个数据表格

#### Sub-Agent C: Assumption Propagation Analyst

**解决**: G002(001), G004(001), G008(001), G009/G010(003)

```
你是一个假设传播分析专家。你的任务是：
1. 从 PCG 获取论文所有关键假设（model assumptions, identification assumptions）
2. 对每个假设追问："如果偏离 10-50%，哪些结论的数值会变？变多少？"
3. 检查：理论模型的设定 vs 实际样本描述性统计是否匹配
4. 检查：关键假设是否有正式检验（统计检验或 sensitivity analysis）

重点关注：
- 归一化假设（如设某参数=1）→ 政策数字如何随之变化
- 代理变量假设 → construct validity 证据
- 小样本 → 大理论的外推
- 模型人数/结构 vs 数据人数/结构的张力

输出：每个有风险的假设 → 影响范围 + 论文是否已做 sensitivity analysis
```

**触发条件**：paper_type 为 empirical_econ 且有 calibration/estimation section

#### Sub-Agent D: Methodology Checklist Reviewer

**解决**: G007(001), G009(001), G006(003), G007(003)

```
你是一个经济学方法论审稿专家。对照以下 checklist 逐项检查：

【因果推断论文】
□ 平行趋势假设：有 event study / placebo test / formal pre-trend test？
□ 多重比较：>5 个 outcome → 是否有 Bonferroni/FDR/Romano-Wolf 校正？
□ 弱工具变量：first-stage F-stat 是否报告且 >10？
□ 过度识别：GMM 是否报告 J-test？

【校准/结构模型论文】
□ 关键参数是否有 sensitivity/robustness analysis？
□ 校准方法论：fit quality 度量？替代参数值尝试？
□ 数值方法透明度：网格大小、收敛判据、步长、初始值
□ 外部验证：模型预测 vs 样本外数据

每项检查的输出格式：
- ✅ 满足 → 论文位置引用
- ❌ 缺失 → 严重程度 + 在全文搜索过但未找到的证据
- ⚠️ 部分满足 → 解释缺少什么
```

**触发条件**：所有 empirical/theoretical 论文

#### Sub-Agent 编排时机

在 DEEP_REVIEW 阶段的 **50% 轮次处** 统一 spawn 所有适用的 Sub-Agents：
- 此时 main agent 已对论文有基本理解，PCG 有初步骨架
- Sub-agents 可利用 PCG 信息聚焦
- 所有 sub-agents **并行运行**（利用 `asyncio.gather`）
- 完成后 findings 注入 main agent 的 findings list
- Main agent 继续后半段 DEEP_REVIEW，可在 sub-agent findings 基础上深挖

---

### Layer 3: 对抗验证 Pass

在 DEEP_REVIEW → SYNTHESIS 转换时，spawn **Devil's Advocate sub-agent**：

```
你是论文的辩护律师。对于每个 finding：
1. 在论文全文中搜索：作者是否已经讨论/回应了这个问题
2. 在学术规范中判断：这个"问题"在该领域是否是标准做法
3. 对 finding 的证据进行质疑：证据是否充分支持结论

对每个 finding 输出裁决：
- CONFIRM: 找不到反驳，finding 成立
- WEAKEN: 找到部分回应，建议降 severity
- REJECT: 论文已充分回应 或 属于领域规范，建议删除
```

---

## 四、两个未命中 P0 目标的具体修复

### P0-残留-1: G005(001) — 跨表数据重复

**当前问题**：ConsistencyValidator Rule 9 只做单表内检验。

**修复方案**：

**方案 A（规则方法）**：新增 ConsistencyValidator Rule 10
```python
class Rule10_CrossTableDuplication:
    """检测不同表格间的异常数据重复"""
    
    def check(self, tables: list[ParsedTable]) -> list[Finding]:
        findings = []
        for i, t1 in enumerate(tables):
            for j, t2 in enumerate(tables):
                if j <= i:
                    continue
                overlap = compute_cell_overlap(t1.data_matrix, t2.data_matrix)
                if overlap > 0.9:  # 90% 以上的数值单元格相同
                    findings.append(Finding(
                        description=f"Table {t1.label} 与 Table {t2.label} 数据重叠率 {overlap:.0%}",
                        severity="medium",
                        evidence=f"共 {t1.cell_count} 个数值单元格中 {int(overlap*t1.cell_count)} 个完全一致"
                    ))
        return findings
```

**方案 B（Sub-Agent）**：Sub-Agent B 做 LLM-based 的深度检查

**推荐**：A + B 结合。Rule 10 做快速筛选，Sub-Agent B 做深度确认。

### P0-残留-2: G005(003) — 公式排版错误

**当前问题**：AppendixMathAuditSkill 检查推导逻辑，不做逐公式下标审计。

**修复方案**：

**方案 A（扩展 Skill）**：新增 SYMBOL_CONSISTENCY 模式
```python
class AppendixMathAuditSkill:
    modes = ["DERIVATION_LOGIC", "SYMBOL_CONSISTENCY"]  # 新增模式
    
    def run_symbol_consistency(self, equations: list[Equation]) -> list[Finding]:
        """
        1. 构建 symbol_table: {变量名: [(位置, 上下文, 下标)]}
        2. 对每个变量检查：下标在推导链中是否保持一致
        3. 检测 anomaly：如 θ₁ 突然变成 θ₂ 但语义应该相同
        """
        symbol_table = self._build_symbol_table(equations)
        anomalies = self._detect_subscript_anomalies(symbol_table)
        return [self._anomaly_to_finding(a) for a in anomalies]
```

**方案 B（Sub-Agent）**：Sub-Agent A (Formula & Symbol Auditor)

**推荐**：A + B 结合。Skill 模式做结构化提取，Sub-Agent 做语义判断。

---

## 五、实现优先级排序

```
Sprint 1（最高 ROI，规则基础，确定性高）：
  1. ReviewChecklist 数据结构 + paper_type → checklist 映射
  2. ConsistencyValidator Rule 10 (跨表重复检测)
  3. AppendixMathAuditSkill SYMBOL_CONSISTENCY mode
  4. Completion Gate 扩展：检查 checklist 覆盖度
  5. Phase 转换门控：unchecked 维度 → nudge

Sprint 2（中等 ROI，需要设计 sub-agent prompt）：
  6. Sub-Agent D: Methodology Checklist Reviewer (prompt + 触发逻辑)
  7. Sub-Agent C: Assumption Propagation Analyst (prompt + 触发逻辑)
  8. DEEP_REVIEW 中期自动 spawn 编排逻辑

Sprint 3（渐进式，需要调优）：
  9. Sub-Agent A: Formula & Symbol Auditor
  10. Sub-Agent B: Cross-Table Data Validator
  11. Layer 3: Devil's Advocate sub-agent

Sprint 4（评估方法升级）：
  12. LLM-as-Judge 替代 Jaccard matching
  13. 多次运行取均值的自动化脚本
  14. 回归测试：确保新机制不退化已有能力
```

---

## 六、预期效果

| 机制 | 解决的 Gold IDs | 预期 Recall 提升 |
|------|---------------|-----------------|
| Cross-Table Rule 10 | G005(001) | +5.3% |
| Symbol Consistency Mode | G005(003), G004(003) | +10.5% |
| Methodology Checklist Agent | G007(001), G009(001), G006(003), G007(003) | +21.1% |
| Assumption Propagation Agent | G002(001), G008(001), G009/G010(003) | +15.8% |
| Literature Verification | G008(003) | +5.3% |
| Coverage Gate (减少随机丢失) | G004(001) | +5.3% |

**当前 Recall**: 31.6% (6/19)
**理论最大 Recall**: 94.9% (18/19, G004-001 随机性难完全控制)
**现实预期 Recall**: 55-65% (Sprint 1+2 完成后)
**目标 F1**: 55-65%

---

## 七、风险与缓解

| 风险 | 影响 | 缓解策略 |
|------|------|----------|
| Token 消耗增加 2-3x | API 成本 | Sub-agents 限制 8 轮 + 精简 tools |
| 运行时间增加到 5-8min | 用户体验 | 并行 spawn 而非串行 |
| 更多 FP | Precision 下降 | Layer 3 对抗验证 |
| Checklist 过长致应付式 | 低质量覆盖 | 分 priority，只强制 high-priority 项 |
| Sub-agent 与 main agent 重复 | 冗余 findings | 已有去重机制 `check_finding_overlap` |

---

## 八、与现有架构的兼容性

所有方案均利用 ScholarAgent V2 **已有**的机制：
- `__PARALLEL_SPAWN__` → Sub-Agents
- `SignalDispatcher` → Checklist nudge 信号
- `PhaseFSM.request_transition()` → Phase 门控
- `FindingQualityGate` → Completion Gate 扩展
- `SkillExecutor` → 新 Skill 模式
- `ConsistencyValidator` → 新 Rule
- `check_finding_overlap` → Sub-agent findings 去重

不需要改变核心循环架构，只是在已有接入点上叠加新功能。
