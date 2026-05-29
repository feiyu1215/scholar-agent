# MetaCognitionLayer 设计方案

**Status**: Proposal  
**Date**: 2026-05-28  
**Author**: CatDesk + yanfeiyu03  
**Priority**: P1  

---

## 1. 问题诊断

### 1.1 当前催促机制的失效

从 verbose 运行的直接证据：

```
Turn 6: [Signal] [多视角 Spawn 建议] 你已进入 DEEP_REVIEW 初段（6/40 轮）...
Turn 7: Agent 直接调用 mark_complete，忽略 spawn 建议
```

**根因**：当前 spawn nudge 是一条低优先级（priority=4）的模板化 system message，没有信息增量。Agent 面对"你应该 spawn"这种泛泛建议时，因为已经有了 7 条 findings 的"安全感"，选择忽略。

**类比**：这就像老板对员工说"你最好再检查一下"——如果员工觉得自己已经做得够好了，这句话毫无约束力。但如果老板说"你的第三个结论好像和表 5 的数据矛盾"——这是具体的、有信息含量的质疑，员工不得不回应。

### 1.2 产品定位约束

用户明确：**Precision > Recall**（"找出来的一定是错误的"）

这意味着催促器的设计目标不是"催产出更多 findings"，而是：
1. 补充主 Agent 的盲点（提 Recall，but only if the finding is solid）
2. 质疑 Agent 现有 findings 的证据强度（保 Precision）
3. 阻止低质量 findings 入库（Precision gate）

---

## 2. 设计方案：MetaCognitionLayer

### 2.1 核心理念

> "得到用户提醒，LLM 就去额外看看" — 用户原话

MetaCognitionLayer（下称 MCL）是一个**轻量 LLM 反馈层**，角色相当于：
- 对主 Agent：像一个"共同审稿人"给反馈（不是老板，是 peer）
- 对系统：兼具 phase 推进 + 视角补充 + 质量门控

### 2.2 架构

```
┌─────────────────────────────────────────────┐
│              Cognitive Loop                   │
│                                              │
│  ┌─────────┐    ┌──────────────────────┐     │
│  │  Main   │    │  MetaCognitionLayer  │     │
│  │  Agent  │◄───│  (gpt-4o-mini)       │     │
│  │(gpt-4.1)│    │                      │     │
│  └────┬────┘    └──────────┬───────────┘     │
│       │                    │                 │
│       ▼                    │ 每 N 轮触发     │
│  [Findings]◄───────────────┘                 │
│  [Sections]                                  │
│  [Tool History]                              │
└─────────────────────────────────────────────┘
```

### 2.3 触发时机

MCL 不是每轮都跑（太贵），而是在**关键节点**触发：

| 触发条件 | 时机 | MCL 角色 |
|----------|------|---------|
| 进入 deep_review 后首次 | ~Turn 2-3 | **视角建议**：论文特征 → 推荐审视角度 |
| 累积 3+ findings | ~Turn 5-6 | **质量审计**：检查 findings 的证据强度 |
| Agent 调用 mark_complete | 任何时候 | **终止门控**：是否真的覆盖够了？ |
| 连续 3 轮无新 finding | 中后期 | **盲点提醒**：你可能遗漏了什么 |

### 2.4 MCL Prompt 模板

```python
MCL_SYSTEM_PROMPT = """
你是一个学术审稿的 meta-cognition 层。你的任务是审视另一个 AI 审稿人的工作状态，
给出具体的、有信息含量的反馈。

你 **不** 直接审稿。你审视审稿人的工作质量和覆盖度。

你的反馈必须满足：
1. 具体（"你的 finding #3 的证据链缺少原文引用"，而非"建议你检查一下"）
2. 可操作（告诉审稿人下一步该做什么）
3. 有节制（不要给超过 3 条建议，优先级排序）

你关注三个维度：
- 精确性：现有 findings 的证据是否足够强？有没有可能是审稿人误判？
- 覆盖度：论文的哪些关键部分还没有被审视？
- 深度：哪些 findings 停留在表面，需要更深入验证？
"""

MCL_USER_TEMPLATE = """
## 论文信息
标题: {paper_title}
已读 Sections: {sections_read}
总 Sections: {total_sections}

## 当前 Findings ({findings_count} 条)
{findings_summary}

## 审稿人行为轨迹
已用 {turns_used}/{max_turns} 轮
最近 3 轮动作: {recent_actions}
spawn 次数: {spawn_count}
外部文献查询次数: {search_count}

## 请给出你的反馈（最多 3 条，按优先级排序）
格式：
1. [类型: 精确性/覆盖度/深度] 具体反馈内容
"""
```

### 2.5 MCL 输出处理

MCL 的输出不是直接注入 system message（那样还是会被忽略），而是：

**方案 1（轻量）**：MCL 输出作为 `mark_complete` 的前置条件
- 当 Agent 调用 `mark_complete` 时，先触发 MCL
- MCL 如果给出"精确性"或"覆盖度"方面的反馈，则阻止 mark_complete 并将反馈作为 tool response 返回
- Agent 必须 address MCL 的反馈后才能再次 mark_complete

**方案 2（中等）**：MCL 输出作为高优先级 assistant message
- MCL 的反馈以 assistant 角色注入（而非 system），模拟"同事的直接对话"
- 在 LLM 视角下，这等同于"有人直接对我说话"，响应率远高于 system hint

**方案 3（重量）**：MCL 自动执行 spawn
- MCL 判断需要多视角时，直接调用 `_run_parallel_perspectives()` 而不需要 Agent 调用 spawn 工具
- Agent 在下一轮看到的是已完成的 spawn 结果，而非一个建议

**推荐**：方案 1 + 方案 3 的组合
- mark_complete 门控（精确性保障）
- 自动 spawn（覆盖度保障）
- 保持每 N 轮的轻量反馈（深度引导）

### 2.6 成本估算

| 模型 | 每次 MCL 调用 tokens | 调用次数/篇论文 | 成本增加 |
|------|---------------------|----------------|---------|
| gpt-4o-mini | ~1500 input + ~300 output | 3-4 次 | ~$0.003/篇 |
| gpt-4.1 主循环 | ~150k total | 1 次 | ~$3/篇 |

MCL 增加的成本 **< 0.1%**，可以忽略。

### 2.7 与现有机制的关系

| 现有机制 | MCL 后的角色变化 |
|----------|----------------|
| Auto Phase Transition | 保持不变（硬阈值静默转换） |
| Spawn Nudge (priority=4) | **替换为 MCL 自动 spawn** |
| Methodology Nudge | **融入 MCL 的"覆盖度"维度** |
| Quality Check (mark_complete 后) | **前移到 mark_complete 前（MCL 门控）** |
| Reflection Nudge | 保持（轻量级，不冲突） |

---

## 3. 实现计划

### Phase 1: Hotfix（今天可做，验证 spawn 增益）

改动最小，验证假设：**"spawn 本身能提升多少？"**

```python
# loop.py: mark_complete 前置条件
if tool_name == "mark_complete" and state.spawn_count == 0:
    # 如果从未 spawn 过，自动执行一次 role-based spawn
    spawn_plan = _build_role_based_spawn_plan(state)
    if spawn_plan:
        # 直接执行而非建议
        await _run_parallel_perspectives(harness, spawn_plan[:3])
        # 让 Agent 看到 spawn 结果后再决定是否 mark_complete
        return "已为你执行多视角审视，请查看子视角的发现后再决定是否结束。"
```

### Phase 2: MCL Core（1-2 天）

1. 新增 `core/meta_cognition.py` 模块
2. 实现 MCL 的三个触发点
3. 实现 mark_complete 门控
4. 实现自动 spawn 路径（bypass Agent）

### Phase 3: 评估口径修正

1. 过滤正面观察（severity=="none"）
2. 实现 soft precision（一对多匹配）
3. 分层评估：确定性错误 vs 方法论质疑

---

## 4. 风险与缓解

| 风险 | 严重度 | 缓解 |
|------|--------|------|
| MCL 产出低质量反馈 → Agent 困惑 | 中 | MCL 的 prompt 需要精心设计；保守策略：MCL 只能"阻止"不能"指令" |
| 自动 spawn 对简单论文过度 → 浪费 token | 低 | MCL 先判断"是否需要 spawn"，而非无条件执行 |
| MCL 与主 Agent 的 context 不一致 | 中 | MCL 只接收 state summary，不接收完整 context |
| 过度门控导致 Agent 循环无法终止 | 高 | MCL 最多阻止 mark_complete 1 次，第二次无条件放行 |

---

## 5. 预期效果

基于今天的诊断：

- **Phase transition 已验证有效**：auto transition 生效后 F1 从 42.6% → 62.5%（单次最优）
- **Spawn 从未被实际验证**：如果 spawn 真的能补充 2-3 条新视角，且精度不低于当前，Recall 可能再提 10-15%
- **质量门控**：过滤掉正面观察类的低质量 findings，Precision 可从 71.4% → ~85%+

保守估计的 post-MCL 目标：**P≥80%, R≥60%, F1≥70%**

---

## 6. 用户原话 → 设计映射

| 用户说的 | 设计中如何体现 |
|----------|--------------|
| "催促器本身应该是 LLM 反馈" | MCL 用 gpt-4o-mini 做智能反馈，不是 if/else |
| "告诉主 Agent 这个可能没做" | MCL 的"覆盖度"维度：具体指出未审视的 section/角度 |
| "得到提醒，LLM 就去额外看看" | MCL 反馈以 tool response 形式返回，Agent 必须面对 |
| "多方面审稿，补充视角" | MCL 判断需要多视角时，自动触发 spawn |
| "小模型做提醒的交互" | MCL 用 gpt-4o-mini，成本<0.1% |
| "兼顾之前的能力" | phase transition 保持、methodology nudge 融入 MCL |
| "找出来的一定是错误的" | MCL 的 mark_complete 门控：审计 findings 精确性 |
| "gold 之外可能还存在更多问题" | Gold Standard 审计确认：FP 中多数实际有效 |
