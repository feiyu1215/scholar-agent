"""
core/post_edit_verify.py — 修改后自动验证（零 LLM 成本）

设计原则 (来自 COGNITIVE_ANCHOR §4.3):
    这是"信息提供"而非"控制"。验证结果作为 tool_result 的一部分返回给 Agent，
    Agent 自己决定是否需要修正。不自动 revert，不阻塞修改。

三层验证:
    Layer 1: 交叉引用一致性（regex）— 修改后是否引入了悬空引用
    Layer 2: 写作风格漂移（统计）— 修改后是否偏离了作者原始风格
    Layer 3: AI 模式回归（regex）— 修改后是否引入了 AI 典型用词

成本: <100ms，零 API 调用。
来源: 适配自 legacy/tools/post_edit_verify.py + legacy/utils/voice_profile.py
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# ============================================================
# Data Classes
# ============================================================

@dataclass
class VerificationResult:
    """修改后验证结果。"""
    passed: bool
    consistency_ok: bool
    voice_drift_ok: bool
    ai_regression_ok: bool
    issues: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


@dataclass
class VoiceFingerprint:
    """作者写作风格指纹（量化）。"""
    avg_sentence_length: float = 0.0
    sentence_length_std: float = 0.0
    passive_ratio: float = 0.0
    hedge_frequency: float = 0.0
    total_words_analyzed: int = 0


# ============================================================
# Layer 1: 交叉引用一致性（Rule-Based, Zero Cost）
# ============================================================

_FIGURE_REF = re.compile(r'(?:Figure|Fig\.?)\s*(\d+(?:\.\d+)?[a-z]?)', re.IGNORECASE)
_TABLE_REF = re.compile(r'(?:Table|Tab\.?)\s*(\d+(?:\.\d+)?[a-z]?)', re.IGNORECASE)
_SECTION_REF = re.compile(r'(?:Section|Sec\.?)\s*(\d+(?:\.\d+)*)', re.IGNORECASE)
_EQUATION_REF = re.compile(r'(?:Equation|Eq\.?|Eqn\.?)\s*\(?(\d+(?:\.\d+)?)\)?', re.IGNORECASE)


def _extract_cross_refs(text: str) -> List[Tuple[str, str]]:
    """提取文本中的所有交叉引用。"""
    refs = []
    for m in _FIGURE_REF.finditer(text):
        refs.append(("figure", m.group(1)))
    for m in _TABLE_REF.finditer(text):
        refs.append(("table", m.group(1)))
    for m in _SECTION_REF.finditer(text):
        refs.append(("section", m.group(1)))
    for m in _EQUATION_REF.finditer(text):
        refs.append(("equation", m.group(1)))
    return refs


def check_consistency(
    new_text: str,
    all_sections_text: str,
) -> Tuple[bool, List[str]]:
    """
    检查修改后的文本中引用的 Figure/Table/Section 是否在全文中存在。

    Args:
        new_text: 修改后的 section 内容
        all_sections_text: 论文全文（拼接的所有 section 内容）

    Returns:
        (passed, issues)
    """
    issues = []
    refs_in_new = _extract_cross_refs(new_text)

    # 从全文中提取已定义的 figure/table（通过标题行识别）
    # 识别 "Figure N:" 或 "Table N:" 这样的定义行
    defined_figures = set(m.group(1) for m in re.finditer(
        r'(?:Figure|Fig\.?)\s*(\d+(?:\.\d+)?[a-z]?)[\s:.：]', all_sections_text, re.IGNORECASE
    ))
    defined_tables = set(m.group(1) for m in re.finditer(
        r'(?:Table|Tab\.?)\s*(\d+(?:\.\d+)?[a-z]?)[\s:.：]', all_sections_text, re.IGNORECASE
    ))

    for ref_type, ref_id in refs_in_new:
        if ref_type == "figure" and defined_figures:
            if ref_id not in defined_figures:
                issues.append(f"可能的悬空引用: Figure {ref_id} 未在论文中找到定义")
        elif ref_type == "table" and defined_tables:
            if ref_id not in defined_tables:
                issues.append(f"可能的悬空引用: Table {ref_id} 未在论文中找到定义")

    return (len(issues) == 0, issues)


# ============================================================
# Layer 2: 写作风格漂移（Statistical, Zero LLM Cost）
# ============================================================

_HEDGE_WORDS = {
    "may", "might", "could", "possibly", "perhaps", "likely",
    "suggests", "indicates", "appears", "seems", "tends",
    "relatively", "somewhat", "approximately", "roughly",
    "arguably", "presumably", "potentially",
}

_PASSIVE_PATTERNS = [
    re.compile(r"\b(?:is|are|was|were|been|being)\s+\w+ed\b", re.IGNORECASE),
    re.compile(r"\b(?:is|are|was|were|been|being)\s+\w+en\b", re.IGNORECASE),
]


def _split_sentences(text: str) -> List[str]:
    """简单英文句子分割器。"""
    text = re.sub(
        r'\b(Dr|Mr|Mrs|Ms|Prof|et al|vs|i\.e|e\.g)\.',
        lambda m: m.group(0).replace('.', '<DOT>'), text
    )
    sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z])', text)
    return [s.replace('<DOT>', '.').strip() for s in sentences if s.strip()]


def extract_voice(text: str) -> VoiceFingerprint:
    """从文本中提取写作风格指纹。"""
    fp = VoiceFingerprint()
    sentences = _split_sentences(text)
    words = text.split()

    if not sentences or not words:
        return fp

    fp.total_words_analyzed = len(words)

    # 句长统计
    sent_lengths = [len(s.split()) for s in sentences]
    fp.avg_sentence_length = round(statistics.mean(sent_lengths), 1)
    fp.sentence_length_std = round(
        statistics.stdev(sent_lengths), 1
    ) if len(sent_lengths) > 1 else 0.0

    # 被动语态比例
    passive_count = sum(
        1 for sent in sentences
        if any(pat.search(sent) for pat in _PASSIVE_PATTERNS)
    )
    fp.passive_ratio = round(passive_count / len(sentences), 2)

    # 模糊限定词频率
    text_lower = text.lower()
    total_hedges = sum(
        len(re.findall(r'\b' + h + r'\b', text_lower))
        for h in _HEDGE_WORDS
    )
    fp.hedge_frequency = round(total_hedges / len(words) * 100, 2)

    return fp


def check_voice_drift(
    old_text: str,
    new_text: str,
    voice_profile: Optional[VoiceFingerprint] = None,
) -> Tuple[bool, List[str]]:
    """
    检测修改前后的写作风格是否发生显著漂移。

    如果提供了 voice_profile（累计指纹），则与 profile 对比；
    否则与 old_text 对比。

    Returns:
        (passed, warnings)
    """
    reference = voice_profile if voice_profile else extract_voice(old_text)
    revised = extract_voice(new_text)

    if reference.total_words_analyzed == 0:
        return (True, [])

    warnings = []

    # 句长漂移（超过 1.5 倍标准差）
    std = reference.sentence_length_std if reference.sentence_length_std > 0 else 5.0
    if abs(revised.avg_sentence_length - reference.avg_sentence_length) > std * 1.5:
        warnings.append(
            f"句长漂移: 修改后 avg={revised.avg_sentence_length} words, "
            f"原文 avg={reference.avg_sentence_length} (±{std})"
        )

    # 被动语态比例偏移（>20% 绝对变化）
    if abs(revised.passive_ratio - reference.passive_ratio) > 0.2:
        warnings.append(
            f"被动语态变化: {revised.passive_ratio:.0%} vs 原文 {reference.passive_ratio:.0%}"
        )

    # 模糊限定词频率偏移（>50% 相对变化）
    if reference.hedge_frequency > 0:
        if abs(revised.hedge_frequency - reference.hedge_frequency) > reference.hedge_frequency * 0.5:
            warnings.append(
                f"学术限定词频率变化: {revised.hedge_frequency:.1f}/100w vs 原文 {reference.hedge_frequency:.1f}/100w"
            )

    return (len(warnings) == 0, warnings)


# ============================================================
# Layer 3: AI 模式回归检测（Regex, Zero Cost）
# ============================================================

_AI_PATTERNS = [
    r'\b(?:delve|delves|delving)\b',
    r'\b(?:tapestry|tapestries)\b',
    r'\b(?:landscape)\b(?:\s+of)',
    r'\b(?:paradigm shift)\b',
    r'\b(?:in the realm of)\b',
    r'\b(?:it is worth noting that)\b',
    r'\b(?:it is important to note)\b',
    r'\b(?:this underscores)\b',
    r'\b(?:a testament to)\b',
    r'\b(?:navigating the)\b',
    r'\b(?:in conclusion,?\s+this)\b',
    r'\b(?:multifaceted)\b',
    r'\b(?:leverage|leveraging|leveraged)\b',
    r'\b(?:underscore|underscores|underscoring)\b',
    r'\b(?:pivotal)\b',
    r'\b(?:groundbreaking)\b',
    r'\b(?:shed(?:s|ding)? light on)\b',
    r'\b(?:pave(?:s|d)? the way)\b',
]

_AI_REGEXES = [re.compile(p, re.IGNORECASE) for p in _AI_PATTERNS]


def check_ai_regression(old_text: str, new_text: str) -> Tuple[bool, List[str]]:
    """
    检测修改是否引入了新的 AI 写作模式。

    对比修改前后的 AI 信号数量。如果增加了，列出新引入的信号。

    Returns:
        (passed, issues)
    """
    old_count = _count_ai_signals(old_text)
    new_count = _count_ai_signals(new_text)

    issues = []
    if new_count > old_count:
        # 找出具体新引入的信号
        for regex in _AI_REGEXES:
            old_matches = set(m.group().lower() for m in regex.finditer(old_text))
            new_matches = set(m.group().lower() for m in regex.finditer(new_text))
            added = new_matches - old_matches
            for signal in added:
                issues.append(f"新引入 AI 信号: '{signal}'")

        if not issues:
            issues.append(
                f"AI 信号增加: {old_count} → {new_count} (增加了 {new_count - old_count} 处)"
            )

    return (len(issues) == 0, issues)


def _count_ai_signals(text: str) -> int:
    """统计文本中 AI 写作模式出现次数。"""
    return sum(len(regex.findall(text)) for regex in _AI_REGEXES)


# ============================================================
# 主入口：三层验证
# ============================================================

def verify_edit(
    section_name: str,
    old_text: str,
    new_text: str,
    all_sections_text: str = "",
    voice_profile: Optional[VoiceFingerprint] = None,
) -> VerificationResult:
    """
    对一次修改执行三层零成本验证。

    Args:
        section_name: 被修改的 section 名称
        old_text: 修改前内容
        new_text: 修改后内容
        all_sections_text: 论文全文（用于交叉引用检查）
        voice_profile: 可选的作者风格指纹

    Returns:
        VerificationResult（非阻塞，Agent 自己决定如何响应）
    """
    all_issues = []
    all_warnings = []

    # Layer 1: 交叉引用一致性
    consistency_ok, c_issues = check_consistency(new_text, all_sections_text)
    all_issues.extend(c_issues)

    # Layer 2: 写作风格漂移
    voice_ok, v_warnings = check_voice_drift(old_text, new_text, voice_profile)
    all_warnings.extend(v_warnings)

    # Layer 3: AI 模式回归
    regression_ok, r_issues = check_ai_regression(old_text, new_text)
    all_issues.extend(r_issues)

    # 整体判断：consistency 和 AI regression 是硬问题，voice drift 是软警告
    passed = consistency_ok and regression_ok

    return VerificationResult(
        passed=passed,
        consistency_ok=consistency_ok,
        voice_drift_ok=voice_ok,
        ai_regression_ok=regression_ok,
        issues=all_issues,
        warnings=all_warnings,
    )


def format_verification_feedback(result: VerificationResult, section_name: str) -> str:
    """
    将验证结果格式化为返回给 Agent 的反馈文本。

    设计原则：简洁、信息密集、不命令 Agent 做什么。
    Agent 看到这个信息后自己判断是否需要修正。
    """
    if result.passed and not result.warnings:
        return f"✓ 修改已应用。验证通过（引用一致 / 无 AI 信号回归 / 风格稳定）。"

    lines = [f"修改已应用于 [{section_name}]。"]

    # 状态总览
    status_parts = []
    status_parts.append(f"引用一致性: {'✓' if result.consistency_ok else '✗'}")
    status_parts.append(f"AI 回归: {'✓' if result.ai_regression_ok else '✗'}")
    status_parts.append(f"风格漂移: {'✓' if result.voice_drift_ok else '⚠'}")
    lines.append("验证: " + " | ".join(status_parts))

    if result.issues:
        lines.append("问题:")
        for issue in result.issues:
            lines.append(f"  · {issue}")

    if result.warnings:
        lines.append("警告 (非阻塞):")
        for w in result.warnings:
            lines.append(f"  · {w}")

    return "\n".join(lines)
