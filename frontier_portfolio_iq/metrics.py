"""Portfolio metrics: enterprise and LOB outcomes, investment, capacity, compliance, risk, maturity."""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional

from .domain import ENTERPRISE_UNALLOCATED, CapacityType, PortfolioData
from .optimizer import PortfolioResult


def lob_value_split(data: PortfolioData, selected) -> Dict[str, float]:
    """Attribute each selected project's enterprise value exactly once: LOB attributions plus the
    unallocated enterprise-wide remainder. Sum equals enterprise value (no double counting)."""
    out: Dict[str, float] = defaultdict(float)
    for pid in selected:
        for l, v in data.attributed(pid).items():
            out[l] += v
        out[ENTERPRISE_UNALLOCATED] += data.unallocated_benefit(pid)
    return dict(out)


def pool_usage(res: PortfolioResult) -> Dict[str, Dict[int, float]]:
    used: Dict[str, Dict[int, float]] = defaultdict(lambda: defaultdict(float))
    for a in res.allocations:
        used[a.pool_id][a.month] += a.hours
    return used


def portfolio_metrics(res: PortfolioResult) -> Dict:
    data = res.data
    P = data.projects
    if not res.feasible:
        return {"status": res.status, "feasible": False, "diagnostics": res.diagnostics,
                "label": res.label, "tier": res.effective_tier.value}
    sel = sorted(res.selected)
    ent_value = sum(P[i].value for i in sel)
    lob_value = lob_value_split(data, sel)

    inv_lob: Dict[str, float] = defaultdict(float)
    inv_month: Dict[int, float] = defaultdict(float)
    inv_lob_month: Dict[str, Dict[int, float]] = defaultdict(lambda: defaultdict(float))
    for i in sel:
        p = P[i]
        for k, c in p.cost.items():
            t = res.starts[i] + k - 1
            inv_month[t] += c
            for l, sh in p.shares().items():
                inv_lob[l] += sh * c
                inv_lob_month[l][t] += sh * c
    transfer_cost = sum(a.transfer_cost for a in res.allocations)
    incremental_cost = sum(a.incremental_cost for a in res.allocations)
    xfer_lob: Dict[str, float] = defaultdict(float)
    for a in res.allocations:
        extra = a.transfer_cost + a.incremental_cost
        if extra:
            for l, sh in P[a.project_id].shares().items():
                xfer_lob[l] += sh * extra
                inv_lob[l] += sh * extra
                inv_month[a.month] += sh * extra
                inv_lob_month[l][a.month] += sh * extra
    investment = sum(inv_lob.values())

    used = pool_usage(res)
    util, avail_by_type, used_by_type = {}, defaultdict(float), defaultdict(float)
    for pid, pool in data.pools.items():
        A = sum(pool.available_hours(t) for t in data.months)
        U = sum(used[pid].values())
        util[pid] = U / A if A else 0.0
        key = "Shared" if pool.capacity_type == CapacityType.SHARED else "Restricted"
        avail_by_type[key] += A
        used_by_type[key] += U
    rf_util = used_by_type["Restricted"] / avail_by_type["Restricted"] if avail_by_type["Restricted"] else 0.0
    sh_util = used_by_type["Shared"] / avail_by_type["Shared"] if avail_by_type["Shared"] else 0.0
    total_A = sum(avail_by_type.values())
    overall_util = sum(used_by_type.values()) / total_A if total_A else 0.0

    compliance = [i for i, p in P.items() if p.compliance]
    comp_sel = [i for i in compliance if i in res.selected]
    kpis = sorted({(b.lob_id, b.kpi) for b in data.benefits})
    covered = sorted({(b.lob_id, b.kpi) for b in data.benefits if b.project_id in res.selected})
    target_attain = []
    for t in data.targets:
        got = sum(b.attributed_benefit for b in data.benefits
                  if b.lob_id == t.lob_id and b.kpi == t.kpi and b.project_id in res.selected)
        target_attain.append({"lob": t.lob_id, "kpi": t.kpi, "target": t.target, "achieved": got,
                              "attainment": got / t.target if t.target else None,
                              "min_outcome": t.min_outcome,
                              "min_outcome_met": (t.min_outcome is None or got >= t.min_outcome - 1e-6)})
    mat_lob: Dict[str, float] = defaultdict(float)
    for i in sel:
        for l in {P[i].primary_lob, *P[i].secondary_lobs}:
            mat_lob[l] += P[i].maturity_gain
    wconf = sum(P[i].delivery_confidence * P[i].value for i in sel) / ent_value if ent_value else 0.0
    discounted = 0.0
    H, d = data.settings.horizon, data.settings.schedule_discount
    for i in sel:
        p = P[i]
        e_min = p.earliest_start + p.duration - 1
        discounted += p.value * (1 - d * max(0, res.end(i) - e_min) / H)

    by_lob: Dict[str, List[str]] = defaultdict(list)
    for i in sel:
        by_lob[P[i].primary_lob].append(i)

    return {
        "label": res.label, "status": res.status, "feasible": True,
        "tier": res.effective_tier.value, "approvals_required": res.approvals_required,
        "enterprise_value": ent_value, "discounted_value": discounted,
        # Net enterprise value: time-adjusted value net of transfer and incremental resource costs.
        # This is the quantity the solver maximizes, so scenario deltas are measured on it.
        "net_value": discounted - transfer_cost - incremental_cost,
        "lob_value": lob_value, "investment": investment, "investment_by_lob": dict(inv_lob),
        "investment_by_month": {t: inv_month.get(t, 0.0) for t in data.months},
        "investment_by_lob_month": {l: {t: v.get(t, 0.0) for t in data.months} for l, v in inv_lob_month.items()},
        "transfer_cost": transfer_cost, "incremental_resource_cost": incremental_cost,
        "transfer_cost_by_lob": dict(xfer_lob),
        "selected": sel, "selected_by_lob": dict(by_lob), "n_selected": len(sel),
        "starts": dict(res.starts), "ends": {i: res.end(i) for i in sel},
        "pool_utilization": util, "ring_fenced_utilization": rf_util,
        "shared_utilization": sh_util, "overall_utilization": overall_util,
        "compliance_coverage": len(comp_sel) / len(compliance) if compliance else 1.0,
        "compliance_missed": [i for i in compliance if i not in res.selected],
        "risk_reduction": sum(P[i].risk_reduction for i in sel),
        "maturity_gain": sum(P[i].maturity_gain for i in sel), "maturity_by_lob": dict(mat_lob),
        "delivery_confidence": wconf,
        "avg_execution_risk": (sum(P[i].execution_risk for i in sel) / len(sel)) if sel else 0.0,
        "kpi_coverage": len(covered) / len(kpis) if kpis else 1.0,
        "kpis_covered": covered, "kpis_total": kpis, "target_attainment": target_attain,
        "financial_efficiency": ent_value / investment if investment else 0.0,
        "transfer_hours": dict(res.rules_used),
    }


def delta(m: Dict, base: Dict, unconstrained: Optional[Dict] = None) -> Dict:
    """Scenario impact versus a baseline portfolio."""
    if not m.get("feasible") or not base.get("feasible"):
        return {"feasible": False}
    lobs = sorted(set(m["lob_value"]) | set(base["lob_value"]))
    added = sorted(set(m["selected"]) - set(base["selected"]))
    removed = sorted(set(base["selected"]) - set(m["selected"]))
    moved = sorted((i, base["starts"][i], m["starts"][i]) for i in set(m["selected"]) & set(base["selected"])
                   if m["starts"][i] != base["starts"][i])
    out = {
        "feasible": True,
        "enterprise_value": m["enterprise_value"] - base["enterprise_value"],
        "net_value": m["net_value"] - base["net_value"],
        "lob_value": {l: m["lob_value"].get(l, 0.0) - base["lob_value"].get(l, 0.0) for l in lobs},
        "investment": m["investment"] - base["investment"],
        "projects_added": added, "projects_removed": removed, "schedule_changes": moved,
        "overall_utilization": m["overall_utilization"] - base["overall_utilization"],
        "ring_fenced_utilization": m["ring_fenced_utilization"] - base["ring_fenced_utilization"],
        "transfer_cost": m["transfer_cost"] - base["transfer_cost"],
        "compliance_coverage": m["compliance_coverage"] - base["compliance_coverage"],
        "risk_reduction": m["risk_reduction"] - base["risk_reduction"],
        "maturity_gain": m["maturity_gain"] - base["maturity_gain"],
    }
    if unconstrained and unconstrained.get("feasible"):
        out["opportunity_cost"] = unconstrained["net_value"] - m["net_value"]
    return out
