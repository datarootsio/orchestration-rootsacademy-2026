"""Where `roots` thinks the lab is.

Before `config/lab-url` existed the fallback was `http://localhost:8090`, which
is correct only for whoever hosts the lab. Every participant therefore needed
`--lab-url` on `roots join` -- a flag the README did not show, on the first
command anyone runs.

`lab host` now writes the address into the repo before it is handed out. The
precedence matters as much as the lookup: `.env` must still win, so a team told
mid-course to `roots online --lab-url ...` stays where they were sent instead of
snapping back to the baked-in address on the next command.
"""

from __future__ import annotations

import pytest

from roots import api

BAKED = "http://192.168.0.155:8090"
FROM_ENV_FILE = "http://10.0.0.9:8090"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    monkeypatch.delenv("ROOTS_LAB_URL", raising=False)
    monkeypatch.delenv("LAB_URL", raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _bake(root, url: str = BAKED) -> None:
    (root / "config").mkdir(exist_ok=True)
    (root / "config" / "lab-url").write_text(url + "\n", encoding="utf-8")


def _dotenv(root, url: str = FROM_ENV_FILE) -> None:
    (root / ".env").write_text(f"LAB_URL={url}\nTEAM_ID=team-01\n", encoding="utf-8")


# ---------------------------------------------------------------- the fallback


def test_localhost_only_when_nothing_else_says_otherwise(clean_env):
    assert api.lab_url() == "http://localhost:8090"


def test_a_baked_address_beats_localhost(clean_env):
    _bake(clean_env)
    assert api.lab_url() == BAKED


def test_a_trailing_slash_is_stripped(clean_env):
    _bake(clean_env, "http://192.168.0.155:8090/")
    assert api.lab_url() == BAKED


def test_comments_and_blank_lines_are_skipped(clean_env):
    (clean_env / "config").mkdir()
    (clean_env / "config" / "lab-url").write_text(
        f"# written by `lab host`\n\n{BAKED}\n", encoding="utf-8"
    )
    assert api.lab_url() == BAKED


def test_an_empty_file_falls_through(clean_env):
    (clean_env / "config").mkdir()
    (clean_env / "config" / "lab-url").write_text("\n# nothing here\n", encoding="utf-8")
    assert api.lab_url() == "http://localhost:8090"


def test_it_is_found_from_a_subdirectory(clean_env):
    """Participants run commands from wherever they happen to be."""
    _bake(clean_env)
    sub = clean_env / "dagster" / "src"
    sub.mkdir(parents=True)
    import os

    os.chdir(sub)
    assert api.lab_url() == BAKED


# ------------------------------------------------------------------ precedence


def test_dotenv_beats_the_baked_address(clean_env):
    """The point of the ordering: `roots online` must stick.

    A team sent to the instructor's laptop mid-course would otherwise revert to
    the shipped address on their very next command, and the symptom would be
    intermittent rather than obvious.
    """
    _bake(clean_env)
    _dotenv(clean_env)
    assert api.lab_url() == FROM_ENV_FILE


def test_environment_beats_dotenv(clean_env, monkeypatch):
    _bake(clean_env)
    _dotenv(clean_env)
    monkeypatch.setenv("LAB_URL", "http://env:8090")
    assert api.lab_url() == "http://env:8090"


def test_roots_lab_url_beats_lab_url(clean_env, monkeypatch):
    monkeypatch.setenv("LAB_URL", "http://env:8090")
    monkeypatch.setenv("ROOTS_LAB_URL", "http://roots:8090")
    assert api.lab_url() == "http://roots:8090"


def test_an_explicit_override_beats_everything(clean_env, monkeypatch):
    _bake(clean_env)
    _dotenv(clean_env)
    monkeypatch.setenv("ROOTS_LAB_URL", "http://roots:8090")
    assert api.lab_url("http://explicit:8090") == "http://explicit:8090"


def test_an_unreadable_baked_file_is_not_fatal(clean_env):
    """A lookup that can raise would take down `roots join` -- the one command
    whose entire job is to recover from a bad setup."""
    (clean_env / "config").mkdir()
    (clean_env / "config" / "lab-url").mkdir()   # a directory where a file belongs
    assert api.lab_url() == "http://localhost:8090"
