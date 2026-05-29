"""
utils/gold_standard.py — Gold Standard Sedimentation.

When all review issues for a section are resolved (all_fixes == []),
the final revised text is saved as a "gold standard" example.

Gold standards serve as:
1. Few-shot examples for future rewrites of similar sections
2. Quality benchmarks for regression detection
3. Voice profile training data
4. Evidence of improvement (before/after pairs)

Storage: .workspace/gold_standards/{section_type}/
"""

from __future__ import annotations

import json
import time
import hashlib
from pathlib import Path
from typing import Dict, List, Optional

WORKSPACE = Path(".workspace")
GOLD_DIR = WORKSPACE / "gold_standards"


def check_and_store_gold(section_id: str, section_type: str = "generic") -> Optional[str]:
    """
    Check if a section qualifies for gold standard (all issues resolved).
    If so, store the before/after pair.
    
    Args:
        section_id: The section that was revised
        section_type: Category for organization (e.g., "introduction", "methods", "results")
    
    Returns:
        Path to gold standard file if stored, None otherwise.
    """
    # Check if all issues for this section are done
    routed_path = WORKSPACE / "review" / "routed_issues.json"
    if not routed_path.exists():
        return None
    
    routed = json.loads(routed_path.read_text(encoding="utf-8"))
    section_issues = [
        i for i in routed
        if section_id.lower() in i.get("location", {}).get("section_id", "").lower()
        or section_id.lower() in str(i.get("location", "")).lower()
    ]
    
    # All issues must be done/fixed
    pending = [i for i in section_issues if i.get("status") not in ("done", "fixed", "skipped")]
    if pending:
        return None
    
    # Must have at least 1 resolved issue (not just "no issues")
    if not section_issues:
        return None
    
    # Get original and revised text
    original_text = _get_original_text(section_id)
    revised_text = _get_revised_text(section_id)
    
    if not original_text or not revised_text:
        return None
    
    # Don't store if no meaningful change
    if original_text.strip() == revised_text.strip():
        return None
    
    # Store gold standard
    gold_entry = {
        "section_id": section_id,
        "section_type": section_type,
        "created_at": time.time(),
        "issues_resolved": len(section_issues),
        "issue_categories": list(set(i.get("category", "unknown") for i in section_issues)),
        "original": original_text,
        "revised": revised_text,
        "content_hash": hashlib.md5(revised_text.encode()).hexdigest()[:8],
        "word_count_original": len(original_text.split()),
        "word_count_revised": len(revised_text.split()),
    }
    
    # Save
    type_dir = GOLD_DIR / section_type
    type_dir.mkdir(parents=True, exist_ok=True)
    
    filename = f"{section_id}_{gold_entry['content_hash']}.json"
    gold_path = type_dir / filename
    gold_path.write_text(
        json.dumps(gold_entry, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    
    return str(gold_path)


def get_gold_examples(section_type: str, max_examples: int = 2) -> List[Dict]:
    """
    Retrieve gold standard examples for a section type.
    Used as few-shot context in rewrite prompts.
    
    Returns list of {original_preview, revised_preview, categories} dicts.
    """
    type_dir = GOLD_DIR / section_type
    if not type_dir.exists():
        return []
    
    examples = []
    for gold_file in sorted(type_dir.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True):
        try:
            data = json.loads(gold_file.read_text(encoding="utf-8"))
            examples.append({
                "original_preview": data["original"][:500],
                "revised_preview": data["revised"][:500],
                "categories": data.get("issue_categories", []),
                "section_id": data.get("section_id", ""),
            })
        except (json.JSONDecodeError, KeyError):
            continue
        
        if len(examples) >= max_examples:
            break
    
    return examples


def format_gold_as_fewshot(examples: List[Dict]) -> str:
    """Format gold examples as few-shot prompt context."""
    if not examples:
        return ""
    
    parts = ["## Reference: Previous successful rewrites for similar sections:\n"]
    for i, ex in enumerate(examples, 1):
        parts.append(f"### Example {i} (categories: {', '.join(ex['categories'])})")
        parts.append(f"BEFORE:\n{ex['original_preview']}\n")
        parts.append(f"AFTER:\n{ex['revised_preview']}\n")
    
    return "\n".join(parts)


def get_gold_stats() -> Dict:
    """Summary stats for /gold command."""
    if not GOLD_DIR.exists():
        return {"total": 0, "by_type": {}}
    
    by_type = {}
    total = 0
    
    for type_dir in GOLD_DIR.iterdir():
        if type_dir.is_dir():
            count = len(list(type_dir.glob("*.json")))
            by_type[type_dir.name] = count
            total += count
    
    return {"total": total, "by_type": by_type}


def _get_original_text(section_id: str) -> Optional[str]:
    """Get original section text."""
    index_path = WORKSPACE / "paper" / "section_index.json"
    if not index_path.exists():
        return None
    
    index = json.loads(index_path.read_text(encoding="utf-8"))
    entry = next((e for e in index if section_id in e.get("id", "")), None)
    if not entry:
        return None
    
    sec_path = Path(entry["file"])
    if not sec_path.exists():
        return None
    
    return sec_path.read_text(encoding="utf-8")


def _get_revised_text(section_id: str) -> Optional[str]:
    """Get revised section text."""
    rev_path = WORKSPACE / "revisions" / f"{section_id}_v2.md"
    if not rev_path.exists():
        return None
    return rev_path.read_text(encoding="utf-8")
