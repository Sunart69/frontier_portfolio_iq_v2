"""Semester cycles and their workback schedules."""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

from .config import CadenceConfig

ONE_DAY = dt.timedelta(days=1)


@dataclass(frozen=True, order=True)
class CycleId:
    year: int
    half: int  # 1 = Jan–Jun, 2 = Jul–Dec

    @classmethod
    def parse(cls, text: str) -> "CycleId":
        m = re.fullmatch(r"\s*(\d{4})\s*-?\s*H([12])\s*", str(text), re.IGNORECASE)
        if not m:
            raise ValueError(f"Cycle id must look like 2027-H2, got {text!r}")
        return cls(int(m.group(1)), int(m.group(2)))

    def __str__(self) -> str:
        return f"{self.year}-H{self.half}"

    @property
    def start(self) -> dt.date:
        return dt.date(self.year, 1 if self.half == 1 else 7, 1)

    @property
    def end(self) -> dt.date:
        return dt.date(self.year, 6, 30) if self.half == 1 else dt.date(self.year, 12, 31)

    @property
    def label(self) -> str:
        return f"{self} ({'January–June' if self.half == 1 else 'July–December'} {self.year})"

    def next(self) -> "CycleId":
        return CycleId(self.year, 2) if self.half == 1 else CycleId(self.year + 1, 1)

    def prev(self) -> "CycleId":
        return CycleId(self.year, 1) if self.half == 2 else CycleId(self.year - 1, 2)


def is_business_day(d: dt.date, holidays: Iterable[dt.date]) -> bool:
    return d.weekday() < 5 and d not in set(holidays)


def previous_business_day(d: dt.date, holidays) -> dt.date:
    while not is_business_day(d, holidays):
        d -= ONE_DAY
    return d


def business_days_between(start: dt.date, end: dt.date, holidays) -> int:
    """Business days after `start` up to and including `end` (0 if end <= start)."""
    n, d = 0, start
    while d < end:
        d += ONE_DAY
        if is_business_day(d, holidays):
            n += 1
    return n


def add_business_days(d: dt.date, n: int, holidays) -> dt.date:
    while n > 0:
        d += ONE_DAY
        if is_business_day(d, holidays):
            n -= 1
    return d


@dataclass
class Milestone:
    key: str
    name: str
    date: dt.date
    owner_role: str = ""
    package: Optional[str] = None

    @property
    def is_due_date(self) -> bool:
        return self.package is not None


@dataclass
class CycleSchedule:
    cycle: CycleId
    decision_date: dt.date
    milestones: List[Milestone]

    def get(self, key: str) -> Milestone:
        return next(m for m in self.milestones if m.key == key)

    def date(self, key: str) -> dt.date:
        return self.get(key).date

    def due(self, package: str) -> dt.date:
        return next(m.date for m in self.milestones if m.package == package)

    @property
    def kickoff(self) -> dt.date:
        return self.date("kickoff")

    @property
    def publish(self) -> dt.date:
        return self.date("publish")

    def phase(self, today: dt.date) -> str:
        """Where the cycle stands on `today`."""
        if today < self.kickoff:
            return "not started"
        if today > self.publish:
            return "closed"
        last = None
        for m in sorted(self.milestones, key=lambda m: m.date):
            if m.date <= today:
                last = m
        names = {"kickoff": "collecting inputs", "templates": "collecting inputs",
                 "validation": "validating inputs", "approvals": "transfer approvals",
                 "solver_run": "optimizing", "preread": "LT pre-read", "decision": "decision made",
                 "publish": "published"}
        if last is None:
            return "collecting inputs"
        if last.package:
            return "collecting inputs"
        return names.get(last.key, last.name)

    def as_rows(self) -> List[Dict]:
        return [{"key": m.key, "name": m.name, "date": m.date.isoformat(), "weekday": m.date.strftime("%a"),
                 "owner": m.owner_role, "package": m.package or ""}
                for m in sorted(self.milestones, key=lambda m: (m.date, m.key))]


def decision_date(cycle: CycleId, cfg: CadenceConfig) -> dt.date:
    o = cfg.cycle_overrides.get(str(cycle), {})
    if "decision_date" in o:
        return o["decision_date"]
    d = cycle.start - dt.timedelta(weeks=cfg.weeks_before_half_start)
    while d.weekday() != cfg.meeting_weekday:
        d -= ONE_DAY
    return previous_business_day(d, cfg.holidays)


def build_schedule(cycle, cfg: CadenceConfig) -> CycleSchedule:
    cycle = CycleId.parse(str(cycle))
    t0 = decision_date(cycle, cfg)
    ms = []
    for spec in cfg.milestones:
        raw = t0 + dt.timedelta(weeks=spec.weeks, days=spec.days)
        d = raw if spec.key == "decision" else previous_business_day(raw, cfg.holidays)
        ms.append(Milestone(spec.key, spec.name, d, spec.owner, spec.package))
    return CycleSchedule(cycle, t0, ms)


def cycles_from(cfg: CadenceConfig, count: int = 4) -> List[CycleSchedule]:
    c = CycleId.parse(cfg.first_cycle)
    out = []
    for _ in range(count):
        out.append(build_schedule(c, cfg))
        c = c.next()
    return out


def active_cycle(today: dt.date, cfg: CadenceConfig) -> CycleSchedule:
    """The cycle in progress on `today`, else the next one to start."""
    c = CycleId.parse(cfg.first_cycle)
    for _ in range(200):
        s = build_schedule(c, cfg)
        if today <= s.publish:
            return s
        c = c.next()
    raise RuntimeError("No upcoming cycle found")
