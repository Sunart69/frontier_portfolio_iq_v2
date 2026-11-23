"""Common solver interface and normalized results.

All MILP engines solve the *same* compiled formulation: the PuLP model built by
`optimizer.PortfolioModel` is compiled once into a sparse matrix form (`MilpMatrix`) and handed to
HiGHS, SCIP and (optionally) CBC. CP-SAT solves its own integer reformulation of the discrete
selection-and-scheduling core (see `cpsat.py`). Every engine returns a `NormalizedResult`, so results
can be compared on equal terms and the proof each engine offers is explicit:

- ``proof = "optimal"``      the engine proved optimality (relative gap below 1e-6);
- ``proof = "within_gap"``   the engine proved the solution is within the requested relative gap;
- ``proof = "feasible"``     a feasible solution without a proven bound inside the requested gap
                              (e.g. time limit reached) — *feasible but unproven*;
- ``proof = "none"``         no solution (infeasible, error, timeout without incumbent, incompatible).
"""
from __future__ import annotations

import enum
import math
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pulp
from scipy import sparse


class SolveStatus(str, enum.Enum):
    OPTIMAL = "optimal"                    # proven within the requested gap
    FEASIBLE = "feasible"                  # incumbent, gap not proven (time limit etc.)
    INFEASIBLE = "infeasible"
    UNBOUNDED = "unbounded"
    TIMEOUT = "timeout"                    # no incumbent before the time limit
    ERROR = "error"
    INCOMPATIBLE = "incompatible"          # formulation not supported by this engine
    UNAVAILABLE = "unavailable"            # engine not installed

    @property
    def has_solution(self) -> bool:
        return self in (SolveStatus.OPTIMAL, SolveStatus.FEASIBLE)


@dataclass
class SolverSettings:
    time_limit: float = 20.0
    mip_gap: float = 0.01
    seed: int = 20270101
    threads: int = 1                       # deterministic by default
    warm_start: Optional[Dict[str, float]] = None   # variable name -> value


@dataclass
class NormalizedResult:
    engine: str
    status: SolveStatus
    objective: Optional[float] = None       # in the model's own (maximize) sense
    bound: Optional[float] = None           # best proven dual bound (maximize: upper bound)
    gap: Optional[float] = None             # relative gap |bound - objective| / max(1, |objective|)
    runtime: float = 0.0
    values: Optional[Dict[str, float]] = None   # variable name -> value
    message: str = ""
    nodes: Optional[int] = None
    requested_gap: float = 0.01
    extra: Dict[str, object] = field(default_factory=dict)

    @property
    def proof(self) -> str:
        if not self.status.has_solution:
            return "none"
        if self.gap is not None and self.gap <= 1e-6:
            return "optimal"
        if self.status == SolveStatus.OPTIMAL and (self.gap is None or self.gap <= self.requested_gap + 1e-9):
            return "within_gap"
        return "feasible"

    def summary(self) -> Dict[str, object]:
        return {"engine": self.engine, "status": self.status.value, "proof": self.proof,
                "objective": self.objective, "bound": self.bound, "gap": self.gap,
                "runtime_s": round(self.runtime, 3), "nodes": self.nodes, "message": self.message,
                **{k: v for k, v in self.extra.items() if isinstance(v, (int, float, str, bool, type(None)))}}


def rel_gap(objective: Optional[float], bound: Optional[float]) -> Optional[float]:
    if objective is None or bound is None or not math.isfinite(bound):
        return None
    return abs(bound - objective) / max(1.0, abs(objective))


# --------------------------------------------------------------------------- compiled model
@dataclass
class MilpMatrix:
    """Sparse, solver-neutral form of a maximization MILP: max c'x + c0, row_lb <= A x <= row_ub."""
    names: List[str]
    c: np.ndarray
    c0: float
    lb: np.ndarray
    ub: np.ndarray
    integer: np.ndarray
    A: sparse.csr_matrix
    row_names: List[str]
    row_lb: np.ndarray
    row_ub: np.ndarray

    @property
    def n_vars(self) -> int:
        return len(self.names)

    @property
    def n_rows(self) -> int:
        return len(self.row_names)

    @property
    def n_int(self) -> int:
        return int(self.integer.sum())

    def index(self) -> Dict[str, int]:
        return {n: k for k, n in enumerate(self.names)}

    def objective_of(self, values: Dict[str, float]) -> float:
        x = np.array([values.get(n, 0.0) for n in self.names])
        return float(self.c @ x + self.c0)

    def max_violation(self, values: Dict[str, float]) -> Dict[str, float]:
        """Largest bound / row / integrality violation of a solution (absolute)."""
        x = np.array([values.get(n, 0.0) for n in self.names])
        act = self.A @ x
        row_v = np.maximum(np.maximum(self.row_lb - act, act - self.row_ub), 0.0)
        bnd_v = np.maximum(np.maximum(self.lb - x, x - self.ub), 0.0)
        int_v = np.abs(x - np.round(x)) * self.integer
        return {"rows": float(row_v.max(initial=0.0)), "bounds": float(bnd_v.max(initial=0.0)),
                "integrality": float(int_v.max(initial=0.0))}


INF = 1e30


def compile_problem(prob: pulp.LpProblem) -> MilpMatrix:
    """Compile a PuLP problem into matrix form (maximization sense)."""
    variables = prob.variables()
    names = [v.name for v in variables]
    idx = {n: k for k, n in enumerate(names)}
    sign = 1.0 if prob.sense == pulp.LpMaximize else -1.0
    c = np.zeros(len(names))
    for v, coef in prob.objective.items():
        c[idx[v.name]] += sign * coef
    c0 = sign * float(prob.objective.constant or 0.0)
    lb = np.array([-INF if v.lowBound is None else float(v.lowBound) for v in variables])
    ub = np.array([INF if v.upBound is None else float(v.upBound) for v in variables])
    integer = np.array([v.cat in (pulp.LpInteger, pulp.LpBinary) for v in variables])
    for k, v in enumerate(variables):
        if v.cat == pulp.LpBinary:
            lb[k], ub[k] = max(lb[k], 0.0), min(ub[k], 1.0)
    rows, cols, vals, rlb, rub, rnames = [], [], [], [], [], []
    for r, (name, con) in enumerate(prob.constraints.items()):
        for v, coef in con.items():
            rows.append(r), cols.append(idx[v.name]), vals.append(float(coef))
        rhs = -float(con.constant or 0.0)
        if con.sense == pulp.LpConstraintLE:
            rlb.append(-INF), rub.append(rhs)
        elif con.sense == pulp.LpConstraintGE:
            rlb.append(rhs), rub.append(INF)
        else:
            rlb.append(rhs), rub.append(rhs)
        rnames.append(name)
    A = sparse.csr_matrix((vals, (rows, cols)), shape=(len(rnames), len(names)))
    return MilpMatrix(names, c, c0, lb, ub, integer, A, rnames, np.array(rlb), np.array(rub))


class Engine:
    """Base class: subclasses implement `available()` and `_solve()`."""
    name = "engine"
    kind = "milp"            # milp | cp | qp | nlp
    role = ""

    @classmethod
    def available(cls) -> bool:
        return False

    def solve(self, compiled, settings: SolverSettings) -> NormalizedResult:
        if not self.available():
            return NormalizedResult(self.name, SolveStatus.UNAVAILABLE, message=f"{self.name} is not installed",
                                    requested_gap=settings.mip_gap)
        t0 = time.time()
        try:
            res = self._solve(compiled, settings)
        except Exception as e:  # engine failures must never take the pipeline down
            res = NormalizedResult(self.name, SolveStatus.ERROR, message=f"{type(e).__name__}: {e}")
        res.runtime = res.runtime or (time.time() - t0)
        res.requested_gap = settings.mip_gap
        return res

    def _solve(self, compiled, settings: SolverSettings) -> NormalizedResult:  # pragma: no cover
        raise NotImplementedError
