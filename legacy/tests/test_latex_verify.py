"""
Tests for tools/latex_verify.py — LaTeX compilation verification.

Tests cover:
- .log file parsing (errors, warnings, overfull boxes)
- LaTeX environment detection (graceful degradation)
- Main tex file discovery
- Result formatting
"""

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from tools.latex_verify import (
    parse_log_file,
    find_main_tex,
    compile_latex,
    latex_verify,
    format_latex_result,
    check_latex_availability,
    LatexError,
    LatexVerifyResult,
)


# ============================================================
# .log Parsing Tests
# ============================================================

class TestLogParsing:
    """Test LaTeX .log file parsing."""

    def test_parse_errors(self, tmp_path):
        """Should extract ! errors with line numbers."""
        log_content = """\
This is pdfTeX, Version 3.141592653
(./main.tex
! Undefined control sequence.
l.42 \\badcommand
               
! Missing $ inserted.
l.55 some math x^2
"""
        log_file = tmp_path / "main.log"
        log_file.write_text(log_content)

        errors, warnings = parse_log_file(log_file)
        assert len(errors) == 2
        assert errors[0].level == "error"
        assert "Undefined control sequence" in errors[0].message
        assert errors[0].line == 42
        assert errors[1].line == 55

    def test_parse_warnings(self, tmp_path):
        """Should extract LaTeX/Package warnings."""
        log_content = """\
(./main.tex
LaTeX Warning: Reference `fig:missing' on page 3 undefined on input line 120.

Package hyperref Warning: Token not allowed in a PDF string (Unicode):

LaTeX Warning: Citation `smith2024' on page 5 undefined on input line 200.
"""
        log_file = tmp_path / "main.log"
        log_file.write_text(log_content)

        errors, warnings = parse_log_file(log_file)
        assert len(errors) == 0
        assert len(warnings) >= 2
        assert any("fig:missing" in w.message for w in warnings)
        assert any("smith2024" in w.message or "Citation" in w.message for w in warnings)

    def test_parse_overfull_boxes(self, tmp_path):
        """Should extract overfull/underfull box warnings."""
        log_content = """\
(./main.tex
Overfull \\hbox (15.2pt too wide) in paragraph at lines 30--35
Underfull \\vbox (badness 10000) has occurred while \\output is active
"""
        log_file = tmp_path / "main.log"
        log_file.write_text(log_content)

        errors, warnings = parse_log_file(log_file)
        assert len(errors) == 0
        assert len(warnings) >= 1
        assert any("Overfull" in w.message for w in warnings)

    def test_parse_empty_log(self, tmp_path):
        """Should handle empty log files gracefully."""
        log_file = tmp_path / "empty.log"
        log_file.write_text("")

        errors, warnings = parse_log_file(log_file)
        assert errors == []
        assert warnings == []

    def test_parse_nonexistent_log(self, tmp_path):
        """Should handle nonexistent log files gracefully."""
        errors, warnings = parse_log_file(tmp_path / "nonexistent.log")
        assert errors == []
        assert warnings == []


# ============================================================
# File Discovery Tests
# ============================================================

class TestFindMainTex:
    """Test main .tex file discovery."""

    def test_finds_documentclass(self, tmp_path):
        """Should find file containing \\documentclass."""
        # Decoy file
        (tmp_path / "chapter1.tex").write_text("\\section{Intro}")
        # Main file
        (tmp_path / "paper.tex").write_text("\\documentclass{article}\n\\begin{document}")

        result = find_main_tex(tmp_path)
        assert result is not None
        assert result.name == "paper.tex"

    def test_falls_back_to_common_names(self, tmp_path):
        """Should fall back to main.tex if no \\documentclass found."""
        (tmp_path / "main.tex").write_text("% just a comment")
        (tmp_path / "appendix.tex").write_text("% appendix")

        result = find_main_tex(tmp_path)
        assert result is not None
        assert result.name == "main.tex"

    def test_returns_none_if_no_tex(self, tmp_path):
        """Should return None if no .tex files exist."""
        (tmp_path / "readme.md").write_text("# Hello")

        result = find_main_tex(tmp_path)
        assert result is None

    def test_finds_in_subdirectory(self, tmp_path):
        """Should search subdirectories if root has no .tex."""
        sub = tmp_path / "src"
        sub.mkdir()
        (sub / "main.tex").write_text("\\documentclass{article}")

        result = find_main_tex(tmp_path)
        assert result is not None
        assert result.name == "main.tex"


# ============================================================
# Compilation Tests (with mocked subprocess)
# ============================================================

class TestCompileLatex:
    """Test compilation with mocked system calls."""

    @patch("tools.latex_verify.check_latex_availability", return_value=False)
    def test_unavailable_graceful_degradation(self, mock_avail, tmp_path):
        """Should return 'unavailable' with guidance when latexmk not found."""
        tex_file = tmp_path / "main.tex"
        tex_file.write_text("\\documentclass{article}")

        result = compile_latex(tex_file)
        assert result.status == "unavailable"
        assert "latexmk" in result.guidance.lower()

    @patch("tools.latex_verify.check_latex_availability", return_value=True)
    def test_file_not_found(self, mock_avail, tmp_path):
        """Should return error when .tex file doesn't exist."""
        result = compile_latex(tmp_path / "nonexistent.tex")
        assert result.status == "errors"
        assert result.error_count == 1
        assert "not found" in result.errors[0].message.lower()

    @patch("tools.latex_verify.check_latex_availability", return_value=True)
    @patch("subprocess.run")
    def test_successful_compilation(self, mock_run, mock_avail, tmp_path):
        """Should return success when latexmk exits 0."""
        tex_file = tmp_path / "main.tex"
        tex_file.write_text("\\documentclass{article}\\begin{document}Hello\\end{document}")
        # Create a clean log
        log_file = tmp_path / "main.log"
        log_file.write_text("This is pdfTeX\n(./main.tex)\nOutput written on main.pdf")

        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        result = compile_latex(tex_file)
        assert result.status == "success"
        assert result.error_count == 0

    @patch("tools.latex_verify.check_latex_availability", return_value=True)
    @patch("subprocess.run")
    def test_compilation_with_errors(self, mock_run, mock_avail, tmp_path):
        """Should parse errors when latexmk exits non-zero."""
        tex_file = tmp_path / "main.tex"
        tex_file.write_text("\\documentclass{article}")
        # Create a log with errors
        log_file = tmp_path / "main.log"
        log_file.write_text(
            "(./main.tex\n! Undefined control sequence.\nl.10 \\badcmd\n"
        )

        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")

        result = compile_latex(tex_file)
        assert result.status == "errors"
        assert result.error_count >= 1

    @patch("tools.latex_verify.check_latex_availability", return_value=True)
    @patch("subprocess.run", side_effect=__import__("subprocess").TimeoutExpired("latexmk", 120))
    def test_timeout(self, mock_run, mock_avail, tmp_path):
        """Should return timeout status when compilation exceeds limit."""
        tex_file = tmp_path / "main.tex"
        tex_file.write_text("\\documentclass{article}")

        result = compile_latex(tex_file)
        assert result.status == "timeout"
        assert "timed out" in result.guidance.lower()


# ============================================================
# Integration Entry Point Tests
# ============================================================

class TestLatexVerify:
    """Test the main latex_verify entry point."""

    @patch("tools.latex_verify.check_latex_availability", return_value=False)
    def test_unavailable_returns_guidance(self, mock_avail, tmp_path):
        """Full pipeline returns guidance when LaTeX unavailable."""
        tex_file = tmp_path / "main.tex"
        tex_file.write_text("\\documentclass{article}")

        result = latex_verify(tex_path=str(tex_file))
        assert result["status"] == "unavailable"
        assert "guidance" in result

    def test_no_tex_file_found(self, tmp_path):
        """Should report error when no .tex file in project_dir."""
        result = latex_verify(project_dir=str(tmp_path))
        assert result["status"] == "errors"
        assert result["error_count"] >= 1


# ============================================================
# Formatting Tests
# ============================================================

class TestFormatResult:
    """Test human-readable formatting."""

    def test_format_success(self):
        result = {
            "status": "success",
            "tex_file": "main.tex",
            "compilation_time": 5.2,
            "error_count": 0,
            "warning_count": 0,
            "errors": [],
            "warnings": [],
            "guidance": "LaTeX compilation successful.",
        }
        output = format_latex_result(result)
        assert "✅" in output
        assert "success" in output

    def test_format_errors(self):
        result = {
            "status": "errors",
            "tex_file": "main.tex",
            "compilation_time": 2.1,
            "error_count": 2,
            "warning_count": 1,
            "errors": [
                {"level": "error", "message": "Undefined control sequence", "line": 42, "file": None, "context": ""},
                {"level": "error", "message": "Missing $ inserted", "line": 55, "file": None, "context": ""},
            ],
            "warnings": [
                {"level": "warning", "message": "Reference undefined", "file": None, "line": None, "context": ""},
            ],
            "guidance": "Fix errors before submission.",
        }
        output = format_latex_result(result)
        assert "❌" in output
        assert "Undefined control sequence" in output
        assert "line 42" in output

    def test_format_unavailable(self):
        result = {
            "status": "unavailable",
            "tex_file": "",
            "compilation_time": 0,
            "error_count": 0,
            "warning_count": 0,
            "errors": [],
            "warnings": [],
            "guidance": "LaTeX not installed.",
        }
        output = format_latex_result(result)
        assert "📋" in output
        assert "unavailable" in output
