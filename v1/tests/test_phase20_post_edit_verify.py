"""
tests/test_phase20_post_edit_verify.py — Phase 20 单元测试

验证三层零成本验证逻辑：
1. 交叉引用一致性（Layer 1）
2. 写作风格漂移（Layer 2）
3. AI 模式回归（Layer 3）

以及集成到 Harness 后的 voice profile 累积。
"""

import pytest
from core.post_edit_verify import (
    check_consistency,
    check_voice_drift,
    check_ai_regression,
    verify_edit,
    format_verification_feedback,
    extract_voice,
    VoiceFingerprint,
    VerificationResult,
)


# ============================================================
# Layer 1: 交叉引用一致性
# ============================================================

class TestConsistencyCheck:
    """Layer 1: 交叉引用一致性验证。"""

    def test_valid_references_pass(self):
        """已有定义的引用应通过。"""
        new_text = "As shown in Figure 1, the results in Table 2 confirm our hypothesis."
        all_text = "Figure 1: Architecture overview.\nTable 2: Main results.\n" + new_text
        passed, issues = check_consistency(new_text, all_text)
        assert passed is True
        assert issues == []

    def test_broken_figure_reference(self):
        """引用不存在的 Figure 应报错。"""
        new_text = "As shown in Figure 5, the improvement is significant."
        all_text = "Figure 1: Architecture.\nFigure 2: Results.\n" + new_text
        passed, issues = check_consistency(new_text, all_text)
        assert passed is False
        assert any("Figure 5" in i for i in issues)

    def test_broken_table_reference(self):
        """引用不存在的 Table 应报错。"""
        new_text = "See Table 9 for ablation results."
        # all_text 只包含 Table 1 和 Table 2 的定义，不包含 new_text 本身
        all_text = "Table 1: Main results.\nTable 2: Ablation.\nSome other content here."
        passed, issues = check_consistency(new_text, all_text)
        assert passed is False
        assert any("Table 9" in i for i in issues)

    def test_no_references_passes(self):
        """没有引用的文本应通过。"""
        new_text = "Our method achieves state-of-the-art performance on three benchmarks."
        all_text = "Some paper content.\n" + new_text
        passed, issues = check_consistency(new_text, all_text)
        assert passed is True

    def test_case_insensitive_references(self):
        """引用匹配不区分大小写。"""
        new_text = "As shown in figure 1 and TABLE 2."
        all_text = "Figure 1: diagram.\nTable 2: data.\n" + new_text
        passed, issues = check_consistency(new_text, all_text)
        assert passed is True

    def test_fig_abbreviation(self):
        """缩写 Fig. 也应被识别。"""
        new_text = "See Fig. 3 for details."
        all_text = "Fig. 3: Illustration.\n" + new_text
        passed, issues = check_consistency(new_text, all_text)
        assert passed is True


# ============================================================
# Layer 2: 写作风格漂移
# ============================================================

class TestVoiceDrift:
    """Layer 2: 写作风格漂移检测。"""

    def test_similar_style_passes(self):
        """风格相似的修改应通过。"""
        old = (
            "The proposed method achieves competitive performance. "
            "We observe that the model may generalize well to unseen domains. "
            "Results indicate significant improvements across all metrics."
        )
        new = (
            "The proposed approach achieves competitive performance. "
            "We observe that the model could generalize well to novel domains. "
            "Results suggest meaningful improvements across all benchmarks."
        )
        passed, warnings = check_voice_drift(old, new)
        assert passed is True

    def test_drastically_different_style_warns(self):
        """风格大幅偏离应产生警告。"""
        # 原文：短句、主动语态
        old = "We test. We find. It works. Done."
        # 修改后：长句、被动语态、大量限定词
        new = (
            "The experimental results were thoroughly examined and it was determined "
            "that the proposed methodology, which was subsequently validated through "
            "rigorous statistical analysis, could potentially indicate that the approach "
            "might be applicable to a broader range of scenarios than was previously "
            "assumed in the preliminary investigations."
        )
        passed, warnings = check_voice_drift(old, new)
        assert passed is False
        assert len(warnings) > 0

    def test_with_voice_profile(self):
        """可以与累积的 voice profile 对比。"""
        # 构造一个 profile：句长约 6，无被动，低 hedge
        profile = VoiceFingerprint(
            avg_sentence_length=6.0,
            sentence_length_std=3.0,
            passive_ratio=0.0,
            hedge_frequency=0.0,
            total_words_analyzed=1000,
        )
        # 新文本也是短句、主动、无 hedge，句长接近 profile
        new = (
            "We propose a new method. "
            "It achieves good results. "
            "Our experiments confirm this."
        )
        passed, warnings = check_voice_drift("", new, voice_profile=profile)
        assert passed is True

    def test_empty_profile_passes(self):
        """空 profile 不应触发任何警告。"""
        passed, warnings = check_voice_drift("old text", "new text", voice_profile=VoiceFingerprint())
        assert passed is True


# ============================================================
# Layer 3: AI 模式回归
# ============================================================

class TestAIRegression:
    """Layer 3: AI 模式回归检测。"""

    def test_no_ai_signals_passes(self):
        """无 AI 信号的修改应通过。"""
        old = "We propose a simple and effective method."
        new = "We introduce a straightforward and effective approach."
        passed, issues = check_ai_regression(old, new)
        assert passed is True
        assert issues == []

    def test_new_ai_signal_detected(self):
        """引入新 AI 信号应被检测到。"""
        old = "We examine the relationship between variables."
        new = "We delve into the multifaceted landscape of variable relationships."
        passed, issues = check_ai_regression(old, new)
        assert passed is False
        assert any("delve" in i.lower() or "multifaceted" in i.lower() or "landscape" in i.lower() for i in issues)

    def test_existing_ai_signal_not_penalized(self):
        """原文已有的 AI 信号不算新增。"""
        old = "This method leverages the power of neural networks."
        new = "This approach leverages deep neural architectures."
        passed, issues = check_ai_regression(old, new)
        assert passed is True  # "leverages" 原来就有

    def test_removed_ai_signal_is_improvement(self):
        """移除 AI 信号不应报错（是改善）。"""
        old = "We delve into the multifaceted tapestry of machine learning."
        new = "We study the diverse aspects of machine learning."
        passed, issues = check_ai_regression(old, new)
        assert passed is True

    def test_multiple_new_signals(self):
        """引入多个 AI 信号应全部列出。"""
        old = "The method works well."
        new = "This groundbreaking method underscores a paradigm shift that paves the way for future work."
        passed, issues = check_ai_regression(old, new)
        assert passed is False
        assert len(issues) >= 2  # 至少检测到 2 个


# ============================================================
# 集成测试：verify_edit 完整流程
# ============================================================

class TestVerifyEdit:
    """完整三层验证流程。"""

    def test_clean_edit_passes_all(self):
        """干净的修改应三层全通过。"""
        old = "Our method achieves 95% accuracy on CIFAR-10, as shown in Table 1."
        new = "Our method achieves 95.2% accuracy on CIFAR-10, as shown in Table 1."
        all_text = "Table 1: Main results.\n" + new
        result = verify_edit("results", old, new, all_text)
        assert result.passed is True
        assert result.consistency_ok is True
        assert result.voice_drift_ok is True
        assert result.ai_regression_ok is True

    def test_broken_ref_fails(self):
        """悬空引用导致验证失败。"""
        old = "Results in Table 1 show improvement."
        new = "Results in Table 5 show significant improvement."
        # all_text 只包含 Table 1 的定义，不出现 "Table 5" 这样的模式
        all_text = "Table 1: data.\nSome other paragraph about the experiment."
        result = verify_edit("results", old, new, all_text)
        assert result.passed is False
        assert result.consistency_ok is False

    def test_ai_regression_fails(self):
        """AI 回归导致验证失败。"""
        old = "We study the effect of parameters."
        new = "We delve into the multifaceted effect of parameters."
        result = verify_edit("method", old, new, "")
        assert result.passed is False
        assert result.ai_regression_ok is False

    def test_voice_drift_only_warns(self):
        """风格漂移只是警告，不阻塞。"""
        old = "We test. It works. Done."
        new = (
            "The comprehensive experimental evaluation was meticulously conducted "
            "across numerous benchmark datasets to thoroughly validate the hypothesis."
        )
        result = verify_edit("experiments", old, new, "")
        # voice drift 是非阻塞的，只要没有 AI regression 和引用问题就 passed
        if result.ai_regression_ok and result.consistency_ok:
            assert result.passed is True
        assert result.voice_drift_ok is False
        assert len(result.warnings) > 0


# ============================================================
# 格式化输出测试
# ============================================================

class TestFormatFeedback:
    """验证反馈格式化。"""

    def test_all_pass_concise(self):
        """全通过时输出应简洁。"""
        result = VerificationResult(
            passed=True,
            consistency_ok=True,
            voice_drift_ok=True,
            ai_regression_ok=True,
        )
        output = format_verification_feedback(result, "introduction")
        assert "✓" in output
        assert "验证通过" in output

    def test_issues_shown(self):
        """有问题时应显示具体问题。"""
        result = VerificationResult(
            passed=False,
            consistency_ok=False,
            voice_drift_ok=True,
            ai_regression_ok=True,
            issues=["Broken reference: Figure 5 not found"],
        )
        output = format_verification_feedback(result, "results")
        assert "Figure 5" in output
        assert "✗" in output


# ============================================================
# Voice Profile 提取测试
# ============================================================

class TestVoiceExtraction:
    """VoiceFingerprint 提取准确性。"""

    def test_basic_extraction(self):
        """基本指标提取正确。"""
        text = (
            "We propose a novel method. "
            "The method is simple yet effective. "
            "Results show improvements. "
            "This may indicate broader applicability."
        )
        fp = extract_voice(text)
        assert fp.total_words_analyzed > 0
        assert fp.avg_sentence_length > 0
        assert 0 <= fp.passive_ratio <= 1
        assert fp.hedge_frequency >= 0

    def test_passive_detection(self):
        """被动语态检测。"""
        # 全被动
        text = "The method was tested. The results were evaluated. The model was trained."
        fp = extract_voice(text)
        assert fp.passive_ratio > 0.5

    def test_hedge_detection(self):
        """限定词检测。"""
        text = (
            "This may possibly indicate that the approach could potentially "
            "be relatively effective in somewhat different scenarios."
        )
        fp = extract_voice(text)
        assert fp.hedge_frequency > 3  # 高频限定词

    def test_empty_text(self):
        """空文本应返回零值 fingerprint。"""
        fp = extract_voice("")
        assert fp.total_words_analyzed == 0
        assert fp.avg_sentence_length == 0.0


# ============================================================
# Harness 集成测试
# ============================================================

class TestHarnessIntegration:
    """验证 Harness 中的集成是否正确工作。"""

    def test_voice_profile_builds_on_read(self, tmp_path):
        """读 section 时应自动构建 voice profile。"""
        from core.harness import Harness

        h = Harness(memory_dir=str(tmp_path / ".memory"))
        h._paper_loaded = True
        h.state.paper_sections = {
            "introduction": (
                "We propose a novel method for few-shot learning. "
                "The method leverages contrastive learning to improve generalization. "
                "Our experiments demonstrate significant improvements over baselines. "
                "The key insight is that adaptive margins help distinguish classes. "
                "We evaluate on multiple standard benchmarks and report consistent gains."
            ) * 3,  # 重复以达到 200 字符阈值
        }
        # 模拟 Agent 读取 section
        h._tool_read_section({"section": "introduction"})
        assert h.state.voice_profile is not None
        assert h.state.voice_profile.total_words_analyzed > 0

    def test_edit_returns_verification_feedback(self, tmp_path):
        """修改 section 后应返回验证反馈。"""
        from core.harness import Harness

        h = Harness(memory_dir=str(tmp_path / ".memory"))
        h._paper_loaded = True
        h.state.paper_sections = {
            "introduction": (
                "We propose a simple method for classification. "
                "The method uses a standard architecture. "
                "Table 1 shows results on three benchmarks. "
                "Our approach outperforms the baselines significantly. "
                "We release our code for reproducibility."
            ) * 2,
        }
        # 先读以建立 voice profile
        h._tool_read_section({"section": "introduction"})

        # 修改：引入 AI 信号
        result = h._tool_edit_section({
            "section": "introduction",
            "new_content": "We delve into a multifaceted method. Table 1 shows results.",
            "reason": "test",
        })
        assert "已修改" in result
        # 应该有 AI 回归信号
        assert "AI" in result or "delve" in result.lower() or "multifaceted" in result.lower()

    def test_clean_edit_shows_pass(self, tmp_path):
        """干净的修改应显示验证通过。"""
        from core.harness import Harness

        h = Harness(memory_dir=str(tmp_path / ".memory"))
        h._paper_loaded = True
        h.state.paper_sections = {
            "abstract": "Our method achieves 95% accuracy on CIFAR-10 benchmark.",
        }
        result = h._tool_edit_section({
            "section": "abstract",
            "new_content": "Our method achieves 95.2% accuracy on the CIFAR-10 benchmark.",
            "reason": "update number",
        })
        assert "✓" in result or "验证通过" in result
