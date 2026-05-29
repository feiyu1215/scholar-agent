# ScholarAgent V2 — 功能走查验证计划

> 目标：系统性验证 Agent 的每一项功能是否在真实场景中正常工作
> 方法：分层验证（黑盒→灰盒→白盒），由外到内，按风险优先级排序
> 产出：每个功能的 PASS/FAIL/PARTIAL 状态 + 出错时的排障路径

---

## 验证层次总览

```
Layer 1: 基础可用性（能不能跑起来）
    ↓
Layer 2: 核心工具链（每个工具能不能正确执行）
    ↓
Layer 3: 高级认知功能（MCL/HD-WM/Spawn/Evolution 是否生效）
    ↓
Layer 4: Skills 体系（领域知识+多模态是否被激活和产出）
    ↓
Layer 5: 训练子系统（对抗训练闭环是否可运行）
```

---

## Layer 1: 基础可用性

### L1.1 论文加载

| 验证点 | 操作 | 预期结果 | 排障路径 |
|--------|------|----------|----------|
| PDF 加载 | `python main.py evaluation/test_papers/paper_001.pdf` | 正常启动，打印 section 列表 | `core/pdf_loader.py` → 3 级 fallback（pymupdf→pdfplumber→regex） |
| Markdown 加载 | `python main.py examples/sample_paper.md` | 正常启动 | `core/paper_loader.py` → `_load_markdown()` |
| 大 PDF（>50页）| 用 examples/radiology_chan_gentzkow_yu.pdf | 不超时，section 数 >10 | `core/pdf_loader.py` font-aware heading detection |
| 参考文献加载 | `--references examples/sample_paper_rdd.pdf` | reference 可被 `read_reference` 工具访问 | `core/agent.py` → `_load_references()` |

### L1.2 基本审稿循环

| 验证点 | 操作 | 预期结果 | 排障路径 |
|--------|------|----------|----------|
| 单轮审稿完成 | `python main.py paper_001.pdf --max-turns 30` | 产出 ≥3 findings，正常退出 | `core/loop.py` → `cognitive_loop()` 主循环 |
| Phase 自动转换 | 观察日志 | INITIAL_SCAN → DEEP_REVIEW 自动触发 | `core/phases.py` → 转换条件（sections_read≥3 + findings≥1） |
| Done 信号 | Agent 自主调用 mark_complete | 循环正常结束 | `core/boundary_guard.py` → `check_completion_gate()` |
| Doom loop 保护 | `--max-turns 5`（故意设短） | 强制停止 + 警告消息 | `core/boundary_guard.py` → `check_doom_loop()` |
| 多轮对话 | interactive 模式 + 追问 | Agent 回应 + 保持上下文 | `core/agent.py` → `chat()` |

### L1.3 输出格式

| 验证点 | 操作 | 预期结果 | 排障路径 |
|--------|------|----------|----------|
| get_findings() | 审稿完成后调用 | 返回结构化 list[dict] | `core/harness.py` → `state.findings` |
| get_stats() | 审稿完成后调用 | 返回 token usage + turns + timing | `core/harness.py` → `_compute_stats()` |
| Session Memory | 检查 `.workspace/metrics/session_summary.jsonl` | 新增一条记录 | `core/session_finalizer.py` |

---

## Layer 2: 核心工具链

> 验证策略：在 interactive 模式下通过自然语言引导 Agent 使用特定工具，或直接调用 tool_handlers

### L2.1 阅读工具

| 工具 | 触发方式 | 预期行为 | 关键参数 | 排障 |
|------|----------|----------|----------|------|
| `read_section` | Agent 自主调用 | 返回 ≤6000 chars + claim signals | section_name, offset | `tool_handlers/reading.py` L30-80 |
| `read_section` (续读) | Agent 用 offset 继续 | 从上次位置续读 | offset > 0 | 同上，检查 continuation 逻辑 |
| `list_sections` | Agent 主动调用 | 返回所有 section 名称 | 无 | `core/harness.py` → `paper_index` |
| `search_literature` | Agent 检索外部文献 | 返回 ≥1 结果 | query string | `core/web_search.py` → 4 后端 fallback |
| `fetch_paper_detail` | Agent 深入某篇引用 | 返回 TLDR + citation 等 | paper_id | `core/web_search.py` → Semantic Scholar |
| `read_reference` | Agent 读参考文献 | 返回 reference content | ref_name | `tool_handlers/reading.py` → workspace refs |

### L2.2 记录工具

| 工具 | 触发方式 | 预期行为 | 关键验证点 | 排障 |
|------|----------|----------|------------|------|
| `update_findings` | Agent 记录问题 | findings 列表增长 | **去重逻辑**：相似 finding 不重复添加 | `tool_handlers/findings.py` 三信号去重 |
| `review_findings` | Agent 回顾发现 | 返回当前 findings 列表 | 格式正确，带 severity | `core/harness.py` |

### L2.3 元认知工具

| 工具 | 触发方式 | 预期行为 | 关键验证点 | 排障 |
|------|----------|----------|------------|------|
| `reflect_and_plan` | Agent 自发反思 | 更新 CognitiveState | strategy/open_questions 字段更新 | `tool_handlers/metacognition.py` |
| `mark_complete` / `done` | Agent 认为审稿完成 | 触发 completion gate 检查 | MCL 评审是否触发 | `tool_handlers/misc.py` + `meta_cognition_layer.py` |

### L2.4 编辑工具

| 工具 | 触发方式 | 预期行为 | 关键验证点 | 排障 |
|------|----------|----------|------------|------|
| `generate_edit_plan` | 切换 writer persona 后 | 产出编辑计划 | 格式结构化 | `tool_handlers/editing.py` |
| `edit_paragraph` | Writer 修改段落 | 返回修改后文本 + 验证反馈 | EDIT-5 三级验证 | 同上，检查 PASS/WARN/FAIL |
| `reword_sentence` | Writer 改写句子 | 单句修改 | AI 信号检测 | `core/deai_detector.py` |

### L2.5 交互/并行工具

| 工具 | 触发方式 | 预期行为 | 关键验证点 | 排障 |
|------|----------|----------|------------|------|
| `talk_to_user` | Agent 需要澄清 | 循环暂停，等用户输入 | LoopTalk 信号正确返回 | `core/loop.py` → `__TALK__` 信号 |
| `spawn_perspective` | Agent 分裂子视角 | 独立子循环运行 | 子视角 findings 回注主循环 | `core/loop.py` → `_run_sub_perspective()` |
| `spawn_parallel_readers` | Agent 并行多视角 | N 个并发子循环 | 不超过 4 个并发 | `tool_handlers/misc.py` max=4 |
| `switch_persona` | Agent 切换到 writer | persona 变更，工具集变更 | 身份切换后工具过滤正确 | `core/identity.py` |

### L2.6 验证工具

| 工具 | 触发方式 | 预期行为 | 关键验证点 | 排障 |
|------|----------|----------|------------|------|
| `detect_ai_signals` | 检测论文 AI 味 | 返回信号列表 + 评分 | 14 种信号 + 5 维度评分 | `core/deai_detector.py` |
| `verify_citations` | 验证引用一致性 | undefined/orphaned 列表 | 支持 natbib/biblatex | `core/bib_verify.py` |
| `recall_context` | 回忆跨 session 经验 | 返回相关 patterns | Memory store 非空时有内容 | `core/memory.py` |

---

## Layer 3: 高级认知功能

### L3.1 MetaCognitionLayer (MCL)

| 验证点 | 操作 | 预期行为 | 排障 |
|--------|------|----------|------|
| MCL 启用状态 | 检查 `MCL_ENABLED` 环境变量 | 默认 ON | `core/meta_cognition_layer.py` |
| Stagnation 检测 | 连续 3 轮无新 finding | MCL 注入突破建议 | `check_stagnation()` |
| Completion Gate | Agent 调用 mark_complete | MCL 评审 pass/block | `gate_completion()` → gpt-4.1-mini |
| Block 后恢复 | MCL block 一次后 | 第二次 mark_complete 直接通过 | `_blocked_once` flag |

### L3.2 Hypothesis-Driven Working Memory (HD-WM)

| 验证点 | 操作 | 预期行为 | 排障 |
|--------|------|----------|------|
| HD-WM 激活 | `--hdwm` 参数 | 3 个额外工具注入 | `core/agent.py` → `enable_hdwm` |
| 假说生成 | Agent 调用 `generate_hypothesis` | 假说列表增长 | `core/hypothesis.py` |
| 证据累积 | Agent 调用 `add_evidence` | 假说的 evidence 列表增长 | 同上 |
| 假说裁定 | Agent 调用 `resolve_hypothesis` | status → supported/refuted | 同上 |
| 饱和检测 | 所有假说已裁定 | HD-WM tick 提示"假说已饱和" | `tick()` → saturation detection |

### L3.3 Spawn 子视角系统

| 验证点 | 操作 | 预期行为 | 排障 |
|--------|------|----------|------|
| 单子视角 | Agent 调用 spawn_perspective | 子循环运行 + findings 回注 | `loop.py` → `_run_sub_perspective()` |
| 并行子视角 | Agent 调用 spawn_parallel_readers | 多个并发 | `loop.py` → `_run_parallel_perspectives()` |
| 自动 Spawn | boundary_guard Phase 2 触发 | 50% 进度时自动提议 spawn | `check_auto_spawn_needed()` |
| 子视角工具限制 | 子视角内无 spawn/edit/talk | 确认工具过滤生效 | `SUB_PERSPECTIVE_IDENTITY` |

### L3.4 Evolution 进化引擎

| 验证点 | 操作 | 预期行为 | 排障 |
|--------|------|----------|------|
| Session 反思 | 审稿结束后 `end_session_with_reflection()` | 产出 procedural learnings | `core/reflection_engine.py` → GlobalReflector |
| Habit 选择 | 下次审稿时 | 基于 phase 选择 ≤5 habits | `core/habits.py` → HabitSelector |
| Progressive Disclosure | 随 turn 增加 | full → short → name-only | 同上，观察 token 节省 |
| IntraSession Contrast | 对比 A/B findings | delta>0.15 时强化 | `core/evolution.py` |

### L3.5 Dual Loop 编排器

| 验证点 | 操作 | 预期行为 | 排障 |
|--------|------|----------|------|
| PaperProfile 生成 | 审稿启动时 | 自动检测方法论/复杂度/领域 | `core/orchestrator.py` → PaperProfile |
| 策略模板匹配 | 基于 profile | 选择 5 种模板之一 | ReviewPlanner |
| 动态重规划 | 执行中偏离计划 | PlanUpdate 注入建议 | DualLoopSignal |
| Kill Switch | `SCHOLAR_GODEL_DUAL_LOOP=0` | 所有方法变 no-op | GodelConfig |

---

## Layer 4: Skills 体系

### L4.1 知识型 Skills（9 个）

| Skill | 触发条件 | 预期产出 | 排障 |
|-------|----------|----------|------|
| `overclaim_rules` | 经济学论文 + deep_review phase | 提供 overclaim 检测规则 | `skills/overclaim_rules.md` |
| `methodology_checklist` | 实证论文 | 方法论检查清单 | `skills/methodology_checklist.md` |
| `review_criteria` | 所有论文 | 审稿评分标准 | `skills/review_criteria.md` |
| `econ_writing` | 经济学论文 + editing phase | 写作规范 | `skills/econ_writing.md` |
| `chinese_academic_standards` | 中文论文 | 中文学术规范 | `skills/chinese_academic_standards.md` |
| `data_availability` | 实证论文 | 数据可用性检查 | `skills/data_availability.md` |
| `section_responsibility` | 所有论文 | 各 section 审查重点 | `skills/section_responsibility.md` |
| `deai_rules` | editing phase | AI 写作去味规则 | `skills/deai_rules.md` (8700 tokens) |
| `structured_export` | synthesis phase | 结构化输出工具 | 唯一 action skill，有 tool handler |

### L4.2 经济学原子 Skills

| Skill | 功能 | 验证方式 | 排障 |
|-------|------|----------|------|
| `MathAuditSkill` | 公式编号/符号/维度检查 | 喂含公式的论文 | `skills/economics/math_audit.py` |
| `DIDAnalysisSkill` | DID 方法论审查 | paper_001 (DID 论文) | `skills/economics/functional.py` |
| `IVAnalysisSkill` | IV 方法论审查 | 需 IV 论文 | 同上 |
| `StandardErrorCheckSkill` | 标准误选择检查 | 实证论文 | `skills/economics/atomic.py` |
| `EndogeneityHintSkill` | 内生性提示 | 实证论文 | 同上 |

### L4.3 多模态 Skills

| Skill | 功能 | 验证方式 | 排障 |
|-------|------|----------|------|
| `TableExtractionSkill` | 提取表格为结构化数据 | 含 regression table 的 PDF | `skills/multimodal/table_parser.py` |
| `TableConsistencySkill` | 跨表一致性检测（10 条规则）| paper_001 对比 summary+regression | `skills/multimodal/consistency_engine.py` |
| `FigureExtractionSkill` | 提取图表 caption | 含图表的 PDF | `skills/multimodal/figure_extractor.py` |
| `TextTableXrefSkill` | 文本↔表格交叉引用 | 文中引用 "Table 3" vs 实际 | `skills/multimodal/text_table_xref.py` |
| `FigureTextXrefSkill` | 文本↔图表交叉引用 | 文中声明 vs 图表内容 | `skills/multimodal/figure_text_xref.py` |

---

## Layer 5: 训练子系统

### L5.1 弱点分析器

| 验证点 | 操作 | 预期 | 排障 |
|--------|------|------|------|
| 分析现有数据 | `WeaknessAnalyzer.analyze()` 加载 session 数据 | 产出 WeaknessProfile | `training/weakness_analyzer.py` |
| 17 维度分类 | 检查 profile.entries | 覆盖多维度 | DimensionMapper 关键词匹配 |
| 优先级排序 | profile.get_top_priorities() | 按公式降序 | priority 公式 |

### L5.2 对抗样本生成

| 验证点 | 操作 | 预期 | 排障 |
|--------|------|------|------|
| 单样本生成 | `AdversarialGenerator.generate_challenge()` | 产出合法 AdversarialCase | `training/adversarial.py` → LLM Prompt |
| Difficulty 自适应 | 连续 pass 3 次 | 难度升级 | DifficultyController ZPD |
| Batch 生成 | `generate_batch(n=5)` | 5 个样本 | 注意 token 消耗 |

### L5.3 课程学习

| 验证点 | 操作 | 预期 | 排障 |
|--------|------|------|------|
| 课程设计 | `CurriculumDesigner.design_curriculum()` | 产出结构化 TrainingCurriculum | `training/curriculum.py` |
| 阶段推进 | 执行训练样本 | CurriculumStage 状态流转 | NOT_STARTED→IN_PROGRESS→PASSED |
| 自适应调整 | plateau 检测 | 插入 recovery stage | adapt_curriculum() |

### L5.4 训练闭环

| 验证点 | 操作 | 预期 | 排障 |
|--------|------|------|------|
| Mini training | `TrainingLoop.run(rounds=3)` | 完成 3 轮 | `training/training_loop.py` |
| 收敛检测 | 多轮后 | ConvergenceDetector 给出判断 | 4 信号检测 |
| 停止条件 | 达到 target_mastery 或收敛 | 正常退出 + TrainingResult | 7 种 StopReason |

### L5.5 红蓝对抗竞技场

| 验证点 | 操作 | 预期 | 排障 |
|--------|------|------|------|
| ELO 初始化 | 创建 ArenaMatch | 初始 ELO 正确 | `training/red_blue_arena.py` |
| 红队出题 | RedTeam.generate_attack() | 基于 strategy 产出挑战 | 6 种 RedStrategy |
| 蓝队答题 | BlueTeam.defend() | 对挑战做审稿 | Agent 实际调用 |
| ELO 更新 | 一局结束 | ELO 双向更新 + K-factor 衰减 | EloRating 类 |
| Kill switches | 分别禁用 4 个开关 | 对应组件 no-op | 4 个独立 env var |

---

## 验证执行优先级

```
P0 - 必须验证（用户直接可感知）:
  L1.1 论文加载
  L1.2 基本审稿循环
  L2.1 阅读工具
  L2.2 记录工具（特别是去重）
  L2.5 spawn 系统

P1 - 高优先验证（影响审稿质量）:
  L3.1 MCL 完成门
  L3.2 HD-WM
  L4.2 经济学 Skills
  L4.3 多模态 Skills

P2 - 中优先验证（功能完整性）:
  L2.3 元认知工具
  L2.4 编辑工具
  L2.6 验证工具
  L3.4 Evolution
  L3.5 Dual Loop

P3 - 低优先验证（进阶功能）:
  L5.* 训练子系统全部
  L4.1 知识型 Skills（只需确认加载）
```

---

## 验证方法

### 方法 A：端到端审稿（Layer 1 + 部分 Layer 2-3）

```bash
cd /Users/yanfeiyu03/Downloads/scholar-agent-public/v2
python main.py evaluation/test_papers/paper_001.pdf --verbose --max-turns 40
```

观察日志中的工具调用序列、Phase 转换、MCL 评审。

### 方法 B：单元调用（Layer 2 特定工具）

```python
# 在 Python REPL 中直接调用 tool handler
from core.tool_handlers.reading import handle_read_section
from core.harness import Harness
# ... 构造 harness，调用单个工具
```

### 方法 C：功能模块独立测试（Layer 4-5）

```python
# 直接实例化 Skill/Training 模块
from core.skills.multimodal.consistency_engine import ConsistencyValidator
validator = ConsistencyValidator()
result = validator.validate(tables_data)
```

### 方法 D：配置开关验证

逐一设置 `SCHOLAR_GODEL_<MODULE>=0`，确认功能 graceful 降级（不崩溃）。

---

## 日志与记录约定

每个验证点的执行记录格式：

```markdown
### [Layer.Point] 验证点名称

- **状态**: PASS / FAIL / PARTIAL / SKIP
- **执行时间**: 2026-05-XX HH:MM
- **观察结果**: （实际行为描述）
- **异常/问题**: （如有）
- **排障定位**: （问题在哪个文件哪一行）
- **修复建议**: （如有）
```

验证日志存放位置：`/v2/evaluation/reports/functional_walkthrough_log.md`

---

## 与 EXECUTION_PLAN 的关系

本计划对应路线图中的 **A.1 深度使用循环**。完成后：
1. 所有 PASS 的功能 → 确认可用，进入日常使用
2. FAIL 的功能 → 记入 bug 清单，后续修复
3. PARTIAL 的功能 → 分析原因（是 bug 还是设计局限），决定是否修复

本计划也为 **B.2 对抗训练** 提供前置验证——如果 L5 训练子系统 FAIL，则 B.2 需要先修复基础设施。
