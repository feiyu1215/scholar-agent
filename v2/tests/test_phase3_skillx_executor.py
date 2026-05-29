"""
Phase 3 SkillX 执行器测试。

覆盖：
  - 单个 Skill 执行
  - 批量执行与 output_data 链式传递
  - 执行计时
  - 错误处理与降级
  - EventBus 集成
  - 执行历史与统计
  - 校验 (validate_context)
"""

import pytest
import time
from core.skills.base import (
    Finding,
    Skill,
    SkillContext,
    SkillDescriptor,
    SkillLevel,
    SkillResult,
)
from core.skills.executor import SkillExecutor, SkillExecutionRecord
from core.event_bus import EventBus, EventType, Event


# ==============================================================
# Test Skills
# ==============================================================

class SuccessSkill(Skill):
    _DESCRIPTOR = SkillDescriptor(
        name="success",
        level=SkillLevel.FUNCTIONAL,
        description="Always succeeds",
        token_cost_estimate=100,
    )

    @property
    def descriptor(self):
        return self._DESCRIPTOR

    def can_apply(self, context):
        return 0.9

    def execute(self, context):
        return SkillResult(
            success=True,
            findings=[
                Finding(
                    category="test",
                    severity="info",
                    description="All good",
                )
            ],
            output_data={"key": "value"},
        )


class SlowSkill(Skill):
    _DESCRIPTOR = SkillDescriptor(
        name="slow",
        level=SkillLevel.FUNCTIONAL,
        description="Takes time",
        token_cost_estimate=200,
    )

    @property
    def descriptor(self):
        return self._DESCRIPTOR

    def can_apply(self, context):
        return 0.7

    def execute(self, context):
        time.sleep(0.05)  # 50ms
        return SkillResult(success=True, output_data={"slow": True})


class FailingSkill(Skill):
    _DESCRIPTOR = SkillDescriptor(
        name="failing",
        level=SkillLevel.FUNCTIONAL,
        description="Always raises",
        token_cost_estimate=100,
    )

    @property
    def descriptor(self):
        return self._DESCRIPTOR

    def can_apply(self, context):
        return 0.5

    def execute(self, context):
        raise RuntimeError("Skill execution failed!")


class ValidationFailSkill(Skill):
    _DESCRIPTOR = SkillDescriptor(
        name="validation_fail",
        level=SkillLevel.FUNCTIONAL,
        description="Fails validation",
        token_cost_estimate=100,
    )

    @property
    def descriptor(self):
        return self._DESCRIPTOR

    def can_apply(self, context):
        return 0.8

    def validate_context(self, context):
        return False, "Missing required field: paper_text"

    def execute(self, context):
        return SkillResult(success=True)


class ChainableSkillA(Skill):
    """Produces output data that B consumes."""
    _DESCRIPTOR = SkillDescriptor(
        name="chain_a",
        level=SkillLevel.ATOMIC,
        description="Produces data for chain_b",
        token_cost_estimate=50,
    )

    @property
    def descriptor(self):
        return self._DESCRIPTOR

    def can_apply(self, ctx):
        return 0.8

    def execute(self, context):
        return SkillResult(
            success=True,
            output_data={"intermediate": 42},
        )


class ChainableSkillB(Skill):
    """Consumes output data from A via context.parameters."""
    _DESCRIPTOR = SkillDescriptor(
        name="chain_b",
        level=SkillLevel.ATOMIC,
        description="Consumes data from chain_a",
        token_cost_estimate=50,
    )

    @property
    def descriptor(self):
        return self._DESCRIPTOR

    def can_apply(self, ctx):
        return 0.8

    def execute(self, context):
        intermediate = context.parameters.get("intermediate", 0)
        return SkillResult(
            success=True,
            output_data={"final": intermediate * 2},
        )


# ==============================================================
# Fixtures
# ==============================================================

@pytest.fixture
def event_bus():
    return EventBus()


@pytest.fixture
def executor(event_bus):
    return SkillExecutor(event_bus=event_bus)


@pytest.fixture
def executor_no_bus():
    return SkillExecutor()


@pytest.fixture
def basic_context():
    return SkillContext(
        paper_text="A paper about regression discontinuity design.",
        current_phase="deep_review",
    )


# ==============================================================
# Tests: Single Execution
# ==============================================================

class TestSingleExecution:
    def test_run_success(self, executor, basic_context):
        skill = SuccessSkill()
        result = executor.run(skill, basic_context)
        assert result.success is True
        assert len(result.findings) == 1
        assert result.findings[0].category == "test"
        assert result.output_data == {"key": "value"}

    def test_run_returns_timing(self, executor, basic_context):
        skill = SlowSkill()
        result = executor.run(skill, basic_context)
        assert result.success is True
        assert result.execution_time_ms >= 40  # at least 40ms

    def test_run_failing_skill(self, executor, basic_context):
        skill = FailingSkill()
        result = executor.run(skill, basic_context)
        assert result.success is False
        assert result.error_message != ""
        assert "Skill execution failed" in result.error_message

    def test_run_validation_fail(self, executor, basic_context):
        skill = ValidationFailSkill()
        result = executor.run(skill, basic_context)
        assert result.success is False
        assert "validation" in result.error_message.lower() or "Missing" in result.error_message

    def test_run_without_event_bus(self, executor_no_bus, basic_context):
        """Executor should work fine without EventBus."""
        skill = SuccessSkill()
        result = executor_no_bus.run(skill, basic_context)
        assert result.success is True


# ==============================================================
# Tests: Batch Execution
# ==============================================================

class TestBatchExecution:
    def test_run_batch(self, executor, basic_context):
        skills = [SuccessSkill(), SlowSkill()]
        results = executor.run_batch(skills, basic_context)
        assert len(results) == 2
        assert all(r.success for r in results)

    def test_batch_output_chaining(self, executor):
        """Output data from skill A should be available to skill B via context.parameters."""
        ctx = SkillContext(
            paper_text="some text",
            current_phase="deep_review",
        )
        skills = [ChainableSkillA(), ChainableSkillB()]
        results = executor.run_batch(skills, ctx)
        assert results[0].output_data == {"intermediate": 42}
        assert results[1].output_data == {"final": 84}

    def test_batch_continues_on_failure(self, executor, basic_context):
        """If one skill fails, subsequent skills still run (default behavior)."""
        skills = [FailingSkill(), SuccessSkill()]
        results = executor.run_batch(skills, basic_context)
        assert results[0].success is False
        assert results[1].success is True

    def test_batch_stop_on_failure(self, executor, basic_context):
        """With stop_on_failure=True, should stop after first failure."""
        skills = [FailingSkill(), SuccessSkill()]
        results = executor.run_batch(skills, basic_context, stop_on_failure=True)
        assert len(results) == 1
        assert results[0].success is False

    def test_batch_empty(self, executor, basic_context):
        results = executor.run_batch([], basic_context)
        assert results == []

    def test_batch_findings_accumulate(self, executor, basic_context):
        """Findings from earlier skills should accumulate in context."""
        skills = [SuccessSkill(), SuccessSkill()]
        executor.run_batch(skills, basic_context)
        # After running, existing_findings in context should have findings from skill 1
        assert len(basic_context.existing_findings) >= 1


# ==============================================================
# Tests: EventBus Integration
# ==============================================================

class TestEventBusIntegration:
    def test_start_event_published(self, executor, event_bus, basic_context):
        """Should publish TOOL_CALL_STARTED event."""
        skill = SuccessSkill()
        executor.run(skill, basic_context)

        events = event_bus.get_history(event_type=EventType.TOOL_CALL_STARTED)
        assert len(events) >= 1
        assert events[-1].payload.get("skill_name") == "success"

    def test_completed_event_published(self, executor, event_bus, basic_context):
        """Should publish TOOL_CALL_COMPLETED event on success."""
        skill = SuccessSkill()
        executor.run(skill, basic_context)

        events = event_bus.get_history(event_type=EventType.TOOL_CALL_COMPLETED)
        assert len(events) >= 1

    def test_failed_event_published(self, executor, event_bus, basic_context):
        """Should publish TOOL_CALL_FAILED event on failure."""
        skill = FailingSkill()
        executor.run(skill, basic_context)

        events = event_bus.get_history(event_type=EventType.TOOL_CALL_FAILED)
        assert len(events) >= 1
        assert events[-1].payload.get("skill_name") == "failing"


# ==============================================================
# Tests: Execution History & Stats
# ==============================================================

class TestHistoryAndStats:
    def test_history_recorded(self, executor, basic_context):
        executor.run(SuccessSkill(), basic_context)
        executor.run(FailingSkill(), basic_context)

        history = executor.history
        assert len(history) == 2

    def test_history_entry_type(self, executor, basic_context):
        executor.run(SuccessSkill(), basic_context)
        entry = executor.history[0]
        assert isinstance(entry, SkillExecutionRecord)
        assert entry.skill_name == "success"
        assert entry.success is True
        assert entry.execution_time_ms >= 0
        assert entry.timestamp > 0

    def test_history_failure_entry(self, executor, basic_context):
        executor.run(FailingSkill(), basic_context)
        entry = executor.history[0]
        assert entry.skill_name == "failing"
        assert entry.success is False
        assert entry.error != ""

    def test_stats(self, executor, basic_context):
        executor.run(SuccessSkill(), basic_context)
        executor.run(SuccessSkill(), basic_context)
        executor.run(FailingSkill(), basic_context)

        stats = executor.get_stats()
        assert stats["total"] == 3
        assert stats["success"] == 2
        assert stats["failure"] == 1

    def test_stats_empty(self, executor):
        stats = executor.get_stats()
        assert stats["total"] == 0

    def test_clear_history(self, executor, basic_context):
        executor.run(SuccessSkill(), basic_context)
        executor.clear_history()
        assert len(executor.history) == 0
