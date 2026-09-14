"""Tests for the sealed processing layer.

The most important test here is
`test_validation_PASSES_on_zero_revenue` -- if that ever fails, mission A4 has no
premise, because the delivery would already be rejected by technical validation.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from rootsmarkt_processing.clean import clean_sales
from rootsmarkt_processing.revenue import daily_revenue, total_revenue_eur
from rootsmarkt_processing.validate import validate_rows

HEADER = "store_id,sale_date,product_id,category,units,unit_price_eur\n"
STORES = ("RM-0101", "RM-0102")


def write_csv(tmp_path, body: str, name="d.csv"):
    p = tmp_path / name
    p.write_text(HEADER + body)
    return p


# ------------------------------------------------------------------------ clean


def test_clean_parses_and_computes_gross(tmp_path):
    p = write_csv(tmp_path, "RM-0101,2026-03-02,P-1,dairy,3,2.50\n")
    r = clean_sales(p, STORES)
    assert r.row_count == 1
    assert r.rows[0]["gross_eur"] == Decimal("7.50")


def test_clean_drops_malformed_rows(tmp_path):
    body = (
        "RM-0101,2026-03-02,P-1,dairy,3,2.50\n"
        "RM-0101,2026-03-02,P-2,dairy,notanumber,2.50\n"   # bad units
        "RM-0101,2026-03-02,P-3,dairy,1,abc\n"             # bad price
        ",2026-03-02,P-4,dairy,1,1.00\n"                   # blank store
        "RM-0101,2026-03-02,P-5,dairy,-2,1.00\n"           # negative units
    )
    r = clean_sales(write_csv(tmp_path, body), STORES)
    assert r.row_count == 1
    assert r.dropped_malformed == 4


def test_clean_dedupes_on_store_date_product(tmp_path):
    body = (
        "RM-0101,2026-03-02,P-1,dairy,3,2.50\n"
        "RM-0101,2026-03-02,P-1,dairy,3,2.50\n"
    )
    r = clean_sales(write_csv(tmp_path, body), STORES)
    assert r.row_count == 1
    assert r.dropped_duplicate == 1


def test_clean_reports_missing_expected_stores(tmp_path):
    p = write_csv(tmp_path, "RM-0101,2026-03-02,P-1,dairy,1,1.00\n")
    r = clean_sales(p, STORES)
    assert r.missing_expected_stores == ["RM-0102"]


def test_clean_does_NOT_filter_unexpected_stores(tmp_path):
    """Locks in the pre-D3 behaviour.

    The test store arrives in every delivery and is NOT dropped. If this ever
    starts passing the other way, mission D3 has already been solved for the
    participants and its milestone becomes unreachable.
    """
    body = (
        "RM-0101,2026-03-02,P-1,dairy,1,10.00\n"
        "T-999,2026-03-02,P-9,dairy,1,99.00\n"
    )
    r = clean_sales(write_csv(tmp_path, body), STORES)
    assert "T-999" in r.store_ids
    assert r.row_count == 2


def test_clean_rejects_a_delivery_with_missing_columns(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("store_id,sale_date\nRM-0101,2026-03-02\n")
    with pytest.raises(ValueError, match="missing columns"):
        clean_sales(p, STORES)


def test_clean_raises_when_there_is_no_delivery(tmp_path):
    with pytest.raises(FileNotFoundError):
        clean_sales(tmp_path / "nope.csv", STORES)


# ---------------------------------------------------------------------- revenue


def test_revenue_aggregates_per_store(tmp_path):
    body = (
        "RM-0101,2026-03-02,P-1,dairy,2,3.00\n"
        "RM-0101,2026-03-02,P-2,dairy,1,4.00\n"
        "RM-0102,2026-03-02,P-3,dairy,5,2.00\n"
    )
    rows = daily_revenue(clean_sales(write_csv(tmp_path, body), STORES), "2026-03-02")
    by_store = {r.store_id: r for r in rows}
    assert by_store["RM-0101"].revenue_eur == Decimal("10.00")
    assert by_store["RM-0101"].units_sold == 3
    assert by_store["RM-0102"].revenue_eur == Decimal("10.00")
    assert total_revenue_eur(rows) == Decimal("20.00")


def test_zero_prices_produce_zero_revenue(tmp_path):
    """The A4 delivery at the processing level: rows present, revenue nil."""
    body = "".join(f"RM-010{i%2+1},2026-03-04,P-{i},dairy,{i+1},0.00\n" for i in range(20))
    rows = daily_revenue(clean_sales(write_csv(tmp_path, body), STORES), "2026-03-04")
    assert len(rows) == 2
    assert total_revenue_eur(rows) == Decimal("0.00")
    assert all(r.units_sold > 0 for r in rows)  # units are real; only prices are nil


# --------------------------------------------------------------------- validate


def test_validation_PASSES_on_zero_revenue():
    """THE test that mission A4 depends on.

    Every store reported, no nulls, no negatives, rows present -- and the data
    product is worth nothing. Technical validation must go GREEN here. If it
    fails, the pipeline rejects the delivery before A4 begins and the mission's
    entire premise ("every task is green and the answer is wrong") evaporates.
    """
    rows = [("RM-0101", Decimal("0.00"), 120), ("RM-0102", Decimal("0.00"), 98)]
    report = validate_rows("2026-03-04", rows, STORES)
    assert report.passed is True
    assert report.total_revenue_eur == Decimal("0.00")
    report.raise_for_status()  # must not raise


def test_validation_fails_on_no_rows():
    assert validate_rows("2026-03-04", [], STORES).failures == ["no rows loaded"]


def test_validation_fails_on_a_missing_store():
    rows = [("RM-0101", Decimal("5.00"), 1)]
    report = validate_rows("2026-03-02", rows, STORES)
    assert not report.passed
    assert "RM-0102" in report.failures[0]


def test_validation_fails_on_negatives_and_nulls():
    assert not validate_rows(
        "2026-03-02", [("RM-0101", Decimal("-1"), 1), ("RM-0102", Decimal("1"), 1)], STORES
    ).passed
    assert not validate_rows(
        "2026-03-02", [("RM-0101", None, 1), ("RM-0102", Decimal("1"), 1)], STORES
    ).passed


def test_validation_ignores_unexpected_stores():
    """A superset passes: the test store must not trip A3."""
    rows = [
        ("RM-0101", Decimal("5.00"), 1),
        ("RM-0102", Decimal("5.00"), 1),
        ("T-999", Decimal("0.50"), 1),
    ]
    assert validate_rows("2026-03-02", rows, STORES).passed is True


def test_raise_for_status_raises_on_failure():
    with pytest.raises(ValueError, match="validation failed"):
        validate_rows("2026-03-04", [], STORES).raise_for_status()


# -------------------------------------------------- clean-CSV round trip
#
# `daily_revenue` used to re-clean the RAW file instead of reading what
# `clean_sales` wrote, which duplicated mission D3's roster filter across two
# assets. A participant fixing clean_sales alone would then have seen revenue
# not budge, and D3's lesson would have been wrong.


def test_rows_from_clean_csv_matches_in_memory_aggregation(tmp_path):
    """Reading from disk must equal aggregating in memory, or the Airflow and
    Dagster halves would disagree on the same delivery."""
    from rootsmarkt_processing.revenue import rows_from_clean_csv

    body = (
        "RM-0101,2026-03-02,P-1,dairy,2,3.00\n"
        "RM-0101,2026-03-02,P-2,dairy,1,4.00\n"
        "RM-0102,2026-03-02,P-3,dairy,5,2.00\n"
    )
    clean = clean_sales(write_csv(tmp_path, body), STORES)
    in_memory = daily_revenue(clean, "2026-03-02")

    out = tmp_path / "clean.csv"
    with out.open("w") as fh:
        fh.write("store_id,sale_date,product_id,category,units,unit_price_eur,gross_eur\n")
        for r in clean.rows:
            fh.write(
                f"{r['store_id']},{r['sale_date']},{r['product_id']},{r['category']},"
                f"{r['units']},{r['unit_price_eur']},{r['gross_eur']}\n"
            )

    from_disk = rows_from_clean_csv(out, "2026-03-02")
    assert [(r.store_id, r.revenue_eur, r.units_sold) for r in from_disk] == [
        (r.store_id, r.revenue_eur, r.units_sold) for r in in_memory
    ]


def test_rows_from_clean_csv_reflects_a_filtered_upstream(tmp_path):
    """If clean_sales drops a store, the downstream total must drop with it.

    This is mission D3's mechanism: the fix belongs in ONE place.
    """
    from rootsmarkt_processing.revenue import rows_from_clean_csv, total_revenue_eur

    out = tmp_path / "clean.csv"
    header = "store_id,sale_date,product_id,category,units,unit_price_eur,gross_eur\n"
    out.write_text(header + "RM-0101,2026-03-02,P-1,dairy,1,10.00,10.00\n")
    assert total_revenue_eur(rows_from_clean_csv(out, "2026-03-02")) == Decimal("10.00")

    out.write_text(
        header
        + "RM-0101,2026-03-02,P-1,dairy,1,10.00,10.00\n"
        + "T-999,2026-03-02,P-9,dairy,1,99.00,99.00\n"
    )
    assert total_revenue_eur(rows_from_clean_csv(out, "2026-03-02")) == Decimal("109.00")
