"""Shared presentation palette and number formatting (dashboard + PowerPoint)."""
from __future__ import annotations

# Categorical slots in fixed order (validated reference palette); entities keep their color.
CATEGORICAL = ["2A78D6", "EB6834", "1BAF7A", "EDA100", "E87BA4", "008300", "6250D6", "E34948"]
STATUS = {"Approved": "0CA30C", "Conditional": "FAB219", "Deferred": "8A8984", "Rejected": "D03B3B"}
INK = "14213D"          # dark title / closing slides
INK_SOFT = "2B3A55"
SURFACE = "FCFCFB"
TEXT = "0B0B0B"
TEXT_2 = "52514E"
MUTED = "8A8984"
GRID = "E4E3DF"
SHARED_C, RESTRICTED_C = CATEGORICAL[0], CATEGORICAL[1]


def lob_colors(lob_ids):
    return {l: CATEGORICAL[i % len(CATEGORICAL)] for i, l in enumerate(lob_ids)}


def money(v_k: float) -> str:
    """$K -> '$27.7M' or '$972K'."""
    if abs(v_k) >= 1000:
        return f"${v_k / 1000:,.1f}M"
    return f"${v_k:,.0f}K"


def signed_money(v_k: float) -> str:
    if abs(v_k) < 0.5:
        return "$0"
    return ("+" if v_k > 0 else "−") + money(abs(v_k))


def pct(v: float) -> str:
    return f"{v * 100:.0f}%"


def hours(v: float) -> str:
    return f"{v:,.0f} h"
