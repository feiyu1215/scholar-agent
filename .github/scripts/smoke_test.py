#!/usr/bin/env python3
"""CI helper: end-to-end smoke test — agent init + config integrity."""
import json
import sys
from pathlib import Path


def main():
    sys.path.insert(0, ".")

    # 1. Verify agent class
    from core.agent import ScholarAgent
    assert hasattr(ScholarAgent, "start"), "Missing start method"
    assert hasattr(ScholarAgent, "chat"), "Missing chat method"
    assert hasattr(ScholarAgent, "get_findings"), "Missing get_findings method"
    print("ScholarAgent class: OK (start, chat, get_findings)")

    # 2. Verify constitutional constants
    from core.godel_config import SUB_PERSPECTIVE_MAX_TURNS_CAP, MAX_META_DEPTH
    assert MAX_META_DEPTH == 2, f"MAX_META_DEPTH={MAX_META_DEPTH}, expected 2"
    assert SUB_PERSPECTIVE_MAX_TURNS_CAP >= 30, f"CAP={SUB_PERSPECTIVE_MAX_TURNS_CAP}, too low"
    print("Constitutional constants: OK")

    # 3. Verify config files parse correctly
    profiles_path = Path("config/model_profiles.json")
    if profiles_path.exists():
        profiles = json.loads(profiles_path.read_text())
        assert "profiles" in profiles, "model_profiles.json missing profiles key"
        assert "default" in profiles, "model_profiles.json missing default key"
        print(f"model_profiles.json: OK ({len(profiles['profiles'])} models)")
    else:
        print("model_profiles.json: SKIPPED (file not found)")

    # 4. Verify kill switch count
    import core.godel_config as gc
    flags = [k for k in dir(gc) if k.startswith("GODEL_") and k.endswith("_ENABLED")]
    assert len(flags) >= 29, f"Only {len(flags)} kill switches found, expected >= 29"
    print(f"Kill switches: {len(flags)} registered")

    print("\nSmoke test PASSED")


if __name__ == "__main__":
    main()
