"""End-to-end analysis orchestration: tiers, scenarios, LOB reconciliation, capacity, decisions.

Everything the dashboards, results workbook and PowerPoint show comes from the AnalysisBundle
produced here, so every published figure traces to a solver run.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

from .capacity import capacity_report
from .domain import ANY, PortfolioData
from .lob import lob_perspective
from .metrics import delta, portfolio_metrics
from .optimizer import PortfolioResult
from .scenarios import (ScenarioOutcome, pool_release_values, run_scenario, scenario_catalogue,
                        tier_portfolios)


@dataclass
class AnalysisBundle:
    data: PortfolioData
    tiers: Dict[str, PortfolioResult]
    tier_metrics: Dict[str, Dict]
    scenarios: List[ScenarioOutcome]
    release_values: List[Dict]
    lob_view: Dict
    capacity: Dict
    classification: Dict[str, str]
    decisions: Dict[str, List]
    run_id: str = field(default_factory=lambda: _dt.datetime.now().strftime("%Y-%m-%d %H:%M"))

    @property
    def committed(self) -> Dict:
        return self.tier_metrics["committed"]

    def scenario(self, key: str) -> ScenarioOutcome:
        return next(s for s in self.scenarios if s.scenario.key == key)


def classify_projects(data: PortfolioData, tiers: Dict[str, PortfolioResult]) -> Dict[str, str]:
    """Approved = in committed; Conditional = needs approvals (conditional tier only);
    Deferred = only realizable if restrictions relaxed; Rejected = not selected in any tier."""
    out = {}
    for pid in data.projects:
        if pid in tiers["committed"].selected:
            out[pid] = "Approved"
        elif tiers["conditional"].feasible and pid in tiers["conditional"].selected:
            out[pid] = "Conditional"
        elif tiers["unconstrained"].feasible and pid in tiers["unconstrained"].selected:
            out[pid] = "Deferred"
        else:
            out[pid] = "Rejected"
    return out


def build_decisions(data: PortfolioData, b_tiers, tm, scenarios, release_values, lob_view) -> Dict[str, List]:
    rules = {r.rule_id: r for r in b_tiers["conditional"].data.transfers}
    cond_gain = tm["conditional"]["net_value"] - tm["committed"]["net_value"] if tm["conditional"].get("feasible") else 0
    capacity_releases = []
    for rid in b_tiers["conditional"].approvals_required:
        r = rules.get(rid)
        if r is None:
            continue
        capacity_releases.append({
            "rule": rid, "owner": data.lobs[r.source_lob].name if r.source_lob in data.lobs else r.source_lob,
            "pool": r.source_pool, "to": "Any LOB" if r.dest_lob == ANY else data.lobs[r.dest_lob].name,
            "hours_used": b_tiers["conditional"].rules_used.get(rid, 0.0)})
    funding = [{"scenario": s.scenario.name, "decision": "; ".join(s.approvals),
                "value_delta": s.deltas.get("net_value", 0.0)}
               for s in scenarios if s.scenario.needs_resources and s.result.feasible]
    commitments = []
    cm = tm["committed"]
    for l in data.business_lobs():
        commitments.append({"lob": data.lobs[l].name, "owner": data.lobs[l].outcome_owner,
                            "sponsor": data.lobs[l].executive_sponsor,
                            "investment": cm["investment_by_lob"].get(l, 0.0),
                            "value": cm["lob_value"].get(l, 0.0),
                            "projects": cm["selected_by_lob"].get(l, [])})
    trade_offs = []
    bal = next((s for s in scenarios if s.scenario.key == "balanced"), None)
    if bal and bal.result.feasible:
        trade_offs.append({"topic": "Balanced LOB outcomes vs enterprise value",
                           "value_delta": bal.deltas.get("net_value", 0.0)})
    for c in lob_view.get("lobs", []):
        if c.get("concession", 0) > 1e-6:
            trade_offs.append({"topic": f"{c['lob_name']} concedes standalone value to enterprise plan",
                               "value_delta": -c["concession"]})
    unc_gap = tm["unconstrained"]["net_value"] - cm["net_value"] if tm["unconstrained"].get("feasible") else 0
    trade_offs.append({"topic": "Value held back by current ring-fencing (analytical)", "value_delta": unc_gap})
    return {"capacity_releases": capacity_releases, "conditional_gain": cond_gain, "funding": funding,
            "commitments": commitments, "trade_offs": trade_offs}


def run_analysis(data: PortfolioData, release_scan: bool = True, **catalogue_kwargs) -> AnalysisBundle:
    tiers = tier_portfolios(data)
    tm = {k: portfolio_metrics(v) for k, v in tiers.items()}
    base, unc = tm["committed"], tm["unconstrained"]
    if not base.get("feasible"):
        raise RuntimeError("Committed portfolio infeasible: " + "; ".join(tiers["committed"].diagnostics))
    for k in ("strict", "conditional", "unconstrained"):
        tm[k]["delta_vs_committed"] = delta(tm[k], base, unc)
    cat = scenario_catalogue(data, tiers["committed"], tiers["unconstrained"], **catalogue_kwargs)
    outs = []
    for sc in cat:
        if sc.key == "current_ring_fencing":
            # Scenario 1 *is* the committed portfolio: reuse it so figures reconcile exactly.
            outs.append(ScenarioOutcome(sc, tiers["committed"], base, delta(base, base, unc)))
        else:
            outs.append(run_scenario(data, sc, base, unc, tiers["committed"]))
    rel = pool_release_values(data, base, committed=tiers["committed"]) if release_scan else []
    lv = lob_perspective(data, tiers["committed"])
    cap = capacity_report(tiers["committed"], tiers["unconstrained"])
    cls = classify_projects(data, tiers)
    dec = build_decisions(data, tiers, tm, outs, rel, lv)
    return AnalysisBundle(data, tiers, tm, outs, rel, lv, cap, cls, dec)


# --------------------------------------------------------------------------- results workbook
def write_results_workbook(b: AnalysisBundle, path: str) -> str:
    wb = Workbook()
    wb.remove(wb.active)
    hdr_fill, hdr_font = PatternFill("solid", fgColor="1F3A5F"), Font(color="FFFFFF", bold=True)

    def sheet(name, header, rows):
        ws = wb.create_sheet(name)
        ws.append(header)
        for r in rows:
            ws.append(r)
        for c in ws[1]:
            c.fill, c.font = hdr_fill, hdr_font
        for col in ws.columns:
            ws.column_dimensions[col[0].column_letter].width = 18
        ws.freeze_panes = "A2"

    d, lobs = b.data, b.data.business_lobs()
    rows = []
    for key, label in [("strict", "Reference"), ("committed", "Committed"), ("conditional", "Conditional"),
                       ("unconstrained", "Unconstrained Opportunity")]:
        m = b.tier_metrics[key]
        if m.get("feasible"):
            rows.append([m["label"], m["tier"], round(m["enterprise_value"]), round(m["net_value"]),
                         round(m["investment"]), m["n_selected"], round(m["ring_fenced_utilization"], 3),
                         round(m["transfer_cost"], 1), ", ".join(m["approvals_required"])]
                        + [round(m["lob_value"].get(l, 0)) for l in lobs])
    sheet("Portfolio Tiers", ["Portfolio", "Tier", "Enterprise Value $K", "Net Value $K", "Investment $K",
                              "Projects", "Ring-fenced Util", "Transfer Cost $K", "Approvals Required"]
          + [f"{d.lobs[l].name} Value $K" for l in lobs], rows)

    rows = []
    for s in b.scenarios:
        m, dl = s.metrics, s.deltas
        if not m.get("feasible"):
            rows.append([s.scenario.sid, s.scenario.name, s.status_label] + [None] * 14
                        + ["; ".join(s.result.diagnostics)])
            continue
        rows.append([s.scenario.sid, s.scenario.name, s.status_label, round(m["enterprise_value"]),
                     round(dl["enterprise_value"]), round(m["net_value"]), round(dl["investment"]),
                     ", ".join(dl["projects_added"]), ", ".join(dl["projects_removed"]),
                     len(dl["schedule_changes"]), round(dl["overall_utilization"], 3),
                     round(m["ring_fenced_utilization"], 3), round(m["transfer_cost"], 1),
                     round(dl["compliance_coverage"], 3), round(dl["risk_reduction"], 1),
                     round(dl["maturity_gain"], 1), round(dl.get("opportunity_cost", 0)), "; ".join(s.approvals)])
    sheet("Scenario Workbench", ["#", "Scenario", "Status", "Enterprise Value $K", "Δ Value $K",
                                 "Net Value $K", "Δ Investment $K", "Projects Added", "Projects Removed",
                                 "Schedule Changes", "Δ Utilization", "Ring-fenced Util",
                                 "Transfer Cost $K", "Δ Compliance Coverage", "Δ Risk Reduction",
                                 "Δ Maturity", "Opportunity Cost $K", "Approvals / Decisions"], rows)

    rows = []
    for s in b.scenarios:
        if s.metrics.get("feasible"):
            rows.append([s.scenario.name] + [round(s.deltas["lob_value"].get(l, 0)) for l in lobs])
    sheet("Scenario LOB Impact", ["Scenario"] + [f"Δ {d.lobs[l].name} $K" for l in lobs], rows)

    cm = b.committed
    sheet("Recommended Portfolio", ["Project", "Name", "Primary LOB", "Classification", "Start", "End",
                                    "Value $K", "Cost $K", "Mandatory", "Compliance", "Shared Platform"],
          [[pid, p.name, p.primary_lob, b.classification[pid],
            d.settings.month_label(cm["starts"][pid]) if pid in cm["starts"] else "",
            d.settings.month_label(cm["ends"][pid]) if pid in cm["ends"] else "",
            p.value, p.total_cost, p.mandatory, p.compliance, p.shared_platform]
           for pid, p in d.projects.items()])
    sheet("Capacity Allocation", ["Pool", "Project", "LOB", "Capability", "Month", "Hours",
                                  "Effective Hours", "Route", "Rule", "Transfer Cost $K"],
          [[a.pool_id, a.project_id, a.lob, a.capability, a.month, round(a.hours, 1),
            round(a.effective_hours, 1), a.kind, a.rule_id or "", round(a.transfer_cost, 2)]
           for a in sorted(b.tiers["committed"].allocations, key=lambda a: (a.pool_id, a.month))])
    sheet("Capacity Report", ["Pool", "Capability", "Owner", "Type", "Month", "Available", "Allocated",
                              "Consumed", "Committed Not Consumed", "Unused", "Utilization"],
          [[r["pool"], r["capability"], r["owner"], r["type"], r["month"], round(r["available"], 1),
            round(r["allocated"], 1), round(r["consumed"], 1), round(r["committed_not_consumed"], 1),
            round(r["unused"], 1), round(r["utilization"], 3)] for r in b.capacity["rows"]])
    sheet("Capacity Shortages", ["LOB", "Capability", "Month", "Unmet Hours"],
          [[s["lob"], s["capability"], s["month"], round(s["hours"], 1)] for s in b.capacity["shortages"]])
    sheet("Release Value by Pool", ["Pool", "Owner", "Capability", "Type", "Release %",
                                    "Incremental Value $K", "Hours Used by Others"],
          [[r["pool"], r["owner"], r["capability"], r["type"], r["release_pct"],
            round(r["incremental_value"]), round(r["hours_released_used"])] for r in b.release_values])
    sheet("LOB Reconciliation", ["LOB", "Standalone Value (own projects) $K", "Enterprise Plan: Own Projects $K",
                                 "Enterprise Plan: Cross-LOB Benefit $K", "Enterprise Plan Total $K",
                                 "Concession $K", "Standalone-only Projects", "Enterprise-only Projects"],
          [[r.get("lob_name", r["lob"]), round(r.get("standalone_value", 0)),
            round(r.get("enterprise_plan_own_value", 0)), round(r.get("cross_lob_benefit_received", 0)),
            round(r.get("enterprise_plan_value", 0)), round(r.get("concession", 0)),
            ", ".join(r.get("standalone_only", [])), ", ".join(r.get("enterprise_only", []))]
           for r in b.lob_view["lobs"]])
    sheet("Shared Capacity Conflicts", ["Pool", "Month", "Available", "Claimed by Standalone LOB Plans",
                                        "Oversubscribed", "Enterprise Allocated"],
          [[c["pool"], c["month"], round(c["available"]), round(c["claimed"]), round(c["oversubscribed"]),
            round(c["enterprise_allocated"])] for c in b.lob_view["shared_conflicts"]])
    wb.save(path)
    return path


# --------------------------------------------------------------------------- shared figure builders
# Dashboards and the PowerPoint both call these, and the tests recompute them, so every published
# figure has one definition.
SPIDER_DIMENSIONS = ["Business value", "Risk reduction", "Capability maturity", "Financial efficiency",
                     "Delivery confidence", "LOB outcome coverage"]


def spider_portfolios(b: AnalysisBundle) -> List[tuple]:
    out = [("Committed", b.tier_metrics["committed"])]
    for key, name in [("balanced", "LOB-balanced"), ("release_20", "Ring-fence release 20%")]:
        s = b.scenario(key)
        if s.metrics.get("feasible"):
            out.append((name, s.metrics))
    if b.tier_metrics["unconstrained"].get("feasible"):
        out.append(("Unconstrained (analytical)", b.tier_metrics["unconstrained"]))
    return out


def spider_scores(b: AnalysisBundle) -> Dict[str, List[float]]:
    raw = {}
    for name, m in spider_portfolios(b):
        raw[name] = [m["net_value"], m["risk_reduction"], m["maturity_gain"], m["financial_efficiency"],
                     m["delivery_confidence"], m["kpi_coverage"]]
    maxes = [max(v[i] for v in raw.values()) or 1.0 for i in range(len(SPIDER_DIMENSIONS))]
    return {n: [round(100.0 * v[i] / maxes[i], 1) for i in range(len(v))] for n, v in raw.items()}


def capability_supply_demand(b: AnalysisBundle) -> Dict[str, Dict[str, float]]:
    d = b.data
    out: Dict[str, Dict[str, float]] = {}
    for p in d.projects.values():
        for (c, _k), h in p.demand.items():
            out.setdefault(c, {"demand": 0.0, "available": 0.0})["demand"] += h
    for pl in d.pools.values():
        out.setdefault(pl.capability, {"demand": 0.0, "available": 0.0})["available"] += \
            sum(pl.available_hours(t) for t in d.months)
    return dict(sorted(out.items()))


def capacity_by_type(b: AnalysisBundle) -> Dict[str, Dict[str, float]]:
    out: Dict[str, Dict[str, float]] = {}
    for r in b.capacity["rows"]:
        k = "shared" if r["type"] == "Shared" else "restricted"
        row = out.setdefault(r["capability"], {"shared_allocated": 0.0, "shared_unused": 0.0,
                                               "restricted_allocated": 0.0, "restricted_unused": 0.0})
        row[f"{k}_allocated"] += r["allocated"]
        row[f"{k}_unused"] += r["unused"]
    return dict(sorted(out.items()))


def fungibility_ladder(b: AnalysisBundle) -> List[tuple]:
    labels = [("strict", "Strict ring-fencing"), ("committed", "Committed (approved transfers)"),
              ("conditional", "Conditional (pending approvals + 20% release)"),
              ("unconstrained", "Unconstrained (analytical)")]
    return [(lab, b.tier_metrics[k]["net_value"]) for k, lab in labels if b.tier_metrics[k].get("feasible")]


def total_budget(b: AnalysisBundle) -> float:
    return sum(x.amount for x in b.data.budgets)


def candidate_investment(b: AnalysisBundle) -> float:
    return sum(p.total_cost for p in b.data.projects.values())
