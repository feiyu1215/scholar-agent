"""
模拟测试 consolidation prompt 效果。

用之前评估报告中的 31 条原始 findings 作为输入，
对比当前 prompt 和改进后 prompt 的合并效果。
"""

from __future__ import annotations

import asyncio
import json
import sys
import os

# 添加项目根目录到 path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from llm.client import LLMClient
from core.consolidation import (
    consolidate_findings,
    _SYSTEM_PROMPT,
    _USER_PROMPT_TEMPLATE,
    _format_findings_for_prompt,
    _parse_json_response,
    _validate_consolidated_output,
    ConsolidationResult,
)


# ============================================================
# 改进后的 Prompt（V2）
# ============================================================

SYSTEM_PROMPT_V2 = """\
你是一位资深期刊编辑（Handling Editor）。你收到了多位审稿人对同一篇论文的意见。
你的任务是整合这些意见：合并说同一个问题的条目，删除完全重复的，保留每个独特问题的最佳表述。

核心规则：
1. 只合并确实在说【完全相同的具体问题】的 findings（即使措辞不同）
2. 以下情况【绝对不要合并】：
   - 两个 findings 讨论同一 section 但指出不同问题
   - 两个 findings 讨论同一主题（如"假设简化"）但针对不同具体假设
   - 一个是方法论问题，另一个是数据问题，即使涉及同一 section
   - 一个是"缺少 X 分析"，另一个是"Y 假设不合理"，即使 X 和 Y 相关
3. 合并时保留最详尽的表述，补充其他条目中独特的证据
4. 保留原始的 priority（取组内最高 priority）
5. 保留原始的 section 字段（如果组内 section 不同，取最具体的那个）
6. 按 priority 排序输出：critical > high > medium > low

判断是否合并的黄金标准：如果一位审稿人在审稿报告中看到这两条意见，
他/她会认为"这是同一个 comment 被写了两遍"还是"这是两个不同的 comments"？
只有前者才应该合并。

额外指令：
- 过滤掉纯确认性发现（如"数据一致性确认""符号定义一致"等不构成批评的条目）
- 这些确认性条目不应出现在最终输出中

输出格式：严格 JSON 数组，每个元素包含以下字段：
- "finding": 合并后的描述文本（string）
- "priority": "critical" | "high" | "medium" | "low"
- "evidence": 合并后的证据（string）
- "section": 对应的论文章节（string）
- "merged_from": 原始编号列表（array of integers，1-based）

只输出 JSON 数组，不要输出任何其他文字。"""


async def run_consolidation_with_prompt(
    findings: list[dict],
    system_prompt: str,
    paper_context: str,
    client: LLMClient,
    model: str,
    label: str,
) -> list[dict] | None:
    """用指定 prompt 跑一次 consolidation。"""
    formatted = _format_findings_for_prompt(findings)
    user_prompt = _USER_PROMPT_TEMPLATE.format(
        paper_context=paper_context,
        n=len(findings),
        formatted_findings=formatted,
    )

    print(f"\n{'='*60}")
    print(f"  Running: {label}")
    print(f"  Input: {len(findings)} findings")
    print(f"{'='*60}")

    try:
        response = await client.chat(
            system=system_prompt,
            user=user_prompt,
            temperature=0.1,
            max_tokens=6000,
            model=model,
        )

        parsed = _parse_json_response(response)
        if parsed and _validate_consolidated_output(parsed, len(findings)):
            print(f"  Output: {len(parsed)} findings")
            print(f"  Reduction: {len(findings)} → {len(parsed)} ({100*(1-len(parsed)/len(findings)):.0f}% reduction)")
            return parsed
        else:
            print(f"  ERROR: Failed to parse or validate output")
            if parsed:
                print(f"  Parsed {len(parsed)} items but validation failed")
            return None
    except Exception as e:
        print(f"  ERROR: {type(e).__name__}: {e}")
        return None


def analyze_results(
    findings: list[dict],
    gold_findings: list[dict],
    label: str,
):
    """分析合并结果的质量。"""
    print(f"\n--- Analysis: {label} ---")
    print(f"  Total findings: {len(findings)}")

    # 统计 priority 分布
    priorities = {}
    for f in findings:
        p = f.get("priority", "unknown")
        priorities[p] = priorities.get(p, 0) + 1
    print(f"  Priority distribution: {priorities}")

    # 统计 merged_from 分布
    merge_sizes = [len(f.get("merged_from", [1])) for f in findings]
    print(f"  Merge group sizes: min={min(merge_sizes)}, max={max(merge_sizes)}, avg={sum(merge_sizes)/len(merge_sizes):.1f}")

    # 检查是否有确认性发现
    confirmatory_keywords = ["一致", "consistent", "confirmed", "no contradiction", "无矛盾"]
    confirmatory = []
    for i, f in enumerate(findings):
        text = f.get("finding", "").lower()
        if any(kw.lower() in text for kw in confirmatory_keywords):
            confirmatory.append(i)
    if confirmatory:
        print(f"  ⚠️  Confirmatory findings still present: indices {confirmatory}")
    else:
        print(f"  ✅ No confirmatory findings detected")

    # 打印每条 finding 的摘要
    print(f"\n  Findings summary:")
    for i, f in enumerate(findings):
        text = f.get("finding", "")[:80]
        merged = f.get("merged_from", [])
        print(f"    [{i}] (merged_from={merged}, priority={f.get('priority','?')}) {text}...")


async def main():
    # 加载之前的 31 条原始 findings
    report_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "reports/recall_verification_20260529_030719.json"
    )
    with open(report_path) as f:
        report = json.load(f)

    raw_findings = report["per_paper"][0]["raw_findings"]
    print(f"Loaded {len(raw_findings)} raw findings from previous run")

    # 加载 gold standard
    gold_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "gold_standard/gold_paper_001.json"
    )
    with open(gold_path) as f:
        gold = json.load(f)
    gold_findings = gold["gold_findings"]

    # 构建 paper context
    paper_context = "论文：Environmental Externalities and Free-riding in the Household (Jack, Jayachandran & Rao 2018)\n"
    paper_context += "章节：2 model of water use within the household, 2.1 optimal water conservation, "
    paper_context += "2.2 individual best response, 2.3 effect of a price change, 2.4 discussion of assumptions, "
    paper_context += "3.1 water use, 3.5 sample construction and summary statistics, "
    paper_context += "4.2 estimation strategy, 4.3 average treatment effects, "
    paper_context += "4.4 intrahousehold heterogeneity, 4.5 robustness checks, "
    paper_context += "5 implications for optimal pricing, Calibration, 6 conclusion"

    # 初始化 LLM client
    client = LLMClient()
    model = "gpt-4.1-mini"

    # 跑当前 prompt (V1)
    v1_results = await run_consolidation_with_prompt(
        findings=raw_findings,
        system_prompt=_SYSTEM_PROMPT,
        paper_context=paper_context,
        client=client,
        model=model,
        label="V1 (Current Prompt)",
    )

    # 跑改进 prompt (V2)
    v2_results = await run_consolidation_with_prompt(
        findings=raw_findings,
        system_prompt=SYSTEM_PROMPT_V2,
        paper_context=paper_context,
        client=client,
        model=model,
        label="V2 (Improved Prompt - anti-over-merge + filter confirmatory)",
    )

    # 分析结果
    if v1_results:
        analyze_results(v1_results, gold_findings, "V1")
    if v2_results:
        analyze_results(v2_results, gold_findings, "V2")

    # 对比
    if v1_results and v2_results:
        print(f"\n{'='*60}")
        print(f"  COMPARISON")
        print(f"{'='*60}")
        print(f"  V1: {len(v1_results)} findings")
        print(f"  V2: {len(v2_results)} findings")
        print(f"  Difference: V2 has {len(v2_results) - len(v1_results):+d} findings vs V1")
        print(f"\n  V2 should have MORE findings than V1 (less aggressive merging)")
        print(f"  V2 should have FEWER confirmatory findings (filtered out)")


if __name__ == "__main__":
    asyncio.run(main())
