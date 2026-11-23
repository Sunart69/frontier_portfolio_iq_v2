# Test report

This is the full suite as executed when the repository was built. Nothing below is estimated.

**Result: 167 passed, 0 failed, 3 warnings, in 884.7 s (14 min 45 s).** (Latest run, after the Spider views sheet was added to the results workbook. Per-file times below are from the run before it, within a few seconds.)

Command: `python -m pytest -q --durations=25` (with `--junitxml` for the per-file table).

Environment: Linux container with 2 vCPUs and 7 GB RAM; Python 3.13.18; HiGHS 1.16.0 (highspy),
PySCIPOpt 6.3.0, OR-Tools 9.15, PuLP 2.9.0 (CBC), NumPy 2.5.3, SciPy 1.18.1, OSQP 1.1.3.
CI (`.github/workflows/tests.yml`) uses Python 3.11 on GitHub's hosted runners, where timings differ.

## By file

Time is per-test call time as recorded by pytest; shared session fixtures (the sample portfolio and the
full decision-intelligence bundle) are charged to the first test that uses them.

| File | Tests | Seconds | What it covers |
|---|---:|---:|---|
| `test_capacity_rules.py` | 22 | 0 | v1: ring-fencing, shared capacity, transfers, budgets, benefit attribution |
| `test_integration.py` | 18 | 0 | v1: tiers, 12 scenarios, workbook and dashboard on the sample |
| `test_intake.py` | 12 | 1 | Excel intake, validation, optional v2 sheets, round trip and fingerprint |
| `test_deck.py` | 11 | 116 | v1 executive deck |
| `test_cadence.py` | 20 | 124 | Cadence agent: schedule, issues, reminders, run (now with v2 outputs), LT pre-read |
| `test_decision.py` | 14 | 25 | LT decision record, `alternative:` adoption, preserve mode, memo and register |
| `test_solvers.py` | 13 | 51 | HiGHS/SCIP agreement, CBC, CP-SAT reformulation, OSQP, validator, confidence report, timeouts |
| `test_pareto.py` | 11 | 139 | Objective linearizations, epsilon-constraint, weighted sum, NSGA-II labelling, dominance, scope |
| `test_montecarlo.py` | 23 | 8 | Distributions, correlation repair, CRN, reproducibility, fixed vs adaptive, VaR/CVaR, convergence |
| `test_decision_intelligence.py` | 16 | 326 | End to end: intake → validated optimization → frontier → MC → recommendation → what-if → dashboard and CEO deck, including the radar views reconciled between dashboard, deck and bundle |
| `test_performance.py` | 7 | 83 | Synthetic generator at 25–500 × 12–36, small end of the grid solved and simulated, benchmark report coverage |
| **Total** | **167** | | |

## Slowest steps (previous run; within a few seconds of the latest)

| Seconds | Step |
|---:|---|
| 169.3 | setup of the full decision-intelligence bundle (shared by `test_decision_intelligence.py`) |
| 119.1 | `test_cadence.py::test_run_cycle_produces_lt_preread` (v1 + v2 pre-read, quick mode) |
| 111.6 | `test_decision_intelligence.py::test_cli_decide_end_to_end` |
| 114.6 | setup of the v1 analysis (shared by `test_deck.py`) |
| 89.6 | setup of the Pareto frontier fixture |
| 78.1 | `test_performance.py::test_small_end_of_the_grid_solves_validates_and_simulates[25-24]` |

## Warnings

- Two `DeprecationWarning`s from Python's `multiprocessing` about `fork()` in a multi-threaded process,
  raised by the parallel Monte Carlo test. Results are checked to be identical to the serial run.
- One `PendingDeprecationWarning` from OSQP about a future default of `raise_error`.

## Fixes made during the first v2 run

An earlier full run (same code, with another job running alongside it) had 3 failures, all in test
assumptions rather than in the package:

- `test_decision.py::test_preserve_mode_keeps_the_risk_adjusted_recommendation` built a "smaller plan"
  by dropping the last three initiatives, which could drop a committed one. It now drops only
  initiatives that are not mandatory, compliance, critical, deadline-bound or a dependency.
- `test_performance.py::test_generated_portfolios_are_valid_intakes[25-12]` and `[100-12]` required a
  dependency in every generated portfolio. At 12 months the generator adds only chains that fit the
  horizon, and none did. The test now checks that dependencies are acyclic and resolvable everywhere and
  present from 24 months up. The generator was not changed, so `docs/PERFORMANCE.md` still matches it.

## Faster subsets

- `pytest -m "not integration" -q` skips the full-pipeline files (`test_decision_intelligence.py`,
  `test_integration.py`, `test_deck.py` and the cadence integration test).
- `pytest tests/test_solvers.py tests/test_montecarlo.py -q` runs the engine tests on their own.
