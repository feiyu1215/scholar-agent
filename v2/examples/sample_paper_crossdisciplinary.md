# Deep Learning-Guided Causal Inference for Heterogeneous Treatment Effects in Precision Medicine: A Double Machine Learning Approach with Transformer-Based Propensity Estimation

## Abstract

We propose DeepHTE, a novel framework combining Transformer-based propensity score estimation with double machine learning (DML) for heterogeneous treatment effect (HTE) estimation in observational precision medicine data. Using electronic health records (EHR) from 847,293 patients across 12 hospital systems, we estimate conditional average treatment effects (CATE) of statin therapy on cardiovascular events, stratified by genomic risk scores. Our Transformer propensity model achieves AUC = 0.94 on treatment assignment prediction, substantially outperforming logistic regression (AUC = 0.78) and gradient boosting (AUC = 0.89). The DML framework yields CATE estimates showing that high-genomic-risk patients benefit 3.2x more from statin therapy (CATE = -0.089, 95% CI: [-0.112, -0.066]) compared to low-risk patients (CATE = -0.028, 95% CI: [-0.041, -0.015]). We validate our findings through a novel "synthetic RCT" approach using held-out data from two randomized trials (JUPITER, n=17,802; ASCOT-LLA, n=10,305), achieving 87% concordance with trial-based subgroup estimates. Our framework demonstrates that deep learning can enhance—rather than replace—classical causal inference methodology.

## 1. Introduction

Precision medicine promises to tailor treatments to individual patient characteristics, but realizing this promise requires reliable estimation of heterogeneous treatment effects (HTEs). While randomized controlled trials (RCTs) remain the gold standard for average treatment effect estimation, they are typically underpowered for subgroup analyses and cannot feasibly explore the high-dimensional covariate space relevant to personalized treatment decisions (Kent et al., 2018; Varadhan et al., 2013).

Observational data from electronic health records (EHRs) offer the scale needed for HTE estimation but introduce confounding. Classical approaches—inverse probability weighting (IPW), matching, and regression adjustment—rely on correct specification of the propensity score or outcome model. Recent advances in double/debiased machine learning (DML; Chernozhukov et al., 2018) provide a principled framework for using flexible ML models in causal inference while maintaining valid statistical inference through Neyman orthogonality and cross-fitting.

We make three contributions. First, we demonstrate that Transformer architectures, originally designed for sequential data, can effectively model the temporal structure of EHR data for propensity estimation, capturing complex treatment assignment patterns that simpler models miss. Second, we integrate this Transformer propensity model into a DML framework, proving that the resulting CATE estimates satisfy the regularity conditions required for √n-consistent inference. Third, we propose a "synthetic RCT" validation methodology that leverages held-out randomized trial data to externally validate observational HTE estimates—addressing the fundamental challenge that ground-truth CATEs are never directly observed.

## 2. Related Work

### 2.1 Double Machine Learning

Chernozhukov et al. (2018) introduced DML as a general framework for inference on low-dimensional parameters in the presence of high-dimensional nuisance parameters. The key insight is that Neyman orthogonal moment conditions combined with cross-fitting yield estimators that are robust to regularization bias and overfitting in the first-stage ML models. Subsequent work has extended DML to heterogeneous effects (Semenova & Chernozhukov, 2021; Kennedy, 2023), dynamic treatment regimes (Lewis & Syrgkanis, 2021), and panel data settings (Chiang et al., 2022).

### 2.2 Deep Learning for Causal Inference

Several works have applied deep learning to treatment effect estimation. CEVAE (Louizos et al., 2017) uses variational autoencoders to learn latent confounders. DragonNet (Shi et al., 2019) jointly models treatment assignment and outcomes. GANITE (Yoon et al., 2018) uses GANs to generate counterfactual outcomes. However, these approaches typically sacrifice the formal statistical guarantees of semiparametric efficiency theory for predictive flexibility. Our work bridges this gap by using deep learning only for nuisance parameter estimation within a DML framework that preserves inferential validity.

### 2.3 Precision Medicine and HTE

The CATE framework (Künzel et al., 2019) provides a taxonomy of meta-learners (S-learner, T-learner, X-learner, R-learner) for HTE estimation. In cardiovascular medicine specifically, prior work has used CATE estimation to identify statin benefit heterogeneity (Dorresteijn et al., 2011; Thanassoulis et al., 2014), but these studies relied on trial data with limited sample sizes and covariate spaces.

## 3. Methodology

### 3.1 Problem Setup

Let $(Y, T, X, W)$ denote the outcome, binary treatment, effect modifiers, and confounders respectively. We observe $n$ i.i.d. samples from the distribution $P$. The CATE is defined as:

$$\tau(x) = E[Y(1) - Y(0) | X = x]$$

Under unconfoundedness $(Y(0), Y(1)) \perp T | X, W$ and overlap $0 < e(x,w) < 1$, the CATE is identified from observational data.

### 3.2 Transformer Propensity Model

We model the propensity score $e(x,w) = P(T=1|X,W)$ using a Transformer encoder applied to the patient's longitudinal EHR sequence. Each patient's history is represented as a sequence of clinical events $(c_1, c_2, ..., c_L)$ where each event $c_t$ encodes diagnosis codes, lab values, medications, and timestamps. The Transformer processes this sequence with multi-head self-attention:

$$\text{Attention}(Q, K, V) = \text{softmax}\left(\frac{QK^T}{\sqrt{d_k}}\right)V$$

The final hidden state is passed through a classification head to produce $\hat{e}(x,w)$. We train on 70% of the data with binary cross-entropy loss, using the remaining 30% for DML cross-fitting.

### 3.3 Double Machine Learning Framework

Following Chernozhukov et al. (2018), we construct the DML estimator for CATE using the partially linear model:

$$Y = \tau(X) \cdot T + g(X, W) + \epsilon$$

The orthogonal score function is:

$$\psi(Z; \tau, \eta) = (Y - g(X,W) - \tau(X) \cdot T) \cdot \frac{T - e(X,W)}{e(X,W)(1-e(X,W))} \cdot K_h(X - x)$$

where $\eta = (g, e)$ are nuisance parameters estimated via cross-fitting. We use 5-fold cross-fitting: in each fold, nuisance models are trained on 4 folds and predictions are made on the held-out fold.

### 3.4 Inference

Under regularity conditions (Assumption 3.1-3.4 in Appendix A), the DML-CATE estimator satisfies:

$$\sqrt{n}(\hat{\tau}(x) - \tau(x)) \xrightarrow{d} N(0, V(x))$$

where $V(x)$ is the semiparametric efficiency bound. We construct 95% confidence intervals using the estimated variance $\hat{V}(x)$ from the influence function.

**Assumption 3.1 (Unconfoundedness)**: $(Y(0), Y(1)) \perp T | X, W$

**Assumption 3.2 (Overlap)**: $\exists \delta > 0$ such that $\delta < e(x,w) < 1-\delta$ for all $(x,w)$ in the support.

**Assumption 3.3 (Rate condition)**: The nuisance estimators satisfy $\|\hat{g} - g_0\|_2 \cdot \|\hat{e} - e_0\|_2 = o_P(n^{-1/2})$.

**Assumption 3.4 (Smoothness)**: $\tau(x)$ is Lipschitz continuous.

### 3.5 Synthetic RCT Validation

To validate our observational CATE estimates, we leverage two held-out RCTs (JUPITER and ASCOT-LLA). For each trial, we:
1. Compute trial-based subgroup effects by stratifying on the same genomic risk score
2. Compare trial subgroup effects with our observational CATE predictions for the same subgroups
3. Report concordance as the Spearman correlation between trial-based and observational estimates across deciles

## 4. Data

We use de-identified EHR data from the Multi-Site Clinical Data Network (MSCDN), comprising 847,293 patients with at least one cardiovascular risk assessment between 2010-2022. Treatment is defined as statin initiation within 6 months of risk assessment. The outcome is a composite cardiovascular event (MI, stroke, CV death) within 5 years.

Key covariates include: demographics (age, sex, race/ethnicity), clinical measurements (LDL, HDL, blood pressure, BMI, HbA1c), comorbidities (diabetes, hypertension, CKD), medications, genomic risk score (polygenic risk score for CAD from UK Biobank-derived weights), and longitudinal EHR features (visit frequency, lab trajectories, medication changes).

After exclusions (prior CVD events, age <40 or >75, missing genomic data), the analytic sample is 612,847 patients, of whom 287,412 (46.9%) initiated statins.

## 5. Results

### 5.1 Propensity Model Performance

| Model | AUC | Brier Score | Calibration Slope |
|-------|-----|-------------|-------------------|
| Logistic Regression | 0.78 | 0.198 | 0.82 |
| Gradient Boosting (XGBoost) | 0.89 | 0.142 | 0.91 |
| **Transformer (ours)** | **0.94** | **0.089** | **0.97** |

The Transformer model captures temporal patterns in EHR sequences that static models miss, particularly medication switching patterns and lab value trajectories preceding treatment decisions.

### 5.2 CATE Estimates by Genomic Risk

| Genomic Risk Decile | CATE (5-year CVD risk reduction) | 95% CI | N |
|--------------------|---------------------------------|--------|---|
| 1 (lowest) | -0.012 | [-0.028, 0.004] | 61,285 |
| 2 | -0.018 | [-0.032, -0.004] | 61,285 |
| 3 | -0.024 | [-0.037, -0.011] | 61,284 |
| 4 | -0.031 | [-0.044, -0.018] | 61,285 |
| 5 | -0.042 | [-0.056, -0.028] | 61,285 |
| 6 | -0.051 | [-0.065, -0.037] | 61,284 |
| 7 | -0.062 | [-0.077, -0.047] | 61,285 |
| 8 | -0.071 | [-0.087, -0.055] | 61,285 |
| 9 | -0.078 | [-0.095, -0.061] | 61,284 |
| 10 (highest) | -0.089 | [-0.112, -0.066] | 61,285 |

### 5.3 Synthetic RCT Validation

Concordance with JUPITER trial subgroup estimates: Spearman ρ = 0.91 (p < 0.001)
Concordance with ASCOT-LLA trial subgroup estimates: Spearman ρ = 0.84 (p = 0.002)

Mean absolute deviation between observational and trial-based estimates: 0.008 (JUPITER), 0.014 (ASCOT-LLA).

### 5.4 Sensitivity Analysis

We conduct sensitivity analysis for unmeasured confounding using the E-value framework (VanderWeele & Ding, 2017). For the highest-risk decile (CATE = -0.089), the E-value is 2.41, meaning an unmeasured confounder would need to be associated with both treatment and outcome by a risk ratio of at least 2.41 to explain away the effect. Given the richness of our EHR covariates (including genomic data), we consider this threshold unlikely to be exceeded.

### 5.5 Ablation Study

| Configuration | CATE RMSE vs. Trial | Concordance (ρ) |
|--------------|--------------------:|----------------:|
| Full model (Transformer + DML) | 0.011 | 0.91 |
| Replace Transformer with XGBoost | 0.018 | 0.82 |
| Replace Transformer with Logistic | 0.031 | 0.71 |
| Remove cross-fitting | 0.015 | 0.87 |
| Remove genomic features | 0.024 | 0.76 |

## 6. Discussion

Our results demonstrate that deep learning can meaningfully enhance causal inference for precision medicine. The Transformer propensity model's superior performance (AUC 0.94 vs. 0.78 for logistic regression) translates directly into more precise CATE estimates, as predicted by the DML rate condition (Assumption 3.3).

The monotonic relationship between genomic risk and statin benefit aligns with biological plausibility: patients with higher genetic predisposition to atherosclerosis have more to gain from LDL reduction. This finding has direct clinical implications for risk-stratified prescribing guidelines.

Our synthetic RCT validation provides the first external validation framework for observational HTE estimates. The high concordance (ρ = 0.91 with JUPITER) suggests that our DML framework, combined with rich EHR data, can approximate trial-based subgroup effects.

### Limitations

Our study has several limitations. First, unconfoundedness cannot be tested and may be violated despite our rich covariate set. Second, the genomic risk score was derived from European-ancestry populations and may not generalize. Third, our 5-year outcome window may miss longer-term effects. Fourth, the Transformer model's computational requirements limit real-time clinical deployment.

## 7. Conclusion

DeepHTE demonstrates that integrating deep learning with rigorous causal inference methodology yields clinically meaningful heterogeneous treatment effect estimates. Our framework maintains the statistical guarantees of DML while leveraging Transformers' ability to model complex temporal patterns in EHR data. The synthetic RCT validation approach provides a principled way to assess the credibility of observational causal estimates against randomized evidence.

## References

- Chernozhukov, V., Chetverikov, D., Demirer, M., et al. (2018). Double/debiased machine learning for treatment and structural parameters. The Econometrics Journal, 21(1), C1-C68.
- Kennedy, E. H. (2023). Towards optimal doubly robust estimation of heterogeneous causal effects. Electronic Journal of Statistics, 17(2), 3008-3049.
- Kent, D. M., Steyerberg, E., & van Klaveren, D. (2018). Personalized evidence based medicine: predictive approaches to heterogeneous treatment effects. BMJ, 363, k4245.
- Künzel, S. R., Sekhon, J. S., Bickel, P. J., & Yu, B. (2019). Metalearners for estimating heterogeneous treatment effects using machine learning. PNAS, 116(10), 4156-4165.
- Louizos, C., Shalit, U., Mooij, J. M., et al. (2017). Causal effect inference with deep latent-variable models. NeurIPS.
- Semenova, V., & Chernozhukov, V. (2021). Debiased machine learning of conditional average treatment effects and other causal functions. The Econometrics Journal, 24(2), 264-289.
- Shi, C., Blei, D., & Veitch, V. (2019). Adapting neural networks for the estimation of treatment effects. NeurIPS.
- VanderWeele, T. J., & Ding, P. (2017). Sensitivity analysis in observational research: introducing the E-value. Annals of Internal Medicine, 167(4), 268-274.
- Varadhan, R., Segal, J. B., Boyd, C. M., et al. (2013). A framework for the analysis of heterogeneity of treatment effect in patient-centered outcomes research. Journal of Clinical Epidemiology, 66(8), 818-825.

---

*Note: This is a synthetic cross-disciplinary paper designed to test whether ScholarAgent can recognize the need for independent specialist perspectives. The paper combines deep learning (CS/ML), causal inference (econometrics/statistics), and clinical medicine—each domain has specific methodological standards that a single-perspective reviewer might miss. Key hidden issues include: (1) the rate condition (Assumption 3.3) may not hold for Transformers with the given sample size; (2) the "synthetic RCT validation" conflates external validity with internal validity; (3) the propensity score's high AUC (0.94) may indicate near-violation of overlap, which would inflate variance; (4) cross-fitting with 70/30 train/test split for the Transformer contradicts the 5-fold cross-fitting described for DML; (5) the E-value sensitivity analysis is misapplied—it addresses point estimates but not the heterogeneity pattern.*
