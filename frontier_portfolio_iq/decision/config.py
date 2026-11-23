"""Decision-intelligence configuration file (YAML) and the decision workbook export.

Example (config/decision.yaml):

    pareto:      {primary: value, secondary: [risk, time_to_value], grid_points: 4, refine_rounds: 2,
                  weighted_step: 0.5, time_limit: 10}
    objectives:  {enabled: [value, npv, alignment, okr, acceleration, compliance, cost, risk, overload,
                            time_to_value, diversification],
                  overrides: {risk: {normalization: minmax}},
                  custom: [{key: nps, name: Customer NPS uplift, direction: max, unit: points,
                            expression: "attributes['nps_uplift']"}]}
    simulation:  {iterations: 10000, mode: fixed, seed: 20270101, workers: 1}
    recommendation: {profile: balanced, rule_sets: [npv_budget80], constraints: [], value_floor: 0.7,
                     custom_profile: {name: cfo, base: balanced, lambda: 0.5, min_value_share: 0.6}}
    stochastic:  {scenarios: 40, lam: 0.35, alpha: 0.9, budget_confidence: null}
    nsga:        {pop_size: 24, generations: 12}           # omit to disable
    whatif:      {run: true, iterations: 2000, scenarios: [{key: hiring_freeze, name: Hiring freeze,
                  shared_capacity: 0.9, capability_month: [[AI Engineering, 1, 6, 0.7]]}]}
"""
from __future__ import annotations

import dataclasses
from typing import Dict, Optional

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

from ..pareto import ParetoConfig
from ..pareto.objectives import DEFAULT_ENABLED, ObjectiveSpec
from ..risk.stochastic import StochasticConfig
from .recommend import PROFILES, RULE_SETS, Constraint, profile_from_config


def load_decision_config(path: Optional[str] = None, **overrides):
    from ..intelligence import DecisionConfig
    from ..whatif import WhatIf
    raw: Dict = {}
    if path:
        import yaml
        with open(path, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    cfg = DecisionConfig()
    p = raw.get("pareto") or {}
    if p:
        if "secondary" in p:
            p["secondary"] = tuple(p["secondary"])
        if "weighted_keys" in p:
            p["weighted_keys"] = tuple(p["weighted_keys"])
        cfg.pareto = dataclasses.replace(cfg.pareto, **p)
    o = raw.get("objectives") or {}
    if o.get("enabled"):
        cfg.objectives_enabled = tuple(o["enabled"])
    cfg.objective_overrides = o.get("overrides") or {}
    for c in o.get("custom") or []:
        cfg.custom_objectives.append(ObjectiveSpec(
            key=str(c["key"]), name=str(c.get("name", c["key"])), direction=str(c.get("direction", "max")),
            unit=str(c.get("unit", "points")), formula=f"sum sel_i * ({c['expression']})",
            normalization=str(c.get("normalization", "minmax")), expression=str(c["expression"]), builtin=False))
        if c["key"] not in cfg.objectives_enabled:
            pass
    s = raw.get("simulation") or {}
    if "iterations" in s:
        cfg.iterations = int(s["iterations"])
    rec = raw.get("recommendation") or {}
    cfg.primary_profile = rec.get("profile", cfg.primary_profile)
    rules = [Constraint(str(c["metric"]), str(c.get("op", ">=")), float(c["value"]), str(c.get("label", "")))
             for c in rec.get("constraints") or []]
    for rs in rec.get("rule_sets") or []:
        rules += RULE_SETS[rs]
    cfg.rules = rules
    if rec.get("value_floor") is not None:
        cfg.value_floor = float(rec["value_floor"])
    if rec.get("custom_profile"):
        cfg.custom_profile = profile_from_config(rec["custom_profile"])
        if cfg.primary_profile == "custom":
            cfg.primary_profile = cfg.custom_profile.name
    if cfg.primary_profile not in PROFILES and (cfg.custom_profile is None or cfg.custom_profile.name != cfg.primary_profile):
        raise ValueError(f"unknown profile {cfg.primary_profile!r}")
    st = raw.get("stochastic", {})
    cfg.stochastic = None if st is False else dataclasses.replace(StochasticConfig(), **(st or {}))
    ns = raw.get("nsga")
    if ns:
        from ..pareto.nsga import NsgaConfig
        cfg.nsga = NsgaConfig(**ns)
    w = raw.get("whatif") or {}
    cfg.run_whatifs = bool(w.get("run", True))
    if "iterations" in w:
        cfg.whatif_iterations = int(w["iterations"])
    if w.get("scenarios"):
        from ..whatif import scenario_set
        items = []
        for sc in w["scenarios"]:
            sc = dict(sc)
            if "capability_month" in sc:
                sc["capability_month"] = [tuple(x) for x in sc["capability_month"]]
            if "start_windows" in sc:
                sc["start_windows"] = {k: tuple(v) for k, v in sc["start_windows"].items()}
            items.append(WhatIf(**sc))
        if w.get("replace_defaults"):
            cfg.whatifs = items
        else:
            cfg.extra_whatifs = items
    for k, v in overrides.items():
        if v is not None:
            setattr(cfg, k, v)
    return cfg, s


# --------------------------------------------------------------------------- workbook export
def write_decision_workbook(b, path: str) -> str:
    wb = Workbook()
    wb.remove(wb.active)
    hf, hfont = PatternFill("solid", fgColor="1F3A5F"), Font(color="FFFFFF", bold=True)

    def sheet(name, header, rows):
        ws = wb.create_sheet(name)
        ws.append(header)
        for r in rows:
            ws.append([("; ".join(map(str, v)) if isinstance(v, (list, tuple)) else v) for v in r])
        for c in ws[1]:
            c.fill, c.font = hf, hfont
        for col in ws.columns:
            ws.column_dimensions[col[0].column_letter].width = 16
        ws.freeze_panes = "A2"

    keys = [s.key for s in b.objectives.specs]
    sheet("Alternatives", ["Plan ID", "Name", "Method", "Status", "Pareto rank", "Initiatives", "Proof", "Gap",
                           "Validated", "Certified (exact)", "Model version", *[b.objectives.spec(k).name for k in keys],
                           "Selected"],
          [[a.alt_id, a.name, a.method, a.status, a.pareto_rank, len(a.starts), a.evidence.proof, a.evidence.gap,
            a.evidence.validated, a.evidence.certified, a.version.label(), *[a.objectives.get(k) for k in keys],
            " ".join(f"{i}@{b.data.settings.month_label(s)}" for i, s in sorted(a.starts.items()))]
           for a in b.frontier.alternatives])
    rows = []
    for aid, r in b.risk.items():
        p = r["probabilities"]
        rows.append([aid, b.alt(aid).name, r["iterations"], r["mode"], r["npv"]["mean"], r["npv"]["p10"], r["npv"]["p50"],
                     r["npv"]["p90"], r["value"]["mean"], r["cost"]["mean"], r["roi"]["mean"], r["completion"]["mean"],
                     p["within_budget"]["p"], p["lob_budget_breach"]["p"], p["meet_key_dates"]["p"],
                     p["okr_plan_targets"]["p"], p["capability_shortage"]["p"], p["min_acceptable_value"]["p"],
                     r["var_cvar"]["var"], r["var_cvar"]["cvar"], r["convergence"]["converged"],
                     r["convergence"]["se_mean_npv"]])
    sheet("Monte Carlo", ["Plan ID", "Name", "Iterations", "Mode", "E[NPV] $K", "P10 NPV", "P50 NPV", "P90 NPV",
                          "E[value] $K", "E[cost] $K", "E[ROI]", "E[completion month]", "P(within budget)",
                          "P(LOB budget breach)", "P(meet key dates)", "P(plan OKR targets met)",
                          "P(capability shortage)", "P(value >= minimum)", "VaR $K", "CVaR $K", "Converged",
                          "SE of E[NPV]"], rows)
    rk = []
    for prof, r in b.rankings.items():
        for row in r.rows:
            rk.append([prof, row["rank"], row["alt_id"], row["name"], row["eligible"], row["score"], row["utility"],
                       "; ".join(f"{c['constraint']}={c['value']:.3f} ({'ok' if c['passed'] else 'FAIL'})"
                                 for c in row["constraints"]),
                       *[row["criteria"][k]["raw"] for k in row["criteria"]]])
    first = next(iter(b.rankings.values())).rows[0]["criteria"] if b.rankings else {}
    sheet("Rankings", ["Profile", "Rank", "Plan ID", "Name", "Eligible", "Score", "Utility", "Constraints",
                       *[v["label"] for v in first.values()]], rk)
    sheet("Strategic choices", ["Choice", "Plan ID", "Name", "Rule"],
          [[v["title"], v["alt_id"], v["name"], v["rule"]] for v in b.choices.values()])
    sheet("What-if", ["Scenario", "Description", "Status", "Best plan", "E[NPV]", "P(within budget)",
                      "Baseline recommendation feasible", "Baseline rec. E[NPV]", "Model version"],
          [[o.whatif.name, o.whatif.description, o.status, (o.recommended or {}).get("name"),
            (o.recommended or {}).get("expected_npv"), (o.recommended or {}).get("within_budget"),
            (o.baseline_plan or {}).get("feasible"), (o.baseline_plan or {}).get("expected_npv"), o.version.label()]
           for o in b.scenarios])
    sheet("Solver confidence", ["Report", "Engine", "Role", "Status", "Proof", "Objective", "Bound", "Gap",
                                "Validated", "Seconds"],
          [[rep.label, r.result.engine, r.role, r.result.status.value, r.result.proof, r.result.objective,
            r.result.bound, r.result.gap, None if r.validation is None else r.validation.feasible, r.result.runtime]
           for rep in [b.confidence] + ([b.recommended_confidence] if b.recommended_confidence else []) for r in rep.runs])
    sheet("Assumptions", ["Item", "Value"],
          [["Calibrated", b.uncertainty.calibrated], *[["Note", n] for n in b.uncertainty.assumptions],
           *[[f"Driver {d.driver_id}", f"{d.dist.describe()} on {d.effect} ({d.mode}) for {d.applies_to}"]
             for d in b.uncertainty.drivers],
           *[[f"Simulation {k}", v] for k, v in dataclasses.asdict(b.uncertainty.settings).items()]])
    # the numbers behind every spider (radar) chart in the dashboard and the CEO deck, one row per spoke
    from ..radar import radar_profiles
    rd = radar_profiles(b)
    titles = {}
    for k, c in b.choices.items():
        titles.setdefault(c["alt_id"], []).append(c["title"])
    nm = lambda aid: (b.alt(aid).name if aid and b.frontier.get(aid) else aid) or aid
    spider = []
    for fam, axes, src in (("Risk trade-off (simulated)", rd["risk_axes"], rd["risk"]),
                           ("Risk trade-off, adaptive execution (simulated)", rd["risk_axes"], rd["risk_adaptive"]),
                           ("Optimization KPIs (proven)", rd["kpi_axes"], rd["kpi"])):
        for aid, vals in src.items():
            for a in axes:
                if a["key"] in vals:
                    spider.append([fam, aid, nm(aid), " = ".join(titles.get(aid, [])), a["label"], vals[a["key"]],
                                   a["definition"]])
    for key, v in rd["scenarios"].items():
        for a in rd["scenario_axes"]:
            spider.append(["What-if (simulated)", v["alt_id"], v["name"], "", a["label"], v["values"][a["key"]],
                           a["definition"]])
    for prof, v in rd["profiles"].items():
        for c in rd["profile_axes"]:
            spider.append(["Risk appetite weights", v["recommended"], prof.replace("_", " "), "", c.replace("_", " "),
                           v["weights"][c], "weight in the profile's score (weights sum to 1)"])
    sheet("Spider views", ["Spider", "Plan ID", "Plan / scenario / profile", "Strategic choice", "Spoke",
                           "Value (0-1, 1 = best)", "Definition"], spider)
    wb.save(path)
    return path
