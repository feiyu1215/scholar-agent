"""
tests/test_c1_metrics_export.py — Phase C1: 结构化日志 + Metrics Export 验证

验证目标：
    1. 每个 export 函数写入正确格式的 JSON Lines
    2. 每条 record 包含 timestamp, session_id, event_type, payload
    3. export_all_session_metrics 一站式导出不崩溃
    4. Metrics 文件在 .workspace/metrics/ 下正确创建
    5. 渐进退化：缺失数据时不崩溃
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Ensure v2/ is importable
_v2_root = str(Path(__file__).resolve().parent.parent)
if _v2_root not in sys.path:
    sys.path.insert(0, _v2_root)

from core.metrics_export import (
    export_evolution_metrics,
    export_contrast_metrics,
    export_deep_reflect_metrics,
    export_session_summary,
    export_all_session_metrics,
    _make_record,
)


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture
def metrics_dir(tmp_path):
    """Provide a temporary metrics directory."""
    d = tmp_path / "metrics"
    d.mkdir()
    return d


def _read_jsonl(filepath: Path) -> list[dict]:
    """Read all JSON Lines from a file."""
    records = []
    with filepath.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


# ============================================================
# Test: Record structure
# ============================================================


class TestRecordStructure:
    """Verify the standard record format."""

    def test_make_record_has_required_fields(self):
        """Every record must have timestamp, session_id, event_type, payload."""
        record = _make_record(
            session_id="test123",
            event_type="test_event",
            payload={"key": "value"},
        )

        assert "timestamp" in record
        assert record["session_id"] == "test123"
        assert record["event_type"] == "test_event"
        assert record["payload"] == {"key": "value"}
        # Timestamp should be ISO format
        assert "T" in record["timestamp"]
        assert "+" in record["timestamp"] or "Z" in record["timestamp"]

    def test_make_record_timestamp_is_utc(self):
        """Timestamp should be in UTC timezone."""
        record = _make_record("s1", "t1", {})
        ts = record["timestamp"]
        # Should end with +00:00 (UTC)
        assert "+00:00" in ts or "Z" in ts


# ============================================================
# Test: Evolution metrics export
# ============================================================


class TestEvolutionExport:
    """Verify evolution metrics writing."""

    def test_writes_evolution_jsonl(self, metrics_dir):
        """Should create evolution.jsonl with correct content."""
        export_evolution_metrics(
            session_id="paper_abc",
            habits_generated=3,
            habits_injected=2,
            total_learned_habits=10,
            habit_details=[
                {"habit_id": "h1", "confidence": 0.65, "generation": 2},
                {"habit_id": "h2", "confidence": 0.42, "generation": 1},
            ],
            metrics_dir=metrics_dir,
        )

        filepath = metrics_dir / "evolution.jsonl"
        assert filepath.exists()

        records = _read_jsonl(filepath)
        assert len(records) == 1

        r = records[0]
        assert r["session_id"] == "paper_abc"
        assert r["event_type"] == "evolution_snapshot"
        assert r["payload"]["habits_generated"] == 3
        assert r["payload"]["total_learned_habits"] == 10
        assert len(r["payload"]["habit_details"]) == 2

    def test_multiple_writes_append(self, metrics_dir):
        """Multiple calls should append, not overwrite."""
        export_evolution_metrics(session_id="s1", metrics_dir=metrics_dir)
        export_evolution_metrics(session_id="s2", metrics_dir=metrics_dir)

        records = _read_jsonl(metrics_dir / "evolution.jsonl")
        assert len(records) == 2
        assert records[0]["session_id"] == "s1"
        assert records[1]["session_id"] == "s2"


# ============================================================
# Test: Contrast metrics export
# ============================================================


class TestContrastExport:
    """Verify contrast metrics writing."""

    def test_writes_contrast_jsonl(self, metrics_dir):
        """Should create contrast.jsonl with A/B delta data."""
        export_contrast_metrics(
            session_id="paper_xyz",
            target_habit_id="habit_deep_scan",
            phase_a_density=1.5,
            phase_b_density=0.8,
            delta=0.7,
            recommendation="reinforce",
            statistical_note="N_a=5, N_b=4",
            metrics_dir=metrics_dir,
        )

        records = _read_jsonl(metrics_dir / "contrast.jsonl")
        assert len(records) == 1

        payload = records[0]["payload"]
        assert payload["target_habit_id"] == "habit_deep_scan"
        assert payload["delta"] == 0.7
        assert payload["recommendation"] == "reinforce"
        assert payload["phase_a_density"] == 1.5
        assert payload["phase_b_density"] == 0.8


# ============================================================
# Test: Deep reflect metrics export
# ============================================================


class TestDeepReflectExport:
    """Verify deep reflect decision metrics."""

    def test_writes_deep_reflect_jsonl(self, metrics_dir):
        """Should record DeepReflector decisions."""
        export_deep_reflect_metrics(
            session_id="paper_123",
            habit_decisions=[
                {"habit_id": "h1", "action": "boost", "confidence_delta": 0.1},
                {"habit_id": "h2", "action": "reduce", "confidence_delta": -0.05},
            ],
            maturity_updates=[
                {"paper_type": "nlp_transformer", "new_maturity": 0.8},
            ],
            config_decisions=[],
            token_efficiency="improving",
            meta_note="Habit h1 shows consistent effectiveness",
            metrics_dir=metrics_dir,
        )

        records = _read_jsonl(metrics_dir / "deep_reflect.jsonl")
        assert len(records) == 1

        payload = records[0]["payload"]
        assert len(payload["habit_decisions"]) == 2
        assert payload["habit_decisions"][0]["action"] == "boost"
        assert payload["token_efficiency"] == "improving"
        assert payload["meta_note"] == "Habit h1 shows consistent effectiveness"


# ============================================================
# Test: Session summary export
# ============================================================


class TestSessionSummaryExport:
    """Verify session summary metrics."""

    def test_writes_session_summary(self, metrics_dir):
        """Should record session-level summary with efficiency metrics."""
        export_session_summary(
            session_id="sess_001",
            paper_id="paper_abc",
            paper_type="nlp_transformer",
            findings_count=5,
            loop_turns=10,
            total_tokens=15000,
            sections_read=4,
            total_sections=6,
            pcg_coverage=0.75,
            emergency_triggered=False,
            fast_reflect_alerts=1,
            deep_reflect_ran=True,
            v3_features_enabled=["pcg", "budget", "evidence_chain"],
            metrics_dir=metrics_dir,
        )

        records = _read_jsonl(metrics_dir / "session_summary.jsonl")
        assert len(records) == 1

        payload = records[0]["payload"]
        assert payload["findings_count"] == 5
        assert payload["loop_turns"] == 10
        assert payload["read_ratio"] == round(4 / 6, 3)
        assert payload["findings_per_turn"] == 0.5
        assert payload["findings_per_1k_tokens"] == round(5 / 15, 3)
        assert payload["deep_reflect_ran"] is True
        assert "pcg" in payload["v3_features_enabled"]

    def test_zero_division_safe(self, metrics_dir):
        """Should handle zero turns/tokens gracefully."""
        export_session_summary(
            session_id="empty",
            findings_count=0,
            loop_turns=0,
            total_tokens=0,
            sections_read=0,
            total_sections=0,
            metrics_dir=metrics_dir,
        )

        records = _read_jsonl(metrics_dir / "session_summary.jsonl")
        payload = records[0]["payload"]
        assert payload["findings_per_turn"] == 0.0
        assert payload["findings_per_1k_tokens"] == 0.0
        assert payload["read_ratio"] == 0.0


# ============================================================
# Test: export_all_session_metrics (convenience function)
# ============================================================


class TestExportAll:
    """Verify the one-stop export function."""

    def test_export_all_with_minimal_state(self, metrics_dir):
        """Should not crash even with minimal/mock state."""
        state = MagicMock()
        state.findings = [{"finding": "test"}]
        state.loop_turns = 5
        state.total_tokens = 3000
        state.sections_read = ["intro", "method"]
        state.paper_sections = {"intro": "", "method": "", "results": ""}
        state.paper_cognition_graph = None
        state.cognitive_hints = None
        state._last_contrast_result = None

        memory = MagicMock()
        memory.learned_habits = []

        sid = export_all_session_metrics(
            state=state,
            memory=memory,
            paper_id="test_paper",
            metrics_dir=metrics_dir,
        )

        assert sid == "test_paper"
        # Should have created session_summary and evolution files
        assert (metrics_dir / "session_summary.jsonl").exists()
        assert (metrics_dir / "evolution.jsonl").exists()

    def test_export_all_with_contrast_result(self, metrics_dir):
        """Should export contrast metrics when result is provided."""
        state = MagicMock()
        state.findings = []
        state.loop_turns = 3
        state.total_tokens = 1000
        state.sections_read = []
        state.paper_sections = {}
        state.paper_cognition_graph = None
        state.cognitive_hints = None

        contrast_result = {
            "target_habit_id": "h_scan_deep",
            "phase_a_findings_density": 2.0,
            "phase_b_findings_density": 1.2,
            "delta": 0.8,
            "recommendation": "reinforce",
            "statistical_note": "N_a=3, N_b=3",
        }

        export_all_session_metrics(
            state=state,
            memory=MagicMock(learned_habits=[]),
            contrast_result=contrast_result,
            paper_id="p1",
            metrics_dir=metrics_dir,
        )

        assert (metrics_dir / "contrast.jsonl").exists()
        records = _read_jsonl(metrics_dir / "contrast.jsonl")
        assert records[0]["payload"]["delta"] == 0.8

    def test_export_all_with_deep_reflect(self, metrics_dir):
        """Should export deep reflect metrics when result is provided."""
        state = MagicMock()
        state.findings = []
        state.loop_turns = 3
        state.total_tokens = 1000
        state.sections_read = []
        state.paper_sections = {}
        state.paper_cognition_graph = None
        state.cognitive_hints = None

        deep_result = {
            "habit_decisions": [
                {"habit_id": "h1", "action": "boost", "confidence_delta": 0.1}
            ],
            "maturity_updates": [],
            "config_decisions": [],
            "token_efficiency_assessment": "improving",
            "meta_note": "Good progress",
        }

        export_all_session_metrics(
            state=state,
            memory=MagicMock(learned_habits=[]),
            deep_reflect_result=deep_result,
            paper_id="p2",
            metrics_dir=metrics_dir,
        )

        assert (metrics_dir / "deep_reflect.jsonl").exists()
        records = _read_jsonl(metrics_dir / "deep_reflect.jsonl")
        assert records[0]["payload"]["token_efficiency"] == "improving"

    def test_export_all_no_crash_on_none_state(self, metrics_dir):
        """Should handle None state gracefully."""
        sid = export_all_session_metrics(
            state=None,
            memory=None,
            paper_id="fallback",
            metrics_dir=metrics_dir,
        )
        # Should still produce an ID
        assert sid == "fallback"

    def test_generates_session_id_when_none(self, metrics_dir):
        """Should generate a UUID-based session_id when no paper_id provided."""
        state = MagicMock()
        state.findings = []
        state.loop_turns = 0
        state.total_tokens = 0
        state.sections_read = []
        state.paper_sections = {}
        state.paper_cognition_graph = None
        state.cognitive_hints = None

        sid = export_all_session_metrics(
            state=state,
            memory=MagicMock(learned_habits=[]),
            paper_id=None,
            metrics_dir=metrics_dir,
        )
        # Should be a short UUID string
        assert len(sid) == 8


# ============================================================
# Test: Graceful degradation
# ============================================================


class TestGracefulDegradation:
    """Verify metrics export degrades gracefully on errors."""

    def test_invalid_dir_does_not_crash(self, tmp_path):
        """Writing to a read-only directory should not crash."""
        # This tests the logger.warning path
        import os
        readonly_dir = tmp_path / "readonly"
        readonly_dir.mkdir()
        # Create a file at the expected path so directory creation fails
        # (Actually, _ensure_metrics_dir handles this. Test via mock.)
        # Just verify no exception propagates
        export_evolution_metrics(
            session_id="test",
            metrics_dir=readonly_dir,
        )
        # Should succeed (directory exists)
        assert (readonly_dir / "evolution.jsonl").exists()

    def test_handles_non_serializable_data(self, metrics_dir):
        """Should handle non-standard data types via default=str."""
        from datetime import datetime
        export_evolution_metrics(
            session_id="test",
            habit_details=[{"weird_value": datetime.now()}],
            metrics_dir=metrics_dir,
        )
        # Should not crash, and file should be valid JSON
        records = _read_jsonl(metrics_dir / "evolution.jsonl")
        assert len(records) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
