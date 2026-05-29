# 交接 Prompt — P1 深度阅读能力提升：代码实施阶段

> **生成时间**：2025-07（对话上下文过长，交接给新会话继续执行）
> **交接状态**：设计文档已完成 ✅ → 代码实施已完成 ✅ （2790 passed, 0 failed）
> **目标目录**：`scholar-agent-public/v2/core/`（**不是** `scholar-agent-github/`）

---

## 一、项目是什么

ScholarAgent V2 是一个**认知身份驱动的学术论文审稿 Agent**。核心设计哲学是"认知身份 > 复杂编排"——通过精心设计的 system prompt（identity + tool descriptions）引导强模型的自主行为，而非靠 routing/classifier/多 Agent 编排来控制。

单 Agent + 状态机 + 黑板架构。一个 Agent loop，一个 WorkspaceState 黑板，工具调用全部走 tool_handlers/。

---

## 二、你现在在做什么（上下文）

我们在执行一个**四阶段全局路线图**（见 `v2/EXECUTION_PLAN.md`）：

```
阶段 1：深度使用 + Recall 提升
  └─ F.1（Gold Standard）✅ → F.2（Recall 诊断）✅ → F.3（针对性修复）← 我们在这里
```

在 F.2 诊断中发现了两个紧急问题，于是**从 F.3 中拆出了一个独立的子任务**：

- **搜索不足**：agent 对方法论细节（数值合理性、参数选择）凭"大概记得"判断，不搜索外部文献
- **Finding 去重失效**：同一问题换个措辞就再记一遍（45 条 raw → 34 条 after manual dedup）

这两个问题已有**完整设计文档**：`docs/P1_DEEP_READING_ENHANCEMENT.md`（691 行）。

---

## 三、设计文档核心内容速览

**四个修改点**，全部作用于 `scholar-agent-public/v2/core/`：

| # | 文件 | 做什么 | 复杂度 |
|---|------|--------|--------|
| 1 | `core/identity.py` | 重写 `search_literature` 的 tool description（加入 WHEN TO USE / WHEN NOT TO USE） | 低 |
| 2 | `core/identity.py` | 在认知习惯 7（文献使用心智模型）中插入 3 句话"搜索的元认知"段落 | 低 |
| 3 | `core/tool_reflect.py` | 新增 reflect nudge 条件 C（方法论判断外部校准检查）+ 新增 `import re` | 中 |
| 4 | `core/tool_handlers/findings.py` | 重写 `check_finding_overlap()` 函数——多信号融合去重 + 原地更新 | 中 |

**实施顺序**：1 → 2 → 3 → 4 → 测试

**核心设计原则**：
- 用 tool description 作为知识边界的具象化表达（论文 2506.00886）
- Claude Code 风格的 WHEN TO USE / WHEN NOT TO USE 双向引导
- C5 约束：Constrain, don't control — 所有引导都是 suggestion，模型可自主决定
- 不新增模块、不改 loop、不新增 LLM 调用、不新增依赖

---

## 四、代码定位指南（以内容锚点为准，不硬信行号）

行号可能因历史变更漂移，**永远以 grep 内容定位为准**：

### 修改点 1：search_literature description

```bash
grep -n '"name": "search_literature"' v2/core/identity.py
# 找到第一处（SCHOLAR_TOOLS 中的），下一行就是 "description": "..."
# 只改第一处（scholar 用的），不改第二处（技术审稿用的 ~行 1174）
```

**替换 description 字段整行**为设计文档第 106 行的增强版本（约 350 tokens 的长文本）。

### 修改点 2：Identity 习惯 7 微调

```bash
grep -n '三种深度（你自己判断何时用哪种）' v2/core/identity.py
# 在该行之前（空行处）插入一段 "搜索的元认知" 文字
```

插入内容见设计文档第 153 行。注意：是在 "7. 文献使用心智模型..." 段落标题和 "三种深度..." 之间的空行处插入。

### 修改点 3：Reflect Nudge 条件 C

```bash
grep -n '即使你还在形成判断，外部文献可以帮你更快定位' v2/core/tool_reflect.py
# 这是现有 elif 块的最后一行，新条件 C 插在其后
```

注意：
- `tool_reflect.py` 当前 **没有 `import re`**，需要在 `from __future__ import annotations` 和 `from typing import Any` 之间新增
- 新 elif 块（~30 行代码）见设计文档第 196-229 行
- 条件 C 与现有条件 A/B **互斥**（A/B 在 search_count==0 时触发，C 在 search_count>0 时触发）

### 修改点 4：重写 check_finding_overlap

```bash
grep -n 'def check_finding_overlap' v2/core/tool_handlers/findings.py
# 从该行到下一个空行+注释分隔的 return None 为止，整体替换
```

替换代码见设计文档第 287-433 行。关键变化：
- 新增 `_extract_numeric_refs()` 辅助函数
- 多信号融合阈值：70% 纯术语 / 60%+数字 / 50%+同section+数字
- 状态升级时**更新原记录**（不 append），证据补充时追加到原记录的 evidence 字段
- `findings.py` 已有 `import re`，不需要额外添加

---

## 五、测试计划

设计文档中规定了 3 类测试：

1. **新增单元测试**：`tests/test_finding_dedup.py`（4 个用例，代码在设计文档第 530-579 行）
2. **新增集成测试**：`tests/test_reflect_nudge.py`（1 个用例，代码在设计文档第 586-603 行）
3. **回归测试**：`cd v2 && python -m pytest --tb=short -q` 全量通过

---

## 六、验证标准

**不设硬性 KPI**（用户明确要求"只是努力，成与不成说不定"）。

方向性期望：
- 搜索频次提升、搜索类型多样化、减少零搜索审稿
- 同一问题跨 turn 重复记录大幅减少
- 状态升级就地更新、不产生双记录

硬性约束（必须满足）：
- 无测试回归
- System prompt token 增量 ≤ 300
- 不引入新的代码依赖或模块

---

## 七、关键设计决策（本轮对话产生的洞察）

1. **Tool description 是最优杠杆点**：论文 2506.00886 的核心发现是"tool-use decision boundary should align with knowledge boundary"。与其让 agent "更聪明地判断何时搜索"，不如让 tool description 本身就描述清楚知识边界信号

2. **三层修改形成认知闭环**：Identity（元认知种子）→ Tool Description（行动指令）→ Reflect Nudge（事后校准）。三者覆盖 agent loop 的不同阶段，互不重叠

3. **去重不用 LLM 做语义判断**：原因是成本和延迟。改用多信号融合（术语+数字+section），效果够用

4. **原地更新替代追加**：这是去重的核心行为变化——状态升级时修改原记录而非新建记录，从根源上杜绝重复

5. **文档放在 `scholar-agent-public/docs/`，不是 github**：`scholar-agent-public/` 是目标工作目录，`scholar-agent-github/` 只是一个副本。所有操作都在 public 上执行

---

## 八、DO / DON'T 速查

### DO

- ✅ 用 `grep` 定位代码位置（内容锚点），不硬信行号
- ✅ 只改 `scholar-agent-public/v2/core/` 下的文件
- ✅ 按顺序 1→2→3→4 实施（后面依赖前面）
- ✅ 每改一个点就跑 `pytest` 确认不回归
- ✅ 只改第一处 `search_literature`（SCHOLAR_TOOLS 中的，约行 201），不改第二处（identity_static 或其他审稿身份中的，约行 1174）
- ✅ 阅读设计文档中的完整代码后再动手

### DON'T

- ❌ 不要碰 `scholar-agent-github/` 目录
- ❌ 不要改 `core/loop.py`、`core/agent.py`、`core/harness.py`
- ❌ 不要新增 Python 文件（除了 test 文件）
- ❌ 不要引入新的第三方依赖
- ❌ 不要添加额外的 LLM 调用
- ❌ 不要修改其他 tool 的 description
- ❌ 不要设定量化 KPI 或 pass/fail 门槛

---

## 九、文档索引

| 文档 | 路径（相对 `scholar-agent-public/`） | 什么时候读 |
|------|------|------|
| **P1 设计文档**（本次任务的完整 spec） | `docs/P1_DEEP_READING_ENHANCEMENT.md` | **第一时间读**，691 行，包含所有修改的完整代码 |
| 总执行计划（全局路线图） | `v2/EXECUTION_PLAN.md` | 了解当前进度在全局中的位置 |
| 认知规范 | `docs/COGNITIVE_SPEC.md` | 理解 identity 设计的哲学背景 |
| 整体架构 | `DESIGN.md` | 理解 V2 的组件关系 |
| Development Roadmap | `/Users/yanfeiyu03/Downloads/ScholarAgent_V2_Development_Roadmap.md` | 了解 Phase 1-9 技术升级（与本任务互不阻塞） |

---

## 十、起手步骤（直接可执行）

```bash
# 0. 进入目标目录
cd /Users/yanfeiyu03/Downloads/scholar-agent-public

# 1. 读设计文档
cat docs/P1_DEEP_READING_ENHANCEMENT.md

# 2. 定位四个修改点的精确位置
grep -n '"name": "search_literature"' v2/core/identity.py
grep -n '三种深度（你自己判断何时用哪种）' v2/core/identity.py
grep -n '即使你还在形成判断，外部文献可以帮你更快定位' v2/core/tool_reflect.py
grep -n 'def check_finding_overlap' v2/core/tool_handlers/findings.py

# 3. 确认当前测试基线
cd v2 && python -m pytest --tb=short -q 2>&1 | tail -5

# 4. 按顺序实施修改点 1 → 2 → 3 → 4

# 5. 每步后回归测试
python -m pytest --tb=short -q

# 6. 全部完成后更新设计文档 Checklist
```

---

## 十一、常见问题

**Q: 为什么不直接强制 agent 搜索？**
A: 违反 C5（Constrain, don't control）。我们的 agent 是认知身份驱动的，所有引导都是 suggestion。强制搜索会破坏 agent 的自主性，且可能导致过度搜索。

**Q: 两个目录（public / github）是什么关系？**
A: `scholar-agent-public/` 是完整的开发目录，包含 `.workspace/`、eval 数据等。`scholar-agent-github/` 是准备推 GitHub 的精简副本。代码相同但 public 有更多运行时数据。所有开发操作在 public 上做。

**Q: 设计文档中的行号还能信吗？**
A: 截至本交接时刻验证过一次，全部正确。但这些文件经过多次变更，行号可能漂移。**永远用 grep 内容锚点定位**，行号只做参考。

**Q: 修改点 3 的 `import re` 为什么要加？**
A: 新增的条件 C 用了 `re.findall()` 做术语提取。当前 `tool_reflect.py` 没有 `import re`（而 `findings.py` 有）。加在 `from __future__ import annotations` 和 `from typing import Any` 之间。

**Q: 完成后下一步是什么？**
A: 回归 EXECUTION_PLAN 的 F.3（可能还有其他遗漏原因需要修复）→ B.1/B.2（激活高级 Phase）→ A.1（形成使用循环）。

**Q: eval 怎么跑？**
A: `cd v2 && python evaluation/run_eval.py`。需要 API key（`.env` 中的 `ANTHROPIC_API_KEY`）。eval 有成本，非必要不跑——先确保 pytest 通过，eval 作为可选验证。

---

## 十二、本轮会话做了什么（完成清单）

- [x] 基于前两轮设计讨论，撰写完整设计文档（691 行，13 节 + 2 附录）
- [x] 将设计文档从错误位置（github/docs/）移动到正确位置（public/docs/）
- [x] 修正文档中的路径引用（移除 scholar-agent-github 前缀，统一为 v2/core/ 相对路径）
- [x] 精确化 import re 的插入位置描述
- [x] 在 EXECUTION_PLAN.md 中添加 P1 引用
- [x] 在 Development Roadmap 中添加"进行中的插入式修复"章节
- [x] 量化 KPI 全部改为方向性期望（用户明确要求）
- [x] 验证所有代码引用与 public 中的实际文件一致
- [ ] **代码实施**（四个修改点）← 交给你
- [ ] **测试**（新增 + 回归）← 交给你
