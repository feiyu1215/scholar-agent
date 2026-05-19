"""
tools/paper_parser.py — Parse PDF/tex papers into section-level files.

This is the entry point of the pipeline: a paper goes in, structured sections come out.
The paper lives in the filesystem, NOT in context. Agent reads sections on demand.

Output structure:
    .workspace/paper/
        metadata.json          — title, authors, abstract, type
        section_index.json     — [{id, title, line_start, line_end, summary, word_count}]
        sections/
            01_abstract.md
            02_introduction.md
            ...
        full_text.md           — complete text (only for final diff)
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional, List, Dict


def parse_paper(paper_path: str, workspace: str = ".workspace") -> str:
    """Parse a paper file and write structured sections to workspace.
    
    Returns a summary of what was parsed (for the agent's context).
    """
    paper_path = Path(paper_path)
    ws = Path(workspace) / "paper"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "sections").mkdir(exist_ok=True)

    if not paper_path.exists():
        return f"Error: File not found: {paper_path}"

    ext = paper_path.suffix.lower()

    if ext == ".pdf":
        text = _parse_pdf(paper_path)
    elif ext in (".tex", ".latex"):
        text = _parse_tex(paper_path)
    elif ext in (".md", ".txt"):
        text = paper_path.read_text(encoding="utf-8")
    else:
        return f"Error: Unsupported format: {ext}. Supported: .pdf, .tex, .md, .txt"

    if not text or len(text.strip()) < 100:
        return f"Error: Could not extract meaningful text from {paper_path}"

    # Save full text
    (ws / "full_text.md").write_text(text, encoding="utf-8")

    # Split into sections
    sections = _split_sections(text)

    # Save section index
    index = []
    for i, sec in enumerate(sections):
        sec_id = f"{i+1:02d}_{sec['slug']}"
        sec_file = ws / "sections" / f"{sec_id}.md"
        sec_file.write_text(sec["content"], encoding="utf-8")
        index.append({
            "id": sec_id,
            "title": sec["title"],
            "slug": sec["slug"],
            "word_count": len(sec["content"].split()),
            "char_count": len(sec["content"]),
            "file": str(sec_file),
        })

    (ws / "section_index.json").write_text(
        json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # Save metadata
    metadata = {
        "source_file": str(paper_path),
        "format": ext,
        "total_sections": len(sections),
        "total_words": sum(s["word_count"] for s in index),
        "total_chars": sum(s["char_count"] for s in index),
    }
    (ws / "metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # Return compact summary for agent context (NOT the full text)
    summary_lines = [f"Parsed: {paper_path.name} ({metadata['total_words']} words, {len(sections)} sections)"]
    summary_lines.append("Sections:")
    for item in index:
        summary_lines.append(f"  - {item['id']}: {item['title']} ({item['word_count']} words)")
    summary_lines.append(f"\nUse read_section(section_id) to read any section.")
    return "\n".join(summary_lines)


def _parse_pdf(path: Path) -> str:
    """Extract text from PDF using pymupdf (fitz) or pdfplumber."""
    try:
        import fitz  # pymupdf
        doc = fitz.open(str(path))
        text = ""
        for page in doc:
            text += page.get_text() + "\n"
        doc.close()
        return text
    except ImportError:
        pass

    try:
        import pdfplumber
        with pdfplumber.open(str(path)) as pdf:
            text = ""
            for page in pdf.pages:
                text += (page.extract_text() or "") + "\n"
        return text
    except ImportError:
        return "Error: Install pymupdf or pdfplumber: pip install pymupdf pdfplumber"


def _parse_tex(path: Path) -> str:
    """Basic tex to text conversion — strip LaTeX commands, keep structure."""
    raw = path.read_text(encoding="utf-8")
    # Remove comments
    lines = [l for l in raw.splitlines() if not l.strip().startswith("%")]
    text = "\n".join(lines)
    # Keep section markers for splitting
    # Replace \section{X} with markdown-style ## X
    text = re.sub(r"\\section\*?\{([^}]+)\}", r"\n## \1\n", text)
    text = re.sub(r"\\subsection\*?\{([^}]+)\}", r"\n### \1\n", text)
    text = re.sub(r"\\subsubsection\*?\{([^}]+)\}", r"\n#### \1\n", text)
    # Strip common commands
    text = re.sub(r"\\(textbf|textit|emph|underline)\{([^}]+)\}", r"\2", text)
    text = re.sub(r"\\cite\{[^}]+\}", "[citation]", text)
    text = re.sub(r"\\ref\{[^}]+\}", "[ref]", text)
    text = re.sub(r"\\label\{[^}]+\}", "", text)
    text = re.sub(r"\\begin\{(abstract)\}", "\n## Abstract\n", text)
    text = re.sub(r"\\end\{(abstract)\}", "", text)
    text = re.sub(r"\\begin\{[^}]+\}", "", text)
    text = re.sub(r"\\end\{[^}]+\}", "", text)
    text = re.sub(r"\\[a-zA-Z]+(\[[^\]]*\])?\{([^}]*)\}", r"\2", text)
    text = re.sub(r"\\[a-zA-Z]+", "", text)
    return text


def _split_sections(text: str) -> List[Dict]:
    """Split text by heading markers (## or similar patterns)."""
    # Try markdown-style headings first
    heading_pattern = re.compile(r"^(#{1,4})\s+(.+)$", re.MULTILINE)
    matches = list(heading_pattern.finditer(text))

    if len(matches) < 2:
        # Try numbered section pattern: "1. Introduction", "2 Methods", etc.
        heading_pattern = re.compile(
            r"^(\d+\.?\s*)(Introduction|Background|Related Work|Literature Review|"
            r"Methodology|Methods|Method|Model|Data|Results|Discussion|"
            r"Conclusion|Conclusions|Abstract|References|Acknowledgements|Appendix)"
            r"(.*)$",
            re.MULTILINE | re.IGNORECASE
        )
        matches = list(heading_pattern.finditer(text))

    if len(matches) < 2:
        # Can't split meaningfully — treat as one section
        return [{
            "title": "Full Paper",
            "slug": "full_paper",
            "content": text.strip(),
        }]

    sections = []
    for i, match in enumerate(matches):
        title = match.group(0).strip().lstrip("#").strip()
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        content = text[start:end].strip()
        slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")[:40]
        sections.append({
            "title": title,
            "slug": slug,
            "content": content,
        })

    return sections
