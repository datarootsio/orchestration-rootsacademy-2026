"""Evidence-submission argument handling.

Regression test for a real fumble: `--rebuilt clean_sales, daily_revenue` — with
a space after the comma — was split by the SHELL into two arguments, and the
second arrived as a stray positional:

    Error: Got unexpected extra argument(s) (daily_revenue)

Typed once, under time pressure, in a room of twenty people. Every spelling of
the list must now produce the same payload.
"""

from __future__ import annotations

import pytest

from roots.main import _asset_list


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param(["clean_sales", "daily_revenue"], id="repeated-flags"),
        pytest.param(["clean_sales,daily_revenue"], id="comma-list"),
        pytest.param(["clean_sales, daily_revenue"], id="quoted-with-space"),
        pytest.param(["clean_sales , daily_revenue "], id="messy-spacing"),
        pytest.param(["clean_sales,", "daily_revenue"], id="the-actual-fumble"),
        pytest.param(["clean_sales,,daily_revenue"], id="double-comma"),
        pytest.param(["clean_sales", "daily_revenue,"], id="trailing-comma"),
    ],
)
def test_every_spelling_yields_the_same_assets(argv):
    assert _asset_list(argv) == ["clean_sales", "daily_revenue"]


def test_empty_and_none_are_empty_lists():
    assert _asset_list(None) == []
    assert _asset_list([]) == []
    assert _asset_list(["", "  ", ","]) == []


def test_order_is_preserved():
    """The server does not care, but a mismatch would be baffling in `lab audit`."""
    assert _asset_list(["b,a", "c"]) == ["b", "a", "c"]


def test_single_asset_still_works():
    assert _asset_list(["raw_delivery"]) == ["raw_delivery"]


# ------------------------------------------------- reporting vs local state
#
# Regression: `_report` only POSTed when a milestone was NEWLY earned locally.
# A milestone earned against a different lab server -- or while this one was
# unreachable and the queue was lost -- stayed in .roots/progress.json forever
# and was never sent again, so the dashboard showed it as not done while the
# team had plainly done it.


def test_reported_is_tracked_separately_from_earned(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from roots import state

    state.record("raw_delivery_landed", "landed")
    assert "raw_delivery_landed" in state.earned()
    assert state.reported() == set(), "earning locally must not imply the server knows"

    state.mark_reported("raw_delivery_landed")
    assert "raw_delivery_landed" in state.reported()


def test_reported_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from roots import state

    state.record("connected", "")
    state.mark_reported("connected")
    state.mark_reported("connected")
    assert sorted(state.progress()["reported"]) == ["connected"]


def test_progress_files_written_before_reporting_existed_still_load(tmp_path, monkeypatch):
    """Old .roots/progress.json has no `reported` key. It must read as
    'nothing acknowledged' so stranded milestones get re-sent, not crash."""
    monkeypatch.chdir(tmp_path)
    import json

    from roots import state

    state.STATE_DIR.mkdir(exist_ok=True)
    state.PROGRESS.write_text(json.dumps({"earned": {"connected": {"at": 1}}, "last_run": 1}))

    assert state.reported() == set()
    assert "connected" in state.earned()
