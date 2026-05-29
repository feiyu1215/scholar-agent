#!/usr/bin/env python3
"""
验证线 B：学习管道端到端贯通测试（策略 B 混合模式）。

利用真实 LLM 运行 13+ session，共享同一个 memory 目录，
观察进化管道每个环节是否真正工作。

使用 5 篇 PDF 论文循环投喂（重复使用以凑够 13 session），
验证各检查点的触发和状态变迁。

运行方式：
    cd v2/
    python3 evaluation/test_evolution_e2e.py

预计耗时：30-60 分钟（13 session × 2-5 min/session）
预计成本：~$5-10（每 session ~$0.5-1，使用 gpt-4.1）
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


# ============================================================
# Configuration
# ============================================================

TOTAL_SESSIONS = 13
PAPERS_DIR = Path(__file__).parent / "test_papers"
PAPER_FILES = sorted(PAPERS_DIR.glob("*.pdf"))  # Will cycle through available papers
SHARED_MEMORY_DIR = Path(__file__).parent / "evolution_test_memory"
REPORT_DIR = Path(__file__).parent / "reports"

# COLD_START_SESSION_THRESHOLD from meta_reflect.py (imported at check time)
COLD_START_THRESHOLD = 10


# ============================================================
# Session Metrics Snapshot
# ============================================================

@dataclass
class SessionSnapshot:
    """Metrics captured after each session."""
    session_id: int
    paper_file: str
    time_seconds: float = 0.0
    findings_count: int = 0
    
    # Memory state (Layer 3: procedures)
    procedures_count: int = 0
    procedures_with_evidence_ge_2: int = 0
    procedures_with_evidence_ge_3: int = 0
    anti_patterns_count: int = 0
    
    # V3 Hierarchical experiences
    session_experiences_v3_count: int = 0
    section_experiences_count: int = 0
    evolution_records_count: int = 0
    contrast_results_count: int = 0
    
    # Learned habits
    learned_habits_count: int = 0
    
    # MetaReflector state
    fast_reflect_triggered: bool = False
    deep_reflect_triggered: bool = False
    fast_reflect_alerts_count: int = 0
    last_fast_reflect_count: int = 0
    last_deep_reflect_count: int = 0
    
    # Maturity & combination
    maturity_levels: dict = field(default_factory=dict)
    combination_log_entries: int = 0
    evolution_stats_entries: int = 0
    
    # Agent stats
    loop_turns: int = 0
    total_tokens: int = 0
    
    # Reflection result
    reflection_stats: dict = field(default_factory=dict)
    
    # Error (if any)
    error: Optional[str] = None


# ============================================================
# Snapshot Extraction
# ============================================================

def capture_snapshot(
    session_id: int,
    paper_file: str,
    memory: MemoryStore,
    agent: Optional["ScholarAgent"] = None,
    elapsed: float = 0.0,
    reflection_stats: Optional[dict] = None,
    fast_triggered: bool = False,
    deep_triggered: bool = False,
    error: Optional[str] = None,
) -> SessionSnapshot:
    """Capture a complete snapshot of system state after a session."""
    state = memory.state
    
    # Count procedures by evidence
    procedures = state.procedures if hasattr(state, 'procedures') else []
    procs_ge_2 = sum(1 for p in procedures if getattr(p, 'evidence_count', 0) >= 2)
    procs_ge_3 = sum(1 for p in procedures if getattr(p, 'evidence_count', 0) >= 3)
    anti_count = sum(1 for p in procedures if getattr(p, 'category', '') == 'anti_pattern')
    
    # Learned habits (from evolution engine if agent available)
    learned_count = 0
    if agent:
        learned_count = len(agent.harness.evolution_engine._learned_habits)
    
    # Findings
    findings_count = 0
    if agent:
        findings_count = len(agent.get_findings())
    
    return SessionSnapshot(
        session_id=session_id,
        paper_file=str(paper_file),
        time_seconds=elapsed,
        findings_count=findings_count,
        procedures_count=len(procedures),
        procedures_with_evidence_ge_2=procs_ge_2,
        procedures_with_evidence_ge_3=procs_ge_3,
        anti_patterns_count=anti_count,
        session_experiences_v3_count=len(getattr(state, 'session_experiences_v3', [])),
        section_experiences_count=len(getattr(state, 'section_experiences', [])),
        evolution_records_count=len(getattr(state, 'evolution_records', [])),
        contrast_results_count=len(getattr(state, 'contrast_results', [])),
        learned_habits_count=learned_count,
        fast_reflect_triggered=fast_triggered,
        deep_reflect_triggered=deep_triggered,
        fast_reflect_alerts_count=len(getattr(state, 'fast_reflect_alerts', [])),
        last_fast_reflect_count=getattr(state, '_last_fast_reflect_count', 0),
        last_deep_reflect_count=getattr(state, '_last_deep_reflect_count', 0),
        maturity_levels=dict(getattr(state, 'maturity_levels', {})),
        combination_log_entries=len(getattr(state, 'combination_log', [])),
        evolution_stats_entries=len(getattr(state, 'evolution_stats', [])),
        loop_turns=agent.get_stats().get('loop_turns_total', 0) if agent else 0,
        total_tokens=agent.get_stats().get('total_tokens', 0) if agent else 0,
        reflection_stats=reflection_stats or {},
        error=error,
    )


# ============================================================
# Single Session Runner
# ============================================================

async def run_session(session_id: int, paper_file: Path) -> SessionSnapshot:
    """Run one complete session and return metrics snapshot."""
    print(f"\n{'━'*60}")
    print(f"  Session {session_id}/{TOTAL_SESSIONS} — {paper_file.name}")
    print(f"{'━'*60}")
    
    t0 = time.time()
    fast_triggered = False
    deep_triggered = False
    
    try:
        # Load shared memory (persisted across sessions)
        memory = MemoryStore(SHARED_MEMORY_DIR)
        memory.load()
        
        # Track pre-session state for trigger detection
        pre_fast_count = getattr(memory.state, '_last_fast_reflect_count', 0)
        pre_deep_count = getattr(memory.state, '_last_deep_reflect_count', 0)
        
        # Initialize agent with shared memory
        agent = ScholarAgent(
            paper_path=str(paper_file),
            model=os.environ.get("LLM_MODEL", "gpt-4.1"),
            verbose=False,
            max_loop_turns=35,  # Moderate budget per session
            token_budget=120_000,
            context_window=128_000,
            persona="scholar",
            enable_hdwm=True,
        )
        
        # Override memory to shared directory
        agent.harness._memory_dir = SHARED_MEMORY_DIR
        agent.harness.memory = memory
        # Re-initialize evolution engine with shared memory state
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
        
        elapsed_review = time.time() - t0
        print(f"  → Review done: {len(agent.get_findings())} findings in {elapsed_review:.1f}s")
        
        # End session with reflection (triggers evolution pipeline)
        reflection_stats = {}
        try:
            async def _llm_call(system: str, user: str, max_tokens: int) -> str:
                return await agent.client.chat(system, user, max_tokens=max_tokens)
            
            reflection_stats = await agent.harness.end_session_with_reflection(
                llm_call_fn=_llm_call,
                user_messages=["请仔细审阅这篇论文"],
            )
            print(f"  → Reflection: {reflection_stats}")
        except Exception as e:
            print(f"  → Reflection failed (non-fatal): {e}")
            agent.harness.end_session(user_messages=["请仔细审阅这篇论文"])
        
        # Detect trigger events
        post_fast_count = getattr(memory.state, '_last_fast_reflect_count', 0)
        post_deep_count = getattr(memory.state, '_last_deep_reflect_count', 0)
        fast_triggered = post_fast_count > pre_fast_count
        deep_triggered = post_deep_count > pre_deep_count
        
        if fast_triggered:
            print(f"  ★ FastReflector TRIGGERED (count: {pre_fast_count} → {post_fast_count})")
        if deep_triggered:
            print(f"  ★ DeepReflector TRIGGERED (count: {pre_deep_count} → {post_deep_count})")
        
        elapsed = time.time() - t0
        
        snapshot = capture_snapshot(
            session_id=session_id,
            paper_file=paper_file.name,
            memory=memory,
            agent=agent,
            elapsed=elapsed,
            reflection_stats=reflection_stats,
            fast_triggered=fast_triggered,
            deep_triggered=deep_triggered,
        )
        
        # Print key metrics
        print(f"  → procedures: {snapshot.procedures_count} "
              f"(≥2: {snapshot.procedures_with_evidence_ge_2}, ≥3: {snapshot.procedures_with_evidence_ge_3})")
        print(f"  → session_exp_v3: {snapshot.session_experiences_v3_count}, "
              f"evolution_records: {snapshot.evolution_records_count}")
        print(f"  → learned_habits: {snapshot.learned_habits_count}")
        print(f"  → Total time: {elapsed:.1f}s")
        
        return snapshot
        
    except Exception as e:
        elapsed = time.time() - t0
        import traceback
        traceback.print_exc()
        
        # Try to capture whatever state we can
        try:
            memory = MemoryStore(SHARED_MEMORY_DIR)
            memory.load()
            return capture_snapshot(
                session_id=session_id,
                paper_file=paper_file.name,
                memory=memory,
                elapsed=elapsed,
                error=str(e)[:300],
            )
        except Exception:
            return SessionSnapshot(
                session_id=session_id,
                paper_file=paper_file.name,
                time_seconds=elapsed,
                error=str(e)[:300],
            )


# ============================================================
# Checkpoint Verification
# ============================================================

def verify_checkpoints(snapshots: List[SessionSnapshot]) -> List[dict]:
    """Verify all 6 pipeline checkpoints from VERIFICATION_PLAN.md.
    
    Returns list of {checkpoint, expected, actual, passed, note}.
    """
    checks = []
    
    # Checkpoint 1: Session 3 后 procedures 数量 > 0
    if len(snapshots) >= 3:
        s3 = snapshots[2]  # 0-indexed
        checks.append({
            "checkpoint": "CP1: Session 3 后 procedures > 0",
            "expected": "> 0",
            "actual": s3.procedures_count,
            "passed": s3.procedures_count > 0,
            "note": "经验沉淀正常" if s3.procedures_count > 0 else "extract_procedural_patterns 未生效",
        })
    
    # Checkpoint 2: Session 5 后存在 evidence_count >= 2 的 pattern
    if len(snapshots) >= 5:
        s5 = snapshots[4]
        checks.append({
            "checkpoint": "CP2: Session 5 后存在 evidence≥2 的 procedure",
            "expected": "> 0",
            "actual": s5.procedures_with_evidence_ge_2,
            "passed": s5.procedures_with_evidence_ge_2 > 0,
            "note": "相似 pattern 正确合并" if s5.procedures_with_evidence_ge_2 > 0 else "_is_similar() 在真实数据上不工作",
        })
    
    # Checkpoint 3: Session 10 后 FastReflector 触发
    if len(snapshots) >= 10:
        # Check if Fast triggered in any session >=10
        fast_ever = any(s.fast_reflect_triggered for s in snapshots[9:])
        checks.append({
            "checkpoint": "CP3: Session 10+ FastReflector 触发",
            "expected": "True",
            "actual": fast_ever,
            "passed": fast_ever,
            "note": "冷启动守卫正确放行" if fast_ever else "COLD_START_SESSION_THRESHOLD 逻辑有误",
        })
    
    # Checkpoint 4: Session 11-12 DeepReflector 触发
    if len(snapshots) >= 11:
        deep_ever = any(s.deep_reflect_triggered for s in snapshots[9:])
        checks.append({
            "checkpoint": "CP4: Session 10+ DeepReflector 触发",
            "expected": "True",
            "actual": deep_ever,
            "passed": deep_ever,
            "note": "LLM 反思链路通畅" if deep_ever else "llm_call_fn 传递问题或 prompt 解析失败",
        })
    
    # Checkpoint 5: 任意 session 后 HabitLearner 有产出
    if len(snapshots) >= 10:
        any_learned = any(s.learned_habits_count > 0 for s in snapshots)
        checks.append({
            "checkpoint": "CP5: HabitLearner 产出 LearnedHabit",
            "expected": "True",
            "actual": any_learned,
            "passed": any_learned,
            "note": "习惯生成正常" if any_learned else "阈值太严或 pattern 数据不满足条件",
        })
    
    # Checkpoint 6: Session 13 findings_count vs Session 1
    if len(snapshots) >= 13:
        s1_findings = snapshots[0].findings_count
        s13_findings = snapshots[12].findings_count
        # 放宽判定：只要不退化即可（相同论文循环导致 findings 不一定增长）
        improved_or_stable = s13_findings >= s1_findings
        checks.append({
            "checkpoint": "CP6: Session 13 findings ≥ Session 1",
            "expected": f"≥ {s1_findings}",
            "actual": s13_findings,
            "passed": improved_or_stable,
            "note": ("系统未退化" if improved_or_stable 
                     else "进化产物质量问题或习惯无效"),
        })
    
    # Bonus: evolution_stats persisted
    if snapshots:
        last = snapshots[-1]
        checks.append({
            "checkpoint": "CP-Bonus: evolution_stats 有记录",
            "expected": "> 0",
            "actual": last.evolution_stats_entries,
            "passed": last.evolution_stats_entries > 0,
            "note": "record_session_stats() 正常工作" if last.evolution_stats_entries > 0 else "harness 未调用 record_session_stats()",
        })
    
    return checks


# ============================================================
# Report Generation
# ============================================================

def generate_report(snapshots: List[SessionSnapshot], checks: List[dict], total_time: float) -> str:
    """Generate human-readable markdown report."""
    lines = [
        "# 验证线 B：学习管道端到端贯通报告",
        f"\n生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"总耗时：{total_time:.1f}s ({total_time/60:.1f} min)",
        f"模型：{os.environ.get('LLM_MODEL', 'gpt-4.1')}",
        f"Sessions: {len(snapshots)}/{TOTAL_SESSIONS}",
        "",
        "## 检查点结果",
        "",
        "| 检查点 | 预期 | 实际 | 通过 | 说明 |",
        "|--------|------|------|------|------|",
    ]
    
    passed_count = 0
    for c in checks:
        status = "✅" if c["passed"] else "❌"
        if c["passed"]:
            passed_count += 1
        lines.append(f"| {c['checkpoint']} | {c['expected']} | {c['actual']} | {status} | {c['note']} |")
    
    lines.append(f"\n**总计：{passed_count}/{len(checks)} 通过**")
    
    # Evolution trajectory
    lines.append("\n## 进化轨迹")
    lines.append("")
    lines.append("| Session | Paper | Findings | Procedures | ≥3 Evidence | Learned | Fast | Deep | Time |")
    lines.append("|---------|-------|----------|------------|-------------|---------|------|------|------|")
    
    for s in snapshots:
        fast_mark = "★" if s.fast_reflect_triggered else ""
        deep_mark = "★" if s.deep_reflect_triggered else ""
        err_mark = " ⚠️" if s.error else ""
        lines.append(
            f"| {s.session_id} | {s.paper_file} | {s.findings_count} | "
            f"{s.procedures_count} | {s.procedures_with_evidence_ge_3} | "
            f"{s.learned_habits_count} | {fast_mark} | {deep_mark} | "
            f"{s.time_seconds:.0f}s{err_mark} |"
        )
    
    # Errors
    errors = [s for s in snapshots if s.error]
    if errors:
        lines.append("\n## 错误")
        for s in errors:
            lines.append(f"\n### Session {s.session_id}")
            lines.append(f"```\n{s.error}\n```")
    
    # Conclusion
    lines.append("\n## 结论")
    if passed_count == len(checks):
        lines.append("\n✅ **管道完全贯通。** 所有检查点通过。")
    elif passed_count >= len(checks) - 1:
        lines.append("\n⚠️ **管道基本贯通。** 大部分检查点通过，需关注未通过项。")
    else:
        lines.append("\n❌ **管道存在断裂。** 多个检查点未通过，需进一步排查。")
    
    return "\n".join(lines)


# ============================================================
# Main
# ============================================================

async def main():
    print("=" * 60)
    print("  验证线 B：学习管道端到端贯通测试")
    print(f"  Sessions: {TOTAL_SESSIONS}")
    print(f"  Papers: {[p.name for p in PAPER_FILES]}")
    print(f"  Memory: {SHARED_MEMORY_DIR}")
    print(f"  Model: {os.environ.get('LLM_MODEL', 'gpt-4.1')}")
    print("=" * 60)
    
    if not PAPER_FILES:
        print("ERROR: No PDF files found in test_papers/", file=sys.stderr)
        sys.exit(1)
    
    # Clean start: fresh memory directory
    if SHARED_MEMORY_DIR.exists():
        import shutil
        shutil.rmtree(SHARED_MEMORY_DIR)
    SHARED_MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    
    total_start = time.time()
    snapshots: List[SessionSnapshot] = []
    
    for i in range(TOTAL_SESSIONS):
        session_id = i + 1
        # Cycle through papers
        paper_file = PAPER_FILES[i % len(PAPER_FILES)]
        
        snapshot = await run_session(session_id, paper_file)
        snapshots.append(snapshot)
        
        # Incremental save (crash-safe)
        interim_path = REPORT_DIR / "evolution_pipeline_interim.json"
        interim_path.parent.mkdir(parents=True, exist_ok=True)
        interim_path.write_text(
            json.dumps([asdict(s) for s in snapshots], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        
        # Brief pause between sessions (rate limit courtesy)
        if session_id < TOTAL_SESSIONS:
            await asyncio.sleep(2)
    
    total_time = time.time() - total_start
    
    # Verify checkpoints
    checks = verify_checkpoints(snapshots)
    
    # Print checkpoint results
    print("\n" + "=" * 60)
    print("  CHECKPOINT RESULTS")
    print("=" * 60)
    for c in checks:
        status = "✅ PASS" if c["passed"] else "❌ FAIL"
        print(f"  {status} | {c['checkpoint']} | actual={c['actual']} | {c['note']}")
    
    passed = sum(1 for c in checks if c["passed"])
    print(f"\n  Total: {passed}/{len(checks)} passed")
    
    # Generate report
    report = generate_report(snapshots, checks, total_time)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = REPORT_DIR / f"evolution_pipeline_{timestamp}.md"
    report_path.write_text(report, encoding="utf-8")
    print(f"\n  Report: {report_path}")
    
    # Save full data
    data_path = REPORT_DIR / f"evolution_pipeline_{timestamp}.json"
    data_path.write_text(
        json.dumps([asdict(s) for s in snapshots], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"  Data: {data_path}")
    
    # Final verdict
    if passed == len(checks):
        print("\n  🎉 管道完全贯通！")
    else:
        print(f"\n  ⚠️  {len(checks) - passed} 个检查点未通过，需排查。")


if __name__ == "__main__":
    asyncio.run(main())
