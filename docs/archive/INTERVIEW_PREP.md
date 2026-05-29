# ScholarAgent — 面试准备材料

> **本文件不放入 GitHub public repo**。仅供面试准备使用。
> 
> 目标：让你在面试中 5 分钟讲清楚、15 分钟讲深入、所有追问都有准备。

---

## B-1. Architecture Walkthrough 讲稿

准备三个时长版本：

**2 分钟 Elevator Pitch**：
> "我做了一个学术论文自主审稿 Agent。市面上的工具要么把全文扔给 LLM（token 贵、注意力散），要么是刚性管道（不能适应不同论文）。我的设计是 Harness Pattern——让 LLM 做决策，但给它受控的上下文、分阶段的工具集、和独立的验证回路。44 个工具、8 阶段状态机、5 角色并行审稿、自动去 AI 痕迹检测。核心理念是 same model, better harness, better results。27K 行 Python，完整跑通了真实论文。"

**5 分钟 Product Story**：
> 问题定义 → 天真解法为什么不行 → 我的 3 个关键设计创新 → 每个创新对应什么用户价值 → 量化结果

**15 分钟 Technical Deep Dive**：
> 挑 2-3 个最深刻的决策展开（见 B-2），配合现场 demo 或代码走读

---

## B-2. 三个最值得讲的设计决策

### 决策 1：De-AI 为什么独立验证器（PEV Loop）

| | |
|---|---|
| **约束** | LLM 自评偏差严重——生成者和评判者不能是同一个调用 |
| **替代方案** | ① 嵌入 prompt 要求"写得自然些" ② 后置人工检查 ③ 第三方 API |
| **选择理由** | PEV Loop 实现"考官≠考生"，12+ 信号类别可量化追踪，Hard Caps 零容忍 |
| **验证方式** | 22 gold standard cases + 分维度评分 + baseline-relative 判定 |
| **PM 洞察** | "我不信任 AI 的自我评价"——这个判断本身就是产品设计能力 |

### 决策 2：Issue Routing 为什么 3 种 action_type

| | |
|---|---|
| **约束** | 全自动太激进（改论点怎么办）、全确认太慢（格式问题不值得问） |
| **替代方案** | ① 2 种（auto/manual） ② 5 种（更细粒度） ③ 用户逐个配置 |
| **选择理由** | 3 种恰好覆盖"安全自动做"/"需确认"/"超出能力" + budget mode 复用 |
| **结果** | 同一系统通过 `--budget` 参数适配 3 种用户偏好 |
| **PM 洞察** | "控制流设计就是产品设计"——同一个引擎，三种产品 |

### 决策 3：Phase State Machine + 动态工具裁剪

| | |
|---|---|
| **约束** | 44 个工具全暴露 → 模型选错概率高，schema token 浪费 |
| **替代方案** | ① 全暴露靠 prompt 引导 ② 硬编码 workflow ③ 用户手动选工具 |
| **选择理由** | 中间路——模型仍做决策，但决策空间从 44 缩至 15-25 个相关工具 |
| **验证** | v3→v4 工具选择准确率提升（从 trace.jsonl 可统计） |
| **PM 洞察** | "给 Agent 自由，但不是无限自由"——约束设计是 Agent PM 的核心能力 |

---

## B-3. STAR Stories（4 个场景覆盖）

### Story 1：攻克技术复杂度
- **S**: 需要一个学术审稿 Agent，但直接全文扔 LLM 效果差
- **T**: 设计 harness 让标准模型产出专家级 review
- **A**: Section 粒度 + 5 角色子 agent 隔离 + PEV 验证回路 + 4 层质量门
- **R**: 从"token-expensive, attention-scattered"变为可追踪、可审计的系统

### Story 2：产品思维的迭代演进
- **S**: v1 是简单管道，用户反馈"全自动太激进"
- **T**: 让同一系统适配不同用户控制偏好
- **A**: 3 种 action_type + budget ceiling + first-of-type 确认
- **R**: 同一系统 3 种产品形态，用户满意度提升

### Story 3：质量工程闭环
- **S**: 修改后的论文反而更像 AI 写的，用户投诉
- **T**: 确保每次修改不引入新 AI 痕迹
- **A**: PEV Loop（验证与生成分离）+ 12+ 信号 + Hard Caps + 分层容忍度
- **R**: 22 gold cases 评测体系，检测精确度可量化

### Story 4：面对不确定性的风险管理
- **S**: Agent 可能修改作者核心论点或杜撰引用
- **T**: 划定 Agent 的能力边界
- **A**: 3 条 Red Lines 硬编码在代码层（不由 model 判断）+ doom loop 检测 + circuit breaker
- **R**: 有些能力主动放弃 = 更值得信任的系统

---

## B-4. Key Metrics 速查卡

```
规模指标
├── 86 源文件 / 27,734 行非测试 Python
├── 44 个注册工具（Phase filtering 缩为 15-25 per phase）
├── 5 角色并行审稿 + 3-pass 去重
├── De-AI 12+ 信号类别 + 1062 行 programmatic detector
└── 8 阶段状态机（IDLE→PARSING→...→DONE）

质量指标
├── 168 个单元测试
├── L1-L4 四级 benchmark（格式/逻辑/学术/领域）
├── 22 个 De-AI gold standard cases
├── 4 个 judge prompts + 4 个 scoring rubrics
└── [待提取] 最新 eval 各级得分

效率指标
├── De-AI precheck 节省 80%+ 不必要 LLM 调用
├── Phase filtering 工具可见数从 44 降至 15-25
├── Section 粒度避免全文 token 浪费
├── Recall cache TTL 避免重复工具调用
└── Two-layer context compression（30K soft / 45K hard）

架构指标
├── v1→v4 四个大版本迭代
├── 3 种 budget mode × 3 种 model tier = 9 种运行配置
├── 4 层错误恢复（retry → backoff → fallback → circuit break）
├── Multi-provider failover + health tracking
├── 3 条代码级 Red Lines（非 prompt 约束）
└── 3 层记忆系统（Identity/Project/Ephemeral）+ 衰减曲线 + Staleness 验证
```

---

## B-5. 面试常见问题 & 回答要点

| 问题 | 核心回答方向 |
|------|------------|
| "Why not just prompt engineering?" | Harness > Prompt：同模型 + 结构化工具 + 受控上下文 = 更好结果。Prompt 无法做 token 预算、无法做验证回路、无法持久记忆。 |
| "How do you evaluate this?" | 4 级 benchmark + 22 gold cases + LLM-as-judge + rubrics。不是"跑一次觉得好"，是有基线、有回归测试。 |
| "What would you do differently?" | ① 早期应该先写 eval 再写功能（TDD for Agent） ② Web UI 还未做 ③ 需要更多真实用户数据 |
| "How does this compare to existing tools?" | 不是 Grammarly（句子级语法）、不是 ChatGPT（一次性全文 review）、是结构化的自主 Agent 系统 |
| "Scale/Performance?" | Parallel review + token budget + phase filtering + context compression 四层优化 |
| "Agent 会不会失控？" | 4 层防线：Red Lines（代码级安全边界）、Doom Loop 检测、Circuit Breaker、Budget Ceiling。设计原则是"可逆性 × 影响范围"矩阵：不可逆高风险 → 硬拦截（Red Lines）、可逆但首次 → 确认门（first-of-type）、可逆低风险 → 自动执行。这和业界 Agent 安全最佳实践完全一致。 |
| "56 个工具怎么编排不出错？" | 两条纪律：① 独立工具并行调用、有依赖的顺序调用（Phase filtering + 工具依赖声明保证） ② 有专用工具不走通用路径（如 deai_audit 专做去 AI 检测，不混入 review 调用）。再加 Doom Loop 检测防止重复调错。 |
| "最难的技术挑战？" | De-AI 闭环——让 AI 检测自己写的东西的 AI 味，且修复后不引入新问题。解决方案是"考官≠考生" |
| "你的 PM sense 体现在哪？" | 控制流设计就是产品设计：3 种 budget = 3 种产品；Red Lines = 产品边界；first-of-type = 渐进式信任 |
| "Agent 有记忆吗？跨会话怎么学习？" | 3 层记忆架构（Identity/Project/Ephemeral）+ 衰减曲线 + Staleness 验证。参考了 Claude Code 的分类法，但针对学术审稿做了领域特化：论文级记忆追踪 revision trajectory，用户偏好通过观察编辑隐式推断。 |
| "记忆会不会过时？怎么处理？" | 每种记忆有 decay_class（slow/medium/fast），召回时按 half-life 计算 freshness_weight。低于 0.3 的标记 STALE 降权。偏好类记忆还有 last_confirmed 机制——30 天未再次观察到则自动衰减。不是"记忆越多越好"，而是"正确的记忆在正确的时候出现"。 |

---

## B-6. Demo 演示脚本

**3-5 分钟 Live Demo**（需提前准备录制版备用）：

```
Step 1 (30s): 展示 --budget minimal 跑短论文
  → 输出结构化 review（5 角色、评分、issue 列表）
  → 重点讲解 action_type 分类逻辑

Step 2 (60s): 展示 --budget full 一个 issue 的完整生命周期
  → review 发现问题 → route 判定为 confirm_fix
  → 展示 proposal → 用户确认 → rewrite 执行
  → de-AI audit 检测 → PASS/FAIL 判定

Step 3 (30s): 展示 --stream 模式 + /pause 命令
  → 体现 human-in-the-loop 交互设计

Step 4 (30s): 展示 --dry-run 模式（零 API 消耗）
  → 输出预估 cost/time/token（展示工程化思维）

Step 5 (30s): 打开 .workspace/ 展示持久化状态
  → trace.jsonl、score_history、voice_profile
  → 体现"Agent 有记忆、可审计"
```

**备选方案**：录制 asciinema 版本（不依赖网络和 API key）。

---

## B-7. Competitive Positioning 一页纸

| 维度 | ChatGPT/Claude 裸用 | Grammarly | Paperpal | ScholarAgent |
|------|-----|-----|-----|-----|
| 审稿粒度 | 全文一次性 | 句子级 | 段落级 | Section + Issue 级 |
| 可追踪性 | 无 | 有限 | 部分 | 完整 trace.jsonl |
| 用户控制 | 无 | 无 | 无 | 3 budget mode + confirm |
| 去 AI 检测 | 无 | 无 | 无 | 12+ 信号 PEV Loop |
| Token 效率 | 低（全文） | N/A | 中 | 高（section + phase filter） |
| 安全边界 | 靠 prompt | N/A | 无 | 3 条代码级 Red Lines |
| 学习能力 | 无 | 无 | 无 | Session Memory + Gold Standard |
| 统计验证 | 无 | 无 | 无 | Stata MCP 集成 |

**你的定位句**："不是让 AI 更聪明，而是让 AI 更可控、可追踪、可验证。Agency comes from the model; the harness makes agency real."

---

## B-8. "下一步计划"完整回答框架（面试必问题）

面试官问"如果继续做下去，你的 Roadmap 是什么？"时，不能只讲功能——需要展现**全局产品思维**。
按 6 个维度准备回答，每个维度 2-3 句话即可，面试官追问再展开。

---

### 维度 1：产品形态演进

> **当前**：CLI 工具（开发者/研究者自用）
> **下一步**：Web UI + 实时协作

- Split-pane 界面：左侧执行 trace 实时滚动，右侧论文编辑器同步 diff
- 从"单人工具"进化为"导师-学生协作平台"：导师设定 review focus + 学生看 Agent 建议
- VS Code 插件形态：在 LaTeX 编辑环境内嵌入审稿+改写能力（不打断写作流）

**面试讲法**：「CLI 验证了核心能力，但真正的用户价值在编辑器内。下一步是让 Agent 从'你跑一次给你报告'变成'你写一句它实时给你反馈'。」

---

### 维度 2：用户增长与分发策略

> **目标用户**：在读研究生（硕博）、年轻教职、独立研究者
> **获客路径**：不是 B2C SaaS，是 PLG（Product-Led Growth）

- 开源核心 + 托管服务（Freemium）：本地跑免费，云端跑按论文篇数计费
- 学术社区渗透：在 Overleaf 社区、学术 Twitter/小红书、导师群发示例报告
- "审稿报告"作为传播载体：用户分享 Agent 生成的 review 报告 → 自然引流
- 与 Overleaf/Zotero 集成：降低试用门槛（不需要 clone repo、不需要配环境）

**面试讲法**：「学术工具的增长不靠投广告，靠的是'我用了、觉得好、发给师弟'。审稿报告本身就是可分享的内容载体。」

---

### 维度 3：数据飞轮与模型改进

> **核心洞察**：每次使用都在产生高质量训练数据

- **飞轮 1**：用户确认/拒绝 Agent 建议 → 标注数据 → 微调 issue routing 模型（哪些该 auto_fix、哪些该问用户）
- **飞轮 2**：De-AI audit PASS/FAIL 判定 + 人工 override → 校准信号检测精度
- **飞轮 3**：Voice Profile 收集 → 按学科/作者风格聚类 → 预训练 rewrite 风格
- **飞轮 4**：Session Memory 的 tool pattern → 优化 Phase Filtering 规则（数据驱动而非手写）

**面试讲法**：「每个用户的使用过程都是隐式标注。confirm_fix 的确认/拒绝天然产出 RLHF 数据，这比我手写规则有效 10 倍。」

---

### 维度 4：技术基建演进

> **从单机 CLI → 可扩展的 Agent 基础设施**

- **Agent 编排层抽象**：当前 agent_loop 是单线程顺序执行。下一步引入 DAG 编排（并行 review 和并行 rewrite 可以同时进行）
- **模型无关化**：当前 router.py 支持 3 tier，但 tool schema 是 OpenAI format。下一步适配 Anthropic native tool_use + 本地模型（vLLM/Ollama）
- **Eval 自动化**：CI 中集成 eval pipeline，每次代码变更自动跑 L1-L4 benchmark + gold set 回归
- **可观测性升级**：从 trace.jsonl 文件 → OpenTelemetry 标准 spans → Grafana dashboard（token cost、latency、质量分）

**面试讲法**：「技术债最大的一块是可观测性。trace.jsonl 够用但不够好——我想要的是每个 tool call 都有 span、cost、latency，然后 dashboard 上能看到'这篇论文花了多少钱、慢在哪一步'。」

---

### 维度 4+：执行/决策分离 — 可解释的决策系统（v5.0 新增）

> **核心叙事框架**："LLM 负责执行，Harness 负责决策质量"

这是 v5.0 新增的决策可观测层（C-8），将系统从"黑箱 Agent"升级为"可解释决策系统"。类比广告系统的 bid explanation：每次竞价不只输出出价金额，还输出"为什么出这个价"。

**两层设计**：

- **Decision Log（execution-time）**：每个 routing 决策生成结构化 `DecisionTrace`，记录 4 项检查（Red Line × 2、First-of-type、Budget ceiling）各自的触发状态和 why-not 理由。写入 `.workspace/trace/routing_decisions.jsonl`，支持事后分析。
- **Decision Report（pipeline-end）**：处理完论文后生成决策摘要——"处理了 N 个 issue，M 个自动修复，K 个超出能力范围；分数从 X 提升到 Y，每个 action 类别贡献多少"。包含 score attribution（类似 Shapley value 的启发式归因）和 capability boundary 识别。

**面试中怎么用这个框架**：

- 当被问"你的 Agent 和直接调用 GPT 有什么区别"时：「区别在于决策层。GPT 做执行——写句子、提建议；但 Harness 做决策——什么该写、写到什么程度、什么绝对不能碰。每个决策都有完整 trace，处理完后输出决策报告。这让 Agent 从黑箱变成可审计的系统。」
- 当被问"怎么保证 Agent 不犯错"时：「两道防线。第一道是 Red Line——某些决策（修改论点、编造引用）code-enforced 永远不会发生。第二道是可观测性——即使 Agent 做了不理想的决策，decision_trace 让我能事后复盘'为什么选了 auto_fix 而不是 confirm_fix'，然后改规则。」
- 当被问"你的设计理念"时：「我从广告系统学到一个原则：好的系统不只做出好的决策，还能解释自己的决策。Decision Report 就是我的 bid explanation。」

**代码实证**：
- `tools/action_router.py` — `DecisionTrace` 数据类 + `_build_decision_summary()` 
- `tools/decision_report.py` — `generate_decision_report()` + score attribution + capability boundaries
- `core/tool_metadata.py` — 56 工具的 meta 声明支撑 `_get_meta_risk_for_category()`

---

### 维度 5：商业化路径

> **不是"能不能赚钱"，而是"这个 Agent 架构的商业价值在哪"**

- **路径 A — 学术 SaaS**：按论文篇数/月订阅收费。对标 Paperpal（$20/月）但提供 10 倍深度。
- **路径 B — 机构 License**：高校/研究所批量采购。卖点是"降低审稿修改的人工时间 50%+"。
- **路径 C — Agent 架构 consulting**：ScholarAgent 的 harness pattern 可以迁移到其他领域（法律文书审核、医疗报告质检）。卖的是架构能力而非产品本身。
- **定价锚点**：一篇论文的 full-budget 运行成本约 $2-5 API 费用。定价 $15-30/篇有 3-6 倍毛利空间。

**面试讲法**：「我不只想做一个工具——Harness Pattern 本身是可迁移的。换一套 domain tools 就能做法律合同审核、换一套 rules 就能做医疗报告质检。这是一个 Agent 架构的垂直应用系列。」

---

### 维度 6：团队与协作

> **如果要把这个项目从个人作品变成团队项目**

- **角色划分**：Agent 核心（我）+ 前端工程师（Web UI）+ NLP 研究员（De-AI 信号优化）+ 学术 domain expert（规则校准）
- **开源社区运营**：CONTRIBUTING.md 不只是形式——真正接受 PR 的领域是 skill files（学科写作规范）和 eval gold cases（社区贡献测试用例）
- **技术文档体系**：从当前的 DESIGN.md 单文件 → ADR（Architecture Decision Records）目录，每个重大决策有独立文档
- **质量门禁**：CI 中 eval regression gate（新代码不能让 L1-L4 分数下降）

**面试讲法**：「如果给我一个 3 人团队，我会让前端做 Web UI（这是用户价值最大的缺口），让 NLP 同学优化信号检测精度（这是技术壁垒），我自己继续迭代 Agent 编排和 harness 设计。」

---

### 完整回答模板（1 分钟版本）

> "下一步有三个层次。**短期**是产品形态升级——从 CLI 到 Web UI，让用户在编辑器里实时看到 Agent 反馈。**中期**是数据飞轮——每次用户的确认/拒绝都是隐式标注，用来优化 routing 和检测精度。**长期**是架构迁移——Harness Pattern 不只能做学术审稿，同样的模式换一套 domain tools 就能做法律/医疗/金融的文档质检。我验证的不是一个 tool，是一种 Agent 设计方法论。"

---

## 面试准备 Checklist

| # | 事项 | 预计耗时 | 状态 |
|---|------|---------|------|
| 1 | Architecture Walkthrough 讲稿排练 | 3h | ⬜ |
| 2 | STAR Stories 详细展开 + 排练 | 2h | ⬜ |
| 3 | Metrics 速查卡（从 eval 提取最新数据） | 1h | ⬜ |
| 4 | 录制 Demo（asciinema + 备用 GIF） | 3h | ⬜ |
| 5 | Competitive Positioning 整理 | 1h | ⬜ |
| 6 | 模拟面试 Q&A 排练 | 2h | ⬜ |
