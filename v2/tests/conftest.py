"""Tests conftest.py — ensures v2/ is importable."""
import sys
from pathlib import Path

# v2/ is the parent of this tests/ directory
_v2_root = str(Path(__file__).parent.parent)
if _v2_root not in sys.path:
    sys.path.insert(0, _v2_root)
