"""
core/reflection_engine.py — 三层反思引擎 (Phase 6)

从现有的 session 级反思（reflection.py）扩展为 micro/phase/global 三层反思系统。

三层架构：
  Micro Reflection（即时反思）：
    - 触发时机：每次工具调用后，如果结果出乎意料
    - 极轻量，不调用 LLM（规则判断为主）
    - 产出：立即修正当前行为的建议

  Phase Reflection（阶段反思）：
    - 触发时机：Phase 结束/切换时
    - 中等重量，可选 LLM 深度反思
    - 产出：是否需要回退重做、覆盖率缺口、深度不足

  Global Reflection（全局反思）：
    - 触发时机：整个审稿完成时
    - 重量级 LLM 调用
    - 产出：自评分数、遗漏要点、改进建议 → 写入 Procedural Memory

与现有 reflection.py 的关系：
  - reflection.py 的 SessionReflector 专注于 session 结束时的经验提炼
  - 本文件的 ReflectionEngine 覆盖整个审稿过程中的实时反思
  - 两者互补：ReflectionEngine.global_reflect() 产出的结论
    可以作为 SessionReflector 的额外输入

设计原则（COGNITIVE_ANCHOR §4.3 约束-而非-控制）：
  - 反思结果是"信息呈现"给 Agent，不是"命令"
  - Agent 看到反思结论后自主决定是否调整行为
  - 避免虚假反思（"我做得很好"但实际不好）——通过具体 evidence 验证
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Protocol, runtime_checkable
import time

logger = logging.getLogger(__name__)


# ==============================================================
# 反思结果数据类型
# ==============================================================

class ReflectionLevel(Enum):
    """反思层级"""
    MICRO = "micro"
    PHASE = "phase"
    GLOBAL = "global"


class MicroVerdict(Enum):
    """Micro Reflection 的快速判定"""
    PASS = "pass"                  # 一切正常
    UNEXPECTED = "unexpected"     # 结果出乎意料，但不严重
    ANOMALY = "anomaly"           # 检测到异常，建议调整
    FAILURE = "failure"           # 明确失败，需要恢复


@dataclass
class MicroReflection:
    """即时反思的结果"""
    verdict: MicroVerdict
    observation: str = ""          # 观察到了什么
    suggestion: str = ""           # 建议的调整
    confidence: float = 0.8        # 判断置信度
    turn: int = 0

    # 快捷构造
    PASS: "MicroReflection" = None  # type: ignore  # 在模块加载后赋值

    @classmethod
    def passed(cls) -> "MicroReflection":
        return cls(verdict=MicroVerdict.PASS)


# Module-level convenience constant
MicroReflection.PASS = MicroReflection(verdict=MicroVerdict.PASS)


@dataclass
class PhaseReflection:
    """阶段反思的结果"""
    phase_name: str
    coverage_score: float = 0.0       # 覆盖率评估 (0-1)
    depth_score: float = 0.0          # 分析深度评估 (0-1)
    evidence_quality: float = 0.0     # 证据质量评估 (0-1)
    gaps_identified: list[str] = field(default_factory=list)     # 发现的覆盖缺口
    should_revisit: bool = False      # 是否建议回退重做
    revisit_reason: str = ""          # 回退原因
    improvements: list[str] = field(default_factory=list)        # 如果重做会做什么不同
    turn: int = 0

    @property
    def overall_score(self) -> float:
        """综合质量分数"""
        return (self.coverage_score + self.depth_score + self.evidence_quality) / 3


@dataclass
class GlobalReflection:
    """全局反思的结果"""
    self_score: float = 0.0           # 自评分数 (0-10)
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    missed_points: list[str] = field(default_factory=list)       # 可能遗漏的要点
    improvement_suggestions: list[str] = field(default_factory=list)
    procedural_learnings: list[dict] = field(default_factory=list)  # 可写入 Procedural Memory 的经验
    confidence_in_review: float = 0.0  # 对整体审稿质量的信心 (0-1)


# ==============================================================
# LLM 反思协议
# ==============================================================

@runtime_checkable
class ReflectionLLM(Protocol):
    """反思引擎依赖的 LLM 接口。"""
    async def reflect(self, prompt: str, context: str = "") -> str:
        """调用 LLM 进行反思性思考。"""
        ...


# ==============================================================
# Micro Reflection 实现
# ==============================================================

class MicroReflector:
    """即时反思：每次工具调用后的快速评估。

    设计：极轻量，不调用 LLM。
    基于规则检测"结果是否符合预期"。
    """

    def __init__(self):
        self._expectations: dict[str, _ToolExpectation] = {}
        self._anomaly_count: int = 0
        self._max_anomalies_before_alert: int = 3

    def reflect(
        self,
        tool_name: str,
        tool_params: dict,
        tool_result: Any,
        success: bool,
        turn: int = 0,
    ) -> MicroReflection:
        """对一次工具调用进行即时反思。

        快速判定：
        1. 工具是否成功？
        2. 结果是否为空/异常短？
        3. 是否与预期不符（如搜索无结果、读 section 返回空）？
        """
        # 失败 → 直接标记
        if not success:
            self._anomaly_count += 1
            return MicroReflection(
                verdict=MicroVerdict.FAILURE,
                observation=f"`{tool_name}` 执行失败",
                suggestion=self._suggest_on_failure(tool_name, tool_params),
                turn=turn,
            )

        # 空结果检测（使用 is None 避免 0/False/[] 等 falsy 值被误判）
        result_str = str(tool_result) if tool_result is not None else ""
        if not result_str or result_str.strip() in ("", "None", "null", "[]", "{}"):
            self._anomaly_count += 1
            return MicroReflection(
                verdict=MicroVerdict.UNEXPECTED,
                observation=f"`{tool_name}` 返回空结果",
                suggestion=self._suggest_on_empty(tool_name, tool_params),
                turn=turn,
            )

        # 连续异常累积
        if self._anomaly_count >= self._max_anomalies_before_alert:
            self._anomaly_count = 0
            return MicroReflection(
                verdict=MicroVerdict.ANOMALY,
                observation=f"连续 {self._max_anomalies_before_alert} 次异常结果",
                suggestion="建议暂停当前方向，用 reflect_and_plan 重新评估策略。",
                turn=turn,
            )

        # 正常
        self._anomaly_count = 0
        return MicroReflection.passed()

    def _suggest_on_failure(self, tool_name: str, params: dict) -> str:
        """工具失败时的建议。"""
        suggestions = {
            "read_section": "确认 section 名称是否正确（检查 paper 结构）",
            "search_literature": "尝试换关键词或缩小搜索范围",
            "edit_section": "确认目标 section 存在且有写入权限",
        }
        return suggestions.get(tool_name, "检查参数是否正确，或尝试替代方法")

    def _suggest_on_empty(self, tool_name: str, params: dict) -> str:
        """空结果时的建议。"""
        suggestions = {
            "read_section": "该 section 可能不存在，检查论文结构",
            "search_literature": "关键词可能过于具体，尝试更宽泛的术语",
            "update_findings": "确认 finding 内容非空",
        }
        return suggestions.get(tool_name, "结果为空，可能需要调整方法")


# ==============================================================
# Phase Reflection 实现
# ==============================================================

class PhaseReflector:
    """阶段反思：Phase 结束时的结构化评估。

    两种模式：
    1. 规则模式（默认）：基于统计指标的快速评估
    2. LLM 模式（可选）：深度反思（token 开销较大）
    """

    def __init__(self, llm: ReflectionLLM | None = None):
        self._llm = llm
        # Phase 期望指标（基于历史数据动态调整）
        self._phase_expectations: dict[str, _PhaseExpectation] = {
            "initial_read": _PhaseExpectation(min_findings=0, min_sections_read=3),
            "deep_analysis": _PhaseExpectation(min_findings=2, min_sections_read=5),
            "methodology_analysis": _PhaseExpectation(min_findings=1, min_sections_read=2),
            "overall_assessment": _PhaseExpectation(min_findings=3, min_sections_read=0),
        }

    async def reflect(
        self,
        phase_name: str,
        findings_in_phase: list[dict],
        sections_read_in_phase: list[str],
        tool_calls_in_phase: list[dict],
        total_turns_in_phase: int,
        turn: int = 0,
    ) -> PhaseReflection:
        """对完成的 Phase 进行反思评估。"""
        # 规则评估
        reflection = self._rule_based_reflect(
            phase_name, findings_in_phase, sections_read_in_phase,
            tool_calls_in_phase, total_turns_in_phase, turn
        )

        # 如果有 LLM 且规则评估发现问题，进行深度反思
        if self._llm and reflection.overall_score < 0.6:
            try:
                deep = await self._llm_reflect(
                    phase_name, findings_in_phase,
                    sections_read_in_phase, tool_calls_in_phase
                )
                # 合并 LLM 反思结果
                reflection.gaps_identified.extend(deep.get("gaps", []))
                reflection.improvements.extend(deep.get("improvements", []))
                if deep.get("should_revisit"):
                    reflection.should_revisit = True
                    reflection.revisit_reason = deep.get("reason", "")
            except Exception as e:
                logger.warning("Phase LLM reflection failed, using rule-based result: %s", e)

        return reflection

    def _rule_based_reflect(
        self,
        phase_name: str,
        findings: list[dict],
        sections_read: list[str],
        tool_calls: list[dict],
        total_turns: int,
        turn: int,
    ) -> PhaseReflection:
        """基于规则的快速反思。"""
        expectation = self._phase_expectations.get(
            phase_name,
            _PhaseExpectation(min_findings=1, min_sections_read=2)
        )

        # 覆盖率
        coverage = min(1.0, len(sections_read) / max(1, expectation.min_sections_read))

        # 深度（通过 findings 数量和质量判断）
        depth = min(1.0, len(findings) / max(1, expectation.min_findings))

        # 证据质量（verified findings 占比）
        verified = sum(1 for f in findings if f.get("status") == "verified")
        evidence_quality = verified / max(1, len(findings)) if findings else 0.5

        # 识别缺口
        gaps = []
        if len(findings) < expectation.min_findings:
            gaps.append(
                f"产出 findings ({len(findings)}) 少于预期 ({expectation.min_findings})"
            )
        if len(sections_read) < expectation.min_sections_read:
            gaps.append(
                f"阅读 sections ({len(sections_read)}) 少于预期 ({expectation.min_sections_read})"
            )

        # 效率检查：如果 turns 很多但 findings 很少
        efficiency = len(findings) / max(1, total_turns)
        if total_turns > 5 and efficiency < 0.2:
            gaps.append(f"效率偏低：{total_turns} 轮只产出 {len(findings)} 条 findings")

        # 是否建议回退
        should_revisit = coverage < 0.4 and depth < 0.4
        revisit_reason = ""
        if should_revisit:
            revisit_reason = "覆盖率和深度均严重不足，建议重新审视"

        return PhaseReflection(
            phase_name=phase_name,
            coverage_score=coverage,
            depth_score=depth,
            evidence_quality=evidence_quality,
            gaps_identified=gaps,
            should_revisit=should_revisit,
            revisit_reason=revisit_reason,
            turn=turn,
        )

    async def _llm_reflect(
        self, phase_name: str, findings: list[dict],
        sections: list[str], tool_calls: list[dict]
    ) -> dict:
        """使用 LLM 进行深度反思（仅在规则评估发现问题时调用）。"""
        findings_text = "\n".join(
            f"- [{f.get('category', '?')}] {f.get('finding', '')[:100]}"
            for f in findings[:10]
        )
        prompt = (
            f"你刚完成了论文审稿的 `{phase_name}` 阶段。回顾以下产出：\n\n"
            f"阅读的 sections: {', '.join(sections[:10])}\n"
            f"产出的 findings:\n{findings_text}\n\n"
            f"请评估：\n"
            f"1. 是否有明显遗漏的分析维度？（gaps）\n"
            f"2. 如果重新做，你会做什么不同？（improvements）\n"
            f"3. 是否需要回退重做此阶段？（should_revisit + reason）\n\n"
            f"以 JSON 格式回答：{{'gaps': [...], 'improvements': [...], "
            f"'should_revisit': bool, 'reason': str}}"
        )
        import json
        response = await self._llm.reflect(prompt)
        try:
            return json.loads(response)
        except (json.JSONDecodeError, TypeError):
            return {"gaps": [], "improvements": [], "should_revisit": False, "reason": ""}


# ==============================================================
# Global Reflection 实现
# ==============================================================

class GlobalReflector:
    """全局反思：整个审稿完成后的综合评估。

    产出写入 Procedural Memory 的经验学习。
    """

    def __init__(self, llm: ReflectionLLM | None = None):
        self._llm = llm

    async def reflect(
        self,
        findings: list[dict],
        edits: list[dict],
        sections_read: list[str],
        tool_call_history: list[dict],
        loop_turns: int,
        total_tokens: int,
    ) -> GlobalReflection:
        """对整个审稿过程进行全局反思。"""
        # 如果有 LLM，进行深度全局反思
        if self._llm:
            try:
                return await self._llm_global_reflect(
                    findings, edits, sections_read, tool_call_history,
                    loop_turns, total_tokens
                )
            except Exception as e:
                logger.warning("Global LLM reflection failed, falling back to rules: %s", e)

        # 规则模式 fallback
        return self._rule_global_reflect(
            findings, edits, sections_read, tool_call_history,
            loop_turns, total_tokens
        )

    def _rule_global_reflect(
        self,
        findings: list[dict],
        edits: list[dict],
        sections_read: list[str],
        tool_calls: list[dict],
        loop_turns: int,
        total_tokens: int,
    ) -> GlobalReflection:
        """基于规则的全局反思。"""
        # 自评分
        findings_score = min(3.0, len(findings) * 0.5)
        depth_score = min(3.0, sum(
            1 for f in findings if f.get("status") == "verified"
        ) * 0.8)
        efficiency_score = min(2.0, len(findings) / max(1, loop_turns) * 10)
        coverage_score = min(2.0, len(sections_read) * 0.3)
        self_score = findings_score + depth_score + efficiency_score + coverage_score

        # 弱点识别
        weaknesses = []
        if len(findings) < 3:
            weaknesses.append("产出 findings 数量偏少")

        verified_ratio = sum(1 for f in findings if f.get("status") == "verified") / max(1, len(findings))
        if verified_ratio < 0.5:
            weaknesses.append("大量 findings 未经验证")

        tool_success = sum(1 for t in tool_calls if t.get("success", True)) / max(1, len(tool_calls))
        if tool_success < 0.8:
            weaknesses.append(f"工具调用成功率偏低 ({tool_success:.0%})")

        # 强项
        strengths = []
        if verified_ratio > 0.8:
            strengths.append("大部分发现有充分的证据支撑")
        if len(sections_read) > 8:
            strengths.append("论文覆盖面广")

        return GlobalReflection(
            self_score=min(10.0, self_score),
            strengths=strengths,
            weaknesses=weaknesses,
            missed_points=[],
            improvement_suggestions=[],
            confidence_in_review=min(1.0, self_score / 10),
        )

    async def _llm_global_reflect(
        self,
        findings: list[dict],
        edits: list[dict],
        sections_read: list[str],
        tool_calls: list[dict],
        loop_turns: int,
        total_tokens: int,
    ) -> GlobalReflection:
        """LLM 驱动的深度全局反思。"""
        findings_summary = "\n".join(
            f"- [{f.get('category', '?')}|{f.get('priority', '?')}] {f.get('finding', '')[:120]}"
            for f in findings[:15]
        )
        prompt = (
            "你刚完成了一篇学术论文的完整审稿。请进行深度自我反思。\n\n"
            f"## 审稿统计\n"
            f"- 总轮数: {loop_turns}\n"
            f"- 阅读 sections: {len(sections_read)} 个\n"
            f"- 产出 findings: {len(findings)} 条\n"
            f"- 编辑操作: {len(edits)} 次\n"
            f"- Token 消耗: {total_tokens}\n\n"
            f"## Findings 摘要\n{findings_summary}\n\n"
            f"## 请评估\n"
            f"1. 给自己打分 (0-10)\n"
            f"2. 做得好的方面\n"
            f"3. 需要改进的方面\n"
            f"4. 可能遗漏的要点\n"
            f"5. 如果从头再来会做什么不同\n"
            f"6. 可以固化为未来经验的学习\n\n"
            f"以 JSON 格式回答。"
        )

        import json
        response = await self._llm.reflect(prompt)
        try:
            data = json.loads(response)
            return GlobalReflection(
                self_score=float(data.get("score", 5.0)),
                strengths=data.get("strengths", []),
                weaknesses=data.get("weaknesses", []),
                missed_points=data.get("missed_points", []),
                improvement_suggestions=data.get("improvements", []),
                procedural_learnings=data.get("learnings", []),
                confidence_in_review=float(data.get("confidence", 0.5)),
            )
        except (json.JSONDecodeError, TypeError, ValueError):
            # 解析失败 → 降级到规则模式
            return self._rule_global_reflect(
                findings, edits, sections_read, tool_calls,
                loop_turns, total_tokens
            )


# ==============================================================
# 统一入口：ReflectionEngine
# ==============================================================

class ReflectionEngine:
    """三层反思系统的统一入口。

    使用方式：
        engine = ReflectionEngine(llm=my_llm)

        # 每次工具调用后
        micro = engine.micro_reflect(tool_name, params, result, success)
        if micro.verdict != MicroVerdict.PASS:
            # 将建议注入对话

        # Phase 结束时
        phase_ref = await engine.phase_reflect("methodology_analysis", findings, ...)
        if phase_ref.should_revisit:
            # 考虑回退

        # 审稿完成时
        global_ref = await engine.global_reflect(findings, edits, ...)
        # 将 procedural_learnings 写入 Memory
    """

    def __init__(self, llm: ReflectionLLM | None = None):
        self.micro = MicroReflector()
        self.phase = PhaseReflector(llm=llm)
        self.global_ = GlobalReflector(llm=llm)
        self._reflection_history: list[dict] = []

    def micro_reflect(
        self,
        tool_name: str,
        tool_params: dict,
        tool_result: Any,
        success: bool,
        turn: int = 0,
    ) -> MicroReflection:
        """即时反思（同步，不调用 LLM）。"""
        result = self.micro.reflect(tool_name, tool_params, tool_result, success, turn)
        if result.verdict != MicroVerdict.PASS:
            self._reflection_history.append({
                "level": "micro",
                "verdict": result.verdict.value,
                "observation": result.observation,
                "turn": turn,
                "timestamp": time.time(),
            })
        return result

    async def phase_reflect(
        self,
        phase_name: str,
        findings_in_phase: list[dict],
        sections_read_in_phase: list[str],
        tool_calls_in_phase: list[dict],
        total_turns_in_phase: int,
        turn: int = 0,
    ) -> PhaseReflection:
        """阶段反思。"""
        result = await self.phase.reflect(
            phase_name, findings_in_phase, sections_read_in_phase,
            tool_calls_in_phase, total_turns_in_phase, turn
        )
        self._reflection_history.append({
            "level": "phase",
            "phase": phase_name,
            "overall_score": result.overall_score,
            "should_revisit": result.should_revisit,
            "turn": turn,
            "timestamp": time.time(),
        })
        return result

    async def global_reflect(
        self,
        findings: list[dict],
        edits: list[dict],
        sections_read: list[str],
        tool_call_history: list[dict],
        loop_turns: int,
        total_tokens: int,
    ) -> GlobalReflection:
        """全局反思。"""
        result = await self.global_.reflect(
            findings, edits, sections_read, tool_call_history,
            loop_turns, total_tokens
        )
        self._reflection_history.append({
            "level": "global",
            "self_score": result.self_score,
            "confidence": result.confidence_in_review,
            "timestamp": time.time(),
        })
        return result

    @property
    def reflection_history(self) -> list[dict]:
        """获取反思历史记录。"""
        return self._reflection_history

    def get_micro_anomaly_rate(self, last_n: int = 20) -> float:
        """最近 N 次 micro reflection 的异常率。"""
        micros = [r for r in self._reflection_history if r["level"] == "micro"][-last_n:]
        if not micros:
            return 0.0
        anomalies = sum(1 for r in micros if r["verdict"] != "pass")
        return anomalies / len(micros)


# ==============================================================
# 内部辅助类型
# ==============================================================

@dataclass
class _PhaseExpectation:
    """Phase 的预期指标（用于规则评估）"""
    min_findings: int = 1
    min_sections_read: int = 2


@dataclass
class _ToolExpectation:
    """工具调用的预期（用于 micro reflection）"""
    expected_success: bool = True
    expected_non_empty: bool = True
