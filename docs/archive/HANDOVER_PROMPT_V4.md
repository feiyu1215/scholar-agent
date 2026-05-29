# ScholarAgent V4 Skill Loading — 交接 Prompt

> **写作日期**: 本轮会话结束时
> **会话主题**: V3 全量代码审计 + V4 Skill 加载机制计划制定
> **接手后首要动作**: 读完本文 → 读 `docs/V4_SKILL_LOADING_PLAN.md` → 读 `docs/SCHOLAR_AGENT_V3_PROMPT.md` → 从 Phase A1 开始实现

---

## 1. 项目定位（一句话）

ScholarAgent 是一个帮助研究者完成"审稿→修改→去AI味"闭环的认知 Agent。V3 全部实现完成（992 tests，F1=0.72）。V4 的目标是为 Agent 增加 Skill 动态加载机制——让领域知识和工具能力可以按论文类型按需注入，而不是硬编码修改。

---

## 2. 架构现状

```
v1/  Prompt-stacking 原型（冻结，不再开发）
v2/  HD-WM + Gödel Agent 认知架构（唯一活跃方向，992 tests）
     ├── core/              核心认知引擎（~20 个模块）
     ├── tool_handlers/     工具实现
     ├── llm/               LLM 调用层（完整 retry/timeout/错误分类）
     ├── tests/             全量测试
     ├── evaluation/        评估框架（gold standard 5 篇 + metrics + reports）
     ├── skills/            领域知识文件（8 个 Markdown，当前无加载机制）
     ├── scripts/           E2E 验证脚本
     └── docs/              设计文档
```

架构哲学：**单 Agent + 状态机 + 黑板模式**。不做多 Agent、不做 workflow engine。LLM 是无状态 CPU，所有跨轮次信息由外部 state 维护并显式注入 context。

核心调用链路：

```
harness.py (856行)             运行时环境 / 子系统编排
  └── loop.py                  认知循环 Observe→Think→Act→Update + SignalDispatcher
        └── agent.py           UnifiedReviewAgent + persona 切换
              └── identity.py (1339行)  3 personas + 17 tools（硬编码）
```

Context 组装链路（V4 重点修改区域）：

```
assembler.py (758行)           ContextAssembler → SectionRegistry → priority 裁剪
  ├── sections.py              SectionDefinition + CachePolicy (NEVER/SESSION/PHASE)
  ├── habits.py (421行)        19 CognitiveHabit + HabitSelector（按阶段选 3-5 条）
  ├── paper_type_hints.py (282行)  Agent 自主生成 CognitiveHints + few-shot 模板
  └── token_budget.py          三区预算：Zone A(8K) + Zone B(40K) + Zone C(80K)
```

已注册的 Section 优先级表（关键参考）：

```
100: static_identity        (SESSION cache)
 95: cognitive_habits       (PHASE cache)
 90: paper_overview         (NEVER)
 89: pcg_navigation         (NEVER)
 88: paper_structure        (NEVER)
 86: cognitive_hints        (NEVER)
 85: findings               (NEVER)
 82: hypothesis_status      (NEVER)
 80: references             (NEVER)
 77: zone_b_paper_content   (NEVER)
 75: section_digests        (NEVER)
>>> 73: domain_skills <<<   ← V4 新增位置（PHASE cache）
 70: metacognition          (NEVER)
 65: memory                 (SESSION cache)
 60: offload_refs           (NEVER)
 55: edits                  (NEVER)
 52: evolution_context      (SESSION cache)
 50: resource_status        (NEVER)
```

---

## 3. 本轮会话完成了什么

### 3.1 关键洞察（纠错）

**重大发现**：V3_REFINEMENT_PLAN.md 的验收标准表 checkbox 全部未勾选（`☐`），但**实际代码全部已实现**。之前的交互者可能基于文档判断"Phase A/B 未完成"——这是错误的。经过逐文件代码审计确认：

| V3_REFINEMENT_PLAN 阶段 | 文档标记 | 代码实际状态 |
|---|---|---|
| A1: Zone B 接入 Assembler | ☐ | ✅ assembler.py L398-445 + L656-663 |
| A2: LLM Client Retry/Timeout | ☐ | ✅ llm/client.py 完整错误分类 |
| A3: 真实长论文 E2E | ☐ | ✅ scripts/e2e_long_paper_validation.py (772行) |
| B1: Mock-LLM 集成测试 | ☐ | ✅ tests/mock_llm.py + 3 test files |
| B2: Evaluation Framework | ☐ | ✅ evaluation/ (gold_standard + metrics + reports, F1=0.72) |
| B3: AdaptiveConfig | ☐ | ✅ core/adaptive_config.py |
| C1: Metrics Export | ☐ | ✅ session_finalizer + test_c1_metrics_export.py |
| C3: PDF Ingestion 加固 | ☐ | ✅ 三级 fallback + test_c3 (16 tests) |
| C4: Kill Switch 降级 | ☐ | ✅ test_c4_kill_switch_degradation.py (17 tests) |

**结论**：V3 是一个**代码完成态**项目。文档没及时更新不代表功能未实现。

### 3.2 产出物

| 文件 | 行数 | 说明 |
|------|------|------|
| `docs/V4_SKILL_LOADING_PLAN.md` | ~310 行 | V4 完整执行计划（效仿 V3_REFINEMENT_PLAN 格式）|
| `docs/SCHOLAR_AGENT_V3_PROMPT.md` | ~310 行 | 更新为 V4 开发 system prompt（重写）|
| `docs/HANDOVER_PROMPT_V4.md` | 本文件 | 交接 prompt |

### 3.3 设计过程

1. **输入**: 用户要求用 serious-mode + fundamental-thinking + rational-skepticism 三 skill 做深度审计
2. **五个审计问题**: 整体评价、hardcode 边界合理性、完成度、Skill 加载能力、easyslides 复用性
3. **关键纠错**: 发现计划文档 vs 实际代码的不一致，通过逐文件读代码确认实际状态
4. **V4 计划制定**: 用户要求覆盖"场景模板化"+"资源结构化管理"+"Skill 加载机制"
5. **格式规范**: 用户要求效仿 V3_REFINEMENT_PLAN 格式（预计工时、优先级、前置、现状、步骤、测试、状态标记）
6. **操作型 Skill**: 用户指出不能只考虑知识层，必须覆盖包含脚本/操作的 Skill

### 3.4 未做的事

- **未写任何实现代码** — V4 计划纯设计文档
- **未修改 v2/core/ 下任何文件** — 992 tests 状态不变
- **未运行过测试** — 本轮是审计+设计会话

---

## 4. V4 核心设计（Skill 加载的两层语义）

| 类型 | 加载方式 | 运行时效果 | 示例 |
|------|----------|-----------|------|
| **知识型** | Markdown 文本注入 system prompt（作为 Section） | LLM 获得额外领域规则参考 | `overclaim_rules.md`, `methodology_checklist.md` |
| **操作型** | Markdown 注入 + Harness 动态注册 tools | LLM 获得新工具能力 | "LaTeX 公式验证器"、"结构化审稿报告导出" |

**关键认知**：这和 Claude Code / CatDesk 的 Skill 机制本质相同——Skill 文件 = 注入 prompt 的文本 + 可选的工具声明。ScholarAgent 的 Assembler Section 体系天然支持前者，Harness 的 `_tool_handlers` dict 天然支持后者。不需要发明新机制。

---

## 5. 十五条设计约束

### 原始六条（C1-C6）

- C1: Agent = Loop + Tools（非 workflow engine）
- C2: LLM = 无状态 CPU（跨轮信息必须显式注入）
- C3: 控制流 > Prompt Engineering
- C4: 分层压缩（Token Pipeline）
- C5: Constrain, don't control
- C6: Keep it simple

### Gödel Agent 五条（C7-C11）

- C7: 有界递归 MAX_META_DEPTH = 2（Level 3 禁止）
- C8: 外部度量锚点（不用 Agent 自评）
- C9: 先验证基座再建上层
- C10: 累积验证 + 回滚优先（evidence ≥ 3）
- C11: 编辑边界不变

### V3 两条（C12-C13）

- C12: 图认知优先（查 PCG 而非重读论文）
- C13: 单 session 闭环验证（≥15 sections）

### V4 新增两条（C14-C15）

- **C14: Skill 是参考，不是指令。** 知识型 Skill 注入时必须用认知辅助框架措辞。Agent 有完全自主权决定是否采纳。
- **C15: 动态扩展不改静态核心。** 操作型 Skill 通过 list concat 扩展 tool list，不修改 `SCHOLAR_TOOLS` 常量。handler 加载失败必须 graceful 降级。

---

## 6. 当前开发进度

```
✅ V3 全部功能实现（992 tests, F1=0.72, V2→V3 +0.16）
✅ V3_REFINEMENT_PLAN Phase A (Zone B, LLM Retry, E2E)
✅ V3_REFINEMENT_PLAN Phase B (Mock-LLM, Evaluation, AdaptiveConfig)
✅ V3_REFINEMENT_PLAN Phase C (Metrics, PDF, Kill Switch)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
→  V4 Phase A: 资源结构化管理 ← 从这里开始
   V4 Phase B: 场景模板化
   V4 Phase C: Assembler 集成（知识型 Skill 注入）
   V4 Phase D: 操作型 Skill（动态 Tool 注册）
   V4 Phase E: 外部 Skill 引入
```

---

## 7. 下一步工作（优先级排序）

### 7.1 Batch 1（可并行，~3h 总计）

| 任务 | 内容 | 预计 |
|------|------|------|
| A1 | `skill_registry.py` + `skills/registry.json`（8 个 skill 的结构化元数据） | 1h |
| A2 | `godel_config.py` 新增 `GODEL_SKILL_LOADING_ENABLED` + `SKILL_ZONE_BUDGET` | 30min |
| B1 | `skills/templates/` 目录 + 6 个 YAML 场景模板 | 1.5h（内容密集） |

### 7.2 Batch 2（依赖 Batch 1，~3h）

| 任务 | 内容 | 预计 |
|------|------|------|
| C1 | `assembler.py` 新增 `domain_skills` section (priority=73) + Harness 接线 | 2h |
| B2 | `tool_handlers/metacognition.py` 模板匹配 + seed CognitiveHints | 1h |

### 7.3 Batch 3-5（后续，视需求）

- C2: 模板 recommended_skills 对接 (~1h)
- D1+D2: 操作型 Skill 框架 + 示范实现 (~7h，最复杂)
- E1: 外部 Skill 引入 (~2h)

---

## 8. 关键设计决策（本轮会话产生）

### 决策 1: Priority=73 用于 domain_skills Section

**理由**: 低于 section_digests(75) 和 zone_b(77)——论文自身内容比外部规则重要。高于 metacognition(70)——领域知识对审稿质量贡献大于元认知状态。token 不够时 domain_skills 先被裁剪（保护论文内容和 findings）。

**验证**: grep 现有 priorities 确认 73 空闲无冲突。

### 决策 2: 知识型和操作型 Skill 统一加载机制

**理由**: 两者本质都是"Markdown 注入 prompt"。操作型只是额外需要在 Harness 注册 tool handler。不需要两套独立的加载系统。SkillRegistry 通过 `type: "knowledge" | "action"` 字段区分。

**影响**: registry.json 一份文件管全部 Skill。

### 决策 3: 模板 seed CognitiveHints 而非直接注入 prompt

**理由**: 直接注入会绕过 Agent 的自主生成过程（违反 C5/C14）。通过 seed_hints 预填 CognitiveHints 字段，Agent 的输入 override 模板值。模板是建议的起点，Agent 有完全修改权。

**影响**: 修改 `tool_handlers/metacognition.py` 而非 `assembler.py`。

### 决策 4: 操作型 Skill handler 用 importlib 动态加载

**理由**: Harness 已有 `_tool_handlers[name]` dict lookup 机制。动态加载只需在 init 时多做一步 `importlib.import_module` + `getattr`。失败时 graceful skip + warn log。不需要改 execute_tool 的 dispatch 逻辑。

**影响**: handler 代码统一放在 `v2/skills/skill_handlers/`，与 Skill Markdown 同目录。

### 决策 5: Token 预算从 Zone A 内部分配 2000 tokens

**理由**: Zone A 默认 8000 tokens。分 2000 给 Skill 后剩 6000——仍高于宪法层 `ZONE_A_MIN_TOKENS=6000`。不侵占 Zone B（论文内容 40K）或 Zone C（对话 80K）。

**影响**: 新增 `SKILL_ZONE_BUDGET = 2000` 宪法常量。

### 决策 6: EvidenceChain 不自动追踪操作型 Skill tools

**理由**: EvidenceChain 追踪的是审稿推理链（finding → evidence → conclusion）。操作型 Skill tool（如公式验证器、报告导出）可能是辅助工具，不属于推理链。如果 tool 结果构成 evidence，Agent 应通过已有的 `add_evidence` tool 手动记录。

**影响**: `_register_skill_handler` 不自动 wrap evidence tracking。

---

## 9. DO / DON'T 速查

### MUST DO

1. 实现前先读 `V4_SKILL_LOADING_PLAN.md` 对应 Phase 的完整 section
2. 每个改动后跑 `cd /Users/yanfeiyu03/Downloads/scholar-agent-public/v2 && python3 -m pytest tests/ -x -q`
3. 每完成一步更新 `V4_SKILL_LOADING_PLAN.md` 中 `☐` → `☑`
4. Skill 内容注入用认知辅助框架措辞：`[领域审稿参考 — 按需加载，非指令]...[以上为参考知识]`
5. 所有新代码路径必须有 `if GODEL_SKILL_LOADING_ENABLED:` 守卫
6. Skill 加载失败 = graceful skip + warn log（不中断审稿）
7. 用 `python3` 不是 `python`

### DON'T

1. 不修改 `SCHOLAR_TOOLS` 常量（用 list concat）
2. 不让 Skill 内容进入 Zone B（Zone B 是论文专用）
3. 不执行外部 Skill 的代码（handler 必须在本地 `skill_handlers/` 实现）
4. 不让 Skill 加载阻塞核心审稿流程
5. 不支持网络 URL 加载 Skill（只读本地文件）
6. 不让模板覆盖 Agent 的输入（Agent override 优先于 seed）
7. 不让操作型 Skill tool 自动接入 EvidenceChain
8. 不改 `identity.py`（1339 行，太危险）
9. 不在 V4 修改任何 V3 核心逻辑（PCG、evolution、meta_reflect 等）

---

## 10. 参考文档索引

| 文档 | 何时读 | 说明 |
|------|--------|------|
| `docs/V4_SKILL_LOADING_PLAN.md` | **必读**，实现任何 Phase 前的执行文档 | 当前计划（含验收标准） |
| `docs/SCHOLAR_AGENT_V3_PROMPT.md` | **必读**，给 AI 开发者的完整上下文 | 已更新为 V4 版本 |
| `docs/COGNITIVE_ANCHOR.md` | 改核心文件前的第一性原理检查 | 设计约束来源 |
| `docs/GODEL_AGENT_PLAN_V3.md` | 理解 V3 架构细节（PCG/Signal/Evidence） | V3 理想态设计 |
| `docs/V3_REFINEMENT_PLAN.md` | 理解 V3 完善过程（已全部完成） | 历史参考 |
| `docs/ARCHITECTURE_V2_BLUEPRINT.md` | 理解 V2 架构全貌 | 基座设计 |

---

## 11. Phase A1 起手指南

```bash
# 1. 确认环境
cd /Users/yanfeiyu03/Downloads/scholar-agent-public/v2
python3 -m pytest tests/ -x -q  # 确认 992 tests 全绿

# 2. 创建 registry.json
# 文件: v2/skills/registry.json
# 为 8 个现有 skill 文件编写元数据:
#   - overclaim_rules.md (1200 tokens, all paper types, DEEP_REVIEW/SYNTHESIS)
#   - methodology_checklist.md (2500 tokens, empirical, DEEP_REVIEW)
#   - review_criteria.md (400 tokens, all, all phases)
#   - econ_writing.md (1800 tokens, empirical/theoretical, DEEP_REVIEW/SYNTHESIS/EDITING)
#   - chinese_academic_standards.md (1000 tokens, all, EDITING)
#   - data_availability.md (800 tokens, empirical/clinical, SYNTHESIS)
#   - section_responsibility.md (600 tokens, all, ORIENTATION)
#   - deai_rules.md (3500 tokens, all, EDITING) ← 注意这个很大

# 3. 创建 skill_registry.py
# 文件: v2/core/skill_registry.py
# 核心类:
#   - SkillMeta dataclass (id, type, file, name, tags, paper_types, phases, token_estimate, priority_hint)
#   - SkillRegistry.__init__(skills_dir: Path) → 从 registry.json 加载
#   - SkillRegistry.query(paper_type, phase, budget_tokens) → list[SkillMeta]
#   - SkillRegistry.load_content(skill_id) → str

# 4. 同时做 A2: godel_config.py 新增
# 在现有 flags 后追加:
#   GODEL_SKILL_LOADING_ENABLED: bool = _env_flag("SCHOLAR_GODEL_SKILL_LOADING")
#   SKILL_ZONE_BUDGET: int = 2000
# 在 log_config_status() 的 flags dict 追加 "SkillLoading" 项

# 5. 写测试
# 文件: v2/tests/test_skill_registry.py
# 覆盖:
#   - registry.json parse 正确
#   - query() 按 paper_type 过滤（empirical → methodology_checklist, 不匹配 chinese_academic）
#   - query() 按 phase 过滤（EDITING → deai_rules, 不匹配 ORIENTATION-only skill）
#   - query() 按 budget 裁剪（设 budget=2000, 不能全部加载 deai_rules 3500 tokens）
#   - load_content() 正确读取 Markdown
#   - Kill Switch OFF → query() 被跳过（在上层测试）

# 6. 全量回归
python3 -m pytest tests/ -x -q  # 992+ tests 全绿

# 7. 更新计划文件
# V4_SKILL_LOADING_PLAN.md: A1 状态 ☐ → ☑, A2 状态 ☐ → ☑
```

---

## 12. 常见问题

### Q1: V3 真的完成了？文档 checkbox 都没勾啊？

**是的，完成了。** 这是本轮会话的关键发现。文档的 checkbox 没有及时更新，但逐文件代码审计确认所有功能已实现。评估报告（`evaluation/reports/`）显示 F1=0.72，V3 vs V2 delta=+0.16。992 tests 通过。

### Q2: `v2/skills/` 那 8 个 Markdown 文件现在有什么用？

**当前无用**。它们是领域知识参考文件，内容很好（如 methodology_checklist 478 行覆盖 RCT/DID/IV），但没有任何代码路径会加载它们。identity.py 的 19 条习惯是独立硬编码的。V4 的目标就是让这些文件被代码消费。

### Q3: 为什么不直接把所有 Skill 文件塞进 system prompt？

三个原因：(1) Token 预算——deai_rules.md 单个就 3500 tokens，全部塞进去占 Zone A 的一半以上；(2) 相关性——审理论论文时不需要 methodology_checklist（它是 empirical 专用的）；(3) C5 约束——Skill 是参考不是指令，需要按场景筛选 + 认知辅助措辞包裹。

### Q4: 操作型 Skill 和知识型 Skill 的加载有什么区别？

**加载机制相同**——都是 SkillRegistry 管理，都注入 prompt。**额外步骤不同**——操作型 Skill 在 Harness init 时还会注册其声明的 tools + import handler。如果 handler import 失败，只影响该 tool（graceful skip），知识型注入部分不受影响。

### Q5: 场景模板和 CognitiveHints 什么关系？

CognitiveHints 是 Agent **自主生成**的审稿策略（通过 `generate_cognitive_hints` tool）。模板是**预置的 seed**——当论文类型匹配某个模板时，模板的 seed_hints 预填 CognitiveHints 字段，但 Agent 的输入可以完全 override。Agent 不知道模板的存在——它只看到 CognitiveHints 的初始值被预填了。

### Q6: 这个计划和 CatDesk 的 Skill 体系兼容吗？

**Phase E 专门解决这个问题**。通过 `source: "external"` + 绝对路径 + `extract_sections` 字段，可以引用 CatDesk 生态的 Skill 文件，只提取知识段落。但不执行 CatDesk Skill 的操作指令——如果需要操作能力，handler 必须在本地 `skill_handlers/` 重新实现。

### Q7: Zone A 分 2000 tokens 给 Skill 够吗？会不会和 static_identity 冲突？

够的。Zone A 默认 8000 tokens。static_identity (~1500 tokens) + cognitive_habits (~800 tokens) + Skill (≤2000 tokens) = 4300，远未到上限。而且 Skill 是 priority=73，当 token 真不够时会被优先裁剪（保护 priority=85+ 的 findings 和论文概况）。宪法层 ZONE_A_MIN_TOKENS=6000 不会被突破。

---

## 13. 本轮会话关键文件修改汇总

| 操作 | 文件 | 说明 |
|------|------|------|
| 新建 | `docs/V4_SKILL_LOADING_PLAN.md` | V4 完整执行计划（~310 行）|
| 重写 | `docs/SCHOLAR_AGENT_V3_PROMPT.md` | 更新为 V4 开发 system prompt |
| 新建 | `docs/HANDOVER_PROMPT_V4.md` | 本文件 |
| 未修改 | `v2/core/*.py` | 零代码修改 |
| 未修改 | `v2/tests/*.py` | 992 tests 状态不变 |

---

## 14. `v2/skills/` 现有文件概览

接手后你需要为这些文件写 registry.json 条目，先了解它们的内容：

| 文件 | 行数 | 内容 | 适用论文类型 |
|------|------|------|-------------|
| `deai_rules.md` | 695 | De-AI 检测规则全集（S1 英文CS/S2 中文/S3 经济学） | 所有 |
| `methodology_checklist.md` | 478 | 实证方法论系统性检查清单（identification/sample/estimation） | empirical |
| `overclaim_rules.md` | 220 | Overclaim 检测 + 学术短语清理规则 | 所有 |
| `review_criteria.md` | 38 | 16 类 issue taxonomy + severity 校准 | 所有 |
| `econ_writing.md` | ~200 | 经济学论文写作规范 | empirical, theoretical |
| `chinese_academic_standards.md` | ~150 | 中文学术写作规范 | 所有（中文论文） |
| `data_availability.md` | ~100 | 数据可获取性声明规范 | empirical, clinical |
| `section_responsibility.md` | ~80 | 各 section 审稿职责划分 | 所有 |

---

## 15. 一句话总结

V3 代码已全部完成但文档未更新 checkbox。V4 计划已制定——5 个 Phase 覆盖资源结构化、场景模板、知识型注入、操作型扩展、外部引入。下一步是 **Batch 1 并行实现（A1 + A2 + B1）**——创建 `registry.json` + `skill_registry.py` + `godel_config.py` 修改 + 6 个 YAML 模板。从 `registry.json` 开始。

---

*交接于本轮会话结束 | 后续疑问请先查阅 `docs/V4_SKILL_LOADING_PLAN.md` 对应章节*
