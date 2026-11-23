"""Independent feasibility validation.

This module re-checks a solution against the *business rules in the domain data*, without using the
optimizer's model, channels or constraint code. A solution passes only if every rule holds within a
small numerical tolerance:

  windows      start/finish inside earliest start, deadline, horizon and any start-by commitment
  scope        excluded / out-of-scope / unfundable initiatives are not selected
  commitments  mandatory, protected critical/compliance and forced initiatives are selected
  dependencies every prerequisite is selected and finishes before the dependent starts
  demand       each selected initiative receives exactly its required effective hours, month by month,
               and no hours go to unselected initiatives or months without demand
  routes       every hour comes from a pool that may serve it: direct eligibility (ownership, eligibility
               rules, funding restrictions), an allowed transfer rule (authorized for the tier, active
               that month), or — only in the Unconstrained tier — a relaxed route
  capacity     hours drawn from each pool in each month <= A[p,t]
  transfers    per-rule ceilings and the owner's retained hours
  budgets      LOB spend by funding source (project costs by share + transfer/incremental costs) within
               budget, cumulative where carry-forward applies, monthly otherwise
  governance   LOB targets, risk tolerance, maturity, concentration and minimum investment shares
  objective    the reported objective equals the objective recomputed from the solution
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..domain import ANY, ApprovalStatus, CapacityType, PortfolioData, PortfolioTier


@dataclass
class Check:
    name: str
    passed: bool
    checked: int = 0
    worst: float = 0.0
    details: List[str] = field(default_factory=list)


@dataclass
class ValidationReport:
    checks: List[Check]
    objective_reported: Optional[float] = None
    objective_recomputed: Optional[float] = None

    @property
    def feasible(self) -> bool:
        return all(c.passed for c in self.checks if c.name != "objective")

    @property
    def objective_consistent(self) -> Optional[bool]:
        c = next((c for c in self.checks if c.name == "objective"), None)
        return None if c is None else c.passed

    def failures(self) -> List[str]:
        return [f"{c.name}: {d}" for c in self.checks if not c.passed for d in (c.details or ["failed"])]

    def summary(self) -> Dict[str, object]:
        return {"feasible": self.feasible, "objective_consistent": self.objective_consistent,
                "objective_reported": self.objective_reported, "objective_recomputed": self.objective_recomputed,
                "checks": {c.name: {"passed": c.passed, "checked": c.checked, "worst_violation": c.worst,
                                    "details": c.details[:5]} for c in self.checks}}


def _avail(pool, t: int) -> float:
    if t < pool.start_month or (pool.end_month is not None and t > pool.end_month):
        return 0.0
    return pool.fte * pool.hours_per_fte * pool.utilization * pool.month_factor.get(t, 1.0)


def _sub_eff(data: PortfolioData, supply: str, demand: str) -> float:
    if supply == demand:
        return 1.0
    for s in data.substitutions:
        if s.from_capability == supply and s.to_capability == demand:
            return s.efficiency
    return 0.0


def _direct_ok(data: PortfolioData, pool, p) -> bool:
    if pool.allowed_funding_sources and p.funding_source not in pool.allowed_funding_sources:
        return False
    rows = [e for e in data.eligibility if e.pool_id == pool.pool_id]

    def type_ok(rt):
        return rt in ("", ANY) or rt == p.project_type

    def match(e):
        if e.authorized_project_id:
            return e.authorized_project_id == p.project_id and type_ok(e.eligible_project_type)
        return e.eligible_lob in (ANY, p.primary_lob) and type_ok(e.eligible_project_type)

    if pool.capacity_type == CapacityType.SHARED:
        return not rows or any(match(e) for e in rows)
    return p.primary_lob == pool.owning_lob or any(match(e) for e in rows)


def _rule_ok_for_tier(rule, tier: PortfolioTier, strict: bool) -> bool:
    if strict:
        return False
    if tier == PortfolioTier.COMMITTED:
        return (not rule.approval_required) or rule.approval_status == ApprovalStatus.APPROVED
    if tier == PortfolioTier.CONDITIONAL:
        return rule.approval_status != ApprovalStatus.REJECTED
    return True


def _rule_cap(rule, A: float) -> float:
    cap = min(A * rule.max_pct, max(0.0, A - rule.min_retained_hours))
    if rule.max_hours_per_month is not None:
        cap = min(cap, rule.max_hours_per_month)
    return max(0.0, cap)


def validate_solution(data: PortfolioData, opts, starts: Dict[str, int], allocations,
                      reported_objective: Optional[float] = None, tol: float = 1e-4,
                      check_objective: bool = True) -> ValidationReport:
    P, s, H = data.projects, data.settings, data.settings.horizon
    tier = opts.tier
    checks: List[Check] = []

    def chk(name):
        c = Check(name, True)
        checks.append(c)
        return c

    def fail(c: Check, msg: str, amount: float = 0.0):
        c.passed = False
        c.worst = max(c.worst, amount)
        if len(c.details) < 20:
            c.details.append(msg)

    def end(i):
        return starts[i] + P[i].duration - 1

    # windows
    c = chk("windows")
    for i, st in starts.items():
        c.checked += 1
        p = P.get(i)
        if p is None:
            fail(c, f"{i} is not an initiative in the data")
            continue
        last = min(H, p.deadline or H)
        if st < max(1, p.earliest_start):
            fail(c, f"{i} starts {st} before earliest start {p.earliest_start}", p.earliest_start - st)
        if end(i) > last:
            fail(c, f"{i} finishes {end(i)} after {last}", end(i) - last)
        if i in opts.start_by and st > opts.start_by[i]:
            fail(c, f"{i} starts {st} after start-by {opts.start_by[i]}", st - opts.start_by[i])
    # scope
    c = chk("scope")
    for i in starts:
        c.checked += 1
        if i in opts.force_exclude:
            fail(c, f"{i} is excluded but selected")
        if opts.candidate_projects is not None and i not in opts.candidate_projects:
            fail(c, f"{i} is outside the candidate set")
        p = P.get(i)
        if p is None:
            continue
        for l in p.shares():
            lines = [b for b in data.budgets if b.lob_id == l and b.funding_source == p.funding_source]
            types = sorted({t for b in lines for t in b.allowed_project_types})
            if not lines:
                fail(c, f"{i}: no {p.funding_source} budget line for {l}")
            elif types and p.project_type not in types:
                fail(c, f"{i}: {p.project_type} not fundable from {l} {p.funding_source}")
    if opts.fixed_starts is not None:
        for i, st in opts.fixed_starts.items():
            c.checked += 1
            if starts.get(i) != st:
                fail(c, f"{i} should start {st} under the fixed plan")
        for i in starts:
            if i not in opts.fixed_starts:
                fail(c, f"{i} selected but not in the fixed plan")
    # commitments
    c = chk("commitments")
    pc = s.protect_compliance if opts.protect_compliance is None else opts.protect_compliance
    pk = s.protect_critical if opts.protect_critical is None else opts.protect_critical
    must = set(opts.force_select) | set(opts.start_by)
    for i, p in P.items():
        if opts.candidate_projects is not None and i not in opts.candidate_projects:
            continue
        if p.mandatory or (pk and p.critical) or (pc and p.compliance):
            must.add(i)
    for i in sorted(must):
        c.checked += 1
        if i not in starts and not opts.soft:
            fail(c, f"commitment {i} not selected")
    # dependencies
    c = chk("dependencies")
    for j in starts:
        for i in P[j].depends_on:
            if i not in P:
                continue
            if opts.candidate_projects is not None and i not in opts.candidate_projects \
                    and opts.assume_external_dependencies:
                continue
            c.checked += 1
            if i not in starts:
                fail(c, f"{j} selected without prerequisite {i}")
            elif end(i) >= starts[j]:
                fail(c, f"{j} starts {starts[j]} before {i} finishes ({end(i)})", end(i) - starts[j] + 1)
    # demand
    c = chk("demand")
    got: Dict[Tuple[str, str, int], float] = defaultdict(float)
    for a in allocations:
        got[a.project_id, a.capability, a.month] += a.effective_hours
    need: Dict[Tuple[str, str, int], float] = {}
    for i, st in starts.items():
        for (cap, k), h in P[i].demand.items():
            if h > 0:
                need[i, cap, st + k - 1] = need.get((i, cap, st + k - 1), 0.0) + h
    for key in set(need) | set(got):
        c.checked += 1
        n, g = need.get(key, 0.0), got.get(key, 0.0)
        if abs(n - g) > tol * max(1.0, n) + 1e-3:
            fail(c, f"{key[0]} {key[1]} month {key[2]}: needs {n:.2f} h, receives {g:.2f} h", abs(n - g))
    # routes
    c = chk("routes")
    rules = {r.rule_id: r for r in data.transfers}
    for a in allocations:
        c.checked += 1
        pool, p = data.pools.get(a.pool_id), P.get(a.project_id)
        if pool is None or p is None:
            fail(c, f"allocation references unknown pool/project {a.pool_id}/{a.project_id}")
            continue
        if _avail(pool, a.month) <= 0:
            fail(c, f"{a.pool_id} has no capacity in month {a.month}", a.hours)
        sub = _sub_eff(data, pool.capability, a.capability)
        if sub <= 0:
            fail(c, f"{a.pool_id} ({pool.capability}) cannot supply {a.capability}", a.hours)
            continue
        if a.kind == "direct":
            if not _direct_ok(data, pool, p):
                fail(c, f"{a.pool_id} may not serve {a.project_id} directly", a.hours)
            eff = sub
        elif a.kind == "transfer":
            r = rules.get(a.rule_id)
            if r is None or r.source_pool != a.pool_id:
                fail(c, f"{a.project_id}: transfer via unknown/mismatched rule {a.rule_id}", a.hours)
                continue
            if not (r.dest_lob == p.primary_lob or (r.dest_lob == ANY and p.primary_lob != r.source_lob)):
                fail(c, f"rule {r.rule_id} does not cover {p.primary_lob}", a.hours)
            if not _rule_ok_for_tier(r, tier, opts.strict_ring_fence):
                fail(c, f"rule {r.rule_id} ({r.approval_status.value}) not allowed in the {tier.value} tier", a.hours)
            if a.month < r.effective_start or (r.effective_end is not None and a.month > r.effective_end):
                fail(c, f"rule {r.rule_id} not active in month {a.month}", a.hours)
            if pool.allowed_funding_sources and p.funding_source not in pool.allowed_funding_sources:
                fail(c, f"{a.pool_id} funding restriction excludes {p.funding_source}", a.hours)
            eff = sub * r.efficiency
        elif a.kind == "relaxed":
            if tier != PortfolioTier.UNCONSTRAINED:
                fail(c, f"relaxed ring-fence route used outside the Unconstrained tier", a.hours)
            eff = sub
        else:
            fail(c, f"unknown route kind {a.kind}")
            continue
        if abs(a.effective_hours - a.hours * eff) > tol * max(1.0, a.hours):
            fail(c, f"{a.project_id}: effective hours {a.effective_hours:.2f} != {a.hours:.2f} x {eff:.3f}")
        if a.hours < -tol:
            fail(c, f"negative hours on {a.pool_id}", -a.hours)
    # capacity
    c = chk("capacity")
    used: Dict[Tuple[str, int], float] = defaultdict(float)
    for a in allocations:
        used[a.pool_id, a.month] += a.hours
    reservation = s.enterprise_reservation_pct if opts.enterprise_reservation_pct is None else opts.enterprise_reservation_pct
    non_ent: Dict[Tuple[str, int], float] = defaultdict(float)
    for a in allocations:
        if not P[a.project_id].shared_platform:
            non_ent[a.pool_id, a.month] += a.hours
    for (pl, t), h in used.items():
        c.checked += 1
        A = _avail(data.pools[pl], t)
        if h > A + tol * max(1.0, A) + 1e-3:
            fail(c, f"{pl} month {t}: {h:.1f} h drawn, {A:.1f} h available", h - A)
        if reservation > 0 and data.pools[pl].capacity_type == CapacityType.SHARED:
            lim = (1 - reservation) * A
            if non_ent[pl, t] > lim + tol * max(1.0, A) + 1e-3:
                fail(c, f"{pl} month {t}: enterprise reservation breached", non_ent[pl, t] - lim)
    # transfers
    c = chk("transfers")
    per_rule: Dict[Tuple[str, int], float] = defaultdict(float)
    per_pool_out: Dict[Tuple[str, int], float] = defaultdict(float)
    retained: Dict[Tuple[str, int], float] = defaultdict(float)
    for a in allocations:
        if a.kind == "transfer" and a.rule_id in rules:
            per_rule[a.rule_id, a.month] += a.hours
            per_pool_out[a.pool_id, a.month] += a.hours
            retained[a.pool_id, a.month] = max(retained[a.pool_id, a.month], rules[a.rule_id].min_retained_hours)
    for (rid, t), h in per_rule.items():
        c.checked += 1
        r = rules[rid]
        cap = _rule_cap(r, _avail(data.pools[r.source_pool], t))
        if h > cap + tol * max(1.0, cap) + 1e-3:
            fail(c, f"rule {rid} month {t}: {h:.1f} h > ceiling {cap:.1f} h", h - cap)
    for (pl, t), h in per_pool_out.items():
        c.checked += 1
        lim = max(0.0, _avail(data.pools[pl], t) - retained[pl, t])
        if h > lim + tol * max(1.0, lim) + 1e-3:
            fail(c, f"{pl} month {t}: owner retained hours breached", h - lim)
    # budgets
    c = chk("budgets")
    spend: Dict[Tuple[str, str], Dict[int, float]] = defaultdict(lambda: defaultdict(float))
    for i, st in starts.items():
        p = P[i]
        for l, sh in p.shares().items():
            for k, cost in p.cost.items():
                spend[l, p.funding_source][st + k - 1] += sh * cost
    for a in allocations:
        extra = a.transfer_cost + a.incremental_cost
        if extra:
            p = P[a.project_id]
            for l, sh in p.shares().items():
                spend[l, p.funding_source][a.month] += sh * extra
    budget_lobs = opts.budget_lobs if opts.budget_lobs is not None else set(data.lobs)
    for (l, f), ser in spend.items():
        if l not in budget_lobs:
            continue
        lines = [b for b in data.budgets if b.lob_id == l and b.funding_source == f]
        carry = all(b.carry_forward for b in lines) if lines else True
        avail = defaultdict(float)
        for b in lines:
            avail[b.month] += b.amount
        cum_s = cum_a = 0.0
        for t in range(1, H + 1):
            c.checked += 1
            cum_s += ser.get(t, 0.0)
            cum_a += avail.get(t, 0.0)
            used_, lim = (cum_s, cum_a) if carry else (ser.get(t, 0.0), avail.get(t, 0.0))
            if used_ > lim + tol * max(1.0, lim) + 1e-3:
                fail(c, f"{l} {f} month {t}: spend {used_:.1f} > budget {lim:.1f} ($K)", used_ - lim)
        beyond = sum(v for t, v in ser.items() if t > H)
        if beyond > 1e-6:
            fail(c, f"{l} {f}: {beyond:.1f} $K spent after the horizon", beyond)
    # governance
    c = chk("governance")
    enforce = s.enforce_lob_targets if opts.enforce_lob_targets is None else opts.enforce_lob_targets
    inv = defaultdict(float)
    for i in starts:
        for l, sh in P[i].shares().items():
            inv[l] += sh * P[i].total_cost
    total_inv = sum(inv.values())
    if enforce:
        for t in data.targets:
            if opts.lob_focus not in (None, t.lob_id):
                continue
            c.checked += 1
            if t.min_outcome:
                got_o = sum(b.attributed_benefit for b in data.benefits
                            if b.lob_id == t.lob_id and b.kpi == t.kpi and b.project_id in starts)
                if got_o < t.min_outcome - 1e-3 and not opts.soft:
                    fail(c, f"{t.lob_id} {t.kpi}: outcome {got_o:.0f} < floor {t.min_outcome:.0f}", t.min_outcome - got_o)
            if t.min_investment and inv[t.lob_id] < t.min_investment - 1e-3 and not opts.soft:
                fail(c, f"{t.lob_id}: investment below minimum", t.min_investment - inv[t.lob_id])
            if t.max_investment and inv[t.lob_id] > t.max_investment + 1e-3:
                fail(c, f"{t.lob_id}: investment above maximum", inv[t.lob_id] - t.max_investment)
        for l in data.business_lobs():
            if opts.lob_focus not in (None, l):
                continue
            lob = data.lobs[l]
            mine = [i for i in starts if P[i].primary_lob == l]
            if lob.risk_tolerance is not None and mine:
                c.checked += 1
                avg = sum(P[i].execution_risk for i in mine) / len(mine)
                if avg > lob.risk_tolerance + 1e-6:
                    fail(c, f"{l}: average execution risk {avg:.3f} > tolerance {lob.risk_tolerance:.3f}",
                         avg - lob.risk_tolerance)
            if lob.maturity_target:
                c.checked += 1
                mat = sum(P[i].maturity_gain for i in starts if P[i].primary_lob == l or l in P[i].secondary_lobs)
                if mat < lob.maturity_target - 1e-6 and not opts.soft:
                    fail(c, f"{l}: maturity gain {mat:.1f} < target {lob.maturity_target:.1f}", lob.maturity_target - mat)
    if s.max_lob_concentration and opts.lob_focus is None:
        for l in data.business_lobs():
            c.checked += 1
            if inv[l] > s.max_lob_concentration * total_inv + 1e-3:
                fail(c, f"{l}: concentration above {s.max_lob_concentration:.0%}")
    if opts.enforce_min_investment_share and opts.lob_focus is None:
        for l, share in s.min_investment_share.items():
            c.checked += 1
            if inv[l] < share * total_inv - 1e-3 and not opts.soft:
                fail(c, f"{l}: investment share below {share:.0%}")
    # objective
    recomputed = None
    if check_objective and reported_objective is not None and opts.objective in ("enterprise", "lob") \
            and not opts.soft:
        from ..optimizer import EPS_HOURS, STRATEGIC_TB
        c = chk("objective")
        c.checked = 1
        tot = 0.0
        for i, st in starts.items():
            p = P[i]
            e_min = p.earliest_start + p.duration - 1
            disc = 1 - s.schedule_discount * max(0, end(i) - e_min) / H
            base = data.lob_benefit(i, opts.lob_focus) if (opts.objective == "lob" and opts.lob_focus) else p.value
            tot += base * opts.value_multipliers.get(i, 1.0) * disc + STRATEGIC_TB * (p.maturity_gain + p.risk_reduction)
        for a in allocations:
            tot -= a.transfer_cost + a.incremental_cost + EPS_HOURS * a.hours
        recomputed = tot
        if abs(tot - reported_objective) > 1e-6 * max(1.0, abs(tot)) + 1e-3:
            fail(c, f"reported {reported_objective:.4f} vs recomputed {tot:.4f}", abs(tot - reported_objective))
    return ValidationReport(checks, reported_objective, recomputed)


def validate_result(res, check_objective: bool = True) -> ValidationReport:
    """Validate a PortfolioResult against its own data and options."""
    return validate_solution(res.data, res.options, res.starts, res.allocations,
                             res.objective if res.feasible else None, check_objective=check_objective)
