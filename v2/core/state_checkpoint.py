"""
core/state_checkpoint.py — 状态检查点与恢复 (Infrastructure)

为 WorkspaceState 提供序列化/反序列化/快照/恢复能力。
支持：
  - 保存当前状态到文件（JSON 序列化）
  - 从文件恢复状态（反序列化）
  - 增量快照（只保存变更的 diff）
  - 多检查点管理（保留最近 N 个，自动清理旧的）

使用场景：
  - Phase 切换前自动保存检查点 → 如果新 Phase 失败可回退
  - 审稿中断（网络/crash）后恢复
  - Phase 6 反思建议回退时，恢复到之前的状态
  - Debug：重放审稿过程到特定轮次

设计原则：
  - 序列化必须处理所有字段类型（包括 None, dataclass, custom objects）
  - 恢复后的状态与原始状态行为完全等价
  - 检查点操作是无副作用的（不影响当前审稿进程）
  - 磁盘空间友好：自动清理 + 压缩
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict, fields
from pathlib import Path
from typing import Any, Optional
import json
import time
import hashlib
import gzip

logger = logging.getLogger(__name__)


# ==============================================================
# JSON 序列化辅助
# ==============================================================

def _json_default(obj: Any) -> Any:
    """json.dumps 的 default 处理器，处理 set 等不可序列化类型。"""
    if isinstance(obj, set):
        return list(obj)
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    if hasattr(obj, '__dataclass_fields__'):
        return asdict(obj)
    # 兜底：转字符串
    return f"__unserializable__:{type(obj).__name__}"


# ==============================================================
# 检查点元数据
# ==============================================================

@dataclass
class CheckpointMeta:
    """检查点的元数据"""
    checkpoint_id: str              # 唯一标识
    turn: int                       # 保存时的轮次
    phase: str                      # 保存时的 Phase
    timestamp: float                # 保存时间戳
    description: str = ""           # 描述（如 "before methodology_analysis"）
    state_hash: str = ""            # 状态内容哈希（用于比较）
    size_bytes: int = 0             # 序列化后的字节数


# ==============================================================
# 序列化器
# ==============================================================

class StateSerializer:
    """WorkspaceState 的序列化/反序列化。

    处理 dataclass 中各种类型的字段：
    - 基本类型：直接 JSON
    - None：保留
    - list[dict]：直接 JSON
    - 自定义 dataclass 字段：递归 asdict
    - 不可序列化字段（如 PaperStructureIndex）：标记为 __unserializable__
    """

    # 不尝试序列化的字段（运行时重建）
    SKIP_FIELDS = {
        "paper_structure_index",   # 从论文内容重建
        "paper_cognition_graph",   # 从 findings 重建
        "cognition_graph",         # 从 findings 重建
        "voice_profile",           # 从论文内容重建
        "cognitive_hints",         # 从论文内容重建
    }

    def serialize(self, state: Any) -> dict:
        """将 WorkspaceState 序列化为可 JSON 化的 dict。"""
        if state is None:
            return {}

        result = {}
        for f in fields(state):
            if f.name in self.SKIP_FIELDS:
                continue

            value = getattr(state, f.name)
            result[f.name] = self._serialize_value(value)

        return result

    # 已知的 dataclass 字段名 → 类型映射（用于反序列化恢复原始类型）
    DATACLASS_FIELDS: dict[str, type] = {}

    @classmethod
    def _init_dataclass_fields(cls) -> None:
        """延迟初始化已知 dataclass 字段映射，避免循环导入。"""
        if cls.DATACLASS_FIELDS:
            return
        from core.state import EditPlan, EditStep
        from core.review_checklist import ReviewChecklist, ChecklistItem
        cls.DATACLASS_FIELDS = {
            "edit_plan": EditPlan,
            "review_checklist": ReviewChecklist,
        }
        # 嵌套 dataclass 映射（类 → 子字段中的 dataclass 列表字段）
        cls._NESTED_LIST_FIELDS = {
            EditPlan: {"steps": EditStep},
            ReviewChecklist: {"items": ChecklistItem},
        }
        # 特殊类型恢复：dict[int, set[str]] 等需要类型转换的字段
        cls._SPECIAL_RESTORE_FIELDS = {
            ReviewChecklist: {
                "_match_keywords": cls._restore_match_keywords,
            },
        }

    @staticmethod
    def _restore_match_keywords(value: Any) -> dict[int, set[str]]:
        """恢复 _match_keywords: JSON dict[str, list[str]] → dict[int, set[str]]"""
        if not isinstance(value, dict):
            return {}
        result = {}
        for k, v in value.items():
            try:
                idx = int(k)
            except (ValueError, TypeError):
                continue
            if isinstance(v, list):
                result[idx] = set(v)
            elif isinstance(v, set):
                result[idx] = v
            else:
                result[idx] = set()
        return result

    def deserialize(self, data: dict, state_class: type) -> Any:
        """从 dict 反序列化为 WorkspaceState 实例。

        只恢复可序列化的字段，跳过的字段保持默认值。
        对已知的 dataclass 字段（如 EditPlan）会正确恢复为 dataclass 实例。
        """
        self._init_dataclass_fields()

        kwargs = {}
        for f in fields(state_class):
            if f.name in self.SKIP_FIELDS:
                continue
            if f.name in data:
                kwargs[f.name] = self._deserialize_value(data[f.name], f.name)

        return state_class(**kwargs)

    def _serialize_value(self, value: Any) -> Any:
        """递归序列化一个值。"""
        if value is None:
            return None
        if isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, set):
            return [self._serialize_value(item) for item in sorted(value, key=str)]
        if isinstance(value, (list, tuple)):
            return [self._serialize_value(item) for item in value]
        if isinstance(value, dict):
            return {str(k): self._serialize_value(v) for k, v in value.items()}
        if hasattr(value, '__dataclass_fields__'):
            # 是 dataclass → 递归序列化每个字段（比 asdict 更安全，正确处理 set 等类型）
            try:
                result = {}
                for f in fields(value):
                    result[f.name] = self._serialize_value(getattr(value, f.name))
                return result
            except (TypeError, RecursionError) as e:
                logger.debug("Cannot serialize value of type %s: %s", type(value).__name__, e)
                return str(value)
        # 不可序列化 → 转字符串
        return f"__unserializable__:{type(value).__name__}"

    def _deserialize_value(self, value: Any, field_name: str) -> Any:
        """反序列化一个值。

        对已知的 dataclass 字段恢复为正确类型，其他基本类型直通。
        """
        if value is None:
            return None
        if isinstance(value, str) and value.startswith("__unserializable__"):
            return None

        # 检查是否是已知的 dataclass 字段
        if field_name in self.DATACLASS_FIELDS and isinstance(value, dict):
            return self._reconstruct_dataclass(value, self.DATACLASS_FIELDS[field_name])

        return value

    def _reconstruct_dataclass(self, data: dict, dc_class: type) -> Any:
        """从 dict 重建 dataclass 实例（递归处理嵌套）。"""
        try:
            nested_list_fields = getattr(self, '_NESTED_LIST_FIELDS', {}).get(dc_class, {})
            special_fields = getattr(self, '_SPECIAL_RESTORE_FIELDS', {}).get(dc_class, {})
            kwargs = {}
            for f in fields(dc_class):
                if f.name not in data:
                    continue
                val = data[f.name]
                # 特殊类型恢复（如 dict[int, set[str]]）
                if f.name in special_fields:
                    kwargs[f.name] = special_fields[f.name](val)
                # 如果该字段是嵌套 dataclass 列表
                elif f.name in nested_list_fields and isinstance(val, list):
                    nested_class = nested_list_fields[f.name]
                    kwargs[f.name] = [
                        self._reconstruct_dataclass(item, nested_class)
                        if isinstance(item, dict) else item
                        for item in val
                    ]
                else:
                    kwargs[f.name] = val
            return dc_class(**kwargs)
        except (TypeError, KeyError) as e:
            # 如果重建失败，返回原始 dict 作为降级
            logger.warning("Failed to reconstruct dataclass %s: %s, returning raw dict", dc_class.__name__, e)
            return data


# ==============================================================
# 检查点管理器
# ==============================================================

class CheckpointManager:
    """管理 WorkspaceState 的检查点。

    使用方式：
        manager = CheckpointManager(workdir="/path/to/checkpoints")

        # 保存检查点
        meta = manager.save(state, turn=5, phase="methodology_analysis")

        # 列出检查点
        checkpoints = manager.list_checkpoints()

        # 恢复
        state = manager.restore(checkpoint_id)

        # 恢复到特定轮次
        state = manager.restore_to_turn(turn=3)
    """

    def __init__(
        self,
        workdir: str | Path = ".checkpoints",
        max_checkpoints: int = 10,
        compress: bool = True,
    ):
        self.workdir = Path(workdir)
        self.max_checkpoints = max_checkpoints
        self.compress = compress
        self._serializer = StateSerializer()
        self._meta_registry: list[CheckpointMeta] = []

        # 确保工作目录存在
        self.workdir.mkdir(parents=True, exist_ok=True)
        self._load_registry()

    # ----------------------------------------------------------
    # 公开接口
    # ----------------------------------------------------------

    def save(
        self,
        state: Any,
        turn: int = 0,
        phase: str = "",
        description: str = "",
    ) -> CheckpointMeta:
        """保存当前状态为检查点。

        Args:
            state: WorkspaceState 实例
            turn: 当前轮次
            phase: 当前 Phase
            description: 描述

        Returns:
            CheckpointMeta 元数据
        """
        # 序列化
        serialized = self._serializer.serialize(state)
        json_str = json.dumps(serialized, ensure_ascii=False, indent=None, default=_json_default)
        state_hash = hashlib.md5(json_str.encode()).hexdigest()[:16]

        # 生成 ID
        checkpoint_id = f"ckpt_{turn:04d}_{int(time.time())}_{state_hash[:8]}"

        # 写入文件
        if self.compress:
            filepath = self.workdir / f"{checkpoint_id}.json.gz"
            with gzip.open(filepath, 'wt', encoding='utf-8') as f:
                f.write(json_str)
        else:
            filepath = self.workdir / f"{checkpoint_id}.json"
            filepath.write_text(json_str, encoding='utf-8')

        # 创建元数据
        meta = CheckpointMeta(
            checkpoint_id=checkpoint_id,
            turn=turn,
            phase=phase,
            timestamp=time.time(),
            description=description,
            state_hash=state_hash,
            size_bytes=filepath.stat().st_size,
        )

        # 注册并清理
        self._meta_registry.append(meta)
        self._save_registry()
        self._cleanup_old()

        return meta

    def restore(self, checkpoint_id: str, state_class: type | None = None) -> Any:
        """从检查点恢复状态。

        Args:
            checkpoint_id: 检查点 ID
            state_class: 状态类（默认从 core.state 导入 WorkspaceState）

        Returns:
            恢复的 WorkspaceState 实例
        """
        if state_class is None:
            from core.state import WorkspaceState
            state_class = WorkspaceState

        # 读取文件
        data = self._read_checkpoint_file(checkpoint_id)
        if data is None:
            raise FileNotFoundError(f"Checkpoint '{checkpoint_id}' not found")

        return self._serializer.deserialize(data, state_class)

    def restore_to_turn(self, turn: int, state_class: type | None = None) -> Any:
        """恢复到指定轮次的最近检查点。"""
        candidates = [m for m in self._meta_registry if m.turn <= turn]
        if not candidates:
            raise ValueError(f"No checkpoint found at or before turn {turn}")

        # 选最接近的
        best = max(candidates, key=lambda m: m.turn)
        return self.restore(best.checkpoint_id, state_class)

    def list_checkpoints(self) -> list[CheckpointMeta]:
        """列出所有检查点（按时间排序）。"""
        return sorted(self._meta_registry, key=lambda m: m.timestamp)

    def get_latest(self) -> CheckpointMeta | None:
        """获取最新检查点的元数据。"""
        if not self._meta_registry:
            return None
        return max(self._meta_registry, key=lambda m: m.timestamp)

    def delete(self, checkpoint_id: str) -> bool:
        """删除一个检查点。"""
        # 找到并删除文件
        for ext in (".json.gz", ".json"):
            filepath = self.workdir / f"{checkpoint_id}{ext}"
            if filepath.exists():
                filepath.unlink()
                break

        # 从注册表移除
        self._meta_registry = [
            m for m in self._meta_registry if m.checkpoint_id != checkpoint_id
        ]
        self._save_registry()
        return True

    def clear_all(self) -> int:
        """清除所有检查点。返回删除数量。"""
        count = len(self._meta_registry)
        for meta in self._meta_registry:
            for ext in (".json.gz", ".json"):
                filepath = self.workdir / f"{meta.checkpoint_id}{ext}"
                if filepath.exists():
                    filepath.unlink()
        self._meta_registry.clear()
        self._save_registry()
        return count

    # ----------------------------------------------------------
    # 增量快照（diff-based）
    # ----------------------------------------------------------

    def save_diff(
        self,
        state: Any,
        base_checkpoint_id: str,
        turn: int = 0,
        phase: str = "",
    ) -> CheckpointMeta | None:
        """保存相对于 base 的增量差异。

        如果状态未变化（hash 相同），跳过保存。

        Returns:
            CheckpointMeta 或 None（如果未变化）
        """
        serialized = self._serializer.serialize(state)
        json_str = json.dumps(serialized, ensure_ascii=False, default=_json_default)
        current_hash = hashlib.md5(json_str.encode()).hexdigest()[:16]

        # 检查是否与 base 相同
        base_meta = next(
            (m for m in self._meta_registry if m.checkpoint_id == base_checkpoint_id),
            None
        )
        if base_meta and base_meta.state_hash == current_hash:
            return None  # 未变化，跳过

        # 有变化 → 保存完整快照（diff 实现留给 Ideal 层）
        return self.save(state, turn=turn, phase=phase, description=f"diff from {base_checkpoint_id}")

    # ----------------------------------------------------------
    # 内部方法
    # ----------------------------------------------------------

    def _read_checkpoint_file(self, checkpoint_id: str) -> dict | None:
        """读取检查点文件。损坏的 gzip/json 文件不会导致 crash。"""
        # 尝试压缩和非压缩格式
        gz_path = self.workdir / f"{checkpoint_id}.json.gz"
        json_path = self.workdir / f"{checkpoint_id}.json"

        try:
            if gz_path.exists():
                with gzip.open(gz_path, 'rt', encoding='utf-8') as f:
                    return json.loads(f.read())
            elif json_path.exists():
                return json.loads(json_path.read_text(encoding='utf-8'))
        except (gzip.BadGzipFile, OSError, json.JSONDecodeError) as e:
            logger.warning(
                "Checkpoint file corrupted or unreadable: %s — %s",
                checkpoint_id, e,
            )
        return None

    def _cleanup_old(self) -> None:
        """清理超出数量限制的旧检查点。"""
        # Full Snapshot（snap_ 前缀）不受自动清理影响
        regular_metas = [
            m for m in self._meta_registry
            if not m.checkpoint_id.startswith("snap_")
        ]
        if len(regular_metas) <= self.max_checkpoints:
            return

        # 按时间排序，删最旧的（仅限普通检查点）
        sorted_metas = sorted(regular_metas, key=lambda m: m.timestamp)
        to_remove = sorted_metas[:len(regular_metas) - self.max_checkpoints]

        for meta in to_remove:
            for ext in (".json.gz", ".json"):
                filepath = self.workdir / f"{meta.checkpoint_id}{ext}"
                if filepath.exists():
                    filepath.unlink()

        removed_ids = {m.checkpoint_id for m in to_remove}
        self._meta_registry = [
            m for m in self._meta_registry if m.checkpoint_id not in removed_ids
        ]
        self._save_registry()

    def _load_registry(self) -> None:
        """从磁盘加载检查点注册表。逐条容错，单条损坏不影响其他记录。"""
        registry_path = self.workdir / "_registry.json"
        if registry_path.exists():
            try:
                data = json.loads(registry_path.read_text(encoding='utf-8'))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to load checkpoint meta registry: %s", e)
                self._meta_registry = []
                return
            loaded = []
            for item in data:
                try:
                    loaded.append(CheckpointMeta(**item))
                except (TypeError, KeyError) as e:
                    logger.warning("Skipping corrupted registry entry: %s", e)
            self._meta_registry = loaded

    def _save_registry(self) -> None:
        """保存检查点注册表到磁盘。"""
        registry_path = self.workdir / "_registry.json"
        data = [
            {
                "checkpoint_id": m.checkpoint_id,
                "turn": m.turn,
                "phase": m.phase,
                "timestamp": m.timestamp,
                "description": m.description,
                "state_hash": m.state_hash,
                "size_bytes": m.size_bytes,
            }
            for m in self._meta_registry
        ]
        registry_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding='utf-8'
        )

    # ----------------------------------------------------------
    # Full Snapshot（断点续传用）
    # ----------------------------------------------------------

    def save_full_snapshot(
        self,
        state: Any,
        messages: list[dict],
        phase: str,
        phase_history: list[str],
        transition_count: int,
        budget_policy_data: dict,
        stop_reason: str,
        paper_path: str,
        model: str,
        persona: str,
    ) -> CheckpointMeta:
        """保存完整运行快照（用于断点续传）。

        保存 WorkspaceState + messages + Phase FSM 状态 + BudgetPolicy，
        使得后续可以通过 restore_full_snapshot() 恢复到截断前的完整状态。

        Args:
            state: WorkspaceState 实例
            messages: 完整对话历史 list[dict]
            phase: 当前 Phase 枚举值（如 "deep_review"）
            phase_history: Phase 转换历史列表
            transition_count: Phase 转换次数
            budget_policy_data: BudgetPolicy 的序列化 dict
            stop_reason: 停止原因描述
            paper_path: 论文文件路径（resume 时需要重新加载）
            model: 使用的 LLM 模型名
            persona: 人格名称

        Returns:
            CheckpointMeta 元数据
        """
        # 序列化 WorkspaceState
        serialized_state = self._serializer.serialize(state)

        # 组装完整快照
        snapshot = {
            "__snapshot_version__": 1,
            "state": serialized_state,
            "messages": messages,
            "phase": phase,
            "phase_history": phase_history,
            "transition_count": transition_count,
            "budget_policy": budget_policy_data,
            "stop_reason": stop_reason,
            "paper_path": paper_path,
            "model": model,
            "persona": persona,
            "timestamp": time.time(),
        }

        json_str = json.dumps(snapshot, ensure_ascii=False, indent=None, default=_json_default)
        state_hash = hashlib.md5(json_str.encode()).hexdigest()[:16]

        # 生成 ID（使用 "snap_" 前缀区分普通 checkpoint）
        turn = getattr(state, 'loop_turns', 0) if state else 0
        checkpoint_id = f"snap_{turn:04d}_{int(time.time())}_{state_hash[:8]}"

        # 写入文件（始终压缩——snapshot 通常较大）
        filepath = self.workdir / f"{checkpoint_id}.json.gz"
        with gzip.open(filepath, 'wt', encoding='utf-8') as f:
            f.write(json_str)

        # 创建元数据
        meta = CheckpointMeta(
            checkpoint_id=checkpoint_id,
            turn=turn,
            phase=phase,
            timestamp=time.time(),
            description=f"full_snapshot: {stop_reason[:50]}",
            state_hash=state_hash,
            size_bytes=filepath.stat().st_size,
        )

        # 注册（snapshot 不受 max_checkpoints 清理影响——手动管理）
        self._meta_registry.append(meta)
        self._save_registry()

        logger.info(
            "Saved full snapshot: %s (size=%d bytes, messages=%d)",
            checkpoint_id, meta.size_bytes, len(messages),
        )
        return meta

    def restore_full_snapshot(
        self,
        checkpoint_id: str | None = None,
    ) -> "FullSnapshot":
        """恢复完整快照。

        Args:
            checkpoint_id: 快照 ID。不传时恢复最新的 snapshot。

        Returns:
            FullSnapshot 数据对象

        Raises:
            FileNotFoundError: 找不到指定的 snapshot
            ValueError: 文件格式不是 full snapshot
        """
        if checkpoint_id is None:
            # 找最新的 snap_ 开头的 checkpoint
            snap_metas = [
                m for m in self._meta_registry
                if m.checkpoint_id.startswith("snap_")
            ]
            if not snap_metas:
                raise FileNotFoundError("No full snapshot found")
            latest = max(snap_metas, key=lambda m: m.timestamp)
            checkpoint_id = latest.checkpoint_id

        data = self._read_checkpoint_file(checkpoint_id)
        if data is None:
            raise FileNotFoundError(f"Snapshot '{checkpoint_id}' not found on disk")

        if "__snapshot_version__" not in data:
            raise ValueError(
                f"Checkpoint '{checkpoint_id}' is not a full snapshot (missing version marker)"
            )

        return FullSnapshot(
            state=data["state"],
            messages=data["messages"],
            phase=data["phase"],
            phase_history=data.get("phase_history", []),
            transition_count=data.get("transition_count", 0),
            budget_policy=data.get("budget_policy", {}),
            stop_reason=data.get("stop_reason", ""),
            timestamp=data.get("timestamp", 0.0),
            paper_path=data.get("paper_path", ""),
            model=data.get("model", ""),
            persona=data.get("persona", "scholar"),
        )

    def get_latest_snapshot_id(self) -> str | None:
        """获取最新 full snapshot 的 ID，如果没有则返回 None。"""
        snap_metas = [
            m for m in self._meta_registry
            if m.checkpoint_id.startswith("snap_")
        ]
        if not snap_metas:
            return None
        return max(snap_metas, key=lambda m: m.timestamp).checkpoint_id


# ==============================================================
# Full Snapshot 数据对象
# ==============================================================

@dataclass
class FullSnapshot:
    """完整运行快照（断点续传用）。

    包含恢复 Agent 运行状态所需的全部信息。
    """
    state: dict
    """WorkspaceState 序列化后的 dict"""

    messages: list[dict]
    """完整对话历史"""

    phase: str
    """当前 Phase 枚举值（如 "deep_review"）"""

    phase_history: list[str]
    """Phase 转换历史"""

    transition_count: int
    """Phase 转换次数"""

    budget_policy: dict
    """BudgetPolicy 序列化 dict"""

    stop_reason: str
    """停止原因描述"""

    timestamp: float
    """保存时间"""

    paper_path: str
    """论文路径"""

    model: str
    """LLM 模型名"""

    persona: str
    """人格名称"""
