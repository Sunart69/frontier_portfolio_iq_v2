"""After the LT decision: record what was decided, re-solve with it, and publish the approved portfolio.

The pre-read shows the solver's recommendation. At the decision meeting the leadership team (LT) may
grant pending approvals, add or remove initiatives, or pull a start date forward. The portfolio office
records that in ``cycles/<cycle>/decision/lt_decision.yaml``; the agent then re-runs the committed
portfolio with those decisions applied (so the published plan is still feasible against capacity and
budgets) and writes the approved-portfolio memo and register to ``cycles/<cycle>/published/``.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import yaml

from ..domain import ANY, ApprovalStatus, PortfolioData, PortfolioTier
from ..intake import parse_month
from ..metrics import portfolio_metrics
from ..optimizer import PortfolioResult, SolveOptions, solve
from ..scenarios import add_release_rules
from .schedule import CycleSchedule

DECISION_FILE = "lt_decision.yaml"
RELEASE_ID = re.compile(r"^REL-(?P<pool>.+)-(?P<pct>\d{1,3})$")


class DecisionError(ValueError):
    pass


# --------------------------------------------------------------------------- the decision record
@dataclass
class ActionItem:
    action: str
    owner: str = ""
    due: Optional[dt.date] = None


@dataclass
class Decision:
    cycle: str
    decided_on: Optional[dt.date] = None
    decided_by: str = "Leadership team"
    approve: List[str] = field(default_factory=list)
    include: List[str] = field(default_factory=list)
    exclude: List[str] = field(default_factory=list)
    start_by: Dict[str, object] = field(default_factory=dict)
    notes: Dict[str, str] = field(default_factory=dict)
    actions: List[ActionItem] = field(default_factory=list)
    alternative: Optional[str] = None     # a strategic choice (balanced, conservative, ...) or plan id

    @property
    def recorded(self) -> bool:
        return self.decided_on is not None


def decision_path(cycle_dir: str) -> str:
    return os.path.join(cycle_dir, "decision", DECISION_FILE)


def _list(v) -> List[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [s.strip() for s in v.split(",") if s.strip()]
    return [str(x).strip() for x in v if str(x).strip()]


def _date(v, what: str) -> Optional[dt.date]:
    if v in (None, ""):
        return None
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    try:
        return dt.date.fromisoformat(str(v).strip())
    except ValueError as e:
        raise DecisionError(f"{what}: expected a date as YYYY-MM-DD, got {v!r}") from e


def load_decision(path: str) -> Decision:
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    if not isinstance(raw, dict):
        raise DecisionError(f"{os.path.basename(path)} must be a YAML mapping")
    acts = []
    for i, a in enumerate(raw.get("actions") or [], 1):
        if isinstance(a, str):
            acts.append(ActionItem(a))
        elif isinstance(a, dict) and a.get("action"):
            acts.append(ActionItem(str(a["action"]), str(a.get("owner") or ""), _date(a.get("due"), f"actions[{i}].due")))
        else:
            raise DecisionError(f"actions[{i}]: each action needs at least an 'action' text")
    return Decision(
        cycle=str(raw.get("cycle") or ""),
        decided_on=_date(raw.get("decided_on"), "decided_on"),
        decided_by=str(raw.get("decided_by") or "Leadership team"),
        approve=_list(raw.get("approve")),
        include=_list(raw.get("include")),
        exclude=_list(raw.get("exclude")),
        start_by={str(k): v for k, v in (raw.get("start_by") or {}).items()},
        notes={str(k): str(v) for k, v in (raw.get("notes") or {}).items()},
        actions=acts,
        alternative=(str(raw["alternative"]).strip() or None) if raw.get("alternative") else None,
    )


def write_decision_template(path: str, schedule: CycleSchedule, recommended: List[str],
                            pending_approvals: List[str], not_recommended: List[str],
                            alternatives: Optional[Dict[str, Dict]] = None) -> str:
    """A pre-filled record for the portfolio office to complete after the LT meeting (never overwritten)."""
    if os.path.exists(path):
        return path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    c = schedule.cycle
    ex_in = not_recommended[0] if not_recommended else "B05"
    ex_out = recommended[-1] if recommended else "A07"
    ex_ap = ", ".join(pending_approvals[:2]) or "TR-02"
    alt_lines = ""
    if alternatives:
        alt_lines = "#\n# Evaluated alternatives (set `alternative:` to adopt one instead of the recommendation):\n" + "\n".join(
            f"#   {k:<13} {v.get('name', '')[:40]:<40} {len(v.get('starts', {}))} initiatives"
            + (f", E[NPV] ${v['expected_npv'] / 1000:,.1f}M" if v.get("expected_npv") is not None else "")
            for k, v in alternatives.items() if not k.startswith("ALT-")) + "\n" + "\n".join(
            f"#   {k}  {v.get('name', '')[:40]}" for k, v in alternatives.items() if k.startswith("ALT-")) + "\n"
    text = f"""# LT decision record for {c}
# Fill this in after the LT decision meeting ({schedule.decision_date:%a %b %-d, %Y}) and merge it to main.
# The agent then re-runs the portfolio with these decisions and publishes the approved-portfolio memo
# to cycles/{c}/published/. Leave decided_on blank until LT has decided.
#
# Recommended in the pre-read ({len(recommended)}): {", ".join(recommended) or "none"}
# Not recommended: {", ".join(not_recommended) or "none"}
# Pending approvals that would add value: {", ".join(pending_approvals) or "none"}
{alt_lines}
cycle: {c}
decided_on:                # YYYY-MM-DD, the date LT decided
decided_by: Leadership team

alternative:               # optional: adopt another evaluated plan instead, e.g. conservative or ALT-1A2B3C4D
approve: []                # approvals LT granted, using the ids above, e.g. [{ex_ap}]
include: []                # initiatives LT added to the portfolio, e.g. [{ex_in}]
exclude: []                # initiatives LT removed, e.g. [{ex_out}]
start_by: {{}}               # latest start month per initiative, e.g. {{{ex_out}: 2027-09}}
notes: {{}}                  # rationale for the memo, e.g. {{{ex_in}: "Board commitment"}}
actions: []                # follow-ups, e.g.
#  - {{action: "Confirm resourcing for {ex_in}", owner: "PMO lead", due: {schedule.publish:%Y-%m-%d}}}
"""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


# --------------------------------------------------------------------------- applying it
def _validate(dec: Decision, data: PortfolioData, schedule: CycleSchedule) -> List[str]:
    errs = []
    if dec.cycle and dec.cycle != str(schedule.cycle):
        errs.append(f"cycle is {dec.cycle!r} but this record sits in the {schedule.cycle} folder")
    P = data.projects
    rules = {r.rule_id for r in data.transfers}
    for key in ("include", "exclude"):
        for pid in getattr(dec, key):
            if pid not in P:
                errs.append(f"{key}: {pid} is not an initiative in this cycle's intake")
    for pid in set(dec.include) & set(dec.exclude):
        errs.append(f"{pid} is in both include and exclude")
    for pid, m in dec.start_by.items():
        if pid not in P:
            errs.append(f"start_by: {pid} is not an initiative in this cycle's intake")
            continue
        try:
            month = parse_month(m, data.settings)
        except ValueError as e:
            errs.append(f"start_by {pid}: {e}")
            continue
        if month is None or not 1 <= month <= data.settings.horizon:
            errs.append(f"start_by {pid}: {m} is outside the planning horizon")
    for rid in dec.approve:
        mm = RELEASE_ID.match(rid)
        if rid in rules:
            continue
        if mm and mm.group("pool") in data.pools and 0 < int(mm.group("pct")) <= 100:
            continue
        errs.append(f"approve: {rid} is neither a transfer rule in the intake nor a ring-fence release "
                    f"(REL-<pool>-<percent>)")
    return errs


def apply_approvals(data: PortfolioData, approve: List[str]) -> PortfolioData:
    """Grant the approvals LT gave: configured rules become Approved; releases become approved rules."""
    d = data.copy()
    granted = set(approve)
    for r in d.transfers:
        if r.rule_id in granted:
            r.approval_status = ApprovalStatus.APPROVED
    for rid in approve:
        mm = RELEASE_ID.match(rid)
        if mm and rid not in {r.rule_id for r in d.transfers}:
            pct = int(mm.group("pct")) / 100.0
            d = add_release_rules(d, pct, pools=[mm.group("pool")])
            for r in d.transfers:
                if r.rule_id == rid:
                    r.approval_status = ApprovalStatus.APPROVED
                    r.note = "Ring-fence release approved by LT"
    return d


@dataclass
class Finalized:
    ok: bool
    reason: str = ""
    errors: List[str] = field(default_factory=list)
    result: Optional[PortfolioResult] = None
    metrics: Dict = field(default_factory=dict)
    changes: List[Dict] = field(default_factory=list)
    files: Dict[str, str] = field(default_factory=dict)


def finalize(data: PortfolioData, dec: Decision, schedule: CycleSchedule,
             recommended: Optional[Dict[str, int]] = None,
             alternatives: Optional[Dict[str, Dict]] = None, preserve: bool = False) -> Finalized:
    """Re-solve the committed portfolio with LT's decisions. Nothing pending is assumed approved.

    Two modes:
      reoptimize (v1 default)  maximize value under LT's decisions, keeping pre-read start months where
                               that costs no more than the optimality gap; approvals may add initiatives.
      preserve (v2 pre-reads)  the recommended plan was chosen on risk-adjusted grounds, so it is *kept*:
                               only LT's explicit changes alter it. Initiatives LT includes are fitted in;
                               a recommended initiative is moved, then dropped, only if LT's changes leave
                               no room for it. Approvals provide capacity; they do not add initiatives.

    If LT adopted another evaluated plan (`alternative:`), that plan's initiatives become the scope —
    everything else is out unless LT explicitly includes it — and its start months are kept where possible."""
    errs = _validate(dec, data, schedule)
    if dec.alternative and dec.alternative not in (alternatives or {}):
        errs.append(f"alternative: {dec.alternative!r} is not one of the evaluated plans "
                    f"({', '.join(sorted(alternatives or {})) or 'none recorded for this cycle'})")
    if errs:
        return Finalized(False, "decision record has errors", errs)
    if dec.alternative:
        alt = alternatives[dec.alternative]
        recommended = {i: int(m) for i, m in alt["starts"].items()}
        scope_out = set(data.projects) - set(recommended) - set(dec.include)
        dec = dataclasses.replace(dec, exclude=sorted(set(dec.exclude) | scope_out),
                                  notes={**{i: f"Not in the adopted {dec.alternative} plan" for i in scope_out
                                            if i not in dec.notes}, **dec.notes})
    d = apply_approvals(data, dec.approve)
    start_by = {pid: parse_month(m, d.settings) for pid, m in dec.start_by.items()}
    if preserve and recommended:
        res = preserve_plan(d, dec, recommended, start_by, schedule)
        if not res.feasible:
            return Finalized(False, "LT decisions cannot all be met with the approved capacity and budgets",
                             res.diagnostics or ["The solver found no feasible plan"], res)
        return Finalized(True, result=res, metrics=portfolio_metrics(res),
                         changes=changes_vs_recommendation(res, dec, recommended))
    cands = []
    if recommended:
        cand = {p: s for p, s in recommended.items() if p in d.projects and p not in dec.exclude}
        cands.append(cand)
    res = solve(d, SolveOptions(tier=PortfolioTier.COMMITTED, label=f"Approved portfolio {schedule.cycle}",
                                force_select=set(dec.include), force_exclude=set(dec.exclude),
                                start_by=start_by, candidate_starts=cands))
    if not res.feasible:
        return Finalized(False, "LT decisions cannot all be met with the approved capacity and budgets",
                         res.diagnostics or ["The solver found no feasible plan"], res)
    if recommended:
        res = prefer_recommended_plan(d, res, cands[0], start_by)
    m = portfolio_metrics(res)
    return Finalized(True, result=res, metrics=m, changes=changes_vs_recommendation(res, dec, recommended))


def preserve_plan(d: PortfolioData, dec: Decision, base: Dict[str, int], start_by: Dict[str, int],
                  schedule: CycleSchedule) -> PortfolioResult:
    """Lexicographic: keep every base start month (weight 1), else keep the initiative (0.5), honour LT's
    includes/excludes/start-by and every portfolio rule; value is only a tie-break."""
    import pulp
    from ..solvers.base import SolverSettings
    from ..solvers.compiled import compile_portfolio, to_portfolio_result
    from ..solvers.milp import HighsEngine
    scope = (set(base) | set(dec.include)) - set(dec.exclude)
    opts = SolveOptions(tier=PortfolioTier.COMMITTED, label=f"Approved portfolio {schedule.cycle}",
                        force_select=set(dec.include), force_exclude=set(d.projects) - scope, start_by=start_by)
    P = d.projects

    def customize(model, prob):
        keep = pulp.lpSum(v for (i, st), v in model.x.items() if base.get(i) == st)
        stay = pulp.lpSum(model.sel[i] for i in model.candidates if i in base)
        val = pulp.lpSum(P[i].value * v for (i, st), v in model.x.items())
        prob.setObjective(keep + 0.5 * stay + 1e-7 * val)

    compiled = compile_portfolio(d, opts, customize)
    compiled.custom_objective = True
    if compiled.issues:
        from ..optimizer import solve as legacy
        return legacy(d, opts)
    nres = HighsEngine().solve(compiled, SolverSettings(time_limit=max(30, d.settings.time_limit), mip_gap=1e-6))
    res = to_portfolio_result(compiled, nres, f"Approved portfolio {schedule.cycle}")
    if res.feasible:       # report the plan with its ordinary (enterprise) objective
        ev = compile_portfolio(d, dataclasses.replace(opts, fixed_starts=dict(res.starts), force_select=set(),
                                                      force_exclude=set(), start_by={}))
        nr = HighsEngine().solve(ev, SolverSettings(time_limit=30, mip_gap=1e-6))
        if nr.status.has_solution:
            res = to_portfolio_result(ev, nr, f"Approved portfolio {schedule.cycle}")
    return res


def prefer_recommended_plan(data: PortfolioData, free: PortfolioResult, keep: Dict[str, int],
                            start_by: Dict[str, int]) -> PortfolioResult:
    """Fit LT's changes around the pre-read schedule.

    A second solve keeps as many pre-read start months as possible while the portfolio's value stays
    within the solver's optimality gap of the best plan. Differences that small are noise, and moving
    work that owners already planned on has a real cost. The kept plan is then scored like any other."""
    import dataclasses
    floor = free.objective - abs(free.objective) * data.settings.mip_gap
    stable = solve(data, dataclasses.replace(free.options, keep_starts=dict(keep), keep_floor=floor,
                                             candidate_starts=[]), diagnose=False)
    if not stable.feasible:
        return free
    scored = solve(data, dataclasses.replace(free.options, fixed_starts=dict(stable.starts),
                                             candidate_starts=[]), diagnose=False)
    if not scored.feasible or scored.objective < floor - 1e-6:
        return free
    scored.options, scored.label = free.options, free.label
    return scored


def changes_vs_recommendation(res: PortfolioResult, dec: Decision,
                              recommended: Optional[Dict[str, int]]) -> List[Dict]:
    if recommended is None:
        return []
    data = res.data
    out = []
    final = res.starts
    why_added = ("capacity freed by the approvals LT granted" if dec.approve else "re-optimized after LT changes")
    for pid in sorted(set(final) - set(recommended)):
        out.append({"project": pid, "change": "Added",
                    "why": dec.notes.get(pid) or ("LT decision" if pid in dec.include else why_added)})
    for pid in sorted(set(recommended) - set(final)):
        out.append({"project": pid, "change": "Removed",
                    "why": dec.notes.get(pid) or ("LT decision" if pid in dec.exclude
                                                  else "displaced by LT's other changes")})
    for pid in sorted(set(final) & set(recommended)):
        if final[pid] != recommended[pid]:
            out.append({"project": pid, "change": (f"Start moved {data.settings.month_label(recommended[pid])} → "
                                                   f"{data.settings.month_label(final[pid])}"),
                        "why": dec.notes.get(pid) or ("LT start-by date" if pid in dec.start_by
                                                      else "re-sequenced to fit capacity")})
    return out


# --------------------------------------------------------------------------- the memo
def _money(k: float) -> str:
    """$K → readable dollars."""
    return f"${k / 1000:,.1f}M" if abs(k) >= 1000 else f"${k:,.0f}K"


def _signed(k: float) -> str:
    return ("+" if k >= 0 else "−") + _money(abs(k))


@dataclass
class MemoContent:
    title: str
    subtitle: str
    headline: str
    key_figures: List[Tuple[str, str]]
    portfolio: List[List[str]]           # ID, initiative, LOB, start, finish, investment, value
    by_lob: List[List[str]]              # LOB, initiatives, investment, value
    changes: List[List[str]]             # ID, initiative, change, why
    approvals: List[List[str]]           # id, what, note
    not_funded: List[List[str]]          # ID, initiative, LOB, note
    actions: List[List[str]]             # action, owner, due
    trace: str
    risk: List[List[str]] = field(default_factory=list)   # measure, value (simulated)
    risk_note: str = ""


def _risk_rows(risk: Optional[Dict]) -> Tuple[List[List[str]], str]:
    if not risk:
        return [], ""
    if "error" in risk:
        return [], f"Risk profile not available: {risk['error']}"
    p = risk["probabilities"]
    rows = [["Expected NPV", _money(risk["npv"]["mean"])],
            ["NPV range (P10 – P90)", f"{_money(risk['npv']['p10'])} – {_money(risk['npv']['p90'])}"],
            ["Probability cost stays within plan + reserve", f"{p['within_budget']['p']:.0%}"],
            ["Probability every committed date is met", f"{p['meet_key_dates']['p']:.0%}"],
            ["Probability of a capability shortage in some month", f"{p['capability_shortage']['p']:.0%}"],
            [f"Expected shortfall in the worst {1 - risk['var_cvar']['alpha']:.0%} of futures (CVaR)",
             _money(risk["var_cvar"]["cvar"])]]
    note = (f"Simulated over {risk['iterations']:,} futures (Monte Carlo, fixed plan). P10 = 10% of futures are "
            f"worse. Probabilities are model estimates that depend on the uncertainty assumptions in the intake.")
    return rows, note


def memo_content(fin: Finalized, dec: Decision, schedule: CycleSchedule,
                 recommended_net_value: Optional[float], risk: Optional[Dict] = None) -> MemoContent:
    res, m = fin.result, fin.metrics
    data = res.data
    P = data.projects
    lob_name = lambda l: data.lobs[l].name if l in data.lobs else l
    sel = sorted(res.starts, key=lambda p: (res.starts[p], p))
    rows = [[p, P[p].name, lob_name(P[p].primary_lob), data.settings.month_label(res.starts[p]),
             data.settings.month_label(res.end(p)), _money(sum(P[p].cost.values())), _money(P[p].value)] for p in sel]
    by_lob = []
    keys = list(data.lobs) + [k for k in list(m["investment_by_lob"]) + list(m["lob_value"]) if k not in data.lobs]
    for l in dict.fromkeys(keys):
        inv, val = m["investment_by_lob"].get(l, 0.0), m["lob_value"].get(l, 0.0)
        n = len(m["selected_by_lob"].get(l, []))
        if not (inv or val or n):
            continue
        name = lob_name(l) if l in data.lobs else "Enterprise-wide benefit (not attributed to one LOB)"
        by_lob.append([name, str(n) if l in data.lobs else "—", _money(inv) if inv else "—", _money(val) if val else "—"])
    by_lob.append(["Total", str(m["n_selected"]), _money(sum(m["investment_by_lob"].values())),
                   _money(m["enterprise_value"])])
    changes = [[c["project"], P[c["project"]].name, c["change"], c["why"]] for c in fin.changes]
    rules = {r.rule_id: r for r in data.transfers}
    approvals = []
    for rid in dec.approve:
        r = rules.get(rid)
        if r is None:
            continue
        dest = "any LOB" if r.dest_lob == ANY else lob_name(r.dest_lob)
        what = (f"{lob_name(r.source_lob)} releases {r.max_pct:.0%} of {r.source_pool} to {dest}"
                if r.synthetic else f"{lob_name(r.source_lob)} lends {r.source_pool} capacity to {dest}")
        used = res.rules_used.get(rid, 0.0)
        approvals.append([rid, what, dec.notes.get(rid) or (f"{used:,.0f} hours used" if used else "Not needed by the final plan")])
    not_funded = [[p, P[p].name, lob_name(P[p].primary_lob), dec.notes.get(p) or
                   ("Removed by LT" if p in dec.exclude else "Did not fit capacity and budget this cycle")]
                  for p in sorted(set(P) - set(res.starts))]
    actions = [[a.action, a.owner or "—", a.due.strftime("%b %-d, %Y") if a.due else "—"] for a in dec.actions]

    n = m["n_selected"]
    head = (f"LT approved {n} initiatives for {schedule.cycle}: {_money(m['investment'])} of investment for "
            f"{_money(m['net_value'])} of net value.")
    if dec.alternative:
        head += f" LT adopted the evaluated '{dec.alternative}' plan rather than the pre-read recommendation."
    if recommended_net_value is not None:
        diff = m["net_value"] - recommended_net_value
        if abs(diff) < 0.5 and not fin.changes:
            head += " This is the plan recommended in the pre-read."
        else:
            head += (f" That is {_signed(diff)} against the pre-read recommendation, after "
                     f"{len(fin.changes)} change{'s' if len(fin.changes) != 1 else ''}.")
    figs = [("Initiatives", str(n)), ("Investment", _money(m["investment"])),
            ("Net value", _money(m["net_value"])),
            ("Compliance coverage", f"{m['compliance_coverage']:.0%}")]
    trace = (f"Re-solved {dt.date.today():%b %-d, %Y} from cycles/{schedule.cycle}/merged_intake.xlsx and "
             f"decision/{DECISION_FILE}; solver status {res.status}. Only approvals granted by LT are used; "
             f"pending ones are not assumed.")
    return MemoContent(
        title=f"Approved portfolio, {schedule.cycle}",
        subtitle=(f"Decided {dec.decided_on:%b %-d, %Y} by {dec.decided_by} · "
                  f"plan period {data.settings.month_label(1)} to {data.settings.month_label(data.settings.horizon)}"),
        headline=head, key_figures=figs, portfolio=rows, by_lob=by_lob, changes=changes,
        approvals=approvals, not_funded=not_funded, actions=actions, trace=trace,
        risk=_risk_rows(risk)[0], risk_note=_risk_rows(risk)[1])


def _md_table(head: List[str], rows: List[List[str]]) -> str:
    esc = lambda s: str(s).replace("|", "\\|")
    lines = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    lines += ["| " + " | ".join(esc(c) for c in r) + " |" for r in rows]
    return "\n".join(lines)


PORTFOLIO_HEAD = ["ID", "Initiative", "LOB", "Start", "Finish", "Investment", "Gross value"]
LOB_HEAD = ["LOB", "Initiatives", "Investment", "Gross value"]
LOB_NOTE = ("Investment is shown against the LOB that funds it, so shared platforms part-funded by the business "
            "LOBs appear partly in their rows. Benefits are attributed once, with no double counting.")


def memo_markdown(c: MemoContent) -> str:
    parts = [f"# {c.title}", f"*{c.subtitle}*", "", c.headline, "",
             " · ".join(f"**{k}:** {v}" for k, v in c.key_figures), "",
             "## Approved initiatives", _md_table(PORTFOLIO_HEAD, c.portfolio), "",
             "## By line of business", _md_table(LOB_HEAD, c.by_lob), "", f"<sub>{LOB_NOTE}</sub>", ""]
    if c.risk or c.risk_note:
        parts += ["## Risk profile (simulated)"] + ([_md_table(["Measure", "Value"], c.risk)] if c.risk else []) + \
                 ["", f"<sub>{c.risk_note}</sub>", ""]
    if c.changes:
        parts += ["## Changes from the pre-read", _md_table(["ID", "Initiative", "Change", "Why"], c.changes), ""]
    if c.approvals:
        parts += ["## Approvals granted", _md_table(["Approval", "What it allows", "Note"], c.approvals), ""]
    if c.not_funded:
        parts += ["## Not funded this cycle", _md_table(["ID", "Initiative", "LOB", "Note"], c.not_funded), ""]
    if c.actions:
        parts += ["## Follow-up actions", _md_table(["Action", "Owner", "Due"], c.actions), ""]
    parts += ["---", f"<sub>{c.trace}</sub>", ""]
    return "\n".join(parts)


def memo_docx(c: MemoContent, path: str) -> str:
    from docx import Document
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Inches, Pt, RGBColor

    NAVY, GREY = RGBColor(0x1F, 0x3A, 0x5F), RGBColor(0x59, 0x59, 0x59)
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Inches(8.5), Inches(11)
    for side in ("left_margin", "right_margin"):
        setattr(sec, side, Inches(0.8))
    sec.top_margin = sec.bottom_margin = Inches(0.75)
    width = 8.5 - 1.6
    st = doc.styles["Normal"]
    st.font.name, st.font.size = "Arial", Pt(11)
    st.element.rPr.rFonts.set(qn("w:eastAsia"), "Arial")
    st.paragraph_format.space_after = Pt(4)
    for name, size in (("Title", 22), ("Heading 1", 13)):
        s = doc.styles[name]
        s.font.name, s.font.size, s.font.bold, s.font.color.rgb = "Arial", Pt(size), True, NAVY
        rf = s.element.rPr.rFonts
        for attr in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):
            rf.attrib.pop(qn(attr), None)
        rf.set(qn("w:ascii"), "Arial"), rf.set(qn("w:hAnsi"), "Arial")
        s.paragraph_format.space_before = Pt(0 if name == "Title" else 12)
        s.paragraph_format.space_after = Pt(4)
        s.paragraph_format.keep_with_next = True

    def shade(cell, hex_fill):
        tcPr = cell._tc.get_or_add_tcPr()
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear"), shd.set(qn("w:color"), "auto"), shd.set(qn("w:fill"), hex_fill)
        tcPr.append(shd)

    def table(head, rows, widths, numeric=()):
        t = doc.add_table(rows=1, cols=len(head))
        t.style = "Table Grid"
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        scale = width / sum(widths)
        for i, h in enumerate(head):
            cell = t.rows[0].cells[i]
            cell.text = ""
            r = cell.paragraphs[0].add_run(h)
            r.bold, r.font.size, r.font.color.rgb = True, Pt(9.5), RGBColor(0xFF, 0xFF, 0xFF)
            shade(cell, "1F3A5F")
        for row in rows:
            cells = t.add_row().cells
            for i, v in enumerate(row):
                cells[i].text = ""
                p = cells[i].paragraphs[0]
                r = p.add_run(str(v))
                r.font.size = Pt(9.5)
                if i in numeric:
                    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        trPr = t.rows[0]._tr.get_or_add_trPr()   # repeat the header row on each page
        hdr = OxmlElement("w:tblHeader")
        hdr.set(qn("w:val"), "true")
        trPr.append(hdr)
        t.autofit = False
        twips = [int(w * scale * 1440) for w in widths]
        grid = t._tbl.tblGrid
        for i, gc in enumerate(grid.findall(qn("w:gridCol"))):
            gc.set(qn("w:w"), str(twips[i]))
        tblPr = t._tbl.tblPr
        tblW = tblPr.find(qn("w:tblW"))
        if tblW is None:
            tblW = OxmlElement("w:tblW")
            tblPr.append(tblW)
        tblW.set(qn("w:w"), str(sum(twips))), tblW.set(qn("w:type"), "dxa")
        layout = OxmlElement("w:tblLayout")
        layout.set(qn("w:type"), "fixed")
        tblPr.append(layout)
        for row in t.rows:
            cant = OxmlElement("w:cantSplit")
            row._tr.get_or_add_trPr().append(cant)
            for i, cell in enumerate(row.cells):
                cell.width = Inches(widths[i] * scale)
        doc.add_paragraph().paragraph_format.space_after = Pt(0)
        return t

    doc.add_paragraph(c.title, style="Title")
    p = doc.add_paragraph()
    r = p.add_run(c.subtitle)
    r.italic, r.font.color.rgb, r.font.size = True, GREY, Pt(10)
    doc.add_paragraph(c.headline)
    p = doc.add_paragraph()
    for i, (k, v) in enumerate(c.key_figures):
        if i:
            p.add_run("   ·   ").font.color.rgb = GREY
        p.add_run(f"{k}: ").font.color.rgb = GREY
        p.add_run(v).bold = True

    doc.add_paragraph("Approved initiatives", style="Heading 1")
    table(PORTFOLIO_HEAD, c.portfolio, [0.4, 1.95, 1.4, 0.75, 0.75, 0.9, 0.75], numeric=(5, 6))
    doc.add_paragraph("By line of business", style="Heading 1")
    lt = table(LOB_HEAD, c.by_lob, [3.4, 1.0, 1.2, 1.3], numeric=(1, 2, 3))
    for cell in lt.rows[-1].cells:
        for r in cell.paragraphs[0].runs:
            r.bold = True
    p = doc.add_paragraph()
    r = p.add_run(LOB_NOTE)
    r.font.size, r.font.color.rgb = Pt(9), GREY
    if c.risk or c.risk_note:
        doc.add_paragraph("Risk profile (simulated)", style="Heading 1")
        if c.risk:
            table(["Measure", "Value"], c.risk, [5.0, 2.0], numeric=(1,))
        p = doc.add_paragraph()
        r = p.add_run(c.risk_note)
        r.font.size, r.font.color.rgb = Pt(9), GREY
    if c.changes:
        doc.add_paragraph("Changes from the pre-read", style="Heading 1")
        table(["ID", "Initiative", "Change", "Why"], c.changes, [0.6, 2.2, 1.9, 2.3])
    if c.approvals:
        doc.add_paragraph("Approvals granted", style="Heading 1")
        table(["Approval", "What it allows", "Note"], c.approvals, [1.5, 3.3, 2.2])
    if c.not_funded:
        doc.add_paragraph("Not funded this cycle", style="Heading 1")
        table(["ID", "Initiative", "LOB", "Note"], c.not_funded, [0.6, 2.4, 1.6, 2.4])
    if c.actions:
        doc.add_paragraph("Follow-up actions", style="Heading 1")
        table(["Action", "Owner", "Due"], c.actions, [3.8, 1.9, 1.3])
    p = doc.add_paragraph()
    r = p.add_run(c.trace)
    r.font.size, r.font.color.rgb = Pt(8.5), GREY
    doc.core_properties.title = c.title
    doc.save(path)
    return path


def register_xlsx(fin: Finalized, dec: Decision, path: str) -> str:
    """The approved plan as a working list for PMOs: one row per initiative, funded or not."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    res = fin.result
    data, P = res.data, res.data.projects
    wb = Workbook()
    ws = wb.active
    ws.title = "Approved portfolio"
    head = ["Initiative ID", "Initiative", "Primary LOB", "Decision", "Start", "Finish", "Investment ($K)",
            "Enterprise value ($K)", "Executive sponsor", "Outcome owner", "Note"]
    ws.append(head)
    for c in ws[1]:
        c.font, c.fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="1F3A5F")
    for pid in sorted(P, key=lambda p: (p not in res.starts, res.starts.get(p, 99), p)):
        p = P[pid]
        on = pid in res.starts
        ws.append([pid, p.name, data.lobs[p.primary_lob].name if p.primary_lob in data.lobs else p.primary_lob,
                   "Approved" if on else "Not funded", data.settings.month_label(res.starts[pid]) if on else "",
                   data.settings.month_label(res.end(pid)) if on else "", round(sum(p.cost.values()), 1),
                   round(p.value, 1), p.executive_sponsor, p.outcome_owner, dec.notes.get(pid, "")])
    for col, w in zip("ABCDEFGHIJK", (12, 36, 26, 12, 10, 10, 15, 19, 22, 22, 36)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"
    if dec.actions:
        wa = wb.create_sheet("Follow-up actions")
        wa.append(["Action", "Owner", "Due"])
        for c in wa[1]:
            c.font, c.fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="1F3A5F")
        for a in dec.actions:
            wa.append([a.action, a.owner, a.due])
        wa.column_dimensions["A"].width, wa.column_dimensions["B"].width = 60, 26
        wa.column_dimensions["C"].width = 14
    wb.save(path)
    return path


def publish(fin: Finalized, dec: Decision, schedule: CycleSchedule, out_dir: str,
            recommended_net_value: Optional[float] = None, risk: Optional[Dict] = None) -> Dict[str, str]:
    if not fin.ok:
        raise DecisionError(f"Cannot publish: {fin.reason}")
    os.makedirs(out_dir, exist_ok=True)
    c = memo_content(fin, dec, schedule, recommended_net_value, risk)
    stem = f"approved_portfolio_{schedule.cycle}"
    files = {
        "memo_md": os.path.join(out_dir, f"{stem}.md"),
        "memo_docx": os.path.join(out_dir, f"{stem}.docx"),
        "register": os.path.join(out_dir, f"{stem}.xlsx"),
        "summary": os.path.join(out_dir, "decision_summary.json"),
    }
    with open(files["memo_md"], "w", encoding="utf-8") as fh:
        fh.write(memo_markdown(c))
    memo_docx(c, files["memo_docx"])
    register_xlsx(fin, dec, files["register"])
    m = fin.metrics
    with open(files["summary"], "w") as fh:
        json.dump({"cycle": str(schedule.cycle), "decided_on": dec.decided_on, "decided_by": dec.decided_by,
                   "approvals_granted": dec.approve, "include": dec.include, "exclude": dec.exclude,
                   "n_selected": m["n_selected"], "investment": m["investment"], "net_value": m["net_value"],
                   "recommended_net_value": recommended_net_value, "starts": m["starts"],
                   "changes": fin.changes, "solver_status": fin.result.status, "alternative": dec.alternative,
                   "risk": None if not risk or "error" in risk else
                   {"expected_npv": risk["npv"]["mean"], "p10_npv": risk["npv"]["p10"], "p90_npv": risk["npv"]["p90"],
                    "p_within_budget": risk["probabilities"]["within_budget"]["p"],
                    "p_meet_key_dates": risk["probabilities"]["meet_key_dates"]["p"],
                    "cvar": risk["var_cvar"]["cvar"], "iterations": risk["iterations"]}},
                  fh, indent=2, default=str)
    fin.files = files
    return files
