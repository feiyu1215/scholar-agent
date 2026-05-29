"""
evaluation/llm_judge.py — LLM-as-Judge 语义匹配模块。

替代 Jaccard token overlap 的纯文本匹配，使用 LLM 判断两条 finding
是否在说同一个问题（跨语言、跨表述风格的语义等价判断）。

设计:
    1. 批量发送 predicted × gold 的 candidate pairs
    2. LLM 对每对做二分判断（match / no_match）
    3. 返回 similarity matrix，交由 greedy matching 分配
    4. Fallback: LLM 不可用时退化为原有 Jaccard 匹配

成本估算:
    paper_001: 9×9=81 pairs → 分组后约 2-3 次 LLM 调用
    使用 gpt-4.1-mini 每次 ~3000 tokens → < $0.01/paper
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass
from typing import Any

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

logger = logging.getLogger(__name__)

# ============================================================
# Configuration
# ============================================================

JUDGE_MODEL = os.environ.get("EVAL_JUDGE_MODEL", "gpt-4.1-mini")
JUDGE_TEMPERATURE = 0.0
JUDGE_MAX_TOKENS = 4000
MATCH_CONFIDENCE_THRESHOLD = 0.6  # LLM 报告的 confidence 低于此值视为不匹配
BATCH_SIZE = 25  # 每次 LLM 调用判断的最大 pair 数


# ============================================================
# Prompt
# ============================================================

JUDGE_SYSTEM_PROMPT = """\
You are an expert academic reviewer evaluating whether two review findings refer to the SAME underlying issue in a paper.

Two findings MATCH if they identify the same core problem, even if:
- One is in Chinese, the other in English
- They use different wording or level of detail
- One is more specific than the other (but the core issue overlaps)

Two findings DO NOT MATCH if:
- They address fundamentally different issues (e.g., one about sensitivity analysis, another about sample size)
- They apply to different parts of the paper with no conceptual overlap
- One is about methodology, another about writing style (unless clearly the same problem)

For each pair, output:
- "match": true/false
- "confidence": 0.0-1.0 (how confident you are)
- "reason": brief explanation (1 sentence)
"""

JUDGE_USER_TEMPLATE = """\
Paper: "{paper_title}"

Below are pairs of review findings. For each pair, judge if they refer to the same underlying issue.

{pairs_text}

Respond in strict JSON array format:
[
  {{"pair_id": 0, "match": true/false, "confidence": 0.0-1.0, "reason": "..."}},
  ...
]
"""


# ============================================================
# Data Structures
# ============================================================

@dataclass
class JudgeResult:
    """Result of LLM judge for one pair."""
    predicted_idx: int
    gold_idx: int
    match: bool
    confidence: float
    reason: str = ""


# ============================================================
# Core Logic
# ============================================================

async def judge_matches(
    predicted_texts: list[str],
    gold_texts: list[str],
    paper_title: str = "",
    llm_client: Any = None,
) -> list[JudgeResult]:
    """使用 LLM 判断 predicted 和 gold findings 之间的语义匹配。

    Args:
        predicted_texts: Agent 产出的 findings 文本列表
        gold_texts: Gold standard findings 文本列表
        paper_title: 论文标题（帮助 LLM 理解上下文）
        llm_client: LLMClient 实例（若为 None 则内部创建）

    Returns:
        所有被判定为 match 的 JudgeResult 列表
    """
    if not predicted_texts or not gold_texts:
        return []

    # 创建 LLM client（如果未提供）
    if llm_client is None:
        from llm.client import LLMClient
        llm_client = LLMClient(model=JUDGE_MODEL)

    # 生成所有候选 pair（全量笛卡尔积，小规模时可接受）
    all_pairs = []
    for pi, p_text in enumerate(predicted_texts):
        for gi, g_text in enumerate(gold_texts):
            all_pairs.append((pi, gi, p_text, g_text))

    logger.info(
        "[LLM-Judge] %d predicted × %d gold = %d pairs to evaluate",
        len(predicted_texts), len(gold_texts), len(all_pairs),
    )

    # 分批调用 LLM
    all_results: list[JudgeResult] = []
    for batch_start in range(0, len(all_pairs), BATCH_SIZE):
        batch = all_pairs[batch_start:batch_start + BATCH_SIZE]
        batch_results = await _judge_batch(batch, paper_title, llm_client)
        all_results.extend(batch_results)

    # 过滤出 match 的结果
    matched = [r for r in all_results if r.match and r.confidence >= MATCH_CONFIDENCE_THRESHOLD]
    logger.info(
        "[LLM-Judge] Results: %d total pairs → %d matches (confidence >= %.2f)",
        len(all_results), len(matched), MATCH_CONFIDENCE_THRESHOLD,
    )

    return matched


async def _judge_batch(
    pairs: list[tuple[int, int, str, str]],
    paper_title: str,
    llm_client: Any,
) -> list[JudgeResult]:
    """对一批 pairs 调用 LLM judge。"""
    # 构建 pairs 文本
    pairs_lines = []
    for i, (pi, gi, p_text, g_text) in enumerate(pairs):
        pairs_lines.append(
            f"--- Pair {i} (predicted[{pi}] vs gold[{gi}]) ---\n"
            f"PREDICTED: {p_text[:400]}\n"
            f"GOLD: {g_text[:400]}\n"
        )

    pairs_text = "\n".join(pairs_lines)

    user_prompt = JUDGE_USER_TEMPLATE.format(
        paper_title=paper_title or "Unknown",
        pairs_text=pairs_text,
    )

    try:
        raw_response = await llm_client.chat(
            system=JUDGE_SYSTEM_PROMPT,
            user=user_prompt,
            temperature=JUDGE_TEMPERATURE,
            max_tokens=JUDGE_MAX_TOKENS,
            model=JUDGE_MODEL,
        )
    except Exception as e:
        logger.warning("[LLM-Judge] LLM call failed: %s. Returning empty.", e)
        return []

    # 解析 JSON 响应
    return _parse_judge_response(raw_response, pairs)


def _parse_judge_response(
    raw: str,
    pairs: list[tuple[int, int, str, str]],
) -> list[JudgeResult]:
    """解析 LLM judge 的 JSON 输出。"""
    results: list[JudgeResult] = []

    # 提取 JSON array
    parsed = _extract_json_array(raw)
    if parsed is None:
        logger.warning("[LLM-Judge] Failed to parse response. Raw[:300]: %s", raw[:300])
        return results

    for item in parsed:
        if not isinstance(item, dict):
            continue

        pair_id = item.get("pair_id", -1)
        if not (0 <= pair_id < len(pairs)):
            continue

        pi, gi, _, _ = pairs[pair_id]
        match = bool(item.get("match", False))
        confidence = float(item.get("confidence", 0.0))
        reason = str(item.get("reason", ""))

        results.append(JudgeResult(
            predicted_idx=pi,
            gold_idx=gi,
            match=match,
            confidence=confidence,
            reason=reason,
        ))

    return results


def _extract_json_array(text: str) -> list | None:
    """从 LLM 输出中提取 JSON array。多种策略。"""
    import re

    # Strategy 1: 直接 parse 整个 text
    text_stripped = text.strip()
    if text_stripped.startswith("["):
        try:
            return json.loads(text_stripped)
        except json.JSONDecodeError:
            pass

    # Strategy 2: 找 ```json ... ``` 代码块
    code_block_match = re.search(r"```(?:json)?\s*(\[[\s\S]*?\])\s*```", text)
    if code_block_match:
        try:
            return json.loads(code_block_match.group(1))
        except json.JSONDecodeError:
            pass

    # Strategy 3: 找第一个 [ 到最后一个 ]
    first_bracket = text.find("[")
    last_bracket = text.rfind("]")
    if first_bracket >= 0 and last_bracket > first_bracket:
        try:
            return json.loads(text[first_bracket:last_bracket + 1])
        except json.JSONDecodeError:
            pass

    return None


# ============================================================
# Greedy Matching (same algorithm as metrics.py but using LLM scores)
# ============================================================

def greedy_match_from_judge(
    judge_results: list[JudgeResult],
    num_predicted: int,
    num_gold: int,
) -> tuple[list[JudgeResult], list[int], list[int]]:
    """从 LLM judge 结果中做贪婪匹配（每个 finding 最多匹配一次）。

    与 metrics.py 的 match_findings 逻辑相同，但输入是 LLM 判断结果。

    Returns:
        (matched_pairs, unmatched_predicted_indices, unmatched_gold_indices)
    """
    # 按 confidence 降序排列
    sorted_results = sorted(judge_results, key=lambda r: r.confidence, reverse=True)

    matched_predicted: set[int] = set()
    matched_gold: set[int] = set()
    final_matches: list[JudgeResult] = []

    for r in sorted_results:
        if r.predicted_idx in matched_predicted or r.gold_idx in matched_gold:
            continue
        final_matches.append(r)
        matched_predicted.add(r.predicted_idx)
        matched_gold.add(r.gold_idx)

    unmatched_pred = [i for i in range(num_predicted) if i not in matched_predicted]
    unmatched_gold = [i for i in range(num_gold) if i not in matched_gold]

    return final_matches, unmatched_pred, unmatched_gold


# ============================================================
# High-Level API: compute_metrics_with_llm_judge
# ============================================================

async def compute_metrics_llm(
    paper_id: str,
    predicted_findings: list[dict],
    gold_findings: list[dict],
    paper_title: str = "",
    llm_client: Any = None,
) -> dict:
    """使用 LLM-as-judge 计算 P/R/F1。

    Args:
        paper_id: 论文 ID
        predicted_findings: Agent 输出的 findings (list of dicts with "finding"/"text" key)
        gold_findings: Gold standard (list of dicts with "text" key)
        paper_title: 论文标题
        llm_client: 可选的 LLMClient 实例

    Returns:
        Dict with precision, recall, f1, matches, details
    """
    # 提取文本
    pred_texts = [
        f.get("finding", f.get("text", "")) for f in predicted_findings
    ]
    gold_texts = [
        f.get("text", f.get("description", "")) for f in gold_findings
    ]

    # LLM judge
    judge_results = await judge_matches(
        predicted_texts=pred_texts,
        gold_texts=gold_texts,
        paper_title=paper_title,
        llm_client=llm_client,
    )

    # Greedy matching
    matches, unmatched_pred, unmatched_gold = greedy_match_from_judge(
        judge_results,
        num_predicted=len(pred_texts),
        num_gold=len(gold_texts),
    )

    # Compute metrics
    num_predicted = len(pred_texts)
    num_gold = len(gold_texts)
    num_matched = len(matches)

    precision = num_matched / num_predicted if num_predicted > 0 else 0.0
    recall = num_matched / num_gold if num_gold > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    # Weighted recall (high/critical 2x)
    HIGH_WEIGHT = 2.0
    total_weight = 0.0
    matched_weight = 0.0
    matched_gold_set = {m.gold_idx for m in matches}
    for i, gf in enumerate(gold_findings):
        priority = gf.get("priority", "medium")
        w = HIGH_WEIGHT if priority in ("high", "critical") else 1.0
        total_weight += w
        if i in matched_gold_set:
            matched_weight += w
    weighted_recall = matched_weight / total_weight if total_weight > 0 else 0.0

    return {
        "paper_id": paper_id,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "weighted_recall": round(weighted_recall, 4),
        "num_predicted": num_predicted,
        "num_gold": num_gold,
        "num_matched": num_matched,
        "matches": [
            {
                "predicted_idx": m.predicted_idx,
                "gold_idx": m.gold_idx,
                "confidence": m.confidence,
                "reason": m.reason,
                "predicted_text": pred_texts[m.predicted_idx][:150],
                "gold_text": gold_texts[m.gold_idx][:150],
            }
            for m in matches
        ],
        "unmatched_predicted": [
            {"idx": i, "text": pred_texts[i][:150]} for i in unmatched_pred
        ],
        "unmatched_gold": [
            {"idx": i, "text": gold_texts[i][:150]} for i in unmatched_gold
        ],
    }
