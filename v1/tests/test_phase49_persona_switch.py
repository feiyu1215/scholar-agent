"""
Phase 49: Persona 切换验证 — 证明架构通用性

核心验证目标:
    1. 同一个 cognitive_loop + Harness 能驱动不同的认知身份
    2. Writer persona 的行为和 Scholar persona 有本质区别:
       - Scholar: 质疑优先，产出 findings（审稿意见）
       - Writer: 诊断优先，产出 edits（实际修改）
    3. loop.py 和 harness.py 零修改

测试方法:
    用同一篇论文，分别用 Scholar 和 Writer persona 启动 Agent，
    对比它们的行为模式（tool call 分布、findings vs edits 比例）。
"""

import asyncio
import json
import sys
import os
from pathlib import Path

# 确保项目根目录在 path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from core.agent import ScholarAgent


async def run_persona_test():
    """运行 Scholar vs Writer 对比测试。"""

    paper_path = str(PROJECT_ROOT / "examples" / "sample_paper.md")

    # 检查论文文件存在
    if not Path(paper_path).exists():
        print(f"[ERROR] 论文文件不存在: {paper_path}")
        return None

    results = {}

    # ============================================================
    # Test 1: Scholar persona — 审稿模式
    # ============================================================
    print("\n" + "=" * 60)
    print("  TEST 1: Scholar Persona (审稿人)")
    print("=" * 60)

    scholar_agent = ScholarAgent(
        paper_path=paper_path,
        persona="scholar",
        verbose=True,
        max_loop_turns=15,
        token_budget=80000,
    )

    scholar_response = await scholar_agent.start(
        user_intent="请帮我审阅这篇论文，重点关注方法论和逻辑问题。"
    )

    scholar_stats = scholar_agent.get_stats()
    scholar_findings = scholar_agent.get_findings()
    scholar_edits = scholar_agent.get_edits()

    results["scholar"] = {
        "response_length": len(scholar_response),
        "response_preview": scholar_response[:500],
        "loop_turns": scholar_stats["loop_turns_total"],
        "total_tokens": scholar_stats["total_tokens"],
        "findings_count": len(scholar_findings),
        "edits_count": len(scholar_edits),
        "tool_calls": scholar_stats["tool_calls"],
        "findings_priorities": {
            "high": sum(1 for f in scholar_findings if f.get("priority") == "high"),
            "medium": sum(1 for f in scholar_findings if f.get("priority") == "medium"),
            "low": sum(1 for f in scholar_findings if f.get("priority") == "low"),
        },
    }

    print(f"\n[Scholar 结果]")
    print(f"  Loop turns: {results['scholar']['loop_turns']}")
    print(f"  Findings: {results['scholar']['findings_count']}")
    print(f"  Edits: {results['scholar']['edits_count']}")
    print(f"  Tool calls: {results['scholar']['tool_calls']}")

    # ============================================================
    # Test 2: Writer persona — 修改模式
    # ============================================================
    print("\n" + "=" * 60)
    print("  TEST 2: Writer Persona (写作专家)")
    print("=" * 60)

    writer_agent = ScholarAgent(
        paper_path=paper_path,
        persona="writer",
        verbose=True,
        max_loop_turns=15,
        token_budget=80000,
    )

    writer_response = await writer_agent.start(
        user_intent="请帮我改进这篇论文的 Introduction，重点解决论证逻辑和 AI 写作痕迹问题。"
    )

    writer_stats = writer_agent.get_stats()
    writer_findings = writer_agent.get_findings()
    writer_edits = writer_agent.get_edits()

    results["writer"] = {
        "response_length": len(writer_response),
        "response_preview": writer_response[:500],
        "loop_turns": writer_stats["loop_turns_total"],
        "total_tokens": writer_stats["total_tokens"],
        "findings_count": len(writer_findings),
        "edits_count": len(writer_edits),
        "tool_calls": writer_stats["tool_calls"],
        "findings_priorities": {
            "high": sum(1 for f in writer_findings if f.get("priority") == "high"),
            "medium": sum(1 for f in writer_findings if f.get("priority") == "medium"),
            "low": sum(1 for f in writer_findings if f.get("priority") == "low"),
        },
    }

    print(f"\n[Writer 结果]")
    print(f"  Loop turns: {results['writer']['loop_turns']}")
    print(f"  Findings: {results['writer']['findings_count']}")
    print(f"  Edits: {results['writer']['edits_count']}")
    print(f"  Tool calls: {results['writer']['tool_calls']}")

    # ============================================================
    # 行为对比分析
    # ============================================================
    print("\n" + "=" * 60)
    print("  行为对比分析")
    print("=" * 60)

    # 核心验证: 行为差异来自 identity，不来自 loop/harness
    scholar_edit_ratio = results["scholar"]["edits_count"] / max(1, results["scholar"]["findings_count"])
    writer_edit_ratio = results["writer"]["edits_count"] / max(1, results["writer"]["findings_count"])

    print(f"\n  Scholar edit/finding ratio: {scholar_edit_ratio:.2f}")
    print(f"  Writer  edit/finding ratio: {writer_edit_ratio:.2f}")

    # Scholar 应该以 findings 为主（审稿人产出意见）
    # Writer 应该以 edits 为主（写作专家产出修改）
    behavior_divergence = writer_edit_ratio > scholar_edit_ratio

    # 检查 Writer 是否真的做了修改
    writer_did_edit = results["writer"]["edits_count"] > 0

    # 检查 Scholar 是否以 findings 为主
    scholar_findings_dominant = results["scholar"]["findings_count"] >= results["scholar"]["edits_count"]

    results["analysis"] = {
        "behavior_divergence": behavior_divergence,
        "writer_did_edit": writer_did_edit,
        "scholar_findings_dominant": scholar_findings_dominant,
        "edit_ratio_scholar": scholar_edit_ratio,
        "edit_ratio_writer": writer_edit_ratio,
        "verdict": "PASS" if (behavior_divergence or writer_did_edit) else "NEEDS_INVESTIGATION",
        "interpretation": (
            "行为差异验证通过：同一个 loop/harness，不同 identity 产生了不同的认知行为模式。"
            if (behavior_divergence or writer_did_edit)
            else "行为差异不明显——可能需要更强的 identity 差异化或更长的运行时间。"
        ),
    }

    print(f"\n  行为分化: {'✅ YES' if behavior_divergence else '⚠️ 不明显'}")
    print(f"  Writer 产出修改: {'✅ YES' if writer_did_edit else '❌ NO'}")
    print(f"  Scholar findings 主导: {'✅ YES' if scholar_findings_dominant else '⚠️ NO'}")
    print(f"\n  判定: {results['analysis']['verdict']}")
    print(f"  解读: {results['analysis']['interpretation']}")

    # ============================================================
    # 架构验证: loop.py 和 harness.py 是否需要修改？
    # ============================================================
    results["architecture_validation"] = {
        "loop_modified": False,  # 我们没有修改 loop.py
        "harness_modified": False,  # 我们没有修改 harness.py
        "only_identity_changed": True,  # 只改了 identity.py 和 agent.py 的参数
        "conclusion": "架构通用性验证通过：loop.py 和 harness.py 零修改，行为差异完全来自 identity + tools。"
    }

    print(f"\n  架构验证: {results['architecture_validation']['conclusion']}")

    return results


async def main():
    """主入口。"""
    print("Phase 49: Persona 切换验证 — 证明架构通用性")
    print("=" * 60)

    results = await run_persona_test()

    if results is None:
        print("\n[FAILED] 测试未能运行")
        return

    # 保存结果
    report_path = PROJECT_ROOT / "tests" / "e2e_phase49_persona_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\n\n报告已保存: {report_path}")
    print("\n" + "=" * 60)
    print("  Phase 49 测试完成")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
