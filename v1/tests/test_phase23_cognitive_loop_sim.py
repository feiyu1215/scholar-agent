"""
Phase 23: 认知循环端到端模拟测试 (Cognitive Loop Simulation Tests)

验证目标：
    1. cognitive_loop 作为认知引擎的正确性——不依赖真实 LLM
    2. 工具组合模式（Agent 多步调用不同工具）是否正确更新 Harness 状态
    3. 信号协议（DONE / NUDGE / TALK）在循环中的正确触发和处理
    4. Harness 守护边界（doom loop、quality gate）的正确拦截行为
    5. Messages 累积和 tool_result 注入的结构正确性
    6. 跨机制协同（context 压缩、voice profile、cognitive prompter）

设计原则 (COGNITIVE_ANCHOR §5.1):
    Loop 不控制 Agent 做什么。这里我们用 MockLLM 模拟 Agent 的"意图"，
    验证 Loop + Harness 系统是否正确传导和执行这些意图。

运行: pytest tests/test_phase23_cognitive_loop_sim.py -v
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

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
# MockLLMClient — 脚本化的假 LLM
# ============================================================

class MockLLMClient:
    """
    脚本化 LLM 客户端：按预定义的 responses 序列返回结果。
    
    每次 chat_with_tools 被调用，从 script 中弹出下一个响应。
    支持 script item 格式:
    
    {"tool_calls": [{"name": "xxx", "arguments": {...}}], "content": "thinking..."}
    
    当 tool_calls 为空或脚本耗尽时，表示 Agent 停止调用工具。
    MockLLM 内部自动生成 tool_call id。
    """

    def __init__(self, script: list[dict]):
        self.script = list(script)
        self._tc_counter = 0
        self.total_calls = 0
        self.total_input_tokens = 0
        self.total_output_tokens = 0
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

def _make_harness(sections: dict[str, str] | None = None, max_turns: int = 15) -> Harness:
    """创建测试用 Harness（纯内存，不写磁盘）。"""
    tmp_dir = tempfile.mkdtemp()
    h = Harness(max_loop_turns=max_turns, memory_dir=tmp_dir)
    h._paper_loaded = True
    if sections is None:
        sections = {
            "abstract": (
                "## Abstract\n\n"
                "We propose DeepFusion, a novel method that achieves state-of-the-art "
                "performance on image classification. Our method improves accuracy by "
                "3.2% over previous best results on ImageNet."
            ),
            "introduction": (
                "## Introduction\n\n"
                "Image classification is a fundamental task in computer vision. "
                "Recent advances in deep learning have achieved remarkable progress. "
                "However, existing methods still suffer from limited feature fusion. "
                "We propose DeepFusion which leverages multi-scale attention."
            ),
            "methodology": (
                "## Methodology\n\n"
                "DeepFusion consists of three main components: "
                "(1) a backbone feature extractor using ResNet-50, "
                "(2) a multi-scale attention module (MSAM), and "
                "(3) a classification head with dropout regularization. "
                "We train for 100 epochs with Adam optimizer (lr=1e-4, beta1=0.9)."
            ),
            "results": (
                "## Results\n\n"
                "| Method | Top-1 Acc | Top-5 Acc |\n"
                "|--------|-----------|----------|\n"
                "| ResNet-50 | 76.1 | 92.9 |\n"
                "| ViT-B/16 | 77.9 | 93.9 |\n"
                "| DeepFusion | 79.3 | 94.8 |\n\n"
                "DeepFusion achieves 79.3% top-1, improving over ViT-B/16 by 1.4%."
            ),
            "conclusion": (
                "## Conclusion\n\n"
                "We presented DeepFusion, achieving SOTA results on ImageNet "
                "classification with 3.2% improvement. Future work will extend "
                "to detection and segmentation."
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
# 测试组 1: 基本循环行为
# ============================================================

class TestBasicCognitiveLoop:
    """验证认知循环的基本正确性。"""

    def test_single_read_then_done(self):
        """最简模式: read → done。"""
        h = _make_harness()
        script = [
            {"content": "读 abstract。", "tool_calls": [
                {"name": "read_section", "arguments": {"section": "abstract"}}
            ]},
            {"content": "完成。", "tool_calls": [
                {"name": "mark_complete", "arguments": {"summary": "初步了解完成"}}
            ]},
        ]
        result = _run(cognitive_loop(_make_messages(), h, SCHOLAR_TOOLS, MockLLMClient(script), verbose=False))

        assert isinstance(result, LoopDone)
        assert "abstract" in h.state.sections_read
        assert h.state.loop_turns == 2

    def test_no_tool_calls_means_immediate_done(self):
        """LLM 不发 tool_calls → 立即停止。"""
        h = _make_harness()
        script = [{"content": "没有需要的。", "tool_calls": []}]
        result = _run(cognitive_loop(_make_messages(), h, SCHOLAR_TOOLS, MockLLMClient(script), verbose=False))

        assert isinstance(result, LoopDone)
        assert "无 tool call" in result.summary
        assert h.state.loop_turns == 1

    def test_script_exhaustion_graceful_stop(self):
        """MockLLM 脚本耗尽 → 返回无 tool_calls → LoopDone。"""
        h = _make_harness()
        script = [
            {"content": "读一下。", "tool_calls": [
                {"name": "read_section", "arguments": {"section": "abstract"}}
            ]},
            # 只有 1 步，之后 MockLLM 返回 no tool_calls
        ]
        client = MockLLMClient(script)
        result = _run(cognitive_loop(_make_messages(), h, SCHOLAR_TOOLS, client, verbose=False))

        assert isinstance(result, LoopDone)
        assert client.total_calls == 2  # 第 2 次调用时脚本耗尽

    def test_unknown_tool_returns_error_message(self):
        """调用不存在的工具 → Harness 返回错误信息，循环继续。"""
        h = _make_harness()
        script = [
            {"content": "调用未知工具。", "tool_calls": [
                {"name": "nonexistent_tool", "arguments": {"x": 1}}
            ]},
            {"content": "哦，那完成吧。", "tool_calls": [
                {"name": "mark_complete", "arguments": {"summary": "遇到错误后结束"}}
            ]},
        ]
        result = _run(cognitive_loop(_make_messages(), h, SCHOLAR_TOOLS, MockLLMClient(script), verbose=False))

        assert isinstance(result, LoopDone)


# ============================================================
# 测试组 2: 信号协议
# ============================================================

class TestSignalProtocol:
    """验证 DONE / NUDGE / TALK 信号的正确处理。"""

    def test_talk_to_user_pauses_loop(self):
        """talk_to_user → LoopTalk，循环暂停。"""
        h = _make_harness()
        script = [
            {"content": "有问题。", "tool_calls": [{
                "name": "talk_to_user",
                "arguments": {"message": "这篇论文比较对象是什么？", "expects_reply": True},
            }]},
        ]
        result = _run(cognitive_loop(_make_messages(), h, SCHOLAR_TOOLS, MockLLMClient(script), verbose=False))

        assert isinstance(result, LoopTalk)
        assert "比较对象" in result.message
        assert result.expects_reply is True

    def test_nudge_on_unverified_high_finding(self):
        """high + needs_verification 的 finding → done 被 nudge 拦截。"""
        h = _make_harness()
        script = [
            # 记录 high + needs_verification
            {"content": "严重问题。", "tool_calls": [{
                "name": "update_findings",
                "arguments": {
                    "finding": "数据可能伪造",
                    "priority": "high",
                    "status": "needs_verification",
                    "section": "results",
                },
            }]},
            # 第 1 次 done → NUDGE (nudge_count=1)
            {"content": "完成。", "tool_calls": [
                {"name": "mark_complete", "arguments": {"summary": "第一次"}}
            ]},
            # 第 2 次 done → NUDGE (nudge_count=2)
            {"content": "确认。", "tool_calls": [
                {"name": "mark_complete", "arguments": {"summary": "第二次"}}
            ]},
            # 第 3 次 done → nudge_count > max_nudges(2) → 强制通过
            {"content": "坚持。", "tool_calls": [
                {"name": "mark_complete", "arguments": {"summary": "第三次"}}
            ]},
        ]
        result = _run(cognitive_loop(_make_messages(), h, SCHOLAR_TOOLS, MockLLMClient(script), verbose=False))

        assert isinstance(result, LoopDone)
        assert result.summary == "Agent 坚持完成"
        assert len(h.state.findings) == 1
        assert h.state.findings[0]["status"] == "needs_verification"

    def test_done_passes_without_high_unverified(self):
        """没有 high+needs_verification → done 直接通过。"""
        h = _make_harness()
        script = [
            {"content": "记录。", "tool_calls": [{
                "name": "update_findings",
                "arguments": {
                    "finding": "小问题",
                    "priority": "low",
                    "status": "verified",
                },
            }]},
            {"content": "完成。", "tool_calls": [
                {"name": "mark_complete", "arguments": {"summary": "低优发现已记录"}}
            ]},
        ]
        result = _run(cognitive_loop(_make_messages(), h, SCHOLAR_TOOLS, MockLLMClient(script), verbose=False))

        assert isinstance(result, LoopDone)
        assert result.summary == "低优发现已记录"

    def test_doom_loop_guard_forces_stop(self):
        """超过 max_turns + 2 → DoomStop。"""
        h = _make_harness(max_turns=3)
        # 10 轮无限读取，会在 3+2=5 轮时被截断
        script = [
            {"content": f"轮 {i}。", "tool_calls": [
                {"name": "read_section", "arguments": {"section": "abstract"}}
            ]} for i in range(10)
        ]
        result = _run(cognitive_loop(_make_messages(), h, SCHOLAR_TOOLS, MockLLMClient(script), verbose=False))

        assert isinstance(result, LoopDoomStop)
        assert h.state.loop_turns <= 6  # hard_limit = 3+2=5, check happens at turn start


# ============================================================
# 测试组 3: 工具组合模式（Phase 23 核心价值）
# ============================================================

class TestToolCombinationPatterns:
    """验证 Agent 自然组合工具产生正确的 Harness 状态变化。"""

    def test_read_find_record_pattern(self):
        """经典模式: read → 发现问题 → update_findings。"""
        h = _make_harness()
        script = [
            {"content": "看 abstract。", "tool_calls": [
                {"name": "read_section", "arguments": {"section": "abstract"}}
            ]},
            {"content": "发现 overclaim。", "tool_calls": [{
                "name": "update_findings",
                "arguments": {
                    "finding": "Abstract 声称 3.2% 但可能是对比 ResNet 而非 SOTA",
                    "priority": "high",
                    "status": "needs_verification",
                    "evidence": "improves accuracy by 3.2% over previous best",
                    "section": "abstract",
                },
            }]},
            {"content": "完成。", "tool_calls": [
                {"name": "mark_complete", "arguments": {"summary": "发现 overclaim"}}
            ]},
            # NUDGE 后继续
            {"content": "验证后完成。", "tool_calls": [{
                "name": "update_findings",
                "arguments": {
                    "finding": "Abstract 声称 3.2% 但可能是对比 ResNet 而非 SOTA",
                    "priority": "high",
                    "status": "verified",
                    "evidence": "Table shows 79.3 vs ViT 77.9 = only 1.4%",
                    "section": "results",
                },
            }]},
            {"content": "已验证。", "tool_calls": [
                {"name": "mark_complete", "arguments": {"summary": "overclaim 已确认"}}
            ]},
        ]
        result = _run(cognitive_loop(_make_messages(), h, SCHOLAR_TOOLS, MockLLMClient(script), verbose=False))

        assert isinstance(result, LoopDone)
        assert len(h.state.findings) == 2
        # 至少一条是 verified
        verified = [f for f in h.state.findings if f["status"] == "verified"]
        assert len(verified) >= 1

    def test_detect_edit_reverify_cycle(self):
        """
        核心认知闭环: detect_ai_signals → update_findings → edit_section → detect_ai_signals
        验证"检测→修改→再验证"。
        """
        h = _make_harness()
        intro_text = h.state.paper_sections["introduction"]
        script = [
            # 1. 读 introduction
            {"content": "检查写作。", "tool_calls": [
                {"name": "read_section", "arguments": {"section": "introduction"}}
            ]},
            # 2. 检测 AI 信号
            {"content": "做 AI 检测。", "tool_calls": [{
                "name": "detect_ai_signals",
                "arguments": {"text": intro_text},
            }]},
            # 3. 记录发现
            {"content": "有 AI 痕迹。", "tool_calls": [{
                "name": "update_findings",
                "arguments": {
                    "finding": "Introduction 有 AI cliché: fundamental task, remarkable progress",
                    "priority": "medium",
                    "status": "verified",
                    "evidence": "fundamental task, remarkable progress",
                    "section": "introduction",
                },
            }]},
            # 4. 编辑修改
            {"content": "修改。", "tool_calls": [{
                "name": "edit_section",
                "arguments": {
                    "section": "introduction",
                    "new_content": (
                        "## Introduction\n\n"
                        "We address the problem of multi-scale feature fusion "
                        "in image classification. Prior work uses single-scale "
                        "features from the final backbone layer; DeepFusion "
                        "instead combines features from layers 3 and 4 via "
                        "learned attention weights."
                    ),
                    "reason": "消除 AI cliché (fundamental task, remarkable progress)",
                },
            }]},
            # 5. 再次检测
            {"content": "验证修改。", "tool_calls": [{
                "name": "detect_ai_signals",
                "arguments": {
                    "text": (
                        "We address the problem of multi-scale feature fusion "
                        "in image classification. Prior work uses single-scale "
                        "features from the final backbone layer; DeepFusion "
                        "instead combines features from layers 3 and 4 via "
                        "learned attention weights."
                    ),
                },
            }]},
            # 6. 完成
            {"content": "OK。", "tool_calls": [
                {"name": "mark_complete", "arguments": {"summary": "AI 痕迹已消除"}}
            ]},
        ]
        result = _run(cognitive_loop(_make_messages(), h, SCHOLAR_TOOLS, MockLLMClient(script), verbose=False))

        assert isinstance(result, LoopDone)
        # 验证状态
        assert "introduction" in h.state.sections_read
        assert len(h.state.findings) == 1
        assert len(h.state.edits) == 1
        # 内容已被修改
        new_intro = h.state.paper_sections["introduction"]
        assert "remarkable progress" not in new_intro
        assert "multi-scale feature fusion" in new_intro

    def test_cross_section_verification(self):
        """跨 section 交叉验证: read abstract → read results → 发现不一致。"""
        h = _make_harness()
        script = [
            {"content": "看 abstract。", "tool_calls": [
                {"name": "read_section", "arguments": {"section": "abstract"}}
            ]},
            {"content": "看 results 验证。", "tool_calls": [
                {"name": "read_section", "arguments": {"section": "results"}}
            ]},
            {"content": "数据不一致！", "tool_calls": [{
                "name": "update_findings",
                "arguments": {
                    "finding": "Abstract 说 3.2% improvement 但 Results 表格最佳对比仅 1.4%",
                    "priority": "high",
                    "status": "verified",
                    "evidence": "Abstract: 3.2% | Table: 79.3-77.9=1.4% vs ViT-B/16",
                    "section": "abstract, results",
                },
            }]},
            {"content": "完成。", "tool_calls": [
                {"name": "mark_complete", "arguments": {"summary": "发现 overclaim"}}
            ]},
        ]
        result = _run(cognitive_loop(_make_messages(), h, SCHOLAR_TOOLS, MockLLMClient(script), verbose=False))

        assert isinstance(result, LoopDone)
        assert "abstract" in h.state.sections_read
        assert "results" in h.state.sections_read
        assert h.state.findings[0]["priority"] == "high"
        assert h.state.findings[0]["status"] == "verified"

    def test_multi_tool_single_turn(self):
        """单轮多个 tool calls（parallel tool use）。"""
        h = _make_harness()
        script = [
            # 单轮同时读两个 section
            {"content": "同时读两个。", "tool_calls": [
                {"name": "read_section", "arguments": {"section": "methodology"}},
                {"name": "read_section", "arguments": {"section": "results"}},
            ]},
            # 单轮记录两个 findings
            {"content": "两个问题。", "tool_calls": [
                {"name": "update_findings", "arguments": {
                    "finding": "缺少 ablation",
                    "priority": "medium", "status": "verified", "section": "methodology",
                }},
                {"name": "update_findings", "arguments": {
                    "finding": "缺少标准差",
                    "priority": "low", "status": "suggestion", "section": "results",
                }},
            ]},
            {"content": "完成。", "tool_calls": [
                {"name": "mark_complete", "arguments": {"summary": "两个问题"}}
            ]},
        ]
        result = _run(cognitive_loop(_make_messages(), h, SCHOLAR_TOOLS, MockLLMClient(script), verbose=False))

        assert isinstance(result, LoopDone)
        assert len(h.state.findings) == 2
        assert "methodology" in h.state.sections_read
        assert "results" in h.state.sections_read

    def test_reflect_then_adjust_strategy(self):
        """反思模式: read → reflect_and_plan → 调整后 done。"""
        h = _make_harness()
        script = [
            {"content": "读。", "tool_calls": [
                {"name": "read_section", "arguments": {"section": "abstract"}}
            ]},
            {"content": "记录。", "tool_calls": [{
                "name": "update_findings",
                "arguments": {
                    "finding": "声称 SOTA",
                    "priority": "medium", "status": "needs_verification",
                    "section": "abstract",
                },
            }]},
            {"content": "反思。", "tool_calls": [{
                "name": "reflect_and_plan",
                "arguments": {
                    "trigger": "两轮后看看方向",
                    "current_thinking": "需要看 results 验证 SOTA claim",
                },
            }]},
            {"content": "完成。", "tool_calls": [
                {"name": "mark_complete", "arguments": {"summary": "完成"}}
            ]},
        ]
        result = _run(cognitive_loop(_make_messages(), h, SCHOLAR_TOOLS, MockLLMClient(script), verbose=False))

        assert isinstance(result, LoopDone)
        assert hasattr(h, '_reflection_log')
        assert len(h._reflection_log) == 1


# ============================================================
# 测试组 4: Harness 机制在循环中的协同
# ============================================================

class TestHarnessMechanismsInLoop:
    """验证 Harness 各子系统在完整循环中正确协同。"""

    def test_cognitive_prompter_fires_after_consecutive_reads(self):
        """连续读取不记录 → 认知催促器注入 system message。"""
        h = _make_harness(max_turns=15)
        # 5 轮纯读取，触发催促器（阈值 3 轮）
        script = [
            {"content": f"读 {s}。", "tool_calls": [
                {"name": "read_section", "arguments": {"section": s}}
            ]} for s in ["abstract", "introduction", "methodology", "results", "conclusion"]
        ] + [
            {"content": "终于记录。", "tool_calls": [{
                "name": "update_findings",
                "arguments": {
                    "finding": "论文完整",
                    "priority": "low", "status": "verified",
                },
            }]},
            {"content": "完成。", "tool_calls": [
                {"name": "mark_complete", "arguments": {"summary": "完成"}}
            ]},
        ]
        messages = _make_messages()
        result = _run(cognitive_loop(messages, h, SCHOLAR_TOOLS, MockLLMClient(script), verbose=False))

        assert isinstance(result, LoopDone)
        # 检查是否有认知催促的 system message
        cognitive_msgs = [
            m for m in messages
            if m.get("role") == "system" and "认知" in m.get("content", "")
        ]
        assert len(cognitive_msgs) >= 1

    def test_voice_profile_accumulates_during_reads(self):
        """读取 section 时自动累积 voice profile。"""
        h = _make_harness()
        script = [
            {"content": "读。", "tool_calls": [
                {"name": "read_section", "arguments": {"section": "methodology"}}
            ]},
            {"content": "再读。", "tool_calls": [
                {"name": "read_section", "arguments": {"section": "results"}}
            ]},
            {"content": "完成。", "tool_calls": [
                {"name": "mark_complete", "arguments": {"summary": "OK"}}
            ]},
        ]
        result = _run(cognitive_loop(_make_messages(), h, SCHOLAR_TOOLS, MockLLMClient(script), verbose=False))

        assert isinstance(result, LoopDone)
        # methodology 和 results 的内容 >= 200 字符，应该触发 voice profile
        assert h.state.voice_profile is not None
        assert h.state.voice_profile.total_words_analyzed > 0

    def test_section_digests_generated_on_read(self):
        """读取 section 时自动生成 digest。"""
        h = _make_harness()
        script = [
            {"content": "读两个。", "tool_calls": [
                {"name": "read_section", "arguments": {"section": "abstract"}},
                {"name": "read_section", "arguments": {"section": "methodology"}},
            ]},
            {"content": "完成。", "tool_calls": [
                {"name": "mark_complete", "arguments": {"summary": "OK"}}
            ]},
        ]
        result = _run(cognitive_loop(_make_messages(), h, SCHOLAR_TOOLS, MockLLMClient(script), verbose=False))

        assert isinstance(result, LoopDone)
        assert "abstract" in h.state.section_digests
        assert "methodology" in h.state.section_digests
        assert len(h.state.section_digests["abstract"]) > 10

    def test_soft_turn_limit_injects_self_assessment(self):
        """Phase 28: 第 15 轮触发认知自评提问。"""
        h = _make_harness(max_turns=50)
        # 需要 15+ 轮脚本来触发自评
        script = [
            {"content": f"轮 {i}。", "tool_calls": [
                {"name": "read_section", "arguments": {"section": "abstract"}}
            ]} for i in range(18)
        ]
        messages = _make_messages()
        result = _run(cognitive_loop(messages, h, SCHOLAR_TOOLS, MockLLMClient(script), verbose=False))

        # 脚本18轮后耗尽，应正常结束（stop）
        # 检查是否注入了自评提问
        self_assessments = [
            m for m in messages
            if m.get("role") == "system" and "自评" in m.get("content", "")
        ]
        assert len(self_assessments) >= 1, (
            f"Expected self-assessment prompt at turn 15, found none. "
            f"System messages: {[m.get('content', '')[:50] for m in messages if m.get('role') == 'system']}"
        )

    def test_post_edit_verify_feedback_in_tool_result(self):
        """edit_section 后 tool result 包含 post-edit verification 反馈。"""
        h = _make_harness()
        # 先读以建立 voice profile
        script = [
            {"content": "读。", "tool_calls": [
                {"name": "read_section", "arguments": {"section": "methodology"}}
            ]},
            {"content": "改。", "tool_calls": [{
                "name": "edit_section",
                "arguments": {
                    "section": "methodology",
                    "new_content": "## Methodology\n\nWe use SGD with momentum 0.9 and lr=0.01. Training on 2xA100 for 50 epochs.",
                    "reason": "简化方法描述",
                },
            }]},
            {"content": "完成。", "tool_calls": [
                {"name": "mark_complete", "arguments": {"summary": "改完"}}
            ]},
        ]
        messages = _make_messages()
        result = _run(cognitive_loop(messages, h, SCHOLAR_TOOLS, MockLLMClient(script), verbose=False))

        assert isinstance(result, LoopDone)
        # 检查 edit_section 的 tool_result 包含反馈
        tool_results = [m.get("content", "") for m in messages if m.get("role") == "tool"]
        edit_results = [r for r in tool_results if "已修改 section" in r]
        assert len(edit_results) == 1

    def test_context_compression_for_long_sessions(self):
        """长对话时 compress_messages 产生有效压缩。"""
        h = _make_harness(max_turns=20)
        # 10 轮读取产生足够多 messages
        script = [
            {"content": f"读 {s}。", "tool_calls": [
                {"name": "read_section", "arguments": {"section": s}}
            ]} for s in ["abstract", "introduction", "methodology", "results", "conclusion"] * 2
        ] + [
            {"content": "完成。", "tool_calls": [
                {"name": "mark_complete", "arguments": {"summary": "OK"}}
            ]},
        ]
        messages = _make_messages()
        result = _run(cognitive_loop(messages, h, SCHOLAR_TOOLS, MockLLMClient(script), verbose=False))

        assert isinstance(result, LoopDone)
        # 验证压缩逻辑存在（compress_messages 返回较短的列表）
        compressed = h.compress_messages(messages)
        orig_len = sum(len(m.get("content", "") or "") for m in messages)
        comp_len = sum(len(m.get("content", "") or "") for m in compressed)
        # messages 足够长时应该有压缩效果
        assert comp_len <= orig_len


# ============================================================
# 测试组 5: Messages 结构正确性
# ============================================================

class TestMessagesStructure:
    """验证 messages 在循环中的累积结构。"""

    def test_messages_grow_with_correct_roles(self):
        """每轮产生 assistant + tool messages。"""
        h = _make_harness()
        script = [
            {"content": "读。", "tool_calls": [
                {"name": "read_section", "arguments": {"section": "abstract"}}
            ]},
            {"content": "完成。", "tool_calls": [
                {"name": "mark_complete", "arguments": {"summary": "OK"}}
            ]},
        ]
        messages = _make_messages()
        initial = len(messages)
        result = _run(cognitive_loop(messages, h, SCHOLAR_TOOLS, MockLLMClient(script), verbose=False))

        assert isinstance(result, LoopDone)
        # Turn 1: +1 assistant + 1 tool = 2
        # Turn 2: +1 assistant + 1 tool(done) = 2
        added = len(messages) - initial
        assert added == 4

        # 验证 role 序列
        new_msgs = messages[initial:]
        assert new_msgs[0]["role"] == "assistant"
        assert new_msgs[1]["role"] == "tool"
        assert new_msgs[2]["role"] == "assistant"
        assert new_msgs[3]["role"] == "tool"

    def test_multi_tool_generates_multiple_tool_results(self):
        """单轮多 tool_calls → 多条 tool result messages。"""
        h = _make_harness()
        script = [
            {"content": "并行读。", "tool_calls": [
                {"name": "read_section", "arguments": {"section": "abstract"}},
                {"name": "read_section", "arguments": {"section": "results"}},
            ]},
            {"content": "完成。", "tool_calls": [
                {"name": "mark_complete", "arguments": {"summary": "OK"}}
            ]},
        ]
        messages = _make_messages()
        initial = len(messages)
        _run(cognitive_loop(messages, h, SCHOLAR_TOOLS, MockLLMClient(script), verbose=False))

        # Turn 1: +1 assistant + 2 tool = 3
        # Turn 2: +1 assistant + 1 tool = 2
        added = len(messages) - initial
        assert added == 5

    def test_assistant_msg_contains_tool_calls_field(self):
        """assistant message 中包含 tool_calls 结构。"""
        h = _make_harness()
        script = [
            {"content": "读。", "tool_calls": [
                {"name": "read_section", "arguments": {"section": "abstract"}}
            ]},
            {"content": None, "tool_calls": []},  # no tool_calls → stop
        ]
        messages = _make_messages()
        _run(cognitive_loop(messages, h, SCHOLAR_TOOLS, MockLLMClient(script), verbose=False))

        # 找到 assistant message
        assistant_msgs = [m for m in messages if m.get("role") == "assistant"]
        assert len(assistant_msgs) >= 1
        first_assistant = assistant_msgs[0]
        assert "tool_calls" in first_assistant
        assert len(first_assistant["tool_calls"]) == 1
        assert first_assistant["tool_calls"][0]["function"]["name"] == "read_section"

    def test_tool_result_has_correct_tool_call_id(self):
        """tool result 的 tool_call_id 与 assistant 中的 id 对应。"""
        h = _make_harness()
        script = [
            {"content": "读。", "tool_calls": [
                {"name": "read_section", "arguments": {"section": "abstract"}}
            ]},
            {"content": None, "tool_calls": []},
        ]
        messages = _make_messages()
        _run(cognitive_loop(messages, h, SCHOLAR_TOOLS, MockLLMClient(script), verbose=False))

        initial_count = 2  # system + user
        assistant_msg = messages[initial_count]
        tool_msg = messages[initial_count + 1]

        assert assistant_msg["role"] == "assistant"
        assert tool_msg["role"] == "tool"
        # id 匹配
        tc_id = assistant_msg["tool_calls"][0]["id"]
        assert tool_msg["tool_call_id"] == tc_id
