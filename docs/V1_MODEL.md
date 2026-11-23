# The v1 core: multi-LOB optimization with ring-fenced capacity

This is the model every v2 stage builds on. v2 adds engines, objectives and risk around it without changing its rules.

## Module map

| Module | Responsibility (spec section) |
|---|---|
| `domain.py` | LOB, Project, CapacityPool, EligibilityRule, TransferRule, BudgetLine, StrategicTarget, BenefitAllocation, Settings (§1, §2) |
| `intake.py` | The 7 new worksheets plus project sheets. Month names ("April") are accepted. Validates every LOB, pool, project and funding relationship (§9) |
| `optimizer.py` | Time-indexed MILP and the three tiers (§3, §5, §10, §14) |
| `scenarios.py` | The 12 what-if scenarios, release/transfer/contractor transforms, per-pool release valuation (§8) |
| `lob.py` | LOB-perspective optimization and reconciliation against the enterprise plan (§5) |
| `capacity.py` | Idle ring-fenced hours, unused shared hours, restricted-unavailable hours, shortages by LOB/capability/month, consumed vs committed-not-consumed (§4) |
| `metrics.py` | Enterprise and LOB outcomes with benefit split counted once, investment, utilization, compliance, risk, maturity, scenario deltas, opportunity cost |
| `analysis.py` | Orchestration (AnalysisBundle), project classification, decisions, results workbook, shared figure builders |
| `dashboard.py`, `deck.py` | Dashboards (§11) and the PowerPoint (§12) |

## Mathematical model (optimizer.py)

**Variables**

- `x[i,s]`: binary, 1 if project i starts in month s.
- `y[ch,t]`: hours drawn in month t through an allocation channel. A channel is (pool p, project i, demanded capability c, route), so it is the spec's `y[p,i,t]` split by capability and route.
- Routes are:
  - *direct*: E[p,i]=1 through ownership, eligibility, owner authorization or pool funding restrictions
  - *transfer*: a TransferRule, including synthetic release rules
  - *relaxed*: unconstrained tier only
- `z[p,l,t]` is the sum of transfer-route `y` for LOB l.
- `A[p,t]` is 0 before the start month and after the end month. Otherwise it is FTE × productive hours × utilization.

**Constraints**

- **Pool capacity**: Σ y over all routes ≤ A[p,t]. Every hour, whether direct, transferred, released or substituted, comes out of exactly one source pool, so nothing is allocated twice.
- **Ring-fence eligibility**: no direct channel exists to an ineligible LOB. Ring-fenced and conditional pools serve their owner, plus projects or LOBs the owner has authorized.
- **Transfers**: Σ z ≤ min(max hours, max % × A, A − minimum retained), and only within the effective dates. Expiry enforces return to the owner. Efficiency scales effective hours, and transfer cost is charged to the receiving LOB's budget.
- **Substitution**: effective hours = hours × substitution efficiency × transfer efficiency.
- **Demand**: Σ effective hours = the hours the chosen schedule requires, for every capability and month.
- **LOB funding**: spend is computed per (LOB, funding source) using contribution shares. A carry-forward budget is checked cumulatively; a month-locked budget is checked monthly. A funding source restricted to certain project types makes other types unfundable.
- **Approval enforcement**:
  - Committed: only approved or pre-authorized rules.
  - Conditional: adds pending rules.
  - Unconstrained: relaxes ring-fencing and is labeled analytical.
  - Anything that uses an unapproved rule is reported as Conditional, never Committed.
- **Dependencies, deadlines and mandatory/critical/compliance protection** are enforced. Governance constraints are all opt-in: minimum outcome per LOB/KPI, minimum/maximum investment, concentration cap, minimum investment share, minimum shared-capacity share, enterprise reservation of shared pools, LOB risk tolerance, and maturity targets.

**Objective**: time-adjusted enterprise value net of transfer and incremental resource costs. Balanced and coverage objectives are available.

**Infeasibility**: when the model is infeasible, it is re-solved with commitments made soft. The diagnostics then name the commitments that cannot be met.

**Solver accuracy**: two safeguards stop scenario comparisons from being distorted by the MIP gap. A scenario that relaxes the committed problem carries the committed objective as a valid lower bound. Every scenario also tests the committed plan as an incumbent. As a result, a relaxation never reports less value than the committed plan, and a restriction never reports more.

## Portfolio tiers and project classification

| Tier | Meaning |
|---|---|
| **Committed** | Executable with the budgets and capacity authorized today |
| **Conditional** | Assumes pending transfers are approved and 20% of release-eligible ring-fenced pools are released |
| **Unconstrained Opportunity** | Analytical only: ring-fencing is relaxed |

Each project is classified as follows:

| Class | Meaning |
|---|---|
| Approved | In the Committed tier |
| Conditional | In the Conditional tier only |
| Deferred | Only in the Unconstrained tier |
| Rejected | In no tier |

## Integration seams

The package exposes stable seams:

- **Intake**: `read_workbook()` returns a `PortfolioData`. Add the 7 sheets to your existing intake template, or map your current project/resource tables into the dataclasses.
- **Solver**: `solve(data, SolveOptions(...))` and `run_analysis(data)` are pure functions of `PortfolioData`. Call them from your scenario workbench or API layer.
- **UI**: `AnalysisBundle` is the single source for dashboards and decks. If your UI is a web app, serialize `analysis_summary.json` or the bundle metrics rather than re-computing.

## Sample data

The sample is synthetic. It has three business LOBs (Retail, Commercial, Wealth) and Enterprise IT, with 19 initiatives (5 cross-LOB platforms) and 11 capacity pools:

- **Ring-fenced**: Integration Engineering for LOB A from April (5 FTE) and Software Engineering for LOB B (6 FTE).
- **Shared**: Solution Architecture and others.
- **Conditionally transferable**: AI Engineering for LOB C from June.
- **Temporary**: a contractor cohort (March–August).

Transfer rules cover the approved, pending and rejected statuses.
