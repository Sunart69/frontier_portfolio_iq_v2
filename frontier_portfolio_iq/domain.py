"""Domain model for multi-LOB portfolio optimization.

Hierarchy: Enterprise -> IT Portfolio -> LOB -> Business Outcome / OKR -> Initiative -> Project.

Months are 1-based integers over the planning horizon (1..H). Monetary values are in $K.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

ANY = "*"
ENTERPRISE_UNALLOCATED = "ENTERPRISE (unallocated)"


class CapacityType(str, Enum):
    RING_FENCED = "Ring-fenced"
    SHARED = "Shared"
    CONDITIONAL = "Conditionally Transferable"

    @classmethod
    def parse(cls, value: str) -> "CapacityType":
        v = str(value).strip().lower()
        if v.startswith("ring"):
            return cls.RING_FENCED
        if v.startswith("shared"):
            return cls.SHARED
        if v.startswith("cond"):
            return cls.CONDITIONAL
        raise ValueError(f"Unknown capacity type: {value!r}")


class ApprovalStatus(str, Enum):
    APPROVED = "Approved"
    PENDING = "Pending"
    REJECTED = "Rejected"

    @classmethod
    def parse(cls, value: str) -> "ApprovalStatus":
        v = str(value).strip().lower()
        for member in cls:
            if member.value.lower() == v:
                return member
        raise ValueError(f"Unknown approval status: {value!r}")


class PortfolioTier(str, Enum):
    COMMITTED = "Committed"
    CONDITIONAL = "Conditional"
    UNCONSTRAINED = "Unconstrained Opportunity"


@dataclass
class LOB:
    lob_id: str
    name: str
    executive_sponsor: str = ""
    outcome_owner: str = ""
    strategic_priority: int = 1
    budget_owner: str = ""
    is_enterprise: bool = False          # enterprise / shared-IT funding entity
    risk_tolerance: Optional[float] = None   # max avg execution risk of selected projects (0-1)
    maturity_target: Optional[float] = None  # min capability maturity gain when targets enforced


@dataclass
class Project:
    project_id: str
    name: str
    primary_lob: str
    initiative: str = ""
    okr: str = ""
    secondary_lobs: List[str] = field(default_factory=list)
    executive_sponsor: str = ""
    outcome_owner: str = ""
    funding_owner: str = ""
    funding_source: str = "Opex"
    project_type: str = "Discretionary"
    lob_priority: int = 3
    enterprise_contribution: float = 0.0     # 0-1 strategic contribution score
    shared_platform: bool = False
    mandatory: bool = False
    compliance: bool = False
    critical: bool = False
    value: float = 0.0                       # total enterprise benefit ($K), counted once
    risk_reduction: float = 0.0              # score
    maturity_gain: float = 0.0               # score
    delivery_confidence: float = 0.8         # 0-1
    execution_risk: float = 0.2              # 0-1
    duration: int = 1
    earliest_start: int = 1
    deadline: Optional[int] = None           # month by which project must finish
    funding_shares: Dict[str, float] = field(default_factory=dict)
    demand: Dict[Tuple[str, int], float] = field(default_factory=dict)  # (capability, rel month) -> hours
    cost: Dict[int, float] = field(default_factory=dict)                # rel month -> $K
    depends_on: List[str] = field(default_factory=list)
    attributes: Dict[str, float] = field(default_factory=dict)  # custom numeric attributes (objectives)
    tags: List[str] = field(default_factory=list)                # e.g. "vendor:CoreVendor" (risk drivers)

    @property
    def total_cost(self) -> float:
        return float(sum(self.cost.values()))

    @property
    def capabilities(self) -> List[str]:
        return sorted({c for (c, _k) in self.demand})

    @property
    def is_cross_lob(self) -> bool:
        return bool(self.secondary_lobs) or self.shared_platform

    def shares(self) -> Dict[str, float]:
        return dict(self.funding_shares) if self.funding_shares else {self.primary_lob: 1.0}


@dataclass
class CapacityPool:
    pool_id: str
    capability: str
    owning_lob: str
    capacity_type: CapacityType
    start_month: int
    end_month: Optional[int]
    fte: float
    utilization: float = 0.85
    hours_per_fte: float = 140.0             # productive hours per FTE per month
    cost_rate: float = 0.0                   # loaded $/hour (reporting)
    incremental_cost_rate: float = 0.0       # $/hour charged to consuming LOB budget (e.g. contractors)
    release_eligible: bool = False           # ring-fenced pool may be partially released
    release_efficiency: float = 0.9
    release_cost_rate: float = 0.0           # $/hour when released capacity is used by another LOB
    allowed_funding_sources: List[str] = field(default_factory=list)  # empty = unrestricted
    funding_restrictions: str = ""
    month_factor: Dict[int, float] = field(default_factory=dict)      # what-if: availability by month

    def available_hours(self, month: int) -> float:
        """A[p,t]: zero before start month and after end month (temporary capacity expires)."""
        if month < self.start_month:
            return 0.0
        if self.end_month is not None and month > self.end_month:
            return 0.0
        return self.fte * self.hours_per_fte * self.utilization * self.month_factor.get(month, 1.0)

    @property
    def is_restricted(self) -> bool:
        return self.capacity_type != CapacityType.SHARED


@dataclass
class EligibilityRule:
    pool_id: str
    eligible_lob: str = ANY
    eligible_project_type: str = ANY
    authorized_project_id: str = ""          # owner authorization for a specific (cross-LOB) project
    allocation_restrictions: str = ""


@dataclass
class TransferRule:
    rule_id: str
    source_pool: str
    source_lob: str
    dest_lob: str                            # LOB id or ANY (any LOB other than owner)
    max_hours_per_month: Optional[float] = None
    max_pct: float = 1.0                     # of A[p,t]
    efficiency: float = 1.0                  # productivity after transfer
    cost_per_hour: float = 0.0               # $/hour transfer cost
    approval_required: bool = True
    approval_status: ApprovalStatus = ApprovalStatus.PENDING
    effective_start: int = 1
    effective_end: Optional[int] = None      # return-to-owner month (inclusive last month)
    min_retained_hours: float = 0.0          # owner keeps at least this many hours/month
    synthetic: bool = False                  # created by a scenario (e.g. ring-fence release)
    note: str = ""

    def is_authorized(self) -> bool:
        return (not self.approval_required) or self.approval_status == ApprovalStatus.APPROVED

    def active(self, month: int) -> bool:
        if month < self.effective_start:
            return False
        return self.effective_end is None or month <= self.effective_end


@dataclass
class BudgetLine:
    lob_id: str
    month: int
    funding_source: str
    amount: float
    carry_forward: bool = True               # transferability across months
    allowed_project_types: List[str] = field(default_factory=list)  # empty = unrestricted
    restriction_note: str = ""


@dataclass
class StrategicTarget:
    lob_id: str
    kpi: str
    target: float = 0.0
    min_outcome: Optional[float] = None      # $K attributed benefit floor (when enforced)
    min_investment: Optional[float] = None   # $K
    max_investment: Optional[float] = None   # $K


@dataclass
class BenefitAllocation:
    project_id: str
    lob_id: str
    kpi: str
    attributed_benefit: float
    benefit_owner: str = ""


@dataclass
class Substitution:
    from_capability: str                     # pool capability supplying hours
    to_capability: str                       # demanded capability
    efficiency: float


@dataclass
class Settings:
    horizon: int = 12
    first_month_label: str = "2027-01"
    as_of_month: int = 1                     # months < as_of are consumed actuals
    schedule_discount: float = 0.15          # value erosion for late completion
    enforce_lob_targets: bool = False
    protect_critical: bool = True
    protect_compliance: bool = False
    max_lob_concentration: Optional[float] = None
    enterprise_reservation_pct: float = 0.0  # share of shared pools reserved for enterprise platforms
    min_shared_share: Dict[str, float] = field(default_factory=dict)
    min_investment_share: Dict[str, float] = field(default_factory=dict)
    balance_weight: float = 0.6
    time_limit: int = 20
    mip_gap: float = 0.01
    discount_rate: float = 0.08              # annual rate for NPV (financial objective, Monte Carlo)

    def month_label(self, month: int) -> str:
        y, m = (int(p) for p in self.first_month_label.split("-"))
        idx = (m - 1) + (month - 1)
        return f"{['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][idx % 12]} {y + idx // 12}"


@dataclass
class PortfolioData:
    lobs: Dict[str, LOB]
    projects: Dict[str, Project]
    pools: Dict[str, CapacityPool]
    eligibility: List[EligibilityRule]
    transfers: List[TransferRule]
    budgets: List[BudgetLine]
    targets: List[StrategicTarget]
    benefits: List[BenefitAllocation]
    substitutions: List[Substitution]
    settings: Settings = field(default_factory=Settings)

    @property
    def months(self) -> List[int]:
        return list(range(1, self.settings.horizon + 1))

    def copy(self) -> "PortfolioData":
        return copy.deepcopy(self)

    def business_lobs(self) -> List[str]:
        return [l for l, o in self.lobs.items() if not o.is_enterprise]

    def attributed(self, project_id: str) -> Dict[str, float]:
        out: Dict[str, float] = {}
        for b in self.benefits:
            if b.project_id == project_id:
                out[b.lob_id] = out.get(b.lob_id, 0.0) + b.attributed_benefit
        return out

    def lob_benefit(self, project_id: str, lob_id: str) -> float:
        return self.attributed(project_id).get(lob_id, 0.0)

    def unallocated_benefit(self, project_id: str) -> float:
        p = self.projects[project_id]
        return max(0.0, p.value - sum(self.attributed(project_id).values()))

    def substitution_efficiency(self, supply_cap: str, demand_cap: str) -> float:
        if supply_cap == demand_cap:
            return 1.0
        for s in self.substitutions:
            if s.from_capability == supply_cap and s.to_capability == demand_cap:
                return s.efficiency
        return 0.0

    def budget_mode(self, lob_id: str, source: str) -> bool:
        lines = [b for b in self.budgets if b.lob_id == lob_id and b.funding_source == source]
        return all(b.carry_forward for b in lines) if lines else True

    def budget_types(self, lob_id: str, source: str) -> List[str]:
        types: List[str] = []
        for b in self.budgets:
            if b.lob_id == lob_id and b.funding_source == source:
                types.extend(b.allowed_project_types)
        return sorted(set(types))
