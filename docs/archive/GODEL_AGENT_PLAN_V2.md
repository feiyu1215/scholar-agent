# ScholarAgent Gödel Layer — 有界递归自改进 (V2 Final)

> **文件定位**：本文件是 ScholarAgent C3（Gödel Agent）能力的最终实现方案。
> 基于 V1 计划的工程严谨性 + V2 认知哲学的方向修正，经过多轮独立审视后确定。
>
> **核心主张**：自改进不是"外部控制系统优化 Agent"，而是"Agent 作为认知实体，具备审视和修正自身学习的能力"。

---

## 一、设计哲学

### 1.1 从 V1 到 V2 的范式转换

| 维度 | V1（控制系统范式） | V2 Final（认知能力范式） |
|------|-------------------|------------------------|
| 度量 | 标量 quality_score（公式加权） | 多维体验记录（保留原始数据，不压缩为标量） |
| 效果验证 | 统计检验 | 认知对照（系统保证有对照数据，LLM 做判断） |
| 元反思 | LLM 优化 prompt 模板 | LLM 质疑自己的学习习惯（不修改代码/模板） |
| 收敛判断 | 硬编码状态机 | LLM 自判"我在这个领域够熟了" |
| 回滚机制 | 参数快照恢复 | 习惯自然衰退 + LLM 主动放弃（认知遗忘） |
| 实现形态 | 独立 MetaReflector 模块 + 状态机 | SessionReflector 的高阶升级（独立 LLM 调用） |

### 1.2 核心原则

**P1: Agent-as-Cognizer, not Agent-as-Subject-of-Optimization**

Harness 呈现事实 → LLM 自己思考 → LLM 自己决定。Harness 永远不替 LLM 做认知判断。

**P2: 独立 LLM 调用，非独立 Agent**

元认知判断通过独立的 LLM 调用实现（类似现有 SessionReflector），不引入 Multi-Agent 通信协议。理由：元认知每 N 个 session 才触发一次，不需要持久实例、不需要黑板通信、不需要消息传递——一个精准的独立调用足矣。

**P3: 约束-而非-控制（§4.3 延续）**

宪法层只限制破坏半径（每次最多 abandon 1 个习惯、冷却期、evidence 门槛），不限制 LLM 的判断方向。

**P4: 与现有架构零冲突**

所有新能力通过组合已有组件实现：MemoryStore 存数据、session_finalizer 管时机、独立 LLM call 做判断。loop.py / harness.py / identity.py 核心逻辑零修改。

### 1.3 与 COGNITIVE_ANCHOR 的对照验证

| §4.3 自检 | 本方案的回答 |
|-----------|------------|
| 人类专家会这样想吗？ | 会。资深审稿人会定期反思"我最近的审稿方法是否在退化？之前学的技巧真的有效吗？" |
| 控制 vs 支撑？ | Harness 准备数据+触发时机+限制破坏半径；LLM 做实际判断。是支撑。 |
| 去掉这个模块 Agent 还能思考吗？ | 能。去掉 meta_reflect，Agent 仍正常审稿，只是不会淘汰无效习惯。增强层，非核心依赖。 |
| 能用意图链解释吗？ | "我审了 10 篇 → 想知道最近质量有没有变 → 发现某习惯学了之后效果变好 → 继续保持" |
| 在枚举场景吗？ | 不是。meta_reflect 是通用的"审视学习效果"过程 |

---

## 二、三层架构

```
┌─────────────────────────────────────────────────────────────┐
│ Layer 0: 宪法层 (Constitutional)                              │
│ 绝对不可被自修改的系统不变量                                      │
│ ─────────────────────────────────────────────────────────── │
│ • MAX_META_DEPTH = 2（禁止 Level 3 递归）                       │
│ • doom_loop_guard + token_budget（不可关闭）                    │
│ • evidence ≥ 3 才允许习惯升级                                   │
│ • 每次 meta_reflect 最多 abandon 1 个习惯                       │
│ • doubt 衰减步长 ≤ 0.15                                        │
│ • 冷却期: abandon 后 12 sessions 内不可对同一习惯重新判断            │
│ • 对照频率 ≥ 10%（保证系统有自我验证数据）                          │
│ • 习惯池上限 MAX_LEARNED_HABITS = 10                            │
│ • JSON 格式约束 + 工具 schema 不可变                              │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ Layer 1: 认知层 (Cognitive)                                    │
│ 可被进化的认知内容                                               │
│ ─────────────────────────────────────────────────────────── │
│ • 学习习惯库（LearnedHabit，由 HabitLearner 从经验中提取）        │
│ • 习惯 confidence 值（0~1，可被 meta_reflect 调整）               │
│ • 策略经验（ProceduralPattern，evidence_count 可增减）            │
│ • 领域成熟度（per paper_type 的连续值）                           │
│ 修改方式: 只通过 Layer 2 的元认知过程                              │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ Layer 2: 元认知层 (Meta-Cognitive)                              │
│ 审视 Layer 1 的认知能力                                         │
│ ─────────────────────────────────────────────────────────── │
│ • 能力 A: 习惯质疑 (Habit Interrogation)                        │
│   — 看对照数据 → 判断 reinforce/maintain/doubt/abandon            │
│ • 能力 B: 成熟度自知 (Maturity Awareness)                        │
│   — 看历史表现 → 判断"我在这个领域够熟了吗"                         │
│ • 能力 C: 元认知笔记 (Meta-Cognitive Note)                       │
│   — 产出注入下次 SessionReflector 的提示                          │
│ 实现形态: 独立 LLM 调用（非独立 Agent）                            │
│ 触发频率: 每 10 个有效 session                                    │
└─────────────────────────────────────────────────────────────┘
```

---

## 三、数据架构

### 3.1 SessionExperience（新增数据结构）

每次审稿 session 结束时，在 `record_review_stats()` 的基础上记录更丰富的多维体验数据。

```python
@dataclass
class SessionExperience:
    """一次审稿的完整体验记录——不压缩为标量，保留原始可解释数据。"""
    
    # === 身份标识 ===
    session_id: str              # 会话唯一ID
    timestamp: str               # ISO 格式时间戳
    paper_type: str              # 论文类型（DID/RCT/ML/...）
    
    # === 行为维度 ===
    total_turns: int             # 总轮次
    findings_count: int          # findings 数量
    high_priority_count: int     # high severity findings 数量
    evidence_ratio: float        # 有证据的 findings 占比
    actionable_ratio: float      # 可操作的 findings 占比
    idle_before_exit: int        # 退出前的空转轮数
    
    # === 效率维度 ===
    turns_to_first_finding: int  # 首次产出 finding 的轮次
    findings_per_turn: float     # findings 密度
    
    # === 认知维度 ===
    strategy_transitions: int    # 策略切换次数
    sections_read_ratio: float   # 已读 section 占比
    
    # === 进化维度（关键）===
    active_habit_ids: list[str]  # 本次注入的学习习惯 ID 列表
    is_contrast_session: bool    # 是否为对照组（未注入学习习惯）
    
    # === 质量信号（复用 FindingQualityGate）===
    quality_signals: dict        # {evidence_ratio, actionable_ratio, specificity...}
```

**存储位置**：`memory.json` 中新增 `session_experiences: list[dict]`，滑动窗口 50 条。

**与现有 SessionRecord 的关系**：SessionRecord 是面向"回忆历史"的摘要（findings_summary 是文本），SessionExperience 是面向"自我评估"的结构化数据。两者正交，同时存储。

### 3.2 存储结构变化

```json
// memory.json 新增字段
{
  "version": "2.1",
  "sessions": [...],           // 现有，不变
  "domain_patterns": [...],    // 现有，不变
  "procedural_patterns": [...], // 现有，不变
  "session_experiences": [     // 【新增】Phase 1
    {
      "session_id": "...",
      "timestamp": "...",
      "paper_type": "DID",
      "total_turns": 18,
      "findings_count": 6,
      "high_priority_count": 2,
      "evidence_ratio": 0.83,
      "actionable_ratio": 0.67,
      "idle_before_exit": 2,
      "turns_to_first_finding": 4,
      "findings_per_turn": 0.33,
      "strategy_transitions": 1,
      "sections_read_ratio": 0.72,
      "active_habit_ids": ["learned_001", "learned_003"],
      "is_contrast_session": false,
      "quality_signals": {"evidence_ratio": 0.83, "actionable_ratio": 0.67}
    }
  ],
  "meta_reflections": [        // 【新增】Phase 2
    {
      "timestamp": "...",
      "trigger_session_count": 10,
      "decisions": [
        {"habit_id": "learned_001", "action": "reinforce", "reason": "..."},
        {"habit_id": "learned_003", "action": "doubt", "reason": "..."}
      ],
      "maturity_updates": [
        {"paper_type": "DID", "new_level": 0.72, "reason": "..."}
      ],
      "meta_note": "下次反思时注意..."
    }
  ],
  "maturity_levels": {         // 【新增】Phase 2
    "DID": 0.72,
    "RCT": 0.45,
    "ML": 0.30
  }
}
```

---

## 四、实现计划

### Phase 0: 地基加固 ✅ 已完成

- [x] `memory.py` 新增 `gc_procedures()`：三级淘汰（低效删除 → 长期未强化删除 → 硬容量裁剪）
- [x] 70 条独立单元测试
- [x] 全部 710 测试通过

---

### Phase 1: 体验记忆 + 认知对照（~1.5天）

**目标**：让系统积累结构化的审稿体验数据，并保证有对照数据可供未来评估。

#### 1.1 SessionExperience 存储

**文件**：`core/memory.py`

新增数据模型和存储方法：

```python
# memory.py 新增

@dataclass
class SessionExperience:
    """结构化审稿体验记录。"""
    session_id: str
    timestamp: str
    paper_type: str
    total_turns: int
    findings_count: int
    high_priority_count: int
    evidence_ratio: float
    actionable_ratio: float
    idle_before_exit: int
    turns_to_first_finding: int
    findings_per_turn: float
    strategy_transitions: int
    sections_read_ratio: float
    active_habit_ids: list[str]
    is_contrast_session: bool
    quality_signals: dict

class MemoryStore:
    # 新增方法
    MAX_EXPERIENCES = 50
    
    def persist_experience(self, exp: SessionExperience) -> None:
        """存储一条体验记录，维护滑动窗口。"""
        self.state.session_experiences.append(asdict(exp))
        if len(self.state.session_experiences) > self.MAX_EXPERIENCES:
            self.state.session_experiences = self.state.session_experiences[-self.MAX_EXPERIENCES:]
    
    def get_experiences_for_contrast(
        self, habit_id: str
    ) -> tuple[list[dict], list[dict]]:
        """获取某习惯的实验组 vs 对照组体验数据。"""
        with_habit = [e for e in self.state.session_experiences 
                      if habit_id in e.get("active_habit_ids", [])
                      and not e.get("is_contrast_session", False)]
        without_habit = [e for e in self.state.session_experiences
                         if e.get("is_contrast_session", False)]
        return with_habit, without_habit
    
    def get_recent_experiences(self, n: int = 10) -> list[dict]:
        """获取最近 N 条体验。"""
        return self.state.session_experiences[-n:]
```

#### 1.2 体验记录集成

**文件**：`core/session_finalizer.py`

在 `end_session()` 末尾新增体验记录步骤：

```python
def end_session(...):
    # ... 现有逻辑不变 ...
    
    # 8. 【新增】记录结构化体验
    _record_session_experience(
        state=state,
        memory=memory,
        paper_type=paper_type,
        active_habit_ids=active_habit_ids,
        is_contrast=is_contrast_session,
    )
    
    # 9. 持久化（原 step 7，移到最后）
    memory.save()
```

#### 1.3 认知对照机制

**文件**：`core/evolution.py`（EvolutionEngine）

在 `initialize()` 中加入对照逻辑：

```python
class EvolutionEngine:
    CONTRAST_PROBABILITY = 0.12  # 宪法层：≥10%
    
    def initialize(self) -> None:
        """会话开始时调用。学习习惯 + 决定是否为对照 session。"""
        # 现有逻辑：学习习惯
        self.learned_habits = self._habit_learner.learn()
        
        # 【新增】认知对照决策
        self.is_contrast_session = self._should_do_contrast()  # 新增属性
        
        if self.is_contrast_session:
            # 对照 session：不注入学习习惯（硬编码习惯不受影响）
            self._contrast_habits = self.learned_habits  # 保存用于记录
            self.learned_habits = []
    
    def _should_do_contrast(self) -> bool:
        """决定本次 session 是否为对照组。
        
        设计决策：
        - 纯随机 12%，不依赖 LLM 判断
        - 理由：如果让 LLM 决定是否对照，它倾向保守（永不对照），
          系统就永远无法获得对照数据。这是宪法层的保证。
        """
        import random
        return random.random() < self.CONTRAST_PROBABILITY
    
    def get_active_habit_ids(self) -> list[str]:
        """返回本次 session 实际注入的习惯 ID 列表。"""
        return [h.id for h in self.learned_habits]
```

#### 1.4 验证标准

- [ ] 10 个 session 后，memory.json 中积累了 10 条 session_experiences
- [ ] ~12% 的 session 标记为 is_contrast_session=True
- [ ] 对照 session 中 active_habit_ids 为空（但硬编码习惯正常注入）
- [ ] 现有 710 测试全部通过（零回归）
- [ ] 新增 ≥15 条单元测试覆盖 Experience 存储、对照逻辑、滑动窗口

---

### Phase 2: 元认知 LLM 调用 — 习惯质疑 + 成熟度自知（~2天）

**目标**：实现 Layer 2 的核心能力——让 LLM 在独立调用中审视学习效果。

#### 2.1 元认知调用模块

**新文件**：`core/meta_reflect.py`

```python
"""
meta_reflect.py — 元认知 LLM 调用

设计定位：
    与 SessionReflector 完全同构的设计模式——
    SessionReflector: "这次 session 我学到了什么"（每次 session 后）
    MetaReflector: "我学到的东西是否真的有效"（每 N 个 session 后）

    两者都是：Harness 预计算数据 → 独立 LLM 调用 → 结构化判断 → 存入 memory

与 COGNITIVE_ANCHOR 的关系：
    §4.3 约束-而非-控制的第六种模式——「自我质疑」：
    系统提供对照数据和触发时机，LLM 自主判断自己的学习是否有效。
    不强制任何方向（可以 reinforce 也可以 abandon），
    只限制破坏半径（宪法层约束）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from typing import Any

logger = logging.getLogger(__name__)


# ============================================================
# 数据结构
# ============================================================

@dataclass
class HabitDecision:
    """对单个习惯的元认知判断。"""
    habit_id: str
    habit_name: str
    action: str  # reinforce / maintain / doubt / abandon
    reason: str
    confidence_delta: float  # reinforce: +0.1, doubt: -0.15, abandon: -1.0


@dataclass
class MaturityUpdate:
    """对单个领域的成熟度判断。"""
    paper_type: str
    new_level: float  # 0.0 ~ 1.0
    reason: str


@dataclass
class MetaReflectionResult:
    """一次元认知调用的完整结果。"""
    habit_decisions: list[HabitDecision]
    maturity_updates: list[MaturityUpdate]
    meta_note: str  # 注入下次 SessionReflector 的提示
    raw_response: str  # LLM 原始输出（调试用）


# ============================================================
# Prompt 模板
# ============================================================

_META_REFLECT_SYSTEM_PROMPT = """你是一位元认知审视者——你的工作不是审稿，而是审视"审稿能力的学习过程"。

你将看到：
1. 最近的审稿体验数据（包含实验组和对照组）
2. 当前学习到的习惯列表（含 confidence 值）
3. 各领域的累积表现

你的任务是做出认知判断：
- 哪些习惯确实在帮助审稿？（reinforce）
- 哪些习惯效果不明确？（maintain，等待更多数据）
- 哪些习惯可能是无效的甚至有害的？（doubt）
- 哪些习惯应该被放弃？（abandon，仅当有充分证据时）

判断标准：
- 比较"注入该习惯的 sessions"和"对照 sessions"的质量信号
- 质量信号包括：evidence_ratio、actionable_ratio、findings_per_turn、idle_before_exit
- 如果数据不足以得出结论，选择 maintain（保守原则）
- 每次最多 abandon 1 个习惯（安全约束）

同时评估领域成熟度：
- 如果某类论文的审稿表现已经稳定且优秀（连续 5+ sessions 质量良好），成熟度应该提高
- 成熟度高的领域可以降低元认知频率（节省资源）

最后，写一条元认知笔记（meta_note）：
- 这条笔记会注入下次 SessionReflector 的 prompt
- 用于引导反思方向（如"最近方法论审视做得不够深入"）
- 不超过 2 句话

输出格式（严格 JSON）：
{
  "habit_decisions": [
    {"habit_id": "...", "habit_name": "...", "action": "reinforce|maintain|doubt|abandon", "reason": "一句话理由"}
  ],
  "maturity_updates": [
    {"paper_type": "...", "new_level": 0.0-1.0, "reason": "一句话理由"}
  ],
  "meta_note": "..."
}"""


_META_REFLECT_USER_TEMPLATE = """## 当前学习习惯

{habits_summary}

## 最近审稿体验数据

### 实验组（注入了学习习惯的 sessions）
{experiment_sessions}

### 对照组（未注入学习习惯的 sessions）
{contrast_sessions}

## 各领域累积表现
{domain_summary}

## 上次元认知笔记
{previous_meta_note}

请基于以上数据做出你的判断。"""


# ============================================================
# 核心类
# ============================================================

class MetaReflector:
    """
    元认知 LLM 调用——审视学习效果。
    
    设计模式与 SessionReflector 完全同构：
    1. Harness 调用 should_trigger() 判断时机
    2. Harness 调用 precompute_context() 准备数据
    3. Harness 调用 reflect() 执行 LLM 调用
    4. Harness 调用 apply_decisions() 写入结果
    """
    
    TRIGGER_INTERVAL = 10  # 每 10 个有效 session 触发一次
    MIN_CONTRAST_SESSIONS = 3  # 至少 3 个对照 session 才有意义
    
    # 宪法层约束
    MAX_ABANDON_PER_CALL = 1
    DOUBT_DECAY = 0.15
    REINFORCE_BOOST = 0.10
    COOLDOWN_SESSIONS = 12
    
    def __init__(self, llm_call_fn=None):
        """
        Args:
            llm_call_fn: 异步 LLM 调用函数。
                签名: async (system: str, user: str, max_tokens: int) -> str
        """
        self.llm_call_fn = llm_call_fn
    
    def should_trigger(self, memory_store) -> bool:
        """判断是否应该触发元认知反思。
        
        触发条件：
        1. 累积 session_experiences ≥ TRIGGER_INTERVAL
        2. 对照 sessions ≥ MIN_CONTRAST_SESSIONS
        3. 距上次 meta_reflection ≥ TRIGGER_INTERVAL 个 session
        """
        experiences = memory_store.state.session_experiences
        if len(experiences) < self.TRIGGER_INTERVAL:
            return False
        
        contrast_count = sum(
            1 for e in experiences if e.get("is_contrast_session", False)
        )
        if contrast_count < self.MIN_CONTRAST_SESSIONS:
            return False
        
        # 检查距上次 meta_reflection 的 session 数
        meta_reflections = getattr(memory_store.state, "meta_reflections", [])
        if meta_reflections:
            last_trigger_count = meta_reflections[-1].get("trigger_session_count", 0)
            current_count = len(experiences)
            if current_count - last_trigger_count < self.TRIGGER_INTERVAL:
                return False
        
        return True
    
    def precompute_context(
        self,
        memory_store,
        learned_habits: list,
    ) -> str:
        """预计算统计数据，格式化为 LLM 可消费的文本。
        
        这是 Harness 的职责：将原始数据转化为 LLM 可做判断的摘要。
        LLM 不应该自己做数学运算。
        """
        experiences = memory_store.state.session_experiences
        
        # 习惯摘要
        habits_lines = []
        for h in learned_habits:
            habits_lines.append(
                f"- [{h.id}] {h.name} "
                f"(confidence={h.confidence:.2f}, generation={h.generation})"
            )
        habits_summary = "\n".join(habits_lines) if habits_lines else "（暂无学习习惯）"
        
        # 实验组/对照组分组
        experiment = [e for e in experiences if not e.get("is_contrast_session", False)]
        contrast = [e for e in experiences if e.get("is_contrast_session", False)]
        
        # 格式化 sessions
        experiment_text = self._format_sessions(experiment[-15:])  # 最近 15 条
        contrast_text = self._format_sessions(contrast[-10:])      # 最近 10 条
        
        # 领域摘要
        domain_stats = self._compute_domain_summary(experiences)
        
        # 上次 meta_note
        meta_reflections = getattr(memory_store.state, "meta_reflections", [])
        previous_note = ""
        if meta_reflections:
            previous_note = meta_reflections[-1].get("meta_note", "")
        
        return _META_REFLECT_USER_TEMPLATE.format(
            habits_summary=habits_summary,
            experiment_sessions=experiment_text,
            contrast_sessions=contrast_text,
            domain_summary=domain_stats,
            previous_meta_note=previous_note or "（首次元认知反思，无历史笔记）",
        )
    
    async def reflect(self, context_text: str) -> MetaReflectionResult | None:
        """执行独立 LLM 调用，获取元认知判断。
        
        Graceful degradation: LLM 调用失败时返回 None，不影响正常审稿。
        """
        if self.llm_call_fn is None:
            return None
        
        try:
            raw = await self.llm_call_fn(
                _META_REFLECT_SYSTEM_PROMPT,
                context_text,
                max_tokens=1200,
            )
            return self._parse_response(raw)
        except Exception as e:
            logger.warning(f"Meta-reflect LLM call failed: {e}")
            return None
    
    def apply_decisions(
        self,
        result: MetaReflectionResult,
        memory_store,
        learned_habits: list,
    ) -> dict:
        """将元认知判断应用到 memory 中。
        
        宪法层约束在此执行：
        - 最多 abandon 1 个
        - doubt 衰减 ≤ 0.15
        - 冷却期检查
        
        Returns:
            统计信息: {"reinforced": int, "doubted": int, "abandoned": int}
        """
        stats = {"reinforced": 0, "doubted": 0, "abandoned": 0, "maintained": 0}
        abandon_count = 0
        
        # 获取冷却中的习惯
        cooled_habits = self._get_cooled_habit_ids(memory_store)
        
        for decision in result.habit_decisions:
            # 冷却期检查
            if decision.habit_id in cooled_habits:
                stats["maintained"] += 1
                continue
            
            # 找到对应习惯（LearnedHabit.字段名是 id，HabitDecision 的 habit_id 是 JSON 协议中的 key）
            habit = next(
                (h for h in learned_habits if h.id == decision.habit_id),
                None,
            )
            if habit is None:
                continue
            
            if decision.action == "reinforce":
                habit.confidence = min(1.0, habit.confidence + self.REINFORCE_BOOST)
                stats["reinforced"] += 1
            elif decision.action == "doubt":
                habit.confidence = max(0.0, habit.confidence - self.DOUBT_DECAY)
                stats["doubted"] += 1
            elif decision.action == "abandon" and abandon_count < self.MAX_ABANDON_PER_CALL:
                habit.confidence = 0.0  # 标记为无效
                abandon_count += 1
                stats["abandoned"] += 1
            else:
                stats["maintained"] += 1
        
        # 存储成熟度更新
        maturity_levels = getattr(memory_store.state, "maturity_levels", {})
        for update in result.maturity_updates:
            maturity_levels[update.paper_type] = update.new_level
        memory_store.state.maturity_levels = maturity_levels
        
        # 存储本次 meta_reflection 记录
        meta_record = {
            "timestamp": _now_iso(),
            "trigger_session_count": len(memory_store.state.session_experiences),
            "decisions": [asdict(d) for d in result.habit_decisions],
            "maturity_updates": [asdict(m) for m in result.maturity_updates],
            "meta_note": result.meta_note,
        }
        if not hasattr(memory_store.state, "meta_reflections"):
            memory_store.state.meta_reflections = []
        memory_store.state.meta_reflections.append(meta_record)
        # 保留最近 20 条 meta_reflection 记录
        if len(memory_store.state.meta_reflections) > 20:
            memory_store.state.meta_reflections = memory_store.state.meta_reflections[-20:]
        
        return stats
    
    # ============================================================
    # 内部方法
    # ============================================================
    
    def _format_sessions(self, sessions: list[dict]) -> str:
        """将 session 列表格式化为 LLM 可读文本。"""
        if not sessions:
            return "（暂无数据）"
        lines = []
        for s in sessions:
            lines.append(
                f"  [{s.get('paper_type', '?')}] "
                f"findings={s.get('findings_count', 0)}, "
                f"evidence_ratio={s.get('evidence_ratio', 0):.2f}, "
                f"actionable_ratio={s.get('actionable_ratio', 0):.2f}, "
                f"turns={s.get('total_turns', 0)}, "
                f"idle_exit={s.get('idle_before_exit', 0)}, "
                f"habits={s.get('active_habit_ids', [])}"
            )
        return "\n".join(lines)
    
    def _compute_domain_summary(self, experiences: list[dict]) -> str:
        """按领域汇总表现。"""
        from collections import defaultdict
        domain_stats = defaultdict(list)
        for e in experiences:
            pt = e.get("paper_type", "unknown")
            domain_stats[pt].append(e)
        
        lines = []
        for domain, sessions in domain_stats.items():
            avg_findings = sum(s.get("findings_count", 0) for s in sessions) / len(sessions)
            avg_evidence = sum(s.get("evidence_ratio", 0) for s in sessions) / len(sessions)
            lines.append(
                f"  {domain}: {len(sessions)} sessions, "
                f"avg_findings={avg_findings:.1f}, "
                f"avg_evidence_ratio={avg_evidence:.2f}"
            )
        return "\n".join(lines) if lines else "（暂无领域数据）"
    
    def _get_cooled_habit_ids(self, memory_store) -> set:
        """获取处于冷却期的习惯 ID。"""
        cooled = set()
        meta_reflections = getattr(memory_store.state, "meta_reflections", [])
        current_count = len(memory_store.state.session_experiences)
        
        for record in meta_reflections:
            trigger_count = record.get("trigger_session_count", 0)
            if current_count - trigger_count < self.COOLDOWN_SESSIONS:
                for d in record.get("decisions", []):
                    if d.get("action") == "abandon":
                        cooled.add(d.get("habit_id", ""))
        return cooled
    
    def _parse_response(self, raw: str) -> MetaReflectionResult:
        """解析 LLM 响应为结构化结果。容错处理。"""
        # 尝试提取 JSON（可能被 markdown code block 包裹）
        text = raw.strip()
        if "```" in text:
            import re
            match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
            if match:
                text = match.group(1).strip()
        
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logger.warning(f"Meta-reflect response not valid JSON: {raw[:200]}")
            return MetaReflectionResult(
                habit_decisions=[], maturity_updates=[],
                meta_note="", raw_response=raw
            )
        
        # 解析 habit_decisions
        decisions = []
        for d in data.get("habit_decisions", []):
            action = d.get("action", "maintain")
            if action not in ("reinforce", "maintain", "doubt", "abandon"):
                action = "maintain"
            decisions.append(HabitDecision(
                habit_id=d.get("habit_id", ""),
                habit_name=d.get("habit_name", ""),
                action=action,
                reason=d.get("reason", ""),
                confidence_delta={"reinforce": 0.1, "doubt": -0.15, "abandon": -1.0}.get(action, 0.0),
            ))
        
        # 解析 maturity_updates
        maturity = []
        for m in data.get("maturity_updates", []):
            level = m.get("new_level", 0.5)
            level = max(0.0, min(1.0, float(level)))
            maturity.append(MaturityUpdate(
                paper_type=m.get("paper_type", ""),
                new_level=level,
                reason=m.get("reason", ""),
            ))
        
        return MetaReflectionResult(
            habit_decisions=decisions,
            maturity_updates=maturity,
            meta_note=data.get("meta_note", ""),
            raw_response=raw,
        )


def _now_iso() -> str:
    """当前时间 ISO 格式。"""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
```

#### 2.2 触发集成

**文件**：`core/session_finalizer.py`

在 `end_session_with_reflection()` 末尾加入元认知触发：

```python
async def end_session_with_reflection(...) -> dict:
    # ... 现有逻辑 ...
    
    # 【新增】检查是否触发元认知反思
    from core.meta_reflect import MetaReflector
    meta_stats = {"meta_triggered": False}
    
    meta_reflector = MetaReflector(llm_call_fn=llm_call_fn)
    if meta_reflector.should_trigger(memory):
        # 获取当前学习习惯
        from core.evolution import HabitLearner
        learner = HabitLearner(memory)
        current_habits = learner.learn()
        
        # 预计算上下文
        context_text = meta_reflector.precompute_context(memory, current_habits)
        
        # 执行独立 LLM 调用
        result = await meta_reflector.reflect(context_text)
        
        if result:
            # 应用判断（含宪法层约束）
            apply_stats = meta_reflector.apply_decisions(result, memory, current_habits)
            meta_stats = {"meta_triggered": True, **apply_stats}
            memory.save()
    
    stats["meta_reflection"] = meta_stats
    return stats
```

#### 2.3 成熟度影响触发频率

当某领域 maturity_level > 0.8 时，该领域的 session 不计入 trigger_interval 计数。效果：成熟领域自动降频元认知，新领域保持全频。

```python
def should_trigger(self, memory_store) -> bool:
    # ... 基础条件检查 ...
    
    # 成熟领域 session 不计入触发条件
    maturity = getattr(memory_store.state, "maturity_levels", {})
    effective_count = 0
    for e in experiences:
        pt = e.get("paper_type", "")
        if maturity.get(pt, 0.0) <= 0.8:
            effective_count += 1
    
    if effective_count < self.TRIGGER_INTERVAL:
        return False
    # ...
```

#### 2.4 元认知笔记注入 SessionReflector

**文件**：`core/reflection.py`

在反思 prompt 中加入上次 meta_note（如有）：

```python
def reflect(self, state, memory_store=None):
    # ... 组装 user_prompt ...
    
    # 【新增】注入元认知笔记
    if memory_store:
        meta_reflections = getattr(memory_store.state, "meta_reflections", [])
        if meta_reflections:
            latest_note = meta_reflections[-1].get("meta_note", "")
            if latest_note:
                user_prompt += f"\n\n## 元认知提醒\n{latest_note}"
```

#### 2.5 验证标准

- [ ] 10 个 session 后（含 ≥3 对照），meta_reflect 自动触发
- [ ] LLM 输出被正确解析为 HabitDecision + MaturityUpdate
- [ ] 宪法层约束被严格执行：abandon ≤ 1、doubt 衰减 ≤ 0.15、冷却期生效
- [ ] 成熟领域（maturity > 0.8）自动降频
- [ ] meta_note 正确注入下次 SessionReflector
- [ ] 现有测试全部通过 + 新增 ≥20 条单元测试
- [ ] LLM 调用失败时 graceful degradation（返回 None，不影响正常流程）

---

### Phase 3: 习惯淘汰 + 进化闭环（~1天）

**目标**：完成从"学习→验证→淘汰"的完整闭环。

#### 3.1 习惯生命周期管理

**文件**：`core/evolution.py`（HabitLearner）

```python
class HabitLearner:
    def learn(self) -> list[LearnedHabit]:
        """从经验中学习习惯——新增：过滤掉 confidence=0 的废弃习惯。"""
        patterns = self._get_mature_patterns()
        habits = []
        for p in patterns:
            habit = self._pattern_to_habit(p)
            # 【新增】检查是否被 meta_reflect abandon 过
            if self._is_abandoned(habit.id):  # 注意: LearnedHabit 的字段名是 id
                continue
            habits.append(habit)
        return habits[:MAX_LEARNED_HABITS]
    
    def _is_abandoned(self, habit_id: str) -> bool:
        """检查习惯是否已被元认知判定为无效。"""
        meta_reflections = getattr(self._memory.state, "meta_reflections", [])
        for record in meta_reflections:
            for d in record.get("decisions", []):
                if d.get("habit_id") == habit_id and d.get("action") == "abandon":
                    return True
        return False
```

#### 3.2 Confidence 影响注入优先级

**文件**：`core/habits.py`（HabitSelector）

```python
def extend_with_learned(self, learned: list[CognitiveHabit]) -> None:
    """P2 扩展：注入学习习惯——新增 confidence 加权。"""
    from dataclasses import replace
    for habit in learned:
        # confidence 影响实际 priority
        # confidence=1.0 → priority 不变
        # confidence=0.5 → priority 减半
        # confidence<0.3 → 不注入（太不确定了）
        if habit.confidence < 0.3:
            continue
        adjusted = replace(habit,
            priority=int(habit.priority * habit.confidence)
        )
        self._learned_pool.append(adjusted)
```

#### 3.3 进化状态展示

**文件**：`core/assembler.py`（_compute_evolution_context）

更新进化状态展示，增加 confidence 信息和成熟度：

```python
def _compute_evolution_context(ctx: dict) -> str:
    """计算进化状态注入文本——增强版。"""
    engine = ctx.get("evolution_engine")
    if engine is None:
        return ""
    
    parts = []
    
    # 习惯状态
    if engine.learned_habits:
        parts.append("[你从经验中学到的审稿习惯]")
        for h in engine.learned_habits:
            confidence_bar = "●" * int(h.confidence * 5) + "○" * (5 - int(h.confidence * 5))
            parts.append(f"  {h.name} [{confidence_bar}]")
        if getattr(engine, "is_contrast_session", False):  # Phase 1 新增属性
            parts.append("  ⚡ 本次为认知对照 session（学习习惯未注入，用于效果验证）")
    
    # 成熟度（如有）
    maturity = getattr(engine, "_maturity_levels", {})
    if maturity:
        mature_domains = [f"{k}({v:.0%})" for k, v in maturity.items() if v > 0.5]
        if mature_domains:
            parts.append(f"  领域熟练度: {', '.join(mature_domains)}")
    
    return "\n".join(parts) if parts else ""
```

#### 3.4 验证标准

- [ ] confidence=0 的习惯不再被 HabitLearner 产出
- [ ] confidence<0.3 的习惯不被注入 system prompt
- [ ] 进化状态展示正确反映 confidence 和对照状态
- [ ] 完整闭环验证：学习(P2) → 积累(Phase1) → 质疑(Phase2) → 淘汰(Phase3)
- [ ] 新增 ≥10 条测试验证生命周期

---

## 五、环境变量 Kill Switch

所有新功能通过环境变量控制，支持渐进式启用：

```python
# 在 EvolutionEngine.__init__ 中检查
import os

GODEL_EXPERIENCE_ENABLED = os.getenv("SCHOLAR_GODEL_EXPERIENCE", "1") == "1"
GODEL_CONTRAST_ENABLED = os.getenv("SCHOLAR_GODEL_CONTRAST", "1") == "1"  
GODEL_META_REFLECT_ENABLED = os.getenv("SCHOLAR_GODEL_META_REFLECT", "1") == "1"
```

关闭任一开关后，对应功能静默跳过，系统退化为 Phase 0 后的基线状态。

---

## 六、与现有模块的关系

```
session_finalizer.py
├── end_session()
│   ├── build_session_record()     [不变]
│   ├── extract_domain_patterns()  [不变]
│   ├── extract_procedural_patterns() [不变]
│   ├── persist_cognitive_hints()  [不变]
│   ├── record_review_stats()      [不变]
│   └── record_session_experience() [新增 Phase 1]
│
└── end_session_with_reflection()
    ├── SessionReflector.reflect()  [不变，但 prompt 可注入 meta_note]
    └── MetaReflector              [新增 Phase 2]
        ├── should_trigger()       — Harness 判断时机
        ├── precompute_context()   — Harness 预计算数据
        ├── reflect()              — 独立 LLM 调用（认知判断）
        └── apply_decisions()      — Harness 执行结果（含宪法约束）

evolution.py
├── EvolutionEngine.initialize()
│   ├── HabitLearner.learn()       [增强: 过滤 abandoned]
│   └── _should_do_contrast()      [新增 Phase 1]
│
└── EvolutionEngine.get_habits_for_selector()
    └── → HabitSelector.extend_with_learned() [增强: confidence 加权]

memory.py
├── MemoryState                    [扩展: session_experiences, meta_reflections, maturity_levels]
├── persist_experience()           [新增 Phase 1]
└── get_experiences_for_contrast() [新增 Phase 2]
```

**零修改的文件**：loop.py、agent.py、identity.py、harness.py（核心逻辑）、phases.py、tools.py。

---

## 七、成本估算

| Phase | 新增代码 | 新增测试 | LLM 运行时成本 |
|-------|---------|---------|--------------|
| Phase 1 | ~150 行 | ~15 条 | 零（纯数据存储） |
| Phase 2 | ~350 行 | ~20 条 | 每 10 session 1 次 LLM call（~1200 tokens） |
| Phase 3 | ~80 行 | ~10 条 | 零（纯逻辑过滤） |
| **总计** | **~580 行** | **~45 条** | **平均每 session 增加 ~120 tokens 成本** |

---

## 八、风险与缓解

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| Meta-reflect LLM 幻觉导致错误 abandon | 中 | 高 | 宪法层：每次最多 abandon 1 个 + 冷却期 12 sessions |
| 对照 session 降低该次审稿质量 | 低 | 低 | 只不注入学习习惯，硬编码习惯（核心认知）仍在 |
| session_experiences 占用存储 | 低 | 低 | 滑动窗口 50 条，每条 ~500 bytes = ~25KB |
| 12% 对照概率导致用户感知差异 | 低 | 低 | 学习习惯本身是增强而非核心，不注入只是回到"基线状态" |

---

## 九、验收标准（整体）

### 功能验收

1. **体验积累**：连续运行 15 个 session 后，memory.json 中有 15 条 session_experiences，其中 ~2 条为对照组
2. **元认知触发**：第 10 个 session 结束后（满足对照数据条件），自动触发 meta_reflect
3. **习惯进化**：meta_reflect 的判断正确应用——reinforced 习惯 confidence 上升，doubted 下降，abandoned 被淘汰
4. **成熟度效果**：maturity > 0.8 的领域自动降低触发频率
5. **闭环验证**：从"HabitLearner 学到习惯"到"MetaReflector 淘汰无效习惯"的完整路径可复现

### 工程验收

1. **零回归**：现有 710+ 测试全部通过
2. **Kill Switch**：环境变量关闭后，系统完全退化为 Phase 0 基线
3. **Graceful Degradation**：LLM 调用失败时静默跳过，不影响正常审稿
4. **代码风格**：与现有代码一致（dataclass、type hints、docstrings）

---

## 十、时间线

```
Phase 0: ✅ 已完成 (gc_procedures + 70 tests)
    ↓
Phase 1: 体验记忆 + 认知对照 (~1.5天)
    ↓  验证: 15 tests 通过 + 手动跑 2 session 确认数据存储
    ↓
Phase 2: 元认知 LLM 调用 (~2天)
    ↓  验证: 20 tests 通过 + mock LLM 验证完整链路
    ↓
Phase 3: 习惯淘汰 + 进化闭环 (~1天)
    ↓  验证: 10 tests 通过 + E2E 手动验证完整生命周期
    ↓
总计: ~4.5 天
```

---

## 十一、未来展望（不在本计划范围内）

以下能力在本计划验证成功后可作为后续迭代方向：

1. **反思 Prompt 自适应**：meta_note 目前只是注入提示，未来可让 MetaReflector 建议 SessionReflector prompt 的微调方向（仍然是信息呈现，不自动修改）
2. **跨领域经验迁移**：当某领域成熟后，其经验是否可泛化到新领域？需要更多数据验证
3. **多维成熟度**：当前 maturity 是单一标量，未来可分维度（方法论审视成熟度、写作审视成熟度等）
4. **用户反馈闭环**：将用户对 findings 的采纳率作为额外的质量信号

---

*文件版本: V2 Final | 创建日期: 基于 V1 + V2 Draft 多轮审视后确定*
*前置依赖: Phase 0 ✅ | 下一步: Phase 1 实施*