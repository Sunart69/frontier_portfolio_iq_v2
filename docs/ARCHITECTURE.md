# Architecture

```
            Excel intake (v1 sheets + optional v2 uncertainty sheets)          config/decision.yaml
                                      │                                               │
                                      ▼                                               │
   ┌──────────────── canonical.py: one versioned model (fingerprint + scenario) ──────┤
   │                                  │                                               │
   │   1 Portfolio model    optimizer.PortfolioModel (v1 rules: capacity, ring-fencing, transfers,
   │                        budgets, dependencies, commitments, governance)
   │                                  │ compiled once → MilpMatrix
   │   2 Multi-solver       solvers/: HiGHS (primary) · SCIP (same matrix) · CP-SAT (integer
   │                        reformulation) · CBC · OSQP · IPOPT; fallback; normalized results
   │   3 Validation         solvers/validate.py (independent rule checks) → Solver Confidence Report
   │                                  │
   │   4 Pareto frontier    pareto/: objectives · payoff table · epsilon-constraint + refinement ·
   │                        weighted sums · NSGA-II (optional) · dominance, ranks, duplicates
   │                        + risk/stochastic.py: scenario-based CVaR MILP (risk-aware plan)
   │                                  │ shortlist
   │   5 Monte Carlo        risk/: distributions · copula drivers · schedule/resource/dependency-aware
   │                        simulation (fixed / adaptive) · common random numbers · metrics, convergence
   │                                  │
   │   6 Recommendation     decision/recommend.py: profiles, decision rules, value floor, ranking,
   │                        four strategic choices; recommended plan re-certified by all engines
   │                                  │
   │   7 What-if            whatif.py levers → new fingerprint → steps 1–6 recomputed (cache.py)
   └──────────────────────────────────┤
                                      ▼
                        intelligence.DecisionBundle (single source)
               ┌──────────────────────┼─────────────────────────────┐
               ▼                      ▼                             ▼
   dashboard_v2.py            deck_v2.py (12 slides +      decision/config.py: decision_results.xlsx
   decision_dashboard.html    7 appendices) + manifest     decision_intelligence.json, solver_confidence.md
          ▲                       ▲
          └─────── radar.py ──────┘   spider-chart profiles shared by dashboard and deck
```

## Package map

| Module | Responsibility |
|---|---|
| `domain.py`, `intake.py`, `sample_data.py` | v1 domain model and Excel intake; v2 adds `discount_rate`, project `attributes`/`tags`, pool `month_factor`, optional sheets |
| `optimizer.py`, `scenarios.py`, `lob.py`, `capacity.py`, `metrics.py`, `analysis.py` | v1 MILP, tiers, 12 scenarios, LOB reconciliation, capacity report ([V1_MODEL.md](V1_MODEL.md)) |
| `canonical.py` | canonical JSON, fingerprints, `ModelVersion` |
| `solvers/` | `base.py` (interface, `MilpMatrix`), `milp.py` (HiGHS, SCIP, CBC), `cpsat.py`, `qp.py` (OSQP, IPOPT), `compiled.py`, `validate.py`, `orchestrate.py` |
| `pareto/` | `objectives.py`, `engine.py` (payoff, epsilon, weighted), `nsga.py`, `frontier.py` (alternatives, dominance) |
| `risk/` | `distributions.py`, `correlation.py`, `uncertainty.py` (model, defaults, sheets), `simulate.py`, `metrics.py`, `stochastic.py` |
| `decision/` | `recommend.py` (profiles, rules, ranking, choices), `config.py` (YAML loader, results workbook) |
| `whatif.py`, `cache.py` | scenario levers and sets; fingerprint-keyed result cache |
| `intelligence.py` | the end-to-end pipeline and `DecisionBundle` |
| `dashboard_v2.py`, `deck_v2.py` | decision dashboard and CEO deck (v1 `dashboard.py` / `deck.py` kept) |
| `radar.py` | radar (spider) profiles: risk trade-off, optimization KPIs, risk appetite, what-if; shared by dashboard and deck |
| `benchmark.py` | synthetic generator and the measured performance grid |
| `cadence/` | semi-annual cycle: schedule, owners, follow-ups, GitHub issues, orchestration, LT decision and publishing — now running the v2 pipeline for the pre-read |

## Data flow in the semi-annual cycle

1. Kickoff → templates (now including the optional uncertainty sheets) → owner issues and reminders.
2. Optimization run: v1 analysis (tiers, capacity, LOB view) **and** the v2 pipeline; outputs in
   `cycles/<cycle>/outputs/` (v1 files plus `decision_dashboard.html`, `ceo_decision_deck_<cycle>.pptx`,
   `decision_results.xlsx`, `decision_intelligence.json`, `solver_confidence.md`).
3. Decision record pre-filled with the risk-adjusted recommendation and the evaluated alternatives.
4. After the LT meeting: `alternative:` (adopt the conservative / accelerated / … plan) plus explicit
   approvals, includes, excludes and start-by dates. Publishing *preserves* the chosen plan and applies
   only LT's changes, re-validates it, simulates its risk profile, and writes the memo, register and
   summary.
