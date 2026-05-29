"""
Pytest configuration for ScholarAgent tests.

Ensures the project root is on sys.path so tests can import
project modules (core, utils, tools, etc.) without installation.
"""
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))
