"""
tests/test_v2_finding_quality.py — Finding Quality Gate (Q1) 单元测试

验证:
    1. 缺乏证据的 finding 被检出
    2. 高优无可操作建议被检出
    3. 空泛描述被检出
    4. 有证据 + 具体 + 可操作的 finding 不触发
    5. format_nudge 输出格式正确
    6. 空 findings 列表不报错
    7. 中文空泛模式检测
    8. 长描述不被误判为 vague

运行: python3 tests/test_v2_finding_quality.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.finding_quality import FindingQualityGate, QualityIssue, _is_vague


# ============================================================
# Test 1: 缺乏证据被检出
# ============================================================

def test_no_evidence_detected():
    """finding 无证据或证据太短时被标记。"""
    gate = FindingQualityGate()
    findings = [
        {"finding": "Baseline comparison is unfair", "priority": "high", "evidence": ""},
        {"finding": "Sample size too small", "priority": "medium", "evidence": "short"},
        {"finding": "Good finding", "priority": "low", "evidence": "Table 3 shows N=50 in treatment vs N=5000 in control, a 100x imbalance"},
    ]

    issues = gate.evaluate(findings)

    # 前两个缺证据
    no_ev_issues = [i for i in issues if i.issue_type == "no_evidence"]
    assert len(no_ev_issues) == 2
    assert no_ev_issues[0].finding_index == 1
    assert no_ev_issues[1].finding_index == 2

    print("  [PASS] test_no_evidence_detected")


# ============================================================
# Test 2: 高优无可操作建议
# ============================================================

def test_high_priority_not_actionable():
    """high priority finding 无可操作建议被检出。"""
    gate = FindingQualityGate()
    findings = [
        # high, 无动作词 → 应被检出
        {"finding": "The IV strategy is problematic", "priority": "high",
         "evidence": "Section 3.2 describes an IV with weak first stage"},
        # high, 有建议词 → 不应被检出
        {"finding": "Authors should add a robustness check", "priority": "high",
         "evidence": "Table 4 only shows one specification without controls"},
        # medium, 无动作词 → 不检查（只检查 high）
        {"finding": "The writing is poor", "priority": "medium",
         "evidence": "Multiple grammatical errors throughout the paper"},
    ]

    issues = gate.evaluate(findings)
    actionable_issues = [i for i in issues if i.issue_type == "not_actionable"]

    assert len(actionable_issues) == 1
    assert actionable_issues[0].finding_index == 1

    print("  [PASS] test_high_priority_not_actionable")


# ============================================================
# Test 3: 空泛描述
# ============================================================

def test_vague_description():
    """短且空泛的描述被检出。"""
    gate = FindingQualityGate()
    findings = [
        {"finding": "Writing needs improvement", "priority": "low",
         "evidence": "The introduction section has several unclear passages about methodology"},
        {"finding": "The identification strategy relies on a parallel trends assumption that is never empirically validated using pre-treatment data", "priority": "high",
         "evidence": "Section 4.1 states 'we assume parallel trends' without any test"},
    ]

    issues = gate.evaluate(findings)
    vague_issues = [i for i in issues if i.issue_type == "too_vague"]

    assert len(vague_issues) == 1
    assert vague_issues[0].finding_index == 1

    print("  [PASS] test_vague_description")


# ============================================================
# Test 4: 高质量 finding 不触发
# ============================================================

def test_good_finding_no_issues():
    """证据充分、具体、可操作的 finding 不触发任何检查。"""
    gate = FindingQualityGate()
    findings = [
        {
            "finding": "Authors should report first-stage F-statistics for the IV",
            "priority": "high",
            "evidence": "Table 2 reports 2SLS estimates but the first-stage F-stat is absent. "
                        "Stock & Yogo (2005) recommend F>10 for valid inference.",
        },
    ]

    issues = gate.evaluate(findings)
    assert len(issues) == 0

    print("  [PASS] test_good_finding_no_issues")


# ============================================================
# Test 5: format_nudge 输出格式
# ============================================================

def test_format_nudge():
    """nudge 文本包含关键信息且措辞正确。"""
    gate = FindingQualityGate()
    issues = [
        QualityIssue(finding_index=1, finding_text="Some finding", issue_type="no_evidence",
                     suggestion="请指出证据"),
        QualityIssue(finding_index=3, finding_text="Another finding", issue_type="too_vague",
                     suggestion="请具体说明"),
    ]

    text = gate.format_nudge(issues)

    assert "[质量自检]" in text
    assert "2 条" in text
    assert "#1" in text
    assert "#3" in text
    assert "你可以选择" in text  # 自主权声明

    print("  [PASS] test_format_nudge")


# ============================================================
# Test 6: 空列表不报错
# ============================================================

def test_empty_findings():
    """空 findings 列表返回空 issues。"""
    gate = FindingQualityGate()
    issues = gate.evaluate([])
    assert issues == []
    assert gate.format_nudge([]) == ""

    print("  [PASS] test_empty_findings")


# ============================================================
# Test 7: 中文空泛模式
# ============================================================

def test_chinese_vague_patterns():
    """中文空泛模式被正确检测。"""
    assert _is_vague("写作需要改进") is True
    assert _is_vague("表述不清") is True
    assert _is_vague("这是一个具体的、有数据支撑的详细描述，说明了 Table 3 中 N=50 的问题") is False

    print("  [PASS] test_chinese_vague_patterns")


# ============================================================
# Test 8: 长描述不误判
# ============================================================

def test_long_description_not_vague():
    """超过 80 字符的描述即使包含空泛词也不被误判。"""
    long_text = "The writing needs improvement in Section 3 where the authors describe their identification strategy using instrumental variables but fail to provide first-stage results"
    assert _is_vague(long_text) is False

    print("  [PASS] test_long_description_not_vague")


# ============================================================
# Test 9: nudge 截断（超过 4 条时）
# ============================================================

def test_format_nudge_truncation():
    """超过 4 条 issues 时显示截断提示。"""
    gate = FindingQualityGate()
    issues = [
        QualityIssue(finding_index=i, finding_text=f"Finding {i}", issue_type="no_evidence",
                     suggestion="add evidence")
        for i in range(1, 8)
    ]

    text = gate.format_nudge(issues)

    assert "7 条" in text  # 总数
    assert "还有 3 条" in text  # 7 - 4 = 3 条截断
    assert "#5" not in text  # 第5条不在详情中

    print("  [PASS] test_format_nudge_truncation")


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":
    print("=" * 60)
    print("Running: test_v2_finding_quality.py")
    print("=" * 60)

    tests = [
        test_no_evidence_detected,
        test_high_priority_not_actionable,
        test_vague_description,
        test_good_finding_no_issues,
        test_format_nudge,
        test_empty_findings,
        test_chinese_vague_patterns,
        test_long_description_not_vague,
        test_format_nudge_truncation,
    ]

    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            print(f"  [FAIL] {t.__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed, {passed + failed} total")
    print("=" * 60)

    if failed > 0:
        sys.exit(1)
