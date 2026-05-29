"""
core/v2/session_memory.py — Session Memory Manager

会话进行中的认知笔记本。在 Smart Compaction 恢复后，Agent 读到这份笔记
就能立即恢复到"我正在审一篇 DID 论文，方法有问题"的状态，
而不是从零重新理解所有 findings。

与 memory.py 的 SessionRecord 区别：
  - SessionRecord: 会话结束后的沉淀物，用于跨会话长期记忆
  - SessionMemoryManager: 会话进行中的实时笔记，用于 Compaction 恢复

更新机制：
  - 使用轻量 LLM 调用（~500 token prompt + ~300 token output）
  - 在"认知断点"时触发（不是每轮）
  - 认知断点: 读完一个核心 section / 新增重要 finding / 阶段转换 / 距上次 3+ 轮

设计依据:
  - UPGRADE_PLAN_FINAL.md P0-M1
  - COGNITIVE_ANCHOR §4.3: 认知辅助模式（信息呈现，不是指令）
  - 约束 C2: 状态由外部维护，LLM 不需要"记住"
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.state import WorkspaceState

logger = logging.getLogger(__name__)


@dataclass
class SessionMemory:
    """9 段结构化认知笔记。"""

    # 基本定位
    task_summary: str = ""          # "审阅一篇关于 XXX 的实证论文"
    current_focus: str = ""         # "正在检查 robustness checks 的充分性"

    # 核心认知判断（规则提取不出来的部分）
    methodology_assessment: str = ""  # "DID with staggered adoption, 未报告 pre-trends"
    evidence_quality: str = ""        # "Figure 3 的 CI 极宽，Table 2 缺 first-stage"
    novelty_judgment: str = ""        # "声称首创但 Smith(2019) 似乎已做过类似工作"

    # 累积观察
    statistical_observations: str = ""  # "SE 只 cluster 到 state 级别，可能不够"
    writing_quality: str = ""           # "Introduction 冗长，Results 缺乏解读"

    # 决策轨迹
    key_decisions: str = ""    # "决定深入检查 IV validity 因为 first-stage 很可疑"
    issue_timeline: str = ""   # "Sec2: assumption 未讨论; Sec4: F-stat 缺失..."

    def to_json(self) -> str:
        """序列化为 JSON（用于 LLM prompt）。"""
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionMemory:
        """从 dict 重建。容忍多余字段。"""
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)

    def is_empty(self) -> bool:
        """是否还没有任何内容。"""
        return not any([
            self.task_summary, self.current_focus,
            self.methodology_assessment, self.evidence_quality,
            self.novelty_judgment, self.statistical_observations,
            self.writing_quality, self.key_decisions, self.issue_timeline,
        ])


# LLM 调用的 prompt 模板
_UPDATE_PROMPT = """You are maintaining review notes for an academic paper reviewer.
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

Return ONLY valid JSON matching this schema (no markdown, no explanation):
{schema}"""

_SCHEMA = """{
  "task_summary": "string",
  "current_focus": "string",
  "methodology_assessment": "string",
  "evidence_quality": "string",
  "novelty_judgment": "string",
  "statistical_observations": "string",
  "writing_quality": "string",
  "key_decisions": "string",
  "issue_timeline": "string"
}"""


class SessionMemoryManager:
    """
    管理 Session Memory 的更新和注入。

    职责:
    1. 判断是否到了"认知断点"（should_update）
    2. 调用 LLM 更新笔记（update）
    3. 格式化为 Compaction 恢复文本（format_for_restoration）
    """

    def __init__(self, llm_call_fn: Any = None) -> None:
        """
        Args:
            llm_call_fn: 异步 LLM 调用函数。签名:
                async (prompt: str, max_tokens: int) -> str
                如果为 None，update() 将跳过（用于测试）。
        """
        self._llm_call_fn = llm_call_fn
        self._memory = SessionMemory()
        self._update_count: int = 0
        self._last_update_round: int = 0
        self._last_findings_count: int = 0

    @property
    def memory(self) -> SessionMemory:
        """当前笔记内容（只读访问）。"""
        return self._memory

    @property
    def update_count(self) -> int:
        """已更新次数。"""
        return self._update_count

    def should_update(self, state: WorkspaceState) -> bool:
        """
        判断是否到了认知断点。

        触发条件（OR 逻辑）:
        1. 刚读完一个核心 section（sections_read 增长）
        2. 新增了重要 finding（priority=high 的 findings 数增长 ≥ 2）
        3. 距上次更新已过 3+ 轮（兜底）
        """
        rounds_since = state.loop_turns - self._last_update_round

        # 条件 1: sections_read 增长（代理"读完核心 section"信号）
        # 每读 2 个新 section 就触发一次
        section_growth = len(state.sections_read) - (
            self._last_update_round  # 粗略近似：上次更新时的进度
            if self._last_update_round > 0 else 0
        )
        just_read_sections = section_growth >= 2 and rounds_since >= 2

        # 条件 2: 新增重要 findings
        current_findings = len(state.findings)
        new_findings = current_findings - self._last_findings_count
        has_new_major = new_findings >= 2

        # 条件 3: 兜底——距上次更新 3+ 轮
        time_based = rounds_since >= 3 and current_findings > 0

        return just_read_sections or has_new_major or time_based

    async def update(
        self,
        state: WorkspaceState,
        recent_activity: str,
        new_findings: list[dict],
    ) -> SessionMemory:
        """
        调用 LLM 更新 Session Memory。

        Args:
            state: 当前工作状态
            recent_activity: 最近几轮的工具调用摘要
            new_findings: 新增的 findings 列表

        Returns:
            更新后的 SessionMemory
        """
        if self._llm_call_fn is None:
            logger.warning("SessionMemoryManager: no LLM function, skipping update")
            return self._memory

        # 构建 prompt
        findings_text = "\n".join(
            f"- [{f.get('priority', '?')}] {f.get('finding', '')[:100]}"
            for f in new_findings
        ) if new_findings else "(无新 findings)"

        prompt = _UPDATE_PROMPT.format(
            current_memory=self._memory.to_json(),
            recent_activity=recent_activity,
            new_findings=findings_text,
            schema=_SCHEMA,
        )

        try:
            response = await self._llm_call_fn(prompt, max_tokens=400)
            parsed = self._parse_response(response)
            if parsed:
                self._memory = parsed
        except Exception as e:
            logger.warning("SessionMemory update failed: %s", e)

        self._update_count += 1
        self._last_update_round = state.loop_turns
        self._last_findings_count = len(state.findings)
        return self._memory

    def update_sync(
        self,
        state: WorkspaceState,
        recent_activity: str,
        new_findings: list[dict],
    ) -> SessionMemory:
        """
        同步版本的 update（用于非异步上下文）。

        直接通过规则提取更新（不调用 LLM），作为 fallback。
        """
        # 基于规则的简单更新
        if not self._memory.task_summary and state.paper_sections:
            section_names = [k for k in state.paper_sections if k != "full"]
            self._memory.task_summary = (
                f"审阅一篇含 {len(section_names)} sections 的论文"
            )

        if new_findings:
            timeline_entries = []
            for f in new_findings:
                sec = f.get("section", "?")
                text = f.get("finding", "")[:60]
                timeline_entries.append(f"{sec}: {text}")
            if timeline_entries:
                existing = self._memory.issue_timeline
                new_entry = "; ".join(timeline_entries)
                self._memory.issue_timeline = (
                    f"{existing}; {new_entry}" if existing else new_entry
                )

        if recent_activity:
            self._memory.current_focus = recent_activity[:120]

        self._update_count += 1
        self._last_update_round = state.loop_turns
        self._last_findings_count = len(state.findings)
        return self._memory

    def format_for_restoration(self) -> str:
        """
        格式化为 Smart Compaction 恢复时的注入文本。

        遵循 COGNITIVE_ANCHOR §4.3: 信息呈现，不是指令。
        Agent 读到这份笔记后可以修正判断，但不应遗忘。
        """
        m = self._memory
        if m.is_empty():
            return ""

        parts = ["[审稿认知笔记 — 你在压缩前的判断状态]"]

        if m.task_summary:
            parts.append(f"任务: {m.task_summary}")
        if m.current_focus:
            parts.append(f"当前关注: {m.current_focus}")

        parts.append("")  # 空行分隔

        if m.methodology_assessment:
            parts.append(f"方法论判断: {m.methodology_assessment}")
        if m.evidence_quality:
            parts.append(f"证据质量: {m.evidence_quality}")
        if m.novelty_judgment:
            parts.append(f"创新性判断: {m.novelty_judgment}")
        if m.statistical_observations:
            parts.append(f"统计问题: {m.statistical_observations}")
        if m.writing_quality:
            parts.append(f"写作质量: {m.writing_quality}")

        parts.append("")  # 空行分隔

        if m.key_decisions:
            parts.append(f"关键决策: {m.key_decisions}")
        if m.issue_timeline:
            parts.append(f"问题时间线: {m.issue_timeline}")

        parts.append("")
        parts.append("[注意: 以上是你之前的判断，你可以修正它们，但不要遗忘。]")

        return "\n".join(parts)

    def _parse_response(self, response: str) -> SessionMemory | None:
        """尝试从 LLM 响应中解析 JSON。"""
        # 去掉可能的 markdown 包裹
        text = response.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            # 去掉首尾 ``` 行
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines)

        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return SessionMemory.from_dict(data)
        except json.JSONDecodeError:
            logger.warning("SessionMemory: failed to parse LLM response as JSON")
        return None
