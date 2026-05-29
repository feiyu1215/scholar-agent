"""
Checkpoint + Resume mechanism for long-running pipelines.

Saves pipeline state to .workspace/checkpoints/<run_id>.json after each step.
On success the checkpoint is cleared; on failure it stays for resume.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class StepRecord:
    """Record of a single completed pipeline step."""
    step_index: int
    name: str
    description: str = ""
    timestamp: str = ""
    duration: float = 0.0
    llm_calls: int = 0
    tokens_used: int = 0


@dataclass
class CheckpointState:
    """Full state of a pipeline checkpoint."""
    run_id: str
    pipeline_name: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    completed_step: int = -1
    total_steps_estimate: int = 0
    steps: List[StepRecord] = field(default_factory=list)
    data: Dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""
    status: str = "in_progress"  # in_progress | completed | failed


class Checkpoint:
    """Manages save/load of pipeline state for resumable execution."""

    def __init__(self, pipeline_name: str, workspace_root: str = ".", **metadata):
        self.pipeline_name = pipeline_name
        self.metadata = metadata
        self.workspace_root = Path(workspace_root)
        self._checkpoint_dir = self.workspace_root / ".workspace" / "checkpoints"
        self._checkpoint_dir.mkdir(parents=True, exist_ok=True)

        # Derive a stable run_id from pipeline_name + sorted metadata
        id_source = pipeline_name + ":" + json.dumps(metadata, sort_keys=True)
        self.run_id = hashlib.sha256(id_source.encode()).hexdigest()[:12]
        self._filepath = self._checkpoint_dir / f"{self.run_id}.json"

        self._state: Optional[CheckpointState] = None
        self._step_start_time: Optional[float] = None

    def has_checkpoint(self) -> bool:
        """Check if a resumable checkpoint exists for this run."""
        return self._filepath.exists()

    def start(self, total_steps_estimate: int = 0) -> CheckpointState:
        """Create a new checkpoint or load an existing one for resume."""
        if self.has_checkpoint():
            self._state = self._load()
            # Update estimate if provided
            if total_steps_estimate > 0:
                self._state.total_steps_estimate = total_steps_estimate
            return self._state

        now = datetime.now(timezone.utc).isoformat()
        self._state = CheckpointState(
            run_id=self.run_id,
            pipeline_name=self.pipeline_name,
            metadata=self.metadata,
            completed_step=-1,
            total_steps_estimate=total_steps_estimate,
            steps=[],
            data={},
            created_at=now,
            updated_at=now,
            status="in_progress",
        )
        self._save()
        return self._state

    def begin_step(self, step_index: int, name: str) -> None:
        """Mark the beginning of a step (starts timer)."""
        self._step_start_time = time.time()

    def complete_step(
        self,
        step_index: int,
        name: str,
        description: str = "",
        data_update: Optional[Dict[str, Any]] = None,
        llm_calls: int = 0,
        tokens_used: int = 0,
    ) -> None:
        """Record a completed step and persist state."""
        if self._state is None:
            raise RuntimeError("Checkpoint not started. Call start() first.")

        duration = 0.0
        if self._step_start_time is not None:
            duration = time.time() - self._step_start_time
            self._step_start_time = None

        record = StepRecord(
            step_index=step_index,
            name=name,
            description=description,
            timestamp=datetime.now(timezone.utc).isoformat(),
            duration=round(duration, 3),
            llm_calls=llm_calls,
            tokens_used=tokens_used,
        )
        self._state.steps.append(record)
        self._state.completed_step = step_index

        if data_update:
            self._state.data.update(data_update)

        self._state.updated_at = datetime.now(timezone.utc).isoformat()
        self._save()

    def get_data(self, key: str, default: Any = None) -> Any:
        """Get a value from the checkpoint data store."""
        if self._state is None:
            return default
        return self._state.data.get(key, default)

    def set_data(self, key: str, value: Any) -> None:
        """Set a value in the checkpoint data store and persist."""
        if self._state is None:
            raise RuntimeError("Checkpoint not started. Call start() first.")
        self._state.data[key] = value
        self._state.updated_at = datetime.now(timezone.utc).isoformat()
        self._save()

    def mark_failed(self, error: str = "") -> None:
        """Mark the checkpoint as failed (keeps file for resume)."""
        if self._state is None:
            raise RuntimeError("Checkpoint not started. Call start() first.")
        self._state.status = "failed"
        if error:
            self._state.data["_last_error"] = error
        self._state.updated_at = datetime.now(timezone.utc).isoformat()
        self._save()

    def clear(self) -> None:
        """Delete the checkpoint file (call on success)."""
        if self._filepath.exists():
            self._filepath.unlink()
        self._state = None

    def summary(self) -> str:
        """Return a human-readable progress summary."""
        if self._state is None:
            return f"[{self.pipeline_name}] No active checkpoint."

        s = self._state
        total = s.total_steps_estimate or "?"
        completed = s.completed_step + 1
        pct = ""
        if isinstance(total, int) and total > 0:
            pct = f" ({completed * 100 // total}%)"

        lines = [
            f"Pipeline: {s.pipeline_name} [{s.status}]",
            f"Run ID: {s.run_id}",
            f"Progress: {completed}/{total} steps{pct}",
        ]

        if s.steps:
            last = s.steps[-1]
            lines.append(f"Last step: [{last.step_index}] {last.name} — {last.description}")

        total_tokens = sum(step.tokens_used for step in s.steps)
        total_llm = sum(step.llm_calls for step in s.steps)
        if total_tokens or total_llm:
            lines.append(f"Total: {total_llm} LLM calls, {total_tokens} tokens")

        return "\n".join(lines)

    # -- Private helpers --

    def _save(self) -> None:
        """Persist state to JSON file."""
        if self._state is None:
            return
        data = asdict(self._state)
        self._filepath.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    def _load(self) -> CheckpointState:
        """Load state from JSON file."""
        raw = json.loads(self._filepath.read_text())
        steps = [StepRecord(**s) for s in raw.pop("steps", [])]
        state = CheckpointState(**raw)
        state.steps = steps
        return state


def list_checkpoints(workspace_root: str = ".") -> List[Dict[str, Any]]:
    """List all in-progress/failed (resumable) checkpoints.
    
    Returns a list of dicts with run_id, pipeline_name, status,
    completed_step, total_steps_estimate, created_at, updated_at.
    """
    checkpoint_dir = Path(workspace_root) / ".workspace" / "checkpoints"
    if not checkpoint_dir.exists():
        return []

    results = []
    for fp in sorted(checkpoint_dir.glob("*.json")):
        try:
            raw = json.loads(fp.read_text())
            # Only include resumable checkpoints (in_progress or failed)
            if raw.get("status") in ("in_progress", "failed"):
                results.append({
                    "run_id": raw.get("run_id", ""),
                    "pipeline_name": raw.get("pipeline_name", ""),
                    "status": raw.get("status", ""),
                    "completed_step": raw.get("completed_step", -1),
                    "total_steps_estimate": raw.get("total_steps_estimate", 0),
                    "created_at": raw.get("created_at", ""),
                    "updated_at": raw.get("updated_at", ""),
                    "metadata": raw.get("metadata", {}),
                })
        except (json.JSONDecodeError, KeyError):
            continue

    return results
