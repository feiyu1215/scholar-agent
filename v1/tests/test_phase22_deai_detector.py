"""
tests/test_phase22_deai_detector.py — Phase 22: DeAI Detector Unit Tests

验证 core/deai_detector.py 的核心检测能力：
1. 英文 AI 信号检测（cliché、formulaic transitions、promotional 等）
2. 中文 AI 信号检测（套话、宣传式、连接词堆砌等）
3. 句长变异度（burstiness）统计
4. Hard caps 硬上限触发
5. 多维度评分 + 分层判定逻辑
6. 自然文本的假阳性控制
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.deai_detector import (
    detect_ai_signals,
    check_burstiness,
    DetectionResult,
    BurstinessResult,
    AISignal,
    _is_chinese_text,
    _split_sentences,
)


# ============================================================
# Test: Chinese Detection
# ============================================================

def test_is_chinese_text():
    """正确识别中文为主的文本。"""
    assert _is_chinese_text("这是一段中文文本，用于测试检测功能。") is True
    assert _is_chinese_text("This is an English text for testing.") is False
    assert _is_chinese_text("") is False
    # 混合文本，中文 > 30%
    assert _is_chinese_text("这是中文 mixed with some English words 但主要是中文内容。") is True
    print("✓ test_is_chinese_text passed")


def test_english_ai_heavy_text_fails():
    """典型的 AI 生成英文文本应该被判定为 FAIL。"""
    ai_text = (
        "Moreover, this groundbreaking study delves into the multifaceted landscape of neural networks. "
        "Furthermore, it is worth noting that the transformative impact of deep learning serves as a testament to computational advancement. "
        "Additionally, the paradigm-shifting nature of this approach underscores the importance of innovation. "
        "Consequently, the pivotal role of attention mechanisms has been demonstrated in numerous studies. "
        "Nevertheless, the unprecedented performance of transformer architectures remains remarkable. "
        "In conclusion, this revolutionary framework paves the way for future research in this vibrant tapestry of AI."
    )
    result = detect_ai_signals(ai_text)
    
    assert result.verdict == "FAIL", f"Expected FAIL, got {result.verdict}"
    assert result.signal_count > 0, "Expected signals detected"
    assert result.critical_count > 0, "Expected critical signals (clichés/promotional)"
    # Note: overall_score is a weighted average across dimensions; vocabulary gets capped
    # but other clean dimensions keep the average up. The key assertion is verdict=FAIL.
    assert result.overall_score < 0.9, f"Expected score < 0.9, got {result.overall_score}"
    
    # 应该检测到 AI clichés
    signal_types = {s.signal_type for s in result.signals}
    assert "AI_CLICHE" in signal_types, f"Expected AI_CLICHE in {signal_types}"
    
    # 应该检测到 formulaic transitions
    assert "FORMULAIC_TRANSITIONS" in signal_types, f"Expected FORMULAIC_TRANSITIONS in {signal_types}"
    
    print(f"✓ test_english_ai_heavy_text_fails: {result.verdict}, score={result.overall_score:.3f}, signals={result.signal_count}")


def test_english_natural_text_passes():
    """自然的英文学术文本不应触发 FAIL。"""
    natural_text = (
        "We collected data from 200 participants over six months. The mean response time was 342ms. "
        "Some subjects showed faster reactions under pressure. Others did not respond differently. "
        "Our analysis reveals a statistically significant difference between groups (p < 0.001). "
        "The effect size, however, was modest at d=0.45, suggesting practical implications may be limited. "
        "Short sentences work here. They break up the rhythm naturally and add variety. "
        "A longer sentence follows to demonstrate the kind of variation that characterizes authentic human academic writing in peer-reviewed journals across multiple disciplines."
    )
    result = detect_ai_signals(natural_text)
    
    assert result.verdict == "PASS", f"Expected PASS, got {result.verdict}. Reason: {result.verdict_reason}"
    assert result.critical_count == 0, f"Expected no critical signals, got {result.critical_count}"
    
    print(f"✓ test_english_natural_text_passes: {result.verdict}, score={result.overall_score:.3f}")


def test_chinese_ai_heavy_text_fails():
    """典型的 AI 生成中文文本应该被判定为 FAIL。"""
    zh_ai_text = (
        "值得注意的是，这项研究具有划时代的意义。"
        "众所周知，深度学习在自然语言处理领域取得了前所未有的突破。"
        "不言而喻，这一颠覆性的技术正在改变学术研究的面貌。"
        "此外，与此同时，不仅如此，更为重要的是，"
        "在此基础上，除此之外，该方法不仅提高了效率，而且降低了成本，更推动了整个领域的蓬勃发展。"
        "毋庸置疑，这项工作既填补了理论空白，又拓展了应用边界，也为后续研究奠定了坚实基础。"
        "事实上，该框架如同一座灯塔，照亮了前进的方向，堪称学术领域的丰碑。"
    )
    result = detect_ai_signals(zh_ai_text)
    
    assert result.verdict == "FAIL", f"Expected FAIL, got {result.verdict}"
    assert result.signal_count > 0
    
    signal_types = {s.signal_type for s in result.signals}
    # 应该检测到中文套话
    assert "THROAT_CLEARING_ZH" in signal_types, f"Expected THROAT_CLEARING_ZH in {signal_types}"
    # 应该检测到宣传式表达
    assert "PROMOTIONAL_ZH" in signal_types, f"Expected PROMOTIONAL_ZH in {signal_types}"
    
    print(f"✓ test_chinese_ai_heavy_text_fails: {result.verdict}, score={result.overall_score:.3f}, signals={result.signal_count}")


def test_chinese_natural_text_passes():
    """自然的中文学术文本不应触发过多信号。"""
    zh_natural = (
        "本文提出了一种基于注意力机制的文本分类方法。"
        "实验在三个公开数据集上进行验证。"
        "结果表明，该方法在准确率上比基线模型高出2.3个百分点。"
        "但在长文本场景下性能有所下降，原因可能是注意力权重的分散。"
        "后续工作将探索分层注意力策略来缓解这一问题。"
        "我们的代码已开源供研究社区使用。"
    )
    result = detect_ai_signals(zh_natural)
    
    # 自然文本可能触发少量 minor 信号，但不应触发 critical
    assert result.critical_count == 0, f"Expected no critical signals, got {result.critical_count}"
    
    print(f"✓ test_chinese_natural_text_passes: {result.verdict}, score={result.overall_score:.3f}")


# ============================================================
# Test: Burstiness
# ============================================================

def test_burstiness_uniform_text():
    """均匀句长应该低 CV。"""
    # 5 sentences of similar length (~15 words each)
    uniform = (
        "The model was trained on a large corpus of text data. "
        "The results show that performance improves with more data. "
        "We evaluated the approach using standard benchmark datasets. "
        "The training process took approximately three hours total. "
        "Our method achieves state of the art on all tasks."
    )
    result = check_burstiness(uniform)
    assert result.cv < 0.35, f"Expected CV < 0.35 for uniform text, got {result.cv}"
    assert result.passed is False, "Expected burstiness check to fail for uniform text"
    print(f"✓ test_burstiness_uniform_text: CV={result.cv:.3f}, passed={result.passed}")


def test_burstiness_varied_text():
    """变化丰富的句长应该高 CV。"""
    # Deliberately mix very short valid sentences (4+ words) with very long ones
    varied = (
        "This works quite well. "
        "The model was trained on an extremely large and diverse corpus of text data from multiple sources spanning several years of web crawls across dozens of languages and domains. "
        "Results improved over baseline. "
        "We evaluated the proposed approach using standard benchmark datasets that are commonly employed by researchers in the natural language processing community for assessing model generalization. "
        "The gap was huge. "
        "Our method achieves competitive performance across all of the evaluated tasks in multiple domains and further demonstrates remarkably strong generalization to previously unseen domains that were not part of training."
    )
    result = check_burstiness(varied)
    assert result.cv >= 0.35, f"Expected CV >= 0.35 for varied text, got {result.cv}"
    assert result.passed is True, "Expected burstiness check to pass for varied text"
    print(f"✓ test_burstiness_varied_text: CV={result.cv:.3f}, passed={result.passed}")


# ============================================================
# Test: Hard Caps
# ============================================================

def test_hard_cap_cliches():
    """3+ AI clichés 应该触发 HC-1。"""
    text_with_cliches = (
        "This groundbreaking approach delves into the multifaceted realm of deep learning. "
        "The pivotal role of attention is paramount in this landscape. "
        "Our transformative framework leverages synergy between components."
    )
    result = detect_ai_signals(text_with_cliches)
    
    # 应该有 hard caps
    hc_messages = result.hard_caps_triggered
    has_hc1 = any("HC-1" in hc for hc in hc_messages)
    assert has_hc1, f"Expected HC-1 triggered, got: {hc_messages}"
    assert result.verdict == "FAIL"
    
    print(f"✓ test_hard_cap_cliches: HC triggered, verdict={result.verdict}")


# ============================================================
# Test: Dimension Scoring
# ============================================================

def test_dimension_scores_computed():
    """检测到信号时应该计算维度评分。"""
    text = (
        "Moreover, this groundbreaking study delves into neural networks. "
        "Furthermore, it is worth noting that deep learning is transformative. "
        "Additionally, the unprecedented nature of this approach is pivotal. "
        "Consequently, the multifaceted implications are clear. "
        "Nevertheless, the remarkable performance underscores the importance."
    )
    result = detect_ai_signals(text)
    
    assert result.dimension_scores, "Expected dimension scores to be computed"
    assert "vocabulary" in result.dimension_scores
    assert "rhythm" in result.dimension_scores
    assert "connectors" in result.dimension_scores
    
    # vocabulary should be penalized (clichés)
    assert result.dimension_scores["vocabulary"] < 1.0, "vocabulary dimension should be penalized"
    
    print(f"✓ test_dimension_scores_computed: dims={result.dimension_scores}")


# ============================================================
# Test: DetectionResult.summary()
# ============================================================

def test_summary_format():
    """summary() 应该返回人类可读的格式化字符串。"""
    result = detect_ai_signals(
        "This groundbreaking study delves into the realm of AI. "
        "Moreover, it is worth noting that the transformative impact is paramount. "
        "Furthermore, the multifaceted landscape of deep learning is pivotal. "
        "Additionally, this unprecedented approach paves the way for future work. "
        "In conclusion, the synergy between these techniques is remarkable."
    )
    
    summary = result.summary()
    assert "AI Signal Detection" in summary
    assert result.verdict in summary
    assert len(summary) > 100, f"Summary too short: {len(summary)} chars"
    
    print(f"✓ test_summary_format: {len(summary)} chars")


# ============================================================
# Test: Edge Cases
# ============================================================

def test_empty_text():
    """空文本应该 PASS。"""
    result = detect_ai_signals("")
    assert result.verdict == "PASS"
    assert result.signal_count == 0
    print("✓ test_empty_text: PASS")


def test_short_text():
    """短文本（<50字符）不做分析。"""
    result = detect_ai_signals("Hello world.")
    assert result.verdict == "PASS"
    assert result.signal_count == 0
    print("✓ test_short_text: PASS")


# ============================================================
# Run all tests
# ============================================================

if __name__ == "__main__":
    test_is_chinese_text()
    test_english_ai_heavy_text_fails()
    test_english_natural_text_passes()
    test_chinese_ai_heavy_text_fails()
    test_chinese_natural_text_passes()
    test_burstiness_uniform_text()
    test_burstiness_varied_text()
    test_hard_cap_cliches()
    test_dimension_scores_computed()
    test_summary_format()
    test_empty_text()
    test_short_text()
    
    print("\n" + "=" * 60)
    print("ALL TESTS PASSED ✅")
    print("=" * 60)
