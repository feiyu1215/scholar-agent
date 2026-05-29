"""
Phase 30: 行动优于建议 (Action Over Suggestion) 测试

验证目标：
    1. Agent 在用户请求修改时选择 edit_section 而非 talk_to_user 给文字建议
    2. Agent 在自己发现问题且改法明确时主动 edit
    3. Agent 在问题根因不清时先 read/investigate 再改（不盲目动手）
    4. identity.py 中 §15 的新措辞正确注入到 system prompt

设计原则 (COGNITIVE_ANCHOR §2.1 + §4.1):
    Agent 的本质是认知驱动行动。人类专家被要求改论文时直接改，
    不会口头建议"你可以这样改"。

运行: pytest tests/test_phase30_action_over_suggestion.py -v
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.loop import cognitive_loop, LoopDone, LoopTalk, LoopDoomStop
from core.harness import Harness
from core.identity import SCHOLAR_IDENTITY, SCHOLAR_TOOLS, build_system_prompt

# 防止 dotenv 环境污染导致 Checker 在 mark_complete 时触发 nudge
import core.checker as _checker_mod
_checker_mod.CHECKER_ENABLED = False


# ============================================================
# MockLLMClient
# ============================================================

class MockLLMClient:
    """脚本化 LLM 客户端，用于验证 Agent 行为路径。"""

    def __init__(self, script: list[dict]):
        self.script = list(script)
        self._tc_counter = 0
        self.total_calls = 0
        self.model = "mock-scripted"

    async def chat_with_tools(self, messages, tools, **kwargs) -> dict:
        self.total_calls += 1
        if not self.script:
            return {"content": "(脚本耗尽)", "tool_calls": [], "finish_reason": "stop",
                    "usage": {"prompt_tokens": 100, "completion_tokens": 50}}

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

    def stats(self):
        return {"provider": "mock", "model": self.model, "total_calls": self.total_calls}


# ============================================================
# 辅助
# ============================================================

def _make_harness(max_turns: int = 50) -> Harness:
    tmp_dir = tempfile.mkdtemp()
    h = Harness(max_loop_turns=max_turns, memory_dir=tmp_dir)
    h._paper_loaded = True
    h.state.paper_sections = {
        "abstract": (
            "## Abstract\n\n"
            "We propose DeepFusion, a novel method that achieves state-of-the-art "
            "performance on image classification. Our method improves accuracy by "
            "3.2% over previous best results on ImageNet."
        ),
        "introduction": (
            "## Introduction\n\n"
            "Image classification has been a cornerstone of computer vision. "
            "Recent advances in transformer-based architectures have pushed "
            "performance boundaries significantly. However, existing methods "
            "suffer from computational inefficiency at scale. "
            "We propose DeepFusion to address this limitation."
        ),
        "methodology": (
            "## Methodology\n\n"
            "DeepFusion uses a multi-scale attention mechanism combined with "
            "residual connections. We train on ImageNet-1K for 300 epochs. "
            "The key insight is that multi-scale features can be efficiently "
            "fused through a lightweight gating mechanism."
        ),
        "results": (
            "## Results\n\n"
            "| Method | Top-1 Acc |\n|--------|----------|\n"
            "| ResNet-50 | 76.1 |\n| ViT-B/16 | 77.9 |\n"
            "| DeepFusion | 79.3 |\n\n"
            "DeepFusion achieves 79.3% top-1 accuracy, improving "
            "over ViT-B/16 by 1.4 percentage points."
        ),
    }
    return h


def _make_messages_with_edit_request(harness: Harness) -> list[dict]:
    """构建包含 '帮我改一下' 请求的 messages。"""
    ws = harness.format_context()
    sp = build_system_prompt(identity=SCHOLAR_IDENTITY, workspace_state=ws)
    return [
        {"role": "system", "content": sp},
        {"role": "user", "content": "帮我改一下 introduction 的逻辑，让它更流畅。"},
    ]


def _make_messages_with_audit_first(harness: Harness) -> list[dict]:
    """构建需要先审后改的 messages。"""
    ws = harness.format_context()
    sp = build_system_prompt(identity=SCHOLAR_IDENTITY, workspace_state=ws)
    return [
        {"role": "system", "content": sp},
        {"role": "user", "content": "请审阅这篇论文，把有问题的地方直接帮我改了。"},
    ]


def _run(coro):
    return asyncio.run(coro)


# ============================================================
# Test 1: 用户请求修改 → Agent 调用 edit_section（正确路径）
# ============================================================

class TestEditOnUserRequest:
    """验证：当用户明确说'帮我改'时，Agent 选择 edit_section 而非 talk_to_user。"""

    def test_direct_edit_on_explicit_request(self):
        """用户说'帮我改一下 introduction'→ Agent 读取后直接 edit。"""
        harness = _make_harness()
        script = [
            # Turn 1: Agent 先读取 section（合理——先看原文再改）
            {
                "content": "用户请求修改 introduction，我先读取原文了解当前状态。",
                "tool_calls": [
                    {"name": "read_section", "arguments": {"section": "introduction"}}
                ]
            },
            # Turn 2: Agent 直接用 edit_section 修改（行动优于建议）
            {
                "content": "原文逻辑跳跃：从'transformer advances'直接到'we propose'缺少过渡。我来直接改。",
                "tool_calls": [
                    {
                        "name": "edit_section",
                        "arguments": {
                            "section": "introduction",
                            "new_content": (
                                "## Introduction\n\n"
                                "Image classification has been a cornerstone of computer vision. "
                                "Recent advances in transformer-based architectures have pushed "
                                "performance boundaries significantly. However, these methods "
                                "often suffer from computational inefficiency at scale, "
                                "limiting their practical deployment. "
                                "To bridge this gap between accuracy and efficiency, "
                                "we propose DeepFusion—a method that leverages multi-scale "
                                "feature fusion to achieve strong performance with lower cost."
                            ),
                            "reason": "逻辑过渡不畅：原文从'existing methods suffer'直接跳到'we propose'，缺少连接语。增加了 bridge sentence 说明 DeepFusion 如何衔接问题与解决方案。"
                        }
                    }
                ]
            },
            # Turn 3: 完成
            {
                "content": "已完成修改。",
                "tool_calls": [
                    {"name": "mark_complete", "arguments": {"summary": "已修改 introduction 的逻辑过渡"}}
                ]
            },
        ]

        messages = _make_messages_with_edit_request(harness)
        client = MockLLMClient(script)
        result = _run(cognitive_loop(messages, harness, SCHOLAR_TOOLS, client, verbose=False))

        # 验证结果
        assert isinstance(result, LoopDone)
        assert len(harness.state.edits) == 1
        assert "introduction" in harness.state.edits[0]["section"]
        assert "逻辑" in harness.state.edits[0]["reason"]

    def test_anti_pattern_talk_instead_of_edit(self):
        """反模式：Agent 用 talk_to_user 给文字建议而非 edit——验证此行为路径的存在性。
        
        这个测试记录了 Phase 29 中观察到的 anti-pattern：Agent 在用户说"帮我改"时
        选择 talk_to_user 给出文字建议。Phase 30 的认知注入目的是减少这种行为的发生。
        """
        harness = _make_harness()
        script = [
            # Turn 1: 读取
            {
                "content": "让我先看看 introduction。",
                "tool_calls": [
                    {"name": "read_section", "arguments": {"section": "introduction"}}
                ]
            },
            # Turn 2: 反模式——用 talk 而非 edit
            {
                "content": "我来给出建议。",
                "tool_calls": [
                    {
                        "name": "talk_to_user",
                        "arguments": {
                            "message": "建议将第二句改为：'However, these methods often suffer from...'这样逻辑更流畅。",
                            "expects_reply": False,
                        }
                    }
                ]
            },
        ]

        messages = _make_messages_with_edit_request(harness)
        client = MockLLMClient(script)
        result = _run(cognitive_loop(messages, harness, SCHOLAR_TOOLS, client, verbose=False))

        # 此路径返回 LoopTalk（Agent 暂停等用户），但 0 edits——这是我们要避免的行为
        assert isinstance(result, LoopTalk)
        assert len(harness.state.edits) == 0  # 没有实际修改
        assert "建议" in result.message  # Agent 在"建议"而非"行动"


# ============================================================
# Test 2: Agent 自主发现问题后直接修改
# ============================================================

class TestProactiveEdit:
    """验证：Agent 在审阅中发现问题且改法明确时，主动使用 edit_section。"""

    def test_edit_after_discovering_issue(self):
        """Agent 审阅时发现数据不一致 → 直接 edit 修正。"""
        harness = _make_harness()
        # 在 abstract 中制造一个不一致：说 "improves by 3.2%" 但 results 表格显示只有 1.4%
        harness.state.paper_sections["abstract"] = (
            "## Abstract\n\n"
            "We propose DeepFusion, achieving 3.2% improvement over the previous best."
        )
        harness.state.paper_sections["results"] = (
            "## Results\n\n"
            "| Method | Top-1 Acc |\n|--------|----------|\n"
            "| ViT-B/16 | 77.9 |\n| DeepFusion | 79.3 |\n\n"
            "DeepFusion improves over ViT-B/16 by 1.4 percentage points."
        )

        script = [
            # Turn 1: 读 abstract
            {
                "content": "审阅开始，先看 abstract。",
                "tool_calls": [
                    {"name": "read_section", "arguments": {"section": "abstract"}}
                ]
            },
            # Turn 2: 读 results 验证
            {
                "content": "Abstract 说 3.2% improvement，让我去 results 核实。",
                "tool_calls": [
                    {"name": "read_section", "arguments": {"section": "results"}}
                ]
            },
            # Turn 3: 发现不一致 → 记录 finding + 直接 edit
            {
                "content": "数据不一致：abstract 说 3.2% 但 results 显示只有 1.4% (79.3 - 77.9)。修正 abstract。",
                "tool_calls": [
                    {
                        "name": "update_findings",
                        "arguments": {
                            "finding": "Abstract 数据不一致：声称 3.2% improvement，但 results 表格显示 DeepFusion 79.3 vs ViT-B/16 77.9，实际仅 1.4%",
                            "evidence": "Abstract: '3.2% improvement'; Results table: 79.3 - 77.9 = 1.4%",
                            "section": "abstract",
                            "priority": "high",
                            "status": "verified",
                        }
                    },
                    {
                        "name": "edit_section",
                        "arguments": {
                            "section": "abstract",
                            "new_content": "## Abstract\n\nWe propose DeepFusion, achieving 1.4 percentage point improvement over the previous best (ViT-B/16).",
                            "reason": "数据修正：原文称 3.2% improvement，但 results 表格显示实际为 1.4pp (79.3 - 77.9)。修正为准确数值。"
                        }
                    }
                ]
            },
            # Turn 4: 完成
            {
                "content": "数据不一致已修正。",
                "tool_calls": [
                    {"name": "mark_complete", "arguments": {"summary": "修正了 abstract 的数据不一致 (3.2% → 1.4pp)"}}
                ]
            },
        ]

        ws = harness.format_context()
        sp = build_system_prompt(identity=SCHOLAR_IDENTITY, workspace_state=ws)
        messages = [
            {"role": "system", "content": sp},
            {"role": "user", "content": "请审阅这篇论文，如果发现数据不一致的地方直接帮我改了。"},
        ]
        client = MockLLMClient(script)
        result = _run(cognitive_loop(messages, harness, SCHOLAR_TOOLS, client, verbose=False))

        assert isinstance(result, LoopDone)
        assert len(harness.state.findings) == 1
        assert harness.state.findings[0]["priority"] == "high"
        assert len(harness.state.edits) == 1
        assert "1.4" in harness.state.paper_sections["abstract"]

    def test_investigate_before_edit_when_unclear(self):
        """当问题根因不清时，Agent 应先调查再决定是否修改。"""
        harness = _make_harness()
        script = [
            # Turn 1: 读取论文结构
            {
                "content": "用户要我审改，先看论文结构。",
                "tool_calls": [
                    {"name": "read_section", "arguments": {"section": "list"}}
                ]
            },
            # Turn 2: 读 abstract
            {
                "content": "从 abstract 开始了解论文核心主张。",
                "tool_calls": [
                    {"name": "read_section", "arguments": {"section": "abstract"}}
                ]
            },
            # Turn 3: 读 methodology
            {
                "content": "看看方法论部分是否有明显问题。",
                "tool_calls": [
                    {"name": "read_section", "arguments": {"section": "methodology"}}
                ]
            },
            # Turn 4: 记录发现（问题需要作者确认，不盲目改）
            {
                "content": "方法论中'multi-scale attention'的具体实现细节不足，但这可能是因为页数限制。先记录为 needs_verification 而非直接改。",
                "tool_calls": [
                    {
                        "name": "update_findings",
                        "arguments": {
                            "finding": "Methodology 缺乏 multi-scale attention 的具体实现细节（层数、head 数、fusion 策略），无法判断是否需要补充还是有 appendix",
                            "section": "methodology",
                            "priority": "medium",
                            "status": "needs_verification",
                        }
                    }
                ]
            },
            # Turn 5: 和用户确认
            {
                "content": "这个问题我不确定是否应该直接改——可能是作者有意精简，需要确认。",
                "tool_calls": [
                    {
                        "name": "talk_to_user",
                        "arguments": {
                            "message": "Methodology 中 multi-scale attention 的实现细节较少。这是因为页数限制还是确实需要补充？如果需要补充，我可以帮你扩写。",
                            "expects_reply": True,
                        }
                    }
                ]
            },
        ]

        messages = _make_messages_with_audit_first(harness)
        client = MockLLMClient(script)
        result = _run(cognitive_loop(messages, harness, SCHOLAR_TOOLS, client, verbose=False))

        # 合理路径：问题不清时先问用户，而非盲目修改
        assert isinstance(result, LoopTalk)
        assert len(harness.state.edits) == 0  # 没有盲目修改
        assert len(harness.state.findings) == 1  # 但记录了发现
        assert result.expects_reply is True  # 需要用户回复


# ============================================================
# Test 3: identity 文本验证
# ============================================================

class TestIdentityText:
    """验证 SCHOLAR_IDENTITY 中包含 Phase 30 新增的认知内容。"""

    def test_action_over_suggestion_in_identity(self):
        """identity 包含'行动优于建议'的关键措辞。"""
        assert "行动优于建议" in SCHOLAR_IDENTITY
        assert "Action Over Suggestion" in SCHOLAR_IDENTITY

    def test_default_reaction_is_edit(self):
        """identity 明确声明 edit_section 是默认反应。"""
        assert "默认反应是用 edit_section 动手改" in SCHOLAR_IDENTITY

    def test_anti_pattern_awareness(self):
        """identity 包含反模式警觉。"""
        assert "反模式警觉" in SCHOLAR_IDENTITY
        assert "我为什么不直接改" in SCHOLAR_IDENTITY

    def test_key_distinction(self):
        """identity 区分了助手行为和专家行为。"""
        assert "用文字描述" in SCHOLAR_IDENTITY
        assert "助手的行为" in SCHOLAR_IDENTITY
        assert "专家的行为" in SCHOLAR_IDENTITY

    def test_exceptions_documented(self):
        """identity 明确了不急着改的例外情况。"""
        assert "问题根因不清" in SCHOLAR_IDENTITY
        assert "损害作者核心论点" in SCHOLAR_IDENTITY


# ============================================================
# Test 4: edit_section 工具的反馈机制
# ============================================================

class TestEditFeedback:
    """验证 edit_section 执行后 harness 正确更新状态。"""

    def test_edit_updates_paper_sections(self):
        """edit 后 paper_sections 内容被更新。"""
        harness = _make_harness()
        original = harness.state.paper_sections["introduction"]

        new_content = "## Introduction\n\nRewritten introduction with better logic flow."
        result = harness.execute_tool("edit_section", {
            "section": "introduction",
            "new_content": new_content,
            "reason": "改善逻辑流畅性"
        })

        assert "已修改" in result
        assert harness.state.paper_sections["introduction"] == new_content
        assert harness.state.paper_sections["introduction"] != original

    def test_edit_records_in_edits_list(self):
        """edit 后记录到 state.edits。"""
        harness = _make_harness()
        harness.execute_tool("edit_section", {
            "section": "introduction",
            "new_content": "## Introduction\n\nNew content.",
            "reason": "测试修改记录"
        })

        assert len(harness.state.edits) == 1
        assert "introduction" in harness.state.edits[0]["section"]
        assert "测试修改记录" == harness.state.edits[0]["reason"]

    def test_multiple_edits_tracked(self):
        """多次 edit 都被追踪。"""
        harness = _make_harness()
        harness.execute_tool("edit_section", {
            "section": "introduction",
            "new_content": "## Introduction\n\nEdit 1.",
            "reason": "第一次修改"
        })
        harness.execute_tool("edit_section", {
            "section": "methodology",
            "new_content": "## Methodology\n\nEdit 2.",
            "reason": "第二次修改"
        })

        assert len(harness.state.edits) == 2
        sections_edited = [e["section"] for e in harness.state.edits]
        assert "introduction" in sections_edited
        assert "methodology" in sections_edited


# ============================================================
# Test 5: 完整多轮路径 — 审阅发现 → 用户要求改 → 直接 edit
# ============================================================

class TestAuditThenEditFlow:
    """验证完整路径：Agent 先审后改的认知连贯性。"""

    def test_audit_then_edit_on_user_request(self):
        """第一轮审阅 → 第二轮用户说'帮我改' → Agent 直接 edit。"""
        harness = _make_harness()

        # 第一轮：审阅并发现问题
        script_round1 = [
            {
                "content": "读 abstract 看核心主张。",
                "tool_calls": [
                    {"name": "read_section", "arguments": {"section": "abstract"}}
                ]
            },
            {
                "content": "发现 overclaim：'state-of-the-art' 需要验证。",
                "tool_calls": [
                    {
                        "name": "update_findings",
                        "arguments": {
                            "finding": "Abstract 使用 'state-of-the-art' 但仅比较了 2 个 baseline (ResNet-50, ViT-B/16)，不足以支撑 SOTA claim",
                            "evidence": "'achieves state-of-the-art performance' — 但 results 只对比了 ResNet-50 和 ViT-B/16",
                            "section": "abstract",
                            "priority": "high",
                            "status": "verified",
                        }
                    }
                ]
            },
            {
                "content": "向用户报告发现。",
                "tool_calls": [
                    {
                        "name": "talk_to_user",
                        "arguments": {
                            "message": "我发现 abstract 中的 'state-of-the-art' claim 缺乏足够 baseline 支撑（仅对比了 2 个方法）。要我帮你弱化这个表述吗？",
                            "expects_reply": True,
                        }
                    }
                ]
            },
        ]

        ws = harness.format_context()
        sp = build_system_prompt(identity=SCHOLAR_IDENTITY, workspace_state=ws)
        messages = [
            {"role": "system", "content": sp},
            {"role": "user", "content": "请先审阅一下 abstract 有没有问题。"},
        ]
        client1 = MockLLMClient(script_round1)
        result1 = _run(cognitive_loop(messages, harness, SCHOLAR_TOOLS, client1, verbose=False))

        assert isinstance(result1, LoopTalk)
        assert len(harness.state.findings) == 1

        # 第二轮：用户回复"帮我改" → Agent 直接 edit
        harness.new_conversation_turn()
        messages.append({"role": "user", "content": "好的，帮我弱化那个 state-of-the-art 的表述。"})

        script_round2 = [
            {
                "content": "用户确认了，我直接修改 abstract 弱化 overclaim。",
                "tool_calls": [
                    {
                        "name": "edit_section",
                        "arguments": {
                            "section": "abstract",
                            "new_content": (
                                "## Abstract\n\n"
                                "We propose DeepFusion, a novel method that achieves "
                                "competitive performance on image classification. "
                                "Our method improves accuracy by 3.2% over ViT-B/16 on ImageNet."
                            ),
                            "reason": "弱化 overclaim：'state-of-the-art' → 'competitive performance'，因为仅对比了 2 个 baseline 不足以支撑 SOTA claim。"
                        }
                    }
                ]
            },
            {
                "content": "修改完成。",
                "tool_calls": [
                    {"name": "mark_complete", "arguments": {"summary": "已弱化 abstract 中的 overclaim 表述"}}
                ]
            },
        ]

        client2 = MockLLMClient(script_round2)
        result2 = _run(cognitive_loop(messages, harness, SCHOLAR_TOOLS, client2, verbose=False))

        assert isinstance(result2, LoopDone)
        assert len(harness.state.edits) == 1
        # 验证 abstract 确实被更新
        assert "competitive performance" in harness.state.paper_sections["abstract"]
        assert "state-of-the-art" not in harness.state.paper_sections["abstract"]
