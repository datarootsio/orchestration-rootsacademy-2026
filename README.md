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

## Setup

Do this **days before the course**, not on the morning. If something is going to
go wrong, it should go wrong while there is time to fix it.

```bash
uv sync
uv run roots join <your-team-code>
uv run --env-file .env roots doctor
```

The same commands work in PowerShell — they are program invocations, not shell
syntax. **Windows users: run everything in PowerShell, not in WSL.**

`roots join` writes `config/team.yaml` and `.env`. `roots doctor` is the
pre-flight check — it tells you exactly what is missing and how to fix it. **Do
not continue until it is green.**

Then start both runtimes:

```bash
astro dev start                                        # Airflow -> http://localhost:8080
uv run --env-file .env dg dev --target-path dagster    # Dagster -> http://localhost:3000
```

The first `astro dev start` pulls a Docker image and takes a few minutes. Every
one after that is fast.

Finally, leave this running in a spare terminal for the whole session:

```bash
uv run --env-file .env roots watch
```

It re-checks your work every 20 seconds and updates the instructor's dashboard.
You never have to think about it again.

### Installing the Astro CLI

You need Docker Desktop running first, on either platform.

**macOS**

```bash
brew install astronomer/tap/astro@1.42.1 --without-podman
docker pull astrocrpublic.azurecr.io/runtime:3.3-2
astro version
```

Not plain `brew install astro` — that formula is a different version and installs
Podman instead of Docker.

**Windows** — in **PowerShell**, not a WSL terminal:

```powershell
winget install -e --id Astronomer.Astro -v 1.42.1 --skip-dependencies
docker pull astrocrpublic.azurecr.io/runtime:3.3-2
astro version
```

`--skip-dependencies` is the Windows equivalent of macOS's `--without-podman`:
without it, winget installs Podman and this course assumes Docker throughout.

See [`WINDOWS-SETUP.md`](WINDOWS-SETUP.md) for the full Windows
walkthrough — WSL2 prerequisites, the Windows-on-ARM path, and the handful of
places Windows differs.

Either way, `roots doctor` tells you if you got it wrong.

---

## Where things are

```
missions/            START HERE. A1 -> A5, then D1 -> D4.
dags/                Your Airflow pipeline. You edit this.
dagster/src/rootsmarkt/defs/
                     Your Dagster assets. You edit this.

packages/rootsmarkt-processing/
                     SEALED. All the data processing, written and tested.
                     Read it freely; you should not need to change it.
config/team.yaml     Your team's roster, warehouse credentials, revenue bounds.
data/                Deliveries you have fetched, and cleaned output.
hints/ checkpoints/  Read via `roots hint` and `roots checkpoint`.
```

---

## The distinction the whole day is about

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

## Commands

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

Prefix them with `uv run --env-file .env` if `roots` is not on your PATH.

**Use `hint` and `checkpoint` freely.** There is no penalty and nobody to ask.
Losing forty minutes to one mission costs you the rest of the course, which is a
much worse outcome than taking a hint.

---

## Your data is yours

Row counts, delivery IDs, store rosters and the plausible revenue range all
differ per team, derived from your team ID.

If your numbers do not match the team beside you, **nothing is broken**. You are
both right. It also means a copied answer will not verify — not because anyone is
policing it, but because it is literally about different data.

---

## If something breaks

`roots doctor` first. It checks versions, ports, Docker, both UIs, the warehouse
and the lab server, and names the fix.

If the lab server becomes unreachable, your work is not lost: milestones are
recorded locally in `.roots/` and sent when it comes back. If your instructor
moves the lab to their own machine, they will give you an address:

```bash
uv run roots online --lab-url http://<address>:8090
uv run --env-file .env roots doctor
```

Everything you have already fetched and loaded stays valid — the delivery data is
identical whichever machine serves it.
