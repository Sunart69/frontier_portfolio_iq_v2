"""Portfolio alternatives, Pareto dominance and the frontier store.

Dominance (maximization form, after multiplying minimized objectives by -1): alternative A dominates B
when A is at least as good on every objective and strictly better on at least one. Comparisons use a
tolerance per objective — ``abs_tol + rel_tol * range_k`` — so solver noise never makes one plan
"strictly better" than an equivalent one.

A frontier is scoped to one model version (fingerprint + scenario) and one objective set. Adding an
alternative from another scope raises `IncompatibleScopeError`: plans optimized under different
assumptions are never placed on the same frontier.
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from ..canonical import ModelVersion


class IncompatibleScopeError(ValueError):
    pass


@dataclass
class Evidence:
    engine: str
    status: str
    proof: str
    gap: Optional[float]
    bound: Optional[float]
    validated: bool
    failures: List[str] = field(default_factory=list)
    runtime: float = 0.0
    certified: bool = True        # False for heuristic (NSGA-II) alternatives until certified

    def to_dict(self):
        return dict(self.__dict__)


@dataclass
class PortfolioAlternative:
    alt_id: str
    method: str                                   # payoff | epsilon | weighted | nsga2 | baseline | stochastic
    version: ModelVersion
    objective_keys: Tuple[str, ...]
    starts: Dict[str, int]
    objectives: Dict[str, float]
    evidence: Evidence
    provenance: Dict[str, object] = field(default_factory=dict)
    capability_allocations: Dict[str, Dict[int, float]] = field(default_factory=dict)   # pool -> month -> hours
    lob_allocations: Dict[str, Dict[str, float]] = field(default_factory=dict)          # lob -> investment/value
    budget_profile: Dict[str, Dict[int, float]] = field(default_factory=dict)           # lob|TOTAL -> month -> $K
    pareto_rank: Optional[int] = None
    status: str = "unranked"                      # efficient | dominated | duplicate | infeasible
    dominated_by: List[str] = field(default_factory=list)
    aliases: List[str] = field(default_factory=list)
    name: str = ""
    result: object = None                         # PortfolioResult (not serialized)

    @property
    def selected(self) -> List[str]:
        return sorted(self.starts)

    def to_dict(self) -> Dict[str, object]:
        return {"alt_id": self.alt_id, "name": self.name, "method": self.method,
                "model_version": self.version.label(), "objective_keys": list(self.objective_keys),
                "selected": self.selected, "starts": self.starts, "objectives": self.objectives,
                "evidence": self.evidence.to_dict(), "provenance": self.provenance,
                "capability_allocations": {p: {str(t): h for t, h in m.items()}
                                           for p, m in self.capability_allocations.items()},
                "lob_allocations": self.lob_allocations,
                "budget_profile": {k: {str(t): v for t, v in m.items()} for k, m in self.budget_profile.items()},
                "pareto_rank": self.pareto_rank, "status": self.status, "dominated_by": self.dominated_by,
                "aliases": self.aliases}


def plan_id(starts: Dict[str, int]) -> str:
    blob = json.dumps(sorted(starts.items()))
    return "ALT-" + hashlib.sha1(blob.encode()).hexdigest()[:8].upper()


def describe_result(res, data) -> Tuple[Dict, Dict, Dict]:
    """Monthly capability allocations, LOB allocations and budget profile of a solved plan."""
    from ..metrics import portfolio_metrics
    cap: Dict[str, Dict[int, float]] = defaultdict(lambda: defaultdict(float))
    for a in res.allocations:
        cap[a.pool_id][a.month] += a.hours
    m = portfolio_metrics(res)
    lob = {l: {"investment": m["investment_by_lob"].get(l, 0.0), "value": m["lob_value"].get(l, 0.0),
               "initiatives": len(m["selected_by_lob"].get(l, []))}
           for l in sorted(set(m["investment_by_lob"]) | set(m["lob_value"]))}
    budget = {l: dict(v) for l, v in m["investment_by_lob_month"].items()}
    budget["TOTAL"] = dict(m["investment_by_month"])
    return ({p: dict(v) for p, v in cap.items()}, lob, budget)


# --------------------------------------------------------------------------- dominance
def to_max(vec: Dict[str, float], signs: Dict[str, float]) -> Dict[str, float]:
    return {k: signs[k] * v for k, v in vec.items() if k in signs}


def dominates(a: Dict[str, float], b: Dict[str, float], tol: Dict[str, float]) -> bool:
    """a, b in maximization form. True if a dominates b beyond tolerance."""
    better = False
    for k, ta in tol.items():
        if a[k] < b[k] - ta:
            return False
        if a[k] > b[k] + ta:
            better = True
    return better


def tolerances(vectors: Sequence[Dict[str, float]], keys: Sequence[str], abs_tol: float = 1e-6,
               rel_tol: float = 1e-4) -> Dict[str, float]:
    out = {}
    for k in keys:
        vals = [v[k] for v in vectors] or [0.0]
        rng = max(vals) - min(vals)
        scale = max(rng, max(abs(x) for x in vals) * 1e-3, 1e-9)
        out[k] = abs_tol + rel_tol * scale
    return out


def nondominated_sort(vectors: List[Dict[str, float]], tol: Dict[str, float]) -> List[int]:
    """Fast non-dominated sorting (Deb et al.). Returns the Pareto rank (1 = efficient) of each vector."""
    n = len(vectors)
    S = [[] for _ in range(n)]
    cnt = [0] * n
    rank = [0] * n
    front = []
    for p in range(n):
        for q in range(n):
            if p == q:
                continue
            if dominates(vectors[p], vectors[q], tol):
                S[p].append(q)
            elif dominates(vectors[q], vectors[p], tol):
                cnt[p] += 1
        if cnt[p] == 0:
            rank[p] = 1
            front.append(p)
    r = 1
    while front:
        nxt = []
        for p in front:
            for q in S[p]:
                cnt[q] -= 1
                if cnt[q] == 0:
                    rank[q] = r + 1
                    nxt.append(q)
        r += 1
        front = nxt
    return rank


@dataclass
class Frontier:
    version: ModelVersion
    objective_keys: Tuple[str, ...]           # objectives that define dominance
    signs: Dict[str, float]                   # +1 max, -1 min
    alternatives: List[PortfolioAlternative] = field(default_factory=list)
    infeasible: List[Dict[str, object]] = field(default_factory=list)
    payoff: Dict[str, Dict[str, float]] = field(default_factory=dict)
    ideal: Dict[str, float] = field(default_factory=dict)
    nadir: Dict[str, float] = field(default_factory=dict)
    log: List[str] = field(default_factory=list)
    rel_tol: float = 1e-4

    def add(self, alt: PortfolioAlternative) -> PortfolioAlternative:
        if not alt.version.compatible(self.version):
            raise IncompatibleScopeError(f"{alt.alt_id} comes from {alt.version.label()}, frontier is "
                                         f"{self.version.label()}")
        if tuple(alt.objective_keys) != tuple(self.objective_keys):
            raise IncompatibleScopeError(f"{alt.alt_id} was optimized on objectives {alt.objective_keys}, "
                                         f"frontier uses {self.objective_keys}")
        same = next((a for a in self.alternatives if a.starts == alt.starts), None)
        if same is not None:                    # exact duplicate plan: merge provenance
            same.aliases.append(f"{alt.method}:{json.dumps(alt.provenance, sort_keys=True, default=str)[:120]}")
            if not same.evidence.certified and alt.evidence.certified:
                same.evidence = alt.evidence
            return same
        self.alternatives.append(alt)
        return alt

    def rank(self) -> None:
        """Pareto ranks over validated alternatives; near-duplicates (equal within tolerance on every
        objective) are kept once — the first certified one — and the others marked `duplicate`."""
        pool = [a for a in self.alternatives if a.evidence.validated]
        for a in self.alternatives:
            if not a.evidence.validated:
                a.status, a.pareto_rank = "infeasible", None
        vecs = [to_max(a.objectives, self.signs) for a in pool]
        tol = tolerances(vecs, self.objective_keys, rel_tol=self.rel_tol)
        keep, vkeep = [], []
        for a, v in sorted(zip(pool, vecs), key=lambda z: (not z[0].evidence.certified, z[0].alt_id)):
            twin = next((k for k, w in zip(keep, vkeep)
                         if all(abs(v[key] - w[key]) <= tol[key] for key in self.objective_keys)), None)
            if twin is not None:
                a.status, a.pareto_rank = "duplicate", twin.pareto_rank
                twin.aliases.append(a.alt_id)
                continue
            keep.append(a), vkeep.append(v)
        ranks = nondominated_sort(vkeep, tol)
        for a, r in zip(keep, ranks):
            a.pareto_rank = r
            a.status = "efficient" if r == 1 else "dominated"
            a.dominated_by = []
        for i, (a, va) in enumerate(zip(keep, vkeep)):
            if a.status == "dominated":
                a.dominated_by = [b.alt_id for b, vb in zip(keep, vkeep) if b is not a and dominates(vb, va, tol)][:5]
        for a in self.alternatives:
            if a.status == "duplicate":
                twin = next((k for k in keep if a.alt_id in k.aliases), None)
                a.pareto_rank = twin.pareto_rank if twin else None

    def efficient(self) -> List[PortfolioAlternative]:
        return [a for a in self.alternatives if a.status == "efficient"]

    def get(self, alt_id: str) -> PortfolioAlternative:
        return next(a for a in self.alternatives if a.alt_id == alt_id)

    def normalized(self, alt: PortfolioAlternative) -> Dict[str, float]:
        """0 = nadir, 1 = ideal for every objective (direction-aware)."""
        out = {}
        for k in alt.objectives:
            if k not in self.ideal or k not in self.nadir:
                continue
            lo, hi = self.nadir[k], self.ideal[k]
            out[k] = 0.0 if abs(hi - lo) < 1e-12 else (alt.objectives[k] - lo) / (hi - lo)
        return out

    def to_dict(self) -> Dict[str, object]:
        return {"model_version": self.version.label(), "objective_keys": list(self.objective_keys),
                "signs": self.signs, "ideal": self.ideal, "nadir": self.nadir, "payoff": self.payoff,
                "infeasible": self.infeasible, "log": self.log,
                "alternatives": [a.to_dict() for a in self.alternatives]}
