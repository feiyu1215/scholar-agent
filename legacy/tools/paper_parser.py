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
        # Fallback: try just the filename in the project root
        fallback = Path(__file__).parent.parent / paper_path.name
        if fallback.exists():
            paper_path = fallback
        else:
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

    # Detect academic field and language (zero LLM cost)
    discipline, field_confidence = _detect_discipline(text, sections)
    language = _detect_language(text)

    # Save metadata (includes discipline + language for downstream tools)
    metadata = {
        "source_file": str(paper_path),
        "format": ext,
        "total_sections": len(sections),
        "total_words": sum(s["word_count"] for s in index),
        "total_chars": sum(s["char_count"] for s in index),
        "discipline": discipline,
        "discipline_confidence": round(field_confidence, 3),
        "language": language,
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
    """Enhanced LaTeX-to-text conversion.

    Improvements over basic version:
        - Multi-file support: resolves \\input{} and \\include{} recursively
        - Citation key preservation: \\cite{key} → [cite:key] for literature_verify
        - Environment awareness: preserves figure/table captions, handles math
        - Economics macro support: \\citet, \\citep, threeparttable, tabular*
    """
    raw = _resolve_tex_includes(path)
    text = _tex_to_text(raw)
    return text


def _resolve_tex_includes(path: Path, _visited: Optional[set] = None) -> str:
    """Recursively resolve \\input{} and \\include{} directives.

    Handles relative paths from the parent .tex file's directory.
    Guards against circular includes.
    """
    if _visited is None:
        _visited = set()

    resolved = path.resolve()
    if resolved in _visited:
        return ""  # Circular include guard
    _visited.add(resolved)

    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""

    base_dir = path.parent

    def _include_replacer(match):
        filename = match.group(1).strip()
        # Try with and without .tex extension
        candidates = [
            base_dir / filename,
            base_dir / f"{filename}.tex",
        ]
        for candidate in candidates:
            if candidate.exists():
                return _resolve_tex_includes(candidate, _visited)
        return ""  # File not found — silently skip

    # Replace \input{file} and \include{file}
    raw = re.sub(r"\\input\{([^}]+)\}", _include_replacer, raw)
    raw = re.sub(r"\\include\{([^}]+)\}", _include_replacer, raw)

    return raw


def _tex_to_text(raw: str) -> str:
    """Convert resolved LaTeX source to structured plain text.

    Preserves:
        - Section hierarchy (as markdown headings)
        - Citation keys (as [cite:key1,key2])
        - Figure/table captions
        - Math environments (as [MATH] placeholders)
    """
    # Step 1: Remove comments (but preserve \% literal percent)
    lines = []
    for line in raw.splitlines():
        # Remove inline comments (% not preceded by \)
        cleaned = re.sub(r"(?<!\\)%.*$", "", line)
        lines.append(cleaned)
    text = "\n".join(lines)

    # Step 2: Remove preamble (everything before \begin{document})
    doc_begin = re.search(r"\\begin\{document\}", text)
    if doc_begin:
        text = text[doc_begin.end():]
    doc_end = re.search(r"\\end\{document\}", text)
    if doc_end:
        text = text[:doc_end.start()]

    # Step 3: Section hierarchy → markdown headings
    text = re.sub(r"\\section\*?\{([^}]+)\}", r"\n## \1\n", text)
    text = re.sub(r"\\subsection\*?\{([^}]+)\}", r"\n### \1\n", text)
    text = re.sub(r"\\subsubsection\*?\{([^}]+)\}", r"\n#### \1\n", text)
    text = re.sub(r"\\paragraph\*?\{([^}]+)\}", r"\n##### \1\n", text)

    # Step 4: Citation commands → [cite:keys] (preserve keys for literature_verify)
    # Handle: \cite{k}, \citet{k}, \citep{k}, \citeauthor{k},
    #         \citealp{k}, \citealt{k}, \textcite{k}, \parencite{k}
    text = re.sub(
        r"\\(?:cite[tp]?|citeauthor|citealp|citealt|textcite|parencite|autocite)"
        r"(?:\[[^\]]*\])*"  # optional [...] arguments
        r"\{([^}]+)\}",
        lambda m: f"[cite:{m.group(1).strip()}]",
        text,
    )

    # Step 5: Abstract environment → heading
    text = re.sub(r"\\begin\{abstract\}", "\n## Abstract\n", text)
    text = re.sub(r"\\end\{abstract\}", "\n", text)

    # Step 6: Math environments → [MATH] placeholder (preserve readability)
    # Display math: equation, align, gather, multline, eqnarray
    math_envs = r"(?:equation|align|gather|multline|eqnarray)\*?"
    text = re.sub(
        r"\\begin\{(" + math_envs + r")\}(.*?)\\end\{\1\}",
        r"[MATH]",
        text,
        flags=re.DOTALL,
    )
    # Inline display math: \[...\]
    text = re.sub(r"\\\[.*?\\\]", "[MATH]", text, flags=re.DOTALL)
    # Inline math: $...$ (keep short ones for readability)
    text = re.sub(r"\$\$.*?\$\$", "[MATH]", text, flags=re.DOTALL)

    # Step 7: Figure/table environments → extract captions
    def _extract_caption(match):
        env_name = match.group(1)
        content = match.group(2)
        # Extract caption text
        caption_match = re.search(r"\\caption\{([^}]+)\}", content)
        caption = caption_match.group(1) if caption_match else ""
        # Extract label
        label_match = re.search(r"\\label\{([^}]+)\}", content)
        label = f" ({label_match.group(1)})" if label_match else ""
        return f"\n[{env_name.upper()}{label}: {caption}]\n" if caption else f"\n[{env_name.upper()}{label}]\n"

    text = re.sub(
        r"\\begin\{(figure|table)\*?\}(.*?)\\end\{\1\*?\}",
        _extract_caption,
        text,
        flags=re.DOTALL,
    )

    # Step 8: Tabular environments → [TABLE] placeholder
    text = re.sub(
        r"\\begin\{(?:tabular|tabular\*|threeparttable|longtable)\}.*?\\end\{(?:tabular|tabular\*|threeparttable|longtable)\}",
        "[TABLE]",
        text,
        flags=re.DOTALL,
    )

    # Step 9: Footnotes → inline (preserve content)
    text = re.sub(r"\\footnote\{([^}]+)\}", r" [\1]", text)

    # Step 10: Cross-references
    text = re.sub(r"\\(?:auto|name|c)?ref\{([^}]+)\}", r"[ref:\1]", text)
    text = re.sub(r"\\label\{[^}]+\}", "", text)

    # Step 11: Text formatting commands → keep content
    text = re.sub(r"\\(?:textbf|textit|emph|underline|textsc|textrm|textsf|texttt)\{([^}]+)\}", r"\1", text)
    text = re.sub(r"\\(?:bf|it|em|sc|rm|sf|tt)\b", "", text)
    # \text{...} inside math
    text = re.sub(r"\\text\{([^}]+)\}", r"\1", text)

    # Step 12: Remove remaining environments we don't need
    # (appendix, acknowledgments are kept as-is since they're just wrappers)
    unwanted_envs = r"(?:tikzpicture|lstlisting|verbatim|comment)"
    text = re.sub(
        r"\\begin\{(" + unwanted_envs + r")\}.*?\\end\{\1\}",
        "",
        text,
        flags=re.DOTALL,
    )

    # Step 13: Other known LaTeX commands → strip or simplify
    text = re.sub(r"\\(?:hline|toprule|midrule|bottomrule|cline\{[^}]*\})", "", text)
    text = re.sub(r"\\(?:vspace|hspace|vskip|hskip|bigskip|medskip|smallskip)(\{[^}]*\}|\*\{[^}]*\})?", "", text)
    text = re.sub(r"\\(?:centering|raggedright|raggedleft|noindent|newpage|clearpage)", "", text)
    text = re.sub(r"\\(?:maketitle|tableofcontents|bibliography\{[^}]*\}|bibliographystyle\{[^}]*\})", "", text)
    text = re.sub(r"\\item\s*", "• ", text)

    # Step 14: Generic cleanup — remaining \command{content} → content
    text = re.sub(r"\\[a-zA-Z]+\*?(\[[^\]]*\])?\{([^}]*)\}", r"\2", text)
    # Remove bare commands: \something (no braces)
    text = re.sub(r"\\[a-zA-Z]+\*?", "", text)
    # Remove leftover braces/brackets
    text = re.sub(r"[{}]", "", text)

    # Step 15: Clean up whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    # Remove leading spaces on each line
    text = re.sub(r"^ +", "", text, flags=re.MULTILINE)

    return text.strip()


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


# ============================================================
# Field & Language Auto-Detection (called during parse)
# ============================================================

def _detect_discipline(full_text: str, sections: list) -> tuple:
    """Detect academic discipline using field_detector.
    
    Extracts abstract (if available) and uses keyword-based TF-IDF matching.
    Returns (field_name, confidence) — e.g. ("economics", 0.85).
    """
    from utils.field_detector import detect_field

    # Try to find abstract section for best signal
    abstract = ""
    title = ""
    for sec in sections:
        slug_lower = sec.get("slug", "").lower()
        title_lower = sec.get("title", "").lower()
        if "abstract" in slug_lower or "abstract" in title_lower:
            abstract = sec.get("content", "")
        elif not title and ("title" in slug_lower or "introduction" in title_lower):
            # Use first sentence of intro as fallback title signal
            title = sec.get("content", "")[:200]

    # If no abstract found, use first 1000 chars of full text
    if not abstract:
        abstract = full_text[:1000]

    field, confidence = detect_field(abstract=abstract, title=title)
    return (field, confidence)


def _detect_language(full_text: str) -> str:
    """Detect primary language of the paper.
    
    Returns "zh" for Chinese, "en" for English (default).
    Uses the same >30% CJK heuristic as deai_engine.
    """
    # Sample from the middle of the text for more representative detection
    sample_start = len(full_text) // 4
    sample = full_text[sample_start:sample_start + 2000]
    if not sample:
        sample = full_text[:2000]

    non_space = sample.replace(" ", "").replace("\n", "").replace("\t", "")
    if not non_space:
        return "en"
    cjk_count = sum(1 for c in non_space if '\u4e00' <= c <= '\u9fff')
    return "zh" if cjk_count / len(non_space) > 0.3 else "en"
