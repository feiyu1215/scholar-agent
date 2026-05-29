# ScholarAgent → 100% Agent 完整升级计划

> **目标**：从当前"半 Agent"（Harness 框架有了、工具有了，但 Agent 行为大量依赖 prompt 约束而非程序化逻辑）升级到真正的 100% Agent——具备主动目标管理、动态工具选择、持久化规划、自我反思、错误恢复、适应性行为的完全自主系统。
>
> **核心哲学**：We are an AGENT, not a simple script-enriched workflow. Same model, better harness, better results.
>
> **参考来源**：Harness Engineering 系列文章、AGENTS.md 开源规范、多 Agent 协作架构模式全景

---

## 一、当前状态诊断

### 1.1 代码库概览

| 指标 | 数值 |
|------|------|
| 总代码量 | ~9,500 行 Python |
| 注册工具 | 43 个 |
| System Prompt | ~130 行（7 层架构 L1-L7） |
| 模型路由 | 3 层（HIGH/MEDIUM/LOW）+ Provider Affinity |
| 测试文件 | 12 个（76 tests passing） |
| 持久化状态 | revision_state.json + checkpoint + SQLite memory |

### 1.2 已有的 Agent 基础设施

ScholarAgent 已经具备的"Agent 骨架"：

- **Native Function Calling Loop**（main.py:1573-1727）：标准的 model-decides / harness-executes 架构
- **Doom Loop Detection**（utils/doom_loop.py）：Jaccard-based 重复检测，sliding window=8
- **Recall Cache**（utils/recall.py）：tool output 缓存 + 定向失效
- **Two-layer Context Compression**（micro_compact + auto_compact）：40K token 阈值触发
- **Budget-aware Routing**（tools/action_router.py）：Red Lines 硬编码 + budget ceiling
- **Three-tier Model Router**（llm/router.py）：按任务复杂度路由模型 + complexity downgrade
- **Checkpoint/Resume**（utils/checkpoint.py）：长流水线断点续跑
- **SQLite Memory**（utils/memory/store.py）：7 种 MemoryType 的持久化存储
- **Voice Profile**（utils/voice_profile.py）：作者风格量化 + drift detection
- **Quality Gate**（tools/quality_gate.py）：5 维度元评估（ship/deepen/restart）
- **Score Tracker**（utils/score_tracker.py）：修改前后分数追踪

### 1.3 十维度差距分析

从"真正的 Agent 应该怎么做"反向推导，逐维度打分：

| 维度 | 当前分 | 核心缺口 |
|------|--------|---------|
| **D1 主动目标管理** | 4/10 | 目标只在 LLM 脑中，没有层级化目标树；无 re-plan 机制；无主动进度追踪 |
| **D2 动态工具选择** | 3/10 | 43 个工具始终全量暴露给 LLM；无 phase-aware filtering；工具选择完全依赖 prompt 约束 |
| **D3 持久化规划** | 6/10 | revision_state 有 phase 概念，但"计划"本身不持久化；一旦压缩上下文，plan 丢失 |
| **D4 人机协同** | 5/10 | 有 ask_user 工具但无 streaming、无 mid-pipeline pause、无 take-over 模式 |
| **D5 自我反思** | 3/10 | 反思存在于 tool 层（post_edit_verify），但 Agent 层无主动反思；session 结束不保存总结 |
| **D6 适应性行为** | 5/10 | action_router 做了 budget 适应，但无策略级调整（遇到连续失败不会自动切换模式） |
| **D7 主动上下文** | 4/10 | memory recall 存在但未自动注入 agent loop；paper 被 parse 后不会主动建议前置步骤 |
| **D8 错误恢复** | 4/10 | doom loop 是被动防御；无 fallback chain；无模型级联（失败后不会升级模型重试） |
| **D9 输出质量** | 5/10 | 模块级验证存在（post_edit_verify, quality_gate），但无端到端 ship-ready 判断 |
| **D10 长期记忆** | 7/10 | SQLite 存储完善、integration 层有 remember_rewrite/remember_review，但 session 结束不自动保存总结，memory 不自动注入 |

**综合 Agent 成熟度：~4.6/10**

### 1.4 核心问题定性

> **大量 Agent 行为被编码为 prompt 文字约束，而非程序化逻辑。当 LLM "忘了"遵循 prompt 指令时，harness 缺乏 enforcement 能力。**

具体表现：
1. Planning Protocol 写在 prompt 里（L3 §Planning Protocol），但如果 LLM 跳过了，harness 不会阻止
2. Iteration Protocol（max 3 attempts）写在 prompt 里，但 harness 不 track attempt count
3. Quality Gate 的 "deepen" 指令只是建议（prompt 文字），harness 不强制执行
4. Phase transition（review → routing → revising → auditing）由 LLM 隐式推进，revision_state.phase 不被 harness 强制检查
5. Tool ordering（"cheap first, expensive later"）完全靠 prompt 约束

---

## 二、设计理念

### 2.1 Harness Engineering 核心思想

```
AI Agent = SOTA Model + Harness（控制系统）
```

Harness 是 LLM 之外的一切：system prompt、tool 定义、状态管理、路由逻辑、验证回路、错误恢复。改善 Harness 比升级模型成本更低、效果更可控。

### 2.2 Map, not Manual

System Prompt 应该是一张 ~50 行的导航地图，而不是 130 行的操作手册。详细规则应按需加载（progressive disclosure）：

```
System Prompt (50 lines) → "Use read_agent_guidelines(topic) when you need strategy guidance"
                              ↓
                .workspace/guidelines/planning.md
                .workspace/guidelines/deai_strategy.md
                .workspace/guidelines/iteration_protocol.md
                ...
```

### 2.3 Token Pipeline

不是"保留最近 5 个 tool result，其余截断"的粗暴策略，而是：

```
Collect → Sort(by relevance) → Compress → Budget allocate → Assemble
```

每个 tool output 都带 retention_policy（ALWAYS_KEEP / KEEP_UNTIL_SUPERSEDED / COMPRESS_TO_SUMMARY），压缩决策是结构化的。

### 2.4 状态机而非 DAG

ScholarAgent 的流程需要循环和回退：revise → verify → fail → 换策略 → 再 revise。这是状态机，不是 DAG。当前 revision_state.py 的 phase 只支持前进，需要增加回退边。

### 2.5 黑板模式（Blackboard）

`.workspace/` 目录已经事实上是一个黑板（review 写入 issues.json，rewrite 工具从中读取），但没有被显式设计和强化。升级后将 `.workspace/` 正式作为结构化共享状态：
- `plan.json` — 持久化计划
- `strategy_state.json` — 当前 session 策略
- `goal_tree.json` — 层级化目标
- `review/issues.json` — issue 状态
- `revision_state.json` — phase + issue lifecycle

### 2.6 级联模式（Cascade）

不止是工具级 fallback，还包括模型级联：小模型先尝试 → verify 失败 → 升级到更强模型重试。当前 llm/router.py 做一次性路由决策，应改为级联。

### 2.7 评估者-优化者（Evaluator-Optimizer）vs 生成-验证（Generator-Verifier）

当前 post_edit_verify 更像 Generator-Verifier（只说 pass/fail）。应进化为 Evaluator-Optimizer：告诉 Agent **具体哪里退步了、建议怎么改**，让 Agent 收到反馈后可以定向修复，而非盲目重来。

---

## 三、实施计划

### 3.0 总体架构：分 4 波（Wave）推进

```
Wave 1 (基础设施) ← 所有其他维度的依赖
    ├── Infra-A: System Prompt 瘦身 + read_agent_guidelines
    ├── Infra-B: Smart Token Pipeline
    └── Infra-C: 管线原子化

Wave 2 (Agent 核心能力) ← Agent 的"大脑"
    ├── D1: Goal Tracker（带状态机回退）
    ├── D2: Phase-aware Tool Filtering
    └── D3: Plan as Persistent Object

Wave 3 (质量保证层) ← Agent 的"免疫系统"
    ├── D5: Self-reflection Injection
    ├── D8: Fallback Chain + Model Cascade
    └── D9: ship_ready_check + Evaluator-Optimizer Verify

Wave 4 (高级能力) ← Agent 的"成熟度"
    ├── D4: Streaming + Pause/Resume + Take-over
    ├── D6: Strategy Adaptation
    ├── D7: Proactive Context Injection
    └── D10: Memory Deep Integration
```

---

### 3.1 Wave 1: 基础设施层

#### Infra-A: System Prompt → Map, not Manual

**当前问题**：
- SYSTEM_PROMPT_STATIC 130 行，含 DO/DO NOT 各十余条、Planning Protocol、Intent Disambiguation 等
- 这些规则 LLM 不一定每次都遵循，且占用大量 prompt cache 空间

**改造方案**：

1. **瘦身**：System Prompt 保留 ≤50 行核心：
   - L1 身份（3 句）
   - L2 红线（3 条，不可删减）
   - L4 工具原则（6 条核心）
   - L5 沟通（4 条核心）
   - 一句指引：`"Use read_agent_guidelines(topic) for detailed strategy guidance."`

2. **迁移**：所有 DO/DO NOT、Planning Protocol、Budget 细节、Iteration Protocol、Intent Disambiguation → `.workspace/guidelines/` 目录下按 topic 组织：
   - `guidelines/planning.md` — 计划制定规范
   - `guidelines/deai_strategy.md` — 去 AI 策略规则
   - `guidelines/budget_rules.md` — 预算模式行为细则
   - `guidelines/iteration_protocol.md` — 迭代重试规范
   - `guidelines/tool_selection.md` — 工具选择优先级

3. **新工具**：`read_agent_guidelines(topic: str) → str`
   - 按需查阅，topics: "planning", "deai_strategy", "budget_rules", "iteration_protocol", "tool_selection"
   - 实现简单：读 `.workspace/guidelines/{topic}.md` 返回内容

**实现优先级**：HIGH（所有后续改造都受益于更短的 system prompt + 更灵活的规则加载）

**文件变更**：
- `main.py`：重写 SYSTEM_PROMPT_STATIC（缩减到 ~50 行）
- 新增 `guidelines/` 目录 + 5 个 .md 文件
- 新增 `_handle_read_agent_guidelines()` handler
- TOOLS 数组新增一个 tool schema

---

#### Infra-B: Smart Token Pipeline

**当前问题**：
- `micro_compact()`：保留最近 5 个 tool result，其余截断到 100 字预览
- `auto_compact()`：超过 40K tokens 时 LLM 做全文 summary → 只保留 summary
- 没有按**相关性**保留；review_paper 的完整输出（可能 5000+ token）跟 read_section 的小输出同等对待

**改造方案**：

1. **Retention Policy 标签**：每个 tool output 在返回时标注保留策略
   ```python
   TOOL_RETENTION = {
       "review_paper": "ALWAYS_KEEP",           # 核心产出，永不压缩
       "architecture_diagnosis": "ALWAYS_KEEP",
       "read_section": "KEEP_UNTIL_SUPERSEDED",  # rewrite 后旧内容可丢弃
       "deai_detect": "COMPRESS_TO_SUMMARY",     # 保留 {score, signals_count}
       "search_literature": "COMPRESS_TO_SUMMARY",
       "verify_doi": "COMPRESS_TO_SUMMARY",
       "diff_section": "KEEP_UNTIL_SUPERSEDED",
       "default": "COMPRESS_AFTER_N_TURNS",       # 默认保留 3 轮后压缩
   }
   ```

2. **smart_compact(messages, current_context)**：替代原有 micro_compact
   - 输入当前 revision_state.phase + current_section_id
   - 按相关性排序：与当前 phase/section 相关的信息优先保留
   - ALWAYS_KEEP 的永不压缩
   - KEEP_UNTIL_SUPERSEDED 的检查是否已有更新版本
   - COMPRESS_TO_SUMMARY 的提取关键字段

3. **预算分配**：
   ```
   Total Budget: 40K tokens
   ├── system_prompt:   30% (12K) — 50 行 static + dynamic
   ├── recent_context:  40% (16K) — 最近 3-5 轮交互
   ├── preserved_tools: 20% (8K)  — ALWAYS_KEEP 的 tool results
   └── memory_inject:   10% (4K)  — 自动注入的 memory context
   ```

**实现优先级**：HIGH（直接影响所有后续工具的有效上下文）

**文件变更**：
- `main.py`：重写 `micro_compact()` 为 `smart_compact()`，修改 `auto_compact()`
- 新增 TOOL_RETENTION 配置（可放入 config/thresholds.yaml）
- 修改 agent_loop 中的压缩调用逻辑

---

#### Infra-C: 管线原子化

**当前问题**：
- `rewrite_section()`（write_engine.py:45-182）是一个 137 行的 monolith：调 LLM rewrite → 保存 → 跑 deai_audit → post_edit_verify → score_track → memory persist → 返回
- Agent 无法在中间插入决策点（比如看到 rewrite 结果后决定不提交）
- `deai_closed_loop`（deai/fix.py）虽然已有 4 步原子管线替代（deai_pipeline.py），但旧工具仍注册在 TOOLS 中

**改造方案**：

1. **Deprecate `deai_closed_loop`**：
   - 在 TOOLS 的 description 中标记 `[DEPRECATED: Use deai_detect → deai_diagnose → deai_rewrite → deai_verify for Agent control]`
   - 保留 handler 但输出 deprecation warning
   - 后续 Wave 2 的 tool filtering 会将其从可见列表中移除

2. **拆分 `rewrite_section` 为 3 个原子工具**：
   - `generate_rewrite(section_id, issues, strategy, custom_instructions)` → 调 LLM 生成 proposed text + diff，**不保存**。返回 proposed_text + changes_summary + estimated_quality
   - `commit_rewrite(section_id)` → 确认写入 .workspace/revisions/ + 触发 score_track + memory persist + recall invalidation
   - `verify_rewrite_quality(section_id)` → 运行 deai_audit + post_edit_verify + consistency_check → 返回包含**具体反馈**的验收报告（不只是 pass/fail）

3. **Agent 获得的决策点**：
   ```
   generate_rewrite → [Agent 查看 proposed diff] 
                    → commit_rewrite (满意)
                    → 或 generate_rewrite with different strategy (不满意)
                    → 或 ask_user (不确定)
   
   commit_rewrite → verify_rewrite_quality
                  → [Agent 看到具体反馈] 
                  → 下一个 section / 或 re-generate
   ```

**实现优先级**：HIGH（后续 D1/D5/D8 都依赖原子化的决策点）

**文件变更**：
- `tools/write_engine.py`：保留旧 `rewrite_section` 作为 legacy，新增 `generate_rewrite()`, `commit_rewrite()`, `verify_rewrite_quality()`
- `main.py`：TOOLS 新增 3 个 schema，TOOL_HANDLERS 新增 3 个 handler
- `tools/deai/fix.py`：在 closed_loop_fix() 加 deprecation warning

---

### 3.2 Wave 2: Agent 核心能力

#### D1: 主动目标管理 + 状态机回退

**设计**：

新模块 `utils/goal_tracker.py`，实现层级化目标树：

```python
@dataclass
class Goal:
    id: str
    description: str
    success_criteria: str
    status: str  # pending | active | completed | blocked | abandoned
    progress: float  # 0.0 - 1.0
    children: List["Goal"]
    blocked_by: Optional[str]  # 另一个 goal_id
    
@dataclass
class GoalTree:
    root: Goal
    created_at: float
    last_updated: float
```

示例目标树：
```
root: "Make paper publish-ready"  (progress: 35%)
├── "Resolve all major review issues" (progress: 3/7)
│   ├── "Fix Section 4 methodology gap" (status: active)
│   └── "Add missing citations" (status: pending)
├── "Pass De-AI audit for all sections" (progress: 2/6)
└── "Score >= 7.5 in final assessment" (blocked_by: issues)
```

**新工具（3个）**：
- `goal_set(description, success_criteria)` → 建立/更新根目标
- `goal_decompose(goal_id)` → Agent 分解子目标（LLM 辅助）
- `goal_check_progress()` → 返回目标树 + 进度 + 建议 re-plan

**状态机回退**（revision_state.py 增强）：

当前 phase 只有前进路径：`review → routing → revising → auditing → done`

增加回退边：
- `auditing → revising`：审计发现问题，需要回退重写
- `revising → routing`：重写 3 次失败，需要重新路由（换策略/降级为 guidance）
- `done → auditing`：用户要求 re-check

每次回退自动触发 re-plan（如果有持久化 plan 则更新它）。

**Harness 级 enforcement**：
- agent_loop 每 5 轮自动注入：`[SYSTEM: Check goal progress. Current status: {progress_summary}. Decide next action.]`
- 当检测到"用户原始请求已超过 8 轮未被 addressed"时触发强制 re-plan 提醒

**文件变更**：
- 新增 `utils/goal_tracker.py`（~200 行）
- 修改 `tools/revision_state.py`：增加 `transition_phase()` 函数，支持回退边 + 约束检查
- `main.py`：新增 3 个工具 + agent_loop 中的 goal progress injection

---

#### D2: Phase-aware Tool Filtering

**设计**：

新模块 `utils/tool_filter.py`，根据当前 phase 动态生成可用工具子集：

```python
PHASE_TOOL_MAP = {
    "initial": {
        "allow": ["parse_paper", "read_agent_guidelines", "ask_user", "session_status"],
        "deny": ["rewrite_*", "deai_*", "approve_fix", "commit_rewrite"],
    },
    "review": {
        "allow": ["review_paper", "run_single_reviewer", "architecture_diagnosis",
                  "presubmission_check", "consistency_check", "search_literature",
                  "read_section", "read_section_index", "build_voice_profile"],
        "deny": ["rewrite_*", "edit_section", "commit_rewrite"],
    },
    "routing": {
        "allow": ["route_issues", "read_issues", "generate_fix_proposal"],
        "deny": ["review_paper", "rewrite_*"],
    },
    "revising": {
        "allow": ["generate_rewrite", "commit_rewrite", "verify_rewrite_quality",
                  "edit_section", "deai_detect", "deai_diagnose", "deai_rewrite",
                  "deai_verify", "read_section", "diff_section"],
        "deny": ["review_paper"],
    },
    "auditing": {
        "allow": ["deai_detect", "deai_verify", "consistency_check",
                  "verify_rewrite_quality", "ship_ready_check"],
        "deny": ["parse_paper"],
    },
}

# 始终可用的 meta 工具（不受 phase filtering 影响）
ALWAYS_AVAILABLE = [
    "ask_user", "session_status", "goal_check_progress",
    "plan_progress", "read_agent_guidelines", "read_section_index",
    "read_section", "revision_progress", "reflect",
]
```

**Agent Loop 改造**：
```python
# 在 chat_with_tools() 调用前
current_phase = load_state()["phase"]
filtered_tools = filter_tools(TOOLS, current_phase)
response = await client.chat_with_tools(messages=full_messages, tools=filtered_tools, ...)
```

效果：任何给定时刻 LLM 只看到 15-25 个相关工具，而非全部 43+ 个。

**文件变更**：
- 新增 `utils/tool_filter.py`（~80 行）
- 修改 `main.py` agent_loop：在 `chat_with_tools` 前调用 filter

---

#### D3: Plan as Persistent Object

**设计**：

Plan 成为一个可追踪的持久化对象（`.workspace/plan.json`）：

```json
{
  "created_turn": 3,
  "objective": "Full review + revision of methodology & results",
  "steps": [
    {"id": "s1", "action": "architecture_diagnosis", "status": "done", "result_summary": "2 failure modes detected"},
    {"id": "s2", "action": "review_paper(focus: methodology)", "status": "in_progress"},
    {"id": "s3", "action": "route_issues", "status": "pending", "depends_on": "s2"},
    {"id": "s4", "action": "generate_rewrite(section_id=04)", "status": "pending", "depends_on": "s3"}
  ],
  "revision_history": [
    {"turn": 5, "reason": "architecture_diagnosis revealed additional gap", "changes": ["added s1.5"]}
  ]
}
```

**新工具（3个）**：
- `plan_create(objective, steps[])` → 创建并持久化计划
- `plan_progress()` → 返回当前进度 + 下一步建议
- `plan_revise(reason, new_steps[])` → 修正计划 + 记录修正原因

**Harness enforcement**：
- `auto_compact()` 永远不压缩活跃 plan（因为已持久化，可通过 `plan_progress` 回忆）
- 每次 step 完成后，harness 自动更新 plan status
- 新 step 开始时，harness 检查 depends_on 是否满足

**文件变更**：
- 新增 `utils/plan_state.py`（~150 行）
- `main.py`：新增 3 个工具 + handler + auto_compact 保护逻辑

---

### 3.3 Wave 3: 质量保证层

#### D5: Self-reflection Injection

**设计**：

在 Agent Loop 中，当检测到"一个 sub-goal 刚完成"时，自动注入反思 checkpoint：

```python
# agent_loop() 中，在 tool result 返回后
if _is_milestone_completed(tool_name, output):
    reflection_msg = {
        "role": "user",
        "content": "[SYSTEM: Reflection checkpoint. Before proceeding:\n"
                   "(1) Did the last action achieve what you intended?\n"
                   "(2) Are there any unexpected side effects?\n"
                   "(3) Should you adjust your plan?]"
    }
    messages.append(reflection_msg)
```

Milestone 定义（触发反思的条件）：
- `commit_rewrite` 返回成功
- `verify_rewrite_quality` 返回 failed
- `review_paper` 完成
- `ship_ready_check` 执行
- doom_loop 被触发

**Session-end 自动总结**：
```python
# main.py 的 REPL 退出时
async def _session_reflection(history, client):
    summary = await client.chat(
        system="Summarize this session: what was accomplished, what failed, key learnings.",
        user=json.dumps(history[-20:], default=str)[:15000],
        max_tokens=500,
    )
    from utils.memory.integration import save_session_summary
    save_session_summary(summary)
    print(f"\n📝 Session summary saved: {summary[:200]}...")
```

**新工具**：
- `reflect(scope: "last_action" | "session" | "strategy")` → Agent 可主动触发反思

**文件变更**：
- 修改 `main.py` agent_loop：增加 milestone 检测 + reflection injection
- 修改 `main.py` main()：退出前调用 session reflection
- 新增 `_handle_reflect()` handler

---

#### D8: Fallback Chain + Model Cascade

**设计**：

新模块 `utils/fallback_chain.py`：

```python
FALLBACK_CHAINS = {
    "generate_rewrite": [
        {"strategy": "full_rewrite", "model_tier": "high"},
        {"strategy": "sentence_level", "model_tier": "high"},
        {"strategy": "surgical_edit", "model_tier": "high"},
        {"strategy": "full_rewrite", "model_tier": "premium"},  # 模型级联升级
        {"strategy": "guidance_only", "model_tier": None},       # 最终降级
    ],
    "deai_rewrite": [
        {"intensity": "full", "model_tier": "medium"},
        {"intensity": "light", "model_tier": "medium"},
        {"intensity": "full", "model_tier": "high"},            # 模型级联
        {"strategy": "guidance_only", "model_tier": None},
    ],
}
```

**Agent Loop 改造**：

Doom loop detector 升级 → 不只检测重复，还跟踪"同一目标的尝试次数"：

```python
# 当尝试次数达到阈值
if attempts_for_goal >= 2 and not _is_improving(recent_scores):
    next_fallback = get_next_fallback(tool_name, current_strategy)
    inject_msg = (
        f"[SYSTEM: Approach '{current_strategy}' has failed {attempts} times. "
        f"Suggested fallback: {next_fallback['strategy']}. "
        f"Model tier: {next_fallback.get('model_tier', 'same')}]"
    )
    messages.append({"role": "user", "content": inject_msg})
```

**Root Cause Analysis**：

`verify_rewrite_quality` 返回 failure 时，分析失败模式并给出定向建议：
- `voice_drift` → "Use more conservative strategy with stricter voice constraints"
- `ai_regression` → "The rewrite introduced AI patterns. Try sentence-level edits"
- `consistency_break` → "Cross-references broken. Check section_index first"
- `score_plateau` → "Rewrite is not improving. Consider escalating to user"

**模型级联实现**（llm/router.py 增强）：

```python
# 新增 premium tier
MODEL_TIERS["premium"] = os.environ.get("LLM_MODEL_PREMIUM", "claude-sonnet-4-20250514")

def escalate_model(current_tier: str) -> Optional[str]:
    """Get the next tier up. Returns None if already at max."""
    ESCALATION = {"low": "medium", "medium": "high", "high": "premium"}
    return ESCALATION.get(current_tier)
```

**文件变更**：
- 新增 `utils/fallback_chain.py`（~100 行）
- 修改 `llm/router.py`：增加 premium tier + `escalate_model()`
- 修改 `main.py` agent_loop：增加 fallback suggestion injection
- 修改 `tools/deai_pipeline.py` 的 `verify_rewrite()`：增加 root cause analysis 字段

---

#### D9: ship_ready_check + Evaluator-Optimizer Verify

**设计**：

**新工具 `ship_ready_check()`**：Agent 完成所有修改后调用的端到端质量门：

```python
def ship_ready_check() -> dict:
    """Global quality assessment: can this paper be submitted?"""
    results = {}
    
    # 1. All critical issues resolved?
    state = load_state()
    unresolved = [i for i in state["issues"].values() 
                  if i["severity"] in ("major", "critical") and i["status"] != "done"]
    results["critical_issues"] = {"passed": len(unresolved) == 0, "remaining": len(unresolved)}
    
    # 2. De-AI scores across all modified sections
    for section_id in get_modified_sections():
        content = read_section_content(section_id)
        score = quick_deai_score(content)
        results[f"deai_{section_id}"] = {"score": score, "passed": score >= PASS_THRESHOLD}
    
    # 3. Cross-section consistency
    consistency = run_consistency_check()
    results["consistency"] = {"passed": len(consistency) == 0, "issues": consistency}
    
    # 4. Score improvement delta
    delta = get_total_improvement()
    results["improvement"] = {"delta": delta, "passed": delta > 0}
    
    # 5. Verdict
    all_passed = all(v.get("passed", True) for v in results.values())
    results["verdict"] = "SHIP" if all_passed else "NEEDS_WORK"
    results["blockers"] = [k for k, v in results.items() if not v.get("passed", True)]
    
    return results
```

**Evaluator-Optimizer 式 Verify**：

改造 `verify_rewrite_quality()` 输出格式，从 pass/fail → 具体反馈：

```python
# 旧：{"passed": False}
# 新：
{
    "passed": False,
    "regressions": [
        {"type": "ai_regression", "location": "sentence 3", 
         "detail": "Added 'Furthermore' connector (AI pattern)",
         "fix_hint": "Replace with specific logical connective or remove"},
        {"type": "voice_drift", "dimension": "sentence_length",
         "detail": "Average increased from 18 to 28 words",
         "fix_hint": "Break longest sentences, target avg ≤ 22"}
    ],
    "improvements": [
        {"type": "clarity", "location": "paragraph 2", "detail": "Method description now specific"}
    ],
    "net_assessment": "Regression outweighs improvement. Retry with conservative strategy."
}
```

**文件变更**：
- 新增 `tools/ship_ready.py`（~120 行）
- 修改 `tools/write_engine.py` 的 `verify_rewrite_quality()`：增加 regressions/improvements/fix_hint 字段
- `main.py`：新增 ship_ready_check 工具 + handler

---

### 3.4 Wave 4: 高级能力

#### D4: Streaming + Pause/Resume + Take-over

**改造目标**：
- `chat_with_tools()` 改为 streaming 模式（逐 token 输出给用户）
- 新增 `/pause` REPL 命令：设置 `_pause_requested` flag，当前工具执行完后暂停
- 新增 `/resume` REPL 命令：清除 pause flag 继续
- 新增 `/takeover <issue_id>` 命令：标记 issue 为 `manual_handling`，Agent 自动跳过
- 长任务（review_paper, rewrite）定期输出进度行

**实现要点**：
- LLMClient 需要支持 `stream=True` 参数
- pause 检查点在每个 tool 执行前（不中断已执行的工具）
- take-over 更新 revision_state 的 issue status

**文件变更**：
- `llm/client.py`：增加 streaming 支持
- `main.py`：agent_loop 增加 pause check + streaming output
- `main.py` REPL：增加 `/pause`, `/resume`, `/takeover`, `/plan` 命令

---

#### D6: Strategy Adaptation

**设计**：

新模块 `utils/strategy_state.py`：

```python
@dataclass
class SessionStrategy:
    rewrite_mode: str = "standard"        # aggressive | standard | conservative | minimal_edit
    deai_priority: str = "normal"         # high | normal | skip
    reviewer_depth: str = "full_5"        # full_5 | targeted_3 | quick_2
    retry_tolerance: int = 3              # 当前允许重试次数
    model_escalation_enabled: bool = True # 是否允许模型级联

    def adapt(self, signal: str, data: dict) -> str:
        """Based on signals, adjust strategy. Returns description of change."""
        ...
```

**自动适应触发条件**：
- `rewrite_regression` 连续 2 次 → `rewrite_mode = "conservative"`
- `quality_gate_restart` → `reviewer_depth = "full_5"`
- `doom_loop_triggered` → `retry_tolerance -= 1`
- 用户明确表达赶时间 → `reviewer_depth = "quick_2"`, `retry_tolerance = 1`

**新工具**：
- `adjust_strategy(dimension, new_value, reason)` → Agent 主动调整
- `show_strategy()` → 显示当前 session 策略

**文件变更**：
- 新增 `utils/strategy_state.py`（~120 行）
- 修改 `main.py`：strategy 初始化 + adaptation hook

---

#### D7: Proactive Context Injection

**设计**：

Agent Loop 改造——自动在关键时刻注入 context：

1. **Paper parse 后**：自动注入建议
   ```
   [SYSTEM: Paper parsed successfully. Recommended next steps: 
    build_voice_profile (understand author style), 
    architecture_diagnosis (detect structural issues). 
    These are zero-cost and provide critical context for later work.]
   ```

2. **Memory 自动注入**：每次 session 开始时
   ```python
   if paper_parsed:
       context = recall_paper_context(paper_id)  # 之前审过同一篇/同类论文的经验
       field_patterns = recall_field_patterns(paper_field)
       if context:
           inject_at_start(messages, f"[CONTEXT: Prior experience with this paper/field: {context}]")
   ```

3. **不确定性检测**：当 LLM 输出包含不确定性语言
   ```python
   uncertainty_patterns = ["I'm not sure", "可能", "presumably", "it seems", "perhaps"]
   if any(p in content.lower() for p in uncertainty_patterns):
       messages.append({
           "role": "user",
           "content": "[SYSTEM: You expressed uncertainty. Consider using search_literature or read_section to verify before proceeding.]"
       })
   ```

**文件变更**：
- 修改 `main.py` agent_loop：增加 proactive injection hooks
- 修改 `main.py` main()：session 开始时注入 memory context

---

#### D10: Memory Deep Integration

**设计**：

1. **Session start**：自动调用 `recall_paper_context()` + `recall_field_patterns()`，注入 system prompt
2. **Session end**：自动保存 SessionSummary（改造 main() 退出逻辑）
3. **Experience record**：每次 `commit_rewrite` 或 `approve_fix` 后自动触发 `remember_rewrite()` / `remember_review()`（当前已有，确保可靠执行）
4. **Memory consolidation**：每 5 个 session 后自动合并重复 patterns

**新工具**：
- `experience_recall(section_type, task)` → 查阅"上次处理同类 section 的经验"
- `experience_record(outcome, context, lesson)` → 主动记录经验教训

**文件变更**：
- 修改 `utils/memory/integration.py`：增加 `consolidate_memories()`
- 修改 `main.py`：session start/end hooks
- 新增 2 个工具 + handlers

---

## 四、工具数量规划

### 4.1 新增工具清单

| 类别 | 工具名 | 来源 |
|------|--------|------|
| Infrastructure | `read_agent_guidelines` | Infra-A |
| Rewrite（拆分） | `generate_rewrite` | Infra-C |
| Rewrite（拆分） | `commit_rewrite` | Infra-C |
| Rewrite（拆分） | `verify_rewrite_quality` | Infra-C |
| Goal Management | `goal_set` | D1 |
| Goal Management | `goal_decompose` | D1 |
| Goal Management | `goal_check_progress` | D1 |
| Plan State | `plan_create` | D3 |
| Plan State | `plan_progress` | D3 |
| Plan State | `plan_revise` | D3 |
| Reflection | `reflect` | D5 |
| Quality | `ship_ready_check` | D9 |
| Strategy | `adjust_strategy` | D6 |
| Strategy | `show_strategy` | D6 |
| Memory | `experience_recall` | D10 |
| Memory | `experience_record` | D10 |

**新增共 16 个工具**

### 4.2 Deprecated/移除

| 工具 | 处理方式 |
|------|---------|
| `deai_closed_loop` | 标记 deprecated → Wave 2 tool filtering 移除可见性 |
| `classify_intent`* | 已在之前的修复中移除 |
| `estimate_edit_impact`* | 已在之前的修复中移除 |

### 4.3 最终工具数

- 当前：43 个
- 新增：16 个
- 移除：1 个（deai_closed_loop 实质移除）
- **最终约 58 个工具**

但由于 **Phase-aware Filtering（D2）** 的存在，任何给定时刻 LLM 只看到 **15-25 个**相关工具，不会过载。

---

## 五、main.py God File 拆分计划

当前 main.py 1818 行，职责过多。计划拆分为：

```
main.py (入口 + REPL, ~200 行)
├── core/
│   ├── prompts.py        (SYSTEM_PROMPT_STATIC/DYNAMIC, ~60 行)
│   ├── tool_schemas.py   (TOOLS array, ~600 行)
│   ├── agent_loop.py     (agent_loop + compact, ~300 行)
│   └── tool_dispatch.py  (TOOL_HANDLERS dict, ~100 行)
├── handlers/
│   ├── paper_ops.py      (parse, read, diff, index handlers)
│   ├── review_ops.py     (review, route, approve handlers)
│   ├── write_ops.py      (rewrite, edit, commit handlers)
│   ├── deai_ops.py       (deai pipeline handlers)
│   ├── meta_ops.py       (session_status, goal, plan, reflect handlers)
│   └── search_ops.py     (literature, doi, citation handlers)
```

**注意**：这个拆分应在 Wave 1 完成后进行（因为 Wave 1 会修改 prompt 和 compact 逻辑），作为 Wave 1 的收尾工作。

---

## 六、config/thresholds.yaml 补充

当前 thresholds.yaml 覆盖了 deai_engine、post_edit_verify、review_engine、quality_gate。升级后需要补充：

```yaml
# Wave 1 新增
token_pipeline:
  total_budget: 40000
  system_prompt_ratio: 0.30
  recent_context_ratio: 0.40
  preserved_tools_ratio: 0.20
  memory_inject_ratio: 0.10
  compress_after_turns: 3

# Wave 2 新增
goal_tracker:
  progress_check_interval: 5      # 每 N 轮检查一次目标进度
  unaddressed_threshold: 8        # 用户请求超过 N 轮未被处理时触发 re-plan
  max_goal_depth: 3               # 目标树最大深度

# Wave 3 新增
fallback_chain:
  max_attempts_before_escalation: 2
  model_escalation_enabled: true
  final_fallback: "guidance_only"

strategy:
  default_rewrite_mode: "standard"
  default_reviewer_depth: "full_5"
  default_retry_tolerance: 3
  regression_threshold_for_conservative: 2  # 连续 N 次 regression 切换
```

---

## 七、实施顺序与依赖关系

```
Week 1: Wave 1 (Infrastructure)
    Day 1-2: Infra-A (Prompt slim + guidelines)
    Day 3-4: Infra-B (Smart Token Pipeline)
    Day 5:   Infra-C (Pipeline atomization)
    Day 6-7: main.py God File 拆分（可选，如果时间允许）

Week 2: Wave 2 (Agent Core)
    Day 1-2: D1 (Goal Tracker + state machine backtracking)
    Day 3-4: D2 (Phase-aware tool filtering)
    Day 5:   D3 (Plan persistence)
    Day 6-7: Integration testing Wave 1+2

Week 3: Wave 3 (Quality Assurance)
    Day 1-2: D5 (Self-reflection injection)
    Day 3-4: D8 (Fallback chain + model cascade)
    Day 5-6: D9 (ship_ready_check + evaluator-optimizer verify)
    Day 7:   Integration testing Wave 3

Week 4: Wave 4 (Advanced)
    Day 1-2: D6 (Strategy adaptation)
    Day 3:   D7 (Proactive context injection)
    Day 4-5: D10 (Memory deep integration)
    Day 6:   D4 (Streaming + pause/resume — if LLMClient supports)
    Day 7:   Full integration test + 10-dimension re-score
```

---

## 八、之前的计划可以再优化的地方

在本次深度审视中，我发现之前计划有以下可优化的地方（已在本文档中修正）：

### 8.1 被遗漏的点

1. **main.py God File 拆分**：之前计划没有提及 main.py 1818 行的结构性问题。1800 行单文件包含 prompt + schema + handlers + loop + CLI = 不可维护。已补充拆分方案。

2. **hardcoded 值迁移不完整**：TOKEN_THRESHOLD=40000、KEEP_RECENT_TOOL_RESULTS=5、MAX_CONSECUTIVE_DOOM_BLOCKS=3 仍在 main.py 中硬编码。已纳入 Infra-B 的 thresholds.yaml 迁移。

3. **Recall invalidation 不完整**：`deai_closed_loop`（以及未来的 `commit_rewrite`）修改文本后不触发 recall invalidation。已纳入 Infra-C 的改造。

4. **auto_compact 的错误处理**：当 LLM summary 调用失败时，messages 会变成空列表——这是灾难性故障。已纳入 Infra-B 的 fallback 策略。

5. **全局可变状态线程不安全**：`_session_budget`、`_doom_detector`、`_consecutive_doom_blocks` 是全局变量。虽然当前单线程无问题，但 D4 引入 streaming/pause 后需要考虑。已在 D4 中注明。

### 8.2 可以优化的设计决策

1. **工具数量控制**：之前计划预估新增 ~17 个工具（最终 60 个），实际审计后优化为 16 个（最终 58 个）。关键优化是：**不新增 `post_action_verify` 独立工具**——因为 `verify_rewrite_quality` 已经覆盖了这个需求。

2. **D6 策略适应的触发机制**：之前设计为"harness 自动调用 strategy.adapt()"，但这太隐式。优化为：harness 只**建议**调整（通过 inject message），最终由 Agent 决定是否调用 `adjust_strategy`。保持 Agent 的决策权。

3. **D7 不确定性检测的精度**：简单关键词匹配（"maybe", "presumably"）会产生误报。优化为：只在 LLM 的 **text-only response**（非 tool call reasoning）中检测，且连续 2 句不确定性才触发。

4. **Wave 顺序调整**：原计划 D4（Streaming）在 Wave 4。但审视后发现 D4 **依赖 LLMClient 的 streaming 支持**（当前不确定是否有），因此调整为 Wave 4 最后一项，且标记为 "conditional"。

### 8.3 发现的新优化空间

1. **`_handle_consolidate_reviews` 语义问题**（main.py:1338）：这个 handler 简单委托给 `review_paper()`，但名字暗示"只做 consolidation"。应该拆分为独立的 consolidation 逻辑，或者直接移除这个工具（让 Agent 直接调 review_paper）。

2. **`deai_pipeline.py` 的 event loop 管理**（344-374行）：`_get_or_create_event_loop()` 有复杂的 async/sync 边界处理，容易出 bug。Wave 1 应将所有 deai 工具统一为 async handler（agent_loop 已经是 async 的）。

3. **评测覆盖率**：有 12 个 test 文件但只覆盖了 deai_engine 和 review_engine。Goal Tracker、Plan State、Strategy Adaptation 等新模块需要配套的 eval 用例。

4. **Skills 目录的利用率**：`skills/` 目录有 8 个 .md 文件，但 `_load_domain_knowledge()` 的关键词匹配过于粗暴（只匹配 "writing" in "introduction"）。可以用 skill_registry.py 的 frontmatter 做更精准的路由。

---

## 九、成功标准

升级完成后，10 维度重新评分应达到：

| 维度 | 目标分 | 验证方式 |
|------|--------|---------|
| D1 主动目标管理 | 9/10 | Agent 自动建立目标树、追踪进度、在偏离时 re-plan |
| D2 动态工具选择 | 9/10 | 任何 phase 下只暴露相关工具，Agent 不会调不可用工具 |
| D3 持久化规划 | 9/10 | 上下文压缩后 plan 不丢失，可通过 plan_progress 恢复 |
| D4 人机协同 | 7/10 | /pause + /resume + /takeover 工作正常（streaming 视情况） |
| D5 自我反思 | 8/10 | 每个 milestone 后自动反思，session 结束有总结 |
| D6 适应性行为 | 8/10 | 连续失败后自动切换策略，用户可手动调整 |
| D7 主动上下文 | 8/10 | session start 自动注入 memory，不确定时建议验证 |
| D8 错误恢复 | 9/10 | fallback chain 有效，模型级联工作，root cause 分析准确 |
| D9 输出质量 | 9/10 | ship_ready_check 覆盖全部检查项，verify 给具体反馈 |
| D10 长期记忆 | 9/10 | 跨 session 经验积累有效，consolidation 减少冗余 |

**综合目标：9.0+/10**（从当前 4.6/10 → 目标 9.0+/10）

---

## 十、风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| Phase filtering 过于严格，Agent 被卡住 | 中 | ALWAYS_AVAILABLE 列表确保 meta 工具始终可用 + Agent 可调 `ask_user` |
| Reflection injection 过于频繁，浪费 tokens | 低 | 只在 milestone 后触发，非每轮；可配置关闭 |
| Fallback chain 导致无限重试 | 中 | 链有终点（guidance_only）+ 总尝试次数硬上限 |
| main.py 拆分引入 import 循环 | 低 | 采用延迟 import + 依赖注入模式 |
| 58 个工具 schema 仍然太大 | 低 | Phase filtering 确保运行时只暴露 15-25 个 |
| LLMClient 不支持 streaming | 中 | D4 标记为 conditional，其他维度不依赖 streaming |

---

## 十一、附录：关键设计选择记录

### A. 为什么选择状态机而非 DAG？

ScholarAgent 的核心流程（review → revise → verify → possibly re-revise）本质上需要循环。DAG 不允许循环，强行用 DAG 建模会导致需要预先枚举所有可能的循环次数。状态机天然支持"在满足条件前反复执行"。

### B. 为什么不把所有工具合并为更少的"super tool"？

考虑过将 deai_detect + deai_diagnose + deai_rewrite + deai_verify 合并为一个 `deai_pipeline(mode)` 工具。不采用的原因：
1. 违反"Agent 在每步都有决策权"的核心原则
2. 一旦合并，Agent 无法在 detect 后决定"这些 signals 不值得修"
3. 调试时无法看到每步的中间状态

### C. 为什么 Goal Tracker 不用 LLM 自动分解？

`goal_decompose` 是 Agent 主动调用的工具（LLM 辅助分解），而非 harness 自动分解。原因：
1. 自动分解可能产生不合理的子目标
2. Agent 应该在理解 paper 结构后才分解（需要先 parse + architecture_diagnosis）
3. 保持 Agent 的 ownership——目标是它的，它负责分解

### D. 为什么 Reflection 是 injection 而非独立 loop？

考虑过"每 N 轮强制一个 reflection loop（Agent 必须生成 reflection 才能继续）"。不采用的原因：
1. 增加延迟和 token 消耗
2. 模型可能生成 boilerplate reflection（"Everything looks good, continuing."）
3. 选择性 injection（只在 milestone 后）更精准

---

*文档版本：v1.0*
*创建时间：2025-07-17*
*作者：ScholarAgent 升级项目*
