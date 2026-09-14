# Checkpoints

Known-good states, one per mission. Adopt one with `roots checkpoint a3`.

**Generated** by `tools/build_checkpoints.py` from `solutions/`. Do not edit
these files -- edit the solution and regenerate.

Each checkpoint is the state at the END of that mission: everything up to and
including it is solved, everything after it is still the starter stub.

`a1` restores the file, but the Airflow **connection** lives in Airflow's own
database rather than in any file -- run `roots airflow-conn` as well.

There is no `d4` checkpoint: D4 asks for reasoning, not code.
