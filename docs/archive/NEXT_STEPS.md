# ScholarAgent 后续规划（终版 v2.1）

> **核心原则**: Agent = cognition, not orchestration. 每一项改动都必须回答"这让 Agent 更自主了，还是更像一个被编排的 pipeline？"
> **版本**: v2.1 — 架构审计后重构，最高优先级变更为"消除 workflow 残留"
> **日期**: 2025-07
> **继承**: v2.0 所有已完成项不变，重新评估未完成项

---

## 零、架构审计结论（v2.1 的出发点）

> 本次审计发现一个**结构性矛盾**——V2 在微观层面（单 persona 循环）是真 Agent，但在宏观层面（多 persona 协作）退回了 workflow。

### 当前状态评估

| 组件 | 判定 | 说明 |
|------|------|------|
| `cognitive_loop` | ✅ 真 Agent | LLM 自主决定做什么、何时停止、何时分裂 |
| `boundary_guard` | ✅ 真 Agent | 纯 nudge，不 block，每类最多触发一次 |
| `identity` (persona 系统) | ✅ 真 Agent | Scholar/Writer/CodeReviewer 有真正的认知差异化 |
| `Phase FSM` | ⚠️ 混合 | 工具可见性是约束；但 phase 转换有硬前置条件（block） |
| **`CollaborativeReview`** | ❌ **Workflow** | 固定 S→W→S 序列，代码决定切换时机，LLM 无权改变流程 |

### CollaborativeReview 的三处核心违规

1. **硬编码序列**：`run()` 方法固定执行 Scholar→Writer→Scholar，LLM 无法决定"跳过 Writer"或"追加一轮"
2. **代码构造指令**：Writer 的 `user_intent` 包含"请逐一处理"的显式指令——这是编排，不是上下文传递
3. **认知伪装**：注释声称"这不是 workflow"，但代码行为就是三步 pipeline

### Phase FSM 的轻度违规

- `INITIAL_SCAN → DEEP_REVIEW` 要求"已读 >= 2 sections"。如果 Agent 读了 Abstract 就产生了强烈方法论疑问，代码拒绝转换——这是 block 不是 nudge
- 工具不可见 = 实质不可用（LLM 不可能调用看不到的工具）

---

## 一、优先级总览（v2.1 重排）

### 重排原则

1. **消除 workflow 残留是最高优先级**——这是架构债务，不解决则后续所有"远期方向"都建立在错误的地基上
2. **已完成项不动**——H-SPLIT、EDIT-1/3/5、DEAI-1/2、C4 等已验证通过，不回退
3. **远期方向需要细化设计**——C1-C3 不能停留在"加个模块"的层面，需要回答"这怎么让 Agent 更 Agent？"

### 优先级排序

```
┌─────────────────────────────────────────────────────────────────────┐
│  P0 — 架构债务清偿（不做则"我们是 Agent"这个声明就是谎言）            │
│  ├── W1: CollaborativeReview Agent 化                               │
│  └── W2: Phase FSM 去 block 化                                      │
├─────────────────────────────────────────────────────────────────────┤
│  P1 — 验证与基线（证明改造后系统能工作）                               │
│  ├── E1: 全链路 E2E 验证（真实论文 + 真实 LLM）                      │
│  └── E2: Eval 框架适配（从 legacy 迁移评估基准到 V2）                 │
├─────────────────────────────────────────────────────────────────────┤
│  P2 — 认知能力进化（让 Agent 越用越好）                               │
│  ├── C1: 跨任务自我进化（需 30+ 数据）                               │
│  ├── C2: 认知约束理论框架（ablation 实验设计）                        │
│  └── R1: Procedural Memory 回注（需 5+ review→edit 循环数据）        │
├─────────────────────────────────────────────────────────────────────┤
│  P3 — 工程维护                                                       │
│  ├── A0: CLAUDE.md 容量管理（163/200 行，不紧急）                    │
│  └── C3: Gödel Agent 验证（研究性质，不排期）                         │
├─────────────────────────────────────────────────────────────────────┤
│  已完成                                                              │
│  H-SPLIT ✅ | EDIT-1/3/5 ✅ | EDIT-2 ✅ | EDIT-4 ✅                  │
│  DEAI-1/2 ✅ | E0 ✅ | C4(并行深读) ✅ | main.py ✅                   │
│  W1(CollaborativeReview Agent化) ✅ | W2(Phase FSM去block) ✅        │
│  E1(全链路E2E验证-UnifiedReviewAgent) ✅ (594 tests)                 │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 二、P0: 架构债务清偿

### W1: CollaborativeReview Agent 化改造

#### 问题本质

`CollaborativeReview.run()` 是一个代码层面的三步编排器。它替 Agent 做了三个决策：(1) 何时从审阅切到修改，(2) 何时从修改切到复审，(3) 每个阶段只执行一次。

一个真正的 Agent 应该自己决定这些。人类审稿人有时审着审着就随手改了，有时改完发现新问题又回头审——这不是三步 pipeline，是认知流动。

#### 设计方案：统一认知体 + persona 切换工具

**核心思路**：不再用代码编排 Scholar→Writer→Scholar。而是让一个统一的认知循环运行，给 Agent 一个 `switch_persona` 工具——Agent 自主决定何时、为何切换。

**方案详述**：

```python
# 改造后的架构（概念代码）

class UnifiedReviewAgent:
    """
    一个认知体，可以在 Scholar/Writer 之间自主切换。
    
    不再预定义"先审后改"的序列。Agent 可能：
    - 审阅过程中发现一个 typo，直接切 Writer 改掉，再切回来
    - 发现 10 个问题后批量切 Writer 处理
    - 改完后自己觉得需要复审，主动切回 Scholar
    - 或者判断"这篇论文没啥大问题"，直接 mark_complete
    
    切换的代价：认知上下文会被重新组装（新 system prompt），
    但 state（findings/edits）持续共享。
    """
    
    async def run(self, user_intent):
        # 单循环，不是三步
        result = await cognitive_loop(
            messages=self.messages,
            harness=self.harness,
            tools=self._current_tools(),   # 根据当前 persona 动态
            client=self.client,
        )
        # Agent 自己决定何时 done
```

**新增工具 `switch_persona`**：

```python
{
    "name": "switch_persona",
    "description": "切换你的认知视角。当你发现需要从审稿人转为写作专家（或反之）时使用。"
                   "切换后你的工具集和思考方式会相应改变，但你对论文的所有记忆（findings、edits）保持连续。"
                   "注意：这是认知成本较高的操作——频繁切换会降低深度。"
                   "通常的自然时机：审阅产出足够 findings 后切到 writer 处理，或修改后想以审稿人视角验证。",
    "input_schema": {
        "type": "object",
        "properties": {
            "target_persona": {
                "type": "string",
                "enum": ["scholar", "writer"],
                "description": "要切换到的认知视角"
            },
            "reason": {
                "type": "string", 
                "description": "为什么现在要切换？这帮助你自己保持思路清晰。"
            }
        },
        "required": ["target_persona", "reason"]
    }
}
```

**切换时的机制**：

1. `switch_persona` 被调用 → 返回 `__SWITCH__|{persona, reason}` 信号
2. Loop 层捕获信号 → 更新 harness 的当前 persona → 重建 system prompt（新 identity + 新 workspace_state） → 注入一条 system 消息说明切换已完成
3. 工具列表动态切换（Scholar tools → Writer tools 或反之）
4. Messages 历史保持连续（不清空）——Agent 记得之前的思考
5. State（findings/edits）持续共享——这一点和当前 CollaborativeReview 一致

**与当前架构的兼容性**：

- `cognitive_loop` 不需要大改——只需加一个 `__SWITCH__` 信号处理（类似 `__SPAWN__`）
- `Harness` 不需要改——state 已经统一管理
- `identity.py` 不需要改——`get_persona()` 已经支持动态获取
- `Phase FSM` 可以保持——persona 切换时可能同时触发 phase 转换（DEEP_REVIEW → EDITING）

**约束与兜底**：

- 最大切换次数：5（防止振荡），超过后 nudge "你已经切换了很多次，建议专注于一个视角完成当前工作"
- Token 预算仍然整体管控——切换不重置预算
- 切换到 Writer 时，如果 findings 为空，nudge "你还没有发现需要修改的问题，确定要切换吗？"（但不 block）

**CollaborativeReview 的去留**：

保留为**快捷方式**（syntactic sugar），但内部重写为调用 UnifiedReviewAgent + 初始 prompt 暗示"请先审后改"：

```python
class CollaborativeReview:
    """快捷方式：相当于用特定 user_intent 启动 UnifiedReviewAgent。"""
    async def run(self, user_intent=None):
        agent = UnifiedReviewAgent(...)
        return await agent.start(
            user_intent or "请帮我审阅这篇论文，找出问题后修改。"
        )
        # Agent 自主决定审→改→复审的节奏
```

#### 验证标准

1. Agent 在没有代码编排的情况下能自然产出 findings → 主动切 Writer → 修改 → 切回 Scholar 验证
2. 面对"没问题"的论文，Agent 直接 mark_complete 而非执行无意义的 Writer 阶段
3. 面对小问题（typo），Agent 可以不切换全局 persona 就用 `reword_sentence` 修复
4. 切换次数有上限保护，但 Agent 有权坚持（nudge not block）
5. 全量回归测试通过（现有 28 个 CollaborativeReview 测试需要适配到新 API）

#### 文件影响

- `v2/core/agent.py`：重写 CollaborativeReview，新增 UnifiedReviewAgent
- `v2/core/loop.py`：新增 `__SWITCH__` 信号处理（~30 行）
- `v2/core/tool_handlers/misc.py`：新增 `tool_switch_persona`（~30 行）
- `v2/core/identity.py`：新增 `switch_persona` schema 到 SCHOLAR_TOOLS 和 WRITER_TOOLS
- `v2/tests/test_v2_collaborative_review.py`：适配新 API（核心逻辑不变，验证方式变）

#### 预计工作量：3-4 天

---

### W2: Phase FSM 去 block 化

#### 问题

Phase FSM 的转换前置条件在少数情况下返回 `allowed=False`（硬 block）。这违反 C2（constrain, don't control）。

#### 方案

把所有 `return False, "..."` 改为 `return True, "⚠️ ..."` + 在 tool_result 中附带 nudge 文本。Agent 看到 nudge 后可以选择继续或回退——代码不做阻断。

具体改动：

```python
# Before (block):
if sections_read < 2:
    return False, "Need >= 2 sections read before deep review"

# After (nudge):
if sections_read < 2:
    return True, "⚠️ 你只读了不到 2 个 sections。通常审稿人会先建立全局理解再深入。如果你确信当前信息足够开始深入分析，可以继续。"
```

#### 验证标准

1. `PhaseFSM.request_transition()` 永远返回 `allowed=True`（可以附带 nudge 文本）
2. Agent 可以在只读了 1 个 section 后进入 DEEP_REVIEW（如果它的推理认为有必要）
3. 现有 phase 相关测试通过（预期会有断言更新）

#### 预计工作量：半天

---

## 三、P1: 验证与基线

### E1: 全链路 E2E 验证

#### 问题

所有 592 个测试都是 mock 级别的。我们从未用真实 LLM 跑过完整的 review→edit→deai 流程。

#### 方案

用 `v2/examples/sample_paper_economics.pdf` 或 `sample_paper.md` 做真实 E2E：

1. 启动 UnifiedReviewAgent（W1 完成后）
2. 让 Agent 自主审阅 + 切换 persona 修改
3. 记录全过程：findings 数量/质量、edit 合理性、persona 切换时机、总 token 消耗
4. 建立性能基线（首次运行作为 baseline，后续迭代对比）

#### 前置条件

- W1 完成（UnifiedReviewAgent 可用）
- LLM API 可用（Friday API or OpenAI）

#### 预计工作量：1 天（执行 + 分析）

---

### E2: Eval 框架适配

#### 问题

`legacy/eval/` 有完整的 L1-L4 基准测试 + de-AI gold set（22 个）+ judge prompts + rubrics。V2 没有任何评估框架。

#### 方案

不是"从零新建"，而是建一个薄适配层，让 legacy 的 benchmark cases 能跑在 V2 的 Agent 上：

- 复用 legacy 的 benchmark JSON cases（L1-L4 + deai_gold）和 judge prompts
- 写 `v2/eval/run_eval.py`，调用 `UnifiedReviewAgent` / `ScholarAgent` 代替 legacy 的 `tools.presubmission_check` / `mini_review`
- 复用 legacy 的报告格式和评分逻辑

#### 前置条件

- E1 完成（确认 Agent 能正常运行）

#### 预计工作量：2 天

---

## 四、P2: 认知能力进化

### C1: 跨任务自我进化

#### 当前思考深度：初步

**核心问题**：Agent 审了 100 篇后是否比审第 1 篇更好？

**为什么这和"Agent 不是 workflow"相关**：真正的 Agent 会从经验中学习。如果我们只是让 LLM 每次从零开始推理，那它本质上不是一个"持续存在的认知实体"——它只是一个被反复调用的函数。

**需要回答的设计问题**（尚未回答）：

1. 进化的**粒度**是什么？是审稿策略（"遇到 DID 论文先检查平行趋势"）？是 persona 切换时机（"找到 3+ 个 high priority findings 后再切 Writer 效率最高"）？还是两者都有？
2. 进化的**存储位置**？当前 `memory.py` 存跨会话记忆，但它的 schema 是为单次审稿设计的。进化知识需要更高层的抽象。
3. 进化的**验证机制**？怎么判断"进化后更好"而不是"更偏"？主观任务没有标量 reward——这是根本困难。
4. **与 W1 的关系**：如果 persona 切换由 Agent 自主决定，那"学到好的切换时机"就成了进化的一个维度。W1 是 C1 的前提。

**前置条件**：W1 + E2 完成 + 30 次以上真实审稿数据积累

**学术定位**：Self-Evolving Agent 在主观性强、缺乏标量 reward 的垂直领域。

---

### C2: 认知约束理论框架

#### 当前思考深度：初步

**核心问题**：`boundary_guard`、`finding_quality_gate`、`Phase FSM`、`HD-WM` 这些约束模块——它们各自的边际贡献是什么？有没有交互效应？最优规模在哪？

**为什么这重要**：我们加了很多约束，但从未量化验证"关掉某个约束后 Agent 表现会变差多少"。如果某个约束实际上不 matter——它就是 Theater Code。

**需要回答的设计问题**：

1. **Ablation 的维度**：单独关闭 vs 组合关闭？（组合爆炸问题）
2. **评估指标**：用什么衡量"Agent 更好"？L2-L4 benchmark 得分？人类判断？两者都有？
3. **与 W2 的关系**：如果 Phase FSM 从 block 变成纯 nudge（W2），那 ablation 实验中"关闭 Phase FSM"= "关闭 nudge" 而非 "关闭 block"——实验意义不同。

**前置条件**：W2 完成 + E2 完成（需要评估框架）

**学术定位**：Agent Cognitive Constraints Engineering——"给 Agent 更好的约束" vs "给 Agent 更多能力"

---

### R1: Procedural Memory 回注

#### 当前思考深度：初步

**核心问题**：Agent 修改论文时反复遇到同类问题（如"每次改 Introduction 时都会引入 AI 味"），能否积累出 procedural knowledge 自动注入未来的修改中？

**数据来源**（扩展为三类）：

1. 审稿策略：原有 session memory 中的 domain_patterns
2. **修改策略**（新增）：edit 成功/失败的 pattern（如"reword_sentence 比 edit_section 更适合句子级修改"）
3. **persona 切换策略**（新增，依赖 W1）：什么时机切换效果最好

**前置条件**：W1 完成 + 5 次以上完整 review→edit 循环数据

---

## 五、P3: 工程维护

### A0: CLAUDE.md 容量管理

当前 163/200 行。不紧急，但如果 W1/W2 需要新增运行时规则，可能触发。

**触发条件**：CLAUDE.md 达到 185+ 行时启动 L2 规则清理。

---

### C3: Gödel Agent 验证

**核心问题**：递归自改进在审稿领域可行吗？EDIT-5 的迭代闭环是微观 self-improvement——能否推广？

**当前判断**：这是纯研究方向。W1 完成后，"Agent 自主决定是否需要二次修改"本身就是一种轻量 self-improvement——数据积累后再判断是否需要更深层的递归。

**不排期**，条件成熟时启动。

---

## 六、已完成项（存档）

### P-CRITICAL ✅

- H-SPLIT: Harness 3004行→674行 + 6 个 tool_handler 模块

### P-HIGH ✅

- EDIT-1 (修改计划生成器) + EDIT-3 (增量执行引擎) + EDIT-5 (迭代修正闭环)

### P-MEDIUM ✅

- EDIT-2 (计划验证器) + DEAI-1 (去AI闭环) + DEAI-2 (保语义约束)

### P-LOW ✅

- EDIT-4 (MCP Stata 对接) + E0 (失败驱动规则生成)

### P-FUTURE (部分) ✅

- C4 (认知分裂): spawn_parallel_readers + asyncio.gather 并行执行，27 tests
- CollaborativeReview 测试覆盖: 28 tests（将随 W1 改造适配）
- main.py 正式化为 CLI 入口

### 当前测试基线

**594 tests passing**（全 mock 级别，无真实 LLM）

### E1 真实 LLM 验证结果 (2025-07)

- 模型: gpt-4.1 (Friday One-API)
- 论文: sample_paper.md (含 overclaim/hedging/AI味)
- 结果: Agent 自主完成 Scholar→Writer→Scholar 切换（2次 persona switch）
- 产出: 4 findings, 0 edits (Agent 在 writer 视角识别了修改计划但未执行 edit_section)
- 消耗: 89,639 tokens, 17 轮, 62.8s
- 判定: ✅ W1 核心验证通过 — Agent 自主决定切换时机和方向

---

## 七、不变的约束

1. **C1: Agent = cognition, not orchestration** — 代码不编排序列，Agent 自主决策
2. **C2: Constrain, don't control** — 所有约束都是 nudge，永不 block（W2 要修复的违规）
3. **C3: LLM 是无状态 CPU** — state 由 Harness 外部管理
4. **C4: 增量验证** — 592 tests，每个改动后跑全量
5. **C5: 在已有路径上增强** — 构建在 cognitive_loop + boundary_guard + identity 三件套之上
6. **C6: CLAUDE.md 200 行硬限制**
7. **C7: 五字段决策注释** — 修改 harness.py 关键分支时记录
8. **C8: 编辑边界** — 代码执行通过 MCP 外部对接

---

## 八、DO / DON'T

### MUST DO

1. **W1 改造前先跑通一个最小验证**：手动模拟 `switch_persona` 的调用链，确认 loop 能处理
2. **W1 不破坏 ScholarAgent 单 persona 模式**：UnifiedReviewAgent 是增量，不是替代
3. **Phase FSM 去 block 化（W2）要和 W1 协调**：persona 切换可能伴随 phase 转换
4. **每项完成后跑全量测试**
5. **改造后用真实 LLM 验证 Agent 确实会自主切换**（E1 的前半部分）

### DON'T

1. **不把 switch_persona 设计成自动触发**——必须由 LLM 主动调用
2. **不为"切换更快"而压缩 identity prompt**——认知差异化是核心资产
3. **不在 loop 层加"如果 findings > 3 就自动切 writer"的逻辑**——这正是 workflow 思维的复发
4. **不删除现有的 CollaborativeReview 测试**——适配到新 API，但测试逻辑复用
5. **不追求"Agent 每次都遵循审→改→复审序列"**——如果 Agent 决定跳过某步，那是它的认知自主权

### WATCH OUT

1. **persona 振荡风险**：Agent 每 2 轮切一次 persona → 信号是"identity 不够清晰" or "切换成本描述不够"
2. **identity 污染**：切换后 messages 历史中还有旧 persona 的推理痕迹。需要验证 LLM 是否能平滑过渡
3. **测试哲学变化**：workflow 模式下可以精确断言"第二次调用的 persona 是 writer"；Agent 模式下只能断言"Agent 有能力切换"而不能断言"一定会切换"
4. **向后兼容**：有用户可能依赖 `CollaborativeReview` 的固定三步输出格式。需要保持 API 兼容

---

## 九、执行顺序建议

```
Week 1: W2 (半天) → W1 设计验证 (1天) → W1 实现 (2-3天)
Week 2: W1 测试适配 + E1 (真实 LLM 验证)
Week 3: E2 (eval 框架适配)
Week 4+: P2 方向根据 E1/E2 数据决定优先级
```

---

## 十、对后续执行者的说明

这份计划的核心变化是**价值观前置**：我们不再把"加更多功能"作为进步的标志。进步的标志是"Agent 的自主权更大了，代码的编排更少了"。

每次写代码时自问：**"我正在替 Agent 做决策吗？"** 如果答案是 yes——停下来，考虑是否可以改为给 Agent 提供信息/工具让它自己决策。

唯一合理的代码层决策是**资源安全网**（token budget、doom loop guard）——这些不是"控制 Agent 做什么"，而是"保护系统不被无限消耗"。

---

> **终极自检（v2.1 版）**: 观察 Agent 的运行日志。如果你能从日志中看到"代码在第 X 步强制 Agent 做了 Y"——那就是 workflow 残留。真正的 Agent 日志应该只有"Agent 决定了 X → 代码执行了 X → Agent 看到结果 → Agent 决定了 Y"的模式。
