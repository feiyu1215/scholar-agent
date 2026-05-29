"""
core/v2/paper_type_hints.py — S1: Paper-Type 自适应认知策略（模板驱动版）

设计原则:
    - Agent 自主生成认知提示，非静态穷举
    - 提供结构化模板（schema），Agent 基于论文实际内容填充
    - 静态表保留为 few-shot 示例——引导 Agent 理解"好的提示长什么样"
    - 生成结果缓存到 WorkspaceState.cognitive_hints

与系统约束的对齐:
    C1: Agent 通过 tool (generate_cognitive_hints) 主动生成，非硬编码注入
    C2: 生成结果存入 state，由 harness 管理
    C5: 模板给结构，Agent 填内容；注入时声明"参考信息，非指令"

认知辅助框架 (COGNITIVE_ANCHOR §4.3):
    - 注入措辞: "[审稿认知提示 — 由你基于论文内容生成]...[以上由你生成，随时可修正。]"
    - 不强制 Agent 遵循提示——Agent 有完全自主权

gate_idle_rounds / min_findings_for_exit:
    供 harness completion gate 参考（R1 后续对接）。
    Agent 生成时可选填，否则走默认值。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ============================================================
# 认知提示 Schema
# ============================================================

@dataclass
class CognitiveHints:
    """Agent 生成的认知提示。存储于 WorkspaceState.cognitive_hints。"""

    # Agent 对论文的类型判断（可能比 PaperStructureIndex.paper_type 更精细）
    paper_type_description: str = ""

    # Agent 认为的关键关注维度（3-5 条）
    focus_dimensions: list[str] = field(default_factory=list)

    # Agent 对此类论文典型弱点的判断（2-4 条）
    typical_weaknesses: list[str] = field(default_factory=list)

    # Agent 计划的验证策略（2-3 条）
    verification_strategies: list[str] = field(default_factory=list)

    # 可选: Agent 自定义的 gate 参数
    gate_idle_rounds: int | None = None
    min_findings_for_exit: int | None = None

    def is_empty(self) -> bool:
        """是否为空（未生成过）。"""
        return (
            not self.paper_type_description
            and not self.focus_dimensions
            and not self.typical_weaknesses
        )

    def format_for_context(self) -> str:
        """
        格式化为 context 注入文本。

        措辞: 认知辅助框架——这是 Agent 自己生成的参考，非外部指令。
        """
        if self.is_empty():
            return ""

        lines = ["[审稿认知提示 — 由你基于论文内容生成]"]

        if self.paper_type_description:
            lines.append(f"论文特征: {self.paper_type_description}")

        if self.focus_dimensions:
            lines.append("关注维度:")
            for dim in self.focus_dimensions:
                lines.append(f"  - {dim}")

        if self.typical_weaknesses:
            lines.append("此类论文常见弱点:")
            for w in self.typical_weaknesses:
                lines.append(f"  - {w}")

        if self.verification_strategies:
            lines.append("验证策略:")
            for s in self.verification_strategies:
                lines.append(f"  - {s}")

        lines.append("[以上由你生成，随时可修正。审稿应基于论文实际内容。]")
        return "\n".join(lines)


# ============================================================
# Few-shot 示例（供 tool description 引导 Agent）
# ============================================================

COGNITIVE_HINTS_EXAMPLES: dict[str, dict] = {
    "empirical": {
        "paper_type_description": "实证研究，使用DID识别策略研究政策效果",
        "focus_dimensions": [
            "identification strategy 的可信度",
            "平行趋势假设的合理性",
            "样本选择与数据质量",
            "结果的经济显著性 (不仅仅是统计显著性)",
        ],
        "typical_weaknesses": [
            "pre-trends 可能不满足但被忽略",
            "robustness to alternative specs 不充分",
            "样本量不足以支撑异质性分析",
        ],
        "verification_strategies": [
            "检查 event study 图的 pre-period",
            "看 robustness table 的 spec 多样性",
            "验证 data section 的样本筛选标准",
        ],
    },
    "theoretical": {
        "paper_type_description": "博弈论模型，研究机制设计问题",
        "focus_dimensions": [
            "假设（assumptions）的经济合理性",
            "证明（proofs）的完整性与严谨性",
            "结论是否 non-trivial（超越已有结果）",
        ],
        "typical_weaknesses": [
            "假设过强导致结论 trivial",
            "proof 中跳步或未验证的声明",
            "未讨论与已有 characterization 结果的关系",
        ],
        "verification_strategies": [
            "逐步验证关键引理的推导",
            "构造假设边界处的反例",
            "检查是否为已有定理的简单推论",
        ],
    },
    "review": {
        "paper_type_description": "系统性综述，梳理某领域近十年进展",
        "focus_dimensions": [
            "文献覆盖是否完整",
            "分类框架是否 MECE",
            "是否有 original synthesis / 新洞察",
        ],
        "typical_weaknesses": [
            "遗漏领域内的重要贡献",
            "分类框架只是列举，缺乏整合视角",
            "未明确指出 open questions / research gaps",
        ],
        "verification_strategies": [
            "对照作者引用列表与领域顶刊发文",
            "检查框架分类的互斥性与完整性",
            "评估 future direction 是否具体可操作",
        ],
    },
}


# ============================================================
# Tool: generate_cognitive_hints
# ============================================================

# Tool description（给 LLM 看的）—— 含模板结构和一个示例
TOOL_DESCRIPTION = """基于你对论文的初步理解，生成针对性的审稿认知提示。

**何时使用**: 读完论文结构/摘要后，对论文的类型和方法论有了初步判断时调用。
**作用**: 将你的审稿策略形式化并记录，后续轮次可作为参考。

**参数**:
- paper_type_description (str, 必填): 你对论文类型/特征的描述（如"使用DID的实证论文"、"博弈论机制设计"、"NLP综述"）
- focus_dimensions (list[str], 必填): 你认为应该重点关注的 3-5 个维度
- typical_weaknesses (list[str], 可选): 此类论文的 2-4 个常见弱点
- verification_strategies (list[str], 可选): 你计划的 2-3 个验证策略

**示例** (实证论文):
  paper_type_description: "计量实证研究，用RDD识别策略"
  focus_dimensions: ["断点处连续性假设", "带宽选择的敏感性", "数据操纵检验"]
  typical_weaknesses: ["McCrary test 被省略", "带宽外推不稳健"]
  verification_strategies: ["检查 density test", "看不同带宽下估计的稳定性"]

你可以随时再次调用来修正之前的认知提示。"""


def handle_generate_cognitive_hints(args: dict) -> tuple[str, CognitiveHints]:
    """
    处理 generate_cognitive_hints tool call。

    Args:
        args: Agent 提供的参数 dict

    Returns:
        (response_text, cognitive_hints): 给 Agent 的反馈 + 结构化对象

    Raises:
        无——即使参数不完整也宽容处理。
    """
    # 解析参数（宽容模式：缺失字段给默认值）
    paper_type_desc = args.get("paper_type_description", "").strip()
    focus_dims = args.get("focus_dimensions", [])
    typical_weak = args.get("typical_weaknesses", [])
    verify_strats = args.get("verification_strategies", [])

    # 校验: paper_type_description 必填
    if not paper_type_desc:
        return (
            "请提供 paper_type_description 参数——描述你对论文类型/特征的判断。"
            "例如: '使用DID的劳动经济学实证论文' 或 '深度学习方法综述'。",
            CognitiveHints(),
        )

    # 校验: focus_dimensions 必填且至少 1 条
    if not focus_dims:
        return (
            "请提供至少 1 条 focus_dimensions——你认为审稿应重点关注的维度。"
            "例如: ['identification strategy', '样本代表性', '外部效度']",
            CognitiveHints(),
        )

    # 规范化: 确保是列表
    if isinstance(focus_dims, str):
        focus_dims = [focus_dims]
    if isinstance(typical_weak, str):
        typical_weak = [typical_weak]
    if isinstance(verify_strats, str):
        verify_strats = [verify_strats]

    # B4: 可选的 gate 参数（Agent 可根据论文复杂度自行设定）
    gate_idle = args.get("gate_idle_rounds")
    gate_min_findings = args.get("min_findings_for_exit")
    gate_idle_int = int(gate_idle) if gate_idle is not None else None
    gate_min_int = int(gate_min_findings) if gate_min_findings is not None else None

    # 构建对象
    hints = CognitiveHints(
        paper_type_description=paper_type_desc,
        focus_dimensions=focus_dims,
        typical_weaknesses=typical_weak,
        verification_strategies=verify_strats,
        gate_idle_rounds=gate_idle_int,
        min_findings_for_exit=gate_min_int,
    )

    # 反馈
    dim_count = len(focus_dims)
    weak_count = len(typical_weak)
    strat_count = len(verify_strats)
    response = (
        f"✅ 认知提示已记录。"
        f"论文特征: {paper_type_desc} | "
        f"关注维度: {dim_count} | 常见弱点: {weak_count} | 验证策略: {strat_count}。\n"
        f"后续轮次你会在上下文中看到这些提示作为参考。你可以随时再次调用来修正。"
    )

    return response, hints


# ============================================================
# Gate 参数（保留兼容接口供 R1 消费）
# ============================================================

# 默认 gate 参数（当 Agent 未指定 / cognitive_hints 为空时使用）
DEFAULT_GATE_PARAMS = {
    "gate_idle_rounds": 4,
    "min_findings_for_exit": 3,
}


def get_gate_params(cognitive_hints: CognitiveHints | None = None) -> dict:
    """
    获取 completion gate 参数。

    优先使用 Agent 自定义值，否则使用默认值。

    Returns:
        {"gate_idle_rounds": int, "min_findings_for_exit": int}
    """
    if cognitive_hints is not None:
        return {
            "gate_idle_rounds": cognitive_hints.gate_idle_rounds or DEFAULT_GATE_PARAMS["gate_idle_rounds"],
            "min_findings_for_exit": cognitive_hints.min_findings_for_exit or DEFAULT_GATE_PARAMS["min_findings_for_exit"],
        }
    return DEFAULT_GATE_PARAMS.copy()
