"""The semester cycle, end to end: plan → kickoff → follow up → merge → optimize → LT decision → publish.

`tick()` is what the daily GitHub Actions run (and the Copilot agent) calls. It is idempotent: it
reads the cycle's state file and the issue tracker, does only what is due today and not yet done,
and returns a summary of what it did (the workflow commits files and opens the pre-read pull request).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .calendar_ics import to_ics
from .decision import DecisionError, decision_path, finalize, load_decision, publish, write_decision_template
from .config import CadenceConfig, Owners
from .followup import (Submission, cycle_dir, evaluate, expected_submissions, find_issue, follow_up,
                       labels_for, open_input_issues, status_markdown)
from .packages import ENTERPRISE, PACKAGES, merge_packages, package_filename, write_template
from .schedule import CycleSchedule, active_cycle, build_schedule


@dataclass
class TickResult:
    cycle: str
    phase: str
    actions: List[str] = field(default_factory=list)
    followups: List[Dict] = field(default_factory=list)
    files_changed: bool = False
    ran_prioritization: bool = False
    blocked: List[str] = field(default_factory=list)

    def as_dict(self):
        return self.__dict__


def _state_path(root, cfg, schedule):
    return os.path.join(cycle_dir(root, cfg, schedule), "state.json")


def load_state(root, cfg, schedule) -> Dict:
    p = _state_path(root, cfg, schedule)
    if os.path.exists(p):
        with open(p) as fh:
            return json.load(fh)
    return {}


def save_state(root, cfg, schedule, state: Dict):
    p = _state_path(root, cfg, schedule)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as fh:
        json.dump(state, fh, indent=2, sort_keys=True, default=str)


def previous_master(root: str, cfg: CadenceConfig, schedule: CycleSchedule) -> str:
    """Latest earlier cycle's merged intake, else the baseline workbook (pre-fills templates)."""
    base = os.path.join(root, cfg.cycles_dir)
    if os.path.isdir(base):
        for name in sorted(os.listdir(base), reverse=True):
            p = os.path.join(base, name, "merged_intake.xlsx")
            if name < str(schedule.cycle) and os.path.exists(p):
                return p
    return os.path.join(root, cfg.baseline_intake)


def plan(root: str, cfg: CadenceConfig, owners: Owners, cycle: str, write_deck: bool = True) -> Dict[str, str]:
    """Write the cycle folder: schedule, calendar, pre-filled templates, stakeholder deck."""
    schedule = build_schedule(cycle, cfg)
    cdir = cycle_dir(root, cfg, schedule)
    tdir = os.path.join(cdir, "templates")
    os.makedirs(os.path.join(cdir, "inputs"), exist_ok=True)
    keep = os.path.join(cdir, "inputs", ".gitkeep")
    if not os.path.exists(keep):
        open(keep, "w").close()
    src = previous_master(root, cfg, schedule)
    written = {}
    for s in expected_submissions(root, cfg, owners, schedule):
        readme = [("Cycle", schedule.cycle.label), ("Input", PACKAGES[s.package].title),
                  ("LOB", s.lob_name), ("Owner", s.owner.name), ("Due", f"{s.due:%A %B %-d, %Y}"),
                  ("What we need", PACKAGES[s.package].description + "."),
                  ("How to return it", f"Save as {cfg.cycles_dir}/{schedule.cycle}/inputs/"
                                       f"{package_filename(s.package, None if s.lob == ENTERPRISE else s.lob)} "
                                       f"and open a pull request (or use Add file → Upload files)."),
                  ("Pre-filled from", os.path.relpath(src, root)),
                  ("Rules", "Keep sheet and column names. Only your LOB's rows. Money in $K. Months are "
                            "1-based within the planning horizon, or names like April.")]
        written[f"{s.package}:{s.lob}"] = write_template(src, tdir, s.package,
                                                          None if s.lob == ENTERPRISE else s.lob, readme)
    with open(os.path.join(cdir, "calendar.ics"), "w", newline="") as fh:
        fh.write(to_ics(schedule, owners))
    written["calendar"] = os.path.join(cdir, "calendar.ics")
    with open(os.path.join(cdir, "SCHEDULE.md"), "w") as fh:
        fh.write(schedule_markdown(schedule, owners))
    written["schedule"] = os.path.join(cdir, "SCHEDULE.md")
    if write_deck:
        from .stakeholder_deck import build_stakeholder_deck
        p = os.path.join(cdir, f"stakeholder_input_deck_{schedule.cycle}.pptx")
        build_stakeholder_deck(schedule, owners, p, cfg)
        written["deck"] = p
    return written


def schedule_markdown(schedule: CycleSchedule, owners: Owners) -> str:
    rows = [f"# Portfolio prioritization {schedule.cycle.label}", "",
            f"LT decision meeting: **{schedule.decision_date:%A %B %-d, %Y}**", "",
            "| Date | Milestone | Owner |", "| --- | --- | --- |"]
    for m in sorted(schedule.milestones, key=lambda m: (m.date, m.key)):
        if m.package == "enterprise":
            who = owners.enterprise_owner.name
        elif m.package:
            who = "; ".join(f"{l.name}: {l.owners[m.package].name}" for l in owners.lobs.values()
                            if m.package in l.owners)
        else:
            who = owners.role(m.owner_role).name if m.owner_role else ""
        rows.append(f"| {m.date:%a %b %-d, %Y} | {m.name} | {who} |")
    return "\n".join(rows) + "\n"


def collect(root, cfg, owners, schedule) -> List[Submission]:
    return evaluate(expected_submissions(root, cfg, owners, schedule))


def run_cycle(root: str, cfg: CadenceConfig, owners: Owners, schedule: CycleSchedule,
              force: bool = False, release_scan: bool = True) -> Dict:
    """Merge inputs and run the full analysis into cycles/<cycle>/outputs."""
    subs = collect(root, cfg, owners, schedule)
    missing = [s for s in subs if s.state != "valid"]
    if missing and cfg.require_all_inputs and not cfg.carry_forward_missing and not force:
        return {"ran": False, "reason": "inputs incomplete",
                "missing": [f"{s.package}:{s.lob} ({s.state})" for s in missing]}
    cdir = cycle_dir(root, cfg, schedule)
    submissions = {s.key: s.path for s in subs if s.state == "valid"}
    fallback = previous_master(root, cfg, schedule) if (cfg.carry_forward_missing or force) else None
    merged = merge_packages(submissions, os.path.join(cdir, "merged_intake.xlsx"), fallback,
                            [s.key for s in subs])
    if not merged.ok:
        return {"ran": False, "reason": "merged intake failed validation", "errors": merged.errors[:30]}
    from ..analysis import run_analysis, write_results_workbook
    from ..dashboard import render_dashboard
    from ..deck import build_deck
    from ..intake import read_workbook
    from ..cli import _summary
    out = os.path.join(cdir, "outputs")
    os.makedirs(out, exist_ok=True)
    b = run_analysis(read_workbook(merged.master_path), release_scan=release_scan)
    write_results_workbook(b, os.path.join(out, "portfolio_results.xlsx"))
    render_dashboard(b, os.path.join(out, "lob_dashboard.html"))
    manifest = build_deck(b, os.path.join(out, f"executive_deck_{schedule.cycle}.pptx"))
    with open(os.path.join(out, "deck_figures_manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=2, default=str)
    summary = _summary(b)
    summary["carried_forward"] = [f"{p}:{l}" for p, l in merged.carried_forward]
    with open(os.path.join(out, "analysis_summary.json"), "w") as fh:
        json.dump(summary, fh, indent=2, default=str)
    cm = b.tier_metrics["committed"]
    recommended = dict(b.tiers["committed"].starts)
    recommended_value = cm["net_value"]
    pending = [r for r in b.tiers["conditional"].approvals_required if r != "RELAXED-RING-FENCING"]
    alternatives: Dict[str, Dict] = {}
    v2 = {}
    if cfg.decision_enabled:
        v2 = run_decision_outputs(root, cfg, read_workbook(merged.master_path), merged.master_path, out,
                                  schedule, analysis=b)
        alternatives = v2.get("alternatives", {})
        if v2.get("recommended_starts"):
            # the v2 risk-adjusted recommendation is what the pre-read recommends
            recommended = v2["recommended_starts"]
            recommended_value = v2.get("recommended_net_value", recommended_value)
    write_decision_template(decision_path(cdir), schedule, sorted(recommended), pending,
                            sorted(set(b.data.projects) - set(recommended)), alternatives)
    return {"ran": True, "outputs": out, "carried_forward": summary["carried_forward"],
            "committed_net_value": recommended_value, "committed_projects": len(recommended),
            "conditional_gain": b.tier_metrics["conditional"]["net_value"] - cm["net_value"],
            "recommended_starts": recommended, "alternatives": alternatives,
            "decision_intelligence": {k: v for k, v in v2.items() if k not in ("alternatives", "recommended_starts")}}


def run_decision_outputs(root: str, cfg: CadenceConfig, data, intake_path: str, out: str, schedule,
                         analysis=None) -> Dict:
    """v2 pre-read: validated multi-solver optimization, Pareto frontier, Monte Carlo, recommendation,
    decision dashboard and CEO deck, written next to the v1 outputs."""
    import dataclasses
    from ..dashboard_v2 import build_decision_dashboard
    from ..deck_v2 import build_ceo_deck
    from ..decision.config import load_decision_config, write_decision_workbook
    from ..intelligence import run_decision_intelligence
    from ..metrics import portfolio_metrics
    from ..risk import read_uncertainty
    cpath = os.path.join(root, cfg.decision_config) if cfg.decision_config else None
    dcfg, sim = load_decision_config(cpath if cpath and os.path.exists(cpath) else None)
    dcfg.iterations = cfg.decision_iterations
    if cfg.decision_quick:
        dcfg.pareto = dataclasses.replace(dcfg.pareto, grid_points=2, refine_rounds=0, weighted_step=0, max_points=8)
        dcfg.iterations = min(dcfg.iterations, 1000)
        dcfg.run_whatifs = False
        dcfg.stochastic = None
        dcfg.validators = ("SCIP",)
    um = read_uncertainty(intake_path, data)
    sim = {k: v for k, v in dict(sim).items() if k != "iterations"}
    if sim:
        um = um.with_settings(**sim)
    bundle = run_decision_intelligence(data, um, dcfg, scenario=str(schedule.cycle), analysis=analysis)
    build_decision_dashboard(bundle, os.path.join(out, "decision_dashboard.html"))
    manifest = build_ceo_deck(bundle, os.path.join(out, f"ceo_decision_deck_{schedule.cycle}.pptx"))
    with open(os.path.join(out, "ceo_deck_manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=2, default=str)
    bundle.write_json(os.path.join(out, "decision_intelligence.json"))
    write_decision_workbook(bundle, os.path.join(out, "decision_results.xlsx"))
    with open(os.path.join(out, "solver_confidence.md"), "w") as fh:
        fh.write(bundle.confidence.markdown())
    rec = bundle.recommended
    alternatives = {}
    for key, c in bundle.choices.items():
        a = bundle.alt(c["alt_id"])
        alternatives[key] = {"alt_id": a.alt_id, "name": a.name, "starts": dict(a.starts),
                             "expected_npv": bundle.risk.get(a.alt_id, {}).get("npv", {}).get("mean")}
    for aid in bundle.risk:
        a = bundle.alt(aid)
        alternatives.setdefault(aid, {"alt_id": aid, "name": a.name, "starts": dict(a.starts),
                                      "expected_npv": bundle.risk[aid]["npv"]["mean"]})
    r = bundle.risk.get(rec.alt_id) if rec else None
    return {"recommended_alt": rec.alt_id if rec else None, "recommended_name": rec.name if rec else None,
            "recommended_starts": dict(rec.starts) if rec else None,
            "recommended_net_value": portfolio_metrics(rec.result)["net_value"] if rec and rec.result is not None else None,
            "expected_npv": r["npv"]["mean"] if r else None,
            "p_within_budget": r["probabilities"]["within_budget"]["p"] if r else None,
            "solver_confidence": bundle.confidence.level, "model_version": bundle.version.label(),
            "alternatives": alternatives}


def publish_cycle(root: str, cfg: CadenceConfig, schedule: CycleSchedule,
                  state: Optional[Dict] = None) -> Dict:
    """Apply the LT decision record, re-solve, and write the approved-portfolio memo and register."""
    state = load_state(root, cfg, schedule) if state is None else state
    cdir = cycle_dir(root, cfg, schedule)
    path = decision_path(cdir)
    rel = os.path.relpath(path, root)
    if not state.get("ran"):
        return {"published": False, "reason": "the optimizer has not run for this cycle yet"}
    if not os.path.exists(path):
        return {"published": False, "reason": "no decision recorded", "record": rel}
    try:
        dec = load_decision(path)
    except (DecisionError, Exception) as e:  # yaml errors included
        return {"published": False, "reason": "decision record has errors", "errors": [str(e)], "record": rel}
    if not dec.recorded:
        return {"published": False, "reason": "no decision recorded", "record": rel}
    from ..intake import read_workbook
    merged = os.path.join(cdir, "merged_intake.xlsx")
    if not os.path.exists(merged):
        return {"published": False, "reason": f"{os.path.relpath(merged, root)} is missing"}
    rs = state.get("run_summary") or {}
    rec = rs.get("recommended_starts")
    recommended = {k: int(v) for k, v in rec.items()} if rec else None
    alts = {k: {**v, "starts": {i: int(m) for i, m in v.get("starts", {}).items()}}
            for k, v in (rs.get("alternatives") or {}).items()}
    data = read_workbook(merged)
    preserve = bool(rs.get("decision_intelligence"))      # v2 pre-read: keep the risk-adjusted recommendation
    fin = finalize(data, dec, schedule, recommended, alts, preserve=preserve)
    if not fin.ok:
        return {"published": False, "reason": fin.reason, "errors": fin.errors, "record": rel}
    risk = None
    if cfg.decision_enabled:
        try:
            from ..risk import evaluate_plan, read_uncertainty
            um = read_uncertainty(merged, data).with_settings(iterations=cfg.publish_iterations)
            risk = evaluate_plan(fin.result.data, fin.result.starts, um, "fixed", result=fin.result,
                                 keep_projects=False)[0]
        except Exception as e:  # the memo is still published; the risk section says why it is missing
            risk = {"error": f"{type(e).__name__}: {e}"}
    files = publish(fin, dec, schedule, os.path.join(cdir, "published"), rs.get("committed_net_value"), risk)
    m = fin.metrics
    return {"published": True, "files": {k: os.path.relpath(v, root) for k, v in files.items()},
            "n_selected": m["n_selected"], "investment": m["investment"], "net_value": m["net_value"],
            "changes": len(fin.changes), "decided_on": dec.decided_on.isoformat()}


def _once(tracker, issue, marker: str, body: str) -> bool:
    """Post a comment on the issue unless one with this marker already exists."""
    if issue is None:
        return False
    tag = f"<!-- {marker} -->"
    if any(tag in (c.get("body") or "") for c in tracker.comments(issue["number"])):
        return False
    tracker.comment(issue["number"], f"{body}\n\n{tag}")
    return True


def _publish_step(root, cfg, owners, tracker, schedule, state, today, res) -> str:
    """Publish once LT has decided; nudge the portfolio lead if the record is late. Returns status text."""
    if not state.get("ran") or state.get("published") or today < schedule.decision_date:
        return ""
    pub = publish_cycle(root, cfg, schedule, state)
    if pub["published"]:
        state["published"] = today.isoformat()
        state["publish_summary"] = {k: v for k, v in pub.items() if k != "files"}
        res.files_changed = True
        res.actions.append(f"Published the approved portfolio for {schedule.cycle}: {pub['files']['memo_md']}")
        nxt = build_schedule(schedule.cycle.next(), cfg).kickoff
        res.actions.append(f"Cycle closed; next cycle starts {nxt:%b %-d, %Y}")
        return (f"**Approved portfolio published** ({today:%b %-d}): {pub['n_selected']} initiatives, "
                f"${pub['investment'] / 1000:,.1f}M investment, ${pub['net_value'] / 1000:,.1f}M net value, "
                f"{pub['changes']} change(s) from the pre-read. Memo: `{pub['files']['memo_md']}` "
                f"(Word copy alongside).")
    res.blocked = pub.get("errors") or []
    if pub["reason"] == "no decision recorded":
        if today < schedule.publish:
            return (f"Waiting for the LT decision record: fill in `{pub['record']}` (set `decided_on`) and merge "
                    f"it; the approved-portfolio memo is published automatically.")
        res.actions.append("Publish overdue: LT decision record not filled in")
        return (f"**Publish overdue** (due {schedule.publish:%b %-d}): fill in `{pub['record']}` with what LT "
                f"decided and set `decided_on`.")
    res.actions.append(f"Publish blocked: {pub['reason']}")
    return f"**Publish blocked**: {pub['reason']}. " + "; ".join(res.blocked[:10])


def _status_issue(tracker, schedule, body):
    cyc_label, _ = labels_for(schedule)
    tracker.ensure_label(cyc_label, "1F3A5F", f"Portfolio prioritization cycle {schedule.cycle}")
    tracker.ensure_label("fpiq:status", "2459B8", "Cycle status tracker")
    marker = f"fpiq:status:{schedule.cycle}"
    issue = find_issue(tracker, cyc_label, marker)
    if issue is None:
        return tracker.create_issue(f"[Portfolio {schedule.cycle}] Cycle status and schedule", body,
                                    [cyc_label, "fpiq:status"], [])
    tracker.update_issue(issue["number"], body=body)
    return issue


def tick(root: str, cfg: CadenceConfig, owners: Owners, tracker, today: dt.date,
         cycle: Optional[str] = None, force_run: bool = False, release_scan: bool = True) -> TickResult:
    schedule = build_schedule(cycle, cfg) if cycle else active_cycle(today, cfg)
    res = TickResult(str(schedule.cycle), schedule.phase(today))
    if not cycle:
        # A decision recorded after the publish date still gets published (the daily run has moved on).
        prev = build_schedule(schedule.cycle.prev(), cfg)
        ps = load_state(root, cfg, prev)
        if ps.get("ran") and not ps.get("published"):
            late = tick(root, cfg, owners, tracker, today, str(prev.cycle), release_scan=release_scan)
            res.actions += [f"{prev.cycle}: {a}" for a in late.actions]
            res.files_changed |= late.files_changed
    if today < schedule.kickoff and not force_run:
        res.actions.append(f"Waiting: {schedule.cycle} kicks off {schedule.kickoff:%a %b %-d, %Y}")
        return res
    state = load_state(root, cfg, schedule)

    if not state.get("planned"):
        plan(root, cfg, owners, str(schedule.cycle))
        state["planned"] = today.isoformat()
        res.files_changed = True
        res.actions.append("Planned cycle: templates, calendar, schedule and stakeholder deck written")

    subs = collect(root, cfg, owners, schedule)
    if not state.get("kicked_off"):
        created = open_input_issues(tracker, subs, schedule, root, cfg)
        state["kicked_off"] = today.isoformat()
        res.files_changed = True
        res.actions.append(f"Kickoff: opened {len(created)} input issues")

    res.followups = follow_up(tracker, subs, schedule, owners, cfg, today, root)
    if res.followups:
        res.actions.append(f"Posted {len(res.followups)} follow-ups")

    extra = ""
    if today >= schedule.date("solver_run") and not state.get("ran"):
        outcome = run_cycle(root, cfg, owners, schedule, force=force_run, release_scan=release_scan)
        if outcome["ran"]:
            state["ran"] = today.isoformat()
            state["run_summary"] = {k: v for k, v in outcome.items() if k != "outputs"}
            res.ran_prioritization = res.files_changed = True
            res.actions.append("Optimization run complete: outputs written for the LT pre-read")
            extra = (f"**Optimization run complete** ({today:%b %-d}). Committed portfolio: "
                     f"{outcome['committed_projects']} initiatives, net value "
                     f"${outcome['committed_net_value'] / 1000:,.1f}M; pending approvals would add "
                     f"${outcome['conditional_gain'] / 1000:,.1f}M. See the pre-read pull request.")
        else:
            res.blocked = outcome.get("missing") or outcome.get("errors") or [outcome["reason"]]
            res.actions.append(f"Optimization blocked: {outcome['reason']}")
            extra = (f"**Optimization run blocked** ({today:%b %-d}): {outcome['reason']}. "
                     + "; ".join(res.blocked[:12]))
    elif state.get("ran"):
        rs = state.get("run_summary", {})
        extra = f"Optimization ran {state['ran']}." + (
            f" Carried forward: {', '.join(rs.get('carried_forward') or [])}." if rs.get("carried_forward") else "")

    pub_text = _publish_step(root, cfg, owners, tracker, schedule, state, today, res)
    if pub_text:
        extra = (extra + "\n\n" + pub_text) if extra else pub_text

    status = status_markdown(subs, schedule, today, cfg, extra)
    issue = _status_issue(tracker, schedule, status)
    lead = owners.portfolio_lead.mention
    if state.get("published"):
        ps = state.get("publish_summary") or {}
        _once(tracker, issue, f"fpiq:published:{schedule.cycle}",
              f"The approved portfolio for {schedule.cycle} is published in `{cfg.cycles_dir}/{schedule.cycle}/"
              f"published/` ({ps.get('n_selected', '?')} initiatives). {lead} please share the memo with the "
              f"LOB sponsors and input owners.")
    elif "Publish overdue" in pub_text:
        _once(tracker, issue, f"fpiq:decision-overdue:{schedule.cycle}",
              f"{lead} the approved-portfolio memo was due {schedule.publish:%b %-d}. Please record LT's "
              f"decisions in `{cfg.cycles_dir}/{schedule.cycle}/decision/lt_decision.yaml` and set `decided_on`.")
    elif "Publish blocked" in pub_text:
        _once(tracker, issue, f"fpiq:publish-blocked:{schedule.cycle}:{hashlib.sha1(pub_text.encode()).hexdigest()[:10]}",
              f"{lead} the LT decision record could not be applied:\n\n{pub_text}")
    # The repo copy of the status changes only when a submission's state does (no daily commit noise).
    sig = ",".join(f"{s.package}:{s.lob}:{s.state}" for s in subs)
    if sig != state.get("status_sig") or res.files_changed:
        state["status_sig"] = sig
        with open(os.path.join(cycle_dir(root, cfg, schedule), "STATUS.md"), "w") as fh:
            fh.write(status)
        res.files_changed = True
        save_state(root, cfg, schedule, state)
    return res
