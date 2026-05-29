"""handlers/deai_ops.py — De-AI audit, closed loop, and pipeline step handlers."""

import json
from pathlib import Path

from core.state import WORKSPACE


async def handle_deai_audit(section_id: str, scene: str = None,
                            provider: str = None, model: str = None) -> str:
    from tools.deai_engine import deai_audit, format_deai_result, detect_scene
    from handlers.paper_ops import _load_paper_metadata

    rev_path = WORKSPACE / "revisions" / (section_id + "_v2.md")
    if rev_path.exists():
        text = rev_path.read_text(encoding="utf-8")
    else:
        index_path = WORKSPACE / "paper" / "section_index.json"
        if not index_path.exists():
            return "Error: No paper parsed."
        index = json.loads(index_path.read_text(encoding="utf-8"))
        entry = next((e for e in index if section_id in e.get("id", "")), None)
        if not entry:
            return "Error: Section '" + section_id + "' not found."
        sec_path = Path(entry["file"])
        if not sec_path.exists():
            return "Error: Section file not found: " + entry["file"]
        text = sec_path.read_text(encoding="utf-8")

    # Auto-detect scene from paper metadata (infrastructure concern, not Agent's job)
    if not scene:
        metadata = _load_paper_metadata()
        scene = detect_scene(text, metadata=metadata)

    verdict = await deai_audit(text, scene=scene, provider=provider, model=model)
    return format_deai_result(verdict)


async def handle_deai_closed_loop(section_id: str, scene: str = None,
                                  provider: str = None, model: str = None) -> str:
    from tools.deai_engine import closed_loop_fix, format_closed_loop_result, detect_scene
    from handlers.paper_ops import _load_paper_metadata

    rev_path = WORKSPACE / "revisions" / (section_id + "_v2.md")
    if rev_path.exists():
        text = rev_path.read_text(encoding="utf-8")
    else:
        index_path = WORKSPACE / "paper" / "section_index.json"
        if not index_path.exists():
            return "Error: No paper parsed."
        index = json.loads(index_path.read_text(encoding="utf-8"))
        entry = next((e for e in index if section_id in e.get("id", "")), None)
        if not entry:
            return "Error: Section '" + section_id + "' not found."
        sec_path = Path(entry["file"])
        if not sec_path.exists():
            return "Error: Section file not found: " + entry["file"]
        text = sec_path.read_text(encoding="utf-8")

    # Auto-detect scene from paper metadata (infrastructure concern, not Agent's job)
    if not scene:
        metadata = _load_paper_metadata()
        scene = detect_scene(text, metadata=metadata)

    orig_path = WORKSPACE / "paper" / (section_id + ".md")
    original_text = orig_path.read_text(encoding="utf-8") if orig_path.exists() else None

    final_text, verdict, self_check, fixes = await closed_loop_fix(
        text, original_text=original_text, scene=scene,
        provider=provider, model=model
    )

    cl_dir = WORKSPACE / "deai" / "closed_loop"
    cl_dir.mkdir(parents=True, exist_ok=True)
    result_path = cl_dir / (section_id + "_result.json")
    result_path.write_text(json.dumps({
        "section_id": section_id,
        "verdict": verdict.to_dict(),
        "self_check_passed": self_check.all_passed,
        "self_check_score": self_check.overall_score,
        "fixes_count": len(fixes),
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    return format_closed_loop_result(verdict, self_check, fixes)


def handle_deai_detect(text: str, scene: str = "S1") -> str:
    from tools.deai_pipeline import detect_ai_signals
    result = detect_ai_signals(text, scene=scene)
    return json.dumps(result, indent=2, ensure_ascii=False)


def handle_deai_diagnose(text: str, signals: list, scene: str = "S1") -> str:
    from tools.deai_pipeline import diagnose_signals
    result = diagnose_signals(text, signals, scene=scene)
    return json.dumps(result, indent=2, ensure_ascii=False)


def handle_deai_rewrite(text: str, fix_strategy: list, scene: str = "S1",
                        author_constraints: str = "") -> str:
    from tools.deai_pipeline import rewrite_text
    result = rewrite_text(text, fix_strategy, scene=scene,
                          author_constraints=author_constraints)
    return json.dumps(result, indent=2, ensure_ascii=False)


def handle_deai_verify(original_text: str, revised_text: str, scene: str = "S1") -> str:
    from tools.deai_pipeline import verify_rewrite
    result = verify_rewrite(original_text, revised_text, scene=scene)
    return json.dumps(result, indent=2, ensure_ascii=False)
