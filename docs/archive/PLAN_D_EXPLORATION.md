# D 方案：CoALA + 假说驱动工作记忆（独立探索规划）

> 状态: 探索规划 | 优先级: 有时间时推进
> 学术贡献目标: 提出 Hypothesis-Driven Working Memory 用于学术认知任务
> 可发表性: 主会议 full paper (ACL/EMNLP/NeurIPS Agent Workshop)

---

## 一、D 方案的核心创新

### 1.1 学术贡献定位

CoALA (Cognitive Architectures for Language Agents, 2023) 提出了通用的认知架构框架，但它是一个**描述性框架**——告诉你 agent 应该有哪些组件，但不告诉你这些组件在特定领域应该如何特化。

D 方案的贡献是：**在 CoALA 框架下，提出一种面向学术认知任务的 Working Memory 特化设计——Hypothesis-Driven Working Memory (HD-WM)**。

核心论点：学术审阅/研究是一种假说驱动的认知活动。审稿人不是线性地"读完再评"，而是在阅读过程中不断生成假说（"这篇论文的方法可能有 X 问题"）、寻找证据、验证或推翻假说。这种认知模式需要一种专门的工作记忆结构来支撑。

### 1.2 与现有工作的差异

| 系统 | Working Memory 设计 | 局限 |
|------|---------------------|------|
| CoALA (原始) | 通用 buffer（当前 percept + 检索结果） | 无领域特化，无结构 |
| Claude Code | Session Memory (9 段固定模板) | 面向编程任务，段落是静态的 |
| ReAct/Reflexion | 无显式 WM，靠 scratchpad | 无持久结构，每轮重建 |
| **D 方案 (HD-WM)** | 假说生命周期管理 + 证据积累 | 面向学术认知任务 |

---

## 二、Hypothesis-Driven Working Memory 设计

### 2.1 核心数据结构

```python
@dataclass
class Hypothesis:
    """一个可验证的学术假说"""
    id: str
    statement: str              # "该论文的 baseline 对比不公平"
    status: HypothesisStatus    # ACTIVE / SUPPORTED / REFUTED / SUSPENDED
    confidence: float           # 0.0 - 1.0
    evidence_for: list[Evidence]
    evidence_against: list[Evidence]
    source_section: str         # 假说产生时正在读的 section
    created_at_turn: int
    resolved_at_turn: int | None
    
    @property
    def evidence_balance(self) -> float:
        """证据平衡度：正面证据 vs 反面证据的加权比"""
        ...

class HypothesisStatus(Enum):
    ACTIVE = "active"           # 正在验证中
    SUPPORTED = "supported"     # 有充分证据支持
    REFUTED = "refuted"         # 被证据推翻
    SUSPENDED = "suspended"     # 暂时搁置（证据不足）
    MERGED = "merged"           # 被合并到更大的假说中

@dataclass
class Evidence:
    """支持或反对假说的证据"""
    content: str
    source: str                 # section 名 / 外部文献
    strength: float             # 0.0 - 1.0
    type: EvidenceType          # DIRECT / INDIRECT / ABSENCE

@dataclass
class HypothesisDrivenWM:
    """假说驱动的工作记忆"""
    
    # 假说池
    hypotheses: list[Hypothesis]
    
    # 当前焦点
    active_hypothesis: Hypothesis | None  # 当前正在验证的假说
    
    # 认知队列
    hypothesis_queue: list[str]  # 待验证假说 ID 队列（优先级排序）
    
    # 元认知
    saturation_signal: bool     # 假说饱和信号（新假说产生速率下降）
    convergence_signal: bool    # 收敛信号（大部分假说已 resolved）
    
    # 生命周期管理
    def generate_hypothesis(self, statement: str, source: str) -> Hypothesis:
        """从阅读中产生新假说"""
        ...
    
    def add_evidence(self, hyp_id: str, evidence: Evidence, direction: str):
        """为假说添加证据（for/against）"""
        ...
    
    def resolve_hypothesis(self, hyp_id: str, status: HypothesisStatus):
        """解决假说（支持/推翻/搁置）"""
        ...
    
    def get_next_hypothesis(self) -> Hypothesis | None:
        """从队列中取出下一个待验证假说"""
        ...
    
    def compute_review_readiness(self) -> float:
        """计算审稿完成度（基于假说解决率 + 覆盖度）"""
        ...
```

### 2.2 决策循环（CoALA 风格）

```
┌─────────────────────────────────────────────────────────────┐
│                    HD-WM Decision Cycle                       │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  PLANNING PHASE (read-only, internal)                 │   │
│  │                                                        │   │
│  │  1. Perceive: 读取当前 section / 工具返回结果          │   │
│  │  2. Hypothesize: 从感知中生成/更新假说                 │   │
│  │  3. Prioritize: 选择下一个要验证的假说                 │   │
│  │  4. Plan: 决定验证策略（读哪个 section / 搜什么文献）  │   │
│  └──────────────────────────────────────────────────────┘   │
│                          │                                   │
│                          ▼                                   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  EXECUTION PHASE (state-changing)                     │   │
│  │                                                        │   │
│  │  Grounding Actions:                                    │   │
│  │    - read_section(target) → 获取验证假说所需的信息     │   │
│  │    - search_literature(query) → 外部证据检索           │   │
│  │    - verify_citations(refs) → 引用验证                 │   │
│  │                                                        │   │
│  │  Learning Actions:                                     │   │
│  │    - add_evidence(hyp, evidence) → 更新假说证据        │   │
│  │    - resolve_hypothesis(hyp, status) → 解决假说        │   │
│  │    - generate_hypothesis(stmt) → 产生新假说            │   │
│  │    - update_findings(finding) → 将已解决假说转为发现   │   │
│  └──────────────────────────────────────────────────────┘   │
│                          │                                   │
│                          ▼                                   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  TERMINATION CHECK                                    │   │
│  │                                                        │   │
│  │  review_readiness >= threshold?                        │   │
│  │    YES → synthesize findings → __DONE__               │   │
│  │    NO  → back to PLANNING                             │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### 2.3 与 C 方案的关键差异

| 维度 | C 方案 | D 方案 |
|------|--------|--------|
| 决策驱动力 | LLM 自主决策（prompt 引导） | 假说队列驱动（结构化引导） |
| 工具调用动机 | LLM 判断"该做什么" | 验证当前假说需要什么信息 |
| 完成判断 | 多信号综合（边界守护） | 假说解决率 + 覆盖度 |
| 认知模型 | 隐式（在 prompt 中描述） | 显式（假说生命周期代码化） |
| 可解释性 | 低（LLM 黑盒决策） | 高（每个行动都关联到假说） |

---

## 三、起点选择分析

### 3.1 从当前架构直接做 D

**优势**:
- 最快验证核心创新（HD-WM）是否有效
- 不需要等 C 完成
- 代码量可控（主要改 loop.py + 新增 hypothesis.py）

**劣势**:
- 继承当前架构的所有技术债（prompt 膨胀、压缩粗暴等）
- HD-WM 的效果可能被基础设施问题掩盖
- 如果 C 后来也完成了，需要二次迁移

**适用场景**: 想尽快写论文、验证学术 idea

### 3.2 从 C 完成后做 D

**优势**:
- Memory 三层已经就位，HD-WM 只需替换 Working Memory 层
- Section 注册机制让假说状态的注入更优雅
- Smart Compaction 保证长论文审阅时假说不丢失
- 工程质量更高，实验结果更可信

**劣势**:
- 需要等 C 完成（~2 周）
- 可能过度工程化（如果 HD-WM 验证失败）

**适用场景**: 追求高质量实现 + 可靠的实验结果

### 3.3 我的建议

**从 C 的 Phase C-1 + C-2 完成后开始 D。**

理由：
1. C-1（Prompt 分层）和 C-2（Memory 三层）是 D 的必要基础设施，总共 ~5-7 天
2. 不需要等 C 全部完成（C-3 工具 Registry、C-4 习惯动态化对 D 不是必须的）
3. 这样 D 可以直接在干净的 Memory 层上实现 HD-WM，避免二次迁移

```
时间线:
Week 1-2: C-1 + C-2 (Prompt 分层 + Memory 三层)
Week 2-3: D 核心实现 (HD-WM + 决策循环)
Week 3-4: D 实验验证 + 论文初稿
Week 4+:  C-3 ~ C-5 (工具 Registry + 习惯动态化 + 集成测试)
```

---

## 四、D 方案实施计划

### Phase D-1: HD-WM 核心实现（~3 天）

**前置条件**: C-2 完成（Memory 三层结构就位）

**具体工作**:
1. 实现 `Hypothesis`, `Evidence`, `HypothesisDrivenWM` 数据结构
2. 实现假说生命周期管理（generate / add_evidence / resolve）
3. 实现 `review_readiness` 计算（假说解决率 + 覆盖度）
4. 将 HD-WM 作为 Working Memory 的一个特化实现

**产出**: `core/memory/hypothesis.py`

### Phase D-2: 决策循环改造（~3 天）

**具体工作**:
1. 修改 `loop.py`：在 LLM 调用前注入当前假说状态
2. 新增 Planning Phase 的显式结构（LLM 输出结构化的 plan）
3. 工具调用与假说关联（每次 tool call 标注"为了验证哪个假说"）
4. 实现 termination check（基于 review_readiness）

**产出**: `core/loop_d.py`（独立文件，不破坏 C 方案的 loop）

### Phase D-3: 假说-发现转换（~2 天）

**具体工作**:
1. 实现 `hypothesis_to_finding()` 转换逻辑
2. 已 SUPPORTED 的假说自动转为 finding（带完整证据链）
3. 已 REFUTED 的假说记录为"排除项"（论文没有这个问题）
4. SUSPENDED 的假说在 synthesis 阶段做最终判断

**产出**: `core/cognition/hypothesis_synthesis.py`

### Phase D-4: 实验设计与验证（~3-5 天）

**实验目标**: 证明 HD-WM 相比无结构 WM 在学术审阅任务上的优势

**实验设计**:

```
对比系统:
  A: ScholarAgent v1 (当前架构，无结构 WM)
  B: ScholarAgent C (混合架构，Session Memory 但无假说)
  C: ScholarAgent D (HD-WM)

评估维度:
  1. 审稿质量: 与人类审稿人的一致性 (Cohen's κ)
  2. 覆盖度: 发现的问题数 / 人类标注的问题数
  3. 精确度: 有效问题数 / 总发现数
  4. 可解释性: 每个发现是否有清晰的证据链
  5. 效率: 达到相同质量所需的 LLM 调用次数

数据集:
  - 10-20 篇已有人类审稿意见的论文（从 OpenReview 获取）
  - 覆盖 NLP / ML / CV 等多个领域

统计检验:
  - Paired t-test / Wilcoxon signed-rank test
  - Bootstrap confidence intervals
```

### Phase D-5: 论文撰写（~5-7 天）

**目标会议**: ACL 2025 / EMNLP 2025 / NeurIPS 2025 Agent Workshop

**论文结构**:

```
Title: Hypothesis-Driven Working Memory for Academic Cognitive Agents

1. Introduction
   - LLM agents 在长程学术任务中的挑战
   - 假说驱动认知的心理学基础
   - 贡献: HD-WM 架构 + 实验验证

2. Related Work
   - Cognitive architectures (CoALA, Soar, ACT-R)
   - LLM-based review agents
   - Working memory in AI systems

3. Hypothesis-Driven Working Memory
   - 形式化定义
   - 假说生命周期
   - 与 CoALA 决策循环的集成
   - 完成度计算

4. Implementation: ScholarAgent-D
   - 系统架构
   - 与 baseline 的差异

5. Experiments
   - 设置 + 数据集 + 评估指标
   - 主实验结果
   - 消融实验（去掉假说队列 / 去掉证据积累 / 去掉完成度计算）
   - Case study

6. Analysis
   - 假说质量分析
   - 认知效率分析
   - 失败案例分析

7. Conclusion + Future Work
   - 通用化: 从审稿到写作/综述/实验设计
   - 多 agent 假说协作
```

---

## 五、通用化展望

HD-WM 的核心抽象是"目标驱动的工作记忆"，假说只是学术审阅场景下的具体实例：

| 任务类型 | "假说"的具体形态 | 证据来源 |
|----------|------------------|----------|
| 论文审阅 | "该方法有 X 缺陷" | 论文段落、外部文献 |
| 论文写作 | "X 论点需要 Y 支撑" | 文献、数据、逻辑推导 |
| 文献综述 | "领域 X 存在 Y gap" | 多篇论文的交叉分析 |
| 实验设计 | "X 变量影响 Y 结果" | 先验知识、pilot 数据 |
| 代码审查 | "这段代码有 X 风险" | 代码上下文、测试结果 |

通用化的关键是将 `Hypothesis` 抽象为 `Goal`（或 `Claim`），将 `Evidence` 抽象为 `Support`，保留生命周期管理和完成度计算的框架。

---

## 六、风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| HD-WM 增加的结构反而限制了 LLM 的灵活性 | 中 | 高 | 设计为"建议"而非"强制"——LLM 可以跳过假说队列 |
| 假说质量依赖 LLM 的推理能力 | 中 | 中 | 提供假说模板 + 质量检查 |
| 实验中难以控制变量（C vs D 差异太多） | 低 | 高 | 严格消融实验设计 |
| 论文审阅数据集获取困难 | 低 | 中 | OpenReview 公开数据 + 自建小规模标注 |
| 实现复杂度超预期 | 中 | 中 | 先做最小可行版本（只有假说+证据，无队列优化） |

---

## 七、最小可行验证（如果时间紧张）

如果无法完成完整的 D 方案，可以先做一个最小验证：

**MVP-D: 在当前架构上加一个假说追踪层**

```python
# 不改 loop.py，只在 harness.py 中加一个假说追踪器
class HypothesisTracker:
    """轻量假说追踪——不改变决策循环，只记录和展示"""
    
    hypotheses: list[dict]
    
    def on_finding_added(self, finding: dict):
        """当 finding 被记录时，自动关联到假说"""
        ...
    
    def format_hypothesis_status(self) -> str:
        """格式化为 system prompt 注入"""
        ...
```

这个 MVP 可以在 1-2 天内完成，用来验证"假说追踪是否真的改善审稿质量"这个核心假设。如果验证成功，再投入完整的 D 方案实现。
