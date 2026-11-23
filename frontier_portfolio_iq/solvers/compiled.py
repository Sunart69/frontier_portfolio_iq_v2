"""Compile a portfolio formulation once, solve it with any engine, map the answer back."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional

import pulp

from ..domain import PortfolioData
from ..optimizer import PortfolioModel, PortfolioResult, SolveOptions
from .base import MilpMatrix, NormalizedResult, compile_problem


@dataclass
class CompiledModel:
    data: PortfolioData
    opts: SolveOptions
    model: PortfolioModel
    prob: pulp.LpProblem
    matrix: MilpMatrix
    build_seconds: float
    issues: list

    @property
    def size(self) -> dict:
        M = self.matrix
        return {"variables": M.n_vars, "integer_variables": M.n_int, "constraints": M.n_rows,
                "nonzeros": int(M.A.nnz)}


def compile_portfolio(data: PortfolioData, opts: Optional[SolveOptions] = None,
                      customize: Optional[Callable[[PortfolioModel, pulp.LpProblem], None]] = None) -> CompiledModel:
    """Build the PortfolioModel formulation. `customize(model, prob)` may replace the objective or add
    constraints (Pareto objectives, epsilon constraints, scenario risk terms) before compilation."""
    t0 = time.time()
    opts = opts or SolveOptions()
    model = PortfolioModel(data, opts)
    prob = model.build()
    if customize is not None:
        customize(model, prob)
    matrix = compile_problem(prob)
    return CompiledModel(data, opts, model, prob, matrix, time.time() - t0, list(model.issues))


def to_portfolio_result(compiled: CompiledModel, res: NormalizedResult, label: str = "") -> PortfolioResult:
    """Map engine values back onto x[i,s] / y[ch,t] and build a PortfolioResult (status Optimal/Feasible
    when the engine has a solution, Infeasible otherwise)."""
    t0 = time.time()
    m = compiled.model
    if not res.status.has_solution or res.values is None:
        out = m._result("Infeasible", 0.0, {}, [], t0, list(compiled.issues) + [f"{res.engine}: {res.status.value}"])
        if label:
            out.label = label
        return out
    vals = res.values
    status = "Optimal" if res.status.value == "optimal" else "Feasible"
    obj = res.objective if res.objective is not None else compiled.matrix.objective_of(vals)
    out = m.extract(lambda v: vals.get(v.name, 0.0), status, obj, t0)
    out.solve_seconds = res.runtime
    if label:
        out.label = label
    return out
