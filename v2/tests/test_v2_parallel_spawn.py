"""
tests/test_v2_parallel_spawn.py — C4 认知分裂: spawn_parallel_readers 测试

覆盖:
1. tool_spawn_parallel_readers 参数校验（空数组、超限、缺字段）
2. 正常路径：生成 __PARALLEL_SPAWN__ 信号 + payload 完整
3. phase gating: 只在 deep_review 阶段可见
4. Harness wrapper 集成：直通调用
5. _run_parallel_perspectives 集成（mock LLM）：并行执行 + findings 合并 + token 汇总
6. SCHOLAR_TOOLS schema 检查
"""

import sys
import json
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from core.tool_handlers.misc import tool_spawn_parallel_readers, _MAX_PARALLEL_READERS
from core.harness import Harness
from core.phases import Phase


# ============================================================
# 1. 参数校验
# ============================================================

class TestParallelSpawnValidation:
    """tool_spawn_parallel_readers 参数校验。"""

    def test_empty_readers_rejected(self):
        result = tool_spawn_parallel_readers({"readers": []})
        assert "至少一个" in result
        assert "__PARALLEL_SPAWN__" not in result

    def test_missing_readers_key(self):
        result = tool_spawn_parallel_readers({})
        assert "至少一个" in result

    def test_readers_not_list(self):
        result = tool_spawn_parallel_readers({"readers": "invalid"})
        assert "数组" in result

    def test_exceeds_max_parallel(self):
        # _MAX_PARALLEL_READERS = 8, 所以需要 9 个才会超限
        readers = [{"lens": f"expert_{i}", "focus": "intro", "question": "ok?"} for i in range(9)]
        result = tool_spawn_parallel_readers({"readers": readers})
        assert f"最多并行 {_MAX_PARALLEL_READERS}" in result
        assert "__PARALLEL_SPAWN__" not in result

    def test_missing_lens_in_reader(self):
        readers = [{"lens": "", "focus": "intro", "question": "what?"}]
        result = tool_spawn_parallel_readers({"readers": readers})
        assert "缺少 lens 或 question" in result

    def test_missing_question_in_reader(self):
        readers = [{"lens": "expert", "focus": "intro", "question": ""}]
        result = tool_spawn_parallel_readers({"readers": readers})
        assert "缺少 lens 或 question" in result

    def test_non_dict_reader_rejected(self):
        readers = ["not a dict", {"lens": "ok", "focus": "x", "question": "y"}]
        result = tool_spawn_parallel_readers({"readers": readers})
        assert "不是有效对象" in result


# ============================================================
# 2. 正常路径：信号生成
# ============================================================

class TestParallelSpawnSignal:
    """正常路径：生成正确的 __PARALLEL_SPAWN__ 信号。"""

    def test_two_readers_generates_valid_signal(self):
        readers = [
            {"lens": "stats_expert", "focus": "methodology, results", "question": "Is 2SLS valid?"},
            {"lens": "writing_expert", "focus": "introduction", "question": "Is the narrative clear?"},
        ]
        result = tool_spawn_parallel_readers({"readers": readers})
        assert result.startswith("__PARALLEL_SPAWN__|")

        payload = json.loads(result.split("|", 1)[1])
        assert len(payload["readers"]) == 2
        assert payload["readers"][0]["lens"] == "stats_expert"
        assert payload["readers"][1]["lens"] == "writing_expert"

    def test_four_readers_max(self):
        readers = [
            {"lens": f"expert_{i}", "focus": f"section_{i}", "question": f"q{i}?"}
            for i in range(4)
        ]
        result = tool_spawn_parallel_readers({"readers": readers})
        assert result.startswith("__PARALLEL_SPAWN__|")
        payload = json.loads(result.split("|", 1)[1])
        assert len(payload["readers"]) == 4

    def test_focus_defaults_to_full(self):
        readers = [
            {"lens": "x", "focus": "", "question": "q?"},
            {"lens": "y", "focus": "  ", "question": "q2?"},
        ]
        result = tool_spawn_parallel_readers({"readers": readers})
        payload = json.loads(result.split("|", 1)[1])
        assert payload["readers"][0]["focus"] == "full"
        assert payload["readers"][1]["focus"] == "full"


# ============================================================
# 3. Phase Gating
# ============================================================

class TestParallelSpawnPhaseGating:
    """spawn_parallel_readers 只在 deep_review 阶段可用。"""

    def test_visible_in_deep_review(self):
        h = Harness()
        tools = h.tool_registry.get_tools_for_phase("deep_review")
        assert "spawn_parallel_readers" in tools

    def test_not_visible_in_initial_scan(self):
        h = Harness()
        tools = h.tool_registry.get_tools_for_phase("initial_scan")
        assert "spawn_parallel_readers" not in tools

    def test_not_visible_in_editing(self):
        h = Harness()
        tools = h.tool_registry.get_tools_for_phase("editing")
        assert "spawn_parallel_readers" not in tools

    def test_not_visible_in_synthesis(self):
        h = Harness()
        tools = h.tool_registry.get_tools_for_phase("synthesis")
        assert "spawn_parallel_readers" not in tools

    def test_spawn_perspective_still_in_synthesis(self):
        """原有 spawn_perspective 在 synthesis 仍可用（不影响）。"""
        h = Harness()
        tools = h.tool_registry.get_tools_for_phase("synthesis")
        assert "spawn_perspective" in tools


# ============================================================
# 4. Harness Wrapper 集成
# ============================================================

class TestHarnessParallelWrapper:
    """Harness._tool_spawn_parallel_readers 直通调用。"""

    def test_passthrough_generates_signal(self):
        """验证 harness wrapper 直接调用 tool handler。"""
        h = Harness()
        readers = [
            {"lens": "a", "focus": "x", "question": "q1"},
            {"lens": "b", "focus": "y", "question": "q2"},
        ]
        result = h._tool_spawn_parallel_readers({"readers": readers})
        assert result.startswith("__PARALLEL_SPAWN__")
        payload = json.loads(result.split("|", 1)[1])
        assert len(payload["readers"]) == 2

    def test_passthrough_rejects_invalid(self):
        """验证 harness wrapper 仍能正确拒绝无效输入。"""
        h = Harness()
        result = h._tool_spawn_parallel_readers({"readers": []})
        assert "__PARALLEL_SPAWN__" not in result
        assert "至少一个" in result


# ============================================================
# 5. _run_parallel_perspectives 集成（mock cognitive_loop）
# ============================================================

class TestRunParallelPerspectives:
    """测试并行执行 + findings 合并 + token 汇总。"""

    @pytest.fixture
    def harness_with_paper(self):
        h = Harness()
        h.state.paper_sections = {
            "methodology": "2SLS estimation with F-stat 45.2...",
            "results": "Column (1) shows OLS estimate 0.032...",
            "introduction": "The question of how X affects Y...",
            "discussion": "Our findings are consistent with...",
        }
        h.state.token_budget = 200000
        h.state.total_tokens = 10000
        h._paper_loaded = True
        return h

    @pytest.mark.asyncio
    async def test_parallel_findings_merged(self, harness_with_paper):
        """并行子视角的 findings 正确合并到主 harness。"""
        from core.loop import _run_parallel_perspectives, LoopDone
        from llm.client import LLMClient

        # Mock cognitive_loop 返回带 findings 的结果
        async def mock_loop(messages, harness, tools, client, verbose=False):
            # 模拟子视角产出 findings
            harness.state.findings.append({
                "finding": f"[{harness.state.paper_sections.keys().__iter__().__next__()}] test finding",
                "priority": "high",
                "status": "verified",
                "evidence": "mock evidence",
                "section": "methodology",
            })
            harness.state.total_tokens = 5000
            harness.state.loop_turns = 3
            return LoopDone(summary="test done", content="analysis complete")

        client = MagicMock(spec=LLMClient)
        client.model = "gpt-4.1-mini"

        readers = [
            {"lens": "stats_expert", "focus": "methodology", "question": "Is 2SLS valid?"},
            {"lens": "writing_expert", "focus": "introduction", "question": "Is narrative clear?"},
        ]

        with patch("core.loop.cognitive_loop", side_effect=mock_loop):
            result = await _run_parallel_perspectives(
                harness=harness_with_paper,
                client=client,
                readers=readers,
                verbose=False,
            )

        # 验证 findings 已合并
        assert len(harness_with_paper.state.findings) == 2
        # 验证 perspective 标记
        for f in harness_with_paper.state.findings:
            assert "perspective" in f

        # 验证 tokens 汇总 (10000 + 5000 * 2 = 20000)
        assert harness_with_paper.state.total_tokens == 20000

        # 验证报告内容
        assert "并行深读完成" in result
        assert "stats_expert" in result
        assert "writing_expert" in result

    @pytest.mark.asyncio
    async def test_parallel_handles_exception(self, harness_with_paper):
        """某个子视角异常不影响其他子视角。"""
        from core.loop import _run_parallel_perspectives, LoopDone
        from llm.client import LLMClient

        call_count = [0]

        async def mock_loop(messages, harness, tools, client, verbose=False):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("LLM call failed")
            # 第二个正常完成
            harness.state.findings.append({
                "finding": "valid finding from second reader",
                "priority": "medium",
                "status": "verified",
                "evidence": "evidence",
                "section": "results",
            })
            harness.state.total_tokens = 4000
            return LoopDone(summary="ok", content="done")

        client = MagicMock(spec=LLMClient)
        client.model = "gpt-4.1-mini"

        readers = [
            {"lens": "failing_expert", "focus": "methodology", "question": "q1?"},
            {"lens": "good_expert", "focus": "results", "question": "q2?"},
        ]

        with patch("core.loop.cognitive_loop", side_effect=mock_loop):
            result = await _run_parallel_perspectives(
                harness=harness_with_paper,
                client=client,
                readers=readers,
                verbose=False,
            )

        # 第二个的 findings 仍然合并
        assert len(harness_with_paper.state.findings) == 1
        assert "valid finding" in harness_with_paper.state.findings[0]["finding"]

        # 报告中记录了异常
        assert "异常" in result or "⚠️" in result

    @pytest.mark.asyncio
    async def test_sub_harness_uses_max_loop_turns_not_budget(self, harness_with_paper):
        """子 harness 的终止由 max_loop_turns 保证，不依赖独立 token_budget。"""
        from core.loop import _run_parallel_perspectives, LoopDone
        from llm.client import LLMClient

        observed_max_turns = []

        async def mock_loop(messages, harness, tools, client, verbose=False):
            observed_max_turns.append(harness.state.max_loop_turns)
            harness.state.total_tokens = 3000
            return LoopDone(summary="done", content="ok")

        client = MagicMock(spec=LLMClient)
        client.model = "gpt-4.1-mini"

        readers = [
            {"lens": "a", "focus": "methodology", "question": "q1?"},
            {"lens": "b", "focus": "results", "question": "q2?"},
        ]

        with patch("core.loop.cognitive_loop", side_effect=mock_loop):
            await _run_parallel_perspectives(
                harness=harness_with_paper,
                client=client,
                readers=readers,
                verbose=False,
            )

        # 子 harness 的 max_loop_turns 应该是 12（create_sub_harness 设定）
        assert all(t == 12 for t in observed_max_turns), f"Got: {observed_max_turns}"
        assert len(observed_max_turns) == 2


# ============================================================
# 6. SCHOLAR_TOOLS schema 检查
# ============================================================

class TestParallelSpawnSchema:
    """spawn_parallel_readers 的 JSON schema 在 SCHOLAR_TOOLS 中正确存在。"""

    def test_schema_present(self):
        from core.identity import SCHOLAR_TOOLS
        names = [t["name"] for t in SCHOLAR_TOOLS]
        assert "spawn_parallel_readers" in names

    def test_schema_has_readers_array(self):
        from core.identity import SCHOLAR_TOOLS
        tool = next(t for t in SCHOLAR_TOOLS if t["name"] == "spawn_parallel_readers")
        schema = tool["input_schema"]
        assert "readers" in schema["properties"]
        assert schema["properties"]["readers"]["type"] == "array"
        assert "required" in schema
        assert "readers" in schema["required"]

    def test_reader_item_schema(self):
        from core.identity import SCHOLAR_TOOLS
        tool = next(t for t in SCHOLAR_TOOLS if t["name"] == "spawn_parallel_readers")
        items = tool["input_schema"]["properties"]["readers"]["items"]
        assert "lens" in items["properties"]
        assert "focus" in items["properties"]
        assert "question" in items["properties"]
        assert items["required"] == ["lens", "focus", "question"]
