"""
core/content_blocks.py — 类型化内容块系统 (Infrastructure)

消息不再是纯 string，而是 list[ContentBlock]。
每种 block 有明确的语义类型，支持：
  - 区分 Agent 推理过程和最终输出（ThinkingBlock vs TextBlock）
  - 结构化的审稿发现（FindingBlock 强制包含所有字段）
  - 图表引用的富内容（FigureBlock 携带视觉数据）
  - 内部指令注入（HintBlock 不对用户可见）

设计原则：
  - Block 是不可变数据对象（frozen dataclass）
  - 所有 Block 有统一的 render 接口（转为 LLM API 可消费的格式）
  - ContentBlocks 是整个系统的数据骨架，所有 Phase 的信息流转建立其上
  - 向后兼容：纯 string 可以自动包装为 TextBlock
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Union
import json


# ==============================================================
# Block 类型枚举
# ==============================================================

class BlockType(Enum):
    """内容块类型"""
    TEXT = "text"
    THINKING = "thinking"
    FINDING = "finding"
    FIGURE = "figure"
    TABLE = "table"
    HINT = "hint"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    CITATION = "citation"
    CODE = "code"


# ==============================================================
# 内容块定义
# ==============================================================

@dataclass(frozen=True)
class TextBlock:
    """普通文本内容。"""
    text: str
    block_type: BlockType = field(default=BlockType.TEXT, init=False)

    def render(self) -> str:
        return self.text

    def to_api_format(self) -> dict:
        """转为 LLM API 格式（OpenAI content part）。"""
        return {"type": "text", "text": self.text}


@dataclass(frozen=True)
class ThinkingBlock:
    """Agent 的推理过程，与最终输出分离。

    用于 Phase 6 反思系统——推理过程可以单独审计/改进，
    不需要混入最终的审稿意见中。
    """
    reasoning: str
    confidence: float = 0.0          # 推理结论的置信度 (0-1)
    reasoning_type: str = "general"  # "deductive" / "inductive" / "abductive" / "general"
    block_type: BlockType = field(default=BlockType.THINKING, init=False)

    def render(self) -> str:
        return f"[Thinking | conf={self.confidence:.2f}] {self.reasoning}"

    def to_api_format(self) -> dict:
        return {"type": "text", "text": f"<thinking>\n{self.reasoning}\n</thinking>"}


@dataclass(frozen=True)
class FindingBlock:
    """审稿发现，结构化表示。

    强制要求所有关键字段，避免每次靠 prompt 约束输出格式。
    这是审稿 Agent 最核心的输出单位。
    """
    category: str           # "methodology" | "statistics" | "clarity" | "logic" | "citation"
    severity: str           # "critical" | "major" | "minor" | "suggestion"
    description: str        # 问题描述
    evidence: str           # 论文中的支撑证据（引文/数据）
    suggestion: str         # 改进建议
    section: str = ""       # 涉及的论文 section
    confidence: float = 0.8 # Agent 对此发现的确信度
    status: str = "verified"  # "verified" | "needs_verification" | "tentative"
    block_type: BlockType = field(default=BlockType.FINDING, init=False)

    def render(self) -> str:
        return (
            f"[{self.severity.upper()}|{self.category}] {self.description}\n"
            f"  Evidence: {self.evidence[:200]}\n"
            f"  Suggestion: {self.suggestion}"
        )

    def to_api_format(self) -> dict:
        return {"type": "text", "text": self.render()}

    def to_finding_dict(self) -> dict:
        """转为现有 findings 列表兼容的 dict 格式。"""
        return {
            "category": self.category,
            "priority": "high" if self.severity in ("critical", "major") else "medium",
            "finding": self.description,
            "evidence": self.evidence,
            "suggestion": self.suggestion,
            "section": self.section,
            "status": self.status,
        }


@dataclass(frozen=True)
class FigureBlock:
    """论文图表引用（Phase 9 多模态）。

    携带图片数据和分析，支持视觉信息的结构化传递。
    """
    figure_id: str              # 图表标识（如 "Figure 3"）
    caption: str                # 图表标题/说明
    image_path: str = ""        # 图片文件路径（本地）
    image_data: bytes | None = field(default=None, repr=False)  # 图片二进制数据
    analysis: str = ""          # AI 分析结果
    data_extracted: dict = field(default_factory=dict)  # 从图表中提取的数据
    block_type: BlockType = field(default=BlockType.FIGURE, init=False)

    def render(self) -> str:
        parts = [f"[Figure: {self.figure_id}] {self.caption}"]
        if self.analysis:
            parts.append(f"  Analysis: {self.analysis}")
        return "\n".join(parts)

    def to_api_format(self) -> dict:
        if self.image_data:
            import base64
            b64 = base64.b64encode(self.image_data).decode()
            return {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}"},
            }
        return {"type": "text", "text": self.render()}


@dataclass(frozen=True)
class TableBlock:
    """表格数据块。"""
    table_id: str
    caption: str = ""
    headers: tuple = field(default_factory=tuple)  # frozen requires tuple
    rows: tuple = field(default_factory=tuple)      # tuple of tuples
    analysis: str = ""
    block_type: BlockType = field(default=BlockType.TABLE, init=False)

    def render(self) -> str:
        parts = [f"[Table: {self.table_id}] {self.caption}"]
        if self.headers:
            parts.append(" | ".join(str(h) for h in self.headers))
            parts.append("-" * 40)
        for row in self.rows[:10]:  # 限制展示行数
            parts.append(" | ".join(str(c) for c in row))
        if len(self.rows) > 10:
            parts.append(f"  ... ({len(self.rows) - 10} more rows)")
        return "\n".join(parts)

    def to_api_format(self) -> dict:
        return {"type": "text", "text": self.render()}


@dataclass(frozen=True)
class HintBlock:
    """内部指令注入（不对用户可见）。

    用于 Harness 向 Agent 注入上下文、约束、提醒等，
    不会出现在最终的审稿报告中。
    """
    content: str
    hint_type: str = "context"  # "context" | "constraint" | "nudge" | "recovery"
    priority: str = "normal"   # "low" | "normal" | "high" | "critical"
    block_type: BlockType = field(default=BlockType.HINT, init=False)

    def render(self) -> str:
        return f"[Hint|{self.hint_type}|{self.priority}] {self.content}"

    def to_api_format(self) -> dict:
        return {"type": "text", "text": self.content}


@dataclass(frozen=True)
class CitationBlock:
    """学术引用块。"""
    citation_key: str           # 引用键（如 "Smith2023"）
    title: str = ""
    authors: str = ""
    year: str = ""
    venue: str = ""
    relevance: str = ""         # 与当前论文的关系描述
    block_type: BlockType = field(default=BlockType.CITATION, init=False)

    def render(self) -> str:
        return f"[{self.citation_key}] {self.authors} ({self.year}). {self.title}. {self.venue}"

    def to_api_format(self) -> dict:
        return {"type": "text", "text": self.render()}


@dataclass(frozen=True)
class CodeBlock:
    """代码块（用于 Phase 9 数据分析脚本）。"""
    code: str
    language: str = "python"
    purpose: str = ""           # 代码目的描述
    output: str = ""            # 执行结果
    block_type: BlockType = field(default=BlockType.CODE, init=False)

    def render(self) -> str:
        parts = []
        if self.purpose:
            parts.append(f"# {self.purpose}")
        parts.append(f"```{self.language}\n{self.code}\n```")
        if self.output:
            parts.append(f"Output: {self.output[:500]}")
        return "\n".join(parts)

    def to_api_format(self) -> dict:
        return {"type": "text", "text": self.render()}


# ==============================================================
# ContentBlock 联合类型
# ==============================================================

ContentBlock = Union[
    TextBlock,
    ThinkingBlock,
    FindingBlock,
    FigureBlock,
    TableBlock,
    HintBlock,
    CitationBlock,
    CodeBlock,
]


# ==============================================================
# 工具函数
# ==============================================================

def wrap_text(text: str) -> TextBlock:
    """将纯字符串包装为 TextBlock。"""
    return TextBlock(text=text)


def blocks_to_string(blocks: list[ContentBlock]) -> str:
    """将 ContentBlock 列表渲染为纯文本。

    用于向后兼容：现有代码期望 string 时，可以用此函数转换。
    """
    return "\n\n".join(block.render() for block in blocks)


def blocks_to_api_parts(blocks: list[ContentBlock]) -> list[dict]:
    """将 ContentBlock 列表转为 LLM API 的 content parts 格式。"""
    return [block.to_api_format() for block in blocks]


def string_to_blocks(text: str) -> list[ContentBlock]:
    """将纯字符串转为单个 TextBlock 的列表。

    向后兼容：现有代码产出 string 时，可以自动适配为 ContentBlock 格式。
    """
    if not text:
        return []
    return [TextBlock(text=text)]


def filter_blocks(
    blocks: list[ContentBlock],
    block_type: BlockType,
) -> list[ContentBlock]:
    """按类型过滤 ContentBlock。"""
    return [b for b in blocks if b.block_type == block_type]


def extract_findings(blocks: list[ContentBlock]) -> list[FindingBlock]:
    """从 ContentBlock 列表中提取所有 FindingBlock。"""
    return [b for b in blocks if isinstance(b, FindingBlock)]


def extract_thinking(blocks: list[ContentBlock]) -> list[ThinkingBlock]:
    """从 ContentBlock 列表中提取所有 ThinkingBlock。"""
    return [b for b in blocks if isinstance(b, ThinkingBlock)]
