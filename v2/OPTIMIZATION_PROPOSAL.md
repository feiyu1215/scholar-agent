# ScholarAgent V2 性能优化实施建议书（修订版 v2 — 含实施状态）

> 基于对 v2 代码库的深度审查 + 用户反馈修正。
>
> **实施状态**（最后更新）:
> - 优化 1（MCL 模型智能路由）: **已实现** ✅
> - 优化 2（Sub-Reader ToolPolicy 层）: **暂缓** ⏸️（用户决定暂不需要）
> - 优化 3（认知习惯渐进加载）: **已实现** ✅
>
> 核心改进：
> - 模型路由从「一刀切降级」升级为「MCL 驱动的 difficulty-aware 动态路由」
> - 认知习惯从「阶段内全量注入」升级为「渐进加载（精简→完整）」

---

## 优化 1：Sub-Reader 模型智能路由（MCL-Driven Difficulty Assessment）

### 问题诊断

当前 `_run_parallel_perspectives()` 和 `_run_sub_perspective()`（loop.py:767-1009）直接复用主 Agent 的 `client` 对象。但子视角任务的难度差异巨大：

- `symbol_auditor` 检查符号一致性 → 简单模式匹配，LOW 模型足矣
- `methodology_critic` 审视一篇复杂结构经济学论文的识别策略 → 需要 HIGH 模型的深度推理
- `literature_verifier` 验证引用是否存在 → 取决于领域复杂度，可能 MEDIUM 或 HIGH

**关键洞察**：不能静态映射 lens → tier。同一个 lens (如 `methodology_critic`)，面对不同论文（简单 ML 实验 vs 复杂结构估计）需要不同模型层级。

### 方案设计：MCL 作为 Spawn-Time Difficulty Router

现有系统已有 MCL（meta_cognition_layer.py），它用 gpt-4.1-mini 做轻量判断。我们扩展 MCL 的职责，在 spawn 时做一次快速 difficulty assessment，决定每个子视角应该用什么模型层级。

**架构位置**：在 `_run_parallel_perspectives()` 启动子视角之前，调用 MCL 做一次 batch difficulty assessment。

```
主 Agent 决定 spawn N 个子视角
    ↓
MCL.assess_reader_difficulty(readers, paper_context)
    ↓ (单次 LLM 调用，~500 tokens，gpt-4.1-mini)
返回 {lens: "high"/"medium"/"low"} 映射
    ↓
每个子视角根据 MCL 返回的 tier 选择对应模型
```

### 改动文件：`v2/core/meta_cognition_layer.py`

新增 difficulty assessment 接口：

```python
# ============================================================
# 对外接口 3: Reader Difficulty Assessment
# ============================================================

MCL_DIFFICULTY_SYSTEM = """\
你是一个任务难度评估器。给定一组即将执行的子审稿视角（sub-reader），
根据论文的学科、方法论复杂度和具体问题，判断每个子视角需要的认知能力层级。

## 输出格式（严格 JSON）
```json
{
  "assessments": [
    {
      "lens": "lens名称",
      "tier": "high" | "medium" | "low",
      "reason": "一句话原因（<40字）"
    }
  ]
}
```

## 判断标准
- **high**: 需要深度推理——复杂因果识别、结构模型假设审视、跨领域文献综合
- **medium**: 结构化分析——标准方法论检查、常规文献验证、逻辑一致性
- **low**: 模式匹配——数值一致性、符号一致性、格式检查
"""

MCL_DIFFICULTY_USER = """\
## 论文信息
- 标题: {paper_title}
- 学科类型: {paper_type}
- Section 数量: {section_count}
- 当前已有 findings: {findings_count} 条

## 待评估的子视角
{readers_desc}

请评估每个子视角所需的认知能力层级。
"""

async def assess_reader_difficulty(
    self,
    readers: list[dict],
    state: Any,
    paper_type: str | None = None,
) -> dict[str, str]:
    """
    评估每个子视角的难度，返回 lens → tier 映射。

    设计原则：
    - 这是一次 batch 调用（不是每个 reader 单独调）
    - 成本：~500 tokens × gpt-4.1-mini ≈ $0.0002，可忽略
    - 失败时 fallback 到静态规则（不阻塞流程）

    Args:
        readers: 子视角列表 [{"lens": ..., "focus": ..., "question": ...}]
        state: WorkspaceState
        paper_type: 论文类型（从 CognitiveHints 推断）

    Returns:
        {lens_name: "high"/"medium"/"low"}
    """
    # 构建描述
    readers_lines = []
    for i, r in enumerate(readers, 1):
        readers_lines.append(
            f"{i}. lens={r['lens']}, focus={r['focus']}, question={r['question']}"
        )
    readers_desc = "\n".join(readers_lines)

    paper_title = getattr(state, "paper_title", "") or "Unknown"
    section_count = len(state.paper_sections or {})
    findings_count = len(state.findings)

    user_prompt = MCL_DIFFICULTY_USER.format(
        paper_title=paper_title,
        paper_type=paper_type or "unknown",
        section_count=section_count,
        findings_count=findings_count,
        readers_desc=readers_desc,
    )

    try:
        response = await self._client.chat(
            system=MCL_DIFFICULTY_SYSTEM,
            user=user_prompt,
            temperature=0.1,
            max_tokens=500,
            model=self._model,  # gpt-4.1-mini
        )
        self._total_calls += 1
        parsed = _extract_json(response)
        if parsed and "assessments" in parsed:
            return {
                a["lens"]: a.get("tier", "medium")
                for a in parsed["assessments"]
                if isinstance(a, dict) and "lens" in a
            }
    except Exception as e:
        logger.warning("[MCL] difficulty assessment failed: %s", e)

    # Fallback: 静态规则（保证不阻塞）
    return self._static_difficulty_fallback(readers)

def _static_difficulty_fallback(self, readers: list[dict]) -> dict[str, str]:
    """MCL 不可用时的静态兜底规则。"""
    STATIC_HINTS = {
        "data_consistency_auditor": "low",
        "symbol_auditor": "low",
        "format_auditor": "low",
        "methodology_critic": "medium",  # 保守：不降到 low
        "literature_verifier": "medium",
        "contribution_evaluator": "medium",
        "logic_flow_auditor": "medium",
    }
    return {r["lens"]: STATIC_HINTS.get(r["lens"], "medium") for r in readers}
```

### 改动文件：`v2/core/loop.py` — `_run_parallel_perspectives()`

在并行启动前调用 MCL difficulty assessment：

```python
async def _run_parallel_perspectives(
    harness: Harness,
    client: LLMClient,
    readers: list[dict],
    budget_per_reader: int = 60000,
    verbose: bool = True,
) -> str:
    # ... 前置文档注释 ...

    # ====== 新增：MCL 驱动的模型路由 ======
    from core.godel_config import GODEL_SUB_READER_ROUTING_ENABLED
    from llm.router import get_model_for_task, MODEL_TIERS

    tier_map: dict[str, str] = {}
    if GODEL_SUB_READER_ROUTING_ENABLED and harness.mcl is not None:
        tier_map = await harness.mcl.assess_reader_difficulty(
            readers=readers,
            state=harness.state,
            paper_type=getattr(harness, '_inferred_paper_type', None),
        )
        if verbose:
            tiers_summary = ", ".join(f"{k}={v}" for k, v in tier_map.items())
            print(f"    [MCL Routing] {tiers_summary}", file=sys.stderr)

    async def _run_single(reader: dict) -> dict:
        lens = reader["lens"]
        # ... 现有的 sub_harness 创建逻辑 ...

        # 根据 MCL 评估选择模型
        tier = tier_map.get(lens, "high")  # 未评估时保守用 high
        sub_model = MODEL_TIERS.get(tier, client.model)
        sub_client = client.with_model_override(sub_model) if sub_model != client.model else client

        sub_result = await cognitive_loop(
            messages=sub_messages,
            harness=sub_harness,
            tools=...,  # 见优化 2
            client=sub_client,
            verbose=False,
        )
        # ... 结果提取逻辑不变 ...
```

### 改动文件：`v2/llm/client.py`

```python
def with_model_override(self, model: str) -> "LLMClient":
    """创建使用不同模型的 client（共享连接池/session）。
    
    设计：浅拷贝，只改 model 字段。如果 model 相同则返回 self（零成本）。
    """
    if model == self.model:
        return self
    import copy
    clone = copy.copy(self)
    clone.model = model
    return clone
```

### 改动文件：`v2/core/godel_config.py`

```python
GODEL_SUB_READER_ROUTING_ENABLED: bool = _env_flag("SCHOLAR_GODEL_SUB_READER_ROUTING")
"""子视角模型智能路由（MCL difficulty assessment）。
OFF 时所有子视角继续使用与主 Agent 相同的模型。"""
```

### 为什么不是静态映射

静态映射 `methodology_critic → medium` 会犯两类错误：
1. **false downgrade**: 一篇结构经济学论文的 methodology 审视可能需要理解 GE 模型的均衡存在性条件——这是 HIGH 任务
2. **false upgrade**: 一篇简单的 DID 论文的 methodology 审视可能只需检查 parallel trends 图——MEDIUM 足矣

MCL 能看到论文标题、学科类型、section 复杂度、已有 findings，做出 context-aware 的判断。成本极低（一次 gpt-4.1-mini 调用 ~$0.0002），但路由精度远超静态规则。

### 与 AdaptiveConfig 的协同

MCL 评估的 tier 还可以影响 budget 分配：

```python
# 在 _run_single 中
TIER_BUDGET_MULTIPLIER = {"high": 1.2, "medium": 1.0, "low": 0.6}
actual_budget = int(budget_per_reader * TIER_BUDGET_MULTIPLIER.get(tier, 1.0))
sub_harness.state.token_budget = actual_budget
```

HIGH 子视角获得更多 budget（它需要更多推理轮次），LOW 子视角获得更少（快速完成）。

---

## 优化 2：Sub-Reader ToolPolicy 层（可扩展的动态权限控制）⏸️ 暂缓

> **状态：暂缓** — 用户决定当前阶段不需要权限隔离。以下为设计方案存档，待未来需要时实施。

### 问题诊断

当前方案的两个根本缺陷：

1. **静态白名单无法响应运行时变化**：ToolGroupManager 在 Phase 切换时动态激活/停用工具组，SkillX 可以动态注册新工具，MCP 桥接可以在运行时加入外部工具——静态白名单无法感知这些。

2. **如果未来想给子视角添加 Zone A 策略中的工具**（如 PCG 导航、知识 Skill 注入），静态白名单需要手动修改代码。应该有一个 Policy 接口，让系统的其他层可以「声明」自己的工具是否对子视角可用。

### 方案设计：ToolPolicy 接口 + 分层组合

核心思想：「工具是否对子视角可用」这个决策不应该由一个静态列表决定，而应该由多个 **Policy** 协同决策：

```
BaseReaderPolicy（基础读写工具 → 始终授予）
    + PhasePolicy（当前阶段的工具组 → 按 ToolGroupManager 逻辑）
    + SkillPolicy（已加载 Skill 的工具 → 按需授权）
    + SecurityPolicy（安全边界 → 绝对禁止 spawn/edit/talk）
    = 最终可用工具集
```

### 改动文件：新增 `v2/core/sub_reader_policy.py`

```python
"""
core/sub_reader_policy.py — Sub-Reader 工具权限策略层

设计原则:
    - 组合式策略（Composite Pattern）：多个 Policy 协同决策
    - 可扩展：新增 Policy 只需实现接口并注册
    - 安全默认：SecurityPolicy 是最终仲裁者，优先级最高
    - 运行时感知：Policy 可以查询 harness state / ToolGroupManager / SkillRegistry

与现有系统的关系:
    - ToolGroupManager (tool_group.py): PhasePolicy 复用其 activate_for_phase 逻辑
    - SkillRegistry / SkillX: SkillPolicy 查询已加载的动态 Skill 工具
    - MCP Bridge (mcp_bridge.py): 如果 MCP 工具标记为 sub_reader_safe，可授权
    - Zone A Token Budget: BudgetPolicy 可根据剩余 budget 限制工具数量

架构层次:
    Layer 0 (宪法层): SecurityPolicy — 绝对禁止列表，不可被其他层覆盖
    Layer 1 (基础层): BaseReaderPolicy — 子视角的核心能力（读、查、报告）
    Layer 2 (上下文层): PhasePolicy + SkillPolicy + MCPPolicy — 根据运行时状态动态授权
    Layer 3 (预算层): BudgetPolicy — 根据工具描述 token 开销做最终裁剪
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from core.state import WorkspaceState
    from core.skills.tool_group import ToolGroupManager

logger = logging.getLogger(__name__)


# ==============================================================
# Policy 接口
# ==============================================================

class SubReaderToolPolicy(ABC):
    """子视角工具策略接口。

    每个 Policy 实现两个方法：
    - allow(tool_name): 是否允许该工具
    - deny(tool_name): 是否禁止该工具（优先级高于 allow）
    
    组合规则：
    - 如果任何 Policy deny → 最终禁止（一票否决）
    - 如果至少一个 Policy allow 且无 deny → 最终允许
    - 如果无 Policy allow → 最终禁止（封闭默认）
    """

    @abstractmethod
    def allow(self, tool_name: str, context: "PolicyContext") -> bool:
        """该 Policy 是否主动授权此工具。"""
        ...

    @abstractmethod
    def deny(self, tool_name: str, context: "PolicyContext") -> bool:
        """该 Policy 是否强制禁止此工具（不可被 allow 覆盖）。"""
        ...


@dataclass
class PolicyContext:
    """策略决策所需的运行时上下文。

    由 ToolPolicyEngine 在调用时构建，包含子视角需要的所有决策信息。
    """
    lens: str                               # 子视角类型
    focus: str                              # 聚焦的 section
    question: str                           # 研究问题
    phase: str = ""                         # 当前认知阶段
    paper_type: str | None = None           # 论文学科类型
    remaining_budget: int = 60000           # 子视角剩余 token 预算
    state: Any = None                       # WorkspaceState 引用
    tool_group_manager: Any = None          # ToolGroupManager 引用
    skill_registry: Any = None              # SkillRegistry 引用


# ==============================================================
# Layer 0: SecurityPolicy（宪法层，不可覆盖）
# ==============================================================

class SecurityPolicy(SubReaderToolPolicy):
    """绝对安全边界。这些工具在任何情况下都不允许子视角使用。

    理由：
    - spawn_*: 防止递归嵌套（子视角再 spawn 子子视角）
    - talk_to_user: 子视角不应与用户直接对话
    - edit_*: 子视角的职责是发现问题，不是修改论文
    - request_phase_transition: 子视角不应控制主 Agent 的状态机
    - switch_persona: 子视角不应切换人格
    """

    ABSOLUTE_DENY: frozenset = frozenset({
        "spawn_perspective",
        "spawn_parallel_readers",
        "talk_to_user",
        "edit_section",
        "edit_paragraph",
        "reword_sentence",
        "insert_content",
        "generate_edit_plan",
        "switch_persona",
        "request_phase_transition",
        "generate_cognitive_hints",
        "reflect_and_plan",
    })

    def allow(self, tool_name: str, context: PolicyContext) -> bool:
        return False  # SecurityPolicy 不主动授权任何工具

    def deny(self, tool_name: str, context: PolicyContext) -> bool:
        return tool_name in self.ABSOLUTE_DENY


# ==============================================================
# Layer 1: BaseReaderPolicy（基础能力层）
# ==============================================================

class BaseReaderPolicy(SubReaderToolPolicy):
    """子视角的核心能力——无论什么 lens 都需要的基础工具。"""

    BASE_TOOLS: frozenset = frozenset({
        "read_section",
        "find_in_paper",
        "update_findings",
        "review_findings",
        "mark_complete",
    })

    def allow(self, tool_name: str, context: PolicyContext) -> bool:
        return tool_name in self.BASE_TOOLS

    def deny(self, tool_name: str, context: PolicyContext) -> bool:
        return False


# ==============================================================
# Layer 2: PhasePolicy（阶段感知层）
# ==============================================================

class PhasePolicy(SubReaderToolPolicy):
    """根据当前认知阶段，授权阶段特有的工具。

    复用 ToolGroupManager 的 Phase → ToolGroup 映射逻辑，
    但只允许子视角使用其中的「分析型」工具（排除编辑型）。
    """

    def allow(self, tool_name: str, context: PolicyContext) -> bool:
        if not context.tool_group_manager or not context.phase:
            return False
        # 查询 ToolGroupManager 中当前 phase 激活的工具
        active_skills = context.tool_group_manager.get_active_skills()
        active_names = {s.descriptor.name for s in active_skills}
        return tool_name in active_names

    def deny(self, tool_name: str, context: PolicyContext) -> bool:
        return False


# ==============================================================
# Layer 2: SkillPolicy（动态 Skill 感知层）
# ==============================================================

class SkillPolicy(SubReaderToolPolicy):
    """根据已加载的 SkillX 动态工具，有条件地授权给子视角。

    规则：
    - Skill 必须标记 `sub_reader_safe = True` 才会被授权
    - 这允许 Skill 开发者显式声明其工具是否适合子视角使用
    """

    def allow(self, tool_name: str, context: PolicyContext) -> bool:
        if not context.skill_registry:
            return False
        skill = context.skill_registry.get_skill_by_tool_name(tool_name)
        if skill is None:
            return False
        # Skill 必须显式标记为 sub_reader_safe
        return getattr(skill, 'sub_reader_safe', False)

    def deny(self, tool_name: str, context: PolicyContext) -> bool:
        return False


# ==============================================================
# Layer 2: LensSpecificPolicy（lens 类型特化层）
# ==============================================================

class LensSpecificPolicy(SubReaderToolPolicy):
    """根据子视角的 lens 类型，授权特定工具。

    这是可配置的映射，替代旧的硬编码白名单。
    可在运行时通过 register_lens_tools() 扩展。
    """

    def __init__(self):
        # 默认映射：可在运行时动态扩展
        self._lens_tools: dict[str, set[str]] = {
            "methodology_critic": {"search_literature"},
            "literature_verifier": {"search_literature", "verify_citation"},
            "contribution_evaluator": {"search_literature"},
            "statistical_auditor": {"search_literature"},
        }

    def register_lens_tools(self, lens: str, tools: set[str]) -> None:
        """运行时扩展 lens → 工具映射。

        使用场景：
        - Zone A 策略新增工具时，可以声明哪些 lens 可用
        - 新 Skill 注册时，关联到适合的 lens
        """
        existing = self._lens_tools.get(lens, set())
        self._lens_tools[lens] = existing | tools

    def allow(self, tool_name: str, context: PolicyContext) -> bool:
        lens_tools = self._lens_tools.get(context.lens, set())
        return tool_name in lens_tools

    def deny(self, tool_name: str, context: PolicyContext) -> bool:
        return False


# ==============================================================
# Layer 3: BudgetPolicy（预算裁剪层）
# ==============================================================

class BudgetPolicy(SubReaderToolPolicy):
    """根据 token 预算限制工具数量。

    如果子视角的 budget 很小（如 MIN_BUDGET_PER_READER=15000），
    工具描述本身就占了大量 context，需要裁剪到最小集。
    """

    # 每个工具描述的估计 token 开销
    TOOL_TOKEN_COST_ESTIMATE: int = 150
    # 工具描述最多占 budget 的比例
    MAX_TOOL_BUDGET_RATIO: float = 0.08  # 8%

    def allow(self, tool_name: str, context: PolicyContext) -> bool:
        return True  # BudgetPolicy 不主动限制单个工具

    def deny(self, tool_name: str, context: PolicyContext) -> bool:
        return False  # 裁剪逻辑在 Engine 层执行，不在单个 deny 中


# ==============================================================
# ToolPolicyEngine（组合执行器）
# ==============================================================

class ToolPolicyEngine:
    """组合所有 Policy，计算子视角的最终可用工具集。

    组合规则（封闭默认 + 一票否决）：
    1. 收集所有 Policy 的 deny 结果 → 如果任何一个 deny，最终禁止
    2. 收集所有 Policy 的 allow 结果 → 如果至少一个 allow 且无 deny，最终允许
    3. 如果无 Policy allow（也无 deny）→ 最终禁止（封闭默认）

    使用方式：
        engine = ToolPolicyEngine.default()
        context = PolicyContext(lens="methodology_critic", phase="deep_review", ...)
        allowed_tools = engine.compute_allowed_tools(all_tools, context)
    """

    def __init__(self, policies: list[SubReaderToolPolicy] | None = None):
        self._policies = policies or []

    def add_policy(self, policy: SubReaderToolPolicy) -> None:
        """运行时添加新 Policy。"""
        self._policies.append(policy)

    def compute_allowed_tools(
        self,
        all_tools: list[dict],
        context: PolicyContext,
    ) -> list[dict]:
        """计算最终可用工具集。

        Args:
            all_tools: 主 Agent 的全量工具定义
            context: 决策上下文

        Returns:
            子视角可用的工具子集
        """
        allowed = []
        for tool in all_tools:
            name = tool.get("name", "")
            if self._is_allowed(name, context):
                allowed.append(tool)

        # Layer 3: Budget 裁剪
        allowed = self._budget_trim(allowed, context)

        # 兜底：确保 mark_complete 存在
        if not any(t.get("name") == "mark_complete" for t in allowed):
            allowed.append({
                "name": "mark_complete",
                "description": "审视完毕，报告结论。",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string", "description": "你的整体结论和关键发现摘要"}
                    },
                    "required": ["summary"]
                }
            })

        return allowed

    def _is_allowed(self, tool_name: str, context: PolicyContext) -> bool:
        """单个工具的最终判定。"""
        has_allow = False
        for policy in self._policies:
            # 一票否决
            if policy.deny(tool_name, context):
                return False
            if policy.allow(tool_name, context):
                has_allow = True
        # 封闭默认：无人 allow 则禁止
        return has_allow

    def _budget_trim(
        self, tools: list[dict], context: PolicyContext
    ) -> list[dict]:
        """根据 token 预算裁剪工具数量。"""
        max_tool_tokens = int(context.remaining_budget * BudgetPolicy.MAX_TOOL_BUDGET_RATIO)
        max_tools = max(5, max_tool_tokens // BudgetPolicy.TOOL_TOKEN_COST_ESTIMATE)
        if len(tools) <= max_tools:
            return tools
        # 保留基础工具（BaseReaderPolicy 的），其余按出现顺序裁剪
        base_names = BaseReaderPolicy.BASE_TOOLS
        base = [t for t in tools if t.get("name") in base_names]
        extras = [t for t in tools if t.get("name") not in base_names]
        return base + extras[:max_tools - len(base)]

    @classmethod
    def default(cls) -> "ToolPolicyEngine":
        """创建默认 Policy 组合。"""
        return cls(policies=[
            SecurityPolicy(),       # Layer 0: 绝对禁止
            BaseReaderPolicy(),     # Layer 1: 基础能力
            PhasePolicy(),          # Layer 2: 阶段感知
            SkillPolicy(),          # Layer 2: Skill 动态授权
            LensSpecificPolicy(),   # Layer 2: lens 特化
            BudgetPolicy(),         # Layer 3: 预算裁剪
        ])
```

### 改动文件：`v2/core/loop.py` — 集成 ToolPolicyEngine

```python
# 在 _run_parallel_perspectives() 中

from core.godel_config import GODEL_SUB_READER_POLICY_ENABLED
from core.sub_reader_policy import ToolPolicyEngine, PolicyContext

async def _run_single(reader: dict) -> dict:
    lens = reader["lens"]
    focus = reader["focus"]
    question = reader["question"]

    # ... sub_harness 创建 ...

    # 工具权限：通过 Policy 层决定
    if GODEL_SUB_READER_POLICY_ENABLED:
        policy_engine = getattr(harness, '_tool_policy_engine', None)
        if policy_engine is None:
            policy_engine = ToolPolicyEngine.default()

        policy_ctx = PolicyContext(
            lens=lens,
            focus=focus,
            question=question,
            phase=harness.phase_fsm.phase_name if hasattr(harness, 'phase_fsm') else "",
            paper_type=getattr(harness, '_inferred_paper_type', None),
            remaining_budget=budget_per_reader,
            state=harness.state,
            tool_group_manager=getattr(harness, 'tool_group_manager', None),
            skill_registry=getattr(harness, 'skill_registry', None),
        )
        sub_tools = policy_engine.compute_allowed_tools(SUB_PERSPECTIVE_TOOLS_BASE, policy_ctx)
    else:
        sub_tools = SUB_PERSPECTIVE_TOOLS  # 旧行为

    sub_result = await cognitive_loop(
        messages=sub_messages,
        harness=sub_harness,
        tools=sub_tools,
        client=sub_client,
        verbose=False,
    )
```

### 可扩展性示例

**场景 A：Zone A 策略新增 PCG 导航工具，希望 methodology_critic 也能用**

```python
# 在 Harness 初始化 PCG 后
harness._tool_policy_engine.get_policy(LensSpecificPolicy).register_lens_tools(
    lens="methodology_critic",
    tools={"navigate_pcg", "query_evidence_chain"},
)
```

**场景 B：SkillX 动态注册了一个 `stata_verify` Skill，标记为子视角安全**

```python
# 在 SkillRegistry.register() 中
class StataVerifySkill(Skill):
    sub_reader_safe = True  # SkillPolicy 会自动授权给子视角
```

**场景 C：新增一种 lens 类型 `causal_inference_auditor`**

```python
# 无需改代码，运行时注册
policy_engine.get_policy(LensSpecificPolicy).register_lens_tools(
    lens="causal_inference_auditor",
    tools={"search_literature", "verify_citation", "query_evidence_chain"},
)
```

### 改动文件：`v2/core/godel_config.py`

```python
GODEL_SUB_READER_POLICY_ENABLED: bool = _env_flag("SCHOLAR_GODEL_SUB_READER_POLICY")
"""子视角 ToolPolicy 层。OFF 时退回旧的黑名单过滤策略（build_sub_perspective_tools）。"""
```

---

## 优化 3：认知习惯渐进加载（减少 System Prompt 膨胀）

### 问题诊断

当前 `_compute_cognitive_habits()`（assembler.py:218-224）每阶段选 5 条习惯注入完整文本。CachePolicy.PHASE 意味着整个阶段内重复注入同样的长文本。

实际上，Agent 只需要在阶段初期「学习」习惯的完整含义，之后一行摘要足以激活已学到的行为模式。

### 方案设计：三阶段渐进 + 阶段切换重置

```
阶段开始 → 前 2 轮: 注入完整 content（~150 tokens/条 × 5 = ~750 tokens）
         → 第 3-5 轮: 注入 short_content（~30 tokens/条 × 5 = ~150 tokens）
         → 第 6+ 轮: 只注入 name 列表（~50 tokens）
阶段切换 → 重置计数，新阶段重新学习
```

### 改动文件：`v2/core/habits.py`

为 `CognitiveHabit` 添加 `short_content`，为 `HabitSelector` 添加渐进格式化：

```python
@dataclass
class CognitiveHabit:
    id: str
    name: str
    phases: list[str]
    priority: int
    content: str
    short_content: str = ""          # 新增：摘要版
    triggers: list[str] = field(default_factory=list)
    discipline_triggers: dict[str, list[str]] = field(default_factory=dict)

    def __post_init__(self):
        if not self.short_content:
            self.short_content = self._auto_summarize()

    def _auto_summarize(self) -> str:
        """提取核心要点（第一句话）。"""
        import re
        text = re.sub(r'\*\*([^*]+)\*\*', r'\1', self.content)
        for sep in ['。', '；', '\n', '. ']:
            idx = text.find(sep)
            if idx > 0:
                return text[:idx + len(sep)].strip()
        return text[:80].strip()
```

```python
class HabitSelector:
    def __init__(self, habits=None, max_per_turn: int = 5):
        # ... 现有初始化 ...
        self._phase_injection_counts: dict[str, int] = {}  # habit_id → 本阶段注入次数
        self._current_phase: str = ""

    def select_and_format_progressive(
        self,
        phase: str = "",
        turn: int = 0,
        triggers: list[str] | None = None,
        paper_type: str | None = None,
    ) -> str:
        """渐进格式化：完整 → 摘要 → 名称列表。"""
        # 阶段切换检测
        if phase != self._current_phase:
            self._phase_injection_counts.clear()
            self._current_phase = phase

        selected = self.select(phase=phase, turn=turn, triggers=triggers, paper_type=paper_type)
        if not selected:
            return ""

        lines = ["## 当前阶段的认知习惯", ""]
        for h in selected:
            count = self._phase_injection_counts.get(h.id, 0)
            self._phase_injection_counts[h.id] = count + 1

            if count < 2:
                # 前 2 次：完整版
                lines.append(f"- {h.content}")
            elif count < 5:
                # 第 3-5 次：摘要版
                lines.append(f"- {h.short_content}")
            else:
                # 第 6 次起：只有名字（激活效应）
                lines.append(f"- [{h.name}]")
            lines.append("")

        return "\n".join(lines).rstrip()

    def reset_phase(self) -> None:
        """外部调用：阶段切换时重置。"""
        self._phase_injection_counts.clear()
        self._current_phase = ""
```

### 改动文件：`v2/core/assembler.py`

```python
def _compute_cognitive_habits(ctx: dict) -> str:
    """认知习惯（渐进加载版本）。"""
    from core.godel_config import GODEL_HABIT_PROGRESSIVE_ENABLED

    phase: str = ctx.get("current_phase", "")
    turn: int = ctx.get("current_turn", 0)
    selector: HabitSelector = ctx["habit_selector"]
    paper_type: str | None = _infer_paper_type(ctx)

    if GODEL_HABIT_PROGRESSIVE_ENABLED:
        return selector.select_and_format_progressive(
            phase=phase, turn=turn, paper_type=paper_type
        )
    else:
        return selector.select_and_format(phase=phase, turn=turn, paper_type=paper_type)
```

**缓存策略调整**：渐进加载要求每轮重新计算（因为 injection_count 递增），需要将 cache_policy 从 PHASE 改为 NEVER：

```python
# assembler.py 注册时
self.registry.register(
    name="cognitive_habits",
    priority=95,
    cache_policy=(
        CachePolicy.NEVER if GODEL_HABIT_PROGRESSIVE_ENABLED
        else CachePolicy.PHASE
    ),
    compute_fn=_compute_cognitive_habits,
)
```

### 改动文件：`v2/core/godel_config.py`

```python
GODEL_HABIT_PROGRESSIVE_ENABLED: bool = _env_flag("SCHOLAR_GODEL_HABIT_PROGRESSIVE")
"""认知习惯渐进加载（完整→摘要→名称）。OFF 时保持当前的 PHASE 缓存全量注入。"""
```

### 预期效果（DEEP_REVIEW 阶段 15 轮为例）

| 轮次 | 当前方案 | 渐进方案 | 节省 |
|------|----------|----------|------|
| 1-2 | ~750 tokens | ~750 tokens | 0% |
| 3-5 | ~750 tokens | ~150 tokens | 80% |
| 6-15 | ~750 tokens | ~50 tokens | 93% |
| 15 轮总计 | ~11250 tokens (PHASE缓存=单次计算但每轮重复注入) | ~3750 tokens | 67% |

---

## 三项优化的架构关系

```
                    ┌─────────────────────────────────────────────┐
                    │         Spawn 决策时                          │
                    │                                             │
                    │  主 Agent / MCL auto_spawn                    │
                    │         │                                   │
                    │         ▼                                   │
                    │  ┌─── MCL.assess_reader_difficulty ────┐    │
                    │  │    (优化1: 模型智能路由)              │    │
                    │  │    返回 {lens: tier}                 │    │
                    │  └──────────────────────────────────────┘    │
                    │         │                                   │
                    │         ▼                                   │
                    │  ┌─── ToolPolicyEngine.compute ────────┐    │
                    │  │    (优化2: 动态权限策略)              │    │
                    │  │    Security → Base → Phase →         │    │
                    │  │    Skill → Lens → Budget             │    │
                    │  │    返回 allowed_tools[]              │    │
                    │  └──────────────────────────────────────┘    │
                    │         │                                   │
                    │         ▼                                   │
                    │  cognitive_loop(                            │
                    │      client=sub_client,  ← 优化1 路由的模型 │
                    │      tools=sub_tools,    ← 优化2 计算的工具集│
                    │  )                                          │
                    └─────────────────────────────────────────────┘

                    ┌─────────────────────────────────────────────┐
                    │         每轮 Context 组装时                   │
                    │                                             │
                    │  assembler.assemble()                        │
                    │         │                                   │
                    │         ▼                                   │
                    │  _compute_cognitive_habits()                 │
                    │  (优化3: 渐进加载)                            │
                    │  count < 2 → full | count < 5 → short       │
                    │  count >= 5 → name only                     │
                    └─────────────────────────────────────────────┘
```

## Kill Switches 汇总（已实现）

| 环境变量 | 控制 | 默认 | OFF 时行为 | 状态 |
|----------|------|------|------------|------|
| `SCHOLAR_GODEL_SUB_READER_ROUTING` | 模型智能路由 | ON | 子视角用主模型 | ✅ 已实现 |
| `SCHOLAR_GODEL_HABIT_PROGRESSIVE` | 习惯渐进加载 | ON | PHASE 缓存全量注入 | ✅ 已实现 |

## 已完成的实施步骤

```
✅ Phase A: 基础设施
  1. LLMClient.with_model_override() — v2/llm/client.py
  2. godel_config.py 新增 2 个 kill switch + log_config_status()

✅ Phase B: MCL 路由
  3. MCL.assess_reader_difficulty() + _static_difficulty_fallback() — v2/core/meta_cognition_layer.py
  4. loop.py 集成 _run_parallel_perspectives() — MCL batch 评估 + 模型/budget 路由
  5. loop.py 集成 _run_sub_perspective() — 单视角 MCL 评估

✅ Phase C: 渐进加载
  6. habits.py — CognitiveHabit.short_content 字段 + 全部 19 条习惯的 short_content
  7. habits.py — HabitSelector.select_and_format_progressive()
  8. assembler.py — _compute_cognitive_habits() 条件分支 + 缓存策略 NEVER/PHASE 动态切换
```

## 暂缓项

```
⏸️ Phase D: ToolPolicy 层（权限隔离） — 用户决定暂不需要
  - sub_reader_policy.py（设计方案已存档在本文档优化 2 中）
  - SCHOLAR_GODEL_SUB_READER_POLICY kill switch
```

## 风险评估（修订）

| 优化项 | 风险 | 缓解措施 |
|--------|------|----------|
| MCL 路由 | MCL 判断错误导致高难任务用低模型 | fallback 规则偏保守（默认 "high"）；`hasattr(harness, 'mcl')` 检查确保 MCL 不可用时退化为全 high；kill switch 可一键关闭 |
| MCL 路由 | MCL 调用失败阻塞 spawn | `try/except` + `_static_difficulty_fallback()` 保证不阻塞 |
| 习惯渐进 | 过早精简导致 Agent 遗忘关键规则 | 3 轮缓冲期（`depth_turn_threshold=3`）；精简版仍保留核心要义；kill switch 可回退到全量注入 |
