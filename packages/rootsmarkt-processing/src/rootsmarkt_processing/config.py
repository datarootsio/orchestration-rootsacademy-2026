"""Team configuration: config/team.yaml.

`expected_stores` and `plausible_daily_revenue_eur` are the load-bearing
parameters (decision D-008). Solutions must read them from here rather than
hardcoding literals -- a copied assertion built around another team's numbers
will fail that team's milestone check, which is what stops an answer travelling
across the room.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

CONFIG_ENV = "ROOTSMARKT_CONFIG"
DATA_DIR_ENV = "ROOTSMARKT_DATA_DIR"
CONFIG_RELPATH = "config/team.yaml"
# Where Astro mounts the project inside the Airflow container.
CONTAINER_PATH = "/usr/local/airflow/config/team.yaml"
# How far up from the cwd to look for the repo root.
MAX_WALK_UP = 6


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class TeamConfig:
    team_id: str
    supplyhub_token: str
    supplyhub_base_url: str
    expected_stores: tuple[str, ...]
    revenue_min_eur: int
    revenue_max_eur: int
    schema: str
    course_today: str
    raw: dict

    def is_plausible(self, total_revenue_eur) -> bool:
        return self.revenue_min_eur <= float(total_revenue_eur) <= self.revenue_max_eur


def find_config() -> Path:
    """Locate config/team.yaml regardless of the working directory.

    This has to be robust to cwd, not merely convenient: Dagster executes steps
    in subprocesses, `dg dev` may be started from the dagster/ subdirectory, and
    Airflow runs inside a container where the repo is mounted somewhere else
    entirely. Resolving relative to the cwd alone fails in all three cases.

    Order: explicit env var, then upward from the cwd, then the container mount.
    """
    explicit = os.environ.get(CONFIG_ENV)
    if explicit:
        p = Path(explicit)
        if p.exists():
            return p
        # Do NOT raise. The same .env is read on the host and inside the Airflow
        # container, so an absolute host path is legitimately absent in the
        # container -- fall through rather than breaking the container.

    here = Path.cwd().resolve()
    for directory in (here, *list(here.parents)[:MAX_WALK_UP]):
        candidate = directory / CONFIG_RELPATH
        if candidate.exists():
            return candidate

    container = Path(CONTAINER_PATH)
    if container.exists():
        return container

    raise ConfigError(
        f"no {CONFIG_RELPATH} found (searched upward from {here}). "
        f"Run `roots join <code>` at the repo root, or set {CONFIG_ENV}."
    )


@lru_cache(maxsize=1)
def team_config(path: str | None = None) -> TeamConfig:
    p = Path(path) if path else find_config()
    data = yaml.safe_load(p.read_text()) or {}

    missing = [
        k for k in ("team_id", "supplyhub_token", "expected_stores", "plausible_daily_revenue_eur")
        if k not in data
    ]
    if missing:
        raise ConfigError(f"{p} is missing: {', '.join(missing)}")

    bounds = data["plausible_daily_revenue_eur"]
    return TeamConfig(
        team_id=data["team_id"],
        supplyhub_token=data["supplyhub_token"],
        supplyhub_base_url=(
            os.environ.get("SUPPLYHUB_BASE_URL")
            or data.get("supplyhub_base_url", "http://localhost:8090")
        ),
        expected_stores=tuple(data["expected_stores"]),
        revenue_min_eur=int(bounds["min"]),
        revenue_max_eur=int(bounds["max"]),
        schema=(data.get("warehouse") or {}).get("schema", data["team_id"].replace("-", "_")),
        course_today=data.get("course_today", ""),
        raw=data,
    )


# ---------------------------------------------------------------- data paths
#
# Anchored to the repo root, NOT to the working directory. Nothing may use a
# cwd-relative data path: Dagster runs steps in subprocesses, `dg dev` sets the
# working directory to `dagster/src`, and Airflow runs inside a container with
# the repo mounted at /usr/local/airflow. A relative Path("data/raw") silently
# resolves somewhere different in each of those, which produces an empty glob
# rather than an error.
#
# Deriving the root from config/team.yaml means the same code is correct on the
# host and in the container, because that file is at the repo root in both.


def repo_root() -> Path:
    """The directory containing `config/team.yaml`."""
    return find_config().resolve().parent.parent


def data_dir() -> Path:
    override = os.environ.get(DATA_DIR_ENV)
    return Path(override).resolve() if override else repo_root() / "data"


def candidate_data_dirs() -> list[Path]:
    """Every directory a delivery could have landed in.

    The two halves of the course legitimately write to different places, because
    Astro mounts `include/` into the Airflow container but not `data/`:

      - Dagster and `roots` run on the host and use `data/`.
      - Airflow runs in the container with ROOTSMARKT_DATA_DIR pointing at
        `/usr/local/airflow/include/data`, which is the host's `include/data/`.

    Milestone checks care that a delivery landed, not which runtime produced it,
    so they search both. Writers still use `data_dir()` -- there is exactly one
    place to write, and two places to look.
    """
    dirs = [data_dir()]
    try:
        mounted = repo_root() / "include" / "data"
    except ConfigError:
        return dirs
    if mounted not in dirs:
        dirs.append(mounted)
    return dirs


def raw_dir(business_date: str) -> Path:
    return data_dir() / "raw" / business_date


def clean_dir() -> Path:
    return data_dir() / "clean"


def clean_file(business_date: str) -> Path:
    return clean_dir() / f"{business_date}.csv"


def delivery_file(business_date: str) -> Path:
    """Where the delivery for this business date lands. Exactly one path.

    Deliberately NOT named after the delivery_id. SupplyHub can republish a
    business date under a new id (mission D2's corrected delivery), and keeping
    id-named files left two deliveries side by side with no reliable way to say
    which was current -- `sorted(glob)[-1]` picks alphabetically, and the
    corrected id sorted BEFORE the stale one often enough to break D2 outright.

    One deterministic path per date means a republish overwrites in place, which
    is exactly what "the source reissued this date" should mean. The delivery_id
    and checksum live in the materialization metadata, where they are observable
    without being load-bearing.
    """
    return raw_dir(business_date) / "delivery.csv"


def require_delivery_file(business_date: str) -> Path:
    """The delivery file, or a diagnosable error naming where we looked."""
    path = delivery_file(business_date)
    if not path.exists():
        raise FileNotFoundError(
            f"no delivery landed for {business_date} at {path}. "
            "Materialize raw_delivery first (re-materialize it if the source reissued the date)."
        )
    return path
