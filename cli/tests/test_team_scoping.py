"""Local progress belongs to a team, and the client can clear it.

TWO BUGS, both found while diagnosing why an instructor's solution runs showed
up on a participant's dashboard.

1. `.roots/progress.json` had no team in it, and `roots join` never touched it.
   Joining a different team carried the previous team's milestones across and
   reported them under the new identity. A team that mistypes a join code, joins
   the wrong team, then re-joins correctly would take the wrong team's progress
   with them.

2. There was no client-side reset at all. That was survivable while a server-side
   `lab reset` was permanent -- but milestones now re-sync (D-062), so a reset
   alone no longer clears a board: any machine still holding local state
   re-reports into the fresh lab. The documented between-sessions procedure
   silently stopped working.
"""

from __future__ import annotations

import pytest

from roots import state


@pytest.fixture(autouse=True)
def here(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _earn(name: str = "warehouse_loaded") -> None:
    state.record(name, "detail")
    state.mark_reported(name)


# ------------------------------------------------------------- team scoping


def test_a_first_join_on_a_clean_machine_clears_nothing():
    assert state.clear_for_team("team-00") is False
    assert state.joined_team() == "team-00"


def test_rejoining_the_same_team_keeps_progress():
    """`roots join` is the documented remedy when something looks wrong, so
    re-running it must not wipe a team's board."""
    state.clear_for_team("team-00")
    _earn()
    assert state.clear_for_team("team-00") is False
    assert "warehouse_loaded" in state.earned()
    assert "warehouse_loaded" in state.reported()


def test_switching_teams_clears_progress():
    """THE bug. Without this, team-01's board inherits team-00's work."""
    state.clear_for_team("team-00")
    _earn()
    state.remember_submission("stale_delivery_detected", {"row_count": 1})
    state.enqueue("check_passing", {})

    assert state.clear_for_team("team-01") is True

    assert state.earned() == set()
    assert state.reported() == set()
    assert state.submissions() == {}
    assert state.drain_queue() == []
    assert state.joined_team() == "team-01"


def test_switching_teams_keeps_machine_facts():
    """Which hints this laptop has read, and which checkpoints it adopted, are
    facts about the machine -- not about whoever it reports as."""
    state.clear_for_team("team-00")
    state.record_hint("a2", 2)
    state.record_adoption("a3", "/tmp/backup")
    _earn()

    state.clear_for_team("team-01")

    assert state.hints_shown("a2") == 2
    assert "a3" in state.adopted()
    assert state.earned() == set()


def test_progress_earned_before_team_tracking_existed_is_not_silently_kept():
    """Upgrade path: a file written before `team_id` existed has progress but no
    owner. Treating that as "same team" would preserve exactly the cross-team
    contamination this prevents."""
    _earn()                                   # no team recorded
    assert state.joined_team() is None
    assert state.clear_for_team("team-01") is True
    assert state.earned() == set()


# ----------------------------------------------------------------- the reset


def test_clear_progress_drops_milestones_and_submissions():
    state.clear_for_team("team-00")
    _earn("warehouse_loaded")
    _earn("validation_passed")
    state.remember_submission("rebuild_justified", {"why": "..."})
    state.enqueue("check_passing", {})

    dropped = state.clear_progress()

    assert dropped == {"earned": 2, "submissions": 1}
    assert state.earned() == set()
    assert state.reported() == set()
    assert state.submissions() == {}
    assert state.drain_queue() == []


def test_clear_progress_keeps_hints_adoptions_and_the_team():
    state.clear_for_team("team-00")
    state.record_hint("a4", 1)
    state.record_adoption("d2", "/tmp/b")
    _earn()

    state.clear_progress()

    assert state.hints_shown("a4") == 1
    assert "d2" in state.adopted()
    assert state.joined_team() == "team-00", (
        "clearing progress is not leaving the team -- the next report must still "
        "go to the right board"
    )


def test_clear_progress_on_a_clean_machine_is_harmless():
    assert state.clear_progress() == {"earned": 0, "submissions": 0}
