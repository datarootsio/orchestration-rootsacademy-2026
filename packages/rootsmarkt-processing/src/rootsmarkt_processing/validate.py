"""Data processing: technical validation of a loaded business date.

Scope: did the load work? Rows present, every expected store accounted for, no
nulls, no negative figures, one row per store.

That is all it checks. Whether the resulting number is *believable* for a
grocery chain is a different question, and not one this function answers.

Sealed: do not modify.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .load import read_daily_revenue


@dataclass
class ValidationReport:
    business_date: str
    row_count: int = 0
    store_count: int = 0
    total_revenue_eur: Decimal = Decimal("0.00")
    failures: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.failures

    def raise_for_status(self) -> "ValidationReport":
        if not self.passed:
            raise ValueError(
                f"validation failed for {self.business_date}: " + "; ".join(self.failures)
            )
        return self

    def __str__(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        return (
            f"[{status}] {self.business_date}: {self.row_count} rows, "
            f"{self.store_count} stores, total EUR {self.total_revenue_eur:,.2f}"
            + ("" if self.passed else " -- " + "; ".join(self.failures))
        )


def validate_load(
    business_date: str,
    expected_stores: list[str] | tuple[str, ...],
    dsn: str | None = None,
    schema: str | None = None,
) -> ValidationReport:
    """Read the loaded rows and validate them."""
    return validate_rows(
        business_date, read_daily_revenue(business_date, dsn, schema), expected_stores
    )


def validate_rows(
    business_date: str,
    rows: list[tuple[str, Decimal, int]],
    expected_stores: list[str] | tuple[str, ...],
) -> ValidationReport:
    """The pure core. Split out from `validate_load` so its behaviour is testable
    without a database -- including the property mission A4 depends on."""
    report = ValidationReport(business_date=business_date)

    if not rows:
        report.failures.append("no rows loaded")
        return report

    report.row_count = len(rows)
    loaded_stores = [r[0] for r in rows]
    report.store_count = len(set(loaded_stores))
    report.total_revenue_eur = sum((r[1] for r in rows if r[1] is not None), Decimal("0.00"))

    missing = sorted(set(expected_stores) - set(loaded_stores))
    if missing:
        report.failures.append(f"missing expected store(s): {', '.join(missing)}")

    if len(loaded_stores) != len(set(loaded_stores)):
        report.failures.append("duplicate store rows for one business date")

    for store_id, revenue, units in rows:
        if revenue is None or units is None:
            report.failures.append(f"{store_id}: null revenue or units")
        elif revenue < 0 or units < 0:
            report.failures.append(f"{store_id}: negative revenue or units")

    return report
