"""Unit tests on a tiny, hand-checkable portfolio: ownership, eligibility, transfers, funding."""
from conftest import SWE, pending_rule, project, tiny

from frontier_portfolio_iq.domain import (
    ANY, ApprovalStatus, BenefitAllocation, BudgetLine, CapacityType, EligibilityRule, PortfolioTier,
)
from frontier_portfolio_iq.intake import validate
from frontier_portfolio_iq.lob import optimize_lob, reconcile
from frontier_portfolio_iq.metrics import lob_value_split, portfolio_metrics
from frontier_portfolio_iq.optimizer import SolveOptions, solve
from frontier_portfolio_iq.scenarios import add_release_rules

C, CO, U = PortfolioTier.COMMITTED, PortfolioTier.CONDITIONAL, PortfolioTier.UNCONSTRAINED


def value(res):
    return sum(res.data.projects[p].value for p in res.selected)


# --- ring-fencing ------------------------------------------------------------------------------
def test_ring_fenced_capacity_cannot_serve_unauthorized_lob(tiny_data):
    res = solve(tiny_data, SolveOptions(tier=C))
    assert res.feasible
    assert res.selected == {"A1"}
    assert all(a.lob == "LOB-A" for a in res.allocations if a.pool_id == "RF-A")
    # unused ring-fenced hours stay idle rather than flowing to LOB-B
    used = sum(a.hours for a in res.allocations)
    assert used == 100.0  # 50 h x 2 months for A1 only


def test_owner_authorization_allows_cross_lob_use(tiny_data):
    tiny_data.eligibility = [EligibilityRule("RF-A", ANY, ANY, authorized_project_id="B1")]
    res = solve(tiny_data, SolveOptions(tier=C))
    assert res.selected == {"A1", "B1"}
    assert res.approvals_required == []
    assert res.effective_tier == C


def test_unconstrained_relaxes_ring_fence_and_is_labelled_analytical(tiny_data):
    res = solve(tiny_data, SolveOptions(tier=U))
    assert res.selected == {"A1", "B1"}
    assert res.effective_tier == U
    assert "RELAXED-RING-FENCING" in res.approvals_required


# --- conditional transfers ---------------------------------------------------------------------
def test_pending_transfer_excluded_from_committed_but_used_in_conditional(tiny_data):
    tiny_data.transfers = [pending_rule()]
    committed = solve(tiny_data, SolveOptions(tier=C))
    assert committed.selected == {"A1"}
    assert not any(a.rule_id for a in committed.allocations)
    conditional = solve(tiny_data, SolveOptions(tier=CO))
    assert conditional.selected == {"A1", "B1"}
    assert conditional.approvals_required == ["TR"]
    assert conditional.effective_tier == CO


def test_approved_transfer_used_in_committed(tiny_data):
    tiny_data.transfers = [pending_rule(status=ApprovalStatus.APPROVED)]
    res = solve(tiny_data, SolveOptions(tier=C))
    assert res.selected == {"A1", "B1"}
    assert res.approvals_required == []
    assert res.rules_used["TR"] == 80.0


def test_rejected_transfer_never_used_outside_unconstrained(tiny_data):
    tiny_data.transfers = [pending_rule(status=ApprovalStatus.REJECTED)]
    assert solve(tiny_data, SolveOptions(tier=CO)).selected == {"A1"}


def test_transfer_limits_and_minimum_retained(tiny_data):
    tiny_data.transfers = [pending_rule(status=ApprovalStatus.APPROVED, max_hours_per_month=30)]
    assert solve(tiny_data, SolveOptions(tier=C)).selected == {"A1"}  # B1 needs 40 h/month
    tiny_data.transfers = [pending_rule(status=ApprovalStatus.APPROVED, min_retained_hours=70)]
    assert solve(tiny_data, SolveOptions(tier=C)).selected == {"A1"}  # only 30 h/month transferable


def test_transfer_efficiency_and_cost(tiny_data):
    tiny_data.transfers = [pending_rule(status=ApprovalStatus.APPROVED, efficiency=0.8, cost_per_hour=100)]
    res = solve(tiny_data, SolveOptions(tier=C))
    xfer = [a for a in res.allocations if a.rule_id == "TR"]
    assert abs(sum(a.hours for a in xfer) - 100.0) < 1e-6           # 80 effective hours / 0.8
    assert abs(sum(a.effective_hours for a in xfer) - 80.0) < 1e-6
    assert abs(portfolio_metrics(res)["transfer_cost"] - 10.0) < 1e-6  # 100 h x $100 = $10K


def test_temporary_transfer_expires(tiny_data):
    tiny_data.projects["B1"] = project("B1", "LOB-B", 500, 40, dur=4)
    tiny_data.transfers = [pending_rule(status=ApprovalStatus.APPROVED, effective_start=1, effective_end=3)]
    res = solve(tiny_data, SolveOptions(tier=C))
    assert "B1" not in res.selected                                   # 4-month need, 3-month loan
    assert all(a.month <= 3 for a in res.allocations if a.rule_id)
    tiny_data.transfers = [pending_rule(status=ApprovalStatus.APPROVED, effective_start=1, effective_end=6)]
    res = solve(tiny_data, SolveOptions(tier=C))
    assert "B1" in res.selected


def test_temporary_pool_expires_after_end_month(tiny_data):
    tiny_data.pools["RF-A"].end_month = 2
    tiny_data.projects["A2"] = project("A2", "LOB-A", 900, 10, earliest_start=3)
    res = solve(tiny_data, SolveOptions(tier=C))
    assert "A2" not in res.selected
    assert all(a.month <= 2 for a in res.allocations)
    assert tiny_data.pools["RF-A"].available_hours(3) == 0.0


def test_capacity_start_month_respected(tiny_data):
    tiny_data.pools["RF-A"].start_month = 4
    res = solve(tiny_data, SolveOptions(tier=C))
    assert res.starts["A1"] >= 4
    assert all(a.month >= 4 for a in res.allocations)
    assert tiny_data.pools["RF-A"].available_hours(3) == 0.0


# --- funding -----------------------------------------------------------------------------------
def test_restricted_funding_source_cannot_fund_other_project_types(tiny_data):
    tiny_data.projects["A1"].funding_source = "Regulatory"
    tiny_data.budgets += [BudgetLine("LOB-A", t, "Regulatory", 100, allowed_project_types=["Compliance"])
                          for t in range(1, 7)]
    assert "A1" in validate(tiny_data).unfundable_projects
    assert "A1" not in solve(tiny_data, SolveOptions(tier=U)).selected
    tiny_data.projects["A1"].project_type = "Compliance"
    assert "A1" in solve(tiny_data, SolveOptions(tier=C)).selected


def test_pool_funding_restriction(tiny_data):
    tiny_data.pools["RF-A"].allowed_funding_sources = ["Capex"]
    assert solve(tiny_data, SolveOptions(tier=C)).selected == set()


def test_monthly_budget_with_carry_forward_delays_start(tiny_data):
    for b in tiny_data.budgets:
        if b.lob_id == "LOB-A":
            b.amount = 5.0
    res = solve(tiny_data, SolveOptions(tier=C))
    assert res.starts["A1"] >= 3  # cumulative $5K/month must cover $10K/month spend
    spend = portfolio_metrics(res)["investment_by_lob_month"]["LOB-A"]
    for t in range(1, 7):
        assert sum(spend[u] for u in range(1, t + 1)) <= 5.0 * t + 1e-6


def test_month_locked_budget(tiny_data):
    for b in tiny_data.budgets:
        if b.lob_id == "LOB-A":
            b.amount, b.carry_forward = 5.0, False
    assert "A1" not in solve(tiny_data, SolveOptions(tier=C)).selected


# --- commitments --------------------------------------------------------------------------------
def test_mandatory_commitment_infeasible_is_diagnosed(tiny_data):
    tiny_data.projects["B1"].mandatory = True
    res = solve(tiny_data, SolveOptions(tier=C))
    assert not res.feasible
    assert any("B1" in d for d in res.diagnostics)


def test_mandatory_commitment_kept_even_at_value_cost(tiny_data):
    tiny_data.projects["A2"] = project("A2", "LOB-A", 50, 60, mandatory=True, deadline=2)
    tiny_data.projects["A1"].deadline = 2  # both must run in months 1-2
    res = solve(tiny_data, SolveOptions(tier=C))
    assert "A2" in res.selected and "A1" not in res.selected  # 110 h/month > 100 h capacity


# --- benefits -----------------------------------------------------------------------------------
def test_cross_lob_benefit_over_allocation_rejected(tiny_data):
    tiny_data.benefits.append(BenefitAllocation("A1", "LOB-B", "KPI-B", 100))
    rep = validate(tiny_data)
    assert any("double-counted" in e for e in rep.errors)


def test_cross_lob_benefit_split_sums_to_enterprise_value(tiny_data):
    tiny_data.benefits = [BenefitAllocation("A1", "LOB-A", "K", 120), BenefitAllocation("A1", "LOB-B", "K", 80),
                          BenefitAllocation("B1", "LOB-B", "K", 500)]
    split = lob_value_split(tiny_data, ["A1", "B1"])
    assert split["LOB-A"] == 120 and split["LOB-B"] == 580
    assert abs(sum(split.values()) - 800) < 1e-9


# --- release scenario quantification -----------------------------------------------------------
def test_release_scenario_quantifies_incremental_value_exactly(tiny_data):
    base = solve(tiny_data, SolveOptions(tier=C))
    r40 = solve(add_release_rules(tiny_data, 0.40), SolveOptions(tier=CO))
    r30 = solve(add_release_rules(tiny_data, 0.30), SolveOptions(tier=CO))
    m0, m40, m30 = (portfolio_metrics(r) for r in (base, r40, r30))
    assert abs((m40["net_value"] - m0["net_value"]) - 500.0) < 1e-6  # B1 fits in 40 h/month exactly
    assert abs(m30["net_value"] - m0["net_value"]) < 1e-6            # 30 h/month is not enough
    assert r40.effective_tier == CO and r40.approvals_required


# --- shared capacity -----------------------------------------------------------------------------
def test_shared_capacity_not_promised_to_multiple_lobs(tiny_data):
    pool = tiny_data.pools["RF-A"]
    pool.capacity_type, pool.owning_lob, pool.hours_per_fte = CapacityType.SHARED, "LOB-A", 60.0
    for p in tiny_data.projects.values():
        p.deadline = 2  # concurrent window forces a choice
    ent = solve(tiny_data, SolveOptions(tier=C))
    assert ent.selected == {"B1"}  # 50 + 40 > 60: enterprise picks the higher-value project
    lob_results = {l: optimize_lob(tiny_data, l) for l in ("LOB-A", "LOB-B")}
    assert lob_results["LOB-A"].selected == {"A1"} and lob_results["LOB-B"].selected == {"B1"}
    rec = reconcile(tiny_data, ent, lob_results)
    assert rec["oversubscribed_hours"] > 0          # standalone plans each claim the shared pool
    assert rec["enterprise_within_capacity"]
    total = sum(sum(v.values()) for v in rec["shared_commitments_by_lob"].values())
    assert total <= 60.0 * 6 + 1e-6


def test_substitution_consumes_source_pool(tiny_data):
    from frontier_portfolio_iq.domain import CapacityPool, Substitution
    tiny_data.projects["A1"] = project("A1", "LOB-A", 300, 0)
    tiny_data.projects["A1"].demand = {("Integration Engineering", k): 30 for k in (1, 2)}
    tiny_data.substitutions = [Substitution(SWE, "Integration Engineering", 0.6)]
    res = solve(tiny_data, SolveOptions(tier=C))
    assert "A1" in res.selected
    for a in res.allocations:
        assert a.pool_id == "RF-A" and abs(a.effective_hours - 0.6 * a.hours) < 1e-6
    assert abs(sum(a.hours for a in res.allocations) - 100.0) < 1e-6  # 60 effective h / 0.6
