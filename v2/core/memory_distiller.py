"""
core/memory_distiller.py — STOM 三层信息蒸馏器 (Phase 2)

实现 Session → Domain → Procedural 的自动信息蒸馏，
让 Agent 从"记住了什么"演化为"学到了什么"。

STOM 三层映射：
  L1 原始信息 → Session Memory 的 raw entries（对话+工具调用记录）
  L2 摘要信息 → Domain Knowledge 的结构化事实（"methodology: RCT with 200 participants"）
  L3 语义原子 → Procedural Memory 的可复用规则（"当 sample size < 30 时标记 power concern"）

蒸馏触发时机：
  - L1→L2: 每次会话结束时自动触发
  - L2→L3: 当累积足够的 Domain Facts + performance_signal 达标时触发

设计原则：
  - 蒸馏是有损压缩，不是无损转储
  - 去重先于积累（同一个 pattern 见多次 → 增加 evidence_count，不是新建记录）
  - 质量门控：表现不好的经验不应被固化为 Procedural Rule
  - 可审计：每条蒸馏产物附带 provenance（从哪条原始数据蒸馏得出）

依赖：
  - core/memory.py (MemoryState, SessionRecord, DomainPattern, ProceduralPattern)
  - LLM 调用（用于 summarization，但有 fallback 的规则蒸馏模式）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable
from datetime import datetime, timezone
import hashlib
import json

logger = logging.getLogger(__name__)


# ==============================================================
# 蒸馏相关数据类型
# ==============================================================

@dataclass
class DomainFact:
    """L2 层蒸馏产物：结构化的领域事实。

    比 DomainPattern 更细粒度——DomainPattern 是跨论文的模式归纳，
    DomainFact 是单次会话中提取的具体事实。多个 DomainFact 积累后
    可能被归纳为一个 DomainPattern。
    """
    category: str                    # methodology / statistics / logic / writing / citation
    subcategory: str = ""            # 更细分类（如 "RCT" / "DID" / "IV"）
    description: str = ""            # 事实描述
    evidence_text: str = ""          # 原始证据文本片段
    confidence: float = 0.8          # 蒸馏可信度 (0-1)
    source_session_id: str = ""      # 来源 session ID
    source_finding_ids: list[int] = field(default_factory=list)  # 来源 finding 索引


@dataclass
class ProceduralRule:
    """L3 层蒸馏产物：可复用的程序性规则。

    描述"在什么条件下做什么"的 IF-THEN 规则。
    """
    trigger: str                     # IF 条件描述（自然语言）
    action: str                      # THEN 动作描述
    rationale: str = ""              # 为什么这条规则有效
    effectiveness: float = 0.0       # 历史有效性评分 (0-1)
    evidence_sessions: list[str] = field(default_factory=list)  # 支撑的 session IDs
    counter_evidence: int = 0        # 反例次数


@dataclass
class DistillationResult:
    """一次蒸馏操作的结果"""
    domain_facts: list[DomainFact] = field(default_factory=list)
    procedural_rules: list[ProceduralRule] = field(default_factory=list)
    merged_count: int = 0            # 与已有知识合并的条数
    new_count: int = 0               # 新增的条数
    discarded_count: int = 0         # 被质量门控拦截的条数


# ==============================================================
# Token 估算器
# ==============================================================

class TokenEstimator:
    """基于规则的 Token 估算（替代粗糙的 len(text) // 3）。

    策略：
    - 优先使用 tiktoken（如果可用）
    - 降级使用经验规则：中文 ~1.5 token/字符，英文 ~0.25 token/单词
    - 带缓存，避免重复计算
    """

    def __init__(self, model: str = "gpt-4"):
        self._model = model
        self._encoder = None
        self._cache: dict[int, int] = {}
        self._try_load_encoder()

    def estimate(self, text: str) -> int:
        """估算文本的 token 数量。"""
        if not text:
            return 0

        text_hash = hash(text)
        if text_hash in self._cache:
            return self._cache[text_hash]

        if self._encoder:
            count = len(self._encoder.encode(text))
        else:
            count = self._rule_based_estimate(text)

        self._cache[text_hash] = count
        return count

    def estimate_messages(self, messages: list[dict]) -> int:
        """估算消息列表的总 token 数。"""
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total += self.estimate(content) + 4  # role/separator overhead
            elif isinstance(content, list):
                # multimodal messages
                for part in content:
                    if isinstance(part, dict) and "text" in part:
                        total += self.estimate(part["text"])
                total += 4
        return total

    def clear_cache(self) -> None:
        """清空缓存。"""
        self._cache.clear()

    def _try_load_encoder(self) -> None:
        """尝试加载 tiktoken encoder。"""
        try:
            import tiktoken
            self._encoder = tiktoken.encoding_for_model(self._model)
        except (ImportError, KeyError):
            self._encoder = None

    @staticmethod
    def _rule_based_estimate(text: str) -> int:
        """经验规则估算。比 len//3 更准确。

        逻辑：
        - CJK 字符：每个字符约 1.5 token
        - ASCII 单词：每个单词约 1.3 token
        - 混合内容加权平均
        """
        cjk_count = 0
        ascii_chars = []

        for char in text:
            if '\u4e00' <= char <= '\u9fff' or '\u3000' <= char <= '\u303f':
                cjk_count += 1
            else:
                ascii_chars.append(char)

        ascii_text = ''.join(ascii_chars)
        ascii_words = len(ascii_text.split())

        return int(cjk_count * 1.5 + ascii_words * 1.3 + 3)  # +3 for BOS/EOS


# ==============================================================
# LLM 调用协议（用于蒸馏时的 summarization）
# ==============================================================

@runtime_checkable
class LLMExtractor(Protocol):
    """蒸馏器依赖的 LLM 提取接口。

    由调用方注入具体实现（避免直接依赖特定 LLM client）。
    """
    async def extract_structured(self, prompt: str, schema: dict) -> list[dict]:
        """调用 LLM 提取结构化信息。"""
        ...


# ==============================================================
# 蒸馏器核心
# ==============================================================

class MemoryDistiller:
    """STOM 风格的多层信息蒸馏器。

    使用方式：
        distiller = MemoryDistiller(llm=my_llm_client)

        # 会话结束时：L1 → L2
        facts = await distiller.distill_session_to_domain(session_entries, findings)

        # 积累足够后：L2 → L3
        rules = await distiller.distill_domain_to_procedural(domain_facts, score=0.75)
    """

    def __init__(
        self,
        llm: LLMExtractor | None = None,
        rule_extraction_threshold: float = 0.6,
        max_facts_per_session: int = 10,
        max_rules_per_batch: int = 5,
    ):
        self.llm = llm
        self.rule_extraction_threshold = rule_extraction_threshold
        self.max_facts_per_session = max_facts_per_session
        self.max_rules_per_batch = max_rules_per_batch
        self.token_estimator = TokenEstimator()

    # ----------------------------------------------------------
    # L1 → L2：Session → Domain Facts
    # ----------------------------------------------------------

    async def distill_session_to_domain(
        self,
        findings: list[dict],
        session_id: str = "",
        tool_call_history: list[dict] | None = None,
    ) -> list[DomainFact]:
        """从会话产出中提取结构化的领域事实。

        两种模式：
        1. LLM 模式（精确但需要调用）：用 LLM 对 findings 做结构化提取
        2. 规则模式（降级 fallback）：基于 findings 的字段直接映射

        Args:
            findings: 会话中的 findings 列表
            session_id: 当前会话 ID
            tool_call_history: 工具调用历史（可选，用于丰富上下文）

        Returns:
            提取的 DomainFact 列表
        """
        if not findings:
            return []

        # 优先使用 LLM 提取
        if self.llm:
            try:
                return await self._llm_extract_facts(findings, session_id)
            except Exception as e:
                logger.warning("LLM fact extraction failed, falling back to rules: %s", e)

        # 规则模式 fallback
        return self._rule_extract_facts(findings, session_id)

    async def _llm_extract_facts(
        self, findings: list[dict], session_id: str
    ) -> list[DomainFact]:
        """使用 LLM 提取领域事实。"""
        prompt = self._build_fact_extraction_prompt(findings)
        schema = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {"type": "string"},
                    "subcategory": {"type": "string"},
                    "description": {"type": "string"},
                    "evidence_text": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["category", "description"],
            },
        }

        raw_results = await self.llm.extract_structured(prompt, schema)

        facts = []
        for item in raw_results[:self.max_facts_per_session]:
            facts.append(DomainFact(
                category=item.get("category", "unknown"),
                subcategory=item.get("subcategory", ""),
                description=item.get("description", ""),
                evidence_text=item.get("evidence_text", ""),
                confidence=item.get("confidence", 0.7),
                source_session_id=session_id,
            ))
        return facts

    def _rule_extract_facts(
        self, findings: list[dict], session_id: str
    ) -> list[DomainFact]:
        """基于规则的事实提取（不依赖 LLM）。"""
        facts = []
        for i, finding in enumerate(findings):
            # 只提取 verified 或 high priority 的 findings
            status = finding.get("status", "")
            priority = finding.get("priority", "")

            if status not in ("verified", "needs_verification") and priority != "high":
                continue

            category = finding.get("category", "general")
            description = finding.get("finding", "")[:200]

            if not description:
                continue

            facts.append(DomainFact(
                category=category,
                subcategory=finding.get("subcategory", ""),
                description=description,
                evidence_text=finding.get("evidence", "")[:300],
                confidence=0.7 if status == "verified" else 0.5,
                source_session_id=session_id,
                source_finding_ids=[i],
            ))

        return facts[:self.max_facts_per_session]

    # ----------------------------------------------------------
    # L2 → L3：Domain Facts → Procedural Rules
    # ----------------------------------------------------------

    async def distill_domain_to_procedural(
        self,
        domain_facts: list[DomainFact],
        performance_signal: float,
        existing_rules: list[ProceduralRule] | None = None,
    ) -> list[ProceduralRule]:
        """从领域事实中归纳可复用的程序性规则。

        仅当 performance_signal 达标时触发——表现不好的经验不应被固化为规则。

        Args:
            domain_facts: 已积累的领域事实
            performance_signal: 性能信号 (0-1)，如审稿质量评分
            existing_rules: 已有的规则（用于去重）

        Returns:
            新归纳的 ProceduralRule 列表
        """
        if performance_signal < self.rule_extraction_threshold:
            return []  # 质量门控

        if not domain_facts:
            return []

        # 优先使用 LLM
        if self.llm:
            try:
                return await self._llm_induce_rules(domain_facts, existing_rules)
            except Exception as e:
                logger.warning("LLM rule induction failed, falling back to rules: %s", e)

        # 规则模式 fallback
        return self._rule_induce_rules(domain_facts, existing_rules)

    async def _llm_induce_rules(
        self,
        domain_facts: list[DomainFact],
        existing_rules: list[ProceduralRule] | None,
    ) -> list[ProceduralRule]:
        """使用 LLM 归纳程序性规则。"""
        prompt = self._build_rule_induction_prompt(domain_facts, existing_rules)
        schema = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "trigger": {"type": "string"},
                    "action": {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": ["trigger", "action"],
            },
        }

        raw_results = await self.llm.extract_structured(prompt, schema)

        rules = []
        for item in raw_results[:self.max_rules_per_batch]:
            rules.append(ProceduralRule(
                trigger=item.get("trigger", ""),
                action=item.get("action", ""),
                rationale=item.get("rationale", ""),
                effectiveness=0.5,  # 初始中性评分
            ))
        return rules

    def _rule_induce_rules(
        self,
        domain_facts: list[DomainFact],
        existing_rules: list[ProceduralRule] | None,
    ) -> list[ProceduralRule]:
        """基于频率的规则归纳（不依赖 LLM）。

        逻辑：如果同一类别出现 3+ 次 domain facts，生成一条通用规则。
        """
        from collections import Counter

        category_counts = Counter(f.category for f in domain_facts)
        existing_triggers = set()
        if existing_rules:
            existing_triggers = {r.trigger for r in existing_rules}

        rules = []
        for category, count in category_counts.most_common(self.max_rules_per_batch):
            if count < 3:
                continue

            trigger = f"审阅论文时遇到 {category} 相关内容"
            if trigger in existing_triggers:
                continue

            # 取该类别中 confidence 最高的 fact 作为示例
            best_facts = sorted(
                [f for f in domain_facts if f.category == category],
                key=lambda f: f.confidence,
                reverse=True,
            )[:3]

            action = f"重点检查以下模式: {'; '.join(f.description[:60] for f in best_facts)}"

            rules.append(ProceduralRule(
                trigger=trigger,
                action=action,
                rationale=f"基于 {count} 次会话中的重复发现归纳",
                effectiveness=0.5,
                evidence_sessions=[f.source_session_id for f in best_facts if f.source_session_id],
            ))

        return rules

    # ----------------------------------------------------------
    # 去重与合并
    # ----------------------------------------------------------

    def deduplicate_and_merge(
        self,
        new_facts: list[DomainFact],
        existing_facts: list[DomainFact],
    ) -> tuple[list[DomainFact], int]:
        """将新 facts 与已有 facts 去重合并。

        策略：
        - 完全相同描述：只增加 evidence，不新建
        - 同类别 + 高相似描述：合并为更精炼的表述
        - 全新：直接添加

        Returns:
            (合并后的完整 fact 列表, 被合并的条数)
        """
        merged_count = 0
        result = list(existing_facts)

        for new_fact in new_facts:
            merged = False
            for existing in result:
                if self._is_duplicate(new_fact, existing):
                    # 合并：保留更高 confidence 的描述
                    if new_fact.confidence > existing.confidence:
                        existing.description = new_fact.description
                        existing.confidence = new_fact.confidence
                    merged_count += 1
                    merged = True
                    break

            if not merged:
                result.append(new_fact)

        return result, merged_count

    def _is_duplicate(self, a: DomainFact, b: DomainFact) -> bool:
        """判断两个 fact 是否是重复的。"""
        if a.category != b.category:
            return False

        # 基于描述文本的相似度（简单的 token overlap）
        tokens_a = set(a.description.lower().split())
        tokens_b = set(b.description.lower().split())

        if not tokens_a or not tokens_b:
            return False

        overlap = len(tokens_a & tokens_b)
        union = len(tokens_a | tokens_b)
        jaccard = overlap / union if union > 0 else 0

        return jaccard > 0.7

    # ----------------------------------------------------------
    # Prompt 构建
    # ----------------------------------------------------------

    def _build_fact_extraction_prompt(self, findings: list[dict]) -> str:
        """构建 L1→L2 的提取 prompt。"""
        findings_text = "\n".join(
            f"- [{f.get('category', '?')}] {f.get('finding', '')[:150]}"
            for f in findings[:20]
        )
        return (
            "你是一个学术审稿知识提取器。从以下审稿发现中提取结构化的领域事实。\n\n"
            "审稿发现：\n"
            f"{findings_text}\n\n"
            "请提取每条发现中的核心知识点，格式为：\n"
            "- category: 类别（methodology/statistics/logic/writing/citation）\n"
            "- subcategory: 子类别\n"
            "- description: 知识描述（简洁，<100字）\n"
            "- evidence_text: 支撑证据\n"
            "- confidence: 可信度 (0-1)\n\n"
            "只提取有普适价值的知识（可用于审其他论文），不要提取特定于本篇的细节。"
        )

    def _build_rule_induction_prompt(
        self, domain_facts: list[DomainFact], existing_rules: list[ProceduralRule] | None
    ) -> str:
        """构建 L2→L3 的规则归纳 prompt。"""
        facts_text = "\n".join(
            f"- [{f.category}] {f.description}"
            for f in domain_facts[:30]
        )
        existing_text = ""
        if existing_rules:
            existing_text = "\n已有规则（避免重复）：\n" + "\n".join(
                f"- IF {r.trigger} THEN {r.action}"
                for r in existing_rules[:10]
            )

        return (
            "你是一个学术审稿策略归纳器。从以下领域事实中归纳可复用的审稿策略规则。\n\n"
            "领域事实：\n"
            f"{facts_text}\n"
            f"{existing_text}\n\n"
            "请归纳 IF-THEN 形式的审稿策略规则：\n"
            "- trigger: 触发条件（什么情况下应该应用）\n"
            "- action: 具体动作（应该做什么）\n"
            "- rationale: 为什么有效\n\n"
            "规则要有普适性（适用于多篇论文），不要过于具体。每条规则独立可用。"
        )


# ==============================================================
# 桥接转换：DomainFact/ProceduralRule → MemoryState 数据类型
# ==============================================================

def domain_facts_to_patterns(facts: list[DomainFact]) -> list:
    """将蒸馏产出的 DomainFact 列表转换为 core.memory.DomainPattern 列表。

    桥接 memory_distiller（L2 output）和 memory.py（MemoryState 存储结构）。
    多个相同 category+description 的 facts 被归并为一个 pattern。
    """
    from core.memory import DomainPattern

    # 按 category+description hash 分组
    groups: dict[str, list[DomainFact]] = {}
    for fact in facts:
        key = hashlib.md5(f"{fact.category}:{fact.description}".encode()).hexdigest()[:12]
        groups.setdefault(key, []).append(fact)

    patterns = []
    for key, group in groups.items():
        representative = max(group, key=lambda f: f.confidence)
        patterns.append(DomainPattern(
            pattern_id=key,
            category=representative.category,
            description=representative.description,
            evidence_count=len(group),
            first_seen=group[0].source_session_id or "",
            last_seen=group[-1].source_session_id or "",
            examples=[f.source_session_id for f in group if f.source_session_id],
        ))
    return patterns


def procedural_rules_to_patterns(rules: list[ProceduralRule]) -> list:
    """将蒸馏产出的 ProceduralRule 列表转换为 core.memory.ProceduralPattern 列表。

    桥接 memory_distiller（L3 output）和 memory.py（MemoryState 存储结构）。
    """
    from core.memory import ProceduralPattern

    patterns = []
    for rule in rules:
        pattern_id = hashlib.md5(f"{rule.trigger}:{rule.action}".encode()).hexdigest()[:12]
        patterns.append(ProceduralPattern(
            pattern_id=pattern_id,
            category="strategy_effectiveness",
            description=f"IF {rule.trigger} THEN {rule.action}",
            trigger_context=rule.trigger,
            evidence_count=len(rule.evidence_sessions) if rule.evidence_sessions else 1,
            effectiveness_score=rule.effectiveness,
        ))
    return patterns
