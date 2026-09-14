"""Client-side tests for the lab API.

These exist because of a real escape. `/healthz` returns plain text, `_call` did
`json.loads` unconditionally, and the resulting JSONDecodeError was not a
LabUnreachable -- so it sailed past `healthy()`'s handler and crashed
`roots doctor`.

The lab test suite covered `/healthz` from the *server* side and was perfectly
green throughout. Only a test that drives the client catches this class of bug,
which is why these run the real app over a real socket.
"""

from __future__ import annotations

import threading
from contextlib import closing
import socket

import pytest
import uvicorn

from lab_server import api as lab_api
from lab_server.store import Store
from roots import api

ADMIN = "test-admin"


def _free_port() -> int:
    with closing(socket.socket()) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    """A real uvicorn on a real port. TestClient would not exercise urllib."""
    state = tmp_path_factory.mktemp("state") / "lab.sqlite3"
    lab_api._store = Store(state)
    lab_api.ADMIN_TOKEN = ADMIN
    port = _free_port()

    config = uvicorn.Config(lab_api.app, host="127.0.0.1", port=port, log_level="error")
    srv = uvicorn.Server(config)
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()

    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        if srv.started:
            break
        threading.Event().wait(0.05)
    else:  # pragma: no cover
        pytest.fail("uvicorn did not start")

    lab_api.store().provision(2)
    yield base
    srv.should_exit = True
    thread.join(timeout=5)


# ------------------------------------------------------- the regression itself


def test_healthy_returns_true_against_plain_text_healthz(server):
    """THE regression test. /healthz returns "ok", not JSON."""
    assert api.healthy(server) is True


def test_healthz_body_is_not_json(server):
    """Proves the endpoint really is plain text, so the test above has teeth."""
    result = api._call("GET", f"{server}/healthz")
    assert result == {"raw": "ok"}


def test_healthy_returns_false_when_unreachable():
    assert api.healthy("http://127.0.0.1:9") is False


def test_healthy_never_raises_on_a_malformed_url():
    """`roots doctor` calls this; it must not be able to take the tool down."""
    for bad in ("not-a-url", "http://", "http://nonexistent.invalid:8090"):
        assert api.healthy(bad) is False


# --------------------------------------------------------------- _call shapes


def test_call_parses_json(server):
    body = api._call("GET", f"{server}/v1/state")
    assert isinstance(body, dict)
    assert len(body["milestones"]) == 14


def test_call_raises_lab_unreachable_on_http_error(server):
    with pytest.raises(api.LabUnreachable, match="HTTP 404"):
        api._call("GET", f"{server}/v1/config/ZZZZZZ")


def test_call_raises_lab_unreachable_on_connection_refused():
    with pytest.raises(api.LabUnreachable, match="cannot reach"):
        api._call("GET", "http://127.0.0.1:9/healthz")


# ------------------------------------------------------------------ real flow


def test_fetch_config_and_post_milestone(server):
    team = lab_api.store().teams()[0]
    cfg = api.fetch_config(team["join_code"], server)
    assert cfg["team_id"] == team["team_id"]
    assert len(cfg["expected_stores"]) == 12
    assert "dsn" in cfg["warehouse"]

    resp = api.post_milestone(
        server, cfg["team_id"], cfg["supplyhub_token"], "warehouse_loaded", {}
    )
    assert resp["accepted"] is True


def test_post_milestone_with_a_bad_token_is_unreachable_not_a_crash(server):
    with pytest.raises(api.LabUnreachable, match="HTTP 401"):
        api.post_milestone(server, "team-00", "wrong-token", "warehouse_loaded", {})


# --------------------------------------------------------------- lab_url rules


def test_lab_url_precedence(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ROOTS_LAB_URL", raising=False)
    monkeypatch.delenv("LAB_URL", raising=False)
    assert api.lab_url() == "http://localhost:8090"

    (tmp_path / ".env").write_text("LAB_URL=http://from-env-file:1/\n")
    assert api.lab_url() == "http://from-env-file:1"

    monkeypatch.setenv("ROOTS_LAB_URL", "http://from-environ:2")
    assert api.lab_url() == "http://from-environ:2"

    assert api.lab_url("http://explicit:3/") == "http://explicit:3"
