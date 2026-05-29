"""
tests/test_v2_session_memory.py — Session Memory Manager 单元测试

验证:
1. SessionMemory 数据结构的序列化/反序列化
2. should_update 的认知断点触发逻辑
3. format_for_restoration 的输出格式
4. update_sync (规则提取 fallback) 行为
5. _parse_response 的 JSON 解析容错
6. 异步 update 的 LLM 调用集成
"""

import asyncio
import json
from typing import Optional, List

import pytest
from unittest.mock import AsyncMock

from core.session_memory import SessionMemory, SessionMemoryManager
from core.state import WorkspaceState


# ============================================================
# SessionMemory dataclass 测试
# ============================================================

class TestSessionMemory:
    """SessionMemory 数据结构基本功能。"""

    def test_empty_detection(self):
        """空笔记应该被检测到。"""
        m = SessionMemory()
        assert m.is_empty()

    def test_non_empty_detection(self):
        """有任何内容就不是空的。"""
        m = SessionMemory(task_summary="审阅一篇 DID 论文")
        assert not m.is_empty()

    def test_to_json_roundtrip(self):
        """JSON 序列化/反序列化应保持一致。"""
        m = SessionMemory(
            task_summary="审阅实证论文",
            methodology_assessment="DID 设计，pre-trends 未报告",
            evidence_quality="Figure 3 CI 极宽",
        )
        json_str = m.to_json()
        data = json.loads(json_str)
        m2 = SessionMemory.from_dict(data)
        assert m2.task_summary == m.task_summary
        assert m2.methodology_assessment == m.methodology_assessment
        assert m2.evidence_quality == m.evidence_quality

    def test_from_dict_tolerates_extra_fields(self):
        """from_dict 应该忽略多余字段（LLM 可能返回额外内容）。"""
        data = {
            "task_summary": "test",
            "extra_field": "should be ignored",
            "another_extra": 123,
        }
        m = SessionMemory.from_dict(data)
        assert m.task_summary == "test"
        assert m.methodology_assessment == ""  # 默认值


# ============================================================
# should_update 触发逻辑测试
# ============================================================

class TestShouldUpdate:
    """认知断点触发条件。"""

    def _make_state(
        self,
        loop_turns: int = 0,
        sections_read: Optional[List] = None,
        findings: Optional[List] = None,
    ) -> WorkspaceState:
        """构造测试用 state。"""
        state = WorkspaceState()
        state.loop_turns = loop_turns
        state.sections_read = sections_read or []
        state.findings = findings or []
        return state

    def test_no_trigger_on_empty_state(self):
        """空状态不触发更新。"""
        mgr = SessionMemoryManager()
        state = self._make_state(loop_turns=0)
        assert not mgr.should_update(state)

    def test_no_trigger_too_early(self):
        """第 1 轮有 1 个 finding 不触发（需要 ≥2 或 3+ 轮兜底）。"""
        mgr = SessionMemoryManager()
        state = self._make_state(
            loop_turns=1,
            findings=[{"finding": "test", "priority": "high"}],
        )
        assert not mgr.should_update(state)

    def test_triggers_on_multiple_new_findings(self):
        """新增 ≥2 个 findings 应触发。"""
        mgr = SessionMemoryManager()
        # 模拟第一次调用时有 3 个 findings
        state = self._make_state(
            loop_turns=3,
            findings=[
                {"finding": "f1", "priority": "high"},
                {"finding": "f2", "priority": "medium"},
                {"finding": "f3", "priority": "high"},
            ],
        )
        # _last_findings_count 是 0（初始），现在有 3，差值 = 3 ≥ 2
        assert mgr.should_update(state)

    def test_triggers_on_time_based_fallback(self):
        """距上次更新 3+ 轮且有 findings 时触发。"""
        mgr = SessionMemoryManager()
        mgr._last_update_round = 0
        state = self._make_state(
            loop_turns=4,
            findings=[{"finding": "some finding", "priority": "medium"}],
        )
        # rounds_since = 4, current_findings = 1, new_findings = 1 (< 2)
        # time_based: rounds_since >= 3 and current_findings > 0 → True
        assert mgr.should_update(state)

    def test_no_trigger_after_recent_update(self):
        """刚更新过（1 轮前），条件不满足。"""
        mgr = SessionMemoryManager()
        mgr._last_update_round = 3
        mgr._last_findings_count = 2
        state = self._make_state(
            loop_turns=4,
            findings=[
                {"finding": "f1", "priority": "high"},
                {"finding": "f2", "priority": "high"},
            ],
        )
        # rounds_since = 1, new_findings = 0 (2-2=0), section_growth 不满足
        assert not mgr.should_update(state)


# ============================================================
# format_for_restoration 测试
# ============================================================

class TestFormatRestoration:
    """恢复文本格式化。"""

    def test_empty_memory_returns_empty(self):
        """空笔记返回空字符串。"""
        mgr = SessionMemoryManager()
        assert mgr.format_for_restoration() == ""

    def test_full_memory_formatting(self):
        """完整笔记应包含所有非空字段。"""
        mgr = SessionMemoryManager()
        mgr._memory = SessionMemory(
            task_summary="审阅一篇 DID 实证论文",
            current_focus="检查 robustness checks",
            methodology_assessment="DID with staggered adoption，未报告 pre-trends",
            evidence_quality="Figure 3 的 CI 极宽",
            novelty_judgment="",
            statistical_observations="SE cluster 到 state 级别",
            writing_quality="",
            key_decisions="深入检查 IV validity",
            issue_timeline="Sec2: assumption 未讨论; Sec4: F-stat 缺失",
        )
        result = mgr.format_for_restoration()

        # 结构检查
        assert "[审稿认知笔记" in result
        assert "方法论判断:" in result
        assert "DID with staggered" in result
        assert "证据质量:" in result
        assert "统计问题:" in result
        assert "关键决策:" in result
        assert "问题时间线:" in result
        # 空字段不应出现
        assert "创新性判断:" not in result
        assert "写作质量:" not in result
        # 尾部提示
        assert "你可以修正它们，但不要遗忘" in result

    def test_restoration_is_informational_not_directive(self):
        """恢复文本应该是信息呈现，不是指令。"""
        mgr = SessionMemoryManager()
        mgr._memory = SessionMemory(
            task_summary="test",
            methodology_assessment="method looks weak",
        )
        result = mgr.format_for_restoration()
        # 不应包含命令式语言
        assert "你必须" not in result
        assert "你应该" not in result
        assert "继续追查" not in result


# ============================================================
# update_sync (规则 fallback) 测试
# ============================================================

class TestUpdateSync:
    """同步更新（规则提取 fallback）。"""

    def test_sets_task_summary_from_paper(self):
        """首次更新应从论文设置 task_summary。"""
        mgr = SessionMemoryManager()
        state = WorkspaceState()
        state.paper_sections = {
            "intro": "...",
            "method": "...",
            "results": "...",
            "full": "...",
        }
        state.loop_turns = 1

        mgr.update_sync(state, recent_activity="", new_findings=[])
        assert "3 sections" in mgr.memory.task_summary

    def test_appends_to_timeline(self):
        """新 findings 应追加到 issue_timeline。"""
        mgr = SessionMemoryManager()
        state = WorkspaceState()
        state.loop_turns = 3

        findings = [
            {"finding": "method weak", "section": "methodology", "priority": "high"},
            {"finding": "stats wrong", "section": "results", "priority": "high"},
        ]
        mgr.update_sync(state, recent_activity="", new_findings=findings)
        assert "methodology: method weak" in mgr.memory.issue_timeline
        assert "results: stats wrong" in mgr.memory.issue_timeline

    def test_updates_current_focus(self):
        """recent_activity 应更新 current_focus。"""
        mgr = SessionMemoryManager()
        state = WorkspaceState()
        state.loop_turns = 2

        mgr.update_sync(
            state,
            recent_activity="read_section(methodology) → update_findings(high)",
            new_findings=[],
        )
        assert "read_section" in mgr.memory.current_focus

    def test_increments_counters(self):
        """更新后计数器应递增。"""
        mgr = SessionMemoryManager()
        state = WorkspaceState()
        state.loop_turns = 5
        state.findings = [{"finding": "x"}]

        mgr.update_sync(state, "", [])
        assert mgr.update_count == 1
        assert mgr._last_update_round == 5
        assert mgr._last_findings_count == 1


# ============================================================
# _parse_response 容错测试
# ============================================================

class TestParseResponse:
    """LLM 响应解析容错。"""

    def test_parses_clean_json(self):
        """干净 JSON 正常解析。"""
        mgr = SessionMemoryManager()
        response = json.dumps({
            "task_summary": "test",
            "methodology_assessment": "weak IV",
        })
        result = mgr._parse_response(response)
        assert result is not None
        assert result.task_summary == "test"
        assert result.methodology_assessment == "weak IV"

    def test_parses_markdown_wrapped_json(self):
        """带 markdown ``` 包裹的 JSON 也能解析。"""
        mgr = SessionMemoryManager()
        response = '```json\n{"task_summary": "test", "evidence_quality": "good"}\n```'
        result = mgr._parse_response(response)
        assert result is not None
        assert result.task_summary == "test"

    def test_returns_none_on_invalid_json(self):
        """无效 JSON 返回 None（不崩溃）。"""
        mgr = SessionMemoryManager()
        result = mgr._parse_response("This is not JSON at all")
        assert result is None

    def test_returns_none_on_array_response(self):
        """数组响应返回 None（需要 object）。"""
        mgr = SessionMemoryManager()
        result = mgr._parse_response('[{"task_summary": "test"}]')
        assert result is None


# ============================================================
# 异步 update 集成测试
# ============================================================

class TestAsyncUpdate:
    """异步 LLM 调用集成。"""

    def test_update_with_mock_llm(self):
        """mock LLM 返回有效 JSON 应更新 memory。"""
        mock_response = json.dumps({
            "task_summary": "Reviewing a DID paper on minimum wage",
            "current_focus": "Checking pre-trends",
            "methodology_assessment": "DID design, no pre-trends reported",
            "evidence_quality": "",
            "novelty_judgment": "",
            "statistical_observations": "",
            "writing_quality": "",
            "key_decisions": "Focus on identification strategy",
            "issue_timeline": "Sec3: no pre-trends; Sec5: weak first-stage",
        })

        async def mock_llm(prompt: str, max_tokens: int) -> str:
            return mock_response

        mgr = SessionMemoryManager(llm_call_fn=mock_llm)
        state = WorkspaceState()
        state.loop_turns = 5
        state.findings = [{"finding": "test"}]

        result = asyncio.run(mgr.update(
            state,
            recent_activity="read_section(methodology)",
            new_findings=[{"finding": "no pre-trends", "priority": "high"}],
        ))

        assert result.task_summary == "Reviewing a DID paper on minimum wage"
        assert result.methodology_assessment == "DID design, no pre-trends reported"
        assert mgr.update_count == 1

    def test_update_survives_llm_failure(self):
        """LLM 调用失败不应崩溃，memory 保持不变。"""
        async def failing_llm(prompt: str, max_tokens: int) -> str:
            raise RuntimeError("API error")

        mgr = SessionMemoryManager(llm_call_fn=failing_llm)
        mgr._memory = SessionMemory(task_summary="existing state")
        state = WorkspaceState()
        state.loop_turns = 3

        result = asyncio.run(mgr.update(state, "", []))

        # memory 应保持不变
        assert result.task_summary == "existing state"
        # 但计数器仍更新
        assert mgr.update_count == 1

    def test_update_with_none_llm_skips(self):
        """llm_call_fn 为 None 时应跳过更新。"""
        mgr = SessionMemoryManager(llm_call_fn=None)
        state = WorkspaceState()
        state.loop_turns = 3

        result = asyncio.run(mgr.update(state, "activity", []))
        assert result.is_empty()  # 没被更新
