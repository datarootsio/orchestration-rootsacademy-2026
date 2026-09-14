"""Local progress state and the offline queue.

`.roots/progress.json` is written on every check, always, before anything is
sent anywhere. That is what makes the course survive losing the dashboard
(CLAUDE.md requires it): teams keep working and can still see where they are.

Milestones are monotonic here too -- a transient check failure must never make a
team look like it regressed.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

STATE_DIR = Path(".roots")
PROGRESS = STATE_DIR / "progress.json"
QUEUE = STATE_DIR / "queue.jsonl"


def _read(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def progress() -> dict:
    data = _read(PROGRESS, {"earned": {}, "reported": [], "last_run": None})
    data.setdefault("reported", [])   # files written before reporting was tracked
    data.setdefault("hints", {})      # mission -> how many hints have been shown
    data.setdefault("adopted", {})    # mission -> when a checkpoint was adopted
    return data


def _write(data: dict) -> None:
    STATE_DIR.mkdir(exist_ok=True)
    PROGRESS.write_text(json.dumps(data, indent=2))


def hints_shown(mission: str) -> int:
    return int(progress()["hints"].get(mission, 0))


def record_hint(mission: str, shown: int) -> None:
    """Remember how far into a mission's hints this team has gone.

    Kept so `roots hint a2` twice gives hint 1 then hint 2, rather than the same
    one forever -- and so the instructor can see, in `roots status`, which
    missions the room actually found hard.
    """
    data = progress()
    data["hints"][mission] = max(shown, int(data["hints"].get(mission, 0)))
    _write(data)


def adopted() -> dict:
    return dict(progress()["adopted"])


def record_adoption(mission: str, backup: str) -> bool:
    """Record that a checkpoint was adopted. Returns True if this is the first.

    Adoption is visible, not punished (D-048). A team that takes a checkpoint has
    made a reasonable call about their remaining time; what matters is that the
    instructor can see it on the board rather than discovering it in the debrief.
    """
    data = progress()
    first = mission not in data["adopted"]
    if first:
        data["adopted"][mission] = {"at": time.time(), "backup": backup}
    _write(data)
    return first


def reported() -> set[str]:
    """Milestones the lab server has acknowledged.

    Tracked separately from `earned` because the two can diverge: a team may
    have earned something locally while pointed at a different lab server, or
    while the server was unreachable and the queue was later lost. Reporting on
    "newly earned locally" alone meant such a milestone was never sent again and
    stayed missing from the dashboard forever.
    """
    return set(progress()["reported"])


def mark_reported(name: str) -> None:
    STATE_DIR.mkdir(exist_ok=True)
    data = progress()
    if name not in data["reported"]:
        data["reported"].append(name)
    PROGRESS.write_text(json.dumps(data, indent=2))


def record(name: str, detail: str = "") -> bool:
    """Mark a milestone locally. Returns True if newly earned."""
    STATE_DIR.mkdir(exist_ok=True)
    data = progress()
    new = name not in data["earned"]
    if new:
        data["earned"][name] = {"at": time.time(), "detail": detail}
    data["last_run"] = time.time()
    PROGRESS.write_text(json.dumps(data, indent=2))
    return new


def earned() -> set[str]:
    return set(progress()["earned"])


def enqueue(name: str, payload: dict) -> None:
    """Hold a milestone that could not be sent, for flushing on reconnect."""
    STATE_DIR.mkdir(exist_ok=True)
    with QUEUE.open("a") as fh:
        fh.write(json.dumps({"milestone": name, "payload": payload, "at": time.time()}) + "\n")


def drain_queue() -> list[dict]:
    if not QUEUE.exists():
        return []
    items = [json.loads(line) for line in QUEUE.read_text().splitlines() if line.strip()]
    QUEUE.unlink()
    return items
