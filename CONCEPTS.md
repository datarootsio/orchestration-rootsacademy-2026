# Concepts

Mental models, not syntax. Keep this open during the missions — the code you need is in the file
you are editing, and the reasoning you need is here.

---

## The distinction the whole day is about

Every line you write today belongs to one of these two columns.

| Data processing | Orchestration |
|---|---|
| Reads, transforms, validates, aggregates, writes data | Decides **when** work runs, represents **dependencies**, waits for conditions, **retries**, records **state**, reacts to **failure**, exposes **operational state** |
| Already written for you, in `packages/rootsmarkt-processing/` | What you write today |

The interesting lines are the ones on the boundary. *"Revenue must be plausible"* is processing
when it computes a boolean; it is orchestration when that boolean decides whether the pipeline
stops. Same expression, two layers, told apart only by who consumes the answer.

---

## Airflow: the **run** is the unit of record

Airflow's memory is organised around *executions*. It knows a great deal about what ran, when, in
what order, and how it ended. Everything it knows about your data is something you chose to tell
it.

**DAG** — a pipeline definition. The unit you author: a named collection of work with an order.

**Task** — one unit of execution inside a DAG. The thing that succeeds or fails.

**Dependency** — the statement that one task comes after another. Two different kinds, and
confusing them is the root of most bad pipelines:

- *Ordering*: B must not start until A finishes. Nothing passes between them.
- *Data*: B needs something A produced.

Both look the same on a graph. Only one of them tells you anything about what to rebuild when
something changes — which is the whole of mission D3, later.

**DAG run vs task instance** — the distinction people trip on, and the one you cannot diagnose
A2 or A4 without. A *DAG run* is one execution of the whole pipeline. A *task instance* is one
execution of one task within one run. "It failed" is never enough information: which run, which
task, which attempt?

**Retries** — a task can be re-attempted automatically. Worth asking every time you set it: *what
would have to be true for a second attempt to succeed?* If the answer is "nothing", retrying only
makes the failure slower and noisier. A source that deterministically returns the wrong thing is
not a transient fault.

**Connection** — named, stored credentials Airflow manages, referenced by ID from your pipeline.
The point is that the secret is not in the code. The moment it is in the code it is in git, in
every clone, and in the screenshot someone pastes into Slack.

**XCom** — a small key-value channel for passing values between tasks. *Small.* Row counts, file
paths, identifiers. Not datasets. Data belongs on disk or in the warehouse; what crosses between
tasks is a reference to it and a few facts about it.

**Where to look** — the Grid view shows runs as columns and tasks as rows, so a failure is a
coloured cell you can click into for logs. When something is wrong, start there, not in the code.

**Airflow has assets.** Airflow can also treat a dataset as a first-class thing: something exists,
here is what we know about it, and other pipelines can be scheduled on it rather than on a clock.
You will use this in A4. Anyone who tells you Airflow is "task-based and therefore cannot do
data-aware orchestration" is describing an older version.

---

## Dagster: the **asset** is the unit of record

Same problem, different centre of gravity. Dagster's memory is organised around *datasets*: what
exists, what it is made of, when it was last built, and whether it is currently valid.

**Asset** — a dataset your pipeline produces. A table, a file, a model. You describe the asset and
how to build it; the build is a consequence of the definition rather than the thing you name.

**Asset graph** — which assets are built from which. This is lineage, and it is the structure the
whole model hangs from.

> **In this course dependencies are *declared*, not inferred.** Elsewhere you will see Dagster
> infer an edge from a function parameter — that works when an asset *returns* its data. Here
> assets hand data over on disk and in the warehouse and return only a record of what happened,
> so there is no value to pass and nothing for the system to infer. You state the edge. The
> practical consequence: writing it as a parameter will not work, and the file you are editing
> says so at the top.

**Materialization** — the event of an asset being built: *this asset now exists, as of this
moment, with this content*. Note what that is not — it is not "a task ran". The record is attached
to the dataset, and it survives as the dataset's history.

**Materialization metadata** — facts you attach to that event. Row counts, totals, checksums,
paths. This is how a dataset tells you what it is worth without anyone opening a log, and in D1 it
is the entire deliverable: you will see a *successful* materialization carrying a visibly wrong
number.

**Asset check** — a named, declared assertion about an asset's validity, whose result is recorded
against the asset. Crucially, an asset can materialize *successfully* and still be marked invalid.
Those are two different statements, and being able to make both is the point.

**Blocking** — a check can be advisory or it can stop everything downstream. The predicate is
identical either way. The difference is whether the check *notifies* you or *prevents propagation*
— which is the difference between finding out and being protected.

**Selective materialization** — you can rebuild a chosen subset rather than everything. This is
only meaningful because the graph knows what depends on what, and it is the difference between a
two-minute recovery and a forty-minute one.

**Where to look** — the asset catalog lists what exists, with lineage and each asset's latest
materialization and check results. It answers "what is the state of my data?" rather than "what
ran last night?"

---

## Comparing them honestly

Not "Airflow is task-based and Dagster is asset-based". Both have assets in the versions you are
using, and their capabilities overlap far more than the internet suggests.

The difference that holds is **what each system treats as its unit of record** — and therefore
what it can tell you afterwards, without you having built the answer yourself:

| | Airflow | Dagster |
|---|---|---|
| Records | the run and its task instances | the asset, its latest materialization and check results |
| Answers well | *what ran last night, and what broke?* | *what is the state of my data, and what is downstream of this change?* |
| Data awareness | available, and you construct the semantics | built in, and the semantics are declared |

Neither is the right answer in general. If what you have is a graph of datasets whose validity you
need to reason about, a model whose unit of record is the dataset does more of the work for you.
If what you have is a heterogeneous collection of jobs that must happen in an order — send an
email, trigger an external system, refresh a cache — a model whose unit of record is the run gets
in your way less.

Most real platforms have both. That is why both tools exist, and why neither is going away.

By the end of today you will have built the same pipeline in each, which makes you one of the few
people qualified to have this argument with evidence.
