"""Dagster definitions.

Everything under `defs/` is autoloaded, so participants add asset files without
editing a central registry -- one less thing to explain in a four-hour course.

`path_within_project` rather than the deprecated `project_root`: verified against
dagster 1.13.14, where `project_root` warns that it is removed in 2.0.
"""

from pathlib import Path

from dagster import definitions, load_from_defs_folder


@definitions
def defs():
    return load_from_defs_folder(path_within_project=Path(__file__).parent)
