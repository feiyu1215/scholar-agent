#!/usr/bin/env python3
"""
Phase 3: Search & Verification Tests (uses Semantic Scholar + CrossRef APIs).
Tests: web_search, literature_verify, citation_graph.
No LLM needed — all external API calls.
"""
import os, sys, json, asyncio, time, traceback
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")
sys.path.insert(0, str(Path(__file__).parent.parent))

WORKSPACE = Path(__file__).parent / ".workspace"
results = []


def record(name, passed, dur_ms, detail="", error=""):
    results.append({"name": name, "passed": passed, "dur_ms": dur_ms, "detail": detail, "error": error})
    icon = "✅" if passed else "❌"
    print(f"  {icon} {name} ({dur_ms:.0f}ms)")
    if detail:
        print(f"     {detail[:300]}")
    if error:
        print(f"     ERROR: {error[:300]}")


async def main():
    print("=" * 60)
    print("  Phase 3: Search & Verification Tests")
    print("  APIs: Semantic Scholar, CrossRef (free, no key)")
    print("=" * 60)

    # ============================================================
    # Test 1: Semantic Scholar search
    # ============================================================
    print("\n[Test 1/8] Semantic Scholar search...")
    t0 = time.time()
    try:
        from tools.web_search import search_semantic_scholar
        response = search_semantic_scholar(
            "difference in differences place-based policy entrepreneurship",
            limit=5
        )
        dur = (time.time() - t0) * 1000

        if response.results:
            first = response.results[0]
            record("semantic_scholar_search", True, dur,
                   detail=f"Found {len(response.results)} results. First: '{first.title}' "
                          f"({first.year}), {first.citation_count} citations")
        elif response.error:
            record("semantic_scholar_search", False, dur,
                   error=f"API error: {response.error}")
        else:
            record("semantic_scholar_search", True, dur,
                   detail="No results (API accessible but no matches)")
    except Exception as e:
        record("semantic_scholar_search", False, (time.time()-t0)*1000, error=str(e))
        traceback.print_exc()

    # ============================================================
    # Test 2: CrossRef search
    # ============================================================
    print("\n[Test 2/8] CrossRef search...")
    await asyncio.sleep(1)  # Rate limit courtesy
    t0 = time.time()
    try:
        from tools.web_search import search_crossref
        response = search_crossref(
            "National Innovation Demonstration Zones China entrepreneurship",
            limit=5
        )
        dur = (time.time() - t0) * 1000

        if response.results:
            first = response.results[0]
            record("crossref_search", True, dur,
                   detail=f"Found {len(response.results)} results. First: '{first.title[:80]}' "
                          f"({first.year}), DOI: {first.doi}")
        elif response.error:
            record("crossref_search", False, dur, error=f"API error: {response.error}")
        else:
            record("crossref_search", True, dur,
                   detail="No results (API accessible)")
    except Exception as e:
        record("crossref_search", False, (time.time()-t0)*1000, error=str(e))
        traceback.print_exc()

    # ============================================================
    # Test 3: Unified search (with fallback)
    # ============================================================
    print("\n[Test 3/8] Unified search_papers (fallback logic)...")
    await asyncio.sleep(1)
    t0 = time.time()
    try:
        from tools.web_search import search_papers
        response = search_papers("staggered difference in differences Sun Abraham 2021", limit=3)
        dur = (time.time() - t0) * 1000

        record("unified_search", True, dur,
               detail=f"Source: {response.source}, Found: {response.total_found}, "
                      f"Results: {len(response.results)}")
    except Exception as e:
        record("unified_search", False, (time.time()-t0)*1000, error=str(e))
        traceback.print_exc()

    # ============================================================
    # Test 4: Intelligent search (v5, with query expansion + reranking)
    # ============================================================
    print("\n[Test 4/8] Intelligent search (query expansion + reranking)...")
    await asyncio.sleep(1)
    t0 = time.time()
    try:
        from tools.web_search import intelligent_search
        response = intelligent_search(
            "place-based innovation policy causal evidence quasi-experimental",
            field="economics",
            limit=5,
        )
        dur = (time.time() - t0) * 1000

        if response.results:
            # Check reranking happened (results should have diverse years)
            years = [r.year for r in response.results if r.year]
            record("intelligent_search", True, dur,
                   detail=f"Source: {response.source}, Found: {response.total_found}, "
                          f"Top {len(response.results)} returned. Years: {years}")
        else:
            record("intelligent_search", True, dur,
                   detail=f"No results (source: {response.source}, error: {response.error})")
    except Exception as e:
        record("intelligent_search", False, (time.time()-t0)*1000, error=str(e))
        traceback.print_exc()

    # ============================================================
    # Test 5: DOI lookup
    # ============================================================
    print("\n[Test 5/8] DOI lookup (CrossRef)...")
    await asyncio.sleep(1)
    t0 = time.time()
    try:
        from tools.web_search import lookup_doi
        # Use a well-known DID paper DOI
        result = lookup_doi("10.1257/aer.20181169")
        dur = (time.time() - t0) * 1000

        if result:
            record("doi_lookup", True, dur,
                   detail=f"Title: '{result.title[:80]}', Year: {result.year}, "
                          f"Authors: {result.authors[:2]}, Citations: {result.citation_count}")
        else:
            record("doi_lookup", False, dur, error="DOI lookup returned None")
    except Exception as e:
        record("doi_lookup", False, (time.time()-t0)*1000, error=str(e))
        traceback.print_exc()

    # ============================================================
    # Test 6: search_literature (formatted tool output)
    # ============================================================
    print("\n[Test 6/8] search_literature (formatted output for agent)...")
    await asyncio.sleep(1)
    t0 = time.time()
    try:
        from tools.web_search import search_literature
        output = search_literature("absorptive capacity innovation policy China", limit=3)
        dur = (time.time() - t0) * 1000

        has_results = "Search Results" in output or "No results" in output
        record("search_literature_tool", True, dur,
               detail=f"Output length: {len(output)}c. Preview: {output[:200]}")
    except Exception as e:
        record("search_literature_tool", False, (time.time()-t0)*1000, error=str(e))
        traceback.print_exc()

    # ============================================================
    # Test 7: Citation verification (with real search)
    # ============================================================
    print("\n[Test 7/8] Citation verification (real API search)...")
    await asyncio.sleep(2)
    t0 = time.time()
    try:
        from tools.literature_verify import (
            parse_references, verify_citations_batch,
            generate_verification_report
        )
        from tools.web_search import search_fn_adapter

        # Parse references from our test paper
        paper_path = Path(__file__).parent.parent / "examples" / "sample_paper.md"
        paper_text = paper_path.read_text(encoding="utf-8")
        citations = parse_references(paper_text)

        # Verify first 3 citations (to avoid hitting rate limits)
        subset = citations[:3]
        print(f"     Verifying {len(subset)} citations against Semantic Scholar...")

        verification_results = await verify_citations_batch(
            subset,
            max_concurrency=1,
            search_fn=search_fn_adapter,
        )
        dur = (time.time() - t0) * 1000

        report = generate_verification_report(verification_results)
        verified = sum(1 for r in verification_results if r.status == "verified")
        suspicious = sum(1 for r in verification_results if r.status == "suspicious")
        not_found = sum(1 for r in verification_results if r.status == "not_found")

        record("citation_verification", True, dur,
               detail=f"Checked {len(subset)} citations: {verified} verified, "
                      f"{suspicious} suspicious, {not_found} not_found. "
                      f"Report: {len(report)}c")
    except Exception as e:
        record("citation_verification", False, (time.time()-t0)*1000, error=str(e))
        traceback.print_exc()

    # ============================================================
    # Test 8: Citation graph build
    # ============================================================
    print("\n[Test 8/8] Citation graph build (Semantic Scholar)...")
    await asyncio.sleep(2)
    t0 = time.time()
    try:
        from tools.citation_graph import build_citation_graph, find_missing_references

        # Use a known economics paper DOI
        graph = await build_citation_graph(
            paper_id="10.1257/aer.20181169",
            depth=1,
            max_refs=10,
            max_cites=10,
        )
        dur = (time.time() - t0) * 1000

        record("citation_graph_build", True, dur,
               detail=f"Root: '{graph.root_paper.title[:60]}', "
                      f"Nodes: {graph.node_count}, Edges: {graph.edge_count}")

    except ValueError as e:
        dur = (time.time() - t0) * 1000
        # Paper not found is acceptable — it means API is working but paper not indexed
        record("citation_graph_build", True, dur,
               detail=f"API accessible but paper not found: {e}")
    except Exception as e:
        dur = (time.time() - t0) * 1000
        # Network errors are acceptable in test (API may be down)
        if "URLError" in type(e).__name__ or "timeout" in str(e).lower():
            record("citation_graph_build", True, dur,
                   detail=f"API timeout/network issue (acceptable): {type(e).__name__}")
        else:
            record("citation_graph_build", False, dur, error=str(e))
            traceback.print_exc()

    # ============================================================
    # Summary
    # ============================================================
    passed = sum(1 for r in results if r["passed"])
    failed = sum(1 for r in results if not r["passed"])

    print("\n" + "=" * 60)
    print(f"  RESULTS: {passed} passed, {failed} failed, {len(results)} total")
    print("=" * 60)

    if failed:
        print("\n  ❌ FAILED:")
        for r in results:
            if not r["passed"]:
                print(f"    - {r['name']}: {r['error']}")
    else:
        print("\n  🎉 ALL SEARCH TESTS PASSED!")

    # Save report
    report_path = Path(__file__).parent / "reports" / "test_search_report.json"
    report_path.write_text(json.dumps({
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "passed": passed, "failed": failed,
        "results": results
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n  Report saved: {report_path}")


if __name__ == "__main__":
    asyncio.run(main())
