"""Multi-solver layer: equivalent formulations, independent validation, comparisons, timeouts, fallback."""
import dataclasses

import pytest

from frontier_portfolio_iq.optimizer import SolveOptions
from frontier_portfolio_iq.domain import PortfolioTier
from frontier_portfolio_iq.solvers import (NormalizedResult, SolverSettings, SolveStatus, compile_portfolio,
                                           engine_inventory, make_engine, solve_validated, to_portfolio_result,
                                           validate_result, validate_solution)
from frontier_portfolio_iq.solvers.cpsat import compatibility
from frontier_portfolio_iq.solvers.milp import HighsEngine
from frontier_portfolio_iq.solvers.orchestrate import EngineRun, compare_runs


@pytest.fixture(scope="module")
def compiled(sample):
    return compile_portfolio(sample, SolveOptions())


@pytest.fixture(scope="module")
def highs(compiled):
    return HighsEngine().solve(compiled, SolverSettings(time_limit=30))


def test_required_engines_installed():
    inv = {e["engine"]: e for e in engine_inventory()}
    for name in ("HiGHS", "SCIP", "CP-SAT"):
        assert inv[name]["available"] and inv[name]["requirement"] == "required", name


def test_compiled_matrix_matches_pulp_objective(compiled, highs):
    # the matrix objective of HiGHS's own solution equals the objective HiGHS reports
    assert compiled.matrix.objective_of(highs.values) == pytest.approx(highs.objective, rel=1e-9)
    v = compiled.matrix.max_violation(highs.values)
    assert v["rows"] < 1e-5 and v["bounds"] < 1e-6 and v["integrality"] < 1e-6


def test_highs_and_scip_agree_on_identical_formulation(compiled, highs):
    scip = make_engine("SCIP").solve(compiled, SolverSettings(time_limit=60))
    assert highs.status.has_solution and scip.status.has_solution
    gap = max(highs.gap or 0, scip.gap or 0, 0.01)
    assert abs(highs.objective - scip.objective) <= gap * abs(highs.objective) + 1e-6
    # neither engine's plan is worth more than the other's proven bound
    assert highs.objective <= scip.bound + 1e-6 * abs(scip.bound) + 1e-3
    assert scip.objective <= highs.bound + 1e-6 * abs(highs.bound) + 1e-3


def test_cpsat_reformulation_is_conservative_and_validated(compiled, sample):
    ok, why = compatibility(compiled)
    assert ok, why
    cp = make_engine("CP-SAT").solve(compiled, SolverSettings(time_limit=30))
    assert cp.status.has_solution
    res = to_portfolio_result(compiled, cp)
    assert validate_result(res).feasible          # integer plan is feasible for the continuous model
    assert cp.extra["bound_note"].startswith("bound from integer-scaled")


def test_cpsat_reports_incompatible_formulations(sample):
    soft = compile_portfolio(sample, SolveOptions(soft=True))
    assert not compatibility(soft)[0]
    bal = compile_portfolio(sample, SolveOptions(objective="balanced"))
    r = make_engine("CP-SAT").solve(bal, SolverSettings(time_limit=5))
    assert r.status == SolveStatus.INCOMPATIBLE and "objective" in r.message


def test_validator_accepts_solver_plan_and_rejects_tampering(compiled, highs):
    res = to_portfolio_result(compiled, highs)
    rep = validate_result(res)
    assert rep.feasible and rep.objective_consistent
    # 1. over-allocate a pool
    a = res.allocations[0]
    bad = [dataclasses.replace(a, hours=a.hours + 10_000, effective_hours=(a.hours + 10_000) * (a.effective_hours / a.hours))] \
        + res.allocations[1:]
    r1 = validate_solution(res.data, res.options, res.starts, bad, res.objective)
    assert not r1.feasible and any(f.startswith("capacity") for f in r1.failures())
    # 2. drop a mandatory initiative
    must = next(i for i, p in res.data.projects.items() if p.mandatory or p.critical)
    starts = {i: s for i, s in res.starts.items() if i != must}
    r2 = validate_solution(res.data, res.options, starts, [x for x in res.allocations if x.project_id != must])
    assert not r2.feasible and any("commitment" in f for f in r2.failures())
    # 3. route ring-fenced hours to another LOB through a pending rule (committed tier)
    pending = next(r for r in res.data.transfers if not r.is_authorized() and r.dest_lob != "*")
    pool = res.data.pools[pending.source_pool]
    month = pending.effective_start
    fake = dataclasses.replace(a, pool_id=pending.source_pool, project_id="B04", capability=pool.capability,
                               month=month, hours=10.0, effective_hours=10.0 * pending.efficiency, kind="transfer",
                               rule_id=pending.rule_id, lob="LOB-B")
    r3 = validate_solution(res.data, res.options, res.starts, res.allocations + [fake])
    assert not r3.feasible and any("not allowed in the Committed tier" in f for f in r3.failures())
    # 4. misreported objective
    r4 = validate_solution(res.data, res.options, res.starts, res.allocations, res.objective + 50)
    assert r4.feasible and r4.objective_consistent is False


def _run(engine, status, obj=None, bound=None, gap=None):
    return EngineRun(NormalizedResult(engine, status, obj, bound, gap, values={}))


def test_comparisons_flag_disagreements_without_rejecting():
    a = _run("HiGHS", SolveStatus.OPTIMAL, 100.0, 100.5, 0.005)
    b = _run("SCIP", SolveStatus.OPTIMAL, 100.2, 100.4, 0.002)
    assert compare_runs(a, b, 0.01)["consistent"]
    c = _run("SCIP", SolveStatus.OPTIMAL, 130.0, 130.0, 0.0)        # beats HiGHS's proven bound
    cmp = compare_runs(a, c, 0.01)
    assert not cmp["consistent"] and any("exceeds HiGHS bound" in p for p in cmp["problems"])
    d = _run("SCIP", SolveStatus.TIMEOUT)
    assert compare_runs(a, d, 0.01)["consistent"]                     # not comparable, not a disagreement


def test_proof_levels_distinguish_proven_from_unproven():
    assert NormalizedResult("x", SolveStatus.OPTIMAL, 10, 10, 0.0).proof == "optimal"
    assert NormalizedResult("x", SolveStatus.OPTIMAL, 10, 10.05, 0.005, requested_gap=0.01).proof == "within_gap"
    assert NormalizedResult("x", SolveStatus.FEASIBLE, 10, 12, 0.2).proof == "feasible"
    assert NormalizedResult("x", SolveStatus.TIMEOUT).proof == "none"


def test_timeouts_and_failures_are_contained(sample, compiled, monkeypatch):
    # an engine exception becomes an ERROR result, never an exception
    monkeypatch.setattr(HighsEngine, "_solve", lambda self, c, s: (_ for _ in ()).throw(RuntimeError("boom")))
    r = HighsEngine().solve(compiled, SolverSettings())
    assert r.status == SolveStatus.ERROR and "boom" in r.message
    # ... and the orchestrator falls back to SCIP, records it, and still validates the plan
    res, rep = solve_validated(sample, SolveOptions(), SolverSettings(time_limit=60), validators=())
    assert res.feasible and rep.chosen_engine == "SCIP"
    assert any("fell back to SCIP" in d for d in rep.diagnostics)


def test_tiny_time_limit_yields_feasible_or_timeout_never_crash(compiled):
    r = make_engine("SCIP").solve(compiled, SolverSettings(time_limit=0.05))
    assert r.status in (SolveStatus.FEASIBLE, SolveStatus.TIMEOUT, SolveStatus.OPTIMAL)
    if r.status == SolveStatus.FEASIBLE:
        assert r.proof == "feasible"


def test_confidence_report_end_to_end(sample):
    res, rep = solve_validated(sample, SolveOptions(), SolverSettings(time_limit=30), validators=("SCIP", "CP-SAT"))
    assert res.feasible and rep.level == "CONFIRMED_OPTIMAL"
    assert rep.certified_gap is not None and rep.certified_gap <= 0.011
    assert all(r.validation.feasible for r in rep.runs if r.validation is not None)
    md = rep.markdown()
    assert "| HiGHS | primary |" in md and "SCIP" in md and "CP-SAT" in md


def test_infeasible_model_is_reported_with_diagnostics(sample):
    d = sample.copy()
    for b in d.budgets:
        b.amount *= 0.05
    res, rep = solve_validated(d, SolveOptions(), SolverSettings(time_limit=10), validators=("SCIP",))
    assert not res.feasible and rep.level in ("INFEASIBLE", "DISAGREEMENT")
    assert res.diagnostics


def test_osqp_leveling_keeps_plan_and_validates(sample, highs, compiled):
    res = to_portfolio_result(compiled, highs)
    fixed = compile_portfolio(sample, SolveOptions(fixed_starts=dict(res.starts)))
    q = make_engine("OSQP").solve(fixed, SolverSettings(time_limit=30))
    if q.status == SolveStatus.UNAVAILABLE:
        pytest.skip("osqp not installed")
    assert q.status == SolveStatus.OPTIMAL
    lv = to_portfolio_result(fixed, q)
    assert lv.starts == res.starts
    rep = validate_result(lv, check_objective=False)
    assert rep.feasible or all(c.worst < 1e-2 for c in rep.checks if not c.passed)
