"""
utils/voice_profile.py — Voice Profile: Writing Style Preservation.

Extracts and preserves the author's unique writing voice across rewrites.
Prevents the common problem where AI rewrites homogenize all text into
generic academic prose.

Two phases:
1. EXTRACT: Analyze original paper sections to build a voice fingerprint
2. APPLY: Inject voice constraints into rewrite prompts

Voice dimensions tracked:
- Sentence length distribution (mean, variance, range)
- Active/passive voice ratio
- Hedging patterns (frequency and preferred words)
- Transition word preferences
- Paragraph structure patterns
- Vocabulary complexity (Flesch-Kincaid proxy)
- Punctuation patterns (semicolons, em-dashes, parentheticals)
- Opening patterns (how paragraphs typically start)

Persistence: .workspace/voice_profile.json
"""

from __future__ import annotations

import re
import json
import statistics
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, field, asdict

WORKSPACE = Path(".workspace")
VOICE_PATH = WORKSPACE / "voice_profile.json"

# Common hedging words in academic writing
HEDGE_WORDS = {
    "may", "might", "could", "possibly", "perhaps", "likely",
    "suggests", "indicates", "appears", "seems", "tends",
    "relatively", "somewhat", "approximately", "roughly",
    "arguably", "presumably", "potentially",
}

# Passive voice indicators (simplified regex detection)
PASSIVE_PATTERNS = [
    r"\b(?:is|are|was|were|been|being)\s+\w+ed\b",
    r"\b(?:is|are|was|were|been|being)\s+\w+en\b",
]


@dataclass
class VoiceFingerprint:
    """Quantified writing style metrics."""
    # Sentence metrics
    avg_sentence_length: float = 0.0  # words per sentence
    sentence_length_std: float = 0.0  # variance in length
    min_sentence_length: int = 0
    max_sentence_length: int = 0
    
    # Voice and structure
    passive_ratio: float = 0.0  # 0.0-1.0
    hedge_frequency: float = 0.0  # hedges per 100 words
    preferred_hedges: List[str] = field(default_factory=list)  # Top 5 used
    
    # Transitions and connectors
    preferred_transitions: List[str] = field(default_factory=list)
    
    # Punctuation style
    semicolons_per_1000_words: float = 0.0
    parentheticals_per_1000_words: float = 0.0
    dashes_per_1000_words: float = 0.0
    
    # Paragraph patterns
    avg_paragraph_length: float = 0.0  # sentences per paragraph
    opening_patterns: List[str] = field(default_factory=list)  # Common para starters
    
    # Vocabulary
    avg_word_length: float = 0.0  # chars per word
    
    # Raw stats for comparison
    total_words_analyzed: int = 0
    total_sentences_analyzed: int = 0
    sections_analyzed: List[str] = field(default_factory=list)


def extract_voice(text: str, section_id: str = "") -> VoiceFingerprint:
    """
    Extract voice fingerprint from a text sample.
    Call this on each original section to build cumulative profile.
    """
    fp = VoiceFingerprint()
    
    # Tokenize
    sentences = _split_sentences(text)
    words = text.split()
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    
    if not sentences or not words:
        return fp
    
    fp.total_words_analyzed = len(words)
    fp.total_sentences_analyzed = len(sentences)
    if section_id:
        fp.sections_analyzed.append(section_id)
    
    # Sentence length stats
    sent_lengths = [len(s.split()) for s in sentences]
    fp.avg_sentence_length = round(statistics.mean(sent_lengths), 1)
    fp.sentence_length_std = round(statistics.stdev(sent_lengths), 1) if len(sent_lengths) > 1 else 0.0
    fp.min_sentence_length = min(sent_lengths)
    fp.max_sentence_length = max(sent_lengths)
    
    # Passive voice ratio
    passive_count = sum(
        1 for sent in sentences
        if any(re.search(pat, sent, re.IGNORECASE) for pat in PASSIVE_PATTERNS)
    )
    fp.passive_ratio = round(passive_count / len(sentences), 2)
    
    # Hedging
    text_lower = text.lower()
    hedge_counts = {}
    for hedge in HEDGE_WORDS:
        count = len(re.findall(r'\b' + hedge + r'\b', text_lower))
        if count > 0:
            hedge_counts[hedge] = count
    
    total_hedges = sum(hedge_counts.values())
    fp.hedge_frequency = round(total_hedges / len(words) * 100, 2)
    fp.preferred_hedges = sorted(hedge_counts, key=hedge_counts.get, reverse=True)[:5]
    
    # Punctuation patterns (per 1000 words)
    word_count = len(words)
    fp.semicolons_per_1000_words = round(text.count(";") / word_count * 1000, 1)
    fp.parentheticals_per_1000_words = round(text.count("(") / word_count * 1000, 1)
    fp.dashes_per_1000_words = round(
        (text.count("—") + text.count(" - ")) / word_count * 1000, 1
    )
    
    # Paragraph stats
    if paragraphs:
        para_sent_counts = [len(_split_sentences(p)) for p in paragraphs]
        fp.avg_paragraph_length = round(statistics.mean(para_sent_counts), 1)
        
        # Opening patterns (first 3-4 words of each paragraph)
        openers = []
        for p in paragraphs[:20]:
            first_words = p.split()[:4]
            if first_words:
                openers.append(" ".join(first_words))
        fp.opening_patterns = openers[:10]
    
    # Vocabulary complexity
    if words:
        fp.avg_word_length = round(statistics.mean(len(w) for w in words), 1)
    
    # Transitions
    transition_patterns = [
        "however", "moreover", "furthermore", "nevertheless",
        "in addition", "consequently", "therefore", "thus",
        "specifically", "in particular", "notably", "importantly",
        "in contrast", "on the other hand", "alternatively",
    ]
    found_transitions = []
    for t in transition_patterns:
        if t.lower() in text_lower:
            count = text_lower.count(t.lower())
            found_transitions.append((t, count))
    
    found_transitions.sort(key=lambda x: x[1], reverse=True)
    fp.preferred_transitions = [t for t, _ in found_transitions[:8]]
    
    return fp


def merge_fingerprints(existing: VoiceFingerprint, new: VoiceFingerprint) -> VoiceFingerprint:
    """
    Merge a new section's fingerprint into the cumulative profile.
    Uses weighted average based on word counts.
    """
    if existing.total_words_analyzed == 0:
        return new
    if new.total_words_analyzed == 0:
        return existing
    
    total = existing.total_words_analyzed + new.total_words_analyzed
    w1 = existing.total_words_analyzed / total
    w2 = new.total_words_analyzed / total
    
    merged = VoiceFingerprint()
    merged.total_words_analyzed = total
    merged.total_sentences_analyzed = existing.total_sentences_analyzed + new.total_sentences_analyzed
    merged.sections_analyzed = existing.sections_analyzed + new.sections_analyzed
    
    # Weighted averages for numeric metrics
    merged.avg_sentence_length = round(existing.avg_sentence_length * w1 + new.avg_sentence_length * w2, 1)
    merged.sentence_length_std = round(existing.sentence_length_std * w1 + new.sentence_length_std * w2, 1)
    merged.min_sentence_length = min(existing.min_sentence_length, new.min_sentence_length)
    merged.max_sentence_length = max(existing.max_sentence_length, new.max_sentence_length)
    
    merged.passive_ratio = round(existing.passive_ratio * w1 + new.passive_ratio * w2, 2)
    merged.hedge_frequency = round(existing.hedge_frequency * w1 + new.hedge_frequency * w2, 2)
    merged.avg_word_length = round(existing.avg_word_length * w1 + new.avg_word_length * w2, 1)
    merged.avg_paragraph_length = round(existing.avg_paragraph_length * w1 + new.avg_paragraph_length * w2, 1)
    
    merged.semicolons_per_1000_words = round(existing.semicolons_per_1000_words * w1 + new.semicolons_per_1000_words * w2, 1)
    merged.parentheticals_per_1000_words = round(existing.parentheticals_per_1000_words * w1 + new.parentheticals_per_1000_words * w2, 1)
    merged.dashes_per_1000_words = round(existing.dashes_per_1000_words * w1 + new.dashes_per_1000_words * w2, 1)
    
    # Merge lists (deduplicate, keep order)
    merged.preferred_hedges = _merge_lists(existing.preferred_hedges, new.preferred_hedges, 5)
    merged.preferred_transitions = _merge_lists(existing.preferred_transitions, new.preferred_transitions, 8)
    merged.opening_patterns = (existing.opening_patterns + new.opening_patterns)[:10]
    
    return merged


def load_voice_profile() -> VoiceFingerprint:
    """Load or create voice profile."""
    if VOICE_PATH.exists():
        try:
            data = json.loads(VOICE_PATH.read_text(encoding="utf-8"))
            return VoiceFingerprint(**data)
        except (json.JSONDecodeError, TypeError):
            pass
    return VoiceFingerprint()


def save_voice_profile(fp: VoiceFingerprint):
    """Persist voice profile."""
    VOICE_PATH.parent.mkdir(parents=True, exist_ok=True)
    VOICE_PATH.write_text(
        json.dumps(asdict(fp), indent=2, ensure_ascii=False), encoding="utf-8"
    )


def build_voice_profile_from_paper():
    """
    Analyze all original paper sections and build cumulative voice profile.
    Called once after parse_paper.
    """
    index_path = WORKSPACE / "paper" / "section_index.json"
    if not index_path.exists():
        return
    
    index = json.loads(index_path.read_text(encoding="utf-8"))
    cumulative = VoiceFingerprint()
    
    for entry in index:
        sec_path = Path(entry["file"])
        if not sec_path.exists():
            continue
        content = sec_path.read_text(encoding="utf-8")
        if len(content) < 100:  # Skip very short sections
            continue
        
        section_fp = extract_voice(content, entry.get("id", entry.get("slug", "")))
        cumulative = merge_fingerprints(cumulative, section_fp)
    
    save_voice_profile(cumulative)
    return cumulative


def get_voice_constraints(fp: VoiceFingerprint = None) -> str:
    """
    Generate voice constraint text to inject into rewrite prompts.
    Keeps it concise and actionable.
    """
    if fp is None:
        fp = load_voice_profile()
    
    if fp.total_words_analyzed == 0:
        return ""
    
    constraints = []
    
    # Sentence length
    constraints.append(
        f"Sentence length: target avg {fp.avg_sentence_length} words "
        f"(range {fp.min_sentence_length}-{fp.max_sentence_length}, "
        f"std dev {fp.sentence_length_std}). Vary length naturally."
    )
    
    # Voice ratio
    if fp.passive_ratio > 0.4:
        constraints.append(f"Passive voice ratio: ~{int(fp.passive_ratio*100)}% (maintain this level).")
    elif fp.passive_ratio < 0.2:
        constraints.append(f"Active voice dominant (~{int((1-fp.passive_ratio)*100)}%). Keep active.")
    
    # Hedging
    if fp.preferred_hedges:
        constraints.append(
            f"Hedging frequency: {fp.hedge_frequency}/100 words. "
            f"Preferred: {', '.join(fp.preferred_hedges[:3])}."
        )
    
    # Punctuation style
    style_notes = []
    if fp.semicolons_per_1000_words > 2:
        style_notes.append("uses semicolons")
    if fp.parentheticals_per_1000_words > 5:
        style_notes.append("uses parentheticals frequently")
    if fp.dashes_per_1000_words > 2:
        style_notes.append("uses em-dashes")
    if style_notes:
        constraints.append(f"Punctuation style: author {', '.join(style_notes)}.")
    
    # Transitions
    if fp.preferred_transitions:
        constraints.append(
            f"Transition preferences: {', '.join(fp.preferred_transitions[:5])}."
        )
    
    if not constraints:
        return ""
    
    header = "## Voice Preservation (match author's writing style):\n"
    return header + "\n".join(f"- {c}" for c in constraints)


def check_voice_drift(original_text: str, revised_text: str, fp: VoiceFingerprint = None) -> Dict:
    """
    Compare revised text against voice profile to detect style drift.
    Returns a dict with drift metrics and warnings.
    """
    if fp is None:
        fp = load_voice_profile()
    
    if fp.total_words_analyzed == 0:
        return {"drift_detected": False, "reason": "no profile"}
    
    revised_fp = extract_voice(revised_text)
    
    warnings = []
    
    # Check sentence length drift
    if abs(revised_fp.avg_sentence_length - fp.avg_sentence_length) > fp.sentence_length_std * 1.5:
        warnings.append(
            f"Sentence length drifted: {revised_fp.avg_sentence_length} vs profile {fp.avg_sentence_length}"
        )
    
    # Check passive voice drift
    if abs(revised_fp.passive_ratio - fp.passive_ratio) > 0.2:
        warnings.append(
            f"Passive voice shifted: {revised_fp.passive_ratio:.0%} vs profile {fp.passive_ratio:.0%}"
        )
    
    # Check hedge frequency drift
    if abs(revised_fp.hedge_frequency - fp.hedge_frequency) > fp.hedge_frequency * 0.5:
        warnings.append(
            f"Hedging changed: {revised_fp.hedge_frequency:.1f}/100w vs profile {fp.hedge_frequency:.1f}/100w"
        )
    
    return {
        "drift_detected": len(warnings) > 0,
        "warnings": warnings,
        "revised_metrics": {
            "avg_sentence_length": revised_fp.avg_sentence_length,
            "passive_ratio": revised_fp.passive_ratio,
            "hedge_frequency": revised_fp.hedge_frequency,
        },
    }


def _split_sentences(text: str) -> List[str]:
    """Simple sentence splitter for English academic text."""
    # Handle common abbreviations
    text = re.sub(r'\b(Dr|Mr|Mrs|Ms|Prof|et al|vs|i\.e|e\.g)\.',
                  lambda m: m.group(0).replace('.', '<DOT>'), text)
    
    # Split on sentence-ending punctuation followed by space+capital or end
    sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z])', text)
    
    # Restore dots
    sentences = [s.replace('<DOT>', '.').strip() for s in sentences if s.strip()]
    return sentences


def _merge_lists(list1: List[str], list2: List[str], max_items: int) -> List[str]:
    """Merge two lists maintaining order, deduplicating."""
    seen = set()
    merged = []
    for item in list1 + list2:
        if item not in seen:
            seen.add(item)
            merged.append(item)
        if len(merged) >= max_items:
            break
    return merged
