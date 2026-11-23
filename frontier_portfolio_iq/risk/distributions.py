"""Probability distributions sampled by inverse CDF.

Every distribution is sampled as ``dist.ppf(u)`` from uniforms ``u``. That one mechanism lets the
engine (a) impose correlation through a Gaussian copula — correlated normals become correlated uniforms
— and (b) use common random numbers across portfolios. No distribution is assumed normal by default.

Text syntax (Excel intake and YAML):

  const(1)                       constant
  tri(min, mode, max)            triangular
  pert(min, mode, max[, lambda]) Beta-PERT, lambda = 4 by default
  normal(mean, sd)               normal (use only where symmetric, unbounded errors are justified)
  truncnormal(mean, sd, lo, hi)  normal truncated to [lo, hi]
  lognormal(median, sigma)       lognormal: exp(ln(median) + sigma * Z)
  uniform(lo, hi)
  bernoulli(p)                   1 with probability p, else 0
  discrete(v1:p1, v2:p2, ...)    discrete scenario distribution (probabilities must sum to 1)
  empirical(x1, x2, ...)         historical observations, each equally likely (inverse empirical CDF)
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Tuple

import numpy as np
from scipy import stats

KINDS = ("const", "tri", "pert", "normal", "truncnormal", "lognormal", "uniform", "bernoulli", "discrete",
         "empirical")


@dataclass(frozen=True)
class Dist:
    kind: str
    params: Tuple[float, ...] = ()
    values: Tuple[float, ...] = ()     # discrete values / empirical sample
    probs: Tuple[float, ...] = ()      # discrete probabilities

    def __post_init__(self):
        k, p = self.kind, self.params
        if k not in KINDS:
            raise ValueError(f"unknown distribution {k!r}; use one of {KINDS}")
        need = {"const": 1, "tri": 3, "normal": 2, "truncnormal": 4, "lognormal": 2, "uniform": 2, "bernoulli": 1}
        if k in need and len(p) != need[k]:
            raise ValueError(f"{k} needs {need[k]} parameters, got {len(p)}")
        if k in ("tri", "pert"):
            a, b, c = p[:3]
            if not (a <= b <= c) or a == c:
                raise ValueError(f"{k}: need min <= mode <= max and min < max, got {p}")
        if k in ("normal", "truncnormal", "lognormal") and p[1] <= 0:
            raise ValueError(f"{k}: spread must be positive")
        if k == "truncnormal" and not p[2] < p[3]:
            raise ValueError("truncnormal: need lo < hi")
        if k == "lognormal" and p[0] <= 0:
            raise ValueError("lognormal: median must be positive")
        if k == "uniform" and not p[0] < p[1]:
            raise ValueError("uniform: need lo < hi")
        if k == "bernoulli" and not 0 <= p[0] <= 1:
            raise ValueError("bernoulli: p must be in [0, 1]")
        if k == "discrete":
            if not self.values or len(self.values) != len(self.probs):
                raise ValueError("discrete: need value:probability pairs")
            if any(q < 0 for q in self.probs) or abs(sum(self.probs) - 1) > 1e-6:
                raise ValueError(f"discrete: probabilities must be >= 0 and sum to 1 (got {sum(self.probs):.4f})")
        if k == "empirical" and len(self.values) < 2:
            raise ValueError("empirical: need at least two observations")

    # ------------------------------------------------------------------ sampling
    def ppf(self, u: np.ndarray) -> np.ndarray:
        u = np.clip(np.asarray(u, dtype=float), 1e-12, 1 - 1e-12)
        k, p = self.kind, self.params
        if k == "const":
            return np.full_like(u, p[0])
        if k == "tri":
            a, b, c = p
            fc = (b - a) / (c - a)
            left = a + np.sqrt(u * (c - a) * (b - a))
            right = c - np.sqrt((1 - u) * (c - a) * (c - b))
            return np.where(u < fc, left, right)
        if k == "pert":
            a, b, c = p[:3]
            lam = p[3] if len(p) > 3 else 4.0
            al = 1 + lam * (b - a) / (c - a)
            be = 1 + lam * (c - b) / (c - a)
            return a + (c - a) * stats.beta.ppf(u, al, be)
        if k == "normal":
            return p[0] + p[1] * stats.norm.ppf(u)
        if k == "truncnormal":
            m, s, lo, hi = p
            return stats.truncnorm.ppf(u, (lo - m) / s, (hi - m) / s, loc=m, scale=s)
        if k == "lognormal":
            return np.exp(math.log(p[0]) + p[1] * stats.norm.ppf(u))
        if k == "uniform":
            return p[0] + (p[1] - p[0]) * u
        if k == "bernoulli":
            return (u > 1 - p[0]).astype(float)   # upper tail = event, so high uniforms are adverse
        if k == "discrete":
            vals = np.array(self.values)
            order = np.argsort(vals)
            cum = np.cumsum(np.array(self.probs)[order])
            idx = np.searchsorted(cum, u, side="left")
            return vals[order][np.clip(idx, 0, len(vals) - 1)]
        if k == "empirical":      # inverse of the empirical CDF: every observation equally likely
            xs = np.sort(np.array(self.values))
            return xs[np.minimum((u * len(xs)).astype(int), len(xs) - 1)]
        raise AssertionError(k)

    # ------------------------------------------------------------------ moments (for documentation/tests)
    def mean(self) -> float:
        k, p = self.kind, self.params
        if k == "const":
            return p[0]
        if k == "tri":
            return sum(p) / 3
        if k == "pert":
            a, b, c = p[:3]
            lam = p[3] if len(p) > 3 else 4.0
            return (a + lam * b + c) / (lam + 2)
        if k == "normal":
            return p[0]
        if k == "truncnormal":
            m, s, lo, hi = p
            return float(stats.truncnorm.mean((lo - m) / s, (hi - m) / s, loc=m, scale=s))
        if k == "lognormal":
            return p[0] * math.exp(p[1] ** 2 / 2)
        if k == "uniform":
            return (p[0] + p[1]) / 2
        if k == "bernoulli":
            return p[0]
        if k == "discrete":
            return float(np.dot(self.values, self.probs))
        if k == "empirical":
            return float(np.mean(self.values))
        raise AssertionError(k)

    def describe(self) -> str:
        if self.kind == "discrete":
            return "discrete(" + ", ".join(f"{v:g}:{q:g}" for v, q in zip(self.values, self.probs)) + ")"
        if self.kind == "empirical":
            return f"empirical({len(self.values)} observations)"
        return f"{self.kind}(" + ", ".join(f"{v:g}" for v in self.params) + ")"


_RX = re.compile(r"^\s*([a-zA-Z_]+)\s*\((.*)\)\s*$")
ALIASES = {"triangular": "tri", "betapert": "pert", "beta_pert": "pert", "beta-pert": "pert", "constant": "const",
           "fixed": "const", "trunc_normal": "truncnormal", "truncatednormal": "truncnormal", "log_normal": "lognormal",
           "scenario": "discrete", "scenarios": "discrete", "hist": "empirical", "historical": "empirical"}


def parse_dist(text) -> Dist:
    """Parse the text syntax; a bare number is a constant."""
    if isinstance(text, Dist):
        return text
    if isinstance(text, (int, float)):
        return Dist("const", (float(text),))
    m = _RX.match(str(text))
    if not m:
        try:
            return Dist("const", (float(text),))
        except ValueError:
            raise ValueError(f"cannot parse distribution {text!r}") from None
    kind = m.group(1).lower()
    kind = ALIASES.get(kind, kind)
    body = m.group(2).strip()
    if kind == "discrete":
        vals, probs = [], []
        for part in body.split(","):
            v, q = part.split(":")
            vals.append(float(v)), probs.append(float(q))
        return Dist("discrete", (), tuple(vals), tuple(probs))
    nums = tuple(float(x) for x in body.split(",") if x.strip())
    if kind == "empirical":
        return Dist("empirical", (), nums)
    return Dist(kind, nums)
