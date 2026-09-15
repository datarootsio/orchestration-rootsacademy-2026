"""`.env` must survive uv's parser on Windows.

THE BUG THIS EXISTS FOR. `roots join` wrote absolute paths with the platform's
own separator. On Windows that is `DAGSTER_HOME=C:\\Users\\bao\\repo\\.dagster`,
and uv's `--env-file` parser rejects a value containing backslashes -- discarding
the ENTIRE FILE, not the offending line, with one `warning:` line and exit code 0.

So every `uv run --env-file .env ...` on Windows would run with no DAGSTER_HOME
(ephemeral Dagster instance, materializations never persist, missions D1 and D3
with nothing to observe), no ROOTSMARKT_DSN and no SUPPLYHUB_TOKEN. The whole
Dagster half, silently broken, presenting as "Dagster is broken" rather than
"your configuration was dropped".

These tests run on any platform, because the fault is in uv's parser and uv
behaves the same everywhere. `test_uv_parses_a_generated_env_file_with_windows_paths`
is the one that would actually have caught it: it drives the real binary rather
than asserting something about our own strings.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import PureWindowsPath

import pytest

from roots import main

CFG = {
    "team_id": "team-04",
    # Deliberately NOT 16 hex characters: that is the shape of a real provisioned
    # token, and tools/tests/test_build_student_repo.py greps the built repo for
    # exactly that shape. A realistic-looking fixture would trip the leak check
    # -- correctly, which is why the fixture changes rather than the pattern.
    "supplyhub_token": "sh_04_fixture_not_a_real_token",
    "supplyhub_base_url": "http://localhost:8090",
    "warehouse": {"schema": "team_04"},
}
DSN = "postgresql://team_04:pw@localhost:5432/rootsmarkt"
AIRFLOW_DSN = "postgresql://team_04:pw@host.docker.internal:5432/rootsmarkt"

WIN_HOME = PureWindowsPath(r"C:\Users\bao\Documents\rootsmarkt-orchestration\.dagster")
WIN_CONFIG = PureWindowsPath(r"C:\Users\bao\Documents\rootsmarkt-orchestration\config\team.yaml")


def _win_body() -> str:
    return main._env_body(CFG, "http://localhost:8090", DSN, AIRFLOW_DSN, WIN_HOME, WIN_CONFIG)


# ------------------------------------------------------------ the value itself


def test_windows_paths_become_forward_slashes():
    assert main._env_path_value(WIN_HOME) == (
        "C:/Users/bao/Documents/rootsmarkt-orchestration/.dagster"
    )


def test_a_plain_string_with_backslashes_is_converted_too():
    assert main._env_path_value(r"C:\Users\bao\.dagster") == "C:/Users/bao/.dagster"


def test_posix_paths_are_untouched():
    from pathlib import PurePosixPath

    assert main._env_path_value(PurePosixPath("/Users/bao/repo/.dagster")) == (
        "/Users/bao/repo/.dagster"
    )


# ------------------------------------------------------------- the file itself


def test_no_backslash_reaches_the_env_file():
    """One character is the whole bug."""
    body = _win_body()
    offenders = [line for line in body.splitlines() if "\\" in line]
    assert not offenders, f"backslash in .env would discard the whole file: {offenders}"


def test_every_variable_the_course_needs_is_present():
    keys = {
        line.split("=", 1)[0]
        for line in _win_body().splitlines()
        if line and not line.startswith("#")
    }
    assert keys == {
        "LAB_URL",
        "TEAM_ID",
        "SUPPLYHUB_TOKEN",
        "SUPPLYHUB_BASE_URL",
        "ROOTSMARKT_SCHEMA",
        "ROOTSMARKT_DSN",
        "AIRFLOW_CONN_ROOTSMARKT_DW",
        "DAGSTER_HOME",
        "ROOTSMARKT_CONFIG",
    }


# ------------------------------------------------- the real parser, end to end


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv not on PATH")
def test_uv_parses_a_generated_env_file_with_windows_paths(tmp_path):
    """Drive the actual `uv run --env-file` against a Windows-shaped `.env`.

    Asserting on our own strings would not have caught the original bug -- the
    strings were fine by any reasonable standard, and it was uv that refused
    them. This test fails loudly if uv's parser ever tightens again.
    """
    env_file = tmp_path / ".env"
    env_file.write_text(_win_body(), encoding="utf-8")

    script = (
        "import os,json;"
        "print(json.dumps({k: os.environ.get(k) for k in "
        "['DAGSTER_HOME','ROOTSMARKT_DSN','SUPPLYHUB_TOKEN','TEAM_ID']}))"
    )
    out = subprocess.run(
        ["uv", "run", "--no-project", "--env-file", str(env_file), sys.executable, "-c", script],
        capture_output=True, text=True, cwd=tmp_path, timeout=120,
    )
    assert "Failed to parse environment file" not in out.stderr, out.stderr

    import json

    values = json.loads(out.stdout.strip().splitlines()[-1])
    assert values["TEAM_ID"] == "team-04"
    assert values["SUPPLYHUB_TOKEN"] == CFG["supplyhub_token"]
    assert values["ROOTSMARKT_DSN"] == DSN
    assert values["DAGSTER_HOME"] == (
        "C:/Users/bao/Documents/rootsmarkt-orchestration/.dagster"
    )


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv not on PATH")
def test_negative_control_uv_really_does_discard_a_backslash_file(tmp_path):
    """Proves the failure mode is real, and that it takes the whole file with it.

    Without this, the test above only shows that a file uv likes is parsed -- not
    that the file we used to write was rejected.
    """
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DAGSTER_HOME=C:\\Users\\bao\\repo\\.dagster\nTEAM_ID=team-04\n", encoding="utf-8"
    )
    out = subprocess.run(
        ["uv", "run", "--no-project", "--env-file", str(env_file), sys.executable,
         "-c", "import os;print(os.environ.get('TEAM_ID'))"],
        capture_output=True, text=True, cwd=tmp_path, timeout=120,
    )
    assert "Failed to parse environment file" in out.stderr
    # TEAM_ID has no backslash and is on its own line, and is still lost.
    assert out.stdout.strip().splitlines()[-1] == "None"
