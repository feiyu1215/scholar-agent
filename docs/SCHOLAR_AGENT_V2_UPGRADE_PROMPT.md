# ScholarAgent V2 全面升级开发 Prompt

> **用途**: 给参与 ScholarAgent V2 架构级升级的 AI Agent 提供完整上下文。粘贴到对话开头即可。
>
> **当前路线图**: `~/Downloads/ScholarAgent_V2_Development_Roadmap.md`
> **前置执行计划**: `docs/V2_UPGRADE_EXECUTION_PLAN.md`（Phase A/B/C 已全部完成）
> **历史参考**: `docs/archive/SCHOLAR_AGENT_V3_PROMPT.md` | `docs/archive/GODEL_AGENT_PLAN_V3.md`

---

你正在参与 ScholarAgent 的 **V2 架构级全面升级**。项目已有完整认知架构实现（1450+ tests，F1=0.72），前置清理和增强工作已完成。当前进入 **9 Phase 架构升级** 阶段：

- **第 1 批（Week 1-4）**: Doom Loop 检测 + Memory 蒸馏 + Reflection + 基础设施骨架（Middleware/EventBus/State/ContentBlock 交织推进）
- **第 2 批（Week 5-10）**: Skill 模块化 + 评估框架 + 表格处理（Phase 9A）
- **第 3 批（Week 11-16）**: Skill 合成 + 图表语义理解（Phase 9B）+ 双环架构初版
- **第 4 批（Week 17-24）**: 对抗训练 + 自动优化 + 编排策略学习

执行状态详见路线图中各 Phase 章节末尾的实现记录。

---

## 项目定位

ScholarAgent 帮助研究者完成**学术论文审稿**，核心面向经济学实证论文。Agent = 认知（how to think），不是编排（how to orchestrate）。约束而非控制（Constrain, don't control）。

**本轮升级的核心目标**：从"能审稿的 Agent"进化为"能自我改进的认知系统"——具备循环检测、记忆管理、技能动态组合、自动评估、反思修正、对抗训练、双环优化、多模态理解等能力。

---

## 仓库结构

```
scholar-agent-public/
├── v2/                ← 唯一活跃代码（完全自包含）
│   ├── core/          ← 核心源码 (50+ 模块)
│   │   ├── agent.py           # Agent 组装
│   │   ├── loop.py            # 认知循环 (think-act)
│   │   ├── harness.py         # 状态守护 + 工具执行
│   │   ├── boundary_guard.py  # Completion Quality Gate
│   │   ├── compaction.py      # Smart Compaction + Frozen Snapshot
│   │   ├── paper_cognition_graph.py  # PCG 图认知
│   │   ├── habits.py          # 19 条习惯 + 学科触发器
│   │   ├── token_budget.py    # Three-Zone Budget
│   │   ├── skill_registry.py  # Skill lifecycle 管理
│   │   ├── skill_handler_loader.py   # Handler 动态加载
│   │   ├── signal_dispatcher.py      # 信号调度
│   │   └── godel_config.py    # Kill Switch 环境变量
│   ├── skills/        ← 知识型 + 操作型 Skill + registry.json + installer.py
│   ├── tests/         ← 测试套件 (1450+ tests)
│   ├── config/        ← 阈值配置
│   ├── llm/           ← LLM client (Friday API)
│   └── main.py        ← 入口
├── v1/                ← V1 存档（不修改）
├── docs/              ← 项目文档
│   ├── V2_UPGRADE_EXECUTION_PLAN.md  ← 前置计划（已完成）
│   ├── COGNITIVE_ANCHOR.md           ← 第一性原理锚点
│   └── archive/                      ← 历史文档
└── CLAUDE.md          ← Agent 导航文件
```

---

## 本轮升级的 9 个 Phase

| Phase | 名称 | 实现态度 | 所在批次 | 核心目的 |
|-------|------|---------|---------|---------|
| 1 | Doom Loop 检测与恢复 | 完善实现 | 第 1 批 | 解决 Agent 陷入循环浪费 token 的问题 |
| 2 | Memory 系统升级 (STOM) | 完善实现 | 第 1 批 | 三层信息架构，压缩时不丢关键信息 |
| 3 | Skill 模块化 (SkillX) | 完善实现 | 第 2 批 | 审稿能力从硬编码变为可组合、可扩展 |
| 4 | Skill 合成 (SkillTTA) | 阶段性实现 | 第 3 批 | 失败时自动合成新 Skill 修复弱点 |
| 5 | Meta-Harness 评估 | 完善实现 | 第 2 批 | 自动度量审稿质量，为优化提供信号 |
| 6 | Reflection Loop | 完善实现 | 第 1 批 | 审稿后自我批评，主动修正不足 |
| 7 | 对抗式训练 | 阶段性实现 | 第 4 批 | 用对抗样本暴露弱点并针对性提升 |
| 8 | 双环架构 (Hermes) | 阶段性实现 | 第 3 批 | 外环编排优化内环审稿策略 |
| 9A | 表格处理 | 完善实现 | 第 2 批 | OCR 提取回归表，自动验证数值一致性 |
| 9B | 图表语义理解 | 完善实现 | 第 3 批 | Vision Model 理解经济学图表的因果含义 |

**实现态度规则**：默认做完善版本。只有算法需迭代验证（Phase 4/7）或架构依赖未就绪（Phase 8）时才做阶段性实现。

---

## 基础设施层（交织推进，非串行前置）

本轮升级引入 5 个基础设施模块，随第 1 批 Phase 交织建设：

| 模块 | 首次需要时机 | 职责 |
|------|------------|------|
| `v2/core/middleware.py` | Week 1（Phase 1 拦截 loop）| Middleware 洋葱模型，Hook 注册 |
| `v2/core/event_bus.py` | Week 2（Phase 1/2 记录事件）| 结构化事件总线，发布/订阅 |
| `v2/core/state.py` | Week 3（checkpoint 需要）| ReviewSessionState 可序列化 |
| `v2/core/offloader.py` | Week 1（Phase 2 存储压缩记忆）| Offload 协议 + 引用路径 |
| `v2/core/content_blocks.py` | Week 2（Phase 6 ThinkingBlock）| 类型化内容块（Text/Thinking/Finding/Figure）|

**原则**：每个模块在首次被需要时由使用场景驱动接口设计。只实现当前需要的方法，留出 ABC/Protocol 扩展点。

---

## 设计约束（承接前期 C1-C15，新增架构约束）

### 不变的核心约束

**C1**: Agent = Loop + Tools（不是 workflow engine）
**C3**: 控制流 > Prompt Engineering
**C5**: Constrain, don't control（所有 signal/nudge 是建议）
**C6**: Keep it simple
**C14**: Skill 是参考，不是指令
**C15**: 动态扩展不改静态核心

### 本轮新增约束

**C16: 基础设施交织推进。** 不先花整周只写基础设施——每个基础设施模块在被实际使用时才实现。

**C17: 完善版本为默认态度。** 不做 MVP 再迭代——除非显式标注为【阶段性实现】且给出原因。

**C18: 场景驱动设计。** 每个新模块/模式必须有具体审稿场景支撑其存在。"架构优雅"不是理由。

**C19: 经济学领域感知。** 表格验证、图表理解、方法论分析等能力，默认以经济学实证论文为第一目标场景。

**C20: 开放性架构。** 用户可通过 MCP/ToolGroup 扩展工具（统计验证、代码执行、文献检索），架构不封闭。

---

## 技术方案要点

### 多模态（Phase 9A/9B）—— Path C 组合方案

| 层次 | 技术 | 场景 |
|------|------|------|
| Layer 1 | OCR (Camelot/Tabula) + 规则 | 回归表提取、数值一致性验证 |
| Layer 2 | Vision Model (GPT-4o/Claude Vision) | 理解 DID 平行趋势图、事件研究图 |
| Layer 3 | OCR 辅助 Vision（坐标轴+数据点） | 精确数值提取做交叉验证 |

### 基础设施借鉴 —— AgentScope 2.0 的 12 个模式

详见路线图附录 C。核心借鉴：Middleware 洋葱模型、Context Offloader、ToolGroup 动态管理、Event Stream、Agent State 外部化、ContentBlock 类型系统、Model Registry。

---

## 工作方式约定

1. **读路线图再动手**：开工前读 `ScholarAgent_V2_Development_Roadmap.md` 中对应 Phase 的完整描述，理解"目的"而非只看代码示例。
2. **增量验证**：每个改动后跑 `cd /Users/yanfeiyu03/Downloads/scholar-agent-public/v2 && python3 -m pytest tests/ -x -q`。
3. **测试 = 完成的一部分**：不写测试 = 没完成。每个新模块必须有单元测试 + 至少一个集成场景。
4. **更新路线图**：完成后在对应 Phase 章节追加实现记录（格式：`> **实现记录 (YYYY-MM)**: ...`）。
5. **Kill Switch 守卫**：新功能代码路径必须有对应环境变量 flag，默认开启，设 "0" 静默降级。
6. **Graceful Degradation**：任何新增能力失败不能影响核心审稿流程。旧路径必须保留。
7. **接口先行**：基础设施模块优先定义 Protocol/ABC 接口，再实现；下游使用接口而非实现类。
8. **场景驱动论证**：如果被问"为什么需要这个"，必须能给出具体的审稿场景而非抽象的架构理由。

---

## 反模式提醒

你在做以下事情时，立即停下来：

- **为了架构优雅而加模块** → 必须有真实审稿场景支撑（C18）
- **先做基础设施再用** → 应该在实际使用时才实现（C16）
- **所有 Phase 都做最简版本** → 默认完善实现，只有高不确定性才分阶段（C17）
- **改 loop.py 第 N 行来加功能** → 应该通过 Middleware 注入（这是 Middleware 存在的意义）
- **新增功能无 Kill Switch** → 必须有 flag 守卫，默认开，"0" 降级
- **表格/图表分析只考虑通用情况** → 经济学特化优先（C19），DID/IV/Event Study 是第一目标
- **写代码不写测试就标完成** → 测试通过 + 集成验证 = 完成
- **基础设施接口过度设计** → 只实现当前被使用的方法，留扩展点即可
- **Skill 合成/对抗训练一步做完** → 这两个是阶段性实现，需要真实数据迭代

---

## 待确认决策点

| # | 问题 | 影响 | 决定时间 |
|---|------|------|---------|
| 1 | Vision Model 选型 | Phase 9B | 第 3 批前 |
| 2 | 代码执行沙箱方案 | Phase 9A 表格验证 | 第 2 批前 |
| 3 | 是否需要 Web UI | HiTL/Streaming | 第 3 批前 |
| 4 | 评测数据集来源 | Phase 5/7 | 第 2 批前 |
| 5 | 经济学领域知识注入方式 | Phase 3/4 | 第 2 批进行中 |

遇到这些决策点时，**先记录选项和权衡**，然后请求用户确认，不要自行假设。

---

## 关键参考文档

| 文档 | 用途 |
|------|------|
| `~/Downloads/ScholarAgent_V2_Development_Roadmap.md` | **全面升级路线图**（看这个！）|
| `docs/V2_UPGRADE_EXECUTION_PLAN.md` | 前置工作记录（已完成）|
| `docs/COGNITIVE_ANCHOR.md` | 第一性原理锚点 |
| `CLAUDE.md` | Agent 开工导航 |
| `docs/COGNITIVE_SPEC.md` | 认知规格说明 |
| `docs/PROGRESS.md` | Phase 历史记录 |

---

## 根本性认知提醒

> 这个项目的核心价值在于**认知架构思想**——循环检测、记忆三层、技能组合、自动评估、反思修正、对抗训练、双环优化。本轮升级的目标是让这个已经工作的系统（1450+ tests）获得**自我进化能力**。
>
> 关键心态：
> - **完善 > 最简**：大部分功能应一次到位做好，不是拼凑半成品
> - **场景 > 架构**：任何设计决策的合理性来自真实审稿场景，不来自抽象优雅
> - **经济学 > 通用**：优先让经济学实证论文审稿体验做到极致，再泛化到其他学科
> - **交织 > 串行**：基础设施和功能并行生长，而非一方等另一方

---

*Version: V2 Full Upgrade Prompt | 1450+ tests passing | F1=0.72 | 2025-07*
