r"""
tools/bib_search.py - Local .bib file search for ScholarAgent.

Provides structured search over the user's local BibTeX/BibLaTeX bibliography,
enabling the agent to find relevant existing citations before suggesting new ones.

This creates a two-tier search strategy:
1. Local bib search (this module) — check what the user already has
2. Online search (web_search.py) — find new papers if local coverage is insufficient

Key capabilities:
- Load and parse .bib files (BibTeX/BibLaTeX, Zotero-compatible)
- Search by author, year, venue, keywords, title/abstract topic matching
- Compact query language: "author:zhang year>=2023 has:doi type:article"
- Relevance scoring with multiple signals
- Citation format output (LaTeX \cite{key} and plain text)
- Recommendation of uncited-but-relevant papers from user's library
"""
from __future__ import annotations

import os
import re
from typing import List, Dict, Optional, Tuple, Set
from dataclasses import dataclass, field
from pathlib import Path


# ─── Data Structures ────────────────────────────────────────────────────────────

@dataclass
class BibEntry:
    """A single parsed BibTeX entry."""
    key: str  # Citation key (e.g., "zhang2023transformer")
    entry_type: str  # article, inproceedings, book, misc, etc.
    title: str = ""
    authors: List[str] = field(default_factory=list)
    year: int = 0
    venue: str = ""  # journal or booktitle
    abstract: str = ""
    keywords: List[str] = field(default_factory=list)
    doi: str = ""
    url: str = ""
    pages: str = ""
    volume: str = ""
    publisher: str = ""
    note: str = ""
    annotation: str = ""
    raw_fields: Dict[str, str] = field(default_factory=dict)

    @property
    def has_abstract(self) -> bool:
        return bool(self.abstract.strip())

    @property
    def has_doi(self) -> bool:
        return bool(self.doi.strip())

    @property
    def has_url(self) -> bool:
        return bool(self.url.strip())

    @property
    def author_string(self) -> str:
        """Formatted author string."""
        if not self.authors:
            return ""
        if len(self.authors) <= 3:
            return ", ".join(self.authors)
        return f"{self.authors[0]} et al."

    def cite_key_latex(self) -> str:
        """LaTeX citation command."""
        return f"\\cite{{{self.key}}}"

    def cite_key_natbib(self) -> str:
        """Natbib-style citation."""
        return f"\\citep{{{self.key}}}"

    def to_reference_string(self) -> str:
        """Human-readable reference string."""
        parts = []
        if self.authors:
            parts.append(self.author_string)
        if self.year:
            parts.append(f"({self.year})")
        if self.title:
            parts.append(f'"{self.title}"')
        if self.venue:
            parts.append(self.venue)
        return ". ".join(parts) + "."


@dataclass
class SearchFilter:
    """Structured search filters for bib queries."""
    query: str = ""  # Free-text topic search
    author: str = ""  # Author name substring match
    year_min: int = 0
    year_max: int = 9999
    year_exact: int = 0
    entry_type: str = ""  # article, inproceedings, etc.
    venue: str = ""  # Journal/conference substring
    has: List[str] = field(default_factory=list)  # doi, abstract, url, keywords
    keywords: List[str] = field(default_factory=list)  # keyword filter


@dataclass
class SearchResult:
    """A scored search result."""
    entry: BibEntry
    relevance_score: float  # 0.0 - 1.0
    match_reasons: List[str] = field(default_factory=list)


@dataclass
class BibSearchResponse:
    """Response from a bib search operation."""
    results: List[SearchResult] = field(default_factory=list)
    total_entries: int = 0
    query_used: str = ""
    error: str = ""

    @property
    def found(self) -> bool:
        return len(self.results) > 0

    def top_n(self, n: int = 5) -> List[SearchResult]:
        return sorted(self.results, key=lambda r: r.relevance_score, reverse=True)[:n]

    def format_results(self, max_results: int = 10) -> str:
        """Format results as readable markdown."""
        if self.error:
            return f"❌ Search error: {self.error}"
        if not self.results:
            return f"No results found for: {self.query_used}"

        lines = [f"Found {len(self.results)} results (showing top {min(max_results, len(self.results))}):\n"]
        for i, r in enumerate(self.top_n(max_results), 1):
            e = r.entry
            lines.append(f"{i}. [{e.cite_key_latex()}] {e.author_string} ({e.year})")
            lines.append(f"   \"{e.title}\"")
            if e.venue:
                lines.append(f"   {e.venue}")
            lines.append(f"   Relevance: {r.relevance_score:.2f} | Reasons: {', '.join(r.match_reasons)}")
            lines.append("")
        return "\n".join(lines)


# ─── Parser ─────────────────────────────────────────────────────────────────────

def parse_bib_file(filepath: str) -> List[BibEntry]:
    """Parse a .bib file into structured BibEntry objects.

    Handles common BibTeX/BibLaTeX syntax including:
    - Standard entry types (@article, @inproceedings, @book, etc.)
    - Braced and quoted field values
    - Multi-line values
    - LaTeX special characters (basic cleanup)
    - Zotero export format quirks
    """
    if not os.path.isfile(filepath):
        return []

    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
        content = f.read()

    entries = []
    # Match entry blocks: @type{key, ... }
    # Use a state-machine approach for robust parsing
    entry_pattern = re.compile(r'@(\w+)\s*\{([^,]*),', re.IGNORECASE)

    pos = 0
    while pos < len(content):
        match = entry_pattern.search(content, pos)
        if not match:
            break

        entry_type = match.group(1).lower()
        key = match.group(2).strip()

        # Skip @string, @preamble, @comment
        if entry_type in ('string', 'preamble', 'comment'):
            pos = match.end()
            continue

        # Find the matching closing brace
        # The opening { was consumed by the regex, so start with count=1
        brace_count = 1
        entry_end = match.end()
        for i in range(match.end(), len(content)):
            if content[i] == '{':
                brace_count += 1
            elif content[i] == '}':
                brace_count -= 1
                if brace_count == 0:
                    entry_end = i + 1
                    break

        # Extract fields from entry body
        body = content[match.end():entry_end - 1]
        fields = _parse_fields(body)

        # Build BibEntry
        entry = BibEntry(
            key=key,
            entry_type=entry_type,
            title=_clean_latex(fields.get('title', '')),
            authors=_parse_authors(fields.get('author', '')),
            year=_parse_year(fields.get('year', '')),
            venue=_clean_latex(fields.get('journal', '') or fields.get('booktitle', '')),
            abstract=_clean_latex(fields.get('abstract', '')),
            keywords=_parse_keywords(fields.get('keywords', '')),
            doi=fields.get('doi', '').strip(),
            url=fields.get('url', '').strip(),
            pages=fields.get('pages', '').strip(),
            volume=fields.get('volume', '').strip(),
            publisher=_clean_latex(fields.get('publisher', '')),
            note=fields.get('note', '').strip(),
            annotation=fields.get('annotation', '') or fields.get('annote', ''),
            raw_fields=fields,
        )
        entries.append(entry)
        pos = entry_end

    return entries


def _parse_fields(body: str) -> Dict[str, str]:
    """Parse field = value pairs from a BibTeX entry body."""
    fields = {}
    # Match: field_name = {value} or field_name = "value" or field_name = number
    field_pattern = re.compile(
        r'(\w+)\s*=\s*(?:\{((?:[^{}]|\{[^{}]*\})*)\}|"([^"]*)"|(\d+))',
        re.DOTALL
    )
    for m in field_pattern.finditer(body):
        name = m.group(1).lower()
        value = m.group(2) if m.group(2) is not None else (
            m.group(3) if m.group(3) is not None else m.group(4)
        )
        fields[name] = value.strip() if value else ""
    return fields


def _parse_authors(author_str: str) -> List[str]:
    """Parse BibTeX author string into list of author names."""
    if not author_str.strip():
        return []
    # Split on " and " (BibTeX convention)
    raw_authors = re.split(r'\s+and\s+', author_str, flags=re.IGNORECASE)
    authors = []
    for a in raw_authors:
        a = _clean_latex(a.strip())
        if not a:
            continue
        # Handle "Last, First" format
        if ',' in a:
            parts = a.split(',', 1)
            a = f"{parts[1].strip()} {parts[0].strip()}"
        authors.append(a)
    return authors


def _parse_year(year_str: str) -> int:
    """Extract year as integer."""
    match = re.search(r'(\d{4})', year_str)
    return int(match.group(1)) if match else 0


def _parse_keywords(kw_str: str) -> List[str]:
    """Parse keyword string (comma or semicolon separated)."""
    if not kw_str.strip():
        return []
    # Split on comma or semicolon
    parts = re.split(r'[,;]', kw_str)
    return [p.strip() for p in parts if p.strip()]


def _clean_latex(text: str) -> str:
    """Basic LaTeX cleanup for display."""
    if not text:
        return ""
    # Remove braces used for case preservation
    text = re.sub(r'(?<!\\)[{}]', '', text)
    # Common LaTeX commands
    text = text.replace('\\&', '&')
    text = text.replace('\\%', '%')
    text = text.replace('\\$', '$')
    text = text.replace('\\textit', '')
    text = text.replace('\\textbf', '')
    text = text.replace('\\emph', '')
    text = re.sub(r'\\[a-zA-Z]+\{([^}]*)\}', r'\1', text)
    # Trim whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    return text


# ─── Query Language Parser ──────────────────────────────────────────────────────

def parse_compact_query(query_string: str) -> SearchFilter:
    """Parse compact query language into structured SearchFilter.

    Supported syntax:
        author:name         — author substring match
        year:2023           — exact year
        year>=2020          — minimum year
        year<=2024          — maximum year
        type:article        — entry type filter
        venue:neurips       — venue/journal substring
        has:doi             — existence filter (doi, abstract, url, keywords)
        keyword:transformer — keyword filter
        <free text>         — topic/title search

    Example: "author:zhang year>=2023 has:doi transformer attention"
    """
    sf = SearchFilter()
    free_text_parts = []

    tokens = _tokenize_query(query_string)

    for token in tokens:
        if ':' in token and not token.startswith('http'):
            key, value = token.split(':', 1)
            key = key.lower()

            if key == 'author':
                sf.author = value
            elif key == 'year':
                sf.year_exact = _parse_year(value)
            elif key == 'type':
                sf.entry_type = value.lower()
            elif key == 'venue':
                sf.venue = value
            elif key == 'has':
                sf.has.append(value.lower())
            elif key in ('keyword', 'kw'):
                sf.keywords.append(value)
            else:
                free_text_parts.append(token)
        elif token.startswith('year>='):
            try:
                sf.year_min = int(token[6:])
            except ValueError:
                free_text_parts.append(token)
        elif token.startswith('year<='):
            try:
                sf.year_max = int(token[6:])
            except ValueError:
                free_text_parts.append(token)
        elif token.startswith('year>'):
            try:
                sf.year_min = int(token[5:]) + 1
            except ValueError:
                free_text_parts.append(token)
        elif token.startswith('year<'):
            try:
                sf.year_max = int(token[5:]) - 1
            except ValueError:
                free_text_parts.append(token)
        else:
            free_text_parts.append(token)

    sf.query = ' '.join(free_text_parts)
    return sf


def _tokenize_query(query: str) -> List[str]:
    """Tokenize query string, respecting quoted phrases."""
    tokens = []
    # Match quoted phrases or individual tokens
    pattern = re.compile(r'"([^"]+)"|(\S+)')
    for m in pattern.finditer(query):
        tokens.append(m.group(1) or m.group(2))
    return tokens


# ─── Search Engine ──────────────────────────────────────────────────────────────

class BibLibrary:
    """In-memory bibliography library with search capabilities."""

    def __init__(self):
        self.entries: List[BibEntry] = []
        self._loaded_files: Set[str] = set()

    def load_file(self, filepath: str) -> int:
        """Load a .bib file. Returns number of entries loaded."""
        filepath = str(Path(filepath).resolve())
        if filepath in self._loaded_files:
            return 0
        new_entries = parse_bib_file(filepath)
        self.entries.extend(new_entries)
        self._loaded_files.add(filepath)
        return len(new_entries)

    def load_directory(self, dirpath: str, recursive: bool = True) -> int:
        """Load all .bib files in a directory."""
        total = 0
        dirpath = Path(dirpath)
        if not dirpath.is_dir():
            return 0
        pattern = '**/*.bib' if recursive else '*.bib'
        for bib_file in dirpath.glob(pattern):
            total += self.load_file(str(bib_file))
        return total

    @property
    def size(self) -> int:
        return len(self.entries)

    def search(self, query: str, limit: int = 10) -> BibSearchResponse:
        """Search the library using compact query language.

        Args:
            query: Search query (compact syntax or free text).
            limit: Maximum results to return.

        Returns:
            BibSearchResponse with scored results.
        """
        sf = parse_compact_query(query)
        return self._search_with_filter(sf, limit, query)

    def search_structured(self, sf: SearchFilter, limit: int = 10) -> BibSearchResponse:
        """Search with a pre-built SearchFilter."""
        return self._search_with_filter(sf, limit, sf.query)

    def find_relevant_uncited(
        self,
        cited_keys: Set[str],
        topic_terms: List[str],
        limit: int = 5,
    ) -> BibSearchResponse:
        """Find papers in library that are relevant but not yet cited.

        This is the key capability for the review workflow:
        "Does the user already have a paper on X in their library that they haven't cited?"

        Args:
            cited_keys: Set of citation keys already used in the paper.
            topic_terms: List of topic/keyword terms to match against.
            limit: Maximum recommendations.

        Returns:
            BibSearchResponse with relevant uncited papers.
        """
        uncited = [e for e in self.entries if e.key not in cited_keys]
        if not uncited or not topic_terms:
            return BibSearchResponse(
                total_entries=self.size,
                query_used=f"uncited relevant to: {', '.join(topic_terms)}",
            )

        # Score each uncited entry against topic terms
        results = []
        for entry in uncited:
            score, reasons = self._score_topic_match(entry, topic_terms)
            if score > 0.1:
                results.append(SearchResult(
                    entry=entry,
                    relevance_score=score,
                    match_reasons=reasons,
                ))

        results.sort(key=lambda r: r.relevance_score, reverse=True)

        return BibSearchResponse(
            results=results[:limit],
            total_entries=self.size,
            query_used=f"uncited relevant to: {', '.join(topic_terms)}",
        )

    def _search_with_filter(
        self, sf: SearchFilter, limit: int, raw_query: str
    ) -> BibSearchResponse:
        """Core search logic with filtering and scoring."""
        candidates = self.entries

        # Apply hard filters
        if sf.author:
            author_lower = sf.author.lower()
            candidates = [
                e for e in candidates
                if any(author_lower in a.lower() for a in e.authors)
            ]

        if sf.year_exact:
            candidates = [e for e in candidates if e.year == sf.year_exact]
        else:
            if sf.year_min:
                candidates = [e for e in candidates if e.year >= sf.year_min]
            if sf.year_max < 9999:
                candidates = [e for e in candidates if e.year <= sf.year_max]

        if sf.entry_type:
            candidates = [e for e in candidates if e.entry_type == sf.entry_type]

        if sf.venue:
            venue_lower = sf.venue.lower()
            candidates = [
                e for e in candidates
                if venue_lower in e.venue.lower()
            ]

        for has_field in sf.has:
            if has_field == 'doi':
                candidates = [e for e in candidates if e.has_doi]
            elif has_field == 'abstract':
                candidates = [e for e in candidates if e.has_abstract]
            elif has_field == 'url':
                candidates = [e for e in candidates if e.has_url]
            elif has_field == 'keywords':
                candidates = [e for e in candidates if e.keywords]

        if sf.keywords:
            kw_lower = [k.lower() for k in sf.keywords]
            candidates = [
                e for e in candidates
                if any(
                    kw in ' '.join(e.keywords).lower()
                    for kw in kw_lower
                )
            ]

        # Score candidates by topic relevance
        results = []
        topic_terms = sf.query.lower().split() if sf.query else []

        for entry in candidates:
            if topic_terms:
                score, reasons = self._score_topic_match(entry, topic_terms)
            else:
                # No topic query — all filtered candidates get base score
                score = 0.5
                reasons = ["filter_match"]

            # Boost by recency
            if entry.year >= 2023:
                score += 0.05
            elif entry.year >= 2020:
                score += 0.02

            results.append(SearchResult(
                entry=entry,
                relevance_score=min(1.0, score),
                match_reasons=reasons,
            ))

        results.sort(key=lambda r: r.relevance_score, reverse=True)

        return BibSearchResponse(
            results=results[:limit],
            total_entries=self.size,
            query_used=raw_query,
        )

    def _score_topic_match(
        self, entry: BibEntry, topic_terms: List[str]
    ) -> Tuple[float, List[str]]:
        """Score an entry against topic terms using multiple signals."""
        score = 0.0
        reasons = []

        # Normalize terms
        terms_lower = [t.lower() for t in topic_terms if len(t) >= 2]
        if not terms_lower:
            return (0.0, [])

        # Title matching (highest weight)
        title_lower = entry.title.lower()
        title_hits = sum(1 for t in terms_lower if t in title_lower)
        if title_hits > 0:
            title_score = min(0.5, title_hits * 0.2)
            score += title_score
            reasons.append(f"title({title_hits} terms)")

        # Abstract matching
        if entry.has_abstract:
            abstract_lower = entry.abstract.lower()
            abstract_hits = sum(1 for t in terms_lower if t in abstract_lower)
            if abstract_hits > 0:
                abs_score = min(0.3, abstract_hits * 0.1)
                score += abs_score
                reasons.append(f"abstract({abstract_hits} terms)")

        # Keywords matching
        if entry.keywords:
            kw_text = ' '.join(entry.keywords).lower()
            kw_hits = sum(1 for t in terms_lower if t in kw_text)
            if kw_hits > 0:
                kw_score = min(0.2, kw_hits * 0.15)
                score += kw_score
                reasons.append(f"keywords({kw_hits} terms)")

        # Venue matching (if topic terms match venue, it's likely relevant)
        if entry.venue:
            venue_lower = entry.venue.lower()
            venue_hits = sum(1 for t in terms_lower if t in venue_lower)
            if venue_hits > 0:
                score += 0.05
                reasons.append("venue_match")

        return (min(1.0, score), reasons)


# ─── Module-level Singleton ─────────────────────────────────────────────────────

_library: Optional[BibLibrary] = None


def get_library() -> BibLibrary:
    """Get or create the module-level BibLibrary singleton."""
    global _library
    if _library is None:
        _library = BibLibrary()
    return _library


def reset_library():
    """Reset the library (for testing)."""
    global _library
    _library = None


# ─── Public API (Tool Interface) ────────────────────────────────────────────────

def search_local_bibliography(
    query: str,
    bib_path: Optional[str] = None,
    limit: int = 10,
) -> str:
    """Search user's local .bib file(s) for relevant references.

    This is the main tool interface called by the agent.

    Args:
        query: Search query (compact syntax or free text).
            Examples:
                "transformer attention mechanism"
                "author:vaswani year:2017"
                "author:zhang year>=2022 has:doi type:article"
        bib_path: Path to .bib file or directory containing .bib files.
            If None, searches .workspace/ and current directory.
        limit: Maximum results.

    Returns:
        Formatted markdown string with search results.
    """
    lib = get_library()

    # Auto-discover bib files if not loaded yet
    if lib.size == 0:
        loaded = _auto_load_bib(lib, bib_path)
        if loaded == 0:
            return (
                "No .bib files found. Please provide the path to your bibliography file "
                "using the bib_path parameter, or place .bib files in the workspace directory."
            )

    response = lib.search(query, limit=limit)
    return response.format_results(max_results=limit)


def find_uncited_relevant(
    cited_keys: List[str],
    topic: str,
    bib_path: Optional[str] = None,
    limit: int = 5,
) -> str:
    """Find papers in user's library that are relevant but not cited.

    Used by the Literature Reviewer to recommend citations the user
    already has but hasn't used in the current paper.

    Args:
        cited_keys: List of citation keys already used in the paper.
        topic: Topic description or keywords for relevance matching.
        bib_path: Path to .bib file(s).
        limit: Maximum recommendations.

    Returns:
        Formatted markdown with recommendations.
    """
    lib = get_library()

    if lib.size == 0:
        loaded = _auto_load_bib(lib, bib_path)
        if loaded == 0:
            return "No .bib files found to search for uncited papers."

    topic_terms = topic.lower().split()
    response = lib.find_relevant_uncited(
        cited_keys=set(cited_keys),
        topic_terms=topic_terms,
        limit=limit,
    )

    if not response.found:
        return f"No relevant uncited papers found for topic: {topic}"

    lines = [f"📚 Found {len(response.results)} relevant papers in your library that aren't cited:\n"]
    for i, r in enumerate(response.results, 1):
        e = r.entry
        lines.append(f"{i}. **{e.cite_key_latex()}**")
        lines.append(f"   {e.author_string} ({e.year}). \"{e.title}\"")
        if e.venue:
            lines.append(f"   *{e.venue}*")
        lines.append(f"   Relevance: {r.relevance_score:.2f} ({', '.join(r.match_reasons)})")
        lines.append("")

    lines.append("Consider adding these citations to strengthen your paper's literature coverage.")
    return "\n".join(lines)


def _auto_load_bib(lib: BibLibrary, explicit_path: Optional[str] = None) -> int:
    """Auto-discover and load .bib files."""
    total = 0

    if explicit_path:
        path = Path(explicit_path)
        if path.is_file() and path.suffix == '.bib':
            total += lib.load_file(str(path))
        elif path.is_dir():
            total += lib.load_directory(str(path))
        return total

    # Try common locations
    search_dirs = [
        '.workspace',
        '.',
        'references',
        'bib',
        'bibliography',
    ]

    for d in search_dirs:
        if Path(d).is_dir():
            total += lib.load_directory(d)

    return total
