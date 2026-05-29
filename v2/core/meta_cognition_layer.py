"""
core/meta_cognition_layer.py — MetaCognitionLayer (MCL)

小模型反馈层：用轻量 LLM (gpt-4.1-mini) 审视主 Agent 的工作状态，
产出具体的、有信息含量的反馈。

设计理念:
    MCL 是系统的"第二双眼睛"——它不审稿，但审视审稿人。
    它解决的核心问题：主 Agent 陷入"安全感假象"后过早退出。

    类比：论文的 Reviewer 1 写完评审后，Area Chair 会检查：
    - 你的评审覆盖了所有关键 section 吗？
    - 你提出的问题有原文证据支撑吗？
    - 你是否遗漏了方法论上的明显缺陷？

    MCL 就是这个 Area Chair。

架构集成:
    1. Harness 初始化时创建 MCL（共享 LLM client 实例）
    2. Loop.py 在 Agent 调用 mark_complete 时 await MCL
    3. MCL 的反馈以 tool_response 形式返回（Agent 必须面对）
    4. MCL 的 spawn 推荐直接由 loop 执行（bypass Agent 决策）
    5. MCL 取代 boundary_guard 中的硬编码 spawn_gate

成本: < 0.1% of total (gpt-4.1-mini, ~1500 tokens/call, 2-3 calls/paper)
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ============================================================
# Configuration
# ============================================================

# MCL 使用的模型
MCL_MODEL = os.environ.get("MCL_MODEL", "gpt-4.1-mini")

# MCL token 上限
MCL_MAX_TOKENS = 1000

# MCL 温度
MCL_TEMPERATURE = 0.2

# 最小 findings 数量（太少时 MCL 无法有效判断）
MCL_MIN_FINDINGS = 3

# 最小 sections（论文太短时 MCL 判断不准）
MCL_MIN_SECTIONS = 3


# ============================================================
# Prompts
# ============================================================

MCL_SYSTEM_PROMPT = """\
你是学术审稿的 Meta-Cognition 层（Area Chair 角色）。
你审视另一个 AI 审稿人的工作状态，判断它是否可以结束审稿。

你 **不** 直接审稿。你只评估审稿人的工作质量。

## 你的判断标准（按重要性排序）

1. **精确性** (Precision)
   - 审稿人的 findings 是否都有原文证据支撑？
   - 有没有 finding 仅凭主观推测而无具体引用？
   - 产品定位：宁可漏报，不可误报。

2. **覆盖度** (Coverage)
   - 论文的核心 sections (Methodology/Results/Discussion) 是否都被审视过？
   - 审稿人是否只从单一角度审视（缺乏交叉验证）？

3. **深度** (Depth)
   - 关键 findings 是否停留在表面（如"表述不清"）而非深入方法论问题？
   - 审稿人是否验证了自己的关键发现？

## 输出格式（严格 JSON，无额外文字）

```json
{
  "verdict": "pass" | "block",
  "confidence": 0.0-1.0,
  "reason": "一句话（<60字）解释判断原因",
  "feedback": [
    {
      "dimension": "precision" | "coverage" | "depth",
      "target": "指向具体 finding 编号或 section 名",
      "action": "具体的下一步操作建议（<80字）"
    }
  ],
  "auto_spawn": {
    "needed": false,
    "perspectives": []
  }
}
```

## 关键规则
- feedback 最多 3 条，按优先级排列
- verdict="pass" 意味着可以结束（即使有小瑕疵）
- verdict="block" 意味着有重要遗漏必须 address
- auto_spawn.needed=true 仅当覆盖度严重不足（如核心 section 未读）时
- 你是辅助角色，不要过度阻止。只在有明确证据表明审稿质量不足时才 block。
"""

MCL_COMPLETION_GATE_USER = """\
## 论文审稿状态（审稿人刚调用了 mark_complete）

**论文标题**: {paper_title}

### Section 覆盖
- 已读: {sections_read_list}
- 未读: {unread_list}
- 覆盖率: {read_count}/{total_count}

### Findings ({findings_count} 条)
{findings_detail}

### 审稿人行为概况
- 总轮次: {turns_used}/{max_turns}
- 多视角 spawn: {spawn_count} 次
- 文献检索: {search_count} 次
- 已验证 findings: {verified_count} 条
- 最近动作: {recent_actions}

### 请做出你的判断
"""

MCL_STAGNATION_USER = """\
## 审稿人行为检测（连续 {stagnant_turns} 轮无新发现）

**论文标题**: {paper_title}
**进度**: Turn {turns_used}/{max_turns}
**当前 Findings**: {findings_count} 条

### 最近 {stagnant_turns} 轮动作
{recent_actions}

### 未读 Sections
{unread_list}

### 请判断：审稿人是卡住了还是确实审完了？

如果卡住了，给出具体建议帮助其突破。
如果确实审完了，verdict="pass"。
"""


# ============================================================
# MCL Difficulty Assessment Prompts (模型智能路由)
# ============================================================

_MCL_DIFFICULTY_SYSTEM = """\
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
- **high**: 需要深度推理——复杂因果识别、结构模型假设审视、跨领域文献综合、非标准统计方法验证
- **medium**: 结构化分析——标准方法论检查、常规文献验证、逻辑一致性、标准统计检查
- **low**: 模式匹配——数值一致性比对、符号一致性检查、格式审查、引用格式验证

## 重要规则
- 同一个 lens 类型面对不同论文可能需要不同 tier
- 宁可给 high 也不要误给 low（保守原则）
- 如果论文学科涉及复杂数学/计量模型，methodology 类 lens 倾向 high
"""

_MCL_DIFFICULTY_USER = """\
## 论文信息
- 标题: {paper_title}
- 学科类型: {paper_type}
- Section 数量: {section_count}
- 当前已有 findings: {findings_count} 条

## 待评估的子视角
{readers_desc}

请评估每个子视角所需的认知能力层级。
"""


# ============================================================
# Data Models
# ============================================================

@dataclass
class MCLFeedbackItem:
    """一条 MCL 反馈。"""
    dimension: str  # precision | coverage | depth
    target: str     # 具体指向
    action: str     # 建议动作


@dataclass
class MCLVerdict:
    """MCL 的完整判决。"""
    verdict: str = "pass"          # "pass" | "block"
    confidence: float = 0.5
    reason: str = ""
    feedback: list[MCLFeedbackItem] = field(default_factory=list)
    auto_spawn_needed: bool = False
    auto_spawn_perspectives: list[str] = field(default_factory=list)
    raw_response: str = ""         # 调试用

    @property
    def should_block(self) -> bool:
        return self.verdict == "block"


# ============================================================
# MetaCognitionLayer
# ============================================================

class MetaCognitionLayer:
    """
    轻量 LLM 反馈层 — 审视主 Agent 的工作质量。

    生命周期:
        1. Harness.__init__ 时创建 MCL（传入 LLM client）
        2. Loop 检测到 mark_complete 时 await mcl.gate_completion(state)
        3. MCL 返回 verdict: pass → 放行, block → 返回反馈给 Agent
        4. MCL 只 block 一次, 第二次 mark_complete 无条件放行

    与旧 spawn_gate 的关系:
        MCL 取代 boundary_guard.check_completion_gate 中的 spawn_gate 逻辑。
        spawn_gate 是硬编码 if/else，MCL 是智能判断。
        旧的 spawn_gate 代码保留作为 MCL 不可用时的 fallback。
    """

    def __init__(self, llm_client: Any, model: str | None = None, session_model_mgr=None):
        """
        Args:
            llm_client: LLMClient 实例（复用主 Agent 的连接）
            model: 覆盖默认 MCL 模型
            session_model_mgr: Optional SessionModelManager for Phase 4 model assignment.
                When provided, uses providers.json config for MCL model.
                Priority: session_model_mgr > model param > env var MCL_MODEL.
        """
        self._client = llm_client
        if session_model_mgr is not None:
            resolved = session_model_mgr.resolve_model_for_role("mcl")
            self._model = resolved if resolved is not None else (model or MCL_MODEL)
        elif model:
            self._model = model
        else:
            self._model = MCL_MODEL
        self._gate_fired: bool = False  # 只 block 一次
        self._stagnation_fired: bool = False
        self._total_calls: int = 0

    # ----------------------------------------------------------
    # 对外接口 1: Completion Gate
    # ----------------------------------------------------------

    async def gate_completion(self, state: Any) -> MCLVerdict:
        """
        Agent 调用 mark_complete 时的质量门控。

        设计:
        - 只 block 一次。已经 block 过 → 直接 pass（Agent 坚持）
        - MCL 不可用或调用失败 → 优雅降级为 pass
        - MCL 判断 pass → 放行
        - MCL 判断 block → 返回具体反馈

        Args:
            state: WorkspaceState

        Returns:
            MCLVerdict
        """
        # 只 block 一次
        if self._gate_fired:
            logger.info("[MCL] Completion gate already fired once. Pass-through.")
            return MCLVerdict(verdict="pass", reason="第二次 mark_complete，放行")

        # 前置条件: findings 太少时 MCL 判断不准，跳过
        if len(state.findings) < MCL_MIN_FINDINGS:
            return MCLVerdict(verdict="pass", reason="findings 不足，跳过 MCL")

        # 构建 prompt
        user_prompt = self._build_completion_prompt(state)

        # 调用小模型
        verdict = await self._invoke(user_prompt)

        # 记录
        if verdict.should_block:
            self._gate_fired = True
            logger.info(
                "[MCL] Completion BLOCKED. Reason: %s. Feedback: %d items. Auto-spawn: %s",
                verdict.reason, len(verdict.feedback), verdict.auto_spawn_needed,
            )
        else:
            logger.info("[MCL] Completion PASSED. Reason: %s", verdict.reason)

        return verdict

    # ----------------------------------------------------------
    # 对外接口 2: Stagnation Check
    # ----------------------------------------------------------

    async def check_stagnation(
        self, state: Any, stagnant_turns: int
    ) -> MCLVerdict | None:
        """
        审稿人连续多轮无新发现时的智能检测。

        只触发一次。与周期性 nudge 不同，这是对"卡住"的诊断。

        Args:
            state: WorkspaceState
            stagnant_turns: 连续无新 finding 的轮数

        Returns:
            MCLVerdict（block=建议继续, pass=可以结束）或 None（不触发）
        """
        if self._stagnation_fired:
            return None

        # 只在卡住较久时触发
        if stagnant_turns < 3:
            return None

        user_prompt = self._build_stagnation_prompt(state, stagnant_turns)
        verdict = await self._invoke(user_prompt)

        self._stagnation_fired = True
        logger.info("[MCL] Stagnation check: verdict=%s", verdict.verdict)

        return verdict

    # ----------------------------------------------------------
    # 格式化输出（给 Agent 看的反馈）
    # ----------------------------------------------------------

    def format_completion_feedback(self, verdict: MCLVerdict) -> str:
        """
        将 MCL verdict 格式化为注入到 Agent tool_response 中的文本。

        设计: 让 Agent 无法忽视——具体、可操作、有压力。
        """
        parts = []

        # 主判断
        parts.append(f"[质量审计] {verdict.reason}")

        # 具体反馈
        if verdict.feedback:
            parts.append("")
            dim_label = {
                "precision": "精确性",
                "coverage": "覆盖度",
                "depth": "深度",
            }
            for i, fb in enumerate(verdict.feedback, 1):
                label = dim_label.get(fb.dimension, fb.dimension)
                parts.append(f"{i}. [{label}] {fb.target} → {fb.action}")

        # Auto-spawn 提示
        if verdict.auto_spawn_needed and verdict.auto_spawn_perspectives:
            parts.append("")
            parts.append(
                "系统将自动执行多视角审视: "
                + ", ".join(verdict.auto_spawn_perspectives[:3])
            )

        # 结束提示
        parts.append("")
        parts.append("请 address 上述反馈后再次调用 mark_complete。")

        return "\n".join(parts)

    def format_stagnation_feedback(self, verdict: MCLVerdict) -> str:
        """格式化停滞检测的反馈。"""
        if not verdict.should_block:
            return ""

        parts = [f"[MCL 观察] {verdict.reason}"]
        if verdict.feedback:
            for fb in verdict.feedback[:2]:
                parts.append(f"  → {fb.action}")
        return "\n".join(parts)

    # ----------------------------------------------------------
    # 内部: LLM 调用
    # ----------------------------------------------------------

    async def _invoke(self, user_prompt: str) -> MCLVerdict:
        """调用 MCL 小模型，解析结果。失败时优雅降级。"""
        try:
            response = await self._client.chat(
                system=MCL_SYSTEM_PROMPT,
                user=user_prompt,
                temperature=MCL_TEMPERATURE,
                max_tokens=MCL_MAX_TOKENS,
                model=self._model,
            )

            self._total_calls += 1
            logger.debug("[MCL] Raw response: %s", response[:300])

            return self._parse(response)

        except Exception as e:
            logger.warning(
                "[MCL] Invocation failed: %s. Graceful fallback to pass.",
                e,
            )
            return MCLVerdict(verdict="pass", reason=f"MCL 调用失败: {e}")

    def _parse(self, raw: str) -> MCLVerdict:
        """解析 MCL 的 JSON 输出。"""
        verdict = MCLVerdict(raw_response=raw)
        parsed = _extract_json(raw)

        if parsed is None:
            logger.warning("[MCL] JSON parse failed. Raw[:200]: %s", raw[:200])
            # 解析失败 → pass（不阻止主流程）
            verdict.reason = "MCL 输出解析失败，放行"
            return verdict

        # verdict
        v = parsed.get("verdict", "pass")
        verdict.verdict = v if v in ("pass", "block") else "pass"

        # confidence
        try:
            verdict.confidence = max(0.0, min(1.0, float(parsed.get("confidence", 0.5))))
        except (TypeError, ValueError):
            verdict.confidence = 0.5

        # reason
        verdict.reason = str(parsed.get("reason", ""))[:120]

        # feedback
        for fb in parsed.get("feedback", []):
            if isinstance(fb, dict) and fb.get("action"):
                verdict.feedback.append(MCLFeedbackItem(
                    dimension=fb.get("dimension", "coverage"),
                    target=str(fb.get("target", "")),
                    action=str(fb.get("action", ""))[:150],
                ))

        # auto_spawn
        spawn_data = parsed.get("auto_spawn", {})
        if isinstance(spawn_data, dict):
            verdict.auto_spawn_needed = bool(spawn_data.get("needed", False))
            perps = spawn_data.get("perspectives", [])
            if isinstance(perps, list):
                verdict.auto_spawn_perspectives = [str(p) for p in perps[:4]]

        return verdict

    # ----------------------------------------------------------
    # 内部: Prompt 构建
    # ----------------------------------------------------------

    def _build_completion_prompt(self, state: Any) -> str:
        """为 completion gate 构建 user prompt。"""
        paper_title = getattr(state, "paper_title", "") or "Unknown"

        # Sections
        all_sections = [k for k in (state.paper_sections or {}) if k != "full"]
        sections_read = list(state.sections_read) if state.sections_read else []
        unread = [s for s in all_sections if s not in sections_read]

        # Findings 详情（精简但足够 MCL 判断）
        findings_detail = self._format_findings(state.findings)

        # 行为统计
        spawn_count = sum(
            1 for t in (state.tool_call_history or [])
            if (t.get("name") or t.get("tool", "")) in {
                "spawn_perspective", "spawn_parallel_readers"
            }
        )
        search_count = sum(
            1 for t in (state.tool_call_history or [])
            if (t.get("name") or t.get("tool", "")) == "search_literature"
        )
        verified_count = sum(
            1 for f in state.findings if f.get("status") == "verified"
        )
        recent_actions = self._recent_actions(state, 6)

        return MCL_COMPLETION_GATE_USER.format(
            paper_title=paper_title,
            sections_read_list=", ".join(sections_read[:8]) or "无",
            unread_list=", ".join(unread[:8]) or "无",
            read_count=len(sections_read),
            total_count=len(all_sections),
            findings_count=len(state.findings),
            findings_detail=findings_detail,
            turns_used=state.loop_turns,
            max_turns=state.max_loop_turns,
            spawn_count=spawn_count,
            search_count=search_count,
            verified_count=verified_count,
            recent_actions=recent_actions,
        )

    def _build_stagnation_prompt(self, state: Any, stagnant_turns: int) -> str:
        """为停滞检测构建 user prompt。"""
        paper_title = getattr(state, "paper_title", "") or "Unknown"
        all_sections = [k for k in (state.paper_sections or {}) if k != "full"]
        sections_read = list(state.sections_read) if state.sections_read else []
        unread = [s for s in all_sections if s not in sections_read]

        recent_actions = self._recent_actions(state, stagnant_turns + 2)

        return MCL_STAGNATION_USER.format(
            paper_title=paper_title,
            stagnant_turns=stagnant_turns,
            turns_used=state.loop_turns,
            max_turns=state.max_loop_turns,
            findings_count=len(state.findings),
            recent_actions=recent_actions,
            unread_list=", ".join(unread[:6]) or "全部已读",
        )

    # ----------------------------------------------------------
    # 内部: Helpers
    # ----------------------------------------------------------

    def _format_findings(self, findings: list[dict]) -> str:
        """格式化 findings 给 MCL（精简但有效）。"""
        if not findings:
            return "(暂无)"

        lines = []
        for i, f in enumerate(findings[:12], 1):
            priority = f.get("priority", "?")
            status = f.get("status", "unverified")
            text = (f.get("finding") or f.get("text", ""))[:100]
            section = f.get("section", "?")
            evidence = f.get("evidence", "")
            ev_marker = "✓有引用" if evidence else "✗无引用"
            lines.append(f"  #{i} [{priority}|{status}] ({section}) {text} [{ev_marker}]")

        if len(findings) > 12:
            lines.append(f"  ...另有 {len(findings) - 12} 条")

        return "\n".join(lines)

    def _recent_actions(self, state: Any, n: int) -> str:
        """获取最近 N 个 tool call。"""
        history = state.tool_call_history or []
        if not history:
            return "(无)"
        recent = history[-n:]
        return " → ".join(
            (t.get("name") or t.get("tool", "?")) for t in recent
        )

    # ----------------------------------------------------------
    # 对外接口 3: Reader Difficulty Assessment (模型智能路由)
    # ----------------------------------------------------------

    async def assess_reader_difficulty(
        self,
        readers: list[dict],
        state: Any,
        paper_type: str | None = None,
    ) -> dict[str, str]:
        """
        评估每个子视角的认知难度，返回 lens → tier 映射。

        设计：
        - 单次 batch 调用（不是每个 reader 单独调）
        - 成本：~500 tokens × gpt-4.1-mini ≈ $0.0002，可忽略
        - 失败时 fallback 到静态规则（不阻塞流程）
        - MCL 根据论文学科、方法论复杂度、子视角问题做 context-aware 判断

        Args:
            readers: 子视角列表 [{"lens": ..., "focus": ..., "question": ...}]
            state: WorkspaceState
            paper_type: 论文学科类型（从 CognitiveHints 推断）

        Returns:
            {lens_name: "high"/"medium"/"low"}
        """
        if not readers:
            return {}

        # 构建描述
        readers_lines = []
        for i, r in enumerate(readers, 1):
            readers_lines.append(
                f"{i}. lens={r['lens']}, focus={r.get('focus', 'full')}, "
                f"question={r.get('question', '')}"
            )
        readers_desc = "\n".join(readers_lines)

        paper_title = getattr(state, "paper_title", "") or "Unknown"
        section_count = len(state.paper_sections or {})
        findings_count = len(state.findings)

        user_prompt = _MCL_DIFFICULTY_USER.format(
            paper_title=paper_title,
            paper_type=paper_type or "unknown",
            section_count=section_count,
            findings_count=findings_count,
            readers_desc=readers_desc,
        )

        try:
            response = await self._client.chat(
                system=_MCL_DIFFICULTY_SYSTEM,
                user=user_prompt,
                temperature=0.1,
                max_tokens=500,
                model=self._model,
            )
            self._total_calls += 1
            parsed = _extract_json(response)
            if parsed and "assessments" in parsed:
                result = {}
                for a in parsed["assessments"]:
                    if isinstance(a, dict) and "lens" in a:
                        tier = a.get("tier", "medium")
                        if tier in ("high", "medium", "low"):
                            result[a["lens"]] = tier
                if result:
                    logger.info(
                        "[MCL] Difficulty assessment: %s",
                        ", ".join(f"{k}={v}" for k, v in result.items()),
                    )
                    return result
        except Exception as e:
            logger.warning("[MCL] difficulty assessment failed: %s", e)

        # Fallback: 静态规则（保证不阻塞）
        return self._static_difficulty_fallback(readers)

    def _static_difficulty_fallback(self, readers: list[dict]) -> dict[str, str]:
        """MCL 不可用时的静态兜底规则。保守策略：默认 medium，不冒险降级。

        注意：data_consistency 类任务需要跨表数值推理能力，不是简单的模式匹配，
        因此路由到 high tier。symbol_auditor 同理（需要跨公式符号追踪）。
        """
        _STATIC_HINTS = {
            "data_consistency_auditor": "high",   # 跨表数值推理，需要强模型
            "data_consistency_reviewer": "high",  # 同上（boundary_guard 产出的名称）
            "symbol_auditor": "medium",           # 符号追踪需要中等推理
            "symbol_consistency_reviewer": "medium",
            "format_auditor": "low",
            "methodology_critic": "medium",
            "literature_verifier": "medium",
            "contribution_evaluator": "medium",
            "logic_flow_auditor": "medium",
            "assumption_reviewer": "medium",
        }
        return {r["lens"]: _STATIC_HINTS.get(r["lens"], "medium") for r in readers}

    # ----------------------------------------------------------
    # 统计
    # ----------------------------------------------------------

    def stats(self) -> dict:
        """MCL 运行统计。"""
        return {
            "model": self._model,
            "total_calls": self._total_calls,
            "gate_fired": self._gate_fired,
            "stagnation_fired": self._stagnation_fired,
        }


# ============================================================
# Utilities
# ============================================================

def _extract_json(text: str) -> dict | None:
    """从 LLM 响应中提取 JSON。多策略容错。"""
    if not text or not text.strip():
        return None

    # 1. 直接解析
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass

    # 2. ```json 代码块
    blocks = re.findall(r'```(?:json)?\s*\n?(.*?)\n?\s*```', text, re.DOTALL)
    for block in reversed(blocks):
        try:
            return json.loads(block.strip())
        except json.JSONDecodeError:
            continue

    # 3. 最外层 { } 块
    first = text.find('{')
    last = text.rfind('}')
    if first != -1 and last > first:
        try:
            return json.loads(text[first:last + 1])
        except json.JSONDecodeError:
            pass

    return None
