"""Rootsmarkt assets — missions D1 to D3.

This is your file for the Dagster block.

Same business problem as the Airflow half, asked the other way round. Airflow
asked "what work must run, in what order?" The question here is "what datasets
exist, what are they made of, and when is one valid?"

Read the briefs in `missions/`: D1, then D2, then D3.

WHAT IS ALREADY DONE FOR YOU
    - The project loads. `dg dev` will show you an empty asset graph right now.
    - All DATA PROCESSING is written and sealed in `rootsmarkt_processing`.
      The same modules your Airflow DAG used this morning.

WHAT YOU WILL WRITE
    Asset boundaries, dependency edges, metadata, and one check. Deciding what
    counts as a dataset is orchestration. Deciding what is worth attaching as
    metadata determines what this system can tell you in six months.

NO IO MANAGERS, NO RESOURCES
    These assets read and write directly and return `MaterializeResult`. That is
    a deliberate course decision, and it has one consequence you need up front:

        Because an asset returns MaterializeResult rather than a value, there is
        no stored output for a downstream asset to load. So you declare edges
        with `@asset(deps=[upstream])`, NOT by taking `upstream` as a function
        parameter. The parameter form is idiomatic Dagster when assets return
        values -- it just cannot work here.

STUCK?
    roots hint d1
    roots checkpoint d1
"""

# NOTE: deliberately NO `from __future__ import annotations` here.
# PEP 563 turns annotations into strings, and Dagster resolves the `context`
# parameter's type at runtime -- with the future import it fails with the
# confusing "Cannot annotate `context` parameter with type AssetExecutionContext."
# Verified on 1.13.14. Leave this alone.

import os

from dagster import (
    AssetCheckResult,
    AssetCheckSeverity,
    AssetExecutionContext,
    MaterializeResult,
    MetadataValue,
    asset,
    asset_check,
)

from rootsmarkt_processing import clean, load, revenue, source
from rootsmarkt_processing.config import (
    clean_file,
    delivery_file,
    require_delivery_file,
    team_config,
)

# Partitions are out of scope for this course, so the business date is a plain
# setting rather than a partition key.
BUSINESS_DATE = os.environ.get("ROOTSMARKT_BUSINESS_DATE", "2026-03-04")

# Do NOT add module-level data paths like Path("data/raw"). `dg dev` runs steps
# with cwd=dagster/src, so a relative path silently resolves somewhere else and
# gives you an empty glob instead of an error. Use the helpers imported above --
# they anchor to the repo root.


# ---------------------------------------------------------------- mission D1
#
# TODO(D1): define three assets.
#
#     raw_delivery  ->  clean_sales  ->  daily_revenue
#
# Nothing here is supposed to fail. You are describing datasets and pressing go.
# The deliverable is what you OBSERVE afterwards in the asset catalog, not the
# code -- so when all three are green, go and read what `daily_revenue` says it
# is worth. That number is the mission.
#
# For each asset:
#   - give it a `description` (it shows in the catalog, and D1 asks you to read
#     the catalog)
#   - declare its dependency with `deps=[...]`
#   - return `MaterializeResult(metadata={...})`, not a bare value
#
# What each one should do:
#
#   raw_delivery    Fetch BUSINESS_DATE from SupplyHub and land it.
#                   `source.SupplyHubClient(cfg.supplyhub_base_url,
#                    cfg.supplyhub_token, cfg.team_id)`, then `.get_delivery()`
#                   and `.download(meta, delivery_file(meta.business_date))`.
#                   Worth carrying over from A2: a 200 does not mean you were
#                   given the date you asked for. Decide what to do about that.
#
#   clean_sales     `clean.clean_sales(require_delivery_file(BUSINESS_DATE),
#                    cfg.expected_stores)`, then write the rows to
#                   `clean_file(BUSINESS_DATE)` with this header:
#                   store_id,sale_date,product_id,category,units,unit_price_eur,gross_eur
#                   Attach at least the row count as metadata.
#
#   daily_revenue   Read what clean_sales WROTE -- `revenue.rows_from_clean_csv(
#                    clean_file(BUSINESS_DATE), BUSINESS_DATE)` -- rather than
#                   re-cleaning the raw file. Then `load.load_daily_revenue(rows,
#                    BUSINESS_DATE, schema=cfg.schema)`.
#                   Attach `total_revenue_eur` as metadata. This is the number
#                   D1 exists to make you look at.
#
# `cfg = team_config()` gives you the roster, schema and bounds for YOUR team.


# ---------------------------------------------------------------- mission D2
#
# TODO(D2): add an asset check on `daily_revenue`.
#
#     @asset_check(asset=daily_revenue, blocking=True, description="...")
#     def revenue_is_plausible() -> AssetCheckResult:
#         ...
#
# Two questions, and they are the mission:
#
#   - What makes a revenue figure believable? `cfg.is_plausible(total)` reads
#     YOUR team's bounds. A literal `total > 0` would pass a EUR 12 day.
#     `load.total_loaded_revenue(BUSINESS_DATE, schema=cfg.schema)` reads back
#     what was actually loaded.
#
#   - What does `blocking=True` change? The predicate inside the check is data
#     processing. The decision to stop everything downstream is orchestration,
#     and it is recorded against the DATASET rather than buried in a task log.
#
# Return `AssetCheckResult(passed=..., severity=..., description=...,
# metadata={...})` -- a failing check should say what the number was and what
# range it missed. Someone reads this at 07:00 with no context.


# ---------------------------------------------------------------- mission D3
#
# TODO(D3): no new asset. One rule changes, and then you decide what that
# invalidates. Read missions/D3.md when you get there.
