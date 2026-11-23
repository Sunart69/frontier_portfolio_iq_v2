"""Optional NSGA-II search (pymoo) for large or nonlinear search spaces.

Genome: one integer gene per initiative — 0 = not selected, k = the k-th allowed start month.
Feasibility-aware generation:

1. *Repair operator* (every offspring): clamp genes to their windows; force commitments in; drop an
   initiative whose prerequisite is out, or push it after its prerequisite finishes; drop the lowest
   value-density optional initiatives while aggregate budget or aggregate capability hours (necessary
   conditions) are exceeded.
2. *Exact feasibility check* (every evaluation): the repaired plan is solved as an LP with the starts
   fixed — capacity, eligibility, ring-fencing, transfers and budgets month by month. If the LP is
   infeasible, the lowest value-density optional initiative is dropped and the LP re-run, up to
   `max_repairs` times; plans still infeasible carry a constraint violation so NSGA-II discards them.

Every plan NSGA-II returns has therefore been *certified feasible* by an exact LP and independently
validated; its *efficiency* is not proven. Alternatives are labelled ``approximate`` and compared with
the exact epsilon-constraint frontier: a plan dominated by an exact plan is marked as such.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np

from ..optimizer import SolveOptions, start_options
from ..solvers.base import SolverSettings
from ..solvers.compiled import compile_portfolio, to_portfolio_result
from ..solvers.milp import HighsEngine
from ..solvers.validate import validate_result
from .frontier import Evidence, PortfolioAlternative


@dataclass
class NsgaConfig:
    pop_size: int = 24
    generations: int = 12
    seed: int = 20270101
    max_repairs: int = 3
    lp_time_limit: float = 5.0


def available() -> bool:
    try:
        import pymoo  # noqa: F401
        return True
    except Exception:
        return False


class PlanCodec:
    def __init__(self, data, opts: SolveOptions):
        self.data, self.opts = data, opts
        s = data.settings
        self.ids = sorted(i for i in data.projects if opts.candidate_projects is None or i in opts.candidate_projects)
        self.S = {i: start_options(data, data.projects[i], opts) for i in self.ids}
        pc = s.protect_compliance if opts.protect_compliance is None else opts.protect_compliance
        pk = s.protect_critical if opts.protect_critical is None else opts.protect_critical
        P = data.projects
        self.must = {i for i in self.ids if P[i].mandatory or (pk and P[i].critical) or (pc and P[i].compliance)
                     or i in opts.force_select} - set(opts.force_exclude)
        self.density = {i: P[i].value / max(1.0, P[i].total_cost) for i in self.ids}
        # aggregate (necessary) capacity per capability and budget per (LOB, source)
        self.cap_hours: Dict[str, float] = {}
        for pool in data.pools.values():
            tot = sum(pool.available_hours(t) for t in data.months)
            for c in {d for p in P.values() for (d, _k) in p.demand}:
                eff = data.substitution_efficiency(pool.capability, c)
                if eff > 0:
                    self.cap_hours[c] = self.cap_hours.get(c, 0.0) + tot * eff
        self.budget: Dict[Tuple[str, str], float] = {}
        for b in data.budgets:
            self.budget[b.lob_id, b.funding_source] = self.budget.get((b.lob_id, b.funding_source), 0.0) + b.amount

    @property
    def upper(self) -> np.ndarray:
        return np.array([len(self.S[i]) for i in self.ids])

    def decode(self, genes) -> Dict[str, int]:
        out = {}
        for g, i in zip(genes, self.ids):
            g = int(round(g))
            if g >= 1 and self.S[i] and i not in self.opts.force_exclude:
                out[i] = self.S[i][min(g, len(self.S[i])) - 1]
        return out

    def encode(self, starts: Dict[str, int]) -> np.ndarray:
        return np.array([(self.S[i].index(starts[i]) + 1) if i in starts and starts[i] in self.S[i] else 0
                         for i in self.ids])

    def repair(self, starts: Dict[str, int]) -> Dict[str, int]:
        P = self.data.projects
        st = dict(starts)
        for i in self.must:
            if i not in st and self.S[i]:
                st[i] = self.S[i][0]
        changed = True
        while changed:
            changed = False
            for j in list(st):
                for i in P[j].depends_on:
                    if i not in P:
                        continue
                    if i not in st:
                        if j in self.must and self.S.get(i):
                            st[i] = self.S[i][0]
                        else:
                            st.pop(j, None)
                        changed = True
                        break
                    end_i = st[i] + P[i].duration - 1
                    if st[j] <= end_i:
                        later = [s for s in self.S[j] if s > end_i]
                        if later:
                            st[j] = later[0]
                        else:
                            st.pop(j, None)
                        changed = True
                        break
        # necessary aggregate conditions
        def over() -> Optional[str]:
            dem: Dict[str, float] = {}
            spend: Dict[Tuple[str, str], float] = {}
            for i in st:
                for (c, _k), h in P[i].demand.items():
                    dem[c] = dem.get(c, 0.0) + h
                for l, sh in P[i].shares().items():
                    spend[l, P[i].funding_source] = spend.get((l, P[i].funding_source), 0.0) + sh * P[i].total_cost
            for c, h in dem.items():
                if h > self.cap_hours.get(c, 0.0) + 1e-6:
                    return c
            for k, v in spend.items():
                if v > self.budget.get(k, 0.0) + 1e-6:
                    return str(k)
            return None
        while over() is not None:
            optional = [i for i in st if i not in self.must]
            if not optional:
                break
            st.pop(min(optional, key=lambda i: self.density[i]))
            st = {i: s for i, s in st.items() if all(d in st or d not in P for d in P[i].depends_on)}
        return st

    def drop_one(self, starts: Dict[str, int]) -> Optional[Dict[str, int]]:
        optional = [i for i in starts if i not in self.must]
        if not optional:
            return None
        out = dict(starts)
        out.pop(min(optional, key=lambda i: self.density[i]))
        return self.repair(out)


def run_nsga2(engine, cfg: Optional[NsgaConfig] = None, seeds: Optional[List[Dict[str, int]]] = None
              ) -> List[PortfolioAlternative]:
    """Run NSGA-II on the engine's objectives and add certified-feasible, approximate alternatives to the
    engine's frontier. `seeds` (e.g. exact frontier plans) can be injected into the first population."""
    from pymoo.algorithms.moo.nsga2 import NSGA2
    from pymoo.core.problem import ElementwiseProblem
    from pymoo.core.repair import Repair
    from pymoo.operators.crossover.sbx import SBX
    from pymoo.operators.mutation.pm import PM
    from pymoo.operators.sampling.rnd import IntegerRandomSampling
    from pymoo.optimize import minimize
    from pymoo.core.population import Population

    cfg = cfg or NsgaConfig()
    data, objset, opts = engine.data, engine.objset, engine.opts
    keys = list(engine.cfg.keys)
    codec = PlanCodec(data, opts)
    cache: Dict[Tuple, Tuple] = {}
    certified: Dict[Tuple, object] = {}

    def evaluate_plan(starts: Dict[str, int]):
        key = tuple(sorted(starts.items()))
        if key in cache:
            return cache[key]
        cur = starts
        for _ in range(cfg.max_repairs + 1):
            compiled = compile_portfolio(data, dataclasses.replace(opts, fixed_starts=dict(cur), candidate_starts=[]))
            if compiled.issues:
                nres = None
            else:
                nres = HighsEngine().solve(compiled, SolverSettings(time_limit=cfg.lp_time_limit, mip_gap=1e-6,
                                                                     seed=cfg.seed))
            if nres is not None and nres.status.has_solution:
                res = to_portfolio_result(compiled, nres, "nsga2")
                vec = objset.evaluate(res)
                out = ([-engine.signs[k] * vec[k] for k in keys], 0.0, res, cur)
                cache[key] = out
                certified[tuple(sorted(cur.items()))] = res
                return out
            nxt = codec.drop_one(cur)
            if nxt is None:
                break
            cur = nxt
        out = ([1e12] * len(keys), 1.0, None, cur)
        cache[key] = out
        return out

    class Prob(ElementwiseProblem):
        def __init__(self):
            super().__init__(n_var=len(codec.ids), n_obj=len(keys), n_ieq_constr=1, xl=0, xu=codec.upper,
                             vtype=int)

        def _evaluate(self, x, out, *args, **kwargs):
            f, g, _res, _st = evaluate_plan(codec.decode(x))
            out["F"], out["G"] = f, [g]

    class DomainRepair(Repair):
        def _do(self, problem, X, **kwargs):
            X = np.clip(np.round(X), 0, codec.upper).astype(int)
            return np.array([codec.encode(codec.repair(codec.decode(x))) for x in X])

    sampling = IntegerRandomSampling()
    if seeds:
        X0 = np.array([codec.encode(codec.repair(s)) for s in seeds])
        rng = np.random.default_rng(cfg.seed)
        extra = max(0, cfg.pop_size - len(X0))
        rand = rng.integers(0, codec.upper + 1, size=(extra, len(codec.ids)))
        sampling = Population.new("X", np.vstack([X0, rand])[:max(cfg.pop_size, len(X0))])
    algo = NSGA2(pop_size=cfg.pop_size, sampling=sampling,
                 crossover=SBX(prob=0.9, eta=15, vtype=float, repair=DomainRepair()),
                 mutation=PM(eta=20, vtype=float, repair=DomainRepair()),
                 repair=DomainRepair(), eliminate_duplicates=True)
    minimize(Prob(), algo, ("n_gen", cfg.generations), seed=cfg.seed, verbose=False)
    # keep NSGA-II's own non-dominated set (not every feasible plan it visited)
    from .frontier import nondominated_sort, to_max, tolerances
    plans = list(certified.items())
    vecs = [to_max(objset.evaluate(r), engine.signs) for _k, r in plans]
    ranks = nondominated_sort(vecs, tolerances(vecs, keys)) if vecs else []
    out = []
    for (key, res), r in zip(plans, ranks):
        if r != 1:
            continue
        val = validate_result(res, check_objective=False)
        ev = Evidence("NSGA-II + HiGHS LP", "feasible", "heuristic", None, None, val.feasible,
                      val.failures()[:5], 0.0, certified=False)
        alt = engine._alternative(res, "nsga2", ev, {"approximate": True,
                                                     "note": "feasibility certified by exact LP; efficiency not proven"})
        out.append(engine.frontier.add(alt))
    engine.frontier.rank()
    engine.frontier.log.append(f"NSGA-II: {len(cache)} plans evaluated, {len(certified)} certified feasible "
                               f"(pop {cfg.pop_size}, {cfg.generations} generations, seed {cfg.seed})")
    return out
