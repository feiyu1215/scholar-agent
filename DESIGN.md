# ScholarAgent v2 — 架构设计文档

> 定位：面向 AI PM 求职作品集的 Academic Paper Review & Revision Agent
> 核心主张：Same model, better harness, better results.

---

## 一、当前状态（v1 Baseline）

当前 ScholarAgent 是一个标准的 Harness pattern 实现：

- `main.py`：~100 行 agent loop + tool dispatch + context compression
- `tools/review_engine.py`：5 角色并行 review（editor/theory/methodology/logic/literature）
- `tools/write_engine.py`：单 section rewrite，加载 domain knowledge
- `skills/`：review_criteria.md + econ_writing.md
- 上下文管理：micro_compact + auto_compact（TOKEN_THRESHOLD=40K）

v1 的问题：
1. Review → Rewrite 是一条直通管道，缺乏验证回路（写完就算完）
2. 所有 issue 只有一种处理方式：auto rewrite（没有 guidance/confirm 区分）
3. 没有 de-AI 检测——改完可能比原文更像 AI 写的
4. domain knowledge 加载是粗暴的关键词匹配，没有按阶段隔离
5. 没有统计验证能力（Stata/R），方法论 issue 只能给建议不能验证

---

## 二、设计目标（v2 新增三大能力）

| # | 能力 | 对应架构模式 | 核心价值 |
|---|------|------------|---------|
| 1 | Budget-aware Guidance Mode | Dry-Run Pattern | 同一系统适配不同预算：穷则给指令，富则自动改 |
| 2 | De-AI Audit & Fix | PEV Loop (验证回路) | 修完后强制过检，确保输出不比原文更 AI |
| 3 | Stata MCP 统计验证 | Mental Loop (先模拟后执行) | 方法论 issue 不只靠语言判断，用代码验证 |

---

## 三、整体架构

```
┌─────────────────────────────────────────────────────────┐
│                    ScholarAgent v2                        │
│                                                          │
│  ┌──────────────────────────────────────────────────┐   │
│  │           Meta-Controller (入口分诊)               │   │
│  │   user_input → classify → route to phase          │   │
│  └──────────┬──────────────┬──────────────┬─────────┘   │
│             │              │              │              │
│     ┌───────▼───────┐ ┌───▼────────┐ ┌──▼──────────┐   │
│     │  Review Phase │ │Revise Phase│ │ Verify Phase │   │
│     │  (Workflow)   │ │ (PEV Loop) │ │  (Audit)     │   │
│     └───────────────┘ └────────────┘ └─────────────┘   │
│                                                          │
│  ┌──────────────────────────────────────────────────┐   │
│  │              Shared State (文件化工作记忆)          │   │
│  │  issues.json / revision_state.json / skill files  │   │
│  └──────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

### 3.1 Agent Red Lines（不可违反的底线）

无论处于哪个 Phase、哪种 budget mode，以下三条规则硬编码在代码层，不由 model 判断：

**Red Line 1：不改变作者核心论点**

Agent 永远不能修改论文的核心主张（thesis statement）或因果方向。即使 review 阶段认为论点有问题，处理方式只能是 `guidance`（告诉作者"你可能需要重新考虑这个论点"），不能 `auto_fix` 或 `confirm_fix` 去改写论点本身。

*Rationale*：ScholarAgent 是修改助手不是合著者。改变论点 = 改变论文的身份，这超出了任何自动化系统的授权边界。代码实现：在 action_type 分类后增加一道硬检查——如果 issue.location 命中 abstract/introduction 的 thesis sentence 且 fix 涉及因果方向变化，强制降级为 guidance。

**Red Line 2：不杜撰引用或数据**

rewrite 输出中不得出现原文未包含的事实性声称（数据数字、文献引用、实验结果）。如果 rewrite 引入了新信息，必须在 post-rewrite 检查中被拦截。

*Rationale*：学术论文中的虚假引用/数据是学术不端，后果严重且不可逆。代码实现：rewrite 后做 diff 检查——新增的 `\cite{}`、数字、百分比等 pattern 如果在原文中不存在，标记为 suspicious 并转为 confirm_fix（让用户确认这些新增内容是否正确）。

**Red Line 3：De-AI fix 不得使表达变得更差**

当 `fix_ai_signals` 的修复让句子可读性明显下降或引入语法错误时，宁可保留 AI 味也不接受一个错误的 fix。

*Rationale*：去 AI 味是锦上添花，但表达正确性是底线。用一个更差的句子替换一个"有 AI 味但正确"的句子，是净损失。代码实现：fix 后对比原句和新句的 perplexity / fluency score，如果新句显著更差（超过阈值），保留原句并标记 "ai_signal_retained: fluency_tradeoff"。

---

## 四、Issue-Based Action Routing（核心创新点）

v2 的核心设计变化：review 产出的每个 issue 不再只是 "一条待修 finding"，而是一个 **带 action_type 标签的可路由对象**。

### 4.1 Issue 结构（扩展后）

```json
{
  "id": "ISS-007",
  "severity": "major",
  "category": "overclaim",
  "location": {"section_id": "04_results", "quote": "..."},
  "description": "Claims causal effect but design is observational",
  "suggestion": "Reframe as correlational or add IV discussion",
  
  "action_type": "confirm_fix",
  "action_rationale": "Involves core argument framing — needs author intent",
  "budget_tier": "medium",
  "fix_complexity": "sentence_level"
}
```

### 4.2 三种 action_type

| action_type | 含义 | 触发条件 | 行为 |
|-------------|------|----------|------|
| `auto_fix` | Agent 直接改，不问人 | 明确的技术问题：语法、格式、引用格式、逻辑连接词 | rewrite_section → de-AI audit → done |
| `confirm_fix` | Agent 提出修改方案，人批准后执行 | 涉及核心论点、可能改变作者意图的修改 | generate_fix_proposal → ask_user → execute/skip |
| `guidance` | 只给修改指令，不动手改 | 需要作者提供新信息（数据、实验、引用）；超出语言修改范畴 | output instructions → user self-fix |

### 4.3 Action Type 分类器

action_type 的分类不由规则硬编码，而由 consolidation 阶段的 LLM 判断，判断依据：

```
IF issue requires information not in the paper (new data, new experiments):
    → guidance
ELIF issue touches core argument framing / author's subjective choice:
    → confirm_fix  
ELIF issue is clearly fixable from existing text (grammar, structure, citation format, logical connectors):
    → auto_fix
```

**设计选择：为什么是 3 种而不是 2 种或 5 种？**

考虑过的替代方案：

- *2 种（auto vs guidance）*：缺少中间态。很多 issue 既不是纯技术改动（可以 auto），也不是需要用户提供新信息（只能 guidance），而是"我能改，但涉及你的意图，改之前问一句"。没有 confirm_fix 会导致要么越权自动改（用户不满），要么所有非trivial issue 都降级为 guidance（系统价值降低）。
- *5 种（加入 batch_fix / deferred_fix）*：batch_fix（攒一组一起改）和 deferred_fix（标记但不立即处理）在概念上合理，但增加了路由复杂度和用户理解成本。更重要的是，batch 行为可以通过 first-of-type 验收机制实现（同类第一个 confirm，后续自动 auto），不需要独立的 action_type。

结论：3 种 action_type 是"用户决策粒度"和"系统复杂度"之间的 sweet spot。

### 4.4 Budget-Aware Mode

用户启动时声明预算等级，系统据此调整行为边界：

| 预算等级 | auto_fix | confirm_fix | guidance |
|---------|----------|-------------|----------|
| `full` (默认) | 直接执行 | 生成方案→人确认→执行 | 输出指令 |
| `medium` | 直接执行 | 降级为 guidance（只给方案不执行） | 输出指令 |
| `minimal` | 降级为 guidance | 降级为 guidance | 输出指令 |

这意味着 `--budget minimal` 时，ScholarAgent 变成一个纯 reviewer + advisor，零 rewrite token 消耗。

---

## 五、De-AI Audit（PEV 验证回路）

### 5.1 设计原则

- De-AI 不是嵌入 rewrite prompt 的一条规则，而是**独立的 post-rewrite verifier**
- 写稿 Agent 和审稿 Agent 必须分离（对抗自评失真）
- 修复粒度是句级，不允许"因为一句 AI 味重写整段"

**设计选择：为什么是独立 verifier 而不是嵌入 rewrite prompt？**

考虑过的替代方案：在 rewrite_section 的 system prompt 里直接加入 de-AI 规则（"改写时避免以下 AI 特征词..."）。这样实现更简单，只需一轮 LLM 调用。

不采用的原因：(1) 自评偏差——让同一个 prompt/调用既负责"写"又负责"检查自己写得像不像 AI"，等于让考生自己给自己判卷；(2) 注意力竞争——rewrite prompt 已经需要关注语义正确性、论证逻辑、学科规范，再加 18 条 de-AI 规则会稀释每条规则的执行力度；(3) 不可调试——如果 de-AI 效果不好，嵌入模式下无法独立地看到"检测了什么、检出了什么、修了什么"，而独立 verifier 的输入输出完全可观测。

代价：多一轮 LLM 调用（~500-1000 tokens input + ~200 output per section）。对 `--budget full` 可接受，对 `--budget medium` 仍可接受（de-AI 是轻量检测），对 `--budget minimal` 不触发（因为没有 rewrite 所以不需要 audit）。

### 5.2 Pipeline

```
rewrite_section(section_id)
        │
        ▼
┌─────────────────┐
│  deai_audit()   │  ← 独立 Agent，独立 prompt，独立上下文
│                 │
│  Input:         │
│    - revised_text (rewrite 输出)
│    - original_text (修改前版本)
│    - scene_config (学术论文 → S1/S3 规则)
│                 │
│  Output:        │
│    DeAIVerdict  │
└────────┬────────┘
         │
    ┌────▼────┐
    │ PASS?   │
    └────┬────┘
     Yes │  No
     │   │
     ▼   ▼
   done  fix_ai_signals() → deai_audit() → ... (max 2 retries)
```

### 5.3 DeAIVerdict 结构

```python
class DeAIVerdict(BaseModel):
    is_natural: bool                    # 总判定：通过/不通过
    overall_score: float                # 0-1, 1=完全自然
    signals: List[AISignal]             # 检出的具体 AI 痕迹
    
class AISignal(BaseModel):
    sentence: str                       # 原句
    signal_type: str                    # e.g., "inflated_symbolism", "rule_of_three", "em_dash_overuse"
    confidence: float                   # 检测置信度
    fix_suggestion: str                 # 句级修改建议
```

### 5.4 De-AI 规则来源

从用户的 `deai-writing` Skill 提取核心规则，按场景路由：

- **学术论文英文**：S1 场景 → 18 条 Universal Rules (U1-U18) + 学术特定补充
- **学术论文中文**：S3 场景 → 中文特征规则
- **通用**：Voice Profile 模块 + 4 层自检协议

不全量加载 402 行 Skill 内容。只在 deai_audit 被调用时，按当前论文语言路由加载对应子集（约 80-120 行关键规则）。

### 5.5 Fix 策略：最小切片

```python
def fix_ai_signals(revised_text: str, signals: List[AISignal]) -> str:
    """
    逐句修复，不动没问题的句子。
    每个 signal 只改对应的 sentence，保持上下文不变。
    """
    for signal in signals:
        # 只替换被标记的那一句
        revised_text = revised_text.replace(
            signal.sentence, 
            rewrite_single_sentence(signal.sentence, signal.fix_suggestion)
        )
    return revised_text
```

### 5.6 失败处理

| 失败场景 | 处理方式 |
|---------|---------|
| deai_audit 连续 2 轮分数无提升（score 差 < 0.05） | 停止重试，输出"以下句子建议手动调整" + 具体 signal 列表。标记 `deai_pass: false, note: "plateau reached"` |
| fix_ai_signals 产出的句子比原句更差（触发 Red Line 3） | 保留原句，标记 `ai_signal_retained: fluency_tradeoff`，跳过该 signal 继续处理其余 |
| rewrite_section 返回与原文无实质差异 | 标记该 issue 为 `fix_attempted_no_change`，跳过 deai_audit 环节 |

---

## 六、Stata MCP 统计验证（Mental Loop）

### 6.1 定位

不是所有 review issue 都需要 Stata。只有 methodology reviewer 标记的特定类型 issue 才触发：

- 样本量充分性质疑
- 统计检验选择质疑
- 回归模型设定质疑
- 稳健性检验缺失

### 6.2 集成方式

```
methodology_reviewer 产出 issue
        │
        ▼
  issue.needs_statistical_verification = true
        │
        ▼
┌──────────────────────────┐
│  stata_verify(issue)     │
│                          │
│  1. 从 issue 生成 Stata 代码（do file）
│  2. 调用 Stata MCP 执行
│  3. 解读输出结果
│  4. 更新 issue.verification_result
└──────────────────────────┘
```

### 6.3 Graceful Degradation

Stata MCP 是可选依赖。系统启动时检测是否可用：

```python
def check_stata_availability() -> bool:
    """Try to connect to Stata MCP server."""
    ...

# 如果 Stata 不可用：
# - methodology issues 仍然正常产出
# - 但 needs_statistical_verification 的 issue 标记为 "unverified"
# - guidance 里注明 "建议用 Stata 验证：[生成的代码]"
```

不可用时不报错、不降级体验，只是验证变成 "建议" 而非 "已验证"。

### 6.4 失败处理

| 失败场景 | 处理方式 |
|---------|---------|
| Stata MCP 连接超时（>30s） | 输出已生成的 .do 代码 + "请手动运行后告知结果"，标记 issue 为 `verification: timeout` |
| Stata 代码执行报错（语法/数据不匹配） | 输出错误信息 + .do 代码 + 修改建议，标记 `verification: execution_error` |
| Stata 验证结果与论文声称不一致 | 只报告差异（"论文声称 p<0.05，Stata 输出 p=0.12"），不自动修改论文。action_type 强制为 guidance（Red Line 1 约束——这可能涉及核心论点） |

---

## 七、分阶段上下文管理

### 7.1 原则

不一股脑灌所有 skill 内容。每个阶段只加载当阶段必需的知识：

| 阶段 | 加载内容 | 不加载 |
|------|---------|--------|
| Review Phase | review_criteria.md | econ_writing.md, de-AI rules |
| Rewrite Phase | econ_writing.md (当前 section 相关部分) | review_criteria.md, de-AI rules |
| De-AI Audit Phase | deai_rules_{lang}.md (按语言路由的子集) | 其他所有 |
| Stata Verify | methodology checklist (精简版) | 写作规则 |

### 7.2 实现：skill 文件拆分

```
skills/
├── review_criteria.md          # Review Phase 专用
├── econ_writing.md             # Rewrite Phase 专用
├── deai_rules_en.md            # De-AI Phase 专用（英文学术）
├── deai_rules_zh.md            # De-AI Phase 专用（中文学术）
└── methodology_checklist.md    # Stata verify 专用
```

---

## 八、Revision State（文件化工作记忆）

### 8.1 为什么需要

当修改过程跨多轮对话时，Agent 需要知道：哪些 issue 已处理、哪些被用户否决、哪些 de-AI 检测未通过需要重试。这些信息不能只活在对话历史里（会被 compress 掉）。

### 8.2 结构

```json
// .workspace/revision_state.json
{
  "budget_mode": "full",
  "paper_language": "en",
  "total_issues": 12,
  "issues_status": {
    "ISS-001": {"status": "fixed", "deai_pass": true, "attempts": 1},
    "ISS-002": {"status": "fixed", "deai_pass": false, "attempts": 2, "note": "max retries reached"},
    "ISS-003": {"status": "user_rejected", "reason": "author prefers original framing"},
    "ISS-004": {"status": "pending", "action_type": "confirm_fix"},
    "ISS-005": {"status": "guidance_issued", "instructions": "..."},
    "ISS-006": {"status": "pending", "action_type": "auto_fix"},
  },
  "first_fix_validated": false,
  "validated_categories": []
}
```

### 8.3 "第一个同类验收"机制

借鉴 web-video-presentation Skill 的"第一章验收"设计：

- 同类型 issue（如 "overclaim" 类别）的第一个修复必须让用户确认
- 用户确认后，该类别记入 `validated_categories`
- 后续同类型 issue 自动降级为 `auto_fix`（不再逐个问人）

这既保证了人对修改方向的掌控，又避免了 12 个 issue 要确认 12 次的疲劳。

---

## 九、新增 Tool 定义

在 v1 的 12 个 tool 基础上，v2 新增 4 个：

| Tool | 用途 | 调用时机 |
|------|------|---------|
| `deai_audit` | 对一个 section 的修改后文本做 AI 痕迹检测 | rewrite_section 之后自动触发 |
| `fix_ai_signals` | 根据 deai_audit 的检出结果做句级修复 | deai_audit 返回 is_natural=false 时 |
| `generate_fix_proposal` | 生成修改方案但不执行（Dry-Run） | confirm_fix 类型 issue |
| `stata_verify` | 调用 Stata MCP 验证统计结论 | methodology issue 且 needs_statistical_verification=true |

### Tool 关系图

```
review_paper
    │
    ▼ (产出 issues.json，每个 issue 带 action_type)
    │
    ├── action_type = "auto_fix"
    │       │
    │       ▼
    │   rewrite_section → deai_audit → [pass] → done
    │                         │
    │                    [fail] → fix_ai_signals → deai_audit → ...
    │
    ├── action_type = "confirm_fix"
    │       │
    │       ▼
    │   generate_fix_proposal → ask_user → [approve] → rewrite_section → deai_audit
    │                                    → [reject] → skip, mark user_rejected
    │
    └── action_type = "guidance"
            │
            ▼
        output instructions (zero token cost beyond generation)
```

---

## 十、Self-Improvement（长期演化）

### 10.1 Gold Standard Memory

借鉴 17 种架构文章中 Self-Improvement Loop 的设计：

```python
# 每次 deai_audit 一次性通过（首次即 pass）的修改，沉淀为 gold example
# 存入 .workspace/gold_examples/
# 后续 rewrite 时作为 few-shot 参考
```

### 10.2 Skill 自演化（来自阿里文章）

```
每次 session 结束后，检查：
  - 哪些 issue 重复出现？→ 考虑更新 review_criteria.md
  - 哪些 de-AI fix 反复失败？→ 考虑更新 deai_rules
  - 哪类 issue 用户总是 reject？→ 考虑调整 action_type 分类逻辑

方式：binary eval (pass/fail) + reflection + patch
```

这部分在 v2.0 中不实现，作为 v2.1 的演化方向标注在此。

---

## 十一、文件变更清单

### 新增文件

```
tools/deai_engine.py          # De-AI audit + fix 逻辑
tools/stata_verify.py         # Stata MCP 集成（可选）
tools/action_router.py        # Issue action_type routing 逻辑
skills/deai_rules_en.md       # De-AI 英文规则（从 deai-writing Skill 提取）
skills/deai_rules_zh.md       # De-AI 中文规则
skills/methodology_checklist.md
```

### 修改文件

```
main.py                       # 新增 4 个 tool 定义 + handler + --budget 参数
tools/review_engine.py        # consolidation 阶段新增 action_type 分类
tools/write_engine.py         # rewrite 后自动触发 deai_audit
skills/review_criteria.md     # 微调（增加 needs_statistical_verification 标记指引）
```

---

## 十二、实现优先级

分三步实施，每步可独立验证：

### Step 1：Issue Action Routing + Guidance Mode（最小可用）
- 修改 review_engine.py consolidation，产出 action_type
- 新增 `generate_fix_proposal` tool
- main.py 增加 `--budget` 参数
- 验证：`--budget minimal` 跑一篇 paper，只产出 review + guidance，零 rewrite

### Step 2：De-AI Audit Loop（核心差异化）
- 新增 `tools/deai_engine.py`
- 从 deai-writing Skill 提取规则写入 `skills/deai_rules_en.md`
- 修改 write_engine.py，rewrite 后自动调 deai_audit
- 新增 `fix_ai_signals` tool + retry 逻辑
- 验证：rewrite 一个 section，观察 deai_audit 是否能检出 AI 痕迹并修复

### Step 3：Stata MCP 集成（锦上添花）
- 新增 `tools/stata_verify.py`
- Graceful degradation 逻辑
- 验证：有/无 Stata 两种场景都能正常工作

---

## 十三、成功标准

作为作品集项目，ScholarAgent v2 需要展示的不是"AI能写论文"，而是：

1. **控制流设计能力**：同一个 model，通过 harness 设计产出更可控的结果
2. **人机协作设计**：budget-aware 模式体现了对不同用户场景的理解
3. **质量闭环**：de-AI audit 证明"系统知道自己什么时候做得不够好"
4. **优雅降级**：Stata 不可用时不 crash，guidance mode 零成本
5. **工程品味**：分阶段上下文、最小切片修复、第一个同类验收

面试时的一句话总结：
> "I didn't make the model smarter. I made the harness know when to act, when to ask, and when to stop."

---

## 十四、v2.1 展望（设计已确认，v2.0 不实现）

以下三个能力方向经过评估认为有价值，但考虑到 v2.0 的 scope 控制和"能讲深比覆盖广更重要"的作品集原则，标注为 v2.1 实现。

### 14.1 Paper Memory（跨 Session 记忆）

**问题**：长 session 中 context 被 compress 后，Agent 丢失"这篇论文在讲什么"和"用户之前确认过什么风格偏好"；同一作者写不同论文时，每次从零开始理解其偏好。

**设计方向**（借鉴 career-copilot 的两类记忆分离）：

| 记忆类型 | 存储位置 | 内容 | 生命周期 |
|---------|---------|------|---------|
| Session Memory | `.workspace/revision_state.json`（已有） | 当前论文的 issue 状态、已确认的修改 | 单篇论文修改周期 |
| Author Profile | `.workspace/author_profile.json`（新增） | 作者写作偏好、确认过的修改边界、拒绝过的修改类型 | 跨论文持久 |

**Author Profile 结构（草案）**：

```json
{
  "style_preferences": {
    "sentence_length_preference": "short",  // 用户多次选择短句版本
    "passive_voice_tolerance": "high",      // 用户多次 reject 被动改主动
    "hedging_preference": "keep"            // 用户偏好保留学术 hedging
  },
  "rejected_patterns": [
    {"category": "overclaim", "reason": "author prefers cautious framing"},
    {"category": "passive_to_active", "reason": "econ convention"}
  ],
  "confirmed_constraints": [
    {"category": "citation_format", "constraint": "APA 7th, always include DOI"}
  ]
}
```

**读取策略**：Session 开始时自动加载 Author Profile（如果存在），作为 rewrite 和 action_type routing 的 prior。不一次性灌入 system prompt，而是在相关决策点按需注入。

**与 v2.0 的关系**：v2.0 的 `validated_categories` + `first_fix_validated` 已经是这个方向的最小实现。v2.1 的进化是把这些运行时状态沉淀为跨 session 的持久 profile。

### 14.2 Voice Profile（作者风格量化注入）

**问题**：rewrite 后的文本读起来"像通用学术英语"而不像"这个作者写的"。不同学者的写作风格差异巨大（Cochrane 极简直接，McCloskey 修辞丰富），通用改写规则无法适配。

**设计方向**（借鉴 deai-writing Skill 的 Voice Profile 模块）：

**激活条件**：用户主动提供 1-2 段自己满意的已发表文本作为 style sample。不提供时系统正常运行，提供后激活 Voice Profile 模块。

**量化流程**：

```
用户提供 style_sample.txt
        │
        ▼
┌─────────────────────────┐
│  extract_voice_profile() │
│                          │
│  硬指标（可靠）：          │
│    - 平均句长 ± std       │
│    - 段落平均长度          │
│    - em-dash / 分号 / 冒号频率 │
│    - 被动语态比例          │
│    - 第一人称使用频率       │
│                          │
│  软指标（实验性）：         │
│    - 偏好短句开头 vs 长句开头 │
│    - 论证推进方式（问句/陈述/让步转折）│
│    - 高频连接词偏好        │
└────────────┬────────────┘
             │
             ▼
      voice_profile.json
```

**注入方式**：rewrite 时在 prompt 中注入 voice_profile 的硬指标作为约束（"保持平均句长在 X±20% 范围内"）。

**优先级规则**（直接采用 deai-writing 的设计）：当 Voice Profile 与 de-AI 通用规则冲突时（如作者风格本身句长均匀，与 U7 Burstiness 冲突），Voice Profile 优先——目标是"像这个作者"而非"像通用人类"。

**与 v2.0 的关系**：v2.0 的 de-AI audit 使用通用规则集（deai_rules_en.md / deai_rules_zh.md）。v2.1 增加 Voice Profile 后，audit 的判断基线从"通用学术人类"变为"这个具体作者"，更精确也更少误报。

### 14.3 Section-level Parallel Processing（并行修改）

**问题**：一篇论文 12 个 auto_fix issue 分布在 5 个 section，串行处理时间线性增长。

**设计方向**（借鉴 omo 的 context pack forwarding 模式）：

**核心 insight**：并行粒度是 section 级而非 issue 级。同一 section 内的多个 issue 必须串行（fix A 可能改变 fix B 所在的句子），不同 section 的 issue 天然独立。

**架构**：

```
                     issues.json
                         │
                         ▼
              ┌─── group_by_section() ───┐
              │          │               │
     section_01    section_03      section_05
     [ISS-001]     [ISS-003,      [ISS-007,
                    ISS-004]       ISS-009]
         │              │               │
         ▼              ▼               ▼
    ┌─────────┐   ┌─────────┐   ┌─────────┐
    │ Context │   │ Context │   │ Context │
    │  Pack 1 │   │  Pack 2 │   │  Pack 3 │
    └────┬────┘   └────┬────┘   └────┬────┘
         │              │               │
    (parallel)    (parallel)      (parallel)
         │              │               │
    rewrite+audit  rewrite+audit  rewrite+audit
         │              │               │
         └──────────────┼───────────────┘
                        ▼
              merge into revision_state.json
```

**Context Pack 内容**（每个并行单元独立完整）：
- 该 section 的完整原文
- 该 section 对应的 issue 列表（含 action_type）
- voice_profile.json（如果有）
- 当前 budget_mode
- 对应的 deai_rules 子集

**约束**：
- confirm_fix 类型 issue 不进入并行——它们需要用户交互，必须串行等待。
- 并行数 ≤ API rate limit 允许的并发请求数（默认 3）。
- 如果某个 section 的 rewrite 失败，不影响其他 section 继续。失败的 section 标记为 pending，后续可单独重试。

**与 v2.0 的关系**：v2.0 使用简单串行循环（遍历 issues → 逐个处理）。v2.1 的并行化只是将循环改为 section-grouped + concurrent execution，核心逻辑（rewrite → deai_audit → fix）不变。这是纯工程优化，不改变设计语义。

---

## 十五、设计审视记录（Rationale Log）

记录本设计过程中做出的关键取舍，供面试时展示"思考过程"：

| 取舍点 | 选择 | 放弃 | 原因 |
|--------|------|------|------|
| De-AI 实现方式 | 独立 verifier | 嵌入 rewrite prompt | 自评偏差 + 可调试性（见 §5.1） |
| action_type 粒度 | 3 种 | 2 种或 5 种 | 决策粒度 vs 系统复杂度的 sweet spot（见 §4.3） |
| first-of-type 机制 | 简单布尔开关 | 约束提取 + 代码层验证 | 实现成本 vs 展示价值不对称；v2.1 演化 |
| 并行策略 | v2.0 串行 | Section-level parallel | 串行已足够快（12 issue ≈ 6 min），并行增加复杂性，v2.1 再做 |
| Stata 集成方式 | 可选依赖 + graceful degradation | 硬依赖 | 大多数用户没有 Stata；不能让核心功能依赖于小众工具 |
| Memory 系统 | v2.0 只有 session 级 revision_state | 跨 session author profile | scope control；先验证核心 loop 有效再加持久记忆 |
| Meta-Controller 实现 | 函数级隔离（同一 main.py 内） | 文件级隔离（multi-agent 架构） | 代码总量 <1000 行时，过度模块化是 over-engineering |
