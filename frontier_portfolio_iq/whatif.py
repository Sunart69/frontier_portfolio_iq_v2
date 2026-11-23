"""What-if levers and scenario sets for the decision-intelligence pipeline.

A `WhatIf` changes the portfolio model and/or the uncertainty model; the pipeline then recomputes the
validated optimization, the Pareto frontier, the Monte Carlo evaluations and the recommendation for the
changed model (cached by fingerprint, so unchanged scenarios are not recomputed).

Levers
  total_budget            multiply every budget line
  lob_budget              {LOB: multiplier} on that LOB's budget lines
  ring_fenced_capacity    multiply FTE of ring-fenced / conditionally transferable pools
  shared_capacity         multiply FTE of shared pools
  capability_month        [(capability, from_month, to_month, multiplier)] on the available hours of every
                          pool of that capability in those months (exact, month-level)
  start_windows           {initiative: (earliest, latest)} start window
  mandatory_add / remove  initiatives that must / need no longer be included
  objective_weights       leadership weights for a custom recommendation profile
  risk_profile            risk_neutral | balanced | risk_averse
  cost_uncertainty        widen (>1) or narrow (<1) effort/cost spread
  schedule_uncertainty    widen or narrow duration spread
  benefit_shift           shift benefit realization (e.g. -0.10)
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .domain import CapacityType, PortfolioData


@dataclass
class WhatIf:
    key: str
    name: str
    description: str = ""
    total_budget: float = 1.0
    lob_budget: Dict[str, float] = field(default_factory=dict)
    ring_fenced_capacity: float = 1.0
    shared_capacity: float = 1.0
    capability_month: List[Tuple[str, int, int, float]] = field(default_factory=list)
    start_windows: Dict[str, Tuple[int, int]] = field(default_factory=dict)
    mandatory_add: List[str] = field(default_factory=list)
    mandatory_remove: List[str] = field(default_factory=list)
    objective_weights: Dict[str, float] = field(default_factory=dict)
    risk_profile: Optional[str] = None
    cost_uncertainty: float = 1.0
    schedule_uncertainty: float = 1.0
    benefit_shift: float = 0.0
    kind: str = "custom"           # baseline | optimistic | constrained | adverse | custom

    def changes_model(self) -> bool:
        return any([self.total_budget != 1.0, self.lob_budget, self.ring_fenced_capacity != 1.0,
                    self.shared_capacity != 1.0, self.capability_month, self.start_windows,
                    self.mandatory_add, self.mandatory_remove])

    def levers(self) -> Dict[str, object]:
        base = WhatIf("", "")
        return {f.name: getattr(self, f.name) for f in dataclasses.fields(self)
                if f.name not in ("key", "name", "description", "kind") and getattr(self, f.name) != getattr(base, f.name)}

    def apply(self, data: PortfolioData, um=None):
        d = data.copy()
        for b in d.budgets:
            b.amount *= self.total_budget * self.lob_budget.get(b.lob_id, 1.0)
        for p in list(d.pools.values()):
            if p.capacity_type == CapacityType.SHARED:
                p.fte *= self.shared_capacity
            else:
                p.fte *= self.ring_fenced_capacity
        for cap, lo, hi, mult in self.capability_month:
            for p in d.pools.values():
                if p.capability == cap:
                    for t in range(max(1, lo), hi + 1):
                        p.month_factor[t] = p.month_factor.get(t, 1.0) * mult
        for pid, (lo, hi) in self.start_windows.items():
            if pid in d.projects:
                p = d.projects[pid]
                p.earliest_start = lo
                p.deadline = min(d.settings.horizon, hi + p.duration - 1)
        for pid in self.mandatory_add:
            if pid in d.projects:
                d.projects[pid].mandatory = True
        for pid in self.mandatory_remove:
            if pid in d.projects:
                d.projects[pid].mandatory = False
                d.projects[pid].critical = False
        u = um.scaled(self.cost_uncertainty, self.schedule_uncertainty, self.benefit_shift) if um is not None else None
        return d, u


def scenario_set(data: PortfolioData) -> List[WhatIf]:
    """Baseline, optimistic, constrained and adverse cases (documented demonstration levers)."""
    return [
        WhatIf("baseline", "Baseline", "Intake as submitted", kind="baseline"),
        WhatIf("optimistic", "Optimistic", "10% more capacity, tighter cost and schedule spread, +5% benefits",
               ring_fenced_capacity=1.1, shared_capacity=1.1, cost_uncertainty=0.7, schedule_uncertainty=0.7,
               benefit_shift=0.05, kind="optimistic"),
        WhatIf("constrained", "Constrained", "Budgets cut 10% and shared capacity cut 10%",
               total_budget=0.9, shared_capacity=0.9, kind="constrained"),
        WhatIf("adverse", "Adverse", "Capacity down 10-15%, 50% wider cost and schedule spread, -10% benefits",
               ring_fenced_capacity=0.9, shared_capacity=0.85, cost_uncertainty=1.5, schedule_uncertainty=1.5,
               benefit_shift=-0.10, risk_profile="risk_averse", kind="adverse"),
    ]
