"""Semi-annual cadence: schedule, calendar, input packages, follow-ups, and the end-to-end cycle."""
import datetime as dt
import os
import shutil

import pytest
import yaml
from openpyxl import load_workbook
from pptx import Presentation

from frontier_portfolio_iq.cadence import orchestrator
from frontier_portfolio_iq.cadence.calendar_ics import to_ics
from frontier_portfolio_iq.cadence.config import load_cadence, load_owners
from frontier_portfolio_iq.cadence.followup import (evaluate, expected_submissions, follow_up, open_input_issues,
                                                    stage)
from frontier_portfolio_iq.cadence.github_api import FakeIssues
from frontier_portfolio_iq.cadence.packages import check_package, merge_packages, split_package, write_template
from frontier_portfolio_iq.cadence.schedule import (CycleId, active_cycle, add_business_days, build_schedule,
                                                    business_days_between)
from frontier_portfolio_iq.cadence.stakeholder_deck import build_stakeholder_deck
from frontier_portfolio_iq.intake import read_workbook

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
D = dt.date


@pytest.fixture
def repo(tmp_path):
    """A throwaway repository with the real cadence config and owners who have GitHub handles."""
    shutil.copytree(os.path.join(ROOT, "cadence"), tmp_path / "cadence")
    owners_path = tmp_path / "cadence" / "owners.yaml"
    raw = yaml.safe_load(owners_path.read_text())
    raw["portfolio_lead"]["github"] = "pm-lead"
    raw["escalation"]["github"] = "cio"
    for lob_id, lob in raw["lobs"].items():
        lob["executive_sponsor"]["github"] = f"sponsor-{lob_id.lower()}"
        for pkg, p in lob["owners"].items():
            p["github"] = f"{pkg}-{lob_id.lower()}"
    owners_path.write_text(yaml.safe_dump(raw))
    return str(tmp_path)


@pytest.fixture
def cfg(repo):
    return load_cadence(os.path.join(repo, "cadence", "cadence.yaml"))


@pytest.fixture
def owners(repo):
    return load_owners(os.path.join(repo, "cadence", "owners.yaml"))


# --- schedule ---------------------------------------------------------------------------------------
def test_workback_schedule_2027_h2(cfg):
    s = build_schedule("2027-H2", cfg)
    assert s.decision_date == D(2027, 6, 8)
    expect = {"kickoff": D(2027, 3, 30), "templates": D(2027, 4, 6), "due_strategy": D(2027, 4, 23),
              "due_budget": D(2027, 4, 30), "due_capacity": D(2027, 4, 30), "due_initiatives": D(2027, 5, 7),
              "due_enterprise": D(2027, 5, 14), "solver_run": D(2027, 5, 25), "preread": D(2027, 6, 1),
              "publish": D(2027, 6, 15)}
    for k, v in expect.items():
        assert s.date(k) == v, k
    assert all(m.date.weekday() < 5 and m.date not in cfg.holidays for m in s.milestones)


def test_next_cycles_and_decision_weekday(cfg):
    s = build_schedule("2028-H1", cfg)
    assert s.decision_date == D(2027, 12, 7) and s.decision_date.weekday() == 1
    assert str(CycleId.parse("2027-h2").next()) == "2028-H1"
    assert str(CycleId.parse("2028-H1").next()) == "2028-H2"


def test_holiday_moves_due_date_to_previous_business_day(cfg):
    cfg.holidays.add(D(2027, 4, 30))
    assert build_schedule("2027-H2", cfg).due("budget") == D(2027, 4, 29)


def test_override_decision_date(cfg):
    cfg.cycle_overrides["2027-H2"] = {"decision_date": D(2027, 6, 15)}
    s = build_schedule("2027-H2", cfg)
    assert s.decision_date == D(2027, 6, 15) and s.kickoff == D(2027, 4, 6)


def test_active_cycle_rolls_forward(cfg):
    assert str(active_cycle(D(2026, 11, 21), cfg).cycle) == "2027-H2"
    assert str(active_cycle(D(2027, 6, 15), cfg).cycle) == "2027-H2"
    assert str(active_cycle(D(2027, 6, 16), cfg).cycle) == "2028-H1"
    s = build_schedule("2027-H2", cfg)
    assert s.phase(D(2027, 3, 1)) == "not started"
    assert s.phase(D(2027, 4, 26)) == "collecting inputs"
    assert s.phase(D(2027, 5, 26)) == "optimizing"
    assert s.phase(D(2027, 6, 30)) == "closed"


def test_business_day_math(cfg):
    assert business_days_between(D(2027, 4, 30), D(2027, 5, 4), cfg.holidays) == 2   # Fri → Tue
    assert add_business_days(D(2027, 5, 28), 1, cfg.holidays) == D(2027, 6, 1)       # Memorial Day skipped


# --- calendar ---------------------------------------------------------------------------------------
def test_ics_calendar(cfg, owners):
    s = build_schedule("2027-H2", cfg)
    ics = to_ics(s, owners)
    assert ics.count("BEGIN:VEVENT") == len(s.milestones) == 13
    assert "DTSTART;VALUE=DATE:20270608" in ics and "UID:fpiq-2027-H2-decision@frontier-portfolio-iq" in ics
    assert all(len(line.encode()) <= 75 for line in ics.split("\r\n"))
    assert "\r\n" in ics and "[Retail budget owner]" in ics


# --- packages ---------------------------------------------------------------------------------------
def test_split_and_merge_round_trip(repo, tmp_path):
    base = os.path.join(repo, "cadence", "baseline_intake.xlsx")
    out = tmp_path / "pk"
    subs = {}
    for lob in ("LOB-A", "LOB-B", "LOB-C", "ENT"):
        for p in ("strategy", "budget", "capacity", "initiatives"):
            f = write_template(base, str(out), p, lob, [("x", "y")])
            assert check_package(f).ok, (p, lob, check_package(f).errors)
            subs[(p, lob)] = f
    subs[("enterprise", "ALL")] = write_template(base, str(out), "enterprise", None, [])
    m = merge_packages(subs, str(out / "merged.xlsx"))
    assert m.ok, m.errors
    a, b = read_workbook(base), read_workbook(m.master_path)
    assert set(a.projects) == set(b.projects) and set(a.pools) == set(b.pools)
    assert len(a.budgets) == len(b.budgets) and len(a.benefits) == len(b.benefits)
    assert len(a.transfers) == len(b.transfers) and len(a.eligibility) == len(b.eligibility)
    for pid in a.projects:
        assert a.projects[pid].demand == b.projects[pid].demand


def test_template_holds_only_owner_rows(repo):
    rows = split_package(os.path.join(repo, "cadence", "baseline_intake.xlsx"), "budget", "LOB-B")
    assert rows["LOB Budget Allocation"] and all(r["LOB ID"] == "LOB-B" for r in rows["LOB Budget Allocation"])
    rows = split_package(os.path.join(repo, "cadence", "baseline_intake.xlsx"), "initiatives", "LOB-C")
    assert {r["Project ID"] for r in rows["Projects"]} == {"C01", "C02", "C03", "C04"}


def _template(repo, tmp_path, pkg, lob):
    return write_template(os.path.join(repo, "cadence", "baseline_intake.xlsx"), str(tmp_path), pkg, lob, [])


def test_package_check_rejects_other_lob_rows(repo, tmp_path):
    f = _template(repo, tmp_path, "budget", "LOB-A")
    wb = load_workbook(f)
    wb["LOB Budget Allocation"]["A2"] = "LOB-B"
    wb.save(f)
    chk = check_package(f)
    assert not chk.ok and any("another LOB" in e for e in chk.errors)


def test_package_check_rejects_bad_numbers_and_missing_sheets(repo, tmp_path):
    f = _template(repo, tmp_path, "initiatives", "LOB-A")
    wb = load_workbook(f)
    wb["Project Costs"]["C2"] = "lots"
    del wb["Project Demand"]
    wb.save(f)
    errs = check_package(f).errors
    assert any("not a number" in e for e in errs) and any("Missing sheet 'Project Demand'" in e for e in errs)


def test_package_check_requires_naming_convention(tmp_path):
    p = tmp_path / "my budget.xlsx"
    p.write_bytes(b"")
    assert "File name must be" in check_package(str(p)).errors[0]


# --- follow-ups -------------------------------------------------------------------------------------
def test_reminder_and_escalation_stages(repo, cfg, owners):
    s = build_schedule("2027-H2", cfg)
    sub = next(x for x in expected_submissions(repo, cfg, owners, s) if x.package == "budget")
    due = sub.due  # Fri Apr 30
    assert stage(sub, due - dt.timedelta(days=8), cfg) is None
    assert stage(sub, due - dt.timedelta(days=7), cfg) == "due-in-7"
    assert stage(sub, due - dt.timedelta(days=3), cfg) == "due-in-7"
    assert stage(sub, due - dt.timedelta(days=2), cfg) == "due-in-2"
    assert stage(sub, due, cfg) == "due-today"
    assert stage(sub, D(2027, 5, 3), cfg) == "overdue"      # 1 business day late
    assert stage(sub, D(2027, 5, 4), cfg) == "escalate-1"   # 2 business days late
    assert stage(sub, D(2027, 5, 7), cfg) == "escalate-2"   # 5 business days late
    sub.state = "valid"
    assert stage(sub, D(2027, 5, 7), cfg) == "received"


def test_kickoff_opens_one_issue_per_input_once(repo, cfg, owners):
    s = build_schedule("2027-H2", cfg)
    gh = FakeIssues()
    subs = evaluate(expected_submissions(repo, cfg, owners, s))
    assert len(subs) == 17
    assert len(open_input_issues(gh, subs, s, repo, cfg)) == 17
    assert open_input_issues(gh, subs, s, repo, cfg) == []
    issue = next(i for i in gh.store if "LOB budget allocation — Retail Banking" in i["title"])
    assert issue["assignees"] == ["budget-lob-a"] and "due Apr 30" in issue["title"]
    assert "cycles/2027-H2/inputs/budget__LOB-A.xlsx" in issue["body"]


def test_follow_ups_are_idempotent_and_escalate(repo, cfg, owners):
    s = build_schedule("2027-H2", cfg)
    gh = FakeIssues()
    subs = evaluate(expected_submissions(repo, cfg, owners, s))
    open_input_issues(gh, subs, s, repo, cfg)
    day = D(2027, 5, 4)   # budget/capacity 2 business days late; strategy 7 late; initiatives due in 3
    first = follow_up(gh, subs, s, owners, cfg, day, repo)
    assert first and follow_up(gh, subs, s, owners, cfg, day, repo) == []
    stages = {(a["package"], a["lob"]): a["stage"] for a in first}
    assert stages[("budget", "LOB-A")] == "escalate-1"
    assert stages[("strategy", "LOB-A")] == "escalate-2"
    assert stages[("initiatives", "LOB-A")] == "due-in-7"
    budget_issue = next(i for i in gh.store if "LOB budget allocation — Retail Banking" in i["title"])
    text = gh.comments(budget_issue["number"])[-1]["body"]
    assert "@budget-lob-a" in text and "@sponsor-lob-a" in text
    strat = next(i for i in gh.store if "LOB strategy & outcomes — Retail Banking" in i["title"])
    assert "@cio" in gh.comments(strat["number"])[-1]["body"]


def test_valid_submission_closes_issue_and_stops_reminders(repo, cfg, owners, tmp_path):
    s = build_schedule("2027-H2", cfg)
    gh = FakeIssues()
    subs = evaluate(expected_submissions(repo, cfg, owners, s))
    open_input_issues(gh, subs, s, repo, cfg)
    target = next(x for x in subs if x.key == ("budget", "LOB-A"))
    os.makedirs(os.path.dirname(target.path), exist_ok=True)
    shutil.copy(_template(repo, tmp_path, "budget", "LOB-A"), target.path)
    subs = evaluate(expected_submissions(repo, cfg, owners, s))
    acts = follow_up(gh, subs, s, owners, cfg, D(2027, 4, 29), repo)
    got = next(a for a in acts if a["package"] == "budget" and a["lob"] == "LOB-A")
    assert got["stage"] == "received"
    issue = next(i for i in gh.store if "LOB budget allocation — Retail Banking" in i["title"])
    assert issue["state"] == "closed"
    assert not [a for a in follow_up(gh, subs, s, owners, cfg, D(2027, 5, 10), repo)
                if a["package"] == "budget" and a["lob"] == "LOB-A"]


# --- stakeholder deck -------------------------------------------------------------------------------
def test_stakeholder_deck_matches_schedule(cfg, owners, tmp_path):
    s = build_schedule("2027-H2", cfg)
    path = str(tmp_path / "deck.pptx")
    manifest = build_stakeholder_deck(s, owners, path, cfg)
    prs = Presentation(path)
    assert len(prs.slides) == 8
    assert manifest[3] == {m.key: m.date.isoformat() for m in s.milestones}
    text4 = "\n".join(c.text for sh in prs.slides[3].shapes if sh.has_table
                      for r in sh.table.rows for c in r.cells)
    for pkg in ("strategy", "budget", "capacity", "initiatives", "enterprise"):
        assert s.due(pkg).strftime("%a %b %-d") in text4
    text1 = "\n".join(sh.text_frame.text for sh in prs.slides[0].shapes if sh.has_text_frame)
    assert "Jun 8" in text1 and "17" in text1


# --- the cycle, end to end ----------------------------------------------------------------------------
def test_tick_runs_the_cycle(repo, cfg, owners, monkeypatch):
    gh = FakeIssues()
    calls = []
    monkeypatch.setattr(orchestrator, "run_cycle",
                        lambda *a, **k: calls.append(1) or {"ran": True, "carried_forward": [],
                                                            "committed_net_value": 27000.0,
                                                            "committed_projects": 14, "conditional_gain": 1000.0})
    r = orchestrator.tick(repo, cfg, owners, gh, D(2027, 3, 1))
    assert "Waiting" in r.actions[0] and not gh.store

    r = orchestrator.tick(repo, cfg, owners, gh, D(2027, 3, 30))
    assert r.files_changed and any("Kickoff" in a for a in r.actions)
    cdir = os.path.join(repo, "cycles", "2027-H2")
    for f in ("calendar.ics", "SCHEDULE.md", "STATUS.md", "state.json", "stakeholder_input_deck_2027-H2.pptx",
              "templates/budget__LOB-A.xlsx", "templates/enterprise__ALL.xlsx"):
        assert os.path.exists(os.path.join(cdir, f)), f
    inputs = [i for i in gh.store if "fpiq:input" in i["labels"]]
    status = [i for i in gh.store if "fpiq:status" in i["labels"]]
    assert len(inputs) == 17 and len(status) == 1

    again = orchestrator.tick(repo, cfg, owners, gh, D(2027, 3, 30))
    assert not again.files_changed and len(gh.store) == 18      # idempotent

    for name in os.listdir(os.path.join(cdir, "templates")):    # every owner submits on time
        shutil.copy(os.path.join(cdir, "templates", name), os.path.join(cdir, "inputs", name))
    r = orchestrator.tick(repo, cfg, owners, gh, D(2027, 4, 20))
    assert len([f for f in r.followups if f["stage"] == "received"]) == 17
    assert all(i["state"] == "closed" for i in gh.store if "fpiq:input" in i["labels"])

    r = orchestrator.tick(repo, cfg, owners, gh, D(2027, 5, 25))
    assert r.ran_prioritization and calls == [1]
    assert "Optimization run complete" in status[0]["body"]
    orchestrator.tick(repo, cfg, owners, gh, D(2027, 5, 26))
    assert calls == [1]                                        # runs once per cycle
    r = orchestrator.tick(repo, cfg, owners, gh, D(2027, 6, 15))
    assert any("Publish overdue" in a for a in r.actions)      # publishing waits for the LT decision record


def test_tick_blocks_run_and_reports_missing_inputs(repo, cfg, owners):
    gh = FakeIssues()
    orchestrator.tick(repo, cfg, owners, gh, D(2027, 3, 30))
    r = orchestrator.tick(repo, cfg, owners, gh, D(2027, 5, 25))
    assert not r.ran_prioritization and len(r.blocked) == 17
    status = next(i for i in gh.store if "fpiq:status" in i["labels"])
    assert "Optimization run blocked" in status["body"] and "0/17 received" in status["body"]


@pytest.mark.integration
def test_run_cycle_produces_lt_preread(repo, cfg, owners):
    cfg.decision_quick = True            # small frontier, 1,000 iterations: keeps the test fast
    s = build_schedule("2027-H2", cfg)
    orchestrator.plan(repo, cfg, owners, "2027-H2", write_deck=False)
    cdir = os.path.join(repo, "cycles", "2027-H2")
    for name in os.listdir(os.path.join(cdir, "templates")):
        shutil.copy(os.path.join(cdir, "templates", name), os.path.join(cdir, "inputs", name))
    out = orchestrator.run_cycle(repo, cfg, owners, s, release_scan=False)
    n = len(out["recommended_starts"])
    assert out["ran"] and out["committed_projects"] == n and n >= 10
    for f in ("portfolio_results.xlsx", "lob_dashboard.html", "executive_deck_2027-H2.pptx",
              "analysis_summary.json", "decision_dashboard.html", "ceo_decision_deck_2027-H2.pptx",
              "decision_intelligence.json", "decision_results.xlsx", "solver_confidence.md"):
        assert os.path.exists(os.path.join(cdir, "outputs", f)), f
    di = out["decision_intelligence"]
    assert di["solver_confidence"] in ("CONFIRMED_OPTIMAL", "PROVEN_SINGLE") and di["model_version"].startswith("2027-H2@")
    assert {"aggressive", "balanced", "conservative", "accelerated"} <= set(out["alternatives"])

    # After the LT meeting: the pre-filled decision record is completed and the approved portfolio published.
    rec_path = os.path.join(cdir, "decision", "lt_decision.yaml")
    text0 = open(rec_path).read()
    assert "Evaluated alternatives" in text0 and "conservative" in text0
    state = {"ran": "2027-05-25", "run_summary": {k: v for k, v in out.items() if k != "outputs"}}
    assert orchestrator.publish_cycle(repo, cfg, s, state)["reason"] == "no decision recorded"
    open(rec_path, "w").write(text0.replace("decided_on:  ", "decided_on: 2027-06-08 #", 1))
    pub = orchestrator.publish_cycle(repo, cfg, s, state)
    assert pub["published"] and pub["n_selected"] == n and pub["changes"] == 0
    for f in pub["files"].values():
        assert os.path.exists(os.path.join(repo, f)), f
    memo = open(os.path.join(repo, pub["files"]["memo_md"])).read()
    assert "This is the plan recommended in the pre-read" in memo and "## Risk profile (simulated)" in memo

    # LT adopts the conservative alternative instead: the published plan is exactly that plan's scope.
    cons = out["alternatives"]["conservative"]
    open(rec_path, "w").write(text0.replace("decided_on:  ", "decided_on: 2027-06-08 #", 1)
                              .replace("alternative:   ", "alternative: conservative #", 1))
    pub2 = orchestrator.publish_cycle(repo, cfg, s, state)
    assert pub2["published"] and pub2["n_selected"] <= len(cons["starts"])
    summ = __import__("json").load(open(os.path.join(repo, pub2["files"]["summary"])))
    assert set(summ["starts"]) <= set(cons["starts"]) and summ["alternative"] == "conservative"
    assert "adopted the evaluated 'conservative' plan" in open(os.path.join(repo, pub2["files"]["memo_md"])).read()
