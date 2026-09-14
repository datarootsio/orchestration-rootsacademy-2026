"""Milestone checks.

These live in the participant repo and run client-side (decision D-021). One
code path works whether a team is on the hosted warehouse or a local fallback --
a server-side query cannot see a team that has run `roots offline`.

Each check returns PASS, FAIL, PENDING (preconditions not met yet) or SKIP
(reported by the server, or submitted by hand).
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from rootsmarkt_processing import source
from rootsmarkt_processing.config import TeamConfig

PASS, FAIL, PENDING, SKIP = "PASS", "FAIL", "PENDING", "SKIP"



@dataclass
class CheckResult:
    status: str
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == PASS


@dataclass
class Check:
    milestone: str
    mission: str
    run: Callable[[TeamConfig], CheckResult]
    note: str = ""


# --------------------------------------------------------------------------- A1


def _landed_files() -> list[Path]:
    """Deliveries landed by EITHER runtime.

    Airflow writes into the container's ROOTSMARKT_DATA_DIR, which Astro mounts
    from `include/data`; Dagster and roots use `data/` on the host. The milestone
    is "a delivery landed", so both are searched -- otherwise a team doing the
    Airflow half first (which is the course order) never sees this go green.
    """
    from rootsmarkt_processing.config import candidate_data_dirs

    found: list[Path] = []
    for directory in candidate_data_dirs():
        raw = directory / "raw"
        if raw.exists():
            found.extend(sorted(raw.glob("*/*.csv")))
    return found


def check_raw_delivery_landed(cfg: TeamConfig) -> CheckResult:
    """A file on disk that matches a delivery SupplyHub actually served us.

    Checksum-verified, so a hand-written CSV does not count.
    """
    files = _landed_files()
    if not files:
        from rootsmarkt_processing.config import candidate_data_dirs

        looked = ", ".join(str(d / "raw") for d in candidate_data_dirs())
        return CheckResult(PENDING, f"no delivery found. Looked in: {looked}")

    client = source.SupplyHubClient(cfg.supplyhub_base_url, cfg.supplyhub_token, cfg.team_id)
    checksums = {}
    for business_date in sorted({p.parent.name for p in files}):
        try:
            meta = client.get_delivery(business_date)
            checksums[meta.checksum_sha256] = meta
        except source.SupplyHubError as exc:
            return CheckResult(FAIL, f"cannot reach SupplyHub to verify: {exc}")

    for path in files:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest in checksums:
            meta = checksums[digest]
            return CheckResult(
                PASS, f"{path} matches {meta.delivery_id} ({meta.row_count} rows)"
            )
    return CheckResult(
        FAIL,
        f"found {len(files)} file(s) but none matches a delivery served to {cfg.team_id}. "
        "Did the download complete?",
    )


# --------------------------------------------------------------------------- A3


def check_warehouse_loaded(cfg: TeamConfig) -> CheckResult:
    """Every expected store present for some business date, with real revenue."""
    try:
        from rootsmarkt_processing import load
    except ImportError as exc:  # pragma: no cover
        return CheckResult(FAIL, f"cannot import loader: {exc}")

    if not os.environ.get("ROOTSMARKT_DSN"):
        return CheckResult(PENDING, "ROOTSMARKT_DSN is not set -- run `roots join` first")

    found = []
    for business_date in ("2026-03-03", "2026-03-04", "2026-03-02"):
        try:
            rows = load.read_daily_revenue(business_date, schema=cfg.schema)
        except Exception as exc:  # noqa: BLE001
            return CheckResult(FAIL, f"cannot query {cfg.schema}.daily_revenue: {exc}")
        if not rows:
            continue
        missing = set(cfg.expected_stores) - {r[0] for r in rows}
        if missing:
            found.append(f"{business_date}: missing {len(missing)} store(s)")
            continue
        return CheckResult(
            PASS, f"{business_date}: {len(rows)} rows, all {len(cfg.expected_stores)} stores"
        )
    return CheckResult(PENDING, "; ".join(found) or "no business date fully loaded yet")


# ------------------------------------------------------- behavioural probes


def _wrap(fn, unavailable_exc) -> "Callable[[TeamConfig], CheckResult]":
    """Adapt a probe returning (ok, detail) into a CheckResult.

    A probe whose tooling is missing yields PENDING, never FAIL: "Airflow is not
    running" is not the same statement as "your gate does not work", and telling
    a team the latter when the former is true is how a pre-flight tool loses
    their trust.
    """

    def run(_cfg: TeamConfig) -> CheckResult:
        try:
            ok, detail = fn()
        except unavailable_exc as exc:
            return CheckResult(PENDING, str(exc))
        except Exception as exc:  # noqa: BLE001 - a probe must not kill the daemon
            return CheckResult(PENDING, f"probe error: {type(exc).__name__}: {str(exc)[:140]}")
        return CheckResult(PASS if ok else FAIL, detail)

    return run


def _dagster_check(name: str):
    from .probes import dagster_probes as dp

    return _wrap(getattr(dp, name), dp.DagsterUnavailable)


def _airflow_check(name: str):
    from .probes import airflow_probes as ap

    return _wrap(getattr(ap, name), ap.AirflowUnavailable)


# ---------------------------------------------------------------- the registry

CHECKS: list[Check] = [
    # Airflow
    Check("raw_delivery_landed", "A1", check_raw_delivery_landed),
    Check("warehouse_loaded", "A3", check_warehouse_loaded),
    Check("validation_passed", "A3", _airflow_check("validation_is_a_separate_step"),
          "checks the SHAPE: a distinct validation task must have succeeded"),
    Check("semantic_gate_enforced", "A4", _airflow_check("semantic_gate_is_enforced"),
          "needs a FAILED run on the EUR 0 date and a SUCCESSFUL one on a good date"),
    Check("asset_event_published", "A4", _airflow_check("asset_event_was_published")),
    # Dagster
    Check("asset_graph_defined", "D1", _dagster_check("graph_is_defined")),
    Check("assets_materialized", "D1", _dagster_check("all_materialized")),
    Check("check_failing_correctly", "D2", _dagster_check("check_has_failed"),
          "the check must have caught the bad delivery at least once"),
    Check("check_passing", "D2", _dagster_check("check_has_passed")),
]

# Milestones no client check can earn.
SERVER_DERIVED = {"connected", "no_unnecessary_refetch"}
SUBMITTED = {"stale_delivery_detected", "rebuild_justified", "comparison_submitted"}

BY_MILESTONE = {c.milestone: c for c in CHECKS}


def for_mission(mission: str) -> list[Check]:
    return [c for c in CHECKS if c.mission.upper() == mission.upper()]
