"""Radar (spider) profiles shared by the decision dashboard and the CEO deck.

Every radar axis is on a fixed 0-1 scale with 1 = best, so shapes can be compared across plans and
scenarios without rescaling. Three families:

* **Risk trade-off** (simulated): eight probabilities and downside measures from the Monte Carlo summary.
  Absolute scales — a probability is shown as itself, never stretched relative to other plans.
* **Optimization KPIs** (proven): every frontier objective normalized from the nadir (0, worst efficient
  value seen) to the ideal (1, best value any single-objective optimum reached), direction-aware.
* **What-if** (simulated): each scenario's recommended plan on return and risk axes. Monetary axes are
  relative to the best scenario; probabilities are absolute.

The dashboard and the deck read these numbers from ``radar_profiles`` so they always agree, and the deck
manifest records them so the tests can reconcile the two.
"""
from __future__ import annotations

from typing import Dict, List, Optional

# key, label, definition
RISK_AXES = [
    ("budget_confidence", "Budget confidence", "P(total cost within plan + reserve)"),
    ("lob_budgets", "LOB budgets held", "1 - P(any LOB over its budget)"),
    ("committed_dates", "Committed dates met", "P(every deadline and commitment met within grace)"),
    ("okr_targets", "OKR targets held", "P(the OKR targets the plan meets stay met)"),
    ("capacity", "Capacity sufficient", "1 - P(capability shortage in any month)"),
    ("value_floor", "Value floor met", "P(value >= minimum acceptable)"),
    ("downside_protection", "Downside protection", "1 - CVaR / expected NPV"),
    ("value_certainty", "Value certainty", "P10 NPV / expected NPV"),
]

SCENARIO_AXES = [
    ("expected_npv", "Expected NPV", "expected NPV / best scenario's expected NPV"),
    ("p10_npv", "P10 NPV", "P10 NPV / best scenario's P10 NPV"),
    ("budget_confidence", "Budget confidence", "P(total cost within plan + reserve)"),
    ("committed_dates", "Committed dates met", "P(every deadline and commitment met within grace)"),
    ("capacity", "Capacity sufficient", "1 - P(capability shortage in any month)"),
    ("downside_protection", "Downside protection", "1 - CVaR / expected NPV"),
    ("value_certainty", "Value certainty", "P10 NPV / expected NPV"),
]

# short labels for the objective registry (custom objectives fall back to their own name)
KPI_SHORT = {"value": "Business value", "npv": "NPV", "alignment": "Strategic alignment", "okr": "OKR coverage",
             "acceleration": "Acceleration", "compliance": "Compliance", "cost": "Cost", "risk": "Execution risk",
             "overload": "Peak load", "time_to_value": "Time to value", "diversification": "Diversification"}

PROFILE_AXES = ["expected_npv", "downside_risk", "budget_confidence", "schedule_confidence", "strategic_alignment",
                "okr_achievement", "resource_resilience", "time_to_value"]


def _clip(v: float) -> float:
    return max(0.0, min(1.0, float(v)))


def _ratio(num: float, den: float) -> float:
    return _clip(num / den) if den and den > 1e-9 else 0.0


def risk_profile(r: Dict) -> Dict[str, float]:
    """Risk trade-off axes (0-1, 1 = best) from a Monte Carlo summary."""
    p = r["probabilities"]
    mean = r["npv"]["mean"]
    return {
        "budget_confidence": _clip(p["within_budget"]["p"]),
        "lob_budgets": _clip(1 - p["lob_budget_breach"]["p"]),
        "committed_dates": _clip(p["meet_key_dates"]["p"]),
        "okr_targets": _clip(p["okr_plan_targets"]["p"]),
        "capacity": _clip(1 - p["capability_shortage"]["p"]),
        "value_floor": _clip(p["min_acceptable_value"]["p"]),
        "downside_protection": _clip(1 - r["var_cvar"]["cvar"] / mean) if mean > 1e-9 else 0.0,
        "value_certainty": _ratio(r["npv"]["p10"], mean),
    }


def kpi_profile(frontier, alt) -> Dict[str, float]:
    """Optimization KPI axes: each objective normalized nadir (0) to ideal (1)."""
    return {k: _clip(v) for k, v in frontier.normalized(alt).items()}


def scenario_profile(brief: Dict, best_npv: float, best_p10: float) -> Dict[str, float]:
    mean = brief["expected_npv"]
    return {
        "expected_npv": _ratio(mean, best_npv),
        "p10_npv": _ratio(brief["p10_npv"], best_p10),
        "budget_confidence": _clip(brief["within_budget"]),
        "committed_dates": _clip(brief["meet_key_dates"]),
        "capacity": _clip(1 - brief["capability_shortage"]),
        "downside_protection": _clip(1 - brief["cvar"] / mean) if mean > 1e-9 else 0.0,
        "value_certainty": _ratio(brief["p10_npv"], mean),
    }


TIE_TOLERANCE = 0.01   # differences within 0.01 on a 0-1 axis (one percentage point) are treated as ties


def _leaders(series: Dict[str, Dict[str, float]], axes: List[str],
             tol: float = TIE_TOLERANCE) -> Dict[str, Optional[str]]:
    """Axis -> the single series that is best on it by more than ``tol``, or None when it is a tie
    (simulated probabilities carry sampling error, so hair-thin leads are not leads)."""
    out = {}
    for a in axes:
        vals = {s: v.get(a) for s, v in series.items() if v.get(a) is not None}
        if not vals:
            out[a] = None
            continue
        top = max(vals.values())
        best = [s for s, v in vals.items() if v >= top - tol]
        out[a] = best[0] if len(best) == 1 else None
    return out


def radar_profiles(bundle) -> Dict:
    """All radar data for one DecisionBundle (JSON-ready)."""
    fr = bundle.frontier
    kpi_keys = [s.key for s in bundle.objectives.specs if s.key in fr.ideal]
    kpi_axes = [(s.key, KPI_SHORT.get(s.key, s.name), f"{s.name} ({s.direction}), nadir = 0, ideal = 1")
                for s in bundle.objectives.specs if s.key in fr.ideal]
    risk = {aid: risk_profile(r) for aid, r in bundle.risk.items()}
    risk_ad = {aid: risk_profile(r) for aid, r in bundle.risk_adaptive.items()}
    kpi = {}
    for a in fr.alternatives:
        if a.alt_id in bundle.risk or a.status == "efficient":
            kpi[a.alt_id] = kpi_profile(fr, a)
    scen = {}
    briefs = []                                # (key, name, alt_id, brief): today's baseline first, then what-ifs
    rec = bundle.recommended_id
    if rec and rec in bundle.risk:
        r = bundle.risk[rec]
        briefs.append(("baseline", "Baseline", rec, {
            "expected_npv": r["npv"]["mean"], "p10_npv": r["npv"]["p10"],
            "within_budget": r["probabilities"]["within_budget"]["p"],
            "meet_key_dates": r["probabilities"]["meet_key_dates"]["p"],
            "capability_shortage": r["probabilities"]["capability_shortage"]["p"], "cvar": r["var_cvar"]["cvar"]}))
    for s in bundle.scenarios:
        if s.recommended and s.recommended.get("expected_npv") is not None:
            briefs.append((s.whatif.key, s.whatif.name, s.recommended.get("alt_id"), s.recommended))
    if briefs:
        best_npv = max(b["expected_npv"] for *_, b in briefs)
        best_p10 = max(b["p10_npv"] for *_, b in briefs)
        scen = {k: {"name": n, "alt_id": a, "values": scenario_profile(b, best_npv, best_p10)}
                for k, n, a, b in briefs}
    choices = {k: c["alt_id"] for k, c in bundle.choices.items()}
    plans = sorted(set(choices.values()))      # distinct plans: coinciding choices are one plan, not a tie
    by_plan_risk = {a: risk[a] for a in plans if a in risk}
    by_plan_kpi = {a: kpi[a] for a in plans if a in kpi}
    profiles = {name: {"weights": {c: rk.profile.weights.get(c, 0.0) for c in PROFILE_AXES},
                       "recommended": rk.recommended, "lambda": rk.profile.lam}
                for name, rk in bundle.rankings.items()}
    # the shortlisted plan with the best average risk score: a reference for how much safety is available
    safest = max(risk, key=lambda a: (sum(risk[a].values()) / len(risk[a]), a)) if risk else None
    return {
        "scale": "0-1 on every axis, 1 = best", "tie_tolerance": TIE_TOLERANCE,
        "safest": safest, "safest_score": (sum(risk[safest].values()) / len(risk[safest])) if safest else None,
        "risk_axes": [{"key": k, "label": l, "definition": d} for k, l, d in RISK_AXES],
        "kpi_axes": [{"key": k, "label": l, "definition": d} for k, l, d in kpi_axes],
        "scenario_axes": [{"key": k, "label": l, "definition": d} for k, l, d in SCENARIO_AXES],
        "profile_axes": PROFILE_AXES,
        "risk": risk, "risk_adaptive": risk_ad, "kpi": kpi, "scenarios": scen, "profiles": profiles,
        "choices": choices,
        "leaders": {"risk": _leaders(by_plan_risk, [k for k, _, _ in RISK_AXES]),   # axis -> alt_id or None (tie)
                    "kpi": _leaders(by_plan_kpi, kpi_keys)},
    }
