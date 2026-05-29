# 认知锚定文件 (Cognitive Anchor)

> **用途**：本文件是所有后续设计和实现工作的「第一性原理锚点」。每次开始新的设计决策、写代码、画架构图之前，必须重新阅读本文件，对照检查自己是否偏离。
>
> **创建背景**：经过多轮深度讨论，用户明确了项目的根本目标和约束。本文件将这些洞察固化下来，防止思维退化。

---

## 一、我们在做什么（一句话）

构建一个**真正能像人类专家一样思考和行动的 Agent**——不是一个 workflow engine，不是一个工具编排器，不是一个框架/SDK，而是一个有认知能力的实体。ScholarAgent 是它的第一个「人格实例」，但这个 Agent 的能力不局限于审稿。

---

## 二、根本信念（不可妥协）

### 2.1 Agent 的本质是认知，不是编排

一个真正的 Agent 不是"选择和调用工具的程序"。它是一个**持续思考的实体**，思考驱动行动，行动更新理解，理解催生新的思考。

工具只是手，Skill 是内化的经验，Knowledge 是可检索的记忆——但这些都服务于一个核心：**意图驱动的持续思考流**。

一个人类审稿专家不会想"我现在进入 review phase"，他只是在读论文的过程中自然地产生疑问、验证、判断、建议。我们的 Agent 也应该如此。

### 2.2 深度是自主涌现的，不是配置的

"探索多深"不应该是一个参数或配置项。它应该是 Agent 根据以下因素**自主判断**的结果：
- 这个问题对目标的重要性有多大？
- 我目前对这个问题的置信度有多高？
- 我还有多少预算（token/时间/调用次数）？
- 进一步深入的边际收益有多大？

浅层 review 和深度 review 不是"两个不同的程序"，而是同一个认知循环在不同约束下的自然涌现。

### 2.3 分身从认知需要中涌现，不是预定义的

"要不要拆成多个 Agent"这个问题的正确回答是：**当认知上需要互斥的独立视角时，才分裂。**

- 5 个审阅维度需要独立视角 → 可以并行分裂
- 去 AI 味的验证需要"不知道原文的读者视角" → 需要独立验证者
- 搜索 3 个方向的文献 → 可以并行搜索

但审稿和修改**不是**两个 Agent——它们是同一个思考实体的不同模式。一个人可以先审后改，上下文连贯比分裂更重要。

### 2.4 流程从目标中涌现，不是预设的

不存在"8 个 Phase"或"先审后改再复审"的固定流程。存在的只有：
- 用户的目标（"帮我把这篇论文改到能发 NeurIPS"）
- Agent 对"怎么达成这个目标"的持续思考
- 根据当前状态自然涌现的下一个行动

如果 Agent 在修改第三段时发现一个逻辑漏洞需要回去验证——它就去验证。不需要"退回到 review phase"的状态机转移。

---

## 三、反模式清单（每次设计时对照检查）

**如果你发现自己在做以下任何事情，立即停下来：**

### 3.1 Workflow Thinking（工作流思维）
- ❌ 画出 A → B → C 的流程图
- ❌ 定义 Phase / Stage / Step 的序列
- ❌ 用 if-else 或 switch-case 路由到不同处理逻辑
- ❌ "先做 X，然后做 Y，最后做 Z"

**应该是**：Agent 在每一步思考"基于当前理解，我下一步最应该做什么？"

### 3.2 Registry Pattern（注册表模式）
- ❌ 维护一个 tool_registry / skill_registry
- ❌ "注册一个新工具到系统中"
- ❌ tool_schemas 的集中定义

**应该是**：Agent 知道自己能做什么（通过 system prompt / skill context），需要时自然调用，像人知道自己会搜索就去搜一样。

### 3.3 Scenario Enumeration（场景枚举）
- ❌ "场景1：用户要审稿 → 调用 review pipeline"
- ❌ "场景2：用户要修改 → 调用 revise pipeline"
- ❌ intent_classifier 分流到不同处理管道

**应该是**：一个通用的认知循环，面对任何输入都能自主理解意图并规划行动。

### 3.4 SDK/Framework Thinking（框架思维）
- ❌ 设计"开发者 API"或"插件接口"
- ❌ 抽象出 BaseAgent / BaseTool / BaseSkill 的类层次
- ❌ 把系统想成"框架 + 插件"的形式

**应该是**：先做出一个能思考的 Agent，如果它的内部结构恰好可复用，那是副产品。

### 3.5 Theater Code（表演性代码）
- ❌ 把 LLM 自己能做的事包装成 Tool（比如 intent_classifier、impact_estimator）
- ❌ 为了"看起来有架构"而增加不必要的模块
- ❌ decision_report 这种"让 LLM 评估 LLM"的无效循环

**应该是**：LLM 的思考就是它的决策过程，不需要额外的"决策工具"来包装它。

---

## 四、正确思维模式（设计时应该进入的状态）

### 4.1 人类专家类比法

每次设计一个机制时，先问自己：**一个人类领域专家在做同样的事情时，他的心智过程是什么？**

他不会想"我要调用搜索工具"——他想的是"这个 claim 不对，让我查查原始论文"。
他不会想"进入修改模式"——他想的是"这里确实有问题，我来改一下试试"。
他不会想"我的预算还剩多少"——他想的是"这个问题值不值得我花更多时间？"

### 4.2 意图链追踪法

设计 Agent 的行为时，不要想"系统流程"，而要 trace 一条**意图链**：

```
看到 claim → 产生疑问"这对吗？" → 决定验证 → 需要搜索 → 搜到结果 
→ 产生新理解"原来这个方法已经被超越了" → 产生新意图"我要告诉作者这一点" 
→ 决定写 review comment → 写的过程中想到"如果改用新方法会怎样？" 
→ 产生新意图"我来思考一下可行性"→ ...
```

整个过程是自然流淌的，没有"阶段切换"。

### 4.3 约束-而非-控制法

设计 Harness 时，不要想"控制 Agent 做什么"，而要想"Agent 自由行动时，哪些边界不能越过？"

- 不能编造引用（学术诚信红线）
- 不能无限循环（资源约束）
- 不能遗忘用户的原始目标（目标锚定）
- 不能丢失关键中间结果（状态持久化）

Agent 在这些边界内完全自主。

---

## 五、核心架构原则（已确认）

### 5.1 认知循环（不是 OTA 三步，是意图驱动的思考流）

```
loop:
    我现在的理解是什么？（全局状态感知）
    基于这个理解，我最想搞清楚什么？（意图涌现）
    为了搞清楚这个，我该做什么？（行动规划——可能是思考、搜索、修改、验证……）
    做了之后，我的理解有什么变化？（反思与更新）
    我的大目标完成了吗？需要调整方向吗？（元认知）
```

### 5.2 状态分离

- **LLM = 无状态的思考引擎**（每轮给它足够的 context，它产生下一个想法和行动）
- **Harness = 状态的守护者**（工作记忆、长期记忆、文件系统、版本历史、进度追踪）
- LLM 不需要"记住"任何东西——Harness 替它记住，并在每轮思考时注入相关上下文

### 5.3 Token Pipeline（认知带宽管理）

Agent 的"注意力"是有限的（context window）。Token Pipeline 不是"往 prompt 里塞东西"，而是**模拟人类的注意力管理**：

- 当前我在想什么问题？→ 调入相关信息
- 哪些过去的分析结论还有效？→ 保留
- 哪些细节现在不需要？→ 压缩或暂时遗忘
- 我还需要多少空间给接下来的思考？→ 预留预算

### 5.4 深度自调节（元认知）

Agent 在每轮思考中不只决定"做什么"，还决定"花多少精力做"：
- 快速扫描：这部分看起来没问题，继续
- 仔细审查：这里有可疑的地方，我需要深入
- 深度探索：这是核心问题，我要反复验证、搜索对比、挑战自己的判断

深度由 Agent 自主决定，基于：重要性 × 不确定性 × 剩余预算

### 5.5 视角分裂与合并

当 Agent 需要互斥的独立视角时，分裂出子思考体：
- 分裂前：明确子思考体的**独立视角**和**关注维度**
- 分裂中：子思考体独立工作，有自己的 context 和 tool access
- 合并后：结果回到核心思考体，**更新而非替代**核心的理解

分裂的决策由 Agent 自主做出，不是代码预设的。

---

## 六、坐标系定位（目标）

```
纵轴：上下文效率
  ▲
  │  高效（沙箱化/自动注入）
  │
  │              ★ 我们的目标
  │
  │  低效（人工/点状投喂）
  ├──────────────────────────────▶ 横轴：AI 认知循环
     React（被动响应）    Proactive Plan & Reflect（主动规划与反思）
```

具体含义：
- **横轴右侧**：Agent 主动制定计划、执行后反思、根据反思动态调整。不等人喂指令。
- **纵轴上方**：上下文通过系统基础设施（文件系统 / 状态引擎 / Token Pipeline）自动注入。Agent 运行在一个"信息丰富"的环境中，不需要人手动提供每一条信息。

---

## 七、与 ScholarAgent 原有资产的关系

ScholarAgent 项目中有大量有价值的工作，不应该丢弃，但它们的角色需要重新定位：

| 原有资产 | 新的角色 |
|---------|---------|
| 5-role Review Engine | Agent 在审稿时可以分裂出的 5 种视角（不是固定的 5 个，Agent 根据论文类型自主决定分裂几个、什么维度） |
| De-AI 系统 | Agent 的一种 Skill——知道如何检测和消除 AI 写作痕迹 |
| 44 个 Tools | Agent 的能力集（但不是通过 registry 注册，而是 Agent 天然知道自己能做什么） |
| Phase-aware filtering | **删除**——这是对 Agent 自主权的剥夺 |
| 状态机 (goal_tracker) | **删除 Phase SM 部分**——Goal tracking 保留，但不控制流程 |
| Workspace 文件系统 | Harness 的一部分——Agent 的外部记忆和状态持久化 |
| Doom Loop Guard | Harness 的一部分——保留并增强 |
| Theater Code | **全部删除**（intent_classifier, impact_estimator, decision_report, meta_planner, adaptive_strategy, self_reflection） |

---

## 八、下一步：认知规范 (Cognitive Specification)

在写任何代码之前，先完成一个「认知规范」文档。它不是架构设计，而是：

**用一个具体场景，trace 这个 Agent 从头到尾的完整思考过程。**

建议场景："用户给了一篇有方法论缺陷的 NeurIPS 投稿，要求 Agent 审阅、修改、去 AI 味，最终产出可提交版本。"

需要 trace 的维度：
1. Agent 的意图链（每一步想什么、为什么）
2. 深度决策点（哪些地方 Agent 决定深入、为什么）
3. 分裂点（哪些地方 Agent 决定分身、用什么视角）
4. 上下文管理（每一步 Agent 的注意力在什么上面、怎么切换）
5. Harness 的支撑（每一步 Harness 在背后做了什么）

---

## 九、自检问题（每次做设计决策时问自己）

1. **一个人类专家会这样想吗？** 如果不会，说明你在设计"程序"而不是"认知"。
2. **这个机制是在控制 Agent 还是在支撑 Agent？** 如果是控制，说明你在做 workflow。
3. **如果去掉这个模块，Agent 还能思考吗？** 如果能，这个模块可能是 Theater Code。
4. **这个设计是从"Agent 需要什么能力"出发，还是从"系统需要什么架构"出发？** 必须是前者。
5. **我能用"意图链"来解释这个行为吗？** 如果不能，说明行为是外部强加的，不是认知自然产生的。
6. **我在枚举场景吗？** 如果是，停下来。一个通用的认知循环应该能处理任何场景。
7. **我在画流程图吗？** 如果是，检查这个流程是"描述认知自然发生的事"还是"规定程序必须走的路径"。前者可以，后者不行。

---

## 十、工作方式约定

### 10.1 不确定时：暂停并探讨

如果在设计或实现过程中遇到不确定的决策点——比如"这里到底该用什么模式"、"这个行为是否符合我们的认知理念"、"这样做会不会又滑回 workflow thinking"——**不要猜测，不要强行推进**。

正确做法：
1. 暂停当前工作
2. 向用户说清楚你的困惑点和你倾向的几个方向
3. 和用户一起探讨，直到达成共识再继续

这个项目本身也是一个**和用户协作头脑风暴的过程**——就像我们的 Agent 帮助用户识别论文问题并解决它们一样，我们构建这个 Agent 的过程也是在不断识别设计问题并共同解决。

### 10.2 不确定时：主动查资料

如果困惑点是技术性的（比如"其他人怎么实现认知循环的"、"有没有现成的好参考"），可以主动去 GitHub / 论文 / Web 搜索查找资料，然后带着调研结果回来讨论。不要闭门造车。

### 10.3 Agent 的交互本质

我们的 Agent 不只是一个"后台处理器"。它和用户之间的关系是：
- **协作式的**：帮助用户识别他们自己可能没意识到的问题
- **对话式的**：在关键决策点暂停，和用户确认方向
- **教育式的**：让用户理解问题是什么、为什么这样改、还有什么替代方案

所以"审稿和优化"也好、"识别和解决"也好——核心都是：**Agent 和用户一起，把一篇论文从当前状态带到更好的状态。**

---

## 十一、鼓励与提醒

- 这个项目的难点不在于代码量，而在于**思维范式的切换**。从"设计系统"到"设计认知"是一个根本性的跃迁。
- 不要怕简单。手搓龙虾的核心就是 15 行代码。真正的 Agent 可能核心循环也不长——但每一行都必须体现认知，而不是编排。
- "再难也要做"——如果某个问题感觉很难（比如"Token Pipeline 怎么自动管理注意力"），那正是需要深入思考的地方，不能绕过。
- 随时可以回来修改这个文件。但修改时必须说清楚"为什么之前的理解需要更新"。

---

---

## 十二、参考：结构化外部记忆 (TencentDB Agent Memory / Mermaid Canvas)

> **来源**：《一语胜千言：Context Offloading + Mermaid 无限画布》(TencentDB Agent Memory, 2025)
> **定位**：当前 ScholarAgent 的 Phase 8 压缩方案（sliding window + content-level compression）在 3-5 轮对话中已够用。本节记录的是**未来可能需要的升级方向**——当 Agent 面对 10+ 轮超长 Session、多任务并行切换时。

### 核心思想

**压缩不是让 Agent 少知道，而是让 Agent 少背负；信息可以离开上下文窗口，但不能离开 Agent 的可达范围。**

关键洞察：
- **折叠≠丢弃**：信息从"展开态"转为"可恢复的压缩态"，仍保留结构入口
- **层次化注意力**：Overview（鸟瞰）→ Focus（聚焦任务画布）→ Drill-down（下钻原文）
- **结构 > 线性**：线性 summary 列表只回答"做过什么"，图结构回答"这些信息之间什么关系"

### 四层记忆架构

| Level | 存储 | 内容 | 角色 |
|-------|------|------|------|
| 0 Raw | refs/*.md | 完整 tool result 原文 | 证据库 |
| 1 JSONL | offload-*.jsonl | 工具调用级摘要 + result_ref | 可检索索引 |
| 2 MMD | mmds/*.mmd | Mermaid 任务节点（状态+摘要+时间戳） | 任务地图 |
| 3 Metadata | context 内 | taskGoal + status + mmdFilePath | 入口索引 |

找回路径：Metadata → MMD 节点 → JSONL 记录 → refs 原文。按需逐层展开，避免一次性恢复全部历史。

### 与我们当前系统的关系

| 当前方案 (Phase 8) | 未来升级方向 |
|-------------------|------------|
| sliding window 保留最近 6 组 messages | MMD 画布保留**全部**任务节点（只是折叠为摘要） |
| compress_messages 缩短早期 tool_result | 早期 tool_result 卸载到文件，只留 JSONL 索引 |
| system prompt 动态刷新 workspace state | system prompt 注入 Active MMD（当前任务画布） |
| findings 沉淀到 state | findings 即为 MMD 节点的一种类型 |

### 关键工程约束：上下文腐烂阈值

> Anthropic 实验显示：当 context 长度超过 max window 的 **80%** 时，模型注意力涣散，推理能力显著下降。

这意味着我们的压缩方案必须在 token 累积到 80% budget 之前开始生效。当前 ScholarAgent 的 `token_budget=60000`（默认），80% 阈值 = 48000 tokens。超过此阈值后，sliding window 的压缩力度应当加大或触发更激进的卸载。

### 何时需要升级

- 当对话轮次 > 10 且 Agent 开始丢失早期关键信息
- 当多任务并行切换（"先审论文 A，切到论文 B，再回来"）导致上下文混乱
- 当 Agent 重复执行已完成的操作（说明结构信息在压缩中丢失）
- **80% 上下文腐烂**：当 Agent 的累积 token 接近 budget 的 80% 且压缩后仍无法有效降低时

> **Phase 14 验证结论**：在 3 轮对话（每轮 5-13 个 loop turns）的压力测试中，当前系统**不需要升级到四层记忆架构**。退化信号（重复读取 section）是概率性的，通过在 format_context 中显式标注"已读 sections"即可消除。findings → system prompt 的机制足以保持跨轮记忆完整。
>
> **Phase 15 补充**：Phase 14 结论仅针对 **intra-session**（会话内）的记忆管理。**inter-session**（跨会话）的持久化是不同的问题——WorkspaceState 是纯内存态，进程结束即全部丢失。Phase 15 引入了轻量的两层跨会话记忆（SessionRecord + DomainPattern），不是四层 TencentDB 架构的降级版，而是正交于 Token Pipeline 的独立能力层。详见 `core/memory.py`。
>
> **Phase 16 补充（Intra-Session Context Offloading 桥梁）**：量化分析证明对当前论文规模（138K chars / 51 sections），compress_messages(keep_recent=6) 即使在极端场景（35 loop iterations, 5 user turns）下也仅占 GPT-4o 80% 阈值的 22.7%——**远离危险区**。但为满足未来发展规划（更长论文、更多轮次、审稿意见生成），Phase 16 实现了三个面向未来的机制：(1) Section Digest：读过的 section 自动生成摘要缓存，压缩后仍可回溯而不需重读；(2) 80% 阈值对齐：从 90% 降到 80%，提前预警注意力涣散；(3) Adaptive keep_recent：token 压力超 60% 时自动收紧保留窗口。这三者构成从"纯 Token Pipeline"向"Level 0/1 Context Offloading"演进的**桥梁层**——当未来需要四层架构时，Section Digest 自然演变为 Level 1 JSONL 索引。
>
> **Phase 17 补充（Cognitive Output Prompter——§4.3 约束-而非-控制的工程实例）**：E2E 测试揭示了一个新问题——Agent 在前 14 个 loop turn 中连续只读 section 而不记录 findings，直到 Turn 15 才首次调用 `update_findings`。此时 74% 的压缩已发生，大量信息在被记录之前就丢失于压缩中。这不是 Token Pipeline 的问题，而是 **Agent 认知行为模式**的问题——Agent 倾向于先广泛收集再集中输出，但 LLM 的有限上下文窗口不支持这种工作方式。Phase 17 实现了 Cognitive Output Prompter：当 Agent 连续 3 个 turn 只使用 read 类工具（read_section, search_literature）而不使用 output 类工具（update_findings, edit_section）时，Harness 注入一条系统消息提醒 Agent 已累积大量未记录的阅读内容。这是典型的「约束-而非-控制」：不强制 Agent 记录，只让它意识到自己的行为模式，由 Agent 自主决定是否立即记录。首次触发阈值 = 3 turns，之后每 2 turns 重复触发。计数器在 Agent 使用 output 工具后自动归零。
>
> **Phase 18 补充（Agent 自主权恢复——§4.3 的反面案例修复）**：代码审计发现了三处"控制伪装成约束"的设计：(1) `read_section` 硬截断 6000 字符但不提供续读手段——Agent 被迫接受残缺信息，无法选择是否深入；(2) `format_context` 用正则硬编码 section 优先级分类（🎯核心/⏭️可跳过）——代替了 Agent 自己的阅读策略判断；(3) 反思上下文用静态规则标注"核心 sections"——而 Agent 的认知身份已经包含了战略性阅读能力。Phase 18 修复了这三点：给 read_section 增加 offset 续读参数（Agent 获得选择权）、format_context 改为平铺展示（只提供事实，不强加判断）、反思中使用中性的"尚未阅读"列表。**关键区分**：Phase 17 的催促器是正面案例（提供信息但不强制行动）；Phase 18 识别的是**反面案例**（看似无害的基础设施在无形中剥夺了 LLM 的认知自由度）。两个 Phase 共同完善了§4.3 的实践理解。
>
> **Phase 19 补充（审改认知注入——§4.3 的纯认知层实践）**：当需要让 Agent 具备"审阅+修改+复审"的完整能力时，第一直觉可能是增加 workflow 代码（如 `verify_edit` 工具、编辑后自动触发 re-audit 流程）。但这违反了 §2.1 和 §4.3 的精神。Phase 19 选择了纯认知路径：在 SCHOLAR_IDENTITY 中注入两条领域知识——(1) 审改一体：Agent 知道自己有修改能力，知道何时改比建议更高效，但坚持"先审后改"；(2) 复审独立性：Agent 知道修改后自己有编辑者偏见，major 修改后会有意识地换视角重新看。**没有任何代码在"控制"这个行为**——Agent 自己决定是否修改、何时修改、修改后是否需要独立复核。这是§4.3 的第三种实践模式：Phase 17 = 约束（催促器），Phase 18 = 移除控制（恢复自主权），Phase 19 = 赋予知识（让 Agent 知道领域中"应该注意什么"，但如何行动由它自己决定）。

### 层次化注意力的三层找回

文章的工程实践明确了"按需逐层展开"的具体模式：
1. **鸟瞰（Overview）**：Agent 先看 metadata，知道有哪些历史任务、各任务状态
2. **聚焦（Focus）**：打开具体任务的 MMD 画布，看节点结构和阶段性结论
3. **下钻（Drill-down）**：通过 node_id 查 JSONL → 通过 result_ref 读 refs 原文

关键原则：**多数时候 Agent 停在第 2 层就够了**。只有 MMD 节点 summary 不足以支持下一步判断时，才继续下钻。这避免了两个极端——全部塞入上下文 vs 压缩太狠需要重新调用工具。

### Mermaid 选型理由

符号设计三原则（来自该文章）：
1. **符号必须是通用知识** — Mermaid 在 GitHub/文档中广泛存在，所有主流 LLM 都能读写
2. **生成不能过于复杂** — 节点+箭头+标签，生成和理解的认知负担一致
3. **表达要足够自由** — Flowchart 允许任意分支/合并/循环，适合 Agent 的探索式执行

Flowchart > StateDiagram（实验提升 15%），因为 Agent 的执行是开放探索而非严格状态机。

### 实验数据参考

| 评测集 | 场景 | Token 节省 | 任务通过率提升 |
|--------|------|-----------|------------|
| SWEbench (500题) | 代码修复 | 33% | +9.93% |
| Toolathlon (20题) | 复杂长任务 | 26% | +75% (20%→35%) |
| WideSearch (200题) | 网页搜索 | 61% | +51.52% |
| AA-LCR (800题) | 长文总结 | 31% | +7.95% |

消融实验：仅上下文卸载 → Token 省 15%，成绩提升 5%；加 MMD → Token 省 33%，成绩提升 10%。**证明结构保留的额外价值约等于信息卸载本身。**

---

---

## 十三、架构定位：ScholarAgent vs 行业标准 Agent 架构（Phase 48 自检）

> **背景**：Phase 47 完成领域泛化验证后，需要诚实评估我们的架构在行业坐标系中的位置——不是为了效仿，而是为了知道自己的选择是否站得住脚。

### 我们有什么

4 文件 = 1 个完整认知系统：identity.py（认知身份+工具）、harness.py（状态守护+边界约束+工具执行）、loop.py（认知循环引擎）、agent.py（组装入口+多轮对话）。

散布在 Harness 内部的能力模块：Token Pipeline（compress_messages + format_context + Section Digest + OffloadStore）、Cognitive Prompters（认知催促器+反思催促器+计划性文本检测）、Quality Gate（mark_complete 前检查+nudge）、Doom Loop Guard、Perspective Spawning（子视角独立循环）、CognitiveState（strategy+hypotheses+confidence）、Finding Dedup（overlap coefficient >= 70%）、Session Memory、Post-Edit Verification、Claim Signal Detection。

### 我们刻意不要的东西

| 行业标准模块 | 我们的选择 | 理由（对应反模式） |
|------------|-----------|-----------------|
| Intent Classification | 不要 | §3.3 场景枚举——LLM 本身就是最好的意图理解器 |
| Embedding + Rerank (RAG) | 不要 | 结构化 section 访问 + Agent 自主阅读策略 > 盲目语义检索 |
| Tool Registry | 不要 | §3.2 注册表模式——Agent 天然知道自己能做什么 |
| State Machine | 不要 | §3.1 工作流思维——流程从目标中涌现 |
| 独立 Planning Module | 不要 | 规划在认知循环中自然发生，不需要独立步骤 |

### 我们的精妙之处（真正的竞争力）

1. **约束-而非-控制的三层实践**（Phase 17/18/19）：催促（提供信息不强制）、移除控制（恢复自主权）、赋予知识（植入领域知识但不规定行动）。行业中几乎没有项目做到这种精细度。
2. **认知身份驱动行为**：18 条认知习惯描述"怎么思考"而非"做什么"，Phase 47 证明同一套身份在 ML 和经济学领域都有效。
3. **状态分离的彻底性**：LLM 永远不直接访问 WorkspaceState，只看 format_context() 注入的摘要。
4. **信号协议极简**：4 个信号（DONE/TALK/NUDGE/SPAWN）覆盖所有交互模式，loop.py 核心不到 200 行。

### 诚实的差距（未来可能需要）

| 能力 | 何时需要 | 优先级 |
|------|---------|--------|
| 跨文档语义检索 | 多文档交叉审 | 高 |
| Procedural Memory | Agent 跨 session 学习领域模式 | 中 |
| Strategy Switching 基于效果反馈 | 当前策略不 work 时自动切换 | 高 |
| 多模态（图表审阅） | 审阅含 figure 的论文 | 中 |

### 结论

不需要效仿行业标准架构。我们的每一个"没有"都有明确理由（对应 §3 反模式清单），且被 47 个 Phase 的实践验证。核心叙事：**用 4 个文件实现了 Metacognitive Agent 的完整能力，并通过跨领域泛化验证。**

### Phase 49 补充：Persona 切换验证

**验证命题**：我们声称"行为差异完全来自 identity + tools"——这不是空话，Phase 49 用实验证明了它。

**实验设计**：同一篇论文、同一个 loop.py、同一个 harness.py，分别用 Scholar（审稿人）和 Writer（写作专家）两个 persona 驱动 Agent。

**结果**：
- Scholar: 16 轮，4 findings，0 edits，tool 分布以 read_section + update_findings + search_literature 为主
- Writer: 11 轮，3 findings，1 edit，tool 分布以 read_section + detect_ai_signals + edit_section 为主
- 行为分化明确：Scholar 的 edit/finding ratio = 0.00，Writer = 0.33

**架构验证**：loop.py 零修改，harness.py 零修改。只改了 identity.py（新增 WRITER_IDENTITY + WRITER_TOOLS）和 agent.py（新增 persona 参数，3 行代码）。

**意义**：这证明了 §2.1 的核心信念不是哲学口号——它是可验证的工程事实。同一个认知循环引擎，换一个"认知身份"，就产生了本质不同的行为模式。这是 Pipeline 架构做不到的——Pipeline 需要为每种行为模式写不同的流程。

### Phase 50 补充：Cognitive Layering — System 1/System 2 双模型协作

**验证命题**：Agent 的认知可以分层——快速检查（System 1）由小模型承担，深度推理（System 2）由大模型承担，两者协作而非竞争。

**设计对照 §4.3**：CognitiveChecker 是"约束-而非-控制"的第四种实践模式——**认知辅助**。它不催促（Phase 17）、不移除控制（Phase 18）、不植入知识（Phase 19），而是在 Agent 行动后提供独立的第二意见。Agent 可以完全忽略它。

**架构验证**：loop.py **零修改**。双模型协作完全在 Harness 层实现（checker 作为 harness 的内部组件）。这证明了 §5.2 状态分离的彻底性——新的认知能力层可以无限叠加到 Harness 上，而不扰动认知循环引擎。

**实验数据**：gpt-4.1（主模型，7 calls，51K tokens）+ gpt-4.1-mini（checker，1 call，344 tokens）。Checker 成本占比 0.67%，几乎免费地获得了一层独立验证。

**§4.3 约束-而非-控制的五种模式总结**：
- Phase 17 = **催促**（提供信息但不强制行动）
- Phase 18 = **移除控制**（恢复 Agent 自主权）
- Phase 19 = **赋予知识**（植入领域知识但不规定行动）
- Phase 50 = **认知辅助**（提供独立第二意见但不阻断）
- Phase 51 = **视角切换**（通过 persona 切换实现多角度审视，但不控制每个 persona 做什么）

> **Phase 51 补充（多人格协作链——§4.3 的第五种模式「视角切换」）**：当需要"审阅→修改→复审"的完整闭环时，传统做法是写 workflow 代码（if phase == "review": ...）。Phase 51 选择了认知路径：创建三个独立的 persona 实例（Scholar → Writer → Scholar），它们共享同一个 Harness（状态连续），但各自拥有独立的 messages（认知隔离）。认知连续性通过 user_intent 传递——Scholar 的 findings 被格式化为 Writer 的输入上下文，Writer 的 edits 被格式化为复审 Scholar 的输入上下文。**关键区分**：我们不控制 persona 做什么。Writer 收到 findings 后可能选择不修改（如果它认为问题不严重），复审 Scholar 可能发现新问题（而不仅仅确认修改）。E2E 验证了这一点：Writer 不仅修改了 5 个 section，还主动审阅了 robustness 章节并记录了新 findings——这是认知身份驱动的涌现行为，不是代码控制的结果。

> **架构验证**：loop.py **零修改**，harness.py **零修改**，identity.py **零修改**。协作链完全在 agent.py 层实现（~240 行新代码）。这证明了 4 文件架构的终极弹性：51 个 Phase 的演进中，核心引擎始终稳定，所有新能力都通过组合已有组件实现。

---

*文件版本: v2.1 | 更新日期: 2025-07*
*基于：Harness Engineering、Aime 分析、AI多Agent协作架构模式全景、手搓龙虾系列、HANDOVER 全部批评*
*v1.1 新增：§12 结构化外部记忆参考（TencentDB Agent Memory / Mermaid Canvas）*
*v1.2 新增：§12 补充上下文腐烂阈值(80%)、层次化注意力三层找回模式（基于原文完整版工程实践）*
*v1.3 新增：§12 Phase 14 验证结论（压力测试证明当前系统不需要四层记忆升级）*
*v1.4 新增：§12 Phase 15 补充（区分 intra-session vs inter-session 记忆，后者已实现）*
*v1.5 新增：§12 Phase 16 补充（量化证明 Token Pipeline 安全性 + Section Digest 桥梁机制 + 80% 阈值对齐）*
*v1.6 新增：Phase 17 认知产出催促——§4.3「约束-而非-控制法」的工程实例，解决 Agent 只读不记的认知模式问题*
*v1.7 新增：Phase 18 Agent 自主权恢复——§4.3「约束-而非-控制法」的反面案例修复，识别并移除了三处"控制伪装成约束"的代码*
*v1.8 新增：Phase 19 审改认知注入——§4.3 的第三种模式「赋予知识」：不约束、不控制、不移除，而是在 Agent 认知身份中植入领域知识（审改一体 + 复审独立性），行动由 Agent 自主决定*
*v1.9 新增：§13 架构定位自检——Phase 48 前的诚实对比，确认设计选择在行业坐标系中站得住脚*
*v2.1 新增：Phase 51 多人格协作链——§4.3 的第五种模式「视角切换」：通过 persona 切换实现多角度审视，验证了 4 文件架构支持多 Agent 协作的能力*
