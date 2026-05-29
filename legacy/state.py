"""
core/state.py — Shared mutable state for ScholarAgent.

All module-level instances and configuration that handlers need to access.
Initialized in main() at startup.
"""
from pathlib import Path

# Configuration
WORKDIR = Path.cwd()
WORKSPACE = WORKDIR / ".workspace"

# Session config (set in main())
session_budget = "full"
session_provider = None
session_model = None

# Wave 2 instances
goal_tracker = None
plan_store = None
reflection_engine = None

# Wave 3 instances
adaptive_engine = None
context_manager = None
error_recovery = None
output_quality = None

# Wave 4 instances
session_memory = None       # Legacy: SessionMemory (JSON-backed, kept for compat)
meta_planner = None

# Wave 5 instances (Unified Memory System)
unified_memory = None       # UnifiedMemory: 3-tier SQLite-backed memory
