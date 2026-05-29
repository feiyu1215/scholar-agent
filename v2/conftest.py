"""Root conftest.py for v2 tests — ensures v2/ is in sys.path and takes priority."""
import os
import sys
from pathlib import Path

# 确保 v2/ 目录在 sys.path 最前面，使 `from core.xxx import ...` 找到 v2/core/
_v2_root = str(Path(__file__).parent)

# 关键：移除可能存在的 repo root（它有一个同名的 legacy core/ 包会 shadow v2/core/）
_repo_root = str(Path(__file__).parent.parent)
while _repo_root in sys.path:
    sys.path.remove(_repo_root)

# 确保 v2/ 在最前面
if _v2_root in sys.path:
    sys.path.remove(_v2_root)
sys.path.insert(0, _v2_root)

# 同时设置 PYTHONPATH（某些 subprocess 测试可能需要）
os.environ["PYTHONPATH"] = _v2_root
