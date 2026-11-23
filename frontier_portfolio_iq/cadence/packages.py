"""Input packages: the intake workbook split by owner, validated per owner, and merged back.

Each owner receives a pre-filled template holding only their sheets and only their LOB's rows,
and returns it as cycles/<cycle>/inputs/<package>__<LOB>.xlsx. The merge rebuilds the full intake
workbook that the optimizer reads.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from ..intake import SHEETS, read_workbook, validate

ENTERPRISE = "ALL"   # file key for the enterprise-level package


@dataclass(frozen=True)
class PackageSpec:
    key: str
    title: str
    sheets: Tuple[str, ...]
    description: str


PACKAGES: Dict[str, PackageSpec] = {
    "strategy": PackageSpec("strategy", "LOB strategy & outcomes",
                            ("LOB Registry", "LOB Strategic Targets", "Cross-LOB Benefit Allocation"),
                            "Your LOB's sponsor and owners, KPI targets and minimum outcomes, and the benefit "
                            "your LOB expects from each initiative, including cross-LOB platforms"),
    "budget": PackageSpec("budget", "LOB budget allocation", ("LOB Budget Allocation",),
                          "Available budget by month and funding source, carry-forward rule and restrictions"),
    "capacity": PackageSpec("capacity", "Capacity pools & transfer rules",
                            ("Capability Pool Ownership", "Capability Eligibility", "Capacity Transfer Rules"),
                            "Teams your LOB owns (FTE, start and end months, ring-fenced or shared), who may use "
                            "them, and transfers you approve, decline or propose"),
    "initiatives": PackageSpec("initiatives", "Initiative proposals",
                               ("Projects", "Project Funding Shares", "Project Demand", "Project Costs"),
                               "Every initiative your LOB sponsors: value, priority, mandatory and compliance "
                               "flags, monthly hours by skill, monthly cost, deadlines and dependencies"),
    "enterprise": PackageSpec("enterprise", "Enterprise settings", ("Settings", "Capability Substitution"),
                              "Planning horizon, governance switches and which skills can substitute for others"),
}

Row = Dict[str, object]


def _read_sheets(path: str) -> Dict[str, Tuple[List[str], List[Row]]]:
    wb = load_workbook(path, data_only=True)
    out = {}
    for ws in wb.worksheets:
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            out[ws.title] = ([], [])
            continue
        header = [str(h).strip() if h is not None else "" for h in rows[0]]
        body = []
        for r in rows[1:]:
            if r is None or all(c is None or (isinstance(c, str) and not c.strip()) for c in r):
                continue
            body.append({header[i]: r[i] for i in range(min(len(header), len(r)))})
        out[ws.title] = (header, body)
    return out


def _s(v) -> str:
    return "" if v is None else str(v).strip()


def _scope_filters(sheets) -> Dict[str, Callable[[Row, str], bool]]:
    """Row → belongs to LOB? Pools/projects referenced by id are resolved from the same workbook."""
    pools_of: Dict[str, str] = {}
    for r in sheets.get("Capability Pool Ownership", ([], []))[1]:
        pools_of[_s(r.get("Pool ID"))] = _s(r.get("Owning LOB"))
    projects_of: Dict[str, str] = {}
    for r in sheets.get("Projects", ([], []))[1]:
        projects_of[_s(r.get("Project ID"))] = _s(r.get("Primary LOB"))
    return {
        "LOB Registry": lambda r, l: _s(r.get("LOB ID")) == l,
        "LOB Strategic Targets": lambda r, l: _s(r.get("LOB ID")) == l,
        "Cross-LOB Benefit Allocation": lambda r, l: _s(r.get("Benefiting LOB")) == l,
        "LOB Budget Allocation": lambda r, l: _s(r.get("LOB ID")) == l,
        "Capability Pool Ownership": lambda r, l: _s(r.get("Owning LOB")) == l,
        "Capability Eligibility": lambda r, l: pools_of.get(_s(r.get("Pool ID"))) == l,
        "Capacity Transfer Rules": lambda r, l: _s(r.get("Source LOB")) == l,
        "Projects": lambda r, l: _s(r.get("Primary LOB")) == l,
        "Project Funding Shares": lambda r, l: projects_of.get(_s(r.get("Project ID"))) == l,
        "Project Demand": lambda r, l: projects_of.get(_s(r.get("Project ID"))) == l,
        "Project Costs": lambda r, l: projects_of.get(_s(r.get("Project ID"))) == l,
    }


HEADER_FILL = PatternFill("solid", fgColor="1F3A5F")
HEADER_FONT = Font(color="FFFFFF", bold=True)


def _write(path: str, sheets: Dict[str, List[Row]], readme: Optional[List[Tuple[str, str]]] = None):
    wb = Workbook()
    wb.remove(wb.active)
    if readme:
        ws = wb.create_sheet("README")
        for k, v in readme:
            ws.append([k, v])
        ws.column_dimensions["A"].width = 26
        ws.column_dimensions["B"].width = 110
        for c in ws["A"]:
            c.font = Font(bold=True)
        for c in ws["B"]:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        header = SHEETS[name]
        ws.append(header)
        for r in rows:
            ws.append([r.get(h) for h in header])
        for c in ws[1]:
            c.fill, c.font = HEADER_FILL, HEADER_FONT
        for i, h in enumerate(header, start=1):
            ws.column_dimensions[get_column_letter(i)].width = max(12, min(34, len(h) + 4))
        ws.freeze_panes = "A2"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    wb.save(path)
    return path


def package_filename(package: str, lob: Optional[str]) -> str:
    return f"{package}__{lob or ENTERPRISE}.xlsx"


def parse_package_filename(name: str) -> Optional[Tuple[str, str]]:
    base = os.path.basename(name)
    if not base.endswith(".xlsx") or "__" not in base:
        return None
    pkg, lob = base[:-5].split("__", 1)
    return (pkg, lob) if pkg in PACKAGES else None


def split_package(master_path: str, package: str, lob: Optional[str]) -> Dict[str, List[Row]]:
    sheets = _read_sheets(master_path)
    filt = _scope_filters(sheets)
    out = {}
    for name in PACKAGES[package].sheets:
        rows = sheets.get(name, ([], []))[1]
        if package == "enterprise" or lob is None:
            out[name] = list(rows)
        else:
            out[name] = [r for r in rows if filt[name](r, lob)]
    return out


def write_template(master_path: str, out_dir: str, package: str, lob: Optional[str],
                   readme: List[Tuple[str, str]]) -> str:
    rows = split_package(master_path, package, lob)
    return _write(os.path.join(out_dir, package_filename(package, lob)), rows, readme)


@dataclass
class PackageCheck:
    package: str
    lob: str
    path: str
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


NUMERIC = {"LOB Budget Allocation": ["Available Budget ($K)"],
           "Capability Pool Ownership": ["Available FTE"],
           "Projects": ["Business Value ($K)", "Duration (months)"],
           "Project Demand": ["Hours"], "Project Costs": ["Cost ($K)"],
           "Cross-LOB Benefit Allocation": ["Attributed Benefit ($K)"]}
REQUIRED = {"LOB Registry": ["LOB ID", "LOB Name"], "LOB Budget Allocation": ["LOB ID", "Month", "Funding Source"],
            "Capability Pool Ownership": ["Pool ID", "Capability Area", "Owning LOB", "Capacity Type"],
            "Projects": ["Project ID", "Project Name", "Primary LOB", "Funding Source"],
            "Project Demand": ["Project ID", "Capability", "Relative Month"],
            "Project Costs": ["Project ID", "Relative Month"], "Settings": ["Key"]}


def check_package(path: str, package: Optional[str] = None, lob: Optional[str] = None) -> PackageCheck:
    """Validate one owner's submission on its own: sheets, headers, required cells, numbers, LOB scope."""
    parsed = parse_package_filename(path)
    if package is None or lob is None:
        if not parsed:
            return PackageCheck(package or "?", lob or "?", path,
                                [f"File name must be <package>__<LOB>.xlsx, e.g. budget__LOB-A.xlsx"])
        package, lob = parsed
    chk = PackageCheck(package, lob, path)
    if package not in PACKAGES:
        chk.errors.append(f"Unknown package '{package}'")
        return chk
    try:
        sheets = _read_sheets(path)
    except Exception as e:  # noqa: BLE001 - surface any unreadable workbook to the owner
        chk.errors.append(f"Workbook cannot be opened: {e}")
        return chk
    filt = _scope_filters(sheets)
    for name in PACKAGES[package].sheets:
        if name not in sheets:
            chk.errors.append(f"Missing sheet '{name}'")
            continue
        header, rows = sheets[name]
        missing = [h for h in SHEETS[name] if h not in header]
        if missing:
            chk.errors.append(f"Sheet '{name}': missing columns {missing}")
            continue
        for i, r in enumerate(rows, start=2):
            for col in REQUIRED.get(name, []):
                if _s(r.get(col)) == "":
                    chk.errors.append(f"Sheet '{name}' row {i}: '{col}' is empty")
            for col in NUMERIC.get(name, []):
                v = r.get(col)
                if v is None or _s(v) == "":
                    continue
                try:
                    if float(v) < 0:
                        chk.errors.append(f"Sheet '{name}' row {i}: '{col}' is negative")
                except (TypeError, ValueError):
                    chk.errors.append(f"Sheet '{name}' row {i}: '{col}' is not a number ({v!r})")
            if package != "enterprise" and name in filt and not filt[name](r, lob):
                chk.errors.append(f"Sheet '{name}' row {i}: belongs to another LOB or references a pool/"
                                  f"project not owned by {lob}")
        if package == "budget" and not rows:
            chk.errors.append("No budget lines submitted")
    if package == "initiatives":
        ids = {_s(r.get("Project ID")) for r in sheets.get("Projects", ([], []))[1]}
        for name in ("Project Demand", "Project Costs"):
            have = {_s(r.get("Project ID")) for r in sheets.get(name, ([], []))[1]}
            for pid in sorted(ids - have):
                chk.errors.append(f"Initiative {pid} has no rows in '{name}'")
    return chk


@dataclass
class MergeResult:
    master_path: str
    used: Dict[Tuple[str, str], str]
    carried_forward: List[Tuple[str, str]]
    errors: List[str]
    warnings: List[str]

    @property
    def ok(self) -> bool:
        return not self.errors


def merge_packages(submissions: Dict[Tuple[str, str], str], out_path: str,
                   fallback_master: Optional[str] = None,
                   expected: Optional[List[Tuple[str, str]]] = None) -> MergeResult:
    """Union every package's rows into one intake workbook; optionally carry forward missing packages
    from the previous cycle's master. Then run the full cross-package validation."""
    rows: Dict[str, List[Row]] = {name: [] for name in SHEETS}
    used, carried = {}, []
    expected = expected or list(submissions)
    for key in expected:
        pkg, lob = key
        path = submissions.get(key)
        if path:
            data = {n: r for n, (h, r) in _read_sheets(path).items() if n in PACKAGES[pkg].sheets}
            used[key] = path
        elif fallback_master:
            data = split_package(fallback_master, pkg, None if lob == ENTERPRISE else lob)
            carried.append(key)
        else:
            continue
        for name, rs in data.items():
            seen = {tuple(sorted((k, _s(v)) for k, v in r.items())) for r in rows[name]}
            for r in rs:
                sig = tuple(sorted((k, _s(v)) for k, v in r.items()))
                if sig not in seen:
                    rows[name].append(r)
                    seen.add(sig)
    _write(out_path, rows)
    errors, warnings = [], []
    try:
        data = read_workbook(out_path, validate_strict=False)
        rep = validate(data)
        errors, warnings = rep.errors, rep.warnings
    except Exception as e:  # noqa: BLE001
        errors = [f"Merged workbook could not be read: {e}"]
    return MergeResult(out_path, used, carried, errors, warnings)
