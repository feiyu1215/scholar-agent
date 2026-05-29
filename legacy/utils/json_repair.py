"""
Four-layer JSON parsing with graceful degradation.

Inspired by career-copilot's multi-layer recovery strategy:
  Layer 1: Direct json.loads
  Layer 2: Find { } or [ ] boundaries and parse the substring
  Layer 3: Fix common LLM formatting errors (trailing commas, single quotes, 
           unescaped newlines, comments, trailing text after JSON)
  Layer 4: Regex extraction of key fields as graceful degradation (marks is_fallback=True)

Every return is a dict with:
  {"data": <parsed_dict_or_list>, "layer": int, "is_fallback": bool, "error": str|None}
"""

import json
import re
from typing import Any, Dict, List, Optional, Union


def robust_json_parse(
    text: str,
    expected_keys: Optional[List[str]] = None,
    fallback_patterns: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Parse JSON from LLM output with 4-layer fallback.
    
    Args:
        text: Raw LLM output (may contain markdown fences, prose, etc.)
        expected_keys: Keys to extract in Layer 4 regex fallback
        fallback_patterns: Custom regex patterns for Layer 4 extraction
            e.g. {"score": r'"score"\s*:\s*(\d+\.?\d*)', "verdict": r'"verdict"\s*:\s*"([^"]+)"'}
    
    Returns:
        {"data": parsed_result, "layer": 1-4, "is_fallback": bool, "error": str|None}
    """
    
    # Layer 1: Direct parse
    result = _layer1_direct(text)
    if result is not None:
        return {"data": result, "layer": 1, "is_fallback": False, "error": None}
    
    # Layer 2: Find JSON boundaries
    result = _layer2_boundaries(text)
    if result is not None:
        return {"data": result, "layer": 2, "is_fallback": False, "error": None}
    
    # Layer 3: Fix common errors then parse
    result = _layer3_repair(text)
    if result is not None:
        return {"data": result, "layer": 3, "is_fallback": False, "error": None}
    
    # Layer 4: Regex extraction (graceful degradation)
    result = _layer4_regex(text, expected_keys, fallback_patterns)
    error_msg = "All JSON parse layers failed; extracted fields via regex"
    if result:
        return {"data": result, "layer": 4, "is_fallback": True, "error": error_msg}
    
    # Complete failure
    return {"data": {}, "layer": 4, "is_fallback": True, "error": f"JSON parse failed completely. Raw text: {text[:200]}"}


def _layer1_direct(text: str) -> Optional[Union[dict, list]]:
    """Try direct json.loads on the full text."""
    text = text.strip()
    # Strip markdown code fences
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first line (```json or ```) and last line (```)
        if lines[-1].strip() == "```":
            text = "\n".join(lines[1:-1])
        else:
            text = "\n".join(lines[1:])
    try:
        result = json.loads(text)
        if isinstance(result, (dict, list)):
            return result
    except (json.JSONDecodeError, ValueError):
        pass
    return None


def _layer2_boundaries(text: str) -> Optional[Union[dict, list]]:
    """Find outermost { } or [ ] boundaries and parse the substring."""
    # Try object first
    obj_result = _find_and_parse(text, "{", "}")
    if obj_result is not None:
        return obj_result
    
    # Try array
    arr_result = _find_and_parse(text, "[", "]")
    if arr_result is not None:
        return arr_result
    
    return None


def _find_and_parse(text: str, open_char: str, close_char: str) -> Optional[Union[dict, list]]:
    """Find balanced delimiters and parse."""
    start = text.find(open_char)
    if start == -1:
        return None
    
    # Find matching closing bracket (handle nesting)
    depth = 0
    in_string = False
    escape_next = False
    
    for i in range(start, len(text)):
        c = text[i]
        
        if escape_next:
            escape_next = False
            continue
        
        if c == '\\' and in_string:
            escape_next = True
            continue
        
        if c == '"' and not escape_next:
            in_string = not in_string
            continue
        
        if in_string:
            continue
        
        if c == open_char:
            depth += 1
        elif c == close_char:
            depth -= 1
            if depth == 0:
                candidate = text[start:i+1]
                try:
                    result = json.loads(candidate)
                    if isinstance(result, (dict, list)):
                        return result
                except (json.JSONDecodeError, ValueError):
                    break
    
    return None


def _layer3_repair(text: str) -> Optional[Union[dict, list]]:
    """Fix common LLM JSON formatting errors and retry parsing."""
    # Strip markdown fences
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        if lines[-1].strip() == "```":
            cleaned = "\n".join(lines[1:-1])
        else:
            cleaned = "\n".join(lines[1:])
    
    # Find JSON boundaries first
    start = -1
    for i, c in enumerate(cleaned):
        if c in ('{', '['):
            start = i
            break
    
    if start == -1:
        return None
    
    # Find the last } or ]
    end = -1
    for i in range(len(cleaned) - 1, start, -1):
        if cleaned[i] in ('}', ']'):
            end = i
            break
    
    if end == -1:
        return None
    
    candidate = cleaned[start:end+1]
    
    # Apply fixes in sequence
    fixes = [
        # Remove trailing commas before } or ]
        (r',\s*([}\]])', r'\1'),
        # Replace single quotes with double quotes (careful with apostrophes)
        # Only replace when it looks like JSON keys/values
        (r"(?<=[{,\[])\s*'([^']+)'\s*:", r' "\1":'),
        (r":\s*'([^']*)'", r': "\1"'),
        # Remove // line comments
        (r'//[^\n]*', ''),
        # Remove /* */ block comments  
        (r'/\*.*?\*/', ''),
    ]
    
    repaired = candidate
    for pattern, replacement in fixes:
        repaired = re.sub(pattern, replacement, repaired)
    
    # Try parsing the repaired version
    try:
        result = json.loads(repaired)
        if isinstance(result, (dict, list)):
            return result
    except (json.JSONDecodeError, ValueError):
        pass
    
    # One more attempt: try to fix missing quotes around keys
    try:
        # Pattern: unquoted keys like {key: "value"}
        fixed_keys = re.sub(r'(?<=[{,])\s*(\w+)\s*:', r' "\1":', repaired)
        result = json.loads(fixed_keys)
        if isinstance(result, (dict, list)):
            return result
    except (json.JSONDecodeError, ValueError):
        pass
    
    return None


def _layer4_regex(
    text: str,
    expected_keys: Optional[List[str]] = None,
    fallback_patterns: Optional[Dict[str, str]] = None,
) -> Optional[dict]:
    """Extract key-value pairs via regex as last resort."""
    result = {}
    
    # Use custom patterns if provided
    if fallback_patterns:
        for key, pattern in fallback_patterns.items():
            match = re.search(pattern, text)
            if match:
                value = match.group(1)
                # Try to parse as number
                try:
                    value = float(value)
                    if value == int(value):
                        value = int(value)
                except (ValueError, TypeError):
                    pass
                result[key] = value
    
    # Try to extract expected keys generically
    if expected_keys:
        for key in expected_keys:
            if key in result:
                continue
            # Try: "key": "value" or "key": number or "key": bool
            patterns = [
                rf'"{key}"\s*:\s*"([^"]*)"',           # string value
                rf'"{key}"\s*:\s*(-?\d+\.?\d*)',        # numeric value
                rf'"{key}"\s*:\s*(true|false|null)',    # bool/null
                rf'"{key}"\s*:\s*\[([^\]]*)\]',        # array (as string)
            ]
            for pat in patterns:
                match = re.search(pat, text, re.IGNORECASE)
                if match:
                    value = match.group(1)
                    # Type coercion
                    if value in ('true', 'True'):
                        value = True
                    elif value in ('false', 'False'):
                        value = False
                    elif value == 'null':
                        value = None
                    else:
                        try:
                            value = float(value)
                            if value == int(value):
                                value = int(value)
                        except (ValueError, TypeError):
                            pass
                    result[key] = value
                    break
    
    return result if result else None
