"""
paper_loader.py — 论文加载模块

从 harness.py 提取。负责将论文（.md / .pdf / workspace 目录）
加载到 WorkspaceState.paper_sections 中。

支持:
- workspace 目录 (含 paper/section_index.json)
- 单个 .md 文件（按 ## heading 拆分）
- 单个 .pdf 文件（委托 pdf_loader）
- 用户参考文献（Phase 58）
"""

from __future__ import annotations

import re
import json
from pathlib import Path
from typing import Any

from core.state import WorkspaceState
from core.paper_index import PaperIndexBuilder


def load_paper(state: WorkspaceState, path: str, allowed_base: str | None = None):
    """加载论文到 state。

    支持:
    - workspace 目录 (含 paper/section_index.json)
    - 单个 .md 文件
    - 单个 .pdf 文件

    Args:
        state: 工作区状态对象
        path: 论文文件/目录路径
        allowed_base: 允许的基础目录（安全沙箱）。
            若设置，将拒绝加载该目录外的文件（防止路径遍历攻击）。
            若为 None，不限制（向后兼容）。
    """
    p = Path(path).resolve()

    # 路径遍历防御
    if allowed_base:
        base = Path(allowed_base).resolve()
        if not p.is_relative_to(base):
            raise ValueError(
                f"Security: paper path '{p}' is outside allowed base '{base}'. "
                f"Path traversal attempt rejected."
            )

    if p.is_dir():
        # 安全基准: 优先用 allowed_base，否则以 p 本身为沙箱
        _base = Path(allowed_base).resolve() if allowed_base else p

        # 优先使用 section_index.json
        index_path = p / "paper" / "section_index.json"
        if index_path.exists():
            index = json.loads(index_path.read_text(encoding="utf-8"))
            for entry in index:
                title = entry.get("title", entry.get("slug", entry["id"]))
                # 将 entry["file"] 相对于 workspace 目录解析，防止二级路径遍历
                file_path = (p / entry["file"]).resolve()
                if not file_path.is_relative_to(_base):
                    continue  # 跳过越界路径
                if file_path.exists():
                    state.paper_sections[title.lower()] = file_path.read_text(encoding="utf-8")
        else:
            # 退化: 直接扫描 sections 目录
            sections_dir = p / "paper" / "sections"
            if sections_dir.exists():
                for f in sorted(sections_dir.glob("*.md")):
                    name = f.stem.split("_", 1)[-1] if "_" in f.stem else f.stem
                    state.paper_sections[name] = f.read_text(encoding="utf-8")

        # 全文（可选）
        full_text_path = p / "paper" / "full_text.md"
        if full_text_path.exists():
            state.paper_sections["full"] = full_text_path.read_text(encoding="utf-8")

    elif p.suffix == ".pdf":
        from core.pdf_loader import load_pdf_as_sections
        state.paper_sections = load_pdf_as_sections(p)

    elif p.suffix == ".md":
        full_text = p.read_text(encoding="utf-8")
        state.paper_sections["full"] = full_text
        # 按 ## heading 拆分
        lines = full_text.split("\n")
        current_section = None
        current_content: list[str] = []

        for line in lines:
            match = re.match(r'^##\s+(.+)', line)
            if match:
                if current_section:
                    state.paper_sections[current_section] = "\n".join(current_content).strip()
                current_section = match.group(1).strip().lower().rstrip(".")
                current_content = [line]
            elif current_section:
                current_content.append(line)

        if current_section and current_content:
            state.paper_sections[current_section] = "\n".join(current_content).strip()

    # Phase B1: 论文加载后自动构建结构预索引
    if state.paper_sections:
        state.paper_structure_index = PaperIndexBuilder().build(
            state.paper_sections
        )


def load_user_references(state: WorkspaceState, paths: list[str]):
    """加载用户提供的参考文献（Phase 58）。

    支持 PDF 和 Markdown 文件。加载后存入 user_reference_docs（完整内容）
    和 reference_papers（元数据摘要，source="user_provided"）。
    """
    for i, path_str in enumerate(paths, 1):
        p = Path(path_str)
        if not p.exists():
            continue

        ref_id = f"ref_{i}"
        title = p.stem.replace("_", " ").replace("-", " ")

        if p.suffix == ".pdf":
            try:
                from core.pdf_loader import load_pdf_as_sections
                sections = load_pdf_as_sections(p)
                abstract = ""
                for key in sections:
                    if "abstract" in key.lower():
                        abstract = sections[key][:500]
                        break
                if not abstract:
                    first_section = next(iter(sections.values()), "")
                    abstract = first_section[:500]
            except Exception:
                sections = {"full": f"[PDF 加载失败: {path_str}]"}
                abstract = ""

        elif p.suffix == ".md":
            full_text = p.read_text(encoding="utf-8")
            sections = {"full": full_text}
            lines = full_text.split("\n")
            current_section = None
            current_content: list[str] = []
            for line in lines:
                match = re.match(r'^##\s+(.+)', line)
                if match:
                    if current_section:
                        sections[current_section] = "\n".join(current_content).strip()
                    current_section = match.group(1).strip().lower()
                    current_content = [line]
                elif current_section:
                    current_content.append(line)
            if current_section and current_content:
                sections[current_section] = "\n".join(current_content).strip()
            abstract = full_text[:500]
        else:
            try:
                text = p.read_text(encoding="utf-8")
                sections = {"full": text}
                abstract = text[:500]
            except Exception:
                continue

        # 存入完整内容
        state.user_reference_docs[ref_id] = {
            "title": title,
            "source_path": str(p),
            "sections": sections,
            "section_names": list(sections.keys()),
        }

        # 存入 reference_papers 元数据
        state.reference_papers[ref_id] = {
            "title": title,
            "authors": [],
            "year": None,
            "venue": None,
            "abstract": abstract[:200] if abstract else None,
            "tldr": None,
            "citation_count": None,
            "source": "user_provided",
            "source_path": str(p),
            "fetch_reason": "用户提供的参考文献",
            "section_count": len(sections),
            "total_chars": sum(len(v) for v in sections.values()),
        }
