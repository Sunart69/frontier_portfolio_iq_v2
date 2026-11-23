# Frontier Portfolio IQ v2 — enterprise portfolio decision intelligence

v2 turns the Frontier Portfolio IQ optimizer into a decision-intelligence platform. It keeps every v1
capability — multi-LOB optimization with ring-fenced capability capacity, transfer approvals, LOB
budgets, cross-LOB benefits counted once, the three portfolio tiers, the 12 scenarios, the Excel intake,
the LOB dashboard and the CEO deck, and the semi-annual cycle agent with its LT decision memo — and adds:

1. **Multi-solver optimization with independent validation**: HiGHS (primary), SCIP on the identical
   formulation, OR-Tools CP-SAT on an integer reformulation, optional CBC / OSQP / IPOPT; fallback;
   a rule-by-rule feasibility validator; a Solver Confidence Report that separates *proven* optimality
   from *feasible-but-unproven* plans and turns disagreements into diagnostics, not rejections.
2. **Pareto frontier analysis**: eleven configurable objectives plus administrator-defined ones;
   epsilon-constraint with adaptive refinement (primary), normalized weighted sums, optional NSGA-II
   (approximate, feasibility-certified); tolerance-aware dominance, ranks and duplicate handling.
3. **Monte Carlo risk evaluation**: cost, duration, benefit, start-slip, failure, availability, hiring
   and productivity uncertainty; correlated shared risk drivers (Gaussian copula); a schedule-,
   resource- and dependency-aware simulation in fixed-plan and adaptive modes; common random numbers;
   P10/P50/P90, VaR, CVaR, budget, schedule, OKR and shortage probabilities with convergence diagnostics.
4. **Risk-adjusted recommendation**: risk-neutral, balanced, risk-averse and custom profiles, leadership
   decision rules (e.g. max E[NPV] s.t. P(within budget) ≥ 80%), four strategic choices, and a
   scenario-based stochastic CVaR MILP for risk-aware optimization.
5. **What-if analysis** across optimization, frontier and risk, cached by model fingerprint.
6. **A decision dashboard and a 12-slide CEO decision deck** (plus technical appendices), built from the
   live run, with PROVEN / SIMULATED / ESTIMATE labels throughout.

All stages work on one canonical, versioned portfolio model.

## Quick start

```bash
pip install -e ".[test]"                                         # all engines + test tools
python -m frontier_portfolio_iq sample --out intake.xlsx          # sample intake (incl. uncertainty sheets)
python -m frontier_portfolio_iq decide --input intake.xlsx --out out/
pytest -q                                                         # full suite (see docs/TEST_REPORT.md)
```

`decide` options: `--config config/decision.yaml`, `--profile balanced|risk_neutral|risk_averse`,
`--rules npv_budget80 okr_dates85 mandatory downside_protected`, `--iterations 10000`,
`--mode fixed|adaptive`, `--workers 4`, `--nsga`, `--no-whatif`, `--quick`, `--no-v1`.

The answers leadership gets:

| Question | Where |
|---|---|
| Which projects should we invest in, and when should they start? | Recommended portfolio (deck slide 10, dashboard *Execution*) |
| Which capabilities and LOBs are constrained? | Slides 3, 8, 9; dashboard *Compare* (bottlenecks) and *Execution* (utilization) |
| What outcomes can we realistically achieve? | Slides 2, 7; dashboard *Risk* |
| What are the best alternative portfolios, and what do we give up? | Slides 4–6; dashboard *Trade-off frontier* and *Compare* |
| How likely are we to achieve the promised outcomes? | Slide 7; dashboard *Risk* (probabilities with sampling error) |
| Which portfolio balances value, risk, cost and confidence best? | Slides 5, 10; dashboard *Portfolio choices* |
| How sure are we of the math? | Appendix A1; dashboard *Decision assurance* |

## Sample output

`sample_output/v2/` holds a full `decide` run on the synthetic sample: the decision dashboard, the CEO
deck, the results workbook, the JSON bundle and the solver confidence reports. `sample_output/` keeps the
v1 outputs and an example approved-portfolio memo.

## Documentation

| Document | Contents |
|---|---|
| [docs/METHODS.md](docs/METHODS.md) | Solvers, validation, objectives, Pareto methods, Monte Carlo, recommendation, stochastic MILP, what-if — with definitions and limitations |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Pipeline, package map, cycle data flow |
| [docs/ASSUMPTIONS.md](docs/ASSUMPTIONS.md) | Every default uncertainty assumption and how to calibrate it |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Install, run, outputs, engines, CI, cycle, agent |
| [docs/DEPENDENCIES.md](docs/DEPENDENCIES.md) | Dependency inventory with versions and licenses |
| [docs/PERFORMANCE.md](docs/PERFORMANCE.md) | **Measured** runtime, memory, gaps and throughput, 25–500 initiatives × 12–36 months |
| [docs/TEST_REPORT.md](docs/TEST_REPORT.md) | The latest full test run, as executed |
| [docs/V1_MODEL.md](docs/V1_MODEL.md) | The v1 MILP, tiers and classification that v2 builds on |
| [docs/CADENCE.md](docs/CADENCE.md) | The semi-annual cycle: setup, follow-ups, LT decision, publishing |
| [docs/explainer/how-the-decision-intelligence-works.md](docs/explainer/how-the-decision-intelligence-works.md) | Plain-language explainer for the leadership team |
| [docs/COPILOT_SETUP_PROMPT.md](docs/COPILOT_SETUP_PROMPT.md) | A phased prompt for Copilot agent mode to set this up for your organization |

## Semi-annual cadence and the agent

Unchanged in how it works ([CADENCE.md](docs/CADENCE.md)): GitHub Actions is the clock, the Copilot
agent is the operator, and every cycle's files live in `cycles/<cycle>/`. What v2 changes:

- The optimization run produces the **v2 pre-read** next to the v1 outputs: decision dashboard, CEO
  decision deck, results workbook, JSON bundle, solver confidence report.
- The LT decision record lists the **evaluated alternatives**; LT can adopt one (`alternative:
  conservative`) as well as grant approvals and add, drop or pull forward initiatives.
- Publishing **preserves** the chosen risk-adjusted plan, applies only LT's explicit changes, re-validates
  it and adds a **simulated risk profile** to the approved-portfolio memo.

```bash
python -m frontier_portfolio_iq cycle schedule
python -m frontier_portfolio_iq cycle status
python -m frontier_portfolio_iq cycle run --cycle 2027-H2
python -m frontier_portfolio_iq cycle publish --cycle 2027-H2
python -m frontier_portfolio_iq cycle tick --dry-run
```

## Package map

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). In short: `solvers/` (engines, validation,
orchestration), `pareto/` (objectives, epsilon/weighted, NSGA-II, dominance), `risk/` (distributions,
correlation, uncertainty, simulation, metrics, stochastic MILP), `decision/` (profiles, rules, ranking,
configuration), `whatif.py`, `cache.py`, `canonical.py`, `intelligence.py` (pipeline),
`dashboard_v2.py`, `deck_v2.py`, `benchmark.py`; v1 modules unchanged in behaviour.
