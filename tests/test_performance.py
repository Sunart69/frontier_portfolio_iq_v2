"""Scale: the synthetic generator, solver behaviour at size, and the measured benchmark report.

The full grid (25-500 initiatives x 12-36 months) is run by `python -m frontier_portfolio_iq.benchmark`
and its measured results are committed in docs/PERFORMANCE.md; these tests run the small end of the
grid and check the report covers every case. They assert behaviour, not timings."""
import json
import os
import time

import pytest

from frontier_portfolio_iq.benchmark import generate_portfolio, run_case
from frontier_portfolio_iq.intake import validate

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.mark.parametrize("n,h", [(25, 12), (100, 12), (250, 24), (500, 36)])
def test_generated_portfolios_are_valid_intakes(n, h):
    d = generate_portfolio(n, h)
    rep = validate(d)
    assert rep.ok, rep.errors[:5]
    assert len(d.projects) == n and d.settings.horizon == h
    assert any(p.mandatory for p in d.projects.values())
    ids = list(d.projects)
    for p in d.projects.values():           # dependencies are acyclic (on earlier initiatives) and resolvable
        assert all(q in d.projects and ids.index(q) < ids.index(p.project_id) for q in p.depends_on)
    if h >= 24:                             # at 12 months few chains fit; the generator only adds those that do
        assert any(p.depends_on for p in d.projects.values())


@pytest.mark.parametrize("n,h", [(25, 12), (25, 24)])
def test_small_end_of_the_grid_solves_validates_and_simulates(n, h):
    t0 = time.time()
    r = run_case(n, h, time_limit=30, iterations=500)
    assert r["HiGHS"]["status"] in ("optimal", "feasible") and r["HiGHS"]["validated"]
    assert r["SCIP"]["status"] in ("optimal", "feasible") and r["SCIP"]["validated"]
    assert r["CP-SAT"]["status"] in ("optimal", "feasible", "timeout", "incompatible")
    assert r["monte_carlo"]["iterations"] == 500 and r["monte_carlo"]["throughput_per_s"] > 0
    assert r["peak_rss_mb"] > 0 and time.time() - t0 < 600


def test_measured_report_covers_the_full_grid():
    with open(os.path.join(ROOT, "docs", "benchmark_results.json")) as fh:
        doc = json.load(fh)
    cases = {(r["projects"], r["horizon"]) for r in doc["results"] if "error" not in r}
    assert cases == {(n, h) for n in (25, 100, 250, 500) for h in (12, 24, 36)}
    for r in doc["results"]:
        assert r["HiGHS"]["status"] in ("optimal", "feasible") and r["HiGHS"]["validated"]
        assert "gap" in r["HiGHS"] and r["monte_carlo"]["throughput_per_s"] > 0
    text = open(os.path.join(ROOT, "docs", "PERFORMANCE.md")).read()
    assert "measured" in text.lower() and text.count("\n| ") >= 13
