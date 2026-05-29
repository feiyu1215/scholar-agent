"""
core/skill_synthesis.py — Phase 4: Test-Time Skill Synthesis (SkillTTA)

当 Skill 执行失败或反思系统检测到反复差距时，运行时动态合成修复性 Skill。
这是 ScholarAgent 从"静态技能库"进化为"能自我修补弱点"的关键环节。

核心组件:
    1. FailureType — 失败分类枚举
    2. FailureContext — 失败上下文的完整快照
    3. RootCauseAnalyzer — 从失败轨迹中分析根因
    4. SynthesizedSkill — 运行时合成的参数化 Skill（不是动态代码，而是配置驱动）
    5. SkillSynthesizer — 合成引擎：分析根因 → 检索历史修复 → 生成候选 → 沙箱验证
    6. SynthesisLifecycleManager — 合成 Skill 的生命周期管理（置信度、降级、清理）
    7. SkillSynthesisOrchestrator — 编排器：实现 SkillSynthesisReceiver Protocol 并协调各组件

设计原则:
    - Kill Switch: SCHOLAR_GODEL_SKILL_SYNTHESIS (默认 ON)
    - 安全性: 不执行动态代码——合成的 Skill 是参数化配置，不是生成的 Python
    - 渐进退化: 合成失败不影响核心审稿流程
    - 置信度标记: 合成的 Skill 标为 "experimental"，需要验证才升级为 "validated"
    - 序列化: 所有状态可持久化
    - 与 Phase 3 集成: 合成 Skill 兼容 SkillX 体系 (Skill ABC)
    - 与 Phase 6 集成: 实现 SkillSynthesisReceiver Protocol 接收 SynthesisSignal

审稿场景支撑 (C18):
    - 场景 1: DID 分析 Skill 对某论文的"平行趋势讨论"判断失败 → 分析根因为"作者用了
      非标准措辞" → 合成一个增强的 DID 检查 Skill，加入更多措辞模式。
    - 场景 2: 统计验证 Skill 反复在"Weak Instruments"类论文上漏报 → 反思触发合成信号
      → 合成专门针对弱工具变量的 Skill。
    - 场景 3: 引文格式检查 Skill 对"非标准引用风格"（如经济学 working paper）误判 →
      合成容错更高的引文格式 Skill。
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional, Protocol, runtime_checkable

from core.skills.base import (
    Finding,
    Skill,
    SkillContext,
    SkillDescriptor,
    SkillLevel,
    SkillResult,
)

logger = logging.getLogger(__name__)


# ==============================================================
# Kill Switch
# ==============================================================

def _is_enabled() -> bool:
    """检查 Phase 4 功能开关。"""
    val = os.environ.get("SCHOLAR_GODEL_SKILL_SYNTHESIS", "1").strip().lower()
    return val in ("1", "true", "yes")


SKILL_SYNTHESIS_ENABLED: bool = _is_enabled()


# ==============================================================
# 1. 失败分类
# ==============================================================

class FailureType(Enum):
    """Skill 执行失败的类型分类。

    分类依据：失败发生在 Skill 生命周期的哪个阶段，以及失败的性质。
    """
    TOOL_ERROR = "tool_error"
    """工具本身报错（API 超时、格式解析异常、依赖缺失）。"""

    WRONG_TOOL = "wrong_tool"
    """选错了 Skill（适用度判断有误，apply score 高但实际不适用）。"""

    INSUFFICIENT_INFO = "insufficient_info"
    """信息不足以完成分析（论文缺少必要段落、表格不完整）。"""

    LOGIC_ERROR = "logic_error"
    """推理逻辑错误（Skill 产出了错误的 Finding，被后续验证否定）。"""

    FORMAT_MISMATCH = "format_mismatch"
    """输出格式不符合预期（Finding 缺少必要字段、evidence 为空）。"""

    TIMEOUT = "timeout"
    """超时未完成（执行时间超过 budget）。"""

    LOW_QUALITY = "low_quality"
    """执行成功但产出质量低（Findings 全部是低置信度或被 discard）。"""

    MISSED_ISSUE = "missed_issue"
    """遗漏了应该发现的问题（由人工反馈或后续 Skill 发现遗漏）。"""


# ==============================================================
# 2. 失败上下文
# ==============================================================

@dataclass
class FailureContext:
    """Skill 失败时的完整上下文快照。

    用于根因分析和合成修复——必须包含"重现失败"所需的全部信息。
    """
    # 失败的 Skill 信息
    skill_name: str
    skill_version: str = "1.0"
    skill_level: str = ""

    # 失败分类
    failure_type: FailureType = FailureType.TOOL_ERROR
    error_message: str = ""

    # 执行上下文快照
    paper_text_snippet: str = ""   # 触发失败的论文片段（截取，避免过长）
    paper_metadata: dict = field(default_factory=dict)
    current_phase: str = ""
    current_section: str = ""

    # 执行结果
    actual_result: dict = field(default_factory=dict)   # 实际输出
    expected_behavior: str = ""                          # 期望行为的描述

    # 时间和会话
    timestamp: float = field(default_factory=time.time)
    session_id: str = ""

    # 相似失败的历史引用
    similar_failure_ids: list[str] = field(default_factory=list)

    @property
    def failure_id(self) -> str:
        """失败记录的唯一标识。"""
        content = f"{self.skill_name}:{self.failure_type.value}:{self.timestamp}"
        return hashlib.md5(content.encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        """序列化为字典。"""
        return {
            "failure_id": self.failure_id,
            "skill_name": self.skill_name,
            "skill_version": self.skill_version,
            "skill_level": self.skill_level,
            "failure_type": self.failure_type.value,
            "error_message": self.error_message,
            "paper_text_snippet": self.paper_text_snippet[:500],  # 截断
            "paper_metadata": self.paper_metadata,
            "current_phase": self.current_phase,
            "current_section": self.current_section,
            "actual_result": self.actual_result,
            "expected_behavior": self.expected_behavior,
            "timestamp": self.timestamp,
            "session_id": self.session_id,
            "similar_failure_ids": self.similar_failure_ids,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FailureContext":
        """从字典反序列化。"""
        ft_str = data.get("failure_type", "tool_error")
        try:
            failure_type = FailureType(ft_str)
        except ValueError:
            failure_type = FailureType.TOOL_ERROR

        return cls(
            skill_name=data.get("skill_name", ""),
            skill_version=data.get("skill_version", "1.0"),
            skill_level=data.get("skill_level", ""),
            failure_type=failure_type,
            error_message=data.get("error_message", ""),
            paper_text_snippet=data.get("paper_text_snippet", ""),
            paper_metadata=data.get("paper_metadata", {}),
            current_phase=data.get("current_phase", ""),
            current_section=data.get("current_section", ""),
            actual_result=data.get("actual_result", {}),
            expected_behavior=data.get("expected_behavior", ""),
            timestamp=data.get("timestamp", 0.0),
            session_id=data.get("session_id", ""),
            similar_failure_ids=data.get("similar_failure_ids", []),
        )


# ==============================================================
# 3. 根因分析
# ==============================================================

@dataclass
class RootCause:
    """根因分析结果。"""
    cause_type: str           # 根因类型 (pattern_gap / coverage_gap / threshold_issue / ...)
    description: str          # 人类可读的根因描述
    confidence: float = 0.0   # 分析置信度 (0-1)
    suggested_fix: str = ""   # 建议的修复方向
    evidence: list[str] = field(default_factory=list)  # 支撑证据


class RootCauseAnalyzer:
    """从失败上下文中推断根因。

    使用规则引擎（不依赖 LLM），基于失败类型和上下文特征进行启发式分析。
    """

    # 失败类型 → 典型根因的映射规则
    _HEURISTIC_RULES: dict[FailureType, list[dict]] = {
        FailureType.WRONG_TOOL: [
            {
                "condition": lambda ctx: "methodology" in ctx.current_section.lower(),
                "cause_type": "misclassification",
                "description": "Skill 的 can_apply() 对当前方法论类型的识别不准确",
                "suggested_fix": "增加方法论关键词模式匹配",
            },
        ],
        FailureType.MISSED_ISSUE: [
            {
                "condition": lambda ctx: any(
                    kw in ctx.paper_text_snippet.lower()
                    for kw in ["however", "although", "but", "limitation"]
                ),
                "cause_type": "pattern_gap",
                "description": "论文使用了非标准或委婉表述来描述问题/限制",
                "suggested_fix": "扩展检测模式，增加委婉表述和领域特定措辞",
            },
            {
                "condition": lambda _: True,  # 兜底
                "cause_type": "coverage_gap",
                "description": "Skill 的检查规则未覆盖此类问题",
                "suggested_fix": "增加针对该类问题的检查规则",
            },
        ],
        FailureType.LOGIC_ERROR: [
            {
                "condition": lambda ctx: "significant" in ctx.paper_text_snippet.lower(),
                "cause_type": "threshold_issue",
                "description": "统计显著性判断阈值可能不适合当前论文的标准",
                "suggested_fix": "使用论文自述的显著性水平而非硬编码阈值",
            },
            {
                "condition": lambda _: True,
                "cause_type": "inference_error",
                "description": "推理链中存在逻辑跳跃或错误假设",
                "suggested_fix": "增加中间验证步骤，将复杂推理拆分为更小的验证单元",
            },
        ],
        FailureType.INSUFFICIENT_INFO: [
            {
                "condition": lambda _: True,
                "cause_type": "data_dependency",
                "description": "Skill 依赖的输入信息在当前上下文中不完整",
                "suggested_fix": "增加 graceful degradation 路径，在信息不完整时给出部分分析",
            },
        ],
        FailureType.LOW_QUALITY: [
            {
                "condition": lambda ctx: ctx.paper_metadata.get("methodology_type") in (
                    "did", "iv", "rdd", "event_study"
                ),
                "cause_type": "domain_specificity_gap",
                "description": "Skill 的通用检查规则对经济学特定方法论不够深入",
                "suggested_fix": "增加经济学方法论特化的检查规则",
            },
            {
                "condition": lambda _: True,
                "cause_type": "calibration_issue",
                "description": "Skill 的 Finding 置信度校准不准确",
                "suggested_fix": "调整置信度计算逻辑，基于证据强度动态设置",
            },
        ],
        FailureType.FORMAT_MISMATCH: [
            {
                "condition": lambda _: True,
                "cause_type": "output_schema_drift",
                "description": "输出格式不符合下游预期",
                "suggested_fix": "增加输出格式验证和规范化后处理",
            },
        ],
        FailureType.TIMEOUT: [
            {
                "condition": lambda _: True,
                "cause_type": "complexity_underestimation",
                "description": "Skill 低估了处理复杂度，未在预算内完成",
                "suggested_fix": "增加早期退出策略和复杂度预估",
            },
        ],
        FailureType.TOOL_ERROR: [
            {
                "condition": lambda _: True,
                "cause_type": "dependency_failure",
                "description": "外部依赖或工具调用失败",
                "suggested_fix": "增加重试机制和备选工具路径",
            },
        ],
    }

    def analyze(self, failure: FailureContext) -> RootCause:
        """分析失败根因。

        Args:
            failure: 失败上下文

        Returns:
            根因分析结果
        """
        rules = self._HEURISTIC_RULES.get(failure.failure_type, [])

        for rule in rules:
            condition_fn = rule.get("condition", lambda _: True)
            try:
                if condition_fn(failure):
                    return RootCause(
                        cause_type=rule["cause_type"],
                        description=rule["description"],
                        confidence=0.7,  # 规则匹配的基线置信度
                        suggested_fix=rule.get("suggested_fix", ""),
                        evidence=[
                            f"Failure type: {failure.failure_type.value}",
                            f"Section: {failure.current_section}",
                            f"Error: {failure.error_message[:200]}" if failure.error_message else "",
                        ],
                    )
            except Exception:
                continue

        # 兜底：无匹配规则
        return RootCause(
            cause_type="unknown",
            description=f"无法精确定位根因，失败类型: {failure.failure_type.value}",
            confidence=0.3,
            suggested_fix="需要更多上下文信息或人工分析",
            evidence=[f"Error: {failure.error_message[:200]}"],
        )

    def analyze_batch(self, failures: list[FailureContext]) -> dict[str, RootCause]:
        """批量分析多个失败，识别共性根因。"""
        results: dict[str, RootCause] = {}
        for failure in failures:
            results[failure.failure_id] = self.analyze(failure)
        return results

    def identify_common_pattern(self, causes: list[RootCause]) -> Optional[str]:
        """从多个根因中识别共性模式。"""
        if not causes:
            return None
        # 按 cause_type 统计频次
        type_counts: dict[str, int] = {}
        for cause in causes:
            type_counts[cause.cause_type] = type_counts.get(cause.cause_type, 0) + 1
        # 找出占比 > 50% 的类型
        total = len(causes)
        for cause_type, count in sorted(type_counts.items(), key=lambda x: -x[1]):
            if count / total > 0.5:
                return cause_type
        return None


# ==============================================================
# 4. 合成 Skill
# ==============================================================

@dataclass
class SynthesisConfig:
    """合成 Skill 的配置——驱动执行逻辑的参数化描述。

    这是"模板 + 参数"合成方式的核心：不生成代码，而是生成配置。
    SynthesizedSkill 根据配置执行不同的检查逻辑。
    """
    # 基本信息
    name: str
    description: str
    target_issue_type: str          # 目标检查的问题类型
    methodology_focus: str = ""     # 聚焦的方法论 (did/iv/rdd/event_study/...)

    # 检查规则
    keyword_patterns: list[str] = field(default_factory=list)
    """关键词模式——在论文文本中搜索的模式列表"""

    negative_patterns: list[str] = field(default_factory=list)
    """反面模式——出现这些模式时标记问题"""

    required_elements: list[str] = field(default_factory=list)
    """必须存在的元素——缺失时产出 Finding"""

    severity_rules: dict[str, str] = field(default_factory=dict)
    """条件 → 严重程度映射"""

    # 适用性条件
    applicable_sections: list[str] = field(default_factory=list)
    applicable_phases: list[str] = field(default_factory=list)
    min_text_length: int = 50

    # 合成来源追踪
    synthesized_from: str = ""       # 原始 Skill 名称
    root_cause: str = ""             # 根因类型
    synthesis_reason: str = ""       # 合成原因

    def to_dict(self) -> dict:
        """序列化。"""
        return {
            "name": self.name,
            "description": self.description,
            "target_issue_type": self.target_issue_type,
            "methodology_focus": self.methodology_focus,
            "keyword_patterns": self.keyword_patterns,
            "negative_patterns": self.negative_patterns,
            "required_elements": self.required_elements,
            "severity_rules": self.severity_rules,
            "applicable_sections": self.applicable_sections,
            "applicable_phases": self.applicable_phases,
            "min_text_length": self.min_text_length,
            "synthesized_from": self.synthesized_from,
            "root_cause": self.root_cause,
            "synthesis_reason": self.synthesis_reason,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SynthesisConfig":
        """反序列化。"""
        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            target_issue_type=data.get("target_issue_type", ""),
            methodology_focus=data.get("methodology_focus", ""),
            keyword_patterns=data.get("keyword_patterns", []),
            negative_patterns=data.get("negative_patterns", []),
            required_elements=data.get("required_elements", []),
            severity_rules=data.get("severity_rules", {}),
            applicable_sections=data.get("applicable_sections", []),
            applicable_phases=data.get("applicable_phases", []),
            min_text_length=data.get("min_text_length", 50),
            synthesized_from=data.get("synthesized_from", ""),
            root_cause=data.get("root_cause", ""),
            synthesis_reason=data.get("synthesis_reason", ""),
        )


class SynthesizedSkill(Skill):
    """运行时合成的参数化 Skill。

    不是动态生成的 Python 代码，而是配置驱动的检查引擎：
    - keyword_patterns → 在文本中搜索匹配
    - negative_patterns → 搜索反面证据
    - required_elements → 验证必要元素存在
    - severity_rules → 根据匹配情况分配严重程度

    安全性: 所有逻辑由 SynthesisConfig 参数化，不执行任意代码。
    """

    def __init__(self, config: SynthesisConfig, version: str = "0.1"):
        self._config = config
        self._version = version

    @property
    def descriptor(self) -> SkillDescriptor:
        phases = tuple(self._config.applicable_phases) if self._config.applicable_phases else ()
        tags = ("synthesized", "experimental")
        if self._config.methodology_focus:
            tags = tags + (self._config.methodology_focus,)

        return SkillDescriptor(
            name=self._config.name,
            level=SkillLevel.ATOMIC,
            description=self._config.description,
            applicable_phases=phases,
            tags=tags,
            version=self._version,
            token_cost_estimate=200,  # 合成 Skill 通常轻量
        )

    def can_apply(self, context: SkillContext) -> float:
        """评估适用度——基于 section、phase、文本长度。"""
        if not SKILL_SYNTHESIS_ENABLED:
            return 0.0

        score = 0.0

        # Section 匹配
        if self._config.applicable_sections:
            section_lower = context.current_section.lower()
            if any(s.lower() in section_lower for s in self._config.applicable_sections):
                score += 0.4
            else:
                return 0.0  # Section 不匹配则不适用
        else:
            score += 0.2  # 无限制 = 通用

        # Phase 匹配
        if self._config.applicable_phases:
            if context.current_phase in self._config.applicable_phases:
                score += 0.3
            else:
                score *= 0.5  # Phase 不匹配降权但不排除
        else:
            score += 0.15

        # 文本长度门槛
        if len(context.paper_text) >= self._config.min_text_length:
            score += 0.2

        # 方法论匹配
        if self._config.methodology_focus:
            method_type = context.paper_metadata.get("methodology_type", "")
            if method_type == self._config.methodology_focus:
                score += 0.2
            elif method_type:
                score *= 0.7  # 方法论类型不同降权

        # 作为 experimental skill，整体降权避免过于激进
        return min(score * 0.8, 0.9)

    def execute(self, context: SkillContext) -> SkillResult:
        """执行配置驱动的检查逻辑。"""
        if not SKILL_SYNTHESIS_ENABLED:
            return SkillResult(success=True, findings=[])

        findings: list[Finding] = []
        text = context.paper_text.lower()

        start_time = time.time()

        try:
            # 1. 关键词模式匹配
            matched_keywords = []
            for pattern in self._config.keyword_patterns:
                if pattern.lower() in text:
                    matched_keywords.append(pattern)

            # 2. 反面模式检测
            negative_matches = []
            for pattern in self._config.negative_patterns:
                if pattern.lower() in text:
                    negative_matches.append(pattern)

            # 3. 必要元素缺失检测
            missing_elements = []
            for element in self._config.required_elements:
                if element.lower() not in text:
                    missing_elements.append(element)

            # 4. 生成 Findings
            # 4a. 反面模式触发 Finding
            for neg in negative_matches:
                severity = self._determine_severity("negative_pattern", neg)
                findings.append(Finding(
                    category=self._config.target_issue_type,
                    severity=severity,
                    description=(
                        f"检测到潜在问题标志: '{neg}' "
                        f"(由合成 Skill '{self._config.name}' 发现)"
                    ),
                    evidence=self._extract_evidence(context.paper_text, neg),
                    suggestion=self._config.description,
                    location=context.current_section,
                    confidence=0.6,  # 合成 Skill 的置信度基线偏保守
                    skill_source=self._config.name,
                ))

            # 4b. 必要元素缺失触发 Finding
            if missing_elements and matched_keywords:
                # 只在有相关关键词（说明该主题被讨论）但缺少必要元素时报告
                severity = self._determine_severity("missing_element", "")
                findings.append(Finding(
                    category=self._config.target_issue_type,
                    severity=severity,
                    description=(
                        f"讨论了相关主题 ({', '.join(matched_keywords[:3])}) "
                        f"但缺少必要元素: {', '.join(missing_elements[:3])}"
                    ),
                    evidence=f"Topic keywords found: {', '.join(matched_keywords[:3])}",
                    suggestion=f"建议补充: {', '.join(missing_elements[:3])}",
                    location=context.current_section,
                    confidence=0.55,
                    skill_source=self._config.name,
                ))

        except Exception as exc:
            elapsed = (time.time() - start_time) * 1000
            return SkillResult(
                success=False,
                error_message=f"SynthesizedSkill execution error: {exc}",
                execution_time_ms=elapsed,
            )

        elapsed = (time.time() - start_time) * 1000
        return SkillResult(
            findings=findings,
            success=True,
            execution_time_ms=elapsed,
            metadata={
                "synthesized_from": self._config.synthesized_from,
                "keywords_matched": len(matched_keywords),
                "negatives_found": len(negative_matches),
                "missing_elements": len(missing_elements),
            },
        )

    def get_instruction(self) -> str:
        """返回该合成 Skill 的完整 SOP。"""
        parts = [
            f"## {self._config.name}",
            f"**描述**: {self._config.description}",
            f"**目标问题类型**: {self._config.target_issue_type}",
        ]
        if self._config.methodology_focus:
            parts.append(f"**方法论聚焦**: {self._config.methodology_focus}")
        if self._config.keyword_patterns:
            parts.append(f"**关键词**: {', '.join(self._config.keyword_patterns[:10])}")
        if self._config.required_elements:
            parts.append(f"**必须检查的元素**: {', '.join(self._config.required_elements[:10])}")
        if self._config.synthesis_reason:
            parts.append(f"**合成原因**: {self._config.synthesis_reason}")
        return "\n".join(parts)

    def _determine_severity(self, check_type: str, matched: str) -> str:
        """根据规则确定严重程度。"""
        # 检查自定义规则
        for condition, severity in self._config.severity_rules.items():
            if condition in matched.lower() or condition == check_type:
                return severity
        # 默认
        if check_type == "negative_pattern":
            return "minor"
        elif check_type == "missing_element":
            return "major"
        return "suggestion"

    def _extract_evidence(self, full_text: str, pattern: str) -> str:
        """从原文中提取匹配模式周围的上下文片段作为证据。"""
        idx = full_text.lower().find(pattern.lower())
        if idx < 0:
            return ""
        start = max(0, idx - 80)
        end = min(len(full_text), idx + len(pattern) + 80)
        snippet = full_text[start:end].strip()
        if start > 0:
            snippet = "..." + snippet
        if end < len(full_text):
            snippet = snippet + "..."
        return snippet

    @property
    def config(self) -> SynthesisConfig:
        """暴露配置供序列化使用。"""
        return self._config


# ==============================================================
# 5. 失败轨迹存储
# ==============================================================

class FailureStore:
    """失败轨迹的内存存储 + 可选持久化。

    维护失败历史，支持按 Skill 名称、失败类型、时间范围检索。
    容量管理：超过 MAX_RECORDS 时自动淘汰最旧记录。
    """

    MAX_RECORDS = 500

    def __init__(self):
        self._records: list[FailureContext] = []
        self._by_skill: dict[str, list[FailureContext]] = {}
        self._by_type: dict[FailureType, list[FailureContext]] = {}

    def record(self, failure: FailureContext) -> None:
        """记录一次失败。"""
        self._records.append(failure)
        # 索引
        if failure.skill_name not in self._by_skill:
            self._by_skill[failure.skill_name] = []
        self._by_skill[failure.skill_name].append(failure)

        if failure.failure_type not in self._by_type:
            self._by_type[failure.failure_type] = []
        self._by_type[failure.failure_type].append(failure)

        # 容量管理
        if len(self._records) > self.MAX_RECORDS:
            evicted = self._records.pop(0)
            # 清理索引
            skill_list = self._by_skill.get(evicted.skill_name, [])
            if evicted in skill_list:
                skill_list.remove(evicted)
            type_list = self._by_type.get(evicted.failure_type, [])
            if evicted in type_list:
                type_list.remove(evicted)

    def query_by_skill(self, skill_name: str, limit: int = 10) -> list[FailureContext]:
        """按 Skill 名称检索失败记录。"""
        return self._by_skill.get(skill_name, [])[-limit:]

    def query_by_type(self, failure_type: FailureType, limit: int = 10) -> list[FailureContext]:
        """按失败类型检索。"""
        return self._by_type.get(failure_type, [])[-limit:]

    def query_similar(self, failure: FailureContext, limit: int = 5) -> list[FailureContext]:
        """检索与给定失败相似的历史记录。

        相似度基于: 同 Skill + 同 failure_type + 同 section 的组合匹配。
        """
        candidates = self._by_skill.get(failure.skill_name, [])
        scored: list[tuple[float, FailureContext]] = []

        for record in candidates:
            if record.failure_id == failure.failure_id:
                continue
            score = 0.0
            if record.failure_type == failure.failure_type:
                score += 0.5
            if record.current_section == failure.current_section:
                score += 0.3
            if record.current_phase == failure.current_phase:
                score += 0.2
            if score > 0:
                scored.append((score, record))

        scored.sort(key=lambda x: -x[0])
        return [r for _, r in scored[:limit]]

    def get_failure_stats(self) -> dict:
        """获取失败统计。"""
        return {
            "total_failures": len(self._records),
            "by_skill": {k: len(v) for k, v in self._by_skill.items()},
            "by_type": {k.value: len(v) for k, v in self._by_type.items()},
        }

    def get_recurring_failures(self, min_count: int = 3) -> list[dict]:
        """获取反复出现的失败模式（同 Skill + 同 failure_type 组合）。"""
        combo_counts: dict[str, int] = {}
        for record in self._records:
            key = f"{record.skill_name}:{record.failure_type.value}"
            combo_counts[key] = combo_counts.get(key, 0) + 1

        recurring = []
        for key, count in combo_counts.items():
            if count >= min_count:
                skill_name, ft_value = key.split(":", 1)
                recurring.append({
                    "skill_name": skill_name,
                    "failure_type": ft_value,
                    "count": count,
                })
        return sorted(recurring, key=lambda x: -x["count"])

    def serialize(self) -> dict:
        """序列化（保留最近 100 条）。"""
        recent = self._records[-100:]
        return {
            "records": [r.to_dict() for r in recent],
            "total_recorded": len(self._records),
        }

    @classmethod
    def deserialize(cls, data: dict) -> "FailureStore":
        """反序列化。"""
        store = cls()
        for record_dict in data.get("records", []):
            try:
                failure = FailureContext.from_dict(record_dict)
                store.record(failure)
            except Exception:
                continue
        return store


# ==============================================================
# 6. Skill 合成引擎
# ==============================================================

# 合成模板：基于根因类型的预定义修复策略
_SYNTHESIS_TEMPLATES: dict[str, dict] = {
    "pattern_gap": {
        "strategy": "expand_patterns",
        "description_template": "增强的{methodology}模式检测——覆盖{original} Skill 未覆盖的表述方式",
        "default_keywords": [],
        "default_negatives": [],
    },
    "coverage_gap": {
        "strategy": "add_checklist",
        "description_template": "补充检查项——覆盖{original} Skill 遗漏的{issue_type}类问题",
        "default_keywords": [],
        "default_negatives": [],
    },
    "threshold_issue": {
        "strategy": "adaptive_threshold",
        "description_template": "自适应阈值检查——避免{original} Skill 的硬编码阈值误判",
        "default_keywords": ["p-value", "significance", "threshold", "cutoff"],
        "default_negatives": [],
    },
    "domain_specificity_gap": {
        "strategy": "domain_specialize",
        "description_template": "经济学方法论特化检查——深化{original} Skill 在{methodology}领域的分析",
        "default_keywords": [],
        "default_negatives": [],
    },
    "misclassification": {
        "strategy": "refine_applicability",
        "description_template": "适用性精确化——修正{original} Skill 的方法论识别逻辑",
        "default_keywords": [],
        "default_negatives": [],
    },
    "inference_error": {
        "strategy": "add_validation_steps",
        "description_template": "推理验证增强——在{original} Skill 的推理链中增加中间验证",
        "default_keywords": [],
        "default_negatives": [],
    },
    "data_dependency": {
        "strategy": "graceful_degradation",
        "description_template": "信息不完整时的降级检查——{original} Skill 的容错版本",
        "default_keywords": [],
        "default_negatives": [],
    },
    "dependency_failure": {
        "strategy": "retry_with_fallback",
        "description_template": "容错重试版——{original} Skill 的轻量备选（不依赖外部工具）",
        "default_keywords": [],
        "default_negatives": [],
    },
    "complexity_underestimation": {
        "strategy": "early_exit",
        "description_template": "轻量精简版——{original} Skill 的快速扫描版本（在{issue_type}场景下提前退出）",
        "default_keywords": [],
        "default_negatives": [],
    },
    "output_schema_drift": {
        "strategy": "normalize_output",
        "description_template": "输出规范化——修正{original} Skill 的输出格式问题",
        "default_keywords": [],
        "default_negatives": [],
    },
    "calibration_issue": {
        "strategy": "recalibrate",
        "description_template": "置信度校准——优化{original} Skill 在{methodology}领域的 Finding 质量",
        "default_keywords": [],
        "default_negatives": [],
    },
}

# 经济学方法论特化的关键词库
_ECON_METHODOLOGY_KEYWORDS: dict[str, list[str]] = {
    "did": [
        "difference-in-differences", "diff-in-diff", "DID", "parallel trends",
        "pre-treatment", "treatment group", "control group", "common trends",
        "staggered adoption", "event study", "two-way fixed effects",
    ],
    "iv": [
        "instrumental variable", "two-stage least squares", "2SLS", "TSLS",
        "first stage", "exclusion restriction", "relevance condition",
        "weak instruments", "Stock-Yogo", "Cragg-Donald", "F-statistic",
        "overidentification", "Sargan", "Hansen J",
    ],
    "rdd": [
        "regression discontinuity", "RDD", "running variable", "cutoff",
        "bandwidth", "local polynomial", "McCrary test", "manipulation test",
        "fuzzy", "sharp", "donut hole",
    ],
    "event_study": [
        "event study", "abnormal returns", "event window", "estimation window",
        "cumulative abnormal return", "CAR", "BHAR",
    ],
}


class SkillSynthesizer:
    """Skill 合成引擎。

    合成流程:
        1. 接收失败上下文或 SynthesisSignal
        2. 分析根因 (RootCauseAnalyzer)
        3. 检索相似失败的历史修复
        4. 选择合成模板
        5. 填充参数生成 SynthesisConfig
        6. 创建 SynthesizedSkill
        7. 沙箱验证（在原始失败上下文中重试）
        8. 注册到 Skill 体系（标记为 experimental）
    """

    def __init__(
        self,
        failure_store: FailureStore | None = None,
        root_cause_analyzer: RootCauseAnalyzer | None = None,
    ):
        self._failure_store = failure_store or FailureStore()
        self._analyzer = root_cause_analyzer or RootCauseAnalyzer()
        self._synthesized_skills: list[SynthesizedSkill] = []
        self._synthesis_history: list[dict] = []

    def synthesize_from_failure(
        self, failure: FailureContext
    ) -> Optional[SynthesizedSkill]:
        """从单次失败中合成修复性 Skill。

        Returns:
            合成的 Skill（如果合成成功），否则 None。
        """
        if not SKILL_SYNTHESIS_ENABLED:
            return None

        # 1. 根因分析
        root_cause = self._analyzer.analyze(failure)
        logger.info(
            "[SkillSynthesizer] Root cause for '%s': %s (confidence=%.2f)",
            failure.skill_name, root_cause.cause_type, root_cause.confidence,
        )

        # 2. 置信度门槛——太低说明分析不可靠，不贸然合成
        if root_cause.confidence < 0.4:
            logger.info("[SkillSynthesizer] Root cause confidence too low, skipping synthesis")
            self._record_synthesis_attempt(failure, root_cause, success=False, reason="low_confidence")
            return None

        # 3. 检索相似失败
        similar_failures = self._failure_store.query_similar(failure, limit=5)

        # 4. 选择合成模板
        template = _SYNTHESIS_TEMPLATES.get(root_cause.cause_type)
        if not template:
            logger.info("[SkillSynthesizer] No template for cause type: %s", root_cause.cause_type)
            self._record_synthesis_attempt(failure, root_cause, success=False, reason="no_template")
            return None

        # 5. 生成配置
        config = self._build_config(failure, root_cause, template, similar_failures)

        # 6. 创建 SynthesizedSkill
        skill = SynthesizedSkill(config, version="0.1")

        # 7. 沙箱验证
        if not self._sandbox_validate(skill, failure):
            logger.info("[SkillSynthesizer] Sandbox validation failed for '%s'", config.name)
            self._record_synthesis_attempt(failure, root_cause, success=False, reason="sandbox_failed")
            return None

        # 8. 注册
        self._synthesized_skills.append(skill)
        self._record_synthesis_attempt(failure, root_cause, success=True)

        logger.info(
            "[SkillSynthesizer] Successfully synthesized '%s' (from %s, cause=%s)",
            config.name, failure.skill_name, root_cause.cause_type,
        )
        return skill

    def synthesize_from_signal(self, signal: Any) -> Optional[SynthesizedSkill]:
        """从 SynthesisSignal（Phase 6 反思触发）合成 Skill。

        SynthesisSignal 提供的信息:
        - gap_pattern: 反复差距模式 (gap_type, severity_trend, sessions_involved)
        - suggested_skill_type: 建议的 Skill 类型
        - trigger_reason: 触发原因
        """
        if not SKILL_SYNTHESIS_ENABLED:
            return None

        # 从 signal 构造虚拟的 FailureContext
        gap_pattern = signal.gap_pattern if hasattr(signal, "gap_pattern") else None
        if not gap_pattern:
            return None

        gap_type = gap_pattern.gap_type if hasattr(gap_pattern, "gap_type") else "unknown"
        description = gap_pattern.description if hasattr(gap_pattern, "description") else ""
        suggested_type = signal.suggested_skill_type if hasattr(signal, "suggested_skill_type") else ""

        # 确定方法论聚焦
        methodology_focus = ""
        if "methodology_" in gap_type:
            methodology_focus = gap_type.replace("methodology_", "")

        # 根据 gap_type 选择模板
        cause_type = self._map_gap_to_cause(gap_type)
        template = _SYNTHESIS_TEMPLATES.get(cause_type, _SYNTHESIS_TEMPLATES.get("coverage_gap"))

        if not template:
            return None

        # 构建配置
        name = f"synth_{suggested_type}_{int(time.time()) % 10000}"
        config = SynthesisConfig(
            name=name,
            description=template["description_template"].format(
                original="(reflection-triggered)",
                methodology=methodology_focus or "general",
                issue_type=gap_type,
            ),
            target_issue_type=gap_type,
            methodology_focus=methodology_focus,
            keyword_patterns=self._get_methodology_keywords(methodology_focus),
            negative_patterns=[],
            required_elements=[],
            applicable_sections=self._infer_sections(gap_type),
            applicable_phases=["DEEP_REVIEW", "SYNTHESIS"],
            synthesized_from="reflection_trigger",
            root_cause=cause_type,
            synthesis_reason=signal.trigger_reason if hasattr(signal, "trigger_reason") else "",
        )

        skill = SynthesizedSkill(config, version="0.1")
        self._synthesized_skills.append(skill)

        self._synthesis_history.append({
            "type": "from_signal",
            "gap_type": gap_type,
            "skill_name": name,
            "timestamp": time.time(),
            "success": True,
        })

        logger.info(
            "[SkillSynthesizer] Synthesized from reflection signal: '%s' (gap=%s)",
            name, gap_type,
        )
        return skill

    def get_synthesized_skills(self) -> list[SynthesizedSkill]:
        """获取所有合成的 Skill。"""
        return list(self._synthesized_skills)

    def get_synthesis_history(self) -> list[dict]:
        """获取合成历史。"""
        return list(self._synthesis_history)

    # --- 内部方法 ---

    def _build_config(
        self,
        failure: FailureContext,
        root_cause: RootCause,
        template: dict,
        similar_failures: list[FailureContext],
    ) -> SynthesisConfig:
        """基于模板和上下文构建 SynthesisConfig。"""
        methodology = failure.paper_metadata.get("methodology_type", "general")
        name = f"synth_{failure.skill_name}_{root_cause.cause_type}_{int(time.time()) % 10000}"

        description = template["description_template"].format(
            original=failure.skill_name,
            methodology=methodology,
            issue_type=failure.failure_type.value,
        )

        # 收集关键词
        keywords = list(template.get("default_keywords", []))
        keywords.extend(self._get_methodology_keywords(methodology))

        # 从失败上下文中提取额外关键词
        if failure.paper_text_snippet:
            context_keywords = self._extract_context_keywords(failure.paper_text_snippet)
            keywords.extend(context_keywords)

        # 从相似失败中提取 patterns
        for similar in similar_failures[:3]:
            if similar.paper_text_snippet:
                extra_kw = self._extract_context_keywords(similar.paper_text_snippet)
                keywords.extend(extra_kw[:3])

        # 去重
        keywords = list(dict.fromkeys(keywords))[:20]

        return SynthesisConfig(
            name=name,
            description=description,
            target_issue_type=failure.failure_type.value,
            methodology_focus=methodology,
            keyword_patterns=keywords,
            negative_patterns=template.get("default_negatives", []),
            required_elements=[],
            severity_rules={},
            applicable_sections=self._infer_sections_from_failure(failure),
            applicable_phases=[failure.current_phase] if failure.current_phase else ["DEEP_REVIEW"],
            min_text_length=50,
            synthesized_from=failure.skill_name,
            root_cause=root_cause.cause_type,
            synthesis_reason=root_cause.description,
        )

    def _sandbox_validate(self, skill: SynthesizedSkill, failure: FailureContext) -> bool:
        """在失败上下文中验证合成 Skill 的基本有效性。

        验证标准（宽松——合成 Skill 不需要完美，只需要"能跑且不产出垃圾"）:
        1. execute() 不抛异常
        2. 如果产出 Findings，必须有非空 description
        3. can_apply() 对原始上下文 > 0
        """
        # 构造模拟上下文
        mock_context = SkillContext(
            paper_text=failure.paper_text_snippet or "This paper examines...",
            paper_metadata=failure.paper_metadata,
            current_phase=failure.current_phase,
            current_section=failure.current_section,
        )

        # 检查 can_apply
        try:
            applicability = skill.can_apply(mock_context)
            if applicability <= 0:
                return False
        except Exception:
            return False

        # 检查 execute
        try:
            result = skill.execute(mock_context)
            if not result.success:
                return False
            # 验证 Findings 格式
            for finding in result.findings:
                if not finding.description:
                    return False
        except Exception:
            return False

        return True

    def _get_methodology_keywords(self, methodology: str) -> list[str]:
        """获取方法论相关的关键词。"""
        return _ECON_METHODOLOGY_KEYWORDS.get(methodology, [])[:10]

    def _extract_context_keywords(self, text: str) -> list[str]:
        """从文本片段中提取潜在的关键词。

        简单启发式：提取包含特定模式的短语。
        """
        keywords = []
        # 提取括号中的术语
        parenthesized = re.findall(r'\(([^)]{3,30})\)', text)
        keywords.extend(parenthesized[:5])
        # 提取引号中的术语
        quoted = re.findall(r'"([^"]{3,30})"', text)
        keywords.extend(quoted[:5])
        return keywords

    def _infer_sections_from_failure(self, failure: FailureContext) -> list[str]:
        """从失败上下文推断适用的 sections。"""
        if failure.current_section:
            return [failure.current_section]
        return []

    def _infer_sections(self, gap_type: str) -> list[str]:
        """从 gap_type 推断适用的 sections。"""
        mapping = {
            "coverage": [],  # 通用
            "depth": ["methodology", "results"],
            "evidence": ["results", "discussion"],
            "efficiency": [],
            "methodology_did": ["methodology", "identification"],
            "methodology_iv": ["methodology", "identification"],
            "methodology_rdd": ["methodology", "identification"],
        }
        return mapping.get(gap_type, [])

    def _map_gap_to_cause(self, gap_type: str) -> str:
        """将 gap_type 映射到根因类型（用于选择模板）。"""
        mapping = {
            "coverage": "coverage_gap",
            "depth": "domain_specificity_gap",
            "evidence": "pattern_gap",
            "efficiency": "data_dependency",
            "methodology_did": "domain_specificity_gap",
            "methodology_iv": "domain_specificity_gap",
            "methodology_rdd": "domain_specificity_gap",
            "methodology_general": "coverage_gap",
        }
        return mapping.get(gap_type, "coverage_gap")

    def _record_synthesis_attempt(
        self,
        failure: FailureContext,
        root_cause: RootCause,
        success: bool,
        reason: str = "",
    ) -> None:
        """记录合成尝试。"""
        self._synthesis_history.append({
            "type": "from_failure",
            "failure_id": failure.failure_id,
            "skill_name": failure.skill_name,
            "root_cause": root_cause.cause_type,
            "success": success,
            "reason": reason,
            "timestamp": time.time(),
        })

    def serialize(self) -> dict:
        """序列化合成器状态。"""
        return {
            "synthesized_skills": [
                s.config.to_dict() for s in self._synthesized_skills
            ],
            "synthesis_history": self._synthesis_history[-50:],
            "failure_store": self._failure_store.serialize(),
        }

    @classmethod
    def deserialize(cls, data: dict) -> "SkillSynthesizer":
        """反序列化。"""
        store = FailureStore.deserialize(data.get("failure_store", {}))
        synthesizer = cls(failure_store=store)
        synthesizer._synthesis_history = data.get("synthesis_history", [])

        # 恢复合成 Skill
        for skill_data in data.get("synthesized_skills", []):
            try:
                config = SynthesisConfig.from_dict(skill_data)
                skill = SynthesizedSkill(config)
                synthesizer._synthesized_skills.append(skill)
            except Exception:
                continue

        return synthesizer


# ==============================================================
# 7. 生命周期管理
# ==============================================================

class SynthesisConfidenceLevel(Enum):
    """合成 Skill 的置信度等级。"""
    EXPERIMENTAL = "experimental"
    """刚合成，未经充分验证"""
    VALIDATED = "validated"
    """通过验证（执行 >= N 次且保留率达标）"""
    PROMOTED = "promoted"
    """晋升为正式 Skill（高保留率 + 长期稳定）"""
    DEPRECATED = "deprecated"
    """已废弃（保留率过低或长期未使用）"""


@dataclass
class SynthesizedSkillRecord:
    """合成 Skill 的生命周期记录。"""
    skill_name: str
    confidence_level: SynthesisConfidenceLevel = SynthesisConfidenceLevel.EXPERIMENTAL
    created_at: float = field(default_factory=time.time)
    last_used_at: float = 0.0
    total_executions: int = 0
    successful_executions: int = 0
    findings_produced: int = 0
    findings_retained: int = 0       # 被最终报告保留的 findings
    findings_discarded: int = 0      # 被丢弃的 findings
    promotion_threshold_executions: int = 10
    promotion_threshold_retention: float = 0.5
    deprecation_threshold_days: int = 30  # 未使用天数
    deprecation_threshold_retention: float = 0.2

    @property
    def retention_rate(self) -> float:
        """Finding 保留率。"""
        total = self.findings_retained + self.findings_discarded
        if total == 0:
            return 0.0
        return self.findings_retained / total

    @property
    def success_rate(self) -> float:
        """执行成功率。"""
        if self.total_executions == 0:
            return 0.0
        return self.successful_executions / self.total_executions

    @property
    def days_since_last_use(self) -> float:
        """距离上次使用的天数。"""
        if self.last_used_at == 0:
            return (time.time() - self.created_at) / 86400
        return (time.time() - self.last_used_at) / 86400

    @property
    def should_promote(self) -> bool:
        """是否应该晋升。"""
        return (
            self.confidence_level == SynthesisConfidenceLevel.EXPERIMENTAL
            and self.total_executions >= self.promotion_threshold_executions
            and self.retention_rate >= self.promotion_threshold_retention
            and self.success_rate >= 0.8
        )

    @property
    def should_deprecate(self) -> bool:
        """是否应该废弃。"""
        # 长期未使用
        if self.days_since_last_use > self.deprecation_threshold_days:
            return True
        # 保留率过低（且有足够样本）
        if (self.total_executions >= 5
                and self.retention_rate < self.deprecation_threshold_retention):
            return True
        return False


class SynthesisLifecycleManager:
    """合成 Skill 的生命周期管理器。

    职责:
    - 追踪每个合成 Skill 的使用情况和质量指标
    - 自动晋升: experimental → validated → promoted
    - 自动废弃: 长期未使用或保留率过低的 Skill
    - 对外暴露健康报告
    """

    def __init__(self):
        self._records: dict[str, SynthesizedSkillRecord] = {}

    def register(self, skill_name: str) -> None:
        """注册一个新合成的 Skill。"""
        if skill_name not in self._records:
            self._records[skill_name] = SynthesizedSkillRecord(skill_name=skill_name)

    def on_skill_executed(self, skill_name: str, result: SkillResult) -> None:
        """记录一次执行。"""
        record = self._records.get(skill_name)
        if not record:
            return

        record.total_executions += 1
        record.last_used_at = time.time()

        if result.success:
            record.successful_executions += 1
            record.findings_produced += len(result.findings)

    def on_findings_retained(
        self, skill_name: str, retained: int, discarded: int
    ) -> None:
        """记录 Findings 保留/丢弃。"""
        record = self._records.get(skill_name)
        if not record:
            return
        record.findings_retained += retained
        record.findings_discarded += discarded

    def run_lifecycle_check(self) -> dict:
        """执行一轮生命周期检查。

        Returns:
            操作摘要 {"promoted": [...], "deprecated": [...]}
        """
        promoted = []
        deprecated = []

        for name, record in self._records.items():
            if record.confidence_level == SynthesisConfidenceLevel.DEPRECATED:
                continue

            if record.should_promote:
                record.confidence_level = SynthesisConfidenceLevel.VALIDATED
                promoted.append(name)
                logger.info("[Lifecycle] Promoted '%s' to VALIDATED", name)

            elif record.should_deprecate:
                record.confidence_level = SynthesisConfidenceLevel.DEPRECATED
                deprecated.append(name)
                logger.info("[Lifecycle] Deprecated '%s'", name)

        return {"promoted": promoted, "deprecated": deprecated}

    def get_active_skills(self) -> list[str]:
        """获取活跃状态（非废弃）的合成 Skill 名称。"""
        return [
            name for name, record in self._records.items()
            if record.confidence_level != SynthesisConfidenceLevel.DEPRECATED
        ]

    def get_record(self, skill_name: str) -> Optional[SynthesizedSkillRecord]:
        """获取指定 Skill 的记录。"""
        return self._records.get(skill_name)

    def get_health_report(self) -> dict:
        """获取整体健康报告。"""
        total = len(self._records)
        by_level = {}
        for record in self._records.values():
            level = record.confidence_level.value
            by_level[level] = by_level.get(level, 0) + 1

        avg_retention = 0.0
        active_records = [
            r for r in self._records.values()
            if r.confidence_level != SynthesisConfidenceLevel.DEPRECATED
            and r.total_executions > 0
        ]
        if active_records:
            avg_retention = sum(r.retention_rate for r in active_records) / len(active_records)

        return {
            "total_synthesized": total,
            "by_confidence_level": by_level,
            "average_retention_rate": round(avg_retention, 3),
            "active_count": len(self.get_active_skills()),
        }

    def serialize(self) -> dict:
        """序列化。"""
        return {
            "records": {
                name: {
                    "confidence_level": record.confidence_level.value,
                    "created_at": record.created_at,
                    "last_used_at": record.last_used_at,
                    "total_executions": record.total_executions,
                    "successful_executions": record.successful_executions,
                    "findings_produced": record.findings_produced,
                    "findings_retained": record.findings_retained,
                    "findings_discarded": record.findings_discarded,
                }
                for name, record in self._records.items()
            }
        }

    @classmethod
    def deserialize(cls, data: dict) -> "SynthesisLifecycleManager":
        """反序列化。"""
        manager = cls()
        for name, record_data in data.get("records", {}).items():
            try:
                level = SynthesisConfidenceLevel(record_data.get("confidence_level", "experimental"))
            except ValueError:
                level = SynthesisConfidenceLevel.EXPERIMENTAL

            record = SynthesizedSkillRecord(
                skill_name=name,
                confidence_level=level,
                created_at=record_data.get("created_at", 0.0),
                last_used_at=record_data.get("last_used_at", 0.0),
                total_executions=record_data.get("total_executions", 0),
                successful_executions=record_data.get("successful_executions", 0),
                findings_produced=record_data.get("findings_produced", 0),
                findings_retained=record_data.get("findings_retained", 0),
                findings_discarded=record_data.get("findings_discarded", 0),
            )
            manager._records[name] = record
        return manager


# ==============================================================
# 8. 编排器 — 实现 SkillSynthesisReceiver Protocol
# ==============================================================

class SkillSynthesisOrchestrator:
    """Phase 4 顶层编排器。

    实现 SkillSynthesisReceiver Protocol，接收 Phase 6 的 SynthesisSignal。
    协调: FailureStore + RootCauseAnalyzer + SkillSynthesizer + LifecycleManager。

    Usage:
        orchestrator = SkillSynthesisOrchestrator()

        # 方式 1: 对接 Phase 6 反思
        trigger.set_receiver(orchestrator)

        # 方式 2: 直接报告失败
        orchestrator.on_skill_failed(failure_context)

        # 方式 3: 获取可用的合成 Skill
        skills = orchestrator.get_available_skills()
    """

    def __init__(self):
        self._failure_store = FailureStore()
        self._analyzer = RootCauseAnalyzer()
        self._synthesizer = SkillSynthesizer(
            failure_store=self._failure_store,
            root_cause_analyzer=self._analyzer,
        )
        self._lifecycle = SynthesisLifecycleManager()
        self._pending_signals: list[Any] = []

    # --- SkillSynthesisReceiver Protocol ---

    def receive_synthesis_signal(self, signal: Any) -> bool:
        """接收 Phase 6 的 SynthesisSignal。

        实现 SkillSynthesisReceiver Protocol。
        """
        if not SKILL_SYNTHESIS_ENABLED:
            return False

        self._pending_signals.append(signal)

        # 立即尝试合成
        skill = self._synthesizer.synthesize_from_signal(signal)
        if skill:
            self._lifecycle.register(skill.config.name)
            return True
        return False

    # --- 失败报告接口 ---

    def on_skill_failed(self, failure: FailureContext) -> Optional[SynthesizedSkill]:
        """报告一次 Skill 失败，触发可能的合成。

        Args:
            failure: 失败上下文

        Returns:
            合成的 Skill（如果合成成功）
        """
        if not SKILL_SYNTHESIS_ENABLED:
            return None

        # 记录到 FailureStore
        self._failure_store.record(failure)

        # 检查是否为反复失败模式
        recurring = self._failure_store.get_recurring_failures(min_count=2)
        is_recurring = any(
            r["skill_name"] == failure.skill_name
            and r["failure_type"] == failure.failure_type.value
            for r in recurring
        )

        # 反复失败才触发合成（单次失败可能只是噪音）
        if not is_recurring:
            return None

        # 合成
        skill = self._synthesizer.synthesize_from_failure(failure)
        if skill:
            self._lifecycle.register(skill.config.name)
            return skill
        return None

    # --- Skill 执行追踪 ---

    def on_synthesized_skill_executed(self, skill_name: str, result: SkillResult) -> None:
        """追踪合成 Skill 的执行。"""
        self._lifecycle.on_skill_executed(skill_name, result)

    def on_synthesized_findings_retained(
        self, skill_name: str, retained: int, discarded: int
    ) -> None:
        """追踪合成 Skill 的 Findings 保留情况。"""
        self._lifecycle.on_findings_retained(skill_name, retained, discarded)

    # --- 查询接口 ---

    def get_available_skills(self) -> list[SynthesizedSkill]:
        """获取所有活跃的合成 Skill（排除已废弃的）。"""
        active_names = set(self._lifecycle.get_active_skills())
        return [
            s for s in self._synthesizer.get_synthesized_skills()
            if s.config.name in active_names
        ]

    def get_failure_stats(self) -> dict:
        """获取失败统计。"""
        return self._failure_store.get_failure_stats()

    def get_synthesis_report(self) -> dict:
        """获取合成报告。"""
        return {
            "failure_stats": self._failure_store.get_failure_stats(),
            "synthesis_history": self._synthesizer.get_synthesis_history()[-20:],
            "lifecycle_health": self._lifecycle.get_health_report(),
            "pending_signals": len(self._pending_signals),
            "recurring_failures": self._failure_store.get_recurring_failures(),
        }

    def run_maintenance(self) -> dict:
        """执行维护操作（生命周期检查）。"""
        return self._lifecycle.run_lifecycle_check()

    # --- 序列化 ---

    def serialize(self) -> dict:
        """序列化整个编排器状态。"""
        return {
            "synthesizer": self._synthesizer.serialize(),
            "lifecycle": self._lifecycle.serialize(),
        }

    @classmethod
    def deserialize(cls, data: dict) -> "SkillSynthesisOrchestrator":
        """反序列化。"""
        orchestrator = cls()
        if "synthesizer" in data:
            orchestrator._synthesizer = SkillSynthesizer.deserialize(data["synthesizer"])
            orchestrator._failure_store = orchestrator._synthesizer._failure_store
        if "lifecycle" in data:
            orchestrator._lifecycle = SynthesisLifecycleManager.deserialize(data["lifecycle"])
        return orchestrator