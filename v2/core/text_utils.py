"""
core/text_utils.py — 文本处理工具函数

从 tool_handlers/findings.py 提取，统一术语提取逻辑。
支持英文词汇 + CJK bigram 双模式。
"""

from __future__ import annotations

import re

# ==============================================================
# Stopwords (共享，避免重复定义)
# ==============================================================

# BASIC 停用词集 — 用于假说匹配等场景（原 check_verification_integrity / hdwm_match_and_resolve）
EN_STOPWORDS_BASIC: frozenset[str] = frozenset({
    'this', 'that', 'with', 'from', 'have', 'been', 'which', 'their',
    'more', 'than', 'also', 'some', 'other', 'about', 'would', 'could',
    'should', 'these', 'those', 'into', 'only', 'very', 'such', 'each',
    'finding', 'section', 'paper', 'author', 'however', 'therefore',
})

# EXTENDED 停用词集 — 用于 finding 去重（原 check_finding_overlap）
EN_STOPWORDS_EXTENDED: frozenset[str] = EN_STOPWORDS_BASIC | frozenset({
    'does', 'will', 'what', 'when', 'where', 'there', 'over', 'under',
    'between', 'through', 'during', 'before', 'after', 'above', 'below',
})

# 向后兼容别名
EN_STOPWORDS: frozenset[str] = EN_STOPWORDS_EXTENDED

CJK_STOPWORDS: frozenset[str] = frozenset({
    '的', '了', '是', '在', '和', '与', '有', '对', '为', '但',
    '中', '等', '已', '此', '其', '不', '也', '而', '或', '该',
    '以', '于', '到', '被', '从', '由', '可', '将', '时', '如',
})


# ==============================================================
# Public API
# ==============================================================

def extract_terms(
    text: str,
    *,
    include_cjk: bool = True,
    extended_stopwords: bool = True,
) -> set[str]:
    """
    提取有意义的术语用于文本相似度比较。

    - 英文：4+ 字母的单词，去停用词
    - CJK：二字词组 (bigrams)，去单字停用词

    Args:
        text: 输入文本
        include_cjk: 是否包含 CJK bigram 提取（默认 True）
        extended_stopwords: 是否使用扩展停用词集（默认 True）。
            - True: 用于 finding 去重场景（过滤更多通用词，提高去重精度）
            - False: 用于假说匹配场景（保留更多词，降低漏匹配风险）

    Returns:
        术语集合（英文小写 + CJK bigram 以 "cjk_" 前缀标记）
    """
    stopwords = EN_STOPWORDS_EXTENDED if extended_stopwords else EN_STOPWORDS_BASIC

    # 英文术语（4+ 字母）
    en_words = set(re.findall(r'[a-zA-Z]{4,}', text.lower()))
    terms = {w for w in en_words if w not in stopwords}

    # CJK 二字词组
    if include_cjk:
        cjk_chars = re.findall(r'[\u4e00-\u9fff]', text)
        cjk_filtered = [c for c in cjk_chars if c not in CJK_STOPWORDS]
        for i in range(len(cjk_filtered) - 1):
            terms.add(f"cjk_{cjk_filtered[i]}{cjk_filtered[i + 1]}")

    return terms
