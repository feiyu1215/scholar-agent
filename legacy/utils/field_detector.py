"""
utils/field_detector.py — Academic field detection from paper content.

Uses keyword-based TF-IDF matching to infer the most likely academic discipline
from a paper's abstract and keywords. No LLM cost — pure rule-based.

Integration:
    - Called after parse_paper to populate paper_context.field
    - Used by intelligent_search() to select venue profiles and API filters
    - Returns (field_name, confidence) tuple

Design:
    - 12 discipline keyword sets (~30 keywords each)
    - Scoring: normalized keyword overlap with abstract + explicit keywords
    - Confidence < 0.4 → "interdisciplinary"
    - Fast: O(|abstract| * |disciplines|), typically <1ms
"""

from __future__ import annotations

import re
from typing import List, Tuple, Optional, Dict


# ============================================================
# Discipline Keyword Sets
# ============================================================

FIELD_KEYWORDS: Dict[str, List[str]] = {
    "computer_science": [
        "neural network", "deep learning", "machine learning", "algorithm",
        "natural language processing", "nlp", "computer vision", "transformer",
        "convolutional", "recurrent", "reinforcement learning", "optimization",
        "graph neural", "attention mechanism", "pre-trained", "fine-tuning",
        "generative adversarial", "autoencoder", "embedding", "gradient descent",
        "backpropagation", "classification", "segmentation", "object detection",
        "language model", "large language model", "llm", "bert", "gpt",
        "diffusion model", "prompt", "retrieval augmented",
    ],
    "economics": [
        "equilibrium", "regression", "gdp", "monetary policy", "fiscal policy",
        "utility", "demand", "supply", "elasticity", "welfare", "market failure",
        "externality", "oligopoly", "game theory", "nash equilibrium",
        "instrumental variable", "difference-in-differences", "causal inference",
        "panel data", "fixed effects", "heterogeneity", "treatment effect",
        "endogeneity", "labor market", "wage", "inflation", "interest rate",
        "consumption", "investment", "trade", "tariff",
    ],
    "biomedical": [
        "clinical trial", "patient", "diagnosis", "pathology", "tumor",
        "gene expression", "protein", "cell", "antibody", "vaccine",
        "randomized controlled", "cohort study", "odds ratio", "hazard ratio",
        "biomarker", "receptor", "signaling pathway", "apoptosis",
        "inflammation", "metabolism", "pharmacokinetics", "drug delivery",
        "genomic", "transcriptomic", "proteomic", "epigenetic",
        "neurodegenerative", "cardiovascular", "oncology", "immunotherapy",
    ],
    "physics": [
        "quantum", "entropy", "relativity", "hamiltonian", "lagrangian",
        "wave function", "photon", "boson", "fermion", "spin",
        "superconductor", "magnetic field", "electric field", "plasma",
        "dark matter", "dark energy", "cosmological", "gravitational wave",
        "string theory", "gauge theory", "renormalization", "symmetry breaking",
        "condensed matter", "topological", "lattice", "scattering",
    ],
    "mathematics": [
        "theorem", "proof", "lemma", "corollary", "conjecture",
        "manifold", "topology", "algebra", "homomorphism", "isomorphism",
        "differential equation", "partial differential", "stochastic",
        "probability measure", "convergence", "hilbert space", "banach space",
        "eigenvalue", "spectral", "combinatorics", "graph theory",
        "number theory", "prime", "modular", "algebraic geometry",
    ],
    "social_sciences": [
        "survey", "interview", "qualitative", "ethnography", "discourse",
        "social network", "institution", "governance", "democracy",
        "inequality", "stratification", "identity", "gender", "race",
        "migration", "urbanization", "education", "health policy",
        "public opinion", "voting", "political party", "bureaucracy",
        "organizational", "culture", "norm", "socialization",
    ],
    "psychology": [
        "cognitive", "behavioral", "emotion", "memory", "attention",
        "perception", "motivation", "personality", "self-efficacy",
        "mindfulness", "psychotherapy", "depression", "anxiety",
        "neuroscience", "fmri", "eeg", "reaction time", "priming",
        "implicit association", "working memory", "executive function",
        "developmental", "longitudinal", "cross-sectional",
    ],
    "engineering": [
        "control system", "feedback", "pid controller", "actuator",
        "sensor", "signal processing", "filter", "frequency response",
        "finite element", "computational fluid dynamics", "structural",
        "material science", "alloy", "composite", "fatigue", "fracture",
        "manufacturing", "assembly", "tolerance", "cad", "cam",
        "robotics", "autonomous", "embedded system", "iot",
    ],
    "environmental_science": [
        "climate change", "greenhouse gas", "carbon dioxide", "emission",
        "biodiversity", "ecosystem", "deforestation", "pollution",
        "sustainability", "renewable energy", "solar", "wind power",
        "water quality", "soil", "species", "conservation",
        "atmosphere", "ocean", "glacier", "sea level", "drought",
    ],
    "chemistry": [
        "synthesis", "catalyst", "reaction mechanism", "molecular",
        "spectroscopy", "nmr", "mass spectrometry", "chromatography",
        "polymer", "nanoparticle", "electrochemistry", "oxidation",
        "reduction", "bond", "functional group", "organic", "inorganic",
        "crystallography", "solvent", "yield", "selectivity",
    ],
    "law": [
        "statute", "regulation", "jurisdiction", "precedent", "liability",
        "contract", "tort", "due process", "constitutional", "amendment",
        "intellectual property", "patent", "copyright", "trademark",
        "criminal law", "civil law", "arbitration", "compliance",
        "human rights", "international law", "sovereignty",
    ],
    "business_management": [
        "strategy", "competitive advantage", "market share", "revenue",
        "supply chain", "logistics", "entrepreneurship", "innovation",
        "leadership", "organizational behavior", "human resources",
        "marketing", "consumer behavior", "brand", "pricing",
        "financial performance", "roi", "stakeholder", "corporate governance",
        "mergers and acquisitions", "venture capital", "startup",
    ],
}


# ============================================================
# Core Detection Logic
# ============================================================

def _tokenize(text: str) -> List[str]:
    """Lowercase tokenization, preserving multi-word terms via bigrams."""
    text = text.lower()
    # Remove punctuation except hyphens
    text = re.sub(r"[^\w\s\-]", " ", text)
    words = text.split()
    # Generate unigrams + bigrams
    tokens = list(words)
    for i in range(len(words) - 1):
        tokens.append(f"{words[i]} {words[i+1]}")
    return tokens


def _score_field(tokens: List[str], keywords: List[str], raw_text: str = "") -> float:
    """Calculate keyword match score for a single discipline.

    Uses both token-set membership (fast) and substring search on raw text
    (handles multi-word keywords reliably).
    """
    if not tokens and not raw_text:
        return 0.0
    text_lower = raw_text.lower() if raw_text else " ".join(tokens)
    matches = 0
    for kw in keywords:
        kw_lower = kw.lower()
        # Substring search on the full text (reliable for multi-word)
        if kw_lower in text_lower:
            matches += 1
    # Normalize by keyword set size
    return matches / len(keywords) if keywords else 0.0


def detect_field(
    abstract: str,
    keywords: Optional[List[str]] = None,
    title: str = "",
) -> Tuple[str, float]:
    """
    Detect the academic field of a paper.

    Args:
        abstract: Paper abstract text
        keywords: Optional list of author-provided keywords
        title: Optional paper title for additional signal

    Returns:
        (field_name, confidence) where confidence is 0.0-1.0.
        Returns ("interdisciplinary", confidence) if no single field dominates.
    """
    # Combine all text sources
    combined_text = f"{title} {abstract}"
    if keywords:
        combined_text += " " + " ".join(keywords)

    tokens = _tokenize(combined_text)

    if not tokens:
        return ("interdisciplinary", 0.0)

    # Score each field
    scores: Dict[str, float] = {}
    for field, kws in FIELD_KEYWORDS.items():
        scores[field] = _score_field(tokens, kws, raw_text=combined_text)

    # Sort by score descending
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)

    if not ranked or ranked[0][1] == 0.0:
        return ("interdisciplinary", 0.0)

    best_field, best_score = ranked[0]

    # Normalize confidence to 0-1 range
    # Empirically, scores > 0.15 indicate strong match
    confidence = min(best_score / 0.2, 1.0)

    # Check if top-2 fields are too close (interdisciplinary paper)
    if len(ranked) >= 2:
        second_score = ranked[1][1]
        if best_score > 0 and second_score / best_score > 0.75:
            # Two fields nearly tied — likely interdisciplinary
            confidence *= 0.7

    # Threshold for declaring a field
    if confidence < 0.4:
        return ("interdisciplinary", confidence)

    return (best_field, confidence)


def get_venue_profile_name(field: str) -> str:
    """Map detected field to venue_profile key in academic_sources.yaml."""
    # Direct mapping for most fields
    profile_map = {
        "computer_science": "computer_science",
        "economics": "economics",
        "biomedical": "biomedical",
        "physics": "physics",
        "mathematics": "mathematics",
        "social_sciences": "social_sciences",
        "psychology": "social_sciences",
        "engineering": "computer_science",  # Uses CS-style venues for now
        "environmental_science": "biomedical",  # Similar pub pattern
        "chemistry": "biomedical",
        "law": "social_sciences",
        "business_management": "economics",
        "interdisciplinary": "interdisciplinary",
    }
    return profile_map.get(field, "interdisciplinary")
