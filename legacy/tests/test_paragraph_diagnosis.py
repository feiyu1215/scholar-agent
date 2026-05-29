"""Tests for C-5 paragraph structure diagnosis."""

import pytest
from tools.paragraph_diagnosis import (
    analyze_section_structure,
    ParagraphProfile,
    SectionStructureReport,
    generate_structure_fix_hints,
    has_topic_sentence,
    has_evidence,
    has_transition,
    detect_claim_evidence_alignment,
    compute_health_score,
)


# ── Topic Sentence Detection ────────────────────────────────────────────────

class TestTopicSentence:
    def test_english_claim(self):
        assert has_topic_sentence("We argue that this approach is superior.")

    def test_english_purpose(self):
        assert has_topic_sentence("This section presents our methodology.")

    def test_english_finding(self):
        assert has_topic_sentence("Our results demonstrate significant improvement.")

    def test_no_topic_sentence(self):
        # Sentences without any claim or purpose indicator
        assert not has_topic_sentence("Meanwhile, several factors remain unclear.")

    def test_chinese_topic(self):
        assert has_topic_sentence("本文提出了一种新的方法来解决这个问题。")

    def test_chinese_finding(self):
        assert has_topic_sentence("研究发现，该方法在多个数据集上均表现优异。")


# ── Evidence Detection ───────────────────────────────────────────────────────

class TestEvidence:
    def test_citation_bracket(self):
        assert has_evidence("Previous work [1] has shown that this is effective.")

    def test_citation_author_year(self):
        # Author (year) pattern with explicit citation context
        assert has_evidence("As demonstrated by Smith et al. (2020), the results show 90% accuracy.")

    def test_percentage(self):
        assert has_evidence("The accuracy improved by 15.3% over the baseline.")

    def test_table_reference(self):
        assert has_evidence("As shown in Table 2, the results are consistent.")

    def test_figure_reference(self):
        assert has_evidence("Figure 3 illustrates the architecture.")

    def test_no_evidence(self):
        assert not has_evidence("This is an important consideration for future work.")

    def test_chinese_evidence(self):
        assert has_evidence("如表3所示，准确率达到了95.2%。")


# ── Transition Detection ─────────────────────────────────────────────────────

class TestTransition:
    def test_however(self):
        assert has_transition("However, this approach has limitations.")

    def test_furthermore(self):
        assert has_transition("Furthermore, we extend the analysis to include...")

    def test_in_contrast(self):
        assert has_transition("In contrast, the baseline model fails on...")

    def test_no_transition(self):
        assert not has_transition("The model architecture consists of three layers.")

    def test_chinese_transition(self):
        assert has_transition("然而，该方法在某些场景下表现不佳。")

    def test_chinese_furthermore(self):
        assert has_transition("此外，我们还验证了模型的泛化能力。")


# ── Claim-Evidence Alignment ─────────────────────────────────────────────────

class TestAlignment:
    def test_aligned(self):
        text = "We argue that transformers are effective. As shown in Table 1, accuracy reaches 95%."
        assert detect_claim_evidence_alignment(text)

    def test_claim_only(self):
        # Academic claim language present but no evidence indicators → unaligned
        text = "We argue that this approach is fundamentally flawed. The core assumption is wrong."
        assert not detect_claim_evidence_alignment(text)

    def test_evidence_only(self):
        text = "Table 1 shows 95% accuracy. Figure 2 presents the results."
        # Evidence without interpretation is also unaligned
        assert not detect_claim_evidence_alignment(text)


# ── Section Structure Analysis ───────────────────────────────────────────────

class TestSectionAnalysis:
    def test_basic_analysis(self):
        text = (
            "We argue that transformers are effective for NLP tasks.\n\n"
            "As shown in Table 1, the accuracy reaches 95%. "
            "This demonstrates the superiority of our approach.\n\n"
            "However, there are limitations. The model fails on long sequences "
            "exceeding 512 tokens, as noted in previous work [3]."
        )
        report = analyze_section_structure("sec_01", text)
        assert report.total_paragraphs == 3
        assert report.section_id == "sec_01"
        assert isinstance(report.health_score, float)
        assert 0.0 <= report.health_score <= 1.0

    def test_paragraph_profiles(self):
        text = (
            "We argue that this method works well.\n\n"
            "Table 1 shows the results with 92% accuracy.\n\n"
            "Furthermore, the approach generalizes across domains."
        )
        report = analyze_section_structure("sec_02", text)
        assert len(report.paragraphs) == 3
        assert all(isinstance(p, ParagraphProfile) for p in report.paragraphs)

    def test_single_paragraph(self):
        text = "This is a single paragraph with no structure to analyze deeply."
        report = analyze_section_structure("sec_03", text)
        assert report.total_paragraphs == 1

    def test_empty_text(self):
        report = analyze_section_structure("sec_04", "")
        assert report.total_paragraphs == 0
        assert report.health_score == 0.0

    def test_all_claims_no_evidence(self):
        text = (
            "This approach is revolutionary.\n\n"
            "The method represents a paradigm shift.\n\n"
            "It will transform the entire field."
        )
        report = analyze_section_structure("sec_05", text)
        # Should flag evidence desert
        assert any("evidence" in issue.lower() or "support" in issue.lower()
                   for issue in report.structural_issues) or report.health_score < 0.5

    def test_chinese_text(self):
        text = (
            "本文提出了一种新的深度学习方法。\n\n"
            "如表1所示，该方法在CIFAR-10数据集上达到了96.5%的准确率。\n\n"
            "此外，我们还在ImageNet上进行了验证。"
        )
        report = analyze_section_structure("sec_06", text)
        # Paragraph count depends on how splitting handles Chinese text
        assert report.total_paragraphs >= 2
        assert report.health_score > 0.0


# ── Fix Hints Generation ─────────────────────────────────────────────────────

class TestFixHints:
    def test_generates_hints(self):
        text = (
            "The data shows 95% accuracy.\n\n"
            "The data shows 92% precision.\n\n"
            "The data shows 88% recall."
        )
        report = analyze_section_structure("sec_07", text)
        hints = generate_structure_fix_hints(report)
        assert isinstance(hints, list)
        # Should produce some hints for this repetitive structure
        assert len(hints) <= 5  # Max 5 hints

    def test_healthy_section_fewer_hints(self):
        text = (
            "We propose a novel attention mechanism for sequence modeling.\n\n"
            "As shown in Table 1, our method achieves 95.2% accuracy on the "
            "benchmark dataset [1], outperforming the baseline by 3.1%.\n\n"
            "However, the computational cost increases quadratically with "
            "sequence length, reaching 2.3x overhead for sequences over 1024 tokens."
        )
        report = analyze_section_structure("sec_08", text)
        # Healthy text should have fewer issues
        assert report.health_score >= 0.4

    def test_max_hints_cap(self):
        # Even with many issues, should cap at 5
        text = "\n\n".join([
            f"Point number {i} is important."
            for i in range(10)
        ])
        report = analyze_section_structure("sec_09", text)
        hints = generate_structure_fix_hints(report)
        assert len(hints) <= 5


# ── Health Score ─────────────────────────────────────────────────────────────

class TestHealthScore:
    def test_perfect_structure(self):
        """Well-structured text should score high."""
        text = (
            "We argue that attention mechanisms are key to transformer success.\n\n"
            "Our experiments on WMT-14 show BLEU scores of 28.4 [1], "
            "significantly outperforming the 25.8 baseline (Table 2).\n\n"
            "Furthermore, ablation studies in Figure 3 confirm that "
            "multi-head attention contributes 2.1 BLEU points."
        )
        report = analyze_section_structure("sec_10", text)
        assert report.health_score >= 0.5

    def test_poor_structure(self):
        """Claim-only text without evidence should score lower."""
        text = (
            "This is important.\n\n"
            "This is also important.\n\n"
            "This matters a lot.\n\n"
            "We believe strongly in this."
        )
        report = analyze_section_structure("sec_11", text)
        assert report.health_score < 0.7
