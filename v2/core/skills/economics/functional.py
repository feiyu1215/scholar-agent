"""
core/skills/economics/functional.py — 经济学 Functional Skills

从现有审稿 Phase 行为中抽象出的 4 个核心 Functional Skills：
  1. MethodologyAnalysisSkill: 研究方法论审查
  2. StatisticalValidationSkill: 统计方法与结果验证
  3. CitationVerificationSkill: 引用一致性与文献覆盖检查
  4. LogicCoherenceSkill: 论证逻辑连贯性检查

每个 Skill 可独立测试，不依赖完整 harness。
"""

from __future__ import annotations

import re
from typing import Optional

from core.skills.base import (
    Finding,
    Skill,
    SkillContext,
    SkillDescriptor,
    SkillLevel,
    SkillResult,
)


# ==============================================================
# 1. 方法论分析
# ==============================================================

class MethodologyAnalysisSkill(Skill):
    """审查经济学论文的研究方法论。

    检查重点：
      - 识别策略（DID / IV / RDD / Matching / Panel FE 等）
      - 核心假设是否显式陈述和验证
      - 内生性讨论是否充分
      - 样本选择偏误是否被处理
      - 稳健性检验是否完备
    """

    _DESCRIPTOR = SkillDescriptor(
        name="methodology_analysis",
        level=SkillLevel.FUNCTIONAL,
        description="审查研究方法论：识别策略合理性、核心假设验证、内生性讨论",
        prerequisites=(),
        input_schema={"paper_text": "str", "paper_metadata": "dict"},
        output_schema={"findings": "list[Finding]"},
        applicable_phases=("deep_review",),
        tags=("economics", "methodology", "causal_inference"),
        token_cost_estimate=800,
        version="1.0",
    )

    # 经济学常见识别策略关键词
    _IDENTIFICATION_KEYWORDS = {
        "did": ["difference-in-difference", "did", "双重差分", "diff-in-diff"],
        "iv": ["instrumental variable", "iv", "工具变量", "2sls", "two-stage"],
        "rdd": ["regression discontinuity", "rdd", "断点回归"],
        "matching": ["propensity score", "psm", "matching", "倾向得分"],
        "panel_fe": ["fixed effect", "panel", "固定效应", "面板"],
        "synth": ["synthetic control", "合成控制"],
        "bunching": ["bunching", "density discontinuity"],
    }

    # 常见方法论弱点指示词
    _WEAKNESS_INDICATORS = [
        "endogeneity",
        "selection bias",
        "omitted variable",
        "reverse causality",
        "measurement error",
        "内生性",
        "遗漏变量",
        "反向因果",
    ]

    @property
    def descriptor(self) -> SkillDescriptor:
        return self._DESCRIPTOR

    def can_apply(self, context: SkillContext) -> float:
        """根据论文是否含实证方法判断适用度。"""
        text_lower = context.paper_text.lower()
        metadata = context.paper_metadata

        score = 0.0

        # 论文类型加分
        paper_type = metadata.get("paper_type", "").lower()
        if paper_type in ("empirical", "实证"):
            score += 0.5

        # 识别策略关键词命中
        for strategy, keywords in self._IDENTIFICATION_KEYWORDS.items():
            if any(kw in text_lower for kw in keywords):
                score += 0.2
                break

        # 方法论讨论存在性
        method_sections = ["methodology", "method", "identification", "empirical strategy",
                          "研究方法", "识别策略", "实证策略"]
        if any(sec in text_lower for sec in method_sections):
            score += 0.2

        # Phase 匹配加分
        if context.current_phase.lower() == "deep_review":
            score += 0.1

        return min(score, 1.0)

    def execute(self, context: SkillContext) -> SkillResult:
        """执行方法论分析。

        当前实现：基于规则的静态分析（关键词检测 + 模式匹配）。
        未来升级：LLM 深度推理（在 SkillExecutor 层面注入 LLM 调用）。
        """
        findings: list[Finding] = []
        text = context.paper_text
        text_lower = text.lower()

        # 1. 识别使用的方法
        identified_methods = self._detect_methods(text_lower)

        # 2. 检查核心假设是否被讨论
        if identified_methods:
            for method in identified_methods:
                assumption_check = self._check_assumption_discussion(
                    method, text_lower
                )
                if assumption_check:
                    findings.append(assumption_check)

        # 3. 检查内生性讨论
        endogeneity_finding = self._check_endogeneity_discussion(text_lower)
        if endogeneity_finding:
            findings.append(endogeneity_finding)

        # 4. 检查稳健性检验
        robustness_finding = self._check_robustness(text_lower)
        if robustness_finding:
            findings.append(robustness_finding)

        # 标记来源
        for f in findings:
            f.skill_source = self.descriptor.name

        return SkillResult(
            findings=findings,
            output_data={"identified_methods": identified_methods},
            success=True,
        )

    def get_instruction(self) -> str:
        """完整 SOP（Layer 2）。"""
        return (
            "# 方法论分析技能 (Methodology Analysis)\n\n"
            "## 目标\n"
            "系统审查论文的因果识别策略及其有效性。\n\n"
            "## 检查清单\n"
            "1. 识别策略类型：DID / IV / RDD / Matching / Panel FE / 其他\n"
            "2. 核心假设：\n"
            "   - DID: 平行趋势假设是否验证（event study图 / placebo test）\n"
            "   - IV: 排他性约束和相关性是否论证（first-stage F > 10）\n"
            "   - RDD: 操纵检验（McCrary test）、带宽敏感性\n"
            "3. 内生性讨论：遗漏变量、反向因果、测量误差是否被正面回应\n"
            "4. 稳健性检验：替代度量、子样本、不同规格\n"
            "5. 外部有效性：样本代表性讨论\n\n"
            "## 评级标准\n"
            "- critical: 核心假设未验证或明显违反\n"
            "- major: 重要方面缺失但不致命\n"
            "- minor: 表述不充分但方法基本正确\n"
        )

    # --- 内部方法 ---

    def _detect_methods(self, text_lower: str) -> list[str]:
        """检测论文使用的识别策略。"""
        methods = []
        for method, keywords in self._IDENTIFICATION_KEYWORDS.items():
            if any(kw in text_lower for kw in keywords):
                methods.append(method)
        return methods

    def _check_assumption_discussion(
        self, method: str, text_lower: str
    ) -> Optional[Finding]:
        """检查特定方法的核心假设是否被讨论。"""
        assumption_keywords = {
            "did": ["parallel trend", "平行趋势", "pre-trend", "event study"],
            "iv": ["exclusion restriction", "排他性", "first stage", "relevance"],
            "rdd": ["manipulation", "mccrary", "bandwidth", "操纵检验"],
            "matching": ["balance", "common support", "平衡性"],
        }

        keywords = assumption_keywords.get(method, [])
        if not keywords:
            return None

        if not any(kw in text_lower for kw in keywords):
            return Finding(
                category="methodology",
                severity="major",
                description=f"使用 {method.upper()} 方法但未充分讨论核心假设验证",
                evidence=f"未找到以下关键讨论：{keywords}",
                suggestion=f"建议补充 {method.upper()} 核心假设的验证分析",
                confidence=0.7,
            )
        return None

    def _check_endogeneity_discussion(self, text_lower: str) -> Optional[Finding]:
        """检查是否讨论了内生性问题。"""
        endogeneity_keywords = [
            "endogeneity", "endogenous", "内生",
            "omitted variable", "遗漏变量",
            "reverse causality", "反向因果",
        ]
        if not any(kw in text_lower for kw in endogeneity_keywords):
            return Finding(
                category="methodology",
                severity="minor",
                description="未显式讨论潜在的内生性问题",
                suggestion="建议增加内生性讨论段落，说明为何当前识别策略能排除主要威胁",
                confidence=0.6,
            )
        return None

    def _check_robustness(self, text_lower: str) -> Optional[Finding]:
        """检查稳健性检验是否存在。"""
        robustness_keywords = [
            "robustness", "robust", "sensitivity",
            "placebo", "falsification",
            "稳健性", "敏感性分析",
        ]
        if not any(kw in text_lower for kw in robustness_keywords):
            return Finding(
                category="methodology",
                severity="major",
                description="未发现稳健性检验或敏感性分析",
                suggestion="建议补充稳健性检验：替代度量、不同样本、替代模型规格",
                confidence=0.65,
            )
        return None


# ==============================================================
# 2. 统计验证
# ==============================================================

class StatisticalValidationSkill(Skill):
    """验证统计方法和结果的正确性。

    检查重点：
      - 模型设定是否合理（OLS / Logit / Probit / Tobit 等）
      - 标准误处理（聚类/异方差稳健/bootstrap）
      - 显著性报告规范（系数 + 标准误/t值 + p值/星号）
      - 样本量与自由度
      - 多重检验修正
    """

    _DESCRIPTOR = SkillDescriptor(
        name="statistical_validation",
        level=SkillLevel.FUNCTIONAL,
        description="验证统计方法与结果：模型设定、标准误处理、显著性报告",
        prerequisites=(),
        input_schema={"paper_text": "str"},
        output_schema={"findings": "list[Finding]"},
        applicable_phases=("deep_review",),
        tags=("economics", "statistics", "validation"),
        token_cost_estimate=600,
        version="1.0",
    )

    @property
    def descriptor(self) -> SkillDescriptor:
        return self._DESCRIPTOR

    def can_apply(self, context: SkillContext) -> float:
        """含统计分析的论文适用度高。"""
        text_lower = context.paper_text.lower()
        score = 0.0

        # 统计关键词
        stat_keywords = [
            "regression", "coefficient", "standard error",
            "p-value", "significant", "ols", "logit",
            "回归", "系数", "标准误", "显著",
        ]
        hits = sum(1 for kw in stat_keywords if kw in text_lower)
        score += min(hits * 0.15, 0.6)

        # 表格存在（通常包含统计结果）
        if "table" in text_lower or "表" in text_lower:
            score += 0.2

        # Phase 匹配
        if context.current_phase.lower() == "deep_review":
            score += 0.1

        return min(score, 1.0)

    def execute(self, context: SkillContext) -> SkillResult:
        """执行统计验证。"""
        findings: list[Finding] = []
        text_lower = context.paper_text.lower()

        # 1. 检查标准误处理
        se_finding = self._check_standard_errors(text_lower)
        if se_finding:
            findings.append(se_finding)

        # 2. 检查多重检验
        multiple_testing = self._check_multiple_testing(text_lower)
        if multiple_testing:
            findings.append(multiple_testing)

        # 3. 检查样本量报告
        sample_finding = self._check_sample_size(text_lower)
        if sample_finding:
            findings.append(sample_finding)

        for f in findings:
            f.skill_source = self.descriptor.name

        return SkillResult(findings=findings, success=True)

    def get_instruction(self) -> str:
        return (
            "# 统计验证技能 (Statistical Validation)\n\n"
            "## 检查要点\n"
            "1. 标准误是否适当聚类（面板数据 → 个体/时间聚类）\n"
            "2. 异方差检验与处理\n"
            "3. 显著性星号与实际 p 值/CI 是否一致\n"
            "4. 多重检验情况下是否调整显著性阈值\n"
            "5. 样本量在不同规格间是否一致\n"
            "6. 经济显著性 vs 统计显著性的讨论\n"
        )

    def _check_standard_errors(self, text_lower: str) -> Optional[Finding]:
        """检查标准误处理是否讨论。"""
        se_keywords = [
            "cluster", "robust", "heteroskedast",
            "bootstrap", "聚类", "稳健标准误",
        ]
        panel_keywords = ["panel", "面板", "longitudinal", "fixed effect"]

        has_panel = any(kw in text_lower for kw in panel_keywords)
        has_se_discussion = any(kw in text_lower for kw in se_keywords)

        if has_panel and not has_se_discussion:
            return Finding(
                category="statistics",
                severity="major",
                description="面板数据分析未讨论标准误聚类方式",
                suggestion="面板数据应报告聚类标准误的聚类维度（如个体层面、时间层面）",
                confidence=0.7,
            )
        return None

    def _check_multiple_testing(self, text_lower: str) -> Optional[Finding]:
        """检查多重检验修正。"""
        # 检测多个因变量/子样本分析
        multiple_indicators = [
            "column (1)", "column (2)", "column (3)", "column (4)",
            "column (5)", "column (6)",
            "specification", "子样本",
        ]
        col_count = sum(1 for ind in multiple_indicators if ind in text_lower)

        correction_keywords = [
            "bonferroni", "holm", "fdr", "false discovery",
            "multiple testing", "多重检验",
        ]
        has_correction = any(kw in text_lower for kw in correction_keywords)

        if col_count >= 4 and not has_correction:
            return Finding(
                category="statistics",
                severity="minor",
                description="多规格/子样本分析（≥4列）但未讨论多重检验问题",
                suggestion="当报告多个假设检验时，建议讨论是否需要多重检验修正或解释为何不需要",
                confidence=0.5,
            )
        return None

    def _check_sample_size(self, text_lower: str) -> Optional[Finding]:
        """检查样本量是否充分报告。"""
        sample_keywords = [
            "observations", "sample size", "n =", "n=",
            "样本量", "观测值",
        ]
        if not any(kw in text_lower for kw in sample_keywords):
            return Finding(
                category="statistics",
                severity="minor",
                description="未明确报告样本量或观测数",
                suggestion="建议在描述性统计或回归表中明确报告样本量(N)",
                confidence=0.6,
            )
        return None


# ==============================================================
# 3. 引用验证
# ==============================================================

class CitationVerificationSkill(Skill):
    """验证引用的一致性和文献覆盖度。

    检查重点：
      - 文内引用与参考文献列表是否一致
      - 领域核心文献是否被引用
      - 自引比例是否异常
      - 引用时效性（是否过于陈旧）
    """

    _DESCRIPTOR = SkillDescriptor(
        name="citation_verification",
        level=SkillLevel.FUNCTIONAL,
        description="验证引用一致性：文内引用匹配、核心文献覆盖、引用时效",
        prerequisites=(),
        input_schema={"paper_text": "str"},
        output_schema={"findings": "list[Finding]"},
        applicable_phases=("deep_review", "synthesis"),
        tags=("citation", "literature", "verification"),
        token_cost_estimate=400,
        version="1.0",
    )

    @property
    def descriptor(self) -> SkillDescriptor:
        return self._DESCRIPTOR

    def can_apply(self, context: SkillContext) -> float:
        """几乎所有学术论文都需要引用验证。"""
        text_lower = context.paper_text.lower()
        score = 0.3  # 基础分

        # 引用存在
        citation_patterns = [
            r"\(\d{4}\)",  # (2023)
            r"\d{4}\)",    # Author, 2023)
            r"et al\.",     # et al.
        ]
        for pattern in citation_patterns:
            if re.search(pattern, text_lower):
                score += 0.2
                break

        # 参考文献段存在
        if "reference" in text_lower or "bibliography" in text_lower or "参考文献" in text_lower:
            score += 0.2

        if context.current_phase.lower() in ("deep_review", "synthesis"):
            score += 0.1

        return min(score, 1.0)

    def execute(self, context: SkillContext) -> SkillResult:
        """执行引用验证。"""
        findings: list[Finding] = []
        text = context.paper_text

        # 1. 提取文内引用
        inline_citations = self._extract_inline_citations(text)

        # 2. 检查引用时效性
        year_finding = self._check_citation_recency(inline_citations)
        if year_finding:
            findings.append(year_finding)

        # 3. 检查自引比例（简化：检测高频作者）
        self_cite_finding = self._check_self_citation_ratio(inline_citations, context)
        if self_cite_finding:
            findings.append(self_cite_finding)

        for f in findings:
            f.skill_source = self.descriptor.name

        return SkillResult(
            findings=findings,
            output_data={"citation_count": len(inline_citations)},
            success=True,
        )

    def get_instruction(self) -> str:
        return (
            "# 引用验证技能 (Citation Verification)\n\n"
            "## 检查要点\n"
            "1. 文内引用是否都出现在参考文献列表中\n"
            "2. 参考文献列表中是否有从未在正文引用的条目\n"
            "3. 领域核心/奠基文献是否被引用\n"
            "4. 最新文献覆盖（近 3 年是否有引用）\n"
            "5. 自引比例是否异常（>20% 需关注）\n"
        )

    def _extract_inline_citations(self, text: str) -> list[str]:
        """提取文内引用（简化版：提取年份模式）。"""
        # 匹配 (Author, 2023) 或 Author (2023) 模式
        pattern = r'(?:[A-Z][a-z]+(?:\s+(?:et\s+al\.?|and|&)\s+[A-Z][a-z]+)?[\s,]*)?(?:\()?(\d{4})(?:\))?'
        years = re.findall(r'\b((?:19|20)\d{2})\b', text)
        return years

    def _check_citation_recency(self, years: list[str]) -> Optional[Finding]:
        """检查引用时效性。"""
        if not years:
            return None

        int_years = [int(y) for y in years if y.isdigit()]
        if not int_years:
            return None

        max_year = max(int_years)
        recent_count = sum(1 for y in int_years if y >= max_year - 3)
        total = len(int_years)

        if total > 10 and recent_count / total < 0.1:
            return Finding(
                category="citation",
                severity="minor",
                description=f"引用时效性不足：近 3 年文献占比 < 10%（{recent_count}/{total}）",
                suggestion="建议补充近年相关研究的引用，特别是方法论和数据方面的最新进展",
                confidence=0.5,
            )
        return None

    def _check_self_citation_ratio(
        self, years: list[str], context: SkillContext
    ) -> Optional[Finding]:
        """检查自引比例（简化版：基于 metadata 中的作者信息）。"""
        # 需要 paper_metadata 中有 authors 信息才能检查
        authors = context.paper_metadata.get("authors", [])
        if not authors:
            return None

        # 简化实现：统计文中作者姓名出现频率
        text_lower = context.paper_text.lower()
        author_mentions = 0
        for author in authors:
            if isinstance(author, str):
                # 取姓氏（最后一个词）
                surname = author.split()[-1].lower() if author.split() else ""
                if surname and len(surname) > 2:
                    author_mentions += text_lower.count(surname)

        # 粗略判断：如果作者姓名出现次数占引用总数 30% 以上
        total_refs = len(years) if years else 1
        if author_mentions > 0 and author_mentions / total_refs > 0.3:
            return Finding(
                category="citation",
                severity="minor",
                description="自引比例可能偏高",
                suggestion="建议检查自引比例是否合理，增加其他研究者相关工作的引用",
                confidence=0.4,
            )
        return None


# ==============================================================
# 4. 逻辑连贯性检查
# ==============================================================

class LogicCoherenceSkill(Skill):
    """检查论文的论证逻辑连贯性。

    检查重点：
      - 假设/命题与证据之间的逻辑链是否完整
      - 结论是否超出数据/方法支撑的范围（overclaim）
      - 各 section 之间的逻辑衔接
      - 内部一致性（前后矛盾）
    """

    _DESCRIPTOR = SkillDescriptor(
        name="logic_coherence",
        level=SkillLevel.FUNCTIONAL,
        description="检查论证逻辑连贯性：假设-证据链、overclaim、section 衔接",
        prerequisites=(),
        input_schema={"paper_text": "str"},
        output_schema={"findings": "list[Finding]"},
        applicable_phases=("deep_review", "synthesis"),
        tags=("logic", "coherence", "overclaim"),
        token_cost_estimate=500,
        version="1.0",
    )

    # Overclaim 指示词
    _OVERCLAIM_INDICATORS = [
        "prove", "proves", "proven",
        "clearly shows", "undeniably",
        "definitively", "certainly",
        "证明了", "毫无疑问", "充分说明了",
        "无可辩驳",
    ]

    # 合理的因果/相关限定词
    _HEDGING_WORDS = [
        "suggest", "indicate", "imply", "consistent with",
        "may", "might", "could", "likely",
        "表明", "暗示", "可能", "或许",
    ]

    @property
    def descriptor(self) -> SkillDescriptor:
        return self._DESCRIPTOR

    def can_apply(self, context: SkillContext) -> float:
        """所有论文都需要逻辑一致性检查。"""
        score = 0.4  # 基础分

        if context.current_phase.lower() in ("deep_review", "synthesis"):
            score += 0.2

        # 有结论段的加分
        text_lower = context.paper_text.lower()
        if "conclusion" in text_lower or "结论" in text_lower:
            score += 0.2

        # 有因果声明的加分
        causal_words = ["cause", "effect", "impact", "因果", "影响"]
        if any(w in text_lower for w in causal_words):
            score += 0.2

        return min(score, 1.0)

    def execute(self, context: SkillContext) -> SkillResult:
        """执行逻辑连贯性检查。"""
        findings: list[Finding] = []
        text = context.paper_text
        text_lower = text.lower()

        # 1. Overclaim 检测
        overclaim_findings = self._detect_overclaim(text_lower)
        findings.extend(overclaim_findings)

        # 2. 因果-相关混淆检查
        causal_finding = self._check_causal_language(text_lower)
        if causal_finding:
            findings.append(causal_finding)

        for f in findings:
            f.skill_source = self.descriptor.name

        return SkillResult(findings=findings, success=True)

    def get_instruction(self) -> str:
        return (
            "# 逻辑连贯性检查 (Logic Coherence)\n\n"
            "## 检查要点\n"
            "1. Overclaim: 结论是否超出数据/方法的支撑范围\n"
            "   - 相关性研究不能声称因果关系\n"
            "   - 单一样本不能推广到全部群体\n"
            "2. 逻辑链完整性：假设 → 方法 → 证据 → 结论\n"
            "3. 内部一致性：摘要、正文、结论之间无矛盾\n"
            "4. Hedging: 使用恰当的限定语言\n"
        )

    def _detect_overclaim(self, text_lower: str) -> list[Finding]:
        """检测过度声明。"""
        findings = []
        for indicator in self._OVERCLAIM_INDICATORS:
            if indicator in text_lower:
                # 检查上下文是否有限定词缓解
                # 简化：仅标记发现
                findings.append(Finding(
                    category="logic",
                    severity="minor",
                    description=f"可能存在过度声明：使用了 '{indicator}'",
                    suggestion="建议使用更审慎的表述（如 suggest/indicate）",
                    confidence=0.5,
                ))
                break  # 只报告第一个，避免重复
        return findings

    def _check_causal_language(self, text_lower: str) -> Optional[Finding]:
        """检查因果语言是否匹配研究设计。"""
        strong_causal = ["cause", "causal effect", "caused by", "因果效应"]
        has_strong_causal = any(term in text_lower for term in strong_causal)

        # 如果使用强因果语言但没有明确的因果识别方法
        causal_methods = [
            "instrumental", "discontinuity", "difference-in-difference",
            "natural experiment", "randomized",
            "工具变量", "断点回归", "双重差分", "随机实验",
        ]
        has_causal_method = any(m in text_lower for m in causal_methods)

        if has_strong_causal and not has_causal_method:
            return Finding(
                category="logic",
                severity="major",
                description="使用因果语言但未发现明确的因果识别策略",
                suggestion="若为因果声明，需明确识别策略（IV/DID/RDD等）；若为相关性研究，应调整为 'associated with' 等表述",
                confidence=0.6,
            )
        return None
