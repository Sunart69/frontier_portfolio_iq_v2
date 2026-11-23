# Performance (measured)

Measured on Linux-6.18.44-fc-v726-x86_64-with-glibc2.39 (2 CPUs), Python 3.13.18, 2026-11-22. Each case runs in its own process; time limit per MILP solve 60.0 s, relative gap target 1%. Monte Carlo: 2,000 iterations, fixed-plan mode, one worker. Synthetic portfolios from `frontier_portfolio_iq.benchmark.generate_portfolio` (seed 7). These are the numbers this machine produced; your hardware will differ.

| Initiatives | Months | Variables (integer) | Constraints | Build s | HiGHS status | HiGHS gap | HiGHS s | Validated | SCIP status | SCIP gap | SCIP s | CP-SAT | MC s | MC iter/s | Peak RSS MB |
|---:|---:|---:|---:|---:|---|---:|---:|---|---|---:|---:|---|---:|---:|---:|
| 25 | 12 | 1,089 (111) | 893 | 0.04 | optimal (within_gap) | 0.16% | 0.48 | yes | optimal | 0.87% | 1.98 | optimal (0.97 s) | 0.33 | 6082.7 | 245.2 |
| 25 | 24 | 2,012 (296) | 1,597 | 0.09 | optimal (within_gap) | 1.00% | 18.04 | yes | feasible | 1.17% | 60.0 | optimal (41.75 s) | 0.39 | 5269.1 | 450.5 |
| 25 | 36 | 2,936 (482) | 2,302 | 0.16 | feasible (feasible) | 2.22% | 60.02 | yes | feasible | 3.74% | 60.0 | feasible (60.01 s) | 0.38 | 5366.9 | 566.2 |
| 100 | 12 | 4,014 (409) | 2,502 | 0.13 | feasible (feasible) | 1.47% | 60.02 | yes | feasible | 1.40% | 60.0 | feasible (60.0 s) | 0.88 | 2295.8 | 521.4 |
| 100 | 24 | 7,347 (1,100) | 4,340 | 0.35 | feasible (feasible) | 2.97% | 60.03 | yes | feasible | 4.51% | 60.0 | feasible (60.02 s) | 1.19 | 1699.1 | 825.7 |
| 100 | 36 | 10,735 (1,800) | 6,212 | 0.57 | feasible (feasible) | 2.01% | 60.04 | yes | feasible | 2.38% | 60.01 | feasible (60.02 s) | 1.26 | 1596.7 | 1064.4 |
| 250 | 12 | 9,407 (1,000) | 5,451 | 0.33 | optimal (within_gap) | 1.00% | 13.21 | yes | optimal | 0.96% | 17.31 | feasible (60.01 s) | 2.33 | 866.1 | 633.1 |
| 250 | 24 | 16,936 (2,624) | 9,255 | 0.9 | optimal (within_gap) | 1.00% | 38.23 | yes | optimal | 0.93% | 21.3 | feasible (60.04 s) | 2.77 | 726.6 | 1049.4 |
| 250 | 36 | 24,674 (4,301) | 13,177 | 1.52 | optimal (within_gap) | 0.97% | 26.86 | yes | optimal | 0.72% | 28.82 | feasible (60.06 s) | 2.75 | 732.5 | 1573.6 |
| 500 | 12 | 19,029 (1,991) | 10,656 | 0.78 | optimal (within_gap) | 0.56% | 8.19 | yes | optimal | 0.73% | 13.4 | feasible (60.02 s) | 3.82 | 527.8 | 959.2 |
| 500 | 24 | 34,194 (5,247) | 17,905 | 1.65 | optimal (within_gap) | 0.69% | 34.48 | yes | optimal | 0.66% | 58.45 | feasible (60.14 s) | 4.93 | 409.0 | 1874.6 |
| 500 | 36 | 49,386 (8,541) | 25,246 | 3.17 | optimal (within_gap) | 0.07% | 49.4 | yes | optimal | 0.54% | 37.74 | feasible (60.15 s) | 5.43 | 370.7 | 2518.5 |

Notes
- *optimal* means the solver proved the plan within the 1% gap; *feasible* means it stopped at the time limit with a plan and the gap shown. *Validated* = the HiGHS plan passed the independent feasibility validator.
- CP-SAT solves an integer reformulation (hours rounded conservatively); its gap refers to that scaled model. Above its integer-variable limit (60,000 by default) it reports *incompatible* and HiGHS and SCIP still cross-check the case. No case in this grid reached that limit.
- Harder is not always bigger: the tightest budget/capacity mixes (e.g. 100 initiatives) can be harder to prove than larger, looser ones. Raise `time_limit` for those, or accept the reported gap.
- Monte Carlo throughput is for the fixed-plan simulation on one worker; it scales with `workers` (results are identical for any worker count).
- Peak RSS includes the Python interpreter and all solver libraries (HiGHS, SCIP, CP-SAT).
