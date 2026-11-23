"""Command line: validate an intake workbook, generate the sample, or run the full multi-LOB analysis."""
from __future__ import annotations

import argparse
import json
import os
import sys

from .analysis import run_analysis, write_results_workbook
from .dashboard import render_dashboard
from .deck import build_deck
from .intake import IntakeError, read_workbook, validate, write_workbook
from .sample_data import build_sample


def _summary(b) -> dict:
    cm = b.tier_metrics
    return {
        "run_id": b.run_id,
        "tiers": {k: {kk: v.get(kk) for kk in ("label", "tier", "status", "enterprise_value", "net_value",
                                                 "investment", "n_selected", "approvals_required")}
                  for k, v in cm.items()},
        "scenarios": [{"id": s.scenario.sid, "name": s.scenario.name, "status": s.status_label,
                       "net_value": s.metrics.get("net_value"), "delta_net_value": s.deltas.get("net_value"),
                       "approvals": s.approvals, "diagnostics": s.result.diagnostics} for s in b.scenarios],
        "classification": b.classification,
        "release_values": b.release_values,
        "lob_reconciliation": {k: v for k, v in b.lob_view.items() if k != "results"},
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="frontier-portfolio-iq")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample", help="write the demonstration intake workbook")
    s.add_argument("--out", default="sample_portfolio.xlsx")
    v = sub.add_parser("validate", help="validate an intake workbook")
    v.add_argument("--input", required=True)
    r = sub.add_parser("run", help="optimize, run scenarios, and publish workbook, dashboard and deck")
    r.add_argument("--input", help="intake workbook (omit to use the sample portfolio)")
    r.add_argument("--out", default="out")
    r.add_argument("--no-release-scan", action="store_true", help="skip per-pool release valuation")
    r.add_argument("--transfer-source"), r.add_argument("--transfer-dest")
    r.add_argument("--budget-cut-lob"), r.add_argument("--contractor-lob")
    r.add_argument("--accelerate", help="PROJECT_ID:MONTH")
    dd = sub.add_parser("decide", help="v2 decision intelligence: validated multi-solver optimization, Pareto "
                                       "frontier, Monte Carlo risk, risk-adjusted recommendation, CEO deck")
    dd.add_argument("--input", help="intake workbook (omit to use the sample portfolio)")
    dd.add_argument("--out", default="out")
    dd.add_argument("--config", help="decision configuration YAML (see config/decision.yaml)")
    dd.add_argument("--iterations", type=int, help="Monte Carlo iterations (default 10,000)")
    dd.add_argument("--mode", choices=["fixed", "adaptive"], help="primary simulation mode (default fixed)")
    dd.add_argument("--seed", type=int)
    dd.add_argument("--workers", type=int, help="processes for the Monte Carlo (results identical for any value)")
    dd.add_argument("--profile", help="risk_neutral | balanced | risk_averse | <custom profile name>")
    dd.add_argument("--rules", nargs="*", default=[], help="rule sets, e.g. npv_budget80 okr_dates85 mandatory")
    dd.add_argument("--nsga", action="store_true", help="also run NSGA-II (approximate, certified feasible)")
    dd.add_argument("--no-whatif", action="store_true")
    dd.add_argument("--no-v1", action="store_true", help="skip the v1 tier/scenario analysis and its outputs")
    dd.add_argument("--quick", action="store_true", help="small frontier and 2,000 iterations (smoke runs)")
    dd.add_argument("--cache", default=".fpiq_cache", help="result cache directory ('' to disable)")
    c = sub.add_parser("cycle", help="semi-annual prioritization cadence (plan, kickoff, follow-up, run, publish)")
    c.add_argument("action", choices=["schedule", "plan", "kickoff", "status", "followup", "check", "run", "publish", "tick"])
    c.add_argument("--cycle", help="e.g. 2027-H2 (default: the active or next cycle)")
    c.add_argument("--today", help="YYYY-MM-DD (default: today)")
    c.add_argument("--root", default=".", help="repository root")
    c.add_argument("--dry-run", action="store_true", help="use an in-memory issue tracker; post nothing")
    c.add_argument("--force", action="store_true", help="run even if inputs are incomplete (carry forward)")
    c.add_argument("--no-release-scan", action="store_true")
    c.add_argument("--files", nargs="*", default=[], help="check: submitted workbooks to validate")
    c.add_argument("--json", action="store_true", help="print a JSON summary")
    args = ap.parse_args(argv)
    if args.cmd == "cycle":
        return cycle_main(args)
    if args.cmd == "decide":
        return decide_main(args)

    if args.cmd == "sample":
        from .risk import default_uncertainty
        d = build_sample()
        write_workbook(d, args.out, uncertainty=default_uncertainty(d))
        print(f"Sample intake written to {args.out}")
        return 0
    if args.cmd == "validate":
        data = read_workbook(args.input, validate_strict=False)
        rep = validate(data)
        for e in rep.errors:
            print(f"ERROR   {e}")
        for w in rep.warnings:
            print(f"WARNING {w}")
        print("Valid" if rep.ok else "Invalid")
        return 0 if rep.ok else 1

    try:
        data = read_workbook(args.input) if args.input else build_sample()
    except IntakeError as e:
        print(str(e), file=sys.stderr)
        return 1
    os.makedirs(args.out, exist_ok=True)
    kw = {k: getattr(args, k) for k in ("transfer_source", "transfer_dest", "budget_cut_lob", "contractor_lob")
          if getattr(args, k)}
    if args.accelerate:
        pid, m = args.accelerate.split(":")
        kw["accelerate"] = (pid, int(m))
    b = run_analysis(data, release_scan=not args.no_release_scan, **kw)
    write_results_workbook(b, os.path.join(args.out, "portfolio_results.xlsx"))
    render_dashboard(b, os.path.join(args.out, "lob_dashboard.html"))
    manifest = build_deck(b, os.path.join(args.out, "executive_deck.pptx"))
    with open(os.path.join(args.out, "deck_figures_manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=2, default=str)
    with open(os.path.join(args.out, "analysis_summary.json"), "w") as fh:
        json.dump(_summary(b), fh, indent=2, default=str)
    cm = b.tier_metrics["committed"]
    print(f"Committed: {cm['n_selected']} projects, net value ${cm['net_value']:,.0f}K, "
          f"investment ${cm['investment']:,.0f}K. Outputs in {args.out}/")
    return 0


def decide_main(args) -> int:
    import dataclasses
    from .decision.config import load_decision_config, write_decision_workbook
    from .decision.recommend import RULE_SETS
    from .risk import default_uncertainty, read_uncertainty
    try:
        data = read_workbook(args.input) if args.input else build_sample()
    except IntakeError as e:
        print(str(e), file=sys.stderr)
        return 1
    um = read_uncertainty(args.input, data) if args.input else default_uncertainty(data)
    cfg, sim = load_decision_config(args.config)
    sim = dict(sim)
    for k in ("mode", "seed", "workers"):
        if getattr(args, k) is not None:
            sim[k] = getattr(args, k)
    sim.pop("iterations", None)
    if sim:
        um = um.with_settings(**sim)
    if args.iterations:
        cfg.iterations = args.iterations
    if args.profile:
        cfg.primary_profile = args.profile
    for rs in args.rules:
        cfg.rules = list(cfg.rules) + RULE_SETS[rs]
    if args.nsga:
        from .pareto.nsga import NsgaConfig
        cfg.nsga = cfg.nsga or NsgaConfig()
    if args.no_whatif:
        cfg.run_whatifs = False
    if args.quick:
        cfg.pareto = dataclasses.replace(cfg.pareto, grid_points=3, refine_rounds=1, weighted_step=0.5, max_points=20)
        cfg.iterations = min(cfg.iterations, 2000)
        cfg.whatif_iterations = 1000
    cfg.cache_dir = args.cache or None
    os.makedirs(args.out, exist_ok=True)
    analysis = None
    if not args.no_v1:
        analysis = run_analysis(data, release_scan=False)
        write_results_workbook(analysis, os.path.join(args.out, "portfolio_results.xlsx"))
        render_dashboard(analysis, os.path.join(args.out, "lob_dashboard.html"))
        build_deck(analysis, os.path.join(args.out, "executive_deck.pptx"))
    from .dashboard_v2 import build_decision_dashboard
    from .deck_v2 import build_ceo_deck
    from .intelligence import run_decision_intelligence
    b = run_decision_intelligence(data, um, cfg, analysis=analysis)
    build_decision_dashboard(b, os.path.join(args.out, "decision_dashboard.html"))
    manifest = build_ceo_deck(b, os.path.join(args.out, "ceo_decision_deck.pptx"))
    with open(os.path.join(args.out, "ceo_deck_manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=2, default=str)
    b.write_json(os.path.join(args.out, "decision_intelligence.json"))
    write_decision_workbook(b, os.path.join(args.out, "decision_results.xlsx"))
    with open(os.path.join(args.out, "solver_confidence.md"), "w") as fh:
        fh.write(b.confidence.markdown())
        if b.recommended_confidence:
            fh.write("\n" + b.recommended_confidence.markdown())
    rec = b.recommended
    r = b.risk.get(rec.alt_id) if rec else None
    print(f"Recommended ({cfg.primary_profile}): {rec.name if rec else '-'} — {len(rec.starts) if rec else 0} initiatives, "
          f"E[NPV] ${r['npv']['mean']:,.0f}K (P10 ${r['npv']['p10']:,.0f}K), P(within budget) "
          f"{r['probabilities']['within_budget']['p']:.0%}. Solver confidence: {b.confidence.level}. "
          f"Outputs in {args.out}/" if r else "No recommendation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


def cycle_main(args) -> int:
    import datetime as dt
    from .cadence.config import load_cadence, load_owners
    from .cadence.followup import evaluate, expected_submissions, follow_up, open_input_issues, status_markdown
    from .cadence.github_api import FakeIssues, GitHubIssues
    from .cadence.orchestrator import load_state, plan, publish_cycle, run_cycle, save_state, tick
    from .cadence.packages import check_package
    from .cadence.schedule import active_cycle, build_schedule

    root = os.path.abspath(args.root)
    cfg = load_cadence(os.path.join(root, "cadence", "cadence.yaml"))
    owners = load_owners(os.path.join(root, "cadence", "owners.yaml"))
    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
    sched = build_schedule(args.cycle, cfg) if args.cycle else active_cycle(today, cfg)

    def tracker():
        return FakeIssues() if args.dry_run else GitHubIssues()

    if args.action == "schedule":
        print(f"{sched.cycle.label} · phase on {today}: {sched.phase(today)}")
        for r in sched.as_rows():
            print(f"  {r['date']} {r['weekday']}  {r['name']}")
        return 0
    if args.action == "plan":
        for k, v in plan(root, cfg, owners, str(sched.cycle)).items():
            print(f"{k}: {os.path.relpath(v, root)}")
        return 0
    if args.action == "check":
        bad = 0
        for f in args.files:
            chk = check_package(f)
            print(f"{'OK  ' if chk.ok else 'FAIL'} {os.path.relpath(f, root)} ({chk.package}, {chk.lob})")
            for e in chk.errors:
                print(f"     - {e}")
            bad += 0 if chk.ok else 1
        return 1 if bad else 0
    subs = evaluate(expected_submissions(root, cfg, owners, sched))
    if args.action == "status":
        print(status_markdown(subs, sched, today, cfg))
        return 0
    if args.action == "kickoff":
        plan(root, cfg, owners, str(sched.cycle))
        n = open_input_issues(tracker(), subs, sched, root, cfg)
        print(f"Opened {len(n)} input issues for {sched.cycle}")
        return 0
    if args.action == "followup":
        acts = follow_up(tracker(), subs, sched, owners, cfg, today, root)
        print(json.dumps(acts, indent=2) if args.json else f"{len(acts)} follow-ups posted")
        return 0
    if args.action == "run":
        out = run_cycle(root, cfg, owners, sched, force=args.force, release_scan=not args.no_release_scan)
        if out.get("ran"):     # record it, so the daily run does not re-run and `publish` can follow
            state = load_state(root, cfg, sched)
            state["ran"] = today.isoformat()
            state["run_summary"] = {k: v for k, v in out.items() if k != "outputs"}
            save_state(root, cfg, sched, state)
        print(json.dumps(out, indent=2, default=str))
        return 0 if out.get("ran") else 2
    if args.action == "publish":
        state = load_state(root, cfg, sched)
        out = publish_cycle(root, cfg, sched, state)
        if out["published"]:
            state["published"] = today.isoformat()
            state["publish_summary"] = {k: v for k, v in out.items() if k != "files"}
            save_state(root, cfg, sched, state)
        print(json.dumps(out, indent=2, default=str))
        return 0 if out["published"] else 2
    res = tick(root, cfg, owners, tracker(), today, args.cycle, force_run=args.force,
               release_scan=not args.no_release_scan)
    if args.json:
        print(json.dumps(res.as_dict(), indent=2, default=str))
    else:
        print(f"{res.cycle} · {res.phase}")
        for a in res.actions:
            print(f"  - {a}")
    out_path = os.environ.get("GITHUB_OUTPUT")
    if out_path:
        with open(out_path, "a") as fh:
            fh.write(f"cycle={res.cycle}\nfiles_changed={str(res.files_changed).lower()}\n"
                     f"ran={str(res.ran_prioritization).lower()}\n")
    return 0
