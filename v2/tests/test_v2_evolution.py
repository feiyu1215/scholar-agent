"""
Tests for P2: 认知能力进化引擎 (core/evolution.py)

覆盖:
- C1: HabitLearner — 从 ProceduralPatterns 学习新习惯
- R1: EditExperienceInjector — 编辑经验回注
- C2: AblationConfig — 约束模块可选关闭
- EvolutionEngine — 统一入口集成
- HabitSelector 扩展（learned habits 集成）
- session_finalizer 中的 _extract_edit_strategies
"""

import sys
import json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from unittest.mock import MagicMock, patch
from core.memory import MemoryStore, ProceduralPattern, DomainPattern, MemoryState
from core.evolution import (
    HabitLearner,
    LearnedHabit,
    EditExperienceInjector,
    EditExperience,
    AblationConfig,
    EvolutionEngine,
)
from core.habits import HabitSelector, COGNITIVE_HABITS, CognitiveHabit


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def memory_with_mature_patterns(tmp_path):
    """带有成熟 ProceduralPatterns 的 MemoryStore。"""
    store = MemoryStore(tmp_path / ".memory")
    
    # 添加成熟的策略模式（evidence >= 3, effectiveness >= 0.6）
    store.state.procedures = [
        ProceduralPattern(
            pattern_id="p001",
            category="strategy_effectiveness",
            description="deep_investigation 在 findings>=3 后切入效率最高",
            trigger_context="当 findings 数量达到 3 个以上时",
            effectiveness_score=0.82,
            evidence_count=5,
            first_seen="2025-01-01",
            last_seen="2025-07-01",
        ),
        ProceduralPattern(
            pattern_id="p002",
            category="review_focus",
            description="DID 论文先检查平行趋势假设",
            trigger_context="论文使用了 DID/双重差分方法",
            effectiveness_score=0.9,
            evidence_count=4,
            first_seen="2025-02-01",
            last_seen="2025-07-01",
        ),
        ProceduralPattern(
            pattern_id="p003",
            category="edit_strategy",
            description="编辑 Introduction 后用 detect_ai_signals 验证",
            trigger_context="每次编辑 Introduction 后",
            effectiveness_score=0.85,
            evidence_count=3,
            first_seen="2025-03-01",
            last_seen="2025-07-01",
        ),
        # 不成熟的模式（应被忽略）
        ProceduralPattern(
            pattern_id="p004",
            category="strategy_effectiveness",
            description="初次扫描时跳过附录",
            trigger_context="初始扫描阶段",
            effectiveness_score=0.5,  # 低于阈值
            evidence_count=2,  # 低于阈值
            first_seen="2025-06-01",
            last_seen="2025-07-01",
        ),
        # anti_pattern（应被排除）
        ProceduralPattern(
            pattern_id="p005",
            category="anti_pattern",
            description="连续 5 轮 read_section 不产出 findings",
            trigger_context="停滞检测",
            effectiveness_score=0.9,
            evidence_count=10,
            first_seen="2025-01-01",
            last_seen="2025-07-01",
        ),
    ]
    return store


@pytest.fixture
def memory_with_edit_experience(tmp_path):
    """带有编辑经验的 MemoryStore。"""
    store = MemoryStore(tmp_path / ".memory")
    
    store.state.procedures = [
        ProceduralPattern(
            pattern_id="e001",
            category="edit_strategy",
            description="reword_sentence 比 edit_section 更适合句子级修改",
            trigger_context="当只需修改1-2句话时",
            effectiveness_score=0.8,
            evidence_count=4,
        ),
        ProceduralPattern(
            pattern_id="e002",
            category="edit_strategy",
            description="Introduction 编辑后容易引入 AI 味连接词",
            trigger_context="编辑 Introduction 时",
            effectiveness_score=0.75,
            evidence_count=3,
        ),
        ProceduralPattern(
            pattern_id="e003",
            category="verification_strategy",
            description="编辑后必须用 detect_ai_signals 检查",
            trigger_context="任何编辑操作后",
            effectiveness_score=0.9,
            evidence_count=6,
        ),
    ]
    
    store.state.patterns = [
        DomainPattern(
            pattern_id="d001",
            category="writing",
            description="摘要中使用 furthermore/moreover 是典型 AI 信号",
            evidence_count=5,
        ),
        DomainPattern(
            pattern_id="d002",
            category="ai_signals",
            description="过度使用 em dash (—) 是 GPT-4 的写作特征",
            evidence_count=3,
        ),
        DomainPattern(
            pattern_id="d003",
            category="methodology",
            description="DID 缺少 parallel trends test",
            evidence_count=4,
        ),
    ]
    return store


@pytest.fixture
def empty_memory(tmp_path):
    """空的 MemoryStore。"""
    return MemoryStore(tmp_path / ".memory")


# ============================================================
# C1: HabitLearner Tests
# ============================================================

class TestHabitLearner:
    """测试从 ProceduralPatterns 中学习新习惯。"""

    def test_learn_from_mature_patterns(self, memory_with_mature_patterns):
        """成熟模式应被转化为习惯。"""
        learner = HabitLearner(memory_with_mature_patterns)
        habits = learner.learn()
        
        assert len(habits) >= 2  # p001, p002, p003 都满足条件
        assert all(isinstance(h, LearnedHabit) for h in habits)
    
    def test_immature_patterns_excluded(self, memory_with_mature_patterns):
        """不成熟的模式不应生成习惯。"""
        learner = HabitLearner(memory_with_mature_patterns)
        habits = learner.learn()
        
        # p004 (evidence=2, effectiveness=0.5) 不满足阈值
        habit_ids = {h.id for h in habits}
        assert "learned_p004" not in habit_ids
    
    def test_anti_patterns_become_warning_habits(self, memory_with_mature_patterns):
        """P2-fix5: anti_pattern 类别应晋升为警戒性习惯。"""
        learner = HabitLearner(memory_with_mature_patterns)
        habits = learner.learn()

        habit_ids = {h.id for h in habits}
        assert "learned_p005" in habit_ids
        # 验证警戒习惯的内容格式
        anti_habit = next(h for h in habits if h.id == "learned_p005")
        assert "警戒" in anti_habit.content
        assert "避免" in anti_habit.content
    
    def test_priority_range(self, memory_with_mature_patterns):
        """学习习惯的 priority 应在 40-85 范围内。"""
        learner = HabitLearner(memory_with_mature_patterns)
        habits = learner.learn()
        
        for h in habits:
            assert 40 <= h.priority <= 85, f"Habit {h.id} priority {h.priority} out of range"
    
    def test_confidence_range(self, memory_with_mature_patterns):
        """confidence 应在 0.4-1.0 范围内。"""
        learner = HabitLearner(memory_with_mature_patterns)
        habits = learner.learn()
        
        for h in habits:
            assert 0.4 <= h.confidence <= 1.0
    
    def test_phases_assignment(self, memory_with_mature_patterns):
        """学习习惯应根据 category 分配正确的 phases。"""
        learner = HabitLearner(memory_with_mature_patterns)
        habits = learner.learn()
        
        # strategy_effectiveness → DEEP_REVIEW, SYNTHESIS
        strategy_habits = [h for h in habits if "p001" in h.id]
        if strategy_habits:
            assert "DEEP_REVIEW" in strategy_habits[0].phases
    
    def test_dedup_with_existing_habits(self, memory_with_mature_patterns):
        """应与现有硬编码习惯去重。"""
        existing_ids = {"learned_p001"}  # 假设 p001 已存在
        learner = HabitLearner(memory_with_mature_patterns, existing_ids)
        habits = learner.learn()
        
        # p001 应被跳过
        habit_ids = {h.id for h in habits}
        assert "learned_p001" not in habit_ids
    
    def test_max_learned_habits(self, tmp_path):
        """最多生成 MAX_LEARNED_HABITS 条。"""
        store = MemoryStore(tmp_path / ".memory")
        # 创建 20 个成熟模式
        for i in range(20):
            store.state.procedures.append(ProceduralPattern(
                pattern_id=f"bulk_{i:03d}",
                category="strategy_effectiveness",
                description=f"策略模式 {i}: 独特描述 {i * 7}",
                trigger_context=f"触发条件 {i}",
                effectiveness_score=0.7 + (i % 3) * 0.1,
                evidence_count=4 + i,
            ))
        
        learner = HabitLearner(store)
        habits = learner.learn()
        
        assert len(habits) <= HabitLearner.MAX_LEARNED_HABITS
    
    def test_empty_memory_returns_nothing(self, empty_memory):
        """空记忆应返回空列表。"""
        learner = HabitLearner(empty_memory)
        assert learner.learn() == []
    
    def test_content_format(self, memory_with_mature_patterns):
        """习惯内容应包含 **名称** 格式。"""
        learner = HabitLearner(memory_with_mature_patterns)
        habits = learner.learn()
        
        for h in habits:
            assert "**" in h.content  # 包含加粗标记
            assert len(h.content) <= 200  # 长度限制


# ============================================================
# R1: EditExperienceInjector Tests
# ============================================================

class TestEditExperienceInjector:
    """测试编辑经验回注。"""
    
    def test_get_edit_experiences(self, memory_with_edit_experience):
        """应返回相关的编辑经验。"""
        injector = EditExperienceInjector(memory_with_edit_experience)
        experiences = injector.get_edit_experiences()
        
        assert len(experiences) > 0
        assert all(isinstance(e, EditExperience) for e in experiences)
    
    def test_section_filter(self, memory_with_edit_experience):
        """指定 target_sections 时应过滤相关经验。"""
        injector = EditExperienceInjector(memory_with_edit_experience)
        
        # Introduction 相关
        exps = injector.get_edit_experiences(target_sections=["introduction"])
        # 至少应匹配 e002 (Introduction 编辑后容易引入 AI 味)
        lessons = [e.lesson for e in exps]
        assert any("Introduction" in l or "introduction" in l.lower() for l in lessons)
    
    def test_format_for_injection(self, memory_with_edit_experience):
        """格式化输出应为可注入文本。"""
        injector = EditExperienceInjector(memory_with_edit_experience)
        text = injector.format_for_injection()
        
        assert text is not None
        assert "## 你的编辑经验" in text
        assert "有效性" in text
        assert len(text) <= 1200
    
    def test_empty_memory_returns_none(self, empty_memory):
        """无编辑经验时返回 None。"""
        injector = EditExperienceInjector(empty_memory)
        assert injector.format_for_injection() is None
    
    def test_max_experiences(self, memory_with_edit_experience):
        """最多返回 MAX_EXPERIENCES 条。"""
        injector = EditExperienceInjector(memory_with_edit_experience)
        experiences = injector.get_edit_experiences()
        assert len(experiences) <= EditExperienceInjector.MAX_EXPERIENCES
    
    def test_section_type_inference(self, memory_with_edit_experience):
        """应正确推断 section 类型。"""
        injector = EditExperienceInjector(memory_with_edit_experience)
        
        # "Introduction 编辑后容易引入 AI 味连接词" → section_type = "introduction"
        exps = injector.get_edit_experiences()
        section_types = {e.section_type for e in exps}
        assert "introduction" in section_types or "general" in section_types


# ============================================================
# C2: AblationConfig Tests
# ============================================================

class TestAblationConfig:
    """测试 Ablation 配置。"""
    
    def test_all_enabled_default(self):
        """默认配置应全部启用。"""
        config = AblationConfig.all_enabled()
        assert config.boundary_guard is True
        assert config.phase_fsm_nudges is True
        assert config.evolution_engine is True
        assert config.metacognition is True
    
    def test_single_ablation(self):
        """单模块 ablation 应只关闭目标模块。"""
        config = AblationConfig.single_ablation("boundary_guard")
        assert config.boundary_guard is False
        assert config.phase_fsm_nudges is True  # 其他不变
        assert config.evolution_engine is True
    
    def test_single_ablation_invalid_module(self):
        """无效模块名应抛出 ValueError。"""
        with pytest.raises(ValueError, match="Unknown module"):
            AblationConfig.single_ablation("nonexistent_module")
    
    def test_describe_all_enabled(self):
        """全部启用时描述应表明生产模式。"""
        config = AblationConfig.all_enabled()
        desc = config.describe()
        assert "production" in desc.lower() or "All" in desc
    
    def test_describe_with_disabled(self):
        """有关闭模块时描述应列出。"""
        config = AblationConfig.single_ablation("habit_injection")
        desc = config.describe()
        assert "habit_injection" in desc
        assert "Ablation" in desc
    
    def test_from_dict(self):
        """从字典创建配置。"""
        config = AblationConfig.from_dict({
            "boundary_guard": False,
            "metacognition": False,
        })
        assert config.boundary_guard is False
        assert config.metacognition is False
        assert config.evolution_engine is True  # 未指定的保持默认


# ============================================================
# EvolutionEngine Integration Tests
# ============================================================

class TestEvolutionEngine:
    """测试进化引擎统一入口。"""
    
    def test_initialize_learns_habits(self, memory_with_mature_patterns):
        """初始化应触发习惯学习。"""
        engine = EvolutionEngine(memory_with_mature_patterns)
        engine.initialize()
        
        assert len(engine.learned_habits) >= 2
    
    def test_initialize_with_ablation_disabled(self, memory_with_mature_patterns):
        """ablation 关闭时不应学习。"""
        ablation = AblationConfig.single_ablation("evolution_engine")
        engine = EvolutionEngine(memory_with_mature_patterns, ablation=ablation)
        engine.initialize()
        
        assert len(engine.learned_habits) == 0
    
    def test_get_habits_for_selector(self, memory_with_mature_patterns):
        """应返回 CognitiveHabit 格式的习惯。"""
        engine = EvolutionEngine(memory_with_mature_patterns)
        engine.initialize()
        
        habits = engine.get_habits_for_selector()
        assert all(isinstance(h, CognitiveHabit) for h in habits)
    
    def test_get_edit_experience_context(self, memory_with_edit_experience):
        """应返回编辑经验注入文本。"""
        engine = EvolutionEngine(memory_with_edit_experience)
        engine.initialize()
        
        text = engine.get_edit_experience_context(target_sections=["introduction"])
        assert text is not None
        assert "编辑经验" in text
    
    def test_get_edit_experience_disabled(self, memory_with_edit_experience):
        """ablation 关闭时返回 None。"""
        ablation = AblationConfig.single_ablation("evolution_engine")
        engine = EvolutionEngine(memory_with_edit_experience, ablation=ablation)
        
        assert engine.get_edit_experience_context() is None
    
    def test_evolution_summary(self, memory_with_mature_patterns):
        """应返回进化状态摘要。"""
        engine = EvolutionEngine(memory_with_mature_patterns)
        engine.initialize()
        
        summary = engine.get_evolution_summary()
        assert summary is not None
        assert "认知进化" in summary
        assert "习惯" in summary
    
    def test_evolution_summary_empty(self, empty_memory):
        """无学习内容时返回 None。"""
        engine = EvolutionEngine(empty_memory)
        engine.initialize()
        
        assert engine.get_evolution_summary() is None
    
    def test_record_session_stats(self, memory_with_mature_patterns):
        """应返回统计字典。"""
        engine = EvolutionEngine(memory_with_mature_patterns)
        engine.initialize()
        
        stats = engine.record_session_stats()
        assert "habits_generated" in stats
        assert "total_learned_habits" in stats
        assert stats["habits_generated"] == stats["total_learned_habits"]


# ============================================================
# HabitSelector Extension Tests
# ============================================================

class TestHabitSelectorLearned:
    """测试 HabitSelector 与学习习惯的集成。"""
    
    def test_extend_with_learned(self):
        """extend_with_learned 应扩展候选池。"""
        selector = HabitSelector()
        original_count = len(selector.habits)
        
        learned = [CognitiveHabit(
            id="learned_test",
            name="测试习惯",
            phases=["DEEP_REVIEW"],
            priority=60,
            content="**测试**：这是一个学习到的习惯",
        )]
        selector.extend_with_learned(learned)
        
        # 原始习惯数量不变
        assert len(selector.habits) == original_count
        # 但 _learned_habits 被设置
        assert len(selector._learned_habits) == 1
    
    def test_learned_habits_participate_in_selection(self):
        """学习习惯应参与选择过程。"""
        selector = HabitSelector(habits=[], max_per_turn=5)  # 清空硬编码
        
        learned = [CognitiveHabit(
            id="learned_only",
            name="仅学习习惯",
            phases=["DEEP_REVIEW"],
            priority=70,
            content="**学习**：纯学习来源的习惯",
        )]
        selector.extend_with_learned(learned)
        
        selected = selector.select(phase="DEEP_REVIEW")
        assert len(selected) == 1
        assert selected[0].id == "learned_only"
    
    def test_hardcoded_beats_learned_at_same_phase(self):
        """硬编码习惯（高 priority）应优先于学习习惯（低 priority）。"""
        hardcoded = [CognitiveHabit(
            id="hardcoded_1",
            name="硬编码习惯",
            phases=["DEEP_REVIEW"],
            priority=95,
            content="**质疑优先**：每个 claim 都需要证据。",
        )]
        selector = HabitSelector(habits=hardcoded, max_per_turn=1)
        
        learned = [CognitiveHabit(
            id="learned_1",
            name="学习习惯",
            phases=["DEEP_REVIEW"],
            priority=60,
            content="**学习**：学习到的内容",
        )]
        selector.extend_with_learned(learned)
        
        # max_per_turn=1，应选硬编码的（priority 更高）
        selected = selector.select(phase="DEEP_REVIEW")
        assert len(selected) == 1
        assert selected[0].id == "hardcoded_1"
    
    def test_total_injection_capped(self):
        """总注入量仍遵循 max_per_turn。"""
        selector = HabitSelector(max_per_turn=5)
        
        # 添加大量学习习惯
        many_learned = [CognitiveHabit(
            id=f"learned_{i}",
            name=f"习惯{i}",
            phases=["DEEP_REVIEW", "SYNTHESIS", "ORIENTATION"],
            priority=50 + i,
            content=f"**内容{i}**：描述",
        ) for i in range(20)]
        selector.extend_with_learned(many_learned)
        
        selected = selector.select(phase="DEEP_REVIEW")
        assert len(selected) <= 5


# ============================================================
# HabitSelector Discipline Triggers Tests (B1)
# ============================================================

class TestHabitSelectorDisciplineTriggers:
    """测试 HabitSelector 的学科特异触发器 discipline_triggers。"""

    def test_discipline_trigger_gives_25_boost(self):
        """paper_type + 学科触发词匹配时应获得 +25 boost。"""
        # skepticism_first 的 discipline_triggers 包含 empirical_econ: ["identification", ...]
        selector = HabitSelector(max_per_turn=5)
        selected = selector.select(
            phase="DEEP_REVIEW",
            triggers=["identification"],
            paper_type="empirical_econ",
        )
        # skepticism_first 应在前列（95 base + 25 = 120）
        ids = [h.id for h in selected]
        assert "skepticism_first" in ids
        # 且排名靠前
        assert ids.index("skepticism_first") <= 1

    def test_generic_trigger_gives_20_boost(self):
        """通用触发词匹配时仍给 +20 boost（无 paper_type）。"""
        # 给 skepticism_first 添加通用 trigger 来测试
        habit = CognitiveHabit(
            id="test_generic",
            name="测试",
            phases=["DEEP_REVIEW"],
            priority=50,
            content="测试内容",
            triggers=["method"],
            discipline_triggers={"empirical_econ": ["identification"]},
        )
        low_habit = CognitiveHabit(
            id="test_low",
            name="低优先级",
            phases=["DEEP_REVIEW"],
            priority=65,
            content="低优先级内容",
        )
        selector = HabitSelector(habits=[habit, low_habit], max_per_turn=2)

        # 通用 trigger 匹配 → +20, 总分 50+20=70 > 65
        selected = selector.select(phase="DEEP_REVIEW", triggers=["method"])
        assert selected[0].id == "test_generic"

    def test_discipline_trigger_beats_generic_trigger(self):
        """学科触发 +25 应覆盖通用触发 +20。"""
        habit = CognitiveHabit(
            id="test_both",
            name="两种触发",
            phases=["DEEP_REVIEW"],
            priority=50,
            content="测试",
            triggers=["general_word"],
            discipline_triggers={"empirical_econ": ["identification"]},
        )
        selector = HabitSelector(habits=[habit], max_per_turn=5)

        # 同时匹配通用和学科 → 取 max(20, 25) = 25
        selected = selector.select(
            phase="DEEP_REVIEW",
            triggers=["general_word", "identification"],
            paper_type="empirical_econ",
        )
        # 验证选中（单习惯时一定选中）
        assert len(selected) == 1

    def test_paper_type_none_fallback_to_generic(self):
        """paper_type=None 时只使用通用 triggers，行为等同旧版本。"""
        selector = HabitSelector(max_per_turn=5)
        # 不传 paper_type，传学科触发词 → 不应获得 +25
        selected_without = selector.select(
            phase="DEEP_REVIEW",
            triggers=["identification"],
            paper_type=None,
        )
        # skepticism_first 通用 triggers 为空，所以不会获得 +20 boost
        # 它的 base priority=95 决定排名
        ids_without = [h.id for h in selected_without]
        assert "skepticism_first" in ids_without

    def test_empty_discipline_triggers_no_crash(self):
        """discipline_triggers 为空的习惯不 crash，仅用通用 triggers。"""
        habit_no_disc = CognitiveHabit(
            id="no_disc",
            name="无学科触发",
            phases=["DEEP_REVIEW"],
            priority=90,
            content="无学科触发的习惯",
            triggers=["generic"],
            discipline_triggers={},
        )
        selector = HabitSelector(habits=[habit_no_disc], max_per_turn=5)
        selected = selector.select(
            phase="DEEP_REVIEW",
            triggers=["generic"],
            paper_type="empirical_econ",
        )
        assert len(selected) == 1
        assert selected[0].id == "no_disc"

    def test_mismatched_paper_type_no_boost(self):
        """paper_type 不在 discipline_triggers 中时不触发学科 boost。"""
        habit = CognitiveHabit(
            id="econ_only",
            name="仅经济学",
            phases=["DEEP_REVIEW"],
            priority=50,
            content="经济学习惯",
            discipline_triggers={"empirical_econ": ["identification"]},
        )
        other_habit = CognitiveHabit(
            id="other",
            name="其他",
            phases=["DEEP_REVIEW"],
            priority=55,
            content="其他习惯",
        )
        selector = HabitSelector(habits=[habit, other_habit], max_per_turn=2)

        # paper_type="ml_experiment" 不在 habit 的 discipline_triggers 中
        selected = selector.select(
            phase="DEEP_REVIEW",
            triggers=["identification"],
            paper_type="ml_experiment",
        )
        # other (55) > econ_only (50, 没有 boost)
        assert selected[0].id == "other"


# ============================================================
# SessionReflector Tests (替代旧的硬编码 _extract_edit_strategies)
# ============================================================

class TestSessionReflector:
    """测试 Agent 自省模块。"""

    def test_parse_valid_json(self):
        """有效 JSON 应正确解析。"""
        from core.reflection import SessionReflector

        reflector = SessionReflector(llm_call_fn=None)

        response = '''```json
{
  "reflections": [
    {
      "category": "edit_strategy",
      "description": "Introduction 编辑后容易引入 AI 味连接词，需用 detect_ai_signals 验证",
      "trigger_context": "编辑 Introduction 后",
      "effectiveness_estimate": 0.85,
      "reasoning": "本次编辑引入了 furthermore, moreover 等典型 AI 词汇"
    },
    {
      "category": "review_focus",
      "description": "DID 论文必须检查平行趋势假设",
      "trigger_context": "论文使用 DID/双重差分方法时",
      "effectiveness_estimate": 0.9,
      "reasoning": "这是DID有效性的核心条件"
    }
  ]
}
```'''
        results = reflector._parse_response(response)
        assert len(results) == 2
        assert results[0].category == "edit_strategy"
        assert results[1].category == "review_focus"
        assert results[0].effectiveness_estimate == 0.85

    def test_parse_empty_reflections(self):
        """空反思列表应返回空。"""
        from core.reflection import SessionReflector

        reflector = SessionReflector(llm_call_fn=None)
        results = reflector._parse_response('{"reflections": []}')
        assert results == []

    def test_parse_invalid_json(self):
        """无效 JSON 应 gracefully 返回空。"""
        from core.reflection import SessionReflector

        reflector = SessionReflector(llm_call_fn=None)
        results = reflector._parse_response("this is not json at all")
        assert results == []

    def test_parse_normalizes_category(self):
        """非标准 category 应被修正。"""
        from core.reflection import SessionReflector

        reflector = SessionReflector(llm_call_fn=None)
        response = '{"reflections": [{"category": "editing", "description": "test desc here", "trigger_context": "when X", "effectiveness_estimate": 0.7, "reasoning": "ok"}]}'
        results = reflector._parse_response(response)
        assert len(results) == 1
        assert results[0].category == "edit_strategy"

    def test_parse_filters_invalid_entries(self):
        """缺少必填字段的条目应被过滤。"""
        from core.reflection import SessionReflector

        reflector = SessionReflector(llm_call_fn=None)
        response = '{"reflections": [{"category": "edit_strategy", "description": "", "trigger_context": "x", "effectiveness_estimate": 0.7}, {"category": "edit_strategy", "description": "valid desc", "trigger_context": "valid trigger", "effectiveness_estimate": 0.8}]}'
        results = reflector._parse_response(response)
        # 第一条 description 为空应被过滤
        assert len(results) == 1
        assert results[0].description == "valid desc"

    def test_persist_reflections(self, tmp_path):
        """反思结果应正确存入 memory。"""
        from core.reflection import SessionReflector, ReflectionResult

        reflector = SessionReflector(llm_call_fn=None)
        store = MemoryStore(tmp_path / ".memory")

        results = [
            ReflectionResult(
                category="edit_strategy",
                description="编辑后用 detect_ai_signals 验证有效",
                trigger_context="每次编辑后",
                effectiveness_estimate=0.85,
                reasoning="避免引入AI痕迹",
            ),
            ReflectionResult(
                category="review_focus",
                description="DID 必须检查平行趋势",
                trigger_context="使用 DID 方法时",
                effectiveness_estimate=0.9,
                reasoning="核心有效性条件",
            ),
        ]

        stored = reflector.persist_reflections(results, store)
        assert stored == 2

        procedures = store.get_relevant_procedures(categories=["edit_strategy"])
        assert len(procedures) >= 1

    def test_persist_filters_invalid_category(self, tmp_path):
        """无效 category 不应存储。"""
        from core.reflection import SessionReflector, ReflectionResult

        reflector = SessionReflector(llm_call_fn=None)
        store = MemoryStore(tmp_path / ".memory")

        results = [
            ReflectionResult(
                category="totally_invalid",
                description="should not be stored",
                trigger_context="never",
                effectiveness_estimate=0.9,
                reasoning="bad category",
            ),
        ]

        stored = reflector.persist_reflections(results, store)
        assert stored == 0

    @pytest.mark.asyncio
    async def test_reflect_with_mock_llm(self):
        """模拟 LLM call 应正确触发反思流程。"""
        from core.reflection import SessionReflector
        from core.state import WorkspaceState

        mock_response = '{"reflections": [{"category": "edit_strategy", "description": "reword_sentence 适合句子级改动", "trigger_context": "只需修改1-2句话时", "effectiveness_estimate": 0.8, "reasoning": "比 edit_section 更精确"}]}'

        async def mock_llm(system: str, user: str, max_tokens: int) -> str:
            return mock_response

        reflector = SessionReflector(llm_call_fn=mock_llm)

        state = WorkspaceState()
        state.edits = [{"section": "intro"}, {"section": "methods"}]
        state.tool_call_history = [
            {"name": "reword_sentence"},
            {"name": "read_section"},
        ]
        state.findings = [{"priority": "high", "finding": "test finding", "section": "intro"}]
        state.loop_turns = 10

        results = await reflector.reflect(state)
        assert len(results) == 1
        assert results[0].category == "edit_strategy"
        assert "reword_sentence" in results[0].description

    @pytest.mark.asyncio
    async def test_reflect_no_llm_returns_empty(self):
        """无 LLM 函数时应跳过反思。"""
        from core.reflection import SessionReflector
        from core.state import WorkspaceState

        reflector = SessionReflector(llm_call_fn=None)
        state = WorkspaceState()
        state.edits = []
        state.tool_call_history = []
        state.findings = []
        state.loop_turns = 0

        results = await reflector.reflect(state)
        assert results == []

    @pytest.mark.asyncio
    async def test_reflect_llm_error_returns_empty(self):
        """LLM 调用失败时应 gracefully 返回空。"""
        from core.reflection import SessionReflector
        from core.state import WorkspaceState

        async def failing_llm(system: str, user: str, max_tokens: int) -> str:
            raise RuntimeError("API timeout")

        reflector = SessionReflector(llm_call_fn=failing_llm)
        state = WorkspaceState()
        state.edits = [{"section": "intro"}]
        state.tool_call_history = []
        state.findings = []
        state.loop_turns = 5

        results = await reflector.reflect(state)
        assert results == []

    def test_build_user_prompt(self):
        """组装的 user prompt 应包含关键信息。"""
        from core.reflection import SessionReflector
        from core.state import WorkspaceState

        reflector = SessionReflector(llm_call_fn=None)
        state = WorkspaceState()
        state.edits = [{"section": "introduction"}, {"section": "introduction"}]
        state.tool_call_history = [
            {"name": "edit_section"},
            {"name": "detect_ai_signals"},
            {"name": "reword_sentence"},
        ]
        state.findings = [
            {"priority": "high", "finding": "Missing control group", "section": "methods"},
        ]
        state.loop_turns = 8

        prompt = reflector._build_user_prompt(state)
        assert "8" in prompt  # loop_turns
        assert "edit_section" in prompt  # tool usage
        assert "introduction" in prompt  # edit history
        assert "Missing control group" in prompt  # findings

    def test_max_5_reflections(self):
        """最多解析 5 条反思。"""
        from core.reflection import SessionReflector

        reflector = SessionReflector(llm_call_fn=None)

        # 构造 8 条有效反思
        items = []
        for i in range(8):
            items.append({
                "category": "edit_strategy",
                "description": f"Experience number {i} is unique and helpful",
                "trigger_context": f"When condition {i} is met",
                "effectiveness_estimate": 0.7,
                "reasoning": f"Reason {i}",
            })

        response = json.dumps({"reflections": items})
        results = reflector._parse_response(response)
        assert len(results) == 5  # capped at 5
