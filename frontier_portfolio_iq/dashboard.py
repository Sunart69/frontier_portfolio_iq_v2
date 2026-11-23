"""Executive and business-stakeholder dashboards (self-contained HTML, inline SVG, hover tooltips).

LOB Portfolio Allocation Dashboard + LOB Trade-Off Matrix. All figures come from the AnalysisBundle.
"""
from __future__ import annotations

import html
from typing import Dict, List, Sequence, Tuple

from .analysis import (AnalysisBundle, SPIDER_DIMENSIONS, capacity_by_type, fungibility_ladder,
                       spider_scores)
from .domain import ENTERPRISE_UNALLOCATED
from .style import CATEGORICAL, STATUS, lob_colors, money, pct, signed_money

E = html.escape


# --------------------------------------------------------------------------- SVG primitives
def hbars(rows: Sequence[Tuple[str, Sequence[float]]], series: Sequence[Tuple[str, str]], fmt=money,
          stacked=False, width=560, label_w=170, bar_h=14, gap=10) -> str:
    """Horizontal grouped/stacked bars with per-mark hover tooltips and selective end labels."""
    n = len(series)
    group_h = bar_h if stacked else bar_h * n + 2 * (n - 1)
    h = len(rows) * (group_h + gap) + 8
    vmax = max([sum(v) if stacked else max(v) for _, v in rows] + [1e-9])
    plot_w = width - label_w - 70
    out = [f'<svg viewBox="0 0 {width} {h}" width="100%" role="img">']
    for i, (lab, vals) in enumerate(rows):
        y0 = 4 + i * (group_h + gap)
        out.append(f'<text x="{label_w - 8}" y="{y0 + group_h / 2 + 4}" text-anchor="end" class="ax">{E(lab)}</text>')
        x = label_w
        for k, ((sname, color), v) in enumerate(zip(series, vals)):
            w = plot_w * v / vmax if vmax else 0
            y = y0 if stacked else y0 + k * (bar_h + 2)
            bx = x if stacked else label_w
            out.append(f'<rect x="{bx:.1f}" y="{y}" width="{max(w, 0):.1f}" height="{bar_h}" rx="3" '
                       f'fill="#{color}" class="mark"><title>{E(lab)} · {E(sname)}: {E(fmt(v))}</title></rect>')
            if stacked:
                x += w + (1 if w > 0 else 0)
            else:
                out.append(f'<text x="{bx + w + 6:.1f}" y="{y + bar_h - 3}" class="val">{E(fmt(v))}</text>')
        if stacked:
            out.append(f'<text x="{x + 6:.1f}" y="{y0 + bar_h - 3}" class="val">{E(fmt(sum(vals)))}</text>')
    out.append("</svg>")
    return "".join(out)


def legend(series: Sequence[Tuple[str, str]]) -> str:
    return '<div class="legend">' + "".join(
        f'<span><i style="background:#{c}"></i>{E(n)}</span>' for n, c in series) + "</div>"


def heatmap(rows: List[Tuple[str, List[float]]], cols: List[str], width=1080) -> str:
    """Sequential single-hue utilization heatmap (0 → 100%+); empty cells = no capacity that month."""
    label_w, cw, ch = 170, (width - 170 - 10) / max(1, len(cols)), 22
    h = 24 + len(rows) * (ch + 3)
    out = [f'<svg viewBox="0 0 {width} {h}" width="100%" role="img">']
    for j, c in enumerate(cols):
        out.append(f'<text x="{label_w + j * cw + cw / 2:.1f}" y="14" text-anchor="middle" class="ax">{E(c)}</text>')
    for i, (lab, vals) in enumerate(rows):
        y = 22 + i * (ch + 3)
        out.append(f'<text x="{label_w - 8}" y="{y + 15}" text-anchor="end" class="ax">{E(lab)}</text>')
        for j, v in enumerate(vals):
            x = label_w + j * cw
            if v is None:
                out.append(f'<rect x="{x:.1f}" y="{y}" width="{cw - 3:.1f}" height="{ch}" rx="3" class="na">'
                           f'<title>{E(lab)} · {E(cols[j])}: no capacity</title></rect>')
                continue
            a = min(1.0, v)
            out.append(f'<rect x="{x:.1f}" y="{y}" width="{cw - 3:.1f}" height="{ch}" rx="3" fill="#2A78D6" '
                       f'fill-opacity="{0.08 + 0.92 * a:.2f}" class="mark"><title>{E(lab)} · {E(cols[j])}: '
                       f'{v * 100:.0f}% utilized</title></rect>')
            if v >= 0.95:
                out.append(f'<text x="{x + (cw - 3) / 2:.1f}" y="{y + 15}" text-anchor="middle" class="hot">●</text>')
    out.append("</svg>")
    return "".join(out)


def radar(scores: Dict[str, List[float]], dims: List[str], size=460) -> str:
    import math
    cx = cy = size / 2
    r = size / 2 - 105
    n = len(dims)
    pt = lambda k, v: (cx + r * v / 100 * math.sin(2 * math.pi * k / n),  # noqa: E731
                       cy - r * v / 100 * math.cos(2 * math.pi * k / n))
    out = [f'<svg viewBox="0 0 {size} {size}" width="100%" style="max-width:{size}px" role="img">']
    for ring in (25, 50, 75, 100):
        pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in (pt(k, ring) for k in range(n)))
        out.append(f'<polygon points="{pts}" class="grid"/>')
    for k, d in enumerate(dims):
        x, y = pt(k, 118)
        out.append(f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="middle" class="ax">{E(d)}</text>')
    for i, (name, vals) in enumerate(scores.items()):
        c = CATEGORICAL[i % len(CATEGORICAL)]
        pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in (pt(k, v) for k, v in enumerate(vals)))
        out.append(f'<polygon points="{pts}" fill="none" stroke="#{c}" stroke-width="2"/>')
        for k, v in enumerate(vals):
            x, y = pt(k, v)
            out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="#{c}" class="mark">'
                       f'<title>{E(name)} · {E(dims[k])}: {v:.0f}</title></circle>')
    out.append("</svg>")
    return "".join(out)


def table(header: List[str], rows: List[List[str]], cls="") -> str:
    h = "".join(f"<th>{E(x)}</th>" for x in header)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<table class="{cls}"><thead><tr>{h}</tr></thead><tbody>{body}</tbody></table>'


def badge(label: str) -> str:
    key = next((k for k in STATUS if label.startswith(k)), None)
    if label.startswith("Committed"):
        key = "Approved"
    color = STATUS.get(key, "8A8984")
    return f'<span class="badge"><i style="background:#{color}"></i>{E(label)}</span>'


def delta_cell(v: float) -> str:
    cls = "pos" if v > 0.5 else "neg" if v < -0.5 else "zero"
    return f'<span class="{cls}">{E(signed_money(v))}</span>'


# --------------------------------------------------------------------------- page
CSS = """
.viz-root{color-scheme:light;--surface:#fcfcfb;--card:#ffffff;--text:#0b0b0b;--text2:#52514e;--muted:#8a8984;
 --grid:#e4e3df;--pos:#1a6fd0;--neg:#c4501f;font-family:Calibri,'Segoe UI',system-ui,sans-serif;background:var(--surface);
 color:var(--text);margin:0;padding:28px 36px;max-width:1240px;margin:auto}
@media (prefers-color-scheme:dark){.viz-root{color-scheme:dark;--surface:#1a1a19;--card:#232322;--text:#f0efec;
 --text2:#c3c2b7;--muted:#9a9990;--grid:#3a3a37;--pos:#5aa2f0;--neg:#f08a5d}}
h1{font-family:Cambria,Georgia,serif;font-size:28px;margin:0 0 4px} h2{font-family:Cambria,Georgia,serif;
 font-size:19px;margin:0 0 4px} .sub{color:var(--text2);margin:0 0 18px}
.kicker{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#2a78d6;font-weight:700}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:18px}.grid4{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}
.card{background:var(--card);border:1px solid var(--grid);border-radius:10px;padding:16px 18px;margin-bottom:18px}
.tile .v{font-family:Cambria,Georgia,serif;font-size:30px;font-weight:700}.tile .l{color:var(--text2);font-size:13px}
.tile .t{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}
.ax{font-size:11px;fill:var(--text2)}.val{font-size:11px;fill:var(--text)}.hot{font-size:9px;fill:#fff}
.na{fill:var(--grid);opacity:.4}.grid{fill:none;stroke:var(--grid)}.mark:hover{opacity:.8}
.legend{display:flex;flex-wrap:wrap;gap:14px;font-size:12px;color:var(--text2);margin:6px 0}
.legend i,.badge i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:6px;vertical-align:-1px}
table{width:100%;border-collapse:collapse;font-size:13px}th{text-align:left;color:var(--text2);font-weight:600;
 border-bottom:1px solid var(--grid);padding:6px 8px;position:sticky;top:0;background:var(--card)}
td{border-bottom:1px solid var(--grid);padding:6px 8px;vertical-align:top}.num{text-align:right}
.pos{color:var(--pos);font-weight:600}.neg{color:var(--neg);font-weight:600}.zero{color:var(--muted)}
.note{font-size:12.5px;color:var(--text2);border-left:0;background:rgba(42,120,214,.07);padding:10px 12px;border-radius:8px}
.badge{white-space:nowrap}.scroll{overflow-x:auto}
.matrix{font-size:12px}.matrix td{white-space:nowrap}.matrix td:nth-child(2){white-space:normal;min-width:190px}.matrix td:last-child{white-space:normal;min-width:380px}.matrix ul{margin:0;padding-left:16px}
"""


def render_dashboard(b: AnalysisBundle, path: str) -> str:
    d, tm, cm = b.data, b.tier_metrics, b.tier_metrics["committed"]
    lobs = d.business_lobs()
    colors = lob_colors(lobs + [l for l in d.lobs if l not in lobs])
    name = lambda l: "Enterprise-wide (unallocated)" if l == ENTERPRISE_UNALLOCATED else d.lobs[l].name  # noqa
    parts: List[str] = []
    P = parts.append

    P(f'<div class="kicker">Frontier Portfolio IQ · run {E(b.run_id)}</div>')
    P("<h1>LOB Portfolio Allocation Dashboard</h1>")
    P('<p class="sub">Committed figures use only approved budgets and capacity. Conditional and unconstrained '
      'figures are labeled and are not approved commitments.</p>')

    cond_gain = tm["conditional"]["net_value"] - cm["net_value"]
    unc_gap = tm["unconstrained"]["net_value"] - cm["net_value"]
    auth_gain = cm["net_value"] - tm["strict"]["net_value"]
    tiles = [("Committed", money(cm["net_value"]), f"net enterprise value · {cm['n_selected']} projects · "
              f"{money(cm['investment'])} invested"),
             ("Conditional", signed_money(cond_gain), "if pending transfers and a 20% ring-fence release are approved"),
             ("Unconstrained (analytical)", signed_money(unc_gap), "value held by current restrictions"),
             ("Authorized sharing", signed_money(auth_gain), "value already created by approved transfers")]
    P('<div class="grid4">' + "".join(
        f'<div class="card tile"><div class="t">{E(t)}</div><div class="v">{E(v)}</div><div class="l">{E(l)}</div></div>'
        for t, v, l in tiles) + "</div>")

    # Investment and outcomes by LOB
    keys = lobs + ["ENT"]
    inv_rows = [(name(l), [cm["investment_by_lob"].get(l, 0.0)]) for l in keys]
    val_rows = [(name(l), [cm["lob_value"].get(l, 0.0)]) for l in lobs + [ENTERPRISE_UNALLOCATED]]
    P('<div class="grid2"><div class="card"><h2>Investment by LOB</h2><p class="sub">Funding shares of '
      'committed projects plus transfer and incremental resource costs.</p>'
      + hbars(inv_rows, [("Investment", CATEGORICAL[3])]) + "</div>")
    P('<div class="card"><h2>Enterprise and LOB business outcomes</h2><p class="sub">Attributed benefit; '
      f'sums to enterprise value of {money(cm["enterprise_value"])} with no double counting.</p>'
      + hbars(val_rows, [("Attributed value", CATEGORICAL[0])]) + "</div></div>")

    # Selected projects by LOB
    rows = []
    for pid, p in sorted(d.projects.items(), key=lambda kv: (kv[1].primary_lob, kv[0])):
        st = cm["starts"].get(pid)
        rows.append([E(pid), E(p.name), E(name(p.primary_lob)), badge(b.classification[pid]),
                     E(d.settings.month_label(st) if st else "—"),
                     E(d.settings.month_label(cm["ends"][pid]) if st else "—"),
                     f'<span class="num">{E(money(p.value))}</span>',
                     E(", ".join(x for x in ["Mandatory" if p.mandatory else "", "Compliance" if p.compliance else "",
                                             "Critical" if p.critical else "", "Shared platform" if p.shared_platform else ""] if x))])
    P('<div class="card"><h2>Selected projects by LOB</h2>' + legend([(k, STATUS[k]) for k in STATUS])
      + '<div class="scroll">' + table(["ID", "Project", "Primary LOB", "Classification", "Start", "Finish",
                                         "Value", "Flags"], rows) + "</div></div>")

    # Shared vs ring-fenced capacity
    ct = capacity_by_type(b)
    cap_series = [("Shared – allocated", CATEGORICAL[0]), ("Shared – unused", "A9C7EE"),
                  ("Ring-fenced/conditional – allocated", CATEGORICAL[1]), ("Ring-fenced/conditional – idle", "F5B79C")]
    cap_rows = [(c, [v["shared_allocated"], v["shared_unused"], v["restricted_allocated"], v["restricted_unused"]])
                for c, v in ct.items()]
    cap = b.capacity
    P('<div class="grid2"><div class="card"><h2>Shared versus ring-fenced capacity</h2>'
      f'<p class="sub">Shared utilization {pct(cm["shared_utilization"])} · ring-fenced and conditional '
      f'{pct(cm["ring_fenced_utilization"])}</p>' + legend(cap_series)
      + hbars(cap_rows, cap_series, fmt=lambda v: f"{v:,.0f} h", stacked=True) + "</div>")
    P('<div class="card"><h2>Unused reserved capacity</h2><p class="sub">'
      f'{cap["idle_ring_fenced_hours"]:,.0f} idle ring-fenced hours · {cap["unused_shared_hours"]:,.0f} unused '
      f'shared hours · {cap["restricted_unavailable_hours"]:,.0f} hours of other-LOB demand blocked by '
      f'restrictions · {cap["committed_not_consumed_hours"]:,.0f} h committed, not yet consumed · '
      f'{cap["consumed_hours"]:,.0f} h consumed</p>')
    rv = {r["pool"]: r for r in b.release_values}
    rows = []
    for pool, idle in sorted(cap["idle_by_restricted_pool"].items(), key=lambda kv: -kv[1]):
        pl = d.pools[pool]
        r = rv.get(pool)
        rows.append([E(pool), E(name(pl.owning_lob)), E(pl.capacity_type.value), f'{idle:,.0f} h',
                     E(money(r["incremental_value"])) if r else "—",
                     E(", ".join(name(l) for l in (r or {}).get("beneficiary_lobs", {})) or "—")])
    P(table(["Pool", "Owner", "Type", "Idle", "Value of 30% release", "LOBs that would benefit"], rows))
    P('<p class="note">Ring-fencing is not inherently inefficient. Restrictions may reflect regulatory '
      'requirements, domain expertise, service commitments or funding conditions; release values show the '
      'economic consequence so owners can decide.</p></div></div>')

    # Monthly utilization heatmap + bottlenecks
    used = {}
    for r in cap["rows"]:
        used.setdefault(r["pool"], {})[r["month"]] = (r["utilization"] if r["available"] > 0 else None)
    hm_rows = [(f"{pid} ({d.pools[pid].capability.split()[0]})", [used[pid][t] for t in d.months]) for pid in d.pools]
    bn = sorted(cap["bottleneck_months_by_capability"].items(), key=lambda kv: -kv[1])
    P('<div class="card"><h2>Monthly capacity utilization and capability bottlenecks</h2><p class="sub">'
      'Darker = higher utilization; ● marks months at 95% or more. Bottleneck months by capability: '
      + E(", ".join(f"{c} {n}" for c, n in bn) or "none") + "</p>"
      + heatmap(hm_rows, [d.settings.month_label(t)[:3] for t in d.months]) + "</div>")

    # Cross-LOB value creation & deferred outcomes
    rows = []
    for pid in cm["selected"]:
        p = d.projects[pid]
        if not p.is_cross_lob:
            continue
        att = d.attributed(pid)
        rows.append([E(f"{pid} {p.name}"), E(name(p.primary_lob)),
                     E(", ".join(f"{name(l)} {money(v)}" for l, v in att.items())),
                     E(money(d.unallocated_benefit(pid))), E(money(p.value)),
                     E(", ".join(f"{name(l)} {s:.0%}" for l, s in p.shares().items()))])
    P('<div class="card"><h2>Cross-LOB value creation</h2><p class="sub">Benefit attributed to each '
      'benefiting LOB plus the enterprise-wide remainder; the total is counted once.</p>'
      + table(["Initiative", "Sponsor", "LOB benefit", "Enterprise-wide", "Total (once)", "Funding shares"], rows)
      + "</div>")
    rows = []
    for t in cm["target_attainment"]:
        if t["attainment"] is not None and t["attainment"] < 1 - 1e-9:
            rows.append([E(name(t["lob"])), E(t["kpi"]), E(money(t["target"])), E(money(t["achieved"])),
                         E(pct(t["attainment"])), "Yes" if t["min_outcome_met"] else "<b>No</b>"])
    deferred = [pid for pid, c in b.classification.items() if c != "Approved"]
    P('<div class="card"><h2>Deferred business outcomes</h2><p class="sub">'
      f'{len(deferred)} initiatives worth {money(sum(d.projects[p].value for p in deferred))} are not in the '
      'committed plan: ' + E(", ".join(f"{p} ({b.classification[p]})" for p in sorted(deferred))) + "</p>"
      + table(["LOB", "KPI", "Target", "Committed", "Attainment", "Minimum met"], rows) + "</div>")

    # Incremental value from authorized sharing + spider
    lad = fungibility_ladder(b)
    P('<div class="grid2"><div class="card"><h2>Incremental value from resource sharing</h2><p class="sub">'
      'Each step widens sharing; only the first two are executable today.</p>'
      + hbars([(l, [v]) for l, v in lad], [("Net value", CATEGORICAL[2])], label_w=260) + "</div>")
    sc = spider_scores(b)
    P('<div class="card"><h2>Portfolio alternatives</h2>'
      + legend([(n, CATEGORICAL[i]) for i, n in enumerate(sc)]) + radar(sc, SPIDER_DIMENSIONS) + "</div></div>")

    # Trade-off matrix
    head = ["#", "Scenario", "Status", "Net value", "Δ vs committed"] + [f"Δ {d.lobs[l].name}" for l in lobs] + \
           ["Δ Investment", "Projects ±", "Ring-fenced util.", "Transfer cost", "Compliance", "Δ Risk red.",
            "Δ Maturity", "Opportunity cost", "Approvals"]
    rows = []
    for o in b.scenarios:
        m, dl = o.metrics, o.deltas
        if not m.get("feasible"):
            rows.append([str(o.scenario.sid), E(o.scenario.name), badge("Rejected — infeasible")]
                        + ["—"] * (len(head) - 4) + [E("; ".join(o.result.diagnostics))])
            continue
        rows.append([str(o.scenario.sid), E(o.scenario.name), badge(o.status_label), E(money(m["net_value"])),
                     delta_cell(dl["net_value"])] + [delta_cell(dl["lob_value"].get(l, 0.0)) for l in lobs]
                    + [delta_cell(dl["investment"]),
                       E(" ".join([f"+{x}" for x in dl["projects_added"]] + [f"−{x}" for x in dl["projects_removed"]]) or "—"),
                       E(pct(m["ring_fenced_utilization"])), E(money(m["transfer_cost"])),
                       E(pct(m["compliance_coverage"])), f'{dl["risk_reduction"]:+.0f}', f'{dl["maturity_gain"]:+.0f}',
                       E(money(dl.get("opportunity_cost", 0.0))),
                       ("<ul>" + "".join(f"<li>{E(a)}</li>" for a in o.approvals) + "</ul>") if o.approvals else "—"])
    P('<div class="card"><h2>LOB Trade-Off Matrix</h2><p class="sub">Enterprise-optimal versus LOB-protected and '
      'capacity-release scenarios. Ring-fencing impact on enterprise value: '
      f'{E(signed_money(unc_gap))} held by all restrictions (analytical); fairness cost of balanced LOB '
      f'outcomes: {E(signed_money(b.scenario("balanced").deltas.get("net_value", 0)))}.</p>'
      f'<div class="scroll">{table(head, rows, "matrix")}</div></div>')

    # LOB reconciliation
    rows = [[E(r.get("lob_name", r["lob"])), E(money(r.get("standalone_value", 0))),
             E(money(r.get("enterprise_plan_own_value", 0))), E(money(r.get("cross_lob_benefit_received", 0))),
             E(money(r.get("concession", 0))), E(", ".join(r.get("standalone_only", [])) or "—")]
            for r in b.lob_view["lobs"]]
    lv = b.lob_view
    P('<div class="card"><h2>LOB optimization reconciled to the enterprise plan</h2><p class="sub">'
      f'If each LOB planned alone, shared pools would be over-promised by {lv["oversubscribed_hours"]:,.0f} hours '
      f'across {len(lv["shared_conflicts"])} pool-months. The enterprise plan is the single allocation of record '
      f'and stays within capacity: {"yes" if lv["enterprise_within_capacity"] else "NO"}.</p>'
      + table(["LOB", "Standalone (own projects)", "Enterprise plan: own", "Cross-LOB benefit received",
               "Concession", "Standalone-only projects"], rows) + "</div>")

    doc = (f'<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" '
           f'content="width=device-width,initial-scale=1"><title>Frontier Portfolio IQ — LOB Dashboard</title>'
           f'<style>{CSS}</style></head><body style="margin:0"><main class="viz-root">{"".join(parts)}</main>'
           f'</body></html>')
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(doc)
    return path
