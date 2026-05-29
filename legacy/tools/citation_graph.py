"""
tools/citation_graph.py - Citation graph walking for academic papers.

Builds and traverses citation networks to find:
- Upstream references (papers cited by the target)
- Downstream citations (papers that cite the target)
- Common ancestors (shared references between papers)
- Missing key references (highly-cited papers in the same subfield)
- Citation chains (A cites B cites C -> transitive influence)

Data sources:
- Semantic Scholar API (free, no key required for basic access)
- CrossRef API (free, mailto for polite pool)
- OpenAlex API (free, no key required)

Architecture:
- Zero paid API dependencies
- Async HTTP via urllib (no requests/aiohttp needed)
- Disk cache for API responses (respects rate limits)
- Integrates with literature_verify.py for cross-checking
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import urllib.request
import urllib.error
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple
from pathlib import Path


# ============================================================
# Configuration
# ============================================================

SEMANTIC_SCHOLAR_API = "https://api.semanticscholar.org/graph/v1"
CROSSREF_API = "https://api.crossref.org/works"
OPENALEX_API = "https://api.openalex.org"

# Polite contact email for CrossRef/OpenAlex (faster rate limits)
_MAILTO = os.environ.get("SCHOLAR_AGENT_EMAIL", "scholar-agent@example.com")

# Cache directory
_CACHE_DIR = Path(os.environ.get(
    "CITATION_CACHE_DIR",
    os.path.join(os.path.dirname(__file__), "..", ".cache", "citation_graph")
))

# Rate limiting
_RATE_LIMIT_DELAY = 1.0  # seconds between API calls


# ============================================================
# Data Classes
# ============================================================

@dataclass
class PaperNode:
    """A node in the citation graph."""
    paper_id: str              # Semantic Scholar paper ID or DOI
    title: str
    authors: List[str]
    year: Optional[int]
    venue: Optional[str]
    doi: Optional[str]
    citation_count: int = 0
    reference_count: int = 0
    abstract: str = ""
    fields_of_study: List[str] = field(default_factory=list)
    source: str = "unknown"    # "semantic_scholar" | "crossref" | "openalex"

    @property
    def display_key(self) -> str:
        first_author = self.authors[0] if self.authors else "Unknown"
        return f"{first_author} ({self.year})"


@dataclass
class CitationEdge:
    """An edge in the citation graph (A cites B)."""
    citing_id: str
    cited_id: str
    context: str = ""          # Citation context sentence (if available)
    is_influential: bool = False


@dataclass
class CitationGraph:
    """The full citation graph structure."""
    root_paper: PaperNode
    nodes: Dict[str, PaperNode] = field(default_factory=dict)
    edges: List[CitationEdge] = field(default_factory=list)
    walk_depth: int = 0

    @property
    def node_count(self) -> int:
        return len(self.nodes)

    @property
    def edge_count(self) -> int:
        return len(self.edges)

    def get_references(self, paper_id: str) -> List[PaperNode]:
        """Get papers cited by the given paper."""
        cited_ids = {e.cited_id for e in self.edges if e.citing_id == paper_id}
        return [self.nodes[pid] for pid in cited_ids if pid in self.nodes]

    def get_citations(self, paper_id: str) -> List[PaperNode]:
        """Get papers that cite the given paper."""
        citing_ids = {e.citing_id for e in self.edges if e.cited_id == paper_id}
        return [self.nodes[pid] for pid in citing_ids if pid in self.nodes]

    def find_common_references(self, paper_a: str, paper_b: str) -> List[PaperNode]:
        """Find papers cited by both paper_a and paper_b."""
        refs_a = {e.cited_id for e in self.edges if e.citing_id == paper_a}
        refs_b = {e.cited_id for e in self.edges if e.citing_id == paper_b}
        common = refs_a & refs_b
        return [self.nodes[pid] for pid in common if pid in self.nodes]

    def most_cited_in_graph(self, top_n: int = 10) -> List[Tuple[PaperNode, int]]:
        """Find the most-cited papers within this subgraph."""
        cite_counts: Dict[str, int] = {}
        for edge in self.edges:
            cite_counts[edge.cited_id] = cite_counts.get(edge.cited_id, 0) + 1
        sorted_ids = sorted(cite_counts.items(), key=lambda x: x[1], reverse=True)[:top_n]
        return [
            (self.nodes[pid], count)
            for pid, count in sorted_ids
            if pid in self.nodes
        ]


@dataclass
class MissingReferenceReport:
    """Report of potentially missing key references."""
    paper: PaperNode
    reason: str                 # Why this paper should be cited
    relevance_score: float      # 0-1
    citation_count: int         # Global citation count
    shared_references: int      # How many refs it shares with the target


# ============================================================
# Disk Cache
# ============================================================

class _DiskCache:
    """Simple disk cache for API responses."""

    def __init__(self, cache_dir: Path, ttl_hours: int = 168):  # 1 week default
        self._dir = cache_dir
        self._ttl = ttl_hours * 3600
        self._dir.mkdir(parents=True, exist_ok=True)

    def _key_path(self, key: str) -> Path:
        h = hashlib.sha256(key.encode()).hexdigest()[:16]
        return self._dir / f"{h}.json"

    def get(self, key: str) -> Optional[Any]:
        path = self._key_path(key)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if time.time() - data.get("ts", 0) > self._ttl:
                path.unlink(missing_ok=True)
                return None
            return data.get("value")
        except (json.JSONDecodeError, OSError):
            return None

    def put(self, key: str, value: Any):
        path = self._key_path(key)
        try:
            path.write_text(
                json.dumps({"ts": time.time(), "value": value}, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            pass


_cache = _DiskCache(_CACHE_DIR)


# ============================================================
# API Clients
# ============================================================

def _http_get(url: str, headers: Optional[Dict[str, str]] = None) -> Optional[Dict]:
    """Synchronous HTTP GET with error handling."""
    req = urllib.request.Request(url)
    req.add_header("User-Agent", f"ScholarAgent/1.0 (mailto:{_MAILTO})")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, OSError):
        return None


async def _async_http_get(url: str, headers: Optional[Dict[str, str]] = None) -> Optional[Dict]:
    """Async wrapper around synchronous HTTP GET (runs in thread pool)."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _http_get, url, headers)


# ============================================================
# Semantic Scholar Client
# ============================================================

async def _s2_paper_details(paper_id: str) -> Optional[Dict]:
    """Fetch paper details from Semantic Scholar."""
    cache_key = f"s2:paper:{paper_id}"
    cached = _cache.get(cache_key)
    if cached:
        return cached

    fields = "paperId,title,authors,year,venue,doi,citationCount,referenceCount,abstract,fieldsOfStudy"
    url = f"{SEMANTIC_SCHOLAR_API}/paper/{urllib.parse.quote(paper_id, safe='')}?fields={fields}"
    data = await _async_http_get(url)
    if data:
        _cache.put(cache_key, data)
    await asyncio.sleep(_RATE_LIMIT_DELAY)
    return data


async def _s2_paper_references(paper_id: str, limit: int = 100) -> List[Dict]:
    """Fetch references (papers cited by this paper)."""
    cache_key = f"s2:refs:{paper_id}:{limit}"
    cached = _cache.get(cache_key)
    if cached:
        return cached

    fields = "paperId,title,authors,year,venue,doi,citationCount,isInfluential"
    url = (
        f"{SEMANTIC_SCHOLAR_API}/paper/{urllib.parse.quote(paper_id, safe='')}"
        f"/references?fields={fields}&limit={limit}"
    )
    data = await _async_http_get(url)
    results = data.get("data", []) if data else []
    if results:
        _cache.put(cache_key, results)
    await asyncio.sleep(_RATE_LIMIT_DELAY)
    return results


async def _s2_paper_citations(paper_id: str, limit: int = 100) -> List[Dict]:
    """Fetch citations (papers that cite this paper)."""
    cache_key = f"s2:cites:{paper_id}:{limit}"
    cached = _cache.get(cache_key)
    if cached:
        return cached

    fields = "paperId,title,authors,year,venue,doi,citationCount,isInfluential,contexts"
    url = (
        f"{SEMANTIC_SCHOLAR_API}/paper/{urllib.parse.quote(paper_id, safe='')}"
        f"/citations?fields={fields}&limit={limit}"
    )
    data = await _async_http_get(url)
    results = data.get("data", []) if data else []
    if results:
        _cache.put(cache_key, results)
    await asyncio.sleep(_RATE_LIMIT_DELAY)
    return results


async def _s2_search(query: str, limit: int = 10) -> List[Dict]:
    """Search for papers by title/keywords."""
    cache_key = f"s2:search:{query}:{limit}"
    cached = _cache.get(cache_key)
    if cached:
        return cached

    fields = "paperId,title,authors,year,venue,doi,citationCount"
    url = (
        f"{SEMANTIC_SCHOLAR_API}/paper/search"
        f"?query={urllib.parse.quote(query)}&fields={fields}&limit={limit}"
    )
    data = await _async_http_get(url)
    results = data.get("data", []) if data else []
    if results:
        _cache.put(cache_key, results)
    await asyncio.sleep(_RATE_LIMIT_DELAY)
    return results


# ============================================================
# Graph Building
# ============================================================

def _s2_to_node(data: Dict, source: str = "semantic_scholar") -> Optional[PaperNode]:
    """Convert Semantic Scholar API response to PaperNode."""
    if not data or not data.get("paperId"):
        return None
    authors = [a.get("name", "") for a in data.get("authors", [])]
    fos = [f.get("category", "") for f in data.get("fieldsOfStudy", []) or []]
    return PaperNode(
        paper_id=data["paperId"],
        title=data.get("title", ""),
        authors=authors,
        year=data.get("year"),
        venue=data.get("venue", ""),
        doi=data.get("doi"),
        citation_count=data.get("citationCount", 0),
        reference_count=data.get("referenceCount", 0),
        abstract=data.get("abstract", "") or "",
        fields_of_study=fos,
        source=source,
    )


async def build_citation_graph(
    paper_id: str,
    depth: int = 1,
    max_refs: int = 50,
    max_cites: int = 50,
    include_abstracts: bool = False,
) -> CitationGraph:
    """
    Build a citation graph centered on a paper.
    
    Args:
        paper_id: Semantic Scholar paper ID, DOI (with doi: prefix), or arXiv ID
        depth: How many hops to walk (1 = direct refs/cites only)
        max_refs: Max references to fetch per paper
        max_cites: Max citations to fetch per paper
        include_abstracts: Whether to fetch abstracts (slower)
    
    Returns:
        CitationGraph with nodes and edges populated
    """
    # Fetch root paper
    root_data = await _s2_paper_details(paper_id)
    if not root_data:
        # Try DOI search
        search_results = await _s2_search(paper_id, limit=1)
        if search_results:
            root_data = await _s2_paper_details(search_results[0]["paperId"])
    
    if not root_data:
        raise ValueError(f"Paper not found: {paper_id}")

    root_node = _s2_to_node(root_data)
    if not root_node:
        raise ValueError(f"Could not parse paper data for: {paper_id}")

    graph = CitationGraph(root_paper=root_node, walk_depth=depth)
    graph.nodes[root_node.paper_id] = root_node

    # BFS walk
    to_visit = [(root_node.paper_id, 0)]
    visited: Set[str] = {root_node.paper_id}

    while to_visit:
        current_id, current_depth = to_visit.pop(0)
        if current_depth >= depth:
            continue

        # Fetch references
        refs = await _s2_paper_references(current_id, limit=max_refs)
        for ref_entry in refs:
            cited_paper = ref_entry.get("citedPaper", {})
            if not cited_paper or not cited_paper.get("paperId"):
                continue
            node = _s2_to_node(cited_paper)
            if not node:
                continue
            graph.nodes[node.paper_id] = node
            graph.edges.append(CitationEdge(
                citing_id=current_id,
                cited_id=node.paper_id,
                is_influential=ref_entry.get("isInfluential", False),
            ))
            if node.paper_id not in visited and current_depth + 1 < depth:
                visited.add(node.paper_id)
                to_visit.append((node.paper_id, current_depth + 1))

        # Fetch citations (who cites this)
        cites = await _s2_paper_citations(current_id, limit=max_cites)
        for cite_entry in cites:
            citing_paper = cite_entry.get("citingPaper", {})
            if not citing_paper or not citing_paper.get("paperId"):
                continue
            node = _s2_to_node(citing_paper)
            if not node:
                continue
            graph.nodes[node.paper_id] = node
            contexts = cite_entry.get("contexts", []) or []
            context_str = contexts[0] if contexts else ""
            graph.edges.append(CitationEdge(
                citing_id=node.paper_id,
                cited_id=current_id,
                context=context_str,
                is_influential=cite_entry.get("isInfluential", False),
            ))
            if node.paper_id not in visited and current_depth + 1 < depth:
                visited.add(node.paper_id)
                to_visit.append((node.paper_id, current_depth + 1))

    return graph


# ============================================================
# Analysis Functions
# ============================================================

def find_missing_references(
    graph: CitationGraph,
    min_citation_count: int = 50,
    min_shared_refs: int = 2,
) -> List[MissingReferenceReport]:
    """
    Identify potentially missing key references.
    
    Heuristic: papers that are highly cited AND share references with
    the root paper but are NOT cited by the root paper.
    """
    root_id = graph.root_paper.paper_id
    root_refs = {e.cited_id for e in graph.edges if e.citing_id == root_id}

    reports = []
    for pid, node in graph.nodes.items():
        if pid == root_id or pid in root_refs:
            continue
        if node.citation_count < min_citation_count:
            continue

        # Count shared references with root
        node_refs = {e.cited_id for e in graph.edges if e.citing_id == pid}
        shared = len(root_refs & node_refs)
        if shared < min_shared_refs:
            continue

        # Calculate relevance score
        relevance = min(1.0, (shared / max(len(root_refs), 1)) * 2 + 
                       min(node.citation_count / 500, 0.5))

        reason = (
            f"Highly cited ({node.citation_count} citations), "
            f"shares {shared} references with your paper"
        )
        reports.append(MissingReferenceReport(
            paper=node,
            reason=reason,
            relevance_score=round(relevance, 2),
            citation_count=node.citation_count,
            shared_references=shared,
        ))

    # Sort by relevance
    reports.sort(key=lambda r: r.relevance_score, reverse=True)
    return reports[:20]


def find_citation_chains(
    graph: CitationGraph,
    source_id: str,
    target_id: str,
    max_depth: int = 3,
) -> List[List[str]]:
    """
    Find citation chains from source to target (source -> ... -> target).
    Returns list of paths (each path is a list of paper IDs).
    """
    if source_id == target_id:
        return [[source_id]]

    # Build adjacency (citing -> cited)
    adj: Dict[str, Set[str]] = {}
    for edge in graph.edges:
        adj.setdefault(edge.citing_id, set()).add(edge.cited_id)

    # BFS for paths
    paths = []
    queue: List[List[str]] = [[source_id]]

    while queue:
        path = queue.pop(0)
        if len(path) > max_depth + 1:
            continue
        current = path[-1]
        for neighbor in adj.get(current, set()):
            if neighbor in path:
                continue  # Avoid cycles
            new_path = path + [neighbor]
            if neighbor == target_id:
                paths.append(new_path)
            else:
                queue.append(new_path)

    return paths


def identify_influential_papers(graph: CitationGraph, top_n: int = 10) -> List[PaperNode]:
    """
    Identify the most influential papers in the citation neighborhood.
    Uses a combination of global citation count and local graph importance.
    """
    # Local importance: how many edges reference this paper within our graph
    local_cite_count: Dict[str, int] = {}
    for edge in graph.edges:
        local_cite_count[edge.cited_id] = local_cite_count.get(edge.cited_id, 0) + 1

    # Score = log(global_citations + 1) * local_in_degree
    import math
    scored: List[Tuple[float, str]] = []
    for pid, node in graph.nodes.items():
        if pid == graph.root_paper.paper_id:
            continue
        local = local_cite_count.get(pid, 0)
        score = math.log(node.citation_count + 1) * (local + 1)
        scored.append((score, pid))

    scored.sort(reverse=True)
    return [graph.nodes[pid] for _, pid in scored[:top_n]]


def get_field_coverage(graph: CitationGraph) -> Dict[str, int]:
    """Analyze which fields of study are represented in the citation graph."""
    field_counts: Dict[str, int] = {}
    for node in graph.nodes.values():
        for fos in node.fields_of_study:
            if fos:
                field_counts[fos] = field_counts.get(fos, 0) + 1
    return dict(sorted(field_counts.items(), key=lambda x: x[1], reverse=True))


# ============================================================
# Tool Interface (for ScholarAgent tool system)
# ============================================================

TOOLS = [
    {
        "name": "citation_graph_build",
        "description": (
            "Build a citation graph for a paper. Fetches references and citations "
            "from Semantic Scholar to map the paper's citation neighborhood. "
            "Returns graph statistics and key findings."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "paper_id": {
                    "type": "string",
                    "description": (
                        "Paper identifier: Semantic Scholar ID, DOI (e.g. '10.1234/...'), "
                        "or arXiv ID (e.g. 'arXiv:2301.00001')"
                    ),
                },
                "depth": {
                    "type": "integer",
                    "description": "Walk depth (1=direct only, 2=refs of refs). Default 1.",
                    "default": 1,
                },
                "max_refs": {
                    "type": "integer",
                    "description": "Max references to fetch per paper. Default 50.",
                    "default": 50,
                },
            },
            "required": ["paper_id"],
        },
    },
    {
        "name": "citation_graph_missing",
        "description": (
            "Find potentially missing key references that the paper should cite. "
            "Identifies highly-cited papers in the same subfield that share "
            "references with the target paper."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "paper_id": {
                    "type": "string",
                    "description": "Paper identifier to analyze.",
                },
                "min_citations": {
                    "type": "integer",
                    "description": "Minimum citation count threshold. Default 50.",
                    "default": 50,
                },
            },
            "required": ["paper_id"],
        },
    },
    {
        "name": "citation_graph_influential",
        "description": (
            "Identify the most influential papers in the citation neighborhood. "
            "Combines global citation count with local graph importance."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "paper_id": {
                    "type": "string",
                    "description": "Paper identifier to analyze.",
                },
                "top_n": {
                    "type": "integer",
                    "description": "Number of top papers to return. Default 10.",
                    "default": 10,
                },
            },
            "required": ["paper_id"],
        },
    },
]


# Store built graphs for reuse within a session
_session_graphs: Dict[str, CitationGraph] = {}


async def _handle_citation_graph_build(args: Dict) -> str:
    """Handle the citation_graph_build tool call."""
    paper_id = args["paper_id"]
    depth = args.get("depth", 1)
    max_refs = args.get("max_refs", 50)

    try:
        graph = await build_citation_graph(
            paper_id=paper_id,
            depth=depth,
            max_refs=max_refs,
            max_cites=max_refs,
        )
        _session_graphs[paper_id] = graph

        # Summary
        influential = identify_influential_papers(graph, top_n=5)
        fields = get_field_coverage(graph)

        result = {
            "status": "success",
            "root_paper": {
                "title": graph.root_paper.title,
                "authors": graph.root_paper.authors[:5],
                "year": graph.root_paper.year,
                "citation_count": graph.root_paper.citation_count,
            },
            "graph_stats": {
                "total_nodes": graph.node_count,
                "total_edges": graph.edge_count,
                "walk_depth": graph.walk_depth,
            },
            "top_influential": [
                {
                    "title": p.title,
                    "authors": p.authors[:3],
                    "year": p.year,
                    "citations": p.citation_count,
                }
                for p in influential
            ],
            "field_coverage": dict(list(fields.items())[:8]),
        }
        return json.dumps(result, ensure_ascii=False, indent=2)

    except ValueError as e:
        return json.dumps({"status": "error", "message": str(e)})
    except Exception as e:
        return json.dumps({"status": "error", "message": f"Unexpected: {type(e).__name__}: {e}"})


async def _handle_citation_graph_missing(args: Dict) -> str:
    """Handle the citation_graph_missing tool call."""
    paper_id = args["paper_id"]
    min_citations = args.get("min_citations", 50)

    # Build graph if not cached
    if paper_id not in _session_graphs:
        try:
            graph = await build_citation_graph(paper_id=paper_id, depth=1)
            _session_graphs[paper_id] = graph
        except Exception as e:
            return json.dumps({"status": "error", "message": str(e)})

    graph = _session_graphs[paper_id]
    reports = find_missing_references(graph, min_citation_count=min_citations)

    result = {
        "status": "success",
        "missing_references": [
            {
                "title": r.paper.title,
                "authors": r.paper.authors[:3],
                "year": r.paper.year,
                "citation_count": r.citation_count,
                "shared_references": r.shared_references,
                "relevance_score": r.relevance_score,
                "reason": r.reason,
            }
            for r in reports[:10]
        ],
        "total_candidates": len(reports),
    }
    return json.dumps(result, ensure_ascii=False, indent=2)


async def _handle_citation_graph_influential(args: Dict) -> str:
    """Handle the citation_graph_influential tool call."""
    paper_id = args["paper_id"]
    top_n = args.get("top_n", 10)

    # Build graph if not cached
    if paper_id not in _session_graphs:
        try:
            graph = await build_citation_graph(paper_id=paper_id, depth=1)
            _session_graphs[paper_id] = graph
        except Exception as e:
            return json.dumps({"status": "error", "message": str(e)})

    graph = _session_graphs[paper_id]
    influential = identify_influential_papers(graph, top_n=top_n)

    result = {
        "status": "success",
        "influential_papers": [
            {
                "title": p.title,
                "authors": p.authors[:3],
                "year": p.year,
                "venue": p.venue,
                "citation_count": p.citation_count,
                "doi": p.doi,
            }
            for p in influential
        ],
    }
    return json.dumps(result, ensure_ascii=False, indent=2)


# Handler dispatch
TOOL_HANDLERS = {
    "citation_graph_build": _handle_citation_graph_build,
    "citation_graph_missing": _handle_citation_graph_missing,
    "citation_graph_influential": _handle_citation_graph_influential,
}
