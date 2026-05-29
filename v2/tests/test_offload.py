"""
OffloadStore 单元测试。

覆盖：
  - offload: 文件写入、ref_id 递增、manifest 追加
  - recall: 通过 ref_id 恢复、缺失返回 None
  - recall_by_key: 精确匹配、模糊匹配、取最新
  - format_refs_summary: 空/少/多条的输出格式
  - should_offload: 各工具阈值判断
  - manifest 持久化: 重建后 entries 恢复
  - 边界情况: 特殊字符 key、manifest 损坏容错
"""

import json
import pytest
from pathlib import Path

from core.offload import OffloadStore, OffloadEntry


# ==============================================================
# Fixtures
# ==============================================================

@pytest.fixture
def store(tmp_path):
    """创建一个临时目录下的 OffloadStore。"""
    refs_dir = tmp_path / "refs"
    return OffloadStore(refs_dir=refs_dir)


@pytest.fixture
def populated_store(tmp_path):
    """预填充 3 条 offload 的 store。"""
    refs_dir = tmp_path / "refs"
    s = OffloadStore(refs_dir=refs_dir)
    s.offload("read_section", "methods", "A" * 1000, "Methods section summary", loop_turn=1)
    s.offload("search_literature", "DID methodology", "B" * 500, "DID search results", loop_turn=2)
    s.offload("read_section", "results", "C" * 800, "Results section summary", loop_turn=3)
    return s


# ==============================================================
# Tests: Basic Offload
# ==============================================================

class TestOffload:
    def test_offload_returns_ref_id(self, store):
        """offload 应返回格式为 ref_NNN 的 id。"""
        ref_id = store.offload("read_section", "intro", "content here", "summary")
        assert ref_id == "ref_001"

    def test_offload_increments_counter(self, store):
        """连续 offload 应递增 counter。"""
        r1 = store.offload("read_section", "intro", "aaa", "s1")
        r2 = store.offload("read_section", "methods", "bbb", "s2")
        r3 = store.offload("search_literature", "DID", "ccc", "s3")
        assert r1 == "ref_001"
        assert r2 == "ref_002"
        assert r3 == "ref_003"

    def test_offload_creates_file(self, store):
        """offload 应在 refs_dir 下创建对应文件。"""
        store.offload("read_section", "methods", "full content", "summary")
        files = list(store.refs_dir.glob("ref_001_*.md"))
        assert len(files) == 1
        assert files[0].read_text(encoding="utf-8") == "full content"

    def test_offload_safe_filename(self, store):
        """含特殊字符的 key 应被清理为安全文件名。"""
        store.offload("read_section", "methods & results (2024)", "content", "summary")
        files = list(store.refs_dir.glob("ref_001_*.md"))
        assert len(files) == 1
        # 文件名不应包含 &、空格、括号
        fname = files[0].name
        assert "&" not in fname
        assert "(" not in fname

    def test_offload_truncates_summary(self, store):
        """超长 summary 应被截断到 150 字符。"""
        long_summary = "x" * 300
        store.offload("read_section", "intro", "content", long_summary)
        entry = store.entries[0]
        assert len(entry.summary) == 150

    def test_offload_records_entry(self, store):
        """offload 应在 entries 列表中添加 OffloadEntry。"""
        store.offload("read_section", "methods", "content", "summary", loop_turn=5)
        assert len(store.entries) == 1
        entry = store.entries[0]
        assert entry.ref_id == "ref_001"
        assert entry.tool_name == "read_section"
        assert entry.key == "methods"
        assert entry.char_count == len("content")
        assert entry.loop_turn == 5

    def test_offload_appends_manifest(self, store):
        """每次 offload 应追加到 manifest.jsonl。"""
        store.offload("read_section", "intro", "aaa", "s1")
        store.offload("read_section", "methods", "bbb", "s2")
        manifest = store.refs_dir / "manifest.jsonl"
        lines = manifest.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2
        data = json.loads(lines[0])
        assert data["ref_id"] == "ref_001"


# ==============================================================
# Tests: Recall
# ==============================================================

class TestRecall:
    def test_recall_by_ref_id(self, populated_store):
        """recall 应通过 ref_id 恢复完整内容。"""
        content = populated_store.recall("ref_001")
        assert content == "A" * 1000

    def test_recall_second_entry(self, populated_store):
        """recall 应能恢复任意 ref_id 的内容。"""
        content = populated_store.recall("ref_002")
        assert content == "B" * 500

    def test_recall_nonexistent_ref_id(self, populated_store):
        """不存在的 ref_id 应返回 None。"""
        assert populated_store.recall("ref_999") is None

    def test_recall_file_deleted(self, populated_store):
        """文件被删除后 recall 应返回 None。"""
        # 删除对应文件
        entry = populated_store.entries[0]
        (populated_store.refs_dir / entry.file_name).unlink()
        assert populated_store.recall("ref_001") is None


# ==============================================================
# Tests: Recall by Key
# ==============================================================

class TestRecallByKey:
    def test_exact_match(self, populated_store):
        """recall_by_key 精确匹配应返回内容。"""
        content = populated_store.recall_by_key("methods")
        assert content == "A" * 1000

    def test_case_insensitive(self, populated_store):
        """recall_by_key 应不区分大小写。"""
        content = populated_store.recall_by_key("METHODS")
        assert content == "A" * 1000

    def test_fuzzy_match(self, populated_store):
        """recall_by_key 模糊匹配应返回包含关键词的内容。"""
        content = populated_store.recall_by_key("DID")
        assert content == "B" * 500

    def test_returns_latest_when_multiple(self, store):
        """同一 key 多次 offload 应返回最新的。"""
        store.offload("read_section", "methods", "version1", "v1")
        store.offload("read_section", "methods", "version2", "v2")
        content = store.recall_by_key("methods")
        assert content == "version2"

    def test_no_match_returns_none(self, populated_store):
        """无匹配时应返回 None。"""
        assert populated_store.recall_by_key("nonexistent_section") is None


# ==============================================================
# Tests: format_refs_summary
# ==============================================================

class TestFormatRefsSummary:
    def test_empty_store(self, store):
        """空 store 应返回空字符串。"""
        assert store.format_refs_summary() == ""

    def test_single_entry(self, store):
        """单条 entry 应包含 ref_id 和 key 信息。"""
        store.offload("read_section", "methods", "content", "Methods section")
        summary = store.format_refs_summary()
        assert "ref_001" in summary
        assert "methods" in summary
        assert "Methods section" in summary

    def test_max_10_shown(self, store):
        """超过 10 条时应只显示最近 10 条并提示更多。"""
        for i in range(15):
            store.offload("read_section", f"section_{i}", f"content_{i}", f"summary_{i}")
        summary = store.format_refs_summary()
        # 不应包含前 5 条
        assert "ref_001" not in summary
        # 应包含最近的
        assert "ref_015" in summary
        # 应提示还有更多
        assert "还有" in summary

    def test_contains_count(self, populated_store):
        """summary 应包含总条数。"""
        summary = populated_store.format_refs_summary()
        assert "3" in summary


# ==============================================================
# Tests: should_offload
# ==============================================================

class TestShouldOffload:
    def test_read_section_above_threshold(self, store):
        """read_section 超过 500 字符应 offload。"""
        assert store.should_offload("x" * 501, "read_section") is True

    def test_read_section_below_threshold(self, store):
        """read_section 不超过 500 字符不应 offload。"""
        assert store.should_offload("x" * 500, "read_section") is False

    def test_search_literature_above_threshold(self, store):
        """search_literature 超过 300 字符应 offload。"""
        assert store.should_offload("x" * 301, "search_literature") is True

    def test_search_literature_below_threshold(self, store):
        """search_literature 不超过 300 字符不应 offload。"""
        assert store.should_offload("x" * 300, "search_literature") is False

    def test_other_tool_never_offload(self, store):
        """其他工具不应 offload，即使内容很长。"""
        assert store.should_offload("x" * 10000, "mark_finding") is False
        assert store.should_offload("x" * 10000, "reflect_and_plan") is False


# ==============================================================
# Tests: Manifest Persistence
# ==============================================================

class TestManifestPersistence:
    def test_reload_from_manifest(self, tmp_path):
        """新建 store 应从 manifest 恢复已有 entries。"""
        refs_dir = tmp_path / "refs"
        s1 = OffloadStore(refs_dir=refs_dir)
        s1.offload("read_section", "intro", "content_intro", "intro summary")
        s1.offload("read_section", "methods", "content_methods", "methods summary")

        # 创建新 store 实例，应从 manifest 恢复
        s2 = OffloadStore(refs_dir=refs_dir)
        assert len(s2.entries) == 2
        assert s2.entries[0].ref_id == "ref_001"
        assert s2.entries[1].ref_id == "ref_002"

    def test_counter_continues_after_reload(self, tmp_path):
        """重建后 counter 应从最后的 ref_id 继续。"""
        refs_dir = tmp_path / "refs"
        s1 = OffloadStore(refs_dir=refs_dir)
        s1.offload("read_section", "intro", "aaa", "s1")
        s1.offload("read_section", "methods", "bbb", "s2")

        s2 = OffloadStore(refs_dir=refs_dir)
        ref_id = s2.offload("read_section", "results", "ccc", "s3")
        assert ref_id == "ref_003"

    def test_corrupted_manifest_graceful(self, tmp_path):
        """manifest 损坏时应 gracefully 使用空状态。"""
        refs_dir = tmp_path / "refs"
        refs_dir.mkdir(parents=True)
        manifest = refs_dir / "manifest.jsonl"
        manifest.write_text("this is not valid json\n{also broken}", encoding="utf-8")

        # 不应抛异常
        s = OffloadStore(refs_dir=refs_dir)
        assert len(s.entries) == 0

    def test_recall_after_reload(self, tmp_path):
        """从 manifest 恢复的 store 应能 recall 内容。"""
        refs_dir = tmp_path / "refs"
        s1 = OffloadStore(refs_dir=refs_dir)
        s1.offload("read_section", "methods", "full methods content here", "methods")

        s2 = OffloadStore(refs_dir=refs_dir)
        content = s2.recall("ref_001")
        assert content == "full methods content here"
