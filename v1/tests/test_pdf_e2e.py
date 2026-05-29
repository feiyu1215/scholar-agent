"""
PDF 端到端验证: PDF → ScholarAgent → Reviewer Report

测试目标:
    1. 验证 PDF 加载管道 (pdf_loader → harness → agent)
    2. 验证 Agent 对 PDF 论文能产出有意义的审阅
    3. 验证输出是否趋向 Reviewer Report 结构格式
       (Overall Assessment, Major Issues, Minor Issues, Strengths, Questions)

用法:
    python core/test_pdf_e2e.py
"""

import os
import sys
import json
import asyncio
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from core.agent import ScholarAgent


async def main():
    pdf_path = str(ROOT / "examples" / "sample_paper_economics.pdf")

    print("=" * 60)
    print("  PDF → ScholarAgent → Reviewer Report  (E2E Test)")
    print("=" * 60)
    print(f"  PDF: {pdf_path}")
    print(f"  Model: {os.environ.get('LLM_MODEL', '?')}")
    print()

    # 用明确的 intent 引导输出为 Reviewer Report 格式
    agent = ScholarAgent(
        paper_path=pdf_path,
        verbose=True,
        max_loop_turns=25,
        token_budget=300000,
    )

    # ---- Agent 审阅 PDF ----
    print("\n[启动 Agent 审阅 PDF 论文...]")
    response = await agent.start(
        user_intent=(
            "请审阅这篇论文。审阅完毕后，直接用标准的 Reviewer Report 格式"
            "输出你的完整审阅结论（包含 Overall Assessment、Major Issues、"
            "Minor Issues、Strengths、Questions for Authors）。"
            "注意：不需要先跟我确认，审阅完直接输出完整报告即可。"
        )
    )

    # 如果 Agent 先 talk 了（说"我读完了"）但还没输出 report，追一轮
    if "overall assessment" not in response.lower() and "major issue" not in response.lower():
        print("\n[Agent 初轮未输出 Report，追问继续...]")
        response = await agent.chat(
            "请现在直接输出完整的 Reviewer Report（包含 Overall Assessment + 推荐、"
            "Major Issues、Minor Issues、Strengths、Questions for Authors），"
            "不要省略，不要只输出 summary。"
        )

    print("\n" + "=" * 60)
    print("  AGENT OUTPUT")
    print("=" * 60)
    print(response[:4000])
    if len(response) > 4000:
        print(f"\n[... 输出截断，总计 {len(response)} 字符 ...]")

    # ---- 验证 ----
    print("\n" + "=" * 60)
    print("  VALIDATION")
    print("=" * 60)

    findings = agent.get_findings()
    stats = agent.get_stats()

    # 检查 Reviewer Report 结构关键词
    response_lower = response.lower()
    report_keywords = {
        "overall assessment": "overall assessment" in response_lower or "总评" in response_lower,
        "major issues": "major issue" in response_lower or "主要问题" in response_lower,
        "minor issues": "minor issue" in response_lower or "次要问题" in response_lower,
        "strengths": "strength" in response_lower or "优势" in response_lower or "优点" in response_lower,
        "questions": "question" in response_lower or "问题" in response_lower,
    }

    checks = {
        "PDF loaded (sections > 0)": len(agent.harness.state.paper_sections) > 1,
        "Agent produced findings": len(findings) > 0,
        "Response is substantive (>200 chars)": len(response) > 200,
        "Tokens within budget": stats["total_tokens"] < 300000,
    }
    # Add report structure checks
    for keyword, found in report_keywords.items():
        checks[f"Report contains '{keyword}'"] = found

    all_pass = True
    for check, result in checks.items():
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"  {status}: {check}")
        if not result:
            all_pass = False

    report_match_count = sum(report_keywords.values())
    print(f"\n  Report structure match: {report_match_count}/5 keywords found")

    print(f"\n  Stats: {json.dumps(stats, indent=2, ensure_ascii=False)}")
    print(f"\n{'✓ ALL CHECKS PASSED' if all_pass else '✗ SOME CHECKS FAILED'}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
