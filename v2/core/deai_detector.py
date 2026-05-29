"""
core/deai_detector.py — Programmatic AI-Writing Signal Detector

零 LLM 依赖的 AI 文本信号检测器。从 legacy/tools/deai 迁移核心程序化能力，
为 Agent 提供它自身无法复制的统计和正则匹配分析。

设计原则 (§4.3 constrain, don't control):
    - 这是一个 Agent 按需调用的工具，不是自动触发的 pipeline
    - Agent 决定何时检测、检测哪段文本、如何使用结果
    - 输出结构化结果，Agent 自行决定下一步行动

迁移来源:
    - legacy/tools/deai/constants.py: 50+ 正则模式 + 阈值配置
    - legacy/tools/deai/signals.py: _detect_programmatic_signals() + check_burstiness()
    - 新增: detect_hard_caps() 作为一等能力

依赖: 仅 Python stdlib (re, statistics, dataclasses)
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from typing import Dict, List, Optional


# ============================================================
# Data Structures
# ============================================================

@dataclass
class AISignal:
    """单个检测到的 AI 写作信号。"""
    signal_type: str          # e.g., "RHYTHM_UNIFORMITY", "THROAT_CLEARING"
    tier: str                 # "critical" | "major" | "minor"
    confidence: float         # 0.0-1.0
    description: str          # 人类可读的描述
    fix_suggestion: str       # 修改建议
    evidence: str = ""        # 触发的具体文本片段


@dataclass
class BurstinessResult:
    """句长变异度检测结果。"""
    passed: bool
    cv: float                 # Coefficient of Variation
    mean_length: float
    std_length: float
    sentence_count: int
    longest: int
    shortest: int
    unit: str                 # "words" | "chars"
    warning: str = ""


@dataclass
class DetectionResult:
    """完整检测结果。"""
    signals: List[AISignal] = field(default_factory=list)
    burstiness: Optional[BurstinessResult] = None
    hard_caps_triggered: List[str] = field(default_factory=list)
    
    # 多维度评分
    dimension_scores: Dict[str, float] = field(default_factory=dict)
    overall_score: float = 1.0
    verdict: str = "PASS"     # "PASS" | "FAIL" | "CONDITIONAL_PASS"
    verdict_reason: str = ""
    
    @property
    def signal_count(self) -> int:
        return len(self.signals)
    
    @property
    def critical_count(self) -> int:
        return sum(1 for s in self.signals if s.tier == "critical")
    
    @property
    def major_count(self) -> int:
        return sum(1 for s in self.signals if s.tier == "major")
    
    def summary(self) -> str:
        """简洁的摘要，适合作为 tool 返回给 Agent。"""
        lines = []
        verdict_icons = {"PASS": "✅", "FAIL": "❌", "CONDITIONAL_PASS": "⚠️"}
        icon = verdict_icons.get(self.verdict, "❓")
        lines.append(f"{icon} AI Signal Detection: {self.verdict} (score: {self.overall_score:.3f})")
        lines.append(f"   Reason: {self.verdict_reason}")
        
        if self.hard_caps_triggered:
            lines.append(f"   Hard Caps: {'; '.join(self.hard_caps_triggered)}")
        
        if self.burstiness:
            b = self.burstiness
            status = "✓" if b.passed else "✗"
            lines.append(f"   Burstiness: {status} CV={b.cv:.3f} (mean={b.mean_length:.0f} {b.unit}, n={b.sentence_count})")
        
        if self.dimension_scores:
            dims = " | ".join(f"{k}={v:.2f}" for k, v in self.dimension_scores.items())
            lines.append(f"   Dimensions: {dims}")
        
        if self.signals:
            lines.append(f"\n   Signals ({len(self.signals)}):")
            for sig in self.signals[:8]:
                tier_icon = {"critical": "🔴", "major": "🟡", "minor": "⚪"}.get(sig.tier, "⚪")
                lines.append(f"     {tier_icon} [{sig.signal_type}] conf={sig.confidence:.2f}")
                lines.append(f"        {sig.description}")
                if sig.fix_suggestion:
                    lines.append(f"        → {sig.fix_suggestion}")
            if len(self.signals) > 8:
                lines.append(f"     ... and {len(self.signals) - 8} more")
        
        return "\n".join(lines)


# ============================================================
# Configuration — Patterns and Thresholds
# ============================================================

# --- Signal Tolerance Tiers ---
SIGNAL_TOLERANCE_TIERS: Dict[str, str] = {
    "AI_CLICHE": "critical",
    "INFLATED_SYMBOLISM": "critical",
    "PROMOTIONAL_LANGUAGE": "critical",
    "PROMOTIONAL_ZH": "critical",
    "INFLATED_SYMBOLISM_ZH": "critical",
    "RHYTHM_UNIFORMITY": "major",
    "FORMULAIC_TRANSITIONS": "major",
    "THROAT_CLEARING": "major",
    "CONNECTOR_OVERUSE_ZH": "major",
    "THROAT_CLEARING_ZH": "major",
    "PASSIVE_VOICE_OVERUSE": "minor",
    "PARALLEL_STRUCTURE": "minor",
    "TYPE_TOKEN_RATIO": "minor",
    "PARALLEL_STRUCTURE_ZH": "minor",
}

# --- Dimension Mapping ---
SIGNAL_TO_DIMENSION: Dict[str, str] = {
    "AI_CLICHE": "vocabulary",
    "INFLATED_SYMBOLISM": "vocabulary",
    "PROMOTIONAL_LANGUAGE": "vocabulary",
    "PROMOTIONAL_ZH": "vocabulary",
    "INFLATED_SYMBOLISM_ZH": "vocabulary",
    "RHYTHM_UNIFORMITY": "rhythm",
    "TYPE_TOKEN_RATIO": "vocabulary",
    "FORMULAIC_TRANSITIONS": "connectors",
    "THROAT_CLEARING": "connectors",
    "CONNECTOR_OVERUSE_ZH": "connectors",
    "THROAT_CLEARING_ZH": "connectors",
    "PASSIVE_VOICE_OVERUSE": "voice",
    "PARALLEL_STRUCTURE": "rhythm",
    "PARALLEL_STRUCTURE_ZH": "rhythm",
}
DEFAULT_DIMENSION = "vocabulary"

DIMENSION_WEIGHTS: Dict[str, float] = {
    "vocabulary": 0.25,
    "rhythm": 0.20,
    "connectors": 0.20,
    "punctuation": 0.15,
    "voice": 0.20,
}
DIMENSION_FLOOR = 0.4

# ─── Compiled Regex Patterns ───────────────────────────────────────────────

# AI Cliché words (English)
_AI_CLICHE_RE = [
    re.compile(r"\b(?:delve|delving|delved)\b", re.IGNORECASE),
    re.compile(r"\b(?:tapestry|vibrant\s+tapestry)\b", re.IGNORECASE),
    re.compile(r"\b(?:game[- ]?changer)\b", re.IGNORECASE),
    re.compile(r"\b(?:groundbreaking)\b", re.IGNORECASE),
    re.compile(r"\b(?:paramount)\b", re.IGNORECASE),
    re.compile(r"\brealm\b", re.IGNORECASE),
    re.compile(r"\b(?:synergy|synergistic)\b", re.IGNORECASE),
    re.compile(r"\b(?:pivotal)\b", re.IGNORECASE),
    re.compile(r"\b(?:multifaceted)\b", re.IGNORECASE),
    re.compile(r"\b(?:landscape)\b", re.IGNORECASE),
    re.compile(r"\b(?:transformative)\b", re.IGNORECASE),
    re.compile(r"\b(?:underscores?\s+the\s+(?:importance|need|significance))\b", re.IGNORECASE),
    re.compile(r"\b(?:it\s+is\s+(?:worth|important\s+to)\s+not(?:e|ing)\s+that)\b", re.IGNORECASE),
]
_AI_CLICHE_THRESHOLD = 1

# Formulaic transitions
_FORMULAIC_TRANSITION_RE = [
    re.compile(r"\b(?:Moreover|Furthermore|Additionally|In\s+addition)\b"),
    re.compile(r"\b(?:Consequently|As\s+a\s+result|Therefore|Thus|Hence)\b"),
    re.compile(r"\b(?:Nevertheless|Nonetheless|However|On\s+the\s+other\s+hand)\b"),
    re.compile(r"\b(?:In\s+(?:conclusion|summary|light\s+of))\b"),
    re.compile(r"\b(?:It\s+is\s+(?:important|noteworthy|essential)\s+to\s+(?:note|highlight|emphasize))\b", re.IGNORECASE),
    re.compile(r"\b(?:Notably|Significantly|Importantly|Crucially)\b"),
    re.compile(r"\b(?:To\s+this\s+end|In\s+this\s+(?:regard|context|vein))\b", re.IGNORECASE),
]
_FORMULAIC_TRANSITION_THRESHOLD = 4

# Throat-clearing phrases (English)
_THROAT_CLEARING_RE = [
    re.compile(r"\bIt\s+is\s+(?:important|worth|crucial|essential)\s+to\s+(?:note|highlight|emphasize|mention)\s+that\b", re.IGNORECASE),
    re.compile(r"\bIt\s+(?:should|must)\s+be\s+(?:noted|emphasized|highlighted)\s+that\b", re.IGNORECASE),
    re.compile(r"\bIt\s+goes\s+without\s+saying\s+that\b", re.IGNORECASE),
    re.compile(r"\bAs\s+(?:we\s+all\s+know|is\s+well\s+known|mentioned\s+(?:above|earlier|previously))\b", re.IGNORECASE),
    re.compile(r"\bNeedless\s+to\s+say\b", re.IGNORECASE),
]
_THROAT_CLEARING_THRESHOLD = 2

# Promotional / superlative language (English)
_PROMOTIONAL_RE = [
    re.compile(r"\b(?:groundbreaking|revolutionary|unprecedented|cutting[- ]edge)\b", re.IGNORECASE),
    re.compile(r"\b(?:game[- ]changing|paradigm[- ]shifting|transformative)\b", re.IGNORECASE),
    re.compile(r"\b(?:unparalleled|remarkable|extraordinary|exceptional)\b", re.IGNORECASE),
    re.compile(r"\b(?:highly\s+(?:innovative|significant|impactful))\b", re.IGNORECASE),
]
_PROMOTIONAL_THRESHOLD = 2

# Inflated symbolism (English)
_INFLATED_SYMBOLISM_RE = [
    re.compile(r"\b(?:tapestry\s+of|testament\s+to|beacon\s+of)\b", re.IGNORECASE),
    re.compile(r"\b(?:cornerstone\s+of|landscape\s+of|fabric\s+of)\b", re.IGNORECASE),
    re.compile(r"\b(?:at\s+the\s+(?:heart|core|nexus)\s+of)\b", re.IGNORECASE),
    re.compile(r"\b(?:paving\s+the\s+way|ushering\s+in|heralding)\b", re.IGNORECASE),
    re.compile(r"\b(?:stands\s+as\s+a|serves\s+as\s+a)\s+(?:testament|beacon|reminder)\b", re.IGNORECASE),
]
_INFLATED_SYMBOLISM_THRESHOLD = 2

# Passive voice detection
_PASSIVE_VOICE_RE = re.compile(
    r"\b(?:is|are|was|were|be|been|being)\s+\w+(?:ed|en)\b",
    re.IGNORECASE,
)
_IMPERSONAL_PASSIVE_RE = re.compile(
    r"\bIt\s+(?:is|was|has\s+been)\s+(?:found|shown|demonstrated|observed|noted|suggested|argued)\s+that\b",
    re.IGNORECASE,
)
_PASSIVE_VOICE_RATIO_THRESHOLD = 0.50

# Methods section detection (to avoid false-flagging passive in Methods)
_METHODS_VERB_RE = re.compile(
    r"\b(?:were?\s+(?:collected|measured|analyzed|performed|conducted|prepared|diluted|centrifuged|incubated))\b",
    re.IGNORECASE,
)
_METHODS_VERB_THRESHOLD = 3

# Parallel structure
_PARALLEL_STRUCTURE_RE = re.compile(
    r"(?:(?:by|through|via|in|for|with)\s+\w+(?:ing|tion|ment)(?:,|\s+and)\s*){2,}",
    re.IGNORECASE,
)
_PARALLEL_STRUCTURE_THRESHOLD = 3

# ─── Chinese Patterns ─────────────────────────────────────────────────────

_THROAT_CLEARING_ZH_RE = [
    re.compile(r"值得注意的是"),
    re.compile(r"众所周知"),
    re.compile(r"不言而喻"),
    re.compile(r"毋庸置疑"),
    re.compile(r"不可否认"),
    re.compile(r"事实上"),
    re.compile(r"需要指出的是"),
    re.compile(r"应当注意到"),
    re.compile(r"显而易见"),
    re.compile(r"不难发现"),
    re.compile(r"正如我们所知"),
    re.compile(r"如前所述"),
]
_THROAT_CLEARING_ZH_THRESHOLD = 3

_PROMOTIONAL_ZH_RE = [
    re.compile(r"(?:划时代|里程碑式|开创性|前所未有)"),
    re.compile(r"(?:颠覆性|革命性|突破性|创新性)"),
    re.compile(r"(?:卓越的|非凡的|杰出的|举世瞩目)"),
    re.compile(r"具有重大意义"),
    re.compile(r"极其重要"),
]
_PROMOTIONAL_ZH_THRESHOLD = 2

_CONNECTOR_ZH_RE = [
    re.compile(r"此外"),
    re.compile(r"与此同时"),
    re.compile(r"不仅如此"),
    re.compile(r"更为重要的是"),
    re.compile(r"值得一提的是"),
    re.compile(r"除此之外"),
    re.compile(r"在此基础上"),
    re.compile(r"进一步(?:地|而言)"),
    re.compile(r"另一方面"),
    re.compile(r"综上所述"),
]
_CONNECTOR_ZH_THRESHOLD = 5

_PARALLEL_ZH_RE = [
    re.compile(r"既[^，。]{2,20}又[^，。]{2,20}也[^。]{2,20}"),
    re.compile(r"不仅[^，。]{2,20}而且[^，。]{2,20}更[^。]{2,20}"),
    re.compile(r"一方面[^，。]{2,20}另一方面[^。]{2,20}"),
    re.compile(r"无论是[^，。]{2,20}还是[^，。]{2,20}都[^。]{2,20}"),
]
_PARALLEL_ZH_THRESHOLD = 2

_INFLATED_ZH_RE = [
    re.compile(r"(?:波澜壮阔|气势磅礴|宏伟蓝图)"),
    re.compile(r"(?:灯塔|丰碑|旗帜|标杆)"),
    re.compile(r"(?:浓墨重彩|浓厚氛围|蓬勃发展)"),
    re.compile(r"(?:绽放光芒|熠熠生辉|璀璨夺目)"),
    re.compile(r"(?:历史长河|时代洪流|伟大征程)"),
]
_INFLATED_ZH_THRESHOLD = 2

# Type-Token Ratio (词汇重复度)
_TTR_WINDOW = 5
_TTR_REPEAT_THRESHOLD = 3
_TTR_CONTENT_STOPWORDS = {
    "this", "that", "with", "from", "have", "been", "also",
    "which", "their", "these", "than", "they", "will", "more",
    "such", "other", "into", "most", "some", "only", "over",
    "between", "through", "about", "each", "were", "what",
    "when", "where", "while", "would", "could", "should",
    "does", "very", "much", "many", "well", "just", "even",
}

# Hard Cap thresholds
_HC_CLICHE_THRESHOLD = 3
_HC_CONSECUTIVE_OPENER_THRESHOLD = 3
_HC_BURSTINESS_CV_THRESHOLD = 0.20


# ============================================================
# Helper Functions
# ============================================================

def _is_chinese_text(text: str) -> bool:
    """判断文本是否以中文为主（CJK 字符 > 30%）。"""
    if not text:
        return False
    cjk_count = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    total = len(text.replace(" ", "").replace("\n", ""))
    return (cjk_count / max(total, 1)) > 0.30


def _split_sentences(text: str, is_zh: bool) -> List[str]:
    """将文本拆分为有效句子。"""
    if is_zh:
        sentences = [s.strip() for s in re.split(r'[。！？]', text) if s.strip() and len(s.strip()) >= 6]
    else:
        sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z])', text.strip())
        sentences = [s for s in sentences if s.strip() and len(s.split()) >= 4]
    return sentences


def _get_sentence_lengths(sentences: List[str], is_zh: bool) -> List[int]:
    """计算句子长度列表。"""
    if is_zh:
        _punct_re = re.compile(r'[\s\u3000，。！？、；：\u201c\u201d\u2018\u2019（）《》【】…—·.,!?;:\"\'()\[\]]')
        lengths = [len(_punct_re.sub('', s)) for s in sentences]
    else:
        lengths = [len(s.split()) for s in sentences]
    return [l for l in lengths if l > 0]


# ============================================================
# Core Detection Functions
# ============================================================

def check_burstiness(text: str, min_cv: float = 0.35) -> BurstinessResult:
    """
    句长变异度检测 — 衡量句子长度的变化程度。
    
    AI 生成的文本倾向于均匀的句子长度（低变异系数）。
    人类学术写作通常 CV >= 0.35-0.50。
    
    支持英文（词数）和中文（字符数）。
    """
    is_zh = _is_chinese_text(text)
    sentences = _split_sentences(text, is_zh)
    length_counts = _get_sentence_lengths(sentences, is_zh)
    unit = "chars" if is_zh else "words"
    
    if len(length_counts) < 3:
        return BurstinessResult(
            passed=True, cv=0.0, mean_length=0.0, std_length=0.0,
            sentence_count=len(length_counts),
            longest=max(length_counts) if length_counts else 0,
            shortest=min(length_counts) if length_counts else 0,
            unit=unit,
        )
    
    mean_w = statistics.mean(length_counts)
    std_w = statistics.stdev(length_counts)
    cv = std_w / mean_w if mean_w > 0 else 0.0
    
    passed = cv >= min_cv
    warning = ""
    if not passed:
        if is_zh:
            warning = (
                f"句长变化不足 (CV={cv:.2f}, 需要>={min_cv})。"
                f"句子过于均匀 ({mean_w:.0f}±{std_w:.1f} 字符)。"
                f"建议长短句交替使用。"
            )
        else:
            warning = (
                f"Burstiness too low (CV={cv:.2f}, need >={min_cv}). "
                f"Sentences are too uniform ({mean_w:.0f}±{std_w:.1f} words). "
                f"Mix short punchy sentences (8-12w) with long complex ones (25-40w)."
            )
    
    return BurstinessResult(
        passed=passed,
        cv=round(cv, 3),
        mean_length=round(mean_w, 1),
        std_length=round(std_w, 1),
        sentence_count=len(length_counts),
        longest=max(length_counts),
        shortest=min(length_counts),
        unit=unit,
        warning=warning,
    )


def _detect_hard_caps(text: str, sentences: List[str], is_zh: bool) -> List[str]:
    """
    硬上限检测 — 不可商量的 AI 信号。
    
    HC-1: 3+ AI cliché words (英文)
    HC-2: 3+ 连续的公式化开头 (英文)
    HC-3: 极低的句长变异度 (CV < 0.20)
    """
    reasons = []
    
    # HC-1: Cliché accumulation (English only)
    if not is_zh:
        cliche_count = 0
        cliche_examples: List[str] = []
        for pat in _AI_CLICHE_RE:
            matches = pat.findall(text)
            if matches:
                cliche_count += len(matches)
                cliche_examples.extend(matches[:2])
        if cliche_count >= _HC_CLICHE_THRESHOLD:
            reasons.append(
                f"HC-1: {cliche_count} AI clichés detected "
                f"(e.g., {', '.join(cliche_examples[:3])})"
            )
    
    # HC-2: Consecutive formulaic openers (English only)
    if not is_zh and len(sentences) >= _HC_CONSECUTIVE_OPENER_THRESHOLD:
        consecutive = 0
        max_consecutive = 0
        for sent in sentences:
            has_opener = any(pat.match(sent) for pat in _FORMULAIC_TRANSITION_RE)
            if has_opener:
                consecutive += 1
                max_consecutive = max(max_consecutive, consecutive)
            else:
                consecutive = 0
        if max_consecutive >= _HC_CONSECUTIVE_OPENER_THRESHOLD:
            reasons.append(
                f"HC-2: {max_consecutive} consecutive sentences start with formulaic transitions"
            )
    
    # HC-3: Extremely low burstiness (both languages)
    lengths = _get_sentence_lengths(sentences, is_zh)
    if len(lengths) >= 5:
        mean_w = statistics.mean(lengths)
        std_w = statistics.stdev(lengths)
        cv = std_w / mean_w if mean_w > 0 else 0.0
        if cv < _HC_BURSTINESS_CV_THRESHOLD:
            reasons.append(
                f"HC-3: Near-zero burstiness (CV={cv:.3f} < {_HC_BURSTINESS_CV_THRESHOLD})"
            )
    
    return reasons


def _detect_english_signals(text: str, sentences: List[str]) -> List[AISignal]:
    """检测英文 AI 写作信号。"""
    signals: List[AISignal] = []
    
    # --- RHYTHM_UNIFORMITY (CV < 0.35) ---
    if len(sentences) >= 5:
        lengths = [len(s.split()) for s in sentences]
        lengths = [l for l in lengths if l > 0]
        if len(lengths) >= 5:
            mean_w = statistics.mean(lengths)
            std_w = statistics.stdev(lengths)
            cv = std_w / mean_w if mean_w > 0 else 0.0
            if cv < 0.35:
                confidence = min(0.9, 0.6 + (0.35 - cv))
                signals.append(AISignal(
                    signal_type="RHYTHM_UNIFORMITY",
                    tier="major",
                    confidence=round(confidence, 2),
                    description=f"Sentence length CV={cv:.2f} (need >=0.35). Sentences are too uniform.",
                    fix_suggestion="Mix short and long sentences for natural rhythm variation.",
                    evidence=f"mean={mean_w:.0f} words, std={std_w:.1f}, n={len(lengths)}",
                ))
    
    # --- FORMULAIC_TRANSITIONS ---
    found_transitions: set = set()
    transition_examples: List[str] = []
    for sent in sentences:
        for i, pat in enumerate(_FORMULAIC_TRANSITION_RE):
            if pat.search(sent):
                found_transitions.add(i)
                if len(transition_examples) < 3:
                    transition_examples.append(sent[:80])
    if len(found_transitions) >= _FORMULAIC_TRANSITION_THRESHOLD:
        confidence = min(0.9, 0.6 + (len(found_transitions) - _FORMULAIC_TRANSITION_THRESHOLD) * 0.1)
        signals.append(AISignal(
            signal_type="FORMULAIC_TRANSITIONS",
            tier="major",
            confidence=round(confidence, 2),
            description=f"{len(found_transitions)} distinct formulaic transitions detected.",
            fix_suggestion="Replace some with implicit logical flow or varied connectors.",
            evidence="; ".join(transition_examples[:2]),
        ))
    
    # --- AI_CLICHE ---
    cliche_matches: List[str] = []
    for pat in _AI_CLICHE_RE:
        for m in pat.finditer(text):
            cliche_matches.append(m.group(0))
    if cliche_matches:
        unique_cliches = list(dict.fromkeys(cliche_matches))
        confidence = min(0.95, 0.7 + len(unique_cliches) * 0.05)
        signals.append(AISignal(
            signal_type="AI_CLICHE",
            tier="critical",
            confidence=round(confidence, 2),
            description=f"{len(unique_cliches)} AI cliché word(s) detected.",
            fix_suggestion="Replace with specific, non-cliché alternatives.",
            evidence=", ".join(unique_cliches[:5]),
        ))
    
    # --- THROAT_CLEARING ---
    throat_matches: List[str] = []
    for sent in sentences:
        for pat in _THROAT_CLEARING_RE:
            m = pat.search(sent)
            if m:
                throat_matches.append(m.group(0))
                break
    if len(throat_matches) >= _THROAT_CLEARING_THRESHOLD:
        confidence = min(0.9, 0.65 + (len(throat_matches) - _THROAT_CLEARING_THRESHOLD) * 0.1)
        signals.append(AISignal(
            signal_type="THROAT_CLEARING",
            tier="major",
            confidence=round(confidence, 2),
            description=f"{len(throat_matches)} throat-clearing phrases detected.",
            fix_suggestion="Delete filler phrases and start directly with substantive content.",
            evidence=throat_matches[0],
        ))
    
    # --- PROMOTIONAL_LANGUAGE ---
    promo_matches: List[str] = []
    for pat in _PROMOTIONAL_RE:
        for m in pat.finditer(text):
            promo_matches.append(m.group(0))
    promo_unique = list(dict.fromkeys(promo_matches))
    if len(promo_unique) >= _PROMOTIONAL_THRESHOLD:
        confidence = min(0.9, 0.6 + (len(promo_unique) - _PROMOTIONAL_THRESHOLD) * 0.1)
        examples = promo_unique[:3]
        signals.append(AISignal(
            signal_type="PROMOTIONAL_LANGUAGE",
            tier="critical",
            confidence=round(confidence, 2),
            description=f"{len(promo_unique)} promotional/superlative terms detected.",
            fix_suggestion=f"Replace {', '.join(examples)} with measured academic language.",
            evidence=", ".join(examples),
        ))
    
    # --- INFLATED_SYMBOLISM ---
    symbolism_matches: List[str] = []
    for pat in _INFLATED_SYMBOLISM_RE:
        for m in pat.finditer(text):
            symbolism_matches.append(m.group(0))
    symbolism_unique = list(dict.fromkeys(symbolism_matches))
    if len(symbolism_unique) >= _INFLATED_SYMBOLISM_THRESHOLD:
        confidence = min(0.9, 0.65 + (len(symbolism_unique) - _INFLATED_SYMBOLISM_THRESHOLD) * 0.15)
        examples = symbolism_unique[:3]
        signals.append(AISignal(
            signal_type="INFLATED_SYMBOLISM",
            tier="critical",
            confidence=round(confidence, 2),
            description=f"{len(symbolism_unique)} inflated symbolism phrases detected.",
            fix_suggestion=f"Replace grand metaphors ({', '.join(examples)}) with concrete descriptions.",
            evidence=", ".join(examples),
        ))
    
    # --- PASSIVE_VOICE_OVERUSE ---
    # Skip if text appears to be a Methods section
    methods_count = len(_METHODS_VERB_RE.findall(text))
    is_likely_methods = methods_count >= _METHODS_VERB_THRESHOLD
    
    if len(sentences) >= 4 and not is_likely_methods:
        passive_count = 0
        passive_examples: List[str] = []
        for sent in sentences:
            if _PASSIVE_VOICE_RE.search(sent) or _IMPERSONAL_PASSIVE_RE.search(sent):
                passive_count += 1
                if len(passive_examples) < 2:
                    passive_examples.append(sent[:80])
        passive_ratio = passive_count / len(sentences)
        if passive_ratio >= _PASSIVE_VOICE_RATIO_THRESHOLD and passive_count >= 3:
            confidence = min(0.85, 0.55 + (passive_ratio - _PASSIVE_VOICE_RATIO_THRESHOLD) * 2)
            signals.append(AISignal(
                signal_type="PASSIVE_VOICE_OVERUSE",
                tier="minor",
                confidence=round(confidence, 2),
                description=f"{passive_count}/{len(sentences)} sentences ({passive_ratio:.0%}) use passive voice.",
                fix_suggestion="Convert some to active: 'We found...' instead of 'It was found that...'",
                evidence=passive_examples[0] if passive_examples else "",
            ))
    
    # --- PARALLEL_STRUCTURE ---
    parallel_count = 0
    parallel_examples: List[str] = []
    for sent in sentences:
        if _PARALLEL_STRUCTURE_RE.search(sent):
            parallel_count += 1
            if len(parallel_examples) < 2:
                parallel_examples.append(sent[:100])
    if parallel_count >= _PARALLEL_STRUCTURE_THRESHOLD:
        confidence = min(0.9, 0.6 + (parallel_count - _PARALLEL_STRUCTURE_THRESHOLD) * 0.15)
        signals.append(AISignal(
            signal_type="PARALLEL_STRUCTURE",
            tier="minor",
            confidence=round(confidence, 2),
            description=f"{parallel_count} sentences use triple-parallel construction.",
            fix_suggestion="Vary sentence structure — not every list needs three items in parallel form.",
            evidence=parallel_examples[0] if parallel_examples else "",
        ))
    
    # --- TYPE_TOKEN_RATIO ---
    if len(sentences) >= _TTR_WINDOW:
        sent_words = []
        for s in sentences:
            words = set(
                w.lower() for w in re.findall(r'\b[a-zA-Z]{4,}\b', s)
                if w.lower() not in _TTR_CONTENT_STOPWORDS
            )
            sent_words.append(words)
        
        worst_word = ""
        worst_count = 0
        for start in range(len(sent_words) - _TTR_WINDOW + 1):
            window = sent_words[start:start + _TTR_WINDOW]
            word_freq: Dict[str, int] = {}
            for sw in window:
                for w in sw:
                    word_freq[w] = word_freq.get(w, 0) + 1
            for w, count in word_freq.items():
                if count >= _TTR_REPEAT_THRESHOLD and count > worst_count:
                    worst_count = count
                    worst_word = w
        
        if worst_count >= _TTR_REPEAT_THRESHOLD:
            confidence = min(0.85, 0.55 + (worst_count - _TTR_REPEAT_THRESHOLD) * 0.1)
            signals.append(AISignal(
                signal_type="TYPE_TOKEN_RATIO",
                tier="minor",
                confidence=round(confidence, 2),
                description=f"Word '{worst_word}' appears in {worst_count}/{_TTR_WINDOW} adjacent sentences.",
                fix_suggestion=f"Use synonyms, pronouns, or restructure to reduce repetition of '{worst_word}'.",
                evidence=f"'{worst_word}' in {worst_count}/{_TTR_WINDOW} sentences",
            ))
    
    return signals


def _detect_chinese_signals(text: str) -> List[AISignal]:
    """检测中文 AI 写作信号。"""
    signals: List[AISignal] = []
    zh_sentences = [s.strip() for s in re.split(r'[。！？]', text) if s.strip() and len(s.strip()) >= 6]
    
    # --- THROAT_CLEARING_ZH ---
    zh_throat_matches: List[str] = []
    for sent in zh_sentences:
        for pat in _THROAT_CLEARING_ZH_RE:
            m = pat.search(sent)
            if m:
                zh_throat_matches.append(m.group(0))
                break
    if len(zh_throat_matches) >= _THROAT_CLEARING_ZH_THRESHOLD:
        confidence = min(0.9, 0.6 + (len(zh_throat_matches) - _THROAT_CLEARING_ZH_THRESHOLD) * 0.1)
        signals.append(AISignal(
            signal_type="THROAT_CLEARING_ZH",
            tier="major",
            confidence=round(confidence, 2),
            description=f"检测到{len(zh_throat_matches)}处中文套话（如「{zh_throat_matches[0]}」）。",
            fix_suggestion="删除这些空话，直接陈述核心内容。",
            evidence=", ".join(zh_throat_matches[:3]),
        ))
    
    # --- PROMOTIONAL_ZH ---
    zh_promo_matches: List[str] = []
    for pat in _PROMOTIONAL_ZH_RE:
        for m in pat.finditer(text):
            zh_promo_matches.append(m.group(0))
    zh_promo_unique = list(dict.fromkeys(zh_promo_matches))
    if len(zh_promo_unique) >= _PROMOTIONAL_ZH_THRESHOLD:
        confidence = min(0.9, 0.65 + (len(zh_promo_unique) - _PROMOTIONAL_ZH_THRESHOLD) * 0.1)
        examples = zh_promo_unique[:3]
        signals.append(AISignal(
            signal_type="PROMOTIONAL_ZH",
            tier="critical",
            confidence=round(confidence, 2),
            description=f"检测到{len(zh_promo_unique)}处宣传式表达：{', '.join(examples)}。",
            fix_suggestion="改用平实、具体的学术语言。",
            evidence=", ".join(examples),
        ))
    
    # --- CONNECTOR_OVERUSE_ZH ---
    zh_connector_matches: List[str] = []
    for pat in _CONNECTOR_ZH_RE:
        for m in pat.finditer(text):
            zh_connector_matches.append(m.group(0))
    if len(zh_connector_matches) >= _CONNECTOR_ZH_THRESHOLD:
        confidence = min(0.85, 0.55 + (len(zh_connector_matches) - _CONNECTOR_ZH_THRESHOLD) * 0.05)
        examples = list(dict.fromkeys(zh_connector_matches))[:4]
        signals.append(AISignal(
            signal_type="CONNECTOR_OVERUSE_ZH",
            tier="major",
            confidence=round(confidence, 2),
            description=f"检测到{len(zh_connector_matches)}处连接词/过渡词堆砌。",
            fix_suggestion="减少显式逻辑连接词，用自然的语序和内在逻辑衔接。",
            evidence=", ".join(examples),
        ))
    
    # --- PARALLEL_STRUCTURE_ZH ---
    zh_parallel_count = 0
    zh_parallel_examples: List[str] = []
    for pat in _PARALLEL_ZH_RE:
        for m in pat.finditer(text):
            zh_parallel_count += 1
            if len(zh_parallel_examples) < 2:
                zh_parallel_examples.append(m.group(0)[:60])
    if zh_parallel_count >= _PARALLEL_ZH_THRESHOLD:
        confidence = min(0.85, 0.6 + (zh_parallel_count - _PARALLEL_ZH_THRESHOLD) * 0.15)
        signals.append(AISignal(
            signal_type="PARALLEL_STRUCTURE_ZH",
            tier="minor",
            confidence=round(confidence, 2),
            description=f"检测到{zh_parallel_count}处排比/递进三段式结构。AI倾向于使用三段排比。",
            fix_suggestion="用更自然的散文句式替代机械的排比结构。",
            evidence=zh_parallel_examples[0] if zh_parallel_examples else "",
        ))
    
    # --- INFLATED_SYMBOLISM_ZH ---
    zh_symbolism_matches: List[str] = []
    for pat in _INFLATED_ZH_RE:
        for m in pat.finditer(text):
            zh_symbolism_matches.append(m.group(0))
    zh_symbolism_unique = list(dict.fromkeys(zh_symbolism_matches))
    if len(zh_symbolism_unique) >= _INFLATED_ZH_THRESHOLD:
        confidence = min(0.9, 0.65 + (len(zh_symbolism_unique) - _INFLATED_ZH_THRESHOLD) * 0.15)
        examples = zh_symbolism_unique[:3]
        signals.append(AISignal(
            signal_type="INFLATED_SYMBOLISM_ZH",
            tier="critical",
            confidence=round(confidence, 2),
            description=f"检测到{len(zh_symbolism_unique)}处华丽辞藻/堆砌修辞：{', '.join(examples)}。",
            fix_suggestion="用具体、精确的表述替代空泛修辞。",
            evidence=", ".join(examples),
        ))
    
    # --- RHYTHM_UNIFORMITY (中文版) ---
    if len(zh_sentences) >= 5:
        _punct_re = re.compile(r'[\s\u3000，。！？、；：\u201c\u201d\u2018\u2019（）《》【】…—·.,!?;:\"\'()\[\]]')
        lengths = [len(_punct_re.sub('', s)) for s in zh_sentences]
        lengths = [l for l in lengths if l >= 4]
        if len(lengths) >= 5:
            mean_w = statistics.mean(lengths)
            std_w = statistics.stdev(lengths)
            cv = std_w / mean_w if mean_w > 0 else 0.0
            if cv < 0.35:
                confidence = min(0.9, 0.6 + (0.35 - cv))
                signals.append(AISignal(
                    signal_type="RHYTHM_UNIFORMITY",
                    tier="major",
                    confidence=round(confidence, 2),
                    description=f"句长变异度不足 CV={cv:.2f} (需>=0.35)。句子过于均匀。",
                    fix_suggestion="长短句交替使用，避免机械的均匀节奏。",
                    evidence=f"mean={mean_w:.0f} chars, std={std_w:.1f}, n={len(lengths)}",
                ))
    
    return signals


def _compute_dimension_scores(signals: List[AISignal]) -> Dict[str, float]:
    """根据检测到的信号计算多维度评分。"""
    penalties: Dict[str, float] = {dim: 0.0 for dim in DIMENSION_WEIGHTS}
    
    for sig in signals:
        dim = SIGNAL_TO_DIMENSION.get(sig.signal_type, DEFAULT_DIMENSION)
        penalty = sig.confidence * 0.15
        penalties[dim] += penalty
    
    return {dim: max(0.0, round(1.0 - penalties[dim], 3)) for dim in DIMENSION_WEIGHTS}


def _compute_overall_score(dim_scores: Dict[str, float]) -> float:
    """加权计算总分。"""
    return round(sum(
        dim_scores.get(dim, 1.0) * weight
        for dim, weight in DIMENSION_WEIGHTS.items()
    ), 3)


def _apply_tiered_judgment(signals: List[AISignal], dim_scores: Dict[str, float]) -> tuple:
    """
    应用分层判定规则。
    
    Returns: (verdict, reason)
    """
    critical_hits: List[str] = []
    major_counts: Dict[str, int] = {}
    minor_total = 0
    
    for sig in signals:
        tier = SIGNAL_TOLERANCE_TIERS.get(sig.signal_type, "major")
        if tier == "critical" and sig.confidence >= 0.7:
            critical_hits.append(f"{sig.signal_type} (conf={sig.confidence:.2f})")
        elif tier == "major":
            major_counts[sig.signal_type] = major_counts.get(sig.signal_type, 0) + 1
        elif tier == "minor":
            minor_total += 1
    
    # Rule 1: Critical zero-tolerance
    if critical_hits:
        return "FAIL", f"Critical signal(s): {', '.join(critical_hits[:3])}"
    
    # Rule 2: Dimension floor
    for dim, score in dim_scores.items():
        if score < DIMENSION_FLOOR:
            return "FAIL", f"Dimension '{dim}' below floor ({score:.2f} < {DIMENSION_FLOOR})"
    
    # Rule 3: Major signal accumulation
    major_violators = [k for k, v in major_counts.items() if v >= 2]
    if major_violators:
        return "FAIL", f"Major signal(s) exceeded threshold: {', '.join(major_violators)}"
    
    # Rule 4: Minor signal flood
    if minor_total >= 4:
        return "FAIL", f"Excessive minor signals: {minor_total} (threshold: 4)"
    
    # Rule 5: PASS
    return "PASS", "All tiered tolerance checks passed"


# ============================================================
# Public API — Agent 调用入口
# ============================================================

def detect_ai_signals(text: str) -> DetectionResult:
    """
    对文本进行全面的 AI 写作信号检测。
    
    这是 Agent 调用的主入口。返回结构化的 DetectionResult，
    包含信号列表、句长统计、维度评分和最终判定。
    
    纯程序化，零 LLM 调用，执行速度极快。
    
    Args:
        text: 待检测的文本（支持中英文自动识别）
    
    Returns:
        DetectionResult with complete analysis
    """
    if not text or len(text.strip()) < 50:
        return DetectionResult(
            verdict="PASS",
            verdict_reason="Text too short for meaningful analysis",
            overall_score=1.0,
        )
    
    is_zh = _is_chinese_text(text)
    sentences = _split_sentences(text, is_zh)
    
    # 1. Hard Caps Detection
    hard_caps = _detect_hard_caps(text, sentences, is_zh)
    
    # 2. Signal Detection (language-specific)
    signals: List[AISignal] = []
    if is_zh:
        signals = _detect_chinese_signals(text)
    else:
        signals = _detect_english_signals(text, sentences)
    
    # 3. Burstiness Analysis
    burstiness = check_burstiness(text)
    
    # 4. Dimension Scores
    dim_scores = _compute_dimension_scores(signals)
    
    # Apply hard cap penalties to dimension scores
    if hard_caps:
        # HC-1 (clichés) → penalize vocabulary
        if any("HC-1" in hc for hc in hard_caps):
            dim_scores["vocabulary"] = min(dim_scores["vocabulary"], 0.3)
        # HC-3 (burstiness) → penalize rhythm
        if any("HC-3" in hc for hc in hard_caps):
            dim_scores["rhythm"] = min(dim_scores["rhythm"], 0.3)
    
    # 5. Overall Score
    overall_score = _compute_overall_score(dim_scores)
    
    # 6. Tiered Judgment
    verdict, verdict_reason = _apply_tiered_judgment(signals, dim_scores)
    
    # Hard caps override: if any HC triggered, force FAIL
    if hard_caps and verdict == "PASS":
        verdict = "FAIL"
        verdict_reason = f"Hard caps triggered: {'; '.join(hard_caps)}"
    
    return DetectionResult(
        signals=signals,
        burstiness=burstiness,
        hard_caps_triggered=hard_caps,
        dimension_scores=dim_scores,
        overall_score=overall_score,
        verdict=verdict,
        verdict_reason=verdict_reason,
    )
