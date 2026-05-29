# C3: Gödel Agent — 递归自改进实施计划

> **核心命题**: Agent 能否改进"改进自己"的过程本身？如果 P2 是单层进化（Agent 学习经验），C3 就是双层进化（Agent 评估并优化自己的学习机制）。
> **版本**: v1.1（基于全量代码审查修订）
> **日期**: 2025-07
> **前置条件**: P2 SessionReflector 已完成，640 tests passing
> **设计原则**: 有界递归的工程可靠 > 无限递归的理论优雅

---

## 零、代码审查发现（v1.1 修订依据）

> v1.0 的计划基于对架构的理论分析。v1.1 在读完全部 15+ 核心模块的实际代码后修正了若干假设。

### 0.1 项目成熟度定位

| 维度 | 评分 | 说明 |
|------|------|------|
| 架构设计 | 5/5 | Harness/Loop/Identity 三层分离、信号协议、nudge-not-block、三层记忆、进化引擎——设计极精巧 |
| 代码质量 | 4/5 | 模块化好、注释充分。扣分：`Any` 类型过多、identity.py 1339行过大、resolve_section_key 重复 |
| Agent-likeness | 5/5 | 不是 "LLM+tools"，而是真认知架构——自省、跨session学习、假说驱动WM、认知习惯动态选择 |
| 测试覆盖 | 3.5/5 | 640 tests，但 memory.py/reflection.py/boundary_guard.py/metacognition.py 无独立测试 |
| 生产就绪 | 3/5 | 缺日志、重试、监控、配置管理。介于"高质量研究原型"和"早期产品"之间 |

### 0.2 影响 Gödel 计划的关键发现

| # | 发现 | 对计划的影响 |
|---|------|------------|
| 1 | `memory.py` 的 `procedures` 列表**无上限无淘汰** | Gödel 层频繁写入 metrics 会加速膨胀→必须先解决 |
| 2 | `finding_quality.py` 已有完整的 finding 质量评分机制 | Phase 1 不必从零发明 quality_score，复用已有评分 |
| 3 | `memory.py` 作为所有进化逻辑的基座**无独立测试** | Phase 1 前必须补测试，否则 meta 层建在沙子上 |
| 4 | `boundary_guard.py` 纯函数设计极优，但阈值全部硬编码 | AdaptiveGate 可以从这里入手（更自然的切入点） |
| 5 | `record_review_stats()` 已用 `density = findings/turns` | 不需要重新发明效率公式，直接扩展 |
| 6 | HabitLearner 的 `activation_count` 无法验证 LLM 是否真正遵循了习惯 | utility_rate 的计算必须用 quality_score 相关性，不能用 activation 计数 |
| 7 | L1 进化（HabitLearner）还没有真实运行数据验证有效性 | 在 L1 未验证前建 L2（ConvergenceMonitor）是过早优化 |

### 0.3 修订摘要

```
v1.0 → v1.1 关键变更:

+ 新增 Phase 0: 记忆健康 + 测试基座（前置条件）
~ Phase 1 修正: quality_score 复用 FindingQualityGate，不从零发明
~ Phase 3 精简: 只做 MetaReflector，ConvergenceMonitor 降级到 Phase 4
~ Phase 4 扩充: EvolutionAuditor + ConvergenceMonitor 合并
+ 新增"工程债务清单"（影响 Gödel 层可靠性的现有问题）
```

---

## 一、架构设计：三层有界递归（不变）

### 1.1 层级架构

```
┌─────────────────────────────────────────────────────────────────────┐
│  Layer 0: 宪法层 (Constitutional Layer)                              │
│  ────────────────────────────────────────────────────                │
│  绝对不可被自修改的系统不变量:                                         │
│  • MAX_META_DEPTH = 2 (禁止 Level 3)                                │
│  • doom_loop_guard + token_budget                                    │
│  • evidence 累积验证机制的存在性 (可调阈值, 不可删除机制)               │
│  • JSON 输出格式约束 (反思prompt内容可变, 格式不可变)                   │
│  • 工具 schema 定义 (Agent不能删除/重定义工具)                         │
│  • 回滚能力的存在性 (自修改必须可逆)                                   │
└─────────────────────────────────────────────────────────────────────┘
                              ↑ 保护
┌─────────────────────────────────────────────────────────────────────┐
│  Layer 1: 认知配置层 (Cognitive Config Layer)                         │
│  ────────────────────────────────────────────────────                │
│  可被自修改的"软参数", 需累积验证:                                      │
│  • 习惯库 + 选择权重 (LearnedHabit pool)                             │
│  • 反思 Prompt 模板 (_REFLECTION_SYSTEM_PROMPT 可微调)                │
│  • HabitLearner 阈值 (min_evidence, min_effectiveness)               │
│  • GateConfig 参数 (idle_rounds, self_eval_interval)                 │
│  • 策略选择先验 (strategy → outcome 的条件概率)                        │
│                                                                      │
│  修改条件: evidence ≥ 3 (和习惯学习同一标准)                           │
│  回滚条件: 连续 2 次 session 的 quality_score 下降                     │
│  修改步长约束: 每次调整幅度 ≤ 预设上限                                  │
└─────────────────────────────────────────────────────────────────────┘
                              ↑ 评估并修改
┌─────────────────────────────────────────────────────────────────────┐
│  Layer 2: 元认知层 (Meta-Cognitive Layer)                             │
│  ────────────────────────────────────────────────────                │
│  评估 Layer 1 的修改效果, 决定是否/如何调整:                            │
│  • MetaReflector: 反思产出的质量评估 + prompt微调                      │
│  • EvolutionAuditor: 习惯学习效果审计 + 阈值调整                      │
│  • ConvergenceMonitor: 收敛检测 + 模式切换                            │
│  • StrategyPrior: 策略→结果映射的积累与注入                            │
│                                                                      │
│  触发频率: 每 N 个 session (N=5~10, 远低于 L1 的每次)                  │
│  递归深度: 1 (评估L1, 但不评估"评估L1的过程")                          │
│  收敛后: 降频运行, 仅保持监控                                          │
└─────────────────────────────────────────────────────────────────────┘
```

### 1.2 递归深度硬约束

```
Level 0: Agent 执行审稿/编辑 (每次 session)
Level 1: Agent 反思执行 → 产出经验 (每次 session 结束, SessionReflector)
Level 2: 系统评估反思质量 → 调整反思配置 (每 N 个 session, MetaReflector)
Level 3: ❌ 禁止 — 评估"评估反思"= 无限递归

频率递减保证:
  L0: 每个 loop turn (~50次/session)
  L1: 每个 session 结束 (1次/session)
  L2: 每 5-10 个 session (0.1~0.2次/session)
```

### 1.3 与 Gödel 不完备定理的类比

Agent 无法完全评估自己的评估是否正确——这是不可避免的。我们的解决方案不是追求完全的自我认知，而是：

1. **外部锚点**：用客观可度量的指标（findings 数量×质量、编辑通过率、session 效率）作为不依赖 Agent 自我评价的真相来源
2. **有限递归**：只做两层，承认"评估评估"这一层已经足够捕获大部分改进空间
3. **保守策略**：宁可少改不多改，宁可慢改不快改——通过 evidence 累积 + 回滚机制保证安全

---

## 二、Phase 0: 地基加固（前置条件）

> **风险等级**: 低
> **LLM 成本**: 零
> **核心产出**: 让 Gödel 层有可靠的地基，不是建在沙子上

### 2.1 问题本质

代码审查发现三个必须先解决的基础问题，否则后续所有 Phase 都有隐患：

1. **记忆膨胀**：`memory.py` 的 `procedures` 列表无上限。Gödel 层会频繁写入 `session_metrics`、`strategy_outcomes` 等记录——不加淘汰机制会导致 memory JSON 无限增长、加载变慢、相似度匹配质量下降。

2. **测试空白**：`memory.py` 是所有进化逻辑的持久化基座，但没有独立单元测试。如果 `add_or_reinforce_procedure` 在边界情况下有 bug（如 description 过长、effectiveness 越界），整个 Gödel 层的数据基础就不可靠。

3. **CLAUDE.md 过时**：目录表只列了 ~12 个文件，实际有 42+。P2 新增的 `evolution.py`、`reflection.py`、`cognition_graph.py` 等关键模块未纳入。

### 2.2 设计方案

#### A. 记忆淘汰机制

在 `MemoryStore` 中增加 `_gc_procedures()` 方法：

```python
def _gc_procedures(self, max_size: int = 100, min_effectiveness: float = 0.3):
    """
    记忆垃圾回收。
    
    淘汰规则（按优先级）:
    1. effectiveness < min_effectiveness 且 evidence <= 1 → 直接删除（低质量+未验证）
    2. 最后 reinforce 时间 > 60 days → 归档（长期未被强化）
    3. 如果仍然超过 max_size → 按 effectiveness 从低到高裁剪
    
    保护规则:
    - evidence >= 3 的 pattern 永不自动删除（已被验证的知识）
    - category="session_metrics" 保留最近 50 条（滑动窗口）
    """
    ...
```

#### B. memory.py 独立测试

新增 `tests/test_v2_memory.py`，覆盖：

- `add_or_reinforce_procedure`：正常路径、重复 reinforce、边界值
- `add_or_reinforce_pattern`：正常路径、相似度匹配
- `get_relevant_procedures`：按 category 过滤、limit 截断
- 新增的 `_gc_procedures`：淘汰逻辑、保护规则
- 新增的 `add_procedure_record`（Phase 1 需要的纯追加接口）
- JSON 持久化：保存→加载 round-trip

#### C. CLAUDE.md 刷新

更新目录表，补充 P2 模块，对齐实际代码。（在 185 行阈值以内。）

### 2.3 文件影响

| 文件 | 改动 | 工作量 |
|------|------|--------|
| `core/memory.py` | 新增 `_gc_procedures()` + `add_procedure_record()` | ~60 行 |
| `tests/test_v2_memory.py` | **新增**：MemoryStore 独立单元测试 | ~200 行 |
| `CLAUDE.md` | 目录表刷新 + P2 模块纳入 | 改动 ~20 行 |

### 2.4 验证标准

1. `_gc_procedures` 正确淘汰低质量 pattern，保护 evidence≥3 的知识
2. `session_metrics` 类型的记录保持滑动窗口（最近 50 条）
3. memory.py 测试覆盖所有公开 API 的正常和边界路径
4. 全量回归 640+ tests 通过
5. CLAUDE.md 行数 ≤ 185

### 2.5 预计工作量：半天

---

## 三、Phase 1: 基础度量（EvolutionMetrics）

> **风险等级**: 低
> **LLM 成本**: 零（纯数据记录，无额外 LLM call）
> **核心产出**: 为后续所有 meta 层提供客观的 session 质量信号

### 3.1 问题本质

当前 `record_review_stats()` 只记录 4 个维度。这不足以支撑"习惯是否真的有帮助"这个判断。

### 3.2 设计修正（v1.1）

**v1.0 的错误**：试图从零发明 quality_score 公式。
**v1.1 修正**：复用已有的 `finding_quality.py`（FindingQualityGate）作为 findings_quality 信号源。

`finding_quality.py` 已经实现了：
- 每个 finding 的质量评分（specificity, actionability, novelty）
- 按 priority 加权
- 质量门控（低质量 finding 会被标记 WARNING）

所以 quality_score 的 `findings_quality` 分量不需要用 `min(count/5, 1.0)` 这种粗糙公式，而应该直接取 FindingQualityGate 的平均质量分。

### 3.3 修正后的 quality_score 计算

```python
quality_score = 0.4 * findings_quality + 0.3 * efficiency + 0.3 * edit_quality

findings_quality:
    # 直接复用 FindingQualityGate 的评分
    if findings_count == 0:
        findings_quality = 0.0
    else:
        avg_finding_score = mean([finding_quality_gate.score(f) for f in findings])
        volume_factor = min(findings_count / 5, 1.0)  # 数量补偿
        findings_quality = avg_finding_score * (0.6 + 0.4 * volume_factor)

efficiency:
    # 复用 record_review_stats 的 density 逻辑
    density = findings_count / max(total_turns, 1)
    idle_penalty = min(idle_rounds_before_exit / 5, 1.0)
    efficiency = min(density * 2, 1.0) * (1.0 - 0.5 * idle_penalty)

edit_quality:
    if edits_count > 0:
        edit_quality = edits_success_rate  # 来自 post_edit_verify 的 PASS 率
    else:
        edit_quality = 0.5  # 纯审阅模式不惩罚
```

### 3.4 存储设计（修正）

**v1.0 的问题**：试图把 metrics 塞进 ProceduralPattern 的 description 字段。这是 schema 滥用——ProceduralPattern 是为"可复用经验"设计的，不是时间序列数据库。

**v1.1 修正**：使用独立的 JSON 文件存储 metrics 记录。

```python
# 存储结构:
# .memory/session_metrics.json
# [
#   {"session_id": "...", "timestamp": "...", "quality_score": 0.72, ...},
#   {"session_id": "...", "timestamp": "...", "quality_score": 0.65, ...},
# ]
# 最多保留 50 条（滑动窗口，Phase 0 的 GC 机制管理）

class EvolutionMetrics:
    """Session 质量记录与查询。独立存储，不污染 ProceduralPattern 空间。"""
    
    METRICS_FILE = "session_metrics.json"
    MAX_RECORDS = 50
    
    def __init__(self, memory_dir: Path):
        self._file = memory_dir / self.METRICS_FILE
        self._records: list[dict] = self._load()
    
    def record(self, state: WorkspaceState, config_snapshot: dict) -> SessionQualityMetrics:
        """计算并追加记录。超过 MAX_RECORDS 时裁剪最旧的。"""
        ...
    
    def get_recent(self, n: int = 10) -> list[SessionQualityMetrics]:
        ...
    
    def get_habit_effectiveness(self, habit_id: str, min_samples: int = 5) -> float | None:
        """
        对比"注入了该习惯的 sessions" vs "未注入的 sessions" 的 quality_score 差异。
        样本不足时返回 None。
        """
        ...
```

### 3.5 与已有基础设施的关系

```
已有:                                    Phase 1 新增:
record_review_stats() ──────────────→   保持不变（服务 GateConfig）
FindingQualityGate.score() ─────────→   被 EvolutionMetrics 复用
post_edit_verify PASS/WARN/FAIL ────→   被 edits_success_rate 复用
EvolutionEngine.record_session_stats ─→ 被 config_snapshot 复用
```

### 3.6 文件影响

| 文件 | 改动 | 工作量 |
|------|------|--------|
| `core/evolution_metrics.py` | **新增** | ~200 行 |
| `core/session_finalizer.py` | `end_session_with_reflection` 中调用 metrics.record() | ~15 行 |
| `tests/test_v2_evolution_metrics.py` | **新增** 测试 | ~180 行 |

### 3.7 验证标准

1. quality_score 区分度：用 mock 数据模拟高/低质量 session，分值差异 > 0.3
2. `get_habit_effectiveness()` 在样本 < min_samples 时返回 None
3. 滑动窗口正确：超过 50 条时裁剪最旧的
4. 全量回归通过

### 3.8 预计工作量：1 天

---

## 四、Phase 2: 策略先验 + GateConfig 自适应

> **风险等级**: 中
> **LLM 成本**: 零（纯统计推理）
> **核心产出**: Agent 获得历史经验信息（信息呈现，不控制）

### 4.1 StrategyPrior

**新增文件**: `core/strategy_prior.py`

当前 Agent 在 `CognitiveState.auto_infer_strategy()` 中根据当前状态选策略，但没有历史信息。StrategyPrior 提供"类似情况下过去什么策略效果最好"的参考——纯信息，不强制。

```python
"""
core/strategy_prior.py — 策略选择先验

设计原则:
    - 只呈现信息, 不强制选择 (Agent 仍有完全的策略自主权)
    - evidence_count < 3 的策略经验不注入 (避免噪声)
    - 总注入量 ≤ 200 tokens (不占过多 context)
    - 论文类型条件化 (DID 论文的策略经验不影响 RCT 论文)

输出格式 (注入 assembler 的 WORKSPACE_STATE 区域):
    "## 策略经验参考
     - 审 DID 论文时: deep_investigation 在 findings≥3 后切入效率最高 (5次验证)
     注意: 以上仅供参考, 你应根据当前情况自主判断。"
"""

@dataclass 
class StrategyOutcome:
    strategy: str
    paper_type: str
    entry_turn: int
    quality_before: float
    quality_after: float
    session_quality: float

class StrategyPrior:
    def __init__(self, memory_dir: Path):
        self._file = memory_dir / "strategy_outcomes.json"
    
    def record_outcome(self, state: WorkspaceState, quality_score: float):
        """从 CognitiveState 的策略变化历史中提取 outcome。"""
        ...
    
    def get_prior_for_context(self, paper_type: str, current_turn: int) -> str | None:
        """返回 None 如果数据不足 (< 3 条验证)，否则返回 ≤200 tokens 的参考文本。"""
        ...
```

### 4.2 AdaptiveGate

**新增文件**: `core/adaptive_gate.py`

`gate_config.py` 的 `compute_gate_config()` 已能从历史中计算最优 idle_rounds。Phase 2 扩展适应范围到 self_eval_interval 和 max_loop_turns。

```python
"""
core/adaptive_gate.py — GateConfig 参数自适应

安全约束:
    - idle_rounds ∈ [2, 6]
    - self_eval_interval ∈ [3, 8]
    - max_loop_turns ∈ [20, 80]
    - 每次调整步长 ≤ 1
"""

class AdaptiveGate:
    def __init__(self, metrics: EvolutionMetrics):
        self._metrics = metrics
    
    def suggest_config(self, paper_type: str) -> dict:
        """
        取该 paper_type 最高 quality 的 top-25% sessions 的 gate 配置，
        在当前配置和最优配置之间取加权平均（保守更新）。
        """
        ...
```

### 4.3 文件影响

| 文件 | 改动 | 工作量 |
|------|------|--------|
| `core/strategy_prior.py` | **新增** | ~150 行 |
| `core/adaptive_gate.py` | **新增** | ~120 行 |
| `core/assembler.py` | 注入 strategy prior 文本 | ~15 行 |
| `core/session_finalizer.py` | 记录 strategy outcome | ~10 行 |
| `tests/test_v2_strategy_prior.py` | **新增** | ~120 行 |
| `tests/test_v2_adaptive_gate.py` | **新增** | ~100 行 |

### 4.4 验证标准

1. StrategyPrior 在数据不足时返回 None（不注入噪声）
2. StrategyPrior 注入文本 ≤ 200 tokens
3. AdaptiveGate 建议值始终在安全范围内，步长 ≤ 1
4. 全量回归通过

### 4.5 预计工作量：1.5 天

---

## 五、Phase 3: 元反思（MetaReflector）

> **风险等级**: 较高
> **LLM 成本**: 每 10 个 session 1 次 LLM call (~800 tokens)
> **核心产出**: 系统能评估"反思机制是否在正常工作"

### 5.1 问题本质

SessionReflector 每次 session 产出 0~5 条经验。但这些经验的质量如何？是泛泛而谈的废话（"要仔细审稿"）还是具体可操作的发现？系统目前没有机制回答这个问题。

### 5.2 设计方案（v1.1 精简）

**v1.0**：Phase 3 同时做 MetaReflector + ConvergenceMonitor。
**v1.1 修正**：Phase 3 只做 MetaReflector。理由：ConvergenceMonitor 需要 20+ session 数据才有意义，而 MetaReflector 在 10 个 session 后就能开始工作。把两者绑定会延迟 MetaReflector 的价值交付。

```python
"""
core/meta_reflection.py — 元反思: 评估并优化反思过程

递归层级: Level 2
触发条件: 每 EVAL_WINDOW (=10) 个 session 执行一次
安全约束:
    - prompt 修改幅度 ≤ 30% (Levenshtein距离)
    - 连续 2 次 metrics 下降 → 回滚
    - 回滚后冷却 10 个 session
    - 核心结构（JSON输出格式）不可修改
"""

@dataclass
class ReflectionQualityMetrics:
    window_size: int
    total_reflections_produced: int
    reflections_matured_to_habit: int
    habits_actually_injected: int
    habits_correlated_with_improvement: int   # 关键：用 quality_score 相关性，不用 activation_count
    
    @property
    def conversion_rate(self) -> float:
        if self.total_reflections_produced == 0: return 0.0
        return self.reflections_matured_to_habit / self.total_reflections_produced
    
    @property
    def utility_rate(self) -> float:
        if self.habits_actually_injected == 0: return 0.0
        return self.habits_correlated_with_improvement / self.habits_actually_injected


class MetaReflector:
    EVAL_WINDOW: int = 10
    MAX_PROMPT_DRIFT: float = 0.3
    ROLLBACK_THRESHOLD: int = 2
    COOLDOWN_SESSIONS: int = 10
    ACCEPTABLE_CONVERSION_RATE: float = 0.1
    ACCEPTABLE_UTILITY_RATE: float = 0.3
    
    def __init__(self, memory_dir: Path, metrics: EvolutionMetrics):
        self._memory_dir = memory_dir
        self._metrics = metrics
        self._prompt_history: list[str] = []
    
    def should_evaluate(self, session_count: int) -> bool:
        """每 EVAL_WINDOW 个 session 执行一次。"""
        ...
    
    async def evaluate_and_adapt(
        self, llm_call_fn: Callable, current_prompt: str,
    ) -> tuple[str, ReflectionQualityMetrics]:
        """评估反思质量，必要时调整 prompt。不需要修改时返回原 prompt。"""
        ...
    
    def _compute_metrics(self) -> ReflectionQualityMetrics:
        """
        关键修正 (v1.1):
        utility_rate 用 EvolutionMetrics.get_habit_effectiveness() 的相关性,
        而不是 HabitLearner 的 activation_count。
        
        因为 activation_count 只证明"习惯被注入了 prompt"，
        不证明"LLM 真正遵循了且产出更好了"。
        """
        ...
    
    def _validate_new_prompt(self, old: str, new: str) -> bool:
        """验证修改幅度在允许范围内, 且保留核心结构。"""
        ...
    
    def _rollback_if_needed(self, metrics_history: list[float]) -> str | None:
        """如果连续下降, 回滚到上一个版本。"""
        ...
```

#### Meta-Reflection 的 LLM Prompt

```
System: 你是一个反思系统的优化器。你要评估一个"反思Prompt"的效果,
       并提出改进建议。你的目标是让反思产出更具体、更可操作的经验。

User: ## 当前反思 Prompt
{current_prompt}

## 最近 10 个 session 的反思统计
- 总共产出 {total} 条反思
- 其中 {matured} 条最终成为习惯 (转化率 {rate}%)
- {util}% 的习惯与 session 质量提升相关

## 常见问题样本
{low_quality_examples}

## 请建议修改
只修改内容, 不修改 JSON 输出格式。修改幅度控制在 30% 以内。
输出修改后的完整 prompt。
```

### 5.3 文件影响

| 文件 | 改动 | 工作量 |
|------|------|--------|
| `core/meta_reflection.py` | **新增** | ~250 行 |
| `core/reflection.py` | `_REFLECTION_SYSTEM_PROMPT` 改为可配置属性 | ~20 行 |
| `core/session_finalizer.py` | 集成 meta 层触发逻辑 | ~30 行 |
| `tests/test_v2_meta_reflection.py` | **新增** | ~200 行 |

### 5.4 验证标准

1. MetaReflector 的 prompt 修改幅度始终 ≤ 30%
2. 回滚机制在 metrics 连续下降时正确触发
3. 冷却期内不做任何修改
4. 全量回归测试通过

### 5.5 预计工作量：2 天

---

## 六、Phase 4: 进化审计 + 收敛监控（完整闭环）

> **风险等级**: 高
> **LLM 成本**: 每 N 个 session 1 次 LLM call（可选）
> **核心产出**: HabitLearner 阈值动态调整 + 系统收敛检测
> **前置条件**: Phase 1-3 全部完成 + 至少 20+ session 的 metrics 数据

### 6.1 EvolutionAuditor

**新增文件**: `core/evolution_auditor.py`

#### 问题本质

HabitLearner 用 `evidence >= 3` 和 `effectiveness >= 0.6` 决定何时将 pattern 升级为 habit。这两个阈值是人类拍脑袋定的。如果对某类论文应该更严格（需要 5 次验证）或更宽松（2 次就够），系统目前无法感知。

#### 设计方案

```python
"""
core/evolution_auditor.py — 进化系统审计与阈值自适应

核心逻辑:
    1. 利用 EvolutionMetrics 对比 "有习惯注入" vs "无习惯注入" 的 session 质量
    2. 计算习惯系统的边际价值 (marginal_value)
    3. 根据 marginal_value 调整 HabitLearner 的阈值

决策规则:
    if marginal_value > 0.1:      # 习惯系统有明显正贡献
        → 可以降低阈值 (更积极地学习)
        → min_evidence -= 1 (下限=2)
        → min_effectiveness -= 0.05 (下限=0.4)
    
    elif 0.02 < marginal_value <= 0.1:  # 贡献一般
        → 保持不变
    
    elif marginal_value <= 0.02:   # 贡献微弱
        → 提高阈值 (更严格地筛选)
        → min_evidence += 1 (上限=5)
        → min_effectiveness += 0.05 (上限=0.8)
    
    elif marginal_value < 0:      # 有负面影响
        → 紧急回滚: 清理最近学到的习惯 + 恢复默认阈值

安全约束:
    - min_evidence 范围: [2, 5]
    - min_effectiveness 范围: [0.4, 0.8]
    - 每次调整步长 ≤ 1 (evidence) 或 ≤ 0.05 (effectiveness)
    - 调整后需要 5 个 session 的观察期才能再次调整
"""

@dataclass
class EvolutionHealth:
    """进化系统健康度报告。"""
    habit_pool_size: int
    avg_habit_confidence: float
    habit_utilization_rate: float          # 被选中注入的比例
    session_quality_with_habits: float     # 有习惯时的平均 quality_score
    session_quality_without: float         # 无习惯时的 quality_score
    marginal_value: float                  # with - without
    
    suggested_min_evidence: int
    suggested_min_effectiveness: float


class EvolutionAuditor:
    """审计进化系统效果, 动态调整学习阈值。"""
    
    EVIDENCE_RANGE: tuple[int, int] = (2, 5)
    EFFECTIVENESS_RANGE: tuple[float, float] = (0.4, 0.8)
    EVIDENCE_STEP: int = 1
    EFFECTIVENESS_STEP: float = 0.05
    HIGH_VALUE: float = 0.1
    LOW_VALUE: float = 0.02
    OBSERVATION_PERIOD: int = 5  # sessions
    
    def __init__(self, memory: MemoryStore):
        self._memory = memory
    
    def compute_health(self) -> EvolutionHealth:
        """计算进化系统当前健康度。"""
        ...
    
    def suggest_threshold_adjustment(self, health: EvolutionHealth) -> dict | None:
        """基于健康度建议阈值调整。"""
        ...
    
    def apply_adjustment(self, adjustment: dict):
        """应用阈值调整, 记录变更历史。"""
        ...
    
    def emergency_rollback(self):
        """紧急回滚: 清理最近习惯 + 恢复默认阈值。"""
        ...
```

### 6.2 ConvergenceMonitor（从原 Phase 3 降级至此）

**新增文件**: `core/convergence_monitor.py`

#### 为什么降级

代码审查后发现：当前系统连"单层进化"都还没有足够的运行数据验证 HabitLearner 是否真的有效。在没有数据证明 L1 有效之前就建 L2 的收敛检测——是在优化一个还没被验证有效的系统。

ConvergenceMonitor 需要 20+ session 的 metrics 数据才有意义，所以和 EvolutionAuditor 放在同一个 Phase。

#### 设计方案

```python
"""
core/convergence_monitor.py — 收敛检测与模式管理

状态机:
    EXPLORING → 积极学习, MetaReflector 正常运行
    EXPLOITING → 学习放缓, MetaReflector 降频
    CONVERGED → 基本停止自修改, 仅保持监控
    
转换条件:
    EXPLORING → EXPLOITING:
        - 最近 K (=10) 个 session 的 quality_score 方差 < ε (=0.05)
        - 且习惯池变化率 < δ (=0.1)
    
    EXPLOITING → CONVERGED:
        - 在 EXPLOITING 下连续 M (=20) 个 session 无有效自修改
    
    ANY → EXPLORING (外部冲击):
        - quality_score 突降 > 30% (连续2个session)
        - 遇到全新论文类型
"""

class ConvergenceMode(str, Enum):
    EXPLORING = "exploring"
    EXPLOITING = "exploiting"
    CONVERGED = "converged"

class ConvergenceMonitor:
    """监控自改进系统是否已收敛。"""
    
    QUALITY_EPSILON: float = 0.05
    HABIT_CHURN_DELTA: float = 0.1
    CONVERGENCE_WINDOW: int = 10
    EXPLOITING_TO_CONVERGED: int = 20
    SHOCK_THRESHOLD: float = 0.3
    
    def get_state(self) -> ConvergenceState: ...
    def should_run_meta(self) -> bool: ...
    def detect_shock(self, recent_scores: list[float]) -> bool: ...
```

### 6.3 AblationConfig 自动化

```python
# 每 20 个 session 中, 随机选择 2-3 个不注入习惯 (作为对照组)
# 用这些 session 的 quality_score 作为 "without habits" 的 baseline
if self._should_ablate_this_session():  # ~10-15% 概率
    self._ablation = AblationConfig.single_ablation("habit_injection")
```

### 6.4 完整闭环图

```
Session N 执行
    ↓
EvolutionMetrics.record() (Phase 1)
    ↓
StrategyPrior.record_strategy_outcome() (Phase 2)
    ↓
SessionReflector.reflect() (P2, 已有)
    ↓
[if session_count % EVAL_WINDOW == 0]
    ↓
MetaReflector.evaluate_and_adapt() (Phase 3)
    ↓
ConvergenceMonitor.get_state() (Phase 4)
    ↓
[if mode != CONVERGED && enough data]
    ↓
EvolutionAuditor.compute_health()
    ↓
[if health warrants adjustment]
    ↓
EvolutionAuditor.apply_adjustment()
    ↓
Session N+1 使用更新后的配置
```

### 6.5 文件影响

| 文件 | 改动 | 工作量 |
|------|------|--------|
| `core/evolution_auditor.py` | **新增** | ~200 行 |
| `core/convergence_monitor.py` | **新增** | ~180 行 |
| `core/evolution.py` | HabitLearner 阈值改为可配置属性 + ablation 概率 | ~50 行 |
| `tests/test_v2_evolution_auditor.py` | **新增** | ~180 行 |
| `tests/test_v2_convergence.py` | **新增** | ~150 行 |

### 6.6 验证标准

1. EvolutionAuditor 的阈值建议始终在安全范围内
2. 负面 marginal_value 触发紧急回滚
3. 观察期内不做二次调整
4. ConvergenceMonitor 状态转换逻辑正确
5. 外部冲击检测能从 CONVERGED 重新触发 EXPLORING
6. 自动 ablation 概率 ~10-15%
7. 全量回归测试通过

### 6.7 预计工作量：2.5 天

---

## 七、风险分析与缓解

### 7.1 风险矩阵

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| **记忆膨胀** | 高 | 高——JSON 文件无限增长 | Phase 0 淘汰机制 |
| **quality_score 不可靠** | 中 | 高——garbage in garbage out | 复用已验证的 FindingQualityGate |
| **反思prompt退化** | 中 | 高——产出垃圾习惯 | MAX_PROMPT_DRIFT + 回滚 + 冷却期 |
| **阈值振荡** | 低 | 中——习惯池不稳定 | 观察期(5 sessions) + 步长限制 |
| **过拟合特定论文类型** | 中 | 中——对新类型变差 | 论文类型条件化 + shock detection |
| **递归不收敛** | 低 | 中——无限自修改 | ConvergenceMonitor + CONVERGED 模式 |
| **负迁移** | 中 | 高——学到的经验有害 | evidence累积 + 紧急回滚 + 外部锚点 |
| **activation_count bias** | 中 | 中——常被选的习惯被认为"好" | 用 quality_score 相关性替代 activation_count |

### 7.2 最坏情况应对：全局 kill switch

```python
# 环境变量控制 (分层开关)
SCHOLAR_REFLECTION=1      # L1: SessionReflector (默认开)
SCHOLAR_META_ENABLED=1    # L2: Phase 3-4 meta 逻辑 (默认开)

# 完全退回静态模式:
SCHOLAR_REFLECTION=0 SCHOLAR_META_ENABLED=0
```

---

## 八、与 NEXT_STEPS.md 中未完成项的关系

### 8.1 P1-E2: Eval 框架适配

**状态**: 未完成
**与 C3 的关系**: 高度相关——Eval 框架提供的标准化评估结果可作为 quality_score 的补充信号源。
**建议**: C3 Phase 1 可独立于 E2 进行。E2 完成后，MetaReflector 可用 Eval 分数作为更可靠的外部锚点。

### 8.2 P2-C1: 跨任务自我进化

**状态**: 未完成（需 30+ 数据）
**结论**: **被 C3 Phase 1-2 包含**。EvolutionMetrics + StrategyPrior 就是"跨任务积累经验"的具体实现。不再单独排期。

### 8.3 P2-C2: 认知约束理论框架

**状态**: 未完成（需 E2）
**结论**: C3 Phase 4 的 EvolutionAuditor + AblationConfig 自动化提供 C2 的**工程基础**。学术验证（发论文级 ablation study）需额外排期。

### 8.4 P2-R1: Procedural Memory 回注

**状态**: 部分完成（SessionReflector 覆盖了"从经验中学习"）
**结论**: 保持 EditExperienceInjector 现状，StrategyPrior 不覆盖编辑经验注入——两者并行。

---

## 九、执行顺序与里程碑（修订版）

```
Week 1:
  Phase 0: 记忆健康 + 测试基座 (1天)
  ├── memory.py 独立单元测试 (~15 tests)
  ├── ProceduralPattern 淘汰机制 (effectiveness < 0.3 + 90天未reinforce → 归档)
  └── CLAUDE.md 刷新 (目录表对齐实际42个文件)

  Phase 1: EvolutionMetrics (1天)
  ├── 复用 FindingQualityGate 的 compute_quality() 作为 findings_quality
  ├── 复用 record_review_stats 的 density 计算
  ├── 新增 config_snapshot 记录 (哪些习惯被注入了)
  └── 编写测试 + 全量回归

Week 2:
  Phase 2: StrategyPrior + AdaptiveGate (1.5天)
  ├── strategy_prior.py: 只呈现, 不控制
  ├── adaptive_gate.py: 建议值, 不强制
  └── 编写测试 + 全量回归

  Phase 3: MetaReflector (2天)
  ├── meta_reflection.py: 评估反思质量
  ├── reflection.py: prompt 改为可配置
  └── 编写测试 + 全量回归

Week 3:
  Phase 4: EvolutionAuditor + ConvergenceMonitor (2.5天)
  ├── evolution_auditor.py: 阈值自适应
  ├── convergence_monitor.py: 收敛检测
  ├── evolution.py: 阈值可配置化 + ablation概率
  └── 编写测试 + 全量回归

  集成验证 (1天)
  ├── 完整闭环 smoke test (模拟 20 个 session)
  ├── 收敛性验证
  └── 回滚机制验证
```

### 里程碑检查点

| 里程碑 | 完成标志 | 可独立交付 |
|--------|---------|------------|
| M0: 基座稳固 | memory.py 有独立测试 + 淘汰机制工作 | ✅ 是 |
| M1: 度量闭环 | EvolutionMetrics 正确计算 quality_score | ✅ 是 |
| M2: 信息呈现 | Agent 能看到策略先验建议 | ✅ 是 |
| M3: 自我评估 | MetaReflector 能评估反思质量 | ✅ 是 |
| M4: 完整递归 | EvolutionAuditor 动态调整阈值 + 收敛检测 | ✅ 是 |
| M5: 收敛验证 | 模拟 50 session 后系统进入 CONVERGED | ❌ 需真实运行 |

---

## 十、不变的约束

1. **C1: Agent = cognition, not orchestration** — meta 层的修改通过 LLM 判断，不是硬编码规则替换
2. **C2: Constrain, don't control** — StrategyPrior 是信息呈现，不是策略强制
3. **C3: LLM 是无状态 CPU** — 所有 meta state 由 MemoryStore 外部管理
4. **C5: 在已有路径上增强** — 每个 Phase 构建在已有模块之上
5. **C6: CLAUDE.md 200 行硬限制**
6. **新增 C9: 有界递归** — MAX_META_DEPTH = 2，禁止 Level 3
7. **新增 C10: 外部度量锚点** — 自改进的好坏不依赖 Agent 自我评价
8. **新增 C11: 先验证基座再建上层** — Phase N+1 不能在 Phase N 有未验证的 bug 时启动

---

## 十一、DO / DON'T

### MUST DO

1. **Phase 0 先行**: 记忆淘汰机制 + memory.py 测试是一切的前提
2. **复用已有评分**: FindingQualityGate 的质量评分已经过 250 行测试验证，不要重新发明
3. **每个 Phase 独立验证可交付**: 不能 Phase 3 做一半发现 Phase 1 有 bug
4. **回滚机制比修改机制更重要**: 如果只能做一件事，先做回滚
5. **环境变量控制所有新功能**: SCHOLAR_META_ENABLED 默认 1，用户随时可关
6. **日志级别**: meta 层的所有决策必须 INFO 级别日志

### DON'T

1. **不追求 Level 3 递归** — 工程灾难
2. **不让 meta 层修改 SCHOLAR_IDENTITY** — 身份是宪法层
3. **不在 quality_score 中引入 Agent 自我评价** — 循环论证
4. **不在数据不足时强行自适应** — < 10 session 时 Phase 2-4 全部 no-op
5. **不为"更快收敛"降低 evidence 下限(≥2)** — 单次偶然不应永久修改
6. **不用 activation_count 衡量习惯效果** — 代码审查发现这只证明"被注入了"不证明"有效"

### WATCH OUT

1. **Goodhart's Law**: quality_score 被优化后可能失去指示性
2. **memory.py 的 procedures 列表无上限**: Phase 0 必须解决
3. **冷启动**: 前 10-20 session 是盲飞期，meta 层不会启动
4. **resolve_section_key 重复实现**: editing.py 和 misc.py 有重复代码，加新功能时注意
5. **identity.py 1339行**: 如果 Gödel 层需要修改 identity 注入逻辑，需要小心

---

## 十二、学术定位

**标题方向**: "Bounded Self-Improvement in Domain-Specific LLM Agents: A Case Study in Academic Review"

**贡献点**:
1. 提出有界递归自改进架构（区别于 Gödel Machine 的无限递归假设）
2. 在缺乏标量 reward 的主观任务中实现可收敛的自改进
3. 宪法层 + 外部度量锚点 + 累积验证三重安全保证
4. 实证：学术审稿场景下系统是否真的"越用越好"

**相关工作**: Gödel Machine (Schmidhuber 2003), Self-Rewarding Language Models (Yuan et al. 2024), SELF-EVOLVING (Wang et al. 2024), Voyager (Wang et al. 2023)

---

## 十三、代码审查发现（本次新增）

> 以下是对全部 15+ 核心模块逐一审查后的发现，影响 Gödel Agent 设计的关键点。

### 13.1 项目整体成熟度

| 维度 | 评分 | 说明 |
|------|------|------|
| 架构设计 | 5/5 | 认知代理架构设计达到研究级水准 |
| 代码质量 | 4/5 | 模块化好，扣分: Any类型过多、identity过大 |
| Agent-likeness | 5/5 | nudge-not-block、认知进化闭环、多层记忆 |
| 可测试性 | 4/5 | 纯函数守卫+模块边界清晰，缺集成测试 |
| 生产就绪度 | 3/5 | 缺日志/重试/监控/配置管理 |

### 13.2 影响 C3 设计的关键发现

| # | 发现 | 影响 | 处置 |
|---|------|------|------|
| 1 | `memory.py` procedures 列表无上限无淘汰 | Gödel 层会加速膨胀 | Phase 0 解决 |
| 2 | `finding_quality.py` 已有 250 行经过验证的质量评分 | quality_score 不需从零发明 | Phase 1 复用 |
| 3 | `activation_count` 只证明"注入了"不证明"有效" | 不能用它衡量习惯效果 | 用 quality_score 相关性 |
| 4 | `reflection.py` 无独立测试（刚补了 12 个） | 基座已补全 | ✅ 已解决 |
| 5 | `boundary_guard.py` 无独立测试 | 不影响 C3，但应补 | 低优先记录 |
| 6 | `identity.py` 1339 行单文件 | Meta 层如需修改身份注入有风险 | 宪法层禁止修改身份 |
| 7 | `metacognition.py` confidence 自评不可靠 | 不能作为 quality_score 的输入 | 只用客观指标 |
| 8 | 工具 schema (identity.py) 与实现 (tool_handlers/) 分离 | 新工具需改两处 | 不影响 C3 |

### 13.3 测试覆盖现状 (640 tests)

```
深度覆盖 ✅: harness, loop, phases, evolution, gate_config, hypothesis,
             cognition_graph, compaction, assembler, edit系列, finding_quality

覆盖不足 ⚠️: memory.py (核心持久层!), boundary_guard, metacognition,
             agent.py (主类), identity.py (动态构建), llm/ 目录全部

完全无覆盖 ❌: web_search, pdf_loader, paper_loader, bib_verify,
              checker, claim_signal, message_compressor, tool_reflect
```

---

> **终极自检**: 观察 meta 层的运行日志。如果你看到"系统在没有数据支撑的情况下修改了配置"——那就是 Gödel Agent 的失控。正确的日志应该是：度量 → 判断 → 修改（或不修改） → 观察 → 验证（或回滚）。每一步都有据可查。
