"""
core/v2/finding_quality.py — Finding Quality Gate (Q1)

设计依据:
    - UPGRADE_PLAN_FINAL §Q1: mark_complete 前做规则基础的结构检查
    - COGNITIVE_ANCHOR §4.3: 认知辅助模式（nudge 而非阻止退出）
    - C5 约束-而非-控制: Agent 可以选择忽略 nudge 并退出

核心思想:
    - 人类审稿人写完意见后会自检: "这条有证据吗？可操作吗？够具体吗？"
    - 规则基础检查，零 LLM 成本
    - 产出 nudge 而非 hard block（Agent 有自主权）

质量维度:
    1. has_evidence: 是否引用了论文中的具体位置或数据？
    2. is_actionable: 作者能否根据这条意见做出具体修改？（仅 high/critical）
    3. is_specific: 是否足够具体（非空泛评价）？
    4. severity_justified: severity 评级是否与描述匹配？（粗略检查）
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# ============================================================
# Data Structures
# ============================================================

@dataclass
class QualityIssue:
    """单个 finding 的一个质量问题。"""
    finding_index: int      # finding 在列表中的位置（从 1 开始）
    finding_text: str       # finding 原文（截断）
    issue_type: str         # "no_evidence" | "not_actionable" | "too_vague" | "severity_mismatch"
    suggestion: str         # 改进建议


# ============================================================
# Vagueness Detection
# ============================================================

# 空泛模式——匹配这些短语且描述很短时判定为 vague
_VAGUE_PATTERNS_EN = [
    r"needs?\s+improvement",
    r"could\s+be\s+better",
    r"is\s+unclear",
    r"should\s+be\s+revised",
    r"not\s+(well|clearly)\s+(written|explained|described)",
    r"lacks?\s+clarity",
    r"poorly\s+written",
    r"needs?\s+more\s+detail",
]

_VAGUE_PATTERNS_ZH = [
    r"写作需要改进",
    r"不够清楚",
    r"表述不清",
    r"需要完善",
    r"有待改进",
    r"建议修改",
]

_VAGUE_COMPILED = [re.compile(p, re.IGNORECASE) for p in _VAGUE_PATTERNS_EN + _VAGUE_PATTERNS_ZH]


def _is_vague(text: str) -> bool:
    """检测是否是空泛表述。短文本 + 匹配空泛模式 → vague。"""
    if len(text) > 80:
        return False  # 长描述通常不会是空泛的
    return any(p.search(text) for p in _VAGUE_COMPILED)


# ============================================================
# FindingQualityGate
# ============================================================

class FindingQualityGate:
    """
    在审稿完成前，对 findings 做规则基础的质量自检。

    用法:
        gate = FindingQualityGate()
        issues = gate.evaluate(findings)
        if issues:
            nudge_text = gate.format_nudge(issues)
    """

    # 证据文本最短长度（低于此视为"无实质证据"）
    MIN_EVIDENCE_LENGTH: int = 20

    def evaluate(self, findings: list[dict]) -> list[QualityIssue]:
        """
        对每条 finding 做质量检查。

        Args:
            findings: state.findings 列表（dict 格式）

        Returns:
            发现的质量问题列表（可能为空）
        """
        issues: list[QualityIssue] = []

        for idx, f in enumerate(findings, 1):
            finding_text = f.get("finding", "")[:80]
            priority = f.get("priority", "medium")
            evidence = f.get("evidence", "") or ""

            # Check 1: 有证据吗？
            if len(evidence.strip()) < self.MIN_EVIDENCE_LENGTH:
                issues.append(QualityIssue(
                    finding_index=idx,
                    finding_text=finding_text,
                    issue_type="no_evidence",
                    suggestion="请指出论文中哪个具体段落/数据支撑这个判断",
                ))

            # Check 2: 可操作吗？（仅对 high priority）
            if priority == "high" and not self._has_actionable_hint(f):
                issues.append(QualityIssue(
                    finding_index=idx,
                    finding_text=finding_text,
                    issue_type="not_actionable",
                    suggestion="严重问题建议附带可操作的修改建议",
                ))

            # Check 3: 够具体吗？
            if _is_vague(finding_text):
                issues.append(QualityIssue(
                    finding_index=idx,
                    finding_text=finding_text,
                    issue_type="too_vague",
                    suggestion="请用具体的数字、位置或例子来说明",
                ))

        return issues

    def format_nudge(self, issues: list[QualityIssue]) -> str:
        """
        格式化为 nudge 文本。

        措辞为认知辅助模式: 提醒 + 自主权声明。
        最多展示 4 条问题（避免信息过载）。
        """
        if not issues:
            return ""

        lines = [f"[质量自检] 发现 {len(issues)} 条 findings 可能需要加强:"]
        for issue in issues[:4]:
            lines.append(
                f"  - #{issue.finding_index} \"{issue.finding_text[:50]}...\" → {issue.issue_type}: {issue.suggestion}"
            )
        if len(issues) > 4:
            lines.append(f"  ... 还有 {len(issues) - 4} 条")
        lines.append(
            "[你可以选择补充证据、修改描述，或确认当前已足够后退出。]"
        )
        return "\n".join(lines)

    def _has_actionable_hint(self, finding: dict) -> bool:
        """
        检查 finding 是否包含可操作建议的线索。

        粗略启发式: 描述中包含建议性动词或"建议"关键词。
        """
        text = finding.get("finding", "") + " " + (finding.get("evidence", "") or "")
        # 英文动作词
        action_patterns = [
            r"\bshould\b", r"\bcould\b", r"\brecommend\b",
            r"\bsuggest\b", r"\badd\b", r"\bremove\b",
            r"\binclude\b", r"\breport\b", r"\bclarif",
        ]
        # 中文建议词
        zh_patterns = [r"建议", r"应该", r"需要", r"可以考虑"]

        for p in action_patterns + zh_patterns:
            if re.search(p, text, re.IGNORECASE):
                return True
        return False
