"""Capacity reporting: utilization, idle ring-fenced hours, unused shared hours, restriction effects,
shortages, and the split between consumed and committed-but-not-yet-consumed capacity."""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional

from .domain import CapacityType, PortfolioData
from .metrics import pool_usage
from .optimizer import PortfolioResult


def shortages(data: PortfolioData, result: PortfolioResult, reference: Optional[PortfolioResult]) -> List[Dict]:
    """Unmet demand: capability hours of projects the reference (opportunity) portfolio would deliver
    but this portfolio defers, at the reference schedule."""
    out: Dict[tuple, float] = defaultdict(float)
    if reference is None or not reference.feasible:
        return []
    for pid in sorted(reference.selected - result.selected):
        p = data.projects[pid]
        s = reference.starts[pid]
        for (c, k), h in p.demand.items():
            out[(p.primary_lob, c, s + k - 1)] += h
    return [{"lob": l, "capability": c, "month": t, "hours": h} for (l, c, t), h in sorted(out.items())]


def capacity_report(result: PortfolioResult, reference: Optional[PortfolioResult] = None) -> Dict:
    data = result.data
    as_of = data.settings.as_of_month
    used = pool_usage(result)
    rows = []
    for pid, pool in data.pools.items():
        for t in data.months:
            A = pool.available_hours(t)
            U = used.get(pid, {}).get(t, 0.0)
            rows.append({
                "pool": pid, "capability": pool.capability, "owner": pool.owning_lob,
                "type": pool.capacity_type.value, "month": t, "available": A, "allocated": U,
                "consumed": U if t < as_of else 0.0,
                "committed_not_consumed": U if t >= as_of else 0.0,
                "unused": max(0.0, A - U), "utilization": (U / A) if A else 0.0,
            })

    short = shortages(data, result, reference)
    short_cap_month: Dict[tuple, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for s in short:
        short_cap_month[(s["capability"], s["month"])][s["lob"]] += s["hours"]

    # Idle restricted hours that other LOBs needed: "unavailable due to restrictions"
    restricted_unavailable = 0.0
    restricted_rows = []
    for r in rows:
        if r["type"] == CapacityType.SHARED.value or r["unused"] <= 0:
            continue
        need = 0.0
        for cap in {r["capability"]} | {s.to_capability for s in data.substitutions
                                         if s.from_capability == r["capability"]}:
            eff = data.substitution_efficiency(r["capability"], cap)
            others = {l: h for l, h in short_cap_month.get((cap, r["month"]), {}).items() if l != r["owner"]}
            need += sum(others.values()) / eff if eff else 0.0
        blocked = min(r["unused"], need)
        if blocked > 0:
            restricted_unavailable += blocked
            restricted_rows.append({"pool": r["pool"], "month": r["month"], "idle": r["unused"],
                                    "blocked_demand_hours": blocked})

    def agg(key):
        out: Dict = defaultdict(float)
        for s in short:
            out[s[key]] += s["hours"]
        return dict(out)

    restricted = [r for r in rows if r["type"] != CapacityType.SHARED.value]
    shared = [r for r in rows if r["type"] == CapacityType.SHARED.value]
    bottlenecks = defaultdict(int)
    for r in rows:
        if r["available"] > 0 and r["utilization"] >= 0.95:
            bottlenecks[r["capability"]] += 1
    idle_by_pool = defaultdict(float)
    for r in restricted:
        idle_by_pool[r["pool"]] += r["unused"]

    return {
        "rows": rows,
        "idle_ring_fenced_hours": sum(r["unused"] for r in restricted),
        "idle_by_restricted_pool": dict(idle_by_pool),
        "unused_shared_hours": sum(r["unused"] for r in shared),
        "restricted_unavailable_hours": restricted_unavailable,
        "restricted_detail": restricted_rows,
        "consumed_hours": sum(r["consumed"] for r in rows),
        "committed_not_consumed_hours": sum(r["committed_not_consumed"] for r in rows),
        "shortages": short,
        "shortage_by_lob": agg("lob"), "shortage_by_capability": agg("capability"),
        "shortage_by_month": agg("month"),
        "bottleneck_months_by_capability": dict(bottlenecks),
    }
