"""OR-Tools CP-SAT: an independent, integer reformulation of the discrete scheduling core.

CP-SAT works on integers only, so the portfolio formulation is restated with:

- ``x[i,s]``  boolean start decisions (identical to the MILP);
- ``y[ch,t]`` *integer* hours per allocation channel and month;
- demand:     ``sum floor(100*eff) * y >= ceil(100*req)``  (an over-supply is harmless: in the
              continuous model hours can always be trimmed back to the requirement);
- capacity:   ``sum y <= floor(A)``; transfer ceilings and retained hours likewise floored;
- budgets:    money in units of $0.1 (1e4 per $K), costs rounded *up*, budgets rounded *down*;
- objective:  value in units of $1 (1e3 per $K), rounded.

Every rounding is conservative, so a CP-SAT plan is always feasible for the continuous model. The
plan's selection and start months are then re-evaluated exactly by HiGHS with the starts fixed (an
LP), which gives the comparable objective and the allocations the independent validator checks.

Compatibility: CP-SAT is used only where this reformulation is exact up to the stated roundings.
Soft (diagnostic) mode, the balanced and coverage objectives, minimum shared-capacity shares,
custom/Pareto objectives and models above `max_int_vars` are reported as INCOMPATIBLE, with the
reason, rather than solved approximately.
"""
from __future__ import annotations

import dataclasses
import math
import time
from typing import Dict, List, Tuple

from .base import Engine, NormalizedResult, SolverSettings, SolveStatus, rel_gap

OBJ_SCALE = 1000.0      # objective: $K -> $1
MONEY_SCALE = 1e4       # budgets: $K -> $0.1
EFF_SCALE = 100         # efficiencies: percent


def compatibility(compiled, max_int_vars: int = 60000) -> Tuple[bool, str]:
    m, opts, s = compiled.model, compiled.opts, compiled.data.settings
    if getattr(compiled, "custom_objective", False):
        return False, "custom (Pareto/stochastic) objective: CP-SAT validates the base formulation only"
    if opts.soft:
        return False, "soft diagnostic mode uses continuous penalty slacks"
    if opts.objective not in ("enterprise", "lob"):
        return False, f"'{opts.objective}' objective uses auxiliary ratio/coverage variables"
    if s.min_shared_share:
        return False, "minimum shared-capacity shares are ratio constraints over hours"
    if opts.keep_starts is not None or opts.objective_floor is not None:
        return False, "plan-stability / objective-floor solves are MILP-specific"
    n_y = sum(len(v) for v in m.ch_months.values())
    if len(m.x) + n_y > max_int_vars:
        return False, f"{len(m.x) + n_y:,} integer variables exceeds the CP-SAT limit of {max_int_vars:,}"
    if compiled.issues:
        return False, "; ".join(compiled.issues)
    return True, "compatible"


class CpSatEngine(Engine):
    name = "CP-SAT"
    kind = "cp"
    role = "discrete scheduling and constraint optimization"

    def __init__(self, workers: int = 8, max_int_vars: int = 60000):
        self.workers = workers
        self.max_int_vars = max_int_vars

    @classmethod
    def available(cls) -> bool:
        try:
            from ortools.sat.python import cp_model  # noqa: F401
            return True
        except Exception:
            return False

    def _solve(self, compiled, settings: SolverSettings) -> NormalizedResult:
        ok, why = compatibility(compiled, self.max_int_vars)
        if not ok:
            return NormalizedResult(self.name, SolveStatus.INCOMPATIBLE, message=why)
        from ortools.sat.python import cp_model
        from ..domain import CapacityType
        from ..optimizer import rule_cap
        m, data, opts = compiled.model, compiled.data, compiled.opts
        P, s = data.projects, data.settings
        cp = cp_model.CpModel()
        X = {k: cp.NewBoolVar(f"x_{k[0]}_{k[1]}") for k in m.x}
        Y = {}
        for ch in m.channels:
            for t in m.ch_months[ch.cid]:
                Y[ch.cid, t] = cp.NewIntVar(0, int(math.floor(m.A[ch.pool_id, t])), f"y_{ch.cid}_{t}")
        sel = {i: [X[(i, st)] for st in m.S[i] if (i, st) in X] for i in m.candidates}
        for i, xs in sel.items():
            if xs:
                cp.Add(sum(xs) <= 1)
        if opts.fixed_starts is not None:
            for (i, st), v in X.items():
                cp.Add(v == (1 if opts.fixed_starts.get(i) == st else 0))
        # commitments
        must = set(opts.force_select) | set(opts.start_by)
        pc = s.protect_compliance if opts.protect_compliance is None else opts.protect_compliance
        pk = s.protect_critical if opts.protect_critical is None else opts.protect_critical
        for i in m.candidates:
            p = P[i]
            if p.mandatory or (pk and p.critical) or (pc and p.compliance):
                must.add(i)
        for i in must:
            if i in sel and sel[i]:
                cp.Add(sum(sel[i]) == 1)
        # capacity
        by_pool: Dict[Tuple[str, int], List] = {}
        for ch in m.channels:
            for t in m.ch_months[ch.cid]:
                by_pool.setdefault((ch.pool_id, t), []).append(ch)
        reservation = s.enterprise_reservation_pct if opts.enterprise_reservation_pct is None else opts.enterprise_reservation_pct
        for (pl, t), chs in by_pool.items():
            cp.Add(sum(Y[c.cid, t] for c in chs) <= int(math.floor(m.A[pl, t])))
            if reservation > 0 and data.pools[pl].capacity_type == CapacityType.SHARED:
                non_ent = [c for c in chs if not P[c.project_id].shared_platform]
                if non_ent:
                    cp.Add(sum(Y[c.cid, t] for c in non_ent) <= int(math.floor((1 - reservation) * m.A[pl, t])))
        # demand (>=, conservative)
        by_dem: Dict[Tuple[str, str, int], List] = {}
        for ch in m.channels:
            for t in m.ch_months[ch.cid]:
                by_dem.setdefault((ch.project_id, ch.capability, t), []).append(ch)
        for i in m.candidates:
            p = P[i]
            for cap in p.capabilities:
                for t in m.months:
                    terms = [(X[(i, st)], p.demand[(cap, t - st + 1)]) for st in m.S[i]
                             if (i, st) in X and p.demand.get((cap, t - st + 1), 0.0) > 0]
                    if not terms:
                        continue
                    chs = by_dem.get((i, cap, t), [])
                    req = sum(int(math.ceil(EFF_SCALE * h)) * v for v, h in terms)
                    if not chs:
                        cp.Add(req == 0)
                        continue
                    cp.Add(sum(int(math.floor(EFF_SCALE * c.efficiency)) * Y[c.cid, t] for c in chs) >= req)
        # transfers
        rules = {r.rule_id: r for r in data.transfers}
        by_rule: Dict[Tuple[str, int], List] = {}
        for ch in m.channels:
            if ch.rule_id:
                for t in m.ch_months[ch.cid]:
                    by_rule.setdefault((ch.rule_id, t), []).append(ch)
        for (rid, t), chs in by_rule.items():
            r = rules[rid]
            cp.Add(sum(Y[c.cid, t] for c in chs) <= int(math.floor(rule_cap(r, m.A[r.source_pool, t]))))
        by_pool_x: Dict[Tuple[str, int], List] = {}
        for (rid, t), chs in by_rule.items():
            by_pool_x.setdefault((rules[rid].source_pool, t), []).extend(chs)
        for (pl, t), chs in by_pool_x.items():
            retained = max([rules[c.rule_id].min_retained_hours for c in chs] + [0.0])
            cp.Add(sum(Y[c.cid, t] for c in chs) <= int(math.floor(max(0.0, m.A[pl, t] - retained))))
        # budgets (money scaled; costs up, budgets down)
        budget_lobs = opts.budget_lobs if opts.budget_lobs is not None else set(data.lobs)
        spend: Dict[Tuple[str, str], Dict[int, list]] = {}
        for i in m.candidates:
            p = P[i]
            for l, sh in p.shares().items():
                ser = spend.setdefault((l, p.funding_source), {t: [] for t in m.months})
                for st in m.S[i]:
                    if (i, st) not in X:
                        continue
                    for k, c in p.cost.items():
                        t = st + k - 1
                        if t in ser and c:
                            ser[t].append(int(math.ceil(sh * c * MONEY_SCALE)) * X[(i, st)])
        for ch in m.channels:
            rate = (ch.transfer_cost_rate + ch.incremental_cost_rate) / 1000.0
            if rate <= 0:
                continue
            p = P[ch.project_id]
            for l, sh in p.shares().items():
                ser = spend.setdefault((l, p.funding_source), {t: [] for t in m.months})
                for t in m.ch_months[ch.cid]:
                    ser[t].append(int(math.ceil(sh * rate * MONEY_SCALE)) * Y[ch.cid, t])
        for (l, f), ser in spend.items():
            if l not in budget_lobs:
                continue
            carry = data.budget_mode(l, f)
            avail = {t: sum(b.amount for b in data.budgets if b.lob_id == l and b.funding_source == f and b.month == t)
                     for t in m.months}
            for t in m.months:
                if carry:
                    lhs = [e for u in m.months if u <= t for e in ser[u]]
                    rhs = sum(avail[u] for u in m.months if u <= t)
                else:
                    lhs, rhs = ser[t], avail[t]
                if lhs:
                    cp.Add(sum(lhs) <= int(math.floor(rhs * MONEY_SCALE + 1e-6)))
        # dependencies
        for j in m.candidates:
            for i in P[j].depends_on:
                if i not in sel:
                    continue
                if sel[j]:
                    cp.Add(sum(sel[j]) <= sum(sel[i]) if sel[i] else sum(sel[j]) == 0)
                for sj in m.S[j]:
                    if (j, sj) not in X:
                        continue
                    pre = [X[(i, si)] for si in m.S[i] if (i, si) in X and si + P[i].duration <= sj]
                    cp.Add(X[(j, sj)] <= sum(pre)) if pre else cp.Add(X[(j, sj)] == 0)
        # governance (linear in selection)
        enforce = s.enforce_lob_targets if opts.enforce_lob_targets is None else opts.enforce_lob_targets
        def selsum(i):
            return sum(sel[i]) if sel.get(i) else 0
        inv = {l: [(P[i].shares().get(l, 0.0) * P[i].total_cost, i) for i in m.candidates] for l in data.lobs}
        if enforce:
            for t in data.targets:
                if opts.lob_focus not in (None, t.lob_id):
                    continue
                if t.min_outcome:
                    cp.Add(sum(int(round(b.attributed_benefit * 100)) * selsum(b.project_id) for b in data.benefits
                               if b.lob_id == t.lob_id and b.kpi == t.kpi and b.project_id in sel)
                           >= int(math.ceil(t.min_outcome * 100)))
                if t.min_investment:
                    cp.Add(sum(int(math.floor(c * 100)) * selsum(i) for c, i in inv[t.lob_id])
                           >= int(math.ceil(t.min_investment * 100)))
                if t.max_investment:
                    cp.Add(sum(int(math.ceil(c * 100)) * selsum(i) for c, i in inv[t.lob_id])
                           <= int(math.floor(t.max_investment * 100)))
            for l in data.business_lobs():
                lob = data.lobs[l]
                if opts.lob_focus not in (None, l):
                    continue
                if lob.risk_tolerance is not None:
                    cp.Add(sum(int(round((P[i].execution_risk - lob.risk_tolerance) * 10000)) * selsum(i)
                               for i in m.candidates if P[i].primary_lob == l) <= 0)
                if lob.maturity_target:
                    cp.Add(sum(int(round(P[i].maturity_gain * 100)) * selsum(i) for i in m.candidates
                               if P[i].primary_lob == l or l in P[i].secondary_lobs)
                           >= int(math.ceil(lob.maturity_target * 100)))
        total = [(c, i) for l in data.lobs for c, i in inv[l]]
        if s.max_lob_concentration and opts.lob_focus is None:
            for l in data.business_lobs():
                cp.Add(sum(int(round(c * 100)) * selsum(i) for c, i in inv[l])
                       <= sum(int(round(s.max_lob_concentration * c * 100)) * selsum(i) for c, i in total))
        if opts.enforce_min_investment_share and opts.lob_focus is None:
            for l, share in s.min_investment_share.items():
                if l in inv:
                    cp.Add(sum(int(round(c * 100)) * selsum(i) for c, i in inv[l])
                           >= sum(int(round(share * c * 100)) * selsum(i) for c, i in total))
        # objective (same weights as the MILP, scaled)
        from ..optimizer import EPS_HOURS, STRATEGIC_TB
        H = s.horizon
        obj = []
        for (i, st), v in X.items():
            p = P[i]
            e_min = p.earliest_start + p.duration - 1
            disc = 1 - s.schedule_discount * max(0, (st + p.duration - 1) - e_min) / H
            base = data.lob_benefit(i, opts.lob_focus) if (opts.objective == "lob" and opts.lob_focus) else p.value
            w = base * opts.value_multipliers.get(i, 1.0) * disc + STRATEGIC_TB * (p.maturity_gain + p.risk_reduction)
            obj.append(int(round(w * OBJ_SCALE)) * v)
        for ch in m.channels:
            rate = (ch.transfer_cost_rate + ch.incremental_cost_rate) / 1000.0 + EPS_HOURS
            k = int(round(rate * OBJ_SCALE))
            if k:
                obj.extend(-k * Y[ch.cid, t] for t in m.ch_months[ch.cid])
        cp.Maximize(sum(obj))
        if settings.warm_start:
            for (i, st), v in X.items():
                cp.AddHint(v, int(round(settings.warm_start.get(f"x_{i}_{st}", 0.0))))
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = float(settings.time_limit)
        solver.parameters.relative_gap_limit = float(settings.mip_gap)
        solver.parameters.num_workers = int(self.workers)
        solver.parameters.random_seed = int(settings.seed) % 2147483647
        t0 = time.time()
        code = solver.Solve(cp)
        rt = time.time() - t0
        name = solver.StatusName(code)
        if code == cp_model.INFEASIBLE:
            return NormalizedResult(self.name, SolveStatus.INFEASIBLE, runtime=rt, message=name)
        if code not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            st = SolveStatus.TIMEOUT if code == cp_model.UNKNOWN else SolveStatus.ERROR
            return NormalizedResult(self.name, st, runtime=rt, message=name)
        starts = {i: st for (i, st), v in X.items() if solver.Value(v)}
        cp_obj = solver.ObjectiveValue() / OBJ_SCALE
        cp_bound = solver.BestObjectiveBound() / OBJ_SCALE
        cp_gap = rel_gap(cp_obj, cp_bound)
        # exact re-evaluation of the CP-SAT schedule in the continuous model
        from .compiled import compile_portfolio
        from .milp import HighsEngine
        fixed = compile_portfolio(data, dataclasses.replace(opts, fixed_starts=dict(starts), candidate_starts=[]))
        ev = HighsEngine().solve(fixed, dataclasses.replace(settings, warm_start=None))
        st = SolveStatus.OPTIMAL if (code == cp_model.OPTIMAL or (cp_gap is not None and cp_gap <= settings.mip_gap)) \
            else SolveStatus.FEASIBLE
        if not ev.status.has_solution:
            return NormalizedResult(self.name, SolveStatus.ERROR, runtime=rt,
                                    message=f"CP-SAT plan could not be re-evaluated exactly: {ev.message}")
        # Rounding makes CP-SAT's bound approximate; report the gap between its own objective and bound,
        # and the exactly re-evaluated objective as the comparable value.
        return NormalizedResult(self.name, st, ev.objective, cp_bound, cp_gap, rt, ev.values, name,
                                extra={"cp_objective_scaled": cp_obj, "integer_vars": len(X) + len(Y),
                                       "bound_note": "bound from integer-scaled model (approximate)"})
