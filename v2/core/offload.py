"""
core/offload.py — 可恢复的上下文卸载 (Phase 32)

设计灵感:
    - TencentDB Agent Memory: "压缩可以发生，但必须可逆"
    - refs/*.md 外部文件 + node_id 回查机制
    - "上层保结构，下层保证据"

设计原则:
    - 工具返回的长结果 (论文 section、搜索结果) 不再被 compress_messages 真正丢弃
    - 而是 offload 到 .workspace/refs/ 目录，保留一个 ref_id 指针
    - Agent 如需回溯，可通过 recall_context(ref_id) 工具恢复完整内容
    - 与 section_digest 形成互补: digest 是"记忆锚点"，offload 是"证据底座"

存储结构:
    .workspace/refs/
    ├── ref_001_methods.md       # 第 1 次读 methods section 的完整内容
    ├── ref_002_search_DID.md    # 第 2 次搜索 "DID" 的完整结果
    ├── ref_003_results.md       # 第 3 次读 results section 的完整内容
    └── manifest.jsonl           # 所有 ref 的索引 (ref_id → file, tool, summary)

不做:
    - 不做 Mermaid 画布 (Phase 32 先验证 offload 有效，画布是后续增强)
    - 不做分级压缩 (当前 compress_messages 的 adaptive keep_recent 已足够)
    - 不做 L0-L3 完整四层 (overengineering for our scale)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass, field


# ============================================================
# Data Models
# ============================================================

@dataclass
class OffloadEntry:
    """一条卸载记录。"""
    ref_id: str           # "ref_001", "ref_002", ...
    tool_name: str        # 触发卸载的工具名 (read_section, search_literature, ...)
    key: str              # 上下文标识 (section 名或搜索 query)
    char_count: int       # 原始内容长度
    summary: str          # 1-2 句话摘要 (< 150 chars)
    timestamp: str        # ISO format
    file_name: str        # 存储文件名 (不含目录)
    loop_turn: int = 0    # 产生时的 loop turn


@dataclass
class OffloadStore:
    """
    管理所有卸载的内容引用。
    
    职责:
    1. offload() — 接收工具返回的长内容，写入文件，返回 ref_id
    2. recall() — 根据 ref_id 从文件恢复完整内容
    3. format_refs_summary() — 生成简短的 refs 摘要列表 (注入 format_context)
    """
    
    refs_dir: Path
    entries: list[OffloadEntry] = field(default_factory=list)
    _counter: int = 0
    
    def __post_init__(self):
        self.refs_dir.mkdir(parents=True, exist_ok=True)
        self._load_manifest()
    
    def _manifest_path(self) -> Path:
        return self.refs_dir / "manifest.jsonl"
    
    def _load_manifest(self) -> None:
        """加载已有的 manifest。渐进退化: 文件不存在时使用空状态。"""
        manifest = self._manifest_path()
        if not manifest.exists():
            return
        try:
            for line in manifest.read_text(encoding="utf-8").strip().split("\n"):
                if line.strip():
                    data = json.loads(line)
                    self.entries.append(OffloadEntry(**data))
                    self._counter = max(self._counter, int(data["ref_id"].split("_")[1]))
        except (json.JSONDecodeError, KeyError, ValueError):
            pass  # 容错: manifest 损坏时丢弃，不崩溃
    
    def offload(
        self,
        tool_name: str,
        key: str,
        content: str,
        summary: str,
        loop_turn: int = 0,
    ) -> str:
        """
        将长内容卸载到文件，返回 ref_id。
        
        Args:
            tool_name: 产生此内容的工具名
            key: 内容标识 (如 section 名、搜索 query)
            content: 要卸载的完整内容
            summary: 1-2 句话摘要 (用于 compress 后的占位)
            loop_turn: 当前 loop turn
            
        Returns:
            ref_id: 如 "ref_003"
        """
        self._counter += 1
        ref_id = f"ref_{self._counter:03d}"
        
        # 生成安全文件名
        safe_key = "".join(c if c.isalnum() or c in "-_" else "_" for c in key)[:30]
        file_name = f"{ref_id}_{safe_key}.md"
        
        # 写入文件
        file_path = self.refs_dir / file_name
        file_path.write_text(content, encoding="utf-8")
        
        # 创建条目
        entry = OffloadEntry(
            ref_id=ref_id,
            tool_name=tool_name,
            key=key,
            char_count=len(content),
            summary=summary[:150],
            timestamp=datetime.now(timezone.utc).isoformat(),
            file_name=file_name,
            loop_turn=loop_turn,
        )
        self.entries.append(entry)
        
        # 追加到 manifest
        self._append_manifest(entry)
        
        return ref_id
    
    def recall(self, ref_id: str) -> str | None:
        """
        根据 ref_id 恢复完整内容。
        
        Returns:
            完整内容，或 None (如果找不到)。
        """
        entry = next((e for e in self.entries if e.ref_id == ref_id), None)
        if not entry:
            return None
        
        file_path = self.refs_dir / entry.file_name
        if not file_path.exists():
            return None
        
        return file_path.read_text(encoding="utf-8")
    
    def recall_by_key(self, key: str) -> str | None:
        """
        根据 key (如 section 名) 恢复最新的卸载内容。
        
        用于 Agent 想回溯"我之前读过的 methods section"但不记得 ref_id 时。
        """
        matching = [e for e in self.entries if e.key.lower() == key.lower()]
        if not matching:
            # 模糊匹配
            matching = [e for e in self.entries if key.lower() in e.key.lower()]
        if not matching:
            return None
        
        # 取最新的
        latest = matching[-1]
        file_path = self.refs_dir / latest.file_name
        if not file_path.exists():
            return None
        
        return file_path.read_text(encoding="utf-8")
    
    def format_refs_summary(self) -> str:
        """
        生成 refs 摘要列表，用于注入 format_context。
        
        设计: 每条 ref 只占 1 行 (< 100 chars)，总长度受控。
        最多展示最近 10 条，避免注入过长。
        """
        if not self.entries:
            return ""
        
        recent = self.entries[-10:]  # 最多最近 10 条
        lines = [f"📂 已卸载的上下文 ({len(self.entries)} 条，可用 recall_context 回查):"]
        for e in recent:
            lines.append(f"  [{e.ref_id}] {e.tool_name}('{e.key}') — {e.summary[:80]}")
        if len(self.entries) > 10:
            lines.append(f"  ...还有 {len(self.entries) - 10} 条更早的")
        
        return "\n".join(lines)
    
    def _append_manifest(self, entry: OffloadEntry) -> None:
        """追加一条到 manifest 文件。"""
        manifest = self._manifest_path()
        data = {
            "ref_id": entry.ref_id,
            "tool_name": entry.tool_name,
            "key": entry.key,
            "char_count": entry.char_count,
            "summary": entry.summary,
            "timestamp": entry.timestamp,
            "file_name": entry.file_name,
            "loop_turn": entry.loop_turn,
        }
        with manifest.open("a", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False) + "\n")
    
    def should_offload(self, content: str, tool_name: str) -> bool:
        """
        判断一个工具返回结果是否值得 offload。
        
        规则:
        - read_section 结果 > 500 chars → offload
        - search_literature 结果 > 300 chars → offload
        - 其他工具 → 不 offload (结果通常很短)
        """
        offload_tools = {
            "read_section": 500,
            "search_literature": 300,
        }
        threshold = offload_tools.get(tool_name)
        if threshold is None:
            return False
        return len(content) > threshold
