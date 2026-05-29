# ScholarAgent 开发 Session Prompt

> 将以下内容作为新会话的起始 prompt，让 AI 快速进入 ScholarAgent 开发上下文。

---

## Prompt 正文

```
你正在参与 ScholarAgent 的开发工作。这是一个学术论文审稿+修订 Agent（不是从零写作工具，不是通用 chatbot）。以下是你必须理解的上下文。

---

## 项目定位

ScholarAgent 帮助经济学研究者完成"审稿→修改→去AI味"的完整闭环。它的用户是有能力写论文但希望 Agent 辅助提升质量的学者。核心价值主张：Agent 不仅告诉你哪里有问题，还帮你改好，而且改完不像 AI 写的。

---

## 架构现状

项目包含两个完全独立的版本：

- **v1/**：Prompt-stacking 原型。通过大量 prompt 堆叠实现，token 消耗高，只能在强模型（Claude Sonnet/GPT-4 级）上工作。保留为参考基线，不再主动开发。298 tests 通过。
- **v2/**：HD-WM（假说驱动工作记忆）认知架构。可泛化、可在便宜模型上运行。主要开发方向。312 tests 通过。

两个版本完全独立（各自有 core/、llm/、config/、tests/），无交叉 import。

### V2 核心组件

- **harness.py**（3004 行）：Agent 的运行时环境，管理状态、工具调用、边界守卫、消息压缩。是下一步拆分的目标。
- **agent.py / loop.py**：认知循环实现（Observe → Think → Act → Update）
- **session_memory.py**：9 段结构化认知笔记，在 compaction 恢复时注入
- **paper_index.py**：论文结构预索引（纯正则，零 LLM 成本）
- **compaction.py**：Smart Compaction 分层恢复
- **finding_quality.py**：Finding 质量门控（规则检查）
- **paper_type_hints.py**：按论文类型注入不同认知策略
- **cognition_graph.py**：审稿认知图谱输出
- **gate_config.py**：Completion Gate 动态配置
- **deai_detector.py**（926 行）：去 AI 味检测器（50+ 模式，中英文）

---

## 六条不可违反的设计约束

**C1: Agent = Loop + Tools。** Agent 是"模型在循环中使用工具自主完成任务"。不是更好的 prompt template，不是 workflow engine。

**C2: LLM = 无状态 CPU。** LLM 每轮调用都是独立的。所有跨轮次信息必须由外部 state 维护并显式注入 context。永远不依赖 LLM "记住"上一轮的结论。

**C3: 控制流 > Prompt Engineering。** 提升 Agent 能力的正确方式是优化控制流（工具集、状态注入、压缩策略），不是优化 system prompt 的措辞。

**C4: 分层压缩（Token Pipeline）。** Context window 是有限认知带宽。Token Pipeline = Collect → Rank → Compress → Budget → Assemble。所有信息注入必须经过这个管道。

**C5: Constrain, don't control。** Harness 约束 Agent 的边界（不能编造引用、不能无限循环），但在边界内 Agent 完全自主。所有注入信息的措辞是"参考/建议"，不是"必须/要求"。

**C6: Keep it simple。** "一开始加太多复杂设计会严重拖慢迭代速度。"每个新增都问：这是最简单的能达到目标的方式吗？

---

## 当前开发阶段

UPGRADE_PLAN_FINAL（审稿认知增强）已完成 77%（10/13 项）。当前进入新阶段：**Edit Agent — 从"只会审"到"能审能改"的跃迁。**

### 下一步工作（按优先级）

详见 `docs/NEXT_STEPS.md`，概要如下：

1. **H-SPLIT**：harness.py 拆分为 5-7 个模块（工程前提）
2. **EDIT-1~5**：编辑 Agent 子流程（修改计划 → 验证 → 增量执行 → MCP 工具 → 迭代闭环）
3. **DEAI-1/2**：去 AI 味道自动化闭环
4. **E0/A0/R1**：遗留项穿插完成

### Edit Agent 的核心设计思想

- 编辑是审稿的自然延伸，不是独立 pipeline。同一个 Agent 先审后改，上下文连贯。
- 修改计划由 Agent（LLM）推理产出，不是程序化生成。
- 增量编辑（段落/句子级）优于全量替换。
- 代码执行通过 MCP 外部对接（Stata MCP），不自建沙箱。复杂代码需求超出范围。
- de-AI 是编辑的最后一道工序，不是独立后处理。

---

## EDIT-4 边界约束（重要）

- ScholarAgent 是学术写作辅助 Agent，**不是代码助手**
- 经济学代码多数是简单的（一条回归命令、一个描述统计表），通过 MCP 调用即可
- 复杂代码需求（自定义 estimator、simulation、大规模数据处理）不在服务范围
- 我们不维护代码执行沙箱，只维护"何时该调用"和"结果如何整合回论文"的逻辑
- 如果 MCP 不可用，优雅降级（标注"需手动验证"），不 crash

---

## 关键参考文档

- `docs/UPGRADE_PLAN_FINAL.md` — 审稿增强计划（77% 完成）
- `docs/NEXT_STEPS.md` — 后续规划终版（Edit Agent + de-AI）
- `docs/ARCHITECTURE_V2_BLUEPRINT.md` — V2 架构蓝图（含自检清单和正反对照）
- `docs/COGNITIVE_ANCHOR.md` — 第一性原理锚点（反模式清单 + 正确思维方式）
- `v2/core/CLAUDE.md` — V2 运行时约束规则
- `MIGRATION_NOTE.md` — V1/V2 目录迁移说明

---

## 工作方式约定

1. **修改前先理解**：修改任何核心文件前，先读 COGNITIVE_ANCHOR.md §3（反模式）和 §9（自检问题）。
2. **增量验证**：每个改动后跑 `cd v2 && python -m pytest tests/ -x -q`。
3. **偏离记录**：如果实现偏离了 NEXT_STEPS.md 的设计，用偏离记录模板记录原因。
4. **不标"完成"除非真完成**：验证标准没通过 = "已实现但待验证"，不是"完成"。
5. **问"人类专家会这样做吗？"**：每个设计决策都问。如果答案是"不会"，说明在做程序而非认知。

---

## 反模式提醒

你在做以下事情时，立即停下来：

- 优化 system prompt 措辞让 Agent "表现更好" → 你在做 Prompt Engineering，应该优化控制流
- 让 LLM 在 prompt 里"记住"上一轮结论 → 你在依赖 LLM 记忆，应该写入 state 再注入
- 设计"审稿完成后自动进入修改阶段"的流程 → 你在做 workflow engine，应该让 Agent 自主决定何时转换
- 把 LLM 能做的推理包装成 Tool → Theater Code，只有需要外部副作用的操作才该是 Tool
- 为"看起来有架构"增加模块 → 简单性检查没通过
```

---

## 使用说明

1. 新开会话时，将上面的 prompt 正文（```包围的部分）粘贴为第一条消息
2. 然后说明本次会话要做什么具体任务
3. 如果是继续上次未完成的工作，简要说明进度（例如"H-SPLIT 已完成 boundary_guard 提取，接下来做 message_compressor"）
