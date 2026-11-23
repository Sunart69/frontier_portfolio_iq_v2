"""Monte Carlo: sampling, reproducibility, correlation, dependencies, capacity, policies, risk statistics."""
import dataclasses

import numpy as np
import pytest

from frontier_portfolio_iq.domain import (LOB, BudgetLine, CapacityPool, CapacityType, PortfolioData, Project,
                                          Settings)
from frontier_portfolio_iq.risk import (Dist, SimulationSettings, default_uncertainty, evaluate_plan, parse_dist,
                                        plan_context, simulate)
from frontier_portfolio_iq.risk.correlation import (CorrelationError, build_matrix, gaussian_to_spearman,
                                                    nearest_correlation, spearman, validate_correlation)
from frontier_portfolio_iq.risk.metrics import batch_se, var_cvar
from frontier_portfolio_iq.risk.simulate import sample_drivers
from frontier_portfolio_iq.risk.uncertainty import (PoolUncertainty, ProjectUncertainty, RiskDriver,
                                                    UncertaintyModel)

U = np.random.default_rng(1).random(200_000)


@pytest.mark.parametrize("text,mean", [
    ("tri(1, 2, 6)", 3.0), ("pert(0.9, 1.0, 1.5)", (0.9 + 4 + 1.5) / 6), ("normal(10, 2)", 10.0),
    ("uniform(2, 4)", 3.0), ("lognormal(1.0, 0.2)", np.exp(0.02)), ("bernoulli(0.3)", 0.3),
    ("discrete(0:0.5, 1:0.3, 3:0.2)", 0.9), ("const(7)", 7.0),
    ("truncnormal(1, 0.5, 0.5, 2)", None), ("empirical(1, 2, 3, 4, 10)", None)])
def test_distribution_sampling_matches_theory(text, mean):
    d = parse_dist(text)
    x = d.ppf(U)
    m = d.mean() if mean is None else mean
    assert d.mean() == pytest.approx(m, rel=1e-3) if mean is not None else True
    assert x.mean() == pytest.approx(m, rel=0.01, abs=0.01)
    if d.kind in ("tri", "pert", "uniform", "truncnormal"):
        lo, hi = (d.params[2], d.params[3]) if d.kind == "truncnormal" else (d.params[0], d.params[-1] if d.kind != "pert" else d.params[2])
        assert x.min() >= lo - 1e-9 and x.max() <= hi + 1e-9
    if d.kind == "discrete":
        for v, p in zip(d.values, d.probs):
            assert (x == v).mean() == pytest.approx(p, abs=0.005)


def test_distribution_validation_and_no_normal_default():
    for bad in ("tri(3, 2, 1)", "pert(1, 1, 1)", "normal(0, -1)", "bernoulli(2)", "discrete(0:0.5, 1:0.4)",
                "lognormal(-1, 0.2)", "weibull(1, 2)"):
        with pytest.raises(ValueError):
            parse_dist(bad)
    um_kinds = {u.effort.kind for u in default_uncertainty(_line()).projects.values()}
    assert "normal" not in um_kinds


def test_correlation_matrix_validation_and_repair():
    C = build_matrix(["a", "b", "c"], {("a", "b"): 0.9, ("b", "c"): 0.9, ("a", "c"): -0.9})
    issues = validate_correlation(C)
    assert issues and "positive semi-definite" in issues[0]
    R = nearest_correlation(C)
    assert not validate_correlation(R) and np.allclose(np.diag(R), 1)
    with pytest.raises(CorrelationError):
        build_matrix(["a"], {("a", "zz"): 0.2})


def test_correlation_is_preserved_through_the_copula():
    data = _line()
    drivers = [RiskDriver("D1", "d1", "x", Dist("pert", (0.8, 1.0, 1.3)), "cost"),
               RiskDriver("D2", "d2", "x", Dist("lognormal", (1.0, 0.1)), "cost"),
               RiskDriver("D3", "d3", "x", Dist("uniform", (0.0, 1.0)), "cost")]
    um = default_uncertainty(data)
    um = dataclasses.replace(um, drivers=drivers, correlations={("D1", "D2"): 0.6, ("D2", "D3"): -0.4})
    s = sample_drivers(um, 7, 0, 40_000)
    assert spearman(s["D1"], s["D2"]) == pytest.approx(gaussian_to_spearman(0.6), abs=0.02)
    assert spearman(s["D2"], s["D3"]) == pytest.approx(gaussian_to_spearman(-0.4), abs=0.02)
    assert abs(spearman(s["D1"], s["D3"])) < 0.03
    um_bad = dataclasses.replace(um, correlations={("D1", "D2"): 0.9, ("D2", "D3"): 0.9, ("D1", "D3"): -0.9})
    with pytest.raises(CorrelationError):
        um_bad.correlation_matrix()


# --------------------------------------------------------------------------- a hand-checkable line portfolio
def _line(cap_fte=1.0, n_months=12) -> PortfolioData:
    """P1 -> P2 dependency; one shared pool of 100 h/month; each initiative needs 50 h/month for 3 months."""
    s = Settings(horizon=n_months, schedule_discount=0.0, protect_critical=False, discount_rate=0.0)
    lobs = {"L": LOB("L", "L")}
    mk = lambda pid, earliest, deps=(): Project(pid, pid, "L", value=100.0, duration=3, earliest_start=earliest,
                                                 demand={("C", k): 50.0 for k in (1, 2, 3)},
                                                 cost={k: 10.0 for k in (1, 2, 3)}, depends_on=list(deps))
    projects = {"P1": mk("P1", 1), "P2": mk("P2", 4, ["P1"])}
    pools = {"POOL": CapacityPool("POOL", "C", "L", CapacityType.SHARED, 1, None, cap_fte, 1.0, 100.0)}
    budgets = [BudgetLine("L", t, "Opex", 100.0) for t in range(1, n_months + 1)]
    return PortfolioData(lobs, projects, pools, [], [], budgets, [], [], [], s)


def _certain(data, **proj) -> UncertaintyModel:
    one = Dist("const", (1.0,))
    zero = Dist("const", (0.0,))
    return UncertaintyModel(
        {i: ProjectUncertainty(proj.get("effort", one), proj.get("duration", one), one, zero, 0.0)
         for i in data.projects},
        {k: PoolUncertainty(proj.get("availability", one), zero, one) for k in data.pools},
        [], {}, SimulationSettings(iterations=200, chunk_size=100, date_grace_months=0), ["test"])


def test_deterministic_replay_reproduces_the_plan():
    d = _line()
    s, run = evaluate_plan(d, {"P1": 1, "P2": 4}, _certain(d))
    f = run.samples
    assert np.all(f["pfin|P1"] == 3) and np.all(f["pfin|P2"] == 6)
    assert s["npv"]["mean"] == pytest.approx(s["reference"]["npv"])
    assert s["probabilities"]["capability_shortage"]["p"] == 0 and s["probabilities"]["meet_key_dates"]["p"] == 1


def test_dependency_delay_propagates():
    d = _line()
    um = _certain(d)
    um.projects["P1"] = dataclasses.replace(um.projects["P1"], duration=Dist("const", (2.0,)))   # P1 takes 6 months
    _s, run = evaluate_plan(d, {"P1": 1, "P2": 4}, um)
    f = run.samples
    assert np.all(f["pfin|P1"] == 6)
    assert np.all(f["pfin|P2"] >= f["pfin|P1"] + 3)     # P2 cannot start before P1 finishes


def test_monthly_capacity_limits_progress():
    d = _line(cap_fte=0.25)                             # 25 h/month for a 50 h/month need
    s, run = evaluate_plan(d, {"P1": 1, "P2": 4}, _certain(d))
    f = run.samples
    assert np.all(f["pfin|P1"] == 6)                    # half the capacity -> twice the duration
    assert s["probabilities"]["capability_shortage"]["p"] == 1.0
    assert s["expected_shortage_hours"]["C"] > 0


def test_ring_fenced_capacity_is_not_borrowed_without_a_rule(tiny_data):
    um = default_uncertainty(tiny_data).with_settings(iterations=200, chunk_size=100)
    s, run = evaluate_plan(tiny_data, {"B1": 1}, um)
    assert not np.isfinite(run.samples["pfin|B1"]).any()   # LOB-B has no route to LOB-A's ring-fenced pool
    assert s["probabilities"]["capability_shortage"]["p"] == 1.0


def test_fixed_vs_adaptive_execution():
    d = _line(cap_fte=0.6)
    um = _certain(d, availability=Dist("pert", (0.5, 0.8, 1.0))).with_settings(
        iterations=500, chunk_size=250, contingency_pct=0.5, contingency_rate=100.0)
    fx, _ = evaluate_plan(d, {"P1": 1, "P2": 4}, um, mode="fixed")
    ad, _ = evaluate_plan(d, {"P1": 1, "P2": 4}, um, mode="adaptive")
    assert fx["contingency_hours"] == 0 and fx["deferred_months"] == 0
    assert ad["contingency_hours"] > 0
    assert ad["completion"]["mean"] < fx["completion"]["mean"]       # recourse recovers schedule ...
    assert ad["cost"]["mean"] > fx["cost"]["mean"]                     # ... at a price, never for free


def test_reproducible_and_worker_independent(sample):
    from frontier_portfolio_iq.solvers import solve_validated
    res, _ = solve_validated(sample, validators=())
    um = default_uncertainty(sample).with_settings(iterations=1000, chunk_size=250)
    a, ra = evaluate_plan(sample, res.starts, um, result=res)
    b, rb = evaluate_plan(sample, res.starts, um.with_settings(workers=2), result=res)
    assert np.array_equal(ra.samples["npv"], rb.samples["npv"])
    c, rc = evaluate_plan(sample, res.starts, um.with_settings(seed=99), result=res)
    assert not np.array_equal(ra.samples["npv"], rc.samples["npv"])


def test_common_random_numbers_across_portfolios(sample):
    """An initiative's draws do not depend on which other initiatives a portfolio selects."""
    from frontier_portfolio_iq.solvers import solve_validated
    res, _ = solve_validated(sample, validators=())
    um = default_uncertainty(sample).with_settings(iterations=400, chunk_size=200)
    full = res.starts
    some = dict(list(full.items())[:5])
    _a, ra = evaluate_plan(sample, full, um, result=res)
    _b, rb = evaluate_plan(sample, some, um)
    for i in some:
        assert np.array_equal(ra.samples[f"in|effort|{i}"], rb.samples[f"in|effort|{i}"])
        assert np.array_equal(ra.samples[f"in|benefit|{i}"], rb.samples[f"in|benefit|{i}"])


def test_percentiles_var_cvar_and_convergence():
    x = np.arange(1, 1001, dtype=float)
    vc = var_cvar(x, 0.95)
    assert vc["var"] == pytest.approx(np.quantile(x, 0.95))
    assert vc["cvar"] == pytest.approx(x[x >= vc["var"]].mean())
    d = _line()
    um = _certain(d, effort=Dist("pert", (0.8, 1.0, 1.6))).with_settings(iterations=4000, chunk_size=1000)
    s, run = evaluate_plan(d, {"P1": 1, "P2": 4}, um)
    npv = run.samples["npv"]
    assert s["npv"]["p10"] == pytest.approx(np.percentile(npv, 10))
    assert s["npv"]["p90"] == pytest.approx(np.percentile(npv, 90))
    assert s["npv"]["p10"] <= s["npv"]["p50"] <= s["npv"]["p90"]
    assert s["cost"]["downside"] == "p90" and s["npv"]["downside"] == "p10"
    lo, hi = s["npv"]["p50_ci"]
    assert lo <= s["npv"]["p50"] <= hi
    tr = s["convergence"]["trace"]
    assert tr[-1]["se"] < tr[0]["se"]                 # sampling error shrinks with iterations
    assert batch_se(npv) > 0 and s["convergence"]["iterations"] == 4000


def test_defaults_are_flagged_uncalibrated(sample):
    um = default_uncertainty(sample)
    assert not um.calibrated and any("demonstration" in a for a in um.assumptions)
    assert {d.category for d in um.drivers} >= {"inflation", "capability", "regulatory"}


def test_intake_sheets_round_trip(sample, tmp_path):
    from frontier_portfolio_iq.intake import read_workbook, write_workbook
    from frontier_portfolio_iq.risk import read_uncertainty
    um = default_uncertainty(sample)
    p = str(tmp_path / "intake.xlsx")
    write_workbook(sample, p, uncertainty=um)
    d2 = read_workbook(p)
    um2 = read_uncertainty(p, d2)
    assert not um2.calibrated                         # default rows keep their 'demonstration default' note
    assert [x.driver_id for x in um2.drivers] == [x.driver_id for x in um.drivers]
    assert um2.projects["A01"].effort.describe() == um.projects["A01"].effort.describe()
    # an intake-supplied row becomes calibrated input
    from openpyxl import load_workbook
    wb = load_workbook(p)
    ws = wb["Uncertainty Projects"]
    ws.cell(row=2, column=2, value="tri(0.95, 1.0, 1.2)")
    ws.cell(row=2, column=8, value="vendor history 2024-26")
    wb.save(p)
    um3 = read_uncertainty(p, read_workbook(p))
    first = ws.cell(row=2, column=1).value
    assert um3.projects[first].source == "intake" and um3.projects[first].effort.kind == "tri"
