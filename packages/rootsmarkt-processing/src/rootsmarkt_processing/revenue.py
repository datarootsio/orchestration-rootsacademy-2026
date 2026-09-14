"""Data processing: aggregate clean sales into per-store daily revenue.

Deliberately uninteresting. The master prompt is explicit that the transform
must not be algorithmically interesting -- the difficulty in this course belongs
to orchestration reasoning, never to the arithmetic.

Sealed: do not modify.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from .clean import CleanResult


@dataclass(frozen=True)
class StoreRevenue:
    business_date: str
    store_id: str
    revenue_eur: Decimal
    units_sold: int


def daily_revenue(clean: CleanResult, business_date: str) -> list[StoreRevenue]:
    """One row per store. Rows whose sale_date differs from `business_date` are
    still aggregated -- deciding what to do about a delivery containing the wrong
    dates is an orchestration question, not this function's business."""
    revenue: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
    units: dict[str, int] = defaultdict(int)

    for row in clean.rows:
        revenue[row["store_id"]] += row["gross_eur"]
        units[row["store_id"]] += row["units"]

    return [
        StoreRevenue(
            business_date=business_date,
            store_id=store_id,
            revenue_eur=revenue[store_id].quantize(Decimal("0.01")),
            units_sold=units[store_id],
        )
        for store_id in sorted(revenue)
    ]


def total_revenue_eur(rows: list[StoreRevenue]) -> Decimal:
    return sum((r.revenue_eur for r in rows), Decimal("0.00"))


def rows_from_clean_csv(path, business_date: str) -> list[StoreRevenue]:
    """Aggregate a clean_sales CSV into per-store daily revenue.

    The counterpart to `daily_revenue`, for when the cleaned rows arrive on disk
    rather than in memory. Reading the upstream asset's actual output — instead
    of re-cleaning the raw delivery — is what keeps the cleaning rule defined in
    exactly one place.
    """
    revenue: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
    units: dict[str, int] = defaultdict(int)

    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            store_id = row["store_id"]
            revenue[store_id] += Decimal(row["gross_eur"])
            units[store_id] += int(row["units"])

    return [
        StoreRevenue(
            business_date=business_date,
            store_id=store_id,
            revenue_eur=revenue[store_id].quantize(Decimal("0.01")),
            units_sold=units[store_id],
        )
        for store_id in sorted(revenue)
    ]
