# ScholarAgent 升级计划（终版 v3）

> **核心原则**: 每一项改动都必须回答"这如何让 Agent 审稿审得更好？"
> **设计哲学**: Agent = cognition, not orchestration. Constrain, don't control.
> **编制依据**: UPGRADE_PLAN_DRAFT.md（v2）+ REFERENCE_ANALYSIS.md + ARCHITECTURE_V2_BLUEPRINT.md + CLAUDE.md + 三重审视（Serious Mode / Fundamental Thinking / Rational Skepticism）
> **日期**: 2025-07
> **版本**: v3.0 — 两份前序文档的合并、修正与最终裁决

---

## 为什么需要第三版

UPGRADE_PLAN_DRAFT（v2）回答的是"下 4 周具体写什么代码"——方案详尽、代码示例完整、实现路径清晰。REFERENCE_ANALYSIS 回答的是"从行业最佳实践看，什么方向最有价值"——视野更广、优先级判断更有理论支撑。

两者的关键分歧在三个点：

1. **失败驱动规则生成**：v2 放在 P2-E1（"防止已知错误复发，有价值但不紧迫"），REFERENCE 放在 P0-A1（"核心学习闭环"）。
2. **Decision Audit Trail**：v2 放在 X2（"维护者导向，顺手做"），REFERENCE 放在 P0-A2（"决策可追溯性基础设施"）。
3. **预索引（B1）vs Session Memory（M1）哪个先做**：v2 认为 B1 先（零 LLM 成本），本版经过假设检验后认为 M1 先（价值密度更高）。

本版的裁决原则：以 v2 的实现方案为主体骨架，以 REFERENCE 的优先级洞见修正排序，补充被 v2 遗漏的低成本高价值项。

---

## 执行优先级总览

```
P0 — 直接提升审稿认知深度 + 建立学习闭环基础
├── M1: Session Memory Manager（LLM 子任务版）         [3-4天]
├── B1: 论文结构预索引（Paper Mental Model）           [2-3天]
├── E0: 失败驱动规则生成机制（从 REFERENCE A1 提升）   [半天]
└── A0: 规则容量管理（CLAUDE.md 200行硬约束）         [2小时]

P1 — 让已有能力真正生效
├── M2: Smart Compaction 恢复质量升级                  [1-2天]
├── R1: Procedural Memory 回注（跨会话学习闭环）      [2天]
├── H1: HD-WM 假说可见性                             [半天]
└── Q1: Finding 质量自评                             [1-2天]

P2 — 认知策略扩展
├── S1: Paper-Type 自适应认知策略                     [1天]
├── K1: 审稿认知图谱输出                             [1-2天]
└── B4: 认知循环动态调优（Completion Gate 自适应）    [1-2天]

X — 工程维护（不阻塞认知改进时顺手做）
├── X1: 目录结构迁移（仅在需要加新文件时触发）
└── X2: harness.py 决策注释（用固定五字段格式，随改随加）
```

---

## 与 v2 的关键差异说明

### 差异 1：E0 从 P2 提升到 P0

**v2 的判断**："失败模式规则提炼帮助的是防止 Agent 犯已知错误——有价值但不如新能力紧迫"，放在 P2-E1。

**本版修正**：这不只是"防御"。ScholarAgent 要走向跨任务自我进化（远期 C1），失败模式的结构化积累是**前置条件**。更重要的是成本极低（半天），收益却是让每次 bug 修复都升级认知约束，形成"每次犯错都变强"的闭环。Karpathy 规则在 30 个代码库上的实测：错误率从 41% 降到 3% 以下——同样的方法论在审稿领域没理由不生效。

**实现**：从 PROGRESS.md 的 Phase 历史中提炼审稿特有的失败模式规则。格式："当 X 情况发生时，Agent 曾经犯 Y 错误，正确做法是 Z"。不是抽象原则，是对观察到的失败模式的闭环回应。

### 差异 2：A0（规则容量管理）新增为 P0

**v2 未提及此项。**

**本版新增理由**：CLAUDE.md 当前 141 行。随着 E0 不断新增规则，如果不设硬约束，很快会超过 200 行——Karpathy 社区实测：超过此长度规则间冲突加剧，agent 对单条规则遵守率下降。这是一个 10 分钟的操作（在 CLAUDE.md 顶部加一行容量声明），但防止的是未来规则集膨胀失控。

### 差异 3：执行顺序调整

**v2 排序**：B1→M1→M2→R1→H1→Q1→S1→K1

**本版排序**：M1→B1→E0+A0→M2→R1→H1→Q1→S1→K1→B4

**理由**：
- M1 先于 B1：Session Memory 产出的笔记是 M2（Compaction 恢复）的前置依赖。如果先做 B1 再做 M1，M2 的实现会被阻塞更久。而且 M1 的验证标准更明确——"Compaction 恢复后 Agent 是否保持判断连贯性"是可测试的。
- E0+A0 穿插在 M1 和 B1 之间或之后：因为极低成本（合计不到 1 天），可以在任何工作间隙完成。
- B4 新增在 P2 末尾：REFERENCE_ANALYSIS 的 B4（Completion Gate 动态调优）是对 v2 的 S1 的自然延伸——S1 给静态策略表，B4 让阈值随经验积累而进化。

### 差异 4：Decision Audit 不作独立任务

**v2 放在 X2（顺手做）**。**REFERENCE 放在 P0-A2**。

**本版裁决**：折中。不作独立任务排期（v2 对此的批评正确——"帮维护者不帮 Agent"），但在每次修改 harness.py 时必须用五字段格式注释关键分支。这是"做 P0/P1 时的执行规范"，不是独立 task。

---

## P0：直接提升审稿认知深度 + 建立学习闭环

### M1. Session Memory Manager — LLM 子任务版

（方案完全沿用 v2，此处不重复代码。见 UPGRADE_PLAN_DRAFT.md 第 62-205 行。）

#### 核心设计要点

- 9 段结构化认知笔记（task_summary, current_focus, methodology_assessment, evidence_quality, novelty_judgment, statistical_observations, writing_quality, key_decisions, issue_timeline）
- 在"认知断点"触发更新：读完核心 section / 新增 major finding / 阶段转换 / 距上次更新 3+ 轮
- 每次更新 ~1100 tokens，一次审稿 5000-7000 额外 tokens（5-10% 成本换认知判断保留）
- 注入时机：仅在 Smart Compaction 恢复时使用，不在正常 context 中占空间

#### 验证标准

- 对同一篇论文触发 Compaction 后，Agent 的后续 findings 是否与压缩前保持逻辑连贯？
- 恢复后是否出现"重复探索已有结论覆盖的方向"？

#### 预计工作量: 3-4 天

---

### B1. 论文结构预索引 — Paper Mental Model

（方案完全沿用 v2，代码见 UPGRADE_PLAN_DRAFT.md 第 280-452 行。）

#### 核心设计要点

- PaperStructureIndex：sections + word_counts + cross_references + evidence_map + dependency_pairs + paper_type
- PaperIndexBuilder：纯正则解析，< 1秒完成，零 LLM 成本
- 注入方式：INITIAL_SCAN 阶段完整注入（~800 tokens），DEEP_REVIEW 阶段只注入相关子集
- 措辞：始终"参考"而非"事实"（认知辅助模式）

#### ⚠️ Rational Skepticism 增补（来自 REFERENCE D1）

论文论证结构不像代码结构那样确定性。正则提取的交叉引用可能有误（如文字恰好包含"Section 3"但不是真正引用）。预索引产出应标记为"初始解析，可能有噪音"，Agent 审稿中发现与实际不符时应能覆盖。

**具体措施**：format_for_context() 的头部加一句"[以下为自动解析结果，仅供导航参考，可能存在噪音]"。

#### 验证标准

- Agent 在有预索引 vs 无预索引时，是否做出更优的阅读顺序决策？
- 预索引的准确率（抽 5 篇论文人工对照）达到 85%+ ？

#### 预计工作量: 2-3 天

---

### E0. 失败驱动规则生成机制

#### 来源

REFERENCE_ANALYSIS A1（Karpathy 规则方法论），从 v2 的 P2-E1 提升。

#### 为什么是 P0

成本极低（半天），但建立的是"系统从错误中学习"的闭环。Karpathy 的核心洞见：**规则应该是对观察到的失败模式的闭环回应，而非抽象设计原则**。ScholarAgent 的 CLAUDE.md 当前规则主要来自架构设计推理，缺乏从实际审稿错误中提炼的经验规则。

#### 实现方案

**Step 1**：回溯 PROGRESS.md 的 Phase 1-27 历史，提取所有"Agent 犯了 X 错误"的记录。

**Step 2**：将重复出现 ≥2 次的失败模式转化为 CLAUDE.md 规则。格式：

```markdown
## 从审稿实践中提炼的认知约束

- [Phase N 教训] 当 {触发条件} 时，不要 {错误行为}，而应 {正确行为}
  例: 当 Agent 发现统计问题时，不要直接产出 finding，而应先检查该统计方法在该领域是否为标准实践
```

**Step 3**：建立持续积累机制——每次新 Phase 的 bug 记录末尾追加"应转化为 CLAUDE.md 规则？是/否"字段。某类 bug 重复 ≥2 次则触发规则化。

#### ScholarAgent 特殊适配

Karpathy 的"先读文件再改"在审稿语境下的等价规则示例：
- "Agent 产出 finding 前必须确认 findings_store 中不存在语义重复的条目"——对应 Phase 12 overlap bug
- "永远不与 LLM 行为经济学对抗——如需额外认知行为，在已有最短路径上自动增强"——对应多次 nudge 设计失败

#### 预计工作量: 半天（含 PROGRESS.md 回溯）

---

### A0. 规则容量管理

#### 来源

REFERENCE_ANALYSIS A3（Karpathy 200 行限制 + 社区规则衰减实践）。v2 未提及。

#### 实现

在 CLAUDE.md 顶部新增容量声明：

```markdown
## 容量约束
本文件硬限制 200 行。新增规则时必须审查是否有可降级/移除的旧规则。
过去 5 个 Phase 未被触发的规则标记为候选移除项。
```

引入三级规则分层：
- **L0（绝对约束）**：设计红线，永不移除（当前 §2.1/§3.1/§4.3/§5.1）
- **L1（强烈建议）**：经验规则，可被新证据覆盖
- **L2（偏好）**：风格性建议，容量紧张时优先移除

#### 预计工作量: 2 小时

---

## P1：让已有能力真正生效

### M2. Smart Compaction 恢复质量升级

（方案沿用 v2 第 209-277 行。分层恢复：Findings → Session Memory 笔记 → HD-WM 假说 → 论文结构索引 → 进度信息，总预算 6000 tokens。）

**前置依赖**: M1 完成后才有 Session Memory 内容可注入。

#### 预计工作量: 1-2 天（M1 完成后）

---

### R1. Procedural Memory 回注 — 跨会话学习闭环

（方案沿用 v2 第 458-536 行。ProceduralMemoryRecaller 基于论文类型+主题召回相关历史策略，注入为参考信息。）

#### 本版增补

会话结束时的 pattern 提取逻辑需要与 E0 机制协同——E0 提取"失败模式规则"写入 CLAUDE.md，R1 提取"成功策略模式"写入 ProceduralPattern。两者互补：一个说"别做什么"，一个说"可以试什么"。

#### ⚠️ "成功策略"的定义标准（来自 REFERENCE D1 警告）

学术审稿缺乏标量 reward signal——同一篇论文两位审稿人可能给截然相反意见。R1 回注的策略如果基于错误的"成功"判断，会引入噪音。

初期方案：由用户在审稿结束后手动标注"本次审稿质量"（简单 1-5 分），仅 ≥4 分的审稿中提取的 pattern 进入 ProceduralPattern 存储。后期（积累 30+ 条数据后）再探索基于 Finding 结构完整性的自动化质量估算。

#### 预计工作量: 2 天

---

### H1. HD-WM 假说可见性

（方案沿用 v2 第 539-577 行。在 Context Assembler 中新增 hypothesis visibility section，让 Agent"看到"但不被"命令追查"。）

**核心措辞**："[当前审稿假说 — 你的待验证猜想] ... [这些假说由你的过往观察自动生成。你可以追查、修正或忽略它们。]"

#### 预计工作量: 半天

---

### Q1. Finding 质量自评

（方案沿用 v2 第 581-668 行。FindingQualityGate 在 mark_complete 前做规则基础的结构检查：has_evidence / is_actionable / is_specific / severity_justified。产出 nudge 而非阻止退出。）

#### 与 REFERENCE B2 的融合

REFERENCE B2 的 Agent-as-a-Judge 思路更激进（用 LLM 评判 findings 质量）。本版保持 v2 的保守方案（规则检查，零 LLM 成本），但预留接口——当积累 20+ 次审稿数据后，可升级为 LLM-based quality scoring。

#### 预计工作量: 1-2 天

---

## P2：认知策略扩展

### S1. Paper-Type 自适应认知策略

（方案沿用 v2 第 671-729 行。静态策略表 PAPER_TYPE_COGNITIVE_HINTS，按论文类型注入不同关注点提示。）

#### 预计工作量: 1 天

---

### K1. 审稿认知图谱输出

（方案沿用 v2 第 733-776 行。ReviewCognitionGraph 结构化产出，构建时机为 mark_complete 成功时，数据全来自已有状态。）

#### 与远期 C1 的关系

K1 输出的认知图谱是未来"跨任务自我进化"（C1）的关键数据源。积累足够图谱后可以分析"什么类型论文 Agent 审得好/差"，指导策略进化。

#### 预计工作量: 1-2 天

---

### B4. 认知循环动态调优

#### 来源

REFERENCE_ANALYSIS B4（GPTSwarm 图优化 + SE-Agent 轨迹进化），v2 中隐含在 S1 但未独立提出。

#### 问题

Completion Gate 的触发阈值（连续 N 轮无新 finding）是固定的。S1 给出了按论文类型区分的静态表（empirical=3, theoretical=5, review=4），但最优阈值应该随经验积累而调整。

#### 实现方案

短期（P2 阶段）：用 S1 的静态配置即可。

长期准备：每次审稿完成时记录 {paper_type, actual_idle_rounds_before_exit, findings_count, review_quality_score}。当积累 20+ 条数据后，用简单统计（均值 ± 1σ）替代人工设定的阈值。

#### 预计工作量: 1-2 天

---

## X：工程维护

### X1. 目录结构迁移

仅当 P0/P1 新增文件使平面结构难以管理时再做。当前 24 个文件仍可管理。

### X2. harness.py 决策注释

在做 P0/P1 修改 harness.py 时，用五字段格式记录关键决策分支：

```python
# DECISION: [简述决策]
# WHY: [为什么这样做]
# ALTERNATIVE REJECTED: [被拒绝的方案]
# TRADEOFF: [权衡取舍]
# OPEN: [遗留问题]
```

这不是独立任务，而是修改 harness.py 时的执行规范。

---

## 执行时间表

```
Week 1:    M1 (Session Memory LLM版) ← 核心中的核心
Week 1-2:  B1 (预索引，可与 M1 并行) + E0 (半天) + A0 (2h)
Week 2:    M2 (Compaction 增强，依赖 M1)
Week 2-3:  R1 (Procedural 回注)
Week 3:    H1 (假说可见性，半天) + Q1 (Finding 自评)
Week 3-4:  S1 (Paper-Type 策略) + K1 (认知图谱) + B4 (动态调优)
随时可做:   X2 (每次改 harness.py 时顺手加注释)
```

### 为什么这个顺序

1. **M1 绝对最先**：它产出的认知笔记是 M2（Compaction 恢复）的前置原料。而且它解决的是"审稿中断后认知断裂"这个直接影响输出质量的问题。
2. **B1 可与 M1 并行**：纯正则实现，不依赖 M1 的任何产出。两者独立开发、独立测试。
3. **E0 和 A0 穿插完成**：极低成本（合计不到 1 天），建立规则健康管理的基础设施。
4. **M2 紧跟 M1**：Session Memory 完成后，增强 Compaction 恢复只需 1-2 天——集成点清晰。
5. **R1 在 M1/M2 之后**：做 M1 时会深入理解 memory.py 数据结构，R1 自然衔接。
6. **H1 + Q1 小而高价值**：半天到一天的改动，直接影响 Agent 的审稿意识和输出质量。

### 时间估算说明

以上各项工期为**纯开发估算**，未计入测试编写、联调调试、CLAUDE.md 小结撰写的时间。建议实际排期按 1.3-1.5 倍计算。P0+P1 实际需 12-17 个工作日。4 周（20 个工作日）完成 P0+P1 是可行的，但 P2 如果有延期，仅保证 S1（1天）能在 4 周内完成，K1 和 B4 可能溢出到第 5 周。这是可接受的——P2 本身优先级较低。

---

## 远期方向（不排进 4 周计划，但指明路径）

### C1. 跨审稿任务的自我进化

来自 REFERENCE_ANALYSIS C1。核心问题：Agent 审了 100 篇论文后，是否比审第 1 篇时更好？

当前答案是"否"。要走向"是"，需要：
- E0 的失败模式积累（已做）→ 规则集不断进化
- R1 的 ProceduralPattern 积累（已做）→ 策略库不断丰富
- K1 的认知图谱积累（已做）→ 可分析 Agent 表现趋势
- 最终：引入 Hermes Agent 的 Skill Distillation / SE-Agent 的轨迹进化机制

**学术贡献定位**："通过审稿实践自我进化的学术审稿 Agent"——Self-Evolving Agent 文献中缺乏的垂直领域验证。

### C2. 认知约束的理论框架

ScholarAgent 的 COGNITIVE_ANCHOR.md 和"constrain, don't control"哲学可形式化为可量化实验。Phase 1-27 已积累数据。如果能系统回答"哪些约束对 Agent 行为改善最大"、"约束集合的最优规模"，就是一篇 Agent Cognitive Constraints Engineering 论文。

### C3. Gödel Agent / HyperAgents 验证

在学术审稿领域验证递归自改进范式的可行性（或不可行性）本身有发表价值。审稿的主观性是否构成 Self-Improving 的根本障碍？如何在缺乏标量 reward signal 的领域定义"改进"？

---

## 不变的约束（每次实施前自检）

1. **Agent = cognition, not orchestration** — 所有新增都是认知增强，不是流程管道
2. **Constrain, don't control** — 所有注入信息都是"参考"措辞，Agent 有最终决策权
3. **LLM 是无状态 CPU** — 不依赖 LLM 记住跨轮信息，状态由外部系统维护
4. **增量验证** — 每个改动后跑 469+ tests
5. **在已有路径上增强** — 优先利用现有机制（如 Assembler section 注入），不发明新管道
6. **CLAUDE.md 200 行硬限制** — 新增规则必须审查是否有可移除的旧规则
7. **五字段决策注释** — 修改 harness.py 关键分支时必须记录

---

## 验证标准（每完成一项后执行）

1. "Agent 审同一篇论文，有这个功能 vs 没有，输出质量有**可观察**的差异吗？"
2. "这个功能在 5 次不同论文的审稿中都能发挥作用，还是只在特定情况下有用？"
3. "COGNITIVE_ANCHOR §9 的自检问题都通过了吗？"
4. "ARCHITECTURE_V2_BLUEPRINT §0 的正反对照诊断没有命中任何红线？"

---

## 与两份前序文档的关系

| 来源 | 本计划如何使用它 |
|------|----------------|
| UPGRADE_PLAN_DRAFT (v2) | **主体骨架**。M1/B1/M2/R1/H1/Q1/S1/K1 的设计方案和代码示例完全沿用。 |
| REFERENCE_ANALYSIS | **优先级修正器 + 补充项**。E0 和 A0 从中提取并提升优先级；B4 从中新增；D 部分的 Rational Skepticism 警告整合进各方案。 |
| ARCHITECTURE_V2_BLUEPRINT | **合规性检查基准**。每项实现必须通过 §0 自检清单。 |
| CLAUDE.md | **红线来源**。四条设计红线是不可违反的硬约束。 |
| COGNITIVE_ANCHOR.md | **根本性参照**。§4.3（认知辅助模式）和 §9（自检）是所有措辞设计的依据。 |

---

## 对后续执行者的说明

这份计划是**指导性**的而非**指令性**的（实践 constrain, don't control 原则——对执行者也适用）。

如果你在实施某项时发现"实际代码状态与计划假设不符"，正确做法是：
1. 记录偏离（用偏离记录模板）
2. 评估是计划需要更新还是代码需要调整
3. 如果计划有误，更新本文档对应部分

如果你在实施某项时感觉"这只是在让代码更好看"——停下来，回到 COGNITIVE_ANCHOR §9 自检。

如果你完成了一项但无法通过验证标准——不要标记为"完成"，记录为"已实现但效果待验证"，并说明缺少什么数据来验证。

---

> **终极自检**: 四周后，一个从未见过这个项目的人类审稿专家用 ScholarAgent 审一篇真实论文。他/她是否会说"这个 Agent 的判断力比普通 reviewer 强"？如果所有 P0/P1 完成后答案仍然是"否"——那不是计划的问题，是我们对"认知增强"的理解还不够深。
