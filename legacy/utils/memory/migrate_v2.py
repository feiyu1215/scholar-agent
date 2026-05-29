"""
utils/memory/migrate_v2.py — Migration script: SessionMemory JSON → Unified SQLite.

Run this script to migrate legacy session_memory/ JSON files into the unified
SQLite memory store. Safe to run multiple times (idempotent — duplicates are
merged, not duplicated).

Usage:
    python -m utils.memory.migrate_v2 [--workspace /path/to/project]

What it does:
    1. Reads session_memory/tool_patterns.json → inserts into tool_patterns table
    2. Reads session_memory/implicit_preferences.json → inserts into implicit_preferences table
    3. Backfills memory_tier column on existing memories table entries
    4. Prints migration summary
    5. Does NOT delete original JSON files (user can remove manually after verification)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from utils.memory.unified import get_unified_memory, TYPE_TO_TIER, MemoryTier
from utils.memory.models import MemoryType


def main():
    parser = argparse.ArgumentParser(
        description="Migrate SessionMemory JSON data to unified SQLite store."
    )
    parser.add_argument(
        "--workspace", type=str, default=".",
        help="Path to the project workspace (default: current directory)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be migrated without making changes"
    )
    args = parser.parse_args()

    workspace = Path(args.workspace).resolve()
    sm_dir = workspace / "session_memory"

    print(f"🔄 Memory Migration v2")
    print(f"   Workspace: {workspace}")
    print(f"   Session memory dir: {sm_dir}")
    print()

    if not sm_dir.exists():
        print("   ℹ️  No session_memory/ directory found. Nothing to migrate.")
        print("   (This is normal if the agent hasn't been run in this workspace.)")
        return

    # Show what's available
    patterns_file = sm_dir / "tool_patterns.json"
    prefs_file = sm_dir / "implicit_preferences.json"
    journals_file = sm_dir / "journals.json"

    pattern_count = 0
    pref_count = 0

    if patterns_file.exists():
        try:
            data = json.loads(patterns_file.read_text(encoding="utf-8"))
            pattern_count = len(data)
            print(f"   📋 tool_patterns.json: {pattern_count} patterns")
        except (json.JSONDecodeError, OSError) as e:
            print(f"   ⚠️  tool_patterns.json: error reading ({e})")
    else:
        print("   📋 tool_patterns.json: not found")

    if prefs_file.exists():
        try:
            data = json.loads(prefs_file.read_text(encoding="utf-8"))
            pref_count = len(data)
            print(f"   📋 implicit_preferences.json: {pref_count} preferences")
        except (json.JSONDecodeError, OSError) as e:
            print(f"   ⚠️  implicit_preferences.json: error reading ({e})")
    else:
        print("   📋 implicit_preferences.json: not found")

    if journals_file.exists():
        try:
            data = json.loads(journals_file.read_text(encoding="utf-8"))
            print(f"   📋 journals.json: {len(data)} journal entries (kept as-is)")
        except (json.JSONDecodeError, OSError):
            pass

    print()

    if pattern_count == 0 and pref_count == 0:
        print("   ✅ Nothing to migrate. Database schema will still be updated.")

    if args.dry_run:
        print("   [DRY RUN] No changes made.")
        return

    # Perform migration
    print("   Migrating...")
    mem = get_unified_memory(workspace=workspace)
    result = mem.migrate_session_memory(sm_dir)

    print(f"\n   ✅ Migration complete:")
    print(f"      Patterns migrated: {result['patterns']}")
    print(f"      Preferences migrated: {result['preferences']}")

    # Show final state
    digest = mem.memory_digest()
    print(f"\n   📊 Memory state after migration:")
    print(f"      Total memories: {digest['total_memories']}")
    print(f"      By tier: {digest['by_tier']}")
    print(f"      Tool patterns: {digest['tool_patterns']}")
    print(f"      Implicit preferences: {digest['implicit_preferences']}")
    print(f"      Papers tracked: {digest['papers']}")

    print(f"\n   💡 Original JSON files are preserved in {sm_dir}/")
    print(f"      You can safely delete them after verifying the migration.")

    mem.close()


if __name__ == "__main__":
    main()
