# ScholarAgent 架构升级规划

> 版本: v1.0 | 决策日期: 2025-01
> 定位: 有学术贡献的认知架构实现 + 好用的学术审稿工具
> 容忍度: 完全重构

---

## 一、当前架构诊断

### 1.1 现有 4 文件架构

```
agent.py (585L)  ─── 组装者/Facade，暴露 start()/chat() API
identity.py (1104L) ─── 巨型 prompt 模板 + 工具定义（3 persona × tools）
harness.py (2309L) ─── 状态守护 + 工具执行 + 边界信号（14 工具 if-elif）
loop.py (446L)   ─── 认知循环引擎 + 子视角分裂
```

### 1.2 核心设计哲学（保留）

- **约束-而非-控制**: Harness 提供信号，不下指令
- **LLM 是无状态 CPU**: 所有记忆由外部维护并注入
- **Identity-driven**: 行为来自认知身份定义，不来自代码路由
- **Signal protocol**: `__DONE__`, `__TALK__`, `__SPAWN__`, `__NUDGE__`

### 1.3 当前瓶颈

| 问题 | 表现 | 根因 |
|------|------|------|
| Prompt 膨胀 | SCHOLAR_IDENTITY ~4500 字，每轮全量注入 | 无静态/动态分层，无按需加载 |
| 状态不透明 | WorkspaceState 20+ 字段全部 format 为文本 | 无结构化 memory schema，无优先级 |
| 工具全量暴露 | 14 工具每轮都在 tools 参数中 | 无阶段感知的工具集切换 |
| 压缩粗暴 | compress_messages 只做尾部保留 | 无"工作台恢复"机制 |
| 记忆断层 | MemoryStore 跨会话但无会话内结构化笔记 | 缺少 Session Memory 层 |

---

## 二、两条独立路径

```
                    ┌─────────────────────────────────┐
                    │     当前架构 (4-file, v0.x)      │
                    └──────────────┬──────────────────┘
                                   │
                    ┌──────────────┴──────────────────┐
                    │                                  │
              ┌─────▼─────┐                    ┌──────▼──────┐
              │  C 方案    │                    │   D 方案     │
              │ 混合架构   │                    │ CoALA+创新   │
              │ (主线开发) │                    │ (独立探索)   │
              └───────────┘                    └─────────────┘
```

**C 和 D 不是线性升级关系。** D 可以从当前架构出发，也可以从 C 完成后出发——取决于哪个起点更合理（见 D 方案文档中的分析）。

---

## 三、C 方案：混合架构（主线）

### 3.1 设计理念

借鉴 Claude Code 的工程成熟模式（动态上下文、Section 注册、Session Memory、Compaction 恢复），融合 CoALA 的概念清晰度（三层记忆分类），保留 ScholarAgent 的领域特色（认知习惯、信号协议、约束-而非-控制）。

**一句话定位**: 用 Claude Code 的工程骨架，装 CoALA 的概念语言，跑 ScholarAgent 的认知灵魂。

### 3.2 架构总览

```
┌─────────────────────────────────────────────────────────────────┐
│                        ScholarAgent v2 (C 方案)                  │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              System Prompt Assembly Layer                  │   │
│  │  ┌────────────────────┐  ┌─────────────────────────────┐ │   │
│  │  │   Static Zone      │  │      Dynamic Zone            │ │   │
│  │  │  (cacheable)       │  │  (per-turn resolved)         │ │   │
│  │  │                    │  │                              │ │   │
│  │  │  - Identity Core   │  │  - Review Progress Section  │ │   │
│  │  │    (1 paragraph)   │  │  - Paper Context Section    │ │   │
│  │  │  - Review Standards│  │  - Active Findings Section  │ │   │
│  │  │  - Signal Protocol │  │  - Tool Guidance Section    │ │   │
│  │  │  - Output Format   │  │  - Cognitive Nudge Section  │ │   │
│  │  └────────────────────┘  └─────────────────────────────┘ │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              Memory Architecture (CoALA-inspired)          │   │
│  │                                                            │   │
│  │  Working Memory          │  Long-term Memory              │   │
│  │  (session-scoped)        │  (persistent)                  │   │
│  │                          │                                │   │
│  │  - Session Notes (9段)   │  - Episodic: 历史审稿记录      │   │
│  │  - Active Workspace      │  - Semantic: 领域知识/标准     │   │
│  │  - Cognitive State        │  - Procedural: 审稿规则/习惯  │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              Decision Cycle (loop.py evolved)              │   │
│  │                                                            │   │
│  │  ┌─────────┐    ┌──────────┐    ┌──────────────────┐     │   │
│  │  │ Perceive │───▶│  Decide  │───▶│     Execute      │     │   │
│  │  │(read ctx)│    │(LLM call)│    │(tool / internal) │     │   │
│  │  └─────────┘    └──────────┘    └──────────────────┘     │   │
│  │       ▲                                    │              │   │
│  │       └────────────────────────────────────┘              │   │
│  │                   + Signal Protocol                        │   │
│  │                   + Boundary Guards                        │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              Context Management Layer                      │   │
│  │                                                            │   │
│  │  - Session Memory (后台 subagent 更新)                     │   │
│  │  - Smart Compaction (总结 + 工作台恢复)                    │   │
│  │  - Section Registry (声明式注册 + 惰性缓存)               │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 3.3 模块详细设计

#### 3.3.1 System Prompt 分层

**静态区（跨论文不变，可 cache）**:

```python
STATIC_IDENTITY = """
你是 ScholarAgent，一个具有深度学术审阅能力的认知系统。
你通过结构化的证据积累和批判性分析来审阅学术论文。
"""

STATIC_REVIEW_STANDARDS = """
## 审稿标准
- 方法论严谨性: 研究设计、统计方法、可重复性
- 证据质量: 数据充分性、因果推断有效性
- 学术贡献: 新颖性、理论/实践价值
- 表达清晰度: 逻辑连贯、术语准确、图表规范
"""

STATIC_SIGNAL_PROTOCOL = """
## 信号协议
- __DONE__|summary: 审阅完成
- __TALK__|message: 需要与用户交流
- __SPAWN__|config: 启动子视角
- __NUDGE__|reason: 系统催促（只读，不可主动发出）
"""
```

**动态区（每轮/每阶段重算）**:

```python
# 每个 section 独立注册，按需计算
@review_section(name="review_progress", cache=False)
def compute_review_progress(state: WorkspaceState) -> str:
    """当前审阅进度——每轮更新"""
    return f"""
    已读 sections: {len(state.sections_read)}/{len(state.paper_sections)}
    已记录 findings: {len(state.findings)} (high: {count_high}, medium: {count_med})
    当前阶段: {state.current_phase}
    """

@review_section(name="paper_context", cache=True)  # 论文不变，缓存
def compute_paper_context(state: WorkspaceState) -> str:
    """论文元信息——加载后不变"""
    return f"标题: {state.title}\n领域: {state.domain}\n..."

@review_section(name="active_findings", cache=False)
def compute_active_findings(state: WorkspaceState) -> str:
    """当前 findings 摘要——每轮更新"""
    ...

@review_section(name="tool_guidance", cache="phase")  # 按阶段缓存
def compute_tool_guidance(state: WorkspaceState) -> str:
    """当前阶段推荐的工具使用方式"""
    ...
```

**关键设计决策**:
- Identity 从 4500 字压缩到 ~500 字（核心身份 + 标准 + 信号协议）
- 认知习惯不再全量注入，而是按阶段/情境动态加载（见 3.3.4）
- 工具定义从 prompt 中移除，改为 API 层的 tools 参数（已有）

#### 3.3.2 Memory 三层架构

**Working Memory（会话内）**:

```python
@dataclass
class WorkingMemory:
    """CoALA Working Memory — 当前认知循环的活跃信息"""
    
    # 当前工作台（compaction 后恢复的核心）
    current_section: str           # 正在审阅的 section
    current_section_content: str   # 该 section 的文本（窗口）
    active_hypothesis: str | None  # 当前正在验证的假设（如有）
    
    # 累积产出
    findings: list[Finding]        # 结构化 findings
    edits: list[Edit]              # 修改记录
    section_digests: dict[str, str]  # 已读 section 的摘要
    
    # 认知状态
    cognitive_state: CognitiveState  # 元认知追踪
    voice_profile: VoiceFingerprint  # 写作风格指纹
```

**Session Memory（会话级结构化笔记，后台更新）**:

```python
SESSION_MEMORY_SCHEMA = {
    "review_title": "5-10 词描述当前审稿任务",
    "current_focus": "当前正在关注的问题/section",
    "methodology_notes": "方法论层面的观察和判断",
    "evidence_assessment": "证据质量的累积评估",
    "statistical_issues": "统计/数据问题记录",
    "writing_observations": "表达和结构问题",
    "novelty_judgment": "创新性和贡献度判断",
    "key_decisions": "重要的审稿判断和理由",
    "issue_log": "按时间顺序的问题发现日志",
}

MAX_SECTION_TOKENS = 2000
MAX_TOTAL_SESSION_MEMORY_TOKENS = 12000
```

**Long-term Memory（跨会话持久化）**:

```python
class LongTermMemory:
    """CoALA Long-term Memory — 跨会话持久化"""
    
    # Episodic: 历史审稿经验
    episodic: EpisodicStore   # "上次审 NLP 论文时，X 模式很常见"
    
    # Semantic: 领域知识
    semantic: SemanticStore    # 学科标准、期刊要求、统计方法知识
    
    # Procedural: 审稿规则
    procedural: ProceduralStore  # 认知习惯、审稿流程、质量标准
```

#### 3.3.3 Context Management（核心创新借鉴）

**Session Memory 后台更新**:

```python
class SessionMemoryManager:
    """后台 subagent 定期提取审稿笔记"""
    
    # 触发条件（双阈值）
    min_tokens_between_update: int = 4000
    min_tool_calls_between_update: int = 5
    
    # 自然断点检测
    def is_natural_breakpoint(self, state: WorkspaceState) -> bool:
        """section 审完 / finding 记录后 / 用户交互后"""
        return (
            state.just_finished_section or
            state.findings_count_delta >= 3 or
            state.last_signal == "__TALK__"
        )
    
    async def update(self, messages: list, state: WorkspaceState):
        """隔离 subagent 更新 session memory"""
        # 只能写 session_memory，不能修改论文或 findings
        ...
```

**Smart Compaction（总结 + 工作台恢复）**:

```python
class SmartCompactor:
    """压缩历史 + 恢复工作台"""
    
    async def compact(self, messages: list, state: WorkspaceState):
        # 1. 总结历史对话为摘要
        summary = await self.summarize(messages)
        
        # 2. 恢复工作台（post-compact restoration）
        restoration = self.build_restoration(state)
        
        # 3. 新的 messages = [system, summary, restoration, recent_messages]
        return self.rebuild_messages(summary, restoration)
    
    def build_restoration(self, state: WorkspaceState) -> str:
        """恢复当前工作状态"""
        parts = []
        
        # 恢复当前正在读的 section（相当于 Claude Code 的"最近文件"）
        if state.current_section:
            parts.append(f"## 当前正在审阅\n{state.current_section_content[:5000]}")
        
        # 恢复 findings 列表（相当于"活跃 Plan"）
        parts.append(f"## 已发现问题\n{format_findings(state.findings)}")
        
        # 恢复审稿进度（相当于"异步 Agent 状态"）
        parts.append(f"## 审稿进度\n{format_progress(state)}")
        
        # 恢复 session memory
        parts.append(f"## 审稿笔记\n{state.session_memory}")
        
        return "\n\n".join(parts)
```

#### 3.3.4 认知习惯的动态加载

当前问题：19 条认知习惯全量注入 prompt，占用大量 token 且大部分在当前轮次不相关。

**新设计：Procedural Memory 按需检索**:

```python
COGNITIVE_HABITS = {
    # 阶段相关
    "initial_scan": [
        "先通读全文建立整体印象，再深入细节",
        "关注 abstract-conclusion 一致性",
    ],
    "deep_review": [
        "每个 claim 都需要对应的 evidence",
        "统计显著性不等于实际意义",
        "注意 cherry-picking 和 p-hacking 信号",
    ],
    "synthesis": [
        "将零散发现组织为结构化论点",
        "区分 major/minor issues",
        "给出建设性的改进建议，不只是批评",
    ],
    
    # 情境触发
    "methodology_focus": [
        "检查实验设计的内部/外部效度",
        "对照组设置是否合理",
    ],
    "statistics_focus": [
        "样本量是否支撑结论",
        "多重比较是否校正",
    ],
}

def get_active_habits(state: WorkspaceState) -> list[str]:
    """根据当前阶段和情境，返回相关的认知习惯"""
    habits = COGNITIVE_HABITS.get(state.current_phase, [])
    
    # 情境触发
    if state.current_section_type == "methodology":
        habits += COGNITIVE_HABITS["methodology_focus"]
    if state.has_statistical_content:
        habits += COGNITIVE_HABITS["statistics_focus"]
    
    return habits[:5]  # 每轮最多 5 条，避免膨胀
```

#### 3.3.5 工具集阶段感知

```python
TOOL_PHASES = {
    "initial_scan": [
        "read_section", "reflect_and_plan", "update_findings", "talk_to_user"
    ],
    "deep_review": [
        "read_section", "search_literature", "fetch_paper_detail",
        "update_findings", "detect_ai_signals", "verify_citations",
        "reflect_and_plan", "talk_to_user", "spawn_perspective"
    ],
    "editing": [
        "read_section", "edit_section", "update_findings",
        "talk_to_user", "mark_complete"
    ],
    "synthesis": [
        "review_findings", "reflect_and_plan",
        "talk_to_user", "mark_complete"
    ],
}

def get_tools_for_phase(phase: str, all_tools: list) -> list:
    """返回当前阶段可用的工具子集"""
    allowed = TOOL_PHASES.get(phase, TOOL_PHASES["deep_review"])
    return [t for t in all_tools if t["name"] in allowed]
```

### 3.4 文件结构重构

```
scholar-agent-public/
├── core/
│   ├── agent.py              # 入口 (保留，精简)
│   ├── loop.py               # 认知循环 (保留，增加 phase transition)
│   ├── identity/
│   │   ├── __init__.py
│   │   ├── static.py         # 静态 prompt 区（身份+标准+信号协议）
│   │   ├── sections.py       # 动态 section 注册表
│   │   └── personas.py       # 多人格定义
│   ├── memory/
│   │   ├── __init__.py
│   │   ├── working.py        # Working Memory (当前工作台)
│   │   ├── session.py        # Session Memory (结构化笔记 + 后台更新)
│   │   ├── longterm.py       # Long-term Memory (跨会话)
│   │   └── compaction.py     # Smart Compaction (总结+恢复)
│   ├── harness/
│   │   ├── __init__.py
│   │   ├── state.py          # WorkspaceState (精简)
│   │   ├── tools.py          # 工具执行 (registry pattern 替代 if-elif)
│   │   ├── guards.py         # 边界守护信号
│   │   └── phases.py         # 阶段管理 + 工具集切换
│   ├── cognition/
│   │   ├── __init__.py
│   │   ├── habits.py         # 认知习惯库 (Procedural Memory)
│   │   ├── checker.py        # CognitiveChecker (保留)
│   │   └── metacognition.py  # 元认知状态 (保留)
│   └── domain/
│       ├── pdf_loader.py     # (保留)
│       ├── web_search.py     # (保留)
│       ├── deai_detector.py  # (保留)
│       ├── bib_verify.py     # (保留)
│       └── claim_signal.py   # (保留)
├── llm/                      # (保留不动)
├── config/                   # (保留不动)
└── docs/
    ├── ARCHITECTURE_UPGRADE_PLAN.md  # 本文档
    └── PLAN_D_EXPLORATION.md         # D 方案探索规划
```

### 3.5 实施阶段

#### Phase C-1: Prompt 分层 + Section 注册（~2-3 天）

**目标**: 将巨型 prompt 拆分为静态/动态两区，建立 section 注册机制。

**具体工作**:
1. 从 `SCHOLAR_IDENTITY` 中提取不变部分 → `identity/static.py`
2. 实现 `@review_section` 装饰器 + 缓存机制 → `identity/sections.py`
3. 将 `harness.format_context()` 拆分为独立 section compute 函数
4. 修改 `agent.py` 的 prompt 组装逻辑

**验证**: 现有测试全部通过 + prompt token 数下降 30%+

#### Phase C-2: Memory 三层重构（~3-4 天）

**目标**: 建立 Working Memory / Session Memory / Long-term Memory 三层结构。

**具体工作**:
1. 从 `WorkspaceState` 中分离出 `WorkingMemory` → `memory/working.py`
2. 实现 Session Memory schema + 后台更新机制 → `memory/session.py`
3. 重构 `MemoryStore` 为 CoALA 三分类 → `memory/longterm.py`
4. 实现 Smart Compaction → `memory/compaction.py`

**验证**: 长论文审阅（>20 轮）不丢失关键信息 + 压缩后能恢复工作状态

#### Phase C-3: 工具 Registry + 阶段感知（~2 天）

**目标**: 工具执行从 if-elif 改为 registry pattern，支持阶段性工具集切换。

**具体工作**:
1. 实现 `ToolRegistry` + `@tool` 装饰器 → `harness/tools.py`
2. 实现阶段管理 + 工具集映射 → `harness/phases.py`
3. 修改 `loop.py` 支持 phase transition 信号

**验证**: 各阶段只暴露相关工具 + 阶段转换平滑

#### Phase C-4: 认知习惯动态化（~1-2 天）

**目标**: 认知习惯从全量注入改为按需检索。

**具体工作**:
1. 将 19 条习惯分类到 `cognition/habits.py`
2. 实现 `get_active_habits()` 按阶段/情境检索
3. 作为动态 section 注入（每轮最多 5 条）

**验证**: 审稿质量不下降 + prompt 进一步精简

#### Phase C-5: 集成测试 + 回归验证（~2 天）

**目标**: 确保重构后的系统在所有测试场景下表现不退化。

**具体工作**:
1. 运行全部现有 e2e 测试
2. 新增 Session Memory 相关测试
3. 新增 Compaction 恢复测试
4. 长论文压力测试（40+ 轮）

---

## 四、C 方案的学术贡献点

虽然 C 方案以工程为主，但仍有可发表的贡献：

1. **动态认知加载机制**: 证明按阶段/情境加载认知规则比全量注入更高效（可量化对比实验）
2. **学术审稿的结构化 Session Memory**: 提出适用于长程学术任务的 9 段记忆模板
3. **约束-而非-控制的边界守护**: 形式化描述信号协议如何在不限制 LLM 自主性的前提下保证质量

这些可以作为 workshop paper 或 demo paper 的素材。

---

## 五、通用化路径

C 方案完成后，要扩展为通用学术助手（写作、综述、实验设计），需要：

1. **扩展 Procedural Memory**: 增加写作规则、综述方法、实验设计规范
2. **Session Memory schema 可切换**: 审稿用 9 段审稿模板，写作用大纲模板，综述用知识图谱模板
3. **工具集扩展**: 增加写作工具（outline_builder, paragraph_generator）、综述工具（knowledge_graph, gap_finder）
4. **Phase 定义扩展**: 从审稿的 4 阶段扩展到各任务类型的阶段定义

架构本身不需要改动——这正是 Section 注册 + Memory 分层的价值所在。

---

## 六、与 D 方案的关系

C 和 D 是两条独立路径：

- **C 完成后转 D**: 如果 C 的 Memory 三层 + Section 注册已经稳定，D 可以在此基础上替换 Working Memory 为 Hypothesis-Driven WM，成本更低
- **直接从当前架构做 D**: 如果想尽快验证假说驱动的学术贡献，可以跳过 C 的工程优化，直接在当前 4 文件架构上实现 CoALA 决策循环 + 假说 WM

**我的判断**: 建议先完成 C-1 和 C-2（Prompt 分层 + Memory 三层），这两步为 D 提供了更好的基础设施。D 的核心创新（假说驱动 WM）需要一个干净的 Memory 层来承载，而 C-2 正好提供了这个。

详见 `PLAN_D_EXPLORATION.md`。
