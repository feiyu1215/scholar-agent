"""Tests for detect_scene() — scene auto-routing for DeAI rules."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.deai_engine import detect_scene, _is_chinese_text


# ─────────────────────────────────────────────────────────────────────────────
# Test: metadata-based routing (Priority 1)
# ─────────────────────────────────────────────────────────────────────────────

class TestMetadataRouting:
    """When metadata has 'discipline' field, it takes priority over text heuristics."""
    
    def test_metadata_economics_english(self):
        """Economics discipline in metadata → S3 regardless of text content."""
        meta = {"discipline": "Economics"}
        text = "This paper presents a novel deep learning architecture for NLP."
        assert detect_scene(text, metadata=meta) == "S3"
    
    def test_metadata_finance(self):
        meta = {"discipline": "Finance"}
        text = "We propose a transformer model for sequence labeling."
        assert detect_scene(text, metadata=meta) == "S3"
    
    def test_metadata_business(self):
        meta = {"discipline": "Business"}
        text = "Our CNN achieves state-of-the-art performance on ImageNet."
        assert detect_scene(text, metadata=meta) == "S3"
    
    def test_metadata_non_econ_with_chinese_text(self):
        """Non-economics discipline + Chinese text → S2 (language fallback)."""
        meta = {"discipline": "cs"}
        text = "本文提出了一种基于注意力机制的深度学习模型，用于自然语言处理中的文本分类任务。"
        assert detect_scene(text, metadata=meta) == "S2"
    
    def test_metadata_cs_discipline_english(self):
        """Non-economics discipline + English text → S1 (language fallback)."""
        meta = {"discipline": "cs"}
        text = "We propose a method for monetary policy analysis using panel data."
        # metadata says CS (non-economics), so text econ terms are ignored → route by language → English → S1
        assert detect_scene(text, metadata=meta) == "S1"
    
    def test_metadata_empty_discipline(self):
        """Empty discipline field falls through to text heuristics."""
        meta = {"discipline": ""}
        text = "This paper presents a novel deep learning architecture."
        # Empty discipline → falsy → falls through to heuristic
        assert detect_scene(text, metadata=meta) == "S1"
    
    def test_metadata_no_discipline_key(self):
        """Metadata without discipline key → text heuristics."""
        meta = {"format": ".md", "total_words": 5000}
        text = "We study the effects of monetary policy on aggregate demand."
        # Falls through to heuristic; "monetary policy" + "aggregate demand" = 2, below threshold
        assert detect_scene(text, metadata=meta) == "S1"
    
    def test_metadata_none(self):
        """None metadata → text heuristics."""
        text = "This paper presents a novel deep learning architecture."
        assert detect_scene(text, metadata=None) == "S1"


# ─────────────────────────────────────────────────────────────────────────────
# Test: English text heuristics (Priority 2 — non-Chinese)
# ─────────────────────────────────────────────────────────────────────────────

class TestEnglishHeuristics:
    """English text without metadata: CS → S1, Economics → S3."""
    
    def test_english_cs_paper(self):
        """Typical CS/AI paper text → S1."""
        text = """
        We propose a novel attention mechanism for natural language processing.
        Our transformer-based model achieves state-of-the-art results on 
        multiple benchmarks including GLUE, SuperGLUE, and SQuAD.
        The architecture uses multi-head self-attention with rotary positional
        embeddings to capture long-range dependencies.
        """
        assert detect_scene(text) == "S1"
    
    def test_english_econ_paper_clear(self):
        """Clearly economics paper → S3."""
        text = """
        We examine the effects of monetary policy on aggregate demand using
        a difference-in-differences approach. Our instrumental variable
        strategy exploits exogenous variation in interest rate changes.
        The panel data regression with fixed effects shows that quantitative
        easing significantly reduces unemployment rate in the short run.
        """
        assert detect_scene(text) == "S3"
    
    def test_english_econ_paper_finance(self):
        """Finance paper → S3."""
        text = """
        This paper contributes to the asset pricing literature by extending
        the Fama-French three-factor model. We document that credit risk
        and default probability are priced in the cross-section of stock
        returns. Our CAPM-based approach identifies significant yield curve
        factors that predict portfolio theory anomalies.
        """
        assert detect_scene(text) == "S3"
    
    def test_english_below_threshold(self):
        """Text with only 1-2 economics terms → stays S1 (threshold is 3)."""
        text = """
        We propose a deep reinforcement learning approach to optimize
        portfolio allocation. Our model uses a novel equilibrium constraint
        to ensure stability during training.
        """
        # "equilibrium" is one match — below threshold
        assert detect_scene(text) == "S1"
    
    def test_english_borderline_three_terms(self):
        """Exactly 3 economics terms → triggers S3."""
        text = """
        The fiscal policy intervention created significant externality effects
        in the regional oligopoly market structure.
        """
        # "fiscal policy", "externality", "oligopoly" = 3 matches
        assert detect_scene(text) == "S3"


# ─────────────────────────────────────────────────────────────────────────────
# Test: Chinese text heuristics (Priority 2 — Chinese)
# ─────────────────────────────────────────────────────────────────────────────

class TestChineseHeuristics:
    """Chinese text without metadata: general academic → S2, economics → S3."""
    
    def test_chinese_cs_paper(self):
        """Chinese CS paper → S2."""
        text = """
        本文提出了一种基于注意力机制的深度学习模型，用于自然语言处理中的
        文本分类任务。实验结果表明，我们的方法在多个基准数据集上取得了
        最优性能，相比之前的方法提升了显著的准确率。
        """
        assert detect_scene(text) == "S2"
    
    def test_chinese_econ_paper(self):
        """Chinese economics paper → S3."""
        text = """
        本文采用双重差分方法研究了货币政策对通货膨胀的影响。
        利用面板数据和工具变量策略，我们发现固定效应模型下
        财政政策的外生冲击对总需求产生了显著影响。
        """
        assert detect_scene(text) == "S3"
    
    def test_chinese_below_threshold(self):
        """Chinese text with only 1-2 economics terms → S2."""
        text = """
        本文研究了深度学习在金融风控领域的应用。我们提出了一种新的
        图神经网络模型，能够有效识别面板数据中的异常交易模式。
        该模型在信用评估任务上表现优异。
        """
        # "面板数据" = 1 match — below threshold of 3
        assert detect_scene(text) == "S2"
    
    def test_chinese_finance_paper(self):
        """Chinese finance paper with enough terms → S3."""
        text = """
        基于资产定价理论，本文研究了有效市场假说在中国A股市场的适用性。
        通过构建套利定价模型，我们发现市场存在显著的道德风险问题，
        这一逆向选择现象影响了资本配置效率。
        """
        # "资产定价", "有效市场", "套利定价", "道德风险", "逆向选择" = 5
        assert detect_scene(text) == "S3"


# ─────────────────────────────────────────────────────────────────────────────
# Test: Edge cases
# ─────────────────────────────────────────────────────────────────────────────

class TestEdgeCases:
    """Edge cases and boundary conditions."""
    
    def test_empty_text(self):
        """Empty text → S1 (default)."""
        assert detect_scene("") == "S1"
    
    def test_very_short_text(self):
        """Very short text → S1 (not enough signal)."""
        assert detect_scene("Hello world.") == "S1"
    
    def test_mixed_language_mostly_chinese(self):
        """Mixed text that's >30% CJK → treated as Chinese."""
        text = "本文采用BERT模型进行中文文本分类，使用了Transformer架构实现端到端训练。"
        assert _is_chinese_text(text) == True
        assert detect_scene(text) == "S2"
    
    def test_metadata_non_econ_overrides_text_heuristic(self):
        """Non-economics metadata + Chinese economics text → S2 (not S3).
        
        Metadata says 'cs', so economics heuristic is skipped. But the text
        IS Chinese, so language fallback gives S2."""
        text = "本文采用双重差分方法研究了货币政策对面板数据中固定效应的影响。"
        meta = {"discipline": "cs"}
        assert detect_scene(text, metadata=meta) == "S2"
    
    def test_case_insensitivity_english(self):
        """English economics terms are matched case-insensitively."""
        text = """
        The MONETARY POLICY changes led to significant FISCAL POLICY
        adjustments. ENDOGENEITY concerns were addressed via IV estimation.
        """
        assert detect_scene(text) == "S3"
    
    def test_metadata_discipline_case_insensitive(self):
        """Metadata discipline matching is case-insensitive."""
        meta = {"discipline": "ECONOMICS"}
        assert detect_scene("some English text here", metadata=meta) == "S3"
    
    def test_metadata_discipline_with_whitespace(self):
        """Metadata discipline is stripped."""
        meta = {"discipline": "  finance  "}
        assert detect_scene("some English text here", metadata=meta) == "S3"
    
    def test_econ_metadata_with_chinese_text(self):
        """Economics metadata + Chinese text → S3 (economics outranks language)."""
        meta = {"discipline": "经济"}
        text = "本文提出了一种基于注意力机制的深度学习模型。"
        assert detect_scene(text, metadata=meta) == "S3"
    
    def test_econ_heuristic_chinese_text(self):
        """Chinese economics text without metadata → S3 (not S2)."""
        text = "本文采用双重差分方法研究了货币政策对通货膨胀的影响，利用面板数据分析固定效应。"
        # "双重差分", "货币政策", "通货膨胀", "面板数据", "固定效应" = 5 terms
        assert detect_scene(text) == "S3"
    
    def test_econ_heuristic_english_text(self):
        """English economics text without metadata → S3 (not S1)."""
        text = "We use instrumental variable estimation to address endogeneity in the panel data fixed effects model."
        # "instrumental variable", "endogeneity", "panel data", "fixed effects" = 4 terms
        assert detect_scene(text) == "S3"
