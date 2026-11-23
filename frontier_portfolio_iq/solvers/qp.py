"""Optional continuous engines.

OSQP (convex QP) — *resource leveling of a fixed plan*: with the selection and start months fixed, the
model is linear in the hours y. Among all allocations that meet every demand within capacity, eligibility,
transfer and budget rules, OSQP finds the one that minimizes the sum of squared pool-month utilizations
(spreading load across eligible pools and routes), lowering bottleneck pressure without changing the plan.
The result is checked by the same independent validator as every MILP solution.

IPOPT (nonlinear) — reserved for nonlinear benefit curves (e.g. diminishing returns on capacity). It is
detected if `cyipopt` is installed; the current formulation is linear, so no IPOPT model is required and
the registry reports it as optional/not used.
"""
from __future__ import annotations

import time

import numpy as np
from scipy import sparse

from .base import INF, Engine, NormalizedResult, SolverSettings, SolveStatus


class OsqpLevelingEngine(Engine):
    name = "OSQP"
    kind = "qp"
    role = "optional convex QP: resource leveling of a fixed plan"

    @classmethod
    def available(cls) -> bool:
        try:
            import osqp  # noqa: F401
            return True
        except Exception:
            return False

    def _solve(self, compiled, settings: SolverSettings) -> NormalizedResult:
        import osqp
        if compiled.opts.fixed_starts is None:
            return NormalizedResult(self.name, SolveStatus.INCOMPATIBLE,
                                    message="leveling needs a fixed plan (fixed_starts)")
        M, m = compiled.matrix, compiled.model
        idx = M.index()
        n = M.n_vars
        # integers are fixed by the plan: pin them via bounds
        lb, ub = M.lb.copy(), M.ub.copy()
        for (i, st), v in m.x.items():
            k = idx[v.name]
            val = 1.0 if compiled.opts.fixed_starts.get(i) == st else 0.0
            lb[k] = ub[k] = val
        rows = []
        for (pl, t) in {(ch.pool_id, t) for ch in m.channels for t in m.ch_months[ch.cid]}:
            A_pt = m.A[pl, t]
            if A_pt <= 0:
                continue
            a = np.zeros(n)
            for ch in m.channels:
                if ch.pool_id == pl and t in m.ch_months[ch.cid]:
                    a[idx[m.y[ch.cid, t].name]] = 1.0 / A_pt
            rows.append(sparse.csr_matrix(a))
        U = sparse.vstack(rows) if rows else sparse.csr_matrix((0, n))
        P = (U.T @ U) * 2.0 + sparse.identity(n) * 1e-9
        q = -M.c * 1e-6                 # keep the original objective as a faint tie-breaker
        Acon = sparse.vstack([M.A, sparse.identity(n)]).tocsc()
        l = np.concatenate([np.maximum(M.row_lb, -INF), np.maximum(lb, -INF)])
        u = np.concatenate([np.minimum(M.row_ub, INF), np.minimum(ub, INF)])
        l = np.where(l <= -INF, -np.inf, l)
        u = np.where(u >= INF, np.inf, u)
        solver = osqp.OSQP()
        solver.setup(P=sparse.triu(P).tocsc(), q=q, A=Acon, l=l, u=u, verbose=False,
                     eps_abs=1e-6, eps_rel=1e-6, max_iter=200000, time_limit=float(settings.time_limit),
                     polishing=True)
        t0 = time.time()
        r = solver.solve()
        rt = time.time() - t0
        status = str(r.info.status)
        if "solved" not in status.lower() or r.x is None:
            return NormalizedResult(self.name, SolveStatus.ERROR, runtime=rt, message=status)
        x = np.clip(r.x, lb, ub)
        vals = dict(zip(M.names, (float(v) for v in x)))
        util = U @ x if U.shape[0] else np.zeros(0)
        return NormalizedResult(self.name, SolveStatus.OPTIMAL, M.objective_of(vals), None, None, rt, vals,
                                status, extra={"peak_utilization": float(util.max(initial=0.0)),
                                               "sum_sq_utilization": float((util ** 2).sum())})


class IpoptEngine(Engine):
    name = "IPOPT"
    kind = "nlp"
    role = "optional nonlinear engine (nonlinear benefit curves); not required by the linear model"

    @classmethod
    def available(cls) -> bool:
        try:
            import cyipopt  # noqa: F401
            return True
        except Exception:
            return False

    def _solve(self, compiled, settings: SolverSettings) -> NormalizedResult:
        return NormalizedResult(self.name, SolveStatus.INCOMPATIBLE,
                                message="no nonlinear terms in the current formulation")
