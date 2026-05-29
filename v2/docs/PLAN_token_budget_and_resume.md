# Token Budget 截断 + 断点续传 实施计划

> 本文档是该功能的完整交接上下文。新会话开始时发送此文档即可续接实施工作。
>
> 最后更新：2025-07 会话（全部 10 步实施完成 + 审核修复 + 死代码清理）
> 状态：**已完成**
> 方案代号：方案 B（简洁截断 + 持久化 + 断点续传）

---

## 一、仓库定位与路径

```
/Users/yanfeiyu03/Downloads/scholar-agent-public/   <- 仓库根目录
├── v2/core/                                       <- 本计划涉及的核心代码目录
│   ├── loop.py              <- 认知循环驱动（硬截断注入点）
│   ├── harness.py           <- Harness 状态守护层（BudgetPolicy 接入点）
│   ├── agent.py             <- ScholarAgent 入口层（resume 方法、截断保存）
│   ├── phases.py            <- Phase FSM（需回退 WRAP_UP）
│   ├── budget_policy.py     <- Budget 策略（需重写简化）
│   ├── review_scope.py      <- Review Scope（需删除）
│   ├── state_checkpoint.py  <- CheckpointManager（需扩展 save_full_snapshot）
│   └── state.py             <- WorkspaceState（token_budget 字段所在）
├── v2/docs/
│   └── PLAN_token_budget_and_resume.md  <- 本文档
└── docs/
    └── HANDOVER_PROMPT.md   <- 项目总交接文档（格式参考源）
```

---

## 二、设计原则

- **Budget 是安全网，不是行为引导**：Agent 在运行期间完全不知道 budget 存在，自由运行直到被硬停。
- **截断即停止**：不做收尾、不做预警、不改变工具集、不注入收尾指令。
- **中间态完整保留**：截断时把 WorkspaceState + messages + Phase 状态完整序列化到磁盘。
- **断点续传（Resume）**：加载两层（messages + WorkspaceState + Phase），LLM 看到完整历史，行为连贯。
- **Session Persistence 作为降级路径**：不 resume 时，新 session 的 Agent 通过 MemoryStore 读到上次 findings 摘要。
- **进度报告是事后的**：给用户看消耗了多少 token，但不给 Agent 看。
- **档位是对话能力**：审稿深度/范围由 Agent 通过自然语言理解 user_intent 来调节，不是结构化参数。

---

## 三、明确排除的设计（不做的事）

- 不做 WRAP_UP Phase（已写入 phases.py，需回退删除）
- 不做 80% 预警 / 三级响应模型（BudgetLevel 枚举需删除）
- 不做 BudgetExhaustionMode 枚举（WRAP_UP / HARD_CUT 选择需删除）
- 不做 BudgetState 运行时对象（wrap_up_signal_fired 等需删除）
- 不做向 Agent 注入 budget 信息的任何行为
- 不做通知 Agent "预算快没了"
- 不做收尾指令注入 / 强制切换 Phase
- 不做改变 Agent 可用工具集
- 不做 ReviewScope 结构化对象（已写入 review_scope.py，需删除整个文件）
- 不做 ReviewAngle 枚举

---

## 四、执行步骤

### Step 1：回退 phases.py — 删除 WRAP_UP

**目标**：恢复 Phase FSM 到四阶段状态（INITIAL_SCAN / DEEP_REVIEW / EDITING / SYNTHESIS）。

**文件**：`/Users/yanfeiyu03/Downloads/scholar-agent-public/v2/core/phases.py`

**具体改动**：

1. 删除 `Phase.WRAP_UP = "wrap_up"` 枚举值（L43）
2. 删除 `_WRAP_UP_TOOLS` 集合定义（L303-316）
3. 删除 `_PHASE_TOOL_MAP` 中的 `Phase.WRAP_UP` 条目（L324）
4. 删除 `suggest_transition()` 中 `elif current == Phase.WRAP_UP` 分支（L185-187）
5. 修正文件顶部 docstring：移除所有 WRAP_UP 相关描述和转换规则（L16, L23-24）
6. 删除 `get_phase_tools()` docstring 中的 "WRAP_UP 工具" 描述（L201）

**验收**：`from core.phases import Phase` 后 `list(Phase)` 只有 4 个值；无 import 错误。

**复杂度**：轻
**风险**：低。WRAP_UP 是本次新加的，没有被任何现有代码消费（ToolRegistry 使用 `get_tools_for_phase()` 不走 `_PHASE_TOOL_MAP`）。

---

### Step 2：重写 budget_policy.py — 极简策略

**目标**：从"三级响应配置对象"简化为"单一阈值 + 暂停开关"。

**文件**：`/Users/yanfeiyu03/Downloads/scholar-agent-public/v2/core/budget_policy.py`

**改动前（当前文件内容）**：BudgetExhaustionMode、BudgetLevel、BudgetPolicy（含 wrap_up_threshold / wrap_up_reserve / mode / compute_level）、BudgetState、6 个序列化函数，共 274 行。

**改动后（完整替换为极简版）**：

```python
"""
core/budget_policy.py -- Token Budget 策略（极简版）

设计原则:
    1. Budget 是安全网/止损线，不是行为引导
    2. Agent 永远不知道 budget 存在
    3. 只有一个判断: is_exceeded? -> 硬停
    4. 支持 allow_pause: 截断后保存状态供断点续传
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class BudgetPolicy:
    """Token Budget 策略。用户通过入口层设置，Harness 在运行时执行。

    Attributes:
        token_limit: 累计 token 消耗上限。0 表示不限制（无限模式）。
        allow_pause: 截断后是否保存 checkpoint 供 resume（True = 保存）。
    """

    token_limit: int = 0
    allow_pause: bool = True

    @property
    def is_unlimited(self) -> bool:
        """是否为无限制模式。"""
        return self.token_limit <= 0

    def is_exceeded(self, total_tokens_used: int) -> bool:
        """当前累计消耗是否已超出限制。"""
        if self.is_unlimited:
            return False
        return total_tokens_used >= self.token_limit

    def format_report(
        self,
        total_tokens_used: int,
        findings_count: int = 0,
        sections_read: int = 0,
        total_sections: int = 0,
        loop_turns: int = 0,
    ) -> str:
        """格式化 post-hoc 进度报告（给用户看，不给 Agent 看）。"""
        if self.is_unlimited:
            parts = [f"已消耗 {total_tokens_used:,} tokens（无上限模式）"]
        else:
            pct = total_tokens_used / self.token_limit * 100
            parts = [f"Token: {total_tokens_used:,}/{self.token_limit:,} ({pct:.0f}%)"]

        if total_sections > 0:
            parts.append(f"进度: {sections_read}/{total_sections} sections")
        if findings_count > 0:
            parts.append(f"产出: {findings_count} findings")
        if loop_turns > 0:
            parts.append(f"轮次: {loop_turns}")

        return " | ".join(parts)


# ==============================================================
# 序列化（用于 checkpoint / resume）
# ==============================================================

def serialize_budget_policy(policy: BudgetPolicy) -> dict:
    """序列化 BudgetPolicy 为 JSON-safe dict。"""
    return {
        "token_limit": policy.token_limit,
        "allow_pause": policy.allow_pause,
    }


def deserialize_budget_policy(data: dict) -> BudgetPolicy:
    """从 JSON dict 反序列化 BudgetPolicy。"""
    return BudgetPolicy(
        token_limit=data.get("token_limit", 0),
        allow_pause=data.get("allow_pause", True),
    )
```

**删除的内容**：BudgetExhaustionMode、BudgetLevel、BudgetState、compute_level、effective_wrap_up_trigger、serialize_budget_state、deserialize_budget_state。

**验收**：`BudgetPolicy(token_limit=50000).is_exceeded(60000)` 返回 True；`BudgetPolicy().is_unlimited` 返回 True。无其他文件依赖被删除的类。

**复杂度**：轻
**风险**：低。这些类是本次新建的，没有被其他文件 import。

---

### Step 3：删除 review_scope.py

**目标**：移除结构化的范围控制对象（311 行）。

**文件**：`/Users/yanfeiyu03/Downloads/scholar-agent-public/v2/core/review_scope.py` — 整文件删除。

**理由**：用户确认"档位/范围是 agent 本身通过对话理解的能力，不是结构化参数"。

**验收**：文件不存在，`grep -r "review_scope" v2/` 无 import 引用。

**复杂度**：轻
**风险**：无。文件是新建的，没有被任何代码消费。

---

### Step 4：harness.py — 接入 BudgetPolicy + 暴露检测接口

**目标**：Harness 持有简化后的 BudgetPolicy，提供 `is_budget_exceeded()` 方法供 loop 使用。

**文件**：`/Users/yanfeiyu03/Downloads/scholar-agent-public/v2/core/harness.py`

**当前状态**：Harness.__init__（L159）接收 `token_budget: int = 200_000`，透传给 WorkspaceState。`check_token_budget()`（L1125）返回 warning 字符串用于 system message 注入。

**改动**：

1. Harness.__init__ 新增可选参数 `budget_policy: BudgetPolicy | None = None`
2. 向后兼容逻辑：如果不传 budget_policy，从已有 token_budget 参数构造默认 `BudgetPolicy(token_limit=token_budget)`
3. 新增方法：

```python
def is_budget_exceeded(self) -> bool:
    """检查 token budget 是否已耗尽（硬截断判定）。"""
    return self.budget_policy.is_exceeded(self.state.total_tokens)
```

4. 清理 `check_token_budget()`：移除 `total_tokens > token_budget` 的 budget warning 逻辑。保留 `context_ratio > 0.8` 的 context window 接近满载提醒（这是 context window 管理问题，不是 budget 问题）。

**验收**：`Harness(paper_path="test.pdf", budget_policy=BudgetPolicy(token_limit=50000))` 正常构造。`harness.is_budget_exceeded()` 在 total_tokens >= 50000 时返回 True。

**复杂度**：轻
**风险**：低。

---

### Step 5：loop.py — Budget 硬截断

**目标**：在 cognitive_loop 每轮开始的边界检查中，新增"budget 超限 -> LoopDoomStop"的硬截断。

**文件**：`/Users/yanfeiyu03/Downloads/scholar-agent-public/v2/core/loop.py`

**当前状态**：
- L131: `doom = harness.check_doom_loop()` 只检查 turn 数
- L151-155 / L193-195: `check_token_budget()` 返回 warning string -> 注入 system message（Agent 可忽略）
- L347: `harness.state.total_tokens += usage["prompt_tokens"] + usage["completion_tokens"]` — token 累加点

**改动方案**：

在 L137（`return LoopDoomStop(reason=doom, ...)`）之后、SignalDispatcher 逻辑之前，插入硬截断检测：

```python
# ---- Token Budget 硬截断 ----
if harness.is_budget_exceeded():
    reason = f"Token budget 已耗尽（{harness.state.total_tokens:,}/{harness.budget_policy.token_limit:,}）"
    if verbose:
        print(f"\n[Budget] {reason}", file=sys.stderr)
    if _use_streaming:
        on_stream(StreamEvent(type="done", text=reason, turn=harness.state.loop_turns))
    return LoopDoomStop(reason=reason, content=accumulated_content)
```

**检测精度分析**：
- 检测位于 turn 开始时（LLM 调用之前）
- 上一轮 LLM call 的 token 已在 L347 计入 total_tokens
- 最大过冲 = 上一轮的 prompt_tokens + completion_tokens（约 4-8K tokens，单轮 max_tokens=4096）
- 用户已确认此精度（95-100%）可接受

**同时清理**：
- 移除 SignalDispatcher 路径（L151-155）和 fallback 路径（L193-195）中 `budget_warning` 的注入逻辑
- 或改为：只在 token_limit=0（无限制模式）时仍保留 context_ratio 检测（context window 管理，非 budget）

**验收**：设置 token_budget=50000，运行到 total_tokens >= 50000 时循环返回 LoopDoomStop。

**复杂度**：中
**风险**：中。需确保 SignalDispatcher 路径和 fallback 路径的行为统一修改。

---

### Step 6：state_checkpoint.py — 扩展支持 Full Snapshot

**目标**：截断时保存 WorkspaceState + messages + Phase 状态 + BudgetPolicy，支持完整恢复。

**文件**：`/Users/yanfeiyu03/Downloads/scholar-agent-public/v2/core/state_checkpoint.py`

**当前 CheckpointManager 能力**：
- save(): 接收 WorkspaceState -> StateSerializer.serialize() -> JSON -> 写文件（可 gzip）
- restore(): 从文件加载 -> deserialize -> 返回 WorkspaceState
- 支持多检查点管理（保留最近 N 个，自动清理）

**新增数据结构**：

```python
@dataclass
class FullSnapshot:
    """完整运行快照（断点续传用）。"""
    state: dict              # WorkspaceState 序列化后的 dict
    messages: list[dict]     # 完整对话历史
    phase: str               # 当前 Phase 枚举值（"deep_review" 等）
    phase_history: list[str] # Phase 转换历史
    transition_count: int    # Phase 转换次数
    budget_policy: dict      # BudgetPolicy 序列化 dict
    stop_reason: str         # 停止原因描述
    timestamp: float         # 保存时间
    paper_path: str          # 论文路径（resume 时需要重新加载论文）
    model: str               # 使用的 LLM 模型名
    persona: str             # 人格名称
```

**新增方法**：

```python
def save_full_snapshot(
    self,
    state: Any,
    messages: list[dict],
    phase_fsm: "PhaseFSM",
    budget_policy: "BudgetPolicy",
    stop_reason: str,
    paper_path: str,
    model: str,
    persona: str,
) -> CheckpointMeta:
    """保存完整运行快照。复用现有压缩/清理逻辑。"""
    ...


def restore_full_snapshot(self, checkpoint_id: str | None = None) -> FullSnapshot:
    """恢复完整快照。不传 id 时恢复最新的。"""
    ...
```

**messages 序列化注意事项**：
- messages 是 `list[dict]`，每个 dict 有 role / content / tool_calls / tool_call_id 等字段
- content 可能为 None（tool_calls 时）——序列化时必须保留 None
- tool_calls 中的 arguments 是 string（JSON-safe）
- 直接 `json.dumps(messages)` 可行，无需特殊处理

**messages 大小应对**：
- 长对话可能产生 100K+ token 的 messages
- 保存时存原始完整版（确保 checkpoint 无损）
- resume 后喂给 LLM 前走 `harness.compress_messages()`（loop.py L255-259 已有的 compaction 逻辑）

**验收**：save_full_snapshot -> restore_full_snapshot 后，state/messages/phase 完整恢复，JSON 文件大小合理（gzip 后 < 1MB 典型值）。

**复杂度**：中
**风险**：中。messages 中 None 值需保留，gzip 对大 messages 的压缩效果待验证。

---

### Step 7：agent.py — 截断时自动保存 + resume() 类方法

**目标**：当 cognitive_loop 因 budget 返回 LoopDoomStop 时，自动保存快照 + 沉淀 session memory。提供 `ScholarAgent.resume()` 从快照恢复继续运行。

**文件**：`/Users/yanfeiyu03/Downloads/scholar-agent-public/v2/core/agent.py`

**7a. 截断时自动保存**

修改 `_handle_result()`（L324）：

```python
def _handle_result(self, result) -> str:
    if isinstance(result, LoopDoomStop):
        # Budget 截断时自动保存 + 沉淀记忆
        if self._budget_policy.allow_pause and "budget" in result.reason.lower():
            self._save_budget_checkpoint(result.reason)
        report = self._format_progress_report()
        return f"[系统中断] {result.reason}\n\n{report}\n\n到目前为止的输出:\n{result.content}"
    elif isinstance(result, LoopDone):
        ...
```

`_save_budget_checkpoint()` 内部：
1. 调用 `CheckpointManager.save_full_snapshot()`
2. 调用 `self.end_session()`（将 findings 沉淀到 MemoryStore，即 Step 9 的 Session Persistence）

**7b. resume() 类方法**

```python
@classmethod
async def resume(
    cls,
    checkpoint_path: str,
    new_token_limit: int | None = None,
    model: str | None = None,
    verbose: bool = True,
    on_stream: OnStreamCallback = None,
) -> str:
    """从断点快照恢复，继续运行。

    Args:
        checkpoint_path: checkpoint 文件路径或包含 checkpoints 的目录
        new_token_limit: 新的 token 上限（追加预算）。不传 = 不额外追加。
        model: 覆盖使用的模型（不传则用原 checkpoint 中记录的模型）
        verbose: 日志输出
        on_stream: 流式回调

    Returns:
        Agent 恢复后继续运行的输出（直到下次停止或完成）
    """
```

**resume 核心流程**：
1. 从磁盘加载 FullSnapshot
2. 用 snapshot.paper_path + snapshot.persona 构造 ScholarAgent（正常构造流程：加载论文、初始化 Harness/MCL 等）
3. 用 FullSnapshot.state 覆盖 `agent.harness.state`（恢复 findings/edits/token_count/sections_read）
4. 用 FullSnapshot.phase 做 `agent.harness.phase_fsm.force_transition(saved_phase)`
5. 恢复 `agent.messages = snapshot.messages`
6. 如果传了 new_token_limit -> `agent._budget_policy.token_limit = new_token_limit`（追加预算）
7. 标记 `agent._started = True`
8. 驱动继续运行：调用 `cognitive_loop(messages=agent.messages, ...)`
9. 返回结果

**关于 Harness 组件重建的精度损失（可接受）**：
- AdaptiveConfig tick 状态：不恢复（会从 turn=0 重新 tick，影响习惯渐进加载的节奏）
- MCL cache：不恢复（MCL 重新初始化，Sub-Reader 路由从冷启动开始）
- EvolutionEngine：从 memory 重新加载（行为可能略有差异）
- 这些都是 Session 级缓存，不影响核心审稿产出

**验收**：Agent 因 budget 停止 -> `ScholarAgent.resume(path, new_token_limit=100000)` -> Agent 基于之前的 findings 继续审稿。

**复杂度**：**中高**（本计划最复杂的 Step）
**风险**：
- Harness 重建时会重新 load_paper（正确的，因为论文内容不在 checkpoint 中）
- force_transition 跳过 precondition 检查（需确认 PhaseFSM 支持，当前 _check_precondition 始终返回 allowed=True，所以不是问题）
- messages 恢复后如果太大，需要 compress_messages 处理

---

### Step 8：agent.py — 入口层参数适配

**目标**：ScholarAgent.__init__ 接收 BudgetPolicy，向后兼容现有 token_budget int 参数。

**文件**：`/Users/yanfeiyu03/Downloads/scholar-agent-public/v2/core/agent.py`（L146 附近）

**改动**：

```python
def __init__(
    self,
    ...
    token_budget: int = 100000,
    budget_policy: BudgetPolicy | None = None,  # 新增
    ...
):
    # 向后兼容: 没传 budget_policy 时从 token_budget 构建
    if budget_policy is None:
        budget_policy = BudgetPolicy(token_limit=token_budget)
    self._budget_policy = budget_policy

    self.harness = Harness(
        ...
        budget_policy=budget_policy,
        ...
    )
```

**验收**：`ScholarAgent(paper_path="x.pdf", token_budget=100000)` 行为不变；`ScholarAgent(paper_path="x.pdf", budget_policy=BudgetPolicy(token_limit=50000))` 正常工作。

**复杂度**：轻
**风险**：低。

---

### Step 9：Session Persistence — 截断时沉淀记忆

**目标**：确保即使用户不 resume，新 session 也能通过 MemoryStore 读到上次的 findings 摘要。

**文件**：`/Users/yanfeiyu03/Downloads/scholar-agent-public/v2/core/agent.py`

**当前状态**：`end_session()`（L358）调用 MemoryStore 存储 findings/patterns，但只在用户显式调用时触发。

**改动**：在 Step 7a 的 `_save_budget_checkpoint()` 中已包含 `self.end_session()` 调用——无需额外改动。

这样，下次 `ScholarAgent.start()` 时，MemoryStore 中已有上次的 findings 和 patterns，通过现有的 `MemoryStore.format_context()` -> `ContextAssembler` 自动注入 system prompt。

**验收**：budget 截断后，`.memory/` 目录下有新的 session record。新建 Agent start() 时 system prompt 包含上次 findings 摘要。

**复杂度**：轻（逻辑已包含在 Step 7a 中）
**风险**：低。复用现有 end_session 逻辑。

---

### Step 10：进度报告 — Post-hoc 消耗统计

**目标**：截断或正常完成时，给用户输出消耗统计。Agent 不可见。

**文件**：`/Users/yanfeiyu03/Downloads/scholar-agent-public/v2/core/agent.py`

**实现**：

```python
def _format_progress_report(self) -> str:
    """格式化 post-hoc 进度报告（给用户看）。"""
    state = self.harness.state
    return "[消耗统计] " + self._budget_policy.format_report(
        total_tokens_used=state.total_tokens,
        findings_count=len(state.findings),
        sections_read=len(state.sections_read),
        total_sections=len(state.paper_sections),
        loop_turns=state.loop_turns,
    )
```

输出示例：`[消耗统计] Token: 45,230/50,000 (90%) | 进度: 4/7 sections | 产出: 6 findings | 轮次: 12`

在 LoopDone 和 LoopDoomStop 的返回文本中追加此报告。

**验收**：任务结束后用户看到消耗统计行。

**复杂度**：轻
**风险**：无。

---

## 五、执行顺序与依赖关系

```
清理阶段（并行，互不依赖）:
  Step 1: 回退 phases.py (删 WRAP_UP)
  Step 2: 重写 budget_policy.py (极简版)
  Step 3: 删除 review_scope.py

基础设施阶段（依赖 Step 2）:
  Step 4: harness.py 接入 BudgetPolicy        <- 依赖 Step 2
  Step 6: state_checkpoint.py 扩展 snapshot   <- 独立

核心逻辑阶段（依赖 Step 4）:
  Step 5: loop.py 硬截断                      <- 依赖 Step 4

入口组装阶段（依赖 Step 4 + Step 5 + Step 6）:
  Step 8: agent.py 入口参数适配               <- 依赖 Step 4
  Step 7: agent.py 截断保存 + resume()        <- 依赖 Step 5 + Step 6 + Step 8
  Step 9: session persistence                 <- 包含在 Step 7 中
  Step 10: 进度报告                           <- 依赖 Step 8
```

**推荐执行序**：1 -> 2 -> 3 -> 4 -> 6 -> 5 -> 8 -> 7 -> 10

理由：先清理过去的错误代码（1/2/3），再搭基础设施（4/6），再改核心逻辑（5），最后组装入口和报告（8/7/10）。

---

## 六、风险评估

| 风险项 | 严重度 | 缓解措施 |
|--------|--------|----------|
| resume 后 Harness 组件（AdaptiveConfig/MCL/EvolutionEngine）状态不完全一致 | 低 | 可接受的精度损失。这些是 session 级缓存，不影响核心审稿产出 |
| messages 恢复后太大，接近 128K context window | 中 | resume 后第一轮走 compress_messages()（loop.py L255-259 现有逻辑），compaction 自动裁剪 |
| 截断精度：过冲 4-8K tokens（一轮 LLM call 的量） | 低 | 用户已确认 95-100% 精度可接受 |
| 向后兼容：不传 budget_policy 时行为必须与当前完全一致 | 高 | BudgetPolicy 默认 token_limit=0（无限制），所有 is_exceeded() early return False |
| 删除 WRAP_UP 后现有测试 break | 低 | WRAP_UP 是本轮新加的，没有被任何测试覆盖 |
| CheckpointManager 存 messages 后文件变大（30 轮对话约 200-500KB） | 低 | 已有 gzip 压缩 + 自动清理旧 checkpoint |
| resume 后 load_paper 重新加载 PDF（重复 IO） | 低 | 正确行为——论文内容不存在 checkpoint 中，需从源文件重建 |

---

## 七、验收场景

| 场景 | 配置 | 预期结果 |
|------|------|----------|
| A: 正常完成 | budget=200K，短论文 | LoopDone + 进度报告，无 checkpoint 生成 |
| B: Budget 截断 | budget=50K，长论文 | LoopDoomStop + 自动保存 checkpoint + session memory 沉淀 |
| C: Resume 续传 | 从 B 的 checkpoint 恢复，new_token_limit=100K | Agent 继续工作，messages 中包含之前的 findings |
| D: 新 Session 读上次 | 不 resume，新建 Agent + start() | system prompt 中通过 MemoryStore 注入上次 findings 摘要 |
| E: 无限制模式 | 不设 budget（默认 token_limit=0） | 行为与当前完全一致，budget 机制透明 |
| F: 子 Agent 回流 | spawn 子视角后 token 回流触发截断 | 子视角 findings 已回流到主 state，checkpoint 中包含 |
| G: 追加预算 | resume(path, new_token_limit=150000) | token 累计继续，新上限生效 |

---

## 八、DO / DON'T 速查

### DO

- Budget 检测放在 LLM 调用之前（turn 开始时），避免多花一轮
- 截断后同时触发 session persistence（end_session），确保新 session 可读
- resume 时正常构造 Harness -> 再用 snapshot 覆盖 state（不是绕过构造函数）
- messages 恢复后走 compress_messages() 再喂 LLM
- BudgetPolicy 默认 token_limit=0 确保向后兼容
- 进度报告只给用户看（print/return），不注入 messages

### DON'T

- 不要给 Agent 注入任何关于 budget 的信息（包括 warning、nudge、收尾指令）
- 不要改变工具集（不做 WRAP_UP 工具限制）
- 不要在 budget 截断时让 Agent "收尾"——直接停
- 不要序列化论文内容到 checkpoint（从源文件重建）
- 不要序列化 AdaptiveConfig/MCL 状态（默认重建，可接受的精度损失）
- 不要把 BudgetPolicy 暴露给 LLM（这是 Harness 层的事）

---

## 九、FAQ

**Q1：为什么不做 WRAP_UP 收尾？**
A：用户明确指出："收尾只是对那些子 agent 而言的...如果我们可以保存截断前的内容，那么就不需要收尾这个功能"。完整保存 messages + state 比让 Agent 仓促总结更有价值。

**Q2：截断精度够吗？**
A：检测在 turn 开始时，过冲最多是上一轮的 prompt_tokens + completion_tokens（约 4-8K）。对于 50K-200K 的 budget，这是 2-8% 的偏差。用户已确认可接受。

**Q3：resume 后 Agent 知道自己被中断过吗？**
A：知道——messages 中包含之前所有的思考和 tool call 结果。LLM 看到完整历史，自然理解"我之前在做什么"。但我们不显式注入"你被中断了"之类的提示。

**Q4：如果 messages 太大怎么办？**
A：resume 后第一轮 LLM 调用前，loop.py L255-259 的 compress_messages() 会自动裁剪。这是现有逻辑，不需要新增代码。

**Q5：子 Agent（spawn）的 token 怎么算？**
A：子 Agent 有独立的 budget（从主 Agent 的剩余 budget 分配）。子 Agent 结束后，token 回流：`harness.state.total_tokens += sub_tokens`。如果回流后主 Agent 超限，下一轮检测时截断。

**Q6：向后兼容怎么保证？**
A：BudgetPolicy 默认 `token_limit=0`，此时 `is_unlimited=True`，`is_exceeded()` 永远返回 False。所有现有用法（`ScholarAgent(paper_path=..., token_budget=100000)`）通过 agent.py 入口层自动转换为 `BudgetPolicy(token_limit=100000)`。Harness 初始化同理。不传 budget_policy 时行为完全不变。

---

## 十、接续工作的起手步骤

1. 读本文档，确认设计决策未变
2. `cd /Users/yanfeiyu03/Downloads/scholar-agent-public`
3. 确认环境：`python -c "import v2; print('ok')"`
4. 按推荐执行序开始：Step 1 -> 2 -> 3 -> 4 -> 6 -> 5 -> 8 -> 7 -> 10
5. 每完成一步，运行 `python -c "from v2.core.agent import ScholarAgent; print('import ok')"` 确认无 import 错误
6. 全部完成后，用验收场景 B（budget=50K）做端到端测试
7. 再测场景 C（resume）和场景 E（无限制模式向后兼容）

---

*End of Plan*
