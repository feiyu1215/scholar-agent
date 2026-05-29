# Recall 提升实施计划（精简可执行版 v2）

**日期**: 2026-05-28  
**目标**: 将 post-fix Recall 从 31.6% (6/19) 提升至 55-65%  
**原则**:
1. "改 prompt 是最直接的方案"——优先 prompt 改动
2. "选择合适的地方进行加入"——利用已有架构接口，不造新轮子
3. **泛化优先**——prompt 层写通用元认知模式，领域具体内容下沉到 `DomainTemplate` 和 `discipline_triggers`

---

## 泛化设计思路

当前的 gold standard findings 来自经济学论文（DID、calibration、multiple testing 等），但 ScholarAgent 服务所有学科。如果把 "看到 DID → 追问平行趋势" 这类 econ-specific 内容写进通用的 `SCHOLAR_IDENTITY`，那 ML 论文或临床试验论文审稿时就会出现不相关的噪音。

**已有的泛化架构**：

| 层级 | 机制 | 作用域 | 在哪里 |
|------|------|--------|--------|
| 通用本能 | `SCHOLAR_IDENTITY` 本能反应列表 | 所有论文 | `core/identity.py` |
| 学科激活 | `CognitiveHabit.discipline_triggers` | 按 paper_type 条件触发 | `core/habits.py` |
| 领域模板 | `DomainTemplate` in PCG | 按 paper_type 提供 checklist/focus_hints | `core/paper_cognition_graph.py` |

**本方案的分层**：
- 通用层（`SCHOLAR_IDENTITY`）：写 **"看到 [通用模式] → 做 [通用追问]"**
- 领域层（`discipline_triggers` / `DomainTemplate`）：写 **具体术语和 checklist**

---

## 总览：改动分三层

| 层级 | 类型 | 文件 | 预计解决 |
|------|------|------|---------|
| **S1** | 纯 Prompt（零代码） | `core/identity.py`, `core/habits.py` | 6~8 个 miss |
| **S2** | 轻量代码 + Prompt | `core/boundary_guard.py`, `core/paper_cognition_graph.py` | 2~3 个 miss（兜底） |
| **S3** | 利用已有 `__PARALLEL_SPAWN__` | `core/identity.py`（prompt 引导 spawn） | 3 个 miss |

---

## S1: 纯 Prompt 改动

### S1.1 在 SCHOLAR_IDENTITY 的"本能反应"中增加通用元认知模式

**文件**: `core/identity.py` line 29-39（`SCHOLAR_IDENTITY` 的 "你面对论文时的本能反应" 列表）

**在现有 10 条之后，追加以下 5 条（注意：全部是领域无关的元认知模式）**：

```
- 看到同一篇论文有多个统计检验/多个 outcome → 追问"多重比较怎么处理的？"——无论是 ML 里的多个 metric、临床的多个 endpoint、还是 econ 的多个 dependent variable，不做校正就有 family-wise error 风险
- 看到核心因果声称（X 导致 Y）→ 追问识别假设的正式检验在哪里——不只看描述性证据（如图表的趋势走向），还要找统计检验。如果只有 suggestive evidence 没有 formal test，这就是一个可记录的 gap
- 看到模型/理论中的关键参数被设为常数或归一化 → 追问 sensitivity analysis：如果这个参数变动 ±20-50%，核心定量结论如何变化？任何被"假设掉"的参数如果恰好决定了结论的量级，那 sensitivity 就是必须的
- 看到数值/计算方法（任何非解析求解：网格搜索、迭代、蒙特卡洛、梯度下降等）→ 追问实现细节透明度：收敛判据？计算规模？对超参数/初始值敏感吗？细节缺失意味着结果不可复现
- 看到理论模型的关键结构假设 → 对照实际数据/实验的样本特征描述，检查假设与现实的张力——2 人模型用于 6 人数据？同质 agent 假设用于高度异质样本？简化假设本身不是问题，但张力如果没被讨论就是问题
```

**为什么这些是通用的**：
- "多重比较" → 在 econ（多 outcomes）、ML（多 metrics/多 datasets）、临床（多 endpoints）、心理学（多量表）中都适用
- "因果声称 → formal test" → DID 要平行趋势、RCT 要 balance test、IV 要 first-stage F、ML 要因果消融
- "参数归一化 → sensitivity" → econ 的 calibration、ML 的超参数选择、理论的 regularity conditions
- "数值方法透明度" → grid search、Adam optimizer 学习率、MCMC 链收敛、FEM mesh size
- "模型假设 vs 数据特征" → 所有 model-based 论文共有的张力

---

### S1.2 强化 `assumption_boundary` 认知习惯（通用 + discipline_triggers）

**文件**: `core/habits.py` line 125-138

**将现有 content 替换为通用版**：

```python
CognitiveHabit(
    id="assumption_boundary",
    name="假设边界审视",
    phases=["DEEP_REVIEW"],
    priority=83,
    content=(
        "**假设边界审视**：理解核心假设后不停在'检验通过了'——追问假设本身可能在哪里"
        "不成立。对每个关键假设，想象怀疑者视角：'如果我想推翻这个假设，从哪个角度进攻？'\n"
        "**三个通用交叉检查**：\n"
        "① 参数选择 → 结论敏感性：被固定/归一化/校准的参数，"
        "如果其值决定了定量结论的量级，就必须有 sensitivity analysis\n"
        "② 模型结构 vs 数据现实：理论/模型的结构设定和实际样本描述之间有没有未讨论的张力？\n"
        "③ 操作化 vs 构念：实验/实证中的度量方式是否真正捕捉了论文声称要测量的抽象概念？"
        "有没有 validity 证据或讨论？"
    ),
    discipline_triggers={
        "empirical_econ": ["identification", "endogeneity", "causal", "calibrat", "normalize"],
        "ml_experiment": ["hyperparameter", "architecture choice", "loss function", "inductive bias"],
        "clinical": ["endpoint", "surrogate", "biomarker", "construct validity"],
        "theoretical": ["axiom", "regularity condition", "convexity", "normalization"],
    },
),
```

**泛化要点**：content 文本完全不含任何领域术语（没有 DID、没有 Bonferroni、没有 ablation），只表达通用的认知模式。`discipline_triggers` 负责在特定 paper_type 下提升这个 habit 的选中优先级。

---

### S1.3 扩展 `pre_completion_check` 认知习惯

**文件**: `core/habits.py` line 153-163

**将现有 content 替换为**：

```python
content=(
    "**完成前自检**：结束前做六项检查——\n"
    "(1) 有没有 high-priority + needs_verification 还未追查？\n"
    "(2) 核心 claim 是否有外部文献校准？\n"
    "(3) 是否深入了解过至少一篇相关外部论文？\n"
    "(4) 发现是否集中在同一维度（遗漏其他维度）？\n"
    "(5) 方法论基础设施：多重比较处理、核心假设的正式检验、参数/超参数敏感性——"
    "与本文相关的那些，我是否至少检查过？\n"
    "(6) 新颖性/贡献声称：如果论文说'首次/无先例/填补空白/outperforms all'，"
    "我搜索确认过吗？"
),
```

**泛化要点**：第(5)条用"多重比较处理、核心假设的正式检验、参数/超参数敏感性"——这些表述在 ML（超参数）、econ（参数）、clinical（sensitivity analysis）中都成立。第(6)条的 "outperforms all" 涵盖了 ML 的 SOTA 声称。

---

### S1.4 在 `SCHOLAR_IDENTITY` 第 15 条"视角分裂"中增加通用 spawn 触发规则

**文件**: `core/identity.py` line 115-119（认知习惯第 15 条末尾）

**在第 15 条末尾追加一段**：

```
   **具体触发场景**：除了跨学科不确定性，以下场景也应该 spawn 专项视角——这些检查需要逐行精确比对，spawn 一个专注的视角比你自己夹在其他分析中做更有效：
   
   - 论文有 ≥3 个数据表格（含 summary statistics / results tables / appendix tables）→ spawn 一个 "data_consistency_auditor" 视角，专门做跨表数值交叉验证（同一统计量在不同表中是否一致、是否有不合理的数据重复）
   - 论文有正文 + 附录共 ≥5 个公式/方程（含推导链或模型定义）→ spawn 一个 "symbol_auditor" 视角，专门构建 symbol table 检查跨 section 变量名/下标/符号体系的一致性
   - 你已在 DEEP_REVIEW 过了约 50% 轮次，且尚未系统性检查过数据一致性/符号一致性 → 这是最佳 spawn 时机
   
   这不是"你不确定才 spawn"——而是"这类精确逐行比对任务，专注的子视角天然比兼顾多维度的主视角做得更好"。用 spawn_parallel_readers 一次发起 2-4 个。
```

**泛化要点**：数据表格和公式在所有定量学科中都存在。这个触发规则不依赖于领域——只依赖于论文的**结构特征**（表格数量、公式数量）。

---

## S2: 轻量代码改动（利用已有接口）

### S2.1 在 Completion Gate 中增加通用"维度覆盖" nudge

**文件**: `core/boundary_guard.py`（`check_completion_gate` 函数末尾）

**新增代码**（~25 行）：

```python
    # --- 维度覆盖检查（通用方法论基础设施）---
    if "dimension_coverage" not in completion_nudges_fired:
        # 通用关键词：涵盖所有学科的方法论审查信号
        methodology_keywords = {
            # 通用
            "multiple", "多重", "correction", "校正", "adjust",
            "sensitivity", "敏感性", "robustness", "稳健",
            "consistency", "一致", "inconsist", "矛盾", "mismatch",
            # 假设检验
            "assumption", "假设", "validity", "有效性",
            "formal test", "正式检验",
        }
        findings_text = " ".join(
            f.get("finding", "").lower() for f in state.findings
        )
        covered_any = any(kw in findings_text for kw in methodology_keywords)
        
        # 只在有一定数量 findings 但完全没涉及方法论审查时触发
        if not covered_any and len(state.findings) >= 3:
            completion_nudges_fired.add("dimension_coverage")
            return (
                f"你有 {len(state.findings)} 条发现，但似乎没有涉及方法论基础设施审查"
                f"（如多重比较处理、核心假设正式检验、参数敏感性分析、数据一致性校验）。\n"
                f"两种可能：(1) 本文方法论确实无明显问题；"
                f"(2) 你尚未系统检查过这些维度。\n"
                f"如果是后者，建议快速过一遍后再结束——再次 mark_complete 即完成。"
            ), completion_nudges_fired
```

**泛化要点**：关键词列表不含任何领域特异术语（没有 DID、没有 ablation、没有 endpoint），只包含跨领域通用的方法论元概念。

---

### S2.2 在 PCG DomainTemplate 中增加 `methodology_checklist` 字段

**文件**: `core/paper_cognition_graph.py`

**这是唯一放领域特异内容的地方**——因为 DomainTemplate 本身就是按 paper_type 分类的。

**改动 1**: `DomainTemplate` dataclass 新增字段

```python
@dataclass
class DomainTemplate:
    paper_type: str
    expected_sections: list[str]
    critical_edges: list[tuple[str, str, float]]
    focus_hints: list[str]
    methodology_checklist: list[str] = field(default_factory=list)  # NEW
```

**改动 2**: 为每个已有 paper_type 填充领域 checklist

```python
# empirical_econ
methodology_checklist=[
    "识别策略关键假设是否有正式统计检验（如平行趋势 formal test, first-stage F-stat）",
    "多重比较：多个 outcome 是否有 Bonferroni/FDR/Romano-Wolf 校正",
    "参数敏感性：calibrated/normalized 参数变动对结论的量化影响",
    "跨表数据一致性：同一统计量在不同表格中值是否一致",
    "模型设定 vs 样本特征：模型人数/结构与数据描述性统计是否匹配",
],

# ml_experiment
methodology_checklist=[
    "多数据集/多 metric 报告是否有统计显著性检验或 confidence interval",
    "超参数选择：搜索范围、选择标准、对最终结果的敏感度是否报告",
    "计算细节透明度：训练时长、硬件、随机种子、收敛判据",
    "Ablation completeness：每个声称有用的组件是否都有 w/o 对照",
    "Baseline fairness：对比方法是否用了同等调优力度",
],

# theoretical
methodology_checklist=[
    "假设 → 定理的逻辑链中是否有未声明的隐含条件",
    "证明中的符号是否与正文/附录一致（注意下标、上标变化）",
    "关键假设的 tightness：能否构造满足所有假设但结论恰好成立的极端 case",
    "正则性条件：是否过强以至于排除了有意义的应用场景",
],

# survey
methodology_checklist=[
    "覆盖声称 vs 实际覆盖：是否有被遗漏的重要工作线",
    "分类法（taxonomy）是否 MECE（互斥且穷尽）",
    "时间范围：是否声称了某个时间段的全面覆盖但有明显遗漏",
],
```

**改动 3**: 在 `format_for_zone_a()` 输出末尾，如果 checklist 非空则追加

```python
if template and template.methodology_checklist:
    lines.append("")
    lines.append("[方法论审查要点] " + " | ".join(template.methodology_checklist[:3]))
```

**泛化要点**：领域特异性内容**全部**封装在 DomainTemplate 中——这是系统为此设计的机制。新增 paper_type 只需新增一个 DomainTemplate 即可，不影响通用 prompt。

---

### S2.3 在 `check_reflection_needed` 中增加条件 D：精确校对未覆盖

**文件**: `core/boundary_guard.py`（`check_reflection_needed` 函数末尾）

**新增条件 D**（~20 行）：

```python
    # === 条件 D: 数据/符号精确校对未覆盖催促 ===
    if not getattr(s, '_cross_check_nudge_fired', False):
        # 通用判断：论文是否有足够多的定量内容值得做交叉校对
        has_quantitative_depth = (
            len(s.sections_read) >= 4
            and any(
                any(kw in name.lower() for kw in [
                    "table", "result", "experiment", "appendix", "data", "figure"
                ])
                for name in s.sections_read
            )
        )
        
        # 检查是否已有交叉校对类的 findings
        cross_check_keywords = ["一致", "重复", "矛盾", "inconsist", "duplicate",
                                "mismatch", "symbol", "notation", "符号", "下标"]
        cross_check_done = any(
            any(kw in f.get("finding", "").lower() for kw in cross_check_keywords)
            for f in s.findings
        )
        
        if has_quantitative_depth and not cross_check_done and s.loop_turns >= 10:
            s._cross_check_nudge_fired = True
            return (
                f"[覆盖提醒] 你已读了多个含定量内容的 sections，"
                f"但尚未对跨表/跨section的数据一致性或符号一致性做过系统检查。"
                f"考虑用 spawn_parallel_readers 发起专项视角"
                f"（如 data_consistency_auditor 或 symbol_auditor）做精确交叉校对？"
                f"（这只是提醒——如果你已在阅读中确认了一致性，继续也可以。）"
            )
```

**泛化要点**：触发条件基于"有定量 section（table/result/experiment/appendix）"——这在所有实证/实验学科中通用，不依赖于特定领域。

---

## 不做的事项

| 原 P1 设计中的方案 | 决定 | 理由 |
|------|------|------|
| `ReviewChecklist` 全新数据结构 | **不做** | S2.2 的 `methodology_checklist` + S1.3 的 habit 已覆盖功能，且零侵入 |
| Layer 3 对抗验证 Devil's Advocate | **不做** | 解决 Precision 而非 Recall，不是当前主矛盾 |
| 自动 50% 轮次 spawn 编排逻辑 | **不做** | S1.4 prompt 引导 + S2.3 nudge 已足够 |
| LLM-as-Judge 评估 | **不做** | 评估基础设施，不影响审稿能力 |
| 在通用 prompt 中写领域特异术语 | **不做** | 破坏泛化性，用 DomainTemplate 替代 |

---

## 执行顺序

### Phase 1: Prompt 改动（S1 全部）— 零代码

| 步骤 | 文件 | 改动 |
|------|------|------|
| 1a | `core/identity.py` | 在本能反应列表（line 39 之后）追加 5 条通用元认知 |
| 1b | `core/identity.py` | 在第 15 条末尾追加 spawn 触发规则 |
| 1c | `core/habits.py` | 替换 `assumption_boundary` habit content + discipline_triggers |
| 1d | `core/habits.py` | 替换 `pre_completion_check` habit content |

**验证**: 对 paper_001 和 paper_003 重跑 evaluation，观察 Recall 变化。

### Phase 2: 代码改动（S2 全部）— ✅ 已完成

| 步骤 | 文件 | 改动 | 状态 |
|------|------|------|------|
| 2a | `core/boundary_guard.py` | 在 `check_completion_gate` 末尾加维度覆盖 nudge | ✅ |
| 2b | `core/boundary_guard.py` | 在 `check_reflection_needed` 末尾加条件 D（方法论交叉审查） | ✅ |
| 2c | `core/paper_cognition_graph.py` | `DomainTemplate` 加 `methodology_checklist` 字段 | ✅ |
| 2d | `core/paper_cognition_graph.py` | 为 4 个已有 paper_type 填充 checklist | ✅ |
| 2e | `core/paper_cognition_graph.py` | `format_for_zone_a()` 追加 checklist 展示 | ✅ |

### Phase 3: ReviewChecklist 结构化追踪（S3）— ✅ 已完成

| 步骤 | 文件 | 改动 | 状态 |
|------|------|------|------|
| 3a | `core/review_checklist.py` | 新模块: ChecklistItem + ReviewChecklist（自动关键词匹配、Zone A 格式化、序列化） | ✅ |
| 3b | `core/state.py` | WorkspaceState 增加 `review_checklist` 字段 | ✅ |
| 3c | `core/harness.py` | PCG 初始化后自动从 DomainTemplate 创建 ReviewChecklist | ✅ |
| 3d | `core/tool_handlers/findings.py` | submit_finding 时自动匹配 checklist 维度 | ✅ |

### Phase 4: Auto-Spawn 调度（S4）— ✅ 已完成

| 步骤 | 文件 | 改动 | 状态 |
|------|------|------|------|
| 4a | `core/boundary_guard.py` | 新函数 `check_auto_spawn_needed`（DEEP_REVIEW 30%+ 触发） | ✅ |
| 4b | `core/harness.py` | 添加 `check_auto_spawn_needed` wrapper | ✅ |
| 4c | `core/loop.py` | 在两个路径（dispatcher + fallback）注入 spawn nudge | ✅ |

### Phase 5: 回归验证

- 确保原本已命中的 6 个 gold findings 没有退化
- 多次运行取稳定性（至少 3 次 per paper）
- **泛化验证**：如果有非 econ 论文的 gold standard，跑一遍确认 prompt 不产生领域噪音

---

## 改动量统计

| 类别 | 改动量 | 修改文件数 | 风险等级 |
|------|--------|-----------|---------|
| S1 Prompt | ~50 行文本 | 2 | 极低 |
| S2 Code | ~85 行 Python | 2 | 低 |
| S3 ReviewChecklist | ~200 行新模块 + ~15 行集成 | 4 | 低 |
| S4 Auto-Spawn | ~85 行 Python | 3 | 低 |
| **总计** | ~435 行 | 8 个文件（含 1 个新文件） | 低 |

---

## 泛化验证 Checklist

实施完成后，用以下问题验证泛化性：

- [ ] 把 S1.1 的 5 条本能反应逐条检查：对 ML 论文读起来自然吗？对临床论文呢？
- [ ] `assumption_boundary` 的 content 文本：不含任何只在 econ 中才有意义的术语？
- [ ] `pre_completion_check` 的第(5)条：ML 审稿人读到"多重比较处理、参数/超参数敏感性"会觉得相关吗？
- [ ] S2.1 Completion Gate 的关键词列表：有没有只在 econ 中出现的词？
- [ ] S2.2 的 methodology_checklist：每个 paper_type 的 checklist 内容是否确实只在该类型中适用？
- [ ] S2.3 的触发条件 `has_quantitative_depth`：对纯理论论文（无 table/result）会不会误触发？（答案应该是不会——因为条件要求 section 名含 table/result/experiment）

---

## 与原设计文档的关系

本文档替代 `P1_RECALL_IMPROVEMENT_DESIGN.md` 作为实施依据。核心转变：

1. **从领域绑定 → 领域解耦**：通用认知模式在 prompt 层，领域内容在 DomainTemplate
2. **从"建新机制" → "在现有接入点加信息"**：利用已有的 SignalDispatcher、Phase Gate、PCG Zone A、`__PARALLEL_SPAWN__`
3. **从 4 个 Sprint → 2 个 Phase**：精简到可以在 1-2 天内完成的工作量
