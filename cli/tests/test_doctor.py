"""`roots doctor` must always finish, and must not fail on things that do not exist.

Doctor is the single mitigation between the course and losing ten minutes of the
Airflow block to a cold Docker image pull. If it crashes, or cries wolf about
projects that are not built yet, participants stop running it -- and then the
mitigation is gone.
"""

from __future__ import annotations

import subprocess

import pytest
import yaml
from typer.testing import CliRunner

from roots import api, main

runner = CliRunner()

VALID_CONFIG = {
    "team_id": "team-00",
    "supplyhub_token": "sh_00_test",
    "supplyhub_base_url": "http://127.0.0.1:9",
    "expected_stores": [f"RM-{n:04d}" for n in range(101, 113)],
    "plausible_daily_revenue_eur": {"min": 170000, "max": 412000},
    "warehouse": {"schema": "team_00", "user": "team_00", "password": "x"},
}


@pytest.fixture()
def workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "team.yaml").write_text(yaml.safe_dump(VALID_CONFIG))
    # A realistic .env, so the environment section is actually exercised rather
    # than short-circuiting on a missing file in every test.
    (tmp_path / ".env").write_text(
        "LAB_URL=http://127.0.0.1:9\n"
        "ROOTSMARKT_DSN=postgresql://x:y@localhost:5432/z\n"
        "DAGSTER_HOME=" + (tmp_path / ".dagster").as_posix() + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ROOTSMARKT_DSN", "postgresql://x:y@localhost:5432/z")
    monkeypatch.setenv("ROOTS_LAB_URL", "http://127.0.0.1:9")
    # team_config is lru_cached, and each test writes a different file.
    from rootsmarkt_processing import config as cfgmod

    cfgmod.team_config.cache_clear()
    return tmp_path


class _Socket:
    """Deterministic stand-in for a socket probe. `busy` decides the verdict."""

    busy = False

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def settimeout(self, _):
        pass

    def connect_ex(self, _addr):
        return 0 if self.busy else 1


@pytest.fixture()
def happy_externals(monkeypatch):
    """Docker present, warehouse reachable, lab server up, all ports free.

    Ports are mocked deliberately. These tests used to probe REAL ports, so they
    passed or failed depending on whether the developer happened to have Airflow
    running on 8080 -- which is exactly what broke them once the lab was actually
    up. A unit test must not depend on the machine's listening sockets.
    """
    import socket as socketmod

    monkeypatch.setattr(
        main, "_run",
        lambda cmd, timeout=25: subprocess.CompletedProcess(cmd, 0, "29.7.2\n", ""),
    )
    monkeypatch.setattr(api, "healthy", lambda base: True)
    monkeypatch.setattr(socketmod, "socket", lambda *a, **k: _Socket())
    monkeypatch.setenv("DAGSTER_HOME", "/tmp/roots-test-dagster-home")
    import rootsmarkt_processing.load as load

    monkeypatch.setattr(load, "read_daily_revenue", lambda *a, **k: [])
    return monkeypatch


def test_exits_zero_when_only_skips_and_passes_remain(workdir, happy_externals):
    """No Dockerfile, no dags/, no dagster/ -- and that is not a failure.

    This is the state of the repo before Layer 2 lands, and doctor must be usable
    as a pre-flight gate in it.
    """
    result = runner.invoke(main.app, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "SKIP" in result.output
    assert "Airflow project not in this repo yet" in result.output
    assert "Dagster project not in this repo yet" in result.output
    assert "all clear" in result.output


def test_demands_astro_once_the_airflow_project_exists(workdir, happy_externals):
    """The same repo, one Layer 2 step later: now astro genuinely is required."""
    (workdir / "Dockerfile").write_text("FROM astrocrpublic.azurecr.io/runtime:3.3-2\n")
    (workdir / "dags").mkdir()

    # astro missing -> _run returns None for every command
    happy_externals.setattr(main, "_run", lambda cmd, timeout=25: None)
    result = runner.invoke(main.app, ["doctor"])
    assert result.exit_code == 1
    assert "astro not found" in result.output

    # The full tap-qualified command. An earlier version of this test asserted
    # only "without-podman", which the WRONG command (`brew install astro@1.42.1`,
    # a formula that does not exist) also satisfied -- so the suite stayed green
    # while the instruction was unusable.
    assert "astronomer/tap/astro@1.42.1" in result.output
    assert "--without-podman" in result.output
    assert "brew install astro@" not in result.output, "must not suggest the core formula"


def test_flags_a_missing_astro_image_when_astro_is_present(workdir, happy_externals):
    (workdir / "Dockerfile").write_text("FROM x\n")
    (workdir / "dags").mkdir()

    def fake_run(cmd, timeout=25):
        if cmd[0] == "astro":
            return subprocess.CompletedProcess(cmd, 0, "astro 1.42.1\n", "")
        if cmd[:2] == ["docker", "images"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")  # not pulled
        return subprocess.CompletedProcess(cmd, 0, "29.7.2\n", "")

    happy_externals.setattr(main, "_run", fake_run)
    result = runner.invoke(main.app, ["doctor"])
    assert result.exit_code == 1
    assert "NOT pulled" in result.output
    assert "docker pull astrocrpublic.azurecr.io/runtime:3.3-2" in result.output


def test_never_crashes_when_a_section_explodes(workdir, happy_externals):
    """A section that raises must degrade to one FAIL line, not a traceback."""
    def boom(*_a, **_k):
        raise RuntimeError("simulated catastrophe")

    happy_externals.setattr(api, "healthy", boom)
    result = runner.invoke(main.app, ["doctor"])
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "check itself failed" in result.output
    assert "warehouse" in result.output, "later sections must still run"


def test_completes_when_the_lab_server_is_down(workdir, happy_externals):
    """An unreachable lab server is a warning, not a failure -- teams keep working."""
    happy_externals.setattr(api, "healthy", lambda base: False)
    result = runner.invoke(main.app, ["doctor"])
    assert result.exit_code == 0
    assert "unreachable" in result.output
    # Must name a command participants actually HAVE. `roots offline` is
    # instructor-only now (D-052), so pointing them at it would be a dead end.
    assert "roots online --lab-url" in result.output
    assert "roots offline" not in result.output


def test_missing_config_is_a_failure(workdir, happy_externals):
    (workdir / "config" / "team.yaml").unlink()
    from rootsmarkt_processing import config as cfgmod

    cfgmod.team_config.cache_clear()
    result = runner.invoke(main.app, ["doctor"])
    assert result.exit_code == 1
    assert "roots join" in result.output


def test_missing_dsn_is_a_failure(workdir, happy_externals):
    happy_externals.delenv("ROOTSMARKT_DSN", raising=False)
    # `.env` must not put it back: `_load_env` loads that file before the checks
    # run, so clearing only the process environment leaves the DSN present and
    # the test asserting nothing.
    (workdir / ".env").write_text("LAB_URL=http://127.0.0.1:9\n", encoding="utf-8")
    result = runner.invoke(main.app, ["doctor"])
    assert result.exit_code == 1
    assert "ROOTSMARKT_DSN not set" in result.output


# ------------------------------------------------------------------ CLI version


def _astro_at(version: str):
    """_run stub where `astro version` reports a given version."""
    def fake_run(cmd, timeout=25):
        if cmd[0] == "astro":
            return subprocess.CompletedProcess(cmd, 0, f"Astro CLI Version: {version}\n", "")
        if cmd[:2] == ["docker", "images"]:
            return subprocess.CompletedProcess(cmd, 0, "sha256:abc\n", "")  # image present
        return subprocess.CompletedProcess(cmd, 0, "29.7.2\n", "")
    return fake_run


@pytest.fixture()
def airflow_project(workdir):
    (workdir / "Dockerfile").write_text("FROM x\n")
    (workdir / "dags").mkdir()
    return workdir


def test_pinned_cli_version_passes_cleanly(airflow_project, happy_externals):
    happy_externals.setattr(main, "_run", _astro_at("1.42.1"))
    result = runner.invoke(main.app, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "astro 1.42.1" in result.output
    assert "off-pin" not in result.output


def test_off_pin_cli_version_warns_but_does_not_fail(airflow_project, happy_externals):
    """Warning, not failure: the Runtime IMAGE tag is what governs Airflow's
    behaviour and is checked exactly. Blocking a participant over a CLI patch
    difference minutes before the course costs more than it protects."""
    happy_externals.setattr(main, "_run", _astro_at("1.45.0"))
    result = runner.invoke(main.app, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "off-pin" in result.output
    assert "1.45.0" in result.output
    assert "astronomer/tap/astro@1.42.1" in result.output


def test_unparseable_cli_version_warns(airflow_project, happy_externals):
    def fake_run(cmd, timeout=25):
        if cmd[0] == "astro":
            return subprocess.CompletedProcess(cmd, 0, "some banner with no number\n", "")
        if cmd[:2] == ["docker", "images"]:
            return subprocess.CompletedProcess(cmd, 0, "sha256:abc\n", "")
        return subprocess.CompletedProcess(cmd, 0, "29.7.2\n", "")

    happy_externals.setattr(main, "_run", fake_run)
    result = runner.invoke(main.app, ["doctor"])
    assert result.exit_code == 0
    assert "could not read its version" in result.output


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Astro CLI Version: 1.42.1", "1.42.1"),
        ("astro version 1.42.1 (commit abc)", "1.42.1"),
        ("1.45.0", "1.45.0"),
        ("no version here", None),
        ("", None),
    ],
)
def test_parse_astro_version(text, expected):
    assert main._parse_astro_version(text) == expected


def test_port_in_use_only_fails_when_that_project_exists(workdir, happy_externals):
    """8080 busy is irrelevant until there is an Airflow project to run on it."""
    happy_externals.setattr(_Socket, "busy", True)
    result = runner.invoke(main.app, ["doctor"])
    assert result.exit_code == 0, "busy ports must not fail before Layer 2 exists"
    assert "fine if that is the lab itself" in result.output


def test_port_in_use_fails_once_the_airflow_project_exists(airflow_project, happy_externals):
    """And once it does exist, a busy 8080 is a real problem."""
    happy_externals.setattr(_Socket, "busy", True)
    happy_externals.setattr(main, "_run", _astro_at("1.42.1"))
    result = runner.invoke(main.app, ["doctor"])
    assert result.exit_code == 1
    assert "port 8080" in result.output


# ------------------------------------------------- warehouse port collisions


def test_auth_failure_on_5432_blames_the_astro_postgres():
    """`astro dev start` publishes its own Postgres on 5432, so a correct DSN can
    reach Airflow's metadata database instead of the warehouse. psycopg reports
    `password authentication failed`, which sends you to check credentials that
    are fine. Found while verifying the generated student repo."""
    lines = main._warehouse_diagnosis(
        "postgresql://team_01:pw@localhost:5432/rootsmarkt",
        RuntimeError('connection failed: FATAL:  password authentication failed for user "team_01"'),
    )
    text = " ".join(lines)
    assert "astro dev start" in text
    assert "5432" in text
    assert "almost certainly correct" in text


def test_auth_failure_on_another_port_still_suggests_looking():
    lines = main._warehouse_diagnosis(
        "postgresql://team_01:pw@localhost:5433/rootsmarkt",
        RuntimeError("password authentication failed"),
    )
    assert any("5433" in line for line in lines)
    assert not any("astro dev start" in line for line in lines)


def test_other_failures_get_no_spurious_advice():
    """A refused connection is not a port collision, and guessing would be worse
    than saying nothing."""
    assert main._warehouse_diagnosis(
        "postgresql://team_01:pw@localhost:5432/rootsmarkt",
        RuntimeError("connection refused"),
    ) == []


# ------------------------------------------------------------ Windows readiness


def test_backslashes_in_env_are_a_loud_failure(workdir, happy_externals):
    """The Windows bug, caught in pre-flight instead of at minute 20.

    uv discards the whole env file on one backslash, so Dagster starts with no
    configuration and the symptom appears four layers away. Doctor is the only
    place this is cheap to spot.
    """
    (workdir / ".env").write_text(
        "ROOTSMARKT_DSN=postgresql://x:y@localhost:5432/z\n"
        "DAGSTER_HOME=C:\\Users\\bao\\repo\\.dagster\n"
        "ROOTSMARKT_CONFIG=C:\\Users\\bao\\repo\\config\\team.yaml\n",
        encoding="utf-8",
    )
    result = runner.invoke(main.app, ["doctor"])
    assert result.exit_code == 1
    assert "backslashes in" in result.output
    assert "DAGSTER_HOME" in result.output and "ROOTSMARKT_CONFIG" in result.output
    assert "discards the ENTIRE env file" in result.output


def test_a_comment_containing_a_backslash_is_not_flagged(workdir, happy_externals):
    (workdir / ".env").write_text(
        "# on Windows this used to be C:\\Users\\...\n"
        "ROOTSMARKT_DSN=postgresql://x:y@localhost:5432/z\n",
        encoding="utf-8",
    )
    result = runner.invoke(main.app, ["doctor"])
    # Assert on the FAILURE wording: the PASS line also says "no backslashes in
    # values", so a bare substring check passes for the wrong reason.
    assert "has backslashes in" not in result.output
    assert "parses (no backslashes in values)" in result.output


def test_clean_env_passes(workdir, happy_externals):
    result = runner.invoke(main.app, ["doctor"])
    assert "parses (no backslashes in values)" in result.output


def test_missing_env_warns_rather_than_failing_twice(workdir, happy_externals):
    """`config` already reports the one actionable cause. A second failure for
    the same cause makes the failure count meaningless."""
    (workdir / ".env").unlink()
    result = runner.invoke(main.app, ["doctor"])
    assert ".env missing" in result.output
    assert "FAIL  .env missing" not in result.output


# --------------------------------------------------------- platform-aware hints


def test_windows_gets_the_winget_command():
    hint = main.astro_install_hint("win32")
    assert "winget install -e --id Astronomer.Astro -v 1.42.1 --skip-dependencies" in hint
    assert "brew" not in hint
    # The two facts a Windows participant cannot guess.
    assert "PowerShell, NOT in a WSL terminal" in hint
    assert "arm64" in hint


def test_macos_still_gets_the_tap_formula():
    hint = main.astro_install_hint("darwin")
    assert "brew install astronomer/tap/astro@1.42.1 --without-podman" in hint
    assert "winget" not in hint


def test_linux_gets_neither():
    hint = main.astro_install_hint("linux")
    assert "brew" not in hint and "winget" not in hint


def test_the_hint_follows_the_running_platform(monkeypatch):
    monkeypatch.setattr(main.sys, "platform", "win32")
    assert "winget" in main.astro_install_hint()
    monkeypatch.setattr(main.sys, "platform", "darwin")
    assert "brew" in main.astro_install_hint()


def test_doctor_prints_the_windows_hint_on_windows(workdir, happy_externals, monkeypatch):
    """End to end: a Windows participant whose astro is missing must not be
    handed a Homebrew command."""
    monkeypatch.setattr(main.sys, "platform", "win32")
    (workdir / "Dockerfile").touch()
    (workdir / "dags").mkdir(exist_ok=True)

    def no_astro(cmd, timeout=25):
        if cmd[0] == "astro":
            return None
        return subprocess.CompletedProcess(cmd, 0, "29.7.2\n", "")

    monkeypatch.setattr(main, "_run", no_astro)
    result = runner.invoke(main.app, ["doctor"])
    assert "winget install" in result.output
    assert "brew install" not in result.output
