# Methods: how v2 optimizes, validates, trades off, simulates and recommends

Every number on the dashboard and in the CEO deck is produced by one of the methods below, on one
canonical model version. Labels in the outputs say which kind of number it is:

| Label | Meaning |
|---|---|
| **PROVEN** | An optimization result with a solver-proven bound (optimal, or within the stated gap), checked by the independent validator |
| **SIMULATED** | A Monte Carlo estimate (expected value, percentile, probability) with its sampling error |
| **ESTIMATE** | An input assumption (a distribution, a correlation, a default) — calibrated or not, as flagged |

## 1. Canonical, versioned model

`canonical.py` renders the portfolio (and the uncertainty model) as sorted JSON and hashes it
(SHA-256, first 16 hex). The fingerprint plus a scenario key is the **model version** that every
artifact carries (`ModelVersion`). Numbers are normalized (`180` and `180.0` are the same input) and
funding shares are written in one form, so an Excel round trip keeps the fingerprint. A Pareto
frontier refuses alternatives from another version (`IncompatibleScopeError`); the dashboard shows a
warning badge if any artifact's version differs.

## 2. Multi-solver optimization and independent validation

**One formulation, several engines.** The MILP built by `optimizer.PortfolioModel` (v1 rules: capacity,
ring-fencing, eligibility, transfers, budgets, dependencies, commitments, governance) is compiled once
into a sparse matrix (`solvers/base.py: MilpMatrix`) and given to:

| Engine | Role | How |
|---|---|---|
| HiGHS | primary | highspy on the matrix; returns objective, dual bound, gap, node count |
| SCIP | independent validation | PySCIPOpt on the *same* matrix; no warm start, so its search is independent |
| CBC | optional benchmark | PuLP's bundled CBC; its dual bound is not exposed, so its proof is solver-reported |
| CP-SAT | discrete scheduling | OR-Tools CP-SAT on an **integer reformulation** (below) |
| OSQP | optional | convex QP resource leveling of a fixed plan (below) |
| IPOPT | optional | detected if installed; the current model is linear, so no NLP is required |

**Normalized results.** Every engine returns `NormalizedResult(status, objective, bound, gap, runtime,
values)`. Status is one of optimal (proven within the requested gap), feasible (incumbent, gap not
proven), infeasible, unbounded, timeout, error, incompatible, unavailable. The *proof* is derived:
`optimal` (gap ≤ 1e-6), `within_gap`, `feasible` (unproven) or `none`.

**CP-SAT reformulation.** Hours become integers; demand is `Σ floor(100·eff)·y ≥ ceil(100·req)`;
capacities, transfer ceilings and budgets are rounded down, costs rounded up (money in $0.1). Every
rounding is conservative, so a CP-SAT plan is feasible for the continuous model; its starts are then
re-evaluated exactly by HiGHS (an LP with starts fixed), which gives the comparable objective. CP-SAT's
bound refers to the scaled model and is labelled approximate. Soft mode, balanced/coverage objectives,
minimum shared-capacity shares, custom objectives and models above 60,000 integer variables are
reported **incompatible** with the reason, never solved approximately.

**Independent validator** (`solvers/validate.py`). Re-derives every business rule from the domain data —
not from the model code — and checks a plan: windows, scope, commitments, dependencies, demand month by
month, route eligibility (ownership, eligibility rules, funding restrictions, tier-allowed and active
transfer rules), pool capacity, transfer ceilings and retained hours, budgets (carry-forward or
monthly), governance, and that the reported objective equals the recomputed one.

**Orchestration and the Solver Confidence Report** (`solvers/orchestrate.py`).
1. Primary engine; if it returns no solution, fall back to SCIP, then CBC (recorded).
2. Validate the plan independently.
3. Re-solve with the validation engines and validate their plans.
4. Compare: objective differences against the engines' gaps, *each objective against the other engines'
   proven bounds*, status conflicts (one says infeasible, another has a validated plan).
5. Level: `CONFIRMED_OPTIMAL` (proven, independently matched) · `PROVEN_SINGLE` · `FEASIBLE_UNPROVEN` ·
   `DISAGREEMENT` (diagnostics; the plan stands — a disagreement never rejects a portfolio by itself) ·
   `INFEASIBLE` (with the engines that agree and the soft-mode diagnosis of which commitments break).
   The certified gap uses the best exact bound across engines.

**OSQP leveling.** With starts fixed the model is linear in hours. OSQP minimizes the sum of squared
pool-month utilizations over the same constraints, spreading load across eligible pools and routes.

## 3. Pareto frontier analysis

**Objectives** (`pareto/objectives.py`) — each with direction, unit, normalization and formula, and two
implementations (MILP expression; evaluation of a solved plan) that tests check agree:

| Key | Objective | Direction | Unit |
|---|---|---|---|
| value | Business value, time-adjusted, net of route costs | max | $K |
| npv | Financial NPV: value at completion and monthly costs discounted at `discount_rate` | max | $K |
| alignment | Σ 100 × enterprise contribution ÷ LOB strategic priority | max | points |
| okr | Σ over KPI targets of min(1, attributed benefit ÷ target) | max | targets |
| acceleration | Value delivered by the milestone month (default H/2) | max | $K |
| compliance | Compliance and mandatory initiatives included | max | initiatives |
| cost | Total investment + route costs | min | $K |
| risk | Σ execution risk × cost | min | $K at risk |
| overload | Peak pool-month utilization | min | share |
| time_to_value | Value-weighted months to benefit (unselected = H+1) | min | months |
| diversification | Minimum LOB share of attributable value captured | max | share |

Administrators add objectives in `config/decision.yaml`: `sum_i sel_i × expr(i)` where `expr` is a safe
arithmetic expression over initiative fields and custom attributes (`Project Attributes` sheet).

**Epsilon-constraint (primary).** Payoff table (each objective optimized alone, then lexicographically
held while the others improve) gives ideal and nadir. Then maximize the primary objective plus an
AUGMECON term `δ · Σ_k range_primary/range_k · g_k` subject to `g_k ≥ e_k` for the secondary objectives
(`g` = maximization form), over a grid from nadir to ideal. Infeasible grid combinations are recorded.
Adaptive refinement bisects gaps between neighbouring efficient plans larger than a normalized
threshold. Warm starts (HiGHS) from the previous plan, fixed seeds and deterministic ordering make runs
reproducible (tested).

**Weighted sum (secondary).** Maximize `Σ w_k (g_k − nadir_k)/(ideal_k − nadir_k)` with every weight ≥
0.001 (zero weights return weakly dominated plans). Normalization puts dollars, months and risk on one
scale. **Limitation**: in a discrete portfolio problem the frontier is non-convex; weighted sums reach
only supported points on the convex hull. `tests/test_pareto.py` builds a portfolio where an efficient
plan lies below the hull: epsilon-constraint finds it; no weight vector does.

**NSGA-II (optional, pymoo).** Integer genome (0 = out, k = k-th start option). Every offspring is
repaired (windows, commitments, dependencies, aggregate capacity and budget), then checked exactly by an
LP with the starts fixed (dropping the lowest value-density optional initiative up to `max_repairs`
times). Returned plans are therefore *certified feasible* and independently validated, but labelled
**approximate**: their efficiency is not proven; they are ranked against the exact frontier.

**Dominance.** A dominates B if A is at least as good on every frontier objective and strictly better on
one, with tolerance `1e-6 + 1e-4 × range` per objective. Fast non-dominated sorting assigns Pareto ranks;
identical plans are merged (other methods kept as aliases); near-duplicates (equal on every objective
within tolerance) are marked `duplicate`. Each alternative stores selection, start months, monthly
capability allocations, LOB allocations, budget profile, objective vector, solver provenance, proof,
validation, Pareto rank and dominance status.

## 4. Monte Carlo risk evaluation

**Uncertainty model** (`risk/uncertainty.py`, optional intake sheets). Per initiative: effort (hours and
cost), duration, benefit realization, start slip, delivery failure (probability and benefit lost). Per
pool: availability, hiring delay, productivity. Shared drivers (inflation, capability shortages, vendor
delays, regulatory change, shared-platform delay, or any you define) with effects on cost, effort,
duration, benefit, start delay or availability, applied to every initiative or pool they reach.
Distributions: triangular, Beta-PERT, normal, truncated normal, lognormal, uniform, Bernoulli,
discrete scenarios, empirical. **No normal default is assumed.**

**Correlation.** Drivers are sampled jointly through a Gaussian copula (Cholesky of the correlation
matrix). The matrix must be symmetric, unit-diagonal, within [−1, 1] and positive semi-definite;
otherwise it is rejected, or — only if configured — repaired to the nearest valid matrix (Higham) and
flagged. Tests check that rank correlation matches the copula's `6/π·asin(ρ/2)`.

**Schedule- and resource-aware execution** (`risk/simulate.py`), vectorized across iterations, month by
month: sample → start when the planned month (+ slip and driver delays) has come and every prerequisite
has finished → request each capability at `work ÷ (D × duration multiplier)` per month → allocate first
along the plan's own routes, then through any route the plan's tier allows (never unauthorized
ring-fenced capacity) within sampled capacity (`A × availability × productivity`, zero until start +
hiring delay), rule ceilings and retained hours → unmet hours stay with the initiative and are recorded
as shortage → finish when no work remains → realize benefits (business value with the optimizer's
schedule erosion; NPV discounted monthly; failures forfeit their share) → spend follows delivered work.
A deterministic replay (no uncertainty) reproduces the optimized plan exactly (tested).

**Fixed-plan vs adaptive execution.** *Fixed*: the approved plan, planned order, no contingency, no
re-sequencing beyond dependency waits. *Adaptive*: commitments first then value density; a start is
deferred (≤ `max_defer_months`) when less than `start_gate` of a month's need is free; approved
contingency hours (`contingency_pct` of capacity at `contingency_rate`) cover shortfalls. Recourse is
bounded and its use reported; unlimited resources or budget are never assumed.

**Common random numbers and parallelism.** Each random variable has its own stream keyed by (seed,
chunk, entity, variable), independent of which initiatives a plan selects. Alternatives therefore face
the same futures, and results are identical for any number of worker processes (tested).

**Outputs and definitions.** Expected NPV, ROI, realized value, cost, completion; P10/P50/P90 with the
direction stated (P10 = 10% of futures are worse — the downside for value and NPV; for cost and
completion the downside is P90); P(cost overrun: cost > plan × (1 + reserve)); P(any LOB budget breach
within the horizon); P(missing a committed date: deadline, or planned finish + grace for mandatory,
compliance and critical work); P(OKR targets met: realized attributed benefit within the horizon ≥
target; separately for targets the plan meets deterministically); P(capability shortage); VaR and CVaR
of the NPV shortfall versus plan at α (default 95%); P(value ≥ minimum acceptable); risk contributions
(each initiative's average shortfall in the worst tail) and sensitivities (rank correlation of each
input with NPV).

**Convergence.** Standard error from 20 batch means; probability SE `√(p(1−p)/N)`; distribution-free
95% intervals for percentiles; a running-mean trace. Converged when SE(mean NPV) ≤ 1% of |mean| and
every probability SE ≤ 0.01 (configurable). More iterations reduce sampling error only — not the error
in the assumptions.

## 5. Risk-adjusted recommendation

**Ex-post risk evaluation** (`decision/recommend.py`). Candidates (efficient alternatives spread along
the frontier, the payoff extremes, the validated baseline, the stochastic plan) are simulated on common
random numbers and ranked:

1. *Constraints* (leadership decision rules), e.g. `within_budget ≥ 0.80`, `meet_key_dates ≥ 0.85`,
   `compliance_coverage ≥ 1`, plus the profile's **value floor** (balanced and risk-averse: a plan must
   keep ≥ 60% of the best plan's expected value — risk aversion should not recommend doing almost
   nothing). Margins are reported.
2. *Criteria* on fixed 0–1 scales: probabilities and shares as they are; dollar and score criteria as
   value ÷ best; time to value as best ÷ value; downside protection = 1 − CVaR ÷ expected NPV.
3. *Score* = Σ weight × criterion; *utility* = E[NPV] − λ·CVaR (reported, tie-break).

| Profile | Weights | λ |
|---|---|---|
| risk_neutral | E[NPV] 0.6, E[value] 0.2, alignment 0.1, OKR 0.1 | 0 |
| balanced | E[NPV] 0.3, downside 0.2, budget 0.15, schedule 0.15, alignment 0.1, OKR 0.1 | 0.35 |
| risk_averse | E[NPV] 0.15, downside 0.35, budget 0.2, schedule 0.2, resilience 0.1 | 0.7 |
| custom | from configuration | configurable |

Rule sets: `npv_budget80` (max E[NPV] s.t. P(within budget) ≥ 80%), `okr_dates85`, `mandatory`,
`downside_protected`. If no candidate meets every rule, the closest one is shown with a note.

**Strategic choices.** Aggressive = highest E[NPV]; Balanced = the balanced-profile recommendation;
Conservative = lowest CVaR among plans keeping ≥ 70% of the aggressive plan's expected value;
Accelerated = earliest value under the same floor. Each also runs in adaptive mode.

**Risk-aware optimization** (`risk/stochastic.py`) — a scenario-based stochastic MILP (sample average
approximation): first-stage portfolio decisions under every deterministic rule; S scenarios of benefit
realization, failures and cost factors; maximize `(1−λ)·mean(V_s) − λ·CVaR`, with CVaR linearized
(Rockafellar–Uryasev) and an optional budget chance constraint. Scenario capacity, slippage and
dependency delays are **not** in this model (they would multiply its size by S); they are captured when
the plan is evaluated by the schedule-aware Monte Carlo, out of sample, on the same futures as every
other candidate. **Monte Carlo evaluation alone does not produce a mathematically risk-optimal
portfolio**; the stochastic MILP is the risk-aware optimization, within the stated scope.

The recommended plan is re-checked independently (HiGHS, SCIP, CP-SAT on the fixed plan).

## 6. What-if scenarios and caching

Levers (`whatif.py`): total and per-LOB budget, ring-fenced and shared capacity, capability availability
by month (exact, month-level), start windows, mandatory additions/removals, objective weights, risk
profile, cost and schedule uncertainty spread, benefit realization shift. Sets: baseline, optimistic,
constrained, adverse (plus any in configuration). Each scenario has its own fingerprint, and its
optimization, frontier, Monte Carlo and recommendation are recomputed for it; the baseline
recommendation is re-evaluated under the scenario (still feasible? its risk there). Results are cached
by (stage, model fingerprint, uncertainty, configuration, code version), so unchanged inputs are reused
and any change forces recomputation.
