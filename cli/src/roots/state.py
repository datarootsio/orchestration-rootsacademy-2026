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
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def progress() -> dict:
    data = _read(PROGRESS, {"earned": {}, "reported": [], "last_run": None})
    data.setdefault("reported", [])   # files written before reporting was tracked
    data.setdefault("hints", {})      # mission -> how many hints have been shown
    data.setdefault("adopted", {})    # mission -> when a checkpoint was adopted
    data.setdefault("submissions", {})  # milestone -> the evidence, for re-sending
    data.setdefault("team_id", None)    # whose progress this is; see clear_for_team()
    return data


def _write(data: dict) -> None:
    STATE_DIR.mkdir(exist_ok=True)
    PROGRESS.write_text(json.dumps(data, indent=2), encoding="utf-8")


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
    PROGRESS.write_text(json.dumps(data, indent=2), encoding="utf-8")


def joined_team() -> str | None:
    """Which team this machine's progress belongs to."""
    return progress().get("team_id")


def clear_for_team(team_id: str) -> bool:
    """Bind local progress to `team_id`, wiping it if it belonged to another.

    `.roots/progress.json` had no team in it at all, and `roots join` never
    touched it -- so joining a different team carried the previous team's
    milestones across and reported them under the new identity. A team that
    mistypes a join code, joins the wrong team and then re-joins correctly would
    take the wrong team's progress with them.

    Hints and checkpoint adoptions are MACHINE facts, not team facts: which
    hints this laptop has read does not belong to whoever it reports as. They
    survive.

    Returns True if progress was wiped, so the caller can say so.
    """
    data = progress()
    previous = data.get("team_id")
    if previous == team_id:
        return False
    if previous is None and not data["earned"]:
        data["team_id"] = team_id          # first join on a clean machine
        _write(data)
        return False
    data.update({
        "team_id": team_id,
        "earned": {},
        "reported": [],
        "submissions": {},
        "last_run": None,
    })
    _write(data)
    QUEUE.unlink(missing_ok=True)
    return True


def clear_progress() -> dict:
    """Forget every milestone this machine has earned. Returns what was dropped.

    The client half of `lab reset`, which never existed. It matters more since
    milestones re-sync (D-062): a server-side reset alone no longer clears a
    board, because any machine still holding local state re-reports into it.

    Hints and adoptions survive, for the same reason as above.
    """
    data = progress()
    dropped = {"earned": len(data["earned"]), "submissions": len(data.get("submissions", {}))}
    data.update({"earned": {}, "reported": [], "submissions": {}, "last_run": None})
    _write(data)
    QUEUE.unlink(missing_ok=True)
    return dropped


def forget_reported(names) -> list[str]:
    """Drop milestones the lab server no longer has, so they are sent again.

    `reported` is the client's record of what the server CONFIRMED. `lab reset`
    can take those rows back, and without this the client would keep skipping
    them forever -- earned locally, absent from the dashboard, unreachable by any
    amount of re-running (D-062).

    Returns what was forgotten, so the caller can say so out loud rather than
    healing silently.
    """
    data = progress()
    forgotten = [n for n in data["reported"] if n in set(names)]
    if forgotten:
        data["reported"] = [n for n in data["reported"] if n not in set(forgotten)]
        _write(data)
    return forgotten


def remember_submission(name: str, payload: dict) -> None:
    """Keep a submission's evidence so it can be re-sent after a server reset.

    Client checks re-derive themselves from the pipeline; submissions cannot --
    the evidence was typed once by a human. Storing only the word "submitted"
    meant a reset destroyed it permanently.
    """
    data = progress()
    data.setdefault("submissions", {})[name] = payload
    _write(data)


def submissions() -> dict:
    return dict(progress().get("submissions", {}))


def record(name: str, detail: str = "") -> bool:
    """Mark a milestone locally. Returns True if newly earned."""
    STATE_DIR.mkdir(exist_ok=True)
    data = progress()
    new = name not in data["earned"]
    if new:
        data["earned"][name] = {"at": time.time(), "detail": detail}
    data["last_run"] = time.time()
    PROGRESS.write_text(json.dumps(data, indent=2), encoding="utf-8")
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
    items = [json.loads(line) for line in QUEUE.read_text(encoding="utf-8").splitlines() if line.strip()]
    QUEUE.unlink()
    return items
