"""`tests/dags/` runs INSIDE the Airflow container (`astro dev pytest`).

Airflow is deliberately not in the host environment (D-002: Airflow lives only in
the Astro image, Dagster only in the host venv), so collecting these on the host
would fail at import. Skipping keeps `uv run pytest` from the repo root green
without hiding anything: `astro dev pytest` is where these actually run, and the
skip reason says so.
"""

import pytest

pytest.importorskip(
    "airflow",
    reason="Airflow lives in the Astro image, not the host venv -- run `astro dev pytest`",
)
