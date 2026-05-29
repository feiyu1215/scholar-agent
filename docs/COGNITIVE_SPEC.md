# 认知规范 (Cognitive Specification)

> **用途**：用一个具体的完整场景，trace 我们的 Agent 从头到尾的心智活动。这不是系统设计文档，而是"一个有认知能力的实体在面对任务时，它的思考到底是什么样的"。
>
> 所有后续的工程实现，都必须能够**支撑**这里描述的认知过程。如果某个工程设计让这里描述的自然思考变得不可能或被扭曲，那个设计就是错的。

---

## 场景设定

**用户输入**："这是我准备投 NeurIPS 的论文，帮我看看有什么问题，然后帮我改好。"

**论文特征**（Agent 尚不知道，需要自己发现）：
- 提出了一种新的对比学习方法用于少样本学习
- Introduction 中有一个因果 claim 缺乏充分 evidence
- Methodology 中的消融实验设计有缺陷（少了关键 baseline）
- Related Work 遗漏了 2024 年的一篇高度相关论文
- Results 中的表格格式不符合 NeurIPS 风格
- 写作整体偏 AI 生成风格（长句多、hedging 多、结构过于对称）

---

## Agent 的完整思考过程

### 阶段零：接收与理解

```
[Agent 内心]
用户给了一篇论文，目标是"看看问题 + 改好"。
这是一个开放式的、需要深度的任务。用户信任我的专业判断。

我需要先理解这篇论文在做什么，然后才能有针对性地审视。
不急着用任何"工具"——我先读。
```

**行动**：读取论文全文（或至少 Abstract + Introduction + 方法概述），建立对论文核心贡献的初步理解。

```
[Agent 内心]
好，这篇论文提出了一种新的对比学习框架 "FewCL"，用于少样本分类。
核心创新是在 contrastive loss 中引入了一个 task-adaptive margin。
作者声称这能让模型在 5-shot 场景下超过 ProtoNet 和 MAML。

我的第一印象：idea 有意思，但我需要验证几个东西——
这个 margin 的 motivation 是什么？为什么它比固定 margin 好？
```

**注意**：此时 Agent 没有"进入审阅模式"。它只是在**读**，像一个学者拿到一篇论文时自然地读和想。

---

### 阶段一：意图涌现——第一个疑问

```
[Agent 内心]
Introduction 第三段说"fixed margins fail to capture task-specific 
distributional properties, leading to suboptimal embeddings"。

这是一个因果 claim。但我只看到他 cite 了一篇 2021 的论文。
这够吗？这个 claim 是不是过强了？

让我看看他在 Results 里有没有 ablation 来支撑这个 claim...
```

**行动**：跳到 Results/Ablation section，寻找相关实验。

```
[Agent 内心]
嗯，Results 里确实有一个 ablation 比较了 fixed margin vs adaptive margin。
但这只证明了 adaptive 比 fixed 好——它没有证明 fixed "fail to capture 
distributional properties"。这是两件事。

作者的 claim 比他的 evidence 要强。这是一个需要修改的地方。
我要记住这个问题。

但在下结论之前，我想确认：这个领域里，其他人对 fixed margin 的批评是什么？
是不是已经有共识了？如果有，那作者的 claim 也许不需要那么强的 evidence。
```

**深度决策点**：Agent 在这里决定**深入验证**而不是直接下结论。判断依据：这个 claim 在 Introduction 中，是全文立论的基础之一。如果这个 claim 站不住，整篇论文的 motivation 就动摇了。所以值得花精力验证。

**行动**：搜索相关文献，看看 "fixed margin in contrastive learning" 的现有批评。

```
[Agent 内心]
搜索结果：
- 2022 年 ICML 有一篇论文讨论了 margin schedule 的问题，但角度不同
- 2023 年 ICLR 有一篇提出了 dynamic margin，和作者的 approach 有相似性
- 2024 年 NeurIPS 有一篇 "Adaptive Contrastive Margins for Few-Shot"——
  等等，这和我们这篇论文做的事情高度重叠！

这是一个重要发现。让我仔细看看这篇 2024 的论文。
```

**意图转变**：原来只是想验证一个 claim，搜索过程中发现了一个**更大的问题**——Related Work 遗漏。Agent 的思考自然转向了这个新发现。

```
[Agent 内心]
读了 2024 年这篇 "ACM-FS" 的 abstract。它确实在做类似的事——
在 contrastive loss 中引入 task-conditioned margin。
但方法不完全相同：ACM-FS 用的是 meta-learned margin，
我们作者用的是 distribution-estimated margin。

这意味着：
1. Related Work 里必须讨论 ACM-FS——当前完全没提
2. Experiments 里最好加 ACM-FS 作为 baseline
3. 好消息是：方法确实不同，所以 novelty 还在

我要把这个问题标记为"高优先级"——因为 NeurIPS reviewer 一定会发现这篇论文。
```

---

### 阶段二：继续审视——方法论深入

```
[Agent 内心]
回到方法部分。核心是 Equation 3 的 adaptive margin 计算。
公式本身看起来合理。但让我检查一下 ablation 设计...

Ablation study (Table 3) 比较了：
- Full model
- w/o adaptive margin (fixed=0.5)
- w/o temperature scaling

嗯，少了一个关键对比：w/o task conditioning。
因为作者声称 margin 需要 "task-adaptive"——
那应该有一个实验去掉 task conditioning 只保留 learnable margin，
来证明 "task-adaptive" 这一点具体贡献了多少。

现在的 ablation 只能证明 "有 adaptive margin 比没有好"，
但不能证明 "adaptive 必须是 task-specific 的"。

这是一个方法论上的缺陷。不是致命的，但如果不修复，
会给 reviewer 一个明显的 attack surface。
```

**深度决策点**：这个问题需要标记，但 Agent 不需要现在就搜索更多文献来验证。它已经能从论文自身的逻辑中判断出缺陷。所以这里**浅过**（标记问题即可），不像上一个需要外部验证。

---

### 阶段三：视角分裂的需要

```
[Agent 内心]
到目前为止，我从"方法论正确性"和"文献覆盖度"两个角度看了论文。
但我还没有系统地检查其他维度：
- 写作质量和清晰度
- 实验设计的完整性（不只是 ablation）
- 论文整体的 narrative 和 story

我可以继续一个一个看。但更高效的方式是——
让我从几个不同视角并行审视这篇论文。
因为每个视角关注的东西不一样，需要不同的注意力分配。

具体来说，我想从以下视角各自独立审视一遍：
1. 理论与创新性：核心 contribution 是否 sound？与现有工作的区别是否 clear？
2. 实验严谨性：实验设计是否公平？baseline 是否充分？统计是否显著？
3. 写作与呈现：逻辑是否流畅？有无 AI 味？格式是否符合 NeurIPS？

（不需要 5 个视角——这篇论文不涉及伦理问题，也不是 survey，
所以"伦理审查"和"文献综述完整性"不需要作为独立视角。
文献问题我已经在前面发现了。）
```

**分裂决策**：Agent 决定分裂出 3 个并行审视。注意——这不是"预设 5 个 reviewer"，而是 Agent 根据**这篇具体论文的特点**判断需要 3 个视角。

**分裂执行**：3 个子思考体各自工作，各带自己的关注维度。

---

### 阶段四：合并与全局判断

```
[Agent 内心]
三个视角的结果回来了。让我整合：

[理论与创新性视角]
- Contribution 是 sound 的，adaptive margin 的 idea 有道理
- 但 novelty 受 ACM-FS (2024) 威胁，需要明确区分
- 理论分析 (Section 3.2) 的 bound 推导有一步跳跃

[实验严谨性视角]
- 缺少 ACM-FS 作为 baseline（和我之前发现的一致）
- Ablation 缺少 task-conditioning 的单独贡献验证（也和之前一致）
- Table 2 的 confidence interval 没报，只有 mean
- 5-shot 结果好但 1-shot 结果 marginal——作者没讨论为什么

[写作与呈现视角]
- 整体写作偏 AI 生成风格：段落开头模式单一、hedging 过多、句式对称
- Introduction 的 story 不错但 3.1 节的 transition 突兀
- Table 格式需要调整为 NeurIPS 的 booktabs style
- Figure 1 的 caption 太短，没有 self-contained

综合来看，这篇论文的核心问题按优先级：
1. 🔴 高优 | 遗漏 ACM-FS (2024)——必须在 Related Work 讨论 + Experiments 对比
2. 🔴 高优 | Ablation 设计缺陷——缺少 task-conditioning 的单独贡献验证
3. 🟡 中优 | Introduction 的因果 claim 过强——需要弱化措辞或增加 evidence
4. 🟡 中优 | 写作 AI 味重——需要整体去 AI 化
5. 🟡 中优 | 1-shot 结果需要讨论
6. 🟢 低优 | 格式问题（表格、Figure caption）

现在我要决定：先和用户沟通这些发现，还是直接开始修改？
用户说了"帮我改好"——但这些问题里有些涉及实验（需要作者补实验），
有些涉及论文观点（需要确认作者的意图）。

我应该先和用户讨论我的发现，确认哪些他想让我直接改、
哪些需要他自己补充（比如实验数据）。
```

---

### 阶段五：和用户交互——协作式识别与决策

**Agent 输出给用户**：

> 我读完了你的论文，发现了几个需要讨论的问题。最重要的两个是：
>
> **1. 有一篇 2024 年的高度相关论文 "ACM-FS" 没有被讨论。** 它和你的方法在 motivation 上非常相似（都是 adaptive margin），但实现方式不同。NeurIPS reviewer 大概率会指出这一点。我建议在 Related Work 中增加讨论并在 experiments 中作为 baseline 对比。你有这篇论文的实验结果吗？还是需要我帮你分析怎么和它做区分？
>
> **2. Ablation study 缺少一个关键实验**——证明 "task-adaptive" 这一点的具体贡献。当前的 ablation 只证明了 adaptive > fixed，但没有分离 "adaptive" 和 "task-specific" 各自的贡献。
>
> 另外，写作整体有 AI 生成的痕迹，格式有些地方需要调整。这些我可以直接帮你修改。
>
> 你想怎么处理？比如 ACM-FS 的实验对比需要你跑数据，但 Related Work 的讨论和写作修改我可以先做。

**用户回复**："ACM-FS 我没跑过，你帮我分析和它的区别就行，实验下次补。先帮我改能改的——Related Work 加讨论、写作去 AI 味、格式调整。ablation 的问题我记下了。"

---

### 阶段六：修改——带着理解的行动

```
[Agent 内心]
好，用户确认了范围。我现在要做三件事：
1. Related Work 增加 ACM-FS 讨论（需要我对这篇论文的理解 + 区分分析）
2. 写作去 AI 味（全文）
3. 格式调整（表格 + Figure caption）

先做 Related Work。因为这需要我对两篇论文的方法差异有深入理解，
是最需要"思考"的部分。格式调整最后做，最机械。

写 Related Work 的讨论时，我要：
- 不是简单加一句 "ACM-FS also proposes adaptive margins"
- 而是清晰地说明：(a) 它解决什么问题 (b) 方法的关键差异 
  (c) 为什么我们的方法有 complementary value
- 这需要我仔细对比两种方法的 formulation
```

**行动**：搜索/获取 ACM-FS 的详细方法描述，进行对比分析，然后写出 Related Work 的新段落。

```
[Agent 内心]
写好了 Related Work 的新段落。让我读一遍检查——

"Recently, [Author] et al. proposed ACM-FS, which shares our motivation of 
adapting contrastive margins to task characteristics. However, ACM-FS employs 
a meta-learned margin function trained end-to-end with the few-shot objective, 
whereas our approach estimates margins directly from the support set distribution 
without additional meta-training. This design choice offers two advantages: 
(1) it avoids the computational overhead of meta-learning an additional margin 
network, and (2) it provides an interpretable connection between margin values 
and task difficulty through the distributional statistics."

嗯，这段本身就有点 AI 味——"shares our motivation"、"offers two advantages" 
这种对称结构。我在去 AI 味阶段会处理它。现在先把内容准确性搞定。

内容上是否准确？让我核对一下 ACM-FS 的方法...
是的，它确实是 meta-learned，我们确实是 distribution-estimated。区分准确。
```

---

### 阶段七：去 AI 味——一种需要"忘掉原文"的视角

```
[Agent 内心]
现在要做去 AI 味。这件事有一个认知上的挑战：
我需要用一个"不知道这篇论文是 AI 写的"的读者视角来检测。
如果我带着"我知道这些文本有 AI 味"的先入为主去读，
我可能会过度修改。

我应该分裂出一个独立视角：一个"纯读者"——
它只看文本本身，标记出读起来不自然的地方，
不知道原文是否是 AI 生成的。
然后我基于它的标记来修改。
```

**分裂**：分裂出一个"纯读者验证者"视角，它拿到全文，任务是标记"读起来不自然/像 AI 写的"的具体段落和模式。

**纯读者回来的结果**：
- 段落开头高频使用 "Moreover, ..." / "Furthermore, ..." / "Additionally, ..."
- 多处使用 "It is worth noting that..."、"It should be noted that..."
- 句式过度对称：连续三段都是 "First... Second... Third..." 结构
- Hedging 过多："may potentially", "could arguably", "it is plausible that"
- 相邻段落的 topic sentence 模式太一致

```
[Agent 内心]
好，有了具体的标记，我来逐段修改。

修改原则：
- 不改变学术内容和含义
- 让文字读起来像一个对方法充分理解的人自然写出来的
- 引入一些变化性：不是每段都用 topic sentence 开头，
  有时直接切入证据或对比
- 减少 hedging：学术论文可以 hedge，但不需要每句都 hedge

让我从 Introduction 开始...
```

**行动**：逐段修改，每改一段后快速检查是否改变了学术含义。

修改完一部分后：

```
[Agent 内心]
我改了 Introduction 和 Method 的开头几段。
让我让"纯读者"再看一遍改后的版本——
确认 AI 味确实减少了，而且没有引入新的问题。
```

**验证循环**：纯读者重新检查 → 确认改善 → 继续下一部分。

---

### 阶段八：格式调整——相对机械但不能出错

```
[Agent 内心]
最后是格式。相对简单但需要准确。
- Table 2/3 换成 booktabs style（\toprule, \midrule, \bottomrule）
- Figure 1 caption 扩写，使其 self-contained
- 检查 NeurIPS 2025 的格式要求有没有其他需要调整的

这些是确定性操作，我不需要"深入思考"，直接做就行。
```

---

### 阶段九：交付前的全局回顾

```
[Agent 内心]
所有修改做完了。在交付给用户之前，我要做一次全局回顾：

1. ✅ Related Work 加了 ACM-FS 讨论——内容准确，区分清晰
2. ✅ 写作去 AI 味——Introduction、Method、Results 都处理了，
   经过纯读者验证
3. ✅ 格式调整——表格和 Figure caption

我还没做的（因为超出用户授权或需要用户自己做）：
- ⏸ Ablation 补实验（用户说自己记下了）
- ⏸ ACM-FS 的实验对比（用户说下次补）
- ⏸ Introduction 因果 claim 弱化（这个我其实可以做——
  用户说"能改的都改"。让我想想...
  弱化一个 claim 不需要新数据，只需要修改措辞。我应该做。）
```

**行动**：修改 Introduction 中的因果 claim，从 "fixed margins fail to..." 改为 "fixed margins may not fully capture..."

```
[Agent 内心]
好，现在真的全部完成了。让我组织一个清晰的交付：
- 修改了什么（逐项列出）
- 没修改什么（为什么，以及建议用户怎么做）
- 给用户一个 before/after 的关键对比
```

---

## Trace 维度总结

### 意图链

```
读论文 → 发现因果 claim 可疑 → 想验证 → 搜索文献 → 
发现遗漏的高相关论文 → 标记为高优问题 → 
继续审视方法论 → 发现 ablation 缺陷 → 标记 →
决定需要多视角并行审视 → 分裂 → 合并结果 →
和用户讨论发现 → 确认修改范围 → 
先做需要思考的 (Related Work) → 
再做需要独立视角的 (去 AI 味) → 
最后做机械的 (格式) → 
交付前全局回顾 → 发现还能做一件事 → 做了 → 交付
```

### 深度决策点

| 时刻 | 决策 | 依据 |
|------|------|------|
| 发现因果 claim 可疑 | 深入验证（搜索文献） | 这是全文 motivation 的基础，值得花精力 |
| 发现 ablation 缺陷 | 浅过（标记问题即可） | 从论文自身逻辑就能判断，不需要外部验证 |
| 决定多视角审视 | 分裂 3 个（不是固定 5 个） | 这篇论文的特点决定了需要哪些视角 |
| 去 AI 味 | 分裂出独立验证者 | 需要"不知道原文"的视角来做有效检测 |
| 格式调整 | 不深入（直接做） | 确定性操作，不需要思考深度 |
| 交付前 | 发现还能弱化 claim | 全局回顾时的"意外"发现，自然追加 |

### 分裂点

| 时刻 | 分裂原因 | 子思考体 |
|------|---------|---------|
| 多视角审视 | 不同维度需要不同注意力分配 | 理论/实验/写作 三个并行审视 |
| 去 AI 味验证 | 需要"不知情读者"的互斥视角 | 独立的纯读者验证者 |

### 用户交互点

| 时刻 | 为什么暂停 | Agent 做了什么 |
|------|-----------|--------------|
| 发现所有问题后 | 有些问题需要用户判断（补实验 vs 不补） | 呈现发现，请用户确认范围 |
| 交付时 | 工作完成 | 结构化呈现结果 + 后续建议 |

### Harness 在背后做了什么

| Agent 的认知活动 | Harness 的支撑 |
|----------------|---------------|
| "让我看看 Results section" | 从论文全文中精确定位 section，注入 context |
| "搜索 ACM-FS" | 执行文献搜索工具，返回结构化结果 |
| "我要记住这个问题" | 将发现持久化到 workspace 状态 |
| "让 3 个视角并行审视" | 分裂子思考体，管理并行执行和结果收集 |
| "对比改前改后" | 维护文件版本历史，提供 diff 能力 |
| Agent 想看论文的某部分 | Token Pipeline 按相关性注入最相关的段落 |
| Agent 发现了 6 个问题 | 状态持久化：问题列表 + 优先级 + 相关证据 |
| 去 AI 味修改 | 维护修改前/后版本，支持验证者对比 |

---

## 从这个 Trace 中提取的工程要求

（这些是为了支撑上述认知过程，Harness/系统层面**必须**提供的能力）

### 必须有的能力

1. **论文感知与导航**：Agent 能自由跳转到论文的任何部分，不被"phase"限制
2. **文献搜索**：Agent 在任何时刻都能搜索外部文献，不受限于特定"阶段"
3. **状态持久化**：发现的问题、已做的修改、对论文的理解——跨轮次保持
4. **版本管理**：修改前/后的对比能力，支持回退
5. **并行分裂**：Agent 决定需要多视角时，能实际 spawn 并行执行
6. **独立验证**：分裂出的验证者只能看到"应该看到"的信息（比如纯读者不该看到修改意图）
7. **Token Pipeline**：在每轮思考时，自动注入最相关的上下文（当前讨论的 section、相关的问题记录、搜索结果等），压缩不相关的
8. **用户交互能力**：Agent 能在任何时刻决定暂停并和用户交流
9. **深度自调节**：没有外部"深度参数"——Agent 自主判断每个问题该花多少精力
10. **Doom Loop Guard**：如果 Agent 对同一个问题反复验证不收敛，外部打断

### 不应该有的东西

1. ❌ "现在进入审阅阶段"——Agent 的审视是连续的思考流
2. ❌ "从工具列表中选一个"——Agent 需要什么就用什么，不需要"选择"
3. ❌ "审阅完了进入修改阶段"——Agent 可能审着审着就顺手改了一个小问题
4. ❌ "强制 5 个 reviewer"——视角数量由论文特点决定
5. ❌ "intent_classifier 分流"——Agent 自己理解用户意图，不需要单独的分类器

---

## 这个 Trace 揭示的核心设计挑战

### 挑战 1：如何让 LLM "自然地思考"而不是"执行流程"？

关键在 System Prompt 的设计。不应该是 "You are a paper reviewer. First do X, then do Y." 而应该是赋予 Agent 一种**认知身份**和**行为倾向**——"你是一个经验丰富的学者，面对论文时你会自然地阅读、质疑、验证、改进。"

### 挑战 2：如何实现"深度自调节"？

这可能是最难的工程问题。当前没有好的开源方案。我的初步想法是：在每轮思考中，Agent 的 prompt 包含"当前剩余 Token 预算"和"已发现问题的优先级"，让 LLM 自然地做出"这个值得深入吗"的判断。但这需要验证有效性。

### 挑战 3：如何做"带信息隔离的分裂"？

去 AI 味验证者不应该知道"这是 AI 写的"——它只应该看到文本本身。这意味着分裂出的子思考体需要有**定制的 context**，不能简单地继承主体的全部记忆。

### 挑战 4：如何在认知流中自然嵌入"和用户交流"？

Agent 需要自主判断"这个决策我能做"vs"这个需要问用户"。判断标准大致是：不可逆的大决策（删除内容、改变论文观点）→ 问用户；可逆的局部修改（措辞调整、格式修改）→ 自主做。

---

*文件版本: v1.0 | 创建日期: 2025-07*
*这是一个活文档。随着设计的深入，应该会有更多具体场景的 trace 被加入。*
