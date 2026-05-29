"""
Tests for C-1: De-AI Rule Engine Upgrade.

Covers:
1. Structured YAML rule loader (tools/deai/rules/loader.py)
2. Perplexity-aware detection (tools/deai/perplexity.py)
3. Integration with existing _load_rules() in signals.py
"""
import pytest
from pathlib import Path


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 1: Rule Loader Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestRuleLoader:
    """Tests for tools/deai/rules/loader.py"""

    def test_load_general_rules(self):
        """S_GENERAL rules load with expected structure."""
        from tools.deai.rules.loader import load_scene_rules, clear_cache
        clear_cache()
        rules = load_scene_rules("S_GENERAL")
        assert rules.scene == "S_GENERAL"
        assert len(rules.rules) == 12  # G1-G12
        assert rules.target_voice != ""

    def test_load_s1_rules(self):
        """S1 CS English rules load with 26 rules."""
        from tools.deai.rules.loader import load_scene_rules, clear_cache
        clear_cache()
        rules = load_scene_rules("S1")
        assert rules.scene == "S1"
        assert len(rules.rules) == 26

    def test_load_s2_rules(self):
        """S2 Chinese rules load with 26 rules + signal categories."""
        from tools.deai.rules.loader import load_scene_rules, clear_cache
        clear_cache()
        rules = load_scene_rules("S2")
        assert rules.scene == "S2"
        assert len(rules.rules) == 26
        assert len(rules.signal_categories) == 12  # S2-Sig01-12
        assert len(rules.conflict_resolutions) >= 7

    def test_load_s3_rules(self):
        """S3 Economics rules load with 31 rules + overrides."""
        from tools.deai.rules.loader import load_scene_rules, clear_cache
        clear_cache()
        rules = load_scene_rules("S3")
        assert rules.scene == "S3"
        assert len(rules.rules) == 31
        assert len(rules.scene_overrides) == 2  # EM_DASH + HEDGE_OPENERS

    def test_unknown_scene_returns_empty(self):
        """Unknown scene returns empty SceneRules."""
        from tools.deai.rules.loader import load_scene_rules, clear_cache
        clear_cache()
        rules = load_scene_rules("S99")
        assert rules.scene == "S99"
        assert len(rules.rules) == 0

    def test_caching(self):
        """Second load returns same object (cached)."""
        from tools.deai.rules.loader import load_scene_rules, clear_cache
        clear_cache()
        r1 = load_scene_rules("S1")
        r2 = load_scene_rules("S1")
        assert r1 is r2  # Same object reference

    def test_clear_cache(self):
        """clear_cache() forces re-parse."""
        from tools.deai.rules.loader import load_scene_rules, clear_cache
        clear_cache()
        r1 = load_scene_rules("S1")
        clear_cache()
        r2 = load_scene_rules("S1")
        assert r1 is not r2  # Different objects after cache clear
        assert r1.scene == r2.scene  # Same content

    def test_get_banned_words_s1(self):
        """S1 aggregates banned words from vocabulary rules."""
        from tools.deai.rules.loader import load_scene_rules, clear_cache
        clear_cache()
        rules = load_scene_rules("S1")
        banned = rules.get_all_banned_words()
        assert "delve" in banned
        assert "leverage" in banned
        assert len(banned) > 20

    def test_get_banned_words_s2(self):
        """S2 aggregates Chinese banned words."""
        from tools.deai.rules.loader import load_scene_rules, clear_cache
        clear_cache()
        rules = load_scene_rules("S2")
        banned = rules.get_all_banned_words()
        assert "赋能" in banned
        assert "助力" in banned

    def test_get_replacements(self):
        """Replacements merge correctly."""
        from tools.deai.rules.loader import load_scene_rules, clear_cache
        clear_cache()
        rules = load_scene_rules("S1")
        repls = rules.get_all_replacements()
        assert "utilize" in repls
        assert repls["utilize"] == "use"

    def test_scene_overrides_s3(self):
        """S3 suppresses EM_DASH_OVERUSE signal."""
        from tools.deai.rules.loader import load_scene_rules, clear_cache
        clear_cache()
        rules = load_scene_rules("S3")
        suppressed = rules.get_suppressed_signals()
        assert "EM_DASH_OVERUSE" in suppressed

    def test_partially_suppressed_s3(self):
        """S3 partially suppresses HEDGE_OPENERS with allowed patterns."""
        from tools.deai.rules.loader import load_scene_rules, clear_cache
        clear_cache()
        rules = load_scene_rules("S3")
        partial = rules.get_partially_suppressed()
        assert "HEDGE_OPENERS" in partial
        assert "appears to" in partial["HEDGE_OPENERS"]

    def test_rules_by_category(self):
        """Filter rules by category works."""
        from tools.deai.rules.loader import load_scene_rules, clear_cache
        clear_cache()
        rules = load_scene_rules("S1")
        vocab_rules = rules.get_rules_by_category("vocabulary")
        assert len(vocab_rules) >= 4
        assert all(r.category == "vocabulary" for r in vocab_rules)

    def test_load_rules_for_audit_s1(self):
        """load_rules_for_audit produces non-empty prompt text."""
        from tools.deai.rules.loader import load_rules_for_audit, clear_cache
        clear_cache()
        text = load_rules_for_audit("S1")
        assert len(text) > 500
        assert "S_GENERAL" in text
        assert "S1" in text
        assert "Fix Principles" in text

    def test_load_rules_for_audit_s2(self):
        """S2 audit text includes Chinese content."""
        from tools.deai.rules.loader import load_rules_for_audit, clear_cache
        clear_cache()
        text = load_rules_for_audit("S2")
        assert "赋能" in text or "中文" in text

    def test_load_rules_for_audit_s3(self):
        """S3 audit text mentions overrides."""
        from tools.deai.rules.loader import load_rules_for_audit, clear_cache
        clear_cache()
        text = load_rules_for_audit("S3")
        assert "EM_DASH_OVERUSE" in text or "suppress" in text

    def test_get_scene_overrides_function(self):
        """get_scene_overrides returns dict of signal→action."""
        from tools.deai.rules.loader import get_scene_overrides, clear_cache
        clear_cache()
        overrides = get_scene_overrides("S3")
        assert overrides["EM_DASH_OVERUSE"] == "suppress"
        assert overrides["HEDGE_OPENERS"] == "suppress_partial"

    def test_rule_dataclass_fields(self):
        """Rule dataclass has expected fields."""
        from tools.deai.rules.loader import load_scene_rules, clear_cache
        clear_cache()
        rules = load_scene_rules("S_GENERAL")
        g1 = rules.rules[0]
        assert g1.id == "G1"
        assert g1.name == "Tricolon Detection"
        assert g1.category == "structural"
        assert g1.signal_type == "TRICOLON"

    def test_signal_categories_s2(self):
        """S2 signal categories have expected structure."""
        from tools.deai.rules.loader import load_scene_rules, clear_cache
        clear_cache()
        rules = load_scene_rules("S2")
        cats = rules.signal_categories
        assert cats[0].id == "S2-Sig01"
        assert cats[0].signal_type == "AI_VOCABULARY"
        assert cats[-1].id == "S2-Sig12"


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 2: Perplexity Detection Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestPerplexityDetection:
    """Tests for tools/deai/perplexity.py"""

    def test_analyze_natural_text(self):
        """Natural academic text should have moderate-to-high perplexity."""
        from tools.deai.perplexity import analyze_perplexity
        text = (
            "We collected survey data from 1,247 participants across three "
            "metropolitan areas. The sampling strategy balanced demographic "
            "representation with geographic diversity. Response rates varied "
            "between 62% and 78% depending on the recruitment method."
        )
        report = analyze_perplexity(text, is_chinese=False)
        # Natural text should score reasonably high
        assert report.overall_score >= 0.4
        assert report.low_perplexity_count <= 1

    def test_analyze_ai_like_text(self):
        """AI-like text with predictable collocations should score low."""
        from tools.deai.perplexity import analyze_perplexity
        text = (
            "This comprehensive analysis serves as a pivotal cornerstone in "
            "our understanding. This demonstrates the significant impact of "
            "the approach. This highlights the crucial role that methodology "
            "plays in achieving comprehensive understanding. These findings "
            "shed light on the multifaceted landscape of the field."
        )
        report = analyze_perplexity(text, is_chinese=False)
        # AI-like text should trigger more low-perplexity sentences
        assert report.low_perplexity_count >= 1
        assert report.overall_score < 0.8

    def test_perplexity_report_summary(self):
        """PerplexityReport.summary() produces readable output."""
        from tools.deai.perplexity import analyze_perplexity
        text = "We examined the data carefully. The results show clear trends."
        report = analyze_perplexity(text, is_chinese=False)
        summary = report.summary()
        assert "Perplexity" in summary
        assert "mean=" in summary

    def test_perplexity_score_properties(self):
        """PerplexityScore has correct property behavior."""
        from tools.deai.perplexity import PerplexityScore
        low = PerplexityScore(sentence="test", score=0.2, bigram_hits=5,
                             predictability_ratio=0.3)
        high = PerplexityScore(sentence="test", score=0.8, bigram_hits=1,
                              predictability_ratio=0.05)
        assert low.is_low_perplexity is True
        assert high.is_low_perplexity is False
        assert low.rewrite_priority > high.rewrite_priority

    def test_get_top_targets(self):
        """get_top_targets returns lowest-scoring sentences."""
        from tools.deai.perplexity import PerplexityScore, PerplexityReport
        scores = [
            PerplexityScore("a", 0.8, 1, 0.1, 0),
            PerplexityScore("b", 0.2, 5, 0.5, 1),
            PerplexityScore("c", 0.3, 4, 0.4, 2),
        ]
        report = PerplexityReport(
            sentences=scores,
            overall_score=0.5,
            low_perplexity_count=2,
            rewrite_targets=[scores[1], scores[2]],
        )
        targets = report.get_top_targets(2)
        assert len(targets) == 2
        assert targets[0].score <= targets[1].score  # Sorted ascending

    def test_needs_rewrite_threshold(self):
        """needs_rewrite triggers at >=30% low-perplexity ratio."""
        from tools.deai.perplexity import PerplexityScore, PerplexityReport
        # 3/10 = 30% → should trigger
        scores = [PerplexityScore(f"s{i}", 0.3 if i < 3 else 0.7, 2, 0.2, i)
                  for i in range(10)]
        report = PerplexityReport(
            sentences=scores,
            overall_score=0.58,
            low_perplexity_count=3,
            rewrite_targets=scores[:3],
        )
        assert report.needs_rewrite is True

    def test_no_rewrite_needed(self):
        """Low ratio of low-perplexity → no rewrite needed."""
        from tools.deai.perplexity import PerplexityScore, PerplexityReport
        scores = [PerplexityScore(f"s{i}", 0.7, 1, 0.1, i) for i in range(10)]
        report = PerplexityReport(
            sentences=scores,
            overall_score=0.7,
            low_perplexity_count=0,
            rewrite_targets=[],
        )
        assert report.needs_rewrite is False

    def test_get_perplexity_fix_hints(self):
        """Fix hints are generated for low-perplexity targets."""
        from tools.deai.perplexity import (
            PerplexityScore, PerplexityReport, get_perplexity_fix_hints
        )
        targets = [
            PerplexityScore("This serves as a pivotal role.", 0.2, 4, 0.4, 0),
            PerplexityScore("The comprehensive analysis.", 0.3, 3, 0.3, 1),
        ]
        report = PerplexityReport(
            sentences=targets,
            overall_score=0.25,
            low_perplexity_count=2,
            rewrite_targets=targets,
        )
        hints = get_perplexity_fix_hints(report, max_hints=5)
        assert len(hints) == 2
        assert "sentence" in hints[0]
        assert "reason" in hints[0]
        assert "strategy" in hints[0]
        assert "perplexity" in hints[0]["reason"].lower()

    def test_chinese_perplexity(self):
        """Chinese text perplexity detection works."""
        from tools.deai.perplexity import analyze_perplexity
        text = (
            "本文对深度学习模型进行了全面分析。"
            "实验结果表明该方法显著提升了分类准确率。"
            "与现有方法相比具有重要的理论意义和实践价值。"
        )
        report = analyze_perplexity(text, is_chinese=True)
        assert len(report.sentences) >= 2
        assert report.overall_score > 0.0

    def test_empty_text(self):
        """Empty text produces empty report."""
        from tools.deai.perplexity import analyze_perplexity
        report = analyze_perplexity("", is_chinese=False)
        assert len(report.sentences) == 0
        assert report.needs_rewrite is False

    def test_short_text(self):
        """Very short text is handled gracefully."""
        from tools.deai.perplexity import analyze_perplexity
        report = analyze_perplexity("Hi.", is_chinese=False)
        assert len(report.sentences) == 0  # Too short to analyze


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 3: Integration Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestIntegration:
    """Tests for integration between new rule engine and existing code."""

    def test_load_rules_uses_yaml_loader(self):
        """signals._load_rules() now uses YAML loader as primary path."""
        from tools.deai.signals import _load_rules
        result = _load_rules("S1")
        # Should contain structured output from YAML loader
        assert "S_GENERAL" in result
        assert "Fix Principles" in result
        assert len(result) > 200

    def test_load_rules_s2(self):
        """_load_rules('S2') returns Chinese rules."""
        from tools.deai.signals import _load_rules
        result = _load_rules("S2")
        assert "S2" in result

    def test_load_rules_s3(self):
        """_load_rules('S3') returns economics rules with overrides."""
        from tools.deai.signals import _load_rules
        result = _load_rules("S3")
        assert "S3" in result

    def test_deai_package_exports(self):
        """All new C-1 exports are accessible from tools.deai."""
        from tools.deai import (
            analyze_perplexity,
            get_perplexity_fix_hints,
            PerplexityScore,
            PerplexityReport,
            load_scene_rules,
            load_rules_for_audit,
            get_scene_overrides,
            SceneRules,
            Rule,
            SceneOverride,
        )
        # All imported successfully
        assert callable(analyze_perplexity)
        assert callable(load_scene_rules)

    def test_scene_overrides_integration(self):
        """Scene overrides can be queried for programmatic detector suppression."""
        from tools.deai.rules.loader import get_scene_overrides
        # S3 suppresses EM_DASH
        s3_overrides = get_scene_overrides("S3")
        assert "EM_DASH_OVERUSE" in s3_overrides
        # S1 has no overrides
        s1_overrides = get_scene_overrides("S1")
        assert len(s1_overrides) == 0

    def test_yaml_files_exist(self):
        """All expected YAML rule files exist."""
        rules_dir = Path(__file__).parent.parent / "tools" / "deai" / "rules"
        assert (rules_dir / "s_general.yaml").exists()
        assert (rules_dir / "s1_cs_english.yaml").exists()
        assert (rules_dir / "s2_chinese.yaml").exists()
        assert (rules_dir / "s3_economics.yaml").exists()
        assert (rules_dir / "__init__.py").exists()
        assert (rules_dir / "loader.py").exists()
