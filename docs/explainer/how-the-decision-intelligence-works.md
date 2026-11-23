# How Frontier Portfolio IQ v2 reaches a recommendation — a guide for the leadership team

*Companion to [how the solver decides](how-the-solver-decides.md), which explains the core optimization.
This guide covers what v2 adds: several solvers that check each other, a menu of efficient choices
instead of one answer, a probability for every promise, and a recommendation that states its risk.*

## The short version

1. **The math is checked twice.** Three independent solvers work the same problem. A separate checker
   re-tests every plan against every business rule. If the solvers disagree, we are told why.
2. **You get choices, not one answer.** The engine maps the *efficient frontier*: every plan where
   gaining more of one thing (value, speed) means giving up some of another (risk, cost). Plans that
   are worse on everything are discarded.
3. **Every promise has odds.** Each shortlisted plan is played forward through thousands of possible
   futures — overruns, late starts, people leaving, vendor delays — month by month, respecting who owns
   which capacity and what depends on what.
4. **The recommendation shows its reasoning.** Plans are ranked by your stated risk appetite and rules
   (for example, "at least an 80% chance of staying within budget"). The deck shows four strategic
   choices side by side so you choose the trade-off knowingly.

## Three kinds of numbers

| Badge | What it is | How much to trust it |
|---|---|---|
| **PROVEN** | An optimization result. The solver proved no better plan exists (or how close it is, e.g. within 1%) | As much as the inputs it was given |
| **SIMULATED** | A probability or a range from the simulation, e.g. "82% chance within budget" | An estimate: it carries a ± sampling error and depends on the assumptions |
| **ESTIMATE** | An input assumption, e.g. "AI engineers are available 85–103% of plan" | Only as good as its source; uncalibrated defaults are flagged |

## Decision tree: how a plan earns a recommendation

```
Is the plan feasible under every rule (capacity, ring-fencing, budgets, dependencies, commitments)?
├── No  → discarded (and the checker says which rule it breaks)
└── Yes → Is it on the efficient frontier (no other plan better on every objective)?
          ├── No  → shown as "dominated", with the plan that beats it
          └── Yes → shortlisted → simulated across thousands of futures
                    └── Does it meet leadership's rules (e.g. ≥ 80% budget confidence, ≥ 60% of best value)?
                        ├── No  → not eligible (the deck shows by how much it misses)
                        └── Yes → scored on value, downside, budget and schedule confidence,
                                  alignment and OKRs, weighted by the chosen risk profile
                                  → highest score = recommended (re-checked by all three solvers)
```

## The four strategic choices

| Choice | Rule | When it fits |
|---|---|---|
| Aggressive | Highest expected NPV | Strong risk appetite, stable delivery environment |
| Balanced | Best score under the balanced risk profile (the default recommendation) | Most cycles |
| Conservative | Lowest downside, while keeping at least 70% of the aggressive plan's value | Uncertain year, tight budgets |
| Accelerated | Earliest value delivery, same 70% floor | Outcomes needed early in the half |

Two choices can coincide; the deck says so.

## What "risk" means here

* **P10 / P50 / P90**: in 10% of simulated futures NPV is below P10; half the futures are below P50.
* **CVaR (worst 5%)**: the average shortfall against plan in the worst 5% of futures — "how bad is bad".
* **Probability of staying within budget**: cost within the plan plus a 5% reserve.
* **Probability of meeting committed dates**: every deadline and every mandatory, compliance and critical
  initiative within one month of plan.

## What the engine does *not* claim

* Simulation does not make a plan optimal under risk. The stochastic optimization (a separate solver run
  that includes scenario risk) is the risk-aware plan; it competes in the same ranking.
* A probability is only as good as the assumptions behind it. Until the uncertainty inputs are
  calibrated from our own delivery history, they are demonstration defaults — and every output says so.
* Running more simulations reduces sampling noise; it does not fix a wrong assumption.

## What we ask of you

Choose the risk posture (one of the four choices, or the recommendation), grant or decline the capacity
approvals listed in the deck, and commission calibration of the uncertainty inputs if they are still
defaults. The decision record captures exactly that, and the approved plan is then published with its
own risk profile.
