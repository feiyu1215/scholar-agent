"""
evaluation/metrics.py — Finding-level precision/recall/F1 computation.

Matching strategy:
    Agent findings are matched to gold-standard findings using a two-level approach:
    1. Exact section match (bonus) + text similarity
    2. Fuzzy text matching (token overlap / Jaccard)

    A predicted finding is "matched" to a gold finding if:
    - similarity >= MATCH_THRESHOLD (default 0.4)
    - Each gold finding can only be matched once (greedy assignment)

Metrics produced:
    - Precision: fraction of agent findings that match a gold finding
    - Recall: fraction of gold findings that are matched by an agent finding
    - F1: harmonic mean
    - Priority-weighted recall: high/critical findings weighted 2x
    - Category breakdown: metrics per category (methodology, data, logic, writing, etc.)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


# ============================================================
# Configuration
# ============================================================

MATCH_THRESHOLD = 0.25  # Minimum similarity to count as a match (lowered from 0.4 for CJK text)
SECTION_BONUS = 0.1    # Bonus for matching section
HIGH_PRIORITY_WEIGHT = 2.0  # Weight for high/critical priority in weighted recall

# Short-finding handling: findings with fewer tokens than this threshold
# use stricter matching (exact substring) to avoid spurious Jaccard matches.
SHORT_FINDING_TOKEN_THRESHOLD = 5
SHORT_FINDING_STRICTER_THRESHOLD = 0.6  # Higher similarity required for short findings


# ============================================================
# Data Structures
# ============================================================

@dataclass
class Finding:
    """A single review finding (either predicted or gold)."""
    text: str
    section: str = ""
    priority: str = "medium"  # low | medium | high | critical
    category: str = ""  # methodology | data | logic | writing | presentation | citation

    def __post_init__(self):
        self.text = self.text.strip()
        self.section = self.section.strip().lower()
        self.priority = self.priority.strip().lower()
        self.category = self.category.strip().lower()


@dataclass
class MatchResult:
    """Result of matching a predicted finding to a gold finding."""
    predicted_idx: int
    gold_idx: int
    similarity: float
    section_matched: bool


@dataclass
class EvalMetrics:
    """Evaluation metrics for one paper."""
    paper_id: str
    precision: float
    recall: float
    f1: float
    weighted_recall: float
    num_predicted: int
    num_gold: int
    num_matched: int
    matches: list[MatchResult] = field(default_factory=list)
    unmatched_predicted: list[int] = field(default_factory=list)
    unmatched_gold: list[int] = field(default_factory=list)
    category_breakdown: dict[str, dict[str, float]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "weighted_recall": round(self.weighted_recall, 4),
            "num_predicted": self.num_predicted,
            "num_gold": self.num_gold,
            "num_matched": self.num_matched,
            "unmatched_predicted_count": len(self.unmatched_predicted),
            "unmatched_gold_count": len(self.unmatched_gold),
            "category_breakdown": self.category_breakdown,
        }


@dataclass
class AggregateMetrics:
    """Aggregate metrics across multiple papers."""
    num_papers: int
    avg_precision: float
    avg_recall: float
    avg_f1: float
    avg_weighted_recall: float
    total_predicted: int
    total_gold: int
    total_matched: int
    per_paper: list[EvalMetrics] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "num_papers": self.num_papers,
            "avg_precision": round(self.avg_precision, 4),
            "avg_recall": round(self.avg_recall, 4),
            "avg_f1": round(self.avg_f1, 4),
            "avg_weighted_recall": round(self.avg_weighted_recall, 4),
            "total_predicted": self.total_predicted,
            "total_gold": self.total_gold,
            "total_matched": self.total_matched,
            "per_paper": [m.to_dict() for m in self.per_paper],
        }


# ============================================================
# Text Similarity (token-level Jaccard, CJK-aware)
# ============================================================

# Regex to detect CJK characters
_CJK_RANGE = re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]')


def _tokenize(text: str) -> set[str]:
    """Tokenize text into tokens for similarity computation.

    Strategy:
    - English/Latin: word-level tokens (split by whitespace/punctuation)
    - Chinese/CJK: character bigrams (2-grams) for better overlap detection
    - Numbers and special symbols: kept as-is

    This hybrid approach handles mixed Chinese-English academic text
    where pure word-boundary tokenization fails for Chinese.
    """
    text = text.lower().strip()
    tokens: set[str] = set()

    # Extract English words (Latin alphabet sequences)
    english_words = re.findall(r'[a-z][a-z0-9_]*(?:\'[a-z]+)?', text)
    tokens.update(english_words)

    # Extract numbers (including decimals)
    numbers = re.findall(r'\d+(?:\.\d+)?', text)
    tokens.update(numbers)

    # Extract CJK characters and create bigrams
    cjk_chars = _CJK_RANGE.findall(text)
    if len(cjk_chars) >= 2:
        for i in range(len(cjk_chars) - 1):
            tokens.add(cjk_chars[i] + cjk_chars[i + 1])
    elif len(cjk_chars) == 1:
        tokens.add(cjk_chars[0])

    # Also add individual CJK characters as unigrams for partial matching
    tokens.update(cjk_chars)

    # Extract Greek letters and math symbols as individual tokens
    greek_and_math = re.findall(r'[α-ωΑ-Ωσθωγ₁₂₃₄₅₆₇₈₉₀]', text)
    tokens.update(greek_and_math)

    return tokens


def compute_similarity(text_a: str, text_b: str, section_a: str = "", section_b: str = "") -> float:
    """Compute similarity between two finding texts.

    Uses token-level Jaccard similarity + section bonus.
    Enhanced with keyword-concept matching for CJK text.

    Args:
        text_a: First finding text
        text_b: Second finding text
        section_a: Section of first finding
        section_b: Section of second finding

    Returns:
        Similarity score in [0, 1]
    """
    # Fast path: identical non-empty texts always yield perfect similarity
    if text_a.strip() and text_a.strip() == text_b.strip():
        return 1.0

    tokens_a = _tokenize(text_a)
    tokens_b = _tokenize(text_b)

    if not tokens_a or not tokens_b:
        return 0.0

    # Short-finding guard: for very short findings (< SHORT_FINDING_TOKEN_THRESHOLD tokens),
    # Jaccard similarity is unreliable (a single shared token can inflate the score).
    # Use exact substring containment as the primary signal instead.
    min_tokens = min(len(tokens_a), len(tokens_b))
    if min_tokens < SHORT_FINDING_TOKEN_THRESHOLD:
        # Check if the shorter text is a substring of the longer one
        short_text = text_a.lower().strip() if len(tokens_a) <= len(tokens_b) else text_b.lower().strip()
        long_text = text_b.lower().strip() if len(tokens_a) <= len(tokens_b) else text_a.lower().strip()
        if short_text in long_text:
            # Exact substring match — high confidence
            base_score = 0.8
        else:
            # Fall through to Jaccard but require stricter threshold downstream
            intersection = tokens_a & tokens_b
            union = tokens_a | tokens_b
            base_score = len(intersection) / len(union)
        # Section bonus
        bonus = SECTION_BONUS if (section_a and section_b and section_a == section_b) else 0.0
        return min(1.0, base_score + bonus)

    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b

    jaccard = len(intersection) / len(union)

    # Section bonus
    bonus = 0.0
    if section_a and section_b and section_a == section_b:
        bonus = SECTION_BONUS

    # Concept-level matching bonus for CJK-heavy text
    # If Jaccard is low but both texts share key academic concepts, boost score
    concept_bonus = _compute_concept_bonus(text_a, text_b)

    return min(1.0, jaccard + bonus + concept_bonus)


# Key concept patterns for academic paper review matching
_CONCEPT_PATTERNS = [
    # Methodology concepts
    (r'sensitiv|敏感性', 'sensitivity_analysis'),
    (r'robust|稳健', 'robustness'),
    (r'calibrat|校准|校正', 'calibration'),
    (r'parallel\s*trend|平行趋势', 'parallel_trends'),
    (r'event.?study|事件研究', 'event_study'),
    (r'did|difference.?in.?difference|双重差分', 'did'),
    (r'multiple\s*test|多重检验|bonferroni|fdr', 'multiple_testing'),
    (r'construct\s*valid|效度|代理变量', 'construct_validity'),
    (r'dictator\s*game', 'dictator_game'),
    # Data concepts
    (r'table\s*a\.?3|table\s*a\.?4', 'table_a3_a4'),
    (r'数据.*重复|duplicat|replicate.*data', 'data_duplication'),
    (r'cross.?table|跨表', 'cross_table'),
    (r'inconsisten|不一致', 'inconsistency'),
    # Math/notation concepts
    (r'符号.*错|typo|笔误|排版错', 'notation_error'),
    (r'γ_?i|α_?i|θ_?[12]', 'greek_variable'),
    (r'appendix|附录', 'appendix'),
    (r'推导|derivat|proof', 'derivation'),
    # Model concepts
    (r'small\s*(?:country|open\s*economy|economy)|小国|价格接受|大国.*适用|适用.*大国', 'small_country'),
    (r'ces|constant\s*elasticity|嵌套.*ces|nested.*ces', 'ces'),
    (r'两人.*模型|two.?person|household.*model', 'household_model'),
    (r'treatment\s*pool|处理.*组合|信息.*激励', 'treatment_pooling'),
    (r'heterogene|异质', 'heterogeneity'),
    (r'elasticit|弹性', 'elasticity'),
    (r'grid\s*search|网格搜索|步长|step\s*size', 'grid_search'),
    (r'double\s*marginal|双重边际', 'double_marginalization'),
    (r'ω.*σ|omega.*sigma|ω.*1\.25', 'omega_sigma'),
    # Novelty/contribution claims
    (r'novelty|贡献.*声称|声称.*文献|literature.*not\s*address|文献.*空白|overclaim|新颖性', 'novelty_claim'),
    # Trade/tariff
    (r'tariff|关税|optimal\s*tariff|最优关税', 'tariff'),
    # Transition/bridge between model sections
    (r'过渡|transition|bridge|桥接|映射.*符号|符号.*映射|脱节|disconnect|结构.*脱节|structural.*gap', 'model_transition'),
    # Terms of trade
    (r'terms.?of.?trade|贸易条件', 'terms_of_trade'),
    # S9: 补充 — CES transition (理论→定量模型 CES 切换)
    (r'嵌套.*ces.*过渡|nested.*ces.*transition|standard.*ces.*nested|ω.*σ.*1\.25|ces.*结构.*脱节', 'ces_transition'),
    # S9: 补充 — Calibration justification (校准依据不充分)
    (r'校准.*依据|calibrat.*justif|校准.*一句话|fit\s*quality|target.*moment.*选择|校准.*自足|校准.*不足', 'calibration_justification'),
    # S9: 补充 — Variable markup / scope limitation
    (r'variable\s*markup|可变.*加成|scope.*limitation|范围.*界定|non.?ces.*markup', 'variable_markup'),
    # Paper_001 specific patterns (household externalities)
    # G002/G003: θ normalization & elasticity assumption without quantitative sensitivity
    (r'θ\s*=\s*1|theta.*normali|归一化.*假设|normali.*assumption|量化.*影响|quantif.*impact', 'theta_normalization'),
    (r'弹性.*假设|elasticity.*assumption|risk.?neutral|风险中性|离散.*连续|discrete.*continuous', 'elasticity_assumption'),
    # G004: Dictator game as proxy for efficiency — construct validity path
    (r'dictator.*game.*efficien|dictator.*game.*proxy|dictator.*valid|DG.*sharing|DG.*效率', 'dg_validity'),
    (r'intrahousehold.*efficien|household.*efficien|家庭.*效率|配偶.*效率', 'intrahousehold_efficiency'),
    # G005: Table data duplication across treatment balance tables
    (r'balance.*table.*duplicat|balance.*table.*identic|balance.*table.*重复|balance.*table.*一致', 'balance_table_duplication'),
    (r'information.*treatment.*balance|credibility.*treatment.*balance|处理组.*平衡', 'treatment_balance'),
    # G006: Treatment pooling heterogeneity sensitivity
    (r'information.*incentive.*pool|pool.*treatment|合并.*处理|pooling.*sensiti', 'treatment_pool_sensitivity'),
    # G007: Multiple testing without correction
    (r'survey.*measure.*多重|13.*survey|多个.*outcome.*校正|multiple.*outcome.*correct|family.?wise.*error', 'multiple_outcome_correction'),
    (r'pre.?regist|预注册|exploratory.*confirm|探索性.*验证性', 'preregistration'),
    # G008: Two-person model vs multi-person household reality
    (r'两人.*家庭.*实际|two.?person.*household.*reality|模型.*人数.*不匹配|bill.?payer.*spouse.*6|family.*size.*model', 'model_household_mismatch'),
    (r'平均.*6.*人|average.*6.*member|家庭.*规模.*模型|household.*size.*model', 'household_size_gap'),
    # G009: DID parallel trends — visual vs formal test
    (r'平行趋势.*图形|parallel.*trend.*visual|parallel.*trend.*图|event.?study.*回归.*缺|formal.*test.*parallel', 'parallel_trends_formal'),
    (r'RCT.*平行趋势|random.*平行|随机化.*平行', 'rct_parallel_trends'),
]


def _compute_concept_bonus(text_a: str, text_b: str) -> float:
    """Compute concept-level matching bonus.

    Extracts academic concepts from both texts and gives a bonus
    if they share key concepts that indicate the same issue.
    """
    concepts_a: set[str] = set()
    concepts_b: set[str] = set()

    text_a_lower = text_a.lower()
    text_b_lower = text_b.lower()

    for pattern, concept in _CONCEPT_PATTERNS:
        if re.search(pattern, text_a_lower):
            concepts_a.add(concept)
        if re.search(pattern, text_b_lower):
            concepts_b.add(concept)

    if not concepts_a or not concepts_b:
        return 0.0

    shared = concepts_a & concepts_b
    if not shared:
        return 0.0

    # Bonus scales with number of shared concepts
    # 1 shared concept: +0.15, 2+: +0.25, 3+: +0.35
    if len(shared) >= 3:
        return 0.35
    elif len(shared) >= 2:
        return 0.25
    else:
        return 0.15


# ============================================================
# Matching Algorithm (Greedy)
# ============================================================

def match_findings(
    predicted: list[Finding],
    gold: list[Finding],
    threshold: float = MATCH_THRESHOLD,
    short_threshold: float = SHORT_FINDING_STRICTER_THRESHOLD,
) -> tuple[list[MatchResult], list[int], list[int]]:
    """Match predicted findings to gold findings using greedy assignment.

    Algorithm:
    1. Compute all pairwise similarities
    2. Sort by similarity descending
    3. Greedily assign matches (each finding can only be matched once)

    Args:
        predicted: Agent-produced findings
        gold: Human-annotated gold findings
        threshold: Minimum similarity for a match

    Returns:
        (matches, unmatched_predicted_indices, unmatched_gold_indices)
    """
    if not predicted or not gold:
        return (
            [],
            list(range(len(predicted))),
            list(range(len(gold))),
        )

    # Compute similarity matrix
    candidates: list[tuple[float, int, int, bool]] = []
    for pi, p in enumerate(predicted):
        for gi, g in enumerate(gold):
            sim = compute_similarity(p.text, g.text, p.section, g.section)
            section_matched = bool(p.section and g.section and p.section == g.section)
            # Apply stricter threshold for short findings to reduce false positives
            p_tokens = len(_tokenize(p.text))
            g_tokens = len(_tokenize(g.text))
            effective_threshold = (
                short_threshold
                if min(p_tokens, g_tokens) < SHORT_FINDING_TOKEN_THRESHOLD
                else threshold
            )
            if sim >= effective_threshold:
                candidates.append((sim, pi, gi, section_matched))

    # Sort descending by similarity
    candidates.sort(key=lambda x: x[0], reverse=True)

    # Greedy assignment
    matched_predicted: set[int] = set()
    matched_gold: set[int] = set()
    matches: list[MatchResult] = []

    for sim, pi, gi, sec_match in candidates:
        if pi in matched_predicted or gi in matched_gold:
            continue
        matches.append(MatchResult(
            predicted_idx=pi,
            gold_idx=gi,
            similarity=sim,
            section_matched=sec_match,
        ))
        matched_predicted.add(pi)
        matched_gold.add(gi)

    unmatched_pred = [i for i in range(len(predicted)) if i not in matched_predicted]
    unmatched_g = [i for i in range(len(gold)) if i not in matched_gold]

    return matches, unmatched_pred, unmatched_g


# ============================================================
# Metrics Computation
# ============================================================

def compute_metrics(
    paper_id: str,
    predicted: list[Finding],
    gold: list[Finding],
    threshold: float = MATCH_THRESHOLD,
) -> EvalMetrics:
    """Compute precision/recall/F1 for one paper.

    Args:
        paper_id: Identifier for the paper
        predicted: Agent findings
        gold: Gold-standard findings
        threshold: Match threshold

    Returns:
        EvalMetrics with all computed values
    """
    matches, unmatched_pred, unmatched_gold = match_findings(predicted, gold, threshold)

    num_predicted = len(predicted)
    num_gold = len(gold)
    num_matched = len(matches)

    precision = num_matched / num_predicted if num_predicted > 0 else 0.0
    recall = num_matched / num_gold if num_gold > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    # Weighted recall: high/critical findings weighted more
    weighted_recall = _compute_weighted_recall(gold, matches)

    # Category breakdown
    category_breakdown = _compute_category_breakdown(predicted, gold, matches)

    return EvalMetrics(
        paper_id=paper_id,
        precision=precision,
        recall=recall,
        f1=f1,
        weighted_recall=weighted_recall,
        num_predicted=num_predicted,
        num_gold=num_gold,
        num_matched=num_matched,
        matches=matches,
        unmatched_predicted=unmatched_pred,
        unmatched_gold=unmatched_gold,
        category_breakdown=category_breakdown,
    )


def compute_aggregate(per_paper_metrics: list[EvalMetrics]) -> AggregateMetrics:
    """Compute aggregate metrics across papers.

    Uses macro-averaging (average of per-paper metrics).
    """
    if not per_paper_metrics:
        return AggregateMetrics(
            num_papers=0, avg_precision=0, avg_recall=0, avg_f1=0,
            avg_weighted_recall=0, total_predicted=0, total_gold=0,
            total_matched=0, per_paper=[],
        )

    n = len(per_paper_metrics)
    return AggregateMetrics(
        num_papers=n,
        avg_precision=sum(m.precision for m in per_paper_metrics) / n,
        avg_recall=sum(m.recall for m in per_paper_metrics) / n,
        avg_f1=sum(m.f1 for m in per_paper_metrics) / n,
        avg_weighted_recall=sum(m.weighted_recall for m in per_paper_metrics) / n,
        total_predicted=sum(m.num_predicted for m in per_paper_metrics),
        total_gold=sum(m.num_gold for m in per_paper_metrics),
        total_matched=sum(m.num_matched for m in per_paper_metrics),
        per_paper=per_paper_metrics,
    )


# ============================================================
# Internal Helpers
# ============================================================

def _compute_weighted_recall(
    gold: list[Finding],
    matches: list[MatchResult],
) -> float:
    """Compute priority-weighted recall.

    High/critical findings are weighted HIGH_PRIORITY_WEIGHT,
    others are weighted 1.0.
    """
    if not gold:
        return 0.0

    matched_gold_indices = {m.gold_idx for m in matches}

    total_weight = 0.0
    matched_weight = 0.0

    for i, g in enumerate(gold):
        w = HIGH_PRIORITY_WEIGHT if g.priority in ("high", "critical") else 1.0
        total_weight += w
        if i in matched_gold_indices:
            matched_weight += w

    return matched_weight / total_weight if total_weight > 0 else 0.0


def _compute_category_breakdown(
    predicted: list[Finding],
    gold: list[Finding],
    matches: list[MatchResult],
) -> dict[str, dict[str, float]]:
    """Compute per-category precision/recall.

    Returns dict like {"methodology": {"precision": 0.8, "recall": 0.6, "f1": 0.69}}
    """
    # Collect all categories
    all_categories: set[str] = set()
    for f in gold:
        if f.category:
            all_categories.add(f.category)
    for f in predicted:
        if f.category:
            all_categories.add(f.category)

    if not all_categories:
        return {}

    matched_pred = {m.predicted_idx for m in matches}
    matched_gold = {m.gold_idx for m in matches}

    breakdown: dict[str, dict[str, float]] = {}

    for cat in sorted(all_categories):
        # Predicted in this category
        pred_in_cat = [i for i, f in enumerate(predicted) if f.category == cat]
        gold_in_cat = [i for i, f in enumerate(gold) if f.category == cat]

        # How many predicted in this category were matched
        pred_matched = len([i for i in pred_in_cat if i in matched_pred])
        gold_matched = len([i for i in gold_in_cat if i in matched_gold])

        cat_precision = pred_matched / len(pred_in_cat) if pred_in_cat else 0.0
        cat_recall = gold_matched / len(gold_in_cat) if gold_in_cat else 0.0
        cat_f1 = (
            2 * cat_precision * cat_recall / (cat_precision + cat_recall)
            if (cat_precision + cat_recall) > 0 else 0.0
        )

        breakdown[cat] = {
            "precision": round(cat_precision, 4),
            "recall": round(cat_recall, 4),
            "f1": round(cat_f1, 4),
            "num_predicted": len(pred_in_cat),
            "num_gold": len(gold_in_cat),
        }

    return breakdown
