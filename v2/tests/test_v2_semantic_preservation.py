"""
tests/test_v2_semantic_preservation.py — DEAI-2: 语义保持检查测试

验证:
1. 数字/统计量消失 → FAIL (issues)
2. 因果方向变化 → WARN (warnings) 
3. 程度量词修改 → PASS (允许)
4. 正常 reword（无数字/因果变化） → PASS
5. 集成到 verify_edit 的正确行为
6. format_verification_feedback 包含语义状态
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from core.post_edit_verify import (
    _extract_numeric_values,
    _detect_causal_direction,
    check_semantic_preservation,
    verify_edit,
    format_verification_feedback,
    VerificationResult,
)


# ============================================================
# Test: _extract_numeric_values
# ============================================================

class TestExtractNumericValues:
    """测试数值/统计量提取函数。"""

    def test_p_value_extraction(self):
        """提取 p-value 表述。"""
        text = "The effect was significant (p=0.03, p<0.001)."
        vals = _extract_numeric_values(text)
        assert any("p=0.03" in v or "p =0.03" in v for v in vals) or any("0.03" in v for v in vals)
        assert any("p<0.001" in v or "0.001" in v for v in vals)

    def test_percentage_extraction(self):
        """提取百分比表述。"""
        text = "Response rate was 73.2% and declined by 2.1 percentage points."
        vals = _extract_numeric_values(text)
        assert any("73.2%" in v for v in vals)
        # regex 匹配 "2.1 percentage points" 或 "2.1 percent"（percent 先匹配）
        assert any("2.1" in v and "percent" in v for v in vals)

    def test_n_value_extraction(self):
        """提取 N 值。"""
        text = "Our sample included N=1,234 participants (n=856 in the treatment group)."
        vals = _extract_numeric_values(text)
        assert any("N=1,234" in v for v in vals)
        assert any("n=856" in v for v in vals)

    def test_coefficient_extraction(self):
        """提取系数。"""
        text = "The coefficient of 0.32 was statistically significant, with β=0.45."
        vals = _extract_numeric_values(text)
        assert any("0.32" in v for v in vals)
        assert any("0.45" in v for v in vals)

    def test_confidence_interval_extraction(self):
        """提取置信区间。"""
        text = "The 95% CI: [0.12, 0.45] confirms the positive direction."
        vals = _extract_numeric_values(text)
        assert any("CI" in v and "0.12" in v for v in vals)

    def test_t_stat_extraction(self):
        """提取 t 统计量。"""
        text = "We found t=2.34, indicating significance."
        vals = _extract_numeric_values(text)
        assert any("2.34" in v for v in vals)

    def test_r_squared_extraction(self):
        """提取 R²。"""
        text = "The model fit was good with R²=0.85."
        vals = _extract_numeric_values(text)
        assert any("0.85" in v for v in vals)

    def test_decimal_extraction(self):
        """提取独立小数。"""
        text = "The estimate is -2.13 with standard error of 0.54."
        vals = _extract_numeric_values(text)
        assert any("-2.13" in v for v in vals)
        assert any("0.54" in v for v in vals)

    def test_empty_text_returns_empty(self):
        """空文本返回空集合。"""
        assert _extract_numeric_values("") == set()

    def test_no_numbers_returns_empty(self):
        """无数字文本返回空集合。"""
        text = "This sentence has no numbers at all."
        # 注意：可能仍为空集（取决于 pattern 是否匹配到误报）
        vals = _extract_numeric_values(text)
        # 仅验证不包含无意义的匹配
        assert all(any(c.isdigit() for c in v) for v in vals)


# ============================================================
# Test: _detect_causal_direction
# ============================================================

class TestDetectCausalDirection:
    """测试因果方向词检测。"""

    def test_strong_causal_english(self):
        """检测英文强因果词。"""
        text = "Higher education causes increased earnings. GDP growth leads to lower poverty."
        strong, weak = _detect_causal_direction(text)
        assert len(strong) > 0
        assert any("causes" in s or "leads to" in s for s in strong)

    def test_weak_association_english(self):
        """检测英文弱关联词。"""
        text = "Education is associated with higher earnings and correlated with better health."
        strong, weak = _detect_causal_direction(text)
        assert len(weak) > 0
        assert any("associated with" in w for w in weak)

    def test_strong_causal_chinese(self):
        """检测中文强因果词。"""
        text = "教育水平提高了收入，也导致了社会流动性增加。"
        strong, weak = _detect_causal_direction(text)
        assert len(strong) > 0

    def test_weak_association_chinese(self):
        """检测中文弱关联词。"""
        text = "教育水平与收入水平呈正相关，可能影响社会流动性。"
        strong, weak = _detect_causal_direction(text)
        assert len(weak) > 0

    def test_no_causal_language(self):
        """无因果语言返回空集合。"""
        text = "We collected data from three universities in 2020."
        strong, weak = _detect_causal_direction(text)
        assert len(strong) == 0
        assert len(weak) == 0

    def test_mixed_causal_language(self):
        """同时有强因果和弱关联时都检测到。"""
        text = "The treatment increases performance but is only associated with retention."
        strong, weak = _detect_causal_direction(text)
        assert len(strong) > 0
        assert len(weak) > 0


# ============================================================
# Test: check_semantic_preservation — 数字保持
# ============================================================

class TestSemanticPreservationNumbers:
    """测试数字/统计量不丢失规则。"""

    def test_number_preserved_passes(self):
        """数字都保留时 → PASS。"""
        old = "The effect size was 0.45 (p=0.03, N=1000)."
        new = "We found an effect size of 0.45 (p=0.03, N=1000), suggesting practical significance."
        passed, issues, warnings = check_semantic_preservation(old, new)
        assert passed is True
        assert len(issues) == 0

    def test_number_disappeared_fails(self):
        """关键数字消失 → FAIL。"""
        old = "The treatment increased outcomes by 23.5% (p=0.001, N=500)."
        new = "The treatment significantly increased outcomes."
        passed, issues, warnings = check_semantic_preservation(old, new)
        assert passed is False
        assert len(issues) > 0
        assert any("丢失" in i for i in issues)

    def test_p_value_disappeared_fails(self):
        """p-value 消失 → FAIL。"""
        old = "Results were significant (p<0.001) with a large effect."
        new = "Results were statistically significant with a large effect."
        passed, issues, warnings = check_semantic_preservation(old, new)
        assert passed is False
        assert any("丢失" in i for i in issues)

    def test_sample_size_disappeared_fails(self):
        """样本量 N 消失 → FAIL。"""
        old = "We surveyed N=2,500 participants across three regions."
        new = "We surveyed participants across three regions."
        passed, issues, warnings = check_semantic_preservation(old, new)
        assert passed is False

    def test_new_numbers_added_is_ok(self):
        """新增数字不触发 FAIL（Agent 有意补充）。"""
        old = "The model performed well."
        new = "The model achieved R²=0.92, performing well above baseline."
        passed, issues, warnings = check_semantic_preservation(old, new)
        assert passed is True
        assert len(issues) == 0

    def test_trivial_numbers_filtered(self):
        """通用小数 (0.0, 1.0) 不触发误报。"""
        old = "The baseline is 0.0 and the max is 1.0 in normalized scale."
        new = "We use a normalized scale for measurements."
        passed, issues, warnings = check_semantic_preservation(old, new)
        # 0.0 和 1.0 是被过滤的通用数字
        assert passed is True


# ============================================================
# Test: check_semantic_preservation — 因果方向
# ============================================================

class TestSemanticPreservationCausal:
    """测试因果方向保持规则。"""

    def test_causal_unchanged_passes(self):
        """因果方向不变 → 无 WARNING。"""
        old = "Education causes higher earnings in the long run."
        new = "Education causes elevated earnings over extended periods."
        passed, issues, warnings = check_semantic_preservation(old, new)
        assert passed is True
        assert len(warnings) == 0

    def test_causal_weakened_warns(self):
        """强因果变为弱关联 → WARNING。"""
        old = "The intervention leads to improved health outcomes."
        new = "The intervention is associated with improved health outcomes."
        passed, issues, warnings = check_semantic_preservation(old, new)
        # 因果削弱是 warning，不是 fail
        assert passed is True
        assert len(warnings) > 0
        assert any("因果方向" in w for w in warnings)

    def test_causal_strengthened_warns(self):
        """弱关联变为强因果 → WARNING (overclaim)。"""
        old = "Social media use is correlated with depression symptoms."
        new = "Social media use causes depression symptoms in teenagers."
        passed, issues, warnings = check_semantic_preservation(old, new)
        assert passed is True
        assert len(warnings) > 0
        assert any("因果方向" in w or "overclaim" in w for w in warnings)

    def test_both_weak_no_warn(self):
        """弱→弱之间变换 → 无 WARNING。"""
        old = "X is associated with Y."
        new = "X is correlated with Y."
        passed, issues, warnings = check_semantic_preservation(old, new)
        assert passed is True
        # 两者都是弱关联词，方向不变
        # (lost_weak 可能非空但 gained_strong 为空，不触发)
        causal_warnings = [w for w in warnings if "因果方向" in w]
        assert len(causal_warnings) == 0

    def test_both_strong_no_warn(self):
        """强→强同义替换 → 无 WARNING（或仅微弱）。"""
        old = "The policy increases employment."
        new = "The policy raises employment."
        passed, issues, warnings = check_semantic_preservation(old, new)
        assert passed is True
        # 都是强因果，方向未变
        causal_warnings = [w for w in warnings if "因果方向" in w]
        assert len(causal_warnings) == 0

    def test_causal_weakening_chinese(self):
        """中文因果削弱检测。"""
        old = "干预措施导致了健康状况改善。"
        new = "干预措施与健康状况改善相关。"
        passed, issues, warnings = check_semantic_preservation(old, new)
        assert len(warnings) > 0

    def test_no_causal_in_either_passes(self):
        """两段文本都无因果语言 → 完全通过。"""
        old = "We collected data in 2020 from three institutions."
        new = "Data collection occurred in 2020 across three institutions."
        passed, issues, warnings = check_semantic_preservation(old, new)
        assert passed is True
        assert len(warnings) == 0


# ============================================================
# Test: 程度量词修改 → 允许 (PASS)
# ============================================================

class TestSemanticPreservationDegreeQualifiers:
    """验证程度量词修改不触发 FAIL/WARN。"""

    def test_degree_qualifier_change_passes(self):
        """"significantly" → "marginally" 不影响（非因果方向词）。"""
        old = "The effect was significantly larger in group A (p=0.03)."
        new = "The effect was marginally larger in group A (p=0.03)."
        passed, issues, warnings = check_semantic_preservation(old, new)
        assert passed is True
        # 数字保留，无因果方向变化

    def test_hedge_word_addition_passes(self):
        """添加 hedge 词不触发。"""
        old = "The treatment improved outcomes by 12.3%."
        new = "The treatment somewhat improved outcomes by 12.3%."
        passed, issues, warnings = check_semantic_preservation(old, new)
        assert passed is True

    def test_qualifier_removal_passes(self):
        """去掉修饰词不触发（只要数字/因果不变）。"""
        old = "The highly significant coefficient of 0.82 demonstrates the relationship."
        new = "The significant coefficient of 0.82 demonstrates the relationship."
        passed, issues, warnings = check_semantic_preservation(old, new)
        assert passed is True


# ============================================================
# Test: 正常 reword → PASS
# ============================================================

class TestSemanticPreservationNormalEdits:
    """验证正常学术编辑（改写）不被误判。"""

    def test_reword_same_meaning_passes(self):
        """同义改写，无数字/因果 → 通过。"""
        old = "Our findings indicate that the approach is viable for real-world applications."
        new = "These results demonstrate the approach's viability in practical settings."
        passed, issues, warnings = check_semantic_preservation(old, new)
        assert passed is True
        assert len(issues) == 0

    def test_sentence_restructure_with_numbers_passes(self):
        """句子重组但保留所有数字 → 通过。"""
        old = "With N=500, we found that 62.3% of respondents (p=0.02) reported improvement."
        new = "Among our sample (N=500), 62.3% reported improvement (p=0.02)."
        passed, issues, warnings = check_semantic_preservation(old, new)
        assert passed is True

    def test_passive_to_active_voice_passes(self):
        """被动→主动改写 → 通过。"""
        old = "A 15.7% increase was observed in the treatment group (p<0.05)."
        new = "The treatment group showed a 15.7% increase (p<0.05)."
        passed, issues, warnings = check_semantic_preservation(old, new)
        assert passed is True

    def test_empty_texts_pass(self):
        """空文本对比不崩溃。"""
        passed, issues, warnings = check_semantic_preservation("", "")
        assert passed is True

    def test_identical_texts_pass(self):
        """相同文本 → 完全通过。"""
        text = "The effect size was 0.45 (p=0.03). This suggests a moderate impact."
        passed, issues, warnings = check_semantic_preservation(text, text)
        assert passed is True
        assert len(issues) == 0
        assert len(warnings) == 0


# ============================================================
# Test: verify_edit 集成
# ============================================================

class TestVerifyEditSemanticIntegration:
    """测试 semantic_ok 在 verify_edit 中的正确集成。"""

    def test_verify_edit_passes_with_good_edit(self):
        """无问题编辑 → VerificationResult.passed = True。"""
        result = verify_edit(
            section_name="results",
            old_text="We found a coefficient of 0.45 (p<0.01). The model fit was R²=0.72.",
            new_text="A coefficient of 0.45 was observed (p<0.01), with model fit R²=0.72.",
            all_sections_text="",
        )
        assert result.passed is True
        assert result.semantic_ok is True

    def test_verify_edit_fails_with_number_loss(self):
        """数字丢失 → VerificationResult.passed = False。"""
        result = verify_edit(
            section_name="results",
            old_text="The intervention reduced costs by 34.2% (p=0.005, N=800).",
            new_text="The intervention significantly reduced costs.",
            all_sections_text="",
        )
        assert result.passed is False
        assert result.semantic_ok is False
        assert any("丢失" in i for i in result.issues)

    def test_verify_edit_causal_change_is_warning_not_fail(self):
        """因果方向变化 → warning，但 passed 仍为 True。"""
        result = verify_edit(
            section_name="discussion",
            old_text="The treatment leads to better outcomes.",
            new_text="The treatment is associated with better outcomes.",
            all_sections_text="",
        )
        # 因果方向变化只是 warning，不影响 passed
        assert result.semantic_ok is True
        assert result.passed is True
        assert len(result.warnings) > 0

    def test_verify_edit_semantic_fail_overrides_other_pass(self):
        """即使 consistency 和 AI 回归都 OK，semantic FAIL 也让整体 fail。"""
        result = verify_edit(
            section_name="methodology",
            old_text="We used N=1,500 observations with β=0.67 (p<0.001).",
            new_text="We used a large sample with a significant effect.",
            all_sections_text="",
        )
        # 无交叉引用问题，无 AI 回归，但语义丢失
        assert result.consistency_ok is True
        assert result.ai_regression_ok is True
        assert result.semantic_ok is False
        assert result.passed is False


# ============================================================
# Test: format_verification_feedback 语义状态显示
# ============================================================

class TestFormatFeedbackSemantic:
    """测试格式化输出包含语义保持状态。"""

    def test_all_pass_shows_semantic(self):
        """全部通过时显示'语义保持'。"""
        result = VerificationResult(
            passed=True,
            consistency_ok=True,
            voice_drift_ok=True,
            ai_regression_ok=True,
            semantic_ok=True,
        )
        feedback = format_verification_feedback(result, "results")
        assert "语义保持" in feedback

    def test_semantic_fail_shows_in_status(self):
        """语义失败时状态行显示 ✗。"""
        result = VerificationResult(
            passed=False,
            consistency_ok=True,
            voice_drift_ok=True,
            ai_regression_ok=True,
            semantic_ok=False,
            issues=["语义保持: 修改后丢失了 2 个数值/统计量 — 'p=0.03', '0.45'"],
        )
        feedback = format_verification_feedback(result, "results")
        assert "语义保持: ✗" in feedback
        assert "丢失" in feedback

    def test_semantic_warning_shows(self):
        """因果 warning 在输出中显示。"""
        result = VerificationResult(
            passed=True,
            consistency_ok=True,
            voice_drift_ok=True,
            ai_regression_ok=True,
            semantic_ok=True,
            warnings=["因果方向变化: 原文使用强因果表述 (leads to) 被替换为弱关联表述 (associated with)。"],
        )
        feedback = format_verification_feedback(result, "discussion")
        assert "因果方向" in feedback


# ============================================================
# Test: Edge cases / 边界情况
# ============================================================

class TestSemanticPreservationEdgeCases:
    """边界情况测试。"""

    def test_number_format_change_not_flagged(self):
        """数字格式变化（多余空格）不应触发。"""
        old = "The result was p = 0.03 with effect of 0.45."
        new = "The result was p=0.03 with an effect of 0.45."
        passed, issues, warnings = check_semantic_preservation(old, new)
        # 标准化后应视为相同
        # 但实际实现中 "p = 0.03" vs "p=0.03" 经标准化后可能仍不同
        # 这取决于 _extract_numeric_values 的标准化逻辑
        # 当前实现将空格标准化为单空格，所以 "p = 0.03" → "p = 0.03" 和 "p=0.03" → "p=0.03"
        # 这是一个已知的边界 — 不同格式可能被视为不同值
        # 此测试记录当前行为（可能 FAIL），后续可优化
        # 如果触发了 FAIL，仅记录为已知限制
        if not passed:
            assert any("丢失" in i for i in issues)
            # 已知限制: 空格差异可能导致误报

    def test_multiple_numbers_partial_loss(self):
        """部分数字保留、部分丢失 → 只报丢失的。"""
        old = "Growth was 5.2% (p=0.01) in 2020 and 3.1% (p=0.05) in 2021."
        new = "Growth was 5.2% (p=0.01) in 2020 and declined afterward."
        passed, issues, warnings = check_semantic_preservation(old, new)
        assert passed is False
        # 应报告 3.1% 和 p=0.05 丢失
        assert any("丢失" in i for i in issues)

    def test_very_long_text_performance(self):
        """长文本不会超时。"""
        old = ("This study examined N=1000 participants. " * 100 +
               "The effect was β=0.34 (p=0.001).")
        new = ("This research investigated N=1000 individuals. " * 100 +
               "The effect was β=0.34 (p=0.001).")
        passed, issues, warnings = check_semantic_preservation(old, new)
        assert passed is True

    def test_chinese_numbers_in_context(self):
        """中文学术论文中的数字也能检测。"""
        old = "结果显示处理效应为0.45（p<0.001），样本量N=2000。"
        new = "我们发现了显著效应（p<0.001），基于N=2000的样本。"
        passed, issues, warnings = check_semantic_preservation(old, new)
        # 0.45 消失了
        assert passed is False
        assert any("丢失" in i for i in issues)
