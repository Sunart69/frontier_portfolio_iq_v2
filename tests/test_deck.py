"""Generated PowerPoint figures must match solver outputs (charts read back from the .pptx)."""
import pytest
from pptx import Presentation

from frontier_portfolio_iq.analysis import (candidate_investment, capability_supply_demand, capacity_by_type,
                                            fungibility_ladder, spider_scores, total_budget)
from frontier_portfolio_iq.domain import ENTERPRISE_UNALLOCATED
from frontier_portfolio_iq.style import money, signed_money

pytestmark = pytest.mark.integration


def charts(slide):
    return [s.chart for s in slide.shapes if s.has_chart]


def text(slide):
    out = []
    for s in slide.shapes:
        if s.has_text_frame:
            out.append(s.text_frame.text)
        if s.has_table:
            out += [c.text for r in s.table.rows for c in r.cells]
    return "\n".join(out)


def series(chart):
    return {s.name: list(s.values) for s in chart.plots[0].series}


def close(a, b, tol=1e-6):
    return len(a) == len(b) and all(abs(x - y) <= tol * max(1.0, abs(y)) for x, y in zip(a, b))


@pytest.fixture(scope="module")
def prs(deck):
    return Presentation(deck[0])


def test_deck_has_twelve_slides_in_storyline_order(prs):
    assert len(prs.slides) == 12
    kickers = [text(s).split("\n")[0].upper() for s in prs.slides]
    assert "EXECUTIVE INVESTMENT DECISION" in kickers[0]
    for i, k in enumerate(["STRATEGIC TENSION", "WHERE BUSINESS VALUE", "HOW CAPACITY", "PORTFOLIO CHOICES",
                           "SPIDER", "FUNGIBILITY", "BUDGET AND EXECUTION", "STAKEHOLDER COMMITMENTS",
                           "WHAT-IF", "RECOMMENDED PORTFOLIO", "DECISIONS REQUIRED"], start=1):
        assert k in kickers[i], (i, kickers[i])


def test_slide1_headline_figures(prs, bundle):
    t = text(prs.slides[0])
    cm, cond, unc = (bundle.tier_metrics[k] for k in ("committed", "conditional", "unconstrained"))
    for v in (money(cm["net_value"]), money(cm["investment"]), signed_money(cond["net_value"] - cm["net_value"]),
              signed_money(unc["net_value"] - cm["net_value"])):
        assert v in t


def test_slide2_supply_demand_chart(prs, bundle):
    sd = capability_supply_demand(bundle)
    s = series(charts(prs.slides[1])[0])
    cats = list(sd)
    assert close(s["Candidate demand (hours)"], [sd[c]["demand"] for c in cats])
    assert close(s["Available capacity (hours)"], [sd[c]["available"] for c in cats])
    assert money(abs(total_budget(bundle) - bundle.committed["investment"])) in text(prs.slides[1]) or \
        money(candidate_investment(bundle) - total_budget(bundle)) in text(prs.slides[1])


def test_slide3_lob_value_and_investment(prs, bundle):
    cm, d = bundle.committed, bundle.data
    keys = d.business_lobs() + ["ENT", ENTERPRISE_UNALLOCATED]
    s = series(charts(prs.slides[2])[0])
    assert close(s["Business value attributed"], [cm["lob_value"].get(k, 0.0) for k in keys])
    assert close(s["Investment funded"], [cm["investment_by_lob"].get(k, 0.0) for k in keys])
    assert abs(sum(s["Business value attributed"]) - cm["enterprise_value"]) < 1e-6


def test_slide4_capacity_chart(prs, bundle):
    ct = capacity_by_type(bundle)
    s = series(charts(prs.slides[3])[0])
    assert close(s["Shared – allocated"], [v["shared_allocated"] for v in ct.values()])
    assert close(s["Ring-fenced / conditional – idle"], [v["restricted_unused"] for v in ct.values()])
    assert f"{bundle.capacity['idle_ring_fenced_hours']:,.0f}" in text(prs.slides[3])


def test_slide5_portfolio_choices(prs, bundle, deck):
    s = series(charts(prs.slides[4])[0])["Net enterprise value"]
    assert s[0] == pytest.approx(bundle.committed["net_value"])
    assert s[-1] == pytest.approx(bundle.tier_metrics["unconstrained"]["net_value"])
    assert close(s, deck[1][5]["net_value"])
    assert "Analytical" in text(prs.slides[4])


def test_slide6_spider_matches_solver(prs, bundle):
    sc = spider_scores(bundle)
    got = series(charts(prs.slides[5])[0])
    assert set(got) == set(sc)
    for n, v in sc.items():
        assert close(got[n], v)


def test_slide7_fungibility(prs, bundle):
    ch = charts(prs.slides[6])
    assert close(series(ch[0])["Net value"], [v for _, v in fungibility_ladder(bundle)])
    rel = [r["incremental_value"] for r in bundle.release_values[:6]][::-1]
    assert close(series(ch[1])["Incremental value"], rel)


def test_slide8_monthly_investment(prs, bundle):
    cm, d = bundle.committed, bundle.data
    s = series(charts(prs.slides[7])[0])
    for l in d.business_lobs():
        assert close(s[d.lobs[l].name], [cm["investment_by_lob_month"][l][t] for t in d.months])
    total = [sum(v[i] for v in s.values()) for i in range(len(d.months))]
    assert close(total, [cm["investment_by_month"][t] for t in d.months])


def test_slide9_10_tables(prs, bundle):
    t9, t10 = text(prs.slides[8]), text(prs.slides[9])
    cm = bundle.committed
    for l in bundle.data.business_lobs():
        assert money(cm["investment_by_lob"][l]) in t9 and money(cm["lob_value"][l]) in t9
    for s in bundle.scenarios:
        assert s.scenario.name in t10 and signed_money(s.deltas["net_value"]) in t10


def test_slide11_12_recommendation_and_decisions(prs, bundle):
    t11, t12 = text(prs.slides[10]), text(prs.slides[11])
    for pid, c in bundle.classification.items():
        assert pid in t11
    n = sum(1 for c in bundle.classification.values() if c == "Approved")
    assert f"Approved ({n})" in t11
    assert money(max(0, bundle.decisions["conditional_gain"])) in t12
    for r in bundle.decisions["capacity_releases"][:5]:
        assert r["pool"] in t12
