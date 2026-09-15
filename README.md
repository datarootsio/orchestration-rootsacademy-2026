# Rootsmarkt — orchestration lab

You are a Data Engineer at **Rootsmarkt**, a grocery chain with twelve stores.

Every night, a supplier platform called **SupplyHub** delivers the day's sales
file. A pipeline picks it up, cleans it, aggregates it, and loads
`daily_revenue`. At 07:00 Finance opens a dashboard built on that table.

On 2026-03-01 the dashboard showed **€0.00** for every store.

The pipeline log was entirely green.

Over the next four hours you will rebuild that pipeline twice — once in Apache
Airflow, once in Dagster — and work out what the system should have known.

---

# Setup

**Do this days before the course, not on the morning.** If something is going to
go wrong, it should go wrong while there is time to fix it. Budget 30 minutes,
most of it waiting for a Docker image.

Six steps, in this order. Each one tells you how to know it worked.

> **Windows:** run everything in **PowerShell** — not in WSL, not in Git Bash.
> The commands below are identical on both platforms; they are program
> invocations, not shell syntax. [`WINDOWS-SETUP.md`](WINDOWS-SETUP.md) has the
> full walkthrough.

---

## Step 1 — Install the prerequisites

You need three things: **Docker Desktop**, **uv**, and the **Astro CLI**.

**macOS**

```bash
brew install --cask docker          # then OPEN it, and leave it running
brew install uv
brew install astronomer/tap/astro@1.42.1 --without-podman
```

Then pull the Airflow image now rather than on the day:

```bash
docker pull astrocrpublic.azurecr.io/runtime:3.3-2
```

**Windows** (PowerShell) — **no `winget` and no admin rights needed**, except for
one step:

1. **Enable WSL 2.** This is the only part needing administrator rights, and it
   is one-time per machine. In an admin PowerShell, then reboot:
   ```powershell
   wsl --update
   wsl --install --no-distribution
   ```
   If you cannot elevate, ask IT **now** — nothing else needs admin, but nothing
   works without this.

2. **Install Docker Desktop**, per-user, no admin:
   [download it](https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe),
   then either double-click it or run
   `& "$HOME\Downloads\Docker Desktop Installer.exe" install --user`.
   Open it and leave it running.

3. **Do Step 2 below (clone the repo) now**, then come back here and run, from
   inside it:
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\setup-windows.ps1
   ```
   It installs uv, downloads the pinned Astro CLI, verifies its checksum against
   Astronomer's published value, puts it on your PATH and pulls the Airflow
   image. Then **open a new terminal** so the PATH change applies.

   `-ExecutionPolicy Bypass` applies to that one run only. It is there because
   managed laptops block scripts by default.

If scripts are blocked outright — common on managed laptops — the same steps by
hand are in [`WINDOWS-SETUP.md`](WINDOWS-SETUP.md), along with the `winget`
shortcut if you happen to have it.

**✓ Done when** all three report a version:

```bash
docker info --format "{{.ServerVersion}}"     # a number, not an error
uv --version
astro version                                 # 1.42.1
```

**If it fails**
- `astro version` reports something other than 1.42.1 → on macOS you installed
  the wrong formula. It must be `astronomer/tap/astro@1.42.1`, not plain
  `brew install astro`, and `--without-podman` matters: without it you get
  Podman, and this course assumes Docker.
- `astro` not found on Windows → you did not open a **new** terminal after the
  script added it to PATH.
- `docker info` errors → Docker Desktop is installed but not *running*. Open it.
- `wsl` errors on Windows → Step 1.1 has not been done, and it needs admin.

---

## Step 2 — Get the repository

```bash
git clone <the URL your instructor gave you> rootsmarkt-orchestration
cd rootsmarkt-orchestration
```

Keep the path **short and local** — `C:\dev\...` or `~/dev/...`. Not a network
drive, not OneDrive, not a folder twelve levels deep. Windows has a
260-character path limit that Docker mounts can hit.

**✓ Done when** `ls` (or `dir`) shows `missions/`, `dags/` and `pyproject.toml`.

---

## Step 3 — Install the Python environment

```bash
uv sync
```

This creates `.venv/` and installs Dagster, the `roots` CLI and the sealed
processing package. It does **not** install Airflow — that lives only inside the
Docker image, deliberately.

**✓ Done when**

```bash
uv run roots --help
```

lists commands including `join`, `doctor`, `verify` and `hint`.

**If it fails** — a resolution error here usually means a stale `.venv`. Delete
it and re-run.

---

## Step 4 — Join your team

Your instructor gives you a **join code** — six characters, like `RMEETP`.

```bash
uv run roots join RMEETP
```

**You do not create `.env` yourself.** This command creates both files you need:

| File | What it holds | Committed? |
|---|---|---|
| `config/team.yaml` | your store roster, plausible revenue bounds, warehouse credentials, SupplyHub token | No — it has secrets |
| `.env` | the same values as environment variables, which is what Airflow and Dagster read | No — same reason |

They hold the same information in two shapes because two different runtimes need
it two different ways. Never edit either by hand — if something is wrong, run
`roots join` again.

> **If your instructor also gave you a lab address**, add it:
> ```bash
> uv run roots join RMEETP --lab-url http://192.168.1.50:8090
> ```
> Usually you will not need to — the address is normally already built into the
> repository you cloned.

**✓ Done when** `config/team.yaml` and `.env` both exist, and the command printed
`joined as team-NN` followed by an Airflow connection recipe.

**If it fails**
- `could not join` → the lab server is unreachable. Check the address with your
  instructor; you may need `--lab-url`.
- `401` or `token does not match` → the join code was mistyped. They are
  case-insensitive but otherwise exact.

---

## Step 5 — Pre-flight check

```bash
uv run --env-file .env roots doctor
```

**Note the `--env-file .env`, and use it from now on.** Step 4 created that
file; every command after this point needs the values in it, and that flag is
what loads them. Forget it and things fail confusingly — Dagster loses its event
log, the warehouse becomes unreachable. Just always include it.

`roots doctor` checks versions, Docker, ports, both runtimes, the lab server and
the warehouse, and tells you exactly what to fix.

**✓ Done when** there are no red `FAIL` lines. Yellow `....` lines are fine.

**Do not go to Step 6 until this is green.** This is the entire reason for doing
setup early.

---

## Step 6 — Start the runtimes

Three things run at once, so you need **three terminal windows**, all in the
repository directory. Leave all three running for the whole course.

**Terminal 1 — Airflow**

```bash
astro dev start
```

The first run takes a few minutes; later ones are fast. **Read the URL it
prints** — usually `http://localhost:8080`, but Astro picks a different port when
8080 is busy. Log in with `admin` / `admin`.

**Terminal 2 — Dagster**

```bash
uv run --env-file .env dg dev --target-path dagster
```

Opens on <http://localhost:3000>. The asset graph will be empty. That is correct
— building it is mission D1.

**Terminal 3 — the progress daemon**

```bash
uv run --env-file .env roots watch
```

Re-checks your work every 20 seconds and updates the instructor's dashboard.
Start it and forget it.

**✓ Done when** both UIs load in a browser and `roots watch` is printing.

---

## On the day

```bash
uv run --env-file .env roots airflow-conn    # the values mission A1 needs
```

Then open [`missions/A1.md`](missions/A1.md) and start.

---

# Where things are

```
missions/            START HERE. A1 -> A5, then D1 -> D4.
dags/                Your Airflow pipeline. You edit this.
dagster/src/rootsmarkt/defs/
                     Your Dagster assets. You edit this.

packages/rootsmarkt-processing/
                     SEALED. All the data processing, written and tested.
                     Read it freely; you should not need to change it.
config/team.yaml     Your team's roster, credentials, revenue bounds.
data/                Deliveries you have fetched, and cleaned output.
hints/ checkpoints/  Read via `roots hint` and `roots checkpoint`.
```

---

# The distinction the whole day is about

**Data processing** reads, transforms, validates, aggregates and writes data.
It is already written for you, in `packages/rootsmarkt-processing/`.

**Orchestration** decides *when* something runs, represents dependencies, waits
for conditions, coordinates systems, retries work, records execution state,
reacts to failures, and exposes what happened.

That is what you will write.

Every time you add a line today, you should be able to say which of the two it
is. Some lines are genuinely on the boundary — those are the interesting ones,
and the missions point them out.

You are not expected to memorise Airflow or Dagster syntax. You are expected to
leave able to say *"this is orchestration because…"* and *"I would represent this
differently in Airflow and Dagster because…"*.

---

# Commands

All of these take the `uv run --env-file .env` prefix.

```bash
roots verify a2          # check one mission now, and report it
roots status             # what this machine thinks you have earned
roots hint a2            # next hint. Run again for the one after.
roots checkpoint a2      # take a known-good state and keep moving
roots submit a2 ...      # evidence submissions (A2, D3, D4)
roots airflow-conn       # the exact Airflow connection values for A1
roots doctor             # when something is wrong and you do not know what
roots online --lab-url … # if the lab moves. Your instructor gives you the address.
```

**Use `hint` and `checkpoint` freely.** There is no penalty and nobody to ask.
Losing forty minutes to one mission costs you the rest of the course, which is a
much worse outcome than taking a hint.

---

# Your data is yours

Row counts, delivery IDs, store rosters and the plausible revenue range all
differ per team, derived from your team ID.

If your numbers do not match the team beside you, **nothing is broken**. You are
both right. It also means a copied answer will not verify — not because anyone is
policing it, but because it is literally about different data.

---

# If something breaks

`roots doctor` first, always. It names the fix.

| Symptom | Cause |
|---|---|
| Dagster materializations vanish between runs | You dropped `--env-file .env`, so `DAGSTER_HOME` was not set |
| `password authentication failed for user "team_NN"` | Your credentials are fine — something else is on that port. `roots doctor` explains |
| Airflow UI not on 8080 | Astro picked another port. Read what `astro dev start` printed |
| A DAG stopped appearing | It failed to parse. `astro dev pytest` gives a readable error |
| Mission A1 cannot reach SupplyHub | `roots doctor` checks that address separately — it is not the same one as the dashboard |

**If the lab server becomes unreachable**, your work is not lost: milestones are
recorded locally in `.roots/` and sent when it comes back. If your instructor
moves the lab to another machine, they will give you an address:

```bash
uv run roots online --lab-url http://<address>:8090
uv run --env-file .env roots doctor
```

Everything you have already fetched and loaded stays valid — the delivery data is
identical whichever machine serves it.
