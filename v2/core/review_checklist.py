"""
core/review_checklist.py — ReviewChecklist: 审稿维度覆盖追踪

设计依据:
    - Recall 提升计划 S3: 结构化追踪已覆盖/未覆盖的审稿维度
    - C5 (Constrain, don't control): 仅呈现覆盖状态，不强制 Agent 行为
    - C12 (图认知优先): 类似 PCG，Agent 查 Checklist 而非回忆"自己审过什么"

核心用途:
    1. 论文加载时从 DomainTemplate.methodology_checklist 初始化
    2. Agent 每次 submit_finding 时自动匹配并标记覆盖
    3. format_for_zone_a() 输出当前覆盖/缺口状态
    4. boundary_guard 可查询覆盖率作为 nudge 条件

生命周期:
    Paper load → PCG initialized → ReviewChecklist.from_template()
    DEEP_REVIEW → findings submitted → auto-mark coverage
    Completion gate → check uncovered dimensions

降级:
    - 无 DomainTemplate 匹配 → 空 checklist (is_empty=True)，不影响流程
    - 自动匹配失败 → 保持 uncovered，等待 completion gate 提示
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ==============================================================
# Checklist Item
# ==============================================================

@dataclass
class ChecklistItem:
    """单个审查维度条目。"""

    description: str                    # 审查维度描述 (来自 methodology_checklist)
    covered: bool = False               # 是否已被 finding 覆盖
    finding_ids: list[int] = field(default_factory=list)  # 覆盖此条目的 finding 索引
    notes: str = ""                     # Agent 可选备注 (如 "本文不适用")

    def mark_covered(self, finding_id: int) -> None:
        """标记为已覆盖。"""
        self.covered = True
        if finding_id not in self.finding_ids:
            self.finding_ids.append(finding_id)


# ==============================================================
# ReviewChecklist
# ==============================================================

@dataclass
class ReviewChecklist:
    """审稿维度覆盖追踪器。

    不控制 Agent 做什么，只呈现"哪些维度审过了、哪些还没审"这一客观事实。
    Agent 可以看到缺口后主动去补，也可以判断"本文该维度不适用"后跳过。
    """

    paper_type: str = ""
    items: list[ChecklistItem] = field(default_factory=list)

    # 匹配关键词映射: checklist index → keywords (用于自动匹配 finding)
    _match_keywords: dict[int, set[str]] = field(default_factory=dict, repr=False)

    def is_empty(self) -> bool:
        return len(self.items) == 0

    @classmethod
    def from_template(cls, paper_type: str, methodology_checklist: list[str]) -> "ReviewChecklist":
        """从 DomainTemplate 的 methodology_checklist 创建。

        同时为每个条目生成匹配关键词，用于自动检测 finding 是否覆盖该维度。
        """
        checklist = cls(paper_type=paper_type)
        for i, desc in enumerate(methodology_checklist):
            checklist.items.append(ChecklistItem(description=desc))
            # 从描述中提取关键词用于自动匹配
            checklist._match_keywords[i] = cls._extract_keywords(desc)
        return checklist

    @staticmethod
    def _extract_keywords(description: str) -> set[str]:
        """从审查维度描述中提取匹配关键词。

        策略: 提取中英文混合关键词，用于模糊匹配 finding 文本。
        """
        # 通用停用词
        stopwords = {
            "是否", "是否有", "的", "了", "在", "和", "与", "对",
            "是", "也", "有", "个", "条", "这", "那", "被",
            "if", "the", "a", "an", "is", "are", "for", "of", "to", "in",
        }

        # 分词 (简易: 中英文混合，按标点和空格切分)
        # 英文词
        en_words = set(re.findall(r'[a-zA-Z_-]{3,}', description.lower()))
        # 中文2-4字组合 (保留有含义的短词)
        cn_words = set(re.findall(r'[\u4e00-\u9fff]{2,4}', description))

        keywords = (en_words | cn_words) - stopwords

        # 保证至少有2个关键词
        if len(keywords) < 2:
            keywords = en_words | cn_words

        return keywords

    def try_match_finding(self, finding_idx: int, finding_text: str) -> list[int]:
        """尝试将一条 finding 匹配到 checklist 条目。

        匹配策略: finding 文本包含条目关键词集中 ≥40% 的关键词即判定覆盖。

        Args:
            finding_idx: finding 在 state.findings 中的索引
            finding_text: finding 的文本内容（已 lower）

        Returns:
            被匹配覆盖的 checklist 条目索引列表
        """
        matched_indices = []
        finding_lower = finding_text.lower()

        for i, keywords in self._match_keywords.items():
            if not keywords or self.items[i].covered:
                continue

            # 计算关键词覆盖率
            hit_count = sum(1 for kw in keywords if kw in finding_lower)
            coverage_ratio = hit_count / len(keywords) if keywords else 0

            if coverage_ratio >= 0.4:
                self.items[i].mark_covered(finding_idx)
                matched_indices.append(i)
                logger.debug(
                    f"ReviewChecklist: finding[{finding_idx}] matched item[{i}] "
                    f"(ratio={coverage_ratio:.2f}, keywords_hit={hit_count}/{len(keywords)})"
                )

        return matched_indices

    def mark_not_applicable(self, item_idx: int, reason: str = "") -> None:
        """Agent 主动标记某维度为"不适用"。

        这是 C5 精神: Agent 可以判断"这个维度对本文不适用"，
        而不是被强制审查每个维度。
        """
        if 0 <= item_idx < len(self.items):
            self.items[item_idx].covered = True
            self.items[item_idx].notes = reason or "Agent 判断: 不适用"

    @property
    def coverage_ratio(self) -> float:
        """已覆盖比例。"""
        if not self.items:
            return 1.0
        return sum(1 for item in self.items if item.covered) / len(self.items)

    @property
    def uncovered_items(self) -> list[tuple[int, ChecklistItem]]:
        """未覆盖的条目 (索引, 条目) 列表。"""
        return [(i, item) for i, item in enumerate(self.items) if not item.covered]

    def format_for_zone_a(self, max_chars: int = 600) -> str:
        """格式化为 Zone A 展示文本。

        紧凑格式，只显示覆盖状态概览 + 未覆盖条目提示。
        """
        if self.is_empty():
            return ""

        lines = [
            f"[审稿维度 Checklist] type={self.paper_type} | "
            f"覆盖 {sum(1 for i in self.items if i.covered)}/{len(self.items)}"
        ]

        for i, item in enumerate(self.items):
            mark = "✓" if item.covered else "○"
            # 截断描述到 50 字符
            desc = item.description[:50] + ("…" if len(item.description) > 50 else "")
            suffix = ""
            if item.notes:
                suffix = f" [{item.notes[:15]}]"
            elif item.finding_ids:
                suffix = f" [F:{','.join(str(f) for f in item.finding_ids[:3])}]"
            lines.append(f"  {mark} {desc}{suffix}")

        result = "\n".join(lines)
        if len(result) > max_chars:
            result = result[:max_chars - 15] + "\n  ... [截断]"
        return result

    def serialize(self) -> dict[str, Any]:
        """序列化用于 compaction 恢复。"""
        return {
            "paper_type": self.paper_type,
            "items": [
                {
                    "desc": item.description,
                    "covered": item.covered,
                    "fids": item.finding_ids,
                    "notes": item.notes,
                }
                for item in self.items
            ],
        }

    @classmethod
    def deserialize(cls, data: dict[str, Any]) -> "ReviewChecklist":
        """从序列化数据恢复。"""
        checklist = cls(paper_type=data.get("paper_type", ""))
        for item_data in data.get("items", []):
            item = ChecklistItem(
                description=item_data["desc"],
                covered=item_data.get("covered", False),
                finding_ids=item_data.get("fids", []),
                notes=item_data.get("notes", ""),
            )
            checklist.items.append(item)
            # 重建关键词索引
            idx = len(checklist.items) - 1
            checklist._match_keywords[idx] = cls._extract_keywords(item.description)
        return checklist
