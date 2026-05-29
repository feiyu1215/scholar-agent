# Consolidation Pass & 交叉验证 实施计划

## 目标

解决 Precision 瓶颈：agent 产出大量语义重复的 findings（同一问题不同措辞），导致 Precision 极低。

**量化目标**：
- paper_001 当前：31 predicted, 7 matched, P=0.226, R=0.778, F1=0.350
- paper_003 当前：11 predicted, 6 matched, P=0.545, R=0.600, F1=0.571
- **预期 paper_001 consolidation 后**：~12 predicted, 7 matched, P≈0.583, R=0.778, F1≈0.667
- **预期 aggregate**：F1 从 0.461 提升至 ~0.62+

**根因分析**：
- `check_finding_overlap`（findings.py:314）使用 term-overlap 阈值 ≥70% 做实时去重
- 但语义重复（同一问题不同措辞，term-overlap 仅 20-40%）完全绕过此检查
- paper_001 的 24 个 FP 中，~13 个是语义重复（6-7 个独特问题被重复表述 2-4 遍）
- 子 agent 并行独立运行，各自发现同一问题但无法互相 suppress

**解决方案**：
1. **Consolidation Pass**（后处理）：agent 认知循环结束后，调一次 LLM 做语义合并/去重
2. **交叉验证**（用户可选）：用户 prompt 触发，对 findings 逐条对照原文复核真伪

---

## 已知约束 & 设计决策

| 决策点 | 结论 | 理由 |
|--------|------|------|
| 去重方式 | LLM-based 语义去重（Consolidation Pass） | term-overlap 无法捕获不同措辞的语义重复 |
| 当前 term-overlap (0.70) | **保留**作为快速粗筛 | 拦截完全字面重复，零成本，不损害体验 |
| Consolidation 位置 | agent `LoopDone` 后、`_handle_result` 前 | 不侵入认知循环，不影响子 agent 独立性 |
| Consolidation 模型 | `LLM_MODEL_MEDIUM`（gpt-4.1-mini） | 结构化任务，不需深度推理，成本可控 |
| 交叉验证触发 | 用户通过 prompt 交互触发 | 最灵活，用户自主决定验证深度 |
| 子 agent 独立性 | 维持现状，不共享 findings | 并行天然约束 + anchoring bias 风险 |
| 评估指标 | Consolidation 后用标准 P/R/F1 | 不把 Recall 当 Precision |

---

## 分层策略

### 核心实现（必须做）— ~300 行新代码
- `core/consolidation.py`：LLM-based 语义整合核心逻辑
- `core/agent.py`：集成点（LoopDone 后自动触发）

### 辅助实现（同步做）— ~200 行新代码
- `core/tool_handlers/cross_validation.py`：交叉验证工具
- `core/tools.py` / `core/identity.py`：注册工具 schema

### 无需改动
- `evaluation/run_recall_verification.py`：agent.get_findings() 已自动返回 consolidated 版本
- `evaluation/metrics.py`：指标计算逻辑不变
- `core/tool_handlers/findings.py`：保留原有 term-overlap 粗筛

---

## 模块详细计划

### 模块 1：Consolidation Pass 核心函数（新建）

**文件**: `core/consolidation.py`（新建，预计 ~180 行）

**关注点**:
- [ ] LLM prompt 设计：准确识别语义重复，不过度合并独立问题
- [ ] JSON 输出解析：兜底处理 LLM 输出格式错误
- [ ] 边界条件：findings ≤ 5 条时跳过（不需要去重）
- [ ] 证据合并策略：同组 findings 的 evidence 合并为最完整版本
- [ ] 原始数据保留：consolidation 前的 raw findings 存入 state 备查
- [ ] 成本控制：一次调用，input ~3K tokens，output ~1.5K tokens

**函数签名**:

```python
async def consolidate_findings(
    raw_findings: list[dict],
    paper_context: str,
    client: "LLMClient",
    model: str | None = None,
    min_findings_to_trigger: int = 6,
) -> ConsolidationResult:
    """
    对 raw findings 做 LLM-based 语义整合。

    策略：
    1. 格式化 findings 为编号列表
    2. 调用 LLM 识别语义重复组
    3. 每组合并为一条（保留最详尽表述 + 合并证据）
    4. 返回去重排序后的 findings 列表

    Args:
        raw_findings: agent 产出的原始 findings 列表
        paper_context: 论文摘要 + section 标题（给 LLM 上下文）
        client: LLM 客户端
        model: 模型覆盖（默认用 LLM_MODEL_MEDIUM）
        min_findings_to_trigger: 少于此数量跳过 consolidation

    Returns:
        ConsolidationResult(
            findings=merged_list,
            merge_map={new_idx: [original_indices]},
            raw_count=原始数量,
            consolidated_count=合并后数量,
        )
    """
```

**Prompt 设计**:

```
System:
你是一位资深期刊编辑（Handling Editor）。你收到了多位审稿人对同一篇论文的意见。
你的任务是整合这些意见：合并说同一个问题的条目，删除完全重复的，保留每个独特问题的最佳表述。

规则：
1. 只合并确实在说同一个问题的 findings（即使措辞不同）
2. 两个 findings 讨论同一 section 的不同问题时，不要合并
3. 合并时保留最详尽的表述，补充其他条目中独特的证据
4. 保留原始的 priority 和 status（取组内最高 priority）
5. 按 priority 排序输出：critical > high > medium > low

User:
论文概要：{paper_context}

原始审稿意见（共 {n} 条）：
{formatted_findings}

请输出整合后的 JSON（格式见示例），并在每条中标注 merged_from 字段（原始编号列表）。
```

**输出 JSON schema**:

```json
[
  {
    "finding": "合并后的描述文本",
    "priority": "high",
    "status": "verified",
    "evidence": "合并后的证据",
    "section": "methodology",
    "merged_from": [1, 4, 7]
  }
]
```

**错误处理**:
- JSON parse 失败 → retry 一次（temperature=0.1 → 0.0）
- 第二次仍失败 → 返回原始 findings 不做合并（graceful degradation）
- LLM 调用超时/异常 → 同上，返回原始

**风险评级**: 🟡 中（核心逻辑简单，风险在 LLM 输出不稳定）

---

### 模块 2：Agent 集成（修改现有文件）

**文件**: `core/agent.py`（修改 ~30 行）

**插入点**: `start()` 方法第 298-307 行，`chat()` 方法第 338-347 行

**当前代码**:
```python
# agent.py: start() — L298-307
result = await cognitive_loop(
    messages=self.messages,
    harness=self.harness,
    tools=self.tools,
    client=self.client,
    verbose=self.verbose,
    on_stream=self.on_stream,
)
return self._handle_result(result)
```

**改为**:
```python
result = await cognitive_loop(
    messages=self.messages,
    harness=self.harness,
    tools=self.tools,
    client=self.client,
    verbose=self.verbose,
    on_stream=self.on_stream,
)

# Consolidation Pass: 语义去重合并
if isinstance(result, LoopDone):
    await self._run_consolidation_pass()

return self._handle_result(result)
```

**`_run_consolidation_pass()` 实现**:
```python
async def _run_consolidation_pass(self) -> None:
    """认知循环完成后，对 findings 做 LLM-based 语义合并。"""
    from core.consolidation import consolidate_findings

    raw_findings = self.harness.state.findings
    if len(raw_findings) < 6:
        return  # 数量少，无需合并

    # 构建论文上下文（摘要 + section 列表）
    paper_context = self._build_paper_context_for_consolidation()

    # 获取 MEDIUM tier 模型
    from llm.router import get_model_for_task
    model = get_model_for_task("consolidate")

    result = await consolidate_findings(
        raw_findings=raw_findings,
        paper_context=paper_context,
        client=self.client,
        model=model,
    )

    # 保留原始版本备查
    self.harness.state._raw_findings_pre_consolidation = raw_findings.copy()

    # 替换为合并后的版本
    self.harness.state.findings = result.findings

    if self.verbose:
        import sys
        print(
            f"[Consolidation] {result.raw_count} findings → "
            f"{result.consolidated_count} unique findings",
            file=sys.stderr,
        )
```

**`_build_paper_context_for_consolidation()` 实现**:
```python
def _build_paper_context_for_consolidation(self) -> str:
    """构建给 consolidation LLM 的论文上下文。"""
    parts = []
    state = self.harness.state

    # 摘要（如果有）
    abstract = state.paper_sections.get("abstract", "")
    if abstract:
        parts.append(f"摘要：{abstract[:500]}")

    # Section 列表
    sections = list(state.paper_sections.keys())
    if sections:
        parts.append(f"论文章节：{', '.join(sections)}")

    return "\n".join(parts) if parts else "（无论文上下文）"
```

**同样修改 `chat()` 方法**：chat 中不需要 consolidation（追问时 findings 已是 consolidated 版本）。只在 `start()` 的 LoopDone 分支中触发。

**风险评级**: 🟢 低（插入点明确，不影响现有逻辑流）

**关注点**:
- [ ] `_handle_result` 是同步方法，consolidation 必须在其之前完成
- [ ] `LoopDone` vs `LoopTalk` vs `LoopDoomStop` 的区分：只在 LoopDone 时触发
- [ ] DoomStop 场景：agent 被强制终止时，findings 可能不完整，此时也做 consolidation 是合理的
- [ ] `start()` 和 `chat()` 的差异：start 时做 consolidation，chat 追问时不重做

---

### 模块 3：评估流程集成（验证无需改动）

**文件**: `evaluation/run_recall_verification.py`

**当前代码（L143-166）**:
```python
output = await agent.start(user_intent=user_intent_text)
# ...
raw_findings = agent.get_findings()  # ← 此时已是 consolidated 后的版本
```

**为什么不需要改**:
- Task 2 中 `_run_consolidation_pass()` 在 `start()` 返回前已执行
- `agent.get_findings()` 返回 `self.harness.state.findings`（已被替换为 consolidated 版本）
- 评估脚本自然获得去重后的 findings，无需任何修改

**需要增加的（可选，提升报告信息量）**:
```python
# 在 result dict 构建时增加：
"raw_findings_count": len(agent.harness.state._raw_findings_pre_consolidation or raw_findings),
"consolidated_findings_count": len(raw_findings),
```

**风险评级**: 🟢 低

---

### 模块 4：交叉验证工具（新建）

**文件**: `core/tool_handlers/cross_validation.py`（新建，预计 ~150 行）

**触发方式**: 用户在 `chat()` 中说：
- "帮我验证这些发现" / "验证所有 findings" → 验证全部
- "验证第 3 条和第 7 条" → 验证指定条目
- "这些发现准确吗？" → 验证全部

Agent 在 chat 模式下自然调用此工具（因为工具 schema 描述了其用途）。

**函数签名**:

```python
async def tool_cross_validate(
    args: dict,
    state: Any,
    client: "LLMClient",
) -> str:
    """
    对指定 findings 做交叉验证：对照原文判断每条 finding 的有效性。

    Args (from tool schema):
        finding_indices: list[int] — 要验证的编号（1-based），空=全部
        mode: "full" | "quick" — full=逐条详细验证; quick=批量快速判断

    Returns:
        验证结果的格式化文本
    """
```

**验证 Prompt 设计**:

```
System:
你是论文作者的辩护律师。你的任务是审视每条审稿意见是否合理、是否有充分的原文依据。

对每条意见，请判断：
1. 该意见指出的问题在原文中是否确实存在？请引用具体原文段落。
2. 从原文到"这是一个问题"的推理是否合理？有没有过度解读或误读？

判断结果：
- confirmed: 问题确实存在，推理合理，审稿意见有效
- questionable: 有一定原文依据，但推理有争议或过度解读
- invalid: 原文不支持此结论，审稿意见无效

User:
论文原文（{section}）：
{section_text}

审稿意见：
{finding_text}

证据引用：
{evidence}

请给出判断和理由（JSON 格式）。
```

**输出格式**:
```json
{
  "verdict": "confirmed",
  "confidence": 0.85,
  "reasoning": "原文第 3 段明确使用了线性价格假设...",
  "original_text_quote": "We assume a linear pricing structure..."
}
```

**工具 Schema**:
```python
{
    "name": "cross_validate_findings",
    "description": "对审稿发现做交叉验证——对照论文原文，从作者辩护角度判断每条发现是否有充分依据、推理是否合理。用户要求验证时调用此工具。",
    "input_schema": {
        "type": "object",
        "properties": {
            "finding_indices": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "要验证的 finding 编号列表（从1开始）。留空或不传则验证全部。"
            },
            "mode": {
                "type": "string",
                "enum": ["full", "quick"],
                "default": "full",
                "description": "full=逐条详细验证（每条独立调用 LLM）; quick=批量一次性判断"
            }
        },
        "required": []
    }
}
```

**设计决策**:

| 维度 | 选择 | 理由 |
|------|------|------|
| full 模式成本 | 每条 finding 一次 LLM 调用 | 独立判断更准确，避免批量时后面的 findings 被忽略 |
| quick 模式 | 所有 findings 一次性提交 | 快速粗判，适合用户只想大致了解 |
| 模型选择 | LLM_MODEL_HIGH | 验证需要深度推理能力 |
| 结果展示 | 返回格式化文本表格 | Agent 可直接 talk_to_user |
| 结果持久化 | 更新 finding 的 status/confidence | 验证结果影响后续报告质量 |

**风险评级**: 🟡 中（依赖 section 原文可用性；LLM 判断准确性）

**关注点**:
- [ ] section 原文获取：从 `state.paper_sections` 中按 finding.section 字段定位
- [ ] section 不存在时的 fallback：尝试 fuzzy match 或跳过该 finding
- [ ] 批量验证的 token 成本控制：full 模式 12 条 × ~1K input ≈ 12K tokens
- [ ] 验证结果的可信度：LLM 可能"和稀泥"（对所有 findings 都说 confirmed）

---

### 模块 5：工具注册（修改现有文件）

**文件**: `core/identity.py`（追加 schema）+ `core/tools.py`（注册 phase 可用性）

**改动**:
1. 在 `identity.py` 的工具 schema 列表中追加 `cross_validate_findings` 的 schema
2. 在 `tools.py` 的 phase 配置中，将此工具注册为 all-phase 可用（或仅 completion phase）
3. 在 `core/harness.py` 中添加对应的 thin wrapper `_tool_cross_validate`

**风险评级**: 🟢 低

---

## 执行顺序 & 依赖关系

```
Phase 1 — 核心实现:
  模块 1 (consolidation.py 新建) → 模块 2 (agent.py 集成)

Phase 2 — 验证:
  模块 3 (确认 eval 无需改动) → 跑 paper_001 + paper_003 验证指标

Phase 3 — 交叉验证:
  模块 4 (cross_validation.py) → 模块 5 (工具注册)

Phase 4 — 最终验证:
  端到端测试 + 代码质量检查
```

**Phase 1-2 为高优先级（解决 Precision 问题的核心）。Phase 3 为补充功能。**

---

## 风险 & 缓解

| 风险 | 概率 | 影响 | 缓解方案 |
|------|------|------|---------|
| LLM 过度合并（两个独立问题被合为一条） | 中 | Recall 下降 | prompt 中显式强调"不同问题不合并" + 验证 Recall 不降 |
| LLM 输出 JSON 格式错误 | 低 | consolidation 失败 | 两次 retry + graceful fallback（返回原始 findings） |
| consolidation 后 Recall 意外下降 | 低 | F1 不升反降 | 保留 raw findings 备查 + 回滚机制 |
| 交叉验证 LLM 过于宽松（全部判 confirmed） | 中 | 验证失去意义 | "辩护律师"角色设计 + 要求给出具体原文引用 |
| paper_context 不足导致 LLM 误判 | 低 | 合并错误 | 提供完整 section 列表 + 摘要，不截断 findings 原文 |
| consolidation 增加的延迟 | 低 | 用户体验 | ~2-3 秒额外延迟（一次 LLM 调用），可接受 |

---

## 验收标准

### Consolidation Pass（Task 1-3）

- [ ] paper_001：findings 数量从 31 降至 ≤15
- [ ] paper_001：Precision 从 0.226 提升至 ≥0.45（目标 0.55+）
- [ ] paper_001：Recall 不下降（维持 ≥0.7）
- [ ] paper_003：F1 维持或提升
- [ ] Aggregate F1 ≥ 0.55（baseline 0.461）
- [ ] findings ≤ 5 条时自动跳过，不调用 LLM
- [ ] LLM 调用失败时 graceful fallback，不 crash
- [ ] `_raw_findings_pre_consolidation` 正确保存原始数据

### 交叉验证（Task 4-5）

- [ ] 用户说"验证这些发现"后，agent 自动调用 `cross_validate_findings`
- [ ] full 模式：逐条输出验证结果（verdict + reasoning + 原文引用）
- [ ] quick 模式：批量输出简表
- [ ] section 原文不存在时不 crash（graceful skip）
- [ ] 验证结果更新 finding 的 metadata

---

## 时间预估

| Phase | 预计时间 | 内容 |
|-------|---------|------|
| Phase 1 | 20 分钟 | 实现 `consolidation.py` + `agent.py` 集成 |
| Phase 2 | 15 分钟 | 跑评估验证效果 |
| Phase 3 | 20 分钟 | 实现交叉验证工具 + 注册 |
| Phase 4 | 10 分钟 | 端到端验证 + 代码质量 |

总计约 65 分钟。

---

## 附录：关键代码位置速查

| 组件 | 文件 | 行号 | 作用 |
|------|------|------|------|
| 实时去重（保留） | `core/tool_handlers/findings.py` | L285-354 | term-overlap ≥70% 拦截字面重复 |
| Agent 认知循环 | `core/loop.py` | 全文 | while True + 信号协议 |
| Agent start() | `core/agent.py` | L254-307 | 入口 → loop → handle_result |
| Agent get_findings() | `core/agent.py` | L544-546 | 返回 state.findings |
| LLM 简单调用 | `llm/client.py` | L245-250 | `chat(system, user)` → str |
| Model Router | `llm/router.py` | L22-50 | task → tier → model 映射 |
| 评估 Agent 调用 | `evaluation/run_recall_verification.py` | L97-166 | 初始化 + start + get_findings |
| 评估指标计算 | `evaluation/metrics.py` | L170-253 | match_findings + compute_metrics |
| Gold Standard | `evaluation/gold_standard/gold_paper_*.json` | 全文 | 标准答案 |
| 最新结果 (paper_001) | `evaluation/reports/recall_verification_20260529_030719.json` | 全文 | P=0.226 R=0.778 |
| 最新结果 (paper_003) | `evaluation/reports/recall_verification_20260529_031557.json` | 全文 | P=0.545 R=0.600 |
