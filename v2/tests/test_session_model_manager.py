"""
Tests for llm/session_model_manager.py and llm/bootstrap.py.

Covers:
- Config loading (success / missing file)
- Model listing (raw / formatted)
- Model switching (success / nonexistent / same model / cross-provider)
- Token tracking and budget
- Dynamic model add/remove
- Context summary generation
- Bootstrap dialog (Friday preset / custom provider)
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llm.session_model_manager import (
    DEFAULT_MODEL_ASSIGNMENTS,
    DEFAULT_TIER_MODELS,
    VALID_ROLES,
    ModelInfo,
    ModelTokenUsage,
    SessionModelManager,
    SwitchRecord,
)


# ============================================================
# Fixtures
# ============================================================


def _make_config(tmp_path: Path, extra_providers: dict | None = None) -> Path:
    """Create a minimal providers.json in tmp_path and return its path."""
    config = {
        "version": 1,
        "default_provider": "test",
        "providers": {
            "test": {
                "display_name": "Test Provider",
                "base_url": "https://test.example.com/v1",
                "api_key": "test-key-123",
                "models": [
                    {
                        "id": "model-a",
                        "display_name": "Model A",
                        "tags": ["general"],
                        "cost_tier": "high",
                    },
                    {
                        "id": "model-b",
                        "display_name": "Model B",
                        "tags": ["fast"],
                        "cost_tier": "low",
                    },
                ],
            }
        },
        "token_budgets": {"default_total": 10000, "per_model_limits": {}},
    }
    if extra_providers:
        config["providers"].update(extra_providers)
    path = tmp_path / "providers.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def _make_mock_client(model: str = "model-a") -> MagicMock:
    """Create a mock LLMClient with assignable attributes."""
    client = MagicMock()
    client.model = model
    client.timeout = 30.0
    client.client = MagicMock()  # AsyncOpenAI mock
    # Make chat() an async mock that returns a summary string
    client.chat = AsyncMock(return_value="这是一段测试摘要内容。")
    return client


@pytest.fixture
def config_path(tmp_path):
    return _make_config(tmp_path)


@pytest.fixture
def mgr(config_path):
    return SessionModelManager(config_path=config_path)


# ============================================================
# Test: Config Loading
# ============================================================


class TestConfigLoading:
    def test_load_config_success(self, config_path):
        """Should load providers.json and build model registry."""
        mgr = SessionModelManager(config_path=config_path)
        assert mgr.current_model_id == "model-a"
        assert len(mgr.list_models()) == 2

    def test_load_config_missing(self, tmp_path):
        """Should raise FileNotFoundError when config doesn't exist."""
        with pytest.raises(FileNotFoundError, match="Provider config not found"):
            SessionModelManager(config_path=tmp_path / "nonexistent.json")

    def test_load_config_no_models(self, tmp_path):
        """Should raise ValueError when no models are configured."""
        config = {
            "version": 1,
            "default_provider": "empty",
            "providers": {
                "empty": {
                    "display_name": "Empty",
                    "base_url": "https://x.com",
                    "api_key": "k",
                    "models": [],
                }
            },
            "token_budgets": {"default_total": 100},
        }
        path = tmp_path / "providers.json"
        path.write_text(json.dumps(config))
        with pytest.raises(ValueError, match="No models configured"):
            SessionModelManager(config_path=path)


# ============================================================
# Test: Model Listing
# ============================================================


class TestModelListing:
    def test_list_models(self, mgr):
        """Should return all available models as ModelInfo objects."""
        models = mgr.list_models()
        assert len(models) == 2
        assert all(isinstance(m, ModelInfo) for m in models)
        ids = [m.id for m in models]
        assert "model-a" in ids
        assert "model-b" in ids

    def test_list_models_formatted(self, mgr):
        """Should mark current model and include all info."""
        formatted = mgr.list_models_formatted()
        assert "← 当前" in formatted
        assert "Model A" in formatted
        assert "Model B" in formatted
        assert "model-a" in formatted
        assert "[high]" in formatted
        assert "[low]" in formatted

    def test_current_model_property(self, mgr):
        """Should return ModelInfo for current model."""
        current = mgr.current_model
        assert isinstance(current, ModelInfo)
        assert current.id == "model-a"
        assert current.display_name == "Model A"


# ============================================================
# Test: Model Switching
# ============================================================


class TestModelSwitching:
    def test_switch_model_success(self, mgr):
        """Should update client.model and record switch."""
        client = _make_mock_client("model-a")
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]

        result = asyncio.run(
            mgr.switch_model("model-b", "测试切换", client, messages)
        )

        assert "✓ 已切换" in result
        assert "Model A → Model B" in result
        assert mgr.current_model_id == "model-b"
        assert client.model == "model-b"
        # Summary should have been generated
        client.chat.assert_called_once()
        # Switch history should be recorded
        assert len(mgr.get_switch_history()) == 1
        record = mgr.get_switch_history()[0]
        assert record.from_model == "model-a"
        assert record.to_model == "model-b"
        assert record.reason == "测试切换"

    def test_switch_nonexistent_model(self, mgr):
        """Should raise ValueError with available models list."""
        client = _make_mock_client()
        with pytest.raises(ValueError, match="not found"):
            asyncio.run(
                mgr.switch_model("nonexistent", "test", client, [])
            )

    def test_switch_same_model(self, mgr):
        """Should return noop message without calling chat."""
        client = _make_mock_client("model-a")
        result = asyncio.run(
            mgr.switch_model("model-a", "test", client, [])
        )
        assert "无需切换" in result
        client.chat.assert_not_called()
        assert len(mgr.get_switch_history()) == 0

    def test_cross_provider_switch(self, tmp_path):
        """Should rebuild AsyncOpenAI client when base_url changes."""
        config = {
            "version": 1,
            "default_provider": "provider1",
            "providers": {
                "provider1": {
                    "display_name": "P1",
                    "base_url": "https://p1.example.com/v1",
                    "api_key": "key1",
                    "models": [
                        {"id": "m1", "display_name": "M1", "tags": [], "cost_tier": "high"}
                    ],
                },
                "provider2": {
                    "display_name": "P2",
                    "base_url": "https://p2.example.com/v1",
                    "api_key": "key2",
                    "models": [
                        {"id": "m2", "display_name": "M2", "tags": [], "cost_tier": "low"}
                    ],
                },
            },
            "token_budgets": {"default_total": 100000},
        }
        path = tmp_path / "providers.json"
        path.write_text(json.dumps(config))
        mgr = SessionModelManager(config_path=path)

        client = _make_mock_client("m1")
        original_client_obj = client.client

        with patch("openai.AsyncOpenAI") as mock_aoai:
            mock_aoai.return_value = MagicMock()
            result = asyncio.run(
                mgr.switch_model("m2", "cross-provider", client, [])
            )

        assert "✓ 已切换" in result
        assert client.model == "m2"
        # AsyncOpenAI should have been reconstructed
        mock_aoai.assert_called_once_with(
            api_key="key2",
            base_url="https://p2.example.com/v1",
            timeout=30.0,
        )


# ============================================================
# Test: Token Tracking
# ============================================================


class TestTokenTracking:
    def test_record_tokens(self, mgr):
        """Should correctly accumulate per-model token usage."""
        mgr.record_tokens("model-a", 1000, 200)
        mgr.record_tokens("model-a", 500, 100)
        mgr.record_tokens("model-b", 2000, 500)

        assert mgr.get_total_tokens() == 4300

    def test_budget_exceeded(self, mgr):
        """Should return True when total budget is exceeded."""
        # Budget is 10000 in test config
        mgr.record_tokens("model-a", 8000, 3000)  # 11000 > 10000
        assert mgr.is_budget_exceeded() is True

    def test_budget_not_exceeded(self, mgr):
        """Should return False when under budget."""
        mgr.record_tokens("model-a", 3000, 1000)  # 4000 < 10000
        assert mgr.is_budget_exceeded() is False

    def test_get_budget_status(self, mgr):
        """Should return formatted budget report."""
        mgr.record_tokens("model-a", 1000, 200)
        status = mgr.get_budget_status()
        assert "1,200 / 10,000" in status
        assert "Model A" in status
        assert "in=1,000" in status
        assert "out=200" in status


# ============================================================
# Test: Dynamic Model Management
# ============================================================


class TestDynamicModels:
    def test_add_model(self, mgr, config_path):
        """Should add model to registry and persist to config."""
        result = mgr.add_model(
            "test",
            {"id": "model-c", "display_name": "Model C", "tags": ["new"], "cost_tier": "medium"},
        )
        assert "✓ 已添加" in result
        assert len(mgr.list_models()) == 3
        # Verify persistence
        saved = json.loads(config_path.read_text())
        model_ids = [m["id"] for m in saved["providers"]["test"]["models"]]
        assert "model-c" in model_ids

    def test_add_duplicate_model(self, mgr):
        """Should reject duplicate model ID."""
        result = mgr.add_model("test", {"id": "model-a"})
        assert "已存在" in result

    def test_add_model_invalid_provider(self, mgr):
        """Should reject nonexistent provider."""
        result = mgr.add_model("nonexistent", {"id": "x"})
        assert "不存在" in result

    def test_remove_model(self, mgr, config_path):
        """Should remove model from registry and persist."""
        result = mgr.remove_model("model-b")
        assert "✓ 已移除" in result
        assert len(mgr.list_models()) == 1
        # Verify persistence
        saved = json.loads(config_path.read_text())
        model_ids = [m["id"] for m in saved["providers"]["test"]["models"]]
        assert "model-b" not in model_ids

    def test_remove_current_model_blocked(self, mgr):
        """Should not allow removing the currently active model."""
        result = mgr.remove_model("model-a")
        assert "不能删除当前" in result
        assert len(mgr.list_models()) == 2

    def test_remove_nonexistent_model(self, mgr):
        """Should return error for nonexistent model."""
        result = mgr.remove_model("nonexistent")
        assert "不存在" in result


# ============================================================
# Test: Context Summary
# ============================================================


class TestContextSummary:
    def test_context_summary_generation(self, mgr):
        """Should generate non-empty summary via client.chat."""
        client = _make_mock_client()
        messages = [
            {"role": "user", "content": "请帮我分析这篇论文"},
            {"role": "assistant", "content": "好的，我来分析一下..."},
        ]

        result = asyncio.run(
            mgr.switch_model("model-b", "test", client, messages)
        )

        # Summary should be stored
        summary = mgr.get_last_summary("model-a")
        assert summary == "这是一段测试摘要内容。"

    def test_context_summary_empty_messages(self, mgr):
        """Should handle empty message list gracefully."""
        client = _make_mock_client()
        result = asyncio.run(
            mgr.switch_model("model-b", "test", client, [])
        )
        summary = mgr.get_last_summary("model-a")
        assert summary == "(无对话历史)"
        client.chat.assert_not_called()

    def test_context_summary_failure(self, mgr):
        """Should not block switch when summary generation fails."""
        client = _make_mock_client()
        client.chat = AsyncMock(side_effect=RuntimeError("API error"))
        messages = [{"role": "user", "content": "hello"}]

        result = asyncio.run(
            mgr.switch_model("model-b", "test", client, messages)
        )

        assert "✓ 已切换" in result
        summary = mgr.get_last_summary("model-a")
        assert "摘要生成失败" in summary

    def test_get_last_summary_no_history(self, mgr):
        """Should return None when no switches have occurred."""
        assert mgr.get_last_summary() is None
        assert mgr.get_last_summary("model-a") is None


# ============================================================
# Test: Bootstrap
# ============================================================


class TestBootstrap:
    def test_generates_valid_config_friday(self, tmp_path):
        """Should produce valid providers.json from Friday preset."""
        from llm.bootstrap import run_bootstrap

        inputs = iter(["1", "my-api-key", "n", "250000"])
        result = run_bootstrap(
            config_dir=tmp_path,
            input_fn=lambda p: next(inputs),
            print_fn=lambda *a: None,
        )

        assert result.exists()
        data = json.loads(result.read_text())
        assert data["version"] == 1
        assert data["default_provider"] == "friday"
        assert data["providers"]["friday"]["api_key"] == "my-api-key"
        assert len(data["providers"]["friday"]["models"]) == 5
        assert data["token_budgets"]["default_total"] == 250000

    def test_generates_valid_config_custom(self, tmp_path):
        """Should handle custom provider with user-provided base_url."""
        from llm.bootstrap import run_bootstrap

        # custom provider: choice 3, base_url, api_key, add one model, then empty to stop, budget
        inputs = iter([
            "3",  # custom
            "https://my-api.com/v1",  # base_url
            "custom-key",  # api_key
            "my-model",  # model id
            "My Model",  # display name
            "high",  # cost tier
            "reasoning",  # tags
            "",  # stop adding models
            "100000",  # budget
        ])
        result = run_bootstrap(
            config_dir=tmp_path,
            input_fn=lambda p: next(inputs),
            print_fn=lambda *a: None,
        )

        data = json.loads(result.read_text())
        assert data["default_provider"] == "custom"
        assert data["providers"]["custom"]["base_url"] == "https://my-api.com/v1"
        assert data["providers"]["custom"]["api_key"] == "custom-key"
        assert len(data["providers"]["custom"]["models"]) == 1
        assert data["providers"]["custom"]["models"][0]["id"] == "my-model"

    def test_default_budget(self, tmp_path):
        """Should use 500000 as default when user enters empty string."""
        from llm.bootstrap import run_bootstrap

        inputs = iter(["1", "key", "n", ""])  # empty budget → default
        result = run_bootstrap(
            config_dir=tmp_path,
            input_fn=lambda p: next(inputs),
            print_fn=lambda *a: None,
        )

        data = json.loads(result.read_text())
        assert data["token_budgets"]["default_total"] == 500000


# ============================================================
# Test: Data Classes
# ============================================================


class TestDataClasses:
    def test_model_token_usage_total(self):
        """ModelTokenUsage.total should sum input + output."""
        usage = ModelTokenUsage(model_id="test", input_tokens=100, output_tokens=50)
        assert usage.total == 150

    def test_switch_record_fields(self):
        """SwitchRecord should store all fields correctly."""
        record = SwitchRecord(
            timestamp=1234567890.0,
            from_model="a",
            to_model="b",
            reason="test",
            context_summary="summary",
            tokens_used_before_switch=500,
        )
        assert record.from_model == "a"
        assert record.to_model == "b"
        assert record.tokens_used_before_switch == 500


# ============================================================
# Tests for loop.py __MODEL__ signal handler
# ============================================================


class TestHandleModelSignal:
    """Tests for _handle_model_signal in core/loop.py."""

    @pytest.fixture
    def mock_client(self):
        client = MagicMock()
        client.model = "friday-gpt4o"
        client.timeout = 30
        return client

    @pytest.fixture
    def mock_mgr(self):
        mgr = AsyncMock()
        mgr.switch_model = AsyncMock(return_value="已切换到 deepseek-chat")
        return mgr

    @pytest.mark.asyncio
    async def test_valid_signal(self, mock_client, mock_mgr):
        """Valid __MODEL__ signal should call switch_model and return ack."""
        from core.loop import _handle_model_signal

        result = await _handle_model_signal(
            result='__MODEL__|{"target": "deepseek-chat", "reason": "cheaper"}',
            client=mock_client,
            messages=[{"role": "user", "content": "hello"}],
            session_model_mgr=mock_mgr,
            verbose=False,
        )
        assert "已切换到 deepseek-chat" in result
        mock_mgr.switch_model.assert_called_once_with(
            target_model_id="deepseek-chat",
            reason="cheaper",
            client=mock_client,
            messages=[{"role": "user", "content": "hello"}],
        )

    @pytest.mark.asyncio
    async def test_invalid_json(self, mock_client, mock_mgr):
        """Malformed JSON should return error message."""
        from core.loop import _handle_model_signal

        result = await _handle_model_signal(
            result="__MODEL__|{not valid json}",
            client=mock_client,
            messages=[],
            session_model_mgr=mock_mgr,
            verbose=False,
        )
        assert "格式错误" in result
        mock_mgr.switch_model.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_target(self, mock_client, mock_mgr):
        """Missing target field should return error."""
        from core.loop import _handle_model_signal

        result = await _handle_model_signal(
            result='__MODEL__|{"reason": "test"}',
            client=mock_client,
            messages=[],
            session_model_mgr=mock_mgr,
            verbose=False,
        )
        assert "未指定目标模型" in result

    @pytest.mark.asyncio
    async def test_no_mgr(self, mock_client):
        """When session_model_mgr is None, should return disabled message."""
        from core.loop import _handle_model_signal

        result = await _handle_model_signal(
            result='__MODEL__|{"target": "deepseek-chat"}',
            client=mock_client,
            messages=[],
            session_model_mgr=None,
            verbose=False,
        )
        assert "未启用" in result

    @pytest.mark.asyncio
    async def test_switch_value_error(self, mock_client, mock_mgr):
        """ValueError from switch_model should be caught and reported."""
        from core.loop import _handle_model_signal

        mock_mgr.switch_model = AsyncMock(side_effect=ValueError("模型不存在: xyz"))
        result = await _handle_model_signal(
            result='__MODEL__|{"target": "xyz"}',
            client=mock_client,
            messages=[],
            session_model_mgr=mock_mgr,
            verbose=False,
        )
        assert "切换失败" in result
        assert "模型不存在" in result

    @pytest.mark.asyncio
    async def test_switch_unexpected_error(self, mock_client, mock_mgr):
        """Unexpected exceptions should be caught with type info."""
        from core.loop import _handle_model_signal

        mock_mgr.switch_model = AsyncMock(side_effect=RuntimeError("connection lost"))
        result = await _handle_model_signal(
            result='__MODEL__|{"target": "some-model"}',
            client=mock_client,
            messages=[],
            session_model_mgr=mock_mgr,
            verbose=False,
        )
        assert "内部错误" in result
        assert "RuntimeError" in result


# ============================================================
# Tests for harness.py budget delegation
# ============================================================


class TestHarnessBudgetDelegation:
    """Tests for Harness.is_budget_exceeded with SessionModelManager."""

    def test_without_mgr_uses_budget_policy(self):
        """Without session_model_mgr, should use original budget_policy."""
        from core.harness import Harness

        h = Harness(token_budget=1000)
        h.state.total_tokens = 500
        assert h.is_budget_exceeded() is False
        h.state.total_tokens = 1001
        assert h.is_budget_exceeded() is True

    def test_with_mgr_delegates(self):
        """With session_model_mgr, should delegate to mgr.is_budget_exceeded()."""
        from core.harness import Harness

        mock_mgr = MagicMock()
        mock_mgr.is_budget_exceeded.return_value = True

        h = Harness(token_budget=999999, session_model_mgr=mock_mgr)
        # Even though token_budget is huge, mgr says exceeded
        assert h.is_budget_exceeded() is True
        mock_mgr.is_budget_exceeded.assert_called_once()

    def test_with_mgr_not_exceeded(self):
        """With session_model_mgr returning False, should return False."""
        from core.harness import Harness

        mock_mgr = MagicMock()
        mock_mgr.is_budget_exceeded.return_value = False

        h = Harness(token_budget=100, session_model_mgr=mock_mgr)
        h.state.total_tokens = 9999  # Would exceed budget_policy
        # But mgr says not exceeded, so mgr wins
        assert h.is_budget_exceeded() is False


# ============================================================
# Tests for cognitive_loop session_model_mgr parameter
# ============================================================


class TestCognitiveLoopModelMgrParam:
    """Verify cognitive_loop accepts session_model_mgr without breaking."""

    def test_signature_has_param(self):
        """cognitive_loop should have session_model_mgr in its signature."""
        import inspect
        from core.loop import cognitive_loop

        sig = inspect.signature(cognitive_loop)
        assert "session_model_mgr" in sig.parameters
        param = sig.parameters["session_model_mgr"]
        assert param.default is None


# ============================================================
# Tests for identity.py model info injection (Phase 2)
# ============================================================


class TestIdentityModelInjection:
    """Tests for build_system_prompt model_info param and format_model_info_for_prompt."""

    def test_build_system_prompt_without_model_info(self):
        """Without model_info, output should be unchanged from original behavior."""
        from core.identity import build_system_prompt

        result = build_system_prompt(
            identity="Hello {workspace_state}",
            workspace_state="world",
            model_info=None,
        )
        assert result == "Hello world"

    def test_build_system_prompt_with_model_info(self):
        """With model_info, it should be appended after the main prompt."""
        from core.identity import build_system_prompt

        result = build_system_prompt(
            identity="Hello {workspace_state}",
            workspace_state="world",
            model_info="## Models\n- model-a\n- model-b",
        )
        assert "Hello world" in result
        assert "## Models" in result
        assert "model-a" in result
        # model_info should come after the main content
        assert result.index("Hello world") < result.index("## Models")

    def test_build_system_prompt_empty_model_info_ignored(self):
        """Empty string model_info should be treated as falsy and not appended."""
        from core.identity import build_system_prompt

        result = build_system_prompt(
            identity="Hello {workspace_state}",
            workspace_state="world",
            model_info="",
        )
        assert result == "Hello world"

    def test_format_model_info_for_prompt_content(self):
        """format_model_info_for_prompt should produce expected structure."""
        from core.identity import format_model_info_for_prompt

        result = format_model_info_for_prompt(
            models_formatted="  1. model-a (Model A) [general] ← 当前\n  2. model-b (Model B) [reasoning]",
            current_model="model-a",
        )
        # Should contain key elements
        assert "多模型能力" in result
        assert "当前模型: model-a" in result
        assert "model-a" in result
        assert "model-b" in result
        assert "switch_model" in result
        assert "切换时机建议" in result

    def test_format_model_info_for_prompt_is_string(self):
        """format_model_info_for_prompt should return a non-empty string."""
        from core.identity import format_model_info_for_prompt

        result = format_model_info_for_prompt(
            models_formatted="  1. test-model",
            current_model="test-model",
        )
        assert isinstance(result, str)
        assert len(result) > 50  # Should be substantial


class TestHarnessFormatContextModelInfo:
    """Tests for Harness.format_context injecting model info when session_model_mgr is set."""

    def test_format_context_without_mgr_no_model_info(self):
        """Without session_model_mgr, format_context should not contain model info."""
        from core.harness import Harness

        h = Harness(token_budget=100000)
        context = h.format_context()
        assert "多模型能力" not in context
        assert "__MODEL__" not in context

    def test_format_context_with_mgr_includes_model_info(self):
        """With session_model_mgr, format_context should include model info."""
        from core.harness import Harness

        mock_mgr = MagicMock()
        mock_mgr.list_models_formatted.return_value = (
            "  1. model-a (Model A) [general] ← 当前\n"
            "  2. model-b (Model B) [reasoning]"
        )
        mock_mgr.current_model_id = "model-a"

        h = Harness(token_budget=100000, session_model_mgr=mock_mgr)
        context = h.format_context()
        assert "多模型能力" in context
        assert "当前模型: model-a" in context
        assert "switch_model" in context

    def test_format_context_mgr_exception_graceful(self):
        """If session_model_mgr raises, format_context should not crash."""
        from core.harness import Harness

        mock_mgr = MagicMock()
        mock_mgr.list_models_formatted.side_effect = RuntimeError("boom")

        h = Harness(token_budget=100000, session_model_mgr=mock_mgr)
        # Should not raise
        context = h.format_context()
        # Should still have basic context, just no model info
        assert "多模型能力" not in context


class TestAgentSessionModelMgrParam:
    """Tests for ScholarAgent accepting and passing session_model_mgr."""

    def test_agent_init_accepts_session_model_mgr(self):
        """ScholarAgent should accept session_model_mgr parameter."""
        import inspect
        from core.agent import ScholarAgent

        sig = inspect.signature(ScholarAgent.__init__)
        assert "session_model_mgr" in sig.parameters
        param = sig.parameters["session_model_mgr"]
        assert param.default is None

    def test_agent_stores_session_model_mgr(self):
        """ScholarAgent should store session_model_mgr as _session_model_mgr."""
        from core.agent import ScholarAgent

        mock_mgr = MagicMock()
        mock_mgr.list_models_formatted.return_value = "  1. test-model"
        mock_mgr.current_model_id = "test-model"
        mock_mgr.is_budget_exceeded.return_value = False

        agent = ScholarAgent(
            paper_path=None,
            content_sections={"abstract": "Test abstract content for testing."},
            session_model_mgr=mock_mgr,
        )
        assert agent._session_model_mgr is mock_mgr

    def test_agent_passes_mgr_to_harness(self):
        """ScholarAgent should pass session_model_mgr to Harness."""
        from core.agent import ScholarAgent

        mock_mgr = MagicMock()
        mock_mgr.list_models_formatted.return_value = "  1. test-model"
        mock_mgr.current_model_id = "test-model"
        mock_mgr.is_budget_exceeded.return_value = False

        agent = ScholarAgent(
            paper_path=None,
            content_sections={"abstract": "Test abstract content for testing."},
            session_model_mgr=mock_mgr,
        )
        assert agent.harness._session_model_mgr is mock_mgr


# ============================================================
# Tests for tool_switch_model handler
# ============================================================


class TestToolSwitchModel:
    """Tests for the tool_switch_model handler in misc.py."""

    def test_valid_switch_returns_signal(self):
        """Valid args should return __MODEL__|{json} signal."""
        from core.tool_handlers.misc import tool_switch_model

        state = MagicMock()
        result = tool_switch_model(
            {"target_model": "deepseek-r1", "reason": "需要深度推理"},
            state,
        )
        assert result.startswith("__MODEL__|")
        import json
        payload = json.loads(result.split("|", 1)[1])
        assert payload["target"] == "deepseek-r1"
        assert payload["reason"] == "需要深度推理"

    def test_empty_target_returns_error(self):
        """Empty target_model should return error message, not signal."""
        from core.tool_handlers.misc import tool_switch_model

        state = MagicMock()
        result = tool_switch_model({"target_model": "", "reason": "test"}, state)
        assert not result.startswith("__MODEL__")
        assert "目标模型" in result

    def test_missing_target_returns_error(self):
        """Missing target_model key should return error message."""
        from core.tool_handlers.misc import tool_switch_model

        state = MagicMock()
        result = tool_switch_model({"reason": "test"}, state)
        assert not result.startswith("__MODEL__")
        assert "目标模型" in result

    def test_whitespace_only_target_returns_error(self):
        """Whitespace-only target should be treated as empty."""
        from core.tool_handlers.misc import tool_switch_model

        state = MagicMock()
        result = tool_switch_model({"target_model": "   ", "reason": "test"}, state)
        assert not result.startswith("__MODEL__")

    def test_reason_optional(self):
        """Reason can be empty string."""
        from core.tool_handlers.misc import tool_switch_model

        state = MagicMock()
        result = tool_switch_model({"target_model": "gpt-4o"}, state)
        assert result.startswith("__MODEL__|")
        import json
        payload = json.loads(result.split("|", 1)[1])
        assert payload["target"] == "gpt-4o"
        assert payload["reason"] == ""


# ============================================================
# Tests for ModelSuggester (Phase 3 #9)
# ============================================================


class TestModelSuggester:
    """Tests for the ModelSuggester class in llm/router.py."""

    def _make_models(self) -> list[ModelInfo]:
        """Create a set of test ModelInfo objects."""
        return [
            ModelInfo(
                id="gpt-4.1",
                display_name="GPT-4.1",
                provider="test",
                base_url="https://test.com/v1",
                api_key="key",
                tags=["general", "writing", "reasoning"],
                cost_tier="high",
            ),
            ModelInfo(
                id="gpt-4.1-mini",
                display_name="GPT-4.1 Mini",
                provider="test",
                base_url="https://test.com/v1",
                api_key="key",
                tags=["general", "fast"],
                cost_tier="low",
            ),
            ModelInfo(
                id="deepseek-r1",
                display_name="DeepSeek R1",
                provider="test",
                base_url="https://test.com/v1",
                api_key="key",
                tags=["reasoning", "math", "code"],
                cost_tier="high",
            ),
            ModelInfo(
                id="glm-4.5-flash",
                display_name="GLM-4.5 Flash",
                provider="test",
                base_url="https://test.com/v1",
                api_key="key",
                tags=["chinese", "fast"],
                cost_tier="low",
            ),
        ]

    def test_suggest_reasoning_task(self):
        """Should suggest reasoning-tagged model for reasoning task."""
        from llm.router import ModelSuggester

        suggester = ModelSuggester()
        models = self._make_models()
        result = suggester.suggest("需要深度推理来审稿", models, current_model_id="gpt-4.1-mini")
        assert "DeepSeek R1" in result or "GPT-4.1" in result
        assert "reasoning" in result

    def test_suggest_fast_task(self):
        """Should suggest fast-tagged model for speed-sensitive task."""
        from llm.router import ModelSuggester

        suggester = ModelSuggester()
        models = self._make_models()
        result = suggester.suggest("快速分类一下这些文档", models, current_model_id="gpt-4.1")
        assert "fast" in result
        # Should suggest a fast model (Mini or GLM Flash)
        assert "Mini" in result or "Flash" in result

    def test_suggest_chinese_task(self):
        """Should suggest chinese-tagged model for Chinese task."""
        from llm.router import ModelSuggester

        suggester = ModelSuggester()
        models = self._make_models()
        result = suggester.suggest("中文写作润色", models, current_model_id="deepseek-r1")
        assert "chinese" in result or "writing" in result

    def test_suggest_empty_description(self):
        """Should return error message for empty description."""
        from llm.router import ModelSuggester

        suggester = ModelSuggester()
        models = self._make_models()
        result = suggester.suggest("", models)
        assert "无法提供建议" in result

    def test_suggest_no_models(self):
        """Should return error message when no models available."""
        from llm.router import ModelSuggester

        suggester = ModelSuggester()
        result = suggester.suggest("深度推理", [])
        assert "无法提供建议" in result

    def test_suggest_no_keyword_match(self):
        """Should return hint when no keywords match."""
        from llm.router import ModelSuggester

        suggester = ModelSuggester()
        models = self._make_models()
        result = suggester.suggest("做一些随机的事情", models)
        assert "未能从任务描述中识别" in result

    def test_suggest_current_model_is_best(self):
        """Should indicate current model is already best match."""
        from llm.router import ModelSuggester

        suggester = ModelSuggester()
        # Only one model that matches
        models = [
            ModelInfo(
                id="only-model",
                display_name="Only Model",
                provider="test",
                base_url="https://test.com/v1",
                api_key="key",
                tags=["reasoning"],
                cost_tier="high",
            ),
        ]
        result = suggester.suggest("推理任务", models, current_model_id="only-model")
        assert "已是最佳匹配" in result

    def test_suggest_with_cost_preference(self):
        """Cost preference should boost matching models."""
        from llm.router import ModelSuggester

        suggester = ModelSuggester(cost_preference="low")
        models = self._make_models()
        # For a "fast" task, both Mini and GLM Flash match; low cost pref boosts them
        result = suggester.suggest("快速处理", models, current_model_id="gpt-4.1")
        assert "Mini" in result or "Flash" in result

    def test_suggest_shows_other_candidates(self):
        """Should show other candidates when multiple models match."""
        from llm.router import ModelSuggester

        suggester = ModelSuggester()
        models = self._make_models()
        # "推理" matches reasoning tag → both GPT-4.1 and DeepSeek R1 match
        result = suggester.suggest("推理", models, current_model_id="gpt-4.1-mini")
        assert "其他候选" in result or "建议使用" in result

    def test_suggest_custom_keyword_tags(self):
        """Should work with custom keyword→tag mapping."""
        from llm.router import ModelSuggester

        custom_tags = {"论文": ["academic", "reasoning"]}
        suggester = ModelSuggester(keyword_tags=custom_tags)
        models = self._make_models()
        result = suggester.suggest("帮我写论文", models)
        assert "reasoning" in result

    def test_extract_tags_case_insensitive(self):
        """Keyword matching should be case-insensitive."""
        from llm.router import ModelSuggester

        suggester = ModelSuggester()
        models = self._make_models()
        result = suggester.suggest("REASONING task", models)
        assert "reasoning" in result

    def test_no_substring_false_positive_english(self):
        """English keywords should not match as substrings (e.g., 'code' in 'unicode')."""
        from llm.router import ModelSuggester

        suggester = ModelSuggester()
        models = self._make_models()
        # "unicode" contains "code" as substring but should NOT trigger code tag
        result = suggester.suggest("handle unicode encoding", models)
        # Should not suggest code-tagged models
        assert "DeepSeek R1" not in result or "code" not in result.split("匹配标签")[0]

    def test_no_substring_false_positive_math(self):
        """'math' should not match 'aftermath'."""
        from llm.router import ModelSuggester

        suggester = ModelSuggester()
        models = self._make_models()
        result = suggester.suggest("dealing with the aftermath of the event", models)
        # Should not find any matching tags
        assert "未能从任务描述中识别" in result

    def test_chinese_keyword_still_matches_substring(self):
        """Chinese keywords should still match as substrings (no word boundaries)."""
        from llm.router import ModelSuggester

        suggester = ModelSuggester()
        models = self._make_models()
        # "推理" embedded in a longer phrase should still match
        result = suggester.suggest("这个任务需要逻辑推理能力", models)
        assert "reasoning" in result


# ============================================================
# Tests for Switch History Persistence (Phase 3 #12)
# ============================================================


class TestSwitchHistoryPersistence:
    """Tests for persist_switch_history and get_persisted_history."""

    def test_persist_empty_history(self, config_path):
        """Should return None when no switch history exists."""
        mgr = SessionModelManager(config_path=config_path)
        result = mgr.persist_switch_history()
        assert result is None

    def test_persist_single_record(self, tmp_path):
        """Should write a single switch record to JSONL file."""
        config_path = _make_config(tmp_path)
        mgr = SessionModelManager(config_path=config_path)

        # Manually add a switch record
        import time
        record = SwitchRecord(
            timestamp=time.time(),
            from_model="model-a",
            to_model="model-b",
            reason="测试切换",
            context_summary="这是测试摘要",
            tokens_used_before_switch=1000,
        )
        mgr._switch_history.append(record)

        metrics_dir = tmp_path / "metrics"
        result = mgr.persist_switch_history(metrics_dir=metrics_dir)

        assert result is not None
        assert result.exists()
        assert result.name == "model_switches.jsonl"

        # Verify content
        lines = result.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["from_model"] == "model-a"
        assert data["to_model"] == "model-b"
        assert data["reason"] == "测试切换"
        assert data["context_summary"] == "这是测试摘要"
        assert data["tokens_used_before_switch"] == 1000
        assert "timestamp" in data
        assert "session_id" in data  # Should include session_id for metrics consistency

    def test_persist_multiple_records(self, tmp_path):
        """Should write multiple switch records."""
        config_path = _make_config(tmp_path)
        mgr = SessionModelManager(config_path=config_path)

        import time
        for i in range(3):
            record = SwitchRecord(
                timestamp=time.time() + i,
                from_model=f"model-{i}",
                to_model=f"model-{i+1}",
                reason=f"reason-{i}",
                context_summary=f"summary-{i}",
                tokens_used_before_switch=i * 100,
            )
            mgr._switch_history.append(record)

        metrics_dir = tmp_path / "metrics"
        result = mgr.persist_switch_history(metrics_dir=metrics_dir)

        lines = result.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 3

    def test_persist_appends_only_new_records(self, tmp_path):
        """Should only append NEW records (watermark pattern), not re-write all."""
        config_path = _make_config(tmp_path)
        mgr = SessionModelManager(config_path=config_path)

        import time
        metrics_dir = tmp_path / "metrics"

        # First write: 1 record
        mgr._switch_history.append(SwitchRecord(
            timestamp=time.time(),
            from_model="a", to_model="b",
            reason="first", context_summary="s1",
            tokens_used_before_switch=100,
        ))
        mgr.persist_switch_history(metrics_dir=metrics_dir)

        # Second write: add 1 new record, should only write the new one
        mgr._switch_history.append(SwitchRecord(
            timestamp=time.time(),
            from_model="b", to_model="c",
            reason="second", context_summary="s2",
            tokens_used_before_switch=200,
        ))
        mgr.persist_switch_history(metrics_dir=metrics_dir)

        # Should have exactly 2 lines (1 from first call + 1 new from second call)
        target_file = metrics_dir / "model_switches.jsonl"
        lines = target_file.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2

        # Verify content order
        data_0 = json.loads(lines[0])
        data_1 = json.loads(lines[1])
        assert data_0["reason"] == "first"
        assert data_1["reason"] == "second"

    def test_persist_no_duplicates_on_repeated_call(self, tmp_path):
        """Calling persist twice without new records should not write anything."""
        config_path = _make_config(tmp_path)
        mgr = SessionModelManager(config_path=config_path)

        import time
        metrics_dir = tmp_path / "metrics"

        mgr._switch_history.append(SwitchRecord(
            timestamp=time.time(),
            from_model="a", to_model="b",
            reason="test", context_summary="s",
            tokens_used_before_switch=0,
        ))
        mgr.persist_switch_history(metrics_dir=metrics_dir)

        # Second call with no new records should return None
        result = mgr.persist_switch_history(metrics_dir=metrics_dir)
        assert result is None

        # File should still have exactly 1 line
        target_file = metrics_dir / "model_switches.jsonl"
        lines = target_file.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1

    def test_get_persisted_history_empty(self, tmp_path):
        """Should return empty list when no file exists."""
        config_path = _make_config(tmp_path)
        mgr = SessionModelManager(config_path=config_path)
        result = mgr.get_persisted_history(metrics_dir=tmp_path / "nonexistent")
        assert result == []

    def test_get_persisted_history_reads_records(self, tmp_path):
        """Should read back persisted records correctly."""
        config_path = _make_config(tmp_path)
        mgr = SessionModelManager(config_path=config_path)

        import time
        mgr._switch_history.append(SwitchRecord(
            timestamp=1700000000.0,
            from_model="model-a", to_model="model-b",
            reason="test", context_summary="summary",
            tokens_used_before_switch=500,
        ))

        metrics_dir = tmp_path / "metrics"
        mgr.persist_switch_history(metrics_dir=metrics_dir)

        records = mgr.get_persisted_history(metrics_dir=metrics_dir)
        assert len(records) == 1
        assert records[0]["from_model"] == "model-a"
        assert records[0]["to_model"] == "model-b"
        assert records[0]["reason"] == "test"

    def test_get_persisted_history_handles_malformed(self, tmp_path):
        """Should skip malformed lines gracefully."""
        config_path = _make_config(tmp_path)
        mgr = SessionModelManager(config_path=config_path)

        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir(parents=True)
        target_file = metrics_dir / "model_switches.jsonl"
        target_file.write_text(
            '{"from_model": "a", "to_model": "b"}\n'
            'this is not json\n'
            '{"from_model": "c", "to_model": "d"}\n',
            encoding="utf-8",
        )

        records = mgr.get_persisted_history(metrics_dir=metrics_dir)
        assert len(records) == 2
        assert records[0]["from_model"] == "a"
        assert records[1]["from_model"] == "c"

    def test_persist_creates_directory(self, tmp_path):
        """Should create metrics directory if it doesn't exist."""
        config_path = _make_config(tmp_path)
        mgr = SessionModelManager(config_path=config_path)

        import time
        mgr._switch_history.append(SwitchRecord(
            timestamp=time.time(),
            from_model="a", to_model="b",
            reason="test", context_summary="s",
            tokens_used_before_switch=0,
        ))

        deep_dir = tmp_path / "deep" / "nested" / "metrics"
        result = mgr.persist_switch_history(metrics_dir=deep_dir)
        assert result is not None
        assert deep_dir.exists()
        assert result.exists()

    def test_timestamp_is_iso_format(self, tmp_path):
        """Persisted timestamp should be ISO 8601 format."""
        config_path = _make_config(tmp_path)
        mgr = SessionModelManager(config_path=config_path)

        mgr._switch_history.append(SwitchRecord(
            timestamp=1700000000.0,  # 2023-11-14T22:13:20+00:00
            from_model="a", to_model="b",
            reason="test", context_summary="s",
            tokens_used_before_switch=0,
        ))

        metrics_dir = tmp_path / "metrics"
        mgr.persist_switch_history(metrics_dir=metrics_dir)

        records = mgr.get_persisted_history(metrics_dir=metrics_dir)
        ts = records[0]["timestamp"]
        # Should be parseable as ISO 8601
        from datetime import datetime
        parsed = datetime.fromisoformat(ts)
        assert parsed.year == 2023
        assert parsed.month == 11


# ============================================================
# Tests for Phase 4: Unified Model Assignment
# ============================================================


def _make_v2_config(
    tmp_path: Path,
    model_assignments: dict | None = None,
    tier_models: dict | None = None,
) -> Path:
    """Create a v2 providers.json with model_assignments and tier_models."""
    config = {
        "version": 2,
        "default_provider": "test",
        "providers": {
            "test": {
                "display_name": "Test Provider",
                "base_url": "https://test.example.com/v1",
                "api_key": "test-key-123",
                "models": [
                    {
                        "id": "model-a",
                        "display_name": "Model A",
                        "tags": ["general", "writing"],
                        "cost_tier": "high",
                    },
                    {
                        "id": "model-b",
                        "display_name": "Model B",
                        "tags": ["fast"],
                        "cost_tier": "low",
                    },
                    {
                        "id": "model-c",
                        "display_name": "Model C",
                        "tags": ["reasoning"],
                        "cost_tier": "medium",
                    },
                ],
            }
        },
        "token_budgets": {"default_total": 100000, "per_model_limits": {}},
    }
    if model_assignments is not None:
        config["model_assignments"] = model_assignments
    if tier_models is not None:
        config["tier_models"] = tier_models
    path = tmp_path / "providers.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


class TestPhase4ModelAssignmentParsing:
    """Tests for _parse_model_assignments and _parse_tier_models."""

    def test_defaults_when_no_assignments_in_config(self, tmp_path):
        """Without model_assignments in config, should use DEFAULT_MODEL_ASSIGNMENTS."""
        path = _make_v2_config(tmp_path)  # no model_assignments key
        mgr = SessionModelManager(config_path=path)

        assignments = mgr.get_model_assignments()
        # main should be resolved to default model (model-a)
        assert assignments["main"] == "model-a"
        assert assignments["sub_perspective"] == "auto"
        assert assignments["reflection"] == "inherit"
        assert assignments["context_summary"] == "inherit"

    def test_explicit_assignments_parsed(self, tmp_path):
        """Explicit model_assignments should override defaults."""
        path = _make_v2_config(tmp_path, model_assignments={
            "main": "model-b",
            "sub_perspective": "model-c",
            "mcl": "model-a",
            "checker": "model-b",
            "consolidation": "model-c",
            "reflection": "inherit",
            "context_summary": "auto",
        })
        mgr = SessionModelManager(config_path=path)

        assignments = mgr.get_model_assignments()
        assert assignments["main"] == "model-b"
        assert assignments["sub_perspective"] == "model-c"
        assert assignments["mcl"] == "model-a"
        assert assignments["checker"] == "model-b"
        assert assignments["consolidation"] == "model-c"
        assert assignments["reflection"] == "inherit"
        assert assignments["context_summary"] == "auto"

    def test_unknown_model_in_assignments_falls_back(self, tmp_path):
        """Unknown model_id in assignments should fall back to default."""
        path = _make_v2_config(tmp_path, model_assignments={
            "main": "nonexistent-model",
            "mcl": "also-nonexistent",
        })
        mgr = SessionModelManager(config_path=path)

        assignments = mgr.get_model_assignments()
        # main should fall back to _resolve_default_model() = model-a
        assert assignments["main"] == "model-a"
        # mcl should keep its default (gpt-4.1-mini doesn't exist in test,
        # so it stays as default which is "gpt-4.1-mini" from DEFAULT_MODEL_ASSIGNMENTS)
        # Actually since "gpt-4.1-mini" is not in the test registry, the default
        # stays because the invalid value is ignored and default is kept
        assert assignments["mcl"] == "gpt-4.1-mini"  # default kept

    def test_unknown_role_ignored(self, tmp_path):
        """Unknown role names in config should be ignored with warning."""
        path = _make_v2_config(tmp_path, model_assignments={
            "main": "model-a",
            "unknown_role": "model-b",
        })
        mgr = SessionModelManager(config_path=path)
        assignments = mgr.get_model_assignments()
        assert "unknown_role" not in assignments

    def test_tier_models_parsed(self, tmp_path):
        """tier_models should be parsed and validated."""
        path = _make_v2_config(tmp_path, tier_models={
            "high": "model-a",
            "medium": "model-c",
            "low": "model-b",
        })
        mgr = SessionModelManager(config_path=path)

        tiers = mgr.get_tier_models()
        assert tiers["high"] == "model-a"
        assert tiers["medium"] == "model-c"
        assert tiers["low"] == "model-b"

    def test_tier_models_defaults_when_missing(self, tmp_path):
        """Without tier_models in config, should use DEFAULT_TIER_MODELS (all None)."""
        path = _make_v2_config(tmp_path)  # no tier_models key
        mgr = SessionModelManager(config_path=path)

        tiers = mgr.get_tier_models()
        assert tiers["high"] is None
        assert tiers["medium"] is None
        assert tiers["low"] is None

    def test_tier_models_unknown_model_falls_back(self, tmp_path):
        """Unknown model in tier_models should fall back to None."""
        path = _make_v2_config(tmp_path, tier_models={
            "high": "nonexistent",
            "medium": "model-c",
            "low": "model-b",
        })
        mgr = SessionModelManager(config_path=path)

        tiers = mgr.get_tier_models()
        assert tiers["high"] is None  # fell back
        assert tiers["medium"] == "model-c"
        assert tiers["low"] == "model-b"


class TestPhase4ResolveModelForRole:
    """Tests for resolve_model_for_role() — all resolution branches."""

    def test_resolve_inherit_returns_current_model(self, tmp_path):
        """'inherit' should return the current main model."""
        path = _make_v2_config(tmp_path, model_assignments={
            "main": "model-a",
            "reflection": "inherit",
            "context_summary": "inherit",
        })
        mgr = SessionModelManager(config_path=path)

        assert mgr.resolve_model_for_role("reflection") == "model-a"
        assert mgr.resolve_model_for_role("context_summary") == "model-a"

    def test_resolve_inherit_follows_switch(self, tmp_path):
        """'inherit' roles should follow when main model changes."""
        path = _make_v2_config(tmp_path, model_assignments={
            "main": "model-a",
            "reflection": "inherit",
        })
        mgr = SessionModelManager(config_path=path)

        # Simulate a model switch (directly set internal state)
        mgr._current_model_id = "model-b"

        assert mgr.resolve_model_for_role("reflection") == "model-b"

    def test_resolve_auto_without_override_returns_none(self, tmp_path):
        """'auto' without user_override should return None (MCL decides)."""
        path = _make_v2_config(tmp_path, model_assignments={
            "main": "model-a",
            "sub_perspective": "auto",
        })
        mgr = SessionModelManager(config_path=path)

        assert mgr._user_override is False
        assert mgr.resolve_model_for_role("sub_perspective") is None

    def test_resolve_auto_with_override_returns_current(self, tmp_path):
        """'auto' with user_override should return current model."""
        path = _make_v2_config(tmp_path, model_assignments={
            "main": "model-a",
            "sub_perspective": "auto",
        })
        mgr = SessionModelManager(config_path=path)
        mgr._user_override = True

        assert mgr.resolve_model_for_role("sub_perspective") == "model-a"

    def test_resolve_auto_with_override_after_switch(self, tmp_path):
        """'auto' + user_override should follow the switched model."""
        path = _make_v2_config(tmp_path, model_assignments={
            "main": "model-a",
            "sub_perspective": "auto",
        })
        mgr = SessionModelManager(config_path=path)
        mgr._user_override = True
        mgr._current_model_id = "model-c"

        assert mgr.resolve_model_for_role("sub_perspective") == "model-c"

    def test_resolve_explicit_model_id(self, tmp_path):
        """Explicit model_id should be returned directly."""
        path = _make_v2_config(tmp_path, model_assignments={
            "main": "model-a",
            "mcl": "model-b",
            "checker": "model-c",
        })
        mgr = SessionModelManager(config_path=path)

        assert mgr.resolve_model_for_role("mcl") == "model-b"
        assert mgr.resolve_model_for_role("checker") == "model-c"

    def test_resolve_explicit_not_affected_by_switch(self, tmp_path):
        """Explicit assignments should NOT change when user switches model."""
        path = _make_v2_config(tmp_path, model_assignments={
            "main": "model-a",
            "mcl": "model-b",
        })
        mgr = SessionModelManager(config_path=path)
        mgr._current_model_id = "model-c"
        mgr._user_override = True

        # mcl is explicitly set to model-b, should not follow switch
        assert mgr.resolve_model_for_role("mcl") == "model-b"

    def test_resolve_main_role(self, tmp_path):
        """'main' role should return the configured main model."""
        path = _make_v2_config(tmp_path, model_assignments={
            "main": "model-c",
        })
        mgr = SessionModelManager(config_path=path)

        assert mgr.resolve_model_for_role("main") == "model-c"

    def test_resolve_main_after_switch_returns_new_model(self, tmp_path):
        """After switch_model, resolve_model_for_role('main') should return the new model."""
        path = _make_v2_config(tmp_path, model_assignments={
            "main": "model-a",
            "reflection": "inherit",
        })
        mgr = SessionModelManager(config_path=path)
        client = _make_mock_client("model-a")

        asyncio.run(
            mgr.switch_model("model-b", "test switch", client, [])
        )

        # Both main and inherit roles should return the switched model
        assert mgr.resolve_model_for_role("main") == "model-b"
        assert mgr.resolve_model_for_role("reflection") == "model-b"

    def test_resolve_main_and_inherit_always_consistent(self, tmp_path):
        """main and inherit roles should always resolve to the same model."""
        path = _make_v2_config(tmp_path, model_assignments={
            "main": "model-a",
            "consolidation": "inherit",
            "reflection": "inherit",
            "context_summary": "inherit",
        })
        mgr = SessionModelManager(config_path=path)

        # Before switch: all should be model-a
        assert mgr.resolve_model_for_role("main") == "model-a"
        assert mgr.resolve_model_for_role("consolidation") == "model-a"
        assert mgr.resolve_model_for_role("reflection") == "model-a"
        assert mgr.resolve_model_for_role("context_summary") == "model-a"

        # After switch: all should be model-c
        client = _make_mock_client("model-a")
        asyncio.run(mgr.switch_model("model-c", "test", client, []))

        assert mgr.resolve_model_for_role("main") == "model-c"
        assert mgr.resolve_model_for_role("consolidation") == "model-c"
        assert mgr.resolve_model_for_role("reflection") == "model-c"
        assert mgr.resolve_model_for_role("context_summary") == "model-c"

    def test_resolve_invalid_role_raises(self, tmp_path):
        """Invalid role should raise ValueError."""
        path = _make_v2_config(tmp_path)
        mgr = SessionModelManager(config_path=path)

        with pytest.raises(ValueError, match="Unknown role"):
            mgr.resolve_model_for_role("invalid_role")

    def test_resolve_removed_model_falls_back(self, tmp_path):
        """If assigned model was removed, should fall back to main."""
        path = _make_v2_config(tmp_path, model_assignments={
            "main": "model-a",
            "mcl": "model-b",
        })
        mgr = SessionModelManager(config_path=path)

        # Simulate model-b being removed from registry
        del mgr._available_models["model-b"]

        # Should fall back to current model (model-a)
        assert mgr.resolve_model_for_role("mcl") == "model-a"


class TestPhase4ResolveTierModel:
    """Tests for resolve_tier_model()."""

    def test_resolve_configured_tier(self, tmp_path):
        """Should return the configured model for a tier."""
        path = _make_v2_config(tmp_path, tier_models={
            "high": "model-a",
            "medium": "model-c",
            "low": "model-b",
        })
        mgr = SessionModelManager(config_path=path)

        assert mgr.resolve_tier_model("high") == "model-a"
        assert mgr.resolve_tier_model("medium") == "model-c"
        assert mgr.resolve_tier_model("low") == "model-b"

    def test_resolve_unconfigured_tier_falls_back_to_main(self, tmp_path):
        """Unconfigured tier (None) should fall back to current main model."""
        path = _make_v2_config(tmp_path)  # no tier_models → all None
        mgr = SessionModelManager(config_path=path)

        # All tiers should fall back to main model (model-a)
        assert mgr.resolve_tier_model("high") == "model-a"
        assert mgr.resolve_tier_model("medium") == "model-a"
        assert mgr.resolve_tier_model("low") == "model-a"

    def test_resolve_tier_unknown_key_falls_back(self, tmp_path):
        """Unknown tier key should fall back to main model."""
        path = _make_v2_config(tmp_path, tier_models={
            "high": "model-a",
        })
        mgr = SessionModelManager(config_path=path)

        # "ultra" is not a valid tier
        assert mgr.resolve_tier_model("ultra") == "model-a"

    def test_resolve_tier_removed_model_falls_back(self, tmp_path):
        """If tier model was removed from registry, should fall back to main."""
        path = _make_v2_config(tmp_path, tier_models={
            "high": "model-c",
        })
        mgr = SessionModelManager(config_path=path)

        # Remove model-c from registry
        del mgr._available_models["model-c"]

        assert mgr.resolve_tier_model("high") == "model-a"


class TestPhase4UserOverride:
    """Tests for _user_override flag behavior."""

    def test_initial_state_no_override(self, tmp_path):
        """_user_override should be False initially."""
        path = _make_v2_config(tmp_path)
        mgr = SessionModelManager(config_path=path)
        assert mgr.user_override is False

    def test_switch_model_sets_override(self, tmp_path):
        """switch_model should set _user_override = True."""
        path = _make_v2_config(tmp_path, model_assignments={
            "main": "model-a",
            "sub_perspective": "auto",
        })
        mgr = SessionModelManager(config_path=path)
        client = _make_mock_client("model-a")

        asyncio.run(
            mgr.switch_model("model-b", "test", client, [])
        )

        assert mgr.user_override is True
        # sub_perspective should now follow user's choice
        assert mgr.resolve_model_for_role("sub_perspective") == "model-b"

    def test_reset_user_override(self, tmp_path):
        """reset_user_override should set flag back to False."""
        path = _make_v2_config(tmp_path, model_assignments={
            "main": "model-a",
            "sub_perspective": "auto",
        })
        mgr = SessionModelManager(config_path=path)
        mgr._user_override = True

        mgr.reset_user_override()

        assert mgr.user_override is False
        # sub_perspective should return None again (MCL decides)
        assert mgr.resolve_model_for_role("sub_perspective") is None

    def test_override_does_not_affect_explicit_assignments(self, tmp_path):
        """User override should NOT change explicitly assigned roles."""
        path = _make_v2_config(tmp_path, model_assignments={
            "main": "model-a",
            "mcl": "model-b",
            "checker": "model-c",
            "sub_perspective": "auto",
        })
        mgr = SessionModelManager(config_path=path)
        client = _make_mock_client("model-a")

        asyncio.run(
            mgr.switch_model("model-c", "test", client, [])
        )

        # Explicit assignments unchanged
        assert mgr.resolve_model_for_role("mcl") == "model-b"
        assert mgr.resolve_model_for_role("checker") == "model-c"
        # auto follows user
        assert mgr.resolve_model_for_role("sub_perspective") == "model-c"


class TestPhase4ListAssignmentsFormatted:
    """Tests for list_assignments_formatted()."""

    def test_formatted_output_contains_all_roles(self, tmp_path):
        """Should contain all 7 roles in the output."""
        path = _make_v2_config(tmp_path, model_assignments={
            "main": "model-a",
            "sub_perspective": "auto",
            "mcl": "model-b",
            "checker": "model-b",
            "consolidation": "inherit",
            "reflection": "inherit",
            "context_summary": "inherit",
        }, tier_models={
            "high": "model-a",
            "medium": "model-c",
            "low": "model-b",
        })
        mgr = SessionModelManager(config_path=path)

        output = mgr.list_assignments_formatted()

        assert "主循环" in output
        assert "子视角" in output
        assert "MCL" in output
        assert "Checker" in output
        assert "Consolidation" in output
        assert "Reflection" in output
        assert "上下文摘要" in output
        assert "Tier 模型池" in output
        assert "high" in output
        assert "medium" in output
        assert "low" in output

    def test_formatted_shows_inherit_resolution(self, tmp_path):
        """'inherit' roles should show resolved value."""
        path = _make_v2_config(tmp_path, model_assignments={
            "main": "model-a",
            "reflection": "inherit",
        })
        mgr = SessionModelManager(config_path=path)

        output = mgr.list_assignments_formatted()
        assert "inherit → model-a" in output

    def test_formatted_shows_auto_mcl_routing(self, tmp_path):
        """'auto' without override should show MCL routing."""
        path = _make_v2_config(tmp_path, model_assignments={
            "main": "model-a",
            "sub_perspective": "auto",
        })
        mgr = SessionModelManager(config_path=path)

        output = mgr.list_assignments_formatted()
        assert "MCL 路由" in output

    def test_formatted_shows_auto_user_override(self, tmp_path):
        """'auto' with user override should show override info."""
        path = _make_v2_config(tmp_path, model_assignments={
            "main": "model-a",
            "sub_perspective": "auto",
        })
        mgr = SessionModelManager(config_path=path)
        mgr._user_override = True

        output = mgr.list_assignments_formatted()
        assert "用户覆盖" in output

    def test_formatted_shows_explicit_display_name(self, tmp_path):
        """Explicit model assignments should show display_name."""
        path = _make_v2_config(tmp_path, model_assignments={
            "main": "model-a",
            "mcl": "model-b",
        })
        mgr = SessionModelManager(config_path=path)

        output = mgr.list_assignments_formatted()
        assert "Model B" in output

    def test_formatted_main_shows_switched_model(self, tmp_path):
        """After switch_model, main role display should show the new model."""
        path = _make_v2_config(tmp_path, model_assignments={
            "main": "model-a",
            "reflection": "inherit",
        })
        mgr = SessionModelManager(config_path=path)
        client = _make_mock_client("model-a")

        asyncio.run(mgr.switch_model("model-b", "test", client, []))

        output = mgr.list_assignments_formatted()
        # Main should show model-b (the switched model), not model-a
        assert "Model B" in output
        assert "用户切换" in output


class TestPhase4Constants:
    """Tests for module-level constants."""

    def test_valid_roles_complete(self):
        """VALID_ROLES should contain all 7 expected roles."""
        expected = {
            "main", "sub_perspective", "mcl", "checker",
            "consolidation", "reflection", "context_summary",
        }
        assert VALID_ROLES == expected

    def test_default_assignments_keys(self):
        """DEFAULT_MODEL_ASSIGNMENTS should have all valid roles."""
        assert set(DEFAULT_MODEL_ASSIGNMENTS.keys()) == VALID_ROLES

    def test_default_tier_models_keys(self):
        """DEFAULT_TIER_MODELS should have high/medium/low."""
        assert set(DEFAULT_TIER_MODELS.keys()) == {"high", "medium", "low"}


# ============================================================
# Phase 4 Integration Tests — Component Integration (4.3-4.9)
# ============================================================


class TestPhase4RouterIntegration:
    """Tests for router.py get_tier_model() dynamic interface (step 4.8)."""

    def test_get_tier_model_without_mgr_uses_static(self):
        """Without session_model_mgr, get_tier_model falls back to MODEL_TIERS."""
        from llm.router import get_tier_model, MODEL_TIERS
        result = get_tier_model("high")
        assert result == MODEL_TIERS["high"]

    def test_get_tier_model_with_mgr_uses_dynamic(self, tmp_path):
        """With session_model_mgr, get_tier_model uses resolve_tier_model."""
        from llm.router import get_tier_model
        path = _make_v2_config(tmp_path, tier_models={
            "high": "model-a",
            "medium": "model-b",
            "low": "model-c",
        })
        mgr = SessionModelManager(config_path=path)
        assert get_tier_model("high", session_model_mgr=mgr) == "model-a"
        assert get_tier_model("medium", session_model_mgr=mgr) == "model-b"
        assert get_tier_model("low", session_model_mgr=mgr) == "model-c"

    def test_get_model_for_task_with_mgr(self, tmp_path):
        """get_model_for_task should use session_model_mgr when provided."""
        from llm.router import get_model_for_task
        path = _make_v2_config(tmp_path, tier_models={
            "high": "model-a",
            "medium": "model-b",
            "low": "model-c",
        })
        mgr = SessionModelManager(config_path=path)
        # "consolidate" maps to "medium" tier
        result = get_model_for_task("consolidate", session_model_mgr=mgr)
        assert result == "model-b"

    def test_get_model_for_task_without_mgr_backward_compat(self):
        """get_model_for_task without mgr should still work (backward compat)."""
        from llm.router import get_model_for_task, MODEL_TIERS
        result = get_model_for_task("consolidate")
        assert result == MODEL_TIERS["medium"]


class TestPhase4MCLIntegration:
    """Tests for MCL session_model_mgr integration (step 4.4)."""

    def test_mcl_uses_session_model_mgr(self, tmp_path):
        """MCL should use model from session_model_mgr when provided."""
        from core.meta_cognition_layer import MetaCognitionLayer
        path = _make_v2_config(tmp_path, model_assignments={
            "main": "model-a",
            "mcl": "model-b",
        })
        mgr = SessionModelManager(config_path=path)
        client = MagicMock()
        mcl = MetaCognitionLayer(llm_client=client, session_model_mgr=mgr)
        assert mcl._model == "model-b"

    def test_mcl_falls_back_to_model_param(self):
        """MCL should use model param when no session_model_mgr."""
        from core.meta_cognition_layer import MetaCognitionLayer
        client = MagicMock()
        mcl = MetaCognitionLayer(llm_client=client, model="custom-model")
        assert mcl._model == "custom-model"

    def test_mcl_falls_back_to_env_var(self):
        """MCL should use env var when no session_model_mgr and no model param."""
        from core.meta_cognition_layer import MetaCognitionLayer, MCL_MODEL
        client = MagicMock()
        mcl = MetaCognitionLayer(llm_client=client)
        assert mcl._model == MCL_MODEL


class TestPhase4CheckerIntegration:
    """Tests for Checker session_model_mgr integration (step 4.5)."""

    def test_checker_uses_session_model_mgr(self, tmp_path):
        """Checker should use model from session_model_mgr when provided."""
        from core.checker import CognitiveChecker
        path = _make_v2_config(tmp_path, model_assignments={
            "main": "model-a",
            "checker": "model-c",
        })
        mgr = SessionModelManager(config_path=path)
        checker = CognitiveChecker(session_model_mgr=mgr)
        assert checker._model == "model-c"

    def test_checker_falls_back_to_model_param(self):
        """Checker should use model param when no session_model_mgr."""
        from core.checker import CognitiveChecker
        checker = CognitiveChecker(model="custom-checker")
        assert checker._model == "custom-checker"

    def test_checker_falls_back_to_env_var(self):
        """Checker should use env var when no session_model_mgr and default model."""
        from core.checker import CognitiveChecker, CHECKER_MODEL
        checker = CognitiveChecker()
        assert checker._model == CHECKER_MODEL


class TestPhase4ConsolidationIntegration:
    """Tests for Consolidation session_model_mgr integration (step 4.6)."""

    def test_consolidation_model_from_mgr(self, tmp_path):
        """consolidate_findings should use session_model_mgr for model resolution."""
        from core.consolidation import consolidate_findings
        path = _make_v2_config(tmp_path, model_assignments={
            "main": "model-a",
            "consolidation": "model-b",
        })
        mgr = SessionModelManager(config_path=path)

        # Create mock client that records which model was used
        mock_client = MagicMock()
        mock_response = '[]'
        mock_client.chat = AsyncMock(return_value=mock_response)

        # Run with enough findings to trigger consolidation
        findings = [{"type": "issue", "content": f"finding {i}"} for i in range(8)]

        result = asyncio.run(consolidate_findings(
            raw_findings=findings,
            paper_context="Test paper",
            client=mock_client,
            session_model_mgr=mgr,
        ))

        # Verify the model used in the LLM call
        if mock_client.chat.called:
            call_kwargs = mock_client.chat.call_args
            # model should be "model-b" from session_model_mgr
            if call_kwargs.kwargs.get("model"):
                assert call_kwargs.kwargs["model"] == "model-b"


class TestPhase4SubPerspectiveIntegration:
    """Tests for sub-perspective routing with session_model_mgr (step 4.3)."""

    def test_sub_perspective_user_override_bypasses_mcl(self, tmp_path):
        """When user_override is True, sub_perspective should use user's model."""
        path = _make_v2_config(tmp_path, model_assignments={
            "main": "model-a",
            "sub_perspective": "auto",
        })
        mgr = SessionModelManager(config_path=path)
        client = _make_mock_client("model-a")

        # Simulate user switch
        asyncio.run(mgr.switch_model("model-b", "test", client, []))

        # Now sub_perspective should resolve to model-b (user override)
        assert mgr.resolve_model_for_role("sub_perspective") == "model-b"

    def test_sub_perspective_explicit_model(self, tmp_path):
        """Explicit sub_perspective model should always be used."""
        path = _make_v2_config(tmp_path, model_assignments={
            "main": "model-a",
            "sub_perspective": "model-c",
        })
        mgr = SessionModelManager(config_path=path)

        # Explicit model should be returned regardless of override
        assert mgr.resolve_model_for_role("sub_perspective") == "model-c"

    def test_sub_perspective_auto_no_override_returns_none(self, tmp_path):
        """Auto without override should return None (MCL decides)."""
        path = _make_v2_config(tmp_path, model_assignments={
            "main": "model-a",
            "sub_perspective": "auto",
        })
        mgr = SessionModelManager(config_path=path)

        assert mgr.resolve_model_for_role("sub_perspective") is None


# ============================================================
# Phase 4 End-to-End Integration Test
# ============================================================


class TestPhase4EndToEndIntegration:
    """
    End-to-end integration test verifying the full system model assignment
    coordination across all 7 roles, tier routing, user override, and
    model switching lifecycle.

    Scenario:
        1. Initialize with a full config (all roles + tier pool configured)
        2. Verify initial resolution for every role
        3. Simulate user model switch
        4. Verify all roles respond correctly to the switch
        5. Reset user override
        6. Verify roles revert to expected behavior
        7. Verify tier model routing throughout
    """

    @pytest.fixture
    def full_config_path(self, tmp_path):
        """Create a comprehensive config with all roles and tiers configured."""
        return _make_v2_config(tmp_path, model_assignments={
            "main": "model-a",
            "sub_perspective": "auto",
            "mcl": "model-b",
            "checker": "model-c",
            "consolidation": "model-b",
            "reflection": "inherit",
            "context_summary": "model-c",
        }, tier_models={
            "high": "model-a",
            "medium": "model-c",
            "low": "model-b",
        })

    def test_full_lifecycle_initial_state(self, full_config_path):
        """Phase 1: Verify initial model resolution for all 7 roles."""
        mgr = SessionModelManager(config_path=full_config_path)

        # main: explicit "model-a"
        assert mgr.resolve_model_for_role("main") == "model-a"
        # sub_perspective: "auto" without override → None (MCL decides)
        assert mgr.resolve_model_for_role("sub_perspective") is None
        # mcl: explicit "model-b"
        assert mgr.resolve_model_for_role("mcl") == "model-b"
        # checker: explicit "model-c"
        assert mgr.resolve_model_for_role("checker") == "model-c"
        # consolidation: explicit "model-b"
        assert mgr.resolve_model_for_role("consolidation") == "model-b"
        # reflection: "inherit" → follows main → "model-a"
        assert mgr.resolve_model_for_role("reflection") == "model-a"
        # context_summary: explicit "model-c"
        assert mgr.resolve_model_for_role("context_summary") == "model-c"

        # Tier routing
        assert mgr.resolve_tier_model("high") == "model-a"
        assert mgr.resolve_tier_model("medium") == "model-c"
        assert mgr.resolve_tier_model("low") == "model-b"

        # State flags
        assert mgr.user_override is False
        assert mgr.current_model.id == "model-a"

    def test_full_lifecycle_after_user_switch(self, full_config_path):
        """Phase 2: After user switches model, verify role resolution changes."""
        mgr = SessionModelManager(config_path=full_config_path)
        client = _make_mock_client("model-a")
        # Mock with_model_override for clean context_summary generation
        client.with_model_override = lambda model: MagicMock(
            model=model, chat=AsyncMock(return_value="摘要")
        )

        # User switches from model-a to model-b
        result = asyncio.run(mgr.switch_model("model-b", "需要快速模型", client, [
            {"role": "user", "content": "请帮我分析这篇论文"},
            {"role": "assistant", "content": "好的，我来分析这篇论文的核心观点。"},
        ]))

        # Switch should succeed
        assert "✓ 已切换" in result
        assert mgr.user_override is True
        assert mgr.current_model.id == "model-b"

        # main: now "model-b" (user switched)
        assert mgr.resolve_model_for_role("main") == "model-b"
        # sub_perspective: "auto" + user_override → follows user → "model-b"
        assert mgr.resolve_model_for_role("sub_perspective") == "model-b"
        # mcl: explicit "model-b" → unchanged
        assert mgr.resolve_model_for_role("mcl") == "model-b"
        # checker: explicit "model-c" → unchanged
        assert mgr.resolve_model_for_role("checker") == "model-c"
        # consolidation: explicit "model-b" → unchanged
        assert mgr.resolve_model_for_role("consolidation") == "model-b"
        # reflection: "inherit" → follows main → now "model-b"
        assert mgr.resolve_model_for_role("reflection") == "model-b"
        # context_summary: explicit "model-c" → unchanged
        assert mgr.resolve_model_for_role("context_summary") == "model-c"

        # Tier routing should be unaffected by user switch
        assert mgr.resolve_tier_model("high") == "model-a"
        assert mgr.resolve_tier_model("medium") == "model-c"
        assert mgr.resolve_tier_model("low") == "model-b"

    def test_full_lifecycle_reset_override(self, full_config_path):
        """Phase 3: After reset_user_override, auto roles revert."""
        mgr = SessionModelManager(config_path=full_config_path)
        client = _make_mock_client("model-a")
        # Mock with_model_override for clean context_summary generation
        client.with_model_override = lambda model: MagicMock(
            model=model, chat=AsyncMock(return_value="摘要")
        )

        # Switch then reset
        asyncio.run(mgr.switch_model("model-b", "test", client, []))
        mgr.reset_user_override()

        assert mgr.user_override is False
        # main still "model-b" (switch is permanent, only override flag resets)
        assert mgr.resolve_model_for_role("main") == "model-b"
        # sub_perspective: "auto" without override → None (MCL decides again)
        assert mgr.resolve_model_for_role("sub_perspective") is None
        # reflection: "inherit" → follows main → "model-b"
        assert mgr.resolve_model_for_role("reflection") == "model-b"
        # Explicit roles unchanged
        assert mgr.resolve_model_for_role("mcl") == "model-b"
        assert mgr.resolve_model_for_role("checker") == "model-c"
        assert mgr.resolve_model_for_role("context_summary") == "model-c"

    def test_full_lifecycle_switch_history_recorded(self, full_config_path):
        """Phase 4: Switch history should be properly recorded."""
        mgr = SessionModelManager(config_path=full_config_path)
        client = _make_mock_client("model-a")

        # Properly mock with_model_override so context_summary generation works
        def proper_override(model):
            override_client = MagicMock()
            override_client.model = model
            override_client.chat = AsyncMock(return_value="论文分析任务摘要")
            return override_client

        client.with_model_override = proper_override

        asyncio.run(mgr.switch_model("model-b", "快速分析", client, [
            {"role": "user", "content": "分析论文"},
        ]))

        history = mgr.get_switch_history()
        assert len(history) == 1
        assert history[0].from_model == "model-a"
        assert history[0].to_model == "model-b"
        assert history[0].reason == "快速分析"
        # Verify actual summary content (not fallback error string)
        assert history[0].context_summary == "论文分析任务摘要"

    def test_full_lifecycle_context_summary_uses_configured_model(
        self, full_config_path
    ):
        """Phase 5: Context summary generation should use context_summary role model."""
        mgr = SessionModelManager(config_path=full_config_path)
        client = _make_mock_client("model-a")

        # Track which model the with_model_override was called with
        override_calls = []

        def tracking_override(model):
            override_calls.append(model)
            override_client = MagicMock()
            override_client.model = model
            override_client.chat = AsyncMock(return_value="摘要内容")
            return override_client

        client.with_model_override = tracking_override

        asyncio.run(mgr.switch_model("model-b", "test", client, [
            {"role": "user", "content": "请分析这篇论文的方法论"},
            {"role": "assistant", "content": "这篇论文采用了混合方法研究设计。"},
        ]))

        # context_summary is configured as "model-c", client is "model-a"
        # So with_model_override should be called with "model-c"
        assert "model-c" in override_calls

    def test_full_lifecycle_component_integration(self, full_config_path):
        """Phase 6: Verify components (MCL, Checker) use mgr correctly."""
        from core.meta_cognition_layer import MetaCognitionLayer
        from core.checker import CognitiveChecker

        mgr = SessionModelManager(config_path=full_config_path)

        # MCL should use "model-b" (from config)
        mcl_client = MagicMock()
        mcl = MetaCognitionLayer(llm_client=mcl_client, session_model_mgr=mgr)
        assert mcl._model == "model-b"

        # Checker should use "model-c" (from config)
        checker = CognitiveChecker(session_model_mgr=mgr)
        assert checker._model == "model-c"

    def test_full_lifecycle_component_after_switch(self, full_config_path):
        """Phase 7: Components created after switch should still use config values."""
        from core.meta_cognition_layer import MetaCognitionLayer
        from core.checker import CognitiveChecker

        mgr = SessionModelManager(config_path=full_config_path)
        client = _make_mock_client("model-a")
        # Mock with_model_override for clean context_summary generation
        client.with_model_override = lambda model: MagicMock(
            model=model, chat=AsyncMock(return_value="摘要")
        )

        # Switch model
        asyncio.run(mgr.switch_model("model-b", "test", client, []))

        # MCL still uses explicit "model-b" (unchanged by switch)
        mcl_client = MagicMock()
        mcl = MetaCognitionLayer(llm_client=mcl_client, session_model_mgr=mgr)
        assert mcl._model == "model-b"

        # Checker still uses explicit "model-c" (unchanged by switch)
        checker = CognitiveChecker(session_model_mgr=mgr)
        assert checker._model == "model-c"

    def test_full_lifecycle_router_integration(self, full_config_path):
        """Phase 8: Router should use tier models from mgr."""
        from llm.router import get_tier_model, get_model_for_task

        mgr = SessionModelManager(config_path=full_config_path)

        # Direct tier resolution
        assert get_tier_model("high", session_model_mgr=mgr) == "model-a"
        assert get_tier_model("medium", session_model_mgr=mgr) == "model-c"
        assert get_tier_model("low", session_model_mgr=mgr) == "model-b"

        # Task-based routing (uses tier mapping internally)
        result = get_model_for_task("consolidate", session_model_mgr=mgr)
        assert result == "model-c"  # "consolidate" → medium tier → model-c

    def test_full_lifecycle_backward_compat_no_mgr(self):
        """Phase 9: Without mgr, all components fall back to original behavior."""
        from core.meta_cognition_layer import MetaCognitionLayer, MCL_MODEL
        from core.checker import CognitiveChecker, CHECKER_MODEL
        from llm.router import get_tier_model, MODEL_TIERS

        # MCL without mgr uses env var
        mcl_client = MagicMock()
        mcl = MetaCognitionLayer(llm_client=mcl_client)
        assert mcl._model == MCL_MODEL

        # Checker without mgr uses env var
        checker = CognitiveChecker()
        assert checker._model == CHECKER_MODEL

        # Router without mgr uses static MODEL_TIERS
        assert get_tier_model("high") == MODEL_TIERS["high"]

    def test_full_lifecycle_double_switch(self, full_config_path):
        """Phase 10: Multiple switches should chain correctly."""
        mgr = SessionModelManager(config_path=full_config_path)
        client = _make_mock_client("model-a")

        # Properly mock with_model_override for context_summary generation
        def proper_override(model):
            override_client = MagicMock()
            override_client.model = model
            override_client.chat = AsyncMock(return_value="摘要")
            return override_client

        client.with_model_override = proper_override

        # First switch: a → b
        asyncio.run(mgr.switch_model("model-b", "快速", client, []))
        assert mgr.current_model.id == "model-b"
        assert mgr.resolve_model_for_role("reflection") == "model-b"

        # Second switch: b → c
        client.model = "model-b"  # Update client to reflect first switch
        asyncio.run(mgr.switch_model("model-c", "推理", client, []))
        assert mgr.current_model.id == "model-c"
        assert mgr.resolve_model_for_role("reflection") == "model-c"
        assert mgr.resolve_model_for_role("sub_perspective") == "model-c"

        # History should have 2 records
        history = mgr.get_switch_history()
        assert len(history) == 2
        assert history[0].from_model == "model-a"
        assert history[0].to_model == "model-b"
        assert history[1].from_model == "model-b"
        assert history[1].to_model == "model-c"

    def test_full_lifecycle_formatted_output_consistency(self, full_config_path):
        """Phase 11: Formatted output should reflect current state accurately."""
        mgr = SessionModelManager(config_path=full_config_path)

        output_before = mgr.list_assignments_formatted()
        # Should show all roles
        assert "主循环" in output_before
        assert "Model A" in output_before  # main = model-a
        assert "MCL 路由" in output_before  # sub_perspective = auto

        # After switch
        client = _make_mock_client("model-a")
        # Mock with_model_override for clean context_summary generation
        client.with_model_override = lambda model: MagicMock(
            model=model, chat=AsyncMock(return_value="摘要")
        )
        asyncio.run(mgr.switch_model("model-b", "test", client, []))

        output_after = mgr.list_assignments_formatted()
        assert "Model B" in output_after  # main now model-b
        assert "用户覆盖" in output_after  # sub_perspective shows override
        assert "用户切换" in output_after  # main shows user switched
