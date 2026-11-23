"""Objective registry for multiobjective portfolio optimization.

Every objective has an explicit direction, unit, normalization method and formula, and two
implementations that must agree exactly:

- ``linear(model, prob)``  a PuLP expression over the MILP's variables (it may add auxiliary variables
                            and constraints, e.g. for a capped OKR attainment or a peak utilization);
- ``evaluate(result)``     the same quantity computed from a solved plan (starts and allocations).

Notation: x[i,s] = 1 if initiative i starts in month s; sel_i = sum_s x[i,s]; D_i = duration;
fin(i,s) = s + D_i - 1; y = hours on an allocation route; rate = transfer + incremental $/hour.

Administrators add objectives in configuration (see `load_objective_config`): a *custom* objective is
``sum_i sel_i * expr(i)`` where ``expr`` is an arithmetic expression over the initiative's numeric
fields and custom attributes (Projects sheet / Project Attributes sheet), e.g.
``risk_reduction * 10 + maturity_gain`` or ``attributes['nps_uplift'] * value / 1000``.
"""
from __future__ import annotations

import ast
import math
import operator
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import pulp

from ..domain import PortfolioData, Project

NORMALIZATIONS = ("minmax", "fixed", "none")


@dataclass
class ObjectiveSpec:
    key: str
    name: str
    direction: str                     # "max" | "min"
    unit: str
    formula: str
    normalization: str = "minmax"      # minmax: payoff-table ideal/nadir; fixed: `fixed_range`; none
    fixed_range: Optional[Tuple[float, float]] = None
    expression: Optional[str] = None   # custom objectives only
    params: Dict[str, float] = field(default_factory=dict)
    builtin: bool = True

    def __post_init__(self):
        if self.direction not in ("max", "min"):
            raise ValueError(f"objective {self.key}: direction must be 'max' or 'min'")
        if self.normalization not in NORMALIZATIONS:
            raise ValueError(f"objective {self.key}: normalization must be one of {NORMALIZATIONS}")
        if self.normalization == "fixed" and not self.fixed_range:
            raise ValueError(f"objective {self.key}: fixed normalization needs fixed_range")

    @property
    def sign(self) -> float:
        """Multiply by this to turn the objective into a maximization."""
        return 1.0 if self.direction == "max" else -1.0

    def to_dict(self) -> Dict[str, object]:
        return {"key": self.key, "name": self.name, "direction": self.direction, "unit": self.unit,
                "formula": self.formula, "normalization": self.normalization,
                "fixed_range": list(self.fixed_range) if self.fixed_range else None,
                "expression": self.expression, "builtin": self.builtin}


# --------------------------------------------------------------------------- helpers shared by objectives
def _disc(data: PortfolioData, p: Project, s: int) -> float:
    st = data.settings
    e_min = p.earliest_start + p.duration - 1
    return 1 - st.schedule_discount * max(0, (s + p.duration - 1) - e_min) / st.horizon


def _pv(data: PortfolioData, month: int) -> float:
    return (1 + data.settings.discount_rate) ** (-month / 12.0)


def _lob_weight(data: PortfolioData, p: Project) -> float:
    lob = data.lobs.get(p.primary_lob)
    return 1.0 / max(1, lob.strategic_priority if lob else 1)


def _routes_cost(model) -> pulp.LpAffineExpression:
    return pulp.lpSum((ch.transfer_cost_rate + ch.incremental_cost_rate) / 1000.0 * model.y[ch.cid, t]
                      for ch in model.channels for t in model.ch_months[ch.cid])


def _alloc_cost(res) -> float:
    return sum(a.transfer_cost + a.incremental_cost for a in res.allocations)


def _x_linear(model, coef: Callable[[str, int], float]) -> pulp.LpAffineExpression:
    return pulp.lpSum(coef(i, s) * v for (i, s), v in model.x.items())


def _x_eval(res, coef: Callable[[str, int], float]) -> float:
    return sum(coef(i, s) for i, s in res.starts.items())


# --------------------------------------------------------------------------- safe custom expressions
_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.Mod: operator.mod}
_FUN = {"min": min, "max": max, "abs": abs, "log": math.log, "sqrt": math.sqrt, "exp": math.exp}
_FIELDS = ("value", "total_cost", "duration", "earliest_start", "lob_priority", "enterprise_contribution",
           "risk_reduction", "maturity_gain", "delivery_confidence", "execution_risk")
_FLAGS = ("mandatory", "compliance", "critical", "shared_platform")


def compile_expression(expr: str) -> Callable[[Project], float]:
    """Compile a custom objective expression into a function of one initiative. Only arithmetic,
    numbers, initiative fields, flags (0/1), `attributes['name']` and min/max/abs/log/sqrt/exp."""
    tree = ast.parse(expr, mode="eval")

    def ev(node, p: Project):
        if isinstance(node, ast.Expression):
            return ev(node.body, p)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
            return _BIN[type(node.op)](ev(node.left, p), ev(node.right, p))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            v = ev(node.operand, p)
            return -v if isinstance(node.op, ast.USub) else v
        if isinstance(node, ast.Name):
            if node.id in _FIELDS:
                return float(getattr(p, node.id))
            if node.id in _FLAGS:
                return 1.0 if getattr(p, node.id) else 0.0
            raise ValueError(f"unknown name {node.id!r} in objective expression")
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "attributes":
            key = node.slice.value if isinstance(node.slice, ast.Constant) else None
            if not isinstance(key, str):
                raise ValueError("attributes[...] needs a quoted attribute name")
            return float(p.attributes.get(key, 0.0))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUN and not node.keywords:
            return float(_FUN[node.func.id](*(ev(a, p) for a in node.args)))
        raise ValueError(f"unsupported syntax in objective expression: {ast.dump(node)[:60]}")

    # validate once on a neutral initiative so syntax errors surface at load time
    probe = Project("probe", "probe", "L")
    try:
        ev(tree, probe)
    except ZeroDivisionError:
        pass
    return lambda p: ev(tree, p)


# --------------------------------------------------------------------------- objective implementations
class Objective:
    def __init__(self, spec: ObjectiveSpec, data: PortfolioData):
        self.spec, self.data = spec, data

    def linear(self, model, prob: pulp.LpProblem) -> pulp.LpAffineExpression:  # pragma: no cover
        raise NotImplementedError

    def evaluate(self, res) -> float:  # pragma: no cover
        raise NotImplementedError

    def applicable(self) -> Tuple[bool, str]:
        return True, ""


class _XObjective(Objective):
    """Objectives linear in x (optionally plus route costs)."""
    with_route_cost = 0.0     # coefficient on route costs ($K): -1 for value, +1 for cost

    def coef(self, i: str, s: int) -> float:  # pragma: no cover
        raise NotImplementedError

    def linear(self, model, prob):
        expr = _x_linear(model, self.coef)
        if self.with_route_cost:
            expr = expr + self.with_route_cost * _routes_cost(model)
        return expr

    def evaluate(self, res):
        return _x_eval(res, self.coef) + self.with_route_cost * _alloc_cost(res)


class ValueObjective(_XObjective):
    with_route_cost = -1.0

    def coef(self, i, s):
        p = self.data.projects[i]
        return p.value * _disc(self.data, p, s)


class NPVObjective(Objective):
    def coef(self, i, s):
        d, p = self.data, self.data.projects[i]
        return p.value * _pv(d, s + p.duration - 1) - sum(c * _pv(d, s + k - 1) for k, c in p.cost.items())

    def linear(self, model, prob):
        d = self.data
        return _x_linear(model, self.coef) - pulp.lpSum(
            (ch.transfer_cost_rate + ch.incremental_cost_rate) / 1000.0 * _pv(d, t) * model.y[ch.cid, t]
            for ch in model.channels for t in model.ch_months[ch.cid])

    def evaluate(self, res):
        d = self.data
        return _x_eval(res, self.coef) - sum((a.transfer_cost + a.incremental_cost) * _pv(d, a.month)
                                             for a in res.allocations)


class AlignmentObjective(_XObjective):
    def coef(self, i, s):
        p = self.data.projects[i]
        return 100.0 * p.enterprise_contribution * _lob_weight(self.data, p)


class AccelerationObjective(_XObjective):
    def milestone(self) -> int:
        return int(self.spec.params.get("milestone_month") or math.ceil(self.data.settings.horizon / 2))

    def coef(self, i, s):
        p = self.data.projects[i]
        return p.value if s + p.duration - 1 <= self.milestone() else 0.0


class ComplianceObjective(_XObjective):
    def coef(self, i, s):
        p = self.data.projects[i]
        return 1.0 if (p.compliance or p.mandatory) else 0.0

    def applicable(self):
        ok = any(p.compliance or p.mandatory for p in self.data.projects.values())
        return ok, "" if ok else "no compliance or mandatory initiatives"


class CostObjective(_XObjective):
    with_route_cost = 1.0

    def coef(self, i, s):
        return self.data.projects[i].total_cost


class RiskObjective(_XObjective):
    def coef(self, i, s):
        p = self.data.projects[i]
        return p.execution_risk * p.total_cost


class TimeToValueObjective(Objective):
    """Value-weighted months until benefit; an unselected initiative counts as delivered at H+1."""

    def total(self) -> float:
        return sum(p.value for p in self.data.projects.values()) or 1.0

    def linear(self, model, prob):
        d, H = self.data, self.data.settings.horizon
        terms = []
        for (i, s), v in model.x.items():
            p = d.projects[i]
            terms.append(p.value * ((s + p.duration - 1) - (H + 1)) * v)
        const = sum(d.projects[i].value * (H + 1) for i in model.candidates)
        return (pulp.lpSum(terms) + const) / self.total()

    def evaluate(self, res):
        d, H = self.data, self.data.settings.horizon
        tot = 0.0
        for i, p in d.projects.items():
            if res.options.candidate_projects is not None and i not in res.options.candidate_projects:
                continue
            tot += p.value * ((res.starts[i] + p.duration - 1) if i in res.starts else H + 1)
        return tot / self.total()


class OKRObjective(Objective):
    """Sum over KPI targets of capped attainment: sum_k min(1, attributed benefit_k / target_k)."""

    def targets(self):
        return [t for t in self.data.targets if t.target and t.target > 0]

    def applicable(self):
        ok = bool(self.targets())
        return ok, "" if ok else "no KPI targets with a positive target value"

    def linear(self, model, prob):
        terms = []
        for k, t in enumerate(self.targets()):
            u = pulp.LpVariable(f"okr_u_{k}", lowBound=0, upBound=1)
            got = pulp.lpSum(b.attributed_benefit * model.sel[b.project_id] for b in self.data.benefits
                             if b.lob_id == t.lob_id and b.kpi == t.kpi and b.project_id in model.sel)
            prob += t.target * u <= got, f"okr_cap_{k}"
            terms.append(u)
        return pulp.lpSum(terms)

    def evaluate(self, res):
        tot = 0.0
        for t in self.targets():
            got = sum(b.attributed_benefit for b in self.data.benefits
                      if b.lob_id == t.lob_id and b.kpi == t.kpi and b.project_id in res.starts)
            tot += min(1.0, got / t.target)
        return tot


class OverloadObjective(Objective):
    """Peak utilization of any pool in any month (share of available hours)."""

    def linear(self, model, prob):
        U = pulp.LpVariable("peak_util", lowBound=0)
        by_pt: Dict[Tuple[str, int], list] = {}
        for ch in model.channels:
            for t in model.ch_months[ch.cid]:
                by_pt.setdefault((ch.pool_id, t), []).append(ch)
        for (pl, t), chs in by_pt.items():
            A = model.A[pl, t]
            if A > 0:
                prob += pulp.lpSum(model.y[c.cid, t] for c in chs) <= A * U, f"peak_{pl}_{t}"
        return U

    def evaluate(self, res):
        used: Dict[Tuple[str, int], float] = {}
        for a in res.allocations:
            used[a.pool_id, a.month] = used.get((a.pool_id, a.month), 0.0) + a.hours
        peak = 0.0
        for (pl, t), h in used.items():
            A = self.data.pools[pl].available_hours(t)
            if A > 0:
                peak = max(peak, h / A)
        return peak


class DiversificationObjective(Objective):
    """Minimum, over business LOBs, of the share of the LOB's attributable value the plan captures."""

    def lobs(self) -> List[Tuple[str, float]]:
        out = []
        chosen = self.spec.params.get("lobs")
        for l in self.data.business_lobs():
            if chosen and l not in chosen:
                continue
            pot = sum(self.data.lob_benefit(i, l) for i in self.data.projects)
            if pot > 0:
                out.append((l, pot))
        return out

    def applicable(self):
        ok = len(self.lobs()) >= 2
        return ok, "" if ok else "fewer than two LOBs with attributed benefits"

    def linear(self, model, prob):
        m = pulp.LpVariable("div_min_share", lowBound=0, upBound=1)
        for l, pot in self.lobs():
            prob += pulp.lpSum(self.data.lob_benefit(i, l) * model.sel[i] for i in model.candidates) >= m * pot, \
                f"div_{l}"
        return m

    def evaluate(self, res):
        vals = []
        for l, pot in self.lobs():
            vals.append(sum(self.data.lob_benefit(i, l) for i in res.starts) / pot)
        return min(vals) if vals else 0.0


class CustomObjective(_XObjective):
    def __init__(self, spec, data):
        super().__init__(spec, data)
        self.fn = compile_expression(spec.expression or "0")
        self._c = {i: float(self.fn(p)) for i, p in data.projects.items()}

    def coef(self, i, s):
        return self._c[i]


IMPLEMENTATIONS = {"value": ValueObjective, "npv": NPVObjective, "alignment": AlignmentObjective,
                   "okr": OKRObjective, "acceleration": AccelerationObjective,
                   "compliance": ComplianceObjective, "cost": CostObjective, "risk": RiskObjective,
                   "overload": OverloadObjective, "time_to_value": TimeToValueObjective,
                   "diversification": DiversificationObjective}


def builtin_specs() -> Dict[str, ObjectiveSpec]:
    S = ObjectiveSpec
    return {s.key: s for s in [
        S("value", "Business value (time-adjusted, net)", "max", "$K",
          "sum x[i,s]*value_i*(1 - schedule_discount*max(0, fin(i,s) - earliest_finish_i)/H) - route costs"),
        S("npv", "Financial NPV", "max", "$K",
          "sum x[i,s]*(value_i*PV(fin(i,s)) - sum_k cost_ik*PV(s+k-1)) - sum route costs*PV(t); "
          "PV(m) = (1+r)^(-m/12), r = discount_rate; value treated as benefit realized at completion"),
        S("alignment", "Strategic alignment", "max", "alignment points",
          "sum sel_i * 100 * enterprise_contribution_i / strategic_priority(primary LOB)"),
        S("okr", "Measurable OKR contribution", "max", "targets (capped attainment)",
          "sum over KPI targets k of min(1, sum attributed_benefit(i,k)*sel_i / target_k)"),
        S("acceleration", "Outcome acceleration", "max", "$K by milestone",
          "sum x[i,s]*value_i where fin(i,s) <= milestone month (default H/2)"),
        S("compliance", "Compliance and mandatory coverage", "max", "initiatives",
          "sum sel_i over compliance or mandatory initiatives"),
        S("cost", "Total portfolio cost", "min", "$K", "sum sel_i*total_cost_i + route costs"),
        S("risk", "Execution risk exposure", "min", "$K at risk",
          "sum sel_i * execution_risk_i * total_cost_i"),
        S("overload", "Peak capability load", "min", "share of capacity",
          "max over pools p and months t of hours_used(p,t) / A(p,t)"),
        S("time_to_value", "Time to business value", "min", "value-weighted months",
          "sum_i value_i * (fin(i) if selected else H+1) / sum_i value_i"),
        S("diversification", "LOB diversification", "max", "min LOB value share",
          "min over business LOBs l of (sum lob_benefit(i,l)*sel_i) / (sum lob_benefit(i,l))"),
    ]}


def make_objective(spec: ObjectiveSpec, data: PortfolioData) -> Objective:
    if not spec.builtin:
        return CustomObjective(spec, data)
    return IMPLEMENTATIONS[spec.key](spec, data)


@dataclass
class ObjectiveSet:
    specs: List[ObjectiveSpec]
    data: PortfolioData
    skipped: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        objs, keep = {}, []
        for sp in self.specs:
            o = make_objective(sp, self.data)
            ok, why = o.applicable()
            if not ok:
                self.skipped[sp.key] = why
                continue
            objs[sp.key] = o
            keep.append(sp)
        self.specs = keep
        self.impl = objs

    @property
    def keys(self) -> List[str]:
        return [s.key for s in self.specs]

    def spec(self, key: str) -> ObjectiveSpec:
        return next(s for s in self.specs if s.key == key)

    def evaluate(self, res) -> Dict[str, float]:
        return {k: float(o.evaluate(res)) for k, o in self.impl.items()}


DEFAULT_ENABLED = ("value", "npv", "alignment", "okr", "acceleration", "compliance", "cost", "risk",
                   "overload", "time_to_value", "diversification")


def objective_set(data: PortfolioData, enabled=DEFAULT_ENABLED, custom: Optional[List[ObjectiveSpec]] = None,
                  overrides: Optional[Dict[str, Dict]] = None) -> ObjectiveSet:
    specs = builtin_specs()
    chosen = []
    for k in enabled:
        if k not in specs:
            raise ValueError(f"unknown objective {k!r}; built-ins are {sorted(specs)}")
        sp = specs[k]
        for attr, v in (overrides or {}).get(k, {}).items():
            setattr(sp, attr, tuple(v) if attr == "fixed_range" else v)
        chosen.append(sp)
    for c in custom or []:
        chosen.append(c)
    return ObjectiveSet(chosen, data)


def load_objective_config(path: str, data: PortfolioData) -> ObjectiveSet:
    """YAML: {enabled: [value, risk, ...], overrides: {risk: {normalization: fixed, fixed_range: [0, 5000]}},
    custom: [{key, name, direction, unit, expression, normalization}]}"""
    import yaml
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    custom = []
    for c in raw.get("custom") or []:
        custom.append(ObjectiveSpec(
            key=str(c["key"]), name=str(c.get("name", c["key"])), direction=str(c.get("direction", "max")),
            unit=str(c.get("unit", "points")), formula=f"sum sel_i * ({c['expression']})",
            normalization=str(c.get("normalization", "minmax")),
            fixed_range=tuple(c["fixed_range"]) if c.get("fixed_range") else None,
            expression=str(c["expression"]), builtin=False))
    return objective_set(data, raw.get("enabled") or DEFAULT_ENABLED, custom, raw.get("overrides") or {})
