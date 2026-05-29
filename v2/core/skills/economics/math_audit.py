"""
core/skills/economics/math_audit.py — 附录数学推导审查技能

AppendixMathAuditSkill: 检测论文数学推导中的符号/公式错误

目标场景（来自人工 Gold Standard 评估的 Recall 缺口）：
  - Paper 001 G001: 附录公式中变量名(下标)错误 → α_i 写成 α_t
  - Paper 003 G005: 公式推导步骤中的排印错误(typo in derivation)
  - 跨节 derivation 中变量定义与使用不一致

审查范围：
  1. 变量名一致性：推导过程中同一变量的表示是否前后一致
  2. 下标/上标一致性：时间/个体/组别下标是否在推导步骤间保持连贯
  3. 公式步骤连续性：相邻推导步骤间是否有突变（突然多/少一个项）
  4. 符号定义引用匹配：正文定义的符号 vs 附录使用的符号

设计原则：
  - 纯基于 regex + heuristic 的扫描（零 LLM 调用）
  - 输出高置信度 Finding → 供 Cognitive Loop 进一步验证
  - 低 token_cost_estimate (300 tokens) — 快速预扫描
"""

from __future__ import annotations

import logging
import re
import time
from collections import Counter, defaultdict
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

logger = logging.getLogger(__name__)


# ==============================================================
# Constants
# ==============================================================

# Common math variable patterns (LaTeX and Unicode)
_VAR_PATTERNS = [
    # Greek letters with subscripts: \alpha_i, \beta_{it}, α_i
    r"\\(?:alpha|beta|gamma|delta|epsilon|varepsilon|zeta|eta|theta|"
    r"iota|kappa|lambda|mu|nu|xi|pi|rho|sigma|tau|upsilon|phi|chi|psi|omega)"
    r"(?:_\{?[a-zA-Z0-9,]+\}?)?",
    # Latin letters with subscripts: X_{it}, Y_i, u_{it}
    r"[A-Za-z](?:_\{?[a-zA-Z0-9,]+\}?)?",
]

# Patterns indicating appendix/proof sections
_APPENDIX_INDICATORS = [
    r"(?:appendix|apéndice|anhang)",
    r"(?:proof\s+of|derivation\s+of|mathematical\s+appendix)",
    r"(?:online\s+appendix|supplementary\s+material)",
    r"\\section\*?\{[Aa]ppendix",
    r"\\appendix",
]

# Equation environment patterns
_EQUATION_PATTERNS = [
    r"\\begin\{(?:equation|align|gather|multline|eqnarray)\*?\}(.*?)\\end\{(?:equation|align|gather|multline|eqnarray)\*?\}",
    r"\$\$(.*?)\$\$",
    r"\\\[(.*?)\\\]",
]


# ==============================================================
# Data Structures
# ==============================================================

@dataclass
class MathSymbol:
    """A mathematical symbol occurrence with context."""
    raw: str                  # Original text form, e.g. "\\alpha_{it}"
    base: str                 # Base variable, e.g. "\\alpha"
    subscript: str = ""       # Subscript content, e.g. "it"
    superscript: str = ""     # Superscript content, e.g. "2"
    location: str = ""        # Where it appears (section/equation number)
    line_number: int = 0


@dataclass
class DerivationStep:
    """A single step in a mathematical derivation."""
    raw_text: str
    symbols_used: list[MathSymbol] = field(default_factory=list)
    step_index: int = 0
    line_number: int = 0


# ==============================================================
# Skill Implementation
# ==============================================================

class AppendixMathAuditSkill(Skill):
    """附录数学推导审查技能。

    三阶段审查流程：
      Stage 1: 定位附录/证明区域，提取所有数学公式
      Stage 2: 构建符号注册表（symbol registry），追踪变量定义
      Stage 3: 检测不一致：下标错误、符号突变、定义-引用不匹配

    典型 Finding 类别:
      - methodology/critical: 推导中的明确符号错误
      - methodology/major: 跨步骤的变量名不一致
      - clarity/minor: 符号未定义或定义模糊
    """

    _DESCRIPTOR = SkillDescriptor(
        name="appendix_math_audit",
        level=SkillLevel.FUNCTIONAL,
        description="审查附录数学推导的符号一致性：变量下标错误、公式步骤突变、定义-引用不匹配",
        prerequisites=(),
        input_schema={
            "paper_text": "str (required, full paper including appendix)",
        },
        output_schema={
            "findings": "list[Finding]",
            "symbol_registry": "dict — all symbols found",
            "derivation_steps": "int — number of steps analyzed",
        },
        applicable_phases=("deep_review", "verification"),
        tags=("mathematics", "appendix", "consistency", "proofreading", "derivation"),
        token_cost_estimate=300,
        version="1.0",
    )

    @property
    def descriptor(self) -> SkillDescriptor:
        return self._DESCRIPTOR

    def can_apply(self, context: SkillContext) -> float:
        """Evaluate whether this paper has appendix math worth auditing."""
        if not context.paper_text:
            return 0.0

        text = context.paper_text
        text_lower = text.lower()
        score = 0.0

        # Strong signal: has appendix section
        if any(re.search(pat, text_lower) for pat in _APPENDIX_INDICATORS):
            score += 0.4

        # Strong signal: has equation environments
        equation_count = sum(
            len(re.findall(pat, text, re.DOTALL))
            for pat in _EQUATION_PATTERNS
        )
        if equation_count >= 5:
            score += 0.3
        elif equation_count >= 2:
            score += 0.15

        # Signal: has proof/derivation keywords
        proof_keywords = ["proof", "derivation", "q.e.d.", "\\qed", "□",
                         "we show that", "it follows that", "by substituting"]
        proof_count = sum(1 for kw in proof_keywords if kw in text_lower)
        if proof_count >= 3:
            score += 0.2
        elif proof_count >= 1:
            score += 0.1

        # Signal: Greek letters with subscripts (math-heavy)
        greek_subs = len(re.findall(
            r"\\(?:alpha|beta|gamma|delta|epsilon|theta|lambda|mu|sigma|phi|omega)"
            r"_\{?[a-zA-Z0-9]",
            text,
        ))
        if greek_subs >= 10:
            score += 0.2
        elif greek_subs >= 5:
            score += 0.1

        # Phase relevance
        if context.current_phase in ("deep_review", "verification"):
            score += 0.1

        # Paper type hint
        paper_type = context.paper_metadata.get("paper_type", "")
        if paper_type in ("theoretical", "empirical_econ"):
            score += 0.1

        return min(score, 1.0)

    def execute(self, context: SkillContext) -> SkillResult:
        """Run math audit on appendix sections."""
        start_time = time.time()
        findings: list[Finding] = []

        text = context.paper_text
        if not text:
            return SkillResult(
                success=True,
                findings=[],
                output_data={"message": "No paper text provided"},
            )

        # Stage 1: Locate appendix/proof sections and extract equations
        appendix_text = self._extract_appendix_region(text)
        equations = self._extract_equations(appendix_text or text)

        if not equations:
            return SkillResult(
                success=True,
                findings=[],
                output_data={
                    "message": "No mathematical equations found in appendix",
                    "derivation_steps": 0,
                },
                execution_time_ms=(time.time() - start_time) * 1000,
            )

        # Stage 2: Build symbol registry
        all_symbols = self._build_symbol_registry(equations, text)

        # Stage 3: Run consistency checks
        findings.extend(self._check_subscript_consistency(all_symbols))
        findings.extend(self._check_derivation_continuity(equations))
        findings.extend(self._check_definition_usage_mismatch(text, all_symbols))
        findings.extend(self._check_sequential_subscript_errors(equations))

        execution_time = (time.time() - start_time) * 1000

        # Build output summary
        symbol_summary = self._summarize_symbols(all_symbols)

        return SkillResult(
            findings=findings,
            output_data={
                "symbol_registry": symbol_summary,
                "derivation_steps": len(equations),
                "appendix_detected": appendix_text is not None,
                "total_symbols_tracked": len(all_symbols),
            },
            success=True,
            execution_time_ms=execution_time,
            metadata={
                "equations_found": len(equations),
                "findings_count": len(findings),
            },
        )

    def get_instruction(self) -> str:
        """Return detailed SOP for the math audit skill (Layer 2)."""
        return (
            "附录数学推导审查技能 (AppendixMathAuditSkill)\n\n"
            "审查策略:\n"
            "1. 定位附录区域 → 提取所有数学公式环境\n"
            "2. 构建符号注册表：追踪每个变量的 base + subscript + 首次出现位置\n"
            "3. 三类不一致检测:\n"
            "   a) 下标变异: 同一变量在相邻步骤中下标不同(α_i vs α_t)\n"
            "   b) 步骤突变: 公式某项突然消失或出现(遗漏传递)\n"
            "   c) 定义漂移: 正文定义了 X_it，附录使用 X_i(丢失时间维度)\n\n"
            "输出要求:\n"
            "- 每个 Finding 须包含 evidence(引用具体公式)\n"
            "- confidence ≥ 0.7 才报告为 major\n"
            "- 仅当存在明确矛盾时报告 critical\n"
        )

    # ==============================================================
    # Stage 1: Extract appendix/proof region
    # ==============================================================

    def _extract_appendix_region(self, text: str) -> Optional[str]:
        """Extract the appendix portion of the paper.

        Strategy:
          1. Look for \\appendix command or 'Appendix' section header
          2. If found, return everything from that point to end
          3. If not found, look for 'Proof of' sections
          4. Return None if no appendix detected
        """
        # LaTeX \appendix command
        appendix_match = re.search(r"\\appendix\b", text)
        if appendix_match:
            return text[appendix_match.start():]

        # Section header "Appendix"
        header_match = re.search(
            r"\\section\*?\{[Aa]ppendix",
            text,
        )
        if header_match:
            return text[header_match.start():]

        # Plain text "Appendix A" or "APPENDIX"
        plain_match = re.search(
            r"\n\s*(?:APPENDIX|Appendix)\s*[A-Z]?\s*[\n:]",
            text,
        )
        if plain_match:
            return text[plain_match.start():]

        # "Proof of Proposition" sections
        proof_match = re.search(
            r"\\(?:sub)?section\*?\{(?:Proof|Derivation)\s+of",
            text,
        )
        if proof_match:
            return text[proof_match.start():]

        return None

    # ==============================================================
    # Stage 1: Extract equations
    # ==============================================================

    def _extract_equations(self, text: str) -> list[str]:
        """Extract all mathematical equations from the text."""
        equations: list[str] = []

        for pattern in _EQUATION_PATTERNS:
            matches = re.findall(pattern, text, re.DOTALL)
            equations.extend(matches)

        # Also capture inline math that's substantial (has subscripts/fractions)
        inline_math = re.findall(r"\$([^$]{10,})\$", text)
        for eq in inline_math:
            # Only keep if it has subscripts or fractions (non-trivial)
            if "_" in eq or "\\frac" in eq or "\\sum" in eq:
                equations.append(eq)

        return equations

    # ==============================================================
    # Stage 2: Build symbol registry
    # ==============================================================

    def _build_symbol_registry(
        self, equations: list[str], full_text: str
    ) -> list[MathSymbol]:
        """Build a registry of all mathematical symbols with their subscripts."""
        symbols: list[MathSymbol] = []

        # Pattern: \greek_{subscript} or \greek_x
        greek_pattern = (
            r"(\\(?:alpha|beta|gamma|delta|epsilon|varepsilon|zeta|eta|theta|"
            r"vartheta|iota|kappa|lambda|mu|nu|xi|pi|rho|sigma|tau|upsilon|"
            r"phi|varphi|chi|psi|omega|Gamma|Delta|Theta|Lambda|Xi|Pi|Sigma|"
            r"Upsilon|Phi|Psi|Omega))"
            r"(?:_\{([^}]*)\}|_([a-zA-Z0-9]))?(?:\^\{([^}]*)\}|\^([a-zA-Z0-9]))?"
        )

        # Pattern: Latin variable with subscript: X_{it}, Y_i
        latin_pattern = (
            r"([A-Za-z])(?:_\{([^}]*)\}|_([a-zA-Z0-9]))"
            r"(?:\^\{([^}]*)\}|\^([a-zA-Z0-9]))?"
        )

        for eq_idx, eq in enumerate(equations):
            # Greek symbols
            for match in re.finditer(greek_pattern, eq):
                base = match.group(1)
                subscript = match.group(2) or match.group(3) or ""
                superscript = match.group(4) or match.group(5) or ""
                symbols.append(MathSymbol(
                    raw=match.group(0),
                    base=base,
                    subscript=subscript,
                    superscript=superscript,
                    location=f"equation_{eq_idx}",
                    line_number=eq_idx,
                ))

            # Latin variables with subscripts
            for match in re.finditer(latin_pattern, eq):
                base = match.group(1)
                subscript = match.group(2) or match.group(3) or ""
                superscript = match.group(4) or match.group(5) or ""
                # Skip common non-variable subscripts (like log_2, e_x)
                if base.lower() in ("e", "d") and subscript in ("x", "t"):
                    continue
                symbols.append(MathSymbol(
                    raw=match.group(0),
                    base=base,
                    subscript=subscript,
                    superscript=superscript,
                    location=f"equation_{eq_idx}",
                    line_number=eq_idx,
                ))

        return symbols

    # ==============================================================
    # Stage 3a: Subscript consistency check
    # ==============================================================

    def _check_subscript_consistency(
        self, symbols: list[MathSymbol]
    ) -> list[Finding]:
        """Detect variables whose subscripts change suspiciously.

        The key insight: within a single derivation context, a variable's
        subscript structure should remain stable. E.g., if α is indexed by
        individual 'i', it shouldn't suddenly become α_t (time-indexed)
        without explicit redefinition.
        """
        findings: list[Finding] = []

        # Group symbols by base variable
        by_base: dict[str, list[MathSymbol]] = defaultdict(list)
        for sym in symbols:
            if sym.subscript:  # Only track subscripted variables
                by_base[sym.base].append(sym)

        for base, occurrences in by_base.items():
            if len(occurrences) < 3:
                continue

            # Count subscript patterns
            sub_counts = Counter(sym.subscript for sym in occurrences)

            # If there's a dominant subscript and rare variants, flag the variants
            total = sum(sub_counts.values())
            for sub, count in sub_counts.items():
                ratio = count / total
                # A subscript appearing only 1-2 times when there are 5+
                # occurrences of a different subscript is suspicious
                if ratio < 0.2 and count <= 2 and total >= 5:
                    dominant_sub = sub_counts.most_common(1)[0][0]
                    dominant_count = sub_counts.most_common(1)[0][1]

                    # Find the locations of the anomalous subscripts
                    anomalous = [s for s in occurrences if s.subscript == sub]
                    locations = [s.location for s in anomalous]

                    findings.append(Finding(
                        category="methodology",
                        severity="major",
                        description=(
                            f"Subscript inconsistency in variable '{base}': "
                            f"appears {dominant_count}× as {base}_{{{dominant_sub}}} "
                            f"but {count}× as {base}_{{{sub}}}. "
                            f"Likely a typo in the minority variant."
                        ),
                        evidence=(
                            f"Dominant form: {base}_{{{dominant_sub}}} "
                            f"({dominant_count} occurrences). "
                            f"Anomalous form: {base}_{{{sub}}} "
                            f"at {', '.join(locations)}"
                        ),
                        suggestion=(
                            f"Verify whether {base}_{{{sub}}} should be "
                            f"{base}_{{{dominant_sub}}}. If intentional "
                            f"(different index set), add explicit definition."
                        ),
                        location=f"Appendix: {locations[0]}",
                        confidence=0.75 if count == 1 else 0.6,
                        skill_source="appendix_math_audit",
                    ))

        return findings

    # ==============================================================
    # Stage 3b: Derivation continuity check
    # ==============================================================

    def _check_derivation_continuity(
        self, equations: list[str]
    ) -> list[Finding]:
        """Detect discontinuities in sequential derivation steps.

        Strategy: Compare the set of variables in consecutive equations.
        A large set difference (variable appearing/disappearing) between
        adjacent steps may indicate a missing step or typo.
        """
        findings: list[Finding] = []

        if len(equations) < 3:
            return findings

        # Extract variable sets for consecutive equations
        prev_vars: set[str] = set()
        for eq_idx, eq in enumerate(equations):
            # Extract all variable-like tokens
            current_vars = set(re.findall(
                r"\\(?:alpha|beta|gamma|delta|epsilon|theta|lambda|mu|sigma|phi|omega)"
                r"(?:_\{?[a-zA-Z0-9,]+\}?)?",
                eq,
            ))
            # Add Latin subscripted variables
            current_vars.update(re.findall(
                r"[A-Za-z]_\{?[a-zA-Z0-9,]+\}?",
                eq,
            ))

            if prev_vars and current_vars:
                # Check for suspicious disappearances
                disappeared = prev_vars - current_vars
                appeared = current_vars - prev_vars

                # Heuristic: if >60% of previous vars vanished and new ones
                # appeared, this might indicate a derivation jump
                if len(prev_vars) >= 3 and len(disappeared) >= 3:
                    disappear_ratio = len(disappeared) / len(prev_vars)
                    if disappear_ratio > 0.6 and appeared:
                        # Only flag if this isn't a clear "therefore" final step
                        # (where only the conclusion variable remains)
                        if len(current_vars) > 2:
                            findings.append(Finding(
                                category="methodology",
                                severity="minor",
                                description=(
                                    f"Large variable set change between "
                                    f"equations {eq_idx} and {eq_idx + 1}: "
                                    f"{len(disappeared)} variables disappeared, "
                                    f"{len(appeared)} new variables appeared. "
                                    f"Possible missing intermediate step."
                                ),
                                evidence=(
                                    f"Disappeared: {', '.join(sorted(disappeared)[:5])}. "
                                    f"Appeared: {', '.join(sorted(appeared)[:5])}"
                                ),
                                suggestion=(
                                    "Consider adding an intermediate step or "
                                    "explicit explanation of the variable substitution."
                                ),
                                location=f"Appendix: equation {eq_idx + 1}",
                                confidence=0.45,
                                skill_source="appendix_math_audit",
                            ))

            prev_vars = current_vars

        return findings

    # ==============================================================
    # Stage 3c: Definition-usage mismatch
    # ==============================================================

    def _check_definition_usage_mismatch(
        self, full_text: str, symbols: list[MathSymbol]
    ) -> list[Finding]:
        """Detect mismatches between variable definitions and usage.

        Looks for patterns like:
          - Main text defines Y_{it} (panel data), appendix uses Y_i (loses time)
          - Definition says "let X ∈ R^n", but usage shows X ∈ R^{n×m}
        """
        findings: list[Finding] = []

        # Find explicit definitions: "let X_sub", "define X_sub", "where X_sub"
        definition_patterns = [
            r"(?:[Ll]et|[Dd]efine|[Ww]here|[Dd]enote)\s+"
            r"(\\?[A-Za-z](?:\\[a-z]+)?)"
            r"(?:_\{([^}]*)\}|_([a-zA-Z0-9]))",
        ]

        definitions: dict[str, str] = {}  # base -> defined subscript
        for pattern in definition_patterns:
            for match in re.finditer(pattern, full_text):
                base = match.group(1)
                subscript = match.group(2) or match.group(3) or ""
                if subscript:
                    definitions[base] = subscript

        if not definitions:
            return findings

        # Compare defined subscript structure against usage
        # Group appendix symbols by base
        by_base: dict[str, list[MathSymbol]] = defaultdict(list)
        for sym in symbols:
            by_base[sym.base].append(sym)

        for base, defined_sub in definitions.items():
            if base not in by_base:
                continue

            usage_subs = Counter(s.subscript for s in by_base[base] if s.subscript)

            # Check if usage frequently deviates from definition
            if defined_sub not in usage_subs and usage_subs:
                # The defined subscript doesn't appear at all in usage
                most_common_usage = usage_subs.most_common(1)[0]
                findings.append(Finding(
                    category="methodology",
                    severity="major",
                    description=(
                        f"Variable '{base}' defined with subscript "
                        f"'{{{defined_sub}}}' in text, but appendix "
                        f"consistently uses '{{{most_common_usage[0]}}}' "
                        f"({most_common_usage[1]} times). "
                        f"Possible dimension mismatch."
                    ),
                    evidence=(
                        f"Definition: {base}_{{{defined_sub}}}. "
                        f"Usage: {base}_{{{most_common_usage[0]}}} "
                        f"(×{most_common_usage[1]})"
                    ),
                    suggestion=(
                        f"Verify that the subscript change from "
                        f"'{defined_sub}' to '{most_common_usage[0]}' is "
                        f"intentional. If the time dimension was dropped, "
                        f"explain why."
                    ),
                    location="Appendix (definition-usage mismatch)",
                    confidence=0.65,
                    skill_source="appendix_math_audit",
                ))

        return findings

    # ==============================================================
    # Helpers
    # ==============================================================

    def _summarize_symbols(
        self, symbols: list[MathSymbol]
    ) -> dict[str, list[str]]:
        """Summarize the symbol registry for output_data."""
        summary: dict[str, list[str]] = defaultdict(list)
        for sym in symbols:
            key = sym.base
            form = f"{sym.base}_{{{sym.subscript}}}" if sym.subscript else sym.base
            if sym.superscript:
                form += f"^{{{sym.superscript}}}"
            if form not in summary[key]:
                summary[key].append(form)
        return dict(summary)

    # ==============================================================
    # Stage 3d: Sequential subscript error detection
    # ==============================================================

    def _check_sequential_subscript_errors(
        self, equations: list[str]
    ) -> list[Finding]:
        """Detect sequence-breaking subscript errors in numbered equations.

        Target scenario (G005-003):
          Equation (43): theta_1 for sector 1's parameter → correct
          Equation (44): theta_1 for sector 2's parameter → WRONG (should be theta_2)
          Equation (45): theta_2 in free entry condition → correct

        Detection strategy:
          1. Find sequences of equations that use the SAME base variable with
             NUMERIC subscripts (indicating indexed sectors/groups/periods)
          2. Check if the subscript sequence is monotonically progressing or
             follows a logical pattern
          3. Flag interruptions where the pattern breaks (e.g., 1,1,2 instead
             of 1,2,2 or 1,2,3)

        This is a COMPLEMENTARY check to _check_subscript_consistency:
          - subscript_consistency detects "dominant vs minority" (statistical)
          - THIS METHOD detects "sequence breaks" (positional/contextual)
        """
        findings: list[Finding] = []

        if len(equations) < 3:
            return findings

        # Extract (equation_index, base_variable, numeric_subscript) triples
        # for variables with purely numeric subscripts
        numeric_sub_pattern = (
            r"(\\(?:alpha|beta|gamma|delta|epsilon|theta|lambda|mu|sigma|phi|omega"
            r"|vartheta|kappa|rho|tau|psi|chi|xi|nu|eta|zeta))"
            r"_\{?(\d+)\}?"
        )
        latin_sub_pattern = r"([A-Za-z])_\{?(\d+)\}?"

        # Build per-equation subscript records
        eq_records: list[list[tuple[str, int]]] = []
        for eq in equations:
            record: list[tuple[str, int]] = []
            for match in re.finditer(numeric_sub_pattern, eq):
                base = match.group(1)
                sub_num = int(match.group(2))
                record.append((base, sub_num))
            for match in re.finditer(latin_sub_pattern, eq):
                base = match.group(1)
                sub_num = int(match.group(2))
                # Skip common non-sector subscripts (single-char vars like x_1)
                if base.lower() in ("x", "y", "z", "a", "b", "c", "n", "k"):
                    continue
                record.append((base, sub_num))
            eq_records.append(record)

        # Look for sequential patterns across consecutive equations
        # Strategy: track base variables that appear in a window of 3+ equations
        # with numeric subscripts, and check for sequence logic

        for window_start in range(len(eq_records) - 2):
            window = eq_records[window_start:window_start + 3]

            # Collect all base variables seen in this window
            bases_in_window: dict[str, list[tuple[int, int]]] = defaultdict(list)
            for offset, record in enumerate(window):
                for base, sub_num in record:
                    bases_in_window[base].append((offset, sub_num))

            for base, occurrences in bases_in_window.items():
                if len(occurrences) < 3:
                    continue

                # Extract the subscript sequence in order
                sub_sequence = [sub for _, sub in occurrences]

                # Check for "expected progression broken" pattern:
                # If we see subscripts like [1, 1, 2] where context suggests
                # it should be [1, 2, 2] or [1, 2, 3]
                unique_subs = sorted(set(sub_sequence))
                if len(unique_subs) < 2:
                    continue  # All same subscript — no progression to check

                # Check: subscripts should generally increase or stay same
                # within a progression. Look for "reversion" — a higher subscript
                # followed by a lower one, then higher again.
                for i in range(1, len(sub_sequence) - 1):
                    prev_s = sub_sequence[i - 1]
                    curr_s = sub_sequence[i]
                    next_s = sub_sequence[i + 1]

                    # Pattern: subscript goes DOWN then back UP
                    # e.g., theta_2 → theta_1 → theta_2
                    # This suggests the middle occurrence is a typo
                    if prev_s > curr_s < next_s and prev_s == next_s:
                        eq_idx = window_start + occurrences[i][0]
                        findings.append(Finding(
                            category="methodology",
                            severity="major",
                            description=(
                                f"Sequential subscript error in '{base}': "
                                f"subscript sequence [..., {prev_s}, {curr_s}, "
                                f"{next_s}, ...] breaks the expected pattern. "
                                f"The middle occurrence ({base}_{{{curr_s}}}) "
                                f"in equation {eq_idx + 1} is likely a typo — "
                                f"expected {base}_{{{prev_s}}} based on context."
                            ),
                            evidence=(
                                f"Equation {window_start + 1}: "
                                f"{base}_{{{sub_sequence[0]}}} | "
                                f"Equation {window_start + 2}: "
                                f"{base}_{{{sub_sequence[1]}}} | "
                                f"Equation {window_start + 3}: "
                                f"{base}_{{{sub_sequence[2]}}}"
                            ),
                            suggestion=(
                                f"Check whether {base}_{{{curr_s}}} in "
                                f"equation {eq_idx + 1} should be "
                                f"{base}_{{{prev_s}}} (matching the surrounding "
                                f"context)."
                            ),
                            location=f"Appendix: equation {eq_idx + 1}",
                            confidence=0.80,
                            skill_source="appendix_math_audit",
                        ))

                    # Pattern: subscript stays same when it should increment
                    # e.g., theta_1 → theta_1 → theta_2 in a "sector 1, 2, 3"
                    # context. The second theta_1 might should be theta_2.
                    elif (
                        curr_s == prev_s
                        and next_s == curr_s + 1
                        and i == 1  # Middle of a 3-equation window
                    ):
                        # Additional check: are equations discussing different
                        # sectors/groups? Look for "sector" or ordinal indicators
                        eq_text = equations[window_start + 1] if (
                            window_start + 1 < len(equations)
                        ) else ""
                        # If the equation context is very short, this might be
                        # a numbering issue
                        eq_idx = window_start + occurrences[i][0]
                        findings.append(Finding(
                            category="methodology",
                            severity="minor",
                            description=(
                                f"Possible subscript stagnation in '{base}': "
                                f"sequence [{prev_s}, {curr_s}, {next_s}] "
                                f"suggests the middle equation may need "
                                f"{base}_{{{curr_s + 1}}} instead of "
                                f"{base}_{{{curr_s}}} if each equation "
                                f"describes a different sector/group."
                            ),
                            evidence=(
                                f"Equation {window_start + 1}: "
                                f"{base}_{{{prev_s}}} | "
                                f"Equation {window_start + 2}: "
                                f"{base}_{{{curr_s}}} (same as prev) | "
                                f"Equation {window_start + 3}: "
                                f"{base}_{{{next_s}}} (increments)"
                            ),
                            suggestion=(
                                f"If equations {window_start + 1}-"
                                f"{window_start + 3} describe different "
                                f"sectors/groups sequentially, the middle "
                                f"equation likely needs {base}_{{{curr_s + 1}}}."
                            ),
                            location=f"Appendix: equation {eq_idx + 1}",
                            confidence=0.70,
                            skill_source="appendix_math_audit",
                        ))

        return findings
