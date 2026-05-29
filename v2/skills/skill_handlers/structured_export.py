"""
skill_handlers/structured_export.py — 结构化审稿报告导出

V4 Phase D2: 第一个操作型 Skill handler 实现。

功能:
    将 Agent 工作状态（findings, edits, sections_read 等）导出为
    结构化报告（Markdown 或 JSON 格式）。

Handler 签名: (args: dict, state: Any) -> str
    - args: LLM tool call 传入的参数
    - state: WorkspaceState 对象
    - return: 格式化报告字符串

参数 schema:
    - format: "markdown" | "json" (default: "markdown")
    - group_by: "priority" | "section" | "status" (default: "priority")
    - include_stats: bool (default: true) — 是否包含审稿统计信息
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any


# ==============================================================
# Priority 排序定义（高→低）
# ==============================================================

_PRIORITY_ORDER = {
    "critical": 0,
    "major": 1,
    "moderate": 2,
    "minor": 3,
    "suggestion": 4,
    "cosmetic": 5,
}

_STATUS_ORDER = {
    "open": 0,
    "in_progress": 1,
    "addressed": 2,
    "dismissed": 3,
}


# ==============================================================
# Handler 入口
# ==============================================================

def handle_export_review(args: dict, state: Any) -> str:
    """导出结构化审稿报告。

    Args:
        args: {
            "format": "markdown" | "json",
            "group_by": "priority" | "section" | "status",
            "include_stats": bool
        }
        state: WorkspaceState 实例

    Returns:
        格式化的审稿报告字符串。
    """
    # 1. 解析参数（带默认值）
    output_format = args.get("format", "markdown")
    group_by = args.get("group_by", "priority")
    include_stats = args.get("include_stats", True)

    # 参数校验
    if output_format not in ("markdown", "json"):
        return f"[ERROR] Invalid format '{output_format}'. Must be 'markdown' or 'json'."
    if group_by not in ("priority", "section", "status"):
        return f"[ERROR] Invalid group_by '{group_by}'. Must be 'priority', 'section', or 'status'."

    # 2. 提取状态数据
    findings = getattr(state, "findings", []) or []
    edits = getattr(state, "edits", []) or []
    sections_read = getattr(state, "sections_read", []) or []
    paper_sections = getattr(state, "paper_sections", {}) or {}
    conversation_turns = getattr(state, "conversation_turns", 0)
    loop_turns = getattr(state, "loop_turns", 0)
    total_tokens = getattr(state, "total_tokens", 0)
    paper_path = getattr(state, "paper_path", None)

    # 3. 构建报告数据结构
    report_data = _build_report_data(
        findings=findings,
        edits=edits,
        sections_read=sections_read,
        paper_sections=paper_sections,
        paper_path=paper_path,
        group_by=group_by,
        include_stats=include_stats,
        conversation_turns=conversation_turns,
        loop_turns=loop_turns,
        total_tokens=total_tokens,
    )

    # 4. 格式化输出
    if output_format == "json":
        return json.dumps(report_data, ensure_ascii=False, indent=2)
    else:
        return _render_markdown(report_data)


# ==============================================================
# 内部构建逻辑
# ==============================================================

def _build_report_data(
    findings: list[dict],
    edits: list[dict],
    sections_read: list[str],
    paper_sections: dict[str, str],
    paper_path: str | None,
    group_by: str,
    include_stats: bool,
    conversation_turns: int,
    loop_turns: int,
    total_tokens: int,
) -> dict:
    """构建标准化报告数据字典。"""

    # -- 元信息 --
    meta = {
        "paper_path": paper_path or "(unknown)",
        "total_findings": len(findings),
        "total_edits": len(edits),
        "sections_covered": len(sections_read),
        "total_sections": len(paper_sections),
    }

    # -- 分组 Findings --
    grouped_findings = _group_findings(findings, group_by)

    # -- 覆盖率分析 --
    coverage = _compute_coverage(sections_read, paper_sections)

    # -- 统计信息 --
    stats = None
    if include_stats:
        stats = _compute_stats(
            findings=findings,
            edits=edits,
            conversation_turns=conversation_turns,
            loop_turns=loop_turns,
            total_tokens=total_tokens,
        )

    return {
        "meta": meta,
        "grouped_findings": grouped_findings,
        "group_by": group_by,
        "edits": _format_edits(edits),
        "coverage": coverage,
        "stats": stats,
    }


def _group_findings(findings: list[dict], group_by: str) -> dict[str, list[dict]]:
    """按指定维度分组 findings。"""
    groups: dict[str, list[dict]] = defaultdict(list)

    for i, f in enumerate(findings):
        # 为每个 finding 添加序号
        entry = {**f, "_index": i}

        if group_by == "priority":
            key = f.get("priority", "unspecified")
        elif group_by == "section":
            key = f.get("section", "unspecified")
        elif group_by == "status":
            key = f.get("status", "open")
        else:
            key = "all"

        groups[key].append(entry)

    # 排序 group keys
    if group_by == "priority":
        sorted_keys = sorted(
            groups.keys(),
            key=lambda k: _PRIORITY_ORDER.get(k, 99),
        )
    elif group_by == "status":
        sorted_keys = sorted(
            groups.keys(),
            key=lambda k: _STATUS_ORDER.get(k, 99),
        )
    else:
        sorted_keys = sorted(groups.keys())

    return {k: groups[k] for k in sorted_keys}


def _format_edits(edits: list[dict]) -> list[dict]:
    """标准化 edit 记录输出。"""
    formatted = []
    for e in edits:
        formatted.append({
            "section": e.get("section", "unknown"),
            "reason": e.get("reason", ""),
            "content_preview": e.get("content_preview", "")[:120],
        })
    return formatted


def _compute_coverage(
    sections_read: list[str],
    paper_sections: dict[str, str],
) -> dict:
    """计算 section 覆盖率。"""
    all_sections = set(paper_sections.keys())
    read_set = set(sections_read)

    if not all_sections:
        return {
            "percentage": 0.0,
            "read": sorted(read_set),
            "unread": [],
        }

    covered = read_set & all_sections
    unread = all_sections - read_set

    return {
        "percentage": round(len(covered) / len(all_sections) * 100, 1),
        "read": sorted(covered),
        "unread": sorted(unread),
    }


def _compute_stats(
    findings: list[dict],
    edits: list[dict],
    conversation_turns: int,
    loop_turns: int,
    total_tokens: int,
) -> dict:
    """计算审稿统计信息。"""
    # Priority 分布
    priority_dist: dict[str, int] = defaultdict(int)
    for f in findings:
        p = f.get("priority", "unspecified")
        priority_dist[p] += 1

    # Status 分布
    status_dist: dict[str, int] = defaultdict(int)
    for f in findings:
        s = f.get("status", "open")
        status_dist[s] += 1

    return {
        "conversation_turns": conversation_turns,
        "loop_turns": loop_turns,
        "total_tokens": total_tokens,
        "priority_distribution": dict(priority_dist),
        "status_distribution": dict(status_dist),
        "edits_count": len(edits),
    }


# ==============================================================
# Markdown 渲染
# ==============================================================

def _render_markdown(report_data: dict) -> str:
    """将报告数据渲染为 Markdown 格式。"""
    lines: list[str] = []
    meta = report_data["meta"]
    group_by = report_data["group_by"]

    # -- 报告标题 --
    lines.append("# Structured Review Report")
    lines.append("")

    # -- 元信息 --
    lines.append("## Overview")
    lines.append("")
    lines.append(f"- **Paper**: {meta['paper_path']}")
    lines.append(f"- **Findings**: {meta['total_findings']}")
    lines.append(f"- **Edits Applied**: {meta['total_edits']}")
    lines.append(f"- **Coverage**: {meta['sections_covered']}/{meta['total_sections']} sections")
    lines.append("")

    # -- Findings 分组 --
    grouped = report_data["grouped_findings"]
    lines.append(f"## Findings (grouped by {group_by})")
    lines.append("")

    for group_key, group_findings in grouped.items():
        lines.append(f"### {group_key.upper()} ({len(group_findings)})")
        lines.append("")
        for f in group_findings:
            idx = f.get("_index", "?")
            finding_text = f.get("finding", "(no description)")
            section = f.get("section", "")
            status = f.get("status", "open")
            section_tag = f" [{section}]" if section and group_by != "section" else ""
            status_tag = f" ({status})" if group_by != "status" else ""
            lines.append(f"- **F{idx}**{section_tag}{status_tag}: {finding_text}")
        lines.append("")

    # -- Edits --
    edits = report_data["edits"]
    if edits:
        lines.append("## Edits Applied")
        lines.append("")
        for i, e in enumerate(edits):
            lines.append(f"{i+1}. **{e['section']}**: {e['reason']}")
            if e.get("content_preview"):
                lines.append(f"   > {e['content_preview']}")
        lines.append("")

    # -- Coverage --
    coverage = report_data["coverage"]
    lines.append("## Coverage Analysis")
    lines.append("")
    lines.append(f"- **Coverage Rate**: {coverage['percentage']}%")
    if coverage["unread"]:
        lines.append(f"- **Unread Sections**: {', '.join(coverage['unread'])}")
    lines.append("")

    # -- Stats --
    stats = report_data.get("stats")
    if stats:
        lines.append("## Session Statistics")
        lines.append("")
        lines.append(f"- **Conversation Turns**: {stats['conversation_turns']}")
        lines.append(f"- **Loop Turns**: {stats['loop_turns']}")
        lines.append(f"- **Total Tokens**: {stats['total_tokens']:,}")
        lines.append(f"- **Priority Distribution**: {_format_dist(stats['priority_distribution'])}")
        lines.append(f"- **Status Distribution**: {_format_dist(stats['status_distribution'])}")
        lines.append("")

    return "\n".join(lines)


def _format_dist(dist: dict[str, int]) -> str:
    """格式化分布字典为可读字符串。"""
    if not dist:
        return "(none)"
    parts = [f"{k}={v}" for k, v in sorted(dist.items())]
    return ", ".join(parts)
