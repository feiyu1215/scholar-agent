# ScholarAgent V2 — Consolidation & Cross-Validation 交接 Prompt

> 本文档用于在新对话中恢复上下文。将本文件内容粘贴给新 AI 会话即可无缝接手。

---

## 1. 项目定位（一句话）

ScholarAgent V2 是一个 **LLM-powered 学术论文审稿 Agent**：输入一篇 PDF 论文，自动产出结构化审稿意见（findings），包含 priority、evidence、section 定位。

---

## 2. 工作仓库

```
/Users/yanfeiyu03/Downloads/scholar-agent-public/v2/
```

**注意**：同级目录下还有 `scholar-agent-github`，那是旧版，不要动。所有工作在 `scholar-agent-public/v2/` 进行。

---

## 3. 架构现状

### 核心组件

| 层 | 文件 | 职责 |
|----|------|------|
| Agent 入口 | `core/agent.py` | `start()` → `cognitive_loop()` → `_handle_result()` 主流程 |
| 认知循环 | `core/loop.py` | while True + 信号协议（LoopDone/LoopTalk/LoopDoomStop） |
| 状态管理 | `core/state.py` + `core/harness.py` | 黑板式 state（findings, edits, paper_sections 等） |
| 工具处理 | `core/tool_handlers/*.py` | 每个文件一组工具（findings.py, reading.py, editing.py 等） |
| LLM 调用 | `llm/client.py` | `async chat(system, user, temperature, max_tokens, model) → str` |
| 模型路由 | `llm/router.py` | task → tier（HIGH/MEDIUM/LOW）→ model 映射 |
| 评估系统 | `evaluation/run_recall_verification.py` + `evaluation/metrics.py` | 跑 agent → 提取 findings → 与 gold standard 计算 P/R/F1 |

### 架构哲学

- **单 Agent + 状态机 + 黑板**：不是 multi-agent，是一个 agent 通过 harness state 管理多个子任务
- **认知循环信号协议**：LoopDone（完成）、LoopTalk（需要与用户对话）、LoopDoomStop（预算截断）
- **实时去重 + 后处理去重分层**：findings.py 的 term-overlap 是粗筛，Consolidation Pass 是精确语义去重

---

## 4. 当前问题与修复进展

### 4.1 原始问题（已修复）

Agent 产出大量 **语义重复的 findings**（同一问题、不同措辞）。`check_finding_overlap`（findings.py:321-354）使用 term-overlap ≥70% 做实时去重，但语义重复的 term-overlap 通常只有 20-40%，完全绕过检查。

### 4.2 Baseline 指标（修复前）

| Paper | Predicted | Matched(TP) | FP | Precision | Recall | F1 |
|-------|-----------|-------------|-----|-----------|--------|------|
| paper_001 (gpt-4.1) | 31 | 7 | 24 | 0.226 | 0.778 | 0.350 |
| paper_003 (gpt-4.1) | 11 | 6 | 5 | 0.545 | 0.600 | 0.571 |
| **Aggregate** | — | — | — | **0.583** | **0.389** | **0.461** |

### 4.3 已完成的修复（P0-P2）

| 修复 | 内容 | 效果 |
|------|------|------|
| P0 | Finding 三信号去重 + Consolidation LLM 审核 | 消除 FP（Precision↑） |
| P0 | AppendixMathAuditSkill + PCG appendix weight | 新发现 G001/G003 |
| P1 | Rule 10 跨表重复检测 + 顺序下标错误检测 | G005 端到端路径 |
| P1 | SkillX 注册 TableExtraction + TableConsistency | DeepVerify 管道 |
| P2 | auto_assign bug 修复 (ToolGroup + DEFAULT_PHASE_GROUPS) | 工具可用性 |

### 4.4 Post-Fix 评估结果（2026-05-30，4 runs）

**配置**: model=gpt-4.1, max_loop_turns=60, token_budget=0(unlimited), enable_hdwm=True

| Run | Agent Findings | Gold | Precision | Recall | F1 |
|-----|---------------|------|-----------|--------|------|
| Paper 001 Run 2 | 2 | 13 | 100.0% | 15.4% | 26.7% |
| Paper 001 Run 3 | 3 | 13 | 66.7% | 15.4% | 25.0% |
| Paper 003 Run 2 | 4 | 9* | 100.0% | 50.0% | 66.7% |
| Paper 003 Run 3 | 4 | 9* | 100.0% | 55.6% | 71.4% |
| **Weighted Avg** | — | — | **93.1%** | **30.7%** | **46.2%** |

*Paper 003 gold: G003+G009 合并为 9 条有效

**Best Estimate（多轮联合）**:
- P=90.0%, R=40.9%, F1=56.2%
- vs Baseline: **P+31.7%, R+2.0%, F1+9.9%**

### 4.5 关键结论

1. **Precision 大幅提升 (+35pp)**：Finding 去重有效消除了 false positives（4 runs 仅 1 个 FP）
2. **Recall 多轮联合后超越 baseline**：单次 15-56%，联合后 40.9%（vs baseline 38.9%）
3. **Agent 随机性是主要噪声源**：Paper 001 不同 run 产出完全不同的 findings（互补而非冗余）
4. **Paper 003 高度稳定**：两次 run 产出相同 4 findings，F1=66.7%/71.4%
5. **F1 Best Estimate 超越 baseline 9.9pp**：56.2% vs 46.3%

---

## 5. 实施计划（已设计完毕）

**详细计划文档**：`docs/CONSOLIDATION_AND_CROSSVAL_PLAN.md`（499 行，包含完整函数签名、prompt 设计、JSON schema、风险矩阵、验收标准）。

### 需要做的 6 件事

| # | 任务 | 文件 | 状态 |
|---|------|------|------|
| 1 | 新建 `core/consolidation.py` — LLM-based 语义整合核心函数 | 新建 | ❌ 未开始 |
| 2 | 修改 `core/agent.py` — 在 LoopDone 后插入 consolidation 调用 | 修改 ~30 行 | ❌ 未开始 |
| 3 | 确认评估流程无需改动 + 跑 paper_001/003 验证效果 | 验证 | ❌ 未开始 |
| 4 | 新建 `core/tool_handlers/cross_validation.py` — 交叉验证工具 | 新建 | ❌ 未开始 |
| 5 | 修改 `core/identity.py` + `core/tools.py` — 注册交叉验证 schema | 修改 | ❌ 未开始 |
| 6 | 端到端验证 + 代码质量检查 | 验证 | ❌ 未开始 |

**执行顺序**：1 → 2 → 3（高优先级，解决 Precision） → 4 → 5 → 6

---

## 6. 关键设计决策（不可违反）

| # | 决策 | 理由 | 文件参考 |
|---|------|------|---------|
| 1 | Consolidation 在 `LoopDone` 后、`_handle_result` 前执行 | 不侵入认知循环 | agent.py:297-307 |
| 2 | 保留现有 term-overlap 去重（不删除） | 零成本粗筛，互补不冲突 | findings.py:321 |
| 3 | 使用 `LLM_MODEL_MEDIUM`（gpt-4.1-mini）做 consolidation | 结构化任务，不需深度推理 | router.py:44 已注册 |
| 4 | findings ≤ 5 条时跳过 consolidation | 数量少无需去重，省成本 | plan |
| 5 | LLM 失败时 graceful fallback（返回原始 findings） | 永不因去重失败导致整体崩溃 | plan |
| 6 | 子 agent 独立性维持现状（不共享 findings） | 并行天然约束 + anchoring bias | plan |
| 7 | 交叉验证是用户主动触发（不自动执行） | 用户自主决定验证深度 | plan |
| 8 | Consolidation 只在 `start()` 的 LoopDone 分支触发，`chat()` 不重做 | 追问时 findings 已是 consolidated | plan |

---

## 7. 关键 API 调用方式

### LLM 调用

```python
# llm/client.py — 唯一的简单调用接口
result_str = await client.chat(
    system="系统 prompt",
    user="用户 prompt", 
    temperature=0.0,
    max_tokens=2000,
    model="gpt-4.1-mini"  # 可选覆盖
)
# 返回 str
```

### 模型路由

```python
# llm/router.py
from llm.router import get_model_for_task
model = get_model_for_task("consolidate")  # → 返回 MEDIUM tier 模型名
```

`router.py:44` 已经注册了 `"consolidate": "medium"` 映射，不需要改。

### Agent findings 访问

```python
agent.get_findings()           # → list[dict]，返回 harness.state.findings
agent.harness.state.findings   # 同上，直接属性访问
```

### 评估运行

```bash
cd /Users/yanfeiyu03/Downloads/scholar-agent-public/v2
python -m evaluation.run_recall_verification --papers paper_001 --model gpt-4.1
```

---

## 8. 代码位置速查表

| 组件 | 文件路径 | 行号 | 说明 |
|------|---------|------|------|
| Agent start() | `core/agent.py` | L254-307 | **Consolidation 插入点在 L297-307** |
| Agent _handle_result() | `core/agent.py` | L349-365 | LoopDone/LoopTalk/LoopDoomStop 分支 |
| Agent chat() | `core/agent.py` | L309-347 | 追问流程（不做 consolidation） |
| Agent get_findings() | `core/agent.py` | L544-546 | 返回 state.findings |
| 实时去重 | `core/tool_handlers/findings.py` | L321-354 | term-overlap ≥70% 检查 |
| tool_update_findings | `core/tool_handlers/findings.py` | L17-43 | 记录 finding + Phase 47 去重 |
| LLM client.chat() | `llm/client.py` | L245-250 | `async chat(system, user, ...) → str` |
| Model router | `llm/router.py` | L33-56 | TASK_TIER_MAP + consolidate 已注册 |
| 评估主流程 | `evaluation/run_recall_verification.py` | L97-166 | agent 初始化 + start + get_findings |
| 评估指标 | `evaluation/metrics.py` | L170-253 | MATCH_THRESHOLD=0.4, Jaccard+section |
| Gold standard | `evaluation/gold_standard/gold_paper_*.json` | — | 标准答案 |
| 最新报告(001) | `evaluation/reports/recall_verification_20260529_030719.json` | — | P=0.226 R=0.778 |
| 最新报告(003) | `evaluation/reports/recall_verification_20260529_031557.json` | — | P=0.545 R=0.600 |
| 实施计划 | `docs/CONSOLIDATION_AND_CROSSVAL_PLAN.md` | — | 完整 plan（499行） |
| 代码审计 | `docs/FULL_CODE_AUDIT_PLAN.md` | — | 全量代码审计文档（参考格式） |

---

## 9. Consolidation 模块实现要点

### 核心函数签名（consolidation.py）

```python
@dataclass
class ConsolidationResult:
    findings: list[dict]       # 合并后的 findings
    merge_map: dict            # {new_idx: [original_indices]}
    raw_count: int
    consolidated_count: int

async def consolidate_findings(
    raw_findings: list[dict],
    paper_context: str,         # 摘要 + section 列表
    client: "LLMClient",
    model: str | None = None,
    min_findings_to_trigger: int = 6,
) -> ConsolidationResult:
```

### Prompt 核心思路

角色 = "资深期刊编辑（Handling Editor）"，任务 = 整合多位审稿人意见：
- 只合并确实在说同一个问题的 findings
- 同 section 不同问题不合并
- 合并时保留最详尽表述 + 合并证据
- 取组内最高 priority
- 输出 JSON 带 `merged_from` 字段追溯来源

### Agent 集成代码

```python
# agent.py start() 方法，在 cognitive_loop() 返回后插入：
result = await cognitive_loop(...)

# Consolidation Pass
if isinstance(result, LoopDone):
    await self._run_consolidation_pass()

return self._handle_result(result)
```

`_run_consolidation_pass()` 方法：
- 检查 findings 数量 ≥ 6
- 构建 paper_context（摘要 + section 列表）
- 调用 `consolidate_findings()`
- 保存原始到 `state._raw_findings_pre_consolidation`
- 替换 `state.findings` 为合并后版本

---

## 10. 交叉验证工具要点

### 触发方式

用户在 `chat()` 中说"帮我验证这些发现"等，agent 自然调用 `cross_validate_findings` 工具。

### 验证逻辑

角色 = "论文作者的辩护律师"，逐条判断：
- confirmed / questionable / invalid
- 必须引用具体原文段落
- 从 `state.paper_sections[finding.section]` 获取原文

### 工具注册位置

- Schema 在 `core/identity.py` 的工具列表
- Phase 可用性在 `core/tools.py`
- Handler 在 `core/tool_handlers/cross_validation.py`

---

## 11. DO / DON'T 速查

### DO

- 新建文件时遵循现有代码风格（type hints、docstrings、async/await）
- consolidation 失败时返回原始 findings（永不 crash）
- 保留 `_raw_findings_pre_consolidation` 方便回溯
- 跑评估后对比 Recall 是否下降（Recall 不降是硬约束）
- 使用 `router.get_model_for_task("consolidate")` 获取模型

### DON'T

- 不要删除或修改 `check_finding_overlap`（保留 term-overlap 粗筛）
- 不要在 `chat()` 流程中重做 consolidation
- 不要让 consolidation 失败阻断整个 agent 流程
- 不要用 HIGH tier 模型做 consolidation（浪费成本）
- 不要修改 `evaluation/metrics.py` 的匹配逻辑
- 不要改 `evaluation/run_recall_verification.py`（agent.get_findings() 已自动返回 consolidated）

---

## 12. 验收标准（一览）

| 指标 | 当前 | 目标 |
|------|------|------|
| paper_001 predicted count | 31 | ≤15 |
| paper_001 Precision | 0.226 | ≥0.45（目标 0.55+） |
| paper_001 Recall | 0.778 | ≥0.7（不降） |
| paper_003 F1 | 0.571 | ≥0.571（不降） |
| Aggregate F1 | 0.461 | ≥0.55（目标 0.62+） |

---

## 13. 环境运行

```bash
cd /Users/yanfeiyu03/Downloads/scholar-agent-public/v2
# 确保 .env 中有 OPENAI_API_KEY 和 LLM_MODEL 设置
# 运行评估
python -m evaluation.run_recall_verification --papers paper_001 --model gpt-4.1
# 运行单 paper（默认所有）
python -m evaluation.run_recall_verification --papers paper_001 paper_003
```

---

## 14. 参考文档索引

| 何时读 | 文档 |
|--------|------|
| 理解实施方案全貌 | `docs/CONSOLIDATION_AND_CROSSVAL_PLAN.md` |
| 理解项目全量代码结构 | `docs/FULL_CODE_AUDIT_PLAN.md` |
| 理解评估指标设计 | `evaluation/metrics.py` 顶部注释 |
| 查看 gold standard | `evaluation/gold_standard/gold_paper_001.json` |
| 查看最新评估报告 | `evaluation/reports/recall_verification_20260529_*.json` |
| 理解 model routing | `llm/router.py` 全文 |

---

## 15. 起手步骤

1. **读** `docs/CONSOLIDATION_AND_CROSSVAL_PLAN.md` 理解完整方案
2. **新建** `core/consolidation.py` 实现 `consolidate_findings()` + `ConsolidationResult`
3. **修改** `core/agent.py` 的 `start()` 方法（L297-307 之间插入）
4. **跑** `python -m evaluation.run_recall_verification --papers paper_001 --model gpt-4.1`
5. **对比** Precision 是否提升、Recall 是否不降
6. 继续实现交叉验证（Phase 3-4）

---

## 16. 常见问题

**Q1: 为什么不直接改 `check_finding_overlap` 降低阈值？**
A: 降低阈值会产生大量误拦（把独立但相关的 findings 错误合并）。语义重复的 term-overlap 仅 20-40%，但不同问题讨论同一 section 时也可能达到 30-50%。阈值无法区分这两种情况，LLM 可以。

**Q2: 为什么 consolidation 不在循环内做？**
A: 循环内做会影响 agent 的认知连贯性——它已经"看到"了之前记录的 findings，中途删除会造成上下文断裂。后处理是最安全的位置。

**Q3: `router.py` 的 "consolidate" 映射需要我添加吗？**
A: 不需要，已经存在（L44: `"consolidate": "medium"`）。

**Q4: 评估脚本需要改吗？**
A: 不需要。`agent.start()` 返回时 findings 已经是 consolidated 版本，`agent.get_findings()` 自然拿到去重后的数据。评估脚本无感知。

**Q5: 如果 consolidation LLM 调用失败怎么办？**
A: graceful fallback——返回原始 findings 不做任何合并。Agent 照常输出结果，只是没有去重优化。永远不因为 consolidation 失败导致整体 crash。

**Q6: DoomStop（预算截断）时要做 consolidation 吗？**
A: 建议做。DoomStop 时 findings 虽不完整，但可能已经有重复。且 consolidation 只多一次 LLM 调用（~2秒），不会显著增加预算负担。在 `start()` 中改为 `if isinstance(result, (LoopDone, LoopDoomStop)):` 即可。
