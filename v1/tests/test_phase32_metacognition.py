"""
Phase 32 集成测试: 元认知自我模型 + 可恢复上下文卸载

测试目标:
    1. CognitiveState 能正确序列化并注入 format_context
    2. reflect_and_plan + cognitive_update 能更新认知状态
    3. OffloadStore 在 read_section 时自动 offload
    4. recall_context 能从 offload store 恢复内容
    5. compress_messages 后，认知状态和 offload refs 仍可用
"""

import tempfile
from pathlib import Path
from typing import Optional
from core.harness import Harness


def make_harness_with_paper(tmp_dir: Optional[str] = None):
    """创建一个带论文的测试 Harness。"""
    if tmp_dir is None:
        tmp_dir = tempfile.mkdtemp()
    
    paper_path = Path(tmp_dir) / "test_paper.md"
    paper_path.write_text(
        "## Abstract\n\nThis paper studies the effect of X on Y using DID.\n\n"
        "## Methodology\n\n" + "We employ a difference-in-differences design. " * 50 + "\n\n"
        "## Results\n\n" + "Table 1 shows treatment effects are significant. " * 40 + "\n\n"
        "## Conclusion\n\nOur findings suggest X causes Y.\n"
    )
    return Harness(paper_path=str(paper_path), max_loop_turns=30)


class TestCognitiveState:
    """元认知自我模型测试。"""

    def test_initial_state_no_injection(self):
        """初始状态下 CognitiveState 不注入 context (减少噪音)。"""
        h = make_harness_with_paper()
        ctx = h.format_context()
        assert "认知状态" not in ctx  # 初始状态不注入
        print("✓ test_initial_state_no_injection passed")

    def test_reflect_auto_infers_strategy(self):
        """第一次 reflect_and_plan 自动推断策略。"""
        h = make_harness_with_paper()
        h.state.loop_turns = 2
        
        result = h.execute_tool("reflect_and_plan", {"trigger": "刚开始"})
        
        # 应该自动推断为 breadth_scan (初始阶段)
        assert h.cognitive_state.current_strategy == "breadth_scan"
        assert "反思时刻" in result
        
        # 现在 format_context 应该包含认知状态
        ctx = h.format_context()
        assert "认知状态" in ctx
        assert "广度扫描" in ctx
        print("✓ test_reflect_auto_infers_strategy passed")

    def test_cognitive_update_via_reflect(self):
        """Agent 通过 cognitive_update 参数显式更新认知状态。"""
        h = make_harness_with_paper()
        h.state.loop_turns = 5
        
        result = h.execute_tool("reflect_and_plan", {
            "trigger": "发现方法论重大问题",
            "current_thinking": "DID的平行趋势假设可能不成立",
            "cognitive_update": {
                "strategy": "deep_investigation",
                "strategy_rationale": "方法论有根本问题，需要深入追查",
                "hypotheses": [
                    {"claim": "DID 平行趋势假设不成立", "confidence": 0.7},
                    {"claim": "样本存在选择偏差", "confidence": 0.4},
                ],
                "questions": ["作者是否做了 placebo test?", "pre-trend 图是否可信?"],
                "confidence": 0.3,
                "assessment": "方法论存疑，需验证"
            }
        })
        
        assert "认知状态已更新" in result
        assert h.cognitive_state.current_strategy == "deep_investigation"
        assert len(h.cognitive_state.hypotheses) == 2
        assert h.cognitive_state.hypotheses[0].confidence == 0.7
        assert len(h.cognitive_state.open_questions) == 2
        assert h.cognitive_state.overall_confidence == 0.3
        
        # format_context 应该展示更新后的认知状态
        ctx = h.format_context()
        assert "深度追查" in ctx
        assert "DID" in ctx
        assert "placebo" in ctx
        assert "30%" in ctx  # overall confidence
        print("✓ test_cognitive_update_via_reflect passed")

    def test_hypothesis_lifecycle(self):
        """假说从 active → confirmed/refuted 的生命周期。"""
        h = make_harness_with_paper()
        h.state.loop_turns = 5
        
        # 创建假说
        h.execute_tool("reflect_and_plan", {
            "trigger": "initial",
            "cognitive_update": {
                "hypotheses": [{"claim": "Method is flawed", "confidence": 0.6}],
            }
        })
        assert h.cognitive_state.hypotheses[0].status == "active"
        
        # 更新假说状态
        h.execute_tool("reflect_and_plan", {
            "trigger": "confirmed hypothesis",
            "cognitive_update": {
                "hypotheses": [{"claim": "Method is flawed", "confidence": 0.95, "status": "confirmed"}],
            }
        })
        assert h.cognitive_state.hypotheses[0].status == "confirmed"
        assert h.cognitive_state.hypotheses[0].confidence == 0.95
        
        # confirmed 假说不出现在 format_context 的活跃列表中
        ctx = h.format_context()
        assert "已确认 1 条" in ctx
        print("✓ test_hypothesis_lifecycle passed")


class TestOffloadStore:
    """可恢复上下文卸载测试。"""

    def test_read_section_triggers_offload(self):
        """读取 section 时，长内容自动 offload。"""
        h = make_harness_with_paper()
        
        # 读取 methodology (内容 > 500 chars，应该触发 offload)
        result = h.execute_tool("read_section", {"section": "methodology"})
        assert "difference-in-differences" in result
        
        # 验证 offload 发生了
        assert len(h.offload_store.entries) == 1
        entry = h.offload_store.entries[0]
        assert entry.tool_name == "read_section"
        assert entry.key == "methodology"
        assert entry.char_count > 500
        print("✓ test_read_section_triggers_offload passed")

    def test_short_section_no_offload(self):
        """短 section 不触发 offload。"""
        h = make_harness_with_paper()
        
        # conclusion 内容很短 (< 500 chars)
        result = h.execute_tool("read_section", {"section": "conclusion"})
        
        # 不应该 offload
        assert len(h.offload_store.entries) == 0
        print("✓ test_short_section_no_offload passed")

    def test_recall_context_by_ref_id(self):
        """用 ref_id 回查卸载的内容。"""
        h = make_harness_with_paper()
        
        # 先读一个长 section
        h.execute_tool("read_section", {"section": "methodology"})
        ref_id = h.offload_store.entries[0].ref_id
        
        # 用 recall_context 回查
        result = h.execute_tool("recall_context", {"ref_id": ref_id})
        assert "回查" in result
        assert "difference-in-differences" in result
        print("✓ test_recall_context_by_ref_id passed")

    def test_recall_context_by_key(self):
        """用 key (section 名) 回查卸载的内容。"""
        h = make_harness_with_paper()
        
        h.execute_tool("read_section", {"section": "methodology"})
        
        # 用 key 回查
        result = h.execute_tool("recall_context", {"key": "methodology"})
        assert "回查" in result
        assert "difference-in-differences" in result
        print("✓ test_recall_context_by_key passed")

    def test_recall_context_not_found(self):
        """回查不存在的内容时给出友好错误。"""
        h = make_harness_with_paper()
        
        result = h.execute_tool("recall_context", {"ref_id": "ref_999"})
        assert "回查失败" in result
        
        result = h.execute_tool("recall_context", {"key": "nonexistent"})
        assert "回查失败" in result
        print("✓ test_recall_context_not_found passed")

    def test_offload_refs_in_format_context(self):
        """offload 后 format_context 显示可回查的引用列表。"""
        h = make_harness_with_paper()
        
        # 读两个长 sections
        h.execute_tool("read_section", {"section": "methodology"})
        h.execute_tool("read_section", {"section": "results"})
        
        ctx = h.format_context()
        assert "已卸载的上下文" in ctx
        assert "ref_001" in ctx
        assert "ref_002" in ctx
        assert "recall_context" in ctx
        print("✓ test_offload_refs_in_format_context passed")


class TestIntegration:
    """元认知 + 卸载的集成场景测试。"""

    def test_full_scenario(self):
        """模拟一个完整审阅场景：读→反思→更新认知→回查。"""
        h = make_harness_with_paper()
        
        # 1. 读 abstract（短，不 offload）
        h.execute_tool("read_section", {"section": "abstract"})
        assert len(h.offload_store.entries) == 0  # 太短
        
        # 2. 读 methodology（长，offload）
        h.execute_tool("read_section", {"section": "methodology"})
        assert len(h.offload_store.entries) == 1
        
        # 3. 反思并更新认知
        h.state.loop_turns = 4
        h.execute_tool("reflect_and_plan", {
            "trigger": "读完方法论，有疑问",
            "cognitive_update": {
                "strategy": "targeted_verification",
                "hypotheses": [{"claim": "DID assumption violated", "confidence": 0.6}],
                "questions": ["Is there a pre-trend plot?"],
                "confidence": 0.35,
            }
        })
        
        # 4. 验证 format_context 同时包含认知状态和 offload refs
        ctx = h.format_context()
        assert "定向验证" in ctx  # strategy
        assert "DID" in ctx  # hypothesis
        assert "ref_001" in ctx  # offload ref
        assert "35%" in ctx  # confidence
        
        # 5. 回查方法论（模拟 compress 后需要重看）
        result = h.execute_tool("recall_context", {"key": "methodology"})
        assert "difference-in-differences" in result
        
        print("✓ test_full_scenario passed")


if __name__ == "__main__":
    import sys
    
    test_classes = [TestCognitiveState, TestOffloadStore, TestIntegration]
    for cls in test_classes:
        print(f"\n{'='*40}")
        print(f" {cls.__name__}")
        print(f"{'='*40}")
        instance = cls()
        for method_name in dir(instance):
            if method_name.startswith("test_"):
                try:
                    getattr(instance, method_name)()
                except Exception as e:
                    print(f"✗ {method_name} FAILED: {e}")
                    sys.exit(1)
    
    print(f"\n{'='*40}")
    print("All Phase 32 tests passed! ✓")
    print(f"{'='*40}")
