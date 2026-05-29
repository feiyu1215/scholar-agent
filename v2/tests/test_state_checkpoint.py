"""
tests/test_state_checkpoint.py — State Checkpoint 管理器单元测试
"""

import unittest
import tempfile
import os
import json
import shutil

from core.state import WorkspaceState
from core.state_checkpoint import (
    CheckpointManager,
    StateSerializer,
    CheckpointMeta,
)


class TestStateSerializer(unittest.TestCase):
    """StateSerializer 序列化/反序列化测试"""

    def setUp(self):
        self.serializer = StateSerializer()

    def test_serialize_default_state(self):
        """默认 WorkspaceState 可序列化"""
        state = WorkspaceState()
        data = self.serializer.serialize(state)
        self.assertIsInstance(data, dict)
        # 应包含可序列化字段
        self.assertIn("findings", data)
        self.assertIn("loop_turns", data)

    def test_skip_fields_excluded(self):
        """SKIP_FIELDS 中的字段不应被序列化"""
        state = WorkspaceState()
        data = self.serializer.serialize(state)
        for skip_field in StateSerializer.SKIP_FIELDS:
            self.assertNotIn(skip_field, data)

    def test_roundtrip_simple_state(self):
        """简单状态的序列化-反序列化往返"""
        state = WorkspaceState(
            paper_sections={"introduction": "This paper studies..."},
            findings=[{"category": "methodology", "finding": "issue 1"}],
            sections_read=["introduction", "methodology"],
            loop_turns=10,
            total_tokens=5000,
            conversation_turns=8,
        )
        data = self.serializer.serialize(state)
        restored = self.serializer.deserialize(data, WorkspaceState)

        self.assertEqual(restored.paper_sections, {"introduction": "This paper studies..."})
        self.assertEqual(restored.findings, [{"category": "methodology", "finding": "issue 1"}])
        self.assertEqual(restored.sections_read, ["introduction", "methodology"])
        self.assertEqual(restored.loop_turns, 10)
        self.assertEqual(restored.total_tokens, 5000)

    def test_serialize_with_none_fields(self):
        """None 字段应正确处理"""
        state = WorkspaceState(paper_path=None, edit_plan=None)
        data = self.serializer.serialize(state)
        self.assertIsNone(data.get("paper_path"))

    def test_serialize_nested_dict(self):
        """嵌套字典应正确序列化"""
        state = WorkspaceState(
            tool_call_counts={"extract_text": 5, "search": 3},
            section_digests={"intro": "digest of introduction"},
        )
        data = self.serializer.serialize(state)
        self.assertEqual(data["tool_call_counts"]["extract_text"], 5)
        self.assertEqual(data["section_digests"]["intro"], "digest of introduction")

    def test_serialize_none_state(self):
        """None 状态应返回空 dict"""
        data = self.serializer.serialize(None)
        self.assertEqual(data, {})


class TestCheckpointManager(unittest.TestCase):
    """CheckpointManager 功能测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.manager = CheckpointManager(
            workdir=self.tmpdir, max_checkpoints=5, compress=True
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_state(self, turn: int = 0, findings_count: int = 0) -> WorkspaceState:
        """创建测试用 WorkspaceState"""
        return WorkspaceState(
            loop_turns=turn,
            conversation_turns=turn,
            findings=[{"category": "test", "finding": f"f{i}"} for i in range(findings_count)],
            sections_read=[f"section_{i}" for i in range(turn)],
            total_tokens=turn * 500,
        )

    def test_save_checkpoint(self):
        """保存 checkpoint"""
        state = self._make_state(turn=1)
        meta = self.manager.save(state, turn=1, phase="initial_read", description="test")
        self.assertIsInstance(meta, CheckpointMeta)
        self.assertEqual(meta.turn, 1)
        self.assertEqual(meta.phase, "initial_read")
        self.assertEqual(meta.description, "test")
        self.assertGreater(meta.size_bytes, 0)

    def test_list_checkpoints(self):
        """列出所有 checkpoints"""
        for i in range(3):
            self.manager.save(self._make_state(turn=i), turn=i, phase=f"phase_{i}")

        cps = self.manager.list_checkpoints()
        self.assertEqual(len(cps), 3)
        # 应该按时间排序
        timestamps = [cp.timestamp for cp in cps]
        self.assertEqual(timestamps, sorted(timestamps))

    def test_restore_by_checkpoint_id(self):
        """按 checkpoint_id 恢复状态"""
        state = WorkspaceState(
            loop_turns=5,
            findings=[{"category": "x", "finding": "important"}],
            sections_read=["intro", "method"],
        )
        meta = self.manager.save(state, turn=5, phase="deep_analysis")

        restored = self.manager.restore(meta.checkpoint_id)
        self.assertIsInstance(restored, WorkspaceState)
        self.assertEqual(restored.loop_turns, 5)
        self.assertEqual(restored.findings, [{"category": "x", "finding": "important"}])
        self.assertEqual(restored.sections_read, ["intro", "method"])

    def test_restore_to_turn(self):
        """按 turn 恢复到最近的 checkpoint"""
        self.manager.save(self._make_state(turn=2), turn=2)
        self.manager.save(self._make_state(turn=5), turn=5)
        self.manager.save(self._make_state(turn=8), turn=8)

        # 请求 turn=6，应恢复 turn=5 的状态
        restored = self.manager.restore_to_turn(6)
        self.assertEqual(restored.loop_turns, 5)

    def test_restore_to_exact_turn(self):
        """恢复精确 turn"""
        self.manager.save(self._make_state(turn=3), turn=3)
        restored = self.manager.restore_to_turn(3)
        self.assertEqual(restored.loop_turns, 3)

    def test_restore_nonexistent_turn_raises(self):
        """恢复不存在的 turn（无任何 checkpoint <= turn）应抛异常"""
        self.manager.save(self._make_state(turn=5), turn=5)
        with self.assertRaises(ValueError):
            self.manager.restore_to_turn(2)  # 无 turn <= 2 的 checkpoint

    def test_restore_nonexistent_id_raises(self):
        """恢复不存在的 id 应抛 FileNotFoundError"""
        with self.assertRaises(FileNotFoundError):
            self.manager.restore("nonexistent_id_12345")

    def test_max_checkpoints_cleanup(self):
        """超过 max_checkpoints 时应清理最早的"""
        for i in range(10):
            self.manager.save(self._make_state(turn=i), turn=i)

        cps = self.manager.list_checkpoints()
        self.assertLessEqual(len(cps), 5)
        # 应保留最新的
        turns = [cp.turn for cp in cps]
        self.assertIn(9, turns)
        self.assertIn(8, turns)
        # 最早的应被清理
        self.assertNotIn(0, turns)

    def test_get_latest(self):
        """获取最新 checkpoint"""
        self.manager.save(self._make_state(turn=1), turn=1)
        self.manager.save(self._make_state(turn=5), turn=5)

        latest = self.manager.get_latest()
        self.assertIsNotNone(latest)
        self.assertEqual(latest.turn, 5)

    def test_get_latest_empty(self):
        """空 registry 应返回 None"""
        result = self.manager.get_latest()
        self.assertIsNone(result)

    def test_delete_checkpoint(self):
        """删除指定 checkpoint"""
        meta1 = self.manager.save(self._make_state(turn=1), turn=1)
        meta2 = self.manager.save(self._make_state(turn=2), turn=2)

        self.manager.delete(meta1.checkpoint_id)
        cps = self.manager.list_checkpoints()
        ids = [cp.checkpoint_id for cp in cps]
        self.assertNotIn(meta1.checkpoint_id, ids)
        self.assertIn(meta2.checkpoint_id, ids)

    def test_clear_all(self):
        """清除所有 checkpoints"""
        for i in range(3):
            self.manager.save(self._make_state(turn=i), turn=i)

        count = self.manager.clear_all()
        self.assertEqual(count, 3)
        self.assertEqual(self.manager.list_checkpoints(), [])

    def test_checkpoint_persistence(self):
        """Checkpoint 应持久化到磁盘（新实例可读取）"""
        state = WorkspaceState(loop_turns=42, total_tokens=9999)
        self.manager.save(state, turn=42, phase="test_persist")

        # 创建新 manager 实例读取同一目录
        new_manager = CheckpointManager(workdir=self.tmpdir, max_checkpoints=5)
        cps = new_manager.list_checkpoints()
        self.assertEqual(len(cps), 1)
        self.assertEqual(cps[0].turn, 42)

        restored = new_manager.restore(cps[0].checkpoint_id)
        self.assertEqual(restored.loop_turns, 42)
        self.assertEqual(restored.total_tokens, 9999)

    def test_compressed_files_exist(self):
        """压缩模式下应创建 .json.gz 文件"""
        meta = self.manager.save(self._make_state(turn=1), turn=1)
        gz_path = os.path.join(self.tmpdir, f"{meta.checkpoint_id}.json.gz")
        self.assertTrue(os.path.exists(gz_path))

    def test_uncompressed_mode(self):
        """非压缩模式下应创建 .json 文件"""
        manager = CheckpointManager(workdir=self.tmpdir, max_checkpoints=5, compress=False)
        meta = manager.save(self._make_state(turn=1), turn=1)
        json_path = os.path.join(self.tmpdir, f"{meta.checkpoint_id}.json")
        self.assertTrue(os.path.exists(json_path))

    def test_save_diff_skips_unchanged(self):
        """diff 保存：状态未变化时应跳过"""
        state = self._make_state(turn=3)
        meta1 = self.manager.save(state, turn=3)

        # 再次保存相同状态应返回 None
        result = self.manager.save_diff(state, base_checkpoint_id=meta1.checkpoint_id, turn=4)
        self.assertIsNone(result)

    def test_save_diff_saves_when_changed(self):
        """diff 保存：状态变化时应保存"""
        state1 = self._make_state(turn=3, findings_count=2)
        meta1 = self.manager.save(state1, turn=3)

        state2 = self._make_state(turn=4, findings_count=5)  # 不同
        result = self.manager.save_diff(state2, base_checkpoint_id=meta1.checkpoint_id, turn=4)
        self.assertIsNotNone(result)
        self.assertEqual(result.turn, 4)

    def test_state_hash_in_meta(self):
        """元数据应包含 state_hash"""
        meta = self.manager.save(self._make_state(turn=1), turn=1)
        self.assertTrue(meta.state_hash)
        self.assertGreater(len(meta.state_hash), 0)


class TestCheckpointManagerEdgeCases(unittest.TestCase):
    """边界情况测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_concurrent_like_saves(self):
        """快速连续保存不应冲突"""
        manager = CheckpointManager(workdir=self.tmpdir, max_checkpoints=20)
        for i in range(15):
            state = WorkspaceState(loop_turns=i, conversation_turns=i)
            manager.save(state, turn=i)
        cps = manager.list_checkpoints()
        self.assertEqual(len(cps), 15)

    def test_corrupted_registry_recovery(self):
        """损坏的 registry 应能恢复"""
        # 写入损坏的 registry
        registry_path = os.path.join(self.tmpdir, "_registry.json")
        with open(registry_path, "w") as f:
            f.write("not valid json {{{")

        # 创建 manager 不应崩溃
        manager = CheckpointManager(workdir=self.tmpdir, max_checkpoints=5)
        self.assertEqual(manager.list_checkpoints(), [])


if __name__ == "__main__":
    unittest.main()
