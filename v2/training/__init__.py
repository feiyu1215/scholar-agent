"""
v2/training/ — Phase 7: 对抗式自我训练 (Adversarial Self-Training)

本模块实现 ScholarAgent 的自我训练闭环系统:
  - 弱点分析: 从 MetaHarness / Memory / FailureStore 中提取系统性弱点
  - 对抗样本生成: 基于弱点自动生成具有挑战性的论文片段
  - 课程学习: 从易到难的自适应训练课程设计
  - 训练循环: 自动化训练 → 评估 → 学习闭环
  - 对抗样本库: 持久化的 "考试题库" 支持回归测试
  - 红蓝对抗: 双 Agent 互相提升的竞争机制

Kill Switch: SCHOLAR_GODEL_ADVERSARIAL_TRAINING (默认 ON)
    OFF 时所有模块方法变为 no-op/空返回，不影响审稿核心流程。

与其他 Phase 的集成:
    - Phase 4 (Skill Synthesis): 训练中的失败触发 Skill 合成
    - Phase 5 (MetaHarness): 读取 Bottleneck + BatchResult 识别弱点
    - Phase 6 (Reflection): 训练结果流入反思系统
    - Phase 8 (DualLoop): 外环观察训练进展调整策略
    - Memory: 新经验写入三层记忆系统
    - EventBus: 发布训练相关事件供全局订阅
"""

from training.weakness_analyzer import (
    WeaknessAnalyzer,
    WeaknessProfile,
    WeaknessDimension,
    WeaknessSource,
)
from training.adversarial import (
    AdversarialGenerator,
    AdversarialCase,
    ChallengeType,
    DifficultyLevel,
    DifficultyController,
    MultiDimensionChallengeFactory,
)
from training.curriculum import (
    CurriculumDesigner,
    TrainingCurriculum,
    CurriculumStage,
    LearningCurveTracker,
    DifficultyGradient,
)
from training.training_loop import (
    TrainingLoop,
    TrainingSession,
    TrainingConfig,
    ConvergenceDetector,
    TrainingResult,
)
from training.adversarial_library import (
    AdversarialLibrary,
    LibraryEntry,
    LibraryIndex,
    RegressionSuiteGenerator,
)
from training.red_blue_arena import (
    RedTeam,
    BlueTeam,
    ArenaOrchestrator,
    ArenaMatch,
    EloRating,
    MatchOutcome,
    RedStrategy,
    BlueStrategy,
    MatchJudge,
    SeasonConfig,
    SeasonSummary,
)


__all__ = [
    # Weakness Analyzer
    "WeaknessAnalyzer",
    "WeaknessProfile",
    "WeaknessDimension",
    "WeaknessSource",
    # Adversarial Generator
    "AdversarialGenerator",
    "AdversarialCase",
    "ChallengeType",
    "DifficultyLevel",
    "DifficultyController",
    "MultiDimensionChallengeFactory",
    # Curriculum
    "CurriculumDesigner",
    "TrainingCurriculum",
    "CurriculumStage",
    "LearningCurveTracker",
    "DifficultyGradient",
    # Training Loop
    "TrainingLoop",
    "TrainingSession",
    "TrainingConfig",
    "ConvergenceDetector",
    "TrainingResult",
    # Library
    "AdversarialLibrary",
    "LibraryEntry",
    "LibraryIndex",
    "RegressionSuiteGenerator",
    # Red Blue Arena
    "RedTeam",
    "BlueTeam",
    "ArenaOrchestrator",
    "ArenaMatch",
    "EloRating",
    "MatchOutcome",
    "RedStrategy",
    "BlueStrategy",
    "MatchJudge",
    "SeasonConfig",
    "SeasonSummary",
]
