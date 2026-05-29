"""
core/v2/cognition_graph.py — K1: 审稿认知图谱

审稿结束时的结构化认知产出。记录 Agent 如何理解这篇论文——
不只是"找到了什么问题"(findings)，还包括"如何理解论证链"、
"假说追查经历了什么"、"审稿策略经验是什么"。

三重价值:
    1. 对用户: 比 findings 列表更能展示审稿深度
    2. 对 Agent 自身: 作为经验的结构化记录，可跨会话复用
    3. 对系统: 积累后可分析"什么类型论文审得好/差"

设计原则:
    - 零 LLM 调用——全部从已有 state 中结构化提取
    - 构建时机: mark_complete 成功后
    - cognitive_hints 持久化: 写入 ProceduralPattern（Layer 3）
    - 输出格式: 人可读 + 机器可解析（双重用途）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from core.state import WorkspaceState
    from core.hypothesis import HypothesisModule
    from core.paper_type_hints import CognitiveHints


# ============================================================
# ReviewCognitionGraph
# ============================================================

@dataclass
class ReviewCognitionGraph:
    """
    审稿认知图谱——审稿过程的结构化产出。

    这不是 Agent 的"成绩单"，而是它认知轨迹的记录。
    Agent 下次审类似论文时，可以从中回溯经验。
    """

    # --- 论文理解 ---
    paper_type: str = ""                    # Agent 判断的论文类型
    core_claims: list[dict] = field(default_factory=list)
    # [{claim: str, evidence_sections: list[str], assessed_strength: str}]

    # --- 证据链 ---
    evidence_chains: list[dict] = field(default_factory=list)
    # [{claim: str, supporting_evidence: list[str], chain_integrity: str}]

    # --- 假说追查结果 ---
    hypothesis_outcomes: list[dict] = field(default_factory=list)
    # [{statement: str, outcome: str, key_evidence: str}]

    # --- Findings 聚类 ---
    finding_clusters: list[dict] = field(default_factory=list)
    # [{theme: str, finding_indices: list[int], cluster_severity: str}]

    # --- 审稿策略经验（由 S1 认知提示演化而来）---
    review_strategy: dict = field(default_factory=dict)
    # {paper_type_description: str, focus_dimensions: list, effective_approaches: list, lessons: list}

    # --- 审稿自评 ---
    sections_read_ratio: float = 0.0        # 已读/总 sections
    total_findings: int = 0
    verified_findings: int = 0
    review_depth: str = "standard"          # "surface" | "standard" | "deep"
    loop_turns_used: int = 0

    def is_empty(self) -> bool:
        """图谱是否为空（未构建）。"""
        return self.total_findings == 0 and not self.core_claims

    def format_for_output(self) -> str:
        """
        格式化为人可读输出（mark_complete 时展示给用户）。
        """
        if self.is_empty():
            return ""

        lines = ["═══ 审稿认知图谱 ═══"]

        # 论文理解
        if self.paper_type:
            lines.append(f"\n论文类型: {self.paper_type}")

        # 核心论点
        if self.core_claims:
            lines.append(f"\n核心论点 ({len(self.core_claims)}):")
            for c in self.core_claims[:5]:
                strength = c.get("assessed_strength", "?")
                lines.append(f"  • {c.get('claim', '?')} [强度: {strength}]")

        # 假说追查
        if self.hypothesis_outcomes:
            lines.append(f"\n假说追查 ({len(self.hypothesis_outcomes)}):")
            for h in self.hypothesis_outcomes:
                lines.append(f"  • {h.get('statement', '?')[:60]} → {h.get('outcome', '?')}")

        # Findings 聚类
        if self.finding_clusters:
            lines.append(f"\n发现聚类 ({len(self.finding_clusters)}):")
            for cl in self.finding_clusters:
                count = len(cl.get("finding_indices", []))
                lines.append(f"  • {cl.get('theme', '?')} ({count} 条, {cl.get('cluster_severity', '?')})")

        # 审稿策略经验
        if self.review_strategy.get("lessons"):
            lines.append("\n审稿经验:")
            for lesson in self.review_strategy["lessons"][:3]:
                lines.append(f"  • {lesson}")

        # 自评
        lines.append(f"\n审稿覆盖: {self.sections_read_ratio:.0%} sections | "
                     f"深度: {self.review_depth} | "
                     f"发现: {self.total_findings} (已验证: {self.verified_findings}) | "
                     f"用时: {self.loop_turns_used} 轮")

        return "\n".join(lines)

    def to_dict(self) -> dict:
        """序列化为 dict（供持久化和传输）。"""
        return {
            "paper_type": self.paper_type,
            "core_claims": self.core_claims,
            "evidence_chains": self.evidence_chains,
            "hypothesis_outcomes": self.hypothesis_outcomes,
            "finding_clusters": self.finding_clusters,
            "review_strategy": self.review_strategy,
            "sections_read_ratio": self.sections_read_ratio,
            "total_findings": self.total_findings,
            "verified_findings": self.verified_findings,
            "review_depth": self.review_depth,
            "loop_turns_used": self.loop_turns_used,
        }


# ============================================================
# Builder: 零 LLM 构建图谱
# ============================================================

def build_cognition_graph(
    state: Any,  # WorkspaceState
    hypothesis_module: Any | None = None,  # HypothesisModule
    cognitive_hints: Any | None = None,  # CognitiveHints
) -> ReviewCognitionGraph:
    """
    从已有 state 构建认知图谱。零 LLM 调用。

    数据来源:
        - state.findings → core_claims + finding_clusters
        - state.paper_structure_index → paper_type
        - state.sections_read + paper_sections → sections_read_ratio
        - hypothesis_module → hypothesis_outcomes
        - cognitive_hints → review_strategy

    Args:
        state: WorkspaceState
        hypothesis_module: HypothesisModule (可为 None)
        cognitive_hints: CognitiveHints (可为 None)

    Returns:
        构建好的 ReviewCognitionGraph
    """
    graph = ReviewCognitionGraph()

    # --- 论文类型 ---
    idx = getattr(state, "paper_structure_index", None)
    if idx is not None and not idx.is_empty():
        graph.paper_type = idx.paper_type

    # 如果 Agent 生成了更精细的描述，用它覆盖
    if cognitive_hints and cognitive_hints.paper_type_description:
        graph.paper_type = cognitive_hints.paper_type_description

    # --- 核心论点（从高优 findings 逆推）---
    graph.core_claims = _extract_core_claims(state.findings)

    # --- 证据链 ---
    graph.evidence_chains = _extract_evidence_chains(state.findings)

    # --- 假说追查 ---
    if hypothesis_module is not None:
        graph.hypothesis_outcomes = _extract_hypothesis_outcomes(hypothesis_module)

    # --- Findings 聚类 ---
    graph.finding_clusters = _cluster_findings(state.findings)

    # --- 审稿策略经验 ---
    graph.review_strategy = _build_review_strategy(cognitive_hints, state.findings)

    # --- 审稿自评 ---
    total_sections = len([k for k in state.paper_sections if k != "full"])
    read_sections = len(state.sections_read)
    graph.sections_read_ratio = read_sections / max(total_sections, 1)
    graph.total_findings = len(state.findings)
    graph.verified_findings = sum(
        1 for f in state.findings if f.get("status") == "verified"
    )
    graph.loop_turns_used = state.loop_turns
    graph.review_depth = _assess_depth(
        read_ratio=graph.sections_read_ratio,
        findings_count=graph.total_findings,
        loop_turns=state.loop_turns,
    )

    return graph


# ============================================================
# 内部构建函数
# ============================================================

def _extract_core_claims(findings: list[dict]) -> list[dict]:
    """
    从 findings 中提取论文核心论点。

    逻辑: 高优 findings 通常针对论文的核心声明。
    从 finding text 中提取被质疑的 claim。
    """
    claims = []
    for f in findings:
        if f.get("priority") in ("high", "medium"):
            section = f.get("section", "unknown")
            # 每个高优 finding 对应一个被质疑的 claim
            claims.append({
                "claim": f.get("finding", "")[:120],
                "evidence_sections": [section] if section != "unknown" else [],
                "assessed_strength": _finding_priority_to_strength(f.get("priority", "low")),
            })
    return claims[:10]  # 最多 10 条


def _extract_evidence_chains(findings: list[dict]) -> list[dict]:
    """
    从有证据的 findings 构建证据链。
    """
    chains = []
    for f in findings:
        if f.get("evidence") and len(f["evidence"]) > 20:
            chains.append({
                "claim": f.get("finding", "")[:80],
                "supporting_evidence": [f["evidence"][:100]],
                "chain_integrity": "verified" if f.get("status") == "verified" else "partial",
            })
    return chains[:8]


def _extract_hypothesis_outcomes(hypothesis_module: Any) -> list[dict]:
    """
    从 HypothesisModule 提取假说追查结果。
    """
    outcomes = []
    for h in hypothesis_module.hypotheses:
        outcomes.append({
            "statement": h.statement[:80],
            "outcome": h.status.value,
            "key_evidence": (
                h.evidence[-1].content[:60] if h.evidence else ""
            ),
        })
    return outcomes


def _cluster_findings(findings: list[dict]) -> list[dict]:
    """
    对 findings 做简单主题聚类（基于 section 分组）。

    不用 NLP——直接按 section 归组，同 section 的 findings 归为一簇。
    """
    section_groups: dict[str, list[int]] = {}
    for i, f in enumerate(findings):
        section = f.get("section", "general")
        section_groups.setdefault(section, []).append(i)

    clusters = []
    for section, indices in section_groups.items():
        if len(indices) >= 1:
            # 确定 cluster 严重度（取最高优先级）
            priorities = [findings[i].get("priority", "low") for i in indices]
            severity = "high" if "high" in priorities else ("medium" if "medium" in priorities else "low")
            clusters.append({
                "theme": section,
                "finding_indices": indices,
                "cluster_severity": severity,
            })

    # 按严重度排序
    severity_order: dict[str, int] = {"high": 0, "medium": 1, "low": 2}
    clusters.sort(key=lambda c: severity_order.get(str(c["cluster_severity"]), 3))
    return clusters[:8]


def _build_review_strategy(cognitive_hints: Any | None, findings: list[dict]) -> dict:
    """
    构建审稿策略经验记录。

    来源:
        - cognitive_hints（Agent 自主生成的审稿策略）
        - findings 的分布模式（Agent 实际关注了什么）

    产出: 结构化的策略经验，可供跨会话复用。
    """
    strategy: dict = {
        "paper_type_description": "",
        "focus_dimensions": [],
        "effective_approaches": [],
        "lessons": [],
    }

    if cognitive_hints and not cognitive_hints.is_empty():
        strategy["paper_type_description"] = cognitive_hints.paper_type_description
        strategy["focus_dimensions"] = cognitive_hints.focus_dimensions
        # verification_strategies 中实际产生了 findings 的 = effective
        strategy["effective_approaches"] = cognitive_hints.verification_strategies

    # 从 findings 分布推断实际关注维度
    section_counts: dict[str, int] = {}
    for f in findings:
        sec = f.get("section", "general")
        section_counts[sec] = section_counts.get(sec, 0) + 1

    if section_counts:
        # 最关注的 sections = 实际上产出最多 findings 的
        top_sections = sorted(section_counts.items(), key=lambda x: x[1], reverse=True)[:3]
        actual_focus = [f"{sec} ({count} findings)" for sec, count in top_sections]
        strategy["lessons"].append(f"实际产出集中在: {', '.join(actual_focus)}")

    # 如果有高优 findings，记录主要问题类型
    high_findings = [f for f in findings if f.get("priority") == "high"]
    if high_findings:
        strategy["lessons"].append(
            f"高优发现 {len(high_findings)} 条，主要涉及: "
            + ", ".join(set(f.get("section", "?") for f in high_findings[:3]))
        )

    return strategy


def _finding_priority_to_strength(priority: str) -> str:
    """finding priority → claim strength（被质疑的程度）。"""
    return {"high": "weak", "medium": "questionable", "low": "adequate"}.get(priority, "unknown")


def _assess_depth(read_ratio: float, findings_count: int, loop_turns: int) -> str:
    """评估审稿深度。"""
    if read_ratio >= 0.8 and findings_count >= 5 and loop_turns >= 15:
        return "deep"
    elif read_ratio >= 0.5 and findings_count >= 3:
        return "standard"
    else:
        return "surface"


# ============================================================
# 认知持久化: cognitive_hints → ProceduralPattern
# ============================================================

def persist_cognitive_hints_as_experience(
    cognitive_hints: Any,  # CognitiveHints
    memory_store: Any,  # MemoryStore
    paper_id: str,
    findings_count: int,
) -> int:
    """
    将 Agent 的认知提示持久化为程序性经验（ProceduralPattern）。

    这是"认知生长"的核心路径：Agent 审稿时生成的策略，
    通过 end_session 沉淀为跨会话可复用的经验。
    下次审类似论文时，Agent 可通过 memory recall 看到过往策略。

    Args:
        cognitive_hints: Agent 生成的认知提示
        memory_store: 跨会话记忆存储
        paper_id: 当前论文 ID
        findings_count: 本次审稿产出的 findings 数（用于评估策略有效性）

    Returns:
        持久化的 pattern 数量
    """
    if cognitive_hints is None or cognitive_hints.is_empty():
        return 0

    persisted = 0

    # 1. 审稿关注维度 → ProceduralPattern (category="review_focus")
    #    trigger_context = 论文类型描述
    #    description = 关注维度
    #    effectiveness = 基于 findings_count 的粗略评估
    effectiveness = min(findings_count / 5.0, 1.0)  # 5+ findings = 满分

    for dim in cognitive_hints.focus_dimensions:
        memory_store.add_or_reinforce_procedure(
            category="review_focus",
            description=dim,
            trigger_context=f"论文类型: {cognitive_hints.paper_type_description}",
            effectiveness_score=effectiveness,
        )
        persisted += 1

    # 2. 验证策略 → ProceduralPattern (category="verification_strategy")
    for strat in cognitive_hints.verification_strategies:
        memory_store.add_or_reinforce_procedure(
            category="verification_strategy",
            description=strat,
            trigger_context=f"论文类型: {cognitive_hints.paper_type_description}",
            effectiveness_score=effectiveness,
        )
        persisted += 1

    # 3. 常见弱点 → DomainPattern (category="typical_weakness")
    #    这是声明性知识（WHAT），用 DomainPattern 更合适
    for weak in cognitive_hints.typical_weaknesses:
        memory_store.add_or_reinforce_pattern(
            category="typical_weakness",
            description=weak,
            paper_id=paper_id,
        )
        persisted += 1

    return persisted
