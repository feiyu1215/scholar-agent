# 多模型切换系统 — 实现蓝图

> **状态**: ✅ Phase 1-4 全部实施完成  
> **作者**: yanfeiyu03  
> **日期**: 2025-06  
> **关联模块**: `llm/client.py`, `core/harness.py`, `core/loop.py`, `config/`  
> **文档版本**: v2 — 新增 §13-§17 精确实施计划；§18 Phase 4 统一模型分配

---

## 1. 设计目标

在单次会话中，用户可以灵活切换不同的云端部署模型，以适配不同任务的需求。例如：

- 用 `deepseek-r1` 做论文审稿（深度推理）
- 用 `gpt-4.1` 做去 AI 味改写（语言质量）
- 用 `gpt-4.1-mini` 做基于审稿意见的编辑（性价比）

核心原则：**用户主导，Agent 辅助**。用户决定何时切换、切换到哪个模型；Agent 负责执行切换、管理上下文迁移、追踪 token 预算。

---

## 2. 架构总览

```
┌─────────────────────────────────────────────────────────────┐
│                      User Session                            │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌──────────────┐    ┌──────────────────────┐              │
│  │ Bootstrap    │───▶│ config/providers.json │              │
│  │ (首次配置)   │    └──────────────────────┘              │
│  └──────────────┘              │                            │
│                                ▼                            │
│  ┌──────────────────────────────────────────┐              │
│  │         SessionModelManager              │              │
│  │  ┌────────────────────────────────┐      │              │
│  │  │ model_stack: [current, ...]    │      │              │
│  │  │ switch_history: [...]          │      │              │
│  │  │ context_summaries: {model: s}  │      │              │
│  │  └────────────────────────────────┘      │              │
│  └──────────────────┬───────────────────────┘              │
│                     │                                       │
│         ┌───────────┼───────────┐                          │
│         ▼           ▼           ▼                          │
│  ┌────────────┐ ┌────────┐ ┌──────────────┐              │
│  │ LLMClient  │ │Harness │ │TokenBudget   │              │
│  │(with_model │ │(phase  │ │Tracker       │              │
│  │ _override) │ │ FSM)   │ │(per-model)   │              │
│  └────────────┘ └────────┘ └──────────────┘              │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. 核心组件设计

### 3.1 `config/providers.json` — Provider 配置文件

用户自管理的模型列表和 API 密钥配置。首次运行时由 Bootstrap 对话生成。

```jsonc
{
  "version": 1,
  "default_provider": "friday",
  "providers": {
    "friday": {
      "display_name": "美团 Friday One-API",
      "base_url": "https://aigc.sankuai.com/v1/openai/native",
      "api_key": "2003426817264898139",
      "models": [
        {
          "id": "gpt-4.1",
          "display_name": "GPT-4.1",
          "tags": ["general", "writing", "reasoning"],
          "cost_tier": "high"
        },
        {
          "id": "gpt-4.1-mini",
          "display_name": "GPT-4.1 Mini",
          "tags": ["general", "fast"],
          "cost_tier": "low"
        },
        {
          "id": "deepseek-r1-friday",
          "display_name": "DeepSeek R1",
          "tags": ["reasoning", "math", "code"],
          "cost_tier": "high"
        },
        {
          "id": "deepseek-v3-friday",
          "display_name": "DeepSeek V3",
          "tags": ["general", "fast"],
          "cost_tier": "medium"
        },
        {
          "id": "glm-4.5-flash",
          "display_name": "GLM-4.5 Flash",
          "tags": ["chinese", "fast"],
          "cost_tier": "low"
        }
      ]
    }
  },
  "token_budgets": {
    "default_total": 500000,
    "per_model_limits": {}
  }
}
```

**Schema 说明**:

- `providers`: 字典，key 为 provider 标识符。每个 provider 包含 `base_url`、`api_key`、`models` 列表。
- `models[].id`: 传给 API 的实际 model name。
- `models[].display_name`: 展示给用户的友好名称。
- `models[].tags`: 用于 Agent 推荐时的语义标签（可选功能）。
- `models[].cost_tier`: `"high"` / `"medium"` / `"low"`，用于预算估算。
- `token_budgets.default_total`: 会话总 token 预算（input + output 合计）。
- `token_budgets.per_model_limits`: 可选的单模型上限（空 = 不限）。

---

### 3.2 `llm/session_model_manager.py` — 会话模型管理器

这是多模型切换的核心状态机。

```python
"""
llm/session_model_manager.py — Session-level model switching manager.

Responsibilities:
1. Load available models from providers.json
2. Track current active model (model stack)
3. Handle switch requests (validate, migrate context, update client)
4. Maintain switch history for observability
5. Generate context summaries on model switch
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from llm.client import LLMClient


# ============================================================
# Data Types
# ============================================================

@dataclass
class ModelInfo:
    """A single available model."""
    id: str
    display_name: str
    provider: str
    base_url: str
    api_key: str
    tags: list[str] = field(default_factory=list)
    cost_tier: str = "medium"


@dataclass
class SwitchRecord:
    """Record of a model switch event."""
    timestamp: float
    from_model: str
    to_model: str
    reason: str
    context_summary: str
    tokens_used_before_switch: int


@dataclass
class ModelTokenUsage:
    """Per-model token tracking."""
    model_id: str
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


# ============================================================
# Session Model Manager
# ============================================================

class SessionModelManager:
    """
    Manages model switching within a single session.
    
    Lifecycle:
        1. __init__() → loads providers.json, builds available model list
        2. current_model → returns active model info
        3. switch_model(model_id, reason) → validates, generates summary, switches
        4. list_models() → returns available models for user selection
        5. get_budget_status() → returns per-model and total token usage
    """

    CONFIG_PATH = Path("config/providers.json")

    def __init__(self, config_path: Path | None = None):
        self._config_path = config_path or self.CONFIG_PATH
        self._config = self._load_config()
        self._available_models: dict[str, ModelInfo] = self._build_model_registry()
        
        # State
        self._current_model_id: str = self._resolve_default_model()
        self._switch_history: list[SwitchRecord] = []
        self._token_usage: dict[str, ModelTokenUsage] = {}
        self._context_summaries: dict[str, str] = {}  # model_id → last summary

    def _load_config(self) -> dict:
        """Load providers.json. Raises FileNotFoundError if not bootstrapped."""
        path = self._config_path
        if not path.exists():
            raise FileNotFoundError(
                f"Provider config not found at {path}. "
                "Run bootstrap to configure your models."
            )
        return json.loads(path.read_text(encoding="utf-8"))

    def _build_model_registry(self) -> dict[str, ModelInfo]:
        """Flatten all providers' models into a single lookup dict."""
        registry = {}
        for provider_key, provider_cfg in self._config.get("providers", {}).items():
            base_url = provider_cfg["base_url"]
            api_key = provider_cfg["api_key"]
            for model_cfg in provider_cfg.get("models", []):
                model_id = model_cfg["id"]
                registry[model_id] = ModelInfo(
                    id=model_id,
                    display_name=model_cfg.get("display_name", model_id),
                    provider=provider_key,
                    base_url=base_url,
                    api_key=api_key,
                    tags=model_cfg.get("tags", []),
                    cost_tier=model_cfg.get("cost_tier", "medium"),
                )
        return registry

    def _resolve_default_model(self) -> str:
        """Determine initial model: first model of default provider."""
        default_provider = self._config.get("default_provider", "")
        provider_cfg = self._config.get("providers", {}).get(default_provider, {})
        models = provider_cfg.get("models", [])
        if models:
            return models[0]["id"]
        # Fallback: first model in registry
        if self._available_models:
            return next(iter(self._available_models))
        raise ValueError("No models configured in providers.json")

    # ---- Public API ----

    @property
    def current_model(self) -> ModelInfo:
        return self._available_models[self._current_model_id]

    @property
    def current_model_id(self) -> str:
        return self._current_model_id

    def list_models(self) -> list[ModelInfo]:
        """Return all available models for user selection."""
        return list(self._available_models.values())

    def list_models_formatted(self) -> str:
        """Return a user-friendly formatted model list."""
        lines = []
        for i, m in enumerate(self._available_models.values(), 1):
            marker = " ← 当前" if m.id == self._current_model_id else ""
            tags_str = ", ".join(m.tags) if m.tags else ""
            lines.append(
                f"  {i}. {m.display_name} ({m.id}) "
                f"[{m.cost_tier}] {tags_str}{marker}"
            )
        return "\n".join(lines)

    async def switch_model(
        self,
        target_model_id: str,
        reason: str,
        client: LLMClient,
        messages: list[dict],
    ) -> str:
        """
        Execute a model switch.
        
        Steps:
            1. Validate target model exists
            2. Generate context summary from current conversation
            3. Record switch event
            4. Update client's active model
            5. Return confirmation message
        
        Args:
            target_model_id: The model to switch to
            reason: User's reason for switching
            client: The LLMClient instance to update
            messages: Current conversation messages (for summary generation)
        
        Returns:
            Confirmation message string
        """
        # Validate
        if target_model_id not in self._available_models:
            available = ", ".join(self._available_models.keys())
            raise ValueError(
                f"Model '{target_model_id}' not found. Available: {available}"
            )
        if target_model_id == self._current_model_id:
            return f"已经在使用 {self.current_model.display_name}，无需切换。"

        # Generate context summary (using current model before switch)
        summary = await self._generate_context_summary(client, messages)
        
        # Record
        old_model = self._current_model_id
        usage = self._token_usage.get(old_model, ModelTokenUsage(model_id=old_model))
        record = SwitchRecord(
            timestamp=time.time(),
            from_model=old_model,
            to_model=target_model_id,
            reason=reason,
            context_summary=summary,
            tokens_used_before_switch=usage.total,
        )
        self._switch_history.append(record)
        self._context_summaries[old_model] = summary

        # Switch
        self._current_model_id = target_model_id
        target_info = self._available_models[target_model_id]
        
        # Update LLMClient (zero-cost switch via model override)
        client.model = target_model_id
        # If provider changed, need to recreate the underlying AsyncOpenAI client
        old_info = self._available_models[old_model]
        if target_info.base_url != old_info.base_url or target_info.api_key != old_info.api_key:
            from openai import AsyncOpenAI
            client.client = AsyncOpenAI(
                api_key=target_info.api_key,
                base_url=target_info.base_url,
                timeout=client.timeout,
            )

        return (
            f"✓ 已切换: {old_info.display_name} → {target_info.display_name}\n"
            f"  原因: {reason}\n"
            f"  上下文摘要已保存（{len(summary)}字）"
        )

    async def _generate_context_summary(
        self, client: LLMClient, messages: list[dict]
    ) -> str:
        """
        Generate a concise summary of current conversation context.
        Used for context migration when switching models.
        
        Strategy: Take last N messages, ask current model to summarize.
        """
        # Take last 10 messages (or fewer) for summary
        recent = messages[-10:] if len(messages) > 10 else messages
        
        # Build summary prompt
        conversation_text = "\n".join(
            f"[{m.get('role', '?')}]: {(m.get('content', '') or '')[:500]}"
            for m in recent
            if m.get('role') in ('user', 'assistant')
        )
        
        if not conversation_text.strip():
            return "(无对话历史)"

        summary = await client.chat(
            system="你是一个对话摘要助手。请用中文简洁概括以下对话的核心内容、当前任务进展、和关键决策。不超过200字。",
            user=conversation_text,
            temperature=0.0,
            max_tokens=300,
        )
        return summary.strip() or "(摘要生成失败)"

    # ---- Token Tracking ----

    def record_tokens(self, model_id: str, input_tokens: int, output_tokens: int):
        """Record token usage for a specific model."""
        if model_id not in self._token_usage:
            self._token_usage[model_id] = ModelTokenUsage(model_id=model_id)
        usage = self._token_usage[model_id]
        usage.input_tokens += input_tokens
        usage.output_tokens += output_tokens

    def get_total_tokens(self) -> int:
        """Total tokens across all models."""
        return sum(u.total for u in self._token_usage.values())

    def get_budget_status(self) -> str:
        """Return formatted budget status."""
        budget_cfg = self._config.get("token_budgets", {})
        total_limit = budget_cfg.get("default_total", 500000)
        total_used = self.get_total_tokens()
        
        lines = [f"Token 预算: {total_used:,} / {total_limit:,} ({total_used/total_limit*100:.1f}%)"]
        lines.append("各模型用量:")
        for model_id, usage in self._token_usage.items():
            display = self._available_models.get(model_id)
            name = display.display_name if display else model_id
            lines.append(
                f"  {name}: {usage.total:,} "
                f"(in={usage.input_tokens:,}, out={usage.output_tokens:,})"
            )
        return "\n".join(lines)

    def is_budget_exceeded(self) -> bool:
        """Check if total budget is exceeded."""
        budget_cfg = self._config.get("token_budgets", {})
        total_limit = budget_cfg.get("default_total", 500000)
        return self.get_total_tokens() >= total_limit

    # ---- Context Retrieval ----

    def get_last_summary(self, model_id: str | None = None) -> str | None:
        """Retrieve the context summary from the last time a model was active."""
        if model_id:
            return self._context_summaries.get(model_id)
        # Return most recent summary
        if self._switch_history:
            return self._switch_history[-1].context_summary
        return None

    def get_switch_history(self) -> list[SwitchRecord]:
        """Return full switch history for observability."""
        return self._switch_history.copy()

    # ---- Model Management ----

    def add_model(self, provider: str, model_cfg: dict) -> str:
        """
        Dynamically add a model to the registry and persist to config.
        
        Args:
            provider: Provider key (must exist in config)
            model_cfg: {"id": "...", "display_name": "...", "tags": [...], "cost_tier": "..."}
        """
        model_id = model_cfg["id"]
        if model_id in self._available_models:
            return f"模型 {model_id} 已存在。"
        
        provider_cfg = self._config["providers"].get(provider)
        if not provider_cfg:
            return f"Provider '{provider}' 不存在。"
        
        # Add to runtime registry
        self._available_models[model_id] = ModelInfo(
            id=model_id,
            display_name=model_cfg.get("display_name", model_id),
            provider=provider,
            base_url=provider_cfg["base_url"],
            api_key=provider_cfg["api_key"],
            tags=model_cfg.get("tags", []),
            cost_tier=model_cfg.get("cost_tier", "medium"),
        )
        
        # Persist to config
        provider_cfg.setdefault("models", []).append(model_cfg)
        self._save_config()
        return f"✓ 已添加模型: {model_cfg.get('display_name', model_id)}"

    def remove_model(self, model_id: str) -> str:
        """Remove a model from registry and config."""
        if model_id not in self._available_models:
            return f"模型 {model_id} 不存在。"
        if model_id == self._current_model_id:
            return f"不能删除当前正在使用的模型。请先切换到其他模型。"
        
        info = self._available_models.pop(model_id)
        # Remove from config
        provider_cfg = self._config["providers"].get(info.provider, {})
        models = provider_cfg.get("models", [])
        provider_cfg["models"] = [m for m in models if m["id"] != model_id]
        self._save_config()
        return f"✓ 已移除模型: {info.display_name}"

    def _save_config(self):
        """Persist current config to disk."""
        self._config_path.write_text(
            json.dumps(self._config, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
```

---

### 3.3 `llm/bootstrap.py` — 首次配置对话

解决 Bootstrap 问题：首次运行时没有 LLM 可用，通过硬编码模板对话引导用户配置。

```python
"""
llm/bootstrap.py — First-run provider configuration dialog.

No LLM dependency. Pure template-driven interactive setup.
Generates config/providers.json from user input.
"""

from __future__ import annotations

import json
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
            {"id": "gpt-4.1", "display_name": "GPT-4.1", "tags": ["general", "writing", "reasoning"], "cost_tier": "high"},
            {"id": "gpt-4.1-mini", "display_name": "GPT-4.1 Mini", "tags": ["general", "fast"], "cost_tier": "low"},
            {"id": "deepseek-r1-friday", "display_name": "DeepSeek R1", "tags": ["reasoning", "math"], "cost_tier": "high"},
            {"id": "deepseek-v3-friday", "display_name": "DeepSeek V3", "tags": ["general", "fast"], "cost_tier": "medium"},
            {"id": "glm-4.5-flash", "display_name": "GLM-4.5 Flash", "tags": ["chinese", "fast"], "cost_tier": "low"},
        ],
    },
    "openai": {
        "display_name": "OpenAI Official",
        "base_url": "https://api.openai.com/v1",
        "description": "OpenAI 官方 API",
        "default_models": [
            {"id": "gpt-4o", "display_name": "GPT-4o", "tags": ["general", "multimodal"], "cost_tier": "high"},
            {"id": "gpt-4o-mini", "display_name": "GPT-4o Mini", "tags": ["general", "fast"], "cost_tier": "low"},
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

def run_bootstrap(config_dir: Path = Path("config")) -> Path:
    """
    Interactive first-run setup. No LLM needed.
    
    Flow:
        1. Welcome message
        2. Select provider preset (or custom)
        3. Enter API key
        4. Confirm/edit model list
        5. Set token budget
        6. Write config/providers.json
    
    Returns:
        Path to generated config file
    """
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "providers.json"

    print("\n" + "=" * 60)
    print("  ScholarAgent — 模型配置向导")
    print("=" * 60)
    print("\n  首次运行，需要配置你的 LLM 模型。\n")

    # Step 1: Select provider
    print("  可选的 Provider 预设:")
    presets = list(PROVIDER_PRESETS.items())
    for i, (key, preset) in enumerate(presets, 1):
        print(f"    {i}. {preset['display_name']} — {preset['description']}")
    
    choice = _input_int("  选择 (输入数字): ", 1, len(presets))
    provider_key, preset = presets[choice - 1]

    # Step 2: API Key
    if provider_key == "custom":
        base_url = input("  请输入 API Base URL: ").strip()
        preset["base_url"] = base_url
    
    api_key = input(f"  请输入 {preset['display_name']} 的 API Key: ").strip()
    if not api_key:
        print("  ⚠ API Key 不能为空，请重新运行配置。")
        raise SystemExit(1)

    # Step 3: Confirm models
    models = preset["default_models"]
    if models:
        print(f"\n  预设模型列表 ({len(models)} 个):")
        for m in models:
            print(f"    - {m['display_name']} ({m['id']}) [{m['cost_tier']}]")
        
        add_more = input("\n  是否添加更多模型? (y/N): ").strip().lower()
        if add_more == "y":
            models = _add_custom_models(models)
    else:
        print("\n  自定义 Provider，请添加至少一个模型:")
        models = _add_custom_models([])

    # Step 4: Token budget
    print(f"\n  Token 预算设置 (input + output 合计):")
    budget_str = input("  总预算 (默认 500000): ").strip()
    budget = int(budget_str) if budget_str.isdigit() else 500000

    # Step 5: Write config
    config = {
        "version": 1,
        "default_provider": provider_key,
        "providers": {
            provider_key: {
                "display_name": preset["display_name"],
                "base_url": preset["base_url"],
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

    print(f"\n  ✓ 配置已保存到: {config_path}")
    print(f"  ✓ 默认模型: {models[0]['display_name'] if models else 'N/A'}")
    print(f"  ✓ Token 预算: {budget:,}")
    print("\n" + "=" * 60 + "\n")

    return config_path


def _input_int(prompt: str, min_val: int, max_val: int) -> int:
    """Safe integer input with range validation."""
    while True:
        try:
            val = int(input(prompt).strip())
            if min_val <= val <= max_val:
                return val
            print(f"    请输入 {min_val}-{max_val} 之间的数字。")
        except ValueError:
            print(f"    请输入数字。")


def _add_custom_models(existing: list) -> list:
    """Interactive loop to add custom models."""
    models = list(existing)
    while True:
        model_id = input("    模型 ID (如 gpt-4o，输入空行结束): ").strip()
        if not model_id:
            break
        display = input(f"    显示名称 (默认 {model_id}): ").strip() or model_id
        cost = input("    成本等级 (high/medium/low, 默认 medium): ").strip() or "medium"
        tags_str = input("    标签 (逗号分隔, 如 general,fast): ").strip()
        tags = [t.strip() for t in tags_str.split(",") if t.strip()] if tags_str else []
        
        models.append({
            "id": model_id,
            "display_name": display,
            "tags": tags,
            "cost_tier": cost,
        })
        print(f"    ✓ 已添加: {display}")
    
    if not models:
        print("    ⚠ 至少需要一个模型。")
        return _add_custom_models(existing)
    return models
```

---

### 3.4 Token Budget Tracker — 预算追踪集成

**设计决策**: Token 预算以 input + output 合计为总量，内部分别记录用于报告。

修改 `core/harness.py` 中的 `BudgetPolicy` 集成：

```python
# 在 Harness 中集成 SessionModelManager 的 token 追踪

class Harness:
    def __init__(self, ..., session_model_mgr: SessionModelManager | None = None):
        # ... existing init ...
        self._model_mgr = session_model_mgr

    def is_budget_exceeded(self) -> bool:
        """Check budget — delegates to SessionModelManager if available."""
        if self._model_mgr:
            return self._model_mgr.is_budget_exceeded()
        # Fallback to existing budget_policy logic
        return self.state.total_tokens >= self.budget_policy.token_limit
```

**Token 记录时机**: 在 `cognitive_loop` 中每次 LLM 调用返回后：

```python
# core/loop.py — 在 LLM 响应后记录 token
if resp.usage and session_model_mgr:
    session_model_mgr.record_tokens(
        model_id=client.model,
        input_tokens=resp.usage.prompt_tokens,
        output_tokens=resp.usage.completion_tokens,
    )
```

---

### 3.5 Cognitive Loop 集成 — 模型切换信号

在 `core/loop.py` 中新增信号协议：

```python
# 新增信号: "__MODEL__|json"
# Agent 识别到用户想切换模型时发出

# 信号格式:
# __MODEL__|{"action": "switch", "target": "deepseek-r1-friday", "reason": "需要深度推理"}
# __MODEL__|{"action": "list"}
# __MODEL__|{"action": "budget"}
# __MODEL__|{"action": "add", "model": {...}}
# __MODEL__|{"action": "remove", "model_id": "..."}
```

Loop 中的处理逻辑：

```python
async def cognitive_loop(
    messages: list[dict],
    harness: Harness,
    tools: list[dict],
    client: LLMClient,
    session_model_mgr: SessionModelManager | None = None,  # NEW
    verbose: bool = True,
    on_stream: OnStreamCallback = None,
) -> LoopResult:
    # ... existing loop body ...
    
    # 在解析 LLM 输出时，检测 __MODEL__ 信号
    if "__MODEL__|" in content:
        model_result = await _handle_model_signal(
            content, client, messages, session_model_mgr
        )
        # Inject result as system message and continue loop
        messages.append({"role": "system", "content": model_result})
        continue  # Re-enter loop with new model active
```

---

### 3.6 Router 降级为可选建议

现有的 `llm/router.py` 不再强制路由，而是作为"建议"功能：

```python
# llm/router.py — 改造后的角色

class ModelSuggester:
    """
    Optional: suggests models based on task type.
    User can accept or ignore suggestions.
    
    NOT used for automatic routing anymore.
    Only activated when user asks "用哪个模型比较好？"
    """
    
    def suggest(self, task_description: str, available_models: list[ModelInfo]) -> str:
        """Return a suggestion string, not an automatic switch."""
        # Match task keywords to model tags
        # Return formatted suggestion for user to decide
        ...
```

---

## 4. 交互流程

### 4.1 首次运行

```
用户启动 ScholarAgent
    │
    ├─ config/providers.json 不存在?
    │   └─ 运行 bootstrap.run_bootstrap()
    │       ├─ 选择 Provider 预设
    │       ├─ 输入 API Key
    │       ├─ 确认模型列表
    │       ├─ 设置 Token 预算
    │       └─ 生成 config/providers.json
    │
    └─ 正常启动 → SessionModelManager 加载配置
```

### 4.2 会话中切换模型

```
用户: "换成 DeepSeek R1，我需要它来做深度推理审稿"
    │
    ├─ Agent 识别意图 → 发出 __MODEL__ 信号
    │
    ├─ Loop 捕获信号 → 调用 session_model_mgr.switch_model()
    │   ├─ 验证目标模型存在
    │   ├─ 用当前模型生成上下文摘要
    │   ├─ 记录切换事件
    │   ├─ 更新 LLMClient.model (零成本切换)
    │   └─ 如果 provider 不同 → 重建 AsyncOpenAI client
    │
    ├─ 切换确认注入 messages
    │
    └─ 继续 loop（新模型生效）
```

### 4.3 查看预算

```
用户: "token 用了多少了？"
    │
    ├─ Agent → __MODEL__|{"action": "budget"}
    │
    └─ 返回格式化的预算报告:
        Token 预算: 45,230 / 500,000 (9.0%)
        各模型用量:
          GPT-4.1: 32,100 (in=28,000, out=4,100)
          DeepSeek R1: 13,130 (in=11,000, out=2,130)
```

### 4.4 添加新模型

```
用户: "我还有一个 qwen3-max 可以用，帮我加上"
    │
    ├─ Agent → __MODEL__|{"action": "add", "model": {"id": "qwen3-max", ...}}
    │
    ├─ session_model_mgr.add_model() → 更新 registry + 持久化
    │
    └─ 确认: "✓ 已添加模型: Qwen3 Max"
```

---

## 5. 对现有代码的修改清单

### 5.1 新增文件

| 文件 | 职责 |
|------|------|
| `llm/session_model_manager.py` | 会话模型管理器（核心） |
| `llm/bootstrap.py` | 首次配置对话 |
| `config/providers.json` | Provider 配置（运行时生成） |

### 5.2 修改文件

| 文件 | 修改内容 |
|------|----------|
| `core/loop.py` | 新增 `session_model_mgr` 参数；新增 `__MODEL__` 信号处理 |
| `core/harness.py` | 集成 `SessionModelManager` 的 budget 检查 |
| `llm/client.py` | 无需修改（已有 `model` 属性可直接赋值） |
| `llm/router.py` | 降级为 `ModelSuggester`，不再自动路由 |
| `main.py` | 启动时检查 config，必要时运行 bootstrap；初始化 `SessionModelManager` |
| `core/identity.py` | System prompt 中注入可用模型列表和切换指令格式 |

### 5.3 不修改的文件

| 文件 | 原因 |
|------|------|
| `core/mcp_loader.py` | MCP 与模型切换无关 |
| `core/tools.py` | 工具注册不受影响 |
| `core/phases.py` | Phase FSM 独立于模型选择 |
| `.env` | 保留作为 fallback，但优先级低于 `providers.json` |

---

## 6. 上下文迁移策略

### 6.1 切换时

1. **生成摘要**: 用当前模型对最近 10 条消息生成 ≤200 字摘要
2. **保存摘要**: 存入 `SessionModelManager._context_summaries[old_model]`
3. **完整历史保留**: `messages` 列表不清空，新模型可以看到全部历史

### 6.2 按需检索

- 用户/Agent 可以主动请求之前模型的摘要：`"之前 GPT-4.1 审稿时说了什么？"`
- Agent 通过 `session_model_mgr.get_last_summary("gpt-4.1")` 获取

### 6.3 长会话优化

当 messages 过长时，结合现有的 `core/compaction.py` 压缩机制：

```python
# 切换模型时，如果 messages 超过阈值，先做一次 compaction
if len(messages) > COMPACTION_THRESHOLD:
    messages = await compact_messages(messages, client)
```

---

## 7. 错误处理

| 场景 | 处理方式 |
|------|----------|
| 目标模型不存在 | 返回可用模型列表，提示用户重新选择 |
| API Key 无效 | 切换失败，回退到上一个模型，提示用户检查配置 |
| 切换后首次调用失败 | 自动回退 + 通知用户 |
| 预算耗尽 | 阻止切换到 high-cost 模型，建议 low-cost 替代 |
| providers.json 损坏 | 重新运行 bootstrap |
| 摘要生成失败 | 记录空摘要，不阻塞切换 |

---

## 8. 实现优先级

### Phase 1: 最小可用（MVP）

1. `config/providers.json` schema 定义 + 手动创建
2. `SessionModelManager` 核心逻辑（switch, list, budget tracking）
3. `cognitive_loop` 集成 `__MODEL__` 信号
4. `main.py` 初始化流程

### Phase 2: 用户体验

5. `bootstrap.py` 首次配置对话
6. `identity.py` 注入模型列表到 system prompt
7. 上下文摘要生成
8. 格式化的预算报告

### Phase 3: 高级功能

9. `ModelSuggester`（原 router 改造）
10. 动态添加/删除模型
11. 跨 provider 切换（不同 base_url）
12. 切换历史持久化（写入 `.workspace/metrics/`）

### Phase 4: 统一模型分配（详见 §18）

13. `providers.json` v2 schema：`model_assignments` + `tier_models`
14. `SessionModelManager.resolve_model_for_role()` / `resolve_tier_model()` / `_user_override`
15. 各组件改造（子视角路由、MCL、Checker、Consolidation、Reflection）
16. `llm/router.py` 动态 tier 接口
17. `/models` 命令增强（显示分配表）

---

## 9. 测试策略

```python
# tests/test_session_model_manager.py

class TestSessionModelManager:
    """Unit tests for model switching."""
    
    def test_load_config(self):
        """Should load providers.json and build model registry."""
    
    def test_list_models(self):
        """Should return all available models."""
    
    def test_switch_model_success(self):
        """Should update client.model and record switch."""
    
    def test_switch_to_nonexistent_model(self):
        """Should raise ValueError with available models list."""
    
    def test_switch_same_model_noop(self):
        """Should return early with message."""
    
    def test_token_tracking(self):
        """Should accumulate per-model token usage."""
    
    def test_budget_exceeded(self):
        """Should detect when total budget is exceeded."""
    
    def test_cross_provider_switch(self):
        """Should recreate AsyncOpenAI client when base_url changes."""
    
    def test_context_summary_generation(self):
        """Should generate summary from recent messages."""
    
    def test_add_remove_model(self):
        """Should persist changes to providers.json."""


class TestBootstrap:
    """Integration tests for first-run dialog."""
    
    def test_generates_valid_config(self, monkeypatch):
        """Should produce valid providers.json from simulated input."""
    
    def test_custom_provider(self, monkeypatch):
        """Should handle custom base_url input."""


class TestLoopModelSignal:
    """Integration tests for __MODEL__ signal in cognitive_loop."""
    
    def test_switch_signal_triggers_manager(self):
        """Should call session_model_mgr.switch_model()."""
    
    def test_budget_signal_returns_report(self):
        """Should inject budget report into messages."""
```

---

## 10. 配置优先级

从高到低：

1. `SessionModelManager` 运行时状态（用户在会话中的切换）
2. `config/providers.json`（持久化配置）
3. `.env` 文件中的 `LLM_MODEL` 等变量（向后兼容 fallback）
4. 代码中的硬编码默认值

---

## 11. 向后兼容

- 如果 `config/providers.json` 不存在且 `.env` 中有 `OPENAI_API_KEY`，系统以单模型模式运行（行为与当前完全一致）
- `SessionModelManager` 为可选注入，`cognitive_loop` 在 `session_model_mgr=None` 时跳过所有多模型逻辑
- 现有的 `LLMClient.with_model_override()` 上下文管理器保留，作为内部临时切换的工具（如 router 建议的 tier 切换）

---

## 12. 安全考虑

- API Key 存储在本地 `config/providers.json`，不上传 git（需加入 `.gitignore`）
- Bootstrap 对话中 API Key 输入不回显（可选改进：使用 `getpass`）
- Token 预算作为软限制，防止意外消耗过多资源
- 切换历史不包含对话内容，只包含摘要和元数据

---

## 13. 实施执行计划（精确到行号）

> 本节参考 `FULL_CODE_AUDIT_PLAN.md` 的格式，为每个实施步骤提供精确的文件位置、修改清单、风险评级和验收标准。

### 13.1 步骤 1.1：创建 `config/providers.json`

| 属性 | 值 |
|------|-----|
| **文件** | `v2/config/providers.json`（新建） |
| **行数** | ~45 行 |
| **风险** | 低 |
| **依赖** | 无 |
| **阻塞** | 步骤 1.2, 1.6 |

**动作**: 基于当前 `.env` 中的 Friday API 配置生成真实可用的 providers.json

**数据来源**:
- API Key: `.env:1` → `OPENAI_API_KEY=2003426817264898139`
- Base URL: `.env:2` → `OPENAI_BASE_URL=https://aigc.sankuai.com/v1/openai/native`
- 模型列表: `.env` 注释中的 Available 列表 → `gpt-4.1, gpt-4.1-mini, gpt-4o-mini, deepseek-v3-friday, deepseek-r1-friday, glm-4.5-flash`

**修改清单**:
- [x] 创建 `v2/config/` 目录（如不存在）
- [x] 写入 `providers.json`，schema 与 §3.1 一致
- [x] 将 `config/providers.json` 加入 `.gitignore`

**验收标准**: `python -c "import json; json.load(open('config/providers.json'))"` 通过 ✅

---

### 13.2 步骤 1.2：实现 `llm/session_model_manager.py`

| 属性 | 值 |
|------|-----|
| **文件** | `v2/llm/session_model_manager.py`（新建） |
| **预计行数** | ~280 行 |
| **风险** | 中 |
| **依赖** | 步骤 1.1（需要 providers.json schema） |
| **阻塞** | 步骤 1.4, 1.5, 1.6 |

**与现有代码的接口点**:

| 接口 | 文件:行号 | 说明 |
|------|-----------|------|
| `LLMClient.model` 属性 | `llm/client.py:166` | `self.model = model` 可直接赋值 |
| `LLMClient.chat()` 方法 | `llm/client.py:280` | 用于生成上下文摘要 |
| `LLMClient.client` 属性 | `llm/client.py:168` | 跨 provider 时需重建 AsyncOpenAI |
| `LLMClient.timeout` 属性 | `llm/client.py:170` | 重建 client 时保持 timeout |
| Config 路径模式 | `config/__init__.py:16` | `_CONFIG_DIR = Path(__file__).resolve().parent` |

**关键设计决策**:
- 路径解析：使用 `Path` 相对于项目根目录，参考 `config/__init__.py:16` 的模式
- Async 边界：`switch_model` 和 `_generate_context_summary` 为 async（因为需要调用 `client.chat()`）
- 命名风格：与 `llm/client.py` 一致（snake_case, type hints, docstring）
- 不依赖 `core/` 中的任何模块（保持 `llm/` 层的独立性）

**修改清单**:
- [x] 实现 `ModelInfo` / `SwitchRecord` / `ModelTokenUsage` 数据类
- [x] 实现 `SessionModelManager.__init__` + `_load_config` + `_build_model_registry`
- [x] 实现 `switch_model`（含验证、摘要生成、client 更新）
- [x] 实现 `_generate_context_summary`（调用 client.chat）
- [x] 实现 `record_tokens` / `get_total_tokens` / `is_budget_exceeded`
- [x] 实现 `add_model` / `remove_model`（含持久化）
- [x] 实现 `list_models_formatted` / `get_budget_status`

**验收标准**: `from llm.session_model_manager import SessionModelManager` 无报错 ✅

---

### 13.3 步骤 1.3：实现 `llm/bootstrap.py`

| 属性 | 值 |
|------|-----|
| **文件** | `v2/llm/bootstrap.py`（新建） |
| **预计行数** | ~150 行 |
| **风险** | 低 |
| **依赖** | 步骤 1.1（schema 定义） |
| **阻塞** | 步骤 1.6 |

**关键设计决策**:
- 纯 stdin/stdout，无 LLM 依赖
- 使用 `getpass.getpass()` 隐藏 API Key 输入
- `PROVIDER_PRESETS` 硬编码 Friday / OpenAI / Custom 三个预设
- 输出路径：`config_dir / "providers.json"`

**修改清单**:
- [x] 实现 `PROVIDER_PRESETS` 常量
- [x] 实现 `run_bootstrap(config_dir)` 交互式流程
- [x] 实现 `_input_int` / `_add_custom_models` 辅助函数
- [x] 输入验证：API Key 非空、模型列表非空

**验收标准**: `monkeypatch` 模拟输入后能生成合法 JSON ✅

---

### 13.4 步骤 1.4：修改 `core/loop.py` — 新增 `__MODEL__` 信号

| 属性 | 值 |
|------|-----|
| **文件** | `v2/core/loop.py`（修改，1087 行） |
| **修改行数** | ~60 行新增 |
| **风险** | 中高 |
| **依赖** | 步骤 1.2 |
| **阻塞** | 步骤 3.1 |

**精确变更点**:

**变更 A — 函数签名** (第 96-103 行):

当前签名:
```
async def cognitive_loop(
    messages: list[dict],
    harness: Harness,
    tools: list[dict],
    client: LLMClient,
    verbose: bool = True,
    on_stream: OnStreamCallback = None,
) -> LoopResult:
```

改为（末尾新增参数）:
```
async def cognitive_loop(
    messages: list[dict],
    harness: Harness,
    tools: list[dict],
    client: LLMClient,
    verbose: bool = True,
    on_stream: OnStreamCallback = None,
    session_model_mgr=None,  # Optional[SessionModelManager]
) -> LoopResult:
```

**变更 B — Token 记录增强** (第 351 行附近，`harness.state.total_tokens += ...` 之后):

新增:
```python
if session_model_mgr:
    session_model_mgr.record_tokens(
        model_id=client.model,
        input_tokens=usage.get("prompt_tokens", 0),
        output_tokens=usage.get("completion_tokens", 0),
    )
```

**变更 C — 信号处理** (第 581 行 `elif result.startswith("__SWITCH__"):` 块之后):

新增 `elif result.startswith("__MODEL__"):` 分支，调用 `_handle_model_signal` 辅助函数。

**变更 D — 新增辅助函数** (文件末尾):

新增 `async def _handle_model_signal(payload, client, messages, session_model_mgr, verbose) -> str`

**关键约束**:
- `session_model_mgr=None` 默认值确保所有现有调用方无需修改
- 信号解析模式与 `__SWITCH__`（第 581-599 行）完全一致
- import 不在文件顶部（避免循环依赖），通过参数传入

**修改清单**:
- [x] 修改 `cognitive_loop` 签名，新增 `session_model_mgr=None`
- [x] 在 token 记录处新增 `session_model_mgr.record_tokens()` 调用
- [x] 在信号处理链中新增 `__MODEL__` 分支
- [x] 实现 `_handle_model_signal` 辅助函数
- [x] 确认现有 6 种信号（`__DONE__`, `__NUDGE__`, `__TALK__`, `__SPAWN__`, `__SWITCH__`, `__TOOL_ERROR__`）不受影响

**验收标准**: `session_model_mgr=None` 时行为与修改前完全一致；传入 mgr 时信号被正确分发

---

### 13.5 步骤 1.5：修改 `core/harness.py` — 集成 budget 检查

| 属性 | 值 |
|------|-----|
| **文件** | `v2/core/harness.py`（修改，1329 行） |
| **修改行数** | ~10 行 |
| **风险** | 低 |
| **依赖** | 步骤 1.2 |
| **阻塞** | 无 |

**精确变更点**:

**变更 A — `__init__` 签名** (第 162 行):

末尾新增参数 `session_model_mgr=None`

**变更 B — `__init__` body** (第 176 行 `self.enable_hdwm = enable_hdwm` 之后):

新增: `self._session_model_mgr = session_model_mgr`

**变更 C — `is_budget_exceeded` 方法** (找到现有方法):

在方法开头新增条件分支:
```python
if self._session_model_mgr is not None:
    return self._session_model_mgr.is_budget_exceeded()
```

**关键约束**:
- `session_model_mgr=None` 默认值确保所有现有调用方（`agent.py` 中 3 处 Harness 实例化）无需修改
- `is_budget_exceeded` 的 fallback 逻辑保持不变
- 不修改 `state.total_tokens` 的累加逻辑

**修改清单**:
- [x] 修改 `Harness.__init__` 签名
- [x] 新增 `self._session_model_mgr` 属性
- [x] 修改 `is_budget_exceeded` 方法

**验收标准**: `session_model_mgr=None` 时 `is_budget_exceeded()` 行为与修改前完全一致

---

### 13.6 步骤 1.6：修改 `main.py` — 初始化流程 + CLI 命令

| 属性 | 值 |
|------|-----|
| **文件** | `v2/main.py`（修改，348 行） |
| **修改行数** | ~40 行 |
| **风险** | 中 |
| **依赖** | 步骤 1.1, 1.2, 1.3 |
| **阻塞** | 无 |

**精确变更点**:

**变更 A — 初始化** (第 161-191 行 `run_interactive` 函数内):

在 agent 初始化之前，新增 `SessionModelManager` 加载逻辑:
```python
session_model_mgr = None
providers_config = Path(__file__).resolve().parent / "config" / "providers.json"
if providers_config.exists():
    from llm.session_model_manager import SessionModelManager
    try:
        session_model_mgr = SessionModelManager(config_path=providers_config)
    except Exception as e:
        print(f"  多模型: OFF ({e})", file=sys.stderr)
```

**变更 B — CLI 命令** (多轮对话循环中，user_input 处理之前):

新增 `models` / `switch <model>` / `budget` 三个 CLI 命令。

**关键约束**:
- Phase 1 不修改 `agent.py`，通过 CLI 命令实现切换
- Phase 2 再将 `session_model_mgr` 注入到 `cognitive_loop`
- 需要确认 agent 内部属性的访问方式

**修改清单**:
- [x] 新增 `SessionModelManager` 初始化逻辑
- [x] 新增 `models` CLI 命令
- [x] 新增 `switch <model>` CLI 命令
- [x] 新增 `budget` CLI 命令
- [x] 启动时打印多模型状态

**验收标准**: `models`/`switch <model>`/`budget` CLI 命令正常工作

---

### 13.7 步骤 2.1：修改 `core/identity.py` — 注入模型列表（Phase 2）

| 属性 | 值 |
|------|-----|
| **文件** | `v2/core/identity.py`（修改） |
| **修改行数** | ~20 行 |
| **风险** | 低 |
| **依赖** | 步骤 1.2 |
| **阻塞** | 无 |

**动作**: 在 system prompt 构建函数中，当 `session_model_mgr` 可用时追加模型列表和切换指令格式。

**修改清单**:
- [x] 在 `build_system_prompt` 或等效函数中新增 `session_model_mgr` 参数
- [x] 追加可用模型列表文本
- [x] 追加 `__MODEL__` 信号格式说明

**验收标准**: system prompt 中包含模型列表信息

---

### 13.8 步骤 3.1：编写测试

| 属性 | 值 |
|------|-----|
| **文件** | `v2/tests/test_session_model_manager.py`（新建） |
| **预计行数** | ~300 行 |
| **风险** | 低 |
| **依赖** | 步骤 1.2, 1.3, 1.4 |
| **阻塞** | 步骤 3.2 |

**测试矩阵**:

| # | 测试名 | 验证内容 | Mock 策略 |
|---|--------|----------|----------|
| T1 | `test_load_config_success` | 正常加载 providers.json | tmp_path 写入测试 JSON |
| T2 | `test_load_config_missing` | 文件不存在时 raise FileNotFoundError | 不创建文件 |
| T3 | `test_list_models` | 返回所有模型，格式正确 | 测试 config |
| T4 | `test_list_models_formatted` | 当前模型有标记 | 测试 config |
| T5 | `test_switch_model_success` | 切换后 client.model 更新 | Mock LLMClient |
| T6 | `test_switch_nonexistent` | raise ValueError | Mock LLMClient |
| T7 | `test_switch_same_model` | 返回 noop 消息 | Mock LLMClient |
| T8 | `test_token_tracking` | record_tokens 正确累加 | 无 mock |
| T9 | `test_budget_exceeded` | 超出预算时返回 True | 设置低 budget |
| T10 | `test_budget_not_exceeded` | 未超出时返回 False | 默认 budget |
| T11 | `test_add_model` | 添加后 list 包含新模型 | tmp_path config |
| T12 | `test_remove_model` | 删除后 list 不包含 | tmp_path config |
| T13 | `test_remove_current_model_blocked` | 不能删除当前模型 | tmp_path config |
| T14 | `test_cross_provider_switch` | base_url 变化时重建 client | Mock AsyncOpenAI |
| T15 | `test_context_summary_generation` | 生成非空摘要 | Mock client.chat |

**修改清单**:
- [x] 实现 `TestSessionModelManager` 类（T1-T15）
- [x] 实现 `TestBootstrap` 类（monkeypatch 模拟输入）
- [x] 实现 `TestLoopModelSignal` 类（信号分发验证）

**验收标准**: 15+ 个测试全部通过

---

### 13.9 步骤 3.2：全量回归验证

| 属性 | 值 |
|------|-----|
| **动作** | `cd v2 && python -m pytest tests/ -v` |
| **风险** | 低 |
| **依赖** | 所有前置步骤 |

**验收标准**:
- 现有测试全部通过（零回归）
- 新增测试全部通过
- 总计 0 failures

---

## 14. 依赖图

```
步骤 1.1 (providers.json) ─────────────────────────────────────┐
    │                                                           │
    ▼                                                           │
步骤 1.2 (SessionModelManager) ────────────────────┐           │
    │                                               │           │
    ├──▶ 步骤 1.4 (loop.py __MODEL__ 信号)         │           │
    │                                               │           │
    ├──▶ 步骤 1.5 (harness.py budget 集成)         │           │
    │                                               │           │
    └──▶ 步骤 2.1 (identity.py 注入) [Phase 2]     │           │
                                                    │           │
步骤 1.3 (bootstrap.py) ◀──────────────────────────┘───────────┘
    │
    ▼
步骤 1.6 (main.py 初始化 + CLI)
    │
    ▼
步骤 3.1 (测试编写)
    │
    ▼
步骤 3.2 (全量回归验证)
```

**并行可能性**: 步骤 1.2 和 1.3 可并行开发（无互相依赖）。步骤 1.4 和 1.5 可并行（都只依赖 1.2）。

---

## 15. 风险登记表

| # | 风险描述 | 概率 | 影响 | 缓解措施 | 状态 |
|---|----------|------|------|----------|------|
| R1 | `cognitive_loop` 签名变更导致现有调用方 break | 中 | 高 | `session_model_mgr=None` 默认值确保向后兼容；grep 所有调用点确认 | 待验证 |
| R2 | `providers.json` 不存在时系统无法启动 | 高 | 高 | fallback 到 `.env` 单模型模式，`SessionModelManager` 为可选 | 已设计 |
| R3 | 跨 provider 切换时 AsyncOpenAI 重建导致连接池丢失 | 低 | 中 | 仅在 base_url 变化时重建；同 provider 内切换零成本 | 已设计 |
| R4 | 上下文摘要生成失败阻塞切换 | 低 | 中 | try/except 包裹，失败时记录空摘要不阻塞 | 已设计 |
| R5 | Token 追踪与现有 `state.total_tokens` 双重计数 | 中 | 中 | `SessionModelManager` 独立追踪，不修改 `state.total_tokens` 逻辑 | 已设计 |
| R6 | `__MODEL__` 信号与现有信号冲突 | 极低 | 高 | 前缀唯一，解析顺序在现有信号之后 | 已设计 |
| R7 | `client.model` 直接赋值后 `with_model_override` 上下文管理器行为异常 | 低 | 中 | 审查 `with_model_override` 实现（`client.py:461`），确认其使用独立的 `_override` 字段 | 待验证 |
| R8 | `agent.py` 内部属性 `_client` / `_messages` 不可外部访问 | 中 | 中 | Phase 1 通过 CLI 命令绕过；Phase 2 正式注入 | 已设计 |

---

## 16. 验收标准（Definition of Done）

### Phase 1 MVP 验收

- [x] `config/providers.json` 存在且 schema 合法
- [x] `SessionModelManager` 可独立实例化、list_models、switch_model
- [x] `bootstrap.py` 可通过模拟输入生成合法 config
- [x] `cognitive_loop` 接受 `session_model_mgr` 参数，None 时行为不变
- [x] `__MODEL__` 信号被正确解析和分发
- [x] CLI 命令 `models` / `switch` / `budget` 正常工作
- [x] 现有测试全部通过（零回归）
- [x] 新增测试 >= 10 个，覆盖核心路径

### Phase 2 用户体验验收

- [x] 首次运行无 `providers.json` 时自动触发 bootstrap
- [x] System prompt 中包含可用模型列表
- [x] 切换时生成上下文摘要
- [x] `budget` 命令返回格式化报告
- [x] Agent 可通过 `__MODEL__` 信号自主切换（不仅限于 CLI）

### Phase 3 高级功能验收

- [x] 动态添加/删除模型并持久化
- [x] 跨 provider 切换正常工作（重建 AsyncOpenAI client）
- [x] 切换历史写入 `.workspace/metrics/`
- [x] `ModelSuggester` 提供建议但不自动路由

---

## 17. 待确认事项（实施前 Checklist）

在开始编码前，需要确认以下事项：

- [x] `LLMClient.model` 是否为可直接赋值的属性？（已确认：`client.py:166` `self.model = model`）
- [x] `LLMClient.client` 是否为可直接赋值的属性？（已确认：`client.py:168` `self.client = ...`）
- [x] `cognitive_loop` 的所有调用点是否使用关键字参数？（已确认：生产代码全部关键字，测试代码部分位置参数但不影响兼容性）
- [x] `Harness.__init__` 的所有调用点是否使用关键字参数？（已确认：所有传参调用均用关键字）
- [x] `with_model_override` 的实现是否使用独立字段（不与 `self.model` 冲突）？（已确认：通过 `copy.copy()` 浅拷贝隔离，等价效果）
- [x] `agent.py` 中 `ScholarAgent` 是否暴露 `_client` 和 `_messages` 的访问方式？（已确认：`agent.client` + `agent.messages` 公开属性）
- [x] 现有测试数量和通过状态？（已确认：3030 passed）

---

## 18. Phase 4：统一模型分配系统（Unified Model Assignment）

> **状态**: ✅ 实施完成（2025-06）
> **动机**: 当前系统有 14 个 LLM 调用点分布在 8 个文件中，模型配置散落在 5+ 个环境变量里（`LLM_MODEL`, `LLM_MODEL_HIGH`, `LLM_MODEL_MEDIUM`, `LLM_MODEL_LOW`, `MCL_MODEL`, `LLM_MODEL_CHECKER`）。用户无法统一管理，也无法感知哪个组件在用哪个模型。

### 18.1 问题诊断

**现状**：

```
环境变量                    默认值           使用者
─────────────────────────────────────────────────────────
LLM_MODEL                  gpt-4.1-mini    主 Agent 循环
LLM_MODEL_HIGH             = LLM_MODEL     router tier high
LLM_MODEL_MEDIUM           = LLM_MODEL     router tier medium / consolidation
LLM_MODEL_LOW              = LLM_MODEL     router tier low
MCL_MODEL                  gpt-4.1-mini    MetaCognitionLayer
LLM_MODEL_CHECKER          gpt-4.1-mini    CognitiveChecker
(无)                       继承主 client    reflection / meta_reflect / harness hints
(无)                       继承主 client    context_summary (切换时)
```

**问题**：
1. 用户配了 `providers.json` 里 5 个模型，但只有主循环在用，其他组件全 fallback 到 `gpt-4.1-mini`
2. MCL 路由的 `MODEL_TIERS` 从环境变量读，如果没设就三个 tier 全是同一个模型——假功能
3. 用户主动 `switch_model` 后，子视角可能被 MCL 路由覆盖回旧模型——违反用户意图
4. 没有一个地方能让用户一眼看到"哪个组件用哪个模型"

### 18.2 设计方案：`model_assignments` + `tier_models`

在 `providers.json` 中新增两个顶层字段：

```jsonc
{
  "version": 2,
  "default_provider": "friday",
  "providers": { /* ... 现有结构不变 ... */ },
  "token_budgets": { /* ... 现有结构不变 ... */ },

  // ===== 新增 =====

  "model_assignments": {
    "main": "gpt-4.1",
    "sub_perspective": "auto",
    "mcl": "gpt-4.1-mini",
    "checker": "gpt-4.1-mini",
    "consolidation": "gpt-4.1-mini",
    "reflection": "inherit",
    "context_summary": "inherit"
  },

  "tier_models": {
    "high": "gpt-4.1",
    "medium": "gpt-4.1-mini",
    "low": "glm-4.5-flash"
  }
}
```

**字段语义**：

| 字段 | 含义 | 可选值 |
|------|------|--------|
| `model_assignments.main` | 主认知循环使用的模型 | 任何 `providers.models[].id` |
| `model_assignments.sub_perspective` | 子视角循环使用的模型 | 具体 model_id / `"auto"` / `"inherit"` |
| `model_assignments.mcl` | MetaCognitionLayer 使用的模型 | 具体 model_id |
| `model_assignments.checker` | CognitiveChecker 使用的模型 | 具体 model_id |
| `model_assignments.consolidation` | Findings 合并使用的模型 | 具体 model_id / `"inherit"` |
| `model_assignments.reflection` | Session 自省使用的模型 | 具体 model_id / `"inherit"` |
| `model_assignments.context_summary` | 模型切换时摘要生成使用的模型 | 具体 model_id / `"inherit"` |
| `tier_models.high/medium/low` | MCL 路由的三级模型池 | 具体 model_id |

**特殊值**：
- `"inherit"`: 跟随 `main` 的当前值（用户切换主模型时自动跟随）
- `"auto"`: 仅用于 `sub_perspective`，表示由 MCL 按 `tier_models` 自动选择

### 18.3 用户主动切换时的行为规则

```
用户执行 switch_model("deepseek-r1-friday")
    │
    ├─ main = "deepseek-r1-friday"  ← 直接更新
    │
    ├─ 所有 "inherit" 的组件自动跟随:
    │   ├─ reflection → deepseek-r1-friday
    │   └─ context_summary → deepseek-r1-friday
    │
    ├─ "auto" 的组件（sub_perspective）:
    │   └─ 设置 _user_override = True
    │       → 子视角强制使用 deepseek-r1-friday
    │       → MCL 路由被旁路（用户意图优先）
    │
    └─ 显式指定的组件不受影响:
        ├─ mcl 仍然用 gpt-4.1-mini
        ├─ checker 仍然用 gpt-4.1-mini
        └─ consolidation 仍然用 gpt-4.1-mini
```

**核心原则**：
1. 用户主动切换 = 全局意图，`"inherit"` 和 `"auto"` 都跟着走
2. 显式指定的组件（如 MCL 用 mini）不受影响——它们有独立的成本/性能考量
3. 用户可以随时修改 `providers.json` 中任何组件的模型分配

### 18.4 `_user_override` 机制

在 `SessionModelManager` 中新增状态：

```python
class SessionModelManager:
    def __init__(self, ...):
        # ... existing ...
        self._user_override: bool = False  # 用户是否主动切换过模型

    async def switch_model(self, target_model_id, reason, client, messages):
        # ... existing switch logic ...
        self._user_override = True  # 标记：用户主动切换了

    def reset_user_override(self):
        """用户明确说"让系统自动选"时调用。"""
        self._user_override = False

    def resolve_model_for_role(self, role: str) -> str:
        """
        根据 role 解析实际应使用的模型。

        Args:
            role: "main" / "sub_perspective" / "mcl" / "checker" / "consolidation" / "reflection" / "context_summary"

        Returns:
            实际的 model_id
        """
        assignment = self._model_assignments.get(role, "inherit")

        if assignment == "inherit":
            return self._current_model_id

        if assignment == "auto":
            # "auto" 仅用于 sub_perspective
            if self._user_override:
                # 用户主动切换了 → 子视角也跟着用户选的
                return self._current_model_id
            else:
                # 没有用户覆盖 → 返回 None，让 MCL 路由决定
                return None

        # 显式指定的 model_id
        return assignment

    def resolve_tier_model(self, tier: str) -> str:
        """从 tier_models 配置解析具体模型。MCL 路由调用此方法。"""
        return self._tier_models.get(tier, self._current_model_id)
```

### 18.5 各组件的改造清单

#### 18.5.1 子视角路由 (`core/loop.py:830-843`)

**当前**：
```python
from llm.router import MODEL_TIERS
sub_model = MODEL_TIERS.get(tier, client.model)
sub_client = client.with_model_override(sub_model)
```

**改为**：
```python
# 先检查用户是否主动指定了模型
sub_model_resolved = session_model_mgr.resolve_model_for_role("sub_perspective")
if sub_model_resolved is not None:
    # 用户指定或 inherit → 直接用
    sub_model = sub_model_resolved
else:
    # "auto" 且无 user_override → MCL 路由
    tier = tier_map.get(lens, "high")
    sub_model = session_model_mgr.resolve_tier_model(tier)

sub_client = client.with_model_override(sub_model)
```

#### 18.5.2 MetaCognitionLayer (`core/meta_cognition_layer.py:45`)

**当前**：
```python
MCL_MODEL = os.environ.get("MCL_MODEL", "gpt-4.1-mini")
```

**改为**：MCL 初始化时从 `SessionModelManager.resolve_model_for_role("mcl")` 获取模型。
环境变量保留作为 fallback（`SessionModelManager` 不可用时）。

```python
class MetaCognitionLayer:
    def __init__(self, client, session_model_mgr=None, ...):
        if session_model_mgr:
            self._model = session_model_mgr.resolve_model_for_role("mcl")
        else:
            self._model = os.environ.get("MCL_MODEL", "gpt-4.1-mini")
```

#### 18.5.3 CognitiveChecker (`core/checker.py:44`)

**当前**：
```python
CHECKER_MODEL = os.environ.get("LLM_MODEL_CHECKER", "gpt-4.1-mini")
```

**改为**：同 MCL 模式，从 `SessionModelManager` 获取，env 作为 fallback。

```python
class CognitiveChecker:
    def __init__(self, model=None, session_model_mgr=None, ...):
        if session_model_mgr:
            self._model = session_model_mgr.resolve_model_for_role("checker")
        elif model:
            self._model = model
        else:
            self._model = os.environ.get("LLM_MODEL_CHECKER", "gpt-4.1-mini")
```

#### 18.5.4 Consolidation (`core/consolidation.py:181`)

**当前**：
```python
from llm.router import get_model_for_task
model = get_model_for_task("consolidate")
```

**改为**：
```python
if session_model_mgr:
    model = session_model_mgr.resolve_model_for_role("consolidation")
else:
    from llm.router import get_model_for_task
    model = get_model_for_task("consolidate")
```

#### 18.5.5 Reflection / Meta-Reflect

这两个通过回调 `llm_call_fn` 间接使用主 client，当 `model_assignments.reflection = "inherit"` 时行为不变（自动跟随主模型）。

如果用户配置了显式模型（如 `"reflection": "glm-4.5-flash"`），需要在回调构建时做 override：

```python
# agent.py 中构建 reflection 回调时
reflection_model = session_model_mgr.resolve_model_for_role("reflection")
if reflection_model != self.client.model:
    reflection_client = self.client.with_model_override(reflection_model)
    llm_call_fn = reflection_client.chat
else:
    llm_call_fn = self.client.chat
```

#### 18.5.6 `llm/router.py` 的 `MODEL_TIERS` 改造

**当前**：从环境变量读取，import 时固化。

**改为**：`MODEL_TIERS` 保留作为 fallback（无 `SessionModelManager` 时），但新增动态获取路径：

```python
def get_tier_model(tier: str, session_model_mgr=None) -> str:
    """获取指定 tier 的模型。优先从 SessionModelManager 读取。"""
    if session_model_mgr:
        return session_model_mgr.resolve_tier_model(tier)
    return MODEL_TIERS.get(tier, _DEFAULT)
```

### 18.6 向后兼容保证

| 场景 | 行为 |
|------|------|
| 无 `providers.json` | 所有组件从环境变量读取，行为与当前完全一致 |
| 有 `providers.json` 但无 `model_assignments` | 使用默认值：main=第一个模型，其余=inherit |
| 有 `providers.json` 但无 `tier_models` | tier 从环境变量 fallback |
| `SessionModelManager` 为 None | 所有组件走原有逻辑（env var），零影响 |

**默认值策略**（`providers.json` 存在但字段缺失时）：

```python
DEFAULT_MODEL_ASSIGNMENTS = {
    "main": None,              # None = 使用 _resolve_default_model() 的结果
    "sub_perspective": "auto", # 默认让 MCL 路由
    "mcl": "gpt-4.1-mini",    # MCL 始终用轻量模型
    "checker": "gpt-4.1-mini", # Checker 始终用轻量模型
    "consolidation": "inherit", # 跟主模型
    "reflection": "inherit",    # 跟主模型
    "context_summary": "inherit", # 跟主模型
}

DEFAULT_TIER_MODELS = {
    "high": None,   # None = 使用 main 的模型
    "medium": None, # None = 使用 main 的模型
    "low": None,    # None = 使用 main 的模型
}
```

### 18.7 用户可见性：`/models` 命令增强

当用户输入 `models` 命令时，除了显示可用模型列表，还显示当前分配：

```
可用模型:
  1. GPT-4.1 (gpt-4.1) [high] general, writing, reasoning ← 当前主模型
  2. GPT-4.1 Mini (gpt-4.1-mini) [low] general, fast
  3. DeepSeek R1 (deepseek-r1-friday) [high] reasoning, math
  4. DeepSeek V3 (deepseek-v3-friday) [medium] general, fast
  5. GLM-4.5 Flash (glm-4.5-flash) [low] chinese, fast

当前模型分配:
  主循环 (main):          gpt-4.1
  子视角 (sub_perspective): auto (MCL 路由)
  MCL:                    gpt-4.1-mini
  Checker:                gpt-4.1-mini
  Consolidation:          gpt-4.1-mini (inherit → gpt-4.1)
  Reflection:             inherit → gpt-4.1
  上下文摘要:              inherit → gpt-4.1

Tier 模型池 (MCL 路由用):
  high:   gpt-4.1
  medium: gpt-4.1-mini
  low:    glm-4.5-flash
```

### 18.8 实施步骤

| # | 步骤 | 文件 | 风险 | 依赖 |
|---|------|------|------|------|
| 4.1 | `providers.json` schema 升级（v2），新增 `model_assignments` + `tier_models` | `config/providers.json` | 低 | 无 |
| 4.2 | `SessionModelManager` 新增 `resolve_model_for_role()` / `resolve_tier_model()` / `_user_override` | `llm/session_model_manager.py` | 中 | 4.1 |
| 4.3 | 子视角路由改造：优先检查 `resolve_model_for_role("sub_perspective")` | `core/loop.py:830-843, 970-1000` | 中 | 4.2 |
| 4.4 | MCL 初始化改造：从 `SessionModelManager` 获取模型 | `core/meta_cognition_layer.py` | 低 | 4.2 |
| 4.5 | Checker 初始化改造 | `core/checker.py` | 低 | 4.2 |
| 4.6 | Consolidation 模型获取改造 | `core/consolidation.py` + `core/agent.py` | 低 | 4.2 |
| 4.7 | Reflection 回调改造 | `core/agent.py` | 低 | 4.2 |
| 4.8 | `llm/router.py` 新增 `get_tier_model()` 动态接口 | `llm/router.py` | 低 | 4.2 |
| 4.9 | `/models` 命令增强：显示分配表 | `main.py` 或 CLI 层 | 低 | 4.2 |
| 4.10 | 测试：`model_assignments` 解析、`resolve_model_for_role` 各场景、`_user_override` 行为 | `tests/test_session_model_manager.py` | 低 | 4.2-4.8 |
| 4.11 | 全量回归验证 | - | 低 | 4.10 |

### 18.9 依赖图

```
步骤 4.1 (schema 升级)
    │
    ▼
步骤 4.2 (SessionModelManager 扩展)
    │
    ├──▶ 步骤 4.3 (子视角路由)
    ├──▶ 步骤 4.4 (MCL)
    ├──▶ 步骤 4.5 (Checker)
    ├──▶ 步骤 4.6 (Consolidation)
    ├──▶ 步骤 4.7 (Reflection)
    ├──▶ 步骤 4.8 (router.py)
    └──▶ 步骤 4.9 (/models 命令)
              │
              ▼
         步骤 4.10 (测试)
              │
              ▼
         步骤 4.11 (回归)
```

**并行可能性**: 步骤 4.3-4.9 全部只依赖 4.2，可并行开发。

### 18.10 Phase 4 验收标准

- [x] `providers.json` v2 schema 包含 `model_assignments` 和 `tier_models`
- [x] `SessionModelManager.resolve_model_for_role()` 正确解析 `"inherit"` / `"auto"` / 具体 model_id
- [x] 用户 `switch_model` 后，`_user_override=True`，子视角强制跟随
- [x] MCL / Checker / Consolidation 从 `providers.json` 读取模型（env var 作为 fallback）
- [x] 无 `providers.json` 时所有组件行为与当前完全一致（零回归）
- [x] `/models` 命令显示完整的模型分配表
- [x] 新增测试 >= 8 个，覆盖 resolve 逻辑各分支（实际 14 个）
- [x] 全量回归 3030+ 测试通过（实际 3080 passed）

### 18.11 风险登记

| # | 风险 | 概率 | 影响 | 缓解 |
|---|------|------|------|------|
| R9 | 组件初始化顺序：MCL/Checker 在 SessionModelManager 之前创建 | 中 | 中 | 延迟解析：组件存储 mgr 引用，首次调用时才 resolve |
| R10 | `providers.json` 中配了不存在的 model_id | 低 | 中 | `resolve_model_for_role` 做 validate，不存在时 fallback 到 main |
| R11 | 用户切换模型后 MCL 被旁路，子视角全用贵模型导致成本飙升 | 中 | 中 | `/budget` 命令实时显示；切换确认时提示"子视角也将使用此模型" |
| R12 | `tier_models` 中的模型不在 `providers` 列表中 | 低 | 低 | 启动时 validate，不匹配时 warning + fallback |

---

## 19. 全系统 LLM 调用点清单（参考）

> 本节记录所有 LLM 调用点，作为 Phase 4 改造的参考基线。

### 19.1 调用拓扑

```
Agent.start() / .chat()
  ├── pre_generate_cognitive_hints()    → client.chat()         [main / inherit]
  └── cognitive_loop()
        ├── client.chat_with_tools()    → [main, 每轮]
        ├── client.chat_with_tools_stream() → [main, 流式]
        ├── MCL._invoke()               → client.chat()         [mcl]
        ├── MCL.assess_reader_difficulty() → client.chat()      [mcl]
        ├── _run_sub_perspective()       → sub_client           [sub_perspective / auto]
        ├── _run_parallel_perspectives() → sub_client           [sub_perspective / auto]
        └── Checker._async_check()      → client.chat()         [checker]

Agent._consolidate_findings()
  └── consolidation._call_llm_with_retry() → client.chat()     [consolidation]

Agent.end_session_with_reflection()
  ├── SessionReflector.reflect()       → llm_call_fn()          [reflection / inherit]
  └── DeepReflector.reflect()          → llm_call_fn()          [reflection / inherit]

SessionModelManager._generate_context_summary()
  └── client.chat()                    → [context_summary / inherit]
```

### 19.2 调用点详表

| # | 文件:行号 | 组件 | 用途 | model_assignments role | 当前模型来源 |
|---|-----------|------|------|----------------------|-------------|
| 1 | `core/loop.py:308` | 主循环(流式) | 每轮 LLM 推理 | `main` | Agent client.model |
| 2 | `core/loop.py:343` | 主循环(非流式) | 每轮 LLM 推理 | `main` | Agent client.model |
| 3 | `core/loop.py:841` | 子视角(单) | 独立视角审视 | `sub_perspective` | MCL 路由 → MODEL_TIERS[tier] |
| 4 | `core/loop.py:1000` | 子视角(并行) | 多视角并发 | `sub_perspective` | MCL 路由 → MODEL_TIERS[tier] |
| 5 | `core/meta_cognition_layer.py:403` | MCL 判断 | 评估是否可结束 | `mcl` | env MCL_MODEL |
| 6 | `core/meta_cognition_layer.py:619` | MCL 难度评估 | 为子视角选 tier | `mcl` | env MCL_MODEL |
| 7 | `core/checker.py:391` | Checker | 编辑后质量校验 | `checker` | env LLM_MODEL_CHECKER |
| 8 | `core/consolidation.py:319` | Consolidation | Findings 合并 | `consolidation` | router.get_model_for_task("consolidate") |
| 9 | `core/agent.py:297` | 认知策略 | 预生成审稿策略 | `main` (inherit) | Agent client.model |
| 10 | `core/harness.py:528` | Harness hints | 认知策略生成 | `main` (inherit) | 回调 → Agent client |
| 11 | `core/reflection.py:171` | Session 自省 | 会话经验提炼 | `reflection` | 回调 → Agent client |
| 12 | `core/meta_reflect.py:439` | 深度元反思 | 跨 session 反思 | `reflection` | 回调 → Agent client |
| 13 | `llm/session_model_manager.py:318` | 上下文摘要 | 切换时生成摘要 | `context_summary` | 当前活跃模型 |
| 14 | `evaluation/llm_judge.py:122` | LLM Judge | 评测用 | (不纳入) | env EVAL_JUDGE_MODEL |

> 注：#14 (LLM Judge) 属于评测模块，不参与运行时，不纳入 `model_assignments`。

### 19.3 环境变量 → `model_assignments` 映射

| 环境变量 | 对应 role | Phase 4 后优先级 |
|----------|-----------|-----------------|
| `LLM_MODEL` | `main` | providers.json > env > 硬编码 |
| `LLM_MODEL_HIGH` | `tier_models.high` | providers.json > env > main |
| `LLM_MODEL_MEDIUM` | `tier_models.medium` | providers.json > env > main |
| `LLM_MODEL_LOW` | `tier_models.low` | providers.json > env > main |
| `MCL_MODEL` | `mcl` | providers.json > env > "gpt-4.1-mini" |
| `LLM_MODEL_CHECKER` | `checker` | providers.json > env > "gpt-4.1-mini" |
| `EVAL_JUDGE_MODEL` | (不纳入) | 保持独立 |
