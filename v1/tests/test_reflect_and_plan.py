"""
测试 Phase 9: reflect_and_plan 元认知工具

验证目标：
1. 基础调用返回正确的结构化反思信息
2. 进度/资源/覆盖度信息随 state 变化正确更新
3. 空状态时的合理输出（刚开始审阅）
4. 多轮使用后的变化追踪
5. 与现有工具（update_findings, read_section）的协作
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from core.harness import Harness


def make_harness_with_paper():
    """创建一个加载了模拟论文的 Harness。"""
    h = Harness(max_loop_turns=30, token_budget=200_000)
    h._paper_loaded = True
    h.state.paper_sections = {
        "abstract": "We propose a novel method X for task Y, achieving state-of-the-art results.",
        "introduction": "Task Y is important because... " * 50,  # ~300 chars
        "related work": "Prior methods include A (2020), B (2021), and C (2022). " * 30,
        "methodology": "Our approach consists of three components. First, we use attention mechanism..." * 80,
        "experiments": "We evaluate on three benchmarks: GLUE, SuperGLUE, and SQuAD. " * 100,
        "results": "Table 1 shows our results. Our method achieves 95.3% on GLUE, surpassing all baselines." * 40,
        "conclusion": "We have presented method X which improves upon the state of the art.",
        "references": "[1] Smith et al. 2020... [2] Jones et al. 2021... " * 50,
        "acknowledgments": "We thank the anonymous reviewers.",
    }
    return h


class TestReflectBasic:
    """基础测试：reflect_and_plan 在不同状态下的输出。"""

    def test_reflect_empty_state(self):
        """刚开始，没有任何 findings——反思应该显示空进度。"""
        h = make_harness_with_paper()
        result = h.execute_tool("reflect_and_plan", {
            "trigger": "刚开始审阅，想确认方向"
        })
        
        assert "反思时刻" in result
        assert "触发原因: 刚开始审阅，想确认方向" in result
        assert "已记录 0 条发现" in result
        assert "轮次: 已用 0/30" in result
        # 所有 sections 应标记为未阅读（Phase 18: 不再用"核心"分类）
        assert "尚未阅读" in result
        print("✓ test_reflect_empty_state passed")

    def test_reflect_with_findings(self):
        """有一些 findings 后的反思。"""
        h = make_harness_with_paper()
        h.state.loop_turns = 8
        h.state.total_tokens = 45000
        
        # 模拟已有发现
        h.execute_tool("update_findings", {
            "finding": "Abstract claims SOTA but Table 1 shows only 2nd best on SuperGLUE",
            "priority": "high",
            "status": "needs_verification",
            "evidence": "Abstract: 'achieving state-of-the-art', Table 1: SuperGLUE 89.1 vs baseline 89.5",
            "section": "abstract",
        })
        h.execute_tool("update_findings", {
            "finding": "Missing ablation for attention mechanism component",
            "priority": "medium",
            "status": "verified",
            "section": "experiments",
        })
        
        result = h.execute_tool("reflect_and_plan", {
            "trigger": "读完了主要sections想看看全局",
            "current_thinking": "这篇论文的主要问题可能在 SOTA claim 的准确性上",
        })
        
        assert "已记录 2 条发现: 1 high, 1 medium, 0 low" in result
        assert "轮次: 已用 8/30 (剩余 22)" in result
        assert "22%" in result or "23%" in result  # 45000/200000 ≈ 22.5%
        assert "待验证" in result
        assert "Abstract claims SOTA" in result
        assert "你当前的思路: 这篇论文的主要问题可能在 SOTA claim 的准确性上" in result
        print("✓ test_reflect_with_findings passed")

    def test_reflect_coverage_tracking(self):
        """验证覆盖度分析——哪些核心 sections 未被触及。"""
        h = make_harness_with_paper()
        
        # 只在 abstract 和 introduction 有发现
        h.execute_tool("update_findings", {
            "finding": "Introduction overclaims novelty",
            "priority": "medium",
            "status": "suggestion",
            "section": "introduction",
        })
        
        result = h.execute_tool("reflect_and_plan", {"trigger": "检查覆盖度"})
        
        # methodology, experiments, results 等核心 section 应该在未触及列表
        assert "尚未阅读" in result
        # 至少 methodology 或 experiments 应该出现在未触及列表
        assert "methodology" in result or "experiments" in result or "results" in result
        print("✓ test_reflect_coverage_tracking passed")

    def test_reflect_resource_awareness(self):
        """验证资源消耗接近上限时的反思输出。"""
        h = make_harness_with_paper()
        h.state.loop_turns = 25  # 接近 30 轮上限
        h.state.total_tokens = 170_000  # 接近 200k 预算
        
        result = h.execute_tool("reflect_and_plan", {"trigger": "轮次过半该规划后半程"})
        
        assert "轮次: 已用 25/30 (剩余 5)" in result
        assert "85%" in result  # 170000/200000 = 85%
        print("✓ test_reflect_resource_awareness passed")

    def test_reflect_log_accumulation(self):
        """验证反思日志记录（用于后续分析 Agent 元认知频率）。"""
        h = make_harness_with_paper()
        
        h.state.loop_turns = 3
        h.execute_tool("reflect_and_plan", {"trigger": "初次反思"})
        
        h.state.loop_turns = 10
        h.execute_tool("reflect_and_plan", {"trigger": "中期反思"})
        
        h.state.loop_turns = 20
        h.execute_tool("reflect_and_plan", {"trigger": "后期反思"})
        
        assert len(h._reflection_log) == 3
        assert h._reflection_log[0]["turn"] == 3
        assert h._reflection_log[1]["turn"] == 10
        assert h._reflection_log[2]["turn"] == 20
        assert h._reflection_log[0]["trigger"] == "初次反思"
        print("✓ test_reflect_log_accumulation passed")


class TestReflectIntegration:
    """集成测试：reflect 和其他工具配合。"""

    def test_full_workflow_with_reflect(self):
        """模拟一个真实审阅流程：读 → 发现 → 反思 → 调整 → 继续。"""
        h = make_harness_with_paper()
        
        # Turn 1-2: Agent 读 abstract + introduction
        h.state.loop_turns = 2
        section_content = h.execute_tool("read_section", {"section": "abstract"})
        assert "novel method" in section_content
        
        # Turn 3: Agent 发现问题
        h.execute_tool("update_findings", {
            "finding": "SOTA claim without sufficient evidence",
            "priority": "high",
            "status": "needs_verification",
            "section": "abstract",
        })
        
        # Turn 4: Agent 读 methodology
        h.state.loop_turns = 4
        h.execute_tool("read_section", {"section": "methodology"})
        
        # Turn 5: Agent 主动反思——这是 Phase 9 的核心价值
        h.state.loop_turns = 5
        result = h.execute_tool("reflect_and_plan", {
            "trigger": "读完 methodology 后想确认方向",
            "current_thinking": "方法论部分看起来还行，但 SOTA claim 还需要去 results 验证",
        })
        
        # 反思应该提供有用的决策信息
        assert "已记录 1 条发现: 1 high" in result
        assert "尚未阅读" in result  # results / experiments 还没看
        assert "剩余 25" in result  # 30 - 5
        
        # Turn 6-8: 基于反思，Agent 直奔 results（而不是机械地读 related work）
        h.state.loop_turns = 6
        results_content = h.execute_tool("read_section", {"section": "results"})
        assert "95.3%" in results_content
        
        # Turn 9: 验证后更新 finding
        h.execute_tool("update_findings", {
            "finding": "Results actually support SOTA claim on GLUE (95.3%)",
            "priority": "medium",
            "status": "verified",
            "section": "results",
        })
        
        # Turn 10: 再次反思——确认方向调整是否有效
        h.state.loop_turns = 10
        result2 = h.execute_tool("reflect_and_plan", {
            "trigger": "验证完 SOTA claim 后重新评估"
        })
        
        assert "已记录 2 条发现: 1 high, 1 medium" in result2
        # 验证 touched_sections 应该包含 abstract, methodology, results
        assert "已触及" in result2
        
        print("✓ test_full_workflow_with_reflect passed")

    def test_reflect_no_paper(self):
        """没有论文时的反思（边界情况）。"""
        h = Harness(max_loop_turns=30, token_budget=200_000)
        result = h.execute_tool("reflect_and_plan", {"trigger": "看看状态"})
        
        assert "反思时刻" in result
        assert "论文共 0 sections" in result
        print("✓ test_reflect_no_paper passed")


if __name__ == "__main__":
    print("=" * 60)
    print("Phase 9 Tests: reflect_and_plan 元认知工具")
    print("=" * 60)
    
    basic = TestReflectBasic()
    basic.test_reflect_empty_state()
    basic.test_reflect_with_findings()
    basic.test_reflect_coverage_tracking()
    basic.test_reflect_resource_awareness()
    basic.test_reflect_log_accumulation()
    
    integration = TestReflectIntegration()
    integration.test_full_workflow_with_reflect()
    integration.test_reflect_no_paper()
    
    print("\n" + "=" * 60)
    print("All Phase 9 tests passed! ✓")
    print("=" * 60)
