"""After the LT decision: the decision record, the re-solve, the approved-portfolio memo, and publishing."""
import datetime as dt
import os
import shutil

import pytest
import yaml
from docx import Document
from openpyxl import load_workbook

from frontier_portfolio_iq.cadence import orchestrator
from frontier_portfolio_iq.cadence.config import load_cadence, load_owners
from frontier_portfolio_iq.cadence.decision import (ActionItem, Decision, DecisionError, apply_approvals, decision_path,
                                                    finalize, load_decision, publish, write_decision_template)
from frontier_portfolio_iq.cadence.github_api import FakeIssues
from frontier_portfolio_iq.cadence.schedule import build_schedule
from frontier_portfolio_iq.domain import ApprovalStatus, PortfolioTier
from frontier_portfolio_iq.metrics import portfolio_metrics
from frontier_portfolio_iq.optimizer import SolveOptions, solve
from frontier_portfolio_iq.sample_data import build_sample

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
D = dt.date


@pytest.fixture(scope="module")
def sched():
    return build_schedule("2027-H2", load_cadence(os.path.join(ROOT, "cadence", "cadence.yaml")))


@pytest.fixture(scope="module")
def sample():
    data = build_sample()
    rec = solve(data, SolveOptions(tier=PortfolioTier.COMMITTED))
    return data, dict(rec.starts), portfolio_metrics(rec)["net_value"]


def record(path, **fields):
    raw = {"cycle": "2027-H2", "decided_on": "2027-06-08", "decided_by": "Leadership team", **fields}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        yaml.safe_dump(raw, fh)
    return path


# --- the record -------------------------------------------------------------------------------------
def test_template_is_prefilled_parseable_and_not_overwritten(tmp_path, sched):
    p = str(tmp_path / "decision" / "lt_decision.yaml")
    write_decision_template(p, sched, ["A01", "B01"], ["TR-02"], ["B05"])
    text = open(p).read()
    assert "Tue Jun 8, 2027" in text and "TR-02" in text and "B05" in text
    dec = load_decision(p)
    assert dec.cycle == "2027-H2" and not dec.recorded and dec.approve == []
    with open(p, "a") as fh:
        fh.write("# edited by the portfolio office\n")
    write_decision_template(p, sched, [], [], [])
    assert "edited by the portfolio office" in open(p).read()


def test_bad_dates_and_actions_are_reported(tmp_path):
    with pytest.raises(DecisionError, match="decided_on"):
        load_decision(record(str(tmp_path / "a.yaml"), decided_on="June 8th"))
    with pytest.raises(DecisionError, match="actions"):
        load_decision(record(str(tmp_path / "b.yaml"), actions=[{"owner": "x"}]))


def test_unknown_ids_are_rejected_before_solving(sample, sched):
    data, rec, _ = sample
    dec = Decision("2027-H2", D(2027, 6, 8), include=["ZZ9", "A04"], exclude=["A04"],
                   approve=["TR-99", "REL-NOPE-20"], start_by={"A01": "2031-01"})
    fin = finalize(data, dec, sched, rec)
    text = " | ".join(fin.errors)
    assert not fin.ok and fin.result is None
    for needle in ("ZZ9", "A04 is in both", "TR-99", "REL-NOPE-20", "outside the planning horizon"):
        assert needle in text, needle
    assert not finalize(data, Decision("2028-H1", D(2027, 6, 8)), sched, rec).ok   # wrong folder


def test_approvals_only_grant_what_lt_approved(sample):
    data, _, _ = sample
    d = apply_approvals(data, ["TR-02", "REL-RF-DATA-C-20"])
    rules = {r.rule_id: r for r in d.transfers}
    assert rules["TR-02"].approval_status == ApprovalStatus.APPROVED
    assert rules["REL-RF-DATA-C-20"].is_authorized() and rules["REL-RF-DATA-C-20"].max_pct == pytest.approx(0.2)
    assert not rules["TR-03"].is_authorized()                    # still pending: never assumed
    assert {r.rule_id: r.approval_status for r in data.transfers}["TR-02"] == ApprovalStatus.PENDING  # input untouched


# --- the re-solve -----------------------------------------------------------------------------------
def test_unchanged_decision_reproduces_the_recommendation(sample, sched):
    data, rec, rec_value = sample
    fin = finalize(data, Decision("2027-H2", D(2027, 6, 8)), sched, rec)
    assert fin.ok and fin.result.starts == rec and fin.changes == []
    assert fin.metrics["net_value"] == pytest.approx(rec_value, rel=1e-6)


def test_lt_changes_are_applied_and_dates_kept(sample, sched):
    data, rec, rec_value = sample
    dec = Decision("2027-H2", D(2027, 6, 8), approve=["TR-02"], exclude=["E05"],
                   notes={"E05": "Deferred to 2028-H1"})
    fin = finalize(data, dec, sched, rec)
    assert fin.ok
    starts = fin.result.starts
    assert "E05" not in starts
    for pid in set(starts) & set(rec):                           # owners keep the dates they planned on
        assert starts[pid] == rec[pid], pid
    used = {a.rule_id for a in fin.result.allocations if a.rule_id}
    assert used <= {r.rule_id for r in apply_approvals(data, ["TR-02"]).transfers if r.is_authorized()}
    removed = next(c for c in fin.changes if c["project"] == "E05")
    assert removed["change"] == "Removed" and removed["why"] == "Deferred to 2028-H1"
    added = {c["project"] for c in fin.changes if c["change"] == "Added"}
    assert added == set(starts) - set(rec)


def test_start_by_is_honoured(sample, sched):
    data, rec, _ = sample
    late = max(rec, key=rec.get)
    month = rec[late] - 1
    fin = finalize(data, Decision("2027-H2", D(2027, 6, 8), start_by={late: month}), sched, rec)
    assert fin.ok and fin.result.starts[late] <= month


def test_infeasible_decisions_explain_themselves(sample, sched):
    data, rec, _ = sample
    fin = finalize(data, Decision("2027-H2", D(2027, 6, 8), include=sorted(data.projects)), sched, rec)
    assert not fin.ok and "cannot all be met" in fin.reason and fin.errors


# --- the memo ---------------------------------------------------------------------------------------
def test_memo_register_and_summary(sample, sched, tmp_path):
    data, rec, rec_value = sample
    dec = Decision("2027-H2", D(2027, 6, 8), approve=["TR-02"], exclude=["E05"],
                   notes={"TR-02": "Approved by Wealth EVP"},
                   actions=[ActionItem("Re-submit E05", "Enterprise PMO lead", D(2027, 11, 15))])
    fin = finalize(data, dec, sched, rec)
    files = publish(fin, dec, sched, str(tmp_path / "published"), rec_value)
    md = open(files["memo_md"]).read()
    for needle in ("# Approved portfolio, 2027-H2", "Decided Jun 8, 2027", "## Approved initiatives",
                   "## Changes from the pre-read", "## Approvals granted", "Approved by Wealth EVP",
                   "## Not funded this cycle", "## Follow-up actions", "Re-submit E05", "| Total |"):
        assert needle in md, needle
    assert f"LT approved {fin.metrics['n_selected']} initiatives" in md

    doc = Document(files["memo_docx"])
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "Approved portfolio, 2027-H2" in text and len(doc.tables) == 6
    assert len(doc.tables[0].rows) == fin.metrics["n_selected"] + 1

    ws = load_workbook(files["register"])["Approved portfolio"]
    decisions = {r[0]: r[3] for r in ws.iter_rows(min_row=2, values_only=True)}
    assert decisions["E05"] == "Not funded" and sum(v == "Approved" for v in decisions.values()) == \
        fin.metrics["n_selected"]
    assert len(decisions) == len(data.projects)


def test_publish_refuses_a_failed_finalize(sample, sched, tmp_path):
    data, rec, _ = sample
    fin = finalize(data, Decision("2027-H2", D(2027, 6, 8), include=["ZZ9"]), sched, rec)
    with pytest.raises(DecisionError):
        publish(fin, Decision("2027-H2", D(2027, 6, 8)), sched, str(tmp_path))


# --- the cadence ------------------------------------------------------------------------------------
@pytest.fixture
def repo(tmp_path):
    shutil.copytree(os.path.join(ROOT, "cadence"), tmp_path / "cadence")
    p = tmp_path / "cadence" / "owners.yaml"
    raw = yaml.safe_load(p.read_text())
    raw["portfolio_lead"]["github"] = "pm-lead"
    p.write_text(yaml.safe_dump(raw))
    return str(tmp_path)


def _ran(repo, cfg, sched):
    orchestrator.save_state(repo, cfg, sched, {"planned": "2027-03-30", "kicked_off": "2027-03-30",
                                               "ran": "2027-05-25", "run_summary": {"committed_net_value": 1.0}})


def test_tick_waits_nudges_once_then_publishes(repo, monkeypatch):
    cfg = load_cadence(os.path.join(repo, "cadence", "cadence.yaml"))
    owners = load_owners(os.path.join(repo, "cadence", "owners.yaml"))
    sched = build_schedule("2027-H2", cfg)
    _ran(repo, cfg, sched)
    gh = FakeIssues()
    calls = []
    real = orchestrator.publish_cycle

    def fake(root, cfg_, schedule, state=None):
        out = real(root, cfg_, schedule, state)
        if not out["published"] and out["reason"] == "no decision recorded":
            return out
        calls.append(str(schedule.cycle))
        return {"published": True, "files": {"memo_md": "cycles/2027-H2/published/approved_portfolio_2027-H2.md"},
                "n_selected": 15, "investment": 9900.0, "net_value": 28700.0, "changes": 5,
                "decided_on": "2027-06-08"}
    monkeypatch.setattr(orchestrator, "publish_cycle", fake)

    r = orchestrator.tick(repo, cfg, owners, gh, D(2027, 6, 9))
    status = next(i for i in gh.store if "fpiq:status" in i["labels"])
    assert "Waiting for the LT decision record" in status["body"] and not calls

    r = orchestrator.tick(repo, cfg, owners, gh, D(2027, 6, 15))
    assert "Publish overdue" in " ".join(r.actions) and "Publish overdue" in status["body"]
    orchestrator.tick(repo, cfg, owners, gh, D(2027, 6, 15))
    nudges = [c for c in gh.comments(status["number"]) if "decision-overdue" in c["body"]]
    assert len(nudges) == 1 and "@pm-lead" in nudges[0]["body"]

    # The record arrives after the publish date: the daily run has moved on to 2028-H1 but still publishes it.
    record(decision_path(os.path.join(repo, "cycles", "2027-H2")), approve=["TR-02"])
    r = orchestrator.tick(repo, cfg, owners, gh, D(2027, 6, 21))
    assert r.cycle == "2028-H1" and calls == ["2027-H2"] and r.files_changed
    assert any(a.startswith("2027-H2: Published the approved portfolio") for a in r.actions)
    assert orchestrator.load_state(repo, cfg, sched)["published"] == "2027-06-21"
    assert "Approved portfolio published" in status["body"]
    assert sum("fpiq:published" in c["body"] for c in gh.comments(status["number"])) == 1
    orchestrator.tick(repo, cfg, owners, gh, D(2027, 6, 22))
    assert calls == ["2027-H2"]                                  # publishes once


def test_tick_reports_a_bad_record_once(repo):
    cfg = load_cadence(os.path.join(repo, "cadence", "cadence.yaml"))
    owners = load_owners(os.path.join(repo, "cadence", "owners.yaml"))
    sched = build_schedule("2027-H2", cfg)
    _ran(repo, cfg, sched)
    record(decision_path(os.path.join(repo, "cycles", "2027-H2")), decided_on="next Tuesday")
    gh = FakeIssues()
    for day in (9, 10):
        r = orchestrator.tick(repo, cfg, owners, gh, D(2027, 6, day))
    status = next(i for i in gh.store if "fpiq:status" in i["labels"])
    assert "Publish blocked" in status["body"] and "decided_on" in status["body"]
    assert sum("publish-blocked" in c["body"] for c in gh.comments(status["number"])) == 1
    assert not orchestrator.load_state(repo, cfg, sched).get("published")


def test_cli_run_records_state_so_publish_can_follow(repo, monkeypatch):
    from frontier_portfolio_iq.cli import main
    cfg = load_cadence(os.path.join(repo, "cadence", "cadence.yaml"))
    monkeypatch.setattr(orchestrator, "run_cycle", lambda *a, **k: {
        "ran": True, "outputs": "x", "committed_net_value": 1.0, "recommended_starts": {"A01": 1}})
    assert main(["cycle", "run", "--root", repo, "--cycle", "2027-H2", "--today", "2027-05-25"]) == 0
    state = orchestrator.load_state(repo, cfg, build_schedule("2027-H2", cfg))
    assert state["ran"] == "2027-05-25" and state["run_summary"]["recommended_starts"] == {"A01": 1}
    assert "outputs" not in state["run_summary"]
    # publish now gets past the "not run" check and asks for the decision record instead
    assert main(["cycle", "publish", "--root", repo, "--cycle", "2027-H2"]) == 2


def test_preserve_mode_keeps_the_risk_adjusted_recommendation(sample, sched):
    """v2 pre-reads recommend on risk-adjusted grounds: publishing keeps that plan; only LT's explicit
    changes alter it, and approvals alone add nothing."""
    data, rec, _ = sample
    needed = {q for i in rec for q in data.projects[i].depends_on}
    optional = [i for i in sorted(rec) if i not in needed and not (data.projects[i].mandatory or
                data.projects[i].compliance or data.projects[i].critical or data.projects[i].deadline)]
    smaller = {i: s for i, s in rec.items() if i not in optional[-3:]}   # a deliberately smaller plan
    fin = finalize(data, Decision("2027-H2", D(2027, 6, 8), approve=["TR-02"]), sched, smaller, preserve=True)
    assert fin.ok and fin.result.starts == smaller and fin.changes == []
    drop = next(iter(smaller))
    keep = {i: s for i, s in smaller.items() if i != drop}
    if not (data.projects[drop].mandatory or data.projects[drop].critical):
        fin2 = finalize(data, Decision("2027-H2", D(2027, 6, 8), exclude=[drop]), sched, smaller, preserve=True)
        assert fin2.ok and drop not in fin2.result.starts
        assert all(fin2.result.starts.get(i) == s for i, s in keep.items())
    add = next(i for i in rec if i not in smaller)
    fin3 = finalize(data, Decision("2027-H2", D(2027, 6, 8), include=[add]), sched, smaller, preserve=True)
    assert fin3.ok and add in fin3.result.starts
    assert any(c["project"] == add and c["change"] == "Added" for c in fin3.changes)
