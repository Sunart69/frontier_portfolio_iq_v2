"""End-to-end tests on the sample portfolio: tiers, 12 scenarios, reconciliation and capacity reports."""
from collections import defaultdict

import pytest

from frontier_portfolio_iq.domain import ANY, CapacityType, PortfolioTier
from frontier_portfolio_iq.metrics import lob_value_split
from frontier_portfolio_iq.optimizer import direct_eligible

pytestmark = pytest.mark.integration


def all_results(b):
    yield from b.tiers.values()
    for s in b.scenarios:
        if s.result.feasible:
            yield s.result


def test_twelve_scenarios_all_feasible(bundle):
    assert [s.scenario.sid for s in bundle.scenarios] == list(range(1, 13))
    assert all(s.result.feasible for s in bundle.scenarios), [s.result.diagnostics for s in bundle.scenarios]
    keys = {"enterprise_value", "lob_value", "investment", "projects_added", "schedule_changes",
            "overall_utilization", "ring_fenced_utilization", "transfer_cost", "compliance_coverage",
            "risk_reduction", "maturity_gain", "opportunity_cost"}
    for s in bundle.scenarios:
        assert keys <= set(s.deltas) | set(s.metrics)


def test_ring_fenced_pools_only_serve_owner_or_authorized(bundle):
    for res in all_results(bundle):
        if res.tier == PortfolioTier.UNCONSTRAINED:
            continue
        d = res.data
        rules = {r.rule_id: r for r in d.transfers}
        for a in res.allocations:
            pool = d.pools[a.pool_id]
            if pool.capacity_type == CapacityType.SHARED:
                continue
            p = d.projects[a.project_id]
            if a.kind == "direct":
                assert direct_eligible(d, pool, p)
            else:
                assert a.kind == "transfer"
                r = rules[a.rule_id]
                assert r.source_pool == pool.pool_id and r.dest_lob in (ANY, p.primary_lob)


def test_no_double_allocation_any_pool_month(bundle):
    for res in all_results(bundle):
        used = defaultdict(float)
        for a in res.allocations:
            used[(a.pool_id, a.month)] += a.hours
        for (pool, t), h in used.items():
            assert h <= res.data.pools[pool].available_hours(t) + 1e-5, (res.label, pool, t)


def test_selected_projects_receive_required_effective_hours(bundle):
    res = bundle.tiers["committed"]
    got = defaultdict(float)
    for a in res.allocations:
        got[(a.project_id, a.capability, a.month)] += a.effective_hours
    for pid, s in res.starts.items():
        for (c, k), h in res.data.projects[pid].demand.items():
            assert abs(got[(pid, c, s + k - 1)] - h) < 1e-4
    assert {k[0] for k in got} <= res.selected


def test_committed_portfolio_uses_no_unapproved_transfers(bundle):
    res = bundle.tiers["committed"]
    assert res.approvals_required == [] and res.effective_tier == PortfolioTier.COMMITTED
    rules = {r.rule_id: r for r in res.data.transfers}
    assert all(rules[r].is_authorized() for r in res.rules_used)


def test_conditional_scenarios_are_labelled_conditional(bundle):
    for s in bundle.scenarios:
        if s.result.approvals_required or s.scenario.needs_resources:
            assert s.status_label.startswith("Conditional")
            assert s.approvals
        else:
            assert s.status_label == "Committed basis"
    assert bundle.tier_metrics["unconstrained"]["tier"] == "Unconstrained Opportunity"


def test_temporary_capacity_and_start_months(bundle):
    d = bundle.data
    for res in all_results(bundle):
        for a in res.allocations:
            pool = res.data.pools[a.pool_id]
            assert a.month >= pool.start_month
            if pool.end_month is not None:
                assert a.month <= pool.end_month
            if a.rule_id:
                assert res.data.transfers[[r.rule_id for r in res.data.transfers].index(a.rule_id)].active(a.month)
    assert d.pools["RF-INT-A"].available_hours(3) == 0 and d.pools["RF-INT-A"].available_hours(4) > 0


def test_lob_budgets_and_funding_restrictions_enforced(bundle):
    for res in all_results(bundle):
        d = res.data
        spend = defaultdict(lambda: defaultdict(float))
        for pid, s in res.starts.items():
            p = d.projects[pid]
            for l, sh in p.shares().items():
                for k, c in p.cost.items():
                    spend[(l, p.funding_source)][s + k - 1] += sh * c
                types = d.budget_types(l, p.funding_source)
                assert not types or p.project_type in types
        for a in res.allocations:
            p = d.projects[a.project_id]
            for l, sh in p.shares().items():
                spend[(l, p.funding_source)][a.month] += sh * (a.transfer_cost + a.incremental_cost)
            pool = d.pools[a.pool_id]
            assert not pool.allowed_funding_sources or p.funding_source in pool.allowed_funding_sources
        for (l, f), series in spend.items():
            cum_s = cum_b = 0.0
            for t in d.months:
                cum_s += series[t]
                cum_b += sum(b.amount for b in d.budgets if b.lob_id == l and b.funding_source == f and b.month == t)
                assert cum_s <= cum_b + 1e-4, (res.label, l, f, t)


def test_cross_lob_benefits_not_double_counted(bundle):
    for res in all_results(bundle):
        split = lob_value_split(res.data, res.selected)
        assert abs(sum(split.values()) - sum(res.data.projects[p].value for p in res.selected)) < 1e-6


def test_mandatory_and_critical_commitments_feasible(bundle):
    d = bundle.data
    must = {pid for pid, p in d.projects.items() if p.mandatory or (p.critical and d.settings.protect_critical)}
    assert must
    for key in ("strict", "committed", "conditional"):
        assert must <= bundle.tiers[key].selected
    for s in bundle.scenarios:
        assert must <= s.result.selected


def test_investment_totals_reconcile(bundle):
    for m in [*bundle.tier_metrics.values(), *(s.metrics for s in bundle.scenarios)]:
        assert abs(sum(m["investment_by_lob"].values()) - m["investment"]) < 1e-6
        assert abs(sum(m["investment_by_month"].values()) - m["investment"]) < 1e-6
        for l, series in m["investment_by_lob_month"].items():
            assert abs(sum(series.values()) - m["investment_by_lob"][l]) < 1e-6
        assert abs(sum(m["lob_value"].values()) - m["enterprise_value"]) < 1e-6


def test_release_scenarios_quantify_non_negative_incremental_value(bundle):
    cm = bundle.tier_metrics["committed"]
    tol = 1.0
    for key in ("all_authorized_sharing", "release_10", "release_20", "transfer_a_b", "shared_plus_20", "contractors"):
        s = bundle.scenario(key)
        assert s.metrics["net_value"] >= cm["net_value"] - tol, key
        assert abs(s.deltas["net_value"] - (s.metrics["net_value"] - cm["net_value"])) < 1e-6
    assert bundle.tier_metrics["conditional"]["net_value"] >= cm["net_value"] - tol
    assert bundle.tier_metrics["unconstrained"]["net_value"] >= bundle.tier_metrics["conditional"]["net_value"] - tol
    assert cm["net_value"] >= bundle.tier_metrics["strict"]["net_value"] - tol
    assert all(r["incremental_value"] >= 0 for r in bundle.release_values)
    # restriction scenarios can never beat the committed plan
    for key in ("protect_compliance", "balanced", "budget_cut", "accelerate"):
        assert bundle.scenario(key).metrics["net_value"] <= cm["net_value"] + tol


def test_lob_reconciliation_detects_shared_overpromising(bundle):
    lv = bundle.lob_view
    assert lv["enterprise_within_capacity"]
    assert lv["oversubscribed_hours"] > 0
    for c in lv["shared_conflicts"]:
        assert c["claimed"] > c["available"] >= c["enterprise_allocated"] - 1e-6
    for row in lv["lobs"]:
        assert row["concession"] >= 0


def test_capacity_report_distinguishes_unused_from_committed(bundle):
    cap = bundle.capacity
    total_alloc = sum(r["allocated"] for r in cap["rows"])
    assert abs(cap["consumed_hours"] + cap["committed_not_consumed_hours"] - total_alloc) < 1e-6
    for r in cap["rows"]:
        assert abs(r["available"] - r["allocated"] - r["unused"]) < 1e-5
    assert cap["idle_ring_fenced_hours"] > 0 and cap["restricted_unavailable_hours"] <= cap["idle_ring_fenced_hours"]
    assert set(cap["shortage_by_lob"]) and set(cap["shortage_by_capability"]) and set(cap["shortage_by_month"])


def test_capacity_report_as_of_split():
    from frontier_portfolio_iq.capacity import capacity_report
    from frontier_portfolio_iq.optimizer import SolveOptions, solve
    from conftest import tiny
    d = tiny()
    d.settings.as_of_month = 2
    d.projects["A1"].deadline = 2  # A1 runs Jan-Feb: Jan is actuals, Feb is committed-not-consumed
    cap = capacity_report(solve(d, SolveOptions()))
    assert cap["consumed_hours"] == 50.0 and cap["committed_not_consumed_hours"] == 50.0


def test_project_classification(bundle):
    cls = bundle.classification
    assert {pid for pid, c in cls.items() if c == "Approved"} == bundle.tiers["committed"].selected
    for pid, c in cls.items():
        if c == "Conditional":
            assert pid in bundle.tiers["conditional"].selected


def test_results_workbook_and_dashboard(bundle, tmp_path):
    from openpyxl import load_workbook
    from frontier_portfolio_iq.analysis import write_results_workbook
    from frontier_portfolio_iq.dashboard import render_dashboard
    from frontier_portfolio_iq.style import money
    wb = load_workbook(write_results_workbook(bundle, str(tmp_path / "r.xlsx")))
    assert {"Portfolio Tiers", "Scenario Workbench", "Capacity Report", "LOB Reconciliation"} <= set(wb.sheetnames)
    assert wb["Scenario Workbench"].max_row == 13
    html = open(render_dashboard(bundle, str(tmp_path / "d.html"))).read()
    assert "LOB Trade-Off Matrix" in html and "not inherently inefficient" in html
    assert money(bundle.tier_metrics["committed"]["net_value"]) in html


def test_release_measurements_isolate_their_own_change(bundle):
    """Per-pool release values and release/transfer scenarios must not ride on unrelated pending approvals."""
    configured_pending = {r.rule_id for r in bundle.data.transfers if not r.is_authorized() and not r.synthetic}
    assert configured_pending
    for key in ("release_10", "release_20", "transfer_a_b"):
        assert not set(bundle.scenario(key).result.approvals_required) & configured_pending, key
    for r in bundle.release_values:
        if r["hours_released_used"] < 1e-6:
            assert r["incremental_value"] < 0.02 * bundle.committed["net_value"], r
