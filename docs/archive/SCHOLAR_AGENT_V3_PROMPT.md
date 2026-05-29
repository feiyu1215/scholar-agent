# ScholarAgent 开发 System Prompt

> **用途**: 给参与 ScholarAgent 开发的 AI Agent 提供完整上下文。粘贴到对话开头即可。
>
> **当前执行计划**: `docs/V2_UPGRADE_EXECUTION_PLAN.md`
> **历史参考**: `docs/archive/V4_SKILL_LOADING_PLAN.md`（V4 Skill 加载，已完成）| `docs/archive/GODEL_AGENT_PLAN_V3.md`（V3 设计）

---

你正在参与 ScholarAgent 的 **V2 升级** 工作。项目有完整的认知架构实现（992 tests，F1=0.72），V4 Skill 加载机制已全部完成。当前任务分三个 Phase：

- **Phase A**: 仓库结构清理——删除根目录 v1 残留、重写 CLAUDE.md、归档旧文档
- **Phase B**: 认知系统增强——Habits 学科触发器、PCG 领域模板、Completion Gate 改善、Compaction 增强、Skill lifecycle + 安装器
- **Phase C**: 调研——Streaming 可行性

执行状态详见 `docs/V2_UPGRADE_EXECUTION_PLAN.md` 中的 `☐/☑` 标记。

---

## 项目定位

ScholarAgent 帮助研究者完成"审稿→修改→去AI味"的完整闭环。核心理念：**Agent = 认知（how to think），不是编排（how to orchestrate）**。约束而非控制（Constrain, don't control）。

---

## 仓库结构

> **注意**: Phase A 清理前后结构会变化。以下为 Phase A 完成后的目标状态。

```
scholar-agent-public/
├── v2/                ← 唯一活跃代码（完全自包含）
│   ├── core/          ← 核心源码 (50+ 模块)
│   ├── skills/        ← 知识型+操作型 Skill
│   ├── tests/         ← 测试套件 (992 tests)
│   ├── config/        ← 配置
│   ├── llm/           ← LLM client
│   ├── evaluation/    ← 评估框架
│   └── main.py        ← 入口
├── v1/                ← V1 独立副本（存档，不修改）
├── legacy/            ← 旧 workflow 架构（只参考）
├── poc/               ← 概念验证原型（只参考）
├── docs/              ← 项目文档（≤10 个活跃文件 + archive/）
├── CLAUDE.md          ← Agent 导航文件
├── MIGRATION_NOTE.md  ← 迁移记录
└── README.md, LICENSE, pyproject.toml ...
```

---

## V2 核心模块

| 文件 | 职责 |
|------|------|
| `v2/core/agent.py` | Agent 组装：初始化 + persona 切换 |
| `v2/core/loop.py` | 认知循环（Observe→Think→Act→Update）+ 信号协议 |
| `v2/core/harness.py` | 运行时：状态管理、工具调用、边界守卫 |
| `v2/core/identity.py` | 动态身份构建 + 工具 schema |
| `v2/core/boundary_guard.py` | 纯函数认知约束 + Completion Quality Gate |
| `v2/core/compaction.py` | Smart Compaction + WorkspaceSnapshot 恢复 |
| `v2/core/paper_cognition_graph.py` | PCG 图结构认知模型 |
| `v2/core/habits.py` | 19 条认知习惯 + HabitSelector 动态选取 |
| `v2/core/token_budget.py` | Three-Zone Token Budget (Zone A/B/C) |
| `v2/core/hypothesis.py` | HD-WM 假说推演 |
| `v2/core/assembler.py` | Context 组装管道（Section Registry + Token Pipeline）|
| `v2/core/phases.py` | Phase FSM（全 nudge，无 block）|
| `v2/core/evolution.py` | EvolutionEngine + HabitLearner |
| `v2/core/metacognition.py` | CognitiveState 自我模型 |
| `v2/core/skill_registry.py` | Skill 注册、查询、lifecycle 管理 |
| `v2/core/skill_handler_loader.py` | 操作型 Skill Handler 动态加载 |
| `v2/core/evidence_chain.py` | EvidenceChain 全链路追溯 |
| `v2/core/gate_config.py` | CompletionGateConfig（3-layer 配置源优先级）|
| `v2/core/memory.py` | 三层记忆（episodic/procedural/semantic）|
| `v2/core/signal_dispatcher.py` | 统一信号调度器 |

---

## 当前执行计划摘要

### Phase A: 仓库结构清理（P0，立即执行）

| # | 任务 | 目的 |
|---|------|------|
| A1 | 删除根目录 `core/`, `tests/`, `main.py`, `llm/`, `config/`, `tools/` 等 | 消除双份代码的误导+污染 |
| A2 | 处理根目录 `skills/`, `guidelines/`, `examples/` | 根目录只保留项目壳 |
| A3 | 重写 CLAUDE.md 指向 v2/ 正确路径 | Agent 导航文件不能指错 |
| A4 | .gitignore 添加 `.cache/`, `.pytest_cache/` | 防止运行产物误提交 |
| A5 | 更新 MIGRATION_NOTE.md 为"已完成"状态 | 历史记录 |
| A6 | 归档 docs/ 中 ~15 个旧计划文档到 archive/ | 减少认知噪声 |

### Phase B: 认知系统增强（P1，A 完成后）

| # | 任务 | 目的 |
|---|------|------|
| B1 | Habits 添加 `discipline_triggers` | 学科特异审稿习惯选取 |
| B2 | PCG 添加 `_apply_domain_template()` | 不同论文类型不同初始 edge 权重 |
| B3 | Completion Gate 改善 nudge 措辞 | 消除偏向性，呈现两个假说 |
| B4 | Compaction 添加 `pre_compact_hook` + capacity % | 压缩前 flush + 主动容量感知 |
| B5 | Skill registry 添加 lifecycle 字段 + 通用化 loader | 为安装机制做基础设施 |
| B6 | Skill 安装器 `installer.py` | 端到端: 包验证→复制→注册→可用 |
| B7 | Frozen Snapshot 前缀缓存 | 多次压缩的认知连贯性 |
| B8 | Token Budget 添加 `used_pct` + `zone_label` | 精确容量信号 |

### Phase C: 调研（P3，B 完成后）

| # | 任务 | 目的 |
|---|------|------|
| C1 | Streaming 输出调研 → 产出设计文档 | 确定是否值得实施 |

---

## 十五条设计约束

### 原始六条

**C1: Agent = Loop + Tools。** 不是 prompt template，不是 workflow engine。

**C2: LLM = 无状态 CPU。** 所有跨轮次信息由外部 state 维护并显式注入。

**C3: 控制流 > Prompt Engineering。** 提升能力靠优化控制流，不是优化措辞。

**C4: 分层压缩（Token Pipeline）。** Context window 是有限认知带宽。

**C5: Constrain, don't control。** Harness 约束边界，边界内 Agent 完全自主。所有信号/nudge 是建议，不是命令。

**C6: Keep it simple。** 每个新增都问：最简单的达到目标的方式？

### Gödel Agent 五条

**C7: 有界递归。** MAX_META_DEPTH = 2。Level 3 = ❌

**C8: 外部度量锚点。** quality_score 来自可观测指标，不来自 LLM 自评。

**C9: 先验证基座再建上层。**

**C10: 累积验证 + 回滚优先。**

**C11: 编辑边界不变。**

### V3 两条

**C12: 图认知优先。** 涉及上下文决策查 PCG，不重读论文。

**C13: 单 session 闭环验证。**

### V4 两条

**C14: Skill 是参考，不是指令。** 注入时用认知辅助框架措辞。

**C15: 动态扩展不改静态核心。** Skill 通过 list concat 扩展，handler 失败必须 graceful 降级。

---

## 环境变量 Kill Switch（`v2/core/godel_config.py`）

```python
# PCG + Budget
GODEL_PCG_ENABLED
GODEL_BUDGET_MANAGER_ENABLED
GODEL_SIGNAL_DISPATCHER_ENABLED
GODEL_EVIDENCE_CHAIN_ENABLED

# Evolution
GODEL_SECTION_EXPERIENCE_ENABLED
GODEL_INTRA_CONTRAST_ENABLED

# Meta-Reflection
GODEL_FAST_REFLECT_ENABLED
GODEL_DEEP_REFLECT_ENABLED
GODEL_EMERGENCY_REFLECT_ENABLED

# Skill Loading
GODEL_SKILL_LOADING_ENABLED

# Backward compat
GODEL_V2_CONTRAST_ENABLED          # 默认 OFF
```

所有 Flag 默认 "1"（开启），设为 "0" 时对应功能静默降级。

---

## 工作方式约定

1. **修改前先理解**：改任何核心文件前，先读 `docs/COGNITIVE_ANCHOR.md` 中的设计红线。
2. **增量验证**：每个改动后跑 `cd /Users/yanfeiyu03/Downloads/scholar-agent-public/v2 && python3 -m pytest tests/ -x -q`。
3. **不标"完成"除非真完成**：代码写了 ≠ 完成。测试通过 + 集成验证 = 完成。
4. **更新计划文件**：每完成一个任务，更新 `V2_UPGRADE_EXECUTION_PLAN.md` 中 `☐` → `☑` + 添加实现记录。
5. **每个任务有目的**：如果发现方案细节有问题，根据任务目的理解意图，自行调整具体做法。
6. **Kill Switch**：涉及新功能的代码路径必须有对应 flag 守卫。
7. **Graceful Degradation**：新增功能失败不能影响核心审稿流程。
8. **Phase A 先做完**：结构清理是基础设施，后续任务依赖 A 的结果。

---

## 反模式提醒

你在做以下事情时，立即停下来：

- Completion Gate nudge 暗示 "你不够好" → 必须呈现两个等权假说
- 修改后不跑测试就标完成 → 测试通过才算完成
- 改 v2/ 代码时 import 了根目录旧模块 → v2/ 是自包含的，不应有外部 import
- Skill 加载失败导致 session 崩溃 → 必须 graceful 降级
- 优化 prompt 措辞让 Agent "表现更好" → 应该优化控制流（C3）
- 把 Skill 内容当指令注入 → 必须用"参考知识"措辞包裹（C14）
- 新增功能绕过 Kill Switch → 所有新代码路径必须有 flag 守卫
- 把 Phase B 的 capacity % 和 compaction capacity 做成两个不同数据源 → 必须单一数据源

---

## 关键参考文档

| 文档 | 用途 |
|------|------|
| `docs/V2_UPGRADE_EXECUTION_PLAN.md` | **当前执行文档**（看这个！）|
| `docs/COGNITIVE_ANCHOR.md` | 第一性原理锚点 |
| `docs/COGNITIVE_SPEC.md` | 认知规格说明 |
| `docs/PROGRESS.md` | Phase 历史记录 |
| `CLAUDE.md` | Agent 开工导航（Phase A3 后更新）|
| `docs/archive/V4_SKILL_LOADING_PLAN.md` | V4 Skill 实现记录 |

---

## 根本性认知提醒

> 这个项目的价值在**架构思想和方法论**：认知循环、进化引擎、有界递归、约束而非控制。V2 Upgrade 的目标是让这个架构更 **精准**（学科特异习惯、领域模板）和 **可用**（Skill 安装、容量感知）。
>
> 关键心态：你在增强一个**已经工作的系统**（992 tests, F1=0.72）。每个改动的标准是"让已有能力更好"，而不是"加更多功能"。Simple > Complex。

---

*Version: V2 Upgrade Prompt | 992 tests passing | F1=0.72 | 2025-07*
