"""Stakeholder input-collection deck (.pptx), generated per cycle from the cadence schedule.

Shared at kickoff: why inputs matter, the workback calendar, what each owner must send and when,
how to submit, how follow-ups and escalations work, and what happens after the inputs are in.
"""
from __future__ import annotations

import datetime as dt
from typing import Dict, Optional

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt

from ..deck import BODY, HEAD, DeckBuilder, _rgb
from ..style import CATEGORICAL, GRID, INK, INK_SOFT, MUTED, STATUS, TEXT, TEXT_2
from .config import CadenceConfig, Owners
from .packages import ENTERPRISE, PACKAGES, package_filename
from .schedule import CycleSchedule, add_business_days

ACCENT, AMBER = CATEGORICAL[0], "D9900F"


class StakeholderDeck(DeckBuilder):
    def __init__(self, schedule: CycleSchedule, owners: Owners, cfg: Optional[CadenceConfig] = None):
        self.schedule, self.owners, self.cfg = schedule, owners, cfg
        self.prs = Presentation()
        self.prs.slide_width, self.prs.slide_height = Inches(13.333), Inches(7.5)
        self.blank = self.prs.slide_layouts[6]
        self.manifest: Dict[int, Dict] = {}

    def footer(self, s, n, dark=False):
        self.text(s, 0.6, 7.05, 10.5, 0.3,
                  f"Frontier Portfolio IQ · {self.schedule.cycle.label} cycle · dates from cadence/cadence.yaml",
                  size=9, color="AEB6C4" if dark else MUTED, name="Footer")
        self.text(s, 11.9, 7.05, 0.8, 0.3, str(n), size=9, color="AEB6C4" if dark else MUTED,
                  align=PP_ALIGN.RIGHT, name="SlideNumber")

    @staticmethod
    def d(x: dt.date, year: bool = False) -> str:
        return x.strftime("%a %b %-d, %Y" if year else "%a %b %-d")

    # ---------------------------------------------------------------- slides
    def s1(self):
        sch = self.schedule
        s = self.slide(dark=True)
        self.text(s, 0.6, 0.6, 12, 0.35, f"PORTFOLIO PRIORITIZATION · {sch.cycle.label.upper()}", size=12,
                  bold=True, color="FAB219")
        self.text(s, 0.6, 1.05, 11.8, 1.5, f"Your inputs shape the {sch.cycle} portfolio", size=40, bold=True,
                  font=HEAD, color="FFFFFF", name="Title")
        self.text(s, 0.6, 2.3, 11.8, 0.9, "Input collection kickoff: what we need from each LOB, when it is "
                  "due, and how the leadership team will use it to decide the portfolio.", size=16, color="D5DAE3")
        n_inputs = len(self.owners.lobs) * 4 + 1
        first_due = min(m.date for m in sch.milestones if m.package)
        stats = [(str(n_inputs), "inputs requested from LOB and enterprise owners"),
                 (first_due.strftime("%b %-d"), "first inputs due"),
                 (sch.decision_date.strftime("%b %-d"), "LT decision meeting"),
                 (sch.publish.strftime("%b %-d"), "committed portfolio published")]
        for i, (v, lab) in enumerate(stats):
            self.stat(s, 0.6 + i * 3.05, 4.1, 2.85, v, lab, dark=True, big_color="FAB219" if i == 2 else None)
        self.footer(s, 1, dark=True)
        self.manifest[1] = {"decision": sch.decision_date.isoformat(), "inputs": n_inputs}

    def s2(self):
        s = self.slide()
        self.title(s, 2, "Your inputs feed an optimizer that respects every LOB's budget and ring-fenced teams",
                   "How your inputs are used")
        steps = [("1 · Your inputs", "Budgets, teams, eligibility, transfer approvals and initiative proposals"),
                 ("2 · The rules", "Ring-fences, monthly budgets, deadlines and mandatory work are hard limits"),
                 ("3 · The search", "Finds the highest-value plan that obeys every rule"),
                 ("4 · Three portfolios", "Committed (executable), Conditional (needs approvals), "
                                         "Unconstrained (comparison only)")]
        for i, (h, b) in enumerate(steps):
            x = 0.6 + i * 3.1
            self.card(s, x, 2.0, 2.85, 2.6, fill="F4F5F7")
            self.text(s, x + 0.25, 2.2, 2.4, 0.5, h, size=16, bold=True, font=HEAD, color=INK)
            self.text(s, x + 0.25, 2.8, 2.4, 1.7, b, size=13, color=TEXT_2)
            if i < 3:
                arr = s.shapes.add_shape(MSO_SHAPE.RIGHT_ARROW, Inches(x + 2.88), Inches(3.15), Inches(0.2),
                                         Inches(0.3))
                arr.fill.solid()
                arr.fill.fore_color.rgb = _rgb("8E959E")
                arr.line.fill.background()
        self.card(s, 0.6, 5.0, 12.1, 1.6, fill="E2EAF7", line=ACCENT)
        self.text(s, 0.85, 5.2, 11.6, 1.3, [
            (("What this means for you", {"bold": True, "size": 15, "color": INK}),),
            "Late or incomplete inputs are the main reason an initiative is deferred: the solver can only fund "
            "what is in the workbook. Ring-fenced teams stay with their owners unless the owner approves a "
            "transfer, and every approval you are asked for comes with the value it would unlock."], size=13,
            color=TEXT_2)

    def s3_calendar(self):
        sch = self.schedule
        s = self.slide()
        self.title(s, 3, f"{(sch.decision_date - sch.kickoff).days // 7} weeks from kickoff to the LT decision on "
                         f"{sch.decision_date:%B %-d}", "Workback calendar")
        start = sch.kickoff - dt.timedelta(days=sch.kickoff.weekday())
        end = sch.publish + dt.timedelta(days=6 - sch.publish.weekday())
        weeks = (end - start).days // 7 + 1
        lab_w, x0, y0, chart_w = 4.3, 0.6, 1.95, 7.8
        cx0 = x0 + lab_w
        day_w = chart_w / ((end - start).days + 1)
        X = lambda d: cx0 + (d - start).days * day_w  # noqa: E731
        ms = sorted(sch.milestones, key=lambda m: (m.date, m.key))
        rh = min(0.33, 4.25 / len(ms))
        for w in range(weeks):
            wd = start + dt.timedelta(weeks=w)
            if w % 2 == 0:
                band = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(X(wd)), Inches(y0 + 0.3),
                                          Inches(7 * day_w), Inches(rh * len(ms) + 0.05))
                band.fill.solid()
                band.fill.fore_color.rgb = _rgb("F4F5F7")
                band.line.fill.background()
            self.text(s, X(wd), y0, 7 * day_w, 0.28, wd.strftime("%b %-d"), size=9, color=MUTED,
                      align=PP_ALIGN.CENTER)
        tdate = sch.date("templates")
        for r, m in enumerate(ms):
            y = y0 + 0.32 + r * rh
            bold = m.key == "decision"
            self.text(s, x0, y, lab_w - 0.15, rh, m.name, size=10.5, bold=bold or bool(m.package),
                      color=INK if (bold or m.package) else TEXT_2, anchor=MSO_ANCHOR.MIDDLE)
            if m.package:
                bar = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(X(tdate)), Inches(y + rh * 0.3),
                                         Inches(max(0.05, X(m.date) - X(tdate))), Inches(rh * 0.4))
                bar.fill.solid()
                bar.fill.fore_color.rgb = _rgb("F5D9A8")
                bar.line.fill.background()
                bar.name = f"Window {m.key}"
            size = 0.2
            dia = s.shapes.add_shape(MSO_SHAPE.DIAMOND, Inches(X(m.date) + day_w / 2 - size / 2),
                                     Inches(y + rh / 2 - size / 2), Inches(size), Inches(size))
            dia.fill.solid()
            dia.fill.fore_color.rgb = _rgb(ACCENT if bold else (AMBER if m.package else INK_SOFT))
            dia.line.fill.background()
            dia.name = f"Milestone {m.key}"
            self.text(s, X(m.date) + day_w / 2 + 0.15, y, 1.4, rh, m.date.strftime("%b %-d"), size=10,
                      bold=bold, color=INK, anchor=MSO_ANCHOR.MIDDLE)
        self.text(s, x0, 6.7, 12.1, 0.3, "Amber bars: the window to prepare each input (templates issued → due "
                  "date). Dates falling on weekends or company holidays move to the previous business day.",
                  size=10.5, color=TEXT_2)
        self.manifest[3] = {m.key: m.date.isoformat() for m in ms}

    def s4_inputs(self):
        sch = self.schedule
        s = self.slide()
        self.title(s, 4, "Five inputs, each with a named owner and a due date", "What we need, from whom, by when")
        rows = [["Due", "Input", "What it covers", "Owners"]]
        for pkg in sorted(PACKAGES, key=lambda p: (sch.due(p), p)):
            if pkg == "enterprise":
                who = self.owners.enterprise_owner.name
            else:
                who = "; ".join(l.owners[pkg].name for l in self.owners.lobs.values() if pkg in l.owners)
            rows.append([sch.due(pkg).strftime("%a %b %-d"), PACKAGES[pkg].title, PACKAGES[pkg].description, who])
        self.table(s, rows, 0.6, 1.95, 12.1, [1.3, 2.2, 5.0, 3.6], font=11, row_h=0.8)
        self.manifest[4] = {p: sch.due(p).isoformat() for p in PACKAGES}

    def s5_submit(self):
        sch = self.schedule
        s = self.slide()
        cyc = f"cycles/{sch.cycle}"
        self.title(s, 5, "Update your pre-filled template and return it; a check runs automatically",
                   "How to submit")
        steps = [("Get your template", f"Pre-filled with last cycle's data, one per owner: {cyc}/templates/. "
                                       "Your GitHub issue links it."),
                 ("Update it", "Keep sheet and column names. Only your LOB's rows. Money in $K; months as "
                               "numbers or names (April)."),
                 ("Return it", f"Save as {cyc}/inputs/<input>__<LOB>.xlsx, e.g. "
                               f"{package_filename('budget', 'LOB-A')}, via a pull request or Add file → Upload "
                               "files. No GitHub access? Email it to the portfolio lead."),
                 ("Automatic check", "The pull request is validated within minutes. Fix what it reports; your "
                                     "issue closes when a valid file is merged.")]
        for i, (h, b) in enumerate(steps):
            y = 1.95 + i * 1.2
            dot = s.shapes.add_shape(MSO_SHAPE.OVAL, Inches(0.6), Inches(y), Inches(0.6), Inches(0.6))
            dot.fill.solid()
            dot.fill.fore_color.rgb = _rgb(ACCENT)
            dot.line.fill.background()
            self.text(s, 0.6, y + 0.1, 0.6, 0.4, str(i + 1), size=18, bold=True, color="FFFFFF",
                      align=PP_ALIGN.CENTER)
            self.text(s, 1.45, y - 0.02, 11.2, 0.4, h, size=16, bold=True, font=HEAD, color=INK)
            self.text(s, 1.45, y + 0.38, 11.2, 0.7, b, size=13, color=TEXT_2)

    def s6_followup(self):
        sch = self.schedule
        s = self.slide()
        pol = self.cfg.reminders if self.cfg else None
        before = pol.before_due_days if pol else [7, 2, 0]
        esc = pol.escalate_after_business_days if pol else [2, 5]
        self.title(s, 6, "Reminders come to you first; escalation starts only after the due date",
                   "Follow-ups and escalation")
        steps = []
        for d in sorted(before, reverse=True):
            steps.append(("Due date" if d == 0 else f"{d} days before",
                          "Due-date reminder on your issue" if d == 0 else
                          ("Heads-up on your issue" if d == max(before) else "Reminder on your issue"), "F4F5F7"))
        labels = ["LOB executive sponsor copied", "Escalation contact and portfolio lead copied"]
        for i, n in enumerate(esc):
            steps.append((f"+{n} business days late", labels[min(i, 1)], "FBEFD5"))
        w = 12.1 / len(steps)
        for i, (h, b, fill) in enumerate(steps):
            x = 0.6 + i * w
            self.card(s, x + 0.05, 2.1, w - 0.15, 2.0, fill=fill)
            self.text(s, x + 0.25, 2.3, w - 0.5, 0.45, h, size=15, bold=True, font=HEAD, color=INK)
            self.text(s, x + 0.25, 2.85, w - 0.5, 1.2, b, size=12.5, color=TEXT_2)
        ex = sch.due("budget")
        self.text(s, 0.6, 4.5, 12.1, 2.0, [
            (("Example: budgets due " + ex.strftime("%a %b %-d"), {"bold": True, "size": 15, "color": INK}),),
            f"Heads-up {(ex - dt.timedelta(days=max(before))):%b %-d}, reminder "
            f"{(ex - dt.timedelta(days=sorted(before)[1] if len(before) > 1 else 0)):%b %-d}, due-date note "
            f"{ex:%b %-d}; if still missing, the sponsor is copied on "
            f"{add_business_days(ex, esc[0], self.cfg.holidays if self.cfg else set()):%b %-d}.",
            "Each message is posted once, on your GitHub issue (and by email if enabled). A valid file stops "
            "all reminders immediately."], size=13, color=TEXT_2)

    def s7_after(self):
        sch = self.schedule
        s = self.slide()
        self.title(s, 7, f"From inputs to a committed portfolio by {sch.publish:%B %-d}",
                   "After the inputs are in")
        keys = ["validation", "approvals", "solver_run", "preread", "decision", "publish"]
        blurb = {"validation": "Portfolio office merges and cross-checks every input",
                 "approvals": "Owning LOBs approve or decline pending transfer and release requests",
                 "solver_run": "Optimizer runs three portfolios and twelve what-if scenarios",
                 "preread": "Executive deck, dashboard and results go to LT",
                 "decision": "LT approves the committed portfolio and decides conditional items",
                 "publish": "Sponsors receive approved, conditional, deferred and rejected decisions"}
        short = {"validation": "Validation complete", "approvals": "Transfer approvals",
                 "solver_run": "Optimization run", "preread": "LT pre-read", "decision": "LT decision",
                 "publish": "Portfolio published"}
        w = 12.1 / len(keys)
        for i, k in enumerate(keys):
            m = sch.get(k)
            x = 0.6 + i * w
            accent = k == "decision"
            self.card(s, x + 0.05, 2.1, w - 0.15, 3.0, fill="E2EAF7" if accent else "F4F5F7",
                      line=ACCENT if accent else None)
            self.text(s, x + 0.22, 2.3, w - 0.45, 0.4, m.date.strftime("%a %b %-d"), size=14, bold=True,
                      color=ACCENT if accent else INK)
            self.text(s, x + 0.22, 2.75, w - 0.45, 0.6, short[k], size=13, bold=True, font=HEAD, color=INK)
            self.text(s, x + 0.22, 3.35, w - 0.45, 1.6, blurb[k], size=11.5, color=TEXT_2)

    def s8_owners(self):
        s = self.slide()
        self.title(s, 8, "Owners for this cycle", "Contacts")
        rows = [["LOB", "Strategy & outcomes", "Budget", "Capacity", "Initiatives", "Executive sponsor"]]
        for l in self.owners.lobs.values():
            rows.append([l.name] + [l.owners[p].name if p in l.owners else "—"
                                    for p in ("strategy", "budget", "capacity", "initiatives")]
                        + [l.executive_sponsor.name])
        self.table(s, rows, 0.6, 1.95, 12.1, [2.4, 2.0, 1.9, 2.0, 1.9, 1.9], font=11, row_h=0.6)
        y = 1.95 + 0.6 * len(rows) + 0.3
        self.text(s, 0.6, y, 12.1, 0.8, [
            f"Portfolio lead: {self.owners.portfolio_lead.name} · Escalation: {self.owners.escalation.name} · "
            f"Enterprise settings: {self.owners.enterprise_owner.name}",
            "Calendar invites for every milestone: calendar.ics in this cycle's folder."], size=12, color=TEXT_2)

    def build(self, path: str):
        for fn in (self.s1, self.s2, self.s3_calendar, self.s4_inputs, self.s5_submit, self.s6_followup,
                   self.s7_after, self.s8_owners):
            fn()
        self.prs.core_properties.title = f"Portfolio {self.schedule.cycle} — input collection"
        self.prs.save(path)
        return self.manifest


def build_stakeholder_deck(schedule: CycleSchedule, owners: Owners, path: str,
                           cfg: Optional[CadenceConfig] = None) -> Dict[int, Dict]:
    return StakeholderDeck(schedule, owners, cfg).build(path)
