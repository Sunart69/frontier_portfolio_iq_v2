"""End-to-end decision-intelligence pipeline.

  canonical model  ->  validated optimization (HiGHS + SCIP + CP-SAT)  ->  Pareto frontier
  (epsilon-constraint, weighted sum, optional NSGA-II, risk-aware stochastic MILP)  ->  Monte Carlo on
  common random numbers (fixed plan; adaptive for the strategic choices)  ->  risk-adjusted ranking
  under leadership profiles and decision rules  ->  four strategic choices  ->  what-if scenarios.

Every stage is stamped with the same model version (fingerprint of the portfolio + uncertainty model),
and the `DecisionBundle` is the single source for the dashboard, the CEO deck and the JSON export.
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
import json
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .cache import ResultCache
from .canonical import ModelVersion, fingerprint
from .decision.recommend import (PROFILES, Candidate, Constraint, Profile, Ranking, rank, strategic_choices)
from .domain import PortfolioData, PortfolioTier
from .optimizer import SolveOptions
from .pareto import ParetoConfig, ParetoEngine, objective_set
from .pareto.frontier import Frontier, PortfolioAlternative
from .pareto.objectives import DEFAULT_ENABLED, ObjectiveSpec
from .risk import default_uncertainty, evaluate_plan
from .risk.stochastic import StochasticConfig, risk_aware_plan
from .risk.uncertainty import UncertaintyModel
from .solvers import SolverConfidenceReport, SolverSettings, engine_inventory, solve_validated
from .whatif import WhatIf, scenario_set


@dataclass
class DecisionConfig:
    pareto: ParetoConfig = field(default_factory=ParetoConfig)
    objectives_enabled: tuple = DEFAULT_ENABLED
    objective_overrides: Dict[str, Dict] = field(default_factory=dict)
    custom_objectives: List[ObjectiveSpec] = field(default_factory=list)
    iterations: int = 10000
    adaptive_for_choices: bool = True
    shortlist: int = 10
    primary_profile: str = "balanced"
    custom_profile: Optional[Profile] = None
    rules: List[Constraint] = field(default_factory=list)
    stochastic: Optional[StochasticConfig] = field(default_factory=StochasticConfig)
    nsga: Optional[object] = None                    # pareto.nsga.NsgaConfig to enable NSGA-II
    validators: tuple = ("SCIP", "CP-SAT")
    whatifs: Optional[List[WhatIf]] = None           # None = baseline/optimistic/constrained/adverse set
    extra_whatifs: List[WhatIf] = field(default_factory=list)   # added to the default set
    run_whatifs: bool = True
    whatif_iterations: int = 2000
    whatif_pareto: ParetoConfig = field(default_factory=lambda: ParetoConfig(grid_points=3, refine_rounds=0,
                                                                            weighted_step=0, max_points=12))
    value_floor: float = 0.7
    cache_dir: Optional[str] = None
    time_limit: Optional[float] = None


@dataclass
class ScenarioOutcome:
    whatif: WhatIf
    version: ModelVersion
    status: str
    recommended: Optional[Dict] = None
    baseline_plan: Optional[Dict] = None       # the baseline recommendation re-evaluated in this scenario
    frontier_size: int = 0
    efficient: int = 0
    note: str = ""

    def to_dict(self):
        return {"key": self.whatif.key, "name": self.whatif.name, "description": self.whatif.description,
                "kind": self.whatif.kind, "levers": {k: (v if not isinstance(v, tuple) else list(v))
                                                     for k, v in self.whatif.levers().items()},
                "model_version": self.version.label(), "status": self.status, "recommended": self.recommended,
                "baseline_plan": self.baseline_plan, "frontier_size": self.frontier_size,
                "efficient": self.efficient, "note": self.note}


@dataclass
class DecisionBundle:
    version: ModelVersion
    data: PortfolioData
    uncertainty: UncertaintyModel
    objectives: object
    baseline: object
    confidence: SolverConfidenceReport
    frontier: Frontier
    risk: Dict[str, Dict]                     # alt_id -> Monte Carlo summary (fixed plan)
    risk_adaptive: Dict[str, Dict]            # alt_id -> Monte Carlo summary (adaptive execution)
    rankings: Dict[str, Ranking]
    choices: Dict[str, Dict[str, str]]
    recommended_id: Optional[str]
    recommended_confidence: Optional[SolverConfidenceReport]
    stochastic: Dict
    scenarios: List[ScenarioOutcome]
    timings: Dict[str, float]
    cache: Dict[str, int]
    config: DecisionConfig
    engines: List[Dict]
    run_id: str = field(default_factory=lambda: _dt.datetime.now().strftime("%Y-%m-%d %H:%M"))
    analysis: object = None                   # v1 AnalysisBundle (tiers, capacity, LOB view) when attached

    def alt(self, alt_id: str) -> PortfolioAlternative:
        return self.frontier.get(alt_id)

    @property
    def recommended(self) -> Optional[PortfolioAlternative]:
        return self.alt(self.recommended_id) if self.recommended_id else None

    @property
    def candidates(self) -> List[str]:
        return list(self.risk)

    def consistency(self) -> List[str]:
        """Every artifact must come from this bundle's model version."""
        issues = []
        for a in self.frontier.alternatives:
            if not a.version.compatible(self.version):
                issues.append(f"{a.alt_id} from {a.version.label()}")
        if self.confidence.version.fingerprint != fingerprint(self.data):
            issues.append("solver confidence report from another portfolio model")
        return issues

    def to_dict(self) -> Dict:
        from .metrics import portfolio_metrics
        data = self.data
        alts = []
        for a in self.frontier.alternatives:
            d = a.to_dict()
            d["in_shortlist"] = a.alt_id in self.risk
            alts.append(d)
        projects = {i: {"name": p.name, "lob": p.primary_lob, "lob_name": data.lobs[p.primary_lob].name
                        if p.primary_lob in data.lobs else p.primary_lob, "value": p.value, "cost": p.total_cost,
                        "duration": p.duration, "mandatory": p.mandatory, "compliance": p.compliance,
                        "critical": p.critical, "risk": p.execution_risk, "confidence": p.delivery_confidence,
                        "depends_on": p.depends_on}
                    for i, p in data.projects.items()}
        pools = {k: {"capability": p.capability, "owner": p.owning_lob, "type": p.capacity_type.value,
                     "available": {t: p.available_hours(t) for t in data.months}} for k, p in data.pools.items()}
        um = self.uncertainty
        return {
            "schema": "fpiq-decision-intelligence/2.0", "run_id": self.run_id,
            "model_version": self.version.label(), "fingerprint": self.version.fingerprint,
            "horizon": data.settings.horizon, "first_month_label": data.settings.first_month_label,
            "months": [data.settings.month_label(t) for t in data.months],
            "lobs": {l: {"name": o.name, "enterprise": o.is_enterprise} for l, o in data.lobs.items()},
            "projects": projects, "pools": pools,
            "objectives": [s.to_dict() for s in self.objectives.specs],
            "objectives_skipped": self.objectives.skipped,
            "baseline": {"metrics": {k: v for k, v in portfolio_metrics(self.baseline).items()
                                     if k in ("net_value", "investment", "n_selected", "selected", "starts",
                                              "overall_utilization", "compliance_coverage")}},
            "solver_confidence": self.confidence.summary(),
            "recommended_confidence": self.recommended_confidence.summary() if self.recommended_confidence else None,
            "engines": self.engines,
            "frontier": {**{k: v for k, v in self.frontier.to_dict().items() if k != "alternatives"},
                         "alternatives": alts},
            "risk": self.risk, "risk_adaptive": self.risk_adaptive,
            "rankings": {k: r.to_dict() for k, r in self.rankings.items()},
            "choices": self.choices, "recommended": self.recommended_id,
            "stochastic": self.stochastic,
            "scenarios": [s.to_dict() for s in self.scenarios],
            "assumptions": {"calibrated": um.calibrated, "notes": um.assumptions,
                            "repaired_correlation": um.repaired_correlation,
                            "drivers": [{"id": d.driver_id, "name": d.name, "category": d.category,
                                         "distribution": d.dist.describe(), "effect": d.effect, "mode": d.mode,
                                         "magnitude": d.magnitude, "applies_to": d.applies_to, "source": d.source}
                                        for d in um.drivers],
                            "correlations": [{"a": a, "b": b, "rho": r} for (a, b), r in um.correlations.items()],
                            "projects": {i: {"effort": u.effort.describe(), "duration": u.duration.describe(),
                                             "benefit": u.benefit.describe(), "start_slip": u.start_slip.describe(),
                                             "failure_prob": u.failure_prob, "failure_loss": u.failure_loss,
                                             "source": u.source} for i, u in um.projects.items()},
                            "pools": {k: {"availability": u.availability.describe(),
                                          "hiring_delay": u.hiring_delay.describe(),
                                          "productivity": u.productivity.describe(), "source": u.source}
                                      for k, u in um.pools.items()},
                            "simulation": dataclasses.asdict(um.settings)},
            "timings": self.timings, "cache": self.cache, "consistency_issues": self.consistency(),
        }

    def write_json(self, path: str) -> str:
        with open(path, "w") as fh:
            json.dump(self.to_dict(), fh, indent=1, default=_json_default)
        return path


def _json_default(o):
    import numpy as np
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.ndarray,)):
        return o.tolist()
    if isinstance(o, (set, tuple)):
        return list(o)
    return str(o)


# --------------------------------------------------------------------------- helpers
def _extra_metrics(data: PortfolioData, alt: PortfolioAlternative) -> Dict[str, float]:
    sel = set(alt.starts)
    P = data.projects
    comp = [i for i, p in P.items() if p.compliance or p.mandatory]
    compliance = (sum(1 for i in comp if i in sel) / len(comp)) if comp else 1.0
    lobs = data.business_lobs()
    inv = {l: alt.lob_allocations.get(l, {}).get("investment", 0.0) for l in data.lobs}
    tot = sum(inv.values()) or 1.0
    ok = 0
    for l in lobs:
        share_ok = inv.get(l, 0.0) / tot >= data.settings.min_investment_share.get(l, 0.0) - 1e-9
        tgt_ok = True
        for t in data.targets:
            if t.lob_id == l and t.min_outcome:
                got = sum(b.attributed_benefit for b in data.benefits
                          if b.lob_id == l and b.kpi == t.kpi and b.project_id in sel)
                tgt_ok &= got >= t.min_outcome - 1e-6
        has = any(P[i].primary_lob == l for i in sel)
        ok += 1 if (share_ok and tgt_ok and has) else 0
    return {"compliance_coverage": compliance, "lob_commitment": ok / len(lobs) if lobs else 1.0}


def _shortlist(frontier: Frontier, k: int, keep: List[str]) -> List[PortfolioAlternative]:
    eff = [a for a in frontier.alternatives if a.status == "efficient"]
    chosen: List[PortfolioAlternative] = []
    for aid in keep:
        try:
            a = frontier.get(aid)
        except StopIteration:
            continue
        if a.evidence.validated and a not in chosen:
            chosen.append(a)
    for key in frontier.objective_keys:       # extremes of every frontier objective
        if not eff:
            break
        sign = frontier.signs[key]
        best = max(eff, key=lambda a: sign * a.objectives[key])
        if best not in chosen:
            chosen.append(best)
    rest = sorted([a for a in eff if a not in chosen], key=lambda a: a.objectives[frontier.objective_keys[0]])
    slots = max(0, k - len(chosen))
    if rest and slots:
        step = max(1, len(rest) / slots)
        picks = [rest[int(j * step)] for j in range(min(slots, len(rest)))]
        chosen += [a for a in picks if a not in chosen]
    return chosen[:max(k, len(keep))]


def _name(alt: PortfolioAlternative, objset) -> str:
    if alt.name:
        return alt.name
    if alt.method == "payoff":
        k = alt.provenance.get("optimize")
        return f"Best {objset.spec(k).name.lower()}" if k in objset.keys else "Payoff plan"
    return {"epsilon": "Trade-off", "epsilon-refine": "Trade-off", "weighted": "Weighted",
            "nsga2": "NSGA-II (approx.)"}.get(alt.method, alt.method.title()) + f" {alt.alt_id[-4:]}"


# --------------------------------------------------------------------------- main
def run_decision_intelligence(data: PortfolioData, um: Optional[UncertaintyModel] = None,
                              config: Optional[DecisionConfig] = None, scenario: str = "baseline",
                              cache: Optional[ResultCache] = None, analysis=None) -> DecisionBundle:
    cfg = config or DecisionConfig()
    um = um or default_uncertainty(data)
    um = um.with_settings(iterations=cfg.iterations)
    cache = cache or ResultCache(cfg.cache_dir)
    version = ModelVersion(fingerprint(data, um), scenario)
    T: Dict[str, float] = {}
    t0 = time.time()
    tl = cfg.time_limit or data.settings.time_limit
    settings = SolverSettings(time_limit=tl, mip_gap=data.settings.mip_gap, seed=um.settings.seed)
    # 1. validated baseline optimization
    base_opts = SolveOptions(tier=PortfolioTier.COMMITTED, label="Committed baseline")
    baseline, conf = cache.get_or_compute("optimize", [data, base_opts.__dict__, settings.__dict__, cfg.validators],
                                          lambda: solve_validated(data, base_opts, settings, validators=cfg.validators,
                                                                  scenario=scenario))
    if not baseline.feasible:
        raise RuntimeError("Baseline portfolio infeasible: " + "; ".join(baseline.diagnostics))
    T["optimize_validate"] = time.time() - t0
    # 2. Pareto frontier
    t1 = time.time()
    objset = objective_set(data, cfg.objectives_enabled, cfg.custom_objectives, cfg.objective_overrides)
    pcfg = cfg.pareto

    def build_frontier():
        eng = ParetoEngine(data, objset, pcfg, base_opts, scenario)
        eng.version = version
        eng.frontier.version = version
        eng.run(weighted=bool(pcfg.weighted_step))
        b = eng.add_plan(baseline, "baseline", "Committed baseline (validated)")
        stoch_info = {}
        if cfg.stochastic is not None:
            sres, stoch_info = risk_aware_plan(data, um, cfg.stochastic)
            if sres.feasible:
                s_alt = eng.add_plan(sres, "stochastic", "Risk-aware (stochastic MILP)",
                                     {"scenarios": cfg.stochastic.scenarios, "lambda": cfg.stochastic.lam})
                stoch_info["alt_id"] = s_alt.alt_id
        if cfg.nsga is not None:
            from .pareto.nsga import available, run_nsga2
            if available():
                run_nsga2(eng, cfg.nsga, seeds=[a.starts for a in eng.frontier.efficient()])
        eng._update_ideal_nadir()
        eng.frontier.rank()
        return eng.frontier, b.alt_id, stoch_info

    frontier, base_id, stoch = cache.get_or_compute(
        "frontier", [data, um.settings.seed, pcfg.to_dict(), [s.to_dict() for s in objset.specs],
                     cfg.stochastic.__dict__ if cfg.stochastic else None, cfg.nsga.__dict__ if cfg.nsga else None],
        build_frontier)
    for a in frontier.alternatives:
        a.name = _name(a, objset)
    T["pareto"] = time.time() - t1
    # 3. Monte Carlo on a shortlist (common random numbers)
    t2 = time.time()
    keep = [base_id] + ([stoch["alt_id"]] if stoch.get("alt_id") else [])
    short = _shortlist(frontier, cfg.shortlist, keep)
    risk: Dict[str, Dict] = {}
    for a in short:
        tier = PortfolioTier.COMMITTED
        risk[a.alt_id] = cache.get_or_compute(
            "simulate", [data, um, a.starts, "fixed"],
            lambda a=a, tier=tier: evaluate_plan(data, a.starts, um, "fixed", tier, a.result, keep_projects=True)[0])
    T["monte_carlo"] = time.time() - t2
    # 4. risk-adjusted rankings
    cands = [Candidate(a.alt_id, a.name, a.method, a.objectives, risk[a.alt_id], _extra_metrics(data, a)) for a in short]
    profiles = dict(PROFILES)
    if cfg.custom_profile is not None:
        profiles[cfg.custom_profile.name] = cfg.custom_profile
    rankings = {}
    for name, prof in profiles.items():
        if cfg.rules and name == cfg.primary_profile:
            prof = dataclasses.replace(prof, constraints=list(prof.constraints) + list(cfg.rules))
        rankings[name] = rank(cands, prof)
    choices = strategic_choices(cands, rankings, cfg.value_floor)
    rec_id = rankings[cfg.primary_profile].recommended
    # 5. adaptive execution for the strategic choices
    t3 = time.time()
    risk_ad: Dict[str, Dict] = {}
    if cfg.adaptive_for_choices:
        for aid in {c["alt_id"] for c in choices.values()} | ({rec_id} if rec_id else set()):
            a = frontier.get(aid)
            risk_ad[aid] = cache.get_or_compute(
                "simulate", [data, um, a.starts, "adaptive"],
                lambda a=a: evaluate_plan(data, a.starts, um, "adaptive", PortfolioTier.COMMITTED, a.result,
                                          keep_projects=False)[0])
    T["monte_carlo_adaptive"] = time.time() - t3
    # 6. independent certification of the recommended plan
    rec_conf = None
    if rec_id:
        a = frontier.get(rec_id)
        fixed = SolveOptions(tier=PortfolioTier.COMMITTED, fixed_starts=dict(a.starts), label=f"Recommended {rec_id}")
        _r, rec_conf = solve_validated(data, fixed, settings, validators=cfg.validators, scenario=scenario)
    # 7. what-if scenarios
    t4 = time.time()
    outcomes: List[ScenarioOutcome] = []
    if cfg.run_whatifs:
        for w in (cfg.whatifs if cfg.whatifs is not None else scenario_set(data) + list(cfg.extra_whatifs)):
            if w.kind == "baseline":
                continue
            outcomes.append(_run_whatif(data, um, w, cfg, cache, frontier.get(rec_id) if rec_id else None, objset))
    T["whatif"] = time.time() - t4
    T["total"] = time.time() - t0
    return DecisionBundle(version, data, um, objset, baseline, conf, frontier, risk, risk_ad, rankings, choices,
                          rec_id, rec_conf, stoch, outcomes, T, cache.stats(), cfg, engine_inventory(),
                          analysis=analysis)


def _risk_brief(r: Dict) -> Dict:
    return {"expected_npv": r["npv"]["mean"], "p10_npv": r["npv"]["p10"], "p90_npv": r["npv"]["p90"],
            "expected_value": r["value"]["mean"], "expected_cost": r["cost"]["mean"],
            "within_budget": r["probabilities"]["within_budget"]["p"],
            "meet_key_dates": r["probabilities"]["meet_key_dates"]["p"],
            "capability_shortage": r["probabilities"]["capability_shortage"]["p"],
            "cvar": r["var_cvar"]["cvar"], "completion_p50": r["completion"]["p50"]}


def _run_whatif(data, um, w: WhatIf, cfg: DecisionConfig, cache: ResultCache, base_rec, objset) -> ScenarioOutcome:
    d2, u2 = w.apply(data, um)
    u2 = u2.with_settings(iterations=cfg.whatif_iterations)
    version = ModelVersion(fingerprint(d2, u2), w.key)
    base_opts = SolveOptions(tier=PortfolioTier.COMMITTED, label=w.name)

    def run():
        from .optimizer import solve as legacy
        res = legacy(d2, base_opts)
        if not res.feasible:
            return ScenarioOutcome(w, version, "infeasible", note="; ".join(res.diagnostics[:3]))
        os2 = objective_set(d2, cfg.objectives_enabled, cfg.custom_objectives, cfg.objective_overrides)
        eng = ParetoEngine(d2, os2, dataclasses.replace(cfg.whatif_pareto, primary=cfg.pareto.primary,
                                                         secondary=cfg.pareto.secondary), base_opts, w.key)
        eng.version = version
        eng.frontier.version = version
        eng.run(weighted=False)
        eng.add_plan(res, "baseline", f"{w.name} baseline")
        eng.frontier.rank()
        short = _shortlist(eng.frontier, max(4, cfg.shortlist // 2), [])
        rk = {}
        for a in short:
            rk[a.alt_id] = evaluate_plan(d2, a.starts, u2, "fixed", PortfolioTier.COMMITTED, a.result,
                                         keep_projects=False)[0]
        cands = [Candidate(a.alt_id, _name(a, os2), a.method, a.objectives, rk[a.alt_id], _extra_metrics(d2, a))
                 for a in short]
        prof = PROFILES.get(w.risk_profile or cfg.primary_profile, PROFILES["balanced"])
        if w.objective_weights:
            prof = Profile(f"{w.key}-weights", dict(w.objective_weights), prof.lam, list(prof.constraints),
                           min_value_share=prof.min_value_share)
        ranking = rank(cands, prof)
        best = next(a for a in short if a.alt_id == ranking.recommended)
        out = ScenarioOutcome(w, version, "ok", frontier_size=len(eng.frontier.alternatives),
                              efficient=len(eng.frontier.efficient()))
        out.recommended = {"alt_id": best.alt_id, "name": _name(best, os2), "profile": prof.name,
                           "selected": best.selected, "objectives": best.objectives, **_risk_brief(rk[best.alt_id])}
        if base_rec is not None:
            # the baseline recommendation, re-evaluated under this scenario's assumptions
            from .solvers.compiled import compile_portfolio, to_portfolio_result
            from .solvers.milp import HighsEngine
            fx = compile_portfolio(d2, SolveOptions(tier=PortfolioTier.COMMITTED, fixed_starts=dict(base_rec.starts)))
            ok = not fx.issues
            nres = HighsEngine().solve(fx, SolverSettings(time_limit=10, mip_gap=1e-6)) if ok else None
            if nres is not None and nres.status.has_solution:
                r = to_portfolio_result(fx, nres)
                br = evaluate_plan(d2, base_rec.starts, u2, "fixed", PortfolioTier.COMMITTED, r, keep_projects=False)[0]
                out.baseline_plan = {"alt_id": base_rec.alt_id, "feasible": True, **_risk_brief(br)}
            else:
                out.baseline_plan = {"alt_id": base_rec.alt_id, "feasible": False,
                                     "note": "the baseline recommendation no longer fits this scenario's budgets or capacity"}
        return out

    return cache.get_or_compute("whatif", [d2, u2, cfg.whatif_pareto.to_dict(), cfg.pareto.primary,
                                           cfg.pareto.secondary, w.levers(), cfg.primary_profile], run)
