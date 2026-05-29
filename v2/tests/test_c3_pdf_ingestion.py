"""
tests/test_c3_pdf_ingestion.py — Phase C3: PDF Ingestion 加固验证

验证目标：
    1. 三级 fallback 策略正常工作（pymupdf → pdfplumber → regex）
    2. 单个 section 解析失败不阻塞全局
    3. pdfplumber fallback 可被正确调用
    4. 空 PDF / 损坏 PDF 给出有意义的错误信息
    5. 各个私有函数的边界情况
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Ensure v2/ is importable and takes priority over root core/
_v2_root = str(Path(__file__).resolve().parent.parent)
_repo_root = str(Path(__file__).resolve().parent.parent.parent)

# Remove repo root from sys.path to avoid shadow from root core/
while _repo_root in sys.path:
    sys.path.remove(_repo_root)

if _v2_root not in sys.path:
    sys.path.insert(0, _v2_root)
elif sys.path[0] != _v2_root:
    sys.path.remove(_v2_root)
    sys.path.insert(0, _v2_root)

# Force reload core.pdf_loader from v2/core/ (root-level has a shadowing copy)
import importlib
import importlib.util

# Ensure core package is loaded and points to v2/core/
import core as _core_mod  # noqa: E402
_core_init_path = Path(__file__).resolve().parent.parent / "core" / "__init__.py"
if _core_init_path.exists() and not hasattr(_core_mod, "__path__"):
    _core_mod.__path__ = [str(_core_init_path.parent)]

# Load pdf_loader from v2/core/ (without replacing the core module itself)
_pdf_loader_path = Path(__file__).resolve().parent.parent / "core" / "pdf_loader.py"
_spec = importlib.util.spec_from_file_location("core.pdf_loader", str(_pdf_loader_path))
_pdf_loader_mod = importlib.util.module_from_spec(_spec)
sys.modules["core.pdf_loader"] = _pdf_loader_mod
_spec.loader.exec_module(_pdf_loader_mod)

# Set attribute on core module so patch("core.pdf_loader.xxx") works
_core_mod.pdf_loader = _pdf_loader_mod


# ============================================================
# Test: Fallback strategy
# ============================================================


class TestFallbackStrategy:
    """Verify the three-level fallback mechanism."""

    def test_file_not_found_raises(self):
        """Should raise FileNotFoundError for missing files."""
        from core.pdf_loader import load_pdf_as_sections

        with pytest.raises(FileNotFoundError, match="PDF 文件不存在"):
            load_pdf_as_sections("/nonexistent/path/paper.pdf")

    def test_pymupdf_failure_falls_to_pdfplumber(self, tmp_path):
        """When pymupdf font extraction fails, should try pdfplumber."""
        from core.pdf_loader import load_pdf_as_sections

        # Create a fake PDF file (content doesn't matter since we'll mock)
        fake_pdf = tmp_path / "test.pdf"
        fake_pdf.write_bytes(b"%PDF-1.4 fake content")

        # pdfplumber needs to return >= 3 sections to be accepted
        mock_sections = {
            "introduction": "Some intro text about the study...",
            "methodology": "Some method text describing approach...",
            "results": "Some results showing outcomes...",
        }

        with patch("core.pdf_loader._extract_with_font_info", side_effect=Exception("font extraction failed")):
            with patch("core.pdf_loader._extract_with_pdfplumber", return_value=mock_sections):
                with patch("core.pdf_loader._get_pdfplumber_full_text", return_value="Full text here is long enough for validation..."):
                    result = load_pdf_as_sections(fake_pdf)

        assert "introduction" in result
        assert "full" in result

    def test_all_extractors_fail_uses_regex(self, tmp_path):
        """When both font and pdfplumber fail, should fall back to regex.
        
        The regex Level 3 fallback requires >= 5 numbered academic headings
        to match. We provide 6 headings to safely pass the threshold.
        """
        from core.pdf_loader import load_pdf_as_sections

        fake_pdf = tmp_path / "test.pdf"
        fake_pdf.write_bytes(b"%PDF-1.4 fake")

        long_text = """
1. Introduction

This is the introduction section with enough text to pass the 200 char threshold.
We discuss important topics here that are relevant to the field.

2. Background

Prior work has established the theoretical foundations for our study.
Several researchers have made important contributions.

3. Methods

We use advanced methods to solve the problem. Our approach builds on prior work.
The experimental setup is described in detail below.

4. Results

Our results show significant improvements over baselines across all metrics.
The improvements are statistically significant at p < 0.05.

5. Discussion

These findings have important implications for the broader field.
Several limitations should be noted for future investigation.

6. Conclusion

We conclude that our method is effective and generalizable.
Future work will extend this to other domains.

7. References

[1] Author A. Title of paper. Journal, 2023.
""".strip()

        with patch("core.pdf_loader._extract_with_font_info", side_effect=Exception("fail")):
            with patch("core.pdf_loader._extract_with_pdfplumber", return_value={}):
                with patch("core.pdf_loader._extract_plain_text", return_value=long_text):
                    result = load_pdf_as_sections(fake_pdf)

        assert "full" in result
        # Should have found sections via regex — at least "full" + several sections
        assert len(result) >= 2  # at least "full" + one section
        # Verify specific sections were extracted
        keys_lower = " ".join(result.keys())
        assert "introduction" in keys_lower or "background" in keys_lower

    def test_both_libraries_missing_raises_import_error(self, tmp_path):
        """When neither pymupdf nor pdfplumber is available, should raise ImportError."""
        from core.pdf_loader import load_pdf_as_sections

        fake_pdf = tmp_path / "test.pdf"
        fake_pdf.write_bytes(b"%PDF-1.4 fake")

        with patch("core.pdf_loader._extract_with_font_info", side_effect=ImportError("no pymupdf")):
            with patch("core.pdf_loader._extract_with_pdfplumber", side_effect=ImportError("no pdfplumber")):
                with patch("core.pdf_loader._extract_plain_text", side_effect=ImportError("no pymupdf")):
                    with patch("core.pdf_loader._get_pdfplumber_full_text", side_effect=ImportError("no pdfplumber")):
                        with pytest.raises(ImportError, match="至少"):
                            load_pdf_as_sections(fake_pdf)

    def test_short_text_raises_value_error(self, tmp_path):
        """Should raise ValueError when extracted text is too short."""
        from core.pdf_loader import load_pdf_as_sections

        fake_pdf = tmp_path / "test.pdf"
        fake_pdf.write_bytes(b"%PDF-1.4 fake")

        with patch("core.pdf_loader._extract_with_font_info", side_effect=Exception("fail")):
            with patch("core.pdf_loader._extract_with_pdfplumber", return_value={}):
                with patch("core.pdf_loader._extract_plain_text", return_value="Too short."):
                    with pytest.raises(ValueError, match="过短"):
                        load_pdf_as_sections(fake_pdf)


# ============================================================
# Test: Error tolerance (per-section)
# ============================================================


class TestErrorTolerance:
    """Verify that single section failures don't break the whole pipeline."""

    def test_section_error_does_not_crash(self):
        """If one heading's content extraction fails, other sections still work."""
        from core.pdf_loader import _build_sections_from_headings, HeadingNode, TextSpan

        # Create minimal test data with 3 headings
        spans = [
            TextSpan("Intro text", 10.0, "Regular", 0, 100.0, 50.0),
            TextSpan("Method text", 10.0, "Regular", 0, 200.0, 50.0),
            TextSpan("Result text", 10.0, "Regular", 1, 100.0, 50.0),
        ]

        headings = [
            HeadingNode(title="Introduction", level=1, page_num=0, content_start=0, content_end=1),
            HeadingNode(title="Methods", level=1, page_num=0, content_start=1, content_end=2),
            HeadingNode(title="Results", level=1, page_num=1, content_start=2, content_end=3),
        ]

        # Normal case: all sections should be extracted
        sections = _build_sections_from_headings(
            spans=spans,
            headings=headings,
            body_size=10.0,
            footnote_threshold=8.0,
            appendix_start_idx=10,  # no appendix
        )

        assert len(sections) == 3
        assert "introduction" in sections
        assert "methods" in sections
        assert "results" in sections

    def test_corrupted_heading_skipped(self):
        """A heading with invalid content_start/end should be skipped gracefully.
        
        Out-of-range indices result in an empty slice (no exception in Python),
        so the section will have only the title text. The key point is no crash.
        """
        from core.pdf_loader import _build_sections_from_headings, HeadingNode, TextSpan

        spans = [
            TextSpan("Valid text", 10.0, "Regular", 0, 100.0, 50.0),
        ]

        headings = [
            HeadingNode(title="Good Section", level=1, page_num=0, content_start=0, content_end=1),
            # This heading has out-of-range indices — should be handled gracefully
            HeadingNode(title="Bad Section", level=1, page_num=0, content_start=100, content_end=200),
        ]

        # Should not crash
        sections = _build_sections_from_headings(
            spans=spans,
            headings=headings,
            body_size=10.0,
            footnote_threshold=8.0,
            appendix_start_idx=10,
        )

        # At least the good section should be extracted
        assert "good section" in sections

    def test_exception_in_section_processing_non_fatal(self):
        """If _make_section_key_from_heading raises, that section is skipped but others work."""
        from core.pdf_loader import _build_sections_from_headings, HeadingNode, TextSpan

        spans = [
            TextSpan("Text A", 10.0, "Regular", 0, 100.0, 50.0),
            TextSpan("Text B", 10.0, "Regular", 0, 200.0, 50.0),
        ]

        headings = [
            HeadingNode(title="Section A", level=1, page_num=0, content_start=0, content_end=1),
            HeadingNode(title="Section B", level=1, page_num=0, content_start=1, content_end=2),
        ]

        # Patch _make_section_key_from_heading to raise on second call
        call_count = [0]
        original_func = None
        from core import pdf_loader
        original_func = pdf_loader._make_section_key_from_heading

        def side_effect_key(heading):
            call_count[0] += 1
            if call_count[0] == 2:
                raise RuntimeError("simulated failure")
            return original_func(heading)

        with patch("core.pdf_loader._make_section_key_from_heading", side_effect=side_effect_key):
            sections = _build_sections_from_headings(
                spans=spans,
                headings=headings,
                body_size=10.0,
                footnote_threshold=8.0,
                appendix_start_idx=10,
            )

        # First section should be there, second should be skipped (not crash)
        assert "section a" in sections
        assert "section b" not in sections


# ============================================================
# Test: pdfplumber integration
# ============================================================


class TestPdfplumberIntegration:
    """Verify pdfplumber extraction path."""

    def test_pdfplumber_full_text_extraction(self):
        """_get_pdfplumber_full_text should extract text with layout=True."""
        from core.pdf_loader import _get_pdfplumber_full_text

        mock_pdf = MagicMock()
        mock_page = MagicMock()
        mock_page.extract_text.return_value = """
1. Introduction

This is a comprehensive introduction to our research problem.
We present novel findings that advance the state of the art.

2. Methods

Our methodology is based on established principles.
We extend prior approaches with a new framework.

3. Results

Significant improvements are observed across all benchmarks.
Our model outperforms all baselines consistently.

4. Discussion

These results suggest that our approach is effective.
Future work will explore additional applications.

References

[1] Smith et al. A great paper. Nature, 2024.
"""
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)

        with patch("pdfplumber.open", return_value=mock_pdf):
            text = _get_pdfplumber_full_text(Path("fake.pdf"))
            assert len(text) > 200
            assert "Introduction" in text
            # Verify layout=True was used
            mock_page.extract_text.assert_called_once()
            call_kwargs = mock_page.extract_text.call_args
            assert call_kwargs[1].get("layout") is True or (call_kwargs[0] if call_kwargs[0] else False)

    def test_pdfplumber_single_page_failure_tolerance(self):
        """If one page extraction fails, others should still work."""
        from core.pdf_loader import _get_pdfplumber_full_text

        mock_pdf = MagicMock()
        good_page = MagicMock()
        good_page.extract_text.return_value = "Good content here."
        bad_page = MagicMock()
        bad_page.extract_text.side_effect = Exception("Page corrupted")

        mock_pdf.pages = [good_page, bad_page, good_page]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)

        with patch("pdfplumber.open", return_value=mock_pdf):
            text = _get_pdfplumber_full_text(Path("fake.pdf"))

        # Should have content from 2 good pages
        assert text.count("Good content here.") == 2

    def test_extract_with_pdfplumber_uses_regex_splitting(self):
        """_extract_with_pdfplumber should call _get_pdfplumber_full_text then regex split."""
        from core.pdf_loader import _extract_with_pdfplumber

        long_text = """1. Introduction

This is the introduction section which is quite detailed and goes into depth.

2. Methods

Our methodology involves multiple sophisticated approaches.

3. Results

We observe significant improvements across benchmarks.

4. Discussion

These findings suggest important implications.

5. Conclusion

We conclude with recommendations and future directions.

References

[1] Author. Paper. 2024.
"""
        # Mock pdfplumber to return our test text
        mock_pdf = MagicMock()
        mock_page = MagicMock()
        mock_page.extract_text.return_value = long_text
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)

        with patch("pdfplumber.open", return_value=mock_pdf):
            sections = _extract_with_pdfplumber(Path("fake.pdf"))

        assert len(sections) >= 3
        # Should have split into sections via regex
        section_keys = list(sections.keys())
        assert any("introduction" in k for k in section_keys)


# ============================================================
# Test: Regex section splitting (internal)
# ============================================================


class TestRegexSplitting:
    """Verify the regex-based section splitting handles various formats."""

    def test_numbered_sections(self):
        """Should split on numbered academic headings."""
        from core.pdf_loader import _split_into_sections_regex

        text = """1. Introduction
This paper presents our findings.
We build on prior work in the field.

2. Methods
We employ a randomized controlled trial.
The sample consists of 500 participants.

3. Results
Our method achieves 95% accuracy.
This represents a 10% improvement.

4. Discussion
These findings have important implications.
Several limitations should be noted.

5. Conclusion
We conclude with recommendations.
Future work will address remaining gaps.

6. References
[1] Author A. Paper title. 2023.
[2] Author B. Another paper. 2024."""

        sections = _split_into_sections_regex(text)
        assert len(sections) >= 5
        # Should have found Introduction, Methods, Results, Discussion, Conclusion

    def test_empty_text_returns_empty(self):
        """Empty text should return empty dict."""
        from core.pdf_loader import _split_into_sections_regex
        assert _split_into_sections_regex("") == {}

    def test_no_structure_returns_empty(self):
        """Unstructured text should return empty dict."""
        from core.pdf_loader import _split_into_sections_regex
        result = _split_into_sections_regex("Just some random text without any structure at all.")
        assert result == {}


# ============================================================
# Test: Clean text
# ============================================================


class TestCleanText:
    """Verify text cleaning removes noise but preserves content."""

    def test_removes_page_numbers(self):
        """Should remove isolated page number lines."""
        from core.pdf_loader import _clean_text
        text = "Some content\n\n42\n\nMore content"
        cleaned = _clean_text(text)
        assert "42" not in cleaned.split("\n")

    def test_collapses_excessive_newlines(self):
        """Should collapse 4+ newlines into 3."""
        from core.pdf_loader import _clean_text
        text = "Para 1\n\n\n\n\n\nPara 2"
        cleaned = _clean_text(text)
        assert "\n\n\n\n" not in cleaned


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
