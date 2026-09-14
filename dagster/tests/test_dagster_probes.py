"""Regression tests for the Dagster check probes.

The bug: a check sits in PLANNED status until its evaluation event arrives, and
a PLANNED record's `.evaluation` is an `AssetCheckEvaluationPlanned` — which has
no `.passed`. Reading it blindly raised
`AttributeError: 'AssetCheckEvaluationPlanned' object has no attribute 'passed'`
and both D2 milestones reported a probe error instead of a verdict.

The fix passes `status={SUCCEEDED, FAILED}` to the history query so the storage
layer excludes PLANNED entirely, rather than the probe guessing at event types.
"""

from __future__ import annotations

import pytest

# Lives in dagster/tests rather than cli/tests because it needs BOTH `roots` and
# `dagster`. Only the root venv has both -- cli/.venv is the CLI's own
# development environment and deliberately has no Dagster.

from roots.probes import dagster_probes as dp


class _FakeStorage:
    def __init__(self, records):
        self.records = records
        self.calls = []

    def get_asset_check_execution_history(self, check_key, limit, status=None, **kw):
        self.calls.append({"check_key": check_key, "limit": limit, "status": status})
        if status is None:
            return self.records
        return [r for r in self.records if r.status in status]


class _FakeInstance:
    def __init__(self, storage):
        self.event_log_storage = storage

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Rec:
    """Mimics AssetCheckExecutionRecord closely enough, including the trap:
    a PLANNED record's `.evaluation` has no `.passed`."""

    def __init__(self, status, passed=None):
        self.status = status
        if passed is None:
            self.evaluation = type("AssetCheckEvaluationPlanned", (), {})()
        else:
            self.evaluation = type("AssetCheckEvaluation", (), {"passed": passed})()


@pytest.fixture()
def statuses():
    from dagster._core.storage.asset_check_execution_record import (
        AssetCheckExecutionRecordStatus as S,
    )

    return S


def _wire(monkeypatch, records):
    storage = _FakeStorage(records)
    monkeypatch.setattr(dp, "_instance", lambda: _FakeInstance(storage))
    return storage


def test_planned_records_are_excluded_by_the_query(monkeypatch, statuses):
    """THE regression. A PLANNED record must never reach `.passed`."""
    storage = _wire(monkeypatch, [_Rec(statuses.PLANNED)])
    assert dp.check_outcomes() == []
    assert storage.calls[0]["status"] == {statuses.SUCCEEDED, statuses.FAILED}


def test_planned_mixed_with_terminal_records(monkeypatch, statuses):
    _wire(
        monkeypatch,
        [_Rec(statuses.PLANNED), _Rec(statuses.FAILED, False), _Rec(statuses.SUCCEEDED, True)],
    )
    # history comes back newest-first; outcomes are returned oldest-first
    assert dp.check_outcomes() == [True, False]


def test_status_is_used_when_no_evaluation_verdict_exists(monkeypatch, statuses):
    """A check that raised has a terminal status but no usable evaluation."""
    _wire(monkeypatch, [_Rec(statuses.FAILED), _Rec(statuses.SUCCEEDED)])
    assert dp.check_outcomes() == [True, False]


def test_check_has_failed_requires_a_recorded_failure(monkeypatch, statuses):
    monkeypatch.setattr(dp, "load_graph", lambda: dp.GraphInfo(set(), {}, {dp.CHECK_NAME}))

    _wire(monkeypatch, [_Rec(statuses.SUCCEEDED, True)])
    ok, detail = dp.check_has_failed()
    assert ok is False
    assert "only ever passed" in detail

    _wire(monkeypatch, [_Rec(statuses.FAILED, False)])
    assert dp.check_has_failed()[0] is True


def test_check_has_failed_reports_a_missing_check_definition(monkeypatch):
    monkeypatch.setattr(dp, "load_graph", lambda: dp.GraphInfo(set(), {}, set()))
    ok, detail = dp.check_has_failed()
    assert ok is False
    assert dp.CHECK_NAME in detail


def test_check_has_passed(monkeypatch, statuses):
    _wire(monkeypatch, [_Rec(statuses.FAILED, False)])
    assert dp.check_has_passed() == (False, "the check has never passed yet")

    _wire(monkeypatch, [_Rec(statuses.SUCCEEDED, True), _Rec(statuses.FAILED, False)])
    assert dp.check_has_passed()[0] is True


def test_no_dagster_home_is_pending_not_an_error(monkeypatch):
    """`roots watch` must not die because the env is half-configured."""
    monkeypatch.delenv("DAGSTER_HOME", raising=False)
    with pytest.raises(dp.DagsterUnavailable, match="DAGSTER_HOME"):
        dp.materializations()
