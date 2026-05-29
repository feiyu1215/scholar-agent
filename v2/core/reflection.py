"""
core/reflection.py — Agent 自省模块 (P2 重构: 真正的 Agent-driven 进化)

设计原则:
    - 进化的"判断力"在 Agent 手上，不在 harness 的硬编码规则里
    - Harness 负责: 何时触发反思、存储结果、累积验证
    - Agent 负责: 决定"我这次学到了什么"
    - 累积验证机制: Agent 说一次不够，多次 session 反复得出类似结论才升级为习惯

调用时机:
    - session 结束时，如果用户配置了 reflection_enabled=True
    - 输入: 本次会话的行为摘要（edits, tool usage, findings, strategies）
    - 输出: 0~5 条 ProceduralPattern（evidence=1 开始，后续累积）

与 HabitLearner 的关系:
    - reflection 产出 ProceduralPatterns → 存入 memory
    - HabitLearner 在下次 session 开始时检查这些 patterns
    - 只有 evidence >= 3 的才升级为习惯
    - 所以: reflection 不直接产出习惯，它只产出"经验假说"

零外部依赖:
    - 用项目已有的 LLMClient.chat() 接口
    - 输出用 JSON 约束（类似 session_memory.py 的模式）
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Awaitable

if TYPE_CHECKING:
    from core.memory import MemoryStore
    from core.state import WorkspaceState

logger = logging.getLogger(__name__)


# ============================================================
# Reflection Prompt
# ============================================================

_REFLECTION_SYSTEM_PROMPT = """\
你是一个经验丰富的学术论文审稿人。你刚完成一次审稿会话，现在需要回顾和反思。

你的任务是：从本次会话的行为中提炼可复用的经验。这些经验会在未来的审稿中帮助你。

要求：
1. 只提炼具体、可操作的经验（不要泛泛而谈如"要仔细审稿"）
2. 每条经验必须包含：触发条件（什么时候用）+ 具体做法 + 为什么有效
3. 区分"编辑策略"和"审稿策略"
4. 如果本次会话没有什么值得记住的新发现，返回空列表即可

输出格式（严格 JSON）：
```json
{
  "reflections": [
    {
      "category": "edit_strategy|review_focus|strategy_effectiveness|verification_strategy|tool_sequence",
      "description": "简洁描述这条经验（<100字）",
      "trigger_context": "在什么情况下应该想起这条经验（<80字）",
      "effectiveness_estimate": 0.5-1.0,
      "reasoning": "为什么你认为这是有效的（1-2句话）"
    }
  ]
}
```

注意：
- 最多 5 条，少比多好（只记真正有价值的）
- effectiveness_estimate 是你的主观判断：这条经验在未来多大概率有用？
- 如果觉得本次没什么新鲜的，直接返回 {"reflections": []}
"""

_REFLECTION_USER_TEMPLATE = """\
## 本次会话摘要

### 基本信息
- 总轮数: {loop_turns}
- 产出 findings: {findings_count} 条
- 编辑操作: {edits_count} 次
- 论文类型: {paper_type}

### 工具使用统计
{tool_usage_summary}

### 编辑历史
{edit_summary}

### 关键 findings（按重要性）
{findings_summary}

### 策略转换
{strategy_summary}

---

请回顾以上行为，提炼你学到的可复用经验。如果没有值得记住的新经验，返回空列表。
"""


# ============================================================
# Core: SessionReflector
# ============================================================

@dataclass
class ReflectionResult:
    """一条反思结果。"""
    category: str
    description: str
    trigger_context: str
    effectiveness_estimate: float
    reasoning: str


class SessionReflector:
    """
    Agent 自省器：在 session 结束时让 Agent 自己反思学到了什么。

    设计约束:
        - 一次 LLM call (~1500 tokens input, ~300 tokens output)
        - 输出严格 JSON，解析失败则 gracefully 退化（不存任何东西）
        - 产出的经验存为 ProceduralPattern（evidence=1）
        - 不调用时零开销（reflection_enabled=False 跳过）

    与硬编码规则的区别:
        - 旧方式: if count >= 2 → 生成 pattern（harness 决定什么值得学）
        - 新方式: Agent 回顾行为 → 自己判断什么值得记住（Agent 有判断力）
    """

    # 有效的 category 值
    VALID_CATEGORIES = {
        "edit_strategy",
        "review_focus",
        "strategy_effectiveness",
        "verification_strategy",
        "tool_sequence",
    }

    def __init__(
        self,
        llm_call_fn: Callable[[str, str, int], Awaitable[str]] | None = None,
    ):
        """
        Args:
            llm_call_fn: 异步 LLM 调用函数。签名:
                async (system: str, user: str, max_tokens: int) -> str
                如果为 None，reflect() 将跳过（用于测试或禁用场景）。
        """
        self._llm_call_fn = llm_call_fn

    async def reflect(self, state: "WorkspaceState") -> list[ReflectionResult]:
        """
        执行一次自省。

        Args:
            state: 当前 session 的 WorkspaceState（包含完整行为历史）

        Returns:
            反思结果列表（0~5 条）。调用者负责存入 memory。
        """
        if self._llm_call_fn is None:
            logger.debug("SessionReflector: no LLM function, skipping reflection")
            return []

        # 组装 context
        user_prompt = self._build_user_prompt(state)

        try:
            response = await self._llm_call_fn(
                _REFLECTION_SYSTEM_PROMPT,
                user_prompt,
                800,  # max_tokens: 足够输出 5 条 JSON
            )
            results = self._parse_response(response)
            if results:
                logger.info(
                    "SessionReflector: Agent 反思产出 %d 条经验",
                    len(results),
                )
            return results
        except Exception as e:
            logger.warning("SessionReflector: reflection failed: %s", e)
            return []

    def persist_reflections(
        self,
        results: list[ReflectionResult],
        memory: "MemoryStore",
    ) -> int:
        """
        将反思结果存入 memory（作为 ProceduralPattern, evidence=1 或 reinforce）。

        Args:
            results: reflect() 的返回值
            memory: 记忆存储

        Returns:
            实际存储的条数
        """
        stored = 0
        for r in results:
            if r.category not in self.VALID_CATEGORIES:
                continue
            if not r.description or not r.trigger_context:
                continue

            memory.add_or_reinforce_procedure(
                category=r.category,
                description=r.description,
                trigger_context=r.trigger_context,
                effectiveness_score=r.effectiveness_estimate,
            )
            stored += 1

        return stored

    # ----------------------------------------------------------
    # 内部方法
    # ----------------------------------------------------------

    def _build_user_prompt(self, state: "WorkspaceState") -> str:
        """从 WorkspaceState 组装反思上下文。"""

        # 工具使用统计
        tool_counts: dict[str, int] = {}
        for tc in state.tool_call_history:
            name = tc.get("name", "unknown")
            tool_counts[name] = tool_counts.get(name, 0) + 1

        tool_lines = []
        for name, count in sorted(tool_counts.items(), key=lambda x: -x[1])[:10]:
            tool_lines.append(f"  {name}: {count}次")
        tool_usage_summary = "\n".join(tool_lines) if tool_lines else "  (无工具调用)"

        # 编辑摘要
        edit_lines = []
        if state.edits:
            edit_sections: dict[str, int] = {}
            for edit in state.edits:
                sec = edit.get("section", "unknown")
                edit_sections[sec] = edit_sections.get(sec, 0) + 1
            for sec, count in sorted(edit_sections.items(), key=lambda x: -x[1]):
                edit_lines.append(f"  {sec}: 编辑{count}次")
        edit_summary = "\n".join(edit_lines) if edit_lines else "  (本次未进行编辑)"

        # Findings 摘要（top-5）
        findings_lines = []
        sorted_findings = sorted(
            state.findings,
            key=lambda f: {"high": 3, "medium": 2, "low": 1}.get(f.get("priority", ""), 0),
            reverse=True,
        )
        for f in sorted_findings[:5]:
            prio = f.get("priority", "?")
            finding = f.get("finding", "")[:80]
            section = f.get("section", "")
            findings_lines.append(f"  [{prio}] ({section}) {finding}")
        findings_summary = "\n".join(findings_lines) if findings_lines else "  (无 findings)"

        # 策略转换（如果有）
        strategy_summary = "  (无策略转换记录)"  # 简化处理

        # 论文类型
        paper_type = ""
        if hasattr(state, 'cognitive_hints') and state.cognitive_hints:
            paper_type = getattr(state.cognitive_hints, 'paper_type_description', '') or ""
        if not paper_type:
            paper_type = "未识别"

        base_prompt = _REFLECTION_USER_TEMPLATE.format(
            loop_turns=state.loop_turns,
            findings_count=len(state.findings),
            edits_count=len(state.edits),
            paper_type=paper_type,
            tool_usage_summary=tool_usage_summary,
            edit_summary=edit_summary,
            findings_summary=findings_summary,
            strategy_summary=strategy_summary,
        )

        # P2-fix: 如果有用户纠正，追加到 prompt 中让 Agent 反思错误
        corrections = getattr(state, 'user_corrections', [])
        if corrections:
            correction_lines = ["\n### 用户纠正（本次会话中用户指出的错误）"]
            for c in corrections[:5]:  # 最多展示 5 条
                msg = c.get("message", "")[:100]
                idx = c.get("related_finding_idx")
                if idx is not None:
                    correction_lines.append(f"  - [关于 finding {idx+1}] {msg}")
                else:
                    correction_lines.append(f"  - {msg}")
            correction_lines.append(
                "\n请特别反思：哪些审稿判断被用户否定了？为什么会犯这类错误？"
                "如果能总结出一条「在什么情况下不应该做什么」的反模式，也请输出。"
            )
            base_prompt += "\n".join(correction_lines)

        return base_prompt

    def _parse_response(self, response: str) -> list[ReflectionResult]:
        """解析 LLM 输出的 JSON。"""
        # 尝试从 response 中提取 JSON
        text = response.strip()

        # 处理 markdown code block 包装
        if "```json" in text:
            start = text.index("```json") + 7
            end = text.index("```", start) if "```" in text[start:] else len(text)
            text = text[start:end].strip()
        elif "```" in text:
            start = text.index("```") + 3
            end = text.index("```", start) if "```" in text[start:] else len(text)
            text = text[start:end].strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("SessionReflector: failed to parse JSON response")
            return []

        reflections_raw = data.get("reflections", [])
        if not isinstance(reflections_raw, list):
            return []

        results = []
        for item in reflections_raw[:5]:  # 最多 5 条
            if not isinstance(item, dict):
                continue

            category = item.get("category", "")
            if category not in self.VALID_CATEGORIES:
                # 尝试修正常见变体
                category = self._normalize_category(category)
                if not category:
                    continue

            description = item.get("description", "").strip()
            trigger = item.get("trigger_context", "").strip()
            effectiveness = item.get("effectiveness_estimate", 0.6)

            # 基本校验
            if not description or len(description) < 5:
                continue
            if not trigger or len(trigger) < 3:
                continue
            if not isinstance(effectiveness, (int, float)):
                effectiveness = 0.6
            effectiveness = max(0.5, min(1.0, float(effectiveness)))

            results.append(ReflectionResult(
                category=category,
                description=description[:150],
                trigger_context=trigger[:100],
                effectiveness_estimate=effectiveness,
                reasoning=item.get("reasoning", "")[:200],
            ))

        return results

    def _normalize_category(self, raw: str) -> str:
        """尝试修正 LLM 输出的 category 变体。"""
        raw_lower = raw.lower().strip()
        mapping = {
            "edit": "edit_strategy",
            "editing": "edit_strategy",
            "review": "review_focus",
            "strategy": "strategy_effectiveness",
            "verification": "verification_strategy",
            "tool": "tool_sequence",
            "tools": "tool_sequence",
        }
        return mapping.get(raw_lower, "")
