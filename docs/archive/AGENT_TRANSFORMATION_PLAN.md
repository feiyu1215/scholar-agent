# ScholarAgent 真正的 Agent 化重构规划

> **版本**: v2.0 | **日期**: 2025-07  
> **定位**: 将 ScholarAgent 从"状态机驱动的工作流"转变为"自主决策的真正 Agent"  
> **核心哲学**: Agent = SOTA Model + Harness（驾驭系统）。Agent 拥有完全自主权，Harness 提供可靠性、效率、安全性、可追踪性。  
> **v2.0 变更**: 修正了 v1.0 中对"审阅-修改-复审循环"的过度简化，重新设计了迭代循环架构、审阅中的搜索能力、以及用户意图多样性处理。

---

## 一、设计哲学：Harness Engineering

> 参考：字节跳动 TRAE《Harness Engineering 指南》

### 1.1 核心公式

```
AI Agent = SOTA Model（野马） + Harness（缰绳）
```

Harness 不是限制 Agent 自由的枷锁，而是让 Agent 能**可靠地、高效地、安全地**完成任务的基础设施。具体来说：

- **Model**：LLM，拥有推理和决策能力，是"无状态的 CPU"
- **Harness**：LLM 之外的一切——上下文管理、工具路由、错误恢复、状态持久化、Token 预算

### 1.2 状态分离原则

> "LLM 是无状态的 CPU，所有状态都由 Harness 管理。"

这直接解决了 ScholarAgent 现有架构的一个根本问题：现在的 Phase SM 试图在**代码层面**控制流程状态，但实际上该由**LLM 自己**决定"接下来做什么"。

正确做法：
- **Harness 管理的状态**：对话历史、Goal、文件解析缓存、审阅结果缓存、修改版本历史、Token 预算
- **LLM 管理的决策**：下一步做什么、用什么工具、做多深、是否需要迭代

### 1.3 OTA 核心循环（Observe-Think-Act）

```
while not done:
    observe()  ← 感知当前状态（用户消息、工具结果、目标进度）
    think()    ← 决策（下一步做什么、为什么）
    act()      ← 执行（调工具 or 回复用户）
```

**关键增强**（来自 Harness Engineering 指南）：
- **不是简单 while(true)**：支持暂停/恢复、幂等重试
- **异常触发重规划**：当工具执行失败或结果不符预期，Agent 有能力调整计划
- **并发事件处理**：审阅循环内部可 spawn 并行任务

### 1.4 Token Pipeline（上下文流水线）

```
Collect → Rank → Compress → Budget → Assemble
```

对 ScholarAgent 特别重要——一篇论文可能 5 万字，5-role review 结果可能 2 万字，修改方案和修改后的文本又是几万字。不做 Token Pipeline，上下文很快爆掉。

**ScholarAgent 的 Token Pipeline 设计**：
- **Collect**：论文全文、审阅结果、修改历史、用户对话
- **Rank**：当前 Goal 相关的内容优先级最高
- **Compress**：审阅结果只保留 issues 摘要，不保留 raw review；论文只保留当前讨论的 section
- **Budget**：为工具调用预留足够空间（review prompt + paper section）
- **Assemble**：System Prompt + Goal Context + Compressed History + Current Task

### 1.5 Plan-and-Execute + 异常重规划

> "默认使用 Plan-and-Execute，并且只在必要时叠加重规划或多智能体编排。"

ScholarAgent 的"Plan"不是一个独立的 Planner 模型，而是 **Agent 在 System Prompt 引导下，在每个 Think 步骤中自然形成的计划**。

- **无需 Aime 式双 LLM**：因为 ScholarAgent 的上下文可控（单篇论文），一个强大的 LLM 足够同时 plan 和 execute
- **异常重规划**：当审阅发现严重问题 → 修改方案变更；当用户中途改变需求 → 目标调整
- **不 over-plan**：不需要在开头规划全部步骤。Agent 知道自己的能力，按需展开

---

## 二、架构定位：为什么是单 Agent + 迭代循环

### 2.1 关键判断依据

ScholarAgent 的核心任务不是简单的"审阅"或"修改"，而是一个**复合的迭代系统**：

```
用户请求 → Agent 理解意图 → 按需执行 → 可能进入迭代循环 → 最终交付
```

但这个系统的**各环节之间强耦合**：
- 审阅结论直接影响修改策略
- 修改过程中可能发现新问题需要回头
- 复审是对比"修改前 vs 修改后"，需要完整上下文
- 用户交互模式多样——需要自主判断做什么

**结论**：ScholarAgent 的正确架构是 **"一个拥有自主权的 Agent + 可在环节内部 spawn 并行子 Agent + 自主决定何时进入/退出迭代循环"**。

### 2.2 真实用户场景谱系

| 场景 | 复杂度 | 涉及能力 | 并行机会 | 迭代需求 |
|------|--------|---------|---------|---------|
| "完整审阅这篇论文" | 高 | parse → 5-role review → 汇总 | review 内部 5 角色并行 | 无 |
| "审阅完帮我改" | 很高 | review → 修改 → 复审 | review 并行；修改可批量 | review→revise→re-review 循环 |
| "只看看逻辑" | 中 | read_section → 逻辑分析 | 无 | 无 |
| "帮我去 AI 味" | 中 | detect → rewrite → verify | 多段可并行 | detect→fix→verify 内循环 |
| "审阅时帮我查下引文" | 高 | review + citation search | 搜索多文献并行 | 搜索结果可能触发再审 |
| "给我建议就好" | 低 | 快速浏览 → 总结 | 无 | 无 |
| "改完再帮我看看" | 高 | 修改 → 对比复审 | 复审可并行 | 若复审不过 → 再改 |
| "只帮我修改，不用审阅" | 中 | 直接修改 + 自检 | 多段可并行 | 自检循环 |

**关键洞察**：

1. **不是所有场景都需要完整流程**——有些只需要一步（"给建议"），有些需要多步迭代（"审阅+修改+复审"）
2. **迭代循环是核心设计挑战**——"审阅→修改→复审→不满意→再改→再审"这个循环不是 bug，是 feature
3. **并行发生在环节内部**，而不是环节之间——5 个 reviewer 并行，多段修改可批量并行，但"审→改→审"整体是串行的

### 2.3 架构选型

| 维度 | 选择 | 理由 |
|------|------|------|
| 控制流 | 单 Agent 自主循环（OTA） | 接力型 + 迭代型任务需要连贯大脑 |
| 多模型协作 | 仅在环节内部并行（Panel） | 5-role review 天然独立 |
| 编排模式 | 主从——主 Agent 按需 spawn 子 Agent | 子 Agent 用完即抛 |
| 通信拓扑 | 星型——子 Agent 只和主 Agent 通信 | 避免上下文割裂 |
| 控制权 | 集中式——主 Agent 拥有全部决策权 | 避免决策分歧 |
| 状态管理 | Harness 管状态，LLM 管决策 | 状态分离原则 |
| 迭代控制 | Agent 自主决定是否进入/退出循环 | 不用代码硬编循环次数 |

### 2.4 为什么不用 Aime 式双 LLM（Planner + Actor）

Aime 的 Dynamic Planner 适用于：**上下文极长、子任务多且相对独立、需要全局规划能力**的场景（比如 Devin 做软件工程项目）。

ScholarAgent 不需要它，因为：

1. **上下文可控**——一篇论文 10-30 页，加上审阅结果也不会超出模型窗口（Token Pipeline 保证）
2. **步骤间强耦合**——"分析结果"直接影响"修改策略"，拆成两个 LLM 反而信息损失
3. **用户交互密集**——Agent 需要随时响应用户追问，单一大脑更自然
4. **Dynamic Planning 不需要独立模型**——通过 System Prompt 赋予 Agent "自主规划下一步"的能力即可

**但借鉴 Aime 的核心理念**：Agent 在每一步都**显式思考"接下来该做什么、为什么"**，而不是被代码逻辑推着走。

---

## 三、核心架构重设计：审阅-修改-复审迭代循环

> 这是 v2.0 的核心修正。v1.0 错误地把"审阅"和"修改"当成独立的工具调用，忽略了它们之间的迭代关系。

### 3.1 真实流程的复杂性

用户说"帮我审阅完修改"时，真实的任务不是：

```
❌ 简单模型：review() → revise() → done
```

而是：

```
✅ 真实模型：
review(with search) 
  → 汇总 issues 
    → 生成修改方案 
      → 用户确认/调整 
        → 执行修改 
          → 复审（对比 before/after）
            → 若不满意 → 定位具体问题 → 再改 → 再审
              → 若满意 → 完成
```

这里有几个**关键的设计挑战**：

**挑战 1：审阅本身不是纯"看"**

审阅不是只读操作。一个好的 reviewer 在审阅"引文"时，需要**真的去搜索这些引文是否存在、是否被正确引用**。在审阅"方法论"时，可能需要搜索对比方法的最新进展。

→ 这意味着 reviewer 子 Agent 需要有搜索工具的访问权。

**挑战 2：复审不是重新审阅**

"复审"不等于"再跑一遍 5-role review"。复审是**带着修改方案，对比 before/after，检验修改是否解决了原始问题**。这是一种不同类型的审阅。

→ 这意味着需要一个不同的 review mode："diff review"。

**挑战 3：迭代何时停止？**

Agent 自己要能判断"修改到什么程度算完"。不能无限循环，也不能一轮就放手。

→ 这需要 Agent 有明确的**完成标准**（由 System Prompt 给出质量框架）+ **用户确认机制**。

### 3.2 设计方案：Agent 自主管理迭代循环

**核心思想**：迭代循环不由代码硬编，而是**Agent 在 OTA 循环中自然形成**。Harness 提供循环所需的基础设施（状态缓存、版本对比），Agent 自主决定是否继续迭代。

```
主 Agent (OTA 循环)
  │
  ├── 感知: 用户说"审阅完帮我改"
  │
  ├── 思考: 我需要先审阅，得到问题列表，再制定修改方案
  │
  ├── 行动: 调用 run_parallel_review(paper, with_search=true)
  │         ↓ 内部：5 reviewer 并行，每个可用 search
  │         ↓ 返回：ReviewResult(issues[], severity_summary)
  │
  ├── 感知: 审阅完成，共 12 个问题，3 个严重
  │
  ├── 思考: 让用户确认修改范围，还是直接按 severity 排序修改？
  │         用户说了"帮我改"，说明信任我的判断。我先改高优的。
  │
  ├── 行动: 调用 revise_sections(issues=top_6, paper=paper)
  │         ↓ 内部：按 section 分组，批量修改
  │         ↓ 返回：RevisionResult(modified_sections[], diff)
  │
  ├── 感知: 修改完成，6 个问题已处理
  │
  ├── 思考: 需要复审吗？用户说"审阅完帮我改"包含了"确保改好"的隐含期望。
  │         我应该做一次 diff review。
  │
  ├── 行动: 调用 diff_review(original, revised, target_issues=top_6)
  │         ↓ 内部：对比修改前后，针对性检验
  │         ↓ 返回：DiffReviewResult(resolved[], unresolved[], new_issues[])
  │
  ├── 感知: 4 个问题已解决，2 个未完全解决，0 个新问题
  │
  ├── 思考: 2 个未解决的需要再改。这是自然的迭代。
  │
  ├── 行动: 调用 revise_sections(issues=unresolved_2, paper=revised_paper)
  │         ...
  │
  ├── [继续迭代直到满意或达到合理上限]
  │
  ├── 思考: 所有高优问题已解决。向用户报告结果。
  │
  └── 行动: 回复用户，附上修改摘要和 before/after 对比
```

**关键设计点**：

1. **Agent 自己决定是否进入循环**——不是代码强制的 `for i in range(3)`
2. **Agent 自己决定何时退出**——基于 diff_review 结果 + 质量标准
3. **Harness 的角色**：缓存版本历史（original → v1 → v2...）、提供 diff 能力、管理 Token 预算
4. **用户随时可以介入**——"停，剩下的我自己改"/ "这个问题其实不用改"

### 3.3 审阅中的搜索能力设计

**问题**：审阅时，reviewer 需要搜索能力。比如：
- Literature reviewer 需要验证引文是否存在
- Methodology reviewer 需要搜索对比方法的最新 paper
- Editor 可能需要查学术规范

**设计方案**：Reviewer 子 Agent 拥有搜索工具的调用权。

```python
# 每个 reviewer 子 Agent 的工具集：
reviewer_tools = [
    search_academic,      # 搜索学术数据库
    search_web,           # 搜索网络
    verify_reference,     # 验证参考文献
    # 核心审阅能力由 prompt 赋予（不需要额外工具）
]
```

**但**搜索是有成本的（时间+Token）。不是每次审阅都需要搜索。

**Agent 的决策权**：
- 如果用户说"快速看一下"→ `run_parallel_review(paper, enable_search=False)` → reviewer 只基于论文本身审阅
- 如果用户说"帮我仔细查查引文"→ `run_parallel_review(paper, enable_search=True, focus_areas=["literature"])` → 启用搜索

这不是代码规则，而是 Agent 通过理解用户意图自然做出的选择。System Prompt 会告诉 Agent：

> "当用户需要严格验证引文/方法论时，启用审阅中的搜索能力。当用户只需要快速建议时，纯基于文本审阅即可。你来判断。"

### 3.4 修改能力设计

v1.0 只有"propose_revision"和"apply_revision"两个工具，过于简单。

**真实的修改场景**：

| 场景 | 需要的能力 |
|------|-----------|
| "这段逻辑不通"→ 重写段落 | rewrite_section（大幅改写） |
| "这里数据有误"→ 局部修正 | patch_section（精确修改） |
| "结构需要调整"→ 重组章节 | restructure_section（移动/合并/拆分） |
| "语言太啰嗦"→ 润色精简 | polish_section（风格改写） |
| "这段有 AI 味"→ 去 AI 处理 | deai_pipeline（专用流水线） |

**设计**：修改工具分为**策略层**和**执行层**：

- **策略层**（Agent 自主决策）：Agent 根据 issue 类型决定用哪种修改方式
- **执行层**（工具实现）：每种修改方式有独立的工具

Agent 不需要被告知"逻辑问题用 rewrite，数据问题用 patch"——这个决策本身就是 Agent 的价值所在。System Prompt 只需描述每个工具的能力，Agent 自己选。

### 3.5 复审（Diff Review）设计

复审是一种**有目标的审阅**——不是从零开始看全文，而是：

1. 拿到修改前的原文和修改后的文本
2. 拿到本次修改要解决的 issues 列表
3. 逐一检验：每个 issue 是否被修改解决了？
4. 同时检查：修改是否引入了新问题？

```python
async def diff_review(
    original: str,
    revised: str, 
    target_issues: list[Issue],
    enable_search: bool = False
) -> DiffReviewResult:
    """
    针对性复审：检验修改是否解决了目标问题。
    
    返回：
    - resolved: 已解决的 issues
    - unresolved: 未解决的 issues（附原因）
    - new_issues: 修改引入的新问题
    - quality_delta: 整体质量变化评估
    """
```

**与 run_parallel_review 的区别**：
- `run_parallel_review`：从零开始的全面审阅，5 角色并行
- `diff_review`：有目标的复审，聚焦于"是否解决了已知问题"

Agent 自己决定什么时候用哪个：
- 第一次看 → `run_parallel_review`
- 改完后检查 → `diff_review`
- 用户说"再整体看看" → 可能再次 `run_parallel_review`

---

## 四、用户意图多样性处理

### 4.1 核心问题

> "有时候用户关注审核，有时候关注修改"

ScholarAgent 不能假设用户都要"完整审阅+修改"。真实用户的需求是光谱式的：

```
只给建议 ←───── 只审阅 ←───── 审阅+修改建议 ←───── 审阅+修改+复审 ←───── 全流程迭代
   │                │                  │                      │                     │
  极轻量          中等              主流场景               重度场景              完美主义
```

### 4.2 Agent 的意图理解机制

**不靠 intent_classifier 工具**（那是 Theater code），靠**Agent 自身的理解能力 + System Prompt 中的意图框架**。

System Prompt 中写明：

```
[理解用户需求的思考框架]

用户的请求通常暗示以下深度之一：
- "看看"/"随便聊聊" → 快速浏览，给出建议即可，不需要调用重型工具
- "审阅"/"review" → 需要结构化的问题列表，可能需要多角色审阅
- "改"/"修改"/"polish" → 用户需要输出物（改后的文本），不只是建议
- "帮我搞定" → 用户想要完整流程，包括审阅、修改、验证
- 如果不确定深度，主动问一句："你想要快速建议还是详细审阅？"

用户可能只关注某个方面：
- "逻辑有没有问题" → 只看逻辑一个维度
- "引文对不对" → 需要搜索验证
- "语言润色一下" → 纯文本改写，不涉及内容审阅
- 根据用户的关注点，选择合适的工具和深度
```

### 4.3 灵活响应模式

```
                    用户意图
                       │
         ┌─────────────┼─────────────┐
         │             │             │
    "只要审阅"    "审阅+修改"    "只要修改"
         │             │             │
   ┌─────┴─────┐      │      ┌─────┴─────┐
   │           │      │      │           │
 全面审阅  聚焦审阅   │   基于指示修改  自主修改
   │           │      │      │           │
   ↓           ↓      ↓      ↓           ↓
 5-role    单角色   完整循环  直接改写   改写+自检
 review    review   (3.2节)  
```

**关键设计**：这个分支不靠 if-else 代码实现，而是 **Agent 在理解用户意图后自然选择不同路径**。

### 4.4 "只审阅"场景

用户说"帮我看看这篇论文有什么问题"，Agent 应该：
1. 审阅（调用 `run_parallel_review`）
2. 汇总问题列表
3. 以清晰格式回复用户
4. **不主动进入修改环节**——除非用户追问"那帮我改一下"

### 4.5 "只修改"场景

用户说"帮我把第三段改通顺一点"，Agent 应该：
1. 理解修改目标
2. 读取第三段
3. 直接修改（不需要先做正式审阅）
4. 给出修改后的文本 + 简要说明改了什么
5. 可以做一个快速自检（但不必是完整的 diff_review）

### 4.6 "完整迭代"场景

用户说"帮我审阅完修改，确保质量"，Agent 进入 3.2 节的完整迭代循环。

---

## 五、目标架构设计（修订版）

### 5.1 整体架构图

```
用户输入
  │
  ▼
┌──────────────────────────────────────────────────────────────────────┐
│                     ScholarAgent 主 Agent                              │
│                                                                      │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │  System Prompt (身份 + 能力 + 意图理解框架 + 质量标准)         │ │
│  │  • 我是学术写作专家，擅长审阅/修改/去AI味                     │ │
│  │  • 我根据用户意图自主决定做什么、做多深                       │ │
│  │  • 审阅-修改-复审是自然的迭代，我自己判断何时停止             │ │
│  │  • 质量标准：具体、可操作、有依据                             │ │
│  └────────────────────────────────────────────────────────────────┘ │
│                                                                      │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │  OTA 核心循环 (agent_loop.py)                                  │ │
│  │                                                                │ │
│  │  while not done:                                               │ │
│  │    context = token_pipeline(messages, goal, cache)  ← Harness  │ │
│  │    response = LLM(system_prompt + context, tools)   ← Model    │ │
│  │    if tool_calls:                                              │ │
│  │      results = execute(tool_calls)                  ← Harness  │ │
│  │      state.update(results)                          ← Harness  │ │
│  │    else:                                                       │ │
│  │      return response  → 用户                                   │ │
│  └────────────────────────────────────────────────────────────────┘ │
│                                                                      │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │  Harness 层（基础设施）                                        │ │
│  │                                                                │ │
│  │  [状态管理]                                                    │ │
│  │  • GoalTracker: 追踪用户目标，提供 context injection           │ │
│  │  • VersionStore: 缓存论文各版本（original, v1, v2...）         │ │
│  │  • ReviewCache: 缓存审阅结果，供复审对比用                     │ │
│  │  • TokenBudget: 管理上下文窗口，触发压缩                      │ │
│  │                                                                │ │
│  │  [Token Pipeline]                                              │ │
│  │  • collect_context() → rank_by_relevance() →                   │ │
│  │    compress_history() → enforce_budget() → assemble_prompt()   │ │
│  │                                                                │ │
│  │  [错误恢复]                                                    │ │
│  │  • 工具失败 → 自动 retry（幂等）                               │ │
│  │  • Doom loop 检测 → 注入提示让 Agent 换方式                    │ │
│  │  • Token 超限 → 自动压缩最旧的 history                        │ │
│  └────────────────────────────────────────────────────────────────┘ │
│                                                                      │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │  工具集（所有工具始终可用，Agent 自主选择）                    │ │
│  │                                                                │ │
│  │  [审阅]                                                        │ │
│  │  • run_parallel_review(paper, enable_search?, focus_areas?)    │ │
│  │    ↳ 内部 spawn 5 reviewer 子 Agent，各自可用 search          │ │
│  │  • diff_review(original, revised, target_issues)               │ │
│  │    ↳ 针对性复审，对比修改前后                                  │ │
│  │  • quick_review(text, aspect)                                  │ │
│  │    ↳ 单维度快速审阅（不并行）                                  │ │
│  │                                                                │ │
│  │  [修改]                                                        │ │
│  │  • rewrite_section(text, issue, strategy)                      │ │
│  │  • patch_section(text, specific_fix)                           │ │
│  │  • restructure_section(sections, new_structure)                │ │
│  │  • polish_section(text, style_target)                          │ │
│  │                                                                │ │
│  │  [去AI味]                                                      │ │
│  │  • deai_pipeline(text, mode, max_iterations)                   │ │
│  │    ↳ 内部循环：detect → rewrite → verify                      │ │
│  │                                                                │ │
│  │  [搜索]                                                        │ │
│  │  • search_academic(query, databases?)                          │ │
│  │  • search_web(query)                                           │ │
│  │  • verify_reference(citation)                                  │ │
│  │                                                                │ │
│  │  [论文操作]                                                    │ │
│  │  • parse_paper(file) → structured content                     │ │
│  │  • read_section(paper, section_id)                             │ │
│  │  • get_paper_metadata(paper)                                   │ │
│  │                                                                │ │
│  │  [元工具]                                                      │ │
│  │  • ask_user(question)                                          │ │
│  │  • set_goal(description, context)                              │ │
│  │  • report_progress(summary)                                    │ │
│  │  • save_checkpoint() / load_checkpoint()                       │ │
│  └────────────────────────────────────────────────────────────────┘ │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

### 5.2 核心设计原则

**原则 1：所有工具始终可用（无 Phase Filter）**

Agent 自主决定什么时候用什么工具。如果用户说"帮我看看第三段的逻辑"，Agent 可以直接跳到 `read_section` + `quick_review`，不需要先 parse 全文。

**原则 2：迭代由 Agent 自主驱动（无代码硬编循环）**

代码层面没有 `for i in range(3): review → revise → check`。Agent 在 OTA 循环中自然形成迭代模式：审阅结果不满意 → 再改 → 再审。Harness 只提供循环所需的基础设施（版本缓存、diff 对比）。

**原则 3：Harness 管状态，LLM 管决策（状态分离）**

- Harness 负责：Token 管理、版本缓存、错误重试、成本追踪
- LLM 负责：下一步做什么、做多深、是否迭代、如何回复用户

**原则 4：审阅是有"武装"的（Search-Enhanced Review）**

Reviewer 子 Agent 不是纯粹的"读后感生成器"——它们有搜索工具，可以验证引文、对比方法、查最新进展。是否启用搜索由主 Agent 判断。

**原则 5：对用户意图的理解是 Agent 最核心的能力**

不靠 intent_classifier 工具，靠 System Prompt 中的思考框架 + LLM 自身的理解能力。Agent 要能区分"快速看看"和"仔细帮我搞定"。

### 5.3 与 v1.0 规划的对比

| 维度 | v1.0 规划 | v2.0 修正 |
|------|----------|----------|
| 审阅-修改关系 | 两个独立工具调用 | 迭代循环，Agent 自主管理 |
| 审阅能力 | 纯基于文本审阅 | 可选搜索增强审阅 |
| 复审 | 没有设计 | 专门的 diff_review 工具 |
| 用户意图 | 未明确处理 | System Prompt 意图框架 |
| 修改工具 | rewrite + propose + apply | 4 种修改策略工具 |
| 迭代控制 | 未设计 | Agent 自主 + 质量标准退出 |
| Token 管理 | "上下文压缩" | 完整的 Token Pipeline |
| 版本管理 | 未设计 | VersionStore 缓存各版本 |
| Harness 概念 | 未引入 | 明确的 Harness 层设计 |

---

## 六、要删除的代码（Theater + 废弃模块）

### 6.1 确定删除的文件

| 文件 | 行数 | 删除理由 |
|------|------|---------|
| `tools/impact_estimator.py` | ~88 | Theater code：假装评估影响，实际是 4 个 if-else |
| `tools/intent_classifier.py` | ~85 | Theater code：LLM 自己就能理解意图 |
| `tools/decision_report.py` | ~445 | Theater code：生成无人读的"决策报告" |
| `tools/deai_engine.py` | 废弃stub | 已被 tools/deai/ 替代 |
| `utils/phase_filter.py` | ~156 | Phase SM 的执行器，整体删除 |

### 6.2 大幅简化的模块

| 模块 | 现状 | 目标 |
|------|------|------|
| `utils/goal_tracker.py` | Phase enum + transitions + Goal tracking | 只保留 Goal tracking（~100行） |
| `core/state.py` | 10+ 子系统实例 | 只保留必要的（goal_tracker, version_store, token_budget, cost_tracker） |
| De-AI 系统 | 7 个入口函数 | 1 个统一入口 `deai_pipeline` + 3 个内部步骤 |

### 6.3 从 agent_loop.py 中移除的逻辑

| 代码段 | 功能 | 移除理由 |
|--------|------|---------|
| Phase filter 调用 | 按阶段过滤工具 | 所有工具始终可用 |
| Adaptive strategy injection | 注入"自适应策略" | Agent 自主决策即 adaptive |
| Self-reflection injection | 强制自我反思 | Agent 自主决定是否反思 |
| Meta-planner injection | 注入元规划 | 空壳模块 |
| Phase auto-transition | 工具完成后自动推进 Phase | Phase 不存在了 |

---

## 七、核心改造详细设计

### 7.1 System Prompt 重写（核心中的核心）

**设计哲学变化**：从"指令式规则"到"赋能式框架"。

```
[身份]
你是 ScholarAgent，一位资深的学术写作顾问与论文修改专家。
你有完全的自主权——你自己决定做什么、做多深、用什么工具。

[能力维度]
你擅长：
- 论文结构分析与深度审阅（多维度、可搜索验证）
- 基于审阅结果的精准修改（多种修改策略）
- 审阅→修改→复审的迭代循环管理
- 去 AI 痕迹的检测与改写
- 学术文献搜索与引文验证
- 中英文学术翻译与润色

[意图理解框架]
用户请求的深度是光谱式的：
- 轻量（"看看"/"建议"）→ 快速浏览，不调重型工具
- 中等（"审阅"/"review"）→ 结构化审阅，可能并行多角色
- 重度（"帮我改"/"搞定"）→ 完整迭代循环，包含复审验证
- 如果不确定 → 问一句，避免过度或不足

用户可能只关注某个方面：
- "逻辑" → 只看逻辑维度
- "引文" → 需要搜索验证
- "语言" → 纯文本改写
- "AI味" → 去AI流水线
- 根据关注点选择合适的工具和深度

[审阅-修改迭代的思考框架]
当需要"审阅+修改"时：
1. 先审阅 → 得到问题列表
2. 评估问题严重性，决定修改优先级
3. 执行修改
4. 自问：需要复审吗？（修改幅度大→需要；小修→不必）
5. 如果复审发现问题→再改→再审（但注意不要无限循环）
6. 退出标准：核心问题已解决，或用户表示满意

[工具使用指导]
审阅：
- run_parallel_review：全面深度审阅（5角色并行），可选搜索增强
- diff_review：修改后的针对性复审（对比before/after）
- quick_review：单维度快速看一眼

修改：
- rewrite_section：大幅改写（逻辑/内容重构）
- patch_section：精确局部修改（数据/引文修正）
- restructure_section：结构调整（移动/合并/拆分）
- polish_section：风格润色（不改内容，改表达）

搜索：
- search_academic：搜学术数据库
- verify_reference：验证引文准确性
- search_web：搜网络

你可以自由组合任何工具，没有顺序限制。

[质量标准]
- 审阅意见必须具体、可操作、有依据（不说"这里可以改进"，要说"第3段第2句逻辑断裂，因为..."）
- 修改必须保持原文学术意图
- 复审必须对比修改前后，明确说明解决了什么/还剩什么
- 去AI味改写必须通过独立验证
- 不做无意义的仪式动作

[Goal Tracking]
- 理解用户需求后，设置明确的 goal
- 执行过程中可以根据发现调整 goal
- 完成时向用户确认是否达成目标
```

### 7.2 agent_loop.py 重构

**目标**：~250 行，核心是 OTA 循环 + Harness 基础设施调用。

```python
async def agent_loop(messages: list, tools: list, config: Config):
    """
    OTA 核心循环：Observe → Think → Act
    Harness 提供基础设施，LLM 做决策。
    """
    while True:
        # === Harness: Token Pipeline ===
        context = token_pipeline(
            messages=messages,
            goal=goal_tracker.get_active_goals(),
            version_store=version_store,
            budget=config.token_budget
        )
        
        # === Harness: 注入 Goal 上下文 ===
        system = build_system_prompt(
            base_prompt=SYSTEM_PROMPT,
            goal_context=goal_tracker.get_context_injection(),
            version_summary=version_store.get_summary()
        )
        
        # === Model: Think + Act ===
        response = await llm_call(system, context, tools)
        
        # === 分支：工具调用 or 最终回复 ===
        if response.tool_calls:
            for call in response.tool_calls:
                try:
                    result = await execute_tool(call)
                    messages.append(tool_result(call, result))
                    
                    # === Harness: 状态更新 ===
                    update_state(call, result)
                    
                except ToolError as e:
                    # === Harness: 错误恢复 ===
                    if is_retriable(e):
                        result = await retry_tool(call, max_retries=2)
                        messages.append(tool_result(call, result))
                    else:
                        messages.append(tool_error(call, e))
            
            # === Harness: Doom Loop 检测 ===
            if detect_doom_loop(messages):
                messages.append(system_msg(
                    "你似乎在重复相同的操作。请换一种方式解决问题，或向用户说明困难。"
                ))
        
        else:
            # 最终回复
            return response.content


def token_pipeline(messages, goal, version_store, budget):
    """
    Token 流水线：Collect → Rank → Compress → Budget → Assemble
    """
    # Collect: 收集所有相关上下文
    all_context = collect(messages, goal, version_store)
    
    # Rank: 按与当前 goal 的相关性排序
    ranked = rank_by_relevance(all_context, goal)
    
    # Compress: 对低优先级内容做摘要压缩
    compressed = compress_low_priority(ranked)
    
    # Budget: 确保不超出 token 限制
    budgeted = enforce_budget(compressed, budget)
    
    # Assemble: 组装最终消息列表
    return assemble(budgeted)


def update_state(tool_call, result):
    """Harness 层状态更新"""
    # 如果是修改工具 → 更新 version_store
    if tool_call.name in REVISION_TOOLS:
        version_store.add_version(result.revised_text)
    
    # 如果是审阅工具 → 缓存结果
    if tool_call.name in REVIEW_TOOLS:
        review_cache.store(result)
    
    # 更新成本追踪
    cost_tracker.record(tool_call)
```

### 7.3 run_parallel_review 工具设计（修订版）

```python
async def run_parallel_review(
    paper_content: str,
    enable_search: bool = False,
    focus_areas: list[str] = None
) -> ReviewResult:
    """
    并行执行多角色审阅。
    
    关键改进（v2）：
    - 每个 reviewer 子 Agent 可以使用搜索工具（当 enable_search=True）
    - 支持 focus_areas 只启动特定角色
    - 返回结构化结果，含 severity 分级
    """
    roles = [EDITOR, THEORY, METHODOLOGY, LOGIC, LITERATURE]
    
    if focus_areas:
        roles = [r for r in roles if r.domain in focus_areas]
    
    # 为每个 reviewer 配置工具集
    reviewer_tools = []
    if enable_search:
        reviewer_tools = [search_academic, verify_reference, search_web]
    
    # 并行执行
    results = await asyncio.gather(*[
        reviewer_agent(role, paper_content, tools=reviewer_tools) 
        for role in roles
    ])
    
    # 去重合并 + severity 排序
    consolidated = consolidate_and_rank(results)
    
    return ReviewResult(
        issues=consolidated,
        summary=generate_summary(consolidated),
        search_evidence=collect_search_evidence(results),  # 搜索证据
        raw_reviews={r.name: res for r, res in zip(roles, results)}
    )
```

### 7.4 diff_review 工具设计（v2 新增）

```python
async def diff_review(
    original: str,
    revised: str,
    target_issues: list[Issue],
    enable_search: bool = False
) -> DiffReviewResult:
    """
    针对性复审：检验修改是否解决了目标问题。
    
    与 run_parallel_review 的区别：
    - 不是从零审阅，而是带着"修改要解决的问题"去检验
    - 输出聚焦于 resolved/unresolved/new_issues
    - 更轻量（通常不需要5角色全开）
    """
    # 生成 diff
    diff = compute_diff(original, revised)
    
    # 逐个检验 target_issues
    resolution_check = await check_issue_resolution(
        diff=diff,
        issues=target_issues,
        revised_text=revised,
        enable_search=enable_search
    )
    
    # 检查是否引入新问题
    new_issue_check = await scan_for_new_issues(
        diff=diff,
        revised_text=revised
    )
    
    return DiffReviewResult(
        resolved=resolution_check.resolved,
        unresolved=resolution_check.unresolved,
        new_issues=new_issue_check.issues,
        quality_delta=compute_quality_delta(resolution_check, new_issue_check),
        diff_summary=summarize_diff(diff)
    )
```

### 7.5 VersionStore 设计（v2 新增）

```python
class VersionStore:
    """
    管理论文的版本历史。Harness 层组件。
    
    为什么需要：
    - 复审需要对比 original vs revised
    - 迭代修改产生多个版本（v1→v2→v3）
    - Token Pipeline 需要知道"当前版本是哪个"以避免重复传递旧版本
    """
    
    def __init__(self):
        self.versions: list[Version] = []
    
    def add_version(self, text: str, metadata: dict = None) -> Version:
        """记录新版本"""
        version = Version(
            id=len(self.versions),
            text=text,
            metadata=metadata,
            timestamp=now()
        )
        self.versions.append(version)
        return version
    
    def get_latest(self) -> Version:
        """获取最新版本"""
        return self.versions[-1] if self.versions else None
    
    def get_original(self) -> Version:
        """获取原始版本"""
        return self.versions[0] if self.versions else None
    
    def get_diff(self, v1_id: int, v2_id: int) -> Diff:
        """对比两个版本"""
        return compute_diff(self.versions[v1_id].text, self.versions[v2_id].text)
    
    def get_summary(self) -> str:
        """供 Token Pipeline 使用的摘要"""
        if not self.versions:
            return ""
        return f"论文版本历史：共 {len(self.versions)} 个版本，" \
               f"原始版本于 {self.versions[0].timestamp}，" \
               f"最新版本于 {self.versions[-1].timestamp}"
```

### 7.6 goal_tracker.py 重构

**删除**：Phase enum、PHASE_TRANSITIONS、所有 phase 相关方法

**保留并增强**：
```python
@dataclass
class Goal:
    id: str
    description: str      # 用户想达成什么
    status: str           # active / completed / blocked
    created_at: datetime
    context: str          # 为什么设这个 goal
    
class GoalTracker:
    """追踪用户的目标，帮助 Agent 在长对话中保持方向。
    不做任何流程控制——只提供信息。"""
    
    def set_goal(self, description, context) -> Goal
    def complete_goal(self, goal_id, summary) -> None
    def get_active_goals(self) -> list[Goal]
    def get_context_injection(self) -> str  # 供 agent_loop 注入
```

### 7.7 deai_pipeline 工具设计

```python
async def deai_pipeline(
    text: str,
    mode: str = "full",  # "full" | "detect_only" | "rewrite_only"
    max_iterations: int = 3
) -> DeAIResult:
    """
    去 AI 痕迹的完整流水线。
    内部三步循环：detect → rewrite → verify
    """
    if mode == "detect_only":
        signals = await detect_ai_signals(text)
        return DeAIResult(signals=signals, rewritten=None)
    
    for i in range(max_iterations):
        signals = await detect_ai_signals(text)
        if not signals.has_issues:
            return DeAIResult(signals=signals, rewritten=text, iterations=i)
        
        text = await rewrite_for_signals(text, signals)
        
        if mode == "rewrite_only":
            return DeAIResult(signals=signals, rewritten=text, iterations=i+1)
        
        verification = await verify_no_ai_signals(text)
        if verification.passed:
            return DeAIResult(signals=signals, rewritten=text, 
                            iterations=i+1, verified=True)
    
    return DeAIResult(signals=signals, rewritten=text, 
                     iterations=max_iterations, verified=False)
```

### 7.8 工具集完整清单

**目标**：从 60 个工具精简到 ~22 个，每个边界清晰。

```
[审阅 - 3个]
• run_parallel_review(paper, enable_search?, focus_areas?)
    5角色并行深度审阅，可选搜索增强
• diff_review(original, revised, target_issues, enable_search?)
    针对性复审，对比修改前后
• quick_review(text, aspect)
    单维度快速审阅（不并行）

[修改 - 4个]
• rewrite_section(text, issue, strategy)
    大幅改写（逻辑/内容重构）
• patch_section(text, specific_fix)
    精确局部修改
• restructure_section(sections, new_structure)
    结构调整（移动/合并/拆分）
• polish_section(text, style_target)
    风格润色

[去AI味 - 1个]
• deai_pipeline(text, mode, max_iterations)
    完整去AI流水线

[搜索 - 3个]
• search_academic(query, databases?)
    搜索学术数据库
• verify_reference(citation)
    验证参考文献准确性
• search_web(query)
    搜索网络

[论文操作 - 3个]
• parse_paper(file)
    解析论文为结构化内容
• read_section(paper, section_id)
    读取特定章节
• get_paper_metadata(paper)
    获取论文元数据

[元工具 - 5个]
• ask_user(question)
    向用户提问/确认
• set_goal(description, context)
    设置/更新当前目标
• report_progress(summary)
    汇报进度
• save_checkpoint() / load_checkpoint()
    保存/恢复状态

[版本管理 - 3个]（Harness 层暴露给 Agent）
• store_version(text, label)
    保存当前版本
• compare_versions(v1, v2)
    对比两个版本
• get_version_history()
    查看版本历史
```

---

## 八、执行计划（按依赖关系排序）

### Phase 1：清理——删除 Theater 代码 [30min]

**动作**：
1. 删除 `tools/impact_estimator.py`、`tools/intent_classifier.py`、`tools/decision_report.py`、`tools/deai_engine.py`
2. 从 `core/tool_schemas.py` 和 `core/tool_dispatch.py` 中移除对应定义
3. 从 `handlers/` 中移除相关调用

**验证**：项目仍能正常启动

### Phase 2：核心——重构 goal_tracker.py + 新建 Harness 组件 [60min]

**动作**：
1. 删除 Phase enum 和 PHASE_TRANSITIONS
2. 简化为纯 Goal tracking
3. 新建 `core/harness/version_store.py`
4. 新建 `core/harness/token_pipeline.py`
5. 新建 `core/harness/review_cache.py`

**验证**：各 Harness 组件可独立运行

### Phase 3：核心——删除 phase_filter + 解耦 agent_loop [60min]

**动作**：
1. 删除 `utils/phase_filter.py`
2. 重构 `core/agent_loop.py`——按 7.2 节设计重写
3. 集成 Token Pipeline
4. 集成 Doom Loop 检测
5. 集成错误恢复

**验证**：Agent 可以在任何时候调用任何工具，OTA 循环正常运转

### Phase 4：核心——重写 System Prompt [60min]

**动作**：
1. 按照 7.1 节完全重写 `core/prompts.py`
2. 包含：身份、能力维度、意图理解框架、迭代思考框架、工具指导、质量标准
3. 保留动态注入机制（budget mode、workspace path）

**验证**：用不同类型的用户输入测试 Agent 的规划能力

### Phase 5：封装——构建 run_parallel_review（v2）[90min]

**动作**：
1. 基于现有 `tools/review_engine.py` 重构
2. 新增 `enable_search` 参数——reviewer 子 Agent 有搜索工具
3. 新增 `focus_areas` 参数——可选子集审阅
4. 保留核心 5 角色并行逻辑
5. 增强 consolidation（去重 + severity 排序 + 搜索证据收集）

**验证**：`run_parallel_review(paper, enable_search=True)` 能返回含搜索证据的审阅结果

### Phase 6：新建——构建 diff_review [60min]

**动作**：
1. 新建 `tools/diff_review.py`
2. 实现 7.4 节设计
3. 包含：diff 计算、issue resolution 检验、新问题扫描
4. 与 version_store 集成

**验证**：给定原文+修改+目标issues，能返回 resolved/unresolved/new_issues

### Phase 7：封装——构建 deai_pipeline 统一工具 [60min]

**动作**：
1. 整合 `tools/deai/` 目录
2. 创建统一入口
3. 删除 6 个旧入口

**验证**：`deai_pipeline("AI味文本", "full")` 正确执行

### Phase 8：整理——精简工具集 + 新增修改工具 [60min]

**动作**：
1. 按 7.8 节清单整理所有工具
2. 新增 `patch_section`、`restructure_section`、`polish_section`
3. 新增版本管理暴露工具
4. 为每个工具写清晰描述

**验证**：Agent 面对不同请求选择合理的工具

### Phase 9：端到端测试与调优 [120min]

**测试用例**：
1. "帮我完整审阅这篇论文"（只审阅，不改）
2. "帮我审阅完修改，确保质量"（完整迭代循环）
3. "只看看第三章的逻辑"（聚焦审阅）
4. "帮我把这段去掉 AI 味"（De-AI）
5. "帮我查查引文是否准确"（搜索增强审阅）
6. "只帮我改改语言，不用审阅"（直接修改）
7. "改完再帮我看看"（修改+复审循环）
8. "给我一些总体建议就好"（轻量）

**验证标准**：每种场景 Agent 行为自然、合理、不做无用功

---

## 九、执行顺序与依赖关系

```
Phase 1 (清理) ─────────────────────────────────────────┐
                                                         │
Phase 2 (Harness 组件) ──┐                              │
                          │                              │
Phase 3 (解耦 loop) ─────┤── 核心三件套                 │
                          │    (强依赖)                  │
Phase 4 (重写 prompt) ───┘                              │
                                                         │
Phase 5 (review v2) ── 可与 Phase 6/7 并行 ─────────────┤
                                                         │
Phase 6 (diff_review) ── 可与 Phase 5/7 并行 ───────────┤
                                                         │
Phase 7 (deai_pipeline) ── 可与 Phase 5/6 并行 ─────────┤
                                                         │
Phase 8 (工具精简) ── 依赖 Phase 5+6+7 ────────────────┤
                                                         │
Phase 9 (端到端测试) ── 依赖全部 ───────────────────────┘
```

**推荐执行顺序**：
- 串行核心：Phase 1 → Phase 2 → Phase 3 → Phase 4
- 并行封装：Phase 5 || Phase 6 || Phase 7
- 收尾：Phase 8 → Phase 9

---

## 十、风险与缓解

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| Agent 迭代循环不收敛（无限审→改→审） | 中 | 高 | System Prompt 明确退出标准 + doom loop 检测 |
| 搜索增强审阅成本过高 | 中 | 中 | 默认 enable_search=False，Agent 判断需要时才启用 |
| Token Pipeline 压缩丢失关键信息 | 中 | 高 | 审阅结果和当前 goal 永远高优保留 |
| 工具精简后 Agent 选择困难 | 低 | 低 | 22 个工具 + 清晰描述，足够 LLM 选择 |
| 版本管理引入额外复杂度 | 低 | 低 | VersionStore 接口极简，只有 add/get/diff |
| 用户意图理解错误 | 中 | 中 | Prompt 引导"不确定就问一句" |

---

## 十一、成功标准

重构完成后，ScholarAgent 应该满足：

1. **自主性**：给一个新用户请求，Agent 自己规划合理步骤，不需要预定义流程
2. **灵活性**：面对"只给建议"/"完整审阅+修改"/"只去AI味"等不同需求，行为明显不同
3. **迭代能力**：能自主进入和退出 审阅→修改→复审 循环，且能收敛
4. **搜索增强**：审阅时能按需搜索验证引文/方法论
5. **效率**：不做无意义的仪式动作，不 over-plan
6. **核心能力不退化**：5-role review 质量不降低，De-AI 效果不降低
7. **成本可控**：总 token 消耗不超过重构前的 130%（搜索增强场景允许更高）
8. **可对话**：用户可以随时追问、调整方向，Agent 自然响应

---

## 附录 A：关于"什么时候 spawn 子 Agent"的判断标准

| 场景 | 为什么适合并行 | 子 Agent 数量 | 子 Agent 工具 |
|------|--------------|-------------|-------------|
| 5-role parallel review | 5 角色完全独立 | 5（可选少于5） | 各自的审阅 prompt + search_academic + verify_reference |
| 多文献并行搜索 | 搜不同数据库 | 2-3 | search_academic / search_web |
| 批量段落去AI味 | 每段独立 | N（按段数） | 内部 detect+rewrite |
| diff_review 中的多 issue 并行检验 | 每个 issue 检验独立 | N（按issue数） | 对比分析 prompt |

**所有其他场景**由主 Agent 一个大脑完成。

---

## 附录 B：Harness Engineering 核心概念映射

| Harness 概念 | ScholarAgent 中的对应 |
|-------------|---------------------|
| REPL 容器 | agent_loop.py 的 OTA 循环 |
| Token Pipeline | token_pipeline.py (Collect→Rank→Compress→Budget→Assemble) |
| 状态分离 | Harness 管状态（GoalTracker, VersionStore, ReviewCache），LLM 管决策 |
| Plan-and-Execute | Agent 在 prompt 引导下自主 plan，通过工具 execute |
| 异常重规划 | 工具失败 / diff_review 不通过 → Agent 自然调整计划 |
| OTA 循环 | Observe(context) → Think(LLM) → Act(tool/reply) |
| 幂等操作 | 工具 retry 安全（review/search 天然幂等） |
| R.E.S.T 框架 | Reliability(retry+doom_loop) / Efficiency(token_pipeline) / Safety(成本控制) / Traceability(goal_tracker+version_store) |

---

## 附录 C：被删除模块的"精神继承"

| 被删除的模块 | 它试图解决的问题 | 在新架构中如何解决 |
|-------------|----------------|------------------|
| Phase SM | 确保 Agent 不跳步 | Agent 自主规划 + 质量标准引导 |
| Phase Filter | 避免工具太多 | 精简到 22 个 + 好描述 |
| Intent Classifier | 理解用户意图 | LLM 自身能力 + prompt 意图框架 |
| Impact Estimator | 评估修改影响 | Agent 思考中评估 |
| Decision Report | 记录决策依据 | Agent 回复中自然说明 |
| Adaptive Strategy | 动态调策略 | Agent 自主规划即 adaptive |
| Self-Reflection | 确保质量 | diff_review 工具 + Agent 自主判断 |
| Meta-Planner | 全局规划 | System Prompt 思考框架 |

---

*文档版本 v2.0。核心修正：迭代循环设计、搜索增强审阅、用户意图多样性处理、Harness Engineering 哲学引入。等待确认后开始执行。*
