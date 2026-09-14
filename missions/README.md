# Missions

Work through these in order. Each one is 15–20 minutes.

| | Mission | What it is really about |
|---|---|---|
| **A1** | [Establish contact](A1.md) | Where credentials live, and what an orchestrator knows about work it did not do itself |
| **A2** | [The delivery lies](A2.md) | A 200 is not evidence you got what you asked for. And: when is retrying the right answer? |
| **A3** | [Transform, load, prove it](A3.md) | Dependencies, data handoff, idempotency, and where validation belongs |
| **A4** | [The 07:00 incident](A4.md) | The one from minute 0. Who decides what "success" means? |
| **A5** | [Let the data schedule](A5.md) | *Stretch.* Scheduling on a dataset instead of a clock |
| **D1** | [Model the data](D1.md) | The same pipeline as a graph of datasets. Then read what the catalog tells you |
| **D2** | [Make validity a property](D2.md) | A check that belongs to the dataset, not to a task log |
| **D3** | [Selective recovery](D3.md) | A rule changed. What does that actually invalidate? |
| **D4** | [The comparison](D4.md) | What you would do differently in each tool, and why |

## The two commands you will use constantly

```
roots verify a2      # check your work, report it to the dashboard
roots status         # what this machine thinks you have earned
```

`roots watch` is running in the background and does this for you every 20 seconds.
`roots verify` is for when you do not want to wait.

## When you are stuck

```
roots hint a2        # first hint. Run it again for the second.
roots checkpoint a2  # take a known-good state and keep moving
```

Use them. Both are yours to call, with no penalty and nobody to ask. A team that
loses forty minutes to one mission loses the rest of the course, which is a far
worse outcome than taking a hint.

Adopting a checkpoint is recorded and shows on the dashboard. That is not a
punishment — it is how the instructor knows which mission to spend the debrief on.

## One thing worth knowing up front

**Your team's data is yours.** Row counts, delivery IDs, store rosters and the
plausible revenue range all differ per team. If your numbers do not match the
team next to you, nothing is broken — you are both right.

Which also means: an answer copied from a neighbour will not verify. Not because
anyone is policing it, but because it is literally about different data.
