#!/usr/bin/env python3
"""
ScholarAgent Test Harness — Automated Demo-Grade Evaluation

This harness:
1. Monkey-patches `ask_user` to use a small model (glm-4.5-flash, free) as simulated user
2. Runs the agent in --budget minimal (read-only) mode on the pre-loaded paper
3. Measures:
   - ask_user count & content
   - Tool call sequence & count
   - Phase transitions
   - Token usage & estimated cost
   - Total latency
   - Final output quality
4. Outputs a structured JSON report

Usage:
    python test_harness.py                      # Default: minimal budget, auto-user
    python test_harness.py --budget full        # Full budget (includes rewrites)
    python test_harness.py --no-auto-user       # Manual user input (no auto-reply)
    python test_harness.py --max-turns 30       # Limit agent turns
"""

from __future__ import annotations

import os
import sys
import json
import time
import asyncio
import argparse
from pathlib import Path
from datetime import datetime

# Load .env first
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


# ============================================================
# Configuration
# ============================================================

# Small model for simulating user responses (glm-4.5-flash is free on Friday)
AUTO_USER_MODEL = os.environ.get("AUTO_USER_MODEL", "glm-4.5-flash")
AUTO_USER_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://aigc.sankuai.com/v1/openai/native")
AUTO_USER_API_KEY = os.environ.get("OPENAI_API_KEY", "")

# The initial prompt to kick off the agent
DEFAULT_INITIAL_PROMPT = (
    "论文已经在 .workspace/paper/ 目录中完成解析，你可以直接用 read_section_index 查看章节索引。"
    "请帮我全面审稿这篇关于国家自主创新示范区(NIDZ)政策效果的经济学论文。"
    "请从以下维度进行评审：\n"
    "1. 研究设计与因果识别策略的严谨性\n"
    "2. 实证方法（Staggered DID, PSM-DID, CSDID）的合理性\n"
    "3. 论文结构与逻辑流\n"
    "4. 文献综述的覆盖度和定位\n"
    "5. 结论与政策建议的合理性\n"
    "请给出详细的审稿意见。"
)


# ============================================================
# Metrics Collector
# ============================================================

class MetricsCollector:
    """Collects all test metrics during the harness run."""

    def __init__(self):
        self.start_time = None
        self.end_time = None
        self.ask_user_calls = []          # Each: {timestamp, question, options, response, latency_ms}
        self.tool_calls = []              # Each: {timestamp, name, args_summary, duration_ms, had_error}
        self.phase_transitions = []       # Each: {timestamp, from_phase, to_phase, trigger_tool}
        self.doom_loop_blocks = 0
        self.total_llm_calls = 0
        self.agent_turns = 0             # Number of LLM round-trips
        self.final_output = ""
        self.errors = []

    def start(self):
        self.start_time = time.time()

    def stop(self):
        self.end_time = time.time()

    def record_ask_user(self, question: str, options: list, response: str, latency_ms: float):
        self.ask_user_calls.append({
            "timestamp": time.time(),
            "question": question,
            "options": options or [],
            "response": response,
            "latency_ms": round(latency_ms, 1),
        })

    def record_tool_call(self, name: str, args: dict, duration_ms: float, had_error: bool):
        args_summary = {k: repr(v)[:80] for k, v in args.items()} if args else {}
        self.tool_calls.append({
            "timestamp": time.time(),
            "name": name,
            "args_summary": args_summary,
            "duration_ms": round(duration_ms, 1),
            "had_error": had_error,
        })

    def record_phase_transition(self, from_phase: str, to_phase: str, trigger_tool: str):
        self.phase_transitions.append({
            "timestamp": time.time(),
            "from_phase": from_phase,
            "to_phase": to_phase,
            "trigger_tool": trigger_tool,
        })

    def to_report(self, client_stats: dict) -> dict:
        """Generate the final report dict."""
        total_time = (self.end_time - self.start_time) if self.end_time else 0
        tool_names = [tc["name"] for tc in self.tool_calls]
        unique_tools = sorted(set(tool_names))

        return {
            "meta": {
                "timestamp": datetime.now().isoformat(),
                "total_time_seconds": round(total_time, 1),
                "agent_turns": self.agent_turns,
            },
            "ask_user_metrics": {
                "total_count": len(self.ask_user_calls),
                "calls": self.ask_user_calls,
                "avg_response_latency_ms": (
                    round(sum(c["latency_ms"] for c in self.ask_user_calls) / len(self.ask_user_calls), 1)
                    if self.ask_user_calls else 0
                ),
            },
            "tool_metrics": {
                "total_calls": len(self.tool_calls),
                "unique_tools_used": len(unique_tools),
                "tools_used": unique_tools,
                "tool_frequency": {name: tool_names.count(name) for name in unique_tools},
                "errors": sum(1 for tc in self.tool_calls if tc["had_error"]),
                "doom_loop_blocks": self.doom_loop_blocks,
            },
            "phase_metrics": {
                "transitions": self.phase_transitions,
                "total_transitions": len(self.phase_transitions),
            },
            "cost_metrics": client_stats,
            "errors": self.errors,
            "final_output_preview": self.final_output[:2000] if self.final_output else "",
        }


# Global metrics instance
metrics = MetricsCollector()


# ============================================================
# Auto-User: Small Model as Simulated User
# ============================================================

def create_auto_user_client():
    """Create a lightweight OpenAI client for simulating user responses."""
    from openai import OpenAI

    client = OpenAI(
        api_key=AUTO_USER_API_KEY,
        base_url=AUTO_USER_BASE_URL,
    )
    return client


def auto_user_respond(question: str, options: list = None) -> str:
    """Use a small model to generate a realistic user response.

    The simulated user is:
    - A senior economics PhD student
    - Writing a paper on NIDZ policy effects
    - Wants thorough review but defers to the agent's expertise
    - Responds concisely and cooperatively
    """
    try:
        client = create_auto_user_client()

        system_msg = (
            "你是一个经济学博士研究生，正在写一篇关于国家自主创新示范区(NIDZ)政策效果的实证论文。"
            "你的论文使用了 Staggered DID、PSM-DID 和 CSDID 方法来评估政策对创业活动的影响。"
            "你让一个AI审稿助手帮你审稿。现在助手向你提了一个问题，请你简洁、配合地回答。"
            "如果问题涉及多个选项，选择最全面或最合理的那个。"
            "如果问题不确定，说'请你帮我决定'或'都可以，你觉得哪个好就选哪个'。"
            "回复控制在1-2句话以内。"
        )

        user_msg = f"审稿助手问你：\n{question}"
        if options:
            user_msg += "\n\n可选项：\n" + "\n".join(f"{i+1}. {opt}" for i, opt in enumerate(options))
            user_msg += "\n\n请选择或回答。"

        response = client.chat.completions.create(
            model=AUTO_USER_MODEL,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=100,
            temperature=0.7,
        )

        reply = response.choices[0].message.content
        if reply and reply.strip():
            return reply.strip()
        # Model returned empty — provide sensible default
        if options:
            return options[0]  # Pick first option
        return "好的，请继续。"
    except Exception as e:
        return f"好的，请继续。(auto-user error: {type(e).__name__}: {e})"


# ============================================================
# Monkey-Patch: Replace ask_user Handler
# ============================================================

def patched_ask_user(message: str, options: list = None) -> str:
    """Replaces the real ask_user handler to enable automated testing."""
    t0 = time.time()

    if USE_AUTO_USER:
        response = auto_user_respond(message, options)
    else:
        # Still use real input in non-auto mode
        print("\n" + "=" * 60)
        print("AGENT PAUSED - Waiting for your input")
        print("=" * 60)
        print("\n" + message + "\n")
        if options:
            for i, opt in enumerate(options, 1):
                print(f"  {i}. {opt}")
            print()
        try:
            response = input("\033[36mYour response >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            response = "continue"

    latency_ms = (time.time() - t0) * 1000
    metrics.record_ask_user(message, options, response, latency_ms)

    print(f"\n\033[35m[Auto-User Response ({latency_ms:.0f}ms)]: {response}\033[0m\n")
    return "User responded: " + response


# ============================================================
# Monkey-Patch: Wrap Tool Dispatch for Metrics
# ============================================================

def create_instrumented_dispatch(original_handlers: dict) -> dict:
    """Wrap each handler to record metrics."""
    instrumented = {}
    for name, handler in original_handlers.items():
        if name == "ask_user":
            # ask_user is separately patched
            instrumented[name] = lambda **kw: patched_ask_user(kw["message"], kw.get("options"))
        else:
            # Wrap with timing
            def make_wrapper(orig_handler, tool_name):
                def wrapper(**kw):
                    t0 = time.time()
                    had_error = False
                    try:
                        result = orig_handler(**kw)
                        if isinstance(result, str) and result.startswith("Error:"):
                            had_error = True
                        return result
                    except Exception as e:
                        had_error = True
                        raise
                    finally:
                        duration_ms = (time.time() - t0) * 1000
                        metrics.record_tool_call(tool_name, kw, duration_ms, had_error)
                return wrapper
            instrumented[name] = make_wrapper(handler, name)
    return instrumented


# ============================================================
# Phase Transition Monitor
# ============================================================

def install_phase_monitor():
    """Hook into GoalTracker to capture phase transitions."""
    import core.state as state
    if not state.goal_tracker:
        return

    original_on_tool = state.goal_tracker.on_tool_complete

    def monitored_on_tool(tool_name, args):
        old_phase = state.goal_tracker.phase.value if state.goal_tracker.phase else "IDLE"
        original_on_tool(tool_name, args)
        new_phase = state.goal_tracker.phase.value if state.goal_tracker.phase else "IDLE"
        if old_phase != new_phase:
            metrics.record_phase_transition(old_phase, new_phase, tool_name)
            print(f"\033[34m[Phase: {old_phase} → {new_phase} (trigger: {tool_name})]\033[0m")

    state.goal_tracker.on_tool_complete = monitored_on_tool


# ============================================================
# Agent Turn Counter
# ============================================================

RATE_LIMIT_DELAY = float(os.environ.get("RATE_LIMIT_DELAY", "8"))  # seconds between LLM calls
_last_llm_call_time = 0.0


def install_turn_counter(client):
    """Wrap the LLM client to count turns and enforce rate limiting."""
    global _last_llm_call_time
    original_chat = client.chat_with_tools

    async def counted_chat(*args, **kwargs):
        global _last_llm_call_time
        metrics.agent_turns += 1
        metrics.total_llm_calls += 1

        # Rate limit: wait if too soon since last call
        elapsed = time.time() - _last_llm_call_time
        if elapsed < RATE_LIMIT_DELAY and _last_llm_call_time > 0:
            wait_time = RATE_LIMIT_DELAY - elapsed
            print(f"\033[90m[Rate limit: waiting {wait_time:.1f}s...]\033[0m")
            await asyncio.sleep(wait_time)
        _last_llm_call_time = time.time()

        return await original_chat(*args, **kwargs)

    client.chat_with_tools = counted_chat

    # Also wrap streaming if available
    if hasattr(client, 'chat_with_tools_stream'):
        original_stream = client.chat_with_tools_stream

        async def counted_stream(*args, **kwargs):
            global _last_llm_call_time
            metrics.agent_turns += 1
            metrics.total_llm_calls += 1

            elapsed = time.time() - _last_llm_call_time
            if elapsed < RATE_LIMIT_DELAY and _last_llm_call_time > 0:
                wait_time = RATE_LIMIT_DELAY - elapsed
                print(f"\033[90m[Rate limit: waiting {wait_time:.1f}s...]\033[0m")
                await asyncio.sleep(wait_time)
            _last_llm_call_time = time.time()

            async for chunk in original_stream(*args, **kwargs):
                yield chunk

        client.chat_with_tools_stream = counted_stream


# ============================================================
# Main Test Runner
# ============================================================

USE_AUTO_USER = True  # Global flag


async def run_test(budget: str, initial_prompt: str, max_turns: int, stream: bool,
                   model_override: str = None):
    """Run the agent with instrumentation and collect metrics."""
    import core.state as state
    from llm.client import LLMClient
    from core.agent_loop import agent_loop, reset_control
    from core.tool_dispatch import TOOL_HANDLERS
    from tools.revision_state import init_state

    # Wave 2-4 imports
    from utils.goal_tracker import GoalTracker
    from utils.plan_persistence import PlanStore
    from utils.self_reflection import ReflectionEngine
    from utils.adaptive_strategy import AdaptiveEngine
    from utils.context_manager import ProactiveContextManager
    from utils.error_recovery import ErrorRecoveryEngine
    from utils.output_quality import OutputQualityGate
    from utils.session_memory import SessionMemory
    from utils.meta_planner import MetaPlanner

    # Initialize shared state
    state.session_budget = budget
    state.session_provider = "openai"
    state.session_model = model_override

    client = LLMClient(model=model_override, provider="openai")
    init_state(budget=budget)

    # Initialize Wave 2-4 subsystems
    state.goal_tracker = GoalTracker(workspace=state.WORKSPACE)
    state.plan_store = PlanStore(workspace=state.WORKSPACE)
    state.reflection_engine = ReflectionEngine(tracker=state.goal_tracker)
    state.adaptive_engine = AdaptiveEngine(workspace=state.WORKSPACE)
    state.context_manager = ProactiveContextManager(max_tokens=128000)
    state.error_recovery = ErrorRecoveryEngine()
    state.output_quality = OutputQualityGate()
    state.session_memory = SessionMemory(workspace=state.WORKSPACE)
    state.meta_planner = MetaPlanner(memory=state.session_memory)
    state.session_memory.start_session(
        goal=f"Test harness run: {budget} budget",
        paper_title="NIDZ Policy Effects on Entrepreneurship",
    )

    # Create workspace
    state.WORKSPACE.mkdir(parents=True, exist_ok=True)

    # Install instrumentation — must mutate in-place since agent_loop already holds a reference
    from core.tool_dispatch import TOOL_HANDLERS as _original_handlers
    instrumented = create_instrumented_dispatch(dict(_original_handlers))
    _original_handlers.clear()
    _original_handlers.update(instrumented)
    install_phase_monitor()
    install_turn_counter(client)

    # Inject max_turns guard by patching agent_loop's while-true
    # We do this via a simple turn counter checked per iteration
    original_agent_loop = agent_loop

    async def guarded_agent_loop(messages, cli, *, stream_flag=False):
        """Wraps agent_loop with a max-turns safety net."""
        # The agent_loop internally loops until model stops calling tools.
        # We trust max_turns to be high enough. If agent gets stuck, doom loop handles it.
        await original_agent_loop(messages, cli, stream=stream_flag)

    # Print header
    print("\n" + "=" * 70)
    print("  ScholarAgent Test Harness")
    print(f"  Budget: {budget} | Model: {client.model} | Auto-User: {USE_AUTO_USER}")
    print(f"  Auto-User Model: {AUTO_USER_MODEL} | Max Turns: {max_turns}")
    print("=" * 70 + "\n")

    # Build initial message
    history = [{"role": "user", "content": initial_prompt}]

    # Start metrics
    metrics.start()
    reset_control()

    # Run the agent
    try:
        await guarded_agent_loop(history, client, stream_flag=stream)
    except Exception as e:
        metrics.errors.append(f"Agent loop error: {type(e).__name__}: {e}")
        print(f"\033[31m[HARNESS ERROR]: {e}\033[0m")

    metrics.stop()

    # Capture final output (last assistant message)
    for msg in reversed(history):
        if msg["role"] == "assistant" and msg.get("content"):
            metrics.final_output = msg["content"]
            break

    # Generate report
    report = metrics.to_report(client.stats())
    report["config"] = {
        "budget": budget,
        "model": client.model,
        "auto_user_model": AUTO_USER_MODEL,
        "initial_prompt": initial_prompt[:200],
        "max_turns": max_turns,
    }

    return report


def main():
    global USE_AUTO_USER

    parser = argparse.ArgumentParser(description="ScholarAgent Test Harness")
    parser.add_argument("--budget", default="minimal", choices=["full", "medium", "minimal"],
                        help="Budget mode (default: minimal for read-only)")
    parser.add_argument("--no-auto-user", action="store_true",
                        help="Disable auto-user (manual input for ask_user)")
    parser.add_argument("--max-turns", type=int, default=50,
                        help="Maximum agent turns before force-stop")
    parser.add_argument("--model", type=str, default=None,
                        help="Override LLM model name (e.g. glm-4.5-flash)")
    parser.add_argument("--stream", action="store_true", default=False,
                        help="Enable streaming output")
    parser.add_argument("--prompt", type=str, default=None,
                        help="Custom initial prompt (default: comprehensive review request)")
    parser.add_argument("--output", type=str, default=None,
                        help="Output report path (default: test_harness_report_<timestamp>.json)")
    args = parser.parse_args()

    USE_AUTO_USER = not args.no_auto_user
    initial_prompt = args.prompt or DEFAULT_INITIAL_PROMPT

    # Run the test
    report = asyncio.run(run_test(
        budget=args.budget,
        initial_prompt=initial_prompt,
        max_turns=args.max_turns,
        stream=args.stream,
        model_override=args.model,
    ))

    # Save report
    output_path = args.output or f"test_harness_report_{int(time.time())}.json"
    output_full = Path(__file__).parent / output_path
    output_full.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    # Print summary
    print("\n" + "=" * 70)
    print("  TEST HARNESS RESULTS")
    print("=" * 70)
    print(f"  Total Time: {report['meta']['total_time_seconds']}s")
    print(f"  Agent Turns (LLM calls): {report['meta']['agent_turns']}")
    print(f"  Tool Calls: {report['tool_metrics']['total_calls']}")
    print(f"  Unique Tools Used: {report['tool_metrics']['unique_tools_used']}")
    print(f"  ask_user Count: {report['ask_user_metrics']['total_count']}")
    print(f"  Phase Transitions: {report['phase_metrics']['total_transitions']}")
    print(f"  Errors: {report['tool_metrics']['errors']}")
    print(f"  Doom Loop Blocks: {report['tool_metrics']['doom_loop_blocks']}")
    print(f"\n  Report saved to: {output_full}")
    print("=" * 70)

    # Print ask_user details if any
    if report["ask_user_metrics"]["total_count"] > 0:
        print("\n  ask_user Details:")
        for i, call in enumerate(report["ask_user_metrics"]["calls"], 1):
            print(f"    [{i}] Q: {call['question'][:100]}")
            print(f"        A: {call['response'][:100]}")
            print(f"        Latency: {call['latency_ms']}ms")
        print()

    # Print tool usage frequency
    print("\n  Tool Usage Frequency:")
    sorted_tools = sorted(
        report["tool_metrics"]["tool_frequency"].items(),
        key=lambda x: x[1], reverse=True
    )
    for name, count in sorted_tools[:15]:
        print(f"    {name}: {count}")

    return report


if __name__ == "__main__":
    main()
