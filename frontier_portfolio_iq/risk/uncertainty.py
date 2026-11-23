"""Uncertainty model: per-initiative and per-pool distributions, shared risk drivers, correlations and
simulation settings — read from optional intake sheets, with documented demonstration defaults.

Defaults are derived from each initiative's own Execution Risk (r) and Delivery Confidence (c) so that
riskier work is more uncertain. They are *uncalibrated demonstration assumptions*: every default the
engine uses is listed in `UncertaintyModel.assumptions`, flagged on the dashboard and in the deck, and
must be replaced with calibrated values (historical overruns, vendor track records, attrition data)
before a recommendation is used for a production decision.

Default initiative assumptions
  effort (hours and cost) multiplier   pert(0.90, 1.00, 1 + 0.8 r)
  duration multiplier                  pert(0.95, 1.00, 1 + 1.0 r)
  benefit realization multiplier       pert(0.5 + 0.3 c, 0.85 + 0.15 c, 1.10)
  start slip (months)                  discrete(0: 1-q, 1: 0.7 q, 2: 0.3 q), q = min(0.5, 0.05 + 0.6 r)
  delivery failure probability         0.5 r (1 - c); a failure forfeits 75% of the benefit
Default pool assumptions
  availability multiplier              pert(0.88, 0.98, 1.03); temporary pools pert(0.75, 0.95, 1.00)
  hiring delay (months)                pools starting after month 1: discrete(0: 0.6, 1: 0.25, 2: 0.15)
  productivity multiplier              pert(0.92, 1.00, 1.04)
Default shared drivers
  INFL        cost inflation, lognormal(1.0, 0.025), multiplies every initiative's cost
  CAP-<name>  shortage in one capability, pert(0.85, 1.0, 1.03) on every pool of that capability;
              capability shortages correlated 0.4 with each other (shared labour market), 0.2 with INFL
  REG         regulatory change, bernoulli(0.15); when it occurs compliance initiatives take 25% longer
  PLAT        shared-platform delay, discrete(0: 0.75, 1: 0.15, 2: 0.10) months on shared-platform
              initiatives (propagates to everything that depends on them)
  VEND-<x>    one per vendor tag ("vendor:x"), discrete(0: 0.7, 1: 0.2, 3: 0.1) months start delay on
              every initiative using that vendor
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..domain import PortfolioData
from .correlation import CorrelationError, build_matrix, nearest_correlation, validate_correlation
from .distributions import Dist, parse_dist

PROJECT_EFFECTS = ("cost", "effort", "duration", "benefit", "start_delay")
POOL_EFFECTS = ("availability",)
MODES = ("multiply", "add", "event")


@dataclass
class ProjectUncertainty:
    effort: Dist
    duration: Dist
    benefit: Dist
    start_slip: Dist
    failure_prob: float
    failure_loss: float = 0.75
    source: str = "default"


@dataclass
class PoolUncertainty:
    availability: Dist
    hiring_delay: Dist
    productivity: Dist
    source: str = "default"


@dataclass
class RiskDriver:
    driver_id: str
    name: str
    category: str
    dist: Dist
    effect: str            # cost | effort | duration | benefit | start_delay | availability
    mode: str = "multiply"  # multiply: factor = sample; add: + sample (months); event: factor = 1 + magnitude*sample
    magnitude: float = 0.0
    applies_to: str = "all"
    source: str = "default"

    def __post_init__(self):
        if self.effect not in PROJECT_EFFECTS + POOL_EFFECTS:
            raise ValueError(f"driver {self.driver_id}: unknown effect {self.effect!r}")
        if self.mode not in MODES:
            raise ValueError(f"driver {self.driver_id}: unknown mode {self.mode!r}")
        if self.effect == "start_delay" and self.mode != "add":
            raise ValueError(f"driver {self.driver_id}: start_delay drivers must use mode 'add'")


@dataclass
class SimulationSettings:
    iterations: int = 10000
    seed: int = 20270101
    chunk_size: int = 2500
    workers: int = 1
    mode: str = "fixed"                       # fixed | adaptive
    extension_months: Optional[int] = None    # months simulated beyond the horizon (default = horizon)
    budget_tolerance: float = 0.05            # overrun if cost > planned cost * (1 + tolerance): 5% reserve
    date_grace_months: int = 1                # key date = deadline, or planned finish + 1 month grace
    min_value_fraction: float = 0.8           # minimum acceptable business value = fraction of plan
    var_alpha: float = 0.95                   # VaR / CVaR confidence level
    contingency_pct: float = 0.10             # adaptive: approved contingency hours per capability/month
    contingency_rate: float = 150.0           # $/hour for contingency hours
    start_gate: float = 0.5                   # adaptive: start only if >= 50% of a month's need is free
    max_defer_months: int = 3                 # adaptive: a gated start is deferred at most this long
    convergence_rel_se: float = 0.01          # converged if SE(mean NPV) <= 1% of |mean| ...
    convergence_prob_se: float = 0.01         # ... and every reported probability has SE <= 0.01
    shortage_threshold_hours: float = 1.0     # unmet hours in a month that count as a shortage

    def __post_init__(self):
        if self.mode not in ("fixed", "adaptive"):
            raise ValueError("simulation mode must be 'fixed' or 'adaptive'")
        if self.iterations < 100:
            raise ValueError("at least 100 iterations")
        if not 0.5 < self.var_alpha < 1:
            raise ValueError("var_alpha must be in (0.5, 1)")


@dataclass
class UncertaintyModel:
    projects: Dict[str, ProjectUncertainty]
    pools: Dict[str, PoolUncertainty]
    drivers: List[RiskDriver]
    correlations: Dict[Tuple[str, str], float] = field(default_factory=dict)
    settings: SimulationSettings = field(default_factory=SimulationSettings)
    assumptions: List[str] = field(default_factory=list)
    repaired_correlation: bool = False
    allow_correlation_repair: bool = False

    @property
    def calibrated(self) -> bool:
        return all(p.source != "default" for p in self.projects.values()) and \
            all(p.source != "default" for p in self.pools.values()) and \
            all(d.source != "default" for d in self.drivers)

    def correlation_matrix(self):
        names = [d.driver_id for d in self.drivers]
        C = build_matrix(names, self.correlations)
        issues = validate_correlation(C)
        if issues:
            if not self.allow_correlation_repair:
                raise CorrelationError("driver correlation matrix invalid: " + "; ".join(issues))
            C = nearest_correlation(C)
            self.repaired_correlation = True
        return names, C

    def with_settings(self, **kw) -> "UncertaintyModel":
        return dataclasses.replace(self, settings=dataclasses.replace(self.settings, **kw))

    def scaled(self, cost: float = 1.0, schedule: float = 1.0, benefit_shift: float = 0.0) -> "UncertaintyModel":
        """What-if: widen/narrow cost and schedule uncertainty (scale the spread around 1) and shift benefit
        realization (e.g. -0.1 = 10 points lower)."""
        def widen(d: Dist, k: float) -> Dist:
            if k == 1.0 or d.kind not in ("tri", "pert", "uniform"):
                return d
            p = list(d.params)
            core = p[:3] if d.kind != "uniform" else p[:2]
            centre = core[1] if d.kind != "uniform" else 1.0
            core = [centre + (v - centre) * k for v in core]
            return Dist(d.kind, tuple(core) + tuple(p[len(core):]))

        def shift(d: Dist, s: float) -> Dist:
            if not s or d.kind not in ("tri", "pert", "uniform", "const"):
                return d
            return Dist(d.kind, tuple(v + s if j < (3 if d.kind in ("tri", "pert") else 2) else v
                                      for j, v in enumerate(d.params)))
        projects = {i: dataclasses.replace(u, effort=widen(u.effort, cost), duration=widen(u.duration, schedule),
                                           benefit=shift(u.benefit, benefit_shift))
                    for i, u in self.projects.items()}
        out = dataclasses.replace(self, projects=projects, assumptions=list(self.assumptions))
        if cost != 1.0 or schedule != 1.0 or benefit_shift:
            out.assumptions.append(f"What-if: cost spread x{cost:g}, schedule spread x{schedule:g}, "
                                   f"benefit realization {benefit_shift:+.0%}")
        return out


# --------------------------------------------------------------------------- defaults
def default_project(p) -> ProjectUncertainty:
    r, c = max(0.0, min(1.0, p.execution_risk)), max(0.0, min(1.0, p.delivery_confidence))
    q = min(0.5, 0.05 + 0.6 * r)
    return ProjectUncertainty(
        effort=Dist("pert", (0.90, 1.00, 1.0 + 0.8 * r + 1e-6)),
        duration=Dist("pert", (0.95, 1.00, 1.0 + 1.0 * r + 1e-6)),
        benefit=Dist("pert", (0.5 + 0.3 * c, 0.85 + 0.15 * c, 1.10)),
        start_slip=Dist("discrete", (), (0.0, 1.0, 2.0), (1 - q, 0.7 * q, 0.3 * q)),
        failure_prob=0.5 * r * (1 - c), failure_loss=0.75)


def default_pool(pool, horizon: int) -> PoolUncertainty:
    temporary = pool.end_month is not None or pool.start_month > 1
    return PoolUncertainty(
        availability=Dist("pert", (0.75, 0.95, 1.00)) if temporary else Dist("pert", (0.88, 0.98, 1.03)),
        hiring_delay=Dist("discrete", (), (0.0, 1.0, 2.0), (0.6, 0.25, 0.15)) if pool.start_month > 1
        else Dist("const", (0.0,)),
        productivity=Dist("pert", (0.92, 1.00, 1.04)))


def default_drivers(data: PortfolioData) -> Tuple[List[RiskDriver], Dict[Tuple[str, str], float]]:
    drivers = [RiskDriver("INFL", "Cost inflation", "inflation", Dist("lognormal", (1.0, 0.025)), "cost",
                          "multiply", 0.0, "all")]
    caps = sorted({pool.capability for pool in data.pools.values()})
    for c in caps:
        drivers.append(RiskDriver(f"CAP-{c[:12].replace(' ', '')}", f"Capability shortage: {c}", "capability",
                                  Dist("pert", (0.85, 1.0, 1.03)), "availability", "multiply", 0.0, f"capability:{c}"))
    if any(p.compliance for p in data.projects.values()):
        drivers.append(RiskDriver("REG", "Regulatory change", "regulatory", Dist("bernoulli", (0.15,)), "duration",
                                  "event", 0.25, "compliance"))
    if any(p.shared_platform for p in data.projects.values()):
        drivers.append(RiskDriver("PLAT", "Shared platform delay", "infrastructure",
                                  Dist("discrete", (), (0.0, 1.0, 2.0), (0.75, 0.15, 0.10)), "start_delay", "add",
                                  0.0, "shared_platform"))
    vendors = sorted({t.split(":", 1)[1] for p in data.projects.values() for t in p.tags if t.startswith("vendor:")})
    for v in vendors:
        drivers.append(RiskDriver(f"VEND-{v}", f"Vendor delay: {v}", "vendor",
                                  Dist("discrete", (), (0.0, 1.0, 3.0), (0.7, 0.2, 0.1)), "start_delay", "add", 0.0,
                                  f"tag:vendor:{v}"))
    corr: Dict[Tuple[str, str], float] = {}
    cap_ids = [d.driver_id for d in drivers if d.category == "capability"]
    for i, a in enumerate(cap_ids):
        corr[("INFL", a)] = 0.2
        for b in cap_ids[i + 1:]:
            corr[(a, b)] = 0.4
    return drivers, corr


DEFAULT_ASSUMPTION_TEXT = [
    "Initiative effort, duration, benefit, start-slip and failure distributions are demonstration defaults "
    "derived from Execution Risk and Delivery Confidence (see docs/ASSUMPTIONS.md); calibrate before use.",
    "Pool availability, hiring delay and productivity distributions are demonstration defaults.",
    "Shared drivers (inflation, capability shortages, regulatory change, platform and vendor delays) and their "
    "correlations are demonstration defaults.",
]


def default_uncertainty(data: PortfolioData, settings: Optional[SimulationSettings] = None) -> UncertaintyModel:
    drivers, corr = default_drivers(data)
    m = UncertaintyModel({i: default_project(p) for i, p in data.projects.items()},
                         {k: default_pool(p, data.settings.horizon) for k, p in data.pools.items()},
                         drivers, corr, settings or SimulationSettings(), list(DEFAULT_ASSUMPTION_TEXT))
    m.correlation_matrix()
    return m


# --------------------------------------------------------------------------- selectors
def selects_project(selector: str, p, data: PortfolioData) -> bool:
    s = selector.strip()
    if s in ("all", "*", ""):
        return True
    if s == "compliance":
        return p.compliance
    if s == "mandatory":
        return p.mandatory
    if s == "critical":
        return p.critical
    if s == "shared_platform":
        return p.shared_platform
    kind, _, arg = s.partition(":")
    if kind == "projects":
        return p.project_id in {x.strip() for x in arg.split(",")}
    if kind == "lob":
        return p.primary_lob == arg or arg in p.secondary_lobs
    if kind == "capability":
        return arg in p.capabilities
    if kind == "tag":
        return arg in p.tags
    if kind == "type":
        return p.project_type == arg
    return False


def selects_pool(selector: str, pool) -> bool:
    s = selector.strip()
    if s in ("all", "*", ""):
        return True
    kind, _, arg = s.partition(":")
    if kind == "capability":
        return pool.capability == arg
    if kind == "pools":
        return pool.pool_id in {x.strip() for x in arg.split(",")}
    if kind == "lob":
        return pool.owning_lob == arg
    if kind == "pool_type":
        return pool.capacity_type.value.lower().startswith(arg.lower())
    return False


# --------------------------------------------------------------------------- intake sheets
from ..intake import OPTIONAL_SHEETS as SHEETS  # noqa: E402  (sheet headers live with the intake)


def _src(row) -> str:
    """Rows written from the defaults keep their 'demonstration default' note: they stay uncalibrated."""
    return "default" if "default" in str(row.get("Notes") or "").lower() else "intake"


def read_uncertainty(path: str, data: PortfolioData) -> UncertaintyModel:
    """Start from defaults and override with whatever the optional sheets provide. Rows marked in the
    sheets are recorded with source='intake' so the dashboard can show what is calibrated."""
    from openpyxl import load_workbook
    from ..intake import _rows
    wb = load_workbook(path, data_only=True)
    m = default_uncertainty(data)
    errors: List[str] = []

    def dist(v, where, fallback):
        if v is None or str(v).strip() == "":
            return fallback
        try:
            return parse_dist(v)
        except ValueError as e:
            errors.append(f"{where}: {e}")
            return fallback

    for r in _rows(wb, "Uncertainty Projects"):
        pid = str(r.get("Project ID") or "").strip()
        if pid not in m.projects:
            if pid:
                errors.append(f"Uncertainty Projects: unknown project {pid}")
            continue
        u = m.projects[pid]
        fp = r.get("Failure Probability")
        fl = r.get("Benefit Lost on Failure")
        m.projects[pid] = ProjectUncertainty(
            dist(r.get("Effort/Cost Distribution"), pid, u.effort), dist(r.get("Duration Distribution"), pid, u.duration),
            dist(r.get("Benefit Distribution"), pid, u.benefit),
            dist(r.get("Start Slip Distribution (months)"), pid, u.start_slip),
            float(fp) if fp not in (None, "") else u.failure_prob,
            float(fl) if fl not in (None, "") else u.failure_loss, _src(r))
    for r in _rows(wb, "Uncertainty Capacity"):
        pl = str(r.get("Pool ID") or "").strip()
        if pl not in m.pools:
            if pl:
                errors.append(f"Uncertainty Capacity: unknown pool {pl}")
            continue
        u = m.pools[pl]
        m.pools[pl] = PoolUncertainty(dist(r.get("Availability Distribution"), pl, u.availability),
                                      dist(r.get("Hiring Delay Distribution (months)"), pl, u.hiring_delay),
                                      dist(r.get("Productivity Distribution"), pl, u.productivity), _src(r))
    rows = list(_rows(wb, "Risk Drivers"))
    if rows:
        drivers = []
        for r in rows:
            did = str(r.get("Driver ID") or "").strip()
            if not did:
                continue
            try:
                drivers.append(RiskDriver(did, str(r.get("Name") or did), str(r.get("Category") or "other"),
                                          parse_dist(r.get("Distribution")), str(r.get("Effect") or "cost").strip(),
                                          str(r.get("Mode") or "multiply").strip(), float(r.get("Magnitude") or 0.0),
                                          str(r.get("Applies To") or "all"), _src(r)))
            except ValueError as e:
                errors.append(f"Risk Drivers {did}: {e}")
        m.drivers = drivers
        m.correlations = {}
    for r in _rows(wb, "Risk Correlations"):
        a, b = str(r.get("Driver A") or "").strip(), str(r.get("Driver B") or "").strip()
        if a and b:
            m.correlations[(a, b)] = float(r.get("Correlation") or 0.0)
    st = m.settings
    for r in _rows(wb, "Simulation Settings"):
        key, val = str(r.get("Key") or "").strip(), r.get("Value")
        if key == "allow_correlation_repair":
            m.allow_correlation_repair = str(val).strip().lower() in ("y", "yes", "true", "1")
            continue
        if not hasattr(st, key):
            errors.append(f"Simulation Settings: unknown key {key}")
            continue
        cur = getattr(st, key)
        try:
            setattr(st, key, type(cur)(val) if cur is not None else int(val))
        except (TypeError, ValueError):
            errors.append(f"Simulation Settings: bad value for {key}")
    m.settings = dataclasses.replace(st)
    if errors:
        raise ValueError("Uncertainty sheets invalid:\n  - " + "\n  - ".join(errors))
    m.correlation_matrix()
    if m.calibrated:
        m.assumptions = ["All uncertainty inputs supplied in the intake workbook."]
    else:
        m.assumptions = list(DEFAULT_ASSUMPTION_TEXT) + [
            f"Intake supplied {sum(u.source == 'intake' for u in m.projects.values())} of {len(m.projects)} "
            f"initiative rows and {sum(u.source == 'intake' for u in m.pools.values())} of {len(m.pools)} pool rows."]
    return m


def write_uncertainty_sheets(wb, m: UncertaintyModel, data: PortfolioData) -> None:
    from ..intake import _sheet
    _sheet(wb, "Uncertainty Projects", [[i, u.effort.describe(), u.duration.describe(), u.benefit.describe(),
                                         u.start_slip.describe(), round(u.failure_prob, 4), u.failure_loss,
                                         "demonstration default" if u.source == "default" else ""]
                                        for i, u in sorted(m.projects.items())])
    _sheet(wb, "Uncertainty Capacity", [[k, u.availability.describe(), u.hiring_delay.describe(),
                                         u.productivity.describe(),
                                         "demonstration default" if u.source == "default" else ""]
                                        for k, u in sorted(m.pools.items())])
    _sheet(wb, "Risk Drivers", [[d.driver_id, d.name, d.category, d.dist.describe(), d.effect, d.mode, d.magnitude,
                                 d.applies_to, "demonstration default" if d.source == "default" else ""]
                                for d in m.drivers])
    _sheet(wb, "Risk Correlations", [[a, b, rho] for (a, b), rho in sorted(m.correlations.items())])
    _sheet(wb, "Simulation Settings", [[f.name, getattr(m.settings, f.name)]
                                       for f in dataclasses.fields(m.settings) if getattr(m.settings, f.name) is not None])
    _sheet(wb, "Project Attributes", [[i, k, v] for i, p in sorted(data.projects.items())
                                      for k, v in sorted(p.attributes.items())] +
                                     [[i, t.split(":", 1)[0], t.split(":", 1)[1]] for i, p in sorted(data.projects.items())
                                      for t in p.tags if ":" in t])
