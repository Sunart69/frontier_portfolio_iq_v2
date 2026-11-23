"""Risk-adjusted portfolio comparison and recommendation.

Every candidate portfolio (Pareto alternatives, the validated baseline, the risk-aware stochastic plan,
leadership-shortlisted plans) is first evaluated by Monte Carlo on common random numbers. The ranking
is then fully traceable:

1. *Constraints* (leadership decision rules), e.g. ``within_budget >= 0.80``: a candidate that fails
   any constraint is not eligible; the margin of every constraint is reported.
2. *Criteria* are taken from the candidate's metrics (expected value, downside, budget and schedule
   confidence, alignment, OKR achievement, resource resilience, LOB commitment fulfilment) and put on
   fixed 0..1 scales: probabilities and shares as they are; dollar and score criteria relative to the
   best candidate (value / best). Fixed scales do not inflate small differences, as min-max would.
3. *Score* = sum of weight x normalized criterion; the weights come from the risk-preference profile.
4. *Risk-adjusted utility* (reported alongside, used as tie-break) = E[NPV] - lambda x CVaR, where
   CVaR is the expected NPV shortfall versus plan in the worst (1 - alpha) of futures.

This is **ex-post risk evaluation**: the candidates were produced by deterministic optimization and are
ranked under uncertainty. It does not by itself produce a mathematically risk-optimal portfolio; the
scenario-based stochastic MILP (`risk/stochastic.py`) is the risk-aware optimization, and its plan
competes here on the same simulated futures.
"""
from __future__ import annotations

import operator
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

OPS = {">=": operator.ge, "<=": operator.le, ">": operator.gt, "<": operator.lt}


def _p(r, key):
    return r["probabilities"][key]["p"]


# criterion -> (label, extractor(risk summary, alternative objectives, extra), scale)
# Scales keep comparisons on meaningful, fixed footings instead of stretching small differences:
#   "best"        value / best value among candidates (higher is better; monetary and score criteria)
#   "best_min"    best value / value (lower is better)
#   "probability" the probability itself (already 0..1)
#   "share"       the share itself (already 0..1)
CRITERIA: Dict[str, Tuple[str, Callable, str]] = {
    "expected_value": ("Expected business value ($K)", lambda r, o, x: r["value"]["mean"], "best"),
    "expected_npv": ("Expected NPV ($K)", lambda r, o, x: r["npv"]["mean"], "best"),
    "downside_risk": ("Downside protection: 1 - CVaR / expected NPV",
                      lambda r, o, x: max(0.0, 1 - r["var_cvar"]["cvar"] / max(abs(r["npv"]["mean"]), 1e-9)), "share"),
    "p10_npv": ("P10 NPV ($K)", lambda r, o, x: r["npv"]["p10"], "best"),
    "budget_confidence": ("P(within budget)", lambda r, o, x: _p(r, "within_budget"), "probability"),
    "schedule_confidence": ("P(meet key dates)", lambda r, o, x: _p(r, "meet_key_dates"), "probability"),
    "strategic_alignment": ("Strategic alignment (points)", lambda r, o, x: o.get("alignment", 0.0), "best"),
    "okr_achievement": ("Expected share of OKR targets met",
                        lambda r, o, x: (sum(v["p"] for v in r["okr_targets"].values()) / len(r["okr_targets"]))
                        if r["okr_targets"] else 1.0, "share"),
    "resource_resilience": ("1 - P(capability shortage)", lambda r, o, x: 1 - _p(r, "capability_shortage"), "probability"),
    "lob_commitment": ("LOB commitments fulfilled (share)", lambda r, o, x: x.get("lob_commitment", 0.0), "share"),
    "time_to_value": ("Value-weighted months to benefit", lambda r, o, x: o.get("time_to_value", 0.0), "best_min"),
    "min_value_probability": ("P(value >= minimum acceptable)", lambda r, o, x: _p(r, "min_acceptable_value"), "probability"),
}

# metrics usable in constraints (probabilities and distribution statistics)
CONSTRAINT_METRICS: Dict[str, Callable] = {
    "within_budget": lambda r, o, x: _p(r, "within_budget"),
    "meet_key_dates": lambda r, o, x: _p(r, "meet_key_dates"),
    "okr_plan_targets": lambda r, o, x: _p(r, "okr_plan_targets"),
    "no_capability_shortage": lambda r, o, x: 1 - _p(r, "capability_shortage"),
    "min_acceptable_value": lambda r, o, x: _p(r, "min_acceptable_value"),
    "expected_npv": lambda r, o, x: r["npv"]["mean"],
    "p10_npv": lambda r, o, x: r["npv"]["p10"],
    "cvar": lambda r, o, x: r["var_cvar"]["cvar"],
    "compliance_coverage": lambda r, o, x: x.get("compliance_coverage", 1.0),
}


@dataclass
class Constraint:
    metric: str
    op: str
    value: float
    label: str = ""

    def __post_init__(self):
        if self.metric not in CONSTRAINT_METRICS:
            raise ValueError(f"unknown constraint metric {self.metric!r}; use one of {sorted(CONSTRAINT_METRICS)}")
        if self.op not in OPS:
            raise ValueError(f"unknown operator {self.op!r}")

    def text(self) -> str:
        v = f"{self.value:.0%}" if 0 < abs(self.value) <= 1 and self.metric not in ("expected_npv", "p10_npv", "cvar") \
            else f"{self.value:,.0f}"
        return self.label or f"{self.metric.replace('_', ' ')} {self.op} {v}"


@dataclass
class Profile:
    name: str
    weights: Dict[str, float]
    lam: float                              # CVaR weight in the utility
    constraints: List[Constraint] = field(default_factory=list)
    description: str = ""
    # Eligibility floor: a candidate must deliver at least this share of the best candidate's expected
    # business value. Risk aversion should protect the downside, not recommend doing almost nothing.
    min_value_share: float = 0.0

    def __post_init__(self):
        bad = [k for k in self.weights if k not in CRITERIA]
        if bad:
            raise ValueError(f"unknown criteria {bad}; use {sorted(CRITERIA)}")
        tot = sum(self.weights.values())
        if tot <= 0:
            raise ValueError("profile weights must sum to a positive number")
        self.weights = {k: v / tot for k, v in self.weights.items()}


PROFILES: Dict[str, Profile] = {
    "risk_neutral": Profile("risk_neutral", {"expected_npv": 0.6, "expected_value": 0.2, "strategic_alignment": 0.1,
                                             "okr_achievement": 0.1}, 0.0,
                            description="Maximize expected value; downside risk is not penalized."),
    "balanced": Profile("balanced", {"expected_npv": 0.30, "downside_risk": 0.20, "budget_confidence": 0.15,
                                     "schedule_confidence": 0.15, "strategic_alignment": 0.10,
                                     "okr_achievement": 0.10}, 0.35,
                        description="Value first, with explicit weight on downside, budget and schedule confidence.",
                        min_value_share=0.6),
    "risk_averse": Profile("risk_averse", {"expected_npv": 0.15, "downside_risk": 0.35, "budget_confidence": 0.20,
                                           "schedule_confidence": 0.20, "resource_resilience": 0.10}, 0.70,
                           description="Protect the downside: confidence and resilience outweigh expected value, "
                                       "among plans that keep at least 60% of the best expected value.",
                           min_value_share=0.6),
}

RULE_SETS: Dict[str, List[Constraint]] = {
    "npv_budget80": [Constraint("within_budget", ">=", 0.80, "P(within budget) >= 80%")],
    "okr_dates85": [Constraint("meet_key_dates", ">=", 0.85, "P(meet committed dates) >= 85%")],
    "mandatory": [Constraint("compliance_coverage", ">=", 1.0, "all mandatory/compliance initiatives included")],
    "downside_protected": [Constraint("min_acceptable_value", ">=", 0.90,
                                      "P(value >= minimum acceptable) >= 90%")],
}


def profile_from_config(raw: Dict) -> Profile:
    cons = [Constraint(str(c["metric"]), str(c.get("op", ">=")), float(c["value"]), str(c.get("label", "")))
            for c in raw.get("constraints") or []]
    for rs in raw.get("rule_sets") or []:
        cons += RULE_SETS[rs]
    if raw.get("base") in PROFILES and not raw.get("weights"):
        b = PROFILES[raw["base"]]
        return Profile(raw.get("name", "custom"), dict(b.weights), float(raw.get("lambda", b.lam)), cons + b.constraints,
                       raw.get("description", b.description), float(raw.get("min_value_share", b.min_value_share)))
    return Profile(raw.get("name", "custom"), dict(raw["weights"]), float(raw.get("lambda", 0.35)), cons,
                   raw.get("description", "Custom leadership profile"), float(raw.get("min_value_share", 0.0)))


@dataclass
class Candidate:
    alt_id: str
    name: str
    method: str
    objectives: Dict[str, float]
    risk: Dict
    extra: Dict[str, float] = field(default_factory=dict)


@dataclass
class Ranking:
    profile: Profile
    rows: List[Dict]
    recommended: Optional[str]
    rationale: List[str]
    eligible: List[str]
    note: str = ""

    def to_dict(self):
        return {"profile": {"name": self.profile.name, "weights": self.profile.weights, "lambda": self.profile.lam,
                            "constraints": [c.text() for c in self.profile.constraints],
                            "description": self.profile.description},
                "rows": self.rows, "recommended": self.recommended, "rationale": self.rationale,
                "eligible": self.eligible, "note": self.note, "min_value_share": self.profile.min_value_share,
                "method": "ex-post risk evaluation of deterministic and risk-aware candidates on common random numbers"}


def utility(risk: Dict, lam: float) -> float:
    return risk["npv"]["mean"] - lam * risk["var_cvar"]["cvar"]


def rank(candidates: List[Candidate], profile: Profile) -> Ranking:
    if not candidates:
        return Ranking(profile, [], None, ["No candidates"], [])
    raw = {c.alt_id: {k: CRITERIA[k][1](c.risk, c.objectives, c.extra) for k in CRITERIA} for c in candidates}
    norm = {c.alt_id: {} for c in candidates}
    for k, (_label, _fn, scale) in CRITERIA.items():
        vals = [raw[c.alt_id][k] for c in candidates]
        best_max, best_min = max(vals), min(vals)
        for c in candidates:
            v = raw[c.alt_id][k]
            if scale == "best":
                x = v / best_max if best_max > 0 else (1.0 if v >= best_max else 0.0)
            elif scale == "best_min":
                x = best_min / v if v > 0 else 1.0
            else:
                x = v
            norm[c.alt_id][k] = float(min(1.0, max(0.0, x)))
    best_value = max(c.risk["value"]["mean"] for c in candidates)
    rows = []
    for c in candidates:
        checks = []
        if profile.min_value_share > 0:
            share = c.risk["value"]["mean"] / best_value if best_value > 0 else 1.0
            checks.append({"constraint": f"expected value >= {profile.min_value_share:.0%} of the best plan",
                           "value": share, "passed": bool(share >= profile.min_value_share - 1e-9),
                           "margin": share - profile.min_value_share})
        for con in profile.constraints:
            v = CONSTRAINT_METRICS[con.metric](c.risk, c.objectives, c.extra)
            ok = OPS[con.op](v + (1e-9 if con.op in (">=", ">") else -1e-9), con.value)
            checks.append({"constraint": con.text(), "value": v, "passed": bool(ok),
                           "margin": (v - con.value) if con.op in (">=", ">") else (con.value - v)})
        contrib = {k: profile.weights.get(k, 0.0) * norm[c.alt_id][k] for k in CRITERIA if profile.weights.get(k)}
        rows.append({"alt_id": c.alt_id, "name": c.name, "method": c.method,
                     "criteria": {k: {"label": CRITERIA[k][0], "raw": raw[c.alt_id][k], "normalized": norm[c.alt_id][k],
                                      "weight": profile.weights.get(k, 0.0),
                                      "contribution": contrib.get(k, 0.0)} for k in CRITERIA},
                     "score": sum(contrib.values()), "utility": utility(c.risk, profile.lam),
                     "constraints": checks, "eligible": all(ch["passed"] for ch in checks)})
    rows.sort(key=lambda r: (not r["eligible"], -r["score"], -r["utility"], r["alt_id"]))
    for k, r in enumerate(rows, 1):
        r["rank"] = k
    eligible = [r["alt_id"] for r in rows if r["eligible"]]
    note = ""
    if eligible:
        best = rows[0]
    else:
        best = max(rows, key=lambda r: (min((ch["margin"] for ch in r["constraints"]), default=0.0), r["score"]))
        note = ("No candidate meets every leadership constraint; the closest candidate is shown "
                "(smallest worst-case constraint shortfall). Relax a rule or add capacity/budget.")
    rationale = _rationale(best, rows, profile)
    return Ranking(profile, rows, best["alt_id"], rationale, eligible, note)


def _rationale(best, rows, profile) -> List[str]:
    out = []
    top = sorted(best["criteria"].items(), key=lambda kv: -kv[1]["contribution"])[:3]
    out.append(f"Highest weighted score under the '{profile.name.replace('_', ' ')}' profile "
               f"({best['score']:.2f}); strongest on " + ", ".join(v["label"] for _k, v in top if v["weight"]) + ".")
    if best["constraints"]:
        out.append("Constraints: " + "; ".join(f"{c['constraint']} ({'met' if c['passed'] else 'NOT met'}, "
                                                f"value {c['value']:.2f})" for c in best["constraints"]))
    others = [r for r in rows if r["alt_id"] != best["alt_id"]][:2]
    for o in others:
        diff = sorted(((k, best["criteria"][k]["raw"] - o["criteria"][k]["raw"]) for k in CRITERIA
                       if profile.weights.get(k)), key=lambda kv: -abs(kv[1]))[:2]
        out.append(f"Versus {o['name'] or o['alt_id']}: " + ", ".join(
            f"{CRITERIA[k][0]} {'+' if d >= 0 else ''}{d:,.2f}" for k, d in diff) + ".")
    return out


# --------------------------------------------------------------------------- the four strategic choices
def strategic_choices(candidates: List[Candidate], rankings: Dict[str, Ranking], value_floor: float = 0.7
                      ) -> Dict[str, Dict[str, str]]:
    """Aggressive = highest expected NPV; Balanced = recommendation under the balanced profile;
    Conservative = lowest CVaR among plans keeping >= `value_floor` of the aggressive plan's expected
    value; Accelerated = earliest value (lowest value-weighted months to benefit) under the same floor."""
    if not candidates:
        return {}
    by = {c.alt_id: c for c in candidates}
    aggressive = max(candidates, key=lambda c: (c.risk["npv"]["mean"], c.alt_id))
    floor = value_floor * aggressive.risk["value"]["mean"]
    viable = [c for c in candidates if c.risk["value"]["mean"] >= floor] or candidates
    conservative = min(viable, key=lambda c: (c.risk["var_cvar"]["cvar"], -c.risk["npv"]["mean"]))
    accelerated = min(viable, key=lambda c: (c.objectives.get("time_to_value", 1e9), c.risk["completion"]["mean"]))
    bal_id = rankings["balanced"].recommended if "balanced" in rankings else aggressive.alt_id
    out = {
        "aggressive": {"alt_id": aggressive.alt_id, "title": "Aggressive", "rule": "highest expected NPV"},
        "balanced": {"alt_id": bal_id, "title": "Balanced", "rule": "best risk-adjusted score (balanced profile)"},
        "conservative": {"alt_id": conservative.alt_id, "title": "Conservative",
                         "rule": f"lowest CVaR keeping >= {value_floor:.0%} of the aggressive plan's expected value"},
        "accelerated": {"alt_id": accelerated.alt_id, "title": "Accelerated delivery",
                        "rule": f"earliest value delivery keeping >= {value_floor:.0%} of the aggressive plan's value"},
    }
    for v in out.values():
        v["name"] = by[v["alt_id"]].name if v["alt_id"] in by else v["alt_id"]
    return out
