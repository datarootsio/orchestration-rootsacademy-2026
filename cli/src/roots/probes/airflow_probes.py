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
from pathlib import Path

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
# The fetch task itself also contains "deliver" (fetch_delivery), so the A2 guard
# probe has to exclude it or it would grade the download as the guard.
FETCH_HINTS = ("fetch", "download", "retrieve")

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


# --------------------------------------------------------------------- caching
#
# Every `astro dev run` is a container exec and costs ~3s -- that is the floor,
# and Airflow's CLI has no bulk form for `tasks states-for-dag-run`.
#
# What was NOT the floor: all four Airflow probes fetched the same data and then
# filtered it differently in memory. With 12 runs that was 56 calls and ~110s per
# `roots verify`, which also made `roots watch` slower than its own interval --
# so the dashboard the instructor steers by lagged reality by minutes.
#
# Two layers:
#   per pass   dag_runs() and task_states() fetched once, shared by every probe
#   across     a FINISHED run's task states are persisted, so later passes only
#              fetch runs they have not seen
#
# The trap in the second one: a finished run is not immutable. Clearing a task in
# Airflow puts the run back to `running` and re-runs it, so a cached `failed`
# could hide a later `success`. Entries are therefore validated against the run's
# (state, end_date), which `dags list-runs` already gives us -- clearing changes
# end_date, which misses the cache and refetches.

TERMINAL_RUN_STATES = ("success", "failed")
CACHE_PATH = Path(".roots") / "airflow-runs.json"

_pass: dict = {}


def begin_pass() -> None:
    """Start a new check pass. Called once per `roots verify` / `watch` cycle.

    Without this the memo would persist for the life of the process and `watch`
    would never see a run made after it started.
    """
    _pass.clear()


def _read_cache() -> dict:
    """Never fatal: a performance cache that can break a milestone check is not
    worth having."""
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_cache(cache: dict) -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(cache), encoding="utf-8")
    except OSError:
        pass


def _fingerprint(run: dict) -> str:
    """What must match for a cached entry to still be valid."""
    return f"{run.get('state')}|{run.get('end_date')}"


def dag_runs(dag_id: str = DAG_ID) -> list[dict]:
    """Most recent runs first. dag_id is POSITIONAL."""
    if "runs" not in _pass:
        _pass["runs"] = _json(_astro("dags", "list-runs", dag_id, "-o", "json"))[:MAX_RUNS]
    return _pass["runs"]


def task_states(dag_id: str, run_id: str) -> list[dict]:
    seen = _pass.setdefault("states", {})
    if run_id in seen:
        return seen[run_id]

    run = next((r for r in dag_runs(dag_id) if r.get("run_id") == run_id), {})
    fingerprint = _fingerprint(run)
    cache = _cache_for_pass(dag_id)

    entry = cache.get(run_id)
    if entry and entry.get("fingerprint") == fingerprint:
        seen[run_id] = entry["states"]
        return entry["states"]

    states = _json(_astro("tasks", "states-for-dag-run", dag_id, run_id, "-o", "json"))
    seen[run_id] = states

    if run.get("state") in TERMINAL_RUN_STATES:
        cache[run_id] = {"fingerprint": fingerprint, "states": states}
        _write_cache(cache)
    return states


def _cache_for_pass(dag_id: str) -> dict:
    """The disk cache, loaded once per pass and pruned to runs Airflow still has.

    Pruning happens on LOAD rather than on write. Doing it on write meant a pass
    that read everything from cache -- the common case, and the whole point --
    never pruned at all, so entries for runs Airflow had dropped lived forever.
    """
    if "cache" not in _pass:
        cache = _read_cache()
        live = {r.get("run_id") for r in dag_runs(dag_id)}
        pruned = {k: v for k, v in cache.items() if k in live}
        if len(pruned) != len(cache):
            _write_cache(pruned)
        _pass["cache"] = pruned
    return _pass["cache"]


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


# States that mean "this task stopped the pipeline". A2 accepts a wider set than
# A4 on purpose: its brief allows retry, wait, fail OR skip, and a team that
# chose to skip is not wrong. A4's brief asks for a failure specifically.
STOPPED_STATES = ("failed", "upstream_failed", "skipped")


def _discriminates(
    hints: tuple[str, ...],
    stop_states: tuple[str, ...],
    exclude: tuple[str, ...] = (),
) -> tuple[str | None, set[str]]:
    """Find a task matching `hints` that has BOTH succeeded and stopped.

    Shared by the A2 guard and the A4 gate, which ask the same question of
    different tasks: does this thing DISCRIMINATE? One that stops everything is
    not a check, and neither is one that passes everything.

    It is also what makes these shape checks rather than liveness checks: an
    untouched stub raises NotImplementedError, so it can only ever fail -- never
    succeed -- and cannot earn the milestone by being run.

    Returns the discriminating task if there is one, otherwise the first
    candidate and its states so the caller can say what is missing.
    """
    seen = _states_by_role(hints, exclude=exclude)
    if not seen:
        return None, set()
    for task_id, states in sorted(seen.items()):
        if "success" in states and any(s in states for s in stop_states):
            return task_id, states
    task_id, states = sorted(seen.items())[0]
    return None, states | {f"__candidate__{task_id}"}


def _candidate_name(states: set[str]) -> str:
    for s in states:
        if s.startswith("__candidate__"):
            return s.removeprefix("__candidate__")
    return "the task"


def stale_delivery_was_blocked() -> tuple[bool, str]:
    """`stale_delivery_blocked`: the A2 guard stops a stale delivery.

    Mission A2 asks teams to make the DAG refuse to proceed on a delivery that
    is not the one requested, and deliberately does NOT grade which response
    they choose -- retry, wait, fail and skip are all defensible, and grading one
    would punish a team that reasoned well and chose differently.

    Not grading WHICH response is a different question from checking whether a
    guard exists at all, which is what this does. Every one of those four
    responses leaves the same trace: the guard passed something and stopped
    something else.

    `fetch_delivery` also matches "deliver", so it is excluded explicitly --
    without that, the fetch task gets mistaken for the guard.
    """
    if not dag_runs():
        return False, f"no runs of {DAG_ID} yet"

    task_id, states = _discriminates(
        SOURCE_CHECK_HINTS, STOPPED_STATES, exclude=FETCH_HINTS + GATE_HINTS
    )
    if task_id:
        return True, f"{task_id} has both passed and stopped a delivery -- the guard discriminates"

    if not states:
        return False, (
            "no staleness guard found. A2 asks for a task that refuses to proceed "
            "when the delivery is not the one you requested -- the stub calls it "
            "assert_delivery_is_current."
        )
    name = _candidate_name(states)
    if "success" not in states:
        return False, (
            f"{name} has never succeeded -- so far it only ever stops. Run a date that "
            "IS available (2026-03-02): a guard that blocks everything is not a guard. "
            "If it is still raising NotImplementedError, that is why."
        )
    return False, (
        f"{name} has only ever succeeded. Run 2026-03-03, which SupplyHub cannot "
        "serve -- does your pipeline notice and stop?"
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

    # Only "failed": A4 asks the pipeline to FAIL on an implausible figure, and a
    # gate that merely skips would leave the bad day silently absent instead.
    task_id, states = _discriminates(GATE_HINTS, ("failed",))
    if task_id:
        return True, f"{task_id} has both failed and succeeded -- the gate discriminates"

    if not states:
        return False, "no gate task found -- expected something like publish_daily_revenue"

    name = _candidate_name(states)
    if "failed" not in states:
        return False, (
            f"{name} has never failed. Run the EUR 0 delivery (2026-03-04) -- "
            "does your pipeline stop? If that date already loads a plausible total, "
            "SupplyHub has moved on to the corrected delivery -- ask your instructor."
        )
    return False, (
        f"{name} has failed but never succeeded. Run a good date (2026-03-03): "
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
