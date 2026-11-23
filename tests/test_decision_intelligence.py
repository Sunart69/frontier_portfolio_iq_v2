"""End-to-end v2: intake -> validated optimization -> Pareto -> Monte Carlo -> recommendation -> what-if ->
dashboard and CEO deck, all on one model version."""
import json
import os
import re

import pytest

from frontier_portfolio_iq.canonical import fingerprint
from frontier_portfolio_iq.decision import (PROFILES, RULE_SETS, Candidate, Constraint, Profile, rank,
                                            strategic_choices)

pytestmark = pytest.mark.integration


def test_excel_intake_through_validated_optimization(sample, tmp_path):
    from frontier_portfolio_iq.intake import read_workbook, write_workbook
    from frontier_portfolio_iq.risk import default_uncertainty, read_uncertainty
    from frontier_portfolio_iq.solvers import solve_validated
    p = str(tmp_path / "intake.xlsx")
    write_workbook(sample, p, uncertainty=default_uncertainty(sample))
    d = read_workbook(p)
    assert fingerprint(d) == fingerprint(sample)          # intake round trip preserves the canonical model
    res, rep = solve_validated(d, validators=("SCIP",))
    assert res.feasible and rep.level in ("CONFIRMED_OPTIMAL", "PROVEN_SINGLE")
    assert read_uncertainty(p, d).settings.iterations == 10000


def test_single_model_version_across_the_pipeline(di_bundle):
    b = di_bundle
    assert b.consistency() == []
    assert all(a.version == b.version for a in b.frontier.alternatives)
    assert b.version.fingerprint == fingerprint(b.data, b.uncertainty)


def test_frontier_alternatives_come_from_feasible_plans(di_bundle):
    f = di_bundle.frontier
    assert len(f.efficient()) >= 4
    for a in f.alternatives:
        if a.status in ("efficient", "dominated"):
            assert a.evidence.validated and a.selected
    # a plan found by several methods is stored once; the others are recorded as aliases
    methods = {a.method for a in f.alternatives} | {x.split(":", 1)[0] for a in f.alternatives for x in a.aliases}
    assert {"payoff", "epsilon", "baseline", "stochastic"} <= methods


def test_monte_carlo_on_shortlist_uses_common_settings(di_bundle):
    R = di_bundle.risk
    assert di_bundle.recommended_id in R and len(R) >= 4
    seeds = {r["seed"] for r in R.values()}
    its = {r["iterations"] for r in R.values()}
    assert len(seeds) == 1 and len(its) == 1
    for r in R.values():
        p = r["probabilities"]
        assert 0 <= p["within_budget"]["p"] <= 1 and r["npv"]["p10"] <= r["npv"]["p50"] <= r["npv"]["p90"]
        assert r["var_cvar"]["cvar"] >= r["var_cvar"]["var"] - 1e-9


def test_recommendation_is_traceable(di_bundle):
    for name, rk in di_bundle.rankings.items():
        for row in rk.rows:
            assert row["score"] == pytest.approx(sum(c["contribution"] for c in row["criteria"].values()))
            for c in row["criteria"].values():
                assert 0 <= c["normalized"] <= 1
        if rk.eligible:
            assert rk.recommended == rk.rows[0]["alt_id"] and rk.rows[0]["eligible"]
        assert rk.rationale
    bal = di_bundle.rankings["balanced"]
    best_value = max(di_bundle.risk[r["alt_id"]]["value"]["mean"] for r in bal.rows)
    rec_value = di_bundle.risk[bal.recommended]["value"]["mean"]
    assert rec_value >= PROFILES["balanced"].min_value_share * best_value - 1e-6


def test_strategic_choices_follow_their_rules(di_bundle):
    ch, R = di_bundle.choices, di_bundle.risk
    assert set(ch) == {"aggressive", "balanced", "conservative", "accelerated"}
    agg = ch["aggressive"]["alt_id"]
    assert R[agg]["npv"]["mean"] == max(r["npv"]["mean"] for r in R.values())
    floor = 0.7 * R[agg]["value"]["mean"]
    cons = ch["conservative"]["alt_id"]
    viable = [k for k, r in R.items() if r["value"]["mean"] >= floor]
    assert cons in viable and R[cons]["var_cvar"]["cvar"] == min(R[k]["var_cvar"]["cvar"] for k in viable)
    assert ch["balanced"]["alt_id"] == di_bundle.rankings["balanced"].recommended
    for v in ch.values():                                  # adaptive execution was simulated for each choice
        assert v["alt_id"] in di_bundle.risk_adaptive


def test_recommended_plan_independently_certified(di_bundle):
    rc = di_bundle.recommended_confidence
    assert rc is not None and rc.level in ("CONFIRMED_OPTIMAL", "PROVEN_SINGLE")
    assert all(r.validation.feasible for r in rc.runs if r.validation is not None)


def test_stochastic_plan_is_labelled_and_evaluated_out_of_sample(di_bundle):
    st = di_bundle.stochastic
    assert st["status"] in ("optimal", "feasible") and st["validated"] and "limitation" in st
    assert st["alt_id"] in di_bundle.risk                 # evaluated by the same Monte Carlo
    a = di_bundle.alt(st["alt_id"])
    assert a.method == "stochastic" or any(x.startswith("stochastic:") for x in a.aliases)


def test_what_if_scenarios_recompute_under_new_fingerprints(di_bundle):
    keys = {o.whatif.key for o in di_bundle.scenarios}
    assert keys == {"optimistic", "constrained", "adverse"}
    for o in di_bundle.scenarios:
        assert o.version.fingerprint != di_bundle.version.fingerprint and o.version.scenario == o.whatif.key
        if o.status == "ok":
            assert o.recommended["expected_npv"] is not None and o.baseline_plan is not None
    opt = next(o for o in di_bundle.scenarios if o.whatif.key == "optimistic")
    assert opt.recommended["expected_npv"] >= 0.9 * di_bundle.risk[di_bundle.recommended_id]["npv"]["mean"]


def test_cache_reuses_identical_scenarios(sample):
    from frontier_portfolio_iq.cache import ResultCache
    from frontier_portfolio_iq.intelligence import DecisionConfig, _run_whatif
    from frontier_portfolio_iq.pareto import ParetoConfig, objective_set
    from frontier_portfolio_iq.risk import default_uncertainty
    from frontier_portfolio_iq.whatif import WhatIf
    cfg = DecisionConfig(whatif_iterations=200, whatif_pareto=ParetoConfig(grid_points=2, refine_rounds=0,
                                                                          weighted_step=0, max_points=4))
    cache = ResultCache()
    um = default_uncertainty(sample)
    w = WhatIf("cut", "Cut", total_budget=0.95)
    a = _run_whatif(sample, um, w, cfg, cache, None, objective_set(sample))
    b = _run_whatif(sample, um, w, cfg, cache, None, objective_set(sample))
    assert cache.stats()["hits"] == 1 and a is b
    c = _run_whatif(sample, um, WhatIf("cut2", "Cut2", total_budget=0.9), cfg, cache, None, objective_set(sample))
    assert cache.stats()["misses"] == 2 and c.version.fingerprint != a.version.fingerprint


def test_what_if_levers_apply_exactly(sample):
    from frontier_portfolio_iq.risk import default_uncertainty
    from frontier_portfolio_iq.whatif import WhatIf
    w = WhatIf("x", "x", total_budget=0.9, lob_budget={"LOB-A": 0.5}, shared_capacity=1.2,
               capability_month=[("AI Engineering", 3, 5, 0.5)], start_windows={"A01": (2, 4)},
               mandatory_add=["B05"], cost_uncertainty=2.0, benefit_shift=-0.1)
    d, u = w.apply(sample, default_uncertainty(sample))
    b0 = sum(b.amount for b in sample.budgets if b.lob_id == "LOB-A")
    assert sum(b.amount for b in d.budgets if b.lob_id == "LOB-A") == pytest.approx(b0 * 0.45)
    ai = next(p for p in d.pools.values() if p.capability == "AI Engineering")
    ai0 = sample.pools[ai.pool_id]
    assert ai.available_hours(4) == pytest.approx(ai0.available_hours(4) * 0.5 * (1.2 if ai0.capacity_type.value == "Shared" else 1.0))
    assert ai.available_hours(6) == pytest.approx(ai0.available_hours(6) * (1.2 if ai0.capacity_type.value == "Shared" else 1.0))
    assert d.projects["A01"].earliest_start == 2 and d.projects["B05"].mandatory
    hi0 = default_uncertainty(sample).projects["A01"].effort.params[2]
    assert u.projects["A01"].effort.params[2] - 1.0 == pytest.approx((hi0 - 1.0) * 2.0)
    assert fingerprint(d) != fingerprint(sample)


def _cand(aid, npv, value, cvar, budget, dates=0.5, ttv=10.0):
    risk = {"npv": {"mean": npv, "p10": npv * 0.8}, "value": {"mean": value}, "var_cvar": {"cvar": cvar, "var": cvar},
            "probabilities": {k: {"p": v} for k, v in {"within_budget": budget, "meet_key_dates": dates,
                                                       "capability_shortage": 0.5, "min_acceptable_value": 0.9,
                                                       "okr_plan_targets": 0.5}.items()},
            "okr_targets": {}, "completion": {"mean": 12}}
    return Candidate(aid, aid, "test", {"alignment": 100, "time_to_value": ttv}, risk, {"lob_commitment": 1.0})


def test_profiles_constraints_and_value_floor():
    big = _cand("BIG", 100, 150, 40, 0.70)
    mid = _cand("MID", 90, 140, 20, 0.85)
    tiny = _cand("TINY", 10, 15, 1, 0.99, dates=0.99)
    neutral = rank([big, mid, tiny], PROFILES["risk_neutral"])
    assert neutral.recommended == "BIG"
    averse = rank([big, mid, tiny], PROFILES["risk_averse"])
    assert averse.recommended == "MID"                # TINY fails the 60%-of-best-value floor
    assert not next(r for r in averse.rows if r["alt_id"] == "TINY")["eligible"]
    ruled = rank([big, mid, tiny], Profile("r", {"expected_npv": 1.0}, 0.0, RULE_SETS["npv_budget80"]))
    assert ruled.recommended == "MID"                 # maximize E[NPV] s.t. P(within budget) >= 80%
    impossible = rank([big, mid], Profile("x", {"expected_npv": 1}, 0.0, [Constraint("within_budget", ">=", 0.99)]))
    assert impossible.note and impossible.recommended == "MID"   # closest to meeting the rule
    with pytest.raises(ValueError):
        Profile("bad", {"nonsense": 1}, 0.0)


def test_dashboard_is_built_from_live_bundle_data(di_bundle, tmp_path):
    from frontier_portfolio_iq.dashboard_v2 import build_decision_dashboard
    p = build_decision_dashboard(di_bundle, str(tmp_path / "d.html"))
    html = open(p).read()
    m = re.search(r"const D = (\{.*?\});\nconst \$", html, re.S)
    D = json.loads(m.group(1).replace("<\\/", "</"))
    assert D["model_version"] == di_bundle.version.label() and D["recommended"] == di_bundle.recommended_id
    r = di_bundle.risk[di_bundle.recommended_id]
    assert D["risk"][di_bundle.recommended_id]["npv"]["mean"] == pytest.approx(r["npv"]["mean"])
    assert D["solver_confidence"]["level"] == di_bundle.confidence.level
    assert len(D["frontier"]["alternatives"]) == len(di_bundle.frontier.alternatives)
    for label in ("PROVEN", "SIMULATED", "ESTIMATE"):
        assert label in html
    assert "NaN" not in json.dumps(D)
    assert D["radar"]["risk"][di_bundle.recommended_id]["budget_confidence"] == \
        pytest.approx(r["probabilities"]["within_budget"]["p"])
    for title in ("Risk trade-off radar", "Optimization KPI radar", "Risk appetite lens", "What-if radar",
                  "Fixed plan vs adaptive execution", "KPI shape of each strategic choice"):
        assert title in html


def test_ceo_deck_reconciles_with_bundle(di_bundle, tmp_path):
    from pptx import Presentation
    from frontier_portfolio_iq.deck_v2 import build_ceo_deck
    from frontier_portfolio_iq.style import money
    p = str(tmp_path / "ceo.pptx")
    m = build_ceo_deck(di_bundle, p)
    prs = Presentation(p)
    assert len(prs.slides) == 19                        # 12 storyline slides + appendices A1-A7
    r = di_bundle.risk[di_bundle.recommended_id]
    assert m["1"]["alt"] == di_bundle.recommended_id and m["1"]["expected_npv"] == pytest.approx(r["npv"]["mean"])
    s1 = " ".join(sh.text_frame.text for sh in prs.slides[0].shapes if sh.has_text_frame)
    assert money(r["npv"]["mean"]) in s1
    titles = [next((sh.text_frame.text for sh in s.shapes if sh.name == "Kicker"), "") for s in prs.slides]
    assert titles[:12] == ["THE DECISION", "THE OPPORTUNITY", "THE CONSTRAINTS", "THE EFFICIENT FRONTIER",
                           "FOUR STRATEGIC CHOICES", "TRADE-OFF RADAR", "RISK UNDER UNCERTAINTY",
                           "EXECUTION FEASIBILITY", "LOB IMPACT", "RECOMMENDED PORTFOLIO", "WHAT-IF SCENARIOS",
                           "LEADERSHIP DECISIONS"]
    assert m["5"]["balanced"]["alt"] == di_bundle.choices["balanced"]["alt_id"]
    assert m["10"]["selected"] == sorted(di_bundle.recommended.starts)
    assert m["A1"]["level"] == di_bundle.confidence.level
    # radar appendices reconcile with the shared radar profiles and are native radar charts
    from frontier_portfolio_iq.radar import radar_profiles
    rd = radar_profiles(di_bundle)
    for aid, vals in m["A5"]["plans"].items():
        assert vals == pytest.approx(rd["risk"][aid])
    for aid, vals in m["A6"]["plans"].items():
        assert vals == pytest.approx(rd["kpi"][aid])
    assert set(m["A6"]["plans"]) == {c["alt_id"] for c in di_bundle.choices.values()}
    assert m["A7"]["profiles"]["balanced"] == pytest.approx(rd["profiles"]["balanced"]["weights"])
    radars = [sh for sl in list(prs.slides)[16:] for sh in sl.shapes if sh.has_chart]
    assert len(radars) >= 4 and all("radar" in str(sh.chart.chart_type).lower() for sh in radars)


def test_radar_profiles_are_bounded_and_consistent(di_bundle):
    from frontier_portfolio_iq.radar import RISK_AXES, radar_profiles, risk_profile
    rd = radar_profiles(di_bundle)
    for fam in ("risk", "risk_adaptive", "kpi"):
        for vals in rd[fam].values():
            assert all(0.0 <= v <= 1.0 for v in vals.values())
    rec = di_bundle.recommended_id
    r = di_bundle.risk[rec]
    rp = risk_profile(r)
    assert set(rp) == {k for k, _, _ in RISK_AXES}
    assert rp["budget_confidence"] == pytest.approx(r["probabilities"]["within_budget"]["p"])
    assert rp["capacity"] == pytest.approx(1 - r["probabilities"]["capability_shortage"]["p"])
    # KPI axes: the ideal is reached by some efficient plan on every objective it was optimized for
    for k in di_bundle.frontier.objective_keys:
        assert max(v.get(k, 0) for v in rd["kpi"].values()) <= 1.0
    # what-if radar: baseline first, monetary axes relative to the best scenario
    sc = rd["scenarios"]
    assert next(iter(sc)) == "baseline" and max(v["values"]["expected_npv"] for v in sc.values()) == pytest.approx(1.0)
    # leaders are a plan among the strategic choices, or None for a tie
    plans = {c["alt_id"] for c in di_bundle.choices.values()}
    assert all(v is None or v in plans for v in rd["leaders"]["risk"].values())


def test_cli_decide_end_to_end(tmp_path):
    from frontier_portfolio_iq.cli import main
    out = str(tmp_path / "out")
    assert main(["decide", "--out", out, "--quick", "--no-whatif", "--no-v1", "--iterations", "500",
                 "--cache", "", "--rules", "mandatory"]) == 0
    for f in ("decision_dashboard.html", "ceo_decision_deck.pptx", "decision_intelligence.json",
              "decision_results.xlsx", "solver_confidence.md", "ceo_deck_manifest.json"):
        assert os.path.exists(os.path.join(out, f)), f
    D = json.load(open(os.path.join(out, "decision_intelligence.json")))
    assert D["rankings"]["balanced"]["profile"]["constraints"]
