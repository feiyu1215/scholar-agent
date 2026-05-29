"""
tools/deai/constants.py - All constants, patterns, keyword lists, prompts, and dataclasses.

Split from monolithic deai_engine.py for maintainability.
"""

from __future__ import annotations

import re
import statistics
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field, asdict

from config import load_thresholds
from llm.client import LLMClient
from llm.router import get_model_for_task
from utils.voice_profile import load_voice_profile, get_voice_constraints, check_voice_drift
from utils.author_profile import load_profile, get_profile_context_for_prompt

_CFG = load_thresholds().get("deai_engine", {})
_SIGNALS_CFG = _CFG.get("signals", {})
_SIGNALS_ZH_CFG = _CFG.get("signals_zh", {})
_HC_CFG = _CFG.get("hard_caps", {})

WORKSPACE = Path(".workspace")
RULES_PATH = Path("skills/deai_rules.md")

MAX_RETRIES = _CFG.get("max_retries", 2)
PASS_THRESHOLD = _CFG.get("pass_threshold", 0.7)
IMPROVEMENT_THRESHOLD = _CFG.get("improvement_threshold", 0.05)  # Minimum score improvement to continue retrying

# ─── Tiered Signal Tolerance (TODO-1) ─────────────────────────────────────────
#
# Signals are classified into three tolerance tiers:
#   CRITICAL: Zero-tolerance — any detection = FAIL regardless of overall score
#   MAJOR:    Standard threshold (≥ 2 detections in same dimension = FAIL)
#   MINOR:    Lenient — only fail if > 3 detections across text
#
# This replaces the flat `overall_score >= 0.7` judgment.

SIGNAL_TOLERANCE_TIERS = {
    # ── CRITICAL (Tier 1): zero tolerance, any detection = FAIL ──
    "AI_VOCABULARY": "critical",
    "INFLATED_SYMBOLISM": "critical",
    "PROMOTIONAL_LANGUAGE": "critical",
    "PROMOTIONAL_TONE": "critical",

    # ── MAJOR (Tier 2): max 1 per text, 2+ = FAIL ──
    "TRICOLON": "major",
    "RHYTHM_UNIFORMITY": "major",
    "CONNECTOR_STACKING": "major",
    "EMPTY_PROGRESSIVE": "major",
    "VAGUE_ATTRIBUTION": "major",
    "FORMULAIC_TRANSITIONS": "major",
    "HEDGE_STACKING": "major",
    "THROAT_CLEARING": "major",

    # ── MINOR (Tier 3): lenient, 4+ across whole text = FAIL ──
    "HEDGE_OPENERS": "minor",
    "PASSIVE_VOICE_OVERUSE": "minor",
    "COPULA_AVOIDANCE": "minor",
    "EM_DASH_OVERUSE": "minor",
    "NEGATION_PARALLEL": "minor",
    "TYPE_TOKEN_RATIO": "minor",
    "RESOLUTION_CLOSER": "minor",
    "PARALLEL_STRUCTURE": "minor",

    # ── Chinese signals (ZH) — mirror English counterparts ──
    "PROMOTIONAL_ZH": "critical",        # mirrors PROMOTIONAL_LANGUAGE
    "INFLATED_SYMBOLISM_ZH": "critical", # mirrors INFLATED_SYMBOLISM
    "THROAT_CLEARING_ZH": "major",       # mirrors THROAT_CLEARING
    "CONNECTOR_OVERUSE_ZH": "major",     # mirrors FORMULAIC_TRANSITIONS
    "PARALLEL_STRUCTURE_ZH": "minor",    # mirrors PARALLEL_STRUCTURE
}

# Default tier for unknown signal types
DEFAULT_SIGNAL_TIER = "major"

# Conditional PASS: when doom_loop max retries used up but score is within this
# range of baseline, grant conditional pass instead of hard FAIL.
CONDITIONAL_PASS_TOLERANCE = _CFG.get("conditional_pass_tolerance", 0.05)  # within 5% of baseline score

# ─── Multi-Dimension Scoring (TODO-2) ─────────────────────────────────────────
#
# De-AI audit quality decomposed into 5 weighted dimensions.
# Each dimension: 0.0-1.0, weighted sum → overall_score.
# Any single dimension < 0.4 triggers "dimension FAIL" regardless of overall.

DIMENSION_WEIGHTS = {
    "vocabulary":    0.25,  # Inflated symbolism, promotional language, AI words
    "rhythm":        0.20,  # Sentence length variation, burstiness, tricolon
    "connectors":    0.20,  # Formulaic transitions, filler phrases, stacking
    "punctuation":   0.15,  # Em-dash overuse, colon patterns, formatting
    "voice":         0.20,  # Voice drift, register shift, consistency
}

DIMENSION_FLOOR = _CFG.get("dimension_floor", 0.4)  # If any dimension < this, force FAIL

# Map: signal_type → dimension
SIGNAL_TO_DIMENSION = {
    # vocabulary
    "AI_VOCABULARY": "vocabulary",
    "INFLATED_SYMBOLISM": "vocabulary",
    "PROMOTIONAL_LANGUAGE": "vocabulary",
    "PROMOTIONAL_TONE": "vocabulary",
    "COPULA_AVOIDANCE": "vocabulary",
    # rhythm
    "TRICOLON": "rhythm",
    "RHYTHM_UNIFORMITY": "rhythm",
    "PARALLEL_STRUCTURE": "rhythm",
    "TYPE_TOKEN_RATIO": "rhythm",
    "NEGATION_PARALLEL": "rhythm",
    # connectors
    "CONNECTOR_STACKING": "connectors",
    "HEDGE_OPENERS": "connectors",
    "HEDGE_STACKING": "connectors",
    "FORMULAIC_TRANSITIONS": "connectors",
    "THROAT_CLEARING": "connectors",
    "VAGUE_ATTRIBUTION": "connectors",
    "EMPTY_PROGRESSIVE": "connectors",
    # punctuation
    "EM_DASH_OVERUSE": "punctuation",
    "RESOLUTION_CLOSER": "punctuation",
    # voice
    "PASSIVE_VOICE_OVERUSE": "voice",
    # Chinese signals (ZH)
    "THROAT_CLEARING_ZH": "connectors",
    "PROMOTIONAL_ZH": "vocabulary",
    "CONNECTOR_OVERUSE_ZH": "connectors",
    "PARALLEL_STRUCTURE_ZH": "rhythm",
    "INFLATED_SYMBOLISM_ZH": "vocabulary",
}

DEFAULT_DIMENSION = "vocabulary"

# ─── Hard Caps (TODO-3) ───────────────────────────────────────────────────────
#
# Hard Caps are programmatic (zero LLM cost) checks that clamp dimension scores
# when specific severe patterns are detected in the text. Even if the overall
# score is high, a hard cap prevents a dimension from scoring above its cap value.
#
# Unlike signal-based penalties (which degrade gradually), hard caps are binary:
# either triggered or not. They represent patterns so characteristic of AI writing
# that their presence alone warrants score limitation.

# HC-1: Typical AI clichés / boilerplate phrases → vocabulary capped at 0.60
# These are phrases almost never used by human academic writers.
AI_CLICHE_PATTERNS = [
    r"\bit is worth noting that\b",
    r"\bit('|')s worth noting that\b",
    r"\bin today('|')s rapidly evolving\b",
    r"\bin this day and age\b",
    r"\bdelve(?:s|d)? (?:into|deeper)\b",
    r"\bunlock(?:s|ing)? (?:the (?:full |true )?potential|new possibilities|insights)\b",
    r"\bleverage(?:s|d)? (?:the power|cutting[- ]edge|innovative)\b",
    r"\ba testament to\b",
    r"\btapestry of\b",
    r"\bparadigm shift\b",
    r"\bgame[- ]?changer\b",
    r"\bseamless(?:ly)? integrat",
    r"\bholistic approach\b",
    r"\brobust (?:framework|solution|approach)\b",
    r"\bsynerg(?:y|ies|istic)\b",
    r"\bgroundbreaking\b",
    r"\bcutting[- ]edge\b",
    r"\btransformative (?:potential|impact|power)\b",
    r"\bpivotal (?:role|moment|point)\b",
    r"\bin conclusion,? (?:it is|this)\b",
    r"\boverall,? (?:it is|this) (?:evident|clear)\b",
]

# Compile for efficiency
_AI_CLICHE_RE = [re.compile(p, re.IGNORECASE) for p in AI_CLICHE_PATTERNS]

# HC-1 config
HC_VOCABULARY_CAP = _HC_CFG.get("vocabulary_cap", 0.60)        # Cap vocabulary dimension at this value
HC_VOCABULARY_THRESHOLD = _HC_CFG.get("vocabulary_threshold", 2)     # Need 2+ distinct cliché matches to trigger

# HC-2: Consecutive sentences with same syntactic opener pattern → rhythm capped at 0.50
# Detects 3+ consecutive sentences starting with the same pattern (e.g., "This ...", "The ...")
HC_RHYTHM_CONSECUTIVE_CAP = _HC_CFG.get("rhythm_consecutive_cap", 0.50)
HC_RHYTHM_CONSECUTIVE_THRESHOLD = _HC_CFG.get("rhythm_consecutive_threshold", 3)  # 3+ consecutive same-opener sentences

# HC-3: Near-zero burstiness (sentence length CV < 0.20) → rhythm capped at 0.40
# CV < 0.20 means sentences are extremely uniform in length — a strong AI fingerprint.
HC_RHYTHM_BURSTINESS_CAP = _HC_CFG.get("rhythm_burstiness_cap", 0.40)
HC_RHYTHM_BURSTINESS_CV_THRESHOLD = _HC_CFG.get("rhythm_burstiness_cv_threshold", 0.20)


@dataclass
class HardCapResult:
    """Result of hard cap detection (TODO-3). Zero LLM cost."""
    triggered: bool = False
    caps: Dict[str, float] = field(default_factory=dict)  # dimension → max allowed score
    reasons: List[str] = field(default_factory=list)       # Human-readable explanations
    details: Dict[str, any] = field(default_factory=dict)  # Debug info

    def apply_to(self, dimensions: "DimensionScores") -> "DimensionScores":
        """Clamp dimension scores to their cap values. Returns new DimensionScores."""
        vocab = min(dimensions.vocabulary, self.caps.get("vocabulary", 1.0))
        rhythm = min(dimensions.rhythm, self.caps.get("rhythm", 1.0))
        conn = min(dimensions.connectors, self.caps.get("connectors", 1.0))
        punct = min(dimensions.punctuation, self.caps.get("punctuation", 1.0))
        voice = min(dimensions.voice, self.caps.get("voice", 1.0))
        return DimensionScores(
            vocabulary=vocab, rhythm=rhythm, connectors=conn,
            punctuation=punct, voice=voice,
        )


# ─── Programmatic Signal Injection (P7) ───────────────────────────────────────
#
# Lightweight zero-LLM detectors for signals that the LLM consistently misses.
# These run in deai_audit() BEFORE dimension scoring, so injected signals
# participate in both DimensionScores computation and TieredJudgment.
#
# Each detector returns a list of AISignal (may be empty).

# FORMULAIC_TRANSITIONS patterns: transitions that AI overuses
_FORMULAIC_TRANSITION_PATTERNS = [
    r"\bFurthermore\b",
    r"\bMoreover\b",
    r"\bAdditionally\b",
    r"\bIn addition\b",
    r"\bConsequently\b",
    r"\bNevertheless\b",
    r"\bNonetheless\b",
    r"\bSubsequently\b",
    r"\bSpecifically\b",
    r"\bNotably\b",
    r"\bImportantly\b",
    r"\bSignificantly\b",
]
_FORMULAIC_TRANSITION_RE = [re.compile(p) for p in _FORMULAIC_TRANSITION_PATTERNS]
_FORMULAIC_TRANSITION_THRESHOLD = _SIGNALS_CFG.get("formulaic_transition_threshold", 3)  # 3+ distinct formulaic transitions → signal

# TYPE_TOKEN_RATIO: repeated content words across adjacent sentences
_TTR_CONTENT_STOPWORDS = {
    # Function words
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "to", "of", "in", "for",
    "on", "with", "at", "by", "from", "as", "into", "through", "during",
    "before", "after", "above", "below", "between", "under", "this", "that",
    "these", "those", "it", "its", "they", "them", "their", "we", "our",
    "not", "no", "nor", "but", "or", "and", "if", "then", "than", "so",
    "which", "who", "whom", "what", "where", "when", "how", "each", "every",
    "all", "both", "few", "more", "most", "other", "some", "such", "only",
    "also", "very", "often", "however", "therefore", "thus", "hence",
    # Academic high-frequency words (natural in scholarly text, not AI-specific)
    "study", "studies", "research", "results", "result", "analysis",
    "method", "methods", "methodology", "approach", "data", "model",
    "paper", "section", "figure", "table", "based", "using", "used",
    "show", "shows", "shown", "find", "found", "suggest", "suggests",
    "provide", "provides", "propose", "proposed", "present", "presented",
    "perform", "performed", "demonstrate", "demonstrates", "indicate",
    "effect", "effects", "impact", "significant", "significantly",
    "different", "similar", "compared", "respectively", "given",
}
_TTR_WINDOW = _SIGNALS_CFG.get("ttr_window", 5)           # sliding window of N sentences
_TTR_REPEAT_THRESHOLD = _SIGNALS_CFG.get("ttr_repeat_threshold", 3)  # same content word appears in 3+ sentences in window

# ── THROAT_CLEARING: formulaic filler phrases (P10) ──────────────────────────
_THROAT_CLEARING_PATTERNS = [
    r"\bIt is (?:important|worth|crucial|essential|necessary) to (?:note|mention|emphasize|highlight|acknowledge|recognize|point out) that\b",
    r"\bIt bears (?:mentioning|noting|emphasizing) that\b",
    r"\bIt should be (?:noted|mentioned|emphasized|highlighted|pointed out) that\b",
    r"\bIt is worth (?:noting|mentioning|pointing out) that\b",
    r"\bIt (?:is|remains) (?:important|imperative|critical) (?:to acknowledge|to recognize|that we recognize) that\b",
    r"\bAs (?:previously|already) (?:mentioned|noted|discussed|stated|indicated)\b",
    r"\bIt (?:has been|was|is) (?:widely )? (?:observed|demonstrated|recognized|noted|shown|found|reported|established|argued|suggested) that\b",
]
_THROAT_CLEARING_RE = [re.compile(p, re.IGNORECASE) for p in _THROAT_CLEARING_PATTERNS]
_THROAT_CLEARING_THRESHOLD = _SIGNALS_CFG.get("throat_clearing_threshold", 2)  # 2+ throat-clearing phrases → signal

# ── PROMOTIONAL: superlative/marketing language (P10) ─────────────────────────
_PROMOTIONAL_PATTERNS = [
    r"\bgroundbreaking\b",
    r"\brevolutionary\b",
    r"\bunprecedented\b",
    r"\bunparalleled\b",
    r"\bgame[- ]?changing\b",
    r"\bparadigm[- ]?shifting\b",
    r"\btransformative (?:potential|impact|power|insights?|approach)\b",
    r"\bremarkable (?:success|achievement|progress|improvement|advance)\b",
    r"\bextraordinary (?:potential|impact|success)\b",
    r"\bpivotal (?:role|moment|contribution|advance)\b",
    r"\bgold standard\b",
    r"\bcutting[- ]?edge\b",
    r"\bstate[- ]of[- ]the[- ]art\b",
]
_PROMOTIONAL_RE = [re.compile(p, re.IGNORECASE) for p in _PROMOTIONAL_PATTERNS]
_PROMOTIONAL_THRESHOLD = _SIGNALS_CFG.get("promotional_threshold", 2)  # 2+ distinct promotional terms → signal

# ── INFLATED_SYMBOLISM: grand metaphors typical of AI writing (P10) ───────────
_INFLATED_SYMBOLISM_PATTERNS = [
    r"\b(?:rich |vibrant )?tapestry of\b",
    r"\bstands as a testament to\b",
    r"\bserves as a testament to\b",
    r"\b(?:is|remains|stands as) a (?:beacon|pillar|cornerstone) of\b",
    r"\bbedrock of\b",
    r"\bfabric of (?:society|our|the)\b",
    r"\bmosaic of\b",
    r"\bcrucible of\b",
    r"\bweaving together\b",
    r"\bfertile ground for\b",
    r"\bat the (?:very )?heart of\b",
    r"\bnexus of\b",
    r"\bepitome of\b",
    r"\bembodiment of\b",
    r"\ba testament to\b",
    r"\blens(?:es)? (?:through which|that)\b",
]
_INFLATED_SYMBOLISM_RE = [re.compile(p, re.IGNORECASE) for p in _INFLATED_SYMBOLISM_PATTERNS]
_INFLATED_SYMBOLISM_THRESHOLD = _SIGNALS_CFG.get("inflated_symbolism_threshold", 2)  # 2+ inflated metaphors → signal

# ── PASSIVE_VOICE_OVERUSE: too many passive constructions (P10) ───────────────
# Matches: "is/are/was/were/been/being + past participle"
_PASSIVE_VOICE_RE = re.compile(
    r"\b(?:is|are|was|were|been|being)\s+(?:\w+ly\s+)?(?:\w+ed|written|shown|given|taken|known|seen|done|made|found|held|built|run|set|put|cut|let|read)\b",
    re.IGNORECASE,
)
# Impersonal passive: "It has been/was observed/demonstrated/... that"
_IMPERSONAL_PASSIVE_RE = re.compile(
    r"\bIt (?:has been|was|is|can be) (?:\w+ly\s+)?(?:observed|demonstrated|recognized|noted|shown|found|reported|established|argued|suggested|determined|concluded|confirmed|verified|hypothesized|proposed)\b",
    re.IGNORECASE,
)
_PASSIVE_VOICE_RATIO_THRESHOLD = _SIGNALS_CFG.get("passive_voice_ratio_threshold", 0.50)  # >50% of sentences passive → signal
# Methods-section heuristic: if text is full of experimental procedure verbs,
# passive voice is legitimate. Suppress detector when enough method-verbs found.
_METHODS_VERB_PATTERNS = re.compile(
    r"\b(?:recruit|enroll|administer|randomiz|counterbalance|assign|collect|record|"
    r"measure|analyz|obtain|consent|instruct|calibrat|pipett|centrifug|incubat|"
    r"dissolv|dilut|inject|anesthetiz|sacrific|perfus|dissect|homogeniz|"
    r"participants? were|subjects? were|samples? were|data were|"
    r"IRB|informed consent|exclusion criteria|inclusion criteria)\w*\b",
    re.IGNORECASE,
)
_METHODS_VERB_THRESHOLD = _SIGNALS_CFG.get("methods_verb_threshold", 4)  # 4+ method-verbs → likely a Methods section

# ── PARALLEL_STRUCTURE: repetitive triple-parallel patterns (P10) ─────────────
# Matches constructions like "to X..., to Y..., and to Z..." or "by Xing..., by Ying..., and by Zing..."
_PARALLEL_STRUCTURE_RE = re.compile(
    r"(?:"
    r"(?:to \w+[^,]*,\s*to \w+[^,]*,\s*and to \w+)"  # triple infinitive
    r"|(?:by \w+ing[^,]*,\s*by \w+ing[^,]*,\s*and by \w+ing)"  # triple "by + gerund"
    r"|(?:on \w+ing[^,]*,\s*on \w+ing[^,]*,\s*and on \w+ing)"  # triple "on + gerund"
    r"|(?:in \w+ing[^,]*,\s*in \w+ing[^,]*,\s*and in \w+ing)"  # triple "in + gerund"
    r"|(?:for \w+ing[^,]*,\s*for \w+ing[^,]*,\s*and for \w+ing)"  # triple "for + gerund"
    r"|(?:from \w+ing[^,]*,\s*from \w+ing[^,]*,\s*and from \w+ing)"  # triple "from + gerund"
    r"|(?:without \w+ing[^,]*,\s*without \w+ing[^,]*,\s*and without \w+ing)"  # triple "without"
    r"|(?:with \w+[^,]*,\s*with \w+[^,]*,\s*and with \w+)"  # triple "with"
    r")",
    re.IGNORECASE,
)
_PARALLEL_STRUCTURE_THRESHOLD = _SIGNALS_CFG.get("parallel_structure_threshold", 3)  # 3+ sentences with triple-parallel → signal

# ── CHINESE DETECTORS ─────────────────────────────────────────────────────────
# Chinese AI text has distinctive patterns separate from English signals.

# ── THROAT_CLEARING_ZH: 中文套话/空话/废话 ──
_THROAT_CLEARING_ZH_PATTERNS = [
    r"值得注意的是",
    r"值得一提的是",
    r"值得关注的是",
    r"众所周知",
    r"不言而喻",
    r"毋庸置疑",
    r"不可否认",
    r"显而易见",
    r"毫无疑问",
    r"正如前文所述",
    r"如前所述",
    r"综上所述",
    r"总而言之",
    r"需要指出的是",
    r"需要强调的是",
    r"必须承认",
    r"不得不承认",
    r"事实上",
    r"实际上",
]
_THROAT_CLEARING_ZH_RE = [re.compile(p) for p in _THROAT_CLEARING_ZH_PATTERNS]
_THROAT_CLEARING_ZH_THRESHOLD = _SIGNALS_ZH_CFG.get("throat_clearing_zh_threshold", 3)  # 3+ (Chinese texts tend to have some naturally)

# ── PROMOTIONAL_ZH: 中文宣传语气/吹捧式表达 ──
_PROMOTIONAL_ZH_PATTERNS = [
    r"具有(?:划时代|里程碑式?|开创性|革命性)(?:的)?(?:意义|价值|贡献|突破)",
    r"取得了(?:令人瞩目|举世瞩目|显著|重大|突破性)(?:的)?(?:成就|进展|突破|成果)",
    r"(?:高度|极具|极为)(?:创新|前瞻|开创|先进)",
    r"开辟了(?:全新|崭新)(?:的)?(?:道路|方向|局面|领域|篇章)",
    r"(?:前所未有|史无前例|空前)(?:的)?(?:突破|创新|成就|规模)",
    r"(?:引领|推动|驱动)(?:了)?(?:新一轮|新时代|未来)(?:的)?(?:变革|发展|浪潮)",
    r"(?:重新定义|彻底改变|颠覆)了",
]
_PROMOTIONAL_ZH_RE = [re.compile(p) for p in _PROMOTIONAL_ZH_PATTERNS]
_PROMOTIONAL_ZH_THRESHOLD = _SIGNALS_ZH_CFG.get("promotional_zh_threshold", 2)

# ── CONNECTOR_OVERUSE_ZH: 中文连接词堆砌 ──
_CONNECTOR_ZH_PATTERNS = [
    r"此外",
    r"与此同时",
    r"另一方面",
    r"不仅如此",
    r"除此之外",
    r"更为重要的是",
    r"更重要的是",
    r"在此基础上",
    r"进一步(?:而言|来说|地)",
    r"从另一个角度(?:来看|而言)",
    r"值得注意的是",  # also functions as connector
    r"另外",
    r"再者",
    r"其次",
    r"首先.*其次.*(?:再次|最后|再者)",
]
_CONNECTOR_ZH_RE = [re.compile(p) for p in _CONNECTOR_ZH_PATTERNS]
_CONNECTOR_ZH_THRESHOLD = _SIGNALS_ZH_CFG.get("connector_zh_threshold", 5)  # 5+ in a single passage (Chinese naturally uses more)

# ── PARALLEL_STRUCTURE_ZH: 中文排比/递进三段式 ──
_PARALLEL_ZH_PATTERNS = [
    r"既[^，。]+，又[^，。]+，(?:也|还|更)[^。]+",       # 既...又...也/还/更...
    r"不仅[^，。]+，而且[^，。]+，更[^。]+",            # 不仅...而且...更...
    r"一方面[^，。]+，另一方面[^，。]+，(?:同时|此外)[^。]+",  # 一方面...另一方面...同时
    r"无论是[^，。]+，还是[^，。]+，(?:还是|亦或)[^。]+",  # 无论是...还是...还是
    r"从[^，。]+到[^，。]+，从[^，。]+到[^。]+",        # 从...到...从...到...
]
_PARALLEL_ZH_RE = [re.compile(p) for p in _PARALLEL_ZH_PATTERNS]
_PARALLEL_ZH_THRESHOLD = _SIGNALS_ZH_CFG.get("parallel_zh_threshold", 2)  # 2+ triple-parallel in Chinese → signal

# ── INFLATED_SYMBOLISM_ZH: 中文华丽辞藻/堆砌式修辞 ──
_INFLATED_ZH_PATTERNS = [
    r"波澜壮阔",
    r"广袤无垠",
    r"博大精深",
    r"源远流长",
    r"深远(?:的)?(?:意义|影响|价值)",
    r"浓墨重彩",
    r"熠熠生辉",
    r"璀璨夺目",
    r"锦绣(?:画卷|篇章|蓝图)",
    r"丰碑",
    r"(?:犹如|恰似|宛如|好比)(?:一[幅座道束缕抹])?(?:画卷|灯塔|明灯|基石|丰碑)",
    r"(?:照亮|点亮)(?:了)?(?:前行|前进|未来)(?:的)?(?:道路|方向)",
    r"浇筑(?:了)?(?:坚实|牢固)(?:的)?(?:基础|基石|根基)",
]
_INFLATED_ZH_RE = [re.compile(p) for p in _INFLATED_ZH_PATTERNS]
_INFLATED_ZH_THRESHOLD = _SIGNALS_ZH_CFG.get("inflated_zh_threshold", 2)



# --- Scene Auto-Detection ---
#
# Design: Scene routing is an infrastructure concern, NOT an Agent reasoning task.
# The discipline is detected ONCE at parse time (by field_detector) and stored in
# metadata.json. This function reads that cached result. If metadata is unavailable
# (e.g. standalone audit call), it falls back to field_detector in real-time.
#
# Mapping:
#   economics / business_management / finance → S3
#   Chinese language (any non-economics discipline) → S2
#   Everything else (CS, physics, math, etc.) → S1

# Disciplines that map to S3 (Economics/Finance rules)
# Includes both field_detector canonical names AND common user-facing aliases
_S3_DISCIPLINES = {
    # Canonical (from field_detector output)
    "economics", "business_management",
    # User-facing aliases (metadata may be set manually)
    "finance", "business", "经济", "金融", "商学", "经济学",
}


def _is_s3_discipline(discipline: str) -> bool:
    """Check if a discipline string maps to S3 (economics/finance/business)."""
    if not discipline:
        return False
    d = discipline.lower().strip()
    # Exact match
    if d in _S3_DISCIPLINES:
        return True
    # Substring match for compound descriptions like "applied economics"
    econ_fragments = {"econom", "financ", "business", "金融", "经济", "商学"}
    return any(frag in d for frag in econ_fragments)


# ── Lightweight economics keyword heuristic ──
# Covers both English AND Chinese terms. Threshold = 3 distinct matches.
# This is intentionally minimal — just enough for scene routing when
# field_detector can't handle the text (e.g. Chinese, or short English).

_ECON_KEYWORDS_EN = {
    "monetary policy", "fiscal policy", "endogeneity", "instrumental variable",
    "difference-in-differences", "panel data", "fixed effects", "causal inference",
    "treatment effect", "regression discontinuity", "heterogeneity",
    "aggregate demand", "inflation", "gdp", "interest rate", "quantitative easing",
    "oligopoly", "externality", "welfare", "market failure", "elasticity",
    "nash equilibrium", "game theory", "utility maximization",
    "fama-french", "capm", "asset pricing", "yield curve", "credit risk",
    "default probability", "portfolio theory", "stock returns",
    "cross-section", "three-factor",
}

_ECON_KEYWORDS_ZH = {
    "货币政策", "财政政策", "双重差分", "工具变量", "面板数据", "固定效应",
    "内生性", "因果推断", "处理效应", "回归断点", "异质性",
    "总需求", "通货膨胀", "通胀", "利率", "量化宽松",
    "寡头", "外部性", "市场失灵", "弹性", "纳什均衡", "博弈论",
    "资产定价", "有效市场", "套利定价", "信用风险", "道德风险",
    "逆向选择", "收益率曲线", "资本配置",
}

_ECON_THRESHOLD = 3  # Minimum distinct keyword matches to trigger S3


def _has_economics_keywords(text: str) -> bool:
    """Lightweight keyword check for economics/finance content.
    
    Returns True if text contains >= _ECON_THRESHOLD distinct economics terms.
    Works on both English and Chinese text.
    """
    if not text or len(text) < 30:
        return False
    
    text_lower = text.lower()
    matches = 0
    
    # English keywords
    for kw in _ECON_KEYWORDS_EN:
        if kw in text_lower:
            matches += 1
            if matches >= _ECON_THRESHOLD:
                return True
    
    # Chinese keywords (no case folding needed)
    for kw in _ECON_KEYWORDS_ZH:
        if kw in text:
            matches += 1
            if matches >= _ECON_THRESHOLD:
                return True
    


# G-code to standard signal_type mapping (from skills/deai_rules.md)
_GCODE_TO_SIGNAL = {
    "g1": "TRICOLON",
    "g2": "RESOLUTION_CLOSER",
    "g3": "RHYTHM_UNIFORMITY",  # Repeated Rhetorical Skeleton → closest match
    "g4": "HEDGE_OPENERS",
    "g5": "RHYTHM_UNIFORMITY",
    "g6": "CONNECTOR_STACKING",
    "g7": "AI_VOCABULARY",
    "g8": "PROMOTIONAL_TONE",
    "g9": "TYPE_TOKEN_RATIO",   # Perplexity Awareness → closest match
    "g10": "NEGATION_PARALLEL",
    "g11": "EMPTY_PROGRESSIVE",
    "g12": "COPULA_AVOIDANCE",
}



# AI text tends to follow predictable macro patterns:
# - 3-part lists (tricolon), uniform paragraph lengths, intro/point/conclusion sandwich
STRUCTURE_PATTERNS = {
    "tricolon": re.compile(r"(?:first(?:ly)?|second(?:ly)?|third(?:ly)?)", re.IGNORECASE),
    "em_dash_chain": re.compile(r"—[^—]+—"),
    "colon_list": re.compile(r":\s*\n\s*[-•]"),
    "sandwich_pattern": re.compile(
        r"(?:In (?:summary|conclusion)|Overall|Ultimately|All in all)",
        re.IGNORECASE
    ),
    "parallel_structure": re.compile(r"(?:This|These|Such)\s+\w+\s+(?:not only|both)\b"),
}


FORBIDDEN_PATTERNS = [
    # AI vocabulary
    (re.compile(r"\b(?:delve|delving|delved)\b", re.IGNORECASE), "BANNED_WORD: delve"),
    (re.compile(r"\b(?:tapestry|vibrant tapestry)\b", re.IGNORECASE), "BANNED_WORD: tapestry"),
    (re.compile(r"\b(?:game[- ]?changer)\b", re.IGNORECASE), "BANNED_WORD: game-changer"),
    (re.compile(r"\b(?:groundbreaking)\b", re.IGNORECASE), "BANNED_WORD: groundbreaking"),
    (re.compile(r"\b(?:paramount)\b", re.IGNORECASE), "BANNED_WORD: paramount"),
    (re.compile(r"\b(?:realm)\b", re.IGNORECASE), "BANNED_WORD: realm"),
    (re.compile(r"\b(?:synergy|synergistic)\b", re.IGNORECASE), "BANNED_WORD: synergy"),
    (re.compile(r"\b(?:pivotal)\b", re.IGNORECASE), "BANNED_WORD: pivotal"),
    (re.compile(r"\b(?:multifaceted)\b", re.IGNORECASE), "BANNED_WORD: multifaceted"),
    (re.compile(r"\b(?:underscores? the (?:importance|need|significance))\b", re.IGNORECASE),
     "PHRASE: underscores the importance"),
    (re.compile(r"\b(?:it is (?:worth|important to) not(?:e|ing) that)\b", re.IGNORECASE),
     "PHRASE: it is worth noting"),
    # Structural markers
    (re.compile(r"(?:—\s*\w+\s*—.*){2,}"), "PATTERN: double em-dash parenthetical chain"),
    (re.compile(r"(?:\bfirstly\b.*\bsecondly\b.*\bthirdly\b)", re.DOTALL | re.IGNORECASE),
     "PATTERN: firstly/secondly/thirdly tricolon"),
]


# ─── Prompts ─────────────────────────────────────────────────────────────────

DEAI_AUDIT_PROMPT = """You are an AI-text detection specialist for academic writing.
Your ONLY job is to detect AI writing signals in the provided text.
You are NOT the author. You are NOT improving the text. You are ONLY detecting.

## Detection Rules
{rules}

## Instructions
1. Read the text carefully
2. For EACH sentence that shows AI signals, report:
   - The exact sentence (verbatim quote)
   - Which signal category it triggers (from the rules above)
   - Your confidence (0.0-1.0) that this is genuinely an AI signal vs. natural style
   - A brief fix suggestion (sentence-level only)
3. Compute an overall naturalness score (0.0-1.0)
4. If score >= 0.7, verdict is PASS. Otherwise FAIL.

## Output (JSON only, no markdown):
{{
  "is_natural": true/false,
  "overall_score": <float>,
  "signals": [
    {{
      "sentence": "<exact quote>",
      "signal_type": "<MUST be one of: AI_VOCABULARY, TRICOLON, RHYTHM_UNIFORMITY, CONNECTOR_STACKING, HEDGE_OPENERS, PROMOTIONAL_TONE, NEGATION_PARALLEL, PASSIVE_VOICE_OVERUSE, COPULA_AVOIDANCE, EMPTY_PROGRESSIVE, VAGUE_ATTRIBUTION, FORMULAIC_TRANSITIONS, TYPE_TOKEN_RATIO, RESOLUTION_CLOSER, THROAT_CLEARING, HEDGE_STACKING, INFLATED_SYMBOLISM, EM_DASH_OVERUSE, PARALLEL_STRUCTURE, PROMOTIONAL_LANGUAGE>",
      "confidence": <float>,
      "fix_suggestion": "<rewritten sentence>"
    }}
  ],
  "summary": "<1-2 sentence overall assessment>"
}}

IMPORTANT:
- Only flag signals with confidence >= 0.5
- Do NOT flag disciplinary conventions as AI signals (e.g., passive in Methods)
- A single banned word in an otherwise natural paragraph = low confidence (0.5-0.6)
- Multiple structural patterns in one paragraph = high confidence (0.8+)
- If the text is already natural, output is_natural: true with empty signals list
{voice_section}"""

VOICE_AUDIT_ADDENDUM = """
## Author Voice Profile
The following metrics describe THIS AUTHOR's natural writing style.
Signals that MATCH these metrics are NOT AI signals — do not flag them.
{voice_constraints}

PRIORITY: Voice Profile > Scene Rules > General Detection.
If author naturally writes with uniform sentence length, do NOT flag RHYTHM_UNIFORMITY.
If author heavily uses parentheticals, do NOT flag em-dash/parenthetical patterns."""

DEAI_FIX_PROMPT = """You are fixing specific AI-writing signals in academic text.

## Rules:
1. Fix ONLY the sentences listed below. Do NOT touch any other sentence.
2. Each fix must be semantically equivalent to the original.
3. Maintain academic register and formality.
4. If you cannot fix a sentence without reducing quality, output it UNCHANGED and mark "kept_original": true.
5. Return the complete text with fixes applied.
6. When choosing replacement words, occasionally pick a less-predictable (but semantically equivalent) alternative over the "default" academic phrasing — break token prediction patterns.
7. Fixed sentences must be >= 10 words in academic context.
{voice_fix_constraints}

## Signals to fix:
{signals_json}

## Original text to fix:
{text}

## Output (JSON):
{{
  "fixed_text": "<complete text with fixes applied>",
  "fixes_applied": [
    {{
      "original": "<original sentence>",
      "fixed": "<new sentence>",
      "kept_original": false
    }}
  ]
}}"""


@dataclass
class AISignal:
    """A single detected AI writing signal."""
    sentence: str
    signal_type: str          # e.g., "AI_VOCABULARY", "TRICOLON", "RHYTHM_UNIFORMITY"
    confidence: float         # 0.0-1.0
    fix_suggestion: str       # Sentence-level rewrite suggestion
    location_hint: str = ""   # Approximate position in text


@dataclass
class DimensionScores:
    """Multi-dimension de-AI quality scores (TODO-2)."""
    vocabulary: float = 1.0     # 0.0-1.0
    rhythm: float = 1.0
    connectors: float = 1.0
    punctuation: float = 1.0
    voice: float = 1.0

    def weighted_overall(self) -> float:
        """Compute weighted overall score."""
        return (
            self.vocabulary * DIMENSION_WEIGHTS["vocabulary"]
            + self.rhythm * DIMENSION_WEIGHTS["rhythm"]
            + self.connectors * DIMENSION_WEIGHTS["connectors"]
            + self.punctuation * DIMENSION_WEIGHTS["punctuation"]
            + self.voice * DIMENSION_WEIGHTS["voice"]
        )

    def floor_violated(self) -> Optional[str]:
        """Return the first dimension below DIMENSION_FLOOR, or None."""
        for dim in DIMENSION_WEIGHTS:
            val = getattr(self, dim)
            if val < DIMENSION_FLOOR:
                return dim
        return None

    def to_dict(self) -> dict:
        return {
            "vocabulary": round(self.vocabulary, 3),
            "rhythm": round(self.rhythm, 3),
            "connectors": round(self.connectors, 3),
            "punctuation": round(self.punctuation, 3),
            "voice": round(self.voice, 3),
            "weighted_overall": round(self.weighted_overall(), 3),
        }

    def diagnosis_report(self) -> str:
        """Human-readable dimension breakdown."""
        lines = ["Dimension Scores:"]
        dims = [
            ("vocabulary", self.vocabulary, DIMENSION_WEIGHTS["vocabulary"]),
            ("rhythm", self.rhythm, DIMENSION_WEIGHTS["rhythm"]),
            ("connectors", self.connectors, DIMENSION_WEIGHTS["connectors"]),
            ("punctuation", self.punctuation, DIMENSION_WEIGHTS["punctuation"]),
            ("voice", self.voice, DIMENSION_WEIGHTS["voice"]),
        ]
        for name, score, weight in dims:
            bar = "█" * int(score * 10) + "░" * (10 - int(score * 10))
            flag = " ⚠️" if score < DIMENSION_FLOOR else ""
            lines.append(f"  {name:12s} [{bar}] {score:.2f} (×{weight:.2f}){flag}")
        lines.append(f"  {'overall':12s}            = {self.weighted_overall():.3f}")
        return "\n".join(lines)


@dataclass
class TieredJudgment:
    """Result of tiered tolerance judgment (TODO-1)."""
    verdict: str              # "PASS" | "FAIL" | "CONDITIONAL_PASS"
    reason: str               # Human-readable reason
    critical_signals: List[str] = field(default_factory=list)   # zero-tolerance hits
    major_violations: int = 0
    minor_violations: int = 0
    dimension_floor_violated: Optional[str] = None
    baseline_delta: Optional[float] = None  # score - baseline (if available)
    hard_caps_triggered: List[str] = field(default_factory=list)  # HC reasons (TODO-3)


@dataclass
class DeAIVerdict:
    """Result of a de-AI audit pass."""
    is_natural: bool          # Overall pass/fail
    overall_score: float      # 0.0-1.0, higher = more natural
    signals: List[AISignal] = field(default_factory=list)
    summary: str = ""
    dimensions: Optional[DimensionScores] = None   # Multi-dim breakdown (TODO-2)
    tiered_judgment: Optional[TieredJudgment] = None  # Tiered tolerance result (TODO-1)
    hard_caps: Optional[HardCapResult] = None  # Hard cap result (TODO-3)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d



@dataclass
class SelfCheckResult:
    """Result from one layer of the self-check protocol."""
    layer: str              # L1/L2/L3/L4
    layer_name: str         # Structure/Rhythm/Forbidden/Voice
    passed: bool
    score: float            # 0.0-1.0
    violations: List[str] = field(default_factory=list)
    details: Dict = field(default_factory=dict)


@dataclass
class SelfCheckReport:
    """Combined report from all 4 self-check layers."""
    all_passed: bool
    overall_score: float    # Weighted average across layers
    layers: List[SelfCheckResult] = field(default_factory=list)
    blocking_layers: List[str] = field(default_factory=list)  # Layers that failed

    def summary(self) -> str:
        status = "✅ ALL PASS" if self.all_passed else "❌ BLOCKED"
        parts = [f"Self-Check: {status} (score: {self.overall_score:.2f})"]
        for layer in self.layers:
            marker = "✓" if layer.passed else "✗"
            parts.append(f"  {marker} {layer.layer} {layer.layer_name}: {layer.score:.2f}")
            for v in layer.violations[:3]:
                parts.append(f"      → {v}")
        return "\n".join(parts)


@dataclass
class DiagnosisResult:
    """Output from the Diagnose step of the closed loop."""
    signal: AISignal
    root_cause: str         # Why this sentence triggers detection
    fix_strategy: str       # How to fix (e.g., "lexical replacement", "restructure", "split")
    priority: int           # 1=must fix, 2=should fix, 3=optional
    context_dependency: str  # "independent" | "requires_neighbor" | "paragraph_level"
