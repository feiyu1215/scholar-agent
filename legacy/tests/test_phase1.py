#!/usr/bin/env python3
"""Phase 1: Zero-cost structural tests (no LLM calls)."""
import os, sys, json, asyncio, time, shutil
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")
os.environ["SCHOLAR_MAX_CONCURRENT"] = "1"
sys.path.insert(0, str(Path(__file__).parent.parent))

WORKSPACE = Path(__file__).parent.parent / ".workspace"
PAPER_PATH = str(Path(__file__).parent.parent / "examples" / "sample_paper.md")

def run():
    print("=" * 60)
    print("  Phase 1: Zero-cost Structural Tests")
    print("=" * 60)

    # Clean workspace
    if WORKSPACE.exists():
        shutil.rmtree(WORKSPACE)

    # Test 1: parse_paper
    print("\n[1/6] parse_paper...")
    from tools.paper_parser import parse_paper
    t0 = time.time()
    r = parse_paper(PAPER_PATH, str(WORKSPACE))
    print(f"  {(time.time()-t0)*1000:.0f}ms | {r[:120]}")
    assert "Error" not in r, f"FAIL: {r}"
    print("  ✅ PASS")

    # Load index for subsequent tests
    idx = json.loads((WORKSPACE / "paper" / "section_index.json").read_text())
    parts = [Path(e["file"]).read_text() for e in idx if Path(e["file"]).exists()]
    paper_text = "\n\n".join(parts)

    # Test 2: presubmission_check
    print("\n[2/6] presubmission_check...")
    from tools.presubmission_check import run_presubmission_checks, format_presubmission_report
    t0 = time.time()
    rpt = run_presubmission_checks(paper_text)
    formatted = format_presubmission_report(rpt)
    print(f"  {(time.time()-t0)*1000:.0f}ms | Verdict: {rpt.verdict}, {rpt.passed}/{rpt.total_checks} passed")
    assert rpt.total_checks > 0
    print("  ✅ PASS")

    # Test 3: architecture_diagnosis
    print("\n[3/6] architecture_diagnosis...")
    from tools.architecture_diagnosis import run_architecture_diagnosis, format_architecture_report
    t0 = time.time()
    arch = run_architecture_diagnosis(paper_text=paper_text)
    print(f"  {(time.time()-t0)*1000:.0f}ms | type={arch.paper_type}, issues={arch.total_issues}, hourglass={arch.hourglass_valid}")
    assert arch.paper_type is not None
    print("  ✅ PASS")

    # Test 4: voice_profile
    print("\n[4/6] build_voice_profile...")
    from utils.voice_profile import build_voice_profile_from_paper, get_voice_constraints
    t0 = time.time()
    fp = build_voice_profile_from_paper()
    constraints = get_voice_constraints(fp)
    print(f"  {(time.time()-t0)*1000:.0f}ms | {fp.total_words_analyzed}w analyzed, "
          f"avg_sent={fp.avg_sentence_length:.1f}, passive={fp.passive_ratio:.1%}")
    assert fp.total_words_analyzed > 0
    print(f"  Constraints preview: {constraints[:150]}")
    print("  ✅ PASS")

    # Test 5: citation analysis
    print("\n[5/6] citation_analysis (rule-based)...")
    from tools.literature_verify import (
        parse_references, extract_citation_claims,
        check_overclaim_in_citations, generate_content_accuracy_report,
    )
    t0 = time.time()
    cits = parse_references(paper_text)
    print(f"  {(time.time()-t0)*1000:.0f}ms | {len(cits)} references parsed")
    if cits:
        claims = extract_citation_claims(paper_text, cits)
        print(f"  {len(claims)} citation claims extracted")
        if claims:
            overclaim = check_overclaim_in_citations(claims)
            report = generate_content_accuracy_report(overclaim)
            print(f"  Overclaim report: {len(report)} chars")
    print("  ✅ PASS")

    # Test 6: section operations
    print("\n[6/6] section_ops (read_section_index, read_section, consistency_check)...")
    from tools.section_ops import read_section_index, read_section, consistency_check
    t0 = time.time()
    idx_str = read_section_index()
    assert "Error" not in idx_str, f"FAIL: {idx_str}"
    sec_content = read_section(idx[0]["id"])
    assert "Error" not in sec_content, f"FAIL: {sec_content}"
    con = consistency_check()
    print(f"  {(time.time()-t0)*1000:.0f}ms | index={len(idx_str)}c, "
          f"section[0]={len(sec_content)}c, consistency={len(con)}c")
    print("  ✅ PASS")

    print("\n" + "=" * 60)
    print("  ALL 6 PHASE-1 TESTS PASSED ✅")
    print("=" * 60)


if __name__ == "__main__":
    run()
