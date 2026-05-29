"""
Unit tests for compute_dimension_bias integration into compute_dimension_scores.

Tests verify:
1. compute_dimension_scores works identically without biases (backward compat)
2. Biases amplify penalties on specified dimensions
3. Bias cap behavior (max 0.05)
4. End-to-end: ReviewHint → compute_dimension_bias → compute_dimension_scores
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.deai_engine import compute_dimension_scores, AISignal
from tools.review_deai_bridge import compute_dimension_bias, ReviewHint


# ─── Helper ────────────────────────────────────────────────────────────────────

def _make_signal(signal_type: str, confidence: float = 0.8) -> AISignal:
    return AISignal(
        sentence="test sentence",
        signal_type=signal_type,
        confidence=confidence,
        fix_suggestion="",
        location_hint="",
    )


# ─── Tests: backward compatibility (no biases) ────────────────────────────────

def test_no_bias_same_as_before():
    """Without biases, function should behave identically to old version."""
    signals = [_make_signal("PROMOTIONAL_LANGUAGE", 0.7)]
    
    result_no_bias = compute_dimension_scores(signals, dimension_biases=None)
    result_empty_bias = compute_dimension_scores(signals, dimension_biases={})
    
    assert result_no_bias.vocabulary == result_empty_bias.vocabulary
    assert result_no_bias.rhythm == result_empty_bias.rhythm


def test_basic_penalty_without_bias():
    """Single signal: vocabulary gets penalty = 0.7 * 0.15 = 0.105."""
    signals = [_make_signal("PROMOTIONAL_LANGUAGE", 0.7)]
    result = compute_dimension_scores(signals)
    
    expected_vocab = 1.0 - (0.7 * 0.15)  # 0.895
    assert abs(result.vocabulary - expected_vocab) < 0.001
    # Other dimensions unaffected
    assert result.rhythm == 1.0
    assert result.voice == 1.0


# ─── Tests: bias amplification ─────────────────────────────────────────────────

def test_bias_amplifies_penalty():
    """With vocabulary bias=0.05, penalty should be amplified by 20%."""
    signals = [_make_signal("PROMOTIONAL_LANGUAGE", 0.7)]
    
    result_no_bias = compute_dimension_scores(signals, dimension_biases=None)
    result_with_bias = compute_dimension_scores(signals, dimension_biases={"vocabulary": 0.05})
    
    # With bias: penalty = 0.7 * 0.15 * (1 + 0.05*4) = 0.7 * 0.15 * 1.2 = 0.126
    # Without: penalty = 0.7 * 0.15 = 0.105
    # So biased score should be lower
    assert result_with_bias.vocabulary < result_no_bias.vocabulary
    
    expected_biased = 1.0 - (0.7 * 0.15 * 1.2)  # 0.874
    assert abs(result_with_bias.vocabulary - expected_biased) < 0.001


def test_bias_only_affects_specified_dimension():
    """Bias on 'rhythm' should not affect 'vocabulary'."""
    signals = [_make_signal("PROMOTIONAL_LANGUAGE", 0.8)]  # maps to vocabulary
    
    result_rhythm_bias = compute_dimension_scores(signals, dimension_biases={"rhythm": 0.05})
    result_no_bias = compute_dimension_scores(signals)
    
    # Vocabulary should be identical (bias is on rhythm, not vocabulary)
    assert abs(result_rhythm_bias.vocabulary - result_no_bias.vocabulary) < 0.001


def test_multiple_signals_same_dimension_with_bias():
    """Multiple signals in same dimension all get amplified."""
    signals = [
        _make_signal("PROMOTIONAL_LANGUAGE", 0.6),  # vocabulary
        _make_signal("INFLATED_SYMBOLISM", 0.7),    # vocabulary
    ]
    
    result_no_bias = compute_dimension_scores(signals)
    result_with_bias = compute_dimension_scores(signals, dimension_biases={"vocabulary": 0.03})
    
    # Both penalties amplified → bigger total effect
    assert result_with_bias.vocabulary < result_no_bias.vocabulary


# ─── Tests: compute_dimension_bias from ReviewHints ────────────────────────────

def test_compute_bias_empty_hints():
    """No hints → empty bias dict."""
    assert compute_dimension_bias([]) == {}


def test_compute_bias_single_dimension():
    """All hints in one dimension → that dimension gets a bias."""
    hints = [
        ReviewHint(quote="q1", concern="c1", source_section="intro",
                   suggested_dimension="vocabulary", severity="major"),
        ReviewHint(quote="q2", concern="c2", source_section="intro",
                   suggested_dimension="vocabulary", severity="minor"),
    ]
    biases = compute_dimension_bias(hints)
    assert "vocabulary" in biases
    assert biases["vocabulary"] > 0
    assert biases["vocabulary"] <= 0.05  # capped


def test_compute_bias_multiple_dimensions():
    """Hints spread across dimensions → each gets proportional bias."""
    hints = [
        ReviewHint(quote="q1", concern="c1", source_section="intro",
                   suggested_dimension="vocabulary", severity="major"),
        ReviewHint(quote="q2", concern="c2", source_section="methods",
                   suggested_dimension="rhythm", severity="minor"),
        ReviewHint(quote="q3", concern="c3", source_section="results",
                   suggested_dimension="rhythm", severity="moderate"),
    ]
    biases = compute_dimension_bias(hints)
    
    # rhythm has 2/3 of hints, vocabulary has 1/3
    assert biases["rhythm"] > biases["vocabulary"]


def test_compute_bias_cap():
    """Even with many hints, bias never exceeds 0.05."""
    hints = [
        ReviewHint(quote=f"q{i}", concern=f"c{i}", source_section="intro",
                   suggested_dimension="voice", severity="major")
        for i in range(20)
    ]
    biases = compute_dimension_bias(hints)
    assert biases["voice"] <= 0.05


# ─── Tests: end-to-end integration ────────────────────────────────────────────

def test_end_to_end_hints_to_scores():
    """Full pipeline: ReviewHints → bias → amplified scoring."""
    # Reviewer flagged rhythm issues
    hints = [
        ReviewHint(quote="The model was trained.", concern="Monotonous rhythm",
                   source_section="methods", suggested_dimension="rhythm",
                   severity="major"),
        ReviewHint(quote="Results were obtained.", concern="Repetitive structure",
                   source_section="results", suggested_dimension="rhythm",
                   severity="moderate"),
    ]
    
    # Signals detected by DeAI (rhythm issue)
    signals = [_make_signal("RHYTHM_UNIFORMITY", 0.75)]
    
    # Compute bias from hints
    biases = compute_dimension_bias(hints)
    
    # Score with and without
    score_no_bias = compute_dimension_scores(signals)
    score_with_bias = compute_dimension_scores(signals, dimension_biases=biases)
    
    # Rhythm should be lower with bias (reviewer concern aligns with detected signal)
    assert score_with_bias.rhythm < score_no_bias.rhythm
    # The difference should be small (soft bias, not dramatic)
    diff = score_no_bias.rhythm - score_with_bias.rhythm
    assert 0 < diff < 0.05  # measurable but not huge


def test_unrelated_bias_no_effect():
    """If bias is on a dimension with no signals, scores are identical."""
    signals = [_make_signal("PASSIVE_VOICE_OVERUSE", 0.8)]  # maps to voice
    
    result_vocab_bias = compute_dimension_scores(signals, dimension_biases={"vocabulary": 0.05})
    result_no_bias = compute_dimension_scores(signals)
    
    # voice score should be the same (bias is on vocabulary, signal is on voice)
    assert abs(result_vocab_bias.voice - result_no_bias.voice) < 0.001


# ─── Run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
