# Streaming 输出设计文档

> **任务**: C1 — Streaming 输出调研  
> **产出时间**: 2025-07  
> **结论**: **条件做** — 仅在接入用户界面（CLI REPL / Web UI）时实施，当前纯后端审稿模式无需 streaming

---

## 1. 场景价值分析

### 1.1 ScholarAgent 的使用模式

ScholarAgent 的核心场景是**批量学术审稿**：用户提交论文 → Agent 自主完成多轮认知循环（通常 8-25 轮）→ 输出结构化审稿报告。这与典型的"人机对话"场景有本质区别：

- **非交互式为主**：审稿过程中 Agent 自主运行，用户不需要实时观察每一步思考
- **工具调用密集**：每轮循环中 LLM 输出大部分是 tool_calls（读论文、记录 finding、验证假说），纯文本 content 占比低
- **结果导向**：用户关心最终审稿报告的质量，不关心中间过程的实时展示

### 1.2 Streaming 能带来的价值

| 场景 | 价值 | 优先级 |
|------|------|--------|
| CLI REPL 模式下用户等待反馈 | 用户能实时看到 Agent "在想什么"，减少焦虑感 | 中 |
| Web UI 集成 | 前端可逐字渲染 Agent 思考过程，提升交互体验 | 中 |
| 长时间运行的审稿任务 | 用户可提前判断 Agent 是否走偏，及时中断 | 低 |
| Token 费用优化 | 无直接关系（streaming 不减少 token 消耗） | 无 |
| 审稿质量提升 | 无直接关系（streaming 不改变认知逻辑） | 无 |

### 1.3 价值判断

Streaming 对 ScholarAgent 的**核心能力**（审稿质量、认知深度）没有任何提升。它的价值完全在**用户体验层**——当且仅当存在一个需要实时展示 Agent 思考过程的前端界面时，streaming 才有意义。

当前 ScholarAgent 的主要使用方式是 `v2/main.py` 的 REPL 模式，用户输入后等待完整结果。在这种模式下，streaming 的价值有限（REPL 可以简单打印进度条或轮次计数器达到类似效果）。

---

## 2. 实现方案

### 两方案核心对比

| 维度 | 方案 A：AsyncGenerator | 方案 B：回调注入 |
|------|----------------------|-----------------|
| **一句话描述** | 把 `cognitive_loop` 整体改造为 async generator，调用方通过 `async for event in cognitive_loop(...)` 消费流式事件 | 不改 `cognitive_loop` 签名，新增一个可选的 `on_stream` 回调参数，有事件时调用它通知外部 |
| **LLM 调用方式** | 始终用流式 `chat_with_tools_stream` | 默认仍用非流式 `chat_with_tools`；当传入 `on_stream` 时可选切换为流式 |
| **函数签名变化** | Breaking change — 返回类型从 `LoopResult` 变为 `AsyncGenerator[StreamEvent, None]` | 非 Breaking — 新增 `on_stream=None` 可选参数，不传时行为完全不变 |
| **调用方改动** | main.py、agent.py、所有测试中 mock cognitive_loop 的地方都要改 | 仅 main.py 传入一个 lambda 即可 |
| **适合场景** | Web UI / SSE / WebSocket 等需要结构化事件流的前端 | CLI REPL 进度展示、简单日志输出 |
| **改动量** | ~190 行（4 个文件） | ~50 行（2 个文件） |
| **风险** | 中（签名变化影响面大） | 低（纯增量，零影响现有行为） |

---

### 方案 A：Loop 层 AsyncGenerator 改造（推荐用于 Web UI 场景）

**核心思路**：将 `cognitive_loop` 从返回 `LoopResult` 的 async 函数改为 `AsyncGenerator[StreamEvent, None]`，在 LLM 响应的 content 部分实时 yield 文本 delta。

**改动点**：

1. **loop.py**（~80 行改动）：
   - 第 225-230 行：`client.chat_with_tools()` → `async for chunk in client.chat_with_tools_stream()`
   - 收到 `content_delta` → `yield StreamEvent(type="thinking", text=chunk["text"])`
   - 收到 `tool_calls` → 进入现有工具执行逻辑（不变）
   - 收到 `finish` → 提取 usage 做 token 统计（不变）
   - 函数签名改为 `async def cognitive_loop(...) -> AsyncGenerator[StreamEvent, None]`
   - 最终结果通过 `yield StreamEvent(type="done", result=loop_result)` 返回

2. **新增 `v2/core/stream_events.py`**（~30 行）：
   ```python
   @dataclass
   class StreamEvent:
       type: Literal["thinking", "tool_start", "tool_result", "done", "error"]
       text: str = ""
       tool_name: str = ""
       result: LoopResult | None = None
   ```

3. **agent.py / main.py**（~20 行改动）：
   - 上层调用从 `result = await cognitive_loop(...)` 改为 `async for event in cognitive_loop(...)`
   - REPL 模式：收到 `thinking` 事件时 `print(event.text, end="", flush=True)`

4. **failover.py**（~60 行新增）：
   - `FailoverClient` 新增 `chat_with_tools_stream()` 方法
   - 逻辑：尝试当前 provider 的 stream，失败时 fallback 到下一个 provider（注意：stream 中途失败无法 resume，需从头重试）

**不需要改动的模块**：
- `harness.py`：所有 harness 交互发生在 LLM 调用前后，不受 streaming 影响
- `compaction.py`：压缩在 LLM 调用前执行（第 189 行），与 streaming 无关
- `boundary_guard.py`：quality gate 在工具执行后触发，与 streaming 无关
- `token_budget.py`：token 统计在 stream 结束后的 `finish` chunk 中获取 usage，逻辑不变

**优点**：
- 改动集中在 loop.py 和 agent.py，不侵入核心认知逻辑
- 向后兼容：可通过 Kill Switch 控制是否启用 streaming（`GODEL_STREAMING_ENABLED`）
- 已有 `chat_with_tools_stream` 实现，底层无需改动

**缺点**：
- `cognitive_loop` 签名变化是 breaking change，所有调用方需适配
- stream 中途网络断开的恢复逻辑较复杂（FailoverClient 需要重试整个请求）
- 测试复杂度增加（需要 mock async generator）

---

### 方案 B：回调/事件总线模式（轻量替代）

**核心思路**：不改 `cognitive_loop` 签名，通过注入回调函数实现实时通知。

**改动点**：

1. **loop.py**（~40 行改动）：
   - 新增参数 `on_stream: Callable[[StreamEvent], None] | None = None`
   - LLM 调用仍用非流式 `chat_with_tools()`（保持现有逻辑不变）
   - 在关键节点调用回调：`on_stream(StreamEvent(type="thinking", text=content))`
   - 工具执行前后也可通知：`on_stream(StreamEvent(type="tool_start", tool_name=name))`

2. **可选增强**：如果需要真正的逐 token 流式，可在 `on_stream` 不为 None 时切换到 `chat_with_tools_stream`：
   ```python
   if on_stream:
       async for chunk in client.chat_with_tools_stream(...):
           if chunk["type"] == "content_delta":
               on_stream(StreamEvent(type="thinking", text=chunk["text"]))
           elif chunk["type"] == "tool_calls":
               tool_calls = chunk["tool_calls"]
           elif chunk["type"] == "finish":
               usage = chunk["usage"]
   else:
       response = await client.chat_with_tools(...)
   ```

3. **main.py**（~10 行改动）：
   - 传入回调：`cognitive_loop(..., on_stream=lambda e: print(e.text, end=""))`

**优点**：
- 不改变 `cognitive_loop` 的返回类型，向后兼容性最好
- 改动量最小（~50 行）
- 不传 `on_stream` 时行为完全等同当前版本
- 测试简单（mock callback 验证调用）

**缺点**：
- 回调模式不如 AsyncGenerator 优雅（无法用 `async for` 消费）
- 如果未来需要 Web UI 的 SSE/WebSocket 推送，回调模式需要额外适配层
- 同步回调在 async 上下文中可能有性能问题（需要 `asyncio.ensure_future` 包装）

---

## 3. 改动量估计

| 方案 | 新增文件 | 修改文件 | 新增代码行 | 修改代码行 | 新增测试 | 风险 |
|------|----------|----------|-----------|-----------|----------|------|
| A（AsyncGenerator） | stream_events.py (~30) | loop.py, agent.py, main.py, failover.py | ~110 | ~80 | ~60 | 中 |
| B（回调模式） | stream_events.py (~30) | loop.py, main.py | ~50 | ~40 | ~30 | 低 |

**方案 A 的风险点**：
- `cognitive_loop` 签名变化影响所有调用方（main.py、测试、可能的外部集成）
- FailoverClient streaming 的中途失败恢复逻辑需要仔细设计
- 现有 1450 个测试中大量 mock `cognitive_loop` 的返回值，需要适配

**方案 B 的风险点**：
- 风险极低，`on_stream=None` 时完全等同当前行为
- 唯一风险是 `if on_stream:` 分支引入的代码路径需要额外测试覆盖

---

## 4. 与现有架构的兼容性分析

### 4.1 不受影响的模块

| 模块 | 原因 |
|------|------|
| `harness.py` | 所有交互在 LLM 调用前后，不在调用中间 |
| `compaction.py` | 压缩在 LLM 调用前执行（loop.py 第 189 行） |
| `boundary_guard.py` | Quality gate 在工具执行后触发 |
| `token_budget.py` | Token 统计在 stream 结束后获取 usage |
| `habits.py` | 习惯选取在 context 组装阶段 |
| `paper_cognition_graph.py` | PCG 在 ORIENTATION 阶段构建，与 streaming 无关 |
| `skill_registry.py` / `installer.py` | Skill 加载在初始化阶段 |

### 4.2 需要注意的交互

| 交互 | 影响 | 处理方式 |
|------|------|----------|
| `harness.compress_messages()` | 无影响 — 在 LLM 调用前执行 | 不变 |
| `harness.state.total_tokens` 累加 | streaming 时 usage 在最后一个 chunk 到达 | 在 `finish` 事件后累加（已有实现） |
| `harness.execute_tool()` | 必须等完整 tool_calls 才能执行 | streaming 中 tool_calls 在 stream 结束后才 yield |
| Signal Dispatcher 注入 | 在 LLM 调用前执行 | 不变 |
| Doom loop check | 在 LLM 调用前执行 | 不变 |

### 4.3 Kill Switch 设计

```python
# godel_config.py
GODEL_STREAMING_ENABLED: bool = _env_flag("SCHOLAR_GODEL_STREAMING", default="0")
"""Streaming 输出。OFF 时使用非流式 chat_with_tools（默认行为）。"""
```

注意默认值为 `"0"`（关闭），因为 streaming 是可选增强，不影响核心审稿能力。

---

## 5. 结论与建议

### 结论：条件做

**不建议现在实施**，原因：
1. ScholarAgent 当前是批量审稿工具，用户不需要实时观察 Agent 思考过程
2. Streaming 对审稿质量零提升，纯粹是 UX 层面的改善
3. 底层 `chat_with_tools_stream` 已经实现，技术债务为零——需要时可以快速接入

**建议在以下条件满足时实施**：
1. ScholarAgent 接入 Web UI 或需要 SSE/WebSocket 推送时 → 实施方案 A
2. 仅需 CLI REPL 的进度反馈时 → 实施方案 B（更轻量）
3. 用户明确表示"想看到 Agent 实时思考过程"时 → 实施方案 B 作为快速验证

**如果要做，推荐路径**：
1. 先实施方案 B（回调模式，~50 行改动，1 天内完成）
2. 验证用户是否真的需要逐 token 流式
3. 如果需要 → 升级为方案 A（AsyncGenerator，~190 行改动，2-3 天）

**预留工作**（零成本，可现在做）：
- 在 `godel_config.py` 预留 `GODEL_STREAMING_ENABLED` flag（默认 OFF）
- 确保 `FailoverClient` 的 TODO 中记录"需要 streaming 方法"

---

## 附录：已有 Streaming 基础设施

`v2/llm/client.py` 第 473-623 行已实现 `chat_with_tools_stream()`：

```python
async def chat_with_tools_stream(
    self, messages, tools, temperature=0.3, max_tokens=4096, tool_choice="auto"
) -> AsyncGenerator[dict, None]:
    # yield {"type": "content_delta", "text": str}  — 增量文本
    # yield {"type": "tool_calls", "tool_calls": [...]}  — 完整 tool calls
    # yield {"type": "finish", "finish_reason": str, "usage": dict}  — 结束信号
```

该方法已处理：
- Tool call chunks 按 index 累积并在 stream 结束后组装
- Usage 从 `stream_options={"include_usage": True}` 的最终 chunk 获取
- Token 计数累加到实例级计数器

**结论**：底层已就绪，上层接入成本可控。

---

*Version: C1 Streaming Design | ScholarAgent V2 | 2025-07*
