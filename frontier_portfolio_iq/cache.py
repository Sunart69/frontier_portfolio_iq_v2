"""Result cache keyed by model fingerprints and configuration hashes.

Key = SHA-256 of (stage, canonical portfolio model, uncertainty model, stage configuration). Any change
to an input that can affect the result — a budget line, a distribution, a Pareto setting, the random
seed — changes the key, so stale results are never reused; identical inputs reuse the stored result.
"""
from __future__ import annotations

import os
import pickle
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from .canonical import digest

# Bump when result structures change so caches written by older code are never reused.
CACHE_SALT = "fpiq-2.0.0-r4"


@dataclass
class ResultCache:
    directory: Optional[str] = None           # None = in-memory only
    hits: int = 0
    misses: int = 0
    _mem: Dict[str, Any] = field(default_factory=dict)
    log: list = field(default_factory=list)

    def key(self, stage: str, *parts) -> str:
        return f"{stage}-{digest([CACHE_SALT, stage, *parts])[:24]}"

    def get_or_compute(self, stage: str, parts, fn: Callable[[], Any]) -> Any:
        k = self.key(stage, *parts)
        if k in self._mem:
            self.hits += 1
            self.log.append(("hit", k))
            return self._mem[k]
        if self.directory:
            path = os.path.join(self.directory, k + ".pkl")
            if os.path.exists(path):
                try:
                    with open(path, "rb") as fh:
                        val = pickle.load(fh)
                    self._mem[k] = val
                    self.hits += 1
                    self.log.append(("hit", k))
                    return val
                except Exception:
                    pass
        self.misses += 1
        self.log.append(("miss", k))
        val = fn()
        self._mem[k] = val
        if self.directory:
            os.makedirs(self.directory, exist_ok=True)
            try:
                with open(os.path.join(self.directory, k + ".pkl"), "wb") as fh:
                    pickle.dump(val, fh)
            except Exception:
                pass
        return val

    def stats(self) -> Dict[str, int]:
        return {"hits": self.hits, "misses": self.misses, "entries": len(self._mem)}
