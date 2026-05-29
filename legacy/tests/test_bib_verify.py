"""
Tests for tools/bib_verify.py — BibTeX bibliography verification.

Tests cover:
- .bib file parsing (entries, fields, nested braces)
- Citation extraction from .tex files
- Entry completeness checking (required/recommended fields)
- Citation consistency (undefined refs, orphaned entries)
- Result formatting
"""

import pytest
from pathlib import Path
from tools.bib_verify import (
    parse_bib_file,
    extract_citations_from_tex,
    verify_bib_completeness,
    verify_citation_consistency,
    bib_verify,
    format_bib_result,
    BibEntry,
    BibIssue,
)


# ============================================================
# .bib Parsing Tests
# ============================================================

class TestParseBibFile:
    """Test BibTeX file parsing."""

    def test_parse_article(self, tmp_path):
        """Should parse a standard @article entry."""
        bib_content = """\
@article{smith2023,
    author = {John Smith and Jane Doe},
    title = {A Study on Something Important},
    journal = {Journal of Important Studies},
    year = {2023},
    volume = {42},
    pages = {1--15},
    doi = {10.1234/jis.2023.001},
}
"""
        bib_file = tmp_path / "refs.bib"
        bib_file.write_text(bib_content)

        entries = parse_bib_file(bib_file)
        assert len(entries) == 1
        assert entries[0].key == "smith2023"
        assert entries[0].entry_type == "article"
        assert entries[0].has_field("author")
        assert entries[0].has_field("title")
        assert entries[0].has_field("doi")

    def test_parse_multiple_entries(self, tmp_path):
        """Should parse multiple entries."""
        bib_content = """\
@article{paper1,
    author = {Alice},
    title = {Paper One},
    journal = {J1},
    year = {2020},
}

@inproceedings{paper2,
    author = {Bob},
    title = {Paper Two},
    booktitle = {CONF 2021},
    year = {2021},
}

@book{book1,
    author = {Charlie},
    title = {A Book},
    publisher = {Publisher Inc},
    year = {2019},
}
"""
        bib_file = tmp_path / "refs.bib"
        bib_file.write_text(bib_content)

        entries = parse_bib_file(bib_file)
        assert len(entries) == 3
        types = {e.entry_type for e in entries}
        assert types == {"article", "inproceedings", "book"}

    def test_parse_nested_braces(self, tmp_path):
        """Should handle nested braces in title."""
        bib_content = """\
@article{gpu2022,
    author = {Smith, A.},
    title = {A {GPU}-accelerated Approach to {NLP}},
    journal = {Computing},
    year = {2022},
}
"""
        bib_file = tmp_path / "refs.bib"
        bib_file.write_text(bib_content)

        entries = parse_bib_file(bib_file)
        assert len(entries) == 1
        title = entries[0].fields.get("title", "")
        assert "GPU" in title

    def test_parse_numeric_year(self, tmp_path):
        """Should handle numeric (unbraced) year values."""
        bib_content = """\
@article{old2010,
    author = {Old Author},
    title = {Old Paper},
    journal = {Old Journal},
    year = 2010,
}
"""
        bib_file = tmp_path / "refs.bib"
        bib_file.write_text(bib_content)

        entries = parse_bib_file(bib_file)
        assert len(entries) == 1
        assert entries[0].has_field("year")

    def test_skip_string_and_preamble(self, tmp_path):
        """Should skip @string and @preamble entries."""
        bib_content = """\
@string{jis = {Journal of Important Studies}}
@preamble{"\\newcommand{\\noopsort}[1]{}"}

@article{real2023,
    author = {Real Author},
    title = {Real Paper},
    journal = jis,
    year = {2023},
}
"""
        bib_file = tmp_path / "refs.bib"
        bib_file.write_text(bib_content)

        entries = parse_bib_file(bib_file)
        assert len(entries) == 1
        assert entries[0].key == "real2023"

    def test_empty_file(self, tmp_path):
        """Should return empty list for empty .bib file."""
        bib_file = tmp_path / "empty.bib"
        bib_file.write_text("")

        entries = parse_bib_file(bib_file)
        assert entries == []

    def test_nonexistent_file(self, tmp_path):
        """Should return empty list for nonexistent file."""
        entries = parse_bib_file(tmp_path / "nonexistent.bib")
        assert entries == []


# ============================================================
# Citation Extraction Tests
# ============================================================

class TestCitationExtraction:
    """Test citation key extraction from .tex files."""

    def test_basic_cite(self, tmp_path):
        """Should extract keys from \\cite{}."""
        tex = tmp_path / "main.tex"
        tex.write_text(r"""
\documentclass{article}
\begin{document}
This work builds on \cite{smith2023} and \cite{doe2021,zhang2022}.
\end{document}
""")
        keys = extract_citations_from_tex(tex)
        assert "smith2023" in keys
        assert "doe2021" in keys
        assert "zhang2022" in keys

    def test_natbib_commands(self, tmp_path):
        """Should extract keys from natbib commands."""
        tex = tmp_path / "main.tex"
        tex.write_text(r"""
\documentclass{article}
\begin{document}
According to \citet{author2020}, this is true \citep{other2019}.
\end{document}
""")
        keys = extract_citations_from_tex(tex)
        assert "author2020" in keys
        assert "other2019" in keys

    def test_biblatex_commands(self, tmp_path):
        """Should extract keys from BibLaTeX commands."""
        tex = tmp_path / "main.tex"
        tex.write_text(r"""
\documentclass{article}
\begin{document}
See \autocite{ref1} and \textcite{ref2} or \parencite{ref3}.
\end{document}
""")
        keys = extract_citations_from_tex(tex)
        assert "ref1" in keys
        assert "ref2" in keys
        assert "ref3" in keys

    def test_follows_input(self, tmp_path):
        """Should follow \\input directives."""
        main = tmp_path / "main.tex"
        main.write_text(r"""
\documentclass{article}
\begin{document}
\cite{mainref}
\input{chapter1}
\end{document}
""")
        chapter = tmp_path / "chapter1.tex"
        chapter.write_text(r"\cite{chapref1,chapref2}")

        keys = extract_citations_from_tex(main)
        assert "mainref" in keys
        assert "chapref1" in keys
        assert "chapref2" in keys

    def test_cite_with_options(self, tmp_path):
        """Should handle cite commands with optional arguments."""
        tex = tmp_path / "main.tex"
        tex.write_text(r"""
\cite[p.~42]{source1}
\citep[see][]{source2}
""")
        keys = extract_citations_from_tex(tex)
        assert "source1" in keys
        assert "source2" in keys

    def test_nonexistent_tex(self, tmp_path):
        """Should return empty set for nonexistent file."""
        keys = extract_citations_from_tex(tmp_path / "nonexistent.tex")
        assert keys == set()


# ============================================================
# Completeness Check Tests
# ============================================================

class TestVerifyCompleteness:
    """Test entry completeness verification."""

    def test_complete_article(self):
        """Should find no issues for complete article."""
        entry = BibEntry(
            key="good2023",
            entry_type="article",
            fields={"author": "Smith", "title": "A Paper", "journal": "J1", "year": "2023"},
            line_number=1,
        )
        issues = verify_bib_completeness([entry])
        # Should have no errors/warnings (may have info for recommended fields)
        errors_warnings = [i for i in issues if i.level in ("error", "warning")]
        assert len(errors_warnings) == 0

    def test_missing_required_field(self):
        """Should flag missing required fields."""
        entry = BibEntry(
            key="bad2023",
            entry_type="article",
            fields={"author": "Smith", "title": "A Paper"},  # Missing journal, year
            line_number=5,
        )
        issues = verify_bib_completeness([entry])
        warnings = [i for i in issues if i.level == "warning" and i.category == "missing_field"]
        assert len(warnings) >= 2
        field_names = {i.field_name for i in warnings}
        assert "journal" in field_names
        assert "year" in field_names

    def test_duplicate_keys(self):
        """Should flag duplicate citation keys."""
        entries = [
            BibEntry(key="dup2023", entry_type="article",
                     fields={"author": "A", "title": "T1", "journal": "J", "year": "2023"},
                     line_number=1),
            BibEntry(key="dup2023", entry_type="article",
                     fields={"author": "B", "title": "T2", "journal": "J", "year": "2023"},
                     line_number=10),
        ]
        issues = verify_bib_completeness(entries)
        duplicates = [i for i in issues if i.category == "duplicate"]
        assert len(duplicates) == 1
        assert duplicates[0].level == "error"

    def test_editor_substitutes_author(self):
        """Should accept editor in place of author for books."""
        entry = BibEntry(
            key="edited2023",
            entry_type="book",
            fields={"editor": "E. Ditor", "title": "Collected Works", "publisher": "Pub", "year": "2023"},
            line_number=1,
        )
        issues = verify_bib_completeness([entry])
        # Should NOT flag missing author since editor is present
        author_issues = [i for i in issues if i.field_name == "author" and i.level == "warning"]
        assert len(author_issues) == 0


# ============================================================
# Citation Consistency Tests
# ============================================================

class TestCitationConsistency:
    """Test cross-reference checking."""

    def test_all_consistent(self):
        """Should report no issues when all citations match."""
        cited = {"a", "b", "c"}
        bib = {"a", "b", "c", "d"}  # d is orphaned but not an error

        undefined, orphaned, issues = verify_citation_consistency(cited, bib)
        assert undefined == set()
        assert orphaned == {"d"}

    def test_undefined_refs(self):
        """Should flag citations not in .bib."""
        cited = {"exists", "missing1", "missing2"}
        bib = {"exists", "other"}

        undefined, orphaned, issues = verify_citation_consistency(cited, bib)
        assert undefined == {"missing1", "missing2"}
        errors = [i for i in issues if i.level == "error"]
        assert len(errors) == 2

    def test_orphaned_entries(self):
        """Should flag .bib entries never cited."""
        cited = {"used1"}
        bib = {"used1", "unused1", "unused2"}

        undefined, orphaned, issues = verify_citation_consistency(cited, bib)
        assert orphaned == {"unused1", "unused2"}
        infos = [i for i in issues if i.level == "info"]
        assert len(infos) == 2


# ============================================================
# Integration Entry Point Tests
# ============================================================

class TestBibVerify:
    """Test the main bib_verify entry point."""

    def test_no_bib_file(self, tmp_path):
        """Should return unavailable when no .bib found."""
        result = bib_verify(project_dir=str(tmp_path))
        assert result["status"] == "unavailable"

    def test_full_pipeline(self, tmp_path):
        """Should run full verification pipeline."""
        # Create .bib
        bib_file = tmp_path / "refs.bib"
        bib_file.write_text("""\
@article{cited2023,
    author = {Author A},
    title = {Cited Paper},
    journal = {J1},
    year = {2023},
}

@article{orphaned2020,
    author = {Author B},
    title = {Orphaned Paper},
    journal = {J2},
    year = {2020},
}
""")
        # Create .tex
        tex_file = tmp_path / "main.tex"
        tex_file.write_text(r"""
\documentclass{article}
\begin{document}
\cite{cited2023}
\cite{undefined_ref}
\end{document}
""")
        result = bib_verify(
            bib_path=str(bib_file),
            tex_path=str(tex_file),
            project_dir=str(tmp_path),
        )
        assert result["status"] == "errors"  # undefined_ref is an error
        assert "undefined_ref" in result["undefined_refs"]
        assert "orphaned2020" in result["orphaned_entries"]


# ============================================================
# Formatting Tests
# ============================================================

class TestFormatResult:
    """Test human-readable formatting."""

    def test_format_clean(self):
        result = {
            "status": "clean",
            "total_entries": 15,
            "error_count": 0,
            "warning_count": 0,
            "issues": [],
            "cited_keys": [],
            "bib_keys": [],
            "undefined_refs": [],
            "orphaned_entries": [],
            "guidance": "Bibliography clean.",
        }
        output = format_bib_result(result)
        assert "✅" in output
        assert "15" in output

    def test_format_errors(self):
        result = {
            "status": "errors",
            "total_entries": 10,
            "error_count": 2,
            "warning_count": 1,
            "issues": [
                {"level": "error", "category": "undefined_ref", "message": "Citation 'x' undefined",
                 "entry_key": "x", "field_name": None, "line_number": None},
            ],
            "cited_keys": [],
            "bib_keys": [],
            "undefined_refs": ["missing1", "missing2"],
            "orphaned_entries": [],
            "guidance": "Fix undefined references.",
        }
        output = format_bib_result(result)
        assert "❌" in output
        assert "missing1" in output
