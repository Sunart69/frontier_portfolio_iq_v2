"""Risk-aware optimization: a scenario-based stochastic MILP (sample average approximation, SAA).

First stage: the usual portfolio decisions x[i,s] and y (selection, start months, routing) under every
deterministic rule — capacity, ring-fencing, transfers, budgets, dependencies, commitments.

Scenarios: S futures are drawn from the same uncertainty model the Monte Carlo uses (on a separate
random stream, so the plan is later evaluated *out of sample*). In scenario k each initiative i has a
realization factor beta_ik = benefit multiplier x (1 - failure x loss) and a cost factor kappa_ik =
effort multiplier x price drivers. Scenario value of a plan:

    V_k = sum_{i,s} x[i,s] * ( value_i * disc(i,s) * beta_ik  -  total_cost_i * (kappa_ik - 1) )

i.e. time-adjusted business value realized, minus the cost overrun against plan. With loss L_k = -V_k,
CVaR at level alpha is linearized (Rockafellar & Uryasev, 2000):

    CVaR = eta + 1 / ((1 - alpha) S) * sum_k u_k,   u_k >= L_k - eta,  u_k >= 0

Objective:  maximize  (1 - lambda) * (1/S) sum_k V_k  -  lambda * CVaR
Optional chance constraint on budget (big-M with binaries z_k):
    sum_{i,s} x[i,s] total_cost_i kappa_ik <= B + M z_k,   sum_k z_k <= floor((1 - q) S)

Scope and limitation (also shown on the dashboard): scenario capacity, schedule slippage and dependency
delays are *not* in the stochastic MILP — modelling month-by-month recourse in every scenario would
multiply the model size by S. Those effects are captured when the resulting plan is evaluated by the
schedule-aware Monte Carlo, on the same simulated futures as every other candidate.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np
import pulp

from ..domain import PortfolioData, PortfolioTier
from ..optimizer import EPS_HOURS, SolveOptions
from ..solvers.base import SolverSettings
from ..solvers.compiled import compile_portfolio, to_portfolio_result
from ..solvers.milp import HighsEngine
from ..solvers.validate import validate_result
from .simulate import _driver_factor, _uniform, sample_drivers
from .uncertainty import UncertaintyModel, selects_project

SAA_CHUNK = 900001     # random-stream offset: scenarios never coincide with Monte Carlo chunks


@dataclass
class StochasticConfig:
    scenarios: int = 40
    lam: float = 0.35
    alpha: float = 0.90
    budget_confidence: Optional[float] = None   # e.g. 0.8 -> P(cost <= budget envelope) >= 80% in-sample
    budget_envelope: Optional[float] = None     # $K; default = total budget lines
    time_limit: float = 30.0
    mip_gap: float = 0.01


def scenario_factors(data: PortfolioData, um: UncertaintyModel, S: int):
    seed = um.settings.seed
    drv = sample_drivers(um, seed, SAA_CHUNK, S)
    beta, kappa = {}, {}
    for i, p in data.projects.items():
        u = um.projects[i]
        b = u.benefit.ppf(_uniform(seed, SAA_CHUNK, f"P|{i}|benefit", S))
        f = _uniform(seed, SAA_CHUNK, f"P|{i}|failure", S) > 1 - u.failure_prob
        e = u.effort.ppf(_uniform(seed, SAA_CHUNK, f"P|{i}|effort", S))
        for d in um.drivers:
            if selects_project(d.applies_to, p, data):
                if d.effect == "benefit":
                    b = b * _driver_factor(d, drv[d.driver_id])
                elif d.effect in ("cost", "effort"):
                    e = e * _driver_factor(d, drv[d.driver_id])
        beta[i] = b * np.where(f, 1 - u.failure_loss, 1.0)
        kappa[i] = e
    return beta, kappa


def risk_aware_plan(data: PortfolioData, um: UncertaintyModel, cfg: Optional[StochasticConfig] = None,
                    opts: Optional[SolveOptions] = None):
    cfg = cfg or StochasticConfig()
    opts = opts or SolveOptions(tier=PortfolioTier.COMMITTED, label="Risk-aware (stochastic MILP)")
    S = cfg.scenarios
    beta, kappa = scenario_factors(data, um, S)
    st = data.settings
    envelope = cfg.budget_envelope if cfg.budget_envelope is not None else sum(b.amount for b in data.budgets)
    info: Dict[str, object] = {"scenarios": S, "lambda": cfg.lam, "alpha": cfg.alpha,
                               "budget_confidence": cfg.budget_confidence, "budget_envelope": envelope}

    def customize(model, prob):
        P = data.projects
        V = []
        for k in range(S):
            terms = []
            for (i, s_), v in model.x.items():
                p = P[i]
                e_min = p.earliest_start + p.duration - 1
                disc = 1 - st.schedule_discount * max(0, s_ + p.duration - 1 - e_min) / st.horizon
                coef = p.value * disc * float(beta[i][k]) - p.total_cost * (float(kappa[i][k]) - 1.0)
                terms.append(coef * v)
            V.append(pulp.lpSum(terms))
        eta = pulp.LpVariable("cvar_eta")
        u = [pulp.LpVariable(f"cvar_u_{k}", lowBound=0) for k in range(S)]
        for k in range(S):
            prob += u[k] >= -V[k] - eta, f"cvar_{k}"
        cvar = eta + pulp.lpSum(u) / ((1 - cfg.alpha) * S)
        mean = pulp.lpSum(V) / S
        route = pulp.lpSum((ch.transfer_cost_rate + ch.incremental_cost_rate) / 1000.0 * model.y[ch.cid, t]
                           for ch in model.channels for t in model.ch_months[ch.cid])
        idle = pulp.lpSum(model.y.values())
        prob.setObjective((1 - cfg.lam) * mean - cfg.lam * cvar - route - EPS_HOURS * idle)
        if cfg.budget_confidence:
            M = sum(P[i].total_cost * float(max(kappa[i])) for i in P) + 1.0
            z = [pulp.LpVariable(f"bud_z_{k}", cat="Binary") for k in range(S)]
            for k in range(S):
                prob += pulp.lpSum(P[i].total_cost * float(kappa[i][k]) * v for (i, s_), v in model.x.items()) \
                    <= envelope + M * z[k], f"bud_chance_{k}"
            prob += pulp.lpSum(z) <= math.floor((1 - cfg.budget_confidence) * S), "bud_chance_count"

    compiled = compile_portfolio(data, opts, customize)
    compiled.custom_objective = True
    nres = HighsEngine().solve(compiled, SolverSettings(time_limit=cfg.time_limit, mip_gap=cfg.mip_gap,
                                                        seed=um.settings.seed))
    res = to_portfolio_result(compiled, nres, "Risk-aware (stochastic MILP)")
    info.update(status=nres.status.value, proof=nres.proof, gap=nres.gap, runtime=nres.runtime,
                objective=nres.objective, size=compiled.size)
    if res.feasible:
        val = validate_result(res, check_objective=False)
        info["validated"] = val.feasible
        info["validation_failures"] = val.failures()[:5]
        # in-sample statistics of the chosen plan
        Vk = []
        for k in range(S):
            tot = 0.0
            for i, s_ in res.starts.items():
                p = data.projects[i]
                e_min = p.earliest_start + p.duration - 1
                disc = 1 - st.schedule_discount * max(0, s_ + p.duration - 1 - e_min) / st.horizon
                tot += p.value * disc * float(beta[i][k]) - p.total_cost * (float(kappa[i][k]) - 1.0)
            Vk.append(tot)
        Vk = np.array(Vk)
        q = np.quantile(-Vk, cfg.alpha)
        info["in_sample"] = {"mean_value": float(Vk.mean()), "cvar_loss": float((-Vk)[-Vk >= q].mean())}
    info["limitation"] = ("Capacity, schedule and dependency uncertainty are evaluated ex post by the Monte Carlo, "
                          "not inside the stochastic MILP.")
    return res, info
