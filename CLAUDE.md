# CLAUDE.md — ScholarAgent V2 认知架构

## 容量约束

本文件硬限制 200 行。新增规则时必须审查是否有可降级/移除的旧规则。
过去 5 个 Phase 未被触发的规则标记为候选移除项。
规则分层: L0=绝对约束(不可移除) | L1=经验规则(可被新证据覆盖) | L2=偏好(容量紧张时优先移除)

## 项目定位

学术审稿的认知 Agent。不是 workflow 引擎，不是 tool router。
核心理念：Agent = 认知（how to think），不是编排（how to orchestrate）。
约束而非控制（Constrain, don't control）。

## 唯一工作目录

```
/Users/yanfeiyu03/Downloads/scholar-agent-public/
```

每次开工第一步：`cd` 到此目录，`git status`，读本文件。不确认不动手。

## 仓库结构

```
scholar-agent-public/
├── v2/                ← V2 主代码（唯一活跃版本，完全自包含）
│   ├── core/          ← 核心源码 (49 模块)
│   ├── skills/        ← 知识型+操作型 Skill + registry.json
│   ├── tests/         ← 测试套件 (1342 tests)
│   ├── config/        ← 阈值配置 (thresholds.yaml, academic_sources.yaml)
│   ├── llm/           ← LLM client (Friday API, gpt-4o-mini)
│   ├── guidelines/    ← Agent 行为指导
│   ├── evaluation/    ← 评估框架
│   └── main.py        ← REPL 入口
├── v1/                ← V1 独立副本（prompt 堆叠模式，存档不修改）
├── legacy/            ← 旧 workflow 架构（只参考，不修改）
├── poc/               ← 概念验证原型（只参考）
├── docs/              ← 项目文档（活跃文件 + archive/）
└── CLAUDE.md          ← 本文件
```

## V2 核心模块（可修改）

| 路径 | 职责 |
|------|------|
| `v2/main.py` | REPL 入口 |
| `v2/core/agent.py` | Agent 组装：初始化 + persona 切换 |
| `v2/core/loop.py` | 认知循环引擎：think-act cycle |
| `v2/core/harness.py` | 状态守护 + 工具执行 + quality gate |
| `v2/core/identity.py` | 认知身份 + system prompt + 工具 schema |
| `v2/core/boundary_guard.py` | 边界守卫 + Completion Quality Gate |
| `v2/core/compaction.py` | Smart Compaction Engine |
| `v2/core/paper_cognition_graph.py` | PCG 图认知模型 |
| `v2/core/habits.py` | 认知习惯库 (19 条，动态选取 5 条/轮) |
| `v2/core/token_budget.py` | Token Budget 3-zone (A/B/C) |
| `v2/core/hypothesis.py` | HD-WM 假说推演 |
| `v2/core/assembler.py` | Context 组装管道 (Section Registry + Token Pipeline) |
| `v2/core/phases.py` | Phase FSM (全 nudge 无 block) |
| `v2/core/evolution.py` | EvolutionEngine + HabitLearner |
| `v2/core/metacognition.py` | CognitiveState 自我模型 |
| `v2/core/skill_registry.py` | Skill 注册、查询、lifecycle |
| `v2/core/skill_handler_loader.py` | 操作型 Skill Handler 动态加载 |
| `v2/core/evidence_chain.py` | EvidenceChain 全链路追溯 |
| `v2/core/gate_config.py` | CompletionGateConfig (3-layer 配置源) |
| `v2/core/memory.py` | 三层记忆 (episodic/procedural/semantic) |
| `v2/core/signal_dispatcher.py` | 统一信号调度器 |
| `v2/core/godel_config.py` | Kill Switch 环境变量控制 |

## 架构关系

```
main.py → agent.py (组装) → loop.py (驱动) + harness.py (执行)
  harness.py 内: boundary_guard, compaction, token_budget, skills
  assembler.py: Section Registry + Token Pipeline → context 注入
```

模型决策，harness 执行。Loop 不控制 Agent 做什么，只驱动 think-act 循环。

## 设计红线 [L0]（来自 COGNITIVE_ANCHOR）

1. §2.1 — Agent = cognition, not orchestration
2. §3.1 — 反 workflow thinking（无 Phase 1/2/3 划分）
3. §4.3 — constrain, don't control（约束目标，不规定步骤）
4. §5.1 — 认知循环是内生的，不是外部脚本驱动的
5. C5 — 所有 nudge/signal/gate 是建议，Agent 有最终决策权
6. C14 — Skill 是参考，不是指令（认知辅助框架措辞）
7. C15 — 动态扩展不改静态核心（list concat，handler 失败必须降级）

## 从审稿实践中提炼的认知约束 [L1]

- [Phase 0/17/34] 当 Agent 已读 2-3 sections 就想退出时，不要满足即止，而应检查覆盖率信号判断是否还有核心维度未审查
- [Phase 34/38/42] 当 Agent 遇到论文的方法论声明时，不要只转述论文说了什么，而应质疑该声明的假设、局限和可替代方案
- [Phase 31/38/39] 当 Agent 遇到"首次/原创"等贡献声明时，不要直接接受，而应调用 search_literature 验证是否有先行研究
- [Phase 33/v2-6] 当工具参数标记为 optional 时，不要期望 LLM 自发使用它——关键字段必须 required，或通过环境信号提示其价值
- [Phase 47/56] 当 Agent 产出 finding 前，不要直接写入，而应先检查 findings_store 中是否已有语义重复的条目
- [v2-6/8/9] 当设计认知行为路径时，不要与 LLM 行为经济学对抗——在已有最短路径上自动增强，而非要求绕远路
- [v2-8/11] 当 Gate 拦截要求 Agent 做某事时，不要假设 Agent 会主动调查——而应通过 Integrity Constraint 验证调查行为实际发生了
- [Phase 17/34/47] 当 Agent 连续读多个 section 不记录时，不要等读完再批量写 finding，而应在每个有发现的 section 后立即 update_findings

## 持续积累机制 [L2]

每次新 Phase 的 bug 末尾追加: "应转化为 CLAUDE.md 规则? 是/否"。某类 bug 重复 ≥2 次则触发规则化。

## 设计文档索引

| 文档 | 用途 |
|------|------|
| `docs/V2_UPGRADE_EXECUTION_PLAN.md` | 当前执行计划 |
| `docs/COGNITIVE_ANCHOR.md` | 第一性原理锚点 |
| `docs/COGNITIVE_SPEC.md` | 认知规格说明 |
| `docs/PROGRESS.md` | Phase 历史记录 |
| `docs/FRIDAY_API_REFERENCE.md` | Friday API 用法 |

## 执行规范

1. 确认当前目录是 `/Users/yanfeiyu03/Downloads/scholar-agent-public/`
2. `git status` + `git log --oneline -3` 确认代码状态
3. 读本文件确认项目结构
4. 读 `docs/PROGRESS.md` 尾部确认进度
5. 读要修改的目标文件确认内容
6. 写出执行计划 → 执行 → 验证 → 写小结
7. 测试命令: `cd v2 && python3 -m pytest tests/ -x -q`
