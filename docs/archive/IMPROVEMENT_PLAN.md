# ScholarAgent — 展示级作品集改进计划

> **目标定位**：让面试官快速理解"你作为 AI PM 如何设计和驾驭一个复杂 Agent 系统"
> 
> 当前状态：Agent 架构设计已完成（86 源文件 / 27K+ 行 / 44 工具 / 8 阶段状态机）。
> 工作重心从"写代码"转向"让人快速理解你写的代码有多好"。

---

## Part A：GitHub 仓库改进（公开展示）

目标：hiring manager 花 2 分钟浏览 repo 就能判断"这不是 toy project"。

---

### A-1. 项目卫生（第一印象）⏱️ 2h

**问题**：根目录 25+ 个 test_*.py + 20+ 个 .json/.log 产物，核心架构被淹没。

| 动作 | 说明 |
|------|------|
| 创建 `tests/` 目录 | 所有 `test_*.py` 移入 |
| 完善 `.gitignore` | 添加 `*.log`、`test_*_report*.json`、`test_coverage*.json`、`.cache/`、`.pytest_cache/` |
| 清理已提交的产物 | `git rm --cached` 所有 test .json/.log 文件 |
| 移除 `thesis_english_v1.md` | 测试用论文不应出现在 public repo |
| 确认 `.workspace/` 状态 | 含真实论文内容，确保未被 commit（或从 history 清除） |

---

### A-2. README 重写为"面试型 README" ⏱️ 3h

当前 README 技术内容扎实但偏内部文档。重写目标：**30 秒让人知道这不是 toy project**。

结构应为：
```
一句话定位 → Badges → Demo GIF/截图 → 3 个核心创新点（每个 ≤3 句）
→ Mermaid 架构图 → Quick Start → 数据指标 → 技术细节折叠
```

具体改进：
- **添加 Badges**：Python version、License、CI status、代码行数
- **添加 Demo GIF**：30 秒 asciinema 录制（见 A-5）
- **Mermaid 架构图**（取代 ASCII art）：画两张
  - Agent Loop 整体流程（含 4 层防御）
  - De-AI Closed Loop（detect → diagnose → rewrite → verify）
- **核心指标醒目展示**：27K 行 / 44 工具 / 168 测试 / L1-L4 benchmark / 22 gold cases
- **技术细节折叠**：用 `<details>` 标签收纳 v1→v4 演进表、完整目录树等

---

### A-3. 缺失的标准文件 ⏱️ 4h

| 文件 | 内容 | 为什么需要 |
|------|------|-----------|
| `CHANGELOG.md` | v1→v4 版本变更记录（按时间倒序） | 展示 iterative delivery + 每版本解决的明确问题 |
| `pyproject.toml` | 取代裸 requirements.txt，带 `[project.scripts]` 入口 | 现代 Python 项目标准信号 |
| `.github/workflows/ci.yml` | ruff lint + pytest（Phase 1 零 LLM cost 测试）+ mypy | 168 个测试有 CI 跑 = 可信 |
| `CONTRIBUTING.md` | 开发环境搭建 + 代码规范 + PR 流程 | 表明考虑过协作 |
| `Dockerfile` | 一键容器化体验 | 降低 evaluator 试用门槛 |

---

### A-4. examples/ 填充 ⏱️ 3h

当前 examples/ 只有 `.gitkeep`。这是最大的错失机会。

需要放入：
- `examples/demo_output/` — 一次完整运行的输出快照
  - `review_consolidated.json`（截断版，展示 5 角色 review 结构）
  - `routed_issues.json`（展示 auto_fix/confirm_fix/guidance 分类）
  - `before_after_diff.md`（一个 section 的修改前后对比）
  - `deai_verdict.json`（De-AI 审计结果，含维度分数）
  - `score_progression.md`（展示分数从 4.0 → 6.5 的提升轨迹）
- `examples/sample_paper.md` — 一篇用于演示的短论文（可以是虚构的 2 页 abstract+intro）
- `examples/run_demo.sh` — 一键运行演示的脚本（使用 --budget minimal 零修改模式）

---

### A-5. Demo 录制 ⏱️ 2h

录制一段 2-3 分钟的 asciinema / GIF，展示完整流程：
```
parse_paper → architecture_diagnosis → review_paper (5 角色并行)
→ route_issues (3 种 action_type 分类) → rewrite_section
→ deai_audit (PEV Loop) → 分数提升展示
```

配合 `--stream` 模式 + `/pause` 命令展示交互性。

工具选择：`asciinema rec` → `agg` 转 GIF → 放到 README 顶部。

---

### A-6. Eval 结果总结 ⏱️ 2h

`eval/reports/` 有 30+ 个 JSON 报告但无人类可读总结。

创建 `eval/RESULTS.md`：
- 当前 L1-L4 各级得分基线
- De-AI gold set 精确率/召回率
- 各信号维度检测准确率
- 历史版本对比（v3 vs v4）
- 一段话总结："在 L1 格式规范上达到 X%，L4 领域专业度达到 Y%"

---

### A-7. 代码集成小改 ⏱️ 2h

| 改动 | 投入 | 价值 |
|------|------|------|
| `main.py` 加 `--failover` 模式 | 20 行 | FailoverClient 已完整实现，接入 = 真正 demo multi-provider |
| `handle_parse_paper` 后自动触发 `adaptive_engine.analyze_paper()` | 10 行 | AdaptiveStrategy 真正"活"起来 |
| `--dry-run` CLI 模式 | 30 行 | 不需 API key 就能展示预估能力（面试 demo 利器） |

---

### A-8. 其他 Repo 改进

- `requirements.txt` 版本约束从 `>=` 改为精确范围（可复现性）
- 核心模块 type hints 加精（`messages: list` → `list[dict[str, Any]]`）
- 添加 `py.typed` marker

---

## Part B：面试准备材料

> **已拆分为独立文件** → [`INTERVIEW_PREP.md`](./INTERVIEW_PREP.md)
> 
> 包含：Architecture Walkthrough 讲稿、设计决策深度讲解、STAR Stories、
> Key Metrics 速查卡、面试 Q&A、Demo 演示脚本、Competitive Positioning、
> "下一步计划"回答框架（6 维度）。
> 
> ⚠️ 该文件不应放入 GitHub public repo（加入 .gitignore）。

---

## Part C：功能集成方向（长期迭代，提升技术深度）

目标：将你已有的其他 Skill/项目能力融合进 ScholarAgent，形成更强的技术纵深。
这些改进放 GitHub，面试时可以作为"未来 Roadmap"讲，也可以选 1-2 项真正实现后作为加分项。

---

### C-1. De-AI 规则引擎升级：融合 deai-writing 的 24+ 条场景规则 ⏱️ 6h

**现状**：`tools/deai/signals.py` 有 12+ 信号类别，主要是 pattern matching + 统计检测。

**融合方向**：deai-writing Skill 有更成熟的场景化规则体系（Burstiness 操控、Perplexity 感知、Voice Profile 注入、结构指纹识别），且覆盖中英文学术 + PRD/技术文档多场景。

**具体动作**：
- 将 deai-writing 的 4 层去 AI 味流程（检测→诊断→改写→验证）的规则提取为 `tools/deai/rules/` 子目录
- 按 S1/S2/S3 场景分文件组织规则集（当前 `skills/deai_rules.md` 是纯文本，升级为结构化 YAML/JSON）
- 引入 deai-writing 的"Voice Profile 8 维度注入改写"逻辑（你已有 `utils/voice_profile.py`，但规则覆盖度可扩展）
- 补充 Perplexity-aware 改写策略：高 perplexity 句子不动、低 perplexity 集中区域重点改

**归属**：GitHub（代码改进）
**面试讲法**："我的 De-AI 不是简单的正则匹配，而是一个分层规则引擎，按论文场景加载不同规则集，配合 Voice Profile 约束改写边界。"

---

### C-2. LaTeX 编译/格式验证集成：引入 latex-paper-en 的执行层 ⏱️ 8h

**现状**：ScholarAgent 处理的论文内容在 `.workspace/paper/sections/*.md` 中，修改后生成 Markdown diff。但无法直接验证 LaTeX 编译、BibTeX 条目、期刊格式合规性。

**融合方向**：latex-paper-en 有完整的编译诊断、BibTeX/Biber 检查、期刊格式验证脚本。

**具体动作**：
- 新增 `tools/latex_verify.py`：接入 latexmk 编译 + 错误解析（graceful degradation：无 LaTeX 环境时输出指导）
- 新增 `tools/bib_verify.py`：BibTeX 条目完整性检查、引用-正文一致性验证
- 在 `action_router.py` 中为 format 类 issue 的 auto_fix 路径添加 LaTeX 编译验证步骤
- 在 `presubmission_check.py` 中增加"LaTeX 编译零 warning"检查项

**归属**：GitHub（新工具模块）
**面试讲法**："Agent 不只改内容——它能验证改完后的 LaTeX 是否还能编译通过，BibTeX 条目是否完整。这是从 review→revise 闭环到 review→revise→compile→verify 全链路。"

---

### C-3. 文献库检索能力：引入 bib-search-citation ⏱️ 4h

**现状**：`tools/web_search.py` 支持 Semantic Scholar + CrossRef 搜索，`tools/literature_verify.py` 验证引用存在性。但不能直接搜索用户自己的 .bib 文献库。

**融合方向**：bib-search-citation 可以按 author/year/venue/keyword 搜索本地 .bib 文件。

**具体动作**：
- 新增 `tools/bib_search.py`：加载用户指定的 .bib 文件，支持语义搜索（topic matching）
- 在 review 阶段的 Literature Reviewer 中注入"从用户文献库搜索相关但未引用的论文"能力
- 在 guidance 类 issue 中，如果 issue 是"缺少关键引用"，自动从文献库推荐候选

**归属**：GitHub（新工具模块）
**面试讲法**："审稿人说'你漏引了 XXX 方向的文献'时，Agent 不只告诉你这个问题——它能从你的文献库里找到具体应该引哪篇。"

---

### C-4. Re-audit 增强：复用 paper-audit 的 issue-level diff ⏱️ 3h ✅ DONE

**现状**：`tools/reaudit.py` 已有 root_cause_key 匹配 + 四状态判定（FULLY/PARTIALLY/NOT_ADDRESSED/NEW）。

**融合方向**：paper-audit 有更精细的 issue-level diff 机制（逐条对比 + 严重程度变化追踪 + 修订质量评分）。

**具体动作**：
- 在 reaudit 中引入"severity 变化追踪"：同一 issue 在 re-review 后 severity 是否下降
- 增加"修订质量分"：不只看"是否 addressed"，还看"addressed 得好不好"
- 输出结构化修订报告：每个 issue 的 before→after 状态 + delta 分数

**归属**：GitHub（现有模块增强）
**面试讲法**："Re-audit 不是简单的'问题修了吗'——它量化追踪每个 issue 的严重程度变化，给出修订质量评分。"

---

### C-5. Rewrite 前结构诊断：引入 nature-polishing 的结构分析 ⏱️ 3h ✅ DONE

**现状**：`tools/architecture_diagnosis.py` 做论文整体结构诊断（沙漏模型验证），但在 rewrite 单个 section 前没有结构层面的预判。

**融合方向**：nature-polishing 有 paragraph-level 结构分析（topic sentence → evidence → transition 的微观结构检查）。

**具体动作**：
- 在 `write_engine.py` 的 rewrite 流程前增加 paragraph structure analysis
- 如果 paragraph 缺少 topic sentence 或 evidence-claim alignment 差，在 rewrite prompt 中注入结构修复指令
- 这让 rewrite 从"句子级润色"升级为"结构+句子"双层改写

**归属**：GitHub（现有模块增强）
**面试讲法**："改写不是逐句润色——Agent 先诊断段落结构是否健康，再针对性修复。"

---

### C-6. 记忆系统重构：从双层孤岛到统一智能记忆 ⏱️ 10h

**设计定位**：

记忆系统不只是"用户写作偏好记录"——它是 Agent 跨会话积累的**全部认知状态**，覆盖 6 类信息：
- **用户习惯**：写作风格偏好、用词倾向、对 Agent 建议的接受/拒绝模式
- **论文知识**：每篇论文的结构特征、核心论点、已发现问题、修订轨迹
- **领域经验**：特定学科的审稿规律（如 CS 论文常见的"实验对比不充分"）
- **工具使用模式**：哪些工具序列在什么场景下效果好/差
- **错误教训**：过去犯的错，避免重复踩坑
- **会话上下文**：当前正在做什么，上次做到哪里

Claude Code 的 Memory 系统面向"多次对话间的用户画像积累"，我们的记忆系统也承担同样的角色，但在 Agent 的学术审稿语境下，"用户画像"只是其中一个维度——更重要的是**论文认知**和**领域经验**的积累。这是领域特化的关键区别。

**部署形态**：完全本地自包含（SQLite + 文件），无需线上服务或用户 ID。下载 repo 后开箱即用。

---

**现状诊断**：

当前记忆系统是"双层孤岛"架构——`utils/memory/`（SQLite 持久层，7 MemoryTypes）和 `utils/session_memory.py`（JSON 文件会话层）彼此独立运作，存在以下结构性问题：

1. **无类型化召回优先级**：7 种 MemoryType 在检索时只做关键词 LIKE 匹配，不区分"这条记忆对当前场景有多重要"
2. **无衰减/新鲜度验证**：MemoryEntry 有 `expires_at` 但无衰减曲线——3 个月前的"user prefers X"和 3 天前的优先级相同
3. **会话层与持久层断裂**：SessionMemory 的 `ToolPattern`/`ImplicitPreference` 不入 SQLite，SessionSummary 在 SQLite 但不被 SessionMemory 消费——两套系统各管各的
4. **无"记忆验证"机制**：过时记忆（如用户已改变偏好）不会被自动挑战或衰减
5. **Paper continuity 仅限浅层**：`recall_paper_context` 只返回 key_issues 列表，不提供 revision trajectory 或 voice drift 信息

**设计参考（Claude Code Memory 架构精华）**：

从 Claude Code 的 Memory 设计中提取 4 个核心理念作为改进锚点：

| Claude Code 理念 | 应用到 ScholarAgent |
|---|---|
| **4 类型分类法**（user/feedback/project/reference） | → 按"寿命"重新组织 7 种 MemoryType 为 3 层：Identity（永久）/ Project（论文级）/ Session（会话级） |
| **3 步判定流程**（Longevity Test → Change Frequency → Cross-Session Value） | → 写入时自动推断记忆的 decay_class（slow/medium/fast），影响召回权重 |
| **Staleness 验证**（使用前先验证是否仍然有效） | → 召回时检查记忆新鲜度，低新鲜度记忆降权或标记待确认 |
| **文件+索引分离**（MEMORY.md 索引 + 详情分文件） | → SQLite 做索引/检索，丰富数据存 JSON segment files（支持增量同步/备份） |

**具体动作**：

**Phase 6a：统一记忆层抽象（4h）**

- 创建 `utils/memory/unified.py`：统一的 `UnifiedMemory` 类，整合 SQLite store + SessionMemory 功能
- 引入 `MemoryTier` 三层分类：
  - `IDENTITY`：用户偏好、写作风格、学科领域（对应 Claude Code 的 user type）—— 极慢衰减
  - `PROJECT`：单篇论文的 review history、issue tracker、voice profile hash —— 中速衰减（论文提交后降权）
  - `EPHEMERAL`：当前会话工具序列、临时错误、context cache —— 快速衰减（48h 后自动清除）
- SessionMemory 的 `ToolPattern` 和 `ImplicitPreference` 纳入 SQLite（新增 `tool_patterns` / `implicit_preferences` 表），消除 JSON 孤岛
- 迁移脚本：从旧 JSON 文件 → SQLite 的一次性数据迁移

**Phase 6b：衰减曲线与新鲜度验证（3h）**

- 为 MemoryEntry 新增 `decay_class` 字段（SLOW/MEDIUM/FAST），根据 MemoryType 自动推断：
  - SLOW：USER_PREFERENCE, FIELD_KNOWLEDGE（跨论文有效）
  - MEDIUM：PAPER_INSIGHT, REVIEW_PATTERN, ERROR_LESSON（跨会话有效，但时效性递减）
  - FAST：SESSION_NOTE, TOOL_USAGE（当前上下文）
- 召回时计算 `freshness_weight`：`base_confidence × decay_factor(age, decay_class)`
  - SLOW: half-life = 90 days
  - MEDIUM: half-life = 14 days
  - FAST: half-life = 2 days
- **Staleness challenge**：当 `freshness_weight < 0.3` 时，召回结果附加 `[STALE — verify before acting]` 标记
- ImplicitPreference 新增 `last_confirmed` 字段：被再次观察到时 refresh；超过 30 天未确认则 confidence 自动衰减

**Phase 6c：智能召回与 Prompt 注入（3h）**

- 重写 `recall_paper_context()` 为 `recall_context(paper_id, phase, budget_mode)`：
  - 按当前 phase 决定召回哪些 tier（PARSING phase 只要 PROJECT+IDENTITY；REVIEW phase 加入 EPHEMERAL 中的 tool patterns）
  - 按 budget_mode 控制注入量（minimal: top 3 memories / full: top 10 + field patterns）
  - 返回结构化 dict 而非裸字符串（便于 prompt template 按需插入）
- 启动时调用 `get_startup_context()` 改为读 UnifiedMemory，同时做 expired entry cleanup
- 新增 `memory_digest()` 方法：生成简洁的"Agent 对此论文的记忆快照"（面试 demo 时展示"Agent 有记忆"的最直观方式）

**面试展示价值**：

- 面试讲法："我的 Agent 不只有短期记忆——它对用户偏好有永久记忆、对论文有项目级记忆、对工具使用有模式学习。而且记忆会衰减，过时信息自动降权，不会用 3 个月前的过时偏好去修改今天的论文。这是从 Claude 的 Memory 架构中学到的核心理念：记忆不是越多越好，而是要有分类、有衰减、有验证。"
- 配合 demo 展示：`session_status` 命令输出 memory digest，展示"Agent 记得上次发现了什么问题、学到了什么偏好"

---

### C-7. 工具元数据声明：为 Router 未来扩展铺路 ⏱️ 2h

**现状**：

当前 56 个工具的风险评估逻辑散布在 `action_router.py` 的 `_touches_thesis`、`_might_introduce_new_claims` 等函数中，属于"逐 case 判断"。工具本身没有声明自己的副作用属性——Router 必须"认识"每个工具才能判定其风险。

当前规模（56 工具）下这是可管理的，但如果未来扩展到 100+，每加一个新工具都要去 Router 里加判断就不可持续了。

**与 Claude Code 的对比**：

Claude Code 的每个操作都有显式的可逆性/影响范围标注（文件删除=不可逆+单文件，命令执行=不可逆+系统级），Router 可以根据元数据自动分级。我们不需要做到同等复杂度（学术论文领域比通用文件系统简单得多），但可以做一个轻量版本。

**具体动作**：

- 在 `tool_schemas.py` 中为每个工具 schema 新增 `meta` 字段：
  ```python
  "meta": {
      "operation": "read" | "write" | "verify",  # 操作性质
      "scope": "sentence" | "paragraph" | "section" | "paper" | "external",  # 影响范围
      "reversible": True | False,  # 是否可逆
      "requires_confirmation": False,  # 是否强制确认（覆盖 Router 默认逻辑）
  }
  ```
- 在 `action_router.py` 中新增 `_assess_risk_from_meta(tool_name)` 函数：从 meta 自动推断风险等级，作为 `_touches_thesis` 等函数的补充（fallback 逻辑：有 meta 用 meta，没 meta 走原逻辑）
- 现有 56 个工具逐步补充 meta（高优先：所有 write 类工具；低优先：read/verify 类）

**投入产出**：2h 搭框架 + 核心 write 工具标注。后续新增工具时只需填 meta 字段即可自动获得风险评估，不需要改 Router 代码。

**归属**：GitHub（代码架构改进）

---

### C-8. 决策可观测性：decision_log + decision_report ⏱️ 4h

**功能启示**：强化"信号→决策→执行→验证"闭环的可观测性。

系统已有完整闭环（review→route→revise→reaudit→score_track），但**可观测性不够**——面试官或用户看不到"决策是怎么做的"。这让系统从"黑箱 Agent"变成"可解释的决策系统"。

**具体动作（3 层，无需新模块）**：

**① decision_log（小改，1.5h）— 在 trace 中记录决策理由**

- 在 `action_router.py` 的 `_route_single_issue` 每个判定分支上，补充 **why-not 解释**：不只记录"选了什么"，还记录"为什么不选另一个"
  - 示例：`"auto_fix chosen: category 'clarity' previously confirmed (run #3), low risk (meta: reversible=True, scope=sentence), no Red Line triggers"`
  - 示例：`"downgraded to confirm_fix: FIRST_OF_TYPE — auto_fix was candidate but category 'evidence_gap' never confirmed before; also _might_introduce_new_claims=True"`
- 在 `RoutedIssue` 中新增 `decision_trace: str` 字段（完整结构化理由），与 `routing_notes`（用户可见简版）区分
- 将 decision_trace 写入 `.workspace/trace/routing_decisions.jsonl`（面试时可展示"Agent 的思考过程"）

**② decision_report（中改，2h）— 论文处理完后输出决策摘要**

- 新增 `tools/decision_report.py`：在 pipeline 末尾（score_track 之后）调用
- 报告内容：
  - 处理概览："本次处理了 N 个 issue，其中 X 个自动修复、Y 个需确认、Z 个超出能力范围"
  - 分数归因："总分从 M.M 提升到 N.N（+K.K）。自动修复贡献 +A.A，确认修复贡献 +B.B"
  - 决策模式："Red Line 拦截 R 次，Budget 降级 B 次，First-of-type 保守确认 F 次"
  - 能力边界："以下 Z 个问题超出自动处理能力：[list with reasons]"
- 输出格式：结构化 JSON（机器可读）+ 人类可读 Markdown summary
- 类比广告系统的 bid explanation / 品牌诊断报告

**③ 叙事类比（面试材料，0.5h）— 执行/决策分离框架**

- 更新 `INTERVIEW_PREP.md`，加入"执行/决策分离"叙事框架：
  - "LLM 负责执行（写句子），Harness 负责约束决策质量（什么该写、写到什么程度、什么绝对不能碰）"
  - 用 C-7 tool_metadata + C-8 decision_report 作为"决策层可观测"的实证支撑

**依赖关系**：
- C-7（tool_metadata）已完成 → decision_log 可引用 meta 信息丰富解释
- C-6（unified_memory）已完成 → decision_report 可引用"Agent 基于什么记忆做出决策"

**归属**：GitHub（代码 + 面试材料）
**面试讲法**："我的 Agent 不是黑箱——每个路由决策都有完整 trace，处理完论文后输出决策摘要，告诉你'我做了什么决策、为什么、每个决策带来了多少分提升'。这和广告系统的 bid explanation 是同一个设计理念。"

---

### C-5+. 集成优先级排序

| 集成项 | 技术难度 | 面试展示价值 | 建议优先级 |
|--------|---------|------------|-----------|
| **C-6 记忆系统重构** | **中高** | **极高（"Agent 有大脑"直觉级展示）** | **★★★★ 最高优先 ✅ 已完成** |
| **C-7 工具元数据声明** | **低** | **中（架构远见，面试可一句话带过）** | **★★★ ✅ 已完成** |
| **C-8 决策可观测性** | **低** | **高（"可解释决策系统"高维类比）** | **★★★ 紧接 C-7** |
| C-1 De-AI 规则引擎升级 | 中 | 极高（核心差异化模块） | ★★★ ✅ 已完成 |
| C-3 文献库检索 | 低 | 高（"Agent 能推荐引文"很直观） | ★★★ ✅ 已完成 |
| C-4 Re-audit 增强 | 低 | 中高（量化闭环完整性） | ★★ 第二做 |
| C-5 结构诊断 | 中 | 中高（"不只润色句子"的深度） | ★★ 第二做 |
| C-2 LaTeX 验证 | 高 | 中（依赖环境，demo 困难） | ★ 最后做 |

---

## 执行顺序总览

### Phase 1：本周（高影响低成本）— GitHub 展示层

| # | 事项 | 归属 | 预计耗时 |
|---|------|------|---------|
| 1 | 清理根目录 + 完善 .gitignore | GitHub | 2h |
| 2 | 添加 Mermaid 架构图到 README | GitHub | 2h |
| 3 | 写 CHANGELOG.md | GitHub | 2h |
| 4 | 写 eval/RESULTS.md | GitHub | 2h |
| 5 | examples/ 填充示例输出 | GitHub | 3h |

### Phase 2：下周（中等成本）— GitHub 工程完整性

| # | 事项 | 归属 | 预计耗时 |
|---|------|------|---------|
| 6 | README 重写（面试型） | GitHub | 3h |
| 7 | GitHub Actions CI | GitHub | 2h |
| 8 | pyproject.toml + 精确依赖 | GitHub | 1h |
| 9 | Dockerfile | GitHub | 1h |
| 10 | 代码集成小改（failover/adaptive/dry-run） | GitHub | 2h |

### Phase 3：功能集成（技术纵深）— GitHub 代码

| # | 事项 | 归属 | 预计耗时 | 状态 |
|---|------|------|---------|------|
| 11 | **C-6 记忆系统重构（统一记忆层 + 衰减 + 智能召回）** | **GitHub** | **10h** | ✅ 已完成 |
| 12 | **C-7 工具元数据声明（56 工具 + risk assessment）** | **GitHub** | **2h** | ✅ 已完成 |
| 13 | **C-8 决策可观测性（decision_log + decision_report）** | **GitHub** | **4h** | ✅ 已完成 |
| 14 | C-1 De-AI 规则引擎升级（融合 deai-writing） | GitHub | 6h | ✅ 已完成 |
| 15 | C-3 文献库检索（融合 bib-search） | GitHub | 4h | ✅ 已完成 |
| 16 | C-4 Re-audit 增强（融合 paper-audit diff） | GitHub | 3h | ✅ 已完成 |
| 17 | C-5 Rewrite 结构诊断（融合 nature-polishing） | GitHub | 3h | ✅ 已完成 |
| 18 | C-2 LaTeX 验证集成（融合 latex-paper-en） | GitHub | 8h | |

### Phase 4：面试准备（独立时间线）

> 详见 [`INTERVIEW_PREP.md`](./INTERVIEW_PREP.md) 底部的 Checklist。

---

## 一句话总结

**Phase 1-2（GitHub 展示层）= 让人 2 分钟判断"这不是 toy project"**
**Phase 3（功能集成）= 技术纵深加码，从"架构设计好"升级为"功能也真的强"**（C-6 记忆系统为最高优先：让面试官看到"Agent 有大脑、会学习、知道什么该忘"）
**Phase 4（面试准备）= 详见 INTERVIEW_PREP.md**
