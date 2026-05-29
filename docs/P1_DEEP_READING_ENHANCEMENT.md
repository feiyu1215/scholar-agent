# P1 增强方案：深度阅读能力提升 — 设计文档

> **版本**: v1.0
> **范围**: F.3 改进路线图 Phase 1 — "When to Search" 机制 + Finding 去重增强
> **状态**: 待实施
> **前置阅读**: `DESIGN.md`（整体架构）、`docs/COGNITIVE_SPEC.md`（认知循环规范）
> **目标代码目录**: `scholar-agent-public/v2/core/`

### 与总路线图的关系

本文档是一个**插入式修复计划**，独立于总执行计划进行。背景如下：

- **总执行计划**：`v2/EXECUTION_PLAN.md`（相对项目根）— 定义了 "阶段1（F→B→A）→ 阶段2 → 阶段3" 的全局路线
- **触发来源**：在执行总计划阶段 1 的 **F.2（Recall 诊断）** 过程中，我们发现了两个具体问题——搜索不足和 finding 重复——需要紧急修复后才能继续 F.3 的整体性针对性修复
- **定位**：本文档属于 F.3（针对性修复）的一个前置子任务，但因为问题独立且明确，单独建档跟踪
- **完成后**：回归总计划的 F.3 → B.1/B.2 → A.1 → 验证 流程

本计划与 `ScholarAgent_V2_Development_Roadmap.md`（Phase 1-9 技术升级）是不同层面的事——后者是架构级升级规划，本计划是认知行为层面的微调。两者互不阻塞。

---

## 一、问题陈述

### 1.1 搜索不足问题

**现象**：在 5 篇论文的 eval 中，agent 对方法论细节的判断频繁依赖"大概记得"而非外部文献校准。具体表现为：

- 对具体数值（如效应量 0.067 SD 在某领域是否合理）做出判断但未搜索同类研究的典型范围
- 对估计方法的局限性做出断言但未查证该方法在近期文献中被指出的具体问题
- 对参数选择（bandwidth、cluster level）的合理性给出意见但未参考实施指南

**根因**：现有 `search_literature` 的 tool description 偏向"审稿 meta 任务"（novelty 验证、引用核查），缺少"方法论细节层面"的知识边界信号。模型不知道"我大概知道但说不清细节"这种状态就是该搜索的信号。

### 1.2 Finding 去重问题

**现象**：同一问题在不同 turn 被重复记录。eval 数据显示 45 条 raw findings → 34 条 after manual dedup（去重率仅 24%）。

**典型案例**（paper_001）：
- Finding #2（turn 3）："6.2-6.7% 与系数换算不透明"
- Finding #4（turn 10）："Table 2 的 coefficient 与百分比结论 0.067 之间缺少明确换算步骤"
- Finding #5（turn 12）：同一问题第三次出现，补充了表格证据

**根因**：现有 `check_finding_overlap()`（`findings.py:285-354`）使用 70% 关键词重叠度阈值，但：
1. 阈值过高——同一问题用不同措辞时重叠度远低于 70%
2. 只匹配英文 4+ 字母单词——忽略数字（"6.2%"、"0.067"、"Table 2"），而数字恰恰是判断同一 finding 的强信号
3. 状态变化时仍追加新记录——导致 needs_verification → verified 的状态升级产生两条记录
4. 纯词袋模型无语义理解——"percentage point effect 6.2-6.7" 和 "coefficient 0.062-0.067 conversion" 被视为不同内容

---

## 二、设计哲学

### 核心原则

**用 tool description 作为知识边界的具象化表达**，让模型的 tool-use 决策自动对齐到正确的知识边界上。

### 方案定位

方案 A（tool description 引导模型判断，如 Claude Code 所做）+ 论文 2506.00886 的关键 Insight（tool-use decision boundary 应与 knowledge boundary 对齐）的结合，辅以方案 B（identity 认知习惯微调）和轻量 harness nudge。

### 设计约束

| 约束 | 说明 |
|------|------|
| 不新增代码逻辑模块 | 无 routing node、无 decision classifier |
| 不改 agent loop 结构 | `core/loop.py` 零改动 |
| 不违反 C5 | Constrain, don't control — 所有引导都是"提示"而非"命令" |
| 认知层面增强 | 改 prompt text（identity + tool desc），不改 execution logic |
| 不新增 LLM 调用 | 去重逻辑不做额外 LLM 判断，纯规则匹配 |

### 与现有架构哲学的对齐

- **"认知身份 > 复杂编排"**：修改点全部作用于认知层（identity + tool description），不引入编排逻辑
- **Agent 自主性**：所有搜索触发信号都是 suggestion（"当你遇到 X 时"），模型可自主决定是否搜索
- **Append-only 状态模型**：去重增强将"追加新记录"改为"更新原记录"，但不改 state 的整体 append-only 模式

---

## 三、修改点总览

| # | 文件 | 行号范围 | 修改类型 | 复杂度 | 风险 |
|---|------|---------|---------|--------|------|
| 1 | `v2/core/identity.py` | 201-216 | 重写 tool description | 低（纯文本替换）| 低 |
| 2 | `v2/core/identity.py` | 70 行之后 | 认知习惯 7 微调 | 低（3句话插入）| 低 |
| 3 | `v2/core/tool_reflect.py` | 109 行之后 | 新增 nudge 条件 C | 中（新增代码逻辑）| 中 |
| 4 | `v2/core/tool_handlers/findings.py` | 285-354 | 重写去重函数 | 中（重写函数）| 中 |

**实施顺序**：1 → 2 → 3 → 4 → 测试

---

## 四、修改点 1：重写 `search_literature` Tool Description

### 位置

`v2/core/identity.py`，第 201-216 行，`SCHOLAR_TOOLS` 列表中 `search_literature` 的 `"description"` 字段。

### 当前版本

```python
"description": "你的 Google Scholar——搜索学术文献来验证论文的 claim。一个好的审稿人不会只凭论文自身的叙述来判断——他会去查。典型使用场景：(1) 论文声称 novelty 或 'first to'——搜索确认是否真的没有先例；(2) 方法论有疑问——搜索看这个方法在其他论文中是否有已知局限性；(3) 引用的关键文献——搜索确认年份/作者/结论是否被正确引用；(4) 核心 claim 的外部验证——搜索看其他研究是否支持或反驳这个结论。如果你审完一篇论文却从未搜索过文献，你可能遗漏了重要的外部证据。",
```

### 增强版本

```python
"description": "你的 Google Scholar——搜索学术文献来校准你的判断。核心原则：你的知识有边界。你大概知道很多方法和理论，但对具体的数值范围、最新进展、已知局限的细节，你的记忆可能过时或模糊。当你意识到自己'大概知道但说不清细节'时，这就是该搜索的时刻。\n\nWHEN TO USE（知识边界信号——当你遇到以下情况时，搜索而非猜测）：\n(1) 具体数值判断：论文报告了效应量、弹性系数、标准误、bandwidth 等数值，你需要判断其合理性——搜索同类研究的典型范围。你可能知道'DID 的效应量一般不大'，但你不确定这个领域 0.3 SD 算大还是小。\n(2) 方法论的已知局限：你遇到一个估计方法（如 synthetic control、bunching estimator、shift-share IV），你知道它的基本原理，但不确定它在最新文献中被指出了哪些具体问题——搜索其局限性和最佳实践。\n(3) 参数选择的合理性：论文选择了某个 bandwidth、cluster level、bootstrap iterations 数值，你不确定这是否符合最佳实践——搜索该方法的实施指南。\n(4) Novelty 验证：论文声称'first to'或'no prior work'——搜索确认是否真的没有先例。\n(5) 引用核查：关键引用的作者/年份/结论是否被正确引用——搜索确认。\n(6) 核心 claim 的外部验证：你对论文的结论有了判断，想看其他研究是否支持或反驳——搜索交叉验证。\n(7) 方法是否被 supersede：论文使用的方法可能已有更优替代——搜索确认该方法在当前文献中的地位。\n\nWHEN NOT TO USE（不需要搜索的情况）：\n- 你对一个纯逻辑问题有确定判断（如'这个证明第三步有跳跃'）——这不需要外部验证\n- 你在描述论文做了什么（理解层）——搜索是为了质疑和验证，不是为了理解\n- 你已经搜过同一个问题且结果清晰——不要重复搜索\n\n如果你审完一篇论文却从未搜索过文献，问自己：你对方法论细节的判断是基于确切知识，还是基于'我大概记得是这样'？后者就是该搜索的信号。",
```

### 设计逻辑

1. **开头明确核心原则**："你的知识有边界"——直接对应 2506.00886 的 insight
2. **场景 (1)-(3) 新增**：知识边界信号——模型"大概知道但不确定具体细节"的情况，这是现有版本最大的缺口
3. **场景 (4)-(6) 保留**：原有场景精练化
4. **场景 (7) 新增**：方法过时性判断
5. **WHEN NOT TO USE**：参考 Claude Code 的做法，明确不该搜的情况，避免过度搜索
6. **结尾反问句**：元认知 trigger——引导模型在完成审稿时自问知识边界问题

### Token 影响

从约 150 tokens 增加到约 350 tokens。增量约 200 tokens，在 system prompt 总量（约 5000+ tokens）中可接受。

### 验收标准

- 模型在遇到具体数值（效应量、bandwidth 等）时，搜索概率应显著提高
- 不应出现对纯逻辑问题的无效搜索（WHEN NOT TO USE 的控制效果）

---

## 五、修改点 2：Identity 认知习惯 7 微调

### 位置

`v2/core/identity.py`，第 70-71 行之间。

第 70 行是认知习惯 7 的总描述句："你不只是在审一篇论文——你是在将这篇论文放入更大的学术语境中评估。你有三种深度不同的文献使用方式，根据审稿情境自主选择："，第 71 行是空行，第 72 行是 `**三种深度（你自己判断何时用哪种）**：`。

### 当前结构

```
70│ 7. **文献使用心智模型（Literature as Cognitive Extension）**：你不只是在审一篇论文——...
71│
72│    **三种深度（你自己判断何时用哪种）**：
73│    - **验证性搜索**（轻量）...
74│    - **参考文献深读**（中等）...
75│    - **主动探索**（深入）...
```

### 插入内容

在第 71 行（空行）之后、第 72 行（"三种深度"）之前，插入以下段落（含尾部空行）：

```
   **搜索的元认知（Meta-cognition of Search）**：你有大量的方法论知识，但你的知识有两个盲区——(a) 具体数值：你可能知道"LATE 估计量通常比 ATE 大"，但不确定某个领域中 0.5 SD 的效应量是否异常；(b) 最新进展：你可能知道某个方法的经典版本，但不确定近 2-3 年是否有人提出了更优的替代或指出了新的局限。当你发现自己的判断依赖于这两类信息时，搜索是必需的——你的目标不是"搜索来学习"，而是"搜索来校准"。

```

### 设计逻辑

- 这段文字很短（3 句话，约 80 tokens），但做了一个关键的事：把"知识边界"的概念从 tool description 延伸到 identity 层的认知习惯中
- 模型在形成认知时就已有"我的知识有边界"的意识，在 tool description 中看到具体触发信号时两者自然呼应
- 与修改点 1 的关系：Identity 是**认知底色**（"你的知识有边界"），Tool Description 是**行动指令**（"你现在该搜"）

### 风险评估

极低。纯文本插入，不改变任何逻辑。最坏情况是模型忽略这段话（neutral impact）。

---

## 六、修改点 3：增强 Reflect Nudge 逻辑

### 位置

`v2/core/tool_reflect.py`，第 109 行之后（现有条件 A/B 的 `elif` 块结束后）。

### 当前逻辑（第 99-109 行）

```python
# Phase 39+41: 外部验证状态
search_count = len(search_log)
lines.append("")
lines.append("【外部验证】")
lines.append(f"  search_literature 已调用 {search_count} 次")
if search_count == 0 and len(s.findings) > 0:
    lines.append("  ⚠ 你有发现但尚未查过外部文献——你的判断完全基于论文自身的叙述。")
    lines.append("  一个好审稿人会用外部文献校准自己的判断——尤其是对方法论和核心 claim 的判断。")
elif search_count == 0 and len(s.sections_read) >= 4:
    lines.append(f"  ⚠ 你已读了 {len(s.sections_read)} 个 section 但尚未查过外部文献。")
    lines.append("  即使你还在形成判断，外部文献可以帮你更快定位论文的真正弱点。")
```

### 新增条件 C

在第 109 行之后（现有 `elif` 块结束后），新增：

```python
    elif search_count > 0 and len(s.findings) >= 2:
        # Phase P1: 方法论判断的外部校准检查
        methodology_keywords = {
            "bandwidth", "cluster", "bootstrap", "robustness", "identification",
            "instrument", "validity", "assumption", "estimat", "specif",
            "heterogeneity", "sensitivity", "placebo", "falsif",
            "synthetic", "bunching", "shift-share", "discontinuity",
        }
        search_queries_text = " ".join(
            entry.get("query", "") for entry in search_log
        ).lower()

        uncalibrated_method_findings = []
        for f in s.findings:
            if f.get("priority") != "high":
                continue
            finding_lower = f["finding"].lower()
            is_methodology = any(kw in finding_lower for kw in methodology_keywords)
            if not is_methodology:
                continue
            # 检查搜索历史中是否有相关查询
            finding_terms = set(re.findall(r'[a-zA-Z]{5,}', finding_lower))
            query_terms = set(re.findall(r'[a-zA-Z]{5,}', search_queries_text))
            overlap = len(finding_terms & query_terms)
            if overlap < 2:  # 搜索历史中几乎没有覆盖这个 finding 的查询
                uncalibrated_method_findings.append(f["finding"][:60])

        if uncalibrated_method_findings:
            lines.append(f"  💡 你搜索了文献，但有 {len(uncalibrated_method_findings)} 条高优方法论判断"
                        f"似乎没有对应的外部校准：")
            for desc in uncalibrated_method_findings[:2]:
                lines.append(f"    • {desc}")
            lines.append("  你对这些判断的信心是基于确切知识还是'大概记得'？")
```

### 需要新增的 import

当前 `tool_reflect.py` 的 imports（第 12-17 行）**不包含 `import re`**。需要在第 13 行（空行）和第 14 行（`from typing import Any`）之间插入一行：

```python
from __future__ import annotations

import re
from typing import Any

from core.state import WorkspaceState
```

即在 `from __future__` 和 `from typing` 之间插入 `import re`（符合 Python stdlib-before-third-party 的 import 顺序）。

### 设计逻辑

| 决策 | 原因 |
|------|------|
| 只在 `search_count > 0` 时触发 | 如果连一次都没搜过，已有条件 A/B 会 nudge |
| 只检测 `priority == "high"` | 避免对 minor issues 过度 nudge |
| 匹配用 5+ 字符英文词 | 比 4 字符更精准，减少 "with"/"from" 等噪音 |
| 阈值 `overlap < 2` | 搜索查询中至少有 2 个词与 finding 相关才算"已覆盖" |
| 措辞是反问 | "你的信心是基于确切知识还是'大概记得'？"——元认知触发，不是命令 |
| 最多展示 2 条 | 避免信息过载 |

### 与现有 nudge 的兼容性

- 条件 A/B：`search_count == 0` 时触发
- 条件 C（新增）：`search_count > 0` 时触发
- 三者互斥，不会重复 nudge。✅

### 潜在风险

- 关键词匹配可能有 false positive：非方法论 finding 碰巧包含 methodology_keywords → 用 `priority == "high"` 限制 + 只匹配 5+ 字符词，误报率较低
- 如果 agent 搜索时用了与 finding 完全不同的术语（如搜了 "DID assumptions" 但 finding 写的是 "parallel trends violation"），可能漏检 → 可接受的 false negative，不影响安全性

---

## 七、修改点 4：增强 Finding 去重机制

### 位置

`v2/core/tool_handlers/findings.py`，第 285-354 行，整体重写 `check_finding_overlap()` 函数。

### 设计策略：多信号融合 + 原地更新

**核心变化**：

| 维度 | 旧版 | 新版 |
|------|------|------|
| 阈值 | 固定 70% | 多信号融合：70% 纯术语 / 60%+数字匹配 / 50%+同section+数字 |
| 数字匹配 | 无 | 提取 Table/Figure 引用 + 小数/百分比数值 |
| 状态升级行为 | 追加新记录 | **更新原记录**（原地修改 status + 追加 evidence）|
| 证据补充 | 不处理 | 新证据追加到原记录的 evidence 字段 |
| 返回消息 | "建议合并" | "已更新/已补充"（让 agent 知道操作已完成）|

### 新版完整代码

```python
def check_finding_overlap(new_finding: dict, state: Any, enable_hdwm: bool, hypothesis_module: Any) -> str | None:
    """
    Phase P1: 检查新 finding 是否与已有 findings 高度重叠。

    去重策略（多信号融合）：
    1. 英文术语重叠 >= 70%（纯术语匹配，与旧版一致）
    2. 英文术语重叠 >= 60% + 数字/表格引用重叠 >= 1
    3. 英文术语重叠 >= 50% + 同 section + 数字/表格引用重叠 >= 1

    行为变化：
    - 状态升级（needs_verification → verified）时：更新原记录，不追加新记录
    - 同一问题补充证据时：追加证据到原记录，不创建新记录
    """

    def _extract_terms(text: str) -> set[str]:
        """提取有意义的英文术语（去停用词）。"""
        en_words = set(re.findall(r'[a-zA-Z]{4,}', text.lower()))
        stopwords = {'this', 'that', 'with', 'from', 'have', 'been', 'which', 'their',
                     'more', 'than', 'also', 'some', 'other', 'about', 'would', 'could',
                     'should', 'these', 'those', 'into', 'only', 'very', 'such', 'each',
                     'finding', 'section', 'paper', 'author', 'however', 'therefore',
                     'does', 'will', 'what', 'when', 'where', 'there', 'over', 'under',
                     'between', 'through', 'during', 'before', 'after', 'above', 'below'}
        return {w for w in en_words if w not in stopwords}

    def _extract_numeric_refs(text: str) -> set[str]:
        """提取数字引用信号：表格编号、具体数值、方程编号等。"""
        refs = set()
        # 表格/图引用：Table 1, Figure 2, Eq. 3
        for m in re.finditer(r'(?:table|figure|fig|eq|equation)\s*\.?\s*(\d+)', text.lower()):
            refs.add(f"ref_{m.group(0).strip()}")
        # 显著数值（小数点数字，如 0.067, 6.2%, 3.2pp）
        for m in re.finditer(r'(\d+\.?\d*)\s*(?:%|pp|percentage)', text.lower()):
            refs.add(f"num_{m.group(1)}")
        for m in re.finditer(r'(?<!\d)0\.\d{2,}', text):
            refs.add(f"num_{m.group(0)}")
        return refs

    new_terms = _extract_terms(new_finding["finding"])
    new_nums = _extract_numeric_refs(new_finding["finding"])
    new_section = new_finding.get("section", "").lower().strip()

    if len(new_terms) < 3:
        return None

    for i, existing in enumerate(state.findings):
        existing_terms = _extract_terms(existing.get("finding", ""))
        if len(existing_terms) < 3:
            continue

        # --- 多信号计算 ---
        intersection = new_terms & existing_terms
        term_overlap = len(intersection) / min(len(new_terms), len(existing_terms))

        existing_nums = _extract_numeric_refs(existing.get("finding", ""))
        num_overlap = len(new_nums & existing_nums) if (new_nums and existing_nums) else 0

        same_section = (new_section and new_section == existing.get("section", "").lower().strip())

        # --- 判定是否为同一问题 ---
        is_duplicate = False
        if term_overlap >= 0.70:
            is_duplicate = True
        elif term_overlap >= 0.60 and num_overlap >= 1:
            # 术语中等重叠 + 引用了相同数字/表格 → 高度可能是同一问题
            is_duplicate = True
        elif term_overlap >= 0.50 and same_section and num_overlap >= 1:
            # 同一 section + 一定术语重叠 + 相同数字 → 几乎确定是同一问题
            is_duplicate = True

        if not is_duplicate:
            continue

        # --- 处理重复 ---
        new_status = new_finding.get("status", "suggestion")
        old_status = existing.get("status", "suggestion")
        new_evidence = new_finding.get("evidence", "")
        old_evidence = existing.get("evidence", "")

        # 状态优先级
        status_priority = {"needs_verification": 0, "suggestion": 1, "verified": 2}
        new_prio = status_priority.get(new_status, 1)
        old_prio = status_priority.get(old_status, 1)

        if new_prio > old_prio:
            # --- 状态升级：原地更新 ---
            # HD-WM 联动（先检查完整性，再决定是否更新状态）
            hdwm_note = ""
            hyp_id = existing.get("_hdwm_hyp_id") or new_finding.get("_hdwm_hyp_id")
            if hyp_id and new_status == "verified":
                new_finding["_hdwm_hyp_id"] = hyp_id
                existing["_hdwm_hyp_id"] = hyp_id
                hdwm_note = hdwm_auto_enhance(new_finding, state, enable_hdwm, hypothesis_module)
                # 如果完整性检查失败（返回了完整性提示），不更新 status
                if hdwm_note and "完整性提示" in hdwm_note:
                    msg = (
                        f"✓ 检测到与已有发现 #{i+1} 的重叠度 {term_overlap:.0%}。"
                        f"\n{hdwm_note}"
                        f" (当前仍为 {len(state.findings)} 条)"
                    )
                    return msg

            # 完整性通过或无 HD-WM：正式更新状态
            existing["status"] = new_status
            if new_evidence and new_evidence != old_evidence:
                if old_evidence:
                    existing["evidence"] = old_evidence + " | " + new_evidence
                else:
                    existing["evidence"] = new_evidence

            msg = (
                f"✓ 已更新发现 #{i+1} 的状态: {old_status} → {new_status}"
                f" (检测到与已有发现的重叠度 {term_overlap:.0%}"
                f"{f', 共同引用 {num_overlap} 个数值/表格' if num_overlap else ''})"
                f" (当前仍为 {len(state.findings)} 条)"
            )
            if new_evidence and new_evidence != old_evidence:
                msg += "\n  新证据已追加到原记录。"
            if hdwm_note:
                msg += f"\n{hdwm_note}"
            return msg

        elif new_prio == old_prio:
            # --- 同状态重复 ---
            if new_evidence and new_evidence != old_evidence and not old_evidence:
                # 新的有证据，旧的没有：补充证据到原记录
                existing["evidence"] = new_evidence
                return (
                    f"✓ 已为发现 #{i+1} 补充证据（检测到重复，未创建新记录）。"
                    f" (当前仍为 {len(state.findings)} 条)"
                )
            elif new_evidence and old_evidence and new_evidence != old_evidence:
                # 都有证据但不同：追加
                existing["evidence"] = old_evidence + " | " + new_evidence
                return (
                    f"✓ 已为发现 #{i+1} 追加额外证据（检测到重复，未创建新记录）。"
                    f" (当前仍为 {len(state.findings)} 条)"
                )
            else:
                # 纯重复，不追加
                return (
                    f"⚠️ 未记录：这条发现与已有发现 #{i+1} 高度重叠 "
                    f"(术语重合 {term_overlap:.0%}"
                    f"{f', 共同引用 {num_overlap} 个数值/表格' if num_overlap else ''})。"
                    f"重复的发现不增加审稿价值。"
                    f"如果你想补充新维度的判断，请确保措辞体现与已有发现的区别。"
                    f" (当前仍为 {len(state.findings)} 条)"
                )

        else:
            # --- 状态降级（罕见） ---
            return (
                f"⚠️ 未记录：已有发现 #{i+1} 状态更高 ({old_status})，"
                f"当前提交为 {new_status}。如果需要撤销之前的验证结论，请显式说明。"
                f" (当前仍为 {len(state.findings)} 条)"
            )

    return None
```

### 对 paper_001 案例的预期行为

以 "6.2-6.7% 与表格系数换算" 为例：

| Turn | 操作 | 新版行为 |
|------|------|---------|
| 3 | 首次记录 | 正常通过，`_extract_numeric_refs` 提取 `{num_6.2, num_0.067}` |
| 10 | 同一问题换措辞 | 数字引用重叠 (`num_0.067` ∈ both) + 术语重叠约 55% → 触发去重 → "⚠️ 未记录" 或 "✓ 已更新状态" |
| 12 | 补充表格证据 | `ref_table 2` 匹配 + 部分术语重叠 → 触发去重 → "✓ 已追加证据到原记录" |

**结果**：3 条变为 1 条（原记录被就地丰富），总 findings 数保持不变。

### HD-WM 联动保证

- 当状态升级触发 `hdwm_auto_enhance` 时，新版代码与旧版行为一致——将 `_hdwm_hyp_id` 从原记录传递给新 finding，再触发 HD-WM 增强
- 关键区别：旧版在状态升级时 `state.findings.append(new_finding)` 导致双记录；新版只更新原记录、不 append
- HD-WM 的 `hyp.is_resolved` 逻辑保持不变

### 与 `_detect_finding_overlaps`（tool_reflect.py）的关系

`tool_reflect.py` 第 122-130 行有一个 `_detect_finding_overlaps` 用于 reflect 时提醒重叠。这个函数不需要修改——它的功能是"事后提醒已有重叠"，而修改点 4 的功能是"事前阻止重叠产生"。两者互补，不冲突。

---

## 八、三个修改点的协同关系

```
┌─────────────────────────────────────────────────────────────┐
│                    Agent 认知循环                              │
│                                                             │
│  ┌──────────┐     ┌──────────────┐     ┌───────────────┐   │
│  │ Identity │     │Tool Descript.│     │   Reflect     │   │
│  │  (背景)   │     │  (决策时刻)   │     │  (事后校准)   │   │
│  └────┬─────┘     └──────┬───────┘     └───────┬───────┘   │
│       │                   │                     │           │
│  "你的知识        "当你遇到具体         "你有方法论判断    │
│   有边界"         数值/不熟悉方法        但没有外部校准"    │
│   (元认知         /参数选择合理性         (事后反问)       │
│    种子)          → 搜索"                                  │
│  [修改点2]       (行动指令)             [修改点3]          │
│       │          [修改点1]                   │              │
│       ▼                   │                  ▼              │
│  ╔═══════════════════════════════════════════════╗          │
│  ║  模型自然形成"不确定时搜索"的行为模式          ║          │
│  ╚═══════════════════════════════════════════════╝          │
│                                                             │
│  ┌──────────────────────────────────────────────────┐      │
│  │  Finding 去重 [修改点4]                            │      │
│  │  确保行为改善后增加的搜索+发现不产生冗余记录        │      │
│  └──────────────────────────────────────────────────┘      │
└─────────────────────────────────────────────────────────────┘
```

**覆盖 agent loop 的不同阶段**：
- **Identity**（修改点 2）→ 认知形成阶段：植入"知识有边界"的元认知种子
- **Tool Description**（修改点 1）→ 决策时刻：给出具体的知识边界信号列表
- **Reflect Nudge**（修改点 3）→ 事后反思：用反问方式提醒未校准的判断
- **Finding Dedup**（修改点 4）→ 输出质量：防止搜索增加后的重复记录

---

## 九、不做的事（设计边界）

| 被考虑但排除的方案 | 排除原因 |
|-------------------|---------|
| ReviewPlanningSkill 标注 "verification_required" | 违反 C5：过于 prescriptive，不信任强模型的自主判断 |
| Routing node / decision classifier | 违反"认知身份 > 复杂编排"哲学 |
| 嵌入静态方法论知识库 | 不可维护；agent 应搜索而非记忆 |
| 强制搜索（检测到方法名就自动触发） | 违反 agent 自主性，可能导致过度搜索 |
| LLM-based 语义去重 | 增加 API 调用成本和延迟，与"不新增 LLM 调用"约束冲突 |
| UALA / SeaKR / Self-RAG | 需要 logprobs/hidden states/fine-tuning，与 API-based agent 不兼容 |

---

## 十、KV-Cache 与 Token 预算影响分析

| 修改点 | 类型 | Token 增量 | 对 cache 的影响 |
|--------|------|-----------|----------------|
| 1 | system prompt 固定文本 | +200 tokens | 仅改一次，不影响运行时 cache prefix 稳定性 |
| 2 | system prompt 固定文本 | +80 tokens | 同上 |
| 3 | 条件性 reflect 输出 | 0（基线不增加） | 只在条件满足时出现，不影响 cache hit rate |
| 4 | 代码逻辑 | N/A（不进入 prompt） | 零影响 |

**总 system prompt 增量**：约 280 tokens，从约 5000 增至约 5280，增幅约 5.6%。可接受。

---

## 十一、测试策略

### 11.1 单元测试

**修改点 4（去重函数）**：

```python
# tests/test_finding_dedup.py

def test_same_finding_different_wording():
    """同一问题不同措辞应被去重。"""
    state = MockState(findings=[
        {"finding": "Table 2 coefficient 0.067 换算为百分比 6.7% 不透明",
         "section": "results", "status": "needs_verification", "priority": "high"}
    ])
    new = {"finding": "coefficient 与 percentage point 6.2-6.7% 之间缺少明确换算步骤",
           "section": "results", "status": "needs_verification", "priority": "high"}
    result = check_finding_overlap(new, state, False, None)
    assert result is not None  # 应被拦截
    assert "未记录" in result or "已更新" in result

def test_status_upgrade_updates_in_place():
    """状态升级应更新原记录而非追加。"""
    state = MockState(findings=[
        {"finding": "identification assumption quasi-random assignment 可能被违反",
         "section": "methodology", "status": "needs_verification", "priority": "high"}
    ])
    new = {"finding": "quasi-random assignment 已验证：identification assumption 确实被违反",
           "section": "methodology", "status": "verified", "priority": "high",
           "evidence": "搜索结果表明..."}
    result = check_finding_overlap(new, state, False, None)
    assert "已更新" in result
    assert state.findings[0]["status"] == "verified"
    assert len(state.findings) == 1  # 不应追加

def test_different_findings_not_blocked():
    """不同问题不应被误判为重复。"""
    state = MockState(findings=[
        {"finding": "identification via quasi-random assignment",
         "section": "methodology", "status": "verified", "priority": "high"}
    ])
    new = {"finding": "external validity concerns: single hospital setting",
           "section": "discussion", "status": "suggestion", "priority": "medium"}
    result = check_finding_overlap(new, state, False, None)
    assert result is None  # 应允许通过

def test_numeric_refs_boost_detection():
    """共同引用相同数字/表格应降低术语匹配阈值。"""
    state = MockState(findings=[
        {"finding": "Table 3 reports effect size 0.045 but text says 4.5%",
         "section": "results", "status": "needs_verification", "priority": "high"}
    ])
    new = {"finding": "The coefficient in Table 3 is 0.045 which contradicts the percentage claim",
           "section": "results", "status": "needs_verification", "priority": "high"}
    result = check_finding_overlap(new, state, False, None)
    assert result is not None  # 数字重叠应触发去重
```

### 11.2 集成测试

**修改点 3（reflect nudge）**：

```python
# tests/test_reflect_nudge.py

def test_uncalibrated_methodology_nudge():
    """搜索过但有未校准的高优方法论 finding 时应触发 nudge。"""
    state = MockState(
        findings=[
            {"finding": "bandwidth selection 200km seems arbitrary for identification",
             "priority": "high", "section": "methodology"},
            {"finding": "minor typo in abstract", "priority": "low", "section": "abstract"},
        ],
        sections_read=["abstract", "methodology", "results"],
    )
    search_log = [{"query": "novelty verification first to study X"}]
    
    output, _ = reflect_and_plan(state, cognitive_state, [], "", search_log, gate_config, {"trigger": "test"})
    assert "方法论判断" in output
    assert "大概记得" in output
```

### 11.3 回归测试

运行现有 `pytest` 确保修改不破坏：

```bash
cd v2 && python -m pytest --tb=short -q
```

关键关注：
- `test_tool_handlers/test_findings.py` 中涉及 `check_finding_overlap` 的用例
- `test_tool_reflect.py` 中涉及 reflect 输出格式的用例
- `evaluation/run_eval.py` 不应有回归（如果有，说明行为变化超出预期）

---

## 十二、预期效果与验证方向

> **注意**：以下为方向性期望而非硬性 KPI。agent 行为受模型、论文类型、prompt 长度等多因素影响，增强后实际效果需通过 eval 观察，不预设数字承诺。

### 搜索行为（修改点 1+2+3）

**期望方向**：
- 搜索频次提升：agent 在遇到具体数值判断、不熟悉方法细节时，应比当前更倾向于搜索而非凭记忆判断
- 搜索类型多样化：从当前以 novelty 验证和引用核查为主，扩展到方法论细节校准、参数合理性验证等场景
- 减少"零搜索审稿"：agent 审完一篇论文却从未搜索的情况应明显减少

**观察指标**（用于 eval 时定性判断，非 pass/fail 门槛）：
- 单篇平均 search 次数变化趋势
- 方法论细节类 search 是否出现（当前几乎没有）
- 搜索的触发时机是否合理（不应出现对纯逻辑问题的无效搜索）

### 去重效果（修改点 4）

**期望方向**：
- 同一问题跨 turn 重复记录的情况应大幅减少
- 状态升级（needs_verification → verified）应就地更新原记录，而非产生双记录
- 不应出现不同问题被误判为重复而丢失的情况

**观察指标**：
- 重新跑 eval 后，人工检查 findings 列表中是否仍有明显重复
- 状态升级场景下 findings 总数是否保持不变（而非递增）
- 抽检 2-3 篇论文确认无误拦截

### 硬性约束（必须满足）

- 无测试回归（现有 pytest 全部通过）
- System prompt token 增量 ≤ 300
- 不引入新的代码依赖或模块

---

## 十三、实施 Checklist

```
[x] 1. identity.py:201-216 — 替换 search_literature 的 description 字段内容
[x] 2. identity.py:71-72 之间 — 插入 3 句话元认知段落
[x] 3. tool_reflect.py:13-14 之间 — 新增 import re（插入在空行后、from typing 前）
[x] 4. tool_reflect.py:109 之后 — 新增条件 C 代码块（方法论校准 nudge）
[x] 5. findings.py:285-354 — 整体替换 check_finding_overlap 函数
[x] 6. 新增 tests/test_finding_dedup.py（8 个用例，含 2 个边界测试）
[x] 7. 新增 tests/test_reflect_nudge.py（5 个用例，含 2 个边界测试）
[x] 8. 运行 pytest 全量测试，确认零回归（2790 passed, 0 failed）
[ ] 9. 运行 eval 对比基线（可选，如 eval 成本可接受）
```

---

## 附录 A：参考资料

- **论文 2506.00886**："tool-use decision boundary should align with knowledge boundary"
- **Claude Code 架构**：tool description 中 WHEN TO USE / WHEN NOT TO USE 的双向引导模式
- **Manus 架构**：judgment 委托给 LLM（context engineering），不做 logit masking
- **ScholarAgent 设计哲学**：`docs/COGNITIVE_SPEC.md` — "认知身份 > 复杂编排"

## 附录 B：与 F.3 路线图其他 Phase 的关系

本文档覆盖 F.3 路线图的 **P1（深度阅读能力提升）**。后续 Phase 的依赖关系：

- **P2（跨论文关联能力）**：依赖 P1 的搜索增强——更多的外部文献搜索为跨论文关联提供素材
- **P3（自适应审稿策略）**：P1 的去重增强减少噪声，使策略调整的信号更清晰
- **P4（领域知识积累）**：P1 中搜索到的外部文献可作为领域知识积累的输入源
