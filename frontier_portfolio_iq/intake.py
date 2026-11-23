"""Excel intake: read / write / validate the multi-LOB portfolio workbook."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .domain import (
    ANY, LOB, ApprovalStatus, BenefitAllocation, BudgetLine, CapacityPool, CapacityType,
    EligibilityRule, PortfolioData, Project, Settings, StrategicTarget, Substitution, TransferRule,
)

MONTH_NAMES = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]

SHEETS: Dict[str, List[str]] = {
    "Settings": ["Key", "Value"],
    "LOB Registry": ["LOB ID", "LOB Name", "Executive Sponsor", "Business Outcome Owner",
                     "Strategic Priority", "Budget Owner", "Enterprise Entity", "Risk Tolerance",
                     "Maturity Target"],
    "LOB Budget Allocation": ["LOB ID", "Month", "Funding Source", "Available Budget ($K)",
                              "Transferability", "Funding Restrictions"],
    "Capability Pool Ownership": ["Pool ID", "Capability Area", "Owning LOB", "Capacity Type",
                                  "Start Month", "End Month", "Available FTE",
                                  "Productive Hours per FTE", "Utilization Factor",
                                  "Cost Rate ($/hr)", "Incremental Cost Rate ($/hr)",
                                  "Release Eligible", "Release Efficiency", "Release Cost ($/hr)",
                                  "Allowed Funding Sources", "Funding Restrictions"],
    "Capability Eligibility": ["Pool ID", "Eligible LOB", "Eligible Project Type",
                               "Authorized Project ID", "Allocation Restrictions"],
    "Capacity Transfer Rules": ["Rule ID", "Source Pool", "Source LOB", "Destination LOB",
                                "Maximum Transferable Hours", "Maximum Transfer %",
                                "Transfer Efficiency", "Transfer Cost ($/hr)", "Approval Required",
                                "Approval Status", "Effective Start Month", "Effective End Month",
                                "Minimum Retained Hours", "Notes"],
    "LOB Strategic Targets": ["LOB ID", "KPI", "Target", "Minimum Outcome Requirement ($K)",
                              "Min Investment ($K)", "Max Investment ($K)"],
    "Cross-LOB Benefit Allocation": ["Project ID", "Benefiting LOB", "KPI",
                                     "Attributed Benefit ($K)", "Benefit Ownership"],
    "Projects": ["Project ID", "Project Name", "Primary LOB", "Secondary LOBs", "Initiative", "OKR",
                 "Executive Sponsor", "Business Outcome Owner", "Funding Owner", "Funding Source",
                 "Project Type", "LOB Priority", "Enterprise Contribution", "Shared Platform",
                 "Mandatory", "Compliance", "Critical", "Business Value ($K)", "Risk Reduction",
                 "Maturity Gain", "Delivery Confidence", "Execution Risk", "Duration (months)",
                 "Earliest Start", "Deadline", "Depends On"],
    "Project Funding Shares": ["Project ID", "LOB ID", "Share"],
    "Project Demand": ["Project ID", "Capability", "Relative Month", "Hours"],
    "Project Costs": ["Project ID", "Relative Month", "Cost ($K)"],
    "Capability Substitution": ["Supply Capability", "Demand Capability", "Efficiency"],
}


# Optional v2 sheets (uncertainty, risk drivers, simulation settings, custom attributes). A workbook
# without them is valid: documented demonstration defaults are used and flagged as uncalibrated.
OPTIONAL_SHEETS: Dict[str, List[str]] = {
    "Uncertainty Projects": ["Project ID", "Effort/Cost Distribution", "Duration Distribution",
                             "Benefit Distribution", "Start Slip Distribution (months)", "Failure Probability",
                             "Benefit Lost on Failure", "Notes"],
    "Uncertainty Capacity": ["Pool ID", "Availability Distribution", "Hiring Delay Distribution (months)",
                             "Productivity Distribution", "Notes"],
    "Risk Drivers": ["Driver ID", "Name", "Category", "Distribution", "Effect", "Mode", "Magnitude",
                     "Applies To", "Notes"],
    "Risk Correlations": ["Driver A", "Driver B", "Correlation"],
    "Simulation Settings": ["Key", "Value"],
    "Project Attributes": ["Project ID", "Attribute", "Value"],
    "Decision Rules": ["Profile", "Criterion", "Weight", "Constraint Operator", "Constraint Value", "Notes"],
}


class IntakeError(Exception):
    def __init__(self, report: "ValidationReport"):
        super().__init__("Workbook validation failed:\n  - " + "\n  - ".join(report.errors))
        self.report = report


@dataclass
class ValidationReport:
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    unfundable_projects: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


# --------------------------------------------------------------------------- parsing helpers
def _blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


def _bool(v: Any, default: bool = False) -> bool:
    if _blank(v):
        return default
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in {"y", "yes", "true", "1", "x"}


def _float(v: Any, default: Optional[float] = None) -> Optional[float]:
    if _blank(v):
        return default
    if isinstance(v, str) and v.strip().endswith("%"):
        return float(v.strip()[:-1]) / 100.0
    return float(v)


def _list(v: Any) -> List[str]:
    if _blank(v):
        return []
    return [s.strip() for s in str(v).replace(";", ",").split(",") if s.strip()]


def parse_month(v: Any, settings: Settings) -> Optional[int]:
    """Accept 1-based ints, month names ('April') or 'YYYY-MM' relative to the horizon start."""
    if _blank(v):
        return None
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip()
    if s.isdigit():
        return int(s)
    y0, m0 = (int(p) for p in settings.first_month_label.split("-"))
    if len(s) >= 7 and s[4] == "-" and s[:4].isdigit():
        y, m = int(s[:4]), int(s[5:7])
        return (y - y0) * 12 + (m - m0) + 1
    key = s[:3].lower()
    if key in MONTH_NAMES:
        m = MONTH_NAMES.index(key) + 1
        rel = m - m0 + 1
        return rel if rel >= 1 else rel + 12
    raise ValueError(f"Cannot parse month {v!r}")


def _rows(wb, sheet: str) -> Iterable[Dict[str, Any]]:
    if sheet not in wb.sheetnames:
        return []
    ws = wb[sheet]
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    header = [str(h).strip() if h is not None else "" for h in rows[0]]
    out = []
    for r in rows[1:]:
        if r is None or all(_blank(c) for c in r):
            continue
        out.append({header[i]: r[i] for i in range(min(len(header), len(r)))})
    return out


# --------------------------------------------------------------------------- read
def read_workbook(path: str, validate_strict: bool = True) -> PortfolioData:
    wb = load_workbook(path, data_only=True)
    settings = Settings()
    for r in _rows(wb, "Settings"):
        key, val = str(r["Key"]).strip(), r.get("Value")
        if key in {"horizon", "as_of_month", "time_limit"}:
            setattr(settings, key, int(val))
        elif key in {"schedule_discount", "enterprise_reservation_pct", "balance_weight", "mip_gap",
                     "discount_rate"}:
            setattr(settings, key, float(val))
        elif key == "max_lob_concentration":
            settings.max_lob_concentration = _float(val)
        elif key in {"enforce_lob_targets", "protect_critical", "protect_compliance"}:
            setattr(settings, key, _bool(val))
        elif key == "first_month_label":
            settings.first_month_label = str(val)
        elif key.startswith("min_shared_share:"):
            settings.min_shared_share[key.split(":", 1)[1]] = float(val)
        elif key.startswith("min_investment_share:"):
            settings.min_investment_share[key.split(":", 1)[1]] = float(val)

    lobs: Dict[str, LOB] = {}
    for r in _rows(wb, "LOB Registry"):
        lid = str(r["LOB ID"]).strip()
        lobs[lid] = LOB(
            lob_id=lid, name=str(r.get("LOB Name") or lid),
            executive_sponsor=str(r.get("Executive Sponsor") or ""),
            outcome_owner=str(r.get("Business Outcome Owner") or ""),
            strategic_priority=int(r.get("Strategic Priority") or 1),
            budget_owner=str(r.get("Budget Owner") or ""),
            is_enterprise=_bool(r.get("Enterprise Entity")),
            risk_tolerance=_float(r.get("Risk Tolerance")),
            maturity_target=_float(r.get("Maturity Target")),
        )

    budgets = [
        BudgetLine(
            lob_id=str(r["LOB ID"]).strip(), month=parse_month(r["Month"], settings),
            funding_source=str(r["Funding Source"]).strip(),
            amount=float(r["Available Budget ($K)"] or 0),
            carry_forward=not str(r.get("Transferability") or "Carry-forward").lower().startswith("month"),
            allowed_project_types=_list(r.get("Funding Restrictions")),
        )
        for r in _rows(wb, "LOB Budget Allocation")
    ]

    pools: Dict[str, CapacityPool] = {}
    for r in _rows(wb, "Capability Pool Ownership"):
        pid = str(r["Pool ID"]).strip()
        pools[pid] = CapacityPool(
            pool_id=pid, capability=str(r["Capability Area"]).strip(),
            owning_lob=str(r["Owning LOB"]).strip(),
            capacity_type=CapacityType.parse(r["Capacity Type"]),
            start_month=parse_month(r["Start Month"], settings) or 1,
            end_month=parse_month(r.get("End Month"), settings),
            fte=float(r["Available FTE"]),
            hours_per_fte=_float(r.get("Productive Hours per FTE"), 140.0),
            utilization=_float(r.get("Utilization Factor"), 0.85),
            cost_rate=_float(r.get("Cost Rate ($/hr)"), 0.0),
            incremental_cost_rate=_float(r.get("Incremental Cost Rate ($/hr)"), 0.0),
            release_eligible=_bool(r.get("Release Eligible")),
            release_efficiency=_float(r.get("Release Efficiency"), 0.9),
            release_cost_rate=_float(r.get("Release Cost ($/hr)"), 0.0),
            allowed_funding_sources=_list(r.get("Allowed Funding Sources")),
            funding_restrictions=str(r.get("Funding Restrictions") or ""),
        )

    eligibility = [
        EligibilityRule(
            pool_id=str(r["Pool ID"]).strip(),
            eligible_lob=str(r.get("Eligible LOB") or ANY).strip(),
            eligible_project_type=str(r.get("Eligible Project Type") or ANY).strip(),
            authorized_project_id=str(r.get("Authorized Project ID") or "").strip(),
            allocation_restrictions=str(r.get("Allocation Restrictions") or ""),
        )
        for r in _rows(wb, "Capability Eligibility")
    ]

    transfers = [
        TransferRule(
            rule_id=str(r["Rule ID"]).strip(), source_pool=str(r["Source Pool"]).strip(),
            source_lob=str(r["Source LOB"]).strip(), dest_lob=str(r["Destination LOB"]).strip(),
            max_hours_per_month=_float(r.get("Maximum Transferable Hours")),
            max_pct=_float(r.get("Maximum Transfer %"), 1.0),
            efficiency=_float(r.get("Transfer Efficiency"), 1.0),
            cost_per_hour=_float(r.get("Transfer Cost ($/hr)"), 0.0),
            approval_required=_bool(r.get("Approval Required"), True),
            approval_status=ApprovalStatus.parse(r.get("Approval Status") or "Pending"),
            effective_start=parse_month(r.get("Effective Start Month"), settings) or 1,
            effective_end=parse_month(r.get("Effective End Month"), settings),
            min_retained_hours=_float(r.get("Minimum Retained Hours"), 0.0),
            note=str(r.get("Notes") or ""),
        )
        for r in _rows(wb, "Capacity Transfer Rules")
    ]

    targets = [
        StrategicTarget(
            lob_id=str(r["LOB ID"]).strip(), kpi=str(r["KPI"]).strip(),
            target=_float(r.get("Target"), 0.0),
            min_outcome=_float(r.get("Minimum Outcome Requirement ($K)")),
            min_investment=_float(r.get("Min Investment ($K)")),
            max_investment=_float(r.get("Max Investment ($K)")),
        )
        for r in _rows(wb, "LOB Strategic Targets")
    ]

    benefits = [
        BenefitAllocation(
            project_id=str(r["Project ID"]).strip(), lob_id=str(r["Benefiting LOB"]).strip(),
            kpi=str(r["KPI"]).strip(), attributed_benefit=float(r["Attributed Benefit ($K)"]),
            benefit_owner=str(r.get("Benefit Ownership") or ""),
        )
        for r in _rows(wb, "Cross-LOB Benefit Allocation")
    ]

    projects: Dict[str, Project] = {}
    for r in _rows(wb, "Projects"):
        pid = str(r["Project ID"]).strip()
        projects[pid] = Project(
            project_id=pid, name=str(r.get("Project Name") or pid),
            primary_lob=str(r["Primary LOB"]).strip(),
            secondary_lobs=_list(r.get("Secondary LOBs")),
            initiative=str(r.get("Initiative") or ""), okr=str(r.get("OKR") or ""),
            executive_sponsor=str(r.get("Executive Sponsor") or ""),
            outcome_owner=str(r.get("Business Outcome Owner") or ""),
            funding_owner=str(r.get("Funding Owner") or ""),
            funding_source=str(r.get("Funding Source") or "Opex").strip(),
            project_type=str(r.get("Project Type") or "Discretionary").strip(),
            lob_priority=int(r.get("LOB Priority") or 3),
            enterprise_contribution=_float(r.get("Enterprise Contribution"), 0.0),
            shared_platform=_bool(r.get("Shared Platform")),
            mandatory=_bool(r.get("Mandatory")), compliance=_bool(r.get("Compliance")),
            critical=_bool(r.get("Critical")),
            value=float(r.get("Business Value ($K)") or 0),
            risk_reduction=_float(r.get("Risk Reduction"), 0.0),
            maturity_gain=_float(r.get("Maturity Gain"), 0.0),
            delivery_confidence=_float(r.get("Delivery Confidence"), 0.8),
            execution_risk=_float(r.get("Execution Risk"), 0.2),
            duration=int(r.get("Duration (months)") or 1),
            earliest_start=parse_month(r.get("Earliest Start"), settings) or 1,
            deadline=parse_month(r.get("Deadline"), settings),
            depends_on=_list(r.get("Depends On")),
        )
    for r in _rows(wb, "Project Funding Shares"):
        pid = str(r["Project ID"]).strip()
        if pid in projects:
            projects[pid].funding_shares[str(r["LOB ID"]).strip()] = float(r["Share"])
    for r in _rows(wb, "Project Demand"):
        pid = str(r["Project ID"]).strip()
        if pid in projects:
            key = (str(r["Capability"]).strip(), int(r["Relative Month"]))
            projects[pid].demand[key] = projects[pid].demand.get(key, 0.0) + float(r["Hours"])
    for r in _rows(wb, "Project Costs"):
        pid = str(r["Project ID"]).strip()
        if pid in projects:
            k = int(r["Relative Month"])
            projects[pid].cost[k] = projects[pid].cost.get(k, 0.0) + float(r["Cost ($K)"])

    substitutions = [
        Substitution(str(r["Supply Capability"]).strip(), str(r["Demand Capability"]).strip(),
                     float(r["Efficiency"]))
        for r in _rows(wb, "Capability Substitution")
    ]

    data = PortfolioData(lobs, projects, pools, eligibility, transfers, budgets, targets,
                         benefits, substitutions, settings)
    for r in _rows(wb, "Project Attributes"):     # optional v2 sheet: custom attributes and tags
        pid = str(r.get("Project ID") or "").strip()
        key, val = str(r.get("Attribute") or "").strip(), r.get("Value")
        if pid not in data.projects or not key:
            continue
        try:
            data.projects[pid].attributes[key] = float(val)
        except (TypeError, ValueError):
            tag = f"{key}:{str(val).strip()}"
            if tag not in data.projects[pid].tags:
                data.projects[pid].tags.append(tag)
    report = validate(data)
    if validate_strict and not report.ok:
        raise IntakeError(report)
    return data


# --------------------------------------------------------------------------- validate
def validate(data: PortfolioData) -> ValidationReport:
    rep = ValidationReport()
    E, W = rep.errors.append, rep.warnings.append
    H = data.settings.horizon
    lobs, pools, projects = data.lobs, data.pools, data.projects

    def lob_ok(lid: str, where: str, allow_any: bool = False):
        if allow_any and lid == ANY:
            return
        if lid not in lobs:
            E(f"{where}: unknown LOB ID '{lid}'")

    # Projects
    for p in projects.values():
        lob_ok(p.primary_lob, f"Project {p.project_id} primary LOB")
        for s in p.secondary_lobs:
            lob_ok(s, f"Project {p.project_id} secondary LOB")
        shares = p.shares()
        for l in shares:
            lob_ok(l, f"Project {p.project_id} funding share")
        if abs(sum(shares.values()) - 1.0) > 1e-3:
            E(f"Project {p.project_id}: funding shares sum to {sum(shares.values()):.3f}, expected 1.0")
        for d in p.depends_on:
            if d not in projects:
                E(f"Project {p.project_id}: depends on unknown project '{d}'")
        if p.duration < 1:
            E(f"Project {p.project_id}: duration must be >= 1")
        for (_c, k) in p.demand:
            if k < 1 or k > p.duration:
                E(f"Project {p.project_id}: demand relative month {k} outside duration {p.duration}")
        for k in p.cost:
            if k < 1 or k > p.duration:
                E(f"Project {p.project_id}: cost relative month {k} outside duration {p.duration}")
        last = min(H, p.deadline or H)
        if p.earliest_start + p.duration - 1 > last:
            msg = f"Project {p.project_id}: cannot finish within horizon/deadline (earliest {p.earliest_start}, duration {p.duration}, deadline {last})"
            (E if p.mandatory else W)(msg)
        for c in p.capabilities:
            if not any(data.substitution_efficiency(pl.capability, c) > 0 for pl in pools.values()):
                E(f"Project {p.project_id}: no capacity pool can supply capability '{c}'")
        # funding relationships
        for l in shares:
            lines = [b for b in data.budgets if b.lob_id == l and b.funding_source == p.funding_source]
            if not lines:
                E(f"Project {p.project_id}: LOB '{l}' has no '{p.funding_source}' budget lines")
                continue
            types = data.budget_types(l, p.funding_source)
            if types and p.project_type not in types:
                msg = (f"Project {p.project_id}: '{p.funding_source}' funding of LOB '{l}' is restricted "
                       f"to {types}; project type '{p.project_type}' is not fundable")
                (E if p.mandatory else W)(msg)
                if p.project_id not in rep.unfundable_projects:
                    rep.unfundable_projects.append(p.project_id)

    # dependency cycles
    seen, stack = set(), set()

    def visit(pid: str) -> bool:
        if pid in stack:
            return True
        if pid in seen or pid not in projects:
            return False
        seen.add(pid)
        stack.add(pid)
        cyc = any(visit(d) for d in projects[pid].depends_on)
        stack.discard(pid)
        return cyc

    for pid in projects:
        if visit(pid):
            E(f"Dependency cycle detected involving project {pid}")
            break

    # Pools
    for pl in pools.values():
        lob_ok(pl.owning_lob, f"Pool {pl.pool_id} owning LOB")
        if pl.start_month < 1:
            E(f"Pool {pl.pool_id}: start month must be >= 1")
        if pl.end_month is not None and pl.end_month < pl.start_month:
            E(f"Pool {pl.pool_id}: end month {pl.end_month} precedes start month {pl.start_month}")
        if not (0 < pl.utilization <= 1):
            E(f"Pool {pl.pool_id}: utilization factor must be in (0, 1]")
        if pl.fte < 0:
            E(f"Pool {pl.pool_id}: FTE must be non-negative")
        if pl.release_eligible and pl.capacity_type == CapacityType.SHARED:
            W(f"Pool {pl.pool_id}: release-eligible flag ignored for shared pools")

    for e in data.eligibility:
        if e.pool_id not in pools:
            E(f"Eligibility: unknown pool '{e.pool_id}'")
        lob_ok(e.eligible_lob, f"Eligibility for pool {e.pool_id}", allow_any=True)
        if e.authorized_project_id and e.authorized_project_id not in projects:
            E(f"Eligibility for pool {e.pool_id}: unknown authorized project '{e.authorized_project_id}'")

    rule_ids = set()
    for t in data.transfers:
        if t.rule_id in rule_ids:
            E(f"Transfer rule '{t.rule_id}' duplicated")
        rule_ids.add(t.rule_id)
        if t.source_pool not in pools:
            E(f"Transfer {t.rule_id}: unknown source pool '{t.source_pool}'")
            continue
        pl = pools[t.source_pool]
        if t.source_lob != pl.owning_lob:
            E(f"Transfer {t.rule_id}: source LOB '{t.source_lob}' does not own pool {pl.pool_id} (owner '{pl.owning_lob}')")
        lob_ok(t.dest_lob, f"Transfer {t.rule_id} destination", allow_any=True)
        if t.dest_lob == t.source_lob:
            E(f"Transfer {t.rule_id}: destination equals source LOB")
        if pl.capacity_type == CapacityType.SHARED:
            W(f"Transfer {t.rule_id}: pool {pl.pool_id} is shared; transfer rule is redundant")
        if pl.capacity_type == CapacityType.RING_FENCED:
            W(f"Transfer {t.rule_id}: acts as explicit release mechanism for ring-fenced pool {pl.pool_id}")
        if not (0 < t.efficiency <= 1):
            E(f"Transfer {t.rule_id}: efficiency must be in (0, 1]")
        if not (0 <= t.max_pct <= 1):
            E(f"Transfer {t.rule_id}: maximum transfer % must be in [0, 1]")
        if t.effective_end is not None and t.effective_end < t.effective_start:
            E(f"Transfer {t.rule_id}: effective end precedes start")

    # Budgets / funding
    for b in data.budgets:
        lob_ok(b.lob_id, "Budget line")
        if b.month is None or not (1 <= b.month <= H):
            E(f"Budget line {b.lob_id}/{b.funding_source}: month {b.month} outside horizon 1..{H}")
    modes: Dict[tuple, set] = {}
    for b in data.budgets:
        modes.setdefault((b.lob_id, b.funding_source), set()).add(b.carry_forward)
    for k, v in modes.items():
        if len(v) > 1:
            W(f"Budget {k[0]}/{k[1]}: mixed transferability flags; treating as month-locked")

    # Targets
    for t in data.targets:
        lob_ok(t.lob_id, f"Strategic target {t.kpi}")
        if t.min_investment is not None and t.max_investment is not None and t.min_investment > t.max_investment:
            E(f"Strategic target {t.lob_id}/{t.kpi}: min investment exceeds max investment")

    # Benefits: no double counting of enterprise value
    totals: Dict[str, float] = {}
    for b in data.benefits:
        if b.project_id not in projects:
            E(f"Benefit allocation: unknown project '{b.project_id}'")
            continue
        lob_ok(b.lob_id, f"Benefit allocation for {b.project_id}")
        if b.attributed_benefit < 0:
            E(f"Benefit allocation {b.project_id}/{b.lob_id}: negative benefit")
        totals[b.project_id] = totals.get(b.project_id, 0.0) + b.attributed_benefit
    for pid, tot in totals.items():
        if tot > projects[pid].value + 1e-6:
            E(f"Project {pid}: attributed LOB benefits ({tot:,.0f}) exceed total enterprise value "
              f"({projects[pid].value:,.0f}) — benefit would be double-counted")
        p = projects[pid]
        benefiting = {b.lob_id for b in data.benefits if b.project_id == pid}
        extra = benefiting - {p.primary_lob, *p.secondary_lobs}
        if extra:
            W(f"Project {pid}: benefit attributed to {sorted(extra)} not listed as primary/secondary LOB")
    return rep


# --------------------------------------------------------------------------- write
HEADER_FILL = PatternFill("solid", fgColor="1F3A5F")
HEADER_FONT = Font(color="FFFFFF", bold=True)


def _sheet(wb: Workbook, name: str, rows: List[List[Any]]):
    ws = wb.create_sheet(name)
    headers = SHEETS.get(name) or OPTIONAL_SHEETS[name]
    ws.append(headers)
    for r in rows:
        ws.append(r)
    for c in ws[1]:
        c.fill, c.font = HEADER_FILL, HEADER_FONT
        c.alignment = Alignment(wrap_text=True, vertical="center")
    for i, h in enumerate(headers, start=1):
        ws.column_dimensions[get_column_letter(i)].width = max(12, min(34, len(h) + 4))
    ws.freeze_panes = "A2"
    return ws


def _yn(b: bool) -> str:
    return "Y" if b else "N"


def write_workbook(data: PortfolioData, path: str, uncertainty=None) -> str:
    """Write the intake workbook. With `uncertainty` (a risk.UncertaintyModel) the optional v2 sheets are
    written too, pre-filled with the model's values (demonstration defaults are marked as such)."""
    wb = Workbook()
    wb.remove(wb.active)
    s = data.settings
    rows = [["horizon", s.horizon], ["first_month_label", s.first_month_label],
            ["as_of_month", s.as_of_month], ["schedule_discount", s.schedule_discount],
            ["enforce_lob_targets", _yn(s.enforce_lob_targets)],
            ["protect_critical", _yn(s.protect_critical)],
            ["protect_compliance", _yn(s.protect_compliance)],
            ["max_lob_concentration", s.max_lob_concentration],
            ["enterprise_reservation_pct", s.enterprise_reservation_pct],
            ["balance_weight", s.balance_weight], ["time_limit", s.time_limit], ["mip_gap", s.mip_gap],
            ["discount_rate", s.discount_rate]]
    rows += [[f"min_shared_share:{k}", v] for k, v in s.min_shared_share.items()]
    rows += [[f"min_investment_share:{k}", v] for k, v in s.min_investment_share.items()]
    _sheet(wb, "Settings", rows)
    _sheet(wb, "LOB Registry", [[l.lob_id, l.name, l.executive_sponsor, l.outcome_owner,
                                 l.strategic_priority, l.budget_owner, _yn(l.is_enterprise),
                                 l.risk_tolerance, l.maturity_target] for l in data.lobs.values()])
    _sheet(wb, "LOB Budget Allocation", [[b.lob_id, b.month, b.funding_source, b.amount,
                                          "Carry-forward" if b.carry_forward else "Month-locked",
                                          ", ".join(b.allowed_project_types)] for b in data.budgets])
    _sheet(wb, "Capability Pool Ownership", [[p.pool_id, p.capability, p.owning_lob,
                                              p.capacity_type.value, p.start_month, p.end_month, p.fte,
                                              p.hours_per_fte, p.utilization, p.cost_rate,
                                              p.incremental_cost_rate, _yn(p.release_eligible),
                                              p.release_efficiency, p.release_cost_rate,
                                              ", ".join(p.allowed_funding_sources),
                                              p.funding_restrictions] for p in data.pools.values()])
    _sheet(wb, "Capability Eligibility", [[e.pool_id, e.eligible_lob, e.eligible_project_type,
                                           e.authorized_project_id, e.allocation_restrictions]
                                          for e in data.eligibility])
    _sheet(wb, "Capacity Transfer Rules", [[t.rule_id, t.source_pool, t.source_lob, t.dest_lob,
                                            t.max_hours_per_month, t.max_pct, t.efficiency,
                                            t.cost_per_hour, _yn(t.approval_required),
                                            t.approval_status.value, t.effective_start,
                                            t.effective_end, t.min_retained_hours, t.note]
                                           for t in data.transfers if not t.synthetic])
    _sheet(wb, "LOB Strategic Targets", [[t.lob_id, t.kpi, t.target, t.min_outcome,
                                          t.min_investment, t.max_investment] for t in data.targets])
    _sheet(wb, "Cross-LOB Benefit Allocation", [[b.project_id, b.lob_id, b.kpi,
                                                 b.attributed_benefit, b.benefit_owner]
                                                for b in data.benefits])
    _sheet(wb, "Projects", [[p.project_id, p.name, p.primary_lob, ", ".join(p.secondary_lobs),
                             p.initiative, p.okr, p.executive_sponsor, p.outcome_owner,
                             p.funding_owner, p.funding_source, p.project_type, p.lob_priority,
                             p.enterprise_contribution, _yn(p.shared_platform), _yn(p.mandatory),
                             _yn(p.compliance), _yn(p.critical), p.value, p.risk_reduction,
                             p.maturity_gain, p.delivery_confidence, p.execution_risk, p.duration,
                             p.earliest_start, p.deadline, ", ".join(p.depends_on)]
                            for p in data.projects.values()])
    _sheet(wb, "Project Funding Shares", [[p.project_id, l, sh] for p in data.projects.values()
                                          for l, sh in p.shares().items()])
    _sheet(wb, "Project Demand", [[p.project_id, c, k, h] for p in data.projects.values()
                                  for (c, k), h in sorted(p.demand.items(), key=lambda kv: (kv[0][1], kv[0][0]))])
    _sheet(wb, "Project Costs", [[p.project_id, k, v] for p in data.projects.values()
                                 for k, v in sorted(p.cost.items())])
    _sheet(wb, "Capability Substitution", [[s.from_capability, s.to_capability, s.efficiency]
                                           for s in data.substitutions])
    if uncertainty is not None:
        from .risk.uncertainty import write_uncertainty_sheets
        write_uncertainty_sheets(wb, uncertainty, data)
    wb.save(path)
    return path
