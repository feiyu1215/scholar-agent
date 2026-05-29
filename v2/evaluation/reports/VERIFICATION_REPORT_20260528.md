# Recall Verification Report — Phase Transition Fix

**Date**: 2026-05-28  
**Model**: gpt-4.1  
**Changes Verified**: Auto Phase Transition + Two-Phase Spawn + Sub-Agent Tool Expansion + Phase Inheritance

---

## Executive Summary

实施 **自动 Phase Transition** 修复后，系统在两篇 gold standard 论文上的 Aggregate F1 从 **46.3%** 提升到 **57.1%**，提升幅度 **+10.8%**。

核心修复：诊断发现 agent 因设计未闭合永远停留在 `initial_scan` 阶段（9/25 工具可见），自动 phase transition 使 agent 在条件满足时无感进入 `deep_review`（20/25 工具可见），两相 spawn 等下游机制得以触发。

---

## Results

### Per-Paper Comparison

| Paper | Metric | Baseline | Post-Fix | Delta |
|-------|--------|----------|----------|-------|
| paper_001 | Precision | 60.0% | 44.4% | -15.6% |
| paper_001 | Recall | 33.3% | 44.4% | **+11.1%** |
| paper_001 | F1 | 42.6% | 44.4% | **+1.8%** |
| paper_003 | Precision | 57.1% | 85.7% | **+28.6%** |
| paper_003 | Recall | 44.4% | 60.0% | **+15.6%** |
| paper_003 | F1 | 49.9% | 70.6% | **+20.7%** |

### Aggregate

| Metric | Baseline | Post-Fix | Delta |
|--------|----------|----------|-------|
| Precision | 58.3% | 62.5% | **+4.2%** |
| Recall | 38.9% | 52.6% | **+13.7%** |
| F1 | 46.3% | 57.1% | **+10.8%** |

### Match Details

- **Paper 001**: 4/9 gold findings matched (G002, G003, G004, G006)
  - Missed: G001 (appendix symbol error), G005 (table duplication), G007 (multiple testing), G008 (model-reality gap), G009 (DID parallel trends)
- **Paper 003**: 6/10 gold findings matched
  - High precision: 6/7 predicted were valid

---

## Root Cause Diagnosis

### Problem: Agent Never Entered `deep_review`

在修复前的 verify_run 日志中，agent 在 26 轮中始终显示 `[Phase] initial_scan: 9/25 tools visible`。

**Root Cause Chain**:

1. `SCHOLAR_IDENTITY` L46 明确告诉 agent: *"你的思考是连续的、自然的。不存在'阶段'"*
2. `request_phase_transition` 工具虽注册到 `ToolRegistry`，但**其 schema 从未加入 `SCHOLAR_TOOLS`**
3. LLM 看到的 tool list 来自 `SCHOLAR_TOOLS`，故**永远无法调用** `request_phase_transition`
4. `PhaseFSM.suggest_transition()` 方法存在但**从未在生产代码中被调用**
5. 结果：agent 永远停在 `initial_scan`，只有 9/25 工具可用，`spawn_perspective`、`fetch_paper_detail`、`read_reference` 等深度工具不可见
6. Two-phase spawn（依赖 `deep_review` 阶段）从未触发

### Fix: Automatic Phase Transition

在 `loop.py` 的每轮循环中新增 `_try_auto_phase_transition()`:

```python
def _try_auto_phase_transition(harness, verbose=False) -> bool:
    """INITIAL_SCAN → DEEP_REVIEW when:
       - sections_read >= 3 AND findings >= 1
       - OR sections_read >= 5 (unconditional)
    """
```

设计哲学：Agent 的 identity 不需要改变（"不存在阶段"是好的设计），FSM 作为 **Harness 层的隐式守护** 自动生效，agent 无需感知。

---

## Changes Made

| File | Change |
|------|--------|
| `core/loop.py` | 新增 `_try_auto_phase_transition()` 函数 + 在循环中调用 |
| `core/harness.py` | `create_sub_harness()` 继承父 phase（上次 session 已完成） |
| `tests/test_v2_phases.py` | 新增 6 个 TestAutoPhaseTransition 测试 |

### Test Results

```
tests/test_v2_phases.py: 49 passed (原 43 + 新增 6)
tests/test_two_phase_spawn.py: 22 passed
Full suite: 2094 passed, 1 failed (预存 mock 类型错误，无关)
```

---

## Analysis & Next Steps

### What Worked

- **Paper 003 大幅提升** (+20.7% F1): 理论/定量论文特别受益于 `fetch_paper_detail` 和 `verify_citations` 等深度工具的可见性
- **Precision 大幅提升** (paper_003: +28.6%): 深度工具帮助 agent 产出更精确、有据的 findings
- **Recall 全面提升**: 两篇论文的 Recall 都有提升（+11.1% 和 +15.6%）

### Remaining Gap

- **Paper 001 Precision 下降** (-15.6%): agent 产出了一些不够精确的 findings，可能需要更好的 finding quality gate
- **仍有 5/9 gold findings missed** (paper_001): 主要是需要精细数值比对的 findings（appendix 符号错误、表格重复、DID parallel trends 等）
- **Two-phase spawn 的实际效果**: 需要在 verbose 模式确认 spawn 是否在 deep_review 阶段被实际触发

### Recommended Next Steps

1. **增加 verification runs** (n=3-5) 减少 LLM 随机性噪声
2. 分析 paper_001 漏检的 5 条 gold findings 属于什么模式，是否需要专项 sub-agent
3. 确认 two-phase spawn boundary_guard 在完整运行中是否被触发
4. 考虑 DEEP_REVIEW → SYNTHESIS 的自动转换条件
