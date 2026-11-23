"""Risk metrics from simulated outcomes, with explicit definitions and sampling-error estimates.

Percentile direction
    Pxx is the statistical xx-th percentile of the simulated distribution: P10 is the value that 10% of
    outcomes fall *below*. For value and NPV, P10 is the downside case (90% of outcomes are better);
    for cost and completion month, P90 is the downside case (only 10% of outcomes are worse).

VaR / CVaR (downside, on NPV)
    loss = deterministic plan NPV - simulated NPV (positive = worse than plan)
    VaR_a  = a-quantile of loss                 (e.g. a = 95%: shortfall exceeded in only 5% of futures)
    CVaR_a = mean loss in the worst (1 - a) tail (expected shortfall when things go badly)

Sampling error
    mean: standard error from 20 batch means (robust to within-chunk correlation);
    probability p: sqrt(p (1 - p) / N); percentile: distribution-free 95% interval from binomial order
    statistics. Converged when SE(mean NPV) <= rel_se x |mean NPV| and every probability SE <= prob_se.
    More iterations reduce sampling error only; they do not make the input assumptions more accurate.
"""
from __future__ import annotations

import math
from typing import Dict, List

import numpy as np
from scipy import stats

from .correlation import spearman


def _pct(x: np.ndarray, q: float) -> float:
    return float(np.percentile(x, q))


def percentile_ci(x: np.ndarray, q: float, conf: float = 0.95) -> List[float]:
    n = len(x)
    xs = np.sort(x)
    z = stats.norm.ppf(0.5 + conf / 2)
    p = q / 100
    lo = int(max(0, math.floor(n * p - z * math.sqrt(n * p * (1 - p)))))
    hi = int(min(n - 1, math.ceil(n * p + z * math.sqrt(n * p * (1 - p)))))
    return [float(xs[lo]), float(xs[hi])]


def batch_se(x: np.ndarray, batches: int = 20) -> float:
    n = len(x)
    b = max(2, min(batches, n // 10))
    means = np.array([m.mean() for m in np.array_split(x, b)])
    return float(means.std(ddof=1) / math.sqrt(b))


def dist_summary(x: np.ndarray, lower_is_worse: bool = True) -> Dict[str, object]:
    x = np.asarray(x, dtype=float)
    return {"mean": float(x.mean()), "sd": float(x.std(ddof=1)), "se": batch_se(x),
            "p10": _pct(x, 10), "p50": _pct(x, 50), "p90": _pct(x, 90),
            "p5": _pct(x, 5), "p95": _pct(x, 95), "min": float(x.min()), "max": float(x.max()),
            "p10_ci": percentile_ci(x, 10), "p50_ci": percentile_ci(x, 50), "p90_ci": percentile_ci(x, 90),
            "downside": "p10" if lower_is_worse else "p90"}


def prob(flag: np.ndarray) -> Dict[str, float]:
    f = np.asarray(flag, dtype=float)
    p = float(f.mean())
    return {"p": p, "se": math.sqrt(max(p * (1 - p), 0.0) / len(f))}


def var_cvar(loss: np.ndarray, alpha: float) -> Dict[str, float]:
    var = float(np.quantile(loss, alpha))
    tail = loss[loss >= var]
    return {"alpha": alpha, "var": var, "cvar": float(tail.mean()) if len(tail) else var}


def convergence(x: np.ndarray, probs: Dict[str, Dict[str, float]], rel_se: float, prob_se: float) -> Dict[str, object]:
    n = len(x)
    checkpoints = sorted({int(v) for v in np.unique(np.geomspace(min(100, n), n, num=12).astype(int))})
    trace = [{"n": k, "mean": float(x[:k].mean()), "se": float(x[:k].std(ddof=1) / math.sqrt(k))} for k in checkpoints]
    se = batch_se(x)
    mean = float(x.mean())
    worst_p = max((v["se"] for v in probs.values()), default=0.0)
    half = float(x[: n // 2].mean()) if n >= 200 else mean
    return {"iterations": n, "mean_npv": mean, "se_mean_npv": se,
            "rel_se": se / max(abs(mean), 1e-9), "max_probability_se": worst_p,
            "half_sample_shift": abs(mean - half), "trace": trace,
            "converged": (se <= rel_se * max(abs(mean), 1e-9)) and worst_p <= prob_se,
            "criteria": {"rel_se": rel_se, "prob_se": prob_se}}


def summarize(run, top: int = 12) -> Dict[str, object]:
    s, st, ctx = run.samples, run.settings, run.context
    data = ctx.data
    npv, value, cost = s["npv"], s["value"], s["cost"]
    loss = ctx.reference_npv - npv
    min_val = st.min_value_fraction * ctx.reference_value
    probabilities = {
        "cost_overrun": prob(s["cost_overrun"]),
        "lob_budget_breach": prob(s["lob_budget_breach"]),
        "within_budget": prob(~s["cost_overrun"]),
        "miss_key_date": prob(s["late_any_key_date"]),
        "meet_key_dates": prob(~s["late_any_key_date"]),
        "okr_all_targets": prob(s["okr_all"]),
        "okr_plan_targets": prob(s["okr_plan_targets"]),
        "capability_shortage": prob(s["shortage_any"]),
        "min_acceptable_value": prob(value >= min_val),
        "npv_negative": prob(npv < 0),
    }
    per_target = {k.split("|", 1)[1]: prob(v) for k, v in s.items() if k.startswith("okr|")}
    per_cap = {k.split("|", 1)[1]: prob(v) for k, v in s.items() if k.startswith("shortage_cap|")}
    short_hours = {k.split("|", 1)[1]: float(v.mean()) for k, v in s.items() if k.startswith("shortage_hours|")}
    late = {k.split("|", 1)[1]: prob(v) for k, v in s.items() if k.startswith("late|")}
    vc = var_cvar(loss, st.var_alpha)
    # tail risk contributions (projects) and driver / input sensitivities
    tail = loss >= vc["var"]
    contrib = []
    P = data.projects
    if any(k.startswith("pnpv|") for k in s):
        tot_tail = float(loss[tail].mean()) if tail.any() else 0.0
        for i in ctx.order:
            p = P[i]
            ref_i = p.value * (1 + data.settings.discount_rate) ** (-(ctx.starts[i] + p.duration - 1) / 12) - sum(
                c * (1 + data.settings.discount_rate) ** (-(ctx.starts[i] + k - 1) / 12) for k, c in p.cost.items())
            li = ref_i - s[f"pnpv|{i}"]
            contrib.append({"project": i, "name": p.name, "tail_shortfall": float(li[tail].mean()) if tail.any() else 0.0,
                            "mean_shortfall": float(li.mean()),
                            "share_of_tail": (float(li[tail].mean()) / tot_tail) if tot_tail else 0.0,
                            "p_late": float((~(s[f"pfin|{i}"] <= ctx.starts[i] + p.duration - 1)).mean())})
        contrib.sort(key=lambda r: -r["tail_shortfall"])
    drivers = []
    sens = []
    for k, v in s.items():
        if not k.startswith("in|"):
            continue
        _, kind, ident = k.split("|", 2)
        rho = spearman(v, npv)
        if kind == "driver":
            sd = float(np.std(v)) or 1.0
            drivers.append({"driver": ident, "spearman_npv": rho,
                            "tail_shift_sd": (float(v[tail].mean()) - float(v.mean())) / sd if tail.any() else 0.0})
        label = f"{ident} {kind}" if kind != "driver" else f"driver {ident}"
        sens.append({"input": label, "kind": kind, "id": ident, "spearman_npv": rho})
    sens.sort(key=lambda r: -abs(r["spearman_npv"]))
    finishes = {}
    T = data.settings.horizon * 2 + 1
    for i in ctx.order:
        f = s.get(f"pfin|{i}")
        if f is None:
            continue
        g = np.where(np.isfinite(f), f, T)
        finishes[i] = {"planned": ctx.starts[i] + P[i].duration - 1, "p50": _pct(g, 50), "p90": _pct(g, 90),
                       "mean": float(g.mean()), "within_horizon": float((g <= data.settings.horizon).mean())}
    conv = convergence(npv, {**probabilities, **per_target, **late}, st.convergence_rel_se, st.convergence_prob_se)
    hist = np.histogram(npv, bins=30)
    comp = s["completion"]
    H = data.settings.horizon
    return {
        "iterations": run.iterations, "mode": st.mode, "seed": st.seed, "chunks": run.chunks,
        "seconds": run.seconds, "throughput": run.throughput,
        "reference": {"npv": ctx.reference_npv, "value": ctx.reference_value, "cost": ctx.planned_cost,
                      "min_acceptable_value": min_val},
        "npv": dist_summary(npv), "value": dist_summary(value), "cost": dist_summary(cost, lower_is_worse=False),
        "roi": dist_summary(s["roi"]), "completion": dist_summary(comp, lower_is_worse=False),
        "completion_within_horizon": prob(comp <= H),
        "probabilities": probabilities, "okr_targets": per_target, "capability_shortage": per_cap,
        "expected_shortage_hours": short_hours, "late_by_project": late,
        "var_cvar": vc, "contributions": contrib[:top], "drivers": sorted(drivers, key=lambda r: -abs(r["spearman_npv"])),
        "sensitivity": sens[:top], "convergence": conv,
        "contingency_hours": float(s["contingency_hours"].mean()), "deferred_months": float(s["deferred_months"].mean()),
        "share_late": float(s["share_late"].mean()), "project_finish": finishes,
        "histogram": {"counts": hist[0].tolist(), "edges": [float(e) for e in hist[1]]},
        "cdf_completion": {"months": list(range(1, int(comp.max()) + 1)),
                           "p": [float((comp <= m).mean()) for m in range(1, int(comp.max()) + 1)]},
        "definitions": {
            "percentiles": "Pxx = xx-th percentile; for value/NPV P10 is the downside, for cost/completion P90",
            "var_cvar": f"loss = plan NPV - simulated NPV; VaR{int(st.var_alpha * 100)} = {st.var_alpha:.0%} quantile "
                        f"of loss; CVaR = mean loss in the worst {1 - st.var_alpha:.0%}",
            "cost_overrun": f"total cost > planned cost x (1 + {st.budget_tolerance:.0%})",
            "lob_budget_breach": "any LOB/funding source spends more than its horizon budget within the horizon",
            "key_dates": f"deadline, or planned finish + {st.date_grace_months} month(s) for mandatory/compliance/critical",
            "okr": "realized attributed benefit delivered within the horizon >= KPI target; okr_plan_targets "
                   "covers the targets the deterministic plan itself meets",
            "capability_shortage": f"any month with > {st.shortage_threshold_hours:g} unmet hours for an active initiative",
            "min_acceptable_value": f"realized business value >= {st.min_value_fraction:.0%} of plan value",
        },
    }
