"""
Table Parser — Multi-source table extraction from text.

Extracts structured tables from:
  - Plain text (ASCII-formatted tables, pipe-delimited, space-aligned)
  - LaTeX tabular environments
  - Markdown tables
  - PDF text output (pymupdf/pdfplumber text representations)

This is the lowest layer of Phase 9A. It converts raw text into
structured RawTable objects that can then be fed to the economics
semantic parser for domain-specific understanding.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class TableFormat(Enum):
    """Detected table format."""
    LATEX = "latex"
    MARKDOWN = "markdown"
    PIPE_DELIMITED = "pipe_delimited"
    SPACE_ALIGNED = "space_aligned"
    TAB_DELIMITED = "tab_delimited"
    UNKNOWN = "unknown"


@dataclass
class CellValue:
    """A single cell in the table with metadata."""
    raw: str
    numeric: Optional[float] = None
    is_empty: bool = False
    has_stars: int = 0  # 0-3 significance stars
    is_parenthesized: bool = False  # indicates SE or t-stat
    is_bracketed: bool = False  # indicates CI or t-stat

    def __post_init__(self) -> None:
        self.raw = self.raw.strip()
        self.is_empty = self.raw in ("", "-", "—", "–", ".", "...", "n/a", "N/A")
        if not self.is_empty:
            self._parse_numeric()

    def _parse_numeric(self) -> None:
        """Extract numeric value from potentially annotated cell."""
        text = self.raw
        # Count significance stars
        star_match = re.search(r"(\*{1,3})\s*$", text)
        if star_match:
            self.has_stars = len(star_match.group(1))
            text = text[: star_match.start()].strip()

        # Check for parentheses (standard errors)
        paren_match = re.match(r"^\((.+)\)$", text)
        if paren_match:
            self.is_parenthesized = True
            text = paren_match.group(1).strip()

        # Check for brackets (confidence intervals or t-stats)
        bracket_match = re.match(r"^\[(.+)\]$", text)
        if bracket_match:
            self.is_bracketed = True
            text = bracket_match.group(1).strip()

        # Try to parse as number
        # Handle thousands separator: 1,234.56 or 1 234.56
        cleaned = text.replace(",", "").replace(" ", "")
        # Handle negative with various dashes
        cleaned = re.sub(r"^[−–—]", "-", cleaned)
        try:
            self.numeric = float(cleaned)
        except (ValueError, TypeError):
            pass


@dataclass
class RawTable:
    """A raw extracted table before economic interpretation."""
    table_id: str
    caption: str = ""
    headers: list[list[str]] = field(default_factory=list)  # multi-row headers
    body: list[list[CellValue]] = field(default_factory=list)
    notes: str = ""  # table footnotes
    source_format: TableFormat = TableFormat.UNKNOWN
    source_location: str = ""  # e.g., "Table 3" or line range

    @property
    def n_cols(self) -> int:
        if self.body:
            return max(len(row) for row in self.body)
        if self.headers:
            return max(len(row) for row in self.headers)
        return 0

    @property
    def n_rows(self) -> int:
        return len(self.body)

    @property
    def flat_headers(self) -> list[str]:
        """Flatten multi-row headers into single row, joining with ' | '."""
        if not self.headers:
            return []
        if len(self.headers) == 1:
            return self.headers[0]
        # Transpose and join
        n_cols = max(len(row) for row in self.headers)
        result = []
        for col_idx in range(n_cols):
            parts = []
            for row in self.headers:
                if col_idx < len(row) and row[col_idx].strip():
                    parts.append(row[col_idx].strip())
            result.append(" | ".join(parts) if parts else "")
        return result


class TextTableParser:
    """
    Extracts tables from plain text using multiple detection strategies.

    Strategy priority:
    1. LaTeX tabular environments (\\begin{tabular}...\\end{tabular})
    2. Markdown/pipe-delimited tables (| col | col |)
    3. Space-aligned tables (detected by column alignment patterns)
    4. Tab-delimited tables
    """

    # LaTeX table patterns
    _LATEX_TABLE_RE = re.compile(
        r"\\begin\{(?:tabular|table|tabularx|longtable)\}.*?\n"
        r"(.*?)"
        r"\\end\{(?:tabular|table|tabularx|longtable)\}",
        re.DOTALL,
    )
    _LATEX_CAPTION_RE = re.compile(r"\\caption\{([^}]*)\}")

    # Table caption patterns (generic)
    _TABLE_CAPTION_RE = re.compile(
        r"^(?:Table|TABLE|表)\s*(\d+[\w.]*)[\s:.：—–-]*(.*)$", re.MULTILINE
    )

    # Pipe-delimited row
    _PIPE_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$")
    # Separator row (markdown-style)
    _SEPARATOR_RE = re.compile(r"^\s*\|?[\s:]*[-=]+[\s|:]*[-=|:\s]*\|?\s*$")

    def extract_all(self, text: str) -> list[RawTable]:
        """Extract all tables from text, using all strategies."""
        tables: list[RawTable] = []

        # Strategy 1: LaTeX
        tables.extend(self._extract_latex_tables(text))

        # Strategy 2: Pipe-delimited / Markdown
        tables.extend(self._extract_pipe_tables(text))

        # Strategy 3: Space-aligned (only if no pipe tables found in same region)
        if not tables:
            tables.extend(self._extract_space_aligned_tables(text))

        # Deduplicate by overlap (same content different format)
        tables = self._deduplicate(tables)

        # Assign IDs if missing
        for i, t in enumerate(tables):
            if not t.table_id:
                t.table_id = f"table_{i + 1}"

        return tables

    # ------------------------------------------------------------------
    # Strategy 1: LaTeX tables
    # ------------------------------------------------------------------

    def _extract_latex_tables(self, text: str) -> list[RawTable]:
        tables = []
        for match in self._LATEX_TABLE_RE.finditer(text):
            content = match.group(1)
            table = self._parse_latex_content(content)
            table.source_format = TableFormat.LATEX

            # Try to find caption nearby
            # Search before and after the match for \caption
            context_start = max(0, match.start() - 200)
            context_end = min(len(text), match.end() + 200)
            context = text[context_start:context_end]
            cap_match = self._LATEX_CAPTION_RE.search(context)
            if cap_match:
                table.caption = cap_match.group(1).strip()

            # Try to find table number
            tbl_match = self._TABLE_CAPTION_RE.search(context)
            if tbl_match:
                table.table_id = f"table_{tbl_match.group(1)}"
                if not table.caption:
                    table.caption = tbl_match.group(2).strip()

            tables.append(table)
        return tables

    def _parse_latex_content(self, content: str) -> RawTable:
        """Parse LaTeX tabular content into RawTable."""
        # Remove LaTeX commands that don't affect data
        content = re.sub(r"\\(?:hline|toprule|midrule|bottomrule|cline\{[^}]*\})", "", content)
        content = re.sub(r"\\multicolumn\{(\d+)\}\{[^}]*\}\{([^}]*)\}", r"\2", content)
        content = re.sub(r"\\textbf\{([^}]*)\}", r"\1", content)
        content = re.sub(r"\\textit\{([^}]*)\}", r"\1", content)
        content = re.sub(r"\\emph\{([^}]*)\}", r"\1", content)
        content = re.sub(r"\\\\\s*(?:\[\d+[a-z]*\])?", "\n", content)  # \\ → newline
        content = re.sub(r"\\\\", "\n", content)

        rows: list[list[str]] = []
        for line in content.split("\n"):
            line = line.strip()
            if not line or line.startswith("%"):
                continue
            cells = [c.strip() for c in line.split("&")]
            if any(c for c in cells):
                rows.append(cells)

        if not rows:
            return RawTable(table_id="")

        # Heuristic: first 1-2 rows are headers if they contain non-numeric text
        header_end = self._detect_header_boundary(rows)
        headers = rows[:header_end]
        body_raw = rows[header_end:]

        body = [[CellValue(raw=c) for c in row] for row in body_raw]

        return RawTable(table_id="", headers=headers, body=body)

    # ------------------------------------------------------------------
    # Strategy 2: Pipe-delimited / Markdown tables
    # ------------------------------------------------------------------

    def _extract_pipe_tables(self, text: str) -> list[RawTable]:
        tables = []
        lines = text.split("\n")
        i = 0
        while i < len(lines):
            if self._PIPE_ROW_RE.match(lines[i]):
                table_lines = []
                start_i = i
                while i < len(lines) and (
                    self._PIPE_ROW_RE.match(lines[i])
                    or self._SEPARATOR_RE.match(lines[i])
                ):
                    table_lines.append(lines[i])
                    i += 1

                if len(table_lines) >= 2:  # At least header + 1 row
                    table = self._parse_pipe_table(table_lines)
                    table.source_format = TableFormat.PIPE_DELIMITED

                    # Look for caption above
                    caption = self._find_caption_above(lines, start_i)
                    if caption:
                        table.caption = caption[1]
                        table.table_id = f"table_{caption[0]}"

                    tables.append(table)
            else:
                i += 1
        return tables

    def _parse_pipe_table(self, lines: list[str]) -> RawTable:
        """Parse pipe-delimited table lines."""
        data_rows: list[list[str]] = []
        separator_indices: list[int] = []

        for idx, line in enumerate(lines):
            if self._SEPARATOR_RE.match(line):
                separator_indices.append(idx)
            else:
                match = self._PIPE_ROW_RE.match(line)
                if match:
                    cells = [c.strip() for c in match.group(1).split("|")]
                    data_rows.append(cells)

        # Headers = rows before first separator
        if separator_indices:
            first_sep_data_idx = 0
            for idx, line in enumerate(lines):
                if self._SEPARATOR_RE.match(line):
                    break
                if self._PIPE_ROW_RE.match(line):
                    first_sep_data_idx += 1

            headers = data_rows[:first_sep_data_idx]
            body_raw = data_rows[first_sep_data_idx:]
        else:
            # No separator — first row is header
            headers = data_rows[:1]
            body_raw = data_rows[1:]

        body = [[CellValue(raw=c) for c in row] for row in body_raw]
        return RawTable(table_id="", headers=headers, body=body)

    # ------------------------------------------------------------------
    # Strategy 3: Space-aligned tables
    # ------------------------------------------------------------------

    def _extract_space_aligned_tables(self, text: str) -> list[RawTable]:
        """
        Detect tables by finding blocks of consistently-spaced lines.
        This handles the output of PDF text extraction tools.
        """
        tables = []
        lines = text.split("\n")
        i = 0

        while i < len(lines):
            # Look for blocks of lines with consistent columnar structure
            block_start = i
            block_lines = []

            while i < len(lines):
                line = lines[i]
                # A "columnar" line has multiple whitespace-separated tokens
                # with consistent alignment
                tokens = re.split(r"\s{2,}", line.strip())
                if len(tokens) >= 3 and line.strip():
                    block_lines.append(line)
                    i += 1
                elif block_lines and not line.strip():
                    # Allow single blank lines within a table
                    if i + 1 < len(lines):
                        next_tokens = re.split(r"\s{2,}", lines[i + 1].strip())
                        if len(next_tokens) >= 3:
                            block_lines.append("")
                            i += 1
                            continue
                    break
                else:
                    break

            if len(block_lines) >= 3:
                # Validate alignment consistency
                table = self._parse_space_aligned_block(block_lines)
                if table and table.n_rows >= 2:
                    table.source_format = TableFormat.SPACE_ALIGNED
                    # Look for caption above
                    caption = self._find_caption_above(lines, block_start)
                    if caption:
                        table.caption = caption[1]
                        table.table_id = f"table_{caption[0]}"
                    tables.append(table)

            if not block_lines:
                i += 1

        return tables

    def _parse_space_aligned_block(self, lines: list[str]) -> Optional[RawTable]:
        """Parse a block of space-aligned lines into a table."""
        # Remove blank lines
        lines = [l for l in lines if l.strip()]
        if len(lines) < 3:
            return None

        # Detect column boundaries using character position analysis
        col_boundaries = self._detect_column_boundaries(lines)
        if not col_boundaries or len(col_boundaries) < 2:
            return None

        # Split each line by detected boundaries
        all_rows: list[list[str]] = []
        for line in lines:
            row = self._split_by_boundaries(line, col_boundaries)
            all_rows.append(row)

        # Detect header
        header_end = self._detect_header_boundary(all_rows)
        headers = all_rows[:header_end]
        body_raw = all_rows[header_end:]

        body = [[CellValue(raw=c) for c in row] for row in body_raw]
        return RawTable(table_id="", headers=headers, body=body)

    def _detect_column_boundaries(self, lines: list[str]) -> list[int]:
        """Find column boundaries by looking for consistent space positions."""
        if not lines:
            return []

        max_len = max(len(l) for l in lines)
        # Count how many lines have a space at each position
        space_counts = [0] * max_len
        for line in lines:
            padded = line.ljust(max_len)
            for pos, ch in enumerate(padded):
                if ch == " ":
                    space_counts[pos] += 1

        # A column boundary is where most lines (>70%) have spaces
        threshold = len(lines) * 0.7
        boundaries = []
        in_gap = False
        gap_start = 0
        for pos, count in enumerate(space_counts):
            if count >= threshold:
                if not in_gap:
                    in_gap = True
                    gap_start = pos
            else:
                if in_gap:
                    # Use middle of gap as boundary
                    boundaries.append((gap_start + pos) // 2)
                    in_gap = False

        return boundaries

    def _split_by_boundaries(self, line: str, boundaries: list[int]) -> list[str]:
        """Split a line at the given character positions."""
        parts = []
        prev = 0
        for b in boundaries:
            parts.append(line[prev:b].strip())
            prev = b
        parts.append(line[prev:].strip())
        return parts

    # ------------------------------------------------------------------
    # Shared utilities
    # ------------------------------------------------------------------

    def _detect_header_boundary(self, rows: list[list[str]]) -> int:
        """
        Detect where headers end and body begins.
        Heuristic: header rows are mostly non-numeric text.
        """
        if len(rows) <= 1:
            return 1 if rows else 0

        for i, row in enumerate(rows):
            if i == 0:
                continue
            # Count numeric-looking cells
            numeric_count = 0
            total_non_empty = 0
            for cell in row:
                text = cell if isinstance(cell, str) else cell.raw
                text = text.strip()
                if not text:
                    continue
                total_non_empty += 1
                # Check if it looks numeric (possibly with stars/parens)
                cleaned = re.sub(r"[*()\[\]−–—,\s]", "", text)
                try:
                    float(cleaned)
                    numeric_count += 1
                except ValueError:
                    pass

            # If >50% of non-empty cells are numeric, this is the body start
            if total_non_empty > 0 and numeric_count / total_non_empty > 0.4:
                return i

        # Default: first row is header
        return 1

    def _find_caption_above(
        self, all_lines: list[str], table_start: int
    ) -> Optional[tuple[str, str]]:
        """Look for a table caption in the 5 lines above the table start."""
        search_start = max(0, table_start - 5)
        for i in range(table_start - 1, search_start - 1, -1):
            match = self._TABLE_CAPTION_RE.match(all_lines[i].strip())
            if match:
                return (match.group(1), match.group(2).strip())
        return None

    def _deduplicate(self, tables: list[RawTable]) -> list[RawTable]:
        """Remove duplicate tables detected by different strategies."""
        if len(tables) <= 1:
            return tables

        # Use caption + first row content as fingerprint
        seen: set[str] = set()
        unique = []
        for t in tables:
            fp_parts = [t.caption]
            if t.body:
                fp_parts.append("|".join(c.raw for c in t.body[0]))
            fp = "::".join(fp_parts)
            if fp not in seen:
                seen.add(fp)
                unique.append(t)
        return unique
