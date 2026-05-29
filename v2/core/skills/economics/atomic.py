"""
core/skills/economics/atomic.py — 经济学 Atomic Skills

单次操作的封装，可被 Functional Skills 组合调用：
  1. ExtractNumericClaimSkill: 提取论文中的数值声明
  2. CompareWithDomainNormSkill: 将数值与领域规范对比
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
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
# 数值声明的结构化表示
# ==============================================================

@dataclass
class NumericClaim:
    """论文中的数值声明。

    Attributes:
        value: 数值（字符串形式，保留原始格式）
        context: 上下文文本
        claim_type: 声明类型（coefficient / sample_size / r_squared / p_value / percentage / other）
        location: 位置信息
    """
    value: str
    context: str
    claim_type: str = "other"
    location: str = ""


# ==============================================================
# 1. 提取数值声明
# ==============================================================

class ExtractNumericClaimSkill(Skill):
    """从论文文本中提取结构化的数值声明。

    提取的声明类型：
      - 系数值（coefficient）
      - 样本量（sample_size）
      - R² / 拟合优度
      - p 值 / 显著性水平
      - 百分比 / 比例
      - 其他数值
    """

    _DESCRIPTOR = SkillDescriptor(
        name="extract_numeric_claim",
        level=SkillLevel.ATOMIC,
        description="提取论文中的数值声明：系数、样本量、R²、p值、百分比",
        prerequisites=(),
        input_schema={"paper_text": "str"},
        output_schema={"claims": "list[NumericClaim]"},
        applicable_phases=("deep_review", "synthesis"),
        tags=("extraction", "numeric", "atomic"),
        token_cost_estimate=200,
        version="1.0",
    )

    # 数值模式
    _PATTERNS = {
        "coefficient": [
            # "coefficient of 0.35" or "系数为0.35"
            r'coefficient\s+(?:of\s+|is\s+|=\s*)?([+-]?\d+\.?\d*)',
            r'系数[为是]\s*([+-]?\d+\.?\d*)',
            # β = 0.35
            r'[βα]\s*=\s*([+-]?\d+\.?\d*)',
        ],
        "sample_size": [
            r'[Nn]\s*=\s*([0-9,]+)',
            r'(\d{2,}[,\d]*)\s*observations',
            r'样本[量数][为是含]?\s*(\d+[,\d]*)',
        ],
        "r_squared": [
            r'[Rr][\s²2]+\s*=?\s*(0\.\d+)',
            r'R-squared\s*[=:]\s*(0\.\d+)',
        ],
        "p_value": [
            r'[Pp]\s*[<>=]+\s*(0\.\d+)',
            r'p-value\s*[=:<>]\s*(0\.\d+)',
            r'显著性水平\s*(0\.\d+)',
        ],
        "percentage": [
            r'(\d+\.?\d*)\s*%',
            r'(\d+\.?\d*)\s*percent',
            r'(\d+\.?\d*)\s*个百分点',
        ],
    }

    @property
    def descriptor(self) -> SkillDescriptor:
        return self._DESCRIPTOR

    def can_apply(self, context: SkillContext) -> float:
        """含数值的文本都适用。"""
        # 检测数字密度
        numbers = re.findall(r'\d+\.?\d*', context.paper_text)
        density = len(numbers) / max(len(context.paper_text), 1) * 100

        if density > 0.5:
            return 0.8
        elif density > 0.2:
            return 0.5
        return 0.2

    def execute(self, context: SkillContext) -> SkillResult:
        """提取论文中的数值声明。"""
        claims: list[NumericClaim] = []
        text = context.paper_text

        for claim_type, patterns in self._PATTERNS.items():
            for pattern in patterns:
                for match in re.finditer(pattern, text, re.IGNORECASE):
                    # 获取上下文（前后 50 字符）
                    start = max(0, match.start() - 50)
                    end = min(len(text), match.end() + 50)
                    context_text = text[start:end].strip()

                    claims.append(NumericClaim(
                        value=match.group(1) if match.groups() else match.group(0),
                        context=context_text,
                        claim_type=claim_type,
                        location=f"char_{match.start()}",
                    ))

        # 去重（同一位置的多模式匹配）
        seen_positions: set[str] = set()
        unique_claims: list[NumericClaim] = []
        for claim in claims:
            if claim.location not in seen_positions:
                seen_positions.add(claim.location)
                unique_claims.append(claim)

        return SkillResult(
            findings=[],  # Atomic Skill 通常不直接产出 Findings
            output_data={
                "claims": [
                    {
                        "value": c.value,
                        "context": c.context,
                        "claim_type": c.claim_type,
                        "location": c.location,
                    }
                    for c in unique_claims
                ],
                "total_claims": len(unique_claims),
            },
            success=True,
        )

    def get_instruction(self) -> str:
        return (
            "# 数值声明提取 (Extract Numeric Claim)\n\n"
            "从论文文本中提取结构化数值声明，包括：\n"
            "- 回归系数 (β, coefficient)\n"
            "- 样本量 (N, observations)\n"
            "- 拟合优度 (R², R-squared)\n"
            "- 显著性水平 (p-value)\n"
            "- 百分比/比例\n\n"
            "输出为结构化列表，供下游 Skill 验证一致性。\n"
        )


# ==============================================================
# 2. 领域规范对比
# ==============================================================

class CompareWithDomainNormSkill(Skill):
    """将论文数值与经济学领域规范进行对比。

    对比维度：
      - 样本量是否合理（不同方法的最低要求）
      - R² 是否在合理范围（截面 vs 面板 vs 时间序列）
      - 效应大小是否异常（过大或过小）
      - First-stage F 统计量（IV 方法：>10 经验规则）
    """

    _DESCRIPTOR = SkillDescriptor(
        name="compare_with_domain_norm",
        level=SkillLevel.ATOMIC,
        description="将数值与经济学领域规范对比：样本量、R²、效应大小、F统计量",
        prerequisites=("extract_numeric_claim",),
        input_schema={"claims": "list[dict]", "paper_metadata": "dict"},
        output_schema={"findings": "list[Finding]"},
        applicable_phases=("deep_review",),
        tags=("economics", "norm", "comparison", "atomic"),
        token_cost_estimate=300,
        version="1.0",
    )

    # 领域规范阈值
    _NORMS = {
        "min_sample_size": {
            "did": 100,
            "iv": 50,
            "rdd": 500,  # RDD 需要较大样本
            "default": 30,
        },
        "r_squared_range": {
            "cross_section": (0.05, 0.80),
            "panel": (0.10, 0.95),
            "time_series": (0.20, 0.99),
            "default": (0.01, 0.95),
        },
        "first_stage_f": 10,  # Stock-Yogo weak instrument threshold
    }

    @property
    def descriptor(self) -> SkillDescriptor:
        return self._DESCRIPTOR

    def can_apply(self, context: SkillContext) -> float:
        """有数值提取结果时才适用。"""
        # 检查 parameters 中是否有上游 claims
        if context.parameters.get("claims"):
            return 0.9

        # 文本中有数值也可以
        text_lower = context.paper_text.lower()
        has_numbers = bool(re.search(r'\d+\.?\d*', text_lower))
        if has_numbers and context.current_phase.lower() == "deep_review":
            return 0.5
        return 0.2

    def execute(self, context: SkillContext) -> SkillResult:
        """执行领域规范对比。"""
        findings: list[Finding] = []

        # 从上游 output_data 获取 claims（如果有）
        claims = context.parameters.get("claims", [])

        # 也从文本中直接检查关键数值
        text_lower = context.paper_text.lower()

        # 1. 检查 First-stage F（IV 方法）
        f_finding = self._check_first_stage_f(text_lower)
        if f_finding:
            findings.append(f_finding)

        # 2. 检查 R² 合理性
        r2_finding = self._check_r_squared(claims, context)
        if r2_finding:
            findings.append(r2_finding)

        # 3. 检查样本量
        sample_finding = self._check_sample_norm(claims, context)
        if sample_finding:
            findings.append(sample_finding)

        for f in findings:
            f.skill_source = self.descriptor.name

        return SkillResult(findings=findings, success=True)

    def get_instruction(self) -> str:
        return (
            "# 领域规范对比 (Compare With Domain Norm)\n\n"
            "## 规范阈值\n"
            "- First-stage F > 10 (Stock-Yogo 弱工具变量检验)\n"
            "- 横截面 R²: 0.05-0.80 为合理范围\n"
            "- 面板 R²: 0.10-0.95 为合理范围\n"
            "- 最低样本量: DID≥100, IV≥50, RDD≥500\n"
            "- 过高的 R² (>0.95) 可能暗示过拟合或数据问题\n"
        )

    def _check_first_stage_f(self, text_lower: str) -> Optional[Finding]:
        """检查 IV 方法的 First-stage F 统计量。"""
        # 检测是否使用 IV
        iv_keywords = ["instrumental variable", "iv", "2sls", "工具变量"]
        if not any(kw in text_lower for kw in iv_keywords):
            return None

        # 查找 F 统计量
        f_match = re.search(r'[Ff][\s-]*(?:stat|statistic)?\s*[=:]\s*(\d+\.?\d*)', text_lower)
        if f_match:
            f_value = float(f_match.group(1))
            if f_value < self._NORMS["first_stage_f"]:
                return Finding(
                    category="statistics",
                    severity="major",
                    description=f"First-stage F = {f_value:.1f} < 10（弱工具变量风险）",
                    evidence=f"检测到 F 统计量 = {f_value}",
                    suggestion="F < 10 暗示弱工具变量问题。建议报告 Anderson-Rubin 置信区间或使用 LIML 估计",
                    confidence=0.85,
                )
        else:
            # 使用 IV 但未报告 F 统计量
            f_report_kw = ["first stage", "first-stage", "f-stat", "f stat"]
            if not any(kw in text_lower for kw in f_report_kw):
                return Finding(
                    category="statistics",
                    severity="major",
                    description="使用工具变量方法但未报告 First-stage F 统计量",
                    suggestion="请报告 First-stage F 统计量以排除弱工具变量问题（经验阈值 > 10）",
                    confidence=0.75,
                )
        return None

    def _check_r_squared(
        self, claims: list[dict], context: SkillContext
    ) -> Optional[Finding]:
        """检查 R² 是否在合理范围。"""
        r2_values = [
            float(c["value"])
            for c in claims
            if c.get("claim_type") == "r_squared" and self._is_float(c.get("value", ""))
        ]

        if not r2_values:
            return None

        # 确定数据类型
        text_lower = context.paper_text.lower()
        if "panel" in text_lower or "面板" in text_lower:
            r2_range = self._NORMS["r_squared_range"]["panel"]
        elif "cross" in text_lower or "截面" in text_lower:
            r2_range = self._NORMS["r_squared_range"]["cross_section"]
        else:
            r2_range = self._NORMS["r_squared_range"]["default"]

        for r2 in r2_values:
            if r2 > r2_range[1]:
                return Finding(
                    category="statistics",
                    severity="minor",
                    description=f"R² = {r2:.3f} 异常偏高，可能暗示过拟合或数据问题",
                    suggestion="请检查模型是否过拟合，或数据是否存在自相关/multicollinearity",
                    confidence=0.6,
                )

        return None

    def _check_sample_norm(
        self, claims: list[dict], context: SkillContext
    ) -> Optional[Finding]:
        """检查样本量是否达到领域最低要求。"""
        sample_claims = [
            c for c in claims if c.get("claim_type") == "sample_size"
        ]
        if not sample_claims:
            return None

        # 尝试解析最小样本量
        sample_sizes = []
        for c in sample_claims:
            val = c.get("value", "").replace(",", "")
            if val.isdigit():
                sample_sizes.append(int(val))

        if not sample_sizes:
            return None

        min_n = min(sample_sizes)

        # 确定方法类型
        text_lower = context.paper_text.lower()
        method = "default"
        for m in ("rdd", "did", "iv"):
            method_kws = {
                "rdd": ["regression discontinuity", "rdd", "断点回归"],
                "did": ["difference-in-difference", "did", "双重差分"],
                "iv": ["instrumental", "2sls", "工具变量"],
            }
            if any(kw in text_lower for kw in method_kws.get(m, [])):
                method = m
                break

        threshold = self._NORMS["min_sample_size"].get(method, 30)
        if min_n < threshold:
            return Finding(
                category="statistics",
                severity="major",
                description=f"样本量 N={min_n} 低于 {method.upper()} 方法建议的最小样本量 {threshold}",
                suggestion=f"{method.upper()} 方法建议样本量至少 {threshold}，请讨论小样本对估计效率和推断有效性的影响",
                confidence=0.7,
            )
        return None

    @staticmethod
    def _is_float(s: str) -> bool:
        """判断字符串是否可转为浮点数。"""
        try:
            float(s)
            return True
        except (ValueError, TypeError):
            return False
