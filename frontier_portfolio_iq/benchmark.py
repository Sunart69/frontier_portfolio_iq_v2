"""Synthetic portfolio generator and performance benchmark.

`generate_portfolio(n, horizon, seed)` builds a realistic synthetic portfolio: four business LOBs plus an
enterprise IT entity, five capabilities, ring-fenced / shared / conditionally transferable pools, approved
and pending transfer rules, monthly LOB budgets by funding source (sized below total demand, so the
optimizer has to choose), mandatory / compliance / critical initiatives, deadlines, dependencies, vendor
tags and cross-LOB benefit attribution.

`python -m frontier_portfolio_iq.benchmark` runs the grid (25, 100, 250, 500 initiatives x 12, 24, 36
months), each case in its own process so peak memory is measured cleanly, and writes the *measured*
results to docs/PERFORMANCE.md and benchmark_results.json. Nothing in those files is estimated.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import resource
import subprocess
import sys
import time
from typing import Dict, List

import numpy as np

from .domain import (ANY, LOB, ApprovalStatus, BenefitAllocation, BudgetLine, CapacityPool, CapacityType,
                     EligibilityRule, PortfolioData, Project, Settings, StrategicTarget, TransferRule)

CAPS = ["Software Engineering", "Integration Engineering", "Data Engineering", "AI Engineering",
        "Solution Architecture"]


def generate_portfolio(n: int, horizon: int = 12, seed: int = 7) -> PortfolioData:
    rng = np.random.default_rng(seed)
    lobs = {f"L{k}": LOB(f"L{k}", f"Line of Business {k}", f"Sponsor {k}", f"Owner {k}", 1 + k % 2,
                         risk_tolerance=0.45, maturity_target=None) for k in range(1, 5)}
    lobs["ENT"] = LOB("ENT", "Enterprise IT", "CIO", "Chief Architect", 1, is_enterprise=True)
    biz = [l for l in lobs if l != "ENT"]
    scale = n / 25.0
    pools: Dict[str, CapacityPool] = {}
    for l in biz:
        for c in CAPS[:3]:
            pid = f"RF-{l}-{c[:4].upper()}"
            pools[pid] = CapacityPool(pid, c, l, CapacityType.RING_FENCED, 1, None, round(1.6 * scale, 2),
                                      release_eligible=True)
    for c in CAPS:
        pid = f"SH-{c[:4].upper()}"
        pools[pid] = CapacityPool(pid, c, "ENT", CapacityType.SHARED, 1, None, round(2.4 * scale, 2))
    pools["CT-AI"] = CapacityPool("CT-AI", "AI Engineering", "L1", CapacityType.CONDITIONAL, min(3, horizon), None,
                                  round(1.0 * scale, 2), incremental_cost_rate=40.0)
    transfers = [TransferRule("TR-01", f"RF-L1-SOFT", "L1", "L2", None, 0.3, 0.9, 20.0, True, ApprovalStatus.APPROVED),
                 TransferRule("TR-02", f"RF-L3-DATA", "L3", ANY, None, 0.25, 0.85, 25.0, True, ApprovalStatus.PENDING),
                 TransferRule("TR-03", "CT-AI", "L1", ANY, None, 0.5, 0.9, 10.0, True, ApprovalStatus.APPROVED)]
    eligibility = [EligibilityRule("CT-AI", "L1")]
    projects: Dict[str, Project] = {}
    vendors = ["VendorA", "VendorB", "VendorC"]
    for k in range(n):
        lob = biz[k % 4] if rng.random() > 0.12 else "ENT"
        dur = int(rng.integers(3, min(10, horizon) + 1))
        caps = list(rng.choice(CAPS, size=int(rng.integers(1, 4)), replace=False))
        demand = {}
        for c in caps:
            h = float(rng.uniform(60, 260))
            for m in range(1, dur + 1):
                demand[(c, m)] = round(h * (0.8 + 0.4 * rng.random()), 1)
        cost_pm = float(rng.uniform(20, 90))
        value = float(np.exp(rng.normal(np.log(cost_pm * dur * 2.6), 0.5)))
        risk = float(np.clip(rng.beta(2, 6), 0.05, 0.9))
        pid = f"P{k + 1:03d}"
        p = Project(pid, f"Initiative {k + 1}", lob, okr=f"{lob}-KR{1 + k % 3}", project_type="Discretionary",
                    funding_source="Opex", value=round(value, 1), duration=dur,
                    earliest_start=int(rng.integers(1, max(2, horizon - dur + 2))),
                    execution_risk=round(risk, 3), delivery_confidence=round(float(np.clip(1 - risk + rng.normal(0, .05), .4, .98)), 3),
                    enterprise_contribution=round(float(rng.random()), 2), maturity_gain=float(rng.integers(0, 4)),
                    risk_reduction=float(rng.integers(0, 3)), demand=demand,
                    cost={m: round(cost_pm, 1) for m in range(1, dur + 1)}, lob_priority=int(rng.integers(1, 4)))
        roll = rng.random()
        if roll < 0.06:
            p.mandatory = True
        elif roll < 0.14:
            p.compliance = True
            p.project_type = "Compliance"
        if lob == "ENT":
            p.shared_platform = True
            p.funding_shares = {l: 0.25 for l in biz}
        if rng.random() < 0.15:
            p.tags.append(f"vendor:{vendors[int(rng.integers(0, 3))]}")
        if p.mandatory or p.compliance:
            p.earliest_start = 1
            p.deadline = None
        projects[pid] = p
    ids = list(projects)
    for k, pid in enumerate(ids):           # dependencies on earlier initiatives (acyclic)
        if k > 5 and rng.random() < 0.1:
            pre = ids[int(rng.integers(0, k))]
            pp, p = projects[pre], projects[pid]
            if not (p.mandatory or p.compliance) and pp.earliest_start + pp.duration + p.duration - 1 <= horizon:
                p.depends_on.append(pre)
                p.earliest_start = max(p.earliest_start, pp.earliest_start + pp.duration)
    for p in projects.values():             # windows always admit at least one start
        p.earliest_start = max(1, min(p.earliest_start, horizon - p.duration + 1))
    benefits, targets = [], []
    for p in projects.values():
        owners = [p.primary_lob] if p.primary_lob != "ENT" else list(rng.choice(biz, size=2, replace=False))
        share = 0.8 / len(owners)
        for l in owners:
            benefits.append(BenefitAllocation(p.project_id, l, p.okr if p.primary_lob == l else f"{l}-KR1",
                                              round(p.value * share, 1)))
    for l in biz:
        tot = sum(b.attributed_benefit for b in benefits if b.lob_id == l and b.kpi == f"{l}-KR1")
        targets.append(StrategicTarget(l, f"{l}-KR1", round(0.4 * tot, 1)))
    demand_cost = sum(p.total_cost for p in projects.values())
    budgets = []
    for l in list(biz) + ["ENT"]:
        mine = sum(p.total_cost * p.shares().get(l, 0.0) for p in projects.values())
        monthly = 0.62 * max(mine, demand_cost * 0.05) / horizon
        for t in range(1, horizon + 1):
            budgets.append(BudgetLine(l, t, "Opex", round(monthly, 1)))
            if l != "ENT":
                budgets.append(BudgetLine(l, t, "Regulatory", round(monthly * 0.25, 1), True, ["Compliance"]))
    for p in projects.values():
        if p.compliance:
            p.funding_source = "Regulatory"
            if p.primary_lob == "ENT":
                p.funding_source = "Opex"
    settings = Settings(horizon=horizon, first_month_label="2027-01", time_limit=60, mip_gap=0.01)
    return PortfolioData(lobs, projects, pools, eligibility, transfers, budgets, targets, benefits, [], settings)


# --------------------------------------------------------------------------- one case
def run_case(n: int, horizon: int, time_limit: float, iterations: int, seed: int = 7) -> Dict:
    from .optimizer import SolveOptions
    from .risk import default_uncertainty, evaluate_plan
    from .solvers import SolverSettings, compile_portfolio, make_engine, to_portfolio_result, validate_result
    from .solvers.cpsat import compatibility
    out: Dict = {"projects": n, "horizon": horizon, "time_limit_s": time_limit}
    data = generate_portfolio(n, horizon, seed)
    t0 = time.time()
    comp = compile_portfolio(data, SolveOptions())
    out["build_s"] = round(time.time() - t0, 2)
    out.update(comp.size)
    st = SolverSettings(time_limit=time_limit, mip_gap=0.01)
    plan = None
    for name in ("HiGHS", "SCIP"):
        r = make_engine(name).solve(comp, st)
        out[name] = {"status": r.status.value, "proof": r.proof, "objective": r.objective, "gap": r.gap,
                     "runtime_s": round(r.runtime, 2)}
        if r.status.has_solution:
            res = to_portfolio_result(comp, r)
            v = validate_result(res)
            out[name]["validated"] = v.feasible
            out[name]["selected"] = len(res.starts)
            if name == "HiGHS":
                plan = res
    ok, why = compatibility(comp)
    if ok:
        r = make_engine("CP-SAT").solve(comp, st)
        out["CP-SAT"] = {"status": r.status.value, "proof": r.proof, "objective": r.objective,
                         "gap_scaled_model": r.gap, "runtime_s": round(r.runtime, 2)}
    else:
        out["CP-SAT"] = {"status": "incompatible", "reason": why}
    if plan is not None and plan.feasible:
        um = default_uncertainty(data).with_settings(iterations=iterations, chunk_size=min(2500, iterations))
        t1 = time.time()
        summ, run = evaluate_plan(data, plan.starts, um, "fixed", result=plan, keep_projects=False)
        out["monte_carlo"] = {"iterations": iterations, "seconds": round(time.time() - t1, 2),
                              "throughput_per_s": round(run.throughput, 1),
                              "converged": summ["convergence"]["converged"],
                              "rel_se_mean_npv": summ["convergence"]["rel_se"]}
    out["peak_rss_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
    return out


def write_report(results: List[Dict], path_md: str, meta: Dict) -> None:
    def g(v):
        return "—" if v is None else (f"{v:.2%}" if isinstance(v, float) else str(v))
    lines = ["# Performance (measured)", "",
             f"Measured on {meta['machine']} ({meta['cpus']} CPUs), Python {meta['python']}, {meta['date']}. "
             f"Each case runs in its own process; time limit per MILP solve {meta['time_limit']} s, relative gap "
             f"target 1%. Monte Carlo: {meta['iterations']:,} iterations, fixed-plan mode, one worker. "
             "Synthetic portfolios from `frontier_portfolio_iq.benchmark.generate_portfolio` (seed 7). "
             "These are the numbers this machine produced; your hardware will differ.", "",
             "| Initiatives | Months | Variables (integer) | Constraints | Build s | HiGHS status | HiGHS gap | HiGHS s | "
             "Validated | SCIP status | SCIP gap | SCIP s | CP-SAT | MC s | MC iter/s | Peak RSS MB |",
             "|---:|---:|---:|---:|---:|---|---:|---:|---|---|---:|---:|---|---:|---:|---:|"]
    for r in results:
        if "error" in r:
            lines.append(f"| {r['projects']} | {r['horizon']} | — | — | — | error: {r['error'][:60]} | | | | | | | | | |")
            continue
        h, s, c, mc = r.get("HiGHS", {}), r.get("SCIP", {}), r.get("CP-SAT", {}), r.get("monte_carlo", {})
        cp = c.get("status", "—") + (f" ({c.get('runtime_s')} s)" if c.get("runtime_s") is not None else "")
        lines.append(f"| {r['projects']} | {r['horizon']} | {r['variables']:,} ({r['integer_variables']:,}) | "
                     f"{r['constraints']:,} | {r['build_s']} | {h.get('status')} ({h.get('proof')}) | {g(h.get('gap'))} | "
                     f"{h.get('runtime_s')} | {'yes' if h.get('validated') else 'NO'} | {s.get('status')} | "
                     f"{g(s.get('gap'))} | {s.get('runtime_s')} | {cp} | "
                     f"{mc.get('seconds', '—')} | {mc.get('throughput_per_s', '—')} | {r['peak_rss_mb']} |")
    ok = [r for r in results if "error" not in r]
    lines += ["", "Notes",
              "- *optimal* means the solver proved the plan within the 1% gap; *feasible* means it stopped at the "
              "time limit with a plan and the gap shown. *Validated* = the HiGHS plan passed the independent "
              "feasibility validator.",
              "- CP-SAT solves an integer reformulation (hours rounded conservatively); its gap refers to that "
              "scaled model. Above its integer-variable limit (60,000 by default) it reports *incompatible* and "
              "HiGHS and SCIP still cross-check the case."
              + ("" if any(r.get("CP-SAT", {}).get("status") == "incompatible" for r in ok)
                 else " No case in this grid reached that limit."),
              "- Harder is not always bigger: the tightest budget/capacity mixes (e.g. 100 initiatives) can be "
              "harder to prove than larger, looser ones. Raise `time_limit` for those, or accept the reported gap.",
              "- Monte Carlo throughput is for the fixed-plan simulation on one worker; it scales with "
              "`workers` (results are identical for any worker count).",
              "- Peak RSS includes the Python interpreter and all solver libraries (HiGHS, SCIP, CP-SAT)."]
    with open(path_md, "w") as fh:
        fh.write("\n".join(lines) + "\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--one", nargs=2, type=int, metavar=("PROJECTS", "MONTHS"))
    ap.add_argument("--sizes", default="25,100,250,500")
    ap.add_argument("--horizons", default="12,24,36")
    ap.add_argument("--time-limit", type=float, default=60.0)
    ap.add_argument("--iterations", type=int, default=2000)
    ap.add_argument("--out", default="docs")
    ap.add_argument("--report-only", action="store_true", help="rewrite PERFORMANCE.md from benchmark_results.json")
    a = ap.parse_args(argv)
    if a.one:
        print(json.dumps(run_case(a.one[0], a.one[1], a.time_limit, a.iterations)))
        return 0
    results = []
    jpath = os.path.join(a.out, "benchmark_results.json")
    if a.report_only:
        with open(jpath) as fh:
            doc = json.load(fh)
        write_report(doc["results"], os.path.join(a.out, "PERFORMANCE.md"), doc["meta"])
        return 0
    for n in [int(x) for x in a.sizes.split(",")]:
        for h in [int(x) for x in a.horizons.split(",")]:
            cmd = [sys.executable, "-m", "frontier_portfolio_iq.benchmark", "--one", str(n), str(h),
                   "--time-limit", str(a.time_limit), "--iterations", str(a.iterations)]
            p = subprocess.run(cmd, capture_output=True, text=True)
            line = [l for l in p.stdout.splitlines() if l.startswith("{")]
            results.append(json.loads(line[-1]) if line else {"projects": n, "horizon": h,
                                                               "error": (p.stderr or "no output")[-300:]})
            print(json.dumps(results[-1])[:300], flush=True)
            meta = {"machine": platform.platform(), "cpus": os.cpu_count(), "python": platform.python_version(),
                    "date": time.strftime("%Y-%m-%d"), "time_limit": a.time_limit, "iterations": a.iterations}
            with open(jpath, "w") as fh:
                json.dump({"meta": meta, "results": results}, fh, indent=1)
            write_report(results, os.path.join(a.out, "PERFORMANCE.md"), meta)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
