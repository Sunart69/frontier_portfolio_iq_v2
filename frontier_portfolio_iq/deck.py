"""CEO / LT PowerPoint generator. Every figure is read from the AnalysisBundle (solver output);
the generator returns a manifest of what it placed so tests can reconcile slides against the solver."""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional

from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LABEL_POSITION, XL_LEGEND_POSITION
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Emu, Inches, Pt

from .analysis import (AnalysisBundle, SPIDER_DIMENSIONS, candidate_investment, capability_supply_demand,
                       capacity_by_type, fungibility_ladder, spider_scores, total_budget)
from .domain import ENTERPRISE_UNALLOCATED
from .style import (CATEGORICAL, GRID, INK, INK_SOFT, MUTED, STATUS, TEXT, TEXT_2, lob_colors, money,
                    pct, signed_money)

HEAD, BODY = "Cambria", "Calibri"
W, H = 13.333, 7.5


def _rgb(h: str) -> RGBColor:
    return RGBColor.from_string(h)


class DeckBuilder:
    def __init__(self, b: AnalysisBundle):
        self.b = b
        self.prs = Presentation()
        self.prs.slide_width, self.prs.slide_height = Inches(W), Inches(H)
        self.blank = self.prs.slide_layouts[6]
        self.manifest: Dict[int, Dict] = {}
        self.d = b.data
        self.lobs = self.d.business_lobs()
        self.colors = lob_colors(self.lobs + [l for l in self.d.lobs if l not in self.lobs])
        self.cm = b.tier_metrics["committed"]

    # ------------------------------------------------------------------ primitives
    def slide(self, dark: bool = False):
        s = self.prs.slides.add_slide(self.blank)
        bg = s.background.fill
        bg.solid()
        bg.fore_color.rgb = _rgb(INK if dark else "FFFFFF")
        return s

    def text(self, s, x, y, w, h, runs, size=14, color=TEXT, bold=False, font=BODY, align=PP_ALIGN.LEFT,
             anchor=MSO_ANCHOR.TOP, name: Optional[str] = None, para_space: int = 6):
        tb = s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
        if name:
            tb.name = name
        tf = tb.text_frame
        tf.word_wrap = True
        tf.vertical_anchor = anchor
        tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
        paras = runs if isinstance(runs, list) else [runs]
        for i, ptxt in enumerate(paras):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            p.alignment = align
            items = ptxt if isinstance(ptxt, tuple) else ((ptxt, {}),)
            for t, opt in items:
                r = p.add_run()
                r.text = t
                f = r.font
                f.size = Pt(opt.get("size", size))
                f.bold = opt.get("bold", bold)
                f.name = opt.get("font", font)
                f.color.rgb = _rgb(opt.get("color", color))
            if len(paras) > 1:
                p.space_after = Pt(para_space)
        return tb

    def title(self, s, n: int, text: str, kicker: str, dark: bool = False):
        self.text(s, 0.6, 0.35, 12.1, 0.3, kicker.upper(), size=11, bold=True,
                  color="FAB219" if dark else CATEGORICAL[0], name="Kicker")
        self.text(s, 0.6, 0.65, 12.1, 1.0, text, size=26, bold=True, font=HEAD,
                  color="FFFFFF" if dark else INK, name="Title")
        self.footer(s, n, dark)

    def footer(self, s, n: int, dark: bool = False):
        self.text(s, 0.6, 7.05, 10.5, 0.3,
                  f"Source: Frontier Portfolio IQ optimization run {self.b.run_id} · committed portfolio unless "
                  f"labeled · $ figures in USD", size=9, color="AEB6C4" if dark else MUTED, name="Footer")
        self.text(s, 11.9, 7.05, 0.8, 0.3, str(n), size=9, color="AEB6C4" if dark else MUTED,
                  align=PP_ALIGN.RIGHT, name="SlideNumber")

    def card(self, s, x, y, w, h, fill="F4F5F7", line=None):
        shp = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
        shp.adjustments[0] = 0.06
        shp.fill.solid()
        shp.fill.fore_color.rgb = _rgb(fill)
        if line:
            shp.line.color.rgb = _rgb(line)
        else:
            shp.line.fill.background()
        shp.shadow.inherit = False
        return shp

    def stat(self, s, x, y, w, big, label, dark=False, big_color=None):
        self.card(s, x, y, w, 1.55, fill=INK_SOFT if dark else "F4F5F7")
        self.text(s, x + 0.25, y + 0.18, w - 0.5, 0.75, big, size=34, bold=True, font=HEAD,
                  color=big_color or ("FFFFFF" if dark else INK), name="StatValue")
        self.text(s, x + 0.25, y + 0.95, w - 0.5, 0.5, label, size=12,
                  color="D5DAE3" if dark else TEXT_2, name="StatLabel")

    def chart(self, s, kind, cats, series, x, y, w, h, colors, fmt='"$"#,##0.0,"M"', legend=True,
              labels=False, title=None, gap=60, overlap=None):
        cd = CategoryChartData()
        cd.categories = cats
        for name, vals in series:
            cd.add_series(name, [float(v) for v in vals])
        gf = s.shapes.add_chart(kind, Inches(x), Inches(y), Inches(w), Inches(h), cd)
        ch = gf.chart
        ch.font.size = Pt(11)
        ch.font.name = BODY
        ch.font.color.rgb = _rgb(TEXT_2)
        ch.has_title = bool(title)
        if title:
            ch.chart_title.text_frame.text = title
            tp = ch.chart_title.text_frame.paragraphs[0]
            tp.runs[0].font.size = Pt(12)
            tp.runs[0].font.bold = True
            tp.runs[0].font.color.rgb = _rgb(TEXT)
        ch.has_legend = legend and len(series) > 1
        if ch.has_legend:
            ch.legend.position = XL_LEGEND_POSITION.BOTTOM
            ch.legend.include_in_layout = False
            ch.legend.font.size = Pt(11)
        plot = ch.plots[0]
        if hasattr(plot, "gap_width"):
            plot.gap_width = gap
        if overlap is not None and hasattr(plot, "overlap"):
            plot.overlap = overlap
        for i, ser in enumerate(plot.series):
            fill = ser.format.fill
            if kind in (XL_CHART_TYPE.RADAR, XL_CHART_TYPE.RADAR_MARKERS, XL_CHART_TYPE.LINE):
                ser.format.line.color.rgb = _rgb(colors[i % len(colors)])
                ser.format.line.width = Pt(2.25)
                ser.smooth = False
            else:
                fill.solid()
                fill.fore_color.rgb = _rgb(colors[i % len(colors)])
        if labels:
            plot.has_data_labels = True
            dl = plot.data_labels
            dl.number_format = fmt
            dl.number_format_is_linked = False
            dl.font.size = Pt(11)
            dl.font.color.rgb = _rgb(TEXT)
            if kind in (XL_CHART_TYPE.COLUMN_CLUSTERED, XL_CHART_TYPE.BAR_CLUSTERED):
                dl.position = XL_LABEL_POSITION.OUTSIDE_END
        try:
            va = ch.value_axis
            va.has_major_gridlines = True
            va.major_gridlines.format.line.color.rgb = _rgb(GRID)
            va.format.line.fill.background()
            va.tick_labels.number_format = fmt
            va.tick_labels.number_format_is_linked = False
            va.tick_labels.font.size = Pt(10)
            if kind not in (XL_CHART_TYPE.RADAR,):
                va.minimum_scale = 0
            ca = ch.category_axis
            ca.format.line.color.rgb = _rgb(GRID)
            ca.tick_labels.font.size = Pt(11)
            ca.has_major_gridlines = False
        except (ValueError, AttributeError):
            pass
        return ch

    def table(self, s, rows, x, y, w, col_w, font=11, header_fill=INK, row_h=0.36, status_col=None):
        shp = s.shapes.add_table(len(rows), len(rows[0]), Inches(x), Inches(y), Inches(w),
                                 Inches(row_h * len(rows)))
        tbl = shp.table
        for j, cw in enumerate(col_w):
            tbl.columns[j].width = Inches(cw)
        for i, r in enumerate(rows):
            tbl.rows[i].height = Inches(row_h)
            for j, v in enumerate(r):
                c = tbl.cell(i, j)
                c.margin_left = c.margin_right = Inches(0.08)
                c.margin_top = c.margin_bottom = Inches(0.03)
                c.vertical_anchor = MSO_ANCHOR.MIDDLE
                tf = c.text_frame
                tf.word_wrap = True
                tf.paragraphs[0].text = ""
                run = tf.paragraphs[0].add_run()
                run.text = str(v)
                run.font.size = Pt(font)
                run.font.name = BODY
                c.fill.solid()
                if i == 0:
                    c.fill.fore_color.rgb = _rgb(header_fill)
                    run.font.bold = True
                    run.font.color.rgb = _rgb("FFFFFF")
                else:
                    c.fill.fore_color.rgb = _rgb("FFFFFF" if i % 2 else "F4F5F7")
                    run.font.color.rgb = _rgb(TEXT)
                    if status_col is not None and j == status_col:
                        v_ = str(v)
                        run.font.bold = True
                        run.font.color.rgb = _rgb("0A7A0A" if v_.startswith("Committed") else
                                                  "8A5A00" if v_.startswith("Conditional") else
                                                  "B02A2A" if v_.startswith("Infeasible") else TEXT_2)
        return tbl

    def lob_name(self, l):
        if l == ENTERPRISE_UNALLOCATED:
            return "Enterprise-wide"
        return self.d.lobs[l].name if l in self.d.lobs else l

    # ------------------------------------------------------------------ slides
    def s1_decision(self):
        b, cm = self.b, self.cm
        s = self.slide(dark=True)
        cond = b.tier_metrics["conditional"]
        unc = b.tier_metrics["unconstrained"]
        self.text(s, 0.6, 0.6, 12, 0.35, "EXECUTIVE INVESTMENT DECISION", size=12, bold=True, color="FAB219")
        self.text(s, 0.6, 1.05, 11.8, 1.6,
                  "Which combination of initiatives maximizes enterprise value while protecting critical "
                  "LOB commitments?", size=32, bold=True, font=HEAD, color="FFFFFF", name="Title")
        n_must = sum(1 for p in self.d.projects.values() if p.mandatory or (p.critical and self.d.settings.protect_critical))
        self.text(s, 0.6, 2.95, 11.8, 0.8,
                  f"The committed portfolio funds {cm['n_selected']} of {len(self.d.projects)} initiatives and "
                  f"protects all {n_must} mandatory and critical commitments, using only approved budgets and capacity.",
                  size=16, color="D5DAE3")
        cond_gain = cond["net_value"] - cm["net_value"]
        unc_gap = unc["net_value"] - cm["net_value"]
        stats = [(money(cm["net_value"]), "Committed net enterprise value (time-adjusted)"),
                 (money(cm["investment"]), "Committed investment across LOBs"),
                 (signed_money(cond_gain), "Additional value if pending approvals are granted"),
                 (signed_money(unc_gap), "Value held by current restrictions (analytical)")]
        for i, (v, lab) in enumerate(stats):
            self.stat(s, 0.6 + i * 3.05, 4.1, 2.85, v, lab, dark=True,
                      big_color="FAB219" if i == 2 else None)
        self.text(s, 0.6, 6.0, 12, 0.6, "Conditional and analytical figures are not approved commitments; "
                  "they show the value of decisions leadership has not yet made.", size=12, color="AEB6C4")
        self.footer(s, 1, dark=True)
        self.manifest[1] = {"committed_net_value": money(cm["net_value"]),
                            "committed_investment": money(cm["investment"]),
                            "conditional_gain": signed_money(cond_gain), "unconstrained_gap": signed_money(unc_gap)}

    def s2_tension(self):
        b = self.b
        s = self.slide()
        sd = capability_supply_demand(b)
        cand, bud = candidate_investment(b), total_budget(b)
        short = [c for c, v in sd.items() if v["demand"] > v["available"]]
        if cand > bud:
            t2 = (f"Enterprise ambition exceeds funding and specialized capacity: candidates need "
                  f"{money(cand)} against {money(bud)} of budget")
        else:
            t2 = (f"Specialized capacity, not total funding, binds: demand exceeds supply in {len(short)} of "
                  f"{len(sd)} capabilities while {money(bud - self.cm['investment'])} of budget goes unspent")
        self.title(s, 2, t2, "The strategic tension")
        cats = list(sd)
        self.chart(s, XL_CHART_TYPE.BAR_CLUSTERED, cats,
                   [("Candidate demand (hours)", [sd[c]["demand"] for c in cats]),
                    ("Available capacity (hours)", [sd[c]["available"] for c in cats])],
                   0.6, 1.85, 8.0, 4.9, [CATEGORICAL[1], CATEGORICAL[0]], fmt='#,##0', gap=50,
                   title="Annual capability demand vs. supply (hours)")
        if cand > bud:
            self.stat(s, 9.0, 1.95, 3.7, money(cand - bud), "Funding gap if every candidate were selected")
        else:
            self.stat(s, 9.0, 1.95, 3.7, money(bud - self.cm["investment"]),
                      "Budget unspent: capacity, timing and funding restrictions bind first")
        self.stat(s, 9.0, 3.7, 3.7, f"{len(short)} of {len(sd)}", "Capabilities where demand exceeds supply")
        self.text(s, 9.0, 5.45, 3.7, 1.3,
                  "Supply counts every pool, including ring-fenced capacity that only its owning LOB may use, "
                  "so usable supply for any single LOB is lower.", size=12, color=TEXT_2)
        self.manifest[2] = {"chart_categories": cats, "demand": [sd[c]["demand"] for c in cats],
                            "available": [sd[c]["available"] for c in cats], "candidate_investment": money(cand),
                            "budget": money(bud)}

    def s3_value(self):
        cm = self.cm
        s = self.slide()
        keys = self.lobs + ["ENT", ENTERPRISE_UNALLOCATED]
        cats = [self.lob_name(k) for k in keys]
        val = [cm["lob_value"].get(k, 0.0) for k in keys]
        inv = [cm["investment_by_lob"].get(k, 0.0) for k in keys]
        top = max(self.lobs, key=lambda l: cm["lob_value"].get(l, 0.0))
        self.title(s, 3, f"{self.lob_name(top)} realizes the most value; shared platforms add "
                         f"{money(cm['lob_value'].get(ENTERPRISE_UNALLOCATED, 0))} of enterprise-wide benefit counted once",
                   "Where business value is created")
        self.chart(s, XL_CHART_TYPE.COLUMN_CLUSTERED, cats,
                   [("Business value attributed", val), ("Investment funded", inv)],
                   0.6, 1.85, 8.4, 4.95, [CATEGORICAL[0], CATEGORICAL[3]], labels=True, gap=70, overlap=-10,
                   title="Committed portfolio by LOB")
        self.text(s, 9.4, 1.95, 3.3, 4.8, [
            (("Benefit attribution", {"bold": True, "size": 14, "color": INK}),),
            "Cross-LOB initiatives split benefit across the LOBs that realize it; only the remainder is "
            "booked as enterprise-wide, so LOB values sum exactly to enterprise value.",
            (("Funding", {"bold": True, "size": 14, "color": INK}),),
            "Shared initiatives are funded by contribution shares; Enterprise IT carries platform capex.",
        ], size=13, color=TEXT_2)
        self.manifest[3] = {"categories": cats, "value": val, "investment": inv}

    def s4_capacity(self):
        b = self.b
        s = self.slide()
        ct = capacity_by_type(b)
        cats = list(ct)
        cap = b.capacity
        self.title(s, 4, f"{cap['idle_ring_fenced_hours']:,.0f} ring-fenced hours sit idle while "
                         f"{cap['restricted_unavailable_hours']:,.0f} hours of other-LOB demand cannot reach them",
                   "How capacity is constrained")
        series = [("Shared – allocated", [ct[c]["shared_allocated"] for c in cats]),
                  ("Shared – unused", [ct[c]["shared_unused"] for c in cats]),
                  ("Ring-fenced / conditional – allocated", [ct[c]["restricted_allocated"] for c in cats]),
                  ("Ring-fenced / conditional – idle", [ct[c]["restricted_unused"] for c in cats])]
        self.chart(s, XL_CHART_TYPE.BAR_STACKED, cats, series, 0.6, 1.85, 8.4, 4.95,
                   [CATEGORICAL[0], "A9C7EE", CATEGORICAL[1], "F5B79C"], fmt='#,##0', gap=55, overlap=100,
                   title="Annual hours by capability and ownership")
        self.stat(s, 9.4, 1.95, 3.3, pct(self.cm["shared_utilization"]), "Shared pool utilization")
        self.stat(s, 9.4, 3.65, 3.3, pct(self.cm["ring_fenced_utilization"]),
                  "Ring-fenced and conditional utilization")
        self.text(s, 9.4, 5.35, 3.3, 1.5,
                  "Ring-fencing is not inherently inefficient: some pools are restricted for regulatory, "
                  "domain-expertise, service or funding reasons.", size=12, color=TEXT_2)
        self.manifest[4] = {"categories": cats, "series": {n: v for n, v in series},
                            "idle_rf": round(cap["idle_ring_fenced_hours"]),
                            "restricted_unavailable": round(cap["restricted_unavailable_hours"])}

    def _choices(self):
        b = self.b
        rows = [("Committed", b.tier_metrics["committed"], "Committed")]
        for key, name in [("balanced", "LOB-balanced"), ("release_20", "Ring-fence release 20%")]:
            sc = b.scenario(key)
            if sc.metrics.get("feasible"):
                rows.append((name, sc.metrics, sc.status_label.split(" —")[0].replace(" basis", "")))
        if b.tier_metrics["conditional"].get("feasible"):
            rows.append(("Conditional (all pending)", b.tier_metrics["conditional"], "Conditional"))
        if b.tier_metrics["unconstrained"].get("feasible"):
            rows.append(("Unconstrained", b.tier_metrics["unconstrained"], "Analytical"))
        return rows

    def s5_choices(self):
        s = self.slide()
        rows = self._choices()
        cm = self.cm
        rel = next((m for n, m, _ in rows if n.startswith("Ring-fence")), None)
        bal = next((m for n, m, _ in rows if n == "LOB-balanced"), None)
        title = "Balancing LOBs costs little; releasing ring-fenced capacity is the larger lever"
        if rel and bal:
            title = (f"Balancing LOBs costs {money(max(0, cm['net_value'] - bal['net_value']))}; a 20% ring-fence "
                     f"release adds {money(max(0, rel['net_value'] - cm['net_value']))}, subject to approval")
        self.title(s, 5, title, "The portfolio choices")
        self.chart(s, XL_CHART_TYPE.COLUMN_CLUSTERED, [r[0] for r in rows],
                   [("Net enterprise value", [r[1]["net_value"] for r in rows])],
                   0.6, 1.85, 6.2, 4.95, [CATEGORICAL[0]], labels=True, legend=False, gap=60,
                   title="Net enterprise value by portfolio")
        tbl = [["Portfolio", "Status", "Investment", "Projects", "Compl."]]
        for n, m, st in rows:
            tbl.append([n, st, money(m["investment"]), str(m["n_selected"]), pct(m["compliance_coverage"])])
        self.table(s, tbl, 7.1, 2.0, 5.6, [1.9, 1.05, 1.0, 0.75, 0.9], font=11, status_col=1, row_h=0.45)
        self.text(s, 7.1, 2.0 + 0.45 * len(tbl) + 0.25, 5.6, 1.2,
                  "Only the committed portfolio is executable today. Conditional rows require LOB approvals; "
                  "unconstrained is an analytical ceiling.", size=12, color=TEXT_2)
        self.manifest[5] = {"portfolios": [r[0] for r in rows], "net_value": [r[1]["net_value"] for r in rows]}

    def s6_spider(self):
        s = self.slide()
        sc = spider_scores(self.b)
        com = sc["Committed"]
        lead = [dname.lower().replace("lob ", "LOB ") for dname, v in zip(SPIDER_DIMENSIONS, com) if v >= 99.95]
        lag = sorted(zip(com, SPIDER_DIMENSIONS))[:2]
        t6 = "The committed portfolio "
        t6 += (f"leads on {' and '.join(lead[:2])}" if lead else "is balanced across dimensions")
        t6 += f" but trails on {lag[0][1].lower().replace('lob ', 'LOB ')} and {lag[1][1].lower().replace('lob ', 'LOB ')}" if lag[0][0] < 99.95 else ""
        self.title(s, 6, t6, "Strategic spider diagram")
        names = list(sc)
        self.chart(s, XL_CHART_TYPE.RADAR, SPIDER_DIMENSIONS, [(n, sc[n]) for n in names],
                   0.6, 1.8, 7.6, 5.1, CATEGORICAL, fmt='0', title="Index: best portfolio on each axis = 100")
        self.text(s, 8.6, 1.95, 4.1, 4.9, [
            (("How to read", {"bold": True, "size": 14, "color": INK}),),
            "Business value is net, time-adjusted enterprise value. Financial efficiency is value per dollar "
            "invested. LOB outcome coverage is the share of LOB KPIs advanced by at least one funded initiative.",
            (("Committed scores", {"bold": True, "size": 14, "color": INK}),),
            ", ".join(f"{d}: {v:.0f}" for d, v in zip(SPIDER_DIMENSIONS, sc["Committed"])),
        ], size=12, color=TEXT_2)
        self.manifest[6] = {"scores": sc}

    def s7_fungibility(self):
        b = self.b
        s = self.slide()
        lad = fungibility_ladder(b)
        auth = b.tier_metrics["committed"]["net_value"] - b.tier_metrics["strict"]["net_value"]
        rel = b.release_values[:6]
        top = rel[0] if rel else None
        t = f"Already-authorized sharing adds {money(max(0, auth))}"
        if top:
            t += f"; releasing 30% of {top['pool']} alone would add {money(top['incremental_value'])}"
        self.title(s, 7, t, "The value of fungibility")
        self.chart(s, XL_CHART_TYPE.COLUMN_CLUSTERED, [l for l, _ in lad], [("Net value", [v for _, v in lad])],
                   0.6, 1.85, 6.1, 4.95, [CATEGORICAL[0]], labels=True, legend=False,
                   title="Net enterprise value as sharing widens")
        if rel:
            self.chart(s, XL_CHART_TYPE.BAR_CLUSTERED, [f"{r['pool']} ({self.lob_name(r['owner'])})" for r in rel][::-1],
                       [("Incremental value", [r["incremental_value"] for r in rel][::-1])],
                       7.0, 1.85, 5.7, 4.1, [CATEGORICAL[2]], labels=True, legend=False,
                       title="Value of releasing 30% of one pool")
        self.text(s, 7.0, 6.05, 5.7, 0.8, "Each release requires the owning LOB's approval and is shown as "
                  "conditional value, not a commitment.", size=12, color=TEXT_2)
        self.manifest[7] = {"ladder": [v for _, v in lad], "release": [r["incremental_value"] for r in rel],
                            "authorized_sharing": money(max(0, auth))}

    def s8_budget(self):
        cm = self.cm
        s = self.slide()
        months = self.d.months
        labels = [self.d.settings.month_label(t)[:3] for t in months]
        keys = [l for l in self.lobs + ["ENT"] if l in cm["investment_by_lob_month"]]
        peak_t = max(months, key=lambda t: cm["investment_by_month"][t])
        self.title(s, 8, f"Spend peaks at {money(cm['investment_by_month'][peak_t])} in "
                         f"{self.d.settings.month_label(peak_t)}; every start date fits monthly LOB budgets",
                   "Budget and execution trade-offs")
        series = [(self.lob_name(l), [cm["investment_by_lob_month"][l][t] for t in months]) for l in keys]
        self.chart(s, XL_CHART_TYPE.COLUMN_STACKED, labels, series, 0.6, 1.85, 6.0, 4.95,
                   [self.colors[l] for l in keys], gap=40, overlap=100, title="Monthly investment by LOB")
        # Gantt of committed initiatives
        x0, y0, wg = 7.0, 1.95, 5.7
        lab_w = 1.9
        cw = (wg - lab_w) / len(months)
        for k, t in enumerate(months):
            self.text(s, x0 + lab_w + k * cw, y0, cw, 0.25, labels[k][0], size=9, color=MUTED, align=PP_ALIGN.CENTER)
        sel = sorted(cm["selected"], key=lambda p: (cm["starts"][p], p))
        rh = min(0.32, 4.4 / max(1, len(sel)))
        for r, pid in enumerate(sel):
            y = y0 + 0.3 + r * rh
            p = self.d.projects[pid]
            self.text(s, x0, y, lab_w - 0.1, rh, f"{pid} {p.name}", size=9, color=TEXT, anchor=MSO_ANCHOR.MIDDLE)
            bar = s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE,
                                     Inches(x0 + lab_w + (cm["starts"][pid] - 1) * cw), Inches(y + rh * 0.18),
                                     Inches(cw * p.duration), Inches(rh * 0.64))
            bar.fill.solid()
            bar.fill.fore_color.rgb = _rgb(self.colors.get(p.primary_lob, MUTED))
            bar.line.fill.background()
            bar.name = f"Gantt {pid}"
        self.manifest[8] = {"series": {n: v for n, v in series}, "starts": dict(cm["starts"])}

    def s9_commitments(self):
        cm = self.cm
        s = self.slide()
        self.title(s, 9, "Each LOB leaves with funded initiatives, KPI outcomes, delivery dates and a named owner",
                   "Business stakeholder commitments")
        rows = [["LOB", "Accountable owner", "Investment", "Value", "KPI outcome attainment", "Delivers"]]
        for l in self.lobs:
            ta = [t for t in cm["target_attainment"] if t["lob"] == l]
            att = "; ".join(f"{t['kpi'].split(' ', 1)[-1]} {pct(min(t['attainment'] or 0, 9.99))}" for t in ta) or "—"
            ends = sorted((cm["ends"][p], p) for p in cm["selected_by_lob"].get(l, []))
            dl = ", ".join(f"{p} {self.d.settings.month_label(e)[:3]}" for e, p in ends)
            rows.append([self.d.lobs[l].name, self.d.lobs[l].outcome_owner, money(cm["investment_by_lob"].get(l, 0)),
                         money(cm["lob_value"].get(l, 0)), att, dl])
        rows.append(["Enterprise IT", self.d.lobs["ENT"].outcome_owner if "ENT" in self.d.lobs else "",
                     money(cm["investment_by_lob"].get("ENT", 0)),
                     money(cm["lob_value"].get(ENTERPRISE_UNALLOCATED, 0)), "Enterprise-wide platform benefit",
                     ", ".join(f"{p} {self.d.settings.month_label(cm['ends'][p])[:3]}"
                               for p in sorted(cm["selected_by_lob"].get("ENT", [])))])
        self.table(s, rows, 0.6, 1.95, 12.1, [1.7, 1.4, 1.1, 1.0, 3.5, 3.4], font=12, row_h=0.85)
        self.text(s, 0.6, 6.35, 12.1, 0.6, "Attainment = attributed benefit of funded initiatives ÷ LOB KPI target. "
                  "Delivery month = planned completion in the committed schedule.", size=11, color=TEXT_2)
        self.manifest[9] = {"investment": {l: money(cm["investment_by_lob"].get(l, 0)) for l in self.lobs},
                            "value": {l: money(cm["lob_value"].get(l, 0)) for l in self.lobs}}

    def s10_whatif(self):
        b = self.b
        s = self.slide()
        feas = [o for o in b.scenarios if o.metrics.get("feasible")]
        best = max(feas, key=lambda o: o.deltas.get("net_value", 0))
        worst = min(feas, key=lambda o: o.deltas.get("net_value", 0))
        self.title(s, 10, f"Largest upside: {best.scenario.name.lower()} ({signed_money(best.deltas['net_value'])}); "
                          f"largest downside: {worst.scenario.name.lower()} ({signed_money(worst.deltas['net_value'])})",
                   "Executive what-if decisions")
        rows = [["#", "Scenario", "Status", "Δ Net value", "Δ Investment", "Added", "Removed"]]
        for o in b.scenarios:
            if not o.metrics.get("feasible"):
                rows.append([o.scenario.sid, o.scenario.name, "Infeasible", "—", "—", "—", "—"])
                continue
            dl = o.deltas
            rows.append([o.scenario.sid, o.scenario.name, o.status_label.split(" —")[0],
                         signed_money(dl["net_value"]), signed_money(dl["investment"]),
                         ", ".join(dl["projects_added"]) or "—", ", ".join(dl["projects_removed"]) or "—"])
        self.table(s, rows, 0.6, 1.8, 12.1, [0.4, 4.3, 1.6, 1.4, 1.4, 1.5, 1.5], font=11, row_h=0.39, status_col=2)
        self.manifest[10] = {"delta_net_value": {o.scenario.sid: signed_money(o.deltas["net_value"])
                                                 for o in feas}}

    def s11_recommended(self):
        b = self.b
        s = self.slide()
        groups = defaultdict(list)
        for pid, c in b.classification.items():
            groups[c].append(pid)
        n_app = len(groups["Approved"])
        self.title(s, 11, f"Approve {n_app} initiatives now; {len(groups['Conditional'])} more become fundable "
                          f"once capacity approvals are granted", "Recommended portfolio")
        cols = ["Approved", "Conditional", "Deferred", "Rejected"]
        sub = {"Approved": "Committed: executable with current budgets and capacity",
               "Conditional": "Needs transfer or release approval",
               "Deferred": "Only if restrictions are relaxed (analytical)",
               "Rejected": "Not selected in any portfolio"}
        cw = 2.9
        for k, c in enumerate(cols):
            x = 0.6 + k * (cw + 0.17)
            self.card(s, x, 1.85, cw, 4.95, fill="F4F5F7")
            dot = s.shapes.add_shape(MSO_SHAPE.OVAL, Inches(x + 0.25), Inches(2.05), Inches(0.22), Inches(0.22))
            dot.fill.solid()
            dot.fill.fore_color.rgb = _rgb(STATUS[c])
            dot.line.fill.background()
            self.text(s, x + 0.6, 1.98, cw - 0.8, 0.4, f"{c} ({len(groups[c])})", size=16, bold=True, color=INK)
            self.text(s, x + 0.25, 2.45, cw - 0.5, 0.5, sub[c], size=11, color=TEXT_2)
            items = [f"{pid}  {b.data.projects[pid].name}" for pid in sorted(groups[c])]
            vals = sum(b.data.projects[pid].value for pid in groups[c])
            self.text(s, x + 0.25, 3.0, cw - 0.5, 3.2, items or ["—"], size=10.5 if len(items) > 10 else 11,
                      color=TEXT, para_space=1 if len(items) > 10 else 4)
            self.text(s, x + 0.25, 6.3, cw - 0.5, 0.35, f"Gross value {money(vals)}", size=11, bold=True, color=INK)
        self.manifest[11] = {c: sorted(groups[c]) for c in cols}

    def s12_decisions(self):
        b = self.b
        dec = b.decisions
        s = self.slide(dark=True)
        self.title(s, 12, f"Approving the pending capacity releases unlocks {money(max(0, dec['conditional_gain']))} "
                          "of conditional value while every mandatory LOB commitment stays funded",
                   "Decisions required", dark=True)
        rel = [f"{r['owner']}: release {r['pool']} to {r['to']} ({r['hours_used']:,.0f} h used)"
               for r in dec["capacity_releases"]][:5]
        fund = [f"{f['decision']} ({signed_money(f['value_delta'])})" for f in dec["funding"]][:4]
        com = [f"{c['lob']}: {money(c['investment'])} for {money(c['value'])} — owner {c['owner']}"
               for c in dec["commitments"]]
        trade = [f"{t['topic']} ({signed_money(t['value_delta'])})" for t in dec["trade_offs"]][:4]
        quads = [("Capacity release approvals", rel), ("Funding approvals", fund),
                 ("LOB commitments to confirm", com), ("Unresolved trade-offs", trade)]
        for k, (head, items) in enumerate(quads):
            x = 0.6 + (k % 2) * 6.15
            y = 1.85 + (k // 2) * 2.55
            self.card(s, x, y, 5.95, 2.4, fill=INK_SOFT)
            self.text(s, x + 0.25, y + 0.15, 5.4, 0.4, head, size=15, bold=True, color="FAB219")
            self.text(s, x + 0.25, y + 0.6, 5.45, 1.75, items or ["None required"], size=11, color="FFFFFF")
        self.manifest[12] = {"conditional_gain": money(max(0, dec["conditional_gain"]))}

    def build(self, path: str) -> Dict[int, Dict]:
        for fn in (self.s1_decision, self.s2_tension, self.s3_value, self.s4_capacity, self.s5_choices,
                   self.s6_spider, self.s7_fungibility, self.s8_budget, self.s9_commitments, self.s10_whatif,
                   self.s11_recommended, self.s12_decisions):
            fn()
        self.prs.core_properties.title = "Frontier Portfolio IQ — Enterprise Portfolio Decision"
        self.prs.save(path)
        return self.manifest


def build_deck(b: AnalysisBundle, path: str) -> Dict[int, Dict]:
    return DeckBuilder(b).build(path)
