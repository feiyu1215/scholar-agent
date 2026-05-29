"""
Tests for B3 Enhancement: Evidence-based adaptive parameter evolution.

Covers:
- AdaptiveParam bounded adjustment logic
- AdaptiveConfig.adjust_from_evidence() with ±20% clamp
- JSON persistence (persist + load_persisted)
- DeepReflector integration (config_decisions in apply_decisions_v3)
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.adaptive_config import (
    AdaptiveParam,
    AdaptiveConfig,
    EVIDENCE_MIN_FOR_ADJUST,
    MAX_ADJUST_RATIO,
)


# ============================================================
# AdaptiveParam Tests
# ============================================================


class TestAdaptiveParam:
    """Unit tests for AdaptiveParam bounded adjustment."""

    def test_propose_adjustment_increase(self):
        """Positive direction proposes +20% within bounds."""
        param = AdaptiveParam(
            name="temperature", current_value=0.3,
            min_bound=0.05, max_bound=0.7,
        )
        new_val = param.propose_adjustment(direction=1.0, evidence=3)
        assert new_val is not None
        assert abs(new_val - 0.36) < 1e-6  # 0.3 + 0.3*0.2 = 0.36

    def test_propose_adjustment_decrease(self):
        """Negative direction proposes -20% within bounds."""
        param = AdaptiveParam(
            name="temperature", current_value=0.3,
            min_bound=0.05, max_bound=0.7,
        )
        new_val = param.propose_adjustment(direction=-1.0, evidence=3)
        assert new_val is not None
        assert abs(new_val - 0.24) < 1e-6  # 0.3 - 0.3*0.2 = 0.24

    def test_propose_blocked_insufficient_evidence(self):
        """Returns None when evidence < threshold."""
        param = AdaptiveParam(
            name="temperature", current_value=0.3,
            min_bound=0.05, max_bound=0.7,
        )
        result = param.propose_adjustment(direction=1.0, evidence=2)
        assert result is None

    def test_propose_clamped_to_max(self):
        """Adjustment clamped to max_bound."""
        param = AdaptiveParam(
            name="temperature", current_value=0.65,
            min_bound=0.05, max_bound=0.7,
        )
        # 0.65 + 0.65*0.2 = 0.78 → clamped to 0.7
        new_val = param.propose_adjustment(direction=1.0, evidence=5)
        assert new_val is not None
        assert abs(new_val - 0.7) < 1e-6

    def test_propose_clamped_to_min(self):
        """Adjustment clamped to min_bound."""
        param = AdaptiveParam(
            name="temperature", current_value=0.06,
            min_bound=0.05, max_bound=0.7,
        )
        # 0.06 - 0.06*0.2 = 0.048 → clamped to 0.05
        new_val = param.propose_adjustment(direction=-1.0, evidence=3)
        assert new_val is not None
        assert abs(new_val - 0.05) < 1e-6

    def test_propose_returns_none_when_at_bound(self):
        """Returns None when already at bound and trying to go further."""
        param = AdaptiveParam(
            name="temperature", current_value=0.7,
            min_bound=0.05, max_bound=0.7,
        )
        # Already at max, direction +1 → no change → None
        result = param.propose_adjustment(direction=1.0, evidence=5)
        assert result is None

    def test_apply_adjustment(self):
        """apply_adjustment updates value and accumulates evidence."""
        param = AdaptiveParam(
            name="test", current_value=1.0,
            min_bound=0.0, max_bound=5.0,
        )
        param.apply_adjustment(1.2, evidence=3)
        assert param.current_value == 1.2
        assert param.evidence_count == 3

        param.apply_adjustment(1.5, evidence=4)
        assert param.current_value == 1.5
        assert param.evidence_count == 7  # 3 + 4


# ============================================================
# AdaptiveConfig.adjust_from_evidence Tests
# ============================================================


class TestAdjustFromEvidence:
    """Integration tests for AdaptiveConfig.adjust_from_evidence."""

    def test_adjust_temperature_up(self):
        """Successfully adjust temperature upward."""
        config = AdaptiveConfig()
        original = config.temperature
        result = config.adjust_from_evidence("temperature", direction=1.0, evidence_count=5)
        assert result is True
        expected = original * (1 + MAX_ADJUST_RATIO)
        assert abs(config.temperature - expected) < 1e-6

    def test_adjust_max_nudges_down(self):
        """Adjust max_nudges downward (integer)."""
        config = AdaptiveConfig(max_nudges=3)
        result = config.adjust_from_evidence("max_nudges", direction=-1.0, evidence_count=3)
        assert result is True
        # 3.0 - 3.0*0.2 = 2.4 → round → 2
        assert config.max_nudges == 2

    def test_adjust_blocked_unknown_param(self):
        """Returns False for unknown parameter name."""
        config = AdaptiveConfig()
        result = config.adjust_from_evidence("nonexistent_param", direction=1.0, evidence_count=5)
        assert result is False

    def test_adjust_blocked_insufficient_evidence(self):
        """Returns False when evidence < threshold (3)."""
        config = AdaptiveConfig()
        original = config.temperature
        result = config.adjust_from_evidence("temperature", direction=1.0, evidence_count=2)
        assert result is False
        assert config.temperature == original  # unchanged

    def test_adjust_logs_adaptation_entry(self):
        """Adjustment is recorded in adaptation_log."""
        config = AdaptiveConfig()
        config.adjust_from_evidence("temperature", direction=1.0, evidence_count=4)
        assert len(config.adaptation_log) == 1
        entry = config.adaptation_log[0]
        assert entry.param == "temperature"
        assert entry.turn == -1  # evolution-driven marker

    def test_frozen_config_still_allows_adjust(self):
        """adjust_from_evidence works even on frozen config (evolution is separate from tick)."""
        config = AdaptiveConfig.frozen_default()
        original = config.temperature
        result = config.adjust_from_evidence("temperature", direction=1.0, evidence_count=5)
        # frozen only blocks tick(), not evolution
        assert result is True
        assert config.temperature != original


# ============================================================
# Persistence Tests
# ============================================================


class TestPersistence:
    """Tests for persist() and load_persisted()."""

    def test_persist_creates_json(self):
        """persist() writes JSON file with adjusted params only."""
        config = AdaptiveConfig()
        config.adjust_from_evidence("temperature", direction=1.0, evidence_count=3)

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "adaptive_state.json"
            config.persist(path)

            assert path.exists()
            data = json.loads(path.read_text())
            assert "temperature" in data
            assert data["temperature"]["evidence_count"] == 3
            # Unadjusted params should NOT be in the file
            assert "max_nudges" not in data

    def test_persist_nothing_when_no_adjustments(self):
        """persist() does NOT create file if no params have been adjusted."""
        config = AdaptiveConfig()

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "adaptive_state.json"
            config.persist(path)
            assert not path.exists()

    def test_load_persisted_restores_values(self):
        """load_persisted() restores saved values and syncs fields."""
        config = AdaptiveConfig()
        config.adjust_from_evidence("temperature", direction=1.0, evidence_count=4)
        adjusted_temp = config.temperature

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "adaptive_state.json"
            config.persist(path)

            # Fresh config should have default temperature
            fresh_config = AdaptiveConfig()
            assert fresh_config.temperature != adjusted_temp

            # Load persisted → should restore
            loaded = fresh_config.load_persisted(path)
            assert loaded == 1
            assert abs(fresh_config.temperature - adjusted_temp) < 1e-6

    def test_load_persisted_missing_file(self):
        """load_persisted() returns 0 when file doesn't exist."""
        config = AdaptiveConfig()
        loaded = config.load_persisted(Path("/nonexistent/path.json"))
        assert loaded == 0

    def test_load_persisted_corrupted_json(self):
        """load_persisted() handles corrupted JSON gracefully."""
        config = AdaptiveConfig()

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "broken.json"
            path.write_text("not valid json {{{{")

            loaded = config.load_persisted(path)
            assert loaded == 0

    def test_load_persisted_respects_bounds(self):
        """load_persisted() clamps values within bounds even if file is stale."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "stale.json"
            # Write a value that exceeds current bounds
            stale_data = {
                "temperature": {"current_value": 99.0, "evidence_count": 10}
            }
            path.write_text(json.dumps(stale_data))

            config = AdaptiveConfig()
            config.load_persisted(path)
            # Should be clamped to max_bound (0.7)
            assert config.temperature == 0.7


# ============================================================
# DeepReflector Integration Tests
# ============================================================


class TestDeepReflectorConfigIntegration:
    """Tests for config_decisions in apply_decisions_v3."""

    def _make_memory_store(self):
        """Create minimal mock memory store."""
        memory = MagicMock()
        memory.state.session_experiences_v3 = [{"id": "s1"}] * 10
        memory.state.contrast_results = []
        memory.state.fast_reflect_alerts = []
        memory.state._last_deep_reflect_count = 0
        memory.persist_evolution_record = MagicMock()
        return memory

    def test_config_decisions_applied(self):
        """config_decisions adjusts adaptive_config via apply_decisions_v3."""
        from core.meta_reflect import DeepReflector

        deep = DeepReflector(llm_call_fn=AsyncMock())
        memory = self._make_memory_store()
        config = AdaptiveConfig()
        original_temp = config.temperature

        result = {
            "habit_decisions": [],
            "config_decisions": [
                {"param": "temperature", "direction": 1.0, "evidence_count": 5}
            ],
        }

        report = deep.apply_decisions_v3(
            result, memory, learned_habits=[], adaptive_config=config
        )

        assert report["config_adjusted"] == 1
        assert config.temperature > original_temp

    def test_config_decisions_blocked_insufficient_evidence(self):
        """config_decisions with low evidence are blocked."""
        from core.meta_reflect import DeepReflector

        deep = DeepReflector(llm_call_fn=AsyncMock())
        memory = self._make_memory_store()
        config = AdaptiveConfig()
        original_temp = config.temperature

        result = {
            "habit_decisions": [],
            "config_decisions": [
                {"param": "temperature", "direction": 1.0, "evidence_count": 1}
            ],
        }

        report = deep.apply_decisions_v3(
            result, memory, learned_habits=[], adaptive_config=config
        )

        assert report["config_adjusted"] == 0
        assert config.temperature == original_temp

    def test_config_decisions_skipped_when_no_adaptive_config(self):
        """No crash when adaptive_config is None (backward compat)."""
        from core.meta_reflect import DeepReflector

        deep = DeepReflector(llm_call_fn=AsyncMock())
        memory = self._make_memory_store()

        result = {
            "habit_decisions": [],
            "config_decisions": [
                {"param": "temperature", "direction": 1.0, "evidence_count": 5}
            ],
        }

        # No adaptive_config → should not crash
        report = deep.apply_decisions_v3(
            result, memory, learned_habits=[], adaptive_config=None
        )

        assert report["config_adjusted"] == 0
        assert report["evolution_recorded"] is True

    def test_config_decisions_max_3(self):
        """At most 3 config_decisions applied per reflect cycle."""
        from core.meta_reflect import DeepReflector

        deep = DeepReflector(llm_call_fn=AsyncMock())
        memory = self._make_memory_store()
        config = AdaptiveConfig()

        result = {
            "habit_decisions": [],
            "config_decisions": [
                {"param": "temperature", "direction": 1.0, "evidence_count": 5},
                {"param": "max_nudges", "direction": 1.0, "evidence_count": 4},
                {"param": "keep_recent", "direction": 1.0, "evidence_count": 3},
                {"param": "signal_max_per_turn", "direction": 1.0, "evidence_count": 6},
            ],
        }

        report = deep.apply_decisions_v3(
            result, memory, learned_habits=[], adaptive_config=config
        )

        # Only first 3 should be applied
        assert report["config_adjusted"] == 3

    def test_config_decisions_persisted_in_evolution_record(self):
        """config_decisions are included in the persisted evolution record."""
        from core.meta_reflect import DeepReflector

        deep = DeepReflector(llm_call_fn=AsyncMock())
        memory = self._make_memory_store()
        config = AdaptiveConfig()

        config_decisions = [
            {"param": "temperature", "direction": 1.0, "evidence_count": 5}
        ]
        result = {
            "habit_decisions": [],
            "config_decisions": config_decisions,
        }

        deep.apply_decisions_v3(
            result, memory, learned_habits=[], adaptive_config=config
        )

        # Check the persisted evolution record includes config_decisions
        call_args = memory.persist_evolution_record.call_args[0][0]
        assert call_args["config_decisions"] == config_decisions


# ============================================================
# Constants Tests
# ============================================================


class TestConstants:
    """Verify B3 constants are properly set."""

    def test_evidence_threshold(self):
        assert EVIDENCE_MIN_FOR_ADJUST == 3

    def test_max_adjust_ratio(self):
        assert MAX_ADJUST_RATIO == 0.20


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
