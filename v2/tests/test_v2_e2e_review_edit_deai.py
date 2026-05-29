"""
tests/test_v2_e2e_review_edit_deai.py — Phase E: E2E 集成测试

验证完整的 Agent 工作链路:
    review findings → generate_edit_plan → edit (reword/paragraph/insert)
    → post_edit_verify (4 layers) → detect_ai_signals → 闭环

测试原则:
    - 不调用 LLM (mock checker, mock paper_loader)
    - 覆盖正常路径 + 退化路径 (AI 回归、语义丢失、EDIT-5 重试)
    - 验证 state 流转的完整性
    - 所有断言针对 tool_result 字符串 + state 副作用
"""

import sys
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from core.harness import Harness
from core.state import EditPlan, EditStep


# ============================================================
# 测试用论文素材
# ============================================================

PAPER_SECTIONS = {
    "abstract": (
        "This study investigates the relationship between X and Y using a sample of N=1,234 "
        "observations from 2010 to 2020. We find that X is associated with a 3.2% increase "
        "in Y (p=0.03, 95% CI: [1.1, 5.3]). Our results suggest that policy interventions "
        "targeting X may improve Y outcomes."
    ),
    "introduction": (
        "The question of how X affects Y has been debated in the literature for decades. "
        "Prior work by Smith (2015) found a positive correlation, while Jones (2018) "
        "reported null results. We contribute to this debate by leveraging a novel "
        "instrumental variable approach. Our identification strategy exploits a natural "
        "experiment arising from the 2012 policy reform."
    ),
    "methodology": (
        "We employ a two-stage least squares (2SLS) estimator with the policy reform as "
        "an instrument. The first stage F-statistic is 45.2, well above the Stock-Yogo "
        "critical value. Our sample consists of N=1,234 firm-year observations drawn from "
        "the Compustat database. We cluster standard errors at the firm level to account "
        "for serial correlation."
    ),
    "results": (
        "Table 1 presents our main results. Column (1) shows the OLS estimate of 0.032 "
        "(p=0.03). Column (2) reports the 2SLS estimate of 0.041 (p=0.01), which is "
        "larger in magnitude, suggesting that OLS is biased downward. The coefficient "
        "implies that a one-standard-deviation increase in X leads to a 4.1% increase in Y. "
        "Figure 1 shows the first-stage relationship graphically."
    ),
    "discussion": (
        "Our findings are consistent with the theoretical predictions of the framework "
        "proposed by Lee (2020). The magnitude of our estimates is somewhat larger than "
        "those reported in prior cross-sectional studies, which may reflect the fact that "
        "our IV approach addresses the attenuation bias from measurement error. "
        "We note several limitations: our sample is limited to publicly traded firms, "
        "and the external validity of our results to smaller firms remains uncertain."
    ),
}

FINDINGS = [
    {
        "finding": "[Overclaim] Introduction uses 'leveraging' which is an AI-typical word",
        "priority": "medium",
        "status": "verified",
        "section": "introduction",
        "evidence": "We contribute to this debate by leveraging a novel instrumental variable approach.",
    },
    {
        "finding": "[Style] Results section uses causal language ('leads to') but identification is correlational",
        "priority": "high",
        "status": "verified",
        "section": "results",
        "evidence": "a one-standard-deviation increase in X leads to a 4.1% increase in Y",
    },
    {
        "finding": "[Suggestion] Discussion could acknowledge the potential for weak instrument bias",
        "priority": "low",
        "status": "suggestion",
        "section": "discussion",
    },
]


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def harness():
    """创建 E2E 测试用 Harness（mock paper_loader + 禁用 checker LLM 调用）。"""
    with patch('core.harness._pl_load_paper'):
        h = Harness(paper_path="fake_paper.md", max_loop_turns=50, enable_hdwm=False)

    # 注入测试论文
    h.state.paper_sections = {k: v for k, v in PAPER_SECTIONS.items()}
    h.state.findings = [f.copy() for f in FINDINGS]
    h.state.sections_read = set(PAPER_SECTIONS.keys())

    # 禁用 Checker 的 LLM 调用（保留调用路径，只让 check_edit 返回 None）
    h.checker._enabled = False

    return h


# ============================================================
# Test 1: 完整正常路径 — findings → plan → reword → verify PASS → DEAI PASS
# ============================================================

class TestE2EHappyPath:
    """正常路径：修复 AI 词汇 → 验证通过 → DEAI 通过。"""

    def test_full_chain_reword_ai_signal(self, harness):
        """
        链路: generate_edit_plan → reword_sentence (修复 'leveraging')
              → post_edit_verify (4 layers all PASS) → detect_ai_signals (PASS)
        """
        h = harness

        # Step 1: generate_edit_plan
        plan_result = h.execute_tool("generate_edit_plan", {
            "steps": [
                {
                    "target_section": "introduction",
                    "action": "reword",
                    "description": "Replace AI-typical 'leveraging' with 'using'",
                    "priority": "must",
                    "finding_ids": [0],
                },
                {
                    "target_section": "results",
                    "action": "reword",
                    "description": "Replace causal 'leads to' with correlational 'is associated with'",
                    "priority": "must",
                    "finding_ids": [1],
                },
            ],
            "estimated_scope": "局部措辞",
            "rationale": "Fix AI signal + overclaim",
        })

        assert "修改计划已生成" in plan_result
        assert "2 步" in plan_result
        assert h.state.edit_plan is not None
        assert len(h.state.edit_plan.steps) == 2

        # Step 2: reword_sentence — fix 'leveraging'
        reword_result_1 = h.execute_tool("reword_sentence", {
            "section": "introduction",
            "sentence_match": "We contribute to this debate by leveraging a novel instrumental variable approach.",
            "new_sentence": "We contribute to this debate by using a novel instrumental variable approach.",
            "reason": "Remove AI-typical word 'leveraging'",
        })

        assert "已替换" in reword_result_1
        assert "introduction" in reword_result_1
        # Verify post_edit_verify passed (no AI regression since we removed an AI word)
        assert "EDIT-PASS" in reword_result_1 or "验证通过" in reword_result_1
        # Verify the text was actually changed
        assert "leveraging" not in h.state.paper_sections["introduction"]
        assert "using a novel" in h.state.paper_sections["introduction"]

        # Step 3: reword_sentence — fix causal overclaim
        reword_result_2 = h.execute_tool("reword_sentence", {
            "section": "results",
            "sentence_match": "a one-standard-deviation increase in X leads to a 4.1% increase in Y",
            "new_sentence": "a one-standard-deviation increase in X is associated with a 4.1% increase in Y",
            "reason": "Replace causal 'leads to' with correlational language",
        })

        assert "已替换" in reword_result_2
        assert "results" in reword_result_2
        # Numbers preserved → Layer 4 should pass
        assert "语义保持" not in reword_result_2 or "✓" in reword_result_2
        assert "4.1%" in h.state.paper_sections["results"]

        # Step 4: detect_ai_signals on edited sections
        # The introduction no longer has 'leveraging', so AI signal count should be lower
        deai_result = h.execute_tool("detect_ai_signals", {
            "section": "introduction",
        })

        assert "de-AI 迭代进度" in deai_result
        assert h.state.deai_check_count == 1
        assert h.state.deai_last_result is not None
        # The introduction should now be cleaner (leveraging removed)
        # Note: it may still have other patterns, but fewer than before

        # Verify state tracking
        assert len(h.state.edits) == 2
        assert h.state.edits[0]["section"] == "introduction"
        assert h.state.edits[1]["section"] == "results"

    def test_plan_then_edit_paragraph(self, harness):
        """
        链路: generate_edit_plan → edit_paragraph → verify 通过
        """
        h = harness

        # Plan
        h.execute_tool("generate_edit_plan", {
            "steps": [
                {
                    "target_section": "discussion",
                    "action": "add_content",
                    "description": "Acknowledge weak instrument bias",
                    "priority": "could",
                    "finding_ids": [2],
                },
            ],
            "estimated_scope": "局部措辞",
            "rationale": "Address reviewer suggestion",
        })

        # Discussion has 1 paragraph currently → edit it
        old_discussion = h.state.paper_sections["discussion"]
        paragraphs = old_discussion.split("\n\n")
        assert len(paragraphs) >= 1

        # Replace the paragraph with an expanded version
        new_paragraph = (
            "Our findings are consistent with the theoretical predictions of the framework "
            "proposed by Lee (2020). The magnitude of our estimates is somewhat larger than "
            "those reported in prior cross-sectional studies, which may reflect the fact that "
            "our IV approach addresses the attenuation bias from measurement error. "
            "We note several limitations: our sample is limited to publicly traded firms, "
            "the external validity of our results to smaller firms remains uncertain, "
            "and although our first-stage F-statistic is well above conventional thresholds, "
            "we cannot fully rule out weak instrument concerns."
        )

        result = h.execute_tool("edit_paragraph", {
            "section": "discussion",
            "paragraph_index": 0,
            "new_content": new_paragraph,
            "reason": "Add weak instrument limitation acknowledgment",
        })

        assert "已替换" in result
        assert "discussion" in result
        # The new content still has 'somewhat', 'may', preserving hedging style
        assert "weak instrument" in h.state.paper_sections["discussion"]

    def test_insert_content_with_verification(self, harness):
        """
        链路: insert_content → post_edit_verify → 验证一致性 + 风格
        """
        h = harness

        new_para = (
            "As a robustness check, we re-estimate our main specification using "
            "alternative instruments. The results, reported in Appendix Table A1, "
            "are qualitatively similar to our baseline estimates."
        )

        result = h.execute_tool("insert_content", {
            "section": "results",
            "position": 1,  # After the first paragraph
            "content": new_para,
            "reason": "Add robustness check mention per reviewer suggestion",
        })

        assert "已在 section" in result
        assert "results" in result
        # Verify the paragraph was inserted
        paragraphs = h.state.paper_sections["results"].split("\n\n")
        assert len(paragraphs) == 2
        assert "robustness check" in paragraphs[1]


# ============================================================
# Test 2: AI 回归检测路径 — 编辑引入 AI 信号 → Layer 3 FAIL
# ============================================================

class TestE2EAIRegression:
    """编辑引入 AI 典型用词时，post_edit_verify Layer 3 捕获。"""

    def test_reword_introduces_ai_signal(self, harness):
        """修改引入 'delve into' → Layer 3 报告 AI regression → EDIT-FAIL。"""
        h = harness

        # Introduce an AI-typical word via edit
        result = h.execute_tool("reword_sentence", {
            "section": "introduction",
            "sentence_match": "The question of how X affects Y has been debated in the literature for decades.",
            "new_sentence": "This study delves into the multifaceted question of how X affects Y, a topic debated for decades.",
            "reason": "Make opening more engaging",
        })

        # post_edit_verify Layer 3 should detect 'delves' and 'multifaceted'
        assert "AI" in result.lower() or "ai" in result.lower() or "新引入" in result
        # Should report FAIL or at least issues
        assert "EDIT-FAIL" in result or "新引入 AI 信号" in result
        assert h.state.edit_retry_counts.get("introduction", 0) >= 1

    def test_deai_detects_introduced_ai_patterns(self, harness):
        """完整链路：引入 AI 词 → DEAI 检测 → FAIL verdict。"""
        h = harness

        # First, do the bad edit
        h.execute_tool("reword_sentence", {
            "section": "methodology",
            "sentence_match": "We employ a two-stage least squares (2SLS) estimator with the policy reform as an instrument.",
            "new_sentence": "We delve into employing a groundbreaking two-stage least squares (2SLS) estimator, leveraging the policy reform as a pivotal instrument.",
            "reason": "Testing AI regression path",
        })

        # Now run DEAI detection
        result = h.execute_tool("detect_ai_signals", {
            "section": "methodology",
        })

        # Should detect multiple AI signals
        assert h.state.deai_check_count == 1
        last_result = h.state.deai_last_result
        assert last_result is not None
        # The edited text has 'delve', 'groundbreaking', 'leveraging', 'pivotal'
        assert last_result["signal_count"] >= 2 or last_result["verdict"] in ("FAIL", "CONDITIONAL_PASS")


# ============================================================
# Test 3: 语义保持 Layer 4 — 编辑丢失数字 → FAIL
# ============================================================

class TestE2ESemanticPreservation:
    """编辑丢失统计量时，Layer 4 (DEAI-2) 报告语义保持失败。"""

    def test_reword_drops_numeric_value(self, harness):
        """修改丢失了 p=0.03 → Layer 4 报告数字丢失。"""
        h = harness

        # Replace a sentence that contains p=0.03, dropping the p-value
        result = h.execute_tool("reword_sentence", {
            "section": "results",
            "sentence_match": "Column (1) shows the OLS estimate of 0.032 (p=0.03).",
            "new_sentence": "Column (1) shows the OLS estimate of 0.032, which is statistically significant.",
            "reason": "Simplify reporting",
        })

        # Layer 4 should detect that p=0.03 was lost
        assert "语义保持" in result or "丢失" in result
        assert "EDIT-FAIL" in result or "问题" in result

    def test_edit_paragraph_drops_n_value(self, harness):
        """edit_paragraph 丢失 N=1,234 → Layer 4 FAIL。"""
        h = harness

        # Replace methodology paragraph without the N value
        result = h.execute_tool("edit_paragraph", {
            "section": "methodology",
            "paragraph_index": 0,
            "new_content": (
                "We employ a two-stage least squares (2SLS) estimator with the policy reform as "
                "an instrument. The first stage F-statistic is 45.2, well above the Stock-Yogo "
                "critical value. Our sample consists of firm-year observations drawn from "
                "the Compustat database. We cluster standard errors at the firm level to account "
                "for serial correlation."
            ),
            "reason": "Minor rewrite",
        })

        # N=1,234 was dropped
        assert "语义保持" in result or "丢失" in result or "N=" in result


# ============================================================
# Test 4: 因果方向保持 — 弱关联 → 强因果升级
# ============================================================

class TestE2ECausalDirection:
    """编辑改变因果方向时 Layer 4 发出警告。"""

    def test_upgrade_correlation_to_causation(self, harness):
        """将 'associated with' 升级为 'causes' → Layer 4 因果方向警告。"""
        h = harness

        result = h.execute_tool("reword_sentence", {
            "section": "abstract",
            "sentence_match": "We find that X is associated with a 3.2% increase in Y (p=0.03, 95% CI: [1.1, 5.3]).",
            "new_sentence": "We find that X causes a 3.2% increase in Y (p=0.03, 95% CI: [1.1, 5.3]).",
            "reason": "Strengthen claim",
        })

        # Layer 4 should warn about causal direction change
        assert "因果" in result or "方向" in result


# ============================================================
# Test 5: EDIT-5 重试闭环 — 连续 FAIL 达到上限
# ============================================================

class TestE2EEditRetryLoop:
    """EDIT-5: 连续失败达到 MAX_RETRIES 时触发人工介入建议。"""

    def test_retry_count_tracks_failures(self, harness):
        """每次 FAIL 递增 retry_count — 通过 edit_section 精确控制。"""
        h = harness

        # 设置一段干净的 introduction（无 AI 信号）以确保引入信号后 Layer 3 报 regression
        clean_intro = (
            "This study examines how X affects Y. "
            "Prior work found mixed results. "
            "We use a new approach."
        )
        h.state.paper_sections["introduction"] = clean_intro

        # 策略：每次编辑前先恢复 clean baseline，再引入 AI 信号。
        # 这确保每次 Layer 3 比较的是 clean → dirty，new_count > old_count。
        ai_laden_texts = [
            "This study delves into the multifaceted tapestry of how X affects Y. Prior work found mixed results. We use a new approach.",
            "This study delves into the pivotal, groundbreaking landscape of how X affects Y. Prior work found mixed results. We use a new approach.",
            "It is worth noting that this study delves into the multifaceted tapestry of how X affects Y, underscoring prior mixed results. We use a new approach.",
        ]

        for i, bad_text in enumerate(ai_laden_texts):
            # 先恢复 clean baseline，确保 old_text 总是干净的
            h.state.paper_sections["introduction"] = clean_intro
            h.execute_tool("edit_section", {
                "section": "introduction",
                "new_content": bad_text,
                "reason": f"Attempt {i+1} — deliberately introducing AI signals",
            })

        # 每次 edit_section: clean → dirty，Layer 3 检测到 AI regression → FAIL → retry_count 递增
        retry_count = h.state.edit_retry_counts.get("introduction", 0)
        assert retry_count >= 3  # All 3 should FAIL

    def test_max_retries_message(self, harness):
        """达到最大重试次数时，输出包含人工介入建议。"""
        h = harness

        # Manually set retry count to 2 (one more failure will hit max=3)
        h.state.edit_retry_counts["introduction"] = 2

        # Introduce another AI-signal-laden edit
        result = h.execute_tool("reword_sentence", {
            "section": "introduction",
            "sentence_match": "The question of how X affects Y has been debated in the literature for decades.",
            "new_sentence": "Delving into the pivotal, multifaceted landscape of how X affects Y has been debated for decades.",
            "reason": "Test max retry path",
        })

        # Should hit max retries and suggest human intervention
        if "EDIT-FAIL" in result:
            assert "最大重试" in result or "人工介入" in result or "第 3 次" in result


# ============================================================
# Test 6: State 完整性 — 全链路后所有 state 字段一致
# ============================================================

class TestE2EStateIntegrity:
    """验证全链路执行后 state 的完整性和一致性。"""

    def test_state_after_full_chain(self, harness):
        """执行完整链路后，state 各字段保持一致。"""
        h = harness

        # Plan
        h.execute_tool("generate_edit_plan", {
            "steps": [
                {"target_section": "introduction", "action": "reword", "description": "fix AI word", "priority": "must", "finding_ids": [0]},
                {"target_section": "results", "action": "reword", "description": "fix causal", "priority": "must", "finding_ids": [1]},
            ],
            "estimated_scope": "局部措辞",
            "rationale": "E2E state test",
        })

        # Edit 1
        h.execute_tool("reword_sentence", {
            "section": "introduction",
            "sentence_match": "We contribute to this debate by leveraging a novel instrumental variable approach.",
            "new_sentence": "We contribute to this debate using a novel instrumental variable approach.",
            "reason": "Remove AI word",
        })

        # Edit 2
        h.execute_tool("reword_sentence", {
            "section": "results",
            "sentence_match": "a one-standard-deviation increase in X leads to a 4.1% increase in Y",
            "new_sentence": "a one-standard-deviation increase in X is associated with a 4.1% increase in Y",
            "reason": "Fix causal overclaim",
        })

        # DEAI check
        h.execute_tool("detect_ai_signals", {"section": "introduction"})

        # State assertions
        assert h.state.edit_plan is not None
        assert len(h.state.edit_plan.steps) == 2
        assert len(h.state.edits) == 2
        assert h.state.deai_check_count == 1
        assert h.state.deai_last_result is not None
        assert h.state.deai_last_result["check_round"] == 1

        # tool_call_counts should reflect all calls
        assert h.state.tool_call_counts.get("generate_edit_plan", 0) == 1
        assert h.state.tool_call_counts.get("reword_sentence", 0) == 2
        assert h.state.tool_call_counts.get("detect_ai_signals", 0) == 1

        # tool_call_history should have 4 entries
        assert len(h.state.tool_call_history) == 4
        assert h.state.tool_call_history[0]["name"] == "generate_edit_plan"
        assert h.state.tool_call_history[1]["name"] == "reword_sentence"
        assert h.state.tool_call_history[2]["name"] == "reword_sentence"
        assert h.state.tool_call_history[3]["name"] == "detect_ai_signals"

    def test_paper_sections_not_corrupted(self, harness):
        """编辑后，未被编辑的 sections 保持不变。"""
        h = harness
        original_abstract = h.state.paper_sections["abstract"]
        original_methodology = h.state.paper_sections["methodology"]

        # Only edit introduction
        h.execute_tool("reword_sentence", {
            "section": "introduction",
            "sentence_match": "We contribute to this debate by leveraging a novel instrumental variable approach.",
            "new_sentence": "We contribute to this debate using a novel instrumental variable approach.",
            "reason": "Remove AI word",
        })

        # Other sections unchanged
        assert h.state.paper_sections["abstract"] == original_abstract
        assert h.state.paper_sections["methodology"] == original_methodology


# ============================================================
# Test 7: DEAI 默认模式 — 自动聚合已编辑 sections
# ============================================================

class TestE2EDeaiDefaultMode:
    """DEAI 不指定 section 时，自动从 state.edits 聚合已编辑 sections。"""

    def test_default_mode_after_edits(self, harness):
        """编辑两个 sections 后，DEAI 默认模式检测两者。"""
        h = harness

        # Edit introduction
        h.execute_tool("reword_sentence", {
            "section": "introduction",
            "sentence_match": "We contribute to this debate by leveraging a novel instrumental variable approach.",
            "new_sentence": "We contribute to this debate using a novel instrumental variable approach.",
            "reason": "Remove AI word",
        })

        # Edit results
        h.execute_tool("reword_sentence", {
            "section": "results",
            "sentence_match": "a one-standard-deviation increase in X leads to a 4.1% increase in Y",
            "new_sentence": "a one-standard-deviation increase in X is associated with a 4.1% increase in Y",
            "reason": "Fix causal",
        })

        # DEAI without specifying section → should auto-aggregate edited sections
        result = h.execute_tool("detect_ai_signals", {})

        assert "de-AI 迭代进度" in result
        assert h.state.deai_check_count == 1
        # Should have checked both introduction and results
        assert "已编辑 sections" in result or h.state.deai_last_result is not None

    def test_default_mode_no_edits_error(self, harness):
        """无编辑记录时 DEAI 默认模式报错。"""
        h = harness
        h.state.edits = []  # Clear any edits

        result = h.execute_tool("detect_ai_signals", {})
        assert "错误" in result


# ============================================================
# Test 8: Phase Gating — editing tools 在 editing phase 可用
# ============================================================

class TestE2EPhaseGating:
    """验证 editing 工具的 phase gating 正确性。"""

    def test_edit_tools_available_in_editing_phase(self, harness):
        """编辑工具在 editing 阶段可用。"""
        h = harness
        editing_tools = h.tool_registry.get_tools_for_phase("editing")

        assert "reword_sentence" in editing_tools
        assert "edit_paragraph" in editing_tools
        assert "insert_content" in editing_tools
        assert "edit_section" in editing_tools
        assert "generate_edit_plan" in editing_tools
        assert "detect_ai_signals" in editing_tools

    def test_edit_tools_not_in_initial_scan(self, harness):
        """编辑工具不在 initial_scan 阶段。"""
        h = harness
        scan_tools = h.tool_registry.get_tools_for_phase("initial_scan")

        assert "reword_sentence" not in scan_tools
        assert "edit_paragraph" not in scan_tools
        assert "insert_content" not in scan_tools
        assert "edit_section" not in scan_tools


# ============================================================
# Test 9: Voice Drift 检测 — 大幅风格变化触发警告
# ============================================================

class TestE2EVoiceDrift:
    """编辑引入大幅风格漂移时 Layer 2 发出警告。"""

    def test_dramatic_sentence_length_change(self, harness):
        """将短句替换为极长句 → 风格漂移警告。"""
        h = harness

        # Original methodology has moderate sentence lengths.
        # Replace with one extremely long sentence.
        result = h.execute_tool("reword_sentence", {
            "section": "methodology",
            "sentence_match": "We cluster standard errors at the firm level to account for serial correlation.",
            "new_sentence": (
                "In order to ensure that our standard errors are robust to the potential "
                "presence of serial correlation within firms over time which could otherwise "
                "lead to an underestimation of the true sampling variability of our coefficient "
                "estimates and consequently inflate our test statistics beyond their appropriate "
                "critical values we implement a firm-level clustering procedure that allows for "
                "arbitrary correlation patterns within each firm across all time periods in our "
                "sample thereby providing a conservative and robust basis for statistical inference."
            ),
            "reason": "Test voice drift detection",
        })

        # Layer 2 should detect sentence length drift
        # The warning is non-blocking but should appear
        assert "漂移" in result or "警告" in result or "EDIT-WARN" in result


# ============================================================
# Test 10: Consistency Check — 引入悬空引用
# ============================================================

class TestE2EConsistency:
    """编辑引入对不存在 Figure/Table 的引用 → Layer 1 报告。"""

    def test_dangling_figure_reference(self, harness):
        """引用 Figure 99（不存在） → Layer 1 发现悬空引用。"""
        h = harness

        # Add a fake Figure definition to make Layer 1's "defined_figures" non-empty
        h.state.paper_sections["results"] = (
            "Figure 1: First stage relationship.\n\n" +
            h.state.paper_sections["results"]
        )

        result = h.execute_tool("reword_sentence", {
            "section": "discussion",
            "sentence_match": "We note several limitations: our sample is limited to publicly traded firms,",
            "new_sentence": "As shown in Figure 99, we note several limitations: our sample is limited to publicly traded firms,",
            "reason": "Test dangling reference detection",
        })

        # Layer 1 should detect that Figure 99 doesn't exist
        assert "悬空引用" in result or "Figure 99" in result or "引用一致" in result
