"""Every DAG must import and have no cycles.

Runs inside the Airflow container, where Airflow actually exists:

    astro dev pytest

This is the cheapest guard against a broken DAG file. Run it whenever a DAG
stops showing up in the UI -- the error here is far more readable than the one
the scheduler prints.

TWO MODES, detected automatically:

  STUB mode      `dags/` holds the shipped starter. Only the invariants that
                 must hold while you are still writing the pipeline are checked:
                 it parses, it has tasks, it has no cycles, and the environment
                 around it is sane.

  SOLUTION mode  `dags/` holds a complete implementation, recognised by the A5
                 consumer DAG being present. The full shape is then asserted.
                 This is what guards the reference during instructor rehearsal
                 (`roots solution airflow`), and what a team that finishes A5
                 gets for free.

`test_mode_is_reported` prints which mode ran, so a silent skip is never
mistaken for a pass.
"""

import os
from contextlib import contextmanager

import pytest
from airflow.models import DagBag

DAGS_DIR = os.environ.get("AIRFLOW__CORE__DAGS_FOLDER", "/usr/local/airflow/dags")

CONSUMER = "rootsmarkt_finance_report"
DELIVERY = "rootsmarkt_delivery"


@contextmanager
def _dagbag():
    bag = DagBag(DAGS_DIR, include_examples=False)
    yield bag


def _solution_mode() -> bool:
    """The A5 consumer only exists once someone has written it."""
    with _dagbag() as bag:
        return CONSUMER in bag.dag_ids


solution_only = pytest.mark.skipif(
    not _solution_mode(),
    reason=f"stub mode: {CONSUMER} not present, so there is no A5 consumer to check yet",
)


def test_mode_is_reported(capsys):
    """Not an assertion about the pipeline -- an assertion about this file.

    Makes the mode visible in the output so `5 passed, 4 skipped` cannot be read
    as a full pass of the solution.
    """
    mode = "SOLUTION" if _solution_mode() else "STUB"
    with capsys.disabled():
        print(f"\n[dag integrity] {mode} mode -- dags/ contains: ", end="")
        with _dagbag() as bag:
            print(", ".join(sorted(bag.dag_ids)) or "(no DAGs)")
    assert mode in ("SOLUTION", "STUB")


def test_no_import_errors():
    with _dagbag() as bag:
        assert not bag.import_errors, f"DAG import errors: {bag.import_errors}"


def test_the_delivery_dag_is_present():
    with _dagbag() as bag:
        assert DELIVERY in bag.dag_ids, (
            f"{DELIVERY} did not register. If the file parses, check that "
            "`rootsmarkt_delivery()` is still called at the bottom."
        )


@solution_only
def test_the_consumer_dag_is_present():
    with _dagbag() as bag:
        assert CONSUMER in bag.dag_ids


@pytest.mark.parametrize("dag_id", [DELIVERY, CONSUMER])
def test_dag_has_tasks_and_no_cycles(dag_id):
    with _dagbag() as bag:
        if dag_id == CONSUMER and not _solution_mode():
            pytest.skip("stub mode: no A5 consumer yet")
        # bag.dags, not bag.get_dag() -- the latter queries the metadata DB.
        dag = bag.dags.get(dag_id)
        assert dag is not None, f"{dag_id} not found"
        assert dag.tasks, f"{dag_id} has no tasks"
        # Airflow 3: DAG.test_cycle() is GONE and
        # airflow.utils.dag_cycle_tester is deprecated. dag.check_cycle() is the
        # replacement. Verified against Astro Runtime 3.3-2 (Airflow 3.3).
        dag.check_cycle()


@solution_only
def test_consumer_is_triggered_by_the_asset_not_a_clock():
    """Mission A5's whole point: `schedule=[DAILY_REVENUE]`.

    If this ever becomes a time-based timetable, the consumer is running on a
    clock again and the lesson is lost.
    """
    with _dagbag() as bag:
        dag = bag.dags["rootsmarkt_finance_report"]
        assert type(dag.timetable).__name__ == "AssetTriggeredTimetable"


@solution_only
def test_the_two_dags_do_not_import_each_other():
    """Importing across DAG files re-executes the other module, registering its
    DAG a second time -- Airflow then rejects the duplicate with
    AirflowDagDuplicatedIdException. The consumer redefines the Asset by name
    instead; assets are identified by name, not object identity."""
    import pathlib

    consumer = pathlib.Path(DAGS_DIR) / "rootsmarkt_consumer.py"
    assert "from dags.rootsmarkt_delivery import" not in consumer.read_text()


# ---------------------------------------------------- environment regressions


def test_postgres_provider_is_installed():
    """Regression: the Astro Runtime base image ships only common-sql and
    standard. Without the Postgres provider there is no hook for conn type
    `postgres`, and SQLExecuteQueryOperator dies at RUNTIME with
    `AirflowException: Unknown hook type "postgres"` -- long after the DAG has
    parsed cleanly, so nothing catches it earlier than a live task."""
    from airflow.providers_manager import ProvidersManager

    assert "postgres" in ProvidersManager().hooks, (
        "apache-airflow-providers-postgres is missing -- check requirements.txt"
    )


def test_asset_uri_avoids_scheme_with_a_provider_normalizer():
    """Regression: the asset URI used to be `postgres://rootsmarkt/daily_revenue`.

    Installing the Postgres provider registered a URI normalizer for that scheme
    which demands `postgres://host:port/database/schema/table`, so BOTH DAGs
    began failing at parse time with
    `ValueError: URI format postgres:// must contain database, schema and table names`.

    Conforming would also pull the team-specific schema into parse time. A
    project-owned scheme has no normalizer, is identical for every team, and
    cannot break DAG import.

    Asserted against the declared literals rather than the timetable internals --
    `AssetTriggeredTimetable.asset_condition` is an `AssetAll` whose traversal
    API is not stable, and the literal is what actually matters.
    """
    import pathlib
    import re
    from urllib.parse import urlsplit

    normalized = {"file", "postgres", "postgresql"}
    for path in sorted(pathlib.Path(DAGS_DIR).glob("*.py")):
        text = path.read_text()
        name = path.name
        for uri in re.findall(r'uri="([^"]+)"', text):
            assert urlsplit(uri).scheme not in normalized, (
                f"{name}: {uri} uses a scheme a provider normalizes -- it will "
                "break DAG parse as soon as that provider is installed"
            )


@solution_only
def test_both_dags_agree_on_the_asset_uri():
    """Producer and consumer declare the Asset independently (D-035), so the two
    literals must stay identical or they describe different assets."""
    import pathlib
    import re

    uris = set()
    for name in ("rootsmarkt_delivery.py", "rootsmarkt_consumer.py"):
        text = (pathlib.Path(DAGS_DIR) / name).read_text()
        uris |= set(re.findall(r'uri="([^"]+)"', text))
    assert len(uris) == 1, f"producer and consumer disagree on the asset URI: {uris}"


def test_installed_processing_layer_matches_what_the_dags_need():
    """Catches a stale baked copy of the sealed package.

    `dags/` is live-mounted but `packages/` is installed into the image at build
    time (D-034), so editing the package and restarting leaves the container on
    the old code. That used to surface mid-run as
    `AttributeError: module 'rootsmarkt_processing.load' has no attribute
    'dsn_from_airflow_conn'`, pointing at a DAG line that is perfectly correct.

    Running `astro dev pytest` after a rebuild catches it before any DAG runs.
    """
    from rootsmarkt_processing import config, load, source

    for module, names in (
        (load, ("dsn_from_airflow_conn", "load_daily_revenue", "read_daily_revenue")),
        (source, ("client_from_airflow_conn", "SupplyHubClient")),
        (config, ("require_delivery_file", "clean_file", "team_config")),
    ):
        missing = [n for n in names if not hasattr(module, n)]
        assert not missing, (
            f"{module.__name__} is missing {missing} -- the image has a stale copy of the "
            "sealed package. Rebuild with `astro dev start`."
        )
