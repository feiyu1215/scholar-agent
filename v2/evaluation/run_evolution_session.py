#!/usr/bin/env python3
"""Run a SINGLE evolution session (for Verification Line B).

Usage:
    python3 evaluation/run_evolution_session.py <session_number> [total_sessions]
    
This runs one session of the 13-session evolution pipeline test.
Each session shares memory in evaluation/evolution_test_memory/.
Results are appended to evaluation/reports/evolution_pipeline_interim.json.

Example: Run sessions 1 through 13 sequentially
    for i in $(seq 1 13); do python3 evaluation/run_evolution_session.py $i; done
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional, List, Dict

# Setup
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from core.agent import ScholarAgent
from core.memory import MemoryStore

PAPERS_DIR = Path(__file__).parent / "test_papers"
PAPER_FILES = sorted(PAPERS_DIR.glob("*.pdf"))
SHARED_MEMORY_DIR = Path(__file__).parent / "evolution_test_memory"
REPORT_DIR = Path(__file__).parent / "reports"
INTERIM_FILE = REPORT_DIR / "evolution_pipeline_interim.json"


async def run_session(session_id: int, paper_file: Path) -> dict:
    """Run one complete session and return metrics dict."""
    print(f"Session {session_id} | Paper: {paper_file.name}", flush=True)
    
    t0 = time.time()
    
    try:
        # Load shared memory
        SHARED_MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        memory = MemoryStore(SHARED_MEMORY_DIR)
        memory.load()
        
        # Pre-session state
        pre_procs = len(memory.state.procedures) if hasattr(memory.state, 'procedures') else 0
        
        # Initialize agent
        agent = ScholarAgent(
            paper_path=str(paper_file),
            model=os.environ.get("LLM_MODEL", "gpt-4.1"),
            verbose=False,
            max_loop_turns=35,
            token_budget=120_000,
            context_window=128_000,
            persona="scholar",
            enable_hdwm=True,
        )
        
        # Override memory to shared directory
        agent.harness._memory_dir = SHARED_MEMORY_DIR
        agent.harness.memory = memory
        
        # Re-init evolution engine with shared memory
        from core.evolution import EvolutionEngine
        agent.harness.evolution_engine = EvolutionEngine(memory)
        from core.habits import COGNITIVE_HABITS
        existing_habit_ids = {h.id for h in COGNITIVE_HABITS}
        agent.harness.evolution_engine.initialize(
            existing_habit_ids,
            paper_sections=agent.harness.state.paper_sections or None,
        )
        
        # Run review
        output = await agent.start(
            "请仔细审阅这篇论文，找出方法论、数据、逻辑、引用和写作方面的问题。"
        )
        
        findings = agent.get_findings()
        stats = agent.get_stats()
        elapsed_review = time.time() - t0
        print(f"  Review: {len(findings)} findings in {elapsed_review:.1f}s", flush=True)
        
        # End session with reflection (evolution pipeline trigger)
        reflection_stats = {}
        try:
            reflection_stats = await agent.end_session_with_reflection()
            print(f"  Reflection OK", flush=True)
        except Exception as e:
            print(f"  Reflection failed: {e}", flush=True)
            agent.end_session()
        
        # Capture post-session state
        memory_post = MemoryStore(SHARED_MEMORY_DIR)
        memory_post.load()
        state = memory_post.state
        
        procedures = state.procedures if hasattr(state, 'procedures') else []
        procs_ge_2 = sum(1 for p in procedures if getattr(p, 'evidence_count', 0) >= 2)
        procs_ge_3 = sum(1 for p in procedures if getattr(p, 'evidence_count', 0) >= 3)
        
        learned_count = len(agent.harness.evolution_engine._learned_habits)
        
        elapsed = time.time() - t0
        
        result = {
            "session_id": session_id,
            "paper_file": paper_file.name,
            "status": "OK",
            "time_seconds": round(elapsed, 1),
            "findings_count": len(findings),
            "procedures_count": len(procedures),
            "procedures_ge_2": procs_ge_2,
            "procedures_ge_3": procs_ge_3,
            "learned_habits_count": learned_count,
            "loop_turns": stats.get("loop_turns_total", 0),
            "total_tokens": stats.get("total_tokens", 0),
            "reflection": reflection_stats,
            "evolution_stats_count": len(getattr(state, 'evolution_stats', [])),
            "session_experiences_v3_count": len(getattr(state, 'session_experiences_v3', [])),
            "error": None,
        }
        
        print(f"  Procs: {pre_procs} -> {len(procedures)} (ge2={procs_ge_2}, ge3={procs_ge_3})")
        print(f"  Learned habits: {learned_count}")
        print(f"  Total: {elapsed:.1f}s", flush=True)
        
        return result
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        elapsed = time.time() - t0
        return {
            "session_id": session_id,
            "paper_file": paper_file.name,
            "status": "FAILED",
            "time_seconds": round(elapsed, 1),
            "findings_count": 0,
            "procedures_count": 0,
            "procedures_ge_2": 0,
            "procedures_ge_3": 0,
            "learned_habits_count": 0,
            "loop_turns": 0,
            "total_tokens": 0,
            "reflection": {},
            "evolution_stats_count": 0,
            "session_experiences_v3_count": 0,
            "error": str(e)[:500],
        }


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 run_evolution_session.py <session_number> [total_sessions]")
        sys.exit(1)
    
    session_id = int(sys.argv[1])
    total = int(sys.argv[2]) if len(sys.argv) > 2 else 13
    
    if not PAPER_FILES:
        print("ERROR: No PDF files in test_papers/")
        sys.exit(1)
    
    # If session 1, clean memory dir
    if session_id == 1 and SHARED_MEMORY_DIR.exists():
        import shutil
        shutil.rmtree(SHARED_MEMORY_DIR)
        print("Cleaned shared memory directory (fresh start)")
    
    # Cycle through papers
    paper_file = PAPER_FILES[(session_id - 1) % len(PAPER_FILES)]
    
    # Run
    result = asyncio.run(run_session(session_id, paper_file))
    
    # Append to interim file
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    existing = []
    if INTERIM_FILE.exists():
        existing = json.loads(INTERIM_FILE.read_text(encoding="utf-8"))
    existing.append(result)
    INTERIM_FILE.write_text(
        json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    
    print(f"\nSession {session_id}/{total} complete. Results in {INTERIM_FILE}")
    
    # Quick checkpoint status
    if session_id >= 3:
        s3 = next((s for s in existing if s["session_id"] == 3), None)
        if s3:
            print(f"  CP1 (S3 procs>0): {'PASS' if s3['procedures_count'] > 0 else 'FAIL'} (actual={s3['procedures_count']})")
    if session_id >= 5:
        s5 = next((s for s in existing if s["session_id"] == 5), None)
        if s5:
            print(f"  CP2 (S5 ge2>0): {'PASS' if s5['procedures_ge_2'] > 0 else 'FAIL'} (actual={s5['procedures_ge_2']})")


if __name__ == "__main__":
    main()
