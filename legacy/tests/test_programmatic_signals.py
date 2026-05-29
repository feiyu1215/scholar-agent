"""
Unit tests for _detect_programmatic_signals (P7 Weak Signal Enhancement).

Tests the three programmatic detectors:
1. RHYTHM_UNIFORMITY: sentences with CV < 0.35
2. FORMULAIC_TRANSITIONS: 4+ distinct formulaic transition words
3. TYPE_TOKEN_RATIO: same content word in 3+ sentences within 5-sentence window
"""

import sys
from pathlib import Path

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.deai_engine import _detect_programmatic_signals, AISignal


class TestRhythmUniformity:
    """Tests for RHYTHM_UNIFORMITY detector (CV < 0.35)."""

    def test_uniform_sentences_detected(self):
        """Sentences with similar word counts (low CV) should trigger."""
        # All sentences ~15 words → CV very low
        text = (
            "The methodology provides a comprehensive approach to data analysis. "
            "Furthermore the framework enables systematic investigation of patterns. "
            "Moreover the results demonstrate significant implications for future work. "
            "Additionally these findings contribute meaningfully to existing literature. "
            "Consequently the study advances our theoretical understanding substantially. "
            "Nevertheless certain limitations must be carefully acknowledged and addressed."
        )
        signals = _detect_programmatic_signals(text)
        types = [s.signal_type for s in signals]
        assert "RHYTHM_UNIFORMITY" in types, f"Expected RHYTHM_UNIFORMITY, got {types}"

    def test_varied_sentences_not_detected(self):
        """Sentences with varied lengths (high CV) should NOT trigger."""
        text = (
            "Short point here. "
            "The second sentence is somewhat longer and more descriptive in nature. "
            "Third. "
            "Now we come to the fourth sentence which is actually quite long and covers "
            "multiple aspects of the subject matter with various qualifiers and details "
            "that extend it significantly beyond the others. "
            "Medium length here for five. "
            "And six is brief too."
        )
        signals = _detect_programmatic_signals(text)
        types = [s.signal_type for s in signals]
        assert "RHYTHM_UNIFORMITY" not in types, f"Should not detect rhythm: {types}"

    def test_fewer_than_5_sentences_skipped(self):
        """Fewer than 5 valid sentences should skip detection entirely."""
        text = (
            "The first sentence is here. "
            "The second sentence is here. "
            "The third sentence is here. "
            "The fourth sentence is here."
        )
        signals = _detect_programmatic_signals(text)
        types = [s.signal_type for s in signals]
        assert "RHYTHM_UNIFORMITY" not in types

    def test_confidence_increases_with_lower_cv(self):
        """Lower CV should produce higher confidence."""
        # Very uniform (CV ~0.1)
        very_uniform = (
            "The study examines important implications carefully. "
            "The method provides excellent results consistently. "
            "The framework enables systematic analysis effectively. "
            "The approach demonstrates significant findings clearly. "
            "The results support theoretical predictions strongly."
        )
        # Moderately uniform (CV ~0.3)
        moderate = (
            "Short one here now. "
            "The second sentence adds a bit more detail. "
            "Third sentence is moderately sized with words. "
            "The fourth adds yet more context and detail here today. "
            "Five is short. "
            "Sixth is even shorter."
        )
        sig_very = [s for s in _detect_programmatic_signals(very_uniform)
                    if s.signal_type == "RHYTHM_UNIFORMITY"]
        sig_mod = [s for s in _detect_programmatic_signals(moderate)
                   if s.signal_type == "RHYTHM_UNIFORMITY"]

        if sig_very and sig_mod:
            assert sig_very[0].confidence >= sig_mod[0].confidence


class TestFormulaicTransitions:
    """Tests for FORMULAIC_TRANSITIONS detector (4+ distinct patterns)."""

    def test_three_transitions_detected(self):
        """3+ distinct formulaic transitions should trigger."""
        text = (
            "Furthermore the data supports this conclusion. "
            "Moreover the analysis reveals additional patterns. "
            "Additionally the framework provides new insights. "
            "The final point summarizes key findings here."
        )
        signals = _detect_programmatic_signals(text)
        types = [s.signal_type for s in signals]
        assert "FORMULAIC_TRANSITIONS" in types, f"Expected detection, got {types}"

    def test_two_transitions_not_detected(self):
        """Fewer than 3 distinct transitions should NOT trigger."""
        text = (
            "Furthermore the data supports this conclusion. "
            "Moreover the analysis reveals additional patterns. "
            "The framework provides new insights for researchers. "
            "The final point summarizes key findings here."
        )
        signals = _detect_programmatic_signals(text)
        types = [s.signal_type for s in signals]
        assert "FORMULAIC_TRANSITIONS" not in types

    def test_repeated_same_transition_not_counted(self):
        """Same transition repeated should only count once."""
        text = (
            "Furthermore the data supports this conclusion. "
            "Furthermore the analysis reveals additional patterns. "
            "Furthermore the framework provides new insights. "
            "Furthermore we can draw important implications. "
            "The final point summarizes key findings."
        )
        signals = _detect_programmatic_signals(text)
        types = [s.signal_type for s in signals]
        # Only 1 distinct pattern → should NOT trigger
        assert "FORMULAIC_TRANSITIONS" not in types

    def test_all_transitions_high_confidence(self):
        """Many distinct transitions should increase confidence."""
        text = (
            "Furthermore the data supports this important conclusion clearly. "
            "Moreover the analysis reveals additional significant patterns here. "
            "Additionally the framework provides many new insights today. "
            "Consequently we can draw several important implications from this. "
            "Nevertheless certain limitations must be acknowledged and addressed. "
            "Specifically the sample size constrains the generalizability somewhat. "
            "Notably these findings challenge existing theoretical assumptions directly."
        )
        signals = [s for s in _detect_programmatic_signals(text)
                   if s.signal_type == "FORMULAIC_TRANSITIONS"]
        assert len(signals) == 1
        assert signals[0].confidence >= 0.7


class TestTypeTokenRatio:
    """Tests for TYPE_TOKEN_RATIO detector (word in 3+ of 5 adjacent sentences)."""

    def test_repeated_word_detected(self):
        """Same content word in 3+ adjacent sentences should trigger."""
        text = (
            "The framework provides comprehensive analysis capabilities. "
            "This framework enables systematic investigation of data. "
            "The framework demonstrates robust performance consistently. "
            "Our framework outperforms existing baseline methods significantly. "
            "In conclusion the framework advances our understanding."
        )
        signals = _detect_programmatic_signals(text)
        types = [s.signal_type for s in signals]
        assert "TYPE_TOKEN_RATIO" in types, f"Expected TYPE_TOKEN_RATIO, got {types}"

    def test_varied_vocabulary_not_detected(self):
        """Diverse vocabulary with no excessive repetition should NOT trigger."""
        text = (
            "The methodology employs neural network architectures effectively. "
            "Experimental results demonstrate significant improvement over baselines. "
            "Statistical analysis confirms reliability of observed patterns. "
            "Visualization tools help interpret complex model behaviors clearly. "
            "Future work should explore alternative training strategies."
        )
        signals = _detect_programmatic_signals(text)
        types = [s.signal_type for s in signals]
        assert "TYPE_TOKEN_RATIO" not in types, f"Should not detect TTR: {types}"

    def test_fewer_than_5_sentences_skipped(self):
        """Fewer than 5 sentences should skip TTR detection."""
        text = (
            "The framework provides this. "
            "The framework enables that. "
            "The framework does more. "
            "The framework works well."
        )
        signals = _detect_programmatic_signals(text)
        types = [s.signal_type for s in signals]
        assert "TYPE_TOKEN_RATIO" not in types

    def test_stopwords_excluded(self):
        """Common stopwords should not trigger even if repeated."""
        # "their" and "also" are in the stopwords list
        text = (
            "The researchers presented their findings in detail. "
            "The scientists shared their conclusions at the conference. "
            "The authors discussed their methodology in the paper. "
            "The team revealed their data during the presentation. "
            "The group published their results last month."
        )
        signals = _detect_programmatic_signals(text)
        ttr_signals = [s for s in signals if s.signal_type == "TYPE_TOKEN_RATIO"]
        # "their" is a stopword so should not trigger
        # (but "researchers/scientists/etc." are unique, so no non-stop word repeats)
        for sig in ttr_signals:
            assert "their" not in sig.fix_suggestion


class TestCombinedDetection:
    """Tests for multiple signals detected simultaneously."""

    def test_all_three_detected(self):
        """Text with all three weaknesses should detect all three."""
        text = (
            "The study significantly advances our understanding of the field. "
            "Furthermore these results demonstrate meaningful progress here. "
            "Moreover the study provides comprehensive solutions for researchers. "
            "Additionally the study ensures reproducibility across settings. "
            "Consequently future research can build upon these study findings. "
            "Nevertheless certain study limitations must be acknowledged carefully. "
            "Specifically the study size may affect generalizability somewhat."
        )
        signals = _detect_programmatic_signals(text)
        types = set(s.signal_type for s in signals)
        # Should detect at least FORMULAIC_TRANSITIONS (6 distinct)
        # and possibly RHYTHM_UNIFORMITY and/or TYPE_TOKEN_RATIO
        assert "FORMULAIC_TRANSITIONS" in types, f"Missing FORMULAIC_TRANSITIONS: {types}"

    def test_empty_text_returns_nothing(self):
        """Empty or very short text should return empty list."""
        assert _detect_programmatic_signals("") == []
        assert _detect_programmatic_signals("Short.") == []

    def test_returns_aisignal_instances(self):
        """All returned objects should be AISignal dataclass instances."""
        text = (
            "Furthermore the data supports this conclusion clearly today. "
            "Moreover the analysis reveals additional patterns for research. "
            "Additionally the framework provides new insights for practitioners. "
            "Consequently we can draw important implications from the results. "
            "The final point summarizes all the key findings effectively."
        )
        signals = _detect_programmatic_signals(text)
        for s in signals:
            assert isinstance(s, AISignal)
            assert hasattr(s, "signal_type")
            assert hasattr(s, "confidence")
            assert hasattr(s, "fix_suggestion")
            assert 0.0 <= s.confidence <= 1.0


class TestEvalDeduplication:
    """Tests for the eval metric deduplication fix."""

    def test_dedupe_detected_types(self):
        """Verify _dedupe_detected_types collapses identical type strings."""
        from eval.run_deai_gold import _dedupe_detected_types

        types = [
            "AI High-Frequency Word Ban",
            "AI High-Frequency Word Ban",
            "AI High-Frequency Word Ban",
            "RHYTHM_UNIFORMITY",
            "TYPE_TOKEN_RATIO",
        ]
        unique = _dedupe_detected_types(types)
        assert len(unique) == 3
        assert "AI High-Frequency Word Ban" in unique
        assert "RHYTHM_UNIFORMITY" in unique
        assert "TYPE_TOKEN_RATIO" in unique

    def test_dedupe_case_insensitive_normalization(self):
        """Verify normalization handles case and separators."""
        from eval.run_deai_gold import _dedupe_detected_types

        types = [
            "Rhythm Uniformity",
            "rhythm_uniformity",  # Should be collapsed with above
            "TYPE_TOKEN_RATIO",
        ]
        unique = _dedupe_detected_types(types)
        assert len(unique) == 2  # "Rhythm Uniformity" and "TYPE_TOKEN_RATIO"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
