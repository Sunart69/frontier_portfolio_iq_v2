"""Cadence and owner configuration (cadence/cadence.yaml, cadence/owners.yaml)."""
from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import yaml

PACKAGE_KEYS = ["strategy", "budget", "capacity", "initiatives", "enterprise"]
WEEKDAYS = {"MON": 0, "TUE": 1, "WED": 2, "THU": 3, "FRI": 4, "SAT": 5, "SUN": 6}


@dataclass
class Person:
    name: str
    github: str = ""
    email: str = ""

    @property
    def mention(self) -> str:
        return f"@{self.github}" if self.github else self.name

    @classmethod
    def from_dict(cls, d) -> "Person":
        d = d or {}
        return cls(str(d.get("name") or "[unassigned]"), str(d.get("github") or "").lstrip("@"),
                   str(d.get("email") or ""))


@dataclass
class MilestoneSpec:
    key: str
    name: str
    weeks: int
    days: int = 0
    owner: str = ""
    package: Optional[str] = None


@dataclass
class ReminderPolicy:
    before_due_days: List[int] = field(default_factory=lambda: [7, 2, 0])
    escalate_after_business_days: List[int] = field(default_factory=lambda: [2, 5])
    email: bool = False


@dataclass
class CadenceConfig:
    first_cycle: str
    timezone: str
    weeks_before_half_start: int
    meeting_weekday: int
    cycle_overrides: Dict[str, Dict]
    milestones: List[MilestoneSpec]
    reminders: ReminderPolicy
    require_all_inputs: bool
    carry_forward_missing: bool
    cycles_dir: str
    baseline_intake: str
    holidays: set
    # v2 decision intelligence in the pre-read (frontier, Monte Carlo, recommendation, CEO deck)
    decision_enabled: bool = True
    decision_config: Optional[str] = "config/decision.yaml"
    decision_iterations: int = 10000
    decision_quick: bool = False
    publish_iterations: int = 5000

    def package_due_key(self, package: str) -> str:
        for m in self.milestones:
            if m.package == package:
                return m.key
        raise KeyError(f"No due-date milestone for package '{package}'")


@dataclass
class LobOwners:
    lob_id: str
    name: str
    executive_sponsor: Person
    owners: Dict[str, Person]


@dataclass
class Owners:
    portfolio_lead: Person
    escalation: Person
    leadership_team: Person
    lobs: Dict[str, LobOwners]
    enterprise_owner: Person

    def owner_for(self, package: str, lob: Optional[str]) -> Person:
        if package == "enterprise":
            return self.enterprise_owner
        return self.lobs[lob].owners.get(package) or self.portfolio_lead

    def role(self, role: str) -> Person:
        if role == "lob_sponsors":
            names = ", ".join(l.executive_sponsor.name for l in self.lobs.values())
            return Person(f"LOB executive sponsors ({names})")
        return {"portfolio_lead": self.portfolio_lead, "escalation": self.escalation,
                "leadership_team": self.leadership_team}.get(role, Person(role or "[unassigned]"))


def _date(v) -> dt.date:
    return v if isinstance(v, dt.date) else dt.date.fromisoformat(str(v))


def load_cadence(path: str = "cadence/cadence.yaml") -> CadenceConfig:
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    dm = raw.get("decision_meeting") or {}
    rem = raw.get("reminders") or {}
    run = raw.get("run") or {}
    paths = raw.get("paths") or {}
    ms = [MilestoneSpec(str(m["key"]), str(m["name"]), int(m.get("weeks", 0)), int(m.get("days", 0)),
                        str(m.get("owner") or ""), m.get("package")) for m in raw.get("milestones") or []]
    keys = [m.key for m in ms]
    for required in ("kickoff", "solver_run", "decision", "publish"):
        if required not in keys:
            raise ValueError(f"cadence.yaml: milestone '{required}' is required")
    for pkg in PACKAGE_KEYS:
        if not any(m.package == pkg for m in ms):
            raise ValueError(f"cadence.yaml: no due-date milestone for package '{pkg}'")
    overrides = {str(k): {kk: (_date(vv) if kk.endswith("date") else vv) for kk, vv in (v or {}).items()}
                 for k, v in (raw.get("cycle_overrides") or {}).items()}
    return CadenceConfig(
        first_cycle=str(raw.get("first_cycle", "2027-H2")),
        timezone=str(raw.get("timezone", "UTC")),
        weeks_before_half_start=int(dm.get("weeks_before_half_start", 3)),
        meeting_weekday=WEEKDAYS[str(dm.get("weekday", "TUE")).upper()[:3]],
        cycle_overrides=overrides,
        milestones=ms,
        reminders=ReminderPolicy([int(x) for x in rem.get("before_due_days", [7, 2, 0])],
                                 [int(x) for x in rem.get("escalate_after_business_days", [2, 5])],
                                 bool(rem.get("email", False))),
        require_all_inputs=bool(run.get("require_all_inputs", True)),
        carry_forward_missing=bool(run.get("carry_forward_missing", False)),
        cycles_dir=str(paths.get("cycles_dir", "cycles")),
        baseline_intake=str(paths.get("baseline_intake", "cadence/baseline_intake.xlsx")),
        holidays={_date(h) for h in raw.get("holidays") or []},
        decision_enabled=bool((raw.get("decision_intelligence") or {}).get("enabled", True)),
        decision_config=(raw.get("decision_intelligence") or {}).get("config", "config/decision.yaml"),
        decision_iterations=int((raw.get("decision_intelligence") or {}).get("iterations", 10000)),
        decision_quick=bool((raw.get("decision_intelligence") or {}).get("quick", False)),
        publish_iterations=int((raw.get("decision_intelligence") or {}).get("publish_iterations", 5000)),
    )


def load_owners(path: str = "cadence/owners.yaml") -> Owners:
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    lobs = {}
    for lob_id, v in (raw.get("lobs") or {}).items():
        v = v or {}
        lobs[str(lob_id)] = LobOwners(str(lob_id), str(v.get("name") or lob_id),
                                      Person.from_dict(v.get("executive_sponsor")),
                                      {k: Person.from_dict(p) for k, p in (v.get("owners") or {}).items()})
    pl = Person.from_dict(raw.get("portfolio_lead"))
    ent_role = ((raw.get("enterprise") or {}).get("owner") or "portfolio_lead")
    ent = pl if ent_role == "portfolio_lead" else Person.from_dict(raw.get(ent_role))
    return Owners(pl, Person.from_dict(raw.get("escalation")), Person.from_dict(raw.get("leadership_team")),
                  lobs, ent)


def repo_paths(root: str):
    return os.path.join(root, "cadence", "cadence.yaml"), os.path.join(root, "cadence", "owners.yaml")
