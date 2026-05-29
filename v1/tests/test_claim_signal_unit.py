"""Unit tests for core/claim_signal.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.claim_signal import detect_verifiable_claims, _detect_claims


def test_abstract_sota():
    """Abstract with SOTA claim should trigger."""
    text = (
        "We propose Adaptive Salience Scoring (ASS), a novel structured pruning "
        "method that removes attention heads based on a learned importance metric. "
        "Our method achieves 2.1x speedup with less than 1% accuracy loss on GLUE "
        "benchmarks, establishing new state-of-the-art for structured Transformer "
        "pruning. We demonstrate that ASS outperforms all existing pruning methods."
    )
    signal = _detect_claims(text)
    assert signal.has_signals, "Should detect SOTA claims in abstract"
    assert len(signal.sota_claims) >= 2, f"Expected >=2 SOTA claims, got {len(signal.sota_claims)}"
    assert len(signal.novelty_claims) >= 1, f"Should detect 'novel method', got {signal.novelty_claims}"

    formatted = detect_verifiable_claims(text)
    assert "[🔍 Claim Signal" in formatted
    assert "SOTA" in formatted
    print("✓ test_abstract_sota passed")


def test_introduction_novelty():
    """Introduction with 'no prior work' + 'to our knowledge' should trigger."""
    text = (
        "Structured pruning of Transformers remains understudied. To our knowledge, "
        "no prior work has addressed head-level pruning with dynamic importance "
        "recomputation during fine-tuning. The closest related work is the lottery "
        "ticket hypothesis (Frankle & Carlin, 2018) which focused on unstructured "
        "weight pruning in CNNs."
    )
    signal = _detect_claims(text)
    assert signal.has_signals
    assert len(signal.novelty_claims) >= 2, f"Expected >=2 novelty, got {signal.novelty_claims}"

    formatted = detect_verifiable_claims(text)
    assert "Novelty" in formatted
    assert "无法仅凭论文内部信息验证" in formatted
    print("✓ test_introduction_novelty passed")


def test_related_work_first():
    """'Our method is the first to combine...' should trigger."""
    text = (
        "Our method is the first to combine: (1) dynamic importance scoring, "
        "(2) head-level granularity, and (3) fine-tuning-aware recomputation. "
        "This combination has not been explored in the literature."
    )
    signal = _detect_claims(text)
    assert signal.has_signals
    novelty_labels = [c.split(":")[0] for c in signal.novelty_claims]
    assert any("first" in l for l in novelty_labels), f"Should detect 'first to', got {novelty_labels}"
    assert any("unexplored" in l or "explored" in l.lower() for l in novelty_labels), \
        f"Should detect 'has not been explored', got {novelty_labels}"
    print("✓ test_related_work_first passed")


def test_neutral_no_signal():
    """Neutral technical description should NOT trigger."""
    text = (
        "We use BERT-base with 12 layers and 12 heads per layer. Training is done "
        "on 4 A100 GPUs with batch size 32. We use Adam optimizer with learning "
        "rate 2e-5. The total training time is approximately 3 hours."
    )
    signal = _detect_claims(text)
    assert not signal.has_signals, f"Neutral text should not trigger, got {signal.novelty_claims + signal.sota_claims}"
    assert detect_verifiable_claims(text) == ""
    print("✓ test_neutral_no_signal passed")


def test_short_text_no_signal():
    """Very short text should return empty (skip detection)."""
    assert detect_verifiable_claims("Hello") == ""
    assert detect_verifiable_claims("") == ""
    print("✓ test_short_text_no_signal passed")


def test_full_fixture_paper():
    """Load the full fixture paper and verify detection on relevant sections."""
    paper_path = Path(__file__).parent / "fixtures" / "paper_with_verifiable_claims.md"
    if not paper_path.exists():
        print("⚠ Fixture paper not found, skipping")
        return

    content = paper_path.read_text()
    # The full paper should have both novelty and SOTA signals
    signal = _detect_claims(content)
    assert signal.has_signals
    assert len(signal.novelty_claims) >= 3, f"Full paper should have >=3 novelty claims, got {len(signal.novelty_claims)}"
    assert len(signal.sota_claims) >= 2, f"Full paper should have >=2 SOTA claims, got {len(signal.sota_claims)}"
    print(f"✓ test_full_fixture_paper passed (novelty={len(signal.novelty_claims)}, sota={len(signal.sota_claims)})")


if __name__ == "__main__":
    test_abstract_sota()
    test_introduction_novelty()
    test_related_work_first()
    test_neutral_no_signal()
    test_short_text_no_signal()
    test_full_fixture_paper()
    print("\n✅ All claim_signal unit tests passed!")
