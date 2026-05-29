"""
core/v2/paper_index.py — Paper Structure Pre-indexing (B1)

设计依据:
    - UPGRADE_PLAN_FINAL §B1: "人类审稿人拿到论文先翻一遍，建立心智模型"
    - COGNITIVE_ANCHOR §4.3: 认知辅助模式（参考，非事实）
    - 蓝图 §〇.5: 战略性阅读——Agent 只读 7/42 核心 sections

核心思想:
    - 在论文加载时用纯正则 (<1秒) 构建结构索引
    - 帮助 Agent 做出更好的阅读顺序决策
    - 所有产出标注为"参考"——Agent 有最终决策权

与现有系统的关系:
    - _load_paper 之后自动调用 PaperIndexBuilder.build()
    - 结果存入 WorkspaceState.paper_structure_index
    - ContextAssembler 在 INITIAL_SCAN 阶段注入完整索引
    - DEEP_REVIEW 阶段只注入当前 section 的相关子集
"""

from __future__ import annotations

import re
import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# ============================================================
# Data Structures
# ============================================================

@dataclass
class CrossReference:
    """论文内部的一个交叉引用。"""
    source_section: str       # 引用发起的 section
    target_type: str          # "figure" | "table" | "equation" | "section"
    target_id: str            # "Figure 3a" | "Table 1" | "Eq. 5" | "Section 3.2"
    context_snippet: str      # 引用所在句子（≤60字符）


@dataclass
class PaperStructureIndex:
    """
    论文预索引——Agent 的论文心智模型。

    纯数据结构，由 PaperIndexBuilder 构建，
    由 ContextAssembler 格式化后注入 Agent 的 context。
    """

    # 骨架
    sections: list = field(default_factory=list)               # 有序 section 标题
    section_word_counts: dict = field(default_factory=dict)    # 各 section 体量

    # 内部引用网络
    cross_references: list = field(default_factory=list)       # List[CrossReference]

    # 快捷视图
    evidence_map: dict = field(default_factory=dict)           # figure/table → 引用它的 sections
    dependency_pairs: list = field(default_factory=list)       # (A, B) = "A 引用了 Section B"

    # 论文类型推断
    paper_type: str = "unknown"  # "empirical" | "theoretical" | "review" | "unknown"

    def is_empty(self) -> bool:
        """索引是否为空（论文未加载或无可解析结构）。"""
        return len(self.sections) == 0

    def get_reading_priority(self) -> list:
        """基于引用密度推荐阅读优先级。被引用最多的 section 应该先读。"""
        ref_counts: Counter = Counter()
        for ref in self.cross_references:
            if ref.target_type == "section":
                ref_counts[ref.target_id] += 1
        return [s for s, _ in ref_counts.most_common()]

    def get_evidence_chain(self, claim_section: str) -> list:
        """给定一个声称结果的 section，找出它依赖的所有证据。"""
        return [
            ref.target_id for ref in self.cross_references
            if ref.source_section == claim_section
            and ref.target_type in ("figure", "table")
        ]

    def format_for_context(self, max_sections: int = 20) -> str:
        """
        格式化为注入 context 的文本。

        措辞要求（COGNITIVE_ANCHOR §4.3）:
        - 始终是"参考"，不是"事实"
        - 明确标注可能有噪音
        """
        if self.is_empty():
            return ""

        lines = [
            "[以下为自动解析结果，仅供导航参考，可能存在噪音]",
            f"论文类型: {self.paper_type} | "
            f"Sections: {len(self.sections)} | "
            f"Figures: {self._count_type('figure')} | "
            f"Tables: {self._count_type('table')}",
        ]

        # 结构概览
        lines.append("\n结构:")
        for sec in self.sections[:max_sections]:
            wc = self.section_word_counts.get(sec, 0)
            refs = [
                r.target_id for r in self.cross_references
                if r.source_section == sec
            ]
            ref_str = f" → 引用 {', '.join(refs[:4])}" if refs else ""
            if len(refs) > 4:
                ref_str += f" +{len(refs)-4}"
            lines.append(f"  {sec} ({wc}w){ref_str}")

        # 证据映射（最常被引用的 figure/table）
        if self.evidence_map:
            lines.append("\n核心证据使用:")
            sorted_evidence = sorted(
                self.evidence_map.items(),
                key=lambda x: -len(x[1]),
            )
            for target, sources in sorted_evidence[:5]:
                unique_sources = list(dict.fromkeys(sources))  # 去重保序
                lines.append(f"  {target} ← 被 {', '.join(unique_sources[:4])} 引用")

        # 阅读建议（参考性，非指令）
        priority = self.get_reading_priority()
        if priority:
            lines.append(
                f"\n[参考] 被其他 section 引用最多: {', '.join(priority[:3])}"
            )

        return "\n".join(lines)

    def format_subset_for_section(self, current_section: str) -> str:
        """
        为当前正在审读的 section 提供相关子集信息。

        在 DEEP_REVIEW 阶段使用——只注入与当前 section 相关的交叉引用。
        """
        # 该 section 引用了什么
        outgoing = [
            ref for ref in self.cross_references
            if ref.source_section == current_section
        ]
        # 谁引用了该 section
        incoming = [
            ref for ref in self.cross_references
            if ref.target_type == "section"
            and current_section.lower() in ref.target_id.lower()
        ]

        if not outgoing and not incoming:
            return ""

        lines = [f"[当前 section '{current_section}' 的结构关联]"]

        if outgoing:
            targets = [f"{r.target_id}({r.target_type})" for r in outgoing[:6]]
            lines.append(f"  引用了: {', '.join(targets)}")

        if incoming:
            sources = list(dict.fromkeys(r.source_section for r in incoming))[:4]
            lines.append(f"  被引用于: {', '.join(sources)}")

        return "\n".join(lines)

    def _count_type(self, target_type: str) -> int:
        """计算某类引用的不同目标数量。"""
        targets = {
            ref.target_id for ref in self.cross_references
            if ref.target_type == target_type
        }
        return len(targets)


# ============================================================
# Builder
# ============================================================

class PaperIndexBuilder:
    """
    从论文 sections 文本构建 PaperStructureIndex。

    纯正则解析，< 1秒完成，零 LLM 成本。
    """

    # 正则模式
    PATTERNS: dict = {
        "figure": [
            r'[Ff]ig(?:ure)?\.?\s*(\d+[a-z]?)',
        ],
        "table": [
            r'[Tt]able\s+(\d+[a-z]?)',
        ],
        "equation": [
            r'[Ee]q(?:uation)?\.?\s*[(\[]?(\d+)[)\]]?',
        ],
        "section": [
            r'[Ss]ection\s+(\d+(?:\.\d+)*)',
            r'[Ss]ec\.?\s*(\d+(?:\.\d+)*)',
            r'§\s*(\d+(?:\.\d+)*)',
        ],
    }

    def build(self, sections: dict) -> PaperStructureIndex:
        """
        从已解析的论文 sections 构建索引。

        Args:
            sections: paper_sections dict（key=section名, value=文本）
                      "full" key 会被跳过（它是全文副本）

        Returns:
            PaperStructureIndex 实例
        """
        # 过滤掉 "full" key
        working_sections = {
            k: v for k, v in sections.items() if k != "full"
        }

        if not working_sections:
            return PaperStructureIndex()

        # 提取交叉引用
        cross_refs = self._extract_cross_references(working_sections)

        # 构建证据映射
        evidence_map: dict = defaultdict(list)
        for ref in cross_refs:
            if ref.target_type in ("figure", "table"):
                evidence_map[ref.target_id].append(ref.source_section)

        # 推断论文类型
        paper_type = self._detect_paper_type(working_sections)

        # 推断 section 间依赖
        dependency_pairs = self._infer_dependencies(cross_refs)

        return PaperStructureIndex(
            sections=list(working_sections.keys()),
            section_word_counts={
                k: len(v.split()) for k, v in working_sections.items()
            },
            cross_references=cross_refs,
            evidence_map=dict(evidence_map),
            dependency_pairs=dependency_pairs,
            paper_type=paper_type,
        )

    def _extract_cross_references(
        self, sections: dict
    ) -> list:
        """从所有 sections 中提取交叉引用。"""
        cross_refs = []
        for sec_name, sec_text in sections.items():
            for ref_type, patterns in self.PATTERNS.items():
                for pattern in patterns:
                    for match in re.finditer(pattern, sec_text):
                        target_id = self._normalize_target(
                            ref_type, match.group(1)
                        )
                        context = self._extract_context(
                            sec_text, match.start(), max_len=60
                        )
                        cross_refs.append(CrossReference(
                            source_section=sec_name,
                            target_type=ref_type,
                            target_id=target_id,
                            context_snippet=context,
                        ))
        return cross_refs

    def _detect_paper_type(self, sections: dict) -> str:
        """启发式论文类型判断（含子领域检测）。

        返回值与 DomainTemplate.paper_type 对齐:
            - "empirical_econ": 计量经济学/reduced-form 实证
            - "structural_econ": 结构经济学模型（CGE/DSGE/贸易/产业组织）
            - "ml_experiment": ML/DL/NLP 实验论文
            - "theoretical": 纯理论（定理-证明体）
            - "survey": 综述
            - "clinical": 临床/流行病学
            - "empirical": 其他实证论文（fallback）
            - "unknown": 无法判断
        """
        names_lower = [s.lower() for s in sections]
        # 拼接全部 section 内容（每 section 取前 1000 字符，确保关键信号不被截断）
        content_sample = " ".join(
            v[:1000] for v in sections.values() if isinstance(v, str)
        ).lower()

        # --- Section 名称信号 ---
        has_experiment = any(
            k in n for n in names_lower
            for k in ("experiment", "result", "data", "empirical", "estimation")
        )
        has_theory = any(
            k in n for n in names_lower
            for k in ("theorem", "proof", "lemma", "proposition")
        )
        has_method = any(
            k in n for n in names_lower
            for k in ("method", "model", "identification", "approach")
        )
        has_calibration = any(
            k in n for n in names_lower
            for k in ("calibrat", "counterfactual", "welfare", "simulation", "quantitative")
        )

        # --- 内容关键词信号 ---
        # 结构经济学
        structural_econ_signals = [
            "calibrat", "general equilibrium", "welfare", "counterfactual",
            "steady state", "armington", "ces ", "trade model", "optimal tariff",
            "pareto distribution", "iceberg", "gravity model", "dsge",
            "computable general", "structural estimation",
        ]
        structural_score = sum(1 for kw in structural_econ_signals if kw in content_sample)

        # 计量经济学 (reduced-form)
        empirical_econ_signals = [
            "identification", "instrument", "diff-in-diff", "difference-in-difference",
            "regression discontinuity", "propensity score", "treatment effect",
            "causal", "endogen", "two-stage", "panel data", "fixed effect",
        ]
        empirical_econ_score = sum(1 for kw in empirical_econ_signals if kw in content_sample)

        # ML/DL
        ml_signals = [
            "neural network", "transformer", "attention", "fine-tun", "pre-train",
            "deep learning", "convolutional", "recurrent", "bert", "gpt",
            "training", "epoch", "batch size", "gradient",
        ]
        ml_score = sum(1 for kw in ml_signals if kw in content_sample)

        # 临床/流行病学
        clinical_signals = [
            "patient", "clinical trial", "randomized controlled", "placebo",
            "hazard ratio", "survival", "cohort", "odds ratio",
        ]
        clinical_score = sum(1 for kw in clinical_signals if kw in content_sample)

        # --- 决策逻辑 ---
        # 结构经济学（校准/反事实 + 结构信号强）
        # 优先于 theoretical 判断：有 calibration section 名称信号时即便有理论也优先判为 structural
        if structural_score >= 3 or (has_calibration and structural_score >= 1):
            return "structural_econ"

        # 纯理论（有定理证明 + 无实证数据 + 无校准）
        if has_theory and not has_experiment and not has_calibration:
            return "theoretical"

        # ML 实验
        if ml_score >= 3:
            return "ml_experiment"

        # 临床
        if clinical_score >= 3:
            return "clinical"

        # 计量经济学 (reduced-form)
        if empirical_econ_score >= 3:
            return "empirical_econ"

        # 带理论的经济学（有定理+有实证/校准）
        if has_theory and (has_experiment or has_calibration) and structural_score >= 1:
            return "structural_econ"

        # Fallback: 通用实证
        if has_experiment or has_method:
            return "empirical"

        if len(sections) > 25:
            return "survey"
        return "unknown"

    def _infer_dependencies(self, refs: list) -> list:
        """推断 section 间的逻辑依赖。A 引用 Section B → (A, B)。"""
        pairs = set()
        for ref in refs:
            if ref.target_type == "section":
                pairs.add((ref.source_section, ref.target_id))
        return list(pairs)

    @staticmethod
    def _normalize_target(ref_type: str, raw_id: str) -> str:
        """标准化引用目标名称。"""
        type_label = {
            "figure": "Figure",
            "table": "Table",
            "equation": "Eq.",
            "section": "Section",
        }
        return f"{type_label.get(ref_type, ref_type)} {raw_id}"

    @staticmethod
    def _extract_context(text: str, match_start: int, max_len: int = 60) -> str:
        """提取引用所在的上下文片段。"""
        # 向前找句子开头或最多 30 字符
        start = max(0, match_start - 30)
        # 向后找句子结尾或最多 30 字符
        end = min(len(text), match_start + 30)

        snippet = text[start:end].strip()
        # 清理换行
        snippet = snippet.replace("\n", " ")

        if len(snippet) > max_len:
            snippet = snippet[:max_len] + "..."
        return snippet
