# ScholarAgent V2 — 交接 Prompt

> 本文档是完整的会话交接上下文。新会话开始时，将此文档作为第一条消息发送，即可无缝续接工作。
> 
> 最后更新：2025-07 会话（Token Budget 截断 + 断点续传 实施完成 + A.1 Layer 1 验证完成）

---

## 一、项目定位

ScholarAgent 是一个**学术审稿认知 Agent**——它不是 workflow 引擎，不是 tool router，而是一个具备自主认知循环的审稿人模拟系统。输入一篇学术论文（PDF/Markdown），Agent 自主阅读、推理、发现问题、提出修改建议，输出结构化的审稿报告。

项目性质：个人工具 + 作品集/Portfolio。许可证 GPL-3.0。

---

## 二、仓库结构与路径

```
/Users/yanfeiyu03/Downloads/scholar-agent-public/   ← 仓库根目录
├── CLAUDE.md              ← 认知架构配置（Agent 读的指令，开发规范）
├── README.md              ← 项目介绍
├── DESIGN.md              ← 架构设计总述
├── .env                   ← API 配置（美团 Friday One-API）
├── pyproject.toml         ← Python 项目配置
├── docs/                  ← 核心文档
│   ├── COGNITIVE_ANCHOR.md         ← 第一性原理锚点（不可违反）
│   ├── COGNITIVE_SPEC.md           ← 认知规格说明
│   ├── PROGRESS.md                 ← Phase 1-56+ 完整开发历史（314KB）
│   ├── HANDOVER.md                 ← 旧版交接文档（可对照）
│   └── V2_UPGRADE_EXECUTION_PLAN.md ← 960 行执行计划
├── v2/                    ← V2 主代码（唯一活跃版本，完全自包含）
│   ├── main.py            ← CLI 入口（interactive / full 模式）
│   ├── core/              ← 核心引擎（49 个模块）
│   ├── evaluation/        ← 评估系统（论文 + gold standard + judge）
│   ├── training/          ← 训练子系统（对抗/课程/竞技场）
│   ├── FUNCTIONAL_WALKTHROUGH_PLAN.md  ← 5 层功能验证计划
│   ├── EXECUTION_PLAN.md  ← 完成路线图（四阶段）
│   └── OPTIMIZATION_PROPOSAL.md ← 性能优化方案
├── v1/                    ← V1 存档（prompt 堆叠模式，不修改）
├── legacy/                ← 旧 workflow 架构（仅参考）
└── poc/                   ← 概念验证原型
```

---

## 三、核心架构（五层体系）

### 3.1 架构哲学

- Agent = 认知（cognition），不是编排（orchestration）
- 深度是自主涌现的，不是配置的
- 流程从目标中涌现，不是预设的
- 约束而非控制（Constrain, don't control）
- LLM = 无状态 CPU；Harness = 寄存器 + 内存 + 总线

### 3.2 执行链路

```
main.py → agent.py (组装) → loop.py (驱动) + harness.py (执行)
  harness.py 内: boundary_guard, compaction, token_budget, skills
  assembler.py: Section Registry + Token Pipeline → context 注入
```

### 3.3 五层分解

| 层次 | 组件 | 说明 |
|------|------|------|
| L1 基础框架 | agent, loop, harness, phases, identity | 认知循环骨架 |
| L2 工具链 | 16 个 tool handlers (reading/findings/editing/metacognition/hypothesis/misc) | Agent 的手和脚 |
| L3 高级认知 | MCL, HD-WM, Spawn, Evolution, PCG | 认知增强层 |
| L4 Skills | economics(4) + multimodal(6) + knowledge(9) | 领域知识注入 |
| L5 训练 | adversarial + curriculum + arena + weakness_analyzer | 自我进化闭环 |

### 3.4 关键机制

- **Phase FSM**：INITIAL_SCAN → DEEP_REVIEW → EDITING → SYNTHESIS（全 nudge 无 block）
- **3 Personas**：scholar（默认）、writer、code_reviewer
- **MCL（Meta-Cognition Layer）**：轻量 LLM 层，负责 Sub-Reader 模型路由（按难度分配 high/medium/low 模型）
- **HD-WM（Hypothesis-Driven Working Memory）**：假说生成 → 证据积累 → 确认/否定
- **Token Budget**：安全网模式（Agent 不感知 budget 存在，超限硬截断 + 断点续传）
- **认知习惯渐进加载**：22 条习惯，按 phase/turn 动态选取 5 条，format 从完整→摘要→名称（节省 67% token）
- **Finding 三信号去重**：防止重复发现

---

## 四、环境配置

### 4.1 API 设置（.env）

```
OPENAI_API_KEY=2003426817264898139
OPENAI_BASE_URL=https://aigc.sankuai.com/v1/openai/native
LLM_MODEL=gpt-4.1
LLM_MODEL_HIGH=gpt-4.1
LLM_MODEL_MEDIUM=gpt-4.1-mini
LLM_MODEL_LOW=gpt-4.1-mini
```

使用美团 Friday One-API（OpenAI-compatible 格式）。

### 4.2 Feature Flags（27 个 Kill Switch）

通过环境变量 `SCHOLAR_GODEL_<MODULE>=0/1` 控制，默认全部 ON（除 Streaming 默认 OFF）。

核心分组：
- 基础设施：PCG, BudgetManager, SignalDispatcher, EvidenceChain
- 元反思：FastReflect, DeepReflect, Emergency
- Skill 系统：SkillLoading, SkillX, SkillSynthesis
- 表格/图表：TableProcessing, FigureSemantic
- 双环架构：DualLoop
- 对抗训练：AdversarialTraining, RedTeam, BlueTeam, ELO, Season
- 习惯渐进：HabitProgressive

**宪法层常量（不可修改）**：MAX_META_DEPTH=2, TOTAL_CONTEXT_WINDOW=128000, ZONE_A_MIN_TOKENS=6000

---

## 五、ScholarAgent 构造与调用

### 5.1 构造签名

```python
ScholarAgent(
    paper_path,              # str: PDF/MD/目录路径
    model,                   # str: LLM 模型名
    verbose,                 # bool: 是否打印详细日志
    max_loop_turns=30,       # int: 最大循环轮次（注意：不是 max_turns）
    token_budget=100000,     # int: token 预算（向后兼容，会转化为 BudgetPolicy）
    budget_policy=None,      # BudgetPolicy | None: 显式传入时覆盖 token_budget
    context_window=128000,   # int: 上下文窗口
    persona='scholar',       # str: scholar/writer/code_reviewer
    content_sections=None,   # dict: 预加载的 sections
    reference_paths=None,    # list: 参考文献路径
    enable_hdwm=False,       # bool: 假说驱动工作记忆
    on_stream=None           # callable: 流式回调
)
```

### 5.2 Budget 截断与断点续传

```python
from v2.core.budget_policy import BudgetPolicy

# 方式 1：旧接口（向后兼容）
agent = ScholarAgent(paper_path="paper.pdf", model="gpt-4.1", token_budget=50000)

# 方式 2：新接口（推荐）
agent = ScholarAgent(
    paper_path="paper.pdf", model="gpt-4.1",
    budget_policy=BudgetPolicy(token_limit=50000, allow_pause=True)
)

# Budget 耗尽时 Agent 被硬截断，自动保存 checkpoint
# 断点续传：
result = await ScholarAgent.resume(
    checkpoint_path=".checkpoints/",
    new_token_limit=100000,  # 追加预算
)
```

**设计原则**：Budget 是安全网，不是行为引导。Agent 在运行期间完全不知道 budget 存在，自由运行直到被硬停。子视角终止由 `max_loop_turns=12` 保证，token 消耗事后回流父级。

### 5.3 论文加载方式

```python
# 方式 1：通过 Agent 自动加载（推荐）
agent = ScholarAgent(paper_path="path/to/paper.pdf", model="gpt-4.1", verbose=True)
result = await agent.start()

# 方式 2：手动加载 sections
from v2.core.paper_loader import load_paper
from v2.core.state import WorkspaceState
state = WorkspaceState(...)
sections = load_paper(state, "path/to/paper.pdf")  # 返回 dict[str, str]

# 方式 3：直接 PDF → sections（跳过 Agent）
from v2.core.pdf_loader import load_pdf_as_sections
sections = load_pdf_as_sections("path/to/paper.pdf")  # 24 sections for paper_001
```

⚠️ `load_pdf_as_sections` 只能处理 PDF，不能处理 .md 文件。用 `paper_loader.load_paper()` 可以自动路由。

---

## 六、当前进度

### 6.1 路线图位置

四阶段路线图中，目前处于**阶段 1**（深度使用 + 激活高级 Phase + Recall 提升）。

### 6.2 已完成

| 任务 | 状态 | 说明 |
|------|------|------|
| F.1 Gold Standard 构造 | ✅ | paper_001, paper_003 已标注 |
| F.2 Recall 诊断 | ✅ | Baseline F1=46.3% |
| F.3 P0 修复 | ✅ | AppendixMathAuditSkill, ConsistencyValidator Rule9, PCG appendix weight |
| P0 验证 Rerun | ✅ | 4/6 命中率 |
| B.1 Phase 9A 激活 | ✅ | TableExtraction + TableConsistency |
| F.3-P1 修复 | ✅ | Rule 10 跨表重复 + 顺序下标错误 |
| auto_assign bug 修复 | ✅ | |
| A.1 深度使用循环 Layer 1 验证 | ✅ | 见 §6.3 |
| Token Budget 截断 + 断点续传 | ✅ | 10 步实施 + 审核修复 + 死代码清理 |

### 6.3 本轮会话成果（A.1 深度使用循环）

**Layer 1 核心框架验证**——已通过：
- 45/45 模块 import 无错误 ✅
- PDF 加载：paper_001 → 24 sections ✅
- MD 加载：sample_paper.md → 7 sections ✅
- DeAI 检测器：零 LLM，14 信号类型，正确识别 AI 文本（FAIL 判定） ✅
- ConsistencyValidator Rule 10：正确检测重复 balance table（2 ERROR violations） ✅
- WeaknessAnalyzer：成功 ingest + build_profile ✅
- HabitSelector：22 习惯中选取 5 条 progressive format ✅
- BibTeX verify：检测 undefined refs ✅
- E2E 测试：Agent 自主运行 15 轮，Phase 转换 + MCL 路由 + Finding 去重均正常 ✅

**已知问题**：
- BibTeX parser 对紧凑多条目格式可能只解析 1/4 ⚠️
- httpx AsyncClient 在事件循环关闭时抛 RuntimeError（不影响功能）⚠️

### 6.4 下一步工作（优先级排序）

```
P0: 验收场景 BCE 端到端测试（Budget截断→保存→Resume续传→无限制模式向后兼容）
P1: 验证 Rerun（确认 P1 修复后 Recall 提升）
P2: A.1 继续 —— Layer 2-5 端到端验证（不是逐模块单测，而是不同场景下的端到端跑通）
P3: B.2 Phase 7 对抗训练激活
P4: 阶段 2（skill-craft / WAL / Skill-Evolver 优化方法论引入）
```

---

## 七、验证方法论（A.1 深度使用循环）

### 7.1 验证哲学

**用户明确要求：不是逐模块单测，而是不同场景的端到端验证。** 即：用不同类型的论文 / 不同配置 / 不同 persona，跑完整的 Agent 流程，观察各层功能是否在真实场景中正常协作。

### 7.2 已制定的验证计划

见 `v2/FUNCTIONAL_WALKTHROUGH_PLAN.md`（5 层 × 优先级 P0-P3 × 验证方法 A-D），核心思路：

- **方法 A**：Python 直接调用组件，读输出判断
- **方法 B**：端到端运行 Agent，观察日志中的行为
- **方法 C**：对比不同配置的输出差异
- **方法 D**：注入异常输入，验证容错

### 7.3 推荐的端到端验证场景

| 场景 | 配置 | 验证目标 |
|------|------|---------|
| 标准审稿 | paper_001.pdf, scholar, 30 turns | 基础流程 + Phase 转换 + Finding 质量 |
| 经济学方法论 | paper_003.pdf, scholar, enable_hdwm=True | HD-WM 假说推演 + 经济学 Skill |
| 写作模式 | paper_001.pdf, writer, 20 turns | Persona 切换 + 编辑工具链 |
| 短论文 MD | sample.md, scholar, 10 turns | MD 加载 + 快速收敛 |
| 对抗训练 | 任意论文 + AdversarialTraining=1 | 训练闭环激活 |
| Kill Switch 对比 | 同论文，关闭 MCL/PCG/DualLoop | 功能降级是否优雅 |

---

## 八、关键设计决策（本轮会话洞察）

1. **不做多 Agent**：单 Agent + 状态机 + 黑板模式。理由：审稿是单一认知任务的深化，不是多角色协作。Sub-Reader 是视角轮换不是独立 Agent。

2. **C4 认知分裂问题**：MCL 和主循环可能对 Phase 判断不一致。通过 boundary_guard 的 Completion Quality Gate 解决。

3. **Edit Agent 哲学**：编辑不是独立 Agent，而是审稿认知的自然延伸。persona=writer 时 identity 和工具权限改变，但还是同一个循环。

4. **Theater Code 标准**：任何看起来"正确"但实际不影响输出质量的代码都是 theater code，应该删除。

5. **Budget 是安全网不是行为引导**：Agent 在运行期间完全不知道 budget 存在。不做 WRAP_UP 收尾、不做预警、不改变工具集。截断时完整保存 messages + state + phase，支持断点续传（resume）。子视角由 `max_loop_turns=12` 硬终止，不设独立 token budget。

6. **子视角 Token 事后回流**：子视角消耗的 token 在 gather 完成后回流父级 `state.total_tokens`。父级下一轮 turn 开始时检测是否超限。

---

## 九、DO / DON'T 速查

### DO ✅
- 改代码前先读 `COGNITIVE_ANCHOR.md` 确认不违反核心哲学
- 用 `max_loop_turns` 而不是 `max_turns`
- `WeaknessAnalyzer.ingest_manual(severity=0.8)` severity 必须是 float
- `WeaknessDimension.METHODOLOGY_ANALYSIS`（不是 `.METHODOLOGY`）
- 端到端验证优先于单元测试
- Kill Switch 用 `SCHOLAR_GODEL_XXX=0` 关闭来做对比实验
- 保留运行日志，出错时从日志定位
- Budget 检测放在 LLM 调用之前（turn 开始时），避免多花一轮
- 截断后同时触发 session persistence（end_session），确保新 session 可读
- resume 时正常构造 Harness → 再用 snapshot 覆盖 state

### DON'T ❌
- 不要用 workflow 思维去理解这个系统
- 不要新增 Registry Pattern（已有的 skill_registry 是例外，因为 skill 是静态知识）
- 不要对 phases 做 hard block（只能 nudge）
- 不要修改宪法层常量（MAX_META_DEPTH=2, TOTAL_CONTEXT_WINDOW=128000, ZONE_A_MIN_TOKENS=6000）
- 不要把 `load_pdf_as_sections` 用在非 PDF 文件上
- 不要写 theater code（看起来对但不影响输出的代码）
- 不要做 Scenario Enumeration（穷举场景的 if-else）
- 不要给 Agent 注入任何关于 budget 的信息（包括 warning、nudge、收尾指令）
- 不要给子视角设独立 token budget（由 max_loop_turns=12 硬终止）

---

## 十、调试指南

### 10.1 常见错误与修复

| 错误 | 原因 | 修复 |
|------|------|------|
| `TypeError: ScholarAgent.__init__() got unexpected keyword argument 'max_turns'` | 参数名错误 | 用 `max_loop_turns` |
| `TypeError: can't multiply sequence by non-int of type 'float'` in WeaknessAnalyzer | severity 传了字符串 | severity 必须是 float (0.0-1.0) |
| `AttributeError: WeaknessDimension.METHODOLOGY` | 枚举名错误 | 用 `METHODOLOGY_ANALYSIS` |
| `pymupdf` error when loading .md | 用错了加载函数 | MD 文件用 `paper_loader.load_paper()` |
| httpx RuntimeError on event loop close | asyncio cleanup issue | 不影响功能，可忽略 |
| BibTeX 只解析 1 条 | 紧凑格式兼容性 | 确保条目间有空行 |

### 10.2 调试入口点

- **循环行为异常** → 看 `loop.py` 的 `_execute_turn()` + `harness.py` 的 `_run_tool()`
- **Phase 不转换** → 看 `phases.py` 的 nudge 条件 + `boundary_guard.py`
- **Finding 被误拒** → 看 `findings.py` 的三信号去重逻辑
- **MCL 路由不生效** → 确认 `SCHOLAR_GODEL_MCL=1`，看 `meta_cognition_layer.py`
- **Skill 不加载** → 看 `skill_registry.py` + `godel_config.py` 的 SkillLoading 开关
- **Token 截断** → 看 `budget_policy.py` 的 `is_exceeded()` + `loop.py` 的硬截断逻辑 + `state_checkpoint.py` 的 `save_full_snapshot()`

### 10.3 日志与验证文件

```
v2/evaluation/reports/walkthrough_e2e_output.log    ← E2E 测试日志
v2/evaluation/reports/VERIFICATION_REPORT_FINAL.md  ← 进化管道验证报告
v2/evaluation/gold_standard/                        ← Gold Standard 标注
v2/evaluation/test_papers/                          ← 5 篇测试论文
```

---

## 十一、文档索引（什么时候读什么）

| 场景 | 读什么 |
|------|--------|
| 理解核心哲学 | `docs/COGNITIVE_ANCHOR.md` |
| 了解所有功能模块 | `CLAUDE.md` + `DESIGN.md` |
| 查看开发历史 | `docs/PROGRESS.md` |
| 确认当前路线图 | `v2/EXECUTION_PLAN.md` |
| 了解 V2 升级计划 | `docs/V2_UPGRADE_EXECUTION_PLAN.md` |
| 验证计划参考 | `v2/FUNCTIONAL_WALKTHROUGH_PLAN.md` |
| MCL 设计细节 | `v2/docs/DESIGN_META_COGNITION_LAYER.md` |
| 性能优化方案 | `v2/OPTIMIZATION_PROPOSAL.md` |
| Token Budget + Resume 设计 | `v2/docs/PLAN_token_budget_and_resume.md` |
| Kill Switch 清单 | `v2/core/godel_config.py`（读代码） |

---

## 十二、常见问题 FAQ

**Q1：为什么不用 LangChain / CrewAI / AutoGen？**
A：ScholarAgent 的核心价值是认知深度，不是工具编排。现有框架都是 workflow 思维，与本项目哲学冲突。单 Agent + 状态机 + 黑板模式更适合深度审稿任务。

**Q2：45 个模块都要懂才能开发吗？**
A：不需要。日常开发只需理解 agent → loop → harness 这条主链路。Skills 和 Training 是独立子系统，按需了解。

**Q3：如何跑一次完整的端到端测试？**
A：
```python
import asyncio
from v2.core.agent import ScholarAgent

async def run():
    agent = ScholarAgent(
        paper_path="v2/evaluation/test_papers/paper_001.pdf",
        model="gpt-4.1",
        verbose=True,
        max_loop_turns=15
    )
    result = await agent.start()
    print(result)

asyncio.run(run())
```
从仓库根目录运行：`cd /Users/yanfeiyu03/Downloads/scholar-agent-public && python -u -c "..."`

**Q4：如何验证某个 Kill Switch 的效果？**
A：在 .env 或环境变量中设置 `SCHOLAR_GODEL_XXX=0`，然后对比同一篇论文开/关时的输出差异。重点观察：Finding 数量变化、Phase 转换时机、Token 消耗。

**Q5：用户的工作风格偏好是什么？**
A：
- 不喜欢 theater code，重视实际效果
- 端到端验证 > 单元测试
- 不要用脚本做 eval matching，直接读结果判断
- 认真思考设计决策，避免自动化倾向
- 经济学背景，关注方法论严谨性

---

## 十三、接续工作的起手步骤

1. 读本文档，理解项目全貌
2. `cd /Users/yanfeiyu03/Downloads/scholar-agent-public`
3. 确认环境：`python -c "import v2; print('ok')"`
4. 查看路线图当前位置：读 `v2/EXECUTION_PLAN.md`
5. 根据优先级选择下一个任务（见 §6.4）
6. 如果做 A.1 深度使用循环：用 §7.3 中的场景做端到端测试，每次记录日志
7. 出错时参考 §10 调试指南定位问题

---

*End of Handover Prompt*
