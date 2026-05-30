"""Unit tests for v2/llm/client.py retry/timeout/backoff behavior.

Tests cover:
- Error classification (_is_transient_error, _is_rate_limit)
- Retry-After header extraction (_extract_retry_after)
- Backoff computation (_compute_backoff)
- Total timeout enforcement (TimeoutError raised)
- Retry on transient errors (429, 500, connection errors)
- Fast-fail on permanent errors (400, 401, 404)
- Logging output (no print to stderr)
"""

from __future__ import annotations

import asyncio
import logging
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Import module under test
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from llm.client import (
    _is_transient_error,
    _is_rate_limit,
    _extract_retry_after,
    LLMClient,
)


# ============================================================
# Test Error Classification
# ============================================================

class TestIsTransientError:
    """Test _is_transient_error classification."""

    def test_429_rate_limit(self):
        e = Exception("rate limit exceeded")
        e.status_code = 429
        assert _is_transient_error(e) is True

    def test_500_server_error(self):
        e = Exception("internal server error")
        e.status_code = 500
        assert _is_transient_error(e) is True

    def test_502_bad_gateway(self):
        e = Exception("bad gateway")
        e.status_code = 502
        assert _is_transient_error(e) is True

    def test_503_service_unavailable(self):
        e = Exception("service unavailable")
        e.status_code = 503
        assert _is_transient_error(e) is True

    def test_400_not_transient(self):
        e = Exception("bad request")
        e.status_code = 400
        assert _is_transient_error(e) is False

    def test_401_not_transient(self):
        e = Exception("unauthorized")
        e.status_code = 401
        assert _is_transient_error(e) is False

    def test_404_not_transient(self):
        e = Exception("not found")
        e.status_code = 404
        assert _is_transient_error(e) is False

    def test_timeout_error(self):
        e = TimeoutError("timed out")
        assert _is_transient_error(e) is True

    def test_connection_error(self):
        e = ConnectionError("connection reset")
        assert _is_transient_error(e) is True

    def test_connection_in_name(self):
        class ConnectionResetError(Exception):
            pass
        e = ConnectionResetError("reset")
        assert _is_transient_error(e) is True

    def test_rate_limit_in_name(self):
        class RateLimitError(Exception):
            pass
        e = RateLimitError("too many")
        assert _is_transient_error(e) is True

    def test_string_fallback_timeout(self):
        e = Exception("Request timeout after 30s")
        assert _is_transient_error(e) is True


class TestIsRateLimit:
    """Test _is_rate_limit classification."""

    def test_status_code_429(self):
        e = Exception("rate limit")
        e.status_code = 429
        assert _is_rate_limit(e) is True

    def test_rate_limit_in_class_name(self):
        class RateLimitError(Exception):
            pass
        e = RateLimitError("too many requests")
        assert _is_rate_limit(e) is True

    def test_429_in_message(self):
        e = Exception("Error code: 429 - Rate limited")
        assert _is_rate_limit(e) is True

    def test_500_not_rate_limit(self):
        e = Exception("internal error")
        e.status_code = 500
        assert _is_rate_limit(e) is False

    def test_generic_timeout_not_rate_limit(self):
        e = TimeoutError("timed out")
        assert _is_rate_limit(e) is False


# ============================================================
# Test Retry-After Extraction
# ============================================================

class TestExtractRetryAfter:
    """Test _extract_retry_after header parsing."""

    def test_response_headers(self):
        """Extract from e.response.headers (OpenAI SDK pattern)."""
        e = Exception("rate limited")
        response = MagicMock()
        response.headers = {"retry-after": "42"}
        e.response = response
        assert _extract_retry_after(e) == 42.0

    def test_response_headers_capitalized(self):
        """Retry-After with capital letters."""
        e = Exception("rate limited")
        response = MagicMock()
        response.headers = {"Retry-After": "15.5"}
        e.response = response
        assert _extract_retry_after(e) == 15.5

    def test_direct_headers_attribute(self):
        """Extract from e.headers directly (some providers)."""
        e = Exception("rate limited")
        e.headers = {"retry-after": "7"}
        assert _extract_retry_after(e) == 7.0

    def test_no_header_returns_none(self):
        """No Retry-After header means None."""
        e = Exception("rate limited")
        assert _extract_retry_after(e) is None

    def test_unparseable_value_returns_none(self):
        """Non-numeric Retry-After returns None."""
        e = Exception("rate limited")
        response = MagicMock()
        response.headers = {"retry-after": "not-a-number"}
        e.response = response
        assert _extract_retry_after(e) is None

    def test_empty_response_headers(self):
        """Empty headers dict returns None."""
        e = Exception("rate limited")
        response = MagicMock()
        response.headers = {}
        e.response = response
        assert _extract_retry_after(e) is None


# ============================================================
# Test Backoff Computation
# ============================================================

class TestComputeBackoff:
    """Test _compute_backoff logic."""

    @pytest.fixture
    def client(self):
        """Create a client with mocked OpenAI dependency."""
        with patch.dict(os.environ, {
            "OPENAI_API_KEY": "test-key",
            "OPENAI_BASE_URL": "http://fake.local/v1",
            "LLM_MODEL": "test-model",
        }):
            with patch("openai.AsyncOpenAI"):
                c = LLMClient(model="test-model")
        return c

    def test_retry_after_takes_priority(self, client):
        """When Retry-After is present, it overrides other calculations."""
        result = client._compute_backoff(attempt=0, is_rate_limit=True, retry_after=25.0)
        assert result == 25.0

    def test_retry_after_capped(self, client):
        """Retry-After is capped at 2x MAX_BACKOFF."""
        result = client._compute_backoff(attempt=0, is_rate_limit=True, retry_after=999.0)
        assert result == client.MAX_BACKOFF * 2

    def test_rate_limit_no_header(self, client):
        """Rate limit without header uses aggressive backoff."""
        result = client._compute_backoff(attempt=0, is_rate_limit=True, retry_after=None)
        # Base: 30 + 0*10 = 30, with ±25% jitter → [22.5, 37.5]
        assert 22.0 <= result <= 38.0

    def test_rate_limit_attempt_2(self, client):
        """Higher attempts increase rate-limit backoff."""
        result = client._compute_backoff(attempt=2, is_rate_limit=True, retry_after=None)
        # Base: 30 + 2*10 = 50, with ±25% jitter → [37.5, 62.5]
        assert 37.0 <= result <= 63.0

    def test_transient_exponential(self, client):
        """Non-rate-limit uses exponential backoff."""
        result = client._compute_backoff(attempt=0, is_rate_limit=False, retry_after=None)
        # Base: 2^1 = 2, with ±25% jitter → [1.5, 2.5]
        assert 1.0 <= result <= 3.0

    def test_transient_capped_at_max(self, client):
        """Exponential backoff is capped at MAX_BACKOFF."""
        result = client._compute_backoff(attempt=10, is_rate_limit=False, retry_after=None)
        # Base: min(2^11, 60) = 60, with ±25% jitter → [45, 75]
        assert result <= client.MAX_BACKOFF * 1.3  # jitter can go slightly over

    def test_minimum_backoff(self, client):
        """Backoff never goes below 0.5s."""
        # Force very small values
        result = client._compute_backoff(attempt=0, is_rate_limit=False, retry_after=None)
        assert result >= 0.5


# ============================================================
# Test Total Timeout
# ============================================================

class TestTotalTimeout:
    """Test total_timeout enforcement in chat methods."""

    @pytest.fixture
    def client(self):
        """Create a client with very short total_timeout for testing."""
        with patch.dict(os.environ, {
            "OPENAI_API_KEY": "test-key",
            "OPENAI_BASE_URL": "http://fake.local/v1",
            "LLM_MODEL": "test-model",
        }):
            with patch("openai.AsyncOpenAI") as mock_openai:
                mock_client = AsyncMock()
                mock_openai.return_value = mock_client
                c = LLMClient(model="test-model", total_timeout=0.5)
                c._mock_client = mock_client
        return c

    @pytest.mark.asyncio
    async def test_chat_total_timeout(self, client):
        """chat() raises TimeoutError when total_timeout exceeded."""
        # Make each attempt take time via side effect
        async def slow_create(**kwargs):
            await asyncio.sleep(0.3)
            raise TimeoutError("single request timeout")

        client._mock_client.chat.completions.create = slow_create

        with pytest.raises(TimeoutError, match="total timeout"):
            await client.chat(system="test", user="test", retries=5)

    @pytest.mark.asyncio
    async def test_chat_messages_total_timeout(self, client):
        """chat_messages() raises TimeoutError when total_timeout exceeded."""
        async def slow_create(**kwargs):
            await asyncio.sleep(0.3)
            raise TimeoutError("single request timeout")

        client._mock_client.chat.completions.create = slow_create

        with pytest.raises(TimeoutError, match="total timeout"):
            await client.chat_messages(
                messages=[{"role": "user", "content": "hi"}],
                retries=5,
            )

    @pytest.mark.asyncio
    async def test_default_total_timeout(self):
        """Default total_timeout is 3x per-request timeout."""
        with patch.dict(os.environ, {
            "OPENAI_API_KEY": "test-key",
            "OPENAI_BASE_URL": "http://fake.local/v1",
            "LLM_MODEL": "test-model",
        }):
            with patch("openai.AsyncOpenAI"):
                c = LLMClient(model="test-model", timeout=60.0)
        assert c.total_timeout == 180.0

    @pytest.mark.asyncio
    async def test_custom_total_timeout(self):
        """Custom total_timeout is respected."""
        with patch.dict(os.environ, {
            "OPENAI_API_KEY": "test-key",
            "OPENAI_BASE_URL": "http://fake.local/v1",
            "LLM_MODEL": "test-model",
        }):
            with patch("openai.AsyncOpenAI"):
                c = LLMClient(model="test-model", total_timeout=300.0)
        assert c.total_timeout == 300.0


# ============================================================
# Test Retry Behavior
# ============================================================

class TestRetryBehavior:
    """Test that transient errors trigger retries and permanent errors don't."""

    @pytest.fixture
    def client(self):
        """Create a client with fast backoff for testing."""
        with patch.dict(os.environ, {
            "OPENAI_API_KEY": "test-key",
            "OPENAI_BASE_URL": "http://fake.local/v1",
            "LLM_MODEL": "test-model",
        }):
            with patch("openai.AsyncOpenAI") as mock_openai:
                mock_client = AsyncMock()
                mock_openai.return_value = mock_client
                c = LLMClient(model="test-model", total_timeout=60.0)
                c._mock_client = mock_client
                # Speed up backoff for tests
                c._compute_backoff = lambda *args, **kwargs: 0.01
        return c

    @pytest.mark.asyncio
    async def test_retry_on_429(self, client):
        """429 error is retried and eventually succeeds."""
        call_count = 0

        async def rate_limited_then_success(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                e = Exception("rate limited")
                e.status_code = 429
                raise e
            # Return successful response
            resp = MagicMock()
            resp.usage = MagicMock(prompt_tokens=10, completion_tokens=20)
            resp.choices = [MagicMock(message=MagicMock(content="success"))]
            return resp

        client._mock_client.chat.completions.create = rate_limited_then_success

        result = await client.chat(system="sys", user="usr", retries=5)
        assert result == "success"
        assert call_count == 3
        assert client.total_retries == 2

    @pytest.mark.asyncio
    async def test_fast_fail_on_400(self, client):
        """400 error is NOT retried (permanent failure)."""
        async def bad_request(**kwargs):
            e = Exception("bad request - invalid model")
            e.status_code = 400
            raise e

        client._mock_client.chat.completions.create = bad_request

        with pytest.raises(Exception, match="bad request"):
            await client.chat(system="sys", user="usr", retries=5)
        assert client.total_permanent_failures == 1
        assert client.total_retries == 0

    @pytest.mark.asyncio
    async def test_fast_fail_on_401(self, client):
        """401 error is NOT retried (authentication failure)."""
        async def unauthorized(**kwargs):
            e = Exception("invalid api key")
            e.status_code = 401
            raise e

        client._mock_client.chat.completions.create = unauthorized

        with pytest.raises(Exception, match="invalid api key"):
            await client.chat(system="sys", user="usr", retries=5)
        assert client.total_permanent_failures == 1

    @pytest.mark.asyncio
    async def test_retry_on_500(self, client):
        """500 error is retried."""
        call_count = 0

        async def server_error_then_success(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                e = Exception("internal server error")
                e.status_code = 500
                raise e
            resp = MagicMock()
            resp.usage = MagicMock(prompt_tokens=5, completion_tokens=10)
            resp.choices = [MagicMock(message=MagicMock(content="recovered"))]
            return resp

        client._mock_client.chat.completions.create = server_error_then_success

        result = await client.chat(system="sys", user="usr", retries=3)
        assert result == "recovered"
        assert client.total_retries == 1

    @pytest.mark.asyncio
    async def test_exhausted_retries_raises(self, client):
        """All retries exhausted raises the last error."""
        async def always_fail(**kwargs):
            e = Exception("service unavailable")
            e.status_code = 503
            raise e

        client._mock_client.chat.completions.create = always_fail

        with pytest.raises(Exception, match="service unavailable"):
            await client.chat(system="sys", user="usr", retries=3)
        assert client.total_retries == 2  # retried twice before final raise


# ============================================================
# Test Logging (no print to stderr)
# ============================================================

class TestLogging:
    """Verify retry warnings go to logging, not print."""

    @pytest.fixture
    def client(self):
        with patch.dict(os.environ, {
            "OPENAI_API_KEY": "test-key",
            "OPENAI_BASE_URL": "http://fake.local/v1",
            "LLM_MODEL": "test-model",
        }):
            with patch("openai.AsyncOpenAI") as mock_openai:
                mock_client = AsyncMock()
                mock_openai.return_value = mock_client
                c = LLMClient(model="test-model", total_timeout=60.0)
                c._mock_client = mock_client
                c._compute_backoff = lambda *args, **kwargs: 0.01
        return c

    @pytest.mark.asyncio
    async def test_retry_logs_warning(self, client, caplog):
        """Retry produces a logging.WARNING message."""
        call_count = 0

        async def fail_once(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                e = Exception("temporary glitch")
                e.status_code = 503
                raise e
            resp = MagicMock()
            resp.usage = MagicMock(prompt_tokens=5, completion_tokens=10)
            resp.choices = [MagicMock(message=MagicMock(content="ok"))]
            return resp

        client._mock_client.chat.completions.create = fail_once

        with caplog.at_level(logging.WARNING, logger="llm.client"):
            result = await client.chat(system="sys", user="usr", retries=3)

        assert result == "ok"
        assert any("retry" in record.message.lower() for record in caplog.records)
        assert any("transient" in record.message.lower() for record in caplog.records)

    @pytest.mark.asyncio
    async def test_rate_limit_log_includes_retry_after(self, client, caplog):
        """Rate-limit retry log includes Retry-After value."""
        call_count = 0

        async def rate_limited_with_header(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                e = Exception("rate limited")
                e.status_code = 429
                response = MagicMock()
                response.headers = {"retry-after": "10"}
                e.response = response
                raise e
            resp = MagicMock()
            resp.usage = MagicMock(prompt_tokens=5, completion_tokens=10)
            resp.choices = [MagicMock(message=MagicMock(content="ok"))]
            return resp

        client._mock_client.chat.completions.create = rate_limited_with_header

        with caplog.at_level(logging.WARNING, logger="llm.client"):
            result = await client.chat(system="sys", user="usr", retries=3)

        assert result == "ok"
        assert any("Retry-After=10.0" in record.message for record in caplog.records)


# ============================================================
# Test with_model_override() shares stats counters
# ============================================================

class TestModelOverrideSharedStats:
    """Verify that with_model_override() clones share the same _stats dict."""

    @pytest.fixture
    def client(self):
        with patch.dict(os.environ, {
            "OPENAI_API_KEY": "test-key",
            "OPENAI_BASE_URL": "http://fake.local/v1",
            "LLM_MODEL": "test-model",
        }):
            with patch("openai.AsyncOpenAI") as mock_openai:
                mock_client = AsyncMock()
                mock_openai.return_value = mock_client
                c = LLMClient(model="test-model", total_timeout=60.0)
                c._mock_client = mock_client
        return c

    def test_same_model_returns_self(self, client):
        """Same model returns the exact same instance."""
        clone = client.with_model_override("test-model")
        assert clone is client

    def test_different_model_shares_stats(self, client):
        """Different model clone shares the same _stats dict."""
        clone = client.with_model_override("another-model")
        assert clone is not client
        assert clone.model == "another-model"
        # They share the same _stats dict object
        assert clone._stats is client._stats

    @pytest.mark.asyncio
    async def test_clone_increments_visible_to_parent(self, client):
        """Incrementing stats on clone is visible from parent."""
        clone = client.with_model_override("another-model")

        async def success(**kwargs):
            resp = MagicMock()
            resp.usage = MagicMock(prompt_tokens=100, completion_tokens=50)
            resp.choices = [MagicMock(message=MagicMock(content="hello"))]
            return resp

        clone._mock_client = client._mock_client
        clone.client = client.client  # share the actual OpenAI client
        client._mock_client.chat.completions.create = success

        result = await clone.chat(system="sys", user="usr", retries=2)
        assert result == "hello"

        # Stats incremented via clone are visible from parent
        assert client.total_calls == 1
        assert client.total_input_tokens == 100
        assert client.total_output_tokens == 50
        # And from clone itself
        assert clone.total_calls == 1
        assert clone.total_input_tokens == 100
        assert clone.total_output_tokens == 50
