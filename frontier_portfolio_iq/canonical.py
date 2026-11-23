"""Canonical, versioned portfolio model.

Every stage of the decision pipeline (optimization, validation, Pareto analysis, Monte Carlo,
recommendation, dashboards, deck) works from one canonical representation of the portfolio and
stamps its outputs with that representation's fingerprint. Two results can only be compared, or
placed on the same Pareto frontier, when their fingerprints and scenario keys match.

The fingerprint is a SHA-256 over a sorted JSON rendering of the data, so it changes whenever any
input that can affect a result changes (a budget line, a pool's FTE, a distribution parameter) and is
identical across runs, machines and Python versions for identical inputs.
"""
from __future__ import annotations

import dataclasses
import enum
import hashlib
import json
from typing import Any, Dict, Optional

SCHEMA_VERSION = "fpiq-canonical/2.0"


def _plain(obj: Any) -> Any:
    """Recursively convert dataclasses, enums, tuples-as-keys and sets into JSON-stable values."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        out = {f.name: _plain(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
        if "funding_shares" in out and hasattr(obj, "shares"):
            out["funding_shares"] = _plain(obj.shares())   # empty = 100% primary LOB: one canonical form
        return out
    if isinstance(obj, enum.Enum):
        return obj.value
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            key = "|".join(str(x) for x in k) if isinstance(k, tuple) else str(k)
            out[key] = _plain(v)
        return dict(sorted(out.items()))
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    if isinstance(obj, (set, frozenset)):
        return sorted(_plain(v) for v in obj)
    if isinstance(obj, bool) or obj is None:
        return obj
    if isinstance(obj, (int, float)):
        return round(float(obj), 9)        # 180 and 180.0 are the same input: one canonical form
    return obj


def canonical_dict(data, uncertainty=None, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    doc = {"schema": SCHEMA_VERSION, "portfolio": _plain(data)}
    if uncertainty is not None:
        doc["uncertainty"] = _plain(uncertainty)
    if extra:
        doc["extra"] = _plain(extra)
    return doc


def digest(obj: Any) -> str:
    blob = json.dumps(_plain(obj), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def fingerprint(data, uncertainty=None, extra: Optional[Dict[str, Any]] = None) -> str:
    """Short, stable identifier of a portfolio model version (first 16 hex chars of SHA-256)."""
    return digest(canonical_dict(data, uncertainty, extra))[:16]


@dataclasses.dataclass(frozen=True)
class ModelVersion:
    """Stamp carried by every pipeline artifact."""
    fingerprint: str
    scenario: str = "baseline"
    schema: str = SCHEMA_VERSION

    def compatible(self, other: "ModelVersion") -> bool:
        return self.fingerprint == other.fingerprint and self.scenario == other.scenario

    def label(self) -> str:
        return f"{self.scenario}@{self.fingerprint}"
