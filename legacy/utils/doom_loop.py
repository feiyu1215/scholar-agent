"""
utils/doom_loop.py — Global doom loop detection for the agent loop.

Prevents the model from calling the same tool with the same core arguments
repeatedly without making progress. When detected, returns a guidance message
to help the model break out of the loop.

Design choices:
- Only core arguments are used for signature (ignoring custom_instructions etc.)
- Tools with built-in retry (deai_audit) have higher thresholds
- Detection message guides the model toward alternative actions
"""

import hashlib
import json
from collections import deque
from typing import Tuple, Set

# Core arguments per tool (only these contribute to the loop signature)
CORE_ARGS = {
    "rewrite_section": ["section_id"],
    "deai_audit": ["section_id"],
    "deai_closed_loop": ["section_id"],
    "fix_ai_signals": ["section_id"],
    "edit_section": ["section_id", "old_text"],
    "generate_fix_proposal": ["issue_id"],
    "stata_verify": ["issue_id"],
    "read_section": ["section_id"],
    "search_literature": ["query"],
    "verify_doi": ["doi"],
    "reaudit": [],
    "architecture_diagnosis": [],
}

# Per-tool thresholds (tools with built-in retry get higher tolerance)
THRESHOLDS = {
    "deai_audit": 4,       # Has internal max 2 retries, so 4 = truly stuck
    "deai_closed_loop": 3, # Complex loop — 3 identical calls = stuck
    "fix_ai_signals": 4,   # Same as deai_audit
    "search_literature": 4, # Might retry with different queries (legitimate)
    "default": 3,
}


class DoomLoopDetector:
    """Sliding-window detector for repetitive tool call patterns.

    Enhanced with fuzzy Jaccard-based matching to detect semantically similar
    calls that differ only in trivial arguments.
    """

    def __init__(self, window: int = 8):
        self.recent_calls: deque = deque(maxlen=window)
        self._signature_tokens: dict = {}  # sig_hash → token set for fuzzy match

    def check(self, tool_name: str, args: dict) -> Tuple[bool, str]:
        """
        Check if the current call constitutes a doom loop.

        Returns:
            (is_looping, message)
            If is_looping is True, message contains guidance for the model.
        """
        signature = self._make_signature(tool_name, args)
        self._signature_tokens[signature] = self._tokenize_args(tool_name, args)
        self.recent_calls.append(signature)

        # Exact match count
        count = self.recent_calls.count(signature)

        # Fuzzy match count: count semantically similar recent calls
        fuzzy_count = sum(
            1 for s in self.recent_calls
            if self._is_semantically_similar(signature, s)
        )
        # Use the higher of exact vs fuzzy (fuzzy always >= exact)
        count = max(count, fuzzy_count)
        threshold = THRESHOLDS.get(tool_name, THRESHOLDS["default"])

        if count >= threshold:
            return True, (
                f"⚠️ LOOP DETECTED: '{tool_name}' has been called {count} times "
                f"with the same core arguments in the last {len(self.recent_calls)} calls. "
                f"This suggests the current approach is not making progress. "
                f"Options: (1) skip this issue and mark as 'needs_manual_fix', "
                f"(2) try a different tool or approach, "
                f"(3) ask the user for guidance via ask_user."
            )

        return False, ""

    def reset(self):
        """Clear detection history (e.g., when user provides new input)."""
        self.recent_calls.clear()
        self._signature_tokens.clear()

    def _make_signature(self, tool_name: str, args: dict) -> str:
        """Generate a signature from tool name + core arguments only."""
        core_keys = CORE_ARGS.get(tool_name, list(args.keys())[:2])
        core_values = {k: args.get(k, "") for k in core_keys}
        raw = f"{tool_name}:{json.dumps(core_values, sort_keys=True)}"
        return hashlib.md5(raw.encode()).hexdigest()[:12]

    def _is_semantically_similar(self, sig_a: str, sig_b: str) -> bool:
        """
        Fuzzy match between two call signatures using Jaccard similarity
        on the underlying token sets.

        This catches near-duplicate calls where only trivial arguments differ
        (e.g., 'rewrite_section' on section_id="2.1" vs "2.1.1" with same instructions).
        """
        tokens_a = self._signature_tokens.get(sig_a, set())
        tokens_b = self._signature_tokens.get(sig_b, set())
        if not tokens_a or not tokens_b:
            return sig_a == sig_b
        intersection = len(tokens_a & tokens_b)
        union = len(tokens_a | tokens_b)
        jaccard = intersection / union if union > 0 else 0.0
        return jaccard >= 0.85  # High similarity threshold

    def _tokenize_args(self, tool_name: str, args: dict) -> Set[str]:
        """Extract token set from tool arguments for fuzzy comparison."""
        core_keys = CORE_ARGS.get(tool_name, list(args.keys())[:2])
        tokens: Set[str] = {tool_name}
        for k in core_keys:
            val = str(args.get(k, "")).lower()
            # Split on non-alphanumeric characters
            tokens.update(w for w in val.split() if len(w) > 1)
        return tokens
