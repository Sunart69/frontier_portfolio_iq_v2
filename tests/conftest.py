"""Shared fixtures: a tiny hand-checkable portfolio, and the full sample analysis (session-scoped)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from frontier_portfolio_iq.domain import (  # noqa: E402
    LOB, ApprovalStatus, BenefitAllocation, BudgetLine, CapacityPool, CapacityType, PortfolioData,
    Project, Settings, TransferRule,
)

SWE = "Software Engineering"


def project(pid, lob, value, hours, dur=2, cost=10.0, **kw):
    return Project(project_id=pid, name=pid, primary_lob=lob, value=value, duration=dur,
                   demand={(SWE, k): hours for k in range(1, dur + 1)}, cost={k: cost for k in range(1, dur + 1)},
                   **kw)


def tiny(**over) -> PortfolioData:
    """Two LOBs. LOB-A owns a ring-fenced SWE pool (100 h/month); LOB-B has no capacity of its own.
    Project B1 (value 500) can only be delivered if A's capacity reaches LOB-B."""
    s = Settings(horizon=6, schedule_discount=0.0, protect_critical=False, time_limit=20, mip_gap=1e-6)
    lobs = {"LOB-A": LOB("LOB-A", "Alpha"), "LOB-B": LOB("LOB-B", "Beta")}
    projects = {
        "A1": project("A1", "LOB-A", 300, 50),
        "B1": project("B1", "LOB-B", 500, 40),
    }
    pools = {"RF-A": CapacityPool("RF-A", SWE, "LOB-A", CapacityType.RING_FENCED, 1, None, 1.0,
                                  utilization=1.0, hours_per_fte=100.0, release_eligible=True,
                                  release_efficiency=1.0)}
    budgets = [BudgetLine(l, t, "Opex", 100.0) for l in lobs for t in range(1, 7)]
    benefits = [BenefitAllocation("A1", "LOB-A", "KPI-A", 300), BenefitAllocation("B1", "LOB-B", "KPI-B", 500)]
    d = PortfolioData(lobs, projects, pools, [], [], budgets, [], benefits, [], s)
    for k, v in over.items():
        setattr(d, k, v)
    return d


def pending_rule(status=ApprovalStatus.PENDING, **kw):
    base = dict(rule_id="TR", source_pool="RF-A", source_lob="LOB-A", dest_lob="LOB-B", max_pct=1.0,
                efficiency=1.0, cost_per_hour=0.0, approval_required=True, approval_status=status)
    base.update(kw)
    return TransferRule(**base)


@pytest.fixture
def tiny_data():
    return tiny()


@pytest.fixture(scope="session")
def bundle():
    from frontier_portfolio_iq.analysis import run_analysis
    from frontier_portfolio_iq.sample_data import build_sample
    return run_analysis(build_sample())


@pytest.fixture(scope="session")
def deck(bundle, tmp_path_factory):
    from frontier_portfolio_iq.deck import build_deck
    path = str(tmp_path_factory.mktemp("deck") / "deck.pptx")
    manifest = build_deck(bundle, path)
    return path, manifest


@pytest.fixture(scope="session")
def sample():
    from frontier_portfolio_iq.sample_data import build_sample
    return build_sample()


@pytest.fixture(scope="session")
def di_bundle(sample):
    """One decision-intelligence run shared by the v2 integration tests (reduced sizes for speed)."""
    from frontier_portfolio_iq.intelligence import DecisionConfig, run_decision_intelligence
    from frontier_portfolio_iq.pareto import ParetoConfig
    from frontier_portfolio_iq.risk.stochastic import StochasticConfig
    cfg = DecisionConfig(pareto=ParetoConfig(grid_points=3, refine_rounds=1, weighted_step=0.5, max_points=24),
                         iterations=2000, whatif_iterations=500, stochastic=StochasticConfig(scenarios=20),
                         whatif_pareto=ParetoConfig(grid_points=2, refine_rounds=0, weighted_step=0, max_points=6))
    return run_decision_intelligence(sample, config=cfg)
