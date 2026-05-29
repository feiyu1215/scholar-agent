# 参考文献与灵感来源

> **用途**：记录对 ScholarAgent 设计产生实质影响的外部文章和项目。每条记录包含：文章核心内容、项目地址（如有）、对本项目的具体启发。
>
> **记录时间**：Phase 32 期间（2025-05-23），通过浏览器逐篇阅读微信公众号文章 + 源码探索。

---

## 1. TencentDB Agent Memory — 四层记忆分治架构

**来源**：微信公众号文章（腾讯技术团队分享）

### 核心内容

TencentDB 团队在数据库运维 Agent 中设计了一套 4 层记忆架构，解决 Agent 在长对话中 token 爆炸和上下文丢失的问题：

- **L0 (Raw)**：原始工具调用返回值，完整保存，不经处理
- **L1 (JSONL)**：结构化提取，从原始返回中抽取关键字段，存为 JSONL 格式索引
- **L2 (Mermaid)**：关系图谱，将 L1 中的实体关系可视化为 Mermaid 图（如表依赖、索引关系）
- **L3 (Metadata)**：高层元数据摘要，包含统计信息、健康状态、趋势判断

**核心数据**：这套分层策略实现了 **61% 的 token 节省**，同时保持了 Agent 的决策质量——因为 Agent 日常只需要 L3 级别的信息就能做出 80% 的正确判断，仅在需要深入时才逐层下钻到 L0。

**项目/代码**：未找到独立开源仓库（内部系统），文章中给出了架构图和效果数据。

### 对 ScholarAgent 的启发

- **直接影响了 Phase 32 的 OffloadStore 设计**：我们采用了类似的"存完整 → 展示摘要 → 按需召回"模式。OffloadStore 的 `manifest.jsonl`（索引）+ `refs/*.md`（原始内容）本质上是 L1+L0 的简化版本。
- **Token Budget 思路**：80% 阈值比 90% 更合理的观点，影响了我们对 `should_offload()` 阈值的设定（500 token）。
- **没有照搬 L2 (Mermaid) 层**：因为 ScholarAgent 处理的是论文文本而非关系型数据，实体关系图谱的收益不明显。未来如果做 citation graph 可视化可能用到。

---

## 2. all-agentic-architectures — 17 种 Agent 架构模式全景

**来源**：微信公众号文章 + GitHub 仓库

**项目地址**：`https://github.com/nicepkg/all-agentic-architectures`

### 核心内容

系统梳理了 17 种主流 Agent 架构模式，从简单到复杂排列：

1. Simple Reflex Agent
2. Model-Based Reflex Agent
3. Goal-Based Agent
4. Utility-Based Agent
5. Learning Agent
6. Hierarchical Agent
7. Multi-Agent System
8. Reactive Agent
9. Deliberative Agent
10. Hybrid Agent
11. BDI (Belief-Desire-Intention) Agent
12. Cognitive Agent
13. **Metacognitive Agent** ← 对我们最相关
14. Autonomous Agent
15. Social Agent
16. Embodied Agent
17. Evolutionary Agent

每种模式都有代码示例和适用场景说明。

### Metacognitive Agent 模式详解

该模式的核心是"Agent 对自身认知过程的显式建模和调控"：

- Agent 维护一个 **self-model**：包含当前策略、置信度、认知负荷
- Agent 在每轮决策前进行 **meta-reasoning**：评估"我的当前方法有效吗？需要切换策略吗？"
- Agent 具有 **strategy switching** 能力：当检测到当前策略效果下降时，自主切换

### 对 ScholarAgent 的启发

- **直接影响了 Phase 32 的 CognitiveState 设计**：`strategy` 字段（deep_investigation / breadth_scan / targeted_verification / revision_mode / synthesis / undecided）就是 Metacognitive Agent 的 strategy 概念。
- **hypotheses + confidence 机制**：来自该模式中"Agent 应该跟踪自己的信念及其确定性"的理念。
- **auto_infer_strategy()**：实现了 strategy switching 的自动化——当假设数量和置信度达到一定条件时，策略自然切换。
- **我们的差异**：all-agentic-architectures 中的实现偏学术/演示性质，每种模式是独立的。我们是在一个已经有 31 个 Phase 积累的真实 Agent 中集成 Metacognitive 能力，需要和已有的 Harness 机制兼容。

---

## 3. Anthropic "How We Build Effective Agents"

**来源**：微信公众号中文翻译 + Anthropic 官方 blog

**原文地址**：`https://www.anthropic.com/research/building-effective-agents`

### 核心内容

Anthropic 团队分享了他们构建有效 Agent 的经验教训，核心观点：

1. **Agent 的定义极简**：Agent = environment + tools + system prompt，在一个 loop 中持续运行。不需要复杂框架。

2. **保持简洁**（Keep it simple）：
   - 不要过度工程化 Agent 的控制流
   - 让 LLM 做它擅长的事（推理、规划），harness 只做 LLM 做不了的事（持久化、外部调用）
   - 避免"为了架构而架构"

3. **工具设计原则**：
   - 工具描述要像给人写文档一样清晰
   - 每个工具应该做一件事，做好
   - 工具返回值要信息密集但不冗余

4. **常见失败模式**：
   - Agent 陷入循环（doom loop）
   - Agent 忘记上下文（context loss）
   - Agent 调用工具但忽略返回值
   - Agent 过于自信地给出错误答案

5. **解决方案方向**：
   - 给 Agent 一个明确的"何时停止"信号
   - 用 system prompt 建立认知习惯而非指令序列
   - 让失败模式可观察（trace/log）

### 对 ScholarAgent 的启发

- **验证了我们的核心架构选择**：ScholarAgent 的 `loop.py` 就是"environment + tools + system prompt in a loop"的实现，这和 Anthropic 的推荐一致。
- **强化了 COGNITIVE_ANCHOR §3.5 的反 Theater Code 原则**：不要把 LLM 能做的事包装成 Tool。
- **Phase 28 Self-Termination 的理论支持**：Anthropic 强调"何时停止"是关键问题，我们的 soft turn limit + self-assessment 机制是对此的回应。
- **保持简洁的持续提醒**：每次想加新机制时，先问"这是 harness 该做的，还是 LLM 推理能自然完成的？"

---

## 4. Agent Memory 设计模式综述

**来源**：微信公众号文章（综述类）

### 核心内容

综述了多种 Agent Memory 的设计范式，对比了不同项目的实现方式：

- **短期记忆**（Working Memory）：当前 context window 内的信息，LLM 直接可见
- **长期记忆**（Long-term Memory）：持久化存储，需要检索后注入 context
- **情景记忆**（Episodic Memory）：按"事件"组织，类似人类的"上次做过类似任务"
- **语义记忆**（Semantic Memory）：按"概念"组织，知识图谱式的结构化知识
- **程序性记忆**（Procedural Memory）：学到的行为模式，如"遇到 X 类问题倾向用 Y 方法"

文章还讨论了记忆的**遗忘机制**：不是所有信息都值得保留，需要有衰减策略。

### 对 ScholarAgent 的启发

- **Phase 15 的 Session Memory 本质上是 Episodic Memory**：按论文 session 组织，可以回顾"上次审 NLP 论文时的经验"。
- **Phase 32 的 CognitiveState 是一种 Working Memory 的显式化**：把原来隐式存在于 message history 中的"当前策略"变成了可查询的结构。
- **尚未实现 Procedural Memory**：Agent 目前不会"学到"行为模式。这可能是未来的方向——比如"在经济学论文中，Agent 学到了总是要检查 DID 假设"。
- **遗忘机制的启发**：Phase 16 的 Section Digest 本质上是一种"有损遗忘"——原文细节被丢弃，只保留摘要。

---

## 5. Harness Engineering — Agent 的"骨骼系统"

**来源**：微信公众号文章

### 核心内容

讨论了 Agent 系统中 Harness（线束/骨架）的设计哲学：

- **Harness vs Framework**：Framework 是"你填代码进我的框架"，Harness 是"我约束你的行为边界但不规定你怎么做"
- **Harness 的三大职责**：
  1. 状态管理：替 LLM 记住东西（因为 LLM 是无状态的）
  2. 安全边界：Red Line 保护（不能做的事）
  3. 资源管理：Token budget、API 调用限制、超时控制
- **Harness 不应该做的事**：
  - 不决定 Agent 做什么（那是 LLM 的事）
  - 不控制流程顺序（那是 Agent 自主涌现的）
  - 不评估 Agent 的输出质量（那会变成"LLM 评估 LLM"的无效循环）

### 对 ScholarAgent 的启发

- **直接对应 COGNITIVE_ANCHOR §5.2 的状态分离原则**：`LLM = 无状态思考引擎，Harness = 状态守护者`。
- **验证了我们删除 Phase Filter 的正确性**：Phase Filter 本质上是 Harness 在控制流程顺序，违反了"不决定 Agent 做什么"的原则。
- **Phase 32 的定位**：OffloadStore 和 CognitiveState 都是 Harness 的职责——替 LLM 管理它自己管不好的状态，但不规定 LLM 怎么用这些状态。

---

## 6. 从 ReAct 到 Cognitive Architecture — Agent 认知架构演进

**来源**：微信公众号文章

### 核心内容

梳理了 Agent 认知架构从简单到复杂的演进路径：

1. **ReAct (Reason + Act)**：最基础的模式，"想一步做一步"，但没有长期规划能力
2. **Plan-then-Execute**：先生成完整计划再执行，但计划和执行解耦导致无法适应变化
3. **Reflexion**：加入反思层，但反思只是"做完之后回顾"，不是持续的元认知
4. **Cognitive Architecture**：
   - 将 Agent 的"思考"本身作为可设计的对象
   - 包含：感知（Perception）、注意力（Attention）、记忆（Memory）、规划（Planning）、执行（Execution）、反思（Reflection）
   - 各模块不是顺序执行而是并行/动态交互

文章指出了当前 Agent 的核心瓶颈不是能力，而是**认知连贯性**——在长对话中保持一致的目标追踪和策略。

### 对 ScholarAgent 的启发

- **验证了我们走 Cognitive Architecture 路线的正确性**：ScholarAgent 不是 ReAct 也不是 Plan-then-Execute，而是一个有认知连贯性的循环。
- **"认知连贯性"是核心挑战**：Phase 14-18 一直在解决这个问题——压缩不丢信息、多轮不漂移、Digest 桥接。Phase 32 的 CognitiveState 是最新的尝试。
- **感知-注意力-记忆-规划-执行-反思** 的六模块模型：
  - 感知 = `read_section` + `search_literature` + Claim Signal
  - 注意力 = Token Pipeline + format_context 的选择性注入
  - 记忆 = Session Memory + OffloadStore + Section Digest
  - 规划 = `reflect_and_plan` + CognitiveState.strategy
  - 执行 = `edit_section` + `update_findings`
  - 反思 = `reflect_and_plan` + cognitive_update + Cognitive Prompter

---

## 综合 Gap 分析（基于 6 篇文章 vs 当前 ScholarAgent）

### 已吸收到代码中

| 概念 | 来源 | 实现 Phase |
|------|------|-----------|
| 无损存储+按需召回 | TencentDB L0-L3 | Phase 32 OffloadStore |
| 显式认知状态 | Metacognitive Agent | Phase 32 CognitiveState |
| 策略自动推断 | Metacognitive Agent | Phase 32 auto_infer_strategy |
| Agent = loop + tools + prompt | Anthropic | Phase 8-10 认知循环 |
| Harness 只约束不控制 | Harness Engineering | Phase 18 自主权恢复 |
| 认知连贯性 | Cognitive Architecture | Phase 14-18 |

### 尚未实现但有价值

| 概念 | 来源 | 潜在价值 | 优先级 |
|------|------|---------|--------|
| Procedural Memory（程序性记忆） | Memory 综述 | Agent 学习"遇到 X 类论文习惯检查 Y" | 中 |
| L2 关系图谱 | TencentDB | Citation 网络可视化 | 低 |
| Strategy Switching 触发器 | Metacognitive Agent | 不只是 auto-infer，而是基于效果反馈动态切换 | 高 |
| 多 Agent 知识共享 | all-agentic-architectures | Spawn 出的子 Agent 共享父 Agent 的记忆 | 中 |
| 遗忘衰减机制 | Memory 综述 | 自动降级不再相关的记忆条目 | 低 |

---

## 附注

这 6 篇文章的共同主题是：**Agent 的核心竞争力在于认知质量而非工具数量**。框架/工具/记忆都是服务于一个目标——让 Agent 在每一轮思考中做出更高质量的判断。

ScholarAgent 的定位——"LLM 是无状态 CPU，Harness 是寄存器+内存+总线"——与这些文章的理念高度一致。Phase 32 之后，CPU 终于能"读到自己的寄存器"了（CognitiveState），也能"把中间结果存到内存再取回来"了（OffloadStore）。下一步的关键是：让 CPU 学会"什么时候该换一个算法"（Strategy Switching based on effectiveness feedback）。

---

---

# Part II: 学术论文素材库

> **用途**：记录对 ScholarAgent 有测试/验证价值的真实学术论文。这些论文不是 Agent 设计参考，而是 Agent 的「审阅对象」——用来验证 Agent 的认知能力是否能处理真实学术水准的方法论审查。

---

## 7. Selection with Variation in Diagnostic Skill: Evidence from Radiologists

**标题**：Selection with Variation in Diagnostic Skill: Evidence from Radiologists

**作者**：David C. Chan (Stanford School of Medicine), Matthew Gentzkow (Stanford Economics), Chuan Yu (Harvard Business School)

**发表**：
- NBER Working Paper No. 26467 (2019)
- **Quarterly Journal of Economics**, Vol. 137, Issue 2, pp. 729-783 (May 2022) — 经济学顶刊
- Microeconomic Insights 通俗解读 (2025年1月)

**论文链接**：
- PDF: https://web.stanford.edu/~gentzkow/research/radiology.pdf
- NBER: https://www.nber.org/papers/w26467
- QJE 正式版: https://academic.oup.com/qje/article-abstract/137/2/729/6513421
- 通俗解读: https://microeconomicinsights.org/the-role-of-diagnostic-skill-how-and-why-it-matters/

### 核心内容

**研究问题**：当医生（放射科医师）的诊断率差异很大时，这种差异来自什么？是**偏好差异**（有的医生更保守/激进）还是**技能差异**（有的医生更擅长看片子）？

**关键创新——方法论框架**：

传统经济学文献中，医生行为差异被简化为"treatment styles"（偏好/风格差异），假设信息相同、只是决策阈值不同。Chan & Gentzkow 指出这种框架有根本缺陷：它**无法区分 preferences 和 skill**。

他们发展了一个新框架，核心思想来自信号检测理论（Signal Detection Theory）：
- 每个放射科医生面对一张胸片，接收到一个**信号**（对"是否有肺炎"的噪声判断）
- **Skill**：信号的精确度（高技能医生的信号更准确，ROC 曲线更优）
- **Preferences/Threshold**：在给定信号下，多大概率时决定诊断为阳性（决策阈值）

两个维度相互独立但都影响最终诊断率。关键洞察：**低技能的医生和高阈值的医生都可能表现出相同的诊断率——但他们的错误模式不同**。

**识别策略**：

利用美国 VA 医院系统中患者到放射科医生的**准随机分配**（quasi-random assignment），实现了：
- 不同医生看到的"真实阳性率"（case mix）在统计上相同
- 通过观察每个医生的**假阴性率**（missed diagnoses，通过后续就诊信息可回溯确认）和**假阳性率**来分离 skill 和 preferences

**核心发现**：
1. 放射科医生的诊断率差异**很大**——最高和最低的医生诊断率差 3 倍以上
2. 差异中**很大一部分来自 skill 差异**（而非纯粹的风格偏好差异）
3. 低技能医生并不"笨"——他们**最优地**选择了更低的诊断阈值来补偿自己的技能劣势（因为漏诊的代价高于误诊）
4. 如果能将低技能医生替换为高技能医生，肺炎漏诊率可以降低约 X%（具体数值在论文结果中）

**结构性模型**：
- 建立了一个基于贝叶斯决策论的结构性模型
- 医生的效用函数中，漏诊（false negative）的成本 > 误诊（false positive）的成本
- 在该模型下，低技能医生最优选择较低阈值是理性行为

### 方法论亮点

1. **Preference-Skill 分离的识别条件**：在准随机分配下，通过观测的错误模式（FP vs FN 率的联合分布）可以同时识别两个潜在维度
2. **后验信息利用**：利用"患者后来确诊了肺炎"这一后验信息来确认真阳性，从而计算每个医生的 miss rate
3. **结构估计 + Reduced-form 证据的互补**：先用 descriptive evidence 展示 skill variation 的存在，再用结构模型量化其大小和福利影响
4. **VA 医院的自然实验性质**：患者分配近似随机（由排班和到达时间决定），提供了因果识别基础

### 对 ScholarAgent 的价值

**作为测试素材的理由**：

1. **方法论复杂度高**：同时涉及信号检测理论、贝叶斯决策模型、结构估计、准实验识别——审稿人需要跨领域知识
2. **Identification 的微妙性**：论文的核心贡献在于"separation of preferences and skill"，一个好的审稿人需要追问"识别条件是否充分？quasi-random 假设是否可信？"
3. **数据和方法的对照**：reduced-form 和 structural 的互补关系——这是经济学论文的经典结构，Agent 应该能识别两者是否一致
4. **现实政策含义**：结论有直接的福利含义（替换低技能医生可以挽救生命），Agent 应该能评估结论的稳健性

**具体可测试的审阅能力**：
- Agent 能否识别出"quasi-random assignment"假设是论文识别的命脉？
- Agent 能否追问"后验信息的选择性"问题（并非所有肺炎都会在后续就诊中被确认）？
- Agent 能否看出 structural model 中的 functional form 假设对结论的敏感性？
- Agent 能否区分这篇论文和传统"physician style"文献的根本不同？

### 备注

这篇论文发表于 QJE（经济学最顶级期刊之一），代表了"用经济学方法研究医疗决策"这个交叉领域的最高水准。它的方法论创新（将信号检测理论引入经济学的 preference-skill 分离框架）在后续文献中被广泛引用和扩展。

如果 ScholarAgent 能对这篇论文给出有深度的审稿意见（而非泛泛的"方法论不错"），这将证明 Agent 具备真正的方法论审查能力。
