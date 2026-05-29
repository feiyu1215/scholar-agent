"""
core/claim_signal.py — Verifiable Claim Signal Detector

Phase 31: 认知触发信号

设计原则 (COGNITIVE_ANCHOR §4.3 约束-而非-控制):
    这个模块检测论文内容中的 "verifiable claims"——那些无法仅凭
    论文内部信息判断真伪、需要外部知识/搜索来验证的断言。

    当检测到时，在 read_section 返回的内容末尾附加一个信号。
    这个信号不是指令（"你必须搜索"），而是环境信号（"这里有你可能
    需要验证的东西"）。

    类比：人类审稿人的大脑在读到 "no prior work" 时会自动产生
    一个"警铃"感觉——"等等，这个说法需要我去查一下"。我们在
    模拟这个无意识的标记过程。

触发条件（任一命中即触发）:
    1. Novelty claims: "first to", "no prior work", "novel", "to our knowledge"
    2. Priority claims: "state-of-the-art", "outperforms all", "best performance"
    3. Citation inconsistencies: 可疑的年份/venue 格式（简单启发式）

返回:
    - 空字符串: 无可验证 claim
    - 非空字符串: 格式化的信号文本（附加在 section 内容末尾）

成本: 零 LLM 调用，纯正则，<1ms 执行时间
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


# ============================================================
# Claim Pattern Definitions
# ============================================================

# Novelty claims — 声称是第一个做某事
_NOVELTY_PATTERNS = [
    (re.compile(r'\b(first|1st)\s+(to|work|method|approach|framework|paper)\b', re.IGNORECASE),
     "novelty_first"),
    (re.compile(r'\bno\s+prior\s+work\b', re.IGNORECASE),
     "novelty_no_prior"),
    (re.compile(r'\bto\s+(the\s+best\s+of\s+)?our\s+knowledge\b', re.IGNORECASE),
     "novelty_to_our_knowledge"),
    (re.compile(r'\bhas\s+not\s+been\s+(explored|studied|investigated|addressed)\b', re.IGNORECASE),
     "novelty_unexplored"),
    (re.compile(r'\bnov[ea]l\s+(\w+\s+){0,3}(method|approach|framework|technique|contribution|algorithm|model|system)\b', re.IGNORECASE),
     "novelty_novel_method"),
    (re.compile(r'\bfirst\s+comprehensive\b', re.IGNORECASE),
     "novelty_first_comprehensive"),
    (re.compile(r'\bthere\s+exists?\s+no\b', re.IGNORECASE),
     "novelty_no_exists"),
]

# SOTA / superlative claims — 声称比所有人都好
_SOTA_PATTERNS = [
    (re.compile(r'\bstate[\-\s]of[\-\s]the[\-\s]art\b', re.IGNORECASE),
     "sota_claim"),
    (re.compile(r'\boutperforms?\s+(all|every|existing|previous|prior|current)\b', re.IGNORECASE),
     "sota_outperforms_all"),
    (re.compile(r'\b(best|highest|lowest|superior)\s+(performance|accuracy|result|score)\b', re.IGNORECASE),
     "sota_superlative"),
    (re.compile(r'\bconsistently\s+(outperforms?|surpass|beats?|exceeds?)\b', re.IGNORECASE),
     "sota_consistently"),
    (re.compile(r'\bnew\s+state[\-\s]of[\-\s]the[\-\s]art\b', re.IGNORECASE),
     "sota_new"),
]

# Citation patterns that might be wrong (simple heuristics)
_CITATION_SUSPECT_PATTERNS = [
    # Author name + wrong year range (very basic)
    (re.compile(r'\(\w+\s*(et\s+al\.?)?\s*,\s*(19[0-7]\d|18\d\d)\)', re.IGNORECASE),
     "citation_very_old"),
    # Venue mismatch hints: e.g., "NeurIPS 2020" in parenthetical refs
    # This is too noisy to be useful alone, skip for now
]


@dataclass
class ClaimSignal:
    """检测到的 claim signal 集合。"""
    novelty_claims: list[str] = field(default_factory=list)
    sota_claims: list[str] = field(default_factory=list)
    citation_flags: list[str] = field(default_factory=list)

    @property
    def has_signals(self) -> bool:
        return bool(self.novelty_claims or self.sota_claims or self.citation_flags)

    @property
    def signal_count(self) -> int:
        return len(self.novelty_claims) + len(self.sota_claims) + len(self.citation_flags)


def _detect_claims(text: str) -> ClaimSignal:
    """内部检测函数：返回结构化的 claim signal。"""
    signal = ClaimSignal()

    for pattern, label in _NOVELTY_PATTERNS:
        if pattern.search(text):
            # 提取匹配的上下文（前后 30 字符）
            match = pattern.search(text)
            if match:
                start = max(0, match.start() - 30)
                end = min(len(text), match.end() + 30)
                context = text[start:end].replace("\n", " ").strip()
                signal.novelty_claims.append(f"{label}: \"...{context}...\"")

    for pattern, label in _SOTA_PATTERNS:
        if pattern.search(text):
            match = pattern.search(text)
            if match:
                start = max(0, match.start() - 30)
                end = min(len(text), match.end() + 30)
                context = text[start:end].replace("\n", " ").strip()
                signal.sota_claims.append(f"{label}: \"...{context}...\"")

    for pattern, label in _CITATION_SUSPECT_PATTERNS:
        if pattern.search(text):
            match = pattern.search(text)
            if match:
                signal.citation_flags.append(f"{label}: \"{match.group()}\"")

    return signal


def detect_verifiable_claims(text: str) -> str:
    """
    检测文本中的 verifiable claims，返回格式化的信号字符串。

    返回空字符串表示无信号；非空字符串将被附加到 read_section 的返回值末尾。

    信号格式设计：
    - 用 [🔍] 标记，视觉上区分于内容
    - 列出检测到的 claim 类型
    - 不说"你必须搜索"，只说"这些 claim 可通过外部信息验证"
    - Agent 自主决定是否行动

    Args:
        text: 论文 section 的文本内容

    Returns:
        格式化信号字符串（空 = 无信号）
    """
    if not text or len(text) < 100:
        return ""

    signal = _detect_claims(text)

    if not signal.has_signals:
        return ""

    # 构建信号文本
    lines = [
        f"\n\n[🔍 Claim Signal: 检测到 {signal.signal_count} 个可外部验证的断言]"
    ]

    if signal.novelty_claims:
        lines.append(f"  • Novelty claims ({len(signal.novelty_claims)}):")
        for claim in signal.novelty_claims[:3]:  # 最多显示 3 条
            lines.append(f"    - {claim}")

    if signal.sota_claims:
        lines.append(f"  • SOTA/superlative claims ({len(signal.sota_claims)}):")
        for claim in signal.sota_claims[:3]:
            lines.append(f"    - {claim}")

    if signal.citation_flags:
        lines.append(f"  • Citation flags ({len(signal.citation_flags)}):")
        for flag in signal.citation_flags[:2]:
            lines.append(f"    - {flag}")

    lines.append("  → 这些断言无法仅凭论文内部信息验证。")

    return "\n".join(lines)
