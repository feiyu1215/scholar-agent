"""tool_handlers/reading.py — 阅读类工具的执行逻辑。

提取自 Harness._tool_read_section, _tool_search_literature,
_tool_fetch_paper_detail, _tool_read_reference。
"""
from __future__ import annotations

import re
from typing import Any

from core.claim_signal import detect_verifiable_claims
from core.post_edit_verify import extract_voice, VoiceFingerprint


# ============================================================
# Section Digest Generator — 纯启发式，不调 LLM
# ============================================================

def _generate_section_digest(section_name: str, content: str) -> str:
    """
    为已读 section 生成一个 1-2 句话的结构化摘要。

    设计原则:
    - 纯启发式（不调 LLM），零额外 API 成本
    - 目标：让 Agent 在 section 原文被压缩出 messages 后，
      仍能回溯"这个 section 讲了什么"而不需要重新 read_section
    - 不是完美的摘要——是"够用的记忆锚点"
    """
    if not content or len(content) < 50:
        return f"({len(content)} chars, 内容极少)"

    # 去掉 markdown 标题行
    lines = [l.strip() for l in content.split("\n") if l.strip() and not l.strip().startswith("#")]

    # 提取第一句有实质内容的文本（跳过空行和表格标记）
    first_sentence = ""
    for line in lines:
        if line.startswith("|") or line.startswith("![") or line.startswith("---"):
            continue
        for end_char in ["。", ".", "?", "？", "!", "！"]:
            idx = line.find(end_char)
            if idx > 10:
                first_sentence = line[:idx + 1]
                break
        if first_sentence:
            break
        if len(line) > 20:
            first_sentence = line[:100]
            break

    if not first_sentence:
        first_sentence = lines[0][:80] if lines else "无内容"

    # 统计特征
    features = []
    table_rows = sum(1 for l in content.split("\n") if l.strip().startswith("|"))
    if table_rows > 2:
        features.append(f"含{table_rows}行表格")

    num_count = len(re.findall(r'\d+\.\d+', content))
    if num_count > 5:
        features.append(f"~{num_count}个数值")

    # 组装 digest (≤150 chars)
    digest = first_sentence[:120]
    if features:
        digest += f" [{', '.join(features)}]"

    return digest[:150]


# ============================================================
# tool_read_section
# ============================================================

def tool_read_section(args: dict, state: Any, offload_store: Any) -> str:
    """读取论文 section 内容，支持续读和模糊匹配。"""
    section = args.get("section", "").lower().strip()
    offset = args.get("offset", 0) or 0  # Phase 18: 续读支持
    sections = state.paper_sections

    # Phase 14/16/20/32: 追踪已读 sections
    def _record_read(resolved_name: str, content: str):
        if resolved_name not in state.sections_read:
            state.sections_read.append(resolved_name)
            # Phase 32: 首次读取时 offload 完整内容到外部文件
            if offload_store.should_offload(content, "read_section"):
                digest = state.section_digests.get(resolved_name, content[:80])
                offload_store.offload(
                    tool_name="read_section",
                    key=resolved_name,
                    content=content,
                    summary=digest,
                    loop_turn=state.loop_turns,
                )
        # Phase 16: 生成 2 句话 digest（纯启发式，不调 LLM）
        if resolved_name not in state.section_digests:
            state.section_digests[resolved_name] = _generate_section_digest(resolved_name, content)
        # Phase 20: 提取并累积作者写作风格指纹
        if len(content) >= 200:
            section_fp = extract_voice(content)
            if section_fp.total_words_analyzed > 0:
                if state.voice_profile is None:
                    state.voice_profile = section_fp
                else:
                    existing = state.voice_profile
                    total = existing.total_words_analyzed + section_fp.total_words_analyzed
                    w1 = existing.total_words_analyzed / total
                    w2 = section_fp.total_words_analyzed / total
                    state.voice_profile = VoiceFingerprint(
                        avg_sentence_length=round(existing.avg_sentence_length * w1 + section_fp.avg_sentence_length * w2, 1),
                        sentence_length_std=round(existing.sentence_length_std * w1 + section_fp.sentence_length_std * w2, 1),
                        passive_ratio=round(existing.passive_ratio * w1 + section_fp.passive_ratio * w2, 2),
                        hedge_frequency=round(existing.hedge_frequency * w1 + section_fp.hedge_frequency * w2, 2),
                        total_words_analyzed=total,
                    )

        # S5: PCG 被动同步 — Agent 读取 section 时自动更新 PCG 节点状态
        # 修复断路点: update_after_read() 注释说"由 reading.py 调用"但从未被实际调用
        pcg = getattr(state, "paper_cognition_graph", None)
        if pcg and hasattr(pcg, "update_after_read"):
            digest = state.section_digests.get(resolved_name, "")
            pcg.update_after_read(resolved_name, digest=digest, read_depth="read")

    # Phase 18: 统一的截断+续读返回逻辑
    WINDOW = 6000

    def _windowed_return(resolved_name: str, content: str) -> str:
        """返回 content[offset:offset+WINDOW]，必要时附带续读提示。"""
        total = len(content)
        if offset >= total:
            return (
                f"[已到达 section '{resolved_name}' 末尾] "
                f"全文 {total} 字符，offset={offset} 已超出范围。无需续读。"
            )
        chunk = content[offset:offset + WINDOW]
        end_pos = offset + len(chunk)
        remaining = total - end_pos

        # Phase 31: 检测 chunk 中的 verifiable claims
        claim_signal = detect_verifiable_claims(chunk)

        if remaining <= 0:
            if offset > 0:
                result = f"[续读 {resolved_name}, 字符 {offset}-{end_pos}/{total}]\n\n{chunk}"
            else:
                result = chunk
            return result + claim_signal if claim_signal else result
        else:
            hint = (
                f"\n\n[... 已显示字符 {offset}-{end_pos}，"
                f"剩余 {remaining} 字符 (共 {total})。"
                f"如需继续阅读，调用 read_section(section=\"{resolved_name}\", offset={end_pos}) ...]"
            )
            if offset > 0:
                result = f"[续读 {resolved_name}, 字符 {offset}-{end_pos}/{total}]\n\n{chunk}{hint}"
            else:
                result = chunk + hint
            return result + claim_signal if claim_signal else result

    if section == "list":
        names = [k for k in sections if k != "full"]
        lines = [f"可用 sections ({len(names)}):"]
        for name in names:
            char_count = len(sections[name])
            lines.append(f"  - {name} ({char_count} 字符)")
        return "\n".join(lines)
    elif section == "full":
        full = sections.get("full", "")
        if not full:
            return "没有全文。请用 read_section('list') 查看可用 sections，逐段读取。"
        if len(full) > 12000:
            names = [k for k in sections if k != "full"]
            return (
                full[:3000]
                + f"\n\n[... 论文共 {len(full)} 字符，已截断。"
                f"可用 sections: {', '.join(names[:10])}。"
                f"请用 read_section 按需读取具体 section，避免全文注入浪费 token。 ...]"
            )
        return full
    else:
        # 1. 精确匹配
        if section in sections:
            content = sections[section]
            if len(content) < 50:
                return (
                    f"[注意] Section '{section}' 内容极少（仅 {len(content)} 字符: \"{content.strip()}\"）。"
                    f"这可能是一个空壳子标题，实际内容在其子 section 中。"
                    f"建议读取其他相关 section。"
                )
            _record_read(section, content)
            return _windowed_return(section, content)

        # 2. 模糊匹配
        candidates = []
        for key in sections:
            if key == "full":
                continue
            if section in key.lower() or key.lower() in section:
                candidates.append(key)

        if candidates:
            best = max(candidates, key=len)
            content = sections[best]
            if len(content) < 50:
                return (
                    f"[注意] Section '{best}' 内容极少（仅 {len(content)} 字符: \"{content.strip()}\"）。"
                    f"这可能是一个空壳子标题，实际内容在其子 section 中。"
                    f"建议读取其他相关 section。"
                )
            _record_read(best, content)
            return _windowed_return(best, content)

        # 3. 尝试数字匹配
        for key in sections:
            if key.startswith(section + ".") or key.startswith(section + " "):
                content = sections[key]
                _record_read(key, content)
                return _windowed_return(key, content)
        available = ", ".join(k for k in sections.keys() if k != "full")
        return f"未找到 section '{section}'。可用: {available}"


# ============================================================
# tool_search_literature
# ============================================================

def tool_search_literature(args: dict, state: Any, offload_store: Any, search_log: list) -> str:
    """搜索学术文献。"""
    query = args.get("query", "")
    reason = args.get("reason", "")
    try:
        from core.web_search import intelligent_search
        response = intelligent_search(query, limit=5)
        search_log.append({
            "query": query,
            "reason": reason,
            "results_count": len(response.results),
            "source": response.source,
            "loop_turn": state.loop_turns,
        })
        if not response.results:
            return f"搜索 '{query}' 无结果。{response.error or ''}\n原因: {reason}"
        lines = [f"搜索 '{query}' 的结果 (来源: {response.source}, 共 {response.total_found} 条):"]
        for i, r in enumerate(response.results, 1):
            authors = ", ".join(r.authors[:3])
            if len(r.authors) > 3:
                authors += " et al."
            lines.append(f"  [{i}] {r.title} ({r.year or '?'})")
            lines.append(f"      作者: {authors} | 发表于: {r.venue or '?'} | 引用: {r.citation_count or 'N/A'}")
            if r.abstract:
                lines.append(f"      摘要: {r.abstract[:150]}...")
        result = "\n".join(lines)

        # Phase 32: offload 搜索结果
        if offload_store.should_offload(result, "search_literature"):
            summary = f"搜索'{query}'得到{len(response.results)}条结果"
            offload_store.offload(
                tool_name="search_literature",
                key=query,
                content=result,
                summary=summary,
                loop_turn=state.loop_turns,
            )

        return result
    except Exception as e:
        search_log.append({
            "query": query,
            "reason": reason,
            "results_count": 0,
            "source": "error",
            "loop_turn": state.loop_turns,
            "error": str(e),
        })
        return f"搜索失败 ({type(e).__name__}: {e})。你可以基于已有知识继续判断，或标记为 'needs_verification'。"


# ============================================================
# tool_fetch_paper_detail
# ============================================================

def tool_fetch_paper_detail(args: dict, state: Any, offload_store: Any) -> str:
    """Phase 57: 获取外部论文的详细信息，存入参考文献工作区。"""
    paper_id = args.get("paper_id")
    doi = args.get("doi")
    title = args.get("title")
    reason = args.get("reason", "")

    if not any([paper_id, doi, title]):
        return "必须提供 paper_id、doi 或 title 中的至少一个。"

    try:
        from core.web_search import fetch_paper_detail as _fetch_detail
        detail = _fetch_detail(paper_id=paper_id, doi=doi, title=title)

        if detail.error:
            return f"获取论文详情失败: {detail.error}\n原因: {reason}"

        # 存入参考文献工作区
        store_key = detail.paper_id or title or doi or "unknown"
        state.reference_papers[store_key] = {
            "title": detail.title,
            "authors": detail.authors,
            "year": detail.year,
            "venue": detail.venue,
            "abstract": detail.abstract,
            "tldr": detail.tldr,
            "citation_count": detail.citation_count,
            "reference_count": detail.reference_count,
            "influential_citation_count": detail.influential_citation_count,
            "fields_of_study": detail.fields_of_study,
            "key_references": detail.key_references,
            "key_citations": detail.key_citations,
            "fetched_at_turn": state.loop_turns,
            "fetch_reason": reason,
        }

        # 格式化返回
        lines = [f"📄 论文详情: {detail.title}"]
        lines.append(f"   作者: {', '.join(detail.authors[:5])}")
        lines.append(f"   年份: {detail.year or '?'} | 发表于: {detail.venue or '?'}")
        lines.append(f"   引用: {detail.citation_count or 'N/A'} (其中 influential: {detail.influential_citation_count or 'N/A'})")
        lines.append(f"   参考文献数: {detail.reference_count or 'N/A'}")
        if detail.fields_of_study:
            lines.append(f"   领域: {', '.join(detail.fields_of_study)}")

        if detail.tldr:
            lines.append(f"\n   TLDR: {detail.tldr}")

        if detail.abstract:
            lines.append(f"\n   完整摘要: {detail.abstract}")

        if detail.key_references:
            lines.append(f"\n   关键参考文献 (该论文引用的高影响力论文, top {len(detail.key_references)}):")
            for i, ref in enumerate(detail.key_references[:7], 1):
                lines.append(f"     [{i}] {ref['title']} ({ref['year']}, {ref['venue']})")

        if detail.key_citations:
            lines.append(f"\n   关键后续引用 (引用该论文的高影响力论文, top {len(detail.key_citations)}):")
            for i, cit in enumerate(detail.key_citations[:7], 1):
                lines.append(f"     [{i}] {cit['title']} ({cit['year']}, {cit['venue']})")

        lines.append(f"\n   [已存入参考文献工作区，共 {len(state.reference_papers)} 篇]")

        result = "\n".join(lines)

        # Offload if too long
        if offload_store.should_offload(result, "fetch_paper_detail"):
            summary = f"获取了'{detail.title}'的详情 (TLDR: {(detail.tldr or '')[:60]})"
            offload_store.offload(
                tool_name="fetch_paper_detail",
                key=detail.title or store_key,
                content=result,
                summary=summary,
                loop_turn=state.loop_turns,
            )

        return result

    except Exception as e:
        return f"获取论文详情时出错 ({type(e).__name__}: {e})。你可以基于搜索结果中的摘要继续判断。"


# ============================================================
# tool_read_reference
# ============================================================

def tool_read_reference(args: dict, state: Any) -> str:
    """Phase 58: 读取用户提供的参考文献内容。"""
    ref_id = args.get("ref_id", "")
    section = args.get("section", "")
    offset = args.get("offset", 0)
    max_chars = args.get("max_chars", 3000)

    # 如果没有用户参考文献
    if not state.user_reference_docs:
        return "当前没有用户提供的参考文献。参考文献工作区中的论文来自 Agent 搜索，请用 fetch_paper_detail 获取详情。"

    # 如果没指定 ref_id，列出所有可用的参考文献
    if not ref_id:
        lines = ["可用的参考文献:"]
        for rid, doc in state.user_reference_docs.items():
            sections_str = ", ".join(doc["section_names"][:10])
            lines.append(f"  • {rid}: {doc['title']} (sections: {sections_str})")
        lines.append("\n用 read_reference(ref_id='ref_1', section='abstract') 读取具体内容。")
        return "\n".join(lines)

    # 查找指定的参考文献
    if ref_id not in state.user_reference_docs:
        available = ", ".join(state.user_reference_docs.keys())
        return f"未找到参考文献 '{ref_id}'。可用的 ref_id: {available}"

    doc = state.user_reference_docs[ref_id]

    # 如果没指定 section，列出该文献的所有 sections
    if not section:
        lines = [f"📎 {doc['title']} 的可用 sections:"]
        for sec_name in doc["section_names"]:
            char_count = len(doc["sections"].get(sec_name, ""))
            lines.append(f"  • {sec_name} ({char_count}字)")
        lines.append(f"\n用 read_reference(ref_id='{ref_id}', section='<name>') 读取具体 section。")
        return "\n".join(lines)

    # 模糊匹配 section 名
    matched_section = None
    section_lower = section.lower().strip()
    for sec_name in doc["section_names"]:
        if sec_name.lower() == section_lower:
            matched_section = sec_name
            break
    if not matched_section:
        for sec_name in doc["section_names"]:
            if section_lower in sec_name.lower() or sec_name.lower() in section_lower:
                matched_section = sec_name
                break
    if not matched_section:
        available = ", ".join(doc["section_names"])
        return f"在 '{ref_id}' 中未找到 section '{section}'。可用: {available}"

    # 读取内容
    content = doc["sections"].get(matched_section, "")
    total_chars = len(content)

    if offset >= total_chars:
        return f"offset {offset} 超出 section '{matched_section}' 的总长度 ({total_chars}字)。"

    chunk = content[offset:offset + max_chars]
    remaining = total_chars - offset - len(chunk)

    header = f"📎 [{ref_id}] {doc['title']} → section: {matched_section}\n"
    header += f"   ({total_chars}字, 当前 offset={offset}, 返回 {len(chunk)}字"
    if remaining > 0:
        header += f", 剩余 {remaining}字 — 用 offset={offset + len(chunk)} 续读"
    header += ")\n\n"

    return header + chunk
