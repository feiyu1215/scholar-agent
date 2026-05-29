"""
core/bib_verify.py — Citation consistency verification tool.

Zero-LLM programmatic checks for bibliography and citation health:
1. Parses .bib content for entry completeness (required fields per type)
2. Cross-references citation keys in LaTeX/Markdown against .bib entries
3. Detects orphaned entries (in .bib but never cited)
4. Detects undefined references (cited but missing from .bib)
5. Checks for duplicate keys, short titles, format issues

Designed as an Agent-callable tool (COGNITIVE_ANCHOR §4.3):
- Agent decides WHEN to invoke it (not auto-triggered)
- Accepts text content directly (no file I/O required)
- Also supports directory-based auto-discovery for convenience

Migrated from: legacy/tools/bib_verify.py
Changes from legacy:
- Removed dependency on tools.latex_verify.find_main_tex (inlined)
- Added content-based API (bib_content/tex_content strings)
- Standalone module with zero external dependencies
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


# ============================================================
# Data Classes
# ============================================================

@dataclass
class BibEntry:
    """A parsed BibTeX/BibLaTeX entry."""
    key: str
    entry_type: str          # article, book, inproceedings, etc.
    fields: Dict[str, str]   # field_name -> value
    line_number: int = 0

    def has_field(self, name: str) -> bool:
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


@dataclass
class BibVerifyResult:
    """Complete result of bibliography verification."""
    status: str             # "clean" | "warnings_only" | "errors" | "unavailable"
    total_entries: int = 0
    issues: List[BibIssue] = field(default_factory=list)
    error_count: int = 0
    warning_count: int = 0
    cited_keys: Set[str] = field(default_factory=set)
    bib_keys: Set[str] = field(default_factory=set)
    undefined_refs: Set[str] = field(default_factory=set)
    orphaned_entries: Set[str] = field(default_factory=set)

    def summary(self) -> str:
        """Format a human-readable summary for Agent consumption."""
        lines = []

        status_labels = {
            "clean": "✅ 引用验证通过",
            "warnings_only": "⚠️ 有警告但无严重错误",
            "errors": "❌ 发现引用错误",
            "unavailable": "📋 无法执行验证",
        }
        lines.append(status_labels.get(self.status, f"状态: {self.status}"))
        lines.append(f"条目总数: {self.total_entries} | 错误: {self.error_count} | 警告: {self.warning_count}")

        # Undefined references (critical)
        if self.undefined_refs:
            lines.append(f"\n未定义的引用 ({len(self.undefined_refs)}):")
            for key in sorted(self.undefined_refs)[:15]:
                lines.append(f"  - \\cite{{{key}}} 在文中使用但 .bib 中不存在")
            if len(self.undefined_refs) > 15:
                lines.append(f"  ... 还有 {len(self.undefined_refs) - 15} 个")

        # Missing required fields
        missing = [i for i in self.issues if i.category == "missing_field" and i.level == "warning"]
        if missing:
            lines.append(f"\n缺失必要字段 ({len(missing)}):")
            for issue in missing[:10]:
                lines.append(f"  - {issue.message}")
            if len(missing) > 10:
                lines.append(f"  ... 还有 {len(missing) - 10} 个")

        # Duplicates
        duplicates = [i for i in self.issues if i.category == "duplicate"]
        if duplicates:
            lines.append(f"\n重复的 key ({len(duplicates)}):")
            for issue in duplicates:
                lines.append(f"  - {issue.message}")

        # Orphaned entries (info)
        if self.orphaned_entries:
            lines.append(f"\n未被引用的条目 ({len(self.orphaned_entries)}):")
            for key in sorted(self.orphaned_entries)[:8]:
                lines.append(f"  - {key}")
            if len(self.orphaned_entries) > 8:
                lines.append(f"  ... 还有 {len(self.orphaned_entries) - 8} 个")

        return "\n".join(lines)


# ============================================================
# Required Fields per Entry Type
# ============================================================

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
    "misc": ["title"],
    "online": ["title", "url"],
    "unpublished": ["author", "title"],
}

RECOMMENDED_FIELDS: Dict[str, List[str]] = {
    "article": ["volume", "pages", "doi"],
    "inproceedings": ["pages"],
    "book": ["isbn"],
}


# ============================================================
# BibTeX Parsing
# ============================================================

_ENTRY_PATTERN = re.compile(
    r"@(\w+)\s*\{\s*([^,\s]+)\s*,",
    re.IGNORECASE
)

_FIELD_PATTERN = re.compile(
    r"(\w+)\s*=\s*(?:\{([^}]*(?:\{[^}]*\}[^}]*)*)\}|\"([^\"]*)\"|(\d+))",
    re.IGNORECASE
)


def parse_bib_content(bib_text: str) -> List[BibEntry]:
    """
    Parse BibTeX/BibLaTeX text into structured entries.

    Handles braced values, quoted values, numeric values, and nested braces.
    Skips @string, @preamble, @comment.
    """
    if not bib_text or not bib_text.strip():
        return []

    entries: List[BibEntry] = []
    lines = bib_text.split("\n")

    for i, line in enumerate(lines):
        match = _ENTRY_PATTERN.match(line.strip())
        if match:
            entry_type = match.group(1).lower()
            key = match.group(2).strip()

            if entry_type in ("string", "preamble", "comment"):
                continue

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
    """Extract full text of a bib entry, handling nested braces."""
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
        value = match.group(2) or match.group(3) or match.group(4) or ""
        fields[name] = value.strip()
    return fields


# ============================================================
# Citation Extraction
# ============================================================

# Patterns for \cite commands (BibTeX + natbib + BibLaTeX)
_CITE_PATTERNS = [
    re.compile(r"\\cite[tp]?\*?\s*(?:\[[^\]]*\]\s*)*\{([^}]+)\}"),
    re.compile(r"\\(?:auto|text|paren|foot|full)cite\*?\s*(?:\[[^\]]*\]\s*)*\{([^}]+)\}"),
    re.compile(r"\\cite(?:author|year|date|title)\*?\s*(?:\[[^\]]*\]\s*)*\{([^}]+)\}"),
    re.compile(r"\\(?:no)?cites?\s*(?:\([^)]*\))?\s*\{([^}]+)\}"),
]


def extract_citations_from_text(tex_text: str) -> Set[str]:
    """
    Extract all citation keys from LaTeX text.

    Handles \\cite{key1, key2}, \\citep{}, \\citet{},
    \\autocite, \\textcite, \\parencite, etc.
    """
    if not tex_text:
        return set()

    cited_keys: Set[str] = set()
    for pattern in _CITE_PATTERNS:
        for match in pattern.finditer(tex_text):
            keys_str = match.group(1)
            for key in keys_str.split(","):
                key = key.strip()
                if key:
                    cited_keys.add(key)
    return cited_keys


def extract_citations_from_file(tex_path: Path) -> Set[str]:
    """Extract citations from a .tex file, following \\input/\\include."""
    if not tex_path.exists():
        return set()

    cited_keys: Set[str] = set()
    _extract_from_file_recursive(tex_path, cited_keys, visited=set())
    return cited_keys


def _extract_from_file_recursive(
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

    # Extract citation keys from this file
    cited_keys.update(extract_citations_from_text(content))

    # Follow \input and \include directives
    input_pattern = re.compile(r"\\(?:input|include)\s*\{([^}]+)\}")
    for match in input_pattern.finditer(content):
        included = match.group(1).strip()
        if not included.endswith(".tex"):
            included += ".tex"
        included_path = tex_path.parent / included
        if included_path.exists():
            _extract_from_file_recursive(included_path, cited_keys, visited)


# ============================================================
# Verification Logic
# ============================================================

def verify_bib_completeness(entries: List[BibEntry]) -> List[BibIssue]:
    """Check each entry for required/recommended fields and duplicates."""
    issues: List[BibIssue] = []
    seen_keys: Dict[str, int] = {}

    for entry in entries:
        # Duplicate keys
        if entry.key in seen_keys:
            issues.append(BibIssue(
                level="error",
                category="duplicate",
                message=f"Duplicate key '{entry.key}' (first at line {seen_keys[entry.key]})",
                entry_key=entry.key,
                line_number=entry.line_number,
            ))
        seen_keys[entry.key] = entry.line_number

        # Required fields
        required = REQUIRED_FIELDS.get(entry.entry_type, [])
        for field_name in required:
            if not entry.has_field(field_name):
                # author/editor flexibility
                if field_name == "author" and entry.has_field("editor"):
                    continue
                issues.append(BibIssue(
                    level="warning",
                    category="missing_field",
                    message=f"'{entry.key}' ({entry.entry_type}): missing '{field_name}'",
                    entry_key=entry.key,
                    field_name=field_name,
                    line_number=entry.line_number,
                ))

        # Recommended fields (info only)
        recommended = RECOMMENDED_FIELDS.get(entry.entry_type, [])
        for field_name in recommended:
            if not entry.has_field(field_name):
                issues.append(BibIssue(
                    level="info",
                    category="missing_field",
                    message=f"'{entry.key}': recommended '{field_name}' absent",
                    entry_key=entry.key,
                    field_name=field_name,
                    line_number=entry.line_number,
                ))

        # Short/empty title check
        if entry.has_field("title"):
            title = entry.fields.get("title", "")
            if len(title) < 3:
                issues.append(BibIssue(
                    level="warning",
                    category="format",
                    message=f"'{entry.key}': title too short ({len(title)} chars)",
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

    Returns: (undefined_refs, orphaned_entries, issues)
    """
    issues: List[BibIssue] = []

    # Undefined: cited but not in .bib
    undefined = cited_keys - bib_keys
    for key in sorted(undefined):
        issues.append(BibIssue(
            level="error",
            category="undefined_ref",
            message=f"\\cite{{{key}}} used but not in .bib",
            entry_key=key,
        ))

    # Orphaned: in .bib but never cited
    orphaned = bib_keys - cited_keys
    for key in sorted(orphaned):
        issues.append(BibIssue(
            level="info",
            category="orphaned",
            message=f"'{key}' in .bib but never cited",
            entry_key=key,
        ))

    return undefined, orphaned, issues


# ============================================================
# File Discovery Helpers (for directory-based mode)
# ============================================================

def find_bib_file(search_dir: Path) -> Optional[Path]:
    """Find a .bib file in a directory (prefers common names)."""
    bib_files = list(search_dir.glob("*.bib"))
    if not bib_files:
        bib_files = list(search_dir.glob("**/*.bib"))
    if not bib_files:
        return None

    common_names = ["references.bib", "bibliography.bib", "refs.bib", "main.bib", "paper.bib"]
    for name in common_names:
        candidate = search_dir / name
        if candidate.exists():
            return candidate

    return max(bib_files, key=lambda p: p.stat().st_size)


def find_main_tex(search_dir: Path) -> Optional[Path]:
    """Find the main .tex file (contains \\documentclass)."""
    tex_files = list(search_dir.glob("*.tex"))
    if not tex_files:
        tex_files = list(search_dir.glob("**/*.tex"))
    if not tex_files:
        return None

    # Look for \documentclass — that's the main file
    for tf in tex_files:
        try:
            content = tf.read_text(encoding="utf-8", errors="replace")[:2000]
            if r"\documentclass" in content:
                return tf
        except OSError:
            continue

    # Fallback: prefer main.tex or paper.tex
    for name in ["main.tex", "paper.tex"]:
        candidate = search_dir / name
        if candidate.exists():
            return candidate

    return tex_files[0] if tex_files else None


# ============================================================
# High-Level Public API
# ============================================================

def verify_citations(
    bib_content: Optional[str] = None,
    tex_content: Optional[str] = None,
    project_dir: Optional[str] = None,
    check_orphaned: bool = True,
) -> BibVerifyResult:
    """
    Verify bibliography completeness and citation consistency.

    Two usage modes:
    1. Content-based: pass bib_content and/or tex_content directly
    2. Directory-based: pass project_dir for auto-discovery

    Args:
        bib_content: .bib file text content (Mode 1)
        tex_content: .tex file text content (Mode 1)
        project_dir: Directory to search for .bib/.tex files (Mode 2)
        check_orphaned: Whether to include orphaned entry warnings

    Returns:
        BibVerifyResult with status, issues, and summary
    """
    # Resolve bib content
    resolved_bib_text = bib_content
    resolved_tex_text = tex_content

    if not resolved_bib_text and project_dir:
        search_dir = Path(project_dir)
        bib_path = find_bib_file(search_dir)
        if bib_path and bib_path.exists():
            try:
                resolved_bib_text = bib_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass

    if not resolved_tex_text and project_dir:
        search_dir = Path(project_dir)
        tex_path = find_main_tex(search_dir)
        if tex_path and tex_path.exists():
            try:
                resolved_tex_text = tex_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass

    # Check if we have bib content to work with
    if not resolved_bib_text:
        return BibVerifyResult(
            status="unavailable",
        )

    # Parse .bib entries
    entries = parse_bib_content(resolved_bib_text)
    if not entries:
        return BibVerifyResult(
            status="unavailable",
        )

    bib_keys = {e.key for e in entries}

    # Check entry completeness
    completeness_issues = verify_bib_completeness(entries)

    # Citation consistency (only if tex content available)
    cited_keys: Set[str] = set()
    undefined_refs: Set[str] = set()
    orphaned_entries: Set[str] = set()
    consistency_issues: List[BibIssue] = []

    if resolved_tex_text:
        cited_keys = extract_citations_from_text(resolved_tex_text)
        if cited_keys:
            undefined_refs, orphaned_entries, consistency_issues = (
                verify_citation_consistency(cited_keys, bib_keys)
            )
            if not check_orphaned:
                consistency_issues = [
                    i for i in consistency_issues if i.category != "orphaned"
                ]
                orphaned_entries = set()  # Clear so summary doesn't show them

    # Combine issues
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
    )
