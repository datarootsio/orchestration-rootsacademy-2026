"""Dagster milestone probes.

All four read *history* rather than executing anything: the asset graph is
inspected statically, and materialization and check outcomes come from the
Dagster event log. Nothing is materialized, nothing is mutated, and a full pass
takes milliseconds -- which is what makes `roots watch` viable on a 20-second
loop (decision D-024).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

EXPECTED_ASSETS = ("raw_delivery", "clean_sales", "daily_revenue")
EXPECTED_EDGES = {"clean_sales": "raw_delivery", "daily_revenue": "clean_sales"}
CHECK_NAME = "revenue_is_plausible"
DEFS_MODULE = "rootsmarkt.definitions"


@dataclass
class GraphInfo:
    keys: set[str]
    deps: dict[str, set[str]]
    checks: set[str]


class DagsterUnavailable(RuntimeError):
    pass


def _instance():
    if not os.environ.get("DAGSTER_HOME"):
        raise DagsterUnavailable(
            "DAGSTER_HOME is not set -- run `roots join`, then re-run. Without it "
            "Dagster uses a temporary instance and nothing persists."
        )
    try:
        from dagster import DagsterInstance
    except ImportError as exc:
        raise DagsterUnavailable("dagster is not installed -- run `uv sync`") from exc
    return DagsterInstance.get()


def load_graph() -> GraphInfo:
    """Inspect the asset graph without executing it."""
    try:
        import importlib

        from dagster import AssetsDefinition
    except ImportError as exc:
        raise DagsterUnavailable("dagster is not installed -- run `uv sync`") from exc

    module = importlib.import_module(DEFS_MODULE)
    holder = getattr(module, "defs", None)
    if holder is None:
        raise DagsterUnavailable(f"{DEFS_MODULE} does not define `defs`")

    # A @definitions-decorated function yields LazyDefinitions; load_fn() resolves
    # it to a real Definitions. Verified against dagster 1.13.14 -- LazyDefinitions
    # exposes only count/has_context_arg/index/load_fn, so there is no
    # get_definitions() to try first.
    if hasattr(holder, "load_fn"):
        resolved = holder.load_fn()
    elif hasattr(holder, "get_definitions"):
        resolved = holder.get_definitions()
    else:
        resolved = holder

    keys: set[str] = set()
    deps: dict[str, set[str]] = {}
    checks: set[str] = set()

    for item in resolved.assets or []:
        if not isinstance(item, AssetsDefinition):
            continue
        for key in item.keys:
            name = key.to_user_string()
            keys.add(name)
            upstream = {
                dep.to_user_string()
                for dep in item.asset_deps.get(key, set())
                if dep != key
            }
            deps[name] = upstream
        for spec in getattr(item, "check_specs", []) or []:
            checks.add(spec.name)

    for check_def in resolved.asset_checks or []:
        for spec in check_def.check_specs:
            checks.add(spec.name)

    return GraphInfo(keys=keys, deps=deps, checks=checks)


def graph_is_defined() -> tuple[bool, str]:
    """`asset_graph_defined`: the three assets exist and are wired in order."""
    graph = load_graph()
    missing = [a for a in EXPECTED_ASSETS if a not in graph.keys]
    if missing:
        return False, f"missing asset(s): {', '.join(missing)}"

    wrong = []
    for downstream, upstream in EXPECTED_EDGES.items():
        if upstream not in graph.deps.get(downstream, set()):
            wrong.append(f"{downstream} does not depend on {upstream}")
    if wrong:
        return False, "; ".join(wrong)

    return True, f"{' -> '.join(EXPECTED_ASSETS)} wired correctly"


def materializations() -> dict[str, dict]:
    """Latest materialization metadata per asset, from the event log."""
    from dagster import AssetKey

    out: dict[str, dict] = {}
    with _instance() as inst:
        for name in EXPECTED_ASSETS:
            event = inst.get_latest_materialization_event(AssetKey(name))
            if event is None or event.asset_materialization is None:
                continue
            raw = event.asset_materialization.metadata or {}
            out[name] = {k: getattr(v, "value", v) for k, v in raw.items()}
    return out


def all_materialized() -> tuple[bool, str]:
    """`assets_materialized`: every asset has been built at least once."""
    found = materializations()
    missing = [a for a in EXPECTED_ASSETS if a not in found]
    if missing:
        return False, f"not yet materialized: {', '.join(missing)}"
    total = found["daily_revenue"].get("total_revenue_eur")
    suffix = f", daily_revenue total EUR {total:,.2f}" if isinstance(total, (int, float)) else ""
    return True, f"all {len(EXPECTED_ASSETS)} assets materialized{suffix}"


def check_outcomes() -> list[bool]:
    """Every *terminal* outcome of the plausibility check, oldest first.

    Both a failure and a later pass are needed: D2 asks teams to watch the check
    catch the bad delivery and then go green on the corrected one.

    Filtering on status in the query is deliberate. A check sits in PLANNED
    until its evaluation event arrives, and a PLANNED record's `.evaluation` is
    an `AssetCheckEvaluationPlanned`, which has no `.passed` -- reading it blindly
    raises AttributeError. Letting the storage layer exclude PLANNED removes the
    guesswork.
    """
    from dagster._core.storage.asset_check_execution_record import (
        AssetCheckExecutionRecordStatus as Status,
    )

    terminal = {Status.SUCCEEDED, Status.FAILED}
    with _instance() as inst:
        records = inst.event_log_storage.get_asset_check_execution_history(
            check_key=_check_key(), limit=50, status=terminal
        )

    outcomes: list[bool] = []
    for rec in reversed(list(records)):
        # Prefer the evaluation's own verdict when it is a real evaluation;
        # fall back to the record status otherwise.
        evaluation = getattr(rec, "evaluation", None)
        passed = getattr(evaluation, "passed", None)
        if passed is None:
            passed = rec.status == Status.SUCCEEDED
        outcomes.append(bool(passed))
    return outcomes


def _check_key():
    from dagster import AssetCheckKey, AssetKey

    return AssetCheckKey(asset_key=AssetKey("daily_revenue"), name=CHECK_NAME)


def check_has_failed() -> tuple[bool, str]:
    """`check_failing_correctly`: the check caught the bad delivery."""
    graph = load_graph()
    if CHECK_NAME not in graph.checks:
        return False, f"no asset check named {CHECK_NAME} is defined"
    outcomes = check_outcomes()
    if not outcomes:
        return False, "the check has never run -- materialize daily_revenue"
    if any(o is False for o in outcomes):
        return True, f"the check has failed at least once ({len(outcomes)} run(s) recorded)"
    return False, "the check has only ever passed -- did it actually see the EUR 0 delivery?"


def check_has_passed() -> tuple[bool, str]:
    """`check_passing`: and it went green once the data was corrected."""
    outcomes = check_outcomes()
    if not outcomes:
        return False, "the check has never run"
    if any(o is True for o in outcomes):
        return True, "the check has passed on corrected data"
    return False, "the check has never passed yet"
