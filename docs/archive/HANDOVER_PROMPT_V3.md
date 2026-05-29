# ScholarAgent C3 Gödel Agent V3 — 交接 Prompt

> **写作日期**: 本轮会话结束时
> **会话主题**: 基于 AI_Agent_Frontier_Report.md 完成 GODEL_AGENT_PLAN_V3.md 理想态设计
> **接手后首要动作**: 读完本文 → 读 `docs/GODEL_AGENT_PLAN_V3.md` → 从 Phase 0.5.1 开始实现

---

## 1. 项目定位（一句话）

ScholarAgent 是一个帮助经济学研究者完成"审稿→修改→去AI味"闭环的认知 Agent。V3 专为 **50-70 页长篇论文**场景设计——Agent 需要图结构认知、动态 token 管理、单 session 内自我验证能力。

---

## 2. 架构现状

```
v1/  Prompt-stacking 原型（冻结，298 tests）
v2/  HD-WM 认知架构（唯一活跃方向，710 tests）
     ├── core/           核心认知引擎
     ├── tool_handlers/  工具实现
     ├── llm/            LLM 调用层
     ├── tests/          全量测试
     └── docs/           设计文档
```

架构哲学：**单 Agent + 状态机 + 黑板模式**。不做多 Agent、不做 workflow engine。LLM 是无状态 CPU，所有跨轮次信息由外部 state 维护并显式注入 context。

核心组件（按调用链路）：

```
harness.py (L139-704)          运行时环境 / 子系统编排
  └── loop.py (L73-728)        认知循环 Observe→Think→Act→Update
        └── agent.py (723行)    UnifiedReviewAgent + persona 切换
              └── identity.py   动态身份构建 + 工具 schema
```

支撑模块：

```
assembler.py (599行)           Context 组装 Token Pipeline
memory.py (821行)              三层记忆 + gc_procedures
evolution.py (643行)           EvolutionEngine + HabitLearner
hypothesis.py (459行)          HypothesisModule 结构化假说
metacognition.py (253行)       CognitiveState HD-WM
compaction.py (463行)          Context 压缩 + WorkspaceSnapshot
session_finalizer.py (283行)   session 结束 metrics + reflection
paper_index.py (214行)         PaperStructureIndex 论文预索引
boundary_guard.py (393行)      纯函数认知约束守卫
finding_quality.py (250行)     Finding 质量门控
```

---

## 3. 本轮会话完成了什么

### 3.1 产出物

| 文件 | 行数 | 说明 |
|------|------|------|
| `docs/GODEL_AGENT_PLAN_V3.md` | 2505 行 | V3 理想态完整设计（Implementation-Ready）|
| `docs/SCHOLAR_AGENT_V3_PROMPT.md` | ~350 行 | V3 开发 system prompt（给 AI 开发者的上下文）|
| `docs/HANDOVER_PROMPT_V3.md` | 本文件 | 交接 prompt |

### 3.2 设计过程

1. **输入**: `AI_Agent_Frontier_Report.md`（7 前沿方向研究报告）+ `GODEL_AGENT_PLAN_V2.md`（V2 Final 计划）
2. **方法**: serious-mode + fundamental-thinking 逐个前沿方向与当前架构碰撞
3. **核心判断**: V2→V3 不是增量打补丁，是面向"50-70 页论文"场景的架构级重设计
4. **迭代**: 初版 1386 行 → 增加 V2 风格精确文件路径/行号/集成代码/JSON 格式/验证清单 → 最终 2505 行

### 3.3 未做的事

- **未写任何代码** — V3 计划纯设计文档，所有代码片段是"应该怎么写"
- **未修改任何现有文件** — 710 tests 状态不变
- **未运行过测试** — 本轮是纯设计会话

---

## 4. V3 核心设计创新（7 项）

| # | 创新 | 解决什么问题 | 来源灵感 |
|---|------|-------------|----------|
| 1 | Paper Cognition Graph (PCG) | Agent 无法维持 50 页论文全局理解 | CodeGraph + TencentDB 分层记忆 |
| 2 | Three-Zone Token Budget | 被动压缩不适合长论文，需主动预分配 | TencentDB Context Offloading |
| 3 | Unified Signal Dispatcher | loop.py 4 个独立 check 叠加 → 注意力稀释 | Anthropic 三元极简 |
| 4 | IntraSession Contrast | V2 12% 跨 session 对比统计上无意义 | GPTSwarm edge probability |
| 5 | EvidenceChain | 高优 finding 缺乏推理可追溯性 | Harness R.E.S.T Traceability |
| 6 | Tri-frequency MetaReflector | 固定 10 session 反思太慢（3-5 周） | 17 Architectures metacognition |
| 7 | Hypothesis 双模块统一 | 两套假说系统同步开销大 | 单一数据源原则 |

---

## 5. 十三条设计约束

### 原始六条（C1-C6，从项目创建至今不变）

- C1: Agent = Loop + Tools
- C2: LLM = 无状态 CPU
- C3: 控制流 > Prompt Engineering
- C4: 分层压缩（Token Pipeline）
- C5: Constrain, don't control
- C6: Keep it simple

### Gödel Agent 引入（C7-C11）

- C7: 有界递归 MAX_META_DEPTH = 2
- C8: 外部度量锚点（不用 Agent 自评）
- C9: 先验证基座再建上层
- C10: 累积验证 + 回滚优先（evidence ≥ 3）
- C11: 编辑边界不变（Stata MCP 外部对接）

### V3 新增（C12-C13）

- C12: 图认知优先（查 PCG 而非重读论文）
- C13: 单 session 闭环验证（IntraSession Contrast 取代跨 session）

---

## 6. 当前开发进度

```
✅ P0 架构债务（W1/W2/H-SPLIT）
✅ P1 验证（E1 全链路 E2E）
✅ P2 认知进化 Session 级（SessionReflector + HabitLearner + LearnedHabit）
✅ C3 Phase 0 地基加固（gc_procedures + 70 tests + 710 全量通过）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
→  C3 Phase 0.5 Paper Cognition Infrastructure ← 下一步从这里开始
   C3 Phase 1 Hierarchical Experience + IntraSession Contrast
   C3 Phase 2 Tri-Frequency MetaReflector
   C3 Phase 3 Habit Evolution + Closed Loop
```

**完成度**: Phase 0.5-3 的设计 100% 完成（含精确文件路径、行号、集成代码、验证清单），实现 0%。

---

## 7. 下一步工作（优先级排序）

### 7.1 Phase 0.5 — Paper Cognition Infrastructure（~3 天）

**这是当前最高优先级**，所有后续 Phase 依赖它。

| 子任务 | 内容 | 预计 |
|--------|------|------|
| 0.5.1 | `core/paper_cognition_graph.py` + `state.py` L104 后扩展 | 0.5d |
| 0.5.2 | `core/token_budget.py` + `assembler.py` L272 + L404 集成 | 0.5d |
| 0.5.3 | `core/signal_dispatcher.py` + `loop.py` L101-130 替换 | 0.5d |
| 0.5.4 | `core/evidence_chain.py` + `harness.py` L401 tracking hook | 0.5d |
| 0.5.5 | `compaction.py` L100 扩展 + `core/godel_config.py` + 30 tests | 1d |

**起手顺序**:
1. 先创建 `core/godel_config.py`（所有 Kill Switch）
2. 再写 `core/paper_cognition_graph.py`（最核心的新模块）
3. 修改 `state.py` 添加字段
4. 修改 `harness.py` 初始化 + `load_paper()` 构建 PCG
5. 写 `core/token_budget.py` + `core/signal_dispatcher.py` + `core/evidence_chain.py`
6. 集成到 `assembler.py` / `loop.py` / `compaction.py`
7. 写 30 个测试
8. 跑全量回归

### 7.2 Phase 1-3（总计 ~5.5 天）

在 Phase 0.5 全部测试通过后才能开始。详见 `GODEL_AGENT_PLAN_V3.md` §5。

---

## 8. 关键设计决策（本轮会话产生）

### 决策 1: PCG 继承而非替代 PaperStructureIndex

**理由**: PaperStructureIndex 的 regex 解析成熟稳定（无 LLM 依赖，<1 sec）。PCG 通过 `from_structure_index()` 继承其骨架，仅增加认知层（digest/claims/edges）。失败时回退到 PaperStructureIndex。

**影响**: `paper_index.py` 零修改。

### 决策 2: IntraSession Contrast 取代 V2 12% 跨 session 对比

**理由**: 50-70 页论文有 15-25 sections，同一篇论文内分 A/B 对比消除了"论文难度差异"这个混淆变量。V2 方案 6/50 sessions = 12% 数据利用率，统计上近乎无意义。

**影响**: V2 `is_contrast_session` 保留但默认 OFF（`GODEL_V2_CONTRAST_ENABLED=0`）。

### 决策 3: SignalDispatcher 替换 loop.py stacked checks

**理由**: 长论文 40-60 轮，若每轮 4-5 条 system message 叠加，Agent 注意力被严重稀释。Dispatcher 强制 max 2/turn + 优先级调度 + 同源去重。

**影响**: `loop.py` L101-130 完整替换，但保留 `if not GODEL_SIGNAL_DISPATCHER_ENABLED:` fallback 分支。

### 决策 4: 三频 MetaReflector 而非 V2 固定 10 session

**理由**: Fast(3 session, 零 LLM) 做趋势预警，Deep(10 session, LLM) 做决策，Emergency(realtime) 做紧急降级。比 V2 的"3-5 周才知道出了问题"快 3-10 倍。

**影响**: V2 `MetaReflector` 被 `DeepReflector` 继承，不删除。

### 决策 5: EvidenceChain 作为 token 管理和质量评估双用途

**理由**: 既解决"Agent 的结论从哪来"（学术审稿需要 evidence），又为 MetaReflector 提供 chain_length + pcg_edges_used 作为 finding 质量信号。一份数据两个用途。

**影响**: `harness.py` execute_tool() 需要添加 tracking hook（~20 行）。

### 决策 6: 所有新功能通过环境变量 Kill Switch 控制

**理由**: V3 是理想态设计，实际实现可能遇到未预见问题。任何单个模块都可以通过 `SCHOLAR_GODEL_XXX=0` 安静关闭，系统退化到 Phase 0 行为。不需要改代码就能回退。

**影响**: 每个新模块的每个调用点都有 `if FLAG:` guard。统一在 `core/godel_config.py` 管理。

---

## 9. DO / DON'T 速查

### MUST DO

1. 实现前先读 `GODEL_AGENT_PLAN_V3.md` 对应 Phase 的完整 section（含验证清单）
2. 每个改动后跑 `cd /Users/yanfeiyu03/Downloads/scholar-agent-public/v2 && python3 -m pytest tests/ -x -q`
3. Phase N 测试全通过才能开始 Phase N+1（C9 约束）
4. 新模块必须有独立测试（不接受"先实现后补测试"）
5. Kill Switch 在调用点 guard，不在 import 时 side-effect
6. PCG 构建失败时 graceful degradation 到 PaperStructureIndex
7. 用 `python3` 不是 `python`

### DON'T

1. 不改 `identity.py`（1339 行，太危险）
2. 不改 `agent.py` / `reflection.py`（V3 不涉及）
3. 不往 Zone A 里放完整 section 内容（只放导航摘要）
4. 不让 SignalDispatcher 超过 2 条/轮（宪法约束）
5. 不在 < 10 session 时启动 Phase 2-3 逻辑（冷启动保护）
6. 不用 Agent 自我评价做 quality_score（循环论证）
7. 不删 V2 逻辑（通过 Kill Switch 禁用）
8. 不追求 Level 3 递归（MAX_META_DEPTH = 2）

---

## 10. 参考文档索引

| 文档 | 何时读 | 行数 |
|------|--------|------|
| `docs/GODEL_AGENT_PLAN_V3.md` | **必读**，实现任何 Phase 前的执行文档 | 2505 |
| `docs/SCHOLAR_AGENT_V3_PROMPT.md` | 给 AI 开发者的上下文 prompt | ~350 |
| `docs/COGNITIVE_ANCHOR.md` | 改核心文件前的第一性原理检查 | — |
| `docs/GODEL_AGENT_PLAN_V2.md` | V2 对比参考（看历史决策） | 1091 |
| `docs/GODEL_AGENT_PLAN.md` | V1.1 原始计划（历史存档） | — |
| `docs/AI_Agent_Frontier_Report.md` | V3 设计灵感来源 | — |
| `docs/ARCHITECTURE_V2_BLUEPRINT.md` | V2 架构全貌 | — |
| `docs/NEXT_STEPS.md` | 后续规划终版 | — |

---

## 11. Phase 0.5 起手指南

```bash
# 1. 确认环境
cd /Users/yanfeiyu03/Downloads/scholar-agent-public/v2
python3 -m pytest tests/ -x -q  # 确认 710 tests 全绿

# 2. 创建 Kill Switch 配置
# 新建 core/godel_config.py（参考 V3 Plan §9）

# 3. 实现 PCG 核心
# 新建 core/paper_cognition_graph.py
# - PCGNode dataclass
# - PCGEdge dataclass
# - PaperCognitionGraph 类
# - from_structure_index() 桥接方法
# - context_for_task(), coverage_gaps(), format_for_zone_a()

# 4. 集成到 state.py
# L104 后新增 paper_cognition_graph + evidence_chains 字段

# 5. 集成到 harness.py
# __init__ L231 后: 初始化 TokenBudgetManager + SignalDispatcher + EvidenceChainTracker
# load_paper L247 后: PaperCognitionGraph.from_structure_index()
# execute_tool L401 末尾: evidence tracking hook

# 6. 实现 TokenBudgetManager
# 新建 core/token_budget.py（参考 V3 Plan §3.2）

# 7. 实现 SignalDispatcher
# 新建 core/signal_dispatcher.py（参考 V3 Plan §3.3）
# 修改 loop.py L101-130（保留 fallback 分支）

# 8. 实现 EvidenceChainTracker
# 新建 core/evidence_chain.py（参考 V3 Plan §4.2.2）

# 9. 扩展 compaction.py
# WorkspaceSnapshot L100-135: +pcg_snapshot +cognitive_state_snapshot +evidence_chain_refs
# _build_layers L136-196: +priority 5 PCG + priority 6 evidence refs

# 10. 集成到 assembler.py
# _compute_paper_structure L272: PCG.format_for_zone_a() 优先
# _register_default_sections L404: 新增 priority=89 pcg_navigation

# 11. 写 30+ 测试
# tests/test_paper_cognition_graph.py
# tests/test_token_budget.py
# tests/test_signal_dispatcher.py
# tests/test_evidence_chain.py

# 12. 全量回归
python3 -m pytest tests/ -x -q  # 740+ tests 全绿（710 existing + 30 new）
```

---

## 12. 常见问题

### Q1: V3 跟 V2 是什么关系？需要删 V2 代码吗？

**不删**。V3 是在 V2 代码基础上的增强层。所有 V3 新模块通过 Kill Switch 控制，`FLAG=0` 时系统行为与 V2 完全一致。`GODEL_AGENT_PLAN_V2.md` 是历史参考，`V3.md` 是当前执行文档。

### Q2: PCG 构建需要 LLM 调用吗？性能如何？

`from_structure_index()` **不需要 LLM**（纯数据映射，<0.5 sec）。PCG 的 `digest` 和 `claims` 字段需要 LLM（在 INITIAL_SCAN phase 由 Agent 逐步填充），但 PCG 骨架本身是零 LLM 成本。

### Q3: IntraSession Contrast 会降低审稿质量吗？

**不会**。Phase B 只移除 1 个待验证 habit（confidence 在 0.4-0.7 之间的），不是移除全部 habits。Agent 在 Phase B 仍然保有 90%+ 的认知增强。且 contrast 分析是 post-hoc 的——不影响 Agent 实时决策。

### Q4: 如果 Phase 0.5 某个子模块实现有问题怎么办？

每个子模块有独立 Kill Switch。例如 `SCHOLAR_GODEL_PCG=0` 只关闭 PCG，其他三个模块（TokenBudget/Dispatcher/EvidenceChain）不受影响。可以逐个模块上线。

### Q5: 为什么不直接跳到 Phase 1-3？

C9 约束：先验证基座再建上层。Phase 1 的 IntraSession Contrast 需要 PCG 的 section 分组信息；Phase 2 的 DeepReflector 需要 Phase 1 的 L0/L1 经验数据。跳过 Phase 0.5 会导致 Phase 1 无法观测到有意义的数据。

### Q6: 这个项目的测试怎么跑？

```bash
cd /Users/yanfeiyu03/Downloads/scholar-agent-public/v2
python3 -m pytest tests/ -x -q          # 全量快速
python3 -m pytest tests/ -v             # 详细输出
python3 -m pytest tests/test_xxx.py -x  # 单文件
```

注意用 `python3` 不是 `python`。`-x` 表示遇到第一个失败就停。

### Q7: 这个计划是"必须严格按照执行"还是"参考性的"？

**架构决策和数据结构是约束性的**（PCGNode/PCGEdge schema、三层架构、13 条设计约束）。具体实现细节是参考性的（方法内部逻辑可以优化，只要接口和行为与计划一致）。验证清单中的每一项都是硬性验收标准。

---

## 13. 本轮会话关键文件修改汇总

| 操作 | 文件 | 说明 |
|------|------|------|
| 新建 | `docs/GODEL_AGENT_PLAN_V3.md` | V3 理想态完整设计（2505 行）|
| 新建 | `docs/SCHOLAR_AGENT_V3_PROMPT.md` | V3 开发 system prompt |
| 新建 | `docs/HANDOVER_PROMPT_V3.md` | 本文件 |
| 未修改 | `v2/core/*.py` | 零代码修改 |
| 未修改 | `v2/tests/*.py` | 710 tests 状态不变 |

---

## 14. 一句话总结

V3 设计已 100% 完成（2505 行 Implementation-Ready 文档），下一步是 **Phase 0.5 实现**——创建 4 个新文件 + 修改 6 个现有文件 + 写 30 个测试。从 `core/godel_config.py` 开始。

---

*交接于本轮会话结束 | 后续任何疑问请先查阅 `docs/GODEL_AGENT_PLAN_V3.md` 对应章节*
