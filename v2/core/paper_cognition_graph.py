"""
core/paper_cognition_graph.py — Paper Cognition Graph (PCG)

V3 Phase 0.5: Agent 对论文的图结构认知模型。

设计依据:
    - GODEL_AGENT_PLAN_V3 §3.1: PCG 继承 PaperStructureIndex 骨架，增加认知层
    - C12: 图认知优先 — 查 PCG 而非重读论文
    - C5: Constrain, don't control — PCG 是认知增强，不是行为强制

继承关系:
    PaperStructureIndex (regex skeleton, <1 sec, zero LLM)
        | enhancement
    PaperCognitionGraph (cognitive layer, LLM fills digest/claims dynamically)

生命周期:
    1. Paper load → PaperIndexBuilder.build() → PaperStructureIndex
    2. from_structure_index() → PCG 初始骨架（零 LLM，<0.5 sec）
    3. DEEP_REVIEW phase → Agent 动态更新 edges/claims/read_depth
    4. mark_complete → PCG 冻结 → 可持久化为 ReviewCognitionGraph

降级策略:
    - GODEL_PCG_ENABLED=0 时，系统回退到 PaperStructureIndex.format_for_context()
    - from_structure_index() 失败时，返回空 PCG（is_empty=True），不阻塞论文加载
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Literal

from core.paper_index import PaperStructureIndex
from core.godel_config import PCG_FORMAT_MAX_TOKENS

logger = logging.getLogger(__name__)


# ==============================================================
# Domain Templates (B2: 领域模板)
# ==============================================================

@dataclass
class DomainTemplate:
    """PCG 领域模板 — 不同论文类型的结构范式和审稿重点依赖。

    用途:
        PCG 初始化时，根据 paper_type 匹配模板，对关键边进行 weight boost，
        帮助 Agent 在 ORIENTATION 阶段更快形成审稿假说。
        这是 C5 精神下的"认知增强"——不控制 Agent 做什么，但让图结构感知更准确。

    降级:
        - paper_type 匹配不到模板 → 静默退出，PCG 不受影响
        - section 名称 fuzzy match 失败 → 跳过该 critical_edge
    """
    paper_type: str
    expected_sections: list[str]                     # fuzzy 匹配目标
    critical_edges: list[tuple[str, str, float]]     # (source_keyword, target_keyword, weight_boost)
    focus_hints: list[str]                           # Agent 应重点关注的 section 组合
    methodology_checklist: list[str] = field(default_factory=list)  # 领域特异方法论审查要点


DOMAIN_TEMPLATES: list[DomainTemplate] = [
    DomainTemplate(
        paper_type="empirical_econ",
        expected_sections=[
            "identification", "data", "results", "robustness", "appendix", "conclusion",
        ],
        critical_edges=[
            ("identification", "results", 0.4),
            ("data", "robustness", 0.3),
            ("identification", "robustness", 0.3),
            ("appendix", "identification", 0.3),
            ("appendix", "robustness", 0.25),
        ],
        focus_hints=[
            "identification → results 是核心依赖链",
            "robustness 验证 identification 有效性",
            "appendix 证明支撑 identification 的数学基础，需审查符号一致性",
        ],
        methodology_checklist=[
            "识别策略关键假设是否有正式统计检验（如平行趋势 formal test, first-stage F-stat）",
            "多重比较：多个 outcome 是否有 Bonferroni/FDR/Romano-Wolf 校正",
            "参数敏感性：calibrated/normalized 参数变动对结论的量化影响",
            "跨表数据一致性：同一统计量在不同表格中值是否一致",
            "模型设定 vs 样本特征：模型人数/结构与数据描述性统计是否匹配",
        ],
    ),
    DomainTemplate(
        paper_type="ml_experiment",
        expected_sections=[
            "method", "experiment", "baseline", "ablation", "results",
        ],
        critical_edges=[
            ("method", "experiment", 0.4),
            ("baseline", "ablation", 0.3),
            ("method", "ablation", 0.3),
        ],
        focus_hints=["method → experiments 是方法有效性的核心链", "ablation 验证各组件贡献"],
        methodology_checklist=[
            "多数据集/多 metric 报告是否有统计显著性检验或 confidence interval",
            "超参数选择：搜索范围、选择标准、对最终结果的敏感度是否报告",
            "计算细节透明度：训练时长、硬件、随机种子、收敛判据",
            "Ablation completeness：每个声称有用的组件是否都有 w/o 对照",
            "Baseline fairness：对比方法是否用了同等调优力度和计算资源",
        ],
    ),
    DomainTemplate(
        paper_type="theoretical",
        expected_sections=[
            "assumption", "proposition", "proof", "theorem", "lemma", "appendix",
        ],
        critical_edges=[
            ("assumption", "proposition", 0.4),
            ("proposition", "proof", 0.4),
            ("lemma", "theorem", 0.3),
            ("appendix", "proof", 0.35),
            ("appendix", "theorem", 0.3),
        ],
        focus_hints=[
            "assumptions → propositions → proofs 是链式高权重",
            "检查假设边界条件",
            "appendix 包含完整证明推导，需逐步验证符号一致性",
        ],
        methodology_checklist=[
            "假设 → 定理的逻辑链中是否有未声明的隐含条件",
            "证明中的符号是否与正文/附录一致（注意下标、上标变化）",
            "关键假设的 tightness：能否构造满足假设但结论恰好成立的极端 case",
            "正则性条件：是否过强以至于排除了有意义的应用场景",
        ],
    ),
    DomainTemplate(
        paper_type="structural_econ",
        expected_sections=[
            "model", "calibration", "results", "counterfactual", "welfare", "appendix",
        ],
        critical_edges=[
            ("model", "calibration", 0.4),
            ("calibration", "results", 0.4),
            ("model", "counterfactual", 0.3),
            ("results", "welfare", 0.3),
            ("appendix", "model", 0.35),
        ],
        focus_hints=[
            "model → calibration → results 是核心逻辑链",
            "检查模型假设在目标经济体的合理性",
            "校准目标选择是否充分覆盖核心参数",
            "appendix 证明与正文模型描述的一致性",
            "理论模型 → 定量模型的过渡是否有结构性脱节",
        ],
        methodology_checklist=[
            "关键简化假设（如 small open economy, representative agent, CES, perfect competition）在目标经济体是否合理——对大国（如美国、中国）该假设是否被讨论",
            "理论模型到定量模型的过渡：是否有桥接推导？关键公式/定理在扩展模型下是否仍成立？符号映射是否显式",
            "校准目标（targeted moments）选择是否合理——calibration justification 是否超过一句话？有无 fit quality 度量、替代值比较",
            "参数敏感性：校准参数变动 ±20-50% 对核心定量结论（如 welfare gains、optimal policy）的影响",
            "数值求解方法细节：grid 精度/步长选择、收敛判据、非网格点最优值处理——是否足够透明以支持复现",
            "符号一致性检查：跨 section 的参数定义（下标 θ₁/θ₂、σ_s 等）是否统一？附录公式符号与正文是否一致",
            "Contribution/novelty 声称 vs 既有文献：'文献空白'声称是否准确？是否有被忽略的 prior work",
            "定性结论的定量支撑：主要经济机制（如 terms of trade effects）的量级是否被报告和讨论",
        ],
    ),
    DomainTemplate(
        paper_type="survey",
        expected_sections=[
            "scope", "coverage", "taxonomy", "gap", "future",
        ],
        critical_edges=[
            ("scope", "coverage", 0.3),
            ("coverage", "gap", 0.4),
            ("gap", "future", 0.3),
        ],
        focus_hints=["coverage → gap analysis 是综述价值的关键", "检查 taxonomy 完整性"],
        methodology_checklist=[
            "覆盖声称 vs 实际覆盖：是否有被遗漏的重要工作线",
            "分类法（taxonomy）是否 MECE（互斥且穷尽）",
            "时间范围：是否声称某时间段全面覆盖但有明显遗漏",
        ],
    ),
]

# 便捷查找
_TEMPLATE_MAP: dict[str, DomainTemplate] = {t.paper_type: t for t in DOMAIN_TEMPLATES}

# Fallback 映射: paper_index 返回的通用类型 → 最接近的模板
# 当精确匹配失败时使用
_TEMPLATE_FALLBACK: dict[str, str] = {
    "empirical": "empirical_econ",  # 通用实证 fallback 到经济学实证模板
}


def get_template_for_paper_type(paper_type: str) -> DomainTemplate | None:
    """获取 paper_type 对应的 DomainTemplate，支持 fallback 映射。"""
    template = _TEMPLATE_MAP.get(paper_type)
    if template is not None:
        return template
    fallback_type = _TEMPLATE_FALLBACK.get(paper_type)
    if fallback_type:
        return _TEMPLATE_MAP.get(fallback_type)
    return None


# ==============================================================
# Data Structures
# ==============================================================

@dataclass
class PCGNode:
    """PCG 节点 = Section 级认知单元。

    分三层:
    - 骨架层（从 PaperStructureIndex 继承，零 LLM）
    - 认知层（Agent 阅读后 LLM 填充）
    - 追踪层（运行时进度标记）
    """

    # --- 骨架层（from_structure_index 填充）---
    section_name: str
    word_count: int = 0
    outgoing_refs: list[str] = field(default_factory=list)
    incoming_refs: list[str] = field(default_factory=list)

    # --- 认知层（Agent 阅读后动态更新）---
    digest: str = ""                    # <=300 char summary
    claims: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)

    # --- 追踪层 ---
    read_depth: Literal["unread", "scanned", "read", "verified"] = "unread"
    findings_linked: list[str] = field(default_factory=list)
    hypotheses_linked: list[str] = field(default_factory=list)

    # --- IntraSession Contrast markers (Phase 1) ---
    contrast_phase: Literal["A", "B", "none"] = "none"
    habits_active_when_read: list[str] = field(default_factory=list)


@dataclass
class PCGEdge:
    """PCG 边 = 逻辑依赖关系（带语义权重）。

    edge_type 枚举:
        REFERENCES   — A 引用了 B（最基础，从 dependency_pairs 继承）
        CLAIM_SUPPORTS — A 的 claim 支持 B 的 claim
        ASSUMPTION_OF — A 依赖 B 的假设
        CONTRADICTS  — A 与 B 存在矛盾/张力
        VALIDATES    — A 的证据验证了 B 的论点
        BUILDS_ON    — A 建立在 B 的结论之上
    """

    source: str
    target: str
    edge_type: str = "REFERENCES"
    weight: float = 1.0         # semantic importance 0.0-1.0
    evidence: str = ""          # brief explanation

    # 运行时演化
    discovered_at_turn: int = 0
    verified: bool = False


# ==============================================================
# Main Class
# ==============================================================

@dataclass
class PaperCognitionGraph:
    """
    Paper Cognition Graph — Agent 对论文的结构化理解模型。

    核心方法:
        from_structure_index() — 从现有 PaperStructureIndex 桥接构建（零 LLM）
        context_for_task()     — 自动组装当前任务相关上下文
        coverage_gaps()        — 哪些 section 还没读/验证
        format_for_zone_a()    — 始终驻留 context 的导航摘要（<= 1500 tokens）
        serialize_for_compaction() — compaction 后恢复认知状态

    运行时更新:
        update_after_read()    — Agent 读完 section 后更新节点
        add_edge()             — 发现新逻辑关系时添加边
        link_finding()         — 将 finding 关联到 section 节点
    """

    nodes: dict[str, PCGNode] = field(default_factory=dict)
    edges: list[PCGEdge] = field(default_factory=list)
    paper_type: str = "unknown"
    _structure_index: PaperStructureIndex | None = field(
        default=None, repr=False
    )

    # ==============================================================
    # Construction
    # ==============================================================

    @classmethod
    def from_structure_index(cls, index: PaperStructureIndex) -> "PaperCognitionGraph":
        """从 PaperStructureIndex 构建初始 PCG。

        这是 V3-to-existing-code 桥接:
        - sections → PCGNode（骨架层填充，认知层留空）
        - dependency_pairs → REFERENCES edges
        - evidence_map → REFERENCES edges（figure/table 引用）

        零 LLM 调用，<0.5 sec。

        失败时返回空 PCG（is_empty=True），不阻塞论文加载。
        """
        try:
            pcg = cls()
            pcg._structure_index = index
            pcg.paper_type = index.paper_type

            # Map sections → nodes
            for section_name in index.sections:
                outgoing = [
                    ref.target_id for ref in index.cross_references
                    if ref.source_section == section_name
                ]
                incoming = [
                    ref.source_section for ref in index.cross_references
                    if ref.target_type == "section"
                    and section_name.lower() in ref.target_id.lower()
                ]
                pcg.nodes[section_name] = PCGNode(
                    section_name=section_name,
                    word_count=index.section_word_counts.get(section_name, 0),
                    outgoing_refs=outgoing,
                    incoming_refs=incoming,
                )

            # Map dependency_pairs → edges
            for source, target in index.dependency_pairs:
                pcg.edges.append(PCGEdge(
                    source=source,
                    target=target,
                    edge_type="REFERENCES",
                    weight=0.5,
                ))

            # Map evidence_map → edges
            for evidence_id, citing_sections in index.evidence_map.items():
                for section in citing_sections:
                    pcg.edges.append(PCGEdge(
                        source=section,
                        target=evidence_id,
                        edge_type="REFERENCES",
                        weight=0.7,
                    ))

            # B2: 应用领域模板（对关键边进行 weight boost）
            pcg._apply_domain_template(pcg.paper_type)

            logger.info(
                "[PCG] Built from PaperStructureIndex: %d nodes, %d edges, type=%s",
                len(pcg.nodes), len(pcg.edges), pcg.paper_type,
            )
            return pcg

        except Exception as e:
            logger.warning("[PCG] from_structure_index failed: %s. Returning empty PCG.", e)
            return cls()

    def is_empty(self) -> bool:
        """PCG 是否为空（构建失败或论文无可解析结构）。"""
        return len(self.nodes) == 0

    # ==============================================================
    # Context Assembly (Zone B decisions)
    # ==============================================================

    def context_for_task(self, task_section: str, max_tokens: int = 3000) -> str:
        """自动组装当前任务相关上下文。

        策略（贪心填充，在 max_tokens 内）:
        1. 当前 section 的 digest + claims + assumptions
        2. 通过边关联的 section 的 digest（按 weight 降序）
        3. 关联的 findings/hypotheses 概要

        Args:
            task_section: 当前正在处理的 section 名称
            max_tokens: token 上限（粗估 1 token ≈ 4 chars）

        Returns:
            格式化的上下文文本，空字符串表示无可用信息
        """
        if self.is_empty() or task_section not in self.nodes:
            return ""

        max_chars = max_tokens * 4  # 粗估
        parts: list[str] = []
        used_chars = 0

        # 1. 当前 section 信息
        node = self.nodes[task_section]
        section_info = self._format_node_detail(node)
        if section_info:
            parts.append(f"[当前 section: {task_section}]")
            parts.append(section_info)
            used_chars += len("\n".join(parts))

        # 2. 关联 sections（通过边，按 weight 排序）
        related = self._get_related_sections(task_section)
        if related:
            parts.append("\n[相关 sections]")
            for rel_name, rel_weight, edge_type in related:
                if rel_name not in self.nodes:
                    continue
                rel_node = self.nodes[rel_name]
                rel_info = f"  {rel_name} ({edge_type}, w={rel_weight:.1f})"
                if rel_node.digest:
                    rel_info += f": {rel_node.digest[:150]}"
                if used_chars + len(rel_info) > max_chars:
                    break
                parts.append(rel_info)
                used_chars += len(rel_info)

        # 3. Findings/hypotheses 概要
        if node.findings_linked:
            findings_line = f"\n[关联 findings]: {', '.join(node.findings_linked[:5])}"
            if used_chars + len(findings_line) <= max_chars:
                parts.append(findings_line)

        return "\n".join(parts) if parts else ""

    def coverage_gaps(self) -> dict[str, list[str]]:
        """返回认知覆盖缺口。

        Returns:
            {
                "unread": [未阅读的 sections],
                "unverified_claims": [有 claims 但未 verified 的 sections],
                "orphan_findings": [findings 未关联到任何 claim 的 sections],
            }
        """
        unread = []
        unverified_claims = []
        orphan_findings = []

        for name, node in self.nodes.items():
            if node.read_depth == "unread":
                unread.append(name)
            elif node.claims and node.read_depth != "verified":
                unverified_claims.append(name)
            if node.findings_linked and not node.claims:
                orphan_findings.append(name)

        return {
            "unread": unread,
            "unverified_claims": unverified_claims,
            "orphan_findings": orphan_findings,
        }

    # ==============================================================
    # Zone A Formatting (always-resident in context)
    # ==============================================================

    def format_for_zone_a(self, max_tokens: int | None = None) -> str:
        """格式化为 Zone A 导航摘要（始终驻留在 context 中）。

        紧凑图概览:
        - 所有 section 名称 + read_depth 标记（~200 tokens）
        - 核心边（weight > 0.7）描述（~300 tokens）
        - Findings/hypotheses 分布热力图（~200 tokens）
        - coverage_gaps 概要（~100 tokens）

        总量控制在 <=1500 tokens。

        Args:
            max_tokens: 输出 token 上限，默认使用 PCG_FORMAT_MAX_TOKENS

        Returns:
            格式化文本，空字符串表示 PCG 为空
        """
        if self.is_empty():
            return ""

        if max_tokens is None:
            max_tokens = PCG_FORMAT_MAX_TOKENS

        max_chars = max_tokens * 4
        lines: list[str] = []

        # Header
        lines.append(
            f"[PCG 导航] type={self.paper_type} | "
            f"sections={len(self.nodes)} | edges={len(self.edges)}"
        )

        # Section overview with read_depth markers
        depth_symbols = {"unread": "○", "scanned": "◐", "read": "●", "verified": "✓"}
        lines.append("\n结构概览:")
        for name, node in self.nodes.items():
            symbol = depth_symbols.get(node.read_depth, "?")
            findings_count = len(node.findings_linked)
            extras = []
            if findings_count > 0:
                extras.append(f"F:{findings_count}")
            if node.claims:
                extras.append(f"C:{len(node.claims)}")
            extra_str = f" [{', '.join(extras)}]" if extras else ""
            lines.append(f"  {symbol} {name} ({node.word_count}w){extra_str}")

        # Core edges (weight > 0.7)
        high_edges = [e for e in self.edges if e.weight > 0.7]
        if high_edges:
            lines.append("\n核心关联:")
            for edge in sorted(high_edges, key=lambda e: -e.weight)[:8]:
                verified_mark = " ✓" if edge.verified else ""
                lines.append(
                    f"  {edge.source} → {edge.target} "
                    f"({edge.edge_type}, w={edge.weight:.1f}){verified_mark}"
                )

        # Methodology checklist (domain-specific review anchors)
        template = get_template_for_paper_type(self.paper_type)
        if template and template.methodology_checklist:
            lines.append("\n方法论审查锚点:")
            for item in template.methodology_checklist:
                lines.append(f"  · {item}")

        # Coverage gaps summary
        gaps = self.coverage_gaps()
        gap_parts = []
        if gaps["unread"]:
            gap_parts.append(f"未读:{len(gaps['unread'])}")
        if gaps["unverified_claims"]:
            gap_parts.append(f"未验证:{len(gaps['unverified_claims'])}")
        if gaps["orphan_findings"]:
            gap_parts.append(f"孤立finding:{len(gaps['orphan_findings'])}")
        if gap_parts:
            lines.append(f"\n覆盖缺口: {' | '.join(gap_parts)}")

        result = "\n".join(lines)

        # Truncate if exceeds budget
        if len(result) > max_chars:
            result = result[:max_chars - 20] + "\n  ... [已截断]"

        return result

    # ==============================================================
    # Serialization (for compaction recovery)
    # ==============================================================

    def serialize_for_compaction(self) -> str:
        """序列化用于 compaction 恢复。

        设计要点:
        - 不保存完整 digest（太大），只保存 claims + edges
        - 保存 read_depth 状态（Agent 知道读过什么）
        - 保存 findings/hypotheses 链接
        - 目标: < 2000 tokens 恢复全局论文理解
        """
        if self.is_empty():
            return ""

        data: dict[str, Any] = {
            "paper_type": self.paper_type,
            "nodes": {},
            "edges": [],
        }

        for name, node in self.nodes.items():
            node_data: dict = {"rd": node.read_depth}
            if node.claims:
                node_data["cl"] = node.claims
            if node.findings_linked:
                node_data["fl"] = node.findings_linked
            if node.hypotheses_linked:
                node_data["hl"] = node.hypotheses_linked
            if node.contrast_phase != "none":
                node_data["cp"] = node.contrast_phase
            data["nodes"][name] = node_data

        for edge in self.edges:
            if edge.weight >= 0.5 or edge.verified:
                data["edges"].append({
                    "s": edge.source,
                    "t": edge.target,
                    "tp": edge.edge_type,
                    "w": round(edge.weight, 2),
                    "v": edge.verified,
                })

        return json.dumps(data, ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def restore_from_compaction(
        cls, serialized: str, index: PaperStructureIndex | None = None
    ) -> "PaperCognitionGraph":
        """从 compaction 序列化数据恢复 PCG。

        先用 from_structure_index 重建骨架，再覆盖认知层状态。
        """
        if not serialized:
            return cls()

        try:
            data = json.loads(serialized)
        except (json.JSONDecodeError, TypeError):
            logger.warning("[PCG] restore_from_compaction: invalid JSON")
            return cls()

        # 如有 index，先重建骨架
        if index is not None:
            pcg = cls.from_structure_index(index)
        else:
            pcg = cls()
            pcg.paper_type = data.get("paper_type", "unknown")

        # 覆盖节点认知层
        for name, node_data in data.get("nodes", {}).items():
            if name in pcg.nodes:
                node = pcg.nodes[name]
                node.read_depth = node_data.get("rd", "unread")
                node.claims = node_data.get("cl", [])
                node.findings_linked = node_data.get("fl", [])
                node.hypotheses_linked = node_data.get("hl", [])
                node.contrast_phase = node_data.get("cp", "none")
            else:
                # index 中不存在的 section（可能是结构变化），跳过
                pass

        # 恢复边（仅覆盖高权重/已验证的）
        restored_edges = []
        for edge_data in data.get("edges", []):
            restored_edges.append(PCGEdge(
                source=edge_data.get("s", ""),
                target=edge_data.get("t", ""),
                edge_type=edge_data.get("tp", "REFERENCES"),
                weight=edge_data.get("w", 0.5),
                verified=edge_data.get("v", False),
            ))

        # 合并: 保留 from_structure_index 的骨架边，添加恢复的认知边
        existing_edge_keys = {(e.source, e.target, e.edge_type) for e in pcg.edges}
        for edge in restored_edges:
            key = (edge.source, edge.target, edge.edge_type)
            if key not in existing_edge_keys:
                pcg.edges.append(edge)

        logger.info(
            "[PCG] Restored from compaction: %d nodes, %d edges",
            len(pcg.nodes), len(pcg.edges),
        )
        return pcg

    # ==============================================================
    # Runtime Update Interfaces (Harness/Tool handlers call)
    # ==============================================================

    def update_after_read(
        self,
        section: str,
        digest: str = "",
        claims: list[str] | None = None,
        assumptions: list[str] | None = None,
        read_depth: Literal["scanned", "read", "verified"] = "read",
    ) -> None:
        """Agent 读完 section 后更新节点。

        由 tool_handlers/reading.py 调用。
        """
        if section not in self.nodes:
            logger.debug("[PCG] update_after_read: unknown section '%s', skipping", section)
            return

        node = self.nodes[section]
        if digest:
            node.digest = digest[:300]  # 硬限 300 chars
        if claims is not None:
            node.claims = claims
        if assumptions is not None:
            node.assumptions = assumptions
        node.read_depth = read_depth

        logger.debug(
            "[PCG] Updated node '%s': depth=%s, claims=%d",
            section, read_depth, len(node.claims),
        )

    def add_edge(
        self,
        source: str,
        target: str,
        edge_type: str,
        weight: float = 0.5,
        evidence: str = "",
        turn: int = 0,
    ) -> None:
        """Agent 发现新逻辑关系时添加边。

        去重: 相同 (source, target, edge_type) 不重复添加，而是更新 weight。
        """
        # 检查是否已存在
        for existing in self.edges:
            if (existing.source == source
                    and existing.target == target
                    and existing.edge_type == edge_type):
                # 更新权重（取更高值）
                existing.weight = max(existing.weight, weight)
                if evidence:
                    existing.evidence = evidence
                return

        self.edges.append(PCGEdge(
            source=source,
            target=target,
            edge_type=edge_type,
            weight=weight,
            evidence=evidence,
            discovered_at_turn=turn,
        ))

    def link_finding(self, finding_id: str, section: str) -> None:
        """将 finding 关联到对应 section 节点。"""
        if section in self.nodes:
            node = self.nodes[section]
            if finding_id not in node.findings_linked:
                node.findings_linked.append(finding_id)

    def link_hypothesis(self, hypothesis_id: str, section: str) -> None:
        """将 hypothesis 关联到对应 section 节点。"""
        if section in self.nodes:
            node = self.nodes[section]
            if hypothesis_id not in node.hypotheses_linked:
                node.hypotheses_linked.append(hypothesis_id)

    def mark_verified(self, source: str, target: str, edge_type: str) -> None:
        """标记边为已验证。"""
        for edge in self.edges:
            if (edge.source == source
                    and edge.target == target
                    and edge.edge_type == edge_type):
                edge.verified = True
                return

    # ==============================================================
    # Domain Template Application (B2)
    # ==============================================================

    def _apply_domain_template(self, paper_type: str) -> None:
        """根据 paper_type 应用领域模板，boost 关键边的权重。

        策略:
        - 从 _TEMPLATE_MAP 查找匹配模板
        - 对模板中每条 critical_edge，fuzzy match 当前 PCG 中的 section 名称
        - 匹配成功 → 找到对应边并 boost weight（加固定增量，cap 在 1.0）
        - 匹配失败 → 静默跳过，不影响原有 PCG

        降级: paper_type 不匹配任何模板时静默退出。
        """
        template = get_template_for_paper_type(paper_type)
        if template is None:
            logger.debug("[PCG] No domain template for paper_type='%s'", paper_type)
            return

        section_names = list(self.nodes.keys())
        boosted = 0

        for source_kw, target_kw, weight_boost in template.critical_edges:
            source_match = self._fuzzy_match_section(source_kw, section_names)
            target_match = self._fuzzy_match_section(target_kw, section_names)

            if source_match is None or target_match is None:
                continue

            # 查找已有边并 boost，或创建新边
            edge_found = False
            for edge in self.edges:
                if edge.source == source_match and edge.target == target_match:
                    edge.weight = min(1.0, edge.weight + weight_boost)
                    edge_found = True
                    boosted += 1
                    break

            if not edge_found:
                # 创建新的 domain-inferred edge
                self.edges.append(PCGEdge(
                    source=source_match,
                    target=target_match,
                    edge_type="REFERENCES",
                    weight=min(1.0, 0.5 + weight_boost),
                    evidence=f"domain_template:{paper_type}",
                ))
                boosted += 1

        if boosted > 0:
            logger.debug(
                "[PCG] Applied domain template '%s': %d edges boosted/created",
                paper_type, boosted,
            )

    @staticmethod
    def _fuzzy_match_section(keyword: str, section_names: list[str]) -> str | None:
        """Fuzzy match a keyword against available section names.

        策略（按优先级）:
        1. 精确匹配（case-insensitive）
        2. 关键词包含在 section name 中（case-insensitive）
        3. section name 包含关键词的前 4 个字符（短关键词容错）

        Returns:
            匹配到的 section 名称，或 None
        """
        kw_lower = keyword.lower()

        # 1. 精确匹配
        for name in section_names:
            if name.lower() == kw_lower:
                return name

        # 2. 关键词包含在 section name 中
        for name in section_names:
            if kw_lower in name.lower():
                return name

        # 3. section name 包含关键词前 4 字符（对 "identification" → "ident" 类）
        if len(kw_lower) >= 4:
            prefix = kw_lower[:4]
            for name in section_names:
                if prefix in name.lower():
                    return name

        return None

    # ==============================================================
    # Internal Helpers
    # ==============================================================

    def _format_node_detail(self, node: PCGNode) -> str:
        """格式化单个节点的详细信息。"""
        parts = []
        if node.digest:
            parts.append(f"摘要: {node.digest}")
        if node.claims:
            parts.append(f"核心论点: {'; '.join(node.claims[:3])}")
        if node.assumptions:
            parts.append(f"前置假设: {'; '.join(node.assumptions[:3])}")
        parts.append(f"阅读深度: {node.read_depth} | 字数: {node.word_count}")
        return "\n".join(parts)

    def _get_related_sections(self, section: str) -> list[tuple[str, float, str]]:
        """获取与指定 section 相关的其他 sections（按 weight 降序）。

        Returns:
            [(section_name, weight, edge_type), ...]
        """
        related: list[tuple[str, float, str]] = []
        seen = set()

        for edge in self.edges:
            if edge.source == section and edge.target in self.nodes:
                key = edge.target
                if key not in seen:
                    related.append((edge.target, edge.weight, edge.edge_type))
                    seen.add(key)
            elif edge.target == section and edge.source in self.nodes:
                key = edge.source
                if key not in seen:
                    related.append((edge.source, edge.weight, edge.edge_type))
                    seen.add(key)

        # 按 weight 降序
        related.sort(key=lambda x: -x[1])
        return related

    def section_count(self) -> int:
        """返回 section 数量。"""
        return len(self.nodes)

    def get_node(self, section: str) -> PCGNode | None:
        """获取指定 section 的节点。"""
        return self.nodes.get(section)
