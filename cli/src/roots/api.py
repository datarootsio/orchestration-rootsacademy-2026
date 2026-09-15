"""Progress API client. Never fatal: a lab server outage must not stop a team."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path


class LabUnreachable(RuntimeError):
    pass


# Written by `lab host` before the student repo is built, so the repository a
# team clones already knows where the lab is and `roots join <code>` needs
# nothing else. One fewer thing to read off a projector and mistype.
BAKED_LAB_URL = Path("config/lab-url")

DEFAULT_LAB_URL = "http://localhost:8090"


def lab_url(override: str | None = None) -> str:
    """Where the lab server is, in order of precedence:

        --lab-url  >  ROOTS_LAB_URL  >  LAB_URL env  >  .env  >  config/lab-url  >  localhost

    `.env` deliberately beats `config/lab-url`. `config/lab-url` is what the repo
    shipped with; `.env` is where the team currently is. A team told to run
    `roots online --lab-url ...` mid-course must stay pointed at the new address,
    not snap back to the baked-in one on the next command.

    The localhost fallback is last and is only ever right for whoever hosts the
    lab. Before this file existed it was the default for everyone, so every
    participant needed `--lab-url` on a command the README did not show it on.
    """
    if override:
        return override.rstrip("/")
    for key in ("ROOTS_LAB_URL", "LAB_URL"):
        if os.environ.get(key):
            return os.environ[key].rstrip("/")
    env = Path(".env")
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("LAB_URL="):
                return line.split("=", 1)[1].strip().rstrip("/")
    baked = _baked_lab_url()
    if baked:
        return baked
    return DEFAULT_LAB_URL


def _baked_lab_url() -> str | None:
    """The address the repo was built with, if any. Never fatal."""
    for candidate in (BAKED_LAB_URL, *(p / BAKED_LAB_URL for p in Path.cwd().parents)):
        try:
            if not candidate.is_file():
                continue
            for line in candidate.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    return stripped.rstrip("/")
        except OSError:
            continue
    return None


def _call(method: str, url: str, token: str = "", body: dict | None = None, timeout: float = 10.0):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    if token:
        headers["X-SupplyHub-Token"] = token
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if not raw:
                return {}
            try:
                return json.loads(raw)
            except ValueError:
                # Not every endpoint speaks JSON -- /healthz returns plain text.
                # Returning it rather than raising keeps a non-JSON response from
                # escaping as a JSONDecodeError, which is not a LabUnreachable and
                # so slipped past every caller's error handling.
                return {"raw": raw.decode(errors="replace")}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        raise LabUnreachable(f"HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LabUnreachable(f"cannot reach the lab server at {url}: {exc}") from exc


def fetch_config(join_code: str, base: str) -> dict:
    return _call("GET", f"{base}/v1/config/{join_code.upper()}")


def post_milestone(base: str, team_id: str, token: str, name: str, payload: dict) -> dict:
    return _call("POST", f"{base}/v1/teams/{team_id}/milestones/{name}", token, payload)


def post_adoption(base: str, team_id: str, token: str, mission: str) -> dict:
    """Tell the lab server a team adopted a checkpoint.

    An annotation, not a milestone: it never enters the milestone grid, and the
    dashboard shows it beside that mission rather than as progress. Adoption is
    made visible so the instructor can see who needed help -- see D-048.
    """
    return _call("POST", f"{base}/v1/teams/{team_id}/adoptions/{mission}", token, {})


def healthy(base: str) -> bool:
    """True if the lab server answers. Never raises -- `roots doctor` calls this,
    and a health probe that can take down the pre-flight tool is worse than no
    probe at all."""
    try:
        _call("GET", f"{base}/healthz", timeout=4.0)
        return True
    except Exception:  # noqa: BLE001 - deliberate: this must never propagate
        return False
