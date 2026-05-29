# ScholarAgent V2 升级执行计划

> **状态**: 待确认（用户审阅后方可执行）  
> **编写时间**: 2025-07  
> **项目路径**: `/Users/yanfeiyu03/Downloads/scholar-agent-public/`  
> **核心原则**: 约束而非控制（Constrain, don't control）  
> **优先级逻辑**: 先把仓库结构清理干净 → 再做认知系统增强 → 最后做调研性工作

---

## 执行原则

1. **每个任务标明"目的"**：执行者若发现方案细节有问题，可根据目的理解意图自行调整。
2. **Phase A 必须先做完**：结构清理是基础设施，后续所有任务的文件路径引用、import、测试都依赖 A 的结果。
3. **Phase B 各任务相对独立**：可并行或任意顺序（除非标注前置依赖）。
4. **每个任务完成后更新本文件状态标记**：`☐` → `☑`。

---

## Phase A：仓库结构清理（立即执行）

### A1. 删除根目录 v1 残留代码

**预计工时**: ~15min  
**优先级**: P0  
**前置**: 无

#### 现状

根目录下仍保留 V1 时期的 `core/`、`tests/`（59 个旧文件）、`main.py`、`run_hdwm_e2e_quick.py`、`llm/`、`config/`、`tools/`。而 `v2/conftest.py` 已主动从 `sys.path` 中移除 repo root 来防止 shadow import——证明 v2/ 完全自包含。`MIGRATION_NOTE.md` 明确列出了可删除项。

#### 目的

消除以下实际问题：
- 新开发者/执行 Agent 被误导去修改错误文件
- CLAUDE.md 无法简洁指向正确代码路径
- IDE 全局搜索出双份结果、git diff/blame 被污染

#### 实施步骤

1. 删除以下根目录项：
   - `core/`（含 `core/v2/` 子目录）
   - `tests/`（根目录下的 59 个旧测试/日志文件）
   - `main.py`
   - `run_hdwm_e2e_quick.py`
   - `llm/`
   - `config/`
   - `tools/`
   - `fake.md`, `fake.pdf`, `fake_paper.md`（v2/ 内已有自己的副本）

2. 保留：`v1/`, `v2/`, `legacy/`, `poc/`, `docs/`, `examples/`, `guidelines/`, `skills/`（A2 再决定）

#### 测试要求

- `cd v2 && python -m pytest tests/ -q --tb=short` → 全部通过（0 failure）
- `git status` 确认只有删除操作，无意外修改

#### 风险

极低。MIGRATION_NOTE.md 已显式授权删除；v2/conftest.py 证明无 import 依赖。

#### 状态：☑

> **实现记录 (2025-07)**: 删除 `core/`, `tests/`, `tools/`, `llm/`, `config/`, `main.py`, `run_hdwm_e2e_quick.py`, `fake.md`, `fake.pdf`, `fake_paper.md`。v2 测试 1342 passed 无回归。

---

### A2. 处理根目录 skills/ 和 guidelines/ 和 examples/

**预计工时**: ~10min  
**优先级**: P0  
**前置**: A1

#### 现状

根目录有独立的 `skills/`、`guidelines/`、`examples/` 目录。v2/ 下也各有一套。MIGRATION_NOTE.md 说这些"可按需保留或移入对应版本"。

#### 目的

根目录的角色应该只是"项目壳"（README、LICENSE、pyproject.toml、docs/ 等元信息）。非项目壳的内容应该归属到对应版本目录内，否则维护者不知道该更新哪个。

#### 实施步骤

1. 对比 `skills/` vs `v2/skills/`：
   - 若完全重复 → 删除根目录副本
   - 若有 v1 专用内容 → 确认 `v1/skills/` 是否已有副本，若无则移入
2. 对比 `guidelines/` vs `v2/guidelines/`：同上逻辑
3. 对比 `examples/` vs `v2/examples/`：同上逻辑
4. 若根目录这些目录被清空 → 删除空目录

#### 测试要求

- `diff -r skills/ v2/skills/` 检查重复程度
- 清理后 `cd v2 && python -m pytest tests/ -q` 通过

#### 状态：☑

> **实现记录 (2025-07)**: `guidelines/` 和 `examples/` 与 v2/ 完全相同，直接删除。`skills/` 中 `deai_rules_en.md` 为 v2 独缺，移入 `v2/skills/` 后删除根目录副本。

---

### A3. 重写 CLAUDE.md

**预计工时**: ~20min  
**优先级**: P0（**Phase A 中最重要的文档工作**）  
**前置**: A1, A2

#### 现状

当前 CLAUDE.md 第 22-106 行指向根目录旧代码路径：`main.py`、`core/agent.py`、`core/loop.py`、`core/harness.py`、`tests/`……A1 删除后这些全部失效。同时它列出了"旧版工作流架构"、"概念验证"、"实验存档"等章节，总行数 164 行（接近 200 行硬限制），内容利用率低。

#### 目的

CLAUDE.md 是每次 Agent 开工的第一个参考文件（执行规范第 3 步："读本文件确认项目结构"）。如果路径全错，Agent 无法工作。重写后它应该：
1. 准确反映 v2/ 为唯一活跃代码
2. 列出 v2/core/ 关键模块及其职责
3. 保留设计红线和认知约束（这些是跨版本的）
4. 保留 200 行硬限制 + 分层规则（L0/L1/L2）

#### 实施步骤

新 CLAUDE.md 结构：

```markdown
# CLAUDE.md — ScholarAgent V2 认知架构

## 容量约束
（保留 200 行限制 + L0/L1/L2 分层机制）

## 项目定位
学术审稿的认知 Agent。Agent = 认知（how to think），不是编排（how to orchestrate）。

## 唯一工作目录
/Users/yanfeiyu03/Downloads/scholar-agent-public/

## 仓库结构
v2/          ← V2 主代码（唯一活跃版本，完全自包含）
v1/          ← V1 独立副本（prompt 堆叠模式，存档不修改）
legacy/      ← 旧 workflow 架构（只参考）
poc/         ← 概念验证原型（只参考）
docs/        ← 项目文档

## V2 核心模块（可修改）
| 路径 | 职责 |
|------|------|
| v2/main.py | REPL 入口 |
| v2/core/agent.py | Agent 组装 |
| v2/core/loop.py | 认知循环引擎：think-act cycle |
| v2/core/harness.py | 工具执行 + 状态守护 + quality gate |
| v2/core/boundary_guard.py | 边界守卫 + Completion Quality Gate |
| v2/core/compaction.py | Smart Compaction Engine |
| v2/core/paper_cognition_graph.py | PCG 图认知模型 |
| v2/core/habits.py | 认知习惯库 (19 条，动态选取) |
| v2/core/token_budget.py | Token Budget 3-zone |
| v2/core/hypothesis.py | HD-WM 假说模块 |
| v2/core/identity.py | 认知身份 + system prompt |
| v2/core/skill_registry.py | Skill 注册与查询 |
| v2/core/skill_handler_loader.py | 操作型 Skill Handler 动态加载 |
| v2/skills/ | 知识型+操作型 Skill 文件 |
| v2/tests/ | 测试套件 |

## 架构关系
main.py → agent.py (组装) → loop.py (驱动) + harness.py (执行)
  harness.py 内: boundary_guard, compaction, token_budget, skills

## 设计红线
（保留 COGNITIVE_ANCHOR 引用 §2.1/§3.1/§4.3/§5.1）

## 认知约束 [L1]
（保留全部 8 条实践规则）

## 执行规范
1. cd 到项目目录
2. git status + git log --oneline -3
3. 读本文件
4. 读 docs/PROGRESS.md 尾部
5. 读目标文件
6. 写计划 → 执行 → 验证 → 写小结
```

#### 测试要求

- 新文件中每个路径 (`v2/core/agent.py` 等) 用 `ls` 确认实际存在
- 行数 ≤ 200
- 保留的设计红线条目与 `docs/COGNITIVE_ANCHOR.md` 内容一致

#### 状态：☑

> **实现记录 (2025-07)**: 重写为 123 行，22 个路径全部 `ls` 验证存在。保留 L0/L1/L2 分层、设计红线 7 条、认知约束 8 条（含 `[Phase X]` 前缀格式以兼容 rule_extractor 匹配逻辑）。

---

### A4. 更新 .gitignore

**预计工时**: ~5min  
**优先级**: P1  
**前置**: 无（与 A1-A3 独立并行）

#### 现状

当前 `.gitignore` 缺少 `.cache/` 和 `.pytest_cache/`。pytest 运行后会产生 `.pytest_cache/`；某些 LLM client 缓存使用 `.cache/`。

#### 目的

防止运行时产物被误提交到 git。

#### 实施步骤

1. 添加以下条目：
   ```
   # Cache
   .cache/
   .pytest_cache/
   ```

#### 测试要求

- `git status` 确认无 untracked cache 目录
- 已有的 `.pytest_cache/` 被正确忽略

#### 状态：☑

> **实现记录 (2025-07)**: 添加 `.cache/` 和 `.pytest_cache/` 到 .gitignore。

---

### A5. 更新 MIGRATION_NOTE.md

**预计工时**: ~5min  
**优先级**: P2  
**前置**: A1

#### 现状

MIGRATION_NOTE.md 仍写着"后续清理：当确认不再需要旧结构时，可删除..."——但我们已经删了。

#### 目的

将其从"待办指南"转变为"历史记录文档"，让未来开发者知道仓库经历过什么结构变化，而非误以为还有东西要删。

#### 实施步骤

1. 将"后续清理"部分改为"清理记录（2025-07 完成）"
2. 记录实际删除了什么、保留了什么、保留原因

#### 状态：☑

> **实现记录 (2025-07)**: 重写为"已完成"状态，详细记录 A1/A2 删除项、保留项及保留原因。

---

### A6. 归档 docs/ 中过时的计划文档

**预计工时**: ~10min  
**优先级**: P2  
**前置**: 无

#### 现状

`docs/` 有 29 个文件，其中 ~15 个是不同阶段的升级计划：GODEL_AGENT_PLAN（3 个版本）、ARCHITECTURE_UPGRADE_PLAN、V3_REFINEMENT_PLAN、V4_SKILL_LOADING_PLAN、IMPROVEMENT_PLAN、UPGRADE_PLAN_DRAFT/FINAL 等。这些已被执行或废弃。

#### 目的

docs/ 顶层应只保留当前有效文档。过多废弃计划会干扰 Agent 选择参考文档（Agent 读 docs/ 目录时被 15 个旧计划分散注意力）。归档到已存在的 `docs/archive/` 子目录。

#### 实施步骤

1. 保留在 docs/ 顶层的文件（当前有效）：
   - `COGNITIVE_ANCHOR.md` — 第一性原理锚点
   - `COGNITIVE_SPEC.md` — 认知规格
   - `FRIDAY_API_REFERENCE.md` — API 参考
   - `PROGRESS.md` — 进度记录
   - `TODO.md` — 待办
   - `V2_UPGRADE_EXECUTION_PLAN.md` — 本文件（当前活跃计划）
   - `HANDOVER.md` — 交接文档

2. 移入 `docs/archive/` 的文件：
   - GODEL_AGENT_PLAN.md, GODEL_AGENT_PLAN_V2.md, GODEL_AGENT_PLAN_V3.md
   - ARCHITECTURE_UPGRADE_PLAN.md, ARCHITECTURE_V2_BLUEPRINT.md
   - V3_REFINEMENT_PLAN.md, V4_SKILL_LOADING_PLAN.md
   - IMPROVEMENT_PLAN.md, UPGRADE_PLAN_DRAFT.md, UPGRADE_PLAN_FINAL.md
   - AGENT_TRANSFORMATION_PLAN.md, PLAN_D_EXPLORATION.md
   - AIME_ANALYSIS.md, REFERENCE_ANALYSIS.md, REFERENCES.md
   - HANDOVER_PROMPT.md, HANDOVER_PROMPT_V3.md, HANDOVER_PROMPT_V4.md
   - SESSION_PROMPT.md, SYSTEM_PROMPT.md, SCHOLAR_AGENT_V3_PROMPT.md
   - INTERVIEW_PREP.md, NEXT_STEPS.md

3. 确认 `ls docs/` 顶层文件数 ≤ 10

#### 测试要求

- `ls docs/ | wc -l` ≤ 10（不含 archive/ 目录本身）
- 归档文件在 `docs/archive/` 中仍可访问

#### 状态：☑

> **实现记录 (2025-07)**: 22 个旧计划/交接文档移入 `docs/archive/`。顶层保留 8 个有效文件（COGNITIVE_ANCHOR, COGNITIVE_SPEC, FRIDAY_API_REFERENCE, HANDOVER, PROGRESS, REFERENCES, TODO, V2_UPGRADE_EXECUTION_PLAN）。

---

## Phase B：认知系统增强

### B1. Habits 系统：学科特异触发器

**预计工时**: ~1.5h  
**优先级**: P1  
**前置**: Phase A 完成

#### 现状

`v2/core/habits.py` 包含 19 条认知习惯。`HabitSelector` 选择逻辑：`phase filter → trigger boost (+20 priority) → priority sort → truncate to 5`。每条习惯有 `triggers: list[str]` 字段（情境触发词），但当前只有通用触发词（如 "method"、"sample"），无学科特异性。

#### 目的

不同学科的审稿关注点完全不同：
- 经济学关注 identification strategy、endogeneity、DID parallel trends
- CS/ML 关注 reproducibility、ablation、baseline fairness
- 生物学关注 sample size、p-hacking、pre-registration

当前 19 条习惯是通用的，添加学科触发器后，Agent 面对经济学论文时自动选中 "质疑因果声称" 而非 "检查代码复现性"。这让每轮注入的 5 条习惯更 relevant，提升 system prompt 信息密度。

#### 实施步骤

1. 在 `CognitiveHabit` dataclass 新增字段：
   ```python
   discipline_triggers: dict[str, list[str]] = field(default_factory=dict)
   # key = paper_type (e.g. "empirical_econ", "ml_experiment")
   # value = 该学科特有的触发关键词
   ```

2. 修改 `HabitSelector._compute_trigger_boost()`:
   - 现有逻辑：匹配 `triggers` 中任一词 → +20 priority
   - 新增逻辑：若 `current_paper_type` 在 `discipline_triggers` 中且匹配 → +25 priority（学科特异 > 通用触发）
   - `current_paper_type` 从 `CognitiveHints.paper_type_description` 或 `PaperStructureIndex` 获取

3. 为以下习惯填充学科触发词（示例）：
   | 习惯 ID | 学科 | 触发词 |
   |---------|------|--------|
   | `skepticism_first` | empirical_econ | identification, endogeneity, causal |
   | `skepticism_first` | ml_experiment | claimed SOTA, benchmark |
   | `verify_claims` | empirical_econ | parallel trends, exclusion restriction |
   | `verify_claims` | clinical | sample size, randomization |

4. `paper_type` 未知时回退到通用 triggers（不降级现有行为）

#### 测试要求

- 现有 habits 测试全部通过（无回归）
- 新测试：paper_type="empirical_econ" + context 含 "identification" → `skepticism_first` 获得 +25 boost
- 新测试：paper_type=None → 只有通用 trigger 生效，行为等同当前版本
- 新测试：discipline_triggers 为空的习惯 → 只用通用 triggers

#### 状态：☑

> **实现记录 (2025-07)**: `CognitiveHabit` dataclass 新增 `discipline_triggers: dict[str, list[str]]` 字段。`HabitSelector.select()` 新增 `paper_type` 参数，学科触发匹配 +25 优先级（高于通用 +20）。为 6 个核心习惯（skepticism_first, data_sensitivity, methodology_scrutiny, assumption_boundary, evidence_grounded）填充了 empirical_econ / ml_experiment / clinical / theoretical 学科触发词。新增 6 个测试覆盖所有场景。1348 tests all pass。

---

### B2. PCG：领域模板方法 `_apply_domain_template`

**预计工时**: ~2h  
**优先级**: P1  
**前置**: 无（与 B1 独立）

#### 现状

`v2/core/paper_cognition_graph.py` 的 `from_structure_index()` 从 `PaperStructureIndex` 继承骨架（sections + dependency_pairs + evidence_map），建立 PCGNode 和 PCGEdge，但完全不考虑论文类型。实证论文和理论论文得到相同的初始 edge weight 分布。

#### 目的

不同类型论文有不同的结构范式和审稿重点依赖：
- 实证论文：Identification Strategy → Results 是高权重边（如果 ID strategy 有问题，Results 不可信）
- 理论论文：Assumptions → Propositions → Proofs 是链式高权重
- 综述论文：Coverage → Gap Analysis 是关键

PCG 如果在初始化时"知道"论文类型，就能预设关键边的权重，帮助 Agent 在 ORIENTATION 阶段更快形成审稿假说。这是 C5 精神下的"认知增强"——不控制 Agent 做什么，但让它的图结构感知更准确。

#### 实施步骤

1. 在 `paper_cognition_graph.py` 中定义领域模板数据结构：
   ```python
   @dataclass
   class DomainTemplate:
       paper_type: str
       expected_sections: list[str]         # fuzzy 匹配目标
       critical_edges: list[tuple[str, str, float]]  # (source, target, weight_boost)
       focus_hints: list[str]               # Agent 应重点关注的 section 组合
   ```

2. 定义至少 4 个模板：
   - `empirical_economics`: 关注 Identification → Results, Data → Robustness
   - `ml_experiment`: 关注 Method → Experiments, Baseline → Ablation
   - `theoretical`: 关注 Assumptions → Propositions → Proofs
   - `survey`: 关注 Scope → Coverage → Gaps

3. 实现 `_apply_domain_template(self, paper_type: str)`:
   - 从已有 section names fuzzy match 到模板的 expected_sections
   - 匹配成功的 edge → boost weight（乘 1.5x 或加固定值）
   - 匹配失败 → 静默退出（`logger.debug("No domain template matched")`），不影响原有 PCG

4. 在 `from_structure_index()` 末尾调用 `_apply_domain_template()`

#### 测试要求

- 现有 PCG 测试全部通过（无回归）
- 新测试：paper_type="empirical_econ" + sections 含 "Identification Strategy" → 相关 edge weight 被 boost
- 新测试：paper_type="unknown_type" → PCG 与未应用模板时完全相同
- 新测试：sections 不匹配模板 expected → 模板不生效，无 crash

#### 状态：☑

> **实现记录 (2025-07)**: 新增 `DomainTemplate` dataclass 和 4 个模板（empirical_econ, ml_experiment, theoretical, survey）。`_apply_domain_template()` 在 `from_structure_index()` 末尾自动调用，通过 fuzzy match section 名称对关键边进行 weight boost（+0.3~0.4，cap 在 1.0）。未匹配模板时静默退出，不影响原有 PCG。`_fuzzy_match_section()` 支持精确/包含/前缀三级匹配。新增 6 个测试。1354 tests all pass。

---

### B3. Completion Quality Gate：改善 nudge 措辞

**预计工时**: ~30min  
**优先级**: P0  
**前置**: 无（独立任务）

#### 现状

`v2/core/boundary_guard.py` 第 359-369 行的 `min_findings` nudge：
```python
f"你目前有 {len(state.findings)} 条发现，"
f"而你之前判断此类论文通常至少应有 {min_f} 条发现。"
f"这只是你自己设定的参考标准——如果你认为已经充分审阅，"
f"再次调用 mark_complete 即可。"
```

**问题核心**：虽然技术上允许 override（再次 mark_complete 放行），但措辞暗示"你还不够"——对 LLM 的 next-token prediction 来说，nudge 的出现本身就在施压。Agent 倾向于"补"几个低质量 findings 来满足门槛，而非做独立判断。

#### 目的

严格遵循 C5"约束而非控制"。Gate 的职责是让 Agent 意识到两种可能性：(1) 论文确实好 (2) 存在遗漏——然后由 Agent 自行判断。措辞不应偏向任何一侧。

#### 实施步骤

1. 将第 363-369 行替换为：
   ```python
   return (
       f"当前 {len(state.findings)} 条发现，"
       f"低于你此前对此类论文的预期（{min_f} 条）。"
       f"两种可能：(1) 这篇论文质量确实较好，问题较少；"
       f"(2) 存在你尚未覆盖的审稿维度。\n"
       f"建议回顾覆盖率信号后做出判断——再次 mark_complete 即完成。"
   ), completion_nudges_fired
   ```

2. 确认其他 nudge（unverified、hdwm_active、quality_check、deai_unchecked）措辞是否有类似偏向问题——如有，一并调整

#### 测试要求

- 现有 boundary_guard 测试全部通过
- nudge 输出中包含"两种可能"且无单方向暗示
- 功能不变：min_findings == 0 时此 nudge 永不触发；第二次 mark_complete 仍可放行

#### 状态：☑

> **实现记录 (2025-07)**: `boundary_guard.py` 第 359-369 行 nudge 替换为双假说措辞，消除单向施压。检查其余 nudge（unverified/hdwm_active/quality_check/deai_unchecked）措辞已中性，无需修改。`test_v2_gate_config.py::test_min_findings_nudge` 断言同步更新。1342 tests all pass。

---

### B4. Smart Compaction：pre_compact_hook + capacity % 信号

**预计工时**: ~2h  
**优先级**: P1  
**前置**: 无（独立任务）

#### 现状

`v2/core/compaction.py` 的 `CompactionConfig` 中 `trigger_token_ratio=0.5` 触发压缩。压缩时直接构建 `WorkspaceSnapshot` 并裁剪对话历史，但：
1. 没有 hook 机制：外部模块无法在压缩前做 flush（如 SessionMemory pending notes）
2. Agent 不知道实时 capacity：只在触发压缩时才被告知，缺乏主动感知

`v2/core/token_budget.py` 有 `get_budget_status()` 返回三区状态（zone_a/zone_b_used/zone_c_available/total），但这是 zone 粒度的，不直接给 Agent 一个 "已用 N%" 的精确信号。

#### 目的

来自 Hermes Agent 的两个可借鉴设计：
1. **pre_compact_hook**: 压缩是有信息丢失风险的操作。如果 SessionMemory 有一个 pending note 还没 flush 到 state，压缩会丢掉那段对话→ note 永远丢失。hook 机制让各模块能在压缩前"保存现场"。
2. **capacity %**: 让 Agent 在 system prompt status 中看到 "context 63%" → 可主动做 session note 或减少 verbose output，而非被动等 Red zone 才反应。这是"约束而非控制"的典型：展示信息，不强制行为。

#### 实施步骤

1. 定义 hook 类型和注册机制：
   ```python
   from typing import Callable, Protocol
   
   class PreCompactHook(Protocol):
       def __call__(self, snapshot: WorkspaceSnapshot) -> None: ...
   
   class CompactionEngine:
       def __init__(self, config: CompactionConfig):
           self._pre_compact_hooks: list[PreCompactHook] = []
       
       def register_pre_compact_hook(self, hook: PreCompactHook) -> None:
           self._pre_compact_hooks.append(hook)
       
       def _fire_hooks(self, snapshot: WorkspaceSnapshot) -> None:
           for hook in self._pre_compact_hooks:
               try:
                   hook(snapshot)
               except Exception as e:
                   logger.warning("Pre-compact hook failed: %s", e)
   ```

2. 在 `_perform_compaction()` 流程中，在构建 snapshot 后、裁剪历史前调用 `_fire_hooks()`

3. 添加 `get_capacity_pct()` 方法：
   ```python
   def get_capacity_pct(self, current_context_tokens: int) -> float:
       """返回 context window 已用百分比 (0.0~1.0)。"""
       return current_context_tokens / self.config.total_context_window
   ```

4. 在 Harness 每轮 status block 中注入 capacity 信息（格式：`[Context: 63%]`）
   - 与 TokenBudgetManager 的 `get_budget_status()` 互补：budget 是 zone 分配视角，capacity 是整体 filling 视角

#### 测试要求

- hook 在压缩触发时被调用（mock hook + 验证调用次数）
- hook 异常不阻断压缩流程（try-except + warning log）
- `get_capacity_pct()` 计算正确（edge: 0%、100%、超限）
- 现有 compaction 测试通过（无回归）

#### 状态：☑

> **实现记录 (2025-07)**: `CompactionConfig` 新增 `total_context_window` 字段（默认 128K）。`CompactionEngine` 新增 `register_pre_compact_hook()` / `_fire_pre_compact_hooks()` 机制，在 `compact()` 裁剪历史前触发所有注册 hooks；hook 异常不阻断压缩（try-except + warning log）。新增 `get_capacity_pct(current_context_tokens)` 返回 0.0~1.0 的 capacity 百分比。新增 `PreCompactHook` Protocol 类型。新增 4 个测试。1358 tests all pass。

---

### B5. Skill 系统：Lifecycle 字段 + 通用化 Action Skill 加载

**预计工时**: ~2h  
**优先级**: P1  
**前置**: 无

#### 现状

`v2/skills/registry.json` 已有 8 knowledge + 1 action + 1 external 条目。`v2/core/skill_handler_loader.py` (170 行) 实现了安全的 importlib 动态加载，但 registry 没有 lifecycle 管理字段（version, status, installed_at）。当前只有 `structured_export` 一个 action skill——loader 能工作但只有一个实例证明。

#### 目的

这是 Skill 安装流程（B6）的**基础设施前置**。用户设计意图："Skill 安装到 ScholarAgent 本身，用户下载后注册"。要实现这个故事，registry 必须能：
1. 标记 Skill 状态（active/inactive/deprecated）→ 用户禁用某 Skill 不需删文件
2. 追踪版本号 → 更新后知道版本差异
3. 记录安装时间 → 排查问题可溯源

这对应 SkillClaw 的 validate/activate/version 语义——不需要完整产品，但 schema 必须支撑这些操作。

#### 实施步骤

1. 扩展 `registry.json` schema，每个条目新增字段：
   ```json
   {
     "id": "overclaim_rules",
     "version": "1.0.0",
     "status": "active",
     "installed_at": "2025-07-01",
     "last_updated": "2025-07-01",
     ... (现有字段不变)
   }
   ```
   - `status`: `"active" | "inactive" | "deprecated" | "draft"`
   - `version`: semver 字符串
   - `installed_at` / `last_updated`: ISO date

2. 修改 `v2/core/skill_handler_loader.py`：
   - `load_all_active()` 方法：扫描 registry 中所有 `type: "action"` + `status: "active"` 条目
   - 动态 import 每个 handler，返回 `dict[tool_name, handler_func]`
   - 加载失败的单个 Skill 不阻塞其他 Skill 加载（warn + skip）

3. 添加内部管理 API（`v2/core/skill_registry.py` 扩展）：
   ```python
   def activate_skill(self, skill_id: str) -> bool: ...
   def deactivate_skill(self, skill_id: str) -> bool: ...
   def list_skills(self, status_filter: str | None = None) -> list[SkillMeta]: ...
   ```

#### 测试要求

- registry.json 中添加 lifecycle 字段后可被正确 parse（向后兼容：缺少字段时用默认值）
- `status: "inactive"` 的 Skill 不被 loader 加载
- `activate_skill()` / `deactivate_skill()` 正确修改 registry 并持久化
- 添加第二个 mock Action Skill → 验证 loader 能自动发现和注册多个 handler

#### 状态：☑

> **实现记录 (2025-07)**: registry.json 所有 10 个条目添加 `version`/`status`/`installed_at`/`last_updated` 字段（registry version 升至 "1.1"）。`SkillHandlerLoader` 新增 `load_all_active()` 方法：扫描 registry 中 `type: "action"` + `status: "active"` 条目，批量加载 handler 返回 `dict[tool_name, handler_fn]`；单个加载失败不阻塞其他。新建 `v2/core/skill_lifecycle.py` 含 `SkillRegistryManager` 类：提供 `activate_skill()`/`deactivate_skill()`/`list_skills()`/`get_skill()` 管理 API，操作幂等并持久化到 registry.json。新增 17 个测试覆盖 lifecycle parsing、inactive filtering、activate/deactivate 持久化、多 handler 发现。1374 tests pass（仅 1 个 pre-existing case-sensitivity mismatch）。
>
> **审计修复 (2025-07)**: (1) `SkillRegistry.query()` 的 `paper_type`/`phase` 过滤改为 case-insensitive（修复 `DEEP_REVIEW` vs `deep_review` 不匹配问题）；(2) `skill_registry.py` 消除重复 `import re`（方法内 `import re as _re` → 使用模块顶部统一导入）；(3) 在 `godel_config.py` 新增 `TOTAL_CONTEXT_WINDOW = 128_000` 共享常量，`token_budget.py` 和 `compaction.py` 改为引用此常量（消除 128K 魔法数字重复定义）。修复后 1375 tests all pass。

---

### B6. Skill 安装流程原型

**预计工时**：~2h  
**优先级**：P1  
**前置**：B5

#### 目的

用户明确要求："Skill 应该安装到 ScholarAgent 本身，用户下载后注册，复杂 Skill 通过 Claude Code 适配"。这个任务将该设计意图变成最小可工作实现——从 "新文件" 到 "Agent 可用" 的完整路径。不需要做成完整产品（无 UI、无版本仓库），但要有端到端链路。

#### 现状

当前添加新 Skill 的方式：手动在 `v2/skills/` 放文件 + 手动编辑 `registry.json`。没有验证、没有错误检查、没有标准包格式。

#### 实施步骤

1. 定义 Skill 包约定（`v2/skills/SKILL_PACKAGE_SPEC.md`）：
   ```
   my-skill/
   ├── skill.json          # 元数据（必须）
   ├── content.md          # 知识内容或行为指令（必须）
   └── handler.py          # Action Skill 的 handler（可选）
   ```

2. `skill.json` schema：
   ```json
   {
     "id": "my_custom_skill",
     "type": "knowledge",
     "name": "My Custom Skill",
     "description": "...",
     "version": "1.0.0",
     "tags": [...],
     "applicable_paper_types": [...],
     "applicable_phases": [...],
     "token_estimate": 1500,
     "priority_hint": 65
   }
   ```

3. 创建 `v2/skills/installer.py`：
   ```python
   class SkillInstaller:
       def install(self, skill_dir: Path) -> InstallResult:
           """验证 → 复制 → 注册 → 返回结果。"""
       def uninstall(self, skill_id: str) -> bool:
           """反注册 → 删除文件 → 返回结果。"""
       def validate(self, skill_dir: Path) -> list[str]:
           """仅验证不安装，返回 error list（空=通过）。"""
   ```

4. 安装流程：
   - 验证 `skill.json` schema 合法性
   - 检查 id 冲突（registry 中已有同 id）
   - 复制 `content.md` 到 `v2/skills/`
   - 如果有 `handler.py`，复制到 `v2/skills/skill_handlers/`
   - 更新 `registry.json`（添加条目，status: active, installed_at: now）

#### 测试要求

- 合法 Skill 包 → 安装成功 + registry 更新 + 文件就位
- id 冲突 → 安装失败 + 明确错误信息
- schema 不合法 → validate() 返回具体错误列表
- uninstall → registry 移除条目 + 文件删除
- 安装后 Agent 下次启动能加载该 Skill（集成验证）

#### 状态：☑

> **实现记录 (2025-07)**: 创建 `v2/skills/SKILL_PACKAGE_SPEC.md` 定义标准包格式（skill.json + content.md + 可选 handler.py）。实现 `v2/skills/installer.py` 含 `SkillInstaller` 类：`validate()` 做 10+ 项 schema 验证（id 格式、type 值、必填字段、token_estimate 正整数、priority_hint 范围、Action Skill handler/tools 检查）；`install()` 实现原子安装（验证→冲突检查→文件复制→registry 更新，失败时回滚）；`uninstall()` 实现反注册+文件删除。新增 29 个测试覆盖全部 5 个验收场景 + 边界情况。集成验证确认安装后 `SkillRegistry.query()`/`load_content()`/`load_tools_from_markdown()` 均能正确加载。1404 tests all pass。

---

### B7. Smart Compaction: Frozen Snapshot 前缀缓存

**预计工时**：~3h  
**优先级**：P2  
**前置**：B4

#### 目的

Hermes Agent 的 "frozen snapshot" 解决两个问题：(1) 一致性——多次压缩的恢复信息不应互相矛盾；(2) 性能——frozen 部分可利用 API prefix caching 减少 token 计费。对 ScholarAgent 来说，一篇论文审稿中可能触发 2-3 次压缩，恢复信息的认知连贯性直接影响审稿质量。

#### 现状

`v2/core/compaction.py` 的 `WorkspaceSnapshot` 每次压缩都完全重新构建 restoration text。如果 Agent 被压缩 3 次，每次的 "你之前做了什么" 描述是独立计算的，可能不一致（如第一次说读了 3 个 section，第二次计算发现数据变了说读了 5 个，前后叙述不连贯）。

#### 实施步骤

1. `WorkspaceSnapshot` 新增字段：
   ```python
   frozen_prefix: str = ""   # 来自上次压缩的恢复文本，本次不再覆盖
   compaction_seq: int = 0   # 第几次压缩（用于日志）
   ```

2. `CompactionEngine`（或 `format_restoration`）逻辑调整：
   - 如果 `frozen_prefix` 非空 → 本次只生成 delta_restoration（上次压缩以来的增量）
   - 最终注入 = `frozen_prefix` + separator + `delta_restoration`
   - 将本次完整输出存为下次的 `frozen_prefix`

3. API 层面（可选优化，视 LLM client 支持情况）：
   - 将 `frozen_prefix` 放在 system message 的前半部分（API cacheable 区域）
   - 需确认 Friday API 是否支持 prefix caching

#### 测试要求

- 模拟 3 次连续压缩：frozen_prefix 保留前次内容 + delta 正确追加
- 第 1 次压缩：frozen_prefix 为空，正常生成完整 restoration
- 第 2 次压缩：frozen_prefix = 第 1 次输出，delta 只含新增信息
- 最终注入文本格式正确、无重复信息

#### 状态：☑

> **实现记录 (2025-07)**: `WorkspaceSnapshot` 新增 `frozen_prefix: str` + `compaction_seq: int` 字段。`format_restoration()` 重构为两层：`_build_restoration_text()` 生成当次 delta，`format_restoration()` 叠加 frozen_prefix + separator + delta 逻辑。每次压缩后完整输出存入 `frozen_prefix` 供下次使用，`compaction_seq` 自增。测试 17 passed（首次压缩、增量压缩、连续 3 次一致性、向后兼容、边界情况）。

---

### B8. Token Budget 3-zone 添加 capacity % 信号

**预计工时**：~1h  
**优先级**：P1  
**前置**：与 B4 共享设计（确保 capacity 计算逻辑唯一）

#### 目的

Token Budget 3-zone 是 Agent 的 "油表"，但目前 Agent 只知道 zone 状态（Green/Yellow/Red），不知道精确位置。85% 和 69% 都在 Yellow zone 含义很不同。精确 % 让 Agent 能更细粒度调整行为——在 Yellow-high 时主动做 session note，而非等 Red zone 慌忙压缩。

#### 现状

`v2/core/token_budget.py` 的 `get_budget_status()` 返回 `dict[str, int]`（zone_a, zone_b_used, zone_b_max, zone_c_available, total），但没有计算整体 capacity %，也没有将此信息注入 Agent 可见的信号中。

#### 实施步骤

1. `TokenBudgetManager.get_budget_status()` 返回值扩展：
   ```python
   {
     ... (现有字段),
     "used_pct": 0.63,      # 整体使用百分比
     "zone_label": "yellow", # Green(<50%) / Yellow(50-80%) / Red(>80%)
   }
   ```

2. 确保这个 % 与 B4 的 `CompactionEngine.get_capacity_status()` 共享计算逻辑（单一数据源，不重复实现）：
   - 方案 A：token_budget.py 计算，compaction.py 调用它
   - 方案 B：提取为共享 utility

3. Harness 在每轮 status block 中注入 capacity 信息（如已有此机制则确认格式）

#### 测试要求

- `used_pct` 计算正确（zone_a + zone_b_used + 对话历史 token / total）
- `zone_label` 与 % 对应正确
- 与 B4 的 capacity 数值一致（不出现两个来源不同数字）

#### 状态：☑

> **实现记录 (2025-07)**: `get_budget_status()` 新增可选参数 `current_context_tokens: int = 0`，返回值扩展 `used_pct` (float 0~1) + `zone_label` ("green"/"yellow"/"red")。新增 `compute_used_pct()` 作为 capacity 计算单一数据源，与 B4 `CompactionEngine.get_capacity_pct()` 共享 `TOTAL_CONTEXT_WINDOW`。阈值: Green(<50%) / Yellow(50%-80%) / Red(≥80%)。测试 29 passed（精度、阈值边界、格式、B4一致性、向后兼容、边界情况）。

---

### Phase B 审计修复记录 (2025-07)

Phase B 全部完成后进行了代码审计，发现并修复了 3 个问题：

**Fix #1 [HIGH] — frozen_prefix 无上限增长**

- 问题：B7 Frozen Snapshot 的 `frozen_prefix` 在多次压缩后会无限增长，最终挤占 Zone B/C 预算
- 修复：`CompactionConfig` 新增 `max_frozen_prefix_tokens: int = 12000`；`format_restoration()` 接受 `max_frozen_tokens` 参数；新增 `_cap_frozen_prefix()` 静态方法，当旧增量段总 token 超限时从最早段开始截断
- 文件：`v2/core/compaction.py`

**Fix #2 [MEDIUM] — format_restoration() 非幂等风险**

- 问题：`format_restoration()` 每次调用都会 append 到 `incremental_segments`，无重入守卫
- 修复：添加 docstring 明确标注"非幂等，调用方需确保每次压缩只调用一次"；引入 `expected_seq` 变量增强可读性
- 文件：`v2/core/compaction.py`

**Fix #3 [MEDIUM] — capacity 计算双源可能发散**

- 问题：`TokenBudgetManager.compute_used_pct()` 和 `CompactionEngine.get_capacity_pct()` 各自独立计算百分比，逻辑重复且可能随维护发散
- 修复：在 `godel_config.py` 新增 `compute_capacity_pct()` 纯函数作为 Single Source of Truth；两个调用方均委托给此函数；`total <= 0` 时安全返回 0.0（表示"未配置"）
- 文件：`v2/core/godel_config.py`、`v2/core/compaction.py`、`v2/core/token_budget.py`

**验证**：修复后全量测试 1450 passed，无回归。

---

## Phase C: 多模态扩展（Phase B 完成后）

### C1. Streaming 输出调研

**预计工时**：~1h（调研 + 写文档）  
**优先级**：P3  
**前置**：Phase B 完成

#### 目的

用户表示 Streaming 方向 "不太确定，需要调研后再说"。本任务只产出设计文档，不做实施。确定 Streaming 对审稿场景的价值、实现复杂度、是否值得投入。

#### 实施步骤

1. 调研 `v2/llm/` client 的 streaming 支持现状
2. 分析 `v2/core/loop.py` 适配 streaming 的改动量（token 计数、tool_call 解析、中断恢复）
3. 产出 `docs/STREAMING_DESIGN.md`，包含：
   - 场景价值分析（审稿场景是否需要实时输出）
   - 实现方案（至少 2 个备选）
   - 改动量估计
   - 明确结论：做 / 不做 / 条件做

#### 测试要求

- 无代码改动，文档内容覆盖上述 4 点

#### 状态：☑

> **实现记录 (2025-07)**: 产出 `docs/STREAMING_DESIGN.md`，覆盖 4 个必要维度：(1) 场景价值分析——streaming 对审稿质量零提升，价值仅在 UX 层；(2) 两个备选方案——方案 A（AsyncGenerator 改造 loop.py，~190 行）和方案 B（回调模式，~50 行）；(3) 改动量估计含风险评级；(4) 明确结论：**条件做**——仅在接入 Web UI 或用户明确需要实时反馈时实施，推荐先用方案 B 验证。底层 `chat_with_tools_stream` 已就绪，技术债务为零。

---

### C2. Streaming 方案 B 实现（回调注入模式）

**预计工时**：~1h（编码 + 测试验证）  
**优先级**：P3  
**前置**：C1 完成

#### 目的

按照 C1 调研结论"条件做 + 方案 B"，实现 Streaming 回调注入模式。核心要求：无 on_stream 时行为零变更；通过 kill switch 环境变量控制（默认 OFF）。

#### 实施步骤

1. 新增 `v2/core/stream_events.py`：定义 `StreamEvent` dataclass 和 `OnStreamCallback` 类型别名
2. `godel_config.py` 新增 `GODEL_STREAMING_ENABLED` flag（默认 OFF）
3. `loop.py`：在 `cognitive_loop` 签名加 `on_stream` 参数，当 `on_stream + STREAMING_ENABLED` 时走 `chat_with_tools_stream` 路径
4. `agent.py`：4 个 `cognitive_loop` 调用点透传 `on_stream`
5. `main.py`：新增 `--stream` CLI 参数，构建打印回调

#### 测试要求

- `cd v2 && python -m pytest tests/ -q` 全量 1450 测试通过（流式为 OFF 时行为无变更）
- 默认 `GODEL_STREAMING_ENABLED=0`，即使传入 `on_stream` 也不走流式路径

#### 状态：☑

> **实现记录 (2025-07)**:
> - 新增 `v2/core/stream_events.py`（47 行）：StreamEvent(type/text/tool_name/turn/metadata) + OnStreamCallback
> - `godel_config.py` 新增 `GODEL_STREAMING_ENABLED = _env_flag("SCHOLAR_GODEL_STREAMING", default="0")`，加入 `log_config_status()`
> - `loop.py`（+49 行净增）：循环外计算 `_use_streaming`，`if _use_streaming:` 走 `chat_with_tools_stream` 逐 chunk 推送，else 走原有 `chat_with_tools`；tool_start/tool_result/done 事件在对应节点推送
> - `agent.py`：ScholarAgent/UnifiedReviewAgent/CollaborativeReview 三个类的 `__init__` 加 `on_stream` 参数，4 个调用点透传
> - `main.py`：`--stream` CLI flag + `_build_stream_callback()` 构建 stderr 实时打印回调
> - 测试：1450 passed，零回归
>
> **修复记录 (2025-07 审查后)**:
> - [P0] `loop.py`: 所有 `harness.state.turn_count` → `harness.state.loop_turns`（原属性不存在，流式路径开启即 AttributeError）
> - [P1] `godel_config.py`: 删除第 98 行的重复 `GODEL_STREAMING_ENABLED` 定义，保留第 170 行完整注释版
> - [P2] `stream_events.py`: `Callable/Optional` import 从文件底部移到顶部 import 区（PEP 8 规范）
> - 修复后 1450 passed + 流式冒烟测试通过

---

## 执行顺序与依赖图

```
Phase A (顺序执行，互相依赖):
  A1 (删除 v1 残留) → A2 (处理 skills/guidelines 副本) → A3 (重写 CLAUDE.md)
  → A4 (.gitignore) → A5 (更新 MIGRATION_NOTE) → A6 (归档旧计划文档)

Phase B (A 完成后，大部分可并行):
  B1 (Habits 学科触发器)        ← 独立
  B2 (PCG 领域模板)             ← 独立
  B3 (Gate 措辞改善)            ← 独立
  B4 (Compaction hook+capacity) ← 独立
  B5 (Skill lifecycle)          ← 独立
  B6 (Skill 安装器)             ← 依赖 B5
  B7 (Frozen snapshot)          ← 依赖 B4
  B8 (Token % 信号)             ← 与 B4 共享设计

Phase C (B 完成后):
  C1 (Streaming 调研)           ← 独立
  C2 (Streaming 方案 B 实现)     ← 依赖 C1
```

**建议执行批次**：
- **Batch 1**（并行）：B1 + B2 + B3 + B4 + B5
- **Batch 2**（依赖 Batch 1）：B6 + B7 + B8
- **Batch 3**：C1 → C2

---

## 验收标准总表

| # | 检查项 | Phase | 状态 |
|---|--------|-------|------|
| 1 | 根目录 `core/`, `tests/`, `main.py`, `run_hdwm_e2e_quick.py`, `llm/`, `config/`, `tools/` 已删除 | A1 | ☑ |
| 2 | 根目录 `skills/`, `guidelines/`, `examples/` 已妥善处理（删除或移入版本目录） | A2 | ☑ |
| 3 | `CLAUDE.md` 所有路径指向 `v2/` 且逐一验证存在 | A3 | ☑ |
| 4 | `.gitignore` 包含 `.cache/` 和 `.pytest_cache/` | A4 | ☑ |
| 5 | `MIGRATION_NOTE.md` 更新为 "已完成" 状态 | A5 | ☑ |
| 6 | `docs/` 顶层文件数 ≤ 10（其余归档到 `docs/archive/`） | A6 | ☑ |
| 7 | Phase A 完成后 `cd v2 && python -m pytest tests/ -q` 全部通过 | A* | ☑ |
| 8 | Habits 系统支持 `discipline_triggers` 且测试验证学科匹配 | B1 | ☑ |
| 9 | PCG `_apply_domain_template()` 对不同 paper_type 产生不同 edge 权重 | B2 | ☑ |
| 10 | Completion Gate min_findings nudge 呈现两个假说、无偏向性 | B3 | ☑ |
| 11 | Compaction 有 `pre_compact_hook` + `get_capacity_status()` | B4 | ☑ |
| 12 | registry.json 支持 lifecycle 字段 + loader 过滤 inactive Skill | B5 | ☑ |
| 13 | `installer.py` 端到端：install → registry 更新 → Agent 可加载 | B6 | ☑ |
| 14 | 3 次连续压缩 frozen_prefix 不变 + delta 正确追加 | B7 | ☑ |
| 15 | `get_budget_status()` 返回 `used_pct` + `zone_label` | B8 | ☑ |
| 16 | `docs/STREAMING_DESIGN.md` 包含明确结论 | C1 | ☑ |
| 17 | 方案 B 实现：`on_stream` 回调 + kill switch，1450 测试零回归 | C2 | ☑ |

---

## 与现有架构的兼容性保证

| 保证项 | 说明 |
|--------|------|
| v2 测试不回归 | Phase A 不改 v2/ 任何代码；Phase B 每个任务完成后跑 pytest |
| Kill Switch 降级 | B5/B6 新增 lifecycle 字段缺失时用默认值（active），不影响现有 Skill 加载 |
| Token Budget 不冲突 | B8 只扩展 `get_budget_status()` 返回值，不改预算计算逻辑 |
| Compaction 向后兼容 | B4/B7 的 hook 和 frozen 是可选增强，无 hook 注册时原有逻辑不变 |
| Assembler 不受影响 | B1/B2 只改 upstream 数据（habits/PCG），不改 Assembler 注入机制 |
| Boundary Guard 行为不变 | B3 只改措辞文本，nudge 的触发条件和放行逻辑不变 |
| registry.json 向后兼容 | 新增字段全部 optional + 有默认值，旧格式仍可被正确 parse |

---

## 工作量估计

| Phase | 任务数 | 新增文件 | 修改文件 | 估计行数 | 风险 |
|-------|--------|----------|----------|----------|------|
| A | 6 | 0 | CLAUDE.md, .gitignore, MIGRATION_NOTE.md | ~200 (文档重写) | 极低 |
| B1 | 1 | 0 | habits.py | ~60 | 低 |
| B2 | 1 | 0 | paper_cognition_graph.py | ~120 | 中 |
| B3 | 1 | 0 | boundary_guard.py | ~10 | 极低 |
| B4 | 1 | 0 | compaction.py, harness.py | ~80 | 中 |
| B5 | 1 | 0 | registry.json, skill_handler_loader.py, skill_registry.py | ~100 | 低 |
| B6 | 1 | installer.py, SKILL_PACKAGE_SPEC.md | registry.json | ~250 | 中 |
| B7 | 1 | 0 | compaction.py | ~80 | 中 |
| B8 | 1 | 0 | token_budget.py | ~30 | 低 |
| C1 | 1 | STREAMING_DESIGN.md | 0 | ~200 (文档) | 低 |
| C2 | 1 | stream_events.py | loop.py, agent.py, main.py, godel_config.py | ~120 | 低 |
| 测试 | — | test_b*.py (多文件) | — | ~400 | — |

**总计**：~1650 行代码/文档 + 测试

---

## 状态跟踪约定

> **每完成一个任务后，必须更新本文件**：
> - `☐` → `☑`
> - 验收标准总表同步更新
> - 在对应任务下方追加 **实现记录**（参照 V4_SKILL_LOADING_PLAN.md 格式）
> - 如有计划变更，在对应 Phase 下方追加 `> [日期] 变更说明`

---

## 待确认事项

1. **根目录 `examples/` 和 `skills/`**：是否有需要保留给 v1 的独特内容？（A2 执行时检查）
2. **v1/ 目录**：保留作为存档，还是需要维护？（当前计划：保留不动）
3. **Streaming**：用户确认方向后再决定 C1 产出的后续行动
4. **Friday API prefix caching**：B7 的可选优化取决于 API 是否支持，需运行时确认
</parameter>
</invoke>