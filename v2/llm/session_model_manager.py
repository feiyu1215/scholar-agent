"""
llm/session_model_manager.py — Session-level model switching manager.

Responsibilities:
1. Load available models from config/providers.json
2. Track current active model (model stack)
3. Handle switch requests (validate, migrate context, update client)
4. Maintain switch history for observability
5. Generate context summaries on model switch
6. Per-model token budget tracking
7. Unified model assignment: resolve which model each role should use
8. Tier-based model pool for MCL routing
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llm.client import LLMClient

logger = logging.getLogger(__name__)


# ============================================================
# Constants — Default Assignments
# ============================================================

# Valid roles for model_assignments
VALID_ROLES = frozenset([
    "main", "sub_perspective", "mcl", "checker",
    "consolidation", "reflection", "context_summary",
])

# Default model_assignments when field is missing from config
DEFAULT_MODEL_ASSIGNMENTS: dict[str, str | None] = {
    "main": None,               # None = use _resolve_default_model() result
    "sub_perspective": "auto",  # MCL routing decides
    "mcl": "gpt-4.1-mini",     # lightweight model for meta-cognition
    "checker": "gpt-4.1-mini", # lightweight model for checking
    "consolidation": "inherit", # follow main
    "reflection": "inherit",    # follow main
    "context_summary": "inherit",  # follow main
}

# Default tier_models when field is missing from config
DEFAULT_TIER_MODELS: dict[str, str | None] = {
    "high": None,    # None = use main model
    "medium": None,  # None = use main model
    "low": None,     # None = use main model
}


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

    Thread Safety:
        This class is NOT thread-safe. It is designed to be used within a single
        async session (one event loop, no concurrent mutations).
    """

    def __init__(self, config_path: Path | str):
        """
        Initialize the session model manager.

        Args:
            config_path: Path to providers.json configuration file.

        Raises:
            FileNotFoundError: If config file does not exist.
            ValueError: If no models are configured.
        """
        self._config_path = Path(config_path)
        self._config: dict = self._load_config()
        self._available_models: dict[str, ModelInfo] = self._build_model_registry()

        # State
        self._current_model_id: str = self._resolve_default_model()
        self._switch_history: list[SwitchRecord] = []
        self._token_usage: dict[str, ModelTokenUsage] = {}
        self._context_summaries: dict[str, str] = {}  # model_id → last summary

        # Phase 4: Unified Model Assignment
        self._model_assignments: dict[str, str | None] = self._parse_model_assignments()
        self._tier_models: dict[str, str | None] = self._parse_tier_models()
        self._user_override: bool = False  # True when user manually switches model

        logger.info(
            "SessionModelManager initialized: %d models, default=%s",
            len(self._available_models),
            self._current_model_id,
        )

    # ============================================================
    # Config Loading
    # ============================================================

    def _load_config(self) -> dict:
        """Load providers.json. Raises FileNotFoundError if not bootstrapped."""
        if not self._config_path.exists():
            raise FileNotFoundError(
                f"Provider config not found at {self._config_path}. "
                "Run bootstrap to configure your models."
            )
        return json.loads(self._config_path.read_text(encoding="utf-8"))

    def _build_model_registry(self) -> dict[str, ModelInfo]:
        """Flatten all providers' models into a single lookup dict."""
        registry: dict[str, ModelInfo] = {}
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

    def _parse_model_assignments(self) -> dict[str, str | None]:
        """
        Parse model_assignments from config, merging with defaults.

        Validates that explicitly assigned model_ids exist in the registry.
        Invalid assignments are logged as warnings and fall back to defaults.
        """
        raw = self._config.get("model_assignments", {})
        result = dict(DEFAULT_MODEL_ASSIGNMENTS)  # start with defaults

        for role, value in raw.items():
            if role not in VALID_ROLES:
                logger.warning(
                    "Unknown role '%s' in model_assignments, ignoring.", role
                )
                continue

            # Validate the value
            if value is None or value in ("inherit", "auto"):
                result[role] = value
            elif isinstance(value, str):
                # Explicit model_id — validate it exists
                if value in self._available_models:
                    result[role] = value
                else:
                    logger.warning(
                        "model_assignments.%s references unknown model '%s', "
                        "falling back to default.",
                        role,
                        value,
                    )
                    # Keep the default for this role
            else:
                logger.warning(
                    "model_assignments.%s has invalid type %s, ignoring.",
                    role,
                    type(value).__name__,
                )

        # If main is None, use the resolved default model
        if result["main"] is None:
            result["main"] = self._current_model_id

        return result

    def _parse_tier_models(self) -> dict[str, str | None]:
        """
        Parse tier_models from config, merging with defaults.

        Validates that assigned model_ids exist in the registry.
        Invalid tier models fall back to None (which resolves to main model).
        """
        raw = self._config.get("tier_models", {})
        result = dict(DEFAULT_TIER_MODELS)  # start with defaults

        for tier, model_id in raw.items():
            if tier not in ("high", "medium", "low"):
                logger.warning(
                    "Unknown tier '%s' in tier_models, ignoring.", tier
                )
                continue

            if model_id is None:
                result[tier] = None
            elif isinstance(model_id, str):
                if model_id in self._available_models:
                    result[tier] = model_id
                else:
                    logger.warning(
                        "tier_models.%s references unknown model '%s', "
                        "falling back to main model.",
                        tier,
                        model_id,
                    )
                    result[tier] = None
            else:
                logger.warning(
                    "tier_models.%s has invalid type %s, ignoring.",
                    tier,
                    type(model_id).__name__,
                )

        return result

    # ============================================================
    # Public API — Model Info
    # ============================================================

    @property
    def current_model(self) -> ModelInfo:
        """Get the currently active model info."""
        return self._available_models[self._current_model_id]

    @property
    def current_model_id(self) -> str:
        """Get the currently active model ID string."""
        return self._current_model_id

    def list_models(self) -> list[ModelInfo]:
        """Return all available models."""
        return list(self._available_models.values())

    def list_models_formatted(self) -> str:
        """Return a user-friendly formatted model list."""
        lines = ["可用模型:"]
        for i, m in enumerate(self._available_models.values(), 1):
            marker = " ← 当前" if m.id == self._current_model_id else ""
            tags_str = ", ".join(m.tags) if m.tags else ""
            lines.append(
                f"  {i}. {m.display_name} ({m.id}) "
                f"[{m.cost_tier}] {tags_str}{marker}"
            )
        return "\n".join(lines)

    # ============================================================
    # Public API — Model Switching
    # ============================================================

    async def switch_model(
        self,
        target_model_id: str,
        reason: str,
        client: "LLMClient",
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

        Raises:
            ValueError: If target model does not exist
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

        # Record switch event
        old_model_id = self._current_model_id
        old_info = self._available_models[old_model_id]
        usage = self._token_usage.get(
            old_model_id, ModelTokenUsage(model_id=old_model_id)
        )
        record = SwitchRecord(
            timestamp=time.time(),
            from_model=old_model_id,
            to_model=target_model_id,
            reason=reason,
            context_summary=summary,
            tokens_used_before_switch=usage.total,
        )
        self._switch_history.append(record)
        self._context_summaries[old_model_id] = summary

        # Execute switch
        self._current_model_id = target_model_id
        self._model_assignments["main"] = target_model_id  # Sync: main tracks user's choice
        self._user_override = True  # Mark: user actively chose this model
        target_info = self._available_models[target_model_id]

        # Update LLMClient model field (zero-cost for same provider)
        client.model = target_model_id

        # If provider changed (different base_url or api_key), rebuild the
        # underlying AsyncOpenAI client to point to the new endpoint.
        if (
            target_info.base_url != old_info.base_url
            or target_info.api_key != old_info.api_key
        ):
            from openai import AsyncOpenAI

            client.client = AsyncOpenAI(
                api_key=target_info.api_key,
                base_url=target_info.base_url,
                timeout=client.timeout,
            )
            logger.info(
                "Rebuilt AsyncOpenAI client for cross-provider switch: %s → %s",
                old_info.provider,
                target_info.provider,
            )

        logger.info(
            "Model switched: %s → %s (reason: %s)",
            old_model_id,
            target_model_id,
            reason,
        )

        return (
            f"✓ 已切换: {old_info.display_name} → {target_info.display_name}\n"
            f"  原因: {reason}\n"
            f"  上下文摘要已保存（{len(summary)}字）"
        )

    async def _generate_context_summary(
        self, client: "LLMClient", messages: list[dict]
    ) -> str:
        """
        Generate a concise summary of current conversation context.
        Used for context migration when switching models.

        Strategy: Take last 10 messages, ask current model to summarize.
        Uses resolve_model_for_role("context_summary") to select the model —
        allows users to configure a cheaper model for summary generation.
        On failure, returns a fallback string (never blocks the switch).
        """
        # Take last 10 messages (or fewer) for summary
        recent = messages[-10:] if len(messages) > 10 else messages

        # Build conversation text from user/assistant messages only
        conversation_text = "\n".join(
            f"[{m.get('role', '?')}]: {(m.get('content', '') or '')[:500]}"
            for m in recent
            if m.get("role") in ("user", "assistant")
        )

        if not conversation_text.strip():
            return "(无对话历史)"

        # Phase 4: Use context_summary role model if configured
        summary_model = self.resolve_model_for_role("context_summary")
        if summary_model and summary_model != client.model:
            summary_client = client.with_model_override(summary_model)
        else:
            summary_client = client

        try:
            summary = await summary_client.chat(
                system=(
                    "你是一个对话摘要助手。请用中文简洁概括以下对话的核心内容、"
                    "当前任务进展、和关键决策。不超过200字。"
                ),
                user=conversation_text,
                temperature=0.0,
                max_tokens=300,
            )
            return summary.strip() or "(摘要生成失败)"
        except Exception as e:
            logger.warning("Context summary generation failed: %s", e)
            return f"(摘要生成失败: {type(e).__name__})"

    # ============================================================
    # Public API — Unified Model Assignment (Phase 4)
    # ============================================================

    @property
    def user_override(self) -> bool:
        """Whether the user has manually switched the model in this session."""
        return self._user_override

    def reset_user_override(self):
        """
        Reset the user override flag.

        Called when user explicitly says "let the system decide" or
        when starting a new research phase where MCL routing should resume.
        """
        self._user_override = False
        logger.info("User override reset — MCL routing re-enabled for 'auto' roles.")

    def resolve_model_for_role(self, role: str) -> str | None:
        """
        Resolve the actual model_id for a given role.

        Resolution logic:
            - "inherit": returns current main model (follows user switches)
            - "auto": if _user_override is True, returns current main model;
                      otherwise returns None (caller should use MCL routing)
            - explicit model_id: returns that model_id directly
            - None (main role default): returns current model

        Args:
            role: One of "main", "sub_perspective", "mcl", "checker",
                  "consolidation", "reflection", "context_summary"

        Returns:
            A model_id string, or None (only for "auto" without user_override,
            meaning MCL should decide).

        Raises:
            ValueError: If role is not recognized.
        """
        if role not in VALID_ROLES:
            raise ValueError(
                f"Unknown role '{role}'. Valid roles: {sorted(VALID_ROLES)}"
            )

        assignment = self._model_assignments.get(role, "inherit")

        if assignment is None or assignment == "inherit":
            return self._current_model_id

        if assignment == "auto":
            # "auto" is only meaningful for sub_perspective
            if self._user_override:
                # User actively switched → sub-perspectives follow user's choice
                return self._current_model_id
            else:
                # No user override → return None to signal MCL should route
                return None

        # Explicit model_id — verify it still exists (could have been removed)
        if assignment in self._available_models:
            return assignment

        # Fallback: model was removed after config was parsed
        logger.warning(
            "Role '%s' assigned to '%s' which no longer exists, "
            "falling back to main model.",
            role,
            assignment,
        )
        return self._current_model_id

    def resolve_tier_model(self, tier: str) -> str:
        """
        Resolve the model_id for a given MCL routing tier.

        Used by MCL when it decides which tier to route a sub-perspective to.
        Falls back to current main model if tier is not configured.

        Args:
            tier: One of "high", "medium", "low"

        Returns:
            A model_id string (never None).
        """
        model_id = self._tier_models.get(tier)
        if model_id and model_id in self._available_models:
            return model_id
        # Fallback: use main model
        return self._current_model_id

    def get_model_assignments(self) -> dict[str, str | None]:
        """Return a copy of current model assignments."""
        return dict(self._model_assignments)

    def get_tier_models(self) -> dict[str, str | None]:
        """Return a copy of current tier models config."""
        return dict(self._tier_models)

    def list_assignments_formatted(self) -> str:
        """
        Return a user-friendly formatted model assignment table.

        Shows which model each role is using, resolving "inherit" and "auto"
        to their actual values for clarity.
        """
        role_labels = {
            "main": "主循环 (main)",
            "sub_perspective": "子视角 (sub_perspective)",
            "mcl": "MCL",
            "checker": "Checker",
            "consolidation": "Consolidation",
            "reflection": "Reflection",
            "context_summary": "上下文摘要 (context_summary)",
        }

        lines = ["当前模型分配:"]
        for role in [
            "main", "sub_perspective", "mcl", "checker",
            "consolidation", "reflection", "context_summary",
        ]:
            label = role_labels.get(role, role)
            raw_value = self._model_assignments.get(role, "inherit")
            resolved = self.resolve_model_for_role(role)

            if role == "main":
                # Main always shows the resolved (current) model
                info = self._available_models.get(resolved)
                display = info.display_name if info else resolved
                if self._user_override:
                    display += " (用户切换)"
            elif raw_value == "inherit":
                display = f"inherit → {resolved}"
            elif raw_value == "auto":
                if self._user_override:
                    display = f"auto (用户覆盖 → {resolved})"
                else:
                    display = "auto (MCL 路由)"
            else:
                # Explicit model_id
                info = self._available_models.get(raw_value)
                display = info.display_name if info else raw_value

            lines.append(f"  {label:30s} {display}")

        # Tier models section
        lines.append("")
        lines.append("Tier 模型池 (MCL 路由用):")
        for tier in ("high", "medium", "low"):
            model_id = self.resolve_tier_model(tier)
            info = self._available_models.get(model_id)
            name = info.display_name if info else model_id
            lines.append(f"  {tier:8s} {name} ({model_id})")

        return "\n".join(lines)

    # ============================================================
    # Public API — Token Tracking
    # ============================================================

    def record_tokens(self, model_id: str, input_tokens: int, output_tokens: int):
        """
        Record token usage for a specific model.

        Called by cognitive_loop after each LLM response.
        This is independent of harness.state.total_tokens (no double-counting).
        """
        if model_id not in self._token_usage:
            self._token_usage[model_id] = ModelTokenUsage(model_id=model_id)
        usage = self._token_usage[model_id]
        usage.input_tokens += input_tokens
        usage.output_tokens += output_tokens

    def get_total_tokens(self) -> int:
        """Total tokens across all models in this session."""
        return sum(u.total for u in self._token_usage.values())

    def get_budget_status(self) -> str:
        """Return formatted budget status report."""
        budget_cfg = self._config.get("token_budgets", {})
        total_limit = budget_cfg.get("default_total", 500000)
        total_used = self.get_total_tokens()

        pct = (total_used / total_limit * 100) if total_limit > 0 else 0
        lines = [f"Token 预算: {total_used:,} / {total_limit:,} ({pct:.1f}%)"]

        if self._token_usage:
            lines.append("各模型用量:")
            for model_id, usage in self._token_usage.items():
                info = self._available_models.get(model_id)
                name = info.display_name if info else model_id
                lines.append(
                    f"  {name}: {usage.total:,} "
                    f"(in={usage.input_tokens:,}, out={usage.output_tokens:,})"
                )
        else:
            lines.append("  (尚无用量记录)")

        return "\n".join(lines)

    def is_budget_exceeded(self) -> bool:
        """Check if total session budget is exceeded."""
        budget_cfg = self._config.get("token_budgets", {})
        total_limit = budget_cfg.get("default_total", 500000)
        return self.get_total_tokens() >= total_limit

    # ============================================================
    # Public API — Context Retrieval
    # ============================================================

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

    # ============================================================
    # Public API — Dynamic Model Management
    # ============================================================

    def add_model(self, provider: str, model_cfg: dict) -> str:
        """
        Dynamically add a model to the registry and persist to config.

        Args:
            provider: Provider key (must exist in config)
            model_cfg: {"id": "...", "display_name": "...", "tags": [...], "cost_tier": "..."}

        Returns:
            Confirmation or error message string
        """
        model_id = model_cfg.get("id", "")
        if not model_id:
            return "错误: model_cfg 必须包含 'id' 字段。"
        if model_id in self._available_models:
            return f"模型 {model_id} 已存在。"

        provider_cfg = self._config.get("providers", {}).get(provider)
        if not provider_cfg:
            available_providers = ", ".join(self._config.get("providers", {}).keys())
            return f"Provider '{provider}' 不存在。可用: {available_providers}"

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

        logger.info("Model added: %s to provider %s", model_id, provider)
        return f"✓ 已添加模型: {model_cfg.get('display_name', model_id)}"

    def remove_model(self, model_id: str) -> str:
        """
        Remove a model from registry and config.

        Cannot remove the currently active model.

        Returns:
            Confirmation or error message string
        """
        if model_id not in self._available_models:
            return f"模型 {model_id} 不存在。"
        if model_id == self._current_model_id:
            return "不能删除当前正在使用的模型。请先切换到其他模型。"

        info = self._available_models.pop(model_id)

        # Remove from config
        provider_cfg = self._config.get("providers", {}).get(info.provider, {})
        models = provider_cfg.get("models", [])
        provider_cfg["models"] = [m for m in models if m.get("id") != model_id]
        self._save_config()

        logger.info("Model removed: %s from provider %s", model_id, info.provider)
        return f"✓ 已移除模型: {info.display_name}"

    # ============================================================
    # Internal — Persistence
    # ============================================================

    def _save_config(self):
        """Persist current config to disk."""
        self._config_path.write_text(
            json.dumps(self._config, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # ============================================================
    # Public API — Switch History Persistence
    # ============================================================

    def persist_switch_history(self, metrics_dir: Path | None = None) -> Path | None:
        """
        Persist NEW switch history records to .workspace/metrics/model_switches.jsonl.

        Uses a watermark (_persisted_count) to track how many records have already
        been written. Only appends records added since the last persist call.
        This makes the method safe to call multiple times without duplicating data.

        Each SwitchRecord is written as a single JSON line with fields:
            - timestamp: ISO 8601 formatted time
            - from_model: source model ID
            - to_model: target model ID
            - reason: user-provided reason
            - context_summary: generated context summary
            - tokens_used_before_switch: token count before switch
            - session_id: session identifier for cross-referencing with other metrics

        Args:
            metrics_dir: Override for the metrics directory path.
                Defaults to .workspace/metrics/ relative to cwd.

        Returns:
            Path to the written file, or None if no NEW history to persist.
        """
        # Watermark: only write records we haven't persisted yet
        already_persisted = getattr(self, "_persisted_count", 0)
        new_records = self._switch_history[already_persisted:]

        if not new_records:
            return None

        from datetime import datetime, timezone

        target_dir = metrics_dir or Path(".workspace/metrics")
        target_dir.mkdir(parents=True, exist_ok=True)
        target_file = target_dir / "model_switches.jsonl"

        # Generate a session_id consistent with metrics_export.py conventions
        session_id = getattr(self, "_session_id", None)
        if session_id is None:
            import uuid
            session_id = str(uuid.uuid4())[:8]
            self._session_id = session_id

        with target_file.open("a", encoding="utf-8") as f:
            for record in new_records:
                entry = {
                    "timestamp": datetime.fromtimestamp(
                        record.timestamp, tz=timezone.utc
                    ).isoformat(),
                    "session_id": session_id,
                    "from_model": record.from_model,
                    "to_model": record.to_model,
                    "reason": record.reason,
                    "context_summary": record.context_summary,
                    "tokens_used_before_switch": record.tokens_used_before_switch,
                }
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        # Advance watermark
        self._persisted_count = len(self._switch_history)

        logger.info(
            "Persisted %d new switch records to %s (total: %d)",
            len(new_records),
            target_file,
            self._persisted_count,
        )
        return target_file

    def get_persisted_history(
        self, metrics_dir: Path | None = None
    ) -> list[dict]:
        """
        Read previously persisted switch history from disk.

        Returns:
            List of switch record dicts from the JSONL file.
            Empty list if file does not exist.
        """
        target_dir = metrics_dir or Path(".workspace/metrics")
        target_file = target_dir / "model_switches.jsonl"

        if not target_file.exists():
            return []

        records = []
        for line in target_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    logger.warning("Skipping malformed line in %s", target_file)
        return records
