#!/usr/bin/env python3
"""
experiment_g005_isolation.py — 隔离实验：验证修复后的 sub-agent 能否命中 G005

实验设计：
    G005 = "Table A.3 与 Table A.4 数据完全重复"
    
    根因诊断（Phase 2 结论）：
    1. boundary_guard `[:4]` 截断导致 data_consistency_reviewer 未被 spawn
    2. MCL static fallback 将 data_consistency_auditor 路由到 tier=low (gpt-4.1-mini)
    3. 子 agent 的 question 太泛（"跨表数值交叉验证"），不够具体
    
    本实验直接调用 sub-agent 逻辑，绕过 boundary_guard 调度，
    测试三种配置下 sub-agent 能否发现 G005：
    
    Config A (baseline): tier=low, 泛化 question, max_turns=8
    Config B (fix model): tier=high, 泛化 question, max_turns=12
    Config C (full fix):  tier=high, 精确 question, max_turns=12
    
    预期：
    - Config A: 大概率 miss（复现当前行为）
    - Config B: 可能 hit（模型能力足够但 question 不够聚焦）
    - Config C: 高概率 hit（模型+question+budget 全部到位）

用法：
    cd v2/
    python3 -m evaluation.experiment_g005_isolation
    
    # 只跑某个 config:
    python3 -m evaluation.experiment_g005_isolation --config C
    
    # 使用不同模型:
    python3 -m evaluation.experiment_g005_isolation --model-high gpt-4.1 --model-low gpt-4.1-mini

输出：
    evaluation/reports/experiment_g005_<timestamp>.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# Ensure v2/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Load .env
_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())


# ============================================================
# Experiment Configurations
# ============================================================

CONFIGS = {
    "A": {
        "name": "baseline (tier=low, vague question, 8 turns)",
        "model_tier": "low",
        "max_turns": 8,
        "question": "跨表数值交叉验证：同一统计量在不同表中是否一致",
        "lens": "data_consistency_auditor",
        "focus": "full",
    },
    "B": {
        "name": "fix model (tier=high, vague question, 12 turns)",
        "model_tier": "high",
        "max_turns": 12,
        "question": "跨表数值交叉验证：同一统计量在不同表中是否一致",
        "lens": "data_consistency_auditor",
        "focus": "full",
    },
    "C": {
        "name": "full fix (tier=high, precise question, 12 turns)",
        "model_tier": "high",
        "max_turns": 12,
        "question": (
            "逐表比对所有 Appendix 表格的数据：对于每对相邻表格（如 Table A.3 vs A.4），"
            "逐行检查均值、标准差、p值是否存在不合理的重复或完全相同的情况。"
            "特别注意：如果两个声称不同处理组的表格数据完全一致，这是严重的制表错误。"
            "你需要用 read_section 读取包含表格的 section，然后逐行比对数值。"
        ),
        "lens": "data_consistency_auditor",
        "focus": "full",
    },
}


# ============================================================
# G005 Hit Detection
# ============================================================

G005_KEYWORDS = [
    "table a.3",
    "table a.4",
    "a3",
    "a4",
    "完全重复",
    "完全一致",
    "identical",
    "duplicate",
    "same data",
    "相同数据",
    "制表错误",
    "copy",
    "复制",
]

def check_g005_hit(findings: list[dict]) -> tuple[bool, str]:
    """检查 findings 中是否命中 G005。
    
    Returns:
        (hit, evidence_text)
    """
    for f in findings:
        text = (f.get("finding", "") + " " + f.get("evidence", "")).lower()
        # 需要同时提到两个表 + 重复/一致
        mentions_tables = (
            ("a.3" in text or "a3" in text or "table 3" in text) and
            ("a.4" in text or "a4" in text or "table 4" in text)
        ) or ("appendix" in text and ("重复" in text or "一致" in text or "identical" in text or "duplicate" in text))
        
        mentions_duplication = any(kw in text for kw in [
            "完全重复", "完全一致", "identical", "duplicate", "same data",
            "相同数据", "制表错误", "copy", "复制", "一模一样",
            "数据重复", "完全相同",
        ])
        
        if mentions_tables and mentions_duplication:
            return True, f.get("finding", "")[:200]
    
    return False, ""


# ============================================================
# Run Single Experiment
# ============================================================

async def run_single_config(
    config_name: str,
    config: dict,
    model_high: str,
    model_low: str,
    verbose: bool = True,
) -> dict:
    """运行单个配置的实验。"""
    from llm.client import LLMClient
    from core.harness import Harness
    from core.loop import cognitive_loop, LoopDone, LoopDoomStop
    from core.identity import SUB_PERSPECTIVE_TOOLS, build_sub_perspective_prompt
    
    print(f"\n{'='*60}")
    print(f"  Config {config_name}: {config['name']}")
    print(f"{'='*60}")
    
    # 选择模型
    model_map = {"high": model_high, "medium": model_high, "low": model_low}
    model = model_map[config["model_tier"]]
    print(f"  Model: {model} (tier={config['model_tier']})")
    print(f"  Max turns: {config['max_turns']}")
    print(f"  Question: {config['question'][:80]}...")
    
    # 创建 LLM client
    client = LLMClient(model=model)
    
    # 创建 Harness（模拟 create_sub_harness 的行为）
    from core.phases import Phase
    harness = Harness(max_loop_turns=config["max_turns"])
    harness._paper_loaded = True
    
    # 继承 deep_review 阶段（子 agent 通常在此阶段被 spawn）
    harness.phase_fsm._state.current = Phase.DEEP_REVIEW
    
    # 加载论文数据（只加载包含表格的 refs 文件，模拟 focus="full" 时的行为）
    test_paper_dir = Path(__file__).parent / "test_papers"
    workspace_dir = test_paper_dir / ".workspace"
    refs_dir = workspace_dir / "refs"
    
    paper_sections = {}
    if refs_dir.exists():
        # 加载所有 refs（模拟 create_sub_harness focus="full" 的 fallback 行为）
        for ref_file in sorted(refs_dir.glob("*.md")):
            content = ref_file.read_text(encoding="utf-8")
            section_name = ref_file.stem
            paper_sections[section_name] = content
    
    if not paper_sections:
        print("  ❌ ERROR: No paper sections found!")
        return {"config": config_name, "error": "no_data"}
    
    print(f"  Loaded {len(paper_sections)} sections")
    
    # 设置 harness state
    harness.state.paper_sections = paper_sections
    harness.state.paper_title = "Intrahousehold Externalities and Water Conservation"
    
    # 构建 sub-perspective prompt
    workspace_state = harness.format_context()
    system_prompt = build_sub_perspective_prompt(
        lens=config["lens"],
        focus=config["focus"],
        question=config["question"],
        workspace_state=workspace_state,
    )
    
    # 构建 messages
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"请开始审视。关注: {config['focus']}。问题: {config['question']}"},
    ]
    
    # 运行 cognitive loop
    start_time = time.time()
    
    result = await cognitive_loop(
        messages=messages,
        harness=harness,
        tools=SUB_PERSPECTIVE_TOOLS,
        client=client,
        verbose=verbose,
    )
    
    elapsed = time.time() - start_time
    
    # 提取结果
    findings = harness.state.findings
    summary = ""
    if isinstance(result, LoopDone):
        summary = result.summary or ""
    elif isinstance(result, LoopDoomStop):
        summary = f"(Doom stop: {result.reason})"
    
    # 检查是否命中 G005
    hit, hit_evidence = check_g005_hit(findings)
    
    # 输出结果
    print(f"\n  --- Results ---")
    print(f"  Turns used: {harness.state.loop_turns}")
    print(f"  Tokens used: {harness.state.total_tokens}")
    print(f"  Findings: {len(findings)}")
    print(f"  Time: {elapsed:.1f}s")
    print(f"  G005 HIT: {'✅ YES' if hit else '❌ NO'}")
    if hit:
        print(f"  Evidence: {hit_evidence[:150]}")
    
    # 打印所有 findings 摘要
    if findings:
        print(f"\n  All findings:")
        for i, f in enumerate(findings, 1):
            priority = f.get("priority", "?")
            text = f.get("finding", "")[:120]
            print(f"    [{i}] ({priority}) {text}")
    
    return {
        "config": config_name,
        "config_detail": config,
        "model_used": model,
        "turns_used": harness.state.loop_turns,
        "tokens_used": harness.state.total_tokens,
        "elapsed_seconds": round(elapsed, 1),
        "findings_count": len(findings),
        "findings": findings,
        "summary": summary,
        "g005_hit": hit,
        "g005_evidence": hit_evidence,
    }


# ============================================================
# Main
# ============================================================

async def main():
    parser = argparse.ArgumentParser(description="G005 isolation experiment")
    parser.add_argument("--config", choices=["A", "B", "C", "all"], default="all",
                        help="Which config to run (default: all)")
    parser.add_argument("--model-high", default=os.environ.get("LLM_MODEL_HIGH", "gpt-4.1"),
                        help="Model for tier=high")
    parser.add_argument("--model-low", default=os.environ.get("LLM_MODEL_LOW", "gpt-4.1-mini"),
                        help="Model for tier=low")
    parser.add_argument("--quiet", action="store_true", help="Suppress verbose loop output")
    args = parser.parse_args()
    
    configs_to_run = list(CONFIGS.keys()) if args.config == "all" else [args.config]
    
    print(f"🧪 G005 Isolation Experiment")
    print(f"   Models: high={args.model_high}, low={args.model_low}")
    print(f"   Configs: {configs_to_run}")
    print(f"   Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    results = []
    for config_name in configs_to_run:
        config = CONFIGS[config_name]
        result = await run_single_config(
            config_name=config_name,
            config=config,
            model_high=args.model_high,
            model_low=args.model_low,
            verbose=not args.quiet,
        )
        results.append(result)
    
    # 汇总
    print(f"\n\n{'='*60}")
    print(f"  EXPERIMENT SUMMARY")
    print(f"{'='*60}")
    for r in results:
        hit_icon = "✅" if r["g005_hit"] else "❌"
        print(f"  Config {r['config']}: {hit_icon} G005={'HIT' if r['g005_hit'] else 'MISS'} "
              f"| {r['findings_count']} findings | {r['turns_used']} turns | {r['tokens_used']} tokens | {r['elapsed_seconds']}s")
    
    # 保存报告
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = REPORTS_DIR / f"experiment_g005_{timestamp}.json"
    
    report = {
        "experiment": "g005_isolation",
        "timestamp": timestamp,
        "models": {"high": args.model_high, "low": args.model_low},
        "results": results,
        "conclusion": {
            "configs_run": configs_to_run,
            "hits": [r["config"] for r in results if r["g005_hit"]],
            "misses": [r["config"] for r in results if not r["g005_hit"]],
        },
    }
    
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  Report saved: {report_path}")


if __name__ == "__main__":
    asyncio.run(main())
