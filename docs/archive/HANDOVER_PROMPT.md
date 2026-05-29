# ScholarAgent V2 Upgrade 交接 Prompt

> **用途**: 新 Agent/会话接手时粘贴此文件。包含完整项目上下文、本轮决策记录、下一步工作。
> **生成时间**: 2025-07
> **项目路径**: `/Users/yanfeiyu03/Downloads/scholar-agent-public/`

---

## 一、项目定位（一句话）

ScholarAgent 是一个**认知驱动的学术审稿 Agent**——不是 workflow 引擎，不是 tool router，而是一个能像人类审稿专家一样持续思考、质疑、验证的 50+ 模块认知系统。帮研究者完成"审稿→修改→去AI味"的完整闭环。

---

## 二、架构现状

### 项目成熟度

| 维度 | 说明 |
|------|------|
| 测试 | 992 unit tests 全部通过 |
| 评估 | F1=0.72（vs V2 基线 +0.16）|
| Skill 系统 | V4 完整实现（知识型注入 + 操作型动态 tool + 外部 Skill）|
| 认知架构 | 完备：PCG + Token Budget 3-zone + Evolution + MetaReflector + Evidence Chain |

### 唯一活跃代码

```
v2/                    ← 完全自包含（conftest.py 主动移除 repo root 防 shadow import）
├── core/              ← 50+ 模块
├── skills/            ← 8 知识型 + 1 操作型 + registry.json
├── tests/             ← 992 tests
├── llm/               ← Friday API client (gpt-4o-mini)
├── config/            ← 阈值配置
├── evaluation/        ← 评估框架
└── main.py            ← REPL 入口
```

### 核心模块速查

| 模块 | 一句话职责 |
|------|-----------|
| `core/loop.py` | 认知循环：think-act cycle |
| `core/harness.py` | 工具执行 + 状态守护 + quality gate |
| `core/boundary_guard.py` | 边界约束（纯函数）+ Completion Quality Gate |
| `core/compaction.py` | Smart Compaction + 7 层恢复优先级 |
| `core/paper_cognition_graph.py` | PCG 图认知（section 级，从 PaperStructureIndex 继承骨架）|
| `core/habits.py` | 19 条认知习惯，HabitSelector 动态选取 5 条/轮 |
| `core/token_budget.py` | Three-Zone Budget（Zone A: identity, Zone B: paper, Zone C: dialogue）|
| `core/gate_config.py` | Completion Gate 3 层配置源（cognitive_hints > experience > defaults）|
| `core/skill_registry.py` | Skill 注册 + query(paper_type, phase, budget) |
| `core/skill_handler_loader.py` | 操作型 Skill handler importlib 动态加载 |
| `core/assembler.py` | Context 组装管道（Section Registry + Token Pipeline + PHASE cache）|
| `core/phases.py` | Phase FSM（ORIENTATION → DEEP_REVIEW → SYNTHESIS → EDITING → COMPLETION），全 nudge 无 block |
| `core/evolution.py` | EvolutionEngine + HabitLearner + IntraSessionContrast |
| `core/hypothesis.py` | HD-WM 假说推演 |

### 架构哲学

**单 Agent + 状态机 + 黑板**。不做 multi-agent：审稿和修改是同一个思考实体的不同模式，上下文连贯比分裂更重要（见 COGNITIVE_ANCHOR §2.3）。所有跨轮次信息由 Harness 维护并注入 context（LLM = 无状态 CPU）。

---

## 三、十五条设计约束（不可违反）

| # | 约束 | 核心含义 |
|---|------|---------|
| C1 | Agent = Loop + Tools | 不是 template，不是 workflow |
| C2 | LLM = 无状态 CPU | 跨轮次靠 state 注入 |
| C3 | 控制流 > Prompt Engineering | 优化逻辑，不是措辞 |
| C4 | 分层压缩（Token Pipeline）| Context 是有限带宽 |
| C5 | **Constrain, don't control** | Harness 约束边界，边界内 Agent 自主 |
| C6 | Keep it simple | 最简方式达目标 |
| C7 | 有界递归 MAX_META_DEPTH=2 | Level 3 禁止 |
| C8 | 外部度量锚点 | quality_score 不来自 LLM 自评 |
| C9 | 先验证基座再建上层 | |
| C10 | 累积验证 + 回滚优先 | |
| C11 | 编辑边界不变 | |
| C12 | 图认知优先 | 查 PCG 不重读论文 |
| C13 | 单 session 闭环验证 | |
| C14 | Skill 是参考，不是指令 | 认知辅助框架措辞 |
| C15 | 动态扩展不改静态核心 | list concat，handler 失败必须降级 |

**最重要的一条是 C5**——贯穿整个项目。所有 nudge/signal/gate 都是建议，Agent 有最终决策权。

---

## 四、本轮会话完成了什么

### 已产出文件

| 文件 | 内容 |
|------|------|
| `docs/V2_UPGRADE_EXECUTION_PLAN.md` | **完整执行计划**：Phase A (6 tasks) + Phase B (8 tasks) + Phase C (1 task)，每个任务含目的/现状/步骤/测试要求 |
| `docs/SCHOLAR_AGENT_V3_PROMPT.md` | **开发 System Prompt**（已更新为 V2 Upgrade 版本）|
| `docs/HANDOVER_PROMPT.md` | 本文件 |

### 本轮关键决策记录

**决策 1：v1 残留现在删**
- 根目录 `core/`, `tests/`, `main.py`, `llm/`, `config/`, `tools/` 等是 V1 时期遗留
- `v2/conftest.py` 主动移除 repo root 防 shadow import → 证明 v2/ 完全自包含
- `MIGRATION_NOTE.md` 已明确授权删除
- **结论**: 不等了，Phase A 第一步就删

**决策 2：Completion Quality Gate 需要改措辞**
- 技术上 `DEFAULT_MIN_FINDINGS_FOR_EXIT = 0`（不硬卡），只在 Agent 自己通过 cognitive_hints 设了 min_findings 时才触发
- 但 nudge 措辞仍暗示"你不够"→ 对 LLM 施压 → Agent 倾向补低质量 findings
- **结论**: 改为呈现两个等权假说（论文好 vs 遗漏维度），让 Agent 自行判断

**决策 3：Skill 安装到 ScholarAgent 本身**
- 用户明确拒绝"复杂 Skill 推到外部产品（CatDesk）"的方案
- **结论**: Skill 系统必须自包含——用户下载 Skill 包 → 运行 installer → Agent 可用。复杂 Skill 通过 Claude Code 适配。不依赖外部产品。

**决策 4：SkillClaw lifecycle 是基础设施**
- 不是未来的事，是 Skill 安装故事的前置
- registry.json 需要 version/status/installed_at 字段
- loader 需要按 status 过滤

**决策 5：Hermes Agent 可借鉴项**
- pre_compact_hook: 压缩前通知模块 flush（SessionMemory pending notes）
- capacity % 信号: Agent 主动感知余量，而非被动等压缩
- frozen snapshot: 多次压缩的恢复信息 append-only，认知连贯性

**决策 6：Habits + PCG 学科增强**
- habits.py: 添加 `discipline_triggers: dict[str, list[str]]`，学科特异 +25 boost
- paper_cognition_graph.py: 添加 `_apply_domain_template(paper_type)`，预设关键 edge 权重
- 两者都是"认知增强"而非"行为控制"

---

## 五、下一步工作（优先级排序）

### 立即执行：Phase A（仓库结构清理）

**为什么先做 A**: CLAUDE.md 指向的路径全是旧代码，不清理就无法有效工作。Phase B 的所有文件引用都基于 A 完成后的结构。

| 顺序 | 任务 | 耗时 |
|------|------|------|
| A1 | 删除根目录 v1 残留（core/, tests/, main.py, llm/, config/, tools/ 等）| 15min |
| A2 | 处理根目录 skills/, guidelines/, examples/（对比 v2/ 后删除或移入）| 10min |
| A3 | **重写 CLAUDE.md**（最重要——所有路径指向 v2/）| 20min |
| A4 | .gitignore 添加 .cache/, .pytest_cache/ | 5min |
| A5 | MIGRATION_NOTE.md 改为"已完成"状态 | 5min |
| A6 | docs/ 旧计划归档到 docs/archive/ | 10min |

**验证**: `cd v2 && python -m pytest tests/ -q --tb=short` 全部通过。

### 然后：Phase B（可并行的 batch）

**Batch 1（独立并行）**: B1 + B2 + B3 + B4 + B5
**Batch 2（有依赖）**: B6（依赖 B5）、B7（依赖 B4）、B8（与 B4 共享）

每个任务的详细步骤、代码示例、测试要求见 `V2_UPGRADE_EXECUTION_PLAN.md`。

### 最后：Phase C

C1 Streaming 调研 → 只产出设计文档，不做实施。

---

## 六、关键设计洞察（本轮对话产生）

### 洞察 1：Nudge 的存在本身就是压力

即使 nudge 技术上允许 override，LLM 的 next-token prediction 被 nudge 内容影响——它看到"你只有 2 条 findings，通常应有 5 条"就会倾向补 findings。C5 要求的不只是"允许 override"，而是**措辞本身不能有偏向性**。

### 洞察 2：Skill 的自包含性是设计底线

"凭什么要把 skill 放到别的产品中，你这样就是不完整的可笑的设计"——用户原话。ScholarAgent 必须是完整的、不依赖外部产品的系统。Skill 的完整 lifecycle（安装、激活、停用、卸载）都在 ScholarAgent 内部完成。

### 洞察 3：执行计划需要"目的"字段

执行 Agent 不是盲目跟步骤的机器。如果方案有问题，它需要知道"为什么要这么做"才能自行调整。每个任务的 Purpose 比 Steps 更重要。

### 洞察 4：v2/ 的自包含性已被代码证明

`v2/conftest.py` 第 10-12 行主动 `while _repo_root in sys.path: sys.path.remove(_repo_root)` — 这不是"将来可以删根目录"，而是"根目录的存在已经是个需要防御的问题"。删除是修正，不是冒险。

---

## 七、DO / DON'T 速查

### MUST DO

1. 改代码前读 `docs/COGNITIVE_ANCHOR.md` 确认不违反设计红线
2. 每改一步跑 `cd v2 && python3 -m pytest tests/ -x -q`
3. 完成任务后更新 `V2_UPGRADE_EXECUTION_PLAN.md` 的 `☐` → `☑` + 添加实现记录
4. Gate/nudge 措辞必须无偏向——呈现事实和可能性，不暗示方向
5. 新功能必须有 Kill Switch 守卫（godel_config.py 的环境变量控制）
6. Skill 加载失败 = warn + skip，不中断审稿
7. capacity % 和 compaction capacity 用单一数据源
8. Phase A 全做完再做 B（路径依赖）

### DON'T

1. **不 import 根目录旧代码** — v2/ 是自包含的，用 `from core.xxx import` 而非 `from v2.core.xxx`
2. **不改 SCHOLAR_TOOLS 常量** — 操作型 Skill 通过 list concat 扩展
3. **不让 Skill 进 Zone B** — Zone B 是论文内容专用，Skill 走 Zone A
4. **不执行外部 .py 文件** — handler 必须在 `v2/skills/skill_handlers/` 内
5. **不让模板覆盖 Agent 输入** — Agent override 优先于 template seed
6. **不追求 Level 3 递归** — MAX_META_DEPTH = 2
7. **不标"完成"除非测试通过** — 代码写了 ≠ 完成
8. **不在 Phase A 改 v2/ 的任何代码** — Phase A 只做删除和文档，零代码风险
9. **不把 Skill 功能推给外部产品** — ScholarAgent 必须自包含

---

## 八、参考文档索引

| 什么时候读 | 读什么 |
|-----------|--------|
| **每次开工** | `CLAUDE.md`（Phase A3 后已更新）|
| **理解设计哲学** | `docs/COGNITIVE_ANCHOR.md` |
| **看执行计划** | `docs/V2_UPGRADE_EXECUTION_PLAN.md` |
| **看具体模块实现** | 直接读 `v2/core/` 对应 .py 文件的 docstring |
| **看 Skill 系统设计** | `docs/archive/V4_SKILL_LOADING_PLAN.md` + `v2/skills/registry.json` |
| **看历史进度** | `docs/PROGRESS.md`（5685 行，Phase 0-21 全记录）|
| **理解 Gate 配置** | `v2/core/gate_config.py` docstring（3 层优先级说明）|
| **理解 Compaction** | `v2/core/compaction.py` docstring（7 层恢复优先级）|
| **给其他 Agent 提供开发 context** | `docs/SCHOLAR_AGENT_V3_PROMPT.md` |

---

## 九、Phase A 执行起手步骤

```bash
# 1. 进入项目目录
cd /Users/yanfeiyu03/Downloads/scholar-agent-public

# 2. 确认状态
git status
git log --oneline -3

# 3. 读计划（确认最新）
cat docs/V2_UPGRADE_EXECUTION_PLAN.md | head -80

# 4. 开始 A1：删除根目录 v1 残留
rm -rf core/ tests/ tools/ llm/ config/
rm -f main.py run_hdwm_e2e_quick.py fake.md fake.pdf fake_paper.md

# 5. 验证 v2 不受影响
cd v2 && python -m pytest tests/ -q --tb=short
cd ..

# 6. A2：对比根目录 skills/ vs v2/skills/
diff -r skills/ v2/skills/ 2>/dev/null | head -20
# 根据结果决定删除或移入 v1/

# 7. A3：重写 CLAUDE.md（最重要）
# 参照 V2_UPGRADE_EXECUTION_PLAN.md 中 A3 的模板

# 8. A4-A6：.gitignore + MIGRATION_NOTE + docs 归档
```

---

## 十、常见问题

### Q1: 为什么根目录还有 `v1/`, `legacy/`, `poc/`？

`v1/` 是 V1 的完整独立副本（prompt-stacking 模式），保留为对比基线，不修改。`legacy/` 是最初的 workflow 架构（56 tools 的 handler 式设计），只参考复用逻辑。`poc/` 是早期概念验证。三者都不参与 v2/ 开发。

### Q2: `v2/core/` 里为什么有 50+ 个文件？

认知架构的每个子系统是独立模块：PCG、Token Budget、Evidence Chain、Evolution、Compaction、Habits、Hypothesis、MetaReflection、Phases、Boundary Guard……每个 200-600 行，职责单一。这不是过度设计——每个模块在评估中证明了对 F1 的贡献。

### Q3: Skill 系统已经完整了，为什么 B5/B6 还要做？

V4 完成了 Skill 的**运行时加载**（registry → query → inject/register）。B5/B6 做的是**用户侧 lifecycle**——用户能安装/卸载/停用 Skill，而不是手动编辑 registry.json。这是"架构完整"到"可用完整"的最后一步。

### Q4: DEFAULT_MIN_FINDINGS_FOR_EXIT = 0 意味着 Gate 不做什么？

对。默认是 0 = 永不触发 min_findings nudge。只有当 Agent 自己通过 `generate_cognitive_hints` 设了 `min_findings_for_exit > 0` 时才会触发。这是 C5 的体现——Agent 自己设标准，Gate 提醒它是否达到了自己的标准。B3 要改的是提醒的措辞，不是机制。

### Q5: 跑测试的正确方式？

```bash
cd /Users/yanfeiyu03/Downloads/scholar-agent-public/v2
python -m pytest tests/ -q --tb=short
# 不要在 repo root 跑——会触发旧 tests/ 目录（Phase A1 删除后不再是问题）
```

---

*交接 prompt 完成。接手后第一步：读 `docs/V2_UPGRADE_EXECUTION_PLAN.md`，从 Phase A1 开始执行。*
