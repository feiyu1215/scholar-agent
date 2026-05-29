"""
rule_extractor.py — 失败驱动规则生成 (E0)

从 PROGRESS.md 中自动提取重复出现的失败模式，格式化为 CLAUDE.md 兼容的规则候选。
不依赖外部库，不依赖 LLM —— 纯确定性的 keyword + pattern 聚类。

设计原则（对齐 COGNITIVE_ANCHOR）:
    - §2.1 Agent = cognition: 这不是 orchestration 脚本，而是 Agent 的"自省工具"
    - §4.3 constrain, don't control: 产出规则候选供人类决策，不自动写入 CLAUDE.md
    - 零外部依赖: 纯 Python stdlib

使用方式:
    # 独立调用
    candidates = extract_rule_candidates("docs/PROGRESS.md")
    for c in candidates:
        print(c.format_claude_md())

    # 集成到 session_finalizer（可选，Phase E 后启用）
    # end_session() 结束后调用 maybe_extract_rules()

输出格式（兼容 CLAUDE.md §从审稿实践中提炼的认知约束）:
    - [Phase X/Y/Z] 当{条件}时，不要{错误行为}，而应{正确行为}
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


# ============================================================
# Data Models
# ============================================================

@dataclass
class FailureEntry:
    """PROGRESS.md 中的一条失败/bug 记录。"""

    line_number: int
    text: str  # 原文
    phase: str  # 所属 Phase（如 "Phase 38"）
    category: str  # 归类（见 FAILURE_CATEGORIES）
    keywords: list[str] = field(default_factory=list)


@dataclass
class RuleCandidate:
    """一条规则候选。"""

    pattern_name: str  # 简短标签
    category: str  # 归类
    occurrences: int  # 出现次数
    source_phases: list[str]  # 来源 Phase 列表
    root_cause: str  # 根因一句话
    rule_text: str  # CLAUDE.md 格式的规则

    def format_claude_md(self) -> str:
        """格式化为 CLAUDE.md 兼容的单行规则。"""
        phases = "/".join(self.source_phases[:4])
        return f"- [{phases}] {self.rule_text}"


# ============================================================
# Failure Category Definitions
# ============================================================

# 每个 category: (标签, 匹配关键词列表, root_cause)
FAILURE_CATEGORIES: dict[str, tuple[list[str], str]] = {
    "satisfy_early": (
        [
            "满足即停", "satisfy early", "过早满足", "过早退出",
            "mark_complete", "收尾模式", "就停", "就想退出",
            "浅扫", "shallow", "只列", "列完.*停",
        ],
        "LLM 得到一个能自圆其说的答案后缺乏内在动机继续深入",
    ),
    "read_not_record": (
        [
            "读.*不记录", "read.*不.*update", "只读不记",
            "read_section.*0.*update_findings",
            "读了.*section.*只.*finding", "read/update",
        ],
        "LLM 无状态——阅读行为和记录行为之间缺乏 Harness 维护的比值信号",
    ),
    "no_search": (
        [
            "0.*search", "不搜索", "no.*search", "未搜索",
            "从未.*search_literature", "search.*0次",
            "搜索行为不稳定",
        ],
        "Agent 停留在理解层面未进入质疑层面，无外部信息需求",
    ),
    "understand_not_question": (
        [
            "理解.*不.*质疑", "描述性", "转述", "做笔记",
            "理解≠审稿", "summariz", "understanding.*not.*question",
            "泛泛描述", "只.*描述",
        ],
        "LLM 默认行为是总结/解释而非批判/质疑",
    ),
    "optional_unused": (
        [
            "optional.*unused", "可选.*跳过", "optional.*不用",
            "从不传", "永不调用", "可选参数",
        ],
        "LLM 遵循最短路径原则，可选行为被系统性跳过",
    ),
    "identity_insufficient": (
        [
            "identity.*不.*行为", "身份.*无变化", "identity alone",
            "改了.*identity.*无", "认知身份.*不够",
        ],
        "Identity 定义方向但不提供状态数据，Agent 不知道自己遗漏了什么",
    ),
    "no_meta_cognition": (
        [
            "不会.*抬头", "no.*self.*interrupt", "不会自主.*reflect",
            "从未反思", "不会中断", "隧道", "边际产出递减",
            "doom", "不会.*元认知",
        ],
        "LLM 是流式推理——一旦进入某条路径不会自行中断做元认知",
    ),
    "shortest_path": (
        [
            "短路径", "shortest.*path", "最短路径",
            "步骤最少", "一步到位", "绕过",
            "行为经济学", "更短路径",
        ],
        "当多条路径可达目标时，LLM 系统性选择步骤最少的那条",
    ),
    "tool_not_used": (
        [
            "工具.*不用", "tool.*不.*use", "工具存在.*不",
            "从未自主触发", "物理不可见", "永不调用",
            "工具可见.*不用",
        ],
        "工具使用需三层同时满足: 物理可见 + 认知引导 + 无更短替代",
    ),
    "content_repeat": (
        [
            "重复.*finding", "高度重复", "重叠", "去重",
            "overlap", "duplicate", "冗余.*finding",
        ],
        "LLM 无状态性导致长 session 中必定产生重复内容",
    ),
}


# ============================================================
# Core Logic
# ============================================================

def extract_failure_entries(progress_text: str) -> list[FailureEntry]:
    """
    从 PROGRESS.md 文本中提取失败/bug 相关条目。

    提取策略:
    - 逐行扫描
    - 匹配"失败/bug/fix/问题/遗漏/未解决"等信号词
    - 记录所属 Phase（最近的 Phase 标题）
    - 按 FAILURE_CATEGORIES 归类

    Returns:
        按行号排序的 FailureEntry 列表
    """
    entries: list[FailureEntry] = []
    current_phase = "Unknown"

    # Phase 标题模式: "## Phase XX" 或 "| XX |" 或 "Phase XX"
    phase_pattern = re.compile(
        r"(?:^##\s*Phase\s*(\d+)|Phase\s+(\d+)(?:\s*[-—:]|\s+\w))"
    )
    # v2 Phase 模式
    v2_phase_pattern = re.compile(r"v2[\s-]*Phase\s*(\d+)", re.IGNORECASE)

    # 失败信号词（宽松匹配，用于初筛）
    failure_signals = re.compile(
        r"bug|Bug|失败|fix|修复|问题|回归|遗漏|未解决|未检出|低效|"
        r"不.*使用|不.*搜索|不.*记录|重复|冗余|过早|shallow|"
        r"doom|满足即停|satisfy|anti.?pattern|死循环|"
        r"不会.*抬头|描述性|泛泛|浅层|identity.*不|"
        r"optional.*不|可选.*跳|绕过|短路径",
        re.IGNORECASE,
    )

    lines = progress_text.split("\n")
    for i, line in enumerate(lines, 1):
        # 更新当前 Phase
        pm = phase_pattern.search(line)
        if pm:
            phase_num = pm.group(1) or pm.group(2)
            current_phase = f"Phase {phase_num}"

        v2m = v2_phase_pattern.search(line)
        if v2m:
            current_phase = f"v2-Phase {v2m.group(1)}"

        # 初筛: 是否包含失败信号
        if not failure_signals.search(line):
            continue

        # 忽略太短的行（表头、分隔符等）
        stripped = line.strip().strip("|").strip()
        if len(stripped) < 15:
            continue

        # 归类
        matched_categories = _categorize_line(line)
        if not matched_categories:
            continue

        for cat, keywords in matched_categories:
            entries.append(FailureEntry(
                line_number=i,
                text=stripped[:200],
                phase=current_phase,
                category=cat,
                keywords=keywords,
            ))

    return entries


def _categorize_line(line: str) -> list[tuple[str, list[str]]]:
    """
    对一行文本进行多类别匹配。

    Returns:
        [(category, matched_keywords), ...]
    """
    results: list[tuple[str, list[str]]] = []
    line_lower = line.lower()

    for cat, (keywords, _root_cause) in FAILURE_CATEGORIES.items():
        matched = []
        for kw in keywords:
            try:
                if re.search(kw, line_lower):
                    matched.append(kw)
            except re.error:
                # 关键词本身不是合法 regex 时退化为 substring match
                if kw.lower() in line_lower:
                    matched.append(kw)

        if matched:
            results.append((cat, matched))

    return results


def cluster_entries(entries: list[FailureEntry]) -> dict[str, list[FailureEntry]]:
    """
    按 category 聚类。

    Returns:
        {category: [entries...]} — 只保留出现 ≥2 次的类别
    """
    clusters: dict[str, list[FailureEntry]] = {}
    for entry in entries:
        if entry.category not in clusters:
            clusters[entry.category] = []
        clusters[entry.category].append(entry)

    # 只保留 ≥2 次的模式
    return {k: v for k, v in clusters.items() if len(v) >= 2}


def generate_rule_candidates(
    clusters: dict[str, list[FailureEntry]],
) -> list[RuleCandidate]:
    """
    从聚类结果生成规则候选。

    每个 cluster 生成一条规则候选，格式与 CLAUDE.md 兼容。
    """
    # 规则模板: category -> (pattern_name, rule_text)
    rule_templates: dict[str, tuple[str, str]] = {
        "satisfy_early": (
            "满足即停",
            "当 Agent 产出初步 findings 后尝试退出时，不要允许满足即止，"
            "而应通过 Harness 呈现未覆盖维度/未验证条目的事实，让 Agent 自主决定是否继续",
        ),
        "read_not_record": (
            "只读不记",
            "当 Agent 连续读 3+ sections 未调用 update_findings 时，"
            "不要等它自行察觉，而应通过反思镜呈现 read/update 比值异常信号",
        ),
        "no_search": (
            "不搜索",
            "当 Agent 遇到论文的原创性/首创/贡献声明时，"
            "不要期望它自发搜索验证，而应在认知层级中建立'质疑>理解'的默认姿态",
        ),
        "understand_not_question": (
            "理解不质疑",
            "当 Agent 的 findings 全是描述性内容（'论文做了X'）而非批判性内容（'X 有问题因为 Y'）时，"
            "不要接受第一层产出，而应通过质量门槛要求 findings 必须包含 WHY 论证",
        ),
        "optional_unused": (
            "可选即跳过",
            "当设计工具参数/行为路径时，不要将关键行为标记为 optional，"
            "而应使其成为唯一路径或让 Harness 自动完成（顺应行为经济学而非对抗）",
        ),
        "identity_insufficient": (
            "身份不够",
            "当期望 Agent 改变行为模式时，不要只修改 identity prompt，"
            "而应配合 Harness 镜子提供结构化状态数据（维度覆盖度、产出密度等）",
        ),
        "no_meta_cognition": (
            "不会抬头",
            "当 Agent 在某方向连续 5+ 轮无新发现时，"
            "不要等待它自行反思，而应由 Harness 呈现边际产出递减信号催促方向切换",
        ),
        "shortest_path": (
            "偏好短路径",
            "当设计认知行为路径时，不要创建与已有行为竞争的平行长路径，"
            "而应在已有最短路径上自动增强（Phase 10 模式）或让短路径的奖励依赖最低限度实质行为",
        ),
        "tool_not_used": (
            "工具存在≠使用",
            "当新增工具后验证 Agent 是否使用时，不要只检查 schema 注册，"
            "而应逐一验证三层条件: 物理可见(schema列表)、认知引导(context解释何时用)、无更短替代路径",
        ),
        "content_repeat": (
            "内容重复",
            "当 Agent 在长 session 中产出 finding 时，不要直接写入，"
            "而应由 Harness 维护关键词 overlap 去重检测（≥70% 相似度拦截）",
        ),
    }

    candidates: list[RuleCandidate] = []

    for cat, entries in sorted(clusters.items(), key=lambda x: -len(x[1])):
        if cat not in rule_templates:
            continue

        pattern_name, rule_text = rule_templates[cat]
        _, root_cause = FAILURE_CATEGORIES[cat]

        # 提取去重的 Phase 列表
        phases = list(dict.fromkeys(e.phase for e in entries))

        candidates.append(RuleCandidate(
            pattern_name=pattern_name,
            category=cat,
            occurrences=len(entries),
            source_phases=phases,
            root_cause=root_cause,
            rule_text=rule_text,
        ))

    return candidates


def diff_with_existing_rules(
    candidates: list[RuleCandidate],
    claude_md_text: str,
) -> tuple[list[RuleCandidate], list[RuleCandidate]]:
    """
    与 CLAUDE.md 已有规则对比，区分已覆盖 vs 新增。

    策略: 如果 CLAUDE.md 中已有 ≥50% keywords 匹配的规则行，
    认为该候选已被覆盖。

    Returns:
        (already_covered, new_candidates)
    """
    # 提取 CLAUDE.md 中的规则行
    existing_rules: list[str] = []
    for line in claude_md_text.split("\n"):
        line_stripped = line.strip()
        if line_stripped.startswith("- [") and "当" in line_stripped:
            existing_rules.append(line_stripped.lower())

    already_covered: list[RuleCandidate] = []
    new_candidates: list[RuleCandidate] = []

    for candidate in candidates:
        # 检查是否有已有规则与此候选相关
        cat_keywords, _ = FAILURE_CATEGORIES[candidate.category]
        is_covered = False

        for existing in existing_rules:
            # 计算关键词命中率（三种匹配方式取或）
            hits = sum(
                1 for kw in cat_keywords[:6]
                if _keyword_matches_rule(kw, existing)
            )
            if hits >= 2:
                is_covered = True
                break

        if is_covered:
            already_covered.append(candidate)
        else:
            new_candidates.append(candidate)

    return already_covered, new_candidates


def _keyword_matches_rule(keyword: str, rule_text: str) -> bool:
    """
    判断单个关键词是否与某条规则匹配。

    三种策略（任一成功即为匹配）:
    1. 字面包含（keyword 直接出现在 rule_text 中）
    2. 正则匹配（keyword 本身作为 regex 对 rule_text 匹配）
    3. 模糊匹配（_fuzzy_in: 核心词段子串检查）
    """
    kw_lower = keyword.lower()
    rule_lower = rule_text.lower()

    # 1. 字面包含
    if kw_lower in rule_lower:
        return True

    # 2. 正则匹配
    try:
        if re.search(kw_lower, rule_lower):
            return True
    except re.error:
        pass

    # 3. 模糊子串
    if _fuzzy_in(keyword, rule_text):
        return True

    return False


def _fuzzy_in(keyword: str, text: str) -> bool:
    """
    模糊包含检测: 关键词的任一核心词段(≥3字符)是否在文本中。

    对中文词，额外检查前 2-3 字符的子串匹配（容忍末字不同，如"满足即停"≈"满足即止"）。
    """
    # 取关键词中所有中文/英文连续段
    parts = re.findall(r"[\u4e00-\u9fff]+|[a-zA-Z]+", keyword)
    if not parts:
        return False
    text_lower = text.lower()
    for part in parts:
        if len(part) >= 3 and part.lower() in text_lower:
            return True
        # 对中文: 如果 ≥3字符 的前缀在 text 中也算匹配
        if len(part) >= 3 and _is_chinese(part):
            prefix = part[:3]
            if prefix in text_lower:
                return True
    return False


def _is_chinese(s: str) -> bool:
    """判断字符串是否全为中文。"""
    return all("\u4e00" <= c <= "\u9fff" for c in s)


# ============================================================
# Public API
# ============================================================

def extract_rule_candidates(
    progress_path: str | Path,
    claude_md_path: str | Path | None = None,
) -> dict:
    """
    端到端提取规则候选。

    Args:
        progress_path: PROGRESS.md 文件路径
        claude_md_path: CLAUDE.md 文件路径（可选，用于 diff）

    Returns:
        {
            "entries_found": int,
            "clusters": {category: count},
            "candidates": [RuleCandidate, ...],
            "already_covered": [RuleCandidate, ...],  # 仅当提供 claude_md_path
            "new_candidates": [RuleCandidate, ...],    # 仅当提供 claude_md_path
        }
    """
    progress_text = Path(progress_path).read_text(encoding="utf-8")
    entries = extract_failure_entries(progress_text)
    clusters = cluster_entries(entries)
    candidates = generate_rule_candidates(clusters)

    result = {
        "entries_found": len(entries),
        "clusters": {k: len(v) for k, v in clusters.items()},
        "candidates": candidates,
    }

    if claude_md_path:
        claude_text = Path(claude_md_path).read_text(encoding="utf-8")
        covered, new = diff_with_existing_rules(candidates, claude_text)
        result["already_covered"] = covered
        result["new_candidates"] = new

    return result


def format_report(result: dict) -> str:
    """格式化为人类可读的报告。"""
    lines = [
        "=" * 60,
        "  E0: 失败驱动规则生成报告",
        "=" * 60,
        "",
        f"扫描条目数: {result['entries_found']}",
        f"聚类数(≥2次): {len(result['clusters'])}",
        f"规则候选数: {len(result['candidates'])}",
        "",
    ]

    if "new_candidates" in result:
        lines.append(f"已被 CLAUDE.md 覆盖: {len(result['already_covered'])}")
        lines.append(f"新增候选: {len(result['new_candidates'])}")
        lines.append("")

        if result["new_candidates"]:
            lines.append("-" * 40)
            lines.append("  新增规则候选（建议加入 CLAUDE.md）")
            lines.append("-" * 40)
            for c in result["new_candidates"]:
                lines.append("")
                lines.append(f"  [{c.pattern_name}] 出现 {c.occurrences} 次")
                lines.append(f"  来源: {', '.join(c.source_phases[:5])}")
                lines.append(f"  根因: {c.root_cause}")
                lines.append(f"  规则: {c.format_claude_md()}")

        if result["already_covered"]:
            lines.append("")
            lines.append("-" * 40)
            lines.append("  已有规则覆盖（无需新增）")
            lines.append("-" * 40)
            for c in result["already_covered"]:
                lines.append(f"  ✓ [{c.pattern_name}] ({c.occurrences} 次)")
    else:
        lines.append("-" * 40)
        lines.append("  全部规则候选")
        lines.append("-" * 40)
        for c in result["candidates"]:
            lines.append("")
            lines.append(f"  [{c.pattern_name}] 出现 {c.occurrences} 次")
            lines.append(f"  来源: {', '.join(c.source_phases[:5])}")
            lines.append(f"  根因: {c.root_cause}")
            lines.append(f"  规则: {c.format_claude_md()}")

    lines.append("")
    lines.append("=" * 60)
    return "\n".join(lines)


# ============================================================
# CLI Entry Point
# ============================================================

if __name__ == "__main__":
    import sys

    # 默认路径
    project_root = Path(__file__).parent.parent.parent
    progress = project_root / "docs" / "PROGRESS.md"
    claude_md = project_root / "CLAUDE.md"

    if len(sys.argv) > 1:
        progress = Path(sys.argv[1])
    if len(sys.argv) > 2:
        claude_md = Path(sys.argv[2])

    if not progress.exists():
        print(f"ERROR: {progress} not found")
        sys.exit(1)

    claude_path = claude_md if claude_md.exists() else None
    result = extract_rule_candidates(progress, claude_path)
    print(format_report(result))
