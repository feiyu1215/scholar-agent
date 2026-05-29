"""
tests/test_spawn_e2e.py — Phase 13: spawn_perspective 端到端验证

验证目标:
1. Agent 在审阅有多维度问题的论文时能否自主触发 spawn_perspective
2. 子循环能否正常运行（独立 context、独立 tools、独立 findings）
3. 子循环的 findings 能否正确注入主 Agent 的 state
4. 主 Agent 能否基于子视角的发现继续推理

测试方式:
- 路径 A: 自然触发 — 给 Agent 足够轮次，看它是否自主 spawn
- 路径 B: 引导触发 — 用 intent 明确引导 Agent 使用多视角审阅

运行方式:
    python tests/test_spawn_e2e.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

# 确保项目根目录在 path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from core.agent import ScholarAgent


# ============================================================
# 测试工具
# ============================================================

class SpawnTracker:
    """追踪 spawn_perspective 调用的 monkey-patch wrapper。"""

    def __init__(self, harness):
        self.harness = harness
        self.spawn_calls = []
        self.original_execute_tool = harness.execute_tool

        # Monkey-patch execute_tool 来追踪 spawn
        def tracked_execute_tool(name, args):
            if name == "spawn_perspective":
                self.spawn_calls.append({
                    "lens": args.get("lens", ""),
                    "focus": args.get("focus", ""),
                    "question": args.get("question", ""),
                    "timestamp": time.time(),
                })
                print(f"  📡 [SPAWN DETECTED] lens={args.get('lens')}, focus={args.get('focus')}")
            return self.original_execute_tool(name, args)

        harness.execute_tool = tracked_execute_tool


# ============================================================
# 测试 A: 自然触发 — Agent 是否自主 spawn
# ============================================================

async def test_natural_spawn():
    """
    给 Agent 足够轮次审阅 sample_paper.md，观察它是否自主决定 spawn。
    
    预期: 论文有方法论问题+写作问题+overclaim 问题，
    如果 Agent 的认知身份工作正常，它"可能"会 spawn 一个统计方法视角。
    但这不是硬性预期——Agent 可能选择自己处理所有维度。
    """
    print("\n" + "=" * 60)
    print("  TEST A: 自然触发 — Agent 自主决定是否 spawn")
    print("=" * 60)

    paper_path = str(PROJECT_ROOT / "examples" / "sample_paper.md")
    agent = ScholarAgent(
        paper_path=paper_path,
        verbose=True,
        max_loop_turns=15,  # 足够轮次让 Agent 有机会 spawn
        token_budget=80000,
    )

    # 安装追踪器
    tracker = SpawnTracker(agent.harness)

    # 启动 — 不给 intent，让 Agent 自主行动
    print("\n[Agent 自主审阅中，无特定 intent...]\n")
    start_time = time.time()
    response = await agent.start()
    elapsed = time.time() - start_time

    # 收集结果
    stats = agent.get_stats()
    findings = agent.get_findings()
    spawn_count = len(tracker.spawn_calls)

    print(f"\n{'─' * 40}")
    print(f"  测试 A 结果:")
    print(f"  - Agent 回复长度: {len(response)} 字符")
    print(f"  - Loop 轮次: {stats['loop_turns_total']}")
    print(f"  - Findings: {stats['findings_count']}")
    print(f"  - Spawn 次数: {spawn_count}")
    print(f"  - 总 Tokens: {stats['total_tokens']}")
    print(f"  - 耗时: {elapsed:.1f}s")
    if tracker.spawn_calls:
        for i, sc in enumerate(tracker.spawn_calls, 1):
            print(f"    Spawn [{i}]: lens={sc['lens']}, focus={sc['focus']}")
    print(f"{'─' * 40}")

    # 检查 spawn 后的 findings 来源
    perspective_findings = [f for f in findings if f.get("perspective")]
    if perspective_findings:
        print(f"\n  ✅ 子视角贡献了 {len(perspective_findings)} 条 findings:")
        for f in perspective_findings:
            print(f"     [{f['perspective']}] {f['finding'][:80]}")

    return {
        "test": "natural_spawn",
        "spawned": spawn_count > 0,
        "spawn_count": spawn_count,
        "spawn_details": tracker.spawn_calls,
        "findings_total": len(findings),
        "findings_from_perspective": len(perspective_findings),
        "loop_turns": stats["loop_turns_total"],
        "total_tokens": stats["total_tokens"],
        "elapsed_seconds": elapsed,
    }


# ============================================================
# 测试 B: 引导触发 — 明确要求多视角审阅
# ============================================================

async def test_guided_spawn():
    """
    用明确的 intent 要求 Agent 使用多视角审阅。
    
    预期: Agent 应该触发至少 1 次 spawn_perspective。
    """
    print("\n" + "=" * 60)
    print("  TEST B: 引导触发 — 明确要求多视角审阅")
    print("=" * 60)

    paper_path = str(PROJECT_ROOT / "examples" / "sample_paper.md")
    agent = ScholarAgent(
        paper_path=paper_path,
        verbose=True,
        max_loop_turns=18,  # 给更多轮次（spawn 本身消耗轮次）
        token_budget=100000,
    )

    # 安装追踪器
    tracker = SpawnTracker(agent.harness)

    # 用明确 intent 引导 spawn
    intent = (
        "请对这篇论文进行多视角审阅。"
        "我特别希望你能用 spawn_perspective 发起至少一个独立视角，"
        "比如让一个统计方法专家专门审查 methodology section 的因果识别策略，"
        "或者让一个写作审查者评估论文的表达质量。"
        "我想看看不同视角的独立发现是否能互相补充。"
    )

    print(f"\n[Intent: {intent[:80]}...]\n")
    start_time = time.time()
    response = await agent.start(user_intent=intent)
    elapsed = time.time() - start_time

    # 收集结果
    stats = agent.get_stats()
    findings = agent.get_findings()
    spawn_count = len(tracker.spawn_calls)

    print(f"\n{'─' * 40}")
    print(f"  测试 B 结果:")
    print(f"  - Agent 回复长度: {len(response)} 字符")
    print(f"  - Loop 轮次: {stats['loop_turns_total']}")
    print(f"  - Findings: {stats['findings_count']}")
    print(f"  - Spawn 次数: {spawn_count}")
    print(f"  - 总 Tokens: {stats['total_tokens']}")
    print(f"  - 耗时: {elapsed:.1f}s")
    if tracker.spawn_calls:
        for i, sc in enumerate(tracker.spawn_calls, 1):
            print(f"    Spawn [{i}]: lens={sc['lens']}, focus={sc['focus']}")
            print(f"               question={sc['question'][:60]}")
    print(f"{'─' * 40}")

    # 检查子视角 findings
    perspective_findings = [f for f in findings if f.get("perspective")]
    if perspective_findings:
        print(f"\n  ✅ 子视角贡献了 {len(perspective_findings)} 条 findings:")
        for f in perspective_findings:
            print(f"     [{f['perspective']}][{f['priority']}] {f['finding'][:80]}")
    else:
        print(f"\n  ⚠️ 没有来自子视角的 findings")

    # 验证子 findings 标记正确
    for f in perspective_findings:
        assert "perspective" in f, "子视角 finding 应标记来源"
        assert f["perspective"] != "", "perspective 标签不应为空"

    return {
        "test": "guided_spawn",
        "spawned": spawn_count > 0,
        "spawn_count": spawn_count,
        "spawn_details": tracker.spawn_calls,
        "findings_total": len(findings),
        "findings_from_perspective": len(perspective_findings),
        "loop_turns": stats["loop_turns_total"],
        "total_tokens": stats["total_tokens"],
        "elapsed_seconds": elapsed,
    }


# ============================================================
# 测试 C: 多轮对话中追加 spawn — 用户在对话中要求新视角
# ============================================================

async def test_chat_spawn():
    """
    先让 Agent 自主审阅（不 spawn），然后通过 chat 要求它 spawn。
    验证多轮对话中 spawn 的工作正确性。
    """
    print("\n" + "=" * 60)
    print("  TEST C: 多轮对话中追加 spawn")
    print("=" * 60)

    paper_path = str(PROJECT_ROOT / "examples" / "sample_paper.md")
    agent = ScholarAgent(
        paper_path=paper_path,
        verbose=True,
        max_loop_turns=10,
        token_budget=100000,
    )

    # 安装追踪器
    tracker = SpawnTracker(agent.harness)

    # 第一轮：自主审阅
    print("\n[第 1 轮: Agent 自主审阅...]\n")
    response1 = await agent.start()
    findings_after_start = len(agent.get_findings())
    spawns_after_start = len(tracker.spawn_calls)
    print(f"  → 审阅完成，{findings_after_start} findings, {spawns_after_start} spawns")

    # 第二轮：明确要求 spawn
    print("\n[第 2 轮: 要求 spawn 统计方法视角...]\n")
    chat_msg = (
        "你的审阅不错。但我想让你再用 spawn_perspective 请一个统计方法专家"
        "专门审查 methodology section 的因果识别策略。"
        "看看有没有你遗漏的方法论问题。"
    )
    response2 = await agent.chat(chat_msg)
    findings_after_chat = len(agent.get_findings())
    spawns_after_chat = len(tracker.spawn_calls)

    print(f"\n{'─' * 40}")
    print(f"  测试 C 结果:")
    print(f"  - 第 1 轮 findings: {findings_after_start}, spawns: {spawns_after_start}")
    print(f"  - 第 2 轮 findings: {findings_after_chat}, spawns: {spawns_after_chat}")
    print(f"  - 新增 spawn: {spawns_after_chat - spawns_after_start}")
    print(f"  - 新增 findings: {findings_after_chat - findings_after_start}")
    stats = agent.get_stats()
    print(f"  - 总 Tokens: {stats['total_tokens']}")
    print(f"{'─' * 40}")

    # 验证
    new_spawns = spawns_after_chat - spawns_after_start
    perspective_findings = [f for f in agent.get_findings() if f.get("perspective")]
    if new_spawns > 0:
        print(f"\n  ✅ 对话中成功触发 {new_spawns} 次 spawn")
        if perspective_findings:
            print(f"  ✅ 子视角产出 {len(perspective_findings)} 条 findings")
    else:
        print(f"\n  ⚠️ Agent 在对话中未触发 spawn（可能直接回答了）")

    return {
        "test": "chat_spawn",
        "spawned_in_chat": new_spawns > 0,
        "spawn_count_total": spawns_after_chat,
        "new_spawns_in_chat": new_spawns,
        "findings_from_perspective": len(perspective_findings),
        "total_tokens": stats["total_tokens"],
    }


# ============================================================
# Main — 运行所有测试
# ============================================================

async def main():
    print("\n╔══════════════════════════════════════════════════════════╗")
    print("║  Phase 13: spawn_perspective E2E 验证                   ║")
    print("╚══════════════════════════════════════════════════════════╝")

    results = {}

    # 测试 B 优先（最可能触发 spawn，快速验证管道正确性）
    try:
        results["guided"] = await test_guided_spawn()
    except Exception as e:
        print(f"\n  ❌ TEST B FAILED: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        results["guided"] = {"error": str(e)}

    # 测试 A（观察自然行为）
    try:
        results["natural"] = await test_natural_spawn()
    except Exception as e:
        print(f"\n  ❌ TEST A FAILED: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        results["natural"] = {"error": str(e)}

    # 测试 C（多轮对话 spawn）
    try:
        results["chat"] = await test_chat_spawn()
    except Exception as e:
        print(f"\n  ❌ TEST C FAILED: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        results["chat"] = {"error": str(e)}

    # 总结
    print("\n" + "═" * 60)
    print("  最终结果汇总")
    print("═" * 60)

    all_spawned = 0
    for name, r in results.items():
        if "error" in r:
            print(f"  [{name}] ❌ ERROR: {r['error'][:80]}")
        else:
            spawned = r.get("spawned", r.get("spawned_in_chat", False))
            count = r.get("spawn_count", r.get("spawn_count_total", 0))
            findings_p = r.get("findings_from_perspective", 0)
            status = "✅" if spawned else "⚠️"
            print(f"  [{name}] {status} spawns={count}, perspective_findings={findings_p}, tokens={r.get('total_tokens', '?')}")
            if spawned:
                all_spawned += 1

    print(f"\n  {'✅ PASS' if all_spawned >= 2 else '⚠️  PARTIAL'}: {all_spawned}/3 tests triggered spawn")
    print("═" * 60)

    # 保存详细结果
    output_path = PROJECT_ROOT / "tests" / "spawn_e2e_results.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n  详细结果已保存到: {output_path}")


if __name__ == "__main__":
    asyncio.run(main())
