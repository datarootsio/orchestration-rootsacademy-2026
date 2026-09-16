"""Config discovery must not depend on the working directory.

Regression tests for a real bug: `config/team.yaml` was resolved relative to the
cwd, which broke as soon as Dagster executed a step in a subprocess. It failed
with "no config/team.yaml found" during a materialization even though the file
was sitting in the repo root.

Three environments have to work, and none of them shares a cwd:
  - the host, from the repo root            (`uv run dagster ...`)
  - the host, from a subdirectory           (`dg dev` inside dagster/)
  - the Airflow container, repo mounted at  /usr/local/airflow
"""

from __future__ import annotations

import yaml
import pytest

from rootsmarkt_processing import config as cfgmod

VALID = {
    "team_id": "team-07",
    "supplyhub_token": "sh_07_test",
    "expected_stores": ["RM-0101", "RM-0102"],
    "plausible_daily_revenue_eur": {"min": 100, "max": 200},
    "warehouse": {"schema": "team_07"},
}


@pytest.fixture(autouse=True)
def _clear_cache(monkeypatch):
    monkeypatch.delenv(cfgmod.CONFIG_ENV, raising=False)
    cfgmod.team_config.cache_clear()
    yield
    cfgmod.team_config.cache_clear()


def _write(root, data=None):
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config" / "team.yaml").write_text(yaml.safe_dump(data or VALID))
    return root / "config" / "team.yaml"


def test_found_in_the_current_directory(tmp_path, monkeypatch):
    _write(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert cfgmod.find_config().resolve() == (tmp_path / "config/team.yaml").resolve()


def test_found_from_a_subdirectory(tmp_path, monkeypatch):
    """`dg dev` is legitimately run from dagster/."""
    _write(tmp_path)
    sub = tmp_path / "dagster" / "src" / "rootsmarkt"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    assert cfgmod.find_config().resolve() == (tmp_path / "config/team.yaml").resolve()


def test_explicit_env_var_wins(tmp_path, monkeypatch):
    other = tmp_path / "elsewhere"
    path = _write(other)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(cfgmod.CONFIG_ENV, str(path))
    assert cfgmod.find_config().resolve() == path.resolve()


def test_missing_env_var_target_falls_through_instead_of_raising(tmp_path, monkeypatch):
    """The SAME .env is read on the host and inside the Airflow container.

    An absolute host path is legitimately absent in the container, so a stale
    ROOTSMARKT_CONFIG must not break the container -- it must fall through to the
    upward search.
    """
    _write(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(cfgmod.CONFIG_ENV, "/nope/does/not/exist/team.yaml")
    assert cfgmod.find_config().resolve() == (tmp_path / "config/team.yaml").resolve()


def test_raises_with_a_useful_message_when_absent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(cfgmod.ConfigError, match="roots join"):
        cfgmod.find_config()


def test_search_does_not_walk_up_forever(tmp_path, monkeypatch):
    """Bounded, so a stray config far up the filesystem is never picked up."""
    _write(tmp_path)
    deep = tmp_path
    for i in range(cfgmod.MAX_WALK_UP + 3):
        deep = deep / f"d{i}"
    deep.mkdir(parents=True)
    monkeypatch.chdir(deep)
    with pytest.raises(cfgmod.ConfigError):
        cfgmod.find_config()


def test_parses_load_bearing_parameters(tmp_path, monkeypatch):
    _write(tmp_path)
    monkeypatch.chdir(tmp_path)
    cfg = cfgmod.team_config()
    assert cfg.team_id == "team-07"
    assert cfg.expected_stores == ("RM-0101", "RM-0102")
    assert cfg.revenue_min_eur == 100
    assert cfg.schema == "team_07"


def test_is_plausible_boundaries(tmp_path, monkeypatch):
    _write(tmp_path)
    monkeypatch.chdir(tmp_path)
    cfg = cfgmod.team_config()
    assert cfg.is_plausible(100) and cfg.is_plausible(200) and cfg.is_plausible(150)
    # The A4 trap: a hardcoded `> 0` would wave both of these through.
    assert not cfg.is_plausible(0)
    assert not cfg.is_plausible(12)
    assert not cfg.is_plausible(201)


def test_missing_required_key_is_reported(tmp_path, monkeypatch):
    _write(tmp_path, {"team_id": "team-07"})
    monkeypatch.chdir(tmp_path)
    with pytest.raises(cfgmod.ConfigError, match="supplyhub_token"):
        cfgmod.team_config()


# ------------------------------------------------------------- data paths
#
# Regression tests for the second cwd bug. Config discovery was fixed by
# walking upward, but the DATA paths were left as cwd-relative
# `Path("data/raw")` -- so under `dg dev` (cwd = dagster/src) the glob came back
# empty and `daily_revenue` died with `IndexError: list index out of range`
# several frames from the cause.


def test_data_paths_anchor_to_the_repo_root_not_the_cwd(tmp_path, monkeypatch):
    """The whole point: same answer from anywhere."""
    _write(tmp_path)
    deep = tmp_path / "dagster" / "src"
    deep.mkdir(parents=True)

    monkeypatch.chdir(tmp_path)
    from_root = cfgmod.raw_dir("2026-03-04")

    monkeypatch.chdir(deep)
    cfgmod.team_config.cache_clear()
    from_subdir = cfgmod.raw_dir("2026-03-04")

    assert from_root == from_subdir == tmp_path / "data" / "raw" / "2026-03-04"
    assert "dagster" not in str(from_subdir)


def test_repo_root_is_the_config_parent(tmp_path, monkeypatch):
    _write(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert cfgmod.repo_root() == tmp_path


def test_data_dir_env_override_wins(tmp_path, monkeypatch):
    """How the Airflow container points at a directory Astro actually mounts."""
    _write(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(cfgmod.DATA_DIR_ENV, str(tmp_path / "include" / "data"))
    assert cfgmod.raw_dir("2026-03-04") == tmp_path / "include" / "data" / "raw" / "2026-03-04"


def test_require_delivery_file_names_the_directory_it_searched(tmp_path, monkeypatch):
    """A missing delivery must raise something diagnosable, not IndexError."""
    _write(tmp_path)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(FileNotFoundError, match="2026-03-04"):
        cfgmod.require_delivery_file("2026-03-04")


def test_delivery_path_is_deterministic_not_id_named(tmp_path, monkeypatch):
    """Regression: id-named files broke mission D2.

    SupplyHub republishes 2026-03-04 under a new delivery_id. With id-named
    files, both sat in the directory and `sorted(glob)[-1]` chose
    ALPHABETICALLY -- and for team-00 the corrected id ("...-81dc") sorts BEFORE
    the stale one ("...-a479"), so the stale zero-price delivery won and the
    plausibility check could never pass.

    The ids below are the real ones from that failure, so this test would have
    caught it.
    """
    _write(tmp_path)
    monkeypatch.chdir(tmp_path)

    stale, corrected = "dlv-team-00-20260304-a479", "dlv-team-00-20260304-81dc"
    assert sorted([f"{corrected}.csv", f"{stale}.csv"])[-1] == f"{stale}.csv", (
        "premise of the bug: alphabetical order favours the stale delivery"
    )

    # One path per business date, regardless of which id the source used.
    path = cfgmod.delivery_file("2026-03-04")
    assert path.name == "delivery.csv"
    assert corrected not in str(path) and stale not in str(path)

    # A republish overwrites in place, so there is never a choice to get wrong.
    path.parent.mkdir(parents=True)
    path.write_text("stale")
    path.write_text("corrected")
    assert cfgmod.require_delivery_file("2026-03-04").read_text() == "corrected"
    assert len(list(path.parent.glob("*.csv"))) == 1


def test_candidate_data_dirs_covers_both_runtimes(tmp_path, monkeypatch):
    """Airflow writes into include/data (Astro mounts it); Dagster and roots use
    data/. A milestone check that knew only one of them left `raw_delivery_landed`
    grey for any team doing the Airflow half first -- which is the course order.
    """
    _write(tmp_path)
    monkeypatch.chdir(tmp_path)
    dirs = cfgmod.candidate_data_dirs()

    assert tmp_path / "data" in dirs, "the host/Dagster location"
    assert tmp_path / "include" / "data" in dirs, "the Airflow-mounted location"
    assert cfgmod.data_dir() == dirs[0], "writers still use exactly one directory"


def test_candidate_data_dirs_has_no_duplicates(tmp_path, monkeypatch):
    _write(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(cfgmod.DATA_DIR_ENV, str(tmp_path / "include" / "data"))
    dirs = cfgmod.candidate_data_dirs()
    assert len(dirs) == len(set(dirs))
