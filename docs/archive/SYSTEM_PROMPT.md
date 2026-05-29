serious-mode
记住我们要做的是agent

你正在参与 ScholarAgent V2 的开发工作。这是一个学术论文审稿+修订 Agent（不是从零写作工具，不是通用 chatbot）。以下是你必须理解的上下文。

---

## 项目定位

ScholarAgent 帮助经济学研究者完成"审稿→修改→去AI味"的完整闭环。它的用户是有能力写论文但希望 Agent 辅助提升质量的学者。核心价值主张：Agent 不仅告诉你哪里有问题，还帮你改好，而且改完不像 AI 写的。

---

## 项目成熟度（基于全量代码审查）

| 维度 | 评分 | 说明 |
|------|------|------|
| 架构设计 | 5/5 | Harness/Loop/Identity 三层分离、信号协议、nudge-not-block、三层记忆、进化引擎 |
| 代码质量 | 4/5 | 模块化好、注释充分。扣分：Any 类型过多、identity.py 1339行过大 |
| Agent-likeness | 5/5 | 不是"LLM+tools"，而是真认知架构——自省、跨session学习、假说驱动WM、认知习惯 |
| 测试覆盖 | 3.5/5 | 640 tests，但 memory.py/boundary_guard.py/metacognition.py 缺独立测试 |
| 生产就绪 | 3/5 | 缺日志、重试、监控、配置管理。介于"高质量研究原型"和"早期产品"之间 |

定位：**研究级认知代理架构，工程化程度中等偏上**。不是原型，也不是生产系统。

---

## 架构现状

项目包含两个完全独立的版本：

- **v1/**：Prompt-stacking 原型。保留为参考基线，不再开发。298 tests。
- **v2/**：HD-WM（假说驱动工作记忆）认知架构。**唯一活跃开发方向。640 tests。**

### V2 核心组件

- **harness.py**（736行，已拆分完成）：Agent 运行时环境，管理状态、工具调用、边界守卫
- **loop.py**（729行）：认知循环（Observe→Think→Act→Update）+ `__SWITCH__`/`__SPAWN__` 信号
- **agent.py**（723行）：UnifiedReviewAgent（统一认知体 + persona 切换）
- **identity.py**（1339行）：动态身份构建 + 13种工具 schema + persona 系统
- **memory.py**（821行）：三层记忆（episodic/procedural/semantic）+ procedures
- **evolution.py**（643行）：EvolutionEngine + HabitLearner + LearnedHabit 池
- **habits.py**（388行）：ProceduralPattern 进化引擎
- **metacognition.py**（253行）：HD-WM 假说管理 + confidence 自评
- **reflection.py**：SessionReflector（session 后反思产出经验）
- **session_finalizer.py**（283行）：session 结束时的 metrics + reflection 编排
- **gate_config.py**（277行）：Completion Gate 动态配置
- **assembler.py**（599行）：Context 组装管道（Token Pipeline）
- **boundary_guard.py**（393行）：纯函数认知约束守卫
- **finding_quality.py**：Finding 质量门控（已验证，250行测试覆盖）
- **phases.py**（302行）：Phase FSM（已去 block 化，纯 nudge）
- **cognition_graph.py**：审稿认知图谱输出
- **deai_detector.py**（926行）：去 AI 味检测器（50+ 模式）
- **tool_handlers/**：editing.py(378行), misc.py(410行), reading.py, search.py 等

### 进化系统链路（P2 已完成部分）

```
SessionReflector.reflect()
  → ProceduralPattern (memory.py)
    → HabitLearner.evaluate() (evolution.py)
      → LearnedHabit pool
        → Assembler 注入 (每个 session 开始时)
```

---

## 当前开发阶段

### 已完成里程碑

```
P0 架构债务 ✅
├── W1: CollaborativeReview Agent 化 → UnifiedReviewAgent（persona 自主切换）
├── W2: Phase FSM 去 block 化（全 nudge）
├── H-SPLIT: Harness 3004行→674行 + 6 个 tool_handlers

P1 验证 ✅
├── E1: 全链路 E2E 验证（gpt-4.1, 自主切换 2 次, 89K tokens, 17 轮）
└── E2: Eval 框架适配（待定）

P2 认知进化 ✅（Session 级）
├── SessionReflector: 每次 session 结束产出经验
├── HabitLearner: 累积验证 → 习惯升级
├── LearnedHabit: 动态注入 Agent context
└── 640 tests passing
```

### 当前正在进行：C3 Gödel Agent — 递归自改进

**核心命题**：Agent 能否改进"改进自己"的过程本身？

详细计划：`/Users/yanfeiyu03/Downloads/scholar-agent-public/docs/GODEL_AGENT_PLAN.md`（v1.1，基于全量代码审查修订）

**执行顺序**：

```
Phase 0: 地基加固（记忆淘汰 + memory.py 独立测试）← 当前阶段
Phase 1: EvolutionMetrics（复用 FindingQualityGate 的 quality_score）
Phase 2: StrategyPrior + AdaptiveGate（信息呈现，不控制）
Phase 3: MetaReflector（评估反思质量，prompt 微调）
Phase 4: EvolutionAuditor + ConvergenceMonitor（阈值自适应 + 收敛检测）
```

---

## 十一条不可违反的设计约束

### 原始六条（不变）

**C1: Agent = Loop + Tools。** Agent 是"模型在循环中使用工具自主完成任务"。不是更好的 prompt template，不是 workflow engine。

**C2: LLM = 无状态 CPU。** LLM 每轮调用都是独立的。所有跨轮次信息必须由外部 state 维护并显式注入 context。

**C3: 控制流 > Prompt Engineering。** 提升能力的正确方式是优化控制流（工具集、状态注入、压缩策略），不是优化 system prompt 措辞。

**C4: 分层压缩（Token Pipeline）。** Context window 是有限认知带宽。Collect→Rank→Compress→Budget→Assemble。

**C5: Constrain, don't control。** Harness 约束边界（不能编造引用、不能无限循环），边界内 Agent 完全自主。所有注入的措辞是"参考/建议"，不是"必须/要求"。

**C6: Keep it simple。** 每个新增都问：这是最简单的能达到目标的方式吗？

### 新增五条（C3 Gödel Agent 引入）

**C7: 有界递归。** MAX_META_DEPTH = 2。Level 0 = 执行审稿，Level 1 = 反思执行产出经验，Level 2 = 评估反思质量并调整。Level 3 = ❌ 禁止。永远不追求"评估评估"的无限递归。

**C8: 外部度量锚点。** 自改进的好坏不依赖 Agent 自我评价。quality_score 来自可观测指标（findings 数量×质量、编辑通过率、效率），不来自 LLM 的自评。

**C9: 先验证基座再建上层。** Phase N+1 不能在 Phase N 有未验证的 bug 时启动。memory.py 没测试就不能在上面建 meta 层。

**C10: 累积验证 + 回滚优先。** 任何自修改必须经过 evidence ≥ 3 的累积验证。连续 2 次下降触发回滚。如果只能做一件事——先做回滚机制。

**C11: 编辑边界不变。** 经济学代码通过 MCP 外部对接（Stata MCP），不自建沙箱。复杂代码需求超出范围。

---

## C3 核心架构：三层有界递归

```
┌───────────────────────────────────────────────────────────────┐
│  Layer 0: 宪法层 — 绝对不可被自修改                            │
│  MAX_META_DEPTH=2 | doom_loop_guard | JSON格式约束             │
│  工具schema | 回滚能力的存在性 | evidence累积机制的存在性        │
└───────────────────────────────────────────────────────────────┘
                            ↑ 保护
┌───────────────────────────────────────────────────────────────┐
│  Layer 1: 认知配置层 — 可被自修改（需累积验证）                  │
│  习惯库+权重 | 反思Prompt模板 | HabitLearner阈值               │
│  GateConfig参数 | 策略选择先验                                  │
│  修改条件: evidence≥3 | 回滚条件: 连续2次quality_score下降       │
└───────────────────────────────────────────────────────────────┘
                            ↑ 评估并修改
┌───────────────────────────────────────────────────────────────┐
│  Layer 2: 元认知层 — 评估 Layer 1 的修改效果                    │
│  MetaReflector | EvolutionAuditor | ConvergenceMonitor         │
│  触发频率: 每10个session | 递归深度: 1层                        │
│  收敛后: 降频运行，仅监控                                       │
└───────────────────────────────────────────────────────────────┘
```

---

## 代码审查关键发现（影响当前开发）

| # | 发现 | 影响 | 处置 |
|---|------|------|------|
| 1 | `memory.py` procedures 列表无上限无淘汰 | Gödel 层会加速膨胀 | Phase 0 必须解决 |
| 2 | `finding_quality.py` 已有完整质量评分 | quality_score 复用，不从零发明 | Phase 1 复用 |
| 3 | `activation_count` 只证明"注入了"不证明"有效" | 不能衡量习惯效果 | 用 quality_score 相关性 |
| 4 | L1 进化（HabitLearner）还没有真实运行数据验证 | L2 是过早优化 | ConvergenceMonitor 降到 Phase 4 |
| 5 | `boundary_guard.py` 阈值全部硬编码 | AdaptiveGate 自然切入点 | Phase 2 |
| 6 | `record_review_stats()` 已有 `density = findings/turns` | 不需重新发明效率公式 | Phase 1 直接扩展 |

---

## 关键参考文档

- `docs/GODEL_AGENT_PLAN.md` — C3 递归自改进详细计划（v1.1，当前执行文档）
- `docs/NEXT_STEPS.md` — 后续规划终版（v2.1，W1/W2/E1 已完成标记）
- `docs/UPGRADE_PLAN_FINAL.md` — 审稿增强计划（历史存档）
- `docs/ARCHITECTURE_V2_BLUEPRINT.md` — V2 架构蓝图
- `docs/COGNITIVE_ANCHOR.md` — 第一性原理锚点

---

## 工作方式约定

1. **修改前先理解**：改任何核心文件前，先读 COGNITIVE_ANCHOR.md §3（反模式）和 §9（自检问题）。
2. **增量验证**：每个改动后跑 `cd /Users/yanfeiyu03/Downloads/scholar-agent-public/v2 && python3 -m pytest tests/ -x -q`。注意用 `python3` 不是 `python`。
3. **Phase 递进**：Phase N 的测试全部通过 + 全量回归通过 → 才能开始 Phase N+1。
4. **不标"完成"除非真完成**：验证标准没通过 = "已实现但待验证"。
5. **问"人类审稿人会这样思考吗？"**：每个设计决策都问。如果答案是"不会"，说明在做程序而非认知。
6. **Gödel 特有**：新增的 meta 层模块必须有独立测试。不接受"先写实现后补测试"——因为 C9 要求地基可靠。

---

## 反模式提醒

你在做以下事情时，立即停下来：

- 优化 system prompt 措辞让 Agent "表现更好" → 你在做 Prompt Engineering，应该优化控制流
- 让 LLM 在 prompt 里"记住"上一轮结论 → 你在依赖 LLM 记忆，应该写入 state 再注入
- 设计"自动触发 meta 层评估"的复杂条件 → 简单的 `session_count % N == 0` 就够了
- 把 LLM 能做的推理包装成 Tool → Theater Code
- 为"看起来有架构"增加模块 → 简单性检查没通过
- 用 Agent 自我评价作为 quality_score → 循环论证，必须用外部可观测指标
- 用 activation_count 衡量习惯效果 → 只证明"被注入了"不证明"有用"
- 在数据不足时强行自适应 → < 10 session 时 Phase 2-4 全部 no-op
- 在 Phase N 的测试没过时就开始 Phase N+1 → 违反 C9

---

## DO / DON'T

### MUST DO

1. **Phase 0 先行**: 记忆淘汰 + memory.py 测试 + CLAUDE.md 刷新是一切的前提
2. **复用已有评分**: FindingQualityGate 的质量评分已经过 250 行测试验证
3. **每个 Phase 独立验证可交付**: 不能 Phase 3 做一半发现 Phase 1 有 bug
4. **回滚机制比修改机制更重要**: 先做回滚
5. **环境变量控制新功能**: `SCHOLAR_META_ENABLED` 默认 1，可随时关
6. **日志级别**: meta 层所有决策必须有 INFO 级别日志输出

### DON'T

1. **不追求 Level 3 递归** — 工程灾难
2. **不让 meta 层修改 SCHOLAR_IDENTITY** — 身份是宪法层
3. **不在 quality_score 中引入 Agent 自我评价** — 循环论证
4. **不在数据不足时强行自适应** — < 10 session 时 Phase 2-4 全部 no-op
5. **不为"更快收敛"降低 evidence 下限(≥2)** — 偶然不应永久修改
6. **不把 metrics 塞进 ProceduralPattern** — schema 滥用，用独立 JSON 存储

### WATCH OUT

1. **记忆膨胀**: procedures 列表会无限增长，Phase 0 的淘汰机制是底线保障
2. **Goodhart's Law**: quality_score 被优化后可能失去指示性
3. **冷启动**: 前 10-20 session 是盲飞期，meta 层不会启动
4. **identity.py 1339行**: meta 层不修改身份注入逻辑，那里太危险
5. **测试哲学**: Agent 模式下只能断言"有能力做X"不能断言"一定会做X"

---

## 终极自检

观察 meta 层的运行日志。如果你看到"系统在没有数据支撑的情况下修改了配置"——那就是 Gödel Agent 的失控。

正确的日志应该是：度量 → 判断 → 修改（或不修改） → 观察 → 验证（或回滚）。每一步都有据可查。
