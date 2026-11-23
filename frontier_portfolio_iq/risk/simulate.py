"""Schedule-, resource- and dependency-aware Monte Carlo evaluation of a portfolio plan.

Each iteration executes the plan month by month (vectorized across iterations):

1. **Sample** every initiative's effort, duration, benefit, start slip and delivery failure, every pool's
   availability, hiring delay and productivity, and the shared risk drivers (jointly, through the
   Gaussian copula). Driver effects apply to every initiative or pool they reach.
2. **Start** an initiative when its planned start month (+ sampled slip and driver delays) has come
   *and every prerequisite has finished* — dependency delays propagate down the chain.
3. **Demand**: an active initiative asks each month for ``work_c / (D * duration_multiplier)`` hours of
   each capability c it still needs (work = planned hours x effort multiplier).
4. **Allocate** month by month in priority order, only through the routes the plan is allowed to use —
   the initiative's own LOB's pools, shared pools it is eligible for, and transfer rules authorized for
   the plan's tier, within each rule's ceiling and the owner's retained hours. Ring-fenced capacity is
   never used by another LOB without an authorized rule. Sampled pool capacity is
   ``A[p,t] x availability x productivity`` (zero until the pool's start month + hiring delay).
5. **Unmet hours** stay with the initiative (it runs longer) and are recorded as a capability shortage.
6. **Finish** when no work remains; benefit is realized at the finish month (business value with the
   same schedule erosion the optimizer uses; NPV discounted monthly); a sampled delivery failure
   forfeits the configured share of the benefit. Spend follows delivered work (plus route costs).
7. **Aggregate** portfolio value, NPV, cost, ROI, completion, budget and date breaches, OKR target
   attainment and shortages per iteration.

Execution policies
  fixed     The committed plan as approved: no change of selection or start months (except that an
            initiative cannot start before its prerequisites finish), allocation in planned-start
            order, no contingency. Unlimited resources or budget are never assumed.
  adaptive  Predefined recourse: commitments (mandatory / compliance / critical) first, then value
            density; an initiative whose capabilities have less than `start_gate` of a month's need
            free is deferred (at most `max_defer_months`); approved contingency hours
            (`contingency_pct` of each capability's planned capacity, at `contingency_rate` $/h) cover
            shortfalls. Recourse is bounded and its use is reported.

Common random numbers: every random variable is drawn from a stream keyed by (seed, chunk, entity,
variable), independent of which initiatives a portfolio selects, so alternatives are compared on the
same simulated futures and results are identical whatever the number of worker processes.
"""
from __future__ import annotations

import concurrent.futures as cf
import math
import zlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
from scipy import stats

from ..domain import PortfolioData, PortfolioTier
from ..optimizer import PortfolioModel, SolveOptions, rule_cap
from .correlation import factor
from .uncertainty import UncertaintyModel, selects_pool, selects_project


@dataclass
class Route:
    pool_id: str
    efficiency: float
    rule_id: Optional[str]
    kind: str
    rate: float          # $/hour (transfer + incremental)


@dataclass
class PlanContext:
    data: PortfolioData
    starts: Dict[str, int]
    order: List[str]
    routes: Dict[Tuple[str, str], List[Route]]
    tier: PortfolioTier
    planned_cost: float
    reference_npv: float
    reference_value: float
    plan_split: Dict[Tuple[str, str], Dict[int, List[Tuple[Route, float]]]] = field(default_factory=dict)


def _route_table(data: PortfolioData, starts: Dict[str, int], tier: PortfolioTier) -> Dict[Tuple[str, str], List[Route]]:
    m = PortfolioModel(data, SolveOptions(tier=tier, candidate_projects=set(starts)))
    P = data.projects
    out: Dict[Tuple[str, str], List[Route]] = {}
    for ch in m.channels:
        if ch.project_id not in starts or ch.kind == "relaxed" and tier != PortfolioTier.UNCONSTRAINED:
            continue
        out.setdefault((ch.project_id, ch.capability), []).append(
            Route(ch.pool_id, ch.efficiency, ch.rule_id, ch.kind, ch.transfer_cost_rate + ch.incremental_cost_rate))

    def rank(r: Route, pid: str):
        pool = data.pools[r.pool_id]
        own = pool.owning_lob == P[pid].primary_lob
        return ({"direct": 0, "transfer": 1, "relaxed": 2}[r.kind], 0 if own else 1, -r.efficiency, r.rate, r.pool_id)
    for (pid, c), rs in out.items():
        rs.sort(key=lambda r: rank(r, pid))
    return out


def _pv(rate: float, month) -> np.ndarray:
    return (1 + rate) ** (-np.asarray(month, dtype=float) / 12.0)


def plan_context(data: PortfolioData, starts: Dict[str, int], mode: str,
                 tier: PortfolioTier = PortfolioTier.COMMITTED, result=None) -> PlanContext:
    P, s = data.projects, data.settings
    if mode == "fixed":
        order = sorted(starts, key=lambda i: (starts[i], i))
    else:
        order = sorted(starts, key=lambda i: (not (P[i].mandatory or P[i].compliance or P[i].critical),
                                              -P[i].value / max(1.0, P[i].total_cost), starts[i], i))
    routes = _route_table(data, starts, tier)
    planned_cost = sum(P[i].total_cost for i in starts)
    ref_npv = sum(P[i].value * float(_pv(s.discount_rate, starts[i] + P[i].duration - 1))
                  - sum(c * float(_pv(s.discount_rate, starts[i] + k - 1)) for k, c in P[i].cost.items())
                  for i in starts)
    if result is not None:
        planned_cost += sum(a.transfer_cost + a.incremental_cost for a in result.allocations)
        ref_npv -= sum((a.transfer_cost + a.incremental_cost) * float(_pv(s.discount_rate, a.month))
                       for a in result.allocations)
    ref_val = 0.0
    for i, st in starts.items():
        p = P[i]
        e_min = p.earliest_start + p.duration - 1
        ref_val += p.value * (1 - s.schedule_discount * max(0, st + p.duration - 1 - e_min) / s.horizon)
    split: Dict[Tuple[str, str], Dict[int, List[Tuple[Route, float]]]] = {}
    if result is not None:
        # the plan's own routing by relative month: fractions of each month's effective hours per route
        acc: Dict[Tuple[str, str, int], Dict[Tuple[str, Optional[str]], float]] = {}
        for a in result.allocations:
            if a.project_id not in starts:
                continue
            k = a.month - starts[a.project_id] + 1
            acc.setdefault((a.project_id, a.capability, k), {})
            key = (a.pool_id, a.rule_id)
            acc[a.project_id, a.capability, k][key] = acc[a.project_id, a.capability, k].get(key, 0.0) + a.effective_hours
        for (i, c, k), by in acc.items():
            tot = sum(by.values())
            if tot <= 0:
                continue
            rl = {(r.pool_id, r.rule_id): r for r in routes.get((i, c), [])}
            fr = [(rl[key], h / tot) for key, h in sorted(by.items(), key=lambda kv: -kv[1]) if key in rl]
            if fr:
                split.setdefault((i, c), {})[k] = fr
    return PlanContext(data, dict(starts), order, routes, tier, planned_cost, ref_npv, ref_val, split)


# --------------------------------------------------------------------------- random streams
def _stream(seed: int, chunk: int, key: str) -> np.random.Generator:
    return np.random.default_rng([int(seed) % (2 ** 32), int(chunk), zlib.crc32(key.encode("utf-8"))])


def _uniform(seed, chunk, key, n) -> np.ndarray:
    return _stream(seed, chunk, key).random(n)


def sample_drivers(um: UncertaintyModel, seed: int, chunk: int, n: int) -> Dict[str, np.ndarray]:
    names, C = um.correlation_matrix()
    if not names:
        return {}
    L = factor(C)
    rng = _stream(seed, chunk, "__drivers__|" + "|".join(names))
    Z = rng.standard_normal((n, len(names))) @ L.T
    U = stats.norm.cdf(Z)
    out = {}
    for k, d in enumerate(um.drivers):
        out[d.driver_id] = d.dist.ppf(U[:, k])
    out["__Z__"] = Z
    return out


def _driver_factor(d, v: np.ndarray) -> np.ndarray:
    if d.mode == "multiply":
        return v
    if d.mode == "event":
        return 1.0 + d.magnitude * v
    return v  # add


# --------------------------------------------------------------------------- one chunk
def simulate_chunk(ctx: PlanContext, um: UncertaintyModel, chunk: int, n: int, keep_projects: bool = True
                   ) -> Dict[str, np.ndarray]:
    data, st = ctx.data, um.settings
    P, S = data.projects, data.settings
    H = S.horizon
    T = H + (st.extension_months if st.extension_months is not None else H)
    seed, mode = st.seed, st.mode
    drv = sample_drivers(um, seed, chunk, n)
    ids = ctx.order
    # ---- initiative factors
    eff, dur, ben, slip, fail, price = {}, {}, {}, {}, {}, {}
    for i in ids:
        u = um.projects[i]
        p = P[i]
        eff[i] = u.effort.ppf(_uniform(seed, chunk, f"P|{i}|effort", n))
        dur[i] = u.duration.ppf(_uniform(seed, chunk, f"P|{i}|duration", n))
        ben[i] = u.benefit.ppf(_uniform(seed, chunk, f"P|{i}|benefit", n))
        slip[i] = np.rint(u.start_slip.ppf(_uniform(seed, chunk, f"P|{i}|slip", n)))
        fail[i] = _uniform(seed, chunk, f"P|{i}|failure", n) > 1 - u.failure_prob
        price[i] = np.ones(n)
        for d in um.drivers:
            if d.effect in ("cost", "effort", "duration", "benefit", "start_delay") and selects_project(d.applies_to, p, data):
                f = _driver_factor(d, drv[d.driver_id])
                if d.effect == "cost":
                    price[i] = price[i] * f
                elif d.effect == "effort":
                    eff[i] = eff[i] * f
                elif d.effect == "duration":
                    dur[i] = dur[i] * f
                elif d.effect == "benefit":
                    ben[i] = ben[i] * f
                else:
                    slip[i] = slip[i] + np.rint(f)
    # ---- pool capacity factors
    pool_mult, pool_from = {}, {}
    for pid, pool in data.pools.items():
        u = um.pools[pid]
        a = u.availability.ppf(_uniform(seed, chunk, f"C|{pid}|availability", n))
        a = a * u.productivity.ppf(_uniform(seed, chunk, f"C|{pid}|productivity", n))
        for d in um.drivers:
            if d.effect == "availability" and selects_pool(d.applies_to, pool):
                a = a * _driver_factor(d, drv[d.driver_id])
        pool_mult[pid] = np.maximum(a, 0.0)
        pool_from[pid] = pool.start_month + np.rint(u.hiring_delay.ppf(_uniform(seed, chunk, f"C|{pid}|hiring", n)))
    rules = {r.rule_id: r for r in data.transfers}
    caps_of = {i: sorted({c for (c, _k) in P[i].demand if P[i].demand[(c, _k)] > 0}) for i in ids}
    W = {(i, c): sum(h for (cc, _k), h in P[i].demand.items() if cc == c) for i in ids for c in caps_of[i]}
    Wtot = {i: sum(W[i, c] for c in caps_of[i]) or 1.0 for i in ids}
    total = {(i, c): W[i, c] * eff[i] for i in ids for c in caps_of[i]}
    rem = {k: v.copy() for k, v in total.items()}
    rate = {(i, c): total[i, c] / np.maximum(1.0, P[i].duration * dur[i]) for i in ids for c in caps_of[i]}
    unit = {i: P[i].total_cost / Wtot[i] * price[i] for i in ids}
    INF = np.inf
    start_m = {i: np.full(n, -1.0) for i in ids}
    finish = {i: np.full(n, INF) for i in ids}
    plan_start = {i: ctx.starts[i] + slip[i] for i in ids}
    spend = {i: np.zeros(n) for i in ids}
    pv_spend = {i: np.zeros(n) for i in ids}
    spend_h = {}                       # (lob, source) -> spend within horizon
    contingency_hours = np.zeros(n)
    deferred = np.zeros(n)
    shortage_any = np.zeros(n, dtype=bool)
    caps_all = sorted({c for i in ids for c in caps_of[i]})
    shortage_cap = {c: np.zeros(n, dtype=bool) for c in caps_all}
    shortage_hours = {c: np.zeros(n) for c in caps_all}
    pool_ids = list(data.pools)
    cont_cap = {}
    if mode == "adaptive":
        for c in caps_all:
            cont_cap[c] = [st.contingency_pct * sum(pl.available_hours(t) for pl in data.pools.values()
                                                     if pl.capability == c) for t in range(1, T + 1)]
    disc = S.discount_rate
    # demand profile per capability (relative months) and the plan's own route split per relative month
    profile = {(i, c): np.array([P[i].demand.get((c, k), 0.0) for k in range(1, P[i].duration + 1)])
               for i in ids for c in caps_of[i]}
    peak = {k: (v.max() if v.size else 0.0) for k, v in profile.items()}
    split = ctx.plan_split

    def allocate(i, c, need, routes_frac, cost_acc):
        """Allocate `need` effective hours along routes; routes_frac = [(route, fraction of need)] or None."""
        targets = routes_frac if routes_frac is not None else [(r, None) for r in ctx.routes.get((i, c), [])]
        for r, frac in targets:
            want = need if frac is None else np.minimum(need, base_need[i, c] * frac)
            if not want.any():
                continue
            give = np.minimum(want / r.efficiency, pool_rem[r.pool_id])
            if r.rule_id:
                give = np.minimum(give, rule_rem.get(r.rule_id, 0.0))
                give = np.minimum(give, out_rem.get(r.pool_id, pool_rem[r.pool_id]))
            give = np.maximum(give, 0.0)
            if r.rule_id:
                rule_rem[r.rule_id] = rule_rem[r.rule_id] - give
                if r.pool_id in out_rem:
                    out_rem[r.pool_id] = out_rem[r.pool_id] - give
            pool_rem[r.pool_id] = pool_rem[r.pool_id] - give
            need = need - give * r.efficiency
            cost_acc[i] = cost_acc[i] + give * r.rate / 1000.0
        return np.maximum(need, 0.0)

    for t in range(1, T + 1):
        pool_rem = {}
        for pid in pool_ids:
            A = data.pools[pid].available_hours(t)      # capacity continues past the horizon unless it ends
            pool_rem[pid] = np.where(t >= pool_from[pid], A * pool_mult[pid], 0.0)
        rule_rem, out_rem = {}, {}
        retained = {}
        for r in data.transfers:
            retained[r.source_pool] = max(retained.get(r.source_pool, 0.0), r.min_retained_hours)
        for rid, r in rules.items():
            if not r.active(t):
                rule_rem[rid] = np.zeros(n)
                continue
            A = pool_rem[r.source_pool]
            capv = np.minimum(A * r.max_pct, np.maximum(0.0, A - r.min_retained_hours))
            if r.max_hours_per_month is not None:
                capv = np.minimum(capv, r.max_hours_per_month)
            rule_rem[rid] = np.maximum(capv, 0.0)
            out_rem[r.source_pool] = np.maximum(0.0, A - retained[r.source_pool])
        cont_rem = {c: np.full(n, cont_cap[c][t - 1]) for c in cont_cap}
        pv_t = float(_pv(disc, t))
        base_need, need_left, req_of, cost_acc = {}, {}, {}, {}
        active_of = {}
        # pass 1: start decisions (priority order) and allocation along the plan's own routes
        for i in ids:
            p = P[i]
            ready = (start_m[i] < 0) & (t >= plan_start[i])
            for j in p.depends_on:
                if j in finish:
                    ready &= finish[j] < t
            if mode == "adaptive" and not (p.mandatory or p.compliance or p.critical):
                gate_ok = np.ones(n, dtype=bool)
                for c in caps_of[i]:
                    free = np.zeros(n)
                    for r in ctx.routes.get((i, c), []):
                        free = free + pool_rem[r.pool_id] * r.efficiency
                    if c in cont_rem:
                        free = free + cont_rem[c]
                    gate_ok &= free >= st.start_gate * peak[i, c] * eff[i] / np.maximum(dur[i], 1e-9)
                overdue = t - plan_start[i] >= st.max_defer_months
                deferred += ready & ~gate_ok & ~overdue
                ready = ready & (gate_ok | overdue)
            start_m[i] = np.where(ready, t, start_m[i])
            active = (start_m[i] >= 0) & np.isinf(finish[i])
            active_of[i] = active
            cost_acc[i] = np.zeros(n)
            if not active.any():
                continue
            krel = np.maximum(1.0, t - start_m[i] + 1)
            for c in caps_of[i]:
                prof = profile[i, c]
                korig = np.clip(np.ceil(krel / np.maximum(dur[i], 1e-9)), 1, len(prof)).astype(int)
                base = prof[korig - 1] * eff[i] / np.maximum(dur[i], 1e-9)
                over = krel > P[i].duration * dur[i]          # past the stretched schedule: catch up
                base = np.where(over, peak[i, c] * eff[i], base)
                req = np.where(active, np.minimum(rem[i, c], base), 0.0)
                req_of[i, c] = req
                base_need[i, c] = req
                plan_routes = split.get((i, c)) if split else None
                if plan_routes:
                    k_mode = int(np.median(korig))
                    fr = plan_routes.get(k_mode) or next(iter(plan_routes.values()))
                    need_left[i, c] = allocate(i, c, req, fr, cost_acc)
                else:
                    need_left[i, c] = req
        # pass 2: whatever is still needed, from any allowed route (then contingency in adaptive mode)
        for i in ids:
            if not active_of[i].any():
                continue
            p = P[i]
            month_cost = cost_acc[i]
            for c in caps_of[i]:
                need = need_left[i, c]
                if need.any():
                    need = allocate(i, c, need, None, cost_acc)
                if mode == "adaptive" and c in cont_rem and need.any():
                    give = np.minimum(need, cont_rem[c])
                    cont_rem[c] = cont_rem[c] - give
                    need = need - give
                    contingency_hours += give
                    cost_acc[i] = cost_acc[i] + give * st.contingency_rate / 1000.0
                need = np.maximum(need, 0.0)
                delivered = req_of[i, c] - need
                rem[i, c] = rem[i, c] - delivered
                cost_acc[i] = cost_acc[i] + delivered * unit[i]      # spend follows delivered hours
                short = active_of[i] & (need > st.shortage_threshold_hours)
                shortage_cap[c] |= short
                shortage_any |= short
                shortage_hours[c] += np.where(active_of[i], need, 0.0)
            month_cost = cost_acc[i]
            spend[i] += month_cost
            pv_spend[i] += month_cost * pv_t
            if t <= H:
                for l, sh in p.shares().items():
                    key = (l, p.funding_source)
                    spend_h[key] = spend_h.get(key, np.zeros(n)) + sh * month_cost
            active = active_of[i]
            done = active & np.all([rem[i, c] <= 1e-6 * np.maximum(total[i, c], 1.0) for c in caps_of[i]], axis=0) \
                if caps_of[i] else active
            finish[i] = np.where(done, t, finish[i])
    # ---- benefits and outcomes
    value = np.zeros(n)
    npv = np.zeros(n)
    cost = np.zeros(n)
    completion = np.zeros(n)
    proj_npv, proj_val, proj_finish = {}, {}, {}
    for i in ids:
        p = P[i]
        u = um.projects[i]
        fin = finish[i]
        done = np.isfinite(fin)
        realize = ben[i] * np.where(fail[i], 1 - u.failure_loss, 1.0) * done
        e_min = p.earliest_start + p.duration - 1
        timing = np.clip(1 - S.schedule_discount * np.maximum(0, np.where(done, fin, T) - e_min) / H, 0.0, 1.0)
        v_i = p.value * realize * timing
        n_i = p.value * realize * _pv(disc, np.where(done, fin, T)) - pv_spend[i]
        value += v_i
        npv += n_i
        cost += spend[i]
        completion = np.maximum(completion, np.where(done, fin, T + 1))
        proj_npv[i], proj_val[i], proj_finish[i] = n_i, v_i, fin
    out = {"value": value, "npv": npv, "cost": cost, "completion": completion,
           "roi": (value - cost) / np.maximum(cost, 1e-9), "contingency_hours": contingency_hours,
           "deferred_months": deferred, "shortage_any": shortage_any}
    for c in caps_all:
        out[f"shortage_cap|{c}"] = shortage_cap[c]
        out[f"shortage_hours|{c}"] = shortage_hours[c]
    # budgets within horizon (total by LOB & funding source over the horizon)
    budget = {}
    for b in data.budgets:
        budget[b.lob_id, b.funding_source] = budget.get((b.lob_id, b.funding_source), 0.0) + b.amount
    breach = np.zeros(n, dtype=bool)
    for key, sp in spend_h.items():
        if key in budget:
            breach |= sp > budget[key] * (1 + st.budget_tolerance) + 1e-6
    out["lob_budget_breach"] = breach
    out["cost_overrun"] = cost > ctx.planned_cost * (1 + st.budget_tolerance) + 1e-6
    # key dates: deadlines, and planned finish (+grace) of commitments
    late_any = np.zeros(n, dtype=bool)
    key_projects = [i for i in ids if P[i].deadline or P[i].mandatory or P[i].compliance or P[i].critical]
    for i in key_projects:
        p = P[i]
        key_date = p.deadline if p.deadline else ctx.starts[i] + p.duration - 1 + st.date_grace_months
        late = ~(proj_finish[i] <= key_date)
        out[f"late|{i}"] = late
        late_any |= late
    out["late_any_key_date"] = late_any
    all_late = np.zeros(n)
    for i in ids:
        planned = ctx.starts[i] + P[i].duration - 1 + st.date_grace_months
        all_late += ~(proj_finish[i] <= planned)
    out["share_late"] = all_late / max(1, len(ids))
    # OKR targets (realized attributed benefit delivered within the horizon)
    met_all = np.ones(n, dtype=bool)
    plan_met = np.ones(n, dtype=bool)          # targets the deterministic plan itself meets
    targets = [t for t in data.targets if t.target and t.target > 0]
    for k, tg in enumerate(targets):
        got = np.zeros(n)
        for b in data.benefits:
            if b.lob_id == tg.lob_id and b.kpi == tg.kpi and b.project_id in finish:
                u = um.projects[b.project_id]
                ok = proj_finish[b.project_id] <= H
                got += b.attributed_benefit * ben[b.project_id] * np.where(fail[b.project_id], 1 - u.failure_loss, 1.0) * ok
        met = got >= tg.target
        out[f"okr|{tg.lob_id}|{tg.kpi}"] = met
        met_all &= met
        planned = sum(b.attributed_benefit for b in data.benefits if b.lob_id == tg.lob_id and b.kpi == tg.kpi
                      and b.project_id in ctx.starts and ctx.starts[b.project_id] + P[b.project_id].duration - 1 <= H)
        if planned >= tg.target:
            plan_met &= met
    out["okr_all"] = met_all if targets else np.ones(n, dtype=bool)
    out["okr_plan_targets"] = plan_met
    if keep_projects:
        for i in ids:
            out[f"pnpv|{i}"] = proj_npv[i]
            out[f"pfin|{i}"] = proj_finish[i]
        for i in ids:
            out[f"in|effort|{i}"] = eff[i]
            out[f"in|duration|{i}"] = dur[i]
            out[f"in|benefit|{i}"] = ben[i]
        for d in um.drivers:
            out[f"in|driver|{d.driver_id}"] = drv[d.driver_id]
    return out


def _chunk_job(args):
    ctx, um, chunk, n, keep = args
    return simulate_chunk(ctx, um, chunk, n, keep)


@dataclass
class SimulationRun:
    samples: Dict[str, np.ndarray]
    iterations: int
    settings: object
    context: PlanContext
    chunks: int
    seconds: float = 0.0
    throughput: float = 0.0   # iterations / second


def simulate(ctx: PlanContext, um: UncertaintyModel, keep_projects: bool = True) -> SimulationRun:
    import time
    st = um.settings
    N, cs = st.iterations, max(100, st.chunk_size)
    sizes = [cs] * (N // cs) + ([N % cs] if N % cs else [])
    jobs = [(ctx, um, k, n, keep_projects) for k, n in enumerate(sizes)]
    t0 = time.time()
    if st.workers > 1 and len(jobs) > 1:
        with cf.ProcessPoolExecutor(max_workers=st.workers) as ex:
            parts = list(ex.map(_chunk_job, jobs))
    else:
        parts = [_chunk_job(j) for j in jobs]
    keys = parts[0].keys()
    samples = {k: np.concatenate([p[k] for p in parts]) for k in keys}
    secs = time.time() - t0
    return SimulationRun(samples, N, st, ctx, len(sizes), secs, N / secs if secs > 0 else float("inf"))
