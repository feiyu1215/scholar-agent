#!/usr/bin/env python3
"""CI helper: verify all core/*.py files parse without syntax errors (stdlib only)."""
import ast
import pathlib
import sys


def main():
    errors = []
    core_dir = pathlib.Path("core")
    if not core_dir.exists():
        print("ERROR: core/ directory not found. Run from v2/.")
        sys.exit(1)

    for f in sorted(core_dir.glob("*.py")):
        try:
            ast.parse(f.read_text())
        except SyntaxError as e:
            errors.append(f"{f}: {e}")

    if errors:
        print(f"FAILED: {len(errors)} files have syntax errors:")
        for e in errors:
            print(f"  {e}")
        sys.exit(1)
    else:
        count = len(list(core_dir.glob("*.py")))
        print(f"OK: All {count} core/*.py files parse successfully")


if __name__ == "__main__":
    main()
