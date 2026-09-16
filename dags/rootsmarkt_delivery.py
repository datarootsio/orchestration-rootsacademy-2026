"""Rootsmarkt daily delivery pipeline — missions A1 to A4.

This is your file. You will be editing it for the whole Airflow block.

Read the briefs in `missions/` as you go: A1 first, then A2, A3, A4.

WHAT IS ALREADY DONE FOR YOU
    - The DAG exists and is registered. It parses right now; check the UI.
    - `ensure_warehouse_table` is written. It is the one piece of SQL the
      orchestrator owns, and it is idempotent so a re-run is harmless.
    - Every piece of DATA PROCESSING is written and sealed, in the
      `rootsmarkt_processing` package. You should never need to open it, but
      reading it is allowed and often the fastest way to find a signature.

WHAT YOU WILL WRITE
    Orchestration. Every TODO below is a decision about *when* something runs,
    *what counts as success*, and *what the system should record* -- not about
    how to parse a CSV.

THE LAYER SPLIT, WHICH IS THE POINT OF THE DAY
    Processing  = reading, cleaning, aggregating, loading.   Sealed package.
    Orchestration = sequencing, failure policy, retries,     THIS FILE.
                    what "done" means, what gets recorded.

    When you write a line here, you should be able to say which one it is. If
    you cannot, that line is probably in the wrong file.

STUCK?
    roots hint a2            # two hints per mission, self-service
    roots checkpoint a2      # adopt a known-good state and keep moving
"""

import os
from datetime import timedelta
from pathlib import Path

from airflow.sdk import Asset, dag, task
from airflow.providers.common.sql.operators.sql import SQLExecuteQueryOperator

from rootsmarkt_processing import clean, load, revenue, source, validate
from rootsmarkt_processing.config import (
    clean_file,
    require_delivery_file,
    delivery_file,
    team_config,
)

# Airflow 3 moved this to airflow.sdk.exceptions; the old path still works but
# warns on every parse. Try the current one first, and keep the fallbacks so a
# DAG parse error can never mask a real problem.
try:
    from airflow.sdk.exceptions import AirflowFailException
except ImportError:  # pragma: no cover
    try:
        from airflow.exceptions import AirflowFailException
    except ImportError:
        AirflowFailException = RuntimeError

# Data paths come from rootsmarkt_processing.config, anchored to wherever
# config/team.yaml lives -- which is the repo root on the host and
# /usr/local/airflow in this container. The Dockerfile sets
# ROOTSMARKT_DATA_DIR to a directory Astro actually mounts, so files written
# here are visible from the host.

# The sealed processing layer is BAKED INTO THE IMAGE (decision D-034), while
# dags/ is live-mounted. So editing packages/ and restarting is not enough --
# the container keeps the old copy, and the mismatch surfaces mid-run as a bare
# `AttributeError: module ... has no attribute ...` pointing at a DAG line that
# looks perfectly correct.
#
# Checking at import time turns that into a DAG import error, visible in the UI
# straight away and naming the remedy.
_REQUIRED = {
    "rootsmarkt_processing.load": ("dsn_from_airflow_conn", "load_daily_revenue"),
    "rootsmarkt_processing.source": ("client_from_airflow_conn",),
    "rootsmarkt_processing.config": ("require_delivery_file", "clean_file"),
}
for _module_name, _names in _REQUIRED.items():
    _module = __import__(_module_name, fromlist=["_"])
    _missing = [n for n in _names if not hasattr(_module, n)]
    if _missing:
        raise RuntimeError(
            f"{_module_name} is missing {', '.join(_missing)}. The image has a stale copy of "
            "the sealed processing layer -- packages/ is baked in at build time, so a restart "
            "is not enough. Rebuild with `astro dev start` (add --no-cache if it persists)."
        )


DW_CONN_ID = "rootsmarkt_dw"      # pre-provisioned; A3 is not a credentials exercise
SUPPLYHUB_CONN_ID = "supplyhub"   # created by participants -- that IS mission A1

# The data product, as something the rest of the platform can depend on.
# Airflow 3.3 has first-class assets; this is not a Dagster-only idea, and the
# course says so out loud (decision D-009).
DAILY_REVENUE = Asset(
    name="rootsmarkt_daily_revenue",
    # NOT postgres:// -- providers register URI normalizers for their own
    # schemes, and the Postgres one (airflow/providers/postgres/assets/postgres.py)
    # demands exactly `postgres://host:port/database/schema/table`. Anything else
    # fails at DAG PARSE time:
    #
    #   ValueError: URI format postgres:// must contain database, schema and table names
    #
    # Conforming would also drag the team-specific schema into parse time, so a
    # missing or wrong env var would break DAG import for the whole room.
    # Normalizers are registered for file://, postgres:// and postgresql:// only,
    # so a project-owned scheme is stable and identical for every team.
    uri="rootsmarkt://warehouse/daily_revenue",
)

DEFAULT_BUSINESS_DATE = os.environ.get("ROOTSMARKT_BUSINESS_DATE", "2026-03-02")


@dag(
    dag_id="rootsmarkt_delivery",
    schedule=None,  # triggered manually; scheduling is out of scope
    catchup=False,
    params={"business_date": DEFAULT_BUSINESS_DATE},
    default_args={"retries": 2, "retry_delay": timedelta(seconds=30)},
    tags=["rootsmarkt", "solution"],
    doc_md=__doc__,
)
def rootsmarkt_delivery():

    @task
    def fetch_delivery(**context) -> dict:
        """A1. Ask SupplyHub for one business date and land the file on disk.

        TODO(A1):
          1. Create an Airflow connection named `supplyhub`. Run
             `roots airflow-conn` -- it prints the exact values, including which
             of your two team secrets is the right one.
          2. Build a client with `source.client_from_airflow_conn(SUPPLYHUB_CONN_ID)`.
             The token must come from the connection. Not from this file, not
             from an environment variable you added.
          3. Ask for the business date in `context["params"]["business_date"]`,
             and write the file to `delivery_file(<the date the delivery is for>)`.

        Return only small values. The data goes to disk; XCom is not a data bus.
        The returned dict is what the next task reasons about, so think about
        what the next task will need to know.
        """
        business_date = context["params"]["business_date"]
        raise NotImplementedError("A1: fetch the delivery -- see missions/A1.md")

    @task
    def assert_delivery_is_current(delivery: dict, **context) -> dict:
        """A2. SupplyHub answered 200. Did it answer the question you asked?

        TODO(A2): decide what this task should check, and what it should do when
        the check fails.

        Two orchestration decisions live here, and they are the mission:
          - What exactly makes a delivery "not the one we asked for"?
          - Is this worth retrying? Look at the `retries` in `default_args` and
            decide whether they should apply to this task at all. There is a
            defensible argument in more than one direction -- make yours.
        """
        raise NotImplementedError("A2: is this delivery the one you asked for? -- see missions/A2.md")

    @task
    def transform(delivery: dict, **context) -> dict:
        """A3. Delegate to the sealed processing layer, then hand off on disk.

        TODO(A3):
          1. `clean.clean_sales(<raw path>, cfg.expected_stores)` does the work.
          2. Write the result to `clean_file(business_date)`, header first:
             store_id,sale_date,product_id,category,units,unit_price_eur,gross_eur
          3. Return what `load_warehouse` needs to find it.

        Resist the urge to validate here. A3 asks for validation as its own
        task, and the reason is worth understanding before you disagree with it.
        """
        raise NotImplementedError("A3: transform -- see missions/A3.md")

    # The one bit of SQL the orchestrator owns. Uses the pre-provisioned DW
    # connection, and is idempotent so a re-run is harmless.
    # NOTE: PostgresOperator is REMOVED in the Airflow 3 provider -- this is
    # SQLExecuteQueryOperator, and `database` replaced `schema`.
    ensure_warehouse_table = SQLExecuteQueryOperator(
        task_id="ensure_warehouse_table",
        conn_id=DW_CONN_ID,
        sql="""
        CREATE TABLE IF NOT EXISTS daily_revenue (
          business_date date          NOT NULL,
          store_id      text          NOT NULL,
          revenue_eur   numeric(14,2) NOT NULL,
          units_sold    integer       NOT NULL,
          loaded_at     timestamptz   NOT NULL DEFAULT now(),
          PRIMARY KEY (business_date, store_id)
        );
        """,
    )

    @task
    def load_warehouse(transformed: dict, **context) -> dict:
        """A3. Write the aggregated revenue to the warehouse.

        TODO(A3):
          1. `revenue.rows_from_clean_csv(<clean path>, business_date)`.
          2. `load.load_daily_revenue(rows, business_date, dsn=..., schema=cfg.schema)`.

        On the DSN: this task runs INSIDE the Airflow container, where
        `localhost` means the container itself. `ROOTSMARKT_DSN` is correct on
        your host for Dagster, and wrong here. Use
        `load.dsn_from_airflow_conn(DW_CONN_ID)` -- the same route
        `ensure_warehouse_table` already takes. One answer to "where is the
        warehouse?", owned by the orchestrator.

        Then think about ordering: this task and `ensure_warehouse_table` have a
        dependency that passing data around will not express for you.
        """
        raise NotImplementedError("A3: load the warehouse -- see missions/A3.md")

    @task
    def validate_load(loaded: dict, **context) -> dict:
        """A3. Validate the loaded data -- as its own task, deliberately.

        TODO(A3): call `validate.validate_load(business_date, cfg.expected_stores,
        dsn=..., schema=cfg.schema)`, print the report, and fail the task if it
        did not pass. `ValidationReport` has a method for that last part.

        Why a separate task: if this lived inside `transform`, Airflow would only
        ever know "transform failed" -- not what was invalid, and not that the
        transform itself was fine. Separation is what makes the failure legible.

        Its scope is TECHNICAL only: rows present, every store accounted for, no
        nulls, no negatives. Keep it that way. What it does not check is A4.
        """
        raise NotImplementedError("A3: validate the load -- see missions/A3.md")

    @task
    def publish_daily_revenue(validated: dict, **context) -> dict:
        """A4. Everything above is green. Is the data product actually worth anything?

        TODO(A4):
          1. Decide what makes a daily revenue figure believable, and stop the
             pipeline when it is not. `cfg.is_plausible(total)` reads the bounds
             from YOUR team's config. A literal `total > 0` would happily pass a
             EUR 12 day, and the check exists precisely for days like that.
          2. Make this task emit the `DAILY_REVENUE` asset event -- and only on
             the success path. Something downstream will be scheduled on it
             (A5), and it should fire on valid data, not on a clock.
             Look at the `outlets` argument of `@task`, and at
             `context["outlet_events"][DAILY_REVENUE].extra`.
          3. Think about `retries` again. Is a poisoned delivery transient?

        This is the incident from minute 0 of the course. The rule "revenue must
        be plausible" is processing. The decision to STOP THE PIPELINE on it is
        orchestration. Same predicate, two different layers, depending on who
        consumes the answer.
        """
        raise NotImplementedError("A4: define what success means -- see missions/A4.md")

    # TODO(A1-A4): wire the tasks together.
    #
    # Calling a task returns a handle you pass to the next one -- that expresses
    # both "runs after" and "hands data to". For a dependency that is ordering
    # only, with no data passed, use `>>`.
    #
    # Knowing which of the two you need, for each edge, is most of mission A3.
    delivery = fetch_delivery()


rootsmarkt_delivery()
