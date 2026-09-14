"""The CLI must work before `roots join` has ever run.

This is the state every participant is in when they open the repo for the first
time, and it is the one state the instructor repo is never in -- `config/team.yaml`
has existed there since the lab was built, so nothing caught it.

What it cost: `roots --help` raised at IMPORT time, because the module decided
whether to register `roots solution` by calling `cfgmod.repo_root()`, which
derives the root from `config/team.yaml` and raises when it is absent. Every
command died, `roots join` and `roots doctor` included -- the two commands whose
entire job is to get a team out of that state.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def unjoined(tmp_path, monkeypatch):
    """A repo-shaped directory with no config/team.yaml, and cwd pointed at it."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\nversion='0'\n")
    (tmp_path / "dags").mkdir()
    (tmp_path / "hints" / "a2").mkdir(parents=True)
    (tmp_path / "hints" / "a2" / "1.md").write_text("first hint")
    (tmp_path / "hints" / "a2" / "2.md").write_text("second hint")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ROOTSMARKT_CONFIG", raising=False)
    return tmp_path


def test_importing_the_cli_does_not_need_a_team_config(unjoined):
    """The regression itself: import-time work must never require joining."""
    import importlib

    from roots import main

    importlib.reload(main)
    assert main.app is not None


def test_repo_root_falls_back_to_walking_up(unjoined):
    import importlib

    from roots import main

    importlib.reload(main)
    assert main._repo_root() == unjoined.resolve()


def test_help_works_in_a_fresh_clone(unjoined):
    """End to end, through the real entry point -- the reload-based tests above
    would not have caught a failure in the console script itself."""
    result = subprocess.run(
        [sys.executable, "-c", "from roots.main import main; main()", "--help"],
        cwd=unjoined, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    for command in ("join", "doctor", "hint", "checkpoint"):
        assert command in result.stdout


def test_solution_is_not_registered_without_a_solutions_directory(unjoined):
    result = subprocess.run(
        [sys.executable, "-c", "from roots.main import main; main()", "--help"],
        cwd=unjoined, capture_output=True, text=True,
    )
    assert "solution" not in result.stdout, (
        "`roots solution` is instructor-only and must not appear in the student repo"
    )


def test_solution_is_registered_when_solutions_exists(unjoined):
    (unjoined / "solutions").mkdir()
    result = subprocess.run(
        [sys.executable, "-c", "from roots.main import main; main()", "--help"],
        cwd=unjoined, capture_output=True, text=True,
    )
    assert "solution" in result.stdout


def test_hint_works_before_joining(unjoined):
    """A team reading ahead on the train should get a hint, not a stack trace."""
    result = subprocess.run(
        [sys.executable, "-c", "from roots.main import main; main()", "hint", "a2"],
        cwd=unjoined, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "first hint" in result.stdout


# ------------------------------------------------------------- the error paths
#
# These exist because the first version of these commands called a helper named
# `_fail` that does not exist in this module -- so every error path raised
# NameError instead of printing its message. The happy-path tests all passed.


@pytest.mark.parametrize("command", ["hint", "checkpoint"])
def test_unknown_mission_is_reported_not_crashed(unjoined, command):
    result = subprocess.run(
        [sys.executable, "-c", "from roots.main import main; main()", command, "zz9"],
        cwd=unjoined, capture_output=True, text=True,
    )
    assert result.returncode == 2
    assert "NameError" not in result.stderr
    assert "unknown mission" in result.stdout + result.stderr


def test_a_mission_with_no_hints_says_so(unjoined):
    result = subprocess.run(
        [sys.executable, "-c", "from roots.main import main; main()", "hint", "d4"],
        cwd=unjoined, capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert "NameError" not in result.stderr
    assert "no hints found" in result.stdout + result.stderr


def test_a_missing_checkpoint_says_so(unjoined):
    result = subprocess.run(
        [sys.executable, "-c", "from roots.main import main; main()", "checkpoint", "a3", "--yes"],
        cwd=unjoined, capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert "NameError" not in result.stderr
    assert "no checkpoint" in result.stdout + result.stderr
