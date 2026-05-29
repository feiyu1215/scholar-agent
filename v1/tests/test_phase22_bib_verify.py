"""
Tests for core/bib_verify.py — Citation consistency verification tool.

Tests cover:
- BibTeX parsing (entries, fields, nested braces, edge cases)
- Citation extraction from LaTeX text
- Entry completeness checking (required/recommended fields)
- Citation consistency (undefined refs, orphaned entries)
- Content-based API (no file I/O needed)
- Directory-based auto-discovery
- Result summary formatting
"""

import pytest
from pathlib import Path
from core.bib_verify import (
    parse_bib_content,
    extract_citations_from_text,
    extract_citations_from_file,
    verify_bib_completeness,
    verify_citation_consistency,
    verify_citations,
    find_bib_file,
    find_main_tex,
    BibEntry,
    BibIssue,
    BibVerifyResult,
)


# ============================================================
# BibTeX Parsing
# ============================================================

class TestParseBibContent:
    """Test BibTeX content parsing."""

    def test_parse_article(self):
        bib = """\
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
        entries = parse_bib_content(bib)
        assert len(entries) == 1
        assert entries[0].key == "smith2023"
        assert entries[0].entry_type == "article"
        assert entries[0].has_field("author")
        assert entries[0].has_field("doi")

    def test_parse_multiple_entries(self):
        bib = """\
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
    title = {A Book Title},
    publisher = {Publisher Inc},
    year = {2019},
}
"""
        entries = parse_bib_content(bib)
        assert len(entries) == 3
        types = {e.entry_type for e in entries}
        assert types == {"article", "inproceedings", "book"}

    def test_parse_nested_braces(self):
        bib = """\
@article{gpu2022,
    author = {Smith, A.},
    title = {A {GPU}-accelerated Approach to {NLP}},
    journal = {Computing},
    year = {2022},
}
"""
        entries = parse_bib_content(bib)
        assert len(entries) == 1
        title = entries[0].fields.get("title", "")
        assert "GPU" in title

    def test_parse_numeric_year(self):
        bib = """\
@article{old2010,
    author = {Old Author},
    title = {Old Paper Title Here},
    journal = {Old Journal},
    year = 2010,
}
"""
        entries = parse_bib_content(bib)
        assert len(entries) == 1
        assert entries[0].has_field("year")

    def test_skip_string_preamble_comment(self):
        bib = """\
@string{jis = {Journal of Important Studies}}
@preamble{"\\newcommand{\\noopsort}[1]{}"}
@comment{This is a comment.}

@article{real2023,
    author = {Real Author},
    title = {Real Paper Title},
    journal = jis,
    year = {2023},
}
"""
        entries = parse_bib_content(bib)
        assert len(entries) == 1
        assert entries[0].key == "real2023"

    def test_empty_content(self):
        assert parse_bib_content("") == []
        assert parse_bib_content("   \n\n  ") == []

    def test_quoted_values(self):
        bib = """\
@article{quoted2023,
    author = "Smith, John",
    title = "A Quoted Title Paper",
    journal = "Some Journal",
    year = "2023",
}
"""
        entries = parse_bib_content(bib)
        assert len(entries) == 1
        assert entries[0].has_field("author")


# ============================================================
# Citation Extraction
# ============================================================

class TestCitationExtraction:
    """Test citation key extraction from LaTeX text."""

    def test_basic_cite(self):
        tex = r"""
\documentclass{article}
\begin{document}
This work builds on \cite{smith2023} and \cite{doe2021,zhang2022}.
\end{document}
"""
        keys = extract_citations_from_text(tex)
        assert "smith2023" in keys
        assert "doe2021" in keys
        assert "zhang2022" in keys

    def test_natbib_commands(self):
        tex = r"""
According to \citet{author2020}, this is true \citep{other2019}.
"""
        keys = extract_citations_from_text(tex)
        assert "author2020" in keys
        assert "other2019" in keys

    def test_biblatex_commands(self):
        tex = r"""
See \autocite{ref1} and \textcite{ref2} or \parencite{ref3}.
"""
        keys = extract_citations_from_text(tex)
        assert "ref1" in keys
        assert "ref2" in keys
        assert "ref3" in keys

    def test_cite_with_options(self):
        tex = r"""
\cite[p.~42]{source1}
\citep[see][]{source2}
"""
        keys = extract_citations_from_text(tex)
        assert "source1" in keys
        assert "source2" in keys

    def test_multiple_keys_in_one_cite(self):
        tex = r"\cite{a,b,c, d , e}"
        keys = extract_citations_from_text(tex)
        assert keys == {"a", "b", "c", "d", "e"}

    def test_empty_text(self):
        assert extract_citations_from_text("") == set()
        assert extract_citations_from_text(None) == set()

    def test_no_citations(self):
        tex = r"""
\documentclass{article}
\begin{document}
This paper has no citations at all.
\end{document}
"""
        keys = extract_citations_from_text(tex)
        assert keys == set()


class TestCitationExtractionFromFile:
    """Test file-based citation extraction with \\input following."""

    def test_follows_input(self, tmp_path):
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

        keys = extract_citations_from_file(main)
        assert "mainref" in keys
        assert "chapref1" in keys
        assert "chapref2" in keys

    def test_nonexistent_file(self, tmp_path):
        keys = extract_citations_from_file(tmp_path / "nonexistent.tex")
        assert keys == set()


# ============================================================
# Completeness Verification
# ============================================================

class TestVerifyCompleteness:
    """Test entry completeness verification."""

    def test_complete_article(self):
        entry = BibEntry(
            key="good2023", entry_type="article",
            fields={"author": "Smith", "title": "A Good Paper", "journal": "J1", "year": "2023"},
            line_number=1,
        )
        issues = verify_bib_completeness([entry])
        errors_warnings = [i for i in issues if i.level in ("error", "warning")]
        assert len(errors_warnings) == 0

    def test_missing_required_field(self):
        entry = BibEntry(
            key="bad2023", entry_type="article",
            fields={"author": "Smith", "title": "A Bad Paper"},  # Missing journal, year
            line_number=5,
        )
        issues = verify_bib_completeness([entry])
        warnings = [i for i in issues if i.level == "warning" and i.category == "missing_field"]
        assert len(warnings) >= 2
        field_names = {i.field_name for i in warnings}
        assert "journal" in field_names
        assert "year" in field_names

    def test_duplicate_keys(self):
        entries = [
            BibEntry(key="dup2023", entry_type="article",
                     fields={"author": "A", "title": "Title One", "journal": "J", "year": "2023"},
                     line_number=1),
            BibEntry(key="dup2023", entry_type="article",
                     fields={"author": "B", "title": "Title Two", "journal": "J", "year": "2023"},
                     line_number=10),
        ]
        issues = verify_bib_completeness(entries)
        duplicates = [i for i in issues if i.category == "duplicate"]
        assert len(duplicates) == 1
        assert duplicates[0].level == "error"

    def test_editor_substitutes_author(self):
        entry = BibEntry(
            key="edited2023", entry_type="book",
            fields={"editor": "E. Ditor", "title": "Collected Works", "publisher": "Pub", "year": "2023"},
            line_number=1,
        )
        issues = verify_bib_completeness([entry])
        author_issues = [i for i in issues if i.field_name == "author" and i.level == "warning"]
        assert len(author_issues) == 0

    def test_short_title_warning(self):
        entry = BibEntry(
            key="short2023", entry_type="article",
            fields={"author": "X", "title": "Hi", "journal": "J", "year": "2023"},
            line_number=1,
        )
        issues = verify_bib_completeness([entry])
        title_issues = [i for i in issues if i.category == "format"]
        assert len(title_issues) == 1


# ============================================================
# Citation Consistency
# ============================================================

class TestCitationConsistency:
    """Test cross-reference checking."""

    def test_all_consistent(self):
        cited = {"a", "b", "c"}
        bib = {"a", "b", "c", "d"}

        undefined, orphaned, issues = verify_citation_consistency(cited, bib)
        assert undefined == set()
        assert orphaned == {"d"}

    def test_undefined_refs(self):
        cited = {"exists", "missing1", "missing2"}
        bib = {"exists", "other"}

        undefined, orphaned, issues = verify_citation_consistency(cited, bib)
        assert undefined == {"missing1", "missing2"}
        errors = [i for i in issues if i.level == "error"]
        assert len(errors) == 2

    def test_orphaned_entries(self):
        cited = {"used1"}
        bib = {"used1", "unused1", "unused2"}

        undefined, orphaned, issues = verify_citation_consistency(cited, bib)
        assert orphaned == {"unused1", "unused2"}


# ============================================================
# High-Level verify_citations API
# ============================================================

class TestVerifyCitations:
    """Test the main verify_citations entry point."""

    def test_no_bib_content(self):
        """Should return unavailable when no bib content."""
        result = verify_citations()
        assert result.status == "unavailable"

    def test_content_based_full_pipeline(self):
        """Should run full verification with content-based API."""
        bib = """\
@article{cited2023,
    author = {Author A},
    title = {Cited Paper Title},
    journal = {J1},
    year = {2023},
}

@article{orphaned2020,
    author = {Author B},
    title = {Orphaned Paper Title},
    journal = {J2},
    year = {2020},
}
"""
        tex = r"""
\documentclass{article}
\begin{document}
\cite{cited2023}
\cite{undefined_ref}
\end{document}
"""
        result = verify_citations(bib_content=bib, tex_content=tex)
        assert result.status == "errors"
        assert "undefined_ref" in result.undefined_refs
        assert "orphaned2020" in result.orphaned_entries
        assert result.total_entries == 2
        assert result.error_count >= 1

    def test_bib_only_no_tex(self):
        """Should check completeness even without tex content."""
        bib = """\
@article{paper2023,
    author = {Author},
    title = {A Paper With Title},
    journal = {Journal},
    year = {2023},
}
"""
        result = verify_citations(bib_content=bib)
        assert result.status == "clean"
        assert result.total_entries == 1

    def test_directory_based(self, tmp_path):
        """Should auto-discover files from directory."""
        bib_file = tmp_path / "refs.bib"
        bib_file.write_text("""\
@article{found2023,
    author = {Auto},
    title = {Found Paper Title},
    journal = {J},
    year = {2023},
}
""")
        tex_file = tmp_path / "main.tex"
        tex_file.write_text(r"""
\documentclass{article}
\begin{document}
\cite{found2023}
\cite{notfound}
\end{document}
""")
        result = verify_citations(project_dir=str(tmp_path))
        assert result.status == "errors"
        assert "notfound" in result.undefined_refs
        assert result.total_entries == 1

    def test_check_orphaned_flag(self):
        """Should suppress orphaned issues when check_orphaned=False."""
        bib = """\
@article{used2023,
    author = {A},
    title = {Used Paper Title},
    journal = {J},
    year = {2023},
}
@article{unused2023,
    author = {B},
    title = {Unused Paper Title},
    journal = {J},
    year = {2023},
}
"""
        tex = r"\cite{used2023}"
        result = verify_citations(bib_content=bib, tex_content=tex, check_orphaned=False)
        orphaned_issues = [i for i in result.issues if i.category == "orphaned"]
        assert len(orphaned_issues) == 0


# ============================================================
# File Discovery
# ============================================================

class TestFileDiscovery:
    """Test bib/tex file discovery helpers."""

    def test_find_bib_common_name(self, tmp_path):
        (tmp_path / "references.bib").write_text("@article{x, title={T}}")
        (tmp_path / "other.bib").write_text("@article{y, title={T}}")
        found = find_bib_file(tmp_path)
        assert found.name == "references.bib"

    def test_find_bib_largest(self, tmp_path):
        (tmp_path / "small.bib").write_text("x")
        (tmp_path / "big.bib").write_text("x" * 1000)
        found = find_bib_file(tmp_path)
        assert found.name == "big.bib"

    def test_find_main_tex(self, tmp_path):
        (tmp_path / "chapter.tex").write_text(r"\section{Intro}")
        (tmp_path / "main.tex").write_text(r"\documentclass{article}" + "\n" + r"\begin{document}")
        found = find_main_tex(tmp_path)
        assert found.name == "main.tex"

    def test_no_bib_file(self, tmp_path):
        assert find_bib_file(tmp_path) is None

    def test_no_tex_file(self, tmp_path):
        assert find_main_tex(tmp_path) is None


# ============================================================
# Result Summary
# ============================================================

class TestResultSummary:
    """Test summary formatting for Agent output."""

    def test_clean_summary(self):
        result = BibVerifyResult(
            status="clean", total_entries=15, error_count=0, warning_count=0,
        )
        summary = result.summary()
        assert "✅" in summary
        assert "15" in summary

    def test_error_summary(self):
        result = BibVerifyResult(
            status="errors", total_entries=10, error_count=2, warning_count=1,
            undefined_refs={"missing1", "missing2"},
            issues=[
                BibIssue(level="error", category="undefined_ref", message="test", entry_key="missing1"),
            ],
        )
        summary = result.summary()
        assert "❌" in summary
        assert "missing1" in summary

    def test_unavailable_summary(self):
        result = BibVerifyResult(status="unavailable")
        summary = result.summary()
        assert "📋" in summary
