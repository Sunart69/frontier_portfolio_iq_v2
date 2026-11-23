"""LOB-aware scenario workbench: the 12 required what-if scenarios plus portfolio tiers."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from .domain import (
    ANY, ApprovalStatus, CapacityPool, CapacityType, PortfolioData, PortfolioTier, TransferRule,
)
from .metrics import delta, portfolio_metrics
from .optimizer import PortfolioResult, SolveOptions, solve

Builder = Callable[[PortfolioData], Tuple[PortfolioData, SolveOptions]]


@dataclass
class Scenario:
    sid: int
    key: str
    name: str
    description: str
    builder: Builder
    decisions: List[str] = field(default_factory=list)   # leadership decisions / approvals needed
    needs_resources: bool = False                          # hiring, funding or capacity not yet authorized
    relaxes_baseline: bool = False                         # feasible region contains the committed plan

    def __getstate__(self):  # builders are closures; drop them so outcomes can be cached/pickled
        st = dict(self.__dict__)
        st["builder"] = None
        return st


@dataclass
class ScenarioOutcome:
    scenario: Scenario
    result: PortfolioResult
    metrics: Dict
    deltas: Dict = field(default_factory=dict)

    @property
    def status_label(self) -> str:
        """Committed only when executable with currently authorized budgets and capacity."""
        if self.result.tier == PortfolioTier.UNCONSTRAINED:
            return "Analytical (unconstrained)"
        if not self.result.feasible:
            return "Infeasible"
        if self.scenario.needs_resources or self.result.approvals_required:
            return "Conditional — requires approval"
        return "Committed basis"

    @property
    def approvals(self) -> List[str]:
        out = list(self.scenario.decisions) if (self.scenario.needs_resources or self.result.approvals_required) else []
        rules = {r.rule_id: r for r in self.result.data.transfers}
        for rid in self.result.approvals_required:
            r = rules.get(rid)
            if r is not None:
                dest = "any LOB" if r.dest_lob == ANY else r.dest_lob
                out.append(f"{r.source_lob} approval to release {r.source_pool} capacity to {dest} ({rid})")
        return list(dict.fromkeys(out))


# --------------------------------------------------------------------------- data transforms
def only_authorized(data: PortfolioData) -> PortfolioData:
    """Drop pending and rejected configured rules, so a scenario measures only its own change.
    Approved / pre-authorized rules stay: they are part of the committed baseline."""
    d = data.copy()
    d.transfers = [r for r in d.transfers if r.is_authorized() or r.synthetic]
    return d


def add_release_rules(data: PortfolioData, pct: float, pools: Optional[List[str]] = None,
                      tag: str = "REL") -> PortfolioData:
    """Authorized release mechanism: a pending rule letting other LOBs use pct of a ring-fenced pool."""
    d = data.copy()
    for pl in d.pools.values():
        if pools is not None:
            if pl.pool_id not in pools or pl.capacity_type == CapacityType.SHARED:
                continue
        elif not (pl.capacity_type == CapacityType.RING_FENCED and pl.release_eligible):
            continue
        d.transfers.append(TransferRule(
            rule_id=f"{tag}-{pl.pool_id}-{int(round(pct * 100))}", source_pool=pl.pool_id,
            source_lob=pl.owning_lob, dest_lob=ANY, max_pct=pct, efficiency=pl.release_efficiency,
            cost_per_hour=pl.release_cost_rate, approval_required=True,
            approval_status=ApprovalStatus.PENDING, effective_start=1, synthetic=True,
            note=f"Scenario release of {pct:.0%} of {pl.pool_id}"))
    return d


def add_lob_transfer(data: PortfolioData, source: str, dest: str, pct: float = 0.25,
                     efficiency: float = 0.9, cost: float = 15.0) -> PortfolioData:
    d = data.copy()
    for pl in d.pools.values():
        if pl.owning_lob == source and pl.capacity_type != CapacityType.SHARED:
            d.transfers.append(TransferRule(
                rule_id=f"XFER-{source}-{dest}-{pl.pool_id}", source_pool=pl.pool_id, source_lob=source,
                dest_lob=dest, max_pct=pct, efficiency=efficiency, cost_per_hour=cost,
                approval_required=True, approval_status=ApprovalStatus.PENDING, synthetic=True,
                note=f"Scenario transfer {pct:.0%} {source}->{dest}"))
    return d


def scale_shared(data: PortfolioData, factor: float) -> PortfolioData:
    d = data.copy()
    for pl in d.pools.values():
        if pl.capacity_type == CapacityType.SHARED:
            pl.fte *= factor
    return d


def scale_budget(data: PortfolioData, lob: str, factor: float) -> PortfolioData:
    d = data.copy()
    for b in d.budgets:
        if b.lob_id == lob:
            b.amount *= factor
    return d


def add_contractors(data: PortfolioData, lob: str, capability: str, fte: float, start: int,
                    months: int, premium_rate: float) -> PortfolioData:
    d = data.copy()
    pid = f"TMP-CONTRACT-{lob}"
    d.pools[pid] = CapacityPool(pid, capability, lob, CapacityType.RING_FENCED, start,
                                min(d.settings.horizon, start + months - 1), fte, cost_rate=premium_rate,
                                incremental_cost_rate=premium_rate,
                                funding_restrictions="Temporary contractor capacity (scenario)")
    return d


# --------------------------------------------------------------------------- helpers
def _capability_pressure(data: PortfolioData, committed: PortfolioResult, lob: str) -> Dict[str, float]:
    """Utilization of the pools a LOB can draw on, by capability, in the committed plan."""
    from .optimizer import direct_eligible  # local import to avoid cycle at module load
    used: Dict[str, float] = defaultdict(float)
    avail: Dict[str, float] = defaultdict(float)
    probe = next((p for p in data.projects.values() if p.primary_lob == lob), None)
    for a in committed.allocations:
        used[a.pool_id] += a.hours
    out: Dict[str, float] = defaultdict(float)
    for pl in data.pools.values():
        if probe is not None and not direct_eligible(data, pl, probe):
            continue
        A = sum(pl.available_hours(t) for t in data.months)
        avail[pl.capability] += A
        out[pl.capability] += used[pl.pool_id]
    return {c: out[c] / avail[c] for c in avail if avail[c] > 0}


def most_constrained_lob(data: PortfolioData, committed: PortfolioResult,
                         unconstrained: PortfolioResult) -> Tuple[str, str]:
    """LOB with the largest value deferred, and the most saturated capability its deferred work needs."""
    lost: Dict[str, float] = defaultdict(float)
    need: Dict[Tuple[str, str], float] = defaultdict(float)
    deferred = [pid for pid in unconstrained.selected - committed.selected]
    if not any(data.projects[p].primary_lob in data.business_lobs() for p in deferred):
        deferred = [pid for pid in data.projects if pid not in committed.selected]
    for pid in deferred:
        p = data.projects[pid]
        if p.primary_lob in data.business_lobs():
            lost[p.primary_lob] += p.value
            for (c, _k), h in p.demand.items():
                need[(p.primary_lob, c)] += h
    lob = max(lost, key=lost.get) if lost else data.business_lobs()[0]
    pressure = _capability_pressure(data, committed, lob)
    cands = {c: pressure.get(c, 1.0) for (l, c), h in need.items() if l == lob and h > 0}
    cap = max(cands, key=cands.get) if cands else next(iter(data.pools.values())).capability
    return lob, cap


def earliest_feasible_start(data: PortfolioData, pid: str) -> int:
    p = data.projects[pid]
    e = p.earliest_start
    for d in p.depends_on:
        if d in data.projects:
            e = max(e, earliest_feasible_start(data, d) + data.projects[d].duration)
    return e


def default_accelerate(data: PortfolioData, committed: PortfolioResult) -> Tuple[str, int]:
    """Priority-1 LOB initiative in the committed plan with the most value-weighted room to accelerate;
    target start is up to three months earlier, never before its dependency chain allows."""
    best = None
    for pid, p in data.projects.items():
        if pid not in committed.selected or p.primary_lob not in data.business_lobs() or p.lob_priority != 1:
            continue
        target = max(earliest_feasible_start(data, pid), committed.starts[pid] - 3)
        gain = (committed.starts[pid] - target) * p.value
        if gain > 0 and (best is None or gain > best[0]):
            best = (gain, pid, target)
    if best is None:
        pid = max((p.value, pid) for pid, p in data.projects.items() if p.primary_lob in data.business_lobs())[1]
        return pid, earliest_feasible_start(data, pid)
    return best[1], best[2]


# --------------------------------------------------------------------------- catalogue
def scenario_catalogue(data: PortfolioData, committed: PortfolioResult, unconstrained: PortfolioResult,
                       transfer_source: Optional[str] = None, transfer_dest: Optional[str] = None,
                       budget_cut_lob: Optional[str] = None, accelerate: Optional[Tuple[str, int]] = None,
                       contractor_lob: Optional[str] = None) -> List[Scenario]:
    biz = data.business_lobs()
    src = transfer_source or biz[0]
    dst = transfer_dest or (biz[1] if len(biz) > 1 else biz[0])
    cut = budget_cut_lob or biz[0]
    acc_pid, acc_month = accelerate or default_accelerate(data, committed)
    c_lob, c_cap = most_constrained_lob(data, committed, unconstrained)
    if contractor_lob:
        c_lob = contractor_lob
    first = data.settings.as_of_month
    platforms = {pid: 1.3 for pid, p in data.projects.items() if p.shared_platform}
    L = lambda l: data.lobs[l].name if l in data.lobs else l  # noqa: E731

    return [
        Scenario(1, "current_ring_fencing", "Enterprise-optimal, current ring-fencing",
                 "Honors all ring-fences; only approved or pre-authorized transfers are used.",
                 lambda d: (d, SolveOptions(tier=PortfolioTier.COMMITTED))),
        Scenario(2, "all_authorized_sharing", "Enterprise-optimal, all authorized shared capacity",
                 "Adds every configured transfer rule not rejected (pending approvals assumed granted).",
                 lambda d: (d, SolveOptions(tier=PortfolioTier.CONDITIONAL)),
                 decisions=["Grant pending transfer approvals"], relaxes_baseline=True),
        Scenario(3, "release_10", "Release 10% of eligible ring-fenced capacity",
                 "Owners release 10% of release-eligible ring-fenced pools to other LOBs.",
                 lambda d: (add_release_rules(only_authorized(d), 0.10), SolveOptions(tier=PortfolioTier.CONDITIONAL)),
                 decisions=["Owning-LOB approval for 10% ring-fence release"], relaxes_baseline=True),
        Scenario(4, "release_20", "Release 20% of eligible ring-fenced capacity",
                 "Owners release 20% of release-eligible ring-fenced pools to other LOBs.",
                 lambda d: (add_release_rules(only_authorized(d), 0.20), SolveOptions(tier=PortfolioTier.CONDITIONAL)),
                 decisions=["Owning-LOB approval for 20% ring-fence release"], relaxes_baseline=True),
        Scenario(5, "transfer_a_b", f"Transfer capacity from {L(src)} to {L(dst)}",
                 f"Up to 25% of {L(src)} ring-fenced pools transferred to {L(dst)} at 90% efficiency.",
                 lambda d: (add_lob_transfer(only_authorized(d), src, dst), SolveOptions(tier=PortfolioTier.CONDITIONAL)),
                 decisions=[f"{L(src)} leadership approval to transfer capacity to {L(dst)}"],
                 relaxes_baseline=True),
        Scenario(6, "shared_plus_20", "Increase enterprise shared capacity by 20%",
                 "Hire or contract 20% more FTE in every enterprise shared pool.",
                 lambda d: (scale_shared(d, 1.2), SolveOptions(tier=PortfolioTier.COMMITTED)),
                 decisions=["Fund and hire +20% enterprise shared capacity"], needs_resources=True,
                 relaxes_baseline=True),
        Scenario(7, "budget_cut", f"Reduce {L(cut)} budget by 15%",
                 f"All {L(cut)} funding sources reduced 15% in every month.",
                 lambda d: (scale_budget(d, cut, 0.85), SolveOptions(tier=PortfolioTier.COMMITTED)),
                 decisions=[f"Leadership decision to reduce {L(cut)} budget"]),
        Scenario(8, "protect_compliance", "Protect all LOB compliance commitments",
                 "Every compliance-classified project becomes mandatory.",
                 lambda d: (d, SolveOptions(tier=PortfolioTier.COMMITTED, protect_compliance=True))),
        Scenario(9, "prioritize_enterprise", "Prioritize enterprise-wide initiatives",
                 "Shared-platform value weighted 1.3x and 25% of shared capacity reserved for them.",
                 lambda d: (d, SolveOptions(tier=PortfolioTier.COMMITTED, value_multipliers=platforms,
                                            enterprise_reservation_pct=0.25))),
        Scenario(10, "balanced", "Optimize for balanced LOB outcomes",
                 "Maximizes the worst-off LOB's outcome attainment with minimum investment shares.",
                 lambda d: (d, SolveOptions(tier=PortfolioTier.COMMITTED, objective="balanced",
                                            enforce_min_investment_share=True))),
        Scenario(11, "accelerate", f"Accelerate {acc_pid} {data.projects[acc_pid].name}",
                 f"{data.projects[acc_pid].name} must start by {data.settings.month_label(acc_month)}.",
                 lambda d: (d, SolveOptions(tier=PortfolioTier.COMMITTED, start_by={acc_pid: acc_month}))),
        Scenario(12, "contractors", f"Add temporary contractors to {L(c_lob)}",
                 f"3 contractor FTE of {c_cap} for 6 months at a $85/hr premium charged to {L(c_lob)}.",
                 lambda d: (add_contractors(d, c_lob, c_cap, 3, first, 6, 85.0),
                            SolveOptions(tier=PortfolioTier.COMMITTED)),
                 decisions=[f"Fund temporary {c_cap} contractors for {L(c_lob)}"], needs_resources=True,
                 relaxes_baseline=True),
    ]


def run_scenario(data: PortfolioData, sc: Scenario, base_metrics: Optional[Dict] = None,
                 unconstrained_metrics: Optional[Dict] = None,
                 baseline: Optional[PortfolioResult] = None) -> ScenarioOutcome:
    d, opts = sc.builder(data)
    opts.label = sc.name
    if baseline is not None and baseline.feasible:
        opts.candidate_starts = [dict(baseline.starts)]
        if sc.relaxes_baseline:
            opts.objective_floor = baseline.objective
    res = solve(d, opts)
    m = portfolio_metrics(res)
    out = ScenarioOutcome(sc, res, m)
    if base_metrics is not None:
        out.deltas = delta(m, base_metrics, unconstrained_metrics)
    return out


def tier_portfolios(data: PortfolioData) -> Dict[str, PortfolioResult]:
    """Committed, conditional and unconstrained-opportunity portfolios plus a strict reference."""
    strict = solve(data, SolveOptions(tier=PortfolioTier.COMMITTED, strict_ring_fence=True,
                                      label="Strict ring-fencing (no transfers)"))
    floor = strict.objective if strict.feasible else None
    committed = solve(data, SolveOptions(tier=PortfolioTier.COMMITTED, label="Committed portfolio",
                                         objective_floor=floor))
    floor = committed.objective if committed.feasible else None
    conditional = solve(add_release_rules(data, 0.20),
                        SolveOptions(tier=PortfolioTier.CONDITIONAL, label="Conditional portfolio",
                                     objective_floor=floor))
    unconstrained = solve(data, SolveOptions(tier=PortfolioTier.UNCONSTRAINED,
                                             label="Unconstrained opportunity portfolio",
                                             objective_floor=floor))
    return {"strict": strict, "committed": committed, "conditional": conditional,
            "unconstrained": unconstrained}


def pool_release_values(data: PortfolioData, committed_metrics: Dict, pct: float = 0.30,
                        committed: Optional[PortfolioResult] = None) -> List[Dict]:
    """Incremental enterprise value from releasing pct of each restricted pool (one at a time)."""
    out = []
    for pl in data.pools.values():
        if pl.capacity_type == CapacityType.SHARED:
            continue
        res = solve(add_release_rules(only_authorized(data), pct, pools=[pl.pool_id], tag="POOLREL"),
                    SolveOptions(tier=PortfolioTier.CONDITIONAL, label=f"Release {pl.pool_id}",
                                 objective_floor=committed.objective if committed else None))
        m = portfolio_metrics(res)
        if not m.get("feasible"):
            continue
        gain = m["net_value"] - committed_metrics["net_value"]
        beneficiaries: Dict[str, float] = defaultdict(float)
        for a in res.allocations:
            if a.pool_id == pl.pool_id and a.rule_id and a.rule_id.startswith("POOLREL"):
                beneficiaries[a.lob] += a.hours
        out.append({"pool": pl.pool_id, "owner": pl.owning_lob, "capability": pl.capability,
                    "type": pl.capacity_type.value, "release_pct": pct,
                    "incremental_value": max(0.0, gain), "hours_released_used": sum(beneficiaries.values()),
                    "beneficiary_lobs": dict(beneficiaries),
                    "release_eligible": pl.release_eligible or pl.capacity_type == CapacityType.CONDITIONAL})
    return sorted(out, key=lambda r: -r["incremental_value"])
