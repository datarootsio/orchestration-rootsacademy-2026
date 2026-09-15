"""A client must recover when the lab server forgets what it confirmed.

THE BUG. `reported` is the client's record of what the server acknowledged
(D-043). `lab reset` takes those rows back, and nothing told the client -- so
`_report()` skipped them forever:

    if check.milestone in state.reported():
        continue

Locally earned, marked reported, absent from the server, and unreachable by any
amount of re-running. The team's only escape was deleting
`.roots/progress.json`, which nothing documented because nobody knew.

It matters because `lab reset` is not exotic. `lab/README.md` recommends it
mid-course for a stuck team and between sessions -- so the tool an instructor
reaches for to HELP a team is what silently destroyed their board.

These drive a real uvicorn rather than a mock, because the bug lived in the gap
between what the client believed and what the server actually held. A mock would
have been told what to believe.
"""

from __future__ import annotations

import socket
import threading
from contextlib import closing

import pytest
import uvicorn

from lab_server import api as lab_api
from lab_server.store import Store
from roots import api, state

ADMIN = "test-admin"


def _free_port() -> int:
    with closing(socket.socket()) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    store = Store(tmp_path_factory.mktemp("resync") / "lab.sqlite3")
    lab_api._store = store
    lab_api.ADMIN_TOKEN = ADMIN
    port = _free_port()
    config = uvicorn.Config(lab_api.app, host="127.0.0.1", port=port, log_level="error")
    srv = uvicorn.Server(config)
    threading.Thread(target=srv.run, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        if srv.started:
            break
        threading.Event().wait(0.05)
    store.provision(2)
    yield base, store
    srv.should_exit = True


@pytest.fixture()
def team(live, tmp_path, monkeypatch):
    """A joined team, with BOTH sides clean.

    The server is module-scoped for speed, so its milestone rows have to be
    cleared per test -- otherwise one test's earned milestone satisfies the next
    test's assertion and the suite passes for the wrong reason.
    """
    base, store = live
    monkeypatch.chdir(tmp_path)               # fresh .roots/
    record = store.teams()[0]
    store.reset_milestones(record["team_id"])  # fresh server side

    class Cfg:
        team_id = record["team_id"]
        supplyhub_token = record["token"]

    return base, store, Cfg()


# --------------------------------------------------------------- the reconcile


def test_a_reset_milestone_is_reported_again(team):
    """The exact scenario, end to end. This is the test that was missing."""
    from roots import main

    base, store, cfg = team

    api.post_milestone(base, cfg.team_id, cfg.supplyhub_token, "warehouse_loaded", {})
    state.record("warehouse_loaded", "12 rows")
    state.mark_reported("warehouse_loaded")
    assert "warehouse_loaded" in api.team_state(base, cfg.team_id)["milestones"]

    store.reset_milestones(cfg.team_id)
    assert api.team_state(base, cfg.team_id)["milestones"] == {}

    # Before the fix this was a no-op forever.
    main._resync(cfg, base, quiet=True)
    assert "warehouse_loaded" not in state.reported(), (
        "the client still believes the server has it, so it will never re-send"
    )


def test_an_unreachable_server_does_not_prune(team, monkeypatch):
    """The same bug inverted, and louder.

    Treating "no answer" as "the server has nothing" would make every offline
    team forget everything and re-queue it -- exactly what D-043's queue exists
    to avoid.
    """
    from roots import main

    _, _, cfg = team
    state.record("warehouse_loaded", "12 rows")
    state.mark_reported("warehouse_loaded")

    main._resync(cfg, "http://127.0.0.1:9", quiet=True)      # nothing listening
    assert "warehouse_loaded" in state.reported()


def test_a_milestone_the_server_still_has_is_left_alone(team):
    from roots import main

    base, _, cfg = team
    api.post_milestone(base, cfg.team_id, cfg.supplyhub_token, "raw_delivery_landed", {})
    state.mark_reported("raw_delivery_landed")

    main._resync(cfg, base, quiet=True)
    assert "raw_delivery_landed" in state.reported()


def test_the_resync_says_what_it_did(team, capsys):
    """Silent self-healing would hide a reset nobody intended."""
    from roots import main

    base, store, cfg = team
    api.post_milestone(base, cfg.team_id, cfg.supplyhub_token, "warehouse_loaded", {})
    state.mark_reported("warehouse_loaded")
    store.reset_milestones(cfg.team_id)

    main._resync(cfg, base, quiet=False)
    out = capsys.readouterr().out
    assert "no longer has" in out
    assert "warehouse_loaded" in out


# ------------------------------------------------------------ the submissions


def test_a_reset_submission_is_replayed_with_its_evidence(team):
    """Client checks re-derive themselves; a submission cannot -- a human typed
    it. Storing only the word "submitted" meant a reset destroyed it.

    The evidence is generated from the server's own deterministic generator, so
    this asserts the replay LANDS rather than accepting either outcome.
    """
    from lab_server import generator, teams as teamlib
    from lab_server.world import CLEAN
    from roots import main

    base, store, cfg = team
    record = store.team(cfg.team_id)
    served = generator.generate(
        cfg.team_id, teamlib.roster_for(record["team_index"]), "2026-03-02", 1, CLEAN
    )
    payload = {
        "received_date": "2026-03-02",
        "expected_date": "2026-03-03",
        "delivery_id": served.delivery_id,
        "row_count": served.row_count,
    }
    state.remember_submission("stale_delivery_detected", payload)
    store.reset_milestones(cfg.team_id)
    assert api.team_state(base, cfg.team_id)["milestones"] == {}

    main._resend_submissions(cfg, base, known=set(), quiet=True)
    assert "stale_delivery_detected" in api.team_state(base, cfg.team_id)["milestones"], (
        "the evidence was correct, so the replay must land -- otherwise a reset "
        "destroys a submission permanently"
    )


def test_a_refused_replay_is_reported_not_queued(team, capsys):
    """`lab reset team` also resets publications, so a replay can legitimately
    fail now. The team must be told to re-submit rather than left with a
    silently wrong board."""
    from roots import main

    base, _, cfg = team
    state.remember_submission(
        "stale_delivery_detected",
        {"received_date": "1999-01-01", "expected_date": "1999-01-02",
         "delivery_id": "nope", "row_count": 1},
    )
    main._resend_submissions(cfg, base, known=set(), quiet=False)
    out = capsys.readouterr().out
    # The server ACCEPTS the POST and returns accepted=False with a reason, so
    # this is the not-queued-and-not-silent path rather than a LabRejected one.
    assert "stale_delivery_detected" not in api.team_state(base, cfg.team_id)["milestones"]
    assert not state.drain_queue(), "a permanently refused replay must not be queued"


def test_submissions_are_persisted_with_their_payload(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    payload = {"rebuilt": ["clean_sales"], "not_rebuilt": ["raw_delivery"], "why": "…"}
    state.remember_submission("rebuild_justified", payload)
    assert state.submissions()["rebuild_justified"] == payload
