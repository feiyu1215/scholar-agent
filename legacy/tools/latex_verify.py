"""
tools/latex_verify.py — LaTeX compilation verification with graceful degradation.

Runs latexmk on the user's .tex project to verify compilation succeeds
after agent-driven modifications. Parses .log output for errors/warnings.

Design choices (mirroring tools/stata_verify.py):
- Graceful degradation: if latexmk not installed, outputs guidance
- .log error parsing: extracts structured errors with line numbers
- Zero-LLM: purely mechanical verification (no API calls)
- Timeout: 120s max for compilation; falls back to guidance on timeout
- Non-destructive: runs in --draftmode (no PDF output) to save time

Integration:
- Called by agent loop when processing format/compilation issues
- Results feed back into action_router routing decisions
- Registered as tool "latex_verify" in tool_schemas/dispatch/metadata
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

WORKSPACE = Path(".workspace")
LATEX_TIMEOUT = 120  # seconds


# ============================================================
# Data Classes
# ============================================================

@dataclass
class LatexError:
    """A single LaTeX error/warning extracted from .log file."""
    level: str          # "error" | "warning" | "info"
    message: str        # Error message text
    file: Optional[str] = None   # Source file if identifiable
    line: Optional[int] = None   # Line number if available
    context: str = ""   # Surrounding lines for context

    def to_dict(self) -> dict:
        return {
            "level": self.level,
            "message": self.message,
            "file": self.file,
            "line": self.line,
            "context": self.context,
        }


@dataclass
class LatexVerifyResult:
    """Result of a LaTeX compilation verification."""
    status: str         # "success" | "warnings_only" | "errors" | "unavailable" | "timeout"
    errors: List[LatexError] = field(default_factory=list)
    warnings: List[LatexError] = field(default_factory=list)
    error_count: int = 0
    warning_count: int = 0
    compilation_time: float = 0.0
    tex_file: str = ""
    log_excerpt: str = ""
    guidance: str = ""

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "errors": [e.to_dict() for e in self.errors],
            "warnings": [w.to_dict() for w in self.warnings],
            "compilation_time": self.compilation_time,
            "tex_file": self.tex_file,
            "log_excerpt": self.log_excerpt,
            "guidance": self.guidance,
        }


# ============================================================
# LaTeX Environment Detection
# ============================================================

_latexmk_available: Optional[bool] = None


def check_latex_availability() -> bool:
    """Check if latexmk is available on the system. Caches result."""
    global _latexmk_available
    if _latexmk_available is not None:
        return _latexmk_available

    _latexmk_available = shutil.which("latexmk") is not None
    return _latexmk_available


def get_latex_version() -> Optional[str]:
    """Get latexmk version string, or None if unavailable."""
    if not check_latex_availability():
        return None
    try:
        result = subprocess.run(
            ["latexmk", "--version"],
            capture_output=True, text=True, timeout=10
        )
        # First line typically contains version
        return result.stdout.strip().split("\n")[0] if result.stdout else None
    except (subprocess.TimeoutExpired, OSError):
        return None


# ============================================================
# .log File Parsing
# ============================================================

# Patterns for LaTeX log parsing
_ERROR_PATTERN = re.compile(
    r"^! (.+?)$", re.MULTILINE
)
_WARNING_PATTERN = re.compile(
    r"^(?:LaTeX|Package|Class)\s+(?:\w+\s+)?Warning:\s*(.+?)$", re.MULTILINE
)
_OVERFULL_PATTERN = re.compile(
    r"^(Overfull \\[hv]box .+?)$", re.MULTILINE
)
_UNDERFULL_PATTERN = re.compile(
    r"^(Underfull \\[hv]box .+?)$", re.MULTILINE
)
_LINE_NUMBER_PATTERN = re.compile(
    r"l\.(\d+)\s*(.*)"
)
_FILE_PATTERN = re.compile(
    r"\(([^\s()]+\.(?:tex|sty|cls|bbl|aux))"
)


def parse_log_file(log_path: Path) -> Tuple[List[LatexError], List[LatexError]]:
    """
    Parse a LaTeX .log file into structured errors and warnings.
    
    Returns:
        (errors, warnings) — lists of LatexError objects
    """
    if not log_path.exists():
        return [], []

    try:
        content = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return [], []

    errors: List[LatexError] = []
    warnings: List[LatexError] = []

    # Track current file context via the parenthesis-based file stack
    current_file = _extract_current_file(content)

    # Parse errors (lines starting with "!")
    for match in _ERROR_PATTERN.finditer(content):
        msg = match.group(1).strip()
        # Look for line number in the next few lines
        after = content[match.end():match.end() + 200]
        line_match = _LINE_NUMBER_PATTERN.search(after)
        line_num = int(line_match.group(1)) if line_match else None
        context = line_match.group(2).strip() if line_match else ""

        errors.append(LatexError(
            level="error",
            message=msg,
            file=current_file,
            line=line_num,
            context=context,
        ))

    # Parse warnings
    for match in _WARNING_PATTERN.finditer(content):
        msg = match.group(1).strip()
        # Multi-line warnings: collect continuation lines
        after = content[match.end():match.end() + 300]
        continuation = []
        for line in after.split("\n"):
            line = line.strip()
            if not line or line.startswith("(") or line.startswith("!"):
                break
            continuation.append(line)
        if continuation:
            msg += " " + " ".join(continuation)

        warnings.append(LatexError(
            level="warning",
            message=msg[:200],  # Cap length
            file=current_file,
        ))

    # Parse overfull/underfull boxes (as info-level warnings)
    for pattern, level_name in [
        (_OVERFULL_PATTERN, "warning"),
        (_UNDERFULL_PATTERN, "info"),
    ]:
        for match in pattern.finditer(content):
            # Only include overfull as warnings; underfull is info
            # Limit to first 20 box warnings to avoid noise
            if len(warnings) > 50:
                break
            warnings.append(LatexError(
                level=level_name,
                message=match.group(1).strip()[:150],
                file=current_file,
            ))

    return errors, warnings


def _extract_current_file(log_content: str) -> Optional[str]:
    """Extract the main .tex file from log opening parentheses."""
    match = _FILE_PATTERN.search(log_content[:500])
    if match:
        return match.group(1)
    return None


# ============================================================
# Compilation
# ============================================================

def find_main_tex(project_dir: Path) -> Optional[Path]:
    """
    Find the main .tex file in a project directory.
    
    Heuristic order:
    1. File containing \\documentclass
    2. main.tex / paper.tex / manuscript.tex
    3. First .tex file found
    """
    tex_files = list(project_dir.glob("*.tex"))
    if not tex_files:
        # Also check one level deep
        tex_files = list(project_dir.glob("**/*.tex"))
        if not tex_files:
            return None

    # Priority 1: Look for \documentclass
    for tf in tex_files:
        try:
            content = tf.read_text(encoding="utf-8", errors="replace")[:2000]
            if r"\documentclass" in content:
                return tf
        except OSError:
            continue

    # Priority 2: Common names
    common_names = ["main.tex", "paper.tex", "manuscript.tex", "article.tex"]
    for name in common_names:
        candidate = project_dir / name
        if candidate.exists():
            return candidate

    # Priority 3: First .tex file
    return tex_files[0] if tex_files else None


def compile_latex(
    tex_path: Path,
    timeout: int = LATEX_TIMEOUT,
    draft_mode: bool = True,
) -> LatexVerifyResult:
    """
    Run latexmk on a .tex file and return structured results.
    
    Args:
        tex_path: Path to the main .tex file
        timeout: Maximum compilation time in seconds
        draft_mode: If True, use -draftmode (faster, no PDF)
    
    Returns:
        LatexVerifyResult with parsed errors/warnings
    """
    if not check_latex_availability():
        return LatexVerifyResult(
            status="unavailable",
            tex_file=str(tex_path),
            guidance=(
                "LaTeX environment (latexmk) not found on this system.\n"
                "To verify compilation manually:\n"
                f"  1. cd {tex_path.parent}\n"
                f"  2. latexmk -pdf {tex_path.name}\n"
                "  3. Check the .log file for errors/warnings.\n"
                "\n"
                "Install guide: https://mg.readthedocs.io/latexmk.html"
            ),
        )

    if not tex_path.exists():
        return LatexVerifyResult(
            status="errors",
            tex_file=str(tex_path),
            error_count=1,
            errors=[LatexError(
                level="error",
                message=f"File not found: {tex_path}",
            )],
            guidance=f"The specified .tex file does not exist: {tex_path}",
        )

    # Build latexmk command
    cmd = [
        "latexmk",
        "-pdf",
        "-interaction=nonstopmode",
        "-halt-on-error",
        f"-outdir={tex_path.parent}",
    ]
    if draft_mode:
        cmd.append("-draftmode")
    cmd.append(str(tex_path.name))

    start_time = time.time()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(tex_path.parent),
        )
        compilation_time = time.time() - start_time
    except subprocess.TimeoutExpired:
        return LatexVerifyResult(
            status="timeout",
            tex_file=str(tex_path),
            compilation_time=timeout,
            guidance=(
                f"LaTeX compilation timed out after {timeout}s.\n"
                "This may indicate an infinite loop (e.g., recursive \\input) "
                "or very large document. Try compiling manually with:\n"
                f"  latexmk -pdf {tex_path.name}"
            ),
        )
    except OSError as e:
        return LatexVerifyResult(
            status="errors",
            tex_file=str(tex_path),
            error_count=1,
            errors=[LatexError(level="error", message=f"OS error: {e}")],
            guidance=f"Failed to run latexmk: {e}",
        )

    # Parse the .log file
    log_path = tex_path.with_suffix(".log")
    errors, warnings = parse_log_file(log_path)

    # Determine status
    if result.returncode == 0 and not errors:
        if warnings:
            status = "warnings_only"
        else:
            status = "success"
    else:
        status = "errors"

    # Extract log excerpt (last 1000 chars for context)
    log_excerpt = ""
    if log_path.exists():
        try:
            full_log = log_path.read_text(encoding="utf-8", errors="replace")
            log_excerpt = full_log[-1000:] if len(full_log) > 1000 else full_log
        except OSError:
            pass

    # Build guidance
    if status == "success":
        guidance = "LaTeX compilation successful — zero errors, zero warnings."
    elif status == "warnings_only":
        guidance = (
            f"LaTeX compiled successfully but produced {len(warnings)} warning(s).\n"
            "Consider addressing warnings for a cleaner submission."
        )
    else:
        guidance = (
            f"LaTeX compilation failed with {len(errors)} error(s) "
            f"and {len(warnings)} warning(s).\n"
            "Fix errors before submission. Most common causes:\n"
            "- Undefined control sequence (typo in command)\n"
            "- Missing $ (math mode delimiter)\n"
            "- Mismatched braces\n"
            "- Missing packages"
        )

    return LatexVerifyResult(
        status=status,
        errors=errors,
        warnings=warnings,
        error_count=len(errors),
        warning_count=len(warnings),
        compilation_time=compilation_time,
        tex_file=str(tex_path),
        log_excerpt=log_excerpt,
        guidance=guidance,
    )


# ============================================================
# High-Level Entry Point (Tool Interface)
# ============================================================

def latex_verify(
    tex_path: Optional[str] = None,
    project_dir: Optional[str] = None,
    draft_mode: bool = True,
) -> Dict:
    """
    Verify LaTeX compilation for a project.
    
    This is the main entry point called by the tool dispatch system.
    
    Args:
        tex_path: Explicit path to main .tex file. If None, auto-discovers.
        project_dir: Directory to search for .tex files. 
                     Defaults to .workspace/paper/ or current dir.
        draft_mode: If True, skip PDF generation (faster).
    
    Returns:
        Dict with verification results (serializable).
    """
    # Resolve the .tex file
    if tex_path:
        resolved_path = Path(tex_path)
    else:
        # Auto-discover
        search_dir = Path(project_dir) if project_dir else _default_project_dir()
        resolved_path = find_main_tex(search_dir)
        if resolved_path is None:
            return LatexVerifyResult(
                status="errors",
                error_count=1,
                errors=[LatexError(
                    level="error",
                    message=f"No .tex file found in {search_dir}",
                )],
                guidance=(
                    f"No .tex file found in {search_dir}.\n"
                    "Please specify tex_path explicitly or ensure your "
                    "LaTeX project is in the expected location."
                ),
            ).to_dict()

    result = compile_latex(resolved_path, draft_mode=draft_mode)
    return result.to_dict()


def _default_project_dir() -> Path:
    """Determine the default project directory for LaTeX files."""
    # Check workspace first
    workspace_paper = WORKSPACE / "paper"
    if workspace_paper.exists():
        # Look for .tex files in workspace/paper
        tex_files = list(workspace_paper.glob("*.tex"))
        if tex_files:
            return workspace_paper

    # Check workspace root
    tex_files = list(WORKSPACE.glob("*.tex"))
    if tex_files:
        return WORKSPACE

    # Fall back to current directory
    return Path(".")


# ============================================================
# Formatting (for tool output)
# ============================================================

def format_latex_result(result: Dict) -> str:
    """Format LaTeX verification result for human-readable display."""
    lines = []
    status = result.get("status", "unknown")

    status_icons = {
        "success": "✅",
        "warnings_only": "⚠️",
        "errors": "❌",
        "unavailable": "📋",
        "timeout": "⏱️",
    }
    icon = status_icons.get(status, "❓")

    lines.append(f"{icon} LaTeX Verification: {status}")

    if result.get("tex_file"):
        lines.append(f"  File: {result['tex_file']}")
    if result.get("compilation_time"):
        lines.append(f"  Time: {result['compilation_time']:.1f}s")

    error_count = result.get("error_count", 0)
    warning_count = result.get("warning_count", 0)
    lines.append(f"  Errors: {error_count} | Warnings: {warning_count}")

    # Show errors (up to 5)
    errors = result.get("errors", [])
    if errors:
        lines.append("\n  Errors:")
        for err in errors[:5]:
            loc = ""
            if err.get("line"):
                loc = f" (line {err['line']})"
            if err.get("file"):
                loc = f" [{err['file']}{loc}]"
            lines.append(f"    ✗ {err['message']}{loc}")
        if len(errors) > 5:
            lines.append(f"    ... and {len(errors) - 5} more error(s)")

    # Show warnings (up to 5)
    warnings = result.get("warnings", [])
    if warnings:
        lines.append("\n  Warnings:")
        for warn in warnings[:5]:
            lines.append(f"    ⚡ {warn['message'][:100]}")
        if len(warnings) > 5:
            lines.append(f"    ... and {len(warnings) - 5} more warning(s)")

    # Guidance
    guidance = result.get("guidance", "")
    if guidance:
        lines.append(f"\n  {guidance}")

    return "\n".join(lines)
