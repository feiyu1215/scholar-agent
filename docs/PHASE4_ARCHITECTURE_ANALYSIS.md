# Phase 4 架构深度分析 — loop ↔ harness 解耦 & Signal 结构化

**创建日期**: 2025-06-01  
**分析方法**: serious-mode / fundamental-thinking / rational-skepticism 三框架联合审视  
**结论**: REFACTOR_PLAN.md 中 Phase 4 的原方案需要重新设计。本文给出基于代码实际状态的替代方案。

---

## 一、背景：REFACTOR_PLAN.md Phase 4 原方案

原计划分两部分：

- **4-1 LoopContext Protocol**：定义 `LoopContext(Protocol)` → harness 实现 → loop 改为接收 `ctx: LoopContext`
- **4-2 Signal Structured Output**：把信号变成 `{"name": "signal_done", "arguments": {...}}` 格式的独立 tool call

---

## 二、根本性思考：三层审视

### 第一层：合法性检验 — 这两件事该做吗？

#### 4-1 LoopContext Protocol — 问题是否真实存在？

**当前现状**（基于代码精确分析）：

loop.py 与 harness 之间有 6 处直接 state 修改（绕过方法），约 25 处 state 属性读取，约 12 处 hasattr/getattr 防御性检查。

**理性怀疑：这个耦合当前造成了什么实际问题？**

诚实回答：**目前没有 bug 是因为这个耦合导致的**。整个系统能跑通 e2e，F1 达到 0.4。耦合是"代码异味"但不是"功能障碍"。

**那为什么还要做？** 真实的 engineering justification：

1. **可测试性**：loop.py 目前无法被独立单测——因为它需要一个完整的 Harness 实例。如果有 Protocol，可以 mock 出 LoopContext。
2. **可换性**：如果将来要支持非 paper-review 的 task（比如 code review），需要一个不同的 harness。Protocol 允许这种替换。
3. **防劣化**：6 处直接 state 修改意味着新增 state 字段时，可能忘记在 loop 中更新——没有编译期保护。

**结论**：4-1 合法，但不紧急。它的价值主要是"可测试性"和"可扩展性"，而非修复当前 bug。

#### 4-2 Signal Structured Output — 问题是否真实存在？

**关键发现：信号已经是通过 tool call 触发的！**

LLM 调用 `done`/`switch_persona` 等工具 → handler 返回字符串信号 → loop 再解析。这是一个"双重间接"。

问题出在"双重间接"的代价：

1. handler 将结构化数据**序列化成字符串** → loop 再**反序列化回来**。多了一层无意义的 string round-trip。
2. `_handle_model_signal` 存在冗余解析 bug（handler 已经解析了一次，这个函数又解析了一次）。
3. `__DONE__xyz`（无 `|` 分隔符）会被误识为信号——虽然当前不太可能触发，但是一个潜在隐患。

**但也要承认**：当前系统工作正常。误触发风险极低（因为只检查 tool 返回值，不检查 LLM 文本）。

**结论**：4-2 合法，价值是"消除无意义的序列化层 + 修复冗余解析 bug"。但如果做得不好，改动范围极大。

---

### 第二层：方案空间探索 — REFACTOR_PLAN.md 方案的缺陷

#### 4-1 的实施困难

REFACTOR_PLAN.md 中的 `LoopContext` Protocol 只有 6 个方法。但实际上 loop 访问了 harness 的远不止 6 个接口：

```
方法调用 (~12个):
  execute_tool, check_doom_loop, is_budget_exceeded, check_soft_turn_limit,
  check_cognitive_output, check_reflection_needed, check_auto_spawn_needed,
  format_context, compress_messages, track_cognitive_output, increment_read_turn,
  ingest_perspective_findings, create_sub_harness

子对象深层访问 (~10个):
  mcl.check_stagnation, mcl.format_stagnation_feedback, mcl.gate_completion,
  mcl.format_completion_feedback, mcl.assess_reader_difficulty,
  phase_fsm.phase_name, phase_fsm.current_phase, phase_fsm.force_transition,
  phase_fsm.request_transition,
  hypothesis_module.tick, hypothesis_module.is_ready, hypothesis_module.is_saturated,
  evolution_engine.get_edit_experience_context,
  adaptive_config, signal_dispatcher, budget_policy.token_limit,
  tool_registry.get_tools_for_phase

State 属性读取 (~8个):
  loop_turns, total_tokens, last_prompt_tokens, current_persona,
  findings, sections_read, paper_sections, token_budget

State 属性直接写入 (6个):
  loop_turns, total_tokens, last_prompt_tokens, current_persona
```

如果定义一个 Protocol 涵盖所有这些，它会有 **30+ 个方法/属性**——这不是"协议"，这是"把 Harness 的全部 API 照搬到一个 interface"。

**这就是原方案的根本缺陷：它假设 loop 只需要少量 harness 功能，但实际上 loop 需要 harness 的几乎所有功能。**

#### 4-2 的实施困难

当前架构中，**信号已经是通过 tool call 产生的**——不是 LLM 直接输出 `__DONE__`，而是 LLM 调用 `done` 工具，handler 内部判断后返回 `__DONE__|...`。

如果改成"信号就是独立 tool call"，需要回答几个关键问题：

1. **quality gate 放在哪里？** 当前 `tool_done` handler 内部会检查 MCL gate，决定返回 `__DONE__` 还是 `__NUDGE__`。如果 done 变成一个"直通" tool call，gate 逻辑要搬到哪里？
2. **tool 列表膨胀**：当前 Agent 看到的是 `done`、`switch_persona` 等业务语义的工具名。改成 `signal_done`、`signal_switch` 等本质上是换了个名字。
3. **向后兼容**：已有的 system prompt、few-shot 示例、COGNITIVE_ANCHOR 中的行为描述全部要改。

---

### 第三层：风险追问

| 场景 | 后果 |
|------|------|
| 不做 4-1（维持现状） | 短期无风险；长期新 task type 时 loop 条件分支膨胀 |
| 做错 4-1（过大的 Protocol） | Protocol 变成 harness 镜像，增加维护成本，不如不做 |
| 不做 4-2（维持现状） | 冗余解析 bug 可独立修；误识风险极低；性能开销可忽略 |
| 做错 4-2（简单换格式但不改架构） | 改动面太大（所有 handler + loop 7 个分支），一处遗漏就会导致 Agent 挂掉 |

---

## 三、重新设计的方案

### 4-1 新方案：渐进式接口面收窄（Facade 模式）

**核心策略**：不引入新的抽象层（Protocol），而是让 harness 暴露更精确的方法、让 loop 不再绕过这些方法。

loop.py 实际上有三类对 harness 的依赖：

| 类别 | 当前做法 | 正确的解耦方向 |
|------|----------|----------------|
| A. State 计数器维护（turns, tokens） | loop 直接 `+=` | 改为 harness 方法调用（已有 `increment_turn()`，只是 loop 没用它） |
| B. 检查/催促系统（doom_loop, budget, nudge...） | loop 调 harness 方法 | **已经是好的设计**，保持不动 |
| C. 子对象深层访问（mcl, phase_fsm, hypothesis_module） | loop 越过 harness 直接访问子对象 | 改为在 harness 上暴露 facade 方法 |

#### Step 1：消除 6 处直接 state 写入（推荐立即做）

| 当前代码（loop.py） | 改为 | harness 侧 |
|---------------------|------|------------|
| `harness.state.loop_turns += 1` (L246) | `harness.increment_turn(usage)` | 已有方法 |
| `harness.state.total_tokens += ...` (L363) | 合并入 `increment_turn(usage)` 调用 | 已支持 usage 参数 |
| `harness.state.last_prompt_tokens = ...` (L366) | `harness.record_usage(usage)` | 新增 one-liner |
| `harness.state.current_persona = target` (L624) | `harness.set_persona(target)` | 新增 facade，可附加校验 |
| `harness.state.total_tokens += sub_tokens` (L962, L1182) | `harness.add_sub_tokens(sub_tokens)` | 新增 facade |

**改动量**：harness 新增 3 个 one-liner 方法，loop 改 6 处调用。每个改动独立可验证。

**为什么这比 Protocol 好？**

- 改动范围可控（每次只改一小块）
- 不引入新的抽象层
- 可以逐个合入验证，不需要 big bang
- 未来如果真需要 Protocol，代码已经准备好了

**潜在 bug 修复**：`increment_turn()` 内部还做了 session_memory 更新。loop 直接 `+= 1` 跳过了这个逻辑。当前可能是一个潜在 bug——loop 递增了 turns 但没有触发 session_memory 的 should_update 检查。

#### Step 2：收拢子对象深层访问（不急，逐步做）

将 `harness.mcl.check_stagnation()` 等深层调用逐步改为 harness 的一等 facade 方法（如 `harness.check_stagnation()`）。不紧急，因为这些子对象的 API 已经稳定。

#### Step 3：未来如果需要 Protocol

到那时 harness 的公开方法已经覆盖了 loop 的所有需求，Protocol 只需从 harness 的公开方法签名自动生成——不是从头设计。

---

### 4-2 新方案：消除 string round-trip（内部表示结构化）

**核心策略**：保持 LLM 侧行为不变（仍然调用 `done`/`switch_persona` 等工具），只改内部数据流——tool handler 返回结构化对象而非字符串。

#### 设计：ToolResult dataclass

```python
# 新增文件: core/tool_result.py（约 25 行）

from dataclasses import dataclass
from typing import Any, Optional
from core.signal_parser import SignalType

@dataclass
class ToolResult:
    """tool handler 的统一返回类型。

    普通工具: ToolResult(content="结果文本")
    信号工具: ToolResult(content="结果文本", signal=SignalResult(...))
    """
    content: str  # 给 messages 的人类可读文本
    signal: Optional['SignalResult'] = None

@dataclass
class SignalResult:
    """结构化信号数据（替代 __DONE__|xxx 字符串）。"""
    signal_type: SignalType
    payload: Any  # dict 或 str
```

#### 迁移路径（逐信号迁移，不 big-bang）

**Phase A：tool_done 先改**（最高价值，因为有 quality gate 逻辑）

```python
# Before (misc.py, tool_done):
def tool_done(...) -> str:
    if checker_nudge:
        return f"__NUDGE__|[Checker 校验] {checker_nudge}"
    if gate_result:
        return f"__NUDGE__|{gate_result}"
    return f"__DONE__|{summary}"

# After:
def tool_done(...) -> ToolResult:
    if checker_nudge:
        return ToolResult(
            content=f"[Checker 校验] {checker_nudge}",
            signal=SignalResult(SignalType.NUDGE, f"[Checker 校验] {checker_nudge}")
        )
    if gate_result:
        return ToolResult(
            content=gate_result,
            signal=SignalResult(SignalType.NUDGE, gate_result)
        )
    return ToolResult(
        content="任务完成。",
        signal=SignalResult(SignalType.DONE, summary)
    )
```

**harness.execute_tool 适配层**：

```python
def execute_tool(self, name: str, args: dict) -> str | ToolResult:
    result = self._dispatch(name, args)
    if isinstance(result, str):
        return result  # 向后兼容未迁移的 handler
    return result
```

**loop.py 适配**：

```python
result = harness.execute_tool(tc["name"], tc["arguments"])

if isinstance(result, ToolResult):
    if result.signal:
        parsed = ParsedSignal(
            signal_type=result.signal.signal_type,
            raw="",
            payload=result.signal.payload,
        )
    else:
        tool_output = result.content
        parsed = None
elif isinstance(result, str):
    # 旧路径：向后兼容
    parsed = parse_signal(result) if is_signal(result) else None
    tool_output = result
```

#### 为什么这个方案好？

1. **可以一个信号一个信号地迁移**。先改 `tool_done` → 验证 → 再改下一个。
2. **不改 LLM 侧任何东西**。Agent 仍然调用 `done`，system prompt 不用动。
3. **不改 signal_parser.py**。旧路径继续工作。新路径绕过它。最终全部迁移完后再标记 deprecated。
4. **自然修复冗余解析 bug**。`ToolResult.signal.payload` 已经是 dict，不需要二次解析。
5. **自然消除误识风险**。新路径不走字符串前缀匹配。

#### 改动量估算

| 改动 | 文件 | 行数 | 风险 |
|------|------|------|------|
| 新增 `core/tool_result.py` | 1 新文件 | ~25 行 | 极低 |
| 改 `execute_tool` 返回类型适配 | harness.py | ~10 行 | 低 |
| 改 loop.py 信号检测双路径 | loop.py | ~15 行 | 中 |
| 迁移 `tool_done` handler | misc.py | ~15 行 | 低 |
| 迁移其余 5 个信号 handler | misc.py | ~50 行 | 低（逐个做） |
| **总计** | | **~115 行** | |

---

## 四、对自身方案的理性怀疑

**Q1：ToolResult 和 str 的并存会不会让代码更复杂？**

短期是的。但这是过渡期的代价。当所有 handler 迁移完后，可以移除 `str` 分支和 `signal_parser` 的使用。

**Q2：为什么不直接一步到位全改？**

因为违反"不引入新问题"的原则。逐步迁移意味着任何时候只有一个 handler 的行为变了，出问题可以精确定位。

**Q3：signal_parser.py 最终会变成死代码？**

是的。但它有 44 个测试覆盖、模块清晰。等所有 handler 迁移完后直接删除。比一开始就删然后发现有遗漏要安全得多。

**Q4：4-1 Step 1 真的有价值吗？只是把 `harness.state.x += 1` 改成方法调用**

有。因为 `increment_turn()` 内部还做了 session_memory 更新。loop 直接 `+= 1` 跳过了这个逻辑——这可能是一个**潜在 bug**。统一入口后，未来加日志/指标收集只需改 harness 一处。

**Q5：为什么不用 `typing.Protocol`？**

因为 harness 是唯一的 concrete 实现。Protocol 的价值在于多个实现做多态。当前只有一个 Harness 类，Protocol 只会增加维护成本。等未来有第二种 task type（比如 CodeReviewHarness）时再引入。

---

## 五、实施优先级建议

### 推荐立即做（30min bugfix）

**4-1 Step 1**：消除 6 处直接 state 写入。改动量小、风险极低、顺带修复潜在 session_memory bug。

### 推荐推迟

**4-2 ToolResult 方案**：方向正确但不紧急。当前系统工作正常，信号误触发风险接近 0。建议在下一个功能迭代（如增加新信号类型）时顺势引入 ToolResult。

### 不建议做

**原方案的大 Protocol 重构**：收益不匹配风险。等有第二种 task type 时再考虑。

---

## 六、实施时间线（如果决定全做）

```
Sprint 1 (1-2天):
  4-1 Step 1 — 消除 6 处直接 state 写入
    ├── 修改 loop.py 使用 harness.increment_turn(usage)
    ├── 新增 harness.record_usage() / set_persona() / add_sub_tokens()
    └── 运行全量测试 + e2e 验证

Sprint 2 (2-3天):
  4-2 Phase A — 引入 ToolResult + 迁移 tool_done
    ├── 新建 core/tool_result.py
    ├── 改 harness.execute_tool 适配层
    ├── 改 loop.py 信号检测双路径
    ├── 迁移 tool_done → 返回 ToolResult
    └── 验证 done/nudge 路径

Sprint 3 (2-3天):
  4-2 逐步迁移其余 handler
    ├── tool_talk_to_user
    ├── tool_spawn_perspective
    ├── tool_spawn_parallel_readers
    ├── tool_switch_persona
    └── tool_switch_model

Sprint 4 (1天):
  清理 — 移除旧路径
    ├── loop.py 移除 str 分支
    ├── 标记/删除 signal_parser.py
    └── 最终全量验证
```

---

## 七、与 REFACTOR_PLAN.md 的关系

本文档是对 REFACTOR_PLAN.md Phase 4 部分的**替代方案**。建议：

1. 将 REFACTOR_PLAN.md 中的 Phase 4 描述更新为指向本文档
2. 保留原方案文字作为"最初构想"的历史记录
3. 以本文档的实施步骤作为实际执行依据
