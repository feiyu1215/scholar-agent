"""
tools/deai/scene.py - Scene detection logic.

Detects whether text should be processed with S1 (CS), S2 (Chinese), or S3 (Economics) rules.
"""

from __future__ import annotations

from typing import Dict, Optional

from tools.deai.constants import (
    _S3_DISCIPLINES,
    _ECON_KEYWORDS_EN,
    _ECON_KEYWORDS_ZH,
    _ECON_THRESHOLD,
)


def _is_chinese_text(text: str) -> bool:
    """Heuristic: text is primarily Chinese if >30% of non-space chars are CJK."""
    non_space = text.replace(" ", "").replace("\n", "")
    if not non_space:
        return False
    cjk_count = sum(1 for c in non_space if '\u4e00' <= c <= '\u9fff')
    return cjk_count / len(non_space) > 0.3


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
    
    return False


def detect_scene(text: str, metadata: dict = None) -> str:
    """
    Auto-detect the appropriate DeAI rule scene.
    
    Priority order (discipline outranks language):
      1. Economics/Finance/Business → S3 (regardless of language)
      2. Chinese text (non-economics) → S2
      3. English text (non-economics) → S1
    
    Data sources (in priority order):
      1. metadata dict (from .workspace/paper/metadata.json, written at parse time)
      2. field_detector real-time detection (fallback for standalone calls)
      3. Language heuristic (_is_chinese_text)
    
    Returns: "S1", "S2", or "S3"
    """
    # ── Resolve discipline ──
    discipline = None
    
    if metadata:
        # metadata.discipline comes from field_detector via paper_parser
        discipline = metadata.get("discipline", "").strip() or None
    
    # Check metadata discipline first (may be user-set or field_detector output)
    if discipline and _is_s3_discipline(discipline):
        return "S3"
    
    if not discipline:
        # Fallback: run field_detector in real-time (zero LLM cost)
        try:
            from utils.field_detector import detect_field
            field, confidence = detect_field(abstract=text[:1500])
            if confidence >= 0.3:  # Lower threshold for scene routing
                discipline = field
                if _is_s3_discipline(discipline):
                    return "S3"
        except ImportError:
            pass
    
    # ── Lightweight economics keyword fallback ──
    # field_detector only has English keywords; for Chinese economics text
    # (or short English text below confidence threshold), we need a direct check.
    # BUT: skip if metadata already declared a non-economics discipline
    # (metadata is the single source of truth when present).
    if not discipline and _has_economics_keywords(text):
        return "S3"
    
    # ── Resolve language ──
    is_zh = False
    if metadata and metadata.get("language"):
        is_zh = metadata["language"] == "zh"
    else:
        is_zh = _is_chinese_text(text)
    
    # ── Route by language (non-economics) ──
    return "S2" if is_zh else "S1"
