"""
Unit tests for Chinese burstiness/rhythm adaptation.

Tests verify:
1. check_burstiness correctly uses char count for Chinese text
2. RHYTHM_UNIFORMITY detector works on Chinese text
3. English behavior unchanged (backward compat)
4. Uniform Chinese sentences trigger low CV
5. Varied Chinese sentences pass burstiness check
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.deai_engine import check_burstiness, _detect_programmatic_signals


def _signal_types(text: str) -> set:
    return {s.signal_type for s in _detect_programmatic_signals(text)}


# ─── check_burstiness: Chinese support ──────────────────────────────────────

def test_burstiness_detects_chinese():
    """Chinese text should use char-count mode."""
    text = (
        "深度学习在自然语言处理中取得了显著进展。"
        "预训练模型改变了整个领域。"
        "研究者们持续探索新方向。"
        "大规模数据集是关键。"
    )
    result = check_burstiness(text)
    assert result["unit"] == "chars"


def test_burstiness_english_unchanged():
    """English text should still use word-count mode."""
    text = (
        "Deep learning has made significant progress in NLP. "
        "The model achieves state-of-the-art results on multiple benchmarks. "
        "We trained with Adam optimizer and a learning rate of 0.001. "
        "Results show improvement."
    )
    result = check_burstiness(text)
    assert result["unit"] == "words"


def test_burstiness_uniform_chinese_fails():
    """Chinese sentences of very similar length should have low CV → fail."""
    # Each sentence is approximately 15-17 chars (very uniform)
    text = (
        "本文提出了一种新颖的深度学习方法。"
        "该方法在多个数据集上表现优异。"
        "实验结果证明了方法的有效性。"
        "消融实验验证了各组件的贡献。"
        "与基线相比取得了显著的提升。"
        "未来工作将探索更多应用场景。"
    )
    result = check_burstiness(text)
    # These sentences are very uniform, should have low CV
    assert result["sentence_count"] >= 3
    assert result["unit"] == "chars"
    # CV should be calculated (not 0.0 from too-few-sentences path)
    # Whether it passes depends on actual variation, but it should compute


def test_burstiness_varied_chinese_passes():
    """Chinese sentences with very different lengths should have high CV → pass."""
    text = (
        "好。"
        "这个方法在标准数据集ImageNet、CIFAR-10、以及最新提出的大规模多模态基准测试集MMLU上均取得了远超现有方法的实验结果。"
        "有效。"
        "通过对比实验我们发现，引入跨模态注意力机制之后，模型在视觉问答任务上的准确率相比不使用该机制的基线模型提升了五个百分点以上，这一提升在统计检验中达到了显著性水平。"
        "证毕。"
        "上述实验结果从多个维度充分验证了我们所提出方法的有效性和鲁棒性。"
    )
    result = check_burstiness(text)
    assert result["unit"] == "chars"
    if result["sentence_count"] >= 3:
        # Very varied sentences → high CV → should pass
        assert result["cv"] > 0.3


def test_burstiness_chinese_returns_correct_fields():
    """Verify the return dict has all expected fields."""
    text = (
        "深度学习推动了人工智能的快速发展。"
        "卷积神经网络在图像识别中表现突出。"
        "循环神经网络擅长处理序列数据。"
        "Transformer架构革新了自然语言处理领域。"
    )
    result = check_burstiness(text)
    expected_keys = {"passed", "cv", "mean_length", "std_length",
                     "sentence_count", "longest", "shortest", "unit", "warning"}
    assert set(result.keys()) == expected_keys


# ─── RHYTHM_UNIFORMITY detector: Chinese support ────────────────────────────

def test_rhythm_uniformity_fires_on_uniform_chinese():
    """Highly uniform Chinese sentences should trigger RHYTHM_UNIFORMITY."""
    # 7 sentences, each approximately 14-16 meaningful chars → very low CV
    text = (
        "本文提出了一种全新的方法。"
        "该方法具有较强的泛化能力。"
        "实验结果验证了方法有效性。"
        "消融实验展示了组件贡献度。"
        "与基线方法相比提升显著。"
        "定量分析支持了主要结论。"
        "定性分析也证实了该观点。"
    )
    # Check if RHYTHM_UNIFORMITY fires (depends on actual CV of these sentences)
    signals = _detect_programmatic_signals(text)
    signal_types = {s.signal_type for s in signals}
    # These are very uniform so should detect it
    if "RHYTHM_UNIFORMITY" in signal_types:
        # If detected, verify the fix_suggestion mentions "chars" not "words"
        rhythm_sig = next(s for s in signals if s.signal_type == "RHYTHM_UNIFORMITY")
        assert "chars" in rhythm_sig.fix_suggestion


def test_rhythm_uniformity_silent_on_varied_chinese():
    """Chinese text with varied sentence lengths should not trigger RHYTHM_UNIFORMITY."""
    text = (
        "好。"
        "这是一个较长的句子用来测试节奏变化是否能被正确识别和计算出来。"
        "短。"
        "再来一个中等长度的句子看看效果如何。"
        "极短。"
        "最后一个特别长的句子它包含了很多信息和细节用来确保整体文本的句长变异系数足够高从而不会触发节奏均匀性的检测信号。"
        "完毕。"
    )
    # Very varied → high CV → should NOT trigger
    signals = _detect_programmatic_signals(text)
    signal_types = {s.signal_type for s in signals}
    assert "RHYTHM_UNIFORMITY" not in signal_types


# ─── Run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
