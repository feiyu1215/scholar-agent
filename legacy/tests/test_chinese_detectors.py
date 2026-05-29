"""
Unit tests for Chinese programmatic detectors:
- THROAT_CLEARING_ZH
- PROMOTIONAL_ZH
- CONNECTOR_OVERUSE_ZH
- PARALLEL_STRUCTURE_ZH
- INFLATED_SYMBOLISM_ZH
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.deai_engine import _detect_programmatic_signals, _is_chinese_text


def _signal_types(text: str) -> set:
    """Helper: return set of signal_type names detected."""
    return {s.signal_type for s in _detect_programmatic_signals(text)}


# ─── _is_chinese_text helper ─────────────────────────────────────────────────

def test_is_chinese_detects_chinese():
    text = "深度学习在自然语言处理中取得了显著进展，尤其是预训练模型的出现改变了整个领域的研究范式。"
    assert _is_chinese_text(text) is True


def test_is_chinese_rejects_english():
    text = "Deep learning has made significant progress in NLP, especially with pretrained models."
    assert _is_chinese_text(text) is False


def test_is_chinese_mixed_mostly_english():
    # Mostly English with some Chinese terms
    text = "The model achieves state-of-the-art on 模型 benchmark. Results show improvement."
    assert _is_chinese_text(text) is False


# ─── THROAT_CLEARING_ZH ─────────────────────────────────────────────────────

def test_throat_clearing_zh_fires():
    text = (
        "值得注意的是，本文提出的方法在多个数据集上均取得了优异的性能。"
        "众所周知，深度学习需要大量数据。"
        "不言而喻，模型的泛化能力至关重要。"
        "实验结果表明该方法具有较强的鲁棒性。"
    )
    assert "THROAT_CLEARING_ZH" in _signal_types(text)


def test_throat_clearing_zh_silent_on_few():
    """Only 1 phrase → should not trigger (threshold is 3)."""
    text = (
        "值得注意的是，本文方法在准确率上有显著提升。"
        "具体而言，在CIFAR-10上达到了95.3%的准确率。"
        "在ImageNet上也取得了78.2%的top-1准确率。"
    )
    assert "THROAT_CLEARING_ZH" not in _signal_types(text)


# ─── PROMOTIONAL_ZH ──────────────────────────────────────────────────────────

def test_promotional_zh_fires():
    text = (
        "本研究具有划时代的意义，为领域发展开辟了全新的方向。"
        "该方法取得了令人瞩目的成就，前所未有的突破性进展令学界振奋。"
        "实验结果证实了方法的有效性。"
    )
    assert "PROMOTIONAL_ZH" in _signal_types(text)


def test_promotional_zh_silent_on_normal():
    text = (
        "本研究提出了一种新的注意力机制，在多个基准测试上取得了较好的结果。"
        "与基线方法相比，准确率提升了2.3个百分点。"
        "消融实验验证了各组件的贡献。"
    )
    assert "PROMOTIONAL_ZH" not in _signal_types(text)


# ─── CONNECTOR_OVERUSE_ZH ────────────────────────────────────────────────────

def test_connector_zh_fires():
    text = (
        "首先，我们需要考虑数据的质量问题。"
        "其次，模型的架构设计也非常关键。"
        "此外，超参数的选择直接影响模型性能。"
        "与此同时，训练策略的优化也不容忽视。"
        "不仅如此，数据增强技术同样发挥了重要作用。"
        "除此之外，正则化方法帮助防止了过拟合。"
    )
    assert "CONNECTOR_OVERUSE_ZH" in _signal_types(text)


def test_connector_zh_silent_on_few():
    """Only 2 connectors → below threshold of 5."""
    text = (
        "首先，我们分析了数据分布。此外，我们还考虑了噪声影响。"
        "实验在三个数据集上进行，每组实验重复五次取平均。"
    )
    assert "CONNECTOR_OVERUSE_ZH" not in _signal_types(text)


# ─── PARALLEL_STRUCTURE_ZH ───────────────────────────────────────────────────

def test_parallel_zh_fires():
    text = (
        "该方法既提高了模型的准确率，又降低了计算复杂度，也增强了泛化能力。"
        "这项研究不仅推动了理论发展，而且促进了实际应用，更为后续工作奠定了基础。"
        "实验结果验证了方法的有效性。"
    )
    assert "PARALLEL_STRUCTURE_ZH" in _signal_types(text)


def test_parallel_zh_silent_on_single():
    """Only 1 parallel → below threshold of 2."""
    text = (
        "该方法既提高了准确率，又降低了延迟，也减少了内存占用。"
        "实验在标准数据集上进行。"
        "结果表明方法的优越性。"
    )
    assert "PARALLEL_STRUCTURE_ZH" not in _signal_types(text)


# ─── INFLATED_SYMBOLISM_ZH ───────────────────────────────────────────────────

def test_inflated_zh_fires():
    text = (
        "深度学习的发展波澜壮阔，为人工智能领域树立了丰碑。"
        "这一技术犹如一座灯塔，照亮了前行的道路。"
        "研究者们在这片广袤无垠的学术领域中不断探索。"
    )
    assert "INFLATED_SYMBOLISM_ZH" in _signal_types(text)


def test_inflated_zh_silent_on_normal():
    text = (
        "近年来，深度学习在计算机视觉领域取得了显著进展。"
        "卷积神经网络的引入极大地提升了图像分类的准确率。"
        "本文在此基础上提出了一种改进的网络架构。"
    )
    assert "INFLATED_SYMBOLISM_ZH" not in _signal_types(text)


# ─── English text should NOT trigger Chinese detectors ───────────────────────

def test_english_text_no_chinese_signals():
    """English-only text must not trigger any _ZH signals."""
    text = (
        "It is important to note that the model converges slowly. "
        "Furthermore, the learning rate affects convergence speed. "
        "Moreover, batch size plays a crucial role in training stability. "
        "Additionally, we observed significant improvements with data augmentation. "
        "The results demonstrate the effectiveness of our proposed approach."
    )
    zh_signals = {s for s in _signal_types(text) if s.endswith("_ZH")}
    assert zh_signals == set()


# ─── Clean Chinese academic text should be quiet ─────────────────────────────

def test_clean_chinese_academic_text():
    """Well-written Chinese academic text should trigger minimal signals."""
    text = (
        "本文提出了一种基于Transformer的多模态融合方法。"
        "该方法通过跨模态注意力机制实现了视觉和语言信息的深度交互。"
        "在VQA和Image Captioning两个任务上的实验表明，"
        "我们的方法相比现有最优方法在准确率上提升了1.8个百分点。"
        "消融实验进一步验证了跨模态注意力模块的关键作用。"
    )
    zh_signals = {s for s in _signal_types(text) if s.endswith("_ZH")}
    assert zh_signals == set()


# ─── SIGNAL_TO_DIMENSION mapping correctness ─────────────────────────────────

def test_zh_signals_map_to_correct_dimensions():
    """Chinese signals must map to the semantically correct dimension."""
    from tools.deai_engine import SIGNAL_TO_DIMENSION
    
    expected = {
        "THROAT_CLEARING_ZH": "connectors",
        "PROMOTIONAL_ZH": "vocabulary",
        "CONNECTOR_OVERUSE_ZH": "connectors",
        "PARALLEL_STRUCTURE_ZH": "rhythm",
        "INFLATED_SYMBOLISM_ZH": "vocabulary",
    }
    for sig, dim in expected.items():
        assert SIGNAL_TO_DIMENSION.get(sig) == dim, f"{sig} should map to {dim}"


def test_zh_connector_signal_penalizes_connectors_dimension():
    """CONNECTOR_OVERUSE_ZH should penalize connectors, not vocabulary."""
    from tools.deai_engine import compute_dimension_scores, AISignal
    
    sig = AISignal(
        sentence="(此外, 与此同时, 不仅如此, 除此之外, 另外)",
        signal_type="CONNECTOR_OVERUSE_ZH",
        confidence=0.7,
        fix_suggestion="",
        location_hint="global",
    )
    result = compute_dimension_scores([sig])
    # Connectors should be penalized
    assert result.connectors < 1.0
    # Vocabulary should remain untouched
    assert result.vocabulary == 1.0


# ─── Run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
