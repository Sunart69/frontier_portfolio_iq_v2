"""CEO-ready decision deck (v2): a consulting-style story built entirely from the DecisionBundle.

  1 The Decision · 2 The Opportunity · 3 The Constraints · 4 The Efficient Frontier · 5 Four Strategic
  Choices · 6 Trade-Off Radar · 7 Risk Under Uncertainty · 8 Execution Feasibility · 9 LOB Impact ·
  10 Recommended Portfolio · 11 What-If Scenarios · 12 Leadership Decisions Required
  Appendices: A1 Solver validation · A2 Model and objectives · A3 Uncertainty assumptions · A4 Sensitivity ·
  A5 Risk trade-off radar · A6 Optimization KPI radar · A7 Risk appetite and what-if radar

Labels distinguish *proven* optimization results, *simulated* probabilities and *estimated* inputs. The
builder returns a manifest of every figure it placed (slide -> values), used by the tests to reconcile
the deck with the bundle.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List

from pptx.chart.data import CategoryChartData, XyChartData
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION, XL_MARKER_STYLE
from pptx.enum.text import PP_ALIGN
from pptx.util import Inches, Pt

from .deck import BODY, HEAD, DeckBuilder, _rgb
from .style import CATEGORICAL, GRID, INK, MUTED, TEXT, TEXT_2, money, pct

CHOICE_ORDER = ("aggressive", "balanced", "conservative", "accelerated")
CHOICE_COLORS = {k: CATEGORICAL[i] for i, k in enumerate(CHOICE_ORDER)}
PROOF_TEXT = {"CONFIRMED_OPTIMAL": "proven optimal within gap, confirmed by independent solvers",
              "PROVEN_SINGLE": "proven optimal within gap (single solver)",
              "FEASIBLE_UNPROVEN": "feasible, optimality not proven",
              "DISAGREEMENT": "solvers disagree — under review", "INFEASIBLE": "infeasible"}


def _m(v):
    return money(v)


class CEODeckBuilder(DeckBuilder):
    def __init__(self, bundle):
        from pptx import Presentation
        from .deck import H, W
        self.B = bundle
        self.prs = Presentation()
        self.prs.slide_width, self.prs.slide_height = Inches(W), Inches(H)
        self.blank = self.prs.slide_layouts[6]
        self.manifest: Dict[str, Dict] = {}
        self.d = bundle.data
        self.lobs = self.d.business_lobs()
        self.colors = {l: CATEGORICAL[i % len(CATEGORICAL)] for i, l in enumerate(list(self.d.lobs))}
        self.rec = bundle.recommended
        self.R = bundle.risk
        self.n_iter = bundle.uncertainty.settings.iterations
        self.calibrated = bundle.uncertainty.calibrated

    # ------------------------------------------------------------------ common
    def footer(self, s, n, dark: bool = False):
        lab = "" if self.calibrated else " · uncertainty inputs are uncalibrated demonstration defaults"
        self.text(s, 0.6, 7.05, 10.8, 0.3,
                  f"Source: Frontier Portfolio IQ v2 · model {self.B.version.label()} · run {self.B.run_id} · "
                  f"probabilities simulated ({self.n_iter:,} iterations){lab}",
                  size=9, color="AEB6C4" if dark else MUTED, name="Footer")
        self.text(s, 11.9, 7.05, 0.8, 0.3, str(n), size=9, color="AEB6C4" if dark else MUTED,
                  align=PP_ALIGN.RIGHT, name="SlideNumber")

    def badge(self, s, x, y, label, kind):
        fill = {"proven": "0A7A0A", "simulated": "2A78D6", "estimate": "8A5A00"}[kind]
        b = self.card(s, x, y, 1.45, 0.3, fill=fill)
        b.text_frame.text = label
        p = b.text_frame.paragraphs[0]
        p.alignment = PP_ALIGN.CENTER
        r = p.runs[0]
        r.font.size, r.font.bold, r.font.name = Pt(9), True, BODY
        r.font.color.rgb = _rgb("FFFFFF")
        return b

    def choice(self, key):
        c = self.B.choices.get(key)
        return (c, self.B.alt(c["alt_id"])) if c else (None, None)

    def risk(self, alt_id):
        return self.R.get(alt_id) or self.B.risk_adaptive.get(alt_id)

    def bullets(self, s, x, y, w, h, items, size=14, color=TEXT):
        return self.text(s, x, y, w, h, [(("•  " + t, {}),) for t in items], size=size, color=color, para_space=8)

    # ------------------------------------------------------------------ slides
    def s1_decision(self, n):
        B, rec = self.B, self.rec
        r = self.risk(rec.alt_id)
        bal_c = B.choices.get("balanced", {})
        s = self.slide(dark=True)
        title = (f"Approve the {bal_c.get('title', 'recommended')} portfolio: {len(rec.starts)} initiatives, "
                 f"{_m(r['npv']['mean'])} expected NPV, {pct(r['probabilities']['within_budget']['p'])} "
                 f"chance of staying within budget")
        self.title(s, n, title, "The decision", dark=True)
        inv = sum(v.get("investment", 0.0) for v in rec.lob_allocations.values())
        self.stat(s, 0.6, 2.3, 2.9, _m(r["npv"]["mean"]), f"expected NPV (P10 {_m(r['npv']['p10'])} – "
                  f"P90 {_m(r['npv']['p90'])})", dark=True)
        self.stat(s, 3.7, 2.3, 2.9, _m(inv), "investment in the plan", dark=True)
        self.stat(s, 6.8, 2.3, 2.9, pct(r["probabilities"]["within_budget"]["p"]),
                  "probability cost stays within plan + reserve", dark=True)
        self.stat(s, 9.9, 2.3, 2.85, pct(r["probabilities"]["meet_key_dates"]["p"]),
                  "probability every committed date is met", dark=True)
        self.badge(s, 0.6, 4.05, "SIMULATED", "simulated")
        self.text(s, 2.2, 4.05, 10.5, 0.4, f"Plan optimized and {PROOF_TEXT.get(B.recommended_confidence.level, '').lower() if B.recommended_confidence else ''}"
                  f"; outcomes simulated over {self.n_iter:,} futures.", size=12, color="D5DAE3")
        asks = ["Fund the recommended portfolio and its start sequence (slide 10)",
                "Choose the risk posture: the four strategic choices trade value against confidence (slides 5–7)",
                "Grant the capacity and budget decisions on slide 12",
                "Commission calibration of the uncertainty inputs before the next cycle" if not self.calibrated
                else "Confirm the risk acceptance level (CVaR) on slide 12"]
        self.bullets(s, 0.6, 4.65, 12.1, 2.2, asks, size=15, color="FFFFFF")
        self.manifest["1"] = {"alt": rec.alt_id, "expected_npv": r["npv"]["mean"], "p_within_budget":
                              r["probabilities"]["within_budget"]["p"], "investment": inv}

    def s2_opportunity(self, n):
        d, rec = self.d, self.rec
        total = sum(p.value for p in d.projects.values())
        captured = sum(d.projects[i].value for i in rec.starts)
        r = self.risk(rec.alt_id)
        s = self.slide()
        self.title(s, n, f"{len(d.projects)} proposals could unlock {_m(total)}; the recommended plan captures "
                         f"{pct(captured / total if total else 0)} of it — {_m(r['value']['mean'])} expected after "
                         f"execution risk", "The opportunity")
        lobs = list(d.lobs)
        pot = [sum(d.lob_benefit(i, l) for i in d.projects) for l in lobs]
        got = [rec.lob_allocations.get(l, {}).get("value", 0.0) for l in lobs]
        self.chart(s, XL_CHART_TYPE.BAR_CLUSTERED, [d.lobs[l].name for l in lobs],
                   [("All proposals (attributed value)", pot), ("Recommended plan", got)], 0.6, 1.8, 7.6, 4.9,
                   [GRID.replace("E4E3DF", "B7B6AF"), CATEGORICAL[0]], labels=True, gap=50)
        self.stat(s, 8.6, 1.9, 4.1, _m(captured), "plan value (deterministic, before risk)")
        self.stat(s, 8.6, 3.6, 4.1, _m(r["value"]["mean"]), "expected value after simulated execution risk")
        self.badge(s, 8.6, 5.3, "SIMULATED", "simulated")
        self.text(s, 8.6, 5.7, 4.1, 1.2, f"P10–P90 range {_m(r['value']['p10'])} – {_m(r['value']['p90'])}. "
                  "Benefits are counted once across LOBs.", size=12, color=TEXT_2)
        self.manifest["2"] = {"total_value": total, "captured": captured, "expected_value": r["value"]["mean"]}

    def s3_constraints(self, n):
        d, rec, B = self.d, self.rec, self.B
        s = self.slide()
        caps = sorted({p.capability for p in d.pools.values()})
        avail = {c: sum(p.available_hours(t) for p in d.pools.values() if p.capability == c for t in d.months)
                 for c in caps}
        demand_all = {c: sum(h for p in d.projects.values() for (cc, _k), h in p.demand.items() if cc == c) for c in caps}
        demand_rec = {c: sum(h for i in rec.starts for (cc, _k), h in d.projects[i].demand.items() if cc == c)
                      for c in caps}
        over = [c for c in caps if demand_all[c] > avail[c]]
        budget = sum(b.amount for b in d.budgets)
        asks = sum(p.total_cost for p in d.projects.values())
        peak = B.recommended.objectives.get("overload", 0.0)
        self.title(s, n, f"Budgets and capability capacity bind: proposals ask for {_m(asks)} against "
                         f"{_m(budget)} of budget, and {len(over)} of {len(caps)} capabilities are oversubscribed",
                   "The constraints")
        self.chart(s, XL_CHART_TYPE.BAR_CLUSTERED, caps,
                   [("Available hours", [avail[c] for c in caps]), ("Demand, all proposals", [demand_all[c] for c in caps]),
                    ("Demand, recommended plan", [demand_rec[c] for c in caps])], 0.6, 1.8, 7.8, 4.9,
                   ["B7B6AF", CATEGORICAL[1], CATEGORICAL[0]], fmt='#,##0', gap=40)
        must = [i for i, p in d.projects.items() if p.mandatory or p.compliance or p.critical]
        self.stat(s, 8.8, 1.9, 3.9, pct(peak), "peak monthly load on any pool in the recommended plan")
        self.stat(s, 8.8, 3.6, 3.9, str(len(must)), "mandatory, compliance or critical commitments")
        notes = [f"Ring-fenced capacity may serve other LOBs only through approved transfer rules",
                 f"{sum(1 for r in d.transfers if not r.is_authorized())} transfer/release requests await approval"]
        if B.analysis is not None:
            cap = B.analysis.capacity
            idle = cap.get("idle_ring_fenced_hours") if isinstance(cap, dict) else None
            if idle:
                notes.append(f"{idle:,.0f} ring-fenced hours sit idle in the committed plan")
        self.bullets(s, 8.8, 5.35, 3.9, 1.6, notes, size=12, color=TEXT_2)
        self.manifest["3"] = {"budget": budget, "asks": asks, "oversubscribed": over, "peak_load": peak}

    def s4_frontier(self, n):
        B, f = self.B, self.B.frontier
        kx, ky = f.objective_keys[1] if len(f.objective_keys) > 1 else f.objective_keys[0], f.objective_keys[0]
        sx, sy = B.objectives.spec(kx), B.objectives.spec(ky)
        eff = [a for a in f.alternatives if a.status == "efficient"]
        dom = [a for a in f.alternatives if a.status == "dominated"]
        s = self.slide()
        lo = min(a.objectives[ky] for a in eff) if eff else 0
        hi = max(a.objectives[ky] for a in eff) if eff else 0
        self.title(s, n, f"{len(eff)} efficient portfolios span {_m(lo)} to {_m(hi)} of value; moving along the "
                         f"frontier trades {sx.name.lower()} for {sy.name.lower()}", "The efficient frontier")
        cd = XyChartData()
        series = [x for x in [("Dominated", dom, "C3C2B7"), ("Efficient", eff, CATEGORICAL[0])] if x[1]]
        for name, alts, _c in series:
            ser = cd.add_series(name)
            for a in alts:
                ser.add_data_point(a.objectives[kx], a.objectives[ky])
        chosen = []
        for k in CHOICE_ORDER:
            c, a = self.choice(k)
            if a is None:
                continue
            ser = cd.add_series(c["title"])
            ser.add_data_point(a.objectives[kx], a.objectives[ky])
            chosen.append((k, CHOICE_COLORS[k]))
        gf = s.shapes.add_chart(XL_CHART_TYPE.XY_SCATTER, Inches(0.6), Inches(1.8), Inches(8.2), Inches(5.0), cd)
        ch = gf.chart
        ch.font.size, ch.font.name = Pt(11), BODY
        ch.has_legend = True
        ch.legend.position = XL_LEGEND_POSITION.BOTTOM
        ch.legend.include_in_layout = False
        colors = [c for _n, _a, c in series] + [c for _k, c in chosen]
        for ser, col in zip(ch.plots[0].series, colors):
            ser.format.line.fill.background()
            ser.marker.style = XL_MARKER_STYLE.CIRCLE
            ser.marker.size = 9 if col not in ("C3C2B7",) else 7
            ser.marker.format.fill.solid()
            ser.marker.format.fill.fore_color.rgb = _rgb(col)
            ser.marker.format.line.color.rgb = _rgb("FFFFFF")
        va, ca = ch.value_axis, ch.category_axis
        for ax, sp in ((va, sy), (ca, sx)):
            ax.has_title = True
            ax.axis_title.text_frame.text = f"{sp.name} ({sp.unit}, {'higher' if sp.direction == 'max' else 'lower'} is better)"
            ax.axis_title.text_frame.paragraphs[0].runs[0].font.size = Pt(10)
            ax.tick_labels.font.size = Pt(10)
            if ax.has_major_gridlines:
                ax.major_gridlines.format.line.color.rgb = _rgb(GRID)
        self.badge(s, 9.1, 1.9, "PROVEN", "proven")
        self.text(s, 9.1, 2.3, 3.6, 4.4, [
            f"Each point is a feasible portfolio solved by HiGHS (epsilon-constraint and weighted sums) and "
            f"checked by an independent validator.",
            f"Efficient = no other plan is at least as good on all of: "
            + ", ".join(B.objectives.spec(k).name.lower() for k in f.objective_keys) + ".",
            f"{len(f.infeasible)} target combinations had no feasible portfolio; they are recorded, not hidden.",
            "Weighted sums can only reach the convex part of the frontier; epsilon-constraint also finds "
            "the non-convex trade-offs."], size=12, color=TEXT_2, para_space=10)
        self.manifest["4"] = {"efficient": len(eff), "dominated": len(dom), "x": kx, "y": ky}

    def _choice_rows(self):
        rows = [["", *[self.B.choices[k]["title"] for k in CHOICE_ORDER if k in self.B.choices]]]
        keys = [k for k in CHOICE_ORDER if k in self.B.choices]
        alts = [self.B.alt(self.B.choices[k]["alt_id"]) for k in keys]
        rk = [self.risk(a.alt_id) for a in alts]

        def row(label, fn):
            rows.append([label, *[fn(a, r) for a, r in zip(alts, rk)]])
        row("Initiatives", lambda a, r: str(len(a.starts)))
        row("Investment", lambda a, r: _m(sum(v.get("investment", 0) for v in a.lob_allocations.values())))
        row("Expected NPV (simulated)", lambda a, r: _m(r["npv"]["mean"]))
        row("P10 – P90 NPV", lambda a, r: f"{_m(r['npv']['p10'])} – {_m(r['npv']['p90'])}")
        row("P(within budget)", lambda a, r: pct(r["probabilities"]["within_budget"]["p"]))
        row("P(meet committed dates)", lambda a, r: pct(r["probabilities"]["meet_key_dates"]["p"]))
        row("CVaR shortfall (worst 5%)", lambda a, r: _m(r["var_cvar"]["cvar"]))
        row("Months to value (value-weighted)", lambda a, r: f"{a.objectives.get('time_to_value', 0):.1f}")
        rules = {"aggressive": "Highest expected NPV", "balanced": "Best balanced risk-adjusted score",
                 "conservative": "Lowest CVaR at ≥70% of value", "accelerated": "Earliest value at ≥70% of value"}
        rows.append(["Selection rule", *[rules[k] for k in keys]])
        return rows, keys, alts, rk

    def s5_choices(self, n):
        rows, keys, alts, rk = self._choice_rows()
        s = self.slide()
        best = max(zip(keys, rk), key=lambda kr: kr[1]["npv"]["mean"])
        safe = max(zip(keys, rk), key=lambda kr: kr[1]["probabilities"]["within_budget"]["p"])
        same = len({a.alt_id for a in alts}) < len(alts)
        self.title(s, n, f"Four strategic choices: {self.B.choices[best[0]]['title']} maximizes expected NPV; "
                         f"{self.B.choices[safe[0]]['title']} gives the most budget confidence"
                         + (" (two choices coincide)" if same else ""), "Four strategic choices")
        self.table(s, rows, 0.6, 1.8, 12.1, [3.1] + [2.25] * (len(rows[0]) - 1), font=11, row_h=0.42)
        self.badge(s, 0.6, 6.6, "SIMULATED", "simulated")
        self.text(s, 2.2, 6.6, 10.5, 0.4, "Probabilities and NPV ranges are Monte Carlo estimates on common random "
                  "numbers, so the four plans face the same simulated futures.", size=11, color=TEXT_2)
        self.manifest["5"] = {k: {"alt": a.alt_id, "expected_npv": r["npv"]["mean"]} for k, a, r in zip(keys, alts, rk)}

    def s6_radar(self, n):
        from .decision.recommend import CRITERIA
        crit = ["expected_npv", "downside_risk", "budget_confidence", "schedule_confidence", "strategic_alignment",
                "okr_achievement", "resource_resilience", "time_to_value"]
        rows = {r["alt_id"]: r for r in self.B.rankings["balanced"].rows}
        keys = [k for k in CHOICE_ORDER if k in self.B.choices]
        s = self.slide()
        winners = {}
        for c in crit:
            vals = {k: rows[self.B.choices[k]["alt_id"]]["criteria"][c]["normalized"] for k in keys}
            best = max(keys, key=lambda k: vals[k])
            if sum(1 for k in keys if vals[k] >= vals[best] - 1e-6) > 1 and \
                    len({self.B.choices[k]["alt_id"] for k in keys if vals[k] >= vals[best] - 1e-6}) > 1:
                continue        # a tie between different plans: nobody "wins" this criterion
            winners.setdefault(self.B.choices[best]["title"], []).append(c.replace("_", " "))
        top = max(winners.items(), key=lambda kv: len(kv[1])) if winners else ("No plan", [])
        self.title(s, n, f"No plan wins everywhere: {top[0]} leads on {len(top[1])} of {len(crit)} criteria; "
                         f"{len(crit) - sum(len(v) for v in winners.values())} are ties", "Trade-off radar")
        short = {"expected_npv": "Expected NPV", "downside_risk": "Downside protection",
                 "budget_confidence": "Budget confidence", "schedule_confidence": "Schedule confidence",
                 "strategic_alignment": "Strategic alignment", "okr_achievement": "OKR achievement",
                 "resource_resilience": "Resource resilience", "time_to_value": "Speed to value"}
        cats = [short[c] for c in crit]
        series = [(self.B.choices[k]["title"], [rows[self.B.choices[k]["alt_id"]]["criteria"][c]["normalized"] for c in crit])
                  for k in keys]
        ch = self.chart(s, XL_CHART_TYPE.RADAR, cats, series, 0.6, 1.7, 7.8, 5.25, [CHOICE_COLORS[k] for k in keys],
                        fmt="0.0", legend=True)
        try:
            ch.value_axis.maximum_scale, ch.value_axis.minimum_scale = 1.0, 0.0
        except Exception:
            pass
        self.text(s, 8.8, 1.9, 3.9, 4.8, ["Scales (1 = best):",
                                         "Expected NPV, alignment: relative to the best plan",
                                         "Probabilities: as simulated",
                                         "Downside protection: 1 − CVaR ÷ expected NPV",
                                         "Months to value: best ÷ this plan",
                                         *[f"{t}: strongest on {', '.join(v[:3])}" for t, v in winners.items()]],
                  size=12, color=TEXT_2, para_space=8)
        self.manifest["6"] = {"criteria": crit, "winners": winners}

    def s7_risk(self, n):
        rec, r = self.rec, self.risk(self.rec.alt_id)
        s = self.slide()
        p = r["probabilities"]
        self.title(s, n, f"Under uncertainty the recommended plan delivers at least {_m(r['npv']['p10'])} NPV in 9 of "
                         f"10 futures; worst-5% shortfall versus plan averages {_m(r['var_cvar']['cvar'])}",
                   "Risk under uncertainty")
        h = r["histogram"]
        edges = h["edges"]
        cats = [f"{(edges[i] + edges[i + 1]) / 2000:,.1f}" for i in range(len(h["counts"]))]
        ch = self.chart(s, XL_CHART_TYPE.COLUMN_CLUSTERED, cats, [("Simulated futures", h["counts"])], 0.6, 1.8, 7.4,
                        4.4, [CATEGORICAL[0]], fmt="#,##0", legend=False, gap=10,
                        title=f"NPV distribution, $M ({r['iterations']:,} simulated futures)")
        self.text(s, 0.6, 6.25, 7.4, 0.6, f"Plan (deterministic) NPV {_m(r['reference']['npv'])} · P10 {_m(r['npv']['p10'])} · "
                  f"P50 {_m(r['npv']['p50'])} · P90 {_m(r['npv']['p90'])}. P10 = 10% of futures are worse.",
                  size=11, color=TEXT_2)
        rows = [["Probability (simulated)", "Value"],
                ["Cost within plan + reserve", pct(p["within_budget"]["p"])],
                ["Every LOB within its budget", pct(1 - p["lob_budget_breach"]["p"])],
                ["All committed dates met", pct(p["meet_key_dates"]["p"])],
                ["OKR targets the plan meets stay met", pct(p["okr_plan_targets"]["p"])],
                ["No capability shortage in any month", pct(1 - p["capability_shortage"]["p"])],
                ["Value ≥ minimum acceptable", pct(p["min_acceptable_value"]["p"])]]
        self.table(s, rows, 8.3, 1.85, 4.4, [3.2, 1.2], font=11, row_h=0.45)
        self.badge(s, 8.3, 5.2, "SIMULATED", "simulated")
        conv = r["convergence"]
        self.text(s, 8.3, 5.6, 4.4, 1.3, f"Sampling error: ±{_m(conv['se_mean_npv'] * 1.96)} on expected NPV (95%); "
                  f"{'converged' if conv['converged'] else 'NOT converged'}. More iterations cut sampling error, "
                  "not assumption error.", size=11, color=TEXT_2)
        self.manifest["7"] = {"p10": r["npv"]["p10"], "cvar": r["var_cvar"]["cvar"], "within_budget": p["within_budget"]["p"]}

    def s8_execution(self, n):
        d, rec, r = self.d, self.rec, self.risk(self.rec.alt_id)
        s = self.slide()
        caps = sorted({p.capability for p in d.pools.values()})
        util = {}
        for c in caps:
            row = []
            for t in d.months:
                A = sum(p.available_hours(t) for p in d.pools.values() if p.capability == c)
                used = sum(rec.capability_allocations.get(pid, {}).get(t, 0.0) for pid, p in d.pools.items()
                           if p.capability == c)
                row.append(used / A if A else 0.0)
            util[c] = row
        top = sorted(caps, key=lambda c: -max(util[c]))[:3]
        fin = r.get("project_finish", {})
        slips = sorted(((i, v) for i, v in fin.items() if v["p90"] > v["planned"]), key=lambda kv: -(kv[1]["p90"] - kv[1]["planned"]))
        likely = [i for i, v in fin.items() if v["p50"] > v["planned"]]
        self.title(s, n, f"Execution is tight: {top[0] if top else 'capacity'} runs at {pct(max(util[top[0]]) if top else 0)} "
                         f"in its peak month, and {len(likely)} of {len(fin)} initiatives more likely than not finish late",
                   "Execution feasibility")
        self.chart(s, XL_CHART_TYPE.LINE_MARKERS, [d.settings.month_label(t) for t in d.months],
                   [(c, util[c]) for c in top], 0.6, 1.8, 6.9, 4.9, [CATEGORICAL[i] for i in range(len(top))],
                   fmt="0%", title="Monthly load, most constrained capabilities (recommended plan)")
        rows = [["Initiative", "Planned finish", "P50", "P90"]]
        for i, v in slips[:8]:
            rows.append([f"{i} {d.projects[i].name}"[:34], d.settings.month_label(v["planned"]),
                         d.settings.month_label(int(round(v["p50"]))), d.settings.month_label(int(round(v["p90"])))])
        if len(rows) == 1:
            rows.append(["No initiative slips at P90", "", "", ""])
        self.table(s, rows, 7.8, 1.85, 4.9, [2.5, 0.85, 0.75, 0.8], font=10, row_h=0.4)
        self.badge(s, 7.8, 5.8, "SIMULATED", "simulated")
        self.text(s, 7.8, 6.2, 4.9, 0.7, "Finish months from the schedule-aware simulation: delays propagate along "
                  "dependencies and through shared capacity.", size=10, color=TEXT_2)
        self.manifest["8"] = {"top_capabilities": top, "likely_late": len(likely)}

    def s9_lob(self, n):
        d, B = self.d, self.B
        rec = self.rec
        _c, agg = self.choice("aggressive")
        s = self.slide()
        lobs = list(d.lobs)
        inv = [rec.lob_allocations.get(l, {}).get("investment", 0.0) for l in lobs]
        val = [rec.lob_allocations.get(l, {}).get("value", 0.0) for l in lobs]
        gains = {l: val[k] - (agg.lob_allocations.get(l, {}).get("value", 0.0) if agg else 0.0) for k, l in enumerate(lobs)}
        loser = min(gains, key=gains.get) if gains else None
        self.title(s, n, f"Every LOB is served: {d.lobs[max(zip(val, lobs))[1]].name} gains most value"
                         + (f"; {d.lobs[loser].name} gives up {_m(-gains[loser])} versus the aggressive plan"
                            if loser and gains[loser] < -0.5 else ""), "LOB impact")
        self.chart(s, XL_CHART_TYPE.BAR_CLUSTERED, [d.lobs[l].name for l in lobs],
                   [("Investment", inv), ("Value delivered (attributed)", val)], 0.6, 1.8, 7.4, 4.9,
                   [CATEGORICAL[1], CATEGORICAL[0]], labels=True, gap=50)
        lines = []
        if B.analysis is not None:
            for c in B.analysis.decisions.get("capacity_releases", [])[:4]:
                lines.append(f"{c['owner']} would release {c['pool']} to {c['to']} ({c['hours_used']:,.0f} h) — needs approval")
        used = defaultdict(float)
        for res in (rec.result,):
            if res is not None:
                for a in res.allocations:
                    if a.kind == "transfer":
                        used[a.rule_id] += a.hours
        for rid, h in used.items():
            lines.append(f"Approved transfer {rid} carries {h:,.0f} h between LOBs")
        if not lines:
            lines.append("No capacity moves between LOBs in the recommended plan")
        self.text(s, 8.3, 1.9, 4.4, 4.8, ["Who gives up capacity:", *lines], size=12, color=TEXT_2, para_space=8)
        self.manifest["9"] = {"investment": dict(zip(lobs, inv)), "value": dict(zip(lobs, val))}

    def s10_recommended(self, n):
        d, B, rec = self.d, self.B, self.rec
        r = self.risk(rec.alt_id)
        s = self.slide()
        self.title(s, n, f"Recommended: {rec.name} — {len(rec.starts)} initiatives sequenced to start between "
                         f"{d.settings.month_label(min(rec.starts.values()))} and {d.settings.month_label(max(rec.starts.values()))}",
                   "Recommended portfolio")
        fin = r.get("project_finish", {})
        rows = [["ID", "Initiative", "LOB", "Start", "Finish", "P90 finish"]]
        for i in sorted(rec.starts, key=lambda i: (rec.starts[i], i))[:13]:
            p = d.projects[i]
            v = fin.get(i, {})
            rows.append([i, p.name[:30], d.lobs[p.primary_lob].name[:18] if p.primary_lob in d.lobs else p.primary_lob,
                         d.settings.month_label(rec.starts[i]), d.settings.month_label(rec.starts[i] + p.duration - 1),
                         d.settings.month_label(int(round(v["p90"]))) if v else "—"])
        self.table(s, rows, 0.6, 1.8, 7.9, [0.6, 3.0, 1.7, 0.85, 0.85, 0.9], font=10, row_h=0.36)
        rk = B.rankings[B.config.primary_profile]
        self.text(s, 8.8, 1.85, 3.9, 4.9, ["Why this plan:", *rk.rationale[:3], *( [rk.note] if rk.note else [])],
                  size=11, color=TEXT_2, para_space=8)
        if len(rec.starts) > 13:
            self.text(s, 0.6, 6.75, 7.9, 0.3, f"+ {len(rec.starts) - 13} more in the results workbook", size=10, color=MUTED)
        self.manifest["10"] = {"alt": rec.alt_id, "selected": sorted(rec.starts)}

    def s11_whatif(self, n):
        B = self.B
        s = self.slide()
        ok = [o for o in B.scenarios if o.status == "ok"]
        broken = [o for o in ok if o.baseline_plan and not o.baseline_plan.get("feasible")]
        self.title(s, n, f"What if assumptions change? The recommendation holds in {len(ok) - len(broken)} of {len(ok)} "
                         f"scenarios; " + (", ".join(o.whatif.name.lower() for o in broken) + " would require a re-plan"
                                           if broken else "none requires a re-plan"), "What-if scenarios")
        rows = [["Scenario", "What changes", "Best plan in scenario", "Expected NPV", "P(within budget)",
                 "Today's recommendation in this scenario"]]
        rb = self.risk(self.rec.alt_id)
        rows.append(["Baseline", "Intake as submitted", self.rec.name[:24], _m(rb["npv"]["mean"]),
                     pct(rb["probabilities"]["within_budget"]["p"]), "—"])
        for o in B.scenarios:
            if o.status != "ok":
                rows.append([o.whatif.name, o.whatif.description[:48], "infeasible", "—", "—", o.note[:40]])
                continue
            rr, bp = o.recommended, o.baseline_plan or {}
            rows.append([o.whatif.name, o.whatif.description[:48], rr["name"][:24], _m(rr["expected_npv"]),
                         pct(rr["within_budget"]),
                         (f"feasible, {_m(bp['expected_npv'])} E[NPV]" if bp.get("feasible") else "no longer feasible")])
        self.table(s, rows, 0.6, 1.85, 12.1, [1.4, 3.6, 2.2, 1.4, 1.4, 2.1], font=10, row_h=0.55)
        self.text(s, 0.6, 6.4, 12.1, 0.5, "Each scenario re-runs the optimization, a reduced frontier, the Monte Carlo "
                  "evaluation and the recommendation under its own assumptions (cached by model fingerprint).",
                  size=11, color=TEXT_2)
        self.manifest["11"] = {o.whatif.key: (o.recommended or {}).get("expected_npv") for o in B.scenarios}

    def s12_decisions(self, n):
        B, d, rec = self.B, self.d, self.rec
        r = self.risk(rec.alt_id)
        s = self.slide(dark=True)
        self.title(s, n, "Leadership decisions required today", "Leadership decisions", dark=True)
        inv = {l: v.get("investment", 0.0) for l, v in rec.lob_allocations.items() if v.get("investment", 0.0) > 0.5}
        pending = [r_.rule_id for r_ in d.transfers if not r_.is_authorized() and r_.approval_status.value == "Pending"]
        out = sorted(set(d.projects) - set(rec.starts))
        items = [
            ("Funding", f"Approve {_m(sum(inv.values()))}: " + ", ".join(f"{d.lobs[l].name if l in d.lobs else l} {_m(v)}" for l, v in inv.items())),
            ("Prioritization", f"Start the {len(rec.starts)} initiatives on slide 10 in sequence; defer "
                               f"{len(out)} ({', '.join(out[:8])}{'…' if len(out) > 8 else ''})"),
            ("Capacity", (f"Decide pending transfer/release requests: {', '.join(pending)}" if pending else
                          "No pending capacity requests") + "; protect the bottleneck capabilities from new demand"),
            ("Risk acceptance", f"Accept a worst-5% NPV shortfall of {_m(r['var_cvar']['cvar'])} and a "
                                f"{pct(r['probabilities']['miss_key_date']['p'])} chance that a committed date slips"),
            ("Assumptions", "Calibrate cost, schedule and benefit uncertainty with historical data before the next cycle"
             if not self.calibrated else "Uncertainty inputs are calibrated; review them each cycle"),
        ]
        y = 1.9
        for head, body in items:
            self.card(s, 0.6, y, 12.1, 0.88, fill="2B3A55")
            self.text(s, 0.85, y + 0.12, 2.4, 0.6, head, size=15, bold=True, color="FAB219", font=HEAD)
            self.text(s, 3.2, y + 0.12, 9.3, 0.7, body, size=13, color="FFFFFF")
            y += 0.98
        self.manifest["12"] = {"funding": sum(inv.values()), "pending": pending, "deferred": out}

    # ------------------------------------------------------------------ appendices
    def a1_solvers(self, n):
        B = self.B
        s = self.slide()
        c = B.confidence
        self.title(s, n, f"A1 · Solver validation: {c.level.replace('_', ' ').lower()} — {c.verdict}", "Appendix")
        rows = [["Engine", "Role", "Status", "Proof", "Objective", "Bound", "Gap", "Validated", "Time"]]
        for run in c.runs + ((B.recommended_confidence.runs if B.recommended_confidence else [])):
            nres = run.result
            rows.append([nres.engine, run.role if run in c.runs else "recommended plan", nres.status.value, nres.proof,
                         f"{nres.objective:,.1f}" if nres.objective is not None else "—",
                         f"{nres.bound:,.1f}" if nres.bound is not None else "—",
                         f"{nres.gap:.2%}" if nres.gap is not None else "—",
                         "—" if run.validation is None else ("yes" if run.validation.feasible else "NO"),
                         f"{nres.runtime:.1f}s"])
        self.table(s, rows[:12], 0.6, 1.8, 12.1, [1.1, 1.7, 1.1, 1.1, 1.5, 1.5, 1.0, 1.0, 0.9], font=10, row_h=0.38)
        self.text(s, 0.6, 6.3, 12.1, 0.7, ["Proof levels: optimal = gap 0; within gap = proven within the requested "
                                           f"{B.data.settings.mip_gap:.0%}; feasible = solution without a proven gap. "
                                           "CP-SAT solves an integer reformulation; its bound is approximate."] +
                  (["Diagnostics: " + "; ".join(c.diagnostics[:3])] if c.diagnostics else []), size=10, color=TEXT_2)
        self.manifest["A1"] = {"level": c.level}

    def a2_model(self, n):
        B = self.B
        s = self.slide()
        self.title(s, n, "A2 · Model and objectives", "Appendix")
        rows = [["Objective", "Direction", "Unit", "Formula"]]
        for sp in B.objectives.specs:
            rows.append([sp.name, sp.direction, sp.unit, sp.formula[:95]])
        self.table(s, rows, 0.6, 1.7, 12.1, [2.6, 0.9, 1.7, 6.9], font=9, row_h=0.36)
        self.text(s, 0.6, 6.25, 12.1, 0.75, "Hard constraints in every plan: pool capacity by month, ring-fencing and "
                  "eligibility, authorized transfers within ceilings and retained hours, LOB budgets by funding source "
                  "and month, dependencies, deadlines and start windows, mandatory/compliance/critical commitments, "
                  "LOB governance targets.", size=10, color=TEXT_2)
        self.manifest["A2"] = {"objectives": [sp.key for sp in B.objectives.specs]}

    def a3_uncertainty(self, n):
        B = self.B
        um = B.uncertainty
        s = self.slide()
        self.title(s, n, "A3 · Uncertainty assumptions " + ("(calibrated)" if um.calibrated else
                                                             "(demonstration defaults — calibrate before production use)"),
                   "Appendix")
        rows = [["Driver", "Category", "Distribution", "Effect", "Applies to"]]
        for dr in um.drivers[:9]:
            rows.append([dr.name[:30], dr.category, dr.dist.describe(), f"{dr.effect} ({dr.mode})", dr.applies_to[:28]])
        self.table(s, rows, 0.6, 1.7, 7.6, [2.3, 1.1, 1.8, 1.3, 1.1], font=9, row_h=0.36)
        st = um.settings
        self.badge(s, 8.5, 1.75, "ESTIMATE", "estimate")
        self.text(s, 8.5, 2.15, 4.2, 4.6, [
            "Initiative defaults: effort pert(0.9, 1, 1+0.8r); duration pert(0.95, 1, 1+r); benefit "
            "pert(0.5+0.3c, 0.85+0.15c, 1.1); start slip discrete; failure 0.5 r (1−c)  (r = execution risk, "
            "c = delivery confidence)",
            f"Simulation: {st.iterations:,} iterations, seed {st.seed}, {st.mode} plan; common random numbers; "
            f"VaR/CVaR at {st.var_alpha:.0%}",
            f"Correlated drivers via Gaussian copula ({len(um.correlations)} pairs), matrix validated PSD"
            + (" (repaired)" if um.repaired_correlation else ""),
            *um.assumptions[:2]], size=10, color=TEXT_2, para_space=7)
        self.manifest["A3"] = {"calibrated": um.calibrated, "drivers": [d.driver_id for d in um.drivers]}

    def a4_sensitivity(self, n):
        r = self.risk(self.rec.alt_id)
        sens = r.get("sensitivity", [])[:10]
        s = self.slide()
        top = sens[0]["input"] if sens else "—"
        self.title(s, n, f"A4 · Sensitivity: {top} moves NPV most (rank correlation)", "Appendix")
        if sens:
            self.chart(s, XL_CHART_TYPE.BAR_CLUSTERED, [x["input"] for x in sens][::-1],
                       [("Spearman correlation with NPV", [x["spearman_npv"] for x in sens][::-1])],
                       0.6, 1.7, 8.0, 5.1, [CATEGORICAL[0]], fmt="0.00", legend=False, labels=True, gap=40)
            try:
                ch = s.shapes[-1].chart
                ch.value_axis.minimum_scale = -1.0
                ch.value_axis.maximum_scale = 1.0
            except Exception:
                pass
        self.text(s, 8.9, 1.8, 3.8, 4.8, ["Positive = higher input, higher NPV (e.g. benefit realization); "
                                         "negative = higher input, lower NPV (e.g. inflation, effort).",
                                         "Tail contributions (worst 5% of futures):",
                                         *[f"{c['project']} {c['name'][:22]}: {_m(c['tail_shortfall'])}"
                                           for c in r.get("contributions", [])[:5]]], size=11, color=TEXT_2, para_space=7)
        self.manifest["A4"] = {"top": [x["input"] for x in sens[:5]]}

    # ------------------------------------------------------------------ radar appendices
    def _radar(self, s, cats, series, x, y, w, h, colors, fmt="0%", top=1.0, title=None, dashed=()):
        ch = self.chart(s, XL_CHART_TYPE.RADAR, cats, series, x, y, w, h, colors, fmt=fmt, legend=True, title=title)
        try:
            ch.value_axis.maximum_scale, ch.value_axis.minimum_scale = top, 0.0
            ch.value_axis.major_unit = top / 4
            ch.value_axis.tick_labels.number_format = fmt
            ch.value_axis.tick_labels.number_format_is_linked = False
            ch.value_axis.tick_labels.font.size = Pt(9)
            ch.value_axis.major_gridlines.format.line.color.rgb = _rgb(GRID)
        except Exception:
            pass
        from pptx.enum.dml import MSO_LINE_DASH_STYLE
        for i in dashed:
            ch.plots[0].series[i].format.line.dash_style = MSO_LINE_DASH_STYLE.DASH
        return ch

    def _choice_plans(self):
        """Distinct plans among the strategic choices, titled by every choice they represent."""
        out = {}
        for k in CHOICE_ORDER:
            c = self.B.choices.get(k)
            if c:
                out.setdefault(c["alt_id"], {"keys": [], "titles": []})
                out[c["alt_id"]]["keys"].append(k)
                out[c["alt_id"]]["titles"].append(c["title"])
        return [(aid, " = ".join(v["titles"]), CHOICE_COLORS[v["keys"][0]]) for aid, v in out.items()]

    def _leader_lines(self, kind, axes):
        L = self.RD["leaders"][kind]
        by, ties = {}, []
        names = {aid: t for aid, t, _ in self._choice_plans()}
        for a in axes:
            lead = L.get(a["key"])
            (by.setdefault(names.get(lead, lead), []).append(a["label"]) if lead else ties.append(a["label"]))
        lines = [f"{t} leads on {', '.join(v)}" for t, v in by.items()]
        if ties:
            lines.append(f"Tied (within {self.RD['tie_tolerance']:.2f}): " + ", ".join(ties))
        return lines, by

    def a5_risk_radar(self, n):
        RD, s = self.RD, self.slide()
        axes = RD["risk_axes"]
        plans = [(aid, t, c) for aid, t, c in self._choice_plans() if aid in RD["risk"]]
        lines, by = self._leader_lines("risk", axes)
        n_ties = sum(1 for a in axes if RD["leaders"]["risk"].get(a["key"]) is None)
        safest = RD.get("safest")
        extra = safest if safest and safest not in {aid for aid, _, _ in plans} else None
        if n_ties * 2 >= len(axes):
            headline = f"the strategic choices carry near-identical risk (tied on {n_ties} of {len(axes)} axes)"
        else:
            top = max(by.items(), key=lambda kv: len(kv[1]))
            headline = f"{top[0]} is safest on {len(top[1])} of {len(axes)} risk axes"
        if extra:
            headline += "; a safer, lower-value plan exists"
        self.title(s, n, "A5 · Risk trade-off radar: " + headline, "Appendix")
        cats = [a["label"] for a in axes]
        series = [(t, [RD["risk"][aid][a["key"]] for a in axes]) for aid, t, _ in plans]
        colors = [c for *_, c in plans]
        if extra:
            er = self.risk(extra)
            series.append((f"Safest shortlisted ({_m(er['npv']['mean'])} E[NPV])", [RD["risk"][extra][a["key"]] for a in axes]))
            colors.append(MUTED)
        self._radar(s, cats, series, 0.5, 1.6, 6.2, 4.75, colors, title="Strategic choices on risk (1 = safest)",
                    dashed=(len(series) - 1,) if extra else ())
        rec = self.rec.alt_id
        adaptive = RD["risk_adaptive"].get(rec)
        if adaptive:
            self._radar(s, cats, [("Fixed plan", [RD["risk"][rec][a["key"]] for a in axes]),
                                  ("Adaptive (recourse)", [adaptive[a["key"]] for a in axes])],
                        6.8, 1.6, 6.0, 4.75, [CATEGORICAL[0], CATEGORICAL[2]], dashed=(1,),
                        title="Recommended plan: fixed vs adaptive execution")
        self.badge(s, 0.6, 6.4, "SIMULATED", "simulated")
        self.text(s, 2.2, 6.35, 10.5, 0.7, "; ".join(lines) + ". Axes: " + "; ".join(
            f"{a['label']} = {a['definition']}" for a in axes[6:]) + "; the rest are simulated probabilities.",
            size=9, color=TEXT_2)
        self.manifest["A5"] = {"axes": [a["key"] for a in axes], "safest_reference": extra,
                               "plans": {aid: RD["risk"][aid] for aid, _, _ in plans},
                               "adaptive": adaptive, "leaders": RD["leaders"]["risk"]}

    def a6_kpi_radar(self, n):
        RD, s = self.RD, self.slide()
        axes = RD["kpi_axes"]
        plans = [(aid, t, c) for aid, t, c in self._choice_plans() if aid in RD["kpi"]]
        lines, by = self._leader_lines("kpi", axes)
        top = max(by.items(), key=lambda kv: len(kv[1])) if by else ("No plan", [])
        self.title(s, n, f"A6 · Optimization KPI radar: {top[0]} leads on {len(top[1])} of {len(axes)} objectives; "
                         "no plan reaches the ideal everywhere", "Appendix")
        self._radar(s, [a["label"] for a in axes], [(t, [RD["kpi"][aid][a["key"]] for a in axes]) for aid, t, _ in plans],
                    0.5, 1.6, 7.6, 5.2, [c for *_, c in plans], fmt="0.00",
                    title="Each objective: 0 = nadir, 1 = ideal (outward = better)")
        self.badge(s, 8.4, 1.75, "PROVEN", "proven")
        self.text(s, 8.4, 2.15, 4.3, 4.7, ["Scale: every objective from the nadir (0, worst efficient value) to the "
                                           "ideal (1, best any plan reached on that objective alone). Cost, risk, peak "
                                           "load and time to value are flipped so outward is always better.",
                                           *lines,
                                           "Values are the optimized plans' deterministic objectives, each plan "
                                           "certified by the solver and the independent validator."],
                  size=11, color=TEXT_2, para_space=8)
        self.manifest["A6"] = {"axes": [a["key"] for a in axes], "plans": {aid: RD["kpi"][aid] for aid, _, _ in plans},
                               "leaders": RD["leaders"]["kpi"]}

    def a7_appetite_whatif_radar(self, n):
        RD, s = self.RD, self.slide()
        plab = {"expected_npv": "Expected NPV", "downside_risk": "Downside protection",
                "budget_confidence": "Budget confidence", "schedule_confidence": "Schedule confidence",
                "strategic_alignment": "Alignment", "okr_achievement": "OKR achievement",
                "resource_resilience": "Resilience", "time_to_value": "Speed to value"}
        profs = list(RD["profiles"].items())[:4]
        same = len({v["recommended"] for _, v in profs}) == 1
        self.title(s, n, "A7 · Risk appetite and what-if radar: " +
                   ("every risk profile recommends the same plan" if same else
                    "the recommendation changes with risk appetite"), "Appendix")
        wmax = max([0.05] + [w for _, v in profs for w in v["weights"].values()])
        top = min(1.0, -(-wmax * 20 // 1) / 20)
        self._radar(s, [plab.get(k, k) for k in RD["profile_axes"]],
                    [(k.replace("_", " "), [v["weights"][a] for a in RD["profile_axes"]]) for k, v in profs],
                    0.5, 1.6, 6.2, 4.6, CATEGORICAL[:len(profs)], top=top,
                    title="Weight each risk profile puts on a criterion")
        sc = list(RD["scenarios"].items())[:4]
        if sc:
            ax = RD["scenario_axes"]
            self._radar(s, [a["label"] for a in ax], [(v["name"], [v["values"][a["key"]] for a in ax]) for _, v in sc],
                        6.8, 1.6, 6.0, 4.6, CATEGORICAL[:len(sc)],
                        title="Best plan in each what-if scenario (1 = best)")
        self.badge(s, 0.6, 6.3, "SIMULATED", "simulated")
        self.text(s, 2.2, 6.25, 10.5, 0.8, ["; ".join(f"{k.replace('_', ' ')} recommends "
                                                      f"{self.B.alt(v['recommended']).name or v['recommended']}"
                                                      for k, v in profs if v["recommended"]) + ".",
                                            "What-if axes: NPV and P10 relative to the best scenario; the rest are "
                                            "absolute probabilities and ratios. Each scenario is re-optimized and "
                                            "re-simulated."], size=9, color=TEXT_2)
        self.manifest["A7"] = {"profiles": {k: v["weights"] for k, v in profs},
                               "scenarios": {k: v["values"] for k, v in sc}}

    def build(self, path: str) -> Dict[str, Dict]:
        from .radar import radar_profiles
        self.RD = radar_profiles(self.B)
        steps = [self.s1_decision, self.s2_opportunity, self.s3_constraints, self.s4_frontier, self.s5_choices,
                 self.s6_radar, self.s7_risk, self.s8_execution, self.s9_lob, self.s10_recommended, self.s11_whatif,
                 self.s12_decisions, self.a1_solvers, self.a2_model, self.a3_uncertainty, self.a4_sensitivity,
                 self.a5_risk_radar, self.a6_kpi_radar, self.a7_appetite_whatif_radar]
        for k, fn in enumerate(steps, 1):
            fn(k)
        self.prs.save(path)
        return self.manifest


def build_ceo_deck(bundle, path: str) -> Dict[str, Dict]:
    return CEODeckBuilder(bundle).build(path)
