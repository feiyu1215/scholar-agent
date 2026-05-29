# ScholarAgent 升级计划（认知能力导向 v2）

> **核心原则**: 每一项改动都必须回答"这如何让 Agent 审稿审得更好？"。如果答案是"让代码更整洁但审稿质量不变"——砍掉。
> **设计哲学**: Agent = cognition, not orchestration. Constrain, don't control.
> **编制依据**: COGNITIVE_ANCHOR.md + REFERENCE_ANALYSIS.md + ARCHITECTURE_V2_BLUEPRINT.md + PROGRESS.md (Phase 1-12) + 当前代码实际状态
> **日期**: 2025-07

---

## 为什么重写这份计划

上一版方案把"目录迁移"、"Decision Audit Trail"、"规则驱动的 Session Memory"放在优先位置。这些要么服务开发者（不是 Agent），要么用"安全低成本"的方式回避了真正有价值的实现。

本版的判断标准（来自 COGNITIVE_ANCHOR §9 自检）：
1. "一个人类审稿专家会想要这个能力吗？" — Yes → 做
2. "这是在帮 Agent 还是在帮维护者？" — 帮维护者 → 降级或砍掉
3. "代价高但质量好 vs 代价低但质量差" — 选前者（用户原话：代价可以承受）

---

## 执行优先级总览

```
P0 — 直接提升审稿认知深度
├── M1: Session Memory Manager（LLM 子任务版）
├── M2: Smart Compaction 恢复质量升级
└── B1: 论文结构预索引（Paper Mental Model）

P1 — 让已有能力真正生效
├── R1: Procedural Memory 回注（跨会话学习闭环）
├── H1: HD-WM 假说可见性（Agent 意识到自己的假说）
└── Q1: Finding 质量自评（输出前自检）

P2 — 认知策略扩展
├── S1: Paper-Type 自适应认知策略
├── K1: 审稿结束时生成认知图谱（结构化产出）
└── E1: 失败模式规则提炼（防止已知认知错误复发）

X — 工程维护（不阻塞认知改进时顺手做）
├── X1: 目录结构迁移（仅在需要加新文件时触发）
└── X2: harness.py 决策注释（维护者导向，低优先级）
```

---

## P0：直接提升审稿认知深度

### M1. Session Memory Manager — LLM 子任务版

#### 为什么这是最高优先级

Smart Compaction 是 Agent 的"睡眠"——当 context 太长时，它必须"醒来"时还记得关键判断。当前的 compaction 恢复只包含 findings 列表和基本状态。但人类审稿人在中断后恢复时，脑子里有的不是"findings 列表"，而是：

- "这篇论文的 IV 策略有问题，first-stage 很弱"（methodology_assessment）
- "创新点声称是第一个做 X 的，但我隐约记得有前作"（novelty_judgment）
- "统计部分的 standard error clustering 可能不对"（statistical_observations）

这些是**判断**，不是**事实**。规则提取不出判断。必须用 LLM。

#### 设计方案

```python
"""
Session Memory Manager — 会话进行中的认知笔记本。

核心理念：这是 Agent 的"审稿笔记"，在压缩恢复后，Agent 读到这份笔记
就能立即恢复到"我正在审一篇 DID 论文，方法有问题"的状态，
而不是从零重新理解所有 findings。

与 memory.py 的 SessionRecord 区别：
  - SessionRecord: 会话结束后的沉淀物，用于跨会话长期记忆
  - SessionMemoryManager: 会话进行中的实时笔记，用于 Compaction 恢复

更新机制：
  - 使用轻量 LLM 调用（~500 token prompt + ~300 token output）
  - 在"认知断点"时触发（不是每轮）
  - 认知断点: 读完一个核心 section / 新增重要 finding / 阶段转换
"""

@dataclass
class SessionMemory:
    """9 段结构化认知笔记。"""
    
    # 基本定位
    task_summary: str = ""          # "审阅一篇关于 XXX 的实证论文"
    current_focus: str = ""         # "正在检查 robustness checks 的充分性"
    
    # 核心认知判断（这是规则提取不出来的部分）
    methodology_assessment: str = ""  # "DID with staggered adoption, 未报告 pre-trends"
    evidence_quality: str = ""        # "Figure 3 的 CI 极宽，Table 2 缺 first-stage"
    novelty_judgment: str = ""        # "声称首创但 Smith(2019) 似乎已做过类似工作"
    
    # 累积观察
    statistical_observations: str = ""  # "SE 只 cluster 到 state 级别，可能不够"
    writing_quality: str = ""           # "Introduction 冗长，Results 缺乏解读"
    
    # 决策轨迹
    key_decisions: str = ""    # "决定深入检查 IV validity 因为 first-stage 很可疑"
    issue_timeline: str = ""   # "Sec2: 发现 assumption 未讨论; Sec4: F-stat 缺失..."


class SessionMemoryManager:
    """管理 Session Memory 的更新和注入。"""
    
    # 更新 prompt 模板——给 LLM 的指令
    UPDATE_PROMPT = """You are maintaining review notes for an academic paper reviewer.
Based on the reviewer's recent actions and observations, update the structured notes.

CURRENT NOTES:
{current_memory}

RECENT ACTIVITY (since last update):
{recent_activity}

NEW FINDINGS ADDED:
{new_findings}

Instructions:
- Update only fields that have new information. Leave others unchanged.
- Write concise expert-level observations, not verbose descriptions.
- methodology_assessment: What do you now think about the paper's method?
- evidence_quality: What's the state of evidence/data quality?
- novelty_judgment: Any updates on how novel this really is?
- Keep each field under 80 words.
- Write in the reviewer's voice (first person, definitive judgments).

Return the updated notes as JSON matching the schema."""

    def __init__(self, llm_client, compaction_engine):
        self._llm = llm_client
        self._compaction = compaction_engine
        self._memory = SessionMemory()
        self._update_count = 0
        self._last_update_round = 0
    
    def should_update(self, state: WorkspaceState) -> bool:
        """判断是否到了"认知断点"——该更新笔记了。"""
        rounds_since_update = state.current_round - self._last_update_round
        
        # 条件 1: 刚读完一个核心 section
        just_read_core_section = self._detect_section_completion(state)
        
        # 条件 2: 新增了重要 finding（severity >= major）
        new_major_findings = self._count_new_major_findings(state)
        
        # 条件 3: 阶段转换
        phase_changed = state.phase_just_changed
        
        # 条件 4: 距上次更新已过 3+ 轮（兜底）
        time_based = rounds_since_update >= 3
        
        return just_read_core_section or new_major_findings >= 2 or phase_changed or time_based
    
    async def update(self, state: WorkspaceState, recent_activity: str, new_findings: list) -> SessionMemory:
        """调用 LLM 更新 Session Memory。"""
        prompt = self.UPDATE_PROMPT.format(
            current_memory=self._memory.to_json(),
            recent_activity=recent_activity,
            new_findings=self._format_findings(new_findings),
        )
        
        response = await self._llm.structured_output(
            prompt=prompt,
            schema=SessionMemory,
            max_tokens=400,
            temperature=0.1,  # 低温度——我们要一致性不要创造性
        )
        
        self._memory = response
        self._update_count += 1
        self._last_update_round = state.current_round
        return self._memory
    
    def format_for_restoration(self) -> str:
        """格式化为 Smart Compaction 恢复时的注入文本。"""
        return f"""[审稿认知笔记 — 你在压缩前的判断状态]
任务: {self._memory.task_summary}
当前关注: {self._memory.current_focus}

方法论判断: {self._memory.methodology_assessment}
证据质量: {self._memory.evidence_quality}
创新性判断: {self._memory.novelty_judgment}
统计问题: {self._memory.statistical_observations}
写作质量: {self._memory.writing_quality}

关键决策: {self._memory.key_decisions}
问题时间线: {self._memory.issue_timeline}

[注意: 以上是你之前的判断，你可以修正它们，但不要遗忘。]"""
```

#### 成本分析

- 每次更新: ~800 tokens (prompt) + ~300 tokens (output) = ~1100 tokens
- 一次完整审稿中触发 4-6 次 = 5000-7000 额外 tokens
- 对比一次审稿总消耗 50000-100000 tokens，这是 5-10% 的额外开销
- **换来的是**: 压缩恢复后 Agent 不丢失认知判断。这是质的改变。

#### 集成点

1. **Harness 主循环**: 每轮结束时检查 `should_update()`，如果是则异步调用 `update()`
2. **Smart Compaction**: `_build_restoration_context()` 中注入 `format_for_restoration()` 输出
3. **Context Assembler**: 不需要改——Session Memory 只在恢复时用，不在正常 context 中占空间

#### 预计工作量: 3-4 天

---

### M2. Smart Compaction 恢复质量升级

#### 为什么这重要

当前 compaction 恢复后，Agent 看到的是：
- findings 列表（有）
- 当前 phase（有）
- 读过哪些 section（有）

缺失的（恢复后 Agent 不知道的）：
- 自己之前对方法论的累积判断
- 为什么决定深入某个方向
- 假说状态（如果 HD-WM 激活）
- 论文的结构心智模型

这就是为什么 M1 和 B1 是前置依赖——它们产出的内容正是恢复时需要注入的。

#### 实现方案

增强 `compaction.py` 的 `_build_restoration_context()`，恢复信息分层：

```python
def _build_restoration_context(self, state: WorkspaceState) -> str:
    """构建压缩恢复上下文。分层优先级确保最重要信息不被截断。"""
    
    layers = []
    
    # Layer 0 (必须): Findings — 永不压缩
    layers.append(("FINDINGS", self._format_all_findings(state), Priority.CRITICAL))
    
    # Layer 1 (必须): Session Memory 认知笔记 — 恢复判断
    if self._session_memory_manager:
        layers.append(("COGNITIVE_NOTES", 
                      self._session_memory_manager.format_for_restoration(),
                      Priority.HIGH))
    
    # Layer 2 (重要): HD-WM 假说状态
    if state.hypothesis_module and state.hypothesis_module.has_active():
        layers.append(("HYPOTHESES",
                      state.hypothesis_module.format_for_restoration(),
                      Priority.HIGH))
    
    # Layer 3 (有用): 论文结构索引（精简版）
    if self._paper_index:
        layers.append(("PAPER_STRUCTURE",
                      self._paper_index.format_for_context(max_tokens=500),
                      Priority.MEDIUM))
    
    # Layer 4 (参考): 读过的 sections 和进度
    layers.append(("PROGRESS", self._format_progress(state), Priority.LOW))
    
    # 按预算裁剪
    return self._assemble_within_budget(layers, budget_tokens=6000)
```

#### 关键设计决策

恢复上下文的**措辞**很重要（COGNITIVE_ANCHOR §4.3 — 认知辅助模式）：

```
# 好的措辞（信息呈现，Agent 自主决策）
"[审稿认知笔记] 你之前判断 IV 策略较弱。你可以验证或修正这个判断。"

# 坏的措辞（控制式指令）
"[系统指令] 你必须继续追查 IV 问题。"
```

#### 预计工作量: 1-2 天（在 M1 完成后）

---

### B1. 论文结构预索引 — Paper Mental Model

#### 为什么这让 Agent 更强

人类专家拿到一篇论文，第一件事不是从头到尾读——而是先翻一遍：
- 这篇多长？什么结构？
- 实验部分引用了哪些 Figure/Table？
- 方法论 section 引用了哪些方程？
- 哪些 section 互相引用密集（暗示逻辑依赖）？

有了这个"心智模型"，审稿人能做出更好的阅读决策："先读 Section 4 因为它是证据链的核心"。

#### 设计方案

```python
@dataclass
class CrossReference:
    """论文内部的一个交叉引用。"""
    source_section: str       # 引用发起的 section
    target_type: str          # "figure" | "table" | "equation" | "section"
    target_id: str            # "Figure 3a" | "Table 1" | "Eq. (5)" | "Section 3.2"
    context_snippet: str      # 引用所在句子（<60字符）

@dataclass  
class PaperStructureIndex:
    """论文预索引——Agent 的论文心智模型。"""
    
    # 骨架
    sections: list[str]                     # 有序 section 标题
    section_word_counts: dict[str, int]     # 各 section 体量
    
    # 内部引用网络
    cross_references: list[CrossReference]
    
    # 快捷视图
    evidence_map: dict[str, list[str]]      # figure/table → 引用它的 sections
    dependency_pairs: list[tuple[str, str]] # (A, B) = "A 的论证依赖 B 的内容"
    
    # 论文类型推断
    paper_type: str = "unknown"  # "empirical" | "theoretical" | "review"
    
    def get_reading_priority(self) -> list[str]:
        """基于引用密度推荐阅读优先级。被引用最多的 section 应该先读。"""
        # 被引次数多 = 其他 section 依赖它 = 核心 section
        ref_counts = Counter()
        for ref in self.cross_references:
            if ref.target_type == "section":
                ref_counts[ref.target_id] += 1
        return [s for s, _ in ref_counts.most_common()]
    
    def get_evidence_chain(self, claim_section: str) -> list[str]:
        """给定一个声称结果的 section，找出它依赖的所有证据（figure/table/equation）。"""
        return [ref.target_id for ref in self.cross_references 
                if ref.source_section == claim_section 
                and ref.target_type in ("figure", "table")]
    
    def format_for_context(self, max_tokens: int = 800) -> str:
        """格式化为注入 prompt 的文本。"""
        lines = ["[论文结构参考]"]
        lines.append(f"类型: {self.paper_type} | Sections: {len(self.sections)} | "
                    f"Figures: {self._count_type('figure')} | Tables: {self._count_type('table')}")
        
        # 结构概览
        lines.append("\n结构:")
        for sec in self.sections:
            wc = self.section_word_counts.get(sec, 0)
            refs = [r.target_id for r in self.cross_references if r.source_section == sec]
            ref_str = f" → 引用 {', '.join(refs[:4])}" if refs else ""
            lines.append(f"  {sec} ({wc}w){ref_str}")
        
        # 证据映射（最重要的 figure/table）
        if self.evidence_map:
            lines.append("\n核心证据使用:")
            for target, sources in sorted(self.evidence_map.items(), 
                                         key=lambda x: -len(x[1]))[:5]:
                lines.append(f"  {target} ← 被 {', '.join(sources)} 引用")
        
        # 阅读建议（参考性，非指令）
        priority = self.get_reading_priority()
        if priority:
            lines.append(f"\n[参考] 被引最多的 section: {', '.join(priority[:3])}")
        
        return "\n".join(lines)
```

#### 解析实现

```python
class PaperIndexBuilder:
    """从论文文本构建预索引。"""
    
    # 正则模式
    PATTERNS = {
        "figure": [r'[Ff]ig(?:ure)?\.?\s*(\d+[a-z]?)'],
        "table": [r'[Tt]able\s+(\d+[a-z]?)'],
        "equation": [r'[Ee]q(?:uation)?\.?\s*[(\[]?(\d+)[)\]]?'],
        "section": [
            r'[Ss]ection\s+(\d+(?:\.\d+)*)',
            r'[Ss]ec\.?\s*(\d+(?:\.\d+)*)',
            r'§\s*(\d+(?:\.\d+)*)',
        ],
    }
    
    def build(self, sections: dict[str, str]) -> PaperStructureIndex:
        """从已解析的论文 sections 构建索引。"""
        cross_refs = []
        for sec_name, sec_text in sections.items():
            for ref_type, patterns in self.PATTERNS.items():
                for pattern in patterns:
                    for match in re.finditer(pattern, sec_text):
                        target_id = self._normalize_target(ref_type, match.group(1))
                        context = self._extract_context(sec_text, match.start(), max_len=60)
                        cross_refs.append(CrossReference(
                            source_section=sec_name,
                            target_type=ref_type,
                            target_id=target_id,
                            context_snippet=context,
                        ))
        
        # 构建证据映射
        evidence_map = defaultdict(list)
        for ref in cross_refs:
            if ref.target_type in ("figure", "table"):
                evidence_map[ref.target_id].append(ref.source_section)
        
        # 推断论文类型
        paper_type = self._detect_paper_type(sections)
        
        return PaperStructureIndex(
            sections=list(sections.keys()),
            section_word_counts={k: len(v.split()) for k, v in sections.items()},
            cross_references=cross_refs,
            evidence_map=dict(evidence_map),
            dependency_pairs=self._infer_dependencies(cross_refs),
            paper_type=paper_type,
        )
    
    def _detect_paper_type(self, sections: dict[str, str]) -> str:
        """启发式论文类型判断。"""
        names_lower = [s.lower() for s in sections]
        
        has_experiment = any(k in n for n in names_lower 
                           for k in ("experiment", "result", "data", "empirical"))
        has_theory = any(k in n for n in names_lower 
                        for k in ("theorem", "proof", "lemma", "proposition"))
        has_method = any(k in n for n in names_lower 
                        for k in ("method", "model", "identification", "estimation"))
        
        if has_theory and not has_experiment:
            return "theoretical"
        if has_experiment and has_method:
            return "empirical"
        if len(sections) > 25:
            return "review"
        return "unknown"
    
    def _infer_dependencies(self, refs: list[CrossReference]) -> list[tuple[str, str]]:
        """推断 section 间的逻辑依赖。A 引用 B → A 可能依赖 B。"""
        pairs = []
        for ref in refs:
            if ref.target_type == "section":
                pairs.append((ref.source_section, ref.target_id))
        return list(set(pairs))
```

#### 注入方式

1. **构建时机**: `_load_paper()` 完成后立即构建（纯正则，<1秒）
2. **存储位置**: 作为 WorkspaceState 的字段
3. **Context 注入**: Assembler 在 INITIAL_SCAN 阶段注入完整索引（~800 tokens）；在 DEEP_REVIEW 阶段只注入与当前 section 相关的子集
4. **呈现措辞**: 始终用"参考"而非"事实"（COGNITIVE_ANCHOR §4.3 认知辅助模式）

#### 预计工作量: 2-3 天

---

## P1：让已有能力真正生效

### R1. Procedural Memory 回注 — 跨会话学习闭环

#### 问题

`memory.py` 已经有完整的 ProceduralPattern 存储（Layer 3）：
```python
@dataclass
class ProceduralPattern:
    """跨会话积累的审稿策略模式。"""
    pattern_id: str
    context: str           # 什么情况下这个策略有效
    strategy: str          # 策略描述
    effectiveness: float   # 历史有效性评分
    usage_count: int
```

这些 pattern 会在会话结束时沉淀。但**下一次审稿时从未被注入回 Agent context**。

这等于：Agent 有"长期记忆"但从不回想。一个有健忘症的专家。

#### 实现方案

```python
class ProceduralMemoryRecaller:
    """在审稿开始时，从长期记忆中召回相关策略。"""
    
    def recall_relevant_patterns(
        self, 
        paper_type: str,
        paper_topics: list[str],  # 从 abstract 提取的关键词
        max_patterns: int = 3,
    ) -> list[ProceduralPattern]:
        """基于论文类型和主题，召回最相关的历史策略。"""
        all_patterns = self._memory_store.get_all_procedural()
        
        # 相关性评分
        scored = []
        for pattern in all_patterns:
            score = self._relevance_score(pattern, paper_type, paper_topics)
            if score > 0.3:  # 阈值——只召回确实相关的
                scored.append((score, pattern))
        
        # 取 top-k
        scored.sort(reverse=True, key=lambda x: x[0])
        return [p for _, p in scored[:max_patterns]]
    
    def format_for_context(self, patterns: list[ProceduralPattern]) -> str:
        """格式化为可注入的文本。遵循 §4.3——信息呈现，不是指令。"""
        if not patterns:
            return ""
        
        lines = ["[你过去审类似论文时的有效策略 — 仅供参考，你决定是否采纳]"]
        for p in patterns:
            lines.append(f"• 场景: {p.context}")
            lines.append(f"  策略: {p.strategy}")
            lines.append(f"  (历史使用 {p.usage_count} 次, 有效性 {p.effectiveness:.0%})")
            lines.append("")
        return "\n".join(lines)
```

**关键措辞设计**（COGNITIVE_ANCHOR §4.3 第五种模式——知识赋予）：

```
# 正确: 信息呈现，Agent 自主决策
"[你过去审类似论文时的有效策略 — 仅供参考，你决定是否采纳]"
"• 场景: 审 DID 实证论文. 策略: 先查 pre-trends + first-stage F-stat"

# 错误: 指令式
"[你必须按以下策略执行]"
```

#### 集成点

1. **Harness 初始化**: 加载论文后，调用 `recall_relevant_patterns()` 
2. **Context Assembler**: 在 INITIAL_SCAN 阶段作为低优先级 section 注入（可被裁剪）
3. **会话结束时**: 从本次审稿轨迹中提取新 pattern → 更新 ProceduralPattern 存储

#### 预计工作量: 2 天

---

### H1. HD-WM 假说可见性

#### 问题

Phase 10 的设计是"HD-WM 自动运行，Agent 不需要知道"。但这导致一个问题：Agent 可能重复探索已有假说覆盖的方向。

人类专家审稿时，假说不是"后台自动运行"的——它是显式意识到的："我怀疑这个 IV 有问题，让我去验证"。

#### 最小改动方案

不需要改变 HD-WM 的自动增强机制——只需要在 Context Assembler 中让 Agent "看到"假说状态：

```python
# assembler.py 中新增一个 section
def _build_hypothesis_visibility_section(self, state: WorkspaceState) -> str:
    """让 Agent 意识到当前活跃假说。"""
    if not state.hypothesis_module or not state.hypothesis_module.has_active():
        return ""
    
    active = state.hypothesis_module.get_active_hypotheses()
    lines = ["[当前审稿假说 — 你的待验证猜想]"]
    for h in active[:3]:  # 最多展示 3 个
        evidence_count = len(h.supporting_evidence) + len(h.contradicting_evidence)
        lines.append(f"• {h.statement}")
        lines.append(f"  状态: {h.status} | 已收集 {evidence_count} 条证据")
    lines.append("[这些假说由你的过往观察自动生成。你可以追查、修正或忽略它们。]")
    return "\n".join(lines)
```

**为什么是"看到"而不是"被要求追查"**：

COGNITIVE_ANCHOR §4.3 明确说——信息呈现。Agent 看到假说后可能会：
- 主动去验证（好）
- 发现新证据修正假说（好）
- 决定假说不重要而忽略（也可以——Agent 有自主权）

Gate checker 仍然在最终退出时检查未追查的假说（已有功能），这是兜底。

#### 预计工作量: 半天

---

### Q1. Finding 质量自评

#### 为什么不等 20 次数据

上一版说"等积累 20 次审稿数据再做"。但这忽略了一个事实：Agent 现在就在输出 findings，其中可能有低质量的。等待就是每次审稿都输出低质量结果。

人类审稿人写完意见后会自检："这条意见是否有具体证据支撑？是否可操作？"

#### 实现方案——自评 Gate

不需要独立的 Judge Agent（成本太高）。在 `mark_complete` 触发的退出前，对每条 finding 做快速自评：

```python
class FindingQualityGate:
    """在审稿完成前，对 findings 做质量自检。"""
    
    # 质量维度（基于学术审稿规范）
    QUALITY_CHECKS = {
        "has_evidence": "是否引用了论文中的具体位置或数据？",
        "is_actionable": "作者能否根据这条意见做出具体修改？",
        "is_specific": "是否足够具体（非"写作需改进"这种空泛评价）？",
        "severity_justified": "severity 评级是否与问题实际影响匹配？",
    }
    
    def evaluate(self, findings: list[Finding]) -> list[QualityIssue]:
        """对每条 finding 做规则基础的质量检查。"""
        issues = []
        for f in findings:
            # Check 1: 有证据吗？
            if not f.evidence or len(f.evidence.strip()) < 20:
                issues.append(QualityIssue(
                    finding_id=f.id,
                    issue="缺乏具体证据",
                    suggestion="请指出论文中哪个具体段落/数据支撑这个判断",
                ))
            
            # Check 2: 可操作吗？
            if f.severity in ("major", "critical") and not self._has_actionable_suggestion(f):
                issues.append(QualityIssue(
                    finding_id=f.id,
                    issue="严重问题但未给出可操作建议",
                    suggestion="请说明作者可以如何修正此问题",
                ))
            
            # Check 3: 够具体吗？
            if self._is_vague(f.description):
                issues.append(QualityIssue(
                    finding_id=f.id,
                    issue="描述过于笼统",
                    suggestion="请用具体的数字、位置或例子来说明",
                ))
        
        return issues
    
    def _is_vague(self, text: str) -> bool:
        """检测是否是空泛表述。"""
        vague_patterns = [
            r"needs? improvement",
            r"could be better",
            r"is unclear",
            r"should be revised",
            r"写作需要改进",
            r"不够清楚",
        ]
        # 如果匹配了空泛模式且文本很短 → 可能过于笼统
        if len(text) < 50:
            return any(re.search(p, text, re.I) for p in vague_patterns)
        return False
    
    def format_nudge(self, issues: list[QualityIssue]) -> str:
        """格式化为 nudge 文本——提醒 Agent 改善而非阻止退出。"""
        if not issues:
            return ""
        return (
            f"[质量自检] 发现 {len(issues)} 条 findings 可能需要加强:\n"
            + "\n".join(f"  - Finding '{i.finding_id}': {i.issue}" for i in issues[:3])
            + "\n[你可以选择补充证据、修改描述，或确认当前已足够后退出。]"
        )
```

#### 集成点

1. **触发时机**: 在 `_check_completion_gate()` 中，findings 通过数量检查后
2. **行为**: 如果有质量问题，生成 nudge（不阻止退出，但提醒一下）
3. **遵循原则**: Constrain, don't control — Agent 可以选择忽略 nudge 并退出

#### 预计工作量: 1-2 天

---

## P2：认知策略扩展

### S1. Paper-Type 自适应认知策略

#### 设计思路

不同类型的论文，人类审稿人会调整策略：
- 实证论文: 重点查 identification strategy + robustness + data quality
- 理论论文: 重点查 proof 完整性 + assumption 合理性 + 与文献关系
- 综述论文: 重点查 coverage + 分类框架 + 是否遗漏重要文献

这不是"控制 Agent 做什么"——而是给 Agent 提供领域专家通常关注什么的参考。

#### 实现

```python
PAPER_TYPE_COGNITIVE_HINTS = {
    "empirical": {
        "gate_idle_rounds": 3,
        "min_findings_for_exit": 3,
        "cognitive_hints": [
            "实证论文的核心弱点通常在: identification strategy、robustness to alternative specs、data quality",
            "检查 first-stage (如有 IV)、pre-trends (如有 DID)、balance tests (如有 RCT)",
            "结果的经济显著性 (不仅仅是统计显著性)",
        ],
    },
    "theoretical": {
        "gate_idle_rounds": 5,
        "min_findings_for_exit": 2,
        "cognitive_hints": [
            "理论论文关注: assumption 是否过强、proof 是否有 gap、结论是否 trivial",
            "检查 assumption 的经济解释——数学上成立但经济上不合理 = 问题",
            "与已有 characterization/impossibility 结果的关系",
        ],
    },
    "review": {
        "gate_idle_rounds": 4,
        "min_findings_for_exit": 4,
        "cognitive_hints": [
            "综述论文关注: 文献覆盖是否完整、分类框架是否 MECE、是否有 original synthesis",
            "检查是否遗漏了领域内的重要贡献",
            "框架是否只是列举还是有 original 洞察",
        ],
    },
}
```

**注入方式**: 在 INITIAL_SCAN 阶段，如果 paper_type 已知，将 cognitive_hints 作为参考信息注入。措辞：

```
[审稿参考 — 基于论文类型的常见关注点]
此论文判断为实证类型。同类论文的审稿人通常关注:
  - identification strategy 的可信度
  - robustness to alternative specifications  
  - 数据质量和样本选择
[这是参考信息，非指令。你的审稿应基于论文实际内容。]
```

#### 预计工作量: 1 天

---

### K1. 审稿认知图谱输出

#### 目标

审稿结束时，除了 findings 列表，还生成结构化的"认知图谱"——记录 Agent 如何理解这篇论文。

#### 价值

1. **对用户**: 比 findings 列表更能展示审稿深度（"Agent 不只是列问题，它理解了论证链"）
2. **对 Agent 自身**: 作为 Episodic Memory 的输入，未来审类似论文时可回溯
3. **对 C1 (跨任务进化)**: 积累足够图谱后可以发现"什么类型的论文，Agent 审得好/差"

#### 实现

```python
@dataclass
class ReviewCognitionGraph:
    """审稿认知图谱——审稿过程的结构化产出。"""
    
    # 论文核心论点
    core_claims: list[dict]  # [{claim, evidence_sections, assessed_strength}]
    
    # 证据链
    evidence_chains: list[dict]  # [{claim_id, evidence_ids, chain_integrity}]
    
    # 假说追查结果
    hypothesis_outcomes: list[dict]  # [{statement, outcome, key_evidence}]
    
    # Findings 间关系
    finding_clusters: list[dict]  # [{root_cause, finding_ids, cluster_severity}]
    
    # 审稿自评
    review_coverage: float    # sections_actually_read / total_sections
    review_depth: str         # "surface" | "standard" | "deep"
    confidence_in_verdict: float  # Agent 对自己总体判断的信心
    
    # 未来使用
    lessons_for_next_time: list[str]  # "下次审此类论文应该..."
```

**构建时机**: `mark_complete` 成功时。数据来源全部是已有状态——不需要额外 LLM 调用。

#### 预计工作量: 1-2 天

---

### E1. 失败模式规则提炼

#### 重新定位

从"基础设施整理"降级到 P2。原因：
- 这帮助的是"防止 Agent 犯已知错误"——有价值但不如新能力紧迫
- 最核心的 2 条规则可以直接加入 CLAUDE.md，不需要独立文件

#### 最小版本

直接在 CLAUDE.md 中新增：

```markdown
## 认知设计红线（从 58 Phase 历史中提炼）
- 永远不与 LLM 行为经济学对抗——如需额外认知行为，在已有最短路径上自动增强，不开辟新路径
- 约束/nudge 信号只描述期望行为，永不提供"如何绕过"的提示或暗示
- 新增工具/功能前先问：Agent 不用它能否完成任务？如果能，就不加
```

**完整的 FAILURE_PATTERNS.md** 可以后续补充，但不阻塞任何认知能力开发。

#### 预计工作量: 2 小时

---

## X：工程维护（低优先级）

### X1. 目录结构迁移

**重新定位**: 仅当 P0/P1 新增文件使平面结构难以管理时再做。当前 24 个文件仍可管理。

如果执行者觉得现在做合适，参照 BLUEPRINT 第八节的映射表执行。但这不应阻塞任何认知能力开发。

### X2. harness.py 决策注释

**重新定位**: 维护者导向。可以在做 P0/P1 时顺手加相关函数的注释，但不作为独立任务排期。

---

## 执行建议

### 推荐顺序

```
Week 1-2:  B1 (预索引) → M1 (Session Memory LLM版)
Week 2-3:  M2 (Compaction 增强) → R1 (Procedural 回注)
Week 3:    H1 (假说可见性) + Q1 (Finding 自评)
Week 4:    S1 (Paper-Type 策略) + K1 (认知图谱)
随时可做:   E1 (2条规则直接加)
```

### 为什么这个顺序

1. **B1 最先做**: 零额外 LLM 成本，纯正则，但直接让 Agent 有"论文心智模型"
2. **M1 紧随其后**: 它是 M2 的前置——Session Memory 产出的笔记就是 Compaction 恢复的原料
3. **R1 在 M1 之后**: 因为 R1 需要理解 memory.py 的数据结构，而做 M1 时自然会深入理解
4. **H1 + Q1 小而高价值**: 半天到一天的改动，但直接影响 Agent 的审稿意识和输出质量

### 验证标准

每完成一项，用以下问题验证：
1. "Agent 审同一篇论文，有这个功能 vs 没有，输出质量有可观察的差异吗？"
2. "这个功能在 5 次不同论文的审稿中都能发挥作用，还是只在特定情况下有用？"
3. "COGNITIVE_ANCHOR §9 的自检问题都通过了吗？"

### 不变的约束

1. **Agent = cognition, not orchestration** — 所有新增都是认知增强，不是流程管道
2. **Constrain, don't control** — 所有注入信息都是"参考"措辞，Agent 有最终决策权
3. **LLM 是无状态 CPU** — 不依赖 LLM 记住跨轮信息
4. **增量验证** — 每个改动后跑 469+ tests
5. **在已有路径上增强** — 优先利用现有机制（如 Assembler section 注入），不发明新管道

---

## 与旧版方案的对比

| 维度 | 旧版 (v1) | 本版 (v2) |
|------|-----------|-----------|
| 优先级判断标准 | "工程合理性" | "Agent 审稿更好了吗" |
| Session Memory | 规则提取（安全但无价值） | LLM 更新（5% 成本换认知判断保留） |
| 目录迁移 | 阶段 0（最先做） | X 类（有空再做） |
| Decision Audit | 阶段 0（独立任务） | 顺手做（不排期） |
| Procedural Memory 回注 | 远期 C1 | P1（已有代码，只差注入） |
| Finding 质量自评 | "等 20 次数据" | P1（现在就做） |
| HD-WM 假说可见性 | 未提及 | P1（半天工作量） |
| 核心追求 | 代码整洁、模块清晰 | Agent 能力更强、审稿更深 |

---

> **给执行者**: 这份计划的每一项都应该让 ScholarAgent 在审一篇真实论文时表现得更像一个有经验的人类审稿人。如果你在实现某项时感觉"这只是在让代码更好看"——停下来，回到 COGNITIVE_ANCHOR §9 自检。
