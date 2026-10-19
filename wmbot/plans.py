"""Public plan tiers and their runtime limits.

The MTProto bot transport in this project is capped at 2,000 MB per file. The Infinity
marketing tier therefore gets unlimited daily jobs and priority, but the same hard file cap.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Plan:
    key: str
    name: str
    price: str
    period_days: int | None
    batch_limit: int
    max_file_mb: int
    daily_jobs: int | None       # None means unlimited
    min_crf: int                 # lower CRF means higher quality; jobs cannot request below this
    priority: int                # lower value is processed first


PLANS: dict[str, Plan] = {
    "free": Plan("free", "Free", "₹0", None, 5, 50, 5, 23, 2),
    "plus": Plan("plus", "Plus", "₹149 / 30 days", 30, 50, 500, 50, 20, 1),
    "pro": Plan("pro", "Pro", "₹299 / 30 days", 30, 200, 2000, 200, 18, 1),
    # Telegram bots cannot promise the 4 GB Premium-user upload tier. The actual limit is 2,000 MB.
    "infinity": Plan("infinity", "Infinity", "₹499 / 30 days", 30, 500, 2000, None, 16, 0),
}


def get_plan(key: str | None) -> Plan:
    return PLANS.get((key or "free").strip().lower(), PLANS["free"])
