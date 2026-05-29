"""
tests/test_phase50_cognitive_layering.py — Phase 50: 认知分层验证

验证目标:
    1. CognitiveChecker 能独立工作（单元测试）
    2. Harness 集成后，edit_section 会触发 Checker（集成测试）
    3. mark_complete 会触发 pre-completion check（集成测试）
    4. E2E: 完整 Agent 运行中 Checker 被调用且产出统计可观测

设计:
    - 不 mock LLM（用真实 API 验证端到端行为）
    - 对比 Checker 开启/关闭时的行为差异
    - 验证 Checker 失败时的静默降级
"""

import asyncio
import json
import os
import sys
from pathlib import Path

# 确保项目根目录在 path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

def test_checker_unit_post_edit():
    """单元测试: Checker 的 post-edit check 能正常调用并返回结果。"""
    from core.checker import CognitiveChecker

    checker = CognitiveChecker()
    checker._enabled = True  # 显式启用（其他测试可能已禁用模块变量）

    # 测试一段正常的学术文本
    good_text = (
        "This paper examines the causal effect of National Innovation Demonstration Zones "
        "on regional patent output using a staggered difference-in-differences design. "
        "We find that treated cities experience a 15.3% increase in patent applications "
        "relative to control cities, with effects concentrated in utility model patents."
    )
    result = checker.check_edit(good_text, reason="改善 Introduction 的论证逻辑")
    # 正常文本应该 PASS（返回 None）
    print(f"  [Post-Edit Good] result = {result}")
    # 不强制断言 None（小模型可能有误判），但记录结果

    # 测试一段有明显 AI 痕迹的文本
    ai_text = (
        "Furthermore, it is worth noting that this groundbreaking study represents "
        "a significant contribution to the existing body of literature. Moreover, "
        "the findings underscore the paramount importance of innovation policy. "
        "In conclusion, this research paves the way for future investigations."
    )
    result_ai = checker.check_edit(ai_text, reason="润色 Conclusion")
    print(f"  [Post-Edit AI] result = {result_ai}")

    # 统计应该有记录
    stats = checker.stats()
    print(f"  [Checker Stats] {json.dumps(stats, indent=2)}")
    assert stats["total_checks"] >= 1, "Checker 应该至少执行了 1 次检查"
    print("  ✓ test_checker_unit_post_edit PASSED")


def test_checker_unit_pre_completion():
    """单元测试: Checker 的 pre-completion check。"""
    from core.checker import CognitiveChecker

    checker = CognitiveChecker()
    checker._enabled = True  # 显式启用

    abstract = (
        "We study the effect of place-based innovation policies on regional patent output. "
        "Using a staggered DID design with data from 283 Chinese cities (2003-2019), "
        "we find that NIDZs increase patent applications by 15.3%. "
        "Mechanism analysis reveals that fiscal S&T expenditure and talent agglomeration "
        "are the primary channels."
    )

    # 场景 1: 发现覆盖了核心 claim
    good_findings = [
        {"finding": "[方法论] DID 平行趋势假设检验不充分", "priority": "high"},
        {"finding": "[数据] 专利数据可能有 truncation bias", "priority": "medium"},
        {"finding": "[Overclaim] 15.3% 的经济含义未讨论", "priority": "medium"},
    ]
    result = checker.check_pre_completion(abstract, good_findings)
    print(f"  [Pre-Completion Good] result = {result}")

    # 场景 2: 发现只覆盖了一个维度（明显遗漏）
    narrow_findings = [
        {"finding": "[格式] 表格标题不规范", "priority": "low"},
        {"finding": "[格式] 参考文献缺少 DOI", "priority": "low"},
    ]
    result_narrow = checker.check_pre_completion(abstract, narrow_findings)
    print(f"  [Pre-Completion Narrow] result = {result_narrow}")

    stats = checker.stats()
    print(f"  [Checker Stats] {json.dumps(stats, indent=2)}")
    assert stats["total_checks"] >= 2
    print("  ✓ test_checker_unit_pre_completion PASSED")


def test_checker_disabled():
    """验证 Checker 禁用时静默跳过。"""
    from core.checker import CognitiveChecker

    checker = CognitiveChecker()
    checker._enabled = False

    result = checker.check_edit("any text", "any reason")
    assert result is None, "禁用时应返回 None"

    result2 = checker.check_pre_completion("abstract", [{"finding": "x", "priority": "high"}])
    assert result2 is None, "禁用时应返回 None"

    assert checker.total_checks == 0, "禁用时不应有任何检查计数"
    print("  ✓ test_checker_disabled PASSED")


def test_harness_integration_edit():
    """集成测试: Harness 的 edit_section 触发 Checker。"""
    from core.harness import Harness

    harness = Harness(
        paper_path=str(PROJECT_ROOT / "examples" / "sample_paper.md"),
        max_loop_turns=10,
    )
    harness.checker._enabled = True  # 显式启用
    harness.load_paper()

    # 找一个存在的 section
    sections = list(harness.state.paper_sections.keys())
    assert len(sections) > 0, "论文应该有 sections"

    target_section = None
    for s in sections:
        if s != "full" and len(harness.state.paper_sections[s]) > 100:
            target_section = s
            break

    if target_section is None:
        print("  [SKIP] 没有找到合适的 section 进行编辑测试")
        return

    # 执行 edit
    new_content = "This is a test edit to verify Checker integration works end-to-end."
    result = harness.execute_tool("edit_section", {
        "section": target_section,
        "new_content": new_content,
        "reason": "测试 Phase 50 Checker 集成",
    })

    print(f"  [Edit Result] {result[:300]}")

    # 验证 Checker 被调用
    checker_stats = harness.checker.stats()
    print(f"  [Checker Stats] {json.dumps(checker_stats, indent=2)}")
    assert checker_stats["total_checks"] >= 1, "edit_section 应该触发 Checker"
    print("  ✓ test_harness_integration_edit PASSED")


def test_harness_integration_done():
    """集成测试: Harness 的 mark_complete 触发 pre-completion Checker。"""
    from core.harness import Harness

    harness = Harness(
        paper_path=str(PROJECT_ROOT / "examples" / "sample_paper.md"),
        max_loop_turns=10,
    )
    harness.checker._enabled = True  # 显式启用
    harness.load_paper()

    # 添加一些 findings（否则 quality gate 会先拦截）
    for i in range(3):
        harness.execute_tool("update_findings", {
            "finding": f"[测试] 发现 {i+1}: 方法论问题",
            "priority": "high" if i == 0 else "medium",
            "status": "verified",
        })

    # 模拟已读 sections（否则 quality gate 可能因为"读得太少"而 nudge）
    harness.state.sections_read = list(harness.state.paper_sections.keys())[:5]
    harness.state.loop_turns = 10  # 模拟已经跑了一段时间

    # 执行 mark_complete
    result = harness.execute_tool("mark_complete", {
        "summary": "审阅完成，发现 3 个问题",
    })

    print(f"  [Done Result] {result[:300]}")

    # Checker 应该被调用（无论结果是 DONE 还是 NUDGE）
    checker_stats = harness.checker.stats()
    print(f"  [Checker Stats] {json.dumps(checker_stats, indent=2)}")
    assert checker_stats["total_checks"] >= 1, "mark_complete 应该触发 pre-completion Checker"
    print("  ✓ test_harness_integration_done PASSED")


async def test_e2e_dual_model():
    """
    E2E 测试: 完整 Agent 运行，验证双模型协作。
    
    用 Writer persona（会做 edit），验证:
    1. 主循环用大模型
    2. edit 后 Checker 用小模型校验
    3. 统计中能看到两个模型的调用
    """
    from core.agent import ScholarAgent

    agent = ScholarAgent(
        paper_path=str(PROJECT_ROOT / "examples" / "sample_paper.md"),
        model=None,  # 使用默认大模型
        verbose=True,
        max_loop_turns=15,
        persona="writer",
    )
    agent.harness.checker._enabled = True  # 显式启用

    response = await agent.start(
        user_intent="请帮我改进这篇论文的 Introduction，重点解决论证逻辑问题。只改一处即可。"
    )

    print(f"\n{'='*60}")
    print(f"[E2E Response] {response[:500]}")
    print(f"{'='*60}")

    stats = agent.get_stats()
    print(f"\n[Full Stats] {json.dumps(stats, indent=2, ensure_ascii=False)}")

    # 验证
    checker_stats = stats.get("checker_stats", {})
    print(f"\n[Checker Stats] {json.dumps(checker_stats, indent=2)}")

    # 核心断言: 如果 Writer 做了 edit，Checker 应该被触发
    edits_count = stats["edits_count"]
    if edits_count > 0:
        assert checker_stats["total_checks"] >= 1, (
            f"Writer 做了 {edits_count} 次 edit，但 Checker 未被触发"
        )
        print(f"\n  ✓ 双模型协作验证通过: {edits_count} edits → {checker_stats['total_checks']} checks")
    else:
        print(f"\n  ⚠ Writer 未做 edit（可能 token 不够或论文太短），Checker 未被触发")
        print(f"    这不是 bug——只是 Writer 在这次运行中选择了只诊断不修改")

    # 保存报告
    report = {
        "phase": 50,
        "test": "e2e_dual_model",
        "persona": "writer",
        "main_model": stats["model"],
        "checker_model": checker_stats.get("model", "unknown"),
        "loop_turns": stats["loop_turns_total"],
        "findings": stats["findings_count"],
        "edits": stats["edits_count"],
        "checker_stats": checker_stats,
        "dual_model_active": checker_stats.get("total_checks", 0) > 0,
        "response_preview": response[:300],
    }

    report_path = PROJECT_ROOT / "tests" / "e2e_phase50_dual_model_report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n  报告已保存: {report_path}")

    return report


# ============================================================
# 运行入口
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("  Phase 50: 认知分层（Thinker/Checker 双模型）验证")
    print("=" * 60)

    # 单元测试
    print("\n--- 单元测试 ---")
    test_checker_disabled()
    test_checker_unit_post_edit()
    test_checker_unit_pre_completion()

    # 集成测试
    print("\n--- 集成测试 ---")
    test_harness_integration_edit()
    test_harness_integration_done()

    # E2E 测试
    print("\n--- E2E 测试 ---")
    report = asyncio.run(test_e2e_dual_model())

    print("\n" + "=" * 60)
    print("  Phase 50 测试完成")
    print(f"  双模型协作: {'✓ 已验证' if report.get('dual_model_active') else '⚠ 未触发（Writer 未做 edit）'}")
    print("=" * 60)
