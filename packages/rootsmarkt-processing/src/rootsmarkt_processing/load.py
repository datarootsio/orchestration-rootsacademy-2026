"""Data processing: load per-store daily revenue into Postgres.

Idempotent BY CONTRACT: `load_daily_revenue` deletes the target business_date
before inserting, so running it twice leaves the same numbers.

That property is why mission A3 works. The lesson is not writing this function
-- it is provided -- but *noticing that it matters*, and that a plain INSERT
would silently double every figure on the second run.

Sealed: do not modify.
"""

from __future__ import annotations

import logging
import os
from decimal import Decimal

from .revenue import StoreRevenue

log = logging.getLogger(__name__)

DSN_ENV = "ROOTSMARKT_DSN"


def dsn_from_airflow_conn(conn_id: str = "rootsmarkt_dw") -> str:
    """Build a libpq DSN from an Airflow connection.

    This is how the Airflow half reaches the warehouse, and it matters for two
    reasons.

    Correctness: the container cannot resolve the host's `localhost`. `.env`
    carries ROOTSMARKT_DSN for the HOST (Dagster, roots) and the Airflow
    connection for the CONTAINER. Reading the env var inside the container gave
    `connection to server at "127.0.0.1", port 5432 failed: Connection refused`,
    because 127.0.0.1 there is the container itself.

    Consistency: `ensure_warehouse_table` already reaches Postgres through this
    connection. Having the load use a different route meant two answers to "where
    is the warehouse?", and only one of them was right. The orchestrator owns the
    credential -- which is the point mission A1 makes.

    NOTE: Airflow stores the DATABASE name in `Connection.schema`. That is not
    the Postgres schema (`team_00`), which is passed separately.
    """
    from urllib.parse import quote

    from .airflow_conn import describe, get_connection

    conn = get_connection(conn_id)
    host = (getattr(conn, "host", "") or "").strip()
    database = (getattr(conn, "schema", "") or "").strip()
    user = (getattr(conn, "login", "") or "").strip()
    password = getattr(conn, "password", "") or ""
    port = getattr(conn, "port", None) or 5432

    missing = [
        label
        for label, value in (("host", host), ("database (schema field)", database), ("login", user))
        if not value
    ]
    if missing:
        raise RuntimeError(
            f"Airflow connection {conn_id!r} is missing: {', '.join(missing)}.\n  "
            + describe(conn_id, conn)
        )

    return (
        f"postgresql://{quote(user, safe='')}:{quote(password, safe='')}"
        f"@{host}:{port}/{database}"
    )


def dsn_from_env() -> str:
    dsn = os.environ.get(DSN_ENV, "")
    if not dsn:
        raise RuntimeError(
            f"{DSN_ENV} is not set. `roots join` writes it to .env; "
            "if the warehouse is unreachable, run `roots offline`."
        )
    return dsn


def _connect(dsn: str | None = None):
    import psycopg

    return psycopg.connect(dsn or dsn_from_env(), autocommit=False)


def load_daily_revenue(
    rows: list[StoreRevenue], business_date: str, dsn: str | None = None, schema: str | None = None
) -> int:
    """Replace this business_date's rows. Returns rows written.

    Delete-then-insert inside one transaction: a failed load leaves the previous
    good data in place rather than a half-written day.
    """
    from psycopg import sql

    schema = schema or os.environ.get("ROOTSMARKT_SCHEMA", "public")
    table = sql.SQL("{}.daily_revenue").format(sql.Identifier(schema))

    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL("DELETE FROM {} WHERE business_date = %s").format(table), (business_date,)
            )
            cur.executemany(
                sql.SQL(
                    "INSERT INTO {} (business_date, store_id, revenue_eur, units_sold)"
                    " VALUES (%s, %s, %s, %s)"
                ).format(table),
                [(r.business_date, r.store_id, r.revenue_eur, r.units_sold) for r in rows],
            )
        conn.commit()

    log.info("loaded %d rows for %s into %s.daily_revenue", len(rows), business_date, schema)
    return len(rows)


def read_daily_revenue(
    business_date: str, dsn: str | None = None, schema: str | None = None
) -> list[tuple[str, Decimal, int]]:
    """(store_id, revenue_eur, units_sold) for one business date."""
    from psycopg import sql

    schema = schema or os.environ.get("ROOTSMARKT_SCHEMA", "public")
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            sql.SQL(
                "SELECT store_id, revenue_eur, units_sold FROM {}.daily_revenue"
                " WHERE business_date = %s ORDER BY store_id"
            ).format(sql.Identifier(schema)),
            (business_date,),
        )
        return cur.fetchall()


def total_loaded_revenue(
    business_date: str, dsn: str | None = None, schema: str | None = None
) -> Decimal:
    rows = read_daily_revenue(business_date, dsn, schema)
    return sum((r[1] for r in rows), Decimal("0.00"))
