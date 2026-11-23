"""LOB-aware, time-indexed MILP for portfolio selection, scheduling and capacity allocation.

Decision variables
------------------
x[i,s]      binary   project i starts in month s
y[ch,t]     >= 0     hours drawn in month t through allocation channel ch. A channel is a
                     (pool p, project i, demanded capability c, route) tuple, so y[ch,t] is the
                     y[p,i,t] of the specification, split by capability and route.
z[p,l,t]    expr     hours of pool p transferred to LOB l in month t (sum of transfer-route y)

Parameters
----------
A[p,t]      effective available hours of pool p in month t (0 before start / after end)
E[p,i]      1 if pool p may serve project i directly (ownership, eligibility, authorization,
            pool funding restrictions)

Every hour, whatever its route (direct, transfer, release, substitution), is drawn from exactly
one source pool's A[p,t]; this is the "no double allocation" guarantee.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

import pulp

from .domain import ANY, ApprovalStatus, CapacityPool, CapacityType, PortfolioData, PortfolioTier, Project

EPS_HOURS = 1e-4      # $K per hour tie-breaker to avoid idle allocations
STRATEGIC_TB = 1.0    # $K per maturity / risk-reduction point (tie-breaker only)
BIG_PENALTY = 1e6


# --------------------------------------------------------------------------- options / results
@dataclass
class SolveOptions:
    tier: PortfolioTier = PortfolioTier.COMMITTED
    label: str = ""
    objective: str = "enterprise"             # enterprise | balanced | coverage | lob
    lob_focus: Optional[str] = None
    value_multipliers: Dict[str, float] = field(default_factory=dict)
    force_select: Set[str] = field(default_factory=set)
    force_exclude: Set[str] = field(default_factory=set)
    start_by: Dict[str, int] = field(default_factory=dict)
    candidate_projects: Optional[Set[str]] = None
    budget_lobs: Optional[Set[str]] = None    # None = enforce all LOB budgets
    strict_ring_fence: bool = False           # ignore all transfer rules, even approved ones
    enforce_lob_targets: Optional[bool] = None
    protect_compliance: Optional[bool] = None
    protect_critical: Optional[bool] = None
    enforce_min_investment_share: bool = False
    enterprise_reservation_pct: Optional[float] = None
    assume_external_dependencies: bool = False  # LOB view: prerequisites outside scope delivered ASAP
    soft: bool = False                        # diagnostic mode: commitments become penalties
    objective_floor: Optional[float] = None   # valid when the feasible region contains a known solution
    fixed_starts: Optional[Dict[str, int]] = None        # evaluate one specific selection/schedule
    candidate_starts: List[Dict[str, int]] = field(default_factory=list)  # known plans to try as incumbents
    # Plan stability: keep as many of these start months as possible while the original objective stays
    # at or above keep_floor (used to publish LT's decision without needless re-sequencing).
    keep_starts: Optional[Dict[str, int]] = None
    keep_floor: Optional[float] = None


@dataclass(frozen=True)
class Channel:
    cid: int
    pool_id: str
    project_id: str
    capability: str
    kind: str                 # direct | transfer | relaxed
    rule_id: Optional[str]
    efficiency: float
    transfer_cost_rate: float  # $/hr
    incremental_cost_rate: float  # $/hr


@dataclass
class Allocation:
    pool_id: str
    project_id: str
    capability: str
    month: int
    hours: float
    effective_hours: float
    kind: str
    rule_id: Optional[str]
    lob: str
    transfer_cost: float      # $K
    incremental_cost: float   # $K


@dataclass
class PortfolioResult:
    label: str
    tier: PortfolioTier
    status: str
    objective: float
    starts: Dict[str, int]
    allocations: List[Allocation]
    data: PortfolioData
    options: SolveOptions
    diagnostics: List[str] = field(default_factory=list)
    solve_seconds: float = 0.0
    rules_used: Dict[str, float] = field(default_factory=dict)

    @property
    def feasible(self) -> bool:
        return self.status in {"Optimal", "Feasible"}

    @property
    def selected(self) -> Set[str]:
        return set(self.starts)

    def end(self, pid: str) -> int:
        return self.starts[pid] + self.data.projects[pid].duration - 1

    @property
    def approvals_required(self) -> List[str]:
        """Transfer / release rules used by this portfolio that are not yet authorized."""
        rules = {r.rule_id: r for r in self.data.transfers}
        out = []
        for rid, hrs in sorted(self.rules_used.items()):
            r = rules.get(rid)
            if r is not None and not r.is_authorized() and hrs > 1e-6:
                out.append(rid)
        if any(a.kind == "relaxed" and a.hours > 1e-6 for a in self.allocations):
            out.append("RELAXED-RING-FENCING")
        return out

    @property
    def effective_tier(self) -> PortfolioTier:
        if self.tier == PortfolioTier.UNCONSTRAINED:
            return PortfolioTier.UNCONSTRAINED
        if self.approvals_required:
            return PortfolioTier.CONDITIONAL
        return self.tier


# --------------------------------------------------------------------------- eligibility
def _type_ok(rule_type: str, project: Project) -> bool:
    return rule_type in ("", ANY) or rule_type == project.project_type


def direct_eligible(data: PortfolioData, pool: CapacityPool, project: Project) -> bool:
    """E[p,i]: may pool p serve project i without any transfer?"""
    if pool.allowed_funding_sources and project.funding_source not in pool.allowed_funding_sources:
        return False
    rows = [e for e in data.eligibility if e.pool_id == pool.pool_id]

    def match(e) -> bool:
        if e.authorized_project_id:
            return e.authorized_project_id == project.project_id and _type_ok(e.eligible_project_type, project)
        return e.eligible_lob in (ANY, project.primary_lob) and _type_ok(e.eligible_project_type, project)

    if pool.capacity_type == CapacityType.SHARED:
        return True if not rows else any(match(e) for e in rows)
    # Ring-fenced / conditionally transferable: owner always eligible; others only if authorized.
    if project.primary_lob == pool.owning_lob:
        return True
    return any(match(e) for e in rows)


def rule_allowed(rule, tier: PortfolioTier, strict: bool) -> bool:
    if strict:
        return False
    if tier == PortfolioTier.COMMITTED:
        return rule.is_authorized()
    if tier == PortfolioTier.CONDITIONAL:
        return rule.approval_status != ApprovalStatus.REJECTED
    return True


def rule_cap(rule, A: float) -> float:
    cap = min(A * rule.max_pct, max(0.0, A - rule.min_retained_hours))
    if rule.max_hours_per_month is not None:
        cap = min(cap, rule.max_hours_per_month)
    return max(0.0, cap)


def start_options(data: PortfolioData, p: Project, opts: SolveOptions) -> List[int]:
    H = data.settings.horizon
    last = min(H, p.deadline or H)
    latest_start = last - p.duration + 1
    if p.project_id in opts.start_by:
        latest_start = min(latest_start, opts.start_by[p.project_id])
    earliest = p.earliest_start
    if opts.assume_external_dependencies and opts.candidate_projects is not None:
        for d in p.depends_on:
            if d not in opts.candidate_projects and d in data.projects:
                dp = data.projects[d]
                earliest = max(earliest, dp.earliest_start + dp.duration)
    return list(range(max(1, earliest), latest_start + 1))


# --------------------------------------------------------------------------- model builder
class PortfolioModel:
    def __init__(self, data: PortfolioData, opts: SolveOptions):
        self.data, self.opts = data, opts
        self.s = data.settings
        self.H = data.settings.horizon
        self.months = data.months
        self.issues: List[str] = []
        self.unfundable = self._unfundable_projects()
        self.candidates = self._candidates()
        self.S = {pid: start_options(data, data.projects[pid], opts) for pid in self.candidates}
        self.channels = self._build_channels()
        self.A = {(p, t): data.pools[p].available_hours(t) for p in data.pools for t in self.months}

    # ---- sets
    def _unfundable_projects(self) -> Set[str]:
        bad = set()
        for pid, p in self.data.projects.items():
            for l in p.shares():
                lines = [b for b in self.data.budgets if b.lob_id == l and b.funding_source == p.funding_source]
                if not lines:
                    bad.add(pid)
                types = self.data.budget_types(l, p.funding_source)
                if types and p.project_type not in types:
                    bad.add(pid)
        return bad

    def _candidates(self) -> List[str]:
        out = []
        for pid in self.data.projects:
            if self.opts.candidate_projects is not None and pid not in self.opts.candidate_projects:
                continue
            out.append(pid)
        return out

    def _active_months(self, pid: str, cap: str) -> Set[int]:
        p = self.data.projects[pid]
        ks = [k for (c, k), h in p.demand.items() if c == cap and h > 0]
        return {s + k - 1 for s in self.S[pid] for k in ks}

    def _build_channels(self) -> List[Channel]:
        data, opts = self.data, self.opts
        chans: List[Channel] = []
        cid = 0
        rules = [r for r in data.transfers if rule_allowed(r, opts.tier, opts.strict_ring_fence)]
        for pid in self.candidates:
            p = data.projects[pid]
            for cap in p.capabilities:
                for pool in data.pools.values():
                    sub = data.substitution_efficiency(pool.capability, cap)
                    if sub <= 0:
                        continue
                    funding_ok = (not pool.allowed_funding_sources
                                  or p.funding_source in pool.allowed_funding_sources)
                    if direct_eligible(data, pool, p):
                        chans.append(Channel(cid, pool.pool_id, pid, cap, "direct", None, sub, 0.0,
                                             pool.incremental_cost_rate))
                        cid += 1
                        continue
                    if not funding_ok:
                        continue
                    routed = False
                    for r in rules:
                        if r.source_pool != pool.pool_id:
                            continue
                        if r.dest_lob == p.primary_lob or (r.dest_lob == ANY and p.primary_lob != r.source_lob):
                            chans.append(Channel(cid, pool.pool_id, pid, cap, "transfer", r.rule_id,
                                                 sub * r.efficiency, r.cost_per_hour,
                                                 pool.incremental_cost_rate))
                            cid += 1
                            routed = True
                    if not routed and opts.tier == PortfolioTier.UNCONSTRAINED:
                        chans.append(Channel(cid, pool.pool_id, pid, cap, "relaxed", None, sub, 0.0,
                                             pool.incremental_cost_rate))
                        cid += 1
        return chans

    # ---- build
    def build(self) -> pulp.LpProblem:
        data, opts, s, H = self.data, self.opts, self.s, self.H
        prob = pulp.LpProblem("FrontierPortfolioIQ", pulp.LpMaximize)
        P = data.projects
        protect_compliance = s.protect_compliance if opts.protect_compliance is None else opts.protect_compliance
        protect_critical = s.protect_critical if opts.protect_critical is None else opts.protect_critical
        enforce_targets = s.enforce_lob_targets if opts.enforce_lob_targets is None else opts.enforce_lob_targets
        reservation = s.enterprise_reservation_pct if opts.enterprise_reservation_pct is None else opts.enterprise_reservation_pct
        penalties = []

        # x[i,s]
        self.x = {(i, st): pulp.LpVariable(f"x_{i}_{st}", cat="Binary")
                  for i in self.candidates for st in self.S[i]
                  if i not in self.unfundable and i not in opts.force_exclude}
        self.sel = {i: pulp.lpSum(self.x[i, st] for st in self.S[i] if (i, st) in self.x) for i in self.candidates}
        for i in self.candidates:
            if any((i, st) in self.x for st in self.S[i]):
                prob += self.sel[i] <= 1, f"once_{i}"
        if opts.fixed_starts is not None:
            for (i, st), var in self.x.items():
                prob += var == (1 if opts.fixed_starts.get(i) == st else 0), f"fix_{i}_{st}"
            for i, st in opts.fixed_starts.items():
                if (i, st) not in self.x:
                    self.issues.append(f"Plan start {i}@{st} not available in this scenario")

        # y[ch,t]
        self.y: Dict[Tuple[int, int], pulp.LpVariable] = {}
        self.ch_months: Dict[int, List[int]] = {}
        rules = {r.rule_id: r for r in data.transfers}
        for ch in self.channels:
            ms = []
            for t in sorted(self._active_months(ch.project_id, ch.capability)):
                if self.A[ch.pool_id, t] <= 0:
                    continue
                if ch.rule_id and not rules[ch.rule_id].active(t):
                    continue
                if ch.rule_id and rule_cap(rules[ch.rule_id], self.A[ch.pool_id, t]) <= 0:
                    continue
                ms.append(t)
                self.y[ch.cid, t] = pulp.LpVariable(f"y_{ch.cid}_{t}", lowBound=0)
            self.ch_months[ch.cid] = ms

        # commitments: mandatory / critical / compliance / forced / start-by
        must = set(opts.force_select) | set(opts.start_by)
        for i in self.candidates:
            p = P[i]
            if p.mandatory or (protect_critical and p.critical) or (protect_compliance and p.compliance):
                must.add(i)
        for i in sorted(must):
            if i not in self.sel:
                continue
            if opts.soft:
                slack = pulp.LpVariable(f"miss_{i}", lowBound=0, upBound=1)
                prob += self.sel[i] + slack >= 1, f"must_{i}"
                penalties.append((BIG_PENALTY, slack, f"Commitment '{i}' ({P[i].name}) cannot be delivered"))
            else:
                if not any((i, st) in self.x for st in self.S[i]):
                    self.issues.append(f"Commitment '{i}' ({P[i].name}) has no feasible start window "
                                       f"or fundable source")
                    continue
                prob += self.sel[i] == 1, f"must_{i}"

        # pool capacity  sum_i y[p,i,t] <= A[p,t]
        by_pool_t: Dict[Tuple[str, int], list] = {}
        for ch in self.channels:
            for t in self.ch_months[ch.cid]:
                by_pool_t.setdefault((ch.pool_id, t), []).append(ch)
        for (pl, t), chs in by_pool_t.items():
            prob += pulp.lpSum(self.y[c.cid, t] for c in chs) <= self.A[pl, t], f"cap_{pl}_{t}"
            if reservation > 0 and data.pools[pl].capacity_type == CapacityType.SHARED:
                non_ent = [c for c in chs if not P[c.project_id].shared_platform]
                if non_ent:
                    prob += (pulp.lpSum(self.y[c.cid, t] for c in non_ent)
                             <= (1 - reservation) * self.A[pl, t]), f"resv_{pl}_{t}"

        # project demand: effective hours meet requirement of chosen schedule
        by_demand: Dict[Tuple[str, str, int], list] = {}
        for ch in self.channels:
            for t in self.ch_months[ch.cid]:
                by_demand.setdefault((ch.project_id, ch.capability, t), []).append(ch)
        for i in self.candidates:
            p = P[i]
            for cap in p.capabilities:
                for t in self.months:
                    terms = [(self.x[i, st], p.demand[(cap, t - st + 1)]) for st in self.S[i]
                             if (i, st) in self.x and p.demand.get((cap, t - st + 1), 0.0) > 0]
                    chs = by_demand.get((i, cap, t), [])
                    if not terms and not chs:
                        continue
                    req = pulp.lpSum(h * v for v, h in terms)
                    if not chs:
                        prob += req == 0, f"dem_{i}_{cap[:4]}_{t}"
                        continue
                    supply = pulp.lpSum(c.efficiency * self.y[c.cid, t] for c in chs)
                    prob += supply == req, f"dem_{i}_{cap[:4]}_{t}"

        # transfer limits  z[p,l,t] <= ceiling
        by_rule_t: Dict[Tuple[str, int], list] = {}
        for ch in self.channels:
            if ch.rule_id:
                for t in self.ch_months[ch.cid]:
                    by_rule_t.setdefault((ch.rule_id, t), []).append(ch)
        for (rid, t), chs in by_rule_t.items():
            r = rules[rid]
            prob += (pulp.lpSum(self.y[c.cid, t] for c in chs)
                     <= rule_cap(r, self.A[r.source_pool, t])), f"xfer_{rid}_{t}"
        # pools with several rules: total transferred out must also respect retained capacity
        by_pool_xfer: Dict[Tuple[str, int], list] = {}
        for (rid, t), chs in by_rule_t.items():
            by_pool_xfer.setdefault((rules[rid].source_pool, t), []).extend(chs)
        for (pl, t), chs in by_pool_xfer.items():
            retained = max([rules[c.rule_id].min_retained_hours for c in chs] + [0.0])
            prob += (pulp.lpSum(self.y[c.cid, t] for c in chs)
                     <= max(0.0, self.A[pl, t] - retained)), f"retain_{pl}_{t}"

        # LOB funding constraints
        self.spend = self._spend_exprs()
        budget_lobs = opts.budget_lobs if opts.budget_lobs is not None else set(data.lobs)
        for (l, f), series in self.spend.items():
            if l not in budget_lobs:
                continue
            carry = data.budget_mode(l, f)
            avail = {t: sum(b.amount for b in data.budgets
                            if b.lob_id == l and b.funding_source == f and b.month == t) for t in self.months}
            for t in self.months:
                if carry:
                    prob += (pulp.lpSum(series[u] for u in self.months if u <= t)
                             <= sum(avail[u] for u in self.months if u <= t)), f"bud_{l}_{f}_{t}"
                else:
                    prob += series[t] <= avail[t], f"bud_{l}_{f}_{t}"

        # dependencies (time-indexed precedence, no big-M): j may start in s only if i finished before s
        for j in self.candidates:
            for i in P[j].depends_on:
                if i not in self.sel:
                    continue
                prob += self.sel[j] <= self.sel[i], f"dep_sel_{i}_{j}"
                for sj in self.S[j]:
                    if (j, sj) not in self.x:
                        continue
                    prob += (self.x[j, sj] <= pulp.lpSum(self.x[i, si] for si in self.S[i]
                                                         if (i, si) in self.x and si + P[i].duration <= sj)), \
                        f"dep_seq_{i}_{j}_{sj}"

        # governance
        self.inv = {l: pulp.lpSum(P[i].shares().get(l, 0.0) * P[i].total_cost * self.sel[i]
                                  for i in self.candidates) for l in data.lobs}
        total_inv = pulp.lpSum(self.inv.values())
        biz = [l for l in data.business_lobs() if opts.lob_focus in (None, l)]

        def lower(expr, rhs, name, why):
            if opts.soft:
                sl = pulp.LpVariable(f"sl_{name}", lowBound=0)
                prob.addConstraint(expr + sl >= rhs, name)
                penalties.append((1000.0, sl, why))
            else:
                prob.addConstraint(expr >= rhs, name)

        if enforce_targets:
            for k, t in enumerate(data.targets):
                if opts.lob_focus not in (None, t.lob_id):
                    continue
                if t.min_outcome:
                    expr = pulp.lpSum(b.attributed_benefit * self.sel[b.project_id] for b in data.benefits
                                      if b.lob_id == t.lob_id and b.kpi == t.kpi and b.project_id in self.sel)
                    lower(expr, t.min_outcome, f"tgt_out_{k}", f"{t.lob_id} outcome floor on {t.kpi}")
                if t.min_investment:
                    lower(self.inv[t.lob_id], t.min_investment, f"tgt_mininv_{k}",
                          f"{t.lob_id} minimum investment")
                if t.max_investment:
                    prob += self.inv[t.lob_id] <= t.max_investment, f"tgt_maxinv_{k}"
            for l in biz:
                lob = data.lobs[l]
                if lob.risk_tolerance is not None:
                    prob += pulp.lpSum((P[i].execution_risk - lob.risk_tolerance) * self.sel[i]
                                       for i in self.candidates if P[i].primary_lob == l) <= 0, f"risk_{l}"
                if lob.maturity_target:
                    mat = pulp.lpSum(P[i].maturity_gain * self.sel[i] for i in self.candidates
                                     if P[i].primary_lob == l or l in P[i].secondary_lobs)
                    lower(mat, lob.maturity_target, f"mat_{l}", f"{l} maturity target")
        if s.max_lob_concentration and opts.lob_focus is None:
            for l in data.business_lobs():
                prob += self.inv[l] <= s.max_lob_concentration * total_inv, f"conc_{l}"
        if opts.enforce_min_investment_share and opts.lob_focus is None:
            for l, share in s.min_investment_share.items():
                if l in self.inv:
                    lower(self.inv[l], share * total_inv, f"mininvshare_{l}", f"{l} minimum investment share")
        if s.min_shared_share and opts.lob_focus is None:
            shared = [ch for ch in self.channels if data.pools[ch.pool_id].capacity_type == CapacityType.SHARED]
            tot_sh = pulp.lpSum(self.y[c.cid, t] for c in shared for t in self.ch_months[c.cid])
            for l, share in s.min_shared_share.items():
                mine = pulp.lpSum(self.y[c.cid, t] for c in shared for t in self.ch_months[c.cid]
                                  if P[c.project_id].primary_lob == l)
                lower(mine, share * tot_sh, f"minshared_{l}", f"{l} minimum shared-capacity allocation")

        # objective
        obj = []
        for (i, st), var in self.x.items():
            p = P[i]
            e_min = p.earliest_start + p.duration - 1
            disc = 1 - s.schedule_discount * max(0, (st + p.duration - 1) - e_min) / H
            base = p.value
            if opts.objective == "lob" and opts.lob_focus:
                base = data.lob_benefit(i, opts.lob_focus)
            mult = opts.value_multipliers.get(i, 1.0)
            w = base * mult * disc + STRATEGIC_TB * (p.maturity_gain + p.risk_reduction)
            if opts.objective == "coverage":
                w = base * disc * 1e-3 + STRATEGIC_TB * 1e-3 * (p.maturity_gain + p.risk_reduction)
            obj.append(w * var)
        for ch in self.channels:
            rate = (ch.transfer_cost_rate + ch.incremental_cost_rate) / 1000.0 + EPS_HOURS
            obj.append(-rate * pulp.lpSum(self.y[ch.cid, t] for t in self.ch_months[ch.cid]))
        if opts.objective == "balanced":
            m = pulp.LpVariable("min_lob_attainment", lowBound=0, upBound=1)
            pot_total = 0.0
            for l in data.business_lobs():
                pot = sum(data.lob_benefit(i, l) for i in self.candidates)
                if pot <= 0:
                    continue
                pot_total += pot
                prob += (pulp.lpSum(data.lob_benefit(i, l) * self.sel[i] for i in self.candidates)
                         >= m * pot), f"bal_{l}"
            obj.append(s.balance_weight * pot_total * m)
        if opts.objective == "coverage":
            for n, (l, kpi) in enumerate(sorted({(b.lob_id, b.kpi) for b in data.benefits})):
                cov = pulp.LpVariable(f"cov_{n}", cat="Binary")
                prob += cov <= pulp.lpSum(self.sel[b.project_id] for b in data.benefits
                                          if b.lob_id == l and b.kpi == kpi and b.project_id in self.sel), \
                    f"cov_c_{n}"
                obj.append(1000.0 * cov)
        for w, var, _why in penalties:
            obj.append(-w * var)
        self.penalties = penalties
        prob += pulp.lpSum(obj)
        if opts.objective_floor is not None and not opts.soft:
            # The scenario relaxes the baseline, so the baseline solution remains feasible: its objective
            # is a valid lower bound. Guarantees relaxations never report less value than the baseline.
            prob += pulp.lpSum(obj) >= opts.objective_floor - 1e-6 * abs(opts.objective_floor) - 1.0, "floor"
        if opts.keep_starts is not None:
            if opts.keep_floor is not None:
                prob += pulp.lpSum(obj) >= opts.keep_floor, "keep_floor"
            prob.setObjective(pulp.lpSum(self.x[i, st] for i, st in opts.keep_starts.items()
                                         if (i, st) in self.x))
        self.prob = prob
        return prob

    def _spend_exprs(self):
        data, P = self.data, self.data.projects
        spend: Dict[Tuple[str, str], Dict[int, list]] = {}
        for i in self.candidates:
            p = P[i]
            for l, sh in p.shares().items():
                series = spend.setdefault((l, p.funding_source), {t: [] for t in self.months})
                for st in self.S[i]:
                    if (i, st) not in self.x:
                        continue
                    for k, c in p.cost.items():
                        t = st + k - 1
                        if t in series:
                            series[t].append(sh * c * self.x[i, st])
        for ch in self.channels:
            rate = (ch.transfer_cost_rate + ch.incremental_cost_rate) / 1000.0
            if rate <= 0:
                continue
            p = P[ch.project_id]
            for l, sh in p.shares().items():
                series = spend.setdefault((l, p.funding_source), {t: [] for t in self.months})
                for t in self.ch_months[ch.cid]:
                    series[t].append(sh * rate * self.y[ch.cid, t])
        return {k: {t: pulp.lpSum(v) for t, v in s.items()} for k, s in spend.items()}

    # ---- solve
    def solve(self) -> PortfolioResult:
        t0 = time.time()
        prob = self.build()
        if self.issues and not self.opts.soft:
            return self._result("Infeasible", 0.0, {}, [], t0, list(self.issues))
        solver = _solver(self.s.time_limit, self.s.mip_gap)
        prob.solve(solver)
        status = pulp.LpStatus[prob.status]
        has_sol = any((v.value() or 0) > 0.5 for v in self.x.values()) or status == "Optimal"
        if status == "Optimal" or (status == "Not Solved" and has_sol):
            st = "Optimal" if status == "Optimal" else "Feasible"
        else:
            return self._result("Infeasible", 0.0, {}, [], t0, list(self.issues))
        return self.extract(lambda v: v.value(), st, pulp.value(prob.objective) or 0.0, t0)

    def extract(self, value_of, status: str, objective: float, t0: float) -> PortfolioResult:
        """Turn variable values (from any solver) into a PortfolioResult: starts, allocations, rules used."""
        starts = {i: s for (i, s), v in self.x.items() if (value_of(v) or 0) > 0.5}
        allocs, used = [], {}
        P = self.data.projects
        for ch in self.channels:
            for t in self.ch_months[ch.cid]:
                h = value_of(self.y[ch.cid, t]) or 0.0
                if h <= 1e-6:
                    continue
                allocs.append(Allocation(ch.pool_id, ch.project_id, ch.capability, t, h, h * ch.efficiency,
                                         ch.kind, ch.rule_id, P[ch.project_id].primary_lob,
                                         h * ch.transfer_cost_rate / 1000.0,
                                         h * ch.incremental_cost_rate / 1000.0))
                if ch.rule_id:
                    used[ch.rule_id] = used.get(ch.rule_id, 0.0) + h
        diags = list(self.issues)
        if self.opts.soft:
            for w, var, why in self.penalties:
                if (value_of(var) or 0) > 1e-6:
                    diags.append(why)
        res = self._result(status, objective, starts, allocs, t0, diags)
        res.rules_used = used
        return res

    def _result(self, status, obj, starts, allocs, t0, diags) -> PortfolioResult:
        return PortfolioResult(label=self.opts.label or self.opts.tier.value, tier=self.opts.tier,
                               status=status, objective=obj, starts=starts, allocations=allocs,
                               data=self.data, options=self.opts, diagnostics=diags,
                               solve_seconds=time.time() - t0)


def _solver(time_limit: int, gap: float):
    avail = pulp.listSolvers(onlyAvailable=True)
    if "HiGHS" in avail:
        return pulp.HiGHS(msg=False, timeLimit=time_limit, gapRel=gap)
    if "PULP_CBC_CMD" in avail:
        return pulp.PULP_CBC_CMD(msg=False, timeLimit=time_limit, gapRel=gap)
    if "HiGHS_CMD" in avail:
        return pulp.HiGHS_CMD(msg=False, timeLimit=time_limit, gapRel=gap)
    raise RuntimeError("No MILP solver available: install 'highspy' or CBC")


def solve(data: PortfolioData, opts: Optional[SolveOptions] = None, diagnose: bool = True) -> PortfolioResult:
    """Solve a portfolio; if infeasible, re-solve in soft mode to explain which commitments break."""
    opts = opts or SolveOptions()
    res = PortfolioModel(data, opts).solve()
    # Known plans (e.g. the committed portfolio) are evaluated as incumbents. With a relative MIP gap the
    # solver may stop at a slightly worse plan; this guarantees a scenario never reports less value than
    # an already-known plan that remains feasible under the scenario's constraints.
    import dataclasses
    for cand in opts.candidate_starts:
        alt = PortfolioModel(data, dataclasses.replace(opts, fixed_starts=dict(cand), candidate_starts=[],
                                                       objective_floor=None)).solve()
        if alt.feasible and (not res.feasible or alt.objective > res.objective + 1e-6):
            alt.options = opts
            res = alt
    if not res.feasible and diagnose and not opts.soft:
        soft = PortfolioModel(data, dataclasses.replace(opts, soft=True)).solve()
        res.diagnostics = list(dict.fromkeys(res.diagnostics + soft.diagnostics)) or \
            ["Model infeasible: check budgets, capacity windows and mandatory deadlines"]
    return res
