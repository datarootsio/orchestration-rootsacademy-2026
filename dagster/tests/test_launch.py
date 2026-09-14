"""The documented launch command must actually resolve.

Regression test for a real failure: `uv run dg dev` was documented but never run.
It fails from the repo root (no `[tool.dg]` in the nearest pyproject.toml) and
fails differently from `dagster/` (uv builds a second venv that cannot resolve
the local path dependency). Neither location worked, and no test noticed.

These assertions cover the configuration that makes
`uv run --env-file .env dg dev --target-path dagster` work.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DAGSTER_PROJECT = REPO / "dagster"


@pytest.fixture(scope="module")
def dg_config() -> dict:
    return tomllib.loads((DAGSTER_PROJECT / "pyproject.toml").read_text())["tool"]["dg"]


def test_dagster_dir_is_a_dg_project(dg_config):
    """`dg dev --target-path dagster` resolves a *project* context, which is what
    makes dg use the ACTIVE venv. In workspace mode it would look for
    dagster/.venv instead and there deliberately isn't one."""
    assert dg_config["directory_type"] == "project"
    assert dg_config["project"]["root_module"] == "rootsmarkt"


def test_code_location_is_named_for_the_project_not_the_directory(dg_config):
    """Otherwise the Dagster UI shows a code location called "dagster", which
    reads as a tool name rather than this project."""
    assert dg_config["project"]["code_location_name"] == "rootsmarkt"


def test_venv_mismatch_warning_is_suppressed(dg_config):
    """The venv lives at the repo root by design (D-002, D-023). dg warns about
    that on every command; suppressing it keeps the course output clean."""
    assert "project_and_activated_venv_mismatch" in dg_config["cli"]["suppress_warnings"]


def test_root_is_not_a_dg_directory():
    """If the root ever gained [tool.dg], `--target-path` would silently resolve
    the wrong context -- and the root cannot be the dg project because the
    Dagster package is not laid out beneath it."""
    root = tomllib.loads((REPO / "pyproject.toml").read_text())
    assert "dg" not in root.get("tool", {})


def test_no_stray_venv_in_the_dagster_project():
    """A leftover dagster/.venv is what produced the venv-mismatch warning. It
    also means someone ran `cd dagster && uv run ...`, which cannot work."""
    assert not (DAGSTER_PROJECT / ".venv").exists(), (
        "dagster/.venv exists -- delete it; the environment lives at the repo root"
    )


def test_root_project_supplies_the_dagster_package_editable():
    """`--target-path` only works because `rootsmarkt` is installed into the ROOT
    venv as an editable path dependency."""
    root = tomllib.loads((REPO / "pyproject.toml").read_text())
    sources = root["tool"]["uv"]["sources"]
    assert sources["rootsmarkt-dagster"]["editable"] is True
    assert sources["rootsmarkt-dagster"]["path"] == "dagster"


def test_definitions_module_imports():
    """The module `dg` will load, and whatever is in defs/ resolves cleanly.

    Deliberately does NOT require the three assets to exist. `defs/` ships as a
    stub, and defining those assets IS mission D1 -- asserting them here would
    mean a participant's very first `uv run pytest` failed before they had
    written anything, which teaches nothing except that the repo is broken.

    The reference assets are asserted in `test_assets.py`, against `solutions/`.
    What matters here is the launch path: the module imports, the definitions
    resolve, and nothing in defs/ raises at load time.
    """
    import importlib

    module = importlib.import_module("rootsmarkt.definitions")
    holder = module.defs
    resolved = holder.load_fn() if hasattr(holder, "load_fn") else holder
    keys = {k.to_user_string() for a in (resolved.assets or []) for k in getattr(a, "keys", [])}

    # A typo'd asset name loads fine and then silently fails every probe, so the
    # one thing worth checking is that nothing unexpected is defined.
    assert keys <= {"raw_delivery", "clean_sales", "daily_revenue"}, (
        f"unexpected asset name(s): {sorted(keys - {'raw_delivery', 'clean_sales', 'daily_revenue'})}"
        " -- the milestone probes look for exactly these three"
    )
