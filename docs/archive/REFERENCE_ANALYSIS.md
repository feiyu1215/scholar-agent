# 参考项目深度分析与 ScholarAgent 借鉴方案

> **版本**: v1.0 | **日期**: 2025-07
> **定位**: ScholarAgent 项目的中间态参考文档，后续计划延伸的基础
> **方法论**: Serious Mode + Fundamental Thinking + Rational Skepticism 三重审视

---

## 第一部分：参考项目核心创新分析

---

### 一、Karpathy 的 CLAUDE.md 规则

#### 1.1 背景与起源

CLAUDE.md 是放置在项目根目录的 Markdown 文件，当启动 Claude Code 时会被自动读取，作为该会话的持久行为上下文。本质上是一个开发者可控制的持久系统提示。

Andrej Karpathy（前特斯拉 AI 总监、OpenAI 创始成员）于 2026 年 1 月在 X 上发布了一组推文，分享使用 Claude Code 进行数周高强度编程后遇到的失败模式。开发者 Forrest Chang 将其提炼为 4 条行为规则发布到 GitHub，第一天获得 5,828 个 Star，截至 2026 年 5 月超过 120,000 个 Star。

#### 1.2 三大 LLM 编程陷阱

| 问题类别 | 描述 | 典型表现 |
|---------|------|----------|
| Silent Assumptions（默默假设） | 模型选择一种解释就一路跑，不检查不质疑 | 错误假设、隐藏困惑、缺少权衡展示 |
| Over-abstraction（过度抽象） | 模型倾向于过度复杂化 API | 100 行能搞定的事写成 1000 行 |
| Collateral Damage（附带伤害） | 模型修改或删除自己不理解的代码/注释 | 顺手重构、风格漂移、与任务无关的修改 |

#### 1.3 四大核心规则

**Rule 1: Think Before Coding** — 不要假设。不要隐藏困惑。呈现权衡。强制 AI 在写代码前明确陈述假设，遇到不确定时必须提问。

**Rule 2: Simplicity First** — 用最少的代码解决问题。禁止为单次使用创建抽象层，禁止没被要求的"灵活性"。

**Rule 3: Surgical Changes** — 只动必须动的。每一行改动都必须直接追溯到用户请求。禁止 drive-by refactoring。

**Rule 4: Goal-Driven Execution** — 将命令式任务转化为声明式、可验证的目标。Step → Verify 循环。

#### 1.4 核心方法论洞见

**规则应该是对观察到的失败模式的闭环回应**。Karpathy 的规则不是抽象设计原则，每一条都对应实际使用中多次遇到的 agent 失败模式。这是"事故驱动的规则生成"范式。

社区测试：30 个代码库上的 6 周测试，错误率从 41% 降到 3% 以下。后续 4 条扩展为 12 条，覆盖 Agent 编排、Token 预算、冲突处理、检查点等。

**关于规则容量**：CLAUDE.md 应控制在 200 行以内。超过此长度，规则间产生冲突，agent 对单条规则的遵守率下降。这是一个容量-遵守率的权衡曲线。

#### 1.5 更广泛的 AI 编程哲学

Karpathy 2026 年提出的多层次工作流：
- 第一层（75%）：Tab 补全——代码本身就是最好的提示
- 第二层：针对性代码修改（高亮代码块）
- 第三层：Claude Code 处理重大功能
- 第四层：GPT-5 Pro 处理最难的问题

核心观点：Vibe Coding 抬高编程下限（让所有人都能写软件），Agentic Engineering 保住天花板（不牺牲质量的前提下用 Agent 加速）。人类不必再记住每个 API 细节，但必须理解系统结构、底层机制和质量标准。

#### 1.6 社区扩展实践

**分层规则体系**：将规则分为"绝对不可违反"、"强烈建议"、"偏好"三级。给 agent 在规则冲突时提供优先级判断依据。

**场景触发规则**：某些规则只在特定条件下活跃，减少 agent 的认知负载。

**规则衰减机制**：连续 N 次未被触发的规则考虑降级或移除，防止规则集无限膨胀。

#### 1.7 资源

| 资源 | 链接 |
|------|------|
| 主仓库 | https://github.com/forrestchang/andrej-karpathy-skills |
| DeepWiki 文档 | https://deepwiki.com/forrestchang/andrej-karpathy-skills |

---

### 二、Implementation-Notes 方法论

#### 2.1 背景与起源

来自 Thariq Shihipar（Anthropic 的 Claude Code 团队工程师），2026 年 5 月分享。

#### 2.2 核心 Prompt

```
"implement <SPEC> and while you do, keep a running implementation-notes.html file
(or markdown) with decisions you had to make that weren't in the spec, things you
had to change, tradeoffs you had to make or anything else I should know"
```

#### 2.3 核心理念

让 AI 在实现 spec 的同时，维护一个记录文件，捕获设计的"灰色地带"。它不是"写完代码后补文档"，也不是"先写文档后编码"，而是**编码与决策记录同步进行**。

#### 2.4 记录的四类内容

**设计决策 (Design Decisions)**：选择了 A 而非 B 方案，及选择理由。例如"选择哈希表而非排序数组，因为查找频率远高于插入频率"。

**偏离 (Deviations)**：发现 spec 不可行或矛盾，主动做出偏离并记录原因和影响。

**权衡 (Tradeoffs)**：意识到当前选择牺牲了某些属性以换取另一些，显式记录交换关系。

**开放问题 (Open Questions)**：需要人类决策者介入的问题点，先记录继续前进。

#### 2.5 认知学贡献

implementation-notes 本质上是 agent 的**决策元认知日志**。不是记录"做了什么"（那是 git log），而是记录"为什么这样做而不那样做"。使用 HTML 格式支持折叠区域、内链跳转和可视化时间线——为 agent 的未来自己设计的文档格式。

#### 2.6 应用价值

- PR review 补充信息
- Hallucination 检测（AI 编造的逻辑通过 notes 中的自圆其说暴露）
- Debugging 回溯——出问题时看 AI 当时的决策链
- 与 Karpathy "Think Before Coding" 一脉相承，但侧重实现过程中的自省

#### 2.7 相关框架

| 名称 | 说明 |
|------|------|
| Spec-Driven Development (SDD) | 先写规格再写代码的开发范式 |
| RPI (Research → Plan → Implement) | 三阶段 gate-based 开发框架 |
| OpenSpec | proposal → review → apply → archive 工作流管理 |
| AWS Kiro / GitHub Spec Kit | 商业化的 Spec-First 开发工具 |

---

### 三、Self-Improving Agents（自改进智能体）

#### 3.1 概念定义

Self-Improving Agents 指能在运行过程中自主评估自身表现，并通过修改自身代码、提示词、策略或知识库来持续提升性能的 AI 系统。具有元认知能力——不仅执行任务，还能"回顾"执行过程并从中学习。

Recursive Self-Improvement（递归自改进）更进一步：agent 不仅改进任务执行能力，还能改进"改进机制本身"。

#### 3.2 技术演进层次

| 层级 | 名称 | 特征 | 代表 |
|------|------|------|------|
| Level 0 | 静态 Agent | 固定提示和工具，无学习能力 | 传统 ReAct/CoT agent |
| Level 1 | 反思型 Agent | 单次任务内通过反馈循环改进输出 | Reflection/Reflexion |
| Level 2 | 跨会话学习 Agent | 通过持久记忆和技能积累跨会话改进 | Hermes Agent |
| Level 3 | 自编辑 Agent | 直接修改自身代码/架构来提升性能 | SICA、Gödel Agent |
| Level 4 | 递归自改进 Agent | 改进"改进机制本身" | HyperAgents |

#### 3.3 核心论文

**Gödel Agent** (ACL 2025): 受 Gödel 机器启发，允许 agent 在无预定义优化算法的情况下递归改进自身。Agent 具备自我感知（读取自身状态）和自我修改（动态调整逻辑和行为）能力。arXiv: 2410.04444

**HyperAgents** (Meta FAIR, 2025): 提出"自指性智能体"——task agent 和 meta agent 整合为一个可编辑程序。关键突破：元修改机制本身也是可修改的，打破"固定元层"局限。在 4 个不同领域验证了泛化能力。arXiv: 2603.19461

**SICA** (布里斯托尔大学 & iGent AI): 证明配备基本编码工具的 agent 可自主编辑自身代码并提升性能。SWE-Bench Verified 上性能提升 17%-53%。arXiv: 2504.15228

**SE-Agent** (NeurIPS 2025, 阶跃星辰): 将推理轨迹视为可进化"物种"，通过 Revision、Recombination、Refinement 三大算子实现轨迹级进化。5 种主流 LLM 修复率提升 30%-112%。arXiv: 2508.02085

**Agent-as-a-Judge** (ICML 2025): 将 LLM-as-a-Judge 范式扩展到 agent 系统——用 agent 评估 agent，能在任务中间过程提供反馈。DevAI benchmark: 55 个真实 AI 开发任务，365 个分层需求标注。显著优于 LLM-as-a-Judge。arXiv: 2410.10934

#### 3.4 关键技术机制

| 机制 | 代表项目 | 原理 |
|------|----------|------|
| Reflection | SELF-REFINE, Reflexion | 审查自身输出，发现缺陷并迭代改进 |
| Reflexion | Shinn et al. 2023 | 自然语言反馈替代标量奖励 |
| Self-Play | arXiv 2512.02731 | 与自身副本对弈以发现弱点 |
| Trajectory Evolution | SE-Agent | 推理轨迹当种群，遗传式进化 |
| Self-Editing Code | SICA, Gödel Agent | 直接修改自己的源代码/工具定义 |
| Meta-Agent Recursion | HyperAgents | 改进"改进算法本身" |
| Skill Distillation | Hermes Agent | 成功经验自动转化为可复用 Skill 文档 |
| Genetic Optimization | Hermes Self-Evolution, SE-Agent | GEPA/遗传算法做帕累托最优搜索 |
| Agent-as-a-Judge | ICML 2025 | 用 agent 系统评估 agent 输出质量 |

#### 3.5 重要开源项目

**Hermes Agent** (NousResearch): MIT 开源，内置学习循环——任务完成后自动生成 Skill 文档，跨会话持久记忆。使用 DSPy + GEPA 自动进化技能、工具描述、系统提示和代码。支持 200+ LLM 模型。GitHub: https://github.com/NousResearch/hermes-agent

#### 3.6 四个自我进化维度（综合归纳）

**Model 层进化**：通过累积的 few-shot examples、failure patterns、preference data 微调 in-context 行为。从历史执行中自动提取"成功模式"。

**Memory 层进化**：记忆不是简单 KV 存储，而是结构化知识图谱。低价值记忆淘汰，高复用记忆提权，记忆间建立关联。

**Tool 层进化**：自主发现需要新工具、复合已有工具为宏工具、淘汰低效工具。GPTSwarm 将工具组合建模为图结构优化。

**Workflow 层进化**：执行策略可进化，根据任务类型和历史成功率动态调整执行顺序和深度。

---

### 四、CodeGraph — 预索引代码知识图谱

#### 4.1 核心问题

AI 编程 Agent 的瓶颈不在生成代码，而在理解代码。传统方式依赖"探索式扫描"——反复调用 grep、glob、Read 来扫描文件。一个架构问题可能需要 23 次工具调用、处理 140 万 token。图谱把 O(n) 暴力搜索变成 O(1) 精准定位。

#### 4.2 三个同名项目

**colbymchenry/codegraph（最主流，15-17K Stars）**: 面向 Claude Code、Cursor 等的预索引本地代码知识图谱。使用 tree-sitter 解析 AST，提取符号及关系，存入 SQLite（带 FTS5 全文检索），通过 MCP 暴露 8 个查询工具。

**codegraph-ai/CodeGraph（跨语言增强版）**: 支持 37 种编程语言，提供 28-45 个 MCP 工具，内置持久化记忆层，支持跨语言查询。

**HKUST-KnowComp/CodeGraph（学术论文）**: 一个 prompting framework，用代码编码图问题的解法。准确率从 63.3% 提升到 96.1%。arXiv: 2408.13863

#### 4.3 核心量化成果（colbymchenry 版）

基准测试（7 个真实开源项目，7 种语言）：
- 平均减少 **92%** 工具调用次数
- 速度提升 **71%**
- Token 消耗减少 **59%**
- 成本降低 **35%**
- Swift Compiler（25,874 文件、272,898 节点）索引耗时不到 4 分钟，Agent 用 6 次 explore 调用 + 0 次文件读取，35 秒内回答复杂跨切面问题

#### 4.4 技术架构

1. **离线索引**：tree-sitter 对代码库进行完整 AST 解析，提取所有符号定义、引用关系、继承链、接口实现
2. **图构建**：符号间关系建模为知识图谱。节点=函数/类/模块，边=调用/继承/导入关系，带位置信息和权重
3. **MCP 工具暴露**：`codegraph_search`、`codegraph_context`、`codegraph_callers`、`codegraph_callees`、`codegraph_impact`、`codegraph_node`、`codegraph_files`、`codegraph_status`

#### 4.5 设计哲学

"预计算所有可以预计算的东西，让 agent 的 runtime 只做真正需要推理的工作。" 这是计算前移策略——将确定性的结构分析从 LLM 推理时间中移除。

#### 4.6 相关工具

| 工具 | 特点 |
|------|------|
| LocAgent (ACL 2025) | 图引导的 LLM 代码定位框架 |
| Graphify | 知识图谱 + Leiden 社区检测，71.5x fewer tokens |
| CodePrism | 100% AI 生成的代码图分析工具 |
| graphify-ts | tree-sitter + graphology 代码导航层 |

#### 4.7 资源

| 资源 | 链接 |
|------|------|
| colbymchenry/codegraph | https://github.com/colbymchenry/codegraph |
| codegraph-ai/CodeGraph | https://github.com/codegraph-ai/CodeGraph |
| HKUST 论文 | https://arxiv.org/abs/2408.13863 |
| LocAgent | https://github.com/gersteinlab/LocAgent |

---

### 五、Understand-Anything — 多 Agent 深度理解流水线

#### 5.1 概述

开源的 AI 驱动代码理解工具，将任意代码库转化为可交互、可探索、可搜索、可问答的知识图谱。口号："Graphs that teach > Graphs that impress"。由 Georgia Tech 的 Yuxiang Lin 创建。8 天内从 0 涨到 5000 Stars，截至 2026 年 5 月 15K+ Stars。MIT 许可证。

#### 5.2 核心创新：5-Agent 多智能体流水线

1. **Project Scanner（项目扫描器）**：读取目录树，识别技术栈，创建初始文件清单
2. **File Analyzer（文件分析器）**：逐文件深度分析，提取函数、类、依赖等结构化信息
3. **Architecture Analyzer（架构分析器）**：识别模块间高层架构模式、分层结构和系统设计
4. **Tour Builder（导览构建器）**：生成"guided tours"——按逻辑顺序组织的代码浏览路径
5. **Graph Reviewer（图谱审核器）**：对知识图谱做质量审核，确保准确性和完整性

技术融合：确定性静态分析（tree-sitter）+ LLM 驱动的语义推理。前者确保结构精确性，后者提供人类可读的语义理解。

#### 5.3 与 CodeGraph 的互补关系

| 维度 | CodeGraph | Understand-Anything |
|------|-----------|---------------------|
| 理解深度 | 底层结构正确但语义缺失 | 语义丰富但依赖 LLM 推理 |
| 确定性 | 高（AST 解析是确定性的） | 中（LLM 判断可能不准确） |
| 产出形式 | 结构化图谱 + MCP 工具查询 | 交互式知识图谱 + 自然语言问答 |
| 最适受众 | AI Agent（精确定位） | 人类开发者 + AI Agent（全局理解） |
| 核心价值 | 消除重复探索（O(1) 查询） | 可持久化的结构化理解资产 |

核心独特价值：把"代码理解"从"一次性探索行为"变成"可持久化、可共享的结构化资产"，且输出物（知识图谱 JSON）天然适合作为 AI Agent 的 context input。

#### 5.4 资源

| 资源 | 链接 |
|------|------|
| GitHub | https://github.com/Lum1104/Understand-Anything |
| 官网 | https://understand-anything.com/ |

---

### 六、Presenton（开源 AI 演示文稿生成器）

#### 6.1 概述

完全开源的 AI PPT 生成工具，是 Gamma、Beautiful.ai 等的开源替代品。核心定位 Local-first，所有生成过程在用户设备完成。Apache 2.0 许可证。

#### 6.2 关键创新

- **BYOK（Bring Your Own Key）**：支持 OpenAI、Gemini、Claude、Ollama 本地模型
- **AI 模板生成**：从现有 PowerPoint 文档创建演示模板
- **自定义 HTML 模板**：完全自定义幻灯片设计
- **API 接口**：自动化批量生成

#### 6.3 资源

| 资源 | 链接 |
|------|------|
| GitHub | https://github.com/presenton/presenton |

---

### 七、NVIDIA LongLive（实时交互式长视频生成）

#### 7.1 概述

NVIDIA NVlabs 联合 MIT、港科大（广州）、港大、清华开发的开源实时交互式长视频生成框架。ICLR 2026 接收。

#### 7.2 关键技术创新

**Attention Sink**：自回归生成中保留关键帧注意力信息，维持长视频全局一致性。

**KV-Recache**：解决 KV Cache 无限增长问题，有限显存内支持超长视频。

**Streaming Long Tuning**：短视频模型高效微调为长视频模型。

**性能数据**：
- 1.0: 单张 H100 达 20.7 FPS，支持最长 240 秒视频，1.3B 参数
- 2.0: 推理 45.7 FPS，5B 参数变体，训练加速 2.15 倍

#### 7.3 与 ScholarAgent 的间接关联

虽然 LongLive 是视频生成项目，但其技术中有两个思想可类比：

**Attention Sink ≈ 认知锚点**：在超长序列生成中保持全局一致性的机制，类似 ScholarAgent 审长论文时需要"记住"前面章节的关键论点。

**KV-Recache ≈ Smart Compaction**：有限资源下维持长期信息的策略，类似 ScholarAgent 的 token 压缩与工作台恢复。

#### 7.4 资源

| 资源 | 链接 |
|------|------|
| GitHub | https://github.com/NVlabs/LongLive |
| 论文 1.0 | https://arxiv.org/abs/2509.22622 |
| 论文 2.0 | https://arxiv.org/pdf/2605.18739 |
| HuggingFace | https://huggingface.co/Efficient-Large-Model/LongLive-1.3B |

---

## 第二部分：对 ScholarAgent 的借鉴方案

---

### A. 立即可借鉴（P0，本周可执行）

#### A1. 失败驱动规则生成——CLAUDE.md 的活文档机制

**来源**: Karpathy 规则方法论

**问题**: ScholarAgent 的 CLAUDE.md（141行）规则主要来自架构设计推理，而非从实际错误日志中提炼。

**方案**: 在 `docs/PROGRESS.md` 每个 Phase 的 bug 记录末尾追加字段"**应转化为 CLAUDE.md 规则？**"。某类 bug 重复出现 ≥2 次，自动提取为 CLAUDE.md 中的一条规则。

**ScholarAgent 特殊适配**: Karpathy 的"先读文件再改"在审稿语境下不直接 applicable。等价规则应该是："Agent 在产出 finding 前必须确认 findings_store 中不存在语义重复的条目"——这正是 Phase 12 overlap bug 暴露的问题。

**预期效果**: 让项目开发本身成为自我进化系统——每次 bug 修复不仅修代码，还升级架构认知约束。

#### A2. harness.py 的 Decision Audit Trail

**来源**: implementation-notes 方法论

**问题**: harness.py（1260+ 行）包含大量隐含决策，目前只记录在 PROGRESS.md 叙事文本中，缺乏结构化索引。

**方案**: 在关键分支点添加结构化注释块：

```python
# DECISION: Phase 12 — conditional status sync
# WHY: Unconditional sync causes overlap checker to see "same-status duplicate"
# ALTERNATIVE REJECTED: Always sync (fails integrity constraint)
# TRADEOFF: Slightly more complex logic in exchange for correctness
# OPEN: Should we log a warning when sync is skipped?
```

**格式规范**: 五个固定字段（DECISION / WHY / ALTERNATIVE REJECTED / TRADEOFF / OPEN），对应 implementation-notes 的四类内容加上上下文锚定。

#### A3. 规则容量管理

**来源**: Karpathy 200 行限制 + 社区的规则衰减实践

**方案**: 为 CLAUDE.md 设置硬性容量约束（200 行）。当新规则需要加入时，必须审查是否有可降级/移除的旧规则。引入"规则热度"概念——过去 5 个 Phase 未被触发的规则标记为候选移除项。

---

### B. 短期可借鉴（P1，下 1-2 个 Phase）

#### B1. 论文结构预索引——CodeGraph 思想的学术论文适配

**来源**: CodeGraph 的预计算哲学

**问题**: Agent 审 50+ section 论文时需多次"搜索-阅读-再搜索"。v1 已验证"战略性阅读"有效（只读 7/42 核心 sections），但决策依赖 LLM 即时判断。

**方案**: PDF 解析阶段增加**论文内部引用图预构建**——从 section headings、内部引用（"as shown in Section 3.2"）、图表引用（"see Figure 4"）中构建轻量级引用关系图。Agent 审稿循环开始时即拥有此图。

**实现**: 正则表达式即可覆盖 90% 的学术论文引用格式。产出为结构化 JSON，注入 Context Assembler。

**架构合规**: 属于 Context Assembler 的预处理增强，图构建是确定性预计算，不违反"Agent = cognition, not orchestration"红线。

**⚠️ Rational Skepticism 警告**: 论文论证结构不像代码结构那样确定性。预索引产出应标记为"初始假设"而非"确定事实"，Agent 在审稿中应能修正此图。这与 HD-WM 的 hypothesis 管理一致。

#### B2. Findings 质量的内部评判层

**来源**: Agent-as-a-Judge (ICML 2025)

**问题**: 当前 quality gate 判断"够不够多"和"该不该停"，但对单条 finding 质量缺乏系统性评分。

**方案**: `update_findings` 录入时触发结构完整性检查——"证据链是否完整？是观点还是有数据支撑？严重程度评级是否合理？" 这不需要额外 LLM 调用，而是基于规则的结构检查。

**前置条件**: 需先定义"好 finding 的结构标准"——这本身就是学术贡献点。

#### B3. 审稿知识的持久化图谱输出

**来源**: Understand-Anything 的 knowledge-graph.json

**方案**: 审稿结束时，输出不仅包含 findings 列表，还包含结构化的"论文认知图"：核心论点 → 支撑证据链 → 证据链薄弱环节（即 findings）→ findings 间关系。

**与 HD-WM 的呼应**: HD-WM 管理的 hypothesis 本身就是图结构（supports/contradicts 关系），将其格式化为持久图谱是自然延伸。

#### B4. 认知循环策略的动态调优

**来源**: GPTSwarm 图优化 + SE-Agent 轨迹进化

**问题**: Completion Gate 的触发阈值（连续 N 轮无新 finding）是固定的，但最优 N 因论文类型而异。

**方案**: 短期用简单统计（理论论文 N=5，实证 N=3），长期积累 20+ 次审稿数据后引入轻量级元学习。SE-Agent 的"轨迹进化"思想可适配为：保存每次审稿的认知轨迹，从成功案例中提取最优策略模板。

---

### C. 长期研究方向（P2，面向学术贡献和论文发表）

#### C1. 跨审稿任务的自我进化

**最有学术价值的方向**。核心问题：Agent 审了 100 篇论文后，是否比审第 1 篇时更好？

当前答案是"否"——每次审稿独立，不存在跨任务能力迁移。

借鉴 Self-Evolving Agent 四维进化：

- **Memory 层**: "通用审稿模式"（如"实证论文未报告效应量是常见问题"）沉淀为跨任务可复用的领域知识
- **Tool 层**: 特定领域需要的验证工具（统计方法论检查、引用网络分析）按需自动引入
- **Workflow 层**: 审理论论文"先读结论再读证明"，审实证"先读方法再看结果"——策略差异作为可学习参数

**学术贡献**: "通过审稿实践自我进化的学术审稿 Agent"——Self-Evolving Agent 文献中缺乏的垂直领域验证。

**借鉴 Hermes Agent**: 其 Skill Distillation 机制（成功经验自动转化为可复用 Skill 文档）+ GEPA 遗传算法对 prompt 进行帕累托最优搜索，可直接适配为 ScholarAgent 的"审稿经验蒸馏"模块。

**借鉴 SE-Agent**: 将审稿轨迹视为可进化的"物种"，通过 Revision（修订）、Recombination（重组）、Refinement（精炼）三大算子实现轨迹级进化。

#### C2. 认知约束的理论框架

ScholarAgent 的 COGNITIVE_ANCHOR.md 和"constrain, don't control"哲学已经是隐式理论。借鉴 Karpathy 的"规则效能量化"，可形式化为可量化实验：

- 哪些约束对 Agent 行为改善最大？
- 约束之间是否存在交互效应？
- 约束集合的最优规模是多少？（Karpathy 的经验：200 行上限）

Phase 1-12 已积累丰富实验数据。如果能系统回答这些问题，就是一篇"Agent Cognitive Constraints Engineering"论文。

#### C3. Gödel Agent / HyperAgents 范式在审稿领域的验证

**Gödel Agent** 的自我感知 + 自我修改循环，和 **HyperAgents** 的元修改机制，代表了 Self-Improving 的最前沿。在学术审稿领域验证这些范式的可行性（或不可行性）本身就有发表价值：

- 审稿 Agent 能否像 SICA 那样自主修改自身的 orchestration 代码？
- 审稿的主观性是否构成 Self-Improving 的根本障碍？
- 如何在缺乏标量 reward signal 的领域定义"改进"？

---

## D. 警示与边界（Rational Skepticism 视角）

### D1. 不应盲目借鉴的部分

**CodeGraph 的确定性假设**: 代码结构是确定性的（AST 是精确的），但论文论证结构不是。同一段文字在不同解读下可能属于不同论证链。将"预计算"思想迁移到论文理解需要额外的"不确定性标注"机制。

**Self-Evolving 的 reward 问题**: 学术审稿评估极其主观——同一篇论文两位审稿人可能给截然相反意见。在缺乏可靠 reward signal 的领域做 self-improvement，存在"优化了错误目标"的风险。Agent-as-a-Judge 在此需格外慎重。

**复杂度膨胀风险**: ARCHITECTURE_V2_BLUEPRINT 的"简单性检查"明确警告了这一点。A 类建议低风险可控，B 类需谨慎增量实现，C 类应先作为独立实验验证再考虑整合。

### D2. Karpathy 规则的适用边界

Karpathy 的规则面向通用 coding agent，其失败模式与 ScholarAgent 不完全重叠。直接照搬规则内容没有意义（"先读文件再修改"在审稿语境下不 applicable），应借鉴的是**规则生成的方法论**——从 ScholarAgent 自身的失败日志中提炼审稿特有的规则。

### D3. 关于 ScholarAgent 当前定位的自检

ScholarAgent 已经在认知架构层面走在前沿（HD-WM、认知催促器、Completion Gate），这些本身就是行业创新。在追逐参考项目的思路时，需要时刻自检：我们是在补充短板，还是在偏离核心优势？

核心原则不变：**Agent = cognition, not orchestration**。所有借鉴都必须通过这条红线的检验。

---

## 第三部分：优先级总览与行动路线

| 优先级 | 编号 | 建议 | 来源 | 预期价值 | 实现成本 | 风险 |
|--------|------|------|------|---------|---------|------|
| P0 | A1 | 失败驱动规则生成 | Karpathy | 防止 bug 重复 | 极低 | 无 |
| P0 | A2 | harness.py Decision Audit | impl-notes | 降低维护成本 | 低 | 无 |
| P0 | A3 | 规则容量管理 | Karpathy + 社区 | 规则集健康度 | 极低 | 无 |
| P1 | B1 | 论文结构预索引 | CodeGraph | 提升导航效率 | 中 | 低 |
| P1 | B2 | Finding 质量评判 | Agent-as-a-Judge | 提升输出质量 | 中 | 中 |
| P1 | B3 | 审稿知识图谱输出 | Understand-Anything | 增强输出格式 | 中 | 低 |
| P1 | B4 | 认知循环动态调优 | GPTSwarm / SE-Agent | 策略自适应 | 中 | 中 |
| P2 | C1 | 跨任务自我进化 | Self-Evolving Agents | 学术贡献（极高） | 高 | 高 |
| P2 | C2 | 认知约束理论框架 | Karpathy 量化 | 论文选题（极高） | 中 | 中 |
| P2 | C3 | Gödel/HyperAgents 验证 | Self-Improving 前沿 | 学术贡献 | 高 | 高 |

---

## 第四部分：行业趋势综合判断

将七份参考材料放在一起看，可以观察到一条清晰的行业演进路线：

**2023年**：Agent 主要靠 prompt 工程（"请仔细审稿"）。

**2024年初**：开始有控制流设计（"循环直到满意"），但仍是静态 workflow。

**2024年末-2025年**：三个维度同时突破：
- (a) 认知架构从静态 workflow 转向动态自适应循环
- (b) 知识管理从 flat context 转向结构化图谱
- (c) 自我改进从人工调优转向自动化进化

**2025年末-2026年**：
- (d) 递归自改进成为研究热点（Gödel Agent → HyperAgents）
- (e) 预索引/预计算成为工程标配（CodeGraph 15K+ Stars）
- (f) Agent 评估从人工转向 Agent-as-a-Judge

ScholarAgent 目前处于 (a) 的前沿，在 (b) 和 (c) 方面有大量可探索空间。本文档为后续的计划延伸提供了具体的方向和优先级框架。

---

## 附录：全部参考资源链接

### Karpathy CLAUDE.md
- https://github.com/forrestchang/andrej-karpathy-skills
- https://deepwiki.com/forrestchang/andrej-karpathy-skills

### Self-Improving Agents
- Gödel Agent: https://arxiv.org/abs/2410.04444
- HyperAgents: https://arxiv.org/abs/2603.19461 | https://github.com/facebookresearch/Hyperagents
- SICA: https://arxiv.org/abs/2504.15228
- SE-Agent: https://arxiv.org/abs/2508.02085 | https://github.com/JARVIS-Xs/SE-Agent
- Agent-as-a-Judge: https://arxiv.org/abs/2410.10934
- Hermes Agent: https://github.com/NousResearch/hermes-agent
- Hermes Self-Evolution: https://github.com/NousResearch/hermes-agent-self-evolution
- Reflexion: https://arxiv.org/abs/2303.11366
- Self-Play: https://arxiv.org/abs/2512.02731

### CodeGraph
- colbymchenry/codegraph: https://github.com/colbymchenry/codegraph
- codegraph-ai/CodeGraph: https://github.com/codegraph-ai/CodeGraph
- HKUST 论文: https://arxiv.org/abs/2408.13863
- LocAgent: https://github.com/gersteinlab/LocAgent
- CodePrism: https://github.com/rustic-ai/codeprism

### Understand-Anything
- https://github.com/Lum1104/Understand-Anything
- https://understand-anything.com/

### Presenton
- https://github.com/presenton/presenton

### NVIDIA LongLive
- https://github.com/NVlabs/LongLive
- 论文 1.0: https://arxiv.org/abs/2509.22622
- 论文 2.0: https://arxiv.org/pdf/2605.18739
- HuggingFace: https://huggingface.co/Efficient-Large-Model/LongLive-1.3B

---

> **下一步**: 基于本文档的优先级排序，制定具体的实施计划（Phase 13+）。重点关注 P0 项（A1-A3，可立即执行）和 P1 项（B1-B4，需要设计后分阶段实施）。