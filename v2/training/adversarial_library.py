"""
training/adversarial_library.py — 对抗样本库 (Adversarial Library)

持久化的 "考试题库"：存储所有生成过的对抗样本，支持按多种维度检索、
版本管理、质量分层、以及自动回归测试套件生成。

核心组件:
    1. LibraryEntry — 题库条目（Case + 元数据 + 使用历史 + 版本）
    2. LibraryIndex — 多维索引（按维度/难度/挑战类型/通过率/质量分的快速检索）
    3. AdversarialLibrary — 核心题库管理器（CRUD + 查询 + 持久化 + 统计）
    4. RegressionSuiteGenerator — 回归测试套件生成器（从题库中选取代表性 cases）

设计原则:
    - 持久化: JSON 文件存储，支持增量保存（append-only log + periodic compaction）
    - 高效检索: 内存索引 + 惰性加载，万级 case 也能毫秒级查询
    - 质量分层: 区分 verified/unverified/deprecated cases
    - 版本管理: 每个 case 可以有多个版本（同核心缺陷的不同表面形式）
    - 回归测试: 自动选取能覆盖所有维度/难度的最小代表集
    - 统计洞察: 提供题库整体的覆盖度、有效性、使用频率分析
    - Kill Switch 守卫: OFF 时所有写操作变为 no-op，读操作返回空

Kill Switch: SCHOLAR_GODEL_ADVERSARIAL_TRAINING
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from training.weakness_analyzer import WeaknessDimension
from training.adversarial import (
    AdversarialCase,
    ChallengeType,
    DifficultyLevel,
)

from core.godel_config import GODEL_ADVERSARIAL_TRAINING_ENABLED

logger = logging.getLogger(__name__)


# ==============================================================
# Kill Switch (delegate to godel_config — single source of truth)
# ==============================================================

ADVERSARIAL_TRAINING_ENABLED: bool = GODEL_ADVERSARIAL_TRAINING_ENABLED
"""Backward-compatible alias. Actual source of truth is core.godel_config."""


# ==============================================================
# 条目状态
# ==============================================================

class EntryStatus(str, Enum):
    """题库条目状态。"""
    ACTIVE = "active"
    """正常可用。"""

    VERIFIED = "verified"
    """经过质量验证（人工或自动验证 gold label 正确性）。"""

    DEPRECATED = "deprecated"
    """已废弃（gold label 有误或样本质量差）。"""

    RETIRED = "retired"
    """已退役（Agent 已完全掌握，不再有挑战价值）。"""

    QUARANTINED = "quarantined"
    """隔离中（疑似有问题，待人工审核）。"""


# ==============================================================
# 题库条目
# ==============================================================

@dataclass
class LibraryEntry:
    """题库中的一个条目——包装 AdversarialCase 并附加管理信息。

    一个 Entry 可以包含同一核心缺陷的多个版本（不同表面形式），
    用于防止 Agent 通过记忆匹配而非真正理解来通过测试。
    """
    entry_id: str = ""
    status: EntryStatus = EntryStatus.ACTIVE

    # 核心 Case
    case: AdversarialCase = field(default_factory=AdversarialCase)

    # 版本管理
    versions: list[AdversarialCase] = field(default_factory=list)
    """同核心缺陷的不同表面形式版本。versions[0] = 原始版本。"""

    current_version: int = 0
    """当前激活的版本索引。"""

    # 使用历史
    total_uses: int = 0
    total_passes: int = 0
    last_used: float = 0.0
    first_used: float = 0.0

    # 历史通过率（时间序列）
    pass_rate_history: list[tuple[float, float]] = field(default_factory=list)
    """(timestamp, pass_rate) 序列，用于追踪随训练的变化。"""

    # 质量评估
    quality_score: float = 0.0
    """质量分 (0~1): 综合考虑 gold label 清晰度、难度适当性、区分度。"""

    quality_notes: list[str] = field(default_factory=list)
    """质量相关备注。"""

    verified_by: str = ""
    """验证者（人工验证时记录）。"""

    # 标签/分组
    tags: list[str] = field(default_factory=list)
    """自定义标签（如 "regression_critical", "new_pattern"）。"""

    collection: str = ""
    """所属集合/专题（如 "DID_methodology", "data_consistency"）。"""

    # 时间戳
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def __post_init__(self):
        if not self.entry_id:
            content = f"entry_{self.case.case_id}_{self.created_at}"
            self.entry_id = hashlib.md5(content.encode()).hexdigest()[:16]
        if not self.versions and self.case.paper_snippet:
            self.versions.append(self.case)

    @property
    def pass_rate(self) -> float:
        """整体通过率。"""
        if self.total_uses == 0:
            return -1.0
        return self.total_passes / self.total_uses

    @property
    def is_effective(self) -> bool:
        """是否仍然有效（Agent 通过率 < 80%）。"""
        return self.pass_rate < 0.8 if self.total_uses > 0 else True

    @property
    def is_retired_candidate(self) -> bool:
        """是否应该退役（Agent 完全掌握）。"""
        return self.total_uses >= 5 and self.pass_rate >= 0.95

    @property
    def discrimination_power(self) -> float:
        """区分度: 通过率越接近 0.5 区分度越高。"""
        if self.total_uses == 0:
            return 0.5
        return 1.0 - abs(self.pass_rate - 0.5) * 2

    def record_usage(self, passed: bool, timestamp: Optional[float] = None) -> None:
        """记录一次使用。"""
        ts = timestamp or time.time()
        self.total_uses += 1
        if passed:
            self.total_passes += 1
        self.last_used = ts
        if self.first_used == 0.0:
            self.first_used = ts
        self.updated_at = ts

        # 更新 case 的 pass_rate
        self.case.record_usage(passed)

        # 记录历史
        self.pass_rate_history.append((ts, self.pass_rate))

    def add_version(self, variant: AdversarialCase) -> int:
        """添加新版本。Returns: 新版本索引。"""
        variant.parent_case_id = self.case.case_id
        self.versions.append(variant)
        self.updated_at = time.time()
        return len(self.versions) - 1

    def switch_version(self, version_idx: int) -> None:
        """切换当前激活版本。"""
        if 0 <= version_idx < len(self.versions):
            self.current_version = version_idx
            self.case = self.versions[version_idx]
            self.updated_at = time.time()

    def deprecate(self, reason: str = "") -> None:
        """废弃此条目。"""
        self.status = EntryStatus.DEPRECATED
        if reason:
            self.quality_notes.append(f"Deprecated: {reason}")
        self.updated_at = time.time()

    def retire(self) -> None:
        """退役此条目（Agent 已完全掌握）。"""
        self.status = EntryStatus.RETIRED
        self.quality_notes.append(f"Retired at pass_rate={self.pass_rate:.2f}")
        self.updated_at = time.time()

    def verify(self, verified_by: str = "auto") -> None:
        """标记为已验证。"""
        self.status = EntryStatus.VERIFIED
        self.verified_by = verified_by
        self.updated_at = time.time()

    def quarantine(self, reason: str = "") -> None:
        """隔离此条目。"""
        self.status = EntryStatus.QUARANTINED
        if reason:
            self.quality_notes.append(f"Quarantined: {reason}")
        self.updated_at = time.time()

    def to_dict(self) -> dict:
        return {
            "entry_id": self.entry_id,
            "status": self.status.value,
            "case": self._case_to_dict(self.case),
            "versions": [self._case_to_dict(v) for v in self.versions],
            "current_version": self.current_version,
            "total_uses": self.total_uses,
            "total_passes": self.total_passes,
            "last_used": self.last_used,
            "first_used": self.first_used,
            "pass_rate_history": self.pass_rate_history[-50:],
            "quality_score": self.quality_score,
            "quality_notes": self.quality_notes,
            "verified_by": self.verified_by,
            "tags": self.tags,
            "collection": self.collection,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @staticmethod
    def _case_to_dict(case: AdversarialCase) -> dict:
        """将 AdversarialCase 序列化为 dict。"""
        return {
            "case_id": case.case_id,
            "paper_snippet": case.paper_snippet,
            "gold_findings": case.gold_findings,
            "gold_explanation": case.gold_explanation,
            "challenge_type": case.challenge_type.value,
            "difficulty": case.difficulty.value,
            "target_dimension": case.target_dimension.value,
            "source_weakness_id": case.source_weakness_id,
            "generated_from_failure": case.generated_from_failure,
            "parent_case_id": case.parent_case_id,
            "quality_verified": case.quality_verified,
            "agent_pass_rate": case.agent_pass_rate,
            "created_at": case.created_at,
            "last_used": case.last_used,
            "use_count": case.use_count,
        }

    @classmethod
    def _case_from_dict(cls, data: dict) -> AdversarialCase:
        """从 dict 恢复 AdversarialCase。"""
        return AdversarialCase(
            case_id=data.get("case_id", ""),
            paper_snippet=data.get("paper_snippet", ""),
            gold_findings=data.get("gold_findings", []),
            gold_explanation=data.get("gold_explanation", ""),
            challenge_type=ChallengeType(data.get("challenge_type", "hidden_endogeneity")),
            difficulty=DifficultyLevel(data.get("difficulty", "medium")),
            target_dimension=WeaknessDimension(data.get("target_dimension", "methodology_analysis")),
            source_weakness_id=data.get("source_weakness_id", ""),
            generated_from_failure=data.get("generated_from_failure", False),
            parent_case_id=data.get("parent_case_id", ""),
            quality_verified=data.get("quality_verified", False),
            agent_pass_rate=data.get("agent_pass_rate", -1.0),
            created_at=data.get("created_at", time.time()),
            last_used=data.get("last_used", 0.0),
            use_count=data.get("use_count", 0),
        )

    @classmethod
    def from_dict(cls, data: dict) -> "LibraryEntry":
        case = cls._case_from_dict(data.get("case", {}))
        versions = [cls._case_from_dict(v) for v in data.get("versions", [])]
        if not versions:
            versions = [case]

        return cls(
            entry_id=data.get("entry_id", ""),
            status=EntryStatus(data.get("status", "active")),
            case=case,
            versions=versions,
            current_version=data.get("current_version", 0),
            total_uses=data.get("total_uses", 0),
            total_passes=data.get("total_passes", 0),
            last_used=data.get("last_used", 0.0),
            first_used=data.get("first_used", 0.0),
            pass_rate_history=data.get("pass_rate_history", []),
            quality_score=data.get("quality_score", 0.0),
            quality_notes=data.get("quality_notes", []),
            verified_by=data.get("verified_by", ""),
            tags=data.get("tags", []),
            collection=data.get("collection", ""),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
        )


# ==============================================================
# 多维索引
# ==============================================================

class LibraryIndex:
    """题库的多维度快速索引。

    维护多个倒排索引，支持毫秒级多条件查询:
        - by_dimension: {dimension_value → set(entry_id)}
        - by_difficulty: {difficulty_value → set(entry_id)}
        - by_challenge_type: {challenge_type_value → set(entry_id)}
        - by_status: {status_value → set(entry_id)}
        - by_pass_rate_bucket: {"0-20"/"20-40"/... → set(entry_id)}
        - by_collection: {collection_name → set(entry_id)}
        - by_tag: {tag → set(entry_id)}
    """

    def __init__(self):
        self._by_dimension: dict[str, set[str]] = defaultdict(set)
        self._by_difficulty: dict[str, set[str]] = defaultdict(set)
        self._by_challenge_type: dict[str, set[str]] = defaultdict(set)
        self._by_status: dict[str, set[str]] = defaultdict(set)
        self._by_pass_rate_bucket: dict[str, set[str]] = defaultdict(set)
        self._by_collection: dict[str, set[str]] = defaultdict(set)
        self._by_tag: dict[str, set[str]] = defaultdict(set)

    def add_entry(self, entry: LibraryEntry) -> None:
        """将条目加入索引。"""
        eid = entry.entry_id
        self._by_dimension[entry.case.target_dimension.value].add(eid)
        self._by_difficulty[entry.case.difficulty.value].add(eid)
        self._by_challenge_type[entry.case.challenge_type.value].add(eid)
        self._by_status[entry.status.value].add(eid)
        self._by_pass_rate_bucket[self._rate_to_bucket(entry.pass_rate)].add(eid)
        if entry.collection:
            self._by_collection[entry.collection].add(eid)
        for tag in entry.tags:
            self._by_tag[tag].add(eid)

    def remove_entry(self, entry: LibraryEntry) -> None:
        """从索引中移除条目。"""
        eid = entry.entry_id
        for idx in (
            self._by_dimension, self._by_difficulty, self._by_challenge_type,
            self._by_status, self._by_pass_rate_bucket, self._by_collection,
            self._by_tag,
        ):
            for bucket in idx.values():
                bucket.discard(eid)

    def update_entry(self, entry: LibraryEntry) -> None:
        """更新条目的索引（先删后加）。"""
        self.remove_entry(entry)
        self.add_entry(entry)

    def query(
        self,
        dimension: Optional[WeaknessDimension] = None,
        difficulty: Optional[DifficultyLevel] = None,
        challenge_type: Optional[ChallengeType] = None,
        status: Optional[EntryStatus] = None,
        pass_rate_max: Optional[float] = None,
        collection: Optional[str] = None,
        tag: Optional[str] = None,
    ) -> set[str]:
        """多条件查询，返回满足所有条件的 entry_id 集合。"""
        result_sets: list[set[str]] = []

        if dimension is not None:
            result_sets.append(set(self._by_dimension.get(dimension.value, set())))
        if difficulty is not None:
            result_sets.append(set(self._by_difficulty.get(difficulty.value, set())))
        if challenge_type is not None:
            result_sets.append(set(self._by_challenge_type.get(challenge_type.value, set())))
        if status is not None:
            result_sets.append(set(self._by_status.get(status.value, set())))
        if collection is not None:
            result_sets.append(set(self._by_collection.get(collection, set())))
        if tag is not None:
            result_sets.append(set(self._by_tag.get(tag, set())))
        if pass_rate_max is not None:
            matching: set[str] = set()
            for bucket, ids in self._by_pass_rate_bucket.items():
                bucket_upper = self._bucket_upper_bound(bucket)
                if bucket_upper <= pass_rate_max:
                    matching.update(ids)
            # 也包含 "unevaluated" 的（pass_rate < 0 的视为待测试）
            matching.update(self._by_pass_rate_bucket.get("unevaluated", set()))
            result_sets.append(matching)

        if not result_sets:
            all_ids: set[str] = set()
            for ids in self._by_status.values():
                all_ids.update(ids)
            return all_ids

        result = result_sets[0].copy()
        for s in result_sets[1:]:
            result.intersection_update(s)
        return result

    def get_coverage_stats(self) -> dict[str, dict[str, int]]:
        """获取覆盖统计。"""
        return {
            "by_dimension": {k: len(v) for k, v in self._by_dimension.items()},
            "by_difficulty": {k: len(v) for k, v in self._by_difficulty.items()},
            "by_challenge_type": {k: len(v) for k, v in self._by_challenge_type.items()},
            "by_status": {k: len(v) for k, v in self._by_status.items()},
            "by_pass_rate_bucket": {k: len(v) for k, v in self._by_pass_rate_bucket.items()},
        }

    def rebuild_from_entries(self, entries: dict[str, LibraryEntry]) -> None:
        """从条目字典完全重建索引。"""
        self._by_dimension.clear()
        self._by_difficulty.clear()
        self._by_challenge_type.clear()
        self._by_status.clear()
        self._by_pass_rate_bucket.clear()
        self._by_collection.clear()
        self._by_tag.clear()
        for entry in entries.values():
            self.add_entry(entry)

    @staticmethod
    def _rate_to_bucket(rate: float) -> str:
        """通过率 → 桶名。"""
        if rate < 0:
            return "unevaluated"
        if rate < 0.2:
            return "0-20"
        elif rate < 0.4:
            return "20-40"
        elif rate < 0.6:
            return "40-60"
        elif rate < 0.8:
            return "60-80"
        else:
            return "80-100"

    @staticmethod
    def _bucket_upper_bound(bucket: str) -> float:
        """桶名 → 上界。"""
        bounds = {
            "unevaluated": 0.0,
            "0-20": 0.2,
            "20-40": 0.4,
            "40-60": 0.6,
            "60-80": 0.8,
            "80-100": 1.0,
        }
        return bounds.get(bucket, 1.0)


# ==============================================================
# 核心题库管理器
# ==============================================================

class AdversarialLibrary:
    """对抗样本题库管理器。

    Usage:
        lib = AdversarialLibrary(storage_dir="./data/adversarial_library")
        lib.load()

        # 添加样本
        entry = lib.add_case(case)

        # 查询
        hard_cases = lib.query(difficulty=DifficultyLevel.HARD, pass_rate_max=0.3)

        # 使用后记录结果
        lib.record_result(entry.entry_id, passed=False)

        # 保存
        lib.save()

        # 生成回归套件
        generator = RegressionSuiteGenerator(lib)
        suite = generator.generate(max_size=20)
    """

    def __init__(
        self,
        storage_dir: Optional[str] = None,
        auto_retire_threshold: float = 0.95,
        auto_retire_min_uses: int = 10,
        max_entries: int = 10000,
    ):
        """初始化题库。

        Args:
            storage_dir: 存储目录（None = 内存模式，不持久化）
            auto_retire_threshold: 自动退役阈值（通过率 >= 此值 → 退役）
            auto_retire_min_uses: 自动退役最低使用次数
            max_entries: 最大条目数（超过时淘汰低质量条目）
        """
        self._storage_dir = Path(storage_dir) if storage_dir else None
        self._auto_retire_threshold = auto_retire_threshold
        self._auto_retire_min_uses = auto_retire_min_uses
        self._max_entries = max_entries

        # 内存存储
        self._entries: dict[str, LibraryEntry] = {}
        self._index: LibraryIndex = LibraryIndex()

        # 统计
        self._total_queries: int = 0
        self._total_adds: int = 0
        self._created_at: float = time.time()

        # 脏标记（用于增量保存）
        self._dirty_entries: set[str] = set()

    @property
    def size(self) -> int:
        """当前条目总数。"""
        return len(self._entries)

    @property
    def active_count(self) -> int:
        """活跃条目数。"""
        return sum(
            1 for e in self._entries.values()
            if e.status in (EntryStatus.ACTIVE, EntryStatus.VERIFIED)
        )

    # ----------------------------------------------------------
    # CRUD 操作
    # ----------------------------------------------------------

    def add_case(
        self,
        case: AdversarialCase,
        tags: Optional[list[str]] = None,
        collection: str = "",
        quality_score: float = 0.5,
    ) -> Optional[LibraryEntry]:
        """添加一个对抗样本到题库。

        Returns:
            创建的 LibraryEntry，如果 Kill Switch OFF 返回 None。
        """
        if not ADVERSARIAL_TRAINING_ENABLED:
            return None

        # 去重检查
        existing = self._find_duplicate(case)
        if existing:
            logger.debug("Duplicate case detected, adding as version to %s", existing.entry_id)
            existing.add_version(case)
            self._index.update_entry(existing)
            self._dirty_entries.add(existing.entry_id)
            return existing

        # 创建新条目
        entry = LibraryEntry(
            case=case,
            tags=tags or [],
            collection=collection,
            quality_score=quality_score,
        )

        # 容量检查
        if len(self._entries) >= self._max_entries:
            self._evict_low_quality()

        self._entries[entry.entry_id] = entry
        self._index.add_entry(entry)
        self._dirty_entries.add(entry.entry_id)
        self._total_adds += 1

        logger.debug(
            "Added case to library: %s (dim=%s, diff=%s)",
            entry.entry_id, case.target_dimension.value, case.difficulty.value,
        )
        return entry

    def add_cases_batch(
        self,
        cases: list[AdversarialCase],
        collection: str = "",
    ) -> list[LibraryEntry]:
        """批量添加。"""
        entries: list[LibraryEntry] = []
        for case in cases:
            entry = self.add_case(case, collection=collection)
            if entry:
                entries.append(entry)
        return entries

    def get_entry(self, entry_id: str) -> Optional[LibraryEntry]:
        """按 ID 获取条目。"""
        return self._entries.get(entry_id)

    def get_case(self, case_id: str) -> Optional[AdversarialCase]:
        """按 case_id 获取 AdversarialCase。"""
        for entry in self._entries.values():
            if entry.case.case_id == case_id:
                return entry.case
        return None

    def get_entry_by_case_id(self, case_id: str) -> Optional[LibraryEntry]:
        """按 case_id 获取 LibraryEntry。"""
        for entry in self._entries.values():
            if entry.case.case_id == case_id:
                return entry
        return None

    def remove_entry(self, entry_id: str) -> bool:
        """移除条目。"""
        entry = self._entries.pop(entry_id, None)
        if entry:
            self._index.remove_entry(entry)
            self._dirty_entries.discard(entry_id)
            return True
        return False

    def update_status(self, entry_id: str, status: EntryStatus) -> bool:
        """更新条目状态。"""
        entry = self._entries.get(entry_id)
        if not entry:
            return False
        entry.status = status
        entry.updated_at = time.time()
        self._index.update_entry(entry)
        self._dirty_entries.add(entry_id)
        return True

    # ----------------------------------------------------------
    # 记录使用结果
    # ----------------------------------------------------------

    def record_result(self, entry_id: str, passed: bool) -> bool:
        """记录一次使用结果。

        自动触发退役检查。
        """
        entry = self._entries.get(entry_id)
        if not entry:
            return False

        entry.record_usage(passed)
        self._index.update_entry(entry)
        self._dirty_entries.add(entry_id)

        # 自动退役检查
        if (entry.total_uses >= self._auto_retire_min_uses
                and entry.pass_rate >= self._auto_retire_threshold):
            entry.retire()
            self._index.update_entry(entry)
            logger.info("Auto-retired entry %s (pass_rate=%.2f)", entry_id, entry.pass_rate)

        return True

    def record_result_by_case_id(self, case_id: str, passed: bool) -> bool:
        """按 case_id 记录使用结果。"""
        entry = self.get_entry_by_case_id(case_id)
        if entry:
            return self.record_result(entry.entry_id, passed)
        return False

    # ----------------------------------------------------------
    # 查询
    # ----------------------------------------------------------

    def query(
        self,
        dimension: Optional[WeaknessDimension] = None,
        difficulty: Optional[DifficultyLevel] = None,
        challenge_type: Optional[ChallengeType] = None,
        status: Optional[EntryStatus] = None,
        pass_rate_max: Optional[float] = None,
        collection: Optional[str] = None,
        tag: Optional[str] = None,
        limit: int = 100,
        sort_by: str = "pass_rate",
    ) -> list[LibraryEntry]:
        """多条件查询。

        Args:
            sort_by: 排序字段 ("pass_rate", "quality_score", "last_used",
                     "created_at", "discrimination")
            limit: 最多返回条数

        Returns:
            满足条件的条目列表（排序后）。
        """
        self._total_queries += 1

        entry_ids = self._index.query(
            dimension=dimension,
            difficulty=difficulty,
            challenge_type=challenge_type,
            status=status,
            pass_rate_max=pass_rate_max,
            collection=collection,
            tag=tag,
        )

        entries = [self._entries[eid] for eid in entry_ids if eid in self._entries]

        # 排序
        sort_keys = {
            "pass_rate": lambda e: e.pass_rate if e.pass_rate >= 0 else 2.0,
            "quality_score": lambda e: -e.quality_score,
            "last_used": lambda e: -e.last_used,
            "created_at": lambda e: -e.created_at,
            "discrimination": lambda e: -e.discrimination_power,
        }
        key_fn = sort_keys.get(sort_by, sort_keys["pass_rate"])
        entries.sort(key=key_fn)

        return entries[:limit]

    def get_effective_challenges(
        self,
        dimension: Optional[WeaknessDimension] = None,
        min_uses: int = 2,
        max_pass_rate: float = 0.7,
        limit: int = 50,
    ) -> list[LibraryEntry]:
        """获取仍然有效的挑战（Agent 尚未掌握的）。"""
        candidates: list[LibraryEntry] = []
        for entry in self._entries.values():
            if entry.status not in (EntryStatus.ACTIVE, EntryStatus.VERIFIED):
                continue
            if entry.total_uses < min_uses:
                continue
            if entry.pass_rate > max_pass_rate:
                continue
            if dimension and entry.case.target_dimension != dimension:
                continue
            candidates.append(entry)

        candidates.sort(key=lambda e: e.pass_rate)
        return candidates[:limit]

    def select_regression_suite(
        self,
        max_cases: int = 50,
        dimension: Optional[WeaknessDimension] = None,
        difficulty: Optional[DifficultyLevel] = None,
    ) -> list[LibraryEntry]:
        """直接从库中选取回归测试子集。

        优先选取: verified > 高区分度 > 最近失败 > 覆盖多维度。
        """
        candidates: list[LibraryEntry] = []
        for entry in self._entries.values():
            if entry.status not in (EntryStatus.ACTIVE, EntryStatus.VERIFIED):
                continue
            if entry.total_uses == 0:
                continue
            if dimension and entry.case.target_dimension != dimension:
                continue
            if difficulty and entry.case.difficulty != difficulty:
                continue
            candidates.append(entry)

        # 排分
        def score(e: LibraryEntry) -> float:
            s = e.discrimination_power * 0.4
            if e.status == EntryStatus.VERIFIED:
                s += 0.2
            if e.pass_rate < 0.5:
                s += 0.2
            # 近期使用加分
            days_since = (time.time() - e.last_used) / 86400 if e.last_used > 0 else 60
            s += max(0, 0.2 - days_since / 300)
            return s

        candidates.sort(key=score, reverse=True)
        return candidates[:max_cases]

    # ----------------------------------------------------------
    # 持久化
    # ----------------------------------------------------------

    def save(self) -> None:
        """保存题库到磁盘。"""
        if not self._storage_dir:
            return
        if not ADVERSARIAL_TRAINING_ENABLED:
            return

        self._storage_dir.mkdir(parents=True, exist_ok=True)

        # 完整保存（定期 compaction）
        data = {
            "metadata": {
                "total_entries": len(self._entries),
                "created_at": self._created_at,
                "saved_at": time.time(),
                "total_adds": self._total_adds,
                "total_queries": self._total_queries,
            },
            "entries": {eid: entry.to_dict() for eid, entry in self._entries.items()},
        }

        main_file = self._storage_dir / "library.json"
        # 先写临时文件再原子重命名
        tmp_file = self._storage_dir / "library.json.tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp_file.replace(main_file)

        self._dirty_entries.clear()
        logger.info("Library saved: %d entries to %s", len(self._entries), main_file)

    def save_incremental(self) -> None:
        """增量保存（只保存脏条目到 append log）。"""
        if not self._storage_dir or not self._dirty_entries:
            return
        if not ADVERSARIAL_TRAINING_ENABLED:
            return

        self._storage_dir.mkdir(parents=True, exist_ok=True)
        log_file = self._storage_dir / "library_append.jsonl"

        with open(log_file, "a", encoding="utf-8") as f:
            for eid in self._dirty_entries:
                entry = self._entries.get(eid)
                if entry:
                    record = {"op": "upsert", "entry": entry.to_dict(), "ts": time.time()}
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")

        self._dirty_entries.clear()

    def load(self) -> int:
        """从磁盘加载题库。

        Returns:
            加载的条目数。
        """
        if not self._storage_dir:
            return 0

        main_file = self._storage_dir / "library.json"
        if not main_file.exists():
            return 0

        try:
            with open(main_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.error("Failed to load library: %s", e)
            return 0

        entries_data = data.get("entries", {})
        for eid, entry_dict in entries_data.items():
            entry = LibraryEntry.from_dict(entry_dict)
            self._entries[entry.entry_id] = entry

        # 重建索引
        self._index.rebuild_from_entries(self._entries)

        # 应用 append log（如果存在）
        self._apply_append_log()

        metadata = data.get("metadata", {})
        self._total_adds = metadata.get("total_adds", 0)
        self._total_queries = metadata.get("total_queries", 0)
        self._created_at = metadata.get("created_at", time.time())

        logger.info("Library loaded: %d entries from %s", len(self._entries), main_file)
        return len(self._entries)

    def _apply_append_log(self) -> None:
        """应用增量日志。"""
        if not self._storage_dir:
            return
        log_file = self._storage_dir / "library_append.jsonl"
        if not log_file.exists():
            return

        applied = 0
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    record = json.loads(line)
                    if record.get("op") == "upsert":
                        entry = LibraryEntry.from_dict(record["entry"])
                        self._entries[entry.entry_id] = entry
                        applied += 1
        except (json.JSONDecodeError, IOError) as e:
            logger.warning("Error applying append log: %s", e)

        if applied > 0:
            self._index.rebuild_from_entries(self._entries)
            # 清空 append log（已合并到内存）
            log_file.unlink(missing_ok=True)
            logger.info("Applied %d append log entries", applied)

    # ----------------------------------------------------------
    # 统计与分析
    # ----------------------------------------------------------

    def get_stats(self) -> dict:
        """获取题库统计信息。"""
        status_dist: dict[str, int] = defaultdict(int)
        dim_dist: dict[str, int] = defaultdict(int)
        diff_dist: dict[str, int] = defaultdict(int)
        total_uses = 0
        total_passes = 0

        for entry in self._entries.values():
            status_dist[entry.status.value] += 1
            dim_dist[entry.case.target_dimension.value] += 1
            diff_dist[entry.case.difficulty.value] += 1
            total_uses += entry.total_uses
            total_passes += entry.total_passes

        return {
            "total_entries": len(self._entries),
            "active_entries": self.active_count,
            "status_distribution": dict(status_dist),
            "dimension_distribution": dict(dim_dist),
            "difficulty_distribution": dict(diff_dist),
            "total_uses": total_uses,
            "total_passes": total_passes,
            "overall_pass_rate": total_passes / total_uses if total_uses > 0 else 0.0,
            "total_adds": self._total_adds,
            "total_queries": self._total_queries,
            "coverage": self._index.get_coverage_stats(),
        }

    def get_dimension_effectiveness(self) -> dict[str, dict[str, float]]:
        """各维度的有效性分析。"""
        dim_stats: dict[str, dict[str, float]] = {}
        dim_entries: dict[str, list[LibraryEntry]] = defaultdict(list)

        for entry in self._entries.values():
            if entry.status in (EntryStatus.ACTIVE, EntryStatus.VERIFIED):
                dim_entries[entry.case.target_dimension.value].append(entry)

        for dim, entries in dim_entries.items():
            uses = sum(e.total_uses for e in entries)
            passes = sum(e.total_passes for e in entries)
            dim_stats[dim] = {
                "count": len(entries),
                "avg_pass_rate": passes / uses if uses > 0 else 0.0,
                "effective_count": sum(1 for e in entries if e.is_effective),
                "avg_quality_score": (
                    sum(e.quality_score for e in entries) / len(entries) if entries else 0.0
                ),
            }

        return dim_stats

    # ----------------------------------------------------------
    # 内部方法
    # ----------------------------------------------------------

    def _find_duplicate(self, case: AdversarialCase) -> Optional[LibraryEntry]:
        """查找重复的 case（基于 case_id 或内容相似度）。"""
        # 精确匹配: case_id
        for entry in self._entries.values():
            if entry.case.case_id == case.case_id:
                return entry
        # 内容指纹匹配（前 200 字符相同视为重复）
        snippet_hash = hashlib.md5(case.paper_snippet[:200].encode()).hexdigest()
        for entry in self._entries.values():
            existing_hash = hashlib.md5(
                entry.case.paper_snippet[:200].encode()
            ).hexdigest()
            if snippet_hash == existing_hash:
                return entry
        return None

    def _evict_low_quality(self, count: int = 100) -> None:
        """淘汰低质量条目（当容量超限时）。

        淘汰策略:
            1. 已退役的优先淘汰
            2. 已废弃的其次
            3. 质量分最低的
        """
        # 按淘汰优先级排序
        entries_by_priority = sorted(
            self._entries.values(),
            key=lambda e: (
                0 if e.status == EntryStatus.RETIRED else
                1 if e.status == EntryStatus.DEPRECATED else
                2,
                e.quality_score,
            ),
        )

        evicted = 0
        for entry in entries_by_priority[:count]:
            self._entries.pop(entry.entry_id, None)
            self._index.remove_entry(entry)
            evicted += 1

        logger.info("Evicted %d low-quality entries (capacity management)", evicted)


# ==============================================================
# 回归测试套件生成器
# ==============================================================

class RegressionSuiteGenerator:
    """回归测试套件生成器。

    从 AdversarialLibrary 中智能选取一个具有代表性的子集，
    用于快速验证 Agent 是否在已修复的问题上出现回归。

    选取策略:
        1. 维度覆盖: 每个活跃维度至少 N 个 case
        2. 难度覆盖: 各难度级别都有代表
        3. 历史高价值: 优先选取曾经有效挑战过 Agent 的 case
        4. 最近失败优先: 近期仍然失败的 case 优先
        5. 大小控制: 整个套件不超过 max_size
    """

    def __init__(self, library: AdversarialLibrary):
        self._library = library

    def generate(
        self,
        max_size: int = 50,
        min_per_dimension: int = 2,
        min_per_difficulty: int = 2,
        recency_weight: float = 0.7,
        failure_weight: float = 0.8,
    ) -> list[AdversarialCase]:
        """生成回归测试套件。

        Returns:
            选取的对抗样本列表（可直接用于 Agent 执行）。
        """
        if not ADVERSARIAL_TRAINING_ENABLED:
            return []

        # 获取所有活跃且已使用过的条目
        all_entries = [
            entry for entry in self._library._entries.values()
            if entry.status in (EntryStatus.ACTIVE, EntryStatus.VERIFIED)
            and entry.total_uses > 0
        ]

        if not all_entries:
            return []

        # 计算每个条目的选取分数
        now = time.time()
        scored: list[tuple[float, LibraryEntry]] = []
        for entry in all_entries:
            score = self._compute_selection_score(entry, now, recency_weight, failure_weight)
            scored.append((score, entry))

        scored.sort(key=lambda x: x[0], reverse=True)

        # 贪心选取：保证覆盖
        selected: list[LibraryEntry] = []
        dim_counts: dict[str, int] = defaultdict(int)
        diff_counts: dict[str, int] = defaultdict(int)
        selected_ids: set[str] = set()

        # Phase 1: 确保最低覆盖
        for _, entry in scored:
            if len(selected) >= max_size:
                break
            dim = entry.case.target_dimension.value
            diff = entry.case.difficulty.value

            needs_dim = dim_counts[dim] < min_per_dimension
            needs_diff = diff_counts[diff] < min_per_difficulty

            if needs_dim or needs_diff:
                selected.append(entry)
                selected_ids.add(entry.entry_id)
                dim_counts[dim] += 1
                diff_counts[diff] += 1

        # Phase 2: 按分数填充剩余
        for _, entry in scored:
            if len(selected) >= max_size:
                break
            if entry.entry_id not in selected_ids:
                selected.append(entry)
                selected_ids.add(entry.entry_id)

        return [entry.case for entry in selected]

    def generate_targeted(
        self,
        dimension: Optional[WeaknessDimension] = None,
        difficulty: Optional[DifficultyLevel] = None,
        max_size: int = 20,
    ) -> list[AdversarialCase]:
        """生成针对特定维度/难度的回归子集。"""
        entries = self._library.select_regression_suite(
            max_cases=max_size,
            dimension=dimension,
            difficulty=difficulty,
        )
        return [e.case for e in entries]

    def _compute_selection_score(
        self,
        entry: LibraryEntry,
        now: float,
        recency_weight: float,
        failure_weight: float,
    ) -> float:
        """计算条目的选取分数。"""
        # 失败分数: pass_rate 越低 → 分数越高
        failure_score = 1.0 - max(0.0, entry.pass_rate) if entry.pass_rate >= 0 else 0.5

        # 近期分数
        days_since_use = (now - entry.last_used) / 86400 if entry.last_used > 0 else 30.0
        recency_score = max(0.0, 1.0 - days_since_use / 30.0)

        # 使用频次: 多次使用过的更可靠
        use_bonus = min(1.0, entry.total_uses / 10.0) * 0.2

        # 验证加分
        verified_bonus = 0.1 if entry.status == EntryStatus.VERIFIED else 0.0

        # 区分度加分
        disc_bonus = entry.discrimination_power * 0.1

        score = (
            failure_score * failure_weight
            + recency_score * recency_weight
            + use_bonus
            + verified_bonus
            + disc_bonus
        )

        return score

    def get_coverage_report(self) -> dict:
        """获取回归套件的覆盖率报告。"""
        suite = self.generate()

        dim_coverage: dict[str, int] = defaultdict(int)
        diff_coverage: dict[str, int] = defaultdict(int)
        ct_coverage: dict[str, int] = defaultdict(int)

        for case in suite:
            dim_coverage[case.target_dimension.value] += 1
            diff_coverage[case.difficulty.value] += 1
            ct_coverage[case.challenge_type.value] += 1

        all_dims = set(d.value for d in WeaknessDimension)
        all_diffs = set(d.value for d in DifficultyLevel)

        return {
            "suite_size": len(suite),
            "dimension_coverage": {
                "covered": len(dim_coverage),
                "total": len(all_dims),
                "ratio": len(dim_coverage) / len(all_dims) if all_dims else 0.0,
                "distribution": dict(dim_coverage),
            },
            "difficulty_coverage": {
                "covered": len(diff_coverage),
                "total": len(all_diffs),
                "ratio": len(diff_coverage) / len(all_diffs) if all_diffs else 0.0,
                "distribution": dict(diff_coverage),
            },
            "challenge_type_coverage": {
                "covered": len(ct_coverage),
                "total": len(ChallengeType),
                "distribution": dict(ct_coverage),
            },
        }
