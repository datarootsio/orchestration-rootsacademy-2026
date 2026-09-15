"""Participant-facing docs must name commands that exist.

Added after `missions/A2.md` told participants to run `roots verify a2`, which
errored with "no checks for mission(s) a2" -- A2 being graded by submission at
the time. A participant reasonably reads that as a broken tool, at exactly the
moment they are already stuck.

Lives here rather than beside `lab/tests/test_documented_commands.py`, which
tests that INSTRUCTOR commands survive being pasted: these need `roots`
importable, and the lab package does not depend on the CLI.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

PARTICIPANT_DOCS = [
    *sorted((REPO / "missions").glob("*.md")),
    REPO / "README.md",
    REPO / "WINDOWS-SETUP.md",
]


def _roots_invocations() -> list[tuple[str, str, str]]:
    """(doc, subcommand, full line) for every `roots ...` in the participant docs."""
    out = []
    for doc in PARTICIPANT_DOCS:
        if not doc.exists():
            continue
        for match in re.finditer(r"^\s*(?:uv run [^\n]*?)?roots ([a-z-]+)([^\n]*)$",
                                 doc.read_text(encoding="utf-8"), re.M):
            out.append((doc.name, match.group(1), match.group(0).strip()))
    return out


def test_there_are_participant_commands_to_check():
    assert len(_roots_invocations()) > 15, "the sweep found almost nothing -- check the regex"


def test_every_documented_roots_subcommand_exists():
    from roots.main import app

    known = {c.name or c.callback.__name__ for c in app.registered_commands}
    known |= {g.name for g in app.registered_groups}
    # `solution` and `offline` register only where solutions/ and lab/ exist, so
    # they are known here (the instructor repo) but absent from the student repo.
    offenders = [
        f"{doc}: {line}"
        for doc, sub, line in _roots_invocations()
        if sub not in known and sub != "--help"
    ]
    assert not offenders, "documented commands that do not exist:\n  " + "\n  ".join(offenders)


def test_every_documented_verify_target_has_checks():
    """`roots verify <mission>` must have something to run.

    This is the exact failure that prompted these tests: A2, D3 and D4 are
    graded by submission, so `verify` had nothing for them -- while two mission
    briefs told participants to run it anyway.
    """
    from roots import checks

    offenders = []
    for doc, sub, line in _roots_invocations():
        if sub != "verify":
            continue
        target = line.split("verify", 1)[1].strip().split()[0] if "verify" in line else ""
        if not target or target.startswith(("-", "<", "[")):
            continue                      # bare `roots verify` runs everything
        if not checks.for_mission(target):
            offenders.append(
                f"{doc}: `{line}` -- no checks for {target.upper()}. "
                "It is graded by submission; point at `roots submit` instead."
            )
    assert not offenders, "\n  " + "\n  ".join(offenders)


def test_every_documented_submit_target_exists():
    from roots.main import submit_app

    known = {c.name or c.callback.__name__ for c in submit_app.registered_commands}
    offenders = []
    for doc, sub, line in _roots_invocations():
        if sub != "submit":
            continue
        target = line.split("submit", 1)[1].strip().split()[0]
        if target and not target.startswith(("-", "<", "[")) and target not in known:
            offenders.append(f"{doc}: `{line}` -- no `roots submit {target}`")
    assert not offenders, "\n  " + "\n  ".join(offenders)
