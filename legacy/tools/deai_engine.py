"""
DEPRECATED: This module is kept for backward compatibility.
The implementation has moved to tools/deai/ package.
Import from tools.deai directly for new code.
"""
from tools.deai import *  # noqa: F401, F403

# Explicit imports for private helpers used by existing tests.
# These are not in __all__ so `import *` doesn't re-export them.
from tools.deai.signals import _detect_programmatic_signals  # noqa: F401
from tools.deai.scene import _is_chinese_text, _is_s3_discipline, _has_economics_keywords  # noqa: F401
