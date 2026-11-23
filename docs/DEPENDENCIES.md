# Dependency inventory

Versions are those installed when the tests and benchmark in this repository were run; the minimum
versions are in `pyproject.toml`. Licenses are as published by each project — confirm them for your own
distribution and legal review.

| Package | Tested version | Purpose | License |
|---|---|---|---|
| PuLP | 2.9.0 | MILP model building; bundles the CBC binary | BSD-style (PuLP); CBC: EPL-2.0 |
| highspy (HiGHS) | 1.16.0 | primary LP/MILP engine | MIT |
| PySCIPOpt (SCIP) | 6.3.0 | independent MILP validation | MIT (PySCIPOpt); SCIP: Apache-2.0 |
| ortools (CP-SAT) | 9.15.6755 | discrete scheduling reformulation | Apache-2.0 |
| numpy | 2.5.3 | vectorized Monte Carlo | BSD-3-Clause |
| scipy | 1.18.1 | sparse matrices, distributions | BSD-3-Clause |
| openpyxl | 3.1.5 | Excel intake and workbooks | MIT |
| python-pptx | 1.0.2 | PowerPoint decks | MIT |
| python-docx | 1.2.0 | approved-portfolio memo (Word) | MIT |
| PyYAML | 6.0.3 | cadence, owners, decision configuration | MIT |
| pymoo (optional) | 0.6.2 | NSGA-II | Apache-2.0 |
| osqp (optional) | 1.1.3 | convex QP leveling | Apache-2.0 |
| cyipopt (optional, not installed) | — | IPOPT interface | EPL-2.0 |
| pytest (test) | 9.1.1 | tests | MIT |

Runtime environment used for the measured results: Python 3.13.18 on Linux x86-64, 2 CPUs. CI runs
Python 3.11 on GitHub-hosted Ubuntu.

The dashboard and decks have no runtime dependencies: the dashboard is one self-contained HTML file
(no CDN, no network calls), and the decks use native PowerPoint charts.
