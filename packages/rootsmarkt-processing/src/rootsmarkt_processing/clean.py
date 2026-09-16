"""Data processing: turn a raw delivery CSV into clean sales rows.

THIS IS THE DATA-PROCESSING LAYER. It reads, coerces, de-duplicates and drops
bad rows. It decides nothing about *when* it runs, what depends on it, or what
should happen when it fails -- that is the orchestration layer's job.

Sealed: do not modify, except where mission D3 tells you to.

Stdlib only, deliberately. This module is imported by both the Astro Docker
image and the host `uv` venv, and a pandas version pinned across two runtimes is
exactly the dependency problem CLAUDE.md says must not become a learning
objective.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

log = logging.getLogger(__name__)

REQUIRED_COLUMNS = ("store_id", "sale_date", "product_id", "category", "units", "unit_price_eur")


@dataclass
class CleanResult:
    rows: list[dict]
    dropped_malformed: int
    dropped_duplicate: int
    missing_expected_stores: list[str]

    @property
    def row_count(self) -> int:
        return len(self.rows)

    @property
    def store_ids(self) -> list[str]:
        return sorted({r["store_id"] for r in self.rows})


def clean_sales(raw_csv_path: str | Path, expected_stores: list[str] | tuple[str, ...]) -> CleanResult:
    """Clean one delivery.

    `expected_stores` is used to report coverage: which stores you were told to
    expect but did not receive. That is a data-quality signal worth logging on
    every run.

    Note what it does NOT currently do: anything about stores that appear in the
    delivery but are *not* in expected_stores.
    """
    path = Path(raw_csv_path)
    if not path.exists():
        raise FileNotFoundError(f"no delivery at {path}")

    expected = set(expected_stores)
    rows: list[dict] = []
    seen: set[tuple] = set()
    malformed = 0
    duplicate = 0

    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        missing_cols = set(REQUIRED_COLUMNS) - set(reader.fieldnames or [])
        if missing_cols:
            raise ValueError(f"delivery is missing columns: {sorted(missing_cols)}")

        for raw in reader:
            try:
                store_id = (raw["store_id"] or "").strip()
                product_id = (raw["product_id"] or "").strip()
                sale_date = (raw["sale_date"] or "").strip()
                units = int(raw["units"])
                unit_price = Decimal(raw["unit_price_eur"])
                if not store_id or not product_id or not sale_date:
                    raise ValueError("blank key field")
                if units < 0 or unit_price < 0:
                    raise ValueError("negative quantity or price")
            except (KeyError, TypeError, ValueError, InvalidOperation):
                malformed += 1
                continue

            key = (store_id, sale_date, product_id)
            if key in seen:
                duplicate += 1
                continue
            seen.add(key)

            rows.append(
                {
                    "store_id": store_id,
                    "sale_date": sale_date,
                    "product_id": product_id,
                    "category": (raw["category"] or "").strip(),
                    "units": units,
                    "unit_price_eur": unit_price,
                    "gross_eur": (unit_price * units).quantize(Decimal("0.01")),
                }
            )

    received = {r["store_id"] for r in rows}
    missing = sorted(expected - received)
    if missing:
        log.warning("delivery is missing %d expected store(s): %s", len(missing), missing)

    result = CleanResult(rows, malformed, duplicate, missing)
    log.info(
        "cleaned %d rows from %d stores (dropped %d malformed, %d duplicate)",
        result.row_count, len(received), malformed, duplicate,
    )
    return result
