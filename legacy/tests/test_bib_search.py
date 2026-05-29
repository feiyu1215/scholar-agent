"""Tests for tools/bib_search.py — Local bibliography search (C-3)."""

import pytest
from pathlib import Path

# ── Sample .bib content for testing ──────────────────────────────────────────

SAMPLE_BIB = r"""
@article{vaswani2017attention,
    title = {Attention Is All You Need},
    author = {Vaswani, Ashish and Shazeer, Noam and Parmar, Niki and Uszkoreit, Jakob and Jones, Llion and Gomez, Aidan N. and Kaiser, Lukasz and Polosukhin, Illia},
    year = {2017},
    journal = {Advances in Neural Information Processing Systems},
    volume = {30},
    doi = {10.5555/3295222.3295349},
    abstract = {The dominant sequence transduction models are based on complex recurrent or convolutional neural networks that include an encoder and a decoder. The best performing models also connect the encoder and decoder through an attention mechanism. We propose a new simple network architecture, the Transformer, based solely on attention mechanisms.},
    keywords = {transformer, attention, self-attention, neural machine translation},
}

@inproceedings{devlin2019bert,
    title = {BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding},
    author = {Devlin, Jacob and Chang, Ming-Wei and Lee, Kenton and Toutanova, Kristina},
    year = {2019},
    booktitle = {Proceedings of NAACL-HLT},
    doi = {10.18653/v1/N19-1423},
    abstract = {We introduce a new language representation model called BERT, which stands for Bidirectional Encoder Representations from Transformers. Unlike recent language representation models, BERT is designed to pre-train deep bidirectional representations.},
    keywords = {pre-training, language model, BERT, transformers},
}

@article{zhang2023survey,
    title = {A Survey of Large Language Models},
    author = {Zhang, Wayne Xin and Zhao, Junyi and Chen, Guohai and Zhou, Jianping},
    year = {2023},
    journal = {arXiv preprint arXiv:2303.18223},
    abstract = {Language is essentially a complex, intricate system of human expressions governed by grammatical rules. Large language models have shown remarkable capabilities.},
    keywords = {large language models, survey, GPT, NLP},
}

@article{brown2020language,
    title = {Language Models are Few-Shot Learners},
    author = {Brown, Tom and Mann, Benjamin and Ryder, Nick and Subbiah, Melanie},
    year = {2020},
    journal = {Advances in Neural Information Processing Systems},
    volume = {33},
    url = {https://proceedings.neurips.cc/paper/2020/hash/1457c0d6},
    abstract = {Recent work has demonstrated that scaling up language models greatly improves task-agnostic, few-shot performance.},
    keywords = {GPT-3, few-shot learning, language models, scaling},
}

@book{goodfellow2016deep,
    title = {Deep Learning},
    author = {Goodfellow, Ian and Bengio, Yoshua and Courville, Aaron},
    year = {2016},
    publisher = {MIT Press},
    keywords = {deep learning, neural networks, machine learning},
}

@inproceedings{liu2021swin,
    title = {Swin Transformer: Hierarchical Vision Transformer using Shifted Windows},
    author = {Liu, Ze and Lin, Yutong and Cao, Yue and Hu, Han and Wei, Yixuan},
    year = {2021},
    booktitle = {Proceedings of ICCV},
    abstract = {This paper presents a new vision Transformer, called Swin Transformer, that capably serves as a general-purpose backbone for computer vision.},
    keywords = {vision transformer, image classification, object detection},
}

@article{wei2022chain,
    title = {Chain-of-Thought Prompting Elicits Reasoning in Large Language Models},
    author = {Wei, Jason and Wang, Xuezhi and Schuurmans, Dale},
    year = {2022},
    journal = {Advances in Neural Information Processing Systems},
    abstract = {We explore how generating a chain of thought—a series of intermediate reasoning steps—significantly improves the ability of large language models to perform complex reasoning.},
    keywords = {chain-of-thought, prompting, reasoning, LLM},
}
"""


@pytest.fixture
def bib_file(tmp_path):
    """Create a temporary .bib file."""
    bib_path = tmp_path / "references.bib"
    bib_path.write_text(SAMPLE_BIB, encoding='utf-8')
    return str(bib_path)


@pytest.fixture
def bib_dir(tmp_path):
    """Create a temporary directory with .bib files."""
    bib_path = tmp_path / "refs" / "main.bib"
    bib_path.parent.mkdir(parents=True)
    bib_path.write_text(SAMPLE_BIB, encoding='utf-8')
    return str(tmp_path / "refs")


@pytest.fixture(autouse=True)
def reset_lib():
    """Reset the global library between tests."""
    from tools.bib_search import reset_library
    reset_library()
    yield
    reset_library()


# ── Parser Tests ─────────────────────────────────────────────────────────────

class TestBibParser:
    def test_parse_basic(self, bib_file):
        from tools.bib_search import parse_bib_file
        entries = parse_bib_file(bib_file)
        assert len(entries) == 7

    def test_parse_entry_types(self, bib_file):
        from tools.bib_search import parse_bib_file
        entries = parse_bib_file(bib_file)
        types = {e.entry_type for e in entries}
        assert 'article' in types
        assert 'inproceedings' in types
        assert 'book' in types

    def test_parse_authors(self, bib_file):
        from tools.bib_search import parse_bib_file
        entries = parse_bib_file(bib_file)
        vaswani = next(e for e in entries if e.key == 'vaswani2017attention')
        assert len(vaswani.authors) == 8
        assert 'Ashish Vaswani' in vaswani.authors

    def test_parse_year(self, bib_file):
        from tools.bib_search import parse_bib_file
        entries = parse_bib_file(bib_file)
        bert = next(e for e in entries if e.key == 'devlin2019bert')
        assert bert.year == 2019

    def test_parse_venue(self, bib_file):
        from tools.bib_search import parse_bib_file
        entries = parse_bib_file(bib_file)
        bert = next(e for e in entries if e.key == 'devlin2019bert')
        assert 'NAACL' in bert.venue

    def test_parse_keywords(self, bib_file):
        from tools.bib_search import parse_bib_file
        entries = parse_bib_file(bib_file)
        vaswani = next(e for e in entries if e.key == 'vaswani2017attention')
        assert 'transformer' in vaswani.keywords

    def test_parse_doi(self, bib_file):
        from tools.bib_search import parse_bib_file
        entries = parse_bib_file(bib_file)
        vaswani = next(e for e in entries if e.key == 'vaswani2017attention')
        assert vaswani.has_doi
        assert '10.5555' in vaswani.doi

    def test_parse_abstract(self, bib_file):
        from tools.bib_search import parse_bib_file
        entries = parse_bib_file(bib_file)
        vaswani = next(e for e in entries if e.key == 'vaswani2017attention')
        assert vaswani.has_abstract
        assert 'Transformer' in vaswani.abstract

    def test_parse_nonexistent_file(self):
        from tools.bib_search import parse_bib_file
        entries = parse_bib_file("/nonexistent/path.bib")
        assert entries == []

    def test_bib_entry_cite_key(self, bib_file):
        from tools.bib_search import parse_bib_file
        entries = parse_bib_file(bib_file)
        vaswani = next(e for e in entries if e.key == 'vaswani2017attention')
        assert vaswani.cite_key_latex() == r"\cite{vaswani2017attention}"
        assert vaswani.cite_key_natbib() == r"\citep{vaswani2017attention}"

    def test_bib_entry_reference_string(self, bib_file):
        from tools.bib_search import parse_bib_file
        entries = parse_bib_file(bib_file)
        bert = next(e for e in entries if e.key == 'devlin2019bert')
        ref = bert.to_reference_string()
        assert '2019' in ref
        assert 'BERT' in ref


# ── Query Language Parser Tests ──────────────────────────────────────────────

class TestQueryParser:
    def test_parse_free_text(self):
        from tools.bib_search import parse_compact_query
        sf = parse_compact_query("transformer attention mechanism")
        assert sf.query == "transformer attention mechanism"
        assert sf.author == ""

    def test_parse_author_filter(self):
        from tools.bib_search import parse_compact_query
        sf = parse_compact_query("author:vaswani")
        assert sf.author == "vaswani"
        assert sf.query == ""

    def test_parse_year_exact(self):
        from tools.bib_search import parse_compact_query
        sf = parse_compact_query("year:2019")
        assert sf.year_exact == 2019

    def test_parse_year_range(self):
        from tools.bib_search import parse_compact_query
        sf = parse_compact_query("year>=2020 year<=2023")
        assert sf.year_min == 2020
        assert sf.year_max == 2023

    def test_parse_type_filter(self):
        from tools.bib_search import parse_compact_query
        sf = parse_compact_query("type:article")
        assert sf.entry_type == "article"

    def test_parse_venue_filter(self):
        from tools.bib_search import parse_compact_query
        sf = parse_compact_query("venue:neurips")
        assert sf.venue == "neurips"

    def test_parse_has_filter(self):
        from tools.bib_search import parse_compact_query
        sf = parse_compact_query("has:doi has:abstract")
        assert 'doi' in sf.has
        assert 'abstract' in sf.has

    def test_parse_combined(self):
        from tools.bib_search import parse_compact_query
        sf = parse_compact_query("author:zhang year>=2022 has:doi transformer")
        assert sf.author == "zhang"
        assert sf.year_min == 2022
        assert 'doi' in sf.has
        assert sf.query == "transformer"

    def test_parse_keyword_filter(self):
        from tools.bib_search import parse_compact_query
        sf = parse_compact_query("keyword:GPT")
        assert 'GPT' in sf.keywords


# ── BibLibrary Search Tests ──────────────────────────────────────────────────

class TestBibLibrarySearch:
    def test_load_file(self, bib_file):
        from tools.bib_search import BibLibrary
        lib = BibLibrary()
        count = lib.load_file(bib_file)
        assert count == 7
        assert lib.size == 7

    def test_load_file_dedup(self, bib_file):
        from tools.bib_search import BibLibrary
        lib = BibLibrary()
        lib.load_file(bib_file)
        count = lib.load_file(bib_file)  # Duplicate load
        assert count == 0
        assert lib.size == 7

    def test_load_directory(self, bib_dir):
        from tools.bib_search import BibLibrary
        lib = BibLibrary()
        count = lib.load_directory(bib_dir)
        assert count == 7

    def test_search_free_text(self, bib_file):
        from tools.bib_search import BibLibrary
        lib = BibLibrary()
        lib.load_file(bib_file)
        resp = lib.search("transformer attention")
        assert resp.found
        # Vaswani paper should be top result
        top = resp.top_n(1)[0]
        assert 'vaswani' in top.entry.key

    def test_search_by_author(self, bib_file):
        from tools.bib_search import BibLibrary
        lib = BibLibrary()
        lib.load_file(bib_file)
        resp = lib.search("author:devlin")
        assert resp.found
        assert resp.results[0].entry.key == 'devlin2019bert'

    def test_search_by_year_range(self, bib_file):
        from tools.bib_search import BibLibrary
        lib = BibLibrary()
        lib.load_file(bib_file)
        resp = lib.search("year>=2022")
        assert resp.found
        for r in resp.results:
            assert r.entry.year >= 2022

    def test_search_by_type(self, bib_file):
        from tools.bib_search import BibLibrary
        lib = BibLibrary()
        lib.load_file(bib_file)
        resp = lib.search("type:book")
        assert resp.found
        assert resp.results[0].entry.key == 'goodfellow2016deep'

    def test_search_by_venue(self, bib_file):
        from tools.bib_search import BibLibrary
        lib = BibLibrary()
        lib.load_file(bib_file)
        resp = lib.search("venue:NAACL")
        assert resp.found
        assert 'devlin' in resp.results[0].entry.key

    def test_search_has_doi(self, bib_file):
        from tools.bib_search import BibLibrary
        lib = BibLibrary()
        lib.load_file(bib_file)
        resp = lib.search("has:doi")
        assert resp.found
        for r in resp.results:
            assert r.entry.has_doi

    def test_search_combined(self, bib_file):
        from tools.bib_search import BibLibrary
        lib = BibLibrary()
        lib.load_file(bib_file)
        resp = lib.search("author:wei year>=2022 reasoning")
        assert resp.found
        assert 'wei2022chain' in resp.results[0].entry.key

    def test_search_no_results(self, bib_file):
        from tools.bib_search import BibLibrary
        lib = BibLibrary()
        lib.load_file(bib_file)
        resp = lib.search("author:nonexistent")
        assert not resp.found

    def test_search_limit(self, bib_file):
        from tools.bib_search import BibLibrary
        lib = BibLibrary()
        lib.load_file(bib_file)
        resp = lib.search("language model", limit=3)
        assert len(resp.results) <= 3


# ── Uncited Relevant Search Tests ────────────────────────────────────────────

class TestFindUncitedRelevant:
    def test_find_uncited(self, bib_file):
        from tools.bib_search import BibLibrary
        lib = BibLibrary()
        lib.load_file(bib_file)
        # User has cited vaswani and devlin, looking for transformer papers
        cited = {'vaswani2017attention', 'devlin2019bert'}
        resp = lib.find_relevant_uncited(cited, ['transformer', 'attention'])
        assert resp.found
        # Swin Transformer should be recommended
        keys = {r.entry.key for r in resp.results}
        assert 'liu2021swin' in keys
        # Already-cited papers should NOT appear
        assert 'vaswani2017attention' not in keys
        assert 'devlin2019bert' not in keys

    def test_find_uncited_llm_topic(self, bib_file):
        from tools.bib_search import BibLibrary
        lib = BibLibrary()
        lib.load_file(bib_file)
        cited = {'vaswani2017attention'}
        resp = lib.find_relevant_uncited(cited, ['language', 'model', 'few-shot'])
        assert resp.found
        keys = {r.entry.key for r in resp.results}
        assert 'brown2020language' in keys

    def test_find_uncited_empty_topic(self, bib_file):
        from tools.bib_search import BibLibrary
        lib = BibLibrary()
        lib.load_file(bib_file)
        resp = lib.find_relevant_uncited(set(), [])
        assert not resp.found


# ── Tool Interface Tests ─────────────────────────────────────────────────────

class TestToolInterface:
    def test_search_local_bibliography(self, bib_file):
        from tools.bib_search import search_local_bibliography
        result = search_local_bibliography("transformer", bib_path=bib_file)
        assert "Found" in result
        assert "vaswani" in result.lower()

    def test_search_no_bib_file(self, tmp_path, monkeypatch):
        from tools.bib_search import search_local_bibliography
        monkeypatch.chdir(tmp_path)
        result = search_local_bibliography("anything", bib_path=str(tmp_path))
        assert "No .bib files found" in result or "No results" in result

    def test_find_uncited_relevant_interface(self, bib_file):
        from tools.bib_search import find_uncited_relevant
        result = find_uncited_relevant(
            cited_keys=['vaswani2017attention'],
            topic='language model',
            bib_path=bib_file,
        )
        assert "relevant" in result.lower() or "Found" in result

    def test_format_results(self, bib_file):
        from tools.bib_search import BibLibrary
        lib = BibLibrary()
        lib.load_file(bib_file)
        resp = lib.search("transformer")
        formatted = resp.format_results()
        assert "\\cite{" in formatted
        assert "Relevance:" in formatted


# ── Integration with Tool Registry ───────────────────────────────────────────

class TestToolRegistration:
    def test_schemas_registered(self):
        from core.tool_schemas import TOOLS
        names = [t["name"] for t in TOOLS]
        assert "search_local_bibliography" in names
        assert "find_uncited_relevant" in names

    def test_handlers_registered(self):
        from core.tool_dispatch import TOOL_HANDLERS
        assert "search_local_bibliography" in TOOL_HANDLERS
        assert "find_uncited_relevant" in TOOL_HANDLERS

    def test_metadata_registered(self):
        from core.tool_metadata import TOOL_META, assess_risk_level
        assert "search_local_bibliography" in TOOL_META
        assert "find_uncited_relevant" in TOOL_META
        # Both should be low risk (read operations)
        assert assess_risk_level("search_local_bibliography") == "low"
        assert assess_risk_level("find_uncited_relevant") == "low"
