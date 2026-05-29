# PoC 认知循环 — 实验结果与分析

> 日期: 2025-07
> 目的: 验证"好的 system prompt + 极简 loop + 状态注入"是否足以产生 COGNITIVE_SPEC 描述的认知行为

---

## 实验设置

- **模型**: deepseek-v3-friday (通过 OpenAI-compatible API)
- **Loop**: 极简 while 循环，无任何流程控制
- **Tools**: read_section, search_literature, update_findings, edit_section, talk_to_user, done
- **测试论文**: 故意植入了 6 个问题的 few-shot learning 论文 (test_paper.md)
- **用户指令**: "帮我看看这篇论文有什么问题，我准备投 NeurIPS，给我你的专业判断。"

---

## 关键结果

### 运行统计
- 轮次: 3
- 发现: 6 条
- Tokens: ~7886
- 耗时: ~30s（含 rate limit 重试）

### Agent 发现的问题

| # | Agent 发现 | 论文中植入的对应问题 | 评价 |
|---|-----------|-------------------|------|
| 1 | Abstract 数据与表格不一致 (2.8% vs 2.62%) | ✅ 正确检出 | 精确 |
| 2 | 理论 bound 假设/证明缺失 | 部分检出（标记了需验证） | 合理 |
| 3 | Ablation 中 fixed=0.5 选择未解释 | ⚠️ 浅层检出（真正问题是缺少 w/o task conditioning） | 浅了 |
| 4 | Overclaim: FewCL < MetaOptNet 但宣称 SOTA | ✅ 正确检出 | 精准 |
| 5 | 温度参数 τ 细节缺失 | 合理发现（虽不是我植入的） | 好 |
| 6 | 数据集划分/预处理未说明 | 合理发现 | 好 |

### 未检出的植入问题

| # | 植入的问题 | Agent 表现 |
|---|-----------|----------|
| A | Ablation 缺少 "w/o task conditioning" 的对比 | 只发现了浅层的 fixed=0.5 问题 |
| B | 1-shot 结果 marginal 需要讨论 | 未提及 |
| C | Related Work 遗漏 2024 相关论文 | 未搜索验证 |
| D | 写作 AI 味重 | 未检查（可能需要更多轮次） |

---

## 对照 COGNITIVE_SPEC 的验证

### ✅ 已验证的认知行为

1. **全局通读后深入** — 先 read_section("full")，再针对性产出发现
2. **数据一致性检查** — 自动核对 Abstract vs Table 的数字
3. **Overclaim 检测** — 发现 SOTA claim 与数据不符
4. **深度自调节初现** — 高确信度问题直接 "verified"，不确定的标 "needs_verification"
5. **极简 Loop 有效** — 无需状态机即可产出有意义结果

### ❌ 尚未出现的认知行为

1. **意图跳转** — 没有"读 Intro → 疑问 → 跳到 Results 验证"的动态行为
2. **深层方法论批判** — ablation 设计缺陷未被识别
3. **持续探索** — 3 轮就停了，没有对关键问题深入
4. **视角分裂** — 没有并行多视角审视（预期中，单 Agent 测试）
5. **用户交互** — 没有主动调用 talk_to_user

---

## 核心洞察

### 洞察 1: System Prompt 的"认知身份"是有效的

从 V1（通用学术研究者）→ V2（怀疑型 AC）的改变带来了**质的飞跃**。V1 产出的是摘要式复述，V2 产出的是具体的、可执行的审阅意见。

**结论**：认知身份 > 指令流程。"你是一个怀疑一切的 AC" 比 "第一步读论文，第二步找问题" 更能产生自然的审稿行为。

### 洞察 2: 极简 Loop 确实够用——但 Agent 倾向于"过早满足"

Agent 在 3 轮内就产出了 6 条发现然后停了。但一个真正的审稿人不会这样——他会对最严重的问题继续追踪。

**可能的解法**：
- 在 system prompt 中加入"你不会在初步扫描就满足。重要问题值得你花 2-3 轮专门追踪"
- 或者：Harness 在 Agent 过早停止时，给一个 "nudge"（"你确定都看完了？你的发现中有 needs_verification 的项目还没验证"）

### 洞察 3: "意图涌现" 需要更长的 context 或 think-step-by-step

当前模型在一次 LLM call 中就产出了所有 findings，没有展现"渐进式发现"。这可能是因为论文较短（全文 ~3000 tokens），模型一次就能看完。

**假设**：面对更长的论文（10+ pages），意图跳转行为可能自然出现（因为全文无法一次读完，必须分段读）。

### 洞察 4: 搜索工具的价值取决于能否返回真实结果

V1 跑时尝试了搜索（因为 findings 不够具体需要外部验证），V2 没搜（因为仅从文本就能判断）。这说明 Agent 对搜索的使用是**基于认知需要的**，不是机械的——符合 COGNITIVE_ANCHOR 的期望。

---

## 下一步方向

### 方向 A: 加强 "持续深入" 行为
- 让 prompt 更强调"不要在初步扫描就停下"
- 或者加入 Harness 级别的 "continuation nudge"

### 方向 B: 用更长/更复杂的论文测试
- 当前论文太短，一次能通读。用项目 .workspace 中的真实长论文测试
- 验证"意图跳转"是否在长文中自然涌现

### 方向 C: 接入真实搜索
- 接 Semantic Scholar API 或 web_search
- 验证 Agent 在能获得真实外部信息时的行为变化

### 方向 D: 实现并行分裂
- 当前只有单线程思考。实现 perspective splitting
- 验证是否能提升深度（多视角发现单视角遗漏的问题）

### 方向 E: Harness 增强
- Token Pipeline: 当论文长到无法一次读完时，如何智能注入
- 状态持久化: 多轮对话间保持发现和进度
- Doom loop guard 的精细化

---

## 结论

**核心假设得到初步验证**：一个好的 system prompt + 极简 loop + 状态注入，确实能让 LLM 产生接近 COGNITIVE_SPEC 描述的认知行为。

但"接近"和"完全符合"之间还有差距。最大的差距在于**持续深入的动力不足**——Agent 倾向于快速列出问题然后停下，而不是像真正的专家那样对关键问题穷追不舍。

这个差距的解决方向可能是：prompt 强化 + Harness nudge + 更长的测试场景。不需要复杂的架构改动。
