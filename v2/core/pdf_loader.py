"""
core/pdf_loader.py — PDF → sections 转换器

职责：
    把一个 PDF 文件转成 dict[str, str]，key 是 section 名（小写），value 是文本内容。
    这个 dict 直接注入 harness.state.paper_sections，Agent 通过 read_section 按需读取。

设计原则：
    - 轻量：不写中间文件，不依赖 workspace 目录结构
    - 宽容：PDF 提取天然有噪声，做合理清理但不追求完美
    - 如果识别不出 section 结构，退化为 "full" 单段

Phase 36 改进：
    - 利用 pymupdf 的字体大小信息识别 heading 层级（而非纯 regex）
    - 区分主文 vs 附录（Online Appendix）
    - 子标题保留父子关系（如 "4. model-free analysis > 4.1 identification"）
    - 脚注文本分离（font_size < body_size 的小字体文本）
    - Figure/Table 页面标记
"""

from __future__ import annotations

import re
from pathlib import Path
from dataclasses import dataclass, field
from collections import Counter


def load_pdf_as_sections(path: str | Path) -> dict[str, str]:
    """
    从 PDF 提取文本并按 heading 分割成 sections。

    采用三级 fallback 策略：
    1. pymupdf 字体感知结构化提取（最佳质量）
    2. pdfplumber 布局感知提取（better for two-column PDFs）
    3. pymupdf 纯文本 + regex 分割（最基础 fallback）

    Args:
        path: PDF 文件路径

    Returns:
        dict[str, str]: section_name -> content 的映射。
        始终包含 "full" key（全文）。

    Raises:
        FileNotFoundError: 文件不存在
        ImportError: 缺少 pymupdf 和 pdfplumber（至少需要一个）
        ValueError: 提取的文本过短（可能是扫描件/图片 PDF）
    """
    import logging
    logger = logging.getLogger(__name__)

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"PDF 文件不存在: {path}")

    errors: list[str] = []

    # === Level 1: pymupdf 字体感知提取 ===
    try:
        sections = _extract_with_font_info(path)
        if sections and len(sections) >= 3:
            full_text = _extract_plain_text(path)
            sections["full"] = full_text
            logger.debug("PDF loaded via pymupdf font-aware extraction: %d sections", len(sections))
            return sections
    except ImportError:
        errors.append("pymupdf not available")
    except Exception as e:
        errors.append(f"pymupdf font extraction failed: {e}")

    # === Level 2: pdfplumber 布局感知提取（better for two-column） ===
    try:
        sections = _extract_with_pdfplumber(path)
        if sections and len(sections) >= 3:
            full_text = _get_pdfplumber_full_text(path)
            sections["full"] = full_text
            logger.debug("PDF loaded via pdfplumber fallback: %d sections", len(sections))
            return sections
    except ImportError:
        errors.append("pdfplumber not available")
    except Exception as e:
        errors.append(f"pdfplumber extraction failed: {e}")

    # === Level 3: 纯文本 + regex 分割 ===
    text = ""
    try:
        text = _extract_plain_text(path)
    except ImportError:
        # pymupdf not available, try pdfplumber for plain text
        try:
            text = _get_pdfplumber_full_text(path)
        except ImportError:
            raise ImportError(
                "需要 pymupdf 或 pdfplumber 中的至少一个来解析 PDF。"
                "安装: pip install pymupdf 或 pip install pdfplumber"
            )
    except Exception as e:
        # pymupdf 可用但提取失败（如文件损坏），fallback 到 pdfplumber
        errors.append(f"pymupdf plain text extraction failed: {e}")
        try:
            text = _get_pdfplumber_full_text(path)
        except ImportError:
            raise ImportError(
                "pymupdf 提取失败且 pdfplumber 不可用。"
                f"pymupdf 错误: {e}\n"
                "安装: pip install pdfplumber"
            )
        except Exception as e2:
            errors.append(f"pdfplumber plain text also failed: {e2}")
            # text 仍为空，将在下方 len < 200 检查中报错

    text = _clean_text(text)

    if len(text.strip()) < 200:
        raise ValueError(
            f"从 PDF 提取的文本过短（{len(text.strip())} 字符）。"
            f"可能是扫描件或图片 PDF，当前不支持 OCR。"
            f"\n提取尝试记录: {'; '.join(errors)}"
        )

    sections = _split_into_sections_regex(text)
    if not sections:
        # 最终退化：整个文本作为 "full" 段
        logger.warning(
            "无法识别 PDF section 结构（%s），退化为单段模式。尝试记录: %s",
            path.name,
            "; ".join(errors),
        )
    sections["full"] = text
    return sections


# ============================================================
# Phase 36: 字体感知的结构化提取
# ============================================================

@dataclass
class TextSpan:
    """一个文本片段，带字体信息。"""
    text: str
    font_size: float
    font_name: str
    page_num: int
    y_pos: float  # 垂直位置（用于判断同一行）
    x_pos: float  # 水平位置（用于判断缩进）
    is_bold: bool = False


@dataclass
class HeadingNode:
    """一个 heading 节点，构成文档结构树。"""
    title: str
    level: int  # 1=最高级标题, 2=次级, 3=子标题
    page_num: int
    content_start: int  # 在 full_spans 列表中的起始位置
    content_end: int = -1  # 在 full_spans 列表中的结束位置
    parent_title: str = ""  # 父标题（用于构建层级 key）
    is_appendix: bool = False  # 是否属于附录部分


def _extract_with_font_info(path: Path) -> dict[str, str]:
    """
    利用 pymupdf 的字体大小信息进行结构化提取。
    
    核心思路：
    1. 提取所有 text spans 及其字体大小
    2. 统计字体大小分布，识别 body_size / heading_sizes
    3. 用字体大小层级识别 heading 结构
    4. 按 heading 切分内容，保留层级关系
    """
    doc = _open_pdf(path)
    
    try:
        # Step 1: 提取所有 spans
        all_spans: list[TextSpan] = []
        for page_num, page in enumerate(doc):
            page_dict = page.get_text("dict")
            for block in page_dict.get("blocks", []):
                if block.get("type") != 0:  # 只处理文本 block
                    continue
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        text = span.get("text", "")
                        if not text.strip():
                            continue
                        font_name = span.get("font", "")
                        is_bold = "Bold" in font_name or "bold" in font_name
                        all_spans.append(TextSpan(
                            text=text,
                            font_size=round(span.get("size", 0), 1),
                            font_name=font_name,
                            page_num=page_num,
                            y_pos=span.get("origin", [0, 0])[1] if "origin" in span else line["bbox"][1],
                            x_pos=span.get("origin", [0, 0])[0] if "origin" in span else line["bbox"][0],
                            is_bold=is_bold,
                        ))
    finally:
        doc.close()
    
    if not all_spans:
        return {}
    
    # Step 2: 分析字体大小分布
    size_counter: Counter[float] = Counter()
    for span in all_spans:
        # 只统计有实质内容的 span（排除单个数字/标点）
        if len(span.text.strip()) > 3:
            size_counter[span.font_size] += len(span.text)
    
    if not size_counter:
        return {}
    
    # body_size = 出现最多的字体大小
    body_size = size_counter.most_common(1)[0][0]
    
    # heading sizes = 比 body_size 大的字体大小（按大小排序）
    heading_sizes = sorted(
        [s for s in size_counter if s > body_size + 0.5],
        reverse=True,
    )
    
    # footnote_size = 比 body_size 小的字体大小
    footnote_threshold = body_size - 1.5
    
    # Step 3: 识别 headings
    headings = _identify_headings(all_spans, body_size, heading_sizes)
    
    if len(headings) < 3:
        return {}
    
    # Step 4: 检测附录边界
    appendix_start_idx = _detect_appendix_boundary(headings, all_spans)
    
    # Step 5: 按 heading 切分内容
    sections = _build_sections_from_headings(
        all_spans, headings, body_size, footnote_threshold, appendix_start_idx
    )
    
    # Step 6: 后处理——合并空壳 section、合并 Figure/Table 为汇总
    sections = _postprocess_sections(sections)
    
    return sections


def _identify_headings(
    spans: list[TextSpan],
    body_size: float,
    heading_sizes: list[float],
) -> list[HeadingNode]:
    """
    从 spans 中识别 heading 节点。
    
    策略：
    - 字体大小 > body_size 的文本行是 heading 候选
    - 合并同一行的多个 span（如 "4.1" + "Identification" 在同一行）
    - 过滤掉 Figure/Table 标题（它们是 caption 不是 section heading）
    - 分配 level（基于字体大小层级）
    """
    headings: list[HeadingNode] = []
    
    # 将 heading_sizes 映射到 level
    # 最大字体 = level 0 (论文标题), 次大 = level 1 (主 section), 再次 = level 2 (子 section)
    size_to_level = {}
    for i, size in enumerate(heading_sizes):
        size_to_level[size] = i  # 0 = 最大标题, 1 = 次级, ...
    
    # 合并同一行的 spans（同一页、y_pos 接近的大字体 span）
    i = 0
    while i < len(spans):
        span = spans[i]
        
        # 只关注大于 body_size 的 span
        if span.font_size <= body_size + 0.5:
            i += 1
            continue
        
        # 找到这个 span 对应的 level
        level = _get_heading_level(span.font_size, heading_sizes)
        if level is None:
            i += 1
            continue
        
        # 合并同一行的后续 span（y_pos 差距 < 2，同一页）
        merged_text = span.text.strip()
        j = i + 1
        while j < len(spans):
            next_span = spans[j]
            if (next_span.page_num == span.page_num and
                abs(next_span.y_pos - span.y_pos) < 3 and
                next_span.font_size > body_size + 0.5):
                merged_text += " " + next_span.text.strip()
                j += 1
            else:
                break
        
        # 清理合并后的文本
        merged_text = _clean_heading_text(merged_text)
        
        # Figure/Table caption：保留为 heading（标记为 figure/table），
        # 后续在 _postprocess_sections 中统一合并
        if _is_figure_or_table_caption(merged_text):
            # 尝试合并 caption 描述行（紧跟的下一个大字体 span）
            if j < len(spans):
                next_s = spans[j]
                if (next_s.font_size > body_size + 0.5 and
                    next_s.page_num - span.page_num <= 1 and
                    not re.match(r'^\d+(\.\d+)?\.?\s', next_s.text.strip()) and
                    not _is_figure_or_table_caption(next_s.text.strip())):
                    # 合并描述行到 caption 标题
                    desc_text = next_s.text.strip()
                    skip_j = j + 1
                    while skip_j < len(spans):
                        ss = spans[skip_j]
                        if (ss.page_num == next_s.page_num and
                            abs(ss.y_pos - next_s.y_pos) < 3 and
                            ss.font_size > body_size + 0.5):
                            desc_text += " " + ss.text.strip()
                            skip_j += 1
                        else:
                            break
                    merged_text = merged_text + ": " + _clean_heading_text(desc_text)
                    j = skip_j
            
            headings.append(HeadingNode(
                title=merged_text,
                level=level,
                page_num=span.page_num,
                content_start=j,
            ))
            i = j
            continue
        
        if _is_metadata(merged_text, span.page_num):
            i = j
            continue
        
        # 过滤：太短的文本（如孤立的 "4.1"）
        # 但保留纯数字编号——它们会在后续和下一个 span 合并
        if len(merged_text) <= 3 and not re.match(r'^\d+(\.\d+)?$', merged_text):
            i = j
            continue
        
        # 特殊处理：如果是纯数字编号（如 "4.1"），尝试和下一个大字体 span 合并
        if re.match(r'^\d+(\.\d+)?\.?$', merged_text.rstrip('.')):
            # 看下一个 span 是否也是大字体且在附近
            if j < len(spans) and spans[j].font_size > body_size + 0.5:
                next_span = spans[j]
                # 允许跨行但同页
                if next_span.page_num == span.page_num:
                    merged_text = merged_text.rstrip('.') + " " + next_span.text.strip()
                    j += 1
                    # 继续合并同行的后续 span
                    while j < len(spans):
                        ns = spans[j]
                        if (ns.page_num == span.page_num and
                            abs(ns.y_pos - spans[j-1].y_pos) < 3 and
                            ns.font_size > body_size + 0.5):
                            merged_text += " " + ns.text.strip()
                            j += 1
                        else:
                            break
                    merged_text = _clean_heading_text(merged_text)
        
        # 再次检查：合并后如果还是纯数字，跳过
        if re.match(r'^\d+(\.\d+)?\.?$', merged_text.rstrip('.')):
            i = j
            continue
        
        headings.append(HeadingNode(
            title=merged_text,
            level=level,
            page_num=span.page_num,
            content_start=j,  # 内容从下一个 span 开始
        ))
        
        i = j
    
    # 设置每个 heading 的 content_end
    for idx in range(len(headings)):
        if idx + 1 < len(headings):
            headings[idx].content_end = headings[idx + 1].content_start
        else:
            headings[idx].content_end = len(spans)
    
    # 建立父子关系
    _assign_parent_titles(headings)
    
    return headings


def _get_heading_level(font_size: float, heading_sizes: list[float]) -> int | None:
    """将字体大小映射到 heading level。"""
    for i, size in enumerate(heading_sizes):
        if abs(font_size - size) < 0.3:
            return i
    return None


def _clean_heading_text(text: str) -> str:
    """清理 heading 文本。"""
    # 去除多余空格
    text = re.sub(r'\s+', ' ', text).strip()
    # 去除尾部句号
    text = text.rstrip('.')
    return text


def _is_figure_or_table_caption(text: str) -> bool:
    """判断是否是 Figure/Table caption（不是 section heading）。"""
    return bool(re.match(
        r'^(Figure|Table|Fig\.?)\s+[A-Z]?\.?\s*\d',
        text, re.IGNORECASE
    )) or bool(re.match(
        r'^(Figure|Table|Fig\.?)\s+[IVXivx]+\b',
        text, re.IGNORECASE
    ))


def _is_metadata(text: str, page_num: int) -> bool:
    """判断是否是元数据（作者名、日期等）。"""
    # 日期模式（任何页面）
    if re.match(r'^(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}$', text):
        return True
    # 作者名模式（短文本，看起来像人名）
    if len(text) < 30 and re.match(r'^[A-Z][a-z]+(\s+[A-Z]\.?)?\s+[A-Z][a-z]+$', text):
        return True
    # 引号包裹的标题（附录封面重复论文标题）
    if text.startswith('"') or text.startswith('\u201c'):
        return True
    return False


def _detect_appendix_boundary(headings: list[HeadingNode], spans: list[TextSpan]) -> int:
    """
    检测 Online Appendix 的起始位置。
    
    常见信号：
    - "Online Appendix" / "Appendix" 作为 level 0 标题
    - 论文标题重复出现（Online Appendix 封面）
    - 页码跳跃后出现 "Appendix A" 等
    """
    for idx, h in enumerate(headings):
        title_lower = h.title.lower()
        if any(kw in title_lower for kw in ['online appendix', 'appendix for', 'supplementary']):
            # 从这里开始都是附录
            return idx
        # "Appendix" 作为独立的高级标题（level 0 或 1）
        if title_lower.strip() in ('appendix', 'appendices') and h.level <= 1:
            return idx
    
    # 没有明确的附录标记
    return len(headings)


def _assign_parent_titles(headings: list[HeadingNode]):
    """为每个 heading 分配父标题，建立层级关系。"""
    # 用栈追踪当前的层级路径
    stack: list[HeadingNode] = []  # stack[i] = 当前 level i 的最近 heading
    
    for h in headings:
        # 弹出所有 level >= 当前 level 的（它们不再是父节点）
        while stack and stack[-1].level >= h.level:
            stack.pop()
        
        # 栈顶就是父节点
        if stack:
            h.parent_title = stack[-1].title
        
        stack.append(h)


def _build_sections_from_headings(
    spans: list[TextSpan],
    headings: list[HeadingNode],
    body_size: float,
    footnote_threshold: float,
    appendix_start_idx: int,
) -> dict[str, str]:
    """
    根据 heading 结构，从 spans 中提取每个 section 的文本内容。
    
    策略：
    - 正文文本（font_size ≈ body_size）直接拼接
    - 脚注文本（font_size < footnote_threshold）标记为 [footnote]
    - 保留层级关系在 key 中（如 "4. model-free analysis > 4.1 identification"）
    - 附录 section 加 "[appendix]" 前缀
    """
    sections: dict[str, str] = {}
    
    import logging
    _logger = logging.getLogger(__name__)

    for idx, heading in enumerate(headings):
        try:
            # 标记附录
            if idx >= appendix_start_idx:
                heading.is_appendix = True
            
            # 提取内容 spans
            content_spans = spans[heading.content_start:heading.content_end]
            
            # 构建文本：区分正文和脚注
            body_lines: list[str] = []
            footnote_lines: list[str] = []
            current_line_parts: list[str] = []
            last_y: float = -999
            last_page: int = -1
            is_footnote_zone = False
            
            for span in content_spans:
                # 跳过大字体（那是下一个 heading 的一部分，不应该出现在这里）
                if span.font_size > body_size + 0.5:
                    continue
                
                # 判断是否换行
                if span.page_num != last_page or abs(span.y_pos - last_y) > 2:
                    # 保存上一行
                    if current_line_parts:
                        line = " ".join(current_line_parts)
                        if is_footnote_zone:
                            footnote_lines.append(line)
                        else:
                            body_lines.append(line)
                    current_line_parts = []
                    
                    # 判断新行是否进入脚注区域
                    is_footnote_zone = span.font_size < footnote_threshold
                
                current_line_parts.append(span.text)
                last_y = span.y_pos
                last_page = span.page_num
            
            # 保存最后一行
            if current_line_parts:
                line = " ".join(current_line_parts)
                if is_footnote_zone:
                    footnote_lines.append(line)
                else:
                    body_lines.append(line)
            
            # 组装 section 内容
            content_parts = []
            # 标题行
            content_parts.append(heading.title)
            content_parts.append("")
            # 正文
            if body_lines:
                content_parts.extend(body_lines)
            # 脚注（如果有）
            if footnote_lines:
                content_parts.append("")
                content_parts.append("[Footnotes]")
                content_parts.extend(footnote_lines)
            
            content = "\n".join(content_parts).strip()
            
            # 构建 key
            key = _make_section_key_from_heading(heading)
            
            # 去重
            if key in sections:
                suffix = 2
                while f"{key} ({suffix})" in sections:
                    suffix += 1
                key = f"{key} ({suffix})"
            
            sections[key] = content

        except Exception as e:
            # C3 错误容忍：单个 section 解析失败不阻塞全局
            _logger.warning(
                "PDF section '%s' 解析失败 (non-fatal): %s",
                getattr(heading, 'title', f'heading_{idx}'),
                e,
            )
            continue
    
    return sections


def _make_section_key_from_heading(heading: HeadingNode) -> str:
    """
    从 HeadingNode 构建 section key。
    
    策略：
    - 保留数字编号（帮助 Agent 理解结构）
    - 附录加 "appendix:" 前缀
    - 子标题不加父标题前缀（避免 key 过长），但保留编号暗示层级
    """
    title = heading.title.strip()
    
    # 小写化
    key = title.lower()
    
    # 去除过长的 key
    if len(key) > 80:
        key = key[:80]
    
    # 附录前缀
    if heading.is_appendix:
        key = "appendix: " + key
    
    return key


def _postprocess_sections(sections: dict[str, str]) -> dict[str, str]:
    """
    后处理：
    1. 合并空壳父标题（如 "4 model-free analysis" 只有标题没有正文）
    2. 合并 Figure/Table 相关 section 为汇总 section
    3. 移除过短的无意义 section
    
    策略：先找到 "references" section 的位置，之后到 appendix 之前的内容
    大概率是 figures/tables 区域。
    """
    result: dict[str, str] = {}
    
    # 收集内容
    main_figures_tables: list[str] = []
    appendix_figures_tables: list[str] = []
    
    keys = list(sections.keys())
    
    # 找到 references 的位置（主文 references，不是 appendix 的）
    refs_idx = -1
    appendix_start_idx = len(keys)
    for i, key in enumerate(keys):
        if key == "references" and refs_idx == -1:
            refs_idx = i
        if key.startswith("appendix: ") and appendix_start_idx == len(keys):
            appendix_start_idx = i
    
    # 判断一个 section 是否是 figure/table 区域的内容
    def _is_figure_table_region(key: str, idx: int, content: str) -> bool:
        """判断是否属于 figure/table 区域。"""
        clean_key = key.replace("appendix: ", "")
        # 明确的 figure/table 标题
        if re.match(r'^(figure|table)\s', clean_key):
            return True
        # 在 references 之后、appendix 之前的非标准 section
        if refs_idx >= 0 and idx > refs_idx and idx < appendix_start_idx:
            # 不是标准学术 section 名称
            if not re.match(r'^\d', clean_key):  # 没有数字编号
                return True
        # 附录中的 figure/table
        if key.startswith("appendix: "):
            if re.match(r'^(figure|table)\s', clean_key):
                return True
            # 附录中的 "figure a.X:" 格式
            if re.match(r'^figure\s+a', clean_key) or re.match(r'^table\s+a', clean_key):
                return True
        return False
    
    for i, key in enumerate(keys):
        content = sections[key]
        is_appendix = key.startswith("appendix: ")
        
        # 检测 Figure/Table 区域
        if _is_figure_table_region(key, i, content):
            if is_appendix:
                appendix_figures_tables.append(content)
            else:
                main_figures_tables.append(content)
            continue
        
        # 检测空壳父标题：内容 < 50 chars（基本只有标题本身）
        content_without_title = "\n".join(content.split("\n")[2:]).strip()
        if len(content_without_title) < 30:
            # 看下一个 section 是否是它的子标题
            if i + 1 < len(keys):
                next_key = keys[i + 1]
                current_num = re.match(r'^(?:appendix: )?(\d+)\s', key)
                next_num = re.match(r'^(?:appendix: )?(\d+\.\d+)\s', next_key)
                if current_num and next_num and next_num.group(1).startswith(current_num.group(1) + "."):
                    continue
            # 内容极少，跳过
            if len(content_without_title) < 10:
                continue
        
        result[key] = content
    
    # 合并 Figure/Table sections
    if main_figures_tables:
        result["figures and tables"] = "\n\n---\n\n".join(main_figures_tables)
    if appendix_figures_tables:
        result["appendix: figures and tables"] = "\n\n---\n\n".join(appendix_figures_tables)
    
    return result


# ============================================================
# Fallback: 纯文本 + regex 方案（保留原有逻辑）
# ============================================================

def _extract_plain_text(path: Path) -> str:
    """使用 pymupdf 提取 PDF 全文（纯文本模式）。"""
    doc = _open_pdf(path)
    try:
        pages = []
        for page in doc:
            pages.append(page.get_text())
        return "\n".join(pages)
    finally:
        doc.close()


def _open_pdf(path: Path):
    """打开 PDF 文件，兼容 pymupdf 新旧版本。"""
    try:
        import pymupdf
        return pymupdf.open(str(path))
    except ImportError:
        pass
    try:
        import fitz
        return fitz.open(str(path))
    except ImportError:
        raise ImportError(
            "需要 pymupdf 来解析 PDF。安装: pip install pymupdf"
        )


# ============================================================
# Level 2: pdfplumber 布局感知提取（Two-column PDF 友好）
# ============================================================


def _extract_with_pdfplumber(path: Path) -> dict[str, str]:
    """
    使用 pdfplumber 提取文本并按 heading 分割。

    pdfplumber 的优势：
    - 天然按阅读顺序提取（对 two-column PDF 效果更好）
    - 表格提取能力更强
    - 对复杂布局有更好的字符定位

    Returns:
        dict[str, str] 或空 dict（提取失败时）
    """
    import pdfplumber

    text = _get_pdfplumber_full_text(path)
    if not text or len(text.strip()) < 200:
        return {}

    text = _clean_text(text)
    sections = _split_into_sections_regex(text)
    return sections


def _get_pdfplumber_full_text(path: Path) -> str:
    """使用 pdfplumber 提取 PDF 全文。"""
    import pdfplumber

    pages = []
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            try:
                page_text = page.extract_text(
                    layout=True,  # 保留布局，对 two-column 更好
                    x_tolerance=3,
                    y_tolerance=3,
                )
                if page_text:
                    pages.append(page_text)
            except Exception:
                # 单页提取失败不阻塞全局
                continue
    return "\n".join(pages)


def _clean_text(text: str) -> str:
    """基本清洗：去除多余空白、页码噪声等。"""
    # 去除孤立的页码行（常见噪声）
    text = re.sub(r"^\s*\d{1,4}\s*$", "", text, flags=re.MULTILINE)
    # 合并过多的空行
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    # 去除行首尾多余空格（但保留缩进结构）
    lines = text.split("\n")
    lines = [line.rstrip() for line in lines]
    text = "\n".join(lines)
    return text


def _split_into_sections_regex(text: str) -> dict[str, str]:
    """
    按 heading 分割文本（纯 regex fallback）。尝试多种模式：
    1. Markdown 风格 (## Heading)
    2. 学术论文数字编号 (1. Introduction, 2 Methods)
    3. 全大写独立行 (INTRODUCTION, METHODOLOGY)
    """
    sections: dict[str, str] = {}

    # 策略 1: Markdown heading
    heading_re = re.compile(r"^(#{1,4})\s+(.+)$", re.MULTILINE)
    matches = list(heading_re.finditer(text))

    if len(matches) >= 3:
        return _extract_sections_from_matches(text, matches, mode="markdown")

    # 策略 2: 数字编号 heading（学术论文最常见）
    numbered_re = re.compile(
        r"^(\d+(?:\.\d+)?\.?\s+)"
        r"((?:Introduction|Background|Related Work|Literature Review|"
        r"Theoretical|Theory|Hypothesis|Hypotheses|"
        r"Methodology|Methods?|Model|Framework|"
        r"Data|Sample|Variables?|Measures?|"
        r"Results?|Findings|Analysis|Empirical|"
        r"Discussion|Implications?|"
        r"Conclusion|Conclusions|Summary|"
        r"References?|Bibliography|"
        r"Acknowledgements?|"
        r"Appendix|Appendices)"
        r".*?)$",
        re.MULTILINE | re.IGNORECASE,
    )
    matches = list(numbered_re.finditer(text))

    if len(matches) >= 5:
        return _extract_sections_from_matches(text, matches, mode="numbered")

    # 策略 3: 独立成行的学术 section 名称
    academic_keywords = (
        r"Abstract|Introduction|Background|"
        r"Related (?:Work|Literature|Research)|Literature Review|"
        r"Theoretical (?:Framework|Background|Model)|Theory|"
        r"Hypothes[ie]s(?: Development)?|"
        r"Methodology|Methods?|Research Design|"
        r"Empirical (?:Framework|Strategy|Results?|Analysis)|"
        r"Setting|Institutional (?:Background|Setting)|"
        r"Data(?: and (?:Sample|Variables|Methods?))?|Sample|Variables?|Measures?|"
        r"(?:Main |Baseline )?Results?|Findings|Analysis|"
        r"(?:Heterogeneous )?Treatment Effects?|Robustness(?: Checks?)?|"
        r"Mechanism(?:s| Analysis)?|Extensions?|"
        r"Discussion|Implications?|Policy Implications|"
        r"Conclusions?|Summary|Concluding Remarks|"
        r"References?|Bibliography|"
        r"Acknowledgements?|"
        r"Appendi(?:x|ces)|"
        r"Structural (?:Analysis|Estimation|Model)|"
        r"Heterogeneity|"
        r"Quasi-Random (?:Assignment|Variation)|"
        r"Identi(?:ﬁ|fi)cation(?:\s+Strategy)?|"
        r"Model-Free (?:Analysis|Evidence)|"
        r"ROC Curves?|"
        r"Counterfactual(?:s| Analysis)?|"
        r"Welfare(?: Analysis)?|"
        r"Estimation(?:\s+Strategy)?|"
        r"Calibration"
    )
    title_case_re = re.compile(
        r"^(" + academic_keywords + r"(?:\s+.{0,40})?)$",
        re.MULTILINE | re.IGNORECASE,
    )
    matches = list(title_case_re.finditer(text))
    if len(matches) >= 3:
        return _extract_sections_from_matches(text, matches, mode="titlecase")

    # 策略 4: 全大写独立行
    upper_re = re.compile(
        r"^([A-Z][A-Z\s]{4,60})$",
        re.MULTILINE,
    )
    matches = list(upper_re.finditer(text))
    matches = [m for m in matches if len(m.group(1).strip()) > 5]

    if len(matches) >= 3:
        return _extract_sections_from_matches(text, matches, mode="upper")

    # 策略 5: 中文论文 heading
    chinese_re = re.compile(
        r"^([一二三四五六七八九十]+[、.．]\s*.+|"
        r"\d+[、.．]\s*[\u4e00-\u9fff].+)$",
        re.MULTILINE,
    )
    matches = list(chinese_re.finditer(text))

    if len(matches) >= 3:
        return _extract_sections_from_matches(text, matches, mode="chinese")

    # 退化：无法识别结构
    return {}


def _extract_sections_from_matches(
    text: str,
    matches: list,
    mode: str,
) -> dict[str, str]:
    """从 regex matches 提取 section 内容。"""
    sections: dict[str, str] = {}

    for i, match in enumerate(matches):
        raw_title = match.group(0).strip()
        if mode == "markdown":
            title = raw_title.lstrip("#").strip()
        elif mode == "numbered":
            title = raw_title
        elif mode == "upper":
            title = raw_title.title()
        elif mode == "titlecase":
            title = raw_title.strip()
        else:
            title = raw_title

        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        content = text[start:end].strip()

        key = _make_section_key(title)
        if key in sections:
            suffix = 2
            while f"{key} ({suffix})" in sections:
                suffix += 1
            key = f"{key} ({suffix})"
        sections[key] = content

    return sections


def _make_section_key(title: str) -> str:
    """将 title 转成小写 key，去除前缀数字和标点。"""
    key = re.sub(r"^\d+(?:\.\d+)?[.、．]?\s*", "", title)
    key = re.sub(r"^[一二三四五六七八九十]+[、.．]\s*", "", key)
    key = key.strip().lower()
    if len(key) > 60:
        key = key[:60]
    return key
