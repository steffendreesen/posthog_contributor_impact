# Hierarchical model for commit impact

This note derives a two-level normal hierarchical model for PostHog commit data. It is the analogue of Hoff, *A First Course in Bayesian Statistics*, Chapter 8 (group comparisons with a shared within-group variance). It is a specification, not a fitted model.

The object of the model is a **contributor-level mean commit impact** θ<sub>j</sub>: how impactful a typical commit by contributor *j* is, after borrowing strength from the rest of the contributor population. The dashboard's "expected commit impact" series will later be a *display* derived from this (or from a later time-resolved version). It is not a level of the hierarchy below.

Sections 1–9 are the hierarchy. **Section 10 defines the per-commit impact score** *y* that the hierarchy consumes; it is a fixed, hand-set heuristic computed before any modeling.

**Out of scope here.** Gibbs / MCMC code; hierarchical variances σ<sub>j</sub>²; time, team, or other covariates.

---

## 1. Data and grouping

The observation unit is a **commit**. The grouping unit is a **contributor** (GitHub login). Both come from `FinalCommitSchema` in `github_etl/src/github_etl/schemas.py` (same shape in `notebooks/schemas.py`).

| Column | Role in this model |
| --- | --- |
| `commit_id` | Observation id. Unique. |
| `contributor` | Group label *j*. Nullable. |
| `committed_at` | Window / later daily display. Not a grouping layer here. |
| `branch`, `pr`, `pr_state`, `number_of_comments_on_pr` | Heuristic *inputs* to *y*, aligned lists. |
| `has_pr_been_merged_into_main` | Heuristic input (bool). |
| `connected_issue`, `number_of_comments_on_connected_issue` | Heuristic inputs, aligned lists. |

A commit may attach to several PRs or issues. That is a feature-construction problem for *y*, not extra hierarchy. The model still has one *y* per `commit_id`.

**Simulated 90-day table** (`notebooks/data/simulated_commits.parquet`): *N* = 4120 commits, *m* = 28 named contributors, 114 commits with missing `contributor`. Among named contributors, *n*<sub>j</sub> ranges from 14 to 553 (median 86.5). About 64% of commits have a PR merged into main; about 99% have at least one PR; about 52% have at least one connected issue.

**Missing contributor.** Drop those rows from the hierarchical likelihood. They have no group. Do not invent a `"null"` group unless we later want an explicit bot / unknown bucket.

**Exchangeability.** Contributors are treated as exchangeable: relabeling logins does not change the model. That is the justification for i.i.d. θ<sub>j</sub> | μ, τ². It is a modeling choice. We currently have no team, tenure, or role covariates that would break it.

---

## 2. The response: commit impact *y*<sub>i,j</sub>

*y*<sub>i,j</sub> is the **impact of commit *i* by contributor *j***. It is not a column in the table; it is a heuristic function of the columns above (merge-to-main, PR comment volume, linked issues, issue comment volume). **Section 10 defines it.** This section states only what the hierarchy needs from it.

Two properties the model relies on:

1. **The scale of the hyperpriors follows from the score's unit.** Hoff could set μ<sub>0</sub> = 50 because the ELS test was nationally scaled. Our unit is whatever Section 10 defines — there, a bounded 1-to-10 point scale, which is what makes the prior settings in Section 10.6 interpretable.
2. **Gaussian *y* is a first specification, not a claim about the heuristic.** If a future version of the score is nonnegative and strongly right-skewed, model log *y* (or another transform) with the same hierarchy. That is still Hoff's Chapter 8 model on the transformed scale. It is *not* the hierarchical-variance extension. The Section 10 score is close to symmetric on the simulated data (skew −0.22), so no transform is applied.

Index commits within contributor *j* by *i* = 1, …, *n*<sub>j</sub>. Let ȳ<sub>j</sub> = *n*<sub>j</sub><sup>−1</sup> Σ<sub>i=1</sub><sup>n<sub>j</sub></sup> *y*<sub>i,j</sub>.

---

## 3. Why a hierarchy (the three pooling regimes)

The analysis questions, rewritten from Hoff's school example:

1. What is typical commit impact in this population of contributors, and how much do contributors' typical impacts differ?
2. How should we estimate contributor *j*'s mean θ<sub>j</sub> when *n*<sub>j</sub> ranges from a handful of commits to several hundred?
3. How much of the variation in commit impact is between contributors versus within a contributor's own commits?
4. How should we rank contributors, and what would we predict for a new contributor?

**No pooling.** Estimate θ<sub>j</sub> by ȳ<sub>j</sub> alone. The sampling variance is σ² / *n*<sub>j</sub>. For a low-volume contributor this interval is wide. Ranking by ȳ<sub>j</sub> then overstates how different the "best" and "worst" contributors are, and the extremes are disproportionately the small-*n*<sub>j</sub> groups. Write

<p align="center">
ȳ<sub>j</sub> = θ<sub>j</sub> + <i>e</i><sub>j</sub>, &emsp; <i>e</i><sub>j</sub> ~ N(0, σ² / <i>n</i><sub>j</sub>).
</p>

Then Var(ȳ<sub>j</sub>) = τ² + σ² / *n*<sub>j</sub> > τ² = Var(θ<sub>j</sub>), so the empirical spread of the ȳ<sub>j</sub> is an inflated version of the spread of the θ<sub>j</sub>. Selecting the maximum ȳ<sub>j</sub> selects for favorable noise: E[*e*<sub>j</sub> | ranked first] > 0. That is the same mechanism as Hoff's plot of school average against sample size.

This matters here because *n*<sub>j</sub> is badly unbalanced (14 vs 553 in the simulation). A contributor with 14 unusually high-scoring commits will look like a star under no pooling whether or not their latent mean is high.

**Complete pooling.** One shared mean for every contributor. That removes the noise problem and answers a different question: it cannot represent contributor differences at all.

**Partial pooling.** Put a common population distribution on the θ<sub>j</sub>. Small τ² drives the fit toward complete pooling; large τ² recovers nearly independent estimates. τ² is inferred, so the degree of pooling is a posterior quantity, not a tuning knob.

---

## 4. The model (Hoff Chapter 8, shared σ²)

This is the **simpler** model: one within-contributor variance shared by everyone. It is *not* the Chapter 11 extension where each group has its own σ<sub>j</sub>².

**Level 1 — within contributor (sampling model).**

<p align="center">
<i>y</i><sub>i,j</sub> = θ<sub>j</sub> + ε<sub>i,j</sub>, &emsp; ε<sub>i,j</sub> | σ² ~ i.i.d. N(0, σ²), &emsp; <i>i</i> = 1, …, <i>n</i><sub>j</sub>.
</p>

θ<sub>j</sub> is contributor *j*'s mean commit impact. σ² is how much a given contributor's commits scatter around that mean. Sharing σ² means we do **not** let some contributors be "more variable" than others in this specification. Failure mode if that is wrong: a contributor whose impact is genuinely bursty (many small chores, occasional large landings) will have their θ<sub>j</sub> overshrunk or their residuals mis-scaled, because the model attributes that extra spread to the common σ².

**Level 2 — between contributors (grouping / pooling layer).**

<p align="center">
θ<sub>j</sub> | μ, τ² ~ i.i.d. N(μ, τ²), &emsp; <i>j</i> = 1, …, <i>m</i>.
</p>

μ is the mean of the *population of contributors*, not the mean of commits (the commit-weighted mean pulls toward high-volume people). τ² is between-contributor variance of mean impact. Failure mode if τ² is forced too small: everyone collapses toward μ and we cannot rank. If it is forced too large: we are back to noisy ȳ<sub>j</sub>.

**Level 3 — hyperpriors.**

<p align="center">
μ ~ N(μ<sub>0</sub>, γ<sub>0</sub>²)
</p>

<p align="center">
τ² ~ InverseGamma(η<sub>0</sub> / 2, &nbsp; η<sub>0</sub> τ<sub>0</sub>² / 2)
</p>

<p align="center">
σ² ~ InverseGamma(ν<sub>0</sub> / 2, &nbsp; ν<sub>0</sub> σ<sub>0</sub>² / 2)
</p>

Each is semiconjugate: every full conditional is a named family, but the joint posterior is not. Weak prior sample sizes η<sub>0</sub> = ν<sub>0</sub> = 1 are the Hoff default. Location/scale (μ<sub>0</sub>, γ<sub>0</sub>², τ<sub>0</sub>², σ<sub>0</sub>²) follow from the score's unit; Section 10.6 sets them once that unit is fixed.

**What is not in this model.** No σ<sub>j</sub>². No time random walk. No PR-level or issue-level hierarchy. Those columns affect *y*, then stop.

---

## 5. Shrinkage

If μ, τ², and σ² were known, the posterior for each θ<sub>j</sub> would be normal with precision *n*<sub>j</sub> / σ² + 1/τ² and mean

<p align="center">
E[θ<sub>j</sub> | <b>y</b><sub>j</sub>, μ, τ², σ²] = <i>w</i><sub>j</sub> ȳ<sub>j</sub> + (1 − <i>w</i><sub>j</sub>) μ
</p>

<p align="center">
<i>w</i><sub>j</sub> = (<i>n</i><sub>j</sub> / σ²) / (<i>n</i><sub>j</sub> / σ² + 1/τ²).
</p>

*w*<sub>j</sub> is the fraction of that contributor's information that comes from their own commits. As *n*<sub>j</sub> grows, *w*<sub>j</sub> → 1 and the estimate sits on ȳ<sub>j</sub>. As *n*<sub>j</sub> → 0 or τ² → 0, it collapses onto μ.

Toy numbers, to make the weights concrete. Suppose after the heuristic we have σ² = 1 and τ² = 0.25 (ICC = 0.2; see below). Then

| *n*<sub>j</sub> | *w*<sub>j</sub> | reading |
| ---: | ---: | --- |
| 14 | 14 / (14 + 4) = 0.78 | about one-fifth of the estimate is pooled |
| 86 | 86 / (86 + 4) = 0.96 | almost no pooling |
| 553 | 553 / (553 + 4) ≈ 1 | own data dominates |

So in this dataset the hierarchy is doing most of its work on the bottom of the *n*<sub>j</sub> distribution. That is the point: we still want to *rank* those people, but we do not want 14 noisy commits to occupy the ends of the list.

The full posterior averages this shrinkage over posterior uncertainty in (μ, τ², σ²). That averaging is what the Gibbs sampler performs.

---

## 6. Variance decomposition

A randomly chosen commit from a randomly chosen contributor has marginal variance τ² + σ². The **intraclass correlation**

<p align="center">
ICC = τ² / (τ² + σ²)
</p>

is both (a) the correlation between two commits by the same contributor and (b) the fraction of total variance that is between contributors.

This is the claim layer to keep separate from ranking. A large ICC means contributors really do differ in typical impact; a small ICC means most of the action is commit-to-commit scatter, and θ<sub>j</sub> estimates will sit near μ. Neither is a statement about *total* output: a high-θ<sub>j</sub> person with *n*<sub>j</sub> = 14 still contributed less mass than a middling-θ<sub>j</sub> person with *n*<sub>j</sub> = 500. If the dashboard wants "total expected impact" it should use a sum (or *n*<sub>j</sub> θ<sub>j</sub>), not θ<sub>j</sub> alone.

---

## 7. Posterior via Gibbs

The joint *p*(θ<sub>1</sub>, …, θ<sub>m</sub>, μ, τ², σ² | **y**) has no closed form. Every full conditional does.

**1. Contributor means.** Independently given the hyperparameters,

<p align="center">
θ<sub>j</sub> | <b>y</b><sub>j</sub>, μ, τ², σ² ~ N(<i>m</i><sub>j</sub>, <i>v</i><sub>j</sub>)
</p>

<p align="center">
<i>m</i><sub>j</sub> = (<i>n</i><sub>j</sub> ȳ<sub>j</sub> / σ² + μ / τ²) / (<i>n</i><sub>j</sub> / σ² + 1/τ²)
</p>

<p align="center">
<i>v</i><sub>j</sub> = 1 / (<i>n</i><sub>j</sub> / σ² + 1/τ²).
</p>

**2. Population mean.** With θ̄ = *m*<sup>−1</sup> Σ<sub>j</sub> θ<sub>j</sub>,

<p align="center">
μ | <b>θ</b>, τ² ~ N( <i>m</i><sub>μ</sub>, <i>v</i><sub>μ</sub> )
</p>

<p align="center">
<i>m</i><sub>μ</sub> = (<i>m</i> θ̄ / τ² + μ<sub>0</sub> / γ<sub>0</sub>²) / (<i>m</i> / τ² + 1/γ<sub>0</sub>²)
</p>

<p align="center">
<i>v</i><sub>μ</sub> = 1 / (<i>m</i> / τ² + 1/γ<sub>0</sub>²).
</p>

The θ<sub>j</sub> act as *m* observations from N(μ, τ²). With *m* = 28 this update is informed by far fewer quantities than σ² is, so μ and τ² will mix more slowly than the θ<sub>j</sub>.

**3. Between-contributor variance.**

<p align="center">
τ² | <b>θ</b>, μ ~ InverseGamma( (η<sub>0</sub> + <i>m</i>) / 2, &nbsp; [η<sub>0</sub> τ<sub>0</sub>² + Σ<sub>j=1</sub><sup>m</sup> (θ<sub>j</sub> − μ)²] / 2 )
</p>

**4. Within-contributor variance.** With *n* = Σ<sub>j</sub> *n*<sub>j</sub>,

<p align="center">
σ² | <b>θ</b>, <b>y</b> ~ InverseGamma( (ν<sub>0</sub> + <i>n</i>) / 2, &nbsp; [ν<sub>0</sub> σ<sub>0</sub>² + Σ<sub>j=1</sub><sup>m</sup> Σ<sub>i=1</sub><sup>n<sub>j</sub></sup> (<i>y</i><sub>i,j</sub> − θ<sub>j</sub>)²] / 2 )
</p>

Cycle 1–4. Check traces, autocorrelation, and effective sample size; expect μ and τ² to be the sticky pair.

**What to read off.**

- Posterior of θ<sub>j</sub>: typical commit impact for contributor *j*.
- Monte Carlo frequency θ<sub>j</sub> > θ<sub>k</sub> and posterior ranks — not a ranking of ȳ<sub>j</sub>.
- Posterior of (μ, τ², σ²) and of the ICC.
- Posterior predictive for a *new* contributor θ̃ ~ N(μ, τ²), then ỹ ~ N(θ̃, σ²).

---

## 8. Relation to the dashboard

The scaffold in `app/` plots a daily series labeled "expected commit impact" per engineer. This model does not have a day index.

A first derived display, once θ<sub>j</sub> is estimated: on day *t*, contributor *j*'s expected *total* impact is *n*<sub>j,t</sub> θ<sub>j</sub> (using that day's commit count, and ignoring within-day composition of *y*). A first derived *per-commit* display is just θ<sub>j</sub>, constant over the window.

Neither is a third hierarchical layer. A time-varying θ<sub>j,t</sub> would be a different model.

---

## 9. Explicitly not this model

Hoff's hierarchical-variance extension (each group has σ<sub>j</sub>², those variances themselves shrunk toward σ<sub>0</sub>²) is deferred. It would matter if we believed some contributors have systematically more variable commit impact than others, and if the small-*n*<sub>j</sub> people needed shrinkage on the second moment as well as the first. Cost: ν<sub>0</sub> is no longer Gibbs-closed.

Also deferred: using `committed_at` as a grouping or regression index; modeling PRs or issues as extra levels; a point-mass / hurdle for zero-impact commits; learning the score weights of Section 10 from data rather than fixing them.

---

## 10. The commit impact score

This section defines the *y*<sub>i,j</sub> that Section 2 assumes. It is a **fixed, hand-set heuristic**, computed per commit before any modeling. It is deliberately not learned: the point is that a reader can look at one commit row and reproduce its score by hand.

The score reads only `FinalCommitSchema` columns. Aggregation to the contributor level is the hierarchical model's job (Sections 4–7), not the score's — the score stops at one number per `commit_id`.

### 10.1 Design rules

1. **Additive points.** Components add. No products, no interactions. A component's weight *is* the maximum number of points it can contribute, so the weights are directly comparable.
2. **Every component is normalized to [0, 1] before weighting.** Raw comment counts never enter the sum. Otherwise the weights would not be comparable across components measured in different units.
3. **Counts saturate.** Discussion volume has diminishing returns — a 20-comment thread is not twenty times a 1-comment thread. Counts pass through *c* / (*c* + *k*), where *k* is the **half-saturation point**: at *c* = *k* the component earns exactly half its weight. This is bounded in [0, 1] with no clipping and one interpretable constant.
4. **Bounded total.** The score lives in a fixed range (1 to 10), so no single outlier commit can dominate a contributor's mean.

### 10.2 Components and weights

| # | Component | Signal, normalized to [0,1] | Weight (max points) |
| :--: | --- | --- | ---: |
| 1 | **Base** | 1 (every commit) | **1.0** |
| 2 | **Landing** | *L*, the ladder in 10.3 | **3.0** |
| 3 | **Review depth** | *c*<sub>pr</sub> / (*c*<sub>pr</sub> + 4) | **2.0** |
| 4 | **Issue attachment** | 1 if the commit's PRs link ≥ 1 issue, else 0 | **1.5** |
| 5 | **Issue complexity** | *c*<sub>iss</sub> / (*c*<sub>iss</sub> + 6) | **2.5** |
| | | **Total** | **10.0** |

<p align="center">
<i>y</i><sub>i,j</sub> = 1.0 + 3.0·<i>L</i> + 2.0·<i>c</i><sub>pr</sub>/(<i>c</i><sub>pr</sub> + 4) + 1.5·<i>A</i> + 2.5·<i>c</i><sub>iss</sub>/(<i>c</i><sub>iss</sub> + 6)
</p>

where, for a given commit,

- *c*<sub>pr</sub> = **max** of `number_of_comments_on_pr` over the commit's PRs (0 if none),
- *c*<sub>iss</sub> = **sum** of `number_of_comments_on_connected_issue` over the commit's linked issues (0 if none),
- *A* = 1 if `connected_issue` is non-empty, else 0,
- *L* = the landing ladder below.

Max over PRs, sum over issues: a commit on two PRs is usually one piece of work plus a cherry-pick, so the second PR's thread is not new discussion; two linked issues are usually two distinct problems, so they accumulate.

### 10.3 The landing ladder *L*

`pr_state` and `has_pr_been_merged_into_main` together distinguish four outcomes. Partial credit for in-flight work avoids scoring an open PR the same as an abandoned one.

| Situation | *L* | Points (3.0 · *L*) | Rationale |
| --- | :--: | ---: | --- |
| `has_pr_been_merged_into_main` is true | 1.00 | 3.00 | Work reached the default branch |
| `pr_state` contains `MERGED`, but not into main | 0.50 | 1.50 | Landed on a feature or stacked branch |
| `pr_state` contains `OPEN` | 0.25 | 0.75 | In flight, outcome unresolved |
| `pr_state` contains only `CLOSED` | 0.00 | 0.00 | Abandoned without merging |
| No PR at all (`pr` is empty) | 0.00 | 0.00 | No review trail |

With multiple PRs, take the best rung.

### 10.4 Why these weights

Landing carries the most weight (3.0) because merging into main is the least ambiguous evidence in the table that work was accepted. It is also the only component that is nearly unforgeable by activity alone.

Issue complexity (2.5) outranks review depth (2.0) deliberately. Issue comment volume proxies **how hard the reported problem was**, which is a property of the work. PR comment volume is more ambiguous: a long review thread can mean a subtle, carefully-scrutinized change, or a change that needed a lot of correction. It gets weight because both readings imply the change mattered enough to discuss, but it gets less than issue complexity because its sign is not clean.

Issue attachment (1.5) is separate from complexity so that linking a commit to tracked work earns credit even when the issue thread is quiet. Base (1.0) keeps every commit worth something and puts a floor on the score.

Half-saturation constants come from the simulated distributions: PR comments have median 2 and 75th percentile 4, so *k*<sub>pr</sub> = 4 puts the three-quarter-percentile PR at half of the review points. Issue comments run higher (median 4, 75th percentile 6), so *k*<sub>iss</sub> = 6.

### 10.5 Worked examples

| Commit | Base | Landing | Review | Attach | Complexity | **Total** |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| No PR, no issue | 1.0 | 0.0 | 0.00 | 0.0 | 0.00 | **1.00** |
| Open PR, 2 comments, no issue | 1.0 | 0.75 | 0.67 | 0.0 | 0.00 | **2.42** |
| Merged to main, 2 comments, no issue | 1.0 | 3.0 | 0.67 | 0.0 | 0.00 | **4.67** |
| Merged to main, 4 comments, 1 issue with 6 comments | 1.0 | 3.0 | 1.00 | 1.5 | 1.25 | **7.75** |
| Merged to main, 11 comments, 3 issues, 18 comments total | 1.0 | 3.0 | 1.47 | 1.5 | 1.88 | **8.84** |

The last row is the maximum actually attained on the simulated table. The theoretical 10.0 requires unbounded comment counts, so real scores compress into roughly 1 to 9.

### 10.6 Behavior on the simulated table

Scoring all 4120 commits of `notebooks/data/simulated_commits.parquet`:

| Quantity | Value |
| --- | --- |
| Range | 1.00 to 8.84 |
| Mean / median | 5.19 / 5.08 |
| Std. dev. | 1.86 |
| Skew | −0.22 |
| Quartiles | 4.00 / 5.08 / 6.85 |

Mean points contributed per commit, against each component's ceiling:

| Component | Mean points | Ceiling | Share of ceiling used |
| --- | ---: | ---: | ---: |
| Base | 1.00 | 1.0 | 100% |
| Landing | 2.18 | 3.0 | 73% |
| Review depth | 0.68 | 2.0 | 34% |
| Issue attachment | 0.78 | 1.5 | 52% |
| Issue complexity | 0.56 | 2.5 | 22% |

Landing dominates in practice, which is intended. Issue complexity uses the least of its ceiling, meaning *k*<sub>iss</sub> = 6 is conservative — if real PostHog issues run longer than the simulation's, this component will spread out more.

Moment estimates of the variance components (naive, not the Gibbs posterior): σ² ≈ 3.31, τ² ≈ 0.48, so **ICC ≈ 0.13**. Most variation is commit-to-commit, not contributor-to-contributor. That is the regime where the hierarchy earns its keep: shrinkage weights come out at *w*<sub>j</sub> ≈ 0.67 for a 14-commit contributor versus 0.99 for a 553-commit one, and shrinkage narrows the range of contributor means from 3.93 raw points to 2.87.

These are order-of-magnitude sanity checks on simulated data, not results. They exist to confirm the score produces a roughly symmetric, bounded response with a non-degenerate between-contributor component — the conditions Sections 4–7 assume.

Because the score is bounded in [1, 10] with an observed mean near 5, the Section 4 hyperpriors can now be set concretely: μ<sub>0</sub> = 5, γ<sub>0</sub>² = 4 (weak, covers the plausible range of the population mean), τ<sub>0</sub>² = 0.5, σ<sub>0</sub>² = 3, η<sub>0</sub> = ν<sub>0</sub> = 1.

### 10.7 Known distortions

**PR attributes are inherited by every commit on the PR.** `number_of_comments_on_pr` is a PR-level fact broadcast to each of its commits. A contributor who splits one PR into eight commits collects the review-depth and landing points eight times. In the simulation, PRs hold 1 to 8 commits, so this is a real effect. It inflates *total* impact for commit-splitters, but the hierarchical model estimates a *mean* per commit (θ<sub>j</sub>), which is much less sensitive — splitting adds both numerator and denominator. Dividing PR-derived points by the PR's commit count is the obvious fix if it turns out to matter; it costs interpretability, so it is not in v1.

**Comment counts measure attention, not quality.** A contentious or poorly-specified change attracts comments. The score cannot separate that from careful review of an important change.

**Commits with no contributor** (114 in the simulation) still receive a score but are dropped before fitting, per Section 1.

**The weights are asserted, not estimated.** They encode a judgment about what matters, which is the point of an interpretable heuristic, but it means disagreement about the ranking is usually disagreement about the five numbers in 10.2 rather than about the model. Those numbers are the intended tuning surface.
