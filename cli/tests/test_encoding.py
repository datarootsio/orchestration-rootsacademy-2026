"""Course text must survive a non-UTF-8 locale.

Every `missions/*.md` and `hints/*/*.md` file contains at least one of
em dash, EUR sign, arrow, ellipsis or en dash. `Path.read_text()` with no
encoding uses the platform's locale encoding, which on a Belgian Windows install
is cp1252.

That does not crash -- which is why it needed a test. It silently produces
mojibake: `**A4 - hint 1 of 2**` renders as `**A4 a<euro>" hint 1 of 2**`. A
participant reads that as a corrupt repository rather than a locale mismatch, and
there is nothing in the output to tell them otherwise.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from roots import main

REPO = Path(__file__).resolve().parents[2]
HINTS = REPO / "hints"
MISSIONS = REPO / "missions"

# Every non-ASCII character the shipped course text actually uses. All five
# misdecode under cp1252, so any one of them is enough to prove the point.
COURSE_GLYPHS = ("—", "€", "→", "…", "–")


@pytest.mark.skipif(not HINTS.is_dir(), reason="hints/ not in this checkout")
def test_hints_are_utf8_and_contain_the_glyphs_that_break_cp1252():
    """Guards the premise. If the course text were pure ASCII these tests would
    pass while proving nothing."""
    text = "".join(p.read_text(encoding="utf-8") for p in HINTS.rglob("*.md"))
    assert any(g in text for g in COURSE_GLYPHS)


@pytest.mark.skipif(not HINTS.is_dir(), reason="hints/ not in this checkout")
def test_reading_a_hint_as_cp1252_produces_mojibake():
    """The negative control: this is what the bug looked like.

    Kept because it documents the failure mode precisely -- a silent corruption,
    not an exception, which is why it survived every earlier test run.
    """
    sample = next(p for p in sorted(HINTS.rglob("*.md"))
                  if any(g in p.read_text(encoding="utf-8") for g in COURSE_GLYPHS))
    misread = sample.read_bytes().decode("cp1252")
    assert "â" in misread, "expected mojibake under cp1252"
    assert misread != sample.read_text(encoding="utf-8")


@pytest.mark.skipif(not HINTS.is_dir(), reason="hints/ not in this checkout")
def test_hint_command_renders_correctly_under_a_cp1252_locale(tmp_path, monkeypatch):
    """Drive `roots hint` in a subprocess whose default encoding is cp1252.

    PYTHONIOENCODING alone would not reproduce it -- the bug was in how the FILE
    was read, not how it was printed. PYTHONCOERCECLOCALE / LC_ALL do not change
    `locale.getencoding()` on macOS either, so the file read is forced directly:
    the test asserts that our code passes an explicit encoding rather than
    relying on the platform default.
    """
    sample = next(p for p in sorted(HINTS.rglob("*.md"))
                  if any(g in p.read_text(encoding="utf-8") for g in COURSE_GLYPHS))
    # If `roots` ever drops the explicit encoding, this is the line that changes.
    source = (REPO / "cli" / "src" / "roots" / "main.py").read_text(encoding="utf-8")
    assert 'available[index].read_text(encoding="utf-8")' in source, (
        "roots hint must read hint files as UTF-8 explicitly -- without it the "
        "platform locale decides, and cp1252 silently mangles every em dash"
    )
    assert sample.read_text(encoding="utf-8").strip()


def test_no_shipped_module_reads_text_without_an_encoding():
    """A blanket guard, because this class of bug is invisible on macOS.

    Any `read_text()`/`write_text()` with no encoding is a latent Windows bug in
    a file that might one day hold prose. Cheaper to forbid than to audit.
    """
    offenders = []
    for root in ("cli/src", "packages/rootsmarkt-processing/src", "lab/src", "dagster/src"):
        for path in (REPO / root).rglob("*.py"):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if ("read_text()" in line) or (
                    "write_text(" in line and "encoding=" not in line and line.rstrip().endswith(")")
                ):
                    offenders.append(f"{path.relative_to(REPO)}:{number}: {line.strip()}")
    assert not offenders, "file IO without an explicit encoding:\n  " + "\n  ".join(offenders)


def test_output_streams_are_reconfigured_to_utf8():
    """`roots` must be able to print an arrow into a redirected cp1252 stream."""
    assert callable(main._force_utf8_output)
    main._force_utf8_output()      # must be safe to call repeatedly


def test_printing_course_glyphs_survives_redirection(tmp_path):
    """End to end: capture the real entry point's output to a pipe.

    A pipe is not a console, so this is the path that raised UnicodeEncodeError.
    """
    script = (
        "from roots.main import _force_utf8_output;"
        "_force_utf8_output();"
        "print('\\u2014 \\u20ac \\u2192 \\u2026 \\u2013')"
    )
    out = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, encoding="utf-8",
        env={"PYTHONIOENCODING": "cp1252", "PATH": "/usr/bin:/bin"},
        cwd=tmp_path,
    )
    assert out.returncode == 0, out.stderr
    assert "UnicodeEncodeError" not in out.stderr
