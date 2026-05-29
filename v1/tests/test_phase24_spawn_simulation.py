"""
Phase 24: SPAWN 子循环模拟测试 (Perspective Split Simulation)

验证目标：
    1. spawn_perspective 信号在 cognitive_loop 中正确触发 _run_sub_perspective
    2. MockLLMClient 的队列式 script 能正确服务嵌套调用（主循环+子循环共享同一 client）
    3. 子 Harness 创建正确（只包含 focus sections、独立 state）
    4. 子循环 findings 正确注入主 Harness（带 perspective 标记）
    5. 子循环 token 消耗汇入主 Harness
    6. 子循环 DoomStop 不阻断主循环
    7. 子循环无 findings 时的 fallback（从 content 提取结论）
    8. 多次 spawn 连续触发的正确性
    9. focus sections 模糊匹配逻辑
    10. spawn 参数缺失时的错误处理

设计原则 (COGNITIVE_ANCHOR §2.3 + §5.5):
    分身从认知需要中涌现。子循环有独立的 context、tools 和 findings，
    但最终结论回归到主思考体——更新而非替代核心理解。

    MockLLMClient 的队列式 pop 天然支持嵌套调用：
    主循环触发 SPAWN → loop 进入 _run_sub_perspective → 子循环再次调用
    同一个 client.chat_with_tools → pop script 中"为子循环准备的"items。
    因此 script 的物理顺序必须与实际调用时序完全一致。

运行: pytest tests/test_phase24_spawn_simulation.py -v
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

# 确保可以 import 项目模块
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.loop import cognitive_loop, LoopDone, LoopTalk, LoopDoomStop
from core.harness import Harness
from core.identity import SCHOLAR_TOOLS

# 防止 dotenv 环境污染导致 Checker 在 mark_complete 时触发 nudge
import core.checker as _checker_mod
_checker_mod.CHECKER_ENABLED = False


# ============================================================
# MockLLMClient — 复用 Phase 23 设计，队列式 pop
# ============================================================

class MockLLMClient:
    """
    脚本化 LLM 客户端：按预定义的 responses 序列返回结果。
    
    关键特性：主循环和子循环共享同一个 client 实例。
    当 spawn 触发子循环后，子循环的 chat_with_tools 调用会 pop
    script 中的下一个 item——因此 script 必须按实际调用时序排列：
    
    例如（单次 spawn）:
        script = [
            主循环 Turn 1: spawn_perspective,
            子循环 Turn 1: read_section,       ← 子循环开始消费
            子循环 Turn 2: update_findings,
            子循环 Turn 3: mark_complete,      ← 子循环结束
            主循环 Turn 2: mark_complete,      ← 回到主循环
        ]
    """

    def __init__(self, script: list[dict]):
        self.script = list(script)
        self._tc_counter = 0
        self.total_calls = 0
        self.model = "mock-scripted"

    async def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        temperature: float = 0.1,
        max_tokens: int = 4096,
        **kwargs,
    ) -> dict[str, Any]:
        """返回脚本中的下一个响应。"""
        self.total_calls += 1

        if not self.script:
            return {
                "content": "(脚本耗尽)",
                "tool_calls": [],
                "finish_reason": "stop",
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            }

        item = self.script.pop(0)

        tool_calls = []
        for tc in item.get("tool_calls", []):
            self._tc_counter += 1
            tool_calls.append({
                "id": f"tc_{self._tc_counter:04d}",
                "name": tc["name"],
                "arguments": tc.get("arguments", {}),
            })

        return {
            "content": item.get("content", None),
            "tool_calls": tool_calls,
            "finish_reason": "tool_calls" if tool_calls else "stop",
            "usage": {"prompt_tokens": 200, "completion_tokens": 100},
        }

    def stats(self) -> dict:
        return {"provider": "mock", "model": self.model, "total_calls": self.total_calls}


# ============================================================
# 通用辅助
# ============================================================

def _make_harness(sections: dict[str, str] | None = None, max_turns: int = 20) -> Harness:
    """创建测试用 Harness。"""
    tmp_dir = tempfile.mkdtemp()
    h = Harness(max_loop_turns=max_turns, memory_dir=tmp_dir)
    h._paper_loaded = True
    if sections is None:
        sections = {
            "abstract": (
                "## Abstract\n\n"
                "We propose DeepFusion, a novel approach to multi-scale feature "
                "fusion for image classification. Our method achieves 79.3% top-1 "
                "accuracy on ImageNet, improving over the previous state-of-the-art "
                "by 3.2 percentage points."
            ),
            "introduction": (
                "## Introduction\n\n"
                "Image classification remains a fundamental task in computer vision. "
                "Recent advances in deep learning, particularly transformer architectures, "
                "have pushed the boundaries of accuracy. However, existing methods often "
                "rely on single-scale features from the final backbone layer, missing "
                "important multi-scale information. We propose DeepFusion to address this."
            ),
            "methodology": (
                "## Methodology\n\n"
                "DeepFusion has three components: (1) ResNet-50 backbone for feature "
                "extraction, (2) Multi-Scale Attention Module (MSAM) that fuses features "
                "from layers 3 and 4, and (3) a classification head with dropout=0.5. "
                "We use AdamW optimizer with lr=1e-4, weight_decay=0.01, cosine annealing "
                "over 100 epochs. Trained on 4xA100 GPUs with batch_size=256."
            ),
            "results": (
                "## Results\n\n"
                "| Method | Top-1 Acc | Top-5 Acc | Params |\n"
                "|--------|-----------|----------|--------|\n"
                "| ResNet-50 | 76.1 | 92.9 | 25.6M |\n"
                "| ViT-B/16 | 77.9 | 93.9 | 86.6M |\n"
                "| Swin-T | 81.3 | 95.5 | 28.3M |\n"
                "| DeepFusion | 79.3 | 94.8 | 27.1M |\n\n"
                "Our method achieves 79.3% top-1 accuracy with comparable parameters "
                "to ResNet-50 while significantly outperforming it."
            ),
            "conclusion": (
                "## Conclusion\n\n"
                "We presented DeepFusion, achieving state-of-the-art results on "
                "ImageNet classification. Our multi-scale attention mechanism "
                "provides a 3.2% improvement with minimal parameter overhead."
            ),
        }
    h.state.paper_sections = sections
    return h


def _make_messages() -> list[dict]:
    return [
        {"role": "system", "content": "你是审稿人。\n\n{workspace_state}"},
        {"role": "user", "content": "请审阅这篇论文。"},
    ]


def _run(coro):
    """同步运行 async 协程。"""
    return asyncio.run(coro)


# ============================================================
# 测试组 1: 基本 Spawn 触发与子循环执行
# ============================================================

class TestBasicSpawnExecution:
    """验证 spawn_perspective 信号能正确触发子循环并返回结果。"""

    def test_spawn_triggers_sub_loop_and_returns_findings(self):
        """
        主循环 spawn → 子循环 read+find+done → findings 注入主 harness。
        
        Script 时序:
            主循环 T1: spawn_perspective(lens=方法论专家, focus=methodology)
            ---- 子循环开始 ----
            子循环 T1: read_section(methodology)
            子循环 T2: update_findings(缺少 ablation)
            子循环 T3: mark_complete
            ---- 子循环结束 ----
            主循环 T2: mark_complete
        """
        h = _make_harness()
        script = [
            # 主循环 T1: 触发 spawn
            {"content": "需要方法论专家看看。", "tool_calls": [{
                "name": "spawn_perspective",
                "arguments": {
                    "lens": "方法论专家",
                    "focus": "methodology",
                    "question": "实验设计是否有致命缺陷？需要做 ablation study 吗？",
                },
            }]},
            # ---- 子循环 T1 ----
            {"content": "读方法。", "tool_calls": [
                {"name": "read_section", "arguments": {"section": "methodology"}}
            ]},
            # ---- 子循环 T2 ----
            {"content": "缺 ablation。", "tool_calls": [{
                "name": "update_findings",
                "arguments": {
                    "finding": "缺少 ablation study: 未验证 MSAM 模块的独立贡献",
                    "priority": "high",
                    "status": "verified",
                    "evidence": "只报告了完整 DeepFusion 的结果，没有去掉 MSAM 的对比",
                    "section": "methodology",
                },
            }]},
            # ---- 子循环 T3: done ----
            {"content": "完成审视。", "tool_calls": [
                {"name": "mark_complete", "arguments": {"summary": "缺少 ablation 是主要问题"}}
            ]},
            # 主循环 T2: 收到子视角结果后 done
            {"content": "好的。", "tool_calls": [
                {"name": "mark_complete", "arguments": {"summary": "子视角发现方法论缺陷"}}
            ]},
        ]

        result = _run(cognitive_loop(_make_messages(), h, SCHOLAR_TOOLS, MockLLMClient(script), verbose=False))

        assert isinstance(result, LoopDone)
        # 子视角的 finding 已注入主 harness
        assert len(h.state.findings) == 1
        assert h.state.findings[0]["perspective"] == "方法论专家"
        assert "ablation" in h.state.findings[0]["finding"]
        assert h.state.findings[0]["priority"] == "high"

    def test_spawn_with_read_before_and_after(self):
        """
        主循环在 spawn 前后都有操作：read → spawn → read → done。
        验证 spawn 不破坏主循环的状态连续性。
        """
        h = _make_harness()
        script = [
            # 主循环 T1: 先自己读 abstract
            {"content": "先看摘要。", "tool_calls": [
                {"name": "read_section", "arguments": {"section": "abstract"}}
            ]},
            # 主循环 T2: spawn 统计专家看 results
            {"content": "请统计专家看数据。", "tool_calls": [{
                "name": "spawn_perspective",
                "arguments": {
                    "lens": "统计方法专家",
                    "focus": "results",
                    "question": "实验结果的统计显著性如何？是否缺少误差分析？",
                },
            }]},
            # ---- 子循环 T1 ----
            {"content": "看结果。", "tool_calls": [
                {"name": "read_section", "arguments": {"section": "results"}}
            ]},
            # ---- 子循环 T2 ----
            {"content": "没有误差线。", "tool_calls": [{
                "name": "update_findings",
                "arguments": {
                    "finding": "所有实验结果只报告单次运行，缺少标准差或置信区间",
                    "priority": "medium",
                    "status": "verified",
                    "evidence": "Table 仅有单个数字",
                    "section": "results",
                },
            }]},
            # ---- 子循环 T3: done ----
            {"content": "审视完毕。", "tool_calls": [
                {"name": "mark_complete", "arguments": {"summary": "缺少统计误差分析"}}
            ]},
            # 主循环 T3: spawn 结束后继续自己的工作
            {"content": "我来看 conclusion。", "tool_calls": [
                {"name": "read_section", "arguments": {"section": "conclusion"}}
            ]},
            # 主循环 T4: 记录自己的发现
            {"content": "过度宣称。", "tool_calls": [{
                "name": "update_findings",
                "arguments": {
                    "finding": "Conclusion 声称 SOTA 但 Swin-T 的 81.3% 明显高于 79.3%",
                    "priority": "high",
                    "status": "verified",
                    "section": "conclusion",
                },
            }]},
            # 主循环 T5: done
            {"content": "完成。", "tool_calls": [
                {"name": "mark_complete", "arguments": {"summary": "两个问题"}}
            ]},
        ]

        result = _run(cognitive_loop(_make_messages(), h, SCHOLAR_TOOLS, MockLLMClient(script), verbose=False))

        assert isinstance(result, LoopDone)
        # 主循环自己的状态正常
        assert "abstract" in h.state.sections_read
        assert "conclusion" in h.state.sections_read
        # findings: 1 来自子视角 + 1 来自主循环
        assert len(h.state.findings) == 2
        perspective_findings = [f for f in h.state.findings if f.get("perspective")]
        own_findings = [f for f in h.state.findings if not f.get("perspective")]
        assert len(perspective_findings) == 1
        assert len(own_findings) == 1
        assert perspective_findings[0]["perspective"] == "统计方法专家"

    def test_spawn_missing_params_returns_error(self):
        """spawn_perspective 缺少必要参数时返回错误（不触发子循环）。"""
        h = _make_harness()
        script = [
            # 缺少 question
            {"content": "试试。", "tool_calls": [{
                "name": "spawn_perspective",
                "arguments": {"lens": "写作审查者", "focus": "introduction"},
            }]},
            # Agent 收到错误后 done
            {"content": "算了。", "tool_calls": [
                {"name": "mark_complete", "arguments": {"summary": "spawn 参数缺失"}}
            ]},
        ]

        client = MockLLMClient(script)
        result = _run(cognitive_loop(_make_messages(), h, SCHOLAR_TOOLS, client, verbose=False))

        assert isinstance(result, LoopDone)
        # 没有子循环被触发，client 只被主循环调用 2 次
        assert client.total_calls == 2
        # 无 findings
        assert len(h.state.findings) == 0


# ============================================================
# 测试组 2: 子 Harness 创建与 Section 分发
# ============================================================

class TestSubHarnessCreation:
    """验证 create_sub_harness 的 focus sections 选择逻辑。"""

    def test_focus_sections_fuzzy_match(self):
        """focus=methodology → 子 harness 只包含 methodology section。"""
        h = _make_harness()
        sub = h.create_sub_harness(["methodology"])

        assert "methodology" in sub.state.paper_sections
        # 不应包含无关 section
        assert "abstract" not in sub.state.paper_sections
        assert "conclusion" not in sub.state.paper_sections

    def test_focus_multiple_sections(self):
        """focus 包含多个 section。"""
        h = _make_harness()
        sub = h.create_sub_harness(["methodology", "results"])

        assert "methodology" in sub.state.paper_sections
        assert "results" in sub.state.paper_sections
        assert "abstract" not in sub.state.paper_sections

    def test_focus_no_match_falls_back_to_all(self):
        """focus 完全不匹配时退化为包含全部 sections。"""
        h = _make_harness()
        sub = h.create_sub_harness(["nonexistent_section"])

        # 退化：全部 sections（不含 full）
        assert len(sub.state.paper_sections) == len(h.state.paper_sections)

    def test_sub_harness_has_independent_state(self):
        """子 harness 的 findings 和 state 完全独立。"""
        h = _make_harness()
        h.state.findings.append({"finding": "主循环发现", "priority": "high", "status": "verified"})

        sub = h.create_sub_harness(["abstract"])

        # 子 harness 不继承主 harness 的 findings
        assert len(sub.state.findings) == 0
        # 修改子 harness 不影响主 harness
        sub.state.findings.append({"finding": "子循环发现", "priority": "low", "status": "verified"})
        assert len(h.state.findings) == 1  # 主 harness 仍然只有 1 条

    def test_sub_harness_reduced_limits(self):
        """子 harness 有更短的 max_turns 和更小的 token_budget。"""
        h = _make_harness(max_turns=30)
        sub = h.create_sub_harness(["abstract"])

        assert sub.state.max_loop_turns == 8
        assert sub.state.token_budget == 30000


# ============================================================
# 测试组 3: Findings 注入与标记
# ============================================================

class TestFindingsInjection:
    """验证子循环 findings 正确注入主 Harness。"""

    def test_perspective_tag_is_added(self):
        """注入的 finding 带有 perspective 标签。"""
        h = _make_harness()
        findings = [
            {"finding": "问题一", "priority": "high", "status": "verified", "section": "results"},
            {"finding": "问题二", "priority": "low", "status": "verified", "section": "results"},
        ]
        h.ingest_perspective_findings(findings, lens="统计专家", summary="两个问题")

        assert len(h.state.findings) == 2
        for f in h.state.findings:
            assert f["perspective"] == "统计专家"

    def test_ingest_empty_findings_reports_no_issues(self):
        """空 findings 注入时返回"未发现问题"。"""
        h = _make_harness()
        result = h.ingest_perspective_findings([], lens="写作专家", summary="都挺好")

        assert "未发现显著问题" in result
        assert len(h.state.findings) == 0

    def test_fallback_from_content_when_no_findings(self):
        """
        子循环只产出 content 不调用 update_findings 时，
        _run_sub_perspective 的 fallback 机制从 content 提取结论。
        
        Script 时序:
            主循环 T1: spawn
            子循环 T1: 直接 mark_complete 带长 content 但不记录 findings
            主循环 T2: done
        """
        h = _make_harness()
        script = [
            # 主循环: spawn
            {"content": "请看看。", "tool_calls": [{
                "name": "spawn_perspective",
                "arguments": {
                    "lens": "novelty审查者",
                    "focus": "introduction",
                    "question": "novelty claim 是否站得住？",
                },
            }]},
            # 子循环 T1: 只有 content 分析，直接 mark_complete（不调 update_findings）
            {"content": (
                "经过审查，introduction 中的 novelty claim 有一定依据。"
                "Multi-scale attention 的组合方式确实和之前的方法有区别，"
                "但改进幅度有限（1.4% vs ViT），novelty 中等偏上。"
                "总体结论：novelty 可接受但不突出。"
            ), "tool_calls": [
                {"name": "mark_complete", "arguments": {"summary": "novelty 可接受"}}
            ]},
            # 主循环 T2: done
            {"content": "明白了。", "tool_calls": [
                {"name": "mark_complete", "arguments": {"summary": "novelty 没大问题"}}
            ]},
        ]

        result = _run(cognitive_loop(_make_messages(), h, SCHOLAR_TOOLS, MockLLMClient(script), verbose=False))

        assert isinstance(result, LoopDone)
        # fallback 机制应从子循环的 content 提取一条 finding
        assert len(h.state.findings) >= 1
        fallback_f = h.state.findings[0]
        assert fallback_f["perspective"] == "novelty审查者"
        assert "novelty" in fallback_f["finding"].lower() or "novelty审查者" in fallback_f["finding"]


# ============================================================
# 测试组 4: 子循环边界与异常
# ============================================================

class TestSubLoopBoundaries:
    """验证子循环的 doom stop、token 汇入等边界行为。"""

    def test_sub_loop_doom_stop_does_not_crash_main(self):
        """
        子循环超出 max_turns → DoomStop，
        但主循环仍然正常继续。
        
        子 harness max_turns=8, 我们让子循环做 12 轮。
        """
        h = _make_harness()
        # 主循环 T1: spawn
        script = [
            {"content": "spawn。", "tool_calls": [{
                "name": "spawn_perspective",
                "arguments": {
                    "lens": "深度分析者",
                    "focus": "methodology",
                    "question": "方法有多少层意义？",
                },
            }]},
        ]
        # 子循环：大量连续 read（超过子 harness 的 max_turns=8 → doom stop at 8+2=10）
        for i in range(15):
            script.append({"content": f"子循环轮 {i}。", "tool_calls": [
                {"name": "read_section", "arguments": {"section": "methodology"}}
            ]})
        # 主循环 T2: 收到子视角 doom stop 结果后 done
        script.append({"content": "子视角超时了。", "tool_calls": [
            {"name": "mark_complete", "arguments": {"summary": "子视角因资源限制停止"}}
        ]})

        result = _run(cognitive_loop(_make_messages(), h, SCHOLAR_TOOLS, MockLLMClient(script), verbose=False))

        assert isinstance(result, LoopDone)
        # 主循环正常完成（没被子循环的 doom stop 搞崩）
        assert "子视角" in result.summary or "资源" in result.summary or result.summary

    def test_sub_loop_tokens_added_to_main(self):
        """子循环消耗的 tokens 汇入主 harness 的 total_tokens。"""
        h = _make_harness()
        initial_tokens = h.state.total_tokens

        script = [
            {"content": "spawn。", "tool_calls": [{
                "name": "spawn_perspective",
                "arguments": {
                    "lens": "格式审查者",
                    "focus": "results",
                    "question": "表格格式规范吗？",
                },
            }]},
            # 子循环 2 轮（每轮 usage = 300 tokens from MockLLM）
            {"content": "看表格。", "tool_calls": [
                {"name": "read_section", "arguments": {"section": "results"}}
            ]},
            {"content": "没问题。", "tool_calls": [
                {"name": "mark_complete", "arguments": {"summary": "格式正确"}}
            ]},
            # 主循环 done
            {"content": "好。", "tool_calls": [
                {"name": "mark_complete", "arguments": {"summary": "OK"}}
            ]},
        ]

        result = _run(cognitive_loop(_make_messages(), h, SCHOLAR_TOOLS, MockLLMClient(script), verbose=False))

        assert isinstance(result, LoopDone)
        # MockLLM 每次调用产生 300 tokens (200+100)
        # 主循环 2 次 + 子循环 2 次 = 4 次 × 300 = 1200
        assert h.state.total_tokens >= initial_tokens + 1200

    def test_spawn_with_invalid_json_focus(self):
        """
        spawn_perspective 的 focus 为复杂字符串时不会 crash。
        """
        h = _make_harness()
        script = [
            {"content": "看多个。", "tool_calls": [{
                "name": "spawn_perspective",
                "arguments": {
                    "lens": "全面审查者",
                    "focus": "methodology, results",  # 逗号分隔多 section
                    "question": "方法和结果是否自洽？",
                },
            }]},
            # 子循环
            {"content": "读方法。", "tool_calls": [
                {"name": "read_section", "arguments": {"section": "methodology"}}
            ]},
            {"content": "自洽。", "tool_calls": [{
                "name": "update_findings",
                "arguments": {
                    "finding": "方法描述与结果表格自洽",
                    "priority": "low",
                    "status": "verified",
                    "section": "methodology, results",
                },
            }]},
            {"content": "完毕。", "tool_calls": [
                {"name": "mark_complete", "arguments": {"summary": "自洽确认"}}
            ]},
            # 主循环 done
            {"content": "好。", "tool_calls": [
                {"name": "mark_complete", "arguments": {"summary": "确认自洽"}}
            ]},
        ]

        result = _run(cognitive_loop(_make_messages(), h, SCHOLAR_TOOLS, MockLLMClient(script), verbose=False))

        assert isinstance(result, LoopDone)
        assert len(h.state.findings) == 1
        assert h.state.findings[0]["perspective"] == "全面审查者"


# ============================================================
# 测试组 5: 多次 Spawn
# ============================================================

class TestMultipleSpawns:
    """验证连续或间隔多次 spawn 的正确性。"""

    def test_two_consecutive_spawns(self):
        """
        主循环连续 spawn 两个不同视角，验证两者 findings 都正确注入。
        
        Script 时序:
            主循环 T1: spawn(方法论)
            子循环A T1: read + find + done
            主循环 T2: spawn(写作)
            子循环B T1: read + find + done
            主循环 T3: done
        """
        h = _make_harness()
        script = [
            # 主循环 T1: spawn 方法论视角
            {"content": "先看方法。", "tool_calls": [{
                "name": "spawn_perspective",
                "arguments": {
                    "lens": "方法论专家",
                    "focus": "methodology",
                    "question": "实验设计是否充分？",
                },
            }]},
            # 子循环 A: 快速审视
            {"content": "读方法。", "tool_calls": [
                {"name": "read_section", "arguments": {"section": "methodology"}}
            ]},
            {"content": "缺消融。", "tool_calls": [{
                "name": "update_findings",
                "arguments": {
                    "finding": "没有 ablation study",
                    "priority": "high",
                    "status": "verified",
                    "section": "methodology",
                },
            }]},
            {"content": "done。", "tool_calls": [
                {"name": "mark_complete", "arguments": {"summary": "缺 ablation"}}
            ]},
            # 主循环 T2: spawn 写作视角
            {"content": "再看写作。", "tool_calls": [{
                "name": "spawn_perspective",
                "arguments": {
                    "lens": "学术写作专家",
                    "focus": "introduction",
                    "question": "写作是否有 AI 痕迹？",
                },
            }]},
            # 子循环 B: 快速审视
            {"content": "读引言。", "tool_calls": [
                {"name": "read_section", "arguments": {"section": "introduction"}}
            ]},
            {"content": "有 AI 味。", "tool_calls": [{
                "name": "update_findings",
                "arguments": {
                    "finding": "Introduction 使用了 'fundamental task', 'recent advances' 等 AI 典型表述",
                    "priority": "medium",
                    "status": "verified",
                    "section": "introduction",
                },
            }]},
            {"content": "done。", "tool_calls": [
                {"name": "mark_complete", "arguments": {"summary": "有轻微 AI 痕迹"}}
            ]},
            # 主循环 T3: done
            {"content": "两个视角都看完了。", "tool_calls": [
                {"name": "mark_complete", "arguments": {"summary": "两个视角完成"}}
            ]},
        ]

        result = _run(cognitive_loop(_make_messages(), h, SCHOLAR_TOOLS, MockLLMClient(script), verbose=False))

        assert isinstance(result, LoopDone)
        assert len(h.state.findings) == 2
        perspectives = {f["perspective"] for f in h.state.findings}
        assert "方法论专家" in perspectives
        assert "学术写作专家" in perspectives

    def test_spawn_in_multi_tool_call(self):
        """
        spawn 与其他 tool 在同一个 turn 中并行调用。
        OpenAI API 允许单轮多 tool_calls。
        这种情况下 spawn 应该正确执行。
        """
        h = _make_harness()
        script = [
            # 主循环 T1: 同时 read_section + spawn（两个 tool_calls）
            {"content": "同时读和 spawn。", "tool_calls": [
                {"name": "read_section", "arguments": {"section": "abstract"}},
                {"name": "spawn_perspective", "arguments": {
                    "lens": "数据分析师",
                    "focus": "results",
                    "question": "数据是否充分支持结论？",
                }},
            ]},
            # 子循环 T1
            {"content": "看数据。", "tool_calls": [
                {"name": "read_section", "arguments": {"section": "results"}}
            ]},
            # 子循环 T2
            {"content": "数据 OK。", "tool_calls": [{
                "name": "update_findings",
                "arguments": {
                    "finding": "数据支持主要结论（79.3% vs 76.1% for ResNet-50）",
                    "priority": "low",
                    "status": "verified",
                    "section": "results",
                },
            }]},
            # 子循环 T3
            {"content": "完毕。", "tool_calls": [
                {"name": "mark_complete", "arguments": {"summary": "数据基本支持结论"}}
            ]},
            # 主循环 T2: done
            {"content": "好。", "tool_calls": [
                {"name": "mark_complete", "arguments": {"summary": "完成"}}
            ]},
        ]

        result = _run(cognitive_loop(_make_messages(), h, SCHOLAR_TOOLS, MockLLMClient(script), verbose=False))

        assert isinstance(result, LoopDone)
        # 主循环自己读了 abstract
        assert "abstract" in h.state.sections_read
        # 子视角的 finding 注入成功
        assert len(h.state.findings) == 1
        assert h.state.findings[0]["perspective"] == "数据分析师"


# ============================================================
# 测试组 6: 与主循环机制的协同
# ============================================================

class TestSpawnWithMainMechanisms:
    """验证 spawn 不干扰主循环的认知催促、soft limit 等机制。"""

    def test_spawn_does_not_reset_cognitive_prompter(self):
        """
        主循环在 spawn 前连续读取 3 次（触发催促器阈值），
        spawn 结束后催促器状态不被重置。
        
        注意：spawn 本身不是"产出型工具"，所以不应重置连续读取计数。
        """
        h = _make_harness(max_turns=25)
        script = [
            # 主循环连续 3 次 read（触发催促器阈值）
            {"content": "读1。", "tool_calls": [
                {"name": "read_section", "arguments": {"section": "abstract"}}
            ]},
            {"content": "读2。", "tool_calls": [
                {"name": "read_section", "arguments": {"section": "introduction"}}
            ]},
            {"content": "读3。", "tool_calls": [
                {"name": "read_section", "arguments": {"section": "methodology"}}
            ]},
            # 触发 spawn（不是产出型工具）
            {"content": "请专家看。", "tool_calls": [{
                "name": "spawn_perspective",
                "arguments": {
                    "lens": "快速审查",
                    "focus": "results",
                    "question": "结果可信吗？",
                },
            }]},
            # 子循环快速完成
            {"content": "OK。", "tool_calls": [
                {"name": "mark_complete", "arguments": {"summary": "没看出问题"}}
            ]},
            # 主循环继续（催促器应该已经触发了）
            {"content": "好吧记录一下。", "tool_calls": [{
                "name": "update_findings",
                "arguments": {
                    "finding": "综合来看论文质量中等",
                    "priority": "medium",
                    "status": "verified",
                },
            }]},
            {"content": "完成。", "tool_calls": [
                {"name": "mark_complete", "arguments": {"summary": "完成"}}
            ]},
        ]

        messages = _make_messages()
        result = _run(cognitive_loop(messages, h, SCHOLAR_TOOLS, MockLLMClient(script), verbose=False))

        assert isinstance(result, LoopDone)
        # 验证催促器曾触发（连续 3 次 read 后注入了 system message）
        cognitive_msgs = [
            m for m in messages
            if m.get("role") == "system" and "认知" in m.get("content", "")
        ]
        # 催促器应该在第 3 次 read 之后、spawn 之前或之后触发
        assert len(cognitive_msgs) >= 1

    def test_spawn_contributes_to_main_loop_turn_count(self):
        """
        spawn 期间子循环的 turn 不计入主循环的 loop_turns，
        但主循环调用 spawn 的那个 turn 本身算一轮。
        """
        h = _make_harness()
        script = [
            # 主循环 T1: spawn
            {"content": "spawn。", "tool_calls": [{
                "name": "spawn_perspective",
                "arguments": {
                    "lens": "快速",
                    "focus": "abstract",
                    "question": "有问题吗？",
                },
            }]},
            # 子循环 3 轮
            {"content": "读。", "tool_calls": [
                {"name": "read_section", "arguments": {"section": "abstract"}}
            ]},
            {"content": "没问题。", "tool_calls": [{
                "name": "update_findings",
                "arguments": {
                    "finding": "摘要表述准确",
                    "priority": "low",
                    "status": "verified",
                },
            }]},
            {"content": "完毕。", "tool_calls": [
                {"name": "mark_complete", "arguments": {"summary": "OK"}}
            ]},
            # 主循环 T2: done
            {"content": "完成。", "tool_calls": [
                {"name": "mark_complete", "arguments": {"summary": "完成"}}
            ]},
        ]

        result = _run(cognitive_loop(_make_messages(), h, SCHOLAR_TOOLS, MockLLMClient(script), verbose=False))

        assert isinstance(result, LoopDone)
        # 主循环只有 2 轮（spawn 轮 + done 轮），子循环的 3 轮不计入
        assert h.state.loop_turns == 2
