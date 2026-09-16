"""Reading Airflow connections, in one place.

The Task SDK moved `BaseHook` between Airflow 2 and 3, and this is the only
version-fragile API the course depends on. Keeping the import chain here means
no mission text hinges on an exact import path, and there is a single place to
fix if it moves again.

Everything here is ORCHESTRATION, not data processing: it is about where
credentials live, not about what the data means. It sits in this package only so
the DAG files stay free of version archaeology.
"""

from __future__ import annotations

import json

_IMPORT_CANDIDATES = (
    ("airflow.sdk", "BaseHook"),
    ("airflow.hooks.base", "BaseHook"),
)


class ConnectionUnavailable(RuntimeError):
    pass


def get_connection(conn_id: str):
    """Fetch an Airflow connection, or explain what was tried."""
    errors = []
    for module, attr in _IMPORT_CANDIDATES:
        try:
            mod = __import__(module, fromlist=[attr])
            return getattr(mod, attr).get_connection(conn_id)
        except Exception as exc:  # noqa: BLE001 - genuinely want the next candidate
            errors.append(f"{module}.{attr}: {exc}")
    raise ConnectionUnavailable(
        f"could not load Airflow connection {conn_id!r}. Tried: " + "; ".join(errors)
    )


def extra_dict(conn) -> dict:
    """A connection's Extra as a dict, tolerating blank or malformed JSON."""
    raw = getattr(conn, "extra", None)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def describe(conn_id: str, conn, extra: dict | None = None) -> str:
    """What a connection actually contains, for error messages.

    Never includes the password. A participant needs to see which fields are
    empty, not the secret.
    """
    extra = extra if extra is not None else extra_dict(conn)
    return (
        f"conn_id={conn_id!r} conn_type={getattr(conn, 'conn_type', None)!r} "
        f"host={getattr(conn, 'host', None)!r} port={getattr(conn, 'port', None)!r} "
        f"schema={getattr(conn, 'schema', None)!r} login={getattr(conn, 'login', None)!r} "
        f"password={'set' if getattr(conn, 'password', None) else 'EMPTY'} "
        f"extra_keys={sorted(extra)}"
    )
