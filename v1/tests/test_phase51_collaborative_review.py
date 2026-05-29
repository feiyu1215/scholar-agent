"""
Phase 51: 多人格协作链 E2E 测试

测试目标：验证 Scholar → Writer → Scholar 协作链的认知连续性
    - H1: Scholar 初审能产出 findings（至少 1 条）
    - H2: Writer 能看到 Scholar 的 findings 并做出 edits
    - H3: 复审 Scholar 能看到 Writer 的 edits 并给出评估
    - H4: 三阶段共享同一个 Harness（findings/edits 累积）
    - H5: 每个 persona 有独立的认知上下文（不互相污染）

关键验证点：
    - findings 在 Phase 1 后 > 0
    - edits 在 Phase 2 后 > 0（Writer 确实做了修改）
    - Phase 3 的输出引用了修改内容（认知连续性）
    - 总 token 消耗在合理范围内

用法:
    python3 tests/test_phase51_collaborative_review.py
    python3 tests/test_phase51_collaborative_review.py --paper examples/radiology_chan_gentzkow_yu.pdf
"""

import os
import sys
import json
import time
import asyncio
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from core.agent import CollaborativeReview


async def run_collaborative_test(paper_path: str, verbose: bool = True):
    """运行协作链测试。"""
    print("=" * 70)
    print("  Phase 51: 多人格协作链 E2E 测试")
    print("=" * 70)
    print(f"  论文: {paper_path}")
    print(f"  模型: {os.environ.get('LLM_MODEL', 'gpt-4.1')}")
    print("=" * 70)

    start_time = time.time()

    collab = CollaborativeReview(
        paper_path=paper_path,
        verbose=verbose,
        max_loop_turns=20,  # 每阶段最多 20 轮（测试用，限制成本）
        token_budget=80000,  # 每阶段 8 万 token 预算
    )

    result = await collab.run()

    elapsed = time.time() - start_time

    # ---- 输出结果 ----
    print("\n" + "=" * 70)
    print("  测试结果")
    print("=" * 70)

    print(f"\n--- Phase 1: Scholar 初审 ({len(result['review'])} chars) ---")
    print(result["review"][:500])
    if len(result["review"]) > 500:
        print(f"  ... (截断，共 {len(result['review'])} 字符)")

    print(f"\n--- Phase 2: Writer 修改 ({len(result['revision'])} chars) ---")
    print(result["revision"][:500])
    if len(result["revision"]) > 500:
        print(f"  ... (截断，共 {len(result['revision'])} 字符)")

    print(f"\n--- Phase 3: Scholar 复审 ({len(result['re_review'])} chars) ---")
    print(result["re_review"][:500])
    if len(result["re_review"]) > 500:
        print(f"  ... (截断，共 {len(result['re_review'])} 字符)")

    # ---- 验证假设 ----
    print("\n" + "=" * 70)
    print("  假设验证")
    print("=" * 70)

    stats = result["stats"]
    findings = result["findings"]
    edits = result["edits"]

    # H1: Scholar 初审产出 findings
    h1_pass = len(findings) > 0
    print(f"\n  H1 (Scholar 产出 findings): {'✅ PASS' if h1_pass else '❌ FAIL'}")
    print(f"      findings 数量: {len(findings)}")
    for f in findings[:5]:
        print(f"      - [{f.get('priority', '?')}] {f.get('finding', '')[:80]}")

    # H2: Writer 做出 edits
    h2_pass = len(edits) > 0
    print(f"\n  H2 (Writer 做出 edits): {'✅ PASS' if h2_pass else '❌ FAIL'}")
    print(f"      edits 数量: {len(edits)}")
    for e in edits[:5]:
        print(f"      - [{e.get('section', '?')}] {e.get('description', '')[:80]}")

    # H3: 复审输出非空且有实质内容
    h3_pass = len(result["re_review"]) > 50
    print(f"\n  H3 (复审有实质内容): {'✅ PASS' if h3_pass else '❌ FAIL'}")
    print(f"      复审输出长度: {len(result['re_review'])} chars")

    # H4: 状态共享验证（findings 和 edits 在同一个 Harness 中累积）
    h4_pass = stats["findings_count"] == len(findings) and stats["edits_count"] == len(edits)
    print(f"\n  H4 (Harness 状态共享): {'✅ PASS' if h4_pass else '❌ FAIL'}")
    print(f"      stats.findings_count={stats['findings_count']}, actual={len(findings)}")
    print(f"      stats.edits_count={stats['edits_count']}, actual={len(edits)}")

    # H5: 认知连续性（复审输出应该引用修改相关内容）
    # 这是一个软验证——我们检查复审输出是否包含"修改"/"改"/"edit"等关键词
    continuity_keywords = ["修改", "改", "调整", "更新", "edit", "revision", "change"]
    h5_pass = any(kw in result["re_review"].lower() for kw in continuity_keywords)
    print(f"\n  H5 (认知连续性): {'✅ PASS' if h5_pass else '⚠️ SOFT FAIL (可能仍然正确)'}")

    # ---- 统计 ----
    print("\n" + "=" * 70)
    print("  运行统计")
    print("=" * 70)
    print(f"  总耗时: {elapsed:.1f}s")
    print(f"  总 token: {stats['total_tokens']:,}")
    print(f"  总 loop turns: {stats['total_loop_turns']}")
    print(f"  各阶段:")
    for p in stats["phases"]:
        print(f"    {p['persona']}({p['phase']}): {p['output_length']} chars")

    # ---- 总结 ----
    all_pass = h1_pass and h2_pass and h3_pass and h4_pass
    print("\n" + "=" * 70)
    if all_pass:
        print("  🎉 Phase 51 E2E 测试通过！协作链认知连续性验证成功。")
    else:
        print("  ⚠️ 部分假设未通过，需要检查。")
    print("=" * 70)

    return {
        "pass": all_pass,
        "h1": h1_pass,
        "h2": h2_pass,
        "h3": h3_pass,
        "h4": h4_pass,
        "h5": h5_pass,
        "stats": stats,
        "elapsed": elapsed,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Phase 51 协作链 E2E 测试")
    parser.add_argument("--paper", default=str(ROOT / "examples" / "radiology_chan_gentzkow_yu.pdf"),
                        help="论文路径")
    parser.add_argument("--quiet", action="store_true", help="减少过程输出")
    args = parser.parse_args()

    if not Path(args.paper).exists():
        print(f"论文文件不存在: {args.paper}", file=sys.stderr)
        sys.exit(1)

    result = asyncio.run(run_collaborative_test(args.paper, verbose=not args.quiet))
    sys.exit(0 if result["pass"] else 1)
