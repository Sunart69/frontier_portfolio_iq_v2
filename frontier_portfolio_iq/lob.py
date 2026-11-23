"""LOB-perspective optimization and reconciliation against the enterprise portfolio."""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List

from .domain import CapacityType, PortfolioData, PortfolioTier
from .metrics import lob_value_split, pool_usage
from .optimizer import PortfolioResult, SolveOptions, solve


def optimize_lob(data: PortfolioData, lob: str, tier: PortfolioTier = PortfolioTier.COMMITTED) -> PortfolioResult:
    """Best achievable outcome for one LOB using its entitled capacity (owned pools, eligible shared
    pools, authorized transfers in) and its own funding. Prerequisites owned by other LOBs are assumed
    delivered at their earliest date; co-funding partners are assumed to fund their shares."""
    cands = {pid for pid, p in data.projects.items() if p.primary_lob == lob}
    opts = SolveOptions(tier=tier, objective="lob", lob_focus=lob, candidate_projects=cands,
                        budget_lobs={lob}, assume_external_dependencies=True,
                        label=f"{data.lobs[lob].name} standalone optimum")
    return solve(data, opts)


def reconcile(data: PortfolioData, enterprise: PortfolioResult,
              lob_results: Dict[str, PortfolioResult]) -> Dict:
    """Compare independently optimized LOB plans with the enterprise plan.

    Shared pools are the conflict surface: each standalone LOB plan sees the full shared pool, so the
    sum of standalone claims can exceed availability. The enterprise plan is the single allocation of
    record; LOB commitments are taken from it, never from standalone plans.
    """
    ent_used = pool_usage(enterprise)
    ent_lob_value = lob_value_split(data, enterprise.selected) if enterprise.feasible else {}
    claims: Dict[str, Dict[int, Dict[str, float]]] = defaultdict(lambda: defaultdict(dict))
    lob_rows = []
    for lob, res in lob_results.items():
        if not res.feasible:
            lob_rows.append({"lob": lob, "standalone_status": res.status})
            continue
        for a in res.allocations:
            if data.pools[a.pool_id].capacity_type == CapacityType.SHARED:
                claims[a.pool_id][a.month][lob] = claims[a.pool_id][a.month].get(lob, 0.0) + a.hours
        standalone_val = sum(data.lob_benefit(pid, lob) for pid in res.selected)
        own = sum(data.lob_benefit(pid, lob) for pid in enterprise.selected
                  if data.projects[pid].primary_lob == lob)
        received = ent_lob_value.get(lob, 0.0) - own
        lob_rows.append({
            "lob": lob, "lob_name": data.lobs[lob].name, "standalone_status": res.status,
            "standalone_value": standalone_val,
            "enterprise_plan_own_value": own,
            "cross_lob_benefit_received": received,
            "enterprise_plan_value": ent_lob_value.get(lob, 0.0),
            # value from the LOB's own projects given up so shared capacity can serve the enterprise plan
            "concession": max(0.0, standalone_val - own),
            "standalone_only": sorted(res.selected - enterprise.selected),
            "enterprise_only": sorted(p for p in enterprise.selected if data.projects[p].primary_lob == lob
                                      and p not in res.selected),
        })

    conflicts = []
    for pool, months in claims.items():
        for t, by_lob in months.items():
            A = data.pools[pool].available_hours(t)
            claimed = sum(by_lob.values())
            if claimed > A + 1e-6:
                conflicts.append({"pool": pool, "month": t, "available": A, "claimed": claimed,
                                  "oversubscribed": claimed - A, "claims": dict(by_lob),
                                  "enterprise_allocated": ent_used.get(pool, {}).get(t, 0.0)})
    conflicts.sort(key=lambda c: (c["pool"], c["month"]))

    # Enterprise commitments by LOB on shared pools (the single allocation of record)
    commitments: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
    within = True
    for pool, by_t in ent_used.items():
        for t, hrs in by_t.items():
            if hrs > data.pools[pool].available_hours(t) + 1e-6:
                within = False
    for a in enterprise.allocations:
        if data.pools[a.pool_id].capacity_type == CapacityType.SHARED:
            commitments[a.lob][a.pool_id] += a.hours

    return {
        "lobs": lob_rows,
        "shared_conflicts": conflicts,
        "oversubscribed_hours": sum(c["oversubscribed"] for c in conflicts),
        "enterprise_within_capacity": within,
        "shared_commitments_by_lob": {l: dict(v) for l, v in commitments.items()},
    }


def lob_perspective(data: PortfolioData, enterprise: PortfolioResult) -> Dict:
    results = {l: optimize_lob(data, l) for l in data.business_lobs()}
    rec = reconcile(data, enterprise, results)
    rec["results"] = results
    return rec
