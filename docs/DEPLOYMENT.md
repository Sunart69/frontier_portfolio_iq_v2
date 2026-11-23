# Deployment

## 1. Install

Python 3.10 or newer (CI uses 3.11). All solver engines install from PyPI wheels; no system packages
are needed on Linux, macOS or Windows.

```bash
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[test]"        # core + HiGHS + SCIP + CP-SAT + pymoo (NSGA-II) + OSQP + pytest
# production without tests:     pip install -e ".[all]"
python -c "import highspy, pyscipopt, ortools, pulp; print('solvers ok')"
python -m frontier_portfolio_iq decide --quick --no-whatif --out out_smoke   # 2-3 minute smoke run
```

Engines and what happens if one is missing:

| Engine | Package | Required | If missing |
|---|---|---|---|
| HiGHS | highspy | yes | the orchestrator falls back to SCIP, then CBC, and records it |
| SCIP | pyscipopt | yes | validation runs with the engines available; level drops to PROVEN_SINGLE |
| CP-SAT | ortools | yes | as above |
| CBC | bundled with PuLP | optional | not used |
| NSGA-II | pymoo | optional | `--nsga` is ignored |
| OSQP | osqp | optional | leveling not offered |
| IPOPT | cyipopt | optional | not needed by the linear model |

## 2. Run

```bash
python -m frontier_portfolio_iq sample --out intake.xlsx      # sample intake incl. uncertainty sheets
python -m frontier_portfolio_iq validate --input intake.xlsx
python -m frontier_portfolio_iq decide --input intake.xlsx --out out/ \
    --config config/decision.yaml --profile balanced --rules npv_budget80 --workers 4
python -m frontier_portfolio_iq run --input intake.xlsx --out out_v1/    # v1 analysis only
```

`decide` writes:

| File | Contents |
|---|---|
| `decision_dashboard.html` | Executive decision dashboard: choices, frontier (2-D, 3-D, parallel coordinates, table), compare (radar, increments, LOB, bottlenecks), risk, execution (timeline, utilization), what-if, decision assurance. Self-contained, light/dark |
| `ceo_decision_deck.pptx` | 12-slide CEO decision story + 7 technical appendices (slide 6 carries the KPI and risk spiders; A5–A7 the ranking-criteria, risk-detail and risk-appetite spiders), native charts, built from the run |
| `ceo_deck_manifest.json` | Every figure placed on each slide, for audit |
| `decision_results.xlsx` | Alternatives, Monte Carlo, rankings, strategic choices, what-if, solver confidence, assumptions |
| `decision_intelligence.json` | The whole bundle, machine-readable (schema `fpiq-decision-intelligence/2.0`) |
| `solver_confidence.md` | Solver Confidence Reports (baseline and recommended plan) |
| v1 files | `portfolio_results.xlsx`, `lob_dashboard.html`, `executive_deck.pptx` (unless `--no-v1`) |

Runtime on the 19-initiative sample with defaults (2 CPUs): about 4–6 minutes (Pareto ≈ 1.5 min,
what-ifs ≈ 2 min). `--quick` ≈ 2 minutes. Results are cached in `.fpiq_cache/` by model fingerprint;
re-runs with unchanged inputs take seconds. Larger portfolios: see [PERFORMANCE.md](PERFORMANCE.md) for
measured solve times; raise `pareto.time_limit` and lower `pareto.grid_points` for 250+ initiatives.

## 3. Semi-annual cycle (GitHub)

Everything in [CADENCE.md](CADENCE.md) applies. v2 adds to `cadence/cadence.yaml`:

```yaml
decision_intelligence:
  enabled: true
  config: config/decision.yaml
  iterations: 10000
  publish_iterations: 5000
```

The `portfolio-cycle` workflow installs the package with all engines; the LT pre-read pull request now
contains the decision dashboard and the CEO deck. GitHub-hosted runners have 2 CPUs and 7 GB RAM, enough
for portfolios up to several hundred initiatives (see the measured peak memory in PERFORMANCE.md).
Self-hosted runners need no extra setup beyond Python.

The **Decision intelligence run** workflow (`.github/workflows/decide.yml`, run manually) runs `decide` on
the sample or on any intake workbook in the repository, with a chosen risk profile, rule sets and
configuration (for what-ifs), and attaches the outputs as the artifact `decision-outputs`. It commits
nothing.

## 4. The agent

`.github/agents/portfolio-cycle.agent.md` works in VS Code agent mode (including the free Copilot plan),
the Copilot CLI, or the Copilot cloud agent (paid plans). Everything scheduled runs in GitHub Actions
without Copilot. [COPILOT_SETUP_PROMPT.md](COPILOT_SETUP_PROMPT.md) walks GitHub Copilot through setup.
For Microsoft 365, [COPILOT_COWORK_SETUP_PROMPT.md](COPILOT_COWORK_SETUP_PROMPT.md) sets up Copilot Cowork as
the people-facing side of the cycle (owner register, calendar, reminders, pre-read, decision record,
distribution) while the engine keeps running in GitHub Actions.

## 5. Tests and benchmark

```bash
pytest -q                                    # full suite; see TEST_REPORT.md for the latest results
python -m frontier_portfolio_iq.benchmark    # 25–500 initiatives × 12–36 months; rewrites PERFORMANCE.md
```
