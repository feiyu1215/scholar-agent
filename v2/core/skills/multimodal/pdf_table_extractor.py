"""
PDF Table Extraction Module.

Extracts tables from PDF files using a layered strategy:
  1. pdfplumber (primary): excellent built-in table detection
  2. pymupdf heuristic (fallback): rule-based line/cell detection

Converts PDF-extracted tables into RawTable objects for downstream
economics semantic parsing.

Design constraints:
  - No Java dependencies (no tabula)
  - pymupdf is the only hard PDF dep (already in project)
  - pdfplumber is optional enhancement (already conditionally imported)
  - Graceful degradation: if both fail, return empty list
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .table_parser import CellValue, RawTable

logger = logging.getLogger(__name__)


@dataclass
class PDFTableRegion:
    """Metadata about a detected table region in PDF."""
    page_num: int
    bbox: tuple[float, float, float, float]  # (x0, y0, x1, y1)
    n_rows: int = 0
    n_cols: int = 0
    has_lines: bool = False  # whether ruled lines are present


class PDFTableExtractor:
    """
    Extracts tables from PDF files and converts them to RawTable objects.

    Usage:
        extractor = PDFTableExtractor()
        tables = extractor.extract(pdf_path)
    """

    def __init__(
        self,
        *,
        min_rows: int = 3,
        min_cols: int = 2,
        max_tables: int = 50,
        detect_captions: bool = True,
    ):
        """
        Args:
            min_rows: Minimum number of rows for a valid table.
            min_cols: Minimum number of columns for a valid table.
            max_tables: Maximum tables to extract (safety limit).
            detect_captions: Whether to attempt caption extraction.
        """
        self.min_rows = min_rows
        self.min_cols = min_cols
        self.max_tables = max_tables
        self.detect_captions = detect_captions

    def extract(self, pdf_path: str | Path) -> list[RawTable]:
        """
        Extract tables from a PDF file.

        Tries pdfplumber first (better table detection), falls back to
        pymupdf heuristic approach.

        Args:
            pdf_path: Path to PDF file.

        Returns:
            List of RawTable objects extracted from the PDF.
        """
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            logger.warning("PDF file not found: %s", pdf_path)
            return []

        tables: list[RawTable] = []

        # Strategy 1: pdfplumber (primary)
        try:
            tables = self._extract_with_pdfplumber(pdf_path)
            if tables:
                logger.info(
                    "Extracted %d tables from PDF via pdfplumber: %s",
                    len(tables), pdf_path.name,
                )
                return tables[:self.max_tables]
        except ImportError:
            logger.debug("pdfplumber not available, falling back to pymupdf")
        except Exception as e:
            logger.warning("pdfplumber table extraction failed: %s", e)

        # Strategy 2: pymupdf heuristic
        try:
            tables = self._extract_with_pymupdf(pdf_path)
            if tables:
                logger.info(
                    "Extracted %d tables from PDF via pymupdf: %s",
                    len(tables), pdf_path.name,
                )
                return tables[:self.max_tables]
        except ImportError:
            logger.warning("Neither pdfplumber nor pymupdf available for table extraction")
        except Exception as e:
            logger.warning("pymupdf table extraction failed: %s", e)

        return []

    # ==================================================================
    # Strategy 1: pdfplumber
    # ==================================================================

    def _extract_with_pdfplumber(self, pdf_path: Path) -> list[RawTable]:
        """Extract tables using pdfplumber's built-in table detection."""
        import pdfplumber

        tables: list[RawTable] = []
        table_counter = 0

        with pdfplumber.open(str(pdf_path)) as pdf:
            for page_num, page in enumerate(pdf.pages):
                page_tables = page.extract_tables(
                    table_settings={
                        "vertical_strategy": "lines_strict",
                        "horizontal_strategy": "lines_strict",
                        "snap_tolerance": 3,
                        "join_tolerance": 3,
                        "edge_min_length": 10,
                        "min_words_vertical": 2,
                        "min_words_horizontal": 1,
                    }
                )

                # If strict lines didn't find tables, try text-based detection
                if not page_tables:
                    page_tables = page.extract_tables(
                        table_settings={
                            "vertical_strategy": "text",
                            "horizontal_strategy": "text",
                            "snap_tolerance": 5,
                            "join_tolerance": 5,
                        }
                    )

                for raw_grid in page_tables:
                    if not raw_grid:
                        continue
                    if len(raw_grid) < self.min_rows:
                        continue
                    if not raw_grid[0] or len(raw_grid[0]) < self.min_cols:
                        continue

                    table_counter += 1
                    raw_table = self._grid_to_raw_table(
                        raw_grid,
                        table_id=f"pdf_table_{table_counter}",
                        page_num=page_num,
                    )

                    if raw_table is not None:
                        # Try to find caption from page text
                        if self.detect_captions:
                            caption = self._find_caption_on_page(page, raw_grid)
                            if caption:
                                raw_table.caption = caption
                        tables.append(raw_table)

                    if len(tables) >= self.max_tables:
                        break
                if len(tables) >= self.max_tables:
                    break

        return tables

    def _find_caption_on_page(self, page, table_grid) -> str:
        """Attempt to find table caption text near the table on the page."""
        try:
            page_text = page.extract_text() or ""
            lines = page_text.split("\n")

            for line in lines:
                line_stripped = line.strip()
                if re.match(
                    r"^Table\s+\d+[\.:]\s+",
                    line_stripped,
                    re.IGNORECASE,
                ):
                    return line_stripped
                if re.match(
                    r"^Table\s+[IVXLC]+[\.:]\s+",
                    line_stripped,
                    re.IGNORECASE,
                ):
                    return line_stripped
        except Exception:
            pass
        return ""

    # ==================================================================
    # Strategy 2: pymupdf heuristic
    # ==================================================================

    def _extract_with_pymupdf(self, pdf_path: Path) -> list[RawTable]:
        """
        Extract tables using pymupdf's text block analysis.

        Heuristic approach:
        1. Detect pages that likely contain tables (presence of many
           aligned numbers, separator lines, or "Table N" captions)
        2. Extract text in blocks and attempt to parse tabular structure
        """
        doc = self._open_pdf(pdf_path)
        tables: list[RawTable] = []
        table_counter = 0

        for page_num in range(len(doc)):
            page = doc[page_num]
            # Get text blocks with position info
            blocks = page.get_text("blocks")
            # Get any ruled lines (drawings)
            drawings = page.get_drawings() if hasattr(page, 'get_drawings') else []

            # Detect table regions using ruled lines
            table_regions = self._detect_table_regions_by_lines(
                drawings, page.rect.width, page.rect.height, page_num
            )

            if table_regions:
                for region in table_regions:
                    grid = self._extract_grid_from_region(page, region)
                    if grid and len(grid) >= self.min_rows:
                        table_counter += 1
                        raw_table = self._grid_to_raw_table(
                            grid,
                            table_id=f"pdf_table_{table_counter}",
                            page_num=page_num,
                        )
                        if raw_table:
                            tables.append(raw_table)
            else:
                # Fallback: try to detect tables from text block alignment
                grid = self._detect_table_from_text_blocks(blocks, page_num)
                if grid and len(grid) >= self.min_rows:
                    table_counter += 1
                    raw_table = self._grid_to_raw_table(
                        grid,
                        table_id=f"pdf_table_{table_counter}",
                        page_num=page_num,
                    )
                    if raw_table:
                        # Look for caption in nearby blocks
                        caption = self._find_caption_in_blocks(blocks)
                        if caption:
                            raw_table.caption = caption
                        tables.append(raw_table)

            if len(tables) >= self.max_tables:
                break

        doc.close()
        return tables

    def _detect_table_regions_by_lines(
        self,
        drawings: list,
        page_width: float,
        page_height: float,
        page_num: int,
    ) -> list[PDFTableRegion]:
        """
        Detect table regions by analyzing ruled lines (horizontal/vertical).

        Tables in economics papers typically have:
        - Multiple horizontal lines (top rule, midrule, bottom rule)
        - Sometimes vertical separators
        """
        if not drawings:
            return []

        horizontal_lines: list[tuple[float, float, float, float]] = []
        vertical_lines: list[tuple[float, float, float, float]] = []

        for d in drawings:
            if not hasattr(d, 'items'):
                continue
            for item in d.get("items", []):
                if item[0] == "l":  # line
                    p1, p2 = item[1], item[2]
                    x0, y0, x1, y1 = p1.x, p1.y, p2.x, p2.y
                    # Horizontal line
                    if abs(y1 - y0) < 2 and abs(x1 - x0) > page_width * 0.3:
                        horizontal_lines.append((x0, y0, x1, y1))
                    # Vertical line
                    elif abs(x1 - x0) < 2 and abs(y1 - y0) > 10:
                        vertical_lines.append((x0, y0, x1, y1))

        if len(horizontal_lines) < 2:
            return []

        # Cluster horizontal lines by y-position to find table boundaries
        horizontal_lines.sort(key=lambda l: l[1])
        regions: list[PDFTableRegion] = []

        # Group lines into tables (lines within reasonable vertical distance)
        groups: list[list[tuple[float, float, float, float]]] = []
        current_group: list[tuple[float, float, float, float]] = [horizontal_lines[0]]

        for i in range(1, len(horizontal_lines)):
            prev_y = horizontal_lines[i - 1][1]
            curr_y = horizontal_lines[i][1]
            # If gap is too large (> 40% page height), it's a new table
            if curr_y - prev_y > page_height * 0.4:
                if len(current_group) >= 2:
                    groups.append(current_group)
                current_group = [horizontal_lines[i]]
            else:
                current_group.append(horizontal_lines[i])

        if len(current_group) >= 2:
            groups.append(current_group)

        for group in groups:
            y_values = [l[1] for l in group]
            x_values = [l[0] for l in group] + [l[2] for l in group]
            region = PDFTableRegion(
                page_num=page_num,
                bbox=(min(x_values), min(y_values), max(x_values), max(y_values)),
                has_lines=True,
            )
            regions.append(region)

        return regions

    def _extract_grid_from_region(
        self, page, region: PDFTableRegion
    ) -> list[list[str]]:
        """Extract a text grid from a detected table region."""
        x0, y0, x1, y1 = region.bbox
        # Add small margins
        clip_rect = (x0 - 5, y0 - 5, x1 + 5, y1 + 5)

        try:
            # Get text within the region with layout
            text = page.get_text("text", clip=clip_rect)
            if not text.strip():
                return []

            # Parse lines into grid
            lines = text.strip().split("\n")
            grid: list[list[str]] = []
            for line in lines:
                # Split by multiple spaces (column separator heuristic)
                cells = re.split(r"\s{2,}", line.strip())
                if len(cells) >= self.min_cols:
                    grid.append(cells)

            return grid
        except Exception:
            return []

    def _detect_table_from_text_blocks(
        self, blocks: list, page_num: int
    ) -> list[list[str]]:
        """
        Heuristic: detect tables from text block alignment.

        If multiple consecutive lines have similar column structure
        (same number of "columns" separated by large gaps), it's likely a table.
        """
        # Extract text from blocks and find table-like patterns
        text_lines: list[str] = []
        for block in blocks:
            if block[6] == 0:  # text block (not image)
                block_text = block[4]
                for line in block_text.split("\n"):
                    text_lines.append(line)

        if not text_lines:
            return []

        # Find consecutive lines with consistent column count
        grid: list[list[str]] = []
        consistent_count = 0
        target_cols = 0

        for line in text_lines:
            cells = re.split(r"\s{3,}", line.strip())
            if len(cells) >= self.min_cols:
                if target_cols == 0:
                    target_cols = len(cells)
                if abs(len(cells) - target_cols) <= 1:
                    grid.append(cells)
                    consistent_count += 1
                else:
                    # Column count changed significantly
                    if consistent_count >= self.min_rows:
                        break
                    grid = [cells]
                    target_cols = len(cells)
                    consistent_count = 1
            else:
                if consistent_count >= self.min_rows:
                    break
                grid = []
                consistent_count = 0
                target_cols = 0

        if len(grid) >= self.min_rows:
            return grid
        return []

    def _find_caption_in_blocks(self, blocks: list) -> str:
        """Find table caption from text blocks."""
        for block in blocks:
            if block[6] == 0:  # text block
                text = block[4].strip()
                if re.match(r"^Table\s+\d+[\.:]\s+", text, re.IGNORECASE):
                    # Return first line only (caption)
                    return text.split("\n")[0].strip()
                if re.match(r"^Table\s+[IVXLC]+[\.:]\s+", text, re.IGNORECASE):
                    return text.split("\n")[0].strip()
        return ""

    # ==================================================================
    # Grid → RawTable conversion
    # ==================================================================

    def _grid_to_raw_table(
        self,
        grid: list[list[Optional[str]]],
        table_id: str,
        page_num: int,
    ) -> Optional[RawTable]:
        """
        Convert a raw grid (list of string lists) into a RawTable.

        Determines which rows are headers vs body based on content analysis.
        """
        if not grid:
            return None

        # Normalize: replace None with empty string
        normalized: list[list[str]] = []
        max_cols = 0
        for row in grid:
            clean_row = [str(cell).strip() if cell else "" for cell in row]
            normalized.append(clean_row)
            max_cols = max(max_cols, len(clean_row))

        # Pad rows to same length
        for row in normalized:
            while len(row) < max_cols:
                row.append("")

        if max_cols < self.min_cols:
            return None

        # Determine header rows
        # Heuristic: first N rows that are mostly text (not numbers) are headers
        header_end = self._find_header_boundary(normalized)

        headers = normalized[:header_end]
        body_cells = normalized[header_end:]

        if len(body_cells) < 1:
            return None

        # Convert body to CellValue objects
        body: list[list[CellValue]] = []
        for row in body_cells:
            cell_row = [CellValue(raw=cell) for cell in row]
            body.append(cell_row)

        raw_table = RawTable(
            table_id=table_id,
            caption="",
            headers=headers,
            body=body,
            notes="",
            source_format=f"pdf_page_{page_num + 1}",
        )

        return raw_table

    def _find_header_boundary(self, rows: list[list[str]]) -> int:
        """
        Find where headers end and body begins.

        Heuristics:
        - Header rows tend to be mostly text
        - Body rows tend to have numbers (especially in economics tables)
        - A separator line (all dashes/empty) marks header end
        - If first row has column numbers like (1) (2) (3), that's a header
        """
        if len(rows) <= 1:
            return 1

        for i, row in enumerate(rows):
            # Check for separator line
            if all(
                re.match(r"^[-─━═]+$", cell) or cell == ""
                for cell in row
            ):
                return i + 1  # header includes rows up to (but not) separator

            # Check if row is predominantly numeric (body indicator)
            numeric_count = sum(
                1 for cell in row[1:]  # Skip first column (row labels)
                if re.match(r"^[-+]?\d*\.?\d+\s*\*{0,3}$", cell.strip())
                or re.match(r"^\([\d.]+\)$", cell.strip())
            )
            non_empty = sum(1 for cell in row[1:] if cell.strip())

            if non_empty > 0 and numeric_count / max(non_empty, 1) > 0.5:
                # This row looks like body
                return max(i, 1)

        # Default: first row is header
        return 1

    # ==================================================================
    # Utilities
    # ==================================================================

    def _open_pdf(self, path: Path):
        """Open PDF with pymupdf (new or legacy API)."""
        try:
            import pymupdf
            return pymupdf.open(str(path))
        except ImportError:
            pass
        try:
            import fitz
            return fitz.open(str(path))
        except ImportError:
            raise ImportError(
                "pymupdf is required for PDF table extraction. "
                "Install: pip install pymupdf"
            )
