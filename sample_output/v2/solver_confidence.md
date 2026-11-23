### Solver confidence: Committed baseline

**Confirmed Optimal** — HiGHS proved a gap of 0.93%; independently confirmed by SCIP, CP-SAT

Model baseline@f26e35b433e5f23d · 1,300 variables (143 integer) · 853 constraints

| Engine | Role | Status | Proof | Objective | Bound | Gap | Validated | Time (s) |
|---|---|---|---|---:|---:|---:|---|---:|
| HiGHS | primary | optimal | within_gap | 27,765.45 | 28,024.06 | 0.931% | yes | 2.90 |
| SCIP | validation | optimal | within_gap | 27,765.45 | 27,990.84 | 0.812% | yes | 5.91 |
| CP-SAT | validation | optimal | optimal | 27,765.45 | 27,768.75 | 0.000% | yes | 18.02 |

| Comparison | Objective difference | Allowed | Consistent |
|---|---:|---:|---|
| HiGHS vs SCIP | -0.00 | 277.68 | yes |
| HiGHS vs CP-SAT | 0.00 | 277.68 | yes |

### Solver confidence: Recommended ALT-D71AA2BD

**Confirmed Optimal** — HiGHS proved optimality; independently confirmed by SCIP, CP-SAT

Model baseline@f26e35b433e5f23d · 1,300 variables (143 integer) · 996 constraints

| Engine | Role | Status | Proof | Objective | Bound | Gap | Validated | Time (s) |
|---|---|---|---|---:|---:|---:|---|---:|
| HiGHS | primary | optimal | optimal | 27,765.45 | 27,765.45 | 0.000% | yes | 0.01 |
| SCIP | validation | optimal | optimal | 27,765.45 | 27,765.45 | 0.000% | yes | 0.01 |
| CP-SAT | validation | optimal | optimal | 27,765.45 | 27,768.75 | 0.000% | yes | 0.05 |

| Comparison | Objective difference | Allowed | Consistent |
|---|---:|---:|---|
| HiGHS vs SCIP | -0.00 | 277.68 | yes |
| HiGHS vs CP-SAT | 0.00 | 277.68 | yes |
