"""
tests/test_v2_collaborative_review.py — CollaborativeReview 集成测试

W1 重构后，CollaborativeReview 不再是三步硬编码 pipeline。
它是一个向后兼容包装器，内部使用统一认知循环。

覆盖:
1. 统一循环: cognitive_loop 只调用一次
2. 初始人格: 以 scholar 身份开始
3. 向后兼容返回值结构 (review/revision/re_review/findings/edits/stats)
4. Token budget 3x 分配
5. Harness 共享验证
6. LoopDoomStop 降级处理
7. LoopTalk 处理
8. switch_persona 工具在 tools 列表中可用
9. stats 收集 (新格式: persona_switches, final_persona)
10. user_intent 正确传递
"""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock, call

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from core.agent import CollaborativeReview
from core.loop import LoopDone, LoopTalk, LoopDoomStop


# ============================================================
# Fixtures
# ============================================================

SAMPLE_FINDINGS = [
    {
        "priority": "high",
        "section": "Introduction",
        "finding": "Overclaim: 作者声称 state-of-the-art 但缺乏对比实验",
        "evidence": "We achieve state-of-the-art performance on all benchmarks",
        "status": "verified",
    },
    {
        "priority": "medium",
        "section": "Method",
        "finding": "DID 平行趋势假设未验证",
        "evidence": "",
        "status": "needs_verification",
    },
]

SAMPLE_EDITS = [
    {
        "section": "Introduction",
        "description": "将 state-of-the-art 改为 competitive performance",
    },
    {
        "section": "Method",
        "description": "增加平行趋势检验表格",
    },
]


def _make_loop_side_effect(findings=None, edits=None, tokens=8000, turns=10):
    """
    构造 cognitive_loop 的 side_effect 函数。

    统一循环: 只会被调用一次。
    在循环内部填充 findings 和 edits，模拟 Agent 自主完成审阅+修改。
    """
    async def side_effect(messages, harness, tools, client, verbose=True, **kwargs):
        # 模拟 Agent 自主完成全流程
        if findings is not None:
            harness.state.findings = findings.copy()
        if edits is not None:
            harness.state.edits = edits.copy()
        harness.state.total_tokens += tokens
        harness.state.loop_turns = turns
        return LoopDone(summary="协作审稿完成", content="论文审阅完成，已发现问题并提出修改建议。")

    return side_effect


@pytest.fixture
def mock_env():
    """Mock 所有外部依赖以隔离 CollaborativeReview 逻辑。"""
    patches = [
        patch("core.agent.cognitive_loop"),
        patch("core.agent.get_persona"),
        patch("core.agent.build_system_prompt"),
        patch("core.agent.LLMClient"),
        patch("core.harness._pl_load_paper"),  # mock __init__ 中直接调用的模块级函数
        patch("core.harness.Harness.load_paper"),
        patch("core.harness.Harness.format_context", return_value="[mocked workspace state]"),
    ]
    mocks = [p.start() for p in patches]

    # Unpack
    mock_loop, mock_get_persona, mock_build_prompt, mock_llm_cls, _mock_pl_load, mock_load_paper, _mock_fmt = mocks

    # get_persona 返回 (identity_str, tools_list)
    # 包含 switch_persona 工具以模拟真实环境
    mock_get_persona.return_value = (
        "你是审稿人。",
        [{"name": "read_section"}, {"name": "switch_persona"}],
    )

    # build_system_prompt 返回简单字符串
    mock_build_prompt.return_value = "System prompt content"

    # LLMClient 实例化不需要真实 API
    mock_llm_cls.return_value = MagicMock()

    # load_paper 不做真实 IO
    mock_load_paper.return_value = None

    yield {
        "loop": mock_loop,
        "get_persona": mock_get_persona,
        "build_prompt": mock_build_prompt,
        "llm_cls": mock_llm_cls,
        "load_paper": mock_load_paper,
    }

    for p in patches:
        p.stop()


# ============================================================
# 1. 统一循环行为
# ============================================================

class TestUnifiedLoop:
    """W1 核心: 统一认知循环只调用一次。"""

    @pytest.mark.asyncio
    async def test_cognitive_loop_called_exactly_once(self, mock_env):
        """cognitive_loop 只被调用一次（不是三次）。"""
        mock_env["loop"].side_effect = _make_loop_side_effect(
            findings=SAMPLE_FINDINGS, edits=SAMPLE_EDITS
        )

        collab = CollaborativeReview(paper_path="fake.md", verbose=False)
        await collab.run()

        assert mock_env["loop"].call_count == 1

    @pytest.mark.asyncio
    async def test_initial_persona_is_scholar(self, mock_env):
        """初始人格通过 get_persona("scholar") 获取。"""
        mock_env["loop"].side_effect = _make_loop_side_effect(
            findings=SAMPLE_FINDINGS, edits=SAMPLE_EDITS
        )

        collab = CollaborativeReview(paper_path="fake.md", verbose=False)
        await collab.run()

        # get_persona 只被调用一次，用 "scholar"
        mock_env["get_persona"].assert_called_once_with("scholar")

    @pytest.mark.asyncio
    async def test_messages_structure_is_system_plus_user(self, mock_env):
        """传入 cognitive_loop 的 messages 是 [system, user] 两条。"""
        mock_env["loop"].side_effect = _make_loop_side_effect(
            findings=SAMPLE_FINDINGS, edits=SAMPLE_EDITS
        )

        collab = CollaborativeReview(paper_path="fake.md", verbose=False)
        await collab.run()

        call_kwargs = mock_env["loop"].call_args[1]
        messages = call_kwargs["messages"]
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"

    @pytest.mark.asyncio
    async def test_switch_persona_tool_available_in_tools(self, mock_env):
        """switch_persona 工具在传入循环的 tools 列表中。"""
        mock_env["loop"].side_effect = _make_loop_side_effect(
            findings=SAMPLE_FINDINGS, edits=SAMPLE_EDITS
        )

        collab = CollaborativeReview(paper_path="fake.md", verbose=False)
        await collab.run()

        call_kwargs = mock_env["loop"].call_args[1]
        tools = call_kwargs["tools"]
        tool_names = [t["name"] for t in tools]
        assert "switch_persona" in tool_names


# ============================================================
# 2. 向后兼容返回值
# ============================================================

class TestBackwardCompatibleReturn:
    """返回值结构向后兼容。"""

    @pytest.mark.asyncio
    async def test_all_keys_present(self, mock_env):
        """返回值包含所有预期 key。"""
        mock_env["loop"].side_effect = _make_loop_side_effect(
            findings=SAMPLE_FINDINGS, edits=SAMPLE_EDITS
        )

        collab = CollaborativeReview(paper_path="fake.md", verbose=False)
        result = await collab.run()

        expected_keys = {"review", "revision", "re_review", "findings", "edits", "stats"}
        assert set(result.keys()) == expected_keys

    @pytest.mark.asyncio
    async def test_review_revision_re_review_share_output(self, mock_env):
        """review/revision/re_review 映射到同一个统一输出。"""
        mock_env["loop"].side_effect = _make_loop_side_effect(
            findings=SAMPLE_FINDINGS, edits=SAMPLE_EDITS
        )

        collab = CollaborativeReview(paper_path="fake.md", verbose=False)
        result = await collab.run()

        # 统一循环的输出被映射到三个字段
        assert result["review"] == result["revision"]
        assert result["revision"] == result["re_review"]
        assert len(result["review"]) > 0

    @pytest.mark.asyncio
    async def test_findings_are_actual_list(self, mock_env):
        """返回的 findings 是实际的列表对象。"""
        mock_env["loop"].side_effect = _make_loop_side_effect(
            findings=SAMPLE_FINDINGS, edits=SAMPLE_EDITS
        )

        collab = CollaborativeReview(paper_path="fake.md", verbose=False)
        result = await collab.run()

        assert isinstance(result["findings"], list)
        assert len(result["findings"]) == 2
        assert result["findings"][0]["finding"] == "Overclaim: 作者声称 state-of-the-art 但缺乏对比实验"

    @pytest.mark.asyncio
    async def test_edits_are_actual_list(self, mock_env):
        """返回的 edits 是实际的列表对象。"""
        mock_env["loop"].side_effect = _make_loop_side_effect(
            findings=SAMPLE_FINDINGS, edits=SAMPLE_EDITS
        )

        collab = CollaborativeReview(paper_path="fake.md", verbose=False)
        result = await collab.run()

        assert isinstance(result["edits"], list)
        assert len(result["edits"]) == 2

    @pytest.mark.asyncio
    async def test_empty_findings_and_edits(self, mock_env):
        """无 findings/edits 时返回空列表。"""
        mock_env["loop"].side_effect = _make_loop_side_effect(
            findings=[], edits=[]
        )

        collab = CollaborativeReview(paper_path="fake.md", verbose=False)
        result = await collab.run()

        assert result["findings"] == []
        assert result["edits"] == []


# ============================================================
# 3. Token Budget 与 Harness 配置
# ============================================================

class TestHarnessConfig:
    """Harness 配置验证。"""

    def test_token_budget_is_3x(self, mock_env):
        """Harness 的 token_budget 是传入值的 3 倍。"""
        collab = CollaborativeReview(
            paper_path="fake.md", token_budget=50000, verbose=False
        )
        assert collab.harness.state.token_budget == 150000

    def test_default_token_budget(self, mock_env):
        """默认 token_budget=100000 → harness 300000。"""
        collab = CollaborativeReview(paper_path="fake.md", verbose=False)
        assert collab.harness.state.token_budget == 300000

    def test_max_loop_turns_is_3x(self, mock_env):
        """max_loop_turns 是传入值的 3 倍。"""
        collab = CollaborativeReview(
            paper_path="fake.md", max_loop_turns=20, verbose=False
        )
        assert collab.harness.state.max_loop_turns == 60

    @pytest.mark.asyncio
    async def test_single_harness_instance_passed_to_loop(self, mock_env):
        """cognitive_loop 接收的是 CollaborativeReview 实例的 harness。"""
        mock_env["loop"].side_effect = _make_loop_side_effect(
            findings=SAMPLE_FINDINGS, edits=SAMPLE_EDITS
        )

        collab = CollaborativeReview(paper_path="fake.md", verbose=False)
        await collab.run()

        call_kwargs = mock_env["loop"].call_args[1]
        assert call_kwargs["harness"] is collab.harness


# ============================================================
# 4. LoopDoomStop 降级
# ============================================================

class TestDoomStopHandling:
    """DoomStop 中断时的处理。"""

    @pytest.mark.asyncio
    async def test_doom_stop_produces_system_interrupt(self, mock_env):
        """LoopDoomStop 输出包含 [系统中断] 和原因。"""
        async def doom_side_effect(messages, harness, tools, client, verbose=True, **kwargs):
            harness.state.total_tokens += 5000
            return LoopDoomStop(reason="token budget exceeded", content="部分审阅结果")

        mock_env["loop"].side_effect = doom_side_effect

        collab = CollaborativeReview(paper_path="fake.md", verbose=False)
        result = await collab.run()

        assert "[系统中断]" in result["review"]
        assert "token budget exceeded" in result["review"]
        assert "部分审阅结果" in result["review"]

    @pytest.mark.asyncio
    async def test_doom_stop_all_fields_same(self, mock_env):
        """DoomStop 时 review/revision/re_review 都是同一个中断输出。"""
        async def doom_side_effect(messages, harness, tools, client, verbose=True, **kwargs):
            return LoopDoomStop(reason="max turns", content="partial")

        mock_env["loop"].side_effect = doom_side_effect

        collab = CollaborativeReview(paper_path="fake.md", verbose=False)
        result = await collab.run()

        assert result["review"] == result["revision"] == result["re_review"]


# ============================================================
# 5. LoopTalk 处理
# ============================================================

class TestLoopTalkHandling:
    """Agent 返回 LoopTalk 时的输出提取。"""

    @pytest.mark.asyncio
    async def test_loop_talk_uses_message_field(self, mock_env):
        """LoopTalk 优先使用 message 字段。"""
        async def talk_side_effect(messages, harness, tools, client, verbose=True, **kwargs):
            return LoopTalk(message="我想确认一下...", content="fallback content")

        mock_env["loop"].side_effect = talk_side_effect

        collab = CollaborativeReview(paper_path="fake.md", verbose=False)
        result = await collab.run()

        assert result["review"] == "我想确认一下..."

    @pytest.mark.asyncio
    async def test_loop_talk_fallback_to_content(self, mock_env):
        """LoopTalk 没有 message 时降级到 content。"""
        async def talk_side_effect(messages, harness, tools, client, verbose=True, **kwargs):
            return LoopTalk(message="", content="fallback content here")

        mock_env["loop"].side_effect = talk_side_effect

        collab = CollaborativeReview(paper_path="fake.md", verbose=False)
        result = await collab.run()

        # message 为空字符串时走 `or` 分支到 content
        assert result["review"] == "fallback content here"


# ============================================================
# 6. Stats 收集 (新格式)
# ============================================================

class TestStatsCollection:
    """_collect_stats 收集统计信息 (W1 新格式)。"""

    @pytest.mark.asyncio
    async def test_stats_total_tokens(self, mock_env):
        """total_tokens 正确反映循环使用。"""
        mock_env["loop"].side_effect = _make_loop_side_effect(
            findings=SAMPLE_FINDINGS, edits=SAMPLE_EDITS, tokens=12000
        )

        collab = CollaborativeReview(paper_path="fake.md", verbose=False)
        result = await collab.run()

        assert result["stats"]["total_tokens"] == 12000

    @pytest.mark.asyncio
    async def test_stats_findings_and_edits_count(self, mock_env):
        """findings_count 和 edits_count 正确。"""
        mock_env["loop"].side_effect = _make_loop_side_effect(
            findings=SAMPLE_FINDINGS, edits=SAMPLE_EDITS
        )

        collab = CollaborativeReview(paper_path="fake.md", verbose=False)
        result = await collab.run()

        assert result["stats"]["findings_count"] == 2
        assert result["stats"]["edits_count"] == 2

    @pytest.mark.asyncio
    async def test_stats_has_conversation_turns(self, mock_env):
        """stats 包含 conversation_turns 字段。"""
        mock_env["loop"].side_effect = _make_loop_side_effect(
            findings=SAMPLE_FINDINGS, edits=SAMPLE_EDITS
        )

        collab = CollaborativeReview(paper_path="fake.md", verbose=False)
        result = await collab.run()

        assert "conversation_turns" in result["stats"]

    @pytest.mark.asyncio
    async def test_stats_keys_complete(self, mock_env):
        """stats 包含所有预期字段。"""
        mock_env["loop"].side_effect = _make_loop_side_effect(
            findings=SAMPLE_FINDINGS, edits=SAMPLE_EDITS
        )

        collab = CollaborativeReview(paper_path="fake.md", verbose=False)
        result = await collab.run()

        expected_stat_keys = {
            "total_tokens", "total_loop_turns", "conversation_turns",
            "findings_count", "edits_count", "phases",
        }
        assert set(result["stats"].keys()) == expected_stat_keys

    @pytest.mark.asyncio
    async def test_stats_total_loop_turns(self, mock_env):
        """total_loop_turns 反映循环轮数。"""
        mock_env["loop"].side_effect = _make_loop_side_effect(
            findings=SAMPLE_FINDINGS, edits=SAMPLE_EDITS, turns=15
        )

        collab = CollaborativeReview(paper_path="fake.md", verbose=False)
        result = await collab.run()

        assert result["stats"]["total_loop_turns"] == 15

    @pytest.mark.asyncio
    async def test_stats_phases_empty_in_unified_mode(self, mock_env):
        """统一循环模式下 phases 为空列表。"""
        mock_env["loop"].side_effect = _make_loop_side_effect(
            findings=SAMPLE_FINDINGS, edits=SAMPLE_EDITS
        )

        collab = CollaborativeReview(paper_path="fake.md", verbose=False)
        result = await collab.run()

        assert result["stats"]["phases"] == []


# ============================================================
# 7. User Intent 传递
# ============================================================

class TestUserIntent:
    """用户意图正确传递给循环。"""

    @pytest.mark.asyncio
    async def test_custom_user_intent_passed_to_loop(self, mock_env):
        """用户提供的 user_intent 传递给 cognitive_loop 的 messages。"""
        mock_env["loop"].side_effect = _make_loop_side_effect(
            findings=[], edits=[]
        )

        custom_intent = "请重点审阅实验部分的统计方法。"
        collab = CollaborativeReview(paper_path="fake.md", verbose=False)
        await collab.run(user_intent=custom_intent)

        call_kwargs = mock_env["loop"].call_args[1]
        messages = call_kwargs["messages"]
        user_msg = messages[-1]["content"]
        assert user_msg == custom_intent

    @pytest.mark.asyncio
    async def test_default_intent_when_none_provided(self, mock_env):
        """未提供 user_intent 时使用默认审稿提示。"""
        mock_env["loop"].side_effect = _make_loop_side_effect(
            findings=[], edits=[]
        )

        collab = CollaborativeReview(paper_path="fake.md", verbose=False)
        await collab.run()

        call_kwargs = mock_env["loop"].call_args[1]
        messages = call_kwargs["messages"]
        user_msg = messages[-1]["content"]
        assert "审阅" in user_msg


# ============================================================
# 8. LoopDone 输出提取
# ============================================================

class TestLoopDoneOutput:
    """LoopDone 正常结束时的输出提取。"""

    @pytest.mark.asyncio
    async def test_loop_done_uses_content(self, mock_env):
        """LoopDone 使用 content 字段作为输出。"""
        async def done_side_effect(messages, harness, tools, client, verbose=True, **kwargs):
            return LoopDone(summary="审阅完成", content="这是一篇高质量论文。")

        mock_env["loop"].side_effect = done_side_effect

        collab = CollaborativeReview(paper_path="fake.md", verbose=False)
        result = await collab.run()

        assert result["review"] == "这是一篇高质量论文。"

    @pytest.mark.asyncio
    async def test_loop_done_empty_content_falls_to_summary(self, mock_env):
        """LoopDone content 为空时降级到 summary。"""
        async def done_side_effect(messages, harness, tools, client, verbose=True, **kwargs):
            return LoopDone(summary="审阅完成无文本", content="")

        mock_env["loop"].side_effect = done_side_effect

        collab = CollaborativeReview(paper_path="fake.md", verbose=False)
        result = await collab.run()

        assert result["review"] == "审阅完成无文本"

    @pytest.mark.asyncio
    async def test_loop_done_both_empty_gives_fallback(self, mock_env):
        """LoopDone content 和 summary 都为空时用兜底文本。"""
        async def done_side_effect(messages, harness, tools, client, verbose=True, **kwargs):
            return LoopDone(summary="", content="")

        mock_env["loop"].side_effect = done_side_effect

        collab = CollaborativeReview(paper_path="fake.md", verbose=False)
        result = await collab.run()

        assert result["review"] == "(完成但无文本输出)"


# ============================================================
# 9. 构建 System Prompt
# ============================================================

class TestSystemPrompt:
    """系统提示正确构建。"""

    @pytest.mark.asyncio
    async def test_build_system_prompt_called_with_identity(self, mock_env):
        """build_system_prompt 使用 scholar identity 调用。"""
        mock_env["loop"].side_effect = _make_loop_side_effect(
            findings=[], edits=[]
        )

        collab = CollaborativeReview(paper_path="fake.md", verbose=False)
        await collab.run()

        mock_env["build_prompt"].assert_called_once()
        call_kwargs = mock_env["build_prompt"].call_args[1]
        assert call_kwargs["identity"] == "你是审稿人。"

    @pytest.mark.asyncio
    async def test_system_prompt_in_messages(self, mock_env):
        """system prompt 内容出现在 messages[0]。"""
        mock_env["loop"].side_effect = _make_loop_side_effect(
            findings=[], edits=[]
        )

        collab = CollaborativeReview(paper_path="fake.md", verbose=False)
        await collab.run()

        call_kwargs = mock_env["loop"].call_args[1]
        messages = call_kwargs["messages"]
        assert messages[0]["content"] == "System prompt content"
