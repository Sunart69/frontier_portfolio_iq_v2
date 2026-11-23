# Repository instructions for Copilot

Frontier Portfolio IQ v2 is a Python package for multi-LOB IT portfolio prioritization. A time-indexed
MILP (PuLP and HiGHS) runs on a semi-annual cadence driven by GitHub Actions. v2 adds decision
intelligence on top: multi-solver validation (`solvers/`), a Pareto frontier (`pareto/`), Monte Carlo
risk (`risk/`), a risk-adjusted recommendation (`decision/`, `intelligence.py`), a decision dashboard
(`dashboard_v2.py`) and a CEO deck (`deck_v2.py`). Methods are in `docs/METHODS.md`.

- Install with `pip install -e ".[test]"`. Run the tests with `pytest -q` (duration in
  `docs/TEST_REPORT.md`); use `-m "not integration"` for the faster set.
- The CLI is `python -m frontier_portfolio_iq`. `run` is the v1 analysis (tiers, 12 scenarios);
  `decide` is the v2 decision intelligence run; the `cycle …` commands handle the semester cadence.
- Do not rebuild existing capabilities; extend them. Any change to model semantics must bump
  `CACHE_SALT` in `cache.py`.
- The cadence configuration lives in `cadence/`. Each cycle's files live in `cycles/<cycle>/`.
- For cycle operations (status, follow-ups, kickoff, running the prioritization, the LT pre-read,
  recording the LT decision and publishing the approved portfolio),
  use the **Portfolio Cycle Orchestrator** agent in `.github/agents/portfolio-cycle.agent.md`.
- Keep committed, conditional and unconstrained results clearly labeled. Every published figure must
  trace to solver or simulation output. Label values as PROVEN (optimization), SIMULATED (Monte Carlo)
  or ESTIMATE (assumption); never call NSGA-II or weighted-sum output a complete or proven frontier.
