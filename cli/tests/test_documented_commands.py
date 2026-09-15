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
    REPO / "CONCEPTS.md",
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


# ------------------------------------- the instructor guide names real things
#
# The milestone register changed twice in one week -- `stale_delivery_blocked`
# was added, and every A2 label shifted. A guide naming a milestone that is not
# on the board sends an instructor hunting for a row that does not exist, in the
# middle of a session.

GUIDE_DOCS = [REPO / "docs" / "instructor-guide.md", REPO / "docs" / "runsheet.md"]


def test_the_guides_exist():
    for doc in GUIDE_DOCS:
        assert doc.is_file(), f"{doc.name} is missing"


def test_every_milestone_named_in_the_guides_exists():
    from lab_server.world import MILESTONE_NAMES

    # Only look at snake_case identifiers that LOOK like milestone names, so
    # ordinary prose cannot trip this.
    candidates = set()
    for doc in GUIDE_DOCS:
        for word in re.findall(r"`([a-z][a-z0-9_]{6,})`", doc.read_text(encoding="utf-8")):
            if "_" in word and not word.endswith((".py", ".md", ".yaml")):
                candidates.add(word)

    known = set(MILESTONE_NAMES)

    # Asset and task names legitimately share stems with milestones --
    # `raw_delivery` (an asset) vs `raw_delivery_landed` (a milestone). Sourced
    # rather than hardcoded so a rename cannot leave this stale.
    from roots.probes.dagster_probes import EXPECTED_ASSETS

    not_milestones = set(EXPECTED_ASSETS) | {
        "fetch_delivery", "assert_delivery_is_current", "load_warehouse",
        "validate_load", "publish_daily_revenue", "ensure_warehouse_table",
        "clean_sales", "daily_revenue", "revenue_is_plausible",
        "expected_stores", "is_plausible", "total_revenue_eur",
        "load_daily_revenue", "business_date", "delivery_id", "row_count",
        "received_date", "requested_date", "container_binary",
    }

    # Anything sharing a stem with a real milestone is being used AS one.
    stems = {n.split("_")[0] for n in known}
    suspects = {c for c in candidates if c.split("_")[0] in stems}
    offenders = sorted(suspects - known - not_milestones)
    assert not offenders, (
        f"named like milestones but not in the register: {offenders}. "
        f"Known: {sorted(known)}"
    )


def test_the_runsheet_stays_one_page():
    """It is held in one hand while ten teams need something. A runsheet that
    needs scrolling is a document, and there is already a document."""
    lines = (REPO / "docs" / "runsheet.md").read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 80, f"runsheet is {len(lines)} lines -- trim it or move it to the guide"


def test_the_guides_point_at_documents_that_exist():
    for doc in GUIDE_DOCS:
        for target in re.findall(r"\]\((?!https?:)([^)#]+)", doc.read_text(encoding="utf-8")):
            assert (doc.parent / target).resolve().exists(), f"{doc.name} -> {target} is a dead link"


# ------------------------------------------------- CONCEPTS.md is concepts only

CONCEPTS = REPO / "CONCEPTS.md"


def test_concepts_ships_to_participants():
    assert CONCEPTS.is_file()


def test_concepts_has_no_code_blocks():
    """The brief was concepts, not syntax. `CLAUDE.md` is explicit that the
    course is not optimised for syntax memorisation, and the code a participant
    needs is in the file they are editing -- a second, drifting copy here would
    be worse than none."""
    text = CONCEPTS.read_text(encoding="utf-8")
    assert "```" not in text, "CONCEPTS.md must stay prose -- no code blocks"


def test_concepts_does_not_contradict_the_shipped_dagster_stub():
    """The stub tells participants dependencies are DECLARED, not inferred from a
    function parameter -- these assets return MaterializeResult, so there is no
    value to pass (D-003).

    `course-design.md` taught the opposite until today. If CONCEPTS.md ever drifts
    the same way, a participant reads the wrong thing in the one document they are
    told to keep open.
    """
    text = CONCEPTS.read_text(encoding="utf-8").lower()
    assert "declared" in text and "infer" in text, (
        "CONCEPTS.md must explain that Dagster dependencies are declared here, "
        "not inferred -- it is the first thing D1 goes wrong on"
    )
    stub = (REPO / "dagster" / "src" / "rootsmarkt" / "defs" / "assets.py").read_text(
        encoding="utf-8"
    )
    assert "NOT by taking" in stub, "the stub no longer warns about this; re-check CONCEPTS.md"
