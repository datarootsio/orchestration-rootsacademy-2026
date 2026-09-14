"""Resolving SupplyHub's base URL from an Airflow connection.

Regression tests for a real failure in mission A1. The connection came back with
no host, and the helper built `f"http://{host}"` unconditionally -- so the base
URL became `"http://"`, `.rstrip("/")` made it `"http:"`, and the participant saw:

    URLError: <urlopen error no host given>

four frames deep in urllib. A setup mistake has to be reported where it can be
fixed, naming what Airflow actually returned.
"""

from __future__ import annotations

import pytest

from rootsmarkt_processing.source import SupplyHubError, _base_url_from_conn


class Conn:
    """Stand-in for an Airflow Connection, defaulting everything to empty."""

    def __init__(self, **kw):
        self.conn_type = "generic"
        self.host = None
        self.schema = None
        self.port = None
        self.password = None
        for k, v in kw.items():
            setattr(self, k, v)


@pytest.mark.parametrize(
    "conn,extra,expected",
    [
        pytest.param(
            Conn(host="http://host.docker.internal:8090"), {},
            "http://host.docker.internal:8090", id="full-url-in-host",
        ),
        pytest.param(
            Conn(host="http://host.docker.internal:8090/"), {},
            "http://host.docker.internal:8090", id="trailing-slash-trimmed",
        ),
        pytest.param(
            Conn(host="host.docker.internal", port=8090, schema="http"), {},
            "http://host.docker.internal:8090", id="host-port-schema",
        ),
        pytest.param(
            Conn(host="lab.example.com", port=443), {},
            "https://lab.example.com:443", id="port-443-implies-https",
        ),
        pytest.param(
            Conn(host="lab.example.com"), {},
            "http://lab.example.com", id="bare-host-defaults-to-http",
        ),
        pytest.param(
            Conn(), {"base_url": "http://lab.example.com:8090"},
            "http://lab.example.com:8090", id="from-extra-base-url",
        ),
        pytest.param(
            Conn(), {"host": "http://lab.example.com:8090"},
            "http://lab.example.com:8090", id="from-extra-host",
        ),
        pytest.param(
            Conn(host="  http://x:8090  "), {}, "http://x:8090", id="whitespace-stripped",
        ),
    ],
)
def test_resolves_every_reasonable_shape(conn, extra, expected):
    assert _base_url_from_conn("supplyhub", conn, extra) == expected


def test_missing_host_raises_instead_of_inventing_a_url():
    """THE regression. Never produce "http:" and defer the failure to urllib."""
    with pytest.raises(SupplyHubError) as exc:
        _base_url_from_conn("supplyhub", Conn(), {})
    message = str(exc.value)

    # names the connection and what was actually found, so it is fixable on sight
    assert "supplyhub" in message
    assert "conn_type='generic'" in message
    assert "host=None" in message
    # and points at the remedy
    assert "roots airflow-conn" in message
    assert "http://host.docker.internal:8090" in message


def test_blank_host_is_treated_as_missing():
    with pytest.raises(SupplyHubError):
        _base_url_from_conn("supplyhub", Conn(host="   "), {})


def test_never_returns_a_url_without_a_host():
    """Belt and braces: whatever the input, the result must have a netloc."""
    from urllib.parse import urlsplit

    for conn, extra in [
        (Conn(host="http://x:1"), {}),
        (Conn(host="x", port=1), {}),
        (Conn(), {"base_url": "http://x"}),
    ]:
        assert urlsplit(_base_url_from_conn("c", conn, extra)).netloc


# --------------------------------------------------------- warehouse DSN
#
# Regression: `load_warehouse` read ROOTSMARKT_DSN from the environment. That
# env var is correct on the HOST (Dagster, roots) but points at `localhost`,
# which inside the Airflow container is the container itself:
#
#   connection to server at "127.0.0.1", port 5432 failed: Connection refused
#
# Meanwhile `ensure_warehouse_table` was already reaching Postgres through the
# Airflow connection. Two routes to one database, only one of them right.


def test_dsn_is_built_from_the_airflow_connection(monkeypatch):
    from rootsmarkt_processing import load

    conn = Conn(
        host="host.docker.internal", port=5432, schema="rootsmarkt",
        login="team_00", password="s3cr3t",
    )
    monkeypatch.setattr(
        "rootsmarkt_processing.airflow_conn.get_connection", lambda cid: conn
    )
    dsn = load.dsn_from_airflow_conn("rootsmarkt_dw")
    assert dsn == "postgresql://team_00:s3cr3t@host.docker.internal:5432/rootsmarkt"
    # the container view, never the host's localhost
    assert "localhost" not in dsn and "127.0.0.1" not in dsn


def test_dsn_defaults_the_port_and_quotes_credentials(monkeypatch):
    from rootsmarkt_processing import load

    conn = Conn(
        host="db.internal", port=None, schema="rootsmarkt",
        login="team/00", password="p@ss:word",
    )
    monkeypatch.setattr(
        "rootsmarkt_processing.airflow_conn.get_connection", lambda cid: conn
    )
    dsn = load.dsn_from_airflow_conn()
    assert ":5432/" in dsn
    # a password containing @ or : must not corrupt the DSN
    assert "p%40ss%3Aword" in dsn and "team%2F00" in dsn


@pytest.mark.parametrize(
    "missing,label",
    [("host", "host"), ("schema", "database"), ("login", "login")],
)
def test_incomplete_connection_is_reported_not_guessed(monkeypatch, missing, label):
    from rootsmarkt_processing import load

    fields = dict(
        host="db.internal", port=5432, schema="rootsmarkt",
        login="team_00", password="pw",
    )
    fields[missing] = None
    monkeypatch.setattr(
        "rootsmarkt_processing.airflow_conn.get_connection", lambda cid: Conn(**fields)
    )
    with pytest.raises(RuntimeError) as exc:
        load.dsn_from_airflow_conn("rootsmarkt_dw")
    assert label in str(exc.value)
    assert "rootsmarkt_dw" in str(exc.value)


def test_connection_description_never_leaks_the_password():
    from rootsmarkt_processing.airflow_conn import describe

    text = describe("rootsmarkt_dw", Conn(host="h", login="u", password="SUPERSECRET"))
    assert "SUPERSECRET" not in text
    assert "password=set" in text
