"""
core/consolidation.py — Findings 语义整合（Consolidation Pass）

设计原则:
    Agent 认知循环结束后，对产出的 findings 做 LLM-based 语义去重合并。
    解决的问题：子 agent 并行独立运行时，同一问题被不同措辞重复记录 2-4 遍，
    导致 Precision 极低（paper_001: 31 predicted, 7 matched, P=0.226）。

    策略：
    1. findings ≤ min_findings_to_trigger 时跳过（数量少无需去重）
    2. 格式化 findings 为编号列表，调用 MEDIUM tier LLM 识别语义重复组
    3. 每组合并为一条（保留最详尽表述 + 合并证据 + 取最高 priority）
    4. LLM 失败时 graceful fallback（返回原始 findings，永不 crash）

位置：LoopDone 后、_handle_result 前执行，不侵入认知循环。
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llm.client import LLMClient


# ============================================================
# Data Types
# ============================================================

@dataclass
class ConsolidationResult:
    """Consolidation 的输出。"""

    findings: list[dict]                    # 合并后的 findings
    merge_map: dict[int, list[int]] = field(default_factory=dict)  # {new_idx: [original_indices]}
    raw_count: int = 0                      # 原始数量
    consolidated_count: int = 0             # 合并后数量
    hints_adopted: int = 0                  # 被 LLM 采纳的 heuristic hints 数量


# ============================================================
# Prompt Templates
# ============================================================

_SYSTEM_PROMPT = """\
你是一位资深期刊编辑（Handling Editor）。你收到了多位审稿人对同一篇论文的意见。
你的任务是整合这些意见：合并说同一个问题的条目，删除完全重复的，保留每个独特问题的最佳表述。

核心规则：
1. 【必须合并】的情况（极严格标准）：两条 findings 指出完全相同的具体问题，只是措辞/详略不同。
   例如："模型假设水价为线性"和"价格机制假设为线性单一价格"是同一个问题，必须合并。
   注意："完全相同的具体问题"意味着如果一位审稿人看到这两条，会说"这是同一句话写了两遍"。
2. 【绝对不要合并】的情况：
   - 两条讨论同一 section 但指出不同问题（即使主题相关）
   - 两条讨论同一主题（如"假设简化"）但针对不同具体假设（如一个是"线性效用"，另一个是"两人家庭"）
   - 一条是方法论问题，另一条是数据问题
   - 两条虽然都涉及"敏感性分析缺失"，但针对不同参数或不同模型部分
   - 两条虽然都涉及"外部有效性"，但一个是样本选择问题，另一个是模型假设问题
   - 两条虽然都涉及同一个模型假设，但一个质疑假设本身，另一个质疑假设的实证验证方式
3. 合并时保留最详尽的表述，补充其他条目中独特的证据
4. 保留原始的 priority（取组内最高 priority）
5. 保留原始的 section 字段（如果组内 section 不同，取最具体的那个）
6. 按 priority 排序输出：critical > high > medium > low
7. 每条输出必须包含 merged_from 字段（即使只有一条原始来源，也要写 [原始编号]）

判断标准：一位审稿人看到这两条意见，会认为"这是同一个 comment 被写了两遍"还是"这是两个不同的 comments"？
只有当答案是"完全是同一句话的不同写法"时才合并。如果有任何犹豫，保持独立。

宁可多保留几条相似但不完全相同的 findings，也不要过度合并导致信息丢失。

额外指令：
- 过滤掉纯确认性发现（如"数据一致性确认""符号定义一致""参数一致"等不构成批评的条目）
- 这些确认性条目不应出现在最终输出中

## Heuristic Hints 审核（如果有）

如果输入中包含"自动检测提示（Heuristic Hints）"section，这些是规则引擎自动扫描的结果，可能有误报。你需要：
1. 逐条判断每个 Hint 是否为真正的问题（结合论文概要和已有 findings 判断）
2. 如果确实是问题且不与已有 findings 重复：纳入输出，标记 "source": "heuristic_adopted"
3. 如果是误报（规则引擎误判、与论文实际内容不符）：直接丢弃，不纳入输出
4. 如果与已有 findings 重复：合并到对应的 finding 中（和正常合并规则一致）
5. 被采纳的 Hint 在 merged_from 中用 "H1", "H2" 等标记（区别于数字编号的原始 findings）

特别注意：理论/校准类论文中，多张表格共享相同的弹性参数、校准值是学术规范中完全合理的做法（如不同 specification 下测试相同参数值的稳健性），不应判为"制表错误"或"复制粘贴重复"。只有当两张表声称报告不同样本/处理组的统计量却完全一致时，才是真正的数据错误。

输出格式：严格 JSON 数组，每个元素包含以下字段：
- "finding": 合并后的描述文本（string）
- "priority": "critical" | "high" | "medium" | "low"
- "evidence": 合并后的证据（string）
- "section": 对应的论文章节（string）
- "merged_from": 原始编号列表（array of integers/strings，1-based；Hint 用 "H1" 等标记）
- "source": 可选，仅对从 Hint 采纳的条目标记 "heuristic_adopted"

只输出 JSON 数组，不要输出任何其他文字。"""

_USER_PROMPT_TEMPLATE = """\
论文概要：
{paper_context}

原始审稿意见（共 {n} 条）：
{formatted_findings}
"""


# ============================================================
# Priority Ordering
# ============================================================

_PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _priority_rank(p: str) -> int:
    """返回 priority 的排序权重（越小越高优先级）。"""
    return _PRIORITY_ORDER.get(p.lower().strip(), 3)


def _highest_priority(priorities: list[str]) -> str:
    """取一组 priority 中最高的。"""
    if not priorities:
        return "medium"
    return min(priorities, key=_priority_rank)


# ============================================================
# Core Function
# ============================================================

async def consolidate_findings(
    raw_findings: list[dict],
    paper_context: str,
    client: "LLMClient",
    model: str | None = None,
    min_findings_to_trigger: int = 6,
    deep_verify_hints: list[dict] | None = None,
    session_model_mgr=None,
) -> ConsolidationResult:
    """
    对 raw findings 做 LLM-based 语义整合。

    同时审核 deep_verify_hints（heuristic 规则引擎的检测结果），
    由 LLM 判断哪些 hints 是真正的问题后才纳入最终 findings。

    Args:
        raw_findings: agent 产出的原始 findings 列表
        paper_context: 论文摘要 + section 标题（给 LLM 上下文）
        client: LLM 客户端
        model: 模型覆盖（默认用 router 的 MEDIUM tier）
        min_findings_to_trigger: 少于此数量跳过 consolidation（但有 hints 时仍执行）
        deep_verify_hints: heuristic 规则引擎的检测结果，待 LLM 审核
        session_model_mgr: Optional SessionModelManager for Phase 4 model assignment.
            When provided, uses providers.json config for consolidation model.

    Returns:
        ConsolidationResult — 合并后的 findings + 追溯映射
    """
    raw_count = len(raw_findings)
    hints = deep_verify_hints or []

    # Guard: 数量不足且无 hints，跳过
    if raw_count < min_findings_to_trigger and not hints:
        return ConsolidationResult(
            findings=raw_findings,
            merge_map={i: [i] for i in range(raw_count)},
            raw_count=raw_count,
            consolidated_count=raw_count,
        )

    # 最小保留比例 guard: 防止 LLM 过度合并
    # 合并后数量不应低于原始数量的 60%（除非原始数量很少）
    min_retain_ratio = 0.6
    min_retain_count = max(3, int(raw_count * min_retain_ratio))

    # 格式化 findings 为编号列表
    formatted = _format_findings_for_prompt(raw_findings)

    # 格式化 heuristic hints（如果有）
    hints_section = _format_hints_section(hints) if hints else ""

    # 构建 user prompt
    user_prompt = _USER_PROMPT_TEMPLATE.format(
        paper_context=paper_context,
        n=raw_count,
        formatted_findings=formatted,
    )

    # 追加 hints section（如果有）
    if hints_section:
        user_prompt += hints_section

    # 输出格式指令放在最后（LLM 对末尾指令遵守度最高）
    user_prompt += "\n请输出整合后的 JSON 数组。"

    # 确定模型 — Phase 4: 优先从 session_model_mgr 获取
    if model is None:
        if session_model_mgr is not None:
            model = session_model_mgr.resolve_model_for_role("consolidation")
        else:
            from llm.router import get_model_for_task
            model = get_model_for_task("consolidate")

    # 调用 LLM（两次机会）
    merged_findings = await _call_llm_with_retry(
        client=client,
        model=model,
        system=_SYSTEM_PROMPT,
        user=user_prompt,
        raw_findings=raw_findings,
        hints_count=len(hints),
    )

    # 如果 LLM 返回 None（两次都失败），graceful fallback
    if merged_findings is None:
        print("[Consolidation] LLM 调用失败，返回原始 findings", file=sys.stderr)
        return ConsolidationResult(
            findings=raw_findings,
            merge_map={i: [i] for i in range(raw_count)},
            raw_count=raw_count,
            consolidated_count=raw_count,
        )

    # 过度合并 guard: 如果 LLM 合并得太激进，拒绝结果并返回原始
    # 排除纯 heuristic 来源的 findings 后计算保留比例
    non_hint_findings = [
        f for f in merged_findings
        if not (f.get("source") or "").startswith("heuristic")
    ]
    if len(non_hint_findings) < min_retain_count:
        print(
            f"[Consolidation] 过度合并警告: {raw_count} → {len(non_hint_findings)} "
            f"(低于最小保留 {min_retain_count})，返回原始 findings",
            file=sys.stderr,
        )
        return ConsolidationResult(
            findings=raw_findings,
            merge_map={i: [i] for i in range(raw_count)},
            raw_count=raw_count,
            consolidated_count=raw_count,
        )

    # 构建 merge_map 并统计 hints 采纳数
    merge_map = {}
    hints_adopted = 0
    for idx, f in enumerate(merged_findings):
        merged_from = f.get("merged_from", [idx + 1])
        # 转为 0-based index（跳过 "H1" 等 hint 标记）
        merge_map[idx] = [i - 1 for i in merged_from if isinstance(i, int)]
        # 统计被采纳的 hints：
        # 1. merged_from 中包含 "H*" 标记（大小写容忍）
        # 2. 或 source 字段包含 "heuristic"（兼容 LLM 可能的变体输出）
        has_hint_ref = any(
            isinstance(ref, str) and ref.upper().startswith("H")
            and any(c.isdigit() for c in ref)  # 确保是 "H1" 而非普通单词
            for ref in merged_from
        )
        source_val = (f.get("source") or "").lower()
        if has_hint_ref or "heuristic" in source_val:
            hints_adopted += 1

    # 清理输出（移除 merged_from 字段，保持与原始 findings 格式一致）
    cleaned_findings = []
    for f in merged_findings:
        cleaned = {
            "finding": f.get("finding", ""),
            "priority": f.get("priority", "medium"),
            "evidence": f.get("evidence", ""),
            "section": f.get("section", ""),
        }
        # 保留 merged_from 作为 metadata（方便追溯）
        cleaned["_merged_from"] = f.get("merged_from", [])
        # 保留 source 标记（区分 heuristic 来源）
        if f.get("source"):
            cleaned["source"] = f["source"]
        cleaned_findings.append(cleaned)

    return ConsolidationResult(
        findings=cleaned_findings,
        merge_map=merge_map,
        raw_count=raw_count,
        consolidated_count=len(cleaned_findings),
        hints_adopted=hints_adopted,
    )


# ============================================================
# Internal Helpers
# ============================================================

def _format_findings_for_prompt(findings: list[dict]) -> str:
    """将 findings 列表格式化为编号文本，供 LLM 阅读。"""
    lines = []
    for i, f in enumerate(findings, 1):
        finding_text = f.get("finding", f.get("description", "(无描述)"))
        priority = f.get("priority", "medium")
        section = f.get("section", "unknown")
        evidence = f.get("evidence", "")

        line = f"[{i}] (priority={priority}, section={section})\n"
        line += f"    问题: {finding_text}\n"
        if evidence:
            line += f"    证据: {evidence}\n"
        lines.append(line)

    return "\n".join(lines)


def _format_hints_section(hints: list[dict]) -> str:
    """将 heuristic hints 格式化为待审核 section，供 LLM 判断是否采纳。"""
    if not hints:
        return ""

    lines = [
        "",
        "---",
        "自动检测提示（Heuristic Hints，待你审核）：",
        "以下是规则引擎自动扫描的结果。请逐条判断是否为真正的问题。",
        "如果确实是问题，纳入输出并标记 source=\"heuristic_adopted\"；如果是误报，直接丢弃。",
        "",
    ]
    for i, h in enumerate(hints, 1):
        finding_text = h.get("finding", h.get("description", "(无描述)"))
        detector = h.get("source", "heuristic")  # 输入端用 detector 语义，避免与输出端 source 混淆
        location = h.get("location", "unknown")
        confidence = h.get("confidence", "?")
        severity = h.get("severity", "medium")

        line = f"[H{i}] (detector={detector}, location={location}, confidence={confidence}, severity={severity})\n"
        line += f"    检测结果: {finding_text}\n"
        lines.append(line)

    return "\n".join(lines)


async def _call_llm_with_retry(
    client: "LLMClient",
    model: str,
    system: str,
    user: str,
    raw_findings: list[dict],
    hints_count: int = 0,
    max_retries: int = 2,
) -> list[dict] | None:
    """
    调用 LLM 并解析 JSON 输出。失败时重试一次（降低 temperature）。

    Args:
        hints_count: heuristic hints 数量，用于验证输出上限。

    Returns:
        解析成功的 findings 列表，或 None（两次都失败）。
    """
    temperatures = [0.0, 0.0]  # 确定性输出，减少随机性导致的合并行为不一致

    for attempt in range(max_retries):
        try:
            response = await client.chat(
                system=system,
                user=user,
                temperature=temperatures[attempt],
                max_tokens=4000,
                model=model,
            )

            # 解析 JSON
            parsed = _parse_json_response(response)
            if parsed is not None:
                # 验证基本结构
                if _validate_consolidated_output(parsed, len(raw_findings), hints_count=hints_count):
                    return parsed
                else:
                    print(
                        f"[Consolidation] Attempt {attempt + 1}: 输出验证失败，重试",
                        file=sys.stderr,
                    )
            else:
                print(
                    f"[Consolidation] Attempt {attempt + 1}: JSON 解析失败，重试",
                    file=sys.stderr,
                )

        except Exception as e:
            print(
                f"[Consolidation] Attempt {attempt + 1}: 异常 {type(e).__name__}: {e}",
                file=sys.stderr,
            )

    return None


def _parse_json_response(response: str) -> list[dict] | None:
    """从 LLM 响应中提取 JSON 数组。处理 markdown code block 包裹的情况。"""
    text = response.strip()

    # 去除 markdown code block
    if text.startswith("```"):
        # 找到第一个换行后的内容
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1:]
        # 去除结尾的 ```
        if text.endswith("```"):
            text = text[:-3].strip()

    # 尝试直接解析
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        pass

    # 尝试找到第一个 [ 和最后一个 ] 之间的内容
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            result = json.loads(text[start:end + 1])
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

    return None


def _validate_consolidated_output(
    findings: list[dict],
    original_count: int,
    hints_count: int = 0,
) -> bool:
    """
    验证 LLM 输出的基本合理性。

    规则：
    1. 输出不能为空
    2. 输出数量不能超过 original_count + hints_count（合并只减少，新增只来自 hints）
    3. 每条必须有 finding 字段
    4. merged_from 中的整数索引不能超出原始范围
    5. merged_from 中的字符串索引（如 "H1"）是 hint 引用，不做范围检查
    """
    if not findings:
        return False

    # 上限 = 原始 findings + 可能被采纳的 hints（+2 容差防止边界误拒）
    max_allowed = original_count + hints_count + 2
    if len(findings) > max_allowed:
        return False

    for f in findings:
        if not isinstance(f, dict):
            return False
        if "finding" not in f:
            return False
        # 检查 merged_from 索引范围（仅检查整数索引，字符串如 "H1" 是 hint 引用）
        merged_from = f.get("merged_from", [])
        if merged_from:
            for idx in merged_from:
                if isinstance(idx, int) and (idx < 1 or idx > original_count):
                    return False
                # 字符串引用（如 "H1", "H2"）是合法的 hint 标记

    return True
