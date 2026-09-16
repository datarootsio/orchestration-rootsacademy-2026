"""SupplyHub client. The boundary between the orchestrator and the outside world.

This module exists so that no mission text depends on an API we have not
verified. `client_from_airflow_conn` hides Airflow 3.3's connection accessor
behind one function: participants learn the *concept* (credentials live in the
orchestrator's connection store, not in pipeline code) without the mission
hinging on an exact import path.
"""

from __future__ import annotations

import os
import urllib.error
import urllib.parse
import urllib.request
import json
from dataclasses import dataclass
from pathlib import Path


class SupplyHubError(RuntimeError):
    pass


@dataclass(frozen=True)
class DeliveryMeta:
    delivery_id: str
    business_date: str
    revision: int
    published_at: str
    row_count: int
    store_count: int
    checksum_sha256: str
    download_url: str

    @classmethod
    def from_json(cls, d: dict) -> "DeliveryMeta":
        return cls(
            delivery_id=d["delivery_id"],
            business_date=d["business_date"],
            revision=d.get("revision", 1),
            published_at=d.get("published_at", ""),
            row_count=d["row_count"],
            store_count=d["store_count"],
            checksum_sha256=d["checksum_sha256"],
            download_url=d["download_url"],
        )


class SupplyHubClient:
    def __init__(self, base_url: str, token: str, team_id: str, timeout: float = 20.0):
        if not base_url or not token or not team_id:
            raise SupplyHubError("base_url, token and team_id are all required")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.team_id = team_id
        self.timeout = timeout

    def _request(self, path: str) -> tuple[bytes, dict]:
        url = f"{self.base_url}{path}"
        req = urllib.request.Request(url, headers={"X-SupplyHub-Token": self.token})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.read(), dict(resp.headers)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")[:300]
            raise SupplyHubError(f"SupplyHub {exc.code} for {path}: {body}") from exc
        except urllib.error.URLError as exc:
            raise SupplyHubError(
                f"cannot reach SupplyHub at {self.base_url}: {exc.reason}. "
                "If the lab server is down, run `roots offline`."
            ) from exc

    def get_delivery(self, business_date: str | None = None) -> DeliveryMeta:
        """Metadata for a delivery.

        NOTE: a 200 response does not mean you were given the date you asked for.
        Check what came back.
        """
        path = f"/v1/deliveries/{self.team_id}"
        if business_date:
            path += "?" + urllib.parse.urlencode({"business_date": business_date})
        raw, _ = self._request(path)
        return DeliveryMeta.from_json(json.loads(raw))

    def download(self, meta: DeliveryMeta, dest: str | Path) -> Path:
        raw, _ = self._request(meta.download_url)
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw)
        return dest


def client_from_env() -> SupplyHubClient:
    """Build a client from environment variables. Used by the Dagster assets."""
    return SupplyHubClient(
        base_url=os.environ.get("SUPPLYHUB_BASE_URL", ""),
        token=os.environ.get("SUPPLYHUB_TOKEN", ""),
        team_id=os.environ.get("TEAM_ID", ""),
    )


def client_from_airflow_conn(conn_id: str = "supplyhub") -> SupplyHubClient:
    """Build a client from an Airflow connection.

    The token is read from the connection's password, or from `extra.token`.
    Participants learn the concept -- credentials live in the orchestrator's
    connection store, not in pipeline code -- without the mission depending on an
    exact import path.
    """
    from .airflow_conn import extra_dict, get_connection

    conn = get_connection(conn_id)
    extra = extra_dict(conn)

    return SupplyHubClient(
        base_url=_base_url_from_conn(conn_id, conn, extra),
        token=conn.password or extra.get("token", ""),
        team_id=os.environ.get("TEAM_ID", "") or extra.get("team_id", ""),
    )


def _base_url_from_conn(conn_id: str, conn, extra: dict) -> str:
    """Work out SupplyHub's base URL from an Airflow connection.

    Accepts a full URL in Host (the documented recipe), a bare host combined
    with Schema/Port, or a `base_url` in Extra.

    Raises rather than guessing when there is no host. An earlier version built
    f"http://{host}" unconditionally, so an empty Host produced the base URL
    "http:" -- which surfaced four frames later as
    `URLError: <urlopen error no host given>`, pointing at urllib rather than at
    the connection. A setup mistake has to be reported where it can be fixed.
    """
    host = (getattr(conn, "host", "") or "").strip()
    schema = (getattr(conn, "schema", "") or "").strip()
    port = getattr(conn, "port", None)

    if not host:
        host = str(extra.get("base_url") or extra.get("host") or "").strip()

    if not host:
        raise SupplyHubError(
            f"Airflow connection {conn_id!r} has no host, so there is nothing to call.\n"
            f"  conn_type={getattr(conn, 'conn_type', None)!r} host={getattr(conn, 'host', None)!r} "
            f"port={port!r} schema={schema!r} extra_keys={sorted(extra)}\n"
            "  Expected Host to be the full URL, e.g. http://host.docker.internal:8090 "
            "(run `roots airflow-conn` for the exact values)."
        )

    if "://" in host:
        return host.rstrip("/")

    scheme = schema if schema in ("http", "https") else ("https" if port == 443 else "http")
    authority = f"{host}:{port}" if port else host
    return f"{scheme}://{authority}".rstrip("/")
