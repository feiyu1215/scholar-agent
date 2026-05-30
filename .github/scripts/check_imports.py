#!/usr/bin/env python3
"""CI helper: verify all core modules import cleanly (requires deps installed)."""
import importlib
import pathlib
import sys


def main():
    errors = []
    core_dir = pathlib.Path("core")
    if not core_dir.exists():
        print("ERROR: core/ directory not found. Run from v2/.")
        sys.exit(1)

    sys.path.insert(0, ".")
    for f in sorted(core_dir.glob("*.py")):
        if f.name == "__init__.py":
            continue
        module_name = f"core.{f.stem}"
        try:
            importlib.import_module(module_name)
        except Exception as e:
            errors.append(f"{module_name}: {type(e).__name__}: {e}")

    if errors:
        print(f"FAILED: {len(errors)} modules failed to import:")
        for e in errors:
            print(f"  {e}")
        sys.exit(1)
    else:
        count = len(list(core_dir.glob("*.py"))) - 1  # exclude __init__.py
        print(f"OK: All {count} core modules imported successfully")


if __name__ == "__main__":
    main()
