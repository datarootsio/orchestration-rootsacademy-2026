"""Airflow milestone probes.

All three read HISTORY rather than executing anything: the team has already run
the pipeline -- that is the mission -- so the evidence is in Airflow's own
records. Nothing is triggered, nothing is mutated (decision D-024).

CLI contract VERIFIED against Astro Runtime 3.3-2 on 2026-09-14. The first draft
was written from assumption and got three things wrong:

  - `dags list-runs` takes dag_id as a POSITIONAL argument, not `-d`.
  - Runs carry `run_id`, not `dag_run_id`, and **no `conf` field at all**, so
    the business_date a run used cannot be recovered from the CLI.
  - `airflow assets` offers only list/details/materialize -- there is no
    `list-events`, so asset events are not readable this way.
"""

from __future__ import annotations

import json
import re
import subprocess

DAG_ID = "rootsmarkt_delivery"
CONSUMER_DAG_ID = "rootsmarkt_finance_report"
ASSET_NAME = "rootsmarkt_daily_revenue"

# Task ids the reference solution uses. Teams may rename, so these are hints for
# identifying the ROLE of a task; the verdict always comes from run outcomes.
VALIDATION_HINTS = ("validate", "check", "assert", "verify")
GATE_HINTS = ("publish", "plausib", "gate", "semantic")
# Mission A2's staleness check also contains "assert", but it guards the SOURCE,
# not the loaded data product. Counting it as A3's validation step would let a
# team earn `validation_passed` without ever validating the warehouse.
SOURCE_CHECK_HINTS = ("deliver", "stale", "current", "fetch", "source")

TIMEOUT = 60
MAX_RUNS = 25


class AirflowUnavailable(RuntimeError):
    pass


def _astro(*args: str) -> str:
    cmd = ["astro", "dev", "run", *args]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)
    except FileNotFoundError as exc:
        raise AirflowUnavailable("astro CLI not found -- see lab/README.md") from exc
    except subprocess.TimeoutExpired as exc:
        raise AirflowUnavailable(f"`{' '.join(cmd)}` timed out after {TIMEOUT}s") from exc
    if out.returncode != 0:
        detail = (out.stderr or out.stdout).strip().splitlines()
        tail = detail[-1] if detail else "no output"
        raise AirflowUnavailable(f"`{' '.join(cmd)}` failed: {tail[:160]}")
    return out.stdout


# ANSI colour/cursor sequences. The Astro CLI is documented to leak these into
# Windows terminal output (astronomer/astro-cli#635, open since 2022), which
# would leave a line reading `\x1b[0m[{"dag_id": ...` -- not starting with `[`,
# so the scan below would skip the only line that mattered and every probe would
# report "no JSON in Airflow output". Stripping costs nothing on macOS and Linux,
# where the sequences simply are not there.
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def _strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


def _json(text: str):
    """Pull the JSON payload out of Airflow CLI output.

    Line-based on purpose. Airflow prefixes structured log lines like
    `[warning  ] ...`, so scanning for the first `[` grabs a log bracket and the
    parse fails on output that is perfectly fine.
    """
    for line in reversed(_strip_ansi(text).splitlines()):
        stripped = line.strip()
        if stripped.startswith(("[", "{")):
            try:
                return json.loads(stripped)
            except ValueError:
                continue
    raise AirflowUnavailable(f"no JSON in Airflow output: {text.strip()[-200:]!r}")


def dag_runs(dag_id: str = DAG_ID) -> list[dict]:
    """Most recent runs first. dag_id is POSITIONAL."""
    return _json(_astro("dags", "list-runs", dag_id, "-o", "json"))[:MAX_RUNS]


def task_states(dag_id: str, run_id: str) -> list[dict]:
    return _json(_astro("tasks", "states-for-dag-run", dag_id, run_id, "-o", "json"))


def _matching(task_id: str, hints: tuple[str, ...]) -> bool:
    lowered = task_id.lower()
    return any(h in lowered for h in hints)


def _states_by_role(hints: tuple[str, ...], exclude: tuple[str, ...] = ()) -> dict[str, set[str]]:
    """Every state seen for tasks matching `hints`, keyed by task_id."""
    seen: dict[str, set[str]] = {}
    for run in dag_runs():
        run_id = run.get("run_id")
        if not run_id:
            continue
        for state in task_states(DAG_ID, run_id):
            task_id = state.get("task_id", "")
            if not _matching(task_id, hints) or (exclude and _matching(task_id, exclude)):
                continue
            seen.setdefault(task_id, set()).add(state.get("state", ""))
    return seen


def validation_is_a_separate_step() -> tuple[bool, str]:
    """`validation_passed`: a distinct validation task has succeeded.

    Checks the SHAPE of the solution, not only its output (D-019). A team that
    hid the check inside `transform` cannot earn this, which is what keeps
    mission A4's premise intact.
    """
    if not dag_runs():
        return False, f"no runs of {DAG_ID} yet"

    # The gate is a different task; it must not be counted as the validation step.
    seen = _states_by_role(VALIDATION_HINTS, exclude=GATE_HINTS + SOURCE_CHECK_HINTS)
    passed = [t for t, states in seen.items() if "success" in states]
    if passed:
        return True, f"separate validation task succeeded: {', '.join(sorted(passed))}"
    if seen:
        return False, f"found {', '.join(sorted(seen))} but none has succeeded yet"
    return False, (
        "no separate validation task found. Is the check inside `transform` "
        "rather than in its own task?"
    )


def semantic_gate_is_enforced() -> tuple[bool, str]:
    """`semantic_gate_enforced`: the gate has both rejected and accepted.

    Judged on the gate task having BOTH a failed and a successful instance --
    it discriminates. A gate that rejects everything is not a gate, and neither
    is one that passes everything.

    Deliberately NOT judged on business dates: `dags list-runs` carries no `conf`
    in Airflow 3.3, so the date a run used is not recoverable from the CLI.
    Discrimination is the better signal anyway -- it tests the behaviour rather
    than inferring it from which date was requested.
    """
    if not dag_runs():
        return False, f"no runs of {DAG_ID} yet"

    seen = _states_by_role(GATE_HINTS)
    if not seen:
        return False, "no gate task found -- expected something like publish_daily_revenue"

    for task_id, states in sorted(seen.items()):
        if "failed" in states and "success" in states:
            return True, f"{task_id} has both failed and succeeded -- the gate discriminates"

    task_id, states = sorted(seen.items())[0]
    if "failed" not in states:
        return False, (
            f"{task_id} has never failed. Run the EUR 0 delivery (2026-03-04) -- "
            "does your pipeline stop? If that date already loads a plausible total, "
            "SupplyHub has moved on to the corrected delivery -- ask your instructor."
        )
    return False, (
        f"{task_id} has failed but never succeeded. Run a good date (2026-03-03): "
        "a gate that rejects everything is not a gate."
    )


def asset_event_was_published() -> tuple[bool, str]:
    """`asset_event_published`: the asset exists and its producing task succeeded.

    Airflow 3.3's `assets` CLI exposes only list/details/materialize -- there is
    no way to read asset EVENTS. So this is inferred: the gate task declares
    `outlets=[DAILY_REVENUE]`, and in Airflow a successful task with an outlet
    emits the event by construction. Both halves are required, so a declared-but-
    never-run asset does not count.
    """
    try:
        assets = _json(_astro("assets", "list", "-o", "json"))
    except AirflowUnavailable as exc:
        return False, f"could not list assets: {exc}"

    names = {a.get("name") for a in assets}
    if ASSET_NAME not in names:
        return False, f"asset {ASSET_NAME!r} is not registered (found: {sorted(n for n in names if n)})"

    seen = _states_by_role(GATE_HINTS)
    succeeded = [t for t, states in seen.items() if "success" in states]
    if not succeeded:
        return False, (
            f"{ASSET_NAME} is registered but no task carrying its outlet has succeeded yet"
        )

    uris = sorted({a.get("uri") for a in assets if a.get("name") == ASSET_NAME})
    note = f" (registered uris: {', '.join(u for u in uris if u)})" if len(uris) > 1 else ""
    return True, f"{succeeded[0]} succeeded, so its asset outlet was emitted{note}"


def verification_checklist() -> str:
    """Kept for reference. The contract below is now VERIFIED, not assumed."""
    return "\n".join(
        [
            "Verified against Astro Runtime 3.3-2 on 2026-09-14:",
            f"  airflow dags list-runs {DAG_ID} -o json      # dag_id POSITIONAL, not -d",
            "     -> fields: dag_id, run_id, state, logical_date, start_date, end_date",
            "     -> NO conf field: the business_date of a run is not recoverable",
            f"  airflow tasks states-for-dag-run {DAG_ID} <run_id> -o json",
            "     -> fields: dag_id, task_id, state, logical_date, start_date, end_date",
            "  airflow assets list -o json                   # no list-events subcommand",
            "     -> fields: name, uri, group, extra",
        ]
    )
