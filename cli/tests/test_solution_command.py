"""`roots solution` must be reversible, because it overwrites the starter files.

INSTRUCTOR-SIDE command, but the test matters more than most: the first version
printed "Undo with: git checkout dags dagster", which does nothing at all when
the stubs are untracked -- and it destroyed them during development. Advice that
depends on the user's VCS state is not an undo.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

STUB_DAG = '''"""stub"""
def fetch_delivery():
    raise NotImplementedError("A1")
'''
SOLUTION_DAG = '''"""solution"""
def fetch_delivery():
    return "the answer"
'''
# All comments, no NotImplementedError -- this is the shape that broke a
# marker-based backup check.
STUB_ASSETS = '''"""stub"""
# TODO(D1): define three assets.
'''
SOLUTION_ASSETS = '''"""solution"""
def raw_delivery():
    return "the answer"
'''


@pytest.fixture
def repo(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\nversion='0'\n")
    (tmp_path / "dags").mkdir()
    (tmp_path / "dags" / "rootsmarkt_delivery.py").write_text(STUB_DAG)
    defs = tmp_path / "dagster" / "src" / "rootsmarkt" / "defs"
    defs.mkdir(parents=True)
    (defs / "assets.py").write_text(STUB_ASSETS)

    sol = tmp_path / "solutions"
    (sol / "airflow").mkdir(parents=True)
    (sol / "airflow" / "rootsmarkt_delivery.py").write_text(SOLUTION_DAG)
    (sol / "dagster").mkdir(parents=True)
    (sol / "dagster" / "assets.py").write_text(SOLUTION_ASSETS)

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ROOTSMARKT_CONFIG", raising=False)
    return tmp_path


def _run(cwd, *args):
    return subprocess.run(
        [sys.executable, "-c", "from roots.main import main; main()", *args],
        cwd=cwd, capture_output=True, text=True,
    )


def test_solution_loads_then_restore_puts_the_stubs_back(repo):
    dag = repo / "dags" / "rootsmarkt_delivery.py"
    assets = repo / "dagster" / "src" / "rootsmarkt" / "defs" / "assets.py"

    assert _run(repo, "solution", "all").returncode == 0
    assert dag.read_text() == SOLUTION_DAG
    assert assets.read_text() == SOLUTION_ASSETS

    assert _run(repo, "solution", "all", "--restore").returncode == 0
    assert dag.read_text() == STUB_DAG
    assert assets.read_text() == STUB_ASSETS, (
        "the Dagster stub is all comments -- a marker-based backup check skips it"
    )


def test_loading_twice_does_not_overwrite_the_saved_stubs(repo):
    """The instructor rehearses, loads again, and must still be able to get back."""
    _run(repo, "solution", "all")
    _run(repo, "solution", "all")
    _run(repo, "solution", "all", "--restore")
    assert (repo / "dags" / "rootsmarkt_delivery.py").read_text() == STUB_DAG


def test_restore_without_a_backup_fails_loudly(repo):
    result = _run(repo, "solution", "all", "--restore")
    assert result.returncode == 1
    assert "nothing to restore" in result.stdout + result.stderr


def test_unknown_target_is_rejected(repo):
    result = _run(repo, "solution", "sparkle")
    assert result.returncode == 2
    assert (repo / "dags" / "rootsmarkt_delivery.py").read_text() == STUB_DAG
