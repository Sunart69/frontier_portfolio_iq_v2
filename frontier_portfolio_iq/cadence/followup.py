"""Who owes what, by when, and the follow-ups that make sure it arrives.

Each expected submission (package × LOB) gets one GitHub issue at kickoff. A daily run evaluates the
submitted files and posts at most one new follow-up per issue per stage, so re-running is always safe:
each message carries a hidden marker and a stage is never posted twice.
"""
from __future__ import annotations

import datetime as dt
import os
import smtplib
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Dict, List, Optional, Tuple

from .config import CadenceConfig, Owners, Person
from .packages import ENTERPRISE, PACKAGES, PackageCheck, check_package, package_filename
from .schedule import CycleSchedule, business_days_between

STATUS_ICON = {"valid": "✅ received", "invalid": "⚠️ needs fixes", "missing": "⏳ not received"}


@dataclass
class Submission:
    package: str
    lob: str                 # LOB id, or ALL for the enterprise package
    lob_name: str
    owner: Person
    sponsor: Person
    due: dt.date
    path: str
    state: str = "missing"   # missing | invalid | valid
    errors: List[str] = field(default_factory=list)

    @property
    def key(self) -> Tuple[str, str]:
        return (self.package, self.lob)

    @property
    def marker(self) -> str:
        return f"fpiq:submission:{self.package}:{self.lob}"

    @property
    def title_suffix(self) -> str:
        return f"{PACKAGES[self.package].title} — {self.lob_name}"


def cycle_dir(root: str, cfg: CadenceConfig, schedule: CycleSchedule) -> str:
    return os.path.join(root, cfg.cycles_dir, str(schedule.cycle))


def expected_submissions(root: str, cfg: CadenceConfig, owners: Owners,
                         schedule: CycleSchedule) -> List[Submission]:
    inputs = os.path.join(cycle_dir(root, cfg, schedule), "inputs")
    subs = []
    for lob_id, lob in owners.lobs.items():
        for pkg in ("strategy", "budget", "capacity", "initiatives"):
            subs.append(Submission(pkg, lob_id, lob.name, owners.owner_for(pkg, lob_id), lob.executive_sponsor,
                                   schedule.due(pkg), os.path.join(inputs, package_filename(pkg, lob_id))))
    subs.append(Submission("enterprise", ENTERPRISE, "Enterprise", owners.owner_for("enterprise", None),
                           owners.escalation, schedule.due("enterprise"),
                           os.path.join(inputs, package_filename("enterprise", None))))
    return subs


def evaluate(subs: List[Submission]) -> List[Submission]:
    for s in subs:
        if not os.path.exists(s.path):
            s.state, s.errors = "missing", []
            continue
        chk: PackageCheck = check_package(s.path, s.package, None if s.lob == ENTERPRISE else s.lob)
        s.state, s.errors = ("valid", []) if chk.ok else ("invalid", chk.errors)
    return subs


def stage(sub: Submission, today: dt.date, cfg: CadenceConfig) -> Optional[str]:
    """The most advanced follow-up stage reached on `today`, or None."""
    if sub.state == "valid":
        return "received"
    days_left = (sub.due - today).days
    if days_left < 0:
        late = business_days_between(sub.due, today, cfg.holidays)
        reached = [n for n in cfg.reminders.escalate_after_business_days if late >= n]
        if reached:
            level = cfg.reminders.escalate_after_business_days.index(max(reached)) + 1
            return f"escalate-{level}"
        return "overdue"
    upcoming = [d for d in cfg.reminders.before_due_days if days_left <= d]
    if not upcoming:
        return None
    d = min(upcoming)
    return "due-today" if d == 0 else f"due-in-{d}"


def _fmt(d: dt.date) -> str:
    return d.strftime("%a %b %-d, %Y")


def message(sub: Submission, st: str, schedule: CycleSchedule, owners: Owners, repo_hint: str) -> str:
    who = sub.owner.mention
    what = f"**{PACKAGES[sub.package].title}** for **{sub.lob_name}**"
    where = f"`{os.path.relpath(sub.path, repo_hint) if repo_hint else sub.path}`"
    fix = ""
    if sub.state == "invalid":
        fix = "\n\nThe file we have needs fixes:\n" + "\n".join(f"- {e}" for e in sub.errors[:10])
    lines = {
        "due-in-7": f"{who} — friendly heads-up: {what} is due **{_fmt(sub.due)}** (one week).",
        "due-in-2": f"{who} — reminder: {what} is due **{_fmt(sub.due)}**, in two days.",
        "due-today": f"{who} — {what} is due **today** ({_fmt(sub.due)}).",
        "overdue": f"{who} — {what} was due {_fmt(sub.due)} and has not been received yet.",
        "escalate-1": f"{who} {sub.sponsor.mention} — {what} is now overdue (due {_fmt(sub.due)}). "
                      f"Escalating to the LOB executive sponsor so the cycle stays on schedule.",
        "escalate-2": f"{who} {sub.sponsor.mention} {owners.escalation.mention} "
                      f"{owners.portfolio_lead.mention} — {what} is still missing (due {_fmt(sub.due)}). "
                      f"The optimization run on {_fmt(schedule.date('solver_run'))} is at risk.",
        "received": f"Thanks {who} — {what} is received and passed validation. Closing this issue.",
    }
    body = lines[st]
    if st != "received":
        body += f"\n\nPlease add the completed workbook as {where} (a pull request or upload). " \
                f"The template is in `templates/` of the same cycle folder." + fix
    return f"<!-- {sub.marker}:{st} -->\n{body}"


def issue_body(sub: Submission, schedule: CycleSchedule, repo_rel_path: str, template_rel: str) -> str:
    spec = PACKAGES[sub.package]
    return (f"<!-- {sub.marker} -->\n"
            f"**Owner:** {sub.owner.mention}  \n**LOB:** {sub.lob_name}  \n"
            f"**Due:** {_fmt(sub.due)}  \n**Cycle:** {schedule.cycle.label}, "
            f"LT decision {_fmt(schedule.decision_date)}\n\n"
            f"### What we need\n{spec.description}.\n\nSheets: " + ", ".join(f"`{s}`" for s in spec.sheets) +
            f"\n\n### How to submit\n1. Download the pre-filled template `{template_rel}`.\n"
            f"2. Update it for the coming half (keep the sheet and column names).\n"
            f"3. Add it as `{repo_rel_path}`: open a pull request, or use **Add file → Upload files**.\n"
            f"4. The pull request is checked automatically; fix anything the check reports.\n\n"
            f"This issue closes automatically when a valid file is merged. Reminders are posted here "
            f"7 and 2 days before the due date, on the due date, and escalated after 2 and 5 business days.")


def _email(person: Person, subject: str, body: str) -> bool:
    host = os.environ.get("FPIQ_SMTP_HOST")
    if not (host and person.email):
        return False
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, os.environ.get("FPIQ_SMTP_FROM", "portfolio@localhost"), person.email
    msg.set_content(body)
    with smtplib.SMTP(host, int(os.environ.get("FPIQ_SMTP_PORT", "587"))) as s:
        if os.environ.get("FPIQ_SMTP_USER"):
            s.starttls()
            s.login(os.environ["FPIQ_SMTP_USER"], os.environ.get("FPIQ_SMTP_PASSWORD", ""))
        s.send_message(msg)
    return True


def labels_for(schedule: CycleSchedule) -> Tuple[str, str]:
    return f"fpiq:{schedule.cycle}", "fpiq:input"


def find_issue(tracker, label: str, marker: str) -> Optional[Dict]:
    for i in tracker.issues(label):
        if f"<!-- {marker} -->" in (i.get("body") or ""):
            return i
    return None


def open_input_issues(tracker, subs: List[Submission], schedule: CycleSchedule, root: str,
                      cfg: CadenceConfig) -> List[int]:
    cyc_label, kind_label = labels_for(schedule)
    tracker.ensure_label(cyc_label, "1F3A5F", f"Portfolio prioritization cycle {schedule.cycle}")
    tracker.ensure_label(kind_label, "D9900F", "Input requested from an owner")
    ms = tracker.ensure_milestone(f"Portfolio {schedule.cycle} decision", schedule.decision_date.isoformat())
    created = []
    for s in subs:
        if find_issue(tracker, cyc_label, s.marker):
            continue
        rel = os.path.relpath(s.path, root)
        tmpl = os.path.join(os.path.dirname(os.path.dirname(rel)), "templates", os.path.basename(rel))
        issue = tracker.create_issue(f"[Portfolio {schedule.cycle}] {s.title_suffix} — due {s.due:%b %-d}",
                                     issue_body(s, schedule, rel, tmpl), [cyc_label, kind_label],
                                     [s.owner.github] if s.owner.github else [], ms)
        created.append(issue["number"])
    return created


def follow_up(tracker, subs: List[Submission], schedule: CycleSchedule, owners: Owners, cfg: CadenceConfig,
              today: dt.date, root: str) -> List[Dict]:
    cyc_label, _ = labels_for(schedule)
    actions = []
    for s in subs:
        issue = find_issue(tracker, cyc_label, s.marker)
        if issue is None:
            continue
        st = stage(s, today, cfg)
        if st is None:
            continue
        if st == "received" and issue.get("state") == "closed":
            continue
        marker = f"<!-- {s.marker}:{st} -->"
        already = any(marker in (c.get("body") or "") for c in tracker.comments(issue["number"]))
        if not already:
            body = message(s, st, schedule, owners, root)
            tracker.comment(issue["number"], body)
            emailed = False
            if cfg.reminders.email and st != "received":
                emailed = _email(s.owner, f"[Portfolio {schedule.cycle}] {s.title_suffix}", body)
            actions.append({"issue": issue["number"], "package": s.package, "lob": s.lob, "stage": st,
                            "emailed": emailed})
        if st == "received" and issue.get("state") != "closed":
            tracker.update_issue(issue["number"], state="closed")
        elif st != "received" and issue.get("state") == "closed":
            tracker.update_issue(issue["number"], state="open")   # a valid file was later removed or broken
    return actions


def status_markdown(subs: List[Submission], schedule: CycleSchedule, today: dt.date, cfg: CadenceConfig,
                    extra: str = "") -> str:
    done = sum(1 for s in subs if s.state == "valid")
    lines = [f"<!-- fpiq:status:{schedule.cycle} -->",
             f"## Portfolio {schedule.cycle} — input status ({done}/{len(subs)} received)",
             f"As of **{_fmt(today)}** · phase: **{schedule.phase(today)}** · optimization run "
             f"{_fmt(schedule.date('solver_run'))} · LT decision {_fmt(schedule.decision_date)}", "",
             "| Input | LOB | Owner | Due | Status |", "| --- | --- | --- | --- | --- |"]
    for s in sorted(subs, key=lambda s: (s.due, s.package, s.lob)):
        st = STATUS_ICON[s.state]
        if s.state != "valid" and today > s.due:
            st += f" — {business_days_between(s.due, today, cfg.holidays)} business days late"
        lines.append(f"| {PACKAGES[s.package].title} | {s.lob_name} | {s.owner.mention} | {s.due:%b %-d} | {st} |")
    lines += ["", "### Schedule", "| Date | Milestone |", "| --- | --- |"]
    for m in sorted(schedule.milestones, key=lambda m: m.date):
        mark = "✔︎ " if m.date < today else ""
        lines.append(f"| {m.date:%a %b %-d} | {mark}{m.name} |")
    if extra:
        lines += ["", extra]
    return "\n".join(lines)
