# Astro Runtime pinned per CLAUDE.md. Verified image reference 2026-08-15.
FROM astrocrpublic.azurecr.io/runtime:3.3-2

# The base image carries ONBUILD triggers that fire immediately after this FROM,
# in this order (verified with `docker image inspect --format '{{.Config.OnBuild}}'`):
#
#   1  COPY packages.txt .
#   2  USER root
#   3  RUN /usr/local/bin/install-system-packages
#   4  COPY requirements.txt .
#   5  RUN /usr/local/bin/install-python-dependencies   <-- requirements installed HERE
#   6  USER astro
#   7  COPY --chown=astro:0 . .                          <-- build context arrives HERE
#
# Two consequences, and the first cost a failed build:
#
#   - requirements.txt is installed at step 5, BEFORE the build context lands at
#     step 7. A `-e ./packages/rootsmarkt-processing` line there fails with
#     "Distribution not found at: file:///usr/local/airflow/packages/..." because
#     that directory does not exist yet. Local paths cannot go in requirements.txt.
#   - Everything below this comment runs AFTER step 7, so packages/ IS present.
#     Step 7 also copies it for us, so no explicit COPY is needed.
#
# Hence: the sealed processing layer is installed here, not in requirements.txt.
#
# Installed non-editable on purpose. packages/ is baked into the image and is not
# one of the directories Astro mounts, so `-e` would imply a liveness that does
# not exist. Editing the sealed package requires a rebuild — acceptable, because
# mission D3's fix lives in the DAG, not in the package (decision D-027).
USER root
RUN uv pip install --system --no-cache-dir \
      -c /etc/pip-constraints.txt \
      /usr/local/airflow/packages/rootsmarkt-processing
USER astro

# Astro mounts include/ into the container, so data written here is visible from
# the host. Without this the DAG would write to a path that is not mounted and
# the files would vanish with the container.
#
# VERIFY IN BLOCK 5: confirm include/ is mounted WRITABLE by `astro dev start`.
# If it is not, point this at a directory that is.
ENV ROOTSMARKT_DATA_DIR=/usr/local/airflow/include/data
