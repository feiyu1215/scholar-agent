# ScholarAgent v2 架构蓝图

> **版本**: v2.0-draft | **日期**: 2025-07
> **定位**: 有学术贡献的认知架构实现——一个真正的学术研究 Agent
> **核心原则**: 我们是一个 Agent，不是一个更好的 chatbot
> **容忍度**: 完全重构（可以推翻一切，只要方向正确）
> **架构关系**: C 方案为主体骨架，D 方案（HD-WM）为可插拔认知模块

---

## 〇、自检清单（每次实施前必读）

### 方向校准问题

在每个实施阶段开始前，执行者必须回答以下问题：

1. **我们是一个 Agent 吗？** — 当前改动是否让系统更像"模型在循环里使用工具自主完成任务"，还是更像"一个更好的 prompt 模板"？如果是后者，停下来重新思考。

2. **控制流 > Prompt Engineering？** — 当前改动是在优化"告诉模型该怎么做"（prompt），还是在优化"系统如何编排模型的行为"（控制流）？Agent 架构的本质是控制流设计，不是 prompt 设计。

3. **状态在哪里？** — LLM 是无状态 CPU。当前改动中，状态是由外部系统维护并注入的，还是依赖 LLM "记住"什么？如果是后者，这是一个 bug。

4. **这是有意偏离还是无意偏离？** — 如果当前实现偏离了本文档的设计，是因为发现了更好的路（记录原因），还是因为偷懒/遗忘？有意偏离需要在本文档中记录为"设计修正"。

5. **简单性检查** — Anthropic 的教训："一开始加太多复杂设计，会严重拖慢迭代速度。" 当前改动是否是最简单的能达到目标的方式？

### 正反对照诊断（如果你发现自己在做以下事情，立即停下来）

| 你在做的事 | 说明你偏离了 | 应该做的是 |
|-----------|------------|-----------|
| 优化 system prompt 的措辞让 Agent "表现更好" | 你在做 Prompt Engineering，不是 Agent 架构 | 优化控制流：工具集、状态注入、压缩策略 |
| 让 LLM 在 prompt 里"记住"上一轮的结论 | 你在依赖 LLM 的记忆，它是无状态 CPU | 把结论写入 StateManager，下一轮通过 Context Assembler 注入 |
| 写 if-elif 判断"用户想审稿还是想修改" | 你在做 intent classification / scenario routing | 让 Agent 在循环中自己理解意图并选择工具 |
| 设计一个"审稿完成后自动进入修改阶段"的流程 | 你在做 workflow engine | 让 Agent 通过信号协议自主决定何时转换 |
| 把 LLM 能做的推理包装成一个 Tool | 你在写 Theater Code | 只有需要外部副作用的操作才应该是 Tool |
| 加一个 `decision_report` 工具让 LLM 评估自己的决策 | 你在做无效循环 | LLM 的思考本身就是决策过程 |

### 偏离记录模板

```markdown
## 设计修正 #N: [标题]
- **日期**: YYYY-MM-DD
- **偏离点**: 原设计说 X，实际做了 Y
- **原因**: 为什么 Y 比 X 更好
- **影响范围**: 哪些后续阶段受影响
- **是否需要更新本文档**: 是/否
```

---

## 〇.5、当前系统能力基线（执行者必须知道的起点）

> 以下数据来自 v1 系统 58 个 Phase 的迭代验证。v2 重构的底线是：不退化于这些已验证的能力。

### 已验证的核心能力

| 能力 | 验证方式 | 量化结果 | 对 v2 的意义 |
|------|---------|---------|-------------|
| 认知循环自主审稿 | 真实经济学论文 E2E | 13 轮产出 12 条 findings，无人工干预 | v2 的 Loop 必须至少达到这个水平 |
| Token Pipeline 压缩 | 24 轮混合操作 | 62-74% 字符压缩率，审稿质量不退化 | v2 的 Compaction 不能比这差 |
| 视角分裂 | 自主触发 + 独立子循环 | 子循环 3 轮完成，只占总 token 4.8% | v2 的 SPAWN 机制可参考此效率 |
| 多人格协作链 | Scholar→Writer→Scholar | loop.py 零修改，harness.py 零修改 | 证明 4 文件架构的弹性，v2 应保持此特性 |
| 跨文档交叉验证 | Semantic Scholar API + 参考文献深读 | Agent 自主调用 3 次 fetch_paper_detail | v2 的工具层必须保留此能力 |
| 战略性阅读 | 51-section 论文 | 只读 7/42 核心 sections，findings 从 2→12 条 | v2 的 Context Assembler 应支持类似信号 |

### 已验证的关键设计模式

| 模式 | Phase 来源 | 核心发现 |
|------|-----------|---------|
| "Optional = 不用" | Phase 33 | 工具 schema 中 required vs optional 对 LLM 行为有显著影响。关键字段必须 required |
| "理解 ≠ 质疑" | Phase 34-38 | Agent 能准确理解论文但缺乏批判性追问。解决方案是认知身份层注入，不是加工具 |
| "约束-而非-控制" 五种模式 | Phase 17-51 | 催促/移除控制/赋予知识/认知辅助/视角切换——都是信号而非指令 |
| 边际产出信号 | Phase 52 | Agent 看到"最近 4 轮零产出"后自主切换策略，不需要外部干预 |
| 4 文件架构稳定性 | Phase 2-58 | 58 个 Phase 中 loop.py 核心不到 200 行，从未需要大改 |

### 已知的失败模式（v2 必须避免）

| 失败模式 | 表现 | 根因 | v2 的防御 |
|---------|------|------|----------|
| 过早满足 | Agent 3 轮就停 | 缺乏深度自调节 | Boundary Guardian 的 Completion Gate |
| Doom Loop | 反复读同一个空壳 section | fuzzy match bug + 无退出信号 | 精确匹配优先 + 停滞检测 |
| 只读不记 | 连续 14 轮只 read 不 update_findings | Agent 倾向先收集再输出 | 认知催促器（信号，非强制） |
| 压缩后失忆 | 早期关键信息在压缩中丢失 | 压缩前未沉淀到 state | Smart Compaction 的工作台恢复 |

### 快速启动命令（当前 v1 系统）

```bash
cd /Users/yanfeiyu03/Downloads/scholar-agent-public

# 确认代码没坏
python3 -m pytest tests/ -x -q

# 交互式审稿（手动测试）
python3 -m core.agent tests/papers/radiology_selection.pdf

# 跑 E2E 测试（需要 API，~90s）
python3 -m core.test_e2e_phase34_methodology
```

### 核心文件地图（v1 → v2 迁移参考）

> **实际策略**: 采用"v1 完整副本 + 原地重构"模式，所有 v2 代码位于 `core/v2/` 平面目录下，
> v1 代码保持不动作为参照。新模块直接在 `core/v2/` 中新增文件，不建子目录。

| v1 文件 | 行数 | 职责 | v2 去向（实际路径） | 当前状态 |
|---------|------|------|-------------------|---------|
| `core/identity.py` | ~1104 | 认知身份 + 工具定义 + 认知习惯 | → `core/v2/identity.py`（原地拆分：静态身份 + 习惯库） | ✅ 已复制，待拆分 |
| `core/harness.py` | ~2309 | 状态 + 工具执行 + 压缩 + 上下文 | → `core/v2/harness.py` + `core/v2/state.py` + `core/v2/tools.py` + `core/v2/assembler.py`(待建) + `core/v2/guardian.py`(待建) | ✅ state/tools 已提取 |
| `core/loop.py` | ~446 | 认知循环引擎 | → `core/v2/loop.py`（重构为 Perceive→Decide→Execute→Feedback） | ✅ 已复制，待重构 |
| `core/agent.py` | ~289 | 入口 + CLI | → `core/v2/agent.py`（精简） | ✅ 已复制 |
| `core/memory.py` | 存在 | 跨会话记忆 | → `core/v2/memory.py` + `core/v2/compaction.py`(待建) | ✅ 已复制，待拆分 |
| `core/metacognition.py` | 存在 | CognitiveState | → `core/v2/metacognition.py` | ✅ 已复制 |
| `core/offload.py` | 存在 | OffloadStore | → 合并入 `core/v2/compaction.py`(待建) | ✅ 已复制 |
| `core/pdf_loader.py` | ~190 | PDF 解析 | → `core/v2/pdf_loader.py`（不动） | ✅ 已复制 |
| `core/web_search.py` | ~1048 | 文献搜索 | → `core/v2/web_search.py`（不动） | ✅ 已复制 |

---

## 一、设计哲学与核心约束

### 1.1 我们到底在做什么

ScholarAgent 是一个**学术研究 Agent**。它的核心能力是：

- 自主阅读学术论文并产出结构化审稿意见
- 在长程任务中保持认知连贯性（不丢失上下文、不重复工作）
- 通过假说驱动的方式进行深度学术分析（D 模块）
- 未来扩展为通用学术助手（写作、综述、实验设计）

**它不是**：
- 一个聊天机器人（不是为了"对话"而存在）
- Claude Code 的学术翻版（不是复刻别人的架构）
- 一个 prompt 工程项目（不是靠写更好的 prompt 来提升能力）

### 1.2 六条核心约束（不可违反）

| # | 约束 | 来源 | 违反后果 |
|---|------|------|----------|
| C1 | **Agent = 模型在循环里使用工具** | Anthropic (Barry Zhang) | 如果模型不在循环里，不调用工具，那就不是 Agent |
| C2 | **LLM 是无状态 CPU，所有状态由外部维护** | Harness 工程学 (文章2) | 依赖 LLM "记住"状态 = 必然丢失 |
| C3 | **控制流设计 > Prompt Engineering** | 17种架构模式 (文章4) | 只优化 prompt 而不优化控制流 = 天花板很低 |
| C4 | **分层压缩，不是全量保留** | TencentDB Agent Memory (文章1/3) | 全量保留 = context window 爆炸 |
| C5 | **约束-而非-控制** | ScholarAgent 原有哲学 | Harness 提供信号和边界，不下指令 |
| C6 | **保持简单，先跑通再优化** | Anthropic (Barry Zhang) | 过度设计 = 无法迭代 |

### 1.3 设计决策的优先级

当设计决策冲突时，按以下优先级裁决：

1. **Agent 自主性** > 工程优雅性（宁可代码丑一点，也要让 Agent 能自主决策）
2. **状态可靠性** > 性能优化（宁可慢一点，也要保证状态不丢失）
3. **简单性** > 完备性（宁可功能少一点，也要每个功能都稳定）
4. **可验证性** > 可解释性（宁可黑盒一点，也要能通过测试验证正确性）

---

## 二、架构总览

### 2.1 系统全景图

```
┌─────────────────────────────────────────────────────────────────────────┐
│                     ScholarAgent v2 Architecture                          │
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                    HARNESS LAYER (缰绳层)                        │    │
│  │  "LLM 的运行容器——管理状态、编排上下文、守护边界"                  │    │
│  │                                                                   │    │
│  │  ┌───────────────┐ ┌───────────────┐ ┌────────────────────────┐  │    │
│  │  │ State Manager │ │Context Assembler│ │  Boundary Guardian    │  │    │
│  │  │ (状态管理器)   │ │(上下文编排器)   │ │  (边界守护)           │  │    │
│  │  │               │ │                │ │                        │  │    │
│  │  │ - Phase FSM   │ │ - Static Zone  │ │  - Signal Protocol    │  │    │
│  │  │ - Tool Registry│ │ - Dynamic Sections│ - Budget Monitor   │  │    │
│  │  │ - WM Snapshot │ │ - Memory Inject│ │  - Error Recovery     │  │    │
│  │  └───────────────┘ └───────────────┘ └────────────────────────┘  │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                                    │                                     │
│                                    ▼                                     │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                    COGNITIVE LOOP (认知循环)                      │    │
│  │  "Agent 的心跳——感知、决策、执行、反馈"                           │    │
│  │                                                                   │    │
│  │       ┌──────────┐     ┌──────────┐     ┌──────────┐            │    │
│  │       │ PERCEIVE │────▶│  DECIDE  │────▶│ EXECUTE  │            │    │
│  │       │(组装ctx) │     │(LLM call)│     │(tool/sig)│            │    │
│  │       └──────────┘     └──────────┘     └──────────┘            │    │
│  │            ▲                                    │                 │    │
│  │            └────────────── FEEDBACK ────────────┘                 │    │
│  │                                                                   │    │
│  │  Loop Variants:                                                   │    │
│  │    - Standard ReAct Loop (默认)                                   │    │
│  │    - HD-WM Loop (D模块激活时: 假说驱动)                           │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                                    │                                     │
│                                    ▼                                     │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                    MEMORY SYSTEM (记忆系统)                       │    │
│  │  "Agent 的大脑——分层存储、按需加载、智能压缩"                     │    │
│  │                                                                   │    │
│  │  ┌─────────────────┐ ┌──────────────────┐ ┌─────────────────┐   │    │
│  │  │ Working Memory  │ │ Session Memory   │ │ Long-term Memory│   │    │
│  │  │ (工作记忆)      │ │ (会话记忆)       │ │ (长期记忆)      │   │    │
│  │  │                 │ │                  │ │                 │   │    │
│  │  │ - Current Focus │ │ - Structured     │ │ - Episodic      │   │    │
│  │  │ - Active State  │ │   Notes (9段)    │ │ - Semantic      │   │    │
│  │  │ - [HD-WM slot]  │ │ - Background     │ │ - Procedural    │   │    │
│  │  │                 │ │   Update         │ │                 │   │    │
│  │  └─────────────────┘ └──────────────────┘ └─────────────────┘   │    │
│  │                                                                   │    │
│  │  ┌──────────────────────────────────────────────────────────┐    │    │
│  │  │ Smart Compaction Engine (智能压缩引擎)                    │    │    │
│  │  │ - Summarize history + Restore workspace                   │    │    │
│  │  │ - TencentDB-style layered compression (L0→L3)            │    │    │
│  │  └──────────────────────────────────────────────────────────┘    │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                                    │                                     │
│                                    ▼                                     │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                    TOOL LAYER (工具层)                            │    │
│  │  "Agent 的手脚——与环境交互的唯一方式"                             │    │
│  │                                                                   │    │
│  │  Phase-Aware Tool Registry:                                       │    │
│  │    initial_scan: [read_section, reflect, update_findings, talk]   │    │
│  │    deep_review:  [read, search, fetch, verify, detect, spawn...] │    │
│  │    editing:      [read, edit, update_findings, mark_complete]     │    │
│  │    synthesis:    [review_findings, reflect, talk, done]           │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.2 与 Anthropic "三要素" 的对应关系

Barry Zhang 说 Agent 由三部分组成。我们的对应：

| Anthropic 三要素 | ScholarAgent v2 对应 | 说明 |
|-----------------|---------------------|------|
| **环境 (Environment)** | 论文文本 + 文献库 + 工作区文件系统 | Agent 操作的对象 |
| **工具 (Tools)** | Tool Layer (Phase-Aware Registry) | Agent 改变环境的接口 |
| **系统提示词 (System Prompt)** | Context Assembler 的输出 | 告诉 Agent 目标、约束、当前状态 |

然后，模型在 Cognitive Loop 中被反复调用，形成 Agent 行为。

### 2.3 与 Harness 工程学的对应关系

第二篇文章的"马与缰绳"隐喻：

| Harness 概念 | ScholarAgent v2 对应 | 说明 |
|-------------|---------------------|------|
| **马 (LLM)** | Cognitive Loop 中的 DECIDE 步骤 | LLM 做推理和决策 |
| **缰绳 (Harness)** | Harness Layer 整体 | 管理状态、编排上下文、守护边界 |
| **REPL 容器** | Cognitive Loop 本身 | 感知→决策→执行→反馈的循环 |
| **状态分离** | State Manager + Memory System | 状态不在 LLM 内部，在外部系统中 |
| **Token 变换管道** | Context Assembler | 将原始状态变换为 LLM 可消费的 token 序列 |

---

## 三、Harness Layer 详细设计

### 3.1 State Manager（状态管理器）

**职责**: 维护 Agent 的所有外部状态，LLM 不需要"记住"任何东西。

```python
class StateManager:
    """
    Agent 的状态中枢。
    
    设计原则 (来自 Harness 工程学):
    - LLM 是无状态 CPU，每次调用都从 StateManager 获取完整状态
    - 状态变更只通过工具执行的副作用发生
    - StateManager 负责状态的持久化和恢复
    """
    
    # ===== 阶段状态机 =====
    phase: ReviewPhase  # INITIAL_SCAN → DEEP_REVIEW → EDITING → SYNTHESIS
    phase_transitions: list[PhaseTransition]  # 阶段转换历史
    
    # ===== 工具注册表 =====
    tool_registry: ToolRegistry  # 所有工具的注册表
    active_tools: list[Tool]     # 当前阶段可用的工具子集
    
    # ===== 工作记忆快照 =====
    working_memory: WorkingMemory  # 当前工作台状态
    
    # ===== 论文状态 =====
    paper_state: PaperState  # 论文加载状态、已读 sections、元信息
    
    # ===== 审稿产出 =====
    findings: list[Finding]  # 结构化发现
    edits: list[Edit]        # 修改记录
    
    # ===== 运行时统计 =====
    turn_count: int
    total_tokens_used: int
    tool_calls_since_last_compaction: int
```

#### 3.1.1 Phase FSM（阶段有限状态机）

```python
class ReviewPhase(Enum):
    """
    审稿阶段。
    
    设计依据: 第四篇文章指出 "Agent 架构本质是控制流设计"。
    阶段不是 prompt 里的一句话，而是控制流的实际分支——
    不同阶段暴露不同工具集、使用不同压缩策略、触发不同认知习惯。
    """
    INITIAL_SCAN = "initial_scan"    # 通读全文，建立整体印象
    DEEP_REVIEW = "deep_review"      # 深度审阅，逐节分析
    EDITING = "editing"              # 修改建议，具体改写
    SYNTHESIS = "synthesis"          # 综合评判，产出最终意见

class PhaseTransitionRule:
    """
    阶段转换规则。
    
    注意: 转换由 Harness 判断，不由 LLM 决定。
    LLM 可以发出 "我觉得该进入下一阶段" 的信号，
    但最终决定权在 Harness（约束-而非-控制）。
    """
    from_phase: ReviewPhase
    to_phase: ReviewPhase
    conditions: list[Callable]  # 所有条件满足才转换
    
TRANSITION_RULES = [
    PhaseTransitionRule(
        from_phase=INITIAL_SCAN,
        to_phase=DEEP_REVIEW,
        conditions=[
            lambda s: s.sections_scanned >= s.total_sections * 0.8,  # 至少扫过 80%
            lambda s: s.initial_impressions_recorded,  # 已记录初步印象
        ]
    ),
    PhaseTransitionRule(
        from_phase=DEEP_REVIEW,
        to_phase=SYNTHESIS,
        conditions=[
            lambda s: s.sections_deeply_reviewed >= s.total_sections * 0.6,  # 至少深审 60%
            lambda s: len(s.findings) >= 3,  # 至少有 3 个发现
            lambda s: s.hypothesis_saturation if s.hdwm_active else True,  # HD-WM 饱和信号
        ]
    ),
    # ...
]
```

#### 3.1.2 Tool Registry（工具注册表）

```python
class ToolRegistry:
    """
    工具注册表——替代原来 harness.py 中的 14 个 if-elif。
    
    设计依据:
    - 第六篇 Anthropic: "工具给 Agent 提供行动接口"
    - 第二篇 Harness 工程学: "自我演化的工具"
    - 第四篇 17种架构: 控制流通过工具集切换实现
    
    关键约束:
    - 每个工具必须声明它属于哪些阶段
    - 工具的 description 是 LLM 理解工具的唯一途径（要写好）
    - 工具执行的副作用必须反映到 StateManager 中
    """
    
    _tools: dict[str, ToolDefinition] = {}
    
    def register(self, name: str, phases: list[ReviewPhase], 
                 handler: Callable, description: str, parameters: dict):
        """注册一个工具"""
        ...
    
    def get_tools_for_phase(self, phase: ReviewPhase) -> list[ToolDefinition]:
        """返回当前阶段可用的工具子集"""
        return [t for t in self._tools.values() if phase in t.phases]
    
    def execute(self, name: str, params: dict, state: StateManager) -> ToolResult:
        """执行工具并更新状态"""
        tool = self._tools[name]
        result = tool.handler(params, state)
        # 工具执行后，状态变更自动反映到 StateManager
        return result

# 工具注册示例
@tool_registry.register(
    name="read_section",
    phases=[INITIAL_SCAN, DEEP_REVIEW, EDITING],
    description="读取论文的指定 section。返回该 section 的完整文本。",
    parameters={"section_id": "str: section 标识符"}
)
def read_section(params: dict, state: StateManager) -> ToolResult:
    section = state.paper_state.get_section(params["section_id"])
    state.working_memory.current_section = section
    state.paper_state.mark_read(params["section_id"])
    return ToolResult(content=section.text)
```

### 3.2 Context Assembler（上下文编排器）

**职责**: 将 StateManager 中的状态变换为 LLM 可消费的 system prompt。

这是 Harness 工程学中"Token 变换管道"的实现。

```python
class ContextAssembler:
    """
    上下文编排器——将状态变换为 prompt。
    
    设计依据:
    - Claude Code: 静态/动态分离 + Section 注册 + 惰性缓存
    - TencentDB: 分层压缩，不同信息有不同的"注意力层级"
    - Anthropic: "模型对世界的理解可能压在 1-2 万 token 里"
    
    核心原则:
    - 静态信息（身份、标准、协议）缓存，不重复计算
    - 动态信息（进度、发现、工作台）每轮重算
    - 总 token 预算有上限，超出时触发 compaction
    """
    
    # Token 预算（硬约束）
    STATIC_ZONE_BUDGET = 2000      # 身份+标准+协议，不可压缩
    DYNAMIC_ZONE_BUDGET = 8000     # 动态 sections 总预算
    SESSION_MEMORY_BUDGET = 4000   # Session Memory 注入预算
    TOTAL_SYSTEM_PROMPT_BUDGET = 15000  # system prompt 总上限
    
    def assemble(self, state: StateManager) -> str:
        """组装完整的 system prompt"""
        parts = []
        
        # 1. 静态区（缓存）
        parts.append(self._get_static_zone())  # 身份 + 标准 + 信号协议
        
        # 2. 动态区（每轮重算）
        for section in self._get_active_sections(state):
            parts.append(section.compute(state))
        
        # 3. Session Memory 注入
        if state.session_memory:
            parts.append(self._format_session_memory(state.session_memory))
        
        # 4. 认知习惯注入（按阶段/情境，最多 5 条）
        habits = self._get_active_habits(state)
        if habits:
            parts.append(self._format_habits(habits))
        
        # 5. Token 预算检查
        assembled = "\n\n".join(parts)
        if self._count_tokens(assembled) > self.TOTAL_SYSTEM_PROMPT_BUDGET:
            assembled = self._trim_to_budget(assembled)
        
        return assembled
```

#### 3.2.1 Section 注册机制

```python
class SectionRegistry:
    """
    动态 Section 注册表。
    
    借鉴 Claude Code 的 Section 注册 + memoization 机制。
    每个 section 是一个独立的信息单元，可以：
    - 按需计算（惰性）
    - 按条件缓存（跨轮/跨阶段/永不缓存）
    - 按优先级裁剪（token 不够时，低优先级 section 被丢弃）
    """
    
    _sections: list[SectionDefinition] = []
    _cache: dict[str, tuple[str, int]] = {}  # name → (content, computed_at_turn)
    
    def register(self, name: str, priority: int, cache_policy: CachePolicy,
                 compute_fn: Callable[[StateManager], str]):
        """注册一个动态 section"""
        ...
    
    def get_active_sections(self, state: StateManager) -> list[str]:
        """返回当前应该注入的 sections（按优先级排序，受预算约束）"""
        sections = []
        budget_remaining = self.DYNAMIC_ZONE_BUDGET
        
        for section_def in sorted(self._sections, key=lambda s: s.priority, reverse=True):
            content = self._compute_or_cache(section_def, state)
            tokens = count_tokens(content)
            if tokens <= budget_remaining:
                sections.append(content)
                budget_remaining -= tokens
            else:
                break  # 预算用完，低优先级 section 被丢弃
        
        return sections

# Section 注册示例
@section_registry.register(
    name="review_progress",
    priority=90,  # 高优先级，几乎总是注入
    cache_policy=CachePolicy.NEVER  # 每轮都变
)
def compute_review_progress(state: StateManager) -> str:
    return f"""## 审稿进度
已扫描: {state.sections_scanned}/{state.total_sections}
已深审: {state.sections_deeply_reviewed}/{state.total_sections}
发现问题: {len(state.findings)} (严重: {count_severe}, 一般: {count_minor})
当前阶段: {state.phase.value}
当前焦点: {state.working_memory.current_focus or '无'}"""

@section_registry.register(
    name="paper_metadata",
    priority=70,
    cache_policy=CachePolicy.SESSION  # 整个会话不变
)
def compute_paper_metadata(state: StateManager) -> str:
    return f"""## 论文信息
标题: {state.paper_state.title}
领域: {state.paper_state.domain}
方法: {state.paper_state.methodology_type}
页数: {state.paper_state.page_count}"""

@section_registry.register(
    name="active_findings",
    priority=85,
    cache_policy=CachePolicy.NEVER
)
def compute_active_findings(state: StateManager) -> str:
    # 只展示最近 10 个 findings 的摘要，避免膨胀
    recent = state.findings[-10:]
    return "## 已发现问题\n" + "\n".join(
        f"- [{f.severity}] {f.summary}" for f in recent
    )

@section_registry.register(
    name="hypothesis_status",
    priority=80,
    cache_policy=CachePolicy.NEVER,
    condition=lambda state: state.hdwm_active  # 只在 D 模块激活时注入
)
def compute_hypothesis_status(state: StateManager) -> str:
    """D 模块的假说状态——只在 HD-WM 激活时出现"""
    hdwm = state.working_memory.hypothesis_module
    active = [h for h in hdwm.hypotheses if h.status == ACTIVE]
    resolved = [h for h in hdwm.hypotheses if h.status in (SUPPORTED, REFUTED)]
    return f"""## 假说状态 (HD-WM)
活跃假说: {len(active)}
已解决: {len(resolved)}
当前验证: {hdwm.active_hypothesis.statement if hdwm.active_hypothesis else '无'}
完成度: {hdwm.review_readiness:.0%}"""
```

### 3.3 Boundary Guardian（边界守护）

```python
class BoundaryGuardian:
    """
    边界守护——约束-而非-控制。
    
    设计原则 (ScholarAgent 原有哲学):
    - 不告诉 LLM "你应该做什么"
    - 只告诉 LLM "你不能做什么" 和 "你现在的状态是什么"
    - 通过信号协议与 LLM 通信
    
    新增 (来自第六篇 Anthropic):
    - 预算意识: 监控 token 消耗，在接近预算时发出警告
    - 错误恢复: 工具调用失败时的自动恢复策略
    """
    
    # ===== 信号协议 (保留) =====
    SIGNALS = {
        "__DONE__": "Agent 认为任务完成",
        "__TALK__": "Agent 需要与用户交流",
        "__SPAWN__": "Agent 请求启动子视角",
        "__NUDGE__": "系统催促（只读，Agent 不可主动发出）",
    }
    
    # ===== 预算监控 (新增) =====
    token_budget: int = 200000  # 单次审稿的 token 总预算
    token_warning_threshold: float = 0.8  # 80% 时发出警告
    
    # ===== 边界规则 =====
    def check_boundaries(self, state: StateManager, llm_output: str) -> BoundaryCheckResult:
        """检查 LLM 输出是否违反边界"""
        violations = []
        
        # 1. 信号格式检查
        if self._has_malformed_signal(llm_output):
            violations.append("信号格式错误")
        
        # 2. 阶段一致性检查（不能在 INITIAL_SCAN 阶段调用 edit 工具）
        if self._has_phase_violation(state, llm_output):
            violations.append("工具与当前阶段不匹配")
        
        # 3. 预算检查
        if state.total_tokens_used > self.token_budget * self.token_warning_threshold:
            violations.append("接近 token 预算上限，建议进入 SYNTHESIS 阶段")
        
        # 4. 死循环检测
        if self._detect_doom_loop(state):
            violations.append("检测到重复行为模式，建议换一个策略")
        
        return BoundaryCheckResult(violations=violations)
```

---

## 四、Cognitive Loop 详细设计

### 4.1 标准 ReAct Loop（默认模式）

```python
class CognitiveLoop:
    """
    认知循环——Agent 的心跳。
    
    设计依据:
    - Anthropic: "Agent = 模型在循环里使用工具"
    - 第四篇 17种架构: ReAct 是基础，更复杂的模式在此之上叠加
    - ScholarAgent 原有: signal protocol + boundary guards
    
    关键约束:
    - 每次循环都是完整的 Perceive→Decide→Execute→Feedback
    - LLM 在 Decide 步骤中是无状态的——所有信息通过 Perceive 注入
    - Execute 步骤的副作用必须反映到 StateManager
    - 循环终止只通过信号协议（__DONE__ / __TALK__）
    """
    
    async def run(self, state: StateManager, assembler: ContextAssembler,
                  guardian: BoundaryGuardian) -> AgentResult:
        
        while True:
            # ===== PERCEIVE: 组装上下文 =====
            system_prompt = assembler.assemble(state)
            messages = state.get_message_history()
            tools = state.tool_registry.get_tools_for_phase(state.phase)
            
            # ===== DECIDE: LLM 调用 =====
            response = await self.llm.call(
                system=system_prompt,
                messages=messages,
                tools=tools,
            )
            
            # ===== 边界检查 =====
            boundary_result = guardian.check_boundaries(state, response)
            if boundary_result.has_violations:
                # 注入边界警告到下一轮的 messages 中
                state.add_system_message(boundary_result.format_warning())
                # 不中断循环，让 LLM 自己修正
            
            # ===== EXECUTE: 处理 LLM 输出 =====
            if response.has_tool_calls:
                for tool_call in response.tool_calls:
                    result = state.tool_registry.execute(
                        tool_call.name, tool_call.params, state
                    )
                    state.add_tool_result(tool_call.id, result)
                    state.tool_calls_since_last_compaction += 1
            
            # ===== 信号检测 =====
            signal = self._detect_signal(response)
            if signal:
                if signal.type == "__DONE__":
                    return AgentResult(status="complete", summary=signal.payload)
                elif signal.type == "__TALK__":
                    return AgentResult(status="needs_input", message=signal.payload)
                elif signal.type == "__SPAWN__":
                    await self._handle_spawn(signal.payload, state)
            
            # ===== 阶段转换检查 =====
            new_phase = state.check_phase_transition()
            if new_phase:
                state.transition_to(new_phase)
                # 阶段转换时触发 session memory 更新
                await state.session_memory_manager.update(state)
            
            # ===== Compaction 检查 =====
            if self._should_compact(state):
                await state.compaction_engine.compact(state)
            
            # ===== Session Memory 后台更新检查 =====
            if state.session_memory_manager.should_update(state):
                await state.session_memory_manager.update(state)
            
            state.turn_count += 1
```

### 4.2 HD-WM Loop（D 模块激活时）

```python
class HDWMLoop(CognitiveLoop):
    """
    假说驱动的认知循环——D 模块的核心。
    
    与标准 ReAct Loop 的区别:
    - Perceive 阶段额外注入假说状态
    - Decide 阶段的 LLM 输出需要关联到假说
    - Execute 阶段额外维护假说生命周期
    - 终止条件基于假说解决率（review_readiness）
    
    设计依据:
    - CoALA: Planning→Execution 决策循环
    - 第四篇: Mental Loop + Metacognitive 架构模式
    - D 方案原始设计: Hypothesis-Driven Working Memory
    
    关键约束:
    - HD-WM 是"建议"而非"强制"——LLM 可以跳过假说队列
    - 假说质量由 LLM 生成，Harness 只管理生命周期
    - 这是一个可插拔模块，关闭后退化为标准 ReAct Loop
    """
    
    async def run(self, state: StateManager, assembler: ContextAssembler,
                  guardian: BoundaryGuardian) -> AgentResult:
        
        hdwm = state.working_memory.hypothesis_module
        
        while True:
            # ===== PERCEIVE (增强): 注入假说状态 =====
            system_prompt = assembler.assemble(state)  # 已包含 hypothesis_status section
            messages = state.get_message_history()
            tools = state.tool_registry.get_tools_for_phase(state.phase)
            # 额外注入: 当前应该验证的假说
            if hdwm.active_hypothesis:
                messages = self._inject_hypothesis_guidance(messages, hdwm)
            
            # ===== DECIDE =====
            response = await self.llm.call(system=system_prompt, messages=messages, tools=tools)
            
            # ===== EXECUTE (增强): 维护假说生命周期 =====
            if response.has_tool_calls:
                for tool_call in response.tool_calls:
                    result = state.tool_registry.execute(tool_call.name, tool_call.params, state)
                    state.add_tool_result(tool_call.id, result)
                    
                    # 假说相关的工具调用
                    if tool_call.name == "generate_hypothesis":
                        hdwm.generate(tool_call.params["statement"], tool_call.params["source"])
                    elif tool_call.name == "add_evidence":
                        hdwm.add_evidence(tool_call.params["hyp_id"], 
                                         tool_call.params["evidence"],
                                         tool_call.params["direction"])
                    elif tool_call.name == "resolve_hypothesis":
                        hdwm.resolve(tool_call.params["hyp_id"], tool_call.params["status"])
            
            # ===== 终止条件 (增强): 基于假说解决率 =====
            signal = self._detect_signal(response)
            if signal and signal.type == "__DONE__":
                # 额外检查: 假说解决率是否达标
                if hdwm.review_readiness >= 0.7:
                    return AgentResult(status="complete", summary=signal.payload)
                else:
                    # 假说未充分解决，提醒 LLM
                    state.add_system_message(
                        f"注意: 仍有 {hdwm.unresolved_count} 个假说未解决，"
                        f"review_readiness={hdwm.review_readiness:.0%}。"
                        f"确定要结束吗？"
                    )
            elif signal:
                # 其他信号正常处理
                ...
            
            # ===== 假说饱和检测 =====
            if hdwm.check_saturation():
                # 新假说产生速率下降 → 可能该进入 SYNTHESIS
                state.add_system_message("假说饱和信号: 新假说产生速率下降，考虑进入综合阶段。")
            
            state.turn_count += 1
```

### 4.3 Loop 选择逻辑

```python
def select_loop(config: AgentConfig) -> CognitiveLoop:
    """
    根据配置选择认知循环。
    
    默认使用标准 ReAct Loop。
    当 config.enable_hdwm = True 时，使用 HD-WM Loop。
    
    这保证了 D 模块是可插拔的——关闭后系统完全退化为 C 方案。
    """
    if config.enable_hdwm:
        return HDWMLoop()
    else:
        return CognitiveLoop()
```

---

## 五、Memory System 详细设计

### 5.1 三层记忆架构

```
┌─────────────────────────────────────────────────────────────┐
│                    Memory System                              │
│                                                              │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  Layer 0: Working Memory (工作记忆)                  │    │
│  │  生命周期: 当前认知循环                               │    │
│  │  容量: ~5000 tokens                                  │    │
│  │  内容: 当前 section + 活跃假说 + 即时观察             │    │
│  │  类比: CPU 寄存器                                    │    │
│  └─────────────────────────────────────────────────────┘    │
│                          │ overflow ↓                        │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  Layer 1: Session Memory (会话记忆)                  │    │
│  │  生命周期: 当前审稿会话                               │    │
│  │  容量: ~12000 tokens (9 段结构化笔记)                │    │
│  │  内容: 累积观察、方法论判断、证据评估                  │    │
│  │  更新: 后台 subagent 在自然断点时更新                 │    │
│  │  类比: 笔记本                                        │    │
│  └─────────────────────────────────────────────────────┘    │
│                          │ session end ↓                     │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  Layer 2: Long-term Memory (长期记忆)                │    │
│  │  生命周期: 永久                                       │    │
│  │  容量: 无限（外部存储）                               │    │
│  │  内容:                                               │    │
│  │    - Episodic: 历史审稿经验 ("上次审 NLP 论文时...")  │    │
│  │    - Semantic: 领域知识 (学科标准、期刊要求)          │    │
│  │    - Procedural: 审稿规则 (认知习惯、流程)           │    │
│  │  类比: 图书馆                                        │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                              │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  Smart Compaction Engine (智能压缩引擎)              │    │
│  │  触发: token 超预算 / 自然断点                        │    │
│  │  策略: 总结历史 + 恢复工作台 + 保留 session memory   │    │
│  │  借鉴: TencentDB L0→L3 分层压缩                     │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### 5.2 Smart Compaction Engine

```python
class SmartCompactionEngine:
    """
    智能压缩引擎。
    
    设计依据:
    - TencentDB Agent Memory: L0(Raw)→L1(JSONL)→L2(Mermaid)→L3(Metadata) 分层压缩
    - Claude Code: Smart Compaction with workspace restoration
    - Anthropic: "模型对世界的理解压在 1-2 万 token 里"
    
    核心思想:
    - 压缩不是"丢弃"，而是"变换表示"
    - 压缩后必须恢复工作台（Agent 不能"忘记"自己在做什么）
    - 不同信息有不同的压缩策略（findings 不压缩，对话历史压缩）
    
    TencentDB 启发的分层策略:
    - L0 (Raw): 原始对话历史 → 保留最近 N 轮
    - L1 (Summary): 较早的对话 → 压缩为结构化摘要
    - L2 (Digest): 更早的对话 → 压缩为一句话 digest
    - L3 (Metadata): 最早的对话 → 只保留元信息（时间、工具调用次数）
    """
    
    # 压缩触发阈值
    COMPACTION_TRIGGER_TOKENS = 80000  # 对话历史超过 80K tokens 时触发
    RECENT_TURNS_TO_KEEP = 6          # 保留最近 6 轮不压缩
    
    async def compact(self, state: StateManager):
        """执行压缩"""
        messages = state.get_message_history()
        
        # 1. 分离: 最近 N 轮 vs 历史
        recent = messages[-self.RECENT_TURNS_TO_KEEP * 2:]  # *2 因为 user+assistant
        history = messages[:-self.RECENT_TURNS_TO_KEEP * 2]
        
        # 2. 压缩历史为摘要 (L0→L1)
        summary = await self._summarize_history(history)
        
        # 3. 构建工作台恢复 (关键!)
        restoration = self._build_workspace_restoration(state)
        
        # 4. 重建 messages
        new_messages = [
            {"role": "user", "content": f"[会话摘要]\n{summary}"},
            {"role": "assistant", "content": "我已了解之前的审稿进展。"},
            {"role": "user", "content": f"[工作台恢复]\n{restoration}"},
            {"role": "assistant", "content": "工作台已恢复，继续审稿。"},
            *recent
        ]
        
        state.set_message_history(new_messages)
        state.tool_calls_since_last_compaction = 0
    
    def _build_workspace_restoration(self, state: StateManager) -> str:
        """
        工作台恢复——压缩后 Agent 需要知道的一切。
        
        这是 Smart Compaction 的核心创新:
        不只是总结历史，还要恢复当前工作状态。
        Agent 压缩后应该能无缝继续工作，就像从未压缩过一样。
        """
        parts = []
        
        # 当前正在审阅的 section
        if state.working_memory.current_section:
            parts.append(f"## 当前正在审阅\n"
                        f"Section: {state.working_memory.current_section_id}\n"
                        f"内容摘要: {state.working_memory.current_section[:2000]}")
        
        # 所有 findings（不压缩，这是核心产出）
        parts.append(f"## 已发现问题 ({len(state.findings)} 个)\n" +
                    "\n".join(f"- [{f.severity}] {f.summary}" for f in state.findings))
        
        # 审稿进度
        parts.append(f"## 审稿进度\n"
                    f"阶段: {state.phase.value}\n"
                    f"已读: {state.sections_read}/{state.total_sections}\n"
                    f"轮次: {state.turn_count}")
        
        # Session Memory（完整保留）
        if state.session_memory:
            parts.append(f"## 审稿笔记\n{state.session_memory.format()}")
        
        # HD-WM 状态（如果激活）
        if state.hdwm_active:
            hdwm = state.working_memory.hypothesis_module
            parts.append(f"## 假说状态\n{hdwm.format_for_restoration()}")
        
        return "\n\n".join(parts)
```

### 5.3 Session Memory Manager

```python
class SessionMemoryManager:
    """
    会话记忆管理器——后台更新结构化笔记。
    
    设计依据:
    - Claude Code: 后台 subagent 更新 Session Memory (9 sections, 12K budget)
    - TencentDB: 上下文卸载到外部画布
    
    核心思想:
    - Session Memory 是 Agent 的"笔记本"
    - 不是每轮都更新，而是在自然断点时更新
    - 更新由独立的 subagent 完成，不干扰主循环
    - 有固定的 schema，保证结构化
    """
    
    SCHEMA = {
        "task_summary": "5-10 词描述当前审稿任务",
        "current_focus": "当前正在关注的问题/section",
        "methodology_assessment": "方法论层面的累积观察和判断",
        "evidence_quality": "证据质量的累积评估",
        "statistical_observations": "统计/数据问题记录",
        "writing_quality": "表达和结构问题",
        "novelty_judgment": "创新性和贡献度判断",
        "key_decisions": "重要的审稿判断和理由",
        "issue_timeline": "按时间顺序的问题发现日志",
    }
    
    MAX_SECTION_TOKENS = 2000
    MAX_TOTAL_TOKENS = 12000
    
    # 更新触发条件
    MIN_TOKENS_BETWEEN_UPDATE = 4000
    MIN_TOOL_CALLS_BETWEEN_UPDATE = 5
    
    def should_update(self, state: StateManager) -> bool:
        """判断是否应该更新 session memory"""
        # 双阈值: token 消耗 AND 工具调用次数
        tokens_since = state.tokens_since_last_sm_update
        tools_since = state.tool_calls_since_last_sm_update
        
        if tokens_since < self.MIN_TOKENS_BETWEEN_UPDATE:
            return False
        if tools_since < self.MIN_TOOL_CALLS_BETWEEN_UPDATE:
            return False
        
        # 自然断点检测
        return self._is_natural_breakpoint(state)
    
    def _is_natural_breakpoint(self, state: StateManager) -> bool:
        """检测自然断点"""
        return (
            state.just_finished_section or      # 刚读完一个 section
            state.findings_added_since_last >= 2 or  # 新增了 2+ findings
            state.phase_just_changed or         # 刚发生阶段转换
            state.last_signal == "__TALK__"      # 刚与用户交流
        )
    
    async def update(self, state: StateManager):
        """使用独立 subagent 更新 session memory"""
        # subagent 只能读取对话历史和当前状态，只能写 session memory
        recent_messages = state.get_recent_messages(last_n=10)
        current_sm = state.session_memory
        
        updated_sm = await self._subagent_update(
            recent_messages=recent_messages,
            current_session_memory=current_sm,
            schema=self.SCHEMA,
            budget=self.MAX_TOTAL_TOKENS,
        )
        
        state.session_memory = updated_sm
        state.tokens_since_last_sm_update = 0
        state.tool_calls_since_last_sm_update = 0
```

---

## 六、D 模块：Hypothesis-Driven Working Memory

### 6.1 定位与关系

D 模块（HD-WM）是 C 方案架构中的一个**可插拔认知模块**。

```
关系图:

C 方案 (主体骨架)
├── Harness Layer ← 始终存在
├── Cognitive Loop ← 有两个变体: Standard / HD-WM
├── Memory System ← 始终存在
│   └── Working Memory
│       └── [HD-WM slot] ← D 模块插入点
└── Tool Layer ← 始终存在，D 模块额外注册假说工具
```

**激活条件**: `config.enable_hdwm = True`

**关闭后的行为**: 系统完全退化为标准 C 方案，无任何副作用。

### 6.2 HD-WM 核心数据结构

（保留 PLAN_D_EXPLORATION.md 中的设计，此处不重复。详见该文档第二节。）

### 6.3 HD-WM 额外注册的工具

```python
# 当 HD-WM 激活时，额外注册以下工具
@tool_registry.register(
    name="generate_hypothesis",
    phases=[INITIAL_SCAN, DEEP_REVIEW],
    description="从当前阅读中产生一个可验证的学术假说。",
    parameters={
        "statement": "str: 假说陈述（如'该论文的 baseline 对比不公平'）",
        "source": "str: 假说产生时正在读的 section"
    }
)
def generate_hypothesis(params, state): ...

@tool_registry.register(
    name="add_evidence",
    phases=[DEEP_REVIEW],
    description="为某个假说添加支持或反对的证据。",
    parameters={
        "hyp_id": "str: 假说 ID",
        "content": "str: 证据内容",
        "direction": "str: 'for' 或 'against'",
        "strength": "float: 证据强度 0.0-1.0"
    }
)
def add_evidence(params, state): ...

@tool_registry.register(
    name="resolve_hypothesis",
    phases=[DEEP_REVIEW, SYNTHESIS],
    description="解决一个假说——标记为 supported/refuted/suspended。",
    parameters={
        "hyp_id": "str: 假说 ID",
        "status": "str: 'supported' / 'refuted' / 'suspended'",
        "reason": "str: 解决理由"
    }
)
def resolve_hypothesis(params, state): ...
```

### 6.4 学术贡献点

D 模块的学术贡献独立于 C 方案的工程贡献：

1. **Hypothesis-Driven Working Memory**: 提出面向学术认知任务的 WM 特化设计
2. **假说生命周期管理**: 形式化描述假说从产生到解决的完整过程
3. **基于假说解决率的任务完成度计算**: 一种新的 Agent 终止条件设计
4. **可插拔认知模块**: 证明认知策略可以作为模块插入通用 Agent 架构

---

## 七、实施计划

### 7.1 阶段总览

```
Phase 1: 基础骨架 (Harness + Standard Loop)     ~4 天
Phase 2: Memory System (三层 + Compaction)       ~4 天
Phase 3: Context Assembler (Section 注册)        ~3 天
Phase 4: Tool Registry + Phase FSM               ~2 天
Phase 5: D 模块 (HD-WM) 集成                    ~4 天
Phase 6: 集成测试 + 回归验证                     ~3 天
                                          总计: ~20 天
```

### 7.2 Phase 1: 基础骨架 ✅ 已完成

**目标**: 建立 Harness Layer + Standard Cognitive Loop 的最小可运行版本。

**具体工作**:

1. ✅ 创建 `core/v2/state.py` — WorkspaceState dataclass（StateManager 雏形）
2. ✅ 创建 `core/v2/tools.py` — ToolRegistry（register/execute 分发模式）
3. ✅ 修改 `core/v2/harness.py` — 引用 state.py + tools.py，用 `_init_tool_registry()` 替代 if-elif
4. ✅ v1 完整副本到 `core/v2/`，所有内部 import 改为 `core.v2.`

**验证标准** (已通过):
- ✅ `from core.v2.harness import Harness` 正常初始化，15 工具全部注册
- ✅ `execute_tool` 通过 ToolRegistry 正确分发
- ✅ v1 测试不受影响（v1 和 v2 独立并存）
- ✅ `tests/test_v2_tool_registry.py` — 5/5 pass

**设计修正 #1: 平面结构替代嵌套目录**
- **偏离点**: 原设计用 `core/harness/`, `core/loop/` 等子目录，实际用 `core/v2/` 平面目录
- **原因**: "v1 完整副本 + 原地重构" 避免命名空间冲突（`core/harness/` 会遮蔽 `core/harness.py`），且保持 v1/v2 可对照
- **影响范围**: 所有后续 Phase 的文件路径
- **是否需要更新本文档**: 是（已更新）

### 7.3 Phase 2: Memory System

**目标**: 建立三层记忆 + Smart Compaction。

**具体工作**:

1. 重构 `core/v2/memory.py` — 拆分为 Working Memory 接口（从 WorkspaceState 分离短期工作状态）
2. 在 `core/v2/memory.py` 中增加 Session Memory Manager（后台更新 session 级别的认知快照）
3. 重构 `core/v2/memory.py` 中现有 MemoryStore — 按 Episodic/Semantic/Procedural 分层
4. 创建 `core/v2/compaction.py` — Smart Compaction Engine（合并现有 offload.py 的能力）

**验证标准**:
- 长论文审阅（>20 轮）不丢失关键信息
- Compaction 后 Agent 能恢复工作状态并继续
- Session Memory 在自然断点时正确更新

**注意事项**:
- Session Memory 的 subagent 更新是异步的，不能阻塞主循环
- Compaction 的"工作台恢复"是核心创新，要重点测试
- Long-term Memory 的 Episodic/Semantic/Procedural 分类可以先用简单的 tag 实现
- 现有 `core/v2/offload.py` 的 OffloadStore 能力将合并入 compaction.py

### 7.4 Phase 3: Context Assembler (部分完成)

**目标**: 实现 Section 注册 + 动态上下文编排。

**具体工作**:

1. ✅ 创建 `core/v2/assembler.py` — ContextAssembler（从 harness.py 的 format_context 提取）
2. ✅ 创建 `core/v2/sections.py` — SectionRegistry + 所有 section 定义（优先级 + 缓存策略）
3. ✅ 创建 `core/v2/identity_static.py` — 静态身份区（~542 tokens）+ `build_system_prompt_v2`
4. ✅ 创建 `core/v2/habits.py` — 认知习惯库（20条习惯分类标注，HabitSelector 按阶段/情境选取，每轮最多5条）

**已完成的验证**:
- ✅ `tests/test_v2_assembler.py` — 12/12 pass
- ✅ `tests/test_v2_identity_habits.py` — 26/26 pass
- ✅ format_context 委托给 ContextAssembler，原实现保留为 _format_context_legacy
- ✅ 所有 v2 测试通过（5 + 6 + 12 + 26 = 49 tests，无退化）
- ✅ Section 按优先级排序、token 预算裁剪、三种缓存策略 (NEVER/SESSION/PHASE) 均验证
- ✅ 不同阶段注入不同的 sections（通过 condition_fn + PHASE 缓存）
- ✅ System prompt token 数下降 87%（~871 tokens vs 旧 ~6325 tokens），远超 40% 目标
- ✅ 认知习惯按需加载（每轮最多 5 条，PHASE 级缓存）
- ✅ loop.py / agent.py 已切换到 assembler 统一组装（不再依赖 build_system_prompt + 全量 identity）

**验证标准**:
- ✅ System prompt token 数比现在下降 40%+（实际下降 87%）
- ✅ 不同阶段注入不同的 sections
- ✅ 认知习惯按需加载（每轮最多 5 条）

**注意事项**:
- ✅ 静态区的内容精简到 ~542 tokens（核心身份 + 本能反应 + 协作声明）
- ✅ Section 的优先级排序很重要——token 不够时低优先级被丢弃
- ✅ 缓存策略要正确（SESSION / PHASE / NEVER）

### 7.5 Phase 4: Phase FSM + 阶段感知工具集 ✅

**目标**: 阶段转换由 FSM 管理，各阶段暴露不同工具子集。

> 注: ToolRegistry 基础设施已在 Phase 1 中完成（`core/v2/tools.py`），本 Phase 聚焦 FSM + 阶段感知。

**具体工作**:

1. ✅ `core/v2/tools.py` — ToolRegistry（register/execute 模式 + phase-aware 过滤）
2. ✅ `core/v2/phases.py` — PhaseFSM（INITIAL_SCAN → DEEP_REVIEW → EDITING → SYNTHESIS）+ 转换规则 + 建议转换 + 阶段-工具映射
3. ✅ `core/v2/tools.py` — ToolDefinition 增加 `phases` 字段 + `get_tools_for_phase()` / `get_tool_schemas_for_phase()`
4. ✅ `core/v2/harness.py` — `_init_tool_registry()` 为每个工具标注可用阶段 + `request_phase_transition` 工具 + FSM 集成

**已完成的验证**:
- ✅ `tests/test_v2_phases.py` — 35/35 pass（FSM 11 + 建议 5 + 工具映射 7 + Registry 5 + Harness 集成 7）
- ✅ 所有 v2 测试通过（5 + 6 + 12 + 26 + 35 = 84 tests，无退化）
- ✅ 各阶段只暴露相关工具（INITIAL_SCAN 无编辑工具，EDITING 无 spawn_perspective）
- ✅ 阶段转换有前置条件但宽松（允许回退，只对关键路径做最小约束）
- ✅ 转换成功时自动 invalidate PHASE 缓存（认知习惯按新阶段重选）
- ✅ execute() 不做 phase 检查（设计决策：phase 只影响可见性，不阻止执行）

**设计决策记录**:
- 转换前置条件最小化：只在 SCAN→DEEP（需读 >=2 sections）和 DEEP→EDITING（需 >=1 verified finding）设置硬约束，其余宽松允许
- `suggest_transition()` 是 Harness 层的"软提示"接口，可作为 __NUDGE__ 信号的数据来源
- 工具执行不受 phase 限制：如果 LLM 幻觉出当前阶段不可见的工具调用，仍然执行（容错设计）

### 7.6 Phase 5: D 模块集成

**目标**: 将 HD-WM 作为可插拔模块集成到 C 方案中。

**前置条件**: Phase 1-4 完成。

**具体工作**:

1. 创建 `core/v2/hypothesis.py` — HD-WM 数据结构 + 生命周期管理
2. 重构 `core/v2/loop.py` — 增加 HD-WM Loop 变体（继承标准 Loop 行为）
3. 在 `core/v2/tools.py` + `core/v2/harness.py` 中注册假说相关工具（generate_hypothesis, add_evidence, resolve_hypothesis）
4. 在 `core/v2/assembler.py` 中实现 hypothesis_status section（条件注入）
5. 实现 review_readiness 计算（基于假说解决率）

**验证标准**:
- HD-WM 激活时，Agent 能产生和管理假说
- HD-WM 关闭时，系统完全退化为标准 C 方案
- 假说解决率能正确反映审稿完成度

**注意事项**:
- HD-WM 是"建议"不是"强制"——如果 LLM 不想用假说工具，不要强迫它
- 假说质量完全依赖 LLM，Harness 只管理生命周期
- 先做最小版本（只有假说+证据），队列优化和饱和检测后面再加

### 7.7 Phase 6: 集成测试 + 回归验证

**目标**: 确保重构后的系统在所有场景下表现不退化。

**具体工作**:

1. 运行全部现有 e2e 测试
2. 新增 Compaction 恢复测试（压缩后能否继续工作）
3. 新增 Session Memory 更新测试
4. 新增 HD-WM 生命周期测试
5. 长论文压力测试（40+ 轮）
6. A/B 对比: v1 vs v2 在相同论文上的审稿质量

**验证标准**:
- 所有现有测试通过
- 新增测试覆盖核心路径
- v2 审稿质量 >= v1（不退化）

---

## 八、文件结构

```
scholar-agent-public/
├── core/
│   ├── __init__.py
│   ├── agent.py                    # 入口 Facade（精简）
│   │
│   ├── harness/                    # Harness Layer
│   │   ├── __init__.py
│   │   ├── state.py               # StateManager
│   │   ├── assembler.py           # ContextAssembler
│   │   ├── sections.py            # SectionRegistry + section 定义
│   │   ├── guardian.py            # BoundaryGuardian
│   │   ├── tools.py              # ToolRegistry
│   │   └── phases.py             # Phase FSM + 转换规则
│   │
│   ├── loop/                      # Cognitive Loop
│   │   ├── __init__.py
│   │   ├── standard.py           # 标准 ReAct Loop
│   │   └── hdwm.py               # HD-WM Loop (D 模块)
│   │
│   ├── memory/                    # Memory System
│   │   ├── __init__.py
│   │   ├── working.py            # Working Memory
│   │   ├── session.py            # Session Memory Manager
│   │   ├── longterm.py           # Long-term Memory (Episodic/Semantic/Procedural)
│   │   ├── compaction.py         # Smart Compaction Engine
│   │   └── hypothesis.py         # HD-WM 数据结构 (D 模块)
│   │
│   ├── identity/                  # Identity (精简)
│   │   ├── __init__.py
│   │   ├── static.py             # 静态 prompt 区
│   │   └── personas.py           # 多人格定义（保留）
│   │
│   ├── cognition/                 # 认知模块
│   │   ├── __init__.py
│   │   ├── habits.py             # 认知习惯库 (Procedural Memory)
│   │   ├── checker.py            # CognitiveChecker (保留)
│   │   └── metacognition.py      # 元认知状态 (保留)
│   │
│   └── domain/                    # 领域工具（保留不动）
│       ├── pdf_loader.py
│       ├── web_search.py
│       ├── deai_detector.py
│       ├── bib_verify.py
│       └── claim_signal.py
│
├── llm/                           # LLM 客户端（保留不动）
├── config/                        # 配置（保留不动）
├── docs/
│   ├── ARCHITECTURE_V2_BLUEPRINT.md  # 本文档
│   ├── ARCHITECTURE_UPGRADE_PLAN.md  # 旧 C 方案（归档参考）
│   └── PLAN_D_EXPLORATION.md         # 旧 D 方案（归档参考）
└── tests/                         # 测试（从 core/ 中迁出）
    ├── test_loop.py
    ├── test_memory.py
    ├── test_compaction.py
    ├── test_hdwm.py
    └── test_e2e.py
```

---

## 九、参考资源索引

### 9.1 六篇文章（核心参考）

| # | 文章标题 | 核心洞察 | 对本架构的影响 |
|---|---------|---------|--------------|
| 1 | TencentDB Agent Memory: Mermaid 画布 + 上下文卸载 | 4 级压缩 (Raw→JSONL→MMD→Metadata)；层次化注意力 (overview→focus→drill-down) | Smart Compaction 的分层策略；Memory 的"卸载到外部"思想 |
| 2 | Harness 工程学: 马与缰绳 | LLM 作为无状态 CPU；REPL 容器；R.E.S.T 框架；状态分离原则；Token 变换管道 | Harness Layer 的整体设计；StateManager 的"外部维护状态"原则 |
| 3 | TencentDB Agent Memory 开源公告 | L0-L3 长期记忆层；GitHub 开源 | Long-term Memory 的分层参考 |
| 4 | 17 种 Agent 架构模式演化 | "Agent 架构本质不是 prompt engineering，而是控制流设计"；从 Reflection→Meta-Controller→Cellular Automata 的演化 | Phase FSM 的设计；控制流优先于 prompt 的核心原则 |
| 5 | GenericAgent 安装教程 | Mixin 自动切换；Hub 总控台；自主行动反射器；定时调度器 | Failover 机制参考；未来多 Agent 协作参考 |
| 6 | Anthropic Barry Zhang: How We Build Effective Agents | Agent = 环境+工具+系统提示词+循环；保持简单；像 Agent 一样思考；预算意识；自我演化工具；多 Agent 协作 | 架构三要素对应；简单性约束；预算监控；Loop 设计 |

### 9.2 源码参考

| 项目 | 地址 | 参考什么 |
|------|------|---------|
| Claude Code (逆向分析) | 内部分析文档（见之前对话） | Section 注册 + memoization；Session Memory (9 sections, 12K budget)；Smart Compaction with workspace restoration；静态/动态 prompt 分离 |
| TencentDB Agent Memory | https://github.com/Tencent/TencentDB-Agent-Memory | Mermaid 画布实现；分层压缩代码；上下文卸载机制 |
| GenericAgent | https://github.com/lsdefine/GenericAgent | Mixin failover；反射器/调度器架构；多前端设计 |
| CoALA 论文 | "Cognitive Architectures for Language Agents" (2023) | Working/Long-term Memory 分类；Decision Cycle 形式化；Episodic/Semantic/Procedural 三分类 |

### 9.3 学术参考

| 论文/框架 | 与本架构的关系 |
|-----------|--------------|
| CoALA (Sumers et al., 2023) | D 模块的理论基础；Memory 三分类的来源 |
| ReAct (Yao et al., 2022) | 标准 Loop 的基础模式 |
| Reflexion (Shinn et al., 2023) | 元认知/自我反思的参考 |
| Soar / ACT-R | 经典认知架构，HD-WM 的心理学基础 |
| Tree of Thoughts (Yao et al., 2023) | 假说分支探索的参考 |

### 9.4 内部文档

| 文档 | 路径 | 内容 |
|------|------|------|
| 旧 C 方案 | `docs/ARCHITECTURE_UPGRADE_PLAN.md` | 原始 C 方案设计（已被本文档取代） |
| 旧 D 方案 | `docs/PLAN_D_EXPLORATION.md` | HD-WM 详细数据结构设计（仍然有效，本文档引用） |
| 认知锚点 | `docs/COGNITIVE_ANCHOR.md` | ScholarAgent 的认知哲学 |
| 认知规格 | `docs/COGNITIVE_SPEC.md` | 认知行为的形式化描述 |
| 设计文档 | `DESIGN.md` | 原始设计理念 |

---

## 十、风险与缓解

| 风险 | 概率 | 影响 | 缓解策略 |
|------|------|------|----------|
| 重构过程中系统不可用 | 高 | 中 | 增量重构：每个 Phase 结束后系统都可运行 |
| 过度设计导致无法完成 | 中 | 高 | 严格遵循 C6（保持简单）；每个 Phase 有明确的"最小可行"标准 |
| HD-WM 增加的结构限制了 LLM 灵活性 | 中 | 中 | HD-WM 是"建议"不是"强制"；可随时关闭 |
| Smart Compaction 丢失关键信息 | 中 | 高 | 工作台恢复机制；findings 永不压缩；压缩前后对比测试 |
| 阶段 FSM 过于僵硬 | 低 | 中 | 转换条件留有弹性；LLM 可以发出"建议转换"信号 |
| Token 预算不够用 | 中 | 中 | 分层压缩；Section 优先级裁剪；预算监控 + 预警 |

---

## 十一、成功标准

### 11.1 工程标准

- [ ] Agent 能自主完成一篇论文的完整审阅（无人工干预）
- [ ] 长论文（40+ 轮）审阅不丢失关键信息
- [ ] Compaction 后能无缝恢复工作
- [ ] System prompt token 数比 v1 下降 40%+
- [ ] 所有现有测试通过

### 11.2 Agent 标准

- [ ] 系统的行为模式是"循环中使用工具"，不是"一次性生成"
- [ ] 状态完全由外部维护，LLM 不需要"记住"任何东西
- [ ] 不同阶段有不同的行为模式（工具集、认知习惯、压缩策略）
- [ ] Agent 能自主判断何时完成任务（通过信号协议或假说解决率）

### 11.3 学术标准（D 模块）

- [ ] HD-WM 能产生有意义的假说
- [ ] 假说生命周期管理正常工作
- [ ] review_readiness 能正确反映审稿完成度
- [ ] 与无 HD-WM 的 baseline 相比，审稿质量有可测量的提升

---

## 十一.5、行为 Trace 示例（v2 架构下的预期行为）

> 以下 Trace 展示 v2 架构下 Agent 审阅一篇经济学论文的前 8 轮行为。
> 目的：让执行者具象化理解"各层如何协作"，作为实现后的验证参照。

```
═══════════════════════════════════════════════════════════════
 ROUND 1 — Phase: ORIENTATION
═══════════════════════════════════════════════════════════════

[Context Assembler] 组装 system prompt:
  - identity/static.py → 基础身份 (固定, ~200 tokens)
  - sections.py → phase_context(ORIENTATION) → "你正在初步了解论文结构"
  - sections.py → tool_registry(ORIENTATION) → 只暴露: read_section, list_sections
  - sections.py → cognitive_habits(ORIENTATION) → "先全局后局部"
  - state.py → current_state → {findings: [], sections_read: [], round: 1}
  总注入: ~1800 tokens (远低于 v1 的 ~4500)

[LLM 决策] → 调用 list_sections(paper_id="econ_paper_1")

[Tool 执行] → 返回 42 个 section 标题

[StateManager 更新] → state.sections_available = [...42 items...]

═══════════════════════════════════════════════════════════════
 ROUND 2 — Phase: ORIENTATION
═══════════════════════════════════════════════════════════════

[Context Assembler] 注入更新后的 state（含 42 sections 列表）

[LLM 决策] → 调用 read_section(section="Abstract")

[Tool 执行] → 返回 Abstract 文本

[StateManager 更新] → state.sections_read = ["Abstract"]

═══════════════════════════════════════════════════════════════
 ROUND 3 — Phase: ORIENTATION → DEEP_REVIEW (自动转换)
═══════════════════════════════════════════════════════════════

[BoundaryGuardian] 检测到转换条件满足:
  - sections_read 包含 Abstract + Introduction ✓
  - Agent 已形成初步理解 ✓
  → 发出 PHASE_TRANSITION 信号: ORIENTATION → DEEP_REVIEW

[Context Assembler] 重新组装（Phase 变了）:
  - tool_registry(DEEP_REVIEW) → 新增: update_findings, spawn_perspective
  - cognitive_habits(DEEP_REVIEW) → "每读一个 section 必须产出 finding 或明确说明为何无发现"
  - 移除 ORIENTATION 专属的"先全局后局部"提示

[LLM 决策] → 调用 read_section(section="Methodology: IV Strategy")

═══════════════════════════════════════════════════════════════
 ROUND 5 — Phase: DEEP_REVIEW, HD-WM 激活
═══════════════════════════════════════════════════════════════

[HD-WM Module] Agent 在 response 中产出结构化假说:
  HYPOTHESIS: "作者的 IV (父母教育水平) 可能违反排他性约束——
              父母教育可能通过家庭收入直接影响子女健康"
  STATUS: active
  EVIDENCE_NEEDED: "检查 first-stage F-stat; 查看是否有 over-id test"

[Harness] 解析 Agent 输出 → 写入 state.hypotheses[0]

[LLM 决策] → 调用 read_section(section="Results: Table 3")
  理由: 需要验证假说中提到的 F-stat

═══════════════════════════════════════════════════════════════
 ROUND 7 — Phase: DEEP_REVIEW, 压缩触发
═══════════════════════════════════════════════════════════════

[BoundaryGuardian] Token 监控:
  当前 conversation history: 18,000 tokens
  预算阈值: 20,000 tokens
  → 触发 Smart Compaction

[Compaction Engine] 执行:
  1. 保护: state.findings (永不压缩)
  2. 保护: state.hypotheses (永不压缩)
  3. 压缩: Round 1-4 的 tool 返回值 → 摘要
  4. 保留: Round 5-7 的完整内容（最近工作台）
  压缩率: 67%
  压缩后 history: 6,100 tokens

[Context Assembler] 下一轮注入时:
  - 在 state 中标注: "注意: Round 1-4 已压缩，如需回顾请重新 read_section"

═══════════════════════════════════════════════════════════════
 ROUND 8 — Phase: DEEP_REVIEW, 假说更新
═══════════════════════════════════════════════════════════════

[LLM 决策] → 调用 update_findings(finding={
  type: "methodology_concern",
  severity: "major",
  content: "IV first-stage F-stat = 8.7 < 10, 弱工具变量风险",
  evidence: "Table 3, Column 2",
  related_hypothesis: 0
})

[StateManager] → state.findings.append(...)
[HD-WM] → hypotheses[0].evidence.append("F-stat=8.7, 支持弱IV担忧")
         → hypotheses[0].confidence: 0.3 → 0.6

[LLM 继续] → "接下来我需要检查作者是否做了 robustness check..."
           → 调用 read_section(section="Robustness Checks")
```

### Trace 中体现的架构原则对照

| 观察到的行为 | 对应的架构组件 | 对应的核心约束 |
|------------|-------------|-------------|
| LLM 不知道自己在哪个 Phase | Phase FSM 在 Harness 层管理 | C1: LLM 是无状态 CPU |
| 工具集随 Phase 变化 | ToolRegistry.get_tools(phase) | C3: 控制流 > Prompt |
| 压缩对 LLM 透明 | Compaction Engine 自动执行 | C1: 状态外部维护 |
| 假说从 Agent 输出中解析 | HD-WM 的 Harness 解析层 | C5: HD-WM 是建议不是强制 |
| Agent 自主决定读哪个 section | 无 if-elif 路由 | C2: Agent 自主决策 |
| 转换条件由 Guardian 判断 | BoundaryGuardian | C4: 约束而非控制 |

---

## 十二、用户偏好与协作约定

> 以下偏好来自项目 owner 在 58 个 Phase 中的反复强调，执行者必须遵守。

### 开发风格

- **增量验证**: 每个改动必须有对应的测试验证，不接受"先全写完再测"
- **不退化原则**: 任何重构后，现有测试必须全部通过才能继续
- **解释优先**: 做任何设计决策前，先用 1-2 句话解释"为什么这样做"
- **最小改动**: 能改 3 行解决的问题不要改 30 行

### 沟通风格

- 不要问"要不要我继续"——直接继续，除非遇到需要决策的分叉点
- 遇到分叉点时，给出 2-3 个选项 + 推荐 + 推荐理由
- 报告进展时用"做了什么 → 发现了什么 → 下一步做什么"的三段式
- 不要重复已知信息，直接说新发现

### 代码风格

- Python 3.10+，type hints 必须
- 函数不超过 30 行（超过就拆）
- 类的 `__init__` 不做复杂逻辑，只做赋值
- 测试文件命名: `test_<module>_<scenario>.py`
- 日志用 `logging`，不用 `print`

---

## 十三、附录：设计修正记录

（实施过程中的有意偏离记录在此）

```
目前无修正记录。
```