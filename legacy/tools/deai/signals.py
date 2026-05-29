"""
tools/deai/signals.py - AI signal detection, dimension scoring, hard caps, and tiered judgment.

Contains programmatic (zero-LLM-cost) detectors and scoring logic.
"""

from __future__ import annotations

import re
import json
import statistics
from typing import List, Dict, Optional, Tuple
from dataclasses import asdict

from tools.deai.constants import (
    SIGNAL_TOLERANCE_TIERS,
    DEFAULT_SIGNAL_TIER,
    CONDITIONAL_PASS_TOLERANCE,
    DIMENSION_WEIGHTS,
    DIMENSION_FLOOR,
    SIGNAL_TO_DIMENSION,
    DEFAULT_DIMENSION,
    AI_CLICHE_PATTERNS,
    _AI_CLICHE_RE,
    HC_VOCABULARY_CAP,
    HC_VOCABULARY_THRESHOLD,
    HC_RHYTHM_CONSECUTIVE_CAP,
    HC_RHYTHM_CONSECUTIVE_THRESHOLD,
    HC_RHYTHM_BURSTINESS_CAP,
    HC_RHYTHM_BURSTINESS_CV_THRESHOLD,
    _FORMULAIC_TRANSITION_RE,
    _FORMULAIC_TRANSITION_THRESHOLD,
    _TTR_CONTENT_STOPWORDS,
    _TTR_WINDOW,
    _TTR_REPEAT_THRESHOLD,
    _THROAT_CLEARING_RE,
    _THROAT_CLEARING_THRESHOLD,
    _PROMOTIONAL_RE,
    _PROMOTIONAL_THRESHOLD,
    _INFLATED_SYMBOLISM_RE,
    _INFLATED_SYMBOLISM_THRESHOLD,
    _PASSIVE_VOICE_RE,
    _IMPERSONAL_PASSIVE_RE,
    _PASSIVE_VOICE_RATIO_THRESHOLD,
    _METHODS_VERB_PATTERNS,
    _METHODS_VERB_THRESHOLD,
    _PARALLEL_STRUCTURE_RE,
    _PARALLEL_STRUCTURE_THRESHOLD,
    _THROAT_CLEARING_ZH_RE,
    _THROAT_CLEARING_ZH_THRESHOLD,
    _PROMOTIONAL_ZH_RE,
    _PROMOTIONAL_ZH_THRESHOLD,
    _CONNECTOR_ZH_RE,
    _CONNECTOR_ZH_THRESHOLD,
    _PARALLEL_ZH_RE,
    _PARALLEL_ZH_THRESHOLD,
    _INFLATED_ZH_RE,
    _INFLATED_ZH_THRESHOLD,
    _GCODE_TO_SIGNAL,
    RULES_PATH,
    DEAI_AUDIT_PROMPT,
    VOICE_AUDIT_ADDENDUM,
    AISignal,
    DimensionScores,
    HardCapResult,
    TieredJudgment,
    DeAIVerdict,
)
from tools.deai.scene import _is_chinese_text

from llm.client import LLMClient
from llm.router import get_model_for_task
from utils.voice_profile import load_voice_profile, get_voice_constraints, check_voice_drift
from utils.author_profile import load_profile, get_profile_context_for_prompt
from tools.deai.constants import WORKSPACE, MAX_RETRIES, PASS_THRESHOLD, IMPROVEMENT_THRESHOLD, DEAI_FIX_PROMPT



def detect_hard_caps(text: str) -> HardCapResult:
    """
    Detect hard cap conditions in text. Programmatic, zero LLM cost.
    
    Hard Caps:
      HC-1: 2+ AI cliché phrases → vocabulary capped at 0.60
      HC-2: 3+ consecutive same-opener sentences → rhythm capped at 0.50
      HC-3: Sentence length CV < 0.20 → rhythm capped at 0.40
    
    Returns HardCapResult with triggered caps and reasons.
    """
    caps: Dict[str, float] = {}
    reasons: List[str] = []
    details: Dict[str, any] = {}

    # ── HC-1: AI Cliché Detection ──
    cliche_matches = []
    for i, pattern_re in enumerate(_AI_CLICHE_RE):
        if pattern_re.search(text):
            cliche_matches.append(AI_CLICHE_PATTERNS[i])
    
    if len(cliche_matches) >= HC_VOCABULARY_THRESHOLD:
        caps["vocabulary"] = min(caps.get("vocabulary", 1.0), HC_VOCABULARY_CAP)
        reasons.append(
            f"HC-1: {len(cliche_matches)} AI clichés detected "
            f"(vocabulary capped at {HC_VOCABULARY_CAP:.0%})"
        )
        details["hc1_matches"] = cliche_matches[:5]

    # ── HC-2: Consecutive Same-Opener Detection ──
    sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z])', text.strip())
    valid_sentences = [s.strip() for s in sentences if len(s.split()) >= 4]
    
    if len(valid_sentences) >= HC_RHYTHM_CONSECUTIVE_THRESHOLD:
        # Extract first 2 words as "opener" pattern (captures subject phrase)
        openers = []
        for sent in valid_sentences:
            words = sent.split()[:2]
            # Normalize: lowercase, strip punctuation
            opener = " ".join(w.lower().rstrip(",.;:") for w in words)
            openers.append(opener)
        
        # Find longest consecutive run of same opener
        max_run = 1
        current_run = 1
        max_opener = ""
        for i in range(1, len(openers)):
            if openers[i] == openers[i - 1]:
                current_run += 1
                if current_run > max_run:
                    max_run = current_run
                    max_opener = openers[i]
            else:
                current_run = 1
        
        if max_run >= HC_RHYTHM_CONSECUTIVE_THRESHOLD:
            caps["rhythm"] = min(caps.get("rhythm", 1.0), HC_RHYTHM_CONSECUTIVE_CAP)
            reasons.append(
                f"HC-2: {max_run} consecutive sentences with same opener "
                f"'{max_opener}' (rhythm capped at {HC_RHYTHM_CONSECUTIVE_CAP:.0%})"
            )
            details["hc2_max_run"] = max_run
            details["hc2_opener"] = max_opener

    # ── HC-3: Near-Zero Burstiness ──
    word_counts = [len(s.split()) for s in valid_sentences if len(s.split()) >= 4]
    if len(word_counts) >= 5:  # Need enough sentences to judge
        mean_w = statistics.mean(word_counts)
        std_w = statistics.stdev(word_counts)
        cv = std_w / mean_w if mean_w > 0 else 0.0
        
        if cv < HC_RHYTHM_BURSTINESS_CV_THRESHOLD:
            caps["rhythm"] = min(caps.get("rhythm", 1.0), HC_RHYTHM_BURSTINESS_CAP)
            reasons.append(
                f"HC-3: Near-zero burstiness (CV={cv:.3f} < {HC_RHYTHM_BURSTINESS_CV_THRESHOLD}) "
                f"(rhythm capped at {HC_RHYTHM_BURSTINESS_CAP:.0%})"
            )
            details["hc3_cv"] = round(cv, 4)
            details["hc3_mean_words"] = round(mean_w, 1)

    triggered = len(caps) > 0
    return HardCapResult(triggered=triggered, caps=caps, reasons=reasons, details=details)


def _detect_programmatic_signals(text: str) -> List["AISignal"]:
    """
    Zero-LLM programmatic detection for signals that LLM consistently misses.
    
    Detects (P7 original):
    - RHYTHM_UNIFORMITY: sentence length CV < 0.35 (softer than HC-3's 0.20)
    - FORMULAIC_TRANSITIONS: 4+ distinct formulaic transition words
    - TYPE_TOKEN_RATIO: same content word in 3+ sentences within a 5-sentence window
    
    Detects (P10 additions — English):
    - THROAT_CLEARING: 2+ filler phrases ("It is important to note that...")
    - PROMOTIONAL_LANGUAGE: 2+ superlative/marketing terms
    - INFLATED_SYMBOLISM: 2+ grand metaphor phrases ("tapestry of", "testament to")
    - PASSIVE_VOICE_OVERUSE: >50% of sentences in passive voice
    - PARALLEL_STRUCTURE: 3+ sentences with triple-parallel constructions
    
    Detects (P10 additions — Chinese, only when text >30% CJK):
    - THROAT_CLEARING_ZH: 3+ 套话 ("值得注意的是", "众所周知", "不言而喻")
    - PROMOTIONAL_ZH: 2+ 宣传式表达 ("划时代", "前所未有的突破")
    - CONNECTOR_OVERUSE_ZH: 5+ 连接词堆砌 ("此外", "与此同时", "不仅如此")
    - PARALLEL_STRUCTURE_ZH: 2+ 排比三段式 ("既...又...也", "不仅...而且...更")
    - INFLATED_SYMBOLISM_ZH: 2+ 华丽辞藻 ("波澜壮阔", "灯塔", "丰碑")
    
    Returns list of AISignal instances to inject into verdict.
    """
    signals: List[AISignal] = []

    # Split into sentences (supports English and Chinese punctuation)
    # English: split after .!? followed by space+uppercase
    # Chinese: split after 。！？ (optionally followed by space)
    sentences = re.split(
        r'(?<=[.!?])\s+(?=[A-Z])|(?<=[。！？])\s*',
        text.strip()
    )
    # Filter short fragments (4+ words for English, 6+ chars for Chinese)
    valid_sentences = [
        s for s in sentences
        if s.strip() and (len(s.split()) >= 4 or len(s.strip()) >= 6)
    ]

    # ── RHYTHM_UNIFORMITY: CV < 0.35 (more sensitive than HC-3's 0.20 threshold) ──
    is_zh = _is_chinese_text(text)
    if len(valid_sentences) >= 5:
        # For Chinese: use char count (excluding punctuation/spaces) as sentence "length"
        # For English: use word count as before
        if is_zh:
            length_counts = [
                len(re.sub(r'[\s\u3000，。！？、；：\u201c\u201d\u2018\u2019（）《》【】…—·.,!?;:\"\'()\[\]]', '', s))
                for s in valid_sentences
            ]
        else:
            length_counts = [len(s.split()) for s in valid_sentences]
        # Filter out zero-length entries
        length_counts = [c for c in length_counts if c > 0]
        if len(length_counts) >= 5:
            mean_w = statistics.mean(length_counts)
            std_w = statistics.stdev(length_counts)
            cv = std_w / mean_w if mean_w > 0 else 0.0
            if cv < 0.35:
                confidence = min(0.9, 0.6 + (0.35 - cv))  # Lower CV → higher confidence
                unit = "chars" if is_zh else "words"
                signals.append(AISignal(
                    sentence="(paragraph-level rhythm analysis)",
                    signal_type="RHYTHM_UNIFORMITY",
                    confidence=round(confidence, 2),
                    fix_suggestion=(
                        f"Sentence length CV={cv:.2f} (need >=0.35). "
                        f"Mix short and long sentences ({unit})."
                    ),
                    location_hint="global",
                ))

    # ── FORMULAIC_TRANSITIONS: 4+ distinct formulaic connectors ──
    found_transitions = set()
    transition_examples = []
    for sent in valid_sentences:
        for i, pat in enumerate(_FORMULAIC_TRANSITION_RE):
            if pat.search(sent):
                found_transitions.add(i)
                if len(transition_examples) < 3:
                    transition_examples.append(sent[:80])
    if len(found_transitions) >= _FORMULAIC_TRANSITION_THRESHOLD:
        confidence = min(0.9, 0.6 + (len(found_transitions) - _FORMULAIC_TRANSITION_THRESHOLD) * 0.1)
        signals.append(AISignal(
            sentence=transition_examples[0] if transition_examples else "(multiple transitions)",
            signal_type="FORMULAIC_TRANSITIONS",
            confidence=round(confidence, 2),
            fix_suggestion=(
                f"{len(found_transitions)} distinct formulaic transitions detected. "
                f"Replace some with implicit logical flow or varied connectors."
            ),
            location_hint="global",
        ))

    # ── TYPE_TOKEN_RATIO: same content word repeated across 3+ sentences in window ──
    if len(valid_sentences) >= _TTR_WINDOW:
        # Tokenize sentences into content words
        sent_words = []
        for s in valid_sentences:
            words = set(
                w.lower() for w in re.findall(r'\b[a-zA-Z]{4,}\b', s)
                if w.lower() not in _TTR_CONTENT_STOPWORDS
            )
            sent_words.append(words)

        # Sliding window: find any word appearing in 3+ sentences within 5
        worst_word = ""
        worst_count = 0
        for start in range(len(sent_words) - _TTR_WINDOW + 1):
            window = sent_words[start:start + _TTR_WINDOW]
            # Count word appearances across sentences in window
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
                sentence=f"(word '{worst_word}' appears in {worst_count}/{_TTR_WINDOW} adjacent sentences)",
                signal_type="TYPE_TOKEN_RATIO",
                confidence=round(confidence, 2),
                fix_suggestion=(
                    f"Word '{worst_word}' over-repeated in adjacent sentences. "
                    f"Use synonyms, pronouns, or restructure to reduce repetition."
                ),
                location_hint="global",
            ))

    # ── THROAT_CLEARING: formulaic filler phrases that delay the main point ──
    throat_matches = []
    for sent in valid_sentences:
        for pat in _THROAT_CLEARING_RE:
            m = pat.search(sent)
            if m:
                throat_matches.append(m.group(0))
                break  # one match per sentence is enough
    if len(throat_matches) >= _THROAT_CLEARING_THRESHOLD:
        confidence = min(0.9, 0.65 + (len(throat_matches) - _THROAT_CLEARING_THRESHOLD) * 0.1)
        signals.append(AISignal(
            sentence=throat_matches[0],
            signal_type="THROAT_CLEARING",
            confidence=round(confidence, 2),
            fix_suggestion=(
                f"{len(throat_matches)} throat-clearing phrases detected "
                f"(e.g., \"{throat_matches[0]}\"). Delete them and start directly "
                f"with the substantive content."
            ),
            location_hint="global",
        ))

    # ── PROMOTIONAL: superlative/marketing language in academic text ──
    promo_matches = []
    for pat in _PROMOTIONAL_RE:
        for m in pat.finditer(text):
            promo_matches.append(m.group(0))
    # Deduplicate (same word appearing multiple times still counts once per unique pattern)
    promo_unique = list(dict.fromkeys(promo_matches))
    if len(promo_unique) >= _PROMOTIONAL_THRESHOLD:
        confidence = min(0.9, 0.6 + (len(promo_unique) - _PROMOTIONAL_THRESHOLD) * 0.1)
        examples = promo_unique[:3]
        signals.append(AISignal(
            sentence=f"({', '.join(examples)})",
            signal_type="PROMOTIONAL_LANGUAGE",
            confidence=round(confidence, 2),
            fix_suggestion=(
                f"{len(promo_unique)} promotional terms detected: {', '.join(examples)}. "
                f"Replace with measured academic language."
            ),
            location_hint="global",
        ))

    # ── INFLATED_SYMBOLISM: overuse of metaphorical/grandiose phrases ──
    symbolism_matches = []
    for pat in _INFLATED_SYMBOLISM_RE:
        for m in pat.finditer(text):
            symbolism_matches.append(m.group(0))
    symbolism_unique = list(dict.fromkeys(symbolism_matches))
    if len(symbolism_unique) >= _INFLATED_SYMBOLISM_THRESHOLD:
        confidence = min(0.9, 0.65 + (len(symbolism_unique) - _INFLATED_SYMBOLISM_THRESHOLD) * 0.15)
        examples = symbolism_unique[:3]
        signals.append(AISignal(
            sentence=f"({', '.join(examples)})",
            signal_type="INFLATED_SYMBOLISM",
            confidence=round(confidence, 2),
            fix_suggestion=(
                f"{len(symbolism_unique)} inflated symbolism phrases: {', '.join(examples)}. "
                f"Replace grand metaphors with concrete, precise descriptions."
            ),
            location_hint="global",
        ))

    # ── PASSIVE_VOICE_OVERUSE: excessive passive constructions ──
    # Skip if text appears to be a Methods section (passive voice is legitimate there)
    methods_verb_count = len(_METHODS_VERB_PATTERNS.findall(text))
    is_likely_methods = methods_verb_count >= _METHODS_VERB_THRESHOLD
    
    if len(valid_sentences) >= 4 and not is_likely_methods:
        passive_count = 0
        passive_examples = []
        for sent in valid_sentences:
            if _PASSIVE_VOICE_RE.search(sent) or _IMPERSONAL_PASSIVE_RE.search(sent):
                passive_count += 1
                if len(passive_examples) < 2:
                    passive_examples.append(sent[:80])
        passive_ratio = passive_count / len(valid_sentences)
        if passive_ratio >= _PASSIVE_VOICE_RATIO_THRESHOLD and passive_count >= 3:
            confidence = min(0.85, 0.55 + (passive_ratio - _PASSIVE_VOICE_RATIO_THRESHOLD) * 2)
            signals.append(AISignal(
                sentence=passive_examples[0] if passive_examples else "(excessive passive voice)",
                signal_type="PASSIVE_VOICE_OVERUSE",
                confidence=round(confidence, 2),
                fix_suggestion=(
                    f"{passive_count}/{len(valid_sentences)} sentences ({passive_ratio:.0%}) use "
                    f"passive voice. Convert some to active: 'We found...' instead of "
                    f"'It was found that...'"
                ),
                location_hint="global",
            ))

    # ── PARALLEL_STRUCTURE: repetitive triple-parallel constructions ──
    parallel_count = 0
    parallel_examples = []
    for sent in valid_sentences:
        if _PARALLEL_STRUCTURE_RE.search(sent):
            parallel_count += 1
            if len(parallel_examples) < 2:
                parallel_examples.append(sent[:100])
    if parallel_count >= _PARALLEL_STRUCTURE_THRESHOLD:
        confidence = min(0.9, 0.6 + (parallel_count - _PARALLEL_STRUCTURE_THRESHOLD) * 0.15)
        signals.append(AISignal(
            sentence=parallel_examples[0] if parallel_examples else "(triple-parallel pattern)",
            signal_type="PARALLEL_STRUCTURE",
            confidence=round(confidence, 2),
            fix_suggestion=(
                f"{parallel_count} sentences use triple-parallel construction "
                f"(prep+verb, prep+verb, and prep+verb). Vary sentence structure — "
                f"not every list needs three items in parallel form."
            ),
            location_hint="global",
        ))

    # ═══════════════════════════════════════════════════════════════════════════
    # CHINESE DETECTORS — only run if text is predominantly Chinese
    # ═══════════════════════════════════════════════════════════════════════════
    if _is_chinese_text(text):
        # Split Chinese sentences (by 。！？)
        zh_sentences = [s.strip() for s in re.split(r'[。！？]', text) if s.strip() and len(s.strip()) >= 6]

        # ── THROAT_CLEARING_ZH ──
        zh_throat_matches = []
        for sent in zh_sentences:
            for pat in _THROAT_CLEARING_ZH_RE:
                m = pat.search(sent)
                if m:
                    zh_throat_matches.append(m.group(0))
                    break
        if len(zh_throat_matches) >= _THROAT_CLEARING_ZH_THRESHOLD:
            confidence = min(0.9, 0.6 + (len(zh_throat_matches) - _THROAT_CLEARING_ZH_THRESHOLD) * 0.1)
            signals.append(AISignal(
                sentence=zh_throat_matches[0],
                signal_type="THROAT_CLEARING_ZH",
                confidence=round(confidence, 2),
                fix_suggestion=(
                    f"检测到{len(zh_throat_matches)}处中文套话"
                    f"（如「{zh_throat_matches[0]}」）。"
                    f"删除这些空话，直接陈述核心内容。"
                ),
                location_hint="global",
            ))

        # ── PROMOTIONAL_ZH ──
        zh_promo_matches = []
        for pat in _PROMOTIONAL_ZH_RE:
            for m in pat.finditer(text):
                zh_promo_matches.append(m.group(0))
        zh_promo_unique = list(dict.fromkeys(zh_promo_matches))
        if len(zh_promo_unique) >= _PROMOTIONAL_ZH_THRESHOLD:
            confidence = min(0.9, 0.65 + (len(zh_promo_unique) - _PROMOTIONAL_ZH_THRESHOLD) * 0.1)
            examples = zh_promo_unique[:3]
            signals.append(AISignal(
                sentence=f"({', '.join(examples)})",
                signal_type="PROMOTIONAL_ZH",
                confidence=round(confidence, 2),
                fix_suggestion=(
                    f"检测到{len(zh_promo_unique)}处宣传式表达：{', '.join(examples)}。"
                    f"改用平实、具体的学术语言。"
                ),
                location_hint="global",
            ))

        # ── CONNECTOR_OVERUSE_ZH ──
        zh_connector_matches = []
        for pat in _CONNECTOR_ZH_RE:
            for m in pat.finditer(text):
                zh_connector_matches.append(m.group(0))
        if len(zh_connector_matches) >= _CONNECTOR_ZH_THRESHOLD:
            confidence = min(0.85, 0.55 + (len(zh_connector_matches) - _CONNECTOR_ZH_THRESHOLD) * 0.05)
            examples = list(dict.fromkeys(zh_connector_matches))[:4]
            signals.append(AISignal(
                sentence=f"({', '.join(examples)})",
                signal_type="CONNECTOR_OVERUSE_ZH",
                confidence=round(confidence, 2),
                fix_suggestion=(
                    f"检测到{len(zh_connector_matches)}处连接词/过渡词堆砌：{', '.join(examples)}。"
                    f"减少显式逻辑连接词，用自然的语序和内在逻辑衔接。"
                ),
                location_hint="global",
            ))

        # ── PARALLEL_STRUCTURE_ZH ──
        zh_parallel_count = 0
        zh_parallel_examples = []
        for pat in _PARALLEL_ZH_RE:
            for m in pat.finditer(text):
                zh_parallel_count += 1
                if len(zh_parallel_examples) < 2:
                    zh_parallel_examples.append(m.group(0)[:60])
        if zh_parallel_count >= _PARALLEL_ZH_THRESHOLD:
            confidence = min(0.85, 0.6 + (zh_parallel_count - _PARALLEL_ZH_THRESHOLD) * 0.15)
            signals.append(AISignal(
                sentence=zh_parallel_examples[0] if zh_parallel_examples else "(中文排比式)",
                signal_type="PARALLEL_STRUCTURE_ZH",
                confidence=round(confidence, 2),
                fix_suggestion=(
                    f"检测到{zh_parallel_count}处排比/递进三段式结构"
                    f"（如「{zh_parallel_examples[0][:40]}」）。"
                    f"AI倾向于使用三段排比——用更自然的散文句式替代。"
                ),
                location_hint="global",
            ))

        # ── INFLATED_SYMBOLISM_ZH ──
        zh_symbolism_matches = []
        for pat in _INFLATED_ZH_RE:
            for m in pat.finditer(text):
                zh_symbolism_matches.append(m.group(0))
        zh_symbolism_unique = list(dict.fromkeys(zh_symbolism_matches))
        if len(zh_symbolism_unique) >= _INFLATED_ZH_THRESHOLD:
            confidence = min(0.9, 0.65 + (len(zh_symbolism_unique) - _INFLATED_ZH_THRESHOLD) * 0.15)
            examples = zh_symbolism_unique[:3]
            signals.append(AISignal(
                sentence=f"({', '.join(examples)})",
                signal_type="INFLATED_SYMBOLISM_ZH",
                confidence=round(confidence, 2),
                fix_suggestion=(
                    f"检测到{len(zh_symbolism_unique)}处华丽辞藻/堆砌修辞：{', '.join(examples)}。"
                    f"用具体、精确的表述替代空泛修辞。"
                ),
                location_hint="global",
            ))

    return signals


def check_burstiness(text: str, min_cv: float = 0.35) -> Dict:
    """
    Programmatic burstiness check — measures sentence-length variation.
    
    AI-generated text tends toward uniform sentence lengths (low coefficient
    of variation). Human academic writing typically has CV >= 0.35-0.50.
    
    Supports both English (word count) and Chinese (character count) texts.
    
    Args:
        text: The text to check.
        min_cv: Minimum acceptable coefficient of variation (default 0.35).
    
    Returns:
        Dict with:
        - passed: bool — whether burstiness meets threshold
        - cv: float — coefficient of variation of sentence lengths
        - mean_length: float — average sentence length (words or chars)
        - std_length: float — standard deviation of sentence lengths
        - sentence_count: int — number of sentences analyzed
        - longest: int — length of longest sentence
        - shortest: int — length of shortest sentence
        - unit: str — "words" or "chars"
        - warning: str — human-readable warning if failed
    """
    is_zh = _is_chinese_text(text)
    
    # Split into sentences (handle both English and Chinese punctuation)
    if is_zh:
        sentences = [s.strip() for s in re.split(r'[。！？]', text.strip()) if s.strip()]
    else:
        sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z])', text.strip())
    
    # Compute sentence lengths
    length_counts = []
    if is_zh:
        # Chinese: count meaningful characters (exclude punctuation and spaces)
        _punct_re = re.compile(r'[\s\u3000，。！？、；：\u201c\u201d\u2018\u2019（）《》【】…—·.,!?;:\"\'()\[\]]')
        for s in sentences:
            char_len = len(_punct_re.sub('', s))
            if char_len >= 4:  # Minimum 4 chars to be a meaningful sentence
                length_counts.append(char_len)
    else:
        # English: count words (minimum 4 words)
        for s in sentences:
            words = len(s.split())
            if words >= 4:
                length_counts.append(words)
    
    unit = "chars" if is_zh else "words"
    
    if len(length_counts) < 3:
        return {
            "passed": True,  # Too few sentences to judge
            "cv": 0.0,
            "mean_length": 0.0,
            "std_length": 0.0,
            "sentence_count": len(length_counts),
            "longest": max(length_counts) if length_counts else 0,
            "shortest": min(length_counts) if length_counts else 0,
            "unit": unit,
            "warning": "",
        }
    
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
    
    return {
        "passed": passed,
        "cv": round(cv, 3),
        "mean_length": round(mean_w, 1),
        "std_length": round(std_w, 1),
        "sentence_count": len(length_counts),
        "longest": max(length_counts),
        "shortest": min(length_counts),
        "unit": unit,
        "warning": warning,
    }


def compute_dimension_scores(
    signals: List[AISignal],
    dimension_biases: Optional[Dict[str, float]] = None,
) -> DimensionScores:
    """Compute per-dimension naturalness scores from detected signals (TODO-2).

    Logic:
    - Start each dimension at 1.0 (perfect)
    - Each signal in that dimension reduces the score by a penalty based on confidence
    - Penalty = signal.confidence * 0.15 (each high-confidence signal ≈ -15%)
    - If dimension_biases provided (from reviewer hints), apply additional soft
      penalties: biased dimensions get an extra penalty boost (max +5%), making
      the scoring slightly stricter on dimensions the reviewer flagged.
    - Floor at 0.0
    """
    penalties = {dim: 0.0 for dim in DIMENSION_WEIGHTS}

    for sig in signals:
        dim = SIGNAL_TO_DIMENSION.get(sig.signal_type.upper(), DEFAULT_DIMENSION)
        # Penalty scales with confidence: high-confidence signals penalize more
        penalty = sig.confidence * 0.15
        # Apply reviewer bias: if this dimension was flagged by reviewer, amplify penalty
        if dimension_biases and dim in dimension_biases:
            bias = dimension_biases[dim]  # 0.0–0.05
            penalty *= (1.0 + bias * 4)  # bias=0.05 → 20% amplification
        penalties[dim] += penalty

    return DimensionScores(
        vocabulary=max(0.0, 1.0 - penalties["vocabulary"]),
        rhythm=max(0.0, 1.0 - penalties["rhythm"]),
        connectors=max(0.0, 1.0 - penalties["connectors"]),
        punctuation=max(0.0, 1.0 - penalties["punctuation"]),
        voice=max(0.0, 1.0 - penalties["voice"]),
    )


def apply_tiered_judgment(
    signals: List[AISignal],
    dimensions: DimensionScores,
    baseline_score: Optional[float] = None,
) -> TieredJudgment:
    """Apply tiered tolerance rules to determine PASS/FAIL/CONDITIONAL_PASS (TODO-1).

    Rules (in priority order):
    1. Any CRITICAL signal with confidence >= 0.7 → immediate FAIL
    2. Any dimension below DIMENSION_FLOOR → FAIL (dimension floor)
    3. MAJOR signals: 2+ of same type → FAIL
    4. MINOR signals: 4+ total → FAIL
    5. If baseline provided and overall within CONDITIONAL_PASS_TOLERANCE → CONDITIONAL_PASS
    6. Otherwise: PASS
    """
    critical_hits = []
    major_counts: Dict[str, int] = {}
    minor_total = 0

    for sig in signals:
        tier = SIGNAL_TOLERANCE_TIERS.get(sig.signal_type.upper(), DEFAULT_SIGNAL_TIER)

        if tier == "critical" and sig.confidence >= 0.7:
            critical_hits.append(f"{sig.signal_type} (conf={sig.confidence:.2f})")
        elif tier == "major":
            major_counts[sig.signal_type] = major_counts.get(sig.signal_type, 0) + 1
        elif tier == "minor":
            minor_total += 1

    # Rule 1: Critical zero-tolerance
    if critical_hits:
        return TieredJudgment(
            verdict="FAIL",
            reason=f"Critical signal(s) detected (zero-tolerance): {', '.join(critical_hits[:3])}",
            critical_signals=critical_hits,
            major_violations=sum(v for v in major_counts.values() if v >= 2),
            minor_violations=minor_total,
        )

    # Rule 2: Dimension floor
    floor_dim = dimensions.floor_violated()
    if floor_dim:
        dim_score = getattr(dimensions, floor_dim)
        return TieredJudgment(
            verdict="FAIL",
            reason=f"Dimension '{floor_dim}' below floor ({dim_score:.2f} < {DIMENSION_FLOOR})",
            dimension_floor_violated=floor_dim,
            major_violations=sum(v for v in major_counts.values() if v >= 2),
            minor_violations=minor_total,
        )

    # Rule 3: Major signal accumulation (2+ of same type)
    major_violators = [k for k, v in major_counts.items() if v >= 2]
    if major_violators:
        return TieredJudgment(
            verdict="FAIL",
            reason=f"Major signal(s) exceeded threshold: {', '.join(major_violators)} (2+ each)",
            major_violations=len(major_violators),
            minor_violations=minor_total,
        )

    # Rule 4: Minor signal flood (4+)
    if minor_total >= 4:
        return TieredJudgment(
            verdict="FAIL",
            reason=f"Excessive minor signals: {minor_total} (threshold: 4)",
            minor_violations=minor_total,
        )

    # Rule 5: Conditional PASS (near baseline after retries)
    # Grant conditional pass when score is only slightly below baseline (within tolerance)
    if baseline_score is not None:
        delta = dimensions.weighted_overall() - baseline_score
        if -CONDITIONAL_PASS_TOLERANCE <= delta < 0:
            return TieredJudgment(
                verdict="CONDITIONAL_PASS",
                reason=f"Score below baseline by {abs(delta):.3f} (within tolerance window)",
                baseline_delta=delta,
                minor_violations=minor_total,
            )

    # Rule 6: PASS
    return TieredJudgment(
        verdict="PASS",
        reason="All tiered tolerance checks passed",
        major_violations=sum(v for v in major_counts.values()),
        minor_violations=minor_total,
    )


def _normalize_signal_type(raw_type: str) -> str:
    """Normalize LLM signal_type output to standard enum names.
    
    Handles cases where LLM returns:
    - G-codes: "G7", "G1", "【G7】Universal Banned Words"
    - Mixed formats: "G7 (Universal Banned Words)"
    - Already-standard: "AI_VOCABULARY" → pass through
    """
    if not raw_type:
        return "unknown"
    
    # Already in standard form (uppercase with underscores)
    upper = raw_type.strip().upper()
    if upper in SIGNAL_TOLERANCE_TIERS:
        return upper
    
    # Try to extract G-code from various formats
    # Match patterns like "G7", "【G7】...", "G7 (...)", "S_GENERAL【G7】..."
    g_match = re.search(r'[【\[]?G(\d+)[】\]]?', raw_type, re.IGNORECASE)
    if g_match:
        g_num = f"g{g_match.group(1)}"
        if g_num in _GCODE_TO_SIGNAL:
            return _GCODE_TO_SIGNAL[g_num]
    
    # Return as-is if no normalization found (eval's SIGNAL_TYPE_MAP handles fuzzy match)
    return raw_type


def _parse_verdict(response: str) -> DeAIVerdict:
    """Parse LLM audit response into DeAIVerdict."""
    response = response.strip()
    if response.startswith("```"):
        response = response.split("```")[1]
        if response.startswith("json"):
            response = response[4:]
    
    try:
        data = json.loads(response)
        signals = [
            AISignal(
                sentence=s.get("sentence", ""),
                signal_type=_normalize_signal_type(s.get("signal_type", "unknown")),
                confidence=s.get("confidence", 0.5),
                fix_suggestion=s.get("fix_suggestion", ""),
            )
            for s in data.get("signals", [])
        ]
        return DeAIVerdict(
            is_natural=data.get("is_natural", True),
            overall_score=data.get("overall_score", 0.7),
            signals=signals,
            summary=data.get("summary", ""),
        )
    except (json.JSONDecodeError, KeyError, TypeError):
        # Parsing failed — assume natural to avoid blocking pipeline
        return DeAIVerdict(
            is_natural=True,
            overall_score=0.7,
            signals=[],
            summary="(Audit parse error — defaulting to PASS)",
        )


def _load_rules(scene: str) -> str:
    """Load de-AI rules for the given scene.
    
    Uses the structured YAML rule loader (tools/deai/rules/loader.py) as primary
    source. Falls back to the legacy Markdown parser if YAML files are unavailable.
    
    Returns formatted text suitable for LLM audit prompt injection.
    """
    # ─── Primary: structured YAML loader ───
    try:
        from tools.deai.rules.loader import load_rules_for_audit
        result = load_rules_for_audit(scene)
        if result and "Unknown scene" not in result and "not found" not in result:
            return result
    except (ImportError, Exception):
        pass  # Fall through to legacy loader

    # ─── Fallback: legacy Markdown parser ───
    if not RULES_PATH.exists():
        return "(No rules file found — using general detection heuristics)"
    
    content = RULES_PATH.read_text(encoding="utf-8")
    lines = content.split("\n")
    
    # Identify key section boundaries
    general_start = None
    scene_start = None
    scene_end = None
    fix_start = None
    general_end = None
    
    scene_header = f"## {scene}:"  # e.g., "## S1:" or "## S2:"
    
    for i, line in enumerate(lines):
        if line.strip().startswith("## S_GENERAL:"):
            general_start = i
        elif general_start and general_end is None and line.startswith("## S") and "S_GENERAL" not in line:
            general_end = i
        if line.strip().startswith(scene_header):
            scene_start = i
        elif scene_start and scene_end is None and line.startswith("## S") and i > scene_start and scene_header not in line:
            scene_end = i
        if line.strip().startswith("## Scoring") or line.strip().startswith("## Fix Principles"):
            if fix_start is None:
                fix_start = i
    
    # Assemble output
    parts = []
    
    # Always include S_GENERAL
    if general_start is not None:
        parts.append("\n".join(lines[general_start:(general_end or scene_start or fix_start or len(lines))]))
    
    # Include scene-specific block
    if scene_start is not None:
        parts.append("\n".join(lines[scene_start:(scene_end or fix_start or len(lines))]))
    
    # Include shared tail (Scoring, Fix Principles, Priority Chain, Self-Check)
    if fix_start is not None:
        parts.append("\n".join(lines[fix_start:]))
    
    if parts:
        return "\n\n---\n\n".join(parts)
    
    # Fallback: return full content
    return content


def format_deai_result(verdict: DeAIVerdict, fixes: List[Dict] = None) -> str:
    """Format de-AI audit result for display, including dimension breakdown."""
    lines = []

    # Header with tiered verdict
    if verdict.tiered_judgment:
        tj = verdict.tiered_judgment
        verdict_map = {"PASS": "✅ PASS", "FAIL": "❌ FAIL", "CONDITIONAL_PASS": "⚠️ CONDITIONAL PASS"}
        status = verdict_map.get(tj.verdict, "❓ UNKNOWN")
        lines.append(f"De-AI Audit: {status} (score: {verdict.overall_score:.3f})")
        lines.append(f"  Judgment: {tj.reason}")
        if tj.critical_signals:
            lines.append(f"  Critical: {', '.join(tj.critical_signals[:3])}")
    else:
        status = "✅ PASS" if verdict.is_natural else "❌ FAIL"
        lines.append(f"De-AI Audit: {status} (score: {verdict.overall_score:.2f})")

    if verdict.summary:
        lines.append(f"  {verdict.summary}")

    # Hard Caps (TODO-3)
    if verdict.hard_caps and verdict.hard_caps.triggered:
        lines.append("")
        lines.append("  Hard Caps 🚫:")
        for reason in verdict.hard_caps.reasons:
            lines.append(f"    ⛔ {reason}")

    # Dimension scores (TODO-2)
    if verdict.dimensions:
        lines.append("")
        lines.append(verdict.dimensions.diagnosis_report())

    if verdict.signals:
        lines.append(f"\n  Signals detected ({len(verdict.signals)}):")
        for i, sig in enumerate(verdict.signals[:5]):
            tier = SIGNAL_TOLERANCE_TIERS.get(sig.signal_type.upper(), DEFAULT_SIGNAL_TIER)
            tier_badge = {"critical": "🔴", "major": "🟡", "minor": "⚪"}.get(tier, "⚪")
            lines.append(f"    {tier_badge} [{sig.signal_type}] (conf: {sig.confidence:.1f})")
            lines.append(f"      \"{sig.sentence[:80]}...\"")
            if sig.fix_suggestion:
                lines.append(f"      → \"{sig.fix_suggestion[:80]}...\"")
        if len(verdict.signals) > 5:
            lines.append(f"    ... and {len(verdict.signals) - 5} more")

    if fixes:
        lines.append(f"\n  Fixes applied: {len(fixes)}")
        for fix in fixes[:3]:
            lines.append(f"    - \"{fix.get('original', '')[:60]}\" → \"{fix.get('fixed', '')[:60]}\"")

    return "\n".join(lines)


async def deai_audit(
    text: str,
    original_text: str = None,
    scene: str = "S1",
    provider: str = None,
    model: str = None,
    skip_precheck: bool = False,
    review_hints: str = "",
    review_hints_structured: Optional[List] = None,
) -> DeAIVerdict:
    """
    Run de-AI detection on text. Returns verdict with signals.
    
    Args:
        text: The text to audit (typically post-rewrite)
        original_text: The pre-rewrite version (for comparison context)
        scene: "S1" (CS academic) or "S3" (economics) — affects rule loading
        provider/model: LLM config
        skip_precheck: If True, bypass the L1 regex/stats gate (used in eval mode
                       to ensure full LLM audit is always exercised)
        review_hints: Pre-formatted context from review engine (expression issues
                     the reviewer flagged). Injected into audit prompt for awareness.
        review_hints_structured: Optional List[ReviewHint] objects from bridge module.
                     Used to compute dimension_biases for scoring amplification.
    """
    # L1 Pre-check: zero-cost regex/stats gate
    from tools.deai_precheck import quick_ai_precheck
    needs_audit, precheck_diagnostics = quick_ai_precheck(text)
    if not needs_audit and not skip_precheck:
        return DeAIVerdict(
            is_natural=True,
            overall_score=0.9,  # Conservative: don't give perfect score without LLM
            signals=[],
            summary=f"L1 precheck PASS ({precheck_diagnostics.get('reason', 'all_signals_clean')})",
        )

    # L2: Full LLM audit
    rules = _load_rules(scene)
    client = LLMClient(model=model, max_concurrent=3, provider=provider)

    # Load Voice Profile for context
    voice_fp = load_voice_profile()
    voice_section = ""
    if voice_fp.total_words_analyzed > 0:
        voice_constraints = get_voice_constraints(voice_fp)
        voice_section = VOICE_AUDIT_ADDENDUM.format(voice_constraints=voice_constraints)

    # Build audit prompt
    system = DEAI_AUDIT_PROMPT.format(rules=rules, voice_section=voice_section)
    # Inject review hints (contextual awareness from reviewer, not directives)
    if review_hints:
        system += f"\n{review_hints}"
    user = f"Text to audit:\n\n{text}"
    if original_text:
        user += f"\n\n---\n[For context, this is the PRE-rewrite version — do NOT audit this, just use for comparison]\n{original_text[:1000]}"

    response = await client.chat(
        system=system,
        user=user,
        max_tokens=2000,
        temperature=0.0,
        model=get_model_for_task("deai_audit"),
    )

    verdict = _parse_verdict(response)

    # ── Hard Caps: programmatic detection, independent of LLM signals (TODO-3) ──
    hard_cap_result = detect_hard_caps(text)
    verdict.hard_caps = hard_cap_result

    # ── Programmatic Signal Injection (P7): zero-LLM-cost detection for weak signals ──
    injected = _detect_programmatic_signals(text)
    if injected:
        # Avoid duplicates: only inject if LLM didn't already detect the same signal type
        existing_types = {s.signal_type for s in verdict.signals}
        for sig in injected:
            if sig.signal_type not in existing_types:
                verdict.signals.append(sig)

    # ── Compute multi-dimension scores and tiered judgment (TODO-1 + TODO-2 + TODO-3) ──
    if verdict.signals:
        # Compute reviewer-based dimension biases (soft scoring amplification)
        _dim_biases = None
        if review_hints_structured:
            from tools.review_deai_bridge import compute_dimension_bias
            _dim_biases = compute_dimension_bias(review_hints_structured)
        dimensions = compute_dimension_scores(verdict.signals, dimension_biases=_dim_biases)
        # Apply hard caps to clamp dimension scores (TODO-3)
        if hard_cap_result.triggered:
            dimensions = hard_cap_result.apply_to(dimensions)
        judgment = apply_tiered_judgment(verdict.signals, dimensions)
        # Propagate hard cap info into judgment
        if hard_cap_result.triggered:
            judgment.hard_caps_triggered = hard_cap_result.reasons
            # If hard caps force a dimension below floor, override verdict to FAIL
            floor_dim = dimensions.floor_violated()
            if floor_dim and judgment.verdict == "PASS":
                judgment.verdict = "FAIL"
                judgment.reason = (
                    f"Hard cap forced '{floor_dim}' below floor "
                    f"({getattr(dimensions, floor_dim):.2f} < {DIMENSION_FLOOR})"
                )
                judgment.dimension_floor_violated = floor_dim
        verdict.dimensions = dimensions
        verdict.tiered_judgment = judgment
        # Override overall_score with dimension-weighted score for consistency
        verdict.overall_score = dimensions.weighted_overall()
        # Override is_natural based on tiered judgment (replaces flat threshold)
        verdict.is_natural = (judgment.verdict == "PASS")
    else:
        # No signals detected
        verdict.dimensions = DimensionScores()  # All 1.0
        # Even without LLM signals, hard caps may still apply
        if hard_cap_result.triggered:
            capped_dims = hard_cap_result.apply_to(verdict.dimensions)
            verdict.dimensions = capped_dims
            verdict.overall_score = capped_dims.weighted_overall()
            verdict.tiered_judgment = TieredJudgment(
                verdict="FAIL",
                reason=f"Hard caps triggered without LLM signals: {'; '.join(hard_cap_result.reasons)}",
                hard_caps_triggered=hard_cap_result.reasons,
            )
            verdict.is_natural = False
        elif verdict.is_natural:
            # LLM agrees text is natural — confirm PASS
            verdict.tiered_judgment = TieredJudgment(
                verdict="PASS", reason="No AI signals detected"
            )
        else:
            # LLM flagged as unnatural but provided no specific signals
            # Respect LLM verdict — mark as FAIL with advisory
            verdict.tiered_judgment = TieredJudgment(
                verdict="FAIL",
                reason="LLM flagged text as unnatural but provided no specific signals (manual review recommended)",
            )
            verdict.overall_score = min(verdict.overall_score, 0.5)

    # Save audit result
    audit_dir = WORKSPACE / "deai"
    audit_dir.mkdir(parents=True, exist_ok=True)

    return verdict
