# Methodology Review Checklist

## Overview

Structured checklist for reviewing empirical methodology across disciplines.
Used by `review_engine` in methodology reviewer role to systematically check
research design, estimation, inference, and reporting standards.

Covers: economics/econometrics, ML/AI experiments, social sciences, and medical/clinical.
Each item has an issue description, what to check, and common fixes.

---

## 1. Research Design

### 1.1 Identification Strategy

```
CHECK: Is the causal identification strategy clearly stated?
  - What is the treatment? What is the control?
  - What variation is being exploited (random assignment, natural experiment, discontinuity)?
  - What are the key identifying assumptions?

COMMON ISSUES:
  - Claiming causality from OLS on observational data
  - Omitted variable bias acknowledged but not addressed
  - Reverse causality hand-waved away without test

FIX SUGGESTIONS:
  - Add falsification tests (placebo treatments, pre-trend checks)
  - Discuss direction of potential bias explicitly
  - Consider bounding exercises (Oster 2019 method)
```

### 1.2 Sample Selection

```
CHECK: Is the sample well-defined and appropriate?
  - Clear inclusion/exclusion criteria?
  - Sample size justified? Power analysis conducted?
  - Selection into sample potentially endogenous?
  - Attrition/missing data addressed?

COMMON ISSUES:
  - Convenience sampling presented as representative
  - No power calculation for RCT/experiment
  - Survivorship bias in firm/fund datasets
  - Dropping observations without justification

FIX SUGGESTIONS:
  - Report exact sample construction steps
  - For RCTs: pre-registration with power calculation
  - Heckman selection correction or bounds if selection present
  - Lee (2009) bounds for attrition
```

### 1.3 Variable Definition

```
CHECK: Are all variables clearly defined and measured appropriately?
  - Outcome variable: valid proxy for concept of interest?
  - Treatment variable: precise, not contaminated by outcome?
  - Controls: justified theoretically, not "bad controls" (Angrist & Pischke)
  - Timing: all RHS variables measured before outcome?

COMMON ISSUES:
  - Dependent variable is a composite index without validation
  - "Kitchen sink" controls including post-treatment variables
  - Subjective measures without inter-rater reliability
  - Same-source bias (all variables from same survey respondent)

FIX SUGGESTIONS:
  - Validate composite measures (factor analysis, Cronbach's alpha)
  - Check each control: would removing it change the story?
  - Report measurement reliability
  - Use different sources for X and Y where possible
```

---

## 2. Estimation Methods

### 2.1 Regression Specifications

```
CHECK: Is the estimation method appropriate for the data and question?
  - Linear model for nonlinear outcome? (binary DV in OLS)
  - Functional form tested?
  - Fixed effects: correct level? Absorb right variation?
  - Clustering: at treatment assignment level?

COMMON ISSUES:
  - OLS on binary/bounded dependent variable without justification
  - Two-way FE without discussing Goodman-Bacon decomposition (staggered DID)
  - Clustering at wrong level (individual when treatment is at group level)
  - Not accounting for serial correlation in time-series panels

FIX SUGGESTIONS:
  - For binary DV: justify LPM or use logit/probit as robustness
  - For staggered DID: use Callaway-Sant'Anna / Sun-Abraham / de Chaisemartin-d'Haultfœuille
  - Cluster at the level of treatment variation (Cameron & Miller 2015)
  - Wild cluster bootstrap for few clusters (Cameron, Gelbach, Miller 2008)

IMPLEMENTATION (multiple languages):
  Stata:    reghdfe y x, absorb(fe1 fe2) cluster(cluster_var)
  Python:   import linearmodels; mod = PanelOLS.from_formula('y ~ x + EntityEffects + TimeEffects', data=df); res = mod.fit(cov_type='clustered', cluster_entity=True)
  R:        library(fixest); feols(y ~ x | fe1 + fe2, data=df, cluster=~cluster_var)
```

### 2.2 Instrumental Variables

```
CHECK: If IV/2SLS is used:
  - Instrument relevance: F-stat > 10? (Stock-Yogo critical values)
  - Instrument validity: exclusion restriction argued convincingly?
  - Over-identification test if multiple instruments?
  - Reduced form reported?
  - Local average treatment effect (LATE) interpretation discussed?

COMMON ISSUES:
  - Weak instrument (F < 10) not acknowledged
  - Exclusion restriction untestable but also unargued
  - Too many instruments → over-fitting first stage
  - LATE vs ATE confusion

FIX SUGGESTIONS:
  - Report effective F-stat (Olea & Pflueger 2013)
  - Always report reduced form alongside 2SLS
  - Discuss who the compliers are (LATE interpretation)
  - Anderson-Rubin confidence intervals if weak instrument suspected

IMPLEMENTATION:
  Stata:    ivregress 2sls y (x = z), first; estat firststage
  Python:   from linearmodels.iv import IV2SLS; mod = IV2SLS.from_formula('y ~ 1 + controls + [x ~ z]', data=df); res = mod.fit(cov_type='robust')
  R:        library(ivreg); ivreg(y ~ x + controls | z + controls, data=df); summary(., diagnostics=TRUE)
```

### 2.3 Difference-in-Differences

```
CHECK: DID assumptions and implementation:
  - Parallel trends assumption: tested/visualized?
  - Pre-treatment dynamics: event study plot shown?
  - Staggered adoption: appropriate estimator?
  - Anticipation effects: considered?
  - Composition changes in treated/control groups?

COMMON ISSUES:
  - No pre-trend visualization
  - TWFE with staggered treatment and heterogeneous effects → biased
  - Parallel trends "tested" by insignificant pre-coefficients (low power issue)
  - Treatment and control groups diverging before treatment

FIX SUGGESTIONS:
  - Always show event study plot with pre-periods
  - For staggered: use CS/SA/dCdH estimator
  - Rambachan & Roth (2023) sensitivity analysis for parallel trends
  - Discuss power to detect pre-trends

IMPLEMENTATION:
  Stata:    did_multiplegt_dyn y group time treatment, effects(5) placebo(3)
            csdid y controls, ivar(id) time(year) gvar(first_treat)
  Python:   # Callaway-Sant'Anna not yet in Python standard; use R via rpy2
            # or: differences package (experimental)
            from differences import ATTgt; att = ATTgt(data=df, cohort_name='first_treat', ...)
  R:        library(did); att_gt(yname="y", tname="year", idname="id", gname="first_treat", data=df)
            library(fixest); feols(y ~ sunab(first_treat, year) | id + year, data=df)
```

### 2.4 Regression Discontinuity

```
CHECK: RDD implementation:
  - Running variable: manipulation? (McCrary/Cattaneo test)
  - Bandwidth: chosen by data-driven method? (Calonico et al.)
  - Specification: local polynomial? Order?
  - Falsification: no jump in covariates at cutoff?
  - Visualization: raw data binned scatter at cutoff?

COMMON ISSUES:
  - No manipulation test
  - Global polynomial (don't do this; Gelman & Imbens 2019)
  - Bandwidth sensitivity not shown
  - No covariate smoothness tests

FIX SUGGESTIONS:
  - Use rdrobust package (optimal bandwidth + bias correction)
  - Report multiple bandwidths as sensitivity
  - Show density plot at cutoff (rddensity)
  - Plot all covariates at cutoff

IMPLEMENTATION:
  Stata:    rdrobust y x, c(0) kernel(triangular) bwselect(mserd)
            rddensity x, c(0)
  Python:   from rdrobust import rdrobust, rdbwselect; est = rdrobust(Y, X, c=0)
  R:        library(rdrobust); rdrobust(y=Y, x=X, c=0); rdplot(y=Y, x=X, c=0)
```

---

## 3. Inference and Robustness

### 3.1 Standard Errors

```
CHECK: Are standard errors correctly computed?
  - Heteroskedasticity-robust if cross-sectional?
  - Clustered at correct level if panel/grouped?
  - Few clusters → appropriate correction?
  - Multiple hypothesis testing correction?

COMMON ISSUES:
  - Default (homoskedastic) SEs in cross-section
  - Clustering at too fine a level (undermines correction)
  - 5-10 clusters with regular cluster-robust SEs
  - 50+ hypothesis tests with no FDR/FWER correction

FIX SUGGESTIONS:
  - Default to robust SEs; report clustered as appropriate
  - Wild cluster bootstrap for < 30 clusters
  - Randomization inference for very few clusters
  - Benjamini-Hochberg or Romano-Wolf for multiple testing

IMPLEMENTATION:
  Stata:    boottest x, cluster(state) boottype(wild) reps(9999)
            rwolf y, indepvar(x1 x2 x3) cluster(state)
  Python:   # wildboottest package
            from wildboottest.wildboottest import wildboottest
            wildboottest(model, cluster=df['state'], B=9999)
  R:        library(fwildclusterboot); boottest(model, param="x", clustid=~state, B=9999)
            library(wildrwolf); rwolf(models, param="x", clustid=~state)
```

### 3.2 Robustness Checks

```
CHECK: Are results robust to reasonable alternatives?
  - Alternative specifications (controls, functional form)
  - Alternative samples (trimming, different periods)
  - Alternative measures of key variables
  - Alternative estimation methods
  - Sensitivity to outliers

MINIMUM ROBUSTNESS BATTERY:
  1. Main specification clearly labeled
  2. Add/remove controls that could be "bad controls"
  3. Alternative outcome measure or transformation
  4. Winsorize or trim extreme values
  5. Split-sample tests (by time period, subgroup)
  6. Oster (2019) bounds for omitted variable bias if observational

IMPLEMENTATION (Oster bounds):
  Stata:    psacalc delta y x controls, rmax(1.3*r2)
  Python:   # No standard package; manual implementation:
            # Compute R² movements as controls added, bound δ
  R:        # omitted_variable_bias package or manual
```

### 3.3 Reporting Standards

```
CHECK: Are results reported clearly and completely?
  - All coefficients of interest shown (not just significant ones)
  - Effect sizes interpretable (standardized or meaningful units)
  - Full regression tables (not just selected rows)
  - N clearly reported for each specification
  - R² or equivalent fit measure
  - Confidence intervals preferred over stars-only

COMMON ISSUES:
  - Reporting only significant results (p-hacking smell)
  - No economic/practical significance discussion
  - Tables show 5 specifications but text only discusses the "preferred" one
  - Dropping observations between columns without explanation

FIX SUGGESTIONS:
  - Report all pre-registered analyses regardless of significance
  - Always discuss magnitude: "a 1 SD increase in X → 0.2 SD change in Y"
  - Explain why each column differs and why one is preferred
  - Report exact p-values, not just stars
```

---

## 4. ML/AI Experiment Standards

### 4.1 Experimental Setup

```
CHECK: Is the ML experiment properly designed?
  - Dataset: public benchmark? Train/val/test split specified?
  - Baselines: appropriate and recent? Fairly tuned?
  - Hyperparameters: search method documented? Budget?
  - Random seeds: multiple runs reported?
  - Computational budget: GPU hours / training cost?

COMMON ISSUES:
  - Compare to 3-year-old baseline with default hyperparameters
  - Single random seed (no variance reported)
  - Unfair comparison (proposed model tuned, baselines default)
  - No computational cost comparison
  - Data leakage between train and test

FIX SUGGESTIONS:
  - Report mean ± std over at least 3-5 seeds
  - Use same hyperparameter search budget for baselines
  - Report wall-clock time and FLOPs alongside accuracy
  - Verify no temporal/ID leakage in splits
```

### 4.2 Evaluation Metrics

```
CHECK: Are metrics appropriate and complete?
  - Task-appropriate metric (not just accuracy for imbalanced data)
  - Multiple complementary metrics reported
  - Statistical significance of differences tested
  - Calibration assessed for probabilistic models
  - Ablation study for model components

COMMON ISSUES:
  - Accuracy only on 95/5 class imbalance
  - No confidence intervals on metrics
  - Claiming SOTA from 0.1% improvement within noise
  - No ablation (can't tell what contributes)

FIX SUGGESTIONS:
  - Report F1/AUC-ROC/AUC-PR for imbalanced problems
  - Bootstrap confidence intervals on metrics
  - Paired test (McNemar / bootstrap) for model comparison
  - Component ablation: remove one piece at a time

IMPLEMENTATION:
  Python:   from scipy.stats import bootstrap
            from sklearn.metrics import classification_report
            # Bootstrap CI on test metric:
            rng = np.random.default_rng(42)
            res = bootstrap((scores,), np.mean, n_resamples=10000, random_state=rng)
            print(f"95% CI: [{res.confidence_interval.low:.4f}, {res.confidence_interval.high:.4f}]")
```

### 4.3 Reproducibility

```
CHECK: Can the experiment be reproduced?
  - Code availability (or will be upon publication)
  - Data availability (or access instructions)
  - Environment specification (requirements.txt / Docker)
  - Random seeds fixed and reported
  - Model checkpoints available for large models

MINIMUM REPRODUCIBILITY STANDARD:
  1. Code on GitHub/artifact with README
  2. requirements.txt or conda environment.yml
  3. Single script to reproduce main results
  4. Seeds for all stochastic operations
  5. Expected runtime and hardware requirements
```

---

## 5. Medical / Clinical Trial Standards

### 5.1 CONSORT Checklist (Abbreviated)

```
CHECK: Does the RCT report follow CONSORT guidelines?
  - Participant flow diagram (enrollment → allocation → follow-up → analysis)
  - Sample size calculation with assumptions
  - Randomization method and allocation concealment
  - Blinding: who was blinded?
  - Primary outcome pre-specified
  - Intention-to-treat analysis
  - Adverse events reported

KEY ISSUES TO FLAG:
  - Per-protocol analysis only (no ITT)
  - Primary outcome changed after seeing data
  - No trial registration number
  - Subgroup analyses not pre-specified
  - Composite endpoints without component reporting
```

### 5.2 Observational Medical Studies (STROBE)

```
CHECK: Does the observational study follow STROBE?
  - Study design clearly stated (cohort/case-control/cross-sectional)
  - Eligibility criteria explicit
  - Exposure and outcome assessment methods described
  - Potential confounders identified and addressed
  - Missing data handling described
  - Sensitivity analyses for unmeasured confounding

FIX SUGGESTIONS:
  - E-value for unmeasured confounding (VanderWeele & Ding 2017)
  - DAG (directed acyclic graph) to justify adjustment set
  - Report both crude and adjusted estimates
```

---

## 6. 中文论文方法论审查要点

### 6.1 常见方法论问题（中文社科/经管）

```
检查项：
  - 内生性是否被讨论？使用什么识别策略？
  - 面板数据：固定效应的选择是否恰当？
  - 工具变量：相关性和外生性是否有充分论证？
  - 稳健性检验：是否包含替换变量/更换样本/更换方法？
  - 中介效应：是否使用了 Baron-Kenny（已过时）？

常见问题：
  - 使用温忠麟中介效应检验但不讨论内生性
  - "Hausman 检验选择固定效应"但不讨论为什么需要个体效应
  - 工具变量弱但仍报告 2SLS 结果
  - PSM-DID 但不验证平行趋势/共同支撑
  - GMM 矩条件过多，Hansen J 检验无效

修改建议：
  - 中介效应建议使用因果中介分析（Imai et al. 2010）或明确假设
  - PSM 仅解决可观测差异；不能替代因果识别
  - GMM 应报告工具变量数量、AR(1)/AR(2) 检验、Hansen J 统计量
  - 建议报告 Oster (2019) 系数稳定性检验
```

### 6.2 常见计量方法（中文论文对应实现）

```
基准回归（面板固定效应）：
  Stata:    xtreg y x controls, fe cluster(id)
  Python:   from linearmodels import PanelOLS
            mod = PanelOLS.from_formula('y ~ x + controls + EntityEffects', data=df.set_index(['id','year']))
            res = mod.fit(cov_type='clustered', cluster_entity=True)
  R:        library(plm); plm(y ~ x + controls, data=pdata, model="within", effect="individual")

PSM-DID：
  Stata:    psmatch2 treat x1 x2, outcome(y) neighbor(1) caliper(0.05)
            diff y, treated(treat) period(post) cov(x1 x2) kernel
  Python:   from causalinference import CausalModel
            # 或使用 DoWhy: import dowhy; model = dowhy.CausalModel(...)
  R:        library(MatchIt); m.out <- matchit(treat ~ x1 + x2, data=df, method="nearest")

系统 GMM：
  Stata:    xtabond2 y L.y x controls, gmm(L.y, lag(2 4)) iv(controls) twostep robust
  Python:   # pydynpd package (experimental)
            from pydynpd import regression; reg = regression.abond('y L.y x | gmm(y, 2:4) | iv(x)', df, ['id','year'])
  R:        library(plm); pgmm(y ~ lag(y,1) + x | lag(y, 2:4), data=pdata, effect="twoways", model="twosteps")
```

---

## 7. Quick Reference: Reviewer Decision Signals

```
METHODOLOGY SEVERITY LEVELS:

FATAL (recommend reject / major revision):
  - No identification strategy for causal claim
  - Data leakage between train and test
  - Primary outcome switched post-hoc
  - Sample selection directly correlated with treatment

MAJOR (require revision):
  - Weak instrument without alternative estimation
  - No robustness checks at all
  - Parallel trends visibly violated
  - Single random seed for ML results

MINOR (suggest in revision letter):
  - Could add additional robustness check
  - Standard errors could be clustered differently
  - Missing one common baseline in ML comparison
  - Reporting could be more complete
```
