"""The Airflow probes must be fast without ever changing their answer.

Each `astro dev run` is a container exec costing ~3s. All four Airflow probes
fetched the same run history and then filtered it differently in memory: with 12
runs that was 56 calls and ~110s per `roots verify`, and it made `roots watch`
slower than its own 20s interval -- so the dashboard the instructor steers by
lagged reality by minutes.

Measured after: 14 calls / 28.6s cold, 2 calls / 4.1s warm.

These tests patch `_astro` rather than `dag_runs`/`task_states`, so the real
caching path is exercised instead of being stubbed over.
"""

from __future__ import annotations

import json

import pytest

from roots.probes import airflow_probes as ap

RUN_OK = {"dag_id": "rootsmarkt_delivery", "run_id": "r-ok", "state": "success",
          "end_date": "2026-09-16T09:00:00+00:00"}
RUN_FAIL = {"dag_id": "rootsmarkt_delivery", "run_id": "r-fail", "state": "failed",
            "end_date": "2026-09-16T08:00:00+00:00"}
RUN_LIVE = {"dag_id": "rootsmarkt_delivery", "run_id": "r-live", "state": "running",
            "end_date": None}

STATES = {
    "r-ok": [{"task_id": "assert_delivery_is_current", "state": "success"}],
    "r-fail": [{"task_id": "assert_delivery_is_current", "state": "failed"}],
    "r-live": [{"task_id": "assert_delivery_is_current", "state": "running"}],
}


@pytest.fixture()
def astro(tmp_path, monkeypatch):
    """A fake `astro dev run` that counts calls and answers from `runs`."""
    monkeypatch.chdir(tmp_path)          # CACHE_PATH is relative to cwd
    ap.begin_pass()

    state = {"runs": [RUN_OK, RUN_FAIL], "calls": [], "states": dict(STATES)}

    def fake(*args):
        state["calls"].append(args)
        if args[:2] == ("dags", "list-runs"):
            return json.dumps(state["runs"])
        if args[:2] == ("tasks", "states-for-dag-run"):
            return json.dumps(state["states"][args[3]])
        if args[:2] == ("assets", "list"):
            return json.dumps([])
        raise AssertionError(f"unexpected astro call: {args}")

    monkeypatch.setattr(ap, "_astro", fake)
    return state


def _calls(state, kind: str) -> int:
    return sum(1 for c in state["calls"] if c[0] == kind)


# ------------------------------------------------------------- within one pass


def test_one_pass_fetches_the_run_list_once(astro):
    """Four probes asking the same question was four times the container execs."""
    ap.stale_delivery_was_blocked()
    ap.validation_is_a_separate_step()
    ap.semantic_gate_is_enforced()
    assert _calls(astro, "dags") == 1


def test_one_pass_fetches_each_run_once(astro):
    ap.stale_delivery_was_blocked()
    ap.semantic_gate_is_enforced()
    assert _calls(astro, "tasks") == len(astro["runs"])


def test_begin_pass_picks_up_a_new_run(astro):
    """`roots watch` loops. A memo that outlived the pass would freeze the
    daemon's view at whatever existed when it started."""
    ap.stale_delivery_was_blocked()
    astro["runs"] = [*astro["runs"], RUN_LIVE]

    ap.begin_pass()
    ap.stale_delivery_was_blocked()
    assert len(ap.dag_runs()) == 3


# -------------------------------------------------------------- across passes


def test_a_second_pass_refetches_only_the_run_list(astro):
    ap.stale_delivery_was_blocked()
    before = len(astro["calls"])

    ap.begin_pass()
    ap.stale_delivery_was_blocked()
    new = astro["calls"][before:]
    assert _calls({"calls": new}, "tasks") == 0, "finished runs must come from cache"
    assert _calls({"calls": new}, "dags") == 1, "the run list is always refetched"


def test_a_running_run_is_never_cached(astro):
    """Its task states can still change. Caching it would freeze a half-finished
    pipeline into the milestone record."""
    astro["runs"] = [RUN_LIVE]
    ap.stale_delivery_was_blocked()

    ap.begin_pass()
    ap.stale_delivery_was_blocked()
    assert _calls(astro, "tasks") == 2, "a running run must be refetched every pass"


def test_a_cleared_and_rerun_run_is_refetched(astro):
    """THE correctness trap. A finished run is not immutable -- clearing a task in
    Airflow puts the run back to `running` and re-runs it, so a cached `failed`
    could hide a later `success`. The entry is validated against (state,
    end_date), which changes when that happens.
    """
    ap.stale_delivery_was_blocked()
    assert _calls(astro, "tasks") == 2

    # Same run_id, re-run: new end_date, and the task now succeeds.
    astro["runs"] = [RUN_OK, {**RUN_FAIL, "state": "success",
                              "end_date": "2026-09-16T10:30:00+00:00"}]
    astro["states"]["r-fail"] = [{"task_id": "assert_delivery_is_current", "state": "success"}]

    ap.begin_pass()
    ap.task_states(ap.DAG_ID, "r-fail")
    fresh = ap.task_states(ap.DAG_ID, "r-fail")
    assert fresh[0]["state"] == "success", "stale cache hid a re-run"


def test_a_corrupt_cache_is_ignored_not_fatal(astro, tmp_path):
    """A performance cache that can break a milestone check is not worth having."""
    ap.CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    ap.CACHE_PATH.write_text("{not json", encoding="utf-8")

    ap.begin_pass()
    ok, detail = ap.stale_delivery_was_blocked()
    assert ok, detail


def test_the_cache_is_pruned_to_runs_airflow_still_has(astro):
    ap.stale_delivery_was_blocked()
    astro["runs"] = [RUN_OK]

    ap.begin_pass()
    ap.stale_delivery_was_blocked()
    assert set(json.loads(ap.CACHE_PATH.read_text(encoding="utf-8"))) == {"r-ok"}


# ---------------------------------------------------------------- equivalence


def test_the_cache_never_changes_a_verdict(astro):
    """The only property that actually matters. A cache that changes answers is
    worse than a slow probe."""
    probes = ("stale_delivery_was_blocked", "validation_is_a_separate_step",
              "semantic_gate_is_enforced")

    ap.begin_pass()
    cold = {n: getattr(ap, n)() for n in probes}

    ap.begin_pass()
    warm = {n: getattr(ap, n)() for n in probes}

    ap.CACHE_PATH.unlink(missing_ok=True)
    ap.begin_pass()
    uncached = {n: getattr(ap, n)() for n in probes}

    assert cold == warm == uncached
