"""Pareto frontier: dominance, ranks, duplicates, scope, epsilon vs weighted sums, non-convex frontiers."""
import pytest

from frontier_portfolio_iq.canonical import ModelVersion, fingerprint
from frontier_portfolio_iq.domain import LOB, BudgetLine, CapacityPool, CapacityType, PortfolioData, Project, Settings
from frontier_portfolio_iq.pareto import (Evidence, Frontier, IncompatibleScopeError, ParetoConfig, ParetoEngine,
                                          PortfolioAlternative, compile_expression, dominates, nondominated_sort,
                                          objective_set, tolerances)
from frontier_portfolio_iq.pareto.frontier import to_max
from frontier_portfolio_iq.pareto.objectives import ObjectiveSpec

TOL = {"a": 1e-6, "b": 1e-6}


def test_dominance_definition():
    assert dominates({"a": 2, "b": 2}, {"a": 1, "b": 2}, TOL)          # better on one, equal on other
    assert not dominates({"a": 2, "b": 2}, {"a": 2, "b": 2}, TOL)      # equal: no strict improvement
    assert not dominates({"a": 3, "b": 1}, {"a": 1, "b": 3}, TOL)      # trade-off
    # tolerance-aware: a difference inside the tolerance is not "strictly better"
    assert not dominates({"a": 1 + 1e-9, "b": 1}, {"a": 1, "b": 1}, {"a": 1e-6, "b": 1e-6})


def test_nondominated_sort_ranks():
    v = [{"a": 3, "b": 1}, {"a": 1, "b": 3}, {"a": 2, "b": 2}, {"a": 1, "b": 1}, {"a": 0, "b": 0}]
    assert nondominated_sort(v, TOL) == [1, 1, 1, 2, 3]


def _alt(aid, starts, objs, version, keys=("a", "b"), validated=True, certified=True):
    return PortfolioAlternative(aid, "test", version, keys, starts, objs,
                                Evidence("x", "optimal", "optimal", 0.0, None, validated, certified=certified))


def test_frontier_duplicates_scope_and_labels():
    v = ModelVersion("f1", "baseline")
    f = Frontier(v, ("a", "b"), {"a": 1.0, "b": -1.0})                # a maximized, b minimized
    f.add(_alt("P1", {"X": 1}, {"a": 10, "b": 5}, v))
    same = f.add(_alt("P1b", {"X": 1}, {"a": 10, "b": 5}, v))          # identical plan -> merged
    assert same.alt_id == "P1" and len(f.alternatives) == 1
    f.add(_alt("P2", {"Y": 1}, {"a": 10 + 1e-9, "b": 5}, v))          # near-duplicate objective vector
    f.add(_alt("P3", {"Z": 1}, {"a": 8, "b": 6}, v))                   # dominated (less a, more b)
    f.add(_alt("P4", {"W": 1}, {"a": 12, "b": 9}, v))                  # trade-off
    f.add(_alt("P5", {"V": 1}, {"a": 99, "b": 0}, v, validated=False))  # infeasible plans never rank
    f.rank()
    st = {a.alt_id: a.status for a in f.alternatives}
    assert st == {"P1": "efficient", "P2": "duplicate", "P3": "dominated", "P4": "efficient", "P5": "infeasible"}
    assert "P1" in f.get("P3").dominated_by
    with pytest.raises(IncompatibleScopeError):
        f.add(_alt("Q", {"X": 2}, {"a": 1, "b": 1}, ModelVersion("other", "baseline")))
    with pytest.raises(IncompatibleScopeError):
        f.add(_alt("Q", {"X": 2}, {"a": 1, "b": 1}, ModelVersion("f1", "adverse")))
    with pytest.raises(IncompatibleScopeError):
        f.add(_alt("Q", {"X": 2}, {"a": 1}, v, keys=("a",)))


def test_custom_objective_expressions_are_safe():
    p = Project("P", "P", "L", value=200, execution_risk=0.5, mandatory=True, attributes={"nps": 4})
    assert compile_expression("value / 100 + attributes['nps'] * 2")(p) == pytest.approx(10)
    assert compile_expression("max(1, execution_risk * 10) + mandatory")(p) == pytest.approx(6)
    for bad in ("__import__('os')", "open('x')", "value.__class__", "lambda: 1"):
        with pytest.raises(ValueError):
            compile_expression(bad)


def test_every_objective_linearization_matches_its_evaluation(sample):
    """The MILP expression and the solved-plan evaluation of each objective must agree."""
    from frontier_portfolio_iq.optimizer import SolveOptions
    from frontier_portfolio_iq.solvers import SolverSettings, compile_portfolio, to_portfolio_result
    from frontier_portfolio_iq.solvers.milp import HighsEngine
    import pulp
    objs = objective_set(sample)
    holder = {}

    def customize(model, prob):
        holder["expr"] = {k: o.linear(model, prob) for k, o in objs.impl.items()}
    comp = compile_portfolio(sample, SolveOptions(), customize)
    nres = HighsEngine().solve(comp, SolverSettings(time_limit=30))
    res = to_portfolio_result(comp, nres)
    vals = objs.evaluate(res)
    for k, e in holder["expr"].items():
        if k in ("okr", "overload", "diversification"):
            # auxiliary-variable objectives: the LP value is a valid bound; the evaluation is exact
            continue
        e = pulp.LpAffineExpression(e)
        lin = sum(c * nres.values[v.name] for v, c in e.items()) + (e.constant or 0)
        assert lin == pytest.approx(vals[k], rel=1e-6, abs=1e-6), k


@pytest.fixture(scope="module")
def engine(sample):
    eng = ParetoEngine(sample, objective_set(sample), ParetoConfig(grid_points=3, refine_rounds=1,
                                                                   weighted_step=0.5, max_points=20))
    eng.run()
    return eng


def test_frontier_alternatives_are_feasible_and_efficient_set_is_mutually_nondominated(engine):
    f = engine.frontier
    eff = f.efficient()
    assert len(eff) >= 4
    assert all(a.evidence.validated for a in f.alternatives)
    vecs = [to_max(a.objectives, f.signs) for a in eff]
    tol = tolerances([to_max(a.objectives, f.signs) for a in f.alternatives if a.evidence.validated], f.objective_keys)
    for i, a in enumerate(vecs):
        for j, b in enumerate(vecs):
            assert i == j or not dominates(a, b, tol)
    for a in f.alternatives:                       # every dominated plan names a dominator that exists
        if a.status == "dominated":
            assert a.dominated_by and all(any(x.alt_id == d for x in f.alternatives) for d in a.dominated_by)


def test_payoff_table_and_ideal_nadir(engine):
    f = engine.frontier
    for k in engine.cfg.keys:
        sign = f.signs[k]
        assert sign * f.ideal[k] >= sign * f.nadir[k]
        assert k in f.payoff


def test_weighted_sum_points_are_not_dominated_by_epsilon_points_beyond_gap(engine):
    f = engine.frontier
    gap = engine.cfg.mip_gap
    eps = [a for a in f.alternatives if a.method.startswith("epsilon") and a.evidence.validated]
    for w in [a for a in f.alternatives if a.method == "weighted"]:
        vw = to_max(w.objectives, f.signs)
        for e in eps:
            ve = to_max(e.objectives, f.signs)
            # e "dominates beyond the gap" only if it is no worse on every objective (exactly, up to rounding)
            # and better than w by more than the solver's gap on at least one
            no_worse = all(ve[k] >= vw[k] - 1e-6 * max(1.0, abs(vw[k])) for k in f.objective_keys)
            better = any(ve[k] > vw[k] + gap * max(1.0, abs(vw[k])) for k in f.objective_keys)
            assert not (no_worse and better), (w.alt_id, e.alt_id)


def test_infeasible_epsilon_combinations_are_recorded(engine):
    for rec in engine.frontier.infeasible:
        assert rec["status"] in ("infeasible", "timeout", "error") and ("epsilon" in rec or "weights" in rec)


def test_reproducible_runs(sample):
    cfg = ParetoConfig(grid_points=2, refine_rounds=0, weighted_step=0, max_points=8)
    a = ParetoEngine(sample, objective_set(sample), cfg).run()
    b = ParetoEngine(sample, objective_set(sample), cfg).run()
    assert [x.alt_id for x in a.alternatives] == [x.alt_id for x in b.alternatives]


# --------------------------------------------------------------------------- non-convex discrete frontier
def _knapsack() -> PortfolioData:
    """Three independent initiatives, ample capacity. Plans (value, cost): {} (0,0), P2 (4,6), P1 (10,10),
    P1+P2 (14,16), ... P2 is efficient (nothing costs <= 6 and is worth >= 4) but lies *below* the
    segment from {} to P1, so no weighted sum of value and cost selects it: an unsupported point."""
    s = Settings(horizon=2, schedule_discount=0.0, protect_critical=False, mip_gap=1e-9, time_limit=10)
    lobs = {"L": LOB("L", "L")}
    projects = {
        "P1": Project("P1", "P1", "L", value=10, duration=1, demand={("C", 1): 1}, cost={1: 10}),
        "P2": Project("P2", "P2", "L", value=4, duration=1, demand={("C", 1): 1}, cost={1: 6}),
    }
    pools = {"POOL": CapacityPool("POOL", "C", "L", CapacityType.SHARED, 1, None, 1.0, 1.0, 100.0)}
    budgets = [BudgetLine("L", t, "Opex", 100.0) for t in (1, 2)]
    return PortfolioData(lobs, projects, pools, [], [], budgets, [], [], [], s)


def test_non_convex_frontier_epsilon_finds_what_weighted_sum_cannot():
    d = _knapsack()
    objs = objective_set(d, enabled=("value", "cost"))
    cfg = ParetoConfig(primary="value", secondary=("cost",), grid_points=9, refine_rounds=2, weighted_step=0.05,
                       weighted_keys=("value", "cost"), mip_gap=1e-9, max_points=60)
    eng = ParetoEngine(d, objs, cfg)
    eng.payoff_table()
    eng.run_epsilon()
    eps_plans = {tuple(sorted(a.starts)) for a in eng.frontier.alternatives if a.status == "efficient"}
    weighted = eng.run_weighted()
    w_plans = {tuple(sorted(a.starts)) for a in weighted}
    assert ("P2",) in eps_plans                        # epsilon-constraint reaches the unsupported plan
    assert ("P2",) not in w_plans                      # no weight vector selects it
    assert w_plans <= {(), ("P1",), ("P1", "P2")}      # weighted sums only reach supported plans
