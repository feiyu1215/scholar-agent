"""
tools/web_search.py — Web search capability for literature verification.

Provides a search_fn adapter that enables literature_verify.py to look up
papers online and verify their existence, year, venue, etc.

Architecture:
    - Zero external dependencies beyond stdlib (uses urllib + xml.etree)
    - Multiple search backends with automatic fallback:
        1. Semantic Scholar API (free, no key needed, academic-focused)
        2. OpenAlex API (free, 250M+ works, high rate limit with polite pool)
        3. CrossRef API (free, DOI-focused)
        4. arXiv API (free, preprints for CS/Physics/Math/Stats)
    - Field-aware backend ordering (reads preferred_sources from config)
    - Rate limiting built-in per backend
    - Returns structured results for easy consumption by literature_verify

v5 Enhancements:
    - intelligent_search(): field-aware, multi-query search with reranking
    - _expand_query(): generates query variants (synonym, truncated, broadened)
    - _rerank_results(): citation-weighted + recency + venue-match scoring
    - Persistent disk cache with TTL (survives session restarts)

Integration:
    - Inject as `search_fn` into verify_citations_batch()
    - Also exposed as a standalone tool for the agent to use directly
    - intelligent_search() used by the agent loop for enhanced searches

Design:
    - Graceful degradation: if all backends fail, returns empty results (no crash)
    - Respects API rate limits (1 req/sec for Semantic Scholar)
    - Caches results within session to avoid repeated queries
    - Persistent cache: JSON file with TTL-based eviction
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field


# ============================================================
# Data Classes
# ============================================================

@dataclass
class SearchResult:
    """A single search result for an academic paper."""
    title: str
    authors: List[str]
    year: Optional[int]
    venue: Optional[str]
    doi: Optional[str]
    url: Optional[str]
    abstract: Optional[str] = None
    citation_count: Optional[int] = None
    source: str = ""  # Which backend found this


@dataclass
class SearchResponse:
    """Response from a search query."""
    query: str
    results: List[SearchResult]
    total_found: int
    source: str             # "semantic_scholar" | "crossref" | "fallback"
    error: Optional[str] = None


# ============================================================
# Rate Limiter
# ============================================================

class RateLimiter:
    """Simple rate limiter: max N requests per second."""

    def __init__(self, requests_per_second: float = 1.0):
        self.min_interval = 1.0 / requests_per_second
        self.last_request = 0.0

    def wait(self):
        now = time.time()
        elapsed = now - self.last_request
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self.last_request = time.time()


_semantic_scholar_limiter = RateLimiter(requests_per_second=3.0)
_crossref_limiter = RateLimiter(requests_per_second=5.0)
_openalex_limiter = RateLimiter(requests_per_second=10.0)
_arxiv_limiter = RateLimiter(requests_per_second=1.0)


# ============================================================
# Session Cache (in-memory)
# ============================================================

_search_cache: Dict[str, SearchResponse] = {}


def _cache_key(query: str, backend: str) -> str:
    return f"{backend}::{query.lower().strip()}"


# ============================================================
# Persistent Disk Cache
# ============================================================

_CACHE_DIR = Path(os.environ.get(
    "SCHOLAR_CACHE_DIR",
    Path.home() / ".scholar_agent" / "search_cache"
))
_CACHE_TTL_HOURS = 24


def _ensure_cache_dir():
    """Create persistent cache directory if it doesn't exist."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _disk_cache_path(key: str) -> Path:
    """Get filesystem path for a cache key (safe filename)."""
    import hashlib
    safe_name = hashlib.sha256(key.encode()).hexdigest()[:32]
    return _CACHE_DIR / f"{safe_name}.json"


def _disk_cache_get(key: str) -> Optional[SearchResponse]:
    """Retrieve a SearchResponse from persistent disk cache."""
    path = _disk_cache_path(key)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        # Check TTL
        cached_at = data.get("_cached_at", 0)
        if time.time() - cached_at > _CACHE_TTL_HOURS * 3600:
            path.unlink(missing_ok=True)
            return None
        # Reconstruct SearchResponse
        results = [
            SearchResult(
                title=r["title"],
                authors=r["authors"],
                year=r.get("year"),
                venue=r.get("venue"),
                doi=r.get("doi"),
                url=r.get("url"),
                abstract=r.get("abstract"),
                citation_count=r.get("citation_count"),
                source=r.get("source", ""),
            )
            for r in data.get("results", [])
        ]
        return SearchResponse(
            query=data["query"],
            results=results,
            total_found=data.get("total_found", len(results)),
            source=data.get("source", "cached"),
        )
    except (json.JSONDecodeError, KeyError, OSError):
        return None


def _disk_cache_put(key: str, response: SearchResponse):
    """Store a SearchResponse to persistent disk cache."""
    _ensure_cache_dir()
    data = {
        "_cached_at": time.time(),
        "query": response.query,
        "source": response.source,
        "total_found": response.total_found,
        "results": [
            {
                "title": r.title,
                "authors": r.authors,
                "year": r.year,
                "venue": r.venue,
                "doi": r.doi,
                "url": r.url,
                "abstract": r.abstract,
                "citation_count": r.citation_count,
                "source": r.source,
            }
            for r in response.results
        ],
    }
    path = _disk_cache_path(key)
    try:
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass  # Non-fatal: cache write failure is OK


# ============================================================
# Backend 1: Semantic Scholar
# ============================================================

SEMANTIC_SCHOLAR_API = "https://api.semanticscholar.org/graph/v1/paper/search"


def search_semantic_scholar(
    query: str,
    limit: int = 5,
    fields: str = "title,authors,year,venue,externalIds,citationCount,abstract",
) -> SearchResponse:
    """Search Semantic Scholar API (free, no API key required).

    Rate limit: 100 requests per 5 minutes for unauthenticated.
    """
    cache_k = _cache_key(query, "semantic_scholar")
    if cache_k in _search_cache:
        return _search_cache[cache_k]

    _semantic_scholar_limiter.wait()

    params = urllib.parse.urlencode({
        "query": query,
        "limit": limit,
        "fields": fields,
    })
    url = f"{SEMANTIC_SCHOLAR_API}?{params}"

    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "ScholarAgent/1.0 (academic-review-tool)",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        results = []
        for paper in data.get("data", []):
            authors = [a.get("name", "") for a in paper.get("authors", [])]
            external_ids = paper.get("externalIds") or {}
            doi = external_ids.get("DOI")

            results.append(SearchResult(
                title=paper.get("title") or "",
                authors=authors,
                year=paper.get("year"),
                venue=paper.get("venue") or "",
                doi=doi,
                url=f"https://www.semanticscholar.org/paper/{paper.get('paperId', '')}",
                abstract=paper.get("abstract"),
                citation_count=paper.get("citationCount"),
                source="semantic_scholar",
            ))

        response = SearchResponse(
            query=query,
            results=results,
            total_found=data.get("total", len(results)),
            source="semantic_scholar",
        )
        _search_cache[cache_k] = response
        return response

    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as e:
        return SearchResponse(
            query=query,
            results=[],
            total_found=0,
            source="semantic_scholar",
            error=f"Semantic Scholar API error: {str(e)}",
        )


# ============================================================
# Backend 2: CrossRef (DOI-focused)
# ============================================================

CROSSREF_API = "https://api.crossref.org/works"


def search_crossref(
    query: str,
    limit: int = 5,
) -> SearchResponse:
    """Search CrossRef API (free, no key needed).

    Best for: verifying DOIs, finding exact title matches.
    """
    cache_k = _cache_key(query, "crossref")
    if cache_k in _search_cache:
        return _search_cache[cache_k]

    _crossref_limiter.wait()

    params = urllib.parse.urlencode({
        "query": query,
        "rows": limit,
        "select": "DOI,title,author,published-print,container-title,is-referenced-by-count",
    })
    url = f"{CROSSREF_API}?{params}"

    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "ScholarAgent/1.0 (mailto:scholaragent@example.com)",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        results = []
        items = data.get("message", {}).get("items", [])
        for item in items:
            title_list = item.get("title", [])
            title = title_list[0] if title_list else ""

            authors = []
            for author in item.get("author", []):
                name = f"{author.get('given', '')} {author.get('family', '')}".strip()
                if name:
                    authors.append(name)

            # Extract year from published-print
            year = None
            date_parts = item.get("published-print", {}).get("date-parts", [[]])
            if date_parts and date_parts[0]:
                year = date_parts[0][0]

            venue_list = item.get("container-title", [])
            venue = venue_list[0] if venue_list else ""

            results.append(SearchResult(
                title=title,
                authors=authors,
                year=year,
                venue=venue,
                doi=item.get("DOI"),
                url=f"https://doi.org/{item.get('DOI', '')}",
                citation_count=item.get("is-referenced-by-count"),
                source="crossref",
            ))

        total = data.get("message", {}).get("total-results", len(results))
        response = SearchResponse(
            query=query,
            results=results,
            total_found=total,
            source="crossref",
        )
        _search_cache[cache_k] = response
        return response

    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as e:
        return SearchResponse(
            query=query,
            results=[],
            total_found=0,
            source="crossref",
            error=f"CrossRef API error: {str(e)}",
        )


# ============================================================
# DOI Lookup (Direct)
# ============================================================

def lookup_doi(doi: str) -> Optional[SearchResult]:
    """Look up a specific DOI via CrossRef."""
    cache_k = _cache_key(doi, "doi_lookup")
    if cache_k in _search_cache:
        cached = _search_cache[cache_k]
        return cached.results[0] if cached.results else None

    _crossref_limiter.wait()

    url = f"{CROSSREF_API}/{urllib.parse.quote(doi, safe='')}"

    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "ScholarAgent/1.0 (mailto:scholaragent@example.com)",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        item = data.get("message", {})
        title_list = item.get("title", [])
        title = title_list[0] if title_list else ""

        authors = []
        for author in item.get("author", []):
            name = f"{author.get('given', '')} {author.get('family', '')}".strip()
            if name:
                authors.append(name)

        year = None
        date_parts = item.get("published-print", {}).get("date-parts", [[]])
        if date_parts and date_parts[0]:
            year = date_parts[0][0]

        venue_list = item.get("container-title", [])
        venue = venue_list[0] if venue_list else ""

        result = SearchResult(
            title=title,
            authors=authors,
            year=year,
            venue=venue,
            doi=doi,
            url=f"https://doi.org/{doi}",
            citation_count=item.get("is-referenced-by-count"),
            source="crossref_doi",
        )

        # Cache it
        _search_cache[cache_k] = SearchResponse(
            query=doi, results=[result], total_found=1, source="crossref_doi"
        )
        return result

    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError):
        return None


# ============================================================
# Backend 3: OpenAlex (free, high rate limit, full coverage)
# ============================================================

OPENALEX_API = "https://api.openalex.org/works"


def search_openalex(
    query: str,
    limit: int = 5,
) -> SearchResponse:
    """Search OpenAlex API (free, no API key required, 100 req/s with polite pool).

    OpenAlex covers 250M+ works across all disciplines.
    Uses the polite pool via mailto header for higher rate limits.
    """
    cache_k = _cache_key(query, "openalex")
    if cache_k in _search_cache:
        return _search_cache[cache_k]

    _openalex_limiter.wait()

    params = urllib.parse.urlencode({
        "search": query,
        "per_page": limit,
        "select": "id,title,authorships,publication_year,primary_location,cited_by_count,doi",
        "mailto": "scholaragent@example.com",
    })
    url = f"{OPENALEX_API}?{params}"

    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "ScholarAgent/1.0 (mailto:scholaragent@example.com)",
            "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        results = []
        for work in data.get("results", []):
            # Extract authors
            authors = []
            for authorship in work.get("authorships", []):
                author_info = authorship.get("author", {})
                name = author_info.get("display_name", "")
                if name:
                    authors.append(name)

            # Extract venue from primary_location
            venue = ""
            primary_loc = work.get("primary_location") or {}
            source_info = primary_loc.get("source") or {}
            venue = source_info.get("display_name", "")

            # Extract DOI (OpenAlex stores as full URL: https://doi.org/10.xxx)
            doi_raw = work.get("doi") or ""
            doi = doi_raw.replace("https://doi.org/", "") if doi_raw else None

            results.append(SearchResult(
                title=work.get("title") or "",
                authors=authors,
                year=work.get("publication_year"),
                venue=venue,
                doi=doi,
                url=work.get("id", ""),  # OpenAlex URL
                citation_count=work.get("cited_by_count"),
                source="openalex",
            ))

        total = data.get("meta", {}).get("count", len(results))
        response = SearchResponse(
            query=query,
            results=results,
            total_found=total,
            source="openalex",
        )
        _search_cache[cache_k] = response
        return response

    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as e:
        return SearchResponse(
            query=query,
            results=[],
            total_found=0,
            source="openalex",
            error=f"OpenAlex API error: {str(e)}",
        )


# ============================================================
# Backend 4: arXiv (preprints — CS, Physics, Math, etc.)
# ============================================================

ARXIV_API = "https://export.arxiv.org/api/query"


def search_arxiv(
    query: str,
    limit: int = 5,
) -> SearchResponse:
    """Search arXiv API (free, returns Atom XML).

    Best for: CS, Physics, Math, Quantitative Biology, Statistics preprints.
    Rate limit: 1 request per 3 seconds recommended.
    """
    cache_k = _cache_key(query, "arxiv")
    if cache_k in _search_cache:
        return _search_cache[cache_k]

    _arxiv_limiter.wait()

    # arXiv uses a simple search_query parameter
    params = urllib.parse.urlencode({
        "search_query": f"all:{query}",
        "start": 0,
        "max_results": limit,
        "sortBy": "relevance",
        "sortOrder": "descending",
    })
    url = f"{ARXIV_API}?{params}"

    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "ScholarAgent/1.0 (academic-review-tool)",
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            xml_data = resp.read().decode("utf-8")

        # Parse Atom XML without external dependencies
        results = _parse_arxiv_xml(xml_data)

        response = SearchResponse(
            query=query,
            results=results,
            total_found=len(results),
            source="arxiv",
        )
        _search_cache[cache_k] = response
        return response

    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
        return SearchResponse(
            query=query,
            results=[],
            total_found=0,
            source="arxiv",
            error=f"arXiv API error: {str(e)}",
        )


def _parse_arxiv_xml(xml_data: str) -> List[SearchResult]:
    """Parse arXiv Atom XML response into SearchResult list.

    Uses xml.etree.ElementTree (stdlib) — no external dependencies.
    """
    import xml.etree.ElementTree as ET

    results = []
    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError:
        return results

    # Atom namespace
    ns = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}

    for entry in root.findall("atom:entry", ns):
        # Title
        title_el = entry.find("atom:title", ns)
        title = title_el.text.strip().replace("\n", " ") if title_el is not None and title_el.text else ""

        # Authors
        authors = []
        for author_el in entry.findall("atom:author", ns):
            name_el = author_el.find("atom:name", ns)
            if name_el is not None and name_el.text:
                authors.append(name_el.text.strip())

        # Published date → year
        published_el = entry.find("atom:published", ns)
        year = None
        if published_el is not None and published_el.text:
            try:
                year = int(published_el.text[:4])
            except (ValueError, IndexError):
                pass

        # Abstract (summary)
        summary_el = entry.find("atom:summary", ns)
        abstract = summary_el.text.strip().replace("\n", " ") if summary_el is not None and summary_el.text else None

        # URL (id is the arxiv abs link)
        id_el = entry.find("atom:id", ns)
        url = id_el.text.strip() if id_el is not None and id_el.text else ""

        # DOI (from arxiv:doi if present)
        doi_el = entry.find("arxiv:doi", ns)
        doi = doi_el.text.strip() if doi_el is not None and doi_el.text else None

        # arXiv category as "venue"
        categories = entry.findall("atom:category", ns)
        primary_category = ""
        if categories:
            primary_category = categories[0].get("term", "")

        results.append(SearchResult(
            title=title,
            authors=authors,
            year=year,
            venue=f"arXiv:{primary_category}" if primary_category else "arXiv",
            doi=doi,
            url=url,
            abstract=abstract,
            citation_count=None,  # arXiv API doesn't provide citation counts
            source="arxiv",
        ))

    return results


# ============================================================
# Unified Search (with fallback)
# ============================================================

def search_papers(
    query: str,
    limit: int = 5,
) -> SearchResponse:
    """Unified search with automatic fallback between backends.

    Priority: Semantic Scholar → OpenAlex → CrossRef → Empty (graceful degradation)
    """
    # Try Semantic Scholar first (better for academic paper search)
    response = search_semantic_scholar(query, limit=limit)
    if response.results:
        return response

    # Fallback to OpenAlex (broad coverage)
    response = search_openalex(query, limit=limit)
    if response.results:
        return response

    # Fallback to CrossRef (DOI-focused)
    response = search_crossref(query, limit=limit)
    if response.results:
        return response

    # All failed — return empty with combined errors
    return SearchResponse(
        query=query,
        results=[],
        total_found=0,
        source="none",
        error="All search backends failed or returned no results.",
    )


# ============================================================
# search_fn Adapter (for literature_verify.py injection)
# ============================================================

async def search_fn_adapter(title: str, claim_text: str = "") -> Optional[str]:
    """Adapter function compatible with literature_verify's search_fn interface.

    literature_verify expects:
        async def search_fn(title: str, claim_text: str) -> Optional[str]

    Returns the abstract/snippet of the found paper (for content verification),
    or None if not found.
    """
    # Search by title (most reliable for verification)
    response = search_papers(title, limit=3)

    if not response.results:
        return None

    # Find best match by title similarity
    best = _best_title_match(title, response.results)
    if best and best.abstract:
        return best.abstract

    # If no abstract from Semantic Scholar, try getting snippet from title match
    if best:
        return f"Found: {best.title} ({best.year}) in {best.venue}. Authors: {', '.join(best.authors[:3])}"

    return None


def _best_title_match(query_title: str, results: List[SearchResult]) -> Optional[SearchResult]:
    """Find the result with highest title similarity."""
    query_words = set(query_title.lower().split())

    best_result = None
    best_score = 0

    for r in results:
        if not r.title:
            continue
        result_words = set(r.title.lower().split())
        if not result_words:
            continue
        overlap = len(query_words & result_words)
        union = len(query_words | result_words)
        score = overlap / union if union > 0 else 0

        if score > best_score:
            best_score = score
            best_result = r

    # Require at least 50% overlap
    return best_result if best_score > 0.5 else None


# ============================================================
# Standalone Tool Interface
# ============================================================

def search_literature(query: str, limit: int = 5) -> str:
    """Standalone tool: search for academic papers.

    Returns formatted results for the agent to use.
    """
    response = search_papers(query, limit=limit)

    if response.error and not response.results:
        return f"Search failed: {response.error}"

    if not response.results:
        return f"No results found for: '{query}'"

    lines = [
        f"## Search Results for: \"{query}\"",
        f"Source: {response.source} | Found: {response.total_found} total",
        "",
    ]

    for i, r in enumerate(response.results, 1):
        author_str = ", ".join(r.authors[:3])
        if len(r.authors) > 3:
            author_str += " et al."

        lines.append(f"### [{i}] {r.title}")
        lines.append(f"Authors: {author_str}")
        lines.append(f"Year: {r.year or 'Unknown'} | Venue: {r.venue or 'Unknown'}")
        if r.doi:
            lines.append(f"DOI: {r.doi}")
        if r.citation_count is not None:
            lines.append(f"Citations: {r.citation_count}")
        if r.abstract:
            abstract_preview = r.abstract[:200]
            if len(r.abstract) > 200:
                abstract_preview += "..."
            lines.append(f"Abstract: {abstract_preview}")
        lines.append("")

    return "\n".join(lines)


def verify_doi(doi: str) -> str:
    """Standalone tool: verify a specific DOI."""
    result = lookup_doi(doi)
    if not result:
        return f"DOI not found or lookup failed: {doi}"

    return (
        f"DOI: {doi}\n"
        f"Title: {result.title}\n"
        f"Authors: {', '.join(result.authors[:5])}\n"
        f"Year: {result.year}\n"
        f"Venue: {result.venue}\n"
        f"Citations: {result.citation_count or 'N/A'}\n"
        f"Status: ✓ Valid DOI"
    )


# ============================================================
# v5: Intelligent Search (Query Expansion + Reranking)
# ============================================================

def _expand_query(query: str) -> List[str]:
    """
    Generate query variants for broader recall.

    Strategy:
        1. Original query (always first)
        2. Truncated: first 6 significant words (handles overly specific queries)
        3. Broadened: remove quotes and parenthetical qualifiers
        4. Key-phrase: extract noun-phrase-like chunks

    Returns 2-4 query variants (deduplicated).
    """
    variants = [query]

    # Truncated variant: first 6 significant words
    stop_words = {"a", "an", "the", "of", "in", "on", "for", "and", "or", "to", "with", "by", "is", "are"}
    words = query.split()
    significant = [w for w in words if w.lower() not in stop_words]
    if len(significant) > 6:
        truncated = " ".join(significant[:6])
        if truncated != query:
            variants.append(truncated)

    # Broadened: remove quotes and parenthetical content
    import re
    broadened = re.sub(r'["\']', '', query)
    broadened = re.sub(r'\([^)]*\)', '', broadened).strip()
    broadened = re.sub(r'\s+', ' ', broadened)
    if broadened and broadened != query and broadened not in variants:
        variants.append(broadened)

    return variants[:4]


def _rerank_results(
    results: List[SearchResult],
    original_query: str,
    recency_weight: float = 0.6,
    citation_weight: float = 0.25,
    venue_match_weight: float = 0.15,
    top_venues: Optional[List[str]] = None,
) -> List[SearchResult]:
    """
    Rerank search results by composite score.

    Scoring formula:
        score = recency_weight * recency_score
              + citation_weight * citation_score
              + venue_match_weight * venue_score

    Where:
        - recency_score: 1.0 for current year, decaying 0.1/year
        - citation_score: log-normalized citation count
        - venue_score: 1.0 if venue matches top_venues list, else 0.0
    """
    import math

    if not results:
        return results

    current_year = time.localtime().tm_year
    top_venues_lower = {v.lower() for v in (top_venues or [])}

    scored: List[Tuple[float, SearchResult]] = []
    for r in results:
        # Recency score
        if r.year:
            age = max(0, current_year - r.year)
            recency = max(0.0, 1.0 - age * 0.1)
        else:
            recency = 0.3  # Unknown year gets modest score

        # Citation score (log-normalized, cap at ~1.0 for 1000+ citations)
        if r.citation_count and r.citation_count > 0:
            citation = min(1.0, math.log10(r.citation_count + 1) / 3.0)
        else:
            citation = 0.0

        # Venue match score
        venue_score = 0.0
        if r.venue and top_venues_lower:
            if r.venue.lower() in top_venues_lower:
                venue_score = 1.0
            else:
                # Partial match (e.g., "NeurIPS 2023" contains "NeurIPS")
                for v in top_venues_lower:
                    if v in r.venue.lower() or r.venue.lower() in v:
                        venue_score = 0.7
                        break

        # Title relevance bonus (Jaccard overlap with query)
        query_words = set(original_query.lower().split())
        title_words = set(r.title.lower().split()) if r.title else set()
        union = len(query_words | title_words)
        title_relevance = len(query_words & title_words) / union if union > 0 else 0.0

        # Composite score
        score = (
            recency_weight * recency
            + citation_weight * citation
            + venue_match_weight * venue_score
            + 0.2 * title_relevance  # Always add title relevance as tie-breaker
        )
        scored.append((score, r))

    # Sort descending by score
    scored.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in scored]


def intelligent_search(
    query: str,
    field: Optional[str] = None,
    limit: int = 10,
    recency_weight: Optional[float] = None,
    citation_weight: Optional[float] = None,
    venue_match_weight: Optional[float] = None,
    top_venues: Optional[List[str]] = None,
) -> SearchResponse:
    """
    Enhanced search with query expansion, multi-backend querying, and reranking.

    This is the v5 primary search entry point. It:
        1. Loads venue profile config based on detected field
        2. Expands query into 2-4 variants
        3. Searches across backends (respecting field-based source preference)
        4. Deduplicates results by title similarity
        5. Reranks with field-appropriate weights
        6. Uses persistent disk cache for completed searches

    Args:
        query: The search query (title, topic, or keywords)
        field: Academic field (e.g., "computer_science"). Auto-detected if None.
        limit: Max results to return after reranking
        recency_weight: Override recency weight (0-1)
        citation_weight: Override citation weight (0-1)
        venue_match_weight: Override venue match weight (0-1)
        top_venues: Override top venue list

    Returns:
        SearchResponse with reranked, deduplicated results
    """
    # Check persistent cache first
    cache_key_str = _cache_key(f"intelligent::{field or 'auto'}::{query}", "intelligent")
    cached = _disk_cache_get(cache_key_str)
    if cached:
        return cached

    # Load venue profile weights if not overridden
    _rw = recency_weight if recency_weight is not None else 0.6
    _cw = citation_weight if citation_weight is not None else 0.25
    _vw = venue_match_weight if venue_match_weight is not None else 0.15
    _venues = top_venues or []

    # Try loading from config if field is specified
    _field_config: dict = {}
    if field:
        try:
            import yaml
            config_path = Path(__file__).parent.parent / "config" / "academic_sources.yaml"
            if config_path.exists():
                with open(config_path, "r", encoding="utf-8") as f:
                    config = yaml.safe_load(f)
                _field_config = config.get("venue_profiles", {}).get(field, {})
                if recency_weight is None:
                    _rw = _field_config.get("recency_weight", _rw)
                if citation_weight is None:
                    _cw = _field_config.get("citation_weight", _cw)
                if venue_match_weight is None:
                    _vw = _field_config.get("venue_match_weight", _vw)
                if not top_venues:
                    _venues = _field_config.get("top_venues", [])
        except (ImportError, OSError):
            pass  # yaml not available or config missing — use defaults

    # Expand query into variants
    query_variants = _expand_query(query)

    # Select backends based on field-preferred sources
    _backend_map = {
        "semantic_scholar": search_semantic_scholar,
        "crossref": search_crossref,
        "openalex": search_openalex,
        "arxiv": search_arxiv,
    }

    # Determine backend ordering: prefer field-configured sources, then fill remaining
    preferred_names: List[str] = _field_config.get("preferred_sources", [])

    # Build ordered backend list: preferred first, then remaining backends
    all_backend_names = ["semantic_scholar", "openalex", "crossref", "arxiv"]
    ordered_backends = []
    for name in preferred_names:
        if name in _backend_map:
            ordered_backends.append(_backend_map[name])
    for name in all_backend_names:
        fn = _backend_map[name]
        if fn not in ordered_backends:
            ordered_backends.append(fn)

    # Search all variants across backends and collect results
    all_results: List[SearchResult] = []
    seen_titles: set = set()

    for variant in query_variants:
        # Try each backend in field-preferred order
        for search_fn_backend in ordered_backends:
            response = search_fn_backend(variant, limit=limit)
            for r in response.results:
                # Deduplicate by normalized title
                title_norm = r.title.lower().strip() if r.title else ""
                if title_norm and title_norm not in seen_titles:
                    seen_titles.add(title_norm)
                    all_results.append(r)

        # Stop early if we have enough results
        if len(all_results) >= limit * 2:
            break

    # Rerank
    reranked = _rerank_results(
        all_results,
        original_query=query,
        recency_weight=_rw,
        citation_weight=_cw,
        venue_match_weight=_vw,
        top_venues=_venues,
    )

    # Trim to limit
    final_results = reranked[:limit]

    response = SearchResponse(
        query=query,
        results=final_results,
        total_found=len(all_results),
        source="intelligent_search",
    )

    # Persist to disk cache
    _disk_cache_put(cache_key_str, response)

    # Also store in session cache
    _search_cache[cache_key_str] = response

    return response
