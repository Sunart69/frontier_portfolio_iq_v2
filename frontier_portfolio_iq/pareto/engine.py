"""Exact multiobjective methods over the portfolio MILP: payoff table, epsilon-constraint with adaptive
refinement, and normalized weighted sums.

Epsilon-constraint (primary method)
    maximize  f_primary + delta * sum_k (f_k - nadir_k) / range_k          (augmented, AUGMECON-style)
    s.t.      f_k >= e_k   for each secondary objective k (maximization form; minimized objectives
              are multiplied by -1), plus every portfolio constraint of the base model.
    The small augmentation term (delta = 1e-3 by default) steers ties towards plans that are not
    weakly dominated. Each e_k runs over a grid between the payoff-table nadir and ideal; grid
    combinations that admit no feasible portfolio are recorded as infeasible (not silently dropped).
    Adaptive refinement then bisects every gap between neighbouring efficient plans whose normalized
    distance exceeds a threshold. Each solve is warm-started from the previous plan where the engine
    supports it (HiGHS), uses a fixed seed and the configured time limit and gap, so runs reproduce.

Weighted sum (secondary method)
    maximize  sum_k w_k * (f_k - nadir_k) / range_k      (every w_k >= 0.001, so optima are efficient)
    Normalization puts dollars, months and risk scores on a common 0–1 scale. In a discrete portfolio
    problem the frontier is generally non-convex: a weighted sum can only reach *supported* efficient
    plans (on the convex hull), so it can miss efficient trade-offs that epsilon-constraint finds.
"""
from __future__ import annotations

import dataclasses
import itertools
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import pulp

from ..canonical import ModelVersion, digest, fingerprint
from ..domain import PortfolioData, PortfolioTier
from ..optimizer import EPS_HOURS, SolveOptions
from ..solvers.base import SolverSettings
from ..solvers.compiled import compile_portfolio, to_portfolio_result
from ..solvers.milp import HighsEngine
from ..solvers.validate import validate_result
from .frontier import Evidence, Frontier, PortfolioAlternative, describe_result, plan_id
from .objectives import ObjectiveSet


@dataclass
class ParetoConfig:
    primary: str = "value"
    secondary: Tuple[str, ...] = ("risk", "time_to_value")
    grid_points: int = 4
    refine_rounds: int = 2
    refine_threshold: float = 0.25
    max_points: int = 36
    augmentation: float = 1e-3
    time_limit: float = 10.0
    mip_gap: float = 0.01
    seed: int = 20270101
    warm_start: bool = True
    weighted_step: float = 0.5          # simplex lattice step for weighted-sum sweeps (0 = off)
    weighted_keys: Tuple[str, ...] = ()  # default: primary + secondary

    @property
    def keys(self) -> Tuple[str, ...]:
        return (self.primary,) + tuple(k for k in self.secondary if k != self.primary)

    def to_dict(self):
        return dataclasses.asdict(self)


class ParetoEngine:
    def __init__(self, data: PortfolioData, objectives: ObjectiveSet, config: Optional[ParetoConfig] = None,
                 base_options: Optional[SolveOptions] = None, scenario: str = "baseline"):
        self.data, self.objset = data, objectives
        self.cfg = config or ParetoConfig()
        missing = [k for k in self.cfg.keys if k not in objectives.keys]
        if missing:
            raise ValueError(f"objectives {missing} are not enabled/applicable: "
                             + "; ".join(f"{k}: {objectives.skipped.get(k, 'not enabled')}" for k in missing))
        self.opts = base_options or SolveOptions(tier=PortfolioTier.COMMITTED)
        self.version = ModelVersion(fingerprint(data), scenario)
        self.signs = {k: self.objset.spec(k).sign for k in self.cfg.keys}
        self.frontier = Frontier(self.version, self.cfg.keys, self.signs)
        self.frontier.log.append(f"config hash {digest(self.cfg.to_dict())[:12]}, model {self.version.label()}")
        self._last_values: Optional[Dict[str, float]] = None
        self.solves = 0

    # ------------------------------------------------------------------ scalarized solve
    def _range(self, k: str) -> Tuple[float, float]:
        """(low, high) in maximization form for normalization."""
        sp = self.objset.spec(k)
        if sp.normalization == "fixed" and sp.fixed_range:
            lo, hi = (sp.sign * v for v in sp.fixed_range)
            return (min(lo, hi), max(lo, hi))
        if sp.normalization == "none":
            return (0.0, 1.0)
        f = self.frontier
        if k in f.ideal and k in f.nadir:
            a, b = self.signs.get(k, sp.sign) * f.nadir[k], self.signs.get(k, sp.sign) * f.ideal[k]
            return (min(a, b), max(a, b))
        return (0.0, 1.0)

    def solve_scalarized(self, weights: Dict[str, float], eps: Dict[str, float], method: str,
                         provenance: Dict[str, object], normalize: bool = False) -> Optional[PortfolioAlternative]:
        """maximize sum_k weights_k * g_k (g = sign*f, optionally normalized) s.t. g_k >= eps_k."""
        objset = self.objset

        def customize(model, prob):
            exprs = {k: objset.spec(k).sign * objset.impl[k].linear(model, prob)
                     for k in set(weights) | set(eps)}
            terms = []
            for k, w in weights.items():
                if not w:
                    continue
                if normalize:
                    lo, hi = self._range(k)
                    terms.append(w * (exprs[k] - lo) / max(hi - lo, 1e-9))
                else:
                    terms.append(w * exprs[k])
            idle = pulp.lpSum(model.y.values())
            prob.setObjective(pulp.lpSum(terms) - EPS_HOURS * 1e-3 * idle)
            for k, e in eps.items():
                prob += exprs[k] >= e, f"eps_{k}"

        compiled = compile_portfolio(self.data, self.opts, customize)
        compiled.custom_objective = True
        settings = SolverSettings(time_limit=self.cfg.time_limit, mip_gap=self.cfg.mip_gap, seed=self.cfg.seed,
                                  warm_start=self._last_values if self.cfg.warm_start else None)
        nres = HighsEngine().solve(compiled, settings)
        self.solves += 1
        if not nres.status.has_solution:
            self.frontier.infeasible.append({"method": method, "epsilon": dict(eps), "weights": dict(weights),
                                             "status": nres.status.value, "message": nres.message})
            return None
        self._last_values = nres.values
        res = to_portfolio_result(compiled, nres, label=method)
        val = validate_result(res, check_objective=False)
        ev = Evidence(nres.engine, nres.status.value, nres.proof, nres.gap, nres.bound, val.feasible,
                      val.failures()[:5], nres.runtime, certified=True)
        alt = self._alternative(res, method, ev, provenance)
        return self.frontier.add(alt)

    def _alternative(self, res, method, ev, provenance) -> PortfolioAlternative:
        cap, lob, budget = describe_result(res, self.data)
        return PortfolioAlternative(plan_id(res.starts), method, self.version, self.cfg.keys, dict(res.starts),
                                    self.objset.evaluate(res), ev, provenance, cap, lob, budget, result=res)

    # ------------------------------------------------------------------ payoff table
    def payoff_table(self) -> None:
        f = self.frontier
        rows: Dict[str, Dict[str, float]] = {}
        for k in self.cfg.keys:
            others = {o: self.cfg.augmentation * 1e-3 for o in self.cfg.keys if o != k}
            alt = self.solve_scalarized({k: 1.0}, {}, "payoff", {"optimize": k})
            if alt is None:
                continue
            # lexicographic tie-break: hold k at its optimum, improve the others slightly
            g = self.signs[k] * alt.objectives[k]
            slack = abs(g) * self.cfg.mip_gap + 1e-6
            alt2 = self.solve_scalarized({k: 1.0, **others}, {k: g - slack}, "payoff", {"optimize": k, "lexicographic": True})
            alt = alt2 or alt
            alt.name = alt.name or f"Best {self.objset.spec(k).name.lower()}"
            rows[k] = dict(alt.objectives)
        f.payoff = rows
        self._update_ideal_nadir()

    def _update_ideal_nadir(self) -> None:
        f = self.frontier
        pts = [a.objectives for a in f.alternatives if a.evidence.validated]
        for k in self.objset.keys:
            sign = self.objset.spec(k).sign
            vals = [p[k] for p in pts if k in p]
            if not vals:
                continue
            best = max(vals, key=lambda v: sign * v)
            if k in f.payoff:
                best = f.payoff[k][k] if sign * f.payoff[k][k] >= sign * best else best
            payoff_vals = [row[k] for row in f.payoff.values() if k in row] or vals
            worst = min(payoff_vals, key=lambda v: sign * v)
            f.ideal[k], f.nadir[k] = best, worst

    # ------------------------------------------------------------------ epsilon-constraint
    def epsilon_grid(self) -> List[Dict[str, float]]:
        grids = []
        n = max(2, self.cfg.grid_points)
        for k in self.cfg.secondary:
            lo, hi = self._range(k)
            # from loose (nadir) to tight (ideal); the tightest level is the ideal itself
            grids.append([(k, lo + (hi - lo) * j / (n - 1)) for j in range(n)])
        combos = [dict(c) for c in itertools.product(*grids)] if grids else [{}]
        return combos

    def _aug(self) -> Dict[str, float]:
        """Augmentation weights: delta * range(primary) / range(k), so each secondary's tie-break term is
        worth at most delta of the primary's range (AUGMECON normalization)."""
        lo_p, hi_p = self._range(self.cfg.primary)
        out = {}
        for k in self.cfg.secondary:
            lo, hi = self._range(k)
            out[k] = self.cfg.augmentation * max(hi_p - lo_p, 1e-9) / max(hi - lo, 1e-9)
        return out

    def run_epsilon(self) -> None:
        primary = self.cfg.primary
        aug = self._aug()
        for eps in self.epsilon_grid():
            if self.solves >= self.cfg.max_points:
                self.frontier.log.append("max_points reached during grid")
                break
            self.solve_scalarized({primary: 1.0, **aug}, eps, "epsilon", {"epsilon": eps}, normalize=False)
        self.frontier.rank()
        for r in range(self.cfg.refine_rounds):
            added = self._refine()
            self.frontier.log.append(f"refinement round {r + 1}: {added} new epsilon combinations")
            self.frontier.rank()
            if not added:
                break

    def _refine(self) -> int:
        f = self.frontier
        eff = f.efficient()
        if len(eff) < 2:
            return 0
        self._update_ideal_nadir()
        keys = self.cfg.keys
        norm = {a.alt_id: f.normalized(a) for a in eff}
        new = 0
        tried = {tuple(sorted((k, round(v, 6)) for k, v in rec.get("epsilon", {}).items())) for rec in f.infeasible}
        for k in self.cfg.secondary:
            ordered = sorted(eff, key=lambda a: self.signs[k] * a.objectives[k])
            for a, b in zip(ordered, ordered[1:]):
                na, nb = norm[a.alt_id], norm[b.alt_id]
                dist = math.sqrt(sum((na.get(q, 0) - nb.get(q, 0)) ** 2 for q in keys))
                if dist <= self.cfg.refine_threshold:
                    continue
                eps = {}
                for s in self.cfg.secondary:
                    ga, gb = self.signs[s] * a.objectives[s], self.signs[s] * b.objectives[s]
                    eps[s] = (ga + gb) / 2 if s == k else min(ga, gb)
                key = tuple(sorted((q, round(v, 6)) for q, v in eps.items()))
                if key in tried or self.solves >= self.cfg.max_points:
                    continue
                tried.add(key)
                aug = self._aug()
                before = len(f.alternatives)
                self.solve_scalarized({self.cfg.primary: 1.0, **aug}, eps, "epsilon-refine", {"epsilon": eps})
                new += len(f.alternatives) - before
        return new

    # ------------------------------------------------------------------ weighted sum
    def weight_vectors(self, keys: Sequence[str], step: float) -> List[Dict[str, float]]:
        n = int(round(1 / step))
        out = []
        for combo in itertools.product(range(n + 1), repeat=len(keys)):
            if sum(combo) == n:
                out.append({k: c / n for k, c in zip(keys, combo)})
        return out

    def run_weighted(self, weights_list: Optional[List[Dict[str, float]]] = None) -> List[PortfolioAlternative]:
        keys = tuple(self.cfg.weighted_keys or self.cfg.keys)
        if weights_list is None:
            if not self.cfg.weighted_step:
                return []
            weights_list = self.weight_vectors(keys, self.cfg.weighted_step)
        self._update_ideal_nadir()
        out = []
        # strictly positive weights: a zero weight lets the solver return weakly dominated plans
        weights_list = [{k: max(v, 1e-3) for k, v in w.items()} for w in weights_list]
        for w in weights_list:
            if self.solves >= self.cfg.max_points + len(weights_list):
                break
            alt = self.solve_scalarized(w, {}, "weighted", {"weights": w}, normalize=True)
            if alt is not None:
                out.append(alt)
        self.frontier.rank()
        return out

    # ------------------------------------------------------------------ all
    def run(self, weighted: bool = True) -> Frontier:
        self.payoff_table()
        self.run_epsilon()
        if weighted:
            self.run_weighted()
        self._update_ideal_nadir()
        self.frontier.rank()
        self.frontier.log.append(f"{self.solves} MILP solves; {len(self.frontier.alternatives)} distinct plans; "
                                 f"{len(self.frontier.efficient())} efficient; "
                                 f"{len(self.frontier.infeasible)} infeasible/unsolved combinations")
        return self.frontier

    def add_plan(self, res, method: str, name: str = "", provenance: Optional[Dict] = None,
                 certified: bool = True) -> PortfolioAlternative:
        """Bring an externally produced plan (baseline, stochastic, NSGA-II, LT decision) onto the frontier
        — only if it was produced under this frontier's model version (caller's responsibility to use the
        same data; the version check is enforced)."""
        val = validate_result(res, check_objective=False)
        ev = Evidence("HiGHS" if certified else method, res.status, "external", None, None, val.feasible,
                      val.failures()[:5], res.solve_seconds, certified=certified)
        alt = self._alternative(res, method, ev, provenance or {})
        alt.name = name
        added = self.frontier.add(alt)
        if name and not added.name:
            added.name = name
        return added
