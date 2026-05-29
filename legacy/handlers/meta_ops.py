"""handlers/meta_ops.py — Agent self-navigation, goal/plan management, learning, and utility handlers."""

import json
from pathlib import Path

from core.state import WORKSPACE, WORKDIR


GUIDELINES_DIR = Path(__file__).parent.parent / "guidelines"


def handle_read_agent_guidelines(topic: str) -> str:
    """Load a guideline file by topic name."""
    guideline_file = GUIDELINES_DIR / f"{topic}.md"
    if not guideline_file.exists():
        available = [f.stem for f in GUIDELINES_DIR.glob("*.md")] if GUIDELINES_DIR.exists() else []
        return f"Error: Guideline '{topic}' not found. Available: {available}"
    content = guideline_file.read_text(encoding="utf-8")
    return content


def handle_set_goal(description: str) -> str:
    """Register a new session goal."""
    from core.state import goal_tracker
    if not goal_tracker:
        return json.dumps({"error": "GoalTracker not initialized"})
    goal = goal_tracker.add_goal(description)
    return json.dumps({
        "goal_id": goal.id,
        "description": goal.description,
        "phase": goal_tracker.phase.value,
        "message": f"Goal '{goal.id}' registered. Phase tracking active.",
    })


def handle_complete_goal(goal_id: str, note: str = "") -> str:
    """Mark a goal as completed."""
    from core.state import goal_tracker
    if not goal_tracker:
        return json.dumps({"error": "GoalTracker not initialized"})
    all_done = goal_tracker.complete_goal(goal_id, note)
    return json.dumps({
        "goal_id": goal_id,
        "all_goals_done": all_done,
        "phase": goal_tracker.phase.value,
        "message": f"Goal '{goal_id}' completed." + (" All goals done!" if all_done else ""),
    })


def handle_save_plan(goal: str, plan_text: str) -> str:
    """Parse and persist a plan."""
    from core.state import plan_store
    from utils.plan_persistence import create_plan_from_text
    if not plan_store:
        return json.dumps({"error": "PlanStore not initialized"})
    plan = create_plan_from_text(plan_text, goal)
    path = plan_store.save_plan(plan)
    return json.dumps({
        "plan_id": plan.plan_id,
        "goal": plan.goal,
        "steps_count": len(plan.steps),
        "file": path,
        "message": f"Plan saved with {len(plan.steps)} steps. Use advance_plan after each step.",
    })


def handle_load_plan(plan_id: str = None) -> str:
    """Load a plan from disk."""
    from core.state import plan_store
    if not plan_store:
        return json.dumps({"error": "PlanStore not initialized"})
    if plan_id:
        plan = plan_store.load_plan(plan_id)
    else:
        plan = plan_store.get_active_plan()

    if not plan:
        plans = plan_store.list_plans()
        return json.dumps({
            "error": "No active plan found.",
            "available_plans": plans,
        })

    return json.dumps({
        "plan_id": plan.plan_id,
        "goal": plan.goal,
        "status": plan.status,
        "progress": plan.progress_summary(),
        "steps": [
            {
                "index": s.index,
                "description": s.description,
                "tool": s.tool,
                "status": s.status,
                "result": s.result_summary,
            }
            for s in plan.steps
        ],
        "next_step": (lambda ns: {
            "index": ns.index,
            "description": ns.description,
            "tool": ns.tool,
        } if ns else None)(plan.next_step()),
    }, ensure_ascii=False)


def handle_advance_plan(plan_id: str, step_index: int,
                        result_summary: str = "", success: bool = True) -> str:
    """Advance a plan step."""
    from core.state import plan_store
    if not plan_store:
        return json.dumps({"error": "PlanStore not initialized"})
    plan = plan_store.load_plan(plan_id)
    if not plan:
        return json.dumps({"error": f"Plan '{plan_id}' not found."})

    plan.advance(step_index, result_summary, success)
    plan_store.save_plan(plan)

    next_step = plan.next_step()
    return json.dumps({
        "plan_id": plan.plan_id,
        "step_completed": step_index,
        "plan_status": plan.status,
        "progress": plan.progress_summary(),
        "next_step": {
            "index": next_step.index,
            "description": next_step.description,
            "tool": next_step.tool,
        } if next_step else None,
    })


def handle_self_critique() -> str:
    """Generate a self-reflection prompt."""
    from core.state import reflection_engine
    if not reflection_engine:
        return "Self-reflection: Check your current progress against your goals. Are you on track?"
    return reflection_engine.generate_goal_check()


def handle_record_lesson(lesson: str, category: str) -> str:
    """Record a lesson learned for cross-session persistence."""
    from core.state import session_memory
    if not session_memory:
        return json.dumps({"error": "Session memory not initialized"})
    if category == "user_preference":
        session_memory._record_implicit_preference(
            category="explicit",
            original_pattern="",
            user_replacement=lesson,
        )
    elif category == "pitfall":
        session_memory.record_decision(f"AVOID: {lesson}")
    else:
        session_memory.record_decision(lesson)
    return json.dumps({"status": "recorded", "lesson": lesson, "category": category})


def handle_observe_edit(original: str, edited: str) -> str:
    """Observe a user edit and learn preferences."""
    from core.state import session_memory
    if not session_memory:
        return json.dumps({"error": "Session memory not initialized"})
    session_memory.observe_user_edit(original, edited)
    return json.dumps({"status": "observed", "note": "Preference will strengthen with repeated observations"})


def handle_ask_user(message: str, options: list = None) -> str:
    """Pauses the agent loop and waits for user input."""
    print("\n" + "=" * 60)
    print("AGENT PAUSED - Waiting for your input")
    print("=" * 60)
    print("\n" + message + "\n")
    if options:
        for i, opt in enumerate(options, 1):
            print("  " + str(i) + ". " + opt)
        print()
    try:
        response = input("\033[36mYour response >> \033[0m")
    except (EOFError, KeyboardInterrupt):
        response = "continue"
    return "User responded: " + response


def handle_load_skill(skill_name: str) -> str:
    skills_dir = Path("skills")
    skill_file = skills_dir / f"{skill_name}.md"
    if not skill_file.exists():
        available = [f.stem for f in skills_dir.glob("*.md")] if skills_dir.exists() else []
        return f"Skill '{skill_name}' not found. Available: {available}"
    content = skill_file.read_text(encoding="utf-8")
    if len(content) > 4000:
        content = content[:4000] + "\n\n[... truncated, " + str(len(content)) + " total chars]"
    return content


def handle_dry_run_estimate(operations: list) -> str:
    from tools.dry_run import estimate_plan, format_dry_run_report
    result = estimate_plan(operations)
    return format_dry_run_report(result)


def handle_estimate_single_operation(operation: str, text_length_words: int = 0,
                                     section_count: int = 1, reviewer_count: int = 5) -> str:
    from tools.dry_run import estimate_operation, format_dry_run_report
    result = estimate_operation(
        operation=operation,
        text_length_words=text_length_words,
        section_count=section_count,
        reviewer_count=reviewer_count,
    )
    return format_dry_run_report(result)


def handle_list_checkpoints() -> str:
    """Handler for list_checkpoints tool."""
    from utils.checkpoint import list_checkpoints
    checkpoints = list_checkpoints(str(WORKDIR))
    if not checkpoints:
        return "No resumable checkpoints found."
    lines = [f"Found {len(checkpoints)} resumable checkpoint(s):\n"]
    for cp in checkpoints:
        progress = f"{cp['completed_step'] + 1}/{cp['total_steps_estimate'] or '?'}"
        lines.append(
            f"  \u2022 [{cp['status']}] {cp['pipeline_name']} "
            f"(run_id: {cp['run_id']}, progress: {progress})"
        )
        if cp.get("metadata"):
            meta_str = ", ".join(f"{k}={v}" for k, v in cp["metadata"].items())
            lines.append(f"    metadata: {meta_str}")
        lines.append(f"    created: {cp['created_at']}, updated: {cp['updated_at']}")
    return "\n".join(lines)
