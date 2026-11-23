"""Monte Carlo risk evaluation: distributions, correlated drivers, schedule-aware simulation, metrics."""
from .distributions import Dist, parse_dist
from .uncertainty import (PoolUncertainty, ProjectUncertainty, RiskDriver, SimulationSettings, UncertaintyModel,
                          default_uncertainty, read_uncertainty)
from .simulate import PlanContext, SimulationRun, plan_context, simulate
from .metrics import summarize


def evaluate_plan(data, starts, um, mode=None, tier=None, result=None, keep_projects=True):
    """Simulate one plan and return (summary dict, SimulationRun)."""
    from ..domain import PortfolioTier
    m = um if mode is None else um.with_settings(mode=mode)
    ctx = plan_context(data, starts, m.settings.mode, tier or PortfolioTier.COMMITTED, result)
    run = simulate(ctx, m, keep_projects=keep_projects)
    return summarize(run), run
