"""Excel intake: round trip, month parsing, and identifier / funding consistency validation."""
import pytest
from openpyxl import load_workbook

from frontier_portfolio_iq.domain import Settings
from frontier_portfolio_iq.intake import SHEETS, IntakeError, parse_month, read_workbook, validate, write_workbook
from frontier_portfolio_iq.sample_data import build_sample

REQUIRED_NEW_SHEETS = ["LOB Registry", "LOB Budget Allocation", "Capability Pool Ownership",
                       "Capability Eligibility", "Capacity Transfer Rules", "LOB Strategic Targets",
                       "Cross-LOB Benefit Allocation"]


@pytest.fixture
def sample_path(tmp_path):
    return write_workbook(build_sample(), str(tmp_path / "intake.xlsx"))


def test_workbook_contains_required_lob_sheets(sample_path):
    names = load_workbook(sample_path).sheetnames
    for s in REQUIRED_NEW_SHEETS:
        assert s in names


def test_round_trip_preserves_model(sample_path):
    a, b = build_sample(), read_workbook(sample_path)
    assert set(a.projects) == set(b.projects) and set(a.pools) == set(b.pools)
    assert len(a.budgets) == len(b.budgets) and len(a.transfers) == len(b.transfers)
    for pid in a.projects:
        assert a.projects[pid].demand == b.projects[pid].demand
        assert a.projects[pid].shares() == b.projects[pid].shares()
    for pid in a.pools:
        assert a.pools[pid].capacity_type == b.pools[pid].capacity_type
        assert [a.pools[pid].available_hours(t) for t in a.months] == [b.pools[pid].available_hours(t) for t in b.months]
    assert validate(b).ok


def test_month_names_parse_relative_to_horizon():
    s = Settings(first_month_label="2027-01")
    assert parse_month("April", s) == 4 and parse_month("2027-06", s) == 6 and parse_month(3, s) == 3
    s2 = Settings(first_month_label="2026-10")
    assert parse_month("Jan", s2) == 4 and parse_month("2027-01", s2) == 4


def _edit(path, sheet, row, col_name, value):
    wb = load_workbook(path)
    ws = wb[sheet]
    col = SHEETS[sheet].index(col_name) + 1
    ws.cell(row=row, column=col, value=value)
    wb.save(path)


@pytest.mark.parametrize("sheet,col,value,needle", [
    ("Capability Pool Ownership", "Owning LOB", "LOB-Z", "unknown LOB ID 'LOB-Z'"),
    ("Capability Eligibility", "Pool ID", "NO-POOL", "unknown pool 'NO-POOL'"),
    ("Capacity Transfer Rules", "Source Pool", "NO-POOL", "unknown source pool"),
    ("Capacity Transfer Rules", "Source LOB", "LOB-B", "does not own pool"),
    ("Cross-LOB Benefit Allocation", "Project ID", "ZZ9", "unknown project 'ZZ9'"),
    ("LOB Budget Allocation", "LOB ID", "LOB-Q", "unknown LOB ID 'LOB-Q'"),
    ("Project Funding Shares", "Share", 0.5, "funding shares sum"),
])
def test_validation_catches_inconsistent_identifiers(sample_path, sheet, col, value, needle):
    _edit(sample_path, sheet, 2, col, value)
    with pytest.raises(IntakeError) as exc:
        read_workbook(sample_path)
    assert any(needle in e for e in exc.value.report.errors), exc.value.report.errors


def test_validation_catches_missing_funding_relationship():
    d = build_sample()
    d.budgets = [b for b in d.budgets if not (b.lob_id == "LOB-C" and b.funding_source == "Capex")]
    errs = validate(d).errors
    assert any("LOB 'LOB-C' has no 'Capex' budget lines" in e for e in errs)


def test_validation_flags_pool_end_before_start():
    d = build_sample()
    d.pools["RF-SWE-C-TMP"].end_month = 1
    assert any("end month" in e for e in validate(d).errors)
