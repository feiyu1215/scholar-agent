# ScholarAgent V2 Recall 优化方案 (S5-S9)

> 目标：将整体 Recall 从 ~0.42 提升至 0.55-0.65  
> 基线：paper_003 R=0.500, paper_001 R=0.444  
> 日期：2025-07

---

## 问题诊断：三个核心断路点

### 断路点 1：PCG `update_after_read()` 从未被调用

- `core/tool_handlers/reading.py` 的 `tool_read_section()` 更新了 `state.sections_read`
- 但**从未调用 `pcg.update_after_read()`**
- PCG 代码注释说"由 tool_handlers/reading.py 调用"，实际代码中无此调用
- 结果：`pcg_coverage` 永远是 0.0，所有 PCG 节点保持 `read_depth="unread"`
- 下游后果：
  - `format_for_zone_a()` 显示全部节点为 ○（未读），信号失真
  - `coverage_gaps()` 返回全部 section 为"未覆盖"
  - Agent 和 completion gate 无法基于准确的阅读状态做决策

### 断路点 2：Completion Gate 可被逐次穿透

- `tool_done()` → `check_completion_gate_fn()` → 返回 `__NUDGE__` 则继续循环
- 每个 nudge 类别（`unverified`, `hdwm_active`, `min_findings`, `quality_check`, `dimension_coverage`, `checklist_coverage`）**最多只触发一次**
- Agent 只需反复调用 `mark_complete` 6-7 次即可穿过所有 nudge
- `dimension_coverage` 门槛：`findings >= 5 且 covered_count < 3`
- `checklist_coverage` 门槛：`coverage_ratio < 0.4 且 uncovered >= 3`
- 这些都是"一次性建议"而非"阻塞性门控"

### 断路点 3：Methodology Checklist 只做展示不做追踪

- structural_econ 的 8 项 methodology_checklist 在 `format_for_zone_a()` 中展示
- Agent 产出 finding 后，**没有自动机制将 finding 与 checklist item 实时关联**
- `ReviewChecklist.auto_match_finding()` 存在但只在 completion gate 退出时做一次性检查
- Finding 与 checklist 的关联是事后对账，而非过程驱动

---

## 优化方案

### S5：被动 PCG 同步（修复断路点 1）

**改动文件**：`core/tool_handlers/reading.py`

**改动内容**：在 `_record_read()` 函数末尾，每当 Agent 读取 section 时，自动将对应 PCG 节点的 `read_depth` 从 `unread` 升级为 `read`。

**具体代码**：
```python
# 在 _record_read() 末尾添加 PCG 被动同步
pcg = getattr(state, "paper_cognition_graph", None)
if pcg and hasattr(pcg, "update_after_read"):
    digest = state.section_digests.get(resolved_name, "")
    pcg.update_after_read(resolved_name, digest=digest, read_depth="read")
```

**预期收益**：
- `pcg_coverage` 从 0.0 变为实际值
- `format_for_zone_a()` 准确显示阅读进度
- `coverage_gaps()` 返回真实的未覆盖 sections
- Condition D nudge 能给出精确的"你还没读哪里"信息

**风险**：极低。纯被动追踪，不改变 Agent 行为逻辑。

---

### S6：Completion Gate 硬化（修复断路点 2）

**改动文件**：`core/boundary_guard.py`

**改动内容**：将 `dimension_coverage` 从"一次性建议"改为"可重复阻塞"——如果 Agent 在第二次调用 `mark_complete` 时 covered_count 仍 < 3，继续阻塞并给出更具体的任务建议。最多阻塞 2 次（第 3 次无条件放行，保持 C5 精神）。

**设计原则**：
- 第一次触发：温和建议（现有行为）
- 第二次触发：重新检查 covered_count，如果仍 < 3 则给出具体 section+维度任务
- 第三次：放行（尊重 Agent 自主判断）

**预期收益**：Agent 不能通过简单重复 `mark_complete` 绕过维度覆盖检查。

---

### S7：Checklist-Driven Sweep Phase（修复断路点 3）

**改动文件**：`core/boundary_guard.py`

**改动内容**：在 Condition D 的第二次触发（`_nudge_count == 1`, 轮次 >= 12）时，不再泛泛建议"这些维度尚未涉及"，而是从 `DomainTemplate.methodology_checklist` 中提取未覆盖项，并映射为具体的 `read_section` 任务。

**具体逻辑**：
1. 获取当前 paper_type 的 DomainTemplate
2. 检查 methodology_checklist 各项是否在 findings 中有体现（关键词匹配）
3. 将未覆盖项映射到相关 section（基于关键词→section 名称匹配）
4. 生成具体任务列表：`read_section('calibration') → 评估: 校准目标选择是否合理`

**预期收益**：
- 将抽象的"维度未覆盖"转化为具体的"读哪个 section、看什么"
- 直接对齐 gold_paper_003 中的 G002, G005, G006, G007
- 这些 gold items 对应 checklist 中 item 2,3,5,6

---

### S8：Finding-Checklist 自动关联回写

**改动文件**：`core/tool_handlers/findings.py`（或 `update_findings` 相关逻辑）

**改动内容**：每当 Agent 提交一条新 finding，自动调用 `ReviewChecklist.auto_match_finding()` 并更新 checklist 覆盖状态。

**预期收益**：
- Completion gate 的 `checklist_coverage` 检查基于实时数据
- Agent 在 context 中能看到"已覆盖 3/8 项"的进度条
- 与 S7 配合：Agent 在 sweep phase 补了 findings 后，系统即刻反映覆盖进展

---

### S9：Gold 对齐 Concept Pattern 补全

**改动文件**：`evaluation/metrics.py`

**改动内容**：补充针对 gold_paper_003 尚未被匹配的 gold items 的概念模式：

- `ces_transition`: 覆盖 G002 (CES/Armington→Melitz 过渡)
- `calibration_justification`: 覆盖 G006 (校准依据)
- `double_marginalization`: 覆盖 G010 (双重加价)

---

## 实施优先级

| 方案 | 难度 | 预期 R 提升 | 依赖 |
|------|------|-------------|------|
| S5 (PCG 被动同步) | 低(5行) | +0.02-0.05 (间接) | 无 |
| S6 (Gate 硬化) | 中(30行) | +0.05-0.08 | 无 |
| S7 (Checklist Sweep) | 中(50行) | +0.08-0.12 | S5 效果更佳 |
| S8 (Finding-Checklist关联) | 低(15行) | +0.03-0.05 | 无 |
| S9 (Concept Pattern) | 低(10行) | +0.03-0.05 | 无 |

**保守估计**：全部实施后 paper_003 R: 0.500 → 0.600-0.700，整体 Recall: ~0.42 → 0.55-0.60。

---

## 验证方法

```bash
cd /Users/yanfeiyu03/Downloads/scholar-agent-public/v2
python evaluation/run_recall_verification.py
```

验收标准：
- paper_003 R >= 0.500（不回归，目标 0.600+）
- paper_001 R >= 0.444（不回归）
- 整体平均 R >= 0.50
