# Windows setup

Everything in this course runs on Windows. Do this **days before the session**.

**The one rule that matters most:** run everything in **PowerShell**, not in a
WSL terminal. This is Astronomer's own instruction — *"The Astro CLI runs
directly on Windows. Run `astro` commands in Windows PowerShell, not in a WSL
terminal."* WSL2 has to be *enabled*, because Docker uses it to run Linux
containers, but it is not where you work.

**You do not need `winget`, and you do not need administrator rights** — with one
exception, called out in step 1. Everything installs under your own user profile.

---

## 1. Enable WSL 2 — the only step needing admin

Docker cannot run Linux containers without it. It is a one-time, per-machine
operation.

In an **administrator** PowerShell, then reboot:

```powershell
wsl --update
wsl --install --no-distribution
```

`--no-distribution` is deliberate. You are installing the WSL2 kernel that Docker
uses, not a Linux you will log into.

**If you cannot elevate, ask IT now.** Nothing else here needs admin, but nothing
works without this — which is the reason to do setup a week early rather than on
the morning.

### While you are here: the machine requirements

| | Requirement |
|---|---|
| Windows | 10 22H2 (build 19045) or Windows 11 23H2 (build 22631) or newer |
| RAM | 8 GB minimum |
| Virtualisation | Enabled in BIOS/UEFI |
| WSL | version 2.1.5 or later |

Those floors come from **Docker Desktop's** requirements, which are stricter than
Astronomer's stated minimum (build 16299). Docker Desktop is the binding
constraint — use its numbers.

---

## 2. Install Docker Desktop — no admin needed

1. Download the installer:
   <https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe>
2. Install it **per-user**, which needs no administrator:

   ```powershell
   & "$HOME\Downloads\Docker Desktop Installer.exe" install --user
   ```

   Per-user is also what you get by just double-clicking — it is the installer's
   default. Docker's own words: *"No administrator privileges required to install
   or update Docker Desktop."* It installs under your profile and works with the
   WSL 2 backend, which is the one this course uses.

3. **Open Docker Desktop and leave it running.** Nothing below works until the
   whale icon says it is running.

> Per-user mode skips Docker's privileged helper service, so the Hyper-V backend
> and Windows containers are unavailable. Neither is used here.

---

## 3. Install uv and the Astro CLI

### The easy way

From the repository directory:

```powershell
powershell -ExecutionPolicy Bypass -File .\setup-windows.ps1
```

It installs uv, downloads the pinned Astro CLI, **verifies its SHA256 against
Astronomer's published checksum**, puts it on your PATH, and pulls the Airflow
image. No package manager, no admin, and safe to run again if something fails
part way.

`-ExecutionPolicy Bypass` is there because managed laptops block scripts by
default. It applies to that one run only and changes nothing permanently.

**Then open a new terminal** so the PATH change applies.

### By hand, if scripts are blocked

Some machines block scripts outright. These are the same steps:

**uv** — installs under your user profile:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**Astro CLI** — a bare `.exe`, not an installer and not an archive:

1. Download from
   <https://github.com/astronomer/astro-cli/releases/tag/v1.42.1>:

   | Your machine | File |
   |---|---|
   | Intel/AMD 64-bit (almost everyone) | `astro_1.42.1_windows_amd64.exe` |
   | Windows on ARM | `astro_1.42.1_windows_arm64.exe` |

2. Rename it to **`astro.exe`**.
3. Move it to a folder you own, e.g. `%LOCALAPPDATA%\Programs\rootsmarkt\bin`.
4. Add that folder to your **user** PATH: Start → "Edit environment variables for
   your account" → `Path` → New.
5. Open a new terminal.

**The Airflow image** — do it now, not on the day:

```powershell
docker pull astrocrpublic.azurecr.io/runtime:3.3-2
```

### The shortcut, if you happen to have winget

Check first — it is not on every machine:

```powershell
winget --version
```

If that works:

```powershell
winget install -e --id astral-sh.uv
winget install -e --id Astronomer.Astro -v 1.42.1 --skip-dependencies
```

`--skip-dependencies` matters: since Astro CLI 1.32.0, winget installs **Podman**
as the default container engine, and this course is Docker throughout. It applies
only to this winget route — the download above has no such problem.

If you ever see Podman mentioned in an error, force Docker:

```powershell
astro config set -g container.binary docker
```

> **Windows on ARM: winget will not help you.** The winget package for 1.42.1 is
> x64 only. Use the script or the manual download, both of which have an arm64
> path.

> **`winget` not found?** It ships inside *App Installer*, which arrives through
> the Microsoft Store and may be absent or blocked on a managed machine. Do not
> fight it — the script and the manual steps above exist precisely for this.

---

## 4. Verify

```powershell
docker info --format "{{.ServerVersion}}"    # a number, not an error
uv --version
astro version                                # 1.42.1
```

All three answering means setup is done. Continue from **Step 2** of
[`README.md`](README.md).

---

## 5. Where to put the repo

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

## 6. Windows-specific things worth knowing

**`.env` must not contain backslashes.** `roots join` handles this — it writes
paths as `C:/dev/...`, with forward slashes, which Windows accepts everywhere.

The reason is worth knowing if you ever edit `.env` by hand: uv's `--env-file`
parser **rejects the entire file** if any value contains a backslash. Not the
offending line — the whole file. It prints one `warning:` and carries on, so
Dagster starts with no configuration and the symptom looks like "Dagster is
broken". `roots doctor` checks for this explicitly, and also runs
`uv run --env-file` end to end to confirm your configuration really loads.

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

## 7. If something is wrong

`roots doctor` first. It knows the Windows install commands, checks your `.env`
for the backslash problem, confirms `uv run --env-file` actually works, and warns
about path length and network drives.

Two things Astronomer does **not** document for Windows, so they are worth
flagging to your instructor rather than fighting alone:

- Docker Desktop file sharing / bind-mount failures (`Mounts denied`)
- `MAX_PATH` errors during `astro dev start`

---

## Sources

- [Install the Astro CLI](https://www.astronomer.io/docs/astro/cli/install-cli) — PowerShell-not-WSL, winget command, WSL2 prerequisite
- [Podman for the Astro CLI](https://www.astronomer.io/docs/astro/cli/use-podman) — Podman default since 1.32.0
- [Configure the Astro CLI](https://www.astronomer.io/docs/astro/cli/configure-cli) — `container.binary`
- [astro-cli releases v1.42.1](https://github.com/astronomer/astro-cli/releases/tag/v1.42.1) — Windows `.exe` assets and checksums
- [Docker Desktop for Windows](https://docs.docker.com/desktop/setup/install/windows-install/) — per-user install, version floors
- [Docker Desktop permission requirements](https://docs.docker.com/desktop/setup/install/windows-permission-requirements/) — what per-user mode does and does not include
- [uv installation](https://docs.astral.sh/uv/getting-started/installation/) — the standalone installer
- [WinGet overview](https://learn.microsoft.com/en-us/windows/package-manager/winget/) — App Installer, and why winget may be absent
- [astro-cli#635](https://github.com/astronomer/astro-cli/issues/635) — ANSI escapes on Windows

Verified 2026-09-15 against these sources.
