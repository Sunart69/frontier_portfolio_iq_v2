"""iCalendar (.ics) export of a cycle's workback schedule: import into Outlook or Google Calendar."""
from __future__ import annotations

import datetime as dt
from typing import Optional

from .config import Owners
from .packages import PACKAGES
from .schedule import CycleSchedule, ONE_DAY


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    out, cur = [], line
    while len(cur.encode()) > 74:
        cut = 74
        while len(cur[:cut].encode()) > 74:
            cut -= 1
        out.append(cur[:cut])
        cur = " " + cur[cut:]
    out.append(cur)
    return "\r\n".join(out)


def to_ics(schedule: CycleSchedule, owners: Optional[Owners] = None,
           stamp: Optional[dt.datetime] = None) -> str:
    stamp = stamp or dt.datetime(schedule.kickoff.year, 1, 1)
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Frontier Portfolio IQ//Cadence//EN",
             "CALSCALE:GREGORIAN", "METHOD:PUBLISH", _fold(f"X-WR-CALNAME:Portfolio {schedule.cycle} cadence")]
    for m in sorted(schedule.milestones, key=lambda m: (m.date, m.key)):
        desc = m.name
        if m.package:
            spec = PACKAGES[m.package]
            desc = f"{spec.title}: {spec.description}."
            if owners is not None:
                if m.package == "enterprise":
                    who = [owners.enterprise_owner.name]
                else:
                    who = [f"{l.name}: {l.owners.get(m.package).name}" for l in owners.lobs.values()
                           if l.owners.get(m.package)]
                desc += " Owners: " + "; ".join(who) + "."
        elif owners is not None and m.owner_role:
            desc += f". Owner: {owners.role(m.owner_role).name}."
        lines += ["BEGIN:VEVENT",
                  f"UID:fpiq-{schedule.cycle}-{m.key}@frontier-portfolio-iq",
                  f"DTSTAMP:{stamp:%Y%m%dT%H%M%SZ}",
                  f"DTSTART;VALUE=DATE:{m.date:%Y%m%d}",
                  f"DTEND;VALUE=DATE:{m.date + ONE_DAY:%Y%m%d}",
                  _fold(f"SUMMARY:{_esc(f'[Portfolio {schedule.cycle}] {m.name}')}"),
                  _fold(f"DESCRIPTION:{_esc(desc)}"),
                  "TRANSP:TRANSPARENT", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"
