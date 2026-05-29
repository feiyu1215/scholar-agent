"""
tools/bib_verify.py — BibTeX/BibLaTeX bibliography verification.

Mechanical checks for bibliography completeness and citation consistency:
1. Parses .bib file for entry completeness (required fields per entry type)
2. Cross-references citations in .tex against .bib entries
3. Detects orphaned entries (in .bib but never cited)
4. Detects undefined references (cited in .tex but missing from .bib)

Design choices:
- Zero-LLM: all checks are rule-based regex/parsing
- Graceful degradation: if .bib file not found, outputs guidance
- Compatible with both BibTeX and BibLaTeX entry types
- Integrates with presubmission_check as an additional check
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

WORKSPACE = Path(".workspace")


# ============================================================
# Data Classes
# ============================================================

@dataclass
class BibEntry:
    """A parsed BibTeX/BibLaTeX entry."""
    key: str
    entry_type: str          # article, book, inproceedings, etc.
    fields: Dict[str, str]   # field_name -> value
    line_number: int = 0     # Line in .bib file

    def has_field(self, name: str) -> bool:
        """Check if a field exists and is non-empty."""
        val = self.fields.get(name, "").strip()
        return len(val) > 0


@dataclass
class BibIssue:
    """A single bibliography issue."""
    level: str              # "error" | "warning" | "info"
    category: str           # "missing_field" | "undefined_ref" | "orphaned" | "duplicate" | "format"
    message: str
    entry_key: Optional[str] = None
    field_name: Optional[str] = None
    line_number: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "level": self.level,
            "category": self.category,
            "message": self.message,
            "entry_key": self.entry_key,
            "field_name": self.field_name,
            "line_number": self.line_number,
        }


@dataclass
class BibVerifyResult:
    """Result of bibliography verification."""
    status: str             # "clean" | "warnings_only" | "errors" | "unavailable"
    total_entries: int = 0
    issues: List[BibIssue] = field(default_factory=list)
    error_count: int = 0
    warning_count: int = 0
    cited_keys: Set[str] = field(default_factory=set)
    bib_keys: Set[str] = field(default_factory=set)
    undefined_refs: Set[str] = field(default_factory=set)
    orphaned_entries: Set[str] = field(default_factory=set)
    guidance: str = ""

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "total_entries": self.total_entries,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "issues": [i.to_dict() for i in self.issues],
            "cited_keys": sorted(self.cited_keys),
            "bib_keys": sorted(self.bib_keys),
            "undefined_refs": sorted(self.undefined_refs),
            "orphaned_entries": sorted(self.orphaned_entries),
            "guidance": self.guidance,
        }


# ============================================================
# Required Fields per Entry Type (BibTeX standard + BibLaTeX)
# ============================================================

# Required fields: if missing, it's a warning (not error, since some
# fields can be legitimately absent depending on venue requirements)
REQUIRED_FIELDS: Dict[str, List[str]] = {
    "article": ["author", "title", "journal", "year"],
    "book": ["author", "title", "publisher", "year"],
    "inproceedings": ["author", "title", "booktitle", "year"],
    "incollection": ["author", "title", "booktitle", "publisher", "year"],
    "phdthesis": ["author", "title", "school", "year"],
    "mastersthesis": ["author", "title", "school", "year"],
    "techreport": ["author", "title", "institution", "year"],
    "inbook": ["author", "title", "publisher", "year"],
    "proceedings": ["title", "year"],
    "misc": ["title"],  # Minimal — misc is catch-all
    "online": ["title", "url"],
    "unpublished": ["author", "title"],
}

# Fields that enhance quality (optional but recommended)
RECOMMENDED_FIELDS: Dict[str, List[str]] = {
    "article": ["volume", "pages", "doi"],
    "inproceedings": ["pages"],
    "book": ["isbn"],
}


# ============================================================
# BibTeX Parsing
# ============================================================

# Pattern to match @type{key, ... }
_ENTRY_PATTERN = re.compile(
    r"@(\w+)\s*\{\s*([^,\s]+)\s*,",
    re.IGNORECASE
)

# Pattern to match field = {value} or field = "value" or field = number
_FIELD_PATTERN = re.compile(
    r"(\w+)\s*=\s*(?:\{([^}]*(?:\{[^}]*\}[^}]*)*)\}|\"([^\"]*)\"|(\d+))",
    re.IGNORECASE
)


def parse_bib_file(bib_path: Path) -> List[BibEntry]:
    """
    Parse a .bib file into structured entries.
    
    Handles common BibTeX/BibLaTeX formats including:
    - Braced values: field = {value}
    - Quoted values: field = "value"
    - Numeric values: field = 2023
    - Nested braces in values (e.g., title = {A {GPU} approach})
    """
    if not bib_path.exists():
        return []

    try:
        content = bib_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    entries: List[BibEntry] = []
    lines = content.split("\n")

    # Find each entry by scanning for @type{key,
    for i, line in enumerate(lines):
        match = _ENTRY_PATTERN.match(line.strip())
        if match:
            entry_type = match.group(1).lower()
            key = match.group(2).strip()

            # Skip @string, @preamble, @comment
            if entry_type in ("string", "preamble", "comment"):
                continue

            # Collect the full entry text (find matching closing brace)
            entry_text = _extract_entry_text(lines, i)
            fields = _parse_fields(entry_text)

            entries.append(BibEntry(
                key=key,
                entry_type=entry_type,
                fields=fields,
                line_number=i + 1,
            ))

    return entries


def _extract_entry_text(lines: List[str], start_line: int) -> str:
    """Extract the full text of a bib entry, handling nested braces."""
    text_lines = []
    brace_depth = 0
    started = False

    for i in range(start_line, min(start_line + 100, len(lines))):
        line = lines[i]
        for ch in line:
            if ch == "{":
                brace_depth += 1
                started = True
            elif ch == "}":
                brace_depth -= 1
                if started and brace_depth == 0:
                    text_lines.append(line)
                    return "\n".join(text_lines)
        text_lines.append(line)

    return "\n".join(text_lines)


def _parse_fields(entry_text: str) -> Dict[str, str]:
    """Parse fields from entry text."""
    fields: Dict[str, str] = {}
    for match in _FIELD_PATTERN.finditer(entry_text):
        name = match.group(1).lower()
        # Value is in group 2 (braced), 3 (quoted), or 4 (numeric)
        value = match.group(2) or match.group(3) or match.group(4) or ""
        fields[name] = value.strip()
    return fields


# ============================================================
# Citation Extraction from .tex
# ============================================================

# Patterns for \cite commands (BibTeX + natbib + BibLaTeX)
# Note: (?:\[[^\]]*\])* handles zero, one, or two optional arguments (natbib style)
_CITE_PATTERNS = [
    re.compile(r"\\cite[tp]?\*?\s*(?:\[[^\]]*\]\s*)*\{([^}]+)\}"),
    re.compile(r"\\(?:auto|text|paren|foot|full)cite\*?\s*(?:\[[^\]]*\]\s*)*\{([^}]+)\}"),
    re.compile(r"\\cite(?:author|year|date|title)\*?\s*(?:\[[^\]]*\]\s*)*\{([^}]+)\}"),
    re.compile(r"\\(?:no)?cites?\s*(?:\([^)]*\))?\s*\{([^}]+)\}"),
]


def extract_citations_from_tex(tex_path: Path) -> Set[str]:
    """
    Extract all citation keys from a .tex file (and \\input'd files).
    
    Handles:
    - \\cite{key1, key2}
    - \\citep{key}, \\citet{key}
    - \\autocite, \\textcite, \\parencite, etc.
    - Multiple keys in one cite command
    """
    if not tex_path.exists():
        return set()

    cited_keys: Set[str] = set()
    _extract_from_file(tex_path, cited_keys, visited=set())
    return cited_keys


def _extract_from_file(
    tex_path: Path, cited_keys: Set[str], visited: Set[Path]
) -> None:
    """Recursively extract citations, following \\input and \\include."""
    resolved = tex_path.resolve()
    if resolved in visited:
        return
    visited.add(resolved)

    try:
        content = tex_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return

    # Extract citation keys
    for pattern in _CITE_PATTERNS:
        for match in pattern.finditer(content):
            keys_str = match.group(1)
            for key in keys_str.split(","):
                key = key.strip()
                if key:
                    cited_keys.add(key)

    # Follow \input and \include directives
    input_pattern = re.compile(r"\\(?:input|include)\s*\{([^}]+)\}")
    for match in input_pattern.finditer(content):
        included = match.group(1).strip()
        if not included.endswith(".tex"):
            included += ".tex"
        included_path = tex_path.parent / included
        if included_path.exists():
            _extract_from_file(included_path, cited_keys, visited)


# ============================================================
# Verification Logic
# ============================================================

def verify_bib_completeness(entries: List[BibEntry]) -> List[BibIssue]:
    """Check each entry for required/recommended fields."""
    issues: List[BibIssue] = []
    seen_keys: Dict[str, int] = {}

    for entry in entries:
        # Check for duplicate keys
        if entry.key in seen_keys:
            issues.append(BibIssue(
                level="error",
                category="duplicate",
                message=f"Duplicate key '{entry.key}' (first at line {seen_keys[entry.key]})",
                entry_key=entry.key,
                line_number=entry.line_number,
            ))
        seen_keys[entry.key] = entry.line_number

        # Check required fields
        required = REQUIRED_FIELDS.get(entry.entry_type, [])
        for field_name in required:
            if not entry.has_field(field_name):
                # author/editor is flexible: either can satisfy
                if field_name == "author" and entry.has_field("editor"):
                    continue
                issues.append(BibIssue(
                    level="warning",
                    category="missing_field",
                    message=f"Entry '{entry.key}' ({entry.entry_type}): missing required field '{field_name}'",
                    entry_key=entry.key,
                    field_name=field_name,
                    line_number=entry.line_number,
                ))

        # Check recommended fields (info-level only)
        recommended = RECOMMENDED_FIELDS.get(entry.entry_type, [])
        for field_name in recommended:
            if not entry.has_field(field_name):
                issues.append(BibIssue(
                    level="info",
                    category="missing_field",
                    message=f"Entry '{entry.key}': recommended field '{field_name}' not present",
                    entry_key=entry.key,
                    field_name=field_name,
                    line_number=entry.line_number,
                ))

        # Check for empty title (common error)
        if entry.has_field("title"):
            title = entry.fields.get("title", "")
            if len(title) < 3:
                issues.append(BibIssue(
                    level="warning",
                    category="format",
                    message=f"Entry '{entry.key}': title appears empty or too short",
                    entry_key=entry.key,
                    field_name="title",
                    line_number=entry.line_number,
                ))

    return issues


def verify_citation_consistency(
    cited_keys: Set[str],
    bib_keys: Set[str],
) -> Tuple[Set[str], Set[str], List[BibIssue]]:
    """
    Cross-reference citations vs bibliography.
    
    Returns:
        (undefined_refs, orphaned_entries, issues)
    """
    issues: List[BibIssue] = []

    # Undefined: cited but not in .bib
    undefined = cited_keys - bib_keys
    for key in sorted(undefined):
        issues.append(BibIssue(
            level="error",
            category="undefined_ref",
            message=f"Citation '{key}' used in text but not defined in .bib file",
            entry_key=key,
        ))

    # Orphaned: in .bib but never cited
    orphaned = bib_keys - cited_keys
    for key in sorted(orphaned):
        issues.append(BibIssue(
            level="info",
            category="orphaned",
            message=f"Entry '{key}' in .bib but never cited in text",
            entry_key=key,
        ))

    return undefined, orphaned, issues


# ============================================================
# High-Level Entry Point (Tool Interface)
# ============================================================

def bib_verify(
    bib_path: Optional[str] = None,
    tex_path: Optional[str] = None,
    project_dir: Optional[str] = None,
    check_orphaned: bool = True,
) -> Dict:
    """
    Verify bibliography completeness and citation consistency.
    
    This is the main entry point called by the tool dispatch system.
    
    Args:
        bib_path: Explicit path to .bib file. If None, auto-discovers.
        tex_path: Explicit path to main .tex file. If None, auto-discovers.
        project_dir: Directory to search. Defaults to workspace.
        check_orphaned: Whether to report orphaned entries (can be noisy).
    
    Returns:
        Dict with verification results (serializable).
    """
    search_dir = Path(project_dir) if project_dir else _default_search_dir()

    # Resolve .bib file
    if bib_path:
        resolved_bib = Path(bib_path)
    else:
        resolved_bib = _find_bib_file(search_dir)

    if resolved_bib is None or not resolved_bib.exists():
        return BibVerifyResult(
            status="unavailable",
            guidance=(
                f"No .bib file found in {search_dir}.\n"
                "Please specify bib_path explicitly or ensure your "
                "bibliography file is in the project directory."
            ),
        ).to_dict()

    # Parse .bib
    entries = parse_bib_file(resolved_bib)
    if not entries:
        return BibVerifyResult(
            status="unavailable",
            guidance=f"Could not parse any entries from {resolved_bib}. File may be empty or malformed.",
        ).to_dict()

    bib_keys = {e.key for e in entries}

    # Check entry completeness
    completeness_issues = verify_bib_completeness(entries)

    # Citation consistency (only if we can find a .tex file)
    cited_keys: Set[str] = set()
    undefined_refs: Set[str] = set()
    orphaned_entries: Set[str] = set()
    consistency_issues: List[BibIssue] = []

    if tex_path:
        resolved_tex = Path(tex_path)
    else:
        from tools.latex_verify import find_main_tex
        resolved_tex = find_main_tex(search_dir)

    if resolved_tex and resolved_tex.exists():
        cited_keys = extract_citations_from_tex(resolved_tex)
        if cited_keys:
            undefined_refs, orphaned_entries, consistency_issues = (
                verify_citation_consistency(cited_keys, bib_keys)
            )
            if not check_orphaned:
                # Remove orphaned issues
                consistency_issues = [
                    i for i in consistency_issues if i.category != "orphaned"
                ]

    # Combine all issues
    all_issues = completeness_issues + consistency_issues
    errors = [i for i in all_issues if i.level == "error"]
    warnings = [i for i in all_issues if i.level == "warning"]

    # Determine status
    if errors:
        status = "errors"
    elif warnings:
        status = "warnings_only"
    else:
        status = "clean"

    # Build guidance
    if status == "clean":
        guidance = f"Bibliography clean: {len(entries)} entries, all fields present, all citations resolved."
    elif status == "warnings_only":
        guidance = (
            f"Bibliography has {len(warnings)} warning(s) but no critical errors.\n"
            "Consider adding missing fields for completeness."
        )
    else:
        guidance = (
            f"Bibliography has {len(errors)} error(s) and {len(warnings)} warning(s).\n"
            "Undefined references will cause compilation failures."
        )

    return BibVerifyResult(
        status=status,
        total_entries=len(entries),
        issues=all_issues,
        error_count=len(errors),
        warning_count=len(warnings),
        cited_keys=cited_keys,
        bib_keys=bib_keys,
        undefined_refs=undefined_refs,
        orphaned_entries=orphaned_entries,
        guidance=guidance,
    ).to_dict()


def _default_search_dir() -> Path:
    """Determine default search directory."""
    workspace_paper = WORKSPACE / "paper"
    if workspace_paper.exists():
        return workspace_paper
    if WORKSPACE.exists():
        return WORKSPACE
    return Path(".")


def _find_bib_file(search_dir: Path) -> Optional[Path]:
    """Find a .bib file in the search directory."""
    bib_files = list(search_dir.glob("*.bib"))
    if not bib_files:
        bib_files = list(search_dir.glob("**/*.bib"))
    if not bib_files:
        return None

    # Prefer common names
    common_names = ["references.bib", "bibliography.bib", "refs.bib", "main.bib", "paper.bib"]
    for name in common_names:
        candidate = search_dir / name
        if candidate.exists():
            return candidate

    # Return the largest .bib file (likely the main one)
    return max(bib_files, key=lambda p: p.stat().st_size)


# ============================================================
# Formatting (for tool output)
# ============================================================

def format_bib_result(result: Dict) -> str:
    """Format bibliography verification result for display."""
    lines = []
    status = result.get("status", "unknown")

    status_icons = {
        "clean": "✅",
        "warnings_only": "⚠️",
        "errors": "❌",
        "unavailable": "📋",
    }
    icon = status_icons.get(status, "❓")

    lines.append(f"{icon} Bibliography Verification: {status}")
    lines.append(f"  Entries: {result.get('total_entries', 0)}")
    lines.append(f"  Errors: {result.get('error_count', 0)} | Warnings: {result.get('warning_count', 0)}")

    # Undefined references (critical)
    undefined = result.get("undefined_refs", [])
    if undefined:
        lines.append(f"\n  ❌ Undefined references ({len(undefined)}):")
        for key in undefined[:10]:
            lines.append(f"    - {key}")
        if len(undefined) > 10:
            lines.append(f"    ... and {len(undefined) - 10} more")

    # Missing required fields (warnings)
    issues = result.get("issues", [])
    missing_fields = [i for i in issues if i.get("category") == "missing_field" and i.get("level") == "warning"]
    if missing_fields:
        lines.append(f"\n  ⚠️ Missing required fields ({len(missing_fields)}):")
        for issue in missing_fields[:8]:
            lines.append(f"    - {issue['message']}")
        if len(missing_fields) > 8:
            lines.append(f"    ... and {len(missing_fields) - 8} more")

    # Orphaned entries (info)
    orphaned = result.get("orphaned_entries", [])
    if orphaned:
        lines.append(f"\n  ℹ️ Uncited entries ({len(orphaned)}):")
        for key in orphaned[:5]:
            lines.append(f"    - {key}")
        if len(orphaned) > 5:
            lines.append(f"    ... and {len(orphaned) - 5} more")

    # Guidance
    guidance = result.get("guidance", "")
    if guidance:
        lines.append(f"\n  {guidance}")

    return "\n".join(lines)
