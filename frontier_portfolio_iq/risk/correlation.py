"""Correlation between shared risk drivers (Gaussian copula).

Drivers (a vendor, inflation, an engineering-capability shortage, a regulatory change, shared
infrastructure) are sampled jointly: standard normals Z ~ N(0, C) are drawn with the Cholesky factor of
the driver correlation matrix C and mapped to uniforms U = Phi(Z), then through each driver's own
marginal distribution. Every initiative or pool exposed to a driver feels the *same* draw in an
iteration, so initiatives that share a vendor or compete for one capability move together instead
of being treated as independent.

`validate_correlation` checks that C is a valid correlation matrix: square, symmetric, unit diagonal,
entries in [-1, 1] and positive semi-definite (smallest eigenvalue >= -1e-10). An invalid matrix is
rejected with the reason; `nearest_correlation` (Higham's alternating projections) can repair it when
the configuration explicitly allows repair, and the repair is reported.
"""
from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

import numpy as np


class CorrelationError(ValueError):
    pass


def build_matrix(names: Sequence[str], pairs: Dict[Tuple[str, str], float]) -> np.ndarray:
    idx = {n: k for k, n in enumerate(names)}
    C = np.eye(len(names))
    for (a, b), rho in pairs.items():
        if a not in idx or b not in idx:
            raise CorrelationError(f"correlation references unknown driver {a if a not in idx else b!r}")
        if a == b:
            if abs(rho - 1) > 1e-12:
                raise CorrelationError(f"self-correlation of {a} must be 1")
            continue
        C[idx[a], idx[b]] = C[idx[b], idx[a]] = float(rho)
    return C


def validate_correlation(C: np.ndarray, tol: float = 1e-10) -> List[str]:
    issues = []
    C = np.asarray(C, dtype=float)
    if C.ndim != 2 or C.shape[0] != C.shape[1]:
        return ["matrix is not square"]
    if not np.allclose(C, C.T, atol=1e-12):
        issues.append("matrix is not symmetric")
    if not np.allclose(np.diag(C), 1.0, atol=1e-12):
        issues.append("diagonal entries must be 1")
    if np.any(np.abs(C) > 1 + 1e-12):
        issues.append("entries must lie in [-1, 1]")
    if C.size and not issues:
        lam = np.linalg.eigvalsh((C + C.T) / 2).min()
        if lam < -tol:
            issues.append(f"matrix is not positive semi-definite (smallest eigenvalue {lam:.4g})")
    return issues


def nearest_correlation(C: np.ndarray, iters: int = 200, tol: float = 1e-9) -> np.ndarray:
    """Higham (2002) alternating projections onto PSD matrices and unit-diagonal matrices."""
    Y = np.array(C, dtype=float)
    dS = np.zeros_like(Y)
    for _ in range(iters):
        R = Y - dS
        w, V = np.linalg.eigh((R + R.T) / 2)
        X = (V * np.maximum(w, 0)) @ V.T
        dS = X - R
        Y_new = X.copy()
        np.fill_diagonal(Y_new, 1.0)
        if np.linalg.norm(Y_new - Y, "fro") < tol:
            Y = Y_new
            break
        Y = Y_new
    w, V = np.linalg.eigh(Y)
    Y = (V * np.maximum(w, 1e-12)) @ V.T
    d = np.sqrt(np.diag(Y))
    return Y / np.outer(d, d)


def factor(C: np.ndarray) -> np.ndarray:
    """Lower-triangular L with L L^T = C (eigen-based fallback for semi-definite matrices)."""
    if C.size == 0:
        return C
    try:
        return np.linalg.cholesky(C)
    except np.linalg.LinAlgError:
        w, V = np.linalg.eigh(C)
        return V * np.sqrt(np.maximum(w, 0))


def correlated_normals(rng: np.random.Generator, n: int, L: np.ndarray) -> np.ndarray:
    if L.size == 0:
        return np.zeros((n, 0))
    return rng.standard_normal((n, L.shape[0])) @ L.T


def spearman(a: np.ndarray, b: np.ndarray) -> float:
    from scipy.stats import spearmanr
    if np.std(a) == 0 or np.std(b) == 0:
        return 0.0
    return float(spearmanr(a, b).statistic)


def gaussian_to_spearman(rho: float) -> float:
    """Rank correlation implied by a Gaussian copula with linear correlation rho."""
    return 6 / np.pi * np.arcsin(rho / 2)
