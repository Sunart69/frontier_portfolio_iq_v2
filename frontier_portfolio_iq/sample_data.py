"""Synthetic demonstration portfolio: three business LOBs plus an Enterprise IT funding entity.

All figures are illustrative. Monetary values in $K, capacity in hours.
"""
from __future__ import annotations

from .domain import (
    ANY, LOB, ApprovalStatus, BenefitAllocation, BudgetLine, CapacityPool, CapacityType,
    EligibilityRule, PortfolioData, Project, Settings, StrategicTarget, Substitution, TransferRule,
)

SWE, INT, ARC, AI, DATA = ("Software Engineering", "Integration Engineering",
                           "Solution Architecture", "AI Engineering", "Data Engineering")


def _project(pid, name, lob, secondary, okr, ptype, source, value, dur, demand, cost_pm, *,
             earliest=1, deadline=None, deps=(), shares=None, mandatory=False, compliance=False,
             critical=False, platform=False, risk_red=0.0, maturity=0.0, conf=0.8, risk=0.2,
             priority=2, ent=0.3, initiative=""):
    return Project(
        project_id=pid, name=name, primary_lob=lob, secondary_lobs=list(secondary), okr=okr,
        initiative=initiative or okr, project_type=ptype, funding_source=source, value=value,
        duration=dur, earliest_start=earliest, deadline=deadline, depends_on=list(deps),
        funding_shares=dict(shares or {}), mandatory=mandatory, compliance=compliance,
        critical=critical, shared_platform=platform, risk_reduction=risk_red,
        maturity_gain=maturity, delivery_confidence=conf, execution_risk=risk,
        lob_priority=priority, enterprise_contribution=ent,
        executive_sponsor=f"{lob} Sponsor", outcome_owner=f"{lob} Outcome Owner",
        funding_owner=f"{lob} Finance",
        demand={(c, k): h for c, h in demand.items() for k in range(1, dur + 1)},
        cost={k: cost_pm for k in range(1, dur + 1)},
    )


def build_sample() -> PortfolioData:
    settings = Settings(horizon=12, first_month_label="2027-01", as_of_month=1,
                        schedule_discount=0.15, protect_critical=True,
                        min_investment_share={"LOB-A": 0.18, "LOB-B": 0.18, "LOB-C": 0.18})

    lobs = {
        "LOB-A": LOB("LOB-A", "Retail Banking", "R. Alvarez (EVP Retail)", "M. Chen", 1, "Retail CFO",
                     risk_tolerance=0.35, maturity_target=6),
        "LOB-B": LOB("LOB-B", "Commercial Banking", "S. Okafor (EVP Commercial)", "J. Patel", 1,
                     "Commercial CFO", risk_tolerance=0.35, maturity_target=6),
        "LOB-C": LOB("LOB-C", "Wealth Management", "D. Laurent (EVP Wealth)", "A. Novak", 2,
                     "Wealth CFO", risk_tolerance=0.40, maturity_target=4),
        "ENT": LOB("ENT", "Enterprise IT (Shared Platforms)", "CIO", "Chief Architect", 1, "IT Finance",
                   is_enterprise=True),
    }

    projects = [
        # ---- LOB A: Retail Banking
        _project("A01", "Digital Onboarding Rebuild", "LOB-A", [], "A-KR1 Digital acquisition",
                 "Discretionary", "Opex", 2600, 6, {SWE: 300, INT: 180, ARC: 40}, 165,
                 maturity=3, risk_red=1, conf=0.8, risk=0.25, priority=1),
        _project("A02", "Payments Hub Integration", "LOB-A", ["LOB-B"], "A-KR2 Payments revenue",
                 "Discretionary", "Opex", 2300, 5, {INT: 380, SWE: 120, ARC: 40}, 150,
                 earliest=4, maturity=2, risk_red=2, conf=0.75, risk=0.3, priority=2),
        _project("A03", "KYC Regulatory Remediation", "LOB-A", [], "A-KR3 Regulatory compliance",
                 "Compliance", "Regulatory", 900, 4, {SWE: 220, INT: 100, DATA: 80}, 60,
                 deadline=9, mandatory=True, compliance=True, risk_red=8, maturity=1, conf=0.9,
                 risk=0.15, priority=1),
        _project("A04", "Branch Workflow Automation", "LOB-A", [], "A-KR1 Digital acquisition",
                 "Discretionary", "Opex", 1100, 4, {SWE: 200, AI: 100}, 90,
                 maturity=2, conf=0.85, risk=0.2, priority=3),
        _project("A05", "Card Fraud Scoring", "LOB-A", [], "A-KR4 Fraud loss reduction",
                 "Discretionary", "Opex", 1900, 5, {AI: 220, DATA: 140, SWE: 80}, 120,
                 deps=["E02"], risk_red=6, maturity=3, conf=0.7, risk=0.35, priority=2),
        # ---- LOB B: Commercial Banking
        _project("B01", "Commercial Loan Origination", "LOB-B", [], "B-KR1 Loan cycle time",
                 "Discretionary", "Opex", 3300, 7, {SWE: 420, INT: 150, ARC: 50}, 195,
                 maturity=3, risk_red=1, conf=0.75, risk=0.3, priority=1),
        _project("B02", "Treasury Portal Refresh", "LOB-B", [], "B-KR2 Treasury fee growth",
                 "Discretionary", "Opex", 1500, 4, {SWE: 320, ARC: 30}, 115,
                 maturity=1, conf=0.85, risk=0.15, priority=3),
        _project("B03", "Basel Reporting Uplift", "LOB-B", [], "B-KR3 Regulatory compliance",
                 "Compliance", "Regulatory", 800, 5, {DATA: 220, SWE: 140}, 55,
                 deadline=10, mandatory=True, compliance=True, risk_red=8, maturity=2, conf=0.9,
                 risk=0.15, priority=1),
        _project("B04", "Relationship Manager Copilot", "LOB-B", [], "B-KR1 Loan cycle time",
                 "Discretionary", "Opex", 2200, 5, {AI: 300, SWE: 120, INT: 60}, 140,
                 deps=["E01"], maturity=4, conf=0.65, risk=0.4, priority=2),
        _project("B05", "Trade Finance Digitization", "LOB-B", ["LOB-A"], "B-KR2 Treasury fee growth",
                 "Discretionary", "Opex", 1700, 6, {INT: 260, SWE: 220}, 125,
                 maturity=2, risk_red=1, conf=0.75, risk=0.25, priority=2),
        # ---- LOB C: Wealth Management
        _project("C01", "Advisor Desktop", "LOB-C", [], "C-KR1 Advisor productivity",
                 "Discretionary", "Opex", 2000, 6, {SWE: 300, INT: 100}, 125,
                 maturity=2, conf=0.8, risk=0.2, priority=1),
        _project("C02", "Portfolio Rebalancing AI", "LOB-C", [], "C-KR2 AUM growth",
                 "Discretionary", "Opex", 2500, 5, {AI: 280, DATA: 120}, 135,
                 deps=["E01"], maturity=4, conf=0.65, risk=0.4, priority=1),
        _project("C03", "Suitability Compliance Controls", "LOB-C", [], "C-KR3 Regulatory compliance",
                 "Compliance", "Regulatory", 700, 3, {SWE: 160, DATA: 60}, 45,
                 compliance=True, risk_red=7, maturity=1, conf=0.9, risk=0.1, priority=2),
        _project("C04", "Client Reporting Modernization", "LOB-C", [], "C-KR1 Advisor productivity",
                 "Discretionary", "Opex", 1300, 4, {DATA: 200, SWE: 150}, 95,
                 maturity=2, conf=0.85, risk=0.15, priority=3),
        # ---- Enterprise / cross-LOB initiatives
        _project("E01", "Enterprise AI Platform", "ENT", ["LOB-A", "LOB-B", "LOB-C"],
                 "ENT-KR1 AI enablement", "Platform", "Capex", 4200, 5,
                 {AI: 240, DATA: 180, ARC: 80, SWE: 160}, 250, platform=True,
                 shares={"ENT": 0.4, "LOB-A": 0.2, "LOB-B": 0.2, "LOB-C": 0.2},
                 maturity=6, risk_red=2, conf=0.7, risk=0.35, priority=1, ent=0.9),
        _project("E02", "Shared Data Platform", "ENT", ["LOB-A", "LOB-B", "LOB-C"],
                 "ENT-KR2 Data reuse", "Platform", "Capex", 3000, 6,
                 {DATA: 300, SWE: 140, ARC: 60}, 190, platform=True,
                 maturity=5, risk_red=2, conf=0.75, risk=0.3, priority=1, ent=0.9),
        _project("E03", "Enterprise Integration Modernization", "ENT", ["LOB-A", "LOB-B"],
                 "ENT-KR3 Integration cost", "Platform", "Capex", 2500, 6,
                 {INT: 320, ARC: 70}, 165, platform=True,
                 maturity=4, risk_red=3, conf=0.75, risk=0.3, priority=2, ent=0.8),
        _project("E04", "Enterprise Compliance Platform", "ENT", ["LOB-A", "LOB-B", "LOB-C"],
                 "ENT-KR4 Compliance efficiency", "Compliance", "Regulatory", 1600, 5,
                 {SWE: 200, DATA: 120}, 90, deadline=11, platform=True, compliance=True,
                 critical=True, shares={"ENT": 0.5, "LOB-A": 0.2, "LOB-B": 0.15, "LOB-C": 0.15},
                 risk_red=9, maturity=3, conf=0.85, risk=0.2, priority=1, ent=0.8),
        _project("E05", "Common Workflow Automation", "ENT", ["LOB-A", "LOB-C"],
                 "ENT-KR1 AI enablement", "Platform", "Capex", 1400, 4,
                 {SWE: 200, AI: 80}, 100, platform=True,
                 maturity=3, conf=0.8, risk=0.2, priority=3, ent=0.6),
    ]
    projects = {p.project_id: p for p in projects}

    pools = [
        CapacityPool("RF-INT-A", INT, "LOB-A", CapacityType.RING_FENCED, 4, None, 5,
                     cost_rate=115, release_eligible=True, release_efficiency=0.9, release_cost_rate=15),
        CapacityPool("RF-SWE-A", SWE, "LOB-A", CapacityType.RING_FENCED, 1, None, 4, cost_rate=105,
                     allowed_funding_sources=["Opex", "Regulatory"],
                     funding_restrictions="Funded by Retail opex; not usable on capital programs"),
        CapacityPool("RF-SWE-B", SWE, "LOB-B", CapacityType.RING_FENCED, 1, None, 6, cost_rate=105,
                     release_eligible=True, release_efficiency=0.9, release_cost_rate=15),
        CapacityPool("RF-DATA-C", DATA, "LOB-C", CapacityType.RING_FENCED, 1, None, 3, cost_rate=110,
                     release_eligible=True, release_efficiency=0.85, release_cost_rate=20),
        CapacityPool("RF-SWE-C-TMP", SWE, "LOB-C", CapacityType.RING_FENCED, 3, 8, 3, cost_rate=140,
                     funding_restrictions="Temporary contractor cohort, Mar-Aug"),
        CapacityPool("CT-AI-C", AI, "LOB-C", CapacityType.CONDITIONAL, 6, None, 4, cost_rate=150),
        CapacityPool("SH-ARC-ENT", ARC, "ENT", CapacityType.SHARED, 1, None, 2.5, cost_rate=135),
        CapacityPool("SH-SWE-ENT", SWE, "ENT", CapacityType.SHARED, 1, None, 4, cost_rate=105),
        CapacityPool("SH-DATA-ENT", DATA, "ENT", CapacityType.SHARED, 1, None, 4, cost_rate=110),
        CapacityPool("SH-AI-ENT", AI, "ENT", CapacityType.SHARED, 1, None, 1.5, cost_rate=150),
        CapacityPool("SH-INT-ENT", INT, "ENT", CapacityType.SHARED, 1, None, 1, cost_rate=115),
    ]
    pools = {p.pool_id: p for p in pools}

    eligibility = [
        EligibilityRule("CT-AI-C", "LOB-C", ANY, "", "Owner LOB"),
        EligibilityRule("CT-AI-C", ANY, ANY, "E01", "Wealth authorizes use on Enterprise AI Platform"),
        EligibilityRule("RF-DATA-C", ANY, ANY, "E02", "Wealth authorizes use on Shared Data Platform"),
        EligibilityRule("RF-INT-A", "LOB-A", ANY, "", "Retail only (domain expertise: payments rails)"),
    ]

    transfers = [
        TransferRule("TR-01", "CT-AI-C", "LOB-C", "LOB-A", max_hours_per_month=150, max_pct=0.4,
                     efficiency=0.85, cost_per_hour=20, approval_required=True,
                     approval_status=ApprovalStatus.APPROVED, effective_start=6, effective_end=12,
                     min_retained_hours=200, note="Approved by Wealth EVP, Q4 planning"),
        TransferRule("TR-02", "CT-AI-C", "LOB-C", "LOB-B", max_hours_per_month=240, max_pct=0.5,
                     efficiency=0.85, cost_per_hour=20, approval_required=True,
                     approval_status=ApprovalStatus.PENDING, effective_start=7, effective_end=12,
                     min_retained_hours=200, note="Requested by Commercial for RM Copilot"),
        TransferRule("TR-03", "RF-SWE-B", "LOB-B", "LOB-C", max_hours_per_month=200, max_pct=0.3,
                     efficiency=0.9, cost_per_hour=10, approval_required=True,
                     approval_status=ApprovalStatus.PENDING, effective_start=4, effective_end=9,
                     min_retained_hours=300, note="Temporary loan, returns to Commercial in Oct"),
        TransferRule("TR-04", "RF-INT-A", "LOB-A", "ENT", max_hours_per_month=120, max_pct=0.25,
                     efficiency=0.9, cost_per_hour=15, approval_required=True,
                     approval_status=ApprovalStatus.REJECTED, effective_start=6, effective_end=12,
                     note="Declined: payments rails expertise retained"),
    ]

    budgets = []
    monthly = {
        ("LOB-A", "Opex"): 200, ("LOB-A", "Regulatory"): 45, ("LOB-A", "Capex"): 45,
        ("LOB-B", "Opex"): 230, ("LOB-B", "Regulatory"): 45, ("LOB-B", "Capex"): 45,
        ("LOB-C", "Opex"): 170, ("LOB-C", "Regulatory"): 25, ("LOB-C", "Capex"): 45,
        ("ENT", "Capex"): 260, ("ENT", "Regulatory"): 35,
    }
    for (lob, src), amt in monthly.items():
        for t in range(1, settings.horizon + 1):
            budgets.append(BudgetLine(lob, t, src, amt, carry_forward=True,
                                      allowed_project_types=["Compliance"] if src == "Regulatory" else []))

    targets = [
        StrategicTarget("LOB-A", "A-KR1 Digital acquisition", 3000, min_outcome=2000, min_investment=900),
        StrategicTarget("LOB-A", "A-KR4 Fraud loss reduction", 1900, min_outcome=None),
        StrategicTarget("LOB-B", "B-KR1 Loan cycle time", 4000, min_outcome=2500, min_investment=900),
        StrategicTarget("LOB-C", "C-KR1 Advisor productivity", 2500, min_outcome=1300, min_investment=700),
        StrategicTarget("LOB-C", "C-KR2 AUM growth", 3000, min_outcome=None),
    ]

    benefits = []
    cross = {
        "E01": [("LOB-A", "A-KR1 Digital acquisition", 900), ("LOB-B", "B-KR1 Loan cycle time", 1100),
                ("LOB-C", "C-KR2 AUM growth", 1200)],
        "E02": [("LOB-A", "A-KR4 Fraud loss reduction", 700), ("LOB-B", "B-KR1 Loan cycle time", 600),
                ("LOB-C", "C-KR1 Advisor productivity", 700)],
        "E03": [("LOB-A", "A-KR2 Payments revenue", 900), ("LOB-B", "B-KR2 Treasury fee growth", 900)],
        "E04": [("LOB-A", "A-KR3 Regulatory compliance", 400), ("LOB-B", "B-KR3 Regulatory compliance", 400),
                ("LOB-C", "C-KR3 Regulatory compliance", 400)],
        "E05": [("LOB-A", "A-KR1 Digital acquisition", 500), ("LOB-C", "C-KR1 Advisor productivity", 500)],
        "A02": [("LOB-A", "A-KR2 Payments revenue", 1800), ("LOB-B", "B-KR2 Treasury fee growth", 500)],
        "B05": [("LOB-B", "B-KR2 Treasury fee growth", 1300), ("LOB-A", "A-KR2 Payments revenue", 400)],
    }
    for pid, p in projects.items():
        if pid in cross:
            for lob, kpi, amt in cross[pid]:
                benefits.append(BenefitAllocation(pid, lob, kpi, amt, f"{lob} Outcome Owner"))
        else:
            benefits.append(BenefitAllocation(pid, p.primary_lob, p.okr, p.value, f"{p.primary_lob} Outcome Owner"))

    substitutions = [
        Substitution(SWE, INT, 0.6),
        Substitution(DATA, AI, 0.5),
        Substitution(AI, DATA, 0.8),
    ]

    return PortfolioData(lobs, projects, pools, eligibility, transfers, budgets, targets, benefits,
                         substitutions, settings)
