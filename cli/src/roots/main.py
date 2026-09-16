"""The `roots` CLI -- the participant's side of the lab.

    roots join <code>     one-time setup
    roots doctor          pre-flight, run DAYS before the course
    roots verify [a1]     run checks now and report
    roots watch           the daemon that keeps the dashboard honest
    roots submit a2 ...   evidence submissions
    roots online --lab-url <url>   point at a different lab server
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import typer
import yaml

from rootsmarkt_processing import config as cfgmod

from . import api, checks, state

app = typer.Typer(add_completion=False, help="Rootsmarkt orchestration lab (participant CLI)")

CONFIG_PATH = Path("config/team.yaml")
ENV_PATH = Path(".env")
PORTS = {8080: "Airflow UI", 3000: "Dagster UI", 8090: "local lab mock", 5433: "local Postgres"}

ASTRO_PINNED = "1.42.1"
ASTRO_PINNED_MINOR = "1.42."
ASTRO_RUNTIME_IMAGE = "astrocrpublic.azurecr.io/runtime:3.3-2"

# macOS. Verified 2026-08-15 against the tap formula index and astro@1.42.1.rb.
# The versioned formula lives in astronomer/homebrew-tap, NOT homebrew-core:
# core is on 1.45.0 and publishes no versioned formulae, so plain
# `brew install astro` is wrong twice over -- off-pin AND it pulls Podman.
# The tap formula declares podman as :recommended, which is what makes
# --without-podman a valid option. See docs/version-verification.md.
ASTRO_INSTALL_HINT_MACOS = (
    f"        brew install astronomer/tap/astro@{ASTRO_PINNED} --without-podman\n"
    f"        docker pull {ASTRO_RUNTIME_IMAGE}\n"
    "      If Homebrew rejects the option, install the binary directly:\n"
    f"        https://github.com/astronomer/astro-cli/releases/tag/v{ASTRO_PINNED}"
)

# Windows. Verified 2026-09-15 against astronomer.io/docs/astro/cli/install-cli,
# docs.docker.com and the winget-pkgs manifest at
# manifests/a/Astronomer/Astro/1.42.1/.
#
# The SCRIPT leads, and winget is demoted to a shortcut, because winget cannot be
# assumed: Microsoft ships it inside App Installer, which arrives via the
# Microsoft Store and may be absent or blocked on a managed laptop. A fallback
# that quietly depends on the thing that is missing is not a fallback.
#
# The script route also needs no administrator rights, which matters more in a
# room of corporate laptops than the package manager does.
#
# `--skip-dependencies` on the winget line is the exact analogue of Homebrew's
# --without-podman: since CLI 1.32.0 both package managers pull Podman in as the
# default engine, and this course is Docker throughout (D-002, D-023).
ASTRO_INSTALL_HINT_WINDOWS = (
    "        powershell -ExecutionPolicy Bypass -File .\\setup-windows.ps1\n"
    "      That installs uv and astro with no package manager and no admin.\n"
    "      By hand instead -- download, rename to astro.exe, add to PATH:\n"
    f"        https://github.com/astronomer/astro-cli/releases/tag/v{ASTRO_PINNED}\n"
    f"        (astro_{ASTRO_PINNED}_windows_amd64.exe / _arm64.exe -- a bare .exe)\n"
    "      Or, IF you have it:\n"
    f"        winget install -e --id Astronomer.Astro -v {ASTRO_PINNED} --skip-dependencies\n"
    f"        docker pull {ASTRO_RUNTIME_IMAGE}\n"
    "      Run astro in PowerShell, NOT in a WSL terminal."
)

ASTRO_INSTALL_HINT_LINUX = (
    f"        curl -sSL install.astronomer.io | sudo bash -s -- v{ASTRO_PINNED}\n"
    f"        docker pull {ASTRO_RUNTIME_IMAGE}"
)


def astro_install_hint(platform: str | None = None) -> str:
    """The install command for the machine this is running on.

    Printed by `roots doctor` when astro is missing or off-pin. A pre-flight tool
    that prints a Homebrew command to a Windows participant has told them nothing
    -- and they are the ones least able to work out the substitution.
    """
    platform = platform if platform is not None else sys.platform
    if platform.startswith("win"):
        return ASTRO_INSTALL_HINT_WINDOWS
    if platform == "darwin":
        return ASTRO_INSTALL_HINT_MACOS
    return ASTRO_INSTALL_HINT_LINUX


def _parse_astro_version(text: str) -> str | None:
    """Pull a semver out of `astro version` output, whatever it wraps it in.

    Tolerant of ANSI escapes, which the Astro CLI is documented to leak into
    Windows terminals (astronomer/astro-cli#635). The digit-matching regex
    survives them anyway; this is stated so the next person does not "fix" it.
    """
    match = re.search(r"(\d+\.\d+\.\d+)", text)
    return match.group(1) if match else None



def _load_env() -> None:
    """Make .env visible to this process. Airflow and Dagster read it themselves."""
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def _repo_root() -> Path:
    """The repo root, WITHOUT requiring config/team.yaml to exist.

    `cfgmod.repo_root()` derives the root from `config/team.yaml`, which is
    correct everywhere the pipeline runs -- but that file does not exist until
    `roots join` has run. Calling it at import time made the whole CLI unusable
    before joining, `roots join` and `roots doctor` included. Found by running
    the generated student repo, which is the only place that state exists.

    So: use the config when it is there, and otherwise walk up looking for the
    two things that identify this repo.
    """
    try:
        return cfgmod.repo_root()
    except Exception:  # noqa: BLE001 - any failure means "not joined yet"
        here = Path.cwd().resolve()
        for candidate in (here, *here.parents):
            if (candidate / "pyproject.toml").is_file() and (candidate / "dags").is_dir():
                return candidate
        return here


def _cfg() -> cfgmod.TeamConfig:
    _load_env()
    try:
        return cfgmod.team_config()
    except cfgmod.ConfigError as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(1)


def _ok(msg: str) -> None:
    typer.secho(f"  PASS  {msg}", fg=typer.colors.GREEN)


def _bad(msg: str) -> None:
    typer.secho(f"  FAIL  {msg}", fg=typer.colors.RED)


def _warn(msg: str) -> None:
    typer.secho(f"  ....  {msg}", fg=typer.colors.YELLOW)


# ---------------------------------------------------------------------- join


def _env_path_value(path) -> str:
    """A filesystem path as it must appear in `.env`: forward slashes, always.

    THIS IS LOAD-BEARING ON WINDOWS. uv's `--env-file` parser rejects a value
    containing backslashes, and when it does it discards the ENTIRE FILE -- not
    the offending line -- emitting one `warning:` and carrying on. So a Windows
    team running

        uv run --env-file .env dg dev --target-path dagster

    would get Dagster with no DAGSTER_HOME (ephemeral instance, materializations
    never persist, so missions D1 and D3 have nothing to observe), no
    ROOTSMARKT_DSN and no SUPPLYHUB_TOKEN. It presents as "Dagster is broken"
    rather than "your configuration was dropped".

    Measured against uv's parser, which is the same on every platform:

        DAGSTER_HOME=C:\\Users\\bao\\repo\\.dagster     whole file discarded
        DAGSTER_HOME="C:\\Users\\bao\\repo\\.dagster"   whole file discarded
        DAGSTER_HOME='C:\\Users\\bao\\repo\\.dagster'   parses
        DAGSTER_HOME=C:/Users/bao/repo/.dagster    parses

    Forward slashes rather than single quotes: Windows accepts them everywhere
    that matters (Python, Dagster, Docker), and it keeps the file free of
    quoting rules that the next person to edit it by hand would have to know.
    """
    if hasattr(path, "as_posix"):          # any pathlib flavour, including PureWindowsPath
        return path.as_posix()
    return str(path).replace("\\", "/")     # a plain string that may already hold separators


def _env_body(cfg: dict, base: str, dsn: str, airflow_dsn: str,
              dagster_home, config_path) -> str:
    """The contents of `.env`. Separated out so it can be tested with Windows
    paths from any platform -- see cli/tests/test_env_file.py."""
    return "\n".join(
        [
            "# Written by `roots join`. Do not commit.",
            "# Paths use forward slashes on every platform -- see _env_path_value().",
            f"LAB_URL={base}",
            f"TEAM_ID={cfg['team_id']}",
            f"SUPPLYHUB_TOKEN={cfg['supplyhub_token']}",
            f"SUPPLYHUB_BASE_URL={cfg.get('supplyhub_base_url', base)}",
            f"ROOTSMARKT_SCHEMA={cfg['warehouse']['schema']}",
            f"ROOTSMARKT_DSN={dsn}",
            "# Pre-provisioned so mission A3 is about dependencies, not credentials.",
            f"AIRFLOW_CONN_ROOTSMARKT_DW={airflow_dsn}",
            "# Dagster needs this to persist its event log. Without it Dagster",
            "# uses a temporary instance, materializations vanish between runs,",
            "# and missions D1 and D3 have nothing to observe.",
            f"DAGSTER_HOME={_env_path_value(dagster_home)}",
            "# Absolute, so subprocesses and subdirectories resolve it. Ignored",
            "# inside the Airflow container, which finds the mounted copy instead.",
            f"ROOTSMARKT_CONFIG={_env_path_value(config_path)}",
            "",
        ]
    )


@app.command()
def join(code: str, lab_url: str = typer.Option("", help="Lab server base URL")):
    """Exchange a join code for this team's configuration. Run once."""
    base = api.lab_url(lab_url or None)
    typer.echo(f"joining via {base} ...")
    try:
        cfg = api.fetch_config(code, base)
    except api.LabRejected as exc:
        typer.secho(f"the lab server refused that join code: {exc}", fg=typer.colors.RED)
        typer.secho("Check the code with your instructor -- they are exact.",
                    fg=typer.colors.YELLOW)
        raise typer.Exit(1)
    except api.LabUnreachable as exc:
        typer.secho(f"could not join: {exc}", fg=typer.colors.RED)
        typer.secho(
            "If the lab server is unreachable, ask the instructor for your "
            "team.yaml (they can run `lab export-configs`).",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(1)

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")

    wh = cfg["warehouse"]
    dsn = wh["dsn"]

    # Dagster stores its event log here. Created now so the first `dg dev` does
    # not warn about a missing dagster.yaml.
    dagster_home = Path(".dagster").resolve()
    dagster_home.mkdir(parents=True, exist_ok=True)
    (dagster_home / "dagster.yaml").touch()
    # The Airflow container cannot reach the host's "localhost". When the
    # warehouse is local, rewrite the host for the container's view only.
    airflow_dsn = dsn.replace("@localhost", "@host.docker.internal").replace(
        "@127.0.0.1", "@host.docker.internal"
    )
    ENV_PATH.write_text(
        _env_body(cfg, base, dsn, airflow_dsn, dagster_home, CONFIG_PATH.resolve()),
        encoding="utf-8",
    )
    _ok(f"joined as {cfg['team_id']}")
    if state.clear_for_team(cfg["team_id"]):
        _warn(
            "local progress belonged to a different team and was cleared. "
            "Milestones you have genuinely earned will be re-detected on the next "
            "`roots verify` or `roots watch`."
        )
    typer.echo(f"  wrote {CONFIG_PATH}, {ENV_PATH} and {dagster_home}/")
    typer.echo(f"  {len(cfg['expected_stores'])} expected stores, "
               f"plausible revenue {cfg['plausible_daily_revenue_eur']}")

    cfgmod.team_config.cache_clear()
    _print_airflow_conn(cfgmod.team_config())
    typer.echo("\nNext: roots doctor")



AIRFLOW_CONN_RECIPE = """\
  Connection Id   : supplyhub
  Connection Type : Generic
  Host            : {base_url}
  Password        : {token}
  Port / Schema   : leave empty"""


def _print_airflow_conn(cfg) -> None:
    typer.echo("\nAirflow Connection for mission A1 (Admin -> Connections -> +):")
    typer.echo(AIRFLOW_CONN_RECIPE.format(base_url=_container_base_url(cfg), token=cfg.supplyhub_token))
    typer.secho(
        "  The Password is the SupplyHub TOKEN above (starts with 'sh_'), "
        "not your join code.",
        fg=typer.colors.YELLOW,
    )


def _container_base_url(cfg) -> str:
    """SupplyHub as the Airflow CONTAINER sees it.

    The container cannot reach the host's loopback, so localhost has to become
    host.docker.internal -- the same rewrite `join` applies to the warehouse DSN.
    """
    return (
        cfg.supplyhub_base_url
        .replace("//localhost", "//host.docker.internal")
        .replace("//127.0.0.1", "//host.docker.internal")
    )


@app.command("airflow-conn")
def airflow_conn():
    """Print the exact Airflow connection values for mission A1."""
    _print_airflow_conn(_cfg())


# --------------------------------------------------------------------- doctor


def _skip(msg: str) -> None:
    typer.secho(f"  SKIP  {msg}", fg=typer.colors.BLUE)


def _run(cmd: list[str], timeout: int = 25) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None


def airflow_project_present() -> bool:
    return Path("Dockerfile").exists() and Path("dags").is_dir()


def dagster_project_present() -> bool:
    return Path("dagster").is_dir()


@app.command()
def doctor(report: bool = typer.Option(False, help="Terse output for the instructor")):
    """Pre-flight. Run this DAYS before the course, not on the morning.

    Checks for the Airflow and Dagster runtimes are skipped when those projects
    are not in the repo. A pre-flight tool that reports failures you cannot act
    on is a pre-flight tool people learn to ignore.
    """
    _load_env()
    failures: list[str] = []

    def fail(msg: str) -> None:
        failures.append(msg)
        _bad(msg)

    def section(name: str, fn) -> None:
        """Run one section. A section that explodes degrades to a FAIL line
        rather than aborting the whole run."""
        typer.echo(name)
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - doctor must always finish
            fail(f"check itself failed: {type(exc).__name__}: {str(exc)[:120]}")

    # ---------------------------------------------------------------- config

    def _config() -> None:
        if not CONFIG_PATH.exists():
            fail(f"{CONFIG_PATH} missing -- run `roots join <code>`")
            return
        try:
            _ok(f"{CONFIG_PATH} -> {cfgmod.team_config().team_id}")
        except cfgmod.ConfigError as exc:
            fail(f"{CONFIG_PATH}: {exc}")

    section("config", _config)

    # ------------------------------------------------------------ environment

    def _environment() -> None:
        """Catch the two things that break silently rather than loudly."""
        if not ENV_PATH.exists():
            # A warning, not a failure: `config` above already reports the one
            # actionable cause (`roots join` has not run), and the warehouse
            # check reports the consequence. Counting it a third time teaches
            # people that the failure count is noise.
            _warn(f"{ENV_PATH} missing -- run `roots join <code>`")
            return

        # THE Windows failure. uv's --env-file parser rejects a value containing
        # a backslash and discards the WHOLE FILE, warning once and carrying on.
        # Dagster then runs with no DAGSTER_HOME, no DSN and no token, which
        # presents as "Dagster is broken" from four layers away.
        offenders = [
            line.split("=", 1)[0]
            for line in ENV_PATH.read_text(encoding="utf-8").splitlines()
            if "=" in line and not line.lstrip().startswith("#") and "\\" in line.split("=", 1)[1]
        ]
        if offenders:
            fail(
                f"{ENV_PATH} has backslashes in: {', '.join(offenders)}. "
                "uv discards the ENTIRE env file when it sees one, so Dagster would "
                "start with no configuration at all. Re-run `roots join <code>`, which "
                "now writes forward slashes."
            )
        else:
            _ok(f"{ENV_PATH} parses (no backslashes in values)")
            _env_file_actually_loads(fail)

        root = _repo_root()
        text = str(root)
        if text.startswith("\\\\") or text.startswith("//"):
            _warn(
                f"the repo is on a network path ({text[:40]}...). Docker Desktop file "
                "sharing is unreliable there -- move it to a local disk"
            )
        elif len(text) > 150:
            _warn(
                f"the repo path is {len(text)} characters deep. Windows has a 260-character "
                "limit that `astro dev start` can hit when it mounts the project -- "
                "consider moving the repo nearer the drive root"
            )
        else:
            _ok(f"repo path is usable ({len(text)} chars)")

    section("environment", _environment)

    # ---------------------------------------------------------------- docker

    def _docker() -> None:
        out = _run(["docker", "info", "--format", "{{.ServerVersion}}"])
        if out is None:
            fail("docker not found -- required for the Airflow half")
        elif out.returncode == 0:
            _ok(f"docker daemon {out.stdout.strip()}")
        else:
            fail("docker is installed but the daemon is not running")

    section("docker", _docker)

    # ------------------------------------------------------------------ astro

    def _astro() -> None:
        if not airflow_project_present():
            _skip("Airflow project not in this repo yet (Dockerfile + dags/) -- Layer 2")
            return
        out = _run(["astro", "version"], timeout=20)
        if out is None:
            fail(f"astro not found. Install it pinned, without Podman:\n{astro_install_hint()}")
            return

        reported = out.stdout.strip().splitlines()[0] if out.stdout.strip() else "unknown"
        found = _parse_astro_version(reported)
        if found is None:
            _warn(f"astro present but could not read its version from {reported!r}")
        elif found.startswith(ASTRO_PINNED_MINOR):
            _ok(f"astro {found} (pinned {ASTRO_PINNED})")
        else:
            # A warning, not a failure. The Runtime IMAGE tag is what determines
            # Airflow's behaviour and that is checked exactly below; hard-failing
            # over a CLI patch difference minutes before the course starts costs
            # more than it protects. But drift must still be visible.
            _warn(
                f"astro {found} is off-pin (course pins {ASTRO_PINNED}). "
                f"Usually harmless, but if Airflow misbehaves, install the pinned CLI:\n"
                f"{astro_install_hint()}"
            )

        img = _run(["docker", "images", "-q", ASTRO_RUNTIME_IMAGE])
        if img is None:
            _warn("could not check for the Astro Runtime image")
        elif img.stdout.strip():
            _ok(f"{ASTRO_RUNTIME_IMAGE} already pulled")
        else:
            fail(
                f"Astro Runtime 3.3-2 NOT pulled. Do it now, not on the day:\n"
                f"        docker pull {ASTRO_RUNTIME_IMAGE}"
            )

    section("airflow runtime", _astro)

    # ---------------------------------------------------------------- dagster

    def _dagster() -> None:
        if not dagster_project_present():
            _skip("Dagster project not in this repo yet (dagster/) -- Layer 2")
            return
        try:
            import dagster  # noqa: PLC0415

            _ok(f"dagster {dagster.__version__} importable")
        except ImportError:
            fail("dagster not installed -- run `uv sync` at the repo root")
            return
        out = _run(["dg", "--version"], timeout=20)
        if out is None:
            fail("`dg` not on PATH -- run `uv sync` at the repo root")
        else:
            _ok(f"dg {out.stdout.strip().splitlines()[0] if out.stdout.strip() else 'present'}")

        # The launch path itself, not just the binary. The dg project lives in
        # dagster/ because the repo root is the Astro project (D-023), so
        # `dg dev` needs --target-path -- a detail worth catching in pre-flight
        # rather than at minute 20 of the Dagster half.
        if not (Path("dagster") / "pyproject.toml").exists():
            fail("dagster/pyproject.toml missing -- the dg project is not there")
        elif "[tool.dg]" not in (Path("dagster") / "pyproject.toml").read_text(encoding="utf-8"):
            fail("dagster/pyproject.toml has no [tool.dg] section -- `dg dev` will refuse")
        else:
            _ok("launch: uv run --env-file .env dg dev --target-path dagster")

        if not os.environ.get("DAGSTER_HOME"):
            fail(
                "DAGSTER_HOME not set -- run `roots join`. Without it Dagster uses a "
                "temporary instance and materializations do not persist"
            )
        else:
            _ok(f"DAGSTER_HOME={os.environ['DAGSTER_HOME']}")

    section("dagster runtime", _dagster)

    # ------------------------------------------------------------------ ports

    def _ports() -> None:
        for port, what in PORTS.items():
            # Only complain about a UI port when that project actually exists.
            required = (port == 8080 and airflow_project_present()) or (
                port == 3000 and dagster_project_present()
            )
            with socket.socket() as s:
                s.settimeout(0.4)
                busy = s.connect_ex(("127.0.0.1", port)) == 0
            if busy and required:
                fail(f"port {port} ({what}) is already in use")
            elif busy:
                _warn(f"port {port} ({what}) in use -- fine if that is the lab itself")
            else:
                _ok(f"port {port} free ({what})")

    section("ports", _ports)

    # ------------------------------------------------------------- lab server

    def _lab() -> None:
        base = api.lab_url()

        # SupplyHub is a SEPARATE address from the progress API, and the two fail
        # independently. `join` takes SUPPLYHUB_BASE_URL from what the lab server
        # advertised about itself (LAB_PUBLIC_URL), not from --lab-url. If the
        # instructor has not run `lab host`, the lab advertises "localhost" --
        # which on this machine means THIS machine -- and `roots join` still
        # succeeds. Mission A1 is then the first thing to notice, twenty minutes
        # later and several layers from the cause.
        supplyhub = os.environ.get("SUPPLYHUB_BASE_URL", "")
        if supplyhub:
            if _is_loopback(supplyhub) and not _is_loopback(base):
                fail(
                    f"SUPPLYHUB_BASE_URL is {supplyhub} but the lab is at {base}. "
                    "You were handed a local address by a remote lab, so SupplyHub "
                    "would resolve to your own machine and mission A1 would fail. "
                    "Ask your instructor to run `lab host`, then `roots join` again."
                )
            elif api.healthy(supplyhub):
                _ok(f"SupplyHub at {supplyhub} reachable")
            else:
                fail(f"SupplyHub at {supplyhub} is unreachable -- A1 cannot fetch a delivery")

        if api.healthy(base):
            _ok(f"{base} reachable")
        else:
            _warn(
                f"{base} unreachable -- you can still work. Ask your instructor for "
                "the lab address, then: roots online --lab-url http://<address>:8090"
            )

    section("lab server", _lab)

    # -------------------------------------------------------------- warehouse

    def _warehouse() -> None:
        dsn = os.environ.get("ROOTSMARKT_DSN")
        if not dsn:
            fail("ROOTSMARKT_DSN not set -- run `roots join`")
            return
        try:
            from rootsmarkt_processing import load  # noqa: PLC0415

            load.read_daily_revenue("1970-01-01", schema=cfgmod.team_config().schema)
            _ok("warehouse reachable and the table exists")
        except Exception as exc:  # noqa: BLE001
            fail(f"warehouse unreachable: {str(exc)[:120]}")
            for line in _warehouse_diagnosis(dsn, exc):
                typer.echo(f"        {line}")

    section("warehouse", _warehouse)

    typer.echo("")
    if failures:
        typer.secho(f"DOCTOR: {len(failures)} problem(s)", fg=typer.colors.RED, bold=True)
        raise typer.Exit(1)
    typer.secho("DOCTOR: all clear", fg=typer.colors.GREEN, bold=True)


# --------------------------------------------------------------------- verify


def _resync(cfg, base: str, quiet: bool = False) -> None:
    """Forget any milestone the lab server no longer has, so it is sent again.

    `lab reset` is a documented, recommended instructor tool -- and every reset
    used to strand the affected teams permanently: earned locally, marked
    reported, gone from the server, and skipped by the loop below forever
    (D-062).

    Deliberately NOT fatal, and deliberately does not prune when the server
    cannot be reached. Treating "no answer" as "the server has nothing" would
    make every offline team re-queue everything -- the same bug inverted, and
    louder.
    """
    try:
        server = api.team_state(base, cfg.team_id)
    except Exception:  # noqa: BLE001 - unreachable, refused, or malformed: all "do not prune"
        return
    known = set(server.get("milestones") or {})
    forgotten = state.forget_reported(set(state.reported()) - known)
    if forgotten and not quiet:
        _warn(
            f"re-reporting {len(forgotten)} milestone(s) the lab server no longer has "
            f"({', '.join(sorted(forgotten))}) -- it was probably reset"
        )
    _resend_submissions(cfg, base, known, quiet)


def _resend_submissions(cfg, base: str, known: set, quiet: bool) -> None:
    """Replay evidence the server has forgotten.

    Client checks re-derive themselves on the next pass; submissions cannot,
    because a human typed the evidence. A replay can legitimately be refused --
    `lab reset team` also resets publications, so the world may have moved -- and
    that path says so rather than queueing forever.
    """
    for name, payload in state.submissions().items():
        if name in known:
            continue
        try:
            resp = api.post_milestone(base, cfg.team_id, cfg.supplyhub_token, name, payload)
        except api.LabRejected as exc:
            if not quiet:
                _warn(f"{name} could not be re-submitted: {exc}. Run `roots submit` again.")
            continue
        except api.LabUnreachable:
            return
        if resp.get("accepted") and not quiet:
            _ok(f"re-submitted {name}")


def _report(cfg, results: list[tuple[checks.Check, checks.CheckResult]], quiet=False) -> int:
    sent = 0
    base = api.lab_url()
    if not quiet and _solutions_dir().is_dir():
        # The instructor repo reports like any other client, and its runs land on
        # a real team's dashboard. That is useful -- rehearsing end to end has
        # caught real bugs -- but it has to be visible. It was not, and an
        # instructor's solution runs turned up on a participant's board looking
        # like milestones they had not earned.
        _warn(f"instructor repo: these milestones are landing on {cfg.team_id}'s dashboard")
    _resync(cfg, base, quiet)
    for check, result in results:
        if not quiet:
            # Collapse multi-line driver errors: psycopg and the astro CLI both
            # emit walls of text, and a check report has to stay scannable.
            detail = " ".join((result.detail or result.status).split())
            if len(detail) > 160:
                detail = detail[:157] + "..."
            {checks.PASS: _ok, checks.FAIL: _bad}.get(result.status, _warn)(
                f"{check.mission} {check.milestone}: {detail}"
            )
        if not result.ok:
            continue
        state.record(check.milestone, result.detail)
        # Report until the SERVER has acknowledged it, not merely until it is
        # earned locally. Those diverge -- a milestone earned against a different
        # lab server, or while this one was unreachable, would otherwise never be
        # sent again and would stay missing from the dashboard.
        if check.milestone in state.reported():
            continue
        try:
            api.post_milestone(base, cfg.team_id, cfg.supplyhub_token, check.milestone, {})
            state.mark_reported(check.milestone)
            sent += 1
        except api.LabRejected as exc:
            # NOT queued: the server answered and refused, so retrying forever
            # cannot help. This used to be reported as "unreachable" while the
            # server was up -- a misdiagnosis that sent people to the network.
            if not quiet:
                _warn(f"{check.milestone} refused by the lab server: {exc}")
        except api.LabUnreachable:
            state.enqueue(check.milestone, {})
            if not quiet:
                _warn(f"queued {check.milestone} -- lab server unreachable")
    return sent


def _flush(cfg) -> int:
    base = api.lab_url()
    flushed = 0
    for item in state.drain_queue():
        try:
            api.post_milestone(
                base, cfg.team_id, cfg.supplyhub_token, item["milestone"], item["payload"]
            )
            flushed += 1
        except api.LabRejected:
            continue                      # dropped: requeuing could never succeed
        except api.LabUnreachable:
            state.enqueue(item["milestone"], item["payload"])
            break
    return flushed


@app.command()
def verify(
    missions: list[str] = typer.Argument(
        None, help="e.g. a1, or 'd1 d2' for several -- omit for all"
    ),
):
    """Run checks now and report the result.

    Accepts several missions: `roots verify d1 d2`. Taking only one was a
    needless papercut -- reaching for both halves of a block at once is the
    obvious thing to do.
    """
    cfg = _cfg()
    if missions:
        todo = [c for m in missions for c in checks.for_mission(m)]
        unknown = [m for m in missions if not checks.for_mission(m)]
        if unknown:
            known = sorted({c.mission for c in checks.CHECKS})
            typer.secho(
                f"no checks for mission(s) {', '.join(unknown)}. Known: {', '.join(known)}",
                fg=typer.colors.RED,
            )
            raise typer.Exit(1)
    else:
        todo = checks.CHECKS
    _flush(cfg)
    results = [(c, c.run(cfg)) for c in todo]
    sent = _report(cfg, results)
    passed = sum(1 for _, r in results if r.ok)
    typer.echo("")
    typer.echo(f"{passed}/{len(results)} passing, {sent} newly reported")
    typer.echo(f"local state: {state.PROGRESS}")


@app.command()
def watch(interval: int = typer.Option(20, min=5, help="Seconds between passes")):
    """Re-run checks on a loop and report changes. Start this once, then forget it.

    Teams do not remember to run verify, and a dashboard that lags reality is
    worse than none -- it sends the instructor to the wrong table.
    """
    cfg = _cfg()
    typer.echo(f"watching every {interval}s as {cfg.team_id} (Ctrl-C to stop)")
    while True:
        try:
            _flush(cfg)
            before = state.earned()
            results = [(c, c.run(cfg)) for c in checks.CHECKS]
            _report(cfg, results, quiet=True)
            for name in sorted(state.earned() - before):
                typer.secho(f"  + {name}", fg=typer.colors.GREEN)
        except KeyboardInterrupt:  # pragma: no cover
            raise
        except Exception as exc:  # noqa: BLE001 - the daemon must not die
            typer.secho(f"  check pass failed: {str(exc)[:140]}", fg=typer.colors.YELLOW)
        time.sleep(interval)


@app.command()
def status():
    """What this machine thinks you have earned."""
    data = state.progress()
    if not data["earned"]:
        typer.echo("nothing earned yet locally")
        return
    for name, meta in sorted(data["earned"].items(), key=lambda kv: kv[1]["at"]):
        typer.echo(f"  {name:26} {meta.get('detail', '')}")
    pending = len(state.drain_queue())
    if pending:
        typer.secho(f"  ({pending} queued for the lab server)", fg=typer.colors.YELLOW)


# --------------------------------------------------------------------- submit


submit_app = typer.Typer(help="Evidence submissions (A2, D3, D4)")
app.add_typer(submit_app, name="submit")


def _send(cfg, name: str, payload: dict) -> None:
    try:
        resp = api.post_milestone(
            api.lab_url(), cfg.team_id, cfg.supplyhub_token, name, payload
        )
    except api.LabRejected as exc:
        typer.secho(f"the lab server refused it: {exc}", fg=typer.colors.RED)
        return
    except api.LabUnreachable as exc:
        state.enqueue(name, payload)
        typer.secho(f"queued -- {exc}", fg=typer.colors.YELLOW)
        return
    if resp.get("accepted"):
        state.record(name, "submitted")
        # Kept so a server reset does not destroy evidence a human typed once.
        state.remember_submission(name, payload)
        _ok(f"{name} accepted")
        for key, value in resp.items():
            if key not in ("accepted", "milestone", "newly_earned"):
                typer.echo(f"  {key}: {value}")
    else:
        _bad(f"{name} not accepted: {resp.get('reason')}")


@submit_app.command("a2")
def submit_a2(
    received_date: str = typer.Option(..., help="business_date you were actually served"),
    expected_date: str = typer.Option(..., help="business_date you asked for"),
    delivery_id: str = typer.Option(..., help="delivery_id you were served"),
    row_count: int = typer.Option(..., help="row_count of that delivery"),
):
    """A2 evidence. Graded on the DIAGNOSIS -- your chosen response is not graded."""
    _send(_cfg(), "stale_delivery_detected", {
        "received_date": received_date, "expected_date": expected_date,
        "delivery_id": delivery_id, "row_count": row_count,
    })


def _asset_list(values: list[str] | None) -> list[str]:
    """Accept every reasonable spelling of a list of assets.

    Repeated flags, a comma list, or a quoted list with spaces all work:

        --rebuilt clean_sales --rebuilt daily_revenue
        --rebuilt clean_sales,daily_revenue
        --rebuilt "clean_sales, daily_revenue"

    Taking a single comma-joined string used to mean that an unquoted
    `--rebuilt a, b` was split by the SHELL, and the second asset arrived as a
    stray positional argument -- a confusing failure for something typed once,
    under time pressure, in a room of twenty people.
    """
    out: list[str] = []
    for value in values or []:
        out.extend(part.strip() for part in value.split(",") if part.strip())
    return out


@submit_app.command("d3")
def submit_d3(
    rebuilt: list[str] = typer.Option(
        ..., help="Assets you rebuilt. Repeat the flag, or comma-separate."
    ),
    not_rebuilt: list[str] = typer.Option(
        ..., help="Assets you deliberately did NOT rebuild. Repeat the flag, or comma-separate."
    ),
    why: str = typer.Option(..., help="Why that boundary is correct (40+ characters)"),
):
    """D3 justification: what you rebuilt, what you did not, and why."""
    _send(_cfg(), "rebuild_justified", {
        "rebuilt": _asset_list(rebuilt),
        "not_rebuilt": _asset_list(not_rebuilt),
        "why": why,
    })


@submit_app.command("d4")
def submit_d4(
    q1: str = typer.Option(..., help="Your three 30-second answers, collated"),
    q2: str = typer.Option(..., help="Which is faster for a new colleague to understand?"),
    q3: str = typer.Option(..., help="What was genuinely WORSE in Dagster?"),
    q4: str = typer.Option(..., help="Which model fits a non-dataset pipeline?"),
):
    """D4 comparison. q3 must be a real answer."""
    _send(_cfg(), "comparison_submitted", {"answers": {"q1": q1, "q2": q2, "q3": q3, "q4": q4}})


# -------------------------------------------------------------------- offline
#
# `roots offline` is INSTRUCTOR-ONLY and is registered only where `lab/` exists.
#
# It starts the lab from `lab/docker-compose.yml`, and `lab/` is deliberately
# excluded from the participant repository (D-047) -- it holds the admin token,
# the A2 quirk and the generator that can compute every team's data. So in the
# student repo the command could never have worked; it would have failed with
# "lab/docker-compose.yml not found" at the exact moment a team needed it most.
#
# The participant-facing fallback is `roots online --lab-url <instructor's IP>`
# against a lab the instructor hosts from their own machine (D-052). That is a
# real reduction in resilience compared with what D-022 originally promised, and
# it is written down rather than papered over.


def _lab_project_present() -> bool:
    return (_repo_root() / "lab" / "docker-compose.yml").is_file()


if _lab_project_present():

    @app.command()
    def offline(compose_file: Path = typer.Option(Path("lab/docker-compose.yml"))):
        """INSTRUCTOR: start the lab locally (SupplyHub mock + Postgres).

        Because delivery data is a pure function of (team_id, business_date),
        this serves byte-identical data to the hosted lab -- every file already
        on disk and every figure already loaded stays valid.

        Lost: the dashboard, and the story gating (this publishes everything at
        once). The game degrades; the learning does not.

        Teams point at it with `roots online --lab-url http://<your-ip>:8090`.
        """
        _offline_impl(compose_file)


def _offline_impl(compose_file: Path) -> None:
    if not compose_file.exists():
        typer.secho(f"{compose_file} not found", fg=typer.colors.RED)
        raise typer.Exit(1)
    typer.echo("starting local lab ...")
    rc = subprocess.run(
        ["docker", "compose", "-f", str(compose_file), "up", "-d"],
        env={**os.environ, "LAB_PORT": "8090", "POSTGRES_PORT": "5433",
             "LAB_ADMIN_TOKEN": os.environ.get("LAB_ADMIN_TOKEN", "offline-local")},
    ).returncode
    if rc != 0:
        typer.secho("docker compose failed -- is Docker running?", fg=typer.colors.RED)
        raise typer.Exit(1)

    _rewrite_env({"SUPPLYHUB_BASE_URL": "http://localhost:8090",
                  "LAB_URL": "http://localhost:8090"})
    _ok("local lab started: SupplyHub and the lab server are now local")
    typer.echo("  Provision teams with `lab provision`, then tell the room:")
    typer.echo("    roots online --lab-url http://<your-ip>:8090")


@app.command()
def reset(yes: bool = typer.Option(False, "--yes", help="Skip the confirmation.")):
    """Forget every milestone this machine has earned.

    The client half of `lab reset`. Instructors reset the lab between sessions,
    and since milestones re-sync (D-062) that alone no longer clears a board --
    any machine still holding local state re-reports into the fresh lab within
    one `roots watch` cycle. Run this on every machine being reused.

    Hints you have read and checkpoints you have adopted survive: those are facts
    about this laptop, not about whoever it reports as.
    """
    data = state.progress()
    if not data["earned"]:
        _ok("nothing to clear -- no milestones recorded on this machine")
        return
    typer.echo(f"This will forget {len(data['earned'])} milestone(s) on THIS machine:")
    for name in sorted(data["earned"]):
        typer.echo(f"    {name}")
    typer.echo("\nThe lab server keeps whatever it already has -- this only clears here.")
    if not yes and not typer.confirm("Continue?"):
        typer.echo("nothing changed")
        raise typer.Exit(0)
    dropped = state.clear_progress()
    _ok(f"cleared {dropped['earned']} milestone(s) and {dropped['submissions']} submission(s)")


@app.command()
def online(lab_url: str = typer.Option(..., help="The lab URL to point at")):
    """Point at a different lab server.

    This is the fallback when the hosted lab is unreachable: the instructor runs
    the lab on their own machine and gives the room its address, which may be a
    phone hotspot or an ad-hoc LAN rather than the venue network.
    """
    base = lab_url.rstrip("/")
    _rewrite_env({"SUPPLYHUB_BASE_URL": base, "LAB_URL": base})
    _ok(f"now pointing at {base}")
    typer.echo("  Re-run `roots doctor` to confirm it is reachable.")


# ------------------------------------------------------- hints and checkpoints
#
# Both are self-service (D-013). One instructor cannot deliver hints to ten teams
# during A2 and A4, which is exactly when several teams need one at once -- and a
# team with no way to skip forward stops learning at minute 60 and loses the rest
# of the course. That is the worst failure mode this design has.

MISSIONS = ("a1", "a2", "a3", "a4", "a5", "d1", "d2", "d3", "d4")


def _mission_arg(mission: str) -> str:
    key = mission.strip().lower()
    if key not in MISSIONS:
        _bad(f"unknown mission {mission!r}. One of: {', '.join(MISSIONS)}")
        raise typer.Exit(2)
    return key


@app.command()
def hint(mission: str = typer.Argument(..., help="a1..a5, d1..d4")):
    """Reveal the next hint for a mission. Run it again for the one after."""
    key = _mission_arg(mission)
    root = _repo_root() / "hints" / key
    available = sorted(root.glob("*.md")) if root.is_dir() else []
    if not available:
        _bad(f"no hints found for {key.upper()} (looked in {root})")
        raise typer.Exit(1)

    already = state.hints_shown(key)
    index = min(already, len(available) - 1) if already >= len(available) else already

    typer.echo("")
    typer.echo(available[index].read_text(encoding="utf-8").strip())
    typer.echo("")

    remaining = len(available) - (index + 1)
    if remaining > 0:
        typer.echo(f"  {remaining} more hint(s) for {key.upper()}: run `roots hint {key}` again.")
    else:
        typer.echo(f"  That was the last hint for {key.upper()}.")
        typer.echo(f"  Still stuck? `roots checkpoint {key}` takes a known-good state.")
    state.record_hint(key, index + 1)


@app.command()
def checkpoint(
    mission: str = typer.Argument(..., help="a1..a4, d1..d3"),
    yes: bool = typer.Option(False, "--yes", help="Skip the confirmation."),
):
    """Adopt the known-good state for a mission so you can keep moving.

    Your current files are backed up first -- nothing is destroyed. Adoption is
    recorded and shown on the dashboard, so the instructor can see who needed it.
    That is the whole of the cost: it is visible, not punished.
    """
    key = _mission_arg(mission)
    src = _repo_root() / "checkpoints" / key
    if not src.is_dir():
        _bad(f"no checkpoint for {key.upper()} (looked in {src})")
        raise typer.Exit(1)

    files = sorted(f for f in src.rglob("*") if f.is_file())
    if not files:
        _bad(f"checkpoint {key} is empty")
        raise typer.Exit(1)

    typer.echo(f"\nAdopting the {key.upper()} checkpoint will overwrite:")
    for f in files:
        typer.echo(f"    {f.relative_to(src)}")
    if not yes and not typer.confirm("\nYour current versions are backed up first. Continue?"):
        typer.echo("nothing changed")
        raise typer.Exit(0)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = Path(".roots") / "backup" / f"{key}-{stamp}"
    root = _repo_root()

    saved = 0
    for f in files:
        rel = f.relative_to(src)
        current = root / rel
        if current.exists():
            dest = backup / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(current.read_bytes())
            saved += 1
        current.parent.mkdir(parents=True, exist_ok=True)
        current.write_bytes(f.read_bytes())

    _ok(f"adopted checkpoint {key.upper()} ({len(files)} file(s))")
    if saved:
        typer.echo(f"  your previous versions: {backup}")

    state.record_adoption(key, str(backup))
    # Recorded locally above, whatever happens next. A team adopting a checkpoint
    # is already having a bad time; a dashboard outage must not make it worse.
    # `_cfg()` is deliberately NOT used here -- it prints in red and exits.
    _load_env()
    try:
        cfg = cfgmod.team_config()
    except cfgmod.ConfigError:
        typer.echo("  (not reported: you have not joined a team yet)")
    else:
        try:
            api.post_adoption(api.lab_url(), cfg.team_id, cfg.supplyhub_token, key)
            typer.echo("  reported to the lab server")
        except api.LabRejected as exc:
            typer.echo(f"  (not reported: the lab server refused it -- {exc})")
        except api.LabUnreachable:
            typer.echo("  (not reported: lab server unreachable -- this is fine)")

    typer.echo("\n  Restart the affected runtime so it picks the new files up:")
    typer.echo("    Airflow  -- it reloads dags/ on its own, give it ~30s")
    typer.echo("    Dagster  -- reload the code location in the UI, or restart dg dev")


# `roots solution` is INSTRUCTOR-ONLY and is registered only where solutions/
# exists -- which is this repository, not the one participants clone. Gating on
# the directory rather than stripping the code keeps one source of truth and
# makes the absence self-explanatory.
def _solutions_dir() -> Path:
    return _repo_root() / "solutions"


if _solutions_dir().is_dir():

    SOLUTION_TARGETS = {
        "airflow": ("solutions/airflow", "dags"),
        "dagster": ("solutions/dagster", "dagster/src/rootsmarkt/defs"),
    }

    @app.command()
    def solution(
        target: str = typer.Argument(..., help="airflow | dagster | all"),
        restore: bool = typer.Option(False, "--restore", help="Put the stubs back."),
    ):
        """INSTRUCTOR: load the reference implementation into the live project.

        Rehearsing the course means running the real pipeline, and hand-copying
        files between solutions/ and dags/ is how a demo goes wrong live.

        Backs the stubs up first, and `--restore` puts them back. The earlier
        version said "undo with git checkout" -- which silently does nothing when
        the stubs are not yet committed, and that destroyed them once already.
        Advice that depends on the user's VCS state is not an undo.
        """
        root = _repo_root()
        names = list(SOLUTION_TARGETS) if target == "all" else [target]
        for name in names:
            if name not in SOLUTION_TARGETS:
                _bad(f"unknown target {name!r}. One of: airflow, dagster, all")
                raise typer.Exit(2)

        backup_root = root / ".roots" / "stubs"

        if restore:
            if not backup_root.is_dir():
                _bad(f"nothing to restore: no backup at {backup_root}")
                raise typer.Exit(1)
            restored = 0
            for saved in sorted(backup_root.rglob("*.py")):
                target_path = root / saved.relative_to(backup_root)
                target_path.parent.mkdir(parents=True, exist_ok=True)
                target_path.write_bytes(saved.read_bytes())
                restored += 1
            _ok(f"restored {restored} stub file(s)")
            return

        for name in names:
            src_rel, dst_rel = SOLUTION_TARGETS[name]
            src, dst = root / src_rel, root / dst_rel
            dst.mkdir(parents=True, exist_ok=True)
            copied = []
            for f in sorted(src.glob("*.py")):
                current = dst / f.name
                # Back up anything that is not already this exact solution.
                # Byte comparison rather than a marker: the Dagster stub is all
                # comments and carries no `NotImplementedError` to look for, so a
                # marker test would have silently skipped backing it up -- the
                # same class of bug as the git-checkout advice this replaced.
                if current.exists() and current.read_bytes() != f.read_bytes():
                    saved = backup_root / dst_rel / f.name
                    saved.parent.mkdir(parents=True, exist_ok=True)
                    saved.write_bytes(current.read_bytes())
                current.write_bytes(f.read_bytes())
                copied.append(f.name)
            _ok(f"{name}: copied {', '.join(copied)} -> {dst_rel}/")

        typer.echo(f"\n  Stubs backed up to {backup_root.relative_to(root)}")
        typer.echo("  Undo with: roots solution all --restore")


def _env_file_actually_loads(fail) -> None:
    """Run the exact mechanism `dg dev` depends on, and see if it works.

    The backslash check above catches the cause we know about (D-051). This
    catches the SYMPTOM whatever the cause -- a quoting rule we have not met, a
    uv change, a half-written file -- by asking uv to load the file and report
    back one value.

    Worth a second of pre-flight because the failure is silent and total: uv
    discards the entire env file, prints one `warning:` line, and exits 0. What
    a participant then sees is Dagster losing its event log and the warehouse
    refusing connections, with nothing anywhere naming the env file.
    """
    if "TEAM_ID" not in ENV_PATH.read_text(encoding="utf-8"):
        return                                   # nothing to look for yet
    probe = "import os;print('TEAM_ID=' + (os.environ.get('TEAM_ID') or 'MISSING'))"
    out = _run(
        ["uv", "run", "--no-project", "--env-file", str(ENV_PATH), sys.executable, "-c", probe],
        timeout=60,
    )
    if out is None:
        _warn("could not test `uv run --env-file` (uv not found?) -- skipped")
        return
    if "TEAM_ID=MISSING" in out.stdout or "Failed to parse environment file" in out.stderr:
        fail(
            "`uv run --env-file .env` does NOT load your configuration. Everything "
            "run that way -- Dagster especially -- would start with nothing set. "
            "Re-run `roots join <code>`; if it persists, show your instructor "
            f"this: {out.stderr.strip().splitlines()[-1] if out.stderr.strip() else 'no stderr'}"
        )
    elif "TEAM_ID=" in out.stdout:
        _ok("`uv run --env-file .env` loads your configuration")
    else:
        _warn("could not confirm `uv run --env-file` behaviour -- check manually")


def _is_loopback(url_or_host: str) -> bool:
    """Whether an address points back at the machine asking.

    Used to catch a remote lab handing out a local address -- the one
    misconfiguration that leaves `roots join` reporting success and everything
    after it failing.
    """
    from urllib.parse import urlsplit

    host = urlsplit(url_or_host).hostname if "//" in url_or_host else url_or_host
    if not host:
        return False
    if host in {"localhost", "0.0.0.0", "::1"}:
        return True
    return host.startswith("127.")


def _warehouse_diagnosis(dsn: str, exc: Exception) -> list[str]:
    """Explain an auth failure that is really a port collision.

    `astro dev start` publishes its own metadata Postgres on host port 5432 --
    the same port the lab warehouse uses. When both run on one machine the
    warehouse DSN reaches Airflow's database instead, which has no `team_NN`
    role, and psycopg reports `password authentication failed`.

    That message sends you to look at credentials, which are fine. Worth the
    extra lines: the wrong-database case is indistinguishable from a wrong
    password unless something says so out loud.
    """
    message = str(exc)
    if "password authentication failed" not in message:
        return []

    port = 5432
    match = re.search(r":(\d+)/", dsn)
    if match:
        port = int(match.group(1))

    lines = [
        "The credentials in config/team.yaml are almost certainly correct.",
        f"Check that port {port} is the warehouse and not something else:",
    ]
    if port == 5432:
        lines += [
            "  `astro dev start` publishes ITS OWN Postgres on 5432. If it is",
            "  running, your DSN is reaching Airflow's metadata database, which",
            "  has no team role -- hence 'authentication failed'.",
            "  Fix: docker ps --format '{{.Names}}\\t{{.Ports}}' | grep 5432",
            "  Then point ROOTSMARKT_DSN at the warehouse's actual host port.",
        ]
    else:
        lines.append(f"  docker ps --format '{{{{.Names}}}}\\t{{{{.Ports}}}}' | grep {port}")
    return lines


def _rewrite_env(updates: dict[str, str]) -> None:
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    keys = set(updates)
    kept = [line for line in lines if line.split("=", 1)[0].strip() not in keys]
    kept += [f"{k}={v}" for k, v in updates.items()]
    ENV_PATH.write_text("\n".join(kept) + "\n", encoding="utf-8")


def main() -> None:
    _force_utf8_output()
    app()


def _force_utf8_output() -> None:
    """Make stdout/stderr able to carry the characters the course text uses.

    The mission briefs and hints contain em dashes, EUR signs and arrows. On a
    Windows console the interactive stream handles them, but a REDIRECTED one
    (`roots status > out.txt`, or any CI capture) falls back to the ANSI code
    page -- cp1252 in Belgium -- and printing an arrow raises UnicodeEncodeError
    from inside a command that had otherwise succeeded.

    `errors="replace"` rather than "strict" on purpose: a lost glyph is a
    cosmetic problem, and a pre-flight tool that dies while reporting is not.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:       # already wrapped, or not a real stream
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # detached or non-reconfigurable stream
            pass


if __name__ == "__main__":
    main()
