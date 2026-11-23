"""Solver orchestration, fallback and the Solver Confidence Report.

Flow for one optimization:

1. compile the formulation once (`compile_portfolio`);
2. solve with the primary engine (HiGHS); if it returns no solution (error, timeout without incumbent),
   fall back to SCIP, then CBC — the fallback is recorded;
3. validate the solution independently (`validate.py`);
4. re-solve with the validation engines — SCIP on the identical formulation, CP-SAT on its integer
   reformulation (where compatible), CBC optionally — and validate each of their solutions too;
5. compare: objectives against each other *and against each engine's proven bound*, statuses, and
   validation outcomes. Inconsistencies produce diagnostics; they never reject a portfolio on their own.

Confidence levels (most to least assured):

- ``CONFIRMED_OPTIMAL``   primary proved optimality (or the requested gap) and at least one independent
                           engine reached a consistent objective; every reported solution validates
- ``PROVEN_SINGLE``       primary proved optimality / gap and validates; no independent engine could
                           confirm (unavailable, incompatible or timed out)
- ``FEASIBLE_UNPROVEN``   a validated solution without a proven gap inside the requested tolerance
- ``DISAGREEMENT``        engines disagree beyond their proven gaps, or a solution failed validation
                           — the result stands, flagged for review with diagnostics
- ``INFEASIBLE``          no engine found a solution (with the engines that agree on it listed)
"""
from __future__ import annotations

import dataclasses
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from ..canonical import ModelVersion, fingerprint
from ..domain import PortfolioData
from ..optimizer import PortfolioResult, SolveOptions
from .base import Engine, NormalizedResult, SolverSettings, SolveStatus
from .compiled import CompiledModel, compile_portfolio, to_portfolio_result
from .cpsat import CpSatEngine
from .milp import CbcEngine, HighsEngine, ScipEngine
from .qp import IpoptEngine, OsqpLevelingEngine
from .validate import ValidationReport, validate_result

ENGINES = {"HiGHS": HighsEngine, "SCIP": ScipEngine, "CP-SAT": CpSatEngine, "CBC": CbcEngine,
           "OSQP": OsqpLevelingEngine, "IPOPT": IpoptEngine}


def engine_inventory() -> List[Dict[str, object]]:
    """What is installed and what each engine is used for."""
    req = {"HiGHS": "required", "SCIP": "required", "CP-SAT": "required", "CBC": "optional",
           "OSQP": "optional", "IPOPT": "optional"}
    return [{"engine": n, "role": cls.role, "kind": cls.kind, "requirement": req[n], "available": cls.available()}
            for n, cls in ENGINES.items()]


def make_engine(name: str) -> Engine:
    return ENGINES[name]()


@dataclass
class EngineRun:
    result: NormalizedResult
    portfolio: Optional[PortfolioResult] = None
    validation: Optional[ValidationReport] = None
    role: str = "validation"          # primary | fallback | validation

    def summary(self) -> Dict[str, object]:
        out = self.result.summary()
        out["role"] = self.role
        if self.validation is not None:
            out["validated_feasible"] = self.validation.feasible
            out["objective_consistent"] = self.validation.objective_consistent
            out["validation_failures"] = self.validation.failures()[:5]
        if self.portfolio is not None and self.portfolio.feasible:
            out["selected"] = sorted(self.portfolio.selected)
        return out


@dataclass
class SolverConfidenceReport:
    version: ModelVersion
    label: str
    size: Dict[str, int]
    runs: List[EngineRun]
    comparisons: List[Dict[str, object]]
    diagnostics: List[str]
    level: str
    verdict: str
    certified_gap: Optional[float]
    best_bound: Optional[float]
    chosen_engine: Optional[str]
    build_seconds: float = 0.0
    settings: Dict[str, object] = field(default_factory=dict)

    @property
    def primary(self) -> Optional[EngineRun]:
        return next((r for r in self.runs if r.role in ("primary", "fallback") and r.result.status.has_solution),
                    self.runs[0] if self.runs else None)

    def summary(self) -> Dict[str, object]:
        return {"label": self.label, "model_version": self.version.label(), "size": self.size,
                "level": self.level, "verdict": self.verdict, "chosen_engine": self.chosen_engine,
                "certified_gap": self.certified_gap, "best_bound": self.best_bound,
                "build_seconds": round(self.build_seconds, 3), "settings": self.settings,
                "runs": [r.summary() for r in self.runs], "comparisons": self.comparisons,
                "diagnostics": self.diagnostics}

    def markdown(self) -> str:
        def f(v, pct=False):
            if v is None:
                return "—"
            return f"{v:.3%}" if pct else f"{v:,.2f}"
        lines = [f"### Solver confidence: {self.label}", "",
                 f"**{self.level.replace('_', ' ').title()}** — {self.verdict}", "",
                 f"Model {self.version.label()} · {self.size.get('variables', 0):,} variables "
                 f"({self.size.get('integer_variables', 0):,} integer) · {self.size.get('constraints', 0):,} constraints",
                 "", "| Engine | Role | Status | Proof | Objective | Bound | Gap | Validated | Time (s) |",
                 "|---|---|---|---|---:|---:|---:|---|---:|"]
        for r in self.runs:
            n = r.result
            val = "—" if r.validation is None else ("yes" if r.validation.feasible else "**no**")
            lines.append(f"| {n.engine} | {r.role} | {n.status.value} | {n.proof} | {f(n.objective)} | "
                         f"{f(n.bound)} | {f(n.gap, True)} | {val} | {n.runtime:.2f} |")
        if self.comparisons:
            lines += ["", "| Comparison | Objective difference | Allowed | Consistent |", "|---|---:|---:|---|"]
            for c in self.comparisons:
                lines.append(f"| {c['pair']} | {f(c.get('difference'))} | {f(c.get('allowed'))} | "
                             f"{'yes' if c['consistent'] else '**no**'} |")
        if self.diagnostics:
            lines += ["", "Diagnostics:"] + [f"- {d}" for d in self.diagnostics]
        return "\n".join(lines) + "\n"


def _map_and_validate(compiled: CompiledModel, nres: NormalizedResult, role: str, label: str) -> EngineRun:
    run = EngineRun(nres, role=role)
    if nres.status.has_solution:
        run.portfolio = to_portfolio_result(compiled, nres, label)
        run.validation = validate_result(run.portfolio, check_objective=not getattr(compiled, "custom_objective", False))
    return run


def compare_runs(a: EngineRun, b: EngineRun, requested_gap: float) -> Dict[str, object]:
    ra, rb = a.result, b.result
    out: Dict[str, object] = {"pair": f"{ra.engine} vs {rb.engine}"}
    if ra.status.has_solution and rb.status.has_solution:
        diff = (ra.objective or 0.0) - (rb.objective or 0.0)
        scale = max(1.0, abs(ra.objective or 0.0), abs(rb.objective or 0.0))
        gaps = [g for g in (ra.gap, rb.gap) if g is not None]
        allowed = (max(gaps + [requested_gap]) + 1e-6) * scale
        problems = []
        # a solution worth more than another engine's proven upper bound means that bound is wrong
        if rb.bound is not None and "approximate" not in str(rb.extra.get("bound_note", "")) \
                and (ra.objective or 0) > rb.bound + 1e-6 * scale + 1e-3:
            problems.append(f"{ra.engine} objective {ra.objective:,.2f} exceeds {rb.engine} bound {rb.bound:,.2f}")
        if ra.bound is not None and "approximate" not in str(ra.extra.get("bound_note", "")) \
                and (rb.objective or 0) > ra.bound + 1e-6 * scale + 1e-3:
            problems.append(f"{rb.engine} objective {rb.objective:,.2f} exceeds {ra.engine} bound {ra.bound:,.2f}")
        both_proven = ra.proof in ("optimal", "within_gap") and rb.proof in ("optimal", "within_gap")
        if both_proven and abs(diff) > allowed:
            problems.append(f"both engines claim optimality but differ by {abs(diff):,.2f} (> {allowed:,.2f})")
        out.update(difference=diff, allowed=allowed, consistent=not problems, problems=problems,
                   same_selection=(a.portfolio is not None and b.portfolio is not None
                                   and a.portfolio.selected == b.portfolio.selected))
        return out
    if ra.status == SolveStatus.INFEASIBLE and rb.status.has_solution and b.validation is not None \
            and b.validation.feasible:
        out.update(consistent=False, problems=[f"{ra.engine} reports infeasible but {rb.engine} found a "
                                               f"validated feasible plan"])
        return out
    if rb.status == SolveStatus.INFEASIBLE and ra.status.has_solution and a.validation is not None \
            and a.validation.feasible:
        out.update(consistent=False, problems=[f"{rb.engine} reports infeasible but {ra.engine} found a "
                                               f"validated feasible plan"])
        return out
    out.update(consistent=True, problems=[], note=f"{rb.engine}: {rb.status.value} — not comparable")
    return out


def solve_validated(data: PortfolioData, opts: Optional[SolveOptions] = None,
                    settings: Optional[SolverSettings] = None,
                    validators: Sequence[str] = ("SCIP", "CP-SAT"),
                    fallback: Sequence[str] = ("SCIP", "CBC"),
                    primary: str = "HiGHS", label: str = "", scenario: str = "baseline",
                    compiled: Optional[CompiledModel] = None) -> tuple:
    """Solve with the primary engine, validate independently, cross-check with other engines.

    Returns (PortfolioResult, SolverConfidenceReport). The returned portfolio is the primary engine's
    plan when it validates; otherwise the best validated plan from another engine (recorded)."""
    opts = opts or SolveOptions()
    settings = settings or SolverSettings(time_limit=data.settings.time_limit, mip_gap=data.settings.mip_gap)
    compiled = compiled or compile_portfolio(data, opts)
    version = ModelVersion(fingerprint(data), scenario)
    label = label or opts.label or opts.tier.value
    runs: List[EngineRun] = []
    diags: List[str] = []
    if compiled.issues and not opts.soft:
        from ..optimizer import solve as legacy_solve
        res = legacy_solve(data, opts)  # explains which commitments break (soft re-solve)
        rep = SolverConfidenceReport(version, label, compiled.size, [], [], list(res.diagnostics), "INFEASIBLE",
                                     "The model is infeasible before solving: " + "; ".join(compiled.issues),
                                     None, None, None, compiled.build_seconds, dataclasses.asdict(settings))
        return res, rep
    # primary + fallback
    order = [primary] + [e for e in fallback if e != primary]
    chosen: Optional[EngineRun] = None
    for k, name in enumerate(order):
        eng = make_engine(name)
        nres = eng.solve(compiled, settings)
        run = _map_and_validate(compiled, nres, "primary" if k == 0 else "fallback", label)
        runs.append(run)
        if nres.status.has_solution:
            chosen = run
            if k > 0:
                diags.append(f"Primary engine {primary} returned {runs[0].result.status.value} "
                             f"({runs[0].result.message}); fell back to {name}")
            break
        if nres.status == SolveStatus.INFEASIBLE:
            break           # infeasibility is a model property, not an engine failure: confirm below
    # validators
    for name in validators:
        if any(r.result.engine == name for r in runs):
            continue
        nres = make_engine(name).solve(compiled, dataclasses.replace(settings, warm_start=None))
        runs.append(_map_and_validate(compiled, nres, "validation", label))
    # validation outcomes
    for r in runs:
        if r.validation is not None and not r.validation.feasible:
            diags.append(f"{r.result.engine} solution fails independent validation: "
                         + "; ".join(r.validation.failures()[:3]))
        if r.validation is not None and r.validation.objective_consistent is False:
            diags.append(f"{r.result.engine} reported objective differs from the recomputed objective "
                         f"({r.validation.objective_reported:,.4f} vs {r.validation.objective_recomputed:,.4f})")
    # comparisons against the chosen/primary run
    ref = chosen or runs[0]
    comparisons = [compare_runs(ref, r, settings.mip_gap) for r in runs if r is not ref]
    for c in comparisons:
        for p in c.get("problems", []):
            diags.append(p)
    # choose the plan: primary if it validates, else best validated alternative
    validated = [r for r in runs if r.portfolio is not None and r.validation is not None and r.validation.feasible]
    if chosen is not None and chosen not in validated and validated:
        best = max(validated, key=lambda r: r.result.objective or float("-inf"))
        diags.append(f"Using the validated {best.result.engine} plan instead of the {chosen.result.engine} plan")
        chosen = best
    # certified bound / gap across engines with exact bounds
    exact_bounds = [r.result.bound for r in runs if r.result.bound is not None
                    and "approximate" not in str(r.result.extra.get("bound_note", ""))
                    and r.result.status.has_solution]
    best_bound = min(exact_bounds) if exact_bounds else None
    best_obj = max((r.result.objective for r in validated if r.result.objective is not None), default=None)
    cert_gap = None
    if best_bound is not None and best_obj is not None:
        cert_gap = max(0.0, best_bound - best_obj) / max(1.0, abs(best_obj))
    # level
    inconsistent = any(not c["consistent"] for c in comparisons) or any(
        r.validation is not None and not r.validation.feasible for r in runs)
    confirmers = [c for c in comparisons if c["consistent"] and c.get("difference") is not None]
    if chosen is None:
        infeasible_by = [r.result.engine for r in runs if r.result.status == SolveStatus.INFEASIBLE]
        level = "INFEASIBLE" if not inconsistent else "DISAGREEMENT"
        verdict = (f"No feasible plan; infeasibility reported by {', '.join(infeasible_by) or 'no engine'}"
                   if infeasible_by else "No engine produced a plan within the limits")
        from ..optimizer import solve as legacy_solve
        res = legacy_solve(data, opts)
        diags += [d for d in res.diagnostics if d not in diags]
    else:
        res = chosen.portfolio
        proof = chosen.result.proof
        if inconsistent:
            level = "DISAGREEMENT"
            verdict = "Engines disagree or a solution failed validation — plan stands, review the diagnostics"
        elif proof in ("optimal", "within_gap") and confirmers:
            level = "CONFIRMED_OPTIMAL"
            names = ", ".join(c["pair"].split(" vs ")[1] for c in confirmers)
            verdict = (f"{chosen.result.engine} proved {'optimality' if proof == 'optimal' else f'a gap of {chosen.result.gap or 0:.2%}'};"
                       f" independently confirmed by {names}")
        elif proof in ("optimal", "within_gap"):
            level = "PROVEN_SINGLE"
            verdict = f"{chosen.result.engine} proved {proof.replace('_', ' ')}; no independent engine confirmed it"
        else:
            level = "FEASIBLE_UNPROVEN"
            verdict = (f"Validated feasible plan; optimality not proven within {settings.mip_gap:.1%} "
                       f"(gap {chosen.result.gap:.2%})" if chosen.result.gap is not None else
                       "Validated feasible plan; no proven bound")
    rep = SolverConfidenceReport(version, label, compiled.size, runs, comparisons, list(dict.fromkeys(diags)),
                                 level, verdict, cert_gap, best_bound,
                                 chosen.result.engine if chosen else None, compiled.build_seconds,
                                 {"time_limit": settings.time_limit, "mip_gap": settings.mip_gap,
                                  "seed": settings.seed, "threads": settings.threads,
                                  "validators": list(validators), "fallback": list(fallback)})
    return res, rep
