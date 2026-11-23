"""MILP engines over the compiled formulation: HiGHS (primary), SCIP (independent validation), CBC
(optional benchmark). All three receive the identical sparse model, so a disagreement between them
is a numerical or algorithmic signal, never a modelling difference."""
from __future__ import annotations

import time

import numpy as np
import pulp

from .base import INF, Engine, NormalizedResult, SolverSettings, SolveStatus, rel_gap


class HighsEngine(Engine):
    name = "HiGHS"
    role = "primary LP/MILP optimizer"

    @classmethod
    def available(cls) -> bool:
        try:
            import highspy  # noqa: F401
            return True
        except Exception:
            return False

    def _solve(self, compiled, settings: SolverSettings) -> NormalizedResult:
        import highspy
        M = compiled.matrix
        inf = highspy.kHighsInf
        h = highspy.Highs()
        h.setOptionValue("output_flag", False)
        h.setOptionValue("time_limit", float(settings.time_limit))
        h.setOptionValue("mip_rel_gap", float(settings.mip_gap))
        h.setOptionValue("random_seed", int(settings.seed) % 2147483647)
        # Note: HiGHS fixes its thread pool once per process, so "threads" is not set per solve.
        lp = highspy.HighsLp()
        lp.num_col_, lp.num_row_ = M.n_vars, M.n_rows
        lp.col_cost_ = M.c.astype(float)
        lp.offset_ = float(M.c0)
        lp.col_lower_ = np.where(M.lb <= -INF, -inf, M.lb)
        lp.col_upper_ = np.where(M.ub >= INF, inf, M.ub)
        lp.row_lower_ = np.where(M.row_lb <= -INF, -inf, M.row_lb)
        lp.row_upper_ = np.where(M.row_ub >= INF, inf, M.row_ub)
        csc = M.A.tocsc()
        lp.a_matrix_.format_ = highspy.MatrixFormat.kColwise
        lp.a_matrix_.start_ = csc.indptr.astype(np.int32)
        lp.a_matrix_.index_ = csc.indices.astype(np.int32)
        lp.a_matrix_.value_ = csc.data.astype(float)
        if M.n_int:
            lp.integrality_ = [highspy.HighsVarType.kInteger if b else highspy.HighsVarType.kContinuous
                               for b in M.integer]
        lp.sense_ = highspy.ObjSense.kMaximize
        h.passModel(lp)
        if settings.warm_start:
            sol = highspy.HighsSolution()
            sol.col_value = [float(settings.warm_start.get(n, 0.0)) for n in M.names]
            h.setSolution(sol)
        t0 = time.time()
        h.run()
        rt = time.time() - t0
        ms = h.getModelStatus()
        info = h.getInfo()
        has_sol = info.primal_solution_status == 2   # kSolutionStatusFeasible
        name = h.modelStatusToString(ms)
        if ms == highspy.HighsModelStatus.kOptimal:
            st = SolveStatus.OPTIMAL
        elif ms in (highspy.HighsModelStatus.kInfeasible,):
            st = SolveStatus.INFEASIBLE
        elif ms in (highspy.HighsModelStatus.kUnbounded, highspy.HighsModelStatus.kUnboundedOrInfeasible):
            st = SolveStatus.UNBOUNDED
        elif has_sol:
            st = SolveStatus.FEASIBLE
        elif ms == highspy.HighsModelStatus.kTimeLimit:
            st = SolveStatus.TIMEOUT
        else:
            st = SolveStatus.ERROR
        if not st.has_solution:
            return NormalizedResult(self.name, st, runtime=rt, message=name)
        obj = float(info.objective_function_value)
        bound = float(info.mip_dual_bound) if M.n_int else obj
        if not np.isfinite(bound):
            bound = None
        vals = dict(zip(M.names, (float(v) for v in h.getSolution().col_value)))
        return NormalizedResult(self.name, st, obj, bound, rel_gap(obj, bound), rt, vals, name,
                                nodes=int(info.mip_node_count) if M.n_int else 0)


class ScipEngine(Engine):
    name = "SCIP"
    role = "independent MILP validation"

    @classmethod
    def available(cls) -> bool:
        try:
            import pyscipopt  # noqa: F401
            return True
        except Exception:
            return False

    def _solve(self, compiled, settings: SolverSettings) -> NormalizedResult:
        from pyscipopt import Model, quicksum
        M = compiled.matrix
        m = Model("FrontierPortfolioIQ")
        m.hideOutput()
        m.setParam("limits/time", float(settings.time_limit))
        m.setParam("limits/gap", float(settings.mip_gap))
        m.setParam("randomization/randomseedshift", int(settings.seed) % 2147483647)
        xs = []
        for k, n in enumerate(M.names):
            lo = None if M.lb[k] <= -INF else float(M.lb[k])
            hi = None if M.ub[k] >= INF else float(M.ub[k])
            vt = "C"
            if M.integer[k]:
                vt = "B" if (lo == 0.0 and hi == 1.0) else "I"
            xs.append(m.addVar(name=f"v{k}", vtype=vt, lb=lo, ub=hi, obj=float(M.c[k])))
        if M.c0:
            m.addVar(name="const", vtype="C", lb=1.0, ub=1.0, obj=float(M.c0))
        m.setMaximize()
        A = M.A.tocsr()
        for r in range(M.n_rows):
            lo_, hi_ = A.indptr[r], A.indptr[r + 1]
            expr = quicksum(float(v) * xs[j] for j, v in zip(A.indices[lo_:hi_], A.data[lo_:hi_]))
            lb, ub = M.row_lb[r], M.row_ub[r]
            if lb > -INF and ub < INF and abs(lb - ub) <= 1e-12:
                m.addCons(expr == float(lb), name=f"r{r}")
            else:
                if lb > -INF:
                    m.addCons(expr >= float(lb), name=f"r{r}l")
                if ub < INF:
                    m.addCons(expr <= float(ub), name=f"r{r}u")
        if settings.warm_start:
            sol = m.createSol()
            for k, n in enumerate(M.names):
                m.setSolVal(sol, xs[k], float(settings.warm_start.get(n, 0.0)))
            m.addSol(sol, free=True)
        t0 = time.time()
        m.optimize()
        rt = time.time() - t0
        status = m.getStatus()
        nsols = m.getNSols()
        if status in ("optimal", "gaplimit"):
            st = SolveStatus.OPTIMAL
        elif status == "infeasible":
            st = SolveStatus.INFEASIBLE
        elif status in ("unbounded", "inforunbd"):
            st = SolveStatus.UNBOUNDED
        elif nsols > 0:
            st = SolveStatus.FEASIBLE
        elif status in ("timelimit", "nodelimit", "totalnodelimit", "stallnodelimit"):
            st = SolveStatus.TIMEOUT
        else:
            st = SolveStatus.ERROR
        if not st.has_solution:
            return NormalizedResult(self.name, st, runtime=rt, message=status)
        obj = float(m.getObjVal())
        bound = float(m.getDualbound())
        vals = {n: float(m.getVal(xs[k])) for k, n in enumerate(M.names)}
        return NormalizedResult(self.name, st, obj, bound, rel_gap(obj, bound), rt, vals, status,
                                nodes=int(m.getNNodes()))


class CbcEngine(Engine):
    name = "CBC"
    role = "optional additional MILP benchmark"

    @classmethod
    def available(cls) -> bool:
        return "PULP_CBC_CMD" in pulp.listSolvers(onlyAvailable=True)

    def _solve(self, compiled, settings: SolverSettings) -> NormalizedResult:
        prob = compiled.prob
        solver = pulp.PULP_CBC_CMD(msg=False, timeLimit=max(1, int(settings.time_limit)),
                                   gapRel=settings.mip_gap, threads=max(1, settings.threads),
                                   options=[f"randomSeed {int(settings.seed) % 2147483647}"])
        t0 = time.time()
        prob.solve(solver)
        rt = time.time() - t0
        status = pulp.LpStatus[prob.status]
        variables = prob.variables()
        has_sol = any(v.varValue is not None for v in variables)
        # PuLP reports "Optimal" also when CBC stops at the time limit with an incumbent; sol_status
        # distinguishes a proven solution (1) from an integer-feasible one (2).
        if status == "Optimal" and getattr(prob, "sol_status", 1) == pulp.LpSolutionOptimal:
            st = SolveStatus.OPTIMAL
        elif status == "Infeasible":
            st = SolveStatus.INFEASIBLE
        elif status == "Unbounded":
            st = SolveStatus.UNBOUNDED
        elif has_sol:
            st = SolveStatus.FEASIBLE
        else:
            st = SolveStatus.TIMEOUT
        if not st.has_solution:
            return NormalizedResult(self.name, st, runtime=rt, message=status)
        vals = {v.name: float(v.varValue or 0.0) for v in variables}
        obj = compiled.matrix.objective_of(vals)
        # PuLP's CBC interface does not return the dual bound: the result can be feasible-but-unproven
        # from our point of view even when CBC reports "Optimal" within its gap.
        return NormalizedResult(self.name, st, obj, None, None, rt, vals,
                                f"{status} (CBC dual bound not exposed through PuLP)")
