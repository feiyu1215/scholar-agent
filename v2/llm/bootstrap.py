"""
llm/bootstrap.py — First-run provider configuration dialog.

No LLM dependency. Pure template-driven interactive setup.
Generates config/providers.json from user input.

Usage:
    from llm.bootstrap import run_bootstrap
    config_path = run_bootstrap(config_dir=Path("config"))
"""

from __future__ import annotations

import getpass
import json
import sys
from pathlib import Path


# ============================================================
# Template: Known Provider Presets
# ============================================================

PROVIDER_PRESETS = {
    "friday": {
        "display_name": "美团 Friday One-API",
        "base_url": "https://aigc.sankuai.com/v1/openai/native",
        "description": "美团内部 OpenAI 兼容接口，支持 GPT/DeepSeek/GLM 等模型",
        "default_models": [
            {
                "id": "gpt-4.1",
                "display_name": "GPT-4.1",
                "tags": ["general", "writing", "reasoning"],
                "cost_tier": "high",
            },
            {
                "id": "gpt-4.1-mini",
                "display_name": "GPT-4.1 Mini",
                "tags": ["general", "fast"],
                "cost_tier": "low",
            },
            {
                "id": "deepseek-r1-friday",
                "display_name": "DeepSeek R1",
                "tags": ["reasoning", "math"],
                "cost_tier": "high",
            },
            {
                "id": "deepseek-v3-friday",
                "display_name": "DeepSeek V3",
                "tags": ["general", "fast"],
                "cost_tier": "medium",
            },
            {
                "id": "glm-4.5-flash",
                "display_name": "GLM-4.5 Flash",
                "tags": ["chinese", "fast"],
                "cost_tier": "low",
            },
        ],
    },
    "openai": {
        "display_name": "OpenAI Official",
        "base_url": "https://api.openai.com/v1",
        "description": "OpenAI 官方 API",
        "default_models": [
            {
                "id": "gpt-4o",
                "display_name": "GPT-4o",
                "tags": ["general", "multimodal"],
                "cost_tier": "high",
            },
            {
                "id": "gpt-4o-mini",
                "display_name": "GPT-4o Mini",
                "tags": ["general", "fast"],
                "cost_tier": "low",
            },
        ],
    },
    "custom": {
        "display_name": "自定义 OpenAI 兼容接口",
        "base_url": "",  # User provides
        "description": "任何 OpenAI 兼容的 API 端点",
        "default_models": [],
    },
}


# ============================================================
# Bootstrap Dialog
# ============================================================


def run_bootstrap(
    config_dir: Path = Path("config"),
    input_fn=None,
    print_fn=None,
) -> Path:
    """
    Interactive first-run setup. No LLM needed.

    Flow:
        1. Welcome message
        2. Select provider preset (or custom)
        3. Enter API key
        4. Confirm/edit model list
        5. Set token budget
        6. Write config/providers.json

    Args:
        config_dir: Directory to write providers.json into.
        input_fn: Optional input function override (for testing).
        print_fn: Optional print function override (for testing).

    Returns:
        Path to generated config file
    """
    # Allow injection for testing
    _input = input_fn or input
    _print = print_fn or print

    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "providers.json"

    _print("")
    _print("=" * 60)
    _print("  ScholarAgent — 模型配置向导")
    _print("=" * 60)
    _print("")
    _print("  首次运行，需要配置你的 LLM 模型。")
    _print("")

    # Step 1: Select provider
    _print("  可选的 Provider 预设:")
    presets = list(PROVIDER_PRESETS.items())
    for i, (key, preset) in enumerate(presets, 1):
        _print(f"    {i}. {preset['display_name']} — {preset['description']}")

    choice = _input_int(
        "  选择 (输入数字): ", 1, len(presets), input_fn=_input, print_fn=_print
    )
    provider_key, preset = presets[choice - 1]

    # Step 2: Base URL (custom only) + API Key
    base_url = preset["base_url"]
    if provider_key == "custom":
        base_url = _input("  请输入 API Base URL: ").strip()
        if not base_url:
            _print("  ⚠ Base URL 不能为空。")
            raise SystemExit(1)

    # Use getpass for real terminal, plain input for testing
    if input_fn is not None:
        api_key = _input(
            f"  请输入 {preset['display_name']} 的 API Key: "
        ).strip()
    else:
        try:
            api_key = getpass.getpass(
                f"  请输入 {preset['display_name']} 的 API Key (输入不回显): "
            )
        except (EOFError, KeyboardInterrupt):
            _print("\n  已取消。")
            raise SystemExit(1)

    if not api_key:
        _print("  ⚠ API Key 不能为空，请重新运行配置。")
        raise SystemExit(1)

    # Step 3: Confirm models
    models = list(preset.get("default_models", []))  # copy
    if models:
        _print(f"\n  预设模型列表 ({len(models)} 个):")
        for m in models:
            _print(f"    - {m['display_name']} ({m['id']}) [{m['cost_tier']}]")

        add_more = _input("\n  是否添加更多模型? (y/N): ").strip().lower()
        if add_more == "y":
            models = _add_custom_models(models, input_fn=_input, print_fn=_print)
    else:
        _print("\n  自定义 Provider，请添加至少一个模型:")
        models = _add_custom_models([], input_fn=_input, print_fn=_print)

    # Step 4: Token budget
    _print("\n  Token 预算设置 (input + output 合计):")
    budget_str = _input("  总预算 (默认 500000): ").strip()
    budget = int(budget_str) if budget_str.isdigit() else 500000

    # Step 5: Write config
    config = {
        "version": 1,
        "default_provider": provider_key,
        "providers": {
            provider_key: {
                "display_name": preset["display_name"],
                "base_url": base_url,
                "api_key": api_key,
                "models": models,
            }
        },
        "token_budgets": {
            "default_total": budget,
            "per_model_limits": {},
        },
    }

    config_path.write_text(
        json.dumps(config, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    # Restrict file permissions: contains API keys
    try:
        config_path.chmod(0o600)
    except OSError:
        pass  # Windows or restricted filesystem — best effort

    _print(f"\n  ✓ 配置已保存到: {config_path}")
    _print(f"  ✓ 默认模型: {models[0]['display_name'] if models else 'N/A'}")
    _print(f"  ✓ Token 预算: {budget:,}")
    _print("")
    _print("=" * 60)
    _print("")

    return config_path


# ============================================================
# Helpers
# ============================================================


def _input_int(
    prompt: str,
    min_val: int,
    max_val: int,
    input_fn=None,
    print_fn=None,
) -> int:
    """Safe integer input with range validation."""
    _input = input_fn or input
    _print = print_fn or print
    while True:
        try:
            val = int(_input(prompt).strip())
            if min_val <= val <= max_val:
                return val
            _print(f"    请输入 {min_val}-{max_val} 之间的数字。")
        except ValueError:
            _print("    请输入数字。")


def _add_custom_models(
    existing: list,
    input_fn=None,
    print_fn=None,
) -> list:
    """Interactive loop to add custom models."""
    _input = input_fn or input
    _print = print_fn or print
    models = list(existing)
    while True:
        model_id = _input("    模型 ID (如 gpt-4o，输入空行结束): ").strip()
        if not model_id:
            break
        display = (
            _input(f"    显示名称 (默认 {model_id}): ").strip() or model_id
        )
        cost = (
            _input("    成本等级 (high/medium/low, 默认 medium): ").strip()
            or "medium"
        )
        tags_str = _input("    标签 (逗号分隔, 如 general,fast): ").strip()
        tags = (
            [t.strip() for t in tags_str.split(",") if t.strip()]
            if tags_str
            else []
        )

        models.append(
            {
                "id": model_id,
                "display_name": display,
                "tags": tags,
                "cost_tier": cost,
            }
        )
        _print(f"    ✓ 已添加: {display}")

    if not models:
        _print("    ⚠ 至少需要一个模型。")
        return _add_custom_models(existing, input_fn=_input, print_fn=_print)
    return models


# ============================================================
# CLI Entry Point (for standalone testing)
# ============================================================

if __name__ == "__main__":
    target_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("config")
    result = run_bootstrap(config_dir=target_dir)
    print(f"\nDone. Config at: {result}")
