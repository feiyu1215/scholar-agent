"""
Dynamic Focus Point Generator (inspired by career-copilot Stage 1.5).

Before detailed review, scans the paper to generate:
1. Paper-specific review focus points (what's unique about THIS paper that reviewers should watch for)
2. Potential confusion areas (claims that sound similar but differ)
3. Methodology-specific checkpoints (based on the actual methods used)

These are injected into reviewer prompts to improve discrimination and reduce generic feedback.
"""

from typing import Dict, Any, List, Optional


def generate_focus_points(
    paper_metadata: Dict[str, Any],
    section_summaries: Dict[str, str],
    detected_methods: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Generate paper-specific focus points for reviewers.
    
    Args:
        paper_metadata: {"title": str, "abstract": str, "paper_type": str, "field": str, "word_count": int}
        section_summaries: {"introduction": "...", "methodology": "...", ...}
        detected_methods: ["DID", "IV", "regression_discontinuity", ...] (optional, from parser)
    
    Returns: {
        "focus_points": [
            {"dimension": "methodology", "point": "Paper claims causal identification via IV - verify instrument relevance and exclusion restriction", "priority": "high"},
            {"dimension": "novelty", "point": "Contribution appears incremental over [X] - check if the distinction is clearly articulated", "priority": "medium"},
            ...
        ],
        "confusion_areas": [
            {"area": "The paper uses 'robust' to describe both their estimator and their results - these are different claims", "relevant_sections": ["methodology", "results"]},
            ...
        ],
        "methodology_checkpoints": [
            {"method": "DID", "must_verify": ["parallel trends assumption", "no anticipation", "SUTVA"], "common_weaknesses": ["pre-trend test only visual, no formal test"]},
            ...
        ],
        "reviewer_injection": {
            "editor": "Focus on whether the causal claim in abstract matches what methodology actually delivers...",
            "methodology": "This paper uses IV with [instrument]. Key verification: ...",
            "theory": "Contribution claims novelty over [X]. Verify the claimed distinction...",
            "logic": "Watch for: conclusions that overstate causal claims given the identification strategy...",
            "literature": "Check if the claimed gap is actually a gap - related work section may omit [Y]..."
        },
        "meta": {
            "paper_type": str,
            "detected_methods": [...],
            "estimated_complexity": "low" | "medium" | "high"
        }
    }
    """
    # Extract key info
    abstract = paper_metadata.get("abstract", "")
    title = paper_metadata.get("title", "")
    paper_type = paper_metadata.get("paper_type", "empirical")
    field = paper_metadata.get("field", "unknown")
    
    # Detect methods if not provided
    if not detected_methods:
        detected_methods = _detect_methods(section_summaries.get("methodology", "") + " " + abstract)
    
    # Generate focus points based on paper characteristics
    focus_points = []
    confusion_areas = []
    methodology_checkpoints = []
    
    # Method-specific focus points
    for method in detected_methods:
        method_info = METHODOLOGY_REGISTRY.get(method, {})
        if method_info:
            focus_points.append({
                "dimension": "methodology",
                "point": f"Paper uses {method_info['display_name']}. Key assumption to verify: {method_info['key_assumption']}",
                "priority": "high",
            })
            methodology_checkpoints.append({
                "method": method,
                "must_verify": method_info.get("must_verify", []),
                "common_weaknesses": method_info.get("common_weaknesses", []),
            })
    
    # Novelty-related focus points
    if "novel" in abstract.lower() or "first" in abstract.lower() or "contribute" in abstract.lower():
        focus_points.append({
            "dimension": "novelty",
            "point": "Paper makes explicit novelty/first-mover claims. Verify against related work completeness.",
            "priority": "high",
        })
    
    # Causal vs correlational confusion
    causal_words = ["cause", "effect", "impact", "leads to", "drives", "determines"]
    correlational_methods = ["regression", "correlation", "association", "ols"]
    has_causal_language = any(w in abstract.lower() for w in causal_words)
    has_correlational_method = any(m in str(detected_methods).lower() for m in correlational_methods)
    
    if has_causal_language and has_correlational_method:
        confusion_areas.append({
            "area": "Paper uses causal language but methodology may only support correlational claims. Check if identification strategy justifies causal interpretation.",
            "relevant_sections": ["abstract", "methodology", "results", "conclusion"],
        })
    
    # Length-based complexity
    word_count = paper_metadata.get("word_count", 5000)
    complexity = "low" if word_count < 5000 else ("high" if word_count > 15000 else "medium")
    
    # Section-specific confusion detection
    methodology_text = section_summaries.get("methodology", "")
    if "robust" in methodology_text.lower():
        confusion_areas.append({
            "area": "'Robust' may refer to robust standard errors, robustness checks, or robust estimators — ensure each usage is clear.",
            "relevant_sections": ["methodology", "results"],
        })
    
    if "significant" in methodology_text.lower() or "significant" in section_summaries.get("results", "").lower():
        confusion_areas.append({
            "area": "'Significant' may conflate statistical significance with economic/practical significance. Check both are addressed.",
            "relevant_sections": ["results", "discussion"],
        })
    
    # Generate reviewer-specific injection prompts
    reviewer_injection = _generate_reviewer_injections(
        focus_points, confusion_areas, methodology_checkpoints, 
        paper_type, detected_methods, field
    )
    
    return {
        "focus_points": focus_points,
        "confusion_areas": confusion_areas,
        "methodology_checkpoints": methodology_checkpoints,
        "reviewer_injection": reviewer_injection,
        "meta": {
            "paper_type": paper_type,
            "detected_methods": detected_methods,
            "estimated_complexity": complexity,
        }
    }


def _detect_methods(text: str) -> List[str]:
    """Detect research methods from text."""
    text_lower = text.lower()
    detected = []
    
    method_keywords = {
        "DID": ["difference-in-difference", "diff-in-diff", "did ", "parallel trends"],
        "IV": ["instrumental variable", " iv ", "2sls", "two-stage least squares", "instrument"],
        "RDD": ["regression discontinuity", "rdd", "running variable", "cutoff"],
        "RCT": ["randomized controlled trial", "rct", "random assignment", "treatment group"],
        "PSM": ["propensity score", "matching", "psm"],
        "panel_FE": ["fixed effects", "panel data", "within estimator"],
        "GMM": ["generalized method of moments", "gmm", "arellano-bond"],
        "ML": ["machine learning", "random forest", "neural network", "deep learning", "xgboost"],
        "structural": ["structural model", "structural estimation", "calibration"],
        "survey": ["survey data", "questionnaire", "likert scale"],
        "qualitative": ["interview", "case study", "ethnograph", "grounded theory"],
        "meta_analysis": ["meta-analysis", "systematic review", "effect size"],
    }
    
    for method, keywords in method_keywords.items():
        if any(kw in text_lower for kw in keywords):
            detected.append(method)
    
    return detected if detected else ["unspecified"]


def _generate_reviewer_injections(
    focus_points: list,
    confusion_areas: list,
    methodology_checkpoints: list,
    paper_type: str,
    methods: list,
    field: str,
) -> Dict[str, str]:
    """Generate reviewer-role-specific injection prompts."""
    injections = {}
    
    # Editor injection
    editor_lines = ["PAPER-SPECIFIC FOCUS (auto-generated):"]
    editor_lines.append(f"Paper type: {paper_type} | Field: {field} | Methods: {', '.join(methods)}")
    if confusion_areas:
        editor_lines.append(f"Watch for: {confusion_areas[0]['area']}")
    injections["editor"] = "\n".join(editor_lines)
    
    # Methodology injection
    meth_lines = ["PAPER-SPECIFIC METHODOLOGY FOCUS (auto-generated):"]
    for cp in methodology_checkpoints:
        meth_lines.append(f"Method: {cp['method']}")
        meth_lines.append(f"  Must verify: {', '.join(cp['must_verify'][:3])}")
        if cp['common_weaknesses']:
            meth_lines.append(f"  Common weaknesses: {', '.join(cp['common_weaknesses'][:2])}")
    if not methodology_checkpoints:
        meth_lines.append("No specific methodology detected — focus on general rigor.")
    injections["methodology"] = "\n".join(meth_lines)
    
    # Theory/novelty injection
    theory_lines = ["PAPER-SPECIFIC NOVELTY FOCUS (auto-generated):"]
    novelty_points = [fp for fp in focus_points if fp["dimension"] == "novelty"]
    if novelty_points:
        for np in novelty_points:
            theory_lines.append(f"• {np['point']}")
    else:
        theory_lines.append("No explicit novelty claims detected — check if contribution is clearly stated.")
    injections["theory"] = "\n".join(theory_lines)
    
    # Logic injection
    logic_lines = ["PAPER-SPECIFIC LOGIC FOCUS (auto-generated):"]
    for ca in confusion_areas:
        logic_lines.append(f"• Potential confusion: {ca['area']}")
        logic_lines.append(f"  Check in: {', '.join(ca['relevant_sections'])}")
    if not confusion_areas:
        logic_lines.append("No specific confusion areas detected — apply standard coherence checks.")
    injections["logic"] = "\n".join(logic_lines)
    
    # Literature injection
    lit_lines = ["PAPER-SPECIFIC LITERATURE FOCUS (auto-generated):"]
    lit_lines.append(f"Field: {field} | Methods: {', '.join(methods)}")
    lit_lines.append("Check: Are key methodological predecessors cited? Is the claimed gap genuine?")
    injections["literature"] = "\n".join(lit_lines)
    
    return injections


# Registry of common research methods and their verification requirements
METHODOLOGY_REGISTRY = {
    "DID": {
        "display_name": "Difference-in-Differences",
        "key_assumption": "parallel trends in absence of treatment",
        "must_verify": [
            "parallel trends test (visual + formal)",
            "no anticipation assumption",
            "SUTVA (no spillovers)",
            "staggered adoption handling (if applicable)",
        ],
        "common_weaknesses": [
            "pre-trend test only visual without formal test",
            "no discussion of potential violations",
            "no placebo/falsification tests",
        ],
    },
    "IV": {
        "display_name": "Instrumental Variables (2SLS)",
        "key_assumption": "instrument relevance and exclusion restriction",
        "must_verify": [
            "first-stage F-statistic (>10 rule of thumb)",
            "exclusion restriction argument",
            "instrument relevance (economic reasoning)",
            "overidentification test (if multiple instruments)",
        ],
        "common_weaknesses": [
            "weak instrument (F<10)",
            "exclusion restriction argued but not testable",
            "LATE interpretation not discussed",
        ],
    },
    "RDD": {
        "display_name": "Regression Discontinuity Design",
        "key_assumption": "no precise manipulation of running variable at cutoff",
        "must_verify": [
            "McCrary density test",
            "covariate balance at cutoff",
            "bandwidth sensitivity",
            "polynomial order sensitivity",
        ],
        "common_weaknesses": [
            "no manipulation test",
            "results sensitive to bandwidth choice",
            "no covariate smoothness checks",
        ],
    },
    "RCT": {
        "display_name": "Randomized Controlled Trial",
        "key_assumption": "successful randomization and no attrition bias",
        "must_verify": [
            "balance table (treatment vs control)",
            "attrition rates and differential attrition",
            "ITT vs LATE distinction",
            "pre-registration (if claimed)",
        ],
        "common_weaknesses": [
            "no attrition analysis",
            "imbalanced covariates without discussion",
            "results not pre-registered but claimed to be",
        ],
    },
    "PSM": {
        "display_name": "Propensity Score Matching",
        "key_assumption": "selection on observables (CIA/unconfoundedness)",
        "must_verify": [
            "covariate balance after matching",
            "common support/overlap",
            "sensitivity to hidden bias (Rosenbaum bounds)",
            "matching algorithm choice justification",
        ],
        "common_weaknesses": [
            "no balance diagnostics shown",
            "no sensitivity analysis for unobservables",
            "CIA assumed without justification",
        ],
    },
    "panel_FE": {
        "display_name": "Panel Fixed Effects",
        "key_assumption": "unobserved heterogeneity is time-invariant",
        "must_verify": [
            "Hausman test (FE vs RE)",
            "cluster-robust standard errors",
            "serial correlation in errors",
            "time-varying confounders discussion",
        ],
        "common_weaknesses": [
            "no clustering of standard errors",
            "no discussion of remaining endogeneity",
            "FE chosen without Hausman test",
        ],
    },
    "ML": {
        "display_name": "Machine Learning Methods",
        "key_assumption": "model generalizes to unseen data",
        "must_verify": [
            "train/test split or cross-validation",
            "hyperparameter tuning procedure",
            "comparison with appropriate baselines",
            "feature importance or interpretability",
        ],
        "common_weaknesses": [
            "no held-out test set",
            "data leakage in preprocessing",
            "no comparison with simpler models",
        ],
    },
}


def format_focus_report(focus_result: Dict[str, Any]) -> str:
    """Format focus points as a human-readable report."""
    lines = ["📋 **Paper-Specific Review Focus Points**", ""]
    
    meta = focus_result.get("meta", {})
    lines.append(f"Type: {meta.get('paper_type', '?')} | Methods: {', '.join(meta.get('detected_methods', []))} | Complexity: {meta.get('estimated_complexity', '?')}")
    lines.append("")
    
    # Focus points
    if focus_result["focus_points"]:
        lines.append("**Key Focus Points:**")
        for fp in focus_result["focus_points"]:
            priority_icon = "🔴" if fp["priority"] == "high" else "🟡"
            lines.append(f"  {priority_icon} [{fp['dimension']}] {fp['point']}")
        lines.append("")
    
    # Confusion areas
    if focus_result["confusion_areas"]:
        lines.append("**Potential Confusion Areas:**")
        for ca in focus_result["confusion_areas"]:
            lines.append(f"  ⚠️ {ca['area']}")
            lines.append(f"     Sections: {', '.join(ca['relevant_sections'])}")
        lines.append("")
    
    # Method checkpoints
    if focus_result["methodology_checkpoints"]:
        lines.append("**Methodology Verification Checklist:**")
        for mc in focus_result["methodology_checkpoints"]:
            lines.append(f"  [{mc['method']}]")
            for item in mc["must_verify"][:3]:
                lines.append(f"    □ {item}")
    
    return "\n".join(lines)
