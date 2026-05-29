"""
Plan Persistence — File-backed plan storage for recovery after context compression.

The agent creates plans (via the planning guideline). This module:
1. Saves plans to .workspace/.plans/
2. Tracks step completion within plans
3. Allows the agent to resume after interruption

Plans are lightweight JSON files, not full conversation replays.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


@dataclass
class PlanStep:
    """A single step in a plan."""
    index: int
    description: str
    tool: str = ""          # Primary tool to use
    depends_on: list = field(default_factory=list)  # Step indices this depends on
    status: str = "pending"  # pending | in_progress | completed | skipped | failed
    result_summary: str = ""  # Brief outcome after completion
    started_at: Optional[float] = None
    completed_at: Optional[float] = None


@dataclass
class Plan:
    """A persisted execution plan."""
    plan_id: str
    goal: str                # What this plan achieves
    steps: list[PlanStep] = field(default_factory=list)
    success_criteria: str = ""
    created_at: float = field(default_factory=time.time)
    status: str = "active"   # active | completed | abandoned
    current_step: int = 0    # Index of the current step

    def next_step(self) -> Optional[PlanStep]:
        """Get the next pending step."""
        for step in self.steps:
            if step.status == "pending":
                return step
        return None

    def advance(self, step_index: int, result_summary: str = "", success: bool = True):
        """Mark a step as completed and advance."""
        if 0 <= step_index < len(self.steps):
            step = self.steps[step_index]
            step.status = "completed" if success else "failed"
            step.result_summary = result_summary
            step.completed_at = time.time()
            self.current_step = step_index + 1

        # Check if plan is done
        if all(s.status in ("completed", "skipped") for s in self.steps):
            self.status = "completed"

    def progress_summary(self) -> str:
        """Compact progress string for context injection."""
        total = len(self.steps)
        done = sum(1 for s in self.steps if s.status == "completed")
        failed = sum(1 for s in self.steps if s.status == "failed")
        current = self.next_step()

        parts = [f"Plan '{self.goal}': {done}/{total} steps done"]
        if failed:
            parts.append(f", {failed} failed")
        if current:
            parts.append(f" | Next: step {current.index + 1} ({current.description})")
        elif self.status == "completed":
            parts.append(" | COMPLETED ✓")

        return "".join(parts)


class PlanStore:
    """Manages plan persistence on the filesystem."""

    def __init__(self, workspace: Path):
        self._dir = workspace / ".plans"
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass  # Graceful fallback; save_plan will fail individually

    def save_plan(self, plan: Plan) -> str:
        """Save a plan to disk. Returns the file path."""
        path = self._dir / f"{plan.plan_id}.json"
        data = asdict(plan)
        path.write_text(json.dumps(data, default=str, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        return str(path)

    def load_plan(self, plan_id: str) -> Optional[Plan]:
        """Load a plan from disk."""
        path = self._dir / f"{plan_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            steps = [PlanStep(**s) for s in data.pop("steps", [])]
            plan = Plan(**data, steps=steps)
            return plan
        except (json.JSONDecodeError, TypeError, KeyError):
            return None

    def get_active_plan(self) -> Optional[Plan]:
        """Get the most recent active plan."""
        plans = []
        for path in self._dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("status") == "active":
                    plans.append((data.get("created_at", 0), path.stem))
            except (json.JSONDecodeError, OSError):
                continue
        if not plans:
            return None
        # Return most recent active plan
        plans.sort(reverse=True)
        return self.load_plan(plans[0][1])

    def list_plans(self) -> list[dict]:
        """List all plans with summary info."""
        result = []
        for path in sorted(self._dir.glob("*.json"), reverse=True):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                total = len(data.get("steps", []))
                done = sum(1 for s in data.get("steps", []) if s.get("status") == "completed")
                result.append({
                    "plan_id": data.get("plan_id", path.stem),
                    "goal": data.get("goal", ""),
                    "status": data.get("status", "unknown"),
                    "progress": f"{done}/{total}",
                })
            except (json.JSONDecodeError, OSError):
                continue
        return result


def create_plan_from_text(plan_text: str, goal: str, plan_id: str = None) -> Plan:
    """Parse a plan from the agent's <plan>...</plan> text output.

    Expected format:
        1. [Step description] — tool: X — depends_on: none
        2. [Step description] — tool: Y — depends_on: step 1

    Returns a Plan object ready for persistence.
    """
    import re

    plan_id = plan_id or f"plan_{int(time.time())}"
    steps = []

    # Match numbered steps
    step_pattern = re.compile(
        r'(\d+)\.\s*\[?(.+?)\]?\s*(?:—|--)\s*tool:\s*(\w+)\s*(?:—|--)\s*depends_on:\s*(.+)',
        re.IGNORECASE
    )

    for line in plan_text.strip().split("\n"):
        line = line.strip()
        match = step_pattern.match(line)
        if match:
            idx = int(match.group(1)) - 1
            desc = match.group(2).strip()
            tool = match.group(3).strip()
            deps_str = match.group(4).strip()

            deps = []
            if deps_str.lower() not in ("none", "n/a", "-"):
                # Parse "step 1, step 2" or "1, 2"
                dep_nums = re.findall(r'\d+', deps_str)
                deps = [int(d) - 1 for d in dep_nums]

            steps.append(PlanStep(
                index=idx,
                description=desc,
                tool=tool,
                depends_on=deps,
            ))
        elif line and not line.startswith("Success criteria"):
            # Try simple numbered format: "1. Do something"
            simple_match = re.match(r'(\d+)\.\s+(.+)', line)
            if simple_match:
                idx = int(simple_match.group(1)) - 1
                desc = simple_match.group(2).strip()
                steps.append(PlanStep(
                    index=idx,
                    description=desc,
                ))

    # Extract success criteria
    success = ""
    for line in plan_text.strip().split("\n"):
        if line.strip().lower().startswith("success criteria"):
            success = line.split(":", 1)[-1].strip() if ":" in line else ""

    # Guard: if no steps were parsed, create a single placeholder step
    if not steps:
        steps = [PlanStep(index=0, description="(unparsed plan — review and restructure)")]

    return Plan(
        plan_id=plan_id,
        goal=goal,
        steps=steps,
        success_criteria=success,
    )
