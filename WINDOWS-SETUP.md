# Windows setup

Everything in this course runs on Windows. Do this **days before the session**.

**The one rule that matters most:** run everything in **PowerShell**, not in a
WSL terminal. This is Astronomer's own instruction — *"The Astro CLI runs
directly on Windows. Run `astro` commands in Windows PowerShell, not in a WSL
terminal."* WSL2 has to be *enabled*, because Docker uses it to run Linux
containers, but it is not where you work.

---

## 1. Prerequisites

| | Requirement |
|---|---|
| Windows | 10 22H2 (build 19045) or Windows 11 23H2 (build 22631) or newer |
| RAM | 8 GB minimum |
| Virtualisation | Enabled in BIOS/UEFI |
| WSL | version 2.1.5 or later |

Those Windows and WSL floors come from **Docker Desktop's** requirements, which
are stricter than Astronomer's stated minimum (build 16299). Docker Desktop is
the binding constraint — use its numbers.

Enable WSL2 (PowerShell as administrator), then reboot:

```powershell
wsl --update
wsl --install --no-distribution
```

`--no-distribution` is deliberate. You are installing the WSL2 kernel for Docker
to use, not a Linux you will log into.

Then install **Docker Desktop** with the WSL 2 backend and start it. It must be
running before anything below.

---

## 2. Install the tools

```powershell
winget install -e --id Astronomer.Astro -v 1.42.1 --skip-dependencies
winget install -e --id astral-sh.uv
docker pull astrocrpublic.azurecr.io/runtime:3.3-2
astro version
```

`--skip-dependencies` matters. Since Astro CLI 1.32.0, winget installs **Podman**
as the default container engine. This course is Docker throughout, so skip it.
The CLI prefers `docker` when both are present, but do not rely on that — if you
ever see Podman mentioned in an error, force it:

```powershell
astro config set -g container.binary docker
```

### If you are on Windows-on-ARM

The winget manifest for 1.42.1 declares **x64 only**. Use the manual install:

1. Download `astro_1.42.1_windows_arm64.exe` from
   <https://github.com/astronomer/astro-cli/releases/tag/v1.42.1>.
   It is a **bare `.exe`**, not a zip — unlike the macOS and Linux assets.
2. Rename it to `astro.exe`.
3. Put its folder on your `PATH`.
4. Restart the machine.

`astro_1.42.1_windows_amd64.exe` is the same route for x64 if winget gives you
trouble.

---

## 3. Where to put the repo

Clone it somewhere **short and local**:

```
C:\dev\rootsmarkt-orchestration
```

Two reasons, and only the first is documented anywhere:

- **Path length.** Windows has a 260-character limit. `astro dev start` mounts
  your project into a container, and a deep path plus a nested filename can cross
  it. `roots doctor` warns past 150 characters.
- **Not a network drive.** Docker Desktop file sharing is unreliable on UNC and
  mapped network paths. `roots doctor` warns about this too.

Do **not** put the project inside the WSL filesystem (`\\wsl$\...`). Astronomer
tells you to run `astro.exe` from PowerShell against a normal Windows path, and
that combination is the one they document.

---

## 4. Set up the project

```powershell
uv sync
uv run roots join <your-team-code>
uv run --env-file .env roots doctor
```

Do not continue until `roots doctor` is green.

Then, in two more PowerShell windows:

```powershell
astro dev start
uv run --env-file .env dg dev --target-path dagster
uv run --env-file .env roots watch
```

---

## 5. Windows-specific things worth knowing

**`.env` must not contain backslashes.** `roots join` handles this — it writes
paths as `C:/dev/...`, with forward slashes, which Windows accepts everywhere.

The reason is worth knowing if you ever edit `.env` by hand: uv's `--env-file`
parser **rejects the entire file** if any value contains a backslash. Not the
offending line — the whole file. It prints one `warning:` and carries on, so
Dagster starts with no configuration and the symptom looks like "Dagster is
broken". `roots doctor` checks for this explicitly.

**The venv layout differs.** Scripts live in `.venv\Scripts\`, not `.venv/bin/`.
Prefixing commands with `uv run` avoids needing to care.

**Standalone Airflow mode is unavailable on Windows.** This course only uses
container mode, so it does not affect you.

**Odd characters in the terminal.** The Astro CLI has a known open bug where
ANSI colour codes leak into Windows terminal output
([astro-cli#635](https://github.com/astronomer/astro-cli/issues/635)). `roots`
strips them before parsing, so your milestone checks are unaffected — but `astro`
output may look untidy.

---

## 6. If something is wrong

`roots doctor` first. It knows the Windows install command, checks your `.env`
for the backslash problem, and warns about path length and network drives.

Two things Astronomer does **not** document for Windows, so they are worth
flagging to your instructor rather than fighting alone:

- Docker Desktop file sharing / bind-mount failures (`Mounts denied`)
- `MAX_PATH` errors during `astro dev start`

---

## Sources

- [Install the Astro CLI](https://www.astronomer.io/docs/astro/cli/install-cli) — PowerShell-not-WSL, winget command, WSL2 prerequisite
- [Podman for the Astro CLI](https://www.astronomer.io/docs/astro/cli/use-podman) — Podman default since 1.32.0
- [Configure the Astro CLI](https://www.astronomer.io/docs/astro/cli/configure-cli) — `container.binary`
- [astro-cli releases v1.42.1](https://github.com/astronomer/astro-cli/releases/tag/v1.42.1) — Windows `.exe` assets
- [Docker Desktop for Windows requirements](https://docs.docker.com/desktop/setup/install/windows-install/) — the binding version floors
- [astro-cli#635](https://github.com/astronomer/astro-cli/issues/635) — ANSI escapes on Windows

Verified 2026-09-15 against these sources.
