"""Airflow probes, against the CLI contract VERIFIED on 2026-09-14.

The first draft was written from assumption and got three things wrong, each of
which surfaced only when run against a real Airflow:

  1. `dags list-runs` takes dag_id POSITIONALLY. Passing `-d` printed the
     top-level help and exited 2.
  2. Runs carry `run_id`, not `dag_run_id`, and there is **no `conf`**, so the
     business_date of a run cannot be recovered from the CLI at all.
  3. `airflow assets` has no `list-events`.

The fixtures below use the real recorded shapes.
"""

from __future__ import annotations

import pytest

from roots.probes import airflow_probes as ap

RUNS = [
    {"dag_id": "rootsmarkt_delivery", "run_id": "manual__2026-09-14T14:14:30+00:00",
     "state": "failed", "logical_date": "2026-09-14T14:14:27+00:00"},
    {"dag_id": "rootsmarkt_delivery", "run_id": "manual__2026-09-14T14:12:47+00:00",
     "state": "success", "logical_date": "2026-09-14T14:12:46+00:00"},
]

ASSETS = [
    {"name": "rootsmarkt_daily_revenue", "uri": "rootsmarkt://warehouse/daily_revenue",
     "group": "asset", "extra": {}},
]


def _states(**task_states):
    return [{"dag_id": "rootsmarkt_delivery", "task_id": t, "state": s}
            for t, s in task_states.items()]


@pytest.fixture()
def wire(monkeypatch):
    def _apply(per_run, assets=ASSETS, runs=RUNS):
        monkeypatch.setattr(ap, "dag_runs", lambda dag_id=ap.DAG_ID: runs)
        monkeypatch.setattr(ap, "task_states", lambda d, rid: per_run[rid])
        monkeypatch.setattr(ap, "_astro", lambda *a: "")
        monkeypatch.setattr(ap, "_json", lambda text: assets)
    return _apply


# ------------------------------------------------------------------ _json


def test_json_ignores_airflow_log_brackets():
    """Airflow prefixes log lines like `[warning  ] ...`. Scanning for the first
    `[` grabbed a log bracket and failed on perfectly good output."""
    text = (
        "2026-09-14 [warning  ] Astro managed secrets backend is disabled\n"
        "You might need to install the graphviz package.\n"
        '[{"dag_id": "d", "run_id": "r", "state": "success"}]\n'
    )
    assert ap._json(text) == [{"dag_id": "d", "run_id": "r", "state": "success"}]


def test_json_raises_when_there_is_no_payload():
    with pytest.raises(ap.AirflowUnavailable):
        ap._json("2026-09-14 [warning  ] nothing structured here\n")


# -------------------------------------------------------- validation_passed


def test_validation_requires_a_separate_task_that_succeeded(wire):
    wire({
        RUNS[0]["run_id"]: _states(transform="success", validate_load="failed"),
        RUNS[1]["run_id"]: _states(transform="success", validate_load="success"),
    })
    ok, detail = ap.validation_is_a_separate_step()
    assert ok and "validate_load" in detail


def test_validation_not_earned_when_the_check_is_hidden_in_transform(wire):
    """The whole point of checking SHAPE: no separate task, no milestone."""
    wire({r["run_id"]: _states(transform="success", load_warehouse="success") for r in RUNS})
    ok, detail = ap.validation_is_a_separate_step()
    assert not ok and "inside `transform`" in detail


def test_a2_source_check_does_not_count_as_a3_validation(wire):
    """`assert_delivery_is_current` matches the 'assert' hint but guards the
    SOURCE, not the loaded data product."""
    wire({r["run_id"]: _states(assert_delivery_is_current="success") for r in RUNS})
    ok, detail = ap.validation_is_a_separate_step()
    assert not ok, f"A2's source check must not earn A3's milestone: {detail}"


def test_the_gate_does_not_count_as_validation(wire):
    wire({r["run_id"]: _states(publish_daily_revenue="success") for r in RUNS})
    assert ap.validation_is_a_separate_step()[0] is False


# --------------------------------------------------- semantic_gate_enforced


def test_gate_earned_only_when_it_both_rejects_and_accepts(wire):
    wire({
        RUNS[0]["run_id"]: _states(publish_daily_revenue="failed"),
        RUNS[1]["run_id"]: _states(publish_daily_revenue="success"),
    })
    ok, detail = ap.semantic_gate_is_enforced()
    assert ok and "discriminates" in detail


def test_gate_that_never_failed_is_not_a_gate(wire):
    wire({r["run_id"]: _states(publish_daily_revenue="success") for r in RUNS})
    ok, detail = ap.semantic_gate_is_enforced()
    assert not ok and "never failed" in detail


def test_gate_that_rejects_everything_is_not_a_gate(wire):
    wire({r["run_id"]: _states(publish_daily_revenue="failed") for r in RUNS})
    ok, detail = ap.semantic_gate_is_enforced()
    assert not ok and "never succeeded" in detail


def test_gate_missing_entirely(wire):
    wire({r["run_id"]: _states(transform="success") for r in RUNS})
    ok, detail = ap.semantic_gate_is_enforced()
    assert not ok and "no gate task" in detail


# ---------------------------------------------------- asset_event_published


def test_asset_event_requires_both_registration_and_a_successful_outlet(wire):
    wire({
        RUNS[0]["run_id"]: _states(publish_daily_revenue="failed"),
        RUNS[1]["run_id"]: _states(publish_daily_revenue="success"),
    })
    assert ap.asset_event_was_published()[0] is True


def test_asset_registered_but_never_emitted(wire):
    wire({r["run_id"]: _states(publish_daily_revenue="failed") for r in RUNS})
    ok, detail = ap.asset_event_was_published()
    assert not ok and "no task carrying its outlet has succeeded" in detail


def test_asset_not_registered(wire):
    wire(
        {r["run_id"]: _states(publish_daily_revenue="success") for r in RUNS},
        assets=[{"name": "something_else", "uri": "x://y"}],
    )
    ok, detail = ap.asset_event_was_published()
    assert not ok and "not registered" in detail


# ------------------------------------------------------- Windows: ANSI escapes


def test_json_survives_ansi_escapes_in_the_output():
    """The Astro CLI leaks ANSI sequences into Windows terminals -- open bug
    astronomer/astro-cli#635. A leaked `\x1b[0m` before the payload means the
    line no longer starts with `[`, so the scanner would skip the only line that
    mattered and every Airflow probe would report "no JSON in Airflow output"."""
    payload = '[{"dag_id": "rootsmarkt_delivery", "run_id": "manual__1", "state": "success"}]'
    assert ap._json(f"\x1b[0m\x1b[1m{payload}\x1b[0m") == [
        {"dag_id": "rootsmarkt_delivery", "run_id": "manual__1", "state": "success"}
    ]


def test_ansi_stripping_does_not_disturb_clean_output():
    payload = '[{"dag_id": "d", "run_id": "r", "state": "success"}]'
    assert ap._json(payload) == [{"dag_id": "d", "run_id": "r", "state": "success"}]


def test_log_brackets_are_still_skipped_when_ansi_is_present():
    """Both hazards at once: a coloured warning line above the real payload."""
    text = (
        "\x1b[33m[warning  ] something harmless\x1b[0m\n"
        '\x1b[0m[{"dag_id": "d", "run_id": "r", "state": "failed"}]\n'
    )
    assert ap._json(text)[0]["state"] == "failed"
